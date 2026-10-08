"""อ่านไฟล์ต้นฉบับ (PDF / ภาพ) · ภาพตัวอย่างสำหรับวาดโซน · เรนเดอร์ภาพโซนที่จะส่ง

หลักการ: **ส่งเฉพาะพิกเซลของโซน** — PDF ถูกเรนเดอร์เป็นภาพเสมอ จึงไม่มี
ข้อความที่มองไม่เห็นใน PDF ติดไปด้วย (ข้อความซ่อนไม่มีหมึกบนภาพ)

พิกัดโซนทุกที่เป็นสัดส่วน ``[x, y, w, h]`` (0..1) ของหน้า/ภาพ ที่ **หมุนตาม
EXIF แล้ว** (ภาพที่ผู้ใช้เห็นบนจอ = ภาพที่ใช้คำนวณ)
"""

from __future__ import annotations

import hashlib
import io
import math
import os
from typing import List, Optional, Tuple

import cv2
import numpy as np

try:
    import fitz  # PyMuPDF
except ImportError:          # pragma: no cover
    fitz = None

from PIL import Image, ImageOps

from . import config

PDF_EXT = (".pdf",)
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")

# กันไฟล์ภาพปลอมที่ประกาศขนาดใหญ่มาก (decompression bomb) — 200 MP พอสำหรับกล้อง
Image.MAX_IMAGE_PIXELS = 200_000_000


def sha1_bytes(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def clamp_bbox(bbox) -> Optional[List[float]]:
    """ตรวจ/ตัด bbox ให้อยู่ใน 0..1 — คืน ``None`` ถ้าใช้ไม่ได้"""
    try:
        x, y, w, h = [float(v) for v in bbox]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x, y, w, h)):
        return None
    x0, y0 = max(0.0, min(1.0, x)), max(0.0, min(1.0, y))
    x1, y1 = max(0.0, min(1.0, x + w)), max(0.0, min(1.0, y + h))
    if x1 - x0 < 0.002 or y1 - y0 < 0.002:
        return None
    return [round(x0, 6), round(y0, 6), round(x1 - x0, 6), round(y1 - y0, 6)]


class Source:
    """ไฟล์ต้นฉบับหนึ่งฝั่ง (A หรือ B)"""

    def __init__(self, path: str):
        self.path = path
        ext = os.path.splitext(path)[1].lower()
        self.is_pdf = ext in PDF_EXT
        self.page_count = 1
        self.pages_pt: List[Tuple[float, float]] = []
        self.exif_orientation: Optional[int] = None
        self.raw_size: Tuple[int, int] = (0, 0)      # ก่อนหมุน EXIF
        self._img: Optional[np.ndarray] = None       # BGR หลังหมุน EXIF
        if self.is_pdf:
            if fitz is None:
                raise RuntimeError("ไม่ได้ติดตั้ง PyMuPDF")
            with fitz.open(path) as doc:
                if doc.needs_pass:
                    raise ValueError("PDF ติดรหัสผ่าน — เปิดไม่ได้")
                self.page_count = doc.page_count
                if self.page_count < 1:
                    raise ValueError("PDF ไม่มีหน้า")
                self.pages_pt = [(p.rect.width, p.rect.height) for p in doc]
        elif ext in IMAGE_EXT:
            with Image.open(path) as im:
                self.raw_size = im.size
                try:
                    self.exif_orientation = im.getexif().get(0x0112)
                except Exception:                      # noqa: BLE001
                    self.exif_orientation = None
                im2 = ImageOps.exif_transpose(im)      # หมุนตามที่กล้องบอก
                im2 = im2.convert("RGB")
                self._img = np.asarray(im2)[:, :, ::-1].copy()
            h, w = self._img.shape[:2]
            self.pages_pt = [(float(w), float(h))]     # หน่วยเป็น px สำหรับภาพ
        else:
            raise ValueError("ชนิดไฟล์ไม่รองรับ: %s" % ext)

    # ── ข้อมูล ───────────────────────────────────────────────────────
    def info(self) -> dict:
        d = {"type": "pdf" if self.is_pdf else "image", "pages": self.page_count}
        if self.is_pdf:
            d["pages_pt"] = [[round(w, 2), round(h, 2)] for w, h in self.pages_pt]
            d["pages_mm"] = [[round(w / 72 * 25.4, 1), round(h / 72 * 25.4, 1)]
                             for w, h in self.pages_pt]
        else:
            h, w = self._img.shape[:2]
            d["image_px"] = [w, h]
            d["raw_px"] = list(self.raw_size)
            d["exif_orientation"] = self.exif_orientation
        return d

    def _check_page(self, page: int) -> int:
        page = int(page or 0)
        if not (0 <= page < self.page_count):
            raise ValueError("หน้า %d ไม่มีในไฟล์ (มี %d หน้า)" % (page + 1, self.page_count))
        return page

    # ── ภาพตัวอย่างสำหรับวาดโซน ───────────────────────────────────────
    def preview(self, page: int = 0, max_side: Optional[int] = None) -> np.ndarray:
        max_side = max_side or config.PREVIEW_MAX_SIDE
        page = self._check_page(page)
        if self.is_pdf:
            w, h = self.pages_pt[page]
            zoom = max_side / float(max(w, h))
            with fitz.open(self.path) as doc:
                pix = doc[page].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            return _pix_to_bgr(pix)
        img = self._img
        h, w = img.shape[:2]
        if max(w, h) <= max_side:
            return img.copy()
        s = max_side / float(max(w, h))
        return _resize(img, s)

    # ── ภาพโซนที่จะส่งให้ Vision ──────────────────────────────────────
    def render_zone(self, page: int, bbox: List[float],
                    scale: float = 1.0, max_side: Optional[int] = None,
                    base_dpi: Optional[float] = None,
                    dpi_cap: Optional[float] = None) -> Tuple[np.ndarray, dict]:
        """คืน ``(ภาพ BGR, ข้อมูล)`` · ``scale`` > 1 ใช้กับการอ่านซ้ำแบบซูม

        ``base_dpi`` แทน ``PDF_ZONE_DPI`` (โหมดคมสูงสุด) · ``dpi_cap`` = เพดาน dpi
        ไม่ส่งทั้งคู่ = เส้นทางเดิมเป๊ะ
        """
        page = self._check_page(page)
        x, y, w, h = bbox
        info: dict = {"warnings": []}
        if self.is_pdf:
            pw, ph = self.pages_pt[page]
            w_in, h_in = w * pw / 72.0, h * ph / 72.0
            dpi = float(base_dpi or config.PDF_ZONE_DPI) * scale
            if dpi_cap:
                dpi = min(dpi, float(dpi_cap))
            long_in = max(w_in, h_in)
            if long_in * dpi < config.ZONE_MIN_LONG_SIDE * scale:
                dpi = min(config.PDF_ZONE_DPI_MAX * scale,
                          config.ZONE_MIN_LONG_SIDE * scale / long_in)
            mp = w_in * h_in * dpi * dpi / 1e6
            if mp > config.MAX_IMAGE_MP:
                dpi = math.sqrt(config.MAX_IMAGE_MP * 1e6 / (w_in * h_in))
                info["warnings"].append("ลด dpi ให้ไม่เกิน %.0f MP" % config.MAX_IMAGE_MP)
            if max_side and long_in * dpi > max_side:
                dpi = max_side / long_in
            clip = fitz.Rect(x * pw, y * ph, (x + w) * pw, (y + h) * ph)
            with fitz.open(self.path) as doc:
                pix = doc[page].get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72),
                                           clip=clip, alpha=False)
            img = _pix_to_bgr(pix)
            info.update({"dpi": round(dpi, 1),
                         "zone_mm": [round(w_in * 25.4, 1), round(h_in * 25.4, 1)]})
        else:
            full = self._img
            H, W = full.shape[:2]
            x0, y0 = int(round(x * W)), int(round(y * H))
            x1, y1 = int(round((x + w) * W)), int(round((y + h) * H))
            img = full[max(0, y0):min(H, y1), max(0, x0):min(W, x1)].copy()
            s = 1.0
            mp = img.shape[0] * img.shape[1] / 1e6
            if mp > config.MAX_IMAGE_MP:
                s = math.sqrt(config.MAX_IMAGE_MP / mp)
                info["warnings"].append("ย่อภาพถ่ายให้ไม่เกิน %.0f MP" % config.MAX_IMAGE_MP)
            if max_side and max(img.shape[:2]) * s > max_side:
                s = max_side / float(max(img.shape[:2]))
            if s < 1.0:
                img = _resize(img, s)
            info["image_scale"] = round(s, 4)
            info["zone_src_px"] = [x1 - x0, y1 - y0]
            if max(img.shape[:2]) < 1024 and scale <= 1.0:
                info["warnings"].append(
                    "ภาพโซนเล็ก (ด้านยาว %d px) — Vision แนะนำอย่างน้อย 1024 px"
                    % max(img.shape[:2]))
        if img.size == 0:
            raise ValueError("โซนว่าง (อยู่นอกภาพ)")
        info["px"] = [int(img.shape[1]), int(img.shape[0])]
        info["mp"] = round(img.shape[0] * img.shape[1] / 1e6, 2)
        return img, info


def pair_image_budget(n: int = 2) -> int:
    """ไบต์ JPEG สูงสุดต่อภาพ ที่ทำให้ ``n`` ภาพยังอยู่ในคำขอ Vision เดียวกันได้

    สูตรเดียวกับ ``vision_client._item_cost`` (base64 = 4/3 เท่า + หัว JSON 400 ไบต์)
    และขีด ``MAX_REQUEST_BYTES - 200`` ของ ``vision_client.pack`` — คู่ A/B ที่อยู่
    คำขอเดียวกันถูกอ่านด้วยโมเดลรุ่นเดียวกันเสมอ
    """
    per_item = (config.MAX_REQUEST_BYTES - 200 - 4096) // max(1, int(n))
    return max(0, (per_item - 400) // 4 * 3 - 3)


def render_zone_sharp(src: "Source", page: int, bbox: List[float],
                      max_bytes: int) -> Tuple[np.ndarray, bytes, dict]:
    """โหมดคมสูงสุด — ไล่ dpi ขึ้นจนภาพ JPEG (คุณภาพสูงสุดในรายการ) เต็มงบ ``max_bytes``

    * เริ่มจากภาพมาตรฐาน (``render_zone`` เดิม) ⇒ **ไม่มีทางได้ dpi ต่ำกว่าเดิม**
    * เพดาน: ``PDF_ZONE_DPI_MAX`` · ``MAX_IMAGE_MP`` (ใน ``render_zone``)
    * ไม่ย่อภาพ ไม่ลดคุณภาพ JPEG เพื่อให้ dpi สูงขึ้น — ความละเอียดได้จากการเรนเดอร์
      vector ใหม่เท่านั้น
    * ภาพถ่าย / ภาพมาตรฐานยังเกินงบ ⇒ คืน ``None`` ให้ผู้เรียกใช้เส้นทางเดิม

    คืน ``(ภาพ, jpeg, ข้อมูล)`` หรือ ``None``
    """
    if not src.is_pdf:
        return None
    q = config.JPEG_QUALITIES[0]
    img0, info0 = src.render_zone(page, bbox)
    jpg0 = encode_jpeg(img0, q)
    base_dpi = float(info0["dpi"])
    if len(jpg0) > max_bytes:
        return None
    best = (base_dpi, img0, jpg0, info0)
    tries = [{"dpi": round(base_dpi, 1), "bytes": len(jpg0), "fit": True}]
    cap = float(config.PDF_ZONE_DPI_MAX)
    target = max_bytes * config.SHARP_FILL
    dpi, nbytes = base_dpi, len(jpg0)
    k = 2.0          # ไบต์ ∝ dpi^k — เริ่มที่ 2 (ตามพื้นที่) แล้ววัดจริงจากสองจุดล่าสุด
    for _ in range(max(0, config.SHARP_MAX_RENDERS - 1)):
        nxt = min(cap, dpi * (target / float(max(1, nbytes))) ** (1.0 / k))
        if nxt <= best[0] * 1.02:          # ขยับได้ไม่ถึง 2% — ไม่คุ้มเรนเดอร์ใหม่
            break
        img, info = src.render_zone(page, bbox, base_dpi=nxt, dpi_cap=cap)
        jpg = encode_jpeg(img, q)
        got = float(info["dpi"])
        fit = len(jpg) <= max_bytes
        tries.append({"dpi": round(got, 1), "bytes": len(jpg), "fit": fit})
        if got > dpi * 1.01 and len(jpg) > nbytes:
            k = min(2.0, max(1.0, math.log(len(jpg) / float(nbytes)) / math.log(got / dpi)))
        dpi, nbytes = got, len(jpg)
        if fit and got > best[0]:
            best = (got, img, jpg, info)
        if fit and (len(jpg) >= 0.85 * max_bytes or got >= cap - 0.5 or got < nxt - 0.5):
            break                           # เต็มงบพอ / ชนเพดาน dpi / ชนเพดาน MP
    _, img, jpg, info = best
    info = dict(info)
    info.update({"sharpness": "max", "base_dpi": round(base_dpi, 1),
                 "budget_bytes": int(max_bytes), "tries": tries,
                 "gain": round(float(info["dpi"]) / base_dpi, 3)})
    return img, jpg, info


ROTATIONS = (0, 90, 180, 270)


def norm_rot(v) -> int:
    """มุมหมุนของโซน (องศาตามเข็ม) · ค่าแปลก/ไม่มี = 0"""
    try:
        r = int(round(float(v))) % 360
    except (TypeError, ValueError):
        return 0
    return r if r in ROTATIONS else 0


def rotate_img(img: np.ndarray, rot: int) -> np.ndarray:
    """หมุนภาพตามเข็ม ``rot`` องศา (ทิศเดียวกับ CSS ``rotate()`` บนหน้าวาดโซน) · 0 = ภาพเดิม (ไม่คัดลอก)"""
    if rot == 90:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if rot == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    if rot == 270:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return img


def unrot_box(box, W: float, H: float, rot: int) -> tuple:
    """กรอบ ``(x0,y0,x1,y1)`` บนภาพที่หมุนแล้ว (กว้าง ``W`` สูง ``H``) → กรอบบนภาพก่อนหมุน"""
    x0, y0, x1, y1 = [float(v) for v in box]
    if rot == 90:
        return (y0, W - x1, y1, W - x0)
    if rot == 180:
        return (W - x1, H - y1, W - x0, H - y0)
    if rot == 270:
        return (H - y1, x0, H - y0, x1)
    return (x0, y0, x1, y1)


def unrot_size(W: float, H: float, rot: int) -> tuple:
    """ขนาดภาพก่อนหมุน จากขนาดภาพที่หมุนแล้ว"""
    return (H, W) if rot in (90, 270) else (W, H)


def _pix_to_bgr(pix) -> np.ndarray:
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 1:
        img = np.repeat(img, 3, axis=2)
    elif pix.n == 4:
        img = img[:, :, :3]
    return img[:, :, ::-1].copy()


def _resize(img: np.ndarray, s: float) -> np.ndarray:
    h, w = img.shape[:2]
    nw, nh = max(1, int(round(w * s))), max(1, int(round(h * s)))
    im = Image.fromarray(img[:, :, ::-1])
    im = im.resize((nw, nh), Image.LANCZOS)
    return np.asarray(im)[:, :, ::-1].copy()


def to_color_mode(img: np.ndarray, mode: str, dpi: Optional[float] = None) -> Tuple[np.ndarray, dict]:
    """แปลงภาพที่จะส่งให้ Vision ตาม ``mode`` (``config.COLOR_MODES``)

    * ``color`` ⇒ คืนภาพเดิม (อ็อบเจกต์เดิม ไม่คัดลอก) = ไบต์เดิมเป๊ะ
    * ``gray``  ⇒ ความสว่างล้วน (ITU-R 601 แบบเดียวกับ OpenCV)
    * ``bw``    ⇒ ตัดเกณฑ์เฉพาะที่ (Gaussian adaptive) — หน้าต่าง ``BW_BLOCK_MM`` คิดจาก dpi
      ที่เรนเดอร์จริง · ภาพถ่าย (ไม่รู้ dpi) ใช้ 1/30 ของด้านสั้น

    คืน 3 ช่องเท่ากันเสมอ (ผู้เรียกเดิมทุกตัวคาดภาพ BGR) · ส่ง ``mono=True`` ให้
    ``encode_jpeg``/``fit_jpeg`` เพื่อเข้ารหัสเป็นช่องเดียว
    """
    if mode not in ("gray", "bw"):
        return img, {"color_mode": "color"}
    g = cv2.cvtColor(np.ascontiguousarray(img[:, :, :3]), cv2.COLOR_BGR2GRAY)
    info = {"color_mode": mode}
    if mode == "bw":
        if dpi:
            blk = float(config.BW_BLOCK_MM) / 25.4 * float(dpi)
        else:
            blk = min(g.shape[:2]) / 30.0
        blk = int(max(15, round(blk)))
        blk += 1 - blk % 2
        g = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
                                  blk, float(config.BW_C))
        info.update({"bw_block_px": blk, "bw_c": float(config.BW_C),
                     "ink_frac": round(float((g == 0).mean()), 4)})
    return np.repeat(g[:, :, None], 3, axis=2), info


def encode_jpeg(img: np.ndarray, quality: int, mono: bool = False) -> bytes:
    """JPEG แบบไม่ลดความละเอียดสี (4:4:4) — ตัวหนังสือสีเล็ก ๆ ไม่เลือนขอบ

    ``mono`` (ภาพจาก ``to_color_mode`` แบบเทา/ขาวดำ) ⇒ JPEG ช่องเดียว · ไม่ส่ง = เส้นทางเดิมเป๊ะ
    """
    buf = io.BytesIO()
    if mono:
        Image.fromarray(np.ascontiguousarray(img[:, :, 0])).save(buf, format="JPEG",
                                                                 quality=int(quality))
        return buf.getvalue()
    Image.fromarray(img[:, :, ::-1]).save(buf, format="JPEG", quality=int(quality),
                                          subsampling=0)
    return buf.getvalue()


def encode_png(img: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(img[:, :, ::-1]).save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def fit_jpeg(img: np.ndarray, max_bytes: int, mono: bool = False) -> Tuple[bytes, np.ndarray, dict]:
    """เข้ารหัสให้ไม่เกิน ``max_bytes`` — ลดคุณภาพก่อน แล้วค่อยย่อ (และบอกเสมอ)

    คืน ``(jpeg, ภาพที่เข้ารหัสจริง, ข้อมูล)`` — ภาพที่คืนคือภาพที่ Vision เห็น
    จึงต้องใช้ภาพนี้วาดกรอบ ไม่ใช่ภาพก่อนย่อ
    """
    info = {"warnings": [], "downscale": 1.0}
    for q in config.JPEG_QUALITIES:
        data = encode_jpeg(img, q, mono)
        if len(data) <= max_bytes:
            info["quality"] = q
            return data, img, info
    # ทุกคุณภาพยังเกิน ⇒ ย่อภาพ (ไม่เงียบ — ใส่คำเตือนลง Log/หน้าเว็บ)
    cur, q = img, config.JPEG_QUALITIES[-1]
    for _ in range(8):
        s = max(0.3, math.sqrt(max_bytes / float(len(data))) * 0.95)
        cur = _resize(cur, s)
        info["downscale"] = round(info["downscale"] * s, 4)
        data = encode_jpeg(cur, q, mono)
        if len(data) <= max_bytes:
            break
    info["quality"] = q
    info["warnings"].append("ไฟล์ใหญ่เกินขีดคำขอ — ย่อภาพเหลือ %.0f%%" % (info["downscale"] * 100))
    return data, cur, info
