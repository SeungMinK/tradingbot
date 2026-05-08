"""#255: 매수 임박 Slack 알림 테스트.

bb_rsi_combined 진입 조건(RSI≤30 AND 가격<BB하단)에 가까운 코인을
4시간 헬스체크에서 감지해 Slack 알림. 6시간 중복 방지.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cryptobot.bot.health_checker import HealthChecker
from cryptobot.data.database import Database


@pytest.fixture
def db():
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    yield db
    db.close()


def _seed_snapshot(db, coin: str, price: float, rsi: float, bb_lower: float):
    db.execute(
        "INSERT INTO market_snapshots (coin, price, rsi_14, bb_lower) VALUES (?, ?, ?, ?)",
        (coin, price, rsi, bb_lower),
    )
    db.commit()


def _seed_whitelist(db, coins: list[str]):
    db.execute("DELETE FROM bot_config WHERE key='coin_whitelist'")
    db.execute(
        "INSERT INTO bot_config (key, value, value_type, category, display_name, description) "
        "VALUES ('coin_whitelist', ?, 'string', 'coin', 'whitelist', 'test')",
        (",".join(coins),),
    )
    db.commit()


def test_no_alert_when_far_from_threshold(db):
    """RSI 65, BB하단 +20% — 임박도 낮음 → 알림 X."""
    _seed_whitelist(db, ["KRW-BTC"])
    _seed_snapshot(db, "KRW-BTC", price=120000000, rsi=65, bb_lower=100000000)
    notifier = MagicMock()
    notifier.is_configured = True
    checker = HealthChecker(db, trader=None, notifier=notifier)
    checker._check_buy_imminent_and_alert()
    notifier.send.assert_not_called()


def test_alert_when_imminent(db):
    """RSI 30.5, BB하단 +1% — 임박도 90%+ → 알림 발송."""
    _seed_whitelist(db, ["KRW-XRP"])
    _seed_snapshot(db, "KRW-XRP", price=2050, rsi=30.5, bb_lower=2030)
    notifier = MagicMock()
    notifier.is_configured = True
    checker = HealthChecker(db, trader=None, notifier=notifier)
    checker._check_buy_imminent_and_alert()
    notifier.send.assert_called_once()
    msg = notifier.send.call_args[0][0]
    assert "XRP" in msg
    assert "매수 임박" in msg


def test_dedup_within_6_hours(db):
    """같은 코인 6시간 내 두 번째 알림 X."""
    _seed_whitelist(db, ["KRW-XRP"])
    _seed_snapshot(db, "KRW-XRP", price=2050, rsi=30.5, bb_lower=2030)
    notifier = MagicMock()
    notifier.is_configured = True
    checker = HealthChecker(db, trader=None, notifier=notifier)

    checker._check_buy_imminent_and_alert()
    assert notifier.send.call_count == 1

    # 곧바로 다시 호출 — 쿨다운으로 차단
    checker._check_buy_imminent_and_alert()
    assert notifier.send.call_count == 1  # 변화 없음


def test_multiple_imminent_coins_one_message(db):
    """여러 코인 임박 시 한 메시지에 묶음."""
    _seed_whitelist(db, ["KRW-XRP", "KRW-ETH", "KRW-SOL"])
    _seed_snapshot(db, "KRW-XRP", price=2050, rsi=30, bb_lower=2030)
    _seed_snapshot(db, "KRW-ETH", price=3340000, rsi=31, bb_lower=3320000)
    _seed_snapshot(db, "KRW-SOL", price=130000, rsi=58, bb_lower=120000)  # 멀리

    notifier = MagicMock()
    notifier.is_configured = True
    checker = HealthChecker(db, trader=None, notifier=notifier)
    checker._check_buy_imminent_and_alert()
    notifier.send.assert_called_once()
    msg = notifier.send.call_args[0][0]
    assert "XRP" in msg
    assert "ETH" in msg
    # SOL은 멀어서 포함 X
    assert "SOL" not in msg


def test_no_notifier_silent(db):
    """notifier 없으면 조용히 종료."""
    _seed_whitelist(db, ["KRW-XRP"])
    _seed_snapshot(db, "KRW-XRP", price=2050, rsi=30, bb_lower=2030)
    checker = HealthChecker(db, trader=None, notifier=None)
    # 예외 없이 종료
    checker._check_buy_imminent_and_alert()


def test_missing_data_skipped(db):
    """RSI/가격/BB 중 하나 없으면 skip."""
    _seed_whitelist(db, ["KRW-XRP"])
    db.execute(
        "INSERT INTO market_snapshots (coin, price, rsi_14, bb_lower) VALUES ('KRW-XRP', 2050, NULL, 2030)"
    )
    db.commit()
    notifier = MagicMock()
    notifier.is_configured = True
    checker = HealthChecker(db, trader=None, notifier=notifier)
    checker._check_buy_imminent_and_alert()
    notifier.send.assert_not_called()
