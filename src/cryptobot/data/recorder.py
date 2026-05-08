"""매매 데이터 기록 모듈.

매매 신호, 체결 내역, 일일 리포트를 DB에 저장한다.
"""

import logging
from datetime import date

from cryptobot.data.database import Database

logger = logging.getLogger(__name__)


class DataRecorder:
    """매매 데이터 기록기."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def record_signal(
        self,
        coin: str,
        signal_type: str,
        strategy: str,
        confidence: float,
        trigger_reason: str,
        current_price: float,
        trigger_value: float | None = None,
        target_price: float | None = None,
        executed: bool = False,
        trade_id: int | None = None,
        skip_reason: str | None = None,
        snapshot_id: int | None = None,
        strategy_params_json: str | None = None,
        market: str = "upbit",
    ) -> int:
        """매매 신호를 기록한다 (실행 여부 관계없이).

        Args:
            market: 시장 식별자 ("upbit" / "kis_kr" / "kis_us"). 기본값 "upbit"는 코인 봇 호환성 유지.

        Returns:
            생성된 signal의 id
        """
        # snapshot_id가 0이면 None으로 (FK 제약 방지)
        if snapshot_id is not None and snapshot_id <= 0:
            snapshot_id = None

        cursor = self._db.execute(
            """
            INSERT INTO trade_signals (
                coin, market, signal_type, strategy, confidence, trigger_reason,
                trigger_value, current_price, target_price,
                executed, trade_id, skip_reason, snapshot_id, strategy_params_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                coin,
                market,
                signal_type,
                strategy,
                confidence,
                trigger_reason,
                trigger_value,
                current_price,
                target_price,
                executed,
                trade_id,
                skip_reason,
                snapshot_id,
                strategy_params_json,
            ),
        )
        # commit은 틱 단위로 배치 처리 (main.py에서 호출)
        return cursor.lastrowid

    def record_trade(
        self,
        coin: str,
        side: str,
        price: float,
        amount: float,
        total_krw: float,
        fee_krw: float,
        strategy: str,
        trigger_reason: str,
        trigger_value: float | None = None,
        param_k_value: float | None = None,
        param_stop_loss: float | None = None,
        param_trailing_stop: float | None = None,
        market_state_at_trade: str | None = None,
        btc_price_at_trade: float | None = None,
        rsi_at_trade: float | None = None,
        buy_trade_id: int | None = None,
        profit_pct: float | None = None,
        profit_krw: float | None = None,
        hold_duration_minutes: int | None = None,
        order_uuid: str | None = None,
        market: str = "upbit",
    ) -> int:
        """매매 체결을 기록한다.

        Returns:
            생성된 trade의 id
        """
        # profit_pct만 들어오고 profit_krw 누락된 경우 자동 계산 (#173)
        # 성과 리포트의 SUM(profit_krw) undercount 방지
        if profit_pct is not None and profit_krw is None and total_krw > 0:
            profit_krw = round(total_krw * profit_pct / 100, 2)

        # buy_trade_id가 주어졌으면 실존 매수 레코드인지 검증 (#173)
        # orphan sell (buy_trade_id가 가리키는 매수가 없는 상태) 방지
        if buy_trade_id is not None:
            exists = self._db.execute(
                "SELECT 1 FROM trades WHERE id = ? AND side = 'buy'",
                (buy_trade_id,),
            ).fetchone()
            if not exists:
                raise ValueError(
                    f"record_trade: buy_trade_id={buy_trade_id}에 해당하는 매수 레코드가 없음 (orphan 방지)"
                )

        cursor = self._db.execute(
            """
            INSERT INTO trades (
                coin, market, side, price, amount, total_krw, fee_krw,
                strategy, trigger_reason, trigger_value,
                param_k_value, param_stop_loss, param_trailing_stop,
                market_state_at_trade, btc_price_at_trade, rsi_at_trade,
                buy_trade_id, profit_pct, profit_krw, hold_duration_minutes,
                order_uuid
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                coin,
                market,
                side,
                price,
                amount,
                total_krw,
                fee_krw,
                strategy,
                trigger_reason,
                trigger_value,
                param_k_value,
                param_stop_loss,
                param_trailing_stop,
                market_state_at_trade,
                btc_price_at_trade,
                rsi_at_trade,
                buy_trade_id,
                profit_pct,
                profit_krw,
                hold_duration_minutes,
                order_uuid,
            ),
        )
        self._db.commit()
        logger.info("매매 기록: %s %s %.8f개 @ %s원", side, coin, amount, f"{price:,.0f}")
        return cursor.lastrowid

    def get_active_buy_trade(self, coin: str) -> dict | None:
        """아직 매도되지 않은 매수 건 조회."""
        row = self._db.execute(
            """
            SELECT * FROM trades
            WHERE coin = ? AND side = 'buy'
              AND id NOT IN (SELECT buy_trade_id FROM trades WHERE buy_trade_id IS NOT NULL)
            ORDER BY id DESC LIMIT 1
            """,
            (coin,),
        ).fetchone()
        return dict(row) if row else None

    def get_today_trades(self, coin: str | None = None) -> list[dict]:
        """오늘 매매 내역 조회. coin=None이면 전체 코인."""
        if coin:
            rows = self._db.execute(
                "SELECT * FROM trades WHERE coin = ? AND DATE(timestamp) = DATE('now') ORDER BY id",
                (coin,),
            ).fetchall()
        else:
            rows = self._db.execute("SELECT * FROM trades WHERE DATE(timestamp) = DATE('now') ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def save_daily_report(
        self,
        report_date: date,
        starting_balance: float,
        ending_balance: float,
        total_asset_value: float,
        realized_pnl: float,
        unrealized_pnl: float,
        trades_summary: dict,
        active_param_id: int | None = None,
        market_state: str | None = None,
        market: str = "upbit",
    ) -> int:
        """일일 정산 리포트를 저장한다.

        Returns:
            생성된 report의 id
        """
        daily_return = (ending_balance - starting_balance) / starting_balance * 100 if starting_balance > 0 else 0

        cursor = self._db.execute(
            """
            INSERT OR REPLACE INTO daily_reports (
                date, market, starting_balance_krw, ending_balance_krw, total_asset_value_krw,
                realized_pnl_krw, unrealized_pnl_krw, daily_return_pct,
                total_trades, buy_trades, sell_trades,
                winning_trades, losing_trades, win_rate,
                avg_profit_pct, avg_loss_pct, max_drawdown_pct, total_fees_krw,
                active_param_id, market_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_date.isoformat(),
                market,
                starting_balance,
                ending_balance,
                total_asset_value,
                realized_pnl,
                unrealized_pnl,
                round(daily_return, 2),
                trades_summary.get("total", 0),
                trades_summary.get("buys", 0),
                trades_summary.get("sells", 0),
                trades_summary.get("wins", 0),
                trades_summary.get("losses", 0),
                trades_summary.get("win_rate", 0),
                trades_summary.get("avg_profit_pct", 0),
                trades_summary.get("avg_loss_pct", 0),
                trades_summary.get("max_drawdown_pct", 0),
                trades_summary.get("total_fees", 0),
                active_param_id,
                market_state,
            ),
        )
        self._db.commit()
        logger.info("일일 리포트 저장: %s, 수익률 %.2f%%", report_date, daily_return)
        return cursor.lastrowid
