"""Pydantic models that describe courses, lessons, and media artifacts."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class DayEntry(BaseModel):
    """Represents a Day node returned by the course structure API."""

    id: str
    name: str
    entry_type: Optional[int] = None
    is_file_entry: bool = False
    raw_entry: Optional[Dict[str, Any]] = None


class VideoResource(BaseModel):
    """Metadata for a single lesson video."""

    name: str
    m3u8_url: str
    location_path: Optional[str] = None
    location: Optional[str] = None
    file_id: Optional[str] = None
    requires_play_info: bool = False


class PDFResource(BaseModel):
    """Metadata for a PDF asset."""

    name: str
    download_url: str
    file_id: Optional[str] = None


class LessonResources(BaseModel):
    """Aggregated assets for a lesson day."""

    day: DayEntry
    videos: List[VideoResource]
    pdfs: List[PDFResource]


class M3U8Info(BaseModel):
    """Parsed metadata from an m3u8 playlist."""

    base_url: str
    ts_urls: List[str]
