"""한국주식 봇 엔트리포인트.

KIS 한국주식 어댑터 + 코스피 우량주 풀 + 단순 매매 루프.
정규장(평일 09:00~15:30 KST)에만 동작. 점심시간 휴장 없음.

사용법:
    python -m cryptobot.entrypoints.run_kis_kr

Related: #246

다음 단계 (별도 이슈):
- 코인 봇의 멀티전략 자동 선택·AI 시장분석·리스크 관리 모듈을 시장 무관 형태로 일반화 후 통합
- 본 엔트리포인트는 1차 검증용 단순 봇 (단일 전략 + 종목별 60초 폴링)
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

# 매매 임계 (#250: 한국 주식은 거래세 0.18% 흡수 위해 4%+ 익절)
TAKE_PROFIT_PCT = 4.0
STOP_LOSS_PCT = -3.0
TICK_INTERVAL_SEC = 60


class KISKoreanBot:
    """KIS 한국주식 단순 봇.

    각 종목 60초마다 폴링 → 단순 추세 룰 매매.
    실제 매매 전 잔고/거래시간/리스크 체크.
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
        self._last_buy_price: dict[str, float] = {}  # 매수 평단 캐시

    def start(self) -> None:
        """봇 시작."""
        logger.info("=== KIS 한국주식 봇 시작 ===")
        logger.info("종목 풀: %s (%d개)", ", ".join(self._universe), len(self._universe))
        logger.info("모의투자: %s", config.kis.is_paper)
        logger.info("익절/손절: +%.1f%% / %.1f%%", TAKE_PROFIT_PCT, STOP_LOSS_PCT)

        if self._notifier.is_configured:
            self._notifier.notify_bot_status("[KIS_KR] 한국주식 봇 시작")

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

    def _tick(self) -> None:
        """매 틱 처리."""
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
        """종목 1개에 대한 단순 매매 판단.

        룰:
        - 보유 중: 익절(+TAKE_PROFIT_PCT%) 또는 손절(STOP_LOSS_PCT%) 도달 시 매도
        - 미보유: TODO — 다음 세션에서 코인 봇 strategies/ 모듈 통합 후 매수 신호 처리
        """
        price = self._exchange.get_current_price(symbol)
        holdings = self._exchange.get_balance(symbol)

        if holdings > 0:
            buy_price = self._last_buy_price.get(symbol)
            if buy_price is None or buy_price <= 0:
                # 매수 평단 캐시 미스 — 잔고 조회로 평단가 가져오기 (TODO)
                logger.debug("%s 매수 평단 미상 — 매도 판단 스킵", symbol)
                return

            pnl_pct = (price - buy_price) / buy_price * 100
            if pnl_pct >= TAKE_PROFIT_PCT:
                logger.info("[익절] %s +%.2f%% (매수 %.0f → 현재 %.0f)", symbol, pnl_pct, buy_price, price)
                self._sell(symbol, "take_profit", pnl_pct)
            elif pnl_pct <= STOP_LOSS_PCT:
                logger.info("[손절] %s %.2f%% (매수 %.0f → 현재 %.0f)", symbol, pnl_pct, buy_price, price)
                self._sell(symbol, "stop_loss", pnl_pct)
        # 매수 신호: 다음 세션에서 strategies/ 통합

    def _sell(self, symbol: str, reason: str, pnl_pct: float) -> None:
        """매도 + 기록."""
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
            strategy="simple_threshold",
            trigger_reason=reason,
            profit_pct=pnl_pct,
            order_uuid=result.order_uuid,
        )
        if self._notifier.is_configured:
            self._notifier.notify_trade(
                f"[KIS_KR][매도/{reason}] {symbol} {result.amount:.0f}주 "
                f"@ {result.price:,.0f}원 (수익률 {pnl_pct:+.2f}%)"
            )
        self._last_buy_price.pop(symbol, None)

    def _on_shutdown(self, *_args) -> None:
        logger.info("=== KIS 한국주식 봇 종료 신호 ===")
        self._running = False
        if self._notifier.is_configured:
            self._notifier.notify_bot_status("[KIS_KR] 한국주식 봇 종료")
        sys.exit(0)


def main() -> None:
    setup_logging()
    KISKoreanBot().start()


if __name__ == "__main__":
    main()
