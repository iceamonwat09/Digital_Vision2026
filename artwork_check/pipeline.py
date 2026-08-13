"""
Inspection orchestration: upload → preview+auto-zones → inspect.

Two-step flow matching the UI:

  1. ``start_inspection(file)``  — persist the upload, render the
     preview, propose zones. The human adjusts zones in the browser.
  2. ``run_inspection(id, zones, brand)`` — per-zone text acquisition
     (PDF text layer or N8N OCR), all check layers, overlay, report.

Optional cross-file compare: ``start_ref(id, file)`` attaches a SECOND
document (the reference / ฉบับเก่า) to the same inspection as
``source_b`` + ``preview_b.png``. Zones carry a ``doc`` field ("a"
default = primary file, "b" = reference); OCR routes each zone to its
own document and the check layers run unchanged over the combined zone
list — cross-file comparison is just cross-panel comparison where the
texts happen to come from two files. Without doc-"b" zones every code
path below is identical to the single-file flow.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import List, Optional, Tuple

import cv2

from . import checks, config, ocr, report, vocab, zones as zones_mod
from .pdf_ingest import (ArtworkDocument, apply_rotation, encode_jpg,
                         resolve_rotation)

logger = logging.getLogger(__name__)

ALLOWED_EXT = (".pdf", ".png", ".jpg", ".jpeg")


def start_inspection(file_bytes: bytes, filename: str,
                     owner: Optional[dict] = None) -> dict:
    """เริ่มการตรวจใหม่จากไฟล์ที่อัปโหลด.

    ``owner`` = ``{"user_id", "username"}`` ของคนที่อัปโหลด (routes.py เป็นคน
    หามาจาก session) — เก็บไว้เพื่อให้หน้าประวัติแสดงเฉพาะงานของเจ้าของได้.
    ``None`` (ค่าเริ่มต้น / ไม่มีระบบล็อกอิน) = ไม่บันทึกเจ้าของ, ทุกอย่าง
    ทำงานเหมือนเดิมทุกประการ.
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError(f"รองรับเฉพาะไฟล์ {', '.join(ALLOWED_EXT)}")
    if not file_bytes:
        raise ValueError("ไฟล์ว่าง")

    rec_id = report.new_inspection_id()
    d = report.inspection_dir(rec_id, create=True)
    report.save_owner(rec_id, owner)
    src_path = os.path.join(d, f"source{ext}")
    with open(src_path, "wb") as f:
        f.write(file_bytes)

    doc = ArtworkDocument(src_path)
    preview = doc.render(config.PREVIEW_DPI)
    cv2.imwrite(os.path.join(d, "preview.png"), preview)

    proposed = zones_mod.propose_zones(preview)
    embedded_chars = len(doc.embedded_text())

    logger.info("[artwork] start %s file=%s zones=%d embedded_chars=%d",
                rec_id, filename, len(proposed), embedded_chars)
    return {
        "id": rec_id,
        "filename": filename,
        "page_count": doc.page_count,
        "preview_size": [preview.shape[1], preview.shape[0]],
        "zones": proposed,
        "has_text_layer": embedded_chars >= config.EMBEDDED_TEXT_MIN_CHARS,
        "ocr_available": ocr.is_ocr_available(),
        "spell_layer_available": checks.spell_layer_available(),
    }


def start_ref(rec_id: str, file_bytes: bytes, filename: str) -> dict:
    """
    Attach the optional REFERENCE file (ฉบับเก่า) to an existing
    inspection: persist as ``source_b.<ext>``, render ``preview_b.png``,
    propose zones on it (ids prefixed ``b``, ``doc="b"``). Re-uploading
    replaces the previous reference. Never touches the primary source,
    preview, or any saved report.
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError(f"รองรับเฉพาะไฟล์ {', '.join(ALLOWED_EXT)}")
    if not file_bytes:
        raise ValueError("ไฟล์ว่าง")
    d = report.inspection_dir(rec_id)
    if not os.path.isdir(d):
        raise FileNotFoundError("ไม่พบรายการอัปโหลดนี้ — เลือกไฟล์หลักก่อน")

    # Replace any previous reference file + artifacts derived from it.
    for e in ALLOWED_EXT:
        old = os.path.join(d, f"source_b{e}")
        if os.path.exists(old):
            os.remove(old)
    for stale in ("overlay_b.png", _OCR_ONLY_CACHE):
        p = os.path.join(d, stale)
        if os.path.exists(p):
            os.remove(p)

    with open(os.path.join(d, f"source_b{ext}"), "wb") as f:
        f.write(file_bytes)

    doc = ArtworkDocument(os.path.join(d, f"source_b{ext}"))
    preview = doc.render(config.PREVIEW_DPI)
    cv2.imwrite(os.path.join(d, "preview_b.png"), preview)

    proposed = _mark_ref_zones(zones_mod.propose_zones(preview))
    embedded_chars = len(doc.embedded_text())

    logger.info("[artwork] ref %s file=%s zones=%d embedded_chars=%d",
                rec_id, filename, len(proposed), embedded_chars)
    return {
        "id": rec_id,
        "filename_b": filename,
        "page_count": doc.page_count,
        "preview_size": [preview.shape[1], preview.shape[0]],
        "zones": proposed,
        "has_text_layer": embedded_chars >= config.EMBEDDED_TEXT_MIN_CHARS,
    }


def _mark_ref_zones(proposed: List[dict]) -> List[dict]:
    """Re-tag freshly proposed zones as reference-file zones: ids b1..bN
    (never collide with z1..zN), doc="b", reference labels. Groups keep
    the same sequential letters the primary proposal uses (b1→A, b2→B,
    … in reading order) so the same-ordinal zone of the primary file
    pairs automatically — the human reviews the ⇄ pairing and edits
    letters where the order differs."""
    for i, z in enumerate(proposed, 1):
        z["id"] = f"b{i}"
        z["doc"] = "b"
        z["label"] = f"อ้างอิง {i}"
    return proposed


def propose_for(rec_id: str, doc: str = "a") -> List[dict]:
    """
    On-demand zone proposal (ปุ่ม "เสนอโซนใหม่") for one document of an
    existing inspection — reads the stored preview, no re-upload, no
    new inspection id. Returns proposed zones only; touches nothing on
    disk, so it can never affect a saved report/overlay.
    """
    if doc not in ("a", "b"):
        raise ValueError("doc ต้องเป็น a หรือ b")
    d = report.inspection_dir(rec_id)
    name = "preview_b.png" if doc == "b" else "preview.png"
    path = os.path.join(d, name)
    if not os.path.exists(path):
        raise FileNotFoundError(
            "ไม่พบไฟล์อ้างอิง (ชิ้นงาน) — แนบไฟล์ก่อนกดเสนอโซน"
            if doc == "b" else "ไม่พบรายการอัปโหลดนี้")
    img = cv2.imread(path)
    if img is None:
        raise ValueError("อ่านภาพ preview ไม่ได้")
    proposed = zones_mod.propose_zones(img)
    if doc == "b":
        proposed = _mark_ref_zones(proposed)
    logger.info("[artwork] propose %s doc=%s zones=%d",
                rec_id, doc, len(proposed))
    return proposed


def _split_docs(zone_list: List[dict]) -> Tuple[List[dict], List[dict]]:
    zones_a = [z for z in zone_list if z.get("doc", "a") != "b"]
    zones_b = [z for z in zone_list if z.get("doc", "a") == "b"]
    return zones_a, zones_b


def _read_all_docs(insp_dir: str, zones_a: List[dict], zones_b: List[dict],
                   auto_rotate: bool = False) -> List[dict]:
    """OCR each zone against ITS OWN document (a → source, b → source_b).
    With no doc-"b" zones this is exactly the original single-doc path.
    ``auto_rotate`` is the page-level toggle passed through to the OCR
    layer (only affects zones with rotate == "default")."""
    doc = ArtworkDocument(_find_source(insp_dir))
    results = ocr.read_all_zones(doc, zones_a, page_auto=auto_rotate)
    if zones_b:
        try:
            src_b = _find_source(insp_dir, "source_b")
        except FileNotFoundError:
            raise ValueError(
                "มีโซนของไฟล์อ้างอิง (ชิ้นงาน) แต่ยังไม่ได้แนบไฟล์อ้างอิง — "
                "แนบไฟล์อ้างอิง หรือลบโซนเหล่านั้นก่อนส่งตรวจ")
        results += ocr.read_all_zones(ArtworkDocument(src_b), zones_b,
                                      page_auto=auto_rotate)
    return results


def run_inspection(rec_id: str, zone_list: List[dict],
                   brand: str = "", auto_rotate: bool = False) -> dict:
    d = report.inspection_dir(rec_id)
    src = _find_source(d)
    zone_list = zones_mod.sanitize_zones(zone_list)
    zones_a, zones_b = _split_docs(zone_list)

    t0 = time.time()
    ocr_results = _read_all_docs(d, zones_a, zones_b, auto_rotate=auto_rotate)
    # Record the concrete angle actually applied back onto each OCR'd zone
    # so the saved report, overlay crops and OCR-review show what OCR read.
    # (ignore-type zones are not OCR'd → left as the user set them.)
    rot_by_id = {r["zone_id"]: r.get("rotate", 0) for r in ocr_results}
    for z in zone_list:
        if z["id"] in rot_by_id:
            z["rotate"] = rot_by_id[z["id"]]

    vocab_words: set = set()
    vocab_phrases: List[str] = []
    if brand:
        v = vocab.load(brand)
        vocab_words = set(v["words"])
        vocab_phrases = v["phrases"]

    defects = checks.run_all_checks(zone_list, ocr_results,
                                    vocab_words=vocab_words,
                                    vocab_phrases=vocab_phrases)

    _tag_highlight_risk(d, zone_list)

    preview = cv2.imread(os.path.join(d, "preview.png"))
    if preview is None:
        preview = ArtworkDocument(src).render(config.PREVIEW_DPI)
    overlay = report.draw_overlay(preview, zones_a, defects)
    cv2.imwrite(os.path.join(d, "overlay.png"), overlay)

    if zones_b:
        preview_b = cv2.imread(os.path.join(d, "preview_b.png"))
        if preview_b is None:
            preview_b = ArtworkDocument(
                _find_source(d, "source_b")).render(config.PREVIEW_DPI)
        overlay_b = report.draw_overlay(preview_b, zones_b, defects)
        cv2.imwrite(os.path.join(d, "overlay_b.png"), overlay_b)

    rep = {
        "id": rec_id,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "filename": os.path.basename(src),
        "brand": brand,
        "verdict": report.compute_verdict(defects),
        "summary": report.summarize(defects),
        "defects": defects,
        "zones": zone_list,
        "ocr": ocr_results,
        "elapsed_s": round(time.time() - t0, 2),
        "spell_layer_available": checks.spell_layer_available(),
        "ocr_available": ocr.is_ocr_available(),
    }
    if zones_b:
        # Cross-file compare was used — the report page shows both docs.
        rep["has_ref"] = True
        rep["filename_b"] = os.path.basename(_find_source(d, "source_b"))
    report.save_report(rec_id, rep)
    logger.info("[artwork] done %s verdict=%s defects=%d in %.1fs",
                rec_id, rep["verdict"], len(defects), rep["elapsed_s"])
    return rep


# ── OCR-only pass (advisory translate tab, BEFORE a full inspection) ──
# This lets the "ข้อความ + คำแปล" tab work without first pressing
# "ส่งตรวจสอบ". It deliberately does NOT run the check layers, draw an
# overlay, or write report.json — so it can never create or mutate an
# inspection verdict. It is fully isolated from run_inspection() above.
_OCR_ONLY_CACHE = "ocr_only.json"


def _zones_signature(zone_list: List[dict], auto_rotate: bool = False) -> str:
    """Stable hash of the zone layout (id/type/group/bbox/doc/rotate) plus
    the page auto-rotate flag, so a repeated translate request reuses the
    cached OCR only when nothing that changes the OCR input has changed."""
    sig = [{k: z.get(k) for k in ("id", "type", "group", "bbox", "doc",
                                  "rotate")}
           for z in zone_list]
    return hashlib.sha1(
        json.dumps({"z": sig, "auto": bool(auto_rotate)},
                   sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _load_ocr_cache(insp_dir: str, zone_list: List[dict],
                    auto_rotate: bool = False) -> Optional[List[dict]]:
    p = os.path.join(insp_dir, _OCR_ONLY_CACHE)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError):
        return None
    if data.get("sig") != _zones_signature(zone_list, auto_rotate):
        return None          # zones/flag changed → cache stale
    return data.get("ocr")


def _save_ocr_cache(insp_dir: str, zone_list: List[dict],
                    ocr_results: List[dict], auto_rotate: bool = False) -> None:
    try:
        with open(os.path.join(insp_dir, _OCR_ONLY_CACHE), "w",
                  encoding="utf-8") as f:
            json.dump({"sig": _zones_signature(zone_list, auto_rotate),
                       "ocr": ocr_results},
                      f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning("[artwork] could not cache ocr-only result: %s", e)


def run_ocr_only(rec_id: str, zone_list: List[dict],
                 auto_rotate: bool = False) -> Tuple[List[dict], List[dict]]:
    """
    Acquire per-zone text only (PDF text layer or N8N OCR) for the advisory
    translate tab, WITHOUT running any check layer or touching report.json /
    overlay. Returns (sanitized_zones, ocr_results). Caches the OCR output by
    zone-layout hash so clicking translate repeatedly does not re-OCR.
    ``auto_rotate`` is the page-level toggle (part of the cache key).
    """
    d = report.inspection_dir(rec_id)
    if not os.path.isdir(d):
        raise FileNotFoundError("ไม่พบรายการอัปโหลดนี้")
    zone_list = zones_mod.sanitize_zones(zone_list)

    cached = _load_ocr_cache(d, zone_list, auto_rotate)
    if cached is not None:
        return zone_list, cached

    zones_a, zones_b = _split_docs(zone_list)
    ocr_results = _read_all_docs(d, zones_a, zones_b, auto_rotate=auto_rotate)
    _save_ocr_cache(d, zone_list, ocr_results, auto_rotate)
    logger.info("[artwork] ocr-only %s zones=%d", rec_id, len(zone_list))
    return zone_list, ocr_results


def zone_crop_jpg(rec_id: str, zone_bbox: List[float],
                  dpi: Optional[int] = None, doc: str = "a",
                  rotate="0", highlight: str = "",
                  zone_id: str = "") -> bytes:
    """High-DPI crop of one zone — used by the UI defect table / preview.
    ``doc="b"`` crops from the attached reference file. ``rotate`` is an
    angle 0/90/180/270 or "auto" (detect + rotate vertical → upright), so
    the preview matches what OCR will actually receive.

    When ``highlight`` (a defect's problem word) and ``zone_id`` are given
    AND a saved report exists, the word is located in the crop and boxed
    in red. This is display-only: locating uses the saved OCR text/blocks
    of that zone, never re-runs a check, and any failure just returns the
    plain crop (identical to omitting ``highlight``)."""
    d = report.inspection_dir(rec_id)
    base = "source_b" if doc == "b" else "source"
    document = ArtworkDocument(_find_source(d, base))
    base_dpi = dpi or config.OCR_DPI
    crop = document.render_zone(zone_bbox, dpi=base_dpi, max_side=1600)
    # A SMALL zone renders small even at OCR_DPI (a 78 pt wide zone is only
    # ~490 px at 450 dpi). Tesseract goes blind at that size — measured on a
    # real station crop it read 0/8 target words at 488 px but 6/8 at 976 px
    # — and the human reviewer cannot read the crop either. For PDFs we can
    # get REAL extra detail by rendering the same zone at a higher dpi, so
    # do that instead of shipping a tiny image.
    if document.is_pdf and crop.size:
        longest = max(crop.shape[:2])
        if longest < config.CROP_MIN_SIDE:
            factor = min(4.0, config.CROP_MIN_SIDE / float(longest))
            crop = document.render_zone(zone_bbox, dpi=int(base_dpi * factor),
                                        max_side=1600)
    angle = resolve_rotation(rotate, page_auto=False, crop=crop) \
        if rotate == "auto" else (int(rotate) if str(rotate) in
                                  ("0", "90", "180", "270") else 0)
    if angle:
        crop = apply_rotation(crop, angle)

    if highlight and zone_id and config.HIGHLIGHT_DEFECT_WORD:
        crop = _highlight_crop(rec_id, crop, highlight, zone_id, angle)
    return encode_jpg(crop, quality=88)


def _highlight_crop(rec_id: str, crop, found: str, zone_id: str,
                    angle: int = 0):
    """Draw the red word-box on ``crop`` using the saved data of
    ``zone_id``. Strategy, most reliable first:
      ② exact PDF text-layer word box (when the zone was read from a live
         text layer — any script, no OCR);
      ①③ then hl.annotate (OCR-backend bbox → Tesseract).
    Isolated + fully guarded: any problem returns the crop untouched so the
    defect card still shows the plain image."""
    try:
        from . import highlight as hl
        rep = report.load_report(rec_id)
        if not rep:
            return crop
        entry = next((r for r in rep.get("ocr", [])
                      if r.get("zone_id") == zone_id), None)
        if entry is None:
            return crop

        if config.HIGHLIGHT_USE_PDF_TEXT and entry.get("engine") == "pdf-text":
            boxes = _pdf_text_boxes(rec_id, rep, zone_id, found, crop, angle)
            if boxes:
                return hl.draw_boxes(crop, boxes)

        return hl.annotate(crop, found, entry.get("text", ""),
                           entry.get("blocks"), entry.get("ocr_wh"),
                           use_tesseract=config.HIGHLIGHT_USE_TESSERACT,
                           use_profile=config.HIGHLIGHT_USE_PROFILE,
                           tess_lang=config.HIGHLIGHT_TESSERACT_LANG,
                           max_boxes=config.HIGHLIGHT_MAX_BOXES,
                           row_verify=config.HIGHLIGHT_ROW_VERIFY)
    except Exception:
        logger.debug("[artwork] highlight skipped for %s/%s",
                     rec_id, zone_id, exc_info=True)
        return crop


def _tag_highlight_risk(insp_dir: str, zone_list: List[dict]) -> None:
    """Mark zones whose crop will be too small/too wide for the red word
    box to work (``hl_risk`` = "wide" | "small"), so the report can tell
    the reviewer to redraw instead of leaving them wondering why a defect
    has no box. Advisory only — never touches text, checks or verdict.
    Silently does nothing if the page size cannot be read."""
    try:
        sizes = {}
        for z in zone_list:
            base = "source_b" if z.get("doc") == "b" else "source"
            if base not in sizes:
                doc = ArtworkDocument(_find_source(insp_dir, base))
                page = doc.render(36)          # tiny render just for aspect
                h, w = page.shape[:2]
                sizes[base] = (w / 36.0 * 72.0, h / 36.0 * 72.0)
            pw, ph = sizes[base]
            risk = zones_mod.highlight_risk(z["bbox"], pw, ph, config.OCR_DPI)
            if risk:
                z["hl_risk"] = risk
            else:
                z.pop("hl_risk", None)
    except Exception:
        logger.debug("[artwork] highlight-risk tagging skipped", exc_info=True)


def _pdf_text_boxes(rec_id: str, rep: dict, zone_id: str, found: str,
                    crop, angle: int) -> list:
    """Exact pixel boxes for EVERY occurrence of ``found`` in the PDF text
    layer of ``zone_id`` (layer ②), capped by HIGHLIGHT_MAX_BOXES. Returns
    [] when the source is not a text-layer PDF, the word isn't present, or
    anything is off → caller falls back to OCR."""
    from . import highlight as hl
    zone = next((z for z in rep.get("zones", [])
                 if z.get("id") == zone_id), None)
    if not zone or not isinstance(zone.get("bbox"), (list, tuple)):
        return []
    d = report.inspection_dir(rec_id)
    base = "source_b" if zone.get("doc") == "b" else "source"
    try:
        document = ArtworkDocument(_find_source(d, base))
    except FileNotFoundError:
        return []
    if not document.is_pdf:
        return []
    words = document.zone_words(list(zone["bbox"]))
    if not words:
        return []
    H, W = crop.shape[:2]
    out = []
    for fb in hl.match_word_boxes(words, found):
        px = hl.frac_to_px(hl.rotate_frac_box(fb, angle or 0), W, H)
        if px is not None:
            out.append(px)
    cap = config.HIGHLIGHT_MAX_BOXES
    if cap and cap > 0:
        out = out[:cap]
    return out


def _find_source(insp_dir: str, base: str = "source") -> str:
    for ext in ALLOWED_EXT:
        p = os.path.join(insp_dir, f"{base}{ext}")
        if os.path.exists(p):
            return p
    raise FileNotFoundError("ไม่พบไฟล์ต้นฉบับของการตรวจนี้"
                            if base == "source"
                            else "ไม่พบไฟล์อ้างอิง (ชิ้นงาน) ของการตรวจนี้")
