"""#274: 시장별 동적 예산 계산.

초기 시드(.env)에 누적 실현 손익을 더하고 보유 평가액을 빼서 현재 사용 가능 예산을 구한다.
즉 수익 내면 자연스럽게 시드가 커지고, 손실 나면 시드가 작아짐.

설계 의도:
- 초기 시드: KIS_KR_BUDGET_KRW / KIS_US_BUDGET_KRW (환경변수)
- 동적 가용 예산: seed + 실현_PnL - 보유_원가
- 봇이 매수 직전 이 함수로 예산 확인

다른 시장(코인)은 거래소 잔고 직접 조회 (Trader.get_balance_krw)로 충분.
KIS는 한국+미국이 같은 계좌라 자체 추적이 필요.
"""

from __future__ import annotations

from cryptobot.bot.config import config
from cryptobot.data.database import Database


def get_seed_for_market(market: str) -> float:
    """시장별 초기 시드 (KRW)."""
    if market == "kis_kr":
        return float(config.kis.kr_budget_krw)
    if market == "kis_us":
        return float(config.kis.us_budget_krw)
    return 0.0


def get_available_budget(db: Database, market: str) -> float:
    """시장별 현재 사용 가능 예산.

    available = seed + 실현_PnL - 보유_원가

    Args:
        db: 데이터베이스
        market: 'kis_kr' / 'kis_us'

    Returns:
        매수 가능한 KRW 금액 (음수면 0으로 클램프).
    """
    seed = get_seed_for_market(market)

    # 실현 PnL (sell만)
    pnl_row = db.execute(
        "SELECT COALESCE(SUM(profit_krw), 0) AS s FROM trades "
        "WHERE market = ? AND side = 'sell'",
        (market,),
    ).fetchone()
    realized_pnl = float(dict(pnl_row)["s"] or 0)

    # 보유 평가 (미매도 매수의 원가 + 수수료)
    held_row = db.execute(
        """
        SELECT COALESCE(SUM(total_krw + COALESCE(fee_krw, 0)), 0) AS s
        FROM trades t WHERE market = ? AND side = 'buy'
        AND NOT EXISTS (
            SELECT 1 FROM trades s WHERE s.buy_trade_id = t.id AND s.side = 'sell'
        )
        """,
        (market,),
    ).fetchone()
    held_cost = float(dict(held_row)["s"] or 0)

    available = seed + realized_pnl - held_cost
    return max(0.0, available)


def get_market_budget_status(db: Database, market: str) -> dict:
    """예산 상태 디버그/표시용."""
    seed = get_seed_for_market(market)
    pnl_row = db.execute(
        "SELECT COALESCE(SUM(profit_krw), 0) AS s FROM trades "
        "WHERE market = ? AND side = 'sell'",
        (market,),
    ).fetchone()
    realized_pnl = float(dict(pnl_row)["s"] or 0)

    held_row = db.execute(
        """
        SELECT COALESCE(SUM(total_krw + COALESCE(fee_krw, 0)), 0) AS s
        FROM trades t WHERE market = ? AND side = 'buy'
        AND NOT EXISTS (
            SELECT 1 FROM trades s WHERE s.buy_trade_id = t.id AND s.side = 'sell'
        )
        """,
        (market,),
    ).fetchone()
    held_cost = float(dict(held_row)["s"] or 0)

    return {
        "market": market,
        "seed": seed,
        "realized_pnl": realized_pnl,
        "held_cost": held_cost,
        "available": max(0.0, seed + realized_pnl - held_cost),
        "current_capital": seed + realized_pnl,  # 자체 시드 = 시드 + 누적 손익
    }
