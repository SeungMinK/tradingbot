"""시장별 자본 입출금 관리 CLI (#276).

KIS 통합 계좌라 봇이 자동 분배 못 함 — 사용자가 명시.

사용법:
    # 한국주식에 +20만 입금
    .venv/bin/python scripts/market_capital.py --market kis_kr --amount 200000 --note "초기 시드"

    # 미국주식에 +30만 추가 입금 (수익 후 자체 추가)
    .venv/bin/python scripts/market_capital.py --market kis_us --amount 300000 --note "추가 입금"

    # 한국주식에서 5만 출금
    .venv/bin/python scripts/market_capital.py --market kis_kr --amount -50000 --note "회수"

    # 현재 시장별 시드/자본 상태 확인
    .venv/bin/python scripts/market_capital.py --status

봇 재시작 불필요 (DB만 변경, market_budget 모듈이 매 호출마다 lookup).
"""

import argparse
import sys

from cryptobot.bot.config import config
from cryptobot.bot.market_budget import get_available_budget, get_market_budget_status
from cryptobot.data.database import Database


VALID_MARKETS = ("kis_kr", "kis_us", "upbit")


def add_capital(db: Database, market: str, amount: float, note: str = "", source: str = "manual") -> int:
    """시장 자본 입출금 기록."""
    cur = db.execute(
        "INSERT INTO market_capital_deposits (market, amount_krw, source, note) VALUES (?, ?, ?, ?)",
        (market, amount, source, note),
    )
    db.commit()
    return cur.lastrowid


def show_status(db: Database) -> None:
    """시장별 시드/자본 상태."""
    print(f"{'시장':<10s}  {'시드':>12s}  {'실현 PnL':>12s}  {'보유 원가':>12s}  {'가용 예산':>12s}  {'자체 자본':>12s}")
    print("-" * 80)
    for m in VALID_MARKETS:
        s = get_market_budget_status(db, m)
        print(
            f"{m:<10s}  {s['seed']:>11,.0f}원  {s['realized_pnl']:>+11,.0f}원  "
            f"{s['held_cost']:>11,.0f}원  {s['available']:>11,.0f}원  "
            f"{s['current_capital']:>11,.0f}원"
        )

    print("\n=== 입출금 이력 ===")
    rows = db.execute(
        "SELECT id, market, amount_krw, deposited_at, source, note "
        "FROM market_capital_deposits ORDER BY deposited_at DESC LIMIT 20"
    ).fetchall()
    if not rows:
        print("  (이력 없음 — 환경변수 KIS_KR_BUDGET_KRW / KIS_US_BUDGET_KRW 사용 중)")
        return
    for r in rows:
        d = dict(r)
        sign = "+" if d["amount_krw"] >= 0 else ""
        note = f"  ({d['note']})" if d.get("note") else ""
        print(
            f"  {d['deposited_at']}  {d['market']:<8s}  "
            f"{sign}{d['amount_krw']:>10,.0f}원  [{d['source']}]{note}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="시장별 자본 입출금 관리")
    parser.add_argument("--market", choices=VALID_MARKETS, help="시장 (kis_kr/kis_us/upbit)")
    parser.add_argument("--amount", type=float, help="금액 (양수=입금, 음수=출금)")
    parser.add_argument("--note", default="", help="메모 (선택)")
    parser.add_argument("--source", default="manual", help="source (manual/initial/rebalance)")
    parser.add_argument("--status", action="store_true", help="현재 상태 표시")
    args = parser.parse_args()

    db = Database(config.bot.db_path)
    db.initialize()

    if args.status or (not args.market and not args.amount):
        show_status(db)
        return 0

    if not args.market or args.amount is None:
        print("ERROR: --market 과 --amount 둘 다 필요. 또는 --status로 상태만 확인.")
        return 1

    new_id = add_capital(db, args.market, args.amount, args.note, args.source)
    sign = "+" if args.amount >= 0 else ""
    print(f"✅ 등록 완료 (id={new_id}): {args.market} {sign}{args.amount:,.0f}원")

    print("\n=== 변경 후 상태 ===")
    s = get_market_budget_status(db, args.market)
    print(f"  시드: {s['seed']:,.0f}원")
    print(f"  실현 PnL: {s['realized_pnl']:+,.0f}원")
    print(f"  보유 원가: {s['held_cost']:,.0f}원")
    print(f"  가용 예산: {s['available']:,.0f}원")
    return 0


if __name__ == "__main__":
    sys.exit(main())
