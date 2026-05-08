"""#321 VwapOrbBreakout 전략 테스트."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from cryptobot.strategies.base import StrategyParams
from cryptobot.strategies.vwap_orb_breakout import (
    EOD_HOUR_KST,
    VwapOrbBreakout,
    filter_today_bars,
    is_eod_window,
)

KST = ZoneInfo("Asia/Seoul")


def _make_15min_df(prices_with_vol):
    """15분봉 더미 (KST 자정부터)."""
    rows = []
    base = pd.Timestamp("2026-05-09 00:00:00")
    for i, (o, h, l, c, v) in enumerate(prices_with_vol):
        rows.append({"date": base + pd.Timedelta(minutes=15 * i),
                     "open": o, "high": h, "low": l, "close": c, "volume": v})
    return pd.DataFrame(rows).set_index("date")


def test_check_buy_orb_breakout():
    """ORB 돌파 + VWAP + 거래량 spike 충족 시 매수."""
    df = _make_15min_df([
        (100, 101, 99, 100.5, 1000),
        (100.5, 102, 100, 101, 1000),
        (101, 103, 100.5, 102, 1000),
        (102, 104, 101, 103, 1000),  # 4봉 = 1시간 ORB. OR_high=104, OR_low=99
        (103, 106, 102, 105, 3000),  # 거래량 spike 3x
    ])
    strategy = VwapOrbBreakout(StrategyParams(extra={"orb_minutes": 60, "bar_minutes": 15, "volume_spike_multiplier": 1.5}))
    sig = strategy.check_buy(df, current_price=106.0)
    assert sig.signal_type == "buy"
    assert sig.stop_loss == 99.0  # OR_low
    assert "ORB↑" in sig.reason


def test_check_buy_below_orb():
    """ORB 미돌파 시 hold."""
    df = _make_15min_df([
        (100, 105, 99, 101, 1000),
        (101, 105, 100, 102, 1000),
        (102, 106, 101, 103, 1000),
        (103, 107, 102, 104, 1000),  # OR_high=107
        (104, 105, 103, 104, 3000),
    ])
    strategy = VwapOrbBreakout(StrategyParams(extra={"orb_minutes": 60, "bar_minutes": 15, "volume_spike_multiplier": 1.5}))
    sig = strategy.check_buy(df, current_price=105.0)
    assert sig.signal_type == "hold"
    assert "ORB 미돌파" in sig.reason


def test_check_buy_no_volume_spike():
    """거래량 spike 부족 시 hold."""
    df = _make_15min_df([
        (100, 101, 99, 100, 1000),
        (100, 102, 100, 101, 1000),
        (101, 103, 100, 102, 1000),
        (102, 104, 101, 103, 1000),
        (103, 106, 102, 105, 1100),  # 1.1x 만 — 임계 1.5x 미달
    ])
    strategy = VwapOrbBreakout(StrategyParams(extra={"orb_minutes": 60, "bar_minutes": 15, "volume_spike_multiplier": 1.5}))
    sig = strategy.check_buy(df, current_price=106.0)
    assert sig.signal_type == "hold"
    assert "거래량 spike 부족" in sig.reason


def test_check_buy_orb_forming():
    """ORB 형성 안된 상태."""
    df = _make_15min_df([
        (100, 101, 99, 100, 1000),
        (100, 102, 100, 101, 1000),
    ])
    strategy = VwapOrbBreakout(StrategyParams(extra={"orb_minutes": 60, "bar_minutes": 15}))
    sig = strategy.check_buy(df, current_price=101.0)
    assert sig.signal_type == "hold"
    assert "ORB 형성 중" in sig.reason


def test_is_eod_window_at_9am():
    """KST 09:00 정각 → EOD."""
    assert is_eod_window(datetime(2026, 5, 9, 9, 0, 0, tzinfo=KST)) is True


def test_is_eod_window_at_904():
    """KST 09:04 → EOD 윈도우 안 (5분 윈도우)."""
    assert is_eod_window(datetime(2026, 5, 9, 9, 4, 0, tzinfo=KST)) is True


def test_is_eod_window_outside():
    """KST 09:10 → 윈도우 밖."""
    assert is_eod_window(datetime(2026, 5, 9, 9, 10, 0, tzinfo=KST)) is False
    assert is_eod_window(datetime(2026, 5, 9, 8, 0, 0, tzinfo=KST)) is False
    assert is_eod_window(datetime(2026, 5, 9, 12, 0, 0, tzinfo=KST)) is False


def test_filter_today_bars():
    """KST 자정 이후 봉만 필터링."""
    df = pd.DataFrame({
        "open": [1, 2, 3], "high": [1, 2, 3], "low": [1, 2, 3],
        "close": [1, 2, 3], "volume": [1, 2, 3],
    }, index=pd.to_datetime([
        "2026-05-08 23:30:00",  # 어제
        "2026-05-09 00:00:00",  # 오늘 자정
        "2026-05-09 06:00:00",  # 오늘
    ]))
    now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=KST)
    filtered = filter_today_bars(df, now=now)
    assert len(filtered) == 2  # 자정과 06:00


def test_strategy_info():
    s = VwapOrbBreakout()
    info = s.info()
    assert info.name == "vwap_orb_breakout"
    assert info.timeframe == "15m"
    assert "Zarattini" in info.display_name


def test_eod_hour_constant():
    """EOD 시간 상수 = KST 09:00."""
    assert EOD_HOUR_KST == 9
