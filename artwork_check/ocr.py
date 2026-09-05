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

from . import bands as bands_mod
from . import config, fonttrust
# อักขระที่ "เป็นไปไม่ได้ในข้อความจริง" นิยามไว้ที่ ``fonttrust`` ที่เดียว —
# ทั้งด่านรายโซน (ไฟล์นี้) และด่านระดับฟอนต์ต้องใช้กติกาเดียวกันเป๊ะ ไม่งั้น
# จะเกิดสภาพ "โซนนี้ผ่านแต่ฟอนต์เดียวกันไม่ผ่าน" ที่อธิบายให้ผู้ใช้ไม่ได้
from .fonttrust import bad_glyph_count, bad_glyph_sample
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


# ── ด่านที่ 2: อักขระที่ "เป็นไปไม่ได้ในข้อความจริง" ────────────────────
# ลายเซ็นของ ToUnicode CMap พัง ไม่ได้มีแบบเดียว: ฟอนต์ subset คนละตัวจะ
# คายขยะออกมาคนละหน้าตา บางตัวได้ตัวเลขแทรกกลางคำ (ด่าน ratio ข้างบนจับ
# ได้) บางตัวได้อักขระควบคุม/Private-Use ปนมา ซึ่งด่าน ratio จับไม่ได้เลย
# เพราะ token นั้นอาจไม่มีตัวเลขสักตัว (เคสจริง: ไฟล์ A4 ของ Cosma).
#
# อักขระในกลุ่มนี้ไม่มีเหตุผลใดที่จะปรากฏในข้อความที่พิมพ์บนฉลาก ⇒ เจอ
# แม้แต่ตัวเดียวก็ตัดสินได้ ไม่ต้องอาศัยสัดส่วน/จำนวนคำขั้นต่ำ.
#
#   Cc = อักขระควบคุม (\x04, \x8c)   Co = Private Use Area
#   Cs = surrogate                   Cn = ยังไม่ถูกกำหนดใน Unicode
#   U+FFFD = REPLACEMENT CHARACTER (ร่องรอยการถอดรหัสล้มเหลว)
#
# ⚠️ **ห้ามใส่ "Cf" (format) เข้าไปเด็ดขาด** — ZWJ/ZWNJ/RLM/LRM เป็นของ
#    ปกติในข้อความอาหรับ/ฮีบรู ถ้าใส่จะฟ้องผิดทุกฉลากที่มีภาษาเหล่านั้น
def text_has_bad_glyphs(text: str, min_count: int = None) -> bool:
    """True เมื่อพบอักขระต้องห้ามอย่างน้อย ``min_count`` ตัว."""
    mc = (config.PDFTEXT_BAD_GLYPH_MIN_COUNT if min_count is None
          else min_count)
    return bad_glyph_count(text) >= max(1, mc)


def garbled_reason(text: str,
                   min_tokens: int = None,
                   ratio: float = None) -> str:
    """เหตุผลว่าทำไม text layer นี้ใช้ไม่ได้ — ``""`` = ใช้ได้.

    คืน "เหตุผล" ไม่ใช่แค่ True/False เพราะสองด่านนี้เกิดจากคนละอาการและ
    ผู้ใช้ต้องไล่ต่อคนละทาง (ฟอนต์คายอักขระต้องห้าม vs คำผิดรูปทั้งบล็อก).
    """
    if not text:                      # None / "" — กันพังแทนที่จะเชื่อว่าเป็น str
        return ""
    if config.PDFTEXT_BAD_GLYPH_CHECK and text_has_bad_glyphs(text):
        return ("text layer ของโซนนี้มีอักขระที่เป็นไปไม่ได้ในข้อความจริง "
                "(%s) — ฟอนต์ในไฟล์แมปอักขระกลับเป็น Unicode ไม่ได้ "
                "จึงไม่ใช้ค่าจาก PDF" % bad_glyph_sample(text))
    mt = config.PDFTEXT_GARBLED_MIN_TOKENS if min_tokens is None else min_tokens
    rt = config.PDFTEXT_GARBLED_RATIO if ratio is None else ratio
    toks = _long_tokens(text)
    if len(toks) < max(1, mt):
        return ""
    bad = sum(1 for t in toks if _malformed(t))
    if (bad / float(len(toks))) >= rt:
        return ("text layer ของโซนนี้อ่านออกมาเป็นคำผิดรูป "
                "(ฟอนต์ในไฟล์แมปอักขระผิด) จึงไม่ใช้ค่าจาก PDF")
    return ""


def text_looks_garbled(text: str,
                       min_tokens: int = None,
                       ratio: float = None) -> bool:
    """True เมื่อข้อความจาก text layer หน้าตาเหมือน "ฟอนต์แมปอักขระผิด".

    สองด่านที่เป็นอิสระจากกัน (ผ่านทั้งคู่ = ใช้ได้):
      ① **อักขระต้องห้าม** (control / PUA / surrogate / unassigned / U+FFFD)
         — เจอตัวเดียวก็พอ ไม่ต้องมีคำยาวขั้นต่ำ
      ② **สัดส่วนคำผิดรูป** (ตัวเลขแทรกกลางคำ) — ตัดสินเฉพาะบล็อกที่มีคำยาว
         มากพอ (``min_tokens``) เพราะแถบรหัสงานพิมพ์มีคำแบบนั้นไม่กี่คำ.
         วัดกับไฟล์จริง 35 บล็อก: ฟ้องผิด 0.
    """
    return bool(garbled_reason(text, min_tokens=min_tokens, ratio=ratio))


def _ocr_in_bands(crop, zone_id: str):
    """อ่านภาพโซนแบบ "หั่นเป็นแถบตามช่องว่างระหว่างบรรทัด" (โหมดทดลอง).

    คืน ``(result | None, note)``. ``None`` = ให้ผู้เรียกใช้ทางเดิม
    (ยิงภาพเดียว) — เกิดเมื่อหาจุดตัดที่ปลอดภัยไม่ได้ หรือมีแถบใดอ่านพลาด.

    ⚠️ **ห้ามคืนข้อความที่ขาดไปบางแถบเด็ดขาด** — วัดแล้วว่าข้อความที่ขาด
    ครึ่งเดียวทำให้ชั้นเทียบฟ้อง defect ปลอม 30 รายการโดยไม่มีสัญญาณอะไร
    (แย่กว่า "อ่านไม่ได้" มาก) ⇒ แถบใดพลาด = ทิ้งผลทั้งชุด ถอยไปทางเดิม

    ⚠️ **``blocks`` ถูกทิ้งในโหมดนี้โดยตั้งใจ** — bbox ที่ backend คืนมา
    อ้างพิกัด "ของแถบนั้น" และแต่ละแถบขนาดต่างกัน เอามารวมเป็นชุดเดียวทำให้
    ``highlight._infer_scale`` เดา convention ผิด ⇒ กรอบแดงไปโผล่ผิดที่
    (กฎเหล็กข้อ 2: ไม่มีกรอบ ดีกว่ากรอบผิดตำแหน่ง). ชั้นกรอบแดงที่เหลือ
    (PDF word box / Tesseract) ยังทำงานตามปกติ
    """
    try:
        spans = bands_mod.find_bands(crop)
    except Exception:                            # pragma: no cover - กันพังล้วน
        logger.exception("[artwork] zone %s: หาจุดหั่นแถบไม่สำเร็จ", zone_id)
        return None, ""
    if len(spans) < 2:
        return None, ""

    texts, confs, engines = [], [], []
    for i, (y0, y1) in enumerate(spans, 1):
        piece = crop[y0:y1]
        if piece is None or piece.size == 0:
            return None, "หั่นแถบแล้วได้ภาพว่าง — ใช้การอ่านทั้งโซนแทน"
        try:
            r = vertex_client.ocr_image(encode_jpg(piece))
        except Exception as e:                   # pragma: no cover
            logger.exception("[artwork] zone %s แถบ %d: OCR ล้มเหลว",
                             zone_id, i)
            return None, ("อ่านแบบหั่นแถบไม่สำเร็จที่แถบ %d (%s) "
                          "— ใช้การอ่านทั้งโซนแทน" % (i, e))
        if not isinstance(r, dict) or r.get("error") or r.get("stub"):
            why = ((r.get("error") if isinstance(r, dict) else None)
                   or "คืนค่าผิดรูป/stub")
            return None, ("อ่านแบบหั่นแถบไม่สำเร็จที่แถบ %d (%s) "
                          "— ใช้การอ่านทั้งโซนแทน" % (i, why))
        texts.append((r.get("text") or "").strip())
        c = _mean_conf(r.get("blocks") or [])
        if c is not None:
            confs.append(c)
        if r.get("engine"):
            engines.append(str(r["engine"]))

    out = {
        "text": "\n".join(t for t in texts if t),
        "blocks": [],
        "engine": engines[0] if engines else "n8n",
        "conf": (sum(confs) / len(confs)) if confs else None,
    }
    note = ("อ่านแบบหั่นเป็น %d แถบ (โหมดทดลอง) — กรอบแดงชี้คำจาก OCR "
            "ถูกปิดในโหมดนี้" % len(spans))
    return out, note


def read_zone(doc: ArtworkDocument, zone: dict,
              page_auto: bool = False, force_ocr: bool = False,
              font_trust: dict = None, split_bands: bool = False) -> dict:
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

    ``force_ocr`` ข้ามชั้น text layer ทั้งหมดแล้วอ่านจากภาพเสมอ — ใช้เมื่อ
    ผู้ใช้รู้ว่าไฟล์นี้ฟอนต์พัง หรือเมื่อต้องการให้ทุกโซนในกลุ่มเดียวกันมา
    จาก engine เดียวกัน. **ถ้าไม่มี OCR backend ให้ใช้ จะไม่บังคับ** — ถอย
    ไปใช้ text layer ตามเดิมพร้อมโน้ตบอกเหตุผล ดีกว่าทิ้งข้อความที่อ่านได้
    อยู่แล้วไปแลกกับ UNREADABLE.

    ``split_bands`` (โหมดทดลอง) หั่นภาพโซนเป็นแถบตามช่องว่างระหว่างบรรทัด
    แล้วยิง OCR ทีละแถบ — ทำให้ตัวหนังสือใหญ่ขึ้นในสายตาโมเดลจริง (วัดได้
    71 -> 213 px บนโซนจริง) และลดอาการโมเดลรวบแถวที่ซ้ำกัน. ราคาคือยิง
    backend หลายครั้งต่อโซน. ไม่ติ๊ก = ทางเดิมเป๊ะ. ดู ``_ocr_in_bands``
    สำหรับกติกาความปลอดภัย (แถบใดพลาด = ถอยไปอ่านทั้งโซน)

    ``font_trust`` = ผลวิเคราะห์ระดับฟอนต์ของทั้งเอกสาร (``fonttrust.analyze``)
    ที่ ``pipeline`` คำนวณครั้งเดียวแล้วส่งต่อ — ใช้ปฏิเสธข้อความของฟอนต์ที่
    **พิสูจน์แล้วว่าพังที่อื่นในไฟล์เดียวกัน** แม้ข้อความในโซนนี้จะดูสะอาด
    (เคสจริง: ``MAČKY`` ออกมาเป็น ``MAÏ/=`` ซึ่งไม่มีอักขระต้องห้ามเลย)
    """
    bbox = zone["bbox"]
    forced = bool(force_ocr) and vertex_client.is_enabled()
    force_note = ""
    if force_ocr and not forced:
        force_note = ("สั่งให้ใช้ OCR แทน text layer แต่ไม่มี OCR backend "
                      "ให้ใช้ — ใช้ค่าจาก text layer ตามเดิม")

    embedded = "" if forced else doc.embedded_text(bbox)
    garbled = ""
    if len(embedded) >= config.EMBEDDED_TEXT_MIN_CHARS:
        reason = (garbled_reason(embedded) if config.PDFTEXT_GARBLED_CHECK
                  else "")
        if not reason:
            # ข้อความก้อนนี้ "ดูสะอาด" แต่ฟอนต์ที่พิมพ์มันอาจถูกพิสูจน์แล้วว่า
            # พังจากที่อื่นในไฟล์เดียวกัน — ถามหลักฐานระดับฟอนต์อีกชั้น
            reason = _font_evidence_reason(doc, bbox, font_trust)
        if reason:
            # text layer มีข้อความ "พอ" แต่ใช้ไม่ได้ (ฟอนต์แมปอักขระผิด).
            # ห้ามส่งต่อด้วย conf 1.0 — ตกไปอ่านจากภาพจริงแทน.
            garbled = reason
            logger.warning("[artwork] zone %s: text layer garbled (%d chars) "
                           "-> fall back to OCR", zone["id"], len(embedded))
        else:
            out = {"zone_id": zone["id"], "text": embedded,
                   "engine": "pdf-text", "conf": 1.0, "rotate": 0}
            if force_note:
                out["note"] = force_note
            return out

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

    # ⚠️ โซนเดียวพังต้องไม่ล้มการตรวจทั้งใบ — ``ocr_image`` สัญญาว่า
    # "Never raises" แต่ backend อื่น/``encode_jpg`` ยังโยนได้ ถ้าปล่อยหลุด
    # ขึ้นไปจะได้ HTTP 500 แทนรายงาน ⇒ ผู้ตรวจไม่ได้อะไรเลยแม้แต่โซนที่
    # อ่านสำเร็จ. แปลงเป็น UNREADABLE เฉพาะโซนนั้นแทน (กฎเหล็กข้อ 2:
    # บอกว่าอ่านไม่ได้ ดีกว่าไม่บอกอะไรเลย)
    band_note = ""
    result = None
    if split_bands:
        result, band_note = _ocr_in_bands(crop, zone["id"])
    try:
        if result is None:
            result = vertex_client.ocr_image(encode_jpg(crop))
    except Exception as e:                       # pragma: no cover - กันพังล้วน
        logger.exception("[artwork] zone %s: OCR backend ล้มเหลว", zone["id"])
        return {"zone_id": zone["id"], "text": "", "engine": "none",
                "conf": None, "rotate": angle,
                "error": "เรียก OCR backend ไม่สำเร็จ: %s" % e}
    if not isinstance(result, dict):
        return {"zone_id": zone["id"], "text": "", "engine": "none",
                "conf": None, "rotate": angle,
                "error": "OCR backend คืนค่าผิดรูป (%s)" % type(result).__name__}
    blocks = result.get("blocks") or []
    out = {
        "zone_id": zone["id"],
        "text": (result.get("text") or "").strip(),
        "engine": result.get("engine", "n8n"),
        "conf": (result["conf"] if "conf" in result
                 else _mean_conf(blocks)),
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
    if result.get("warning"):
        # backend อ่านสำเร็จ "ในทางเทคนิค" แต่รูปแบบคำตอบไม่ตรงสัญญา
        # (เช่นไม่ใช่ JSON) ⇒ ข้อความอาจปนขยะ. ไม่ทำให้โซนตกเป็น error
        # เพราะบาง workflow ตั้งให้คืน plain text จริง ๆ — แต่ต้องให้
        # ผู้ตรวจเห็น ไม่ใช่ทิ้งไปเงียบ ๆ อย่างที่เคยเป็น
        out["note"] = " · ".join(
            x for x in (out.get("note"), str(result["warning"])) if x)
    if band_note:
        # โหมดหั่นแถบ: บอกทั้งตอนสำเร็จ (กรอบแดงถูกปิด) และตอนถอยกลับ
        # ไปอ่านทั้งโซน (จะได้ไม่เข้าใจผิดว่าผลนี้มาจากการหั่น)
        out["note"] = " · ".join(x for x in (out.get("note"), band_note) if x)
    if garbled:
        # บอกไว้ในผลว่าทำไมโซนนี้ไม่ได้ใช้ text layer ทั้งที่ไฟล์มี —
        # ไม่ใช่ error (OCR อ่านสำเร็จ) แต่ผู้ตรวจควรรู้.
        # ต่อท้าย ไม่ทับ — โซนหนึ่งเจอได้ทั้งสองอย่างพร้อมกัน
        out["note"] = " · ".join(x for x in (garbled, out.get("note")) if x)
    if forced:
        # โซนนี้อาจมี text layer ที่ใช้ได้อยู่ แต่ถูกสั่งให้อ่านจากภาพแทน —
        # ต้องบอกไว้ ไม่งั้นผู้ตรวจจะไม่รู้ว่าข้อความที่เห็นมาจาก OCR
        out["forced_ocr"] = True
        out["note"] = " · ".join(
            x for x in ("อ่านด้วย OCR ตามที่สั่ง (ข้ามชั้น text layer)",
                        out.get("note")) if x)
    return out


def _font_evidence_reason(doc: ArtworkDocument, bbox,
                          font_trust: dict) -> str:
    """เหตุผลจาก "หลักฐานระดับฟอนต์" — ``""`` = ไม่มีข้อสงสัย.

    ราคาเป็นศูนย์กับไฟล์ปกติ: ถ้าไม่มีฟอนต์ไหนถูกพิสูจน์ว่าพัง จะไม่แตะ
    เอกสารเลย (ไม่มีการอ่าน span เพิ่ม)
    """
    if not font_trust or not font_trust.get("suspect"):
        return ""
    if font_trust.get("mode", "off") == "off":
        return ""
    try:
        spans = doc.text_spans(bbox)
    except Exception:            # pragma: no cover - ตัวช่วย ไม่ใช่ทางหลัก
        logger.debug("[artwork] font-evidence: อ่าน span ไม่ได้", exc_info=True)
        return ""
    return fonttrust.zone_reason(spans, font_trust)


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


def read_image(crop) -> dict:
    """อ่านข้อความจากภาพเล็ก ๆ ที่ให้มาตรง ๆ (ไม่ผ่านการเรนเดอร์โซน).

    ใช้กับโหมดเทียบพิกเซล: เมื่อรู้แล้วว่า "ต่างตรงไหน" ค่อยอ่านเฉพาะ
    บริเวณนั้น — ครอปเล็กอ่านได้นิ่งกว่าการอ่านทั้งแผงมาก และถึงอ่านไม่ได้
    เราก็ยังรู้ตำแหน่งอยู่ดี

    คืน ``{"text", "engine"}`` — ล้มเหลว/ไม่มี backend = ``text`` ว่าง
    **ไม่โยน exception และไม่เดา** (กฎเหล็กข้อ 2)
    """
    if crop is None or getattr(crop, "size", 0) == 0:
        return {"text": "", "engine": "none"}
    if not vertex_client.is_enabled():
        return {"text": "", "engine": "none"}
    try:
        r = vertex_client.ocr_image(encode_jpg(crop))
    except Exception:
        logger.exception("[artwork] read_image ล้มเหลว")
        return {"text": "", "engine": "none"}
    if not isinstance(r, dict) or r.get("error") or r.get("stub"):
        return {"text": "", "engine": str((r or {}).get("engine", "n8n"))}
    return {"text": (r.get("text") or "").strip(),
            "engine": str(r.get("engine", "n8n"))}


def read_all_zones(doc: ArtworkDocument, zones: List[dict],
                   page_auto: bool = False,
                   force_ocr: bool = False, split_bands: bool = False,
                   font_trust: dict = None) -> List[dict]:
    out = []
    for z in zones:
        if z.get("type") == "ignore":
            continue
        r = read_zone(doc, z, page_auto=page_auto, force_ocr=force_ocr,
                      split_bands=split_bands,
                      font_trust=font_trust)
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
