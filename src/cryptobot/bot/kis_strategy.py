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

매수 cooldown:
  - 같은 종목 24h 내 재매수 X (체결 직후 변동 노이즈 회피)
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
    rebuy_cooldown_hours: int = 24       # 같은 종목 재매수 금지 시간


def calc_position_size(
    available_budget_krw: float,
    current_price_krw: float,
    fractional: bool,
    params: KISStrategyParams = KISStrategyParams(),
) -> tuple[float, str]:
    """매수 수량 계산.

    Args:
        available_budget_krw: 시장 가용 예산 (KRW)
        current_price_krw: 현재가 (KRW 환산. US는 USD×환율)
        fractional: True=소수점 매수 가능 (미국주식), False=1주 단위 (한국주식)

    Returns:
        (qty, reason). qty=0 이면 매수 불가, reason은 사유.

    설계:
    - 종목당 한도: 시드 × max_position_per_symbol_pct / 100
    - 한국주식(1주 단위): qty = floor(한도 / 가격). 0이면 skip
    - 미국주식(소수점): qty = 한도 / 가격 (소수점 4자리)
    """
    if available_budget_krw <= 0:
        return 0.0, "가용 예산 없음"
    if current_price_krw <= 0:
        return 0.0, "가격 정보 없음"

    target = available_budget_krw * (params.max_position_per_symbol_pct / 100.0)

    if not fractional:
        qty = int(target // current_price_krw)
        if qty < 1:
            return 0.0, (
                f"예산 부족 (한도 {target:,.0f} < 1주 가격 {current_price_krw:,.0f})"
            )
        return float(qty), f"{qty}주 (한도 {target:,.0f}원)"

    qty = round(target / current_price_krw, 4)
    if qty < 0.001:
        return 0.0, "수량 < 0.001 (소수점 한도)"
    return qty, f"{qty:.4f}주 (한도 {target:,.0f}원)"


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
) -> KISSellSignal:
    """매도 판단. highest_since_buy 는 외부에서 추적 (트레일링용)."""
    if buy_price <= 0:
        return KISSellSignal(False, "매수가 미상")

    pnl_pct = (current_price - buy_price) / buy_price * 100

    # 1. 손절 — 무조건
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
