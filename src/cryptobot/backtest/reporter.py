"""주간 백테스트 리포터.

매주 일요일 02:00에 전 전략 × 전 코인 조합으로 백테스트를 실행하고:
1. 결과를 backtest_results 테이블에 저장
2. Slack으로 요약 리포트 전송
"""

import itertools
import json
import logging
from datetime import date

from cryptobot.backtest.engine import BacktestEngine
from cryptobot.backtest.result import BacktestResult
from cryptobot.strategies.base import StrategyParams

logger = logging.getLogger(__name__)

# 전략별 핵심 파라미터 스윕 범위 — 조합 폭발 방지를 위해 핵심 1~2개만
SWEEP_CONFIGS: dict[str, dict[str, list]] = {
    "volatility_breakout": {"k_value": [0.3, 0.5, 0.7]},
    "ma_crossover": {"short_period": [5, 10], "long_period": [20, 40]},
    "macd": {"fast": [8, 12], "slow": [21, 26]},
    "supertrend": {"st_multiplier": [2.0, 3.0, 4.0]},
    "rsi_mean_reversion": {"oversold": [25, 30, 35]},
    "bollinger_bands": {"bb_std": [1.5, 2.0, 2.5]},
    "grid_trading": {"grid_count": [5, 10, 15]},
    "breakout_momentum": {"entry_period": [10, 20, 30]},
    "bollinger_squeeze": {"bb_std": [1.5, 2.0, 2.5]},
    "bb_rsi_combined": {"rsi_oversold": [25, 30, 35], "bb_std": [1.5, 2.0]},
    # #226: 진입 임계값(공포지수, RSI) 두 축으로 sweep
    "long_term_swing": {"fear_threshold": [25, 30, 35], "rsi_entry_max": [40, 45, 50]},
    # #321/#372: ORB 시간 + 거래량 + 트레일링 최소익절 가드 sweep
    "vwap_orb_breakout": {
        "orb_minutes": [60, 90],
        "volume_spike_multiplier": [1.5, 2.0],
        "min_profit_for_trailing": [0.5, 1.5, 2.5],
    },
}


class BacktestReporter:
    """전 전략 백테스트 실행 + DB 저장 + Slack 알림."""

    def __init__(self, db, db_path: str, notifier=None) -> None:
        self._db = db
        self._db_path = db_path
        self._notifier = notifier

    def run_all(self, coins: list[str] | None = None) -> dict:
        """모든 전략 × 코인 조합으로 백테스트 실행.

        Args:
            coins: 대상 코인 목록. None이면 DB의 전 코인.

        Returns:
            코인별 BacktestResult 목록 dict
        """
        from cryptobot.bot.strategy_selector import STRATEGY_CLASSES

        if coins is None:
            coins = self._get_coins_from_db()

        if not coins:
            logger.warning("백테스트 대상 코인 없음")
            return {}

        run_date = date.today()
        all_results: dict[str, list[BacktestResult]] = {}

        for coin in coins:
            coin_results: list[BacktestResult] = []

            for name, cls in STRATEGY_CLASSES.items():
                base_params = self._get_default_params(name)
                sweep = SWEEP_CONFIGS.get(name, {})
                combos = self._generate_sweep_combos(base_params, sweep) if sweep else [base_params]

                for params_override in combos:
                    try:
                        params = StrategyParams(extra=params_override)
                        strategy = cls(params)
                        engine = BacktestEngine.from_db(self._db_path, coin, strategy)
                        result = engine.run()
                        coin_results.append(result)
                        self._save_result(run_date, result)
                        param_str = self._format_key_params(result.params, name)
                        logger.info(
                            "백테스트 완료: %s × %s%s → %d건, %.1f%%",
                            coin,
                            name,
                            param_str,
                            result.num_trades,
                            result.total_return_pct,
                        )
                    except Exception as e:
                        logger.warning("백테스트 스킵: %s × %s — %s", coin, name, e)

            if coin_results:
                coin_results.sort(key=lambda r: r.total_return_pct, reverse=True)
                all_results[coin] = coin_results

        self._db.commit()

        if all_results and self._notifier:
            self._send_slack(all_results)

        total_count = sum(len(v) for v in all_results.values())
        logger.info("주간 백테스트 완료: %d개 코인, %d개 결과", len(all_results), total_count)
        return all_results

    def _get_coins_from_db(self) -> list[str]:
        """DB에서 OHLCV 데이터가 있는 코인 목록 조회."""
        rows = self._db.execute("SELECT DISTINCT coin FROM ohlcv_daily").fetchall()
        return [dict(r)["coin"] for r in rows]

    def _get_default_params(self, strategy_name: str) -> dict:
        """DB에서 전략 기본 파라미터 조회."""
        row = self._db.execute(
            "SELECT default_params_json FROM strategies WHERE name = ?",
            (strategy_name,),
        ).fetchone()
        if row and dict(row)["default_params_json"]:
            try:
                return json.loads(dict(row)["default_params_json"])
            except (json.JSONDecodeError, TypeError):
                pass
        return {}

    @staticmethod
    def _generate_sweep_combos(base_params: dict, sweep: dict[str, list]) -> list[dict]:
        """기본 파라미터에 스윕 조합을 머지하여 반환.

        Args:
            base_params: 전략 기본 파라미터
            sweep: {파라미터명: [값 리스트]} 형태의 스윕 설정

        Returns:
            머지된 파라미터 dict 리스트
        """
        keys = list(sweep.keys())
        value_lists = [sweep[k] for k in keys]
        combos = []
        for combo in itertools.product(*value_lists):
            merged = {**base_params, **dict(zip(keys, combo))}
            combos.append(merged)
        return combos

    @staticmethod
    def _format_key_params(params: dict, strategy_name: str) -> str:
        """스윕 대상 파라미터만 간결하게 표시.

        Args:
            params: 전략 파라미터 dict
            strategy_name: 전략 이름

        Returns:
            "(k=0.7)" 형태 문자열. 스윕 설정이 없으면 빈 문자열.
        """
        sweep = SWEEP_CONFIGS.get(strategy_name, {})
        if not sweep:
            return ""
        parts = []
        # 파라미터명 약축: 긴 이름은 짧게
        short_names = {
            "k_value": "k",
            "short_period": "short",
            "long_period": "long",
            "st_multiplier": "st_m",
            "rsi_oversold": "rsi_os",
            "bb_std": "bb",
            "grid_count": "grid",
            "entry_period": "entry",
            "oversold": "os",
            "min_profit_for_trailing": "min_tp",
            "volume_spike_multiplier": "vol_sp",
        }
        for key in sweep:
            if key in params:
                short = short_names.get(key, key)
                val = params[key]
                # 정수면 정수로, 소수면 소수로
                if isinstance(val, float) and val == int(val):
                    parts.append(f"{short}={int(val)}")
                else:
                    parts.append(f"{short}={val}")
        return f"({','.join(parts)})" if parts else ""

    def _save_result(self, run_date: date, result: BacktestResult) -> None:
        """백테스트 결과를 DB에 저장."""
        self._db.execute(
            """INSERT INTO backtest_results (
                run_date, strategy_name, coin, period,
                num_trades, win_rate, total_return_pct, max_drawdown_pct,
                sharpe_ratio, avg_profit_pct, avg_loss_pct,
                best_trade_pct, worst_trade_pct, params_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_date.isoformat(),
                result.strategy_name,
                result.coin,
                result.period,
                result.num_trades,
                result.win_rate,
                result.total_return_pct,
                result.max_drawdown_pct,
                result.sharpe_ratio,
                result.avg_profit_pct,
                result.avg_loss_pct,
                result.best_trade_pct,
                result.worst_trade_pct,
                json.dumps(result.params, ensure_ascii=False),
            ),
        )

    def _send_slack(self, all_results: dict[str, list[BacktestResult]]) -> None:
        """Slack 백테스트 요약 전송."""
        lines = ["🔬 *주간 백테스트 리포트*\n"]

        # 현재 활성 전략 조회
        active_row = self._db.execute("SELECT name FROM strategies WHERE is_active = TRUE LIMIT 1").fetchone()
        active_strategy = dict(active_row)["name"] if active_row else None

        for coin, results in all_results.items():
            if not results:
                continue

            period = results[0].period
            # 기간 일수 계산
            try:
                dates = period.split(" ~ ")
                from datetime import datetime

                d1 = datetime.strptime(dates[0], "%Y-%m-%d")
                d2 = datetime.strptime(dates[1], "%Y-%m-%d")
                days = (d2 - d1).days
                period_str = f"{dates[0]} ~ {dates[1]}, {days}일"
            except Exception:
                period_str = period

            lines.append(f"📊 *{coin}* ({period_str})")

            medals = ["🥇", "🥈", "🥉"]
            for i, r in enumerate(results):
                medal = medals[i] if i < len(medals) else "  "
                param_str = self._format_key_params(r.params, r.strategy_name)
                entry = f"  {medal} {r.strategy_name}{param_str}: {r.total_return_pct:+.1f}%"
                entry += f" | {r.num_trades}건 승률{r.win_rate:.0f}%"
                entry += f" | MDD {r.max_drawdown_pct:.1f}%"
                if r.sharpe_ratio != 0:
                    entry += f" | sharpe {r.sharpe_ratio:.2f}"
                lines.append(entry)

            # 활성 전략 vs 백테스트 1위 비교
            if active_strategy:
                top = results[0].strategy_name
                if active_strategy == top:
                    lines.append(f"  💡 현재 활성: {active_strategy} → 백테스트 1위 ✅")
                else:
                    lines.append(f"  💡 현재 활성: {active_strategy} → 백테스트 1위: {top}")
            lines.append("")

        self._notifier.send("\n".join(lines))
