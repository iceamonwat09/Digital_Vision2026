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
import logging
import os
import re
import shutil
import time
import uuid
from typing import Dict, List, Optional

import cv2
import numpy as np

from . import config

logger = logging.getLogger(__name__)

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


# ── เจ้าของการตรวจ ───────────────────────────────────────────────────
# เก็บแยกไฟล์ ไม่ใส่ใน report.json เพราะ report.json เกิดตอนกด "ส่งตรวจสอบ"
# เท่านั้น แต่ระหว่างจัดโซนมี endpoint ที่ต้องเช็คสิทธิ์แล้ว (preview / crop /
# propose / snap / autopair) — ถ้ารอ report.json ช่วงนั้นจะไม่มีเจ้าของให้เทียบ.
_OWNER_FILE = "owner.json"
# เพดานจำนวนโฟลเดอร์ที่ไล่อ่านตอนกรองตามเจ้าของ — กันกรณีผู้ใช้ใหม่ที่ยังไม่มี
# บันทึกของตัวเองเลย ต้องไล่ทั้งคลังประวัติทุกครั้งที่เปิดหน้า
_MAX_SCAN = 2000


def save_owner(rec_id: str, owner: Optional[dict]) -> None:
    """บันทึกว่าใครเป็นคนอัปโหลดการตรวจนี้ (best-effort — ไม่ raise).

    ``owner`` = ``{"user_id": "7", "username": "somchai"}`` หรือ ``None``
    (ไม่มีระบบล็อกอิน) ซึ่งจะไม่เขียนไฟล์เลย = บันทึกนั้นไม่มีเจ้าของ.
    """
    if not owner:
        return
    try:
        with open(os.path.join(inspection_dir(rec_id), _OWNER_FILE),
                  "w", encoding="utf-8") as f:
            json.dump({
                "user_id": str(owner.get("user_id") or ""),
                "username": owner.get("username") or "",
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }, f, ensure_ascii=False, indent=1)
    except OSError as e:
        # การตรวจต้องทำงานต่อได้แม้เขียนไฟล์นี้ไม่สำเร็จ. ผลคือบันทึกนั้น
        # กลายเป็น "ไม่มีเจ้าของ" = เห็นได้เฉพาะ admin (ปลอดภัยไว้ก่อน)
        logger.warning("[artwork] save_owner failed for %s: %s", rec_id, e)


def load_owner(rec_id: str) -> Optional[dict]:
    """เจ้าของการตรวจนี้ หรือ ``None`` ถ้าเป็นบันทึกเก่า/อ่านไม่ได้.

    ``None`` แปลว่า "ไม่รู้ว่าใครเป็นเจ้าของ" เสมอ — ฝั่งนโยบาย
    (``ownership.can_access``) เป็นคนตัดสินว่าให้ใครเห็น.
    """
    p = os.path.join(inspection_dir(rec_id), _OWNER_FILE)
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def list_inspections(limit: int = 50, can_view=None) -> List[dict]:
    """รายการตรวจล่าสุด (ใหม่สุดก่อน).

    ``can_view`` = callable ``(owner_dict|None) -> bool`` สำหรับกรองตามเจ้าของ.
    ``None`` (ค่าเริ่มต้น) = ไม่กรอง → เดินเส้นทางเดิมทุกประการ.
    """
    out = []
    try:
        ids = sorted(os.listdir(config.INSPECTIONS_DIR), reverse=True)
    except FileNotFoundError:
        return []
    limit = max(1, limit)
    # ไม่กรอง = ตัดตั้งแต่ต้นเหมือนเดิม; ถ้ากรองต้องเดินต่อจนกว่าจะครบ limit
    # (แต่มีเพดานกันไล่ทั้งโฟลเดอร์เมื่อผู้ใช้ใหม่ยังไม่มีบันทึกของตัวเอง)
    scan = ids[:limit] if can_view is None else ids[:_MAX_SCAN]
    for rec_id in scan:
        if len(out) >= limit:
            break
        owner = None
        if can_view is not None:
            try:
                owner = load_owner(rec_id)
            except ValueError:      # ชื่อโฟลเดอร์ไม่ใช่ id ที่ถูกต้อง
                continue
            if not can_view(owner):
                continue
        rep = None
        try:
            rep = load_report(rec_id)
        except (ValueError, json.JSONDecodeError):
            pass
        if rep:
            row = {
                "id": rec_id,
                "created_at": rep.get("created_at", ""),
                "filename": rep.get("filename", ""),
                "brand": rep.get("brand", ""),
                "verdict": rep.get("verdict", ""),
                "defect_count": len(rep.get("defects", [])),
            }
            if can_view is None:
                try:
                    owner = load_owner(rec_id)
                except ValueError:
                    owner = None
            row["owner"] = (owner or {}).get("username", "")
            out.append(row)
    return out


def delete_inspection(rec_id: str) -> bool:
    d = inspection_dir(rec_id)
    if os.path.isdir(d):
        shutil.rmtree(d)
        return True
    return False
