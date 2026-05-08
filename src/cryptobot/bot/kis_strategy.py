"""KIS 봇 보수적 전략 (#279).

주식은 거래세 0.18%(한국) / 환전 스프레드(미국) + 수수료가 코인보다 비싸므로
**매수 신중 + 큰 이익폭 + 추세 살아있으면 보유**가 핵심.

매수 조건 (모두 충족 AND):
  1. RSI(14) ≤ rsi_oversold (기본 35) — 약한 과매도
  2. 가격 < MA20 (저평가 영역)
  3. 가격 > MA60 의 0.92 (장기 추세 깨짐 시 진입 X — 잘못된 저점)
  4. 최근 거래량 평균보다 큼 (저거래 잡종목 회피)

매도 조건 (하나라도 충족):
  - 손절: -3% (즉시)
  - 트레일링 스탑: 고점 대비 -2% (수익 중일 때만)
  - 강한 익절 + 과열: ROI ≥ take_profit AND RSI ≥ 70 (확실히 빠짐)
  - 미온 익절 + 추세 깨짐: ROI ≥ take_profit AND 가격 < MA20 (탈출)
  - 추세 살아있음: ROI ≥ take_profit AND RSI 50~70 → **즉시 매도 X, 트레일링만**

종목당 매수 한도:
  - 시드 × max_position_per_symbol_pct (기본 30%)
  - → 3종목 분산. 한 종목 망해도 시드의 30% 한도

매도 ↔ 매수 충돌 방지:
  - 봇은 종목별로 보유 여부에 따라 분기 (보유 중→매도판단 / 미보유→매수판단)
  - 즉 같은 틱 안에서 매도 직후 매수가 일어나지 않음 (구조적으로 보장)
  - 다음 틱 (60초 후) 부터는 정상 평가 — 별도 쿨다운 없음

단타모드 (#285 day_trading_mode):
  - 미국 정규장(NY 09:30~16:00) 끝나기 전 모든 보유 종목 청산
  - 마감 N분 전부터 신규 매수 금지 (남은 시간으로 익절·손절 발동 어려움)
  - 마감 N분 전 강제 시장가 매도 (다음날 갭 위험 회피)
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class KISBuySignal:
    """매수 신호."""
    should_buy: bool
    reason: str
    confidence: float = 0.0  # 0~1
    # #305 Zarattini ORB 모드: 매수 시 OR_low를 손절 가격으로 전달
    stop_loss_price: float | None = None


@dataclass
class KISSellSignal:
    """매도 신호."""
    should_sell: bool
    reason: str
    is_profit_taking: bool = False


@dataclass
class KISStrategyParams:
    """KIS 보수적 전략 파라미터.

    한국/미국 시장별로 약간 다르게 (수수료 차이 반영).
    """
    rsi_period: int = 14
    rsi_oversold: float = 35.0          # 매수 RSI 임계 (코인은 30, 주식은 약간 완화)
    rsi_overbought: float = 70.0        # 매도 과열 RSI
    ma_short: int = 20
    ma_long: int = 60
    ma_long_threshold: float = 0.92     # 가격이 MA60 × 이 값 이상이어야 매수
    take_profit_pct: float = 4.0        # 익절 임계
    stop_loss_pct: float = -3.0         # 손절 임계
    trailing_stop_pct: float = -2.0     # 고점 대비 트레일링 (수익 중일 때만)
    max_position_per_symbol_pct: float = 30.0  # 종목당 시드 % (분산)
    # #285 단타모드 (Day Trading): 장 끝나기 전 강제 청산
    day_trading_mode: bool = False
    no_buy_window_minutes_before_close: int = 30   # 마감 N분 전부터 신규 매수 X
    force_sell_window_minutes_before_close: int = 10  # 마감 N분 전 강제 매도
    # #303 VWAP+ORB+거래량 spike 전략 파라미터
    # 학술 근거: Zarattini & Aziz (2023) QQQ 5분 ORB → 누적 +1,484%/8년. TQQQ/SOXL 등 3X ETF 권장.
    # 표준: RVOL 2~3x, R:R 1:2~1:3, 시간대 10:00~12:00 EST 가장 강함.
    orb_minutes: int = 30                # ORB 형성 시간 (학술: 5~30분, 30분=노이즈 균형)
    volume_spike_multiplier: float = 2.0 # 평균 거래량 × N 이상이면 spike (학술 권고 2~3x)
    vwap_proximity_pct: float = 1.0      # 가격이 VWAP 위 N% 이내면 풀백 진입 가능


def calc_position_size(
    available_budget: float,
    current_price: float,
    fractional: bool,
    params: KISStrategyParams = KISStrategyParams(),
) -> tuple[float, str]:
    """매수 수량 계산. 통화 무관 — budget/price만 같은 통화로 들어오면 OK.

    Args:
        available_budget: 시장 가용 예산 (KR=KRW, US=USD)
        current_price: 현재가 (KR=KRW, US=USD)
        fractional: True=소수점 매수 가능 (미국주식), False=1주 단위 (한국주식)

    Returns:
        (qty, reason). qty=0 이면 매수 불가, reason은 사유.

    설계:
    - 종목당 한도: 가용예산 × max_position_per_symbol_pct / 100
    - 한국주식(1주 단위): qty = floor(한도 / 가격). 0이면 skip
    - 미국주식(소수점): qty = 한도 / 가격 (소수점 4자리)
    """
    if available_budget <= 0:
        return 0.0, "가용 예산 없음"
    if current_price <= 0:
        return 0.0, "가격 정보 없음"

    target = available_budget * (params.max_position_per_symbol_pct / 100.0)

    if not fractional:
        qty = int(target // current_price)
        if qty < 1:
            return 0.0, (
                f"예산 부족 (한도 {target:,.0f} < 1주 가격 {current_price:,.0f})"
            )
        return float(qty), f"{qty}주 (한도 {target:,.0f})"

    qty = round(target / current_price, 4)
    if qty < 0.001:
        return 0.0, "수량 < 0.001 (소수점 한도)"
    return qty, f"{qty:.4f}주 (한도 {target:,.2f})"


def _calc_rsi(prices: pd.Series, period: int = 14) -> float | None:
    if len(prices) < period + 1:
        return None
    delta = prices.diff().dropna()
    gain = delta.where(delta > 0, 0).rolling(period).mean().iloc[-1]
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean().iloc[-1]
    if loss == 0:
        return 100.0
    rs = gain / loss
    return round(100 - (100 / (1 + rs)), 2)


def _calc_ma(prices: pd.Series, period: int) -> float | None:
    if len(prices) < period:
        return None
    return float(prices.iloc[-period:].mean())


def calc_vwap(df: pd.DataFrame) -> float | None:
    """오늘 분봉 데이터로 VWAP (거래량 가중 평균가) 계산.

    VWAP = Σ(Typical_Price × Volume) / Σ(Volume)
    Typical_Price = (High + Low + Close) / 3

    df는 오늘 봉만 들어와야 함 (호출자가 필터링).
    """
    if df is None or len(df) == 0:
        return None
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"]
    total_vol = vol.sum()
    if total_vol <= 0:
        return None
    return float((typical * vol).sum() / total_vol)


def calc_orb(df: pd.DataFrame, orb_minutes: int = 30, bar_minutes: int = 5) -> tuple[float, float] | None:
    """ORB (Opening Range Breakout) 형성: 장 시작 후 N분의 고저점.

    Args:
        df: 오늘 분봉 데이터 (호출자가 NY 09:30 이후로 필터링)
        orb_minutes: ORB 형성 시간 (기본 30분)
        bar_minutes: 봉 단위 (기본 5분)

    Returns:
        (or_high, or_low) 또는 None (데이터 부족)
    """
    bars_needed = max(1, orb_minutes // bar_minutes)
    if df is None or len(df) < bars_needed:
        return None
    or_window = df.iloc[:bars_needed]
    return float(or_window["high"].max()), float(or_window["low"].min())


def evaluate_buy_breakout(
    df_today: pd.DataFrame,
    current_price: float,
    bar_minutes: int = 5,
    params: KISStrategyParams = KISStrategyParams(),
) -> KISBuySignal:
    """#303 VWAP + ORB + 거래량 spike 단타 매수 평가.

    매수 조건 (모두 충족):
      1. ORB 돌파: 가격 > ORB 고점 (장 시작 후 N분 고점)
      2. VWAP 강세: 가격 > VWAP (당일 거래량 가중 평균)
      3. 거래량 spike: 직전 봉 거래량 > 평균 × multiplier
      4. ORB 형성 완료 (장 시작 후 N분 경과)

    Args:
        df_today: 오늘 분봉 데이터 (NY 09:30 이후만)
        current_price: 현재가
        bar_minutes: 봉 단위 분 (5분봉 디폴트)
        params: 전략 파라미터

    Returns:
        KISBuySignal
    """
    bars_needed = max(1, params.orb_minutes // bar_minutes)
    if df_today is None or len(df_today) < bars_needed + 1:
        return KISBuySignal(False, f"ORB 형성 중 (현재 {len(df_today) if df_today is not None else 0}/{bars_needed + 1}봉)")

    orb = calc_orb(df_today, params.orb_minutes, bar_minutes)
    if orb is None:
        return KISBuySignal(False, "ORB 계산 불가")
    or_high, or_low = orb

    vwap = calc_vwap(df_today)
    if vwap is None:
        return KISBuySignal(False, "VWAP 계산 불가")

    # 거래량 spike — 직전 봉 vs 평균
    last_vol = float(df_today["volume"].iloc[-1])
    avg_vol = float(df_today["volume"].mean())
    spike_ratio = last_vol / avg_vol if avg_vol > 0 else 0.0

    cond_orb = current_price > or_high
    cond_vwap = current_price > vwap
    cond_volume = spike_ratio >= params.volume_spike_multiplier

    if not (cond_orb and cond_vwap and cond_volume):
        miss = []
        if not cond_orb:
            miss.append(f"ORB 미돌파 (현재 ${current_price:.2f} ≤ OR고점 ${or_high:.2f})")
        if not cond_vwap:
            miss.append(f"VWAP 아래 (가격 ${current_price:.2f} < VWAP ${vwap:.2f})")
        if not cond_volume:
            miss.append(f"거래량 spike 없음 ({spike_ratio:.2f}x < {params.volume_spike_multiplier}x)")
        return KISBuySignal(False, "조건 미충족: " + ", ".join(miss))

    # 신뢰도: ORB 돌파폭 + VWAP 거리 + 거래량 spike
    breakout_strength = (current_price - or_high) / or_high  # ORB 위 얼마나
    vwap_strength = (current_price - vwap) / vwap            # VWAP 위 얼마나
    conf = round(min(0.95,
                     min(breakout_strength * 50, 0.4) +    # ORB 돌파 폭 0~0.4
                     min(vwap_strength * 30, 0.3) +         # VWAP 위 0~0.3
                     min((spike_ratio - 1) * 0.15, 0.25)),  # 거래량 spike 0~0.25
                 2)

    return KISBuySignal(
        True,
        f"ORB↑ ${or_high:.2f}→${current_price:.2f} (+{breakout_strength*100:.2f}%) | "
        f"VWAP ${vwap:.2f} (+{vwap_strength*100:.2f}%) | 거래량 {spike_ratio:.1f}x | "
        f"손절 OR_low ${or_low:.2f}",
        confidence=max(conf, 0.3),
        stop_loss_price=or_low,  # #305 Zarattini 논문: OR 반대편이 손절선
    )


def evaluate_buy(
    df: pd.DataFrame,
    current_price: float,
    params: KISStrategyParams = KISStrategyParams(),
) -> KISBuySignal:
    """OHLCV 일봉 기준 매수 판단."""
    if df is None or len(df) < params.ma_long + 1:
        return KISBuySignal(False, f"데이터 부족 ({len(df) if df is not None else 0}/{params.ma_long}일)")

    closes = df["close"]
    rsi = _calc_rsi(closes, params.rsi_period)
    ma_short = _calc_ma(closes, params.ma_short)
    ma_long = _calc_ma(closes, params.ma_long)

    if rsi is None or ma_short is None or ma_long is None:
        return KISBuySignal(False, "지표 계산 불가")

    # 거래량 체크 (있을 때만)
    volume_ok = True
    if "volume" in df.columns and len(df["volume"]) >= 20:
        recent_vol = df["volume"].iloc[-1]
        avg_vol = df["volume"].iloc[-20:-1].mean()
        if avg_vol > 0 and recent_vol < avg_vol * 0.5:
            volume_ok = False  # 거래량 절반 이하면 잡종목 의심

    rsi_ok = rsi <= params.rsi_oversold
    ma_short_ok = current_price < ma_short
    # 장기 추세 깨지면 잘못된 저점 — 진입 X (예: 회사 큰 사고)
    ma_long_ok = current_price > ma_long * params.ma_long_threshold

    if not (rsi_ok and ma_short_ok and ma_long_ok and volume_ok):
        miss = []
        if not rsi_ok: miss.append(f"RSI {rsi:.1f}>{params.rsi_oversold:.0f}")
        if not ma_short_ok: miss.append(f"가격>MA{params.ma_short}({ma_short:.0f})")
        if not ma_long_ok: miss.append(f"장기추세 깨짐 (가격<MA{params.ma_long}×{params.ma_long_threshold})")
        if not volume_ok: miss.append("거래량 부족")
        return KISBuySignal(False, "조건 미충족: " + ", ".join(miss))

    # confidence: RSI 낮을수록 + MA 아래로 멀수록 강함
    conf = round(min(0.95, (1 - rsi / params.rsi_oversold) * 0.5 +
                      min((ma_short - current_price) / ma_short, 0.05) / 0.05 * 0.5), 2)
    return KISBuySignal(
        True,
        f"RSI={rsi:.0f}, 가격<MA{params.ma_short}({ma_short:.0f})",
        confidence=max(conf, 0.3),
    )


def evaluate_sell(
    df: pd.DataFrame,
    current_price: float,
    buy_price: float,
    highest_since_buy: float | None,
    params: KISStrategyParams = KISStrategyParams(),
    stop_loss_price: float | None = None,
) -> KISSellSignal:
    """매도 판단. highest_since_buy는 외부에서 추적 (트레일링용).

    Args:
        stop_loss_price: #305 Zarattini ORB 모드 — 절대 가격 손절선 (OR_low).
            None이면 stop_loss_pct(%) 사용. 값 있으면 우선.
    """
    if buy_price <= 0:
        return KISSellSignal(False, "매수가 미상")

    pnl_pct = (current_price - buy_price) / buy_price * 100

    # 1-A. ORB 손절 (Zarattini 논문 모드) — 절대 가격
    if stop_loss_price is not None and stop_loss_price > 0 and current_price <= stop_loss_price:
        return KISSellSignal(
            True,
            f"손절 OR_low ${stop_loss_price:.2f} 도달 (현재 ${current_price:.2f}, {pnl_pct:.2f}%)",
            is_profit_taking=False,
        )

    # 1-B. 절대% 손절 (기존)
    if pnl_pct <= params.stop_loss_pct:
        return KISSellSignal(True, f"손절 {pnl_pct:.2f}%", is_profit_taking=False)

    # 2. 트레일링 스탑 (수익 중일 때만)
    if highest_since_buy and highest_since_buy > buy_price:
        drop_from_high = (current_price - highest_since_buy) / highest_since_buy * 100
        if drop_from_high <= params.trailing_stop_pct and pnl_pct > 0:
            return KISSellSignal(
                True,
                f"트레일링 스탑 (고점 {highest_since_buy:.0f} → 현재 {current_price:.0f}, {drop_from_high:.2f}%)",
                is_profit_taking=True,
            )

    # 3. 익절 임계 도달 — 추세에 따라 차등
    if pnl_pct >= params.take_profit_pct:
        if df is not None and len(df) >= params.ma_short + 1:
            closes = df["close"]
            rsi = _calc_rsi(closes, params.rsi_period)
            ma_short = _calc_ma(closes, params.ma_short)

            # 과열 — 즉시 익절
            if rsi is not None and rsi >= params.rsi_overbought:
                return KISSellSignal(
                    True, f"과열 익절 (RSI {rsi:.0f}≥{params.rsi_overbought:.0f}, +{pnl_pct:.1f}%)",
                    is_profit_taking=True,
                )

            # 추세 깨짐 (가격이 MA20 아래) — 탈출
            if ma_short and current_price < ma_short:
                return KISSellSignal(
                    True, f"추세 이탈 (가격<MA{params.ma_short}, +{pnl_pct:.1f}%)",
                    is_profit_taking=True,
                )

            # 추세 살아있음 — 트레일링만 적용 (즉시 매도 X)
            rsi_str = f"{rsi:.0f}" if rsi is not None else "?"
            return KISSellSignal(
                False,
                f"보유 유지 (+{pnl_pct:.1f}% 익절선 도달이나 RSI={rsi_str} 추세 살아있음)",
            )

        # df 없으면 단순 익절
        return KISSellSignal(True, f"익절 +{pnl_pct:.2f}%", is_profit_taking=True)

    return KISSellSignal(False, f"보유 ({pnl_pct:+.2f}%)")
