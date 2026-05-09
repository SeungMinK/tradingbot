"""ORB volume_spike 측정 방식 + 임계값 백테스트 비교 (1회성 분석).

비교 대상:
  A) 현재: 자정 누적 평균(cumulative) × 1.5
  B) 제안: 직전 N봉 rolling 평균 × 2.0
  + 추가 그리드: rolling N=20 with 1.5/2.0/2.5

데이터: ohlcv_minutes → 15분봉 리샘플.
일별로 KST 00:00~09:00 (다음날) 사이에서 ORB(00:00~01:00) 형성 후 진입 평가.
하루 1회만 진입. 진입 후 보유는 같은 세션 동안.

청산 룰:
  1) low <= OR_low → 손절 OR_low
  2) KST 09:00 도달 → EOD 종가 청산
수수료: 편도 0.05% (왕복 0.1%)
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
ORB_MINUTES = 60
BAR_MINUTES = 15
FEE_RATE = 0.0005  # 편도
EOD_HOUR = 9  # KST 09:00


@dataclass
class Config:
    name: str
    method: str          # "cumulative" | "rolling"
    rolling_n: int       # method=="rolling"일 때만 사용
    threshold: float


def load_15m(coin: str) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM ohlcv_minutes "
        "WHERE coin = ? ORDER BY timestamp ASC",
        conn,
        params=(coin,),
    )
    conn.close()
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp")
    out = df.resample("15min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    return out


def split_sessions(df_15m: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    """KST 00:00~다음날 EOD_HOUR 사이를 한 세션으로 split.

    봉 timestamp는 봉 시작 시각으로 가정.
    """
    sessions: list[tuple[str, pd.DataFrame]] = []
    if df_15m.empty:
        return sessions
    start_dates = sorted({d.date() for d in df_15m.index.normalize()})
    for d in start_dates:
        start = pd.Timestamp(d)
        end = start + pd.Timedelta(hours=24 + EOD_HOUR)
        sub = df_15m[(df_15m.index >= start) & (df_15m.index < end)]
        if not sub.empty:
            sessions.append((str(d), sub))
    return sessions


def spike_ratio(sub: pd.DataFrame, idx: int, cfg: Config) -> float:
    last_vol = float(sub["volume"].iloc[idx])
    if cfg.method == "cumulative":
        avg_vol = float(sub["volume"].iloc[: idx + 1].mean())
    else:
        # rolling N: 직전 N봉 (현재 봉 제외) 평균
        start = max(0, idx - cfg.rolling_n)
        if idx - start < 3:
            return 0.0  # 표본 부족
        avg_vol = float(sub["volume"].iloc[start:idx].mean())
    return last_vol / avg_vol if avg_vol > 0 else 0.0


def run_session(session: pd.DataFrame, cfg: Config) -> dict | None:
    """한 세션 백테스트: 첫 시그널에 진입, OR_low 손절 또는 EOD 청산."""
    bars_needed = ORB_MINUTES // BAR_MINUTES  # 4
    # ORB는 00:00~01:00 4봉. 그 다음 봉부터 진입 검토.
    # 00:00 미만으로 시작하는 세션은 ORB 형성 X로 처리.
    or_high, or_low = None, None
    orb = calc_orb(session, orb_minutes=ORB_MINUTES, bar_minutes=BAR_MINUTES)
    if orb is None:
        return None
    or_high, or_low = orb

    # 진입 탐색
    entry_idx = None
    for i in range(bars_needed, len(session)):
        sub = session.iloc[: i + 1]
        vwap = calc_vwap(sub)
        if vwap is None:
            continue
        last_close = float(sub["close"].iloc[-1])
        sp = spike_ratio(sub, len(sub) - 1, cfg)
        if last_close > or_high and last_close > vwap and sp >= cfg.threshold:
            entry_idx = i
            break
    if entry_idx is None:
        return None

    entry_price = float(session["close"].iloc[entry_idx])

    # 청산 탐색: low <= OR_low → OR_low 손절, EOD(09:00) 도달 → 종가
    exit_price = None
    exit_reason = None
    for j in range(entry_idx + 1, len(session)):
        bar = session.iloc[j]
        ts = session.index[j]
        if float(bar["low"]) <= or_low:
            exit_price = or_low
            exit_reason = "stop"
            break
        if ts.hour >= EOD_HOUR and ts.normalize() > session.index[entry_idx].normalize():
            exit_price = float(bar["close"])
            exit_reason = "eod"
            break
    if exit_price is None:
        # 데이터 끝까지 미청산 → 마지막 종가로 처리
        exit_price = float(session["close"].iloc[-1])
        exit_reason = "endofdata"

    gross = (exit_price - entry_price) / entry_price
    net = gross - 2 * FEE_RATE
    return {
        "entry_price": entry_price,
        "exit_price": exit_price,
        "gross_pct": gross * 100,
        "net_pct": net * 100,
        "exit_reason": exit_reason,
    }


def run_config(coins: list[str], cfg: Config) -> dict:
    trades: list[dict] = []
    skipped = 0
    for coin in coins:
        df_15m = load_15m(coin)
        if df_15m.empty:
            continue
        sessions = split_sessions(df_15m)
        for date, sess in sessions:
            r = run_session(sess, cfg)
            if r is None:
                skipped += 1
                continue
            r["coin"] = coin
            r["date"] = date
            trades.append(r)

    if not trades:
        return {"cfg": cfg, "trades": 0, "skipped": skipped}

    pnl_list = [t["net_pct"] for t in trades]
    wins = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p <= 0]
    total_compound = 1.0
    for p in pnl_list:
        total_compound *= (1 + p / 100)
    return {
        "cfg": cfg,
        "trades": len(trades),
        "win_rate": len(wins) / len(pnl_list) * 100,
        "avg_pnl": sum(pnl_list) / len(pnl_list),
        "median_pnl": sorted(pnl_list)[len(pnl_list) // 2],
        "best": max(pnl_list),
        "worst": min(pnl_list),
        "compound_pct": (total_compound - 1) * 100,
        "stop_count": sum(1 for t in trades if t["exit_reason"] == "stop"),
        "eod_count": sum(1 for t in trades if t["exit_reason"] == "eod"),
        "trades_data": trades,
    }


WHITELIST = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL",
    "KRW-ADA", "KRW-DOGE", "KRW-AVAX", "KRW-LINK",
]


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="전체 코인 (기본: 화이트리스트 8개)")
    args = parser.parse_args()

    if args.all:
        conn = sqlite3.connect(DB_PATH)
        coins = [
            r[0] for r in conn.execute(
                "SELECT coin FROM ohlcv_minutes WHERE coin LIKE 'KRW-%' "
                "GROUP BY coin HAVING COUNT(*) >= 1000 ORDER BY COUNT(*) DESC"
            ).fetchall()
        ]
        conn.close()
    else:
        coins = WHITELIST
    print(f"분석 대상 코인: {len(coins)}개 ({'전체' if args.all else '화이트리스트'})\n")

    configs = [
        Config("A) 현재: cumulative × 1.5", "cumulative", 0, 1.5),
        Config("B) 제안: rolling20 × 2.0", "rolling", 20, 2.0),
        Config("C) rolling20 × 1.5", "rolling", 20, 1.5),
        Config("D) rolling20 × 2.5", "rolling", 20, 2.5),
        Config("E) rolling12 × 2.0", "rolling", 12, 2.0),
    ]

    results = []
    for cfg in configs:
        r = run_config(coins, cfg)
        results.append(r)

    print(f"{'config':<32} | {'trades':>7} | {'win%':>6} | {'avg%':>7} | {'med%':>7} | "
          f"{'best%':>7} | {'worst%':>7} | {'compound%':>10} | {'stop':>4} | {'eod':>4}")
    print("-" * 130)
    for r in results:
        if r["trades"] == 0:
            print(f"{r['cfg'].name:<32} | (no trades)")
            continue
        print(
            f"{r['cfg'].name:<32} | "
            f"{r['trades']:>7} | "
            f"{r['win_rate']:>5.1f}% | "
            f"{r['avg_pnl']:>+6.2f}% | "
            f"{r['median_pnl']:>+6.2f}% | "
            f"{r['best']:>+6.2f}% | "
            f"{r['worst']:>+6.2f}% | "
            f"{r['compound_pct']:>+9.2f}% | "
            f"{r['stop_count']:>4} | "
            f"{r['eod_count']:>4}"
        )


if __name__ == "__main__":
    main()
