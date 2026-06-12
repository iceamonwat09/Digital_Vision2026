"""
Per-zone text acquisition.

Order of preference per zone:
  1. PDF embedded text layer  — exact, free, offline. Outlined artwork
     (the usual case for print masters) has none, so:
  2. N8N → Gemini OCR via ``inspectors.vertex_client`` — the SAME
     dispatcher the Label Paper mode uses, imported read-only so this
     mode cannot affect the existing pipeline.

The N8N workflow MUST be configured for verbatim transcription
(temperature 0, "do not correct spelling") — see N8N_PROMPT.md in this
package. An LLM that silently fixes "caliddd" → "calidad" would hide
exactly the defects this mode exists to catch.
"""

from __future__ import annotations

import logging
from typing import List

from inspectors import vertex_client   # read-only reuse of the dispatcher

from . import config
from .pdf_ingest import ArtworkDocument, encode_jpg

logger = logging.getLogger(__name__)


def is_ocr_available() -> bool:
    return vertex_client.is_enabled()


def read_zone(doc: ArtworkDocument, zone: dict) -> dict:
    """
    Returns:
        {
          "zone_id": "z1",
          "text":    "...",        # newline separated
          "engine":  "pdf-text" | "<ocr engine>" | "none",
          "conf":    float | None, # mean OCR block confidence when known
          "error":   "..."         # present only on failure
        }
    """
    bbox = zone["bbox"]

    embedded = doc.embedded_text(bbox)
    if len(embedded) >= config.EMBEDDED_TEXT_MIN_CHARS:
        return {"zone_id": zone["id"], "text": embedded,
                "engine": "pdf-text", "conf": 1.0}

    if not vertex_client.is_enabled():
        return {"zone_id": zone["id"], "text": "", "engine": "none",
                "conf": None,
                "error": "ไม่ได้ตั้งค่า OCR backend (N8N_OCR_WEBHOOK_URL) "
                         "และไฟล์นี้ไม่มี text layer"}

    crop = doc.render_zone(bbox, dpi=config.OCR_DPI,
                           max_side=config.OCR_CROP_MAX_SIDE)
    if crop.size == 0:
        return {"zone_id": zone["id"], "text": "", "engine": "none",
                "conf": None, "error": "โซนว่าง (bbox ตัดออกนอกหน้า)"}

    result = vertex_client.ocr_image(encode_jpg(crop))
    out = {
        "zone_id": zone["id"],
        "text": (result.get("text") or "").strip(),
        "engine": result.get("engine", "n8n"),
        "conf": _mean_conf(result.get("blocks") or []),
    }
    if result.get("error"):
        out["error"] = str(result["error"])
    if result.get("stub"):
        out["error"] = out.get("error") or "OCR backend ตอบกลับเป็น stub"
    return out


def read_all_zones(doc: ArtworkDocument, zones: List[dict]) -> List[dict]:
    out = []
    for z in zones:
        if z.get("type") == "ignore":
            continue
        r = read_zone(doc, z)
        logger.info("[artwork] zone %s engine=%s chars=%d%s",
                    z["id"], r["engine"], len(r["text"]),
                    f" ERROR={r['error']}" if r.get("error") else "")
        out.append(r)
    return out


def _mean_conf(blocks: list):
    confs = [float(b.get("conf", 0) or 0) for b in blocks
             if isinstance(b, dict)]
    confs = [c for c in confs if c > 0]
    return round(sum(confs) / len(confs), 3) if confs else None
