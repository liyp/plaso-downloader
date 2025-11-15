"""Filesystem helpers for preparing output folders and safe filenames."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

INVALID_FILENAME_CHARS = re.compile(r"[\\/:*?\"<>|]")


def sanitize_filename(value: str, default: str = "file") -> str:
    """Removes characters that are invalid on most filesystems."""

    sanitized = INVALID_FILENAME_CHARS.sub("", value or "").strip()
    return sanitized or default


def ensure_directory(path: str) -> str:
    """Creates a directory (and its parents) if needed."""

    Path(path).mkdir(parents=True, exist_ok=True)
    return path


def cleanup_directory(path: str) -> None:
    """Deletes a directory tree if it exists."""

    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def build_day_directory(base_output: str, day_name: str) -> str:
    """Returns the folder where assets for a Day should be stored."""

    safe_name = sanitize_filename(day_name, default="Day")
    day_folder = os.path.join(base_output, safe_name)
    return ensure_directory(day_folder)


def build_tmp_segment_dir(output_file: str) -> str:
    """Creates a deterministic temporary folder next to ``output_file``."""

    parent = os.path.dirname(os.path.abspath(output_file)) or "."
    tmp_root = os.path.join(parent, "tmp_ts")
    ensure_directory(tmp_root)
    stem = Path(output_file).stem
    segment_dir = os.path.join(tmp_root, stem)
    return ensure_directory(segment_dir)


def build_package_directory(base_output: str, group_name: str, package_title: str) -> str:
    """Returns the directory path for storing an entire package under a group."""

    group_folder = ensure_directory(os.path.join(base_output, sanitize_filename(group_name, default="group")))
    package_folder = os.path.join(group_folder, sanitize_filename(package_title, default="package"))
    return ensure_directory(package_folder)
