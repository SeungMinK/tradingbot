"""활성 전략 전환 스크립트.

사용법:
    .venv/bin/python scripts/switch_strategy.py --to long_term_swing
    .venv/bin/python scripts/switch_strategy.py --to bb_rsi_combined  # 롤백
    .venv/bin/python scripts/switch_strategy.py --list  # 사용 가능 전략

봇 재시작 후 적용됨: bash scripts/stop_daemon.sh && bash scripts/start_daemon.sh
"""

import argparse
import sys

from cryptobot.bot.config import config
from cryptobot.data.database import Database


def list_strategies(db: Database) -> None:
    rows = db.execute(
        "SELECT name, display_name, is_active, status FROM strategies WHERE is_available=TRUE ORDER BY name"
    ).fetchall()
    print(f"{'이름':<22s} {'표시명':<18s} {'활성':<6s} {'상태'}")
    print("-" * 60)
    for r in rows:
        d = dict(r)
        active = "✓" if d["is_active"] else " "
        print(f"  {d['name']:<22s} {d['display_name']:<18s}  [{active}]  {d['status']}")


def switch_to(db: Database, target: str) -> int:
    exists = db.execute("SELECT 1 FROM strategies WHERE name=? AND is_available=TRUE", (target,)).fetchone()
    if not exists:
        print(f"❌ 전략 '{target}'가 존재하지 않거나 사용 불가.")
        list_strategies(db)
        return 1

    cur = db.execute("SELECT name FROM strategies WHERE is_active=TRUE").fetchone()
    cur_name = dict(cur)["name"] if cur else "(없음)"
    if cur_name == target:
        print(f"이미 {target} 활성 상태. 변경 없음.")
        return 0

    db.execute("UPDATE strategies SET is_active=FALSE")
    db.execute("UPDATE strategies SET is_active=TRUE WHERE name=?", (target,))
    db.commit()
    print(f"✅ 활성 전략 변경: {cur_name} → {target}")
    print(f"\n봇 재시작 필요: bash scripts/stop_daemon.sh && bash scripts/start_daemon.sh")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="활성 전략 전환")
    parser.add_argument("--to", help="전환할 전략 이름")
    parser.add_argument("--list", action="store_true", help="사용 가능 전략 목록")
    args = parser.parse_args()

    db = Database(config.bot.db_path)
    db.initialize()

    if args.list or not args.to:
        list_strategies(db)
        return 0

    return switch_to(db, args.to)


if __name__ == "__main__":
    sys.exit(main())
