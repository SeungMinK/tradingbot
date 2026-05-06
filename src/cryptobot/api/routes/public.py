"""공개 API — 인증 불필요, 비율 기반 데이터만 반환.

절대값(원, 수량)은 반환하지 않음. 비율(%)만 제공.
"""

import time
from collections import defaultdict

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from cryptobot.api.deps import get_db

router = APIRouter(prefix="/api/public", tags=["public"])

# Rate limit (IP당 30회/분)
_rate_limits: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT = 100
RATE_WINDOW = 60


def _check_rate_limit(request: Request) -> bool:
    """rate limit 체크. 초과 시 False."""
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    _rate_limits[ip] = [t for t in _rate_limits[ip] if now - t < RATE_WINDOW]
    if len(_rate_limits[ip]) >= RATE_LIMIT:
        return False
    _rate_limits[ip].append(now)
    return True


@router.get("/summary")
def get_public_summary(request: Request):
    """수익률 요약 — 승률, 수익률, 거래 수."""
    if not _check_rate_limit(request):
        return JSONResponse(status_code=429, content={"detail": "Too many requests"})

    db = get_db()

    # 전체 성과
    row = db.execute(
        """
        SELECT
            COUNT(*) as total_sells,
            SUM(CASE WHEN profit_krw > 0 THEN 1 ELSE 0 END) as wins,
            ROUND(AVG(profit_pct), 2) as avg_profit_pct,
            ROUND(AVG(CASE WHEN profit_pct > 0 THEN profit_pct END), 2) as avg_win_pct,
            ROUND(AVG(CASE WHEN profit_pct <= 0 THEN profit_pct END), 2) as avg_loss_pct
        FROM trades WHERE side = 'sell'
        AND (trigger_reason IS NULL OR trigger_reason NOT LIKE '[BUG]%')
        """
    ).fetchone()
    r = dict(row) if row else {}
    total = r.get("total_sells", 0) or 0
    wins = r.get("wins", 0) or 0
    avg_win = r.get("avg_win_pct") or 0
    avg_loss = abs(r.get("avg_loss_pct") or 0)

    # 오늘 성과
    today_row = db.execute(
        """
        SELECT
            COUNT(*) as sells,
            SUM(CASE WHEN profit_krw > 0 THEN 1 ELSE 0 END) as wins,
            ROUND(AVG(profit_pct), 2) as avg_pct
        FROM trades WHERE side = 'sell' AND DATE(timestamp) = DATE('now')
        """
    ).fetchone()
    t = dict(today_row) if today_row else {}
    today_sells = t.get("sells", 0) or 0
    today_wins = t.get("wins", 0) or 0

    rr_ratio = round(avg_loss / avg_win, 1) if avg_win > 0 else 0

    return {
        "total_trades": total,
        "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
        "avg_profit_pct": r.get("avg_profit_pct", 0) or 0,
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(-avg_loss, 2),
        "risk_reward_ratio": rr_ratio,
        "today_trades": today_sells,
        "today_win_rate": round(today_wins / today_sells * 100, 1) if today_sells > 0 else 0,
        "today_avg_pct": t.get("avg_pct", 0) or 0,
    }


@router.get("/trades")
def get_public_trades(request: Request, limit: int = Query(20, ge=1, le=100)):
    """최근 매매 — 금액 제거, 비중(%)만."""
    if not _check_rate_limit(request):
        return JSONResponse(status_code=429, content={"detail": "Too many requests"})

    db = get_db()
    rows = db.execute(
        """
        SELECT coin, side, strategy, trigger_reason, profit_pct,
            price, timestamp, hold_duration_minutes
        FROM trades
        ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()

    return [
        {
            "coin": dict(r)["coin"],
            "side": dict(r)["side"],
            "strategy": dict(r)["strategy"],
            "trigger_reason": dict(r)["trigger_reason"],
            "profit_pct": dict(r)["profit_pct"] if dict(r)["side"] == "sell" else None,
            "price": dict(r)["price"],
            "timestamp": dict(r)["timestamp"],
            "hold_minutes": dict(r)["hold_duration_minutes"],
        }
        for r in rows
    ]


@router.get("/portfolio")
def get_public_portfolio(request: Request):
    """보유 코인 비중 (%) — 절대값 없음."""
    if not _check_rate_limit(request):
        return JSONResponse(status_code=429, content={"detail": "Too many requests"})

    db = get_db()
    rows = db.execute(
        """
        SELECT coin, total_krw FROM trades t
        WHERE side = 'buy'
        AND NOT EXISTS (
            SELECT 1 FROM trades s WHERE s.buy_trade_id = t.id AND s.side = 'sell'
        )
        """
    ).fetchall()

    coin_total = sum(dict(r)["total_krw"] for r in rows)

    # KRW 잔고 — Trader 또는 DB 기반 역산
    krw = 0
    try:
        from cryptobot.bot.trader import Trader

        trader = Trader()
        if trader.is_ready:
            krw = trader.get_balance_krw()
    except Exception:
        pass
    # Trader 안 되면 초기자금(100000) - 코인투자액으로 추정
    if krw <= 0:
        initial = 100000
        krw = max(0, initial - coin_total)

    grand_total = coin_total + krw
    if grand_total <= 0:
        return {"positions": [], "total_coins": 0}

    positions = []
    positions.append({"coin": "KRW", "weight_pct": round(krw / grand_total * 100, 1)})
    for r in rows:
        d = dict(r)
        pct = round(d["total_krw"] / grand_total * 100, 1)
        positions.append({"coin": d["coin"], "weight_pct": pct})

    return {"positions": positions, "total_coins": len(positions)}


@router.get("/analysis")
def get_public_analysis(request: Request, limit: int = Query(3, ge=1, le=10)):
    """LLM 분석 요약 — 프롬프트/비용 제외."""
    if not _check_rate_limit(request):
        return JSONResponse(status_code=429, content={"detail": "Too many requests"})

    db = get_db()
    rows = db.execute(
        """
        SELECT output_market_state, output_aggression, output_reasoning, timestamp
        FROM llm_decisions ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()

    results = []
    for r in rows:
        d = dict(r)
        reasoning = d["output_reasoning"] or ""
        # 첫 문단만 (상세 근거는 비공개)
        summary = reasoning.split("\n\n")[0] if reasoning else ""
        results.append(
            {
                "market_state": d["output_market_state"],
                "aggression": d["output_aggression"],
                "summary": summary,
                "timestamp": d["timestamp"],
            }
        )

    return results


@router.get("/news")
def get_public_news(request: Request, limit: int = Query(20, ge=1, le=50)):
    """최근 뉴스 + F&G — 공개 데이터."""
    if not _check_rate_limit(request):
        return JSONResponse(status_code=429, content={"detail": "Too many requests"})

    db = get_db()

    news = db.execute(
        "SELECT title, source, sentiment_keyword, published_at, url FROM news_articles ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()

    fg = db.execute("SELECT value, classification, timestamp FROM fear_greed_index ORDER BY id DESC LIMIT 1").fetchone()

    return {
        "news": [dict(r) for r in news],
        "fear_greed": dict(fg) if fg else None,
    }


@router.get("/daily-returns")
def get_public_daily_returns(request: Request, days: int = Query(30, ge=1, le=90)):
    """일별 수익률 (%) — 금액 없음."""
    if not _check_rate_limit(request):
        return JSONResponse(status_code=429, content={"detail": "Too many requests"})

    db = get_db()
    rows = db.execute(
        """
        SELECT
            DATE(timestamp) as date,
            ROUND(SUM(CASE WHEN side='sell' THEN profit_pct ELSE 0 END), 2) as daily_pnl_pct,
            COUNT(*) as total_trades,
            SUM(CASE WHEN side='sell' AND profit_krw > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN side='sell' THEN 1 ELSE 0 END) as sells,
            ROUND(AVG(CASE WHEN side='sell' AND profit_pct > 0 THEN profit_pct END), 2) as avg_win,
            ROUND(AVG(CASE WHEN side='sell' AND profit_pct <= 0 THEN profit_pct END), 2) as avg_loss
        FROM trades
        WHERE timestamp >= datetime('now', ?)
        AND (trigger_reason IS NULL OR trigger_reason NOT LIKE '[BUG]%')
        GROUP BY DATE(timestamp) ORDER BY date
        """,
        (f"-{days} days",),
    ).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        sells = d.pop("sells", 0) or 0
        wins = d.pop("wins", 0) or 0
        avg_w = d.pop("avg_win", 0) or 0
        avg_l = abs(d.pop("avg_loss", 0) or 0)
        d["win_rate"] = round(wins / sells * 100, 1) if sells > 0 else 0
        d["risk_reward"] = round(avg_l / avg_w, 1) if avg_w > 0 else 0
        result.append(d)
    return result


@router.get("/monitoring-coins")
def get_public_monitoring_coins(request: Request):
    """현재 모니터링 중인 코인 + RSI/시장 상태.

    #228: 화이트리스트 모드면 화이트리스트만 반환. 아니면 최근 snapshot 기반.
    """
    if not _check_rate_limit(request):
        return JSONResponse(status_code=429, content={"detail": "Too many requests"})

    db = get_db()

    # 화이트리스트 모드 체크
    wl_enabled = db.execute(
        "SELECT value FROM bot_config WHERE key='coin_whitelist_enabled'"
    ).fetchone()
    if wl_enabled and dict(wl_enabled).get("value", "false").lower() == "true":
        wl_row = db.execute(
            "SELECT value FROM bot_config WHERE key='coin_whitelist'"
        ).fetchone()
        if wl_row and dict(wl_row).get("value"):
            whitelist = [c.strip() for c in dict(wl_row)["value"].split(",") if c.strip()]
            placeholders = ",".join("?" for _ in whitelist)
            rows = db.execute(
                f"""
                SELECT coin, price, rsi_14, market_state
                FROM market_snapshots
                WHERE id IN (SELECT MAX(id) FROM market_snapshots WHERE coin IN ({placeholders}) GROUP BY coin)
                ORDER BY coin
                """,
                whitelist,
            ).fetchall()
            return [
                {
                    "coin": dict(r)["coin"],
                    "price": dict(r)["price"],
                    "rsi": round(dict(r)["rsi_14"], 0) if dict(r)["rsi_14"] else None,
                    "market_state": dict(r)["market_state"],
                }
                for r in rows
            ]

    # 화이트리스트 OFF — 기존 동작 (최근 10분)
    rows = db.execute(
        """
        SELECT coin, price, rsi_14, market_state
        FROM market_snapshots
        WHERE id IN (SELECT MAX(id) FROM market_snapshots GROUP BY coin)
        AND timestamp >= datetime('now', '-10 minutes')
        ORDER BY coin
        """
    ).fetchall()
    return [
        {
            "coin": dict(r)["coin"],
            "price": dict(r)["price"],
            "rsi": round(dict(r)["rsi_14"], 0) if dict(r)["rsi_14"] else None,
            "market_state": dict(r)["market_state"],
        }
        for r in rows
    ]


@router.get("/strategies")
def get_public_strategies(request: Request):
    """사용 가능한 전략 목록 (파라미터 비공개)."""
    if not _check_rate_limit(request):
        return JSONResponse(status_code=429, content={"detail": "Too many requests"})

    db = get_db()
    rows = db.execute(
        """
        SELECT name, display_name, description, category, market_states,
            timeframe, difficulty, is_active
        FROM strategies WHERE is_available = TRUE ORDER BY is_active DESC, name
        """
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/strategy-stats")
def get_public_strategy_stats(request: Request):
    """전략별 성과 — 승률, 평균 수익률."""
    if not _check_rate_limit(request):
        return JSONResponse(status_code=429, content={"detail": "Too many requests"})

    db = get_db()
    rows = db.execute(
        """
        SELECT strategy,
            COUNT(*) as trades,
            SUM(CASE WHEN profit_krw > 0 THEN 1 ELSE 0 END) as wins,
            ROUND(AVG(profit_pct), 2) as avg_pct
        FROM trades WHERE side = 'sell'
        AND trigger_reason NOT LIKE '[BUG]%'
        GROUP BY strategy ORDER BY trades DESC
        """
    ).fetchall()

    return [
        {
            "strategy": dict(r)["strategy"],
            "trades": dict(r)["trades"],
            "win_rate": round((dict(r)["wins"] or 0) / dict(r)["trades"] * 100, 1) if dict(r)["trades"] > 0 else 0,
            "avg_pct": dict(r)["avg_pct"] or 0,
        }
        for r in rows
    ]
