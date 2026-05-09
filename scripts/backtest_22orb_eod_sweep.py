"""KST 22:00 ORB / 진입 5h 고정 + EOD 시간만 sweep.

목적: Option 1 (22:00 ORB, 5h entry)의 EOD를 어디로 잡는 게 최적인지.
각 EOD 시점의 거래량/슬리피지 + 백테스트 결과 같이 비교.

세션: 22:00 ~ 다음날 EOD (4h~16h 가변)
진입: 23:00 ~ 다음날 04:00 (5h, ORB 형성 후)
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
ORB_HOUR = 22
ORB_MINUTES = 60
BAR_MINUTES = 15
ENTRY_WINDOW_H = 5
SPIKE_THRESHOLD = 1.5
WHITELIST = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL",
    "KRW-ADA", "KRW-DOGE", "KRW-AVAX", "KRW-LINK",
]

# 시간대별 거래량 (slippage 추정용)
VOL_NORM = {
    0: 1.05, 1: 0.63, 2: 0.83, 3: 0.55, 4: 0.41, 5: 0.51, 6: 0.67, 7: 1.01,
    8: 0.90, 9: 1.58, 10: 1.24, 11: 1.02, 12: 0.94, 13: 0.78, 14: 0.78,
    15: 0.73, 16: 0.99, 17: 1.13, 18: 1.37, 19: 1.00, 20: 1.11, 21: 1.03,
    22: 1.94, 23: 1.58,
}


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


def split_sessions(df: pd.DataFrame, session_h: int) -> list[pd.DataFrame]:
    sessions = []
    days = sorted({d.date() for d in df.index})
    for d in days:
        start = pd.Timestamp(d) + pd.Timedelta(hours=ORB_HOUR)
        end = start + pd.Timedelta(hours=session_h)
        sub = df[(df.index >= start) & (df.index < end)]
        if len(sub) >= 8:
            sessions.append(sub)
    return sessions


def run_session(session: pd.DataFrame, session_h: int) -> dict | None:
    bars_needed = ORB_MINUTES // BAR_MINUTES  # 4
    if len(session) < bars_needed + 1:
        return None
    orb = calc_orb(session, orb_minutes=ORB_MINUTES, bar_minutes=BAR_MINUTES)
    if orb is None:
        return None
    or_high, or_low = orb

    entry_end_idx = bars_needed + ENTRY_WINDOW_H * (60 // BAR_MINUTES)
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


def run_eod(eod_hour: int) -> dict:
    session_h = (24 + eod_hour - ORB_HOUR) % 24 or 24
    if session_h < ENTRY_WINDOW_H + 2:  # 진입 윈도우 + 최소 1시간 보유
        return {"eod": eod_hour, "trades": 0, "session_h": session_h}

    trades = []
    for coin in WHITELIST:
        df = load_15m(coin)
        if df.empty:
            continue
        for sess in split_sessions(df, session_h):
            r = run_session(sess, session_h)
            if r is not None:
                trades.append(r)
    if not trades:
        return {"eod": eod_hour, "trades": 0, "session_h": session_h}

    pnl = [t["net_pct"] for t in trades]
    wins = [p for p in pnl if p > 0]
    compound = 1.0
    for p in pnl:
        compound *= (1 + p / 100)
    return {
        "eod": eod_hour,
        "session_h": session_h,
        "trades": len(trades),
        "win_rate": len(wins) / len(pnl) * 100,
        "avg_pnl": sum(pnl) / len(pnl),
        "best": max(pnl),
        "worst": min(pnl),
        "compound_pct": (compound - 1) * 100,
    }


def main() -> None:
    # 22:00 ORB + 5h entry → entry ends 04:00 다음날
    # 가능한 EOD: 05:00 (1h hold) ~ 21:00 (17h hold) — 24시간 사이클 안에서
    eod_candidates = list(range(5, 22))  # 05~21시
    results = [run_eod(h) for h in eod_candidates]

    # 슬리피지 = EOD 시간 거래량 (낮을수록 슬리피지 적음)
    print(f"\n{'EOD':>5} | {'세션h':>5} | {'EOD 거래량':>10} | {'tr':>3} | {'win%':>5} | "
          f"{'avg%':>7} | {'best':>7} | {'worst':>7} | {'복리':>9}")
    print("-" * 95)
    for r in results:
        if r["trades"] == 0:
            print(f"{r['eod']:>2}:00 | {r['session_h']:>4}h | (skip)")
            continue
        v = VOL_NORM.get(r["eod"], 0)
        slippage_mark = "✅" if v < 0.8 else ("△" if v < 1.2 else "⚠️")
        marker = "  ★" if r["compound_pct"] > 18 else "   "
        print(
            f"{r['eod']:>2}:00 | "
            f"{r['session_h']:>4}h | "
            f"{v:>4.2f}x {slippage_mark}  | "
            f"{r['trades']:>3} | "
            f"{r['win_rate']:>4.1f}% | "
            f"{r['avg_pnl']:>+6.2f}% | "
            f"{r['best']:>+6.2f}% | "
            f"{r['worst']:>+6.2f}% | "
            f"{r['compound_pct']:>+8.2f}%{marker}"
        )

    print("\n=== 복리 + 슬리피지 종합 평가 (낮은 거래량 EOD 선호) ===")
    valid = [r for r in results if r["trades"] >= 5]
    # 종합 점수 = 복리 - 슬리피지 페널티 (거래량×2)
    for r in valid:
        v = VOL_NORM.get(r["eod"], 1.0)
        r["score"] = r["compound_pct"] - v * 2
    valid.sort(key=lambda r: r["score"], reverse=True)
    print(f"  ※ 종합 점수 = 복리 − (EOD 거래량 × 2) — 슬리피지 페널티")
    for r in valid[:8]:
        v = VOL_NORM.get(r["eod"], 0)
        print(
            f"  EOD {r['eod']:>2}:00 — 복리 {r['compound_pct']:+.2f}% / "
            f"거래량 {v:.2f}x / 종합 점수 {r['score']:+.2f}"
        )


if __name__ == "__main__":
    main()
