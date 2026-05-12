"""#382: bb_rsi_combined swing 활성 시 통합 smoke test.

- 전략 selector가 bb_rsi_combined을 로드할 수 있는가
- DB default_params_json이 strategy 인스턴스에 반영되는가
- min_profit_for_trailing 5.0 디폴트가 작동하는가
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from cryptobot.bot.strategy_selector import STRATEGY_CLASSES
from cryptobot.data.database import Database
from cryptobot.strategies.base import StrategyParams
from cryptobot.strategies.bb_rsi_combined import BBRSICombined


@pytest.fixture
def db_with_active_bb_rsi():
    """bb_rsi_combined을 활성으로 시드한 임시 DB."""
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    # PR4 마이그레이션 적용
    db.execute("UPDATE strategies SET is_active = 0")
    db.execute(
        "UPDATE strategies SET is_active = 1, default_params_json = ? WHERE name = 'bb_rsi_combined'",
        (json.dumps({
            "bb_std": 1.5,
            "bb_period": 20,
            "rsi_period": 14,
            "rsi_oversold": 25,
            "rsi_overbought": 50,
            "min_profit_for_trailing": 5.0,
        }),),
    )
    db.commit()
    yield db
    db.close()


def test_bb_rsi_combined_in_strategy_classes():
    """레지스트리에 bb_rsi_combined 등록 확인."""
    assert "bb_rsi_combined" in STRATEGY_CLASSES
    assert STRATEGY_CLASSES["bb_rsi_combined"] is BBRSICombined


def test_db_active_strategy_is_bb_rsi(db_with_active_bb_rsi):
    """DB 활성 전략 = bb_rsi_combined."""
    row = db_with_active_bb_rsi.execute(
        "SELECT name FROM strategies WHERE is_active = 1"
    ).fetchone()
    assert dict(row)["name"] == "bb_rsi_combined"


def test_default_params_load_into_strategy(db_with_active_bb_rsi):
    """DB default_params_json이 BBRSICombined 인스턴스에 반영."""
    row = db_with_active_bb_rsi.execute(
        "SELECT default_params_json FROM strategies WHERE name = 'bb_rsi_combined'"
    ).fetchone()
    params_dict = json.loads(dict(row)["default_params_json"])
    s = BBRSICombined(StrategyParams(stop_loss_pct=-5.0, trailing_stop_pct=-2.5, extra=params_dict))
    assert s._bb_std == 1.5
    assert s._rsi_oversold == 25
    assert s._min_profit_for_trailing == 5.0


def test_swing_mode_holds_at_4pct_profit():
    """+4% 수익 (5% 가드 미달) → hold (swing 모드 핵심 동작)."""
    s = BBRSICombined(StrategyParams(
        stop_loss_pct=-5.0,
        trailing_stop_pct=-2.5,
        extra={"bb_std": 1.5, "rsi_oversold": 25, "min_profit_for_trailing": 5.0},
    ))
    # 평탄한 가격 → BB std=0이라 영향 없음. RSI ~50 정도.
    df = pd.DataFrame({
        "open": [100.0] * 25, "high": [100.5] * 25, "low": [99.5] * 25,
        "close": [100.0] * 25, "volume": [1000] * 25,
    })
    # +4% gross, net 3.9%, 5% 가드 미달
    sig = s.check_sell(df, current_price=104.0, buy_price=100.0)
    assert sig.signal_type == "hold"
    assert "가드" in sig.reason


def test_swing_mode_fires_trailing_at_6pct_profit_after_peak():
    """피크 +10% → 피크-3% drop, net 6.9% > 5% 가드 → trailing 매도."""
    s = BBRSICombined(StrategyParams(
        stop_loss_pct=-5.0,
        trailing_stop_pct=-2.5,
        extra={"bb_std": 1.5, "rsi_oversold": 25, "min_profit_for_trailing": 5.0},
    ))
    df = pd.DataFrame({
        "open": [100.0] * 25, "high": [100.5] * 25, "low": [99.5] * 25,
        "close": [100.0] * 25, "volume": [1000] * 25,
    })
    s.check_sell(df, current_price=110.0, buy_price=100.0)  # 피크 갱신
    sig = s.check_sell(df, current_price=106.5, buy_price=100.0)  # -3.18% from peak, +6.5% gross
    assert sig.signal_type == "sell"


def test_stop_loss_5pct_fires_unconditionally():
    """-5% 손절은 가드 무관 작동."""
    s = BBRSICombined(StrategyParams(
        stop_loss_pct=-5.0,
        trailing_stop_pct=-2.5,
        extra={"min_profit_for_trailing": 5.0},
    ))
    df = pd.DataFrame({
        "open": [100.0] * 25, "high": [100.5] * 25, "low": [99.5] * 25,
        "close": [100.0] * 25, "volume": [1000] * 25,
    })
    sig = s.check_sell(df, current_price=94.5, buy_price=100.0)  # -5.5%
    assert sig.signal_type == "sell"
    assert "손절" in sig.reason
    assert sig.is_profit_taking is False


def test_backtest_filter_config_seeded(db_with_active_bb_rsi):
    """bot_config에 백테스트 필터 시드 (이미 있으면 그대로)."""
    # initialize() 시 자동 시드되거나, 마이그레이션이 추가
    rows = db_with_active_bb_rsi.execute(
        "SELECT key, value FROM bot_config WHERE key LIKE 'coin_backtest%'"
    ).fetchall()
    keys = {dict(r)["key"] for r in rows}
    # 시드 키 3개 모두 존재
    assert "coin_backtest_filter_enabled" in keys
    assert "coin_backtest_min_avg_profit" in keys
    assert "coin_backtest_min_trades" in keys


def test_backtest_filter_enabled_after_migration_386(db_with_active_bb_rsi):
    """#386 마이그레이션 적용으로 coin_backtest_filter_enabled = true."""
    row = db_with_active_bb_rsi.execute(
        "SELECT value FROM bot_config WHERE key = 'coin_backtest_filter_enabled'"
    ).fetchone()
    assert dict(row)["value"] == "true"
