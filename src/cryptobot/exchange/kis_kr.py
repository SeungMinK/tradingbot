"""KIS Developers 한국주식 거래소 어댑터.

한국투자증권 OpenAPI를 통해 한국주식 매매를 실행.
`Exchange` 인터페이스 구현. 코인 봇과 동일 패러다임으로 운영.

KIS 공식 레포(koreainvestment/open-trading-api)의 examples_user/domestic_stock 패턴 참조.

Related: #246
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

# 한국주식 호가단위 (2023.01.25~ 통합 적용, 코스피·코스닥 동일)
TICK_SIZE_TABLE_KR = [
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
    (float("inf"), 1_000),
]

# KIS tr_id 매핑 (실전 / 모의)
TR_ID_REAL = {
    "balance": "TTTC8434R",
    "buy_market": "TTTC0802U",
    "sell_market": "TTTC0801U",
    "cancel_revise": "TTTC0803U",
    "rvsecncl_inquire": "TTTC8036R",
    "current_price": "FHKST01010100",
    "daily_chart": "FHKST03010100",
    "minute_chart": "FHKST03010200",
}
TR_ID_PAPER = {
    "balance": "VTTC8434R",
    "buy_market": "VTTC0802U",
    "sell_market": "VTTC0801U",
    "cancel_revise": "VTTC0803U",
    "rvsecncl_inquire": "VTTC8036R",
    "current_price": "FHKST01010100",
    "daily_chart": "FHKST03010100",
    "minute_chart": "FHKST03010200",
}


def korean_stock_tick_size(price: float) -> int:
    """한국주식 가격대별 호가단위 반환."""
    for upper, tick in TICK_SIZE_TABLE_KR:
        if price < upper:
            return tick
    return 1_000


class KISKoreanExchange(Exchange):
    """KIS 한국주식 어댑터.

    Args:
        token_manager: KIS 토큰 매니저 (KISTokenManager)
        account_number: 계좌번호 8자리 (예: '12345678')
        account_product_code: 상품코드 2자리 (대부분 '01')
        is_paper: 모의투자 여부
    """

    # 한국주식 거래시간 (정규장)
    MARKET_OPEN = time(9, 0)
    MARKET_CLOSE = time(15, 30)

    # 거래세 (매도 시 0.18%)
    SELL_TAX_RATE = 0.0018
    # 위탁수수료 추정 (KIS 기본수수료 약 0.015%, 통합증거금 추가비용 별도)
    BROKER_FEE_RATE = 0.00015

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
        self._cano = account_number  # 계좌번호 앞 8자리
        self._acnt_prdt_cd = account_product_code  # 상품코드 2자리
        self._tr_ids = TR_ID_PAPER if is_paper else TR_ID_REAL
        self._is_paper = is_paper

    # ---- 메타 ----

    @property
    def market_id(self) -> str:
        return "kis_kr"

    @property
    def is_ready(self) -> bool:
        try:
            self._tm.get_token()
            return True
        except Exception:
            return False

    def is_market_open(self) -> bool:
        """평일 09:00~15:30 KST 정규장만 True.

        동시호가(08:30~09:00, 15:20~15:30)는 처리 복잡성 때문에 False로 둠.
        주말·공휴일은 KIS API 거부 응답으로 자연 차단.
        """
        now = datetime.now(KST)
        if now.weekday() >= 5:  # 토(5), 일(6)
            return False
        return self.MARKET_OPEN <= now.time() <= self.MARKET_CLOSE

    # ---- 잔고/시세 ----

    def get_balance(self, asset: str) -> float:
        """자산별 잔고.

        Args:
            asset: "KRW" (예수금) 또는 종목코드 6자리 (예: "005930")
        """
        data = self._inquire_balance()
        if asset == "KRW":
            # output2: 잔고 요약. dnca_tot_amt = 예수금 총금액
            output2 = data.get("output2", [])
            if not output2:
                return 0.0
            return float(output2[0].get("dnca_tot_amt", 0))

        # 종목별 보유량
        for row in data.get("output1", []):
            if row.get("pdno") == asset:
                return float(row.get("hldg_qty", 0))
        return 0.0

    def get_current_price(self, symbol: str) -> float:
        """현재가 조회 (종목코드 6자리)."""
        data = self._client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id=self._tr_ids["current_price"],
            params={
                "FID_COND_MRKT_DIV_CODE": "J",  # J=KRX
                "FID_INPUT_ISCD": symbol,
            },
        )
        output = data.get("output", {})
        price_str = output.get("stck_prpr")
        if price_str is None:
            raise APIError(f"현재가 조회 응답에 stck_prpr 없음: {symbol}")
        return float(price_str)

    def get_ohlcv(
        self,
        symbol: str,
        interval: str = "day",
        count: int = 200,
    ) -> pd.DataFrame:
        """OHLCV 일봉 조회.

        Args:
            symbol: 종목코드 6자리
            interval: "day" / "week" / "month" (분봉은 별도 API 필요)
            count: 가져올 캔들 수 (KIS는 한 번에 최대 100)
        """
        # 일/주/월봉
        period_map = {"day": "D", "week": "W", "month": "M"}
        if interval not in period_map:
            raise ValueError(f"interval={interval} 미지원 (day/week/month)")

        end_date = datetime.now(KST).strftime("%Y%m%d")
        # 시작일은 count×7일 정도로 여유있게 잡고 KIS 응답에서 잘라냄
        from datetime import timedelta as _td

        start_date = (datetime.now(KST) - _td(days=count * 2)).strftime("%Y%m%d")

        data = self._client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            tr_id=self._tr_ids["daily_chart"],
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_DATE_1": start_date,
                "FID_INPUT_DATE_2": end_date,
                "FID_PERIOD_DIV_CODE": period_map[interval],
                "FID_ORG_ADJ_PRC": "0",  # 0=수정주가, 1=원주가
            },
        )

        rows = data.get("output2", [])
        if not rows:
            raise APIError(f"OHLCV 조회 결과 없음: {symbol}")

        df_rows = []
        for r in rows:
            try:
                df_rows.append(
                    {
                        "date": pd.to_datetime(r["stck_bsop_date"], format="%Y%m%d"),
                        "open": float(r["stck_oprc"]),
                        "high": float(r["stck_hgpr"]),
                        "low": float(r["stck_lwpr"]),
                        "close": float(r["stck_clpr"]),
                        "volume": float(r["acml_vol"]),
                    }
                )
            except (KeyError, ValueError):
                continue
        df = pd.DataFrame(df_rows).sort_values("date").set_index("date")
        return df.tail(count)

    # ---- 주문 ----

    def buy_market(self, symbol: str, amount: float) -> OrderResult:
        """시장가 매수.

        Args:
            symbol: 종목코드 6자리
            amount: 매수 수량(주). KIS 한국주식은 1주 단위 정수.
        """
        qty = int(amount)
        if qty <= 0:
            return OrderResult(
                success=False,
                side="buy",
                coin=symbol,
                price=0,
                amount=0,
                total_krw=0,
                fee_krw=0,
                error="매수 수량이 0 이하",
            )

        try:
            data = self._client.post(
                "/uapi/domestic-stock/v1/trading/order-cash",
                tr_id=self._tr_ids["buy_market"],
                body={
                    "CANO": self._cano,
                    "ACNT_PRDT_CD": self._acnt_prdt_cd,
                    "PDNO": symbol,
                    "ORD_DVSN": "01",  # 01=시장가
                    "ORD_QTY": str(qty),
                    "ORD_UNPR": "0",  # 시장가 = 0
                },
            )
        except APIError:
            raise
        except Exception as e:
            raise APIError(f"한국주식 매수 실패: {e}") from e

        output = data.get("output", {})
        order_no = output.get("ODNO")

        # 체결가는 별도 조회 필요. 시장가 매수 직후엔 추정값으로 기록 후 reconcile.
        est_price = self.get_current_price(symbol)
        est_total = est_price * qty
        # 매수 시 위탁수수료만 (거래세는 매도 시)
        est_fee = est_total * self.BROKER_FEE_RATE

        logger.info("한국주식 매수 주문: %s %d주 (주문번호 %s)", symbol, qty, order_no)
        return OrderResult(
            success=True,
            side="buy",
            coin=symbol,
            price=est_price,
            amount=qty,
            total_krw=est_total,
            fee_krw=est_fee,
            raw_response=data,
            order_uuid=order_no,
        )

    def sell_market(self, symbol: str, amount: float | None = None) -> OrderResult:
        """시장가 매도."""
        if amount is None:
            amount = self.get_balance(symbol)

        qty = int(amount)
        if qty <= 0:
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

        try:
            data = self._client.post(
                "/uapi/domestic-stock/v1/trading/order-cash",
                tr_id=self._tr_ids["sell_market"],
                body={
                    "CANO": self._cano,
                    "ACNT_PRDT_CD": self._acnt_prdt_cd,
                    "PDNO": symbol,
                    "ORD_DVSN": "01",
                    "ORD_QTY": str(qty),
                    "ORD_UNPR": "0",
                },
            )
        except APIError:
            raise
        except Exception as e:
            raise APIError(f"한국주식 매도 실패: {e}") from e

        output = data.get("output", {})
        order_no = output.get("ODNO")

        est_price = self.get_current_price(symbol)
        est_total = est_price * qty
        # 매도 수수료 = 위탁수수료 + 거래세 0.18%
        est_fee = est_total * (self.BROKER_FEE_RATE + self.SELL_TAX_RATE)

        logger.info("한국주식 매도 주문: %s %d주 (주문번호 %s)", symbol, qty, order_no)
        return OrderResult(
            success=True,
            side="sell",
            coin=symbol,
            price=est_price,
            amount=qty,
            total_krw=est_total,
            fee_krw=est_fee,
            raw_response=data,
            order_uuid=order_no,
        )

    def cancel_all_orders(self, symbol: str) -> int:
        """미체결 전부 취소.

        TODO: KIS의 미체결 조회 → 각각 취소. 미구현 상태라 0 반환.
        """
        logger.warning("cancel_all_orders 미구현 (KIS): symbol=%s", symbol)
        return 0

    def get_order_detail(self, order_id: str) -> dict | None:
        """주문 ID로 체결 상세 조회.

        TODO: KIS 일별주문체결조회 API 연동.
        """
        logger.debug("get_order_detail 미구현 (KIS): order_id=%s", order_id)
        return None

    # ---- 거래소 특수 ----

    def tick_size(self, symbol: str, price: float) -> float:
        """한국주식 가격대별 호가단위."""
        return float(korean_stock_tick_size(price))

    def get_deposit_history(self, currency: str = "KRW", limit: int = 100) -> list[dict]:
        """입금 내역.

        TODO: KIS 입출금 조회 API 연동. 현재는 빈 리스트 반환.
        """
        logger.debug("get_deposit_history 미구현 (KIS)")
        return []

    # ---- 내부 ----

    def _inquire_balance(self) -> dict:
        """주식 잔고 조회 원본 응답."""
        return self._client.get(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id=self._tr_ids["balance"],
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "01",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )
