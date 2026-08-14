"""
Self-service registration rules — pure logic, no Flask, no DB.

Kept separate from ``routes.py`` so every rule that decides *who may create an
account* is unit-testable without a request context or a SQL Server. The route
is a thin shell around these functions; it never re-implements a rule here.

Two gates:
  • ``check_email``  — format + allowed domain + length that fits the DB column.
  • ``check_rate``   — a per-IP throttle, because ``POST /api/auth/register`` is
                       the only unauthenticated endpoint that WRITES a row.
"""

from __future__ import annotations

import re
import threading
import time
from typing import List, Optional, Tuple

from . import config as ac

# Deliberately conservative: local part of the usual mail characters, then a
# dotted domain. The domain allow-list below is the real gate — this only stops
# obvious garbage before it reaches the database.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def normalize_email(raw: Optional[str]) -> str:
    """Trim + lower-case. Registration is case-insensitive ("พิมพ์เล็กใหญ่ก็ได้"),
    so one canonical form is stored — otherwise ``A@x.com`` and ``a@x.com``
    would become two accounts on a case-sensitive DB collation."""
    return (raw or "").strip().lower()


def email_domain(email: str) -> str:
    """Domain part of an already-normalised address ("" when malformed)."""
    _, sep, domain = email.rpartition("@")
    return domain if sep else ""


def domains_label(domains=None) -> str:
    """Human list for UI/error text, e.g. "@thaiunion.com"."""
    doms = list(domains if domains is not None else ac.REGISTER_EMAIL_DOMAINS)
    return " หรือ ".join("@" + d for d in doms) if doms else ""


def check_email(raw: Optional[str], domains=None) -> Tuple[bool, str, str]:
    """
    Validate a sign-up address.

    Returns ``(ok, normalized_email, error_message)``. ``error_message`` is
    Thai and safe to show the user as-is.
    """
    email = normalize_email(raw)
    if not email:
        return False, "", "กรุณากรอกอีเมล"
    if not _EMAIL_RE.match(email):
        return False, email, "รูปแบบอีเมลไม่ถูกต้อง"

    allowed = [d.lower() for d in
               (domains if domains is not None else ac.REGISTER_EMAIL_DOMAINS)]
    # Exact domain match only — a look-alike such as "x@evil-thaiunion.com" or a
    # subdomain must NOT pass a rule the user stated as "@thaiunion.com เท่านั้น".
    if allowed and email_domain(email) not in allowed:
        return False, email, (f"อนุญาตเฉพาะอีเมล {domains_label(allowed)} เท่านั้น")

    # The username column is NVARCHAR(64) and username == email here, so a
    # longer address would be truncated (or error) inside SQL Server.
    if len(email) > ac.USERNAME_MAX_LEN:
        return False, email, (f"อีเมลยาวเกิน {ac.USERNAME_MAX_LEN} ตัวอักษร")
    return True, email, ""


# ── Per-IP throttle ───────────────────────────────────────────────────
# In-memory and per-process: the station runs a single Flask process, so this
# is enough to stop a script hammering the endpoint. It is a rate limit, not a
# security boundary — the domain allow-list is.

_hits: dict = {}
_hits_lock = threading.Lock()
_WINDOW_SEC = 3600.0


def check_rate(ip: str, now: Optional[float] = None,
               limit: Optional[int] = None) -> Tuple[bool, int]:
    """
    Record an attempt from ``ip`` and report whether it is allowed.

    Returns ``(allowed, retry_after_seconds)``. ``limit <= 0`` disables the
    throttle entirely (``AUTH_REGISTER_MAX_PER_IP_HOUR=0``).
    """
    cap = ac.REGISTER_MAX_PER_IP_HOUR if limit is None else limit
    if cap <= 0:
        return True, 0
    t = time.time() if now is None else now
    key = ip or "-"
    with _hits_lock:
        recent: List[float] = [ts for ts in _hits.get(key, []) if t - ts < _WINDOW_SEC]
        if len(recent) >= cap:
            _hits[key] = recent
            retry = int(_WINDOW_SEC - (t - recent[0])) + 1
            return False, max(retry, 1)
        recent.append(t)
        _hits[key] = recent
        # Opportunistic cleanup so a long-running station does not accumulate
        # one list per IP that ever registered.
        if len(_hits) > 512:
            for k in [k for k, v in _hits.items()
                      if not v or t - v[-1] >= _WINDOW_SEC]:
                _hits.pop(k, None)
    return True, 0


def reset_rate() -> None:
    """Clear the throttle state (tests / manual recovery)."""
    with _hits_lock:
        _hits.clear()
