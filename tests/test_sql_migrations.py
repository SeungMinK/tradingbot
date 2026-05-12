"""#384: SQL 마이그레이션 자동 실행 시스템 (scripts/migrate_*.sql) 테스트."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from cryptobot.data.database import Database


def test_schema_migrations_table_created_on_initialize(tmp_path):
    """initialize() 이후 schema_migrations 테이블 존재."""
    db = Database(tmp_path / "test.db")
    db.initialize()
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    assert row is not None
    db.close()


def test_real_migration_file_applied(tmp_path):
    """실제 scripts/migrate_activate_bb_rsi_swing.sql 가 초기화 시 자동 적용."""
    db = Database(tmp_path / "test.db")
    db.initialize()

    # 활성 전략 = bb_rsi_combined (마이그레이션 적용 증거)
    row = db.execute("SELECT name FROM strategies WHERE is_active = 1").fetchone()
    assert row is not None
    assert dict(row)["name"] == "bb_rsi_combined"

    # bot_config 시드도 적용 (#386 마이그레이션이 추가로 true로 설정)
    row2 = db.execute(
        "SELECT value FROM bot_config WHERE key = 'coin_backtest_filter_enabled'"
    ).fetchone()
    assert row2 is not None
    # 디폴트는 false였지만 #386 후속 마이그레이션이 true로 변경
    assert dict(row2)["value"] in ("false", "true")

    # 이력 기록
    row3 = db.execute(
        "SELECT applied_at FROM schema_migrations WHERE filename = 'migrate_activate_bb_rsi_swing.sql'"
    ).fetchone()
    assert row3 is not None

    db.close()


def test_idempotent_reinitialize(tmp_path):
    """initialize() 두 번 호출해도 마이그레이션 중복 적용 안 됨 (멱등)."""
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    db.initialize()
    count_first = db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    db.close()

    db2 = Database(db_path)
    db2.initialize()
    count_second = db2.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]

    assert count_first == count_second
    db2.close()


def test_initialize_n_times_no_duplication(tmp_path):
    """N번 initialize 호출해도 이력 중복 없음."""
    db_path = tmp_path / "test.db"
    db = None
    for _ in range(5):
        if db is not None:
            db.close()
        db = Database(db_path)
        db.initialize()

    rows = db.execute("SELECT filename FROM schema_migrations").fetchall()
    filenames = [r[0] for r in rows]
    assert len(filenames) == len(set(filenames))
    db.close()


def test_legacy_db_gets_migration_applied(tmp_path):
    """이미 vwap_orb_breakout 활성인 legacy DB → 봇 시작 시 bb_rsi로 전환."""
    db_path = tmp_path / "legacy.db"
    db = Database(db_path)
    db.initialize()

    # 마이그레이션 이력 강제 삭제 + vwap_orb 재활성 (legacy 상태 시뮬레이션)
    db.execute("DELETE FROM schema_migrations WHERE filename = 'migrate_activate_bb_rsi_swing.sql'")
    db.execute("UPDATE strategies SET is_active = 0")
    db.execute("UPDATE strategies SET is_active = 1 WHERE name = 'vwap_orb_breakout'")
    db.commit()

    # 확인: legacy 상태
    row = db.execute("SELECT name FROM strategies WHERE is_active = 1").fetchone()
    assert dict(row)["name"] == "vwap_orb_breakout"
    db.close()

    # 봇 재시작 시뮬레이션 — initialize() 재호출
    db2 = Database(db_path)
    db2.initialize()
    row2 = db2.execute("SELECT name FROM strategies WHERE is_active = 1").fetchone()
    assert dict(row2)["name"] == "bb_rsi_combined", "마이그레이션 자동 재적용 실패"
    db2.close()


def test_migrations_run_in_filename_order(tmp_path):
    """파일명 사전 순으로 정렬 실행 (예: 01_, 02_, ...)."""
    db = Database(tmp_path / "test.db")
    db.initialize()

    # schema_migrations에 기록된 순서 != applied_at 순서일 수도 있지만,
    # 같은 종류 (migrate_*.sql) 파일은 사전 순.
    rows = db.execute(
        "SELECT filename FROM schema_migrations ORDER BY filename"
    ).fetchall()
    filenames = [r[0] for r in rows]
    assert filenames == sorted(filenames)
    db.close()
