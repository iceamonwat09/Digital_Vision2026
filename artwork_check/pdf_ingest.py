"""
PDF / image ingestion for Artwork Proof Check.

Renders the artwork page to numpy BGR at a requested DPI and extracts
the embedded text layer (when the PDF still has live text — most print
masters are outlined, in which case the text layer is empty and the
pipeline falls back to OCR).

Zone bboxes everywhere in this mode are NORMALIZED [x, y, w, h] in the
0..1 range relative to the page, so the same zones work at preview DPI
and at OCR DPI without conversion bookkeeping.
"""

from __future__ import annotations

import io
import os
from typing import List, Optional, Tuple

import numpy as np

try:
    import fitz  # PyMuPDF — already a project dependency (label_paper)
except ImportError:          # pragma: no cover
    fitz = None

import cv2


class ArtworkDocument:
    """One artwork file (PDF page or plain image) ready for inspection."""

    def __init__(self, path: str, page_index: int = 0):
        self.path = path
        self.page_index = page_index
        self.is_pdf = path.lower().endswith(".pdf")
        self.page_count = 1
        if self.is_pdf:
            if fitz is None:
                raise RuntimeError("PyMuPDF (fitz) is not installed")
            with fitz.open(path) as doc:
                self.page_count = doc.page_count
                if not (0 <= page_index < doc.page_count):
                    raise ValueError(f"page {page_index} out of range "
                                     f"(document has {doc.page_count})")

    # ── Rendering ────────────────────────────────────────────────────
    def render(self, dpi: int) -> np.ndarray:
        """Render the page to a BGR numpy array at ``dpi``."""
        if self.is_pdf:
            with fitz.open(self.path) as doc:
                page = doc[self.page_index]
                zoom = dpi / 72.0
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom),
                                      alpha=False)
                img = np.frombuffer(pix.samples, dtype=np.uint8)
                img = img.reshape(pix.height, pix.width, pix.n)
                if pix.n == 3:
                    return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
                return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        img = cv2.imread(self.path, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"cannot read image: {self.path}")
        return img

    def render_zone(self, bbox_norm: List[float], dpi: int,
                    max_side: Optional[int] = None) -> np.ndarray:
        """Render just one normalized-bbox zone at high DPI (PDF) or crop
        the image. Downscales to ``max_side`` if the crop is huge."""
        x, y, w, h = bbox_norm
        if self.is_pdf:
            with fitz.open(self.path) as doc:
                page = doc[self.page_index]
                rect = page.rect
                clip = fitz.Rect(rect.x0 + x * rect.width,
                                 rect.y0 + y * rect.height,
                                 rect.x0 + (x + w) * rect.width,
                                 rect.y0 + (y + h) * rect.height)
                zoom = dpi / 72.0
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom),
                                      clip=clip, alpha=False)
                img = np.frombuffer(pix.samples, dtype=np.uint8)
                img = img.reshape(pix.height, pix.width, pix.n)
                crop = (cv2.cvtColor(img, cv2.COLOR_RGB2BGR) if pix.n == 3
                        else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
        else:
            full = self.render(dpi)
            H, W = full.shape[:2]
            x0, y0 = int(x * W), int(y * H)
            x1, y1 = int((x + w) * W), int((y + h) * H)
            crop = full[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]

        if max_side and crop.size:
            longest = max(crop.shape[:2])
            if longest > max_side:
                scale = max_side / float(longest)
                crop = cv2.resize(crop, None, fx=scale, fy=scale,
                                  interpolation=cv2.INTER_AREA)
        return crop

    # ── Embedded text layer ──────────────────────────────────────────
    def embedded_text(self, bbox_norm: Optional[List[float]] = None) -> str:
        """Text from the PDF text layer inside ``bbox_norm`` (or the whole
        page). Returns '' for images and for outlined PDFs."""
        if not self.is_pdf:
            return ""
        with fitz.open(self.path) as doc:
            page = doc[self.page_index]
            if bbox_norm is None:
                return page.get_text("text").strip()
            x, y, w, h = bbox_norm
            rect = page.rect
            clip = fitz.Rect(rect.x0 + x * rect.width,
                             rect.y0 + y * rect.height,
                             rect.x0 + (x + w) * rect.width,
                             rect.y0 + (y + h) * rect.height)
            return page.get_text("text", clip=clip).strip()

    def zone_words(self, bbox_norm: List[float]) -> List[tuple]:
        """PDF text-layer words inside ``bbox_norm``, each as
        ``(text, (fx0, fy0, fx1, fy1))`` with the box as FRACTIONS relative
        to the zone. Returns [] for plain images and for outlined PDFs (no
        text layer) — used by the defect-card highlighter to draw an exact,
        any-script box without OCR. Never raises: on any error it yields the
        words it could read (possibly [])."""
        if not self.is_pdf:
            return []
        x, y, w, h = bbox_norm
        out: List[tuple] = []
        try:
            with fitz.open(self.path) as doc:
                page = doc[self.page_index]
                rect = page.rect
                zx0 = rect.x0 + x * rect.width
                zy0 = rect.y0 + y * rect.height
                zw = w * rect.width
                zh = h * rect.height
                if zw <= 0 or zh <= 0:
                    return []
                clip = fitz.Rect(zx0, zy0, zx0 + zw, zy0 + zh)
                for wx0, wy0, wx1, wy1, txt, *_ in \
                        page.get_text("words", clip=clip):
                    t = (txt or "").strip()
                    if not t:
                        continue
                    out.append((t, ((wx0 - zx0) / zw, (wy0 - zy0) / zh,
                                    (wx1 - zx0) / zw, (wy1 - zy0) / zh)))
        except Exception:
            return out
        return out


# ── Text orientation (auto-rotate vertical zones before OCR) ──────────
# Zones on side panels are often printed rotated 90°; OCR of tilted text
# hallucinates (a real case read خالٍ من الزيوت المهدرجة as a different,
# common phrase). ROTATE values are DEGREES CLOCKWISE applied to the crop
# before OCR: 0 none, 90 CW, 180, 270 (= 90 CCW). Vertical side text on
# these labels reads bottom-to-top → 270 (CCW) makes it upright (verified
# on the real file for both the Arabic and English vertical zones).
ROTATE_VALUES = (0, 90, 180, 270)


def apply_rotation(img: np.ndarray, angle: int) -> np.ndarray:
    """Rotate ``img`` by ``angle`` degrees clockwise (0/90/180/270)."""
    if angle == 90:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if angle == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    if angle == 270:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return img


def detect_orientation(crop: np.ndarray) -> str:
    """
    Deterministic "horizontal" | "vertical" | "empty" from image
    structure only (no OCR). Words glued by a small morphological close
    are wider-than-tall when horizontal, taller-than-wide when the panel
    is rotated 90°; a projection-profile ratio breaks ties. Measured 6/6
    on the real label's zone mix (incl. single-line). Conservative:
    returns "vertical" only on a clear signal so upright zones are never
    rotated by accident.
    """
    if crop is None or crop.size == 0:
        return "empty"
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, ink = cv2.threshold(gray, 0, 255,
                           cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if (ink > 0).mean() < 0.002:
        return "empty"
    H, W = ink.shape
    k = cv2.getStructuringElement(cv2.MORPH_RECT,
                                  (max(3, W // 60), max(3, H // 60)))
    closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, k)
    cnts, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL,
                               cv2.CHAIN_APPROX_SIMPLE)
    min_area = (H * W) * 0.001
    ars = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if w * h < min_area:
            continue
        ars.append(w / max(1, h))
    med_ar = float(np.median(ars)) if ars else 1.0

    inkf = (ink > 0).astype(np.float32)

    def _cv(p):
        m = p.mean()
        return (p.std() / m) if m > 1e-6 else 0.0

    proj_ratio = (_cv(inkf.mean(axis=1)) /
                  _cv(inkf.mean(axis=0))) if _cv(inkf.mean(axis=0)) > 1e-6 \
        else 99.0
    if med_ar >= 1.4:
        return "horizontal"
    if med_ar <= 0.7:
        return "vertical"
    return "horizontal" if proj_ratio >= 1.0 else "vertical"


def resolve_rotation(rotate, page_auto: bool, crop: np.ndarray) -> int:
    """
    Turn a zone's ``rotate`` setting into a concrete clockwise angle.

      int 0/90/180/270  → that angle (manual override, no detection)
      "auto"            → detect; vertical → 270 (CCW upright), else 0
      "default"         → follow the page: page_auto → detect, else 0

    ``crop`` is used only when detection is needed. Unknown values fall
    back to 0 (= no rotation = current behavior).
    """
    if isinstance(rotate, bool):        # guard: bool is a subclass of int
        rotate = "default"
    if isinstance(rotate, int) and rotate in ROTATE_VALUES:
        return rotate
    want_detect = (rotate == "auto") or (rotate == "default" and page_auto)
    if not want_detect:
        return 0
    return 270 if detect_orientation(crop) == "vertical" else 0


def encode_jpg(img: np.ndarray, quality: int = 92) -> bytes:
    ok, buf = cv2.imencode(".jpg", img,
                           [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return buf.tobytes()


def encode_png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("PNG encode failed")
    return buf.tobytes()
