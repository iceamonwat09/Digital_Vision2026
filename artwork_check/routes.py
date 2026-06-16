"""
Flask blueprint for Artwork Proof Check.

Registered from ``app.py`` inside a try/except so any failure here can
only disable THIS mode — never the existing Can Dent / Label / Label
Paper features.
"""

from __future__ import annotations

import logging
import os

from flask import (Blueprint, jsonify, render_template, request,
                   send_file, send_from_directory)

from . import pipeline, report, translate, vocab, zones as zones_mod

logger = logging.getLogger(__name__)

artwork_bp = Blueprint("artwork_check", __name__)

MAX_UPLOAD_MB = 40


# ── Pages ─────────────────────────────────────────────────────────────

@artwork_bp.route("/artwork_check")
def artwork_page():
    return render_template("artwork_check.html")


@artwork_bp.route("/artwork_check/history")
def artwork_history_page():
    return render_template("artwork_check_history.html")


# ── Inspection flow ───────────────────────────────────────────────────

@artwork_bp.route("/api/artwork/upload", methods=["POST"])
def api_upload():
    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify({"error": "ไม่พบไฟล์"}), 400
    data = f.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        return jsonify({"error": f"ไฟล์ใหญ่เกิน {MAX_UPLOAD_MB} MB"}), 400
    try:
        result = pipeline.start_inspection(data, f.filename)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("[artwork] upload failed")
        return jsonify({"error": f"เปิดไฟล์ไม่สำเร็จ: {e}"}), 500
    return jsonify(result)


@artwork_bp.route("/api/artwork/<rec_id>/inspect", methods=["POST"])
def api_inspect(rec_id):
    body = request.get_json(silent=True) or {}
    try:
        zone_list = zones_mod.sanitize_zones(body.get("zones"))
    except ValueError as e:
        return jsonify({"error": f"โซนไม่ถูกต้อง: {e}"}), 400
    brand = str(body.get("brand", "")).strip()[:60]
    try:
        rep = pipeline.run_inspection(rec_id, zone_list, brand=brand)
    except (ValueError, FileNotFoundError) as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.exception("[artwork] inspection failed for %s", rec_id)
        return jsonify({"error": f"ตรวจไม่สำเร็จ: {e}"}), 500
    return jsonify(rep)


@artwork_bp.route("/api/artwork/<rec_id>/preview.png")
def api_preview(rec_id):
    return _send_artifact(rec_id, "preview.png")


@artwork_bp.route("/api/artwork/<rec_id>/overlay.png")
def api_overlay(rec_id):
    return _send_artifact(rec_id, "overlay.png")


def _send_artifact(rec_id, name):
    try:
        d = report.inspection_dir(rec_id)
    except ValueError:
        return jsonify({"error": "bad id"}), 400
    if not os.path.exists(os.path.join(d, name)):
        return jsonify({"error": "not found"}), 404
    return send_from_directory(d, name, max_age=0)


@artwork_bp.route("/api/artwork/<rec_id>/crop")
def api_crop(rec_id):
    """High-DPI crop for the defect table. Query: x,y,w,h (normalized)."""
    try:
        bbox = [float(request.args.get(k, "")) for k in ("x", "y", "w", "h")]
    except ValueError:
        return jsonify({"error": "ต้องระบุ x,y,w,h"}), 400
    try:
        jpg = pipeline.zone_crop_jpg(rec_id, bbox)
    except (ValueError, FileNotFoundError) as e:
        return jsonify({"error": str(e)}), 404
    import io
    return send_file(io.BytesIO(jpg), mimetype="image/jpeg", max_age=0)


@artwork_bp.route("/api/artwork/<rec_id>/snap", methods=["POST"])
def api_snap(rec_id):
    """Fit a zone bbox to the content under it (double-click in the UI).
    Body: {"bbox": [x, y, w, h]} normalized. Returns {"bbox": [...]}."""
    body = request.get_json(silent=True) or {}
    raw = body.get("bbox")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return jsonify({"error": "ต้องส่ง bbox [x,y,w,h]"}), 400
    try:
        bbox = [float(v) for v in raw]
    except (TypeError, ValueError):
        return jsonify({"error": "bbox ไม่ใช่ตัวเลข"}), 400
    if not (0 <= bbox[0] < 1 and 0 <= bbox[1] < 1
            and 0 < bbox[2] <= 1 and 0 < bbox[3] <= 1):
        return jsonify({"error": "bbox นอกช่วง 0..1"}), 400
    try:
        d = report.inspection_dir(rec_id)
    except ValueError:
        return jsonify({"error": "bad id"}), 400
    path = os.path.join(d, "preview.png")
    if not os.path.exists(path):
        return jsonify({"error": "ไม่พบ preview"}), 404
    import cv2
    img = cv2.imread(path)
    if img is None:
        return jsonify({"error": "อ่าน preview ไม่ได้"}), 500
    return jsonify({"bbox": zones_mod.snap_bbox(img, bbox)})


@artwork_bp.route("/api/artwork/<rec_id>/translate", methods=["POST"])
def api_translate(rec_id):
    """Build the per-line text table and (when a translate webhook is
    configured) attach EN translations. Advisory only — reads the saved
    report's OCR text, never re-runs OCR and never touches the verdict."""
    try:
        d = report.inspection_dir(rec_id)
    except ValueError:
        return jsonify({"error": "bad id"}), 400
    rep = report.load_report(rec_id)
    if rep is None:
        return jsonify({"error": "ยังไม่มีผลตรวจของรายการนี้"}), 404

    zone_list = rep.get("zones", [])
    ocr_results = rep.get("ocr", [])
    vocab_words: set = set()
    brand = rep.get("brand", "")
    if brand:
        try:
            vocab_words = set(vocab.load(brand)["words"])
        except ValueError:
            pass

    rows = translate.build_table(zone_list, ocr_results,
                                 vocab_words=vocab_words,
                                 defects=rep.get("defects", []))
    try:
        result = translate.translate_table(d, rows)
    except Exception as e:
        logger.exception("[artwork] translate failed for %s", rec_id)
        return jsonify({"error": f"แปลไม่สำเร็จ: {e}"}), 500

    # translate_table may return rows from an older cache that predates the
    # mismatch cross-check. Keep the freshly-built status/flags authoritative
    # and only borrow the EN strings (which are what the cache really saves).
    en_by_src: dict = {}
    for rr in result.get("rows", []):
        en_by_src.setdefault(rr.get("src", ""), rr.get("en", ""))
    for r in rows:
        r["en"] = en_by_src.get(r["src"], r.get("en", ""))
    result["rows"] = rows

    result["enabled"] = translate.is_enabled()
    return jsonify(result)


@artwork_bp.route("/api/artwork/<rec_id>/report")
def api_report(rec_id):
    try:
        rep = report.load_report(rec_id)
    except ValueError:
        return jsonify({"error": "bad id"}), 400
    if rep is None:
        return jsonify({"error": "ไม่พบรายงาน"}), 404
    return jsonify(rep)


@artwork_bp.route("/api/artwork/history")
def api_history():
    limit = request.args.get("limit", 50, type=int)
    return jsonify({"records": report.list_inspections(limit=limit)})


@artwork_bp.route("/api/artwork/<rec_id>", methods=["DELETE"])
def api_delete(rec_id):
    try:
        ok = report.delete_inspection(rec_id)
    except ValueError:
        return jsonify({"error": "bad id"}), 400
    return jsonify({"deleted": ok})


# ── Zone templates ────────────────────────────────────────────────────

@artwork_bp.route("/api/artwork/templates")
def api_templates():
    return jsonify({"templates": zones_mod.list_templates()})


@artwork_bp.route("/api/artwork/templates/<name>")
def api_template_load(name):
    try:
        return jsonify({"name": name, "zones": zones_mod.load_template(name)})
    except (FileNotFoundError, ValueError):
        return jsonify({"error": "ไม่พบ template"}), 404


@artwork_bp.route("/api/artwork/templates", methods=["POST"])
def api_template_save():
    body = request.get_json(silent=True) or {}
    name = str(body.get("name", "")).strip()
    try:
        zones_mod.save_template(name, body.get("zones"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"saved": name})


# ── Brand vocabulary ──────────────────────────────────────────────────

@artwork_bp.route("/api/artwork/vocab")
def api_vocab_brands():
    return jsonify({"brands": vocab.list_brands()})


@artwork_bp.route("/api/artwork/vocab/<brand>")
def api_vocab_get(brand):
    try:
        return jsonify(vocab.load(brand))
    except ValueError:
        return jsonify({"error": "ชื่อแบรนด์ไม่ถูกต้อง"}), 400


@artwork_bp.route("/api/artwork/vocab/<brand>", methods=["POST"])
def api_vocab_save(brand):
    body = request.get_json(silent=True) or {}
    try:
        data = vocab.save(brand,
                          body.get("words") or [],
                          body.get("phrases") or [])
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(data)
