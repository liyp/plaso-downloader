"""API client for fetching packages (course bundles) under a group."""

from __future__ import annotations

import logging
from typing import List

from ..models import CoursePackageInfo
from ..utils.http_client import HttpClient

PACKAGE_PATH = "course/api/v1/m/package/list"


class PackageAPI:
    """Fetches course packages for a specific group."""

    def __init__(self, http_client: HttpClient) -> None:
        self._client = http_client

    def get_packages(self, group_id: int, search: str = "") -> List[CoursePackageInfo]:
        payload = {
            "groupId": group_id,
            "search": search,
        }
        try:
            data = self._client.request_api(PACKAGE_PATH, payload)
        except Exception as exc:  # pragma: no cover - network errors
            logging.error("Failed to fetch packages for group %s: %s", group_id, exc)
            raise

        packages = []
        for item in data.get("obj", []):
            xfile = item.get("xFile") or {}
            dir_id = xfile.get("dirId") or xfile.get("fileCommon", {}).get("_id")
            xfile_id = xfile.get("_id") or item.get("originId")
            title = item.get("title") or xfile.get("fileCommon", {}).get("name")
            if not dir_id or not xfile_id or not title:
                logging.debug("Skipping package entry lacking ids: %s", item)
                continue
            pkg_id = str(item.get("id") or xfile_id)
            packages.append(
                CoursePackageInfo(
                    id=pkg_id,
                    title=str(title),
                    group_id=group_id,
                    xfile_id=str(xfile_id),
                    dir_id=str(dir_id),
                    task_num=int(item.get("taskNum") or 0),
                    cover=item.get("cover") or xfile.get("coverImg"),
                    progress_rate=item.get("progressRate"),
                )
            )
        return packages
