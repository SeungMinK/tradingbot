"""#278: 시장별 모니터링 종목 풀 + 활성 전략 (admin)."""

import logging

from fastapi import APIRouter, Depends

from cryptobot.api.auth import UserResponse, get_current_user
from cryptobot.api.deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/market-universe", tags=["market-universe"])


@router.get("")
def get_market_universe(_: UserResponse = Depends(get_current_user)):
    """시장별 모니터링 종목 + 활성 전략 + 매매 룰 표시."""
    db = get_db()

    # 코인 봇 — 화이트리스트 + 활성 전략
    wl_row = db.execute(
        "SELECT value FROM bot_config WHERE key='coin_whitelist'"
    ).fetchone()
    coin_whitelist = []
    if wl_row and dict(wl_row).get("value"):
        coin_whitelist = [c.strip() for c in dict(wl_row)["value"].split(",") if c.strip()]
    active_strat = db.execute(
        "SELECT name, display_name, default_params_json FROM strategies "
        "WHERE is_active=TRUE AND status='active' LIMIT 1"
    ).fetchone()
    coin_strategy = {
        "name": "bb_rsi_combined",
        "display_name": "볼린저+RSI 복합",
        "params_json": None,
    }
    if active_strat:
        d = dict(active_strat)
        coin_strategy = {
            "name": d["name"],
            "display_name": d["display_name"],
            "params_json": d.get("default_params_json"),
        }

    # KIS 한국 종목 풀 (코드에 하드코딩됨 — 추후 DB로 옮길 수 있음)
    from cryptobot.entrypoints.run_kis_kr import DEFAULT_KOSPI_UNIVERSE
    from cryptobot.entrypoints.run_kis_us import DEFAULT_US_UNIVERSE
    from cryptobot.bot.profit_threshold import get_thresholds

    kr_th = get_thresholds("kis_kr")
    us_th = get_thresholds("kis_us")

    return {
        "markets": [
            {
                "market": "upbit",
                "display_name": "🪙 코인 (Upbit)",
                "symbols": coin_whitelist,
                "symbol_count": len(coin_whitelist),
                "strategy": coin_strategy,
                "rules": {
                    "type": "strategy_module",
                    "description": "10개 전략 모듈 — 활성 전략의 매수 신호로 자동 매매",
                    "stop_loss_pct": -5.0,
                    "take_profit_via": "ROI 테이블 + 트레일링",
                },
            },
            {
                "market": "kis_kr",
                "display_name": "🇰🇷 한국주식 (KIS)",
                "symbols": list(DEFAULT_KOSPI_UNIVERSE),
                "symbol_count": len(DEFAULT_KOSPI_UNIVERSE),
                "strategy": {
                    "name": "kis_conservative",
                    "display_name": "KIS 보수적 전략 (#279)",
                    "params_json": None,
                },
                "rules": {
                    "type": "kis_conservative",
                    "description": (
                        "매수: RSI≤35 AND 가격<MA20 AND 가격>MA60×0.92 AND 거래량 OK. "
                        "매도: 손절(-3%) → 트레일링(-2%) → 추세 기반 익절. "
                        "24h 재매수 금지, 종목당 시드 30~40% 한도."
                    ),
                    "take_profit_pct": kr_th.take_profit_pct,
                    "stop_loss_pct": kr_th.stop_loss_pct,
                    "fee_guard_pct": kr_th.fee_guard_pct,
                },
            },
            {
                "market": "kis_us",
                "display_name": "🇺🇸 미국주식 (KIS)",
                "symbols": list(DEFAULT_US_UNIVERSE),
                "symbol_count": len(DEFAULT_US_UNIVERSE),
                "strategy": {
                    "name": "kis_conservative",
                    "display_name": "KIS 보수적 전략 (#279)",
                    "params_json": None,
                },
                "rules": {
                    "type": "kis_conservative",
                    "description": (
                        "매수: RSI≤35 AND 가격<MA20 AND 가격>MA60×0.92 AND 거래량 OK. "
                        "매도: 손절(-3%) → 트레일링(-2%) → 추세 기반 익절. "
                        "24h 재매수 금지, 종목당 시드 30~40% 한도."
                    ),
                    "take_profit_pct": us_th.take_profit_pct,
                    "stop_loss_pct": us_th.stop_loss_pct,
                    "fee_guard_pct": us_th.fee_guard_pct,
                },
            },
        ]
    }
