"""#378: BacktestCoinFilter 단위 + 통합 테스트."""

from __future__ import annotations

import sqlite3

from cryptobot.bot.backtest_coin_filter import BacktestCoinFilter


def _create_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT,
            strategy_name TEXT,
            coin TEXT,
            num_trades INTEGER,
            avg_profit_pct REAL
        )
    """)
    return db


def _insert(db, run_date: str, coin: str, strategy: str, num_trades: int, avg_profit_pct: float) -> None:
    db.execute(
        "INSERT INTO backtest_results (run_date, coin, strategy_name, num_trades, avg_profit_pct) VALUES (?, ?, ?, ?, ?)",
        (run_date, coin, strategy, num_trades, avg_profit_pct),
    )


# === 디폴트 값 검증 ===


def test_default_thresholds():
    assert BacktestCoinFilter.DEFAULT_MIN_AVG_PROFIT == 5.0
    assert BacktestCoinFilter.DEFAULT_MIN_TRADES == 3


# === get_validated_coins 단위 ===


def test_returns_coins_above_thresholds():
    db = _create_db()
    _insert(db, "2026-05-10", "KRW-BIO", "bollinger_bands", 5, 8.5)  # 통과
    _insert(db, "2026-05-10", "KRW-WET", "volatility_breakout", 4, 5.0)  # 통과 (정확히 5%)
    _insert(db, "2026-05-10", "KRW-NEWT", "vwap_orb_breakout", 4, 2.0)  # 평균익절 미달
    _insert(db, "2026-05-10", "KRW-LOW", "bb_rsi_combined", 2, 8.0)  # 거래수 미달
    db.commit()

    f = BacktestCoinFilter(db)
    coins = f.get_validated_coins()
    assert coins == {"KRW-BIO", "KRW-WET"}


def test_only_latest_run_date():
    """오래된 run_date는 무시."""
    db = _create_db()
    _insert(db, "2026-05-03", "KRW-OLD", "x", 5, 10.0)  # 이전 run, 무시
    _insert(db, "2026-05-10", "KRW-NEW", "x", 5, 8.0)
    db.commit()

    f = BacktestCoinFilter(db)
    coins = f.get_validated_coins()
    assert "KRW-OLD" not in coins
    assert "KRW-NEW" in coins


def test_any_param_combo_passes():
    """한 코인이 여러 strategy/params 결과 가지면 어느 하나라도 통과 시 통과."""
    db = _create_db()
    _insert(db, "2026-05-10", "KRW-BIO", "vol_breakout(k=0.3)", 5, 3.0)  # 미달
    _insert(db, "2026-05-10", "KRW-BIO", "bollinger_bands(bb=2)", 4, 8.5)  # 통과
    db.commit()

    f = BacktestCoinFilter(db)
    assert "KRW-BIO" in f.get_validated_coins()


def test_empty_results():
    db = _create_db()
    f = BacktestCoinFilter(db)
    assert f.get_validated_coins() == set()


def test_custom_thresholds():
    db = _create_db()
    _insert(db, "2026-05-10", "KRW-A", "x", 5, 4.0)  # 디폴트 5%면 탈락, 3%면 통과
    db.commit()

    f_default = BacktestCoinFilter(db)
    assert "KRW-A" not in f_default.get_validated_coins()

    f_relaxed = BacktestCoinFilter(db, min_avg_profit=3.0)
    assert "KRW-A" in f_relaxed.get_validated_coins()


# === filter_coins 통합 ===


def test_filter_intersects_with_validated():
    db = _create_db()
    _insert(db, "2026-05-10", "KRW-BIO", "x", 5, 10.0)
    _insert(db, "2026-05-10", "KRW-WET", "x", 5, 8.0)
    db.commit()

    f = BacktestCoinFilter(db)
    coins = ["KRW-BTC", "KRW-BIO", "KRW-WET", "KRW-NEWT"]
    filtered = f.filter_coins(coins)
    assert filtered == ["KRW-BIO", "KRW-WET"]


def test_filter_preserves_input_order():
    db = _create_db()
    _insert(db, "2026-05-10", "KRW-A", "x", 5, 10.0)
    _insert(db, "2026-05-10", "KRW-B", "x", 5, 10.0)
    _insert(db, "2026-05-10", "KRW-C", "x", 5, 10.0)
    db.commit()

    f = BacktestCoinFilter(db)
    filtered = f.filter_coins(["KRW-C", "KRW-A", "KRW-B"])
    assert filtered == ["KRW-C", "KRW-A", "KRW-B"]


def test_filter_empty_validation_returns_original():
    """백테스트 결과 없으면 원본 그대로 (안전 fallback)."""
    db = _create_db()
    f = BacktestCoinFilter(db)
    coins = ["KRW-BTC", "KRW-ETH"]
    assert f.filter_coins(coins) == coins


def test_filter_no_intersection_returns_empty():
    """입력 코인 중 통과한 게 없으면 빈 리스트."""
    db = _create_db()
    _insert(db, "2026-05-10", "KRW-ALT1", "x", 5, 10.0)
    db.commit()

    f = BacktestCoinFilter(db)
    assert f.filter_coins(["KRW-BTC", "KRW-ETH"]) == []
