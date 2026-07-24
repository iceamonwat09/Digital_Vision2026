"""
Locate a defect's problem word inside a zone crop and draw a red box on
it — a DISPLAY-ONLY helper for the defect cards ("รายการที่พบ").

It never runs during ``run_inspection`` and never touches the PASS/FAIL
verdict, the counts, or ``report.json``: it is called only when the
browser asks for a defect crop (``/api/artwork/<id>/crop?...&hl=...``).
Any failure here degrades to "no box" — the caller returns the plain crop
exactly as before this module existed.

Two location strategies, tried in order, most reliable first:

  1. OCR blocks bbox — when the OCR backend returned per-element
     ``blocks`` with a ``bbox`` (see ``inspectors.ocr_n8n``), and one
     block's text matches the word, its box is used directly. Gemini
     bounding boxes come in several coordinate conventions (0..1,
     0..1000, or raw pixels); ``_norm_block_bbox`` normalizes them and a
     box that cannot be normalized cleanly is skipped (→ strategy 2).

  2. Deterministic projection-profile CV — no OCR bbox needed and no LLM
     trust: the crop is split into text ROWS by a horizontal ink profile
     (the same idea as ``pdf_ingest.detect_orientation``); the OCR text
     tells us which line index the word is on; within that row a vertical
     ink profile splits WORDS and the word's token index picks the
     column. Every step is gated on a confidence check (line/row counts
     must agree, etc.); when a step is not confident it falls back to a
     coarser box (whole row) or to nothing, so a wrong-word box is never
     drawn silently.

The pure-python parts (which OCR line / token a word sits on) have no
numpy/cv2 dependency so they unit-test on any machine; the image parts
import numpy lazily and are guarded by the caller.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

# A located region inside the crop, in PIXELS: (x0, y0, x1, y1).
Box = Tuple[int, int, int, int]

# Red, BGR (OpenCV order) — matches the "found" chip color in the UI.
_BOX_BGR = (40, 40, 220)

# Ink-profile tuning. Deliberately conservative: a row/word is only
# "active" with clearly more than background ink, so faint JPEG noise
# does not invent bands.
_ROW_INK_FRAC = 0.010     # a row counts as text when >1% of its width is ink
_COL_INK_FRAC = 0.020     # a column counts as ink when >2% of band height
_ROW_GAP_MERGE = 0.6      # merge row bands closer than 0.6× median row height
_WORD_GAP_FRAC = 0.5      # column gap ≥ 0.5× band height separates two words


# ── pure-python: which line / token does the word sit on ──────────────

def _norm(s: str) -> str:
    """Uppercase, keep only letters/digits — same spirit as
    ``checks._norm_key`` but dependency-free and used only for locating
    (never for a verdict)."""
    return re.sub(r"[\W_]+", "", (s or "").upper())


def locate_token(found: str, ocr_text: str) -> Optional[dict]:
    """
    Work out where ``found`` sits in the OCR text of its zone.

    Returns a dict::

        {"line": i, "n_lines": N,          # 0-based line index / line count
         "token": j, "n_tokens": M,        # token index in that line / count
         "multi": bool}                    # found spans >1 token → box the line

    or ``None`` when the word is not found in the OCR text (then the
    caller draws no box). ``multi`` is True for phrase/number defects
    whose ``found`` is several words — those get a whole-line box, which
    is still a useful narrowing and can never point at the wrong word.
    """
    if not found or not ocr_text:
        return None
    lines = [ln for ln in ocr_text.splitlines()]
    # Keep only lines with real content for indexing, but remember the
    # index within THAT filtered list — the CV row bands also drop blank
    # rows, so the two lists line up.
    content_lines = [ln for ln in lines if _norm(ln)]
    if not content_lines:
        return None

    fkey = _norm(found)
    if not fkey:
        return None
    found_tokens = [t for t in re.split(r"\s+", found.strip()) if _norm(t)]
    multi = len(found_tokens) > 1

    for i, line in enumerate(content_lines):
        tokens = [t for t in re.split(r"\s+", line.strip()) if _norm(t)]
        keys = [_norm(t) for t in tokens]
        if multi:
            # phrase/number: the whole found must appear as a substring of
            # the flattened line (forgives spacing/punctuation noise).
            if fkey in _norm(line):
                return {"line": i, "n_lines": len(content_lines),
                        "token": 0, "n_tokens": len(tokens), "multi": True}
            continue
        # single word: exact key match first, else the token that
        # contains it (e.g. "Cude" inside "Cude:"), else startswith.
        for j, k in enumerate(keys):
            if k == fkey:
                return {"line": i, "n_lines": len(content_lines),
                        "token": j, "n_tokens": len(tokens), "multi": False}
        for j, k in enumerate(keys):
            if fkey and (fkey in k or k in fkey):
                return {"line": i, "n_lines": len(content_lines),
                        "token": j, "n_tokens": len(tokens), "multi": False}
    return None


# ── OCR-blocks strategy (used when the backend returned bbox) ──────────

def _infer_scale(blocks: list, ocr_wh) -> Optional[Tuple[float, float]]:
    """Infer ONE (sx, sy) for a whole zone that turns its raw block bboxes
    into 0..1 fractions. The convention is decided per-zone (all blocks
    share it) from the LARGEST coordinate across every block, so a single
    small box near the origin can't be misread:

      * ``0..1``   — fractions (overall max ≤ ~1)                → (1, 1)
      * ``0..1000``— Gemini's normalized convention              → (1000, 1000)
      * pixels     — of the OCR crop ``ocr_wh``                  → (ow, oh)

    0..1000 and pixels are told apart by magnitude: a real OCR crop is
    hundreds–thousands of px on its long side (here ≥ ~1400), so pixel
    coords reach that while 0..1000 coords never pass ~1000. Returns None
    when the convention cannot be inferred (coords not 0..1 and no usable
    ``ocr_wh``) → caller falls back to the CV strategy."""
    m = 0.0
    for b in blocks:
        if not isinstance(b, dict):
            continue
        bb = b.get("bbox")
        if not bb or len(bb) != 4:
            continue
        try:
            x, y, w, h = (float(v) for v in bb)
        except (TypeError, ValueError):
            continue
        m = max(m, x, y, x + w, y + h)
    if m <= 0:
        return None
    if m <= 1.02:
        return (1.0, 1.0)
    if not ocr_wh or len(ocr_wh) != 2:
        return None
    try:
        ow, oh = float(ocr_wh[0]), float(ocr_wh[1])
    except (TypeError, ValueError):
        return None
    if ow <= 0 or oh <= 0:
        return None
    if max(ow, oh) >= 1400 and m <= 1050:
        return (1000.0, 1000.0)
    return (ow, oh)


def _norm_block_bbox(bbox, W: int, H: int,
                     scale: Optional[Tuple[float, float]]) -> Optional[Box]:
    """Turn one raw block ``bbox`` [x, y, w, h] into display-crop pixels
    (x0,y0,x1,y1) using the zone's ``scale`` (from ``_infer_scale``).
    Because the display crop and the OCR crop are the SAME zone content
    (same aspect + rotation, different size only), a box expressed as
    0..1 fractions maps straight onto the display crop. Returns None when
    the box is unusable or ``scale`` is None."""
    if not bbox or len(bbox) != 4 or not scale:
        return None
    try:
        x, y, w, h = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    sx, sy = scale
    fx0, fy0, fx1, fy1 = x / sx, y / sy, (x + w) / sx, (y + h) / sy
    # reject clearly out-of-frame fractions (bad convention guess)
    if max(fx0, fy0, fx1, fy1) > 1.05 or min(fx0, fy0) < -0.05:
        return None
    x0, y0, x1, y1 = fx0 * W, fy0 * H, fx1 * W, fy1 * H
    x0, x1 = sorted((max(0, min(W, x0)), max(0, min(W, x1))))
    y0, y1 = sorted((max(0, min(H, y0)), max(0, min(H, y1))))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    # Reject a "box" that is basically the whole crop (no localization
    # value).
    if (x1 - x0) >= 0.98 * W and (y1 - y0) >= 0.98 * H:
        return None
    return (int(x0), int(y0), int(x1), int(y1))


def _block_box(found: str, blocks: list, W: int, H: int,
               ocr_wh=None) -> Optional[Box]:
    if not blocks:
        return None
    fkey = _norm(found)
    if not fkey:
        return None
    scale = _infer_scale(blocks, ocr_wh)   # one convention for the zone
    if scale is None:
        return None
    best = None
    for b in blocks:
        if not isinstance(b, dict):
            continue
        bkey = _norm(b.get("text", ""))
        if not bkey:
            continue
        # exact word, or the word sits inside a longer element key
        if bkey == fkey or fkey in bkey:
            px = _norm_block_bbox(b.get("bbox"), W, H, scale)
            if px is None:
                continue
            # prefer the tightest matching block (closest length to word)
            score = abs(len(bkey) - len(fkey))
            if best is None or score < best[0]:
                best = (score, px)
    return best[1] if best else None


# ── deterministic CV strategy (numpy only) ────────────────────────────

def _ink_mask(crop):
    """Boolean HxW ink mask (True = text pixel), numpy only. Picks the
    darker OR lighter minority class as ink so it works on both
    dark-on-light and light-on-dark labels."""
    import numpy as np
    if crop is None or getattr(crop, "size", 0) == 0:
        return None
    arr = crop.astype("float32")
    if arr.ndim == 3:
        gray = (0.114 * arr[:, :, 0] + 0.587 * arr[:, :, 1]
                + 0.299 * arr[:, :, 2])
    else:
        gray = arr
    gray = gray.astype("uint8")
    thr = _otsu(gray)
    dark = gray < thr
    frac = float(dark.mean())
    # text is the sparse class; if "dark" covers most of the crop the
    # label is light-on-dark → ink is the light side instead.
    ink = dark if frac <= 0.5 else ~dark
    return ink


def _otsu(gray) -> int:
    import numpy as np
    hist = np.bincount(gray.ravel(), minlength=256).astype("float64")
    total = gray.size
    if total == 0:
        return 127
    omega = np.cumsum(hist)
    mu = np.cumsum(hist * np.arange(256))
    mu_t = mu[-1]
    denom = omega * (total - omega)
    denom[denom == 0] = 1e-9
    sigma_b = (mu_t * omega - mu * total) ** 2 / (denom * total)
    return int(np.argmax(sigma_b))


def _bands(active, gap_merge: int) -> List[Tuple[int, int]]:
    """Contiguous True runs of a 1-D boolean array, merging runs
    separated by a gap smaller than ``gap_merge``. Returns [(lo, hi), …]
    half-open."""
    bands: List[Tuple[int, int]] = []
    n = len(active)
    i = 0
    while i < n:
        if active[i]:
            j = i
            while j < n and active[j]:
                j += 1
            bands.append((i, j))
            i = j
        else:
            i += 1
    if not bands:
        return bands
    merged = [bands[0]]
    for lo, hi in bands[1:]:
        if lo - merged[-1][1] < gap_merge:
            merged[-1] = (merged[-1][0], hi)
        else:
            merged.append((lo, hi))
    return merged


def _row_bands(ink) -> List[Tuple[int, int]]:
    H, W = ink.shape
    row_ink = ink.sum(axis=1)
    active = row_ink > (_ROW_INK_FRAC * W)
    if not active.any():
        return []
    bands = _bands(active, gap_merge=1)
    if not bands:
        return []
    heights = sorted(hi - lo for lo, hi in bands)
    med = heights[len(heights) // 2]
    return _bands(active, gap_merge=max(1, int(_ROW_GAP_MERGE * med)))


def _word_bands(ink, y0: int, y1: int) -> List[Tuple[int, int]]:
    band = ink[y0:y1]
    bh, W = band.shape
    col_ink = band.sum(axis=0)
    active = col_ink > (_COL_INK_FRAC * bh)
    if not active.any():
        return []
    return _bands(active, gap_merge=max(2, int(_WORD_GAP_FRAC * bh)))


def _cv_box(crop, loc: dict) -> Optional[Box]:
    """Locate the word via row/word ink profiles. Returns a pixel box, or
    None. Confidence gates: the visual row count must match the OCR line
    count for index mapping to be trusted; likewise word count vs token
    count. When rows match but words don't, box the whole row (still
    correct, just coarser)."""
    ink = _ink_mask(crop)
    if ink is None:
        return None
    H, W = ink.shape  # noqa: F841 (H used in clamps below)
    rows = _row_bands(ink)
    if not rows:
        return None

    li, nlines = loc["line"], loc["n_lines"]
    # Trust index mapping only when the detected rows match the OCR lines.
    if len(rows) == nlines:
        ry0, ry1 = rows[li]
    elif nlines == 1 and len(rows) >= 1:
        ry0, ry1 = rows[0][0], rows[-1][1]      # single logical line
    else:
        return None                             # ambiguous → no box

    pad = max(2, (ry1 - ry0) // 6)
    if loc["multi"]:
        return (0, max(0, ry0 - pad), W, min(H, ry1 + pad))  # whole line

    words = _word_bands(ink, ry0, ry1)
    ti, ntok = loc["token"], loc["n_tokens"]
    if words and len(words) == ntok and 0 <= ti < len(words):
        wx0, wx1 = words[ti]
        wpad = max(2, (ry1 - ry0) // 8)
        return (max(0, wx0 - wpad), max(0, ry0 - pad),
                min(W, wx1 + wpad), min(H, ry1 + pad))
    # word segmentation didn't line up → box the whole row (safe coarse)
    return (0, max(0, ry0 - pad), W, min(H, ry1 + pad))


# ── public entry point ────────────────────────────────────────────────

def locate(crop, found: str, ocr_text: str,
           blocks: Optional[list] = None,
           ocr_wh=None) -> Optional[Box]:
    """Best pixel box for ``found`` inside ``crop`` (BGR numpy). Tries the
    OCR-blocks bbox first, then the deterministic profile. Returns None
    when nothing is confident (caller draws no box)."""
    if crop is None or getattr(crop, "size", 0) == 0 or not found:
        return None
    H, W = crop.shape[:2]
    box = _block_box(found, blocks or [], W, H, ocr_wh)
    if box is not None:
        return box
    loc = locate_token(found, ocr_text or "")
    if loc is None:
        return None
    try:
        return _cv_box(crop, loc)
    except Exception:
        return None


def draw(crop, box: Box):
    """Draw a red rectangle (+ soft translucent fill) on a copy of the
    crop. cv2 only. Returns the annotated copy."""
    import cv2
    out = crop.copy()
    H, W = out.shape[:2]
    x0, y0, x1, y1 = box
    thick = max(2, W // 300)
    # translucent fill so the word is unmistakable without hiding it
    overlay = out.copy()
    cv2.rectangle(overlay, (x0, y0), (x1, y1), _BOX_BGR, -1)
    cv2.addWeighted(overlay, 0.18, out, 0.82, 0, out)
    cv2.rectangle(out, (x0, y0), (x1, y1), _BOX_BGR, thick)
    return out


def annotate(crop, found: str, ocr_text: str,
             blocks: Optional[list] = None, ocr_wh=None):
    """Convenience: locate + draw. Returns the annotated crop, or the
    original crop unchanged when the word cannot be located."""
    try:
        box = locate(crop, found, ocr_text, blocks, ocr_wh)
    except Exception:
        box = None
    if box is None:
        return crop
    try:
        return draw(crop, box)
    except Exception:
        return crop
