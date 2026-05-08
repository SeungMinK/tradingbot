"""시장별 익절/손절/수수료 가드 임계 모듈.

거래비용 격차에 맞춘 시장별 차등 임계 lookup.
- 코인(왕복 ~0.1%): 익절 3%+, 수수료 가드 0.2%
- 한국주식(거래세 0.18% + 수수료): 익절 4%+, 수수료 가드 0.4%
- 미국주식(환전 + 수수료): 익절 5%+, 수수료 가드 0.5%

운영 중 코인 봇 영향 최소화를 위해 lookup 함수만 제공. 호출처에서 명시 적용.

Related: #250
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MarketThresholds:
    """시장별 매매 임계."""

    take_profit_pct: float  # 익절 트리거 (%, 양수)
    stop_loss_pct: float  # 손절 트리거 (%, 음수)
    fee_guard_pct: float  # 수수료 가드 — 예상 수익이 이보다 작으면 매매 차단 (%)
    name: str  # 시장 식별자 ("upbit" / "kis_kr" / "kis_us")


# 시장별 임계 정의 (사용자 결정사항 #250)
# 사용자 원칙: "수익 1%로 안 팔고 3%+에서", "거래세·환전 흡수 위해 한국 4%, 미국 5%"
THRESHOLDS_BY_MARKET: dict[str, MarketThresholds] = {
    "upbit": MarketThresholds(
        take_profit_pct=3.0,
        stop_loss_pct=-2.5,
        fee_guard_pct=0.2,
        name="upbit",
    ),
    "kis_kr": MarketThresholds(
        take_profit_pct=4.0,
        stop_loss_pct=-3.0,
        fee_guard_pct=0.4,
        name="kis_kr",
    ),
    "kis_us": MarketThresholds(
        take_profit_pct=5.0,
        stop_loss_pct=-3.0,
        fee_guard_pct=0.5,
        name="kis_us",
    ),
}


def get_thresholds(market: str) -> MarketThresholds:
    """시장 식별자로 임계 조회.

    Args:
        market: "upbit" / "kis_kr" / "kis_us"

    Returns:
        해당 시장의 MarketThresholds

    Raises:
        ValueError: 미지원 시장
    """
    if market not in THRESHOLDS_BY_MARKET:
        raise ValueError(f"미지원 시장: {market} (지원: {list(THRESHOLDS_BY_MARKET.keys())})")
    return THRESHOLDS_BY_MARKET[market]


def should_take_profit(market: str, pnl_pct: float) -> bool:
    """현재 손익률이 익절 임계 도달했는지."""
    return pnl_pct >= get_thresholds(market).take_profit_pct


def should_stop_loss(market: str, pnl_pct: float) -> bool:
    """현재 손익률이 손절 임계 도달했는지."""
    return pnl_pct <= get_thresholds(market).stop_loss_pct


def passes_fee_guard(market: str, expected_profit_pct: float) -> bool:
    """예상 수익률이 시장별 수수료 가드 임계 이상인지.

    잦은 매매로 수수료가 알파를 갉아먹는 것을 방지.

    Args:
        market: 시장 식별자
        expected_profit_pct: 예상 수익률 (%, 절대값)

    Returns:
        True면 매매 허용, False면 차단
    """
    return abs(expected_profit_pct) >= get_thresholds(market).fee_guard_pct
