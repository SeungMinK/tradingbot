"""#218: /api/balance 응답에 total_deposits_krw 포함 검증.

대시보드 시작금액 100,000 고정값 → capital_deposits 합산으로 동적화.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from cryptobot.data.database import Database


@pytest.fixture
def db_path():
    tmpdir = tempfile.mkdtemp()
    p = Path(tmpdir) / "test.db"
    db = Database(p)
    db.initialize()
    db.close()
    return p


def _client(db_path):
    """API TestClient — auth bypass + db override."""
    from cryptobot.api.main import app
    from cryptobot.api.auth import UserResponse, get_current_user
    from cryptobot.api.deps import get_db

    fake_user = UserResponse(id=1, username="test", display_name="test", is_admin=True)
    app.dependency_overrides[get_current_user] = lambda: fake_user

    db = Database(db_path)
    db.initialize()
    # get_db는 함수라 monkeypatch
    import cryptobot.api.routes.balance as bal_mod
    bal_mod.get_db = lambda: db

    return TestClient(app), db


def test_balance_includes_total_deposits_field(db_path):
    """기본 응답에 total_deposits_krw 필드 포함."""
    client, db = _client(db_path)
    with patch("cryptobot.bot.trader.Trader") as MockTrader:
        MockTrader.return_value.is_ready = False
        r = client.get("/api/balance")
    assert r.status_code == 200
    data = r.json()
    assert "total_deposits_krw" in data


def test_balance_sums_capital_deposits(db_path):
    """capital_deposits 합산이 반환됨."""
    db = Database(db_path)
    db.initialize()
    db.execute(
        "INSERT INTO capital_deposits (currency, amount_krw, deposited_at, source) "
        "VALUES ('KRW', 100000, '2026-04-04 00:00:00', 'initial')"
    )
    db.execute(
        "INSERT INTO capital_deposits (currency, amount_krw, deposited_at, source) "
        "VALUES ('KRW', 400000, '2026-04-19 22:06:00', 'api')"
    )
    db.commit()
    db.close()

    client, _ = _client(db_path)
    with patch("cryptobot.bot.trader.Trader") as MockTrader:
        MockTrader.return_value.is_ready = False
        r = client.get("/api/balance")
    assert r.json()["total_deposits_krw"] == 500000


def test_balance_fallback_to_first_daily_report(db_path):
    """capital_deposits 비었으면 첫 daily_reports.starting_balance fallback."""
    db = Database(db_path)
    db.initialize()
    db.execute(
        "INSERT INTO daily_reports (date, starting_balance_krw, ending_balance_krw) "
        "VALUES ('2026-04-04', 95000, 95000)"
    )
    db.commit()
    db.close()

    client, _ = _client(db_path)
    with patch("cryptobot.bot.trader.Trader") as MockTrader:
        MockTrader.return_value.is_ready = False
        r = client.get("/api/balance")
    assert r.json()["total_deposits_krw"] == 95000


def test_balance_zero_when_no_data(db_path):
    """둘 다 비었으면 0."""
    client, _ = _client(db_path)
    with patch("cryptobot.bot.trader.Trader") as MockTrader:
        MockTrader.return_value.is_ready = False
        r = client.get("/api/balance")
    assert r.json()["total_deposits_krw"] == 0
