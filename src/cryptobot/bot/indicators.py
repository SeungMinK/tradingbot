"""기술적 지표 계산 모듈.

NestJS의 Service 레이어와 동일한 역할.
pandas DataFrame을 받아서 RSI, 이동평균, 볼린저밴드, ATR 등을 계산한다.
"""

import numpy as np
import pandas as pd


def calculate_rsi(prices: pd.Series, period: int = 14) -> float | None:
    """RSI (Relative Strength Index) 계산.

    Args:
        prices: 종가 시리즈 (최소 period+1 개)
        period: RSI 기간 (기본 14)

    Returns:
        RSI 값 (0~100), 데이터 부족 시 None
    """
    if len(prices) < period + 1:
        return None

    delta = prices.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()

    last_avg_gain = avg_gain.iloc[-1]
    last_avg_loss = avg_loss.iloc[-1]

    if last_avg_loss == 0:
        return 100.0

    rs = last_avg_gain / last_avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def calculate_ma(prices: pd.Series, period: int) -> float | None:
    """단순 이동평균 (SMA) 계산.

    Args:
        prices: 종가 시리즈
        period: 이동평균 기간

    Returns:
        이동평균 값, 데이터 부족 시 None
    """
    if len(prices) < period:
        return None
    return round(prices.rolling(window=period).mean().iloc[-1], 2)


def calculate_bollinger_bands(prices: pd.Series, period: int = 20, num_std: float = 2.0) -> tuple[float, float] | None:
    """볼린저밴드 상단/하단 계산.

    Args:
        prices: 종가 시리즈
        period: 이동평균 기간 (기본 20)
        num_std: 표준편차 배수 (기본 2.0)

    Returns:
        (upper, lower) 튜플, 데이터 부족 시 None
    """
    if len(prices) < period:
        return None

    sma = prices.rolling(window=period).mean().iloc[-1]
    std = prices.rolling(window=period).std().iloc[-1]

    upper = round(sma + num_std * std, 2)
    lower = round(sma - num_std * std, 2)
    return upper, lower


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float | None:
    """ATR (Average True Range) 계산.

    Args:
        high: 고가 시리즈
        low: 저가 시리즈
        close: 종가 시리즈
        period: ATR 기간 (기본 14)

    Returns:
        ATR 값, 데이터 부족 시 None
    """
    if len(close) < period + 1:
        return None

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = tr.rolling(window=period).mean().iloc[-1]
    return round(atr, 2) if not np.isnan(atr) else None


def calculate_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float | None:
    """ADX (Average Directional Index) — 추세 강도 0~100.

    > 25: 강한 추세 / < 20: 약한 추세 (횡보) / 20~25: 모호.
    추세 *강도*만 측정 — 방향(상승/하락)과 무관.

    #214: 동적 stop_loss 결정에 활용. 횡보장(ADX<20)에선 stop을 넓혀 노이즈 손절 방지,
    추세장(ADX>=20)에선 좁혀 빠른 손절. 백테스트 결과 4전략 중 3개 개선.

    Args:
        high, low, close: OHLCV 시리즈
        period: ADX 기간 (기본 14)

    Returns:
        ADX 값 (0~100), 데이터 부족 시 None
    """
    if len(close) < period * 2:
        return None

    high_diff = high.diff()
    low_diff = -low.diff()
    plus_dm = high_diff.clip(lower=0).where(high_diff > low_diff, 0)
    minus_dm = low_diff.clip(lower=0).where(low_diff > high_diff, 0)

    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()

    plus_di = 100 * (plus_dm.rolling(period).mean() / atr).fillna(0)
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr).fillna(0)
    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    adx = dx.rolling(period).mean().iloc[-1]
    return round(float(adx), 2) if pd.notna(adx) else None


def calculate_all(df: pd.DataFrame) -> dict:
    """OHLCV DataFrame으로부터 모든 지표를 한번에 계산.

    Args:
        df: 컬럼에 'close', 'high', 'low'가 포함된 DataFrame

    Returns:
        모든 지표가 담긴 dict
    """
    close = df["close"]
    high = df["high"]
    low = df["low"]

    bb = calculate_bollinger_bands(close)

    return {
        "rsi_14": calculate_rsi(close),
        "ma_5": calculate_ma(close, 5),
        "ma_20": calculate_ma(close, 20),
        "ma_60": calculate_ma(close, 60),
        "bb_upper": bb[0] if bb else None,
        "bb_lower": bb[1] if bb else None,
        "atr_14": calculate_atr(high, low, close),
    }
