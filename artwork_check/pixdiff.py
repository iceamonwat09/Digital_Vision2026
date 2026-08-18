# -*- coding: utf-8 -*-
"""
เทียบ artwork "ฉบับเก่า ↔ ฉบับใหม่" ระดับพิกเซล (advisory ล้วน).

ทำไมต้องมีชั้นนี้ทั้งที่มีชั้นตรวจข้อความอยู่แล้ว
--------------------------------------------------
ชั้นที่มีอยู่ (MISMATCH / SPELL / NUMBER) **ต้องอ่านข้อความให้ออกก่อน**
จึงจับได้เฉพาะสิ่งที่เป็น "ตัวหนังสือ" และต้องลากโซนให้ตรงด้วย. การเทียบ
พิกเซลจับได้ทุกอย่างที่ตาเห็น — ฟอนต์เปลี่ยน สีเพี้ยน โลโก้ขยับ แท่ง
บาร์โค้ดเปลี่ยน — โดย **ไม่ง้อ OCR ไม่ง้อภาษา ไม่ง้อการลากโซน**.

หลักการที่ยึด (กฎเหล็กข้อ 2: ผลที่ผิดแบบมั่นใจ แย่กว่าไม่แสดงผล)
-----------------------------------------------------------------
* เทียบได้ต่อเมื่อ **สองไฟล์เป็นหน้าขนาดเดียวกัน** เท่านั้น. วัดมาแล้วว่า
  การย่อ/ขยายให้เท่ากันแบบ naive ทำให้เกิด **370 บริเวณปลอม** — ตัวเลข
  แบบนั้นทำให้ผู้ตรวจไล่ของปลอมทั้งวันแล้วเลิกเชื่อระบบ. ขนาดไม่ตรง =
  **ไม่เทียบ แล้วบอกเหตุผลเป็นตัวเลขจริง** ดีกว่าเดา.
* ทุกอย่างในโมดูลนี้เป็น **advisory 100%** — ไม่แตะ ``defects`` /
  ``verdict`` / การนับ / DB และไม่ถูกเรียกจาก ``run_inspection``.

ไม่ import Flask (เทสต์ตรง ๆ ได้) และไม่เขียนไฟล์ลงดิสก์เอง — ผู้เรียก
เป็นคนตัดสินใจว่าจะบันทึกอะไรที่ไหน.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional, Tuple

import cv2
import numpy as np

try:                                   # ให้เทสต์/สคริปต์ import ได้แม้ไม่มี fitz
    import fitz                        # PyMuPDF
except Exception:                      # pragma: no cover
    fitz = None

logger = logging.getLogger(__name__)

# ── ค่าคงที่ (Phase 1 เก็บไว้ในโมดูล; ตอนต่อ UI ค่อยย้ายเข้า config) ──
PIXDIFF_DPI = 200            # DPI ที่ใช้เทียบ — 200 พอเห็นความต่าง ~0.5pt
# ⚠️ ค่านี้ต้องแคบกว่า PIXEL_SIZE_TOL เมื่อแปลงเป็นพิกเซลที่ PIXDIFF_DPI
# (0.2mm = 0.57pt ≈ 1.6px ที่ 200 DPI < 2) ไม่งั้นจะมีเคสที่ "ผ่านด่าน mm
# แต่ตกด่านพิกเซล" แล้วผู้ใช้ได้เหตุผลที่ชี้ไปคนละเรื่อง.
# และเหตุผลที่ตั้งแคบมาก: หน้าที่ขนาดต่างกันแม้เศษมิลลิเมตร อาจแปลว่า
# **เนื้อหาถูกจัดใหม่ทั้งหน้า** ⇒ ครอปให้เท่ากันแล้วทุกตัวอักษรเลื่อน = ต่างทั้งใบ
PAGE_SIZE_TOL_MM = 0.2       # ขนาดหน้าต่างกันเกินนี้ = ไม่เทียบ
PIXEL_SIZE_TOL = 2           # ขนาดภาพที่เรนเดอร์ได้ต่างกันได้กี่พิกเซล (ปัดเศษ)
DIFF_THRESHOLD = 32          # 0-255: ต่างน้อยกว่านี้ถือว่าเป็น anti-alias noise
MIN_REGION_PX = 40           # บริเวณที่เล็กกว่านี้ (พิกเซล) ไม่รายงาน
MERGE_RADIUS_PX = 4          # รวมพิกเซลที่ต่างและอยู่ใกล้กันเป็นบริเวณเดียว
MAX_REGIONS = 200            # เพดานจำนวนบริเวณที่รายงาน (กันรายงานยาวไร้ประโยชน์)
# ต่างเกินสัดส่วนนี้ของหน้า = แทบไม่มีทางเป็น "การแก้ไขฉลาก" แต่เป็นคนละงาน /
# ทั้งหน้าเลื่อน / จัดเลย์เอาต์ใหม่ ⇒ รายงาน 500 กรอบไปก็ไม่มีใครไล่ไหว
MAX_DIFF_RATIO = 0.20

# ── โหมดโซน (เทียบเฉพาะแผงที่จับคู่กัน) ─────────────────────────────
# วัดจากไฟล์จริง: 2 ใน 3 คู่เทียบทั้งหน้าไม่ได้ (ขนาดหน้าต่างกัน / จัด layout
# ใหม่) — โหมดโซนคือทางเดียวที่ใช้ได้กับงานจริงส่วนใหญ่.
#
# ⚠️ ต่างจากโหมดทั้งหน้าตรงที่ **ต้อง align ก่อนเทียบ**: ผู้ใช้ลากโซนด้วยมือ
# ขอบสองฝั่งไม่มีทางตรงกันเป๊ะ ถ้าเทียบดิบ ๆ จะได้ false positive เต็มไปหมด.
# การเลื่อนที่ align ออกไป **ไม่ได้ถูกซ่อน** — คืนมาในฟิลด์ ``shift_mm``
# ให้ผู้ตรวจเห็นว่าเนื้อหาขยับไปเท่าไร
ZONE_ALIGN_MARGIN_MM = 6.0   # ขยายกรอบฝั่งอ้างอิงเพื่อให้มีที่ให้เลื่อนหา
MIN_MATCH_CONF = 0.55        # คะแนนจับคู่ต่ำกว่านี้ = ไม่มั่นใจ → ไม่รายงาน
MAX_ZONE_DIFF_RATIO = 0.35   # โซนต่างเกินนี้ = คนละเนื้อหา ไม่ใช่การแก้ไข
# ยอมให้เนื้อหาคลาดกันได้กี่พิกเซลก่อนนับว่า "ต่าง" — จำเป็นเฉพาะโหมดโซน
# เพราะ align ได้ละเอียดแค่ระดับพิกเซล แต่ของจริงคลาดกันเป็นเศษพิกเซล
ZONE_TOLERANCE_PX = 1
# โซนที่มีหมึกน้อยกว่านี้ = ไม่มีอะไรให้เทียบ (ดู zone_blank)
MIN_INK_RATIO = 0.002

# สถานะที่คืนได้ — ผู้เรียกต้องแยก "เทียบแล้วไม่ต่าง" ออกจาก "เทียบไม่ได้"
OK = "ok"
SKIPPED = "skipped"

_REASON_TEXT = {
    "page_size_mismatch":
        "ขนาดหน้าสองไฟล์ไม่เท่ากัน — เทียบพิกเซลไม่ได้ (การย่อ/ขยายให้เท่ากัน "
        "ทำให้เกิดบริเวณต่างปลอมจำนวนมาก)",
    "raster_size_mismatch":
        "ไฟล์ภาพสองไฟล์ขนาดพิกเซลไม่เท่ากัน — เทียบไม่ได้",
    "mixed_type":
        "เทียบได้เฉพาะไฟล์ชนิดเดียวกัน (PDF↔PDF หรือ ภาพ↔ภาพ)",
    "render_failed":
        "เรนเดอร์ไฟล์ใดไฟล์หนึ่งไม่สำเร็จ",
    "no_fitz":
        "ไม่ได้ติดตั้ง PyMuPDF (fitz) — เทียบ PDF ไม่ได้",
    "too_different":
        "ต่างกันมากเกินกว่าจะเป็นการแก้ไขฉลาก — น่าจะเป็นคนละงาน "
        "หรือเนื้อหาทั้งหน้าเลื่อน/จัดใหม่ จึงไม่รายงานรายบริเวณ",
    "file_not_found":
        "ไม่พบไฟล์",
    "align_failed":
        "จับคู่ตำแหน่งเนื้อหาในสองโซนไม่ได้ — อาจเป็นคนละเนื้อหา หรือโซนที่ลาก "
        "ครอบคนละส่วนกัน (ไม่รายงานดีกว่าชี้ผิดที่)",
    "zone_too_different":
        "เนื้อหาในโซนต่างกันมากเกินกว่าจะเป็นการแก้ไข — น่าจะเป็นคนละแผง",
    "zone_empty":
        "โซนที่ลากอยู่นอกหน้า หรือเล็กเกินกว่าจะเทียบได้",
    "zone_blank":
        "โซนแทบไม่มีเนื้อหาให้เทียบ (พื้นที่ว่าง) — การบอกว่า 'ไม่พบความต่าง' "
        "จากพื้นที่ว่างคือความมั่นใจปลอม",
    "not_pdf":
        "โหมดเทียบรายโซนรองรับเฉพาะ PDF (ต้องรู้ขนาดจริงเป็นมิลลิเมตร)",
}


def reason_text(reason: str) -> str:
    """คำอธิบายภาษาไทยของเหตุผลที่ไม่เทียบ (ใช้แสดงให้ผู้ตรวจอ่าน)."""
    return _REASON_TEXT.get(reason, reason or "")


# ── ขนาดหน้า ─────────────────────────────────────────────────────────
def page_size_mm(path: str, page_index: int = 0) -> Optional[Tuple[float, float]]:
    """ขนาดหน้าเป็นมิลลิเมตร (PDF เท่านั้น). คืน ``None`` ถ้าไม่ใช่ PDF/อ่านไม่ได้."""
    if fitz is None or not path.lower().endswith(".pdf"):
        return None
    try:
        with fitz.open(path) as doc:
            if not (0 <= page_index < doc.page_count):
                return None
            r = doc[page_index].rect
            return (r.width / 72.0 * 25.4, r.height / 72.0 * 25.4)
    except Exception:                       # pragma: no cover - ไฟล์เสีย
        return None


def _fmt_mm(size: Optional[Tuple[float, float]]) -> str:
    if not size:
        return "?"
    return "%.1f x %.1f mm" % size


def same_page_size(a: str, b: str, tol_mm: float = PAGE_SIZE_TOL_MM) -> bool:
    sa, sb = page_size_mm(a), page_size_mm(b)
    if not sa or not sb:
        return False
    return abs(sa[0] - sb[0]) <= tol_mm and abs(sa[1] - sb[1]) <= tol_mm


# ── การเทียบ ─────────────────────────────────────────────────────────
def _skip(reason: str, **extra) -> dict:
    out = {"status": SKIPPED, "reason": reason, "message": reason_text(reason),
           "regions": [], "diff_px": 0, "diff_ratio": 0.0}
    out.update(extra)
    return out


def _diff_mask(a, b, threshold: int, tolerance_px: int):
    """แผนที่พิกเซลที่ "ต่างจริง".

    ``tolerance_px = 0`` → เทียบตรง ๆ (ใช้กับโหมดทั้งหน้า ซึ่งสองไฟล์
    เรนเดอร์ลงกริดเดียวกัน จึงต้องเป๊ะได้).

    ``tolerance_px = n`` → พิกเซลจะนับว่าต่างก็ต่อเมื่อ **ไม่มีพิกเซลใดใน
    รัศมี n** ของอีกฝั่งที่ใกล้เคียงกันเลย. จำเป็นกับโหมดโซน เพราะแผง
    เดียวกันบนหน้าคนละขนาดจะตกลงบน "เศษส่วนพิกเซล" คนละค่า ⇒ ขอบตัวอักษร
    ทุกตัวต่างกันนิดเดียวทั้งแผง (วัดได้ 13 บริเวณปลอมจากการเลื่อน < 1px).
    ทำสองทางแล้วเอาค่ามากสุด เพื่อไม่ให้ผลขึ้นกับว่าไฟล์ไหนเป็น a หรือ b.
    """
    if tolerance_px <= 0:
        return (cv2.absdiff(a, b).max(axis=2) >= threshold).astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_RECT,
                                  (2 * tolerance_px + 1, 2 * tolerance_px + 1))
    ai, bi = a.astype(np.int16), b.astype(np.int16)
    a_min, a_max = cv2.erode(a, k).astype(np.int16), cv2.dilate(a, k).astype(np.int16)
    b_min, b_max = cv2.erode(b, k).astype(np.int16), cv2.dilate(b, k).astype(np.int16)
    d1 = np.maximum(ai - b_max, b_min - ai)
    d2 = np.maximum(bi - a_max, a_min - bi)
    d = np.maximum(d1, d2).max(axis=2)
    return (d >= threshold).astype(np.uint8) * 255


def compare_images(img_a, img_b,
                   threshold: int = DIFF_THRESHOLD,
                   min_region_px: int = MIN_REGION_PX,
                   merge_radius_px: int = MERGE_RADIUS_PX,
                   max_regions: int = MAX_REGIONS,
                   tolerance_px: int = 0) -> dict:
    """เทียบภาพ BGR สองภาพที่ **ขนาดเท่ากันแล้ว** → บริเวณที่ต่าง.

    คืน dict: ``status`` · ``regions`` (bbox เป็นสัดส่วน 0..1 ของหน้า +
    พื้นที่พิกเซล) · ``diff_px`` · ``diff_ratio``.
    """
    if img_a is None or img_b is None:
        return _skip("render_failed")
    ha, wa = img_a.shape[:2]
    hb, wb = img_b.shape[:2]
    # เรนเดอร์จาก DPI เดียวกันอาจต่างกัน 1 พิกเซลจากการปัดเศษ — ตัดให้เท่ากัน
    if abs(ha - hb) > PIXEL_SIZE_TOL or abs(wa - wb) > PIXEL_SIZE_TOL:
        return _skip("raster_size_mismatch",
                     size_a=[wa, ha], size_b=[wb, hb])
    h, w = min(ha, hb), min(wa, wb)
    a = img_a[:h, :w]
    b = img_b[:h, :w]

    # ต่างกันมากสุดในสามช่องสี = ไวกับสีเพี้ยนที่ความสว่างเท่าเดิม
    # (เทียบ grayscale อย่างเดียวจะมองไม่เห็นแดง↔เขียวที่ luminance ใกล้กัน)
    mask = _diff_mask(a, b, threshold, tolerance_px)
    diff_px = int(np.count_nonzero(mask))

    # ต่างกันทั้งใบ = ไม่ใช่ "การแก้ฉลาก" — รายงานรายบริเวณไปก็ไร้ประโยชน์
    # และทำให้ผู้ตรวจเลิกเชื่อชั้นนี้ (กฎเหล็กข้อ 2)
    ratio = diff_px / float(w * h)
    if ratio > MAX_DIFF_RATIO:
        out = _skip("too_different",
                    message="%s (ต่างกัน %.1f%% ของหน้า)"
                            % (reason_text("too_different"), ratio * 100))
        out["diff_px"] = diff_px
        out["diff_ratio"] = round(ratio, 8)
        out["size"] = [int(w), int(h)]
        return out

    regions: List[dict] = []
    if diff_px:
        # รวมพิกเซลที่อยู่ใกล้กันเป็นบริเวณเดียว ไม่งั้นตัวอักษรหนึ่งคำจะ
        # กลายเป็นสิบ ๆ บริเวณ (ผู้ตรวจอ่านไม่รู้เรื่อง)
        if merge_radius_px > 0:
            k = 2 * merge_radius_px + 1
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            merged = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            merged = cv2.dilate(merged, kernel, iterations=1)
        else:
            merged = mask
        n, _lbl, stats, _cent = cv2.connectedComponentsWithStats(merged, 8)
        for i in range(1, n):
            x, y, bw, bh, area = (stats[i, cv2.CC_STAT_LEFT],
                                  stats[i, cv2.CC_STAT_TOP],
                                  stats[i, cv2.CC_STAT_WIDTH],
                                  stats[i, cv2.CC_STAT_HEIGHT],
                                  stats[i, cv2.CC_STAT_AREA])
            # นับเฉพาะพิกเซลที่ "ต่างจริง" ในกรอบนี้ ไม่ใช่พื้นที่หลังขยาย
            real = int(np.count_nonzero(mask[y:y + bh, x:x + bw]))
            if real < min_region_px:
                continue
            # ⚠️ ต้อง cast เป็น float/int ของ Python ให้หมด — ค่าจาก numpy
            # (np.int32/np.float64) ทำให้ json.dumps โยน TypeError ตอนบันทึก
            # ลง report.json แบบไม่มีใครเห็นจนกว่าจะถึงหน้าเว็บ
            regions.append({
                "bbox": [round(float(x) / w, 5), round(float(y) / h, 5),
                         round(float(bw) / w, 5), round(float(bh) / h, 5)],
                "px": [int(x), int(y), int(bw), int(bh)],
                "area_px": int(real),
            })
        regions.sort(key=lambda r: r["area_px"], reverse=True)

    truncated = len(regions) > max_regions
    return {
        "status": OK,
        "reason": "",
        "message": "",
        "size": [int(w), int(h)],
        "regions": regions[:max_regions],
        "region_count": len(regions),
        "truncated": truncated,
        "diff_px": diff_px,
        "diff_ratio": round(diff_px / float(w * h), 8),
        "tolerance_px": int(tolerance_px),
    }


def compare_files(path_a: str, path_b: str,
                  dpi: int = PIXDIFF_DPI,
                  page_index: int = 0,
                  **kw) -> dict:
    """เทียบไฟล์สองไฟล์ (a = ฉบับใหม่, b = ฉบับอ้างอิง/ฉบับเก่า).

    เช็คขนาดหน้าก่อนเสมอ — ไม่เท่ากัน = ``status="skipped"`` พร้อมเหตุผล
    และตัวเลขขนาดจริงของทั้งสองไฟล์ (ไม่พยายาม align/ย่อขยายให้).
    """
    # ⚠️ เช็ค "มีไฟล์จริงไหม" ก่อนเสมอ — ไม่งั้นไฟล์ที่พิมพ์ชื่อผิด/ไม่มีอยู่
    # จะไปโผล่เป็น "เรนเดอร์ไม่สำเร็จ" ซึ่งชี้สาเหตุผิด แล้วผู้ใช้ไปไล่หา
    # ปัญหาที่ตัว PDF ทั้งที่แค่ path ผิด
    missing = [p for p in (path_a, path_b) if not os.path.isfile(p)]
    if missing:
        return _skip("file_not_found", missing=missing,
                     message="ไม่พบไฟล์: %s" % " · ".join(missing))

    a_pdf = path_a.lower().endswith(".pdf")
    b_pdf = path_b.lower().endswith(".pdf")
    if a_pdf != b_pdf:
        return _skip("mixed_type")

    size_a = size_b = None
    if a_pdf:
        if fitz is None:
            return _skip("no_fitz")
        size_a = page_size_mm(path_a, page_index)
        size_b = page_size_mm(path_b, page_index)
        if not size_a or not size_b:
            return _skip("render_failed")
        if (abs(size_a[0] - size_b[0]) > PAGE_SIZE_TOL_MM or
                abs(size_a[1] - size_b[1]) > PAGE_SIZE_TOL_MM):
            return _skip("page_size_mismatch",
                         page_size_a=[round(v, 2) for v in size_a],
                         page_size_b=[round(v, 2) for v in size_b],
                         message="%s — ไฟล์ใหม่ %s · ไฟล์อ้างอิง %s"
                                 % (reason_text("page_size_mismatch"),
                                    _fmt_mm(size_a), _fmt_mm(size_b)))

    try:
        img_a = _render(path_a, dpi, page_index)
        img_b = _render(path_b, dpi, page_index)
    except Exception as e:                  # pragma: no cover - ไฟล์เสีย
        logger.warning("[pixdiff] เรนเดอร์ไม่สำเร็จ: %s", e)
        return _skip("render_failed", message="%s (%s)"
                     % (reason_text("render_failed"), e))

    out = compare_images(img_a, img_b, **kw)
    out["dpi"] = dpi
    if size_a and size_b:
        out["page_size_a"] = [round(v, 2) for v in size_a]
        out["page_size_b"] = [round(v, 2) for v in size_b]
    return out


def _render(path: str, dpi: int, page_index: int = 0):
    """เรนเดอร์เป็น BGR — เส้นทางเดียวกับ ``pdf_ingest.ArtworkDocument.render``
    แต่แยกไว้เพื่อให้โมดูลนี้ไม่ผูกกับ pipeline (เทสต์ง่าย)."""
    if path.lower().endswith(".pdf"):
        with fitz.open(path) as doc:
            page = doc[page_index]
            zoom = dpi / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            img = np.frombuffer(pix.samples, dtype=np.uint8)
            img = img.reshape(pix.height, pix.width, pix.n)
            if pix.n == 3:
                return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("อ่านไฟล์ภาพไม่ได้: %s" % path)
    return img


# ── โหมดโซน: เทียบเฉพาะแผงที่จับคู่กัน ───────────────────────────────
def render_zone_mm(path: str, bbox, dpi: int, page_index: int = 0,
                   margin_mm: float = 0.0):
    """เรนเดอร์โซน (bbox เป็นสัดส่วน 0..1 ของหน้า) ที่ **สเกลจริงตาม mm**.

    หัวใจของโหมดโซน: สองไฟล์ที่หน้าคนละขนาดจะให้ภาพที่ ``mm ต่อพิกเซล``
    เท่ากันเสมอเมื่อเรนเดอร์ที่ DPI เดียวกัน ⇒ ฉลากขนาดจริงเท่ากันจะได้ภาพ
    ขนาดพิกเซลเท่ากัน แม้จะวางอยู่บนหน้า A4 กับหน้า 758mm ก็ตาม.
    """
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) is not installed")
    x, y, w, h = [float(v) for v in bbox]
    with fitz.open(path) as doc:
        page = doc[page_index]
        r = page.rect
        mpp = 25.4 / dpi                      # มิลลิเมตรต่อพิกเซล
        pad_pt = margin_mm / 25.4 * 72.0
        clip = fitz.Rect(
            max(r.x0, r.x0 + x * r.width - pad_pt),
            max(r.y0, r.y0 + y * r.height - pad_pt),
            min(r.x1, r.x0 + (x + w) * r.width + pad_pt),
            min(r.y1, r.y0 + (y + h) * r.height + pad_pt))
        if clip.width <= 1 or clip.height <= 1:
            return None, 0.0
        zoom = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip,
                              alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8)
        img = img.reshape(pix.height, pix.width, pix.n)
        img = (cv2.cvtColor(img, cv2.COLOR_RGB2BGR) if pix.n == 3
               else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
        return img, mpp


def content_bbox(path: str, page_index: int = 0, dpi: int = 72,
                 white_thresh: int = 245):
    """กรอบที่มี "หมึก" จริงบนหน้า → (bbox สัดส่วน, กว้าง mm, สูง mm).

    ใช้ตอบคำถามที่สำคัญที่สุดของการเทียบสองไฟล์ที่หน้าคนละขนาด:
    *เนื้อหาข้างในเป็นขนาดจริงเท่ากันไหม* (ถ้าเท่า = วางคนละที่บนแผ่นคนละ
    ขนาด เทียบรายโซนได้เลย · ถ้าไม่เท่า = ไฟล์หนึ่งถูกย่อ ต้องจัดการก่อน).
    """
    size = page_size_mm(path, page_index)
    if not size:
        return None, 0.0, 0.0
    try:
        img = _render(path, dpi, page_index)
    except Exception:                          # pragma: no cover
        return None, 0.0, 0.0
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ink = (gray < white_thresh).astype(np.uint8)
    if not np.count_nonzero(ink):
        return None, 0.0, 0.0
    x, y, w, h = cv2.boundingRect(ink)
    ih, iw = gray.shape[:2]
    bbox = [x / float(iw), y / float(ih), w / float(iw), h / float(ih)]
    return ([round(float(v), 5) for v in bbox],
            round(bbox[2] * size[0], 1), round(bbox[3] * size[1], 1))


def _ink_ratio(img) -> float:
    """สัดส่วนพิกเซลที่ "มีหมึก" (ไม่ใช่พื้นขาว) ในภาพ"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return float(np.count_nonzero(gray < 245)) / float(gray.size or 1)


def _locate(template, haystack):
    """หา template ใน haystack → (dx, dy, score). ใช้ NCC ซึ่งทนต่อความ
    ต่างของความสว่าง/คอนทราสต์เล็กน้อยจากการ export คนละรอบ."""
    th, tw = template.shape[:2]
    hh, hw = haystack.shape[:2]
    if th > hh or tw > hw:
        return None
    g_t = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    g_h = cv2.cvtColor(haystack, cv2.COLOR_BGR2GRAY)
    res = cv2.matchTemplate(g_h, g_t, cv2.TM_CCOEFF_NORMED)
    _minv, maxv, _minl, maxl = cv2.minMaxLoc(res)
    return int(maxl[0]), int(maxl[1]), float(maxv)


def compare_zone(path_a: str, bbox_a, path_b: str, bbox_b,
                 dpi: int = PIXDIFF_DPI,
                 page_index: int = 0,
                 margin_mm: float = ZONE_ALIGN_MARGIN_MM,
                 min_conf: float = MIN_MATCH_CONF,
                 **kw) -> dict:
    """เทียบ **โซนต่อโซน** ระหว่างสองไฟล์ที่หน้าคนละขนาดก็ได้.

    a = ฉบับใหม่ · b = ฉบับอ้างอิง. ``bbox_*`` เป็นสัดส่วน 0..1 ของหน้า
    ไฟล์ตัวเอง (คนละค่ากันได้ เพราะแผงอาจอยู่คนละตำแหน่งบนหน้า).

    ขั้นตอน: เรนเดอร์ทั้งสองที่สเกล mm เดียวกัน → หาตำแหน่งที่ตรงกันที่สุด
    (align เฉพาะการเลื่อน ไม่ยืด/บิดภาพ) → เทียบเฉพาะส่วนที่ซ้อนทับ.
    การเลื่อนที่พบรายงานกลับใน ``shift_mm`` — ไม่ซ่อนจากผู้ตรวจ.
    """
    missing = [p for p in (path_a, path_b) if not os.path.isfile(p)]
    if missing:
        return _skip("file_not_found", missing=missing,
                     message="ไม่พบไฟล์: %s" % " · ".join(missing))
    if not (path_a.lower().endswith(".pdf") and path_b.lower().endswith(".pdf")):
        return _skip("not_pdf")
    if fitz is None:
        return _skip("no_fitz")

    try:
        img_a, mpp = render_zone_mm(path_a, bbox_a, dpi, page_index)
        # ฝั่งอ้างอิงเรนเดอร์เผื่อขอบ เพื่อให้มีที่ให้ align เลื่อนหา
        img_b, _ = render_zone_mm(path_b, bbox_b, dpi, page_index,
                                  margin_mm=margin_mm)
    except Exception as e:                    # pragma: no cover
        return _skip("render_failed",
                     message="%s (%s)" % (reason_text("render_failed"), e))
    if img_a is None or img_b is None or img_a.size == 0 or img_b.size == 0:
        return _skip("zone_empty")

    # ⚠️ โซนว่างเทียบกับโซนว่าง = "ไม่พบความต่าง" ทั้งที่ไม่ได้ตรวจอะไรเลย
    # (template ที่เป็นสีขาวล้วนจับคู่ได้คะแนนเต็มกับพื้นที่ขาวที่ไหนก็ได้)
    # นี่คือความมั่นใจปลอมแบบที่กฎเหล็กข้อ 2 ห้ามไว้ตรง ๆ
    ink_a, ink_b = _ink_ratio(img_a), _ink_ratio(img_b)
    if min(ink_a, ink_b) < MIN_INK_RATIO:
        return _skip("zone_blank",
                     ink_ratio=[round(ink_a, 5), round(ink_b, 5)],
                     message="%s (มีหมึก %.2f%% และ %.2f%% ของโซน)"
                             % (reason_text("zone_blank"),
                                ink_a * 100, ink_b * 100))

    # โซนที่เล็กกว่าเป็น template — ทนต่อการที่ผู้ใช้ลากสองฝั่งไม่เท่ากัน
    swapped = False
    tpl, hay = img_a, img_b
    if tpl.shape[0] > hay.shape[0] or tpl.shape[1] > hay.shape[1]:
        tpl, hay = img_b, img_a
        swapped = True

    found = _locate(tpl, hay)
    if not found:
        return _skip("align_failed",
                     message="%s (ขนาดโซนสองฝั่งต่างกันมากเกินไป)"
                             % reason_text("align_failed"))
    dx, dy, score = found
    if score < min_conf:
        return _skip("align_failed",
                     match_score=round(score, 4),
                     message="%s (คะแนนจับคู่ %.2f ต่ำกว่าเกณฑ์ %.2f)"
                             % (reason_text("align_failed"), score, min_conf))

    th, tw = tpl.shape[:2]
    win = hay[dy:dy + th, dx:dx + tw]
    a_img, b_img = (win, tpl) if swapped else (tpl, win)

    kw.setdefault("tolerance_px", ZONE_TOLERANCE_PX)
    out = compare_images(a_img, b_img, **kw)
    if out["status"] == OK and out["diff_ratio"] > MAX_ZONE_DIFF_RATIO:
        out = _skip("zone_too_different",
                    message="%s (ต่างกัน %.1f%% ของโซน)"
                            % (reason_text("zone_too_different"),
                               out["diff_ratio"] * 100),
                    diff_px=out["diff_px"], diff_ratio=out["diff_ratio"])

    # ระยะที่เนื้อหาขยับ (เทียบกับจุดที่คาดว่าจะตรงกันพอดี = ขอบเผื่อ)
    pad_px = int(round(margin_mm / mpp)) if mpp else 0
    off_x, off_y = (dx - pad_px), (dy - pad_px)
    if swapped:
        off_x, off_y = -off_x, -off_y
    out["match_score"] = round(score, 4)
    out["shift_px"] = [int(off_x), int(off_y)]
    out["shift_mm"] = [round(off_x * mpp, 2), round(off_y * mpp, 2)]
    out["mm_per_px"] = round(mpp, 5)
    out["dpi"] = dpi
    out["zone_size_mm"] = [round(tw * mpp, 1), round(th * mpp, 1)]
    return out


# ── ภาพประกอบผล (display-only) ───────────────────────────────────────
def draw_regions(img, regions, color=(0, 140, 255), thickness: int = 3,
                 pad: int = 6):
    """วาดกรอบส้มรอบบริเวณที่ต่าง ลงบนสำเนาของ ``img`` (ไม่แก้ต้นฉบับ)."""
    out = img.copy()
    h, w = out.shape[:2]
    for r in regions:
        x, y, bw, bh = r["px"]
        cv2.rectangle(out,
                      (max(0, x - pad), max(0, y - pad)),
                      (min(w - 1, x + bw + pad), min(h - 1, y + bh + pad)),
                      color, thickness)
    return out
