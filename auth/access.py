"""
Request guard + RBAC enforcement + template helpers.

Centralised so the many existing routes in app.py do not each need a decorator:
a single ``before_request`` enforces "must be logged in" everywhere except the
public auth endpoints, and a path→permission table authorises the protected
areas. The backend ALWAYS re-checks here — the frontend hiding a menu is only
UX, never the security boundary.
"""

from __future__ import annotations

import logging

from flask import (Flask, g, jsonify, redirect, request, url_for)

from . import config as ac, store, tokens

logger = logging.getLogger(__name__)

# Endpoints/paths reachable without a session.
_PUBLIC_PATHS = (
    "/login",
    "/api/auth/login",
    "/api/auth/refresh",
    "/api/auth/logout",
    "/api/auth/policy",
)

# Path prefix → required permission. Checked most-specific-first, so
# "/label_paper/history" (view_history) wins over "/label_paper" (inspect).
_PERM_RULES = [
    ("/admin",                   "manage_users"),
    ("/api/auth/users",          "manage_users"),
    ("/api/auth/roles",          "manage_users"),
    ("/api/auth/permissions",    "manage_users"),
    # History (pages + their data APIs) — most-specific first so these win
    # over the broader inspect_* prefixes below.
    ("/api/label_paper/history", "view_history"),
    ("/api/artwork/history",     "view_history"),
    ("/label_paper/history",     "view_history"),
    ("/artwork_check/history",   "view_history"),
    ("/history",                 "view_history"),
    ("/api/defects",             "view_history"),
    ("/dashboard",             "view_dashboard"),
    ("/api/stats",             "view_dashboard"),
    ("/label_paper",           "inspect_label_paper"),
    ("/api/label_paper",       "inspect_label_paper"),
    ("/artwork_check",         "inspect_artwork"),
    ("/api/artwork",           "inspect_artwork"),
    # Live detection + snapshot + camera/model control + the home page.
    ("/video_feed",            "run_live_detection"),
    ("/viewfinder_feed",       "run_live_detection"),
    ("/api/detection",         "run_live_detection"),
    ("/api/viewfinder",        "run_live_detection"),
    ("/api/snapshot",          "run_live_detection"),
    ("/api/camera",            "run_live_detection"),
    ("/api/mode",              "run_live_detection"),
    ("/api/models",            "run_live_detection"),
    ("/api/modes",             "run_live_detection"),
]


def _required_permission(path: str):
    if path == "/":
        return "run_live_detection"
    for prefix, perm in _PERM_RULES:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix):
            return perm
    return None


def _is_public(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in _PUBLIC_PATHS)


def _load_user():
    """
    Resolve the current user from cookies.

    Returns (claims, new_access_token). ``new_access_token`` is non-None when the
    access cookie was expired/missing but the refresh cookie is still valid — the
    guard then transparently re-issues an access token (set by after_request) so
    page navigation does not bounce the user to /login mid-session.
    """
    access = request.cookies.get(ac.COOKIE_ACCESS)
    claims = tokens.decode(access, "access")
    if claims:
        # The access token carries perms/role captured at login. Re-resolve them
        # from the DB on each request so a role change or permission edit takes
        # effect immediately — not only after the token's 60-min lifetime.
        fresh, deny = _resolve_live(claims.get("sub"))
        if deny:
            return None, None            # account now missing/disabled/locked
        return (fresh or claims), None   # fresh perms, or token fallback on DB error

    refresh = request.cookies.get(ac.COOKIE_REFRESH)
    rdata = tokens.decode(refresh, "refresh")
    if rdata:
        fresh, _deny = _resolve_live(rdata["sub"])
        if fresh:
            return fresh, tokens.make_access(fresh)
    return None, None


def _resolve_live(user_id):
    """Build current claims straight from the DB for an authenticated user.

    Returns ``(claims, deny)``:
      * ``(claims, False)`` — account OK, claims reflect current role/perms.
      * ``(None, True)``    — account missing/disabled/locked → deny access.
      * ``(None, False)``   — DB error → caller falls back to token claims.
    """
    if user_id is None:
        return None, True
    try:
        user = store.get_user_by_id(user_id)
        if not user or not user["is_active"] or store.is_locked(user):
            return None, True
        perms = store.get_permissions(user["role_id"])
        return tokens.user_claims(user, perms), False
    except Exception as e:
        logger.error("_resolve_live failed: %s", e)
        return None, False


def _deny(path: str, status: int, perm: str = ""):
    if path.startswith("/api/") or path in ("/video_feed", "/viewfinder_feed"):
        msg = ("กรุณาเข้าสู่ระบบ" if status == 401
               else "บัญชีนี้ไม่มีสิทธิ์เข้าถึงฟังก์ชันนี้")
        return jsonify({"error": msg, "status": status}), status
    if status == 401:
        return redirect(url_for("auth_check.login_page", next=path))
    # 403 — logged in but lacks permission. Link to /home (the neutral landing
    # every signed-in account can open) — NOT "/", which itself needs
    # run_live_detection and would bounce limited accounts straight back here.
    label = ac.PERMISSIONS.get(perm, perm)
    perm_line = (
        f"<p style='color:#777;font-size:13px'>สิทธิ์ที่ต้องมี: "
        f"<b>{label}</b> <code>({perm})</code></p>" if perm else ""
    )
    return (
        "<meta charset='utf-8'><div style='font-family:sans-serif;"
        "max-width:520px;margin:80px auto;text-align:center'>"
        "<h2>ไม่มีสิทธิ์เข้าถึง (403)</h2>"
        "<p>บัญชีของคุณไม่มีสิทธิ์ใช้งานหน้านี้ "
        "กรุณาติดต่อผู้ดูแลระบบ</p>"
        + perm_line +
        "<p><a href='/home'>กลับหน้าเมนูหลัก</a></p></div>", 403,
    )


def install(app: Flask) -> None:

    @app.before_request
    def _guard():
        g.current_user = None
        g.new_access_cookie = None
        g.auth_enabled = ac.AUTH_ENABLED

        if not ac.AUTH_ENABLED:
            return None
        if request.endpoint == "static":
            return None

        claims, new_access = _load_user()
        g.current_user = claims
        g.new_access_cookie = new_access

        if _is_public(request.path):
            return None

        if claims is None:
            return _deny(request.path, 401)

        perm = _required_permission(request.path)
        if perm and perm not in (claims.get("perms") or []):
            return _deny(request.path, 403, perm)
        return None

    @app.after_request
    def _refresh_cookie(resp):
        new_access = getattr(g, "new_access_cookie", None)
        if new_access:
            resp.set_cookie(
                ac.COOKIE_ACCESS, new_access,
                max_age=ac.ACCESS_TTL_MIN * 60,
                httponly=True, secure=ac.COOKIE_SECURE,
                samesite=ac.COOKIE_SAMESITE, path="/",
            )
        return resp

    @app.context_processor
    def _inject_auth():
        user = getattr(g, "current_user", None)
        enabled = getattr(g, "auth_enabled", ac.AUTH_ENABLED)

        def has_perm(key: str) -> bool:
            if not enabled:
                return True          # auth off → everything visible
            return bool(user) and key in (user.get("perms") or [])

        return {
            "current_user": user,
            "auth_enabled": enabled,
            "has_perm": has_perm,
        }
