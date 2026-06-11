"""
Persist label-paper inspection results for QC audit trail.

Each inspection is saved as its own directory under ``INSPECTIONS_DIR``:

    <timestamp>_<sku>/
        crop.jpg      — the warped/cropped image that was inspected
        report.json   — the full InspectionReport.to_dict()

A factory QC process needs to answer "what did we ship and what did the
system say about it" months later, so this is deliberately a flat,
self-contained, human-browsable layout (no database dependency — the SQL
side is for the YOLO line, not label paper).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[0-9]{8}_[0-9]{6}_[A-Za-z0-9._-]+$")
_REPORT_FILE = "report.json"
_CROP_FILE = "crop.jpg"


def _safe_sku(sku: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", sku or "unknown")[:64]


def save_inspection(inspections_dir: str, sku_code: str,
                    crop_jpeg: bytes, report: dict) -> Optional[str]:
    """
    Save one inspection. Returns its record id (directory name) or ``None``
    on failure. Never raises — history is an audit aid, not a hard
    dependency of the inspection response.
    """
    try:
        os.makedirs(inspections_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        rec_id = f"{ts}_{_safe_sku(sku_code)}"
        rec_dir = os.path.join(inspections_dir, rec_id)
        # Disambiguate sub-second collisions
        suffix = 1
        while os.path.exists(rec_dir):
            rec_dir = os.path.join(inspections_dir, f"{rec_id}-{suffix}")
            suffix += 1
        os.makedirs(rec_dir)

        if crop_jpeg:
            with open(os.path.join(rec_dir, _CROP_FILE), "wb") as f:
                f.write(crop_jpeg)

        # Store the report without the heavy embedded base64 images (the crop
        # is on disk; heatmap/previews are regenerated on demand if needed).
        slim = _slim_report(report)
        slim["_saved_at"] = datetime.now().isoformat(timespec="seconds")
        with open(os.path.join(rec_dir, _REPORT_FILE), "w", encoding="utf-8") as f:
            json.dump(slim, f, ensure_ascii=False, indent=2)

        logger.info("Saved inspection record: %s", os.path.basename(rec_dir))
        return os.path.basename(rec_dir)
    except Exception as e:
        logger.warning("Failed to save inspection history: %s", e)
        return None


def _slim_report(report: dict) -> dict:
    """Drop bulky base64 image fields before persisting to keep records small."""
    slim = dict(report)
    px = slim.get("pixel_inspection")
    if isinstance(px, dict):
        px = dict(px)
        for k in ("heatmap_png_b64", "master_png_b64", "aligned_png_b64"):
            if px.get(k):
                px[k] = ""        # blanked, not removed → schema stays stable
        slim["pixel_inspection"] = px
    return slim


def list_inspections(inspections_dir: str, limit: int = 100) -> List[dict]:
    """
    Return recent inspection summaries, newest first:
        {id, sku_code, verdict, saved_at, summary, has_crop}
    """
    if not os.path.isdir(inspections_dir):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(inspections_dir), reverse=True):
        rec_dir = os.path.join(inspections_dir, name)
        report_path = os.path.join(rec_dir, _REPORT_FILE)
        if not os.path.isfile(report_path):
            continue
        try:
            with open(report_path, "r", encoding="utf-8") as f:
                rep = json.load(f)
            out.append({
                "id": name,
                "sku_code": rep.get("sku_code", ""),
                "verdict": rep.get("verdict", ""),
                "saved_at": rep.get("_saved_at", ""),
                "summary": rep.get("summary", {}),
                "has_crop": os.path.isfile(os.path.join(rec_dir, _CROP_FILE)),
            })
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out


def load_report(inspections_dir: str, rec_id: str) -> Optional[dict]:
    """Load the full stored report for a record, or None if invalid/missing."""
    if not _ID_RE.match(rec_id):           # guard against path traversal
        return None
    report_path = os.path.join(inspections_dir, rec_id, _REPORT_FILE)
    if not os.path.isfile(report_path):
        return None
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def crop_path(inspections_dir: str, rec_id: str) -> Optional[str]:
    """Absolute path to a record's crop.jpg, or None if invalid/missing."""
    if not _ID_RE.match(rec_id):
        return None
    p = os.path.join(inspections_dir, rec_id, _CROP_FILE)
    return p if os.path.isfile(p) else None
