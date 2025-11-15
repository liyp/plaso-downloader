"""Simple helpers for persisting access tokens locally."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_CACHE_PATH = os.path.join(PROJECT_ROOT, ".cache", "token.json")


def load_cached_token(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        token = data.get("access_token")
        if token:
            logging.info("Using cached access token from %s", path)
        return token
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as exc:  # pragma: no cover - io errors
        logging.warning("Failed to read token cache %s: %s", path, exc)
        return None


def save_cached_token(path: Optional[str], token: str) -> None:
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"access_token": token, "timestamp": time.time()}, f)
        logging.debug("Saved access token cache to %s", path)
    except OSError as exc:  # pragma: no cover - io errors
        logging.warning("Unable to write token cache %s: %s", path, exc)


def clear_cached_token(path: Optional[str]) -> None:
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
            logging.info("Cleared cached token %s", path)
    except OSError as exc:  # pragma: no cover
        logging.warning("Failed to remove token cache %s: %s", path, exc)
