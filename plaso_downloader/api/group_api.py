"""API client responsible for fetching user groups."""

from __future__ import annotations

import logging
from typing import List

from ..models import GroupInfo
from ..utils.http_client import HttpClient

GROUP_PATH = "gt/servlet/group/getGroupsByActive"


class GroupAPI:
    """Provides helpers for listing the groups tied to the active account."""

    def __init__(self, http_client: HttpClient) -> None:
        self._client = http_client

    def get_groups(self, year: int = 0, active: int = 1, page_size: int = 100, page_num: int = 0) -> List[GroupInfo]:
        payload = {
            "year": year,
            "active": active,
            "pageSize": page_size,
            "pageNum": page_num,
            "sortKey": 2,
            "sortType": 1,
        }
        try:
            data = self._client.request_api(GROUP_PATH, payload)
        except Exception as exc:  # pragma: no cover - network errors
            logging.error("Failed to fetch group list: %s", exc)
            raise

        groups = []
        for item in (data.get("obj") or {}).get("list", []):
            group_id = item.get("id")
            name = item.get("groupName")
            if not group_id or not name:
                continue
            groups.append(GroupInfo(id=int(group_id), name=str(name), org_id=item.get("orgId")))
        return groups
