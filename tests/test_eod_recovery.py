"""#373: missed-EOD 복구 로직 테스트.

봇 재시작·장애로 EOD 윈도우(±5분)를 놓친 경우, EOD 시각부터 다음 ORB 사이의
재시작 시 즉시 청산되어야 함.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def _is_eod_or_missed(now_kst: datetime, eod_hour: int, orb_hour: int) -> tuple[bool, bool]:
    """bot/main.py:_maybe_eod_clearance 의 윈도우 판정 로직 미러.

    Returns:
        (in_window, missed_eod)
    """
    # in_window: ±5분 (is_eod_window 디폴트)
    eod = now_kst.replace(hour=eod_hour, minute=0, second=0, microsecond=0)
    delta = abs((now_kst - eod).total_seconds())
    in_window = delta <= 5 * 60

    missed_eod = (
        not in_window
        and eod_hour <= now_kst.hour < orb_hour
    )
    return in_window, missed_eod


# Option 1: EOD 11:00, ORB 22:00


def test_in_window_at_eod_exact():
    """KST 11:00 정각 → in_window."""
    in_w, missed = _is_eod_or_missed(datetime(2026, 5, 11, 11, 0, tzinfo=KST), 11, 22)
    assert in_w is True
    assert missed is False


def test_in_window_within_5min():
    """KST 11:04 → in_window."""
    in_w, missed = _is_eod_or_missed(datetime(2026, 5, 11, 11, 4, tzinfo=KST), 11, 22)
    assert in_w is True
    assert missed is False


def test_missed_eod_just_after_window():
    """KST 11:06 → missed-EOD 복구."""
    in_w, missed = _is_eod_or_missed(datetime(2026, 5, 11, 11, 6, tzinfo=KST), 11, 22)
    assert in_w is False
    assert missed is True


def test_missed_eod_at_15h():
    """KST 15:00 → missed-EOD 복구."""
    in_w, missed = _is_eod_or_missed(datetime(2026, 5, 11, 15, 0, tzinfo=KST), 11, 22)
    assert in_w is False
    assert missed is True


def test_missed_eod_at_21h59():
    """KST 21:59 → missed-EOD 복구 마지막 분."""
    in_w, missed = _is_eod_or_missed(datetime(2026, 5, 11, 21, 59, tzinfo=KST), 11, 22)
    assert in_w is False
    assert missed is True


def test_no_action_at_22h_orb_start():
    """KST 22:00 ORB 시작 → 청산 안함 (다음 사이클 진입)."""
    in_w, missed = _is_eod_or_missed(datetime(2026, 5, 11, 22, 0, tzinfo=KST), 11, 22)
    assert in_w is False
    assert missed is False


def test_no_action_during_entry_window():
    """KST 23:30 진입 윈도우 안 → 청산 안함 (오늘 매수 중)."""
    in_w, missed = _is_eod_or_missed(datetime(2026, 5, 11, 23, 30, tzinfo=KST), 11, 22)
    assert in_w is False
    assert missed is False


def test_no_action_at_midnight():
    """KST 00:30 → 청산 안함 (어제 시작된 진입 윈도우 안)."""
    in_w, missed = _is_eod_or_missed(datetime(2026, 5, 11, 0, 30, tzinfo=KST), 11, 22)
    assert in_w is False
    assert missed is False


def test_no_action_at_05h_pre_eod():
    """KST 05:00 진입 윈도우 끝난 직후 ~ EOD 전 → 청산 안함 (보유 유지)."""
    in_w, missed = _is_eod_or_missed(datetime(2026, 5, 11, 5, 0, tzinfo=KST), 11, 22)
    assert in_w is False
    assert missed is False


def test_no_action_at_10h_just_before_eod():
    """KST 10:00 → 청산 안함 (EOD 1시간 전)."""
    in_w, missed = _is_eod_or_missed(datetime(2026, 5, 11, 10, 0, tzinfo=KST), 11, 22)
    assert in_w is False
    assert missed is False
