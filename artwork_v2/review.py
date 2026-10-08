"""การรีวิวจุดต่างโดยคน — ``run_<n>/review.json`` (แยกจากผลตรวจโดยสิ้นเชิง)

ต่อจุดต่าง 1 จุด (เลขจุด ``id`` ของรอบนั้น) ผู้ตรวจเลือกได้ 2 อย่าง:

* ``real``  — "✓ ยืนยัน" = **เป็นข้อผิดพลาดจริงของงาน** (ระบบชี้ถูก)
* ``false`` — "⚑ รายงานปัญหา" = **ระบบแจ้งผิด** (ไม่ใช่ข้อผิดพลาดของงาน) + เหตุผล (ไม่บังคับ)
* ``None``  — ยกเลิก (กลับเป็นยังไม่รีวิว)

หลัก:
* **ไม่แตะ ``result.json`` / ผลตัดสิน / การนับ** — การรีวิวเป็นข้อมูลของคน ไม่ใช่ของระบบ
* รีวิวได้เฉพาะจุดใน ``pairs[].findings`` (ตารางหลัก = จุดที่นับในผลตัดสิน) — รายการพับไม่ต้องรีวิว
* เลขจุดที่ไม่มีในรอบนั้น ⇒ ปฏิเสธ (ไม่เขียนข้อมูลลอย ๆ)
* บันทึกผู้รีวิว + เวลา + ประวัติทุกครั้งที่กด (ย้อนดูได้ว่าใครเปลี่ยนอะไร)
"""

from __future__ import annotations

import os
import threading
import time
from typing import Iterable, List, Optional

from . import config, jobs

STATUSES = ("real", "false")
STATUS_TH = {"real": "ยืนยัน — ผิดจริง", "false": "รายงานปัญหา — ระบบแจ้งผิด"}
_HIST_MAX = 500
_LOCK = threading.Lock()      # เขียนทีละคำขอ (Flask threaded=True) — อ่าน-แก้-เขียนต้องไม่ชนกัน


def _path(rd: str) -> str:
    return os.path.join(rd, "review.json")


def findings_of(result: dict) -> List[dict]:
    """จุดที่รีวิวได้ = ตารางหลักของทุกคู่ (ลำดับตามผล)"""
    out = []
    for pr in (result or {}).get("pairs") or []:
        for f in pr.get("findings") or []:
            if isinstance(f, dict) and f.get("id") is not None:
                out.append(dict(f, _pair=pr.get("n")))
    return out


def _load_raw(rd: str) -> dict:
    try:
        d = jobs.read_json(_path(rd))
    except (OSError, ValueError):
        return {"items": {}, "history": []}
    if not isinstance(d, dict):
        return {"items": {}, "history": []}
    items = d.get("items") if isinstance(d.get("items"), dict) else {}
    hist = d.get("history") if isinstance(d.get("history"), list) else []
    return {"items": items, "history": hist}


def summary(result: dict, items: dict) -> dict:
    fs = findings_of(result)
    real, false, todo = [], [], []
    for f in fs:
        st = (items.get(str(f["id"])) or {}).get("status")
        (real if st == "real" else false if st == "false" else todo).append(f["id"])
    red_todo = [f["id"] for f in fs if f["id"] in todo and f.get("severity") == "red"]
    return {"total": len(fs), "reviewed": len(real) + len(false), "real": real, "false": false,
            "todo": todo, "red_todo": red_todo, "complete": bool(fs) and not todo}


def load(job_id: str, run: str) -> dict:
    rd = jobs.run_dir(job_id, run)
    result = jobs.read_json(os.path.join(rd, "result.json"))
    d = _load_raw(rd)
    valid = {str(f["id"]) for f in findings_of(result)}
    items = {k: v for k, v in d["items"].items() if k in valid and isinstance(v, dict)
             and v.get("status") in STATUSES}
    return {"items": items, "summary": summary(result, items),
            "history": d["history"][-50:], "log_text": log_text(result, items)}


def _clean_note(note) -> str:
    s = " ".join(str(note or "").split())
    return s[:max(0, config.REVIEW_NOTE_MAX)]


def set_status(job_id: str, run: str, ids: Iterable, status: Optional[str], note=None,
               user: str = "") -> dict:
    if status is not None and status not in STATUSES:
        raise ValueError("สถานะไม่ถูกต้อง (ต้องเป็น real / false / null)")
    ids = [str(x) for x in (ids or [])]
    if not ids or len(ids) > 200:
        raise ValueError("ต้องระบุเลขจุด 1-200 จุด")
    rd = jobs.run_dir(job_id, run)
    result = jobs.read_json(os.path.join(rd, "result.json"))
    valid = {str(f["id"]) for f in findings_of(result)}
    bad = [x for x in ids if x not in valid]
    if bad:
        raise ValueError("ไม่มีจุด %s ในตารางจุดต่างของรอบนี้" % ", ".join("#" + b for b in bad[:5]))
    note = _clean_note(note) if status == "false" else ""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _LOCK:
        d = _load_raw(rd)
        for x in ids:
            prev = (d["items"].get(x) or {}).get("status")
            if status is None:
                d["items"].pop(x, None)
            else:
                d["items"][x] = {"status": status, "note": note, "by": user or "", "at": now}
            d["history"].append({"id": x, "from": prev, "to": status, "note": note,
                                 "by": user or "", "at": now})
        d["history"] = d["history"][-_HIST_MAX:]
        jobs.write_json(_path(rd), d)
    return load(job_id, run)


def log_text(result: dict, items: dict) -> str:
    """ส่วน ``[REVIEW]`` ต่อท้าย Log — ว่าง ⇒ ยังไม่มีใครรีวิว (ไม่พิมพ์อะไร)"""
    if not items:
        return ""
    s = summary(result, items)
    out = ["", "[REVIEW] (ผู้ตรวจรีวิวหลังตรวจ · ไม่ใช่ผลของระบบ · ไม่เปลี่ยนผลตัดสิน)",
           "reviewed=%d/%d real=%d false=%d todo=%s" % (
               s["reviewed"], s["total"], len(s["real"]), len(s["false"]),
               ",".join("#%s" % x for x in s["todo"]) or "-")]
    for f in findings_of(result):
        it = items.get(str(f["id"]))
        if not it:
            continue
        a = (f.get("a") or {}).get("frag") or ""
        b = (f.get("b") or {}).get("frag") or ""
        out.append("  #%s pair=%s %s/%s %r→%r : %s%s · %s %s" % (
            f["id"], f.get("_pair"), f.get("severity"), f.get("class"), a, b,
            it.get("status"), (" (" + it["note"] + ")") if it.get("note") else "",
            it.get("by") or "-", it.get("at") or ""))
    return "\n".join(out) + "\n"
