"""
Flask blueprint for authentication + user management.

Routes
  GET  /login                 — login page
  POST /api/auth/login        — sign in (sets httpOnly cookies)
  POST /api/auth/logout       — clear cookies
  POST /api/auth/refresh      — mint a new access token from the refresh cookie
  GET  /api/auth/me           — current user + permissions
  GET  /api/auth/policy       — password policy (for live UX validation)
  GET  /api/auth/users        — list users           (needs manage_users)
  POST /api/auth/users        — create a user        (needs manage_users)
"""

from __future__ import annotations

import logging
import re

from flask import (Blueprint, g, jsonify, render_template, request)

from . import config as ac, passwords, store, tokens

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth_check", __name__)


# ── Helpers ───────────────────────────────────────────────────────────

def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or ""


def _set_auth_cookies(resp, access: str, refresh: str, remember: bool):
    """httpOnly cookies. remember → persistent; otherwise session cookies."""
    access_age = ac.ACCESS_TTL_MIN * 60 if remember else None
    refresh_age = ac.REFRESH_TTL_DAYS * 86400 if remember else None
    common = dict(httponly=True, secure=ac.COOKIE_SECURE,
                  samesite=ac.COOKIE_SAMESITE, path="/")
    resp.set_cookie(ac.COOKIE_ACCESS, access, max_age=access_age, **common)
    resp.set_cookie(ac.COOKIE_REFRESH, refresh, max_age=refresh_age, **common)


def _clear_auth_cookies(resp):
    resp.delete_cookie(ac.COOKIE_ACCESS, path="/")
    resp.delete_cookie(ac.COOKIE_REFRESH, path="/")


def _public_user(user: dict, perms: list) -> dict:
    return {
        "username": user["username"],
        "role": user.get("role_name", ""),
        "permissions": perms,
    }


# ── Pages ─────────────────────────────────────────────────────────────

@auth_bp.route("/login")
def login_page():
    return render_template("login.html")


# ── Auth API ──────────────────────────────────────────────────────────

@auth_bp.route("/api/auth/policy")
def api_policy():
    return jsonify(passwords.policy_dict())


@auth_bp.route("/api/auth/login", methods=["POST"])
def api_login():
    body = request.get_json(silent=True) or request.form
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    remember = bool(body.get("remember"))
    ip, ua = _client_ip(), request.headers.get("User-Agent", "")

    if not username or not password:
        return jsonify({"error": "กรุณากรอกชื่อผู้ใช้และรหัสผ่าน"}), 400

    if not store.db_available():
        return jsonify({"error": "ระบบฐานข้อมูลไม่พร้อม (pyodbc ไม่ได้ติดตั้ง)"}), 503

    user = store.get_user_by_login(username)

    # Generic message on user-not-found / wrong-password to avoid user enumeration.
    GENERIC = "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"

    if user is None or not user["is_active"]:
        store.record_login_attempt(username, None, False, ip, ua, "no_user_or_inactive")
        return jsonify({"error": GENERIC}), 401

    if store.is_locked(user):
        store.record_login_attempt(username, user["user_id"], False, ip, ua, "locked")
        return jsonify({
            "error": f"บัญชีถูกล็อกชั่วคราว ลองใหม่อีกครั้งในอีกประมาณ "
                     f"{ac.LOCK_MINUTES} นาที"
        }), 423

    if not passwords.verify_password(password, user["password_hash"]):
        store.register_failure(user["user_id"], ac.MAX_FAILED, ac.LOCK_MINUTES)
        store.record_login_attempt(username, user["user_id"], False, ip, ua, "bad_password")
        return jsonify({"error": GENERIC}), 401

    # Success
    store.register_success(user["user_id"])
    perms = store.get_permissions(user["role_id"])
    claims = tokens.user_claims(user, perms)
    access = tokens.make_access(claims)
    refresh = tokens.make_refresh(user)
    store.record_login_attempt(username, user["user_id"], True, ip, ua, "ok")

    resp = jsonify({"status": "ok", "user": _public_user(user, perms)})
    _set_auth_cookies(resp, access, refresh, remember)
    return resp


@auth_bp.route("/api/auth/refresh", methods=["POST"])
def api_refresh():
    rdata = tokens.decode(request.cookies.get(ac.COOKIE_REFRESH), "refresh")
    if not rdata:
        return jsonify({"error": "เซสชันหมดอายุ กรุณาเข้าสู่ระบบใหม่"}), 401
    user = store.get_user_by_id(rdata["sub"])
    if not user or not user["is_active"] or store.is_locked(user):
        return jsonify({"error": "บัญชีถูกปิดหรือถูกล็อก"}), 401
    perms = store.get_permissions(user["role_id"])
    access = tokens.make_access(tokens.user_claims(user, perms))
    resp = jsonify({"status": "ok", "user": _public_user(user, perms)})
    resp.set_cookie(ac.COOKIE_ACCESS, access, max_age=ac.ACCESS_TTL_MIN * 60,
                    httponly=True, secure=ac.COOKIE_SECURE,
                    samesite=ac.COOKIE_SAMESITE, path="/")
    return resp


@auth_bp.route("/api/auth/logout", methods=["POST"])
def api_logout():
    resp = jsonify({"status": "ok"})
    _clear_auth_cookies(resp)
    return resp


@auth_bp.route("/api/auth/me")
def api_me():
    user = getattr(g, "current_user", None)
    if not user:
        if not ac.AUTH_ENABLED:
            return jsonify({"auth_enabled": False, "user": None})
        return jsonify({"error": "ยังไม่ได้เข้าสู่ระบบ"}), 401
    return jsonify({
        "auth_enabled": True,
        "user": {
            "username": user["username"],
            "role": user.get("role", ""),
            "permissions": user.get("perms", []),
        },
    })


# ── User management (manage_users) ────────────────────────────────────
# The guard already enforces manage_users on /api/auth/users via access.py.

@auth_bp.route("/admin/users")
def admin_users_page():
    return render_template("admin_users.html")


@auth_bp.route("/api/auth/users", methods=["GET"])
def api_users_list():
    return jsonify({"users": store.list_users(),
                    "roles": store.role_names()})


@auth_bp.route("/api/auth/users", methods=["POST"])
def api_users_create():
    if not passwords.hashing_available():
        return jsonify({"error": "bcrypt ไม่ได้ติดตั้ง"}), 503
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    email = (body.get("email") or "").strip() or None
    password = body.get("password") or ""
    role = (body.get("role") or "").strip()

    if not username or not role:
        return jsonify({"error": "ต้องระบุ username และ role"}), 400

    role_id = store.get_role_id(role)
    if role_id is None:
        return jsonify({"error": f"ไม่พบ role '{role}' ในฐานข้อมูล"}), 400

    ok, errs = passwords.validate_password(password)
    if not ok:
        return jsonify({"error": "รหัสผ่านไม่ผ่านเงื่อนไข", "details": errs}), 400

    try:
        uid = store.create_user(username, email,
                                passwords.hash_password(password), role_id)
    except Exception as e:
        logger.error("create_user failed: %s", e)
        return jsonify({"error": f"สร้างผู้ใช้ไม่สำเร็จ (ซ้ำ?): {e}"}), 409
    return jsonify({"status": "ok", "user_id": uid})


@auth_bp.route("/api/auth/users/<username>/role", methods=["POST"])
def api_user_set_role(username):
    """Assign an account to a different role."""
    body = request.get_json(silent=True) or {}
    role = (body.get("role") or "").strip()
    role_id = store.get_role_id(role)
    if role_id is None:
        return jsonify({"error": f"ไม่พบ role '{role}'"}), 400

    # Guard: don't let the last manage_users account demote itself out of access.
    me = getattr(g, "current_user", None)
    if (me and me.get("username") == username
            and "manage_users" not in (store.get_role_by_name(role) or {}).get("permissions", [])
            and store.count_users_with_permission("manage_users") <= 1):
        return jsonify({"error": "ไม่สามารถถอดสิทธิ์จัดการผู้ใช้ของตัวเองได้ "
                                 "(เป็นบัญชีสุดท้ายที่มีสิทธิ์นี้)"}), 400

    ok = store.set_user_role(username, role_id)
    if not ok:
        return jsonify({"error": "ไม่พบผู้ใช้"}), 404
    return jsonify({"status": "ok"})


@auth_bp.route("/api/auth/users/<username>/active", methods=["POST"])
def api_user_set_active(username):
    """Enable/disable an account."""
    body = request.get_json(silent=True) or {}
    active = bool(body.get("active"))
    me = getattr(g, "current_user", None)
    if me and me.get("username") == username and not active:
        return jsonify({"error": "ปิดบัญชีของตัวเองไม่ได้"}), 400
    ok = store.set_user_active(username, active)
    if not ok:
        return jsonify({"error": "ไม่พบผู้ใช้"}), 404
    return jsonify({"status": "ok"})


# ── Role + permission management (manage_users) ───────────────────────

@auth_bp.route("/api/auth/permissions")
def api_permissions():
    return jsonify({"permissions": store.list_permissions()})


@auth_bp.route("/api/auth/roles")
def api_roles_list():
    return jsonify({"roles": store.list_roles_with_perms(),
                    "permissions": store.list_permissions()})


_ROLE_NAME_RE = re.compile(r"^[A-Za-z0-9_ ]{2,50}$")


@auth_bp.route("/api/auth/roles", methods=["POST"])
def api_role_create():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    desc = (body.get("description") or "").strip()
    perms = [str(p) for p in (body.get("permissions") or [])]
    if not _ROLE_NAME_RE.match(name):
        return jsonify({"error": "ชื่อ role ต้องเป็น A-Z/0-9/_/เว้นวรรค ยาว 2-50"}), 400
    if store.get_role_id(name) is not None:
        return jsonify({"error": f"role '{name}' มีอยู่แล้ว"}), 409
    try:
        rid = store.create_role(name, desc)
        store.set_role_permissions(rid, perms)
    except Exception as e:
        logger.error("create_role failed: %s", e)
        return jsonify({"error": f"สร้าง role ไม่สำเร็จ: {e}"}), 500
    return jsonify({"status": "ok", "role_id": rid})


@auth_bp.route("/api/auth/roles/<int:role_id>", methods=["PUT"])
def api_role_update(role_id):
    """Replace a role's permission set (and optionally its description)."""
    body = request.get_json(silent=True) or {}
    perms = [str(p) for p in (body.get("permissions") or [])]
    desc = body.get("description")
    role = next((r for r in store.list_roles_with_perms()
                 if r["role_id"] == role_id), None)
    if role is None:
        return jsonify({"error": "ไม่พบ role"}), 404

    # Guard: removing manage_users from a role must not strip the last admin.
    if ("manage_users" in role["permissions"] and "manage_users" not in perms
            and store.count_users_with_permission("manage_users")
            <= role["user_count"]):
        return jsonify({"error": "ถอดสิทธิ์ 'จัดการผู้ใช้' ไม่ได้ "
                                 "เพราะจะไม่เหลือบัญชีที่จัดการระบบได้"}), 400
    try:
        store.set_role_permissions(role_id, perms,
                                   description=desc if desc is not None else None)
    except Exception as e:
        logger.error("update_role failed: %s", e)
        return jsonify({"error": f"แก้ไข role ไม่สำเร็จ: {e}"}), 500
    return jsonify({"status": "ok"})


@auth_bp.route("/api/auth/roles/<int:role_id>", methods=["DELETE"])
def api_role_delete(role_id):
    role = next((r for r in store.list_roles_with_perms()
                 if r["role_id"] == role_id), None)
    if role is None:
        return jsonify({"error": "ไม่พบ role"}), 404
    if role["user_count"] > 0:
        return jsonify({"error": f"ลบไม่ได้ — ยังมีผู้ใช้ {role['user_count']} คน "
                                 "ใช้ role นี้ (ย้ายไป role อื่นก่อน)"}), 400
    try:
        store.delete_role(role_id)
    except Exception as e:
        logger.error("delete_role failed: %s", e)
        return jsonify({"error": f"ลบ role ไม่สำเร็จ: {e}"}), 500
    return jsonify({"status": "ok"})
