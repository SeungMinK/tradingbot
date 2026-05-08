"""KIS Developers 토큰 매니저.

한국투자증권 OpenAPI는 1일 유효 토큰을 사용한다.
6시간 이내 재발급 시 동일 토큰을 반환하므로 파일 캐싱으로 재사용한다.

KIS 공식 레포(koreainvestment/open-trading-api)의 `kis_auth.py` 패턴 차용.

Related: #246, #247
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from cryptobot.exceptions import APIError, ConfigError

logger = logging.getLogger(__name__)

KIS_HOST_REAL = "https://openapi.koreainvestment.com:9443"
KIS_HOST_PAPER = "https://openapivts.koreainvestment.com:29443"

DEFAULT_TOKEN_CACHE = Path.home() / ".config" / "cryptobot" / "kis_token.json"


class KISTokenManager:
    """KIS API 접근 토큰 관리자.

    토큰을 파일에 캐싱하고, 만료 임박 시 자동 갱신한다.
    한국주식·미국주식 어댑터가 동일 인스턴스를 공유하면 토큰 1회 발급으로 충분.
    """

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        is_paper: bool = False,
        cache_path: Path | None = None,
    ) -> None:
        if not app_key or not app_secret:
            raise ConfigError("KIS APP_KEY / APP_SECRET 미설정")

        self._app_key = app_key
        self._app_secret = app_secret
        self._host = KIS_HOST_PAPER if is_paper else KIS_HOST_REAL
        self._cache_path = cache_path or DEFAULT_TOKEN_CACHE

        self._token: str | None = None
        self._expires_at: datetime | None = None

        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_from_cache()

    @property
    def host(self) -> str:
        return self._host

    @property
    def app_key(self) -> str:
        return self._app_key

    @property
    def app_secret(self) -> str:
        return self._app_secret

    def get_token(self) -> str:
        """유효한 토큰 반환. 만료 임박 시 자동 갱신.

        만료 30분 전부터 갱신 트리거 (안전 마진).
        """
        if self._token and self._expires_at:
            now = datetime.now(timezone.utc)
            if now < self._expires_at - timedelta(minutes=30):
                return self._token

        return self._issue_token()

    def _issue_token(self) -> str:
        """토큰 신규 발급."""
        url = f"{self._host}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise APIError(f"KIS 토큰 발급 실패: {e}") from e

        token = data.get("access_token")
        if not token:
            raise APIError(f"KIS 토큰 발급 응답에 access_token 없음: {data}")

        expires_in = int(data.get("expires_in", 86400))
        self._token = token
        self._expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        self._save_to_cache()
        logger.info("KIS 토큰 신규 발급 (만료: %s)", self._expires_at.isoformat())
        return token

    def _load_from_cache(self) -> None:
        if not self._cache_path.exists():
            return
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            if data.get("app_key") != self._app_key:
                return  # 다른 키의 캐시
            self._token = data.get("token")
            exp = data.get("expires_at")
            if exp:
                self._expires_at = datetime.fromisoformat(exp)
            logger.debug("KIS 토큰 캐시 로드 완료 (만료: %s)", self._expires_at)
        except Exception as e:
            logger.warning("KIS 토큰 캐시 로드 실패: %s", e)

    def _save_to_cache(self) -> None:
        try:
            data = {
                "app_key": self._app_key,
                "token": self._token,
                "expires_at": self._expires_at.isoformat() if self._expires_at else None,
            }
            self._cache_path.write_text(json.dumps(data), encoding="utf-8")
            os.chmod(self._cache_path, 0o600)
        except Exception as e:
            logger.warning("KIS 토큰 캐시 저장 실패: %s", e)

    def auth_headers(self, tr_id: str, custtype: str = "P") -> dict[str, str]:
        """REST 호출 공통 헤더.

        Args:
            tr_id: 거래 ID (API마다 다름. 예: 한국 매수 'TTTC0802U')
            custtype: 'P' 개인 / 'B' 법인

        Returns:
            Authorization 포함 헤더 dict
        """
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.get_token()}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "custtype": custtype,
        }
