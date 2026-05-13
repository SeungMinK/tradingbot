"""#393: KIS US 봇 Slack 통합 보고 메시지 포매터.

구성:
- format_market_open(): 장 시작 알림
- format_buy(): 매수 체결 알림
- format_sell(): 매도 알림 (손절/EOD 익절/EOD 손실 분기)
- format_daily_summary(): 일일 결산 (매매 있는/없는 날 모두)

사용자 가독성 최우선 — 이모지 + 정렬 + 핵심 정보 highlight.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")


def _now_kst() -> datetime:
    return datetime.now(KST)


def _now_ny() -> datetime:
    return datetime.now(NY)


def format_market_open(
    universe: list[str],
    usd_available: float,
    fx_krw_per_usd: float,
    strategy: str = "Zarattini Pure 3X",
) -> str:
    """장 시작 알림 — NY 09:30 직후 1회."""
    coins_str = " · ".join(f"`{s}`" for s in universe)
    krw_eq = int(usd_available * fx_krw_per_usd)
    return (
        f"🇺🇸 *KIS 미국주식 봇 — 장 시작!*\n"
        f"\n"
        f"📊 *오늘 운영*\n"
        f"• 종목: {coins_str}\n"
        f"• 가용: *${usd_available:,.2f}* (≈ ₩{krw_eq:,})\n"
        f"• 전략: {strategy}\n"
        f"• 손절: ATR × 5% 동적\n"
        f"\n"
        f"⏰ *주요 시점*\n"
        f"• 진입: NY 09:35 (KST 22:35)\n"
        f"• EOD: NY 15:50 (KST 04:50)\n"
        f"\n"
        f"_좋은 매매 되세요_ 🍀"
    )


def format_buy(
    symbol: str,
    qty: float,
    price: float,
    signal_reason: str,
    stop_loss_price: float | None = None,
    risk_usd: float | None = None,
    account_usd: float | None = None,
) -> str:
    """매수 체결 알림."""
    qty_str = f"{int(qty)}주" if qty == int(qty) else f"{qty:.4f}주"
    total = qty * price
    now_kst = _now_kst()
    now_ny = _now_ny()

    lines = [
        f"✅ *매수 체결* — `{symbol}`",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💰 *{qty_str}* × ${price:.2f} = *${total:,.2f}*",
        f"📈 시그널: {signal_reason[:80]}",
    ]
    if stop_loss_price is not None and stop_loss_price > 0:
        stop_pct = (stop_loss_price - price) / price * 100
        lines.append(f"🛡 손절선: ${stop_loss_price:.2f} ({stop_pct:+.2f}%)")
    if risk_usd is not None and account_usd is not None and account_usd > 0:
        risk_pct = risk_usd / account_usd * 100
        lines.append(f"⚖️ 최대 리스크: ${risk_usd:.2f} (계좌 {risk_pct:.1f}%)")
    lines.append("")
    lines.append(f"⏱ {now_kst.strftime('%H:%M:%S')} KST · NY {now_ny.strftime('%H:%M:%S')}")
    return "\n".join(lines)


def format_sell(
    symbol: str,
    qty: float,
    price: float,
    pnl_pct: float,
    pnl_usd: float,
    hold_minutes: int,
    sell_type: str,  # "stop_loss" / "eod_profit" / "eod_loss"
    is_one_per_day: bool = True,
) -> str:
    """매도 알림 — 손절/EOD 익절/EOD 손실 케이스별."""
    qty_str = f"{int(qty)}주" if qty == int(qty) else f"{qty:.4f}주"
    hold_str = _format_hold_duration(hold_minutes)

    if sell_type == "stop_loss":
        header = f"🔴 *손절* — `{symbol}`"
        result_emoji = "📉" if pnl_pct < 0 else "📈"
        result_line = f"{result_emoji} *결과*: *{pnl_usd:+.2f}$* ({pnl_pct:+.2f}%) 😔"
        extra = (
            f"⏱ 보유 {hold_str}\n"
            f"🛡 ATR × 5% stop hit\n"
            f"\n"
            f"🔒 오늘 `{symbol}` 매매 종료 (1일 1회 룰)"
        )
    elif sell_type == "eod_profit":
        header = f"🎉 *EOD 익절* — `{symbol}`"
        result_line = f"📈 *결과*: *+${pnl_usd:.2f}* (+{pnl_pct:.2f}%) ✨"
        extra = (
            f"⏱ 보유 {hold_str}\n"
            f"🏁 마감 10분 전 강제 청산"
        )
    elif sell_type == "eod_loss":
        header = f"🟡 *EOD 청산 (손실)* — `{symbol}`"
        result_line = f"📉 *결과*: *${pnl_usd:.2f}* ({pnl_pct:.2f}%)"
        extra = (
            f"⏱ 보유 {hold_str}\n"
            f"🏁 손절선 안 닿았지만 마감 청산"
        )
    else:
        header = f"💼 *매도* — `{symbol}`"
        result_line = f"📊 결과: ${pnl_usd:+.2f} ({pnl_pct:+.2f}%)"
        extra = f"⏱ 보유 {hold_str}"

    return (
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{result_line}\n"
        f"💰 매도가: ${price:.2f} × {qty_str}\n"
        f"{extra}"
    )


def format_daily_summary(
    today_trades: list[dict],
    skip_reasons: dict[str, str],  # symbol -> "도지" / "음봉" 등
    usd_now: float,
    usd_start_of_day: float,
    week_pnl_usd: float,
    week_trade_days: int,
    month_pnl_usd: float,
    total_pnl_usd: float | None = None,
) -> str:
    """일일 결산 — 매매 있는 날/없는 날 모두 표시."""
    ny_now = _now_ny()
    date_str = ny_now.strftime("%Y-%m-%d (%a)").replace(
        "Mon", "월").replace("Tue", "화").replace("Wed", "수").replace(
        "Thu", "목").replace("Fri", "금").replace("Sat", "토").replace("Sun", "일")
    next_day_kst = (_now_kst().replace(hour=22, minute=30, second=0, microsecond=0))
    from datetime import timedelta
    if _now_kst().hour >= 22:  # 22:30 이전이면 같은 날, 이후면 내일
        next_day_kst += timedelta(days=1)
    next_day_str = next_day_kst.strftime("%m/%d (%a)").replace(
        "Mon", "월").replace("Tue", "화").replace("Wed", "수").replace(
        "Thu", "목").replace("Fri", "금").replace("Sat", "토").replace("Sun", "일")

    today_pnl = usd_now - usd_start_of_day
    today_pnl_pct = (today_pnl / usd_start_of_day * 100) if usd_start_of_day > 0 else 0.0
    pnl_emoji = "📈" if today_pnl > 0 else ("📉" if today_pnl < 0 else "➡️")

    if today_trades:
        # 매매 있는 날
        trade_lines = []
        for t in today_trades:
            emoji = {"eod_profit": "🎉", "stop_loss": "🔴", "eod_loss": "🟡"}.get(
                t.get("sell_type", ""), "💼"
            )
            label = {
                "eod_profit": "EOD 익절",
                "stop_loss": "손절",
                "eod_loss": "EOD 손실",
            }.get(t.get("sell_type", ""), "매도")
            trade_lines.append(
                f"  {emoji} {t['symbol']} {label} "
                f"{t['pnl_usd']:+.2f}$ ({t['pnl_pct']:+.2f}%)"
            )
        trade_block = "\n".join(trade_lines)

        msg = (
            f"📊 *KIS 미국주식 일일 결산*\n"
            f"*{date_str}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"\n"
            f"✅ *오늘 매매*\n"
            f"{trade_block}\n"
            f"\n"
            f"💰 *오늘 손익*: *{today_pnl:+.2f}$* ({today_pnl_pct:+.2f}%) {pnl_emoji}\n"
            f"📦 누적 USD: ${usd_start_of_day:.2f} → *${usd_now:.2f}*\n"
            f"📅 이번 주: ${week_pnl_usd:+.2f} ({week_trade_days}매매일)\n"
            f"📅 이번 달: ${month_pnl_usd:+.2f}\n"
        )
        if total_pnl_usd is not None:
            msg += f"🏆 운영 누적: ${total_pnl_usd:+.2f}\n"
    else:
        # 매매 없는 날
        skip_lines = []
        for symbol, reason in skip_reasons.items():
            skip_lines.append(f"  • `{symbol}` bar1: {reason}")
        skip_block = "\n".join(skip_lines) if skip_lines else "  • 시장 미동작 또는 데이터 X"

        msg = (
            f"😴 *KIS 미국주식 일일 결산*\n"
            f"*{date_str}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"\n"
            f"📭 *오늘 매매 없음*\n"
            f"\n"
            f"🔍 시그널 사유\n"
            f"{skip_block}\n"
            f"\n"
            f"💰 누적 USD: *${usd_now:.2f}* (변동 없음)\n"
            f"📅 이번 주: ${week_pnl_usd:+.2f} ({week_trade_days}매매일)\n"
        )

    msg += f"\n⏰ 다음: {next_day_str} KST 22:30"
    return msg


def _format_hold_duration(minutes: int) -> str:
    """N분 → '17분' 또는 '6h 15m' 형식."""
    if minutes < 60:
        return f"{minutes}분"
    h, m = divmod(minutes, 60)
    return f"{h}h {m}m"


# === DB 집계 헬퍼 ===


def calc_today_pnl(db, symbol_filter: str = "kis_us") -> tuple[float, list[dict]]:
    """오늘(NY 거래일) 매매 손익 + 매매 상세 리스트.

    Returns:
        (today_pnl_usd, trades_list)
    """
    from datetime import timezone

    ny_now = _now_ny()
    ny_midnight = ny_now.replace(hour=0, minute=0, second=0, microsecond=0)
    ny_midnight_utc = ny_midnight.astimezone(timezone.utc)

    rows = db.execute(
        """
        SELECT id, coin, side, price, amount, profit_pct, trigger_reason, timestamp
        FROM trades
        WHERE market = ? AND timestamp >= ?
        ORDER BY timestamp, id
        """,
        (symbol_filter, ny_midnight_utc.strftime("%Y-%m-%d %H:%M:%S")),
    ).fetchall()

    pnl_total = 0.0
    trades_summary = []
    # 매수-매도 쌍 매칭
    buys = {}
    for r in rows:
        d = dict(r)
        if d["side"] == "buy":
            buys[d["coin"]] = d
        elif d["side"] == "sell":
            buy = buys.get(d["coin"])
            if buy:
                pnl_usd = (d["price"] - buy["price"]) * d["amount"]
                pnl_total += pnl_usd
                trigger = (d.get("trigger_reason") or "").lower()
                if "손절" in (d.get("trigger_reason") or "") or "stop" in trigger:
                    sell_type = "stop_loss"
                elif "eod" in trigger or "day_trading_close" in trigger or "강제" in (d.get("trigger_reason") or ""):
                    sell_type = "eod_profit" if pnl_usd > 0 else "eod_loss"
                else:
                    sell_type = "other"
                trades_summary.append({
                    "symbol": d["coin"],
                    "pnl_usd": pnl_usd,
                    "pnl_pct": d.get("profit_pct") or 0.0,
                    "sell_type": sell_type,
                })
                del buys[d["coin"]]
    return pnl_total, trades_summary


# === #396: 일일 매매 history 기록 ===


def _ny_today_str() -> str:
    """오늘 NY 거래일 'YYYY-MM-DD'."""
    return _now_ny().strftime("%Y-%m-%d")


def record_daily_history(
    db,
    ticker: str,
    bar1_pattern: str | None = None,
    bar1_body_pct: float | None = None,
    signal_price: float | None = None,
    bought: bool = False,
    buy_price: float | None = None,
    qty: float | None = None,
    skip_reason: str | None = None,
) -> None:
    """매수 시도 시점에 history 기록 (UPSERT).

    매수 안 한 경우 (도지/음봉/갭가드/자금부족) skip_reason 기록.
    매도는 update_daily_history_sell() 로 별도.
    """
    today = _ny_today_str()
    db.execute(
        """
        INSERT INTO kis_us_daily_history
            (trade_date, ticker, bar1_pattern, bar1_body_pct, signal_price,
             bought, buy_price, qty, skip_reason, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(trade_date, ticker) DO UPDATE SET
            bar1_pattern = excluded.bar1_pattern,
            bar1_body_pct = excluded.bar1_body_pct,
            signal_price = excluded.signal_price,
            bought = MAX(kis_us_daily_history.bought, excluded.bought),
            buy_price = COALESCE(excluded.buy_price, kis_us_daily_history.buy_price),
            qty = COALESCE(excluded.qty, kis_us_daily_history.qty),
            skip_reason = COALESCE(excluded.skip_reason, kis_us_daily_history.skip_reason),
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            today, ticker, bar1_pattern, bar1_body_pct, signal_price,
            1 if bought else 0,
            buy_price, qty, skip_reason,
        ),
    )
    db.commit()


def update_daily_history_sell(
    db,
    ticker: str,
    sell_price: float,
    pnl_usd: float,
    pnl_pct: float,
    sell_type: str,  # "stop_loss" / "eod_profit" / "eod_loss"
) -> None:
    """매도 발생 시 history 업데이트."""
    today = _ny_today_str()
    db.execute(
        """
        UPDATE kis_us_daily_history
        SET sold = 1,
            sell_price = ?,
            pnl_usd = ?,
            pnl_pct = ?,
            sell_type = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE trade_date = ? AND ticker = ?
        """,
        (sell_price, pnl_usd, pnl_pct, sell_type, today, ticker),
    )
    db.commit()


def calc_period_pnl(db, days: int, symbol_filter: str = "kis_us") -> tuple[float, int]:
    """최근 N일 손익 + 매매일 수."""
    from datetime import timedelta, timezone

    cutoff = (_now_ny() - timedelta(days=days)).replace(hour=0, minute=0)
    cutoff_utc = cutoff.astimezone(timezone.utc)

    rows = db.execute(
        """
        SELECT id, coin, side, price, amount, timestamp
        FROM trades
        WHERE market = ? AND timestamp >= ?
        ORDER BY timestamp, id
        """,
        (symbol_filter, cutoff_utc.strftime("%Y-%m-%d %H:%M:%S")),
    ).fetchall()

    pnl = 0.0
    trade_days: set = set()
    buys = {}
    for r in rows:
        d = dict(r)
        if d["side"] == "buy":
            buys[d["coin"]] = d
        elif d["side"] == "sell":
            buy = buys.get(d["coin"])
            if buy:
                pnl += (d["price"] - buy["price"]) * d["amount"]
                # NY date 추출 (단순화: timestamp 앞 10자)
                trade_days.add(d["timestamp"][:10])
                del buys[d["coin"]]
    return pnl, len(trade_days)
