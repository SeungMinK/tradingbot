"""매매 내역 라우트.

NestJS의 TradeController와 동일.
"""

import logging

from fastapi import APIRouter, Depends, Query

from cryptobot.api.auth import UserResponse, get_current_user
from cryptobot.api.deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("")
def get_trades(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    coin: str | None = None,
    strategy: str | None = None,
    side: str | None = None,
    market: str | None = None,  # #266 시장 필터
    date_from: str | None = None,
    date_to: str | None = None,
    _: UserResponse = Depends(get_current_user),
):
    """매매 내역 조회 (페이지네이션 + 필터)."""
    db = get_db()
    conditions = []
    params: list = []

    if coin:
        conditions.append("t.coin = ?")
        params.append(coin)
    if strategy:
        conditions.append("t.strategy = ?")
        params.append(strategy)
    if side:
        conditions.append("t.side = ?")
        params.append(side)
    if market:
        # #266: market 컬럼은 #249에서 추가됨. NULL은 'upbit'로 간주.
        if market == "upbit":
            conditions.append("(t.market = 'upbit' OR t.market IS NULL)")
        else:
            conditions.append("t.market = ?")
            params.append(market)
    if date_from:
        conditions.append("DATE(t.timestamp) >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("DATE(t.timestamp) <= ?")
        params.append(date_to)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    offset = (page - 1) * limit

    # 총 개수
    total = db.execute(f"SELECT COUNT(*) FROM trades t {where}", tuple(params)).fetchone()[0]

    # 데이터 (trade_signals에서 confidence JOIN)
    rows = db.execute(
        f"""
        SELECT t.*, ts.confidence as signal_confidence
        FROM trades t
        LEFT JOIN trade_signals ts ON ts.trade_id = t.id AND ts.executed = TRUE
        {where}
        ORDER BY t.timestamp DESC, t.id DESC LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    ).fetchall()

    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit,
    }


@router.get("/stats")
def get_trade_stats(
    days: int = Query(7, ge=1, le=365),
    _: UserResponse = Depends(get_current_user),
):
    """매매 통계."""
    db = get_db()
    row = db.execute(
        """
        SELECT
            COUNT(*) as total_trades,
            SUM(CASE WHEN side='buy' THEN 1 ELSE 0 END) as buys,
            SUM(CASE WHEN side='sell' THEN 1 ELSE 0 END) as sells,
            SUM(CASE WHEN side='sell' AND profit_pct > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN side='sell' AND profit_pct <= 0 THEN 1 ELSE 0 END) as losses,
            AVG(CASE WHEN side='sell' THEN profit_pct END) as avg_profit_pct,
            SUM(CASE WHEN side='sell' THEN profit_krw ELSE 0 END) as total_profit_krw,
            SUM(fee_krw) as total_fees
        FROM trades
        WHERE timestamp >= datetime('now', ?)
        AND (trigger_reason IS NULL OR trigger_reason NOT LIKE '[BUG]%')
        """,
        (f"-{days} days",),
    ).fetchone()

    sells = (row["wins"] or 0) + (row["losses"] or 0)
    realized_profit = round(row["total_profit_krw"] or 0, 0)

    # 미실현 손익 계산 (보유 중인 코인)
    unrealized_profit = 0
    held = db.execute(
        """
        SELECT t.coin, t.price, t.amount, t.total_krw, t.fee_krw
        FROM trades t WHERE t.side = 'buy'
        AND NOT EXISTS (SELECT 1 FROM trades s WHERE s.buy_trade_id = t.id AND s.side = 'sell')
        """
    ).fetchall()

    if held:
        import pyupbit

        # 배치 API 호출 (N개 → 1개)
        coins = list(set(h["coin"] for h in held))
        try:
            prices = (
                pyupbit.get_current_price(coins) if len(coins) > 1 else {coins[0]: pyupbit.get_current_price(coins[0])}
            )
        except Exception as e:
            logger.warning("코인 가격 조회 실패: %s", e)
            prices = {}
        if prices:
            for h in held:
                current_price = prices.get(h["coin"])
                if current_price:
                    cost = h["total_krw"] + (h["fee_krw"] or 0)
                    value = h["amount"] * current_price
                    unrealized_profit += value - cost
    unrealized_profit = round(unrealized_profit, 0)

    return {
        "period_days": days,
        "total_trades": row["total_trades"] or 0,
        "buys": row["buys"] or 0,
        "sells": sells,
        "wins": row["wins"] or 0,
        "losses": row["losses"] or 0,
        "win_rate": round((row["wins"] or 0) / sells * 100, 1) if sells > 0 else 0,
        "avg_profit_pct": round(row["avg_profit_pct"] or 0, 2),
        "total_profit_krw": realized_profit,
        "unrealized_profit_krw": unrealized_profit,
        "total_pnl_krw": realized_profit + unrealized_profit,
        "total_fees": round(row["total_fees"] or 0, 0),
    }


@router.get("/daily")
def get_daily_returns(
    days: int = Query(30, ge=1, le=365),
    _: UserResponse = Depends(get_current_user),
):
    """일별 수익률."""
    db = get_db()
    rows = db.execute(
        """
        SELECT
            DATE(timestamp) as date,
            SUM(CASE WHEN side='sell' THEN profit_pct ELSE 0 END) as daily_pnl_pct,
            SUM(CASE WHEN side='sell' THEN profit_krw ELSE 0 END) as daily_pnl_krw,
            COUNT(*) as total_trades,
            COUNT(*) as trade_count,
            SUM(CASE WHEN side='sell' AND profit_krw > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN side='sell' THEN 1 ELSE 0 END) as sells
        FROM trades
        WHERE timestamp >= datetime('now', ?)
        AND (trigger_reason IS NULL OR trigger_reason NOT LIKE '[BUG]%')
        GROUP BY DATE(timestamp)
        ORDER BY date
        """,
        (f"-{days} days",),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        sells = d.pop("sells", 0) or 0
        wins = d.pop("wins", 0) or 0
        d["win_rate"] = round(wins / sells * 100, 1) if sells > 0 else 0
        d["daily_return_pct"] = d["daily_pnl_pct"]
        result.append(d)
    return result


@router.get("/{trade_id}")
def get_trade_detail(trade_id: int, _: UserResponse = Depends(get_current_user)):
    """매매 상세 조회."""
    db = get_db()
    row = db.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if row is None:
        return {"detail": "매매 내역을 찾을 수 없습니다"}
    return dict(row)
