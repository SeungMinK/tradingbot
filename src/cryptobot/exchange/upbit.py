"""업비트 거래소 어댑터.

기존 `bot/trader.py`의 로직을 `Exchange` 인터페이스에 맞춰 이전.
호환성 유지를 위해 `bot/trader.py`는 이 클래스를 `Trader`로 re-export한다.

Related: #244
"""

from __future__ import annotations

import hashlib
import logging
import time as _time
import uuid as _uuid
from urllib.parse import urlencode

import jwt
import pandas as pd
import pyupbit
import requests

from cryptobot.bot.config import config
from cryptobot.exceptions import APIError, ConfigError, InsufficientBalanceError
from cryptobot.exchange.base import Exchange, OrderResult

logger = logging.getLogger(__name__)


class UpbitExchange(Exchange):
    """업비트 거래소 어댑터.

    NestJS의 외부 API 호출 Service에 해당. pyupbit를 통해 실제 주문을 실행한다.
    """

    FEE_RATE = 0.0005  # 업비트 현물 수수료 0.05%

    def __init__(self) -> None:
        if not config.upbit.is_configured:
            logger.warning("업비트 API Key 미설정 — 주문 실행 불가 (조회만 가능)")
            self._upbit: pyupbit.Upbit | None = None
        else:
            self._upbit = pyupbit.Upbit(config.upbit.access_key, config.upbit.secret_key)

    # ---- 메타 ----

    @property
    def market_id(self) -> str:
        return "upbit"

    @property
    def is_ready(self) -> bool:
        return self._upbit is not None

    def is_market_open(self) -> bool:
        return True  # 코인은 24/7

    # ---- 잔고/시세 ----

    def get_balance(self, asset: str) -> float:
        """자산별 잔고.

        Args:
            asset: "KRW" 또는 코인 티커("BTC") 또는 종목 코드("KRW-BTC")
        """
        self._ensure_ready()
        try:
            ticker = asset.replace("KRW-", "") if asset.startswith("KRW-") else asset
            balance = self._upbit.get_balance(ticker)
            return float(balance) if balance else 0.0
        except Exception as e:
            raise APIError(f"잔고 조회 실패: {e}") from e

    def get_balance_krw(self) -> float:
        """원화 잔고 (호환성 유지)."""
        return self.get_balance("KRW")

    def get_balance_coin(self, coin: str) -> float:
        """코인 보유량 (호환성 유지).

        Args:
            coin: 종목 코드 (예: "KRW-BTC")
        """
        return self.get_balance(coin)

    def get_current_price(self, symbol: str) -> float:
        try:
            price = pyupbit.get_current_price(symbol)
            if price is None:
                raise APIError(f"현재가 조회 실패: {symbol}")
            return float(price)
        except Exception as e:
            raise APIError(f"현재가 조회 실패: {e}") from e

    def get_ohlcv(
        self,
        symbol: str,
        interval: str = "day",
        count: int = 200,
    ) -> pd.DataFrame:
        """OHLCV 조회.

        interval은 pyupbit 형식: "day" / "minute60" / "minute5" 등
        """
        try:
            df = pyupbit.get_ohlcv(symbol, interval=interval, count=count)
            if df is None or df.empty:
                raise APIError(f"OHLCV 조회 실패: {symbol}")
            return df
        except Exception as e:
            raise APIError(f"OHLCV 조회 실패: {e}") from e

    # ---- 주문 ----

    def buy_market(self, symbol: str, amount: float) -> OrderResult:
        """시장가 매수.

        Args:
            symbol: 종목 코드 (예: "KRW-BTC")
            amount: 매수 금액 (원, KRW)
        """
        self._ensure_ready()

        if amount < 5000:
            return OrderResult(
                success=False,
                side="buy",
                coin=symbol,
                price=0,
                amount=0,
                total_krw=0,
                fee_krw=0,
                error="최소 주문 금액 5,000원 미달",
            )

        try:
            balance = self.get_balance("KRW")
            if balance < amount:
                raise InsufficientBalanceError(f"잔고 부족: {balance:,.0f}원 < {amount:,.0f}원")

            result = self._upbit.buy_market_order(symbol, amount)
            logger.info("매수 주문 실행: %s %s원", symbol, f"{amount:,.0f}")

            order_uuid = result.get("uuid") if isinstance(result, dict) else None

            est_price = self.get_current_price(symbol)
            est_fee = amount * self.FEE_RATE
            est_amount = (amount - est_fee) / est_price

            price, qty, total_krw, fee = est_price, est_amount, amount, est_fee
            if order_uuid:
                trade_detail = self._fetch_order_detail(order_uuid)
                if trade_detail:
                    price = trade_detail["price"]
                    qty = trade_detail["volume"]
                    total_krw = trade_detail["funds"]
                    fee = trade_detail["fee"]
                    logger.info(
                        "매수 실체결가: %s %.0f원 × %.8f개 (수수료 %.0f원)",
                        symbol,
                        price,
                        qty,
                        fee,
                    )

            return OrderResult(
                success=True,
                side="buy",
                coin=symbol,
                price=price,
                amount=qty,
                total_krw=total_krw,
                fee_krw=fee,
                raw_response=result,
                order_uuid=order_uuid,
            )
        except (InsufficientBalanceError, APIError):
            raise
        except Exception as e:
            raise APIError(f"매수 주문 실패: {e}") from e

    def sell_market(self, symbol: str, amount: float | None = None) -> OrderResult:
        """시장가 매도.

        Args:
            symbol: 종목 코드 (예: "KRW-BTC")
            amount: 매도 수량. None이면 전량 매도
        """
        self._ensure_ready()

        try:
            if amount is None:
                amount = self.get_balance(symbol)

            if amount <= 0:
                return OrderResult(
                    success=False,
                    side="sell",
                    coin=symbol,
                    price=0,
                    amount=0,
                    total_krw=0,
                    fee_krw=0,
                    error="매도 가능한 수량 없음",
                )

            result = self._upbit.sell_market_order(symbol, amount)
            logger.info("매도 주문 실행: %s %.8f개", symbol, amount)

            order_uuid = result.get("uuid") if isinstance(result, dict) else None

            est_price = self.get_current_price(symbol)
            est_total = est_price * amount
            est_fee = est_total * self.FEE_RATE

            price, sell_amount, total_krw, fee = est_price, amount, est_total, est_fee
            if order_uuid:
                trade_detail = self._fetch_order_detail(order_uuid)
                if trade_detail:
                    price = trade_detail["price"]
                    sell_amount = trade_detail["volume"]
                    total_krw = trade_detail["funds"]
                    fee = trade_detail["fee"]
                    logger.info(
                        "매도 실체결가: %s %.0f원 × %.8f개 (수수료 %.0f원)",
                        symbol,
                        price,
                        sell_amount,
                        fee,
                    )

            return OrderResult(
                success=True,
                side="sell",
                coin=symbol,
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

    def cancel_all_orders(self, symbol: str) -> int:
        self._ensure_ready()
        try:
            orders = self._upbit.get_order(symbol, state="wait")
            if not orders:
                return 0
            for order in orders:
                self._upbit.cancel_order(order["uuid"])
            logger.info("미체결 주문 %d건 취소: %s", len(orders), symbol)
            return len(orders)
        except Exception as e:
            raise APIError(f"주문 취소 실패: {e}") from e

    def get_order_detail(self, order_id: str) -> dict | None:
        self._ensure_ready()
        return self._fetch_order_detail(order_id)

    # ---- 거래소 특수 ----

    def tick_size(self, symbol: str, price: float) -> float:
        """업비트는 호가단위를 자동 처리하므로 가격 그대로 반환."""
        return price

    def get_deposit_history(self, currency: str = "KRW", limit: int = 100) -> list[dict]:
        """업비트 입금 내역 (state=ACCEPTED만).

        pyupbit가 입금 API를 노출하지 않아 직접 JWT 서명 후 호출.
        """
        self._ensure_ready()

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
                        "deposited_at": d.get("done_at") or d.get("created_at"),
                    }
                )
            except (TypeError, ValueError):
                continue
        return results

    # ---- 내부 ----

    def _fetch_order_detail(self, order_uuid: str, max_retries: int = 3) -> dict | None:
        """주문 UUID로 실체결 상세 조회."""
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

    def _ensure_ready(self) -> None:
        if not self.is_ready:
            raise ConfigError("업비트 API Key가 설정되지 않았습니다. .env 파일을 확인하세요.")
