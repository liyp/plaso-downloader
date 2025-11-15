"""API client responsible for fetching the course structure."""

from __future__ import annotations

import logging
from typing import List

from ..models import DayEntry
from ..utils.http_client import HttpClient

COURSE_PATH = "yxt/servlet/bigDir/getXfgTask"


class CourseAPI:
    """Wraps the course API and exposes typed helpers."""

    def __init__(self, http_client: HttpClient) -> None:
        self._client = http_client

    def get_days(self, course_id: str, group_id: int, xfile_id: str) -> List[DayEntry]:
        payload = {
            "id": course_id,
            "needProgress": True,
            "groupId": group_id,
            "xFileId": xfile_id,
            "needMyFav": True,
            "sourceWay": 1,
            "hiddenTask": 1,
        }
        try:
            data = self._client.request_api(COURSE_PATH, payload)
        except Exception as exc:  # pragma: no cover - network errors
            logging.error("Failed to fetch course days: %s", exc)
            raise

        day_entries = []
        for entry in data.get("obj", []) or []:
            day_id = entry.get("_id") or entry.get("id")
            name = entry.get("name") or "Unnamed Day"
            if not day_id:
                logging.debug("Skipping entry without id: %s", entry)
                continue
            entry_type = entry.get("type")
            has_children = bool(entry.get("dirs") or entry.get("files")) or entry_type == 0
            is_file_entry = not has_children and entry_type not in {0, None}
            day_entries.append(
                DayEntry(
                    id=str(day_id),
                    name=str(name),
                    entry_type=entry_type,
                    is_file_entry=is_file_entry,
                    raw_entry=entry,
                )
            )

        if not day_entries:
            logging.warning("No Day entries were returned for course %s", course_id)
        return day_entries
