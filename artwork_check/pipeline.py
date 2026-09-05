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

from . import bands as bands_mod
from . import confirm as confirm_mod
from . import panelmatch as panelmatch_mod
from . import (checks, config, fonttrust, ocr, pixdiff, report, vocab,
               zones as zones_mod)
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
        "is_pdf": bool(doc.is_pdf),
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
        "is_pdf": bool(doc.is_pdf),
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


def font_trust(doc: ArtworkDocument) -> dict:
    """วิเคราะห์ความน่าเชื่อถือของ text layer **ต่อฟอนต์** ของเอกสารนี้.

    อ่าน span ทั้งเอกสารครั้งเดียว (~60-180 ms) แล้วส่งผลให้ ``read_zone``
    ใช้ซ้ำทุกโซน. ปิดด้วย ``ARTWORK_PDFTEXT_FONT_EVIDENCE=off``.
    """
    mode = config.PDFTEXT_FONT_EVIDENCE
    if mode == "off" or not getattr(doc, "is_pdf", False):
        return {"mode": "off", "suspect": [], "poisoned": "", "fonts": {}}
    try:
        spans = doc.text_spans(
            all_pages=True, max_pages=config.PDFTEXT_FONT_EVIDENCE_MAX_PAGES)
    except Exception:            # pragma: no cover - ตัวช่วย ไม่ใช่ทางหลัก
        logger.debug("[artwork] font-trust: อ่าน span ไม่ได้", exc_info=True)
        return {"mode": "off", "suspect": [], "poisoned": "", "fonts": {}}
    # หลักฐานทางที่ 2: ฟอนต์ที่ตามสเปกแล้วถอดกลับเป็น Unicode ไม่ได้ —
    # รู้ได้โดยไม่ต้องรอให้เห็นขยะสักตัว (ราคา ~6 ms)
    unmappable = []
    if config.PDFTEXT_FONT_STRUCTURE_CHECK:
        try:
            unmappable = doc.unmappable_fonts(
                max_pages=config.PDFTEXT_FONT_EVIDENCE_MAX_PAGES)
        except Exception:        # pragma: no cover - ตัวช่วย ไม่ใช่ทางหลัก
            logger.debug("[artwork] font-trust: อ่านตารางฟอนต์ไม่ได้",
                         exc_info=True)
    trust = fonttrust.analyze(spans, mode=mode, unmappable_fonts=unmappable)
    if trust.get("suspect"):
        logger.warning(
            "[artwork] ฟอนต์ที่เชื่อข้อความไม่ได้ในไฟล์นี้: %s (โหมด %s · "
            "หลักฐาน %s) — ข้อความของฟอนต์เหล่านี้จะไม่ถูกใช้จาก text layer",
            ", ".join(trust["suspect"]), trust.get("mode"),
            trust.get("why"))
    return trust


def _read_all_docs(insp_dir: str, zones_a: List[dict], zones_b: List[dict],
                   auto_rotate: bool = False,
                   force_ocr: bool = False,
                   split_bands: bool = False) -> Tuple[List[dict], dict]:
    """OCR each zone against ITS OWN document (a → source, b → source_b).
    With no doc-"b" zones this is exactly the original single-doc path.
    ``auto_rotate`` is the page-level toggle passed through to the OCR
    layer (only affects zones with rotate == "default"). ``force_ocr``
    ข้ามชั้น text layer ทั้งใบ (ผู้ใช้สั่งเอง).

    คืน ``(ocr_results, {doc: font_trust})`` — ส่งหลักฐานระดับฟอนต์ออกมาด้วย
    แทนที่จะเก็บในตัวแปรระดับโมดูล เพราะ Flask รันแบบ ``threaded=True``
    การตรวจสองใบพร้อมกันจะเขียนทับกันได้."""
    docs = {"a": ArtworkDocument(_find_source(insp_dir))}
    trust = {"a": font_trust(docs["a"])}
    results = ocr.read_all_zones(docs["a"], zones_a, page_auto=auto_rotate,
                                 force_ocr=force_ocr,
                                 split_bands=split_bands,
                                 font_trust=trust["a"])
    if zones_b:
        try:
            src_b = _find_source(insp_dir, "source_b")
        except FileNotFoundError:
            raise ValueError(
                "มีโซนของไฟล์อ้างอิง (ชิ้นงาน) แต่ยังไม่ได้แนบไฟล์อ้างอิง — "
                "แนบไฟล์อ้างอิง หรือลบโซนเหล่านั้นก่อนส่งตรวจ")
        docs["b"] = ArtworkDocument(src_b)
        # ไฟล์อ้างอิงเป็นคนละเอกสาร ⇒ หลักฐานของมันต้องแยกกัน (ฟอนต์ชื่อ
        # เดียวกันในสองไฟล์อาจ subset คนละชุด — นั่นคือต้นเรื่องของทั้งหมดนี้)
        trust["b"] = font_trust(docs["b"])
        results += ocr.read_all_zones(docs["b"], zones_b,
                                      page_auto=auto_rotate,
                                      force_ocr=force_ocr,
                                      split_bands=split_bands,
                                      font_trust=trust["b"])
    if not force_ocr and config.OCR_GROUP_ENGINE_CONSISTENCY:
        results = _unify_group_engines(docs, zones_a + zones_b, results,
                                       auto_rotate)
    return results, trust


def _unify_group_engines(docs: dict, zone_list: List[dict],
                         results: List[dict],
                         auto_rotate: bool) -> List[dict]:
    """อ่านโซนที่ใช้ text layer ซ้ำด้วย OCR เมื่อกลุ่มของมัน engine ปนกัน.

    เปิดด้วย ``OCR_GROUP_ENGINE_CONSISTENCY`` เท่านั้น (default ปิด).

    ⚠️ กติกาสำคัญ: **ถ้าอ่านซ้ำแล้วได้ผลที่แย่กว่าเดิม (error หรือข้อความ
    ว่าง) ให้เก็บผลจาก text layer ไว้เหมือนเดิม** — การทิ้งข้อความที่เป๊ะ
    100% ไปแลกกับ "อ่านไม่ได้" คือการทำให้แย่ลง ไม่ใช่ทำให้สม่ำเสมอ
    """
    if not ocr.is_ocr_available():
        return results
    mixed = set(checks.engine_mix_groups(zone_list, results))
    if not mixed:
        return results
    by_id = {z["id"]: z for z in zone_list}
    out = []
    for r in results:
        z = by_id.get(r["zone_id"])
        g = (z.get("group") or "").strip() if z else ""
        if not z or g not in mixed or r.get("engine") != "pdf-text":
            out.append(r)
            continue
        doc = docs.get("b" if z.get("doc") == "b" else "a")
        if doc is None:
            out.append(r)
            continue
        again = ocr.read_zone(doc, z, page_auto=auto_rotate, force_ocr=True)
        if again.get("error") or not (again.get("text") or "").strip():
            # อ่านซ้ำไม่สำเร็จ — คงข้อความเดิมของ text layer ไว้ แล้วบอกไว้
            r = dict(r)
            r["note"] = " · ".join(x for x in (
                r.get("note"),
                "พยายามอ่านซ้ำด้วย OCR เพื่อให้ engine ตรงกันทั้งกลุ่ม %s "
                "แต่ไม่สำเร็จ — ใช้ค่าจาก text layer ตามเดิม" % g) if x)
            logger.warning("[artwork] zone %s: group-engine re-read failed, "
                           "keeping pdf-text", r["zone_id"])
            out.append(r)
            continue
        again["note"] = " · ".join(x for x in (
            again.get("note"),
            "อ่านซ้ำด้วย OCR เพื่อให้เทียบกับโซนอื่นในกลุ่ม %s ด้วย engine "
            "เดียวกัน" % g) if x)
        logger.info("[artwork] zone %s: re-read with OCR for group %s "
                    "engine consistency", again["zone_id"], g)
        out.append(again)
    return out


def _pixel_compare(insp_dir: str, zone_list: List[dict],
                   defects: List[dict]):
    """โหมดทดลอง: เทียบ "แผงต่อแผง" ระดับพิกเซลแทนชั้นเทียบข้อความ.

    ทำเฉพาะกลุ่มที่มีโซนชนิด panel **สองโซนพอดี** และทั้งคู่มาจากไฟล์ PDF —
    กลุ่มอื่นและชั้นตรวจอื่น (ตัวเลข · บาร์โค้ด · อ่านไม่ออก) **ไม่ถูกแตะ**

    ⚠️ เทียบไม่ได้ (คนละเนื้อหา / เรนเดอร์ไม่ได้) ⇒ **คงผลชั้นข้อความของ
       กลุ่มนั้นไว้ทั้งหมด** ห้ามทิ้ง coverage เพราะเราเทียบไม่ได้เอง
    """
    srcs = {"a": _find_source(insp_dir)}
    try:
        srcs["b"] = _find_source(insp_dir, "source_b")
    except FileNotFoundError:
        pass

    by_group = {}
    for z in zone_list:
        if z.get("type") != "panel":
            continue
        by_group.setdefault(z.get("group") or "", []).append(z)

    replaced_groups = set()
    new_defects: List[dict] = []
    pairs = []
    for g, zs in sorted(by_group.items()):
        if not g or len(zs) != 2:
            continue
        za, zb = zs
        pa, pb = srcs.get(za.get("doc", "a")), srcs.get(zb.get("doc", "a"))
        if not pa or not pb:
            continue
        if not (ArtworkDocument(pa).is_pdf and ArtworkDocument(pb).is_pdf):
            pairs.append({"group": g, "status": "skipped",
                          "reason": "not_pdf", "regions": 0})
            continue
        res, img_a, img_b = panelmatch_mod.compare_ex(
            pa, za["bbox"], pb, zb["bbox"])
        # ⬇️ ตัวเลขทุกตัวที่ใช้ "พัฒนาต่อ" ต้องไปถึงหน้าจอ ไม่ใช่ให้เดาจาก
        #    จำนวน defect (ข้อกำหนดผู้ใช้ 5 ก.ย.) — โดยเฉพาะ ``ecc``
        #    (คุณภาพการทาบภาพ) และ ``edge_regions`` (ของที่ลากเกินแผงเข้ามา)
        #    ⚠️ ``ncc`` **ห้ามใช้ตัดสิน** — วัดแล้วได้ 1.0000 ในเคสที่ผลมั่ว
        entry = {"group": g, "status": res.get("status"),
                 "reason": res.get("reason", ""),
                 "regions": len(res.get("regions") or []),
                 "edge_regions": res.get("edge_regions"),
                 "areas_mm2": res.get("areas_mm2"),
                 "diff_ratio": res.get("diff_ratio"),
                 "min_region_mm2": res.get("min_region_mm2"),
                 "mm_per_px": res.get("mm_per_px"),
                 "size": res.get("size"), "dpi": res.get("dpi"),
                 "scale": res.get("scale"), "ncc": res.get("ncc"),
                 "ecc": res.get("ecc")}
        pairs.append(entry)
        if res.get("status") != pixdiff.OK or img_a is None:
            continue                       # เทียบไม่ได้ ⇒ ใช้ผลชั้นข้อความเดิม

        def _read(which, px, _a=img_a, _b=img_b):
            """อ่านข้อความเฉพาะบริเวณที่ต่าง — ครอปเล็ก ๆ อ่านได้นิ่งกว่ามาก.
            อ่านไม่ได้ = คืนค่าว่าง **ห้ามเดา** (กฎเหล็กข้อ 2)"""
            img = _a if which == "a" else _b
            pad = 14
            x, y, w, h = px
            y0, y1 = max(0, y - pad), min(img.shape[0], y + h + pad)
            x0, x1 = max(0, x - pad), min(img.shape[1], x + w + pad)
            crop = img[y0:y1, x0:x1]
            if crop.size == 0:
                return ""
            r = ocr.read_image(crop)
            return (r or {}).get("text", "")

        found = panelmatch_mod.regions_to_defects(res, za, zb, _read)
        # ⚠️ **พบ 0 บริเวณ ห้ามลบผลชั้นข้อความ** — "ภาพไม่เห็น" ไม่ใช่ "ไม่มี"
        #    เกิดจริงบนสถานี 5 ก.ย.: แผงเล็กทำให้ความต่างเหลือ 5 พิกเซล ⇒
        #    พบ 0 บริเวณ ⇒ ลบ MISMATCH ของชั้นข้อความทิ้ง ⇒ **รายงานขึ้น 0 ทุกช่อง
        #    ทั้งที่ OCR สองฝั่งอ่าน 20% กับ 24% ต่างกันชัด ๆ** (กฎเหล็กข้อ 2)
        #    ⇒ แทนที่ได้ก็ต่อเมื่อชั้นภาพ "มีอะไรจะพูด" เท่านั้น
        if not found:
            entry["kept_text_layer"] = True
            continue
        new_defects += found
        replaced_groups.add(g)

    if not replaced_groups:
        return defects, {"pairs": pairs, "used": 0}

    # แทนที่เฉพาะ MISMATCH_* ของกลุ่มที่เทียบพิกเซลสำเร็จ — คลาสอื่นคงเดิม
    ids = {z["id"] for z in zone_list
           if (z.get("group") or "") in replaced_groups}
    kept = [x for x in defects
            if not (str(x.get("class", "")).startswith("MISMATCH_")
                    and x.get("zone_id") in ids)]
    return kept + new_defects, {"pairs": pairs, "used": len(replaced_groups)}


def run_inspection(rec_id: str, zone_list: List[dict],
                   brand: str = "", auto_rotate: bool = False,
                   force_ocr: bool = False,
                   split_bands: bool = False,
                   confirm_reads: bool = False,
                   pixel_check: bool = False) -> dict:
    d = report.inspection_dir(rec_id)
    src = _find_source(d)
    zone_list = zones_mod.sanitize_zones(zone_list)
    zones_a, zones_b = _split_docs(zone_list)

    t0 = time.time()
    ocr_results, trust = _read_all_docs(d, zones_a, zones_b,
                                        auto_rotate=auto_rotate,
                                        force_ocr=force_ocr,
                                        split_bands=split_bands)
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

    def _checks(res):
        return checks.run_all_checks(zone_list, res,
                                     vocab_words=vocab_words,
                                     vocab_phrases=vocab_phrases)

    defects = _checks(ocr_results)
    confirm_info = None
    if confirm_reads:
        # โหมดทดลอง: อ่านซ้ำอีกรอบด้วยเส้นทางเดียวกันเป๊ะ แล้วเชื่อเฉพาะ
        # defect ที่โผล่ทั้งสองรอบ. เป็นการ **กรอง** ไม่ใช่การสร้างใหม่ ⇒
        # ผลที่แสดงกับผู้ใช้หน้าตาเหมือนเดิมทุกประการ แค่เหลือน้อยลง
        # (เหตุผลเชิงตัวเลขทั้งหมดอยู่ใน artwork_check/confirm.py)
        try:
            ocr_2, _ = _read_all_docs(d, zones_a, zones_b,
                                      auto_rotate=auto_rotate,
                                      force_ocr=force_ocr,
                                      split_bands=split_bands)
            r2 = _checks(ocr_2)
            n1 = len(defects)
            defects, unconfirmed = confirm_mod.confirm([defects, r2])
            confirm_info = confirm_mod.summary(defects, unconfirmed, 2,
                                               [n1, len(r2)])
        except Exception:
            # อ่านรอบสองไม่สำเร็จ = ยืนยันไม่ได้ ⇒ **คงผลรอบแรกไว้ทั้งหมด**
            # (ห้ามทิ้ง defect เพราะเหตุขัดข้องของเราเอง) พร้อมบอกให้เห็น
            logger.exception("[artwork] อ่านรอบยืนยันไม่สำเร็จ — ใช้ผลรอบเดียว")
            confirm_info = {"rounds": 1, "confirmed": len(defects),
                            "unconfirmed": 0, "items": [],
                            "error": "อ่านรอบที่สองไม่สำเร็จ — ผลนี้มาจากการอ่านรอบเดียว"}

    pixel_info = None
    if pixel_check:
        try:
            defects, pixel_info = _pixel_compare(d, zone_list, defects)
        except Exception:
            logger.exception("[artwork] เทียบพิกเซลไม่สำเร็จ — ใช้ผลชั้นข้อความ")
            pixel_info = {"pairs": [], "used": 0,
                          "error": "เทียบพิกเซลไม่สำเร็จ — ผลนี้มาจากชั้นข้อความเหมือนเดิม"}

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
        # ชั้นไหน "ได้ทำงานจริง" กับงานใบนี้ — advisory ล้วน คำนวณ *หลัง*
        # ได้ defects แล้ว จึงไม่มีทางกระทบ verdict/การนับ. ต้องมีเพราะ
        # PASS ไม่ได้แปลว่าตรวจครบ (ดู checks.check_coverage)
        "coverage": checks.check_coverage(zone_list, ocr_results),
        # อ่านทั้งใบด้วย OCR ตามที่ผู้ใช้สั่งหรือไม่ — บันทึกไว้เพื่อให้อ่าน
        # รายงานย้อนหลังแล้วรู้ว่าข้อความมาจากเส้นทางไหน
        "force_ocr": bool(force_ocr),
        "split_bands": bool(split_bands),
        # โหมดยืนยันด้วยการอ่านซ้ำ — advisory ล้วน. defect ที่ "ยังไม่ยืนยัน"
        # ต้องแสดงให้ผู้ตรวจเห็น ไม่ใช่ทิ้งเงียบ ๆ (กฎเหล็กข้อ 2)
        "confirm_reads": bool(confirm_reads),
        "confirm": confirm_info,
        # โหมดเทียบพิกเซล — บอกว่ากลุ่มไหนใช้ผลจากภาพแทนชั้นข้อความ
        "pixel_check": bool(pixel_check),
        "pixel": pixel_info,
        # ฟอนต์ที่ text layer เชื่อไม่ได้ — ผู้ตรวจเอาไปบอกคนทำ artwork ได้ว่า
        # ต้อง export ไฟล์ใหม่ (ต้นเหตุจริงอยู่ที่ขั้นตอนนั้น ไม่ใช่ที่ระบบนี้)
        "font_trust": {k: fonttrust.summary(v) for k, v in trust.items()},
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


def _ocr_fingerprint() -> dict:
    """ค่าตั้งทุกตัวที่ "เปลี่ยนแล้วข้อความที่ OCR อ่านได้จะเปลี่ยน".

    ต้องอยู่ใน cache key ด้วย ไม่ใช่แค่ layout ของโซน — ไม่งั้นการแก้ค่า
    เหล่านี้ (หรือ deploy โค้ดที่แก้เส้นทางอ่าน) จะไม่ทำให้ cache หลุด แล้ว
    แท็บ "ข้อความ + คำแปล" จะเสิร์ฟข้อความเก่าต่อไปเรื่อย ๆ ทั้งที่ระบบ
    อ่านได้ดีขึ้นแล้ว — เงียบและหาสาเหตุยากมาก.

    เคสจริงที่ทำให้ต้องเพิ่ม: การเปิด OCR_CROP_MIN_SIDE ทำให้ไฟล์ที่ถูกย่อ
    ลง A4 อ่านได้จาก 1.2% เป็น 97.6% แต่งานที่เคยกดแปลไปแล้วจะยังได้ข้อความ
    ชุดเก่า เพราะ layout โซนไม่ได้เปลี่ยน.
    """
    return {
        "dpi": config.OCR_DPI,
        "max_side": config.OCR_CROP_MAX_SIDE,
        "min_side": config.OCR_CROP_MIN_SIDE,
        "dpi_max_factor": config.OCR_DPI_MAX_FACTOR,
        "embed_min": config.EMBEDDED_TEXT_MIN_CHARS,
        "garbled": bool(config.PDFTEXT_GARBLED_CHECK),
        "garbled_tokens": config.PDFTEXT_GARBLED_MIN_TOKENS,
        "garbled_ratio": config.PDFTEXT_GARBLED_RATIO,
        # ด่านอักขระต้องห้าม — เปลี่ยนค่าแล้วโซนที่เคยใช้ text layer อาจ
        # ตกไปใช้ OCR (หรือกลับกัน) ⇒ ข้อความที่ได้เปลี่ยน ⇒ cache ต้องหลุด
        "bad_glyph": bool(config.PDFTEXT_BAD_GLYPH_CHECK),
        "bad_glyph_min": config.PDFTEXT_BAD_GLYPH_MIN_COUNT,
        # engine consistency ต่อกลุ่ม — เปลี่ยนแล้วบางโซนถูกอ่านซ้ำด้วย OCR
        "group_engine": bool(config.OCR_GROUP_ENGINE_CONSISTENCY),
        # หลักฐานระดับฟอนต์ — เปลี่ยนโหมดแล้วโซนที่เคยใช้ text layer อาจ
        # ตกไปใช้ OCR (หรือกลับกัน) ⇒ ข้อความที่ได้เปลี่ยน
        "font_evidence": config.PDFTEXT_FONT_EVIDENCE,
        "font_structure": bool(config.PDFTEXT_FONT_STRUCTURE_CHECK),
        # โหมดหั่นแถบ (ตัวสวิตช์เป็น per-request อยู่ใน _zones_signature
        # แล้ว — ที่นี่คือ "ค่าจูนการหั่น" ซึ่งเปลี่ยนแล้วได้แถบคนละชุด
        # ⇒ ข้อความที่อ่านได้เปลี่ยน ⇒ cache ต้องหลุด)
        "band_target": bands_mod.BAND_TARGET_PX,
        "band_min": bands_mod.BAND_MIN_PX,
        "band_max": bands_mod.MAX_BANDS,
        "band_quiet": bands_mod.QUIET_RATIO,
    }


def _zones_signature(zone_list: List[dict], auto_rotate: bool = False,
                     force_ocr: bool = False,
                     split_bands: bool = False) -> str:
    """Stable hash of the zone layout (id/type/group/bbox/doc/rotate), the
    page auto-rotate flag, the force-OCR flag, AND the OCR settings that
    decide what the text acquisition step will produce — so a repeated
    translate request reuses the cached OCR only when nothing that changes
    the OCR input has changed."""
    sig = [{k: z.get(k) for k in ("id", "type", "group", "bbox", "doc",
                                  "rotate")}
           for z in zone_list]
    return hashlib.sha1(
        json.dumps({"z": sig, "auto": bool(auto_rotate),
                    "force_ocr": bool(force_ocr),
                    "split_bands": bool(split_bands),
                    "ocr": _ocr_fingerprint()},
                   sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _load_ocr_cache(insp_dir: str, zone_list: List[dict],
                    auto_rotate: bool = False,
                    force_ocr: bool = False,
                    split_bands: bool = False) -> Optional[List[dict]]:
    p = os.path.join(insp_dir, _OCR_ONLY_CACHE)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError):
        return None
    if data.get("sig") != _zones_signature(zone_list, auto_rotate, force_ocr,
                                          split_bands):
        return None          # zones/flag changed → cache stale
    return data.get("ocr")


def _save_ocr_cache(insp_dir: str, zone_list: List[dict],
                    ocr_results: List[dict], auto_rotate: bool = False,
                    force_ocr: bool = False,
                    split_bands: bool = False) -> None:
    try:
        with open(os.path.join(insp_dir, _OCR_ONLY_CACHE), "w",
                  encoding="utf-8") as f:
            json.dump({"sig": _zones_signature(zone_list, auto_rotate,
                                               force_ocr, split_bands),
                       "ocr": ocr_results},
                      f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning("[artwork] could not cache ocr-only result: %s", e)


# ── Pixel diff (advisory) — เทียบฉบับใหม่กับฉบับอ้างอิงระดับพิกเซล ──
# ⚠️ **ไม่ถูกเรียกจาก run_inspection เด็ดขาด** — ผู้ใช้ต้องกดปุ่มเอง.
# ไม่แตะ defects / verdict / summary / การนับ / DB และไม่เขียน report.json
PIXDIFF_FILE = "pixdiff.json"


def run_pixdiff(rec_id: str, zone_list: List[dict]) -> dict:
    """เทียบไฟล์หลัก (ฉบับใหม่) กับไฟล์อ้างอิง (ฉบับเก่า) ระดับพิกเซล.

    จับคู่โซนด้วย ``group`` — ชุดเดียวกับที่ชั้นเทียบข้ามไฟล์ใช้อยู่แล้ว
    ⇒ ผู้ใช้ไม่ต้องเรียนรู้กติกาใหม่. โซนที่ไม่มีคู่จะถูกรายงานว่าข้าม
    พร้อมเหตุผล ไม่ใช่เงียบหายไป.
    """
    d = report.inspection_dir(rec_id)
    src_a = _find_source(d)
    try:
        src_b = _find_source(d, "source_b")
    except FileNotFoundError:
        return {"status": "no_ref",
                "message": "ยังไม่ได้แนบไฟล์อ้างอิง (ฉบับเก่า) — "
                           "อัปโหลดที่ช่อง 'ไฟล์อ้างอิง' ก่อน",
                "zones": []}

    zone_list = zones_mod.sanitize_zones(zone_list)
    zones_a, zones_b = _split_docs(zone_list)
    by_group_b = {}
    for z in zones_b:
        g = (z.get("group") or "").strip()
        if g:
            by_group_b.setdefault(g, z)

    t0 = time.time()
    out_zones = []
    for za in zones_a:
        if za.get("type") == "ignore":
            continue
        g = (za.get("group") or "").strip()
        zb = by_group_b.get(g)
        if not zb:
            out_zones.append({
                "zone_id": za["id"], "group": g, "status": "skipped",
                "reason": "no_pair",
                "message": "ไม่มีโซนของไฟล์อ้างอิงที่ตั้ง 'กลุ่ม' ตรงกัน "
                           "— ลากโซนบนไฟล์อ้างอิงแล้วตั้งกลุ่มเป็น '%s'" % (g or "?"),
                "regions": []})
            continue
        res = pixdiff.compare_zone(src_a, za["bbox"], src_b, zb["bbox"],
                                   dpi=config.PIXDIFF_DPI)
        res["zone_id"] = za["id"]
        res["ref_zone_id"] = zb["id"]
        res["group"] = g
        res["label"] = za.get("label") or za["id"]
        # เก็บ bbox ไว้ในผลเลย — ภาพกรอบส้มจะได้ไม่ต้องพึ่ง report.json
        # ซึ่งอาจยังไม่มี (ผู้ใช้กดเทียบพิกเซลก่อนกดส่งตรวจสอบได้)
        res["bbox"] = [float(v) for v in za["bbox"]]
        out_zones.append(res)

    n_cmp = sum(1 for z in out_zones if z.get("status") == pixdiff.OK)
    n_diff = sum(1 for z in out_zones
                 if z.get("status") == pixdiff.OK and z.get("region_count"))
    rep = {
        "status": "ok",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_s": round(time.time() - t0, 2),
        "dpi": config.PIXDIFF_DPI,
        "filename_a": os.path.basename(src_a),
        "filename_b": os.path.basename(src_b),
        "compared": n_cmp,
        "with_diff": n_diff,
        "skipped": len(out_zones) - n_cmp,
        "zones": out_zones,
    }
    try:
        with open(os.path.join(d, PIXDIFF_FILE), "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
    except OSError as e:                       # บันทึกไม่ได้ก็ยังคืนผลได้
        logger.warning("[artwork] could not save pixdiff result: %s", e)
    logger.info("[artwork] pixdiff %s: เทียบ %d โซน · พบต่าง %d · ข้าม %d (%.1fs)",
                rec_id, n_cmp, n_diff, rep["skipped"], rep["elapsed_s"])
    return rep


def load_pixdiff(rec_id: str) -> Optional[dict]:
    """ผลเทียบพิกเซลครั้งล่าสุด (ถ้ามี) — ใช้ตอนเปิดดูรายงานย้อนหลัง"""
    p = os.path.join(report.inspection_dir(rec_id), PIXDIFF_FILE)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def pixdiff_zone_png(rec_id: str, zone_id: str) -> Optional[bytes]:
    """ภาพโซนฝั่งฉบับใหม่ + กรอบส้มชี้บริเวณที่ต่าง (display-only)"""
    rep = load_pixdiff(rec_id)
    if not rep:
        return None
    z = next((x for x in rep.get("zones", []) if x.get("zone_id") == zone_id), None)
    if not z or z.get("status") != pixdiff.OK or not z.get("bbox"):
        return None
    d = report.inspection_dir(rec_id)
    img, _mpp = pixdiff.render_zone_mm(_find_source(d), z["bbox"],
                                       config.PIXDIFF_DPI)
    if img is None:
        return None
    out = pixdiff.draw_regions(img, z.get("regions") or [])
    ok, buf = cv2.imencode(".png", out)
    return buf.tobytes() if ok else None


def run_ocr_only(rec_id: str, zone_list: List[dict],
                 auto_rotate: bool = False,
                 force_ocr: bool = False,
                 split_bands: bool = False) -> Tuple[List[dict], List[dict]]:
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

    cached = _load_ocr_cache(d, zone_list, auto_rotate, force_ocr,
                             split_bands)
    if cached is not None:
        return zone_list, cached

    zones_a, zones_b = _split_docs(zone_list)
    ocr_results, _trust = _read_all_docs(d, zones_a, zones_b,
                                         auto_rotate=auto_rotate,
                                         force_ocr=force_ocr,
                                         split_bands=split_bands)
    _save_ocr_cache(d, zone_list, ocr_results, auto_rotate, force_ocr,
                    split_bands)
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
