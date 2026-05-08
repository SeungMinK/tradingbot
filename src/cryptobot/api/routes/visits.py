"""#240: 방문자 통계 (admin 전용)."""

import logging

from fastapi import APIRouter, Depends, Query

from cryptobot.api.auth import UserResponse, get_current_user
from cryptobot.api.deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/visits", tags=["visits"])


@router.get("/stats")
def get_visit_stats(_: UserResponse = Depends(get_current_user), days: int = Query(30, ge=1, le=365)):
    """방문자 통계 — 오늘/어제/7일/누적 + 일별 추이."""
    db = get_db()

    # 오늘
    today = db.execute(
        "SELECT COUNT(*) AS pv, SUM(CASE WHEN is_unique THEN 1 ELSE 0 END) AS uv "
        "FROM page_visits WHERE DATE(visited_at, '+9 hours') = DATE('now', '+9 hours')"
    ).fetchone()
    # 어제
    yesterday = db.execute(
        "SELECT COUNT(*) AS pv, SUM(CASE WHEN is_unique THEN 1 ELSE 0 END) AS uv "
        "FROM page_visits WHERE DATE(visited_at, '+9 hours') = DATE('now', '+9 hours', '-1 day')"
    ).fetchone()
    # 최근 7일
    last_7 = db.execute(
        "SELECT COUNT(*) AS pv, SUM(CASE WHEN is_unique THEN 1 ELSE 0 END) AS uv "
        "FROM page_visits WHERE visited_at >= datetime('now', '-7 days')"
    ).fetchone()
    # 누적
    total = db.execute(
        "SELECT COUNT(*) AS pv, SUM(CASE WHEN is_unique THEN 1 ELSE 0 END) AS uv FROM page_visits"
    ).fetchone()

    # 일별 N일치
    daily_rows = db.execute(
        f"""
        SELECT DATE(visited_at, '+9 hours') AS d,
               COUNT(*) AS pv,
               SUM(CASE WHEN is_unique THEN 1 ELSE 0 END) AS uv
        FROM page_visits
        WHERE visited_at >= datetime('now', '-{days} days')
        GROUP BY d ORDER BY d ASC
        """
    ).fetchall()

    def s(row):
        d = dict(row) if row else {}
        return {"pv": d.get("pv") or 0, "uv": d.get("uv") or 0}

    return {
        "today": s(today),
        "yesterday": s(yesterday),
        "last_7_days": s(last_7),
        "total": s(total),
        "daily": [{"date": dict(r)["d"], "pv": dict(r)["pv"] or 0, "uv": dict(r)["uv"] or 0} for r in daily_rows],
    }
