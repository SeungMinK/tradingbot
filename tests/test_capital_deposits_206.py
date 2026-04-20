"""#206: capital_deposits 테이블 + sync_deposits 테스트.

- 신규 입금 등록
- upbit_uuid 중복 방지
- _calculate_db_total_asset이 capital_deposits 합산을 사용
- 빈 테이블 fallback (legacy daily_reports.starting_balance)
"""

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from cryptobot.bot.health_checker import HealthChecker
from cryptobot.data.database import Database


@pytest.fixture
def db():
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    yield db
    db.close()


class _FakeTrader:
    def __init__(self, deposits):
        self._deposits = deposits
        self.is_ready = True

    def get_deposit_history(self, currency="KRW", limit=100):
        return list(self._deposits)


class _FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)


# ===================================================================
# 스키마
# ===================================================================


def test_capital_deposits_table_exists(db):
    """initialize 후 테이블 생성 확인."""
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='capital_deposits'"
    ).fetchall()
    assert len(rows) == 1


def test_capital_deposits_columns(db):
    """필수 컬럼 존재 확인."""
    cols = {dict(r)["name"] for r in db.execute("PRAGMA table_info(capital_deposits)").fetchall()}
    assert {"id", "currency", "amount_krw", "deposited_at", "source", "upbit_uuid", "note"} <= cols


def test_upbit_uuid_unique_constraint(db):
    """upbit_uuid UNIQUE — 같은 uuid 두 번 못 들어감."""
    db.execute(
        "INSERT INTO capital_deposits (currency, amount_krw, deposited_at, upbit_uuid) "
        "VALUES ('KRW', 100000, '2026-04-19 22:06:00', 'uuid-1')"
    )
    db.commit()
    with pytest.raises(Exception):
        db.execute(
            "INSERT INTO capital_deposits (currency, amount_krw, deposited_at, upbit_uuid) "
            "VALUES ('KRW', 100000, '2026-04-19 22:06:00', 'uuid-1')"
        )
        db.commit()


# ===================================================================
# sync_deposits
# ===================================================================


def test_sync_inserts_new_deposits(db):
    """신규 입금 모두 등록."""
    trader = _FakeTrader(
        [
            {"uuid": "u1", "amount_krw": 400000.0, "deposited_at": "2026-04-19T22:06:15+09:00"},
            {"uuid": "u2", "amount_krw": 100000.0, "deposited_at": "2026-04-03T22:51:55+09:00"},
        ]
    )
    notifier = _FakeNotifier()
    checker = HealthChecker(db, trader, notifier)
    result = checker.sync_deposits()
    assert result["status"] == "ok"
    assert result["fetched"] == 2
    assert result["new"] == 2
    assert result["total_added_krw"] == 500000.0
    # Slack 호출됨
    assert len(notifier.sent) == 1
    assert "신규 입금" in notifier.sent[0]


def test_sync_skips_existing_deposits(db):
    """이미 등록된 uuid는 다시 안 넣음."""
    trader = _FakeTrader(
        [{"uuid": "u1", "amount_krw": 400000.0, "deposited_at": "2026-04-19T22:06:15+09:00"}]
    )
    notifier = _FakeNotifier()
    checker = HealthChecker(db, trader, notifier)
    # 1차
    r1 = checker.sync_deposits()
    assert r1["new"] == 1
    # 2차 — 같은 입금
    r2 = checker.sync_deposits()
    assert r2["new"] == 0
    assert r2["fetched"] == 1
    # 두 번째에는 Slack 안 보냄 (new==0)
    assert len(notifier.sent) == 1


def test_sync_partial_new(db):
    """일부 기존 + 일부 신규 — 신규만 등록."""
    trader = _FakeTrader(
        [
            {"uuid": "u1", "amount_krw": 100000.0, "deposited_at": "2026-04-01T00:00:00+09:00"},
            {"uuid": "u2", "amount_krw": 400000.0, "deposited_at": "2026-04-19T22:06:15+09:00"},
        ]
    )
    notifier = _FakeNotifier()
    checker = HealthChecker(db, trader, notifier)
    checker.sync_deposits()  # u1, u2 등록

    # 새 입금 추가
    trader._deposits.insert(0, {"uuid": "u3", "amount_krw": 50000.0, "deposited_at": "2026-04-20T10:00:00+09:00"})
    r = checker.sync_deposits()
    assert r["new"] == 1
    assert r["total_added_krw"] == 50000.0


def test_sync_no_trader_returns_skip(db):
    """API 미설정이면 스킵."""
    checker = HealthChecker(db, trader=None, notifier=None)
    result = checker.sync_deposits()
    assert result["status"] == "ok"
    assert "스킵" in result["message"]


def test_sync_skips_old_deposits_via_cutoff(db):
    """첫 daily_report.date 이전 입금은 자동 제외 (운영 자본과 무관한 옛날 입금 보호)."""
    db.execute(
        "INSERT INTO daily_reports (date, starting_balance_krw, ending_balance_krw) "
        "VALUES ('2026-04-04', 95000, 95000)"
    )
    db.commit()

    trader = _FakeTrader(
        [
            {"uuid": "old", "amount_krw": 2000000.0, "deposited_at": "2021-12-15T07:11:13+09:00"},
            {"uuid": "new", "amount_krw": 400000.0, "deposited_at": "2026-04-19T22:06:15+09:00"},
        ]
    )
    checker = HealthChecker(db, trader, notifier=None)
    r = checker.sync_deposits()
    assert r["new"] == 1
    assert r["total_added_krw"] == 400000.0
    assert r["skipped_old"] == 1
    assert r["since"] == "2026-04-04"


def test_sync_explicit_since_overrides_default(db):
    """since 인자가 명시되면 daily_reports 무시하고 그대로 사용."""
    trader = _FakeTrader(
        [
            {"uuid": "u1", "amount_krw": 100000.0, "deposited_at": "2026-04-10T00:00:00+09:00"},
            {"uuid": "u2", "amount_krw": 400000.0, "deposited_at": "2026-04-19T22:06:15+09:00"},
        ]
    )
    checker = HealthChecker(db, trader, notifier=None)
    r = checker.sync_deposits(since="2026-04-15")
    assert r["new"] == 1
    assert r["total_added_krw"] == 400000.0


def test_sync_handles_uuid_none(db):
    """uuid 없는 항목은 스킵."""
    trader = _FakeTrader(
        [
            {"uuid": None, "amount_krw": 100000.0, "deposited_at": "2026-04-01T00:00:00+09:00"},
            {"uuid": "u1", "amount_krw": 400000.0, "deposited_at": "2026-04-19T22:06:15+09:00"},
        ]
    )
    checker = HealthChecker(db, trader, notifier=None)
    r = checker.sync_deposits()
    assert r["new"] == 1


# ===================================================================
# _calculate_db_total_asset가 capital_deposits 사용
# ===================================================================


def test_total_asset_uses_capital_deposits(db):
    """capital_deposits 합산이 total_deposits로 사용됨."""
    db.execute(
        "INSERT INTO capital_deposits (currency, amount_krw, deposited_at, source) "
        "VALUES ('KRW', 100000, '2026-04-04 00:00:00', 'initial')"
    )
    db.execute(
        "INSERT INTO capital_deposits (currency, amount_krw, deposited_at, source) "
        "VALUES ('KRW', 400000, '2026-04-19 22:06:00', 'api')"
    )
    db.commit()

    checker = HealthChecker(db, trader=None, notifier=None)
    total = checker._calculate_db_total_asset()
    # net_flow=0, db_coin_value=0 (거래 없음)
    assert total == 500000


def test_total_asset_fallback_to_daily_reports(db):
    """capital_deposits 비었으면 첫 daily_reports.starting_balance 사용."""
    db.execute(
        "INSERT INTO daily_reports (date, starting_balance_krw, ending_balance_krw) "
        "VALUES ('2026-04-04', 95000, 95000)"
    )
    db.execute(
        "INSERT INTO daily_reports (date, starting_balance_krw, ending_balance_krw) "
        "VALUES ('2026-04-05', 96000, 96000)"
    )
    db.commit()

    checker = HealthChecker(db, trader=None, notifier=None)
    total = checker._calculate_db_total_asset()
    assert total == 95000  # 첫 날 기준
