"""장기 스윙 전략 — 진득한 매매로 수수료/잡음 회피.

설계 의도:
- 잦은 매매(일 8건)가 수수료 0.1% > 건당 EV 0.058%로 구조적 적자였음.
- "저점에 사서 고점에 팔기" 식 진득한 포지션 트레이딩으로 전환.
- 메이저 코인(BTC, XRP) 위주, 평균 보유 10~16일.

진입 조건 (모두 충족):
  - 공포탐욕지수(F&G) ≤ fear_threshold (기본 30, 극도 공포)
  - 가격이 50일 MA 아래 (저평가 영역)
  - RSI(14d) ≤ rsi_entry_max (기본 45, 약한 과매도)

청산 조건 (하나라도 충족):
  - F&G ≥ greed_threshold (기본 70) — 시장 과열, 차익실현
  - ROI ≥ take_profit_pct (기본 +20%)
  - stop_loss (BaseStrategy 공통 처리) — 기본 -15% (진득 보유라 더 넓게)
  - 보유 14일 이상 + 가격 < MA(20) — 추세 이탈

122일 백테스트 (2025-12 ~ 2026-04 약세장):
  BTC: -11.85% (Buy&Hold -22.61%, +10.76%p)
  XRP:  -2.84% (Buy&Hold -27.65%, +24.81%p)
  ETH: -49.62% (-15% 갭다운 두 번, 일봉 한계 — 분봉이면 더 정확)
"""

import logging

import pandas as pd

from cryptobot.bot.indicators import calculate_rsi
from cryptobot.strategies.base import BaseStrategy, Signal, StrategyInfo, StrategyParams

logger = logging.getLogger(__name__)


class LongTermSwing(BaseStrategy):
    """장기 스윙 — 공포탐욕지수 + MA + RSI 결합 진득한 매매."""

    def __init__(self, params: StrategyParams | None = None) -> None:
        super().__init__(params)
        e = self.params.extra
        self._ma_long = int(e.get("ma_long", 50))  # 저평가 판정용
        self._ma_short = int(e.get("ma_short", 20))  # 추세 이탈 판정용
        self._rsi_period = int(e.get("rsi_period", 14))
        self._rsi_entry_max = float(e.get("rsi_entry_max", 45))
        self._fear_threshold = float(e.get("fear_threshold", 30))  # 진입 조건
        self._greed_threshold = float(e.get("greed_threshold", 70))  # 청산 조건
        self._take_profit_pct = float(e.get("take_profit_pct", 20.0))
        self._min_hold_days = int(e.get("min_hold_days", 7))  # 매매 빈도 보호

        # 외부에서 주입되는 공포탐욕지수 (없으면 50=중립으로 폴백)
        # main.py에서 매 tick 갱신해야 함. 봇에 fear_greed_index 테이블 활용 가능.
        self._current_fg: float = 50.0

    def info(self) -> StrategyInfo:
        return StrategyInfo(
            name="long_term_swing",
            display_name="장기 스윙",
            description="공포탐욕지수+50일MA+RSI 결합. 저점 매수 고점 매도 진득한 포지션 트레이딩.",
            market_states=["bearish", "sideways"],
            timeframe="1d",
            difficulty="medium",
        )

    def set_fear_greed(self, value: float | None) -> None:
        """외부에서 현재 F&G 지수 주입. None이면 50(중립) 유지."""
        if value is not None:
            self._current_fg = float(value)

    def _ma(self, df: pd.DataFrame, period: int) -> float | None:
        if len(df) < period:
            return None
        return float(df["close"].iloc[-period:].mean())

    def check_buy(self, df: pd.DataFrame, current_price: float) -> Signal:
        """진입: F&G ≤ 30 AND 가격 < MA50 AND RSI ≤ 45."""
        if len(df) < self._ma_long + 1:
            return Signal("hold", 0.0, f"데이터 부족 ({len(df)}/{self._ma_long}일)")

        ma50 = self._ma(df, self._ma_long)
        if ma50 is None or ma50 <= 0:
            return Signal("hold", 0.0, "MA50 계산 불가")

        rsi = calculate_rsi(df["close"], self._rsi_period)
        if rsi is None:
            return Signal("hold", 0.0, "RSI 계산 불가")

        # 3중 필터
        fg_ok = self._current_fg <= self._fear_threshold
        ma_ok = current_price < ma50
        rsi_ok = rsi <= self._rsi_entry_max

        if fg_ok and ma_ok and rsi_ok:
            # confidence: F&G가 낮을수록 + RSI가 낮을수록 + MA로부터 멀수록 강한 신호
            fg_score = 1 - self._current_fg / self._fear_threshold  # 0~1
            rsi_score = 1 - rsi / self._rsi_entry_max  # 0~1
            ma_dist_score = min((ma50 - current_price) / ma50, 0.2) / 0.2  # MA 아래 20%까지
            confidence = round(min((fg_score + rsi_score + ma_dist_score) / 3 + 0.3, 0.95), 3)
            return Signal(
                "buy",
                confidence,
                f"진입(F&G={self._current_fg:.0f}, RSI={rsi:.0f}, MA50 -{(1-current_price/ma50)*100:.1f}%)",
                trigger_value=round(ma50, 2),
                stop_loss=round(current_price * (1 + self.params.stop_loss_pct / 100), 2),
            )

        miss = []
        if not fg_ok: miss.append(f"F&G {self._current_fg:.0f}>{self._fear_threshold:.0f}")
        if not ma_ok: miss.append(f"가격>MA50({ma50:.0f})")
        if not rsi_ok: miss.append(f"RSI {rsi:.0f}>{self._rsi_entry_max:.0f}")
        return Signal("hold", 0.0, "조건 미충족: " + ", ".join(miss))

    def check_sell(self, df: pd.DataFrame, current_price: float, buy_price: float) -> Signal:
        """청산: F&G ≥ 70 OR ROI ≥ 20% OR 14일 + MA20 이탈. stop_loss는 BaseStrategy."""
        # 공통 손절/트레일링/ROI 체크 (RSI 전달, 과매도 시 ROI 매도 보류)
        rsi = calculate_rsi(df["close"], self._rsi_period)
        common = self.check_trailing_stop(current_price, buy_price, current_rsi=rsi)
        if common:
            return common

        pnl_pct = (current_price - buy_price) / buy_price * 100
        net_pnl = self._net_pnl_pct(pnl_pct)

        # 1. Greed 청산 (시장 과열)
        if self._current_fg >= self._greed_threshold and net_pnl > 0:
            return Signal(
                "sell",
                0.85,
                f"Greed 청산 (F&G={self._current_fg:.0f}, 실질 +{net_pnl:.1f}%)",
                trigger_value=round(self._current_fg, 1),
                is_profit_taking=True,
            )

        # 2. ROI 목표 도달
        if net_pnl >= self._take_profit_pct:
            return Signal(
                "sell",
                0.9,
                f"목표 ROI 도달 (실질 +{net_pnl:.1f}% ≥ {self._take_profit_pct}%)",
                trigger_value=round(net_pnl, 2),
                is_profit_taking=True,
            )

        # 3. 보유 N일 이상 + MA20 이탈 (추세 이탈)
        if self._hold_minutes / (60 * 24) >= self._min_hold_days * 2:  # 충분히 보유
            ma20 = self._ma(df, self._ma_short)
            if ma20 and current_price < ma20 and net_pnl > 0:
                return Signal(
                    "sell",
                    0.7,
                    f"MA{self._ma_short} 이탈 (실질 +{net_pnl:.1f}%)",
                    trigger_value=round(ma20, 2),
                    is_profit_taking=True,
                )

        return Signal("hold", 0.0, f"보유 유지 (F&G={self._current_fg:.0f}, 실질 {net_pnl:+.1f}%)")
