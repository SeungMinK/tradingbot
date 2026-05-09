"""화이트리스트 8개 코인의 KST 시간대별 거래량 분포 분석 (1회성).

목적: ORB 윈도우의 "진짜 개장" 시점 후보 선정.
- 시간대별 평균 거래량
- 시간대별 변동성 (high-low) / open
- 데이터 적은 코인은 자체 평균 대비 정규화

ORB 전략은 "장 시작 직후 집중 거래량 + 방향 결정"이 핵심. 거래량/변동성이
가장 높은 시간대가 ORB 윈도우 후보.
"""

import sqlite3
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

DB_PATH = str(PROJECT_ROOT / "data" / "cryptobot.db")
WHITELIST = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL",
    "KRW-ADA", "KRW-DOGE", "KRW-AVAX", "KRW-LINK",
]


def load_minutes(coin: str) -> pd.DataFrame:
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
    return df


def main() -> None:
    rows = []
    for coin in WHITELIST:
        df = load_minutes(coin)
        if df.empty:
            print(f"  {coin}: 데이터 없음")
            continue
        # 5분봉 리샘플 (논문 단위로)
        df = df.set_index("timestamp")
        df_5m = df.resample("5min").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna()
        df_5m["hour"] = df_5m.index.hour
        # 변동성 = (high - low) / open * 100
        df_5m["vol_pct"] = (df_5m["high"] - df_5m["low"]) / df_5m["open"] * 100
        # 정규화: 코인별 평균 거래량으로 나눔 → cross-coin 비교 가능
        avg_vol = df_5m["volume"].mean()
        df_5m["vol_norm"] = df_5m["volume"] / avg_vol
        for hour, group in df_5m.groupby("hour"):
            rows.append({
                "coin": coin,
                "hour": hour,
                "vol_norm_avg": group["vol_norm"].mean(),
                "vol_pct_avg": group["vol_pct"].mean(),
                "bars": len(group),
            })

    if not rows:
        print("데이터 없음")
        return

    df_summary = pd.DataFrame(rows)
    # 시간대별 평균 (코인 가로질러)
    by_hour = df_summary.groupby("hour").agg(
        vol_norm_avg=("vol_norm_avg", "mean"),
        vol_pct_avg=("vol_pct_avg", "mean"),
        coins=("coin", "nunique"),
        total_bars=("bars", "sum"),
    ).reset_index()

    # 정렬: 거래량 정규화 평균 내림차순
    by_hour_sorted = by_hour.sort_values("vol_norm_avg", ascending=False)
    print("\n=== 시간대별 평균 (8개 코인 평균, 정규화) ===")
    print(f"{'KST hour':>8} | {'vol(norm×)':>11} | {'변동성%':>8} | {'표본봉':>7}")
    print("-" * 50)
    for _, r in by_hour.sort_values("hour").iterrows():
        bar = "█" * int(r["vol_norm_avg"] * 20)
        print(
            f"{int(r['hour']):>4}~{int(r['hour'])+1:>2} | "
            f"{r['vol_norm_avg']:>10.2f}x | "
            f"{r['vol_pct_avg']:>7.3f}% | "
            f"{int(r['total_bars']):>7} {bar}"
        )

    print("\n=== 거래량 TOP 5 시간대 ===")
    for _, r in by_hour_sorted.head(5).iterrows():
        print(f"  KST {int(r['hour']):>2}:00~{int(r['hour'])+1:>2}:00 — "
              f"거래량 {r['vol_norm_avg']:.2f}x / 변동성 {r['vol_pct_avg']:.3f}%")

    print("\n=== 거래량 BOTTOM 5 시간대 ===")
    for _, r in by_hour_sorted.tail(5).iterrows():
        print(f"  KST {int(r['hour']):>2}:00~{int(r['hour'])+1:>2}:00 — "
              f"거래량 {r['vol_norm_avg']:.2f}x / 변동성 {r['vol_pct_avg']:.3f}%")

    # 현재 ORB 윈도우 평가
    print("\n=== 현재 ORB 윈도우 (KST 00:00~01:00) ===")
    cur = by_hour[by_hour["hour"].isin([0])]
    if not cur.empty:
        r = cur.iloc[0]
        rank = (by_hour_sorted["hour"].tolist().index(0) + 1)
        print(f"  거래량 {r['vol_norm_avg']:.2f}x / 변동성 {r['vol_pct_avg']:.3f}% / "
              f"24시간 중 {rank}위")


if __name__ == "__main__":
    main()
