"""거래소 어댑터 패키지.

모든 거래소(Upbit, KIS 한국주식, KIS 미국주식)는 `Exchange` 인터페이스를 구현한다.

Related: #244
"""

from cryptobot.exchange.base import Exchange, OrderResult
from cryptobot.exchange.upbit import UpbitExchange

__all__ = ["Exchange", "OrderResult", "UpbitExchange"]
