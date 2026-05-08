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

# KIS API rate limit (#319):
# - 공식 실전 한도 1초 20건이지만, 특정 endpoint(시세/잔고)는 더 빡빡 (~5건/초)
# - 봇 + admin API + 디버그 동시 호출 시 한꺼번에 터져 "초당 거래건수 초과" 빈번
# - 0.06초(16건/초)는 너무 공격적 → 0.25초(4건/초) 안전 마진
DEFAULT_MIN_INTERVAL_SEC = 0.25  # 1초 4건 (5건 한도 + 마진)
PAPER_MIN_INTERVAL_SEC = 0.5  # 1초 2건 (모의)


class KISClient:
    """KIS API REST 호출 클라이언트.

    rate limiter 내장 (스레드 세이프).
    """

    def __init__(self, token_manager: KISTokenManager, is_paper: bool = False) -> None:
        self._tm = token_manager
        self._min_interval = PAPER_MIN_INTERVAL_SEC if is_paper else DEFAULT_MIN_INTERVAL_SEC
        self._last_call_at = 0.0
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
        self._throttle()

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

    def _throttle(self) -> None:
        """rate limit. 마지막 호출 후 min_interval 미만이면 sleep."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call_at
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_call_at = time.monotonic()
