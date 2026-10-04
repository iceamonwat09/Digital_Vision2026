"""ที่เก็บงาน (job) ของ Artwork V2 — ``data/artwork_v2/jobs/<id>/``

ไฟล์ในโฟลเดอร์งาน:
    meta.json          ข้อมูลไฟล์ A/B + เจ้าของ
    a.<ext> b.<ext>    ไฟล์ต้นฉบับ
    preview_<s>_<p>.png ภาพตัวอย่างสำหรับวาดโซน
    run_<n>/           ผลการตรวจแต่ละรอบ (ภาพที่ส่ง · ผลดิบ · result.json · log.txt)
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import time
from typing import Optional

from . import config, imaging

_ID_RE = re.compile(r"^[0-9]{8}_[0-9]{6}_[0-9a-f]{6}$")
_RUN_RE = re.compile(r"^run_[0-9]{3}$")
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


def valid_id(job_id: str) -> bool:
    return bool(job_id) and bool(_ID_RE.match(job_id))


def job_dir(job_id: str) -> str:
    if not valid_id(job_id):
        raise ValueError("รหัสงานไม่ถูกต้อง")
    d = os.path.join(config.JOBS_DIR, job_id)
    if not os.path.isdir(d):
        raise FileNotFoundError("ไม่พบงาน %s" % job_id)
    return d


def _ext(name: str) -> str:
    ext = os.path.splitext(name or "")[1].lower()
    if ext not in imaging.PDF_EXT + imaging.IMAGE_EXT:
        raise ValueError("ชนิดไฟล์ไม่รองรับ (%s) — ใช้ PDF / JPG / PNG / WEBP / TIFF / BMP"
                         % (ext or "ไม่มีนามสกุล"))
    return ext


def _tmp(path: str) -> str:
    """ชื่อไฟล์ชั่วคราวไม่ซ้ำ — สองคำขอพร้อมกันเขียนไฟล์เดียวกันได้โดยไม่ทับกันกลางทาง"""
    return "%s.%s.tmp" % (path, secrets.token_hex(4))


def _write_json(path: str, data) -> None:
    tmp = _tmp(path)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def create(file_a: tuple, file_b: tuple, owner: Optional[dict] = None) -> dict:
    """``file_x`` = ``(ชื่อไฟล์, bytes)`` · ไฟล์เปิดไม่ได้ = ลบโฟลเดอร์ทิ้ง ไม่ค้างครึ่ง ๆ"""
    sides = {"a": file_a, "b": file_b}
    for s, (name, data) in sides.items():
        _ext(name)
        if not data:
            raise ValueError("ไฟล์ %s ว่างเปล่า" % s.upper())
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError("ไฟล์ %s ใหญ่เกิน %d MB" % (s.upper(), MAX_UPLOAD_BYTES // 2 ** 20))
    job_id = time.strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(3)
    d = os.path.join(config.JOBS_DIR, job_id)
    os.makedirs(d)
    try:
        meta = {"id": job_id, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "owner": owner or {}, "files": {}}
        for s, (name, data) in sides.items():
            ext = _ext(name)
            path = os.path.join(d, s + ext)
            with open(path, "wb") as f:
                f.write(data)
            try:
                src = imaging.Source(path)
            except Exception as e:                       # noqa: BLE001
                raise ValueError("เปิดไฟล์ %s ไม่ได้: %s" % (s.upper(), e))
            meta["files"][s] = dict(name=os.path.basename(name), ext=ext, bytes=len(data),
                                    sha1=imaging.sha1_bytes(data), **src.info())
        _write_json(os.path.join(d, "meta.json"), meta)
        return meta
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise


def meta(job_id: str) -> dict:
    return read_json(os.path.join(job_dir(job_id), "meta.json"))


def source(job_id: str, side: str) -> imaging.Source:
    if side not in ("a", "b"):
        raise ValueError("side ต้องเป็น a หรือ b")
    m = meta(job_id)
    return imaging.Source(os.path.join(job_dir(job_id), side + m["files"][side]["ext"]))


def preview_path(job_id: str, side: str, page: int) -> str:
    d = job_dir(job_id)
    page = int(page)
    path = os.path.join(d, "preview_%s_%d.png" % (side, page))
    if not os.path.isfile(path):
        img = source(job_id, side).preview(page)
        tmp = _tmp(path)
        try:
            with open(tmp, "wb") as f:
                f.write(imaging.encode_png(img))
            os.replace(tmp, path)
        except OSError:
            if os.path.exists(tmp):
                os.remove(tmp)
            if not os.path.isfile(path):         # อีกคำขอเขียนเสร็จก่อน = ใช้ไฟล์นั้น
                raise
    return path


def new_run_dir(job_id: str) -> str:
    d = job_dir(job_id)
    for n in range(1, 1000):
        rd = os.path.join(d, "run_%03d" % n)
        try:
            os.makedirs(rd)            # สร้างแบบอะตอม — กดตรวจซ้อนกันได้ไม่ชนกัน
        except FileExistsError:
            continue
        os.makedirs(os.path.join(rd, "img"))
        os.makedirs(os.path.join(rd, "raw"))
        return rd
    raise ValueError("งานนี้มีรอบตรวจครบ 999 รอบแล้ว — เริ่มงานใหม่")


def run_dir(job_id: str, run: str) -> str:
    if not _RUN_RE.match(run or ""):
        raise ValueError("รหัสรอบไม่ถูกต้อง")
    rd = os.path.join(job_dir(job_id), run)
    if not os.path.isdir(rd):
        raise FileNotFoundError("ไม่พบรอบ %s" % run)
    return rd


def finished_runs(d: str) -> list:
    """รอบที่ตรวจเสร็จ (มี ``result.json``) เรียงตามลำดับ — รอบที่ล้มกลางทางไม่ถูกนับ"""
    try:
        names = os.listdir(d)
    except OSError:
        return []
    return sorted(x for x in names if _RUN_RE.match(x)
                  and os.path.isfile(os.path.join(d, x, "result.json")))


def recent(limit: int = 20, can_view=None) -> list:
    out = []
    try:
        names = sorted(os.listdir(config.JOBS_DIR), reverse=True)
    except OSError:
        return out
    for n in names:
        if not valid_id(n):
            continue
        try:
            m = read_json(os.path.join(config.JOBS_DIR, n, "meta.json"))
        except (OSError, ValueError):
            continue
        if not isinstance(m, dict):
            continue
        if can_view is not None and not can_view(m):
            continue
        try:
            row = {"id": n, "created": m.get("created"),
                   "a": m["files"]["a"]["name"], "b": m["files"]["b"]["name"],
                   "owner": (m.get("owner") or {}).get("username", "")}
        except (KeyError, TypeError, AttributeError):
            continue                               # meta.json ผิดรูป ⇒ ข้ามงานนั้น ไม่ล้มทั้งรายการ
        runs = finished_runs(os.path.join(config.JOBS_DIR, n))
        last = None
        if runs:
            try:
                r = read_json(os.path.join(config.JOBS_DIR, n, runs[-1], "result.json"))
                last = {"run": runs[-1], "verdict": r.get("verdict")}
            except (OSError, ValueError):
                last = {"run": runs[-1], "verdict": None}
        row["last"] = last
        out.append(row)
        if len(out) >= limit:
            break
    return out


write_json = _write_json
