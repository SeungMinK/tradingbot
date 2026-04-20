"""#216: 분봉 OHLCV 수집 테스트.

DataCollector._save_ohlcv_minutes — UNIQUE 중복 방지, 1h cooldown, 정상 저장.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from cryptobot.data.collector import DataCollector
from cryptobot.data.database import Database


@pytest.fixture
def db():
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    yield db
    db.close()


def _fake_ohlcv(n: int = 10, base_minute: int = 0):
    """가짜 분봉 OHLCV — DatetimeIndex."""
    import numpy as np
    idx = pd.date_range("2026-04-20 09:00:00", periods=n, freq="5min") + pd.Timedelta(minutes=base_minute)
    return pd.DataFrame({
        "open": np.linspace(1000, 1100, n),
        "high": np.linspace(1010, 1110, n),
        "low": np.linspace(990, 1090, n),
        "close": np.linspace(1005, 1105, n),
        "volume": np.linspace(100, 200, n),
    }, index=idx)


# ===================================================================
# 스키마
# ===================================================================


def test_ohlcv_minutes_table_exists(db):
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ohlcv_minutes'"
    ).fetchall()
    assert len(rows) == 1


def test_ohlcv_minutes_columns(db):
    cols = {dict(r)["name"] for r in db.execute("PRAGMA table_info(ohlcv_minutes)").fetchall()}
    assert {"id", "coin", "interval_min", "timestamp", "open", "high", "low",
            "close", "volume", "collected_at"} <= cols


def test_unique_constraint_prevents_duplicate(db):
    """coin + interval_min + timestamp UNIQUE."""
    db.execute(
        "INSERT INTO ohlcv_minutes (coin, interval_min, timestamp, open, high, low, close, volume, collected_at) "
        "VALUES ('KRW-BTC', 5, '2026-04-20 09:00:00', 1000, 1010, 990, 1005, 100, '2026-04-20 10:00:00')"
    )
    db.commit()
    with pytest.raises(Exception):
        db.execute(
            "INSERT INTO ohlcv_minutes (coin, interval_min, timestamp, open, high, low, close, volume, collected_at) "
            "VALUES ('KRW-BTC', 5, '2026-04-20 09:00:00', 999, 999, 999, 999, 99, '2026-04-20 10:01:00')"
        )
        db.commit()


# ===================================================================
# _save_ohlcv_minutes
# ===================================================================


def test_save_inserts_candles(db):
    """첫 호출 — N캔들 모두 INSERT."""
    collector = DataCollector(db, coin="KRW-BTC")
    with patch("pyupbit.get_ohlcv", return_value=_fake_ohlcv(n=10)):
        n = collector._save_ohlcv_minutes(interval_min=5, count=10)
    assert n == 10
    saved = db.execute("SELECT COUNT(*) FROM ohlcv_minutes WHERE coin='KRW-BTC'").fetchone()[0]
    assert saved == 10


def test_cooldown_skips_repeated_calls(db):
    """1h cooldown — 같은 호출 즉시 반복 시 0."""
    collector = DataCollector(db, coin="KRW-BTC")
    with patch("pyupbit.get_ohlcv", return_value=_fake_ohlcv(n=5)):
        n1 = collector._save_ohlcv_minutes()
        n2 = collector._save_ohlcv_minutes()
    assert n1 == 5
    assert n2 == 0  # cooldown


def test_cooldown_can_be_bypassed_for_test(db):
    """cooldown override — _last_minutes_fetch=0 리셋 시 다시 호출."""
    collector = DataCollector(db, coin="KRW-BTC")
    with patch("pyupbit.get_ohlcv", return_value=_fake_ohlcv(n=5)):
        collector._save_ohlcv_minutes()
        collector._last_minutes_fetch = 0  # cooldown 리셋
        # 같은 데이터 다시 호출 — UNIQUE로 0건 추가
        collector._save_ohlcv_minutes()
    saved = db.execute("SELECT COUNT(*) FROM ohlcv_minutes").fetchone()[0]
    assert saved == 5  # 중복 안 들어감


def test_api_failure_returns_zero(db):
    """API 예외 → 0 반환, 다음 호출 가능."""
    collector = DataCollector(db, coin="KRW-BTC")
    with patch("pyupbit.get_ohlcv", side_effect=Exception("rate limit")):
        n = collector._save_ohlcv_minutes()
    assert n == 0
    assert collector._last_minutes_fetch == 0  # 미갱신 → 다음 즉시 가능


def test_empty_response_returns_zero(db):
    """빈 응답."""
    collector = DataCollector(db, coin="KRW-BTC")
    with patch("pyupbit.get_ohlcv", return_value=pd.DataFrame()):
        n = collector._save_ohlcv_minutes()
    assert n == 0


def test_partial_overlap_inserts_only_new(db):
    """일부 겹치는 데이터 — UNIQUE로 새 것만 추가."""
    collector = DataCollector(db, coin="KRW-BTC")
    df1 = _fake_ohlcv(n=5)  # 09:00 ~ 09:20
    df2 = _fake_ohlcv(n=5, base_minute=15)  # 09:15 ~ 09:35 (3개 겹침)

    with patch("pyupbit.get_ohlcv", return_value=df1):
        collector._save_ohlcv_minutes()
    collector._last_minutes_fetch = 0
    with patch("pyupbit.get_ohlcv", return_value=df2):
        collector._save_ohlcv_minutes()

    # df1: 09:00~09:20 (5캔들), df2: 09:15~09:35 (5캔들, 09:15/09:20 overlap)
    # → 5 + 3 신규 = 8
    saved = db.execute("SELECT COUNT(*) FROM ohlcv_minutes").fetchone()[0]
    assert saved == 8


def test_different_intervals_kept_separate(db):
    """interval_min이 다르면 같은 timestamp도 별개."""
    collector = DataCollector(db, coin="KRW-BTC")
    with patch("pyupbit.get_ohlcv", return_value=_fake_ohlcv(n=3)):
        collector._save_ohlcv_minutes(interval_min=5)
    collector._last_minutes_fetch = 0
    with patch("pyupbit.get_ohlcv", return_value=_fake_ohlcv(n=3)):
        collector._save_ohlcv_minutes(interval_min=15)

    saved_5 = db.execute("SELECT COUNT(*) FROM ohlcv_minutes WHERE interval_min=5").fetchone()[0]
    saved_15 = db.execute("SELECT COUNT(*) FROM ohlcv_minutes WHERE interval_min=15").fetchone()[0]
    assert saved_5 == 3
    assert saved_15 == 3
