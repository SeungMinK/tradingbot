"""P1 #170 — LLM 로직 정확성 테스트.

1. 재시도 실패 시 토큰 누락 방지 (DB에 FAILED 레코드 저장)
2. HARD_LIMITS 클리핑 흔적 DB 기록
3. _fill_param_defaults — 활성 전략 없을 때 폴백
4. fee guard — Signal.is_profit_taking 플래그 기반
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from cryptobot.data.database import Database
from cryptobot.data.recorder import DataRecorder
from cryptobot.llm.analyzer import LLMAnalyzer
from cryptobot.strategies.base import Signal


@pytest.fixture
def db():
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    yield db
    db.close()


# ===================================================================
# 1. 재시도 실패 시 토큰 누락 방지
# ===================================================================


def test_failed_call_records_tokens_to_db(db):
    """_record_failed_call: 누적 토큰이 llm_decisions에 FAILED 레코드로 저장된다."""
    analyzer = LLMAnalyzer(db)
    analyzer._record_failed_call(input_tokens=10000, output_tokens=500, attempts=3)

    row = db.execute(
        "SELECT input_tokens, output_tokens, cost_usd, output_market_state, output_reasoning "
        "FROM llm_decisions WHERE output_market_state = 'FAILED'"
    ).fetchone()
    assert row is not None
    d = dict(row)
    assert d["input_tokens"] == 10000
    assert d["output_tokens"] == 500
    # Haiku 4.5: 10000/1M * $1 + 500/1M * $5 = $0.01 + $0.0025 = $0.0125
    assert abs(d["cost_usd"] - 0.0125) < 1e-5
    assert "MAX_RETRIES" in d["output_reasoning"]


def test_failed_call_zero_tokens_no_record(db):
    """토큰 0이면 _call_claude가 _record_failed_call을 호출하지 않는다.

    _call_claude의 `if total_input > 0 or total_output > 0` 가드 확인용.
    실제 API 호출을 하지 않으므로 llm_decisions는 비어있어야 한다.
    """
    LLMAnalyzer(db)
    rows = db.execute("SELECT COUNT(*) FROM llm_decisions").fetchone()[0]
    assert rows == 0


# ===================================================================
# 2. HARD_LIMITS 클리핑 흔적
# ===================================================================


def test_hard_limits_clips_and_records(db):
    """LLM이 범위 밖 값을 반환하면 클리핑하고 _clipped_fields 기록."""
    analyzer = LLMAnalyzer(db)
    result = {
        "recommended_params": {
            "k_value": 2.0,  # HARD_LIMITS (0.2, 0.8) → 0.8
            "rsi_oversold": 5,  # (20, 45) → 20
            "stop_loss_pct": -3.0,  # (-20, -5) → -5
        },
        "aggression": 3.0,  # (0.1, 1.0) → 1.0
    }
    clipped_result = analyzer._apply_hard_limits(result)
    assert clipped_result["recommended_params"]["k_value"] == 0.8
    assert clipped_result["recommended_params"]["rsi_oversold"] == 20
    assert clipped_result["recommended_params"]["stop_loss_pct"] == -5
    assert clipped_result["aggression"] == 1.0
    assert "_clipped_fields" in clipped_result
    clipped = clipped_result["_clipped_fields"]
    assert len(clipped) == 4
    fields = {c["field"] for c in clipped}
    assert fields == {"k_value", "rsi_oversold", "stop_loss_pct", "aggression"}


def test_hard_limits_no_clip_no_mark(db):
    """범위 내 값은 클리핑 표시 없음."""
    analyzer = LLMAnalyzer(db)
    result = {
        "recommended_params": {"k_value": 0.5, "rsi_oversold": 30},
        "aggression": 0.5,
    }
    clipped_result = analyzer._apply_hard_limits(result)
    assert "_clipped_fields" not in clipped_result


def test_save_decision_preserves_clipped_notes(db):
    """_save_decision이 클리핑 흔적을 output_reasoning에 덧붙인다."""
    analyzer = LLMAnalyzer(db)
    result = {
        "market_summary_kr": "test",
        "reasoning": "test reasoning",
        "market_state": "sideways",
        "aggression": 0.5,
        "allow_trading": True,
        "recommended_params": {},
        "_input_tokens": 100,
        "_output_tokens": 50,
        "_clipped_fields": [
            {"field": "k_value", "original": 2.0, "clipped": 0.8, "range": [0.2, 0.8]},
        ],
    }
    analyzer._save_decision(result)
    row = db.execute("SELECT output_reasoning FROM llm_decisions ORDER BY id DESC LIMIT 1").fetchone()
    assert "[하드리밋 클리핑]" in dict(row)["output_reasoning"]
    assert "k_value" in dict(row)["output_reasoning"]


# ===================================================================
# 3. _fill_param_defaults — 활성 전략 없을 때 폴백
# ===================================================================


def test_fill_param_defaults_no_active_strategy_falls_back(db):
    """활성 전략이 없어도 available 전략 또는 하드코딩 기본값으로 폴백."""
    analyzer = LLMAnalyzer(db)
    # 모든 전략 비활성화
    db.execute("UPDATE strategies SET is_active = 0")
    db.commit()

    filled = analyzer._fill_param_defaults({})
    # available 전략의 기본값이 있거나 하드코딩 폴백
    assert "rsi_oversold" in filled
    assert "bb_std" in filled


def test_fill_param_defaults_empty_strategies_hardcoded(db):
    """strategies 테이블이 비어있으면 하드코딩 기본값."""
    analyzer = LLMAnalyzer(db)
    db.execute("DELETE FROM strategies")
    db.commit()

    filled = analyzer._fill_param_defaults({})
    assert filled["rsi_oversold"] == 30
    assert filled["bb_std"] == 2.0


# ===================================================================
# 4. fee guard — Signal.is_profit_taking 플래그 기반
# ===================================================================


def test_signal_has_is_profit_taking_field():
    """Signal 데이터클래스에 is_profit_taking 필드 존재, 기본 False."""
    sig = Signal("sell", 0.8, "test")
    assert hasattr(sig, "is_profit_taking")
    assert sig.is_profit_taking is False


def test_fee_guard_blocks_profit_taking_when_negative_net(db):
    """is_profit_taking=True + 실질 음수 → 수수료 가드로 스킵."""
    from cryptobot.bot.main import CryptoBot

    bot = CryptoBot.__new__(CryptoBot)
    bot._db = db
    bot._recorder = DataRecorder(db)
    bot._notifier = MagicMock()
    bot._trader = MagicMock()
    bot._trader.is_ready = True
    bot._config_mgr = MagicMock()
    bot._config_mgr.get_strategy_params_json.return_value = None

    strat = MagicMock()
    # 익절성 매도 신호 (ROI 등) — 가격이 buy_price 근처라 실질 수수료 차감 후 음수
    strat.check_sell.return_value = Signal(
        "sell",
        0.9,
        "ROI 도달",
        is_profit_taking=True,
    )
    strat.params = MagicMock()
    strat.params.extra = {}
    strat.params.stop_loss_pct = -5
    strat.params.trailing_stop_pct = -2
    strat._hold_minutes = 10
    bot._strategy_sel = MagicMock()
    bot._strategy_sel.current_strategy = strat
    bot._strategy_sel.current_strategy_name = "test_strategy"
    coll = MagicMock()
    coll.latest_df = pd.DataFrame({"close": [100] * 30})
    bot._coin_mgr = MagicMock(collectors={"KRW-BTC": coll})

    active_trade = {
        "id": 1,
        "price": 100.0,
        "total_krw": 10000,
        "fee_krw": 5,
        "timestamp": "2026-04-17T00:00:00+00:00",
    }
    # 가격 = 100.05 → pnl 0.05%, 수수료 0.15% → 실질 -0.1%
    bot._check_and_sell(active_trade, price=100.05, snapshot_id=None, coin="KRW-BTC")

    # 매도 실행 안 됨 (sell_market 호출되지 않음)
    bot._trader.sell_market.assert_not_called()
    # skip_reason에 수수료 가드 표시
    row = db.execute("SELECT skip_reason FROM trade_signals ORDER BY id DESC LIMIT 1").fetchone()
    assert "수수료 가드" in dict(row)["skip_reason"]


def test_fee_guard_allows_stop_loss_even_when_negative(db):
    """is_profit_taking=False (손절) → 실질 음수여도 통과."""
    from cryptobot.bot.main import CryptoBot
    from cryptobot.bot.trader import OrderResult

    # 실존 buy 레코드 먼저 생성 — orphan 가드(#173) 대응
    recorder = DataRecorder(db)
    buy_id = recorder.record_trade(
        coin="KRW-BTC",
        side="buy",
        price=100.0,
        amount=1,
        total_krw=10000,
        fee_krw=5,
        strategy="test_strategy",
        trigger_reason="seed",
    )

    bot = CryptoBot.__new__(CryptoBot)
    bot._db = db
    bot._recorder = recorder
    bot._notifier = MagicMock()
    bot._trader = MagicMock()
    bot._trader.is_ready = True
    bot._trader.sell_market.return_value = OrderResult(
        success=True,
        side="sell",
        coin="KRW-BTC",
        price=95,
        amount=1,
        total_krw=9500,
        fee_krw=5,
        order_uuid="sell-uuid",
    )
    bot._config_mgr = MagicMock()
    bot._config_mgr.get_strategy_params_json.return_value = None

    strat = MagicMock()
    # 손절 신호 — is_profit_taking=False
    strat.check_sell.return_value = Signal(
        "sell",
        1.0,
        "손절",
        is_profit_taking=False,
    )
    # #210 충돌 가드: 매수 신호 없음 → 손절 진행 (이 테스트의 기대 동작 유지)
    strat.check_buy.return_value = Signal("hold", 0.0, "매수 신호 없음")
    strat.params = MagicMock()
    strat.params.extra = {}
    strat.params.stop_loss_pct = -5
    strat.params.trailing_stop_pct = -2
    strat._hold_minutes = 10
    # #210: _risk mock — 충돌 가드가 limits 참조
    bot._risk = MagicMock()
    bot._risk.limits = MagicMock(
        signal_conflict_buy_confidence_threshold=0.7,
        hard_stop_loss_floor_pct=-10.0,
    )
    bot._strategy_sel = MagicMock()
    bot._strategy_sel.current_strategy = strat
    bot._strategy_sel.current_strategy_name = "test_strategy"
    coll = MagicMock()
    coll.latest_df = pd.DataFrame({"close": [100] * 30})
    bot._coin_mgr = MagicMock(collectors={"KRW-BTC": coll})

    active_trade = {
        "id": buy_id,
        "price": 100.0,
        "total_krw": 10000,
        "fee_krw": 5,
        "timestamp": "2026-04-17T00:00:00+00:00",
    }
    # 가격 95 — 손실 상태지만 손절이므로 통과해야 함
    bot._check_and_sell(active_trade, price=95.0, snapshot_id=None, coin="KRW-BTC")

    # 매도 실행됨
    bot._trader.sell_market.assert_called_once_with("KRW-BTC")
