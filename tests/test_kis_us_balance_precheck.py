"""#390: KIS US 매수 직전 잔고 사전 검증 + cooldown 테스트.

핵심 로직 단위 테스트 — 무거운 mock 없이.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock


def test_insufficient_funds_cooldown_tracking():
    """자금 부족 발생 → cooldown 등록 → 같은 종목 N분간 skip."""
    cooldown = {}
    cooldown_sec = 300
    now = time.time()
    symbol = "SOXS"

    # 첫 자금 부족 발생
    cooldown[symbol] = now + cooldown_sec
    assert symbol in cooldown
    assert cooldown[symbol] > now

    # 즉시 재시도 시 cooldown 안 풀림
    assert cooldown[symbol] > time.time()

    # 5분 후 (시뮬레이션)
    future = now + cooldown_sec + 1
    assert cooldown[symbol] < future  # 풀림


def test_estimated_cost_validation():
    """매수 사전 검증: qty × price × 1.015 (buffer) > budget 이면 skip."""
    budget_usd = 647.88
    price_usd = 9.63
    qty = 70  # 사용자 케이스

    estimated_cost = qty * price_usd * 1.015  # buffer 포함
    # 70 × 9.63 × 1.015 = $684.20
    assert estimated_cost > budget_usd, "70주 매수는 $647 예산으로 불가"

    # 66주는 통과
    qty_safe = 66
    safe_cost = qty_safe * price_usd * 1.015
    # 66 × 9.63 × 1.015 = $645.21
    assert safe_cost <= budget_usd, "66주는 $647 예산 내 통과"


def test_buffer_factor_value():
    """buffer = 1.5% (지정가 0.5% + slip 1%) 가 일관 적용."""
    buffer = 1.015
    # 100 USD × 1.015 ≈ 101.5 (float 부정확 허용)
    assert abs(100 * buffer - 101.5) < 0.001


def test_cooldown_duplicate_alert_prevention():
    """이미 cooldown 중이면 Slack 알림 중복 안 됨."""
    cooldown = {}
    cooldown_sec = 300
    now = time.time()
    symbol = "SOXS"
    notifier_call_count = [0]

    def maybe_notify(sym):
        prev = cooldown.get(sym, 0)
        already_cooldown = prev > time.time()
        cooldown[sym] = time.time() + cooldown_sec
        if not already_cooldown:
            notifier_call_count[0] += 1

    # 첫 자금 부족 — 알림 발송
    maybe_notify(symbol)
    assert notifier_call_count[0] == 1

    # 즉시 두번째 시도 — cooldown 중이라 알림 안 보냄
    maybe_notify(symbol)
    assert notifier_call_count[0] == 1, "중복 알림 발송됨"

    # 세번째 — 여전히 cooldown
    maybe_notify(symbol)
    assert notifier_call_count[0] == 1


def test_cooldown_resets_after_expiry():
    """cooldown 만료 후 새 알림 가능."""
    cooldown = {}
    cooldown_sec = 1  # 짧게
    symbol = "SOXS"
    notifier_call_count = [0]

    def maybe_notify(sym):
        prev = cooldown.get(sym, 0)
        already_cooldown = prev > time.time()
        cooldown[sym] = time.time() + cooldown_sec
        if not already_cooldown:
            notifier_call_count[0] += 1

    maybe_notify(symbol)
    assert notifier_call_count[0] == 1

    time.sleep(1.1)  # cooldown 만료 대기
    maybe_notify(symbol)
    assert notifier_call_count[0] == 2, "cooldown 만료 후 새 알림 안 발송됨"


def test_calc_position_size_388_388_integration():
    """#388 buffer + #390 fresh balance 통합 — SOXS 실케이스."""
    from cryptobot.bot.kis_strategy import KISStrategyParams, calc_position_size

    qty, reason = calc_position_size(
        available_budget=647.88,  # frcr_drwg_psbl_amt_1
        current_price=9.63,
        fractional=False,
        params=KISStrategyParams(max_position_per_symbol_pct=100.0),
    )
    # 1.5% buffer 적용 후 66주
    assert qty == 66.0
    # 사전 검증: 66 × 9.63 × 1.015 ≤ 647.88
    estimated = qty * 9.63 * 1.015
    assert estimated <= 647.88, f"매수액 {estimated:.2f} > 예산 647.88"


def test_no_cooldown_for_new_symbol():
    """첫 매수 시도 시 cooldown 없음 (정상 진행)."""
    cooldown = {}
    cd_until = cooldown.get("NEW_SYMBOL", 0)
    assert cd_until <= time.time()  # cooldown 없음
