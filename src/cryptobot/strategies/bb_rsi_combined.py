"""볼린저밴드 + RSI 복합 전략 (Swing 모드, #376).

단일 지표보다 거짓 신호를 줄여 승률을 높이는 전략.
매수: RSI 과매도 + 볼린저 하단 이탈 (두 조건 동시 충족)
매도 우선순위 (roi_table 우회, swing 패턴):
1. 손절 -5% — 무조건
2. 트레일링 (피크 -trailing_stop_pct) — net_pnl >= min_profit_for_trailing 통과 시만
3. RSI 정상 복귀 — net_pnl >= min_profit_for_trailing 통과 시만
4. BB 중간선 도달 — net_pnl >= min_profit_for_trailing 통과 시만

학계 근거: BB+RSI 결합 정확도 87.5% (ResearchGate 2024), Liu/Tsyvinski 2022.
벤치마크: 60%+ 승률, 시장의 ~34% 시간만 포지션 보유.
"""

import pandas as pd

from cryptobot.strategies.base import BaseStrategy, Signal, StrategyInfo, StrategyParams

# #376: swing 익절 가드 — 디폴트 +5% 미만 수익권에선 익절성 매도 안 함.
# user 멘탈 모델 "저점 진입 → +5% 후 추세 보고 매도" 구현.
MIN_PROFIT_FOR_TRAILING = 5.0


class BBRSICombined(BaseStrategy):
    """볼린저밴드 + RSI 복합 전략 (Swing 모드)."""

    def __init__(self, params: StrategyParams | None = None) -> None:
        super().__init__(params)
        self._bb_period = int(self.params.extra.get("bb_period", 20))
        self._bb_std = self.params.extra.get("bb_std", 2.0)
        self._rsi_period = int(self.params.extra.get("rsi_period", 14))
        self._rsi_oversold = self.params.extra.get("rsi_oversold", 30)
        self._rsi_overbought = self.params.extra.get("rsi_overbought", 50)
        # 부분 점수 허용 (#167): 한쪽 조건만 충족 시 낮은 confidence로 매수 신호.
        # 기본 False (기존 엄격한 AND 동작 유지). LLM이 매매 0건 지속 시 True로 전환.
        self._allow_partial_signal = bool(self.params.extra.get("allow_partial_signal", False))
        self._partial_confidence = float(self.params.extra.get("partial_confidence", 0.4))
        # #376: swing 익절 가드
        self._min_profit_for_trailing = float(
            self.params.extra.get("min_profit_for_trailing", MIN_PROFIT_FOR_TRAILING)
        )

    def info(self) -> StrategyInfo:
        return StrategyInfo(
            name="bb_rsi_combined",
            display_name="볼린저+RSI 복합",
            description="RSI 과매도 + 볼린저 하단 이탈 동시 충족 시 매수. 거짓 신호 감소로 높은 승률.",
            market_states=["sideways", "bearish"],
            timeframe="1d",
            difficulty="medium",
        )

    def _calc_rsi(self, df: pd.DataFrame) -> float | None:
        """RSI 계산."""
        if len(df) < self._rsi_period + 1:
            return None
        deltas = df["close"].diff().dropna()
        gains = deltas.where(deltas > 0, 0)
        losses = -deltas.where(deltas < 0, 0)
        avg_gain = gains.rolling(self._rsi_period).mean().iloc[-1]
        avg_loss = losses.rolling(self._rsi_period).mean().iloc[-1]
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 2)

    def _calc_bb(self, df: pd.DataFrame) -> tuple[float, float, float] | None:
        """볼린저밴드 계산. (중간, 상단, 하단)"""
        if len(df) < self._bb_period:
            return None
        ma = df["close"].rolling(self._bb_period).mean().iloc[-1]
        std = df["close"].rolling(self._bb_period).std().iloc[-1]
        upper = ma + std * self._bb_std
        lower = ma - std * self._bb_std
        return (ma, upper, lower)

    def check_buy(self, df: pd.DataFrame, current_price: float) -> Signal:
        """매수: RSI < oversold AND 가격 < 볼린저 하단."""
        rsi = self._calc_rsi(df)
        bb = self._calc_bb(df)

        if rsi is None or bb is None:
            return Signal("hold", 0.0, "데이터 부족")

        ma, upper, lower = bb

        rsi_oversold = current_price > 0 and rsi <= self._rsi_oversold
        below_lower = current_price < lower

        if rsi_oversold and below_lower:
            # 두 조건 모두 충족 → 강한 매수 신호
            # confidence: RSI가 낮을수록 + 밴드 이탈이 클수록 높음
            rsi_strength = max(0, (self._rsi_oversold - rsi) / self._rsi_oversold)
            band_depth = min((lower - current_price) / (upper - lower), 1.0) if upper != lower else 0
            confidence = min(0.5 + rsi_strength * 0.3 + band_depth * 0.2, 1.0)

            return Signal(
                "buy",
                round(confidence, 3),
                f"RSI({rsi:.0f}) 과매도 + 볼린저 하단 이탈",
                trigger_value=round(lower, 2),
                stop_loss=round(current_price * (1 + self.params.stop_loss_pct / 100), 2),
            )

        # 부분 충족 — allow_partial_signal=True면 낮은 confidence로 매수 신호 (#167)
        if rsi_oversold and not below_lower:
            if self._allow_partial_signal:
                return Signal(
                    "buy",
                    self._partial_confidence,
                    f"[부분] RSI({rsi:.0f}) 과매도 (하단 미이탈이지만 약한 매수)",
                    trigger_value=round(lower, 2),
                    stop_loss=round(current_price * (1 + self.params.stop_loss_pct / 100), 2),
                )
            return Signal("hold", 0.0, f"RSI({rsi:.0f}) 과매도이나 볼린저 하단 미이탈")

        if below_lower and not rsi_oversold:
            if self._allow_partial_signal:
                return Signal(
                    "buy",
                    self._partial_confidence,
                    f"[부분] 볼린저 하단 이탈 (RSI={rsi:.0f} 정상이지만 약한 매수)",
                    trigger_value=round(lower, 2),
                    stop_loss=round(current_price * (1 + self.params.stop_loss_pct / 100), 2),
                )
            return Signal("hold", 0.0, f"볼린저 하단 이탈이나 RSI({rsi:.0f}) 정상")

        return Signal("hold", 0.0, f"조건 미충족 (RSI={rsi:.0f})")

    def check_sell(self, df: pd.DataFrame, current_price: float, buy_price: float) -> Signal:
        """매도 (Swing 모드, #376). roi_table 우회.

        우선순위:
        1. 손절 (params.stop_loss_pct) — 무조건
        2. 트레일링 (params.trailing_stop_pct) — net_pnl >= min_profit_for_trailing 가드 통과 시만
        3. RSI 정상 복귀 — 가드 통과 시만
        4. BB 중간선 도달 — 가드 통과 시만

        가드 미달 익절성 매도는 hold로 → 큰 추세 끝까지 보유 (user 멘탈 모델).
        """
        # 피크 추적
        if self._highest_price is None or current_price > self._highest_price:
            self._highest_price = current_price

        pnl_pct = (current_price - buy_price) / buy_price * 100
        net_pnl = self._net_pnl_pct(pnl_pct)

        # 1. 손절 (무조건, 가드 무시)
        if pnl_pct <= self.params.stop_loss_pct:
            return Signal(
                "sell",
                1.0,
                f"손절 {pnl_pct:.2f}%",
                trigger_value=round(pnl_pct, 2),
                is_profit_taking=False,
            )

        # 2. 트레일링 (피크 갱신 후 -trailing_stop_pct 빠질 때) — +5% 가드 통과 시만
        drop_pct = (current_price - self._highest_price) / self._highest_price * 100
        if (
            drop_pct <= self.params.trailing_stop_pct
            and net_pnl >= self._min_profit_for_trailing
        ):
            return Signal(
                "sell",
                0.8,
                f"트레일링 (실질 {net_pnl:+.2f}%, ≥{self._min_profit_for_trailing}% 가드)",
                trigger_value=round(drop_pct, 2),
                is_profit_taking=True,
            )

        # 3. RSI 정상 복귀 (mean reversion 완료) — +5% 가드 통과 시만
        rsi = self._calc_rsi(df) if df is not None else None
        if (
            rsi is not None
            and rsi >= self._rsi_overbought
            and net_pnl >= self._min_profit_for_trailing
        ):
            return Signal(
                "sell",
                0.7,
                f"RSI({rsi:.0f}) 정상 복귀 (실질 {net_pnl:+.2f}%)",
                trigger_value=round(rsi, 1),
                is_profit_taking=True,
            )

        # 4. BB 중간선 도달 — +5% 가드 통과 시만
        bb = self._calc_bb(df) if df is not None else None
        if bb is not None:
            ma, _upper, _lower = bb
            if current_price >= ma and net_pnl >= self._min_profit_for_trailing:
                return Signal(
                    "sell",
                    0.6,
                    f"BB 중간선 익절 (실질 +{net_pnl:.2f}%)",
                    trigger_value=round(ma, 2),
                    is_profit_taking=True,
                )

        rsi_str = f"RSI={rsi:.0f}, " if rsi is not None else ""
        return Signal(
            "hold",
            0.0,
            f"보유 유지 ({rsi_str}실질 {net_pnl:+.2f}%, "
            f"트레일링 가드 ≥{self._min_profit_for_trailing}%)",
        )
