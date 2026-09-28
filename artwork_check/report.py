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
import threading
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
    "MISMATCH_CASE":   (200, 60, 60),    # blue-ish red
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


def list_inspections(limit: int = 50, can_view=None,
                     include_translate: bool = False,
                     filters: Optional[dict] = None) -> List[dict]:
    """รายการตรวจล่าสุด (ใหม่สุดก่อน).

    ``can_view`` = callable ``(owner_dict|None) -> bool`` สำหรับกรองตามเจ้าของ.
    ``None`` (ค่าเริ่มต้น) = ไม่กรอง → เดินเส้นทางเดิมทุกประการ.

    ``include_translate`` = รวมงานที่ "แปลอย่างเดียว" (ยังไม่มี report.json)
    และแนบคีย์ ``kind`` / ``translate`` ให้ทุกแถว. ``filters`` = ตัวกรองจาก
    หน้าประวัติ (ดู ``normalize_filters``). ไม่ส่งทั้งสองอย่าง = เส้นทางเดิม
    ทุกบรรทัด (คีย์ของแถวเท่าเดิมเป๊ะ).
    """
    if include_translate or filters:
        return _list_extended(limit, can_view, include_translate,
                              normalize_filters(filters))
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


# ── ข้อมูลไฟล์ตอนอัปโหลด ────────────────────────────────────────────
# ชื่อไฟล์เดิมถูกบันทึกลง report.json เท่านั้น (เกิดตอนกด "ส่งตรวจสอบ")
# ⇒ งานที่กดแปลอย่างเดียวไม่มีชื่อไฟล์ให้แสดงในประวัติ. ไฟล์นี้เขียนตั้งแต่
# ตอนอัปโหลด (best-effort — เขียนไม่สำเร็จแค่ทำให้หน้าประวัติไม่มีชื่อไฟล์)
_META_FILE = "meta.json"


def save_meta(rec_id: str, filename: str,
              extra: Optional[dict] = None) -> None:
    try:
        data = {"filename": filename or "",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        if extra:
            data.update(extra)
        with open(os.path.join(inspection_dir(rec_id), _META_FILE),
                  "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except OSError as e:
        logger.warning("[artwork] save_meta failed for %s: %s", rec_id, e)


def load_meta(rec_id: str) -> dict:
    try:
        with open(os.path.join(inspection_dir(rec_id), _META_FILE),
                  encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _time_from_id(rec_id: str) -> str:
    """``20260811-090207-xxxxxx`` → ``2026-08-11 09:02:07`` (เวลาอัปโหลด)."""
    m = re.match(r"(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})", rec_id)
    if not m:
        return ""
    y, mo, d, h, mi, se = m.groups()
    return f"{y}-{mo}-{d} {h}:{mi}:{se}"


# ── ประวัติการแปล ────────────────────────────────────────────────────
# แยกขาดจาก translation.json (แคช) โดยตั้งใจ: แคชเขียนเฉพาะผลที่ "ครบ"
# (ผลไม่ครบต้องไม่ถูกแช่ ไม่งั้นกดแปลซ้ำแล้วได้ช่องว่างเดิมตลอดไป) ส่วน
# ประวัติต้องเก็บ "ทุกครั้งที่กด" รวมครั้งที่ไม่สำเร็จ — เอาสองหน้าที่นี้
# ไปรวมไฟล์เดียวกันจะต้องแก้เงื่อนไขของแคช = เปลี่ยนพฤติกรรมเดิม
_TR_LOG = "translate_log.jsonl"     # 1 บรรทัด = 1 ครั้งที่กด (สรุปตัวเลข)
_TR_LAST = "translate_last.json"    # ตารางเต็มของครั้งล่าสุด
_TR_LOCK = threading.Lock()         # Flask threaded=True — กันเขียนชนกัน


def _is_issue(row: dict) -> bool:
    """นิยามเดียวกับตัวกรอง "เฉพาะบรรทัดน่าสงสัย" ของ renderTextTable."""
    return (row.get("status") in ("spell", "mismatch")
            or bool((row.get("ai_spell") or {}).get("flagged")))


def translate_entry(result: dict, by: str = "", brand: str = "") -> dict:
    """สรุปตัวเลขของผลแปล 1 ครั้ง (ไม่มีตารางเต็ม)."""
    rows = result.get("rows") or []
    return {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "by": by or "",
        "brand": brand or "",
        "rows": len(rows),
        "issues": sum(1 for r in rows if _is_issue(r)),
        "spell": sum(1 for r in rows if r.get("status") == "spell"),
        "mismatch": sum(1 for r in rows if r.get("status") == "mismatch"),
        "ai_flagged": sum(1 for r in rows
                          if (r.get("ai_spell") or {}).get("flagged")),
        "unsupported": sum(1 for r in rows
                           if r.get("status") == "unsupported"),
        "translated": bool(result.get("translated")),
        "cached": bool(result.get("cached")),
        "ocr_only": bool(result.get("ocr_only")),
        "note": result.get("note") or "",
    }


def record_translation(rec_id: str, result: dict, by: str = "",
                       brand: str = "",
                       error: Optional[str] = None) -> Optional[dict]:
    """บันทึกการกดแปล 1 ครั้ง (best-effort — ไม่ raise).

    ต้องไม่ทำให้การแปลล้ม: เขียนไฟล์ไม่ได้ = ประวัติขาดครั้งนั้นไป ผู้ใช้
    ยังได้ตารางคำแปลตามปกติ.

    ``error`` = ครั้งที่ทำงานไม่สำเร็จเลย (OCR/แปลโยน exception) — บันทึก
    ลง log อย่างเดียว **ไม่ทับตารางของครั้งล่าสุดที่สำเร็จ** (ไม่งั้นกดพลาด
    ครั้งเดียว ตารางที่เคยได้ก็หายจากหน้าประวัติ)
    """
    try:
        d = inspection_dir(rec_id)
        entry = translate_entry(result, by=by, brand=brand)
        if error:
            entry["error"] = str(error)[:300]
            with _TR_LOCK:
                with open(os.path.join(d, _TR_LOG), "a",
                          encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return entry
        last = dict(entry)
        last["table"] = {
            "rows": result.get("rows") or [],
            "translated": bool(result.get("translated")),
            "cached": bool(result.get("cached")),
            "ai_spell_available": bool(result.get("ai_spell_available")),
            "ocr_only": bool(result.get("ocr_only")),
            "enabled": bool(result.get("enabled")),
            "note": result.get("note"),
        }
        with _TR_LOCK:
            with open(os.path.join(d, _TR_LOG), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            tmp = os.path.join(d, _TR_LAST + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(last, f, ensure_ascii=False)
            os.replace(tmp, os.path.join(d, _TR_LAST))
        return entry
    except (OSError, ValueError, TypeError) as e:
        logger.warning("[artwork] record_translation failed for %s: %s",
                       rec_id, e)
        return None


def _read_log(rec_id: str) -> List[dict]:
    """ทุกบรรทัดของ log (เก่าสุดก่อน). บรรทัดที่เสีย (เขียนค้างครึ่ง) ข้าม."""
    p = os.path.join(inspection_dir(rec_id), _TR_LOG)
    try:
        with open(p, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def translate_summary(rec_id: str) -> Optional[dict]:
    """สรุปสำหรับแถวในตารางประวัติ หรือ ``None`` ถ้ายังไม่เคยกดแปล."""
    log = _read_log(rec_id)
    if not log:
        return None
    last = log[-1]
    return {
        "count": len(log),
        "first_at": log[0].get("at", ""),
        "last_at": last.get("at", ""),
        "last_by": last.get("by", ""),
        "rows": last.get("rows", 0),
        "issues": last.get("issues", 0),
        "translated": bool(last.get("translated")),
        "ocr_only": bool(last.get("ocr_only")),
        "error": last.get("error", ""),
        "brand": last.get("brand", ""),
        # ใช้กรองตามวันที่เท่านั้น — ถูกถอดออกก่อนตอบ JSON
        "_days": sorted({(e.get("at") or "")[:10] for e in log
                         if e.get("at")}),
    }


def load_translations(rec_id: str) -> dict:
    """ตารางเต็มของครั้งล่าสุด + รายการทุกครั้ง (ใหม่สุดก่อน)."""
    last = None
    try:
        with open(os.path.join(inspection_dir(rec_id), _TR_LAST),
                  encoding="utf-8") as f:
            last = json.load(f)
    except (OSError, json.JSONDecodeError):
        last = None
    log = _read_log(rec_id)
    total = len(log)
    cap = max(1, config.TRANSLATE_LOG_MAX)
    return {"last": last if isinstance(last, dict) else None,
            "log": list(reversed(log[-cap:])),
            "count": total}


# ── ตัวกรองของหน้าประวัติ ─────────────────────────────────────────────
_KINDS = ("inspect", "translate", "both")
_VERDICTS = ("PASS", "REVIEW", "FAIL")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def normalize_filters(raw: Optional[dict]) -> dict:
    """ตรวจ/ทำความสะอาดตัวกรองจาก query string. ค่าที่ไม่รู้จัก = ไม่กรอง
    (ไม่ใช่ error — ลิงก์เก่า/พิมพ์ผิดต้องยังเปิดหน้าได้)."""
    raw = raw or {}
    f: dict = {}
    for k in ("date_from", "date_to"):
        v = str(raw.get(k) or "").strip()
        if _DATE_RE.fullmatch(v):
            f[k] = v
    q = str(raw.get("q") or "").strip()[:100]
    if q:
        f["q"] = q.casefold()
    kind = str(raw.get("kind") or "").strip().lower()
    if kind in _KINDS:
        f["kind"] = kind
    verdict = str(raw.get("verdict") or "").strip().upper()
    if verdict in _VERDICTS:
        f["verdict"] = verdict
    if str(raw.get("tr_issues") or "").strip().lower() in ("1", "true", "yes"):
        f["tr_issues"] = True
    owner = str(raw.get("owner") or "").strip()[:64]
    if owner:
        f["owner"] = owner.casefold()
    return f


def _match(row: dict, days: List[str], f: dict) -> bool:
    if "date_from" in f or "date_to" in f:
        lo, hi = f.get("date_from", ""), f.get("date_to", "9999-99-99")
        # วันที่ของงาน = วันที่อัปโหลด/ตรวจ + ทุกวันที่มีการกดแปล ⇒ งานเก่า
        # ที่เพิ่งถูกแปลวันนี้ยังหาเจอเมื่อกรอง "วันนี้"
        if not any(lo <= d <= hi for d in days if d):
            return False
    if "q" in f:
        hay = (str(row.get("filename") or "") + " " +
               str(row.get("brand") or "")).casefold()
        if f["q"] not in hay:
            return False
    if "kind" in f and row.get("kind") != f["kind"]:
        return False
    if "verdict" in f and row.get("verdict") != f["verdict"]:
        return False
    if f.get("tr_issues"):
        tr = row.get("translate") or {}
        if not tr.get("issues"):
            return False
    if "owner" in f and str(row.get("owner") or "").casefold() != f["owner"]:
        return False
    return True


def _list_extended(limit: int, can_view, include_translate: bool,
                   f: dict) -> List[dict]:
    try:
        ids = sorted(os.listdir(config.INSPECTIONS_DIR), reverse=True)
    except FileNotFoundError:
        return []
    limit = max(1, limit)
    if not include_translate:
        # ปิดประวัติการแปล = แถวไม่มี kind/translate ⇒ ตัวกรองสองตัวนี้
        # ไม่มีความหมาย (ถ้าคงไว้จะตัดทุกแถวทิ้งเงียบ ๆ)
        f = {k: v for k, v in f.items() if k not in ("kind", "tr_issues")}
    out: List[dict] = []
    for rec_id in ids[:_MAX_SCAN]:
        if len(out) >= limit:
            break
        try:
            owner = load_owner(rec_id)
        except ValueError:          # ชื่อโฟลเดอร์ไม่ใช่ id ที่ถูกต้อง
            continue
        if can_view is not None and not can_view(owner):
            continue
        try:
            rep = load_report(rec_id)
        except (ValueError, json.JSONDecodeError):
            rep = None
        tr = translate_summary(rec_id) if include_translate else None
        if not rep and not tr:
            continue
        meta = load_meta(rec_id)
        rep = rep or {}
        row = {
            "id": rec_id,
            "created_at": (rep.get("created_at") or meta.get("created_at")
                           or _time_from_id(rec_id)),
            # meta ก่อน: report.json เก็บชื่อไฟล์ที่ถูกบันทึกบนดิสก์ (``source.pdf``
            # เสมอ) ไม่ใช่ชื่อที่ผู้ใช้อัปโหลด — ค้นหาด้วยชื่อไฟล์จึงใช้ไม่ได้
            "filename": meta.get("filename") or rep.get("filename") or "",
            "brand": rep.get("brand") or (tr or {}).get("brand", ""),
            "verdict": rep.get("verdict", "") if rep else "",
            "defect_count": len(rep.get("defects", [])) if rep else None,
            "owner": (owner or {}).get("username", ""),
        }
        if meta.get("cloned_from"):
            row["cloned_from"] = {"id": meta.get("cloned_from"),
                                  "filename": meta.get("cloned_from_filename", ""),
                                  "created_at": meta.get("cloned_from_at", "")}
        days = [row["created_at"][:10]]
        if include_translate:
            row["kind"] = ("both" if rep and tr
                           else "translate" if tr else "inspect")
            if tr:
                days += tr.pop("_days", [])
            row["translate"] = tr
        if f and not _match(row, days, f):
            continue
        out.append(row)
    return out


def list_owners(can_view=None) -> List[str]:
    """ชื่อผู้ตรวจทั้งหมดที่ผู้ดูคนนี้มีสิทธิ์เห็น (ใช้ทำตัวเลือกกรอง)."""
    try:
        ids = sorted(os.listdir(config.INSPECTIONS_DIR), reverse=True)
    except FileNotFoundError:
        return []
    names = set()
    for rec_id in ids[:_MAX_SCAN]:
        try:
            owner = load_owner(rec_id)
        except ValueError:
            continue
        if can_view is not None and not can_view(owner):
            continue
        n = (owner or {}).get("username")
        if n:
            names.add(n)
    return sorted(names, key=str.casefold)


# ── กรอบตามที่ผู้ใช้วาด (ใช้เป็นต้นแบบตรวจ Lot ใหม่) ───────────────────
# ⚠️ ห้ามดึงกรอบจาก report.json ตรง ๆ เป็นทางหลัก: ``zones[].rotate`` ในนั้น
# ถูกเขียนทับเป็น "มุมที่ OCR หมุนจริง" (เป็น 0 เสมอในเส้นทาง pdf-text) และ
# ``view_rot`` แยกไม่ออกว่าผู้ใช้ "ปักหมุด 270" หรือ "ตั้ง auto แล้ว OCR เลือก
# 270" ⇒ ไฟล์นี้เก็บกรอบ **ก่อน** ส่งเข้า pipeline = สิ่งที่ผู้ใช้เห็นจริง
_SETUP_FILE = "setup.json"
_SETUP_KEYS = ("brand", "page_rot", "auto_rotate", "force_ocr",
               "split_bands", "confirm_reads", "pixel_check")


def save_setup(rec_id: str, zones: List[dict], settings: Optional[dict] = None,
               by: str = "") -> None:
    """best-effort — เขียนไม่ได้ต้องไม่ทำให้การตรวจ/แปลล้ม."""
    try:
        d = inspection_dir(rec_id)
        # ค่าตั้งที่ครั้งนี้ไม่ได้ส่งมา (เช่นแท็บแปลไม่รู้จักช่องติ๊ก pixel)
        # ต้องคงค่าเดิมไว้ ไม่ใช่หายไปเงียบ ๆ
        prev = {}
        try:
            with open(os.path.join(d, _SETUP_FILE), encoding="utf-8") as f:
                prev = json.load(f) or {}
        except (OSError, json.JSONDecodeError):
            prev = {}
        data = {"zones": zones, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "by": by or ""}
        for k in _SETUP_KEYS:
            if settings and k in settings:
                data[k] = settings[k]
            elif isinstance(prev, dict) and k in prev:
                data[k] = prev[k]
        tmp = os.path.join(d, _SETUP_FILE + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(d, _SETUP_FILE))
    except (OSError, ValueError, TypeError) as e:
        logger.warning("[artwork] save_setup failed for %s: %s", rec_id, e)


def _legacy_setup(rep: dict) -> Optional[dict]:
    """งานก่อนมี setup.json — ประกอบกลับจาก report.json เท่าที่ทำได้.

    มุมหมุน: ``view_rot`` (มุมที่ผู้ใช้เห็นตอนลาก) ถ้ามี ไม่งั้น ``default``.
    **ไม่ใช้ ``rotate``** เพราะเป็นมุมที่ OCR หมุนจริง (0 ในเส้นทาง pdf-text
    แม้ผู้ใช้ปักหมุดไว้) · ติดธง ``approx_rotate`` ให้ UI บอกผู้ใช้ตรวจทาน
    """
    zones = []
    for z in rep.get("zones") or []:
        if not isinstance(z, dict):
            continue
        zz = {k: v for k, v in z.items()
              if k in ("id", "type", "group", "bbox", "label", "doc")}
        vr = z.get("view_rot")
        zz["rotate"] = vr if vr in (90, 180, 270) else "default"
        zones.append(zz)
    if not zones:
        return None
    out = {"zones": zones, "approx_rotate": True,
           "saved_at": rep.get("created_at", "")}
    for k in _SETUP_KEYS:
        if k in rep:
            out[k] = rep[k]
    return out


def load_setup(rec_id: str) -> Optional[dict]:
    """กรอบ + ค่าตั้งของงานนี้ หรือ ``None`` ถ้าไม่มีอะไรให้ใช้เป็นต้นแบบ."""
    d = inspection_dir(rec_id)
    try:
        with open(os.path.join(d, _SETUP_FILE), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("zones"), list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    try:
        rep = load_report(rec_id)
    except (ValueError, json.JSONDecodeError):
        rep = None
    return _legacy_setup(rep) if rep else None


def _zones_a(zones: List[dict]) -> List[dict]:
    return [z for z in zones or []
            if isinstance(z, dict) and str(z.get("doc", "a") or "a") == "a"]


def list_sources(q: str = "", can_view=None, limit: int = 30) -> List[dict]:
    """งานที่ใช้เป็นต้นแบบได้ (มีกรอบฝั่ง 🅰 อย่างน้อย 1 กรอบ) ใหม่สุดก่อน.

    คืนเฉพาะข้อมูลสรุป — **ไม่มีผลตรวจ/ภาพ** เพราะอาจเป็นงานของคนอื่น
    (CLONE_SHARE_ALL) และด่านเจ้าของยังคุมทุก endpoint ที่เปิดงานนั้นอยู่
    """
    try:
        ids = sorted(os.listdir(config.INSPECTIONS_DIR), reverse=True)
    except FileNotFoundError:
        return []
    qf = (q or "").strip().casefold()[:100]
    out: List[dict] = []
    for rec_id in ids[:_MAX_SCAN]:
        if len(out) >= max(1, limit):
            break
        try:
            owner = load_owner(rec_id)
        except ValueError:
            continue
        if can_view is not None and not can_view(owner):
            continue
        setup = load_setup(rec_id)
        if not setup:
            continue
        za = _zones_a(setup.get("zones"))
        if not za:
            continue
        meta = load_meta(rec_id)
        try:
            rep = load_report(rec_id) or {}
        except (ValueError, json.JSONDecodeError):
            rep = {}
        # งานที่เปิดจากต้นแบบแล้วยังไม่เคยตรวจ/แปล = สำเนาของต้นแบบเป๊ะ ๆ
        # ⇒ ไม่แสดงซ้ำ (ไม่งั้นทุกครั้งที่กดเปิดแล้วเปลี่ยนใจ รายการจะยาวขึ้น)
        if meta.get("cloned_from") and not rep and not _read_log(rec_id):
            continue
        filename = meta.get("filename") or rep.get("filename") or ""
        brand = setup.get("brand") or rep.get("brand") or ""
        if qf and qf not in (filename + " " + brand).casefold():
            continue
        out.append({
            "id": rec_id,
            "filename": filename,
            "brand": brand,
            "created_at": (meta.get("created_at") or rep.get("created_at")
                           or _time_from_id(rec_id)),
            "saved_at": setup.get("saved_at", ""),
            "owner": (owner or {}).get("username", ""),
            "zones_a": len(za),
            "verdict": rep.get("verdict", ""),
            "approx_rotate": bool(setup.get("approx_rotate")),
            "cloned_from": meta.get("cloned_from", ""),
        })
    return out
