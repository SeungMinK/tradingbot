"""KIS rate limiter (endpoint group별 차등 throttle) 테스트."""

import time
from unittest.mock import MagicMock

import pytest

from cryptobot.exchange.kis.client import (
    ENDPOINT_INTERVALS_SEC,
    KISClient,
    classify_endpoint,
)


class TestClassifyEndpoint:
    """classify_endpoint() 분류 정확성 테스트."""

    def test_minute_chart(self):
        assert classify_endpoint("/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice") == "minute_chart"

    def test_balance(self):
        assert classify_endpoint("/uapi/overseas-stock/v1/trading/inquire-balance") == "balance"
        assert classify_endpoint("/uapi/overseas-stock/v1/trading/inquire-present-balance") == "balance"
        assert classify_endpoint("/uapi/domestic-stock/v1/trading/inquire-balance") == "balance"

    def test_order(self):
        assert classify_endpoint("/uapi/overseas-stock/v1/trading/order") == "order"
        assert classify_endpoint("/uapi/domestic-stock/v1/trading/order-cash") == "order"

    def test_default(self):
        assert classify_endpoint("/uapi/overseas-price/v1/quotations/price") == "default"
        assert classify_endpoint("/uapi/overseas-price/v1/quotations/dailyprice") == "default"
        assert classify_endpoint("/uapi/domestic-stock/v1/quotations/inquire-price") == "default"


class TestEndpointIntervals:
    """ENDPOINT_INTERVALS_SEC 설정값 검증."""

    def test_groups_present(self):
        for g in ("minute_chart", "balance", "order", "default"):
            assert g in ENDPOINT_INTERVALS_SEC

    def test_minute_chart_strictest(self):
        """분봉이 가장 보수적이어야 함."""
        mc = ENDPOINT_INTERVALS_SEC["minute_chart"]
        for g, v in ENDPOINT_INTERVALS_SEC.items():
            if g != "minute_chart":
                assert mc >= v, f"minute_chart({mc}s)가 {g}({v}s)보다 짧음"

    def test_default_fastest(self):
        """default가 가장 빨라야 함 (또는 동률)."""
        dft = ENDPOINT_INTERVALS_SEC["default"]
        for v in ENDPOINT_INTERVALS_SEC.values():
            assert dft <= v


def _make_client() -> KISClient:
    tm = MagicMock()
    tm.host = "https://test"
    tm.auth_headers = MagicMock(return_value={})
    return KISClient(tm)


class TestThrottlePerEndpoint:
    """endpoint group별 독립 throttle 동작 검증."""

    def test_separate_groups_dont_block_each_other(self):
        """다른 group 호출은 서로 막지 않음."""
        client = _make_client()
        # 분봉 group을 막 호출한 것처럼 시뮬레이션
        client._endpoint_last_call["minute_chart"] = time.monotonic()
        # default group throttle 호출은 즉시 통과해야 함
        start = time.monotonic()
        client._throttle("/uapi/overseas-price/v1/quotations/price")
        elapsed = time.monotonic() - start
        assert elapsed < 0.1, f"default group이 minute_chart 때문에 막힘: {elapsed}s"

    def test_same_group_blocks(self):
        """같은 group 호출은 interval 만큼 sleep."""
        client = _make_client()
        # 첫 호출
        client._throttle("/uapi/overseas-price/v1/quotations/price")
        # 즉시 두 번째 호출 — default interval(0.25s)만큼 sleep되어야
        start = time.monotonic()
        client._throttle("/uapi/overseas-price/v1/quotations/price")
        elapsed = time.monotonic() - start
        # 약간의 마진 (0.2 ~ 0.35)
        assert 0.2 <= elapsed <= 0.4, f"default interval 미적용: {elapsed}s"

    def test_minute_chart_blocks_longer(self):
        """분봉은 1초 이상 sleep."""
        client = _make_client()
        client._throttle("/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice")
        start = time.monotonic()
        client._throttle("/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice")
        elapsed = time.monotonic() - start
        assert 0.9 <= elapsed <= 1.15, f"minute_chart interval 미적용: {elapsed}s"


@pytest.mark.parametrize(
    "path,expected_group",
    [
        ("/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice", "minute_chart"),
        ("/uapi/overseas-stock/v1/trading/inquire-balance", "balance"),
        ("/uapi/overseas-stock/v1/trading/inquire-present-balance", "balance"),
        ("/uapi/overseas-stock/v1/trading/order", "order"),
        ("/uapi/domestic-stock/v1/trading/order-cash", "order"),
        ("/uapi/overseas-price/v1/quotations/price", "default"),
        ("/uapi/overseas-price/v1/quotations/dailyprice", "default"),
    ],
)
def test_classify_endpoint_param(path: str, expected_group: str):
    assert classify_endpoint(path) == expected_group
