"""전략 저장소 테스트."""

import tempfile
from pathlib import Path

from cryptobot.data.database import Database
from cryptobot.data.strategy_repository import StrategyRepository


def _make_repo():
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    return StrategyRepository(db), db


def test_get_all_strategies():
    """초기화 시 11개 전략이 삽입되는지 (#226 long_term_swing 추가)."""
    repo, db = _make_repo()
    try:
        strategies = repo.get_all()
        assert len(strategies) == 11
        names = [s["name"] for s in strategies]
        assert "volatility_breakout" in names
        assert "rsi_mean_reversion" in names
        assert "long_term_swing" in names
    finally:
        db.close()


def test_get_by_name():
    """이름으로 전략 조회."""
    repo, db = _make_repo()
    try:
        s = repo.get_by_name("macd")
        assert s is not None
        assert s["display_name"] == "MACD"
        assert s["category"] == "trend"

        assert repo.get_by_name("nonexistent") is None
    finally:
        db.close()


def test_get_active_default():
    """초기 상태에서 bb_rsi_combined만 활성화 (#197 기본값 변경)."""
    repo, db = _make_repo()
    try:
        active = repo.get_active()
        assert len(active) == 1
        assert active[0]["name"] == "bb_rsi_combined"
    finally:
        db.close()


def test_activate_and_deactivate():
    """전략 활성화/비활성화 — 기본(bb_rsi_combined) + rsi_mean_reversion."""
    repo, db = _make_repo()
    try:
        # activate가 기존 활성을 shutting_down으로 옮기지만 is_active는 TRUE 유지
        repo.activate("rsi_mean_reversion", source="manual", reason="횡보장 대비")
        active = repo.get_active()
        # bb_rsi_combined(shutting_down, is_active=T) + rsi_mean_reversion(active) = 2
        assert len(active) == 2

        repo.deactivate("rsi_mean_reversion", source="manual")
        active = repo.get_active()
        # 기본 전략만 남음
        assert len(active) == 1
    finally:
        db.close()


def test_switch_strategy():
    """전략 전환 (#197 기본 bb_rsi_combined → rsi_mean_reversion)."""
    repo, db = _make_repo()
    try:
        repo.switch(
            from_strategy="bb_rsi_combined",
            to_strategy="rsi_mean_reversion",
            source="auto",
            market_state="sideways",
            reason="시장 횡보 전환",
        )

        active = repo.get_active()
        assert len(active) == 1
        assert active[0]["name"] == "rsi_mean_reversion"

        # 이력 확인
        history = repo.get_activation_history()
        assert len(history) >= 2  # deactivate + activate
    finally:
        db.close()


def test_activation_history():
    """활성화 이력 기록."""
    repo, db = _make_repo()
    try:
        repo.activate("macd", source="llm", reason="LLM 추천")
        repo.deactivate("macd", source="manual")

        history = repo.get_activation_history()
        assert len(history) >= 2
        assert history[0]["action"] == "deactivate"
        assert history[1]["action"] == "activate"
    finally:
        db.close()


def test_get_active_for_market():
    """시장 상태별 활성 전략 조회 (#197 기본값 bb_rsi_combined로 변경 반영)."""
    repo, db = _make_repo()
    try:
        # bullish용 전략 추가 활성화
        repo.activate("volatility_breakout")

        bullish = repo.get_active_for_market("bullish")
        assert any(s["name"] == "volatility_breakout" for s in bullish)

        # bb_rsi_combined은 sideways,bearish 기본 활성
        sideways = repo.get_active_for_market("sideways")
        assert any(s["name"] == "bb_rsi_combined" for s in sideways)
    finally:
        db.close()


def test_strategy_stats_empty():
    """매매 없을 때 통계."""
    repo, db = _make_repo()
    try:
        stats = repo.get_strategy_stats("volatility_breakout")
        assert stats["total_trades"] == 0
        assert stats["win_rate"] == 0
    finally:
        db.close()
