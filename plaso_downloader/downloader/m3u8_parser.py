"""Tools for parsing m3u8 playlists into discrete TS URLs."""

from __future__ import annotations

import logging
from urllib.parse import urljoin

from ..models import M3U8Info
from ..utils.http_client import HttpClient


class M3U8Parser:
    """Fetches m3u8 manifests and extracts their TS files."""

    def __init__(self, http_client: HttpClient) -> None:
        self._http_client = http_client

    def parse(self, m3u8_url: str) -> M3U8Info:
        text = self._http_client.fetch_cdn_text(m3u8_url)
        ts_urls = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            ts_urls.append(urljoin(m3u8_url, line))

        base_url = m3u8_url.rsplit("/", 1)[0] + "/"
        if not ts_urls:
            logging.warning("m3u8 at %s did not contain TS segments", m3u8_url)
        return M3U8Info(base_url=base_url, ts_urls=ts_urls)
