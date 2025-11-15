"""Shared HTTP helpers for plaso APIs and CDN resources."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import aiohttp
import requests

REAL_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 26_1_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) ?????/1.07.129 Chrome/89.0.4389.128 Electron/12.0.18 Safari/537.36"
)

API_BASE = "https://www.plaso.cn/"
API_HEADERS_TEMPLATE: Dict[str, str] = {
    "Host": "www.plaso.cn",
    "platform": "plaso",
    "content-type": "application/json",
    "accept": "*/*",
    "user-agent": REAL_USER_AGENT,
    "sec-fetch-site": "cross-site",
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
    "accept-language": "zh-CN,zh;q=0.9",
}

CDN_HEADERS: Dict[str, str] = {
    "user-agent": REAL_USER_AGENT,
    "accept": "*/*",
    "accept-language": "zh-CN,zh;q=0.9",
}


class AuthenticationError(Exception):
    """Raised when the plaso API rejects authentication."""


class HttpClient:
    """Handles API and CDN requests with proper headers and throttling."""

    def __init__(self, access_token: str, timeout: int = 10) -> None:
        self.access_token = access_token
        self.timeout = timeout
        self._api_session = requests.Session()
        self._cdn_session = requests.Session()

        self._api_headers = API_HEADERS_TEMPLATE.copy()
        self._api_headers["access-token"] = access_token
        self._api_session.headers.update(self._api_headers)

        self._cdn_headers = CDN_HEADERS.copy()
        self._cdn_session.headers.update(self._cdn_headers)

        self._last_api_call = 0.0
        self._calls_since_pause = 0
        self._pause_after = random.randint(3, 5)
        self._min_api_interval = 1.0 / 8.0

        self._cdn_async_session: Optional[aiohttp.ClientSession] = None
        self._cdn_async_lock: Optional[asyncio.Lock] = None
        self._cdn_loop: Optional[asyncio.AbstractEventLoop] = None

    def request_api(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST to a plaso API path while enforcing QPS and headers."""

        self._enforce_api_rate_limit()
        url = urljoin(API_BASE, path.lstrip("/"))
        try:
            response = self._api_session.post(url, json=payload, timeout=self.timeout)
        except requests.RequestException as exc:  # pragma: no cover - network errors
            logging.error("HTTP POST to %s failed: %s", url, exc)
            raise

        if response.status_code in {401, 403}:
            logging.error("Authentication failed (status %s).", response.status_code)
            raise AuthenticationError("access-token 失效，请重新抓取后重试。")

        try:
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:  # pragma: no cover - network errors
            logging.error("API request to %s failed: %s", url, exc)
            raise

    def fetch_cdn_text(self, url: str) -> str:
        """Fetch a CDN resource as text (e.g., m3u8)."""

        try:
            response = self._cdn_session.get(url, timeout=self.timeout)
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:  # pragma: no cover - network errors
            logging.error("CDN text download failed: %s", exc)
            raise

    def download_cdn_file(self, url: str, dest_path: str) -> None:
        """Download a CDN file (PDF) to disk."""

        try:
            with self._cdn_session.get(url, stream=True, timeout=self.timeout) as resp:
                resp.raise_for_status()
                with open(dest_path, "wb") as file_obj:
                    for chunk in resp.iter_content(chunk_size=1 << 14):
                        if chunk:
                            file_obj.write(chunk)
        except requests.RequestException as exc:  # pragma: no cover - network errors
            logging.error("CDN file download failed from %s: %s", url, exc)
            raise

    async def download_cdn_stream(self, url: str, dest_path: str) -> None:
        """Asynchronously download a CDN file (TS segment)."""

        session = await self._get_cdn_async_session()
        async with session.get(url, headers=self._cdn_headers) as resp:
            if resp.status in {401, 403}:  # pragma: no cover - unexpected for CDN
                raise AuthenticationError("CDN 请求被拒绝，可能需要稍后重试。")
            resp.raise_for_status()
            with open(dest_path, "wb") as file_obj:
                async for chunk in resp.content.iter_chunked(1 << 14):
                    if chunk:
                        file_obj.write(chunk)

    def _enforce_api_rate_limit(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_api_call
        if elapsed < self._min_api_interval:
            time.sleep(self._min_api_interval - elapsed)
        self._last_api_call = time.monotonic()

        self._calls_since_pause += 1
        if self._calls_since_pause >= self._pause_after:
            time.sleep(random.uniform(0.1, 0.4))
            self._calls_since_pause = 0
            self._pause_after = random.randint(3, 5)

    async def _get_cdn_async_session(self) -> aiohttp.ClientSession:
        current_loop = asyncio.get_running_loop()
        if self._cdn_async_session:
            if (
                self._cdn_async_session.closed
                or not self._cdn_loop
                or self._cdn_loop.is_closed()
                or self._cdn_loop is not current_loop
            ):
                await self._shutdown_cdn_session()

        if self._cdn_async_lock is None:
            self._cdn_async_lock = asyncio.Lock()

        async with self._cdn_async_lock:
            if self._cdn_async_session and not self._cdn_async_session.closed:
                return self._cdn_async_session
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            connector = aiohttp.TCPConnector(limit=0)
            self._cdn_async_session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers=self._cdn_headers.copy(),
            )
            self._cdn_loop = current_loop
        return self._cdn_async_session

    async def _shutdown_cdn_session(self) -> None:
        if self._cdn_async_session:
            try:
                await self._cdn_async_session.close()
            except Exception:
                pass
        self._cdn_async_session = None
        self._cdn_loop = None

    def close(self) -> None:
        self._api_session.close()
        self._cdn_session.close()

        if self._cdn_async_session and not self._cdn_async_session.closed:
            try:
                asyncio.run(self._cdn_async_session.close())
            except RuntimeError:
                loop = asyncio.get_running_loop()
                loop.create_task(self._cdn_async_session.close())
        self._cdn_async_session = None
        self._cdn_loop = None

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
