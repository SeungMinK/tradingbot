"""#277: 시장별 자본 입출금 + 이동 API (admin)."""

import logging
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException

from cryptobot.api.auth import UserResponse, get_current_user
from cryptobot.api.deps import get_db
from cryptobot.bot.market_budget import get_market_budget_status

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/market-capital", tags=["market-capital"])


VALID_KIS_MARKETS = ("kis_kr", "kis_us")


def _record(db, market: str, amount: float, source: str, note: str = "") -> int:
    cur = db.execute(
        "INSERT INTO market_capital_deposits (market, amount_krw, source, note) "
        "VALUES (?, ?, ?, ?)",
        (market, amount, source, note),
    )
    db.commit()
    return cur.lastrowid


def _fetch_live_balance(market: str) -> dict:
    """실제 KIS API 잔고 조회. 실패 시 None 반환 — UI가 '조회불가' 표시."""
    from cryptobot.bot.config import config

    if not config.kis.is_configured:
        return {"available": None, "currency": None, "error": "KIS 미설정"}

    try:
        from cryptobot.exchange.kis.auth import KISTokenManager

        tm = KISTokenManager(
            app_key=config.kis.app_key,
            app_secret=config.kis.app_secret,
            is_paper=config.kis.is_paper,
        )
        if market == "kis_kr":
            from cryptobot.exchange.kis_kr import KISKoreanExchange

            ex = KISKoreanExchange(
                token_manager=tm,
                account_number=config.kis.account_number,
                account_product_code=config.kis.account_product_code,
                is_paper=config.kis.is_paper,
            )
            return {"available": ex.get_balance("KRW"), "currency": "KRW", "error": None}
        if market == "kis_us":
            from cryptobot.exchange.kis_us import KISUSExchange

            ex = KISUSExchange(
                token_manager=tm,
                account_number=config.kis.account_number,
                account_product_code=config.kis.account_product_code,
                is_paper=config.kis.is_paper,
            )
            return {"available": ex.get_balance("USD"), "currency": "USD", "error": None}
    except Exception as e:
        logger.warning("실잔고 조회 실패 (%s): %s", market, e)
        return {"available": None, "currency": None, "error": str(e)}
    return {"available": None, "currency": None, "error": "unknown market"}


@router.get("/status")
def get_status(_: UserResponse = Depends(get_current_user)):
    """시장별 장부(ledger) 상태 + 실제 KIS API 잔고."""
    db = get_db()
    markets = []
    for m in VALID_KIS_MARKETS:
        ledger = get_market_budget_status(db, m)
        live = _fetch_live_balance(m)
        markets.append({**ledger, "live": live})
    return {"markets": markets}


@router.get("/history")
def get_history(_: UserResponse = Depends(get_current_user), limit: int = 50):
    """입출금 이력 (최근 N건)."""
    db = get_db()
    rows = db.execute(
        "SELECT id, market, amount_krw, deposited_at, source, note "
        "FROM market_capital_deposits ORDER BY deposited_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


@router.post("/deposit")
def deposit(
    payload: dict = Body(...),
    _: UserResponse = Depends(get_current_user),
):
    """입금 — split=true 면 한국/미국 자동 50:50 분배.

    Body: { amount: number, market?: 'kis_kr'|'kis_us', split?: bool, note?: str }
    - split=true: amount를 둘로 나눠 두 시장에 각각 INSERT
    - market 지정 + split=false (또는 미지정): 그 시장에 단일 INSERT
    """
    amount = float(payload.get("amount", 0))
    if amount <= 0:
        raise HTTPException(400, "amount must be positive (use /withdraw for negative)")
    note = payload.get("note", "")
    split = bool(payload.get("split", False))
    market = payload.get("market")

    db = get_db()
    if split:
        half = amount / 2
        _record(db, "kis_kr", half, source="auto_split", note=note or "50:50 분배")
        _record(db, "kis_us", half, source="auto_split", note=note or "50:50 분배")
        return {"ok": True, "split": True, "kis_kr": half, "kis_us": half}

    if market not in VALID_KIS_MARKETS:
        raise HTTPException(400, f"market must be one of {VALID_KIS_MARKETS} or use split=true")
    new_id = _record(db, market, amount, source="manual", note=note)
    return {"ok": True, "id": new_id, "market": market, "amount": amount}


@router.post("/withdraw")
def withdraw(
    payload: dict = Body(...),
    _: UserResponse = Depends(get_current_user),
):
    """출금. amount 양수로 받고 내부에서 음수로 저장."""
    amount = float(payload.get("amount", 0))
    market = payload.get("market")
    if amount <= 0:
        raise HTTPException(400, "amount must be positive")
    if market not in VALID_KIS_MARKETS:
        raise HTTPException(400, f"market must be one of {VALID_KIS_MARKETS}")
    note = payload.get("note", "")

    db = get_db()
    new_id = _record(db, market, -amount, source="manual", note=note or "출금")
    return {"ok": True, "id": new_id, "market": market, "amount": -amount}


@router.post("/transfer")
def transfer(
    payload: dict = Body(...),
    _: UserResponse = Depends(get_current_user),
):
    """시장 간 자본 이동. from에서 빼고 to에 넣음 (DB 두 건)."""
    from_market = payload.get("from_market")
    to_market = payload.get("to_market")
    amount = float(payload.get("amount", 0))
    note = payload.get("note", "")

    if from_market == to_market:
        raise HTTPException(400, "from_market == to_market")
    if from_market not in VALID_KIS_MARKETS or to_market not in VALID_KIS_MARKETS:
        raise HTTPException(400, f"market must be in {VALID_KIS_MARKETS}")
    if amount <= 0:
        raise HTTPException(400, "amount must be positive")

    db = get_db()
    transfer_note = note or f"이동: {from_market} → {to_market}"
    out_id = _record(db, from_market, -amount, source="rebalance", note=transfer_note)
    in_id = _record(db, to_market, amount, source="rebalance", note=transfer_note)
    return {
        "ok": True,
        "out_id": out_id,
        "in_id": in_id,
        "from_market": from_market,
        "to_market": to_market,
        "amount": amount,
    }
