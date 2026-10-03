"""Flask blueprint ของ Artwork V2 — หน้า ``/artwork_v2`` + API ``/api/artwork_v2/*``

สิทธิ์:
* เข้าหน้า/ใช้งาน = สิทธิ์ ``inspect_artwork`` (คุมที่ ``auth/access.py`` ตาม path)
* ตั้ง/ลบ/ทดสอบ API key = ต้องมี ``manage_users`` (ผู้ดูแล) — เช็คในไฟล์นี้
* งานแต่ละงานเปิดได้เฉพาะเจ้าของหรือผู้ดูแล (ด่าน ``_job_guard``)
* ไม่มีระบบล็อกอิน (AUTH_ENABLED ปิด) = ทุกอย่างเปิด เหมือนโหมดอื่น
"""

from __future__ import annotations

import logging
import os
import re
import time

from flask import (Blueprint, abort, g, jsonify, render_template, request,
                   send_file)

from . import VERSION, config, jobs, keystore, pipeline, vision_client

logger = logging.getLogger(__name__)

artwork_v2_bp = Blueprint("artwork_v2", __name__)

_IMG_RE = re.compile(r"^(p[0-9]+|rr[0-9]+)_[ab]\.jpg$")
_RAW_RE = re.compile(r"^p[0-9]+_[ab]\.json$")


def _viewer():
    if not getattr(g, "auth_enabled", False):
        return None
    return getattr(g, "current_user", None) or {}


def _is_admin(viewer) -> bool:
    if viewer is None:
        return True
    return "manage_users" in (viewer.get("perms") or [])


def _can_view_job(meta: dict) -> bool:
    viewer = _viewer()
    if viewer is None or _is_admin(viewer):
        return True
    owner = (meta or {}).get("owner") or {}
    oid, vid = str(owner.get("user_id") or ""), str(viewer.get("sub") or "")
    return bool(oid) and bool(vid) and oid == vid


@artwork_v2_bp.before_request
def _job_guard():
    job_id = (request.view_args or {}).get("job_id")
    if not job_id:
        return None
    try:
        m = jobs.meta(job_id)
    except (ValueError, FileNotFoundError):
        return jsonify({"error": "ไม่พบงาน"}), 404
    if not _can_view_job(m):
        return jsonify({"error": "งานนี้เป็นของผู้ใช้อื่น"}), 403
    return None


def _err(msg, code=400):
    return jsonify({"error": keystore.redact(msg)}), code


# ── หน้าเว็บ ─────────────────────────────────────────────────────────

@artwork_v2_bp.route("/artwork_v2")
def page():
    return render_template("artwork_v2.html", v2_version=VERSION,
                           can_manage_key=_is_admin(_viewer()),
                           v2_sharpness=config.SHARPNESS)


# ── API key ──────────────────────────────────────────────────────────

@artwork_v2_bp.route("/api/artwork_v2/settings", methods=["GET"])
def settings_get():
    st = keystore.status()
    st["can_manage"] = _is_admin(_viewer())
    st["version"] = VERSION
    return jsonify(st)


@artwork_v2_bp.route("/api/artwork_v2/settings", methods=["POST"])
def settings_save():
    if not _is_admin(_viewer()):
        return _err("ต้องเป็นผู้ดูแลระบบจึงจะตั้งค่า API key ได้", 403)
    key = ((request.get_json(silent=True) or {}).get("key") or "").strip()
    err = keystore.validate(key)
    if err:
        return _err(err)
    keystore.save(key)
    logger.info("[artwork_v2] API key saved (%s)", keystore.mask(key))
    st = keystore.status()
    st["can_manage"] = True
    return jsonify(st)


@artwork_v2_bp.route("/api/artwork_v2/settings", methods=["DELETE"])
def settings_delete():
    if not _is_admin(_viewer()):
        return _err("ต้องเป็นผู้ดูแลระบบจึงจะลบ API key ได้", 403)
    keystore.delete()
    st = keystore.status()
    st["can_manage"] = True
    return jsonify(st)


def _test_image() -> bytes:
    import cv2
    import numpy as np
    from . import imaging
    img = np.full((360, 1400, 3), 255, np.uint8)
    cv2.putText(img, "VISION TEST 2026", (40, 150), cv2.FONT_HERSHEY_SIMPLEX, 3.2,
                (0, 0, 0), 8, cv2.LINE_AA)
    cv2.putText(img, "Net weight 85 g", (40, 290), cv2.FONT_HERSHEY_SIMPLEX, 2.6,
                (0, 0, 0), 6, cv2.LINE_AA)
    return imaging.encode_jpeg(img, 92)


@artwork_v2_bp.route("/api/artwork_v2/settings/test", methods=["POST"])
def settings_test():
    if not _is_admin(_viewer()):
        return _err("ต้องเป็นผู้ดูแลระบบจึงจะทดสอบ API key ได้", 403)
    t0 = time.time()
    res = vision_client.annotate([[{"id": "test", "jpeg": _test_image()}]])
    r = res["results"].get("test") or {}
    text = ((r.get("fta") or {}).get("text") or "").strip()
    call = (res["calls"] or [{}])[0]
    ok = bool(r.get("ok")) and "VISION" in text.upper()
    return jsonify({
        "ok": ok,
        "read_text": text[:200],
        "error": r.get("error", ""),
        "http": call.get("status"),
        "attempts": call.get("attempts"),
        "ms": int((time.time() - t0) * 1000),
        "endpoint": config.ENDPOINT,
        "model": config.MODEL,
        "hint": "" if ok or not r.get("ok") else "เรียกสำเร็จแต่อ่านข้อความทดสอบไม่ออก",
    })


# ── งาน ──────────────────────────────────────────────────────────────

@artwork_v2_bp.route("/api/artwork_v2/jobs", methods=["POST"])
def job_create():
    fa, fb = request.files.get("file_a"), request.files.get("file_b")
    if not fa or not fb or not fa.filename or not fb.filename:
        return _err("ต้องแนบไฟล์ทั้ง A และ B")
    viewer = _viewer()
    owner = None
    if viewer:
        owner = {"user_id": str(viewer.get("sub") or ""),
                 "username": viewer.get("username") or ""}
    try:
        m = jobs.create((fa.filename, fa.read()), (fb.filename, fb.read()), owner)
    except ValueError as e:
        return _err(str(e))
    except Exception as e:                       # noqa: BLE001
        logger.exception("[artwork_v2] สร้างงานไม่สำเร็จ")
        return _err("สร้างงานไม่สำเร็จ: %s" % e, 500)
    return jsonify(m)


@artwork_v2_bp.route("/api/artwork_v2/jobs", methods=["GET"])
def job_list():
    return jsonify({"jobs": jobs.recent(20, can_view=_can_view_job)})


@artwork_v2_bp.route("/api/artwork_v2/jobs/<job_id>", methods=["GET"])
def job_get(job_id):
    m = jobs.meta(job_id)
    d = jobs.job_dir(job_id)
    m["runs"] = sorted(x for x in os.listdir(d) if jobs._RUN_RE.match(x))
    return jsonify(m)


@artwork_v2_bp.route("/api/artwork_v2/jobs/<job_id>/preview/<side>/<int:page>.png")
def job_preview(job_id, side, page):
    if side not in ("a", "b"):
        abort(404)
    try:
        path = jobs.preview_path(job_id, side, page)
    except ValueError as e:
        return _err(str(e), 404)
    return send_file(path, mimetype="image/png", max_age=3600)


@artwork_v2_bp.route("/api/artwork_v2/jobs/<job_id>/run", methods=["POST"])
def job_run(job_id):
    body = request.get_json(silent=True) or {}
    try:
        res = pipeline.run(job_id, body.get("pairs"), sharpness=body.get("sharpness"))
    except ValueError as e:
        return _err(str(e))
    except Exception as e:                       # noqa: BLE001
        logger.exception("[artwork_v2] ตรวจไม่สำเร็จ")
        return _err("ตรวจไม่สำเร็จ: %s: %s" % (type(e).__name__, e), 500)
    logger.info("[artwork_v2] %s %s verdict=%s total=%sms", job_id, res.get("run"),
                res.get("verdict"), (res.get("stage") or {}).get("total_ms"))
    return jsonify(res)


@artwork_v2_bp.route("/api/artwork_v2/jobs/<job_id>/runs/<run>", methods=["GET"])
def run_get(job_id, run):
    try:
        rd = jobs.run_dir(job_id, run)
        return send_file(os.path.join(rd, "result.json"), mimetype="application/json",
                         max_age=0)
    except (ValueError, FileNotFoundError) as e:
        return _err(str(e), 404)


@artwork_v2_bp.route("/api/artwork_v2/jobs/<job_id>/runs/<run>/img/<name>")
def run_img(job_id, run, name):
    if not _IMG_RE.match(name or ""):
        abort(404)
    try:
        path = os.path.join(jobs.run_dir(job_id, run), "img", name)
    except (ValueError, FileNotFoundError):
        abort(404)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, mimetype="image/jpeg", max_age=3600)


@artwork_v2_bp.route("/api/artwork_v2/jobs/<job_id>/runs/<run>/log.txt")
def run_log(job_id, run):
    try:
        path = os.path.join(jobs.run_dir(job_id, run), "log.txt")
    except (ValueError, FileNotFoundError):
        abort(404)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, mimetype="text/plain; charset=utf-8", as_attachment=True,
                     download_name="artwork_v2_%s_%s_log.txt" % (job_id, run))


@artwork_v2_bp.route("/api/artwork_v2/jobs/<job_id>/runs/<run>/raw/<name>")
def run_raw(job_id, run, name):
    if not _RAW_RE.match(name or ""):
        abort(404)
    try:
        path = os.path.join(jobs.run_dir(job_id, run), "raw", name)
    except (ValueError, FileNotFoundError):
        abort(404)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, mimetype="application/json", as_attachment=True,
                     download_name="artwork_v2_%s_%s_%s" % (job_id, run, name))
