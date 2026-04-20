"""리스크 관리 테스트."""

import tempfile
from pathlib import Path

from cryptobot.bot.risk import RiskLimits, RiskManager
from cryptobot.data.database import Database
from cryptobot.data.recorder import DataRecorder


def _make_risk_manager(limits: RiskLimits | None = None):
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    return RiskManager(db, limits), DataRecorder(db), db


def test_can_buy_normal():
    """정상 상황에서 매수 허용."""
    rm, _, db = _make_risk_manager()
    try:
        can, reason = rm.check_can_buy("KRW-BTC", 100_000, 500_000)
        assert can is True
    finally:
        db.close()


def test_block_min_balance():
    """최소 잔고 미달 시 차단."""
    limits = RiskLimits(min_balance_krw=50_000)
    rm, _, db = _make_risk_manager(limits)
    try:
        can, reason = rm.check_can_buy("KRW-BTC", 80_000, 100_000)
        assert can is False
        assert "최소 잔고" in reason
    finally:
        db.close()


def test_block_max_position_size():
    """최대 매수 금액 초과 시 차단."""
    limits = RiskLimits(max_position_size_krw=100_000)
    rm, _, db = _make_risk_manager(limits)
    try:
        can, reason = rm.check_can_buy("KRW-BTC", 200_000, 500_000)
        assert can is False
        assert "최대 매수 금액" in reason
    finally:
        db.close()


def test_block_daily_trades_limit():
    """일일 거래 횟수 초과 시 차단."""
    limits = RiskLimits(max_daily_trades=2)
    rm, recorder, db = _make_risk_manager(limits)
    try:
        # 2건 거래 기록
        for _ in range(2):
            recorder.record_trade(
                coin="KRW-BTC",
                side="buy",
                price=50000000,
                amount=0.001,
                total_krw=50000,
                fee_krw=25,
                strategy="test",
                trigger_reason="test",
            )

        can, reason = rm.check_can_buy("KRW-BTC", 50_000, 500_000)
        assert can is False
        assert "거래 횟수" in reason
    finally:
        db.close()


def test_block_consecutive_losses():
    """연속 손실 시 차단."""
    limits = RiskLimits(max_consecutive_losses=2)
    rm, recorder, db = _make_risk_manager(limits)
    try:
        for i in range(2):
            buy_id = recorder.record_trade(
                coin="KRW-BTC",
                side="buy",
                price=50000000,
                amount=0.001,
                total_krw=50000,
                fee_krw=25,
                strategy="test",
                trigger_reason="test",
            )
            recorder.record_trade(
                coin="KRW-BTC",
                side="sell",
                price=49000000,
                amount=0.001,
                total_krw=49000,
                fee_krw=24.5,
                strategy="test",
                trigger_reason="손절",
                buy_trade_id=buy_id,
                profit_pct=-2.0,
                profit_krw=-1000,
            )

        can, reason = rm.check_can_buy("KRW-BTC", 50_000, 500_000)
        assert can is False
        assert "연속" in reason
    finally:
        db.close()


def test_safe_position_size():
    """안전한 매수 금액 계산 (기본: confidence=1.0, position_size_pct=100)."""
    limits = RiskLimits(min_balance_krw=10_000, max_position_size_krw=500_000)
    rm, _, db = _make_risk_manager(limits)
    try:
        # 잔고 100,000 - 최소잔고 10,000 = 90,000
        size = rm.get_safe_position_size(100_000)
        assert size == 90_000

        # 잔고 1,000,000 - 최소잔고 10,000 = 990,000 → max 500,000으로 제한
        size = rm.get_safe_position_size(1_000_000)
        assert size == 500_000

        # 잔고 부족
        size = rm.get_safe_position_size(5_000)
        assert size == 0
    finally:
        db.close()


def test_position_size_with_confidence():
    """confidence에 비례하여 매수 금액이 조절된다."""
    limits = RiskLimits(min_balance_krw=10_000, max_position_size_krw=1_000_000)
    rm, _, db = _make_risk_manager(limits)
    try:
        # 잔고 110,000 → 가용 100,000
        # confidence=0.5 → 50,000
        size = rm.get_safe_position_size(110_000, confidence=0.5)
        assert size == 50_000

        # confidence=0.3 → 30,000
        size = rm.get_safe_position_size(110_000, confidence=0.3)
        assert size == 30_000

        # confidence=1.0 → 100,000 (전액)
        size = rm.get_safe_position_size(110_000, confidence=1.0)
        assert size == 100_000

        # confidence=0.0 → 0
        size = rm.get_safe_position_size(110_000, confidence=0.0)
        assert size == 0
    finally:
        db.close()


def test_position_size_with_pct():
    """position_size_pct로 최대 비율을 제한한다."""
    limits = RiskLimits(min_balance_krw=10_000, max_position_size_krw=1_000_000)
    rm, _, db = _make_risk_manager(limits)
    try:
        # 가용 100,000, confidence=1.0, pct=50 → 50,000
        size = rm.get_safe_position_size(110_000, confidence=1.0, position_size_pct=50.0)
        assert size == 50_000

        # 가용 100,000, confidence=0.7, pct=50 → 35,000
        size = rm.get_safe_position_size(110_000, confidence=0.7, position_size_pct=50.0)
        assert size == 35_000
    finally:
        db.close()


def test_position_size_capped_by_max():
    """confidence×pct 적용 후에도 max_position_size_krw로 상한 제한."""
    limits = RiskLimits(min_balance_krw=10_000, max_position_size_krw=50_000)
    rm, _, db = _make_risk_manager(limits)
    try:
        # 가용 990,000, confidence=1.0 → 990,000이지만 max 50,000
        size = rm.get_safe_position_size(1_000_000, confidence=1.0)
        assert size == 50_000
    finally:
        db.close()


def test_min_order_amount_check():
    """업비트 최소 주문 금액(5,000원) 미달 시 차단."""
    rm, _, db = _make_risk_manager()
    try:
        can, reason = rm.check_can_buy("KRW-BTC", 3_000, 500_000)
        assert can is False
        assert "최소 주문 금액" in reason
    finally:
        db.close()


def test_sell_always_allowed():
    """매도는 항상 허용 (손절 차단하면 안 됨)."""
    rm, _, db = _make_risk_manager()
    try:
        can, _ = rm.check_can_sell("KRW-BTC")
        assert can is True
    finally:
        db.close()


def test_consecutive_losses_outside_window_ignored():
    """시간 윈도우 밖의 과거 손실은 현재 매매를 차단하지 않는다.

    과거 이슈: 한번 연속 3회 손실난 코인이 수익 거래가 나올 때까지 영구 차단됐음.
    """
    limits = RiskLimits(max_consecutive_losses=3, consecutive_loss_window_hours=24)
    rm, recorder, db = _make_risk_manager(limits)
    try:
        # 2일 전 매도 3건 — 전부 손실
        for _ in range(3):
            buy_id = recorder.record_trade(
                coin="KRW-BTC",
                side="buy",
                price=50000000,
                amount=0.001,
                total_krw=50000,
                fee_krw=25,
                strategy="test",
                trigger_reason="test",
            )
            recorder.record_trade(
                coin="KRW-BTC",
                side="sell",
                price=48000000,
                amount=0.001,
                total_krw=48000,
                fee_krw=24,
                strategy="test",
                trigger_reason="손절",
                buy_trade_id=buy_id,
                profit_pct=-4.0,
                profit_krw=-2000,
            )
        # 모든 레코드를 윈도우 밖으로 이동 (2일 전)
        db.execute("UPDATE trades SET timestamp = datetime('now', '-2 days')")
        db.commit()

        can, _ = rm.check_can_buy("KRW-BTC", 50_000, 500_000)
        assert can is True, "24시간 윈도우 밖 손실은 차단해선 안 됨"
    finally:
        db.close()


def test_consecutive_losses_within_window_blocks():
    """시간 윈도우 안 연속 손실은 차단한다."""
    limits = RiskLimits(max_consecutive_losses=3, consecutive_loss_window_hours=24, max_daily_loss_pct=-100.0)
    rm, recorder, db = _make_risk_manager(limits)
    try:
        for _ in range(3):
            buy_id = recorder.record_trade(
                coin="KRW-BTC",
                side="buy",
                price=50000000,
                amount=0.001,
                total_krw=50000,
                fee_krw=25,
                strategy="test",
                trigger_reason="test",
            )
            recorder.record_trade(
                coin="KRW-BTC",
                side="sell",
                price=49950000,
                amount=0.001,
                total_krw=49950,
                fee_krw=25,
                strategy="test",
                trigger_reason="손절",
                buy_trade_id=buy_id,
                profit_pct=-0.1,
                profit_krw=-50,
            )

        can, reason = rm.check_can_buy("KRW-BTC", 50_000, 500_000)
        assert can is False
        assert "연속" in reason
    finally:
        db.close()


def test_default_consecutive_losses_allows_4_losses():
    """기본 max_consecutive_losses=5 — 4회 연속 손실까지는 이 가드에서 차단 안 함.

    다른 가드(일일 손실률 등)는 분리 테스트. 여기선 연속 손실 카운트만 검증.
    """
    # cooldown=0: 이 테스트는 매도 직후 매수 가능 여부만 검증, #208 재매수 가드는 분리 테스트.
    limits = RiskLimits(max_consecutive_losses=5, max_daily_loss_pct=-100.0, coin_reentry_cooldown_minutes=0)
    rm, recorder, db = _make_risk_manager(limits)
    try:
        for _ in range(4):
            buy_id = recorder.record_trade(
                coin="KRW-BTC",
                side="buy",
                price=50000000,
                amount=0.001,
                total_krw=50000,
                fee_krw=25,
                strategy="test",
                trigger_reason="test",
            )
            recorder.record_trade(
                coin="KRW-BTC",
                side="sell",
                price=49950000,
                amount=0.001,
                total_krw=49950,
                fee_krw=25,
                strategy="test",
                trigger_reason="손절",
                buy_trade_id=buy_id,
                profit_pct=-0.1,
                profit_krw=-50,
            )

        can, _ = rm.check_can_buy("KRW-BTC", 50_000, 500_000)
        assert can is True, "4회 연속 손실은 5회 미만이라 허용돼야 함"
    finally:
        db.close()
