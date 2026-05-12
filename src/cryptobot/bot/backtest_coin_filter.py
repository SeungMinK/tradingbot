"""#378: 백테스트 검증 통과 코인만 매수 풀에 포함하는 필터.

실제 데이터 분석:
- 백테스트 데이터 없는 신생 알트 매수 → 큰 손실 (NEWT -31k원 등)
- 백테스트에서 양수 결과 본 알트 (BIO/WET/0G/TOKAMAK) → 실제도 양수
→ 백테스트 검증 통과 코인만 매수 허용으로 위험 제거.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class BacktestCoinFilter:
    """백테스트 결과로 매수 코인 풀 자동 필터.

    조건: 최신 백테스트 run에서 한 코인에 대해 어느 strategy/params 조합이든
    `avg_profit_pct >= min_avg_profit` AND `num_trades >= min_trades` 모두 만족.

    한 코인이 여러 결과를 가질 때 (sweep) 어느 하나라도 통과하면 통과로 간주.
    """

    DEFAULT_MIN_AVG_PROFIT = 5.0
    DEFAULT_MIN_TRADES = 3

    def __init__(
        self,
        db,
        min_avg_profit: float | None = None,
        min_trades: int | None = None,
    ) -> None:
        self._db = db
        self._min_avg_profit = (
            min_avg_profit if min_avg_profit is not None else self.DEFAULT_MIN_AVG_PROFIT
        )
        self._min_trades = (
            min_trades if min_trades is not None else self.DEFAULT_MIN_TRADES
        )

    def get_validated_coins(self) -> set[str]:
        """최신 백테스트 run에서 기준 통과 코인 set 반환.

        Returns:
            통과 코인 set. 백테스트 결과 없으면 빈 set.
        """
        rows = self._db.execute(
            """
            SELECT DISTINCT coin
            FROM backtest_results
            WHERE run_date = (SELECT MAX(run_date) FROM backtest_results)
              AND avg_profit_pct >= ?
              AND num_trades >= ?
            """,
            (self._min_avg_profit, self._min_trades),
        ).fetchall()
        return {r["coin"] if hasattr(r, "keys") else r[0] for r in rows}

    def filter_coins(self, coins: list[str]) -> list[str]:
        """입력 코인 리스트를 백테스트 검증 통과 코인으로 필터.

        - 백테스트 결과 없으면 원본 그대로 반환 (안전 fallback).
        - 통과 코인 집합과 교집합 계산.
        - 입력 순서 유지.

        Args:
            coins: 원본 코인 리스트

        Returns:
            필터링된 코인 리스트
        """
        validated = self.get_validated_coins()
        if not validated:
            logger.warning(
                "backtest_coin_filter: 백테스트 검증 결과 없음 — 원본 코인 그대로 사용 (%d종)",
                len(coins),
            )
            return list(coins)

        filtered = [c for c in coins if c in validated]
        logger.info(
            "backtest_coin_filter: 검증 통과 %d종 (원본 %d종, 기준 avg≥%.1f%% trades≥%d)",
            len(filtered),
            len(coins),
            self._min_avg_profit,
            self._min_trades,
        )
        return filtered
