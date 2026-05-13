"""#391: KIS US 1일 1회 룰 (논문 정신) 테스트.

DB 쿼리 기반 — 봇 재시작 시에도 유지.
NY timezone 정확히 사용 (EDT/EST 자동 처리).
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cryptobot.data.database import Database

NY = ZoneInfo("America/New_York")


@pytest.fixture
def db():
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    yield db
    db.close()


def _insert_kis_trade(db, coin: str, side: str, timestamp_utc: str | None = None):
    """KIS US trade 시드. timestamp는 UTC ISO 형식 또는 None(현재)."""
    if timestamp_utc:
        db.execute(
            "INSERT INTO trades (timestamp, market, coin, side, price, amount, total_krw, fee_krw, strategy) "
            "VALUES (?, 'kis_us', ?, ?, 10, 1, 14000, 5, 'zarattini_3x_atr')",
            (timestamp_utc, coin, side),
        )
    else:
        db.execute(
            "INSERT INTO trades (market, coin, side, price, amount, total_krw, fee_krw, strategy) "
            "VALUES ('kis_us', ?, ?, 10, 1, 14000, 5, 'zarattini_3x_atr')",
            (coin, side),
        )
    db.commit()


def _already_traded_today(db, symbol: str) -> bool:
    """run_kis_us._evaluate_buy_only 안의 룰 미러."""
    ny_now = datetime.now(NY)
    ny_midnight = ny_now.replace(hour=0, minute=0, second=0, microsecond=0)
    ny_midnight_utc = ny_midnight.astimezone(timezone.utc)
    row = db.execute(
        "SELECT id FROM trades WHERE market='kis_us' AND coin=? AND timestamp >= ? LIMIT 1",
        (symbol, ny_midnight_utc.strftime("%Y-%m-%d %H:%M:%S")),
    ).fetchone()
    return row is not None


# === 기본 룰 ===


def test_no_trades_today_returns_false(db):
    """오늘 매매 없으면 1일 1회 룰 통과."""
    assert _already_traded_today(db, "SOXL") is False


def test_today_buy_returns_true(db):
    """오늘 매수 있으면 skip."""
    _insert_kis_trade(db, "SOXL", "buy")
    assert _already_traded_today(db, "SOXL") is True


def test_today_sell_returns_true(db):
    """오늘 매도(손절)만 있어도 skip (논문: 1일 1회)."""
    _insert_kis_trade(db, "SOXS", "sell")
    assert _already_traded_today(db, "SOXS") is True


def test_other_symbol_today_not_blocking(db):
    """SOXL 매매 있어도 SOXS는 별개로 평가."""
    _insert_kis_trade(db, "SOXL", "buy")
    assert _already_traded_today(db, "SOXL") is True
    assert _already_traded_today(db, "SOXS") is False


def test_yesterday_trade_not_blocking(db):
    """어제 NY 시간 매매는 오늘 신호 안 막음."""
    # NY 어제 자정 - 1시간 (어제 23:00 NY = UTC 03:00) 시뮬레이션
    ny_yesterday_evening = datetime.now(NY).replace(hour=23, minute=0)
    # 어제로 — replace로 day 빼기 simple
    from datetime import timedelta
    ny_yesterday = ny_yesterday_evening - timedelta(days=1)
    yesterday_utc = ny_yesterday.astimezone(timezone.utc)
    _insert_kis_trade(db, "SOXL", "buy", yesterday_utc.strftime("%Y-%m-%d %H:%M:%S"))
    assert _already_traded_today(db, "SOXL") is False


def test_other_market_not_blocking(db):
    """upbit 매매는 KIS US 룰에 영향 X."""
    db.execute(
        "INSERT INTO trades (market, coin, side, price, amount, total_krw, fee_krw, strategy) "
        "VALUES ('upbit', 'KRW-BTC', 'buy', 100000000, 0.001, 100000, 50, 'test')"
    )
    db.commit()
    assert _already_traded_today(db, "KRW-BTC") is False  # different market, but kis_us filter
    # SOXL은 upbit 트레이드 무관
    assert _already_traded_today(db, "SOXL") is False


# === 시나리오 — 어제 SOXS 손절 케이스 ===


def test_real_scenario_sell_then_block_for_rest_of_day(db):
    """오늘 22:30 KST 매수 후 22:43 KST 손절 → 그 후 모든 매수 시도 skip.

    KST 22:30 = UTC 13:30 = NY 09:30 EDT (정상 ORB 시간)
    """
    # NY 시간 09:35 EDT = UTC 13:35 (예시)
    ny_now = datetime.now(NY)
    today_morning = ny_now.replace(hour=9, minute=35, second=0)
    morning_utc = today_morning.astimezone(timezone.utc)

    # 매수 → 매도 시뮬
    _insert_kis_trade(db, "SOXS", "buy", morning_utc.strftime("%Y-%m-%d %H:%M:%S"))
    _insert_kis_trade(db, "SOXS", "sell", morning_utc.strftime("%Y-%m-%d %H:%M:%S"))

    # 이후 모든 시점에 SOXS 신호 떠도 skip
    assert _already_traded_today(db, "SOXS") is True
