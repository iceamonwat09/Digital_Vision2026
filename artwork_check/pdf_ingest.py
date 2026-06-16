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
