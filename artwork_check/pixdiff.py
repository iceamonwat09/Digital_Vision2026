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


def compare_images(img_a, img_b,
                   threshold: int = DIFF_THRESHOLD,
                   min_region_px: int = MIN_REGION_PX,
                   merge_radius_px: int = MERGE_RADIUS_PX,
                   max_regions: int = MAX_REGIONS) -> dict:
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
    delta = cv2.absdiff(a, b).max(axis=2)
    mask = (delta >= threshold).astype(np.uint8) * 255
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
    }


def compare_files(path_a: str, path_b: str,
                  dpi: int = PIXDIFF_DPI,
                  page_index: int = 0,
                  **kw) -> dict:
    """เทียบไฟล์สองไฟล์ (a = ฉบับใหม่, b = ฉบับอ้างอิง/ฉบับเก่า).

    เช็คขนาดหน้าก่อนเสมอ — ไม่เท่ากัน = ``status="skipped"`` พร้อมเหตุผล
    และตัวเลขขนาดจริงของทั้งสองไฟล์ (ไม่พยายาม align/ย่อขยายให้).
    """
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
