"""
Data access for the auth layer — SQL Server via pyodbc, parameterized queries
only (no string concatenation → no SQL injection). A short-lived connection is
opened per call so the layer is safe under Flask's threaded server.

Connection details are reused from the root ``config.py`` (the same VisionIQ
database the inspection history already uses).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

import config as app_config

logger = logging.getLogger(__name__)

try:
    import pyodbc
    _PYODBC_OK = True
except ImportError:  # pragma: no cover
    _PYODBC_OK = False


def db_available() -> bool:
    return _PYODBC_OK


def _connect():
    """Open a fresh SQL Server connection (mirrors database.py's auth style)."""
    if not _PYODBC_OK:
        raise RuntimeError("pyodbc ไม่ได้ติดตั้ง")
    cs = (
        f"DRIVER={{SQL Server}};"
        f"SERVER={getattr(app_config, 'SQL_SERVER', 'localhost')};"
        f"DATABASE={getattr(app_config, 'SQL_DATABASE', 'VisionIQ')};"
        f"UID={getattr(app_config, 'SQL_USER', 'sa')};"
        f"PWD={getattr(app_config, 'SQL_PASSWORD', '')};"
    )
    return pyodbc.connect(cs, timeout=5)


def _row_to_user(row, cols) -> dict:
    d = dict(zip(cols, row))
    return {
        "user_id": d["UserId"],
        "username": d["Username"],
        "email": d.get("Email"),
        "password_hash": d["PasswordHash"],
        "role_id": d["RoleId"],
        "role_name": d.get("RoleName", ""),
        "is_active": bool(d["IsActive"]),
        "failed_attempts": int(d.get("FailedAttempts") or 0),
        "locked_until": d.get("LockedUntil"),
    }


_USER_SELECT = (
    "SELECT u.UserId, u.Username, u.Email, u.PasswordHash, u.RoleId, "
    "r.RoleName, u.IsActive, u.FailedAttempts, u.LockedUntil "
    "FROM AuthUsers u JOIN AuthRoles r ON r.RoleId = u.RoleId "
)


def get_user_by_login(login: str) -> Optional[dict]:
    """Look up by username OR email (case-insensitive via the DB collation)."""
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(_USER_SELECT + "WHERE u.Username = ? OR u.Email = ?",
                        login, login)
            row = cur.fetchone()
            if not row:
                return None
            cols = [c[0] for c in cur.description]
            return _row_to_user(row, cols)
    except Exception as e:
        logger.error("get_user_by_login failed: %s", e)
        return None


def get_user_by_id(user_id: int) -> Optional[dict]:
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(_USER_SELECT + "WHERE u.UserId = ?", int(user_id))
            row = cur.fetchone()
            if not row:
                return None
            cols = [c[0] for c in cur.description]
            return _row_to_user(row, cols)
    except Exception as e:
        logger.error("get_user_by_id failed: %s", e)
        return None


def get_permissions(role_id: int) -> List[str]:
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT p.PermissionKey FROM AuthRolePermissions rp "
                "JOIN AuthPermissions p ON p.PermissionId = rp.PermissionId "
                "WHERE rp.RoleId = ?",
                int(role_id),
            )
            return [r[0] for r in cur.fetchall()]
    except Exception as e:
        logger.error("get_permissions failed: %s", e)
        return []


def register_failure(user_id: int, max_failed: int, lock_minutes: int) -> None:
    """Increment failed attempts; lock the account when the threshold is hit."""
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE AuthUsers SET FailedAttempts = FailedAttempts + 1, "
                "LockedUntil = CASE WHEN FailedAttempts + 1 >= ? "
                "  THEN DATEADD(MINUTE, ?, SYSUTCDATETIME()) ELSE LockedUntil END "
                "WHERE UserId = ?",
                int(max_failed), int(lock_minutes), int(user_id),
            )
            conn.commit()
    except Exception as e:
        logger.error("register_failure failed: %s", e)


def register_success(user_id: int) -> None:
    """Reset lockout counters and stamp last login on a successful sign-in."""
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE AuthUsers SET FailedAttempts = 0, LockedUntil = NULL, "
                "LastLoginAt = SYSUTCDATETIME() WHERE UserId = ?",
                int(user_id),
            )
            conn.commit()
    except Exception as e:
        logger.error("register_success failed: %s", e)


def record_login_attempt(username: str, user_id, success: bool,
                         ip: str, user_agent: str, reason: str) -> None:
    """Append to the login audit trail (best-effort — never blocks login)."""
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO AuthLoginAudit "
                "(Username, UserId, Success, Ip, UserAgent, Reason) "
                "VALUES (?,?,?,?,?,?)",
                (username or "")[:64], user_id, 1 if success else 0,
                (ip or "")[:64], (user_agent or "")[:400], (reason or "")[:200],
            )
            conn.commit()
    except Exception as e:
        logger.error("record_login_attempt failed: %s", e)


def is_locked(user: dict) -> bool:
    lu = user.get("locked_until")
    if not lu:
        return False
    if isinstance(lu, datetime):
        # DB stores UTC (SYSUTCDATETIME); compare naive-UTC to naive-UTC.
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        ref = lu.replace(tzinfo=None) if lu.tzinfo else lu
        return ref > now
    return False


# ── User management (manage_users) ────────────────────────────────────

def get_role_id(role_name: str) -> Optional[int]:
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT RoleId FROM AuthRoles WHERE RoleName = ?",
                        role_name)
            row = cur.fetchone()
            return int(row[0]) if row else None
    except Exception as e:
        logger.error("get_role_id failed: %s", e)
        return None


def list_users() -> List[dict]:
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT u.UserId, u.Username, u.Email, r.RoleName, u.IsActive, "
                "u.LastLoginAt, u.LockedUntil FROM AuthUsers u "
                "JOIN AuthRoles r ON r.RoleId = u.RoleId ORDER BY u.Username"
            )
            cols = [c[0] for c in cur.description]
            out = []
            for row in cur.fetchall():
                d = dict(zip(cols, row))
                out.append({
                    "user_id": d["UserId"],
                    "username": d["Username"],
                    "email": d.get("Email"),
                    "role": d.get("RoleName"),
                    "is_active": bool(d["IsActive"]),
                    "last_login_at": (d["LastLoginAt"].isoformat()
                                      if d.get("LastLoginAt") else None),
                    "locked": is_locked({"locked_until": d.get("LockedUntil")}),
                })
            return out
    except Exception as e:
        logger.error("list_users failed: %s", e)
        return []


def create_user(username: str, email: Optional[str], password_hash: str,
                role_id: int) -> int:
    """Insert a new user. Returns new UserId. Raises on duplicate / DB error."""
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO AuthUsers (Username, Email, PasswordHash, RoleId) "
            "OUTPUT INSERTED.UserId VALUES (?,?,?,?)",
            username, email, password_hash, int(role_id),
        )
        row = cur.fetchone()
        conn.commit()
        return int(row[0])


def upsert_user(username: str, email: Optional[str], password_hash: str,
                role_id: int) -> int:
    """Create the user, or update password/role if the username already exists.
    Used by the admin-seed CLI so re-running it is safe."""
    existing = get_user_by_login(username)
    if existing is None:
        return create_user(username, email, password_hash, role_id)
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE AuthUsers SET Email = ?, PasswordHash = ?, RoleId = ?, "
            "IsActive = 1, FailedAttempts = 0, LockedUntil = NULL "
            "WHERE UserId = ?",
            email, password_hash, int(role_id), int(existing["user_id"]),
        )
        conn.commit()
        return int(existing["user_id"])


def set_user_role(username: str, role_id: int) -> bool:
    """Reassign an account to a different role. Returns True if a row changed."""
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE AuthUsers SET RoleId = ? WHERE Username = ?",
                    int(role_id), username)
        conn.commit()
        return cur.rowcount > 0


def set_user_active(username: str, active: bool) -> bool:
    """Enable/disable an account (disabled accounts cannot log in)."""
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE AuthUsers SET IsActive = ? WHERE Username = ?",
                    1 if active else 0, username)
        conn.commit()
        return cur.rowcount > 0


# ── Role / permission management (manage_users) ───────────────────────

def list_permissions() -> List[dict]:
    """All permission keys + their human label (drives the UI checkboxes)."""
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT PermissionKey, Description FROM AuthPermissions "
                        "ORDER BY PermissionId")
            return [{"key": r[0], "label": r[1] or r[0]} for r in cur.fetchall()]
    except Exception as e:
        logger.error("list_permissions failed: %s", e)
        return []


def list_roles_with_perms() -> List[dict]:
    """Every role with its granted permission keys and how many users hold it."""
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT r.RoleId, r.RoleName, r.Description, "
                "(SELECT COUNT(*) FROM AuthUsers u WHERE u.RoleId = r.RoleId) "
                "FROM AuthRoles r ORDER BY r.RoleName"
            )
            roles = []
            by_id = {}
            for rid, name, desc, ucount in cur.fetchall():
                row = {"role_id": int(rid), "name": name,
                       "description": desc or "", "user_count": int(ucount),
                       "permissions": []}
                roles.append(row)
                by_id[int(rid)] = row
            cur.execute(
                "SELECT rp.RoleId, p.PermissionKey FROM AuthRolePermissions rp "
                "JOIN AuthPermissions p ON p.PermissionId = rp.PermissionId"
            )
            for rid, key in cur.fetchall():
                if int(rid) in by_id:
                    by_id[int(rid)]["permissions"].append(key)
            return roles
    except Exception as e:
        logger.error("list_roles_with_perms failed: %s", e)
        return []


def role_names() -> List[str]:
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT RoleName FROM AuthRoles ORDER BY RoleName")
            return [r[0] for r in cur.fetchall()]
    except Exception as e:
        logger.error("role_names failed: %s", e)
        return []


def create_role(name: str, description: str = "") -> int:
    """Create a role (no permissions yet). Raises on duplicate."""
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO AuthRoles (RoleName, Description) "
            "OUTPUT INSERTED.RoleId VALUES (?, ?)",
            name, description or None,
        )
        rid = int(cur.fetchone()[0])
        conn.commit()
        return rid


def set_role_permissions(role_id: int, perm_keys: List[str],
                         description: Optional[str] = None) -> None:
    """Replace a role's permission set (+ optional description) atomically."""
    with _connect() as conn:
        cur = conn.cursor()
        if description is not None:
            cur.execute("UPDATE AuthRoles SET Description = ? WHERE RoleId = ?",
                        description or None, int(role_id))
        cur.execute("DELETE FROM AuthRolePermissions WHERE RoleId = ?",
                    int(role_id))
        if perm_keys:
            placeholders = ",".join("?" for _ in perm_keys)
            cur.execute(
                f"INSERT INTO AuthRolePermissions (RoleId, PermissionId) "
                f"SELECT ?, PermissionId FROM AuthPermissions "
                f"WHERE PermissionKey IN ({placeholders})",
                int(role_id), *perm_keys,
            )
        conn.commit()


def delete_role(role_id: int) -> None:
    """Delete a role. Caller must ensure no users are assigned first.
    AuthRolePermissions rows are removed by the ON DELETE CASCADE constraint."""
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM AuthRoles WHERE RoleId = ?", int(role_id))
        conn.commit()


def count_users_with_permission(perm_key: str) -> int:
    """How many ACTIVE users currently hold a given permission (any role).
    Used to stop an admin from removing the last ``manage_users`` grant."""
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(DISTINCT u.UserId) FROM AuthUsers u "
                "JOIN AuthRolePermissions rp ON rp.RoleId = u.RoleId "
                "JOIN AuthPermissions p ON p.PermissionId = rp.PermissionId "
                "WHERE u.IsActive = 1 AND p.PermissionKey = ?",
                perm_key,
            )
            return int(cur.fetchone()[0])
    except Exception as e:
        logger.error("count_users_with_permission failed: %s", e)
        return 0


def get_role_by_name(name: str) -> Optional[dict]:
    for r in list_roles_with_perms():
        if r["name"] == name:
            return r
    return None
