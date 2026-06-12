"""
Inspection orchestration: upload → preview+auto-zones → inspect.

Two-step flow matching the UI:

  1. ``start_inspection(file)``  — persist the upload, render the
     preview, propose zones. The human adjusts zones in the browser.
  2. ``run_inspection(id, zones, brand)`` — per-zone text acquisition
     (PDF text layer or N8N OCR), all check layers, overlay, report.
"""

from __future__ import annotations

import logging
import os
import time
from typing import List, Optional

import cv2

from . import checks, config, ocr, report, vocab, zones as zones_mod
from .pdf_ingest import ArtworkDocument, encode_jpg

logger = logging.getLogger(__name__)

ALLOWED_EXT = (".pdf", ".png", ".jpg", ".jpeg")


def start_inspection(file_bytes: bytes, filename: str) -> dict:
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError(f"รองรับเฉพาะไฟล์ {', '.join(ALLOWED_EXT)}")
    if not file_bytes:
        raise ValueError("ไฟล์ว่าง")

    rec_id = report.new_inspection_id()
    d = report.inspection_dir(rec_id, create=True)
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


def run_inspection(rec_id: str, zone_list: List[dict],
                   brand: str = "") -> dict:
    d = report.inspection_dir(rec_id)
    src = _find_source(d)
    doc = ArtworkDocument(src)
    zone_list = zones_mod.sanitize_zones(zone_list)

    t0 = time.time()
    ocr_results = ocr.read_all_zones(doc, zone_list)

    vocab_words: set = set()
    vocab_phrases: List[str] = []
    if brand:
        v = vocab.load(brand)
        vocab_words = set(v["words"])
        vocab_phrases = v["phrases"]

    defects = checks.run_all_checks(zone_list, ocr_results,
                                    vocab_words=vocab_words,
                                    vocab_phrases=vocab_phrases)

    preview = cv2.imread(os.path.join(d, "preview.png"))
    if preview is None:
        preview = doc.render(config.PREVIEW_DPI)
    overlay = report.draw_overlay(preview, zone_list, defects)
    cv2.imwrite(os.path.join(d, "overlay.png"), overlay)

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
    report.save_report(rec_id, rep)
    logger.info("[artwork] done %s verdict=%s defects=%d in %.1fs",
                rec_id, rep["verdict"], len(defects), rep["elapsed_s"])
    return rep


def zone_crop_jpg(rec_id: str, zone_bbox: List[float],
                  dpi: Optional[int] = None) -> bytes:
    """High-DPI crop of one zone — used by the UI defect table."""
    d = report.inspection_dir(rec_id)
    doc = ArtworkDocument(_find_source(d))
    crop = doc.render_zone(zone_bbox, dpi=dpi or config.OCR_DPI,
                           max_side=1600)
    return encode_jpg(crop, quality=88)


def _find_source(insp_dir: str) -> str:
    for ext in ALLOWED_EXT:
        p = os.path.join(insp_dir, f"source{ext}")
        if os.path.exists(p):
            return p
    raise FileNotFoundError("ไม่พบไฟล์ต้นฉบับของการตรวจนี้")
