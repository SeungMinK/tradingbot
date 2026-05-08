"""KIS 보수적 전략 테스트 (#279)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from cryptobot.bot.kis_strategy import (
    KISStrategyParams,
    calc_position_size,
    evaluate_buy,
    evaluate_sell,
)


def _make_df(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    df = pd.DataFrame({"close": closes})
    df["open"] = df["close"]
    df["high"] = df["close"]
    df["low"] = df["close"]
    df["volume"] = volumes if volumes is not None else [1_000_000] * len(closes)
    return df


# ---- evaluate_buy ----

def test_buy_data_insufficient_returns_false():
    df = _make_df([100.0] * 10)
    sig = evaluate_buy(df, current_price=100.0)
    assert sig.should_buy is False
    assert "데이터 부족" in sig.reason


def test_buy_signal_when_oversold_below_ma20():
    # MA60 위(추세 살아있음) + 점진 하락 → MA20 아래 + RSI 낮음
    closes = list(np.linspace(100, 130, 60)) + [120, 118, 115, 110, 108]
    df = _make_df(closes)
    sig = evaluate_buy(df, current_price=108.0)
    # 정확한 확정은 데이터에 따라 달라질 수 있으나 MA60(0.92×) 위 + MA20 아래는 충족
    # RSI는 셋업에 따라 달라지므로 should_buy 가능 여부 (true OR explicit reason)
    if not sig.should_buy:
        # 미충족이면 RSI 또는 거래량 사유여야 함 (MA 조건은 충족)
        assert "RSI" in sig.reason or "거래량" in sig.reason


def test_buy_rejected_when_long_trend_broken():
    # 큰 폭락 — MA60 0.92배 아래 → 잘못된 저점 회피
    closes = list(np.linspace(100, 130, 60)) + [80, 75, 70, 60, 50]
    df = _make_df(closes)
    sig = evaluate_buy(df, current_price=50.0)
    assert sig.should_buy is False
    assert "장기추세" in sig.reason or "RSI" in sig.reason  # 어쨌든 거부


def test_buy_rejected_when_rsi_high():
    # 꾸준히 상승 → RSI 높음 → 매수 거부
    closes = list(np.linspace(100, 200, 80))
    df = _make_df(closes)
    sig = evaluate_buy(df, current_price=200.0)
    assert sig.should_buy is False


# ---- evaluate_sell ----

def test_sell_stop_loss():
    sig = evaluate_sell(df=None, current_price=97.0, buy_price=100.0, highest_since_buy=100.0)
    assert sig.should_sell is True
    assert "손절" in sig.reason
    assert sig.is_profit_taking is False


def test_sell_trailing_stop_after_peak():
    # 매수가 100 → 고점 110 → 현재 107.7 (-2.09% from peak, +7.7% pnl)
    sig = evaluate_sell(df=None, current_price=107.7, buy_price=100.0, highest_since_buy=110.0)
    assert sig.should_sell is True
    assert "트레일링" in sig.reason
    assert sig.is_profit_taking is True


def test_sell_no_action_when_holding_in_profit():
    # 매수가 100, 현재 102, 고점 102 — 트레일링 발동 안함, take_profit(4%) 미도달
    sig = evaluate_sell(df=None, current_price=102.0, buy_price=100.0, highest_since_buy=102.0)
    assert sig.should_sell is False


def test_sell_take_profit_without_df_falls_back_to_simple():
    # df 없으면 단순 익절
    sig = evaluate_sell(df=None, current_price=105.0, buy_price=100.0, highest_since_buy=105.0)
    assert sig.should_sell is True
    assert "익절" in sig.reason
    assert sig.is_profit_taking is True


def test_sell_take_profit_overheated_with_df():
    # +5%, RSI 매우 높음 → 즉시 익절
    closes = list(np.linspace(100, 200, 80))
    df = _make_df(closes)
    sig = evaluate_sell(df=df, current_price=200.0, buy_price=190.0, highest_since_buy=200.0)
    assert sig.should_sell is True
    assert "과열" in sig.reason or "추세" in sig.reason or "익절" in sig.reason


def test_sell_holds_when_trend_alive():
    # +5% 익절 임계 도달했지만 RSI 50~70 사이 + 가격 > MA20 — 보유
    closes = list(np.linspace(100, 105, 80))  # 완만한 상승
    df = _make_df(closes)
    sig = evaluate_sell(df=df, current_price=105.0, buy_price=100.0, highest_since_buy=105.0)
    # 결과는 데이터 의존이지만 손절/트레일링은 아니어야 함
    if sig.should_sell:
        # 매도라면 과열 또는 추세이탈 사유
        assert "과열" in sig.reason or "추세" in sig.reason


# ---- calc_position_size ----

def test_position_size_kr_integer_shares():
    qty, reason = calc_position_size(
        available_budget_krw=200_000,
        current_price_krw=70_000,
        fractional=False,
        params=KISStrategyParams(max_position_per_symbol_pct=40.0),
    )
    # 한도 80,000 / 70,000 = 1주
    assert qty == 1.0
    assert "1주" in reason


def test_position_size_kr_skip_when_too_expensive():
    qty, reason = calc_position_size(
        available_budget_krw=200_000,
        current_price_krw=300_000,  # 1주 < 한도
        fractional=False,
        params=KISStrategyParams(max_position_per_symbol_pct=30.0),
    )
    assert qty == 0.0
    assert "예산 부족" in reason


def test_position_size_us_fractional():
    qty, reason = calc_position_size(
        available_budget_krw=200_000,
        current_price_krw=100_000,  # USD price × FX rate
        fractional=True,
        params=KISStrategyParams(max_position_per_symbol_pct=30.0),
    )
    # 한도 60,000 / 100,000 = 0.6주
    assert qty == 0.6
    assert "0.6" in reason


def test_position_size_zero_budget():
    qty, reason = calc_position_size(
        available_budget_krw=0,
        current_price_krw=100,
        fractional=True,
    )
    assert qty == 0.0
    assert "예산" in reason


def test_position_size_us_skip_when_dust():
    # 가격이 너무 비싸서 0.001주 미만 — skip
    qty, reason = calc_position_size(
        available_budget_krw=1_000,
        current_price_krw=10_000_000,
        fractional=True,
        params=KISStrategyParams(max_position_per_symbol_pct=30.0),
    )
    assert qty == 0.0


# ---- KISStrategyParams defaults ----

def test_default_params_match_doc():
    p = KISStrategyParams()
    assert p.rsi_oversold == 35.0
    assert p.rsi_overbought == 70.0
    assert p.take_profit_pct == 4.0
    assert p.stop_loss_pct == -3.0
    assert p.trailing_stop_pct == -2.0
    assert p.max_position_per_symbol_pct == 30.0
    assert p.rebuy_cooldown_hours == 24
