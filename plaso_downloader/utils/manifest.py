"""Simple JSON-backed manifest to skip already downloaded resources."""

from __future__ import annotations

import json
import os
from typing import Dict, Optional


class DownloadManifest:
    def __init__(self, path: str) -> None:
        self.path = path
        self._data: Dict[str, str] = {}
        self.load()

    def load(self) -> None:
        if not os.path.exists(self.path):
            self._data = {}
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            self._data = payload.get("files", {})
        except (json.JSONDecodeError, OSError):  # pragma: no cover - corrupt manifest
            self._data = {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"files": self._data}, handle, ensure_ascii=False, indent=2)

    def is_downloaded(self, file_key: str, target_path: str) -> bool:
        saved_path = self._data.get(file_key)
        if saved_path and os.path.exists(saved_path):
            return True
        if saved_path and os.path.exists(target_path):
            return True
        return False

    def mark_downloaded(self, file_key: str, path: str) -> None:
        self._data[file_key] = path
        self.save()
