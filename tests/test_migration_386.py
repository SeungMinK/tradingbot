"""#386: 백테스트 필터 ON + 화이트리스트 확장 마이그레이션 테스트."""

from __future__ import annotations

import tempfile
from pathlib import Path

from cryptobot.bot.coin_manager import CoinManager
from cryptobot.bot.config_manager import ConfigManager
from cryptobot.data.database import Database


def _make_db():
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()  # 자동 마이그레이션 적용
    return db


def test_backtest_filter_enabled_after_migration():
    """마이그레이션 후 coin_backtest_filter_enabled = true."""
    db = _make_db()
    row = db.execute(
        "SELECT value FROM bot_config WHERE key = 'coin_backtest_filter_enabled'"
    ).fetchone()
    assert dict(row)["value"] == "true"
    db.close()


def test_whitelist_expanded_after_migration():
    """화이트리스트에 메이저 8종 + 알트 7종 = 15종."""
    db = _make_db()
    row = db.execute("SELECT value FROM bot_config WHERE key = 'coin_whitelist'").fetchone()
    coins = [c.strip() for c in dict(row)["value"].split(",") if c.strip()]
    expected = {
        "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-ADA", "KRW-DOGE", "KRW-AVAX", "KRW-LINK",
        "KRW-BIO", "KRW-WET", "KRW-0G", "KRW-AERO", "KRW-RENDER", "KRW-BOUNTY", "KRW-JST",
    }
    assert set(coins) == expected
    db.close()


def test_migration_recorded_in_schema_migrations():
    """schema_migrations에 #386 마이그레이션 이력."""
    db = _make_db()
    row = db.execute(
        "SELECT applied_at FROM schema_migrations "
        "WHERE filename = 'migrate_enable_backtest_filter_and_expand_whitelist.sql'"
    ).fetchone()
    assert row is not None
    db.close()


def test_idempotent_reruns():
    """여러 번 initialize() 호출해도 동일 결과."""
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "test.db"

    db = Database(db_path)
    db.initialize()
    db.close()

    db2 = Database(db_path)
    db2.initialize()
    db2.close()

    db3 = Database(db_path)
    db3.initialize()

    row = db3.execute(
        "SELECT value FROM bot_config WHERE key = 'coin_backtest_filter_enabled'"
    ).fetchone()
    assert dict(row)["value"] == "true"
    db3.close()


def _seed_backtest(db, coin: str, num_trades: int, avg_profit_pct: float, run_date: str = "2026-05-10"):
    db.execute(
        "INSERT INTO backtest_results "
        "(run_date, strategy_name, coin, period, num_trades, win_rate, "
        " total_return_pct, max_drawdown_pct, sharpe_ratio, avg_profit_pct, "
        " avg_loss_pct, best_trade_pct, worst_trade_pct, params_json) "
        "VALUES (?, 'test', ?, '30d', ?, 60.0, 5.0, -3.0, 1.0, ?, -1.0, 5.0, -3.0, '{}')",
        (run_date, coin, num_trades, avg_profit_pct),
    )
    db.commit()


def test_coin_manager_uses_filtered_pool_after_migration():
    """CoinManager.refresh() 후 active_coins = 화이트리스트 ∩ 백테스트 통과."""
    db = _make_db()
    # 일부 화이트리스트 코인을 백테스트 통과로 시드
    _seed_backtest(db, "KRW-ETH", 5, 10.0)
    _seed_backtest(db, "KRW-XRP", 5, 8.0)
    _seed_backtest(db, "KRW-BIO", 5, 15.0)
    _seed_backtest(db, "KRW-WET", 5, 12.0)
    # BTC는 시드 안 함 (미통과)

    cm = ConfigManager(db)
    mgr = CoinManager(db, cm)
    mgr.refresh()

    # 필터 통과한 4종만 active_coins에 (held 없음 가정)
    assert "KRW-ETH" in mgr.active_coins
    assert "KRW-XRP" in mgr.active_coins
    assert "KRW-BIO" in mgr.active_coins
    assert "KRW-WET" in mgr.active_coins
    assert "KRW-BTC" not in mgr.active_coins, "백테스트 미통과 BTC가 매수 풀에 있으면 안됨"
    db.close()
