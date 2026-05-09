"""ORB 진입 윈도우 제한 백테스트.

질문: ORB 00:00 형성 후
  A) 01:00~06:00 (5h 엄격) 만 진입 허용
  B) 01:00~24:00 (현재, 시간 제한 없음) 진입 허용
어느 쪽이 자산 불리는데 더 좋은가?

같은 ORB 같은 진입 룰. 차이는 *진입 가능 시간* 만.

청산: OR_low / -3% 손절 / -3% 트레일링 / EOD 06:00 (#358)
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
ORB_MINUTES = 60
BAR_MINUTES = 15
SPIKE_THRESHOLD = 1.5
WHITELIST = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL",
    "KRW-ADA", "KRW-DOGE", "KRW-AVAX", "KRW-LINK",
]


@dataclass
class Config:
    name: str
    entry_end_hour: int    # 진입 허용 마지막 KST 시 (이 시각 봉까지 진입 가능, 그 후 hold만)
    eod_hour: int = 6      # EOD KST 시


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


def split_24h_sessions(df: pd.DataFrame) -> list[pd.DataFrame]:
    """KST 자정~다음날 06:00 (29h) — entry까지 충분 + EOD 청산 포함."""
    sessions = []
    days = sorted({d.date() for d in df.index})
    for d in days:
        start = pd.Timestamp(d)  # 00:00 KST
        end = start + pd.Timedelta(hours=30)  # 다음날 06:00 + 약간
        sub = df[(df.index >= start) & (df.index < end)]
        if len(sub) >= 8:
            sessions.append(sub)
    return sessions


def run_session(session: pd.DataFrame, cfg: Config) -> dict | None:
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
        ts = session.index[i]
        # 진입 허용 시간 체크 (KST hour 기준)
        if ts.hour > cfg.entry_end_hour:
            # 다음날 새벽이면 (예: 다음날 03시는 엄격 5h 윈도우 밖)
            # 진입 시간 윈도우 끝났으면 더 이상 진입 X
            break
        if ts.hour == cfg.entry_end_hour and ts.minute >= 0:
            # entry_end_hour 정각도 진입 가능 (≤ entry_end_hour:00)
            pass
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
    entry_hour = session.index[entry_idx].hour
    highest = entry_price
    exit_price = None
    exit_reason = None

    for j in range(entry_idx + 1, len(session)):
        bar = session.iloc[j]
        h, l, c = float(bar["high"]), float(bar["low"]), float(bar["close"])
        ts = session.index[j]
        pnl = (c - entry_price) / entry_price * 100
        # EOD 도달 (다음날 06:00)
        if ts.hour >= cfg.eod_hour and ts.normalize() > session.index[entry_idx].normalize():
            exit_price = c
            exit_reason = "eod"
            break
        # 같은 날 06시 도달 시 (00시 ORB 후 06시면 EOD)
        if ts.hour >= cfg.eod_hour and ts.hour < cfg.entry_end_hour + 1 and \
           ts.normalize() == session.index[entry_idx].normalize():
            exit_price = c
            exit_reason = "eod"
            break
        # OR_low 손절
        if l <= or_low:
            exit_price = or_low
            exit_reason = "stop"
            break
        # 절대 % 손절
        if pnl <= -3.0:
            exit_price = c
            exit_reason = "stop"
            break
        # 트레일링
        if h > highest:
            highest = h
        drop = (c - highest) / highest * 100
        if drop <= -3.0 and c > entry_price:
            exit_price = c
            exit_reason = "trailing"
            break
    if exit_price is None:
        exit_price = float(session["close"].iloc[-1])
        exit_reason = "endofdata"
    gross = (exit_price - entry_price) / entry_price
    net = gross - 2 * FEE_RATE
    return {"net_pct": net * 100, "exit_reason": exit_reason, "entry_hour": entry_hour}


def run_config(cfg: Config) -> dict:
    trades = []
    for coin in WHITELIST:
        df = load_15m(coin)
        if df.empty:
            continue
        for sess in split_24h_sessions(df):
            r = run_session(sess, cfg)
            if r is not None:
                trades.append(r)
    if not trades:
        return {"cfg": cfg, "trades": 0}

    pnl = [t["net_pct"] for t in trades]
    wins = [p for p in pnl if p > 0]
    compound = 1.0
    for p in pnl:
        compound *= (1 + p / 100)

    # 진입 시간대별 분포
    by_hour = {}
    for t in trades:
        h = t["entry_hour"]
        by_hour.setdefault(h, []).append(t["net_pct"])

    return {
        "cfg": cfg,
        "trades": len(trades),
        "win_rate": len(wins) / len(pnl) * 100,
        "avg_pnl": sum(pnl) / len(pnl),
        "best": max(pnl),
        "worst": min(pnl),
        "compound_pct": (compound - 1) * 100,
        "by_hour": by_hour,
    }


def main() -> None:
    configs = [
        Config(name="A) 5h 엄격: ORB 00:00 + 진입 01~05시까지", entry_end_hour=5),
        Config(name="B) 8h: ORB 00:00 + 진입 01~08시까지", entry_end_hour=8),
        Config(name="C) 12h: ORB 00:00 + 진입 01~12시까지", entry_end_hour=12),
        Config(name="D) 무제한 (현재 코드 동작): 진입 01~23시", entry_end_hour=23),
    ]

    results = [run_config(c) for c in configs]

    print(f"\n{'config':<55} | {'tr':>3} | {'win%':>5} | {'avg%':>7} | {'best':>7} | {'worst':>7} | {'comp%':>9}")
    print("-" * 115)
    for r in results:
        if r["trades"] == 0:
            print(f"{r['cfg'].name:<55} | 0 trades")
            continue
        print(
            f"{r['cfg'].name:<55} | "
            f"{r['trades']:>3} | "
            f"{r['win_rate']:>4.1f}% | "
            f"{r['avg_pnl']:>+6.2f}% | "
            f"{r['best']:>+6.2f}% | "
            f"{r['worst']:>+6.2f}% | "
            f"{r['compound_pct']:>+8.2f}%"
        )

    print("\n=== 진입 시간대별 평균 P&L (D 무제한 기준) ===")
    d = next(r for r in results if "무제한" in r["cfg"].name)
    for h in sorted(d["by_hour"].keys()):
        pnls = d["by_hour"][h]
        avg = sum(pnls) / len(pnls)
        marker = "✅" if avg > 0 else "❌"
        print(f"  KST {h:>2}시 진입: {len(pnls):>2}건 / 평균 {avg:>+6.2f}% {marker}")


if __name__ == "__main__":
    main()
