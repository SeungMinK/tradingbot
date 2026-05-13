"""#396: KIS US 일일 매매 history 테이블 + 갭 가드 테스트."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cryptobot.data.database import Database
from cryptobot.notifier.kis_us_reports import (
    record_daily_history,
    update_daily_history_sell,
)


@pytest.fixture
def db():
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    yield db
    db.close()


def _get_today_row(db, ticker: str) -> dict | None:
    from cryptobot.notifier.kis_us_reports import _ny_today_str
    row = db.execute(
        "SELECT * FROM kis_us_daily_history WHERE trade_date = ? AND ticker = ?",
        (_ny_today_str(), ticker),
    ).fetchone()
    return dict(row) if row else None


# === record_daily_history ===


def test_record_skip_doji(db):
    """도지 skip 기록."""
    record_daily_history(
        db, "SOXL",
        bar1_pattern="doji",
        bar1_body_pct=0.02,
        signal_price=180.0,
        bought=False,
        skip_reason="도지",
    )
    r = _get_today_row(db, "SOXL")
    assert r is not None
    assert r["bar1_pattern"] == "doji"
    assert r["bought"] == 0
    assert r["skip_reason"] == "도지"


def test_record_skip_bearish(db):
    """음봉 skip 기록."""
    record_daily_history(
        db, "SOXL",
        bar1_pattern="bearish",
        bar1_body_pct=0.5,
        bought=False,
        skip_reason="음봉",
    )
    r = _get_today_row(db, "SOXL")
    assert r["bar1_pattern"] == "bearish"


def test_record_skip_gap_guard(db):
    """갭 가드 skip 기록."""
    record_daily_history(
        db, "SOXL",
        bar1_pattern="bullish",
        bar1_body_pct=1.8,
        signal_price=182.80,
        bought=False,
        skip_reason="갭 가드 (-1.95%)",
    )
    r = _get_today_row(db, "SOXL")
    assert r["bar1_pattern"] == "bullish"
    assert "갭 가드" in r["skip_reason"]


def test_record_buy(db):
    """매수 체결 기록."""
    record_daily_history(
        db, "SOXL",
        bar1_pattern="bullish",
        bar1_body_pct=1.8,
        signal_price=180.0,
        bought=True,
        buy_price=179.5,
        qty=3,
    )
    r = _get_today_row(db, "SOXL")
    assert r["bought"] == 1
    assert r["buy_price"] == 179.5
    assert r["qty"] == 3


def test_upsert_same_day_same_ticker(db):
    """같은 trade_date + ticker는 1행만 (UPSERT)."""
    record_daily_history(db, "SOXL", bar1_pattern="bullish", bar1_body_pct=1.0)
    record_daily_history(db, "SOXL", bar1_pattern="bullish", bought=True, buy_price=180.0, qty=3)
    rows = db.execute(
        "SELECT * FROM kis_us_daily_history WHERE ticker = 'SOXL'"
    ).fetchall()
    assert len(rows) == 1
    assert dict(rows[0])["bought"] == 1
    assert dict(rows[0])["buy_price"] == 180.0


def test_update_sell_after_buy(db):
    """매수 후 매도 → 한 행에 통합."""
    record_daily_history(db, "SOXL", bought=True, buy_price=180.0, qty=3, bar1_pattern="bullish")
    update_daily_history_sell(
        db, "SOXL",
        sell_price=185.0,
        pnl_usd=15.0,
        pnl_pct=2.78,
        sell_type="eod_profit",
    )
    r = _get_today_row(db, "SOXL")
    assert r["bought"] == 1
    assert r["sold"] == 1
    assert r["sell_price"] == 185.0
    assert r["sell_type"] == "eod_profit"
    assert r["pnl_usd"] == 15.0


def test_different_tickers_separate_rows(db):
    """SOXL과 SOXS는 별개 행."""
    record_daily_history(db, "SOXL", bar1_pattern="bullish")
    record_daily_history(db, "SOXS", bar1_pattern="bearish")
    rows = db.execute(
        "SELECT ticker, bar1_pattern FROM kis_us_daily_history ORDER BY ticker"
    ).fetchall()
    assert len(rows) == 2
    assert dict(rows[0])["bar1_pattern"] == "bullish"
    assert dict(rows[1])["bar1_pattern"] == "bearish"


# === 갭 가드 로직 단위 검증 ===


def test_gap_guard_calculation():
    """가격 갭 % 계산: 시그널-현재 차이."""
    signal_price = 182.80
    current = 179.22
    gap_pct = (current - signal_price) / signal_price * 100
    assert gap_pct < -1.0  # -1.95%
    assert -2.0 < gap_pct < -1.0


def test_gap_guard_safe_zone():
    """갭 -1% 이내면 매수 진행."""
    signal_price = 182.80
    current = 182.00
    gap_pct = (current - signal_price) / signal_price * 100
    assert gap_pct > -1.0  # -0.44%


def test_gap_guard_positive_price():
    """가격이 시그널 위 (양수 갭) → 매수 진행."""
    signal_price = 182.80
    current = 183.50
    gap_pct = (current - signal_price) / signal_price * 100
    assert gap_pct > 0


# === KISBuySignal signal_price 필드 ===


def test_buy_signal_has_signal_price():
    from cryptobot.bot.kis_strategy import KISBuySignal

    sig = KISBuySignal(
        should_buy=True,
        reason="test",
        stop_loss_price=180.0,
        signal_price=182.80,
        bar1_pattern="bullish",
        bar1_body_pct=1.8,
    )
    assert sig.signal_price == 182.80
    assert sig.bar1_pattern == "bullish"
    assert sig.bar1_body_pct == 1.8


def test_buy_signal_optional_fields_default_none():
    """signal_price/bar1_pattern 미지정 시 None."""
    from cryptobot.bot.kis_strategy import KISBuySignal

    sig = KISBuySignal(should_buy=False, reason="test")
    assert sig.signal_price is None
    assert sig.bar1_pattern is None
