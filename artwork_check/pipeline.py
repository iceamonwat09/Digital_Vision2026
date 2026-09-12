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
import threading
import time
from collections import OrderedDict
from typing import List, Optional, Tuple

import cv2

from . import bands as bands_mod
from . import confirm as confirm_mod
from . import appearance
from . import panelmatch as panelmatch_mod
from . import progress as progress_mod
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


def _read_pair(crop_a, crop_b):
    """อ่านข้อความของครอปสองฝั่ง **พร้อมกัน** → ``(text_a, text_b)``.

    เป็นการรอ backend ตอบล้วน ๆ ไม่มี state ร่วม ⇒ ยิงพร้อมกันได้ผลเท่าเดิม
    ทุกประการ แค่เร็วขึ้นเท่าตัว. ``OCR_PARALLEL <= 1`` = ทางเดิม (เรียงกัน)
    """
    def _rd(c):
        return (ocr.read_image(c) or {}).get("text", "")
    if int(getattr(config, "OCR_PARALLEL", 1) or 1) <= 1:
        return _rd(crop_a), _rd(crop_b)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as ex:
        fa = ex.submit(_rd, crop_a)
        fb = ex.submit(_rd, crop_b)
        return fa.result(), fb.result()


def _pixel_untrusted(res: dict) -> Optional[str]:
    """ผลชั้นภาพชุดนี้ดีพอจะ **แทนที่** ผลชั้นข้อความไหม — ไม่ใช่ก็คืนเหตุผล

    ⚠️ ตัวเลขทั้งสองตัวเป็นค่าที่รายงานแสดงอยู่แล้ว ไม่ใช่เกณฑ์ที่คิดขึ้นใหม่:
       ``diff_ratio`` คือค่าเดียวกับที่หน้าจอเตือนว่า "สูงกว่าการแก้ไขฉลาก
       ปกติมาก" อยู่แล้ว ⇒ ถ้าระบบเตือนตัวเองว่าผลอาจไม่ครบ มันก็ไม่ควร
       เอาผลนั้นไปลบผลของชั้นอื่นทิ้ง (กฎเหล็กข้อ 2)
    """
    if not config.PIXEL_TRUST_GATE:
        return None
    dr = res.get("diff_ratio")
    if dr is not None and float(dr) > config.PIXEL_TRUST_MAX_DIFF:
        return "ต่างกัน %.2f%% ซึ่งสูงกว่าการแก้ไขฉลากปกติมาก" % (100.0 * float(dr))
    ecc = res.get("ecc")
    if ecc is not None and float(ecc) < config.PIXEL_TRUST_MIN_ECC:
        return "คุณภาพการทาบต่ำ (%.4f)" % float(ecc)
    return None


def _pixel_compare(insp_dir: str, zone_list: List[dict],
                   defects: List[dict], progress=None, deadline=None):
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

    pg = progress or progress_mod.NullRun()
    replaced_groups = set()
    new_defects: List[dict] = []
    pairs = []
    stopped = ""
    for g, zs in sorted(by_group.items()):
        if deadline and time.time() > deadline:
            # หมดเวลารวม ⇒ หยุดเฉพาะชั้นนี้ **ไม่ทิ้งผลที่ได้แล้ว**
            stopped = "หมดเวลารวมของการตรวจ — หยุดที่กลุ่ม %s" % g
            pairs.append({"group": g, "status": "skipped",
                          "reason": "timeout", "regions": 0})
            continue
        pg.start("pixel", "กำลังเทียบกลุ่ม %s" % g)
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
        # ── ชั้นภาพใช้ "มุมหมุนของโซน" ตัวเดียวกับที่ชั้น OCR ใช้ ────
        # ค่านี้ถูกเขียนกลับเป็นองศาจริง (int) ก่อนถึงตรงนี้แล้ว ส่วนโซนที่
        # ยังเป็น "default"/"auto" (ไม่เคยผ่าน OCR) ให้ถือเป็น 0 = ทางเดิม
        ra = za.get("rotate") if isinstance(za.get("rotate"), int) else 0
        rb = zb.get("rotate") if isinstance(zb.get("rotate"), int) else 0
        res, img_a, img_b = panelmatch_mod.compare_ex(
            pa, za["bbox"], pb, zb["bbox"], rotate_a=ra, rotate_b=rb)
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
                 "ecc": res.get("ecc"),
                 # แผงสองฝั่งขนาดต่างกันกี่เท่า + ปรับให้แล้วหรือยัง
                 "zone_ratio": res.get("zone_ratio"),
                 "prescaled": res.get("prescaled"),
                 "dpi_a": res.get("dpi_a"), "dpi_b": res.get("dpi_b"),
                 # มุมที่ชั้นภาพใช้จริง (มาจากค่าของโซน = ที่ OCR ใช้)
                 "rotate_a": res.get("rotate_a"),
                 "rotate_b": res.get("rotate_b"),
                 # ความหนาหมึกของทั้งแผง — อธิบายว่าทำไมบริเวณถึงเยอะ
                 "panel_ink": res.get("panel_ink")}
        pairs.append(entry)
        if res.get("status") != pixdiff.OK or img_a is None:
            continue                       # เทียบไม่ได้ ⇒ ใช้ผลชั้นข้อความเดิม

        def _inspect(px, _a=img_a, _b=img_b):
            """ตรวจบริเวณที่ต่างหนึ่งจุด — อ่านข้อความสองฝั่ง + วัดรูปลักษณ์.

            ⚠️ **ต้องขยายกรอบให้ถึงขอบคำก่อนอ่าน** — กรอบที่ได้จากการเทียบ
               ครอบเฉพาะ *พิกเซลที่ต่าง* ซึ่งกรณีฟอนต์ต่างจะกระจุกอยู่บาง
               ส่วนของคำ ⇒ อ่านตามกรอบตรง ๆ ได้ข้อความไม่ครบ (วัดจริงบน
               สถานี: ได้ ``Manuf``/``Manufa`` แทน ``Manufacturing``)
               ``expand_box`` ขยายเฉพาะที่ว่างทั้งสองฝั่ง ⇒ ใช้ได้ทุกภาษา
               และไม่มีทางกินคำข้าง ๆ
            """
            if deadline and time.time() > deadline:
                return None                 # หมดเวลา ⇒ ไม่อ่าน แต่ยังรายงาน
            box = appearance.expand_box(_a, _b, px)
            ca, cb = appearance.crop(_a, box), appearance.crop(_b, box)
            if getattr(ca, "size", 0) == 0 or getattr(cb, "size", 0) == 0:
                return None
            # อ่านสองฝั่งพร้อมกัน — เป็นการรอ backend ล้วน ไม่มี state ร่วม
            ta, tb = _read_pair(ca, cb)
            rel = appearance.relation(ta, tb)
            # วัดรูปลักษณ์เฉพาะตอนที่ตัวอักษรเหมือนกัน — ถ้าตัวอักษรต่างกัน
            # อยู่แล้ว ตัวเลขความหนา/ขนาดไม่ได้บอกอะไรเพิ่ม
            look = appearance.look_delta(ca, cb) if rel == "same" else None
            return {"a": ta, "b": tb, "relation": rel, "look": look,
                    "box": box}

        found = panelmatch_mod.regions_to_defects(
            res, za, zb, inspect_region=_inspect,
            max_inspect=config.PIXEL_MAX_OCR_REGIONS)
        n_reg = len(res.get("regions") or [])
        cap = int(config.PIXEL_MAX_OCR_REGIONS or 0)
        if cap and n_reg > cap:
            entry["ocr_capped"] = [cap, n_reg]
        # ⚠️ **พบ 0 บริเวณ ห้ามลบผลชั้นข้อความ** — "ภาพไม่เห็น" ไม่ใช่ "ไม่มี"
        #    เกิดจริงบนสถานี 5 ก.ย.: แผงเล็กทำให้ความต่างเหลือ 5 พิกเซล ⇒
        #    พบ 0 บริเวณ ⇒ ลบ MISMATCH ของชั้นข้อความทิ้ง ⇒ **รายงานขึ้น 0 ทุกช่อง
        #    ทั้งที่ OCR สองฝั่งอ่าน 20% กับ 24% ต่างกันชัด ๆ** (กฎเหล็กข้อ 2)
        #    ⇒ แทนที่ได้ก็ต่อเมื่อชั้นภาพ "มีอะไรจะพูด" เท่านั้น
        # ── ด่านความน่าเชื่อถือของชั้นภาพเอง ────────────────────────
        #
        # ชั้นภาพกับชั้นข้อความ **จับคนละอย่าง** — ชั้นภาพเห็นฟอนต์หนา-บาง
        # ที่ OCR มองไม่เห็น ส่วนชั้นข้อความทนการที่ข้อความไหลใหม่ ⇒ ไม่มี
        # ชั้นไหนดีกว่าเสมอ. การให้ชั้นหนึ่ง "ลบ" อีกชั้นทิ้งจึงต้องมีเงื่อนไข
        #
        # เกิดจริงบนสถานี 9 ก.ย.: คู่ไฟล์ที่เนื้อหาต่างกันยกบรรทัด (ฝั่งหนึ่ง
        # มีโอเมกา-3/แคลเซียม/ฟอสฟอรัสเพิ่ม) ⇒ ข้อความไหลใหม่ทั้งครึ่งล่าง
        # ⇒ ชั้นภาพฟ้อง 35 บริเวณ (ecc 0.51 · ต่าง 19.73%) ไปลบผลชั้นข้อความ
        # 7 รายการที่ตรงกับความต่างจริงพอดี
        why = _pixel_untrusted(res)
        if not found or why:
            entry["kept_text_layer"] = True
            if why:
                entry["untrusted"] = why
                pg.note("pixel", "กลุ่ม %s · ผลชั้นภาพยังไม่น่าเชื่อถือ (%s) "
                                 "→ คงผลชั้นข้อความไว้" % (g, why))
            continue
        new_defects += found
        replaced_groups.add(g)

    if stopped:
        pg.note("pixel", stopped)
    if not replaced_groups:
        return defects, {"pairs": pairs, "used": 0,
                         "stopped": stopped or None}

    # แทนที่เฉพาะ MISMATCH_* ของกลุ่มที่เทียบพิกเซลสำเร็จ — คลาสอื่นคงเดิม
    ids = {z["id"] for z in zone_list
           if (z.get("group") or "") in replaced_groups}
    kept = [x for x in defects
            if not (str(x.get("class", "")).startswith("MISMATCH_")
                    and x.get("zone_id") in ids)]
    return kept + new_defects, {"pairs": pairs, "used": len(replaced_groups),
                                "stopped": stopped or None}


def run_inspection(rec_id: str, zone_list: List[dict],
                   brand: str = "", auto_rotate: bool = False,
                   force_ocr: bool = False,
                   split_bands: bool = False,
                   confirm_reads: bool = False,
                   pixel_check: bool = False,
                   page_rot: int = 0,
                   progress=None) -> dict:
    # ``progress`` = ตัวบันทึกจุดเช็คพอยต์ให้หน้าเว็บวาดเส้นความคืบหน้า
    # (advisory ล้วน — ไม่แตะผลตรวจ · ไม่ส่งมา = ไม่บันทึกอะไรเลย)
    pg = progress or progress_mod.NullRun()
    # มุมที่ "จอหมุนอยู่" ตอนผู้ใช้ลากโซน — เก็บไว้เพื่อให้รายงานแสดงภาพ
    # ทั้งหน้าในแนวเดียวกับที่คนเพิ่งจัดมา (แสดงผลล้วน ไม่แตะพิกัด/ผลตรวจ)
    page_rot = page_rot if page_rot in (90, 180, 270) else 0
    pg.start("prepare")
    d = report.inspection_dir(rec_id)
    src = _find_source(d)
    zone_list = zones_mod.sanitize_zones(zone_list)
    zones_a, zones_b = _split_docs(zone_list)
    n_zone = len([z for z in zone_list if z.get("type") != "ignore"])
    pg.done("prepare", progress_mod.OK,
            "%d โซน (ไฟล์หลัก %d · ไฟล์อ้างอิง %d)"
            % (n_zone, len(zones_a), len(zones_b)))

    t0 = time.time()
    # เวลารวมสูงสุด — เกินแล้ว **ข้ามเฉพาะชั้นเสริม** (อ่านซ้ำ / เทียบพิกเซล /
    # อ่านบริเวณที่ต่าง) แล้วออกรายงานเท่าที่มี. ห้ามทิ้ง defect ที่ได้แล้ว
    # เพราะเราหมดเวลาเอง (กฎเหล็กข้อ 2) · 0 = ไม่จำกัด
    deadline = (t0 + config.INSPECT_TIMEOUT_S
                if config.INSPECT_TIMEOUT_S else None)
    timed_out = False
    pg.start("ocr", "กำลังอ่าน %d โซน (พร้อมกันสูงสุด %d สาย)"
             % (n_zone, max(1, int(config.OCR_PARALLEL or 1))))
    ocr_results, trust = _read_all_docs(d, zones_a, zones_b,
                                        auto_rotate=auto_rotate,
                                        force_ocr=force_ocr,
                                        split_bands=split_bands)
    _report_ocr_progress(pg, ocr_results, trust)
    # Record the concrete angle actually applied back onto each OCR'd zone
    # so the saved report, overlay crops and OCR-review show what OCR read.
    # (ignore-type zones are not OCR'd → left as the user set them.)
    rot_by_id = {r["zone_id"]: r.get("rotate", 0) for r in ocr_results}
    for z in zone_list:
        # ⚠️ ต้องอ่านมุมที่ผู้ใช้ "ปักหมุด" ไว้ **ก่อน** บรรทัดที่ทับด้านล่าง
        pinned = z.get("rotate") if z.get("rotate") in (90, 180, 270) else None
        if z["id"] in rot_by_id:
            z["rotate"] = rot_by_id[z["id"]]
        # ── มุมสำหรับ "แสดงผล" เท่านั้น (ผู้ใช้ 11 ก.ย.) ─────────────
        #
        # ``rotate`` ข้างบนแปลว่า "OCR หมุนภาพไปกี่องศาก่อนอ่าน" ซึ่ง
        # **เป็น 0 เสมอ** ในเส้นทาง ``pdf-text``/``none`` (อ่านจาก text
        # layer ไม่ได้หมุนภาพเลย) ⇒ พอเอาไปทับ มุมที่ผู้ใช้ปักหมุดหายไป
        # ⇒ การ์ด defect ของไฟล์ PDF ที่มี text layer ไม่เคยหมุนตามที่ตั้ง
        # ไว้เลย ทั้งที่ภาพบนจอตอนลากโซนหมุนอยู่ (= สิ่งที่ผู้ใช้ร้องขอ)
        #
        # แยกเป็นคีย์ใหม่แทนการแก้ ``rotate`` โดยตั้งใจ: ``rotate`` ยังต้อง
        # บอกความจริงว่า OCR ทำอะไร (เส้นความคืบหน้า/แท็บข้อความอ่านค่านี้)
        # และ **ชั้น pixel ใช้ค่านี้ตัดสินใจหมุนก่อนทาบภาพ** ⇒ แตะไม่ได้
        if config.REPORT_VIEW_ROTATE:
            view = pinned if pinned is not None else z.get("rotate")
            if view in (90, 180, 270):
                z["view_rot"] = view

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

    pg.start("checks")
    defects = _checks(ocr_results)
    pg.done("checks", progress_mod.OK,
            "พบ %d รายการจากชั้นข้อความ" % len(defects))

    confirm_info = None
    over = bool(deadline) and time.time() > deadline
    if over:
        timed_out = True
    if not confirm_reads:
        pg.skip("confirm", "ไม่ได้ติ๊กช่อง “อ่านซ้ำ 2 รอบ”")
    elif over:
        pg.skip("confirm", "ข้ามเพราะใช้เวลาเกิน %.0f วินาทีแล้ว"
                % config.INSPECT_TIMEOUT_S)
        confirm_info = {"rounds": 1, "confirmed": len(defects),
                        "unconfirmed": 0, "items": [],
                        "error": "ข้ามการอ่านรอบที่สองเพราะใช้เวลาเกินกำหนด — "
                                 "ผลนี้มาจากการอ่านรอบเดียว"}
    if confirm_reads and not over:
        pg.start("confirm", "อ่านรอบที่สองด้วยเส้นทางเดียวกัน")
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
            ag = confirm_info.get("agreement")
            pg.done("confirm",
                    progress_mod.WARN if (ag is not None and ag < 0.5)
                    else progress_mod.OK,
                    "แต่ละรอบฟ้อง %d · %d รายการ · ยืนยันได้ %d · ตกไป %d%s"
                    % (n1, len(r2), confirm_info["confirmed"],
                       confirm_info["unconfirmed"],
                       "" if ag is None
                       else " · ตรงกัน %d%%" % round(ag * 100)))
        except Exception:
            # อ่านรอบสองไม่สำเร็จ = ยืนยันไม่ได้ ⇒ **คงผลรอบแรกไว้ทั้งหมด**
            # (ห้ามทิ้ง defect เพราะเหตุขัดข้องของเราเอง) พร้อมบอกให้เห็น
            logger.exception("[artwork] อ่านรอบยืนยันไม่สำเร็จ — ใช้ผลรอบเดียว")
            confirm_info = {"rounds": 1, "confirmed": len(defects),
                            "unconfirmed": 0, "items": [],
                            "error": "อ่านรอบที่สองไม่สำเร็จ — ผลนี้มาจากการอ่านรอบเดียว"}
            pg.done("confirm", progress_mod.FAIL,
                    "อ่านรอบที่สองไม่สำเร็จ — ใช้ผลรอบเดียว")

    pixel_info = None
    over = bool(deadline) and time.time() > deadline
    if over:
        timed_out = True
    if not pixel_check:
        pg.skip("pixel", "ไม่ได้ติ๊กช่อง “เทียบแผงระดับพิกเซล”")
    elif over:
        pg.skip("pixel", "ข้ามเพราะใช้เวลาเกิน %.0f วินาทีแล้ว"
                % config.INSPECT_TIMEOUT_S)
        pixel_info = {"pairs": [], "used": 0,
                      "error": "ข้ามการเทียบพิกเซลเพราะใช้เวลาเกินกำหนด — "
                               "ผลนี้มาจากชั้นข้อความเหมือนเดิม"}
    if pixel_check and not over:
        pg.start("pixel", "จับคู่กลุ่มที่มีโซน panel สองโซน")
        try:
            defects, pixel_info = _pixel_compare(d, zone_list, defects,
                                                 progress=pg,
                                                 deadline=deadline)
            if (pixel_info or {}).get("stopped"):
                timed_out = True
            _report_pixel_progress(pg, pixel_info)
        except Exception:
            logger.exception("[artwork] เทียบพิกเซลไม่สำเร็จ — ใช้ผลชั้นข้อความ")
            pixel_info = {"pairs": [], "used": 0,
                          "error": "เทียบพิกเซลไม่สำเร็จ — ผลนี้มาจากชั้นข้อความเหมือนเดิม"}
            pg.done("pixel", progress_mod.FAIL,
                    "เทียบพิกเซลไม่สำเร็จ — ใช้ผลชั้นข้อความ")

    pg.start("coverage")
    _tag_highlight_risk(d, zone_list)

    cov = checks.check_coverage(zone_list, ocr_results)
    _report_coverage_progress(pg, cov)

    pg.start("report", "วาดภาพสรุปและบันทึก")
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
        # มุมที่จอหมุนอยู่ตอนลากโซน — รายงาน (และหน้าประวัติ) หมุนภาพทั้งหน้า
        # ตามค่านี้ **ตอนแสดงผลเท่านั้น** ไฟล์ preview/overlay ไม่ถูกแตะ
        "page_rot": page_rot,
        "ocr": ocr_results,
        "elapsed_s": round(time.time() - t0, 2),
        "spell_layer_available": checks.spell_layer_available(),
        "ocr_available": ocr.is_ocr_available(),
        # ชั้นไหน "ได้ทำงานจริง" กับงานใบนี้ — advisory ล้วน คำนวณ *หลัง*
        # ได้ defects แล้ว จึงไม่มีทางกระทบ verdict/การนับ. ต้องมีเพราะ
        # PASS ไม่ได้แปลว่าตรวจครบ (ดู checks.check_coverage)
        "coverage": cov,
        # อ่านทั้งใบด้วย OCR ตามที่ผู้ใช้สั่งหรือไม่ — บันทึกไว้เพื่อให้อ่าน
        # รายงานย้อนหลังแล้วรู้ว่าข้อความมาจากเส้นทางไหน
        "force_ocr": bool(force_ocr),
        "split_bands": bool(split_bands),
        # ใช้เวลาเกินกำหนดจนต้องข้ามชั้นเสริม — ต้องบอก ไม่ใช่เงียบ
        "timed_out": bool(timed_out),
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
    pg.done("report", progress_mod.OK,
            "%s · %d รายการ · %.1f วินาที"
            % (rep["verdict"], len(defects), rep["elapsed_s"]))
    pg.finish(progress_mod.OK, rep["verdict"])
    # เส้นความคืบหน้าต้อง **อยู่ต่อหลังตรวจเสร็จ** — เก็บลงรายงานเลย ไม่ใช่
    # อ่านจาก registry ในหน่วยความจำ (ซึ่งเก็บแค่ 32 ครั้ง และหายตอนรีสตาร์ต)
    # ⇒ หน้าประวัติเห็นด้วย · advisory ล้วน ไม่แตะ defects/verdict/การนับ
    snap = progress_mod.snapshot(rec_id)
    if snap:
        rep["flow"] = snap
    report.save_report(rec_id, rep)
    logger.info("[artwork] done %s verdict=%s defects=%d in %.1fs",
                rec_id, rep["verdict"], len(defects), rep["elapsed_s"])
    return rep


# ── ตัวช่วยรายงานความคืบหน้า (advisory ล้วน — แยกออกมาให้ทดสอบได้ตรง ๆ) ──
def _report_ocr_progress(pg, ocr_results, trust) -> None:
    """สรุปผลชั้นอ่านข้อความลงจุดเช็คพอยต์ — บอกว่าโซนไหนใช้ engine อะไร."""
    eng = {}
    bad = 0
    for r in ocr_results or []:
        e = str(r.get("engine", "?"))
        eng[e] = eng.get(e, 0) + 1
        if r.get("error") or not (r.get("text") or "").strip():
            bad += 1
        pg.note("ocr", "%s · %s · %d ตัวอักษร%s"
                % (r.get("zone_id", "?"), e, len((r.get("text") or "")),
                   " · %s" % r["error"] if r.get("error") else ""))
    mix = " · ".join("%s %d" % (k, v) for k, v in sorted(eng.items()))
    pg.done("ocr", progress_mod.WARN if bad else progress_mod.OK,
            "%s%s" % (mix, " · อ่านไม่ได้ %d โซน" % bad if bad else ""))
    # ชั้นฟอนต์ทำงานก่อนหน้าเสมอ (อยู่ใน _read_all_docs) — รายงานย้อนหลัง
    susp = []
    for doc, tr in (trust or {}).items():
        try:
            names = list((fonttrust.summary(tr) or {}).get("suspect", []) or [])
        except Exception:
            names = []
        susp += ["%s:%s" % (doc, n) for n in names]
    if susp:
        pg.done("fonttrust", progress_mod.WARN,
                "ฟอนต์ที่ถอดข้อความไม่ได้ %d ตัว — โซนที่ใช้ฟอนต์นี้ถูกส่งไป OCR แทน"
                % len(susp))
        for n in susp[:20]:
            pg.note("fonttrust", n)
    else:
        pg.done("fonttrust", progress_mod.OK, "ไม่พบฟอนต์ที่น่าสงสัย")


def _report_pixel_progress(pg, info) -> None:
    """บอกว่ากลุ่มไหนเทียบด้วยภาพได้จริง กลุ่มไหนตกเงื่อนไข — และเพราะอะไร."""
    pairs = (info or {}).get("pairs") or []
    if (info or {}).get("stopped"):
        pg.note("pixel", str(info["stopped"]))
    if not pairs:
        pg.done("pixel", progress_mod.SKIP,
                "ไม่มีกลุ่มที่เข้าเงื่อนไข (ต้องมีโซน panel สองโซนในกลุ่มเดียวกัน "
                "และเป็น PDF ทั้งคู่)")
        return
    used = int((info or {}).get("used") or 0)
    for p in pairs:
        if p.get("status") != "ok":
            pg.note("pixel", "กลุ่ม %s · เทียบไม่ได้ (%s) → ใช้ผลชั้นข้อความ"
                    % (p.get("group"), p.get("reason") or p.get("status")))
        elif p.get("kept_text_layer"):
            pg.note("pixel", "กลุ่ม %s · เทียบแล้วไม่พบความต่าง → คงผลชั้นข้อความ"
                    % p.get("group"))
        else:
            cap = p.get("ocr_capped")
            pg.note("pixel", "กลุ่ม %s · พบ %s บริเวณ%s · ต่าง %s%%%s"
                    % (p.get("group"), p.get("regions"),
                       " · ตัดทิ้งติดขอบ %s" % p["edge_regions"]
                       if p.get("edge_regions") else "",
                       round((p.get("diff_ratio") or 0) * 100, 4),
                       " · อ่านข้อความ %d จาก %d บริเวณ (เพดาน)"
                       % (cap[0], cap[1]) if cap else ""))
    pg.done("pixel",
            progress_mod.OK if used else progress_mod.WARN,
            "ใช้ผลจากภาพ %d จาก %d กลุ่ม" % (used, len(pairs)))


def _report_coverage_progress(pg, cov) -> None:
    """ชั้นไหน "ได้ทำงานจริง" — ตัวเดียวกับแถบ coverage บนรายงาน."""
    ran, missed = [], []
    for name, v in (cov or {}).items():
        if not isinstance(v, dict):
            continue
        (ran if v.get("ran") else missed).append(name)
        if not v.get("ran"):
            pg.note("coverage", "%s — ไม่ได้ทำงาน (%s)"
                    % (name, v.get("reason") or "ไม่ระบุ"))
    pg.done("coverage", progress_mod.WARN if missed else progress_mod.OK,
            "ทำงาน %d ชั้น%s"
            % (len(ran), " · ไม่ได้ทำงาน %d ชั้น" % len(missed) if missed else ""))


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
                  zone_id: str = "", box: Optional[List[float]] = None,
                  highlights: Optional[List[str]] = None) -> bytes:
    """High-DPI crop of one zone — used by the UI defect table / preview.
    ``doc="b"`` crops from the attached reference file. ``rotate`` is an
    angle 0/90/180/270 or "auto" (detect + rotate vertical → upright), so
    the preview matches what OCR will actually receive.

    When ``highlight`` (a defect's problem word) and ``zone_id`` are given
    AND a saved report exists, the word is located in the crop and boxed
    in red. This is display-only: locating uses the saved OCR text/blocks
    of that zone, never re-runs a check, and any failure just returns the
    plain crop (identical to omitting ``highlight``)."""
    base_dpi = dpi or config.OCR_DPI
    crop = _render_zone_cached(rec_id, zone_bbox, base_dpi, doc)
    angle = resolve_rotation(rotate, page_auto=False, crop=crop) \
        if rotate == "auto" else (int(rotate) if str(rotate) in
                                  ("0", "90", "180", "270") else 0)
    # ── กรอบที่ "วัดมา" (โหมดเทียบพิกเซล) ────────────────────────────
    # ต่างจาก ``highlight`` ตรงที่ไม่ต้องค้นหาคำ ⇒ ใช้ได้ทุกภาษา และใช้ได้
    # แม้อ่านข้อความตรงนั้นไม่ออกเลย. วาด **ก่อนหมุน** เพื่อให้กรอบหมุนตาม
    # ภาพเอง ไม่ต้องแปลงพิกัดเอง (แปลงเองพลาดง่ายและกรอบจะไปผิดที่)
    if box and crop.size:
        crop = _draw_measured_box(crop, box)
    if angle:
        crop = apply_rotation(crop, angle)

    targets = [t for t in (highlights or ([highlight] if highlight else []))
               if t]
    if targets and zone_id and config.HIGHLIGHT_DEFECT_WORD:
        crop = _highlight_crop(rec_id, crop, targets, zone_id, angle)
    return encode_jpg(crop, quality=88)


# ── แคชภาพ crop ที่เรนเดอร์แล้ว ────────────────────────────────────────
# การ์ด defect หนึ่งใบขอรูป 2 ใบ (ฝั่งหลัก + ฝั่งอ้างอิง) และรายงานหนึ่งใบมี
# หลายการ์ดที่มัก **ชี้ไปที่โซนเดิม** ⇒ เดิมเรนเดอร์ PDF โซนเดียวกันซ้ำ
# 10-20 ครั้งต่อการเปิดรายงานหนึ่งครั้ง (และโซนเล็กเรนเดอร์ 2 รอบต่อครั้ง)
# การเรนเดอร์เป็น deterministic ⇒ แคชได้โดยผลไม่เปลี่ยนแม้แต่พิกเซลเดียว
_CROP_CACHE: "OrderedDict" = OrderedDict()
_CROP_LOCK = threading.Lock()


def _render_zone_cached(rec_id: str, zone_bbox: List[float],
                        base_dpi: int, doc: str):
    """``render_zone`` + ชั้นเพิ่ม DPI ของโซนเล็ก พร้อมแคช LRU.

    คืน **สำเนา** เสมอ เพื่อไม่ให้ผู้เรียกที่วาดกรอบทับไปแก้ของในแคช
    (วันนี้ทุกทางวาดบนสำเนาอยู่แล้ว — นี่คือกันไว้เชิงโครงสร้าง)
    """
    key = (rec_id, doc, tuple(round(float(v), 6) for v in zone_bbox),
           int(base_dpi))
    if config.CROP_CACHE_MAX > 0:
        with _CROP_LOCK:
            hit = _CROP_CACHE.get(key)
            if hit is not None:
                _CROP_CACHE.move_to_end(key)
                return hit.copy()

    d = report.inspection_dir(rec_id)
    base = "source_b" if doc == "b" else "source"
    document = ArtworkDocument(_find_source(d, base))
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
    if config.CROP_CACHE_MAX > 0:
        with _CROP_LOCK:
            _CROP_CACHE[key] = crop
            _CROP_CACHE.move_to_end(key)
            while len(_CROP_CACHE) > config.CROP_CACHE_MAX:
                _CROP_CACHE.popitem(last=False)
        return crop.copy()
    return crop


def _draw_measured_box(crop, box):
    """วาดกรอบแดงจากพิกัดสัดส่วนของโซน (0..1) ที่ **วัดมาแล้ว**.

    ใช้กับโหมดเทียบพิกเซลซึ่งรู้ตำแหน่งจากการทาบภาพ ไม่ใช่จากการค้นหาคำ
    ⇒ ไม่ผูกกับภาษา และวาดได้ทั้งฝั่งหลักและฝั่งอ้างอิง. เพี้ยน/ผิดรูป =
    คืนภาพเดิม (แสดงผลอย่างเดียว ห้ามทำให้การ์ดพัง)
    """
    try:
        x, y, w, h = [float(v) for v in box]
    except (TypeError, ValueError):
        return crop
    if not (0.0 <= x < 1.0 and 0.0 <= y < 1.0 and 0.0 < w <= 1.0
            and 0.0 < h <= 1.0):
        return crop
    H, W = crop.shape[:2]
    x0, y0 = int(round(x * W)), int(round(y * H))
    x1, y1 = int(round((x + w) * W)), int(round((y + h) * H))
    pad = max(2, int(0.004 * max(W, H)))
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(W - 1, x1 + pad), min(H - 1, y1 + pad)
    if x1 <= x0 or y1 <= y0:
        return crop
    out = crop.copy()
    cv2.rectangle(out, (x0, y0), (x1, y1), (0, 0, 220),
                  max(2, int(0.003 * max(W, H))))
    return out


def _highlight_crop(rec_id: str, crop, found, zone_id: str,
                    angle: int = 0):
    """Draw the red word-box(es) on ``crop`` using the saved data of
    ``zone_id``. Strategy, most reliable first:
      ② exact PDF text-layer word box (when the zone was read from a live
         text layer — any script, no OCR);
      ①③ then hl.annotate (OCR-backend bbox → Tesseract).
    Isolated + fully guarded: any problem returns the crop untouched so the
    defect card still shows the plain image.

    ``found`` may be one string or a LIST of strings ("ช่วงที่ต่าง" หลายจุด
    ของบรรทัดเดียว). แต่ละตัวถูกค้นแยกกันแล้วรวมกรอบ — การอ่านภาพด้วย
    Tesseract ถูกแคชไว้ต่อ (รูป, ภาษา, psm) แล้ว ⇒ ตัวที่สองเป็นต้นไป
    แทบไม่มีต้นทุนเพิ่มเมื่ออยู่ภาษาเดียวกัน"""
    targets = [found] if isinstance(found, str) else list(found or [])
    targets = [t for t in targets if t]
    if not targets:
        return crop
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
            boxes = []
            for t in targets:
                boxes += _pdf_text_boxes(rec_id, rep, zone_id, t, crop, angle)
            if boxes:
                return hl.draw_boxes(crop, boxes[:config.HIGHLIGHT_MAX_BOXES]
                                     if config.HIGHLIGHT_MAX_BOXES else boxes)

        boxes = []
        for t in targets:
            boxes += hl.locate_all(crop, t, entry.get("text", ""),
                                   entry.get("blocks"), entry.get("ocr_wh"),
                                   use_tesseract=config.HIGHLIGHT_USE_TESSERACT,
                                   use_profile=config.HIGHLIGHT_USE_PROFILE,
                                   tess_lang=config.HIGHLIGHT_TESSERACT_LANG,
                                   max_boxes=config.HIGHLIGHT_MAX_BOXES,
                                   row_verify=config.HIGHLIGHT_ROW_VERIFY)
        if not boxes:
            return crop
        if config.HIGHLIGHT_MAX_BOXES:
            boxes = boxes[:config.HIGHLIGHT_MAX_BOXES]
        return hl.draw_boxes(crop, boxes)
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
