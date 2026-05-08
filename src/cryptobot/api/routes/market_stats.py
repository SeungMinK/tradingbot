"""#254 6단계: 시장별 PnL 통계 (admin)."""

import logging

from fastapi import APIRouter, Depends

from cryptobot.api.auth import UserResponse, get_current_user
from cryptobot.api.deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/market-stats", tags=["market-stats"])


@router.get("")
def get_market_stats(_: UserResponse = Depends(get_current_user)):
    """시장별(upbit/kis_kr/kis_us) 매매 통계.

    DB.trades.market 컬럼 기반. KIS 봇 거래 시작 후 데이터 누적.
    """
    db = get_db()
    rows = db.execute(
        """
        SELECT COALESCE(market, 'upbit') AS market,
               COUNT(*) AS total,
               SUM(CASE WHEN side='buy' THEN 1 ELSE 0 END) AS buys,
               SUM(CASE WHEN side='sell' THEN 1 ELSE 0 END) AS sells,
               SUM(CASE WHEN side='sell' AND profit_pct > 0 THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN side='sell' AND profit_pct <= 0 THEN 1 ELSE 0 END) AS losses,
               COALESCE(SUM(CASE WHEN side='sell' THEN profit_krw END), 0) AS total_pnl,
               COALESCE(AVG(CASE WHEN side='sell' THEN profit_pct END), 0) AS avg_profit_pct
        FROM trades GROUP BY market
        """
    ).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        sells = d.get("sells", 0) or 0
        wins = d.get("wins", 0) or 0
        items.append({
            "market": d["market"],
            "total_trades": d.get("total", 0) or 0,
            "buys": d.get("buys", 0) or 0,
            "sells": sells,
            "wins": wins,
            "losses": d.get("losses", 0) or 0,
            "win_rate": round(wins / sells * 100, 1) if sells else 0,
            "total_pnl_krw": round(d.get("total_pnl", 0) or 0, 0),
            "avg_profit_pct": round(d.get("avg_profit_pct", 0) or 0, 2),
        })
    return {"markets": items}
