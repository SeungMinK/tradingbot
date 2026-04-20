"""리스크 관리 모듈.

NestJS의 Guard/Middleware와 비슷한 역할.
매매 실행 전에 리스크 조건을 체크하고, 위험한 주문을 차단한다.
"""

import logging
from dataclasses import dataclass

from cryptobot.data.database import Database

logger = logging.getLogger(__name__)


@dataclass
class RiskLimits:
    """리스크 한도 설정."""

    max_daily_trades: int = 10  # 일일 최대 거래 횟수
    max_daily_loss_pct: float = -7.0  # 코인별 일일 최대 손실률 (%)
    max_position_size_krw: float = 300_000  # 최대 1회 매수 금액 (원)
    min_balance_krw: float = 5_000  # 최소 유지 잔고 (업비트 최소 주문금액)
    max_consecutive_losses: int = 3  # 연속 손실 시 매매 중단 (최근 1일 윈도우)
    consecutive_loss_window_hours: int = 24  # 연속 손실 판정 윈도우
    # #208: 매도 직후 같은 코인 재매수 차단. 손절(-5%)과 RSI 과매도 매수 신호가
    # 같은 가격 사건에 대해 모순적으로 발생하는 패턴(수수료 왕복 0.1% 손실)을 막는다.
    coin_reentry_cooldown_minutes: int = 10
    min_order_krw: float = 5_000  # 업비트 최소 주문 금액 (원)
    # 계좌 전체 일일 실현 손실 한도 (매수 차단용, 매도는 허용).
    # 코인별 한도(max_daily_loss_pct)와 별도로 "계좌 전체"를 보호.
    max_daily_account_loss_pct: float = -10.0


class RiskManager:
    """리스크 관리자.

    매매 전에 check_*() 메서드로 리스크를 점검한다.
    NestJS의 Guard처럼, 조건 미충족 시 매매를 차단한다.
    """

    def __init__(self, db: Database, limits: RiskLimits | None = None) -> None:
        self._db = db
        self.limits = limits or RiskLimits()

    def check_can_buy(self, coin: str, buy_amount_krw: float, current_balance_krw: float) -> tuple[bool, str]:
        """매수 가능 여부 점검.

        Args:
            coin: 종목 코드
            buy_amount_krw: 매수 금액
            current_balance_krw: 현재 잔고

        Returns:
            (가능 여부, 사유)
        """
        # 0. 업비트 최소 주문 금액 체크
        if buy_amount_krw < self.limits.min_order_krw:
            return False, f"최소 주문 금액 미달: {buy_amount_krw:,.0f}원 < {self.limits.min_order_krw:,.0f}원"

        # 1. 최소 잔고 유지 체크
        remaining = current_balance_krw - buy_amount_krw
        if remaining < self.limits.min_balance_krw:
            return False, f"최소 잔고 미달: 잔여 {remaining:,.0f}원 < {self.limits.min_balance_krw:,.0f}원"

        # 2. 최대 1회 매수 금액 체크
        if buy_amount_krw > self.limits.max_position_size_krw:
            return False, f"최대 매수 금액 초과: {buy_amount_krw:,.0f}원 > {self.limits.max_position_size_krw:,.0f}원"

        # 3. 일일 거래 횟수 체크
        today_count = self._get_today_trade_count(coin)
        if today_count >= self.limits.max_daily_trades:
            return False, f"일일 최대 거래 횟수 도달: {today_count}/{self.limits.max_daily_trades}"

        # 4. 일일 손실률 체크
        daily_pnl = self._get_today_pnl_pct(coin)
        if daily_pnl <= self.limits.max_daily_loss_pct:
            return False, f"일일 최대 손실 도달: {daily_pnl:.1f}% <= {self.limits.max_daily_loss_pct:.1f}%"

        # 5. 연속 손실 체크
        consecutive = self._get_consecutive_losses(coin)
        if consecutive >= self.limits.max_consecutive_losses:
            return False, f"연속 {consecutive}회 손실 — 매매 중단"

        # 6. 매도 직후 재매수 쿨다운 — ALGO처럼 손절→1분뒤 재매수 패턴 차단
        cooldown_min = self.limits.coin_reentry_cooldown_minutes
        if cooldown_min > 0:
            mins_since_sell = self._minutes_since_last_sell(coin)
            if mins_since_sell is not None and mins_since_sell < cooldown_min:
                return False, (
                    f"재매수 쿨다운: {coin} 마지막 매도 {mins_since_sell:.0f}분 전 "
                    f"({cooldown_min}분 후 재진입 가능)"
                )

        return True, "리스크 점검 통과"

    def check_can_sell(self, coin: str) -> tuple[bool, str]:
        """매도 가능 여부 점검. (현재는 항상 허용 — 손절은 막으면 안 됨)"""
        return True, "매도 허용"

    def check_account_daily_loss(self, current_krw: float) -> tuple[bool, str]:
        """계좌 전체 오늘(KST) 실현 손실 %가 한도 이하면 매수 차단.

        매도는 영향 없음 (check_can_sell은 항상 허용) — 손절·익절은 계속 작동해야 함.

        계산 근사:
          시작_자산 ≈ 현재_KRW + 보유_코인_원가_합 - 오늘_실현_PnL
          손실% = 오늘_실현_PnL / 시작_자산 × 100

        미실현 손익은 무시. "확정 손실" 기준.
        """
        today_pnl = self._get_today_account_pnl_krw()
        if today_pnl >= 0:
            return True, "오늘 흑자"

        held_cost = self._get_held_coins_total_cost()
        start_asset = current_krw + held_cost - today_pnl
        if start_asset <= 0:
            return True, "시작 자산 산정 불가"

        loss_pct = today_pnl / start_asset * 100
        limit = self.limits.max_daily_account_loss_pct
        if loss_pct <= limit:
            return False, (
                f"계좌 일일 손실 한도 도달: {loss_pct:.1f}% ≤ {limit:.1f}% "
                f"(실현 {today_pnl:,.0f}원 / 시작 {start_asset:,.0f}원). "
                f"매도는 계속 허용, 매수만 차단."
            )
        return True, f"계좌 손실 {loss_pct:.1f}% (한도 {limit:.1f}%)"

    def _get_today_account_pnl_krw(self) -> float:
        """오늘(KST) 전체 실현 손익 합계."""
        row = self._db.execute(
            "SELECT COALESCE(SUM(profit_krw), 0) FROM trades "
            "WHERE side='sell' AND DATE(timestamp, '+9 hours') = DATE('now', '+9 hours')"
        ).fetchone()
        return float(row[0]) if row else 0.0

    def _get_held_coins_total_cost(self) -> float:
        """현재 보유 중인 모든 코인의 매수 원가(+수수료) 합계."""
        row = self._db.execute(
            """
            SELECT COALESCE(SUM(total_krw + COALESCE(fee_krw, 0)), 0)
            FROM trades t WHERE side='buy'
            AND NOT EXISTS (SELECT 1 FROM trades s WHERE s.buy_trade_id = t.id AND s.side='sell')
            """
        ).fetchone()
        return float(row[0]) if row else 0.0

    def get_safe_position_size(
        self, balance_krw: float, confidence: float = 1.0, position_size_pct: float = 100.0
    ) -> float:
        """신호 강도 기반 안전한 매수 금액 계산.

        confidence와 position_size_pct를 곱하여 매수 비율을 결정한다.
        예) confidence=0.7, position_size_pct=50 → 가용 잔고의 35% 매수

        Args:
            balance_krw: 현재 잔고
            confidence: 매수 신호 강도 (0.0 ~ 1.0)
            position_size_pct: 전략 파라미터의 포지션 비율 (0 ~ 100)

        Returns:
            매수 가능 금액 (원)
        """
        available = balance_krw - self.limits.min_balance_krw
        if available <= 0:
            return 0

        ratio = max(0.0, min(confidence, 1.0)) * max(0.0, min(position_size_pct, 100.0)) / 100.0
        sized_amount = available * ratio

        return min(sized_amount, self.limits.max_position_size_krw)

    def _get_today_trade_count(self, coin: str) -> int:
        """오늘(KST) 거래 횟수.

        DB timestamp는 UTC. KST 일 경계 기준으로 카운트하려면 +9시간 변환 후 DATE 비교.
        UTC 기준 DATE로 하면 KST 00시~09시 구간이 전날로 오판됨.
        """
        row = self._db.execute(
            "SELECT COUNT(*) FROM trades WHERE coin = ? AND DATE(timestamp, '+9 hours') = DATE('now', '+9 hours')",
            (coin,),
        ).fetchone()
        return row[0] if row else 0

    def _get_today_pnl_pct(self, coin: str) -> float:
        """오늘(KST) 누적 수익률."""
        row = self._db.execute(
            """
            SELECT COALESCE(SUM(profit_pct), 0)
            FROM trades
            WHERE coin = ? AND side = 'sell'
              AND DATE(timestamp, '+9 hours') = DATE('now', '+9 hours')
            """,
            (coin,),
        ).fetchone()
        return float(row[0]) if row else 0.0

    def _minutes_since_last_sell(self, coin: str) -> float | None:
        """해당 코인의 가장 최근 매도 후 경과 분. 매도 기록 없으면 None."""
        row = self._db.execute(
            """
            SELECT (julianday('now') - julianday(timestamp)) * 24 * 60 AS gap_min
            FROM trades
            WHERE coin = ? AND side = 'sell'
            ORDER BY id DESC LIMIT 1
            """,
            (coin,),
        ).fetchone()
        if not row:
            return None
        gap = dict(row)["gap_min"]
        return float(gap) if gap is not None else None

    def _get_consecutive_losses(self, coin: str) -> int:
        """최근 consecutive_loss_window_hours 내 연속 손실 횟수.

        시간 윈도우를 두지 않으면 한 번 연속 손실이 발생한 코인이 영구 차단된다
        (다음 수익 거래가 나올 때까지 재매수 금지). 매매가 아예 중단된 코인에는
        이 서킷브레이커가 의미 없으므로 최근 윈도우로 제한한다.
        """
        window_hours = self.limits.consecutive_loss_window_hours
        rows = self._db.execute(
            """
            SELECT profit_pct FROM trades
            WHERE coin = ? AND side = 'sell' AND profit_pct IS NOT NULL
              AND timestamp >= datetime('now', ?)
            ORDER BY id DESC LIMIT ?
            """,
            (coin, f"-{window_hours} hours", self.limits.max_consecutive_losses),
        ).fetchall()

        count = 0
        for row in rows:
            if row["profit_pct"] < 0:
                count += 1
            else:
                break
        return count
