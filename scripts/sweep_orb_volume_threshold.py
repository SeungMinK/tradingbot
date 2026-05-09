"""ORB volume_spike_multiplier 임계값 sweep 분석 (1회성 분석 스크립트).

용도: vwap_orb_breakout 전략의 거래량 spike 임계값(1.5/2.0/2.5/3.0)별로
"매수 신호가 발생한 (코인, 날짜)" 건수를 집계해서 임계값 변경 영향을 가늠.

데이터: ohlcv_minutes 테이블의 1분봉 → 15분봉 리샘플 → 라이브 로직과
동일하게 calc_orb/calc_vwap/spike_ratio 계산.

진입 룰 (vwap_orb_breakout):
  - ORB 형성 (자정 + 60분 = 15분봉 4개) 후
  - 가격 > OR_high
  - 가격 > VWAP (자정 누적)
  - 직전 15분봉 거래량 ≥ 자정 이후 평균 × N (N = 임계값)
"""

import sqlite3
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from cryptobot.bot.kis_strategy import calc_orb, calc_vwap  # noqa: E402

DB_PATH = str(PROJECT_ROOT / "data" / "cryptobot.db")
ORB_MINUTES = 60
BAR_MINUTES = 15
THRESHOLDS = [1.5, 2.0, 2.5, 3.0]


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
    df = df.set_index("timestamp")
    return df


def resample_15m(df_1m: pd.DataFrame) -> pd.DataFrame:
    return df_1m.resample("15min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()


def simulate_day(df_day_15m: pd.DataFrame, threshold: float) -> tuple[bool, dict]:
    """하루치 15분봉에서 매수 신호 발생 여부.

    각 봉을 순회하며 ORB 형성 이후부터 진입 조건 평가. 첫 시그널 발생 시 즉시 break.
    """
    bars_needed = ORB_MINUTES // BAR_MINUTES  # 4
    if len(df_day_15m) < bars_needed + 1:
        return False, {}

    for i in range(bars_needed, len(df_day_15m)):
        sub = df_day_15m.iloc[: i + 1]
        orb = calc_orb(sub, orb_minutes=ORB_MINUTES, bar_minutes=BAR_MINUTES)
        if orb is None:
            continue
        or_high, _ = orb
        vwap = calc_vwap(sub)
        if vwap is None:
            continue
        last_close = float(sub["close"].iloc[-1])
        last_vol = float(sub["volume"].iloc[-1])
        avg_vol = float(sub["volume"].mean())
        spike = last_vol / avg_vol if avg_vol > 0 else 0.0

        if last_close > or_high and last_close > vwap and spike >= threshold:
            return True, {
                "time": str(sub.index[-1]),
                "or_high": or_high,
                "vwap": vwap,
                "close": last_close,
                "spike": spike,
            }
    return False, {}


def split_by_kst_day(df_15m: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """KST 자정~다음 자정으로 split. timestamp는 이미 KST 가정."""
    out: dict[str, pd.DataFrame] = {}
    for date, group in df_15m.groupby(df_15m.index.normalize()):
        out[str(date.date())] = group
    return out


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    coins = [
        r[0] for r in conn.execute(
            "SELECT coin FROM ohlcv_minutes WHERE coin LIKE 'KRW-%' "
            "GROUP BY coin HAVING COUNT(*) >= 1000 ORDER BY COUNT(*) DESC"
        ).fetchall()
    ]
    conn.close()
    print(f"분석 대상 코인: {len(coins)}개 (1분봉 1000개 이상)")

    # threshold별 (코인, 날짜) 시그널 카운트
    sig_counts: dict[float, int] = {t: 0 for t in THRESHOLDS}
    day_counts: dict[float, int] = {t: 0 for t in THRESHOLDS}
    sample_signals: dict[float, list] = {t: [] for t in THRESHOLDS}
    total_days = 0

    for coin in coins:
        df_1m = load_minutes(coin)
        if df_1m.empty:
            continue
        df_15m = resample_15m(df_1m)
        days = split_by_kst_day(df_15m)
        for date, day_df in days.items():
            total_days += 1
            for t in THRESHOLDS:
                day_counts[t] += 1
                fired, info = simulate_day(day_df, t)
                if fired:
                    sig_counts[t] += 1
                    if len(sample_signals[t]) < 3:
                        sample_signals[t].append((coin, date, info))

    print(f"\n총 (코인×일) 표본: {total_days}")
    print("\n=== 임계값별 매수 시그널 발생률 ===")
    print(f"{'threshold':>10} | {'signals':>8} | {'rate':>8}")
    print("-" * 35)
    for t in THRESHOLDS:
        rate = sig_counts[t] / total_days * 100 if total_days else 0
        print(f"{t:>10}x | {sig_counts[t]:>8} | {rate:>7.2f}%")

    print("\n=== threshold별 샘플 시그널 (최대 3건) ===")
    for t in THRESHOLDS:
        print(f"\n[{t}x]")
        if not sample_signals[t]:
            print("  (시그널 없음)")
            continue
        for coin, date, info in sample_signals[t]:
            print(
                f"  {coin}  {date}  @ {info['time']}  "
                f"close={info['close']:.4g}  OR_high={info['or_high']:.4g}  "
                f"VWAP={info['vwap']:.4g}  spike={info['spike']:.2f}x"
            )


if __name__ == "__main__":
    main()
