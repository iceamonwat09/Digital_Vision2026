"""
Zone model, automatic text-zone proposal and zone-template storage.

A zone is a dict:

    {
      "id":    "z3",
      "type":  "panel" | "zoom" | "header" | "ignore",
      "group": "A",                  # zones sharing a group are expected
                                     # to carry IDENTICAL text and are
                                     # cross-compared (majority voting);
                                     # "" = standalone zone
      "bbox":  [x, y, w, h],         # normalized 0..1 page coordinates
      "label": "SIDE 1",             # free text for the human
      "doc":   "a" | "b"             # which uploaded file the zone lives
                                     # on: "a" = primary artwork (default,
                                     # the only doc before the cross-file
                                     # compare feature), "b" = optional
                                     # reference file (ฉบับเก่า)
    }

Zones from doc "a" and doc "b" sharing a ``group`` are compared by the
same majority-vote / zoom-reference logic in ``checks.py`` — the check
layers never look at ``doc``, only at the text keyed by zone id.

Auto-proposal only has to be *good enough to adjust*, not perfect — the
UI lets the user move/resize/retype every box, and the layout can be
saved as a template per print house so this is a one-time job per form.
"""

from __future__ import annotations

# ── Highlight feasibility (advisory — never affects the verdict) ──────
# Measured on real artwork: the red word-box needs the zone's text to be
# roughly 9-20 px tall in the rendered crop. A zone drawn WIDE (the whole
# printed strip) renders to something like 1600x340 after the crop's
# max-side cap, its small print lands at ~8 px, and word localisation
# collapses (0/14 target words found; raising the resolution only reached
# 6/14 because the wide crop also mixes graphics, photos and several
# scripts). A zone drawn tightly around the same table renders ~1450x990,
# the text sits at 12-20 px, and localisation is 14/14.
#
# The short side of the crop separates those cases cleanly on every file
# tested (fails: 340, 487 · works: 895, 988, 1186), so it is what we warn
# on. Pure arithmetic — no rendering, no OCR — so it is free to compute
# and safe to show while the human is still editing zones.
HL_MIN_SHORT_SIDE = 700
# 4:1 already separates "a text block" from "a whole printed strip" on the
# files tested (the failing strip zone came out 1600x339 = 4.7:1).
HL_MAX_ASPECT = 4.0


def predict_crop_size(bbox, page_w_pt: float, page_h_pt: float,
                      dpi: int, max_side: int = 1600,
                      min_side: int = 1200) -> tuple:
    """Pixel size of the crop ``pipeline.zone_crop_jpg`` would produce for
    ``bbox`` — same dpi cap and small-zone boost, done in arithmetic."""
    x, y, w, h = [float(v) for v in bbox]
    pw = max(1.0, w * page_w_pt) / 72.0 * dpi
    ph = max(1.0, h * page_h_pt) / 72.0 * dpi
    longest = max(pw, ph)
    if longest > max_side:                     # max-side cap
        s = max_side / longest
        pw, ph = pw * s, ph * s
    longest = max(pw, ph)
    if longest < min_side:                     # small-zone re-render boost
        s = min(4.0, min_side / longest)
        pw, ph = pw * s, ph * s
        longest = max(pw, ph)
        if longest > max_side:
            s = max_side / longest
            pw, ph = pw * s, ph * s
    return int(round(pw)), int(round(ph))


def highlight_risk(bbox, page_w_pt: float, page_h_pt: float,
                   dpi: int) -> str:
    """"" when the zone should highlight fine, else a short reason code:
    ``"wide"`` (extreme aspect ratio) or ``"small"`` (crop too small for
    the text to be readable). Advisory only."""
    try:
        pw, ph = predict_crop_size(bbox, page_w_pt, page_h_pt, dpi)
    except Exception:
        return ""
    if min(pw, ph) <= 0:
        return "small"
    aspect = max(pw, ph) / float(min(pw, ph))
    if min(pw, ph) < HL_MIN_SHORT_SIDE:
        return "wide" if aspect >= HL_MAX_ASPECT else "small"
    return ""

import json
import math
import os
import re
import time
from typing import List

import cv2
import numpy as np

from . import config


VALID_TYPES = ("panel", "zoom", "header", "ignore")


# ── ขนาดโซน ↔ ความละเอียดที่ตัวอ่าน OCR ได้จริง (advisory ล้วน) ────────
# Google ประกาศกฎการรับภาพของ Gemini ไว้ว่า: ภาพที่ด้านใดด้านหนึ่งเกิน
# 384 px จะถูกหั่นเป็นไทล์ขนาด clamp(min(W,H)/1.5, 256, 768) แล้ว
# **ทุกไทล์ถูกขยายเป็น 768x768** ⇒ กำลังขยายที่โมเดลเห็น = 768 / tile.
#
# ผลที่สวนสามัญสำนึกและเป็นหัวใจของด่านนี้: **ยิ่งด้านสั้นของภาพใหญ่
# ยิ่งได้ขยายน้อยลง** และพอด้านสั้นแตะ 768*1.5 = 1152 px ก็ไม่ได้ขยาย
# อีกเลยไม่ว่าจะส่งภาพใหญ่แค่ไหน (= 65.0 mm ที่ OCR_DPI 450)
#
# วัดกับไฟล์จริง (John West · แผงโภชนาการเดียวกันของสองไฟล์ ·
# gemini-2.5-flash · อ่าน 3 รอบ):
#   69.8x66.2 mm -> 1236x1172 px -> 4 ไทล์ -> ขยาย 1.00 -> เลขอาหรับ
#                   (٧ ١ ٠) หายเกือบทุกรอบ  => defect ปลอม 3-5 รายการ
#   72.8x50.4 mm -> 1289x892  px -> 6 ไทล์ -> ขยาย 1.29 -> อ่านครบทุกรอบ
# เกณฑ์ข้างล่างตั้งจากช่องว่างที่วัดได้นั้น ไม่ใช่จากทฤษฎี
#
# ⚠️ ค่าเหล่านี้ถูกคัดลอกไว้ใน static/js/artwork_check.js ด้วย (คำเตือน
#    ต้องขึ้นตอนกำลังลากโซน = ต้องคำนวณฝั่งเบราว์เซอร์) —
#    tests/test_artwork_zone_quality.py อ่านไฟล์ JS มาเทียบกันกันค่าเพี้ยน
GEM_SMALL_SIDE = 384        # ทั้งสองด้าน <= นี้ = ไทล์เดียว ไม่หั่น
GEM_TILE_DIV = 1.5
GEM_TILE_MIN = 256
GEM_TILE_MAX = 768
# ── เกณฑ์ตัดสิน: "ความละเอียดที่โมเดลเห็น (dpi)" ────────────────────
#
# ⚠️ เดิมตัดสินด้วย **กำลังขยาย (mag)** ซึ่งวัดแล้วว่า **ไร้ความหมายกับ
#    โซนเล็ก** — ชั้นเพิ่ม DPI (OCR_CROP_MIN_SIDE) ตรึงด้านยาวของภาพที่ส่ง
#    ไว้ที่ 1200 px ⇒ กำลังขยายกับความละเอียดต้นทางหักล้างกันพอดี วัดได้ว่า
#    โซน 28x29 (เต็มแผงพอดี) กับ 28x34 (เลยขอบแผง) โมเดลเห็นเท่ากันเป๊ะ
#    (~1050 dpi ทั้งคู่) แต่ป้ายเดิมให้ ✗ กับ ✓ ⇒ **บอกให้ผู้ใช้ตัดเนื้อหาทิ้ง**
#    ซึ่งแย่กว่าไม่แนะนำเลย (กฎเหล็กข้อ 2)
#
# dpi ที่โมเดลเห็นรวมทุกชั้นแล้ว (เพิ่ม DPI + ย่อเพราะชนเพดาน + กำลังขยาย
# จากการหั่นไทล์) ⇒ ใช้ได้ทั้งโซนเล็กและโซนใหญ่ และตรงกับที่วัดจริง 2 จุด:
#   450 dpi (= พื้นของ OCR_DPI พอดี) -> เลขอาหรับหายเกือบทุกรอบ
#   581 dpi                           -> อ่านครบทุกรอบ
# ⚠️ **มีแค่ 2 จุด** — เกณฑ์ข้างล่างวางให้คร่อมทั้งสอง ไม่ใช่เส้นที่พิสูจน์แล้ว
ZONE_DPI_OK = 580.0         # >= นี้ = เท่ากับเคสที่วัดว่าอ่านครบ
ZONE_DPI_BAD = 500.0        # < นี้ = ใกล้พื้น 450 ซึ่งวัดว่าอ่านตก
# 1 ไทล์ = 258 token เสมอ (ทุกไทล์ถูกขยายเป็น 768x768 เท่ากันหมด) ⇒
# "จำนวนไทล์" คือ **โควตาความสนใจ** ที่โมเดลมีให้กับภาพนี้ และเป็นตัวเลข
# ที่ผู้ใช้เทียบสองโซนกันได้ตรง ๆ
#
# ⚠️ ทำไมต้องพูดถึงไทล์ ไม่ใช่ "กำลังขยาย": วัดสองโซนที่ผลอ่านต่างกันจริง
#    แล้วพบว่า **ตัวหนังสือในสายตาโมเดลเท่ากัน** (z1 = 71.1 px · b2 = 68.9
#    px ต่างกัน 3%) เพราะแผงของ z1 ใหญ่กว่าบนแผ่นจริง กำลังขยายที่น้อยกว่า
#    จึงหักล้างกันพอดี ⇒ ป้ายเดิมที่เขียนว่า "ไม่ได้ขยาย" **บอกสาเหตุผิด**
#    สิ่งที่ต่างจริงคือ token ต่อบรรทัด (z1 ~62 · b2 ~93) ⇒ z1 ต้องเลือกว่า
#    จะพิมพ์อะไรทิ้ง แล้วแถวสั้น ๆ ที่ซ้ำกันหายก่อน (ตรงกับที่วัดได้จริง)
GEM_TOKENS_PER_TILE = 258
# ⚠️ กับดักที่วัดเจอตอนทดสอบบนเบราว์เซอร์: เกณฑ์จริงคือ "ด้านสั้นของ **ภาพ
#    ที่ส่งจริง**" ไม่ใช่ "ด้านสั้นของโซนเป็นมิลลิเมตร" — เพราะชั้นเพิ่ม DPI
#    ให้โซนเล็ก (OCR_CROP_MIN_SIDE = 1200) ดันโซน "เกือบจัตุรัส" ให้ทั้งสอง
#    ด้านไปอยู่ใกล้ 1200 พร้อมกัน ⇒ ด้านสั้นทะลุ 1152 ทั้งที่โซนเล็กกว่า 65 mm
#    วัดจริงบนโซนกว้าง 60 mm:  สูง 50 -> ok · 60-62 -> bad · 66 -> warn · 70 -> warn
#    ⇒ ข้อความบน UI ต้องอ้าง "ด้านสั้นของภาพที่ส่ง (px)" และแนะให้ทำโซนให้
#      **แบนลง** (ไม่ใช่แค่ "เล็กลง") ห้ามไปอ้างเลข mm ตายตัว


def ocr_crop_size(bbox, page_w_pt: float, page_h_pt: float) -> tuple:
    """``(กว้าง_px, สูง_px, ตัวคูณ)`` ของภาพที่ ``ocr._render_for_ocr``
    จะส่งให้ OCR backend สำหรับ ``bbox`` นี้ — เลขคณิตล้วน ไม่เรนเดอร์.

    ``ตัวคูณ < 1.0`` = ภาพโดนเพดาน ``OCR_CROP_MAX_SIDE`` ย่อลง
    (เสีย dpi จริง ไม่ใช่แค่เสียกำลังขยาย).
    """
    x, y, w, h = [float(v) for v in bbox]
    pw = max(1.0, w * page_w_pt) / 72.0 * config.OCR_DPI
    ph = max(1.0, h * page_h_pt) / 72.0 * config.OCR_DPI
    scale = 1.0
    longest = max(pw, ph)
    if config.OCR_CROP_MAX_SIDE and longest > config.OCR_CROP_MAX_SIDE:
        scale = config.OCR_CROP_MAX_SIDE / longest
        pw, ph = pw * scale, ph * scale
    # ชั้น "เพิ่ม DPI ให้โซนเล็ก" ของ _render_for_ocr — ผลลัพธ์ไม่มีทาง
    # เกิน OCR_CROP_MIN_SIDE (factor = min(4, MIN/longest)) จึงไม่ต้อง
    # เช็คเพดานซ้ำเหมือน predict_crop_size ของชั้นกรอบแดง
    longest = max(pw, ph)
    if config.OCR_CROP_MIN_SIDE and longest < config.OCR_CROP_MIN_SIDE:
        f = min(config.OCR_DPI_MAX_FACTOR,
                config.OCR_CROP_MIN_SIDE / longest)
        pw, ph = pw * f, ph * f
    return pw, ph, scale


def gemini_tiling(w_px: float, h_px: float) -> tuple:
    """``(จำนวนไทล์, กำลังขยาย)`` ตามกฎที่ Google ประกาศ."""
    if w_px <= GEM_SMALL_SIDE and h_px <= GEM_SMALL_SIDE:
        return 1, 1.0
    tile = min(w_px, h_px) / GEM_TILE_DIV
    tile = max(float(GEM_TILE_MIN), min(float(GEM_TILE_MAX), tile))
    return (int(math.ceil(w_px / tile)) * int(math.ceil(h_px / tile)),
            GEM_TILE_MAX / tile)


def zone_short_side_limit_mm() -> float:
    """ด้านสั้นของโซน (มม.) ที่เกินแล้วจะไม่ได้กำลังขยายจาก OCR อีกเลย."""
    return GEM_TILE_MAX * GEM_TILE_DIV / float(config.OCR_DPI) * 25.4


def zone_ocr_quality(bbox, page_w_pt: float, page_h_pt: float) -> dict:
    """โซนนี้จะถูกส่งให้ OCR ด้วยความละเอียดที่โมเดลเห็นเท่าไร.

    ``level``:
      ``"bad"``  โดนย่อเพราะชนเพดาน หรือไม่ได้กำลังขยายเลย
      ``"warn"`` ได้ขยายบ้างแต่ยังต่ำกว่าที่วัดว่าปลอดภัย
      ``"ok"``   ปลอดภัย
    advisory 100% — ไม่ห้ามวาด ไม่แตะ defect/verdict/การนับ
    """
    try:
        pw, ph, scale = ocr_crop_size(bbox, page_w_pt, page_h_pt)
        tiles, mag = gemini_tiling(pw, ph)
    except Exception:                            # pragma: no cover
        return {}
    w_mm = max(1e-6, float(bbox[2]) * page_w_pt) / 72.0 * 25.4
    h_mm = max(1e-6, float(bbox[3]) * page_h_pt) / 72.0 * 25.4
    downscaled = scale < 0.999
    # ⚠️ ห้ามคิดจาก ``scale`` — มันรายงานเฉพาะ "ย่อเพราะชนเพดาน" ไม่รวม
    #    ชั้นเพิ่ม DPI ให้โซนเล็ก ⇒ eff_dpi เดิมรายงาน 450 ให้โซนที่จริง ๆ
    #    ถูกเรนเดอร์ที่ 1051 dpi (ต่ำกว่าความจริง 2.3 เท่า)
    eff_dpi = pw / max(1e-6, w_mm / 25.4) * mag
    if eff_dpi >= ZONE_DPI_OK:
        level = "ok"
    elif eff_dpi >= ZONE_DPI_BAD:
        level = "warn"
    else:
        level = "bad"
    return {"level": level, "w": int(round(pw)), "h": int(round(ph)),
            "tiles": tiles, "mag": round(mag, 2),
            "tokens": int(tiles) * GEM_TOKENS_PER_TILE,
            # ด้านของไทล์ (px ของภาพต้นทางที่ 1 ไทล์กิน) — ตัวเลขเดียวที่
            # **เทียบข้ามขนาดโซนได้** เพราะไม่ขึ้นกับพื้นที่: ยิ่งเล็ก =
            # สุ่มตัวอย่างละเอียด = token ต่อเนื้อหาเท่าเดิมยิ่งมาก และ
            # ตันที่ GEM_TILE_MAX (768) ซึ่งคือเงื่อนไขของ level "bad"
            #   ⚠️ "จำนวนไทล์" ห้ามเอาไปเป็นป้ายตัดสิน — มันโตตามพื้นที่
            #      โซนด้วย ⇒ โซนใหญ่ได้ไทล์เยอะทั้งที่หยาบที่สุด
            "tile_px": int(round(GEM_TILE_MAX / mag)),
            "eff_dpi": int(round(eff_dpi)),
            "w_mm": round(w_mm, 1), "h_mm": round(h_mm, 1),
            "short_mm": round(min(w_mm, h_mm), 1),
            "short_px": int(round(min(pw, ph))),
            "limit_px": int(round(GEM_TILE_MAX * GEM_TILE_DIV)),
            "limit_mm": round(zone_short_side_limit_mm(), 1),
            "downscaled": downscaled}


# ลำดับ group อัตโนมัติ — ข้าม I/O กันสับสนกับเลข 1/0 (ชุดเดียวกับที่
# ฝั่ง JS ใช้ตอนลากวาดโซนเพิ่มเอง — ห้ามแก้ข้างเดียว)
GROUP_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"


def seq_group(i: int) -> str:
    """Group ลำดับที่ ``i`` (เริ่ม 0): A..Z แล้วต่อ A2..Z2, A3.. —
    ใช้ร่วมกันทั้งการเสนอโซนไฟล์หลักและไฟล์ชิ้นงาน เพื่อให้โซนลำดับ
    เดียวกันของสองไฟล์ได้ group ตรงกันและจับคู่เทียบข้ามไฟล์อัตโนมัติ."""
    n = len(GROUP_LETTERS)
    letter = GROUP_LETTERS[i % n]
    rnd = i // n
    return letter if rnd == 0 else f"{letter}{rnd + 1}"


def propose_zones(preview_bgr: np.ndarray,
                  max_zones: int = 24) -> List[dict]:
    """
    Suggest text-bearing zones on the rendered page.

    Morphological closing over an inverted-threshold image merges glyphs
    into blocks; blocks are filtered by size and returned largest-first.
    Groups are assigned SEQUENTIALLY in reading order (A, B, C, …) so
    the same-ordinal zone of the other file in cross-file compare gets
    the same group and pairs automatically. (Replaced the old
    size-cluster heuristic — approved 2026-07-20: it never fired on the
    real photo-label workflow and could silently mis-pair same-size
    blocks; repeated-panel dielines can still share a group manually or
    via a saved template.)
    """
    gray = cv2.cvtColor(preview_bgr, cv2.COLOR_BGR2GRAY)
    H, W = gray.shape[:2]

    # Ink mask: anything noticeably darker than paper.
    _, ink = cv2.threshold(gray, 0, 255,
                           cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Merge characters → words → blocks. Kernel scales with page size.
    # (W//100, H//120, 1 iteration) measured best on real print masters:
    # large enough to merge a text panel, small enough not to swallow
    # the whole dieline into one blob.
    kw = max(8, W // 100)
    kh = max(6, H // 120)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kw, kh))
    blocks = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, kernel, iterations=1)

    contours, _ = cv2.findContours(blocks, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    min_area = (W * H) * 0.0004      # drop specks / dieline tick marks
    max_area = (W * H) * 0.35        # blobs above this get re-split
    rects = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        if area < min_area:
            continue
        if area <= max_area:
            rects.append((x, y, w, h))
        else:
            # A whole row of carton panels often merges into one blob
            # (panels touch via the dieline). Re-segment that region
            # with a smaller kernel to split it into its panels.
            sub = ink[y:y + h, x:x + w]
            k2 = cv2.getStructuringElement(
                cv2.MORPH_RECT, (max(4, kw // 3), max(3, kh // 3)))
            sub_blocks = cv2.morphologyEx(sub, cv2.MORPH_CLOSE, k2)
            sub_cnts, _ = cv2.findContours(sub_blocks, cv2.RETR_EXTERNAL,
                                           cv2.CHAIN_APPROX_SIMPLE)
            for sc in sub_cnts:
                sx, sy, sw, sh = cv2.boundingRect(sc)
                if min_area <= sw * sh <= max_area:
                    rects.append((x + sx, y + sy, sw, sh))
    rects.sort(key=lambda r: r[2] * r[3], reverse=True)
    rects = rects[:max_zones]
    # Reading order for stable ids.
    rects.sort(key=lambda r: (r[1] // max(1, H // 20), r[0]))

    # Sequential groups in reading order (z1→A, z2→B, …). Single-member
    # groups are kept — voting needs ≥2 readable panels in a group, so a
    # lone letter can never produce a defect; its partner arrives later
    # from the reference file (or a hand-drawn zone with the same letter).
    zones: List[dict] = []
    for i, (x, y, w, h) in enumerate(rects):
        zones.append({
            "id": f"z{i + 1}",
            "type": "panel",
            "group": seq_group(i),
            "bbox": [round(x / W, 5), round(y / H, 5),
                     round(w / W, 5), round(h / H, 5)],
            "label": f"โซน {i + 1}",
        })
    return zones


def snap_bbox(preview_bgr: np.ndarray, bbox: List[float],
              pad: float = 0.08) -> List[float]:
    """
    Fit a user-drawn bbox to the content under it (double-click in UI).

    The box is first expanded by ``pad`` (fraction of its own size) so
    content the user accidentally cut off is recovered, then shrunk to
    the tight bounds of "non-background" pixels. Background is sampled
    from the border ring of the expanded crop, which makes the result
    polarity-independent: a red/navy panel on a white page snaps to the
    panel edge, while a box inside a flat panel snaps to its text block.

    Repeated calls keep growing toward content cut off farther than one
    pad step, so double-clicking again refines the fit. Returns the
    original bbox unchanged when no usable content is found.
    """
    H, W = preview_bgr.shape[:2]
    x, y, w, h = bbox
    px, py = w * pad, h * pad
    x0 = max(0, int(round((x - px) * W)))
    y0 = max(0, int(round((y - py) * H)))
    x1 = min(W, int(round((x + w + px) * W)))
    y1 = min(H, int(round((y + h + py) * H)))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return [round(float(v), 5) for v in bbox]

    gray = cv2.cvtColor(preview_bgr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    ch, cw = gray.shape[:2]

    # background reference = median of the crop's border ring
    t = max(2, min(ch, cw) // 100)
    border = np.concatenate([gray[:t].ravel(), gray[-t:].ravel(),
                             gray[:, :t].ravel(), gray[:, -t:].ravel()])
    bg = float(np.median(border))
    mask = (np.abs(gray.astype(np.int16) - bg) > 24).astype(np.uint8)

    # rows/cols with almost no content are specks or neighbour slivers —
    # they must not stretch the snapped box
    col_density = mask.sum(axis=0) / float(ch)
    row_density = mask.sum(axis=1) / float(cw)
    keep_x = np.where(col_density > 0.01)[0]
    keep_y = np.where(row_density > 0.01)[0]
    if keep_x.size < 4 or keep_y.size < 4:
        return [round(float(v), 5) for v in bbox]

    m = max(2, min(ch, cw) // 150)            # small visual margin
    nx0 = max(0, int(keep_x[0]) - m) + x0
    nx1 = min(cw, int(keep_x[-1]) + 1 + m) + x0
    ny0 = max(0, int(keep_y[0]) - m) + y0
    ny1 = min(ch, int(keep_y[-1]) + 1 + m) + y0
    return [round(nx0 / W, 5), round(ny0 / H, 5),
            round((nx1 - nx0) / W, 5), round((ny1 - ny0) / H, 5)]


def autopair_bbox(preview_a: np.ndarray, preview_b: np.ndarray,
                  bbox: List[float], scales: List[float] = None,
                  ) -> tuple:
    """
    Locate the content block of a doc-A zone on doc-B (cross-file pairing).

    Crops the patch under ``bbox`` from ``preview_a`` and searches for the
    same block on ``preview_b`` with ``cv2.matchTemplate`` (TM_CCOEFF_NORMED
    on grayscale). Returns ``(bbox_b, conf)`` where ``bbox_b`` is the matched
    box on B (normalized 0..1, same block size) and ``conf`` is the match
    score 0..1. The caller decides whether ``conf`` is high enough to trust
    (``config.AUTOPAIR_MIN_CONF``) — this function never fabricates a box, it
    only reports the best location and how confident it is.

    ``matchTemplate`` is not scale-invariant, so the patch is tried at a few
    scales (``scales``, default ``config.AUTOPAIR_SCALES``) to tolerate A/B
    rendered at slightly different scales; the highest-scoring scale wins.
    Returns ``(None, 0.0)`` when the patch can't be placed at any scale.
    """
    if scales is None:
        scales = config.AUTOPAIR_SCALES or [1.0]
    Ha, Wa = preview_a.shape[:2]
    Hb, Wb = preview_b.shape[:2]
    x, y, w, h = bbox
    ax0 = max(0, min(Wa - 1, int(round(x * Wa))))
    ay0 = max(0, min(Ha - 1, int(round(y * Ha))))
    ax1 = max(ax0 + 1, min(Wa, int(round((x + w) * Wa))))
    ay1 = max(ay0 + 1, min(Ha, int(round((y + h) * Ha))))
    patch = cv2.cvtColor(preview_a[ay0:ay1, ax0:ax1], cv2.COLOR_BGR2GRAY)
    bg = cv2.cvtColor(preview_b, cv2.COLOR_BGR2GRAY)
    ph, pw = patch.shape[:2]
    if ph < 4 or pw < 4:
        return None, 0.0

    best = None       # (conf, x0, y0, tw, th)
    for s in scales:
        tw, th = int(round(pw * s)), int(round(ph * s))
        if tw < 8 or th < 8 or tw > Wb or th > Hb:
            continue
        interp = cv2.INTER_AREA if s < 1.0 else cv2.INTER_CUBIC
        tmpl = patch if s == 1.0 else cv2.resize(patch, (tw, th),
                                                 interpolation=interp)
        res = cv2.matchTemplate(bg, tmpl, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if best is None or mx > best[0]:
            best = (float(mx), loc[0], loc[1], tw, th)
    if best is None:
        return None, 0.0

    conf, bx0, by0, bw, bh = best
    bbox_b = [round(bx0 / Wb, 5), round(by0 / Hb, 5),
              round(bw / Wb, 5), round(bh / Hb, 5)]
    return bbox_b, round(conf, 4)


def sanitize_zones(raw) -> List[dict]:
    """Validate zones arriving from the browser. Raises ValueError."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("zones must be a non-empty list")
    out = []
    seen_ids = set()
    for i, z in enumerate(raw):
        if not isinstance(z, dict):
            raise ValueError(f"zone {i} is not an object")
        zid = str(z.get("id") or f"z{i + 1}")
        if zid in seen_ids:
            raise ValueError(f"duplicate zone id {zid}")
        seen_ids.add(zid)
        ztype = str(z.get("type", "panel")).lower()
        if ztype not in VALID_TYPES:
            raise ValueError(f"zone {zid}: bad type {ztype!r}")
        bbox = z.get("bbox")
        if (not isinstance(bbox, (list, tuple)) or len(bbox) != 4):
            raise ValueError(f"zone {zid}: bbox must be [x,y,w,h]")
        x, y, w, h = (float(v) for v in bbox)
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1):
            raise ValueError(f"zone {zid}: bbox out of 0..1 range")
        x, y = max(0.0, x), max(0.0, y)
        w, h = min(w, 1.0 - x), min(h, 1.0 - y)
        # doc: which uploaded file the zone belongs to. Absent (all
        # payloads/templates from before the cross-file compare feature)
        # → "a", so the single-file flow is byte-for-byte unchanged.
        doc = str(z.get("doc", "a") or "a").lower()
        if doc not in ("a", "b"):
            raise ValueError(f"zone {zid}: bad doc {doc!r}")
        # rotate: OCR crop orientation. Absent (old payloads/templates) →
        # "default" = follow the page-level auto-rotate toggle (itself
        # OFF by default) → no rotation → identical to current behavior.
        # "auto" = always auto-detect this zone; 0/90/180/270 = pinned
        # clockwise angle. Numeric strings are accepted from JSON.
        rot = z.get("rotate", "default")
        if isinstance(rot, bool):
            rot = "default"
        elif isinstance(rot, str) and rot.strip().lstrip("-").isdigit():
            rot = int(rot)
        if rot in (0, 90, 180, 270):
            rotate = int(rot)
        elif rot in ("auto", "default"):
            rotate = rot
        else:
            rotate = "default"
        out.append({
            "id": zid,
            "type": ztype,
            "group": re.sub(r"[^A-Za-z0-9_-]", "", str(z.get("group", "")))[:12],
            "bbox": [round(x, 5), round(y, 5), round(w, 5), round(h, 5)],
            "label": str(z.get("label", ""))[:80],
            "doc": doc,
            "rotate": rotate,
        })
    return out


# ── Zone templates (per print-house layout) ───────────────────────────

def _template_path(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9ก-๛_ -]", "", name).strip()
    if not safe:
        raise ValueError("invalid template name")
    return os.path.join(config.TEMPLATES_DIR, f"{safe}.json")


def list_templates() -> List[dict]:
    out = []
    for fn in sorted(os.listdir(config.TEMPLATES_DIR)):
        if fn.endswith(".json"):
            out.append({"name": fn[:-5]})
    return out


def save_template(name: str, zones: List[dict]) -> None:
    data = {"name": name, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "zones": sanitize_zones(zones)}
    with open(_template_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_template(name: str) -> List[dict]:
    with open(_template_path(name), encoding="utf-8") as f:
        return sanitize_zones(json.load(f).get("zones", []))
