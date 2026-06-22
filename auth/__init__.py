"""
Authentication + RBAC package for the VisionIQ inspection app.

Fully isolated like ``artwork_check``: registered from ``app.py`` inside a
try/except so a failure here cannot break the existing inspection modes. When
``AUTH_ENABLED`` is false the whole layer becomes a no-op (every route is open
and ``has_perm`` returns True) — this lets a running production station roll
auth out (or back) without code changes.

Public entry point: ``install_auth(app)``.
"""

from __future__ import annotations

import logging

from flask import Flask

logger = logging.getLogger(__name__)


def install_auth(app: Flask) -> None:
    """Wire the auth blueprint, request guard and template helpers into ``app``."""
    from . import access, config as ac
    from .routes import auth_bp

    app.register_blueprint(auth_bp)
    access.install(app)
    logger.info("Auth + RBAC installed (AUTH_ENABLED=%s)", ac.AUTH_ENABLED)
