"""VWAP + ORB + Volume Spike 단타 전략 (Zarattini 2023 코인 적용, #321/#360).

학술 근거: Zarattini & Aziz (2023) "Can Day Trading Really Be Profitable?"
- QQQ 5분 ORB → 8년 누적 +1,484%, 연환산 알파 33%
- TQQQ/SOXL 등 3X 레버리지 ETF 권고 → 변동성 큰 코인 적용 가설

Option 1 (#360, 2026-05-09 채택):
- ORB 형성: KST 22:00~23:00 (US 개장 글로벌 변동성 피크 활용)
- 진입 윈도우: 23:00~04:00 (5h)
- EOD 청산: 다음날 KST 11:00
- 봉 단위: 15분봉 (5분은 코인 노이즈 큼)
- 거래량 spike: 1.5x (코인은 변동성 커서 살짝 완화)
- 손절: OR_low (가변)
- 매도: 손절 + 트레일링 -3% + EOD

매수 조건 (모두 충족):
1. 진입 윈도우 안 (23:00~04:00 KST)
2. ORB 돌파 (가격 > OR_high)
3. VWAP 강세 (가격 > 24시간 누적 VWAP)
4. 거래량 spike (직전 봉 ≥ 평균 × 1.5)
5. ORB 형성 완료 (22:00 + 1시간 경과)

후보 옵션 백테스트 결과 (8 화이트리스트, ~3주):
- Option 1 (현재): ORB 22 / 진입 5h / EOD 11 → 80% 승률 / 복리 +20.63%
- Option 2: ORB 0 / 진입 5h / EOD 6 → 73% / +16.29%
- Option 3: ORB 10 / 진입 4h / EOD 20 → 75% / +16.20%
- Option 4: ORB 0 / 진입 2h / EOD 11 → 100%(7건) / +16.12%
"""

from __future__ import annotations

import logging
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from cryptobot.bot.kis_strategy import calc_orb, calc_vwap
from cryptobot.strategies.base import BaseStrategy, Signal, StrategyInfo

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")

# Option 1 디폴트 (#360, 백테스트 1위 조합) — 코드에서만 관리 (env 분기 제거)
ORB_HOUR_KST = 22       # ORB 시작 KST 시
EOD_HOUR_KST = 11       # EOD 청산 KST 시
ENTRY_WINDOW_HOURS = 5  # ORB 형성(1h) 후 진입 허용 시간


class VwapOrbBreakout(BaseStrategy):
    """VWAP + ORB + 거래량 spike 단타 (코인용, KST 22:00 ORB + 11:00 EOD)."""

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
                "KST 22:00 후 1시간 ORB 형성 → 23:00~04:00 진입 윈도우 안에서 "
                "돌파 + VWAP 강세 + 거래량 spike 시 매수. KST 11:00 EOD 청산."
            ),
            market_states=["bullish", "sideways"],
            timeframe="15m",
            difficulty="medium",
        )

    def check_buy(self, df: pd.DataFrame, current_price: float) -> Signal:
        """ORB 돌파 + VWAP + 거래량 spike + 진입 윈도우 평가.

        df는 15분봉 OHLCV (현재 세션 시작 후 봉만 들어와야).
        호출자가 시점 필터링 책임.
        """
        if not is_entry_window():
            return Signal("hold", 0.0, "진입 윈도우 외 (휴식)")

        if df is None or len(df) == 0:
            return Signal("hold", 0.0, "데이터 없음")

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
            stop_loss=or_low,
        )

    def check_sell(self, df: pd.DataFrame, current_price: float, buy_price: float) -> Signal:
        """매도 룰: 손절 + 트레일링만. EOD 청산은 봇이 별도 처리.

        BaseStrategy.check_trailing_stop을 호출하지 않음 — 그 안의 roi_table
        시간 기반 익절(+0.8%/60분 등)이 ORB 단타의 큰 추세를 너무 일찍 자름.
        EOD 청산이 보유 시간 캡 역할.
        """
        if self._highest_price is None or current_price > self._highest_price:
            self._highest_price = current_price

        pnl_pct = (current_price - buy_price) / buy_price * 100
        net_pnl = self._net_pnl_pct(pnl_pct)

        if pnl_pct <= self.params.stop_loss_pct:
            return Signal(
                "sell",
                1.0,
                f"손절 {pnl_pct:.2f}%",
                trigger_value=round(pnl_pct, 2),
                is_profit_taking=False,
            )

        drop_pct = (current_price - self._highest_price) / self._highest_price * 100
        if drop_pct <= self.params.trailing_stop_pct and net_pnl > 0:
            return Signal(
                "sell",
                0.8,
                f"트레일링 (실질 {net_pnl:+.2f}%)",
                trigger_value=round(drop_pct, 2),
                is_profit_taking=True,
            )

        return Signal("hold", 0.0, f"보유 유지 (실질 {net_pnl:+.2f}%, EOD 청산 대기)")


def is_eod_window(now: datetime | None = None, window_minutes: int = 5) -> bool:
    """EOD 시점 ±window_minutes 사이면 True (강제 청산 윈도우).

    EOD 시각은 EOD_HOUR_KST (현재 KST 11:00).
    """
    if now is None:
        now = datetime.now(KST)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    eod = now.replace(hour=EOD_HOUR_KST, minute=0, second=0, microsecond=0)
    delta = abs((now - eod).total_seconds())
    return delta <= window_minutes * 60


def is_entry_window(now: datetime | None = None) -> bool:
    """현재 시각이 진입 허용 윈도우 안인지.

    진입 윈도우 = ORB 형성 후(orb_hour + 1) ~ orb_hour + 1 + entry_window_h.
    Option 1 디폴트: 23:00 ~ 04:00 (자정 wrap).
    """
    if now is None:
        now = datetime.now(KST)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=KST)

    entry_start = (ORB_HOUR_KST + 1) % 24
    entry_end = (ORB_HOUR_KST + 1 + ENTRY_WINDOW_HOURS) % 24

    h = now.hour
    if entry_start <= entry_end:
        return entry_start <= h < entry_end
    # 자정 wrap (예: 23 → 04)
    return h >= entry_start or h < entry_end


def filter_session_bars(df: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """현재 시점 기준 가장 최근 ORB 시작점 이후 봉만 필터.

    ORB hour가 22면 22:00~다음날 11:00이 한 세션. 호출 시점에 따라:
    - now가 22:00 이상 (당일 22~23:59): 오늘 22:00부터
    - now가 22:00 미만 (다음날 00:00~21:59): 어제 22:00부터

    df의 index가 datetime이어야. (Upbit pyupbit는 KST datetime index 반환)
    """
    if df is None or len(df) == 0:
        return df
    if now is None:
        now = datetime.now(KST)

    if now.hour >= ORB_HOUR_KST:
        session_start = now.replace(hour=ORB_HOUR_KST, minute=0, second=0, microsecond=0)
    else:
        session_start = (now - timedelta(days=1)).replace(
            hour=ORB_HOUR_KST, minute=0, second=0, microsecond=0
        )

    if df.index.tz is None:
        session_start_naive = session_start.replace(tzinfo=None)
        return df[df.index >= session_start_naive]
    return df[df.index >= session_start]


# 하위 호환: 기존 import 유지 (별칭)
filter_today_bars = filter_session_bars
