"""#321/#360 VwapOrbBreakout 전략 테스트."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from cryptobot.strategies.base import StrategyParams
from cryptobot.strategies.vwap_orb_breakout import (
    EOD_HOUR_KST,
    ENTRY_WINDOW_HOURS,
    ORB_HOUR_KST,
    VwapOrbBreakout,
    filter_session_bars,
    filter_today_bars,
    is_entry_window,
    is_eod_window,
)

KST = ZoneInfo("Asia/Seoul")


def _make_15min_df(prices_with_vol, base_str: str = "2026-05-09 22:00:00"):
    """15분봉 더미. 디폴트 base = ORB 시작 시각(KST 22:00)."""
    rows = []
    base = pd.Timestamp(base_str)
    for i, (o, h, l, c, v) in enumerate(prices_with_vol):
        rows.append({"date": base + pd.Timedelta(minutes=15 * i),
                     "open": o, "high": h, "low": l, "close": c, "volume": v})
    return pd.DataFrame(rows).set_index("date")


def _patch_now(monkeypatch, dt: datetime) -> None:
    """vwap_orb_breakout 내부 datetime.now만 패치 (다른 datetime 동작 유지)."""
    import cryptobot.strategies.vwap_orb_breakout as mod

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return dt if tz is None else dt.astimezone(tz)

    monkeypatch.setattr(mod, "datetime", _DT)


def test_check_buy_orb_breakout(monkeypatch):
    """진입 윈도우 안에서 ORB 돌파 + VWAP + 거래량 spike 충족 시 매수."""
    _patch_now(monkeypatch, datetime(2026, 5, 9, 23, 30, tzinfo=KST))
    df = _make_15min_df([
        (100, 101, 99, 100.5, 1000),
        (100.5, 102, 100, 101, 1000),
        (101, 103, 100.5, 102, 1000),
        (102, 104, 101, 103, 1000),  # 4봉 = 1시간 ORB. OR_high=104, OR_low=99
        (103, 106, 102, 105, 3000),  # 거래량 spike 3x
    ])
    strategy = VwapOrbBreakout(StrategyParams(extra={
        "orb_minutes": 60, "bar_minutes": 15, "volume_spike_multiplier": 1.5,
    }))
    sig = strategy.check_buy(df, current_price=106.0)
    assert sig.signal_type == "buy"
    assert sig.stop_loss == 99.0  # OR_low
    assert "ORB↑" in sig.reason


def test_check_buy_below_orb(monkeypatch):
    _patch_now(monkeypatch, datetime(2026, 5, 9, 23, 30, tzinfo=KST))
    df = _make_15min_df([
        (100, 105, 99, 101, 1000),
        (101, 105, 100, 102, 1000),
        (102, 106, 101, 103, 1000),
        (103, 107, 102, 104, 1000),  # OR_high=107
        (104, 105, 103, 104, 3000),
    ])
    strategy = VwapOrbBreakout(StrategyParams(extra={
        "orb_minutes": 60, "bar_minutes": 15, "volume_spike_multiplier": 1.5,
    }))
    sig = strategy.check_buy(df, current_price=105.0)
    assert sig.signal_type == "hold"
    assert "ORB 미돌파" in sig.reason


def test_check_buy_no_volume_spike(monkeypatch):
    _patch_now(monkeypatch, datetime(2026, 5, 9, 23, 30, tzinfo=KST))
    df = _make_15min_df([
        (100, 101, 99, 100, 1000),
        (100, 102, 100, 101, 1000),
        (101, 103, 100, 102, 1000),
        (102, 104, 101, 103, 1000),
        (103, 106, 102, 105, 1100),  # 1.1x — 임계 1.5x 미달
    ])
    strategy = VwapOrbBreakout(StrategyParams(extra={
        "orb_minutes": 60, "bar_minutes": 15, "volume_spike_multiplier": 1.5,
    }))
    sig = strategy.check_buy(df, current_price=106.0)
    assert sig.signal_type == "hold"
    assert "거래량 spike 부족" in sig.reason


def test_check_buy_outside_entry_window(monkeypatch):
    """진입 윈도우 밖(예: 10:00 KST)이면 hold."""
    _patch_now(monkeypatch, datetime(2026, 5, 9, 10, 0, tzinfo=KST))
    df = _make_15min_df([
        (100, 101, 99, 100.5, 1000),
        (100.5, 102, 100, 101, 1000),
        (101, 103, 100.5, 102, 1000),
        (102, 104, 101, 103, 1000),
        (103, 106, 102, 105, 3000),
    ])
    strategy = VwapOrbBreakout(StrategyParams(extra={"volume_spike_multiplier": 1.5}))
    sig = strategy.check_buy(df, current_price=106.0)
    assert sig.signal_type == "hold"
    assert "진입 윈도우 외" in sig.reason


def test_is_eod_window_at_11am(monkeypatch):
    """KST 11:00 정각 → EOD."""
    monkeypatch.setenv("COIN_EOD_HOUR_KST", "11")
    assert is_eod_window(datetime(2026, 5, 9, 11, 0, 0, tzinfo=KST)) is True


def test_is_eod_window_at_1104(monkeypatch):
    monkeypatch.setenv("COIN_EOD_HOUR_KST", "11")
    assert is_eod_window(datetime(2026, 5, 9, 11, 4, 0, tzinfo=KST)) is True


def test_is_eod_window_outside(monkeypatch):
    monkeypatch.setenv("COIN_EOD_HOUR_KST", "11")
    assert is_eod_window(datetime(2026, 5, 9, 11, 10, 0, tzinfo=KST)) is False
    assert is_eod_window(datetime(2026, 5, 9, 10, 0, 0, tzinfo=KST)) is False
    assert is_eod_window(datetime(2026, 5, 9, 22, 0, 0, tzinfo=KST)) is False


def test_is_entry_window_in_window(monkeypatch):
    monkeypatch.setenv("COIN_ORB_HOUR_KST", "22")
    monkeypatch.setenv("COIN_ENTRY_WINDOW_HOURS", "5")
    assert is_entry_window(datetime(2026, 5, 9, 23, 30, tzinfo=KST)) is True
    assert is_entry_window(datetime(2026, 5, 10, 0, 30, tzinfo=KST)) is True
    assert is_entry_window(datetime(2026, 5, 10, 3, 59, tzinfo=KST)) is True


def test_is_entry_window_outside(monkeypatch):
    monkeypatch.setenv("COIN_ORB_HOUR_KST", "22")
    monkeypatch.setenv("COIN_ENTRY_WINDOW_HOURS", "5")
    assert is_entry_window(datetime(2026, 5, 10, 4, 1, tzinfo=KST)) is False
    assert is_entry_window(datetime(2026, 5, 9, 22, 30, tzinfo=KST)) is False  # ORB 형성 중
    assert is_entry_window(datetime(2026, 5, 9, 12, 0, tzinfo=KST)) is False


def test_filter_session_bars_after_orb_start(monkeypatch):
    """now가 23시(ORB 시작 22시 후) → 오늘 22:00 이후 봉만."""
    monkeypatch.setenv("COIN_ORB_HOUR_KST", "22")
    df = pd.DataFrame({
        "open": [1, 2, 3, 4], "high": [1, 2, 3, 4], "low": [1, 2, 3, 4],
        "close": [1, 2, 3, 4], "volume": [1, 2, 3, 4],
    }, index=pd.to_datetime([
        "2026-05-09 21:30:00",
        "2026-05-09 22:00:00",
        "2026-05-09 22:30:00",
        "2026-05-09 23:30:00",
    ]))
    now = datetime(2026, 5, 9, 23, 45, 0, tzinfo=KST)
    filtered = filter_session_bars(df, now=now)
    assert len(filtered) == 3  # 22:00, 22:30, 23:30


def test_filter_session_bars_before_orb_start(monkeypatch):
    """now가 03시(ORB 시작 22시 전) → 어제 22:00 이후 봉만."""
    monkeypatch.setenv("COIN_ORB_HOUR_KST", "22")
    df = pd.DataFrame({
        "open": [1, 2, 3, 4], "high": [1, 2, 3, 4], "low": [1, 2, 3, 4],
        "close": [1, 2, 3, 4], "volume": [1, 2, 3, 4],
    }, index=pd.to_datetime([
        "2026-05-09 21:00:00",
        "2026-05-09 22:30:00",
        "2026-05-10 00:30:00",
        "2026-05-10 02:30:00",
    ]))
    now = datetime(2026, 5, 10, 3, 0, 0, tzinfo=KST)
    filtered = filter_session_bars(df, now=now)
    assert len(filtered) == 3  # 어제 22:30 + 오늘 00:30, 02:30


def test_filter_today_bars_alias():
    """filter_today_bars는 filter_session_bars 별칭 (하위 호환)."""
    assert filter_today_bars is filter_session_bars


def test_strategy_info():
    s = VwapOrbBreakout()
    info = s.info()
    assert info.name == "vwap_orb_breakout"
    assert info.timeframe == "15m"
    assert "Zarattini" in info.display_name


def test_default_constants():
    """Option 1 디폴트 상수 (#360)."""
    assert ORB_HOUR_KST == 22
    assert EOD_HOUR_KST == 11
    assert ENTRY_WINDOW_HOURS == 5


# === check_sell: roi_table 우회 검증 (#356/#357 머지됨) ===


def _strategy_with_roi_table():
    params = StrategyParams(
        stop_loss_pct=-5.0,
        trailing_stop_pct=-3.0,
        roi_table={10: 3.5, 60: 0.8, 240: 1.5},
    )
    return VwapOrbBreakout(params)


def test_check_sell_does_not_fire_on_small_profit_after_60min():
    s = _strategy_with_roi_table()
    s._hold_minutes = 70
    sig = s.check_sell(df=None, current_price=100.9, buy_price=100.0)
    assert sig.signal_type == "hold", f"roi_table 우회 실패 — {sig.reason}"


def test_check_sell_stop_loss_fires():
    s = _strategy_with_roi_table()
    sig = s.check_sell(df=None, current_price=94.5, buy_price=100.0)
    assert sig.signal_type == "sell"
    assert sig.is_profit_taking is False
    assert "손절" in sig.reason


def test_check_sell_trailing_fires_after_peak_drop():
    s = _strategy_with_roi_table()
    s.check_sell(df=None, current_price=110.0, buy_price=100.0)
    sig = s.check_sell(df=None, current_price=106.0, buy_price=100.0)
    assert sig.signal_type == "sell"
    assert sig.is_profit_taking is True
    assert "트레일링" in sig.reason


def test_check_sell_holds_in_normal_run():
    s = _strategy_with_roi_table()
    sig = s.check_sell(df=None, current_price=101.5, buy_price=100.0)
    assert sig.signal_type == "hold"
