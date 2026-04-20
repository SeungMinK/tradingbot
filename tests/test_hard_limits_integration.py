"""HARD_LIMITS end-to-end 통합 테스트.

LLM이 범위 밖 값을 반환해도 최종 DB/전략 인스턴스에 안전한 값만 반영되는지
3가지 경로 모두 검증:

1. recommended_params → _apply_hard_limits → bot_config / strategies.default_params_json
2. coin_strategies → CoinStrategyRepository.assign (clip) → coin_strategy_assignment
3. bot_config 읽을 때 _get_config_float → HARD_LIMITS 범위 체크
"""

import json
import tempfile
from pathlib import Path

import pytest

from cryptobot.data.database import Database
from cryptobot.llm.analyzer import HARD_LIMITS, LLMAnalyzer


@pytest.fixture
def db():
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    yield db
    db.close()


def _seed_active_strategy(db, name: str = "bb_rsi_combined") -> None:
    db.execute("UPDATE strategies SET is_active = 0")
    db.execute("UPDATE strategies SET is_active = 1 WHERE name = ?", (name,))
    db.execute("INSERT INTO llm_decisions (timestamp, model) VALUES (datetime('now'), 'test')")
    # _apply_recommendations가 UPDATE만 하므로 사전 INSERT 필요
    for k, v in (
        ("stop_loss_pct", "-5.0"),
        ("trailing_stop_pct", "-3.0"),
        ("max_position_per_coin_pct", "50"),
        ("k_value", "0.5"),
        ("allow_trading", "true"),
    ):
        db.execute(
            "INSERT OR IGNORE INTO bot_config (key, value, value_type, category, display_name) "
            "VALUES (?, ?, 'float', 'strategy', ?)",
            (k, v, k),
        )
    db.commit()


# ===================================================================
# 1. recommended_params → bot_config end-to-end
# ===================================================================


def test_bot_config_never_gets_out_of_range_value(db):
    """LLM이 범위 밖 값 → _apply_hard_limits → bot_config에 클리핑된 값만 저장."""
    _seed_active_strategy(db)
    analyzer = LLMAnalyzer(db)

    # LLM이 범위 밖 값 반환 시뮬레이션
    result = {
        "market_summary_kr": "t",
        "market_state": "sideways",
        "confidence": 0.5,
        "aggression": 5.0,  # HARD_LIMITS (0.1, 1.0) → 1.0
        "allow_trading": True,
        "recommended_strategy": "bb_rsi_combined",
        "recommended_params": {
            "stop_loss_pct": -50.0,  # HARD_LIMITS (-20, -5) → -20
            "trailing_stop_pct": -30.0,  # (-10, -1) → -10
            "max_position_per_coin_pct": 200,  # (30, 80) → 80
            "roi_240min": 100.0,  # (0.8, 3.0) → 3.0
        },
        "reasoning": "t",
    }

    # 클리핑 적용
    analyzer._apply_hard_limits(result)
    # 실제 적용
    analyzer._apply_recommendations(result)

    # bot_config 확인 — 범위 밖 값이 DB에 들어가면 안 됨
    rows = db.execute(
        "SELECT key, value FROM bot_config "
        "WHERE key IN ('stop_loss_pct', 'trailing_stop_pct', 'max_position_per_coin_pct')"
    ).fetchall()
    cfg = {dict(r)["key"]: float(dict(r)["value"]) for r in rows}
    assert cfg["stop_loss_pct"] == -20.0, f"stop_loss_pct 범위 밖: {cfg['stop_loss_pct']}"
    assert cfg["trailing_stop_pct"] == -10.0
    assert cfg["max_position_per_coin_pct"] == 80


def test_roi_table_from_out_of_range_input_clipped(db):
    """LLM이 roi_* 범위 밖 값 반환 → clipped 후 roi_table에 저장."""
    _seed_active_strategy(db)
    analyzer = LLMAnalyzer(db)

    result = {
        "market_summary_kr": "t",
        "market_state": "sideways",
        "confidence": 0.5,
        "aggression": 0.5,
        "allow_trading": True,
        "recommended_strategy": "bb_rsi_combined",
        "recommended_params": {
            # #224 시간 구간 확장 후 HARD_LIMITS 상한 clipped 검증
            "roi_10min": 999.0,  # (1.5, 6.0) → 6.0
            "roi_120min": 999.0,  # (1.0, 4.0) → 4.0
            "roi_240min": 999.0,  # (0.8, 3.0) → 3.0
            "roi_600min": 999.0,  # (0.5, 2.0) → 2.0
        },
        "reasoning": "t",
    }
    analyzer._apply_hard_limits(result)
    analyzer._apply_recommendations(result)

    row = db.execute("SELECT value FROM bot_config WHERE key = 'roi_table'").fetchone()
    table = json.loads(dict(row)["value"])
    assert table["10"] == 6.0
    assert table["120"] == 4.0
    assert table["240"] == 3.0
    assert table["600"] == 2.0


# ===================================================================
# 2. coin_strategies → coin_strategy_assignment end-to-end
# ===================================================================


def test_coin_strategies_params_clipped_in_db(db):
    """coin_strategies에 범위 밖 값 → assignment 저장 시 클리핑."""
    _seed_active_strategy(db)
    analyzer = LLMAnalyzer(db)

    # 모니터링 코인 세팅 (market_snapshot 필요 — #186 active_coins 필터)
    db.execute("INSERT INTO market_snapshots (coin, timestamp, price) VALUES ('KRW-BTC', datetime('now'), 100)")
    db.commit()

    result = {
        "market_summary_kr": "t",
        "market_state": "sideways",
        "confidence": 0.5,
        "aggression": 0.5,
        "allow_trading": True,
        "recommended_strategy": "bb_rsi_combined",
        "recommended_params": {},
        "coin_strategies": {
            "KRW-BTC": {
                "strategy": "bb_rsi_combined",
                "params": {"rsi_oversold": 999, "bb_std": 99.0},  # 범위 밖
            }
        },
        "reasoning": "t",
    }
    analyzer._apply_recommendations(result)

    # coin_strategy_assignment DB 확인
    row = db.execute("SELECT params_json FROM coin_strategy_assignment WHERE coin = 'KRW-BTC'").fetchone()
    stored = json.loads(dict(row)["params_json"])
    assert stored["rsi_oversold"] == 45, f"클리핑 안 됨: {stored}"
    assert stored["bb_std"] == 2.5


def test_coin_strategies_valid_params_unchanged(db):
    """범위 내 값은 그대로 저장."""
    _seed_active_strategy(db)
    db.execute("INSERT INTO market_snapshots (coin, timestamp, price) VALUES ('KRW-ETH', datetime('now'), 100)")
    db.commit()
    analyzer = LLMAnalyzer(db)

    result = {
        "market_summary_kr": "t",
        "market_state": "sideways",
        "confidence": 0.5,
        "aggression": 0.5,
        "allow_trading": True,
        "recommended_strategy": "bb_rsi_combined",
        "recommended_params": {},
        "coin_strategies": {
            "KRW-ETH": {
                "strategy": "bb_rsi_combined",
                "params": {"rsi_oversold": 30, "bb_std": 2.0},  # 범위 내
            }
        },
        "reasoning": "t",
    }
    analyzer._apply_recommendations(result)

    row = db.execute("SELECT params_json FROM coin_strategy_assignment WHERE coin='KRW-ETH'").fetchone()
    stored = json.loads(dict(row)["params_json"])
    assert stored["rsi_oversold"] == 30
    assert stored["bb_std"] == 2.0


# ===================================================================
# 3. _get_config_float의 HARD_LIMITS 범위 검증
# ===================================================================


def test_config_float_rejects_out_of_range_in_db(db):
    """DB에 이미 저장된 범위 밖 값도 조회 시 default로 폴백."""
    analyzer = LLMAnalyzer(db)
    # 이상한 값 직접 저장
    db.execute(
        "INSERT INTO bot_config (key, value, value_type, category, display_name) "
        "VALUES ('emergency_held_pct', '0.01', 'float', 'strategy', 'emergency')"
    )
    db.commit()

    # 0.01은 HARD_LIMITS (1.0, 10.0) 밖 → default 3.0 반환
    value = analyzer._get_config_float("emergency_held_pct", 3.0)
    assert value == 3.0


# ===================================================================
# 4. HARD_LIMITS 정의 자체 정합성
# ===================================================================


def test_hard_limits_all_values_are_sane():
    """HARD_LIMITS의 모든 키가 (min, max) 형태 + min < max + 숫자."""
    for key, val in HARD_LIMITS.items():
        assert isinstance(val, tuple), f"{key}: tuple 아님 {val}"
        assert len(val) == 2, f"{key}: len != 2"
        mn, mx = val
        assert isinstance(mn, (int, float)) and isinstance(mx, (int, float))
        assert mn < mx, f"{key}: min ≥ max ({mn} ≥ {mx})"


def test_hard_limits_covers_all_llm_adjustable_params():
    """LLM이 조절 가능한 공통 파라미터가 모두 HARD_LIMITS에 있음."""
    required = {
        "stop_loss_pct",
        "trailing_stop_pct",
        "max_position_per_coin_pct",
        "roi_10min",
        "roi_120min",
        "roi_240min",
        "roi_600min",
        "max_spread_pct",
        "emergency_held_pct",
        "emergency_non_held_pct",
        "aggression",
        "k_value",
        "bb_std",
        "rsi_oversold",
    }
    missing = required - set(HARD_LIMITS.keys())
    assert not missing, f"HARD_LIMITS 누락: {missing}"


# ===================================================================
# 5. Emergency 쿨다운 + HARD_LIMITS 상호작용 확인 (회귀)
# ===================================================================


def test_emergency_cooldown_does_not_bypass_hard_limits(db):
    """Emergency로 force=True 호출되어도 HARD_LIMITS 클리핑은 유지."""
    _seed_active_strategy(db)
    analyzer = LLMAnalyzer(db)

    result = {
        "market_summary_kr": "t",
        "market_state": "sideways",
        "confidence": 0.5,
        "aggression": 999.0,  # 범위 밖
        "allow_trading": True,
        "recommended_strategy": "bb_rsi_combined",
        "recommended_params": {"stop_loss_pct": -999.0},  # 범위 밖
        "reasoning": "t",
    }
    # Emergency 경로도 analyze 내부에서 _apply_hard_limits 호출
    clipped = analyzer._apply_hard_limits(result)
    assert clipped["aggression"] == 1.0  # (0.1, 1.0) 상한
    assert clipped["recommended_params"]["stop_loss_pct"] == -20.0  # (-20, -5) 하한
