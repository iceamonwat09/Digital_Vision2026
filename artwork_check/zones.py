"""
Zone model, automatic text-zone proposal and zone-template storage.

A zone is a dict:

    {
      "id":    "z3",
      "type":  "panel" | "zoom" | "header" | "ignore",
      "group": "A",                  # zones sharing a group are expected
                                     # to carry IDENTICAL text and are
                                     # cross-compared (majority voting);
                                     # "" = standalone zone
      "bbox":  [x, y, w, h],         # normalized 0..1 page coordinates
      "label": "SIDE 1"              # free text for the human
    }

Auto-proposal only has to be *good enough to adjust*, not perfect — the
UI lets the user move/resize/retype every box, and the layout can be
saved as a template per print house so this is a one-time job per form.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import List

import cv2
import numpy as np

from . import config


VALID_TYPES = ("panel", "zoom", "header", "ignore")


def propose_zones(preview_bgr: np.ndarray,
                  max_zones: int = 24) -> List[dict]:
    """
    Suggest text-bearing zones on the rendered page.

    Morphological closing over an inverted-threshold image merges glyphs
    into blocks; blocks are filtered by size and returned largest-first.
    Groups are pre-assigned by matching block sizes (repeated carton
    panels have near-identical dimensions).
    """
    gray = cv2.cvtColor(preview_bgr, cv2.COLOR_BGR2GRAY)
    H, W = gray.shape[:2]

    # Ink mask: anything noticeably darker than paper.
    _, ink = cv2.threshold(gray, 0, 255,
                           cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Merge characters → words → blocks. Kernel scales with page size.
    # (W//100, H//120, 1 iteration) measured best on real print masters:
    # large enough to merge a text panel, small enough not to swallow
    # the whole dieline into one blob.
    kw = max(8, W // 100)
    kh = max(6, H // 120)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kw, kh))
    blocks = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, kernel, iterations=1)

    contours, _ = cv2.findContours(blocks, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    min_area = (W * H) * 0.0004      # drop specks / dieline tick marks
    max_area = (W * H) * 0.35        # blobs above this get re-split
    rects = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        if area < min_area:
            continue
        if area <= max_area:
            rects.append((x, y, w, h))
        else:
            # A whole row of carton panels often merges into one blob
            # (panels touch via the dieline). Re-segment that region
            # with a smaller kernel to split it into its panels.
            sub = ink[y:y + h, x:x + w]
            k2 = cv2.getStructuringElement(
                cv2.MORPH_RECT, (max(4, kw // 3), max(3, kh // 3)))
            sub_blocks = cv2.morphologyEx(sub, cv2.MORPH_CLOSE, k2)
            sub_cnts, _ = cv2.findContours(sub_blocks, cv2.RETR_EXTERNAL,
                                           cv2.CHAIN_APPROX_SIMPLE)
            for sc in sub_cnts:
                sx, sy, sw, sh = cv2.boundingRect(sc)
                if min_area <= sw * sh <= max_area:
                    rects.append((x + sx, y + sy, sw, sh))
    rects.sort(key=lambda r: r[2] * r[3], reverse=True)
    rects = rects[:max_zones]
    # Reading order for stable ids.
    rects.sort(key=lambda r: (r[1] // max(1, H // 20), r[0]))

    # Pre-group near-identical sizes → likely repeated panels.
    zones: List[dict] = []
    group_letters = iter("ABCDEFGHJKLMNPQRSTUVWXYZ")
    size_groups: List[dict] = []   # {"w":..,"h":..,"name":..,"count":..}
    for i, (x, y, w, h) in enumerate(rects):
        gname = ""
        for sg in size_groups:
            if (abs(w - sg["w"]) / max(w, sg["w"]) < 0.06 and
                    abs(h - sg["h"]) / max(h, sg["h"]) < 0.06):
                sg["count"] += 1
                gname = sg["name"]
                break
        else:
            try:
                gname = next(group_letters)
            except StopIteration:
                gname = ""
            size_groups.append({"w": w, "h": h, "name": gname, "count": 1})
        zones.append({
            "id": f"z{i + 1}",
            "type": "panel",
            "group": gname,
            "bbox": [round(x / W, 5), round(y / H, 5),
                     round(w / W, 5), round(h / H, 5)],
            "label": f"โซน {i + 1}",
        })

    # Groups that ended up with a single member are standalone.
    counts: dict = {}
    for z in zones:
        counts[z["group"]] = counts.get(z["group"], 0) + 1
    for z in zones:
        if counts.get(z["group"], 0) < 2:
            z["group"] = ""
    return zones


def snap_bbox(preview_bgr: np.ndarray, bbox: List[float],
              pad: float = 0.08) -> List[float]:
    """
    Fit a user-drawn bbox to the content under it (double-click in UI).

    The box is first expanded by ``pad`` (fraction of its own size) so
    content the user accidentally cut off is recovered, then shrunk to
    the tight bounds of "non-background" pixels. Background is sampled
    from the border ring of the expanded crop, which makes the result
    polarity-independent: a red/navy panel on a white page snaps to the
    panel edge, while a box inside a flat panel snaps to its text block.

    Repeated calls keep growing toward content cut off farther than one
    pad step, so double-clicking again refines the fit. Returns the
    original bbox unchanged when no usable content is found.
    """
    H, W = preview_bgr.shape[:2]
    x, y, w, h = bbox
    px, py = w * pad, h * pad
    x0 = max(0, int(round((x - px) * W)))
    y0 = max(0, int(round((y - py) * H)))
    x1 = min(W, int(round((x + w + px) * W)))
    y1 = min(H, int(round((y + h + py) * H)))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return [round(float(v), 5) for v in bbox]

    gray = cv2.cvtColor(preview_bgr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    ch, cw = gray.shape[:2]

    # background reference = median of the crop's border ring
    t = max(2, min(ch, cw) // 100)
    border = np.concatenate([gray[:t].ravel(), gray[-t:].ravel(),
                             gray[:, :t].ravel(), gray[:, -t:].ravel()])
    bg = float(np.median(border))
    mask = (np.abs(gray.astype(np.int16) - bg) > 24).astype(np.uint8)

    # rows/cols with almost no content are specks or neighbour slivers —
    # they must not stretch the snapped box
    col_density = mask.sum(axis=0) / float(ch)
    row_density = mask.sum(axis=1) / float(cw)
    keep_x = np.where(col_density > 0.01)[0]
    keep_y = np.where(row_density > 0.01)[0]
    if keep_x.size < 4 or keep_y.size < 4:
        return [round(float(v), 5) for v in bbox]

    m = max(2, min(ch, cw) // 150)            # small visual margin
    nx0 = max(0, int(keep_x[0]) - m) + x0
    nx1 = min(cw, int(keep_x[-1]) + 1 + m) + x0
    ny0 = max(0, int(keep_y[0]) - m) + y0
    ny1 = min(ch, int(keep_y[-1]) + 1 + m) + y0
    return [round(nx0 / W, 5), round(ny0 / H, 5),
            round((nx1 - nx0) / W, 5), round((ny1 - ny0) / H, 5)]


def sanitize_zones(raw) -> List[dict]:
    """Validate zones arriving from the browser. Raises ValueError."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("zones must be a non-empty list")
    out = []
    seen_ids = set()
    for i, z in enumerate(raw):
        if not isinstance(z, dict):
            raise ValueError(f"zone {i} is not an object")
        zid = str(z.get("id") or f"z{i + 1}")
        if zid in seen_ids:
            raise ValueError(f"duplicate zone id {zid}")
        seen_ids.add(zid)
        ztype = str(z.get("type", "panel")).lower()
        if ztype not in VALID_TYPES:
            raise ValueError(f"zone {zid}: bad type {ztype!r}")
        bbox = z.get("bbox")
        if (not isinstance(bbox, (list, tuple)) or len(bbox) != 4):
            raise ValueError(f"zone {zid}: bbox must be [x,y,w,h]")
        x, y, w, h = (float(v) for v in bbox)
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1):
            raise ValueError(f"zone {zid}: bbox out of 0..1 range")
        x, y = max(0.0, x), max(0.0, y)
        w, h = min(w, 1.0 - x), min(h, 1.0 - y)
        out.append({
            "id": zid,
            "type": ztype,
            "group": re.sub(r"[^A-Za-z0-9_-]", "", str(z.get("group", "")))[:12],
            "bbox": [round(x, 5), round(y, 5), round(w, 5), round(h, 5)],
            "label": str(z.get("label", ""))[:80],
        })
    return out


# ── Zone templates (per print-house layout) ───────────────────────────

def _template_path(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9ก-๛_ -]", "", name).strip()
    if not safe:
        raise ValueError("invalid template name")
    return os.path.join(config.TEMPLATES_DIR, f"{safe}.json")


def list_templates() -> List[dict]:
    out = []
    for fn in sorted(os.listdir(config.TEMPLATES_DIR)):
        if fn.endswith(".json"):
            out.append({"name": fn[:-5]})
    return out


def save_template(name: str, zones: List[dict]) -> None:
    data = {"name": name, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "zones": sanitize_zones(zones)}
    with open(_template_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_template(name: str) -> List[dict]:
    with open(_template_path(name), encoding="utf-8") as f:
        return sanitize_zones(json.load(f).get("zones", []))
