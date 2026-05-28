"""
Spatial + textual block matching between master and captured OCR results.

Each OCR result (from ``ocr_n8n.ocr_image``) returns a ``blocks`` list::

    [{"text": str, "bbox": [x, y, w, h] | null, "conf": float}, ...]

where ``bbox`` coordinates are pixel offsets in the image that was sent to
OCR.  This module normalises bboxes to [0, 1] fractions (relative to image
dimensions), then greedily matches each master block to the best unmatched
captured block using a combined spatial + textual score.

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
from typing import List, Optional, Tuple

# Weights for the combined spatial + textual score
_SPATIAL_WEIGHT = 0.5
_TEXT_WEIGHT    = 0.5
# Minimum combined score to accept a match (below → no match)
_MIN_SCORE      = 0.20
# How far to expand the master bbox search region (fraction of image size)
_SEARCH_EXPAND  = 0.15


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


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score_pair(m: dict, c: dict) -> float:
    """Combined spatial + textual score in [0, 1] for a master/captured pair."""
    text_sim = _text_similarity(m["text"], c["text"])
    m_bn, c_bn = m.get("bbox_n"), c.get("bbox_n")

    if m_bn is not None and c_bn is not None:
        iou = _iou(m_bn, c_bn)
        # Centroid proximity (scaled so 33% of label width = 0)
        dist = _centroid_dist(m_bn, c_bn)
        spatial = max(iou, max(0.0, 1.0 - dist * 3.0))
        return _SPATIAL_WEIGHT * spatial + _TEXT_WEIGHT * text_sim
    # No spatial info on at least one side → text-only
    return text_sim


# ── Public API ────────────────────────────────────────────────────────────────

def match_blocks(master_blocks: list,
                 captured_blocks: list,
                 master_img_w: int = 1,
                 master_img_h: int = 1,
                 captured_img_w: int = 1,
                 captured_img_h: int = 1) -> dict:
    """
    Greedy match: for each master block (in reading order) find the best
    unmatched captured block.  Pairs scoring below ``_MIN_SCORE`` are left
    unmatched.

    Returns ``{"matched": [...], "missing": [...], "extra": [...]}``
    (see module docstring for full schema).
    """
    m_prep = _prepare_blocks(master_blocks, master_img_w, master_img_h)
    c_prep = _prepare_blocks(captured_blocks, captured_img_w, captured_img_h)

    matched: List[dict] = []
    used_c: set = set()

    for m in m_prep:
        best_score, best_idx = -1.0, -1
        for i, c in enumerate(c_prep):
            if i in used_c:
                continue
            s = _score_pair(m, c)
            if s > best_score:
                best_score, best_idx = s, i

        if best_idx >= 0 and best_score >= _MIN_SCORE:
            c = c_prep[best_idx]
            used_c.add(best_idx)
            matched.append({
                "master":   m,
                "captured": c,
                "iou":      round(_iou(m["bbox_n"], c["bbox_n"]), 3)
                             if (m.get("bbox_n") and c.get("bbox_n")) else 0.0,
                "text_sim": round(_text_similarity(m["text"], c["text"]), 3),
                "score":    round(best_score, 3),
            })

    matched_master_ids = {id(p["master"]) for p in matched}
    missing = [m for m in m_prep if id(m) not in matched_master_ids]
    extra   = [c_prep[i] for i in range(len(c_prep)) if i not in used_c]

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

    Returns ``None`` when blocks are unavailable, so the caller can fall
    back to flat-text ``_find_candidate``.
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
        # Fallback: pick master block with highest text similarity
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
    # Return None when confidence too low so flat-text fallback can take over.
    # Short strings (≤6 chars) need a higher bar because many blocks score
    # similarly by chance.
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
