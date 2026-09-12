# -*- coding: utf-8 -*-
"""ความคืบหน้าของการตรวจ — "จุดเช็คพอยต์จริง" ไม่ใช่ข้อความรอลอย ๆ.

ทำไมต้องมี
-----------
เดิมระหว่างส่งตรวจหน้าเว็บขึ้นข้อความคงที่ว่า *"กำลัง OCR ทีละโซนและตรวจทุกชั้น
— โซนเยอะอาจใช้เวลาหลายสิบวินาที…"* ซึ่ง **ไม่ได้บอกอะไรเลย**:

* ไม่รู้ว่าตอนนี้ทำอะไรอยู่ · เหลืออีกกี่ขั้น
* ไม่รู้ว่า **โหมดที่ติ๊กไว้ได้ทำงานจริงไหม** หรือตกเงื่อนไขไปเงียบ ๆ
  (โหมด pixel ต้องมีโซน panel สองโซนในกลุ่มเดียวกันและเป็น PDF ทั้งคู่ —
  ถ้าไม่เข้าเงื่อนไขมันจะข้ามไปโดยผู้ใช้ไม่รู้ตัวจนกว่าจะอ่านรายงานจบ)
* เวลาไล่ปัญหาร่วมกัน เราเดาไม่ได้ว่าติดที่ขั้นไหน

โมดูลนี้เก็บสถานะ **ต่อการตรวจหนึ่งครั้ง** ไว้ในหน่วยความจำ ให้หน้าเว็บ poll
ไปวาดเป็นเส้นแนวนอนพร้อมจุดสถานะ

หลักการที่ยึด
--------------
* **ขั้นตอนคงที่เสมอ** — โหมดที่ไม่ได้เปิดจะขึ้นเป็น ``skip`` พร้อมเหตุผล
  ไม่ใช่หายไปจากเส้น (ผู้ใช้ต้องเห็นว่ามีขั้นนั้นอยู่และทำไมไม่ทำงาน)
* **advisory 100%** — ไม่แตะ ``defects`` / ``verdict`` / การนับ / DB
* **ล้มเหลวเงียบเสมอ** — บันทึกความคืบหน้าไม่สำเร็จต้องไม่ทำให้การตรวจพัง
* ไม่ import Flask ⇒ เทสต์ตรง ๆ ได้
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Dict, List, Optional

# ลำดับขั้นของการตรวจหนึ่งครั้ง — ต้องตรงกับลำดับจริงใน
# ``pipeline.run_inspection`` (มีเทสต์ล็อกไว้)
STEPS: List[tuple] = [
    ("prepare", "เตรียมไฟล์และโซน"),
    ("fonttrust", "ตรวจฟอนต์ใน PDF"),
    ("ocr", "อ่านข้อความทุกโซน"),
    ("checks", "ชั้นตรวจข้อความ"),
    ("confirm", "อ่านซ้ำเพื่อยืนยัน"),
    ("pixel", "เทียบแผงระดับพิกเซล"),
    ("coverage", "สรุปชั้นที่ได้ตรวจ"),
    ("report", "สร้างรายงาน"),
]
_KEYS = [k for k, _ in STEPS]

# สถานะที่หน้าเว็บรู้จัก — ``skip`` ต้องมีเหตุผลเสมอ
PENDING, RUNNING, OK, SKIP, WARN, FAIL = (
    "pending", "running", "ok", "skip", "warn", "fail")

_LOCK = threading.Lock()
_RUNS: "OrderedDict[str, dict]" = OrderedDict()
MAX_RUNS = 32          # เก็บย้อนหลังพอให้เปิดดูซ้ำได้ ไม่ให้บวมไม่จำกัด


class Run:
    """ตัวบันทึกความคืบหน้าของการตรวจหนึ่งครั้ง (ปลอดภัยต่อหลายเธรด)."""

    def __init__(self, rec_id: str, flags: Optional[dict] = None):
        self.rec_id = str(rec_id)
        self._t0 = time.time()
        self._cur: Optional[str] = None
        self._t_step = self._t0
        with _LOCK:
            _RUNS[self.rec_id] = {
                "id": self.rec_id,
                "t0": self._t0,
                "done": False,
                "flags": dict(flags or {}),
                "steps": [{"key": k, "label": lb, "status": PENDING,
                           "detail": "", "notes": [], "ms": None}
                          for k, lb in STEPS],
            }
            _RUNS.move_to_end(self.rec_id)
            while len(_RUNS) > MAX_RUNS:
                _RUNS.popitem(last=False)

    # ── ภายใน ────────────────────────────────────────────────────────
    def _find(self, key: str):
        run = _RUNS.get(self.rec_id)
        if not run:
            return None, None
        for s in run["steps"]:
            if s["key"] == key:
                return run, s
        return run, None

    # ── API ที่ pipeline เรียก ────────────────────────────────────────
    def start(self, key: str, detail: str = "") -> None:
        """ขั้นนี้กำลังทำงาน."""
        try:
            with _LOCK:
                run, s = self._find(key)
                if s is None:
                    return
                # ขั้นก่อนหน้าที่ยังค้างเป็น running ให้ปิดเป็น ok เสมอ —
                # ไม่งั้นเส้นจะมีจุดกะพริบสองจุดพร้อมกัน
                for prev in run["steps"]:
                    if prev["status"] == RUNNING:
                        prev["status"] = OK
                        if prev["ms"] is None:
                            prev["ms"] = int((time.time() - self._t_step) * 1000)
                s["status"] = RUNNING
                s["detail"] = detail
            self._cur = key
            self._t_step = time.time()
        except Exception:                       # pragma: no cover - กันพังล้วน
            pass

    def done(self, key: str, status: str = OK, detail: str = "",
             **extra) -> None:
        """ขั้นนี้จบแล้ว — ``status`` บอกว่าจบแบบไหน."""
        try:
            ms = int((time.time() - self._t_step) * 1000)
            with _LOCK:
                _run, s = self._find(key)
                if s is None:
                    return
                s["status"] = status
                s["detail"] = detail
                s["ms"] = ms
                for k, v in (extra or {}).items():
                    s[k] = v
        except Exception:                       # pragma: no cover
            pass

    def note(self, key: str, text: str, limit: int = 40) -> None:
        """บันทึกรายละเอียดย่อยของขั้นนั้น (เช่น engine ที่ใช้ต่อโซน)."""
        try:
            with _LOCK:
                _run, s = self._find(key)
                if s is None:
                    return
                if len(s["notes"]) < limit:
                    s["notes"].append(str(text)[:160])
        except Exception:                       # pragma: no cover
            pass

    def skip(self, key: str, reason: str) -> None:
        """ขั้นนี้ไม่ได้ทำงาน — **ต้องบอกเหตุผลเสมอ**."""
        self.done(key, SKIP, reason)

    def finish(self, status: str = OK, detail: str = "") -> None:
        try:
            with _LOCK:
                run = _RUNS.get(self.rec_id)
                if not run:
                    return
                for s in run["steps"]:
                    if s["status"] == RUNNING:
                        s["status"] = status if status == FAIL else OK
                    elif s["status"] == PENDING:
                        s["status"] = FAIL if status == FAIL else SKIP
                        if not s["detail"]:
                            s["detail"] = ("ไม่ได้ทำงาน (การตรวจจบก่อน)"
                                           if status == FAIL else "")
                run["done"] = True
                run["status"] = status
                run["detail"] = detail
                run["elapsed_s"] = round(time.time() - self._t0, 2)
        except Exception:                       # pragma: no cover
            pass


class NullRun(Run):
    """ตัวแทนที่ไม่บันทึกอะไรเลย — ใช้เมื่อผู้เรียกไม่ต้องการความคืบหน้า
    (เทสต์เดิมและเส้นทางที่ไม่มี rec_id) ⇒ ``run_inspection`` ไม่ต้องเช็ค
    ``if progress:`` ทุกบรรทัด."""

    def __init__(self):                          # ไม่ลงทะเบียนใน _RUNS
        self._t0 = self._t_step = time.time()
        self.rec_id = ""

    def start(self, *a, **k): pass
    def done(self, *a, **k): pass
    def note(self, *a, **k): pass
    def skip(self, *a, **k): pass
    def finish(self, *a, **k): pass


def begin(rec_id: str, flags: Optional[dict] = None) -> Run:
    return Run(rec_id, flags)


def snapshot(rec_id: str) -> Optional[dict]:
    """สถานะปัจจุบันของการตรวจนั้น (คัดลอกออกมา ปลอดภัยต่อการแก้ไข)."""
    with _LOCK:
        run = _RUNS.get(str(rec_id))
        if not run:
            return None
        return {
            "id": run["id"],
            "done": bool(run["done"]),
            "status": run.get("status", ""),
            "detail": run.get("detail", ""),
            "elapsed_s": run.get("elapsed_s",
                                 round(time.time() - run["t0"], 2)),
            "flags": dict(run["flags"]),
            "steps": [dict(s, notes=list(s["notes"])) for s in run["steps"]],
        }


def clear(rec_id: str) -> None:
    with _LOCK:
        _RUNS.pop(str(rec_id), None)
