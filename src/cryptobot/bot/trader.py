"""호환성 shim — `UpbitExchange`를 `Trader`로 re-export.

기존 코드는 `from cryptobot.bot.trader import Trader, OrderResult`를 그대로 사용 가능.
신규 코드는 `from cryptobot.exchange import UpbitExchange, OrderResult`를 권장.

Related: #244
"""

from cryptobot.exchange.base import OrderResult
from cryptobot.exchange.upbit import UpbitExchange as Trader

__all__ = ["OrderResult", "Trader"]
