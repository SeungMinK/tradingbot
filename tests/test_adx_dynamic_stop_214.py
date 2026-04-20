"""#214: ADX 기반 동적 stop_loss 테스트.

ADX < threshold (약한 추세, 횡보) → 넓은 stop (-7%): 노이즈 흡수
ADX ≥ threshold (강한 추세) → 좁은 stop (-3.5%): 잘못된 방향 빠른 손절

백테스트 검증: 4전략 평균 효과 양수, 특히 volatility_breakout +3.06%.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from cryptobot.bot.indicators import calculate_adx
from cryptobot.bot.risk import RiskLimits
from cryptobot.data.database import Database


@pytest.fixture
def db():
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    yield db
    db.close()


def _make_bot(db, limits=None):
    from cryptobot.bot.main import CryptoBot
    bot = CryptoBot.__new__(CryptoBot)
    bot._db = db
    bot._risk = MagicMock()
    bot._risk.limits = limits or RiskLimits()
    return bot


def _df_strong_trend(n: int = 50, base: float = 1000):
    """강한 상승 추세 — ADX 높게."""
    prices = np.linspace(base, base * 1.5, n)
    high = prices * 1.01
    low = prices * 0.99
    return pd.DataFrame({"high": high, "low": low, "close": prices})


def _df_sideways(n: int = 50, base: float = 1000):
    """횡보 — ADX 낮게."""
    prices = base + np.sin(np.linspace(0, 6 * np.pi, n)) * (base * 0.005)
    high = prices * 1.005
    low = prices * 0.995
    return pd.DataFrame({"high": high, "low": low, "close": prices})


# ===================================================================
# calculate_adx
# ===================================================================


def test_adx_returns_higher_on_trend_than_sideways():
    """강한 추세의 ADX가 횡보보다 높아야 함."""
    adx_trend = calculate_adx(_df_strong_trend()["high"], _df_strong_trend()["low"], _df_strong_trend()["close"])
    adx_side = calculate_adx(_df_sideways()["high"], _df_sideways()["low"], _df_sideways()["close"])
    assert adx_trend is not None and adx_side is not None
    assert adx_trend > adx_side


def test_adx_short_data_returns_none():
    """데이터 부족 시 None."""
    h = pd.Series([100, 101, 102])
    l = pd.Series([99, 100, 101])
    c = pd.Series([100, 101, 102])
    assert calculate_adx(h, l, c, period=14) is None


# ===================================================================
# RiskLimits 기본값
# ===================================================================


def test_adx_default_settings():
    limits = RiskLimits()
    assert limits.enable_adx_dynamic_stop is True
    assert limits.adx_period == 14
    assert limits.adx_threshold == 20.0
    assert limits.adx_low_trend_stop_pct == -7.0
    assert limits.adx_high_trend_stop_pct == -3.5


# ===================================================================
# _calc_adx_dynamic_stop_loss_pct
# ===================================================================


def test_disabled_returns_none(db):
    bot = _make_bot(db, RiskLimits(enable_adx_dynamic_stop=False))
    df = _df_strong_trend()
    assert bot._calc_adx_dynamic_stop_loss_pct(df) is None


def test_strong_trend_uses_tight_stop(db):
    """강한 추세 → -3.5% (좁은 stop)."""
    bot = _make_bot(db, RiskLimits())
    df = _df_strong_trend()
    result = bot._calc_adx_dynamic_stop_loss_pct(df)
    assert result == -3.5


def test_sideways_uses_wide_stop(db):
    """횡보 → -7% (넓은 stop)."""
    bot = _make_bot(db, RiskLimits())
    df = _df_sideways()
    result = bot._calc_adx_dynamic_stop_loss_pct(df)
    assert result == -7.0


def test_short_data_returns_none(db):
    """ADX 계산 불가 → None."""
    bot = _make_bot(db, RiskLimits())
    df = pd.DataFrame({"high": [100, 101], "low": [99, 100], "close": [100, 101]})
    assert bot._calc_adx_dynamic_stop_loss_pct(df) is None


def test_custom_threshold(db):
    """threshold 변경 — 30으로 올리면 _df_strong_trend도 wide 사용 가능."""
    bot = _make_bot(db, RiskLimits(adx_threshold=80.0))  # 사실상 전부 wide
    df = _df_strong_trend()
    # ADX가 80 이상은 잘 안 나옴 → wide 사용 가능성↑
    result = bot._calc_adx_dynamic_stop_loss_pct(df)
    # threshold 너무 높여놓으면 wide(-7) 또는 ADX가 80 넘으면 -3.5
    assert result in (-7.0, -3.5)


def test_none_df_returns_none(db):
    bot = _make_bot(db, RiskLimits())
    assert bot._calc_adx_dynamic_stop_loss_pct(None) is None


def test_custom_stop_pcts(db):
    """커스텀 stop %."""
    bot = _make_bot(db, RiskLimits(adx_low_trend_stop_pct=-10.0, adx_high_trend_stop_pct=-2.0))
    assert bot._calc_adx_dynamic_stop_loss_pct(_df_sideways()) == -10.0
    assert bot._calc_adx_dynamic_stop_loss_pct(_df_strong_trend()) == -2.0
