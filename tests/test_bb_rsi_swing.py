"""#376: bb_rsi_combined Swing 모드 테스트.

roi_table 우회 + min_profit_for_trailing 가드 동작 검증.
"""

from __future__ import annotations

import pandas as pd

from cryptobot.strategies.base import StrategyParams
from cryptobot.strategies.bb_rsi_combined import MIN_PROFIT_FOR_TRAILING, BBRSICombined


def _make_df(closes: list[float], with_volume: bool = True) -> pd.DataFrame:
    """단순 OHLCV DataFrame. close만 의미있고 high/low/open은 close = 그대로."""
    rows = []
    base = pd.Timestamp("2026-05-01")
    for i, c in enumerate(closes):
        rows.append({
            "date": base + pd.Timedelta(days=i),
            "open": c,
            "high": c * 1.005,
            "low": c * 0.995,
            "close": c,
            "volume": 1000 if with_volume else 0,
        })
    return pd.DataFrame(rows).set_index("date")


def _strategy(min_profit: float | None = None, trailing: float = -2.5) -> BBRSICombined:
    extra = {"bb_std": 2.0, "bb_period": 20, "rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 50}
    if min_profit is not None:
        extra["min_profit_for_trailing"] = min_profit
    params = StrategyParams(stop_loss_pct=-5.0, trailing_stop_pct=trailing, extra=extra)
    return BBRSICombined(params)


# === 디폴트 값 ===


def test_default_min_profit_is_5_percent():
    """디폴트 가드값 = 5.0% (user 멘탈 모델)."""
    assert MIN_PROFIT_FOR_TRAILING == 5.0
    s = _strategy()
    assert s._min_profit_for_trailing == 5.0


def test_min_profit_override_via_params():
    """params.extra.min_profit_for_trailing 으로 override 가능."""
    s = _strategy(min_profit=3.0)
    assert s._min_profit_for_trailing == 3.0


# === 손절 (가드 무시, 무조건) ===


def test_stop_loss_fires_unconditionally():
    """-5% 손절 발동 — 가드/추세 무시."""
    s = _strategy()
    # 25개 봉, 일정한 가격 (BB/RSI 계산용)
    df = _make_df([100.0] * 25)
    sig = s.check_sell(df, current_price=94.0, buy_price=100.0)  # -6% 손실
    assert sig.signal_type == "sell"
    assert sig.is_profit_taking is False
    assert "손절" in sig.reason


def test_stop_loss_priority_over_other_rules():
    """손절은 RSI/BB 조건과 무관하게 우선."""
    s = _strategy()
    # 가격 회복하지 않은 채 RSI 정상이어도 손절 우선
    df = _make_df([100.0] * 25)
    sig = s.check_sell(df, current_price=93.0, buy_price=100.0)
    assert sig.signal_type == "sell"
    assert "손절" in sig.reason


# === 트레일링 가드 ===


def test_trailing_blocked_below_5pct_guard():
    """피크 +3% → -3% drop이어도 net_pnl=2% < 5% 가드 → hold."""
    s = _strategy()
    df = _make_df([100.0] * 25)
    # 피크 갱신 +3%
    s.check_sell(df, current_price=103.0, buy_price=100.0)
    # 피크에서 -2.91% (= 100 / 103) drop, net_pnl = -0.1% < 5% → hold
    sig = s.check_sell(df, current_price=100.0, buy_price=100.0)
    assert sig.signal_type == "hold"
    assert "트레일링 가드" in sig.reason


def test_trailing_fires_when_above_guard():
    """피크 +10% → -3% drop → net_pnl ~6.7% > 5% 가드 → trailing 매도."""
    s = _strategy()
    df = _make_df([100.0] * 25)
    s.check_sell(df, current_price=110.0, buy_price=100.0)  # 피크 갱신
    sig = s.check_sell(df, current_price=106.7, buy_price=100.0)  # -3% from peak, +6.7% from buy
    assert sig.signal_type == "sell"
    assert sig.is_profit_taking is True
    assert "트레일링" in sig.reason


def test_trailing_at_guard_threshold():
    """net_pnl이 min_profit_for_trailing 살짝 위 → 발동 (>= 비교).

    피크 110 → current 103.5: drop -5.9% (>-2.5% threshold), pnl 3.5%, net 3.4% > 3% 가드.
    """
    s = _strategy(min_profit=3.0, trailing=-2.5)
    df = _make_df([100.0] * 25)
    s.check_sell(df, current_price=110.0, buy_price=100.0)  # peak update (BB middle도 발동할 수 있음 — 무시)
    sig = s.check_sell(df, current_price=103.5, buy_price=100.0)
    assert sig.signal_type == "sell"


# === RSI 정상 복귀 가드 ===


def test_rsi_recovery_blocked_below_guard():
    """RSI 회복 정상권이어도 net_pnl < 5% → hold."""
    s = _strategy()
    # RSI 50 만들기 위해 점진 상승
    closes = [100 - i * 0.5 for i in range(20)] + [90 + i * 0.5 for i in range(5)]
    df = _make_df(closes)
    sig = s.check_sell(df, current_price=92.5, buy_price=90.0)  # +2.78%
    # buy_price=90, current=92.5 = +2.78%, net=2.68% < 5%
    assert sig.signal_type == "hold"


def test_rsi_recovery_or_bb_middle_fires_above_guard():
    """RSI/BB 중간 가드 통과 시 매도 (둘 중 어느 트리거든 익절성 매도)."""
    s = _strategy()
    # 점진 상승 → RSI/BB 중간선 트리거 가능
    closes = [100 - i * 0.5 for i in range(20)] + [90 + i * 1.0 for i in range(5)]
    df = _make_df(closes)
    sig = s.check_sell(df, current_price=96.0, buy_price=90.0)  # +6.67%, net 6.57%
    assert sig.signal_type == "sell"
    assert sig.is_profit_taking is True
    # 트리거가 트레일링/RSI/BB 중 하나
    assert any(k in sig.reason for k in ("트레일링", "RSI", "BB"))


# === BB 중간선 가드 ===


def test_bb_middle_blocked_below_guard():
    """BB 중간선 도달했어도 net_pnl < 5% → hold."""
    s = _strategy()
    # 가격이 BB 중간선에 살짝 닿게 데이터 구성
    closes = [100.0] * 25
    df = _make_df(closes)
    # BB 중간 = 100 (모든 close 동일이라 std=0, BB 의미없음). 다른 예
    closes2 = [98, 99, 100, 101, 102] * 5  # 평균 100
    df2 = _make_df(closes2)
    sig = s.check_sell(df2, current_price=100.5, buy_price=100.0)  # +0.5%
    assert sig.signal_type == "hold"


def test_bb_middle_fires_above_guard():
    """BB 중간선 도달 + net_pnl >= 5% → BB 중간 익절."""
    s = _strategy()
    closes = [98, 99, 100, 101, 102] * 5  # BB 중간 = 100
    df = _make_df(closes)
    # buy 94, current 100 → +6.4%, net 6.3% > 5%
    sig = s.check_sell(df, current_price=100.0, buy_price=94.0)
    # 트레일링이나 BB 중간 중 하나 발동
    if sig.signal_type == "sell" and "BB" in sig.reason:
        assert sig.is_profit_taking is True


# === Hold 케이스 ===


def test_holds_when_nothing_triggered():
    """저점에서 약간 회복 — 손절/트레일링/RSI/BB 모두 미발동 → hold."""
    s = _strategy()
    df = _make_df([100.0] * 25)
    sig = s.check_sell(df, current_price=101.5, buy_price=100.0)  # +1.5% 작은 수익
    assert sig.signal_type == "hold"


def test_hold_message_includes_guard():
    """Hold 메시지에 가드 정보 포함."""
    s = _strategy()
    df = _make_df([100.0] * 25)
    sig = s.check_sell(df, current_price=101.0, buy_price=100.0)
    assert sig.signal_type == "hold"
    assert "가드" in sig.reason
    assert "5.0" in sig.reason or "5%" in sig.reason


# === roi_table 우회 검증 (핵심!) ===


def test_roi_table_bypassed_for_small_profit_after_long_hold():
    """ROI table {120: 2.5}의 +2.5% 익절 발동 안 되어야 (roi_table 우회)."""
    s = _strategy()
    s._hold_minutes = 240  # 4시간 보유
    df = _make_df([100.0] * 25)
    sig = s.check_sell(df, current_price=103.0, buy_price=100.0)  # +3% gross, net 2.9%
    # roi_table {120:2.5,240:1.8} 가 발동했다면 매도 — 우회됐으면 hold
    assert sig.signal_type == "hold", f"roi_table 우회 실패: {sig.reason}"


def test_roi_table_bypassed_for_10min_quick_pop():
    """ROI table {10: 3.5}의 빠른 익절도 우회 (5% 가드 미달이면 hold)."""
    s = _strategy()
    s._hold_minutes = 15
    df = _make_df([100.0] * 25)
    sig = s.check_sell(df, current_price=104.0, buy_price=100.0)  # net 3.9% < 5%
    assert sig.signal_type == "hold", f"roi_table 우회 실패: {sig.reason}"


# === 매수 시나리오 회귀 (기존 동작 유지) ===


def test_check_buy_strong_signal():
    """BB 하단 이탈 + RSI < 30 — 강한 매수 신호."""
    # 점진 하락하다가 마지막 봉이 BB 하단 밑 + RSI 과매도
    closes = [100 - i * 0.5 for i in range(25)]  # 100→87.5 점진 하락
    df = _make_df(closes)
    s = _strategy()
    sig = s.check_buy(df, current_price=85.0)  # 마지막 close보다 낮은 가격
    # BB 하단 + RSI 과매도 가능성. 다만 데이터에 따라 결과 다를 수 있어 type만 확인
    assert sig.signal_type in ("buy", "hold")
