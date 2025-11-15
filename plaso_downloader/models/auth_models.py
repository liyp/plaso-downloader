"""Models related to authentication and login responses."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel


class LoginResult(BaseModel):
    """Simplified view of the doLogin API response."""

    access_token: str
    login_name: str
    user_id: Optional[int] = None
    org_id: Optional[int] = None
    raw: Dict[str, Any]
