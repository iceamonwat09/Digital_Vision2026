"""
JWT helpers: short-lived access tokens + longer-lived refresh tokens.

Access tokens carry the user's permissions so the guard can authorise without a
DB hit on every request. Refresh tokens carry only the user id — permissions
are re-loaded from the DB when a new access token is minted, so a permission
change takes effect within one access-token lifetime.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from . import config as ac


def _now() -> datetime:
    return datetime.now(timezone.utc)


def user_claims(user: dict, perms: list) -> dict:
    """Identity payload shared by the access token and Flask's request context.

    ``sub`` is stored as a string: PyJWT >= 2.10 enforces RFC 7519 and rejects a
    non-string ``sub`` on decode (InvalidSubjectError). Readers coerce back with
    ``int()`` (store.get_user_by_id already does).
    """
    return {
        "sub": str(user["user_id"]),
        "username": user["username"],
        "role": user.get("role_name", ""),
        "perms": list(perms),
    }


def make_access(claims: dict) -> str:
    payload = {
        **claims,
        "type": "access",
        "iat": _now(),
        "exp": _now() + timedelta(minutes=ac.ACCESS_TTL_MIN),
    }
    return jwt.encode(payload, ac.JWT_SECRET, algorithm=ac.JWT_ALG)


def make_refresh(user: dict) -> str:
    payload = {
        "sub": str(user["user_id"]),  # string sub — see user_claims()
        "type": "refresh",
        "iat": _now(),
        "exp": _now() + timedelta(days=ac.REFRESH_TTL_DAYS),
    }
    return jwt.encode(payload, ac.JWT_SECRET, algorithm=ac.JWT_ALG)


def decode(token: str, expected_type: str) -> Optional[dict]:
    """Decode + verify signature/expiry/type. Returns claims or None."""
    if not token:
        return None
    try:
        data = jwt.decode(token, ac.JWT_SECRET, algorithms=[ac.JWT_ALG])
    except jwt.PyJWTError:
        return None
    if data.get("type") != expected_type:
        return None
    return data
