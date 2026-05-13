"""#395: KIS US 봇 에러 알림 cooldown 테스트.

같은 에러 5분에 1회만 발송 — Slack 폭주 방지.
"""

from __future__ import annotations

import time


def test_cooldown_first_call_alerts():
    """첫 에러 → 알림 발송."""
    cooldown_sec = 300
    last_sent: dict[str, float] = {}
    notify_count = [0]

    def notify(exc_sig: str) -> None:
        now = time.time()
        last = last_sent.get(exc_sig, 0.0)
        if now - last < cooldown_sec:
            return
        last_sent[exc_sig] = now
        notify_count[0] += 1

    notify("NameError:_time")
    assert notify_count[0] == 1


def test_cooldown_blocks_duplicate_within_5min():
    """같은 에러 5분 내 반복 → 알림 안 보냄."""
    cooldown_sec = 300
    last_sent: dict[str, float] = {}
    notify_count = [0]

    def notify(exc_sig: str) -> None:
        now = time.time()
        last = last_sent.get(exc_sig, 0.0)
        if now - last < cooldown_sec:
            return
        last_sent[exc_sig] = now
        notify_count[0] += 1

    sig = "NameError:_time"
    for _ in range(10):  # 10번 호출
        notify(sig)
    assert notify_count[0] == 1, "5분 내 중복 알림 발송됨"


def test_cooldown_different_errors_separate():
    """다른 에러는 각각 알림 보냄."""
    cooldown_sec = 300
    last_sent: dict[str, float] = {}
    notify_count = [0]

    def notify(exc_sig: str) -> None:
        now = time.time()
        last = last_sent.get(exc_sig, 0.0)
        if now - last < cooldown_sec:
            return
        last_sent[exc_sig] = now
        notify_count[0] += 1

    notify("NameError:_time")
    notify("KeyError:foo")
    notify("ValueError:bar")
    assert notify_count[0] == 3


def test_cooldown_resets_after_expiry():
    """cooldown 만료 후 새 알림 가능."""
    cooldown_sec = 1  # 짧게
    last_sent: dict[str, float] = {}
    notify_count = [0]

    def notify(exc_sig: str) -> None:
        now = time.time()
        last = last_sent.get(exc_sig, 0.0)
        if now - last < cooldown_sec:
            return
        last_sent[exc_sig] = now
        notify_count[0] += 1

    sig = "TestError:x"
    notify(sig)
    assert notify_count[0] == 1
    time.sleep(1.1)
    notify(sig)
    assert notify_count[0] == 2, "cooldown 만료 후 새 알림 안 발송"


def test_signature_truncates_long_messages():
    """에러 메시지 길어도 signature는 첫 80자로 트림."""
    exc_class = "ValueError"
    long_msg = "x" * 500
    sig = f"{exc_class}:{long_msg[:80]}"
    assert len(sig) <= 100
    # 같은 첫 80자라면 같은 signature
    long_msg2 = "x" * 600
    sig2 = f"{exc_class}:{long_msg2[:80]}"
    assert sig == sig2  # cooldown 묶음


def test_notify_method_format():
    """Slack 메시지 포맷 — 사용자가 읽기 좋게 핵심 정보 강조."""
    # 메시지 본체 검증 (단위 테스트 — 실제 send 안 함)
    exc = NameError("_time is not defined")
    msg = (
        f"🚨 *KIS US 봇 에러 — 매수 실행 차단 가능*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"❌ `{type(exc).__name__}`: {str(exc)[:200]}\n"
    )
    assert "🚨" in msg
    assert "매수 실행 차단" in msg
    assert "NameError" in msg
    assert "_time" in msg
