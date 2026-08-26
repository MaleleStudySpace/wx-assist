"""Synchronous QQ Bot OpenAPI client.

Protocol values follow Hermes' QQ adapter and the official QQ Bot API:
access_token is obtained from bots.qq.com and REST requests use
``Authorization: QQBot <token>``.
"""

import logging
import threading
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

TOKEN_URL = "https://api.bot.qq.com/app/getAppAccessToken"
API_BASE = "https://api.bot.qq.com"
DEFAULT_TIMEOUT = 30.0


class QQOpenAPIError(RuntimeError):
    """An HTTP or protocol error returned by QQ OpenAPI."""


class QQOpenAPIClient:
    def __init__(self, app_id: str, client_secret: str,
                 session: Optional[requests.Session] = None):
        self.app_id = app_id.strip()
        self.client_secret = client_secret.strip()
        self._session = session or requests.Session()
        self._access_token: Optional[str] = None
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()
        self._request_lock = threading.RLock()

    def ensure_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token
        with self._token_lock:
            if self._access_token and time.time() < self._token_expires_at - 60:
                return self._access_token
            logger.info("[qqbot] requesting access_token from %s", TOKEN_URL)
            try:
                response = self._session.post(
                    TOKEN_URL,
                    json={"appId": self.app_id, "clientSecret": self.client_secret},
                    timeout=DEFAULT_TIMEOUT,
                )
                if response.status_code >= 400:
                    logger.error("[qqbot] token request failed [%d]: %s", response.status_code, response.text[:300])
                    raise QQOpenAPIError(
                        f"token request failed [{response.status_code}]: {response.text[:300]}"
                    )
                data = response.json()
                token = str(data.get("access_token", ""))
                if not token:
                    logger.error("[qqbot] token response missing access_token: %s", data)
                    raise QQOpenAPIError("token response did not contain access_token")
                expires_in = int(data.get("expires_in", 7200) or 7200)
                self._access_token = token
                self._token_expires_at = time.time() + expires_in
                logger.info("[qqbot] access_token obtained, expires_in=%ds", expires_in)
                return token
            except Exception as exc:
                logger.error("[qqbot] ensure_token failed: %s", exc)
                raise

    def get_gateway_url(self) -> str:
        logger.info("[qqbot] requesting gateway URL from %s/gateway", API_BASE)
        data = self.request("GET", "/gateway")
        url = str(data.get("url", ""))
        if not url:
            raise QQOpenAPIError("gateway response did not contain url")
        logger.info("[qqbot] gateway url: %s", url[:80])
        return url

    def request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        with self._request_lock:
            token = self.ensure_token()
            response = self._session.request(
                method,
                f"{API_BASE}{path}",
                headers={
                    "Authorization": f"QQBot {token}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=DEFAULT_TIMEOUT,
            )
            if response.status_code == 401:
                self._access_token = None
            if response.status_code >= 400:
                raise QQOpenAPIError(
                    f"QQ API error [{response.status_code}] {path}: {response.text[:300]}"
                )
            if not response.text:
                return {}
            data = response.json()
            return data if isinstance(data, dict) else {"data": data}

    def close(self) -> None:
        self._session.close()
