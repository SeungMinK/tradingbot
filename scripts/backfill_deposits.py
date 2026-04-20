"""capital_deposits 백필 스크립트 (#206, #220 cutoff 7일 여유).

업비트 입금 내역을 1회 가져와 capital_deposits 테이블에 등록한다.
sync_deposits가 봇 시작일 -7일 cutoff로 직전 입금까지 자동 포함하므로 별도 시드 불필요.

사용법:
    .venv/bin/python scripts/backfill_deposits.py
"""

import logging
import sys

from cryptobot.bot.config import config
from cryptobot.bot.health_checker import HealthChecker
from cryptobot.bot.trader import Trader
from cryptobot.data.database import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    db = Database(config.bot.db_path)
    db.initialize()

    trader = Trader()
    if not trader.is_ready:
        logger.error("업비트 API Key 미설정")
        return 1

    # #220: initial 시드 로직 제거. sync_deposits가 봇 시작일 -7일 cutoff로 직전 입금까지
    # 자동으로 등록하므로 별도 시드 불필요. starting_balance를 시드로 쓰면 매매 비용/시드
    # 시점 차이로 실제 입금액과 어긋남(사용자 케이스: 100,000 → 94,998).

    # 업비트 입금 내역 sync
    checker = HealthChecker(db, trader, notifier=None)
    result = checker.sync_deposits()
    logger.info("sync 결과: %s", result)

    # 3. 현재 총 입금액
    total_row = db.execute(
        "SELECT COALESCE(SUM(amount_krw), 0) AS total FROM capital_deposits WHERE currency='KRW'"
    ).fetchone()
    logger.info("총 등록 입금액: %.0f원", dict(total_row)["total"])

    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
