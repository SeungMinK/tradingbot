"""전략 선택 + 전환 관리."""

import json
import logging

from cryptobot.strategies.base import BaseStrategy, StrategyParams
from cryptobot.strategies.bb_rsi_combined import BBRSICombined
from cryptobot.strategies.bollinger_bands import BollingerBands
from cryptobot.strategies.bollinger_squeeze import BollingerSqueeze
from cryptobot.strategies.breakout_momentum import BreakoutMomentum
from cryptobot.strategies.grid_trading import GridTrading
from cryptobot.strategies.long_term_swing import LongTermSwing
from cryptobot.strategies.ma_crossover import MACrossover
from cryptobot.strategies.macd_strategy import MACDStrategy
from cryptobot.strategies.registry import StrategyRegistry
from cryptobot.strategies.rsi_mean_reversion import RSIMeanReversion
from cryptobot.strategies.supertrend import Supertrend
from cryptobot.strategies.volatility_breakout import VolatilityBreakout
from cryptobot.strategies.vwap_orb_breakout import VwapOrbBreakout

logger = logging.getLogger(__name__)

STRATEGY_CLASSES: dict[str, type[BaseStrategy]] = {
    "volatility_breakout": VolatilityBreakout,
    "ma_crossover": MACrossover,
    "macd": MACDStrategy,
    "rsi_mean_reversion": RSIMeanReversion,
    "bollinger_bands": BollingerBands,
    "bollinger_squeeze": BollingerSqueeze,
    "supertrend": Supertrend,
    "grid_trading": GridTrading,
    "breakout_momentum": BreakoutMomentum,
    "bb_rsi_combined": BBRSICombined,
    "long_term_swing": LongTermSwing,  # #226
    "vwap_orb_breakout": VwapOrbBreakout,  # #321 Zarattini ORB (코인 단타)
}


class StrategySelector:
    """전략 레지스트리 + 코인별 전략 선택."""

    def __init__(self, db, config_manager) -> None:
        self._db = db
        self._config = config_manager
        self.registry = StrategyRegistry()
        self.current_strategy: BaseStrategy | None = None
        self.current_strategy_name: str = ""
        self._load_strategies()
        self._select_active()

    def _load_strategies(self) -> None:
        """DB에서 전략 목록을 읽고 레지스트리에 등록."""
        rows = self._db.execute("SELECT * FROM strategies WHERE is_available = TRUE").fetchall()
        for row in rows:
            name = row["name"]
            cls = STRATEGY_CLASSES.get(name)
            if cls is None:
                continue
            extra = json.loads(row["default_params_json"]) if row["default_params_json"] else {}
            # ROI 테이블 (LLM 조절 가능)
            roi_table = {10: 3.0, 30: 2.0, 60: 1.0, 120: 0.1}
            roi_json = self._config.get("roi_table", "")
            if roi_json:
                try:
                    custom_roi = json.loads(roi_json)
                    roi_table = {int(k): float(v) for k, v in custom_roi.items()}
                except (json.JSONDecodeError, ValueError):
                    pass

            params = StrategyParams(
                stop_loss_pct=float(self._config.get("stop_loss_pct", "-5.0")),
                trailing_stop_pct=float(self._config.get("trailing_stop_pct", "-3.0")),
                position_size_pct=float(self._config.get("position_size_pct", "100.0")),
                extra=extra,
                roi_table=roi_table,
            )
            try:
                self.registry.register(cls(params))
            except Exception as e:
                logger.error("전략 초기화 실패: %s — %s", name, e)

    def _select_active(self) -> None:
        """DB에서 is_active=True인 전략을 설정."""
        row = self._db.execute(
            "SELECT name FROM strategies WHERE is_active = TRUE AND status = 'active' LIMIT 1"
        ).fetchone()
        fallback = self._config.get("fallback_strategy", "bb_rsi_combined")
        strategy = self.registry.get(row["name"]) if row else self.registry.get(fallback)
        if strategy:
            self.current_strategy = strategy
            self.current_strategy_name = strategy.info().name

    def refresh(self, notifier=None) -> None:
        """전략 변경 감지 + 파라미터 실시간 반영 (전략 재생성)."""
        from cryptobot.data.strategy_repository import StrategyRepository

        repo = StrategyRepository(self._db)
        repo.complete_shutdown()

        # 전략 인스턴스 전체 재생성 (LLM 파라미터 반영)
        self._load_strategies()

        # 전략 전환 감지
        row = self._db.execute(
            "SELECT name FROM strategies WHERE is_active = TRUE AND status = 'active' LIMIT 1"
        ).fetchone()
        fallback = self._config.get("fallback_strategy", "bb_rsi_combined")
        new_name = row["name"] if row else fallback
        if new_name != self.current_strategy_name:
            new_strategy = self.registry.get(new_name)
            if new_strategy:
                old = self.current_strategy_name
                self.current_strategy = new_strategy
                self.current_strategy_name = new_name
                logger.info("전략 전환: %s → %s", old, new_name)
                if notifier:
                    notifier.notify_bot_status(f"전략 전환: {old} → {new_name}")
        else:
            # 같은 전략이어도 재생성된 인스턴스로 교체 (파라미터 반영)
            updated = self.registry.get(self.current_strategy_name)
            if updated:
                self.current_strategy = updated

    def get_coin_strategy(self, coin: str, coin_category: str, collectors: dict) -> tuple[BaseStrategy | None, str]:
        """코인의 시장 상태에 맞는 전략 반환.

        우선순위 (#152):
        1. coin_strategy_assignment — LLM이 코인별로 지정한 전략
        2. current_strategy — 기존 단일 LLM 추천 전략 (호환)
        3. registry.select_by_market — 시장 상태 기반 폴백
        4. fallback — bot_config.fallback_strategy
        """
        # 1순위: 코인별 배정 (LLM이 coin_strategies dict로 준 것)
        strategy: BaseStrategy | None = None
        assignment_params: dict | None = None
        try:
            row = self._db.execute(
                "SELECT strategy_name, params_json FROM coin_strategy_assignment WHERE coin = ?",
                (coin,),
            ).fetchone()
        except Exception:
            row = None
        if row:
            name = dict(row)["strategy_name"]
            assigned = self.registry.get(name)
            if assigned is not None:
                strategy = assigned
                pj = dict(row).get("params_json")
                if pj:
                    try:
                        assignment_params = json.loads(pj)
                    except json.JSONDecodeError:
                        pass

        # 2순위: 기존 단일 추천 전략 (코인별 배정 없을 때)
        if strategy is None:
            strategy = self.current_strategy

        # 3순위: 시장 상태 기반 폴백
        if strategy is None:
            collector = collectors.get(coin)
            snapshot = collector.get_latest_snapshot() if collector else None
            market_state = snapshot.get("market_state", "sideways") if snapshot else "sideways"
            strategy = self.registry.select_by_market(market_state)

        fallback = self._config.get("fallback_strategy", "bb_rsi_combined")
        if strategy is None:
            strategy = self.registry.get(fallback)
        if strategy is None:
            return self.current_strategy, self.current_strategy_name

        # 코인별 assignment_params 적용 — 전략 파라미터 오버라이드
        # #186: 항상 _orig_extra 세팅 (빈 dict여도). finally 복원 일관성 보장.
        if assignment_params is not None:
            strategy._orig_extra = dict(strategy.params.extra)
            if assignment_params:
                for k, v in assignment_params.items():
                    strategy.params.extra[k] = v

        # 카테고리별 리스크 파라미터 (공유 인스턴스 보호 — 매 틱 후 복원)
        row = self._db.execute("SELECT * FROM coin_strategy_config WHERE category = ?", (coin_category,)).fetchone()
        if row:
            # 원본 저장 → _tick_coin의 finally에서 복원됨
            strategy._orig_stop_loss = strategy.params.stop_loss_pct
            strategy._orig_trailing = strategy.params.trailing_stop_pct
            strategy._orig_position = strategy.params.position_size_pct
            strategy.params.stop_loss_pct = row["stop_loss_pct"]
            strategy.params.trailing_stop_pct = row["trailing_stop_pct"]
            strategy.params.position_size_pct = row["position_size_pct"]

        return strategy, strategy.info().name
