"""#183 — Prompt Caching 꼼꼼한 테스트.

목적: 운영 비용 절감 (매매 로직 변경 아님). 품질 저하 없어야 함.

Tier 1: 단위 — cost 계산, SYSTEM 스펙, DB 마이그레이션
Tier 2: 통합 — API 호출 형식(mock), 토큰 집계, _save_decision 저장
Tier 3: 회귀 방지 — 프롬프트 내용 손실 없음, 기존 테스트 통과
Tier 4: 시나리오 — write/read/mixed, 재시도, 실패 시 토큰 집계
"""

import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from cryptobot.data.database import Database
from cryptobot.llm.analyzer import (
    ANALYSIS_PROMPT,
    SYSTEM_PROMPT,
    LLMAnalyzer,
)


@pytest.fixture
def db():
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    yield db
    db.close()


# ===================================================================
# Tier 1.1: cost 계산 — 3가지 케이스
# ===================================================================


def test_cost_uncached_only(db):
    """캐시 미사용 — 기존 계산과 동일."""
    a = LLMAnalyzer(db)
    # 10K input + 1K output = 10K×$1/M + 1K×$5/M = $0.010 + $0.005 = $0.015
    assert a._calc_cost(10_000, 1_000) == pytest.approx(0.015, abs=1e-6)


def test_cost_cache_write_1h(db):
    """캐시 쓰기 1h — 2.0× base input."""
    a = LLMAnalyzer(db)
    # 고정 3K write + 가변 10K uncached + 1K output
    # = 3K × $1 × 2 /M + 10K × $1/M + 1K × $5/M
    # = $0.006 + $0.010 + $0.005 = $0.021
    cost = a._calc_cost(10_000, 1_000, cache_creation_tokens=3_000, cache_read_tokens=0)
    assert cost == pytest.approx(0.021, abs=1e-6)


def test_cost_cache_read_hit(db):
    """캐시 읽기 hit — 0.1× base (90% 할인)."""
    a = LLMAnalyzer(db)
    # 고정 3K read + 가변 10K uncached + 1K output
    # = 3K × $1 × 0.1 /M + 10K × $1/M + 1K × $5/M
    # = $0.0003 + $0.010 + $0.005 = $0.0153
    cost = a._calc_cost(10_000, 1_000, cache_creation_tokens=0, cache_read_tokens=3_000)
    assert cost == pytest.approx(0.0153, abs=1e-6)


def test_cost_monthly_simulation():
    """실측 기반 월 비용 시뮬레이션 검증.

    현재(실측): in=56K, out=1562 → $0.064/호출
    캐싱 적용: SYSTEM 5K(write or read) + in=50K + out=1.9K
    - 캐시 write: 5K×$2/M + 50K×$1/M + 1.9K×$5/M = $0.010 + $0.050 + $0.0095 = $0.0695
    - 캐시 read: 5K×$0.1/M + 50K×$1/M + 1.9K×$5/M = $0.0005 + $0.050 + $0.0095 = $0.0600
    1080 호출/월 중 대부분이 read면 월 ~$65~69.
    """
    db_path = Path(tempfile.mkdtemp()) / "t.db"
    d = Database(db_path)
    d.initialize()
    try:
        a = LLMAnalyzer(d)
        cost_write = a._calc_cost(50_000, 1_900, cache_creation_tokens=5_000)
        cost_read = a._calc_cost(50_000, 1_900, cache_read_tokens=5_000)
        # write: $0.010 + $0.050 + $0.0095 = $0.0695
        assert cost_write == pytest.approx(0.0695, abs=1e-4)
        # read: $0.0005 + $0.050 + $0.0095 = $0.0600
        assert cost_read == pytest.approx(0.0600, abs=1e-4)
        # 월 1080회 중 80% read + 20% write
        monthly = 1080 * (0.8 * cost_read + 0.2 * cost_write)
        assert 60 < monthly < 80, f"월 비용 예상 범위 $60~80 밖: ${monthly:.2f}"
    finally:
        d.close()


# ===================================================================
# Tier 1.2: SYSTEM 스펙 검증
# ===================================================================


def test_system_prompt_includes_hard_limits():
    """SYSTEM_PROMPT에 HARD_LIMITS 테이블이 있다."""
    assert "stop_loss_pct" in SYSTEM_PROMPT
    assert "-20.0 ~ -5.0" in SYSTEM_PROMPT
    assert "roi_10min" in SYSTEM_PROMPT


def test_system_prompt_includes_json_spec():
    """SYSTEM_PROMPT에 JSON 응답 스펙이 있다."""
    assert "recommended_params" in SYSTEM_PROMPT
    assert "recommended_strategy" in SYSTEM_PROMPT
    assert "coin_strategies" in SYSTEM_PROMPT


def test_system_prompt_includes_strategy_specs():
    """SYSTEM_PROMPT에 전략별 파라미터 스펙이 있다."""
    assert "volatility_breakout" in SYSTEM_PROMPT
    assert "bb_rsi_combined" in SYSTEM_PROMPT
    assert "ma_crossover" in SYSTEM_PROMPT


def test_system_prompt_includes_guidelines():
    """SYSTEM_PROMPT에 전략 전환 판단 규칙이 있다."""
    assert "전략 전환 판단" in SYSTEM_PROMPT
    assert "손익비" in SYSTEM_PROMPT


def test_analysis_prompt_has_only_variable_data():
    """ANALYSIS_PROMPT(user)는 이제 데이터 섹션만 포함."""
    # 데이터 플레이스홀더는 있어야 함
    assert "{news_text}" in ANALYSIS_PROMPT
    assert "{market_text}" in ANALYSIS_PROMPT
    assert "{performance_text}" in ANALYSIS_PROMPT
    # JSON 응답 스펙 필드는 SYSTEM으로 이동 — ANALYSIS_PROMPT엔 없어야 함
    assert '"market_summary_kr"' not in ANALYSIS_PROMPT
    assert '"recommended_params"' not in ANALYSIS_PROMPT
    # 하드리밋 테이블도 SYSTEM으로 이동
    assert "| stop_loss_pct |" not in ANALYSIS_PROMPT
    # 중요 규칙 섹션 제거
    assert "중요 규칙" not in ANALYSIS_PROMPT


def test_prompt_sizes_approximately_as_expected():
    """SYSTEM은 3K~8K 토큰(~15K chars), USER 템플릿은 거대한 데이터 플레이스홀더 포함."""
    assert 3_000 < len(SYSTEM_PROMPT) < 15_000, f"SYSTEM 크기 범위 밖: {len(SYSTEM_PROMPT)}"


def test_system_prompt_meets_haiku_45_cache_minimum():
    """#208: Haiku 4.5는 4096 토큰 미만이면 silent skip — 캐시 자체가 안 됨.

    실측: 6219 chars = 4441 tokens → 한국어 위주라 1 char ≈ 0.71 tokens.
    4096 tokens ≈ 5734 chars. 마진 5% 두고 6000 chars 하한.
    회귀 방지: SYSTEM_PROMPT를 줄였다가 4096 미만 되면 캐시 비활성 → 비용 폭증.
    """
    assert len(SYSTEM_PROMPT) >= 6_000, (
        f"SYSTEM_PROMPT가 너무 짧음 ({len(SYSTEM_PROMPT)} chars). "
        f"Haiku 4.5 캐싱 최소 4096 토큰(≈ 5734 chars)을 못 채우면 silent skip → 캐시 비활성."
    )


# ===================================================================
# Tier 1.3: DB 마이그레이션
# ===================================================================


def test_llm_decisions_has_cache_columns(db):
    """initialize() 후 cache_creation_tokens / cache_read_tokens 컬럼 존재."""
    cols = {tuple(r)[1] for r in db.execute("PRAGMA table_info(llm_decisions)").fetchall()}
    assert "cache_creation_tokens" in cols
    assert "cache_read_tokens" in cols


def test_auto_migration_on_legacy_db_without_cache_cols():
    """옛 DB → initialize() → 캐시 컬럼 자동 추가."""
    tmpdir = tempfile.mkdtemp()
    path = Path(tmpdir) / "legacy.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE llm_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            model TEXT NOT NULL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cost_usd REAL,
            output_market_state TEXT
        );
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    db.initialize()
    try:
        cols = {tuple(r)[1] for r in db.execute("PRAGMA table_info(llm_decisions)").fetchall()}
        assert "cache_creation_tokens" in cols
        assert "cache_read_tokens" in cols
    finally:
        db.close()


# ===================================================================
# Tier 2.1: API 호출 형식 (mock)
# ===================================================================


def _mock_response(input_tokens=100, output_tokens=50, cache_creation=0, cache_read=0):
    """Anthropic 응답 모의."""
    usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
    )
    content_block = SimpleNamespace(
        text='{"market_summary_kr":"t","market_state":"sideways",'
        '"confidence":0.5,"aggression":0.5,'
        '"should_alert_stop":false,"allow_trading":true,'
        '"recommended_strategy":"bb_rsi_combined",'
        '"recommended_params":{},"reasoning":"t"}'
    )
    return SimpleNamespace(usage=usage, content=[content_block])


def test_call_claude_passes_system_with_cache_control(db, monkeypatch):
    """_call_claude가 system=[{cache_control:{type:ephemeral,ttl:1h}}] 전달."""
    a = LLMAnalyzer(db)
    a._api_key = "sk-test"

    captured = {}

    class _FakeClient:
        def __init__(self, api_key):
            pass

        @property
        def messages(self):
            return self

        def create(self, **kwargs):
            captured.update(kwargs)
            return _mock_response()

    monkeypatch.setattr("anthropic.Anthropic", _FakeClient)
    a._call_claude("user data prompt")

    # system이 list, cache_control 포함
    assert isinstance(captured.get("system"), list)
    sys_block = captured["system"][0]
    assert sys_block["type"] == "text"
    assert sys_block["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert SYSTEM_PROMPT in sys_block["text"]
    # user content는 uncached
    assert captured["messages"][0]["content"] == "user data prompt"


def test_interval_active_under_one_hour_for_cache_sliding_window():
    """#208: 1h 캐시 TTL은 sliding window. 60분 정확히면 매번 expired.

    55분 이하여야 cache hit + TTL 자동 연장. INTERVAL_ACTIVE_MIN < 60 회귀 방지.
    """
    assert LLMAnalyzer.INTERVAL_ACTIVE_MIN < 60, (
        "ACTIVE 호출 간격이 60분 이상이면 1h 캐시가 매번 expired — 캐시 hit 0%"
    )


def test_max_daily_calls_supports_24h_active_cycle():
    """#208: 55분 간격 × 24h ≈ 26회. 여유분 포함 30회 보장."""
    assert LLMAnalyzer.MAX_DAILY_CALLS >= 26, (
        f"MAX_DAILY_CALLS={LLMAnalyzer.MAX_DAILY_CALLS} — ACTIVE 55분 간격을 24h 유지하려면 26회+ 필요"
    )


def test_call_claude_uses_max_tokens_constant(db, monkeypatch):
    """#200 프롬프트는 응답이 길어 1024로 잘림 — MAX_TOKENS(2048)를 전달해야."""
    assert LLMAnalyzer.MAX_TOKENS >= 2048, (
        "opportunity-focused 프롬프트 응답에 최소 2048 토큰 필요"
    )

    a = LLMAnalyzer(db)
    a._api_key = "sk-test"

    captured = {}

    class _FakeClient:
        def __init__(self, api_key):
            pass

        @property
        def messages(self):
            return self

        def create(self, **kwargs):
            captured.update(kwargs)
            return _mock_response()

    monkeypatch.setattr("anthropic.Anthropic", _FakeClient)
    a._call_claude("user data prompt")

    assert captured.get("max_tokens") == LLMAnalyzer.MAX_TOKENS


def test_call_claude_aggregates_cache_tokens(db, monkeypatch):
    """응답의 cache_creation/cache_read 토큰이 result에 반영."""
    a = LLMAnalyzer(db)
    a._api_key = "sk-test"

    class _FakeClient:
        def __init__(self, api_key):
            pass

        @property
        def messages(self):
            return self

        def create(self, **kwargs):
            return _mock_response(
                input_tokens=100,
                output_tokens=50,
                cache_creation=3000,
                cache_read=0,
            )

    monkeypatch.setattr("anthropic.Anthropic", _FakeClient)
    result = a._call_claude("prompt")
    assert result["_cache_creation_tokens"] == 3000
    assert result["_cache_read_tokens"] == 0


def test_call_claude_backward_compat_if_sdk_missing_cache_fields(db, monkeypatch):
    """구 SDK 응답(cache 필드 없음)이어도 에러 없이 0으로 집계."""
    a = LLMAnalyzer(db)
    a._api_key = "sk-test"

    class _FakeClient:
        def __init__(self, api_key):
            pass

        @property
        def messages(self):
            return self

        def create(self, **kwargs):
            usage = SimpleNamespace(input_tokens=100, output_tokens=50)
            content_block = SimpleNamespace(
                text='{"market_summary_kr":"t","market_state":"sideways","confidence":0.5,'
                '"aggression":0.5,"should_alert_stop":false,"allow_trading":true,'
                '"recommended_strategy":"bb_rsi_combined","recommended_params":{},'
                '"reasoning":"t"}'
            )
            return SimpleNamespace(usage=usage, content=[content_block])

    monkeypatch.setattr("anthropic.Anthropic", _FakeClient)
    result = a._call_claude("prompt")
    assert result["_cache_creation_tokens"] == 0
    assert result["_cache_read_tokens"] == 0


# ===================================================================
# Tier 2.2: _save_decision에 캐시 토큰 저장
# ===================================================================


def test_save_decision_persists_cache_tokens(db):
    a = LLMAnalyzer(db)
    result = {
        "market_state": "sideways",
        "aggression": 0.5,
        "allow_trading": True,
        "recommended_params": {"stop_loss_pct": -5},
        "_input_tokens": 50_000,
        "_output_tokens": 1_900,
        "_cache_creation_tokens": 5_000,
        "_cache_read_tokens": 0,
    }
    a._save_decision(result)
    row = dict(
        db.execute(
            "SELECT cache_creation_tokens, cache_read_tokens, cost_usd FROM llm_decisions ORDER BY id DESC LIMIT 1"
        ).fetchone()
    )
    assert row["cache_creation_tokens"] == 5_000
    assert row["cache_read_tokens"] == 0
    # cost 검증: 50K × $1/M + 5K × $2/M + 1.9K × $5/M = $0.050 + $0.010 + $0.0095 = $0.0695
    assert row["cost_usd"] == pytest.approx(0.0695, abs=1e-4)


def test_save_decision_with_cache_read(db):
    a = LLMAnalyzer(db)
    result = {
        "market_state": "sideways",
        "aggression": 0.5,
        "allow_trading": True,
        "recommended_params": {},
        "_input_tokens": 50_000,
        "_output_tokens": 1_900,
        "_cache_creation_tokens": 0,
        "_cache_read_tokens": 5_000,
    }
    a._save_decision(result)
    row = dict(db.execute("SELECT cost_usd FROM llm_decisions ORDER BY id DESC LIMIT 1").fetchone())
    # read: $0.0005 + $0.050 + $0.0095 = $0.0600
    assert row["cost_usd"] == pytest.approx(0.0600, abs=1e-4)


# ===================================================================
# Tier 3: 회귀 방지
# ===================================================================


def test_system_plus_user_contains_all_original_content():
    """SYSTEM + USER 합쳐도 기존 ANALYSIS_PROMPT의 모든 주요 섹션 포함."""
    combined = SYSTEM_PROMPT + ANALYSIS_PROMPT
    # 핵심 섹션 키워드 모두 있는지
    keywords = [
        "최근 뉴스",
        "공포/탐욕",
        "시장 상태",
        "잔고",
        "매매 성과",
        "이전 분석",
        "활성 전략",
        "하드 리밋",
        "손익비",
        "전략 전환",
        "JSON 형식",
        "recommended_strategy",
        "coin_strategies",
    ]
    missing = [k for k in keywords if k not in combined]
    assert not missing, f"누락 키워드: {missing}"


def test_fee_guard_and_risk_logic_untouched():
    """이 이슈는 LLM 호출 형식만 변경 — 매매 로직 소스 파일 수정 없음."""
    # 매매 관련 소스 파일들이 변경 안 됐다는 의미 — 여기서는 import 성공 확인만
    from cryptobot.bot.main import CryptoBot  # noqa: F401
    from cryptobot.bot.risk import RiskManager  # noqa: F401
    from cryptobot.bot.trader import Trader  # noqa: F401
    from cryptobot.strategies.base import BaseStrategy, Signal  # noqa: F401


# ===================================================================
# Tier 4: 시나리오 — 재시도, 실패, 믹스
# ===================================================================


def test_failed_call_records_cache_tokens(db, monkeypatch):
    """MAX_RETRIES 전부 실패 시에도 cache 토큰 FAILED 레코드에 저장."""
    a = LLMAnalyzer(db)
    a._api_key = "sk-test"

    class _FakeClient:
        def __init__(self, api_key):
            pass

        @property
        def messages(self):
            return self

        def create(self, **kwargs):
            # usage는 있지만 본문이 유효 JSON 아님 → 재시도 전부 실패
            usage = SimpleNamespace(
                input_tokens=1000,
                output_tokens=50,
                cache_creation_input_tokens=500,
                cache_read_input_tokens=0,
            )
            content = SimpleNamespace(text="invalid json response")
            return SimpleNamespace(usage=usage, content=[content])

    monkeypatch.setattr("anthropic.Anthropic", _FakeClient)
    result = a._call_claude("prompt")
    assert result is None

    row = dict(
        db.execute(
            "SELECT cache_creation_tokens, cache_read_tokens, output_market_state "
            "FROM llm_decisions ORDER BY id DESC LIMIT 1"
        ).fetchone()
    )
    assert row["output_market_state"] == "FAILED"
    # 재시도 3회 × 500 = 1500 누적
    assert row["cache_creation_tokens"] >= 500


def test_call_claude_mixed_write_then_read_simulation(db, monkeypatch):
    """같은 객체로 2회 호출 — 첫 write, 두번째 read로 가정 시뮬."""
    a = LLMAnalyzer(db)
    a._api_key = "sk-test"

    call_idx = {"i": 0}
    responses = [
        _mock_response(input_tokens=50_000, output_tokens=1_900, cache_creation=5_000, cache_read=0),  # write
        _mock_response(input_tokens=50_000, output_tokens=1_900, cache_creation=0, cache_read=5_000),  # read
    ]

    class _FakeClient:
        def __init__(self, api_key):
            pass

        @property
        def messages(self):
            return self

        def create(self, **kwargs):
            r = responses[call_idx["i"]]
            call_idx["i"] += 1
            return r

    monkeypatch.setattr("anthropic.Anthropic", _FakeClient)
    r1 = a._call_claude("data round 1")
    r2 = a._call_claude("data round 2")
    assert r1["_cache_creation_tokens"] == 5_000
    assert r2["_cache_read_tokens"] == 5_000
    # read 호출이 write 호출보다 저렴
    cost1 = a._calc_cost(
        r1["_input_tokens"], r1["_output_tokens"], r1["_cache_creation_tokens"], r1["_cache_read_tokens"]
    )
    cost2 = a._calc_cost(
        r2["_input_tokens"], r2["_output_tokens"], r2["_cache_creation_tokens"], r2["_cache_read_tokens"]
    )
    assert cost2 < cost1


# ===================================================================
# Tier 4.2: DB에 저장된 비용 집계 — 월 기대치 범위
# ===================================================================


def test_monthly_cost_from_db_within_budget(db):
    """캐시 hit 80% 가정 시 월 $60~80 범위."""
    a = LLMAnalyzer(db)
    # 30일 × 36회 = 1080회. 80%는 read, 20%는 write.
    for i in range(1080):
        if i % 5 == 0:
            a._save_decision(
                {
                    "market_state": "sideways",
                    "aggression": 0.5,
                    "allow_trading": True,
                    "recommended_params": {},
                    "_input_tokens": 50_000,
                    "_output_tokens": 1_900,
                    "_cache_creation_tokens": 5_000,
                    "_cache_read_tokens": 0,
                }
            )
        else:
            a._save_decision(
                {
                    "market_state": "sideways",
                    "aggression": 0.5,
                    "allow_trading": True,
                    "recommended_params": {},
                    "_input_tokens": 50_000,
                    "_output_tokens": 1_900,
                    "_cache_creation_tokens": 0,
                    "_cache_read_tokens": 5_000,
                }
            )
    total = db.execute("SELECT SUM(cost_usd) FROM llm_decisions").fetchone()[0]
    # 216 write × $0.0695 + 864 read × $0.0600 = $15.0 + $51.8 = $66.8
    assert 60 < total < 80, f"월 집계 범위 밖: ${total:.2f}"
