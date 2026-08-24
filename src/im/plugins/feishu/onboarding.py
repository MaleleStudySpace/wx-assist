"""Feishu/Lark QR application registration flow.

Mirrors Hermes' registration endpoint flow.  It creates an OAuth device
registration task and polls until the user authorizes it in Feishu.
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

DOMAINS = {
    "feishu": "https://accounts.feishu.cn",
    "lark": "https://accounts.larksuite.com",
}
REGISTRATION_PATH = "/oauth/v1/app/registration"
REQUEST_TIMEOUT = 10.0


@dataclass
class FeishuOnboardingTask:
    task_id: str
    domain: str
    interval: int
    expires_at: float
    status: str = "pending"
    result: Optional[dict] = None


class FeishuOnboarding:
    def __init__(self):
        self._tasks: dict[str, FeishuOnboardingTask] = {}
        self._lock = threading.RLock()

    def start(self, domain: str = "feishu", timeout_seconds: int = 600) -> dict:
        if domain not in DOMAINS:
            raise ValueError("unsupported Feishu domain")
        base = DOMAINS[domain]
        self._post(base, {"action": "init"})
        data = self._post(base, {
            "action": "begin",
            "archetype": "PersonalAgent",
            "auth_method": "client_secret",
            "request_user_info": "open_id",
        })
        task_id = str(data.get("device_code", ""))
        if not task_id:
            raise RuntimeError("Feishu registration missing device_code")
        expires = min(int(data.get("expire_in", 600)), timeout_seconds)
        task = FeishuOnboardingTask(
            task_id=task_id,
            domain=domain,
            interval=int(data.get("interval", 5) or 5),
            expires_at=time.time() + expires,
        )
        with self._lock:
            self._tasks[task_id] = task
        return {
            "task_id": task_id,
            "qr_url": data.get("verification_uri_complete", ""),
            "user_code": data.get("user_code", ""),
            "expires_at": task.expires_at,
        }

    def poll(self, task_id: str) -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            raise KeyError("Feishu onboarding task not found")
        if time.time() >= task.expires_at:
            task.status = "expired"
            return {"status": task.status}
        data = self._post(DOMAINS[task.domain], {
            "action": "poll",
            "device_code": task.task_id,
            "tp": "ob_app",
        }, allow_error=True)
        if data.get("client_id") and data.get("client_secret"):
            task.status = "completed"
            task.result = {
                "app_id": str(data["client_id"]),
                "app_secret": str(data["client_secret"]),
                "domain": task.domain,
                "open_id": str((data.get("user_info") or {}).get("open_id", "")),
            }
            return {"status": task.status, **task.result}
        error = data.get("error", data.get("error_code", ""))
        if error in ("access_denied", "expired_token"):
            task.status = "expired" if error == "expired_token" else "denied"
        return {"status": task.status, "error": error}

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            task.status = "cancelled"
            return True

    @staticmethod
    def _post(base: str, body: dict, allow_error: bool = False) -> dict:
        response = requests.post(
            f"{base}{REGISTRATION_PATH}",
            data=urlencode(body).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code >= 400 and not allow_error:
            raise RuntimeError(f"Feishu registration HTTP {response.status_code}")
        data = response.json()
        if not allow_error and data.get("error"):
            raise RuntimeError(data.get("error"))
        return data


_onboarding = FeishuOnboarding()


def get_feishu_onboarding() -> FeishuOnboarding:
    return _onboarding
