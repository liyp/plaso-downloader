"""Models describing course groups and packages."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class GroupInfo(BaseModel):
    """Represents a plaso course group that a user can access."""

    id: int
    name: str
    org_id: Optional[int] = None


class CoursePackageInfo(BaseModel):
    """Metadata for a package/course bundle under a group."""

    id: str
    title: str
    group_id: int
    xfile_id: str
    dir_id: str
    task_num: int
    cover: Optional[str] = None
    progress_rate: Optional[int] = None
