"""
Configuration for the auth + RBAC layer.

Everything is overridable via environment variables so a deployment can be
tuned without editing code. The JWT signing secret is loaded from the
environment, or persisted to ``data/auth/secret.key`` on first run so issued
tokens survive an app restart on a single station.
"""

from __future__ import annotations

import os
import secrets

# Reuse the app's SQL Server connection details (defined in the root config.py).
import config as app_config  # noqa: F401  (re-exported indirectly via store.py)

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(os.path.dirname(_HERE), "data", "auth")
os.makedirs(_DATA_DIR, exist_ok=True)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


# ── Master switch ─────────────────────────────────────────────────────
# When false the guard is a no-op: every route is open and every permission
# check passes. Lets the station run before the DB tables / admin user exist,
# and gives an instant rollback path.
AUTH_ENABLED = _env_bool("AUTH_ENABLED", True)

# ── Token lifetimes ───────────────────────────────────────────────────
ACCESS_TTL_MIN = int(os.getenv("AUTH_ACCESS_TTL_MIN", "60"))        # 1 hour
REFRESH_TTL_DAYS = int(os.getenv("AUTH_REFRESH_TTL_DAYS", "7"))     # 1 week

# ── Cookies (httpOnly — recommended over localStorage) ────────────────
COOKIE_ACCESS = os.getenv("AUTH_COOKIE_ACCESS", "vq_access")
COOKIE_REFRESH = os.getenv("AUTH_COOKIE_REFRESH", "vq_refresh")
# Secure=False by default because the station typically runs over HTTP on the
# plant intranet. Set AUTH_COOKIE_SECURE=1 when serving over HTTPS.
COOKIE_SECURE = _env_bool("AUTH_COOKIE_SECURE", False)
COOKIE_SAMESITE = os.getenv("AUTH_COOKIE_SAMESITE", "Lax")

# ── Self-service registration (ปุ่ม "ลงทะเบียน" บนหน้า /login) ─────────
# New accounts always land on REGISTER_ROLE — the sign-up form has no role
# selector, and the backend ignores any role sent by the client. What that role
# may actually open is decided by its permissions in the DB (แก้ได้จากหน้า
# "จัดการผู้ใช้") — registration never grants a permission by itself.
# Kill switch: AUTH_REGISTER_ENABLED=0 hides the button AND rejects the API.
REGISTER_ENABLED = _env_bool("AUTH_REGISTER_ENABLED", True)
REGISTER_ROLE = os.getenv("AUTH_REGISTER_ROLE", "Viewer").strip() or "Viewer"
# Only company addresses may self-register (comma separated; empty = any).
REGISTER_EMAIL_DOMAINS = tuple(
    d.strip().lower()
    for d in os.getenv("AUTH_REGISTER_EMAIL_DOMAINS", "thaiunion.com").split(",")
    if d.strip()
)
# Per-IP sign-ups allowed per hour (0 = unlimited).
REGISTER_MAX_PER_IP_HOUR = int(os.getenv("AUTH_REGISTER_MAX_PER_IP_HOUR", "5"))
# Mirrors AuthUsers.Username NVARCHAR(64) in Connection_sql/auth_schema.sql.
USERNAME_MAX_LEN = 64

# ── Account lockout (temporary) ───────────────────────────────────────
MAX_FAILED = int(os.getenv("AUTH_MAX_FAILED", "5"))
LOCK_MINUTES = int(os.getenv("AUTH_LOCK_MINUTES", "15"))

# ── Password policy (enforced on BOTH frontend and backend) ───────────
PASSWORD_MIN_LEN = int(os.getenv("AUTH_PASSWORD_MIN_LEN", "8"))
PASSWORD_MAX_LEN = int(os.getenv("AUTH_PASSWORD_MAX_LEN", "128"))
PASSWORD_SPECIALS = "!@#$%^&*"

# ── Permission catalogue (mapped to this app's real features) ─────────
# key → human label (Thai). This is the single source of truth; the DB seed
# (Connection_sql/auth_schema.sql) mirrors these keys.
PERMISSIONS = {
    "view_dashboard":      "ดูแดชบอร์ด",
    "run_live_detection":  "ตรวจจับสด + ถ่ายรูปตรวจ (กล้อง/โมเดล)",
    "inspect_label_paper": "ตรวจฉลากกระดาษ (ΔE2000)",
    "inspect_artwork":     "ตรวจ Artwork (OCR + 4 ชั้น)",
    "view_history":        "ดูประวัติการตรวจ",
    "manage_users":        "จัดการผู้ใช้และสิทธิ์",
}

_ALL = list(PERMISSIONS.keys())

# Role → permission keys. Seeded into the DB by auth_schema.sql; kept here so
# the seed script can reuse it.
ROLES = {
    "Admin":   list(_ALL),
    "Manager": ["view_dashboard", "run_live_detection",
                "inspect_label_paper", "inspect_artwork", "view_history"],
    "Staff":   ["view_dashboard", "run_live_detection",
                "inspect_label_paper", "inspect_artwork"],
    "Viewer":  ["view_dashboard", "view_history"],
}


def _load_or_create_secret() -> str:
    env = os.getenv("AUTH_JWT_SECRET", "").strip()
    if env:
        return env
    path = os.path.join(_DATA_DIR, "secret.key")
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                val = f.read().strip()
            if val:
                return val
        val = secrets.token_urlsafe(48)
        with open(path, "w", encoding="utf-8") as f:
            f.write(val)
        os.chmod(path, 0o600)
        return val
    except Exception:
        # Last resort: ephemeral secret (tokens won't survive a restart).
        return secrets.token_urlsafe(48)


JWT_SECRET = _load_or_create_secret()
JWT_ALG = "HS256"
