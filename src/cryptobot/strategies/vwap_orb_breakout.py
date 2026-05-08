"""VWAP + ORB + Volume Spike 단타 전략 (Zarattini 2023 코인 적용, #321).

학술 근거: Zarattini & Aziz (2023) "Can Day Trading Really Be Profitable?"
- QQQ 5분 ORB → 8년 누적 +1,484%, 연환산 알파 33%
- TQQQ/SOXL 등 3X 레버리지 ETF 권고 → 변동성 큰 코인 적용 가설

코인 적용 시 차이 (vs 미국주식):
- 24/7 시장 → "EOD" 개념 = KST 09:00 (매일 아침 청산)
- ORB 형성: KST 00:00~01:00 (자정 후 1시간) — 5분봉 12개
- 봉 단위: 15분봉 (5분은 코인 노이즈 큼)
- 거래량 spike: 1.5x (코인은 변동성 커서 살짝 완화)
- 손절: OR_low (가변)
- 익절: 트레일링 -2% (EOD 청산 보조)

매수 조건 (모두 충족):
1. ORB 돌파 (가격 > OR_high)
2. VWAP 강세 (가격 > 24시간 누적 VWAP)
3. 거래량 spike (직전 봉 ≥ 평균 × 1.5)
4. ORB 형성 완료 (자정 + 1시간 경과)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from cryptobot.bot.kis_strategy import calc_orb, calc_vwap
from cryptobot.strategies.base import BaseStrategy, Signal, StrategyInfo

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")

# EOD = 매일 KST 09시 (사용자 정의 가능, env COIN_EOD_HOUR_KST)
# 추천: 자정 ORB와 가까운 23시 (사이클 23시간 활용) 또는 09시 (사용자 일어남 직후 결과 확인)
EOD_HOUR_KST = int(os.getenv("COIN_EOD_HOUR_KST", "9"))


class VwapOrbBreakout(BaseStrategy):
    """VWAP + ORB + 거래량 spike 단타 (코인용, KST 자정 ORB + 09:00 EOD)."""

    def __init__(self, params=None) -> None:
        super().__init__(params)
        self._orb_minutes = int(self.params.extra.get("orb_minutes", 60))
        self._volume_spike = float(self.params.extra.get("volume_spike_multiplier", 1.5))
        self._bar_minutes = int(self.params.extra.get("bar_minutes", 15))

    def info(self) -> StrategyInfo:
        return StrategyInfo(
            name="vwap_orb_breakout",
            display_name="VWAP+ORB 단타 (Zarattini)",
            description=(
                "Zarattini 2023 논문 기반 단타 전략. "
                "KST 자정 후 1시간 ORB 형성 → 돌파 + VWAP 강세 + 거래량 spike 시 매수. "
                "KST 09:00 EOD 청산. 변동성 큰 종목에 효과적."
            ),
            market_states=["bullish", "sideways"],
            timeframe="15m",
            difficulty="medium",
        )

    def check_buy(self, df: pd.DataFrame, current_price: float) -> Signal:
        """ORB 돌파 + VWAP + 거래량 spike 평가.

        df는 15분봉 OHLCV (오늘 KST 자정 이후 봉만 들어와야).
        호출자가 시점 필터링 책임.
        """
        if df is None or len(df) == 0:
            return Signal("hold", 0.0, "데이터 없음")

        # ORB 형성 봉 수 (자정 + 1시간 = 15분봉 4개)
        bars_needed = max(1, self._orb_minutes // self._bar_minutes)
        if len(df) < bars_needed + 1:
            return Signal("hold", 0.0, f"ORB 형성 중 ({len(df)}/{bars_needed + 1}봉)")

        orb = calc_orb(df, orb_minutes=self._orb_minutes, bar_minutes=self._bar_minutes)
        if orb is None:
            return Signal("hold", 0.0, "ORB 계산 불가")
        or_high, or_low = orb

        vwap = calc_vwap(df)
        if vwap is None:
            return Signal("hold", 0.0, "VWAP 계산 불가")

        last_vol = float(df["volume"].iloc[-1])
        avg_vol = float(df["volume"].mean())
        spike_ratio = last_vol / avg_vol if avg_vol > 0 else 0.0

        cond_orb = current_price > or_high
        cond_vwap = current_price > vwap
        cond_volume = spike_ratio >= self._volume_spike

        if not (cond_orb and cond_vwap and cond_volume):
            miss = []
            if not cond_orb:
                miss.append(f"ORB 미돌파 (현재 ≤ OR고점 {or_high:.2f})")
            if not cond_vwap:
                miss.append(f"VWAP 아래 ({current_price:.2f} < {vwap:.2f})")
            if not cond_volume:
                miss.append(f"거래량 spike 부족 ({spike_ratio:.2f}x < {self._volume_spike}x)")
            return Signal(
                "hold",
                0.0,
                "조건 미충족: " + ", ".join(miss),
                trigger_value=or_high,
            )

        breakout_strength = (current_price - or_high) / or_high
        vwap_strength = (current_price - vwap) / vwap
        confidence = round(min(0.95,
            min(breakout_strength * 50, 0.4) +
            min(vwap_strength * 30, 0.3) +
            min((spike_ratio - 1) * 0.15, 0.25)
        ), 2)

        return Signal(
            "buy",
            max(confidence, 0.3),
            f"ORB↑ {or_high:.2f}→{current_price:.2f} (+{breakout_strength*100:.2f}%) | "
            f"VWAP {vwap:.2f} (+{vwap_strength*100:.2f}%) | 거래량 {spike_ratio:.1f}x | "
            f"손절 OR_low {or_low:.2f}",
            trigger_value=or_high,
            stop_loss=or_low,  # OR_low를 손절가로 (가변)
        )

    def check_sell(self, df: pd.DataFrame, current_price: float, buy_price: float) -> Signal:
        """매도 룰: 손절(OR_low 또는 -3%) + 트레일링 (EOD 청산은 봇이 별도 처리)."""
        # 공통 트레일링 + 손절 (BaseStrategy 헬퍼)
        stop_signal = self.check_trailing_stop(current_price, buy_price)
        if stop_signal:
            return stop_signal
        return Signal("hold", 0.0, "보유 유지 (EOD 청산 대기 또는 트레일링)")


def is_eod_window(now: datetime | None = None, window_minutes: int = 5) -> bool:
    """KST 09:00~09:05 사이면 True (EOD 청산 윈도우).

    매일 아침 9시 정각 ± 5분에 보유 코인 강제 매도.
    """
    if now is None:
        now = datetime.now(KST)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    eod = now.replace(hour=EOD_HOUR_KST, minute=0, second=0, microsecond=0)
    delta = abs((now - eod).total_seconds())
    return delta <= window_minutes * 60


def filter_today_bars(df: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """KST 자정 이후 봉만 필터 (ORB/VWAP는 당일 데이터 사용).

    df의 index가 datetime이어야. (Upbit pyupbit는 KST datetime index 반환)
    """
    if df is None or len(df) == 0:
        return df
    if now is None:
        now = datetime.now(KST)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if df.index.tz is None:
        # naive index — KST 가정
        midnight_naive = midnight.replace(tzinfo=None)
        return df[df.index >= midnight_naive]
    return df[df.index >= midnight]
