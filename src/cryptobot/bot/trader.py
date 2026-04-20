"""주문 실행 모듈.

NestJS의 외부 API 호출 Service와 동일한 역할.
pyupbit을 통해 업비트에 실제 주문을 실행한다.
"""

import logging
from dataclasses import dataclass

import pyupbit

from cryptobot.bot.config import config
from cryptobot.exceptions import APIError, ConfigError, InsufficientBalanceError

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    """주문 실행 결과."""

    success: bool
    side: str  # "buy" / "sell"
    coin: str
    price: float
    amount: float
    total_krw: float
    fee_krw: float
    raw_response: dict | None = None
    error: str | None = None
    order_uuid: str | None = None


class Trader:
    """업비트 주문 실행기.

    NestJS에서 외부 API를 호출하는 Service와 동일.
    """

    FEE_RATE = 0.0005  # 업비트 수수료 0.05%

    def __init__(self) -> None:
        if not config.upbit.is_configured:
            logger.warning("업비트 API Key 미설정 — 주문 실행 불가 (조회만 가능)")
            self._upbit: pyupbit.Upbit | None = None
        else:
            self._upbit = pyupbit.Upbit(config.upbit.access_key, config.upbit.secret_key)

    @property
    def is_ready(self) -> bool:
        """주문 가능 상태인지 확인."""
        return self._upbit is not None

    def get_balance_krw(self) -> float:
        """원화 잔고 조회."""
        self._ensure_ready()
        try:
            balance = self._upbit.get_balance("KRW")
            return float(balance) if balance else 0.0
        except Exception as e:
            raise APIError(f"잔고 조회 실패: {e}") from e

    def get_balance_coin(self, coin: str) -> float:
        """코인 보유량 조회.

        Args:
            coin: 종목 코드 (예: "KRW-BTC" → "BTC")
        """
        self._ensure_ready()
        try:
            ticker = coin.replace("KRW-", "")
            balance = self._upbit.get_balance(ticker)
            return float(balance) if balance else 0.0
        except Exception as e:
            raise APIError(f"코인 잔고 조회 실패: {e}") from e

    def get_current_price(self, coin: str) -> float:
        """현재가 조회 (API Key 없이도 가능)."""
        try:
            price = pyupbit.get_current_price(coin)
            if price is None:
                raise APIError(f"현재가 조회 실패: {coin}")
            return float(price)
        except Exception as e:
            raise APIError(f"현재가 조회 실패: {e}") from e

    def buy_market(self, coin: str, krw_amount: float) -> OrderResult:
        """시장가 매수.

        Args:
            coin: 종목 코드 (예: "KRW-BTC")
            krw_amount: 매수 금액 (원)

        Returns:
            주문 실행 결과
        """
        self._ensure_ready()

        if krw_amount < 5000:
            return OrderResult(
                success=False,
                side="buy",
                coin=coin,
                price=0,
                amount=0,
                total_krw=0,
                fee_krw=0,
                error="최소 주문 금액 5,000원 미달",
            )

        try:
            balance = self.get_balance_krw()
            if balance < krw_amount:
                raise InsufficientBalanceError(f"잔고 부족: {balance:,.0f}원 < {krw_amount:,.0f}원")

            result = self._upbit.buy_market_order(coin, krw_amount)
            logger.info("매수 주문 실행: %s %s원", coin, f"{krw_amount:,.0f}")

            order_uuid = result.get("uuid") if isinstance(result, dict) else None

            # 추정값 (폴백용)
            est_price = self.get_current_price(coin)
            est_fee = krw_amount * self.FEE_RATE
            est_amount = (krw_amount - est_fee) / est_price

            # 실체결가 조회
            price, amount, total_krw, fee = est_price, est_amount, krw_amount, est_fee
            if order_uuid:
                trade_detail = self._fetch_order_detail(order_uuid)
                if trade_detail:
                    price = trade_detail["price"]
                    amount = trade_detail["volume"]
                    total_krw = trade_detail["funds"]
                    fee = trade_detail["fee"]
                    logger.info("매수 실체결가: %s %.0f원 × %.8f개 (수수료 %.0f원)", coin, price, amount, fee)

            return OrderResult(
                success=True,
                side="buy",
                coin=coin,
                price=price,
                amount=amount,
                total_krw=total_krw,
                fee_krw=fee,
                raw_response=result,
                order_uuid=order_uuid,
            )
        except (InsufficientBalanceError, APIError):
            raise
        except Exception as e:
            raise APIError(f"매수 주문 실패: {e}") from e

    def sell_market(self, coin: str, amount: float | None = None) -> OrderResult:
        """시장가 매도.

        Args:
            coin: 종목 코드 (예: "KRW-BTC")
            amount: 매도 수량 (None이면 전량 매도)

        Returns:
            주문 실행 결과
        """
        self._ensure_ready()

        try:
            if amount is None:
                amount = self.get_balance_coin(coin)

            if amount <= 0:
                return OrderResult(
                    success=False,
                    side="sell",
                    coin=coin,
                    price=0,
                    amount=0,
                    total_krw=0,
                    fee_krw=0,
                    error="매도 가능한 수량 없음",
                )

            result = self._upbit.sell_market_order(coin, amount)
            logger.info("매도 주문 실행: %s %.8f개", coin, amount)

            order_uuid = result.get("uuid") if isinstance(result, dict) else None

            # 추정값 (폴백용)
            est_price = self.get_current_price(coin)
            est_total = est_price * amount
            est_fee = est_total * self.FEE_RATE

            # 실체결가 조회
            price, sell_amount, total_krw, fee = est_price, amount, est_total, est_fee
            if order_uuid:
                trade_detail = self._fetch_order_detail(order_uuid)
                if trade_detail:
                    price = trade_detail["price"]
                    sell_amount = trade_detail["volume"]
                    total_krw = trade_detail["funds"]
                    fee = trade_detail["fee"]
                    logger.info("매도 실체결가: %s %.0f원 × %.8f개 (수수료 %.0f원)", coin, price, sell_amount, fee)

            return OrderResult(
                success=True,
                side="sell",
                coin=coin,
                price=price,
                amount=sell_amount,
                total_krw=total_krw,
                fee_krw=fee,
                raw_response=result,
                order_uuid=order_uuid,
            )
        except APIError:
            raise
        except Exception as e:
            raise APIError(f"매도 주문 실패: {e}") from e

    def cancel_all_orders(self, coin: str) -> int:
        """미체결 주문 전부 취소.

        Args:
            coin: 종목 코드

        Returns:
            취소한 주문 수
        """
        self._ensure_ready()
        try:
            orders = self._upbit.get_order(coin, state="wait")
            if not orders:
                return 0
            for order in orders:
                self._upbit.cancel_order(order["uuid"])
            logger.info("미체결 주문 %d건 취소: %s", len(orders), coin)
            return len(orders)
        except Exception as e:
            raise APIError(f"주문 취소 실패: {e}") from e

    def _fetch_order_detail(self, order_uuid: str, max_retries: int = 3) -> dict | None:
        """주문 UUID로 실체결 상세를 조회한다.

        Args:
            order_uuid: 업비트 주문 UUID
            max_retries: 최대 재시도 횟수

        Returns:
            {"price": 평균체결가, "volume": 체결수량, "funds": 체결금액, "fee": 수수료} 또는 None
        """
        import time as _time

        for attempt in range(max_retries):
            _time.sleep(0.5 * (attempt + 1))
            try:
                detail = self._upbit.get_individual_order(order_uuid)
                if not isinstance(detail, dict):
                    continue

                trades = detail.get("trades", [])
                state = detail.get("state", "")

                if state not in ("done", "cancel") and not trades:
                    logger.debug("주문 미체결 상태 (attempt %d): %s", attempt + 1, state)
                    continue

                if not trades:
                    logger.warning("체결 내역 없음: uuid=%s, state=%s", order_uuid, state)
                    return None

                total_funds = sum(float(t.get("funds", 0)) for t in trades)
                total_volume = sum(float(t.get("volume", 0)) for t in trades)
                paid_fee = float(detail.get("paid_fee", 0))
                avg_price = total_funds / total_volume if total_volume > 0 else 0

                return {
                    "price": avg_price,
                    "volume": total_volume,
                    "funds": total_funds,
                    "fee": paid_fee,
                }
            except Exception as e:
                logger.warning("체결 상세 조회 실패 (attempt %d): %s", attempt + 1, e)

        logger.warning("체결 상세 조회 최종 실패: uuid=%s", order_uuid)
        return None

    def get_order_detail(self, order_uuid: str) -> dict | None:
        """외부에서 주문 UUID로 체결 상세를 조회한다.

        Args:
            order_uuid: 업비트 주문 UUID

        Returns:
            {"price": 평균체결가, "volume": 체결수량, "funds": 체결금액, "fee": 수수료} 또는 None
        """
        self._ensure_ready()
        return self._fetch_order_detail(order_uuid)

    def get_deposit_history(self, currency: str = "KRW", limit: int = 100) -> list[dict]:
        """업비트 입금 내역을 조회한다 (state=ACCEPTED만).

        pyupbit가 입금 API를 노출하지 않아 직접 JWT 서명 후 호출.

        Args:
            currency: 'KRW' 등
            limit: 최대 조회 건수 (업비트는 100이 한도)

        Returns:
            [{"uuid", "amount_krw", "deposited_at"}, ...] (최신순)
        """
        self._ensure_ready()
        import hashlib
        import uuid as _uuid
        from urllib.parse import urlencode

        import jwt
        import requests

        query = {"currency": currency, "state": "ACCEPTED", "limit": str(limit)}
        qhash = hashlib.sha512(urlencode(query).encode()).hexdigest()
        payload = {
            "access_key": config.upbit.access_key,
            "nonce": str(_uuid.uuid4()),
            "query_hash": qhash,
            "query_hash_alg": "SHA512",
        }
        token = jwt.encode(payload, config.upbit.secret_key)
        try:
            resp = requests.get(
                "https://api.upbit.com/v1/deposits",
                params=query,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("입금 내역 조회 실패: %s", e)
            raise APIError(f"입금 내역 조회 실패: {e}") from e

        if not isinstance(data, list):
            return []

        results = []
        for d in data:
            try:
                results.append(
                    {
                        "uuid": d.get("uuid"),
                        "amount_krw": float(d.get("amount", 0)),
                        # done_at(완료시각) 우선, 없으면 created_at
                        "deposited_at": d.get("done_at") or d.get("created_at"),
                    }
                )
            except (TypeError, ValueError):
                continue
        return results

    def _ensure_ready(self) -> None:
        """API Key 설정 확인."""
        if not self.is_ready:
            raise ConfigError("업비트 API Key가 설정되지 않았습니다. .env 파일을 확인하세요.")
