"""#380: ATR 기반 변동성 regime 분류 + adaptive 파라미터.

학계 컨센서스:
- mean reversion (BB+RSI) → low-vol/sideways regime 강함
- high-vol 추세장 → mean reversion false signal 다수
- 따라서 regime별 다른 risk 파라미터 적용이 정석

분류 기준:
- ATR / 현재가 비율 (normalized volatility)
- low: < 2%  (잔잔, 작은 swing 가능)
- normal: 2~5% (디폴트 모드)
- high: > 5%  (변동성 큼, 큰 추세 또는 노이즈)
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# 디폴트 임계값 (정규화된 ATR / price)
LOW_VOL_THRESHOLD = 0.02   # 2%
HIGH_VOL_THRESHOLD = 0.05  # 5%

# Regime별 adaptive 파라미터 (디폴트)
# 키 = regime 이름, 값 = (min_profit_for_trailing, stop_loss_pct, trailing_stop_pct)
ADAPTIVE_PARAMS_DEFAULT: dict[str, tuple[float, float, float]] = {
    "high":   (7.0, -7.0, -3.5),   # 변동성 큼 → 크게 노리고 손절 폭 넓힘
    "normal": (5.0, -5.0, -2.5),   # 디폴트 (PR1 셋팅)
    "low":    (3.0, -3.0, -1.5),   # 잔잔 → 작게 자주, 작은 손실로 끊음
}


@dataclass
class AdaptiveParams:
    """regime별 adaptive 파라미터 묶음."""

    min_profit_for_trailing: float
    stop_loss_pct: float
    trailing_stop_pct: float
    regime: str  # 어느 regime에서 나왔는지 (로그/디버그용)


def calc_atr(df: pd.DataFrame, period: int = 14) -> float | None:
    """ATR(period) 마지막 봉 값. True Range의 rolling mean.

    Args:
        df: high/low/close 컬럼 있는 OHLCV
        period: ATR 기간 (디폴트 14)

    Returns:
        ATR 값. 데이터 부족 시 None.
    """
    if df is None or len(df) < period + 1:
        return None

    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(window=period).mean().iloc[-1]
    if pd.isna(atr):
        return None
    return float(atr)


def classify_regime(
    df: pd.DataFrame,
    current_price: float,
    period: int = 14,
    low_threshold: float = LOW_VOL_THRESHOLD,
    high_threshold: float = HIGH_VOL_THRESHOLD,
) -> str:
    """변동성 regime 분류.

    Returns:
        "high" / "normal" / "low" 중 하나. 데이터 부족 시 "normal" (안전 디폴트).
    """
    atr = calc_atr(df, period)
    if atr is None or current_price <= 0:
        return "normal"

    ratio = atr / current_price
    if ratio < low_threshold:
        return "low"
    if ratio > high_threshold:
        return "high"
    return "normal"


def adaptive_params(
    regime: str,
    table: dict[str, tuple[float, float, float]] | None = None,
) -> AdaptiveParams:
    """regime → AdaptiveParams 매핑.

    Args:
        regime: "high" / "normal" / "low"
        table: 커스텀 매핑 (없으면 디폴트 ADAPTIVE_PARAMS_DEFAULT)

    Returns:
        AdaptiveParams. 알 수 없는 regime은 "normal"로 처리 (안전 fallback).
    """
    t = table or ADAPTIVE_PARAMS_DEFAULT
    if regime not in t:
        regime = "normal"
    mp, sl, ts = t[regime]
    return AdaptiveParams(
        min_profit_for_trailing=mp,
        stop_loss_pct=sl,
        trailing_stop_pct=ts,
        regime=regime,
    )
