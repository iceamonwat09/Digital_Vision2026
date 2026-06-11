"""
Spatial + textual block matching between master and captured OCR results.

Each OCR result (from ``ocr_n8n.ocr_image``) returns a ``blocks`` list::

    [{"text": str, "bbox": [x, y, w, h] | null, "conf": float}, ...]

where ``bbox`` coordinates are pixel offsets in the image that was sent to
OCR.  This module normalises bboxes to [0, 1] fractions (relative to image
dimensions), then matches blocks using a **sequence-aware** algorithm:

  1. Sort both lists by reading order (y-band strips → x position).
  2. ``difflib.SequenceMatcher`` derives an alignment skeleton on the text
     sequences.  'equal' blocks are paired directly; 'replace' windows are
     resolved locally with a combined spatial + textual score.
  3. This prevents cross-position matches (a block at the top of the master
     being incorrectly paired with a block at the bottom of the captured
     image) that plagued the old greedy approach.

Output of ``match_blocks``::

    {
      "matched": [
        {
          "master":   {"text": ..., "bbox_n": [fx, fy, fw, fh], ...},
          "captured": {"text": ..., "bbox_n": [fx, fy, fw, fh], ...},
          "iou":      float,       # overlap of normalised bboxes
          "text_sim": float,       # 1 - normalised_levenshtein ∈ [0, 1]
          "score":    float,       # combined matching score
        },
        ...
      ],
      "missing": [block, ...],   # master blocks with no captured match
      "extra":   [block, ...],   # captured blocks not matched to any master
    }

``find_field_candidate`` is the drop-in replacement for
``text_compare._find_candidate`` that uses spatial position from master
blocks to narrow the search in captured blocks before doing text scoring.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

# Weights for the combined spatial + textual score
_SPATIAL_WEIGHT = 0.5
_TEXT_WEIGHT    = 0.5
# Minimum combined score to accept a match (below → no match)
_MIN_SCORE      = 0.20
# How far to expand the master bbox search region (fraction of image size)
_SEARCH_EXPAND  = 0.15
# y-band granularity for reading-order sort (fraction of label height)
_YBAND          = 0.05


# ── Levenshtein (pure-Python fallback, no circular dependency) ───────────────

def _levenshtein(a: str, b: str) -> int:
    try:
        import Levenshtein
        return Levenshtein.distance(a, b)
    except ImportError:
        if a == b:
            return 0
        if not a:
            return len(b)
        if not b:
            return len(a)
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                cur.append(min(
                    prev[j] + 1,
                    cur[j - 1] + 1,
                    prev[j - 1] + (ca != cb),
                ))
            prev = cur
        return prev[-1]


def _text_similarity(a: str, b: str) -> float:
    """Normalised similarity in [0, 1]; 1.0 = identical."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    max_len = max(len(a), len(b))
    return 1.0 - _levenshtein(a, b) / max_len


# ── BBox helpers ─────────────────────────────────────────────────────────────

def _normalise_bbox(bbox, img_w: int, img_h: int) -> Optional[Tuple[float, float, float, float]]:
    """[x, y, w, h] pixels → (x, y, w, h) ∈ [0, 1]. Returns None if invalid."""
    if not bbox or len(bbox) != 4:
        return None
    x, y, w, h = (float(v) for v in bbox)
    if img_w <= 0 or img_h <= 0 or w <= 0 or h <= 0:
        return None
    return (x / img_w, y / img_h, w / img_w, h / img_h)


def _iou(a: Tuple[float, float, float, float],
         b: Tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx1, by1, bx2, by2 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter == 0.0:
        return 0.0
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _centroid_dist(a: Tuple[float, float, float, float],
                   b: Tuple[float, float, float, float]) -> float:
    cx1, cy1 = a[0] + a[2] / 2, a[1] + a[3] / 2
    cx2, cy2 = b[0] + b[2] / 2, b[1] + b[3] / 2
    return ((cx1 - cx2) ** 2 + (cy1 - cy2) ** 2) ** 0.5


# ── Block preparation ─────────────────────────────────────────────────────────

def _prepare_blocks(raw_blocks: list,
                    img_w: int, img_h: int) -> List[dict]:
    """Normalise each block's bbox to fractions; keep bbox_n=None if unavailable."""
    out = []
    for b in raw_blocks:
        if not isinstance(b, dict):
            continue
        text = str(b.get("text", "")).strip()
        bbox_n = _normalise_bbox(b.get("bbox"), img_w, img_h)
        out.append({
            "text":   text,
            "bbox":   b.get("bbox"),   # original pixels (for downstream use)
            "bbox_n": bbox_n,          # normalised fractions (for matching)
            "conf":   float(b.get("conf", 0.0) or 0.0),
        })
    return out


def _reading_order_key(b: dict) -> tuple:
    """
    Sort key for reading order.

    Groups blocks into horizontal y-bands (``_YBAND`` of label height) to
    tolerate minor baseline jitter between master and captured OCR, then
    orders within each band by x position (left-to-right).
    """
    bn = b.get("bbox_n")
    if bn is None:
        return (1.0, 0.0)
    y_band = round(bn[1] / _YBAND) * _YBAND
    return (y_band, bn[0])


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score_pair(m: dict, c: dict) -> float:
    """Combined spatial + textual score in [0, 1] for a master/captured pair."""
    text_sim = _text_similarity(m["text"], c["text"])
    m_bn, c_bn = m.get("bbox_n"), c.get("bbox_n")

    if m_bn is not None and c_bn is not None:
        iou = _iou(m_bn, c_bn)
        dist = _centroid_dist(m_bn, c_bn)
        spatial = max(iou, max(0.0, 1.0 - dist * 3.0))
        return _SPATIAL_WEIGHT * spatial + _TEXT_WEIGHT * text_sim
    return text_sim


def _build_pair(m: dict, c: dict, score: float) -> dict:
    return {
        "master":   m,
        "captured": c,
        "iou":      round(_iou(m["bbox_n"], c["bbox_n"]), 3)
                     if (m.get("bbox_n") and c.get("bbox_n")) else 0.0,
        "text_sim": round(_text_similarity(m["text"], c["text"]), 3),
        "score":    round(score, 3),
    }


# ── Public API ────────────────────────────────────────────────────────────────

def match_blocks(master_blocks: list,
                 captured_blocks: list,
                 master_img_w: int = 1,
                 master_img_h: int = 1,
                 captured_img_w: int = 1,
                 captured_img_h: int = 1) -> dict:
    """
    Sequence-aware block match.

    Sorts both block lists by reading order, runs
    ``difflib.SequenceMatcher`` on the text sequences to derive an alignment
    skeleton, then resolves changed ('replace') windows using the spatial +
    textual score.  This prevents cross-position false matches that occur
    with a purely greedy approach.

    Returns ``{"matched": [...], "missing": [...], "extra": [...]}``
    (see module docstring for full schema).
    """
    m_prep = _prepare_blocks(master_blocks, master_img_w, master_img_h)
    c_prep = _prepare_blocks(captured_blocks, captured_img_w, captured_img_h)

    # Sort both lists into reading order before sequence alignment
    m_sorted = sorted(m_prep, key=_reading_order_key)
    c_sorted = sorted(c_prep, key=_reading_order_key)

    m_texts = [b["text"] for b in m_sorted]
    c_texts = [b["text"] for b in c_sorted]

    matched:   List[dict] = []
    matched_m: set        = set()
    matched_c: set        = set()

    sm = SequenceMatcher(None, m_texts, c_texts, autojunk=False)

    for tag, i1, i2, j1, j2 in sm.get_opcodes():

        if tag == "equal":
            # Text is identical → pair up in reading order, verify with score
            for d in range(i2 - i1):
                mi, ci = i1 + d, j1 + d
                m, c = m_sorted[mi], c_sorted[ci]
                score = _score_pair(m, c)
                if score >= _MIN_SCORE:
                    matched.append(_build_pair(m, c, score))
                    matched_m.add(mi)
                    matched_c.add(ci)

        elif tag == "replace":
            # Text differs locally → score-based matching confined to this
            # window (cross-position matches are structurally impossible here)
            for mi in range(i1, i2):
                m = m_sorted[mi]
                best_s, best_ci = -1.0, -1
                for ci in range(j1, j2):
                    if ci in matched_c:
                        continue
                    s = _score_pair(m, c_sorted[ci])
                    if s > best_s:
                        best_s, best_ci = s, ci
                if best_ci >= 0 and best_s >= _MIN_SCORE:
                    c = c_sorted[best_ci]
                    matched.append(_build_pair(m, c, best_s))
                    matched_m.add(mi)
                    matched_c.add(best_ci)

        # 'delete' → master blocks with no captured match → missing
        # 'insert' → captured blocks with no master match → extra

    missing = [m_sorted[i] for i in range(len(m_sorted)) if i not in matched_m]
    extra   = [c_sorted[i] for i in range(len(c_sorted)) if i not in matched_c]

    return {"matched": matched, "missing": missing, "extra": extra}


def find_field_candidate(expected: str,
                         method: str,
                         master_blocks: list,
                         captured_blocks: list,
                         master_img_w: int = 1,
                         master_img_h: int = 1,
                         captured_img_w: int = 1,
                         captured_img_h: int = 1) -> Optional[str]:
    """
    Spatial-aware field candidate finder.

    1. Locate which master block contains (or best matches) ``expected``.
    2. Use that block's normalised bbox as a search region in captured blocks
       (expanded by ±``_SEARCH_EXPAND`` in each direction).
    3. From the spatial candidates, return the best text match.

    Returns ``None`` when blocks are unavailable or confidence is too low,
    so the caller can fall back to anchor-based or flat-text search.
    """
    m_prep = _prepare_blocks(master_blocks, master_img_w, master_img_h)
    c_prep = _prepare_blocks(captured_blocks, captured_img_w, captured_img_h)

    if not m_prep or not c_prep:
        return None

    meth = method.lower()

    # ── Step 1: find master block that best matches expected ─────────────────
    target_m: Optional[dict] = None
    if meth == "regex":
        for b in m_prep:
            if re.search(expected, b["text"]):
                target_m = b
                break
    elif meth == "exact":
        for b in m_prep:
            if b["text"] == expected or expected in b["text"]:
                target_m = b
                break

    if target_m is None:
        target_m = max(m_prep, key=lambda b: _text_similarity(expected, b["text"]))

    # ── Step 2: spatial candidates from captured ─────────────────────────────
    m_bn = target_m.get("bbox_n")
    if m_bn is not None:
        sx = max(0.0, m_bn[0] - _SEARCH_EXPAND)
        sy = max(0.0, m_bn[1] - _SEARCH_EXPAND)
        sr = min(1.0, m_bn[0] + m_bn[2] + _SEARCH_EXPAND)
        sb = min(1.0, m_bn[1] + m_bn[3] + _SEARCH_EXPAND)
        search_box = (sx, sy, sr - sx, sb - sy)
        spatial_candidates = [
            c for c in c_prep
            if c.get("bbox_n") and _iou(search_box, c["bbox_n"]) > 0
        ]
        candidates = spatial_candidates if spatial_candidates else c_prep
    else:
        candidates = c_prep

    # ── Step 3: best text match among candidates ─────────────────────────────
    if not candidates:
        return None

    if meth == "regex":
        for c in candidates:
            if re.search(expected, c["text"]):
                return c["text"]
        return ""

    best = max(candidates, key=lambda c: _text_similarity(expected, c["text"]))
    if not best:
        return None
    min_sim = 0.5 if len(expected) <= 6 else 0.3
    if _text_similarity(expected, best["text"]) < min_sim:
        return None
    return best["text"]


def diff_summary(match_result: dict) -> dict:
    """
    Compact summary of a ``match_blocks`` result, suitable for serialising
    as context for Gemini's visual diff prompt.
    """
    matched = match_result.get("matched", [])
    missing = match_result.get("missing", [])
    extra   = match_result.get("extra", [])

    low_sim = [
        {"master": p["master"]["text"], "captured": p["captured"]["text"],
         "score": p["score"]}
        for p in matched
        if p["text_sim"] < 0.85
    ]

    return {
        "matched_count": len(matched),
        "missing_count": len(missing),
        "extra_count":   len(extra),
        "missing_texts": [b["text"] for b in missing if b["text"]][:10],
        "extra_texts":   [b["text"] for b in extra   if b["text"]][:10],
        "suspect_matches": low_sim[:10],
    }
