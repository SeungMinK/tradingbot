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
    # #364 Pure Zarattini Bar-1: 10R 익절 절대가
    take_profit_price: float | None = None
    # #364 Pure Zarattini Bar-1: 1% 리스크 사이징용 risk_per_share (= |entry − stop|)
    risk_per_share: float | None = None
    # #396: 시그널 발생 시점의 entry 가격 (bar1 close). 매수 직전 갭 가드 비교용.
    signal_price: float | None = None
    # #396: bar1 패턴 ("bullish" / "bearish" / "doji" / None). 일일 history 기록용.
    bar1_pattern: str | None = None
    bar1_body_pct: float | None = None


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
    orb_minutes: int = 30                # ORB 형성 시간 (학술: 5~30분, 30분=noise 균형)
    volume_spike_multiplier: float = 2.0 # 평균 거래량 × N 이상이면 spike (학술 권고 2~3x)
    vwap_proximity_pct: float = 1.0      # 가격이 VWAP 위 N% 이내면 풀백 진입 가능
    # #364 Pure Zarattini Bar-1 모드 파라미터
    doji_threshold_pct: float = 0.05     # bar1 |close-open|/open < N% 이면 도지로 판정 (skip)
    risk_pct_per_trade: float = 1.0      # 계좌 대비 % 리스크/거래 (포지션 사이징)
    r_multiple_target: float = 10.0      # baseline 모드 익절 = entry + R×(entry−stop)
    # #364 3X 최적 변형 파라미터 (논문 TQQQ 변형: +9,350% / 93% 알파)
    atr_stop_pct: float = 5.0            # stop_distance = (atr_stop_pct / 100) × ATR(period)
    atr_period: int = 14                 # ATR 계산 일수 (논문: 14일)


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

    #388: KIS US는 시장가 미지원 → 지정가 +0.5% buffer로 주문하므로
    실제 주문가는 current_price × 1.005. 거기에 슬리피지·환율 변동 흡수용
    추가 1% safety margin → 가용예산 × 0.985 기준으로 수량 산정.
    """
    if available_budget <= 0:
        return 0.0, "가용 예산 없음"
    if current_price <= 0:
        return 0.0, "가격 정보 없음"

    target = available_budget * (params.max_position_per_symbol_pct / 100.0)

    # #388: 지정가 buffer(0.5%) + 안전 마진(1%) = 1.5% 차감해서 수량 산정
    # → KIS API "주문가능금액 초과" 에러 방지
    safe_price = current_price * 1.015

    if not fractional:
        qty = int(target // safe_price)
        if qty < 1:
            return 0.0, (
                f"예산 부족 (한도 {target:,.0f} < 1주 가격 {current_price:,.0f} × 1.015 buffer)"
            )
        return float(qty), f"{qty}주 (한도 {target:,.0f}, buffer 1.5%)"

    qty = round(target / safe_price, 4)
    if qty < 0.001:
        return 0.0, "수량 < 0.001 (소수점 한도)"
    return qty, f"{qty:.4f}주 (한도 {target:,.2f}, buffer 1.5%)"


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


def calc_atr(df: pd.DataFrame, period: int = 14) -> float | None:
    """#364 14일 ATR (Average True Range) 계산.

    True Range = max(high - low, |high - prev_close|, |low - prev_close|).
    ATR = TR의 period일 단순 이동 평균.

    Args:
        df: 일봉 OHLCV (high, low, close 컬럼). period+1봉 이상 필요.
        period: 평균 일수 (논문: 14)

    Returns:
        ATR 값 ($/주). 데이터 부족 시 None.
    """
    if df is None or len(df) < period + 1:
        return None
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    prev_close = df["close"].astype(float).shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    if pd.isna(atr) or atr <= 0:
        return None
    return float(atr)


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


def evaluate_zarattini_bar1(
    df_today_5m: pd.DataFrame,
    params: KISStrategyParams = KISStrategyParams(),
) -> KISBuySignal:
    """#364 Pure Zarattini Bar-1 directional 진입 판단.

    논문(Zarattini & Aziz 2023) 정확 사양:
    - 첫 5분봉(09:30~09:35) 양봉이면 둘째 봉 시작에 LONG 진입
    - 도지(|close-open|/open < doji_threshold_pct%)면 패스
    - 한국 KIS API는 미국주식 공매도 불가 → 음봉 케이스는 *이 함수 호출 측*에서
      인버스 ETF(SOXS 등)에 대해 별도 호출하면 됨 (페어 자동 처리)

    호출자가 df_today_5m을 NY 09:35 이후 시점에 한 번 평가하면 됨.
    df는 최소 1봉(첫 5분봉) 이상 들어와야. 첫 봉이 시그널 결정.

    Returns:
        KISBuySignal:
          - should_buy=True: 양봉, stop_loss_price=bar1_low,
                             take_profit_price=entry+10R, risk_per_share 채워짐
          - should_buy=False: 도지/음봉/데이터 부족
    """
    if df_today_5m is None or len(df_today_5m) < 1:
        return KISBuySignal(False, "Bar-1 데이터 없음")

    bar1 = df_today_5m.iloc[0]
    o = float(bar1["open"])
    h = float(bar1["high"])
    l = float(bar1["low"])
    c = float(bar1["close"])

    if o <= 0:
        return KISBuySignal(False, "Bar-1 open 가격 이상")

    body_pct = abs(c - o) / o * 100  # bar1 몸통 비율 %

    # 도지 — 방향성 X, 매매 X (논문 그대로)
    if body_pct < params.doji_threshold_pct:
        return KISBuySignal(
            False,
            f"Bar-1 도지 (몸통 {body_pct:.3f}% < {params.doji_threshold_pct}% — 매매 X)",
        )

    # 음봉 — 한국 일반 계좌는 공매도 X. 인버스 ETF는 별도 ticker로 호출됨.
    if c < o:
        return KISBuySignal(
            False,
            f"Bar-1 음봉 (close ${c:.2f} < open ${o:.2f}) — 이 종목 매수 X "
            f"(인버스 ETF 별도 평가)",
        )

    # 양봉 — 둘째 봉 시작에 LONG 진입.
    # 진입가는 호출자 시점의 *현재가* 또는 *둘째 봉 open* — 호출 시점에 따라 다름.
    # 봇이 09:35 직후 평가 → 둘째 봉 open ≈ 첫 봉 close (보통 매우 가까움)
    # 정확한 매수가는 주문 체결가로 결정.
    entry = c  # 첫 봉 close ≈ 둘째 봉 open (proxy)
    stop = l   # 첫 봉 low
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return KISBuySignal(False, f"리스크 계산 불가 (bar1 close {c} ≤ low {l})")

    target = entry + params.r_multiple_target * risk_per_share

    # 신뢰도: 양봉 몸통이 클수록 ↑ (직관적: 큰 모멘텀일수록 강한 시그널)
    conf = round(min(0.95, body_pct / 2.0), 2)  # 0.5%면 0.25, 2%면 0.95

    return KISBuySignal(
        True,
        f"Bar-1 양봉 ${o:.2f}→${c:.2f} (+{body_pct:.2f}%) | "
        f"손절 ${stop:.2f} (R=${risk_per_share:.2f}) | "
        f"익절 {params.r_multiple_target:.0f}R = ${target:.2f}",
        confidence=max(conf, 0.3),
        stop_loss_price=stop,
        take_profit_price=target,
        risk_per_share=risk_per_share,
    )


def evaluate_zarattini_3x_atr(
    df_today_5m: pd.DataFrame,
    df_daily: pd.DataFrame,
    params: KISStrategyParams = KISStrategyParams(),
) -> KISBuySignal:
    """#364 Pure Zarattini 3X 변형 — TQQQ 변형 +9,350% / 93% 알파.

    논문 사양 (3X 레버리지 ETF 최적화):
    - 진입: bar1 양봉 (도지/음봉은 같은 종목 매수 X)
    - 손절: 0.05 × ATR(14일) (절대 가격) — bar1 low보다 정밀
    - 익절: 없음 — EOD까지 hold (큰 모멘텀 끝까지 가져감)
    - 사이징: 1% 리스크 (calc_position_size_risk_based)

    왜 baseline 변형과 다른가:
    - 3X ETF는 일일 변동성이 큼 → bar1 low 손절은 좁아 가짜 stop-out 빈발
    - ATR×5% 손절은 14일 변동성에 적응 (여전히 tight하지만 noise 흡수)
    - No TP = 큰 추세 끝까지 잡음 (EOD까지 hold)
    - 백테스트(논문): TQQQ 2016~2023 +9,350%

    Args:
        df_today_5m: 오늘 5분봉 (bar1 = 첫 봉, 양봉/음봉/도지 판단용)
        df_daily: 14일 + α 일봉 (ATR 계산용)
        params: doji_threshold_pct, atr_stop_pct (5.0), atr_period (14), risk_pct_per_trade

    Returns:
        KISBuySignal:
          - should_buy=True: 양봉 + ATR 가용 → stop_loss_price 절대가, take_profit_price=None
          - should_buy=False: 도지/음봉/데이터 부족
    """
    if df_today_5m is None or len(df_today_5m) < 1:
        return KISBuySignal(False, "Bar-1 데이터 없음")

    bar1 = df_today_5m.iloc[0]
    o = float(bar1["open"])
    c = float(bar1["close"])

    if o <= 0:
        return KISBuySignal(False, "Bar-1 open 가격 이상")

    body_pct = abs(c - o) / o * 100

    # 도지 — 매매 X
    if body_pct < params.doji_threshold_pct:
        return KISBuySignal(
            False,
            f"Bar-1 도지 (몸통 {body_pct:.3f}% < {params.doji_threshold_pct}% — 매매 X)",
            bar1_pattern="doji",
            bar1_body_pct=body_pct,
        )

    # 음봉 — 인버스 ETF는 별도 호출
    if c < o:
        return KISBuySignal(
            False,
            f"Bar-1 음봉 ${c:.2f} < ${o:.2f} — 이 종목 매수 X (인버스 ETF 별도 평가)",
            bar1_pattern="bearish",
            bar1_body_pct=body_pct,
        )

    # 양봉 — ATR 손절 계산
    atr = calc_atr(df_daily, period=params.atr_period)
    if atr is None:
        return KISBuySignal(
            False,
            f"ATR({params.atr_period}d) 계산 불가 — 일봉 데이터 부족 ({len(df_daily) if df_daily is not None else 0}봉)",
            bar1_pattern="bullish",
            bar1_body_pct=body_pct,
        )

    entry = c  # 첫 봉 close ≈ 둘째 봉 open (실 체결가는 주문 시 결정)
    stop_distance = (params.atr_stop_pct / 100.0) * atr  # 0.05 × ATR
    stop = entry - stop_distance
    if stop <= 0 or stop_distance <= 0:
        return KISBuySignal(
            False,
            f"ATR 손절 계산 이상 (entry ${entry:.2f}, ATR ${atr:.2f}, stop_dist ${stop_distance:.4f})",
            bar1_pattern="bullish",
            bar1_body_pct=body_pct,
        )

    # 신뢰도: 양봉 몸통 + ATR 대비 진입가 (정성적)
    conf = round(min(0.95, body_pct / 2.0), 2)

    return KISBuySignal(
        True,
        f"3X-ATR 양봉 ${o:.2f}→${c:.2f} (+{body_pct:.2f}%) | "
        f"ATR(14d)=${atr:.2f} → 손절 ${stop:.2f} (dist ${stop_distance:.4f}, "
        f"{params.atr_stop_pct}% × ATR) | TP 없음 (EOD까지)",
        confidence=max(conf, 0.3),
        stop_loss_price=stop,
        signal_price=entry,  # #396: 갭 가드 비교용
        bar1_pattern="bullish",
        bar1_body_pct=body_pct,
        take_profit_price=None,  # 3X 변형: TP 없음, EOD가 익절
        risk_per_share=stop_distance,
    )


def calc_position_size_risk_based(
    available_budget: float,
    current_price: float,
    risk_per_share: float,
    fractional: bool,
    params: KISStrategyParams = KISStrategyParams(),
) -> tuple[float, str]:
    """#364 1% 리스크 기반 포지션 사이징 (Pure Zarattini).

    논문 사양: 한 거래당 최대 손실 = 계좌 × risk_pct_per_trade%.
    수량 = (계좌 × 0.01) / |entry − stop|.
    실제 포지션 크기는 계좌 풀매수와 min 처리 (자본 한도).

    Args:
        available_budget: 가용 예산 (USD)
        current_price: 매수가 (USD)
        risk_per_share: |entry − stop| (주당 리스크 USD)
        fractional: True면 소수점 매수 가능
    """
    if available_budget <= 0:
        return 0.0, "가용 예산 없음"
    if current_price <= 0:
        return 0.0, "가격 정보 없음"
    if risk_per_share <= 0:
        return 0.0, "주당 리스크 0 이하"

    # 1% 리스크 사이징
    max_risk = available_budget * (params.risk_pct_per_trade / 100.0)
    qty_by_risk = max_risk / risk_per_share

    # 자본 한도
    qty_by_budget = available_budget / current_price

    qty_target = min(qty_by_risk, qty_by_budget)
    constraint = "리스크" if qty_by_risk < qty_by_budget else "자본"

    if not fractional:
        qty = int(qty_target)
        if qty < 1:
            return 0.0, (
                f"수량 0 (목표 {qty_target:.2f} < 1주, 리스크 ${max_risk:.2f}/주당 ${risk_per_share:.2f})"
            )
        return float(qty), (
            f"{qty}주 (제약={constraint}, 리스크 ${max_risk:.2f}/{params.risk_pct_per_trade}%)"
        )

    qty = round(qty_target, 4)
    if qty < 0.001:
        return 0.0, "수량 < 0.001 (소수점 한도)"
    return qty, (
        f"{qty:.4f}주 (제약={constraint}, 리스크 ${max_risk:.2f}/{params.risk_pct_per_trade}%)"
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
    take_profit_price: float | None = None,
) -> KISSellSignal:
    """매도 판단. highest_since_buy는 외부에서 추적 (트레일링용).

    Args:
        stop_loss_price: #305 Zarattini ORB 모드 — 절대 가격 손절선 (OR_low).
            None이면 stop_loss_pct(%) 사용. 값 있으면 우선.
        take_profit_price: #364 Pure Zarattini 모드 — 절대 가격 익절선 (10R).
            None이면 take_profit_pct(%) 룰. 값 있으면 도달 시 즉시 익절.
    """
    if buy_price <= 0:
        return KISSellSignal(False, "매수가 미상")

    pnl_pct = (current_price - buy_price) / buy_price * 100

    # 1-A. ORB 손절 (Zarattini ORB / Bar-1 공통) — 절대 가격
    if stop_loss_price is not None and stop_loss_price > 0 and current_price <= stop_loss_price:
        return KISSellSignal(
            True,
            f"손절 ${stop_loss_price:.2f} 도달 (현재 ${current_price:.2f}, {pnl_pct:.2f}%)",
            is_profit_taking=False,
        )

    # 1-B. 절대% 손절 (기존)
    if pnl_pct <= params.stop_loss_pct:
        return KISSellSignal(True, f"손절 {pnl_pct:.2f}%", is_profit_taking=False)

    # 1-C. #364 10R 익절 (Pure Zarattini 모드) — 절대 가격 도달 시 즉시 매도
    if take_profit_price is not None and take_profit_price > 0 and current_price >= take_profit_price:
        return KISSellSignal(
            True,
            f"10R 익절 ${take_profit_price:.2f} 도달 (현재 ${current_price:.2f}, {pnl_pct:+.2f}%)",
            is_profit_taking=True,
        )

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
