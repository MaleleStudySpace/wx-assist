"""Synchronous Feishu Open API client."""

import threading
import time
from typing import Optional

import requests

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
API_BASE = "https://open.feishu.cn/open-apis"
DEFAULT_TIMEOUT = 30.0


class FeishuAPIError(RuntimeError):
    pass


class FeishuOpenAPIClient:
    def __init__(self, app_id: str, app_secret: str,
                 session: Optional[requests.Session] = None):
        self.app_id = app_id.strip()
        self.app_secret = app_secret.strip()
        self._session = session or requests.Session()
        self._token: Optional[str] = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def ensure_token(self) -> str:
        if self._token and time.time() < self._expires_at - 1800:
            return self._token
        with self._lock:
            if self._token and time.time() < self._expires_at - 1800:
                return self._token
            response = self._session.post(
                TOKEN_URL,
                json={"app_id": self.app_id, "app_secret": self.app_secret},
                timeout=DEFAULT_TIMEOUT,
            )
            if response.status_code >= 400:
                raise FeishuAPIError(f"token request failed [{response.status_code}]")
            data = response.json()
            if data.get("code", 0) != 0 or not data.get("tenant_access_token"):
                raise FeishuAPIError(f"token request failed: {data.get('msg', data)}")
            self._token = data["tenant_access_token"]
            self._expires_at = time.time() + int(data.get("expire", 7200))
            return self._token

    def request(self, method: str, path: str, *, params=None, body=None) -> dict:
        for attempt in range(2):
            token = self.ensure_token()
            response = self._session.request(
                method,
                f"{API_BASE}{path}",
                params=params,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                timeout=DEFAULT_TIMEOUT,
            )
            if response.status_code == 401 and attempt == 0:
                with self._lock:
                    self._token = None
                    self._expires_at = 0.0
                continue
            if response.status_code >= 400:
                raise FeishuAPIError(f"HTTP {response.status_code}: {response.text[:300]}")
            data = response.json()
            if data.get("code", 0) != 0:
                raise FeishuAPIError(f"Feishu API error: {data.get('msg', data)}")
            return data
        raise FeishuAPIError("Feishu request failed after token refresh")

    def send_text(self, receive_id: str, text: str,
                  receive_id_type: str = "chat_id") -> dict:
        return self.request(
            "POST",
            "/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            body={
                "receive_id": receive_id,
                "msg_type": "text",
                "content": __import__("json").dumps(
                    {"text": text}, ensure_ascii=False
                ),
            },
        )

    def close(self):
        self._session.close()
