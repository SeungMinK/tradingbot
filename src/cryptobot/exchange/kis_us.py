"""KIS Developers 미국주식 거래소 어댑터.

한국투자증권 OpenAPI 해외주식을 통해 미국주식 매매를 실행.
`Exchange` 인터페이스 구현. 통합증거금으로 원화 자동환전, 소수점 거래 지원.

KIS 공식 레포(koreainvestment/open-trading-api) examples_user/overseas_stock 참조.

Related: #247
"""

from __future__ import annotations

import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from cryptobot.exceptions import APIError, ConfigError
from cryptobot.exchange.base import Exchange, OrderResult
from cryptobot.exchange.kis.auth import KISTokenManager
from cryptobot.exchange.kis.client import KISClient

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")  # 서머타임 자동 처리

# tr_id 매핑 (실전 / 모의)
TR_ID_REAL = {
    "balance": "TTTS3012R",
    "buy_market": "TTTT1002U",
    "sell_market": "TTTT1006U",
    "cancel_revise": "TTTT1004U",
    "current_price": "HHDFS00000300",
    "daily_chart": "HHDFS76240000",
}
TR_ID_PAPER = {
    "balance": "VTTS3012R",
    "buy_market": "VTTT1002U",
    "sell_market": "VTTT1006U",
    "cancel_revise": "VTTT1004U",
    "current_price": "HHDFS00000300",
    "daily_chart": "HHDFS76240000",
}

# 거래소 코드 매핑 (KIS API)
EXCHANGE_CODE = {
    "NASD": "NASD",  # NASDAQ
    "NAS": "NASD",
    "NYSE": "NYSE",
    "NYS": "NYSE",
    "AMEX": "AMEX",
    "AMS": "AMEX",
}

# 종목별 기본 거래소 (보유 풀 기준 사전 매핑)
# 작업 시 종목 풀이 늘어나면 별도 종목 마스터로 분리 필요
DEFAULT_EXCHANGE_BY_TICKER = {
    # 빅테크 + 일반 NASDAQ
    "NVDA": "NASD",
    "TSLA": "NASD",
    "AAPL": "NASD",
    "MSFT": "NASD",
    "GOOGL": "NASD",
    "META": "NASD",
    "AMZN": "NASD",
    "AMD": "NASD",
    "COIN": "NASD",
    "MSTR": "NASD",
    "ARM": "NASD",
    "PLTR": "NASD",
    "NFLX": "NASD",
    "HOOD": "NASD",
    "RIVN": "NASD",
    "AVGO": "NASD",
    "ASML": "NASD",
    "SNDK": "NASD",
    # NYSE
    "TSM": "NYSE",
    # AMEX (NYSE Arca ETF — KIS는 NYSE Arca를 AMEX 코드로 통합)
    # 사용자 KIS 캡쳐로 확인: SOXL=아멕스, SNXX(Tradr) 도 동일 분류 추정
    "SOXL": "AMEX",   # Direxion Semi Bull 3X (매매단위 1주)
    "SOXS": "AMEX",   # Direxion Semi Bear 3X
    "TQQQ": "AMEX",   # ProShares UltraPro QQQ 3X
    "SQQQ": "AMEX",   # ProShares UltraPro Short QQQ 3X
    "USD":  "AMEX",   # ProShares Ultra Semiconductors 2X
    "TECL": "AMEX",   # Direxion Tech Bull 3X
    "NVDL": "AMEX",   # GraniteShares NVDA 2X
    "SNXX": "AMEX",   # Tradr 2X Long SNDK
    "SPXL": "AMEX",   # Direxion S&P 500 Bull 3X
    "SPXS": "AMEX",   # Direxion S&P 500 Bear 3X
    "TSLL": "AMEX",   # Direxion TSLA Bull 2X
    "LABU": "AMEX",   # Direxion Bio Bull 3X
    "LABD": "AMEX",   # Direxion Bio Bear 3X
    # ETF (1X)
    "QQQ": "NASD",
    "SOXX": "NASD",
    "SMH":  "NASD",
    "SPY":  "AMEX",
    "VOO":  "AMEX",
    "DIA":  "AMEX",
}

# 정수(1주) 매매만 지원하는 종목 — KIS 응답 "매매단위: 1" 종목들
# 사용자 KIS 앱 캡쳐로 확인. fractional=False로 매수 처리.
INTEGER_ONLY_TICKERS: set[str] = {
    "SOXL", "SOXS", "TQQQ", "SQQQ", "USD", "TECL", "NVDL", "SNXX",
    "SPXL", "SPXS", "TSLL", "LABU", "LABD",
}

# #311: KIS 분봉 미지원 종목 (HHDFS76950200 응답 빈 결과)
# 활성화해도 OHLCV 조회 실패로 평가 불가. Admin UI에서 dim 처리.
KIS_MINUTE_UNSUPPORTED: set[str] = {
    "TQQQ", "SQQQ", "NVDL", "TSLL", "BIB", "QLD",
}


class KISUSExchange(Exchange):
    """KIS 미국주식 어댑터.

    Args:
        token_manager: 한국주식과 토큰 공유 (동일 키)
        account_number: 계좌번호 8자리
        account_product_code: 상품코드 2자리
        is_paper: 모의투자 여부
    """

    # 미국 거래시간 (NY 현지 09:30~16:00 → KST 23:30~06:00, 서머타임은 22:30~05:00)
    # zoneinfo로 NY 시간 직접 비교가 정확
    NY_OPEN = time(9, 30)
    NY_CLOSE = time(16, 0)

    # 환전 스프레드 추정 (왕복 평균 0.5%, 단방향 0.25%) + 위탁수수료 0.07%
    BROKER_FEE_RATE = 0.0007  # 0.07%
    FX_SPREAD_RATE = 0.0025  # 0.25% 단방향

    def __init__(
        self,
        token_manager: KISTokenManager,
        account_number: str,
        account_product_code: str = "01",
        is_paper: bool = False,
    ) -> None:
        if not account_number:
            raise ConfigError("KIS 계좌번호 미설정")
        self._tm = token_manager
        self._client = KISClient(token_manager, is_paper=is_paper)
        self._cano = account_number
        self._acnt_prdt_cd = account_product_code
        self._tr_ids = TR_ID_PAPER if is_paper else TR_ID_REAL
        self._is_paper = is_paper
        # #297: OHLCV 캐시 — 일봉 RSI/MA는 일중 자주 안 바뀌니 60초 캐시
        # rate limit (KIS 초당 5건) 회피 + dailyprice API 부하 절감
        self._ohlcv_cache: dict[str, tuple[float, "pd.DataFrame"]] = {}
        self._ohlcv_cache_ttl_sec = 60
        # #319: 잔고 조회 캐시 (rate limit 회피)
        # 매 틱마다 종목별 보유량 + USD 잔고 호출 → 5+ 건 누적
        # 잔고는 매수/매도 직후만 변경되므로 짧은 캐시 OK (30초)
        self._balance_cache: dict[str, tuple[float, float]] = {}
        self._balance_cache_ttl_sec = 30
        self._present_balance_cache: tuple[float, dict] | None = None
        self._present_balance_ttl_sec = 30

    # ---- 메타 ----

    @property
    def market_id(self) -> str:
        return "kis_us"

    @property
    def is_ready(self) -> bool:
        try:
            self._tm.get_token()
            return True
        except Exception:
            return False

    def is_market_open(self) -> bool:
        """미국 정규장 (NY 09:30~16:00) — 서머타임 zoneinfo가 자동 처리."""
        ny_now = datetime.now(NY)
        if ny_now.weekday() >= 5:
            return False
        return self.NY_OPEN <= ny_now.time() <= self.NY_CLOSE

    # ---- 잔고/시세 ----

    def get_balance(self, asset: str) -> float:
        """자산별 잔고.

        Args:
            asset: "USD"(외화예수금) / "KRW"(원화예수금) / 종목 ticker (예: "NVDA")

        USD/KRW 예수금은 inquire-present-balance API의 output2/output3에서 조회.
        - output2: 통화별 외화 잔고 (USD 단위 frcr_dncl_amt_2)
        - output3.tot_dncl_amt: 원화예수금 (KRW)
        종목 보유는 inquire-balance API의 output1.
        """
        if asset == "USD":
            present = self._inquire_present_balance()
            for row in present.get("output2") or []:
                if row.get("crcy_cd") == "USD":
                    return float(row.get("frcr_dncl_amt_2", 0))
            return 0.0

        if asset == "KRW":
            present = self._inquire_present_balance()
            output3 = present.get("output3") or {}
            return float(output3.get("tot_dncl_amt", 0))

        # 종목별 보유 수량 (소수점 가능)
        data = self._inquire_balance()
        for row in data.get("output1", []):
            if row.get("ovrs_pdno") == asset:
                return float(row.get("ovrs_cblc_qty", 0))
        return 0.0

    def get_current_price(self, symbol: str) -> float:
        """현재가 조회.

        Args:
            symbol: ticker (예: "NVDA"). 거래소는 자동 매핑.
        """
        excd = self._quote_exchange_code(symbol)  # 시세는 3글자
        data = self._client.get(
            "/uapi/overseas-price/v1/quotations/price",
            tr_id=self._tr_ids["current_price"],
            params={
                "AUTH": "",
                "EXCD": excd,
                "SYMB": symbol,
            },
        )
        output = data.get("output", {})
        last = output.get("last")
        if not last:
            raise APIError(f"미국주식 현재가 조회 실패: {symbol}")
        return float(last)

    def get_ohlcv(
        self,
        symbol: str,
        interval: str = "day",
        count: int = 200,
    ) -> pd.DataFrame:
        """OHLCV 조회 (미국 시간 기준).

        Args:
            interval: "day" (일봉) 또는 "1min"/"5min"/"15min"/"30min"/"60min" (분봉, #299)
            count: 조회 행 수

        #297: 캐시 — 30초 폴링에 매번 호출하면 rate limit 걸림.
        - 일봉: 60초 캐시 (일중 미세변동만)
        - 분봉: interval 분량의 절반 캐시 (예: 15분봉 → 7분 캐시)
        """
        # 캐시 체크 (interval별 분리)
        import time as _time
        cache_key = f"{symbol}|{interval}"
        cached = self._ohlcv_cache.get(cache_key)
        ttl = self._ohlcv_cache_ttl_sec
        if interval != "day":
            try:
                mins = int(interval.replace("min", ""))
                ttl = max(20, mins * 30)  # 분봉은 N분의 절반(=30초×N) TTL
            except ValueError:
                pass
        if cached and (_time.time() - cached[0]) < ttl:
            df_cached = cached[1]
            return df_cached.tail(count)

        excd = self._quote_exchange_code(symbol)

        if interval == "day":
            df = self._fetch_daily(symbol, excd, count)
        else:
            df = self._fetch_minute(symbol, excd, interval, count)

        self._ohlcv_cache[cache_key] = (_time.time(), df)
        return df.tail(count)

    def _fetch_daily(self, symbol: str, excd: str, count: int) -> pd.DataFrame:
        end_date = datetime.now(NY).strftime("%Y%m%d")
        from datetime import timedelta as _td

        start_date = (datetime.now(NY) - _td(days=count * 2)).strftime("%Y%m%d")

        data = self._client.get(
            "/uapi/overseas-price/v1/quotations/dailyprice",
            tr_id=self._tr_ids["daily_chart"],
            params={
                "AUTH": "",
                "EXCD": excd,
                "SYMB": symbol,
                "GUBN": "0",
                "BYMD": end_date,
                "MODP": "1",
            },
        )
        rows = data.get("output2", [])
        if not rows:
            raise APIError(f"미국주식 OHLCV 조회 결과 없음: {symbol}")

        df_rows = []
        for r in rows:
            try:
                df_rows.append(
                    {
                        "date": pd.to_datetime(r["xymd"], format="%Y%m%d"),
                        "open": float(r["open"]),
                        "high": float(r["high"]),
                        "low": float(r["low"]),
                        "close": float(r["clos"]),
                        "volume": float(r["tvol"]),
                    }
                )
            except (KeyError, ValueError):
                continue
        df = pd.DataFrame(df_rows).sort_values("date").set_index("date")
        df = df[df.index >= pd.to_datetime(start_date, format="%Y%m%d")]
        return df

    def _fetch_minute(self, symbol: str, excd: str, interval: str, count: int) -> pd.DataFrame:
        """분봉 OHLCV (#299).

        TR_ID: HHDFS76950200, EP: /quotations/inquire-time-itemchartprice.
        응답 output2 필드: tymd, xymd, xhms, open, high, low, last, evol, eamt
        """
        try:
            nmin = int(interval.replace("min", ""))
        except ValueError as e:
            raise ValueError(f"interval은 'day' 또는 'Nmin' 형식 (요청: {interval})") from e
        if nmin not in (1, 5, 15, 30, 60):
            raise ValueError(f"분봉은 1/5/15/30/60만 지원 (요청: {nmin})")

        nrec = max(50, min(count, 200))  # KIS 분봉 최대 200건/호출
        data = self._client.get(
            "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice",
            tr_id="HHDFS76950200",
            params={
                "AUTH": "",
                "EXCD": excd,
                "SYMB": symbol,
                "NMIN": str(nmin),
                "PINC": "1",        # 1=최근부터 역순
                "NEXT": "",
                "NREC": str(nrec),
                "FILL": "",
                "KEYB": "",
            },
        )
        rows = data.get("output2", []) or []
        if not rows:
            raise APIError(f"미국주식 분봉 조회 결과 없음: {symbol} ({interval})")

        df_rows = []
        for r in rows:
            try:
                # tymd=YYYYMMDD, xhms=HHMMSS (NY 현지 시간)
                ts = pd.to_datetime(f"{r['tymd']} {r['xhms']:>06}", format="%Y%m%d %H%M%S")
                df_rows.append(
                    {
                        "date": ts,
                        "open": float(r["open"]),
                        "high": float(r["high"]),
                        "low": float(r["low"]),
                        "close": float(r["last"]),  # 분봉은 'last'가 종가
                        "volume": float(r.get("evol", 0)),
                    }
                )
            except (KeyError, ValueError):
                continue
        df = pd.DataFrame(df_rows).sort_values("date").set_index("date")
        return df

    # ---- 주문 ----

    def buy_market(self, symbol: str, amount: float) -> OrderResult:
        """시장가 매수.

        Args:
            symbol: ticker
            amount: 매수 수량 (소수점 가능, 예: 0.5)

        Note:
            KIS 미국주식 시장가는 'OVRS_ORD_UNPR=0' + 'ORD_DVSN=00' 으로 처리.
            소수점 거래는 일부 종목 한정 (S&P500/NASDAQ100 등) — KIS API 응답에서 거부될 수 있음.
            매매단위 1주만 지원하는 종목(레버리지 ETF 등)은 INTEGER_ONLY_TICKERS에서 정수 강제.
        """
        # 정수 매매단위 종목은 floor 처리
        if symbol in INTEGER_ONLY_TICKERS:
            amount = float(int(amount))
        if amount <= 0:
            return OrderResult(
                success=False,
                side="buy",
                coin=symbol,
                price=0,
                amount=0,
                total_krw=0,
                fee_krw=0,
                error="매수 수량 0 이하",
            )

        excd = self._exchange_code(symbol)
        # #317: KIS 미국주식은 시장가 주문 코드 없음 (00=지정가만).
        # OVRS_ORD_UNPR=0 보내면 "MCA 전문바디 구성 중 오류" 에러.
        # 시장가 효과 위해 현재가 +0.5% 버퍼로 지정가 주문 (체결 보장).
        try:
            cur_price = self.get_current_price(symbol)
        except APIError:
            raise
        order_price = round(cur_price * 1.005, 2)  # +0.5% 슬리피지 버퍼
        # #322: KIS는 ORD_QTY 정수 문자열 요구 ("1" OK, "1.0" 거부 → "MCA 전문바디 오류")
        # INTEGER_ONLY_TICKERS면 정수, 아니면 소수점 4자리까지
        if symbol in INTEGER_ONLY_TICKERS:
            qty_str = str(int(amount))
        else:
            qty_str = f"{amount:g}" if amount == int(amount) else f"{amount:.4f}"
        try:
            data = self._client.post(
                "/uapi/overseas-stock/v1/trading/order",
                tr_id=self._tr_ids["buy_market"],
                body={
                    "CANO": self._cano,
                    "ACNT_PRDT_CD": self._acnt_prdt_cd,
                    "OVRS_EXCG_CD": excd,
                    "PDNO": symbol,
                    "ORD_QTY": qty_str,
                    "OVRS_ORD_UNPR": str(order_price),
                    "ORD_SVR_DVSN_CD": "0",
                    "ORD_DVSN": "00",
                },
            )
        except APIError:
            raise
        except Exception as e:
            raise APIError(f"미국주식 매수 실패: {e}") from e

        output = data.get("output", {})
        order_no = output.get("ODNO")

        usd_price = cur_price  # 위에서 조회한 가격 재사용 (rate limit 회피)
        usd_total = usd_price * amount
        # 환율은 통합증거금 사용 시 KIS 자동환전 — 추정용 기본 1,380 KRW/USD
        # 정확한 환율은 잔고 조회로 별도 reconcile (TODO)
        fx_rate = 1380.0
        krw_total = usd_total * fx_rate
        fee_krw = krw_total * (self.BROKER_FEE_RATE + self.FX_SPREAD_RATE)

        logger.info(
            "미국주식 매수 주문: %s %.4f주 @ $%.2f (주문번호 %s)",
            symbol,
            amount,
            usd_price,
            order_no,
        )
        return OrderResult(
            success=True,
            side="buy",
            coin=symbol,
            price=usd_price,  # USD 기준 (DB는 USD 가격으로 저장)
            amount=amount,
            total_krw=krw_total,
            fee_krw=fee_krw,
            raw_response=data,
            order_uuid=order_no,
        )

    def sell_market(self, symbol: str, amount: float | None = None) -> OrderResult:
        """시장가 매도."""
        if amount is None:
            amount = self.get_balance(symbol)
        if amount is None or amount <= 0:
            return OrderResult(
                success=False,
                side="sell",
                coin=symbol,
                price=0,
                amount=0,
                total_krw=0,
                fee_krw=0,
                error="매도 수량 0",
            )

        excd = self._exchange_code(symbol)
        # #317: 매도도 시장가 코드 없음 → 현재가 -0.5% 버퍼 지정가 (체결 보장)
        try:
            cur_price = self.get_current_price(symbol)
        except APIError:
            raise
        order_price = round(cur_price * 0.995, 2)  # -0.5% 슬리피지 버퍼
        # #322: ORD_QTY 정수 문자열 (KIS body 검증 통과)
        if symbol in INTEGER_ONLY_TICKERS:
            qty_str = str(int(amount))
        else:
            qty_str = f"{amount:g}" if amount == int(amount) else f"{amount:.4f}"
        try:
            data = self._client.post(
                "/uapi/overseas-stock/v1/trading/order",
                tr_id=self._tr_ids["sell_market"],
                body={
                    "CANO": self._cano,
                    "ACNT_PRDT_CD": self._acnt_prdt_cd,
                    "OVRS_EXCG_CD": excd,
                    "PDNO": symbol,
                    "ORD_QTY": qty_str,
                    "OVRS_ORD_UNPR": str(order_price),
                    "ORD_SVR_DVSN_CD": "0",
                    "ORD_DVSN": "00",
                },
            )
        except APIError:
            raise
        except Exception as e:
            raise APIError(f"미국주식 매도 실패: {e}") from e

        output = data.get("output", {})
        order_no = output.get("ODNO")

        usd_price = self.get_current_price(symbol)
        usd_total = usd_price * amount
        fx_rate = 1380.0
        krw_total = usd_total * fx_rate
        fee_krw = krw_total * (self.BROKER_FEE_RATE + self.FX_SPREAD_RATE)

        logger.info(
            "미국주식 매도 주문: %s %.4f주 @ $%.2f (주문번호 %s)",
            symbol,
            amount,
            usd_price,
            order_no,
        )
        return OrderResult(
            success=True,
            side="sell",
            coin=symbol,
            price=usd_price,
            amount=amount,
            total_krw=krw_total,
            fee_krw=fee_krw,
            raw_response=data,
            order_uuid=order_no,
        )

    def cancel_all_orders(self, symbol: str) -> int:
        """미체결 취소 — TODO."""
        logger.warning("cancel_all_orders 미구현 (KIS US): %s", symbol)
        return 0

    def get_order_detail(self, order_id: str) -> dict | None:
        """주문 상세 — TODO."""
        logger.debug("get_order_detail 미구현 (KIS US): %s", order_id)
        return None

    # ---- 거래소 특수 ----

    def tick_size(self, symbol: str, price: float) -> float:
        """미국주식 호가단위 — 0.01 USD (소수점 둘째자리)."""
        return 0.01

    def get_deposit_history(self, currency: str = "USD", limit: int = 100) -> list[dict]:
        """입금 내역 — TODO."""
        logger.debug("get_deposit_history 미구현 (KIS US)")
        return []

    # ---- 내부 ----

    def _exchange_code(self, symbol: str) -> str:
        """ticker → 주문/잔고용 거래소 코드 (4글자: NASD/NYSE/AMEX).

        풀에 없으면 NASDAQ 기본값. 추후 종목 마스터 도입 시 정확화.
        """
        return DEFAULT_EXCHANGE_BY_TICKER.get(symbol, "NASD")

    def _quote_exchange_code(self, symbol: str) -> str:
        """ticker → 시세 API용 거래소 코드 (3글자: NAS/NYS/AMS).

        KIS는 시세 API와 주문 API가 거래소 코드 체계가 다름:
        - 시세 (/quotations/price, /quotations/dailyprice): EXCD 3글자
        - 주문/잔고 (/trading/order, /inquire-balance): OVRS_EXCG_CD 4글자
        """
        order_code = DEFAULT_EXCHANGE_BY_TICKER.get(symbol, "NASD")
        return {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}.get(order_code, "NAS")

    def _inquire_balance(self) -> dict:
        # #319: 30초 캐시 (rate limit 회피). 매수/매도 시 invalidate_balance_cache 호출.
        import time as _time
        cached = self._balance_cache.get("__inquire_balance__")
        if cached and (_time.time() - cached[0]) < self._balance_cache_ttl_sec:
            return cached[1]
        data = self._client.get(
            "/uapi/overseas-stock/v1/trading/inquire-balance",
            tr_id=self._tr_ids["balance"],
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "OVRS_EXCG_CD": "NASD",
                "TR_CRCY_CD": "USD",
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": "",
            },
        )
        self._balance_cache["__inquire_balance__"] = (_time.time(), data)
        return data

    def invalidate_balance_cache(self) -> None:
        """매수/매도 직후 호출하여 잔고 캐시 무효화."""
        self._balance_cache.clear()
        self._present_balance_cache = None

    def _inquire_present_balance(self) -> dict:
        """해외주식 현재잔고 조회 — 외화예수금/원화예수금/환율 포함. 30초 캐시 (#319).

        TR_ID: CTRP6504R (실전 전용. 모의 미지원).

        응답 구조:
        - output2[]: 통화별 외화 잔고
            * crcy_cd: USD
            * frcr_dncl_amt_2: 외화예수금 (USD 단위)
            * frst_bltn_exrt: 환율 (KRW/USD)
            * frcr_drwg_psbl_amt_1: 외화 출금가능금액
        - output3: 계좌 요약
            * tot_dncl_amt: 원화예수금
            * tot_asst_amt: 총자산 (KRW)
            * frcr_evlu_tota: 외화평가총액 (KRW 환산)
        """
        if self._is_paper:
            return {"output2": [], "output3": {}}
        # #319: 30초 캐시 (USD/KRW 잔고 + 환율)
        import time as _time
        if self._present_balance_cache and (_time.time() - self._present_balance_cache[0]) < self._present_balance_ttl_sec:
            return self._present_balance_cache[1]
        data = self._client.get(
            "/uapi/overseas-stock/v1/trading/inquire-present-balance",
            tr_id="CTRP6504R",
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "WCRC_FRCR_DVSN_CD": "02",
                "NATN_CD": "840",
                "TR_MKET_CD": "00",
                "INQR_DVSN_CD": "00",
            },
        )
        self._present_balance_cache = (_time.time(), data)
        return data

    def get_fx_rate_krw_per_usd(self) -> float | None:
        """현재 KRW/USD 환율 (KIS 잔고 응답의 frst_bltn_exrt). 조회 실패 시 None."""
        try:
            present = self._inquire_present_balance()
            for row in present.get("output2") or []:
                if row.get("crcy_cd") == "USD":
                    rate = float(row.get("frst_bltn_exrt", 0))
                    return rate if rate > 0 else None
        except Exception as e:
            logger.warning("FX rate 조회 실패: %s", e)
        return None
