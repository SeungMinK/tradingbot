"""CryptoBot 메인 루프.

스케줄러를 초기화하고, 매 틱마다 멀티코인 매매 판단을 실행한다.

사용법:
    python -m cryptobot.bot.main
"""

import logging
import signal
import sys
import time as _time
from datetime import date, datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler

from cryptobot.bot.coin_manager import CoinManager
from cryptobot.bot.config import config
from cryptobot.bot.indicators import calculate_adx, calculate_atr
from cryptobot.bot.config_manager import ConfigManager
from cryptobot.bot.health_checker import HealthChecker
from cryptobot.bot.monthly_audit import MonthlyAudit
from cryptobot.bot.risk import RiskManager
from cryptobot.bot.strategy_selector import StrategySelector
from cryptobot.bot.trader import Trader
from cryptobot.bot.weekly_reporter import WeeklyReporter
from cryptobot.data.database import Database
from cryptobot.data.recorder import DataRecorder
from cryptobot.exceptions import APIError, InsufficientBalanceError
from cryptobot.notifier.slack import SlackNotifier
from cryptobot.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class CryptoBot:
    """메인 봇 클래스."""

    def __init__(self) -> None:
        self._db = Database(config.bot.db_path)
        self._db.initialize()

        self._trader = Trader()
        self._recorder = DataRecorder(self._db)
        self._notifier = SlackNotifier()
        self._risk = RiskManager(self._db)

        self._config_mgr = ConfigManager(self._db)
        self._coin_mgr = CoinManager(self._db, self._config_mgr)
        self._strategy_sel = StrategySelector(self._db, self._config_mgr)

        self._scheduler = BlockingScheduler()
        self._tick_interval = int(self._config_mgr.get("tick_interval_seconds", "60"))
        self._coin_highest_prices: dict[str, float | None] = {}  # 코인별 최고가 추적

    def start(self) -> None:
        """봇 시작."""
        logger.info("=== TradingBot (코인 봇) 시작 ===")
        logger.info("종목: %s (%d개)", ", ".join(self._coin_mgr.active_coins), len(self._coin_mgr.active_coins))
        logger.info("활성 전략: %s", self._strategy_sel.current_strategy_name)
        logger.info("등록 전략: %s", ", ".join(self._strategy_sel.registry.list_names()))
        logger.info(
            "API Key: %s | Slack: %s",
            "O" if self._trader.is_ready else "X",
            "O" if self._notifier.is_configured else "X",
        )

        self._notifier.notify_bot_status("시작됨")
        self._safety_check()

        # Mac sleep/suspend 등으로 스케줄러가 수분간 멈추면 APScheduler 기본 misfire_grace=1초를
        # 초과한 job은 스킵된다(2026-04-19 09:42 periodic_health 스킵 사례). 밀린 job이라도 깨어나면
        # 1회 실행되도록 미스파이어 유예를 크게 + coalesce=True로 중복 누적 방지.
        # tick은 실시간성이 중요하므로 기본값(짧게) 유지, 그 외 주기 job은 관대하게.
        _LAX_MISFIRE = {"misfire_grace_time": 3600, "coalesce": True}

        self._scheduler.add_job(self._tick, "interval", seconds=self._tick_interval, id="main_tick")
        self._scheduler.add_job(self._daily_report, "cron", hour=0, minute=0, id="daily_report", **_LAX_MISFIRE)
        self._scheduler.add_job(self._daily_health_check, "cron", hour=6, minute=0, id="daily_health", **_LAX_MISFIRE)
        # #195: 4시간 주기 경량 헬스체크 — 외출 중에도 Slack으로 상태 확인
        self._scheduler.add_job(
            self._periodic_health_check, "interval", hours=4, id="periodic_health", **_LAX_MISFIRE
        )
        self._scheduler.add_job(
            self._hourly_reconciliation, "interval", hours=1, id="hourly_reconciliation", **_LAX_MISFIRE
        )
        # #206: 일일 입금 sync (06:30) — 일일 헬스체크(06:00) 직후 실행
        self._scheduler.add_job(
            self._daily_deposit_sync, "cron", hour=6, minute=30, id="daily_deposit_sync", **_LAX_MISFIRE
        )
        self._scheduler.add_job(
            self._weekly_report, "cron", day_of_week="sun", hour=3, minute=0, id="weekly_report", **_LAX_MISFIRE
        )
        self._scheduler.add_job(
            self._weekly_backtest,
            "cron",
            day_of_week="sun",
            hour=2,
            minute=0,
            id="weekly_backtest",
            **_LAX_MISFIRE,
        )
        self._scheduler.add_job(
            self._monthly_audit, "cron", day=1, hour=4, minute=0, id="monthly_audit", **_LAX_MISFIRE
        )
        self._scheduler.add_job(self._llm_analyze, "interval", minutes=10, id="llm_analyze", **_LAX_MISFIRE)

        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        logger.info("스케줄러 시작 (%d초 간격)", self._tick_interval)
        try:
            self._scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            self._shutdown()

    def _tick(self) -> None:
        """매 틱 실행."""
        try:
            self._config_mgr.refresh()
            self._strategy_sel.refresh(self._notifier)
            self._coin_mgr.refresh()

            self._risk.limits.max_daily_trades = int(self._config_mgr.get("max_daily_trades", "10"))
            self._risk.limits.max_daily_loss_pct = float(self._config_mgr.get("max_daily_loss_pct", "-7.0"))
            self._risk.limits.max_consecutive_losses = int(self._config_mgr.get("max_consecutive_losses", "3"))
            self._risk.limits.max_position_size_krw = float(self._config_mgr.get("max_position_size_krw", "300000"))
            self._risk.limits.max_daily_account_loss_pct = float(
                self._config_mgr.get("max_daily_account_loss_pct", "-10.0")
            )
            self._risk.limits.coin_reentry_cooldown_minutes = int(
                self._config_mgr.get("coin_reentry_cooldown_minutes", "10")
            )
            self._risk.limits.signal_conflict_buy_confidence_threshold = float(
                self._config_mgr.get("signal_conflict_buy_confidence_threshold", "0.7")
            )
            self._risk.limits.hard_stop_loss_floor_pct = float(
                self._config_mgr.get("hard_stop_loss_floor_pct", "-10.0")
            )
            # #212: ATR 동적 stop_loss
            self._risk.limits.enable_dynamic_stop_loss = (
                self._config_mgr.get("enable_dynamic_stop_loss", "true").lower() == "true"
            )
            self._risk.limits.atr_stop_loss_multiplier = float(
                self._config_mgr.get("atr_stop_loss_multiplier", "2.0")
            )
            self._risk.limits.dynamic_stop_loss_min_abs_pct = float(
                self._config_mgr.get("dynamic_stop_loss_min_abs_pct", "5.0")
            )
            self._risk.limits.dynamic_stop_loss_max_abs_pct = float(
                self._config_mgr.get("dynamic_stop_loss_max_abs_pct", "12.0")
            )
            # #214: ADX 동적 stop_loss
            self._risk.limits.enable_adx_dynamic_stop = (
                self._config_mgr.get("enable_adx_dynamic_stop", "true").lower() == "true"
            )
            self._risk.limits.adx_threshold = float(
                self._config_mgr.get("adx_threshold", "20.0")
            )
            self._risk.limits.adx_low_trend_stop_pct = float(
                self._config_mgr.get("adx_low_trend_stop_pct", "-7.0")
            )
            self._risk.limits.adx_high_trend_stop_pct = float(
                self._config_mgr.get("adx_high_trend_stop_pct", "-3.5")
            )

            new_interval = int(self._config_mgr.get("tick_interval_seconds", "60"))
            if new_interval != self._tick_interval:
                self._scheduler.reschedule_job("main_tick", trigger="interval", seconds=new_interval)
                self._tick_interval = new_interval

            if not self._strategy_sel.current_strategy or not self._config_mgr.get_bool("allow_trading", True):
                return

            # #322: VwapOrbBreakout 활성 시 KST 09:00 EOD 강제 청산
            self._maybe_eod_clearance()

            for i, coin in enumerate(self._coin_mgr.active_coins):
                try:
                    if i > 0:
                        _time.sleep(0.5)
                    self._tick_coin(coin)
                except Exception as e:
                    logger.error("틱 에러 (%s): %s", coin, e, exc_info=True)
        except Exception as e:
            logger.error("틱 실행 에러: %s", e, exc_info=True)
            self._notifier.notify_error(str(e))
        finally:
            try:
                self._db.commit()
            except Exception as e:
                logger.warning("commit 실패: %s", e)

    def _tick_coin(self, coin: str) -> None:
        """개별 코인 매매 판단."""
        collector = self._coin_mgr.collectors.get(coin)
        if not collector:
            return
        snapshot_id = collector.collect_and_save()
        if not snapshot_id:
            return
        snapshot = collector.get_latest_snapshot()
        if not snapshot:
            return

        category = self._coin_mgr.get_category(coin)
        strategy, name = self._strategy_sel.get_coin_strategy(coin, category, self._coin_mgr.collectors)
        if not strategy:
            return

        orig, orig_name = self._strategy_sel.current_strategy, self._strategy_sel.current_strategy_name
        self._strategy_sel.current_strategy = strategy
        self._strategy_sel.current_strategy_name = name
        try:
            # 코인별 최고가 복원 (전략 인스턴스 공유 문제 방지)
            strategy._highest_price = self._coin_highest_prices.get(coin)

            active_trade = self._recorder.get_active_buy_trade(coin)
            if active_trade:
                self._check_and_sell(active_trade, snapshot["price"], snapshot_id, snapshot, coin)
            else:
                self._check_and_buy(snapshot, snapshot["price"], snapshot_id, coin)

            # 코인별 최고가 저장
            self._coin_highest_prices[coin] = strategy._highest_price
        finally:
            # 카테고리별 파라미터 복원 (공유 인스턴스 보호)
            # #186: 복원 중 자체에서도 예외 안전하게 — 각 속성별 try/except
            for attr_restore in (
                ("_orig_stop_loss", "stop_loss_pct"),
                ("_orig_trailing", "trailing_stop_pct"),
                ("_orig_position", "position_size_pct"),
            ):
                marker, field = attr_restore
                if hasattr(strategy, marker):
                    try:
                        setattr(strategy.params, field, getattr(strategy, marker))
                    finally:
                        try:
                            delattr(strategy, marker)
                        except AttributeError:
                            pass
            # #152: 코인별 assignment_params로 오버라이드한 extra 복원
            if hasattr(strategy, "_orig_extra"):
                try:
                    strategy.params.extra = strategy._orig_extra
                finally:
                    try:
                        delattr(strategy, "_orig_extra")
                    except AttributeError:
                        pass
            self._strategy_sel.current_strategy = orig
            self._strategy_sel.current_strategy_name = orig_name

    def _calc_adx_dynamic_stop_loss_pct(self, df) -> float | None:
        """#214: ADX 기반 동적 stop_loss(%) 계산.

        ADX < threshold (약한 추세, 횡보) → 넓은 stop (노이즈 흡수)
        ADX ≥ threshold (강한 추세) → 좁은 stop (잘못된 방향 빠른 손절)

        백테스트 검증 효과 (4전략 평균 양수). ATR 동적(#212)보다 우수.
        """
        limits = self._risk.limits
        if not limits.enable_adx_dynamic_stop or df is None:
            return None
        try:
            adx = calculate_adx(df["high"], df["low"], df["close"], period=limits.adx_period)
        except Exception as e:
            logger.debug("ADX 계산 실패: %s", e)
            return None
        if adx is None:
            return None
        return limits.adx_low_trend_stop_pct if adx < limits.adx_threshold else limits.adx_high_trend_stop_pct

    def _calc_dynamic_stop_loss_pct(self, df, current_price: float) -> float | None:
        """#212: ATR 기반 동적 stop_loss(%) 계산.

        ATR(N) / 현재가 × 100 = 변동성 % → multiplier 곱해 stop 폭 결정.
        clamp [-max_abs, -min_abs]. ATR 계산 실패하거나 enable=False면 None 반환 (호출측 fallback).
        """
        limits = self._risk.limits
        if not limits.enable_dynamic_stop_loss or df is None or current_price <= 0:
            return None
        try:
            atr = calculate_atr(df["high"], df["low"], df["close"], period=limits.atr_period)
        except Exception as e:
            logger.debug("ATR 계산 실패: %s", e)
            return None
        if atr is None or atr <= 0:
            return None
        atr_pct = atr / current_price * 100
        dynamic = -atr_pct * limits.atr_stop_loss_multiplier
        # clamp 음수 영역: -max_abs ≤ dynamic ≤ -min_abs
        return max(-limits.dynamic_stop_loss_max_abs_pct, min(-limits.dynamic_stop_loss_min_abs_pct, dynamic))

    def _check_and_buy(self, snapshot, price, snapshot_id, coin=None):
        """매수 신호 확인 및 실행."""
        coin = coin or config.bot.coin
        s = self._strategy_sel.current_strategy
        sn = self._strategy_sel.current_strategy_name
        collector = self._coin_mgr.collectors.get(coin)
        df = collector.latest_df if collector else None
        if df is None or s is None:
            return

        # #322: VwapOrbBreakout는 분봉 + 오늘 자정 이후 봉 필터 필요
        from cryptobot.strategies.vwap_orb_breakout import VwapOrbBreakout, filter_today_bars
        if isinstance(s, VwapOrbBreakout):
            try:
                import pyupbit
                minute_df = pyupbit.get_ohlcv(coin, interval="minute15", count=100)
                if minute_df is not None and len(minute_df) > 0:
                    minute_df = filter_today_bars(minute_df)
                    sig = s.check_buy(minute_df, price)
                else:
                    sig = s.check_buy(df, price)  # 폴백
            except Exception as e:
                logger.warning("VwapOrbBreakout 분봉 fetch 실패: %s — 일봉 폴백", e)
                sig = s.check_buy(df, price)
        else:
            sig = s.check_buy(df, price)
        pj = self._config_mgr.get_strategy_params_json(sn)

        if sig.signal_type != "buy":
            self._recorder.record_signal(
                coin=coin,
                signal_type=sig.signal_type,
                strategy=sn,
                confidence=sig.confidence,
                trigger_reason=sig.reason,
                current_price=price,
                trigger_value=sig.trigger_value,
                skip_reason=sig.reason,
                snapshot_id=snapshot_id,
                strategy_params_json=pj,
            )
            return
        if not self._trader.is_ready:
            self._recorder.record_signal(
                coin=coin,
                signal_type="buy",
                strategy=sn,
                confidence=sig.confidence,
                trigger_reason=sig.reason,
                current_price=price,
                trigger_value=sig.trigger_value,
                skip_reason="api_key_not_configured",
                snapshot_id=snapshot_id,
                strategy_params_json=pj,
            )
            return

        bal = self._trader.get_balance_krw()
        # 계좌 전체 일일 손실 한도 체크 — 매수만 차단, 매도는 영향 없음
        ok_acct, reason_acct = self._risk.check_account_daily_loss(bal)
        if not ok_acct:
            self._recorder.record_signal(
                coin=coin,
                signal_type="buy",
                strategy=sn,
                confidence=sig.confidence,
                trigger_reason=sig.reason,
                current_price=price,
                trigger_value=sig.trigger_value,
                skip_reason=reason_acct,
                snapshot_id=snapshot_id,
                strategy_params_json=pj,
            )
            return

        max_pct = float(self._config_mgr.get("max_position_per_coin_pct", "25"))
        avail = min(bal * max_pct / 100, bal - self._risk.limits.min_balance_krw)
        if avail <= 0:
            return
        ratio = max(0, min(sig.confidence, 1)) * max(0, min(s.params.position_size_pct, 100)) / 100
        amount = min(avail * ratio, self._risk.limits.max_position_size_krw)

        ok, reason = self._risk.check_can_buy(coin, amount, bal)
        if not ok:
            self._recorder.record_signal(
                coin=coin,
                signal_type="buy",
                strategy=sn,
                confidence=sig.confidence,
                trigger_reason=sig.reason,
                current_price=price,
                trigger_value=sig.trigger_value,
                skip_reason=reason,
                snapshot_id=snapshot_id,
                strategy_params_json=pj,
            )
            return

        # 중복 매수 방지 — 매수 직전 재확인
        if self._recorder.get_active_buy_trade(coin):
            logger.warning("중복 매수 방지: %s 이미 보유 중", coin)
            return

        bal_before = bal  # 잔고 스냅샷
        # 주문 실행 — APIError가 중간에 나면 주문이 접수됐을 수도 있으니 긴급 알림 필수
        try:
            order = self._trader.buy_market(coin, amount)
        except (APIError, InsufficientBalanceError) as e:
            logger.error("매수 API 실패: %s %s원 — %s", coin, f"{amount:,.0f}", e)
            self._notifier.notify_error(
                f"⚠️ 매수 주문 API 실패: {coin} {amount:,.0f}원 — 접수 후 체결 조회 실패 가능성. "
                f"Upbit에서 수동 확인 필요.\n{e}"
            )
            skip = f"API 예외: {type(e).__name__}"
            self._recorder.record_signal(
                coin=coin,
                signal_type="buy",
                strategy=sn,
                confidence=sig.confidence,
                trigger_reason=sig.reason,
                current_price=price,
                trigger_value=sig.trigger_value,
                skip_reason=skip,
                snapshot_id=snapshot_id,
                strategy_params_json=pj,
            )
            return
        if order.success:
            # #214 우선: ADX 기반 동적 stop (검증된 +효과). 비활성/실패 시 #212 ATR fallback.
            adx_stop = self._calc_adx_dynamic_stop_loss_pct(df)
            atr_stop = self._calc_dynamic_stop_loss_pct(df, order.price)
            dyn_stop = adx_stop if adx_stop is not None else atr_stop
            stop_to_save = dyn_stop if dyn_stop is not None else s.params.stop_loss_pct
            if dyn_stop is not None and abs(dyn_stop - s.params.stop_loss_pct) >= 0.5:
                source = "ADX" if adx_stop is not None else "ATR"
                logger.info(
                    "[%s] 동적 stop_loss: %.1f%% (기본 %.1f%%) — %s 기반",
                    coin, dyn_stop, s.params.stop_loss_pct, source,
                )
            tid = self._recorder.record_trade(
                coin=coin,
                side="buy",
                price=order.price,
                amount=order.amount,
                total_krw=order.total_krw,
                fee_krw=order.fee_krw,
                strategy=sn,
                trigger_reason=sig.reason,
                trigger_value=sig.trigger_value,
                param_k_value=s.params.extra.get("k_value"),
                param_stop_loss=stop_to_save,
                param_trailing_stop=s.params.trailing_stop_pct,
                market_state_at_trade=snapshot.get("market_state"),
                btc_price_at_trade=price,
                rsi_at_trade=snapshot.get("rsi_14"),
                order_uuid=order.order_uuid,
            )
            # 즉시 commit — 다음 틱이 이 buy를 찾지 못해 중복 매수하는 것을 방지
            try:
                self._db.commit()
            except Exception as ce:
                logger.critical("매수 trade commit 실패: coin=%s tid=%s — %s", coin, tid, ce)
                self._notifier.notify_error(f"🚨 DB commit 실패 (매수): {coin} — 수동 확인 필수")
                raise
            # DB 쓰기 검증
            verify = self._db.execute("SELECT id FROM trades WHERE id = ?", (tid,)).fetchone()
            if not verify:
                logger.error("DB 쓰기 검증 실패: trade_id=%s", tid)
                self._notifier.notify_error(f"DB 쓰기 검증 실패: {coin} 매수 기록 누락")
            # 잔고 일관성 체크
            bal_after = self._trader.get_balance_krw() if self._trader.is_ready else bal_before
            expected_diff = order.total_krw
            actual_diff = bal_before - bal_after
            if abs(actual_diff - expected_diff) > expected_diff * 0.05:
                logger.warning("잔고 불일치: 예상 -%s, 실제 -%s", f"{expected_diff:,.0f}", f"{actual_diff:,.0f}")
            self._recorder.record_signal(
                coin=coin,
                signal_type="buy",
                strategy=sn,
                confidence=sig.confidence,
                trigger_reason=sig.reason,
                current_price=price,
                trigger_value=sig.trigger_value,
                executed=True,
                trade_id=tid,
                snapshot_id=snapshot_id,
                strategy_params_json=pj,
            )
        else:
            # 사전 검증 실패 (최소 주문 금액 미달 등) — 기록만
            self._recorder.record_signal(
                coin=coin,
                signal_type="buy",
                strategy=sn,
                confidence=sig.confidence,
                trigger_reason=sig.reason,
                current_price=price,
                trigger_value=sig.trigger_value,
                skip_reason=order.error or "주문 실패",
                snapshot_id=snapshot_id,
                strategy_params_json=pj,
            )

    def _maybe_eod_clearance(self) -> None:
        """#322: vwap_orb_breakout 활성 시 KST 09:00 ±5분에 보유 코인 강제 매도.

        Zarattini 논문 EOD 청산 — 24/7 코인 시장에선 사용자 정의 시점(KST 09:00).
        """
        from cryptobot.strategies.vwap_orb_breakout import VwapOrbBreakout, is_eod_window

        s = self._strategy_sel.current_strategy
        if not isinstance(s, VwapOrbBreakout):
            return
        if not is_eod_window():
            return

        if not self._trader.is_ready:
            logger.warning("EOD 청산 — Trader 미준비, 스킵")
            return

        # 보유 중인 코인 SQL 직접 조회 (sell 매칭 안 된 buy)
        active_buys = self._db.execute(
            "SELECT id, coin, price, amount FROM trades "
            "WHERE side='buy' AND market='upbit' "
            "AND NOT EXISTS (SELECT 1 FROM trades s WHERE s.buy_trade_id=trades.id AND s.side='sell')"
        ).fetchall()
        if not active_buys:
            return

        logger.info("[EOD 청산] 보유 %d종목 청산 시작 (KST 09:00)", len(active_buys))

        for trade in active_buys:
            coin = trade["coin"]
            try:
                amount = self._trader.get_balance_coin(coin) or 0
                if amount <= 0:
                    continue
                order = self._trader.sell_market(coin, amount)
                if order and order.success:
                    buy_price = trade["price"] or 0
                    pnl_pct = ((order.price - buy_price) / buy_price * 100) if buy_price > 0 else 0.0
                    self._recorder.record_trade(
                        coin=coin, market="upbit", side="sell",
                        price=order.price, amount=order.amount,
                        total_krw=order.total_krw, fee_krw=order.fee_krw,
                        strategy="vwap_orb_breakout",
                        trigger_reason="EOD 청산 (KST 09:00)",
                        profit_pct=pnl_pct, buy_trade_id=trade["id"],
                    )
                    logger.info("[EOD] %s 매도 @ %.2f (%.2f%%)", coin, order.price, pnl_pct)
                    if self._notifier.is_configured:
                        self._notifier.notify_trade(
                            f"[EOD] {coin} 매도 @ {order.price:.0f} ({pnl_pct:+.2f}%)"
                        )
            except Exception as e:
                logger.exception("EOD 청산 실패 (%s): %s", coin, e)

    def _check_and_sell(self, active_trade, price, snapshot_id, snapshot=None, coin=None):
        """매도 신호 확인 및 실행."""
        coin = coin or config.bot.coin
        s = self._strategy_sel.current_strategy
        sn = self._strategy_sel.current_strategy_name
        collector = self._coin_mgr.collectors.get(coin)
        df = collector.latest_df if collector else None
        if df is None or s is None:
            return

        buy_price = active_trade["price"]
        buy_time = datetime.fromisoformat(active_trade["timestamp"])
        if buy_time.tzinfo is None:
            buy_time = buy_time.replace(tzinfo=timezone.utc)
        s._hold_minutes = int((datetime.now(timezone.utc) - buy_time).total_seconds() / 60)

        # #212: 매수 시 저장된 동적 stop_loss를 strategy.params에 일시 override.
        # 코인별 ATR로 결정된 폭이 적용되어 변동성에 맞는 손절선이 작동.
        # finally에서 _orig_stop_loss로 복원 (#152 패턴 활용).
        saved_stop = active_trade.get("param_stop_loss")
        if saved_stop is not None and not hasattr(s, "_orig_stop_loss"):
            s._orig_stop_loss = s.params.stop_loss_pct
            s.params.stop_loss_pct = float(saved_stop)

        sig = s.check_sell(df, price, buy_price)
        pj = self._config_mgr.get_strategy_params_json(sn)

        if sig.signal_type != "sell":
            self._recorder.record_signal(
                coin=coin,
                signal_type=sig.signal_type,
                strategy=sn,
                confidence=sig.confidence,
                trigger_reason=sig.reason,
                current_price=price,
                trigger_value=sig.trigger_value,
                skip_reason=sig.reason,
                snapshot_id=snapshot_id,
                strategy_params_json=pj,
            )
            return
        if not self._trader.is_ready:
            return

        pnl_pct = (price - buy_price) / buy_price * 100
        net_pnl = pnl_pct - BaseStrategy.ROUND_TRIP_FEE_PCT

        # #210: 손절 vs 매수 신호 충돌 통합 의사결정 ("더 높은 신호 채택").
        # ALGO처럼 손절 직후 1분 뒤 RSI 매수 신호로 재매수하는 패턴은 같은 가격 사건에 대한
        # 모순적 결정. 손절 시점에 매수 신호 강도가 임계 이상이면 손절 보류 ("들고 간다").
        # 단 pnl <= hard_floor면 안전장치로 무조건 손절.
        if not sig.is_profit_taking and "손절" in sig.reason:
            hard_floor = self._risk.limits.hard_stop_loss_floor_pct
            if pnl_pct > hard_floor:
                buy_sig = s.check_buy(df, price)
                conf_threshold = self._risk.limits.signal_conflict_buy_confidence_threshold
                if buy_sig.signal_type == "buy" and buy_sig.confidence >= conf_threshold:
                    skip_msg = (
                        f"손절-매수 충돌: 손절 -{abs(pnl_pct):.2f}% vs "
                        f"매수 confidence {buy_sig.confidence:.2f} ≥ {conf_threshold:.2f} → 보유 유지"
                    )
                    logger.info("[%s] %s", coin, skip_msg)
                    self._recorder.record_signal(
                        coin=coin,
                        signal_type="hold",
                        strategy=sn,
                        confidence=buy_sig.confidence,
                        trigger_reason=f"신호 충돌: {sig.reason} vs {buy_sig.reason}",
                        current_price=price,
                        trigger_value=pnl_pct,
                        skip_reason=skip_msg,
                        snapshot_id=snapshot_id,
                        strategy_params_json=pj,
                    )
                    if self._notifier and self._notifier.is_configured:
                        self._notifier.send(
                            f"⚖️ *손절-매수 충돌 보류* — {coin}\n"
                            f">  손절 {pnl_pct:+.2f}% vs 매수 conf {buy_sig.confidence:.2f}\n"
                            f">  사유: {buy_sig.reason}"
                        )
                    return

        # 수수료 가드: Signal에 명시된 is_profit_taking 플래그 기반 (이전엔 reason 문자열 매칭 취약).
        # 익절 신호(ROI/트레일링/중간선 등)만 수수료로 인한 실질 음수 시 차단.
        # 손절/전략 판단(RSI 정상복귀, 데드크로스 등)은 통과.
        if sig.is_profit_taking and net_pnl <= 0:
            self._recorder.record_signal(
                coin=coin,
                signal_type="sell",
                strategy=sn,
                confidence=sig.confidence,
                trigger_reason=sig.reason,
                current_price=price,
                trigger_value=sig.trigger_value,
                skip_reason=f"수수료 가드: 가격 {pnl_pct:+.2f}% 실질 {net_pnl:+.2f}%",
                snapshot_id=snapshot_id,
                strategy_params_json=pj,
            )
            return

        try:
            order = self._trader.sell_market(coin)
        except (APIError, InsufficientBalanceError) as e:
            logger.error("매도 API 실패: %s — %s", coin, e)
            self._notifier.notify_error(
                f"⚠️ 매도 주문 API 실패: {coin} — 접수 후 체결 조회 실패 가능성. Upbit에서 수동 확인 필요.\n{e}"
            )
            skip = f"API 예외: {type(e).__name__}"
            self._recorder.record_signal(
                coin=coin,
                signal_type="sell",
                strategy=sn,
                confidence=sig.confidence,
                trigger_reason=sig.reason,
                current_price=price,
                trigger_value=sig.trigger_value,
                skip_reason=skip,
                snapshot_id=snapshot_id,
                strategy_params_json=pj,
            )
            return
        if order.success:
            bf = active_trade.get("fee_krw") or 0
            profit_krw = round((order.total_krw - order.fee_krw) - (active_trade["total_krw"] + bf), 2)
            profit_pct = (
                round(profit_krw / (active_trade["total_krw"] + bf) * 100, 2)
                if (active_trade["total_krw"] + bf) > 0
                else 0
            )
            tid = self._recorder.record_trade(
                coin=coin,
                side="sell",
                price=order.price,
                amount=order.amount,
                total_krw=order.total_krw,
                fee_krw=order.fee_krw,
                strategy=sn,
                trigger_reason=sig.reason,
                trigger_value=sig.trigger_value,
                param_k_value=s.params.extra.get("k_value"),
                param_stop_loss=s.params.stop_loss_pct,
                param_trailing_stop=s.params.trailing_stop_pct,
                buy_trade_id=active_trade["id"],
                profit_pct=profit_pct,
                profit_krw=profit_krw,
                hold_duration_minutes=s._hold_minutes,
                order_uuid=order.order_uuid,
            )
            # 즉시 commit — 미커밋으로 다음 틱이 이 매도를 놓치면 이중 매도 위험
            try:
                self._db.commit()
            except Exception as ce:
                logger.critical("매도 trade commit 실패: coin=%s tid=%s — %s", coin, tid, ce)
                self._notifier.notify_error(f"🚨 DB commit 실패 (매도): {coin} — 수동 확인 필수")
                raise
            # DB 쓰기 검증
            verify = self._db.execute("SELECT id FROM trades WHERE id = ?", (tid,)).fetchone()
            if not verify:
                logger.error("DB 쓰기 검증 실패: trade_id=%s", tid)
                self._notifier.notify_error(f"DB 쓰기 검증 실패: {coin} 매도 기록 누락")
            self._recorder.record_signal(
                coin=coin,
                signal_type="sell",
                strategy=sn,
                confidence=sig.confidence,
                trigger_reason=sig.reason,
                current_price=price,
                trigger_value=sig.trigger_value,
                executed=True,
                trade_id=tid,
                snapshot_id=snapshot_id,
                strategy_params_json=pj,
            )
            s.reset()
        else:
            self._recorder.record_signal(
                coin=coin,
                signal_type="sell",
                strategy=sn,
                confidence=sig.confidence,
                trigger_reason=sig.reason,
                current_price=price,
                trigger_value=sig.trigger_value,
                skip_reason=order.error or "주문 실패",
                snapshot_id=snapshot_id,
                strategy_params_json=pj,
            )

    def _llm_analyze(self):
        try:
            from cryptobot.llm.analyzer import LLMAnalyzer

            a = LLMAnalyzer(self._db)
            if not a.is_configured:
                return
            # 시장 급변 감지 → 즉시 분석
            force = a.check_emergency()
            r = a.analyze(force=force)
            if r:
                self._config_mgr.refresh()
                self._strategy_sel.refresh(self._notifier)
                # 전략 적용 검증
                recommended = r.get("recommended_strategy")
                if recommended and recommended != self._strategy_sel.current_strategy_name:
                    logger.warning(
                        "전략 불일치: LLM 추천=%s, 실제=%s",
                        recommended,
                        self._strategy_sel.current_strategy_name,
                    )
                    self._notifier.notify_error(
                        f"전략 불일치: 추천={recommended}, 실제={self._strategy_sel.current_strategy_name}"
                    )
        except Exception as e:
            logger.error("LLM 에러: %s", e, exc_info=True)

    def _daily_report(self):
        try:
            import pyupbit

            today = date.today()
            trades = self._recorder.get_today_trades()  # 전체 코인
            sells = [t for t in trades if t["side"] == "sell"]
            buys = [t for t in trades if t["side"] == "buy"]
            wins = [t for t in sells if (t.get("profit_pct") or 0) > 0]
            losses = [t for t in sells if (t.get("profit_pct") or 0) <= 0]
            wr = (len(wins) / len(sells) * 100) if sells else 0

            # 실제 자산 가치 계산 (KRW + 보유 코인)
            krw = self._trader.get_balance_krw() if self._trader.is_ready else 0
            coin_value = 0
            unrealized = 0
            for coin in self._coin_mgr.active_coins:
                active = self._recorder.get_active_buy_trade(coin)
                if active:
                    cp = pyupbit.get_current_price(coin)
                    if cp:
                        val = active["amount"] * cp
                        coin_value += val
                        unrealized += val - active["total_krw"]

            total_asset = krw + coin_value
            realized = sum(t.get("profit_krw", 0) or 0 for t in sells)
            total_fees = sum(t.get("fee_krw", 0) or 0 for t in trades)

            avg_profit = sum(t.get("profit_pct", 0) or 0 for t in wins) / len(wins) if wins else 0
            avg_loss = sum(t.get("profit_pct", 0) or 0 for t in losses) / len(losses) if losses else 0

            self._recorder.save_daily_report(
                report_date=today,
                starting_balance=total_asset,
                ending_balance=total_asset,
                total_asset_value=total_asset,
                realized_pnl=realized,
                unrealized_pnl=round(unrealized, 2),
                trades_summary={
                    "total": len(trades),
                    "buys": len(buys),
                    "sells": len(sells),
                    "wins": len(wins),
                    "losses": len(losses),
                    "win_rate": round(wr, 1),
                    "avg_profit_pct": round(avg_profit, 2),
                    "avg_loss_pct": round(avg_loss, 2),
                    "total_fees": round(total_fees, 2),
                },
            )

            if self._config_mgr.get_bool("slack_daily_report", True):
                # #197: 승률 제거 + 금일 실현 수익률 % 계산 (시작 자산 기준)
                # 시작 자산 = 현재 총자산 - 실현 PnL - 미실현 PnL
                start_asset = total_asset - realized - unrealized
                realized_pct = (realized / start_asset * 100) if start_asset > 0 else 0
                self._notifier.notify_daily_report(
                    date_str=today.isoformat(),
                    realized_pnl_pct=round(realized_pct, 2),
                    realized_pnl_krw=round(realized, 0),
                    unrealized_pnl_krw=round(unrealized, 0),
                    total_asset_krw=round(total_asset, 0),
                    total_trades=len(trades),
                )
        except Exception as e:
            logger.error("일일 정산 에러: %s", e, exc_info=True)

    def _daily_health_check(self):
        """일일 헬스체크 (06:00)."""
        try:
            checker = HealthChecker(self._db, self._trader, self._notifier)
            checker.run_all()
        except Exception as e:
            logger.error("헬스체크 에러: %s", e, exc_info=True)

    def _periodic_health_check(self):
        """#195: 4시간 주기 경량 헬스체크 — 외부에서도 Slack으로 상태 확인."""
        try:
            checker = HealthChecker(self._db, self._trader, self._notifier)
            checker.run_periodic()
        except Exception as e:
            logger.error("주기 헬스체크 에러: %s", e, exc_info=True)

    def _daily_deposit_sync(self):
        """#206: 매일 1회 업비트 입금 내역 sync — 신규 입금 자동 등록 + Slack 알림."""
        try:
            checker = HealthChecker(self._db, self._trader, self._notifier)
            checker.sync_deposits()
        except Exception as e:
            logger.error("입금 sync 에러: %s", e, exc_info=True)

    def _hourly_reconciliation(self):
        """매시간 체결 정합성 검증."""
        try:
            checker = HealthChecker(self._db, self._trader, self._notifier)
            checker.reconcile_trades()
        except Exception as e:
            logger.error("체결 정합성 검증 에러: %s", e, exc_info=True)

    def _weekly_report(self):
        """주간 리포트 (일요일 03:00)."""
        try:
            reporter = WeeklyReporter(self._db, self._notifier)
            reporter.run_all()
        except Exception as e:
            logger.error("주간 리포트 에러: %s", e, exc_info=True)

    def _weekly_backtest(self):
        """주간 백테스트 (일요일 02:00)."""
        try:
            from cryptobot.backtest.reporter import BacktestReporter

            reporter = BacktestReporter(self._db, config.bot.db_path, self._notifier)
            reporter.run_all()
        except Exception as e:
            logger.error("주간 백테스트 에러: %s", e, exc_info=True)

    def _monthly_audit(self):
        """월간 감사 (매월 1일 04:00)."""
        try:
            audit = MonthlyAudit(self._db, config.bot.db_path, self._notifier)
            audit.run_all()
        except Exception as e:
            logger.error("월간 감사 에러: %s", e, exc_info=True)

    def _safety_check(self):
        if self._trader.is_ready:
            for coin in self._coin_mgr.active_coins:
                c = self._trader.cancel_all_orders(coin)
                if c > 0:
                    logger.info("미체결 주문 %d건 취소 (%s)", c, coin)

    def _shutdown(self, *args):
        logger.info("=== TradingBot (코인 봇) 종료 ===")
        self._notifier.notify_bot_status("종료됨")
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        self._db.close()
        sys.exit(0)


def main():
    from cryptobot.logging_config import setup_logging

    setup_logging("bot", config.bot.log_level)
    CryptoBot().start()


if __name__ == "__main__":
    main()
