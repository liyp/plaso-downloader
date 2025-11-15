"""Authentication helpers for plaso Electron login flow."""

from __future__ import annotations

import hashlib
import logging
from typing import Dict

from ..models import LoginResult
from ..utils.http_client import AuthenticationError, HttpClient

LOGIN_PATH = "custom/usr/doLogin"
PRESET_DEVICE_INFO: Dict[str, str | int] = {
    "role": 1,
    "osInfo": "Apple M1 Pro 32.00G 0.07G Darwin25.1.0 arm64 ",
    "version": "12.0.18_5.61.185",
    "deviceId": "mac-58caaf03-2c25-5b0d-9bdc-6407dbf0bb17",
    "deviceName": "PT7F40Q9RL",
    "systemInfo": "25.1.0 arm64 1.07.129",
    "clientVersion": "5.61.185",
}


class AuthAPI:
    """Encapsulates the login process to retrieve a new access-token."""

    def __init__(self, http_client: HttpClient) -> None:
        self._client = http_client

    def login(self, phone: str, password: str, password_is_md5: bool = False) -> LoginResult:
        hashed_password = password if password_is_md5 else hashlib.md5(password.encode("utf-8")).hexdigest()
        payload = {
            **PRESET_DEVICE_INFO,
            "rawName": phone,
            "name": phone,
            "loginName": phone,
            "loginMobile": phone,
            "passwd": hashed_password,
        }
        try:
            data = self._client.request_api(LOGIN_PATH, payload)
        except AuthenticationError:
            raise
        except Exception as exc:  # pragma: no cover - network errors
            logging.error("Login request failed: %s", exc)
            raise

        if data.get("code") != 0:
            message = data.get("msg") or data.get("message") or "登录失败"
            raise ValueError(f"Plaso 登录失败: {message}")

        obj = data.get("obj") or {}
        access_token = obj.get("access_token")
        if not access_token:
            raise ValueError("登录响应缺少 access_token")

        login_result = LoginResult(
            access_token=access_token,
            login_name=obj.get("loginName") or obj.get("loginname") or phone,
            user_id=obj.get("id") or obj.get("myid"),
            org_id=(obj.get("myOrg") or {}).get("parentId") or obj.get("org_id"),
            raw=obj,
        )
        logging.info("登录成功，用户 %s (uid=%s)", login_result.login_name, login_result.user_id)
        return login_result
