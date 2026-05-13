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

env (시크릿 + 토글만):
- KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NUMBER / KIS_ACCOUNT_PRODUCT_CODE
- KIS_IS_PAPER (모의투자 토글)
- KIS_US_ENABLED (마켓 토글)

운영 파라미터 (#392, 코드 상수로 통합):
- 모듈 상단 "KIS US 운영 파라미터" 블록 참고 (TICK_INTERVAL_SEC, STRATEGY, ATR_STOP_PCT 등)
- 종목 풀은 DB 우선 (kis_us_symbols 테이블), fallback DEFAULT_US_UNIVERSE

사용법:
    python -m cryptobot.entrypoints.run_kis_us

Related: #247, #279, #285, #392
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo

from cryptobot.bot.config import config
from cryptobot.bot.kis_strategy import (
    KISStrategyParams,
    calc_position_size,
    calc_position_size_risk_based,
    evaluate_buy,
    evaluate_buy_breakout,
    evaluate_sell,
    evaluate_zarattini_3x_atr,
    evaluate_zarattini_bar1,
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

# #364 Pure Zarattini Bar-1 페어 — 인버스 ETF가 짝. 둘 중 하나가 보유 중이면 짝은 매수 X.
# 한국 KIS API는 미국주식 공매도 불가 → 음봉 시 인버스 ETF 매수로 효과적 숏.
ZARATTINI_PAIRS = {
    "SOXL": "SOXS",  # 반도체 강세 3X ↔ 약세 3X (#364 시작 페어)
    "SOXS": "SOXL",
    "TQQQ": "SQQQ",  # NASDAQ-100 3X ↔ 약세 3X
    "SQQQ": "TQQQ",
    "SPXL": "SPXS",  # S&P500 3X ↔ 약세 3X
    "SPXS": "SPXL",
    "TECL": "TECS",  # 기술주 3X ↔ 약세 3X
    "TECS": "TECL",
}

# 디폴트 풀 (#285): 빅테크/반도체/레버리지/크립토/EV/AI 분산
# 종목 풀 fallback — DB 없거나 env 미지정 시
DEFAULT_US_UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",          # 빅테크
    "NVDA", "AMD", "TSM", "AVGO", "ASML",             # 반도체
    "SNDK", "SNXX",                                    # 메모리/레버리지
    "COIN", "MSTR", "HOOD",                            # 크립토 노출
    "TSLA", "RIVN",                                    # EV
    "PLTR", "ARM", "NFLX",                             # AI/소프트웨어
]

# ============================================================
# #392: KIS US 운영 파라미터 (코드 관리, env 분기 제거)
# ============================================================
# 시크릿(KIS_APP_KEY 등) + 마켓 토글(KIS_US_ENABLED, KIS_KR_ENABLED)만 env 유지.
# 그 외 운영 값은 모두 여기서 관리 → .env 흩어짐/오타 위험 제거.

# 모드 & 전략 (논문 Pure Zarattini)
DAY_TRADING_MODE = True
STRATEGY = "zarattini_3x_atr"  # 옵션: "zarattini_3x_atr" / "zarattini_bar1" / "breakout" / "mean_reversion"

# 폴링 (논문 즉시 진입 정확도)
TICK_INTERVAL_SEC = 20  # 코드 최소 15초 강제
INSUFFICIENT_FUNDS_COOLDOWN_SEC = 300  # 자금 부족 시 종목별 cooldown
REBUY_COOLDOWN_SEC = 0  # in-memory cooldown 사용 안 함 (#391 DB 1일 1회 룰로 충분)

# OHLCV 봉 단위 — 논문은 5분봉 (zarattini/breakout 모두)
OHLCV_INTERVAL_BREAKOUT = "5min"
OHLCV_INTERVAL_ZARATTINI = "5min"
OHLCV_INTERVAL_MEANREV = "15min"
OHLCV_INTERVAL_SWING = "day"

# 매수 조건 (breakout/meanrev 모드용)
RSI_OVERSOLD = 35
RSI_OVERBOUGHT = 70
VOLUME_SPIKE_MULTIPLIER = 1.6

# ORB 형성 시간 (분)
ORB_MINUTES_DAY_TRADING = 5  # 논문: 첫 5분봉
ORB_MINUTES_SWING = 30

# 손절/익절/트레일링 디폴트
# zarattini_3x_atr: 손절은 ATR_STOP_PCT 기반, TP/TR 사실상 비활성
TAKE_PROFIT_PCT_ZARATTINI = 999.0
STOP_LOSS_PCT_ZARATTINI = -4.0  # 폴백 (stop_loss_price 우선)
TRAILING_STOP_PCT_ZARATTINI = -99.0  # 비활성
# meanrev 단타
TAKE_PROFIT_PCT_MEANREV = 4.0
STOP_LOSS_PCT_MEANREV = -4.0
TRAILING_STOP_PCT_MEANREV = -2.0
# 스윙
TAKE_PROFIT_PCT_SWING = 10.0
STOP_LOSS_PCT_SWING = -10.0
TRAILING_STOP_PCT_SWING = -3.0

# 마감 윈도우 (NY 시간 기준 분)
NO_BUY_BEFORE_CLOSE_MIN = 30  # 마감 30분 전부터 매수 금지
FORCE_SELL_BEFORE_CLOSE_MIN = 10  # 마감 10분 전 강제 청산

# Pure Zarattini 논문 파라미터 (논문 그대로)
ATR_STOP_PCT = 5.0  # 0.05 × ATR(14)
ATR_PERIOD = 14
DOJI_THRESHOLD_PCT = 0.05
RISK_PCT_PER_TRADE = 1.0
R_MULTIPLE_TARGET = 10.0

# #396: 시그널-매수 가격 갭 가드 (%)
# 시그널(bar1 close) 대비 매수 직전 가격이 이 % 이상 빠졌으면 매수 skip.
# 매수 즉시 stop 아래 진입 방지 (#394 _time 버그 시점 SOXL -1.95% 갭 같은 사례).
GAP_GUARD_PCT = 1.0

# 종목당 최대 포지션 (#309: 풀매수, 여러 종목은 신뢰도 정렬로 순차)
MAX_POSITION_PER_SYMBOL_PCT = 100.0


def _parse_universe(db: "Database | None" = None) -> list[str]:
    """종목 풀 결정. 우선순위: DB(kis_us_symbols.enabled) > env KIS_US_UNIVERSE > DEFAULT.

    #297: DB 기반 종목 관리 — admin에서 토글하면 봇 재시작 없이 다음 틱부터 반영.
    #315: 분봉 미지원 종목은 자동 필터 (DB enabled=1이라도 봇 평가 불가하므로 제외).
    """
    from cryptobot.exchange.kis_us import KIS_MINUTE_UNSUPPORTED

    candidates: list[str]
    if db is not None:
        try:
            rows = db.execute(
                "SELECT ticker FROM kis_us_symbols WHERE enabled = 1 ORDER BY ticker"
            ).fetchall()
            if rows:
                candidates = [dict(r)["ticker"] for r in rows]
                return [t for t in candidates if t not in KIS_MINUTE_UNSUPPORTED]
        except Exception:
            pass  # DB 조회 실패 시 env로 fallback

    # #392: env KIS_US_UNIVERSE 제거 — DB 우선 정책. fallback은 DEFAULT_US_UNIVERSE.
    raw = ""
    if raw:
        candidates = [s.strip().upper() for s in raw.split(",") if s.strip()]
    else:
        candidates = list(DEFAULT_US_UNIVERSE)
    return [t for t in candidates if t not in KIS_MINUTE_UNSUPPORTED]


def _build_params(universe_size: int) -> KISStrategyParams:
    """env 기반 전략 파라미터 생성.

    #309: 종목당 한도 = 100% (풀매수). 여러 종목 활성화 시 신뢰도 정렬로 순차 매수.
    #392: env 분기 제거 — 운영 파라미터는 위쪽 모듈 상수에서 관리.
    전략별 TP/SL/TR/ORB:
    - zarattini_3x_atr / zarattini_bar1 / breakout: TP 999, SL -4 (폴백), TR -99, ORB 5분
    - mean_reversion (단타): TP +4 / SL -4 / TR -2 / ORB 30분
    - 스윙: TP +10 / SL -10 / TR -3 / ORB 30분
    """
    if universe_size <= 0:
        universe_size = 1
    is_day_trading = DAY_TRADING_MODE
    strategy = STRATEGY.lower()

    if is_day_trading and strategy in ("zarattini_3x_atr", "zarattini_bar1", "breakout"):
        tp = TAKE_PROFIT_PCT_ZARATTINI
        sl = STOP_LOSS_PCT_ZARATTINI
        tr = TRAILING_STOP_PCT_ZARATTINI
        orb = ORB_MINUTES_DAY_TRADING
    elif is_day_trading:
        # mean_reversion 단타
        tp = TAKE_PROFIT_PCT_MEANREV
        sl = STOP_LOSS_PCT_MEANREV
        tr = TRAILING_STOP_PCT_MEANREV
        orb = ORB_MINUTES_SWING  # mean_reversion은 30분 ORB
    else:
        # 스윙
        tp = TAKE_PROFIT_PCT_SWING
        sl = STOP_LOSS_PCT_SWING
        tr = TRAILING_STOP_PCT_SWING
        orb = ORB_MINUTES_SWING

    return KISStrategyParams(
        rsi_oversold=RSI_OVERSOLD,
        rsi_overbought=RSI_OVERBOUGHT,
        take_profit_pct=tp,
        stop_loss_pct=sl,
        trailing_stop_pct=tr,
        max_position_per_symbol_pct=MAX_POSITION_PER_SYMBOL_PCT,
        day_trading_mode=is_day_trading,
        no_buy_window_minutes_before_close=NO_BUY_BEFORE_CLOSE_MIN,
        force_sell_window_minutes_before_close=FORCE_SELL_BEFORE_CLOSE_MIN,
        orb_minutes=orb,
        volume_spike_multiplier=VOLUME_SPIKE_MULTIPLIER,
        # Pure Zarattini 파라미터 (논문 그대로)
        doji_threshold_pct=DOJI_THRESHOLD_PCT,
        risk_pct_per_trade=RISK_PCT_PER_TRADE,
        r_multiple_target=R_MULTIPLE_TARGET,
        atr_stop_pct=ATR_STOP_PCT,
        atr_period=ATR_PERIOD,
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
        # #392: 운영 파라미터 코드 상수에서 직접 (env 분기 제거)
        self._rebuy_cooldown_sec = REBUY_COOLDOWN_SEC
        self._tick_interval_sec = max(15, TICK_INTERVAL_SEC)
        self._heartbeat_every_n_ticks = max(1, 300 // self._tick_interval_sec)  # ~5분 1회 살아있음 핑
        self._tick_count = 0
        self._strategy = STRATEGY.lower()
        # OHLCV 봉 단위 — 전략별 매핑 (모두 5분봉, 단 mean_reversion만 15분봉)
        if self._strategy in ("zarattini_3x_atr", "zarattini_bar1", "breakout"):
            self._ohlcv_interval = OHLCV_INTERVAL_ZARATTINI  # 5min (논문 정확)
        elif self._params.day_trading_mode:
            self._ohlcv_interval = OHLCV_INTERVAL_MEANREV  # 15min
        else:
            self._ohlcv_interval = OHLCV_INTERVAL_SWING  # day
        self._running = False
        self._last_buy_price: dict[str, float] = {}  # USD 기준
        self._highest_since_buy: dict[str, float] = {}
        # #364 Pure Zarattini Bar-1: 매수 시 take_profit_price (10R) 보존
        self._take_profit_price_at_buy: dict[str, float] = {}
        self._pending_take_profit: float | None = None
        self._last_sell_at: dict[str, float] = {}  # 종목별 마지막 매도 timestamp (epoch)
        self._stop_loss_price_at_buy: dict[str, float] = {}  # #305 ORB 모드 OR_low 손절가
        self._pending_stop_loss: float | None = None  # #305 매수 직전 OR_low 임시
        # #390: 자금 부족 종목별 cooldown — 한 번 자금 부족 발생 시 N분간 같은 종목 매수 시도 안 함
        # 같은 에러 반복(176회) 방지 + Slack 알림 폭주 방지
        self._insufficient_funds_cooldown: dict[str, float] = {}  # symbol -> 다음 시도 가능 epoch
        self._insufficient_funds_cooldown_sec = INSUFFICIENT_FUNDS_COOLDOWN_SEC
        # #393: Slack 통합 보고 — 장 시작/일일 결산 1회 가드 (NY 날짜별)
        self._market_open_sent_date: str | None = None  # NY date "YYYY-MM-DD"
        self._daily_summary_sent_date: str | None = None
        self._usd_start_of_day: float | None = None  # 장 시작 시점 USD 잔고 (일일 손익 계산용)
        # #395: 에러 알림 cooldown — 같은 에러 폭주 방지 (5분 1회만)
        self._error_alert_last_sent: dict[str, float] = {}  # signature -> epoch
        self._error_alert_cooldown_sec = 300

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
        logger.info("OHLCV 봉 단위: %s", self._ohlcv_interval)
        logger.info("매수 전략: %s", self._strategy)

        if self._notifier.is_configured:
            self._notifier.notify_bot_status(f"[KIS_US] 미국주식 봇 시작 ({mode})")

        signal.signal(signal.SIGINT, self._on_shutdown)
        signal.signal(signal.SIGTERM, self._on_shutdown)
        self._running = True

        while self._running:
            try:
                self._tick()
            except Exception as e:
                #395: 매수 시그널 떴는데 코드 버그로 실패 시 명확한 Slack 알림 + cooldown
                logger.exception("틱 처리 중 예외: %s", e)
                self._notify_error_throttled(e)
            time.sleep(self._tick_interval_sec)

    def _notify_error_throttled(self, exc: Exception) -> None:
        """#395: 봇 에러 Slack 알림 — 같은 에러 5분 cooldown으로 폭주 방지.

        매수 시그널 떴는데 코드 버그/API 에러로 매수 못 한 케이스를 명확히 알림.
        같은 에러 매분 발생 시 1회만 발송 (Slack rate limit + 사용자 알람 폭주 방지).
        """
        if not self._notifier or not self._notifier.is_configured:
            return
        import traceback as _tb

        sig = f"{type(exc).__name__}:{str(exc)[:80]}"
        now_epoch = time.time()
        last = self._error_alert_last_sent.get(sig, 0.0)
        if now_epoch - last < self._error_alert_cooldown_sec:
            return  # cooldown 중 — 로그만, Slack 안 보냄
        self._error_alert_last_sent[sig] = now_epoch

        tb_short = _tb.format_exc()[-400:] if _tb.format_exc() else ""
        msg = (
            f"🚨 *KIS US 봇 에러 — 매수 실행 차단 가능*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"❌ `{type(exc).__name__}`: {str(exc)[:200]}\n"
            f"\n"
            f"⚠️ 매수 시그널 떠도 이 에러로 봇이 매수 못 할 수 있음.\n"
            f"🔧 즉시 로그 확인 + 핫픽스 필요.\n"
            f"\n"
            f"```{tb_short}```\n"
            f"_5분 cooldown — 같은 에러 반복돼도 추가 알림 X_"
        )
        try:
            self._notifier.notify_trade_message(msg)
        except Exception as e:
            logger.warning("에러 Slack 알림 실패: %s", e)

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
        #393: 장 시작 / 일일 결산 트리거 (시장 상태 무관)
        self._maybe_send_market_open()
        self._maybe_send_daily_summary()
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

        # 매수 후보 수집 → 신뢰도 정렬 → 자금 가능한 만큼 순차 매수.
        # 보유 종목은 즉시 매도 평가 (분기 차단으로 매수와 충돌 X).
        # #364 buy_candidates 튜플: (symbol, price, signal, sl_price, tp_price, risk_per_share)
        buy_candidates: list[tuple[str, float, object, float | None, float | None, float | None]] = []
        # 페어 mutex — 보유 중인 인버스 ETF 짝을 미리 모아둠
        held_set: set[str] = set()
        for symbol in self._universe:
            try:
                if self._exchange.get_balance(symbol) > 0:
                    held_set.add(symbol)
            except APIError:
                pass

        for symbol in self._universe:
            try:
                price = self._exchange.get_current_price(symbol)
                if symbol in held_set:
                    self._evaluate_sell_with_price(symbol, price)
                    continue

                # #364 페어 mutex — 인버스 짝이 보유 중이면 매수 평가 자체 skip
                inverse = ZARATTINI_PAIRS.get(symbol)
                if inverse and inverse in held_set:
                    logger.debug("%s 페어 인버스(%s) 보유 중 — 매수 skip", symbol, inverse)
                    continue

                result = self._evaluate_buy_only(symbol, price)
                if result is None:
                    continue
                sig, sl, tp, rps = result
                if sig and getattr(sig, "should_buy", False):
                    buy_candidates.append((symbol, price, sig, sl, tp, rps))
            except APIError as e:
                logger.warning("%s 평가 실패: %s", symbol, e)
            except Exception as e:
                logger.exception("%s 평가 중 예외: %s", symbol, e)

        # 매수 후보 신뢰도 내림차순 → 자금 0될 때까지 순차 매수
        if buy_candidates:
            buy_candidates.sort(key=lambda x: getattr(x[2], "confidence", 0.0), reverse=True)
            self._execute_buy_queue(buy_candidates)

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
            df = self._exchange.get_ohlcv(symbol, interval=self._ohlcv_interval, count=80)
        except APIError as e:
            logger.warning("%s OHLCV 조회 실패 (매도 판단은 단순 룰로): %s", symbol, e)

        sl_price = self._stop_loss_price_at_buy.get(symbol)
        tp_price = self._take_profit_price_at_buy.get(symbol)  # #364 10R 익절가
        signal_ = evaluate_sell(
            df, price_usd, buy_price, prev_high, self._params,
            stop_loss_price=sl_price, take_profit_price=tp_price,
        )
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

    def _evaluate_buy_only(self, symbol: str, price_usd: float):
        """매수 평가만 (실 매수 X). 신호 + 손절/익절가 + 주당 리스크 반환.

        Returns:
            (signal, stop_loss_price, take_profit_price, risk_per_share)
            매수 미충족/스킵 시 None.
        """
        # 단타 매수 금지 윈도우 (마감 30분 전부터)
        if self._params.day_trading_mode:
            mins = self._minutes_to_close()
            if 0 <= mins <= self._params.no_buy_window_minutes_before_close:
                logger.debug(
                    "%s 마감 임박 매수금지 (%.0f분 남음, 임계 %d분)",
                    symbol, mins, self._params.no_buy_window_minutes_before_close,
                )
                return None

        # 짧은 재매수 쿨다운 (옵션) — 매도 직후 노이즈 매매 회피
        if self._rebuy_cooldown_sec > 0:
            last_sell_ts = self._last_sell_at.get(symbol, 0.0)
            elapsed = time.time() - last_sell_ts
            if last_sell_ts > 0 and elapsed < self._rebuy_cooldown_sec:
                logger.debug(
                    "%s 재매수 쿨다운 (%ds 중 %ds 경과)",
                    symbol, self._rebuy_cooldown_sec, int(elapsed),
                )
                return None

        # #391: 같은 NY 거래일에 같은 종목 이미 매매 있으면 skip (논문 룰: 1일 1회)
        # in-memory cooldown은 봇 재시작 시 사라지지만 DB 쿼리는 영속.
        # NY timezone 정확히 사용 (EDT/EST 자동 처리).
        if self._params.day_trading_mode:
            ny_now = datetime.now(NY)
            ny_midnight = ny_now.replace(hour=0, minute=0, second=0, microsecond=0)
            ny_midnight_utc = ny_midnight.astimezone(timezone.utc)
            already = self._db.execute(
                """
                SELECT id, side FROM trades
                WHERE market = 'kis_us' AND coin = ?
                  AND timestamp >= ?
                LIMIT 1
                """,
                (symbol, ny_midnight_utc.strftime("%Y-%m-%d %H:%M:%S")),
            ).fetchone()
            if already:
                logger.info(
                    "%s NY 거래일 (오늘) 이미 %s — 1일 1회 룰 (논문) skip",
                    symbol, dict(already)["side"],
                )
                return None

        try:
            df = self._exchange.get_ohlcv(symbol, interval=self._ohlcv_interval, count=80)
        except APIError as e:
            logger.warning("%s OHLCV 조회 실패 — 매수 판단 스킵: %s", symbol, e)
            err_msg = str(e)[:120]
            try:
                self._db.execute(
                    "INSERT INTO kis_us_evaluations "
                    "(ticker, price, rsi, ma20, ma60, should_buy, reason, confidence, holds_already) "
                    "VALUES (?, ?, NULL, NULL, NULL, 0, ?, 0, 0)",
                    (symbol, float(price_usd), f"OHLCV 조회 실패: {err_msg}"),
                )
                self._db.commit()
            except Exception:
                pass
            return None

        # 전략 분기
        ny_today = datetime.now(NY).date()
        df_today = df[df.index.date == ny_today] if hasattr(df.index, "date") else df

        if self._strategy == "zarattini_3x_atr":
            # #364 Pure Zarattini 3X 변형 — bar1 양봉 + ATR(14d) 손절 + No TP
            try:
                df_daily = self._exchange.get_ohlcv(symbol, interval="day", count=20)
            except APIError as e:
                logger.warning("%s 일봉 조회 실패 — ATR 계산 불가: %s", symbol, e)
                return None
            # ATR은 *완성된* 14일 (오늘 진행 중 일봉은 high/low 부분 정보)이라
            # 오늘 봉을 명시적으로 제외하고 계산해야 정확. KIS API 응답에 오늘이
            # 포함될 수 있으므로 방어적으로 필터.
            if hasattr(df_daily.index, "date"):
                df_daily = df_daily[df_daily.index.date < ny_today]
            signal_ = evaluate_zarattini_3x_atr(df_today, df_daily, params=self._params)
            sl_price = signal_.stop_loss_price
            tp_price = None  # 3X 변형: TP 없음
            rps = signal_.risk_per_share
        elif self._strategy == "zarattini_bar1":
            # #364 Pure Zarattini baseline — bar1 stop + 10R TP
            signal_ = evaluate_zarattini_bar1(df_today, params=self._params)
            sl_price = signal_.stop_loss_price
            tp_price = signal_.take_profit_price
            rps = signal_.risk_per_share
        elif self._strategy == "breakout":
            try:
                bar_min = int(self._ohlcv_interval.replace("min", ""))
            except ValueError:
                bar_min = 5
            signal_ = evaluate_buy_breakout(df_today, price_usd, bar_minutes=bar_min, params=self._params)
            sl_price = signal_.stop_loss_price
            tp_price = None
            rps = None
        else:
            signal_ = evaluate_buy(df, price_usd, self._params)
            sl_price = None
            tp_price = None
            rps = None

        self._record_evaluation(symbol, price_usd, signal_, df=df, holds_already=False)
        if not signal_.should_buy:
            logger.debug("%s 매수 미판정: %s", symbol, signal_.reason)
            #396: 도지/음봉 skip을 일일 history에 기록 (매일 1행, 첫 평가 시점)
            pattern = getattr(signal_, "bar1_pattern", None)
            if pattern in ("doji", "bearish") and self._params.day_trading_mode:
                reason_short = "도지" if pattern == "doji" else "음봉 (페어 평가)"
                self._record_history_skip(symbol, signal_, reason_short)
            return signal_, None, None, None

        return signal_, sl_price, tp_price, rps

    def _execute_buy_queue(self, candidates: list) -> None:
        """매수 후보 신뢰도 정렬 후 순차 매수.

        #390: 매수 직전 fresh 잔고 조회 + 자금 부족 종목 cooldown 적용.
        candidates 튜플: (symbol, price, sig, sl_price, tp_price, risk_per_share)
        Pure Zarattini 모드는 risk_per_share 기반 1% 사이징, 그 외엔 풀매수.
        """
        # #390: 매수 직전 캐시 무효화 후 fresh 잔고 조회
        # KIS settlement 지연 + 봇 캐시(60s)로 stale 잔고 위험 → 매번 fresh 호출
        if hasattr(self._exchange, "invalidate_balance_cache"):
            self._exchange.invalidate_balance_cache()
        try:
            budget_usd = self._exchange.get_balance("USD")
        except APIError as e:
            logger.warning("USD 예수금 조회 실패 — 매수 큐 스킵: %s", e)
            return
        logger.info("[매수 큐] 가용 USD = $%.2f (출금가능액 기준)", budget_usd)

        now_epoch = time.time()

        for entry in candidates:
            symbol, price_usd, sig, sl_price, tp_price, rps = entry
            if budget_usd <= 0:
                logger.info("USD 잔고 0 — 남은 후보 %d건 매수 스킵", len([c for c in candidates if c[0] != symbol]))
                break

            # #390: 자금 부족 cooldown 체크 — 5분 내 자금 부족 발생한 종목 skip
            cd_until = self._insufficient_funds_cooldown.get(symbol, 0)
            if cd_until > now_epoch:
                remaining = int(cd_until - now_epoch)
                logger.info(
                    "%s 자금부족 cooldown 남은 %d초 — 매수 시도 skip", symbol, remaining,
                )
                continue

            is_fractional = symbol not in INTEGER_ONLY_TICKERS

            # #364 zarattini_bar1: 1% 리스크 기반 사이징 (rps 있을 때)
            if self._strategy == "zarattini_bar1" and rps and rps > 0:
                qty, size_reason = calc_position_size_risk_based(
                    available_budget=budget_usd,
                    current_price=price_usd,
                    risk_per_share=rps,
                    fractional=is_fractional,
                    params=self._params,
                )
            else:
                qty, size_reason = calc_position_size(
                    available_budget=budget_usd,
                    current_price=price_usd,
                    fractional=is_fractional,
                    params=self._params,
                )

            if qty <= 0:
                logger.info(
                    "%s 매수 신호 conf=%.2f 이나 사이즈 0 ($%.2f, %s) — 스킵",
                    symbol, sig.confidence, budget_usd, size_reason,
                )
                continue

            # #396: 시그널-매수 가격 갭 가드
            # 시그널 발생 시점(bar1 close) 대비 현재가가 -1% 이상 빠졌으면 매수 skip
            # → 매수 즉시 stop 아래로 진입하는 케이스 방지 (시그널 시점부터 진입까지
            # 가격이 크게 빠진 경우. 봇 재시작·delay·burst 시 발생)
            signal_price = getattr(sig, "signal_price", None)
            if signal_price and signal_price > 0:
                gap_pct = (price_usd - signal_price) / signal_price * 100
                if gap_pct < -GAP_GUARD_PCT:
                    skip_msg = (
                        f"{symbol} 갭 가드 skip — 시그널 ${signal_price:.2f} → 현재 ${price_usd:.2f} "
                        f"({gap_pct:+.2f}%, 임계 -{GAP_GUARD_PCT}%)"
                    )
                    logger.warning(skip_msg)
                    # 도지처럼 "오늘 안 산 날"로 history 기록
                    self._record_history_skip(symbol, sig, f"갭 가드 ({gap_pct:.2f}%)")
                    continue

            # #390: 매수 직전 사전 검증 — qty × price × 1.015 (buffer) ≤ budget
            estimated_cost = qty * price_usd * 1.015
            if estimated_cost > budget_usd:
                logger.warning(
                    "%s 사전 검증 실패: 예상 매수액 $%.2f > 가용 $%.2f — 매수 큐 중단",
                    symbol, estimated_cost, budget_usd,
                )
                self._mark_insufficient_funds(symbol, budget_usd, estimated_cost)
                self._record_history_skip(symbol, sig, "자금 부족")
                continue

            logger.info(
                "[매수실행] %s conf=%.2f @ $%.2f — %s | %s | 예상비용 $%.2f / 가용 $%.2f",
                symbol, sig.confidence, price_usd, sig.reason, size_reason,
                estimated_cost, budget_usd,
            )
            self._pending_stop_loss = sl_price
            self._pending_take_profit = tp_price
            buy_success = self._buy(symbol, qty, price_usd, sig.reason)
            if buy_success is False:
                # 자금 부족 등 실패 시 cooldown 등록 (Slack 1회 알림)
                self._mark_insufficient_funds(symbol, budget_usd, estimated_cost)
                # 잔고 재조회 (다음 후보 계산 정확하게)
                if hasattr(self._exchange, "invalidate_balance_cache"):
                    self._exchange.invalidate_balance_cache()
                try:
                    budget_usd = self._exchange.get_balance("USD")
                except APIError:
                    break
                continue
            cost = qty * price_usd
            budget_usd = max(0.0, budget_usd - cost)

    def _maybe_send_market_open(self) -> None:
        """#393: NY 09:30 직후 1회 장 시작 알림."""
        ny_now = datetime.now(NY)
        if ny_now.weekday() >= 5:
            return  # 주말
        # 09:30~09:32 사이만 (3분 윈도우)
        if not (ny_now.hour == 9 and 30 <= ny_now.minute <= 32):
            return
        today = ny_now.strftime("%Y-%m-%d")
        if self._market_open_sent_date == today:
            return
        if not (self._notifier and self._notifier.is_configured):
            self._market_open_sent_date = today
            return
        try:
            from cryptobot.notifier.kis_us_reports import format_market_open

            usd = self._exchange.get_balance("USD")
            try:
                fx = self._exchange.get_fx_rate_krw_per_usd()
            except Exception:
                fx = 1400.0
            msg = format_market_open(
                universe=list(self._universe),
                usd_available=usd,
                fx_krw_per_usd=fx,
            )
            self._notifier.notify_trade_message(msg)
            self._usd_start_of_day = usd
            self._market_open_sent_date = today
            logger.info("[Slack] 장 시작 알림 발송 (%s)", today)
        except Exception as e:
            logger.warning("장 시작 알림 실패: %s", e)

    def _maybe_send_daily_summary(self) -> None:
        """#393: NY 16:05 ~ 16:10 사이 1회 일일 결산."""
        ny_now = datetime.now(NY)
        if ny_now.weekday() >= 5:
            return
        # 16:05~16:10 윈도우 (마감 5~10분 후)
        if not (ny_now.hour == 16 and 5 <= ny_now.minute <= 10):
            return
        today = ny_now.strftime("%Y-%m-%d")
        if self._daily_summary_sent_date == today:
            return
        if not (self._notifier and self._notifier.is_configured):
            self._daily_summary_sent_date = today
            return
        try:
            from cryptobot.notifier.kis_us_reports import (
                calc_period_pnl,
                calc_today_pnl,
                format_daily_summary,
            )

            today_pnl, today_trades = calc_today_pnl(self._db)
            week_pnl, week_days = calc_period_pnl(self._db, days=7)
            month_pnl, _ = calc_period_pnl(self._db, days=30)
            total_pnl, _ = calc_period_pnl(self._db, days=3650)  # 10년 = 사실상 전체

            usd_now = self._exchange.get_balance("USD")
            usd_start = self._usd_start_of_day if self._usd_start_of_day is not None else usd_now - today_pnl

            # 매매 없는 날 → skip 사유 수집
            skip_reasons: dict[str, str] = {}
            if not today_trades:
                # 가장 최근 evaluation을 종목별로 1건씩
                for symbol in self._universe:
                    row = self._db.execute(
                        "SELECT reason FROM kis_us_evaluations "
                        "WHERE ticker = ? AND evaluated_at >= date('now', '-1 day') "
                        "ORDER BY id DESC LIMIT 1",
                        (symbol,),
                    ).fetchone()
                    if row:
                        skip_reasons[symbol] = (dict(row).get("reason") or "")[:60]

            msg = format_daily_summary(
                today_trades=today_trades,
                skip_reasons=skip_reasons,
                usd_now=usd_now,
                usd_start_of_day=usd_start,
                week_pnl_usd=week_pnl,
                week_trade_days=week_days,
                month_pnl_usd=month_pnl,
                total_pnl_usd=total_pnl if abs(total_pnl) > 0.01 else None,
            )
            self._notifier.notify_trade_message(msg)
            self._daily_summary_sent_date = today
            logger.info("[Slack] 일일 결산 발송 (%s, 매매 %d건)", today, len(today_trades))
        except Exception as e:
            logger.exception("일일 결산 알림 실패: %s", e)

    def _record_history_skip(self, symbol: str, sig, skip_reason: str) -> None:
        """#396: 매수 안 한 날(도지/음봉/갭 가드/자금 부족) history 기록."""
        try:
            from cryptobot.notifier.kis_us_reports import record_daily_history

            record_daily_history(
                self._db,
                ticker=symbol,
                bar1_pattern=getattr(sig, "bar1_pattern", None),
                bar1_body_pct=getattr(sig, "bar1_body_pct", None),
                signal_price=getattr(sig, "signal_price", None),
                bought=False,
                skip_reason=skip_reason,
            )
        except Exception as e:
            logger.debug("history skip 기록 실패 (%s): %s", symbol, e)

    def _record_history_buy(self, symbol: str, sig, qty: float, buy_price: float) -> None:
        """#396: 매수 체결 시 history 기록."""
        try:
            from cryptobot.notifier.kis_us_reports import record_daily_history

            record_daily_history(
                self._db,
                ticker=symbol,
                bar1_pattern=getattr(sig, "bar1_pattern", None),
                bar1_body_pct=getattr(sig, "bar1_body_pct", None),
                signal_price=getattr(sig, "signal_price", None),
                bought=True,
                buy_price=buy_price,
                qty=qty,
            )
        except Exception as e:
            logger.debug("history buy 기록 실패 (%s): %s", symbol, e)

    def _record_history_sell(self, symbol: str, sell_price: float, pnl_usd: float, pnl_pct: float, sell_type: str) -> None:
        """#396: 매도 시 history 업데이트."""
        try:
            from cryptobot.notifier.kis_us_reports import update_daily_history_sell

            update_daily_history_sell(
                self._db,
                ticker=symbol,
                sell_price=sell_price,
                pnl_usd=pnl_usd,
                pnl_pct=pnl_pct,
                sell_type=sell_type,
            )
        except Exception as e:
            logger.debug("history sell 기록 실패 (%s): %s", symbol, e)

    def _mark_insufficient_funds(self, symbol: str, budget: float, cost: float) -> None:
        """#390: 자금 부족 종목 cooldown 등록 + Slack 1회 알림."""
        cd_sec = self._insufficient_funds_cooldown_sec
        prev = self._insufficient_funds_cooldown.get(symbol, 0)
        # 이미 cooldown 중이면 Slack 알림 안 함 (중복 방지)
        already_cooldown = prev > time.time()
        self._insufficient_funds_cooldown[symbol] = time.time() + cd_sec
        if not already_cooldown and self._notifier and self._notifier.is_configured:
            try:
                self._notifier.notify_trade_message(
                    f"⚠️ [KIS_US] {symbol} 매수 자금 부족 — "
                    f"필요 ${cost:.2f} / 가용 ${budget:.2f} (부족 ${cost - budget:.2f}). "
                    f"{cd_sec // 60}분 cooldown."
                )
            except Exception as e:
                logger.warning("Slack 알림 실패: %s", e)

    def _evaluate_sell_with_price(self, symbol: str, price_usd: float) -> None:
        """#309: _evaluate_symbol 매도 분기 추출 (price 외부 주입)."""
        # 단타 강제청산 윈도우
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

    def _buy(self, symbol: str, qty: float, price_usd: float, reason: str) -> None:
        result = self._exchange.buy_market(symbol, qty)
        if not result.success:
            logger.warning("매수 실패: %s — %s", symbol, result.error)
            return

        # #319: 잔고 캐시 무효화 (다음 매수 평가 시 정확한 USD 잔고)
        if hasattr(self._exchange, "invalidate_balance_cache"):
            self._exchange.invalidate_balance_cache()

        self._last_buy_price[symbol] = result.price
        self._highest_since_buy[symbol] = result.price
        # ORB / Pure Zarattini 모드: stop_loss_price (OR_low) 저장
        if hasattr(self, "_pending_stop_loss") and self._pending_stop_loss is not None:
            self._stop_loss_price_at_buy[symbol] = self._pending_stop_loss
            self._pending_stop_loss = None
        # #364 Pure Zarattini: 10R take_profit_price 저장
        if self._pending_take_profit is not None:
            self._take_profit_price_at_buy[symbol] = self._pending_take_profit
            self._pending_take_profit = None
        self._recorder.record_trade(
            coin=symbol,
            market="kis_us",
            side="buy",
            price=result.price,
            amount=result.amount,
            total_krw=result.total_krw,
            fee_krw=result.fee_krw,
            strategy=f"kis_us_{self._strategy}",  # #364: 전략 모드 명시 (백테스트 추적용)
            trigger_reason=reason,
            order_uuid=result.order_uuid,
        )
        #396: 일일 history 기록 (매수 체결)
        try:
            from cryptobot.notifier.kis_us_reports import record_daily_history

            record_daily_history(
                self._db,
                ticker=symbol,
                signal_price=None,  # _buy 시점엔 sig 객체 없음 — 매수가만
                bought=True,
                buy_price=result.price,
                qty=result.amount,
            )
        except Exception as e:
            logger.debug("history buy 기록 실패 (%s): %s", symbol, e)

        if self._notifier.is_configured:
            #393: 통합 보고 포맷
            from cryptobot.notifier.kis_us_reports import format_buy

            try:
                budget = self._exchange.get_balance("USD")
            except Exception:
                budget = None
            stop = self._stop_loss_price_at_buy.get(symbol)
            risk = abs(stop - result.price) * result.amount if stop else None
            self._notifier.notify_trade_message(
                format_buy(
                    symbol=symbol,
                    qty=result.amount,
                    price=result.price,
                    signal_reason=reason,
                    stop_loss_price=stop,
                    risk_usd=risk,
                    account_usd=budget,
                )
            )

    def _sell(self, symbol: str, reason: str, pnl_pct: float) -> None:
        result = self._exchange.sell_market(symbol)
        if not result.success:
            logger.warning("매도 실패: %s — %s", symbol, result.error)
            return

        # #319: 잔고 캐시 무효화
        if hasattr(self._exchange, "invalidate_balance_cache"):
            self._exchange.invalidate_balance_cache()

        self._recorder.record_trade(
            coin=symbol,
            market="kis_us",
            side="sell",
            price=result.price,
            amount=result.amount,
            total_krw=result.total_krw,
            fee_krw=result.fee_krw,
            strategy=f"kis_us_{self._strategy}",  # #364: 전략 모드 명시
            trigger_reason=reason,
            profit_pct=pnl_pct,
            order_uuid=result.order_uuid,
        )
        # sell_type 분류 (history + Slack 메시지 공통 사용)
        reason_lower = (reason or "").lower()
        is_eod = "day_trading_close" in reason_lower or "eod" in reason_lower or "강제" in (reason or "")
        if is_eod:
            sell_type = "eod_profit" if pnl_pct > 0 else "eod_loss"
        else:
            sell_type = "stop_loss"
        pnl_usd_val = result.amount * (result.price - (self._last_buy_price.get(symbol, result.price)))

        #396: 일일 history 업데이트 (매도)
        try:
            from cryptobot.notifier.kis_us_reports import update_daily_history_sell

            update_daily_history_sell(
                self._db,
                ticker=symbol,
                sell_price=result.price,
                pnl_usd=pnl_usd_val,
                pnl_pct=pnl_pct,
                sell_type=sell_type,
            )
        except Exception as e:
            logger.debug("history sell 기록 실패 (%s): %s", symbol, e)

        if self._notifier.is_configured:
            #393: 통합 보고 포맷 — 손절/EOD 익절/EOD 손실 분기
            from cryptobot.notifier.kis_us_reports import format_sell
            # 보유 시간 추정 (last_buy_price 기준, 정확도 낮으나 표시용)
            hold_min = 0
            try:
                buy_row = self._db.execute(
                    "SELECT timestamp FROM trades WHERE market='kis_us' AND coin=? AND side='buy' "
                    "ORDER BY id DESC LIMIT 1",
                    (symbol,),
                ).fetchone()
                if buy_row:
                    from datetime import datetime as _dt
                    buy_t = _dt.strptime(dict(buy_row)["timestamp"], "%Y-%m-%d %H:%M:%S")
                    hold_min = int((_dt.utcnow() - buy_t).total_seconds() / 60)
            except Exception:
                pass
            self._notifier.notify_trade_message(
                format_sell(
                    symbol=symbol,
                    qty=result.amount,
                    price=result.price,
                    pnl_pct=pnl_pct,
                    pnl_usd=pnl_usd_val,
                    hold_minutes=hold_min,
                    sell_type=sell_type,
                )
            )
        self._last_buy_price.pop(symbol, None)
        self._highest_since_buy.pop(symbol, None)
        self._stop_loss_price_at_buy.pop(symbol, None)
        self._take_profit_price_at_buy.pop(symbol, None)  # #364 10R 익절가 정리
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
