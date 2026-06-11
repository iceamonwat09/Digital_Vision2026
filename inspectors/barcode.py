"""
Barcode decoding for label inspection.

Why decode instead of OCR the digits?
    A 1-D barcode (EAN-13 on tuna cans) is the single most critical field
    and the one OCR fails on most often — the human-readable digits under
    the bars are tiny and low-contrast. A real barcode decoder reads the
    *bars themselves*, giving the exact value AND proving the symbol is
    machine-scannable (the production QC checklist literally requires
    "Barcode 100% scan rate").

Backends, tried in order, all optional:
    1. pyzbar (zbar)           — most reliable for EAN/UPC; needs the zbar
                                 system lib + ``pyzbar`` wheel.
    2. cv2.barcode.BarcodeDetector — bundled with opencv-contrib builds.

When no backend is available (or none decodes), an empty list is returned
and the pipeline silently falls back to OCR — nothing breaks.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def _decode_pyzbar(bgr: np.ndarray) -> Optional[List[dict]]:
    try:
        from pyzbar.pyzbar import decode as zbar_decode
    except Exception:
        return None
    out: List[dict] = []
    for sym in zbar_decode(bgr):
        try:
            data = sym.data.decode("utf-8", "ignore").strip()
        except Exception:
            continue
        if data:
            out.append({"data": data, "type": str(sym.type), "engine": "pyzbar"})
    return out


def _clean_type(raw) -> str:
    """cv2's return tuple order varies by build; only keep a tidy type string
    (drop anything that is actually a points array)."""
    s = str(raw).strip()
    if not s or "\n" in s or "[" in s:
        return ""
    return s


def _decode_cv2(bgr: np.ndarray) -> Optional[List[dict]]:
    barcode_mod = getattr(cv2, "barcode", None)
    if barcode_mod is None or not hasattr(barcode_mod, "BarcodeDetector"):
        return None
    try:
        detector = barcode_mod.BarcodeDetector()
        ret = detector.detectAndDecodeMulti(bgr)
    except Exception as e:
        logger.debug("cv2.barcode decode failed: %s", e)
        return None

    # Builds differ: (ok, decoded, types, points) or (ok, decoded, points).
    if not ret or not ret[0]:
        return []
    decoded = ret[1] if len(ret) > 1 else []
    types = ret[2] if len(ret) > 2 else []
    if decoded is None:
        return []

    out: List[dict] = []
    types = list(types) if hasattr(types, "__iter__") else []
    for i, data in enumerate(decoded):
        data = (data or "").strip()
        if data:
            btype = _clean_type(types[i]) if i < len(types) else ""
            out.append({"data": data, "type": btype, "engine": "cv2.barcode"})
    return out


def decode_barcodes(image_bytes: bytes) -> List[dict]:
    """
    Decode all 1-D/2-D barcodes in an encoded image.

    Returns ``[{"data", "type", "engine"}, ...]`` (possibly empty). Never
    raises — a missing backend or undecodable image yields ``[]``.
    """
    if not image_bytes:
        return []
    bgr = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        return []

    for backend in (_decode_pyzbar, _decode_cv2):
        result = backend(bgr)
        if result:                      # non-empty decode wins
            logger.info("Barcode decoded via %s: %s",
                        result[0]["engine"], [r["data"] for r in result])
            return result
    return []


def digits_only(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def best_match(decoded: List[dict], expected_digits: str) -> Optional[str]:
    """
    Pick the decoded barcode whose digit string best matches ``expected_digits``.

    Exact digit match wins outright; otherwise the longest common-prefix
    candidate is returned so a near-miss (one misread module) still surfaces
    as the found value for the diff. Returns ``None`` when nothing decoded.
    """
    if not decoded:
        return None
    exp = digits_only(expected_digits)
    cands = [d["data"] for d in decoded if digits_only(d["data"])]
    if not cands:
        return None

    for c in cands:
        if digits_only(c) == exp and exp:
            return c

    def common_prefix(a: str, b: str) -> int:
        n = 0
        for x, y in zip(a, b):
            if x != y:
                break
            n += 1
        return n

    return max(cands, key=lambda c: common_prefix(digits_only(c), exp))
