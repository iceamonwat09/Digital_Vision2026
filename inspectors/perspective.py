"""
Perspective crop + dual-resolution image preparation for Label Paper inspection.

The browser sends the working photo (EXIF orientation already baked in,
long edge ≤ 4096) plus 4 corner points of the label quadrilateral, ordered
TL, TR, BR, BL **of the upright label**.  The corner order itself encodes
rotation — a photo taken sideways still warps to an upright rectangle, so
no separate rotation parameter is needed.

One ``cv2.warpPerspective`` produces two JPEG variants:

    ocr    — long edge ≤ ``OCR_MAX_EDGE``  (4096)
             Maximum text legibility for the OCR / visual-diff engines.
             At 4096 px across a 307 mm label this is ~13 px/mm, so the
             1.2 mm minimum font height renders at ~16 px.

    pixel  — long edge ≤ ``PIXEL_MAX_EDGE`` (= master render cap, 2400)
             Input for the alignment + ΔE2000 pixel pipeline.  Registration
             resizes the capture to exactly the master's render size, so any
             smaller cap forces a downsample-then-upsample cycle that blurs
             text edges and inflates false ΔE — and any larger cap buys
             nothing because ΔE is computed at master size anyway.
"""

from __future__ import annotations

import json
from typing import List, Optional, Tuple

import cv2
import numpy as np

from .master_renderer import MASTER_MAX_EDGE

OCR_MAX_EDGE = 4096
PIXEL_MAX_EDGE = MASTER_MAX_EDGE

_JPEG_QUALITY = 92
_MIN_EDGE_PX = 40        # reject quads smaller than this on either axis

Corners = List[Tuple[float, float]]


def parse_corners(raw) -> Optional[Corners]:
    """
    Parse the ``corners`` form field (JSON ``[[x,y],[x,y],[x,y],[x,y]]``).

    Returns ``None`` when the field is absent/empty (legacy client without
    perspective crop).  Raises ``ValueError`` when present but malformed,
    so the API can answer 400 instead of warping garbage.
    """
    if raw is None or str(raw).strip() == "":
        return None
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise ValueError(f"corners is not valid JSON: {e}")
    if not isinstance(data, list) or len(data) != 4:
        raise ValueError("corners must be a list of exactly 4 [x, y] points")
    pts: Corners = []
    for p in data:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise ValueError("each corner must be an [x, y] pair")
        try:
            pts.append((float(p[0]), float(p[1])))
        except (TypeError, ValueError):
            raise ValueError("corner coordinates must be numbers")
    return pts


def _edge_len(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return float(np.hypot(b[0] - a[0], b[1] - a[1]))


def quad_output_size(corners: Corners,
                     max_long_edge: int = OCR_MAX_EDGE) -> Tuple[int, int]:
    """
    Output rectangle size for a quad: each output edge takes the longer of
    the two opposite quad edges (so no text is squeezed), then the whole
    rectangle is scaled down to fit ``max_long_edge``.
    """
    tl, tr, br, bl = corners
    width = max(_edge_len(tl, tr), _edge_len(bl, br))
    height = max(_edge_len(tl, bl), _edge_len(tr, br))
    if width < _MIN_EDGE_PX or height < _MIN_EDGE_PX:
        raise ValueError(
            "crop quad is too small — drag all 4 corners onto the label first"
        )
    long_edge = max(width, height)
    if long_edge > max_long_edge:
        scale = max_long_edge / long_edge
        width *= scale
        height *= scale
    return max(int(round(width)), 1), max(int(round(height)), 1)


def warp_quad(bgr: np.ndarray, corners: Corners,
              max_long_edge: int = OCR_MAX_EDGE) -> np.ndarray:
    """
    Warp the quad marked by ``corners`` (TL, TR, BR, BL of the upright
    label, in image pixel coordinates) to an upright rectangle.
    Coordinates are clamped to the image bounds before warping.
    """
    h, w = bgr.shape[:2]
    pts = [(min(max(x, 0.0), w - 1.0), min(max(y, 0.0), h - 1.0))
           for x, y in corners]
    out_w, out_h = quad_output_size(pts, max_long_edge)

    src = np.float32(pts)
    dst = np.float32([[0, 0], [out_w - 1, 0],
                      [out_w - 1, out_h - 1], [0, out_h - 1]])
    matrix = cv2.getPerspectiveTransform(src, dst)
    # INTER_CUBIC: noticeably crisper text than INTER_LINEAR at near-1:1 scale
    return cv2.warpPerspective(bgr, matrix, (out_w, out_h),
                               flags=cv2.INTER_CUBIC)


def downscale_long_edge(img: np.ndarray, max_edge: int) -> np.ndarray:
    """Downscale so the long edge ≤ ``max_edge``; never upscales."""
    h, w = img.shape[:2]
    long_edge = max(h, w)
    if long_edge <= max_edge:
        return img
    scale = max_edge / long_edge
    new_w = max(int(round(w * scale)), 1)
    new_h = max(int(round(h * scale)), 1)
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)


def _encode_jpeg(bgr: np.ndarray, quality: int = _JPEG_QUALITY) -> bytes:
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise ValueError("failed to encode JPEG")
    return bytes(buf)


def prepare_inspection_images(image_bytes: bytes,
                              corners: Optional[Corners] = None
                              ) -> Tuple[bytes, bytes]:
    """
    Decode the upload, optionally perspective-warp it, and return
    ``(ocr_jpeg_bytes, pixel_jpeg_bytes)`` — same crop, two resolutions
    (see module docstring).  Without ``corners`` (legacy client) the image
    is used as-is, only resized.

    Raises ``ValueError`` on an undecodable image or a degenerate quad.
    """
    arr = np.frombuffer(image_bytes, np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("image could not be decoded")

    if corners is not None:
        ocr_img = warp_quad(bgr, corners, OCR_MAX_EDGE)
    else:
        ocr_img = downscale_long_edge(bgr, OCR_MAX_EDGE)

    pixel_img = downscale_long_edge(ocr_img, PIXEL_MAX_EDGE)
    return _encode_jpeg(ocr_img), _encode_jpeg(pixel_img)
