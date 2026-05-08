"""거래소 추상 인터페이스.

NestJS의 외부 API 추상 클래스(Strategy/Adapter 패턴)와 동일한 역할.
모든 거래소(Upbit, KIS 한국주식, KIS 미국주식)는 이 인터페이스를 구현한다.

Related: #244
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


@dataclass
class OrderResult:
    """주문 실행 결과.

    Attributes:
        success: 주문 성공 여부
        side: "buy" / "sell"
        coin: 종목 코드 (코인은 "KRW-BTC", 한국주식은 "005930", 미국주식은 "AAPL")
              ※ 호환성 유지를 위해 필드명 'coin' 유지. 의미는 일반 'symbol'.
        price: 평균 체결가
        amount: 체결 수량
        total_krw: 체결 총금액 (원화 기준 환산)
        fee_krw: 수수료 (원화 기준)
        raw_response: 거래소 원본 응답
        error: 에러 메시지
        order_uuid: 거래소 주문 식별자
    """

    success: bool
    side: str
    coin: str
    price: float
    amount: float
    total_krw: float
    fee_krw: float
    raw_response: dict | None = None
    error: str | None = None
    order_uuid: str | None = None


class Exchange(ABC):
    """거래소 추상 인터페이스.

    구현체는 시장(코인/한국주식/미국주식)별로 분리되며 공통 메서드 시그니처를 따른다.
    개별 거래소의 특수 로직(rate limit, 토큰 갱신 등)은 어댑터 내부에서 처리.
    """

    # ---- 메타 ----

    @property
    @abstractmethod
    def market_id(self) -> str:
        """DB `market` 컬럼에 기록되는 시장 식별자.

        Returns:
            "upbit" / "kis_kr" / "kis_us"
        """

    @property
    @abstractmethod
    def is_ready(self) -> bool:
        """주문 가능 상태 (API Key·토큰 정상)."""

    @abstractmethod
    def is_market_open(self) -> bool:
        """현재 거래 가능 시간 여부.

        - 코인: 항상 True
        - 한국주식: 평일 09:00~15:30 KST (동시호가 별도 처리)
        - 미국주식: 23:30~06:00 KST (서머타임 22:30~05:00)
        """

    # ---- 잔고/시세 ----

    @abstractmethod
    def get_balance(self, asset: str) -> float:
        """자산별 잔고.

        Args:
            asset: "KRW" / "USD" / "BTC" / "AAPL" / "005930" 등
        """

    @abstractmethod
    def get_current_price(self, symbol: str) -> float:
        """현재가 조회."""

    @abstractmethod
    def get_ohlcv(
        self,
        symbol: str,
        interval: str = "day",
        count: int = 200,
    ) -> pd.DataFrame:
        """OHLCV 시계열 조회.

        Args:
            symbol: 종목 코드
            interval: "day" / "minute60" / "minute5" 등 (거래소별 매핑)
            count: 가져올 캔들 수

        Returns:
            DataFrame[index=datetime, columns=open/high/low/close/volume]
        """

    # ---- 주문 ----

    @abstractmethod
    def buy_market(self, symbol: str, amount: float) -> OrderResult:
        """시장가 매수.

        Args:
            symbol: 종목 코드
            amount: 거래소별 의미 다름
                - Upbit: KRW 금액
                - KIS 한국주식: 주식 수량(주)
                - KIS 미국주식: 주식 수량(소수점 가능)
        """

    @abstractmethod
    def sell_market(self, symbol: str, amount: float | None = None) -> OrderResult:
        """시장가 매도.

        Args:
            symbol: 종목 코드
            amount: 매도 수량. None이면 전량 매도
        """

    @abstractmethod
    def cancel_all_orders(self, symbol: str) -> int:
        """미체결 주문 전부 취소."""

    @abstractmethod
    def get_order_detail(self, order_id: str) -> dict | None:
        """주문 ID로 체결 상세 조회.

        Returns:
            {"price": 평균체결가, "volume": 체결수량, "funds": 체결금액, "fee": 수수료}
            미체결/오류 시 None
        """

    # ---- 거래소 특수 ----

    @abstractmethod
    def tick_size(self, symbol: str, price: float) -> float:
        """주어진 가격대의 호가단위 반환.

        - 코인(Upbit): pyupbit가 자동 처리. 가격 그대로 반환
        - 한국주식: 가격대별 1/5/10/50/100/500/1000원
        - 미국주식: 0.01 USD (소수점 둘째자리)
        """

    @abstractmethod
    def get_deposit_history(self, currency: str, limit: int) -> list[dict]:
        """입금 내역 조회.

        Returns:
            [{"uuid": str, "amount_krw": float, "deposited_at": str}, ...]
        """
