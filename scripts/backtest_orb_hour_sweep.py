"""ORB 시작 시간 sweep 백테스트 (#358 후속).

질문: ORB 윈도우를 KST 몇 시에 시작하는 게 최적인가?

테스트 후보 (시간대별 평균 거래량 / 24h 중 순위):
  - KST 22:00 (1.94x, 1위) — US 개장 글로벌 변동성 피크
  - KST 23:00 (1.58x, 2위)
  - KST 09:00 (1.58x, 3위) — KR retail 개장
  - KST 18:00 (1.37x) — Europe 마감
  - KST 00:00 (1.05x, 8위) — 현재 디폴트
  - KST 10:00 (1.24x)
  - KST 11:00, 21:00 등 추가

세션 길이: 12시간 고정 (ORB 시작 H ~ H+12h). EOD 시간(#358 06:00)과
별개로 시간 비교에만 집중.

진입 룰: ORB 돌파 + VWAP + 거래량 spike 1.5x cumulative (현재와 동일)
청산: OR_low / -3% 손절 / -3% 트레일링 / 12h 후 EOD
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from cryptobot.bot.kis_strategy import calc_orb, calc_vwap  # noqa: E402

DB_PATH = str(PROJECT_ROOT / "data" / "cryptobot.db")
FEE_RATE = 0.0005
SESSION_HOURS = 12       # 모든 후보 동일 12h 세션
ORB_MINUTES = 60
BAR_MINUTES = 15
SPIKE_THRESHOLD = 1.5
WHITELIST = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL",
    "KRW-ADA", "KRW-DOGE", "KRW-AVAX", "KRW-LINK",
]


@dataclass
class HourResult:
    hour: int
    trades: int
    win_rate: float
    avg_pnl: float
    best: float
    worst: float
    compound_pct: float
    eod_count: int
    stop_count: int
    trail_count: int


def load_15m(coin: str) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM ohlcv_minutes "
        "WHERE coin = ? ORDER BY timestamp ASC",
        conn, params=(coin,),
    )
    conn.close()
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp")
    return df.resample("15min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()


def split_sessions(df: pd.DataFrame, open_hour: int) -> list[pd.DataFrame]:
    """ORB 시작 hour부터 SESSION_HOURS 시간 후까지 한 세션."""
    sessions = []
    days = sorted({d.date() for d in df.index})
    for d in days:
        start = pd.Timestamp(d) + pd.Timedelta(hours=open_hour)
        end = start + pd.Timedelta(hours=SESSION_HOURS)
        sub = df[(df.index >= start) & (df.index < end)]
        if len(sub) >= 8:
            sessions.append(sub)
    return sessions


def run_session(session: pd.DataFrame) -> dict | None:
    bars_needed = ORB_MINUTES // BAR_MINUTES  # 4
    if len(session) < bars_needed + 1:
        return None
    orb = calc_orb(session, orb_minutes=ORB_MINUTES, bar_minutes=BAR_MINUTES)
    if orb is None:
        return None
    or_high, or_low = orb

    entry_idx = None
    for i in range(bars_needed, len(session)):
        sub = session.iloc[: i + 1]
        last_close = float(sub["close"].iloc[-1])
        if last_close <= or_high:
            continue
        v = calc_vwap(sub)
        if v is None or last_close <= v:
            continue
        last_vol = float(sub["volume"].iloc[-1])
        avg_vol = float(sub["volume"].iloc[: i + 1].mean())
        sp = last_vol / avg_vol if avg_vol > 0 else 0.0
        if sp < SPIKE_THRESHOLD:
            continue
        entry_idx = i
        break
    if entry_idx is None:
        return None

    entry_price = float(session["close"].iloc[entry_idx])
    highest = entry_price
    exit_price = None
    exit_reason = None
    for j in range(entry_idx + 1, len(session)):
        bar = session.iloc[j]
        h, l, c = float(bar["high"]), float(bar["low"]), float(bar["close"])
        pnl = (c - entry_price) / entry_price * 100
        if l <= or_low:
            exit_price = or_low
            exit_reason = "stop"
            break
        if pnl <= -3.0:
            exit_price = c
            exit_reason = "stop"
            break
        if h > highest:
            highest = h
        drop = (c - highest) / highest * 100
        if drop <= -3.0 and c > entry_price:
            exit_price = c
            exit_reason = "trailing"
            break
    if exit_price is None:
        exit_price = float(session["close"].iloc[-1])
        exit_reason = "eod"
    gross = (exit_price - entry_price) / entry_price
    net = gross - 2 * FEE_RATE
    return {"net_pct": net * 100, "exit_reason": exit_reason}


def run_hour(hour: int) -> HourResult:
    trades = []
    for coin in WHITELIST:
        df = load_15m(coin)
        if df.empty:
            continue
        for sess in split_sessions(df, hour):
            r = run_session(sess)
            if r is not None:
                trades.append(r)

    if not trades:
        return HourResult(hour, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    pnl = [t["net_pct"] for t in trades]
    wins = [p for p in pnl if p > 0]
    compound = 1.0
    for p in pnl:
        compound *= (1 + p / 100)
    return HourResult(
        hour=hour,
        trades=len(trades),
        win_rate=len(wins) / len(pnl) * 100,
        avg_pnl=sum(pnl) / len(pnl),
        best=max(pnl),
        worst=min(pnl),
        compound_pct=(compound - 1) * 100,
        eod_count=sum(1 for t in trades if t["exit_reason"] == "eod"),
        stop_count=sum(1 for t in trades if t["exit_reason"] == "stop"),
        trail_count=sum(1 for t in trades if t["exit_reason"] == "trailing"),
    )


def main() -> None:
    # 24시간 모두 sweep — 거래량 0.4x dead time 포함 데이터로 보여줌
    hours = list(range(24))
    results = [run_hour(h) for h in hours]

    # 거래량 정보 (분석 결과에서)
    vol_norm = {
        0: 1.05, 1: 0.63, 2: 0.83, 3: 0.55, 4: 0.41, 5: 0.51, 6: 0.67, 7: 1.01,
        8: 0.90, 9: 1.58, 10: 1.24, 11: 1.02, 12: 0.94, 13: 0.78, 14: 0.78,
        15: 0.73, 16: 0.99, 17: 1.13, 18: 1.37, 19: 1.00, 20: 1.11, 21: 1.03,
        22: 1.94, 23: 1.58,
    }

    print(f"\n{'KST hour':>9} | {'vol(x)':>7} | {'tr':>3} | {'win%':>5} | {'avg%':>7} | {'best':>7} | {'worst':>7} | {'comp%':>9} | {'eod':>3}/{'stop':>4}/{'trail':>5}")
    print("-" * 120)
    for r in results:
        if r.trades == 0:
            print(f"{r.hour:>4}~{(r.hour+1)%24:>2} | {vol_norm.get(r.hour, 0):>5.2f}x | 0 trades")
            continue
        marker = "  ★" if r.compound_pct > 0 else "   "
        print(
            f"{r.hour:>4}~{(r.hour+1)%24:>2} | "
            f"{vol_norm.get(r.hour, 0):>5.2f}x | "
            f"{r.trades:>3} | "
            f"{r.win_rate:>4.1f}% | "
            f"{r.avg_pnl:>+6.2f}% | "
            f"{r.best:>+6.2f}% | "
            f"{r.worst:>+6.2f}% | "
            f"{r.compound_pct:>+8.2f}%{marker} | "
            f"{r.eod_count:>3}/{r.stop_count:>4}/{r.trail_count:>5}"
        )

    print("\n=== TOP 5 (복리 기준) ===")
    top5 = sorted([r for r in results if r.trades > 0], key=lambda r: r.compound_pct, reverse=True)[:5]
    for r in top5:
        print(
            f"  KST {r.hour:>2}:00 ORB ({vol_norm.get(r.hour, 0):.2f}x 거래량) → "
            f"{r.trades}건 / 승률 {r.win_rate:.1f}% / 평균 {r.avg_pnl:+.2f}% / "
            f"복리 {r.compound_pct:+.2f}%"
        )

    print("\n=== 현재 디폴트 vs 최적 ===")
    cur = next((r for r in results if r.hour == 0), None)
    best = top5[0] if top5 else None
    if cur and best:
        delta = best.compound_pct - cur.compound_pct
        print(
            f"  현재 KST 00:00 → {cur.compound_pct:+.2f}%\n"
            f"  최적 KST {best.hour:02d}:00 → {best.compound_pct:+.2f}%\n"
            f"  차이 {delta:+.2f}%p"
        )


if __name__ == "__main__":
    main()
