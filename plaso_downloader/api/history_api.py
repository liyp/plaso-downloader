"""API client to fetch historical liveclass recordings."""

from __future__ import annotations

import logging
from typing import Dict, List

from ..utils.http_client import HttpClient

HISTORY_PATH = "liveclassgo/api/v1/history/listRecord"


class HistoryAPI:
    def __init__(self, http_client: HttpClient) -> None:
        self._client = http_client

    def list_records(self, date_from: int, date_to: int, page_size: int = 50) -> List[Dict]:
        records: List[Dict] = []
        index_start = 0
        while True:
            payload = {
                "dateFrom": date_from,
                "dateTo": date_to,
                "indexStart": index_start,
                "pageSize": page_size,
            }
            try:
                data = self._client.request_api(HISTORY_PATH, payload)
            except Exception as exc:  # pragma: no cover - network errors
                logging.error("History list request failed: %s", exc)
                raise
            obj = data.get("obj") or {}
            batch = obj.get("list") or []
            records.extend(batch)
            if len(records) >= obj.get("total", 0) or not batch:
                break
            index_start += len(batch)
        return records
