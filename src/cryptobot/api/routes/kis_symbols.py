"""#297: KIS 미국주식 종목 풀 관리 (admin)."""

import logging

from fastapi import APIRouter, Body, Depends, HTTPException

from cryptobot.api.auth import UserResponse, get_current_user
from cryptobot.api.deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/kis-symbols", tags=["kis-symbols"])


@router.get("")
def list_symbols(_: UserResponse = Depends(get_current_user)):
    """전체 종목 목록 (활성/비활성 포함). 카테고리별 정렬.

    #311: minute_supported 필드 추가 — KIS 분봉 미지원 종목은 admin UI에서 dim 처리.
    """
    from cryptobot.exchange.kis_us import KIS_MINUTE_UNSUPPORTED

    db = get_db()
    rows = db.execute(
        """
        SELECT ticker, display_name, exchange, is_integer_only, category, enabled, note,
               updated_at
        FROM kis_us_symbols
        ORDER BY enabled DESC, category, ticker
        """
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["minute_supported"] = d["ticker"] not in KIS_MINUTE_UNSUPPORTED
        result.append(d)
    return result


@router.post("/toggle")
def toggle_symbol(
    payload: dict = Body(...),
    _: UserResponse = Depends(get_current_user),
):
    """종목 활성/비활성 토글. body: {ticker: str, enabled: bool}.

    #315: 분봉 미지원 종목은 enable=true 거부 (봇 평가 불가).
    """
    from cryptobot.exchange.kis_us import KIS_MINUTE_UNSUPPORTED

    ticker = (payload.get("ticker") or "").strip().upper()
    enabled = bool(payload.get("enabled"))
    if not ticker:
        raise HTTPException(400, "ticker required")
    if enabled and ticker in KIS_MINUTE_UNSUPPORTED:
        raise HTTPException(400, f"{ticker}: KIS 분봉 미지원 종목은 활성화 불가")

    db = get_db()
    cur = db.execute(
        "UPDATE kis_us_symbols SET enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE ticker = ?",
        (1 if enabled else 0, ticker),
    )
    db.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, f"종목 {ticker} 없음")
    logger.info("KIS US symbol toggle: %s = %s", ticker, enabled)
    return {"ok": True, "ticker": ticker, "enabled": enabled}


@router.post("")
def add_symbol(
    payload: dict = Body(...),
    _: UserResponse = Depends(get_current_user),
):
    """종목 추가. body: {ticker, display_name?, exchange?, is_integer_only?, category?, note?}"""
    ticker = (payload.get("ticker") or "").strip().upper()
    if not ticker:
        raise HTTPException(400, "ticker required")
    exchange = (payload.get("exchange") or "NASD").strip().upper()
    if exchange not in ("NASD", "NYSE", "AMEX"):
        raise HTTPException(400, "exchange must be NASD/NYSE/AMEX")

    db = get_db()
    try:
        db.execute(
            "INSERT INTO kis_us_symbols "
            "(ticker, display_name, exchange, is_integer_only, category, enabled, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                ticker,
                payload.get("display_name") or ticker,
                exchange,
                1 if payload.get("is_integer_only") else 0,
                payload.get("category") or "etc",
                1 if payload.get("enabled") else 0,
                payload.get("note") or "",
            ),
        )
        db.commit()
    except Exception as e:
        raise HTTPException(409, f"이미 존재하거나 추가 실패: {e}")
    return {"ok": True, "ticker": ticker}


@router.get("/evaluations")
def list_evaluations(
    limit: int = 50,
    ticker: str | None = None,
    _: UserResponse = Depends(get_current_user),
):
    """매 틱 매수 평가 결과 (#297-2). 사용자가 봇이 어떻게 판단했는지 확인.

    응답: [{evaluated_at, ticker, price, rsi, ma20, ma60, should_buy, reason, confidence}]
    """
    db = get_db()
    if ticker:
        rows = db.execute(
            "SELECT * FROM kis_us_evaluations WHERE ticker = ? ORDER BY id DESC LIMIT ?",
            (ticker.upper(), limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM kis_us_evaluations ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.delete("/{ticker}")
def delete_symbol(ticker: str, _: UserResponse = Depends(get_current_user)):
    """종목 삭제."""
    db = get_db()
    cur = db.execute("DELETE FROM kis_us_symbols WHERE ticker = ?", (ticker.upper(),))
    db.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, f"종목 {ticker} 없음")
    return {"ok": True, "ticker": ticker.upper()}
