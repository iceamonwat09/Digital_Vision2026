"""
Password hashing (bcrypt) + policy validation.

The same policy runs on the backend (security — never trust the client) and is
mirrored by static/js/login.js for live UX feedback. NEVER store plain text:
``hash_password`` salts automatically via bcrypt's gensalt().
"""

from __future__ import annotations

import re
from typing import List, Tuple

from . import config as ac

try:
    import bcrypt
    _BCRYPT_OK = True
except ImportError:  # pragma: no cover - dependency guard
    _BCRYPT_OK = False


def hashing_available() -> bool:
    return _BCRYPT_OK


# ── Hashing ───────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    """bcrypt hash (salt embedded). Raises if bcrypt is not installed."""
    if not _BCRYPT_OK:
        raise RuntimeError("bcrypt ไม่ได้ติดตั้ง — `pip install bcrypt`")
    # bcrypt caps input at 72 bytes; encode then hash.
    digest = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt())
    return digest.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time compare via bcrypt.checkpw. False on any error."""
    if not _BCRYPT_OK or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ── Policy ────────────────────────────────────────────────────────────

_SPECIAL_RE = re.compile("[" + re.escape(ac.PASSWORD_SPECIALS) + "]")


def validate_password(pw: str) -> Tuple[bool, List[str]]:
    """
    Return (ok, errors). Rules (all required):
      • length >= PASSWORD_MIN_LEN (and <= PASSWORD_MAX_LEN)
      • >=1 uppercase, >=1 lowercase, >=1 digit, >=1 special (!@#$%^&*)
    """
    errors: List[str] = []
    if len(pw) < ac.PASSWORD_MIN_LEN:
        errors.append(f"ต้องยาวอย่างน้อย {ac.PASSWORD_MIN_LEN} ตัวอักษร")
    if len(pw) > ac.PASSWORD_MAX_LEN:
        errors.append(f"ยาวเกิน {ac.PASSWORD_MAX_LEN} ตัวอักษร")
    if not re.search(r"[A-Z]", pw):
        errors.append("ต้องมีตัวพิมพ์ใหญ่ (A-Z) อย่างน้อย 1 ตัว")
    if not re.search(r"[a-z]", pw):
        errors.append("ต้องมีตัวพิมพ์เล็ก (a-z) อย่างน้อย 1 ตัว")
    if not re.search(r"[0-9]", pw):
        errors.append("ต้องมีตัวเลข (0-9) อย่างน้อย 1 ตัว")
    if not _SPECIAL_RE.search(pw):
        errors.append(f"ต้องมีอักขระพิเศษ ({ac.PASSWORD_SPECIALS}) อย่างน้อย 1 ตัว")
    return (not errors, errors)


def policy_dict() -> dict:
    """Policy descriptor sent to the frontend (GET /api/auth/policy)."""
    return {
        "min_len": ac.PASSWORD_MIN_LEN,
        "max_len": ac.PASSWORD_MAX_LEN,
        "specials": ac.PASSWORD_SPECIALS,
        "require": ["upper", "lower", "digit", "special"],
    }
