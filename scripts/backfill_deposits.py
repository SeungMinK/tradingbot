"""capital_deposits 백필 스크립트 (#206).

업비트 입금 내역을 1회 가져와 capital_deposits 테이블에 등록한다.
초기 자본(daily_reports의 첫 starting_balance)이 별도 등록되어 있지 않으면
'initial' source로 같이 시드한다.

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

    # 1. 초기 자본 시드 (capital_deposits가 비어 있을 때만)
    existing = db.execute("SELECT COUNT(*) AS c FROM capital_deposits").fetchone()
    if dict(existing)["c"] == 0:
        # 봇 시작일 첫 daily_reports 기준으로 'initial' 등록
        first = db.execute(
            "SELECT date, starting_balance_krw FROM daily_reports ORDER BY date ASC LIMIT 1"
        ).fetchone()
        if first:
            f = dict(first)
            db.execute(
                """
                INSERT INTO capital_deposits (currency, amount_krw, deposited_at, source, note)
                VALUES ('KRW', ?, ?, 'initial', '봇 시작일 첫 잔고 기준')
                """,
                (f["starting_balance_krw"], f"{f['date']} 00:00:00"),
            )
            db.commit()
            logger.info("초기 자본 시드: %s = %.0f원", f["date"], f["starting_balance_krw"])

    # 2. 업비트 입금 내역 sync
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
