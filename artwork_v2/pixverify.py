"""หลักฐานภาพ — ยืนยันจุดต่างทุกจุดด้วยหมึกจริงบนไฟล์ PDF (7 ต.ค. · ข้อสรุปทีม)

คำถามเดียวต่อจุดต่าง: **หมึกตรงนี้ของ A กับ B เหมือนกันไหม** — ตอบด้วยการเรนเดอร์ไฟล์
ต้นฉบับใหม่ในเครื่อง (ไม่ยิง Vision ไม่ยิง Gemini) เพราะต้นเหตุของจุดแดงหลอกคือ OCR อ่าน
พิกเซลเดียวกันออกมาต่างกัน (จุดอักษรอาหรับ ~1-2 px · Vision จัดบรรทัดต่างกัน)

ขั้นตอน (ค่าทั้งหมดวัดจาก Log สถานี 13 รอบ · ``team/pixel.md``):

1. **ทาบโซนทั้งคู่ครั้งเดียว** — ORB + ECC affine (ภาพโซนที่ส่ง A → B) · ดูดซับการลากโซนต่างกัน
   และขนาดพิมพ์ต่างกัน (AvoDerm 🅱 เล็กกว่า 10%)
2. **ครอปทีละจุด** เผื่อขอบรอบกรอบ · เรนเดอร์สูงสุด 1600 dpi (ไม่เกิน 1800 px) · ฝั่ง B ที่สเกล
   จริงเดียวกัน + ช่องค้นหา 0.5 mm · template match + ECC เฉพาะที่
3. **หมึกเทียบพื้นหลังเฉพาะที่** ทั้งสองขั้ว (black-hat / top-hat 0.8 mm) ⇒ ตัวอักษรสีอ่อน ·
   ตัวขาวบนพื้นเข้ม · พื้นไล่สี ใช้ได้
4. **ก้อนที่ต่าง** = XOR ของหน้ากากหมึก (ยอมเลื่อน ±1 px) นับเมื่อ **ทั้งสองข้อ**:
   พื้นที่ ≥ 6 px ที่ 1600 dpi และ ≥ 5% ของพิกเซลต่างกัน ≥ 40 ระดับเทา (ยอมเลื่อน ±1 px)
   — ก้อนหลอกจากขอบรอยหยัก/การทาบมีค่านี้ 0.00 ทุกก้อน (19/19)
5. **ตัดสินต่อการตรวจ**: หมึกฝั่งเดียว ⇒ DIFF · ncc < 0.60 ⇒ UNVERIFIABLE · มีก้อนที่นับ ⇒ DIFF ·
   ความเข้มของหมึกร่วมต่างเกิน 40 ⇒ DIFF · ncc ≥ 0.95 ⇒ SAME · ที่เหลือ UNVERIFIABLE
6. **จุดต่าง = SAME เมื่อทุกการตรวจ SAME** — ทุกสมาชิกของการ์ดรวม และ (โหมดบรรทัด) ทั้งบรรทัด
   OCR ของทั้งสองฝั่ง ⇒ กันกรอบที่ Vision วางผิดคำบนบรรทัดที่ต่างจริง

ผล: SAME ⇒ ย้ายไปรายการพับ ``pixel_same`` พร้อมภาพ A | B | ต่าง (**ไม่ลบ**) · DIFF ⇒ คงไว้ +
ป้าย "ภาพยืนยันว่าต่าง" · UNVERIFIABLE ⇒ คงไว้ตามเดิม · ไฟล์ใดไม่ใช่ PDF ⇒ ไม่แตะเลย

วัดบน 13 รอบสถานี: แดงหลอก 20/20 → SAME · ของจริง 0/59 → SAME · การแก้ที่ใส่เอง 511 แบบ
จับได้ 94.9% · แก้จุด/ฮัมซะอาหรับในตาราง John West 190/190 · ทดสอบแบบกันข้อมูล (จูนบน AvoDerm
ทดสอบ John West) 18/18 · ข้อจำกัด: การแก้ < 0.005 mm² จับได้บางส่วน · สีที่ความสว่างเท่าเดิม
มองไม่เห็น · ใช้ได้เฉพาะ PDF ↔ PDF

**ภาพสแกน (``PIXEL_RASTER`` · 7 ต.ค. รอบ 5 · Friskies):** โซนที่เป็นภาพสแกนล้วน (``zone_raster``)
เทียบที่ความละเอียดจริงของภาพนั้น (ไม่ขยายจุดรบกวน JPEG/การสแกนขึ้นไป 1600 dpi) + เบลอ σ 0.8 px
ทั้งสองฝั่ง · Friskies (🅱 JPEG 300 dpi): ภาพเหมือน 0 → 18 จุด · ลบเครื่องหมายเล็กทั้งตัว 40 จุด
พลาด 0 · ไฟล์สังเคราะห์ 200-600 dpi × JPEG q75/q92: ลบจุดทั้งจุดไม่มีทาง SAME · สแกน < 200 dpi
และ PDF เวกเตอร์ = เส้นทางเดิมทุกพิกเซล
"""

from __future__ import annotations

import math
import os
import time
from typing import Dict, List, Optional

import numpy as np

try:
    import cv2
except ImportError:          # pragma: no cover
    cv2 = None

try:
    import fitz
except ImportError:          # pragma: no cover
    fitz = None

from . import config

if fitz is not None:
    try:
        fitz.TOOLS.mupdf_display_errors(False)
    except Exception:        # noqa: BLE001  pragma: no cover
        pass

TOL = 1             # ยอมเลื่อน ±px
NCC_SAME = 0.95
NCC_MIN = 0.60
BG_MM = 0.8         # ขนาดเคอร์เนลพื้นหลังเฉพาะที่
INK_T = 50
SEARCH_MM = 0.5
DPI_MAX = 1600.0
CROP_MAX = 1800.0
GRAY_DIFF = 40
GRAY_FRAC = 0.05
MIN_AREA = 6.0      # px ที่ 1600 dpi
TONE_MAX = 40.0
BIG = 600           # การ์ดรวมที่กรอบใหญ่เกินนี้ (px ภาพที่ส่ง) ⇒ ตรวจสมาชิกแทนกรอบรวม
CHUNK = 280         # โหมดบรรทัด: แบ่งบรรทัดยาวเป็นท่อน ๆ (คงความละเอียด 1600 dpi)
RASTER_COVER = 0.90  # ภาพเดียวต้องคลุมโซนอย่างน้อยเท่านี้ถึงนับว่าโซนเป็น "ภาพสแกน"
RASTER_MIN_DPI = 200.0  # สแกนหยาบกว่านี้ = จุด/จุดทศนิยมเหลือ ~2 px (150 dpi ลบจุดทั้งจุดแล้วยังพลาด 3/30) ⇒ เส้นทางเดิม
RASTER_LOW_CONTRAST = 120.0  # ``_contrast`` · สแกนพื้นขาว ≥ 144 · พื้นเทา (สังเคราะห์) ≤ 71 · Friskies ≤ 103 · ใช้กับ PIXEL_RASTER_INK_T
RASTER_SIGMA = 0.8   # เบลอ (px ที่ความละเอียดจริง) ทั้งสองฝั่ง — วัดบน Friskies: σ 0.8 = 18 จุดภาพเหมือน · mutation 0/40 พลาด (σ 1.0 พลาด 1 · σ 0.7 ภาพเหมือนน้อยลง)
# ทุกอย่างที่ "วาด" ลงหน้า ยกเว้นภาพ และข้อความที่มองไม่เห็น (ชั้น OCR ของไฟล์สแกน = ignore-text)
_RASTER_SKIP = ("fill-image", "fill-imgmask", "ignore-text", "clip", "pop", "begin", "end")


def zone_raster(page, rect_pt) -> Optional[dict]:
    """โซนนี้เป็น "ภาพสแกนล้วน" ไหม · คืน ``{"dpi", "cover"}`` หรือ ``None``

    ต้องครบสองข้อ: ภาพเดียวคลุมโซน ≥ ``RASTER_COVER`` **และ** ในโซนไม่มีของเวกเตอร์ใด ๆ
    (ข้อความที่มองเห็น/เส้น/พื้นไล่สี) — artwork ที่วางข้อความเวกเตอร์ทับภาพพื้นหลังความละเอียดต่ำ
    (AvoDerm M2: ภาพ 72 dpi คลุมโซน 92% + เส้น 1,187 ชิ้น) ต้องไม่เข้าเงื่อนไข ไม่งั้นเทียบที่ 72 dpi
    ความละเอียดจริง = จำนวนพิกเซลต่อความยาวบนหน้า (ไม่ขึ้นกับการหมุนภาพบนหน้า) · ใช้แกนที่หยาบกว่า"""
    return raster_check(page, rect_pt)[0]


def _rect_area(r) -> float:
    """พื้นที่ของ ``fitz.Rect`` ที่ไม่ขึ้นกับรุ่นของ PyMuPDF — เมธอด ``get_area`` ของ Rect ไม่มีในบางรุ่น
    (สถานีใช้ 1.26.5 แล้วได้ ``AttributeError`` ⇒ ชั้นภาพสแกนไม่เคยทำงานบนสถานี) · สี่เหลี่ยมว่าง/กลับด้าน = 0"""
    r = fitz.Rect(r)
    return max(0.0, float(r.width)) * max(0.0, float(r.height))


def raster_check(page, rect_pt):
    """เหมือน ``zone_raster`` แต่คืน ``(ผล, เหตุผล)`` — เหตุผลเป็นข้อความสั้นสำหรับ Log เสมอ
    (ทั้งตอนเป็นและไม่เป็นภาพสแกน) ⇒ ไฟล์ที่คาดว่าเป็นสแกนแต่ไม่เข้าเงื่อนไข บอกได้ว่าติดข้อไหน"""
    zr = fitz.Rect(*rect_pt)
    if page.rotation:        # get_image_info/get_bboxlog ใช้พิกัดของหน้าที่ยังไม่หมุน
        zr = zr * page.derotation_matrix
        zr.normalize()
    za = _rect_area(zr)
    if za <= 0:
        return None, "โซนว่าง"
    best, top_cov, n_img = None, 0.0, 0
    for im in page.get_image_info():
        t = im.get("transform") or (0, 0, 0, 0, 0, 0)
        sx, sy = math.hypot(t[0], t[1]), math.hypot(t[2], t[3])
        if sx <= 0 or sy <= 0 or not im.get("width") or not im.get("height"):
            continue
        n_img += 1
        cov = _rect_area(fitz.Rect(im["bbox"]) & zr) / za
        top_cov = max(top_cov, cov)
        if cov >= RASTER_COVER and (best is None or cov > best["cover"]):
            best = {"cover": round(cov, 3),
                    "dpi": round(min(im["width"] / (sx / 72.0), im["height"] / (sy / 72.0)), 1)}
    if best is None:
        return None, "ไม่มีภาพเดียวคลุมโซน ≥ %d%% (ภาพ %d ชิ้น · คลุมมากสุด %d%%)" % (
            RASTER_COVER * 100, n_img, round(top_cov * 100))
    if best["dpi"] < RASTER_MIN_DPI:
        return None, "ภาพคลุม %d%% แต่ %g dpi < %g" % (round(best["cover"] * 100), best["dpi"], RASTER_MIN_DPI)
    kinds = {}
    for kind, r in page.get_bboxlog():
        if kind.startswith(_RASTER_SKIP):
            continue
        if not (fitz.Rect(r) & zr).is_empty:
            kinds[kind] = kinds.get(kind, 0) + 1
    if kinds:
        return None, "ภาพ %g dpi คลุม %d%% แต่มีของเวกเตอร์ในโซน %s" % (
            best["dpi"], round(best["cover"] * 100), dict(sorted(kinds.items())))
    return best, "ภาพสแกน %g dpi คลุม %d%%" % (best["dpi"], round(best["cover"] * 100))


def _odd(n) -> int:
    n = int(round(n))
    return n + 1 - n % 2


class _Doc:
    def __init__(self, path: str):
        self.doc = fitz.open(path)

    def render(self, page: int, rect_pt, dpi: float) -> np.ndarray:
        p = self.doc[page]
        pix = p.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0),
                           clip=fitz.Rect(*rect_pt), alpha=False)
        a = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)
        if pix.n >= 3:
            return cv2.cvtColor(np.ascontiguousarray(a[:, :, :3]), cv2.COLOR_RGB2GRAY)
        return a[:, :, 0].copy()

    def page_pt(self, page: int):
        r = self.doc[page].rect
        return r.width, r.height

    def close(self):
        try:
            self.doc.close()
        except Exception:    # noqa: BLE001
            pass


def _rot(g, rot):
    if rot == 90:
        return cv2.rotate(g, cv2.ROTATE_90_CLOCKWISE)
    if rot == 180:
        return cv2.rotate(g, cv2.ROTATE_180)
    if rot == 270:
        return cv2.rotate(g, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return g


class PairCheck:
    """คู่โซนหนึ่งคู่ · ``zA``/``zB`` = {"page", "bbox", "W", "H", "rot"?} (W/H = ขนาดภาพที่ส่ง)

    ``rot`` = มุมที่ภาพที่ส่งถูกหมุน (ตามเข็ม) ⇒ ทุกพิกัด/ภาพในคลาสนี้อยู่ในแนวของภาพที่ส่ง ·
    ไม่มี/0 = เส้นทางเดิมเป๊ะ"""

    def __init__(self, path_a: str, path_b: str, zA: dict, zB: dict):
        self.dA, self.dB = _Doc(path_a), _Doc(path_b)
        self.z = {"A": zA, "B": zB}
        self.rot = {"A": int(zA.get("rot") or 0), "B": int(zB.get("rot") or 0)}
        self.geo = {}
        self.wh0 = {}
        for s, d in (("A", self.dA), ("B", self.dB)):
            z = self.z[s]
            pw, ph = d.page_pt(z["page"])
            x, y, w, h = z["bbox"]
            W0, H0 = (z["H"], z["W"]) if self.rot[s] in (90, 270) else (z["W"], z["H"])
            self.wh0[s] = (W0, H0)
            self.geo[s] = (x * pw, y * ph, w * pw / W0, h * ph / H0)
        self.ginfo: dict = {}
        # ภาพสแกน (``zone_raster``) ต่อฝั่ง · ปิดธง/ตรวจไม่ได้ = None = เส้นทางเดิมทุกพิกเซล
        self.raster = {"A": None, "B": None}
        self.raster_why = {}     # เหตุผลต่อฝั่ง (Log) — ตรวจไม่ได้ ≠ ไม่ใช่ภาพสแกน ต้องเห็นต่างกัน
        if config.PIXEL_RASTER:
            for s, d in (("A", self.dA), ("B", self.dB)):
                try:
                    z = self.z[s]
                    self.raster[s], self.raster_why[s] = raster_check(
                        d.doc[z["page"]], self._box_pt(s, (0, 0, z["W"], z["H"])))
                except Exception as e:    # noqa: BLE001
                    self.raster[s] = None
                    self.raster_why[s] = "ตรวจไม่ได้ (%s: %s)" % (type(e).__name__, str(e)[:120])
        self.ok = self._global_align()
        if self.ok and (self.raster["A"] or self.raster["B"]):
            self.ginfo["raster"] = {s.lower(): self.raster[s] for s in ("A", "B")}

    def close(self):
        self.dA.close()
        self.dB.close()

    def _px2pt(self, s, x, y):
        g = self.geo[s]
        return g[0] + x * g[2], g[1] + y * g[3]

    def _box_pt(self, s, b):
        """กรอบบนภาพที่ส่ง (แนวที่หมุนแล้ว) → สี่เหลี่ยมบนหน้า (pt)"""
        r = self.rot[s]
        if r:
            W, H = self.z[s]["W"], self.z[s]["H"]
            x0, y0, x1, y1 = b
            if r == 90:
                b = (y0, W - x1, y1, W - x0)
            elif r == 180:
                b = (W - x1, H - y1, W - x0, H - y0)
            else:
                b = (H - y1, x0, H - y0, x1)
        return self._px2pt(s, b[0], b[1]) + self._px2pt(s, b[2], b[3])

    def _render(self, s, b, dpi):
        d = self.dA if s == "A" else self.dB
        return _rot(d.render(self.z[s]["page"], self._box_pt(s, b), dpi), self.rot[s])

    def _zone_img(self, s, dpi_scale=1.0):
        z, g = self.z[s], self.geo[s]
        dpi = 72.0 / g[2] * dpi_scale
        return self._render(s, (0, 0, z["W"], z["H"]), dpi)

    def _global_align(self) -> bool:
        t0 = time.time()
        s = min(1.0, 1100.0 / max(self.z["A"]["W"], self.z["B"]["W"]))
        sa, sb = self._zone_img("A", s), self._zone_img("B", s)
        orb = cv2.ORB_create(5000)
        ka, da = orb.detectAndCompute(sa, None)
        kb, db = orb.detectAndCompute(sb, None)
        if da is None or db is None or len(ka) < 8 or len(kb) < 8:
            self.ginfo = {"error": "จุดเด่นในภาพไม่พอให้ทาบ"}
            return False
        mt = sorted(cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db),
                    key=lambda m: m.distance)[:1000]
        if len(mt) < 8:
            self.ginfo = {"error": "จับคู่จุดเด่นไม่พอ"}
            return False
        M, inl = cv2.estimateAffinePartial2D(
            np.float32([ka[m.queryIdx].pt for m in mt]), np.float32([kb[m.trainIdx].pt for m in mt]),
            method=cv2.RANSAC, ransacReprojThreshold=2.0)
        if M is None:
            self.ginfo = {"error": "หาการแปลงไม่ได้"}
            return False
        warp = M.astype(np.float32)
        H = max(sa.shape[0], sb.shape[0])
        Wd = max(sa.shape[1], sb.shape[1])

        def pad(g):
            return cv2.copyMakeBorder(g, 0, H - g.shape[0], 0, Wd - g.shape[1],
                                      cv2.BORDER_CONSTANT, value=255).astype(np.float32)
        try:
            ecc, warp = cv2.findTransformECC(
                pad(sa), pad(sb), warp, cv2.MOTION_AFFINE,
                (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6), None, 5)
        except cv2.error:
            ecc = float("nan")
        W = warp.astype(np.float64).copy()
        W[:, 2] /= s
        self.W = W
        self.inv = cv2.invertAffineTransform(W)
        self.gscale = math.sqrt(abs(np.linalg.det(W[:, :2])))
        self.ginfo = {"inliers": int(inl.sum()) if inl is not None else 0,
                      "ecc": None if ecc != ecc else round(float(ecc), 4),
                      "scale": round(self.gscale, 4), "tx": round(float(W[0, 2]), 1),
                      "ty": round(float(W[1, 2]), 1), "ms": int((time.time() - t0) * 1000)}
        return True

    def _crops(self, side, box):
        x0, y0, x1, y1 = box
        if side == "B":
            p = self.inv @ np.array([[x0, x1, x0, x1], [y0, y0, y1, y1], [1, 1, 1, 1]])
            x0, x1, y0, y1 = p[0].min(), p[0].max(), p[1].min(), p[1].max()
        h = max(y1 - y0, 12)
        w = x1 - x0
        pad = 0.6 * min(h, max(w, 12)) + 6
        bA = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
        longpx = max(bA[2] - bA[0], bA[3] - bA[1])
        dpiA_sent = 72.0 / self.geo["A"][2]
        dpi = min(DPI_MAX, CROP_MAX / longpx * dpiA_sent)
        q = self.W @ np.array([[bA[0], bA[2], bA[0], bA[2]], [bA[1], bA[1], bA[3], bA[3]],
                               [1, 1, 1, 1]])
        m = SEARCH_MM / 25.4 * 72 / self.geo["B"][2]
        bB = (q[0].min() - m, q[1].min() - m, q[0].max() + m, q[1].max() + m)
        phys = self.gscale * self.geo["B"][2] / self.geo["A"][2]
        ra, rb = self.raster["A"], self.raster["B"]
        if ra or rb:     # ไม่เรนเดอร์ละเอียดเกินพิกเซลที่ภาพสแกนมีจริง (ฝั่ง B คิดเป็นสเกลของ A)
            dpi = min([dpi] + ([ra["dpi"]] if ra else []) + ([rb["dpi"] * phys] if rb else []))
        pa = self._render("A", bA, dpi)
        pb = self._render("B", bB, dpi / phys)
        if ra or rb:
            pa = cv2.GaussianBlur(pa, (0, 0), RASTER_SIGMA)
            pb = cv2.GaussianBlur(pb, (0, 0), RASTER_SIGMA)
        return pa, pb, dpi

    def check(self, side, box) -> dict:
        t0 = time.time()
        pa, pb, dpi = self._crops(side, box)
        pb2, ncc = _align(pa, pb)
        # คู่ภาพสแกน: ตัดสิน "สีต่างจริงไหม" ที่ตำแหน่งเดิม (ไม่ยอมเลื่อน) — ที่ ~300 dpi เส้นบางกว้างแค่ 2-3 px
        # การยอมเลื่อน ±TOL จะไปเจอพื้นหลังข้างเส้นเสมอ ⇒ เส้นที่หายทั้งเส้นถูกนับว่า "ไม่ต่าง" (``PIXEL_RASTER_STRICT_GRAY``)
        scan = bool(self.raster["A"] or self.raster["B"])
        strict = bool(config.PIXEL_RASTER_STRICT_GRAY and scan)
        # คู่ภาพสแกนบนพื้นสีกลาง: จุดเล็ก (~0.17 mm ที่ 300 dpi) หลังเบลอเข้มไม่ถึง ``INK_T`` ทั้งสองฝั่ง ⇒ ถือว่า "ว่างทั้งคู่"
        # (เฉพาะครอปที่ตัวอักษรกับพื้นต่างกันน้อย — พื้นขาวไม่ต้องลด: สแกน 200 dpi q75 จะพับได้น้อยลงโดยไม่ได้อะไร)
        ink_t = None
        if scan and 0 < float(config.PIXEL_RASTER_INK_T) < INK_T and _contrast(pa, pb2) < RASTER_LOW_CONTRAST:
            ink_t = float(config.PIXEL_RASTER_INK_T)
        c = _compare(pa, pb2, dpi, gray_tol=0 if strict else TOL, ink_t=ink_t)
        emptyA, emptyB = c["inkA"] < 15, c["inkB"] < 15
        if emptyA != emptyB:
            st = "DIFF"
        elif not (ncc == ncc) or ncc < NCC_MIN:
            st = "UNVERIFIABLE"
        elif c["sig"]:
            st = "DIFF"
        elif c["tone"] > TONE_MAX:
            st = "DIFF"
        elif ncc >= NCC_SAME:
            st = "SAME"
        else:
            st = "UNVERIFIABLE"
        c.update(status=st, side=side, ncc=None if ncc != ncc else round(float(ncc), 4),
                 dpi=round(dpi), ms=int((time.time() - t0) * 1000), _pa=pa, _pb=pb2)
        return c


def _align(pa, pb):
    if pb.shape[0] < pa.shape[0] or pb.shape[1] < pa.shape[1]:
        pb = cv2.copyMakeBorder(pb, 0, max(0, pa.shape[0] - pb.shape[0]), 0,
                                max(0, pa.shape[1] - pb.shape[1]), cv2.BORDER_REPLICATE)
    fa, fb = pa.astype(np.float32), pb.astype(np.float32)
    if fa.std() < 1e-3:
        return pb[:pa.shape[0], :pa.shape[1]], float("nan")
    r = cv2.matchTemplate(fb, fa, cv2.TM_CCOEFF_NORMED)
    _, mx, _, ml = cv2.minMaxLoc(r)
    pb2 = pb[ml[1]:ml[1] + pa.shape[0], ml[0]:ml[0] + pa.shape[1]]
    if mx > NCC_MIN:
        warp = np.array([[1, 0, ml[0]], [0, 1, ml[1]]], np.float32)
        try:
            _, warp = cv2.findTransformECC(
                fa, fb, warp, cv2.MOTION_AFFINE,
                (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-5), None, 3)
            if config.PIXEL_WARP_GUARD and not _warp_ok(warp, ml, pb.shape, pa.shape):
                return pb2, float(mx)       # บิดภาพเกินจริง (กลบความต่างได้) ⇒ เลื่อนอย่างเดียว
            w2 = cv2.warpAffine(pb, warp, (pa.shape[1], pa.shape[0]),
                                flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                borderMode=cv2.BORDER_REPLICATE)
            n2 = float(np.corrcoef(fa.ravel(), w2.astype(np.float32).ravel())[0, 1])
            if n2 >= mx:
                return w2, n2
        except cv2.error:
            pass
    return pb2, float(mx)


def _warp_ok(warp, ml, shb, sha) -> bool:
    """การจัดละเอียดต่อจุดต้องใกล้ "เลื่อนอย่างเดียว" — ภาพสองฝั่งถูกเรนเดอร์ที่สเกลเดียวกันแล้ว
    (การจัดทั้งโซนจัดการสเกลไปแล้ว) ⇒ สเกล/เฉือนที่เหลือมีแต่เศษเล็ก ๆ · วัดบนสถานี 13 รอบ: p99 0.04 ·
    เลื่อนต้องอยู่ในหน้าต่างค้นหา (ไม่ออกนอกภาพ B ที่เผื่อขอบไว้)"""
    m = float(config.PIXEL_WARP_MAX)
    if max(abs(warp[0, 0] - 1), abs(warp[1, 1] - 1), abs(warp[0, 1]), abs(warp[1, 0])) > m:
        return False
    tx, ty = float(warp[0, 2]), float(warp[1, 2])
    return (abs(tx - ml[0]) <= max(2.0, 0.5 * (shb[1] - sha[1]) + 1) and
            abs(ty - ml[1]) <= max(2.0, 0.5 * (shb[0] - sha[0]) + 1))


def _contrast(pa, pb2) -> float:
    """ความต่างของพื้นกับหมึกที่เข้มที่สุดในครอป (มัธยฐาน − p0.5 ของฝั่งที่ต่ำกว่า) — ครอปรอบจุดเล็ก ๆ
    บนพื้นขาวก็ยังได้ค่าสูง (มัธยฐาน = พื้น) ต่างจาก p95−p5 ที่ตกเมื่อหมึกน้อย"""
    return float(min(np.median(g) - np.percentile(g, 0.5) for g in (pa, pb2)))


def _inkmask(g, dpi, ink_t=None):
    t = INK_T if ink_t is None else ink_t
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (_odd(BG_MM / 25.4 * dpi),) * 2)
    gs = cv2.GaussianBlur(g, (0, 0), 0.8 * dpi / 1600)
    dark = cv2.morphologyEx(gs, cv2.MORPH_BLACKHAT, k)
    light = cv2.morphologyEx(gs, cv2.MORPH_TOPHAT, k)
    return (dark > t).astype(np.uint8), (light > t).astype(np.uint8)


def _tol_absdiff(pa, pb2, tol):
    a = pa.astype(np.int16)
    b = cv2.copyMakeBorder(pb2, tol, tol, tol, tol, cv2.BORDER_REPLICATE).astype(np.int16)
    H, W = a.shape
    best = None
    for dy in range(2 * tol + 1):
        for dx in range(2 * tol + 1):
            d = np.abs(a - b[dy:dy + H, dx:dx + W])
            best = d if best is None else np.minimum(best, d)
    return best


def _diff_blobs(ma, mb, band, D):
    k = np.ones((2 * TOL + 1,) * 2, np.uint8)
    d = ((ma & (1 - cv2.dilate(mb, k))) | (mb & (1 - cv2.dilate(ma, k)))).astype(np.uint8)
    if band > 0:     # พื้นหลังเฉพาะที่ไม่น่าเชื่อใกล้ขอบครอป
        d[:band, :] = 0
        d[-band:, :] = 0
        d[:, :band] = 0
        d[:, -band:] = 0
    n, lab, st, _ = cv2.connectedComponentsWithStats(d, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        sel = lab[y:y + h, x:x + w] == i
        g = float((D[y:y + h, x:x + w][sel] > GRAY_DIFF).mean())
        out.append({"area": int(a), "bbox": (int(x), int(y), int(w), int(h)), "g": g})
    return out


def _compare(pa, pb2, dpi, gray_tol=None, ink_t=None):
    """``gray_tol`` = ระยะเลื่อนที่ยอมตอนวัด "สีต่างกันจริง" ของก้อนที่ต่าง (ไม่ส่ง = ``TOL`` = เดิมเป๊ะ) —
    ตำแหน่งของก้อนยังยอมเลื่อน ±TOL ผ่านหน้ากากหมึกเสมอ · ``ink_t`` = เกณฑ์หมึก (ไม่ส่ง = ``INK_T``)"""
    ma, la = _inkmask(pa, dpi, ink_t)
    mb, lb = _inkmask(pb2, dpi, ink_t)
    band = _odd(BG_MM / 25.4 * dpi) // 2 + 2 * TOL + 1
    D = _tol_absdiff(pa, pb2, TOL if gray_tol is None else gray_tol)
    blobs = _diff_blobs(ma, mb, band, D) + _diff_blobs(la, lb, band, D)
    k = 1600.0 / dpi
    sig = [b for b in blobs if b["area"] * k * k >= MIN_AREA and b["g"] >= GRAY_FRAC]
    tone = 0.0
    both = (ma & mb).astype(bool)
    if both.sum() > 50:
        tone = float(abs(np.median(pa[both].astype(np.float32))
                         - np.median(pb2[both].astype(np.float32))))
    return {"sig": sig, "inkA": int(ma.sum()), "inkB": int(mb.sum()), "tone": round(tone, 1)}


# ── ระดับจุดต่าง ─────────────────────────────────────────────────────────

def _box(side_rec) -> Optional[tuple]:
    b = (side_rec or {}).get("box")
    if not b or len(b) != 4:
        return None
    return tuple(float(v) for v in b)


def _targets(f: dict) -> List[dict]:
    ms = f.get("members") or []
    if not ms:
        return [f]
    out = list(ms)
    big = any(_box(f[s]) and max(_box(f[s])[2] - _box(f[s])[0], _box(f[s])[3] - _box(f[s])[1]) > BIG
              for s in ("a", "b"))
    if not big:
        out.append(f)
    return out


def _line_tiles(t: dict, side: str, lines: Dict[str, list]) -> Optional[list]:
    """กรอบทั้งบรรทัด OCR ของจุดต่าง แบ่งเป็นท่อน · ``None`` = เลขบรรทัดเชื่อไม่ได้"""
    rec = t.get(side) or {}
    ln, box = rec.get("line"), _box(rec)
    if ln is None or box is None:
        return []
    L = lines.get(side) or []
    if not (0 <= ln < len(L)) or not L[ln].get("box"):
        return None
    b = L[ln]["box"]
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    if not (b[0] - 4 <= cx <= b[2] + 4 and b[1] - 4 <= cy <= b[3] + 4):
        return None
    x0, y0, x1, y1 = b
    n = max(1, int(math.ceil((x1 - x0) / float(CHUNK))))
    return [(x0 + (x1 - x0) * i / n, y0, x0 + (x1 - x0) * (i + 1) / n, y1) for i in range(n)]


def verify(pc: PairCheck, f: dict, lines: Dict[str, list]) -> tuple:
    """คืน ``(สถานะ, การตรวจ[])`` · สถานะ SAME / DIFF / UNVERIFIABLE"""
    checks = []
    for t in _targets(f):
        for s, S in (("a", "A"), ("b", "B")):
            b = _box(t.get(s))
            if b:
                checks.append(pc.check(S, b))
    if not checks:
        return "UNVERIFIABLE", checks
    sts = [c["status"] for c in checks]
    if "DIFF" in sts:
        return "DIFF", checks
    if not all(x == "SAME" for x in sts):
        return "UNVERIFIABLE", checks
    if not config.PIXEL_LINE_MODE:
        return "SAME", checks
    for t in (f.get("members") or [f]):
        for s, S in (("a", "A"), ("b", "B")):
            tiles = _line_tiles(t, s, lines)
            if tiles is None:
                return "UNVERIFIABLE", checks
            for tb in tiles:
                c = pc.check(S, tb)
                c["line"] = True
                checks.append(c)
                if c["status"] != "SAME":
                    return ("DIFF" if c["status"] == "DIFF" else "UNVERIFIABLE"), checks
    return "SAME", checks


def _evidence(c: dict, path: str) -> bool:
    """ภาพหลักฐาน A | B (ทาบแล้ว) | จุดที่ต่าง (แดง) — ความสูงไม่เกิน 220 px"""
    try:
        pa, pb = c["_pa"], c["_pb"]
        diff = cv2.cvtColor(np.minimum(pa, pb), cv2.COLOR_GRAY2BGR)
        diff = (diff * 0.35 + 165).astype(np.uint8)
        for b in c.get("sig") or []:
            x, y, w, h = b["bbox"]
            cv2.rectangle(diff, (x - 2, y - 2), (x + w + 2, y + h + 2), (0, 0, 230), 2)
        sep = np.full((pa.shape[0], 6, 3), 200, np.uint8)
        img = np.hstack([cv2.cvtColor(pa, cv2.COLOR_GRAY2BGR), sep,
                         cv2.cvtColor(pb, cv2.COLOR_GRAY2BGR), sep, diff])
        s = min(1.0, 220.0 / img.shape[0], 1400.0 / img.shape[1])
        if s < 1.0:
            img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        return bool(cv2.imwrite(path, img, [cv2.IMWRITE_JPEG_QUALITY, 85]))
    except Exception:        # noqa: BLE001
        return False


_TH = {"SAME": "ภาพเหมือนกันทุกพิกเซล — OCR อ่านต่างเอง",
       "DIFF": "ภาพยืนยันว่าต่าง",
       "UNVERIFIABLE": "ตรวจด้วยภาพไม่ได้"}
# ``PIXEL_RASTER_NOTE`` (8 ต.ค. รอบ 4): คู่ที่ฝั่งใดเป็นภาพสแกน — ภาพสแกนกับเวกเตอร์ต่างกันเสมอ (ขอบตัวอักษร ·
# จุดรบกวน · บรรทัดที่ตัดคนละที่) ⇒ DIFF ไม่ใช่หลักฐานว่าข้อความต่าง (Friskies: ผิด 20/22) · SAME ยังเชื่อได้
_TH_RASTER_DIFF = "ตรวจด้วยภาพแล้วยังไม่ยืนยันว่าเหมือน — ฝั่งหนึ่งเป็นภาพสแกน ภาพจึงต่างกันเสมอ (ไม่ใช่หลักฐานว่าข้อความต่าง)"
# ``PIXEL_RASTER_KEEP_NUMBER`` (9 ต.ค.): บนคู่ภาพสแกน จุดทศนิยม/จุลภาคขนาด ~0.17 mm เล็กกว่าที่ 300 dpi ยืนยันได้
# บนพื้นสีกลาง ⇒ จุดต่างที่เป็นตัวเลขไม่พับเป็น "ภาพเหมือน" (1.5g ↔ 15g คือข้อมูลสำคัญที่สุดของฉลาก)
_RASTER_NUMBER_CLASSES = ("NUMBER", "PLACEHOLDER", "FRACTION")
_TH_RASTER_NUMBER = ("ภาพดูเหมือนกัน แต่ฝั่งหนึ่งเป็นภาพสแกน — จุดทศนิยม/เครื่องหมายเล็กของตัวเลขเล็กกว่าความละเอียด"
                     "ที่ภาพสแกนยืนยันได้ จึงไม่พับ (โปรดดูด้วยตา)")


def _is_number(f: dict) -> bool:
    return any((t.get("class") in _RASTER_NUMBER_CLASSES) for t in [f] + list(f.get("members") or []))


def run(pairs: List[dict], srcs: dict, rd: str, warnings: List[str], say=None) -> dict:
    """ตรวจทุกจุดของทุกคู่ · แก้ ``pairs`` ในที่ · คืนสรุปสำหรับ Log"""
    log = {"enabled": config.PIXEL_VERIFY, "line_mode": config.PIXEL_LINE_MODE,
           "raster": config.PIXEL_RASTER,
           "pymupdf": getattr(fitz, "VersionBind", None) if fitz is not None else None,
           "pairs": [], "same": 0, "diff": 0, "diff_raster": 0, "unverifiable": 0, "skipped": 0, "ms": 0,
           "raster_number_kept": 0}
    if not config.PIXEL_VERIFY:
        return log
    t_all = time.time()
    if cv2 is None or fitz is None:
        log["reason"] = "ไม่มี OpenCV/PyMuPDF"
        return log
    a, b = srcs.get("a"), srcs.get("b")
    if not (a is not None and b is not None and a.is_pdf and b.is_pdf):
        log["reason"] = "ไม่ใช่ PDF ทั้งสองฝั่ง — ไม่ตรวจด้วยภาพ (ผลเดิมทุกจุด)"
        return log
    deadline = t_all + config.PIXEL_TIME_BUDGET_S
    for pr in pairs:
        fs = pr.get("findings") or []
        plog = {"n": pr["n"], "items": []}
        log["pairs"].append(plog)
        if not fs or pr.get("unreadable"):
            continue
        sd = pr["sides"]
        try:
            pc = PairCheck(a.path, b.path,
                           {"page": sd["a"]["page"], "bbox": sd["a"]["bbox"],
                            "W": sd["a"]["sent_px"][0], "H": sd["a"]["sent_px"][1],
                            "rot": sd["a"].get("rotate", 0)},
                           {"page": sd["b"]["page"], "bbox": sd["b"]["bbox"],
                            "W": sd["b"]["sent_px"][0], "H": sd["b"]["sent_px"][1],
                            "rot": sd["b"].get("rotate", 0)})
        except Exception as e:   # noqa: BLE001
            plog["error"] = str(e)[:200]
            warnings.append("คู่ %d: ตรวจด้วยภาพไม่ได้ (%s)" % (pr["n"], str(e)[:80]))
            continue
        plog["align"] = pc.ginfo
        if pc.raster_why:
            plog["raster_check"] = {k.lower(): v for k, v in pc.raster_why.items()}
        if pc.ok and pc.ginfo.get("raster"):
            plog["raster"] = pc.ginfo["raster"]
            warnings.append("คู่ %d: %s — หลักฐานภาพเทียบที่ความละเอียดของภาพสแกน (เบลอ σ %g px "
                            "กันจุดรบกวนของการสแกน/JPEG)" % (pr["n"], " · ".join(
                                "ไฟล์ %s เป็นภาพสแกน %g dpi" % (k.upper(), v["dpi"])
                                for k, v in sorted(pc.ginfo["raster"].items()) if v), RASTER_SIGMA))
        if not pc.ok:
            plog["error"] = pc.ginfo.get("error")
            pc.close()
            continue
        if say:
            say("กำลังตรวจจุดต่างด้วยภาพ คู่ %d (%d จุด)" % (pr["n"], len(fs)))
        raw_ok = (pr.get("ai") or {}).get("mode") == "raw"
        cmp_lines = {"a": pr.get("lines", {}).get("a") or [], "b": pr.get("lines", {}).get("b") or []}
        raw_lines = {"a": (pr.get("_raw") or {}).get("a") or [], "b": (pr.get("_raw") or {}).get("b") or []}
        keep, same = [], []
        raster_pair = bool(config.PIXEL_RASTER_NOTE and pc.ginfo.get("raster"))
        for f in fs:
            if time.time() > deadline:
                log["skipped"] += 1
                f.setdefault("notes", []).append("ไม่ได้ตรวจด้วยภาพ (หมดเวลา)")
                keep.append(f)
                continue
            lines = raw_lines if (raw_ok and f.get("raw")) else cmp_lines
            t0 = time.time()
            try:
                st, checks = verify(pc, f, lines)
            except Exception as e:   # noqa: BLE001
                st, checks = "UNVERIFIABLE", []
                plog.setdefault("errors", []).append("F%s: %s" % (f.get("id"), str(e)[:120]))
            number_kept = bool(st == "SAME" and config.PIXEL_RASTER_KEEP_NUMBER
                               and pc.ginfo.get("raster") and _is_number(f))
            if number_kept:
                st = "UNVERIFIABLE"
                log["raster_number_kept"] += 1
            ev = None
            show = next((c for c in checks if c["status"] == st), checks[0] if checks else None)
            if show is not None and st in ("SAME", "DIFF") and f.get("id") is not None:
                name = "pv%d.jpg" % int(f["id"])
                if _evidence(show, os.path.join(rd, "img", name)):
                    ev = name
            f["pixel"] = {"status": st, "evidence": ev,
                          "checks": [{k: c.get(k) for k in ("side", "status", "ncc", "dpi", "tone", "ms", "line")}
                                     | {"blobs": len(c.get("sig") or [])} for c in checks]}
            note = _TH_RASTER_NUMBER if number_kept else _TH[st]
            if number_kept:
                f["pixel"]["raster_number"] = True
            if st == "DIFF" and raster_pair:
                note = _TH_RASTER_DIFF
                f["pixel"]["raster"] = True
                log["diff_raster"] += 1
            f.setdefault("notes", []).append(note)
            plog["items"].append({"id": f.get("id"), "status": st, "n_checks": len(checks),
                                  "ms": int((time.time() - t0) * 1000)})
            log[st.lower()] += 1
            if st == "SAME":
                f["pixel"]["was"] = f["severity"]
                f["severity"] = "pixel_same"
                same.append(f)
            else:
                keep.append(f)
        pc.close()
        pr["findings"] = keep
        pr["pixel_same"] = same
    log["ms"] = int((time.time() - t_all) * 1000)
    return log
