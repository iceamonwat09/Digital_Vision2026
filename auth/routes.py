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

@auth_bp.route("/api/auth/users", methods=["GET"])
def api_users_list():
    return jsonify({"users": store.list_users(),
                    "roles": list(ac.ROLES.keys())})


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
    if role not in ac.ROLES:
        return jsonify({"error": f"role ไม่ถูกต้อง (เลือกจาก {list(ac.ROLES)})"}), 400

    ok, errs = passwords.validate_password(password)
    if not ok:
        return jsonify({"error": "รหัสผ่านไม่ผ่านเงื่อนไข", "details": errs}), 400

    role_id = store.get_role_id(role)
    if role_id is None:
        return jsonify({"error": "ไม่พบ role ในฐานข้อมูล — รัน auth_schema.sql แล้วหรือยัง?"}), 400

    try:
        uid = store.create_user(username, email,
                                passwords.hash_password(password), role_id)
    except Exception as e:
        logger.error("create_user failed: %s", e)
        return jsonify({"error": f"สร้างผู้ใช้ไม่สำเร็จ (ซ้ำ?): {e}"}), 409
    return jsonify({"status": "ok", "user_id": uid})
