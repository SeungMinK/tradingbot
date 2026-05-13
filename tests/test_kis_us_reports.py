"""#393: KIS US Slack 통합 보고 메시지 포맷 + 집계 테스트."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cryptobot.data.database import Database
from cryptobot.notifier.kis_us_reports import (
    calc_period_pnl,
    calc_today_pnl,
    format_buy,
    format_daily_summary,
    format_market_open,
    format_sell,
)


@pytest.fixture
def db():
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    yield db
    db.close()


# === 포맷 테스트 ===


def test_format_market_open_basic():
    msg = format_market_open(
        universe=["SOXL", "SOXS"],
        usd_available=647.88,
        fx_krw_per_usd=1473.0,
    )
    assert "장 시작" in msg
    assert "SOXL" in msg and "SOXS" in msg
    assert "647" in msg
    assert "954" in msg  # ₩954,XXX (대략)


def test_format_buy_basic():
    msg = format_buy(
        symbol="SOXL",
        qty=3,
        price=176.08,
        signal_reason="bar1 양봉 +0.42%",
        stop_loss_price=175.22,
        risk_usd=2.58,
        account_usd=647.88,
    )
    assert "매수 체결" in msg
    assert "SOXL" in msg
    assert "3주" in msg
    assert "176.08" in msg
    assert "528.24" in msg  # 3 × 176.08
    assert "175.22" in msg
    assert "리스크" in msg


def test_format_buy_fractional_qty():
    msg = format_buy(
        symbol="NVDA",
        qty=0.5,
        price=200.0,
        signal_reason="test",
    )
    assert "0.5000주" in msg


def test_format_sell_stop_loss():
    msg = format_sell(
        symbol="SOXL",
        qty=3,
        price=175.22,
        pnl_pct=-0.49,
        pnl_usd=-2.58,
        hold_minutes=17,
        sell_type="stop_loss",
    )
    assert "손절" in msg
    assert "SOXL" in msg
    assert "-0.49" in msg
    assert "-2.58" in msg
    assert "17분" in msg
    assert "1일 1회 룰" in msg


def test_format_sell_eod_profit():
    msg = format_sell(
        symbol="SOXL",
        qty=3,
        price=182.50,
        pnl_pct=3.65,
        pnl_usd=19.26,
        hold_minutes=375,  # 6h 15m
        sell_type="eod_profit",
    )
    assert "EOD 익절" in msg
    assert "+3.65" in msg
    assert "$19.26" in msg
    assert "6h 15m" in msg


def test_format_sell_eod_loss():
    msg = format_sell(
        symbol="SOXS",
        qty=10,
        price=8.50,
        pnl_pct=-0.90,
        pnl_usd=-0.77,
        hold_minutes=300,
        sell_type="eod_loss",
    )
    assert "EOD 청산" in msg
    assert "손실" in msg
    assert "-0.90" in msg


# === 일일 결산 ===


def test_format_daily_summary_with_trades():
    trades = [
        {"symbol": "SOXL", "pnl_usd": 19.26, "pnl_pct": 3.65, "sell_type": "eod_profit"},
        {"symbol": "SOXS", "pnl_usd": -2.58, "pnl_pct": -0.49, "sell_type": "stop_loss"},
    ]
    msg = format_daily_summary(
        today_trades=trades,
        skip_reasons={},
        usd_now=664.56,
        usd_start_of_day=647.88,
        week_pnl_usd=16.68,
        week_trade_days=1,
        month_pnl_usd=-30.0,
        total_pnl_usd=50.0,
    )
    assert "일일 결산" in msg
    assert "SOXL" in msg and "SOXS" in msg
    assert "EOD 익절" in msg
    assert "손절" in msg
    assert "+16.68" in msg  # today_pnl
    assert "664.56" in msg
    assert "운영 누적" in msg


def test_format_daily_summary_no_trades():
    skip = {
        "SOXL": "도지 (몸통 0.02% < 0.05%)",
        "SOXS": "음봉",
    }
    msg = format_daily_summary(
        today_trades=[],
        skip_reasons=skip,
        usd_now=647.88,
        usd_start_of_day=647.88,
        week_pnl_usd=16.68,
        week_trade_days=1,
        month_pnl_usd=0.0,
    )
    assert "매매 없음" in msg
    assert "도지" in msg
    assert "음봉" in msg
    assert "변동 없음" in msg


# === DB 집계 ===


def _ny_today_morning_utc() -> str:
    """오늘 NY 거래일 10:00 EDT (확실히 NY 자정 이후) UTC ISO 문자열."""
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    ny_now = datetime.now(ny)
    ny_morning = ny_now.replace(hour=10, minute=0, second=0, microsecond=0)
    return ny_morning.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _seed_trade(db, coin, side, price, amount, pnl_pct=None, reason="", timestamp_utc=None):
    if timestamp_utc is None:
        timestamp_utc = _ny_today_morning_utc()
    db.execute(
        "INSERT INTO trades (timestamp, market, coin, side, price, amount, total_krw, fee_krw, "
        "strategy, trigger_reason, profit_pct) "
        "VALUES (?, 'kis_us', ?, ?, ?, ?, 0, 0, 'zarattini_3x_atr', ?, ?)",
        (timestamp_utc, coin, side, price, amount, reason, pnl_pct),
    )
    db.commit()


def test_calc_today_pnl_buy_sell_pair(db):
    """오늘 buy + sell 쌍 → pnl 계산."""
    _seed_trade(db, "SOXL", "buy", 176.0, 3.0)
    _seed_trade(db, "SOXL", "sell", 182.0, 3.0, pnl_pct=3.41, reason="day_trading_close")
    pnl, trades = calc_today_pnl(db)
    assert pnl == pytest.approx(18.0, abs=0.01)
    assert len(trades) == 1
    assert trades[0]["symbol"] == "SOXL"
    assert trades[0]["sell_type"] == "eod_profit"


def test_calc_today_pnl_stop_loss(db):
    _seed_trade(db, "SOXS", "buy", 9.0, 50.0)
    _seed_trade(db, "SOXS", "sell", 8.6, 50.0, pnl_pct=-4.44, reason="손절 -4.5%")
    _, trades = calc_today_pnl(db)
    assert trades[0]["sell_type"] == "stop_loss"


def test_calc_period_pnl_7d(db):
    """7일 손익 + 매매일 수."""
    # 오늘
    _seed_trade(db, "SOXL", "buy", 100.0, 1.0)
    _seed_trade(db, "SOXL", "sell", 105.0, 1.0)
    # 어제
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    _seed_trade(db, "SOXS", "buy", 10.0, 5.0, timestamp_utc=yesterday)
    _seed_trade(db, "SOXS", "sell", 11.0, 5.0, timestamp_utc=yesterday)
    pnl, days = calc_period_pnl(db, days=7)
    assert pnl == pytest.approx(10.0, abs=0.01)  # 5 (오늘) + 5 (어제)
    assert days >= 1


def test_calc_today_pnl_empty(db):
    pnl, trades = calc_today_pnl(db)
    assert pnl == 0.0
    assert trades == []
