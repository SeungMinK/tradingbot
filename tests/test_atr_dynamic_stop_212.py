"""#212: ATR 기반 동적 stop_loss 테스트.

매수 시점에 ATR(14) × multiplier로 코인별 변동성에 맞춰 손절폭 결정.
clamp [-max_abs, -min_abs]. ATR 실패 시 fallback.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from cryptobot.bot.risk import RiskLimits
from cryptobot.data.database import Database


@pytest.fixture
def db():
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    yield db
    db.close()


def _make_bot(db, limits: RiskLimits | None = None):
    from cryptobot.bot.main import CryptoBot

    bot = CryptoBot.__new__(CryptoBot)
    bot._db = db
    bot._risk = MagicMock()
    bot._risk.limits = limits or RiskLimits()
    return bot


def _df_with_atr(atr_target: float, current_price: float = 1000.0, n: int = 30):
    """ATR이 atr_target이 되도록 OHLCV 생성 (단순 high-low=atr_target)."""
    high = np.full(n, current_price + atr_target / 2)
    low = np.full(n, current_price - atr_target / 2)
    close = np.full(n, current_price)
    return pd.DataFrame({"high": high, "low": low, "close": close})


# ===================================================================
# 기본값
# ===================================================================


def test_atr_default_settings():
    """기본 OFF — 백테스트가 부정적이라 운영 영향 없게. 인프라만 보존."""
    limits = RiskLimits()
    assert limits.enable_dynamic_stop_loss is False  # 기본 OFF
    assert limits.atr_period == 14
    assert limits.atr_stop_loss_multiplier == 2.0
    assert limits.dynamic_stop_loss_min_abs_pct == 5.0
    assert limits.dynamic_stop_loss_max_abs_pct == 12.0


def test_default_off_returns_none(db):
    """기본값으로는 dynamic stop이 적용 안 됨 — 정확히 None 반환."""
    bot = _make_bot(db, RiskLimits())  # 기본값 사용
    df = _df_with_atr(atr_target=40)
    assert bot._calc_dynamic_stop_loss_pct(df, 1000) is None


# ===================================================================
# _calc_dynamic_stop_loss_pct
# ===================================================================


def test_disabled_returns_none(db):
    """enable_dynamic_stop_loss=False → None."""
    bot = _make_bot(db, RiskLimits(enable_dynamic_stop_loss=False))
    df = _df_with_atr(atr_target=20)
    assert bot._calc_dynamic_stop_loss_pct(df, 1000) is None


def test_low_volatility_clamped_to_min(db):
    """저변동성 코인 (ATR 1%) → 손절폭 -5% 최소 보장."""
    bot = _make_bot(db, RiskLimits(enable_dynamic_stop_loss=True, atr_stop_loss_multiplier=2.0))
    # ATR=10, price=1000 → atr_pct=1%, dynamic = -2% → clamp to -5%
    df = _df_with_atr(atr_target=10, current_price=1000)
    result = bot._calc_dynamic_stop_loss_pct(df, 1000)
    assert result == -5.0


def test_high_volatility_widens_stop(db):
    """고변동성 코인 (ATR 4%) → 손절폭 -8%."""
    bot = _make_bot(db, RiskLimits(enable_dynamic_stop_loss=True, atr_stop_loss_multiplier=2.0))
    # ATR=40, price=1000 → atr_pct=4%, dynamic = -8% → clamp [-12, -5] → -8%
    df = _df_with_atr(atr_target=40, current_price=1000)
    result = bot._calc_dynamic_stop_loss_pct(df, 1000)
    assert result == pytest.approx(-8.0, abs=0.01)


def test_extreme_volatility_clamped_to_max(db):
    """초고변동성 (ATR 10%) → -12% 상한."""
    bot = _make_bot(db, RiskLimits(enable_dynamic_stop_loss=True, atr_stop_loss_multiplier=2.0))
    # ATR=100, price=1000 → atr_pct=10%, dynamic=-20% → clamp to -12%
    df = _df_with_atr(atr_target=100, current_price=1000)
    result = bot._calc_dynamic_stop_loss_pct(df, 1000)
    assert result == -12.0


def test_atr_calculation_failure_returns_none(db):
    """df=None → None."""
    bot = _make_bot(db, RiskLimits(enable_dynamic_stop_loss=True))
    assert bot._calc_dynamic_stop_loss_pct(None, 1000) is None


def test_zero_price_returns_none(db):
    """current_price=0 → None (0 나눗셈 방지)."""
    bot = _make_bot(db, RiskLimits(enable_dynamic_stop_loss=True))
    df = _df_with_atr(atr_target=20)
    assert bot._calc_dynamic_stop_loss_pct(df, 0) is None


def test_multiplier_higher_means_wider_stop(db):
    """multiplier가 크면 stop 폭도 넓음."""
    df = _df_with_atr(atr_target=30, current_price=1000)  # atr_pct=3%
    bot1 = _make_bot(db, RiskLimits(enable_dynamic_stop_loss=True, atr_stop_loss_multiplier=1.5))
    bot2 = _make_bot(db, RiskLimits(enable_dynamic_stop_loss=True, atr_stop_loss_multiplier=3.0))
    r1 = bot1._calc_dynamic_stop_loss_pct(df, 1000)  # -4.5% → clamp -5%
    r2 = bot2._calc_dynamic_stop_loss_pct(df, 1000)  # -9% → -9%
    assert abs(r2) > abs(r1)


def test_custom_min_max_clamp(db):
    """min/max 커스텀 적용."""
    df = _df_with_atr(atr_target=80, current_price=1000)  # atr_pct=8%, dynamic=-16%
    bot = _make_bot(db, RiskLimits(
        enable_dynamic_stop_loss=True,
        atr_stop_loss_multiplier=2.0,
        dynamic_stop_loss_min_abs_pct=3.0,
        dynamic_stop_loss_max_abs_pct=10.0,
    ))
    result = bot._calc_dynamic_stop_loss_pct(df, 1000)
    assert result == -10.0


def test_negative_atr_returns_none(db):
    """ATR <= 0 → None (방어)."""
    bot = _make_bot(db, RiskLimits(enable_dynamic_stop_loss=True))
    # 짧은 df로 ATR이 NaN/None이 나오는 케이스
    df = pd.DataFrame({"high": [1000], "low": [1000], "close": [1000]})  # period=14, 데이터 1개
    assert bot._calc_dynamic_stop_loss_pct(df, 1000) is None
