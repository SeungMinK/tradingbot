"""미국주식 봇 엔트리포인트.

KIS 미국주식 어댑터 + 빅테크 우량주 풀 + 보수적 매매 룰 (#279).
NY 정규장 09:30~16:00 (KST 22:30~06:00, 서머타임 23:30~06:00) 동작.

매매 룰 (`bot.kis_strategy` 모듈):
- 매수: RSI≤35 AND 가격<MA20 AND 가격>MA60×0.92 AND 거래량 OK
- 매도: 손절(-3%) → 트레일링 스탑(-2%) → 추세 기반 익절
- 종목당 시드의 30% 한도 (소수점 매수 가능). 매수/매도 충돌은 분기 구조로 방지.

사용법:
    python -m cryptobot.entrypoints.run_kis_us

Related: #247, #279
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from cryptobot.bot.config import config
from cryptobot.bot.kis_strategy import (
    KISStrategyParams,
    calc_position_size,
    evaluate_buy,
    evaluate_sell,
)
from cryptobot.data.database import Database
from cryptobot.data.recorder import DataRecorder
from cryptobot.exceptions import APIError, ConfigError
from cryptobot.exchange.kis.auth import KISTokenManager
from cryptobot.exchange.kis_us import KISUSExchange
from cryptobot.logging_config import setup_logging
from cryptobot.notifier.slack import SlackNotifier

logger = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")

# 미국 빅테크 + 관심주 (10종목)
DEFAULT_US_UNIVERSE = [
    "NVDA",
    "TSLA",
    "AAPL",
    "MSFT",
    "GOOGL",
    "META",
    "AMZN",
    "AMD",
    "COIN",
    "MSTR",
]

TICK_INTERVAL_SEC = 60

# 미국주식 보수적 파라미터 (소수점 매수 가능 → 30% 그대로)
US_PARAMS = KISStrategyParams(
    rsi_oversold=35.0,
    take_profit_pct=5.0,  # 미국은 환전 스프레드 추가 → 익절 임계 약간 높임
    stop_loss_pct=-3.0,
    trailing_stop_pct=-2.0,
    max_position_per_symbol_pct=30.0,
)


class KISUSBot:
    """KIS 미국주식 보수적 봇 (#279)."""

    def __init__(self) -> None:
        if not config.kis.is_configured:
            raise ConfigError("KIS 설정 미완료. .env에 KIS_APP_KEY/KIS_APP_SECRET/KIS_ACCOUNT_NUMBER 설정 필요.")

        self._db = Database(config.bot.db_path)
        self._db.initialize()
        self._recorder = DataRecorder(self._db)
        self._notifier = SlackNotifier()

        self._token_mgr = KISTokenManager(
            app_key=config.kis.app_key,
            app_secret=config.kis.app_secret,
            is_paper=config.kis.is_paper,
        )
        self._exchange = KISUSExchange(
            token_manager=self._token_mgr,
            account_number=config.kis.account_number,
            account_product_code=config.kis.account_product_code,
            is_paper=config.kis.is_paper,
        )
        self._universe = DEFAULT_US_UNIVERSE
        self._running = False
        self._last_buy_price: dict[str, float] = {}  # USD 기준
        self._highest_since_buy: dict[str, float] = {}

    def start(self) -> None:
        logger.info("=== KIS 미국주식 봇 시작 (#279 보수적 룰) ===")
        logger.info("종목 풀: %s (%d개)", ", ".join(self._universe), len(self._universe))
        logger.info("모의투자: %s", config.kis.is_paper)
        logger.info(
            "매수: RSI≤%.0f, 종목당 시드 %.0f%% 한도 (소수점)",
            US_PARAMS.rsi_oversold,
            US_PARAMS.max_position_per_symbol_pct,
        )
        logger.info(
            "매도: 손절 %.1f%% / 트레일링 %.1f%% / 익절 %.1f%%(+추세)",
            US_PARAMS.stop_loss_pct,
            US_PARAMS.trailing_stop_pct,
            US_PARAMS.take_profit_pct,
        )

        if self._notifier.is_configured:
            self._notifier.notify_bot_status("[KIS_US] 미국주식 봇 시작 (보수적 룰)")

        signal.signal(signal.SIGINT, self._on_shutdown)
        signal.signal(signal.SIGTERM, self._on_shutdown)
        self._running = True

        while self._running:
            try:
                self._tick()
            except Exception as e:
                logger.exception("틱 처리 중 예외: %s", e)
                if self._notifier.is_configured:
                    self._notifier.notify_error(f"[KIS_US] 틱 예외: {e}")
            time.sleep(TICK_INTERVAL_SEC)

    def _tick(self) -> None:
        if not self._exchange.is_market_open():
            ny = datetime.now(NY).strftime("%H:%M")
            logger.debug("미국 정규장 외 (%s NY). 스킵", ny)
            return

        for symbol in self._universe:
            try:
                self._evaluate_symbol(symbol)
            except APIError as e:
                logger.warning("%s 평가 실패: %s", symbol, e)
            except Exception as e:
                logger.exception("%s 평가 중 예외: %s", symbol, e)

    def _evaluate_symbol(self, symbol: str) -> None:
        price_usd = self._exchange.get_current_price(symbol)
        holdings = self._exchange.get_balance(symbol)

        if holdings > 0:
            self._evaluate_sell(symbol, price_usd)
        else:
            self._evaluate_buy(symbol, price_usd)

    def _evaluate_sell(self, symbol: str, price_usd: float) -> None:
        buy_price = self._last_buy_price.get(symbol)
        if buy_price is None or buy_price <= 0:
            logger.debug("%s 매수 평단 미상 — 매도 판단 스킵", symbol)
            return

        prev_high = self._highest_since_buy.get(symbol, buy_price)
        if price_usd > prev_high:
            self._highest_since_buy[symbol] = price_usd
            prev_high = price_usd

        df = None
        try:
            df = self._exchange.get_ohlcv(symbol, count=80)
        except APIError as e:
            logger.warning("%s OHLCV 조회 실패 (매도 판단은 단순 룰로): %s", symbol, e)

        signal_ = evaluate_sell(df, price_usd, buy_price, prev_high, US_PARAMS)
        if signal_.should_sell:
            pnl_pct = (price_usd - buy_price) / buy_price * 100
            logger.info(
                "[매도/%s] %s — %s",
                symbol,
                signal_.reason,
                "익절" if signal_.is_profit_taking else "손절/트레일링",
            )
            self._sell(symbol, signal_.reason, pnl_pct)
        else:
            logger.debug("%s 보유 유지: %s", symbol, signal_.reason)

    def _evaluate_buy(self, symbol: str, price_usd: float) -> None:
        try:
            df = self._exchange.get_ohlcv(symbol, count=80)
        except APIError as e:
            logger.warning("%s OHLCV 조회 실패 — 매수 판단 스킵: %s", symbol, e)
            return

        signal_ = evaluate_buy(df, price_usd, US_PARAMS)
        if not signal_.should_buy:
            logger.debug("%s 매수 미판정: %s", symbol, signal_.reason)
            return

        # 실제 KIS API USD 예수금 사용 (#279 후속): 환율 고정값 제거
        try:
            budget_usd = self._exchange.get_balance("USD")
        except APIError as e:
            logger.warning("USD 예수금 조회 실패 — 매수 스킵: %s", e)
            return
        qty, size_reason = calc_position_size(
            available_budget=budget_usd,
            current_price=price_usd,
            fractional=True,
            params=US_PARAMS,
        )
        if qty <= 0:
            logger.info("%s 매수 신호이나 사이즈 0 ($%.2f USD, %s) — 스킵", symbol, budget_usd, size_reason)
            return

        logger.info(
            "[매수신호] %s @ $%.2f — %s | conf=%.2f | %s",
            symbol,
            price_usd,
            signal_.reason,
            signal_.confidence,
            size_reason,
        )
        self._buy(symbol, qty, price_usd, signal_.reason)

    def _buy(self, symbol: str, qty: float, price_usd: float, reason: str) -> None:
        result = self._exchange.buy_market(symbol, qty)
        if not result.success:
            logger.warning("매수 실패: %s — %s", symbol, result.error)
            return

        self._last_buy_price[symbol] = result.price
        self._highest_since_buy[symbol] = result.price
        self._recorder.record_trade(
            coin=symbol,
            market="kis_us",
            side="buy",
            price=result.price,
            amount=result.amount,
            total_krw=result.total_krw,
            fee_krw=result.fee_krw,
            strategy="kis_conservative",
            trigger_reason=reason,
            order_uuid=result.order_uuid,
        )
        if self._notifier.is_configured:
            self._notifier.notify_trade(
                f"[KIS_US][매수] {symbol} {result.amount:.4f}주 @ ${result.price:.2f} — {reason}"
            )

    def _sell(self, symbol: str, reason: str, pnl_pct: float) -> None:
        result = self._exchange.sell_market(symbol)
        if not result.success:
            logger.warning("매도 실패: %s — %s", symbol, result.error)
            return

        self._recorder.record_trade(
            coin=symbol,
            market="kis_us",
            side="sell",
            price=result.price,
            amount=result.amount,
            total_krw=result.total_krw,
            fee_krw=result.fee_krw,
            strategy="kis_conservative",
            trigger_reason=reason,
            profit_pct=pnl_pct,
            order_uuid=result.order_uuid,
        )
        if self._notifier.is_configured:
            self._notifier.notify_trade(
                f"[KIS_US][매도] {symbol} {result.amount:.4f}주 @ ${result.price:.2f} ({pnl_pct:+.2f}%) — {reason}"
            )
        self._last_buy_price.pop(symbol, None)
        self._highest_since_buy.pop(symbol, None)

    def _on_shutdown(self, *_args) -> None:
        logger.info("=== KIS 미국주식 봇 종료 신호 ===")
        self._running = False
        if self._notifier.is_configured:
            self._notifier.notify_bot_status("[KIS_US] 미국주식 봇 종료")
        sys.exit(0)


def main() -> None:
    setup_logging("bot_kis_us")
    KISUSBot().start()


if __name__ == "__main__":
    main()
