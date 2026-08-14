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
import re
from typing import List

from inspectors import vertex_client   # read-only reuse of the dispatcher

from . import config
from .pdf_ingest import (ArtworkDocument, apply_rotation, encode_jpg,
                         resolve_rotation)

logger = logging.getLogger(__name__)


def is_ocr_available() -> bool:
    return vertex_client.is_enabled()


# ── ด่านคุณภาพของ PDF text layer ─────────────────────────────────────
_TOKEN_RE = re.compile(r"\S+")
_STRIP = ".,;:()[]{}%/\\\"'“”‘’«»"


def _long_tokens(text: str, min_len: int = 8) -> List[str]:
    toks = (t.strip(_STRIP) for t in _TOKEN_RE.findall(text))
    return [t for t in toks if len(t) >= min_len]


def _malformed(tok: str) -> bool:
    """คำที่ "ผิดรูป": ยาวพอ และมีตัวเลขแทรกอยู่กลางคำที่เป็นตัวอักษร.

    ฉลากจริงมีคำปนตัวเลขเยอะ แต่ตัวเลขจะอยู่ท้ายคำหรือแยกเป็นคำของตัวเอง
    ("B12", "OMEGA-3", "170G", "E1520") ส่วนข้อความที่ฟอนต์แมปอักขระผิดจะได้
    ตัวเลขโผล่กลางคำยาว ๆ ("PR3374Y0KOI", "ROL12SSAL").
    """
    if not (any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok)):
        return False
    head = tok.rstrip("0123456789")
    # ตัวเลขอยู่ท้ายล้วน = รหัส/สารเติมแต่งปกติ ไม่ใช่คำเสีย
    return bool(head) and any(c.isdigit() for c in head)


def text_looks_garbled(text: str,
                       min_tokens: int = None,
                       ratio: float = None) -> bool:
    """True เมื่อข้อความจาก text layer หน้าตาเหมือน "ฟอนต์แมปอักขระผิด".

    ตัดสินเฉพาะบล็อกที่มีคำยาวมากพอ (``min_tokens``) — แถบรหัสงานพิมพ์มีคำ
    แบบนั้นไม่กี่คำจึงไม่ถูกตัดสิน. วัดกับไฟล์จริง 35 บล็อก: ฟ้องผิด 0.
    """
    mt = config.PDFTEXT_GARBLED_MIN_TOKENS if min_tokens is None else min_tokens
    rt = config.PDFTEXT_GARBLED_RATIO if ratio is None else ratio
    toks = _long_tokens(text)
    if len(toks) < max(1, mt):
        return False
    bad = sum(1 for t in toks if _malformed(t))
    return (bad / float(len(toks))) >= rt


def read_zone(doc: ArtworkDocument, zone: dict,
              page_auto: bool = False) -> dict:
    """
    Returns:
        {
          "zone_id": "z1",
          "text":    "...",        # newline separated
          "engine":  "pdf-text" | "<ocr engine>" | "none",
          "conf":    float | None, # mean OCR block confidence when known
          "rotate":  int,          # clockwise degrees applied before OCR
          "error":   "..."         # present only on failure
        }

    ``page_auto`` is the page-level auto-rotate toggle; it only affects
    zones whose ``rotate`` is "default". Rotation applies to the IMAGE
    OCR path only — a zone read from the PDF text layer keeps rotate 0
    (embedded text already carries reading order).
    """
    bbox = zone["bbox"]

    embedded = doc.embedded_text(bbox)
    garbled = ""
    if len(embedded) >= config.EMBEDDED_TEXT_MIN_CHARS:
        if config.PDFTEXT_GARBLED_CHECK and text_looks_garbled(embedded):
            # text layer มีข้อความ "พอ" แต่ใช้ไม่ได้ (ฟอนต์แมปอักขระผิด).
            # ห้ามส่งต่อด้วย conf 1.0 — ตกไปอ่านจากภาพจริงแทน.
            garbled = ("text layer ของโซนนี้อ่านออกมาเป็นคำผิดรูป "
                       "(ฟอนต์ในไฟล์แมปอักขระผิด) จึงไม่ใช้ค่าจาก PDF")
            logger.warning("[artwork] zone %s: text layer garbled (%d chars) "
                           "-> fall back to OCR", zone["id"], len(embedded))
        else:
            return {"zone_id": zone["id"], "text": embedded,
                    "engine": "pdf-text", "conf": 1.0, "rotate": 0}

    if not vertex_client.is_enabled():
        if garbled:
            # ไม่มี OCR ให้ถอยไปใช้ — คืนข้อความเดิมพร้อมธง error เพื่อให้
            # กลายเป็น UNREADABLE (ขอให้คนดู) แทนที่จะเงียบว่าถูกต้อง
            return {"zone_id": zone["id"], "text": embedded,
                    "engine": "pdf-text", "conf": None, "rotate": 0,
                    "error": garbled + " และไม่มี OCR backend ให้ใช้แทน"}
        return {"zone_id": zone["id"], "text": "", "engine": "none",
                "conf": None, "rotate": 0,
                "error": "ไม่ได้ตั้งค่า OCR backend (N8N_OCR_WEBHOOK_URL) "
                         "และไฟล์นี้ไม่มี text layer"}

    crop = _render_for_ocr(doc, bbox)
    if crop is None or crop.size == 0:
        return {"zone_id": zone["id"], "text": "", "engine": "none",
                "conf": None, "rotate": 0,
                "error": "โซนว่าง (bbox ตัดออกนอกหน้า)"}

    angle = resolve_rotation(zone.get("rotate", "default"), page_auto, crop)
    if angle:
        crop = apply_rotation(crop, angle)

    result = vertex_client.ocr_image(encode_jpg(crop))
    blocks = result.get("blocks") or []
    out = {
        "zone_id": zone["id"],
        "text": (result.get("text") or "").strip(),
        "engine": result.get("engine", "n8n"),
        "conf": _mean_conf(blocks),
        "rotate": angle,
        # Per-element boxes (text/bbox/conf) when the backend returned
        # them — kept ONLY so the defect-card red-box highlighter
        # (artwork_check.highlight) can point at a word. Never used by any
        # check layer or the verdict. Empty for the PDF-text path.
        "blocks": [b for b in blocks
                   if isinstance(b, dict) and b.get("bbox")],
        # Pixel size of the image the OCR engine actually saw (crop after
        # rotation, before JPEG). Lets the highlighter normalize block
        # bboxes no matter which convention the engine used (0..1 / 0..1000
        # / raw pixels) — see highlight._norm_block_bbox.
        "ocr_wh": [int(crop.shape[1]), int(crop.shape[0])],
    }
    if result.get("error"):
        out["error"] = str(result["error"])
    if result.get("stub"):
        out["error"] = out.get("error") or "OCR backend ตอบกลับเป็น stub"
    if garbled:
        # บอกไว้ในผลว่าทำไมโซนนี้ไม่ได้ใช้ text layer ทั้งที่ไฟล์มี —
        # ไม่ใช่ error (OCR อ่านสำเร็จ) แต่ผู้ตรวจควรรู้
        out["note"] = garbled
    return out


def _render_for_ocr(doc: ArtworkDocument, bbox):
    """เรนเดอร์โซนสำหรับส่ง OCR — เหมือนเดิมทุกอย่าง ยกเว้นเพิ่ม DPI ให้โซน
    ที่เรนเดอร์ออกมาเล็กเกินกว่า OCR จะอ่านได้ (ตรรกะเดียวกับที่
    ``pipeline.zone_crop_jpg`` ใช้กับภาพบนการ์ดอยู่แล้ว).

    ``OCR_CROP_MIN_SIDE = 0`` = ปิด = เส้นทางเดิมเป๊ะ.
    """
    crop = doc.render_zone(bbox, dpi=config.OCR_DPI,
                           max_side=config.OCR_CROP_MAX_SIDE)
    min_side = config.OCR_CROP_MIN_SIDE
    if not min_side or crop is None or crop.size == 0:
        return crop
    # ภาพ raster ไม่มีรายละเอียดเพิ่มให้ดึง — ขยายได้แค่ความเบลอ
    if not getattr(doc, "is_pdf", False):
        return crop
    longest = max(crop.shape[:2])
    if longest >= min_side:
        return crop
    factor = min(config.OCR_DPI_MAX_FACTOR, min_side / float(longest))
    bigger = doc.render_zone(bbox, dpi=int(config.OCR_DPI * factor),
                             max_side=config.OCR_CROP_MAX_SIDE)
    if bigger is None or bigger.size == 0:
        return crop
    logger.info("[artwork] zone crop %dx%d เล็กเกินไป -> เรนเดอร์ใหม่ที่ "
                "DPI x%.1f ได้ %dx%d", crop.shape[1], crop.shape[0], factor,
                bigger.shape[1], bigger.shape[0])
    return bigger


def read_all_zones(doc: ArtworkDocument, zones: List[dict],
                   page_auto: bool = False) -> List[dict]:
    out = []
    for z in zones:
        if z.get("type") == "ignore":
            continue
        r = read_zone(doc, z, page_auto=page_auto)
        logger.info("[artwork] zone %s engine=%s rot=%d chars=%d%s",
                    z["id"], r["engine"], r.get("rotate", 0), len(r["text"]),
                    f" ERROR={r['error']}" if r.get("error") else "")
        out.append(r)
    return out


def _mean_conf(blocks: list):
    confs = [float(b.get("conf", 0) or 0) for b in blocks
             if isinstance(b, dict)]
    confs = [c for c in confs if c > 0]
    return round(sum(confs) / len(confs), 3) if confs else None
