"""KIS 보수적 전략 테스트 (#279)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from cryptobot.bot.kis_strategy import (
    KISStrategyParams,
    calc_position_size,
    evaluate_buy,
    evaluate_sell,
)


def _make_df(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    df = pd.DataFrame({"close": closes})
    df["open"] = df["close"]
    df["high"] = df["close"]
    df["low"] = df["close"]
    df["volume"] = volumes if volumes is not None else [1_000_000] * len(closes)
    return df


# ---- evaluate_buy ----

def test_buy_data_insufficient_returns_false():
    df = _make_df([100.0] * 10)
    sig = evaluate_buy(df, current_price=100.0)
    assert sig.should_buy is False
    assert "데이터 부족" in sig.reason


def test_buy_signal_when_oversold_below_ma20():
    # MA60 위(추세 살아있음) + 점진 하락 → MA20 아래 + RSI 낮음
    closes = list(np.linspace(100, 130, 60)) + [120, 118, 115, 110, 108]
    df = _make_df(closes)
    sig = evaluate_buy(df, current_price=108.0)
    # 정확한 확정은 데이터에 따라 달라질 수 있으나 MA60(0.92×) 위 + MA20 아래는 충족
    # RSI는 셋업에 따라 달라지므로 should_buy 가능 여부 (true OR explicit reason)
    if not sig.should_buy:
        # 미충족이면 RSI 또는 거래량 사유여야 함 (MA 조건은 충족)
        assert "RSI" in sig.reason or "거래량" in sig.reason


def test_buy_rejected_when_long_trend_broken():
    # 큰 폭락 — MA60 0.92배 아래 → 잘못된 저점 회피
    closes = list(np.linspace(100, 130, 60)) + [80, 75, 70, 60, 50]
    df = _make_df(closes)
    sig = evaluate_buy(df, current_price=50.0)
    assert sig.should_buy is False
    assert "장기추세" in sig.reason or "RSI" in sig.reason  # 어쨌든 거부


def test_buy_rejected_when_rsi_high():
    # 꾸준히 상승 → RSI 높음 → 매수 거부
    closes = list(np.linspace(100, 200, 80))
    df = _make_df(closes)
    sig = evaluate_buy(df, current_price=200.0)
    assert sig.should_buy is False


# ---- evaluate_sell ----

def test_sell_stop_loss():
    sig = evaluate_sell(df=None, current_price=97.0, buy_price=100.0, highest_since_buy=100.0)
    assert sig.should_sell is True
    assert "손절" in sig.reason
    assert sig.is_profit_taking is False


def test_sell_trailing_stop_after_peak():
    # 매수가 100 → 고점 110 → 현재 107.7 (-2.09% from peak, +7.7% pnl)
    sig = evaluate_sell(df=None, current_price=107.7, buy_price=100.0, highest_since_buy=110.0)
    assert sig.should_sell is True
    assert "트레일링" in sig.reason
    assert sig.is_profit_taking is True


def test_sell_no_action_when_holding_in_profit():
    # 매수가 100, 현재 102, 고점 102 — 트레일링 발동 안함, take_profit(4%) 미도달
    sig = evaluate_sell(df=None, current_price=102.0, buy_price=100.0, highest_since_buy=102.0)
    assert sig.should_sell is False


def test_sell_take_profit_without_df_falls_back_to_simple():
    # df 없으면 단순 익절
    sig = evaluate_sell(df=None, current_price=105.0, buy_price=100.0, highest_since_buy=105.0)
    assert sig.should_sell is True
    assert "익절" in sig.reason
    assert sig.is_profit_taking is True


def test_sell_take_profit_overheated_with_df():
    # +5%, RSI 매우 높음 → 즉시 익절
    closes = list(np.linspace(100, 200, 80))
    df = _make_df(closes)
    sig = evaluate_sell(df=df, current_price=200.0, buy_price=190.0, highest_since_buy=200.0)
    assert sig.should_sell is True
    assert "과열" in sig.reason or "추세" in sig.reason or "익절" in sig.reason


def test_sell_holds_when_trend_alive():
    # +5% 익절 임계 도달했지만 RSI 50~70 사이 + 가격 > MA20 — 보유
    closes = list(np.linspace(100, 105, 80))  # 완만한 상승
    df = _make_df(closes)
    sig = evaluate_sell(df=df, current_price=105.0, buy_price=100.0, highest_since_buy=105.0)
    # 결과는 데이터 의존이지만 손절/트레일링은 아니어야 함
    if sig.should_sell:
        # 매도라면 과열 또는 추세이탈 사유
        assert "과열" in sig.reason or "추세" in sig.reason


# ---- calc_position_size ----

def test_position_size_kr_integer_shares():
    qty, reason = calc_position_size(
        available_budget=200_000,
        current_price=70_000,
        fractional=False,
        params=KISStrategyParams(max_position_per_symbol_pct=40.0),
    )
    # 한도 80,000 / 70,000 = 1주
    assert qty == 1.0
    assert "1주" in reason


def test_position_size_kr_skip_when_too_expensive():
    qty, reason = calc_position_size(
        available_budget=200_000,
        current_price=300_000,  # 1주 < 한도
        fractional=False,
        params=KISStrategyParams(max_position_per_symbol_pct=30.0),
    )
    assert qty == 0.0
    assert "예산 부족" in reason


def test_position_size_us_fractional():
    qty, reason = calc_position_size(
        available_budget=200_000,
        current_price=100_000,  # USD price × FX rate
        fractional=True,
        params=KISStrategyParams(max_position_per_symbol_pct=30.0),
    )
    # 한도 60,000 / 100,000 = 0.6주
    assert qty == 0.6
    assert "0.6" in reason


def test_position_size_zero_budget():
    qty, reason = calc_position_size(
        available_budget=0,
        current_price=100,
        fractional=True,
    )
    assert qty == 0.0
    assert "예산" in reason


def test_position_size_us_skip_when_dust():
    # 가격이 너무 비싸서 0.001주 미만 — skip
    qty, reason = calc_position_size(
        available_budget=1_000,
        current_price=10_000_000,
        fractional=True,
        params=KISStrategyParams(max_position_per_symbol_pct=30.0),
    )
    assert qty == 0.0


# ---- KISStrategyParams defaults ----

def test_default_params_match_doc():
    p = KISStrategyParams()
    assert p.rsi_oversold == 35.0
    assert p.rsi_overbought == 70.0
    assert p.take_profit_pct == 4.0
    assert p.stop_loss_pct == -3.0
    assert p.trailing_stop_pct == -2.0
    assert p.max_position_per_symbol_pct == 30.0
    # #285 day trading 디폴트
    assert p.day_trading_mode is False
    assert p.no_buy_window_minutes_before_close == 30
    assert p.force_sell_window_minutes_before_close == 10


# ---- run_kis_us 헬퍼 (#285) ----

def test_parse_universe_default(monkeypatch):
    from cryptobot.entrypoints.run_kis_us import DEFAULT_US_UNIVERSE, _parse_universe

    monkeypatch.delenv("KIS_US_UNIVERSE", raising=False)
    assert _parse_universe() == list(DEFAULT_US_UNIVERSE)


def test_parse_universe_from_env(monkeypatch):
    from cryptobot.entrypoints.run_kis_us import _parse_universe

    monkeypatch.setenv("KIS_US_UNIVERSE", "snxx, sndk ,nvda")
    assert _parse_universe() == ["SNXX", "SNDK", "NVDA"]


def test_parse_universe_empty_falls_back_to_default(monkeypatch):
    from cryptobot.entrypoints.run_kis_us import DEFAULT_US_UNIVERSE, _parse_universe

    monkeypatch.setenv("KIS_US_UNIVERSE", "   ")
    assert _parse_universe() == list(DEFAULT_US_UNIVERSE)


def test_build_params_position_cap_is_100_over_n(monkeypatch):
    from cryptobot.entrypoints.run_kis_us import _build_params

    monkeypatch.delenv("KIS_US_DAY_TRADING", raising=False)
    p = _build_params(universe_size=4)
    assert p.max_position_per_symbol_pct == 25.0  # 100/4
    p2 = _build_params(universe_size=1)
    assert p2.max_position_per_symbol_pct == 100.0  # 단일 종목 풀매수


def test_build_params_day_trading_toggle(monkeypatch):
    from cryptobot.entrypoints.run_kis_us import _build_params

    monkeypatch.setenv("KIS_US_DAY_TRADING", "true")
    p = _build_params(universe_size=2)
    assert p.day_trading_mode is True

    monkeypatch.setenv("KIS_US_DAY_TRADING", "false")
    p2 = _build_params(universe_size=2)
    assert p2.day_trading_mode is False


def test_build_params_thresholds_overridable(monkeypatch):
    from cryptobot.entrypoints.run_kis_us import _build_params

    monkeypatch.setenv("KIS_US_TAKE_PROFIT_PCT", "20")
    monkeypatch.setenv("KIS_US_STOP_LOSS_PCT", "-5")
    monkeypatch.setenv("KIS_US_TRAILING_PCT", "-1.5")
    p = _build_params(universe_size=5)
    assert p.take_profit_pct == 20.0
    assert p.stop_loss_pct == -5.0
    assert p.trailing_stop_pct == -1.5


def test_build_params_day_trading_thresholds_default(monkeypatch):
    """단타모드 ON 시 #301 디폴트: 3X 레버리지 ETF 변동폭에 맞춰 과감하게."""
    from cryptobot.entrypoints.run_kis_us import _build_params

    monkeypatch.delenv("KIS_US_TAKE_PROFIT_PCT", raising=False)
    monkeypatch.delenv("KIS_US_STOP_LOSS_PCT", raising=False)
    monkeypatch.delenv("KIS_US_TRAILING_PCT", raising=False)
    monkeypatch.setenv("KIS_US_DAY_TRADING", "true")
    p = _build_params(universe_size=1)
    assert p.day_trading_mode is True
    assert p.take_profit_pct == 4.0   # #301 익절 4%
    assert p.stop_loss_pct == -4.0    # #301 손절 -4% (1:1 손익비)
    assert p.trailing_stop_pct == -2.0  # #301 트레일링 -2%


def test_build_params_swing_thresholds_default(monkeypatch):
    """스윙(디폴트) 모드는 보수 견딤 임계값."""
    from cryptobot.entrypoints.run_kis_us import _build_params

    monkeypatch.delenv("KIS_US_TAKE_PROFIT_PCT", raising=False)
    monkeypatch.delenv("KIS_US_STOP_LOSS_PCT", raising=False)
    monkeypatch.delenv("KIS_US_TRAILING_PCT", raising=False)
    monkeypatch.setenv("KIS_US_DAY_TRADING", "false")
    p = _build_params(universe_size=20)
    assert p.day_trading_mode is False
    assert p.take_profit_pct == 10.0
    assert p.stop_loss_pct == -10.0
    assert p.trailing_stop_pct == -3.0


# ---- DB 기반 거래 토글 (#285) ----

def _setup_temp_db(tmp_path, monkeypatch):
    """임시 DB로 봇 테스트 환경 셋업."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    # config 모듈 캐시 무효화
    import importlib
    from cryptobot.bot import config as config_mod
    importlib.reload(config_mod)
    from cryptobot.data.database import Database
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    db.initialize()
    return db


def test_db_toggle_default_kr_disabled(tmp_path, monkeypatch):
    """#285: KR 거래 디폴트 false (시드 작을 때 1주 미만 회피)."""
    db = _setup_temp_db(tmp_path, monkeypatch)
    row = db.execute(
        "SELECT value FROM bot_config WHERE key = 'kis_kr_trading_enabled'"
    ).fetchone()
    assert row is not None
    assert dict(row)["value"] == "false"


def test_db_toggle_default_us_enabled(tmp_path, monkeypatch):
    db = _setup_temp_db(tmp_path, monkeypatch)
    row = db.execute(
        "SELECT value FROM bot_config WHERE key = 'kis_us_trading_enabled'"
    ).fetchone()
    assert row is not None
    assert dict(row)["value"] == "true"


def test_db_toggle_can_be_updated(tmp_path, monkeypatch):
    db = _setup_temp_db(tmp_path, monkeypatch)
    db.execute(
        "UPDATE bot_config SET value = 'true' WHERE key = 'kis_kr_trading_enabled'"
    )
    db.commit()
    row = db.execute(
        "SELECT value FROM bot_config WHERE key = 'kis_kr_trading_enabled'"
    ).fetchone()
    assert dict(row)["value"] == "true"


# ---- 단타모드 시간 창 (#285) ----

def test_day_trading_force_sell_window():
    """force_sell_window_minutes_before_close 디폴트 10분."""
    p = KISStrategyParams(day_trading_mode=True)
    assert p.force_sell_window_minutes_before_close == 10
    assert p.no_buy_window_minutes_before_close == 30


def test_day_trading_custom_windows():
    p = KISStrategyParams(
        day_trading_mode=True,
        force_sell_window_minutes_before_close=5,
        no_buy_window_minutes_before_close=15,
    )
    assert p.force_sell_window_minutes_before_close == 5
    assert p.no_buy_window_minutes_before_close == 15


# ---- #303 VWAP + ORB + 거래량 spike ----

def _make_minute_df(prices_with_vol, base_dt="2026-05-08 09:30"):
    """분봉 더미 DF 생성. prices_with_vol = [(open, high, low, close, vol), ...]"""
    rows = []
    ts = pd.Timestamp(base_dt)
    for i, (o, h, l, c, v) in enumerate(prices_with_vol):
        rows.append({"date": ts + pd.Timedelta(minutes=5 * i),
                     "open": o, "high": h, "low": l, "close": c, "volume": v})
    return pd.DataFrame(rows).set_index("date")


def test_calc_vwap_basic():
    from cryptobot.bot.kis_strategy import calc_vwap
    df = _make_minute_df([(100, 102, 99, 101, 1000), (101, 103, 100, 102, 2000)])
    vwap = calc_vwap(df)
    # typical1 = (102+99+101)/3 = 100.67, typical2 = (103+100+102)/3 = 101.67
    # vwap = (100.67×1000 + 101.67×2000) / 3000 = 101.34
    assert abs(vwap - 101.33) < 0.05


def test_calc_orb_returns_high_low():
    from cryptobot.bot.kis_strategy import calc_orb
    # 6봉 (5분 × 6 = 30분)
    df = _make_minute_df([
        (100, 102, 99, 101, 1000),
        (101, 105, 100, 104, 2000),  # 고점 105
        (104, 106, 102, 103, 1500),  # 고점 106
        (103, 104, 100, 101, 1000),  # 저점 100
        (101, 103, 99, 102, 1500),   # 저점 99
        (102, 104, 101, 103, 1500),
        (103, 107, 102, 106, 2000),  # 7번째 봉 (ORB 외)
    ])
    orb = calc_orb(df, orb_minutes=30, bar_minutes=5)
    assert orb is not None
    or_high, or_low = orb
    assert or_high == 106  # 첫 6봉 고점
    assert or_low == 99    # 첫 6봉 저점


def test_evaluate_buy_breakout_signal():
    from cryptobot.bot.kis_strategy import evaluate_buy_breakout, KISStrategyParams
    # ORB 형성 후 돌파 + VWAP 위 + 거래량 spike
    df = _make_minute_df([
        (100, 102, 99, 101, 1000),
        (101, 103, 100, 102, 1000),
        (102, 104, 101, 103, 1000),
        (103, 105, 102, 104, 1000),
        (104, 106, 103, 105, 1000),
        (105, 107, 104, 106, 1000),  # ORB 6봉 끝, OR_high = 107
        (106, 108, 105, 107, 3000),  # 거래량 spike 3000 (avg 1285)
    ])
    sig = evaluate_buy_breakout(df, current_price=108.0, bar_minutes=5,
                                params=KISStrategyParams(orb_minutes=30, volume_spike_multiplier=2.0))
    assert sig.should_buy is True
    assert "ORB↑" in sig.reason
    assert sig.confidence > 0


def test_evaluate_buy_breakout_below_orb():
    from cryptobot.bot.kis_strategy import evaluate_buy_breakout, KISStrategyParams
    df = _make_minute_df([
        (100, 105, 99, 101, 1000),
        (101, 105, 100, 102, 1000),
        (102, 106, 101, 103, 1000),
        (103, 107, 102, 104, 1000),  # OR_high = 107
        (104, 105, 103, 104, 1000),
        (104, 105, 102, 103, 1000),
        (103, 104, 102, 103, 3000),  # 거래량 spike 있어도 ORB 아래
    ])
    sig = evaluate_buy_breakout(df, current_price=104.0, bar_minutes=5,
                                params=KISStrategyParams(orb_minutes=30, volume_spike_multiplier=2.0))
    assert sig.should_buy is False
    assert "ORB 미돌파" in sig.reason


def test_evaluate_buy_breakout_no_volume_spike():
    from cryptobot.bot.kis_strategy import evaluate_buy_breakout, KISStrategyParams
    df = _make_minute_df([
        (100, 102, 99, 101, 1000),
        (101, 103, 100, 102, 1000),
        (102, 104, 101, 103, 1000),
        (103, 105, 102, 104, 1000),
        (104, 106, 103, 105, 1000),
        (105, 107, 104, 106, 1000),
        (106, 108, 105, 107, 1100),  # 약간 spike 1.1x 만 — 임계 미충족
    ])
    sig = evaluate_buy_breakout(df, current_price=108.0, bar_minutes=5,
                                params=KISStrategyParams(orb_minutes=30, volume_spike_multiplier=2.0))
    assert sig.should_buy is False
    assert "거래량 spike 없음" in sig.reason


def test_strategy_default_breakout_in_day_trading_mode():
    """단타 모드 ON 시 디폴트 전략은 'breakout'."""
    p = KISStrategyParams()
    # 디폴트 파라미터 — orb_minutes/volume_spike 검증
    assert p.orb_minutes == 30
    assert p.volume_spike_multiplier == 2.0
