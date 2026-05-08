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
    "QQQ": "NASD",
    "SPY": "NYSE",
    "VOO": "NYSE",
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
            asset: "USD"(예수금) / "KRW"(원화 예수금) / 종목 ticker (예: "NVDA")
        """
        data = self._inquire_balance()

        if asset in ("USD", "KRW"):
            output2 = data.get("output2", {})
            if asset == "USD":
                # frcr_dncl_amt1 = 외화예수금
                return float(output2.get("frcr_dncl_amt1", 0))
            # KRW 예수금
            return float(output2.get("krw_dncl_amt", 0))

        # 종목별 보유 수량 (소수점 가능)
        for row in data.get("output1", []):
            if row.get("ovrs_pdno") == asset:
                return float(row.get("ovrs_cblc_qty", 0))
        return 0.0

    def get_current_price(self, symbol: str) -> float:
        """현재가 조회.

        Args:
            symbol: ticker (예: "NVDA"). 거래소는 자동 매핑.
        """
        excd = self._exchange_code(symbol)
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
        """OHLCV 일봉 조회 (미국 시간 기준)."""
        if interval != "day":
            raise ValueError(f"미국주식은 day만 지원 (요청: {interval})")

        excd = self._exchange_code(symbol)
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
                "GUBN": "0",  # 0=일봉, 1=주봉, 2=월봉
                "BYMD": end_date,
                "MODP": "1",  # 1=수정주가
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
        # KIS는 시작 날짜로부터 역순으로 응답하기도 하므로 start_date 이후만 필터
        df = df[df.index >= pd.to_datetime(start_date, format="%Y%m%d")]
        return df.tail(count)

    # ---- 주문 ----

    def buy_market(self, symbol: str, amount: float) -> OrderResult:
        """시장가 매수.

        Args:
            symbol: ticker
            amount: 매수 수량 (소수점 가능, 예: 0.5)

        Note:
            KIS 미국주식 시장가는 'OVRS_ORD_UNPR=0' + 'ORD_DVSN=00' 으로 처리.
            소수점 거래는 일부 종목 한정 (S&P500/NASDAQ100 등) — KIS API 응답에서 거부될 수 있음.
        """
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
        try:
            data = self._client.post(
                "/uapi/overseas-stock/v1/trading/order",
                tr_id=self._tr_ids["buy_market"],
                body={
                    "CANO": self._cano,
                    "ACNT_PRDT_CD": self._acnt_prdt_cd,
                    "OVRS_EXCG_CD": excd,
                    "PDNO": symbol,
                    "ORD_QTY": str(amount),
                    "OVRS_ORD_UNPR": "0",
                    "ORD_SVR_DVSN_CD": "0",
                    "ORD_DVSN": "00",  # 00=지정가/시장가 (미국은 시장가가 별도 코드 없음)
                },
            )
        except APIError:
            raise
        except Exception as e:
            raise APIError(f"미국주식 매수 실패: {e}") from e

        output = data.get("output", {})
        order_no = output.get("ODNO")

        usd_price = self.get_current_price(symbol)
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
        try:
            data = self._client.post(
                "/uapi/overseas-stock/v1/trading/order",
                tr_id=self._tr_ids["sell_market"],
                body={
                    "CANO": self._cano,
                    "ACNT_PRDT_CD": self._acnt_prdt_cd,
                    "OVRS_EXCG_CD": excd,
                    "PDNO": symbol,
                    "ORD_QTY": str(amount),
                    "OVRS_ORD_UNPR": "0",
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
        """ticker → 거래소 코드 매핑.

        풀에 없으면 NASDAQ 기본값. 추후 종목 마스터 도입 시 정확화.
        """
        return DEFAULT_EXCHANGE_BY_TICKER.get(symbol, "NASD")

    def _inquire_balance(self) -> dict:
        return self._client.get(
            "/uapi/overseas-stock/v1/trading/inquire-balance",
            tr_id=self._tr_ids["balance"],
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "OVRS_EXCG_CD": "NASD",  # 통합조회는 거래소 무관하게 동작
                "TR_CRCY_CD": "USD",
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": "",
            },
        )
