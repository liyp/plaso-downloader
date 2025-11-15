"""API client for fetching file metadata such as pdfLocation."""

from __future__ import annotations

import logging

from ..utils.http_client import HttpClient

FILE_INFO_PATH = "yxt/servlet/file/getfileinfo"
PLAY_INFO_PATH = "yxt/servlet/ali/getPlayInfo"


class FileAPI:
    """Retrieves extra metadata for files (PDF pages, etc.)."""

    def __init__(self, http_client: HttpClient) -> None:
        self._client = http_client

    def get_file_info(self, file_id: str) -> dict:
        payload = {"fileId": file_id, "checkResource": True}
        try:
            data = self._client.request_api(FILE_INFO_PATH, payload)
            return data.get("obj") or data.get("file") or {}
        except Exception as exc:  # pragma: no cover - network errors
            logging.error("Failed to fetch file info for %s: %s", file_id, exc)
            raise

    def get_play_info(self, file_id: str) -> dict:
        payload = {"id": file_id, "fileId": file_id}
        try:
            data = self._client.request_api(PLAY_INFO_PATH, payload)
            return data.get("obj") or {}
        except Exception as exc:  # pragma: no cover - network errors
            logging.error("Failed to fetch play info for %s: %s", file_id, exc)
            raise
