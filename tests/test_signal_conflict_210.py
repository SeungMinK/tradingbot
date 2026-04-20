"""#210: 손절 vs 매수 신호 충돌 통합 의사결정 테스트.

ALGO 사례: 20:39 손절 -5.65% → 1분 뒤 RSI 매수 신호로 재매수.
같은 가격 사건에 대한 모순적 결정. 사용자 표현: "더 높은 애 거로 들어야지".

가드:
- 손절 시 매수 신호 confidence ≥ 임계 (기본 0.7) → 손절 보류 (들고 간다)
- pnl ≤ hard_stop_loss_floor_pct (-10%) → 매수 신호 무관 무조건 손절 (안전장치)
- ROI/트레일링(is_profit_taking=True) → 충돌 가드 미적용 (기존 로직)
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from cryptobot.bot.risk import RiskLimits
from cryptobot.data.database import Database
from cryptobot.data.recorder import DataRecorder
from cryptobot.strategies.base import Signal


@pytest.fixture
def db():
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    yield db
    db.close()


def _make_bot(db, sell_sig: Signal, buy_sig: Signal, conf_threshold: float = 0.7, hard_floor: float = -10.0):
    """Bot mock with risk limits and strategy mock."""
    from cryptobot.bot.main import CryptoBot

    bot = CryptoBot.__new__(CryptoBot)
    bot._db = db
    bot._recorder = DataRecorder(db)
    bot._notifier = MagicMock()
    bot._notifier.is_configured = True
    bot._trader = MagicMock()
    bot._trader.is_ready = True
    bot._config_mgr = MagicMock()
    bot._config_mgr.get_strategy_params_json.return_value = None

    bot._risk = MagicMock()
    bot._risk.limits = MagicMock(
        signal_conflict_buy_confidence_threshold=conf_threshold,
        hard_stop_loss_floor_pct=hard_floor,
    )

    strat = MagicMock()
    strat.check_sell.return_value = sell_sig
    strat.check_buy.return_value = buy_sig
    strat.params = MagicMock(stop_loss_pct=-5, trailing_stop_pct=-2, extra={})
    strat._hold_minutes = 10
    bot._strategy_sel = MagicMock()
    bot._strategy_sel.current_strategy = strat
    bot._strategy_sel.current_strategy_name = "test"
    coll = MagicMock()
    coll.latest_df = pd.DataFrame({"close": [100] * 30})
    bot._coin_mgr = MagicMock(collectors={"KRW-BTC": coll})

    return bot, strat


def _seed_buy(db, price: float = 100.0):
    """선행 매수 레코드. orphan 가드 회피."""
    rec = DataRecorder(db)
    return rec.record_trade(
        coin="KRW-BTC", side="buy", price=price, amount=1, total_krw=int(price * 100),
        fee_krw=5, strategy="test", trigger_reason="seed",
    )


# ===================================================================
# 기본값
# ===================================================================


def test_risk_limits_default_threshold_and_floor():
    limits = RiskLimits()
    assert limits.signal_conflict_buy_confidence_threshold == 0.7
    assert limits.hard_stop_loss_floor_pct == -10.0


# ===================================================================
# 핵심: 충돌 보류 vs 손절 진행
# ===================================================================


def test_strong_buy_signal_holds_stop_loss(db):
    """손절 신호 + 강한 매수 신호(0.8) → 손절 보류."""
    buy_id = _seed_buy(db, price=100)
    sell_sig = Signal("sell", 1.0, "손절", is_profit_taking=False)
    buy_sig = Signal("buy", 0.8, "RSI 13 극과매도")

    bot, _ = _make_bot(db, sell_sig, buy_sig)
    active_trade = {"id": buy_id, "price": 100.0, "total_krw": 10000, "fee_krw": 5,
                    "timestamp": "2026-04-19T11:00:00+00:00"}
    # 가격 95 → -5% 손절 트리거
    bot._check_and_sell(active_trade, price=95.0, snapshot_id=None, coin="KRW-BTC")

    # 매도 안 일어남 (보류)
    bot._trader.sell_market.assert_not_called()
    # Slack 알림 발송
    assert bot._notifier.send.called
    msg = bot._notifier.send.call_args[0][0]
    assert "손절-매수 충돌" in msg
    # trade_signals에 hold + skip_reason 기록
    row = db.execute("SELECT signal_type, skip_reason FROM trade_signals ORDER BY id DESC LIMIT 1").fetchone()
    d = dict(row)
    assert d["signal_type"] == "hold"
    assert "보유 유지" in d["skip_reason"]


def test_weak_buy_signal_executes_stop_loss(db):
    """손절 신호 + 약한 매수(0.5 < 임계 0.7) → 손절 진행."""
    from cryptobot.bot.trader import OrderResult
    buy_id = _seed_buy(db, price=100)
    sell_sig = Signal("sell", 1.0, "손절", is_profit_taking=False)
    buy_sig = Signal("buy", 0.5, "약한 매수")

    bot, _ = _make_bot(db, sell_sig, buy_sig)
    bot._trader.sell_market.return_value = OrderResult(
        success=True, side="sell", coin="KRW-BTC",
        price=95, amount=1, total_krw=9500, fee_krw=5, order_uuid="u1",
    )
    active_trade = {"id": buy_id, "price": 100.0, "total_krw": 10000, "fee_krw": 5,
                    "timestamp": "2026-04-19T11:00:00+00:00"}
    bot._check_and_sell(active_trade, price=95.0, snapshot_id=None, coin="KRW-BTC")

    # 매도 진행
    bot._trader.sell_market.assert_called_once_with("KRW-BTC")


def test_no_buy_signal_executes_stop_loss(db):
    """손절 + check_buy가 hold 반환 → 손절 진행."""
    from cryptobot.bot.trader import OrderResult
    buy_id = _seed_buy(db, price=100)
    sell_sig = Signal("sell", 1.0, "손절", is_profit_taking=False)
    buy_sig = Signal("hold", 0.0, "매수 신호 없음")

    bot, _ = _make_bot(db, sell_sig, buy_sig)
    bot._trader.sell_market.return_value = OrderResult(
        success=True, side="sell", coin="KRW-BTC",
        price=95, amount=1, total_krw=9500, fee_krw=5, order_uuid="u1",
    )
    active_trade = {"id": buy_id, "price": 100.0, "total_krw": 10000, "fee_krw": 5,
                    "timestamp": "2026-04-19T11:00:00+00:00"}
    bot._check_and_sell(active_trade, price=95.0, snapshot_id=None, coin="KRW-BTC")

    bot._trader.sell_market.assert_called_once_with("KRW-BTC")


# ===================================================================
# 안전장치: 하드 플로어
# ===================================================================


def test_hard_floor_overrides_buy_signal(db):
    """pnl ≤ -10% (하드 플로어) → 강한 매수 신호 무시, 무조건 손절."""
    from cryptobot.bot.trader import OrderResult
    buy_id = _seed_buy(db, price=100)
    sell_sig = Signal("sell", 1.0, "손절", is_profit_taking=False)
    buy_sig = Signal("buy", 0.95, "초강한 매수")  # 임계 훨씬 위

    bot, _ = _make_bot(db, sell_sig, buy_sig, hard_floor=-10.0)
    bot._trader.sell_market.return_value = OrderResult(
        success=True, side="sell", coin="KRW-BTC",
        price=88, amount=1, total_krw=8800, fee_krw=5, order_uuid="u1",
    )
    active_trade = {"id": buy_id, "price": 100.0, "total_krw": 10000, "fee_krw": 5,
                    "timestamp": "2026-04-19T11:00:00+00:00"}
    # 가격 88 → -12% (하드 플로어 -10% 초과)
    bot._check_and_sell(active_trade, price=88.0, snapshot_id=None, coin="KRW-BTC")

    # 매수 신호 강해도 무조건 손절
    bot._trader.sell_market.assert_called_once_with("KRW-BTC")


def test_just_above_hard_floor_with_strong_buy_holds(db):
    """pnl=-9.5% (하드 플로어 -10% 안쪽) + 강한 매수 → 보류 가능."""
    buy_id = _seed_buy(db, price=100)
    sell_sig = Signal("sell", 1.0, "손절", is_profit_taking=False)
    buy_sig = Signal("buy", 0.85, "강한 매수")

    bot, _ = _make_bot(db, sell_sig, buy_sig, hard_floor=-10.0)
    active_trade = {"id": buy_id, "price": 100.0, "total_krw": 10000, "fee_krw": 5,
                    "timestamp": "2026-04-19T11:00:00+00:00"}
    bot._check_and_sell(active_trade, price=90.5, snapshot_id=None, coin="KRW-BTC")  # -9.5%

    bot._trader.sell_market.assert_not_called()


# ===================================================================
# ROI/트레일링은 충돌 가드 미적용
# ===================================================================


def test_roi_sell_not_affected_by_buy_signal(db):
    """is_profit_taking=True인 ROI 매도는 충돌 가드 미적용."""
    from cryptobot.bot.trader import OrderResult
    buy_id = _seed_buy(db, price=100)
    sell_sig = Signal("sell", 0.9, "ROI 도달 (60분 보유, 실질 +1.5%)", is_profit_taking=True)
    buy_sig = Signal("buy", 0.95, "강한 매수 (영향 없어야)")

    bot, _ = _make_bot(db, sell_sig, buy_sig)
    bot._trader.sell_market.return_value = OrderResult(
        success=True, side="sell", coin="KRW-BTC",
        price=102, amount=1, total_krw=10200, fee_krw=5, order_uuid="u1",
    )
    active_trade = {"id": buy_id, "price": 100.0, "total_krw": 10000, "fee_krw": 5,
                    "timestamp": "2026-04-19T11:00:00+00:00"}
    bot._check_and_sell(active_trade, price=102.0, snapshot_id=None, coin="KRW-BTC")

    # ROI는 그대로 진행
    bot._trader.sell_market.assert_called_once_with("KRW-BTC")
    # 충돌 알림은 발송 안 됨
    if bot._notifier.send.called:
        for call in bot._notifier.send.call_args_list:
            assert "충돌" not in call[0][0]


# ===================================================================
# 임계값 조정
# ===================================================================


def test_lower_threshold_holds_more_aggressively(db):
    """임계 0.5로 낮추면 더 자주 보류."""
    buy_id = _seed_buy(db, price=100)
    sell_sig = Signal("sell", 1.0, "손절", is_profit_taking=False)
    buy_sig = Signal("buy", 0.5, "중간 강도")  # 0.7이면 미달, 0.5면 정확히 통과

    bot, _ = _make_bot(db, sell_sig, buy_sig, conf_threshold=0.5)
    active_trade = {"id": buy_id, "price": 100.0, "total_krw": 10000, "fee_krw": 5,
                    "timestamp": "2026-04-19T11:00:00+00:00"}
    bot._check_and_sell(active_trade, price=95.0, snapshot_id=None, coin="KRW-BTC")

    bot._trader.sell_market.assert_not_called()
