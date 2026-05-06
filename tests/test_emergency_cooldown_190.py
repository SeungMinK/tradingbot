"""#190 — Emergency 과호출 방지 테스트.

실측: 4/13 47회/일 — check_emergency가 10분마다 True 반환해 동적 간격 무력화.

수정:
- _should_run(force=True)도 EMERGENCY_MIN_COOLDOWN_MIN(20분) 쿨다운 적용
- MAX_DAILY_CALLS 36 → 20 하향
"""

import tempfile
from pathlib import Path

import pytest

from cryptobot.data.database import Database
from cryptobot.llm.analyzer import LLMAnalyzer


@pytest.fixture
def db():
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    # #230: llm_enabled가 기본 false라 _should_run이 즉시 차단 → 테스트는 활성 가정
    db.execute("UPDATE bot_config SET value='true' WHERE key='llm_enabled'")
    db.commit()
    yield db
    db.close()


def _insert_decision(db, minutes_ago: int) -> None:
    """N분 전 LLM 호출 기록 삽입."""
    db.execute(
        "INSERT INTO llm_decisions (timestamp, model) VALUES (datetime('now', ?), 'test')",
        (f"-{minutes_ago} minutes",),
    )
    db.commit()


# ===================================================================
# 1. Emergency 쿨다운 (force=True도 너무 잦으면 스킵)
# ===================================================================


def test_force_skipped_within_cooldown(db):
    """force=True라도 최근 호출 후 20분 미만이면 스킵."""
    _insert_decision(db, minutes_ago=10)  # 10분 전 호출
    a = LLMAnalyzer(db)
    assert a._should_run(force=True) is False


def test_force_allowed_after_cooldown(db):
    """force=True + 20분 경과 → 실행."""
    _insert_decision(db, minutes_ago=25)  # 25분 전 호출
    a = LLMAnalyzer(db)
    assert a._should_run(force=True) is True


def test_force_allowed_when_no_prior_calls(db):
    """첫 호출은 force=True 즉시 실행 (이전 기록 없음)."""
    a = LLMAnalyzer(db)
    assert a._should_run(force=True) is True


def test_force_at_exact_cooldown_boundary(db):
    """정확히 EMERGENCY_MIN_COOLDOWN_MIN(20분) 경과 시 실행."""
    _insert_decision(db, minutes_ago=21)  # 약간의 margin
    a = LLMAnalyzer(db)
    assert a._should_run(force=True) is True


# ===================================================================
# 2. 일반 호출(force=False)은 기존 동적 간격 유지
# ===================================================================


def test_non_force_respects_interval(db):
    """force=False이면 기존 동적 간격(QUIET=240분 등) 유지."""
    _insert_decision(db, minutes_ago=30)  # 30분 전
    a = LLMAnalyzer(db)
    # 매매/포지션 0 → QUIET(240분) → 30분 미만이므로 스킵
    assert a._should_run(force=False) is False


def test_non_force_runs_after_interval(db):
    """QUIET 간격(240분) 경과 후 일반 호출 실행."""
    _insert_decision(db, minutes_ago=250)
    a = LLMAnalyzer(db)
    assert a._should_run(force=False) is True


# ===================================================================
# 3. MAX_DAILY_CALLS — #208에서 20 → 30 상향 (캐시 hit으로 비용 영향 미미)
# ===================================================================


def test_max_daily_calls_is_30(db):
    """#208: 캐시 hit 보장으로 30회까지 안전."""
    a = LLMAnalyzer(db)
    assert a.MAX_DAILY_CALLS == 30


def test_daily_limit_blocks_even_force(db):
    """MAX_DAILY_CALLS 도달 시 force=True도 차단."""
    a = LLMAnalyzer(db)
    # 오늘 한도+0건 삽입
    for _ in range(a.MAX_DAILY_CALLS):
        db.execute("INSERT INTO llm_decisions (timestamp, model) VALUES (datetime('now', '-5 minutes'), 'test')")
    db.commit()

    # force=True여도 일일 한도 초과라 스킵
    assert a._should_run(force=True) is False


def test_under_daily_limit_allows_call(db):
    """한도 직전이면 force=True 한 번 더 허용."""
    a = LLMAnalyzer(db)
    for _ in range(a.MAX_DAILY_CALLS - 1):
        db.execute("INSERT INTO llm_decisions (timestamp, model) VALUES (datetime('now', '-30 minutes'), 'test')")
    db.commit()

    # 20분 쿨다운 통과 + 한도 미만 → True
    assert a._should_run(force=True) is True


# ===================================================================
# 4. EMERGENCY_MIN_COOLDOWN_MIN 상수 존재 확인
# ===================================================================


def test_emergency_cooldown_constant():
    assert LLMAnalyzer.EMERGENCY_MIN_COOLDOWN_MIN == 20
