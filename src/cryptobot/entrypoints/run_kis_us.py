"""미국주식 봇 엔트리포인트 (#279, #285).

KIS 미국주식 어댑터 + 사용자 정의 풀 + 보수적/단타 매매 룰.
NY 정규장 09:30~16:00 (KST 22:30~06:00, 서머타임 23:30~06:00) 동작.

USD 기반 거래:
- 봇은 KIS API USD 예수금만 보고 매수
- 매수/매도 모두 USD로 진행 — 환전 비용 0
- 사용자가 KRW→USD 환전을 직접 1회만 해놓으면 됨

매매 룰 (`bot.kis_strategy` 모듈):
- 매수: RSI≤rsi_oversold AND 가격<MA20 AND 가격>MA60×0.92 AND 거래량 OK
- 매도: 손절 → 트레일링 → 추세 기반 익절
- 종목당 한도: 100% / N (N=풀 종목 수, 자동)
- 단타모드: 마감 30분 전 매수 금지, 10분 전 강제 청산

환경변수:
- KIS_US_ENABLED=true/false (기본 true)
- KIS_US_UNIVERSE=SNXX,SNDK,NVDA (콤마 구분, 기본 = DEFAULT_US_UNIVERSE)
- KIS_US_DAY_TRADING=true/false (기본 false)
- KIS_US_TAKE_PROFIT_PCT, KIS_US_STOP_LOSS_PCT, KIS_US_TRAILING_PCT (선택)
- KIS_US_REBUY_COOLDOWN_SEC (기본 0 = 없음). 매도 후 같은 종목 재매수까지 최소 초.
  단타 노이즈 매매 회피용. 0이면 다음 틱부터 즉시 재매수 가능.
- KIS_US_TICK_INTERVAL_SEC (기본 30). 봇 폴링 주기. 단타는 빠른 반응(30초)이 적절.
  너무 짧으면(<15초) KIS rate limit 우려.

사용법:
    python -m cryptobot.entrypoints.run_kis_us

Related: #247, #279, #285
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from datetime import datetime, time as dtime
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
from cryptobot.exchange.kis_us import INTEGER_ONLY_TICKERS, KISUSExchange
from cryptobot.logging_config import setup_logging
from cryptobot.notifier.slack import SlackNotifier

logger = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
NY_CLOSE_TIME = dtime(16, 0)  # 미국 정규장 마감 16:00 NY

# 디폴트 풀 (#285): 빅테크/반도체/레버리지/크립토/EV/AI 분산
# env KIS_US_UNIVERSE 로 오버라이드 가능
DEFAULT_US_UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",          # 빅테크
    "NVDA", "AMD", "TSM", "AVGO", "ASML",             # 반도체
    "SNDK", "SNXX",                                    # 메모리/레버리지
    "COIN", "MSTR", "HOOD",                            # 크립토 노출
    "TSLA", "RIVN",                                    # EV
    "PLTR", "ARM", "NFLX",                             # AI/소프트웨어
]

DEFAULT_TICK_INTERVAL_SEC = 30  # #295: 단타용 빠른 반응 (기존 60→30)


def _parse_universe(db: "Database | None" = None) -> list[str]:
    """종목 풀 결정. 우선순위: DB(kis_us_symbols.enabled) > env KIS_US_UNIVERSE > DEFAULT.

    #297: DB 기반 종목 관리 — admin에서 토글하면 봇 재시작 없이 다음 틱부터 반영.
    DB가 비어있거나 enabled 종목 0건이면 env fallback.
    """
    if db is not None:
        try:
            rows = db.execute(
                "SELECT ticker FROM kis_us_symbols WHERE enabled = 1 ORDER BY ticker"
            ).fetchall()
            if rows:
                return [dict(r)["ticker"] for r in rows]
        except Exception:
            pass  # DB 조회 실패 시 env로 fallback

    raw = os.getenv("KIS_US_UNIVERSE", "").strip()
    if not raw:
        return list(DEFAULT_US_UNIVERSE)
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _build_params(universe_size: int) -> KISStrategyParams:
    """env 기반 전략 파라미터 생성.

    종목당 한도는 100/N로 자동 (N=풀 크기).
    단타모드 ON일 때 손절/익절/트레일링 디폴트가 자동으로 단타용으로 좁아짐:
    - 스윙: TP +10% / SL -10% / TR -3%
    - 단타: TP +2.5% / SL -1.5% / TR -1.0% (하루 안에 발동 가능한 폭)
    env로 명시적 오버라이드 가능 (KIS_US_TAKE_PROFIT_PCT 등).
    """
    if universe_size <= 0:
        universe_size = 1
    is_day_trading = os.getenv("KIS_US_DAY_TRADING", "false").lower() == "true"
    # 모드별 디폴트
    default_tp = "2.5" if is_day_trading else "10"
    default_sl = "-1.5" if is_day_trading else "-10"
    default_tr = "-1.0" if is_day_trading else "-3"
    return KISStrategyParams(
        rsi_oversold=float(os.getenv("KIS_US_RSI_OVERSOLD", "35")),
        rsi_overbought=float(os.getenv("KIS_US_RSI_OVERBOUGHT", "70")),
        take_profit_pct=float(os.getenv("KIS_US_TAKE_PROFIT_PCT", default_tp)),
        stop_loss_pct=float(os.getenv("KIS_US_STOP_LOSS_PCT", default_sl)),
        trailing_stop_pct=float(os.getenv("KIS_US_TRAILING_PCT", default_tr)),
        max_position_per_symbol_pct=100.0 / universe_size,
        day_trading_mode=is_day_trading,
        no_buy_window_minutes_before_close=int(os.getenv("KIS_US_NO_BUY_BEFORE_CLOSE_MIN", "30")),
        force_sell_window_minutes_before_close=int(os.getenv("KIS_US_FORCE_SELL_BEFORE_CLOSE_MIN", "10")),
    )


class KISUSBot:
    """KIS 미국주식 봇 (#279, #285).

    USD 기반 거래 — 환전 0, 사용자가 미리 KRW→USD 환전.
    풀과 파라미터는 환경변수로 제어.
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
        self._exchange = KISUSExchange(
            token_manager=self._token_mgr,
            account_number=config.kis.account_number,
            account_product_code=config.kis.account_product_code,
            is_paper=config.kis.is_paper,
        )
        self._universe = _parse_universe(self._db)  # DB 우선
        self._params = _build_params(len(self._universe))
        self._rebuy_cooldown_sec = int(os.getenv("KIS_US_REBUY_COOLDOWN_SEC", "0"))
        self._tick_interval_sec = max(15, int(os.getenv("KIS_US_TICK_INTERVAL_SEC", str(DEFAULT_TICK_INTERVAL_SEC))))
        self._heartbeat_every_n_ticks = max(1, 300 // self._tick_interval_sec)  # ~5분에 한 번 살아있음 핑
        self._tick_count = 0
        self._running = False
        self._last_buy_price: dict[str, float] = {}  # USD 기준
        self._highest_since_buy: dict[str, float] = {}
        self._last_sell_at: dict[str, float] = {}  # 종목별 마지막 매도 timestamp (epoch)

    def start(self) -> None:
        mode = "단타(데일리)" if self._params.day_trading_mode else "스윙"
        logger.info("=== KIS 미국주식 봇 시작 — %s 모드 ===", mode)
        logger.info("종목 풀 (%d개): %s", len(self._universe), ", ".join(self._universe))
        logger.info("모의투자: %s", config.kis.is_paper)
        logger.info(
            "매수: RSI≤%.0f, 종목당 USD %.1f%% 한도 (= 풀 / N)",
            self._params.rsi_oversold,
            self._params.max_position_per_symbol_pct,
        )
        logger.info(
            "매도: 손절 %.1f%% / 트레일링 %.1f%% / 익절 %.1f%%",
            self._params.stop_loss_pct,
            self._params.trailing_stop_pct,
            self._params.take_profit_pct,
        )
        if self._params.day_trading_mode:
            logger.info(
                "단타: 마감 %d분 전 매수금지, %d분 전 강제청산",
                self._params.no_buy_window_minutes_before_close,
                self._params.force_sell_window_minutes_before_close,
            )
        logger.info("폴링 주기: %d초", self._tick_interval_sec)

        if self._notifier.is_configured:
            self._notifier.notify_bot_status(f"[KIS_US] 미국주식 봇 시작 ({mode})")

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
            time.sleep(self._tick_interval_sec)

    def _is_trading_enabled(self) -> bool:
        """DB bot_config.kis_us_trading_enabled 체크. 없으면 디폴트 enabled."""
        row = self._db.execute(
            "SELECT value FROM bot_config WHERE key = 'kis_us_trading_enabled'"
        ).fetchone()
        if not row:
            return True
        return str(dict(row).get("value", "true")).lower() == "true"

    def _tick(self) -> None:
        self._tick_count += 1
        if not self._is_trading_enabled():
            logger.debug("kis_us 거래 DB에서 비활성. 스킵")
            return
        if not self._exchange.is_market_open():
            ny = datetime.now(NY).strftime("%H:%M")
            logger.debug("미국 정규장 외 (%s NY). 스킵", ny)
            return

        # ~5분에 한 번 살아있음 핑 + DB 종목 풀 재로딩 (admin 토글 반영, #297)
        if self._tick_count % self._heartbeat_every_n_ticks == 0:
            ny = datetime.now(NY).strftime("%H:%M")
            new_universe = _parse_universe(self._db)
            universe_changed = new_universe != self._universe
            if universe_changed:
                logger.info("종목 풀 변경: %s → %s", self._universe, new_universe)
                self._universe = new_universe
                self._params = _build_params(len(self._universe)) if self._universe else self._params
            try:
                usd = self._exchange.get_balance("USD")
                logger.info(
                    "[heartbeat] tick=%d, NY %s, USD=$%.2f, 풀=%s",
                    self._tick_count, ny, usd, ",".join(self._universe) or "(empty)",
                )
            except Exception:
                logger.info("[heartbeat] tick=%d, NY %s (잔고 조회 실패)", self._tick_count, ny)

        for symbol in self._universe:
            try:
                self._evaluate_symbol(symbol)
            except APIError as e:
                logger.warning("%s 평가 실패: %s", symbol, e)
            except Exception as e:
                logger.exception("%s 평가 중 예외: %s", symbol, e)

    def _minutes_to_close(self) -> float:
        """미국 정규장 마감까지 분. 음수면 이미 마감."""
        ny_now = datetime.now(NY)
        close_dt = ny_now.replace(
            hour=NY_CLOSE_TIME.hour,
            minute=NY_CLOSE_TIME.minute,
            second=0,
            microsecond=0,
        )
        if ny_now > close_dt:
            return -1.0
        return (close_dt - ny_now).total_seconds() / 60.0

    def _evaluate_symbol(self, symbol: str) -> None:
        price_usd = self._exchange.get_current_price(symbol)
        holdings = self._exchange.get_balance(symbol)

        if holdings > 0:
            # 단타모드: 마감 임박 강제 청산 (가격/룰 무관 시장가 매도)
            if self._params.day_trading_mode:
                mins = self._minutes_to_close()
                if 0 <= mins <= self._params.force_sell_window_minutes_before_close:
                    buy_price = self._last_buy_price.get(symbol, price_usd)
                    pnl_pct = (price_usd - buy_price) / buy_price * 100 if buy_price > 0 else 0.0
                    logger.info(
                        "[데일리 강제청산] %s @ $%.2f (%.2f%%, 마감 %.0f분 전)",
                        symbol, price_usd, pnl_pct, mins,
                    )
                    self._sell(symbol, "day_trading_close", pnl_pct)
                    return
            self._evaluate_sell(symbol, price_usd)
        else:
            # 단타모드: 마감 임박 매수 금지 (남은 시간으로 익절·손절 발동 어려움)
            if self._params.day_trading_mode:
                mins = self._minutes_to_close()
                if 0 <= mins <= self._params.no_buy_window_minutes_before_close:
                    logger.debug(
                        "%s 마감 임박 매수금지 (%.0f분 남음, 임계 %d분)",
                        symbol, mins, self._params.no_buy_window_minutes_before_close,
                    )
                    return
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

        signal_ = evaluate_sell(df, price_usd, buy_price, prev_high, self._params)
        if signal_.should_sell:
            pnl_pct = (price_usd - buy_price) / buy_price * 100
            logger.info(
                "[매도/%s] %s — %s",
                symbol, signal_.reason,
                "익절" if signal_.is_profit_taking else "손절/트레일링",
            )
            self._sell(symbol, signal_.reason, pnl_pct)
        else:
            logger.debug("%s 보유 유지: %s", symbol, signal_.reason)

    def _record_evaluation(
        self,
        symbol: str,
        price: float,
        signal_,
        df=None,
        holds_already: bool = False,
    ) -> None:
        """매 틱 매수 평가 결과 DB 기록 (#297-2). 사용자 가시성용."""
        from cryptobot.bot.kis_strategy import _calc_ma, _calc_rsi

        rsi = ma20 = ma60 = None
        if df is not None:
            try:
                rsi = _calc_rsi(df["close"], 14)
                ma20 = _calc_ma(df["close"], 20)
                ma60 = _calc_ma(df["close"], 60)
            except Exception:
                pass
        try:
            self._db.execute(
                "INSERT INTO kis_us_evaluations "
                "(ticker, price, rsi, ma20, ma60, should_buy, reason, confidence, holds_already) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    symbol, float(price), rsi, ma20, ma60,
                    1 if (signal_ and getattr(signal_, "should_buy", False)) else 0,
                    getattr(signal_, "reason", "") if signal_ else "",
                    getattr(signal_, "confidence", 0.0) if signal_ else 0.0,
                    1 if holds_already else 0,
                ),
            )
            self._db.commit()
        except Exception as e:
            logger.debug("evaluation 기록 실패: %s", e)

    def _evaluate_buy(self, symbol: str, price_usd: float) -> None:
        # 짧은 재매수 쿨다운 (옵션) — 매도 직후 노이즈 매매 회피
        if self._rebuy_cooldown_sec > 0:
            last_sell_ts = self._last_sell_at.get(symbol, 0.0)
            elapsed = time.time() - last_sell_ts
            if last_sell_ts > 0 and elapsed < self._rebuy_cooldown_sec:
                logger.debug(
                    "%s 재매수 쿨다운 (%ds 중 %ds 경과)",
                    symbol, self._rebuy_cooldown_sec, int(elapsed),
                )
                return

        try:
            df = self._exchange.get_ohlcv(symbol, count=80)
        except APIError as e:
            logger.warning("%s OHLCV 조회 실패 — 매수 판단 스킵: %s", symbol, e)
            return

        signal_ = evaluate_buy(df, price_usd, self._params)
        # 매 틱 평가 결과 DB 기록 (사용자 가시성)
        self._record_evaluation(symbol, price_usd, signal_, df=df, holds_already=False)
        if not signal_.should_buy:
            logger.debug("%s 매수 미판정: %s", symbol, signal_.reason)
            return

        try:
            budget_usd = self._exchange.get_balance("USD")
        except APIError as e:
            logger.warning("USD 예수금 조회 실패 — 매수 스킵: %s", e)
            return
        # 매매단위 1주 종목(레버리지 ETF 등)은 fractional=False로 정수 매수만
        is_fractional = symbol not in INTEGER_ONLY_TICKERS
        qty, size_reason = calc_position_size(
            available_budget=budget_usd,
            current_price=price_usd,
            fractional=is_fractional,
            params=self._params,
        )
        if qty <= 0:
            logger.info(
                "%s 매수 신호이나 사이즈 0 ($%.2f USD, %s) — 스킵",
                symbol, budget_usd, size_reason,
            )
            return

        logger.info(
            "[매수신호] %s @ $%.2f — %s | conf=%.2f | %s",
            symbol, price_usd, signal_.reason, signal_.confidence, size_reason,
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
        self._last_sell_at[symbol] = time.time()  # 재매수 쿨다운 기준

    def _on_shutdown(self, *_args) -> None:
        logger.info("=== KIS 미국주식 봇 종료 신호 ===")
        self._running = False
        if self._notifier.is_configured:
            self._notifier.notify_bot_status("[KIS_US] 미국주식 봇 종료")
        sys.exit(0)


def main() -> None:
    setup_logging("bot_kis_us")
    if not config.kis.us_enabled:
        logger.info("KIS_US_ENABLED=false — 미국주식 봇 비활성. 종료.")
        sys.exit(0)
    KISUSBot().start()


if __name__ == "__main__":
    main()
