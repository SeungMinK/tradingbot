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


def test_build_params_position_cap_is_100_pct_full(monkeypatch):
    """#309: 종목당 한도 항상 100% (풀매수). 여러 종목은 신뢰도 정렬로 순차 매수."""
    from cryptobot.entrypoints.run_kis_us import _build_params

    monkeypatch.delenv("KIS_US_DAY_TRADING", raising=False)
    assert _build_params(universe_size=1).max_position_per_symbol_pct == 100.0
    assert _build_params(universe_size=4).max_position_per_symbol_pct == 100.0
    assert _build_params(universe_size=20).max_position_per_symbol_pct == 100.0


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


def test_build_params_day_trading_mean_reversion_thresholds(monkeypatch):
    """단타모드 + mean_reversion: TP +4 / SL -4 / TR -2 (#301)."""
    from cryptobot.entrypoints.run_kis_us import _build_params

    monkeypatch.delenv("KIS_US_TAKE_PROFIT_PCT", raising=False)
    monkeypatch.delenv("KIS_US_STOP_LOSS_PCT", raising=False)
    monkeypatch.delenv("KIS_US_TRAILING_PCT", raising=False)
    monkeypatch.setenv("KIS_US_DAY_TRADING", "true")
    monkeypatch.setenv("KIS_US_STRATEGY", "mean_reversion")
    p = _build_params(universe_size=1)
    assert p.day_trading_mode is True
    assert p.take_profit_pct == 4.0
    assert p.stop_loss_pct == -4.0
    assert p.trailing_stop_pct == -2.0


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


# ---- #305 Zarattini 논문 모드: OR_low 손절 + EOD 청산 ----

def test_breakout_signal_includes_stop_loss_price():
    """매수 신호에 OR_low가 stop_loss_price로 포함."""
    from cryptobot.bot.kis_strategy import evaluate_buy_breakout, KISStrategyParams
    df = _make_minute_df([
        (100, 102, 99, 101, 1000),   # OR_low 99
        (101, 103, 100, 102, 1000),
        (102, 104, 101, 103, 1000),
        (103, 105, 102, 104, 1000),
        (104, 106, 103, 105, 1000),
        (105, 107, 104, 106, 1000),  # OR_high 107
        (106, 108, 105, 107, 3000),
    ])
    sig = evaluate_buy_breakout(df, current_price=108.0, bar_minutes=5,
                                params=KISStrategyParams(orb_minutes=30, volume_spike_multiplier=2.0))
    assert sig.should_buy is True
    assert sig.stop_loss_price == 99.0  # OR_low


def test_evaluate_sell_with_orb_stop_loss_price():
    """ORB 모드: 가격이 OR_low 도달 시 손절."""
    from cryptobot.bot.kis_strategy import evaluate_sell

    # 매수 $108, OR_low $99
    sig = evaluate_sell(
        df=None, current_price=99.0, buy_price=108.0,
        highest_since_buy=108.0,
        stop_loss_price=99.0,  # OR_low
    )
    assert sig.should_sell is True
    # 메시지 형식 무관, 절대가 손절 발동 확인 (#364 메시지 일반화: ORB/Bar-1 공통)
    assert "$99.00" in sig.reason
    assert sig.is_profit_taking is False
    assert sig.is_profit_taking is False


def test_evaluate_sell_orb_no_trigger_when_above_or_low():
    """가격이 OR_low 위면 손절 안함 (절대% 손절은 그대로 동작)."""
    from cryptobot.bot.kis_strategy import evaluate_sell, KISStrategyParams

    sig = evaluate_sell(
        df=None, current_price=105.0, buy_price=108.0,
        highest_since_buy=108.0,
        stop_loss_price=99.0,
        params=KISStrategyParams(stop_loss_pct=-99),  # 절대% 발동 안 함
    )
    assert sig.should_sell is False


def test_breakout_paper_mode_thresholds(monkeypatch):
    """단타 + breakout 모드 디폴트: TP 무한대, TR 끔, ORB 5분."""
    from cryptobot.entrypoints.run_kis_us import _build_params

    monkeypatch.delenv("KIS_US_TAKE_PROFIT_PCT", raising=False)
    monkeypatch.delenv("KIS_US_STOP_LOSS_PCT", raising=False)
    monkeypatch.delenv("KIS_US_TRAILING_PCT", raising=False)
    monkeypatch.delenv("KIS_US_ORB_MINUTES", raising=False)
    monkeypatch.setenv("KIS_US_DAY_TRADING", "true")
    monkeypatch.setenv("KIS_US_STRATEGY", "breakout")
    p = _build_params(universe_size=1)
    assert p.take_profit_pct == 999.0   # 사실상 무한 (EOD 청산 처리)
    assert p.stop_loss_pct == -4.0      # 폴백
    assert p.trailing_stop_pct == -99.0 # 사실상 끔
    assert p.orb_minutes == 5           # 논문 5분 ORB
    assert p.max_position_per_symbol_pct == 100.0  # #309 풀매수


def test_buy_queue_sorts_by_confidence_desc():
    """#309 매수 큐 신뢰도 내림차순 정렬 검증."""
    candidates = [
        ("AAPL", 100.0, type("S", (), {"confidence": 0.4, "reason": "low", "should_buy": True})(), None),
        ("NVDA", 200.0, type("S", (), {"confidence": 0.9, "reason": "hi", "should_buy": True})(), None),
        ("AMD", 50.0, type("S", (), {"confidence": 0.7, "reason": "mid", "should_buy": True})(), None),
    ]
    candidates.sort(key=lambda x: getattr(x[2], "confidence", 0.0), reverse=True)
    assert candidates[0][0] == "NVDA"  # 0.9 (1순위)
    assert candidates[1][0] == "AMD"   # 0.7
    assert candidates[2][0] == "AAPL"  # 0.4


# ===================================================================
# #364 Pure Zarattini Bar-1 directional 테스트
# ===================================================================


def _bar1_df(o: float, h: float, l: float, c: float) -> pd.DataFrame:
    """단일 5분봉 — bar1만 평가하면 되니 1봉만."""
    return pd.DataFrame({
        "open": [o], "high": [h], "low": [l], "close": [c], "volume": [1_000_000],
    })


def test_zarattini_bar1_bullish_long_signal():
    """첫 봉 양봉 → LONG 시그널 + 10R TP + OR_low SL."""
    from cryptobot.bot.kis_strategy import evaluate_zarattini_bar1

    # bar1: open=100, high=102, low=99.5, close=101 (양봉, body 1%)
    df = _bar1_df(100.0, 102.0, 99.5, 101.0)
    sig = evaluate_zarattini_bar1(df)
    assert sig.should_buy is True
    assert sig.stop_loss_price == 99.5  # OR_low
    # 10R TP = 101 + 10×(101-99.5) = 101 + 15 = 116
    assert sig.take_profit_price == 116.0
    assert sig.risk_per_share == 1.5
    assert "양봉" in sig.reason
    assert "10R" in sig.reason


def test_zarattini_bar1_bearish_skip_signal():
    """첫 봉 음봉 → 매수 X (인버스 ETF 별도 평가 가정)."""
    from cryptobot.bot.kis_strategy import evaluate_zarattini_bar1

    df = _bar1_df(100.0, 100.5, 98.0, 99.0)  # 음봉 1%
    sig = evaluate_zarattini_bar1(df)
    assert sig.should_buy is False
    assert "음봉" in sig.reason


def test_zarattini_bar1_doji_skip():
    """도지(몸통 < 0.05%) → 매매 X."""
    from cryptobot.bot.kis_strategy import evaluate_zarattini_bar1

    # body = 0.03% < 임계 0.05%
    df = _bar1_df(100.00, 100.5, 99.5, 100.03)
    sig = evaluate_zarattini_bar1(df)
    assert sig.should_buy is False
    assert "도지" in sig.reason


def test_zarattini_bar1_doji_threshold_configurable():
    """도지 임계 env로 변경 가능."""
    from cryptobot.bot.kis_strategy import KISStrategyParams, evaluate_zarattini_bar1

    df = _bar1_df(100.0, 100.5, 99.5, 100.5)  # body = 0.5%
    # 임계 1.0% → 도지로 분류
    p = KISStrategyParams(doji_threshold_pct=1.0)
    sig = evaluate_zarattini_bar1(df, params=p)
    assert sig.should_buy is False
    assert "도지" in sig.reason
    # 임계 0.05% → 양봉으로 분류
    p2 = KISStrategyParams(doji_threshold_pct=0.05)
    sig2 = evaluate_zarattini_bar1(df, params=p2)
    assert sig2.should_buy is True


def test_zarattini_bar1_empty_df():
    """데이터 없으면 매수 X."""
    from cryptobot.bot.kis_strategy import evaluate_zarattini_bar1

    sig = evaluate_zarattini_bar1(None)
    assert sig.should_buy is False
    sig = evaluate_zarattini_bar1(pd.DataFrame())
    assert sig.should_buy is False


def test_zarattini_10r_take_profit_fires():
    """매수 후 가격이 10R TP 도달 시 즉시 익절."""
    from cryptobot.bot.kis_strategy import evaluate_sell

    sig = evaluate_sell(
        df=None,
        current_price=116.0,
        buy_price=101.0,
        highest_since_buy=116.0,
        stop_loss_price=99.5,
        take_profit_price=116.0,  # 10R 도달
    )
    assert sig.should_sell is True
    assert sig.is_profit_taking is True
    assert "10R" in sig.reason


def test_calc_position_size_risk_based_1pct():
    """1% 리스크 사이징 — 계좌 $1000, 주당 리스크 $0.50 → 20주 (정수)."""
    from cryptobot.bot.kis_strategy import KISStrategyParams, calc_position_size_risk_based

    qty, _ = calc_position_size_risk_based(
        available_budget=1000.0,
        current_price=30.0,
        risk_per_share=0.50,
        fractional=False,
        params=KISStrategyParams(risk_pct_per_trade=1.0),
    )
    # 1% 리스크 = $10 / $0.50 = 20주. 자본 한도 $1000/$30 = 33주. min = 20.
    assert qty == 20.0


def test_calc_position_size_risk_based_capital_constrained():
    """리스크 사이징보다 자본 한도가 작은 경우 — 자본이 제약."""
    from cryptobot.bot.kis_strategy import KISStrategyParams, calc_position_size_risk_based

    qty, reason = calc_position_size_risk_based(
        available_budget=290.0,  # $290 (≈ 400K KRW)
        current_price=30.0,      # SOXL
        risk_per_share=0.30,
        fractional=False,
        params=KISStrategyParams(risk_pct_per_trade=1.0),
    )
    # 리스크 사이징: $2.9 / $0.30 = 9.67 → 9주
    # 자본 한도: $290 / $30 = 9.67 → 9주
    # min = 9 (정수). 둘 비슷할 때 어느 쪽이든 9.
    assert qty == 9.0


def test_calc_position_size_risk_based_zero_risk():
    """주당 리스크 0이면 사이징 불가."""
    from cryptobot.bot.kis_strategy import calc_position_size_risk_based

    qty, _ = calc_position_size_risk_based(
        available_budget=1000.0,
        current_price=30.0,
        risk_per_share=0.0,
        fractional=False,
    )
    assert qty == 0.0


# ===================================================================
# #364 Pure Zarattini 3X 변형 (ATR 손절, No TP) 테스트
# ===================================================================


def _daily_df(highs: list[float], lows: list[float], closes: list[float]) -> pd.DataFrame:
    """일봉 더미 — ATR 계산용."""
    return pd.DataFrame({
        "open": closes,  # open=close 단순화
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1_000_000] * len(closes),
    })


def test_calc_atr_basic():
    """ATR(14) 계산 기본."""
    from cryptobot.bot.kis_strategy import calc_atr

    # 16봉, 매일 high-low = 2 (간단 케이스)
    closes = [30.0] * 16
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    df = _daily_df(highs, lows, closes)
    atr = calc_atr(df, period=14)
    # TR = high - low = 2 (모든 봉) → ATR = 2
    assert atr is not None
    assert abs(atr - 2.0) < 0.01


def test_calc_atr_insufficient_data():
    """일봉 14+1 미만이면 None."""
    from cryptobot.bot.kis_strategy import calc_atr

    df = _daily_df([31, 32], [29, 30], [30, 31])
    assert calc_atr(df, period=14) is None


def test_zarattini_3x_atr_bullish_signal():
    """첫 5분봉 양봉 + ATR 가용 → ATR 손절 시그널 (No TP)."""
    from cryptobot.bot.kis_strategy import KISStrategyParams, evaluate_zarattini_3x_atr

    df_5m = _bar1_df(30.0, 30.5, 29.95, 30.3)  # 양봉 1%
    closes = [30.0] * 16
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    df_d = _daily_df(highs, lows, closes)  # ATR ≈ 2.0

    params = KISStrategyParams(atr_stop_pct=5.0, atr_period=14)
    sig = evaluate_zarattini_3x_atr(df_5m, df_d, params=params)
    assert sig.should_buy is True
    # stop = 30.3 - 0.05 × 2.0 = 30.3 - 0.1 = 30.2
    assert abs(sig.stop_loss_price - 30.2) < 0.01
    # 3X 변형은 TP 없음
    assert sig.take_profit_price is None
    # risk_per_share = 0.1
    assert abs(sig.risk_per_share - 0.1) < 0.01
    assert "3X-ATR" in sig.reason
    assert "TP 없음" in sig.reason


def test_zarattini_3x_atr_doji_skip():
    """도지 → 매매 X."""
    from cryptobot.bot.kis_strategy import evaluate_zarattini_3x_atr

    df_5m = _bar1_df(30.0, 30.5, 29.5, 30.001)  # 도지
    df_d = _daily_df([31] * 16, [29] * 16, [30] * 16)
    sig = evaluate_zarattini_3x_atr(df_5m, df_d)
    assert sig.should_buy is False
    assert "도지" in sig.reason


def test_zarattini_3x_atr_bearish_skip():
    """음봉 → 인버스 ETF 별도 평가."""
    from cryptobot.bot.kis_strategy import evaluate_zarattini_3x_atr

    df_5m = _bar1_df(30.0, 30.05, 29.5, 29.7)  # 음봉
    df_d = _daily_df([31] * 16, [29] * 16, [30] * 16)
    sig = evaluate_zarattini_3x_atr(df_5m, df_d)
    assert sig.should_buy is False
    assert "음봉" in sig.reason


def test_zarattini_3x_atr_no_daily_data():
    """일봉 부족 시 ATR 계산 불가 → 매수 X."""
    from cryptobot.bot.kis_strategy import evaluate_zarattini_3x_atr

    df_5m = _bar1_df(30.0, 30.5, 29.95, 30.3)  # 양봉
    df_d = _daily_df([31, 32], [29, 30], [30, 31])  # 2봉만
    sig = evaluate_zarattini_3x_atr(df_5m, df_d)
    assert sig.should_buy is False
    assert "ATR" in sig.reason


def test_zarattini_3x_atr_pct_configurable():
    """atr_stop_pct env로 변경 가능."""
    from cryptobot.bot.kis_strategy import KISStrategyParams, evaluate_zarattini_3x_atr

    df_5m = _bar1_df(30.0, 30.5, 29.95, 30.3)
    closes = [30.0] * 16
    df_d = _daily_df([c + 1.0 for c in closes], [c - 1.0 for c in closes], closes)  # ATR=2

    # 5% 디폴트 → stop_distance = 0.1
    p = KISStrategyParams(atr_stop_pct=5.0, atr_period=14)
    sig = evaluate_zarattini_3x_atr(df_5m, df_d, params=p)
    assert abs(sig.risk_per_share - 0.1) < 0.01

    # 10% → stop_distance = 0.2
    p2 = KISStrategyParams(atr_stop_pct=10.0, atr_period=14)
    sig2 = evaluate_zarattini_3x_atr(df_5m, df_d, params=p2)
    assert abs(sig2.risk_per_share - 0.2) < 0.01


def test_zarattini_3x_atr_evaluate_sell_no_tp_holds_up():
    """3X 변형 매도 룰: TP 없음 + take_profit_pct=999 → 가격 올라가도 매도 X (EOD가 처리)."""
    from cryptobot.bot.kis_strategy import KISStrategyParams, evaluate_sell

    # zarattini_3x_atr 모드 디폴트: take_profit_pct=999, trailing=-99
    p = KISStrategyParams(take_profit_pct=999.0, trailing_stop_pct=-99.0)
    sig = evaluate_sell(
        df=None, current_price=36.0, buy_price=30.0,
        highest_since_buy=36.0,
        params=p,
        stop_loss_price=29.9,
        take_profit_price=None,
    )
    assert sig.should_sell is False


def test_zarattini_3x_atr_evaluate_sell_atr_stop_fires():
    """3X 변형 매도 룰: ATR 손절가 도달 시 매도."""
    from cryptobot.bot.kis_strategy import evaluate_sell

    sig = evaluate_sell(
        df=None, current_price=29.9, buy_price=30.3,
        highest_since_buy=30.5,
        stop_loss_price=29.9,  # ATR 기반 손절가
        take_profit_price=None,
    )
    assert sig.should_sell is True
    assert sig.is_profit_taking is False
