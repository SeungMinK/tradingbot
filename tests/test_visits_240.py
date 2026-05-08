"""#240: 방문자 추적 + 통계 테스트."""

import tempfile
from pathlib import Path
from unittest.mock import patch

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
    from cryptobot.api.main import app
    from cryptobot.api.auth import UserResponse, get_current_user
    import cryptobot.api.routes.visits as v_mod
    import cryptobot.api.routes.public as p_mod

    fake = UserResponse(id=1, username="test", display_name="t", is_admin=True)
    app.dependency_overrides[get_current_user] = lambda: fake

    db = Database(db_path)
    db.initialize()
    v_mod.get_db = lambda: db
    p_mod.get_db = lambda: db
    return TestClient(app), db


def test_page_visits_table_exists(db_path):
    """initialize 후 page_visits 테이블 생성."""
    db = Database(db_path); db.initialize()
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='page_visits'"
    ).fetchall()
    assert len(rows) == 1


def test_visit_post_inserts_row(db_path):
    """POST /visit → 1건 INSERT."""
    client, db = _client(db_path)
    r = client.post("/api/public/visit", json={"session_id": "s1", "page": "/"})
    assert r.status_code == 200
    cnt = db.execute("SELECT COUNT(*) FROM page_visits").fetchone()[0]
    assert cnt == 1


def test_visit_first_today_marked_unique(db_path):
    """그 날 첫 방문 → is_unique=True."""
    client, db = _client(db_path)
    client.post("/api/public/visit", json={"session_id": "s1", "page": "/"})
    row = db.execute("SELECT is_unique FROM page_visits WHERE session_id='s1'").fetchone()
    assert dict(row)["is_unique"] == 1


def test_visit_second_today_not_unique(db_path):
    """같은 세션 두 번째 방문 → is_unique=False."""
    client, db = _client(db_path)
    client.post("/api/public/visit", json={"session_id": "s1", "page": "/"})
    client.post("/api/public/visit", json={"session_id": "s1", "page": "/"})
    rows = db.execute("SELECT is_unique FROM page_visits ORDER BY id").fetchall()
    assert dict(rows[0])["is_unique"] == 1
    assert dict(rows[1])["is_unique"] == 0


def test_visits_stats_endpoint(db_path):
    """GET /visits/stats — 인증 필요 + 카운트 정확."""
    client, db = _client(db_path)
    client.post("/api/public/visit", json={"session_id": "s1", "page": "/"})
    client.post("/api/public/visit", json={"session_id": "s2", "page": "/"})
    client.post("/api/public/visit", json={"session_id": "s1", "page": "/"})  # dup

    r = client.get("/api/visits/stats?days=7")
    assert r.status_code == 200
    data = r.json()
    assert data["today"]["pv"] == 3
    assert data["today"]["uv"] == 2  # s1, s2 unique
    assert data["total"]["pv"] == 3
    assert "daily" in data


def test_visit_no_session_id_still_works(db_path):
    """session_id 없어도 IP 기반으로 동작."""
    client, db = _client(db_path)
    r = client.post("/api/public/visit", json={"page": "/"})
    assert r.status_code == 200
    cnt = db.execute("SELECT COUNT(*) FROM page_visits").fetchone()[0]
    assert cnt == 1
