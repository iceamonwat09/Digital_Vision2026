"""
Report assembly, defect overlay rendering and inspection history.

Inspection folder layout (one per upload):

    data/artwork_check/inspections/<id>/
        source.<pdf|png|jpg>   uploaded artwork
        preview.png            page render at PREVIEW_DPI
        overlay.png            preview + colored defect boxes
        report.json            zones + ocr + defects + verdict
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from typing import Dict, List, Optional

import cv2
import numpy as np

from . import config

_SEVERITY_RANK = {"critical": 2, "warning": 1, "info": 0}
_CLASS_COLORS_BGR = {
    "MISMATCH_PANELS": (40, 40, 220),    # red
    "MISMATCH_ZOOM":   (0, 140, 255),    # orange
    "NUMBER_FAIL":     (180, 0, 180),    # magenta
    "PHRASE_FAIL":     (0, 0, 160),      # dark red
    "SPELL_FAIL":      (0, 200, 255),    # yellow
    "UNREADABLE":      (160, 160, 160),  # gray
}


def new_inspection_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def inspection_dir(rec_id: str, create: bool = False) -> str:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", rec_id):
        raise ValueError("bad inspection id")
    d = os.path.join(config.INSPECTIONS_DIR, rec_id)
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def compute_verdict(defects: List[dict]) -> str:
    worst = max((_SEVERITY_RANK.get(d["severity"], 0) for d in defects),
                default=0)
    return {2: "FAIL", 1: "REVIEW", 0: "PASS"}[worst]


def summarize(defects: List[dict]) -> Dict[str, int]:
    out = {cls: 0 for cls in config.DEFECT_CLASSES}
    for d in defects:
        if d["class"] in out:
            out[d["class"]] += 1
    return out


def draw_overlay(preview_bgr: np.ndarray, zones: List[dict],
                 defects: List[dict]) -> np.ndarray:
    """Zone outlines in light blue; zones with defects get a thick box
    in the color of their worst defect class."""
    img = preview_bgr.copy()
    H, W = img.shape[:2]
    by_zone: Dict[str, List[dict]] = {}
    for d in defects:
        by_zone.setdefault(d["zone_id"], []).append(d)

    for z in zones:
        x, y, w, h = z["bbox"]
        p1 = (int(x * W), int(y * H))
        p2 = (int((x + w) * W), int((y + h) * H))
        zdefs = by_zone.get(z["id"], [])
        if zdefs:
            worst = max(zdefs,
                        key=lambda d: _SEVERITY_RANK.get(d["severity"], 0))
            color = _CLASS_COLORS_BGR.get(worst["class"], (40, 40, 220))
            cv2.rectangle(img, p1, p2, color, max(3, W // 500))
            tag = f"{z['id']} {worst['class']}"
        else:
            cv2.rectangle(img, p1, p2, (200, 160, 60), max(1, W // 1200))
            tag = z["id"]
        cv2.putText(img, tag, (p1[0] + 4, max(14, p1[1] - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (60, 60, 60), 1, cv2.LINE_AA)
    return img


def save_report(rec_id: str, report: dict) -> None:
    with open(os.path.join(inspection_dir(rec_id), "report.json"),
              "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


def load_report(rec_id: str) -> Optional[dict]:
    p = os.path.join(inspection_dir(rec_id), "report.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def list_inspections(limit: int = 50) -> List[dict]:
    out = []
    try:
        ids = sorted(os.listdir(config.INSPECTIONS_DIR), reverse=True)
    except FileNotFoundError:
        return []
    for rec_id in ids[:max(1, limit)]:
        rep = None
        try:
            rep = load_report(rec_id)
        except (ValueError, json.JSONDecodeError):
            pass
        if rep:
            out.append({
                "id": rec_id,
                "created_at": rep.get("created_at", ""),
                "filename": rep.get("filename", ""),
                "brand": rep.get("brand", ""),
                "verdict": rep.get("verdict", ""),
                "defect_count": len(rep.get("defects", [])),
            })
    return out


def delete_inspection(rec_id: str) -> bool:
    d = inspection_dir(rec_id)
    if os.path.isdir(d):
        shutil.rmtree(d)
        return True
    return False
