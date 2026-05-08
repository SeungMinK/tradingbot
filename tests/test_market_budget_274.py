"""#274: 시장별 동적 예산 테스트.

수익 → 시드 자체 증가, 손실 → 자체 감소.
"""

import os
import tempfile
from pathlib import Path

import pytest

from cryptobot.data.database import Database


@pytest.fixture(autouse=True)
def env_budgets(monkeypatch):
    """초기 시드 20만/20만 고정."""
    monkeypatch.setenv("KIS_KR_BUDGET_KRW", "200000")
    monkeypatch.setenv("KIS_US_BUDGET_KRW", "200000")
    # config 모듈 reload
    import importlib
    from cryptobot.bot import config as _cfg
    importlib.reload(_cfg)
    yield


@pytest.fixture
def db():
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    yield db
    db.close()


def _record_buy(db, market: str, total_krw: float):
    db.execute(
        "INSERT INTO trades (timestamp, coin, market, side, price, amount, total_krw, fee_krw, strategy, trigger_reason) "
        "VALUES (datetime('now'), 'X', ?, 'buy', 100, 1, ?, 50, 'test', 'seed')",
        (market, total_krw),
    )
    db.commit()


def _record_sell(db, market: str, profit_krw: float, buy_id: int):
    db.execute(
        "INSERT INTO trades (timestamp, coin, market, side, price, amount, total_krw, fee_krw, strategy, "
        "trigger_reason, buy_trade_id, profit_krw, profit_pct) "
        "VALUES (datetime('now'), 'X', ?, 'sell', 100, 1, 99000, 50, 'test', 'sold', ?, ?, 0)",
        (market, buy_id, profit_krw),
    )
    db.commit()


def test_initial_budget_equals_seed(db):
    """거래 없으면 가용 예산 = 시드."""
    from cryptobot.bot.market_budget import get_available_budget
    assert get_available_budget(db, "kis_kr") == 200000.0
    assert get_available_budget(db, "kis_us") == 200000.0


def test_held_position_reduces_available(db):
    """보유 중이면 그만큼 예산 차감."""
    from cryptobot.bot.market_budget import get_available_budget
    _record_buy(db, "kis_kr", total_krw=50000)
    # seed 20만 - 보유 5만(+ 수수료 50) = 14만 9950
    assert get_available_budget(db, "kis_kr") == 200000 - 50050


def test_realized_profit_increases_available(db):
    """매도로 수익 실현 시 예산 증가."""
    from cryptobot.bot.market_budget import get_available_budget
    _record_buy(db, "kis_kr", total_krw=50000)
    buy_id = db.execute("SELECT id FROM trades WHERE side='buy' ORDER BY id DESC LIMIT 1").fetchone()[0]
    _record_sell(db, "kis_kr", profit_krw=10000, buy_id=buy_id)
    # 시드 20만 + 실현 +1만 - 보유 0 = 21만
    assert get_available_budget(db, "kis_kr") == 210000


def test_realized_loss_decreases_available(db):
    """매도로 손실 시 예산 감소."""
    from cryptobot.bot.market_budget import get_available_budget
    _record_buy(db, "kis_kr", total_krw=50000)
    buy_id = db.execute("SELECT id FROM trades WHERE side='buy' ORDER BY id DESC LIMIT 1").fetchone()[0]
    _record_sell(db, "kis_kr", profit_krw=-30000, buy_id=buy_id)
    # 시드 20만 + 실현 -3만 - 보유 0 = 17만
    assert get_available_budget(db, "kis_kr") == 170000


def test_kr_and_us_budgets_independent(db):
    """한국/미국 예산 독립 — 다른 시장 손익 영향 X."""
    from cryptobot.bot.market_budget import get_available_budget
    _record_buy(db, "kis_kr", total_krw=50000)
    buy_id = db.execute("SELECT id FROM trades WHERE side='buy' ORDER BY id DESC LIMIT 1").fetchone()[0]
    _record_sell(db, "kis_kr", profit_krw=50000, buy_id=buy_id)
    # 한국: 25만, 미국: 20만 (영향 없음)
    assert get_available_budget(db, "kis_kr") == 250000
    assert get_available_budget(db, "kis_us") == 200000


def test_negative_clamped_to_zero(db):
    """예산 음수면 0으로 클램프 (매수 차단)."""
    from cryptobot.bot.market_budget import get_available_budget
    _record_buy(db, "kis_us", total_krw=300000)  # 시드보다 큰 보유 (이론적 케이스)
    assert get_available_budget(db, "kis_us") == 0.0


def test_status_dict(db):
    """status 디버그 dict."""
    from cryptobot.bot.market_budget import get_market_budget_status
    s = get_market_budget_status(db, "kis_kr")
    assert s["market"] == "kis_kr"
    assert s["seed"] == 200000
    assert s["realized_pnl"] == 0
    assert s["available"] == 200000
