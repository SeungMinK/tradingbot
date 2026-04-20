"""#208: 매도 후 재매수 쿨다운 가드 테스트.

ALGO 사례: 20:39 손절 -5.65% → 20:40 재매수 (1분 차이, 수수료 왕복 0.1% 손실).
같은 가격 사건에 대해 손절 신호와 RSI 매수 신호가 서로 모순적으로 발생.
"""

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


def _record_sell(db, coin: str, minutes_ago: float) -> None:
    """N분 전 매도 기록 삽입."""
    db.execute(
        f"""
        INSERT INTO trades (timestamp, coin, side, price, amount, total_krw, fee_krw, strategy, trigger_reason)
        VALUES (datetime('now', '-{minutes_ago} minutes'), ?, 'sell', 100, 1, 100000, 50, 'test', '손절')
        """,
        (coin,),
    )
    db.commit()


# ===================================================================
# 기본 동작
# ===================================================================


def test_default_cooldown_is_10_minutes():
    """기본값 10분."""
    assert RiskLimits().coin_reentry_cooldown_minutes == 10


def test_buy_blocked_within_cooldown(db):
    """매도 5분 후 재매수 시도 → 차단."""
    _record_sell(db, "KRW-ALGO", minutes_ago=5)
    rm = RiskManager(db, RiskLimits(coin_reentry_cooldown_minutes=10))
    ok, reason = rm.check_can_buy("KRW-ALGO", buy_amount_krw=50_000, current_balance_krw=500_000)
    assert ok is False
    assert "쿨다운" in reason
    assert "ALGO" in reason


def test_buy_allowed_after_cooldown(db):
    """매도 11분 후 재매수 → 허용."""
    _record_sell(db, "KRW-ALGO", minutes_ago=11)
    rm = RiskManager(db, RiskLimits(coin_reentry_cooldown_minutes=10))
    ok, reason = rm.check_can_buy("KRW-ALGO", buy_amount_krw=50_000, current_balance_krw=500_000)
    assert ok is True


def test_cooldown_per_coin_isolated(db):
    """ALGO 매도가 BTC 매수에는 영향 없음."""
    _record_sell(db, "KRW-ALGO", minutes_ago=2)
    rm = RiskManager(db, RiskLimits(coin_reentry_cooldown_minutes=10))
    ok, _ = rm.check_can_buy("KRW-BTC", buy_amount_krw=50_000, current_balance_krw=500_000)
    assert ok is True


def test_no_sell_history_no_cooldown(db):
    """매도 기록 없으면 쿨다운 미적용."""
    rm = RiskManager(db, RiskLimits(coin_reentry_cooldown_minutes=10))
    ok, _ = rm.check_can_buy("KRW-NEW", buy_amount_krw=50_000, current_balance_krw=500_000)
    assert ok is True


def test_cooldown_uses_most_recent_sell(db):
    """여러 매도 중 가장 최근 매도 기준."""
    _record_sell(db, "KRW-ALGO", minutes_ago=60)  # 옛날
    _record_sell(db, "KRW-ALGO", minutes_ago=3)  # 최근 — 이거 기준
    rm = RiskManager(db, RiskLimits(coin_reentry_cooldown_minutes=10))
    ok, reason = rm.check_can_buy("KRW-ALGO", buy_amount_krw=50_000, current_balance_krw=500_000)
    assert ok is False
    assert "쿨다운" in reason


def test_cooldown_zero_disables_guard(db):
    """coin_reentry_cooldown_minutes=0 이면 가드 비활성."""
    _record_sell(db, "KRW-ALGO", minutes_ago=1)
    rm = RiskManager(db, RiskLimits(coin_reentry_cooldown_minutes=0))
    ok, _ = rm.check_can_buy("KRW-ALGO", buy_amount_krw=50_000, current_balance_krw=500_000)
    assert ok is True


def test_minutes_since_last_sell_no_record_returns_none(db):
    """매도 기록 0건 → None."""
    rm = RiskManager(db, RiskLimits())
    assert rm._minutes_since_last_sell("KRW-NEW") is None


def test_minutes_since_last_sell_returns_positive(db):
    """매도 기록 있으면 양수."""
    _record_sell(db, "KRW-ALGO", minutes_ago=5)
    rm = RiskManager(db, RiskLimits())
    gap = rm._minutes_since_last_sell("KRW-ALGO")
    assert gap is not None
    assert 4 < gap < 6  # 5분 근처
