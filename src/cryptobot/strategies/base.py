"""전략 베이스 클래스.

NestJS의 interface/abstract class와 동일한 역할.
모든 매매 전략은 이 클래스를 상속해서 구현한다.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Signal:
    """매매 신호. 모든 전략이 공통으로 반환하는 결과."""

    signal_type: str  # "buy" / "sell" / "hold"
    confidence: float  # 0.0 ~ 1.0
    reason: str  # 신호 발생 사유
    trigger_value: float | None = None  # 돌파 기준가 등
    stop_loss: float | None = None  # 이 신호에 대한 권장 손절가
    take_profit: float | None = None  # 이 신호에 대한 권장 익절가
    # 익절성 매도(ROI/트레일링/밴드 중간선 등) 여부.
    # main.py의 fee guard가 "수수료 커버 안 되면 스킵" 결정에 사용.
    # 손절성/전략적 매도(RSI 정상복귀 등)는 False. 각 전략이 명시적으로 설정.
    is_profit_taking: bool = False


@dataclass
class StrategyInfo:
    """전략 메타 정보."""

    name: str  # 전략 식별자 (예: "volatility_breakout")
    display_name: str  # 화면 표시명 (예: "변동성 돌파")
    description: str  # 전략 설명
    market_states: list[str]  # 적합한 시장 상태 ["bullish", "sideways", "bearish"]
    timeframe: str  # 권장 타임프레임 ("1m", "5m", "1h", "1d")
    difficulty: str  # 구현 난이도 ("easy", "medium", "hard")


@dataclass
class StrategyParams:
    """전략 공통 파라미터."""

    stop_loss_pct: float = -5.0  # 손절률 (%)
    trailing_stop_pct: float = -3.0  # 트레일링 스탑 (%)
    position_size_pct: float = 100.0  # 포지션 크기 (잔고 대비 %)
    extra: dict = field(default_factory=dict)  # 전략별 추가 파라미터

    # 시간 기반 ROI 테이블: {보유 분: 최소 수익%}
    # 보유 시간이 길어질수록 목표 수익을 낮춤
    # #224: 시간 구간 확장 (30/60/120 → 120/240/600분). 실측 보유 시간이 247~781분이라
    # 기존 120분 컷에 조기 매도 빈발. 봇 매매 주기(LLM 55분/tick 60초)와 안 맞아
    # "진짜 오래 들고 가야 하는 거래"가 120분 ROI에 막혀 수익 못 크게 가져감.
    roi_table: dict = field(
        default_factory=lambda: {
            10: 3.5,  # 10분 내 +3.5% 이상이면 매도 (급등 포착)
            120: 2.5,  # 2시간 내 +2.5%
            240: 1.8,  # 4시간 내 +1.8%
            600: 1.0,  # 10시간 내 실질 +1.0% 이상이면 탈출 (자본 회수)
        }
    )


class BaseStrategy(ABC):
    """매매 전략 베이스 클래스.

    NestJS의 abstract class + interface와 동일.
    모든 전략은 이 클래스를 상속하고 아래 메서드를 구현해야 한다.
    """

    def __init__(self, params: StrategyParams | None = None) -> None:
        self.params = params or StrategyParams()
        self._highest_price: float | None = None  # 트레일링 스탑용
        self._hold_minutes: int = 0  # 보유 시간 (main.py에서 설정)

    @abstractmethod
    def info(self) -> StrategyInfo:
        """전략 메타 정보 반환."""

    @abstractmethod
    def check_buy(self, df: pd.DataFrame, current_price: float) -> Signal:
        """매수 신호 판단.

        Args:
            df: OHLCV DataFrame (최근 N일 봉 데이터)
            current_price: 현재가

        Returns:
            매수 신호 또는 hold
        """

    @abstractmethod
    def check_sell(self, df: pd.DataFrame, current_price: float, buy_price: float) -> Signal:
        """매도 신호 판단.

        Args:
            df: OHLCV DataFrame
            current_price: 현재가
            buy_price: 매수가

        Returns:
            매도 신호 또는 hold
        """

    # 업비트 왕복 수수료: 매수 0.05% + 매도 0.05% = 0.1%
    ROUND_TRIP_FEE_PCT = 0.1

    def _net_pnl_pct(self, pnl_pct: float) -> float:
        """가격 수익률에서 수수료를 빼 실질 수익률 계산."""
        return pnl_pct - self.ROUND_TRIP_FEE_PCT

    def check_trailing_stop(
        self,
        current_price: float,
        buy_price: float,
        hold_minutes: int | None = None,
        current_rsi: float | None = None,
    ) -> Signal | None:
        """공통 트레일링 스탑 + 손절 + ROI + 수수료 반영."""
        # 최고가 갱신
        if self._highest_price is None or current_price > self._highest_price:
            self._highest_price = current_price

        pnl_pct = (current_price - buy_price) / buy_price * 100
        net_pnl = self._net_pnl_pct(pnl_pct)

        # 손절 — 무조건 실행 (수수료 무시, RSI 무시)
        # 손절은 is_profit_taking=False — fee guard에 막히면 안 됨
        if pnl_pct <= self.params.stop_loss_pct:
            return Signal("sell", 1.0, "손절", trigger_value=round(pnl_pct, 2), is_profit_taking=False)

        # RSI 과매도 판단 (전략별 oversold 기준)
        rsi_oversold = self.params.extra.get("rsi_oversold", self.params.extra.get("oversold", 30))
        is_oversold = current_rsi is not None and current_rsi <= rsi_oversold

        # 시간 기반 ROI — RSI 신뢰도 비교
        hold_minutes = hold_minutes if hold_minutes is not None else self._hold_minutes
        if hold_minutes > 0 and self.params.roi_table:
            for minutes, min_roi in sorted(self.params.roi_table.items()):
                if hold_minutes >= minutes and net_pnl >= min_roi and net_pnl > 0:
                    # RSI 과매도 시 ROI 크기로 판단
                    if is_oversold:
                        # ROI가 손절폭의 50% 이상이면 충분한 수익 → 매도
                        strong_roi = abs(self.params.stop_loss_pct) * 0.5
                        if net_pnl >= strong_roi:
                            return Signal(
                                "sell",
                                0.9,
                                f"ROI 강제 (RSI={current_rsi:.0f} 과매도이나 실질 +{net_pnl:.2f}% 충분)",
                                trigger_value=round(net_pnl, 2),
                                is_profit_taking=True,
                            )
                        return None  # RSI 과매도 + ROI 약함 → 매도 보류
                    return Signal(
                        "sell",
                        0.9,
                        f"ROI 도달 ({hold_minutes}분 보유, 실질 +{net_pnl:.2f}% >= {min_roi}%)",
                        trigger_value=round(net_pnl, 2),
                        is_profit_taking=True,
                    )

        # 트레일링 스탑 — 실질 수익이 있을 때만 매도
        drop_pct = (current_price - self._highest_price) / self._highest_price * 100
        if drop_pct <= self.params.trailing_stop_pct:
            if net_pnl > 0:
                return Signal(
                    "sell",
                    0.8,
                    f"트레일링 스탑 (실질 {net_pnl:+.2f}%)",
                    trigger_value=round(drop_pct, 2),
                    is_profit_taking=True,
                )
            return None

        return None

    def reset(self) -> None:
        """포지션 종료 시 내부 상태 초기화."""
        self._highest_price = None
