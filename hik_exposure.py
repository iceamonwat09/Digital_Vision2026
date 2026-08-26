# -*- coding: utf-8 -*-
"""
hik_exposure.py — "ไม่มีไฟเพิ่ม จะกด exposure ลงได้ต่ำสุดแค่ไหน ก่อนโมเดลจะเชื่อไม่ได้"

โจทย์ที่ไฟล์นี้ตอบ
------------------
ความเบลอของภาพบนไลน์ขึ้นกับ **exposure อย่างเดียว** (``เบลอ = ความเร็ว × exposure``)
⇒ อยากได้ภาพไม่เบลอที่ 450 ใบ/นาที ต้องกด exposure ลง ⇒ ภาพมืดลงตามสัดส่วน.
เมื่อ **ไม่มีไฟเพิ่ม** ทางเดียวที่เหลือคือ **ดัน gain ขึ้นชดเชย** ซึ่งแลกมาด้วย
สัญญาณรบกวน. คำถามจริงจึงไม่ใช่ "กดลงได้ไหม" (กดได้อยู่แล้ว) แต่คือ
**"กดลงถึงเท่าไรแล้วโมเดลยังเชื่อถือได้"** ซึ่งเดาไม่ได้ ต้องวัด

⚠️ ต้องวัด **สองทิศทาง** ไม่ใช่ทางเดียว — สัญญาณรบกวนทำร้ายงาน QC ได้ 2 แบบ:
    role="ng"  วางกระป๋องที่มีรอยบุบจริง → ถามว่า **ยังเจอไหม** (recall)
    role="ok"  วางกระป๋องดี             → ถามว่า **เจอของที่ไม่มีไหม** (NG ปลอม)
วัดแต่ทางแรกแล้วสรุปว่า "ใช้ได้" คือคำตอบที่ผิดแบบมั่นใจ — บทเรียนเดียวกับ
`blur_tolerance.py` ที่วัดแค่ "ยังเจอไหม" แล้วยังปิดช่อง "เจอของที่ไม่มี" ไม่ได้

สิ่งที่ **ไม่** ทำโดยตั้งใจ
-------------------------
* ไม่ import Flask / ultralytics — รับ ``detect_fn`` เข้ามา จึงเทสต์ด้วยของปลอมได้
* ไม่แตะการนับ / DB / verdict ของโหมดสด (ผู้เรียกเป็นคนหยุดการตรวจสดให้)
* คืนค่าเดิมของกล้องทุกตัวใน ``finally`` เสมอ — รวมถึงตอนถูกยกเลิกกลางคัน
"""

import json
import math
import os
import time

import cv2
import numpy as np

from logger import setup_logger

logger = setup_logger(__name__)

try:
    import config
except Exception:                                        # pragma: no cover
    config = None


def _cfg(name, default=None):
    return getattr(config, name, default) if config is not None else default


# ความสว่างเฉลี่ยที่ถือว่า "พอใช้งาน" — **ต้องตรงกับ hik_burst.TARGET_MEAN**
# และ diagnose_hikrobot.TARGET_MEAN (มีเทสต์เทียบข้ามไฟล์)
TARGET_MEAN = 80.0

# ยอมให้ห่างจากเป้าได้กี่ส่วน ก่อนเลิกไล่ gain (ไล่เกินนี้ไม่ได้ข้อมูลเพิ่ม)
MEAN_TOLERANCE = 0.12
GAIN_STEPS = 3                     # จำนวนรอบที่ไล่ gain ต่อ exposure หนึ่งค่า

# เฟรมที่ต้องทิ้งหลังเปลี่ยนค่ากล้อง — เฟรมที่ค้างอยู่ในท่อยังเป็นของค่าเดิม
SETTLE_FRAMES = 3

# ── ด่าน "ฉากนิ่งจริงไหม" — ตัดสินด้วยสัดส่วนที่ **ไม่ขึ้นกับความสว่าง/คอนทราสต์** ──
#
# หลักการ: เบลอภาพด้วยเคอร์เนล k×k แล้วดูว่า σ ข้ามเฟรม "เหลือเท่าไร"
#   · สัญญาณรบกวน = ความถี่สูงไม่มีโครงสร้าง ⇒ การเบลอลดมันลง **k เท่า**
#     (ทฤษฎี: 1/9 = 0.111 · วัดจริงบนภาพจำลอง: 0.114-0.116 ทุกระดับ gain)
#   · วัตถุที่เคลื่อนที่ = โครงสร้าง ⇒ **รอดจากการเบลอ** ⇒ สัดส่วนเข้าใกล้ 1
#     (วัดจริง: 0.51-0.93 ตั้งแต่ขยับ 5 px/เฟรม ขึ้นไป)
#
# ⚠️ **ทำไมต้องใช้สัดส่วน ไม่ใช่ค่าสัมบูรณ์:** เกณฑ์แบบ "ต่างกันเกิน N ระดับสี"
# จะกล่าวหาผิดทันทีที่ gain สูง — ที่ 18-21 dB สัญญาณรบกวนเองมี σ ≈ 8-12 ระดับสี
# ⇒ ระบบจะบอกว่า "ฉากขยับ" แล้วไม่ยอมรายงาน noise **ในขั้นที่สำคัญที่สุดพอดี**
# สัดส่วนนี้คงที่ 0.114 ตั้งแต่ gain 0 ถึง 21 dB (วัดแล้ว) จึงไม่มีปัญหานั้น
STATIC_STRUCTURE_MAX = 0.30        # ห่างจากทั้งสองฝั่งประมาณ 2.6 เท่า
# ⚠️ ขีดจำกัดที่วัดได้: ที่ gain 21 dB (สัญญาณรบกวนหนัก) ด่านนี้จับการเคลื่อนที่
# ได้ตั้งแต่ ~8 px/เฟรม ขึ้นไป · ที่ gain 0 จับได้ตั้งแต่ ~3 px/เฟรม.
# การขยับที่เล็กกว่านั้นแยกจากสัญญาณรบกวนไม่ออก — แต่ก็ทำให้ σ เพี้ยนน้อยมาก
# เช่นกัน จึงยอมรับได้ (มีเทสต์ล็อกขีดนี้ไว้ให้รู้ว่ามันอยู่ตรงไหน)
STATIC_BLUR = 9                    # เคอร์เนลเบลอ — ตัวหารของสัดส่วนข้างบน
ANALYSIS_SCALE = 0.25

# ต่ำกว่านี้ = ภาพมืดจนไม่มีข้อมูลให้ตัดสินอะไรได้เลย (ฝาเลนส์ปิด / รูรับแสงปิดสุด)
DARK_MEAN = 2.0
CLIP_LEVEL = 250                   # พิกเซลที่ถือว่า "ล้น"
DARK_LEVEL = 8                     # พิกเซลที่ถือว่า "จมมืด"

ROLES = ("ng", "ok")

# จำนวนกรอบสูงสุดที่เก็บไว้ต่อขั้น (พอสำหรับเทียบตำแหน่ง ไม่บวม JSON)
MAX_BOXES = 10
# กรอบสองอันถือว่า "ที่เดียวกัน" เมื่อ IoU เกินนี้ — หลวมโดยตั้งใจ เพราะกรอบ
# ของภาพที่ noise สูงจะขยับ/ขยายเป็นธรรมชาติ (เกณฑ์เดียวกับ blur_tolerance)
BOX_MATCH_IOU = 0.20


# ────────────────────────────────────────────────────────── โฟลเดอร์/ไฟล์
def root_dir():
    return _cfg("HIK_EXPOSURE_DIR", os.path.join("data", "hik_exposure"))


def session_dir(name, must_exist=True):
    """โฟลเดอร์ของชุดหนึ่ง — กันการหลุดออกนอก root (ชื่อมาจาก HTTP)."""
    root = os.path.abspath(root_dir())
    path = os.path.abspath(os.path.join(root, name or ""))
    if not path.startswith(root + os.sep):
        raise ValueError("ชื่อชุดไม่ถูกต้อง")
    if must_exist and not os.path.isdir(path):
        raise ValueError("ไม่พบชุดนี้")
    return path


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    if os.path.exists(path):
        os.remove(path)
    os.rename(tmp, path)


def _read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_session(name):
    return _read_json(os.path.join(session_dir(name), "ladder.json"))


def list_sessions():
    root = root_dir()
    if not os.path.isdir(root):
        return []
    out = []
    for entry in sorted(os.listdir(root), reverse=True):
        data = _read_json(os.path.join(root, entry, "ladder.json"))
        if not data:
            continue
        out.append({"name": entry, "role": data.get("role"),
                    "created": data.get("created"),
                    "steps": len(data.get("rows") or []),
                    "verdict": (data.get("summary") or {}).get("headline")})
    return out


def delete_session(name):
    import shutil
    shutil.rmtree(session_dir(name), ignore_errors=True)
    return True


# ────────────────────────────────────────────────────────── การวัดต่อขั้น
def gain_for(mean, gain_db, target=TARGET_MEAN):
    """
    gain (dB) ที่ควรตั้งเพื่อให้ความสว่างเฉลี่ยไปที่ ``target``.

    เซนเซอร์เป็นเชิงเส้นก่อนแกมมา ⇒ สว่างขึ้นเป็นเท่า = gain (เท่า) เดียวกัน
    และ dB ของกล้องอุตสาหกรรมเป็น **20·log10(เท่า)** ตามมาตรฐาน.
    คืน ``None`` เมื่อคำนวณไม่ได้ (ภาพมืดสนิทจนหารไม่ได้) — ไม่เดา
    """
    if mean is None or mean <= 0.5:
        return None
    ratio = float(target) / float(mean)
    if ratio <= 0:
        return None
    return float(gain_db or 0.0) + 20.0 * math.log10(ratio)


def _gray(frame):
    if frame is None:
        return None
    if frame.ndim == 3:
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return frame


def _sharpness(gray):
    """Tenengrad — พลังงานขอบ. ใช้เทียบ *ภายในชุดเดียวกัน* เท่านั้น."""
    small = cv2.resize(gray, None, fx=ANALYSIS_SCALE, fy=ANALYSIS_SCALE,
                       interpolation=cv2.INTER_AREA)
    gx = cv2.Sobel(small, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(small, cv2.CV_32F, 0, 1, ksize=3)
    return float(np.mean(gx * gx + gy * gy))


def frame_stats(frames):
    """
    ตัวเลขของภาพชุดหนึ่งที่ถ่ายด้วยค่าเดียวกัน.

    **สัญญาณรบกวนวัดตามเวลา (temporal)** = ส่วนเบี่ยงเบนของพิกเซลเดียวกัน
    ข้ามเฟรม ซึ่งเป็นนิยามตรงตัวของ noise และแยกออกจาก "ลายของวัตถุ" ได้
    (การวัด σ ในเฟรมเดียวจะนับลายกระป๋องเป็น noise ไปด้วย = ตัวเลขที่ผิด)

    ⚠️ ใช้ได้เฉพาะเมื่อ **ฉากนิ่ง** — ถ้าขยับ σ จะกลายเป็นการวัดการเคลื่อนที่
    ⇒ คืน ``noise = None`` พร้อม ``moved = True`` แทนที่จะรายงานตัวเลขที่ผิด
    """
    grays = [_gray(f) for f in frames if f is not None]
    if not grays:
        return {}
    stack = np.stack([g.astype(np.float32) for g in grays])
    mean = float(stack.mean())
    first = grays[0]
    out = {
        "frames": len(grays),
        "mean": round(mean, 1),
        "clip_pct": round(float((first >= CLIP_LEVEL).mean()) * 100.0, 2),
        "dark_pct": round(float((first <= DARK_LEVEL).mean()) * 100.0, 2),
        "sharpness": round(_sharpness(first), 1),
    }
    if len(grays) < 3:
        out["noise"] = None
        out["moved"] = None
        return out

    small = np.stack([
        cv2.resize(g, None, fx=ANALYSIS_SCALE, fy=ANALYSIS_SCALE,
                   interpolation=cv2.INTER_AREA).astype(np.float32)
        for g in grays])
    blurred = np.stack([cv2.blur(f, (STATIC_BLUR, STATIC_BLUR)) for f in small])
    sig_raw = float(np.mean(small.std(axis=0)))
    sig_blur = float(np.mean(blurred.std(axis=0)))
    if sig_raw < 0.05:
        out["structure_ratio"] = None      # ภาพนิ่งสนิทไม่มีอะไรเปลี่ยนเลย
        out["moved"] = False
    else:
        ratio = sig_blur / sig_raw
        out["structure_ratio"] = round(ratio, 3)
        if ratio > STATIC_STRUCTURE_MAX:
            out["moved"] = True
            out["noise"] = None            # วัดไม่ได้ ≠ ไม่มี — ไม่เดา
            return out
        out["moved"] = False
    noise = float(np.mean(stack.std(axis=0)))
    out["noise"] = round(noise, 2)
    out["snr_db"] = round(20.0 * math.log10(mean / noise), 1) if noise > 0.01 else None
    return out


# ────────────────────────────────────────────────────────────── ตัวไล่ค่า
def _read_current(cam):
    """ค่าที่ต้องคืนกลับตอนจบ + ช่วงของ gain — อ่านครั้งเดียว."""
    params = cam.get_params() or {}

    def entry(key):
        e = params.get(key)
        return e if isinstance(e, dict) and e.get("supported") else None

    exp, gain = entry("exposure_us"), entry("gain_db")
    return {
        "exposure_us": (exp or {}).get("value"),
        "gain_db": (gain or {}).get("value"),
        "exposure_auto": (params.get("exposure_auto") or {}).get("symbolic"),
        "gain_auto": (params.get("gain_auto") or {}).get("symbolic"),
        "gain_min": (gain or {}).get("min", 0.0),
        "gain_max": (gain or {}).get("max", 0.0),
        "exposure_min": (exp or {}).get("min"),
        "exposure_max": (exp or {}).get("max"),
        "size": "%sx%s" % ((params.get("width") or {}).get("value"),
                           (params.get("height") or {}).get("value")),
    }


def _grab(cam, n, settle=SETTLE_FRAMES, timeout=3.0):
    """ทิ้ง ``settle`` เฟรมแรก (ยังเป็นของค่าเดิมที่ค้างในท่อ) แล้วเก็บ ``n`` เฟรม."""
    for _ in range(max(0, settle)):
        cam.snap_full(timeout=timeout)
    out = []
    for _ in range(max(1, n)):
        f = cam.snap_full(timeout=timeout)
        if f is not None:
            out.append(f)
    return out


def _tune_gain(cam, base, target, frames, settle, timeout):
    """
    ไล่ gain ให้ความสว่างเข้าใกล้ ``target`` — **วัดจริงทุกรอบ ไม่เชื่อสูตรรอบเดียว**.

    สมมติฐาน "สว่างเป็นสัดส่วนตรงกับ gain" ถูกโดยประมาณ แต่ถ้าเซนเซอร์มีแกมมา
    หรือชนเพดาน/พื้น สูตรจะพลาด ⇒ วัดซ้ำแล้วปรับ สูงสุด ``GAIN_STEPS`` รอบ
    คืน ``(กรอบภาพล่าสุด, gain ที่ตั้งได้จริง, ชนเพดานไหม)``
    """
    gain = float(base.get("gain_db") or 0.0)
    gmin = float(base.get("gain_min") or 0.0)
    gmax = float(base.get("gain_max") or 0.0) or 24.0
    shots, capped = [], False
    for _ in range(GAIN_STEPS):
        shots = _grab(cam, frames, settle, timeout)
        if not shots:
            return [], gain, capped
        mean = float(np.mean([_gray(f).mean() for f in shots]))
        if abs(mean - target) <= target * MEAN_TOLERANCE:
            break
        want = gain_for(mean, gain, target)
        if want is None:
            break
        new = min(gmax, max(gmin, want))
        capped = want > gmax + 1e-6
        if abs(new - gain) < 0.05:          # ขยับไม่ได้แล้ว (ชนเพดาน/พื้น)
            break
        cam.set_params({"gain_db": new})
        gain = new
    return shots, gain, capped


def run_ladder(cam, detect_fn, exposures, role="ng", frames=5,
               target_mean=TARGET_MEAN, settle=SETTLE_FRAMES, timeout=3.0,
               save_dir=None, annotate_fn=None, job=None, mm_per_px=None,
               line_speed_px_s=None):
    """
    ไล่ ``exposures`` (µs, เรียงจากสว่างสุดไปมืดสุด) แล้วคืน ``rows``.

    ``detect_fn(frame)`` ต้องคืน **เฉพาะกล่องตำหนิ** (ผู้เรียกกรองคลาส can/good
    ออกไปแล้ว) — โมดูลนี้จึงไม่ต้องรู้จักชื่อคลาสของโหมดใดเลย
    """
    base = _read_current(cam)
    exposures = [float(e) for e in exposures if e]
    exposures.sort(reverse=True)            # สว่าง → มืด: หาจุดที่ "เริ่มพัง" ตามลำดับ
    rows = []
    if job is not None:
        job.total = len(exposures)
    try:
        cam.set_params({"exposure_auto": "Off", "gain_auto": "Off"})
        for us in exposures:
            if job is not None and job.cancelled:
                break
            row = {"exposure_us": round(us, 1)}
            res = cam.set_params({"exposure_us": us}) or {}
            if "exposure_us" in (res.get("failed") or {}):
                row["error"] = (res["failed"]["exposure_us"].get("message")
                                or "ตั้ง exposure ไม่สำเร็จ (นอกช่วงที่กล้องรับ)")
                rows.append(row)
                if job is not None:
                    job.done += 1
                continue

            shots, gain, capped = _tune_gain(cam, base, target_mean, frames, settle, timeout)
            row["gain_db"] = round(gain, 2)
            row["gain_capped"] = bool(capped)
            row["gain_max"] = round(float(base.get("gain_max") or 0.0), 2)
            if not shots:
                row["error"] = "ไม่ได้ภาพจากกล้องภายในเวลาที่รอ"
                rows.append(row)
                if job is not None:
                    job.done += 1
                continue

            row.update(frame_stats(shots))
            # ⚠️ แยก "มืดจนไม่มีข้อมูล" ออกจาก "มืดแต่ยังพอเห็น" — สองอย่างนี้
            # ทำให้สรุปคนละเรื่องกันโดยสิ้นเชิง (ดู `summarize`)
            row["dark"] = bool((row.get("mean") or 0.0) <= DARK_MEAN)

            # ── รันโมเดลทุกเฟรม ไม่ใช่เฟรมเดียว ────────────────────────
            # ที่ noise สูง ผลตรวจจะ **กะพริบ** (เจอบ้างไม่เจอบ้าง) ซึ่งมองไม่เห็น
            # เลยถ้าดูเฟรมเดียว. "เจอ 5/5" กับ "เจอ 1/5" คือคนละคำตอบ
            hits, confs, counts = 0, [], []
            best_shot, best_dets = shots[0], []
            for i, f in enumerate(shots):
                dets = detect_fn(f) or []
                counts.append(len(dets))
                if dets:
                    hits += 1
                    confs.append(max(d["confidence"] for d in dets))
                    if len(dets) >= len(best_dets):
                        best_shot, best_dets = f, dets
            row["frames_with_defect"] = hits
            row["defect_rate"] = round(hits / float(len(shots)), 3)
            # ⚠️ เก็บ **ตำแหน่ง** ของกรอบไว้ด้วย — ไม่งั้น "เจอรอยบุบ 5/5" จะ
            # แยกไม่ออกระหว่าง *เจอรอยบุบเดิม* กับ *เจอของอย่างอื่นคนละที่*
            # (เกิดจริงบนสถานี 26 ส.ค.: กรอบที่ 512 µs กับ 350 µs อยู่คนละจุด
            #  = ลายเซ็นของการตรวจที่ขับด้วยสัญญาณรบกวน ไม่ใช่รอยบุบจริง)
            row["boxes"] = _boxes_of(best_dets)
            row["defects_max"] = max(counts) if counts else 0
            row["conf_max"] = round(max(confs), 3) if confs else None
            row["conf_min"] = round(min(confs), 3) if confs else None

            if line_speed_px_s:
                row["blur_at_line_px"] = round(float(line_speed_px_s) * us / 1e6, 2)
            if mm_per_px and line_speed_px_s:
                row["blur_at_line_mm"] = round(row["blur_at_line_px"] * float(mm_per_px), 3)

            if save_dir:
                try:
                    os.makedirs(save_dir, exist_ok=True)
                    img = annotate_fn(best_shot, best_dets) if annotate_fn else best_shot
                    row["image"] = "exp_%06dus.jpg" % int(us)
                    cv2.imwrite(os.path.join(save_dir, row["image"]), img,
                                [int(cv2.IMWRITE_JPEG_QUALITY), 92])
                except Exception as e:                    # pragma: no cover
                    logger.warning("[hik-exp] เขียนภาพไม่สำเร็จ: %s", e)
            rows.append(row)
            if job is not None:
                job.done += 1
    finally:
        # ⚠️ คืนค่าเดิม **เสมอ** — รวมถึงตอนถูกยกเลิก/เกิด exception กลางคัน.
        # ถ้าไม่คืน กล้องจะค้างอยู่ที่ exposure ของขั้นสุดท้าย (มืดสนิท + gain สูง)
        # แล้วผลตรวจสดหลังจากนั้นจะผิดโดยที่ผู้ใช้ไม่รู้ว่าทำไม
        restore = {}
        if base.get("exposure_us") is not None:
            restore["exposure_us"] = base["exposure_us"]
        if base.get("gain_db") is not None:
            restore["gain_db"] = base["gain_db"]
        if base.get("exposure_auto"):
            restore["exposure_auto"] = base["exposure_auto"]
        if base.get("gain_auto"):
            restore["gain_auto"] = base["gain_auto"]
        try:
            cam.set_params(restore)
            logger.info("[hik-exp] คืนค่ากล้องเดิมแล้ว: %s", restore)
        except Exception as e:                            # pragma: no cover
            logger.error("[hik-exp] คืนค่ากล้องไม่สำเร็จ: %s", e)
    return rows, base


def _boxes_of(dets):
    """ดึง bbox ที่ใช้เทียบตำแหน่งได้ — ตัวที่ไม่มี/รูปแบบไม่ถูกให้ข้ามไปเงียบ ๆ.

    ``detect_fn`` ถูกนิยามไว้แค่ว่า "คืนกล่องตำหนิ" ⇒ ต้องไม่พังเมื่อผู้เรียก
    ส่ง dict ที่ไม่มี ``bbox`` มา (การเทียบตำแหน่งเป็นของแถม ไม่ใช่ของบังคับ)
    """
    out = []
    for d in (dets or [])[:MAX_BOXES]:
        box = d.get("bbox") if isinstance(d, dict) else None
        try:
            x0, y0, x1, y1 = [int(v) for v in box]
        except (TypeError, ValueError):
            continue
        out.append([x0, y0, x1, y1])
    return out


def _iou(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    iw = max(0, min(ax1, bx1) - max(ax0, bx0))
    ih = max(0, min(ay1, by1) - max(ay0, by0))
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / union if union > 0 else 0.0


def mark_box_agreement(rows):
    """ทำเครื่องหมายว่ากรอบของแต่ละขั้น **อยู่ที่เดียวกับขั้นอ้างอิง** หรือไม่.

    ⚠️ ``defect_rate == 1.0`` บอกแค่ว่า *มีกรอบ* ไม่ได้บอกว่า **กรอบอยู่ตรงไหน**
    ⇒ ขั้นที่โมเดลไปเจอเงา/ขอบคนละจุดจะถูกนับว่า "ผ่าน" เหมือนกัน ทั้งที่มัน
    ไม่ได้เห็นรอยบุบเลย (เกิดจริงบนสถานี 26 ส.ค.).

    ขั้นอ้างอิง = **exposure ยาวที่สุดที่มีกรอบและภาพไม่มืด** (สว่างที่สุด =
    เชื่อถือได้ที่สุด). ตั้งค่า ``boxes_match``:
        True  — มีกรอบอย่างน้อย 1 อันทับกับกรอบของขั้นอ้างอิง
        False — มีกรอบ แต่ไม่มีอันไหนทับเลย (น่าสงสัยว่าเจอคนละของ)
        None  — ไม่มีข้อมูลพอจะตัดสิน (ไม่มีกรอบ / ไม่มีขั้นอ้างอิง)
    """
    ref = None
    for r in sorted(rows, key=lambda x: -(x.get("exposure_us") or 0)):
        if r.get("error") or r.get("dark"):
            continue
        if r.get("boxes"):
            ref = r
            break
    for r in rows:
        boxes = r.get("boxes") or []
        if ref is None or not boxes:
            r["boxes_match"] = None
            continue
        if r is ref:
            r["boxes_match"] = True
            continue
        r["boxes_match"] = any(_iou(b, rb) >= BOX_MATCH_IOU
                               for b in boxes for rb in (ref.get("boxes") or []))
    return rows


def summarize(rows, role, line_speed_px_s=None, mm_per_px=None,
              blur_target_px=None):
    """
    หา **exposure ต่ำสุดที่ยังเชื่อถือได้** แล้วแปลเป็นคำตอบเรื่องความเบลอที่ไลน์.

    ⚠️ เดินจาก exposure **ยาวสุด (สว่างสุด) ลงไป** แล้วหยุดที่ขั้นแรกที่ตก —
    ไม่ใช่ "หาขั้นต่ำสุดที่ยังผ่าน". ขั้นที่ผ่านแบบฟลุ๊คหลังจากพังไปแล้วจะทำให้
    ได้คำตอบที่ดีเกินจริง (บทเรียนเดียวกับ `blur_tolerance.py` ข้อ 3)
    """
    usable = [r for r in rows if not r.get("error")]
    out = {"role": role, "steps": len(rows), "steps_ok": len(usable)}
    if not usable:
        out["headline"] = "ไม่มีขั้นไหนวัดได้เลย — ดูข้อความ error ในตาราง"
        return out

    mark_box_agreement(rows)
    ordered = sorted(usable, key=lambda r: -r["exposure_us"])
    if role == "ng":
        # ``boxes_match is False`` = มีหลักฐานบวกว่ากรอบไปอยู่คนละที่ ⇒ ไม่ผ่าน
        # ``None`` = ไม่มีข้อมูลอ้างอิง ⇒ ไม่ตัดสิน (ไม่ใช่เหตุให้ตก)
        passed = lambda r: (r.get("defect_rate") == 1.0
                            and not r.get("gain_capped")
                            and r.get("boxes_match") is not False)
        crit = ("เจอรอยบุบครบทุกเฟรม · กรอบอยู่ที่เดิม · gain ยังไม่ชนเพดาน")
    else:
        passed = lambda r: r.get("frames_with_defect") == 0 and not r.get("gain_capped")
        crit = "ไม่มี NG ปลอมเลยสักเฟรม และ gain ยังไม่ชนเพดาน"
    out["criterion"] = crit

    # ── ข้อ ⑤: ฉากไม่นิ่ง = เทียบข้ามขั้นไม่ยุติธรรม ────────────────────
    # เดิมเว้นแค่ช่อง noise ไว้ แล้วปล่อยให้อ่านตารางเหมือนปกติ ⇒ ผู้ใช้สรุปจาก
    # ข้อมูลที่ฉากเปลี่ยนไปมาโดยไม่รู้ตัว (เกิดจริง: 3 ใน 6 ขั้นขยับ แล้วผลตรวจ
    # กระโดด 0/5 → 3/5 → 2/5 → 0/5 ซึ่งอธิบายด้วยการขยับได้ทั้งหมด)
    moved = [r for r in usable if r.get("moved")]
    if moved:
        out["moved_steps"] = len(moved)
        out["warn_moved"] = (
            "ฉากไม่นิ่ง %d ใน %d ขั้น ⇒ **การเทียบข้ามขั้นของรอบนี้ไม่ยุติธรรม** "
            "— มุมที่แสงตกกระทบเปลี่ยนไปด้วย ให้ล็อกชิ้นงานไม่ให้ขยับแล้ววัดใหม่"
            % (len(moved), len(usable)))

    # ── ข้อ ⑥: ลายเซ็นของ "เลือกด้านผิด" ────────────────────────────────
    # ด้าน NG ที่ไม่เจออะไรเลยสักขั้น = หน้าตาของกระป๋องดีเป๊ะ ⇒ ต้องเดาให้ถูก
    # ก่อนส่งผู้ใช้ไปไล่โฟกัส/โมเดล (ซึ่งเป็นการแก้ของที่ไม่ได้พัง)
    if role == "ng" and all((r.get("frames_with_defect") or 0) == 0 for r in usable):
        out["maybe_wrong_role"] = True
        out["warn_role"] = (
            "ไม่เจอตำหนิเลยสักขั้น — นี่คือลายเซ็นของ **กระป๋องดี** "
            "ถ้าใบที่วางอยู่ไม่ได้บุบ ให้เปลี่ยนด้านของชุดนี้เป็น \"กระป๋องดี\" "
            "(ไม่ต้องถ่ายใหม่) แล้วผลจะกลายเป็นคำตอบด้าน NG ปลอมทันที")

    # ⚠️ ทุกขั้นมืดสนิท = ไม่มีข้อมูลให้ตัดสิน ⇒ **ห้ามไปโทษโมเดล/การวางกระป๋อง**
    # (ข้อความนั้นจะส่งผู้ใช้ไปแก้ของที่ไม่ได้พัง — กฎเหล็กข้อ 2)
    if all(r.get("dark") for r in usable):
        out["limit_us"] = None
        out["all_dark"] = True
        out["headline"] = ("ทุกขั้นได้ภาพ**มืดสนิท** (สว่าง ≤ %.0f/255) ⇒ ยังไม่มีข้อมูล"
                           "ให้ตัดสินอะไรได้เลย — เปิดฝาเลนส์/รูรับแสง แล้ววัดใหม่"
                           % DARK_MEAN)
        return out

    if not passed(ordered[0]):
        out["limit_us"] = None
        if out.get("maybe_wrong_role"):
            # ⚠️ เดาสาเหตุที่ **น่าจะเป็นที่สุด** ก่อน — การส่งผู้ใช้ไปไล่โฟกัส/
            # โมเดลทั้งที่แค่เลือกด้านผิด คือการแก้ของที่ไม่ได้พัง
            out["headline"] = (
                "ไม่เจอตำหนิเลยสักขั้น แม้ที่ exposure ยาวที่สุด (%.0f µs) ⇒ "
                "**น่าจะเลือกด้านผิด** — ถ้าใบที่วางอยู่เป็นกระป๋องดี ให้กด "
                "\"เปลี่ยนเป็นกระป๋องดี\" (ไม่ต้องถ่ายใหม่)"
                % ordered[0]["exposure_us"])
        else:
            out["headline"] = ("แม้แต่ exposure ยาวที่สุด (%.0f µs) ก็ยังไม่ผ่านเกณฑ์ "
                               "⇒ ปัญหาไม่ได้อยู่ที่ความสว่าง — ตรวจการวางกระป๋อง/โฟกัส/"
                               "โมเดล ก่อนสรุปเรื่อง exposure" % ordered[0]["exposure_us"])
        return out

    limit = ordered[0]
    for r in ordered:
        if not passed(r):
            break
        limit = r
    out["limit_us"] = limit["exposure_us"]
    out["limit_gain_db"] = limit.get("gain_db")
    out["limit_noise"] = limit.get("noise")
    out["limit_snr_db"] = limit.get("snr_db")
    lowest = ordered[-1]["exposure_us"]
    out["ladder_bottom_us"] = lowest
    out["limit_is_bottom"] = bool(abs(limit["exposure_us"] - lowest) < 1e-6)

    if line_speed_px_s:
        blur = float(line_speed_px_s) * limit["exposure_us"] / 1e6
        out["blur_at_line_px"] = round(blur, 2)
        out["line_speed_px_s"] = round(float(line_speed_px_s), 1)
        if mm_per_px:
            out["blur_at_line_mm"] = round(blur * float(mm_per_px), 3)
        target = float(blur_target_px or 0)
        if target > 0:
            out["blur_target_px"] = target
            out["meets_target"] = bool(blur <= target)
            out["headline"] = (
                "%s exposure ต่ำสุดที่ผ่านเกณฑ์ = %.0f µs (gain %.1f dB) "
                "⇒ เบลอที่ความเร็วไลน์ %.2f px %s เป้า %.0f px"
                % ("✅" if blur <= target else "⚠️", limit["exposure_us"],
                   limit.get("gain_db") or 0.0, blur,
                   "≤" if blur <= target else ">", target))
        else:
            out["headline"] = ("exposure ต่ำสุดที่ผ่านเกณฑ์ = %.0f µs ⇒ เบลอที่ไลน์ %.2f px"
                               % (limit["exposure_us"], blur))
    else:
        out["headline"] = ("exposure ต่ำสุดที่ผ่านเกณฑ์ = %.0f µs (gain %.1f dB)"
                           % (limit["exposure_us"], limit.get("gain_db") or 0.0))

    if out.get("limit_is_bottom"):
        out["note_bottom"] = ("ผ่านทุกขั้นจนถึงค่าต่ำสุดที่ทดสอบ ⇒ **ยังไม่เจอขีดจำกัด** "
                              "— เติมค่าที่ต่ำกว่านี้เข้าไปในลิสต์แล้วรันซ้ำ")
    return out


def resummarize(data, role, line_speed_px_s=None, mm_per_px=None,
                blur_target_px=None):
    """เปลี่ยน "ด้าน" ของชุดที่วัดไว้แล้ว **โดยไม่ต้องถ่ายใหม่**.

    ทุกอย่างที่ ``summarize()`` ต้องใช้อยู่ใน ``rows`` แล้ว (``defect_rate`` ·
    ``frames_with_defect`` · ``gain_capped`` · ``boxes``) ⇒ การเลือกด้านผิด
    ตอนกดปุ่มไม่ควรทำให้ต้องเสียเวลาวัดใหม่ 3 นาทีทั้งที่ข้อมูลถูกต้องอยู่แล้ว.

    คืน ``data`` ที่แก้แล้ว (แก้ในตัว) — ผู้เรียกเป็นคนบันทึกลงดิสก์
    """
    if role not in ROLES:
        raise ValueError("ด้านต้องเป็น ng หรือ ok")
    rows = data.get("rows") or []
    data["role"] = role
    data["summary"] = summarize(rows, role, line_speed_px_s=line_speed_px_s,
                                mm_per_px=mm_per_px, blur_target_px=blur_target_px)
    return data


def combine(ng_summary, ok_summary):
    """
    คำตอบสุดท้ายต้องใช้ **ทั้งสองด้าน** — exposure ที่ใช้ได้จริงคือค่าที่
    *ยังเจอรอยบุบจริง* และ *ยังไม่สร้าง NG ปลอม* พร้อมกัน

    คืน ``None`` เมื่อยังมีด้านใดด้านหนึ่งขาด — ไม่สรุปจากด้านเดียว (กฎเหล็กข้อ 2)
    """
    if not ng_summary or not ok_summary:
        return None
    a, b = ng_summary.get("limit_us"), ok_summary.get("limit_us")
    if a is None or b is None:
        return {"ok": False,
                "headline": "มีด้านที่ยังไม่ผ่านเกณฑ์เลย — ยังสรุปไม่ได้"}
    limit = max(a, b)          # ยิ่ง exposure ยาว ยิ่งปลอดภัย ⇒ เอาตัวที่เข้มกว่า
    out = {"ok": True, "limit_us": limit,
           "limited_by": "กระป๋อง NG (recall)" if a >= b else "กระป๋องดี (NG ปลอม)"}
    line = ng_summary.get("line_speed_px_s") or ok_summary.get("line_speed_px_s")
    if line:
        out["blur_at_line_px"] = round(float(line) * limit / 1e6, 2)
    out["headline"] = ("exposure ที่ใช้ได้จริง = %.0f µs (ตัวจำกัดคือ %s)"
                       % (limit, out["limited_by"]))
    return out


def default_exposures(current_us=None):
    """
    ลิสต์ตั้งต้น — ไล่จากค่าที่ใช้อยู่ลงไปจนถึงบริเวณที่เบลอ ~2 px ที่ไลน์ 450 CPM.
    ค่าคงที่เหล่านี้มาจากสูตร ``exposure = เบลอ ÷ ความเร็วไลน์`` (7,800 px/s):
    1025 µs = 8 px (เพดานที่โมเดลทนได้) · 512 µs = 4 px (เป้าออกแบบ) · 256 µs = 2 px
    (ปัดลงเสมอ — 4 px พอดีคือ 512.8 µs ปัดขึ้นแล้วจะ **เกินเป้าไปนิดเดียว**
    ซึ่งทำให้รายงานว่า 'ไม่ผ่าน' ทั้งที่ตั้งใจให้ค่านี้คือจุดที่ผ่าน)
    """
    base = [2165.0, 1500.0, 1025.0, 700.0, 512.0, 350.0, 256.0]
    if current_us:
        cur = float(current_us)
        base = sorted({round(v, 1) for v in base if v < cur} | {round(cur, 1)},
                      reverse=True)
    return base
