"""LLM 전략 전환 통합 테스트.

DB에서 is_available=TRUE 전략을 동적으로 가져와 테스트하므로
새 전략 추가 시 자동으로 커버됨.
"""

import json
import tempfile
from pathlib import Path

from cryptobot.data.database import Database
from cryptobot.data.strategy_repository import StrategyRepository
from cryptobot.llm.analyzer import COMMON_PARAM_KEYS, HARD_LIMITS, LLMAnalyzer


def _make_analyzer():
    """테스트용 LLMAnalyzer + Database 생성."""
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    return LLMAnalyzer(db), db


def _get_available_strategies(db):
    """DB에서 available 전략 목록 동적 조회."""
    rows = db.execute("SELECT name, default_params_json FROM strategies WHERE is_available = TRUE").fetchall()
    return [dict(r) for r in rows]


def _build_llm_result(strategy_name, extra_params=None):
    """LLM 응답 dict 생성."""
    result = {
        "market_summary_kr": "테스트 시장 요약",
        "market_state": "sideways",
        "confidence": 0.7,
        "aggression": 0.5,
        "should_alert_stop": False,
        "allow_trading": True,
        "recommended_strategy": strategy_name,
        "recommended_params": {
            "stop_loss_pct": -7.0,
            "trailing_stop_pct": -3.0,
            "max_position_per_coin_pct": 50,
            "roi_10min": 3.5,
            "roi_120min": 2.5,
            "roi_240min": 1.8,
            "roi_600min": 1.0,
            **(extra_params or {}),
        },
        "reasoning": "테스트 전략 전환 근거",
    }
    return result


class TestSwitchToEachStrategy:
    """test_switch_to_each_strategy — DB의 모든 available 전략 루프, 전환 + 활성화 검증."""

    def test_switch_to_each_strategy(self):
        analyzer, db = _make_analyzer()
        try:
            strategies = _get_available_strategies(db)
            assert len(strategies) > 0, "available 전략이 0개"

            repo = StrategyRepository(db)

            for strat in strategies:
                name = strat["name"]
                # 전략별 기본 파라미터를 extra_params로 전달
                default_params = json.loads(strat["default_params_json"]) if strat["default_params_json"] else {}
                result = _build_llm_result(name, extra_params=default_params)

                # shutting_down 전략이 있으면 정리
                repo.complete_shutdown()

                analyzer._apply_recommendations(result)

                # 활성화 검증 (shutting_down은 아직 is_active=TRUE이므로 status='active'로 필터)
                row = db.execute(
                    "SELECT name, is_active, status FROM strategies WHERE is_active = TRUE AND status = 'active'"
                ).fetchone()
                assert row is not None, f"전략 {name} 활성화 후 active 전략 없음"
                assert dict(row)["name"] == name, f"활성 전략 불일치: {dict(row)['name']} != {name}"
                assert dict(row)["status"] == "active"
        finally:
            db.close()


class TestStrategyParamsMerged:
    """test_strategy_params_merged_correctly — 전략별 파라미터 머지, COMMON_PARAM_KEYS 분리."""

    def test_strategy_params_merged_correctly(self):
        analyzer, db = _make_analyzer()
        try:
            strategies = _get_available_strategies(db)
            repo = StrategyRepository(db)

            for strat in strategies:
                name = strat["name"]
                default_params = json.loads(strat["default_params_json"]) if strat["default_params_json"] else {}
                result = _build_llm_result(name, extra_params=default_params)

                repo.complete_shutdown()
                analyzer._apply_recommendations(result)

                # DB에서 전략 파라미터 확인
                row = db.execute("SELECT default_params_json FROM strategies WHERE name = ?", (name,)).fetchone()
                saved_params = json.loads(dict(row)["default_params_json"])

                # 전략별 파라미터(공통 키 제외)가 저장되었는지 확인
                for key, value in default_params.items():
                    if key not in COMMON_PARAM_KEYS:
                        assert key in saved_params, f"전략 {name}: 파라미터 {key} 누락"
        finally:
            db.close()


class TestMissingParamsDefense:
    """test_missing_strategy_params_defense — 필수 필드 누락 시 _fill_param_defaults() 방어."""

    def test_missing_strategy_params_defense(self):
        analyzer, db = _make_analyzer()
        try:
            # 빈 파라미터로 _fill_param_defaults 호출
            filled = analyzer._fill_param_defaults({})

            # bot_config에서 기본값이 채워져야 함
            # initialize()에서 stop_loss_pct 등을 bot_config에 넣으므로 값이 존재해야 함
            config_keys_in_db = ["stop_loss_pct", "trailing_stop_pct", "k_value", "max_position_per_coin_pct"]
            for key in config_keys_in_db:
                row = db.execute("SELECT value FROM bot_config WHERE key = ?", (key,)).fetchone()
                if row:
                    assert key in filled, f"_fill_param_defaults가 {key}를 채우지 않음"
        finally:
            db.close()


class TestHardLimitsClipping:
    """test_hard_limits_clipping — 범위 밖 파라미터 클리핑."""

    def test_hard_limits_clipping(self):
        analyzer, db = _make_analyzer()
        try:
            # 범위 밖 값으로 결과 생성
            result = _build_llm_result(
                "volatility_breakout",
                extra_params={
                    "k_value": 99.0,  # 범위: 0.2~0.8
                    "stop_loss_pct": -100.0,  # 범위: -20.0~-5.0
                    "rsi_oversold": 99,  # 범위: 20~45
                },
            )
            result["aggression"] = 5.0  # 범위: 0.1~1.0

            clipped = analyzer._apply_hard_limits(result)
            params = clipped["recommended_params"]

            assert params["k_value"] == HARD_LIMITS["k_value"][1], f"k_value 클리핑 실패: {params['k_value']}"
            assert params["stop_loss_pct"] == HARD_LIMITS["stop_loss_pct"][0], (
                f"stop_loss_pct 클리핑 실패: {params['stop_loss_pct']}"
            )
            assert params["rsi_oversold"] == HARD_LIMITS["rsi_oversold"][1], (
                f"rsi_oversold 클리핑 실패: {params['rsi_oversold']}"
            )
            assert clipped["aggression"] == HARD_LIMITS["aggression"][1], (
                f"aggression 클리핑 실패: {clipped['aggression']}"
            )
        finally:
            db.close()


class TestBeforeAfterSnapshot:
    """test_before_after_snapshot — llm_decisions에 before/after 기록."""

    def test_before_after_snapshot(self):
        analyzer, db = _make_analyzer()
        try:
            # 먼저 save_decision으로 레코드 생성
            result = _build_llm_result("volatility_breakout", extra_params={"k_value": 0.5})
            result["_input_tokens"] = 100
            result["_output_tokens"] = 50
            result["_model"] = "test-model"
            result["_prompt_version_id"] = None

            analyzer._save_decision(result)
            analyzer._apply_recommendations(result)

            # llm_decisions의 input_news_summary에 before/after JSON이 기록되었는지
            row = db.execute("SELECT input_news_summary FROM llm_decisions ORDER BY id DESC LIMIT 1").fetchone()
            assert row is not None, "llm_decisions 레코드 없음"

            snapshot = json.loads(dict(row)["input_news_summary"])
            assert "before" in snapshot, "before 스냅샷 없음"
            assert "after" in snapshot, "after 스냅샷 없음"
            assert "strategy" in snapshot, "strategy 필드 없음"
        finally:
            db.close()


class TestCommonParamsNotInStrategy:
    """test_common_params_not_in_strategy — 공통 파라미터가 strategy params에 안 들어가는지."""

    def test_common_params_not_in_strategy(self):
        analyzer, db = _make_analyzer()
        try:
            result = _build_llm_result(
                "bb_rsi_combined",
                extra_params={
                    "bb_std": 1.5,
                    "rsi_oversold": 35,
                    "bb_period": 20,
                    "rsi_period": 14,
                },
            )

            repo = StrategyRepository(db)
            repo.complete_shutdown()
            analyzer._apply_recommendations(result)

            # 전략 파라미터에서 공통 키가 없어야 함
            row = db.execute("SELECT default_params_json FROM strategies WHERE name = 'bb_rsi_combined'").fetchone()
            saved_params = json.loads(dict(row)["default_params_json"])

            for common_key in COMMON_PARAM_KEYS:
                assert common_key not in saved_params, (
                    f"공통 파라미터 {common_key}가 전략 params에 들어감: {saved_params}"
                )
        finally:
            db.close()


class TestUnknownStrategyRejected:
    """test_unknown_strategy_rejected — DB에 없는 전략 graceful 처리."""

    def test_unknown_strategy_rejected(self):
        analyzer, db = _make_analyzer()
        try:
            result = _build_llm_result("nonexistent_strategy_xyz")
            result["_input_tokens"] = 100
            result["_output_tokens"] = 50
            result["_model"] = "test-model"
            result["_prompt_version_id"] = None

            analyzer._save_decision(result)

            # apply_recommendations는 StrategyRepository.activate()에서 False 반환 → 예외 없이 처리
            analyzer._apply_recommendations(result)

            # 활성 전략이 변경되지 않아야 함 (기존 전략 유지)
            active = db.execute("SELECT name FROM strategies WHERE is_active = TRUE AND status = 'active'").fetchone()
            if active:
                assert dict(active)["name"] != "nonexistent_strategy_xyz"
        finally:
            db.close()


class TestGetStrategiesTextDynamic:
    """test_get_strategies_text_dynamic — 프롬프트 전략 목록 동적 생성 (DB 전략 수 일치)."""

    def test_get_strategies_text_dynamic(self):
        analyzer, db = _make_analyzer()
        try:
            strategies = _get_available_strategies(db)
            text = analyzer._get_strategies_text()

            # 각 전략 이름이 텍스트에 포함되어야 함
            for strat in strategies:
                assert strat["name"] in text, f"전략 {strat['name']}이 프롬프트 텍스트에 없음"

            # "###" 헤더 수가 전략 수와 일치
            header_count = text.count("### ")
            assert header_count == len(strategies), (
                f"프롬프트 전략 수 불일치: 헤더 {header_count}개 vs DB {len(strategies)}개"
            )
        finally:
            db.close()


class TestGetActiveStrategyText:
    """test_get_active_strategy_text — 활성 전략 텍스트 출력."""

    def test_get_active_strategy_text(self):
        analyzer, db = _make_analyzer()
        try:
            # #197: 기본 활성 전략은 이제 bb_rsi_combined
            text = analyzer._get_active_strategy_text()
            assert "bb_rsi_combined" in text, f"활성 전략 텍스트에 전략 이름 없음: {text}"
            assert "적합 시장" in text, f"적합 시장 정보 없음: {text}"

            # 다른 전략으로 전환 후 확인 (volatility_breakout으로)
            repo = StrategyRepository(db)
            repo.activate("volatility_breakout", source="test")
            repo.complete_shutdown()

            text = analyzer._get_active_strategy_text()
            assert "volatility_breakout" in text, f"전환 후 텍스트에 volatility_breakout 없음: {text}"
        finally:
            db.close()
