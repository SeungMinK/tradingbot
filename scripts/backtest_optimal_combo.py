"""ORB 시간 × 진입 윈도우 × EOD 시간 3차원 최적 조합 sweep.

목표: 8개 화이트리스트 코인에 대해 자산 불리기 최적 조합 찾기.

탐색:
  ORB hour: 00, 09, 10, 22, 23 (이전 sweep 상위 5)
  진입 윈도우 길이 (ORB 형성 1h 포함, 그 이후 N시간): 1h, 2h, 3h, 4h, 5h
  EOD 추가 보유 시간 (진입 마감 후 +Nh): 1h, 3h, 5h, 8h
  → 5 × 5 × 4 = 100 조합

진입 룰: ORB 돌파 + VWAP + 거래량 spike 1.5x cumulative
청산: OR_low / -3% 손절 / -3% 트레일링 / EOD
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
    orb_hour: int           # ORB 시작 KST 시
    entry_window_h: int     # ORB 형성(1h) 후 N시간 동안 진입 허용
    hold_buffer_h: int      # 진입 마감 후 N시간 동안 보유 가능 (= EOD)
    @property
    def entry_end_hour(self) -> int:
        return (self.orb_hour + 1 + self.entry_window_h) % 24

    @property
    def eod_hour(self) -> int:
        return (self.entry_end_hour + self.hold_buffer_h) % 24

    @property
    def session_h(self) -> int:
        return 1 + self.entry_window_h + self.hold_buffer_h


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


def split_sessions(df: pd.DataFrame, cfg: Config) -> list[pd.DataFrame]:
    sessions = []
    days = sorted({d.date() for d in df.index})
    for d in days:
        start = pd.Timestamp(d) + pd.Timedelta(hours=cfg.orb_hour)
        end = start + pd.Timedelta(hours=cfg.session_h)
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

    # 진입 마감 봉 인덱스 = ORB(4봉) + entry_window_h*4봉
    entry_end_idx = bars_needed + cfg.entry_window_h * (60 // BAR_MINUTES)

    entry_idx = None
    for i in range(bars_needed, min(entry_end_idx + 1, len(session))):
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
            exit_price, exit_reason = or_low, "stop"
            break
        if pnl <= -3.0:
            exit_price, exit_reason = c, "stop"
            break
        if h > highest:
            highest = h
        drop = (c - highest) / highest * 100
        if drop <= -3.0 and c > entry_price:
            exit_price, exit_reason = c, "trailing"
            break
    if exit_price is None:
        exit_price = float(session["close"].iloc[-1])
        exit_reason = "eod"
    gross = (exit_price - entry_price) / entry_price
    net = gross - 2 * FEE_RATE
    return {"net_pct": net * 100, "exit_reason": exit_reason}


def run_config(cfg: Config) -> dict:
    trades = []
    for coin in WHITELIST:
        df = load_15m(coin)
        if df.empty:
            continue
        for sess in split_sessions(df, cfg):
            r = run_session(sess, cfg)
            if r is not None:
                trades.append(r)
    if not trades:
        return {"cfg": cfg, "trades": 0, "win_rate": 0, "avg_pnl": 0,
                "compound_pct": 0, "best": 0, "worst": 0}

    pnl = [t["net_pct"] for t in trades]
    wins = [p for p in pnl if p > 0]
    compound = 1.0
    for p in pnl:
        compound *= (1 + p / 100)
    return {
        "cfg": cfg,
        "trades": len(trades),
        "win_rate": len(wins) / len(pnl) * 100,
        "avg_pnl": sum(pnl) / len(pnl),
        "best": max(pnl),
        "worst": min(pnl),
        "compound_pct": (compound - 1) * 100,
    }


def main() -> None:
    orb_hours = [0, 9, 10, 22, 23]
    entry_windows = [1, 2, 3, 4, 5]
    hold_buffers = [1, 3, 5, 8]

    results = []
    for h in orb_hours:
        for w in entry_windows:
            for b in hold_buffers:
                cfg = Config(orb_hour=h, entry_window_h=w, hold_buffer_h=b)
                results.append(run_config(cfg))

    # 최소 trades 5건 이상 + 복리 내림차순
    valid = [r for r in results if r["trades"] >= 5]
    valid.sort(key=lambda r: r["compound_pct"], reverse=True)

    print(f"\n=== TOP 15 조합 (trade ≥5건, 복리 내림차순) ===")
    print(f"{'#':>2} | {'ORB':>5} | {'진입(h)':>6} | {'EOD':>5} | {'세션h':>5} | "
          f"{'tr':>3} | {'win%':>5} | {'avg%':>7} | {'best':>7} | {'worst':>7} | {'복리':>9}")
    print("-" * 105)
    for i, r in enumerate(valid[:15], 1):
        c = r["cfg"]
        print(
            f"{i:>2} | "
            f"{c.orb_hour:>2}:00 | "
            f"{c.entry_window_h:>5}h | "
            f"{c.eod_hour:>2}:00 | "
            f"{c.session_h:>5}h | "
            f"{r['trades']:>3} | "
            f"{r['win_rate']:>4.1f}% | "
            f"{r['avg_pnl']:>+6.2f}% | "
            f"{r['best']:>+6.2f}% | "
            f"{r['worst']:>+6.2f}% | "
            f"{r['compound_pct']:>+8.2f}%"
        )

    print(f"\n=== ORB 시간별 BEST 조합 ===")
    for h in orb_hours:
        same_orb = [r for r in valid if r["cfg"].orb_hour == h]
        if not same_orb:
            continue
        best = same_orb[0]
        c = best["cfg"]
        print(
            f"  KST {h:>2}:00 ORB → 진입 {c.entry_window_h}h / EOD {c.eod_hour:02d}:00 / "
            f"{best['trades']}건 / 승률 {best['win_rate']:.1f}% / 복리 {best['compound_pct']:+.2f}%"
        )


if __name__ == "__main__":
    main()
