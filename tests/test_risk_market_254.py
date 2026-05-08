"""#254 1단계: RiskManager market 인자 통합 테스트."""

import tempfile
from pathlib import Path

import pytest

from cryptobot.bot.risk import RiskLimits, RiskManager
from cryptobot.data.database import Database


@pytest.fixture
def db():
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    yield db
    db.close()


def test_default_market_is_upbit(db):
    """market 미지정 → upbit (코인 봇 호환성)."""
    rm = RiskManager(db)
    assert rm.market == "upbit"
    assert rm.market_thresholds is not None
    assert rm.market_thresholds.name == "upbit"
    assert rm.market_thresholds.take_profit_pct == 3.0


def test_explicit_kis_kr(db):
    """한국주식 시장 지정 → 4% 임계."""
    rm = RiskManager(db, market="kis_kr")
    assert rm.market == "kis_kr"
    assert rm.market_thresholds.take_profit_pct == 4.0
    assert rm.market_thresholds.fee_guard_pct == 0.4


def test_explicit_kis_us(db):
    """미국주식 시장 지정 → 5% 임계."""
    rm = RiskManager(db, market="kis_us")
    assert rm.market_thresholds.take_profit_pct == 5.0
    assert rm.market_thresholds.fee_guard_pct == 0.5


def test_unknown_market_falls_back_gracefully(db):
    """미지원 시장 → market_thresholds=None, RiskManager 동작은 유지."""
    rm = RiskManager(db, market="kraken")
    assert rm.market == "kraken"
    assert rm.market_thresholds is None
    # 기존 check_can_buy 동작 정상
    ok, _ = rm.check_can_buy("X", 100_000, 500_000)
    assert isinstance(ok, bool)


def test_existing_check_can_buy_unchanged(db):
    """기존 동작 호환 — coin/limits/check_can_buy 그대로."""
    rm = RiskManager(db, RiskLimits(coin_reentry_cooldown_minutes=0))
    ok, reason = rm.check_can_buy("KRW-BTC", 50_000, 500_000)
    assert ok is True
    assert "통과" in reason
