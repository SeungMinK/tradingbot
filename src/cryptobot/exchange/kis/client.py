"""KIS Developers REST 클라이언트.

토큰 매니저를 받아 KIS API REST 호출을 일관된 인터페이스로 제공.
한국주식·미국주식 어댑터에서 공통으로 사용.

Related: #246, #247
"""

from __future__ import annotations

import logging
import threading
import time

import requests

from cryptobot.exceptions import APIError
from cryptobot.exchange.kis.auth import KISTokenManager

logger = logging.getLogger(__name__)

# KIS API rate limit (#326, #325):
# - 공식 한도 1초 20건이지만 endpoint별 차등 (분봉/잔고는 1초 1회 수준)
# - 0.25초(4건/초)도 분봉 API에서 자주 터짐
# - endpoint group별 차등 throttle로 분봉만 보수적, 나머지는 빠르게
DEFAULT_MIN_INTERVAL_SEC = 0.25  # 1초 4건 (가격/일봉/주문 등 일반 endpoint)
PAPER_MIN_INTERVAL_SEC = 0.25

# endpoint group별 최소 간격 (실전 기준)
# - minute_chart: 분봉 API는 1초 1회 수준 (가장 보수적)
# - balance: 잔고 조회도 종종 throttle, 0.5s 마진
# - order: 주문은 신중히, 0.4s
# - default: 일반 조회 (현재가, 일봉)
ENDPOINT_INTERVALS_SEC: dict[str, float] = {
    "minute_chart": 1.0,
    "balance": 0.5,
    "order": 0.4,
    "default": DEFAULT_MIN_INTERVAL_SEC,
}


def classify_endpoint(path: str) -> str:
    """API 경로를 endpoint group으로 분류.

    Args:
        path: KIS API 경로 (예: /uapi/overseas-price/v1/quotations/inquire-time-itemchartprice)

    Returns:
        endpoint group 이름 (minute_chart / balance / order / default)
    """
    if "inquire-time-itemchartprice" in path:
        return "minute_chart"
    if "inquire-balance" in path or "inquire-present-balance" in path:
        return "balance"
    if "/trading/order" in path:
        return "order"
    return "default"


class KISClient:
    """KIS API REST 호출 클라이언트.

    rate limiter 내장 (스레드 세이프, endpoint group별 차등).
    """

    def __init__(self, token_manager: KISTokenManager, is_paper: bool = False) -> None:
        self._tm = token_manager
        # 모의투자도 동일 정책 (PAPER_MIN_INTERVAL_SEC는 default group에만 영향)
        intervals = dict(ENDPOINT_INTERVALS_SEC)
        if is_paper:
            intervals["default"] = PAPER_MIN_INTERVAL_SEC
        self._endpoint_intervals = intervals
        self._endpoint_last_call: dict[str, float] = {g: 0.0 for g in intervals}
        self._lock = threading.Lock()

    @property
    def host(self) -> str:
        return self._tm.host

    def get(
        self,
        path: str,
        tr_id: str,
        params: dict | None = None,
        timeout: int = 10,
    ) -> dict:
        """GET 호출.

        Args:
            path: API 경로 (예: /uapi/domestic-stock/v1/quotations/inquire-price)
            tr_id: 거래 ID
            params: 쿼리 파라미터

        Returns:
            응답 JSON dict
        """
        return self._call("GET", path, tr_id, params=params, timeout=timeout)

    def post(
        self,
        path: str,
        tr_id: str,
        body: dict | None = None,
        timeout: int = 10,
    ) -> dict:
        """POST 호출 (주문 등)."""
        return self._call("POST", path, tr_id, body=body, timeout=timeout)

    def _call(
        self,
        method: str,
        path: str,
        tr_id: str,
        params: dict | None = None,
        body: dict | None = None,
        timeout: int = 10,
    ) -> dict:
        """공통 호출 로직 (rate limit + 에러 처리)."""
        self._throttle(path)

        url = f"{self._tm.host}{path}"
        headers = self._tm.auth_headers(tr_id)

        try:
            if method == "GET":
                resp = requests.get(url, headers=headers, params=params, timeout=timeout)
            elif method == "POST":
                resp = requests.post(url, headers=headers, json=body, timeout=timeout)
            else:
                raise ValueError(f"지원하지 않는 메서드: {method}")
            resp.raise_for_status()
            data = resp.json()
        except requests.HTTPError as e:
            body_text = e.response.text if e.response is not None else ""
            raise APIError(f"KIS {method} {path} 실패 ({e.response.status_code}): {body_text}") from e
        except Exception as e:
            raise APIError(f"KIS {method} {path} 호출 실패: {e}") from e

        # KIS 응답 규약: rt_cd='0'이면 성공, 그 외는 에러
        rt_cd = data.get("rt_cd")
        if rt_cd is not None and rt_cd != "0":
            msg_cd = data.get("msg_cd")
            msg = data.get("msg1", "")
            raise APIError(f"KIS API 에러 ({path}): rt_cd={rt_cd} msg_cd={msg_cd} {msg}")

        return data

    def _throttle(self, path: str) -> None:
        """rate limit. endpoint group별 마지막 호출 후 min_interval 미만이면 sleep."""
        group = classify_endpoint(path)
        interval = self._endpoint_intervals[group]
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._endpoint_last_call[group]
            if elapsed < interval:
                time.sleep(interval - elapsed)
            self._endpoint_last_call[group] = time.monotonic()
