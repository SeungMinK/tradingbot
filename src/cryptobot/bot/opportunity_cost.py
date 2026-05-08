"""기회비용 평가 알고리즘.

보유 종목과 후보 종목의 매력도를 비교하여 회전(보유 매도 → 후보 매수) 추천 여부 결정.

핵심 룰 (1차, AI 통합은 추후):
- 보유 종목 "약함": RSI > 70 (과매수) 또는 추세 약화 (현재가 < MA20)
- 후보 종목 "강함": RSI 30~55 + 현재가 > MA20 + 추세 강화 (MA5 > MA20)
- 회전 트리거: 보유 약함 + 후보 강함 + 매력도 차이가 회전 비용(수수료 왕복) + 안전 마진 초과

사용자 원칙 (#250):
- "잡주식 X, 든든한 애들로"
- "수익 1%로 안 팔고 3%+에서"
- "기회비용 잘 계산해서 알고리즘 강화"

Related: #250
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from cryptobot.bot.profit_threshold import get_thresholds

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PositionSnapshot:
    """현재 포지션의 시장 지표 스냅샷."""

    symbol: str
    current_price: float
    buy_price: float
    rsi_14: float | None = None
    ma_5: float | None = None
    ma_20: float | None = None
    held_minutes: int = 0  # 보유 시간 (분)

    @property
    def pnl_pct(self) -> float:
        if self.buy_price <= 0:
            return 0.0
        return (self.current_price - self.buy_price) / self.buy_price * 100


@dataclass(frozen=True)
class CandidateSnapshot:
    """매수 후보 종목의 시장 지표."""

    symbol: str
    current_price: float
    rsi_14: float | None = None
    ma_5: float | None = None
    ma_20: float | None = None
    confidence: float = 0.5  # 매수 신호 신뢰도 (0~1)


@dataclass(frozen=True)
class RotationDecision:
    """회전 결정 결과."""

    should_rotate: bool
    sell_symbol: str | None
    buy_symbol: str | None
    reason: str
    score_diff: float  # 후보 매력도 - 보유 매력도 (높을수록 회전 유리)


def score_position(pos: PositionSnapshot) -> float:
    """보유 종목 매력도 점수.

    낮을수록 매도 후보(회전 대상). 0~100 스케일.
    - RSI > 70: -20점 (과매수, 익절 압력)
    - 현재가 < MA20: -15점 (추세 약화)
    - MA5 < MA20: -15점 (단기 약세)
    - PnL > 익절 임계의 70%: -10점 (익절 임박, 다른 기회 검토 가치)
    - 보유 시간 > 1440분(1일): -5점 (자본 회전 압력)
    기본 60점.
    """
    score = 60.0

    if pos.rsi_14 is not None and pos.rsi_14 > 70:
        score -= 20
    if pos.ma_20 is not None and pos.current_price < pos.ma_20:
        score -= 15
    if pos.ma_5 is not None and pos.ma_20 is not None and pos.ma_5 < pos.ma_20:
        score -= 15
    if pos.held_minutes > 1440:
        score -= 5
    return max(0.0, min(100.0, score))


def score_candidate(cand: CandidateSnapshot) -> float:
    """후보 종목 매력도 점수.

    높을수록 매수 가치. 0~100 스케일.
    - RSI 30~55 (과매도 회복 구간): +20점
    - 현재가 > MA20: +15점 (추세 확인)
    - MA5 > MA20 (골든크로스 영역): +15점
    - confidence > 0.6: +10점
    기본 40점.
    """
    score = 40.0

    if cand.rsi_14 is not None and 30 <= cand.rsi_14 <= 55:
        score += 20
    if cand.ma_20 is not None and cand.current_price > cand.ma_20:
        score += 15
    if cand.ma_5 is not None and cand.ma_20 is not None and cand.ma_5 > cand.ma_20:
        score += 15
    if cand.confidence > 0.6:
        score += 10

    return max(0.0, min(100.0, score))


def evaluate_rotation(
    market: str,
    holdings: list[PositionSnapshot],
    candidates: list[CandidateSnapshot],
    safety_margin_pct: float = 1.0,
) -> RotationDecision:
    """기회비용 기반 회전 평가.

    Args:
        market: 시장 식별자 ("upbit" / "kis_kr" / "kis_us")
        holdings: 현재 보유 포지션 스냅샷 목록
        candidates: 매수 후보 스냅샷 목록
        safety_margin_pct: 회전 비용 외 추가 마진 (%, 기본 1%)

    Returns:
        RotationDecision — should_rotate=True면 sell_symbol → buy_symbol로 회전 추천

    회전 트리거:
        후보 점수 - 보유 점수 > (수수료 왕복 + 안전 마진)을 점수로 환산한 값
        (점수 1점 ≈ 0.5%로 환산. 즉 fee_guard 0.2% + safety 1% = 1.2% → 2.4점 이상 차이 필요)
    """
    if not holdings or not candidates:
        return RotationDecision(False, None, None, "보유 또는 후보 부재", 0.0)

    # 가장 약한 보유 종목 (회전 대상)
    holdings_scored = sorted(holdings, key=score_position)
    weakest = holdings_scored[0]
    weakest_score = score_position(weakest)

    # 가장 강한 후보 (매수 대상)
    candidates_scored = sorted(candidates, key=score_candidate, reverse=True)
    strongest = candidates_scored[0]
    strongest_score = score_candidate(strongest)

    # 같은 종목이면 회전 의미 없음
    if weakest.symbol == strongest.symbol:
        return RotationDecision(False, None, None, "약점·후보 동일 종목", 0.0)

    score_diff = strongest_score - weakest_score

    # 회전 비용 (수수료 왕복) — 시장별 임계의 2배 (매도 + 매수)
    fee_round_trip = get_thresholds(market).fee_guard_pct * 2
    cost_pct = fee_round_trip + safety_margin_pct
    # 점수 1점 ≈ 0.5% 가치 가정
    required_score_diff = cost_pct / 0.5

    if score_diff < required_score_diff:
        return RotationDecision(
            False,
            None,
            None,
            f"매력도 차이 부족: {score_diff:.1f} < {required_score_diff:.1f} (비용 {cost_pct:.1f}%)",
            score_diff,
        )

    return RotationDecision(
        True,
        weakest.symbol,
        strongest.symbol,
        f"회전 추천: {weakest.symbol}(매력도 {weakest_score:.0f}) → "
        f"{strongest.symbol}(매력도 {strongest_score:.0f}, 차 {score_diff:.1f})",
        score_diff,
    )
