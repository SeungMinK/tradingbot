"""ORB 제거 + VWAP + Volume Spike 백테스트 (#358 후속).

옵션 A 검증: ORB 빼고 VWAP 강세 + 거래량 spike 2.0x로 진입 트리거.

비교:
  ① 현재 (ORB + VWAP + 1.5x)
  ② No-ORB + VWAP + 2.0x (사용자 요청 옵션 A)
  ③ No-ORB + VWAP + 1.5x (대조)
  ④ No-ORB + VWAP + 2.5x (대조)
  ⑤ No-ORB + VWAP + 3.0x (대조 — 더 엄격)

진입 트리거 (옵션 A):
  - 첫 5봉(00:00~01:15) 이후 평가 시작 (VWAP 안정화 위해 최소 봉수 필요)
  - 가격 > VWAP
  - 거래량 spike >= 임계값
  - rolling 12봉 spike (직전 N봉 평균 대비) — 자정 누적 평균보다 일관됨

청산:
  - 손절 -3% (절대 %, ORB 없으니 OR_low 못 씀)
  - 트레일링 -3% from peak (실질 수익 시)
  - EOD 06:00 (KST)
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
EOD_HOUR = 6  # KST 06:00 (#358)
WHITELIST = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL",
    "KRW-ADA", "KRW-DOGE", "KRW-AVAX", "KRW-LINK",
]


@dataclass
class Config:
    name: str
    use_orb: bool
    spike_threshold: float
    spike_window: int  # rolling N봉 평균 (0이면 cumulative)
    bar_minutes: int = 15
    orb_minutes: int = 60
    open_hour: int = 0  # 세션 시작 KST
    stop_loss_pct: float = -3.0
    trailing_pct: float = -3.0


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


def split_sessions(df: pd.DataFrame, open_hour: int, eod_hours: int) -> list[pd.DataFrame]:
    sessions = []
    days = sorted({d.date() for d in df.index})
    for d in days:
        start = pd.Timestamp(d) + pd.Timedelta(hours=open_hour)
        end = start + pd.Timedelta(hours=eod_hours)
        sub = df[(df.index >= start) & (df.index < end)]
        if len(sub) >= 5:
            sessions.append(sub)
    return sessions


def spike_ratio(sub: pd.DataFrame, idx: int, window: int) -> float:
    last_vol = float(sub["volume"].iloc[idx])
    if window <= 0:
        avg = float(sub["volume"].iloc[: idx + 1].mean())
    else:
        start = max(0, idx - window)
        if idx - start < 3:
            return 0.0
        avg = float(sub["volume"].iloc[start:idx].mean())
    return last_vol / avg if avg > 0 else 0.0


def run_session(session: pd.DataFrame, cfg: Config) -> dict | None:
    if cfg.use_orb:
        bars_needed = max(1, cfg.orb_minutes // cfg.bar_minutes)
        if len(session) < bars_needed + 1:
            return None
        orb = calc_orb(session, orb_minutes=cfg.orb_minutes, bar_minutes=cfg.bar_minutes)
        if orb is None:
            return None
        or_high, or_low = orb
        start_idx = bars_needed
    else:
        # ORB 없음 — VWAP 안정화 위해 최소 5봉 후부터 평가
        or_high, or_low = None, None
        start_idx = 5

    entry_idx = None
    for i in range(start_idx, len(session)):
        sub = session.iloc[: i + 1]
        last_close = float(sub["close"].iloc[-1])
        # ORB 조건
        if cfg.use_orb and last_close <= or_high:
            continue
        # VWAP 조건
        v = calc_vwap(sub)
        if v is None or last_close <= v:
            continue
        # Volume spike 조건
        sp = spike_ratio(sub, len(sub) - 1, cfg.spike_window)
        if sp < cfg.spike_threshold:
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
        # OR_low 손절 (ORB 모드)
        if cfg.use_orb and or_low is not None and l <= or_low:
            exit_price = or_low
            exit_reason = "stop_or_low"
            break
        # 절대 % 손절
        if pnl <= cfg.stop_loss_pct:
            exit_price = c
            exit_reason = "stop_pct"
            break
        # 트레일링
        if h > highest:
            highest = h
        drop = (c - highest) / highest * 100
        if drop <= cfg.trailing_pct and c > entry_price:
            exit_price = c
            exit_reason = "trailing"
            break

    if exit_price is None:
        exit_price = float(session["close"].iloc[-1])
        exit_reason = "eod"

    gross = (exit_price - entry_price) / entry_price
    net = gross - 2 * FEE_RATE
    return {
        "entry_price": entry_price,
        "exit_price": exit_price,
        "gross_pct": gross * 100,
        "net_pct": net * 100,
        "exit_reason": exit_reason,
    }


def run_config(cfg: Config) -> dict:
    trades = []
    eod_hours = (24 + EOD_HOUR - cfg.open_hour) % 24 or 24
    if cfg.use_orb:
        eod_hours = 24 + EOD_HOUR - cfg.open_hour  # 자정 시작 → 다음날 EOD
    for coin in WHITELIST:
        df = load_15m(coin)
        if df.empty:
            continue
        sessions = split_sessions(df, cfg.open_hour, eod_hours)
        for sess in sessions:
            r = run_session(sess, cfg)
            if r is None:
                continue
            r["coin"] = coin
            trades.append(r)

    if not trades:
        return {"cfg": cfg, "trades": 0}
    pnl = [t["net_pct"] for t in trades]
    wins = [p for p in pnl if p > 0]
    compound = 1.0
    for p in pnl:
        compound *= (1 + p / 100)
    reasons = {}
    for t in trades:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
    return {
        "cfg": cfg,
        "trades": len(trades),
        "win_rate": len(wins) / len(pnl) * 100,
        "avg_pnl": sum(pnl) / len(pnl),
        "best": max(pnl),
        "worst": min(pnl),
        "compound_pct": (compound - 1) * 100,
        "reasons": reasons,
    }


def main() -> None:
    configs = [
        Config(
            name="① 현재 (ORB + VWAP + 1.5x cum)",
            use_orb=True, spike_threshold=1.5, spike_window=0,
        ),
        Config(
            name="② No-ORB + VWAP + 2.0x rolling12 (사용자 옵션 A)",
            use_orb=False, spike_threshold=2.0, spike_window=12,
        ),
        Config(
            name="③ No-ORB + VWAP + 1.5x rolling12",
            use_orb=False, spike_threshold=1.5, spike_window=12,
        ),
        Config(
            name="④ No-ORB + VWAP + 2.5x rolling12",
            use_orb=False, spike_threshold=2.5, spike_window=12,
        ),
        Config(
            name="⑤ No-ORB + VWAP + 3.0x rolling12",
            use_orb=False, spike_threshold=3.0, spike_window=12,
        ),
        Config(
            name="⑥ No-ORB + VWAP + 2.0x cumulative (현재 측정 방식)",
            use_orb=False, spike_threshold=2.0, spike_window=0,
        ),
    ]

    results = [run_config(c) for c in configs]

    print(f"\n{'config':<58} | {'tr':>3} | {'win%':>5} | {'avg%':>7} | {'best':>7} | {'worst':>7} | {'comp%':>8}")
    print("-" * 120)
    for r in results:
        if r["trades"] == 0:
            print(f"{r['cfg'].name:<58} | 0 trades")
            continue
        print(
            f"{r['cfg'].name:<58} | "
            f"{r['trades']:>3} | "
            f"{r['win_rate']:>4.1f}% | "
            f"{r['avg_pnl']:>+6.2f}% | "
            f"{r['best']:>+6.2f}% | "
            f"{r['worst']:>+6.2f}% | "
            f"{r['compound_pct']:>+7.2f}%"
        )

    print("\n=== exit reason 분포 ===")
    for r in results:
        if r["trades"] == 0:
            continue
        print(f"  [{r['cfg'].name[:35]:<35}] {r['reasons']}")


if __name__ == "__main__":
    main()
