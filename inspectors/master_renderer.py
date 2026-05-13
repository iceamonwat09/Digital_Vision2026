"""
Render an SKU master PDF to a high-DPI RGB numpy array.

The result is cached next to the PDF as ``master_<dpi>dpi.png`` so subsequent
inspections don't pay the render cost again.

Why a raster master at all?
    The Phase 2 pixel-level pipeline (ΔE2000 map → defect clustering) needs a
    pixel-for-pixel ground-truth to compare the cropped photo against.
    Rendering at 300 DPI from the vector PDF gives a stable, lighting-free
    reference — far better than a photo of an approved sample.
"""

from __future__ import annotations

import os
from typing import Optional

import cv2
import numpy as np


_DEFAULT_DPI = 300


def _cache_path(pdf_path: str, dpi: int) -> str:
    base, _ = os.path.splitext(pdf_path)
    return f"{base}_{dpi}dpi.png"


def render_pdf_to_rgb(pdf_path: str, dpi: int = _DEFAULT_DPI,
                      page_index: int = 0) -> np.ndarray:
    """Render a single PDF page to a HxWx3 uint8 RGB array."""
    import fitz  # PyMuPDF
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        scale = dpi / 72.0
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, alpha=False, colorspace=fitz.csRGB)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, 3
        )
        return arr.copy()
    finally:
        doc.close()


def get_master_image(pdf_path: str, dpi: int = _DEFAULT_DPI,
                     use_cache: bool = True,
                     max_long_edge: Optional[int] = 2400) -> np.ndarray:
    """
    Return the rendered master, using ``master_<dpi>dpi.png`` cache when it
    exists. Optionally downscale so the long edge ≤ ``max_long_edge`` to keep
    ΔE computation bounded (300 DPI of a tuna-can wrapper would be ~6000px wide).
    """
    cache_path = _cache_path(pdf_path, dpi)
    if use_cache and os.path.isfile(cache_path):
        bgr = cv2.imread(cache_path, cv2.IMREAD_COLOR)
        if bgr is not None:
            img = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        else:
            img = render_pdf_to_rgb(pdf_path, dpi=dpi)
    else:
        img = render_pdf_to_rgb(pdf_path, dpi=dpi)
        try:
            cv2.imwrite(cache_path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        except Exception:
            pass  # cache is an optimisation, not a requirement

    if max_long_edge is not None:
        h, w = img.shape[:2]
        long_edge = max(h, w)
        if long_edge > max_long_edge:
            scale = max_long_edge / long_edge
            new_w = int(round(w * scale))
            new_h = int(round(h * scale))
            img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)

    return img


def encode_rgb_to_jpeg_bytes(rgb: np.ndarray, quality: int = 90) -> bytes:
    """RGB ndarray → JPEG bytes (for upload to Gemini via N8N)."""
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise RuntimeError("cv2.imencode failed to produce JPEG")
    return bytes(buf)


def render_master_to_jpeg_bytes(pdf_path: str,
                                dpi: int = _DEFAULT_DPI,
                                max_long_edge: Optional[int] = 1600,
                                quality: int = 88) -> bytes:
    """
    Convenience: render the master PDF and return JPEG bytes ready to upload.

    A smaller default ``max_long_edge`` than the pixel-ΔE path keeps the
    payload to Gemini reasonable (~150-300 KB) without sacrificing
    legibility of label text.
    """
    rgb = get_master_image(pdf_path, dpi=dpi, max_long_edge=max_long_edge)
    return encode_rgb_to_jpeg_bytes(rgb, quality=quality)
