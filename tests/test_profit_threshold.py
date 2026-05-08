"""시장별 임계 + 기회비용 알고리즘 단위 테스트.

Related: #250
"""

from __future__ import annotations

import pytest

from cryptobot.bot.opportunity_cost import (
    CandidateSnapshot,
    PositionSnapshot,
    evaluate_rotation,
    score_candidate,
    score_position,
)
from cryptobot.bot.profit_threshold import (
    get_thresholds,
    passes_fee_guard,
    should_stop_loss,
    should_take_profit,
)

# ---- 시장별 임계 ----


def test_thresholds_by_market():
    """사용자 결정사항 (#250) — 시장별 임계 정확성."""
    upbit = get_thresholds("upbit")
    assert upbit.take_profit_pct == 3.0
    assert upbit.fee_guard_pct == 0.2

    kr = get_thresholds("kis_kr")
    assert kr.take_profit_pct == 4.0
    assert kr.fee_guard_pct == 0.4

    us = get_thresholds("kis_us")
    assert us.take_profit_pct == 5.0
    assert us.fee_guard_pct == 0.5


def test_unknown_market_raises():
    with pytest.raises(ValueError, match="미지원 시장"):
        get_thresholds("invalid_market")


@pytest.mark.parametrize(
    "market,pnl,expected",
    [
        ("upbit", 3.0, True),  # 코인 3% 도달
        ("upbit", 2.5, False),
        ("kis_kr", 4.0, True),
        ("kis_kr", 3.5, False),
        ("kis_us", 5.0, True),
        ("kis_us", 4.5, False),
    ],
)
def test_should_take_profit(market, pnl, expected):
    assert should_take_profit(market, pnl) is expected


@pytest.mark.parametrize(
    "market,pnl,expected",
    [
        ("upbit", -2.5, True),
        ("upbit", -2.0, False),
        ("kis_kr", -3.0, True),
        ("kis_kr", -2.5, False),
    ],
)
def test_should_stop_loss(market, pnl, expected):
    assert should_stop_loss(market, pnl) is expected


@pytest.mark.parametrize(
    "market,profit,expected",
    [
        ("upbit", 0.3, True),  # 코인 0.2% 통과
        ("upbit", 0.1, False),  # 차단
        ("kis_kr", 0.5, True),
        ("kis_kr", 0.3, False),
        ("kis_us", 0.6, True),
        ("kis_us", 0.4, False),
    ],
)
def test_fee_guard(market, profit, expected):
    assert passes_fee_guard(market, profit) is expected


# ---- 기회비용 평가 ----


def test_score_position_weak_signals():
    """과매수 + MA20 이탈 + MA5 < MA20 = 매도 압력."""
    pos = PositionSnapshot(
        symbol="005930",
        current_price=70_000,
        buy_price=70_000,
        rsi_14=75.0,  # 과매수 -20
        ma_5=69_000,  # MA5 < MA20 -15
        ma_20=72_000,  # 현재가 < MA20 -15
        held_minutes=1500,  # > 1일 -5
    )
    score = score_position(pos)
    # 60 - 20 - 15 - 15 - 5 = 5
    assert score == 5.0


def test_score_position_strong_holding():
    """RSI 정상 + MA 위 + 단기 강세 = 보유 유지."""
    pos = PositionSnapshot(
        symbol="005930",
        current_price=72_000,
        buy_price=70_000,
        rsi_14=55.0,
        ma_5=71_500,
        ma_20=70_500,
        held_minutes=300,
    )
    score = score_position(pos)
    assert score == 60.0  # 모든 약점 해당 X


def test_score_candidate_strong_signals():
    """RSI 회복 구간 + MA 위 + 골든크로스 + 신뢰도 높음."""
    cand = CandidateSnapshot(
        symbol="000660",
        current_price=150_000,
        rsi_14=40.0,  # 30~55 +20
        ma_5=152_000,  # MA5 > MA20 +15
        ma_20=148_000,  # 현재가 > MA20 +15
        confidence=0.7,  # > 0.6 +10
    )
    score = score_candidate(cand)
    assert score == 100.0  # 40 + 20 + 15 + 15 + 10 = 100


def test_score_candidate_weak():
    """과매수 + MA 이탈 = 후보 약함."""
    cand = CandidateSnapshot(
        symbol="000660",
        current_price=150_000,
        rsi_14=80.0,  # 30~55 밖
        ma_5=148_000,  # 골든크로스 X
        ma_20=152_000,  # 현재가 < MA20
        confidence=0.3,
    )
    score = score_candidate(cand)
    assert score == 40.0  # 기본값 그대로


def test_evaluate_rotation_recommends_when_diff_large():
    """약한 보유 + 강한 후보 → 회전 추천."""
    holdings = [
        PositionSnapshot(
            symbol="005930",
            current_price=70_000,
            buy_price=70_000,
            rsi_14=75,
            ma_5=69_000,
            ma_20=72_000,
            held_minutes=1500,
        ),
    ]  # score=5
    candidates = [
        CandidateSnapshot(
            symbol="000660",
            current_price=150_000,
            rsi_14=40,
            ma_5=152_000,
            ma_20=148_000,
            confidence=0.7,
        ),
    ]  # score=100
    decision = evaluate_rotation("kis_kr", holdings, candidates)
    assert decision.should_rotate is True
    assert decision.sell_symbol == "005930"
    assert decision.buy_symbol == "000660"
    assert decision.score_diff == 95.0


def test_evaluate_rotation_holds_when_diff_small():
    """매력도 차이가 비용 못 넘으면 보유 유지."""
    holdings = [
        PositionSnapshot(symbol="005930", current_price=70_000, buy_price=70_000, rsi_14=55),
    ]  # 60점
    candidates = [
        CandidateSnapshot(symbol="000660", current_price=150_000, rsi_14=80),
    ]  # 40점 (RSI 80 → 30~55 밖, 가산 X)
    decision = evaluate_rotation("kis_kr", holdings, candidates)
    assert decision.should_rotate is False
    assert "차이 부족" in decision.reason


def test_evaluate_rotation_empty():
    decision = evaluate_rotation("upbit", [], [])
    assert decision.should_rotate is False
    assert "부재" in decision.reason


def test_evaluate_rotation_same_symbol():
    """약점·후보가 같은 종목이면 회전 의미 없음."""
    holdings = [PositionSnapshot(symbol="NVDA", current_price=100, buy_price=100, rsi_14=75)]
    candidates = [CandidateSnapshot(symbol="NVDA", current_price=100, rsi_14=40)]
    decision = evaluate_rotation("kis_us", holdings, candidates)
    assert decision.should_rotate is False
    assert "동일 종목" in decision.reason


def test_market_threshold_affects_rotation_cost():
    """미국주식은 회전 비용 더 커서 같은 매력도 차이라도 더 보수적."""
    # 매력도 차 50 (= 25%로 환산) — 어떤 시장이든 회전
    holdings = [PositionSnapshot(symbol="A", current_price=100, buy_price=100, rsi_14=80, ma_20=110)]
    candidates = [CandidateSnapshot(symbol="B", current_price=100, rsi_14=40, ma_20=90, ma_5=95, confidence=0.7)]

    # 코인 (수수료 작음) → 회전 가능
    upbit_decision = evaluate_rotation("upbit", holdings, candidates)
    # 미국 (수수료 큼) → 같은 매력도라도 더 보수적
    us_decision = evaluate_rotation("kis_us", holdings, candidates)

    # 둘 다 차이 충분하면 회전. 차이 작으면 미국이 먼저 거부.
    # 본 테스트는 차이가 충분히 커서 둘 다 회전 가능 케이스
    assert upbit_decision.should_rotate is True
    assert us_decision.should_rotate is True
