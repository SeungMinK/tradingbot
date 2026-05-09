"""#228: 메이저 코인 화이트리스트 테스트.

진짜 레버: 알트(NEWT 등) 자동 제외로 큰 손실 차단.
한 달 운영 통계: NEWT 한 종목만 -31,056원 (한 달 손해의 1.5배).
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cryptobot.bot.coin_manager import CoinManager
from cryptobot.bot.config_manager import ConfigManager
from cryptobot.data.database import Database


@pytest.fixture
def db():
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    yield db
    db.close()


def _make_mgr(db, **config_overrides):
    """CoinManager + ConfigManager — 옵션은 bot_config에 직접 UPDATE."""
    for key, value in config_overrides.items():
        # 기존 행 UPDATE (display_name 등 NOT NULL 제약 충족)
        cur = db.execute("UPDATE bot_config SET value=? WHERE key=?", (value, key)).rowcount
        if cur == 0:
            db.execute(
                "INSERT INTO bot_config (key, value, value_type, category, display_name, description) "
                "VALUES (?, ?, 'string', 'test', 'test', 'test')",
                (key, value),
            )
    db.commit()
    cm = ConfigManager(db)
    return CoinManager(db, cm)


# ===================================================================
# 기본 화이트리스트
# ===================================================================


def test_default_whitelist_includes_all_8_majors():
    """티어 1+2 = 8개 코인."""
    expected = {"KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL",
                "KRW-ADA", "KRW-DOGE", "KRW-AVAX", "KRW-LINK"}
    assert set(CoinManager.DEFAULT_WHITELIST) == expected


def test_core_coins_includes_sol():
    """#228: SOL 추가 (글로벌 시총 5위)."""
    assert "KRW-SOL" in CoinManager.CORE_COINS


def test_held_coins_filters_by_market(db):
    """KIS US 보유 종목(SOXL 등)이 코인봇 active_coins에 흘러들어가면 안됨."""
    # 코인봇 보유 1건 + KIS US 보유 1건
    db.execute(
        "INSERT INTO trades (market, coin, side, price, amount, total_krw, fee_krw, strategy) "
        "VALUES ('upbit', 'KRW-BTC', 'buy', 100000000, 0.001, 100000, 50, 'test')"
    )
    db.execute(
        "INSERT INTO trades (market, coin, side, price, amount, total_krw, fee_krw, strategy) "
        "VALUES ('kis_us', 'SOXL', 'buy', 30, 1, 40000, 20, 'test')"
    )
    db.commit()
    mgr = _make_mgr(db)
    held = mgr._get_held_coins()
    assert "KRW-BTC" in held
    assert "SOXL" not in held, "KIS US 종목이 코인봇 보유 목록에 포함되면 안됨"


def test_whitelist_seeded_in_db(db):
    """initialize 시 coin_whitelist_enabled / coin_whitelist 시드."""
    enabled = db.execute("SELECT value FROM bot_config WHERE key='coin_whitelist_enabled'").fetchone()
    coins = db.execute("SELECT value FROM bot_config WHERE key='coin_whitelist'").fetchone()
    assert enabled is not None
    assert coins is not None
    assert "KRW-BTC" in dict(coins)["value"]
    assert "KRW-SOL" in dict(coins)["value"]


# ===================================================================
# 동작
# ===================================================================


def test_whitelist_mode_active_uses_only_whitelist(db):
    """화이트리스트 모드 ON → active_coins는 화이트리스트뿐."""
    mgr = _make_mgr(db)  # 기본값 ON
    mgr.refresh()
    # 보유 코인 없으면 화이트리스트 그대로
    assert set(mgr.active_coins) == set(CoinManager.DEFAULT_WHITELIST)


def test_held_coins_protected_outside_whitelist(db):
    """화이트리스트 밖 코인 보유 중이면 active_coins에 유지 (강제 청산 방지)."""
    db.execute(
        "INSERT INTO trades (timestamp, coin, side, price, amount, total_krw, fee_krw, strategy, trigger_reason) "
        "VALUES (datetime('now'), 'KRW-NEWT', 'buy', 100, 1, 100, 0.05, 'test', 'seed')"
    )
    db.commit()
    mgr = _make_mgr(db)
    mgr.refresh()
    assert "KRW-NEWT" in mgr.active_coins  # 보유 중이라 보존
    # 그러나 화이트리스트 코인은 모두 포함
    for c in CoinManager.DEFAULT_WHITELIST:
        assert c in mgr.active_coins


def test_whitelist_mode_disabled_falls_back_to_scanner(db):
    """ON=False → 기존 scanner/CORE_COINS 동작."""
    mgr = _make_mgr(db, coin_whitelist_enabled="false")
    # 화이트리스트 없으면 _get_whitelist는 None 반환
    assert mgr._get_whitelist() is None


def test_custom_whitelist_csv(db):
    """coin_whitelist 직접 변경 — 사용자 토글."""
    mgr = _make_mgr(db, coin_whitelist="KRW-BTC,KRW-ETH")
    wl = mgr._get_whitelist()
    assert wl == ["KRW-BTC", "KRW-ETH"]


def test_whitespace_trimmed_in_whitelist(db):
    """CSV 공백/탭 자동 제거."""
    mgr = _make_mgr(db, coin_whitelist="KRW-BTC , KRW-ETH ,KRW-XRP")
    wl = mgr._get_whitelist()
    assert wl == ["KRW-BTC", "KRW-ETH", "KRW-XRP"]


def test_empty_whitelist_returns_none(db):
    """빈 문자열 → None (화이트리스트 미사용 효과)."""
    mgr = _make_mgr(db, coin_whitelist="")
    assert mgr._get_whitelist() is None
