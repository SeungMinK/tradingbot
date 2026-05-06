"""#226: LongTermSwing 전략 단위 테스트.

진입 조건: F&G ≤ 30 AND 가격 < MA50 AND RSI ≤ 45 (3중 필터)
청산 조건: F&G ≥ 70 / ROI ≥ 20% / 14일+MA20 이탈 / stop_loss
"""

import numpy as np
import pandas as pd
import pytest

from cryptobot.strategies.base import StrategyParams
from cryptobot.strategies.long_term_swing import LongTermSwing


def _df(n: int = 60, trend_pct: float = 0.0):
    """OHLCV — 추세 비율 기반 가격 시리즈."""
    base = np.linspace(1000, 1000 * (1 + trend_pct), n)
    return pd.DataFrame({"open": base, "high": base * 1.01, "low": base * 0.99, "close": base, "volume": [100] * n})


# ===================================================================
# 메타
# ===================================================================


def test_strategy_info():
    s = LongTermSwing()
    info = s.info()
    assert info.name == "long_term_swing"
    assert "스윙" in info.display_name
    assert "bearish" in info.market_states


# ===================================================================
# 진입
# ===================================================================


def test_no_buy_when_data_insufficient():
    """50일 미만 → hold."""
    s = LongTermSwing()
    s.set_fear_greed(20)
    sig = s.check_buy(_df(n=30), 1000)
    assert sig.signal_type == "hold"
    assert "데이터 부족" in sig.reason


def test_no_buy_when_fear_greed_high():
    """F&G=60(중립) → hold (≤30 조건 미충족)."""
    s = LongTermSwing()
    s.set_fear_greed(60)
    df = _df(n=60, trend_pct=-0.1)  # 하락 추세 (RSI 낮음)
    sig = s.check_buy(df, 950)
    assert sig.signal_type == "hold"


def test_no_buy_when_price_above_ma():
    """가격 > MA50 → hold."""
    s = LongTermSwing()
    s.set_fear_greed(20)
    df = _df(n=60, trend_pct=-0.1)
    ma50 = df["close"].iloc[-50:].mean()
    sig = s.check_buy(df, ma50 + 100)  # MA50 위
    assert sig.signal_type == "hold"


def test_buy_when_all_conditions_met():
    """F&G≤30 + 가격<MA50 + RSI≤45 → buy."""
    s = LongTermSwing()
    s.set_fear_greed(15)
    # 단순 하락 추세 — RSI 낮음
    df = _df(n=60, trend_pct=-0.2)
    ma50 = df["close"].iloc[-50:].mean()
    sig = s.check_buy(df, ma50 * 0.9)  # MA 아래
    assert sig.signal_type == "buy"
    assert sig.confidence > 0.3


def test_default_fear_greed_is_neutral():
    """F&G 미주입 → 50(중립)이라 hold 유지."""
    s = LongTermSwing()  # set_fear_greed 호출 안 함
    df = _df(n=60, trend_pct=-0.2)
    ma50 = df["close"].iloc[-50:].mean()
    sig = s.check_buy(df, ma50 * 0.9)
    assert sig.signal_type == "hold"


# ===================================================================
# 청산
# ===================================================================


def test_sell_on_greed():
    """F&G≥70 + 수익 → 청산."""
    s = LongTermSwing()
    s.set_fear_greed(80)
    df = _df(n=60)
    sig = s.check_sell(df, current_price=1100, buy_price=1000)  # +10%
    assert sig.signal_type == "sell"
    assert "Greed" in sig.reason
    assert sig.is_profit_taking is True


def test_sell_on_take_profit():
    """ROI ≥ 20% → 청산."""
    s = LongTermSwing()
    s.set_fear_greed(50)  # 중립
    df = _df(n=60)
    sig = s.check_sell(df, current_price=1250, buy_price=1000)  # +25%
    assert sig.signal_type == "sell"
    assert "ROI" in sig.reason or "도달" in sig.reason


def test_hold_at_low_profit():
    """수익 < 20% + F&G 중립 → 보유 유지."""
    s = LongTermSwing()
    s.set_fear_greed(50)
    df = _df(n=60)
    sig = s.check_sell(df, current_price=1050, buy_price=1000)  # +5%
    assert sig.signal_type == "hold"


def test_sell_on_stop_loss():
    """기본 stop_loss(-5%) 무조건 발동."""
    p = StrategyParams(stop_loss_pct=-5.0)
    s = LongTermSwing(p)
    s.set_fear_greed(20)
    df = _df(n=60)
    sig = s.check_sell(df, current_price=940, buy_price=1000)  # -6%
    assert sig.signal_type == "sell"


# ===================================================================
# Confidence
# ===================================================================


def test_lower_fg_higher_confidence():
    """F&G가 더 낮으면 confidence 더 높음."""
    df = _df(n=60, trend_pct=-0.2)
    ma50 = df["close"].iloc[-50:].mean()

    s_extreme = LongTermSwing()
    s_extreme.set_fear_greed(5)
    sig_extreme = s_extreme.check_buy(df, ma50 * 0.9)

    s_mild = LongTermSwing()
    s_mild.set_fear_greed(28)
    sig_mild = s_mild.check_buy(df, ma50 * 0.9)

    if sig_extreme.signal_type == "buy" and sig_mild.signal_type == "buy":
        assert sig_extreme.confidence >= sig_mild.confidence


# ===================================================================
# Registry
# ===================================================================


def test_strategy_registered_in_selector():
    """strategy_selector.STRATEGY_CLASSES에 등록 — 봇 시작 시 로드 가능."""
    from cryptobot.bot.strategy_selector import STRATEGY_CLASSES
    assert "long_term_swing" in STRATEGY_CLASSES
    assert STRATEGY_CLASSES["long_term_swing"] is LongTermSwing
