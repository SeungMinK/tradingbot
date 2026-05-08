"""한국주식 봇 엔트리포인트.

KIS 한국주식 어댑터 + 코스피 우량주 풀 + 보수적 매매 룰 (#279).
정규장(평일 09:00~15:30 KST)에만 동작. 점심시간 휴장 없음.

매매 룰 (`bot.kis_strategy` 모듈):
- 매수: RSI≤35 AND 가격<MA20 AND 가격>MA60×0.92 AND 거래량 OK
- 매도: 손절(-3%) → 트레일링 스탑(-2% from peak) → 추세 기반 익절
- 종목당 시드의 30% 한도 (1주 단위, 우량주). 매수/매도 충돌은 분기 구조로 방지.

사용법:
    python -m cryptobot.entrypoints.run_kis_kr

Related: #246, #279
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
from cryptobot.exchange.kis_kr import KISKoreanExchange
from cryptobot.logging_config import setup_logging
from cryptobot.notifier.slack import SlackNotifier

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")

# 코스피 시총 상위 우량주 풀 (10종목 고정)
# 사용자 원칙: "굵직한 우량주", 알트성·소형주 제외
DEFAULT_KOSPI_UNIVERSE = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "035420",  # NAVER
    "035720",  # 카카오
    "005380",  # 현대차
    "373220",  # LG에너지솔루션
    "068270",  # 셀트리온
    "006400",  # 삼성SDI
    "105560",  # KB금융
    "005490",  # POSCO홀딩스
]

TICK_INTERVAL_SEC = 60

# 한국주식 보수적 파라미터 (작은 시드 대응 — 종목당 한도 40%)
KR_PARAMS = KISStrategyParams(
    rsi_oversold=35.0,
    take_profit_pct=4.0,
    stop_loss_pct=-3.0,
    trailing_stop_pct=-2.0,
    max_position_per_symbol_pct=40.0,  # 작은 시드 + 우량주 가격대 고려
)


class KISKoreanBot:
    """KIS 한국주식 보수적 봇 (#279).

    각 종목 60초마다 폴링 → kis_strategy 룰 매매.
    - 매수: 보수적 4중 조건. 시드의 max_position_per_symbol_pct% 한도, 1주 단위.
    - 매도: 손절/트레일링/추세 기반 익절.
    - 매수/매도 충돌은 분기(`if holdings > 0`)로 구조적 차단 — 같은 틱에 양쪽 평가 X.
    """

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
        self._exchange = KISKoreanExchange(
            token_manager=self._token_mgr,
            account_number=config.kis.account_number,
            account_product_code=config.kis.account_product_code,
            is_paper=config.kis.is_paper,
        )
        self._universe = DEFAULT_KOSPI_UNIVERSE
        self._running = False
        self._last_buy_price: dict[str, float] = {}
        self._highest_since_buy: dict[str, float] = {}  # 트레일링 스탑용

    def start(self) -> None:
        logger.info("=== KIS 한국주식 봇 시작 (#279 보수적 룰) ===")
        logger.info("종목 풀: %s (%d개)", ", ".join(self._universe), len(self._universe))
        logger.info("모의투자: %s", config.kis.is_paper)
        logger.info(
            "매수: RSI≤%.0f, 종목당 시드 %.0f%% 한도",
            KR_PARAMS.rsi_oversold,
            KR_PARAMS.max_position_per_symbol_pct,
        )
        logger.info(
            "매도: 손절 %.1f%% / 트레일링 %.1f%% / 익절 %.1f%%(+추세)",
            KR_PARAMS.stop_loss_pct,
            KR_PARAMS.trailing_stop_pct,
            KR_PARAMS.take_profit_pct,
        )

        if self._notifier.is_configured:
            self._notifier.notify_bot_status("[KIS_KR] 한국주식 봇 시작 (보수적 룰)")

        signal.signal(signal.SIGINT, self._on_shutdown)
        signal.signal(signal.SIGTERM, self._on_shutdown)
        self._running = True

        while self._running:
            try:
                self._tick()
            except Exception as e:
                logger.exception("틱 처리 중 예외: %s", e)
                if self._notifier.is_configured:
                    self._notifier.notify_error(f"[KIS_KR] 틱 예외: {e}")
            time.sleep(TICK_INTERVAL_SEC)

    def _is_trading_enabled(self) -> bool:
        """DB bot_config.kis_kr_trading_enabled 체크. 없으면 디폴트 enabled."""
        row = self._db.execute(
            "SELECT value FROM bot_config WHERE key = 'kis_kr_trading_enabled'"
        ).fetchone()
        if not row:
            return True
        return str(dict(row).get("value", "true")).lower() == "true"

    def _tick(self) -> None:
        if not self._is_trading_enabled():
            logger.debug("kis_kr 거래 DB에서 비활성. 스킵")
            return
        if not self._exchange.is_market_open():
            now = datetime.now(KST).strftime("%H:%M")
            logger.debug("정규장 외 시간 (%s KST). 스킵", now)
            return

        for symbol in self._universe:
            try:
                self._evaluate_symbol(symbol)
            except APIError as e:
                logger.warning("%s 평가 실패: %s", symbol, e)
            except Exception as e:
                logger.exception("%s 평가 중 예외: %s", symbol, e)

    def _evaluate_symbol(self, symbol: str) -> None:
        price = self._exchange.get_current_price(symbol)
        holdings = self._exchange.get_balance(symbol)

        if holdings > 0:
            self._evaluate_sell(symbol, price)
        else:
            self._evaluate_buy(symbol, price)

    def _evaluate_sell(self, symbol: str, price: float) -> None:
        buy_price = self._last_buy_price.get(symbol)
        if buy_price is None or buy_price <= 0:
            logger.debug("%s 매수 평단 미상 — 매도 판단 스킵", symbol)
            return

        prev_high = self._highest_since_buy.get(symbol, buy_price)
        if price > prev_high:
            self._highest_since_buy[symbol] = price
            prev_high = price

        df = None
        try:
            df = self._exchange.get_ohlcv(symbol, count=80)
        except APIError as e:
            logger.warning("%s OHLCV 조회 실패 (매도 판단은 단순 룰로): %s", symbol, e)

        signal_ = evaluate_sell(df, price, buy_price, prev_high, KR_PARAMS)
        if signal_.should_sell:
            pnl_pct = (price - buy_price) / buy_price * 100
            logger.info("[매도/%s] %s — %s", symbol, signal_.reason, "익절" if signal_.is_profit_taking else "손절/트레일링")
            self._sell(symbol, signal_.reason, pnl_pct)
        else:
            logger.debug("%s 보유 유지: %s", symbol, signal_.reason)

    def _evaluate_buy(self, symbol: str, price: float) -> None:
        try:
            df = self._exchange.get_ohlcv(symbol, count=80)
        except APIError as e:
            logger.warning("%s OHLCV 조회 실패 — 매수 판단 스킵: %s", symbol, e)
            return

        signal_ = evaluate_buy(df, price, KR_PARAMS)
        if not signal_.should_buy:
            logger.debug("%s 매수 미판정: %s", symbol, signal_.reason)
            return

        # 실제 KIS API 잔고 사용 (#279 후속): 가정된 시드가 아닌 실잔고 기준
        try:
            budget = self._exchange.get_balance("KRW")
        except APIError as e:
            logger.warning("KRW 예수금 조회 실패 — 매수 스킵: %s", e)
            return
        qty, size_reason = calc_position_size(
            available_budget=budget,
            current_price=price,
            fractional=False,
            params=KR_PARAMS,
        )
        if qty <= 0:
            logger.info("%s 매수 신호이나 사이즈 0 (잔고=%.0f원, %s) — 스킵", symbol, budget, size_reason)
            return

        logger.info(
            "[매수신호] %s @ %.0f원 — %s | conf=%.2f | %s",
            symbol,
            price,
            signal_.reason,
            signal_.confidence,
            size_reason,
        )
        self._buy(symbol, qty, price, signal_.reason)

    def _buy(self, symbol: str, qty: float, price: float, reason: str) -> None:
        result = self._exchange.buy_market(symbol, qty)
        if not result.success:
            logger.warning("매수 실패: %s — %s", symbol, result.error)
            return

        self._last_buy_price[symbol] = result.price
        self._highest_since_buy[symbol] = result.price
        self._recorder.record_trade(
            coin=symbol,
            market="kis_kr",
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
            self._notifier.notify_trade_message(
                f"[KIS_KR][매수] {symbol} {result.amount:.0f}주 @ {result.price:,.0f}원 — {reason}"
            )

    def _sell(self, symbol: str, reason: str, pnl_pct: float) -> None:
        result = self._exchange.sell_market(symbol)
        if not result.success:
            logger.warning("매도 실패: %s — %s", symbol, result.error)
            return

        self._recorder.record_trade(
            coin=symbol,
            market="kis_kr",
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
            self._notifier.notify_trade_message(
                f"[KIS_KR][매도] {symbol} {result.amount:.0f}주 "
                f"@ {result.price:,.0f}원 ({pnl_pct:+.2f}%) — {reason}"
            )
        self._last_buy_price.pop(symbol, None)
        self._highest_since_buy.pop(symbol, None)

    def _on_shutdown(self, *_args) -> None:
        logger.info("=== KIS 한국주식 봇 종료 신호 ===")
        self._running = False
        if self._notifier.is_configured:
            self._notifier.notify_bot_status("[KIS_KR] 한국주식 봇 종료")
        sys.exit(0)


def main() -> None:
    setup_logging("bot_kis_kr")
    if not config.kis.kr_enabled:
        logger.info("KIS_KR_ENABLED=false — 한국주식 봇 비활성. 종료.")
        sys.exit(0)
    KISKoreanBot().start()


if __name__ == "__main__":
    main()
