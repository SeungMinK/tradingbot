"""미국주식 봇 엔트리포인트.

KIS 미국주식 어댑터 + 빅테크 우량주 풀 + 단순 매매 루프.
NY 정규장 09:30~16:00 (KST 22:30~06:00, 서머타임 23:30~06:00) 동작.

사용법:
    python -m cryptobot.entrypoints.run_kis_us

Related: #247
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from cryptobot.bot.config import config
from cryptobot.data.database import Database
from cryptobot.data.recorder import DataRecorder
from cryptobot.exceptions import APIError, ConfigError
from cryptobot.exchange.kis.auth import KISTokenManager
from cryptobot.exchange.kis_us import KISUSExchange
from cryptobot.logging_config import setup_logging
from cryptobot.notifier.slack import SlackNotifier

logger = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")

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

# #254 2단계: profit_threshold 모듈 lookup (단일 진실의 원천)
from cryptobot.bot.profit_threshold import get_thresholds as _get_thresholds  # noqa: E402

_US_THRESHOLDS = _get_thresholds("kis_us")
TAKE_PROFIT_PCT = _US_THRESHOLDS.take_profit_pct  # 5.0
STOP_LOSS_PCT = _US_THRESHOLDS.stop_loss_pct  # -3.0
TICK_INTERVAL_SEC = 60


class KISUSBot:
    """KIS 미국주식 단순 봇."""

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
        self._last_buy_price: dict[str, float] = {}

    def start(self) -> None:
        logger.info("=== KIS 미국주식 봇 시작 ===")
        logger.info("종목 풀: %s (%d개)", ", ".join(self._universe), len(self._universe))
        logger.info("모의투자: %s", config.kis.is_paper)
        logger.info("익절/손절: +%.1f%% / %.1f%%", TAKE_PROFIT_PCT, STOP_LOSS_PCT)

        if self._notifier.is_configured:
            self._notifier.notify_bot_status("[KIS_US] 미국주식 봇 시작")

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
        """단순 익절/손절 판단. 매수 신호는 다음 세션 strategies/ 통합 후."""
        price = self._exchange.get_current_price(symbol)
        holdings = self._exchange.get_balance(symbol)

        if holdings > 0:
            buy_price = self._last_buy_price.get(symbol)
            if buy_price is None or buy_price <= 0:
                logger.debug("%s 매수 평단 미상 — 매도 판단 스킵", symbol)
                return

            pnl_pct = (price - buy_price) / buy_price * 100
            if pnl_pct >= TAKE_PROFIT_PCT:
                logger.info("[익절] %s +%.2f%% ($%.2f → $%.2f)", symbol, pnl_pct, buy_price, price)
                self._sell(symbol, "take_profit", pnl_pct)
            elif pnl_pct <= STOP_LOSS_PCT:
                logger.info("[손절] %s %.2f%% ($%.2f → $%.2f)", symbol, pnl_pct, buy_price, price)
                self._sell(symbol, "stop_loss", pnl_pct)

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
            strategy="simple_threshold",
            trigger_reason=reason,
            profit_pct=pnl_pct,
            order_uuid=result.order_uuid,
        )
        if self._notifier.is_configured:
            self._notifier.notify_trade(
                f"[KIS_US][매도/{reason}] {symbol} {result.amount:.4f}주 @ ${result.price:.2f} (수익률 {pnl_pct:+.2f}%)"
            )
        self._last_buy_price.pop(symbol, None)

    def _on_shutdown(self, *_args) -> None:
        logger.info("=== KIS 미국주식 봇 종료 신호 ===")
        self._running = False
        if self._notifier.is_configured:
            self._notifier.notify_bot_status("[KIS_US] 미국주식 봇 종료")
        sys.exit(0)


def main() -> None:
    setup_logging()
    KISUSBot().start()


if __name__ == "__main__":
    main()
