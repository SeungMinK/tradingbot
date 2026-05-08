"""일일 헬스체크 모듈.

매일 06:00에 실행하여 봇 상태를 점검한다:
1. 매매 정합성 (DB vs 업비트)
2. 뉴스 수집기 상태
3. 미체결 주문 정리
4. LLM 비용 일일 집계
5. DB 데이터 무결성
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class HealthChecker:
    """일일 헬스체크."""

    def __init__(self, db, trader=None, notifier=None) -> None:
        self._db = db
        self._trader = trader
        self._notifier = notifier

    def run_all(self) -> dict:
        """전체 헬스체크 실행. 결과를 dict로 반환."""
        results = {}

        results["trade_integrity"] = self._check_trade_integrity()
        results["trade_reconciliation"] = self.reconcile_trades()
        results["balance_check"] = self._check_balance_consistency()
        results["news_collector"] = self._check_news_collector()
        results["pending_orders"] = self._check_pending_orders()
        results["llm_cost"] = self._check_llm_cost()
        results["data_integrity"] = self._check_data_integrity()
        results["strategy_consistency"] = self._check_strategy_consistency()

        # 전체 상태
        issues = [k for k, v in results.items() if v.get("status") == "warning"]
        results["overall"] = "healthy" if not issues else "warning"
        results["issues"] = issues
        results["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Slack 알림
        if self._notifier and issues:
            self._send_alert(results)
        elif self._notifier:
            self._notifier.send("✅ *일일 헬스체크 정상* — 이상 없음")

        logger.info("헬스체크 완료: %s (%d건 이상)", results["overall"], len(issues))
        return results

    def _check_trade_integrity(self) -> dict:
        """매매 정합성: 매수 후 매도 안 된 건 vs 실제 보유."""
        try:
            # DB에서 활성 매수 (매도 안 된 건)
            db_active = self._db.execute(
                """
                SELECT coin, price, amount, total_krw FROM trades t
                WHERE side = 'buy'
                AND NOT EXISTS (
                    SELECT 1 FROM trades s WHERE s.buy_trade_id = t.id AND s.side = 'sell'
                )
                """
            ).fetchall()
            db_coins = {dict(r)["coin"] for r in db_active}

            # 실제 업비트 보유 확인
            if self._trader and self._trader.is_ready:
                upbit_coins = set()
                for coin in db_coins:
                    try:
                        bal = self._trader.get_balance_coin(coin)
                        if bal > 0:
                            upbit_coins.add(coin)
                    except Exception:
                        pass

                db_only = db_coins - upbit_coins  # DB에만 있음 (매도 누락?)
                upbit_only = upbit_coins - db_coins  # 업비트에만 있음 (매수 미기록?)

                if db_only or upbit_only:
                    logger.warning(
                        "매매 정합성 불일치: DB에만=%s, 업비트에만=%s",
                        db_only,
                        upbit_only,
                    )
                    return {
                        "status": "warning",
                        "db_only": list(db_only),
                        "upbit_only": list(upbit_only),
                        "message": f"DB에만 {len(db_only)}건, 업비트에만 {len(upbit_only)}건",
                    }

                return {"status": "ok", "active_positions": len(db_coins)}
            return {"status": "ok", "message": "API 미설정 — 스킵"}
        except Exception as e:
            logger.error("매매 정합성 체크 실패: %s", e)
            return {"status": "warning", "message": str(e)}

    def _check_news_collector(self) -> dict:
        """뉴스 수집기 상태: 24시간 내 수집 건수."""
        try:
            row = self._db.execute(
                "SELECT COUNT(*) as cnt FROM news_articles WHERE collected_at >= datetime('now', '-24 hours')"
            ).fetchone()
            count = row[0] if row else 0

            fg_row = self._db.execute(
                "SELECT COUNT(*) as cnt FROM fear_greed_index WHERE collected_at >= datetime('now', '-24 hours')"
            ).fetchone()
            fg_count = fg_row[0] if fg_row else 0

            if count == 0 and fg_count == 0:
                return {
                    "status": "warning",
                    "news_count": count,
                    "fg_count": fg_count,
                    "message": "24시간 내 뉴스+F&G 수집 0건 — 수집기 중단 의심",
                }

            return {
                "status": "ok",
                "news_count": count,
                "fg_count": fg_count,
            }
        except Exception as e:
            logger.error("뉴스 수집기 체크 실패: %s", e)
            return {"status": "warning", "message": str(e)}

    def _check_pending_orders(self) -> dict:
        """미체결 주문 확인 및 정리."""
        try:
            if not self._trader or not self._trader.is_ready:
                return {"status": "ok", "message": "API 미설정 — 스킵"}

            # 활성 코인 목록
            coins = self._db.execute(
                """
                SELECT DISTINCT coin FROM trades
                WHERE side = 'buy'
                AND NOT EXISTS (
                    SELECT 1 FROM trades s WHERE s.buy_trade_id = trades.id AND s.side = 'sell'
                )
                """
            ).fetchall()

            total_cancelled = 0
            for r in coins:
                coin = dict(r)["coin"]
                cancelled = self._trader.cancel_all_orders(coin)
                total_cancelled += cancelled

            if total_cancelled > 0:
                logger.info("미체결 주문 %d건 취소", total_cancelled)
                return {
                    "status": "warning",
                    "cancelled": total_cancelled,
                    "message": f"미체결 주문 {total_cancelled}건 자동 취소",
                }

            return {"status": "ok", "cancelled": 0}
        except Exception as e:
            logger.error("미체결 주문 체크 실패: %s", e)
            return {"status": "warning", "message": str(e)}

    def _check_llm_cost(self) -> dict:
        """LLM 비용 일일 집계."""
        try:
            row = self._db.execute(
                """
                SELECT COUNT(*) as calls, COALESCE(SUM(cost_usd), 0) as total_cost
                FROM llm_decisions
                WHERE DATE(timestamp) = DATE('now')
                """
            ).fetchone()

            calls = row[0] if row else 0
            cost = row[1] if row else 0

            status = "warning" if calls > 10 else "ok"
            return {
                "status": status,
                "calls": calls,
                "cost_usd": round(cost, 4),
                "message": f"LLM {calls}회 호출, ${cost:.4f}" if calls > 10 else None,
            }
        except Exception as e:
            logger.error("LLM 비용 체크 실패: %s", e)
            return {"status": "warning", "message": str(e)}

    def _check_data_integrity(self) -> dict:
        """DB 데이터 무결성."""
        try:
            issues = []

            # 매도인데 buy_trade_id 없는 건
            row = self._db.execute(
                "SELECT COUNT(*) FROM trades WHERE side = 'sell' AND buy_trade_id IS NULL"
            ).fetchone()
            orphan_sells = row[0] if row else 0
            if orphan_sells > 0:
                issues.append(f"매도 {orphan_sells}건에 buy_trade_id 없음")

            # 실행된 신호인데 trade_id 없는 건
            row = self._db.execute(
                "SELECT COUNT(*) FROM trade_signals WHERE executed = 1 AND trade_id IS NULL"
            ).fetchone()
            orphan_signals = row[0] if row else 0
            if orphan_signals > 0:
                issues.append(f"실행된 신호 {orphan_signals}건에 trade_id 없음")

            # 가격 0인 스냅샷
            row = self._db.execute(
                """
                SELECT COUNT(*) FROM market_snapshots
                WHERE price IS NULL OR price = 0
                AND timestamp >= datetime('now', '-24 hours')
                """
            ).fetchone()
            bad_snapshots = row[0] if row else 0
            if bad_snapshots > 0:
                issues.append(f"가격 0/NULL 스냅샷 {bad_snapshots}건 (24시간)")

            if issues:
                return {
                    "status": "warning",
                    "issues": issues,
                    "message": "; ".join(issues),
                }

            return {"status": "ok"}
        except Exception as e:
            logger.error("데이터 무결성 체크 실패: %s", e)
            return {"status": "warning", "message": str(e)}

    def _check_strategy_consistency(self) -> dict:
        """LLM 추천 vs DB 저장 vs 실제 신호 적용 — 3중 검증."""
        try:
            import json

            issues = []

            # 1. 활성 전략 확인
            active_row = self._db.execute(
                "SELECT name FROM strategies WHERE is_active = TRUE AND status = 'active' LIMIT 1"
            ).fetchone()
            active_name = dict(active_row)["name"] if active_row else "없음"

            # 2. DB 파라미터 범위 검증
            if active_row:
                strategy_row = self._db.execute(
                    "SELECT default_params_json FROM strategies WHERE name = ?",
                    (active_name,),
                ).fetchone()
                if strategy_row:
                    try:
                        db_params = json.loads(dict(strategy_row)["default_params_json"] or "{}")
                        rsi = db_params.get("rsi_oversold")
                        if rsi is not None and (rsi < 20 or rsi > 45):
                            issues.append(f"rsi_oversold={rsi} 범위 이탈 (20~45)")
                        bb = db_params.get("bb_std")
                        if bb is not None and (bb < 0.8 or bb > 2.5):
                            issues.append(f"bb_std={bb} 범위 이탈 (0.8~2.5)")
                    except json.JSONDecodeError:
                        issues.append("전략 파라미터 JSON 파싱 실패")

            # 3. LLM 설정값 vs 실제 신호에 적용된 값 비교
            llm_row = self._db.execute(
                "SELECT input_news_summary FROM llm_decisions ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if llm_row and dict(llm_row)["input_news_summary"]:
                try:
                    ba = json.loads(dict(llm_row)["input_news_summary"])
                    llm_strategy = ba.get("strategy")

                    # 전략 불일치
                    if llm_strategy and llm_strategy != active_name:
                        issues.append(f"전략 불일치: LLM 추천={llm_strategy}, 활성={active_name}")

                    # 최근 신호에 적용된 파라미터 확인
                    recent_signal = self._db.execute(
                        "SELECT strategy, strategy_params_json FROM trade_signals ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                    if recent_signal:
                        signal_strategy = dict(recent_signal)["strategy"]
                        signal_params_json = dict(recent_signal)["strategy_params_json"]

                        if signal_strategy != active_name:
                            issues.append(f"신호 전략 불일치: 신호={signal_strategy}, 활성={active_name}")

                        if signal_params_json and active_row:
                            try:
                                signal_params = json.loads(signal_params_json)
                                # rsi_oversold 비교
                                if "rsi_oversold" in db_params and "rsi_oversold" in signal_params:
                                    if db_params["rsi_oversold"] != signal_params["rsi_oversold"]:
                                        issues.append(
                                            f"rsi_oversold 미반영: DB={db_params['rsi_oversold']}, "
                                            f"신호={signal_params['rsi_oversold']}"
                                        )
                            except (json.JSONDecodeError, TypeError):
                                pass

                except (json.JSONDecodeError, TypeError):
                    pass

            if issues:
                return {"status": "warning", "active": active_name, "issues": issues, "message": "; ".join(issues)}
            return {"status": "ok", "active": active_name}
        except Exception as e:
            logger.error("전략 일관성 체크 실패: %s", e)
            return {"status": "warning", "message": str(e)}

    # #220: 봇 시작 *직전*(예: 1~3일 전) 입금이 cutoff에 걸려 누락되는 케이스가 발견됨
    # (사용자 4/3 입금 100,000원이 4/4 첫 daily_report 기준 cutoff에 막힘). 봇 시작 며칠 전
    # 입금까지 자본금으로 인정해야 손익 계산이 정확. 7일 여유로 풀고, 그래도 옛 입금
    # (수년 전)은 충분히 차단.
    DEPOSIT_SYNC_CUTOFF_BUFFER_DAYS = 7

    def sync_deposits(self, since: str | None = None) -> dict:
        """업비트 입금 내역을 조회해 capital_deposits 테이블에 신규 건만 등록.

        upbit_uuid UNIQUE 제약으로 중복 삽입 방지. 신규 건이 있으면 Slack 알림.

        Args:
            since: ISO datetime 문자열 (예: '2026-04-04'). 이 시각 이전 입금은 무시.
                None이면 첫 daily_reports.date - DEPOSIT_SYNC_CUTOFF_BUFFER_DAYS 를 자동 사용.
                봇 시작 직전 입금까지 자본금으로 잡되, 수년 전 옛 입금은 차단.

        Returns:
            {"status", "fetched", "new", "total_added_krw"}
        """
        try:
            if not self._trader or not self._trader.is_ready:
                return {"status": "ok", "message": "API 미설정 — 스킵"}

            # cutoff 결정
            if since is None:
                first = self._db.execute(
                    "SELECT date FROM daily_reports ORDER BY date ASC LIMIT 1"
                ).fetchone()
                if first:
                    base = dict(first)["date"]
                    # base date - buffer days
                    from datetime import datetime, timedelta
                    base_dt = datetime.fromisoformat(str(base)[:10])
                    since = (base_dt - timedelta(days=self.DEPOSIT_SYNC_CUTOFF_BUFFER_DAYS)).strftime("%Y-%m-%d")
                else:
                    since = "2000-01-01"
            since_str = str(since)[:10]  # 'YYYY-MM-DD'

            history = self._trader.get_deposit_history(currency="KRW", limit=100)
            fetched = len(history)
            if fetched == 0:
                return {"status": "ok", "fetched": 0, "new": 0}

            new_count = 0
            new_total = 0.0
            new_items: list[dict] = []
            skipped_old = 0
            for h in history:
                if not h.get("uuid"):
                    continue
                # cutoff 비교 (deposited_at는 ISO 문자열, 앞 10자리가 날짜)
                dep_date = str(h.get("deposited_at") or "")[:10]
                if dep_date < since_str:
                    skipped_old += 1
                    continue
                # 이미 있는지
                exist = self._db.execute(
                    "SELECT 1 FROM capital_deposits WHERE upbit_uuid = ?", (h["uuid"],)
                ).fetchone()
                if exist:
                    continue
                self._db.execute(
                    """
                    INSERT INTO capital_deposits (currency, amount_krw, deposited_at, source, upbit_uuid)
                    VALUES ('KRW', ?, ?, 'api', ?)
                    """,
                    (h["amount_krw"], h["deposited_at"], h["uuid"]),
                )
                new_count += 1
                new_total += h["amount_krw"]
                new_items.append(h)

            self._db.commit()

            if new_count > 0 and self._notifier:
                lines = [f"💰 *신규 입금 감지* — `{new_count}건` (총 `{new_total:,.0f}원`)"]
                for it in new_items[:5]:
                    lines.append(f">  · {it['deposited_at']}  ·  `{it['amount_krw']:,.0f}원`")
                self._notifier.send("\n".join(lines))
                logger.info("신규 입금 %d건 등록: %.0f원", new_count, new_total)

            return {
                "status": "ok",
                "fetched": fetched,
                "new": new_count,
                "total_added_krw": new_total,
                "skipped_old": skipped_old,
                "since": since_str,
            }
        except Exception as e:
            logger.error("입금 sync 실패: %s", e, exc_info=True)
            return {"status": "warning", "message": str(e)}

    def reconcile_trades(self) -> dict:
        """미검증 거래의 체결 정합성을 검증하고 보정한다.

        order_uuid가 있는 미검증(reconciled=0) 거래를 업비트 API로 확인하여
        실체결가와 DB 기록의 차이가 0.1% 이상이면 DB를 보정한다.

        Returns:
            검증 결과 dict
        """
        try:
            if not self._trader or not self._trader.is_ready:
                return {"status": "ok", "message": "API 미설정 — 스킵"}

            # 미검증 거래 조회 (최근 7일, order_uuid 있는 건)
            rows = self._db.execute(
                """
                SELECT id, coin, side, price, amount, total_krw, fee_krw, order_uuid, buy_trade_id
                FROM trades
                WHERE reconciled = 0
                  AND order_uuid IS NOT NULL
                  AND timestamp >= datetime('now', '-7 days')
                ORDER BY id
                """
            ).fetchall()

            if not rows:
                return {"status": "ok", "checked": 0, "corrected": 0}

            checked = 0
            corrected = 0
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            for row in rows:
                trade = dict(row)
                detail = self._trader.get_order_detail(trade["order_uuid"])
                if not detail:
                    logger.warning("체결 상세 조회 실패 (trade_id=%d)", trade["id"])
                    continue

                checked += 1
                db_price = trade["price"]
                db_total = trade["total_krw"]
                actual_price = detail["price"]
                actual_total = detail["funds"]
                actual_fee = detail["fee"]
                actual_volume = detail["volume"]

                # 오차율 계산
                price_diff = abs(db_price - actual_price) / actual_price if actual_price > 0 else 0
                total_diff = abs(db_total - actual_total) / actual_total if actual_total > 0 else 0

                if price_diff > 0.001 or total_diff > 0.001:
                    # DB 보정
                    self._db.execute(
                        """
                        UPDATE trades
                        SET price = ?, amount = ?, total_krw = ?, fee_krw = ?,
                            reconciled = 2, reconciled_at = ?
                        WHERE id = ?
                        """,
                        (actual_price, actual_volume, actual_total, actual_fee, now_str, trade["id"]),
                    )
                    corrected += 1
                    logger.info(
                        "거래 보정: id=%d %s 가격 %.0f→%.0f 금액 %.0f→%.0f",
                        trade["id"],
                        trade["side"],
                        db_price,
                        actual_price,
                        db_total,
                        actual_total,
                    )

                    # 매도 거래의 profit 재계산
                    if trade["side"] == "sell" and trade["buy_trade_id"]:
                        self._recalculate_profit(trade["id"], trade["buy_trade_id"])
                else:
                    # 일치 확인
                    self._db.execute(
                        "UPDATE trades SET reconciled = 1, reconciled_at = ? WHERE id = ?",
                        (now_str, trade["id"]),
                    )

            self._db.commit()

            result = {"status": "ok", "checked": checked, "corrected": corrected}
            if corrected > 0:
                result["status"] = "warning"
                result["message"] = f"{corrected}건 보정됨 (총 {checked}건 검증)"
                logger.warning("체결 정합성: %d건 보정 / %d건 검증", corrected, checked)
            else:
                logger.info("체결 정합성: %d건 검증 완료 — 이상 없음", checked)

            return result
        except Exception as e:
            logger.error("체결 정합성 검증 실패: %s", e, exc_info=True)
            return {"status": "warning", "message": str(e)}

    def _recalculate_profit(self, sell_trade_id: int, buy_trade_id: int) -> None:
        """보정된 매수/매도 값 기준으로 profit_krw, profit_pct를 재계산한다."""
        try:
            buy = self._db.execute("SELECT total_krw, fee_krw FROM trades WHERE id = ?", (buy_trade_id,)).fetchone()
            sell = self._db.execute("SELECT total_krw, fee_krw FROM trades WHERE id = ?", (sell_trade_id,)).fetchone()

            if not buy or not sell:
                return

            buy = dict(buy)
            sell = dict(sell)
            buy_cost = buy["total_krw"] + (buy["fee_krw"] or 0)
            sell_revenue = sell["total_krw"] - (sell["fee_krw"] or 0)
            profit_krw = round(sell_revenue - buy_cost, 2)
            profit_pct = round(profit_krw / buy_cost * 100, 2) if buy_cost > 0 else 0

            self._db.execute(
                "UPDATE trades SET profit_krw = ?, profit_pct = ? WHERE id = ?",
                (profit_krw, profit_pct, sell_trade_id),
            )
            logger.info("수익 재계산: sell_id=%d profit=%.0f원 (%.2f%%)", sell_trade_id, profit_krw, profit_pct)
        except Exception as e:
            logger.error("수익 재계산 실패 (sell_id=%d): %s", sell_trade_id, e)

    def _check_balance_consistency(self) -> dict:
        """DB 역산 잔고 vs 실제 업비트 KRW 잔고 비교.

        차이 > 2%이면 미검증 거래를 즉시 재보정한 후 재확인.
        재보정 후에도 차이 > 2%이면 Slack 경고.
        """
        try:
            if not self._trader or not self._trader.is_ready:
                return {"status": "ok", "message": "API 미설정 — 스킵"}

            # 실제 업비트 자산 조회
            actual_krw = self._trader.get_balance_krw()

            import pyupbit

            active_rows = self._db.execute(
                """
                SELECT coin, amount FROM trades t
                WHERE side = 'buy'
                AND NOT EXISTS (SELECT 1 FROM trades s WHERE s.buy_trade_id = t.id AND s.side = 'sell')
                """
            ).fetchall()
            coin_value = 0
            for ar in active_rows:
                ad = dict(ar)
                cp = pyupbit.get_current_price(ad["coin"])
                if cp:
                    coin_value += ad["amount"] * cp

            total_actual = actual_krw + coin_value
            logger.info("잔고 검증: KRW=%.0f 코인=%.0f 합계=%.0f", actual_krw, coin_value, total_actual)

            # DB 기준 총자산 역산
            db_total = self._calculate_db_total_asset()

            if total_actual <= 0 or db_total <= 0:
                return {"status": "ok", "krw_balance": actual_krw, "coin_value": coin_value, "total": total_actual}

            diff_pct = abs(total_actual - db_total) / total_actual * 100

            if diff_pct > 2.0:
                logger.warning(
                    "잔고 차이 %.1f%%: 실제=%.0f, DB 역산=%.0f → 미검증 거래 즉시 재보정",
                    diff_pct,
                    total_actual,
                    db_total,
                )
                # 자동 복구: 미검증 거래 재보정
                recon_result = self.reconcile_trades()
                corrected = recon_result.get("corrected", 0)

                if corrected > 0:
                    # 재보정 후 재확인
                    db_total_after = self._calculate_db_total_asset()
                    diff_pct_after = abs(total_actual - db_total_after) / total_actual * 100
                    logger.info(
                        "재보정 후 잔고 차이: %.1f%% → %.1f%% (%d건 보정)",
                        diff_pct,
                        diff_pct_after,
                        corrected,
                    )

                    if diff_pct_after > 2.0:
                        msg = (
                            f"잔고 차이 {diff_pct_after:.1f}%: "
                            f"실제={total_actual:,.0f}원, DB={db_total_after:,.0f}원 "
                            f"({corrected}건 보정 후)"
                        )
                        if self._notifier:
                            self._notifier.send(f"⚠️ *잔고 불일치 경고*\n{msg}")
                        return {"status": "warning", "message": msg, "diff_pct": diff_pct_after}

                    return {
                        "status": "ok",
                        "message": f"자동 보정 완료 ({corrected}건): {diff_pct:.1f}% → {diff_pct_after:.1f}%",
                        "krw_balance": actual_krw,
                        "total": total_actual,
                    }
                else:
                    msg = (
                        f"잔고 차이 {diff_pct:.1f}%: "
                        f"실제={total_actual:,.0f}원, DB={db_total:,.0f}원 (보정 가능 건 없음)"
                    )
                    if self._notifier:
                        self._notifier.send(f"⚠️ *잔고 불일치 경고*\n{msg}")
                    return {"status": "warning", "message": msg, "diff_pct": diff_pct}

            return {"status": "ok", "krw_balance": actual_krw, "coin_value": coin_value, "total": total_actual}
        except Exception as e:
            logger.error("잔고 일관성 체크 실패: %s", e)
            return {"status": "warning", "message": str(e)}

    def _calculate_db_total_asset(self) -> float:
        """DB 기록 기준 총자산을 역산한다."""
        try:
            import pyupbit

            # 매도 수익 합산
            row = self._db.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN side = 'sell' THEN total_krw - fee_krw ELSE 0 END), 0)
                    - COALESCE(SUM(CASE WHEN side = 'buy' THEN total_krw ELSE 0 END), 0)
                    AS net_flow
                FROM trades
                """
            ).fetchone()
            net_flow = row[0] if row else 0

            # 미매도 코인 DB 기록 가치
            active_rows = self._db.execute(
                """
                SELECT coin, amount FROM trades t
                WHERE side = 'buy'
                AND NOT EXISTS (SELECT 1 FROM trades s WHERE s.buy_trade_id = t.id AND s.side = 'sell')
                """
            ).fetchall()
            db_coin_value = 0
            for ar in active_rows:
                ad = dict(ar)
                cp = pyupbit.get_current_price(ad["coin"])
                if cp:
                    db_coin_value += ad["amount"] * cp

            # 총 입금액: capital_deposits 테이블에 등록된 모든 입금 합산.
            # 비어 있으면(레거시) 첫 daily_reports.starting_balance를 fallback.
            dep_row = self._db.execute(
                "SELECT COALESCE(SUM(amount_krw), 0) AS total FROM capital_deposits WHERE currency = 'KRW'"
            ).fetchone()
            total_deposits = dict(dep_row)["total"] if dep_row else 0
            if total_deposits <= 0:
                first_report = self._db.execute(
                    "SELECT starting_balance_krw FROM daily_reports ORDER BY date ASC LIMIT 1"
                ).fetchone()
                total_deposits = dict(first_report)["starting_balance_krw"] if first_report else 0

            return total_deposits + net_flow + db_coin_value
        except Exception as e:
            logger.error("DB 총자산 역산 실패: %s", e)
            return 0

    def _send_alert(self, results: dict) -> None:
        """이상 발견 시 Slack 알림."""
        lines = ["⚠️ *일일 헬스체크 이상 발견*\n"]
        for key in results.get("issues", []):
            detail = results.get(key, {})
            msg = detail.get("message", "상세 정보 없음")
            lines.append(f"• *{key}*: {msg}")

        self._notifier.send("\n".join(lines))

    # ------------------------------------------------------------
    # #195: 4시간 주기 경량 헬스체크 — 외출 중에도 Slack으로 상태 확인
    # ------------------------------------------------------------

    def run_periodic(self) -> dict:
        """4시간 주기 경량 헬스체크. Slack으로 요약 전송.

        run_all(일일)과 달리 빠른 liveness 위주 — 프로세스/DB/최근 활동.
        #255: 매수 임박(80%+) 코인 있으면 별도 Slack 알림.
        """
        import subprocess

        results = {
            "bot_process": self._check_bot_liveness(),
            "api_process": self._check_api_liveness(),
            "news_process": self._check_news_liveness(),
            "db_signals": self._check_recent_signals(),
            "error_logs": self._check_recent_errors(),
            "llm_today": self._check_llm_today_kst(),
            "trading_today": self._check_trading_today_kst(),
        }
        _ = subprocess  # placeholder: 하위 체크에서 실제 사용

        # Slack 전송
        if self._notifier:
            message = self._format_periodic_slack(results)
            self._notifier.send(message)

        # #255: 매수 임박 코인 별도 알림
        try:
            self._check_buy_imminent_and_alert()
        except Exception as e:
            logger.warning("매수 임박 체크 실패: %s", e)

        logger.info("주기 헬스체크 완료")
        return results

    def _check_buy_imminent_and_alert(self) -> None:
        """#255: bb_rsi_combined 매수 조건(RSI≤30 AND 가격<BB하단)에 임박한 코인 알림.

        임박도 = (1 - rsi_gap/30) × 0.5 + (1 - bb_gap/10) × 0.5 (각 0~1 clamp).
        80% 이상이면 Slack 알림. 같은 코인 6시간 1회 제한 (스팸 방지).
        """
        from cryptobot.bot.coin_manager import CoinManager
        # 화이트리스트 8개 + 메이저 위주
        whitelist_row = self._db.execute(
            "SELECT value FROM bot_config WHERE key='coin_whitelist'"
        ).fetchone()
        if whitelist_row and dict(whitelist_row).get("value"):
            coins = [c.strip() for c in dict(whitelist_row)["value"].split(",") if c.strip()]
        else:
            coins = list(CoinManager.DEFAULT_WHITELIST)

        candidates = []
        for coin in coins:
            row = self._db.execute(
                "SELECT price, rsi_14, bb_lower FROM market_snapshots "
                "WHERE coin=? ORDER BY id DESC LIMIT 1", (coin,)
            ).fetchone()
            if not row:
                continue
            d = dict(row)
            rsi = d.get("rsi_14") or 0
            price = d.get("price") or 0
            bb_lower = d.get("bb_lower") or 0
            if not (rsi and price and bb_lower):
                continue
            rsi_gap = max(0, rsi - 30)  # 0이면 충족
            bb_gap_pct = max(0, (price - bb_lower) / bb_lower * 100)
            score = max(0, 1 - rsi_gap / 30) * 0.5 + max(0, 1 - bb_gap_pct / 10) * 0.5
            if score >= 0.80:
                candidates.append({
                    "coin": coin, "rsi": rsi, "bb_gap_pct": bb_gap_pct, "score": score,
                })

        if not candidates:
            return
        candidates.sort(key=lambda c: -c["score"])

        # 6시간 내 같은 코인 알림 중복 방지 — bot_config에 마지막 알림 시각 저장
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        new_alerts = []
        for c in candidates:
            key = f"alert_buy_imminent_{c['coin']}"
            last = self._db.execute(
                "SELECT value FROM bot_config WHERE key=?", (key,)
            ).fetchone()
            if last:
                try:
                    last_ts = datetime.fromisoformat(dict(last)["value"])
                    if last_ts.tzinfo is None:
                        last_ts = last_ts.replace(tzinfo=timezone.utc)
                    if (now - last_ts).total_seconds() < 6 * 3600:
                        continue
                except Exception:
                    pass
            new_alerts.append(c)
            # 알림 시각 기록 (UPSERT)
            self._db.execute(
                "INSERT INTO bot_config (key, value, value_type, category, display_name, description) "
                "VALUES (?, ?, 'datetime', 'alert', '매수 임박 알림 시각', '#255: 6시간 쿨다운') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, now.isoformat()),
            )
        self._db.commit()

        if not new_alerts or not self._notifier:
            return

        lines = ["🎯 *매수 임박 코인* — 곧 매수 가능"]
        for c in new_alerts[:3]:
            short = c["coin"].replace("KRW-", "")
            lines.append(
                f">  *{short}*  ·  RSI `{c['rsi']:.1f}` (한 발자국)"
                f"  ·  BB하단 +`{c['bb_gap_pct']:.2f}%`  ·  임박도 `{int(c['score']*100)}%`"
            )
        self._notifier.send("\n".join(lines))
        logger.info("매수 임박 알림 전송: %d건", len(new_alerts))

    def _check_bot_liveness(self) -> dict:
        """BOT 프로세스 생존성 — 최근 5분 내 market_snapshot 기록 있는지."""
        try:
            row = self._db.execute(
                "SELECT MAX(timestamp) AS last_ts FROM market_snapshots "
                "WHERE timestamp >= datetime('now', '-15 minutes')"
            ).fetchone()
            last = dict(row).get("last_ts") if row else None
            if last:
                # 몇 분 전인지 계산
                gap_row = self._db.execute(
                    "SELECT (julianday('now') - julianday(?)) * 24 * 60 AS gap_min", (last,)
                ).fetchone()
                gap = dict(gap_row)["gap_min"] or 0
                status = "ok" if gap < 5 else "warning"
                return {"status": status, "last_snapshot_min_ago": round(gap, 1)}
            return {"status": "warning", "message": "최근 15분 내 snapshot 없음 — 봇 정지 의심"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _check_api_liveness(self) -> dict:
        """API 서버 self-ping (localhost:8000 /api/health)."""
        try:
            import urllib.error
            import urllib.request

            req = urllib.request.Request("http://localhost:8000/api/health")
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    return {"status": "ok"}
                return {"status": "warning", "message": f"HTTP {resp.status}"}
        except urllib.error.URLError as e:
            return {"status": "warning", "message": f"응답 없음: {e.reason}"}
        except Exception as e:
            return {"status": "warning", "message": str(e)}

    def _check_news_liveness(self) -> dict:
        """뉴스 수집기 생존성 — 최근 2시간 수집 건수."""
        try:
            row = self._db.execute(
                "SELECT COUNT(*) AS cnt FROM news_articles WHERE collected_at >= datetime('now', '-2 hours')"
            ).fetchone()
            cnt = dict(row)["cnt"] or 0
            if cnt == 0:
                return {"status": "warning", "news_count_2h": 0, "message": "2시간 내 수집 0건"}
            return {"status": "ok", "news_count_2h": cnt}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _check_recent_signals(self) -> dict:
        """trade_signals 최근 1시간 누적."""
        try:
            row = self._db.execute(
                "SELECT signal_type, COUNT(*) AS cnt FROM trade_signals "
                "WHERE timestamp >= datetime('now', '-1 hour') GROUP BY signal_type"
            ).fetchall()
            counts = {dict(r)["signal_type"]: dict(r)["cnt"] for r in row}
            total = sum(counts.values())
            status = "ok" if total > 0 else "warning"
            return {"status": status, "total": total, **counts}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _check_recent_errors(self) -> dict:
        """최근 4시간 에러 로그 건수 — logs/error/<date>/*.log grep."""
        try:
            import subprocess
            from pathlib import Path

            log_root = Path("logs/error")
            if not log_root.exists():
                return {"status": "ok", "error_count_4h": 0, "message": "로그 디렉토리 없음"}

            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            log_dir = log_root / today
            if not log_dir.exists():
                return {"status": "ok", "error_count_4h": 0}

            # 최근 4시간 내 수정된 에러 로그 파일 행 수
            count = 0
            for f in log_dir.glob("*.log"):
                try:
                    result = subprocess.run(
                        ["wc", "-l", str(f)],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if result.returncode == 0:
                        n = int(result.stdout.split()[0])
                        count += n
                except Exception:
                    continue

            if count == 0:
                return {"status": "ok", "error_count_4h": 0}
            if count >= 10:
                return {"status": "warning", "error_count_4h": count, "message": f"에러 {count}건"}
            return {"status": "ok", "error_count_4h": count}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _check_llm_today_kst(self) -> dict:
        """오늘(KST) LLM 호출 수 + 비용 + 캐시 hit rate."""
        try:
            row = self._db.execute(
                "SELECT COUNT(*) AS cnt, COALESCE(SUM(cost_usd), 0) AS cost, "
                "COALESCE(SUM(cache_creation_tokens), 0) AS write_tok, "
                "COALESCE(SUM(cache_read_tokens), 0) AS read_tok, "
                "MAX(timestamp) AS last_ts "
                "FROM llm_decisions WHERE DATE(timestamp, '+9 hours') = DATE('now', '+9 hours')"
            ).fetchone()
            d = dict(row)
            total_cache = (d["write_tok"] or 0) + (d["read_tok"] or 0)
            hit_pct = round((d["read_tok"] or 0) / total_cache * 100, 1) if total_cache > 0 else 0
            # 최근 호출 N시간 전
            last_gap_min = None
            if d["last_ts"]:
                gap_row = self._db.execute(
                    "SELECT (julianday('now') - julianday(?)) * 24 * 60 AS gap", (d["last_ts"],)
                ).fetchone()
                last_gap_min = round(dict(gap_row)["gap"] or 0, 1)
            return {
                "status": "ok",
                "calls_today": d["cnt"] or 0,
                "cost_usd": round(d["cost"] or 0, 4),
                "cache_hit_pct": hit_pct,
                "last_call_min_ago": last_gap_min,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _check_trading_today_kst(self) -> dict:
        """오늘(KST) 매매 건수 + 실현 PnL + 보유 포지션.

        #197: 실현 수익률 % 추가 (승률 제거, 사용자는 금일 몇 % 벌었는지가 핵심).
        """
        try:
            trades_row = self._db.execute(
                "SELECT "
                "SUM(CASE WHEN side='buy' THEN 1 ELSE 0 END) AS buys, "
                "SUM(CASE WHEN side='sell' THEN 1 ELSE 0 END) AS sells, "
                "COALESCE(SUM(CASE WHEN side='sell' THEN profit_krw END), 0) AS pnl, "
                "COALESCE(SUM(CASE WHEN side='buy' THEN total_krw END), 0) AS buy_cost "
                "FROM trades WHERE DATE(timestamp, '+9 hours') = DATE('now', '+9 hours')"
            ).fetchone()
            t = dict(trades_row)

            # 보유 포지션
            held_rows = self._db.execute(
                "SELECT DISTINCT coin FROM trades t WHERE side='buy' "
                "AND NOT EXISTS (SELECT 1 FROM trades s WHERE s.buy_trade_id = t.id AND s.side='sell')"
            ).fetchall()
            held = [dict(r)["coin"] for r in held_rows]

            # 실현 수익률 % (대략치): 실현 PnL / 오늘 매수 금액 총합
            realized_pct = 0.0
            buy_cost = t["buy_cost"] or 0
            if buy_cost > 0:
                realized_pct = round((t["pnl"] or 0) / buy_cost * 100, 2)

            return {
                "status": "ok",
                "buys_today": t["buys"] or 0,
                "sells_today": t["sells"] or 0,
                "pnl_today_krw": round(t["pnl"] or 0, 0),
                "pnl_today_pct": realized_pct,
                "held_coins": held,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _format_periodic_slack(self, results: dict) -> str:
        """4시간 체크 결과를 Slack용 텍스트로 포맷.

        #197: 가독성 개선 — Slack mrkdwn 활용 + 금일 손익 % 강조.
        """
        now_kst = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M KST")

        def emoji(status: str) -> str:
            return {"ok": "✅", "warning": "⚠️", "error": "❌"}.get(status, "❔")

        bot = results["bot_process"]
        api = results["api_process"]
        news = results["news_process"]
        sigs = results["db_signals"]
        errs = results["error_logs"]
        llm = results["llm_today"]
        trd = results["trading_today"]

        lines = [
            f"🏥 *시스템 상태 체크* — _{now_kst}_",
            "━━━━━━━━━━━━━━━━━━━",
        ]

        # 프로세스
        lines.append("*🖥️  프로세스*")
        # BOT
        bot_line = f">  {emoji(bot['status'])}  BOT"
        if bot.get("last_snapshot_min_ago") is not None:
            bot_line += f"  ·  마지막 tick `{bot['last_snapshot_min_ago']:.1f}분 전`"
        if bot.get("message"):
            bot_line += f"  ·  _{bot['message']}_"
        lines.append(bot_line)

        # API
        api_line = f">  {emoji(api['status'])}  API"
        if api.get("message"):
            api_line += f"  ·  _{api['message']}_"
        lines.append(api_line)

        # NEWS
        news_line = f">  {emoji(news['status'])}  NEWS"
        if news.get("news_count_2h") is not None:
            news_line += f"  ·  최근 2h `{news['news_count_2h']}건`"
        lines.append(news_line)

        # 매매 (핵심) — 오늘 몇 % 벌었는지
        lines.append("")
        pnl_pct = trd.get("pnl_today_pct", 0)
        pnl_krw = trd.get("pnl_today_krw", 0)
        pnl_icon = "📈" if pnl_pct >= 0 else "📉"
        trend_emoji = "🟢" if pnl_pct >= 0 else "🔴"
        lines.append(f"*{pnl_icon} 오늘 매매 손익*")
        lines.append(f">  {trend_emoji}  `{pnl_pct:+.2f}%`  ({pnl_krw:+,.0f}원)")
        lines.append(f">  체결: 매수 `{trd.get('buys_today', 0)}` · 매도 `{trd.get('sells_today', 0)}`")
        held = trd.get("held_coins", [])
        if held:
            lines.append(f">  보유: {', '.join(c.replace('KRW-', '') for c in held)}")
        else:
            lines.append(">  보유: _없음_")

        # DB 시그널
        lines.append("")
        lines.append("*📡 신호 — 최근 1h*")
        total = sigs.get("total", 0)
        buy_n = sigs.get("buy", 0)
        sell_n = sigs.get("sell", 0)
        hold_n = sigs.get("hold", 0)
        lines.append(
            f">  {emoji(sigs['status'])}  총 `{total}건`  ·  buy `{buy_n}` · sell `{sell_n}` · hold `{hold_n}`"
        )

        # LLM
        lines.append("")
        lines.append("*🤖 LLM — 오늘*")
        calls = llm.get("calls_today", 0)
        cost = llm.get("cost_usd", 0)
        hit = llm.get("cache_hit_pct", 0)
        lines.append(f">  호출 `{calls}/20`  ·  비용 `${cost:.3f}`  ·  캐시 hit `{hit}%`")
        last_gap = llm.get("last_call_min_ago")
        if last_gap is not None:
            lines.append(f">  마지막 분석 `{last_gap:.0f}분 전`")

        # 에러
        lines.append("")
        err_cnt = errs.get("error_count_4h", 0)
        err_line = f"*⚠️ 에러 로그* · {emoji(errs['status'])} `{err_cnt}건`"
        if errs.get("message"):
            err_line += f"\n>  _{errs['message']}_"
        lines.append(err_line)

        return "\n".join(lines)
