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


@artwork_bp.route("/api/artwork/<rec_id>/upload_ref", methods=["POST"])
def api_upload_ref(rec_id):
    """Attach the optional REFERENCE file (ฉบับเก่า) for cross-file
    compare. The primary upload/inspect flow is untouched — an
    inspection without this call behaves exactly as before."""
    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify({"error": "ไม่พบไฟล์"}), 400
    data = f.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        return jsonify({"error": f"ไฟล์ใหญ่เกิน {MAX_UPLOAD_MB} MB"}), 400
    try:
        result = pipeline.start_ref(rec_id, data, f.filename)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.exception("[artwork] ref upload failed for %s", rec_id)
        return jsonify({"error": f"เปิดไฟล์อ้างอิงไม่สำเร็จ: {e}"}), 500
    return jsonify(result)


@artwork_bp.route("/api/artwork/<rec_id>/propose", methods=["POST"])
def api_propose(rec_id):
    """On-demand zone proposal for one document (ปุ่ม "เสนอโซนใหม่").
    Body: {"doc": "a"|"b"} (default "a"). Read-only w.r.t. stored state."""
    body = request.get_json(silent=True) or {}
    doc = str(body.get("doc", "a")).lower()
    try:
        zones = pipeline.propose_for(rec_id, doc)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.exception("[artwork] propose failed for %s", rec_id)
        return jsonify({"error": f"เสนอโซนไม่สำเร็จ: {e}"}), 500
    return jsonify({"zones": zones})


@artwork_bp.route("/api/artwork/<rec_id>/inspect", methods=["POST"])
def api_inspect(rec_id):
    body = request.get_json(silent=True) or {}
    try:
        zone_list = zones_mod.sanitize_zones(body.get("zones"))
    except ValueError as e:
        return jsonify({"error": f"โซนไม่ถูกต้อง: {e}"}), 400
    brand = str(body.get("brand", "")).strip()[:60]
    auto_rotate = bool(body.get("auto_rotate"))
    try:
        rep = pipeline.run_inspection(rec_id, zone_list, brand=brand,
                                      auto_rotate=auto_rotate)
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


@artwork_bp.route("/api/artwork/<rec_id>/preview_b.png")
def api_preview_b(rec_id):
    return _send_artifact(rec_id, "preview_b.png")


@artwork_bp.route("/api/artwork/<rec_id>/overlay_b.png")
def api_overlay_b(rec_id):
    return _send_artifact(rec_id, "overlay_b.png")


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
    """High-DPI crop for the defect table. Query: x,y,w,h (normalized)
    + optional doc=a|b (b = crop from the attached reference file)."""
    try:
        bbox = [float(request.args.get(k, "")) for k in ("x", "y", "w", "h")]
    except ValueError:
        return jsonify({"error": "ต้องระบุ x,y,w,h"}), 400
    doc = request.args.get("doc", "a").lower()
    if doc not in ("a", "b"):
        return jsonify({"error": "doc ต้องเป็น a หรือ b"}), 400
    rotate = request.args.get("rotate", "0")
    if rotate not in ("0", "90", "180", "270", "auto"):
        return jsonify({"error": "rotate ต้องเป็น 0/90/180/270/auto"}), 400
    try:
        jpg = pipeline.zone_crop_jpg(rec_id, bbox, doc=doc, rotate=rotate)
    except (ValueError, FileNotFoundError) as e:
        return jsonify({"error": str(e)}), 404
    import io
    return send_file(io.BytesIO(jpg), mimetype="image/jpeg", max_age=0)


@artwork_bp.route("/api/artwork/<rec_id>/snap", methods=["POST"])
def api_snap(rec_id):
    """Fit a zone bbox to the content under it (double-click in the UI).
    Body: {"bbox": [x, y, w, h], "doc": "a"|"b"} normalized (doc
    optional, default "a"). Returns {"bbox": [...]}."""
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
    doc = str(body.get("doc", "a")).lower()
    if doc not in ("a", "b"):
        return jsonify({"error": "doc ต้องเป็น a หรือ b"}), 400
    try:
        d = report.inspection_dir(rec_id)
    except ValueError:
        return jsonify({"error": "bad id"}), 400
    path = os.path.join(d, "preview_b.png" if doc == "b" else "preview.png")
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
    configured) attach EN translations. Advisory only — never touches the
    PASS/FAIL verdict.

    Two modes, picked automatically:
      • A full inspection already exists (report.json) → use its saved OCR +
        defects so the table can also flag cross-panel mismatch lines.
      • No inspection yet → OCR the zones sent in the request body on the fly
        (cached) so the translate tab works WITHOUT pressing "ส่งตรวจสอบ".
        This advisory-only path has no defects, so it reports spelling +
        translation only (never a mismatch verdict)."""
    try:
        d = report.inspection_dir(rec_id)
    except ValueError:
        return jsonify({"error": "bad id"}), 400

    body = request.get_json(silent=True) or {}
    rep = report.load_report(rec_id)
    ocr_only = rep is None

    if not ocr_only:
        zone_list = rep.get("zones", [])
        ocr_results = rep.get("ocr", [])
        brand = rep.get("brand", "")
        defects = rep.get("defects", [])
    else:
        # No full inspection yet — OCR the supplied zones on the fly.
        try:
            zone_list = zones_mod.sanitize_zones(body.get("zones"))
        except ValueError as e:
            return jsonify({"error": f"โซนไม่ถูกต้อง: {e}"}), 400
        if not zone_list:
            return jsonify({
                "error": "ยังไม่มีผลตรวจของรายการนี้ และไม่ได้ส่งโซนมาเพื่อ OCR "
                         "(กรุณาจัดโซนก่อน หรือกด ‘ส่งตรวจสอบ’)"
            }), 400
        brand = str(body.get("brand", "")).strip()[:60]
        auto_rotate = bool(body.get("auto_rotate"))
        try:
            zone_list, ocr_results = pipeline.run_ocr_only(
                rec_id, zone_list, auto_rotate=auto_rotate)
        except (ValueError, FileNotFoundError) as e:
            return jsonify({"error": str(e)}), 404
        except Exception as e:
            logger.exception("[artwork] ocr-only failed for %s", rec_id)
            return jsonify({"error": f"OCR ไม่สำเร็จ: {e}"}), 500
        defects = None   # advisory: no verdict/mismatch without a full inspection

    vocab_words: set = set()
    if brand:
        try:
            vocab_words = set(vocab.load(brand)["words"])
        except ValueError:
            pass

    rows = translate.build_table(zone_list, ocr_results,
                                 vocab_words=vocab_words,
                                 defects=defects)
    try:
        result = translate.translate_table(d, rows)
    except Exception as e:
        logger.exception("[artwork] translate failed for %s", rec_id)
        return jsonify({"error": f"แปลไม่สำเร็จ: {e}"}), 500

    # translate_table may return rows from an older cache that predates the
    # mismatch cross-check. Keep the freshly-built status/flags authoritative
    # and only borrow the EN strings (which are what the cache really saves).
    cache_by_src: dict = {}
    for rr in result.get("rows", []):
        cache_by_src.setdefault(rr.get("src", ""), rr)
    for r in rows:
        cached_row = cache_by_src.get(r["src"], {})
        r["en"] = cached_row.get("en", r.get("en", ""))
        r["ai_spell"] = cached_row.get(
            "ai_spell", {"flagged": False, "suggestion": None})
    result["rows"] = rows

    result["enabled"] = translate.is_enabled()
    # Tells the UI this table came from the on-the-fly OCR path (no full
    # inspection yet) so it can note that cross-panel mismatch isn't checked.
    result["ocr_only"] = ocr_only
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
