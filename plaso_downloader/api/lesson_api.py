"""API client for retrieving assets that belong to a given Day."""

from __future__ import annotations

import logging
from typing import List

from ..models import DayEntry, LessonResources, PDFResource, VideoResource
from ..utils.http_client import HttpClient

CONTENT_PATH = "yxt/servlet/bigDir/getAllContent"
FILE_CDN_BASE = "https://filecdn.plaso.com"


class LessonAPI:
    """Fetches video and PDF resources for an individual lesson Day."""

    def __init__(self, http_client: HttpClient) -> None:
        self._client = http_client

    def get_lesson_resources(self, day: DayEntry, group_id: int, xfile_id: str) -> LessonResources:
        entries = self._resolve_day_entries(day, group_id, xfile_id)
        videos: List[VideoResource] = []
        pdfs: List[PDFResource] = []

        for entry in entries:
            record_files = entry.get("recordFiles") or []
            base_name = entry.get("name") or day.name
            entry_type = entry.get("type")
            entry_name = entry.get("name") or ""
            if record_files:
                for idx, record in enumerate(record_files, start=1):
                    location_path = record.get("locationPath") or entry.get("locationPath")
                    location = record.get("location")
                    if not location_path or not location:
                        logging.debug("Skipping record without location: %s", record)
                        continue
                    m3u8_url = f"{FILE_CDN_BASE}/{location_path}/{location}/a1/a.m3u8"
                    video_name = f"{base_name}_video_{idx}"
                    videos.append(
                        VideoResource(
                            name=video_name,
                            m3u8_url=m3u8_url,
                            location_path=location_path,
                            location=location,
                            file_id=record.get("_id") or record.get("myid") or entry.get("_id") or entry.get("myid"),
                        )
                    )

            if entry_type == 1 or entry_name.lower().endswith(".pdf"):
                pdf_location_path = entry.get("locationPath")
                pdf_location = entry.get("location")
                download_url = ""
                if pdf_location_path and pdf_location:
                    download_url = f"{FILE_CDN_BASE}/{pdf_location_path}/{pdf_location}"
                pdf_name = entry_name or f"{day.name}_pdf"
                pdfs.append(
                    PDFResource(
                        name=pdf_name,
                        download_url=download_url,
                        file_id=entry.get("_id") or entry.get("myid"),
                    )
                )
            elif not record_files and entry_type in {7, 20}:
                file_id = entry.get("_id") or entry.get("myid")
                if file_id:
                    video_name = entry_name or base_name
                    requires_play_info = entry_type == 20 or (entry.get("locationPath") == "ossvideo")
                    videos.append(
                        VideoResource(
                            name=video_name,
                            m3u8_url="",
                            file_id=file_id,
                            requires_play_info=requires_play_info,
                            location_path=entry.get("locationPath"),
                            location=entry.get("location"),
                        )
                    )

        return LessonResources(day=day, videos=videos, pdfs=pdfs)

    def list_files(self, day: DayEntry, group_id: int, xfile_id: str) -> List[dict]:
        """Returns the raw file entries inside a task/day."""

        return self._resolve_day_entries(day, group_id, xfile_id)

    def _fetch_lesson_entries(self, dir_id: str, group_id: int, xfile_id: str) -> List[dict]:
        payload = {
            "needDirConfig": True,
            "needMyFav": True,
            "sourceWay": 1,
            "xFileGroupId": xfile_id,
            "groupId": group_id,
            "dirId": dir_id,
            "needProgress": True,
            "hiddenTask": 1,
        }
        try:
            data = self._client.request_api(CONTENT_PATH, payload)
        except Exception as exc:  # pragma: no cover - network errors
            logging.error("Failed to fetch lesson %s resources: %s", dir_id, exc)
            raise
        return data.get("obj", []) or []

    def _resolve_day_entries(self, day: DayEntry, group_id: int, xfile_id: str) -> List[dict]:
        if day.is_file_entry and day.raw_entry:
            return [day.raw_entry]
        return self._fetch_lesson_entries(day.id, group_id, xfile_id)
