"""#195 — 4시간 주기 헬스체크 테스트."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cryptobot.bot.health_checker import HealthChecker
from cryptobot.data.database import Database
from cryptobot.data.recorder import DataRecorder


@pytest.fixture
def db():
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    yield db
    db.close()


# ===================================================================
# 1. 각 개별 체크
# ===================================================================


def test_check_bot_liveness_no_snapshots(db):
    """snapshot 없으면 warning."""
    checker = HealthChecker(db)
    result = checker._check_bot_liveness()
    assert result["status"] == "warning"


def test_check_bot_liveness_recent_snapshot(db):
    """최근 2분 전 snapshot → ok."""
    db.execute(
        "INSERT INTO market_snapshots (coin, timestamp, price) VALUES ('KRW-BTC', datetime('now', '-2 minutes'), 100)"
    )
    db.commit()
    checker = HealthChecker(db)
    result = checker._check_bot_liveness()
    assert result["status"] == "ok"
    assert result["last_snapshot_min_ago"] < 5


def test_check_bot_liveness_stale_snapshot(db):
    """10분 전 snapshot → warning (5분 초과)."""
    db.execute(
        "INSERT INTO market_snapshots (coin, timestamp, price) VALUES ('KRW-BTC', datetime('now', '-10 minutes'), 100)"
    )
    db.commit()
    checker = HealthChecker(db)
    result = checker._check_bot_liveness()
    assert result["status"] == "warning"


def test_check_news_liveness_empty(db):
    """2시간 내 뉴스 0건 → warning."""
    checker = HealthChecker(db)
    result = checker._check_news_liveness()
    assert result["status"] == "warning"
    assert result["news_count_2h"] == 0


def test_check_news_liveness_with_recent(db):
    db.execute(
        "INSERT INTO news_articles (source, title, collected_at) "
        "VALUES ('test', 'news1', datetime('now', '-30 minutes'))"
    )
    db.commit()
    checker = HealthChecker(db)
    result = checker._check_news_liveness()
    assert result["status"] == "ok"
    assert result["news_count_2h"] == 1


def test_check_recent_signals_empty(db):
    checker = HealthChecker(db)
    result = checker._check_recent_signals()
    assert result["status"] == "warning"
    assert result["total"] == 0


def test_check_recent_signals_with_data(db):
    rec = DataRecorder(db)
    rec.record_signal(
        coin="KRW-BTC",
        signal_type="buy",
        strategy="test",
        confidence=0.8,
        trigger_reason="test",
        current_price=100,
    )
    rec.record_signal(
        coin="KRW-BTC",
        signal_type="hold",
        strategy="test",
        confidence=0.0,
        trigger_reason="test",
        current_price=100,
    )
    checker = HealthChecker(db)
    result = checker._check_recent_signals()
    assert result["status"] == "ok"
    assert result["total"] == 2


def test_check_llm_today_counts_and_cost(db):
    # 오늘 날짜로 2건 (KST datetime(now) + 9h = today)
    db.execute(
        "INSERT INTO llm_decisions (timestamp, model, cost_usd, "
        "cache_creation_tokens, cache_read_tokens) "
        "VALUES (datetime('now'), 'test', 0.05, 1000, 0)"
    )
    db.execute(
        "INSERT INTO llm_decisions (timestamp, model, cost_usd, "
        "cache_creation_tokens, cache_read_tokens) "
        "VALUES (datetime('now'), 'test', 0.03, 0, 4000)"
    )
    db.commit()
    checker = HealthChecker(db)
    result = checker._check_llm_today_kst()
    assert result["status"] == "ok"
    assert result["calls_today"] == 2
    assert result["cost_usd"] == pytest.approx(0.08, abs=1e-4)
    # 캐시 hit rate: 4000 / (1000 + 4000) = 80%
    assert result["cache_hit_pct"] == 80.0


def test_check_trading_today_pnl(db):
    rec = DataRecorder(db)
    # buy + sell
    buy_id = rec.record_trade(
        coin="KRW-BTC",
        side="buy",
        price=100,
        amount=1,
        total_krw=10000,
        fee_krw=5,
        strategy="test",
        trigger_reason="test",
    )
    rec.record_trade(
        coin="KRW-BTC",
        side="sell",
        price=105,
        amount=1,
        total_krw=10500,
        fee_krw=5,
        strategy="test",
        trigger_reason="익절",
        buy_trade_id=buy_id,
        profit_pct=4.9,
        profit_krw=490,
    )
    checker = HealthChecker(db)
    result = checker._check_trading_today_kst()
    assert result["buys_today"] == 1
    assert result["sells_today"] == 1
    assert result["pnl_today_krw"] == 490


# ===================================================================
# 2. run_periodic 통합
# ===================================================================


def test_run_periodic_sends_slack(db):
    """run_periodic이 Slack notifier.send를 호출한다."""
    notifier = MagicMock()
    checker = HealthChecker(db, notifier=notifier)

    # API self-ping 실패 모킹
    with patch("urllib.request.urlopen", side_effect=Exception("refused")):
        checker.run_periodic()

    notifier.send.assert_called_once()
    sent_msg = notifier.send.call_args[0][0]
    # 포맷 검증 (#197 가독성 개선 반영)
    assert "시스템 상태 체크" in sent_msg
    assert "코인 봇" in sent_msg  # #267: 'BOT' → '코인 봇 (Upbit)' 라벨 변경
    assert "API" in sent_msg
    assert "NEWS" in sent_msg
    assert "신호 — 최근 1h" in sent_msg
    assert "LLM" in sent_msg
    assert "매매 손익" in sent_msg  # #197 "오늘 매매 손익" 섹션
    # #197 금일 손익 % 포함
    assert "%" in sent_msg


def test_run_periodic_includes_emoji_status(db):
    """상태별 emoji가 포함됨."""
    notifier = MagicMock()
    checker = HealthChecker(db, notifier=notifier)
    with patch("urllib.request.urlopen", side_effect=Exception("refused")):
        checker.run_periodic()
    msg = notifier.send.call_args[0][0]
    # 최소 ✅ 또는 ⚠️ 하나는 있어야
    assert "✅" in msg or "⚠️" in msg or "❌" in msg


def test_run_periodic_no_notifier_silent(db):
    """notifier 없으면 조용히 실행 (에러 없음)."""
    checker = HealthChecker(db, notifier=None)
    with patch("urllib.request.urlopen", side_effect=Exception("refused")):
        result = checker.run_periodic()
    assert isinstance(result, dict)
    assert "bot_process" in result


# ===================================================================
# 3. 에러 로그 체크
# ===================================================================


def test_check_recent_errors_no_log_dir(db):
    """logs/error 디렉토리 없으면 ok."""
    checker = HealthChecker(db)
    result = checker._check_recent_errors()
    assert result["status"] == "ok"
    assert result["error_count_4h"] == 0
