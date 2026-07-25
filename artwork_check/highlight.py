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

import os
import re
import shutil
import sys
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
    into 0..1 fractions. Three conventions are possible:

      * ``0..1``   — already fractions                → (1, 1)
      * ``0..1000``— Gemini's normalized convention   → (1000, 1000)
      * pixels     — of the OCR crop ``ocr_wh``       → (ow, oh)

    Magnitude alone can't separate 0..1000 from pixels (600 could be
    either), so instead every candidate scale is TESTED against all the
    zone's boxes: a scale is valid only if every box lands inside the
    frame (0..1 ± slack), and among valid scales the winner is the one
    whose boxes best FILL the crop (largest reached fraction). A zone is
    drawn tightly around its content, so the correct convention makes the
    text span ~the whole crop, while a wrong one leaves everything
    bunched in a corner (low coverage) or pushed out of frame (invalid).
    Decided once per zone from all boxes, so a single corner box can't
    flip it. Returns None when no candidate fits (→ CV fallback)."""
    boxes = []
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
        if w > 0 and h > 0:
            boxes.append((x, y, x + w, y + h))
    if not boxes:
        return None

    candidates = [(1.0, 1.0)]
    if ocr_wh and len(ocr_wh) == 2:
        try:
            ow, oh = float(ocr_wh[0]), float(ocr_wh[1])
        except (TypeError, ValueError):
            ow = oh = 0.0
        if ow > 0 and oh > 0:
            candidates.append((1000.0, 1000.0))
            candidates.append((ow, oh))

    best = None            # (coverage, scale)
    for sx, sy in candidates:
        cover = 0.0
        ok = True
        for x0, y0, x1, y1 in boxes:
            fx0, fy0, fx1, fy1 = x0 / sx, y0 / sy, x1 / sx, y1 / sy
            if fx0 < -0.05 or fy0 < -0.05 or fx1 > 1.05 or fy1 > 1.05:
                ok = False
                break
            cover = max(cover, fx1, fy1)
        if ok and (best is None or cover > best[0]):
            best = (cover, (sx, sy))
    return best[1] if best else None


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


def _block_boxes(found: str, blocks: list, W: int, H: int,
                 ocr_wh=None) -> List[Box]:
    """Every OCR-backend block box matching ``found`` (tightest first)."""
    if not blocks:
        return []
    fkey = _norm(found)
    if not fkey:
        return []
    scale = _infer_scale(blocks, ocr_wh)   # one convention for the zone
    if scale is None:
        return []
    hits = []
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
            hits.append((abs(len(bkey) - len(fkey)), px))
    hits.sort(key=lambda p: p[0])
    return [px for _, px in hits]


def _block_box(found: str, blocks: list, W: int, H: int,
               ocr_wh=None) -> Optional[Box]:
    hits = _block_boxes(found, blocks, W, H, ocr_wh)
    return hits[0] if hits else None


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
    dark = gray <= thr
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


# ── local Tesseract word localization (optional, most accurate) ───────
# We already KNOW the target word (the defect's ``found``); Tesseract is
# used ONLY to find WHERE that word sits — its transcription accuracy is
# irrelevant to the QC verdict, which is computed elsewhere from the OCR
# backend. In a synthetic benchmark of label crops (see
# scratchpad/bench_highlight.py) this located the right word ~89% of the
# time (IoU 0.95) vs ~54% for the projection profile, and — critically —
# far fewer wrong boxes. Import + binary are optional: if pytesseract or
# the tesseract binary is missing, this returns None and the caller falls
# back (never an error).

_tess_state: Optional[bool] = None


def _find_tesseract_cmd() -> Optional[str]:
    """Locate the tesseract executable so the station does not have to add
    it to PATH by hand. Order:
      1. ``ARTWORK_TESSERACT_CMD`` env — an explicit full path (wins).
      2. already on PATH (``shutil.which``) — Linux/mac installs, or a
         Windows box where the installer added it to PATH.
      3. the usual Windows install locations of the UB-Mannheim build
         (``C:\\Program Files\\Tesseract-OCR\\tesseract.exe`` and the
         per-user ``%LOCALAPPDATA%\\Programs\\...`` variant).
    Returns a path (or the bare name found on PATH), or None if nothing
    is found — in which case the caller simply draws no box."""
    cmd = os.getenv("ARTWORK_TESSERACT_CMD", "").strip().strip('"')
    if cmd and os.path.isfile(cmd):
        return cmd

    on_path = shutil.which("tesseract")
    if on_path:
        return on_path

    if sys.platform.startswith("win"):
        roots = [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        for root in roots:
            if not root:
                continue
            p = os.path.join(root, "Tesseract-OCR", "tesseract.exe")
            if os.path.isfile(p):
                return p
    return None


def _tesseract_available() -> bool:
    global _tess_state
    if _tess_state is None:
        _tess_state = False
        try:
            import pytesseract
            cmd = _find_tesseract_cmd()
            if cmd:
                pytesseract.pytesseract.tesseract_cmd = cmd
            pytesseract.get_tesseract_version()
            _tess_state = True
        except Exception:
            _tess_state = False
    return _tess_state


_lang_cache: dict = {}


def _resolve_langs(requested: str) -> str:
    """Filter a '+'-joined tesseract language string down to the languages
    actually installed. This is the safety net for the config value
    ``ARTWORK_HIGHLIGHT_TESS_LANG``: if it names a language whose
    traineddata is NOT installed (e.g. ``eng+ara+tha`` on a box with only
    ``eng``), passing it to tesseract raises "Failed loading language ..."
    for the WHOLE call — which would wipe out every red box, English
    included. Filtering keeps only installed languages so English (or
    whatever IS present) still works; the missing one just doesn't get
    localized. Falls back to ``eng``. Cached per requested string."""
    req = (requested or "eng").strip() or "eng"
    if req in _lang_cache:
        return _lang_cache[req]
    avail = set()
    try:
        import pytesseract
        avail = set(pytesseract.get_languages(config="") or [])
    except Exception:
        avail = set()
    if avail:
        keep = [ln for ln in req.split("+") if ln and ln in avail]
        if not keep:
            keep = ["eng"] if "eng" in avail else \
                (sorted(avail - {"osd"})[:1] or ["eng"])
        resolved = "+".join(keep)
    else:
        # couldn't enumerate installed langs → best-effort: try as-is
        # (still guarded by the try/except around image_to_data below)
        resolved = req
    _lang_cache[req] = resolved
    return resolved


def _tess_boxes(crop, found: str, lang: str = "eng") -> List[Box]:
    """Every Tesseract word box matching ``found`` (best tier first)."""
    if not _tesseract_available():
        return []
    try:
        import cv2
        import pytesseract
        from pytesseract import Output
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        data = pytesseract.image_to_data(rgb, lang=_resolve_langs(lang),
                                         output_type=Output.DICT)
    except Exception:
        return []
    fkey = _norm(found)
    if not fkey:
        return []
    words = []
    n = len(data.get("text", []))
    for i in range(n):
        k = _norm(data["text"][i])
        if not k:
            continue
        box = (int(data["left"][i]), int(data["top"][i]),
               int(data["left"][i] + data["width"][i]),
               int(data["top"][i] + data["height"][i]))
        words.append((k, box))
    return _all_word_matches(words, fkey)


def _tess_box(crop, found: str, lang: str = "eng") -> Optional[Box]:
    hits = _tess_boxes(crop, found, lang)
    return hits[0] if hits else None


def _all_word_matches(words, fkey: str) -> list:
    """``words``: list of (normalized_key, payload). Return the payloads of
    EVERY occurrence of ``fkey``, best-tier first, or [].

    Tiering matters for correctness: only ONE tier is ever returned. If any
    literal match (exact / substring) exists, fuzzy candidates are dropped
    entirely — mixing them would add boxes on merely-similar words next to
    the real ones. Within the literal tier all hits are kept (a misspelling
    like "Cude" is typically printed on several rows of the same table, and
    the reviewer must see all of them to fix them in one pass).

    The fuzzy fallback runs ONLY for ASCII-Latin words of length >= 5. A
    1–2 character edit on a short or non-Latin word is a DIFFERENT word —
    e.g. the Chinese 灰分 (ash) vs 水分 (moisture) differ by one glyph — so
    an edit-distance match there draws a confidently-WRONG box, which in a
    QC tool is worse than drawing none. Latin typos the mode exists to
    catch ("SHREDDED"→"REDDED") are long and ASCII, so they still fuzzy."""
    if not fkey:
        return []
    literal = []
    for k, payload in words:
        if not k:
            continue
        if k == fkey or fkey in k or (k in fkey and len(k) >= 3):
            literal.append((abs(len(k) - len(fkey)), payload))
    if literal:
        # tightest keys first (exact word before a long line that contains it)
        literal.sort(key=lambda p: p[0])
        return [payload for _, payload in literal]
    if not (fkey.isascii() and len(fkey) >= 5):
        return []
    try:
        from .checks import levenshtein
    except Exception:
        return []
    fuzzy = []
    for k, payload in words:
        if not k:
            continue
        d = levenshtein(k, fkey)
        if d <= len(fkey) // 3:
            fuzzy.append((d, payload))
    fuzzy.sort(key=lambda p: p[0])
    return [payload for _, payload in fuzzy]


def _best_word_match(words, fkey: str):
    """Single best payload for ``fkey`` (see ``_all_word_matches``), or
    None. Kept as the single-box entry point."""
    hits = _all_word_matches(words, fkey)
    return hits[0] if hits else None


# ── PDF text-layer word boxes (exact, any script, no OCR) ─────────────
# When a zone was read from a PDF text layer (engine == "pdf-text"), the
# exact word rectangles are already in the PDF — pdf_ingest hands them
# here as (text, fraction-box-within-zone). No OCR, no traineddata, works
# for every script the PDF carries (Hebrew, Arabic, CJK, …). Highest
# accuracy of all strategies, so it runs first when available.

def match_word_boxes(words, found: str) -> List[Tuple[float, float,
                                                      float, float]]:
    """EVERY zone-fraction box for ``found`` among PDF ``words`` — a list of
    (text, (fx0, fy0, fx1, fy1)) with fractions relative to the zone. Same
    matching rules as OCR, incl. the CJK/short-word fuzzy guard."""
    keyed = [(_norm(t), fb) for t, fb in (words or [])]
    return _all_word_matches(keyed, _norm(found))


def match_word_box(words, found: str) -> Optional[Tuple[float, float,
                                                        float, float]]:
    """Best single zone-fraction box for ``found`` (see
    ``match_word_boxes``), or None."""
    hits = match_word_boxes(words, found)
    return hits[0] if hits else None


def rotate_frac_box(box: Tuple[float, float, float, float],
                    angle: int) -> Tuple[float, float, float, float]:
    """Rotate a fraction box within the unit square to match a crop that
    was rotated ``angle`` degrees CLOCKWISE (0/90/180/270). PDF word boxes
    live in the un-rotated page; the displayed crop may be rotated, so the
    box must follow."""
    a = angle % 360
    if a == 0:
        return box
    x0, y0, x1, y1 = box
    pts = [(x0, y0), (x1, y1)]
    if a == 90:      # (x,y) -> (1-y, x)
        pts = [(1 - y, x) for x, y in pts]
    elif a == 180:   # (x,y) -> (1-x, 1-y)
        pts = [(1 - x, 1 - y) for x, y in pts]
    elif a == 270:   # (x,y) -> (y, 1-x)
        pts = [(y, 1 - x) for x, y in pts]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def frac_to_px(fbox: Tuple[float, float, float, float], W: int,
               H: int) -> Optional[Box]:
    """Scale a fraction box to pixel (x0,y0,x1,y1) inside a W×H crop, with
    the same guards as the OCR path (min size, not the whole crop)."""
    fx0, fy0, fx1, fy1 = fbox
    x0, x1 = sorted((max(0, min(W, fx0 * W)), max(0, min(W, fx1 * W))))
    y0, y1 = sorted((max(0, min(H, fy0 * H)), max(0, min(H, fy1 * H))))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    if (x1 - x0) >= 0.98 * W and (y1 - y0) >= 0.98 * H:
        return None
    return (int(x0), int(y0), int(x1), int(y1))


# ── public entry point ────────────────────────────────────────────────

def locate_all(crop, found: str, ocr_text: str,
               blocks: Optional[list] = None, ocr_wh=None,
               use_tesseract: bool = True, use_profile: bool = False,
               tess_lang: str = "eng", max_boxes: int = 0) -> List[Box]:
    """EVERY pixel box of ``found`` inside ``crop`` (BGR numpy), using the
    most reliable strategy that produces a hit:

      1. OCR-blocks bbox — when the backend returned per-word boxes.
      2. Tesseract — local word localization (``use_tesseract``, default
         on; auto-skips when the binary/lib is absent). Benchmarked most
         accurate.
      3. Projection profile — deterministic but error-prone (draws a wrong
         box ~40% of the time on dense tables), so OFF by default
         (``use_profile``); kept for a no-dependency last resort. Always
         a single box.

    A misspelling is usually printed on several rows of the same table
    ("Cude Protein" / "Cude Fat" / "Cude Fiber"), and a reviewer who sees
    only one box may fix only one of them — so all occurrences of the SAME
    strategy are returned, ordered best-first. ``max_boxes`` > 0 caps the
    list (keeping the best ones) so a very common word cannot bury the
    crop in rectangles. [] when nothing is confident → draw nothing."""
    if crop is None or getattr(crop, "size", 0) == 0 or not found:
        return []
    H, W = crop.shape[:2]
    hits = _block_boxes(found, blocks or [], W, H, ocr_wh)
    if not hits and use_tesseract:
        hits = _tess_boxes(crop, found, tess_lang)
    if not hits and use_profile:
        loc = locate_token(found, ocr_text or "")
        if loc is not None:
            try:
                box = _cv_box(crop, loc)
            except Exception:
                box = None
            if box is not None:
                hits = [box]
    hits = _dedupe_boxes(hits)
    if max_boxes and max_boxes > 0:
        hits = hits[:max_boxes]
    return hits


def _dedupe_boxes(boxes: List[Box], iou_thr: float = 0.5) -> List[Box]:
    """Drop boxes that overlap an already-kept one (same word matched by
    both a tight key and the line that contains it). Keeps input order, so
    best-first survives."""
    kept: List[Box] = []
    for b in boxes:
        if not any(_iou(b, k) >= iou_thr for k in kept):
            kept.append(b)
    return kept


def _iou(a: Box, b: Box) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    ua = (ax1-ax0)*(ay1-ay0) + (bx1-bx0)*(by1-by0) - inter
    return inter / ua if ua > 0 else 0.0


def locate(crop, found: str, ocr_text: str,
           blocks: Optional[list] = None, ocr_wh=None,
           use_tesseract: bool = True, use_profile: bool = False,
           tess_lang: str = "eng") -> Optional[Box]:
    """Best single pixel box for ``found`` (see ``locate_all``), or None."""
    hits = locate_all(crop, found, ocr_text, blocks, ocr_wh,
                      use_tesseract=use_tesseract, use_profile=use_profile,
                      tess_lang=tess_lang, max_boxes=1)
    return hits[0] if hits else None


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


def draw_boxes(crop, boxes):
    """Draw every box in ``boxes`` on one copy of the crop."""
    out = crop
    for i, b in enumerate(boxes or []):
        out = draw(out if i else crop, b)
    return out


def annotate(crop, found: str, ocr_text: str,
             blocks: Optional[list] = None, ocr_wh=None,
             use_tesseract: bool = True, use_profile: bool = False,
             tess_lang: str = "eng", max_boxes: int = 1):
    """Convenience: locate + draw. ``max_boxes`` = 1 boxes only the best
    occurrence (previous behavior); > 1 boxes up to that many occurrences
    of the same word; 0 = unlimited. Returns the annotated crop, or the
    original crop unchanged when the word cannot be located."""
    try:
        boxes = locate_all(crop, found, ocr_text, blocks, ocr_wh,
                           use_tesseract=use_tesseract,
                           use_profile=use_profile,
                           tess_lang=tess_lang, max_boxes=max_boxes)
    except Exception:
        boxes = []
    if not boxes:
        return crop
    try:
        return draw_boxes(crop, boxes)
    except Exception:
        return crop
