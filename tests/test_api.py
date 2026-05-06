"""FastAPI 엔드포인트 테스트."""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import cryptobot.api.deps as deps
from cryptobot.api.auth import hash_password
from cryptobot.api.main import app
from cryptobot.data.database import Database


@pytest.fixture(autouse=True)
def _test_db():
    """매 테스트마다 새 DB를 주입."""
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()

    # 테스트 유저 삽입
    db.execute(
        "INSERT INTO users (username, password_hash, display_name, is_admin) VALUES (?, ?, ?, ?)",
        ("admin", hash_password("test1234"), "Admin", True),
    )
    db.commit()

    # 테스트 DB 오버라이드
    deps._test_db_override = db

    # rate limit 초기화
    from cryptobot.api.routes.auth import _login_attempts

    _login_attempts.clear()

    yield db

    deps._test_db_override = None
    db.close()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_header(client):
    """로그인 후 인증 헤더 반환."""
    response = client.post("/api/auth/login", data={"username": "admin", "password": "test1234"})
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_health_check(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_login_success(client):
    response = client.post("/api/auth/login", data={"username": "admin", "password": "test1234"})
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_login_fail(client):
    response = client.post("/api/auth/login", data={"username": "admin", "password": "wrong"})
    assert response.status_code == 401


def test_get_me(client, auth_header):
    response = client.get("/api/auth/me", headers=auth_header)
    assert response.status_code == 200
    assert response.json()["username"] == "admin"


def test_unauthorized(client):
    response = client.get("/api/trades")
    assert response.status_code == 401


def test_get_strategies(client, auth_header):
    response = client.get("/api/strategies", headers=auth_header)
    assert response.status_code == 200
    # #226: long_term_swing 추가로 11개
    assert len(response.json()) == 11


def test_get_active_strategies(client, auth_header):
    response = client.get("/api/strategies/active", headers=auth_header)
    assert response.status_code == 200
    active = response.json()
    assert len(active) == 1
    # #197: 기본 활성 전략 bb_rsi_combined로 변경
    assert active[0]["name"] == "bb_rsi_combined"


def test_activate_strategy(client, auth_header):
    response = client.put("/api/strategies/macd/activate", headers=auth_header)
    assert response.status_code == 200

    response = client.get("/api/strategies/active", headers=auth_header)
    names = [s["name"] for s in response.json()]
    assert "macd" in names


def test_get_trades_empty(client, auth_header):
    response = client.get("/api/trades", headers=auth_header)
    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_get_trade_stats(client, auth_header):
    response = client.get("/api/trades/stats", headers=auth_header)
    assert response.status_code == 200
    assert response.json()["total_trades"] == 0


def test_get_market_no_data(client, auth_header):
    response = client.get("/api/market/current", headers=auth_header)
    assert response.status_code == 200
    assert response.json()["status"] == "no_data"
