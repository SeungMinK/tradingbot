"""#380: ATR 변동성 regime 분류 + adaptive 파라미터 테스트."""

from __future__ import annotations

import pandas as pd

from cryptobot.strategies.volatility_regime import (
    ADAPTIVE_PARAMS_DEFAULT,
    HIGH_VOL_THRESHOLD,
    LOW_VOL_THRESHOLD,
    adaptive_params,
    calc_atr,
    classify_regime,
)


def _make_ohlcv(closes: list[float], range_pct: float = 0.01) -> pd.DataFrame:
    """high/low를 close ±range_pct로 생성."""
    rows = []
    base = pd.Timestamp("2026-05-01")
    for i, c in enumerate(closes):
        rows.append({
            "date": base + pd.Timedelta(days=i),
            "open": c,
            "high": c * (1 + range_pct),
            "low": c * (1 - range_pct),
            "close": c,
            "volume": 1000,
        })
    return pd.DataFrame(rows).set_index("date")


# === calc_atr ===


def test_atr_returns_none_for_short_df():
    df = _make_ohlcv([100.0] * 10)
    assert calc_atr(df, period=14) is None


def test_atr_returns_value_for_sufficient_data():
    df = _make_ohlcv([100.0] * 30, range_pct=0.02)
    atr = calc_atr(df, period=14)
    assert atr is not None
    assert atr > 0


def test_atr_higher_for_more_volatile_data():
    """변동성 큰 데이터 → 더 큰 ATR."""
    low_vol_df = _make_ohlcv([100.0] * 30, range_pct=0.005)
    high_vol_df = _make_ohlcv([100.0] * 30, range_pct=0.05)
    low_atr = calc_atr(low_vol_df)
    high_atr = calc_atr(high_vol_df)
    assert high_atr > low_atr


def test_atr_none_for_empty():
    assert calc_atr(None) is None
    assert calc_atr(pd.DataFrame()) is None


# === classify_regime ===


def test_default_thresholds():
    assert LOW_VOL_THRESHOLD == 0.02
    assert HIGH_VOL_THRESHOLD == 0.05


def test_regime_low_for_small_atr_ratio():
    """ATR / price < 2% → low."""
    df = _make_ohlcv([100.0] * 30, range_pct=0.005)
    regime = classify_regime(df, current_price=100.0)
    assert regime == "low"


def test_regime_high_for_large_atr_ratio():
    """ATR / price > 5% → high. range_pct=0.04 → TR=0.08c → ratio 0.08 > 0.05."""
    df = _make_ohlcv([100.0] * 30, range_pct=0.04)
    regime = classify_regime(df, current_price=100.0)
    assert regime == "high"


def test_regime_normal_for_mid_atr_ratio():
    """2% < ATR / price < 5% → normal. range_pct=0.015 → TR=0.03c → ratio 0.03."""
    df = _make_ohlcv([100.0] * 30, range_pct=0.015)
    regime = classify_regime(df, current_price=100.0)
    assert regime == "normal"


def test_regime_fallback_for_insufficient_data():
    """데이터 부족 시 normal (안전 fallback)."""
    df = _make_ohlcv([100.0] * 5)
    assert classify_regime(df, current_price=100.0) == "normal"


def test_regime_fallback_for_zero_price():
    """current_price 0 이하 → normal."""
    df = _make_ohlcv([100.0] * 30)
    assert classify_regime(df, current_price=0) == "normal"
    assert classify_regime(df, current_price=-10) == "normal"


def test_regime_custom_thresholds():
    """임계 커스텀. range_pct=0.015 → TR=0.03c ratio 0.03."""
    df = _make_ohlcv([100.0] * 30, range_pct=0.015)
    # 디폴트 (2% / 5%): 0.03 → normal
    assert classify_regime(df, current_price=100.0) == "normal"
    # 임계 변경 (4% / 6%): 0.03 < 0.04 → low
    assert classify_regime(df, current_price=100.0, low_threshold=0.04, high_threshold=0.06) == "low"


# === adaptive_params ===


def test_adaptive_params_default_mapping():
    """디폴트 ADAPTIVE_PARAMS_DEFAULT 매핑 확인."""
    high = adaptive_params("high")
    assert high.min_profit_for_trailing == 7.0
    assert high.stop_loss_pct == -7.0
    assert high.trailing_stop_pct == -3.5
    assert high.regime == "high"

    normal = adaptive_params("normal")
    assert normal.min_profit_for_trailing == 5.0
    assert normal.stop_loss_pct == -5.0
    assert normal.trailing_stop_pct == -2.5

    low = adaptive_params("low")
    assert low.min_profit_for_trailing == 3.0
    assert low.stop_loss_pct == -3.0
    assert low.trailing_stop_pct == -1.5


def test_adaptive_params_unknown_regime_falls_back_to_normal():
    """잘못된 regime 이름 → normal."""
    ap = adaptive_params("unknown")
    assert ap.min_profit_for_trailing == 5.0
    assert ap.regime == "normal"


def test_adaptive_params_custom_table():
    """커스텀 매핑 테이블 사용."""
    custom = {"high": (10.0, -8.0, -4.0), "normal": (5.0, -5.0, -2.5), "low": (2.0, -2.0, -1.0)}
    ap = adaptive_params("high", table=custom)
    assert ap.min_profit_for_trailing == 10.0
    assert ap.stop_loss_pct == -8.0
    assert ap.trailing_stop_pct == -4.0


def test_adaptive_table_has_all_three_regimes():
    """디폴트 테이블에 high/normal/low 모두 포함."""
    for r in ("high", "normal", "low"):
        assert r in ADAPTIVE_PARAMS_DEFAULT


def test_adaptive_high_more_aggressive_than_low():
    """high regime의 min_profit > low regime."""
    high = adaptive_params("high")
    low = adaptive_params("low")
    assert high.min_profit_for_trailing > low.min_profit_for_trailing
    assert high.stop_loss_pct < low.stop_loss_pct  # 더 작은 음수 (더 깊은 손절)
