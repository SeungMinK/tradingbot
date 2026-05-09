"""Pure Zarattini 룰 vs 현재 ORB 변형 백테스트 비교 (1회성 분석).

비교:
  1. 현재 (변형): ORB 윈도우 KST 00:00, 60분 ORB, 15분봉, OR_high 돌파 대기,
     트레일링 −2%, 익절 없음
  2. KST 09:00 윈도우 (변형 유지): ORB 시작만 09:00로
  3. Pure Zarattini (KST 09:00): 5분봉, 5분 ORB(=첫 봉), 첫 봉 방향 진입,
     10R TP, OR_low SL, 트레일링 X, EOD 청산

데이터: ohlcv_minutes, 화이트리스트 8개.
세션: 시작시각 ~ 23시간 후 EOD까지.
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
WHITELIST = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL",
    "KRW-ADA", "KRW-DOGE", "KRW-AVAX", "KRW-LINK",
]


@dataclass
class Config:
    name: str
    open_hour: int          # 세션 시작 KST 시
    bar_minutes: int        # 봉 단위 (5 or 15)
    orb_minutes: int        # ORB 형성 시간
    rule: str               # "breakout" or "directional"
    use_vwap: bool          # VWAP 강세 필터
    use_volume_spike: bool  # 거래량 spike 필터
    spike_threshold: float
    use_trailing: bool
    trailing_pct: float
    use_10r_tp: bool        # 10R 익절
    eod_hours: int          # 진입 후 N시간 = EOD
    # KIS US sell 패턴 옵션
    profit_lock_pct: float = 0.0   # 트레일링은 +N% 도달 후만 활성화
    take_profit_threshold: float = 0.0  # 익절선 % (이상일 때 RSI/MA 체크)


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


def resample(df: pd.DataFrame, bar_min: int) -> pd.DataFrame:
    return df.resample(f"{bar_min}min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()


def split_sessions(df: pd.DataFrame, open_hour: int, eod_hours: int) -> list[pd.DataFrame]:
    """세션 = open_hour ~ open_hour+eod_hours (다음날일 수도)."""
    sessions = []
    if df.empty:
        return sessions
    days = sorted({d.date() for d in df.index})
    for d in days:
        start = pd.Timestamp(d) + pd.Timedelta(hours=open_hour)
        end = start + pd.Timedelta(hours=eod_hours)
        sub = df[(df.index >= start) & (df.index < end)]
        if len(sub) >= 5:
            sessions.append(sub)
    return sessions


def run_breakout(session: pd.DataFrame, cfg: Config) -> dict | None:
    """현재 룰: ORB 형성 후 돌파 대기 + 옵션 필터."""
    bars_needed = max(1, cfg.orb_minutes // cfg.bar_minutes)
    if len(session) < bars_needed + 1:
        return None
    orb = calc_orb(session, orb_minutes=cfg.orb_minutes, bar_minutes=cfg.bar_minutes)
    if orb is None:
        return None
    or_high, or_low = orb

    entry_idx = None
    for i in range(bars_needed, len(session)):
        sub = session.iloc[: i + 1]
        last_close = float(sub["close"].iloc[-1])
        if last_close <= or_high:
            continue
        if cfg.use_vwap:
            v = calc_vwap(sub)
            if v is None or last_close <= v:
                continue
        if cfg.use_volume_spike:
            last_vol = float(sub["volume"].iloc[-1])
            avg_vol = float(sub["volume"].mean())
            sp = last_vol / avg_vol if avg_vol > 0 else 0.0
            if sp < cfg.spike_threshold:
                continue
        entry_idx = i
        break
    if entry_idx is None:
        return None

    return walk_exit(session, entry_idx, or_low, cfg)


def run_directional(session: pd.DataFrame, cfg: Config) -> dict | None:
    """Pure Zarattini: 첫 봉 방향 → 둘째 봉 시작 진입, 도지 제외."""
    if len(session) < 2:
        return None
    bar0 = session.iloc[0]
    o, c = float(bar0["open"]), float(bar0["close"])
    if o <= 0:
        return None
    body_pct = abs(c - o) / o
    if body_pct < 0.0005:  # 도지 (0.05% 이내) 제외
        return None
    if c <= o:
        return None  # 음봉 → 숏 (현물 X, 스킵)

    # 양봉 — 둘째 봉 시작에 진입 (open price)
    entry_idx = 1
    or_low = float(bar0["low"])
    return walk_exit(session, entry_idx, or_low, cfg, entry_at_open=True)


def walk_exit(
    session: pd.DataFrame, entry_idx: int, or_low: float, cfg: Config,
    entry_at_open: bool = False,
) -> dict:
    """진입 후 손절/익절/트레일링/EOD 평가."""
    entry_bar = session.iloc[entry_idx]
    entry_price = float(entry_bar["open"]) if entry_at_open else float(entry_bar["close"])
    risk = entry_price - or_low
    tp_price = entry_price + 10 * risk if cfg.use_10r_tp and risk > 0 else None

    highest = entry_price
    exit_price = None
    exit_reason = None

    for j in range(entry_idx + 1, len(session)):
        bar = session.iloc[j]
        h, l, c = float(bar["high"]), float(bar["low"]), float(bar["close"])
        pnl = (c - entry_price) / entry_price * 100
        # 손절
        if l <= or_low:
            exit_price = or_low
            exit_reason = "stop"
            break
        # 10R TP
        if tp_price is not None and h >= tp_price:
            exit_price = tp_price
            exit_reason = "tp_10r"
            break
        # 익절 임계 도달 — KIS US 패턴: 추세 죽음 확인 후만 매도
        if cfg.take_profit_threshold > 0 and pnl >= cfg.take_profit_threshold:
            # 추세 죽음 simple proxy: 직전 N봉 평균보다 c가 낮으면 추세 깨짐
            window = max(5, 10)
            if j >= window:
                ma = float(session["close"].iloc[j - window:j].mean())
                if c < ma:
                    exit_price = c
                    exit_reason = "tp_trend_dead"
                    break
            # 추세 살아있음 → 보유, 트레일링만
        # 트레일링 (profit_lock 도달 후만 활성화)
        if cfg.use_trailing:
            if h > highest:
                highest = h
            if pnl >= cfg.profit_lock_pct:
                drop = (c - highest) / highest * 100
                if drop <= cfg.trailing_pct and c > entry_price:
                    exit_price = c
                    exit_reason = "trailing"
                    break

    if exit_price is None:
        # EOD 청산 (세션 마지막 봉 종가)
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
    for coin in WHITELIST:
        df_1m = load_minutes(coin)
        if df_1m.empty:
            continue
        df_bars = resample(df_1m, cfg.bar_minutes)
        sessions = split_sessions(df_bars, cfg.open_hour, cfg.eod_hours)
        for sess in sessions:
            r = run_breakout(sess, cfg) if cfg.rule == "breakout" else run_directional(sess, cfg)
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
        "cfg": cfg, "trades": len(trades),
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
            name="① 현재 (KST 00:00, 15m, OR60m, 돌파+VWAP+1.5x, 트레일-2%)",
            open_hour=0, bar_minutes=15, orb_minutes=60, rule="breakout",
            use_vwap=True, use_volume_spike=True, spike_threshold=1.5,
            use_trailing=True, trailing_pct=-2.0, use_10r_tp=False, eod_hours=23,
        ),
        Config(
            name="② KR 09:00 윈도우 (변형 유지)",
            open_hour=9, bar_minutes=15, orb_minutes=60, rule="breakout",
            use_vwap=True, use_volume_spike=True, spike_threshold=1.5,
            use_trailing=True, trailing_pct=-2.0, use_10r_tp=False, eod_hours=23,
        ),
        Config(
            name="③ KR 09:00 + 트레일 끄고 10R TP (논문 정신)",
            open_hour=9, bar_minutes=15, orb_minutes=60, rule="breakout",
            use_vwap=True, use_volume_spike=True, spike_threshold=1.5,
            use_trailing=False, trailing_pct=0, use_10r_tp=True, eod_hours=23,
        ),
        Config(
            name="④ Pure Zarattini (KST 09:00, 5m, Bar1 dir, 10R TP)",
            open_hour=9, bar_minutes=5, orb_minutes=5, rule="directional",
            use_vwap=False, use_volume_spike=False, spike_threshold=0,
            use_trailing=False, trailing_pct=0, use_10r_tp=True, eod_hours=23,
        ),
        Config(
            name="⑤ Pure Zarattini (KST 22:00 미국개장)",
            open_hour=22, bar_minutes=5, orb_minutes=5, rule="directional",
            use_vwap=False, use_volume_spike=False, spike_threshold=0,
            use_trailing=False, trailing_pct=0, use_10r_tp=True, eod_hours=23,
        ),
        Config(
            name="⑥ Hybrid: 5m bar/5m ORB 돌파 + VWAP + 10R (KST 09:00)",
            open_hour=9, bar_minutes=5, orb_minutes=5, rule="breakout",
            use_vwap=True, use_volume_spike=False, spike_threshold=0,
            use_trailing=False, trailing_pct=0, use_10r_tp=True, eod_hours=23,
        ),
        Config(
            name="⑦ KIS US 패턴 (현재 설정 + profit_lock +5% 후 트레일)",
            open_hour=0, bar_minutes=15, orb_minutes=60, rule="breakout",
            use_vwap=True, use_volume_spike=True, spike_threshold=1.5,
            use_trailing=True, trailing_pct=-2.0, use_10r_tp=False, eod_hours=23,
            profit_lock_pct=5.0,
        ),
        Config(
            name="⑧ KIS US 패턴 + TP 임계 +4% 추세 체크",
            open_hour=0, bar_minutes=15, orb_minutes=60, rule="breakout",
            use_vwap=True, use_volume_spike=True, spike_threshold=1.5,
            use_trailing=True, trailing_pct=-2.0, use_10r_tp=False, eod_hours=23,
            profit_lock_pct=5.0, take_profit_threshold=4.0,
        ),
        Config(
            name="⑨ 트레일 -3% 완화 (profit_lock 없음)",
            open_hour=0, bar_minutes=15, orb_minutes=60, rule="breakout",
            use_vwap=True, use_volume_spike=True, spike_threshold=1.5,
            use_trailing=True, trailing_pct=-3.0, use_10r_tp=False, eod_hours=23,
        ),
    ]

    results = [run_config(c) for c in configs]

    print(f"\n{'config':<60} | {'tr':>3} | {'win%':>5} | {'avg%':>7} | {'best':>7} | {'worst':>7} | {'comp%':>8}")
    print("-" * 120)
    for r in results:
        if r["trades"] == 0:
            print(f"{r['cfg'].name:<60} | 0 trades")
            continue
        print(
            f"{r['cfg'].name:<60} | "
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
        print(f"  [{r['cfg'].name[:30]}] {r['reasons']}")


if __name__ == "__main__":
    main()
