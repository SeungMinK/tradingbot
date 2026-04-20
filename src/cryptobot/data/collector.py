"""시장 데이터 수집기.

10초 간격으로 업비트에서 시세/거래량 데이터를 수집하고
기술적 지표를 계산하여 market_snapshots에 저장한다.
OHLCV 일봉 데이터는 ohlcv_daily 테이블에 별도 저장 (백테스팅/LLM용).
"""

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pyupbit

from cryptobot.bot.indicators import calculate_all
from cryptobot.bot.strategy import determine_market_state
from cryptobot.data.database import Database
from cryptobot.exceptions import APIError

if TYPE_CHECKING:
    import pandas as pd  # noqa: F401 — type-hint only

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    """UTC ISO 포맷 타임스탬프."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class DataCollector:
    """시장 데이터 수집 및 저장."""

    def __init__(self, db: Database, coin: str = "KRW-BTC") -> None:
        self._db = db
        self._coin = coin
        self._latest_df: "pd.DataFrame | None" = None
        self._last_ohlcv_save_date: str = ""  # 일봉 저장 중복 방지
        self._last_ohlcv_fetch: float = 0  # OHLCV 캐시 타임스탬프
        self._ohlcv_cache_seconds: int = 3600  # 1시간 캐시
        self._last_price: float = 0  # 가격 급변 감지용
        # #216: 분봉 OHLCV 수집 — 매 시간 1회만 호출 (rate-limit 보호 + 데이터 충분).
        # 5분봉 200캔들 = 약 17시간치. 다음 호출 사이 새로 생긴 캔들만 INSERT OR IGNORE.
        self._last_minutes_fetch: float = 0
        self._minutes_fetch_interval_sec: int = 3600  # 1시간마다

    @property
    def latest_df(self) -> "pd.DataFrame | None":
        """가장 최근 수집한 OHLCV DataFrame."""
        return self._latest_df

    def collect_and_save(self) -> int | None:
        """현재 시장 데이터를 수집하고 DB에 저장.

        Returns:
            저장된 snapshot의 id, 실패 시 None
        """
        try:
            snapshot = self._collect_market_data()
            if snapshot is None:
                return None

            snapshot_id = self._save_snapshot(snapshot)

            # OHLCV 일봉 데이터 저장 (하루 1회)
            self._save_ohlcv_daily()

            # #216: 5분봉 OHLCV 저장 (1시간에 1회 호출)
            self._save_ohlcv_minutes(interval_min=5)

            logger.debug("스냅샷 저장: id=%d, price=%s", snapshot_id, f"{snapshot['price']:,.0f}")
            return snapshot_id
        except Exception as e:
            logger.error("데이터 수집 실패: %s", e)
            return None

    def _collect_market_data(self) -> dict | None:
        """업비트 API로 시장 데이터 수집."""
        import time as _time

        try:
            # OHLCV 데이터 조회 — 1시간 캐시 (일봉은 자주 안 바뀜)
            now = _time.time()
            if self._latest_df is None or (now - self._last_ohlcv_fetch) > self._ohlcv_cache_seconds:
                df = pyupbit.get_ohlcv(self._coin, interval="day", count=120)
                if df is None or df.empty:
                    raise APIError(f"OHLCV 데이터 없음: {self._coin}")
                self._latest_df = df
                self._last_ohlcv_fetch = now
            else:
                df = self._latest_df

            # 현재가 (이건 매 틱마다 필요) + 가격 검증
            current_price = pyupbit.get_current_price(self._coin)
            if current_price is None or current_price <= 0:
                raise APIError(f"가격 검증 실패: {self._coin} = {current_price}")
            if self._last_price and self._last_price > 0:
                change_pct = abs(current_price - self._last_price) / self._last_price * 100
                if change_pct > 20:
                    logger.warning(
                        "가격 급변: %s %.1f%% (%s → %s)", self._coin, change_pct, self._last_price, current_price
                    )
            self._last_price = current_price

            # 기술적 지표 계산
            indicators = calculate_all(df)

            # 시장 상태 판단
            market_state = determine_market_state(indicators["ma_5"], indicators["ma_20"])

            # 24시간 변동 데이터
            today = df.iloc[-1]

            return {
                "timestamp": _utcnow(),
                "coin": self._coin,
                "price": current_price,
                "open_24h": today["open"],
                "high_24h": today["high"],
                "low_24h": today["low"],
                "change_pct_24h": round((current_price - today["open"]) / today["open"] * 100, 2),
                "volume_24h": today["volume"],
                "rsi_14": indicators["rsi_14"],
                "ma_5": indicators["ma_5"],
                "ma_20": indicators["ma_20"],
                "ma_60": indicators["ma_60"],
                "bb_upper": indicators["bb_upper"],
                "bb_lower": indicators["bb_lower"],
                "atr_14": indicators["atr_14"],
                "market_state": market_state,
            }
        except Exception as e:
            logger.error("시장 데이터 수집 실패: %s", e)
            return None

    def _save_snapshot(self, data: dict) -> int:
        """스냅샷을 DB에 저장하고 id 반환."""
        cursor = self._db.execute(
            """
            INSERT INTO market_snapshots (
                timestamp, coin, price, open_24h, high_24h, low_24h,
                change_pct_24h, volume_24h, rsi_14,
                ma_5, ma_20, ma_60,
                bb_upper, bb_lower, atr_14, market_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["timestamp"],
                data["coin"],
                data["price"],
                data["open_24h"],
                data["high_24h"],
                data["low_24h"],
                data["change_pct_24h"],
                data["volume_24h"],
                data["rsi_14"],
                data["ma_5"],
                data["ma_20"],
                data["ma_60"],
                data["bb_upper"],
                data["bb_lower"],
                data["atr_14"],
                data["market_state"],
            ),
        )
        self._db.commit()
        return cursor.lastrowid

    def _save_ohlcv_daily(self) -> None:
        """OHLCV 일봉 데이터를 ohlcv_daily 테이블에 저장.

        매 틱마다 호출되지만 날짜 기준으로 중복 방지.
        120일 캔들 전체를 upsert (과거 데이터도 보정).
        """
        if self._latest_df is None:
            return

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today_str == self._last_ohlcv_save_date:
            return  # 오늘 이미 저장함

        now = _utcnow()
        rows = []
        for idx, row in self._latest_df.iterrows():
            date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
            rows.append(
                (
                    self._coin,
                    date_str,
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    row["volume"],
                    now,
                )
            )

        self._db.executemany(
            """
            INSERT OR REPLACE INTO ohlcv_daily (coin, date, open, high, low, close, volume, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self._db.commit()
        self._last_ohlcv_save_date = today_str
        logger.info("OHLCV 일봉 저장: %s %d일치", self._coin, len(rows))

    def _save_ohlcv_minutes(self, interval_min: int = 5, count: int = 200) -> int:
        """#216: 분봉 OHLCV 수집·저장.

        업비트 API는 1m/3m/5m/15m/30m/60m/240m 지원. 5분봉 200캔들 = 약 17시간치.
        매 1시간에 1회만 호출(rate-limit). UNIQUE 제약으로 중복 방지.

        Returns:
            새로 저장된 캔들 수.
        """
        import time as _time

        now_ts = _time.time()
        if now_ts - self._last_minutes_fetch < self._minutes_fetch_interval_sec:
            return 0

        try:
            df = pyupbit.get_ohlcv(self._coin, interval=f"minute{interval_min}", count=count)
        except Exception as e:
            logger.warning("분봉 수집 실패: %s %s", self._coin, e)
            return 0
        if df is None or df.empty:
            return 0

        now_str = _utcnow()
        rows = []
        for idx, row in df.iterrows():
            ts_str = idx.strftime("%Y-%m-%d %H:%M:%S") if hasattr(idx, "strftime") else str(idx)
            rows.append((self._coin, interval_min, ts_str, row["open"], row["high"],
                        row["low"], row["close"], row["volume"], now_str))

        self._db.executemany(
            """
            INSERT OR IGNORE INTO ohlcv_minutes
              (coin, interval_min, timestamp, open, high, low, close, volume, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self._db.commit()
        self._last_minutes_fetch = now_ts
        logger.info("분봉(%dm) 저장 시도: %s %d캔들", interval_min, self._coin, len(rows))
        return len(rows)

    def get_latest_snapshot(self) -> dict | None:
        """이 코인의 가장 최근 스냅샷 조회."""
        row = self._db.execute(
            "SELECT * FROM market_snapshots WHERE coin = ? ORDER BY id DESC LIMIT 1",
            (self._coin,),
        ).fetchone()
        return dict(row) if row else None
