"""잔고 + 포지션 라우트."""

import logging

from fastapi import APIRouter, Depends, Query

from cryptobot.api.auth import UserResponse, get_current_user
from cryptobot.api.deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/balance", tags=["balance"])


@router.get("")
def get_balance(_: UserResponse = Depends(get_current_user)):
    """현재 잔고 + 보유 포지션 + 누적 입금액."""
    from cryptobot.bot.config import config
    from cryptobot.bot.trader import Trader

    trader = Trader()
    result = {
        "krw_balance": 0,
        "coin_balance": 0,
        "coin_value_krw": 0,
        "total_asset_krw": 0,
        "total_deposits_krw": 0,  # #218: 누적 입금액 (대시보드 손익 계산 기준)
        "api_connected": False,
    }

    # #218: capital_deposits 합산 — 추가 입금이 있어도 손익 기준이 정확.
    # 비어 있으면 첫 daily_reports.starting_balance fallback.
    try:
        db = get_db()
        dep_row = db.execute(
            "SELECT COALESCE(SUM(amount_krw), 0) AS total FROM capital_deposits WHERE currency = 'KRW'"
        ).fetchone()
        total_deposits = dict(dep_row)["total"] if dep_row else 0
        if total_deposits <= 0:
            first = db.execute(
                "SELECT starting_balance_krw FROM daily_reports ORDER BY date ASC LIMIT 1"
            ).fetchone()
            total_deposits = dict(first)["starting_balance_krw"] if first else 0
        result["total_deposits_krw"] = float(total_deposits)
    except Exception as e:
        logger.warning("누적 입금액 조회 실패: %s", e)

    if trader.is_ready:
        try:
            result["api_connected"] = True
            result["krw_balance"] = trader.get_balance_krw()
            result["coin_balance"] = trader.get_balance_coin(config.bot.coin)
            price = trader.get_current_price(config.bot.coin)
            result["coin_value_krw"] = result["coin_balance"] * price
            result["total_asset_krw"] = result["krw_balance"] + result["coin_value_krw"]
        except Exception as e:
            logger.warning("잔고 조회 실패: %s", e)
            pass

    return result


@router.get("/positions")
def get_positions(_: UserResponse = Depends(get_current_user)):
    """현재 보유 포지션 전체 (멀티코인 대응)."""

    db = get_db()
    # 미매도 매수 건 전체 조회
    rows = db.execute(
        """
        SELECT t.* FROM trades t
        WHERE t.side = 'buy'
        AND NOT EXISTS (SELECT 1 FROM trades s WHERE s.buy_trade_id = t.id AND s.side = 'sell')
        ORDER BY t.id DESC
        """
    ).fetchall()

    if not rows:
        return {"has_position": False, "positions": []}

    positions = []
    # 배치 가격 조회 (N개 코인 → 1 API)
    import pyupbit

    coins = list(set(dict(r)["coin"] for r in rows))
    try:
        prices = pyupbit.get_current_price(coins) if len(coins) > 1 else {coins[0]: pyupbit.get_current_price(coins[0])}
    except Exception:
        prices = {}

    for row in rows:
        trade = dict(row)
        coin = trade["coin"]
        current_price = (prices or {}).get(coin, 0) or 0
        if current_price and trade["price"]:
            unrealized_pnl_pct = (current_price - trade["price"]) / trade["price"] * 100
            unrealized_pnl_krw = (current_price - trade["price"]) * trade["amount"]
        else:
            unrealized_pnl_pct = 0
            unrealized_pnl_krw = 0

        positions.append(
            {
                **trade,
                "current_price": current_price,
                "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
                "unrealized_pnl_krw": round(unrealized_pnl_krw, 0),
            }
        )

    return {
        "has_position": True,
        "positions": positions,
        # 하위 호환: 첫 번째 포지션
        "position": positions[0] if positions else None,
    }


@router.get("/history")
def get_balance_history(
    days: int = Query(30, ge=1, le=365),
    _: UserResponse = Depends(get_current_user),
):
    """자산 추이 (daily_reports 기반)."""
    db = get_db()
    rows = db.execute(
        """
        SELECT date, ending_balance_krw, total_asset_value_krw,
               daily_return_pct, cumulative_return_pct
        FROM daily_reports
        WHERE date >= date('now', ?)
        ORDER BY date
        """,
        (f"-{days} days",),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/history/snapshots")
def get_balance_history_snapshots(
    hours: int = Query(1, ge=1, le=720),
    _: UserResponse = Depends(get_current_user),
):
    """시간 기반 자산 추이 (market_snapshots 기반).

    1시간~30일까지 지원. 데이터 포인트는 최대 200개로 제한.
    """
    db = get_db()
    rows = db.execute(
        """
        SELECT timestamp, price, market_state
        FROM market_snapshots
        WHERE timestamp >= datetime('now', ?)
        ORDER BY timestamp
        """,
        (f"-{hours} hours",),
    ).fetchall()

    if not rows:
        return []

    # 데이터 포인트가 너무 많으면 샘플링
    data = [dict(r) for r in rows]
    if len(data) > 200:
        step = len(data) // 200
        data = data[::step] + [data[-1]]

    return data
