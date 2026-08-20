"""
hik_burst.py — โหมด **"ถ่ายรัว"** ของแท็บกล้องอุตสาหกรรม (เครื่องมือทดสอบล้วน)

โจทย์ที่โมดูลนี้ตอบ
-------------------
*"เลื่อนวัตถุผ่านหน้ากล้อง แล้วกล้องจับภาพที่ **ไม่เบลอ** ได้ไหม"*

หลักการที่ยึด (และเหตุผลที่ยึด)
--------------------------------
1. **การถ่ายต้องวิ่งที่อัตราของกล้อง ไม่ใช่อัตราของโมเดล.** ถ้าทำเป็น
   ``ถ่าย → ตรวจ → ถ่าย → ตรวจ`` จะได้ ~2 ภาพ/วินาที (detect imgsz 1280 =
   370-468 ms) ⇒ วัตถุขยับ ~270 mm ระหว่างสองภาพ = วัด "ความเร็วของ bestX"
   ไม่ใช่ "ความสามารถของกล้อง". โมดูลนี้จึง **เก็บภาพก่อน ตรวจทีหลัง**.
2. **ตัวเลขความคมต้องวัดที่ "วัตถุที่เคลื่อนไหว" ไม่ใช่ทั้งเฟรม.** ถ้าโบกวัตถุ
   หน้าฉากหลังที่คมและนิ่ง ค่าความคมของทั้งเฟรมแทบไม่ขยับ ⇒ ระบบจะรายงานว่า
   "คม" ทั้งที่วัตถุเละ = **คำตอบที่ผิดแบบมั่นใจ** (กฎเหล็กข้อ 2 ห้ามตรง ๆ).
   จึงประมาณฉากหลังจากค่ามัธยฐานของทั้งชุด แล้ววัดเฉพาะบริเวณที่ต่างจากฉากหลัง.
3. **ไม่มั่นใจ = ไม่แสดงตัวเลข.** การจับคู่ตำแหน่ง (phase correlation) ที่คะแนน
   ต่ำจะคืน ``None`` แทนการเดา — ผู้ใช้จะเห็น "—" ไม่ใช่เลขที่ดูน่าเชื่อถือแต่มั่ว.
4. **คำนวณตัวเลขตอนเปิดดู ไม่ใช่ตอนถ่าย.** เธรดเขียนไฟล์ที่ช้าลง = เฟรมถูกทิ้ง
   = กระป๋องหายไปจากชุดทดสอบ. ตอนถ่ายจึงบันทึกแค่ภาพ + เวลาที่เฟรมมาถึง.

โมดูลนี้ **ไม่ import Flask และไม่ import ultralytics** — การตรวจถูกฉีดเข้ามา
เป็นฟังก์ชัน (``detect_fn``) จาก ``app.py`` จึงเทสต์ได้ตรง ๆ บนเครื่องที่ไม่มีโมเดล.
"""

import json
import math
import os
import re
import shutil
import threading
import time

import numpy as np

try:
    import cv2
except Exception:                                    # pragma: no cover - อยู่ใน requirements
    cv2 = None

try:
    import config
except Exception:                                    # pragma: no cover
    config = None

try:
    from logger import setup_logger
    logger = setup_logger(__name__)
except Exception:                                    # pragma: no cover
    import logging
    logger = logging.getLogger(__name__)


def _cfg(name, default=None):
    return getattr(config, name, default) if config is not None else default


# ── ค่าคงที่ของการวัด (อธิบายเหตุผลไว้ตรงจุด) ──────────────────────────
ANALYSIS_SCALE = 0.5      # หาบริเวณที่เคลื่อนไหวบนภาพย่อครึ่ง (เร็วขึ้น 4 เท่า,
                          # ตำแหน่งระดับ ±2 px พอสำหรับการ "เลือกกรอบ")
BG_SAMPLE = 25            # จำนวนเฟรมที่สุ่มมาหาค่ามัธยฐานเป็นฉากหลัง
MIN_DIFF_THR = 18.0       # ต่างจากฉากหลังอย่างน้อยเท่านี้จึงนับว่า "เคลื่อนไหว"
MIN_MOVE_FRAC = 0.004     # บริเวณเล็กกว่า 0.4% ของเฟรม = สัญญาณรบกวน ไม่ใช่วัตถุ
MAX_MOVE_FRAC = 0.80      # ใหญ่กว่า 80% = กล้องสั่น/ไฟกระพริบทั้งเฟรม ไม่ใช่วัตถุ
MIN_PC_RESPONSE = 0.12    # คะแนน phase correlation ต่ำกว่านี้ = ไม่รู้ ⇒ ไม่รายงาน
# ⚠️ คะแนนอย่างเดียวเชื่อไม่ได้: วัดแล้วภาพสุ่มสองใบที่ไม่เกี่ยวกันเลยได้ 0.265
# ขณะที่การเลื่อนจริง 25 px ได้ 0.289 ⇒ ตั้งเกณฑ์คะแนนให้แยกสองเคสนี้ไม่ได้.
# จึงต้อง **ตรวจซ้ำด้วยการวัดที่เป็นอิสระจากกัน**: ระยะที่จุดกึ่งกลางกรอบวัตถุ
# ขยับ (หยาบ ±ไม่กี่พิกเซล แต่ไม่ขึ้นกับ phase correlation เลย). ถ้าสองค่าไม่ตรงกัน
# = ไม่รู้จริง ⇒ ไม่รายงาน. (แนวคิดเดียวกับ `_verify_boxes` ของชั้นกรอบแดง)
# 💡 ไอเดียที่ **วัดแล้วปฏิเสธ**: เช็คความสมมาตร a→b กับ b→a — cv2.phaseCorrelate
#    ให้ผลตรงข้ามกันเป๊ะเสมอ (คลาดเคลื่อน 0.000 ทุกเคสรวมทั้งภาพสุ่ม) = ใช้แยกไม่ได้
# 💡 อีกไอเดียที่ **วัดแล้วปฏิเสธ**: ลบฉากหลังออกก่อนจับคู่ (absdiff กับฉากหลัง)
#    ฟังดูควรช่วย แต่วัดแล้วแย่ลงมาก (เลื่อนจริง 24 px อ่านได้ 1.7 px) เพราะภาพ
#    ผลต่างของวัตถุ ณ สองตำแหน่งมี "ลายข้างใน" ไม่เหมือนกัน (ฉากหลังใต้วัตถุคนละที่)
#    ⇒ ไม่ใช่การเลื่อนของภาพเดียวกันอีกต่อไป. ภาพดิบให้ผลแม่นทุกระยะที่ทดสอบ
#    (6/12/24/40/60 px อ่านได้ 5.4/10.9/24.7/40.9/59.9) ตราบใดที่หน้าต่างรัดวัตถุ.
PC_TOL_PX = 6.0           # กรอบวัตถุคลาดได้เองราวนี้ (ขอบที่ threshold ได้มาไม่นิ่ง)
PC_TOL_FRAC = 0.6
MIN_PC_SIDE = 16          # หน้าต่างที่เล็กกว่านี้จับคู่ตำแหน่งไม่ได้
SHARP_REL_OK = 0.80       # คมเกิน 80% ของภาพที่คมที่สุดในชุด = ถือว่า "คม"
# ความสว่างเฉลี่ยที่ "พอใช้งาน" สำหรับภาพ QC — ค่าเดียวกับ `diagnose_hikrobot.TARGET_MEAN`
# (ถ้าแก้ที่หนึ่ง ต้องแก้อีกที่ ไม่งั้นเครื่องมือสองตัวจะแนะนำไฟคนละจำนวนเท่า)
TARGET_MEAN = 80.0
DIR_RATIO_MARGIN = 0.70   # อัตราส่วนแกนต่างจากฐานเกินเท่านี้จึงกล้าบอกทิศทางเบลอ

METRICS_FILE = "metrics.json"
DETECT_FILE = "detect.json"
META_FILE = "meta.json"
THUMB_DIR = "_thumbs"
METRICS_VERSION = 2

_NAME_RE = re.compile(r"^[0-9A-Za-z_.-]{1,64}$")
_FRAME_RE = re.compile(r"^[0-9]{1,8}\.jpg$")


# ════════════════════════════════════════════════════════════════════
# เส้นทางไฟล์ — ทุกทางเข้าต้องผ่าน session_dir()/frame_path()
# (ชื่อชุด/ชื่อไฟล์มาจาก URL ⇒ ถือเป็นข้อมูลที่ไม่น่าเชื่อถือเสมอ)
# ════════════════════════════════════════════════════════════════════
def burst_root():
    root = _cfg("HIK_BURST_DIR") or os.path.join("data", "hik_burst")
    if not os.path.isabs(root):
        base = _cfg("BASE_DIR") or os.path.dirname(os.path.abspath(__file__))
        root = os.path.join(base, root)
    return root


def session_dir(name, must_exist=True):
    """คืน path เต็มของชุดภาพ — โยน ``ValueError`` ถ้าชื่อไม่ปลอดภัย/ไม่มีอยู่."""
    if not isinstance(name, str) or not _NAME_RE.match(name) or name in (".", ".."):
        raise ValueError("ชื่อชุดภาพไม่ถูกต้อง")
    path = os.path.join(burst_root(), name)
    # ด่านที่สอง: ยืนยันด้วย path ที่ resolve แล้ว ไม่ใช่เชื่อ regex อย่างเดียว
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(burst_root()):
        raise ValueError("ชื่อชุดภาพไม่ถูกต้อง")
    if must_exist and not os.path.isdir(path):
        raise ValueError("ไม่พบชุดภาพนี้ (อาจถูกลบไปแล้ว)")
    return path


def frame_path(name, filename):
    if not isinstance(filename, str) or not _FRAME_RE.match(filename):
        raise ValueError("ชื่อไฟล์ภาพไม่ถูกต้อง")
    return os.path.join(session_dir(name), filename)


def list_frames(path):
    try:
        names = [f for f in os.listdir(path) if _FRAME_RE.match(f)]
    except OSError:
        return []
    return sorted(names)


def _read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def _write_json(path, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception as e:                            # pragma: no cover - ดิสก์/สิทธิ์
        logger.warning("[hik-burst] เขียน %s ไม่สำเร็จ: %s", path, e)
        return False


def free_mb():
    try:
        root = burst_root()
        os.makedirs(root, exist_ok=True)
        return int(shutil.disk_usage(root).free / (1024 * 1024))
    except Exception:
        return None


def _frame_index(filename):
    """ลำดับของเฟรมจากชื่อไฟล์ (ฐาน 0) — ใช้จับคู่กับ ``frame_ts`` ใน meta.json.
    ใช้ชื่อไฟล์ ไม่ใช่ตำแหน่งในลิสต์ เพราะผู้ใช้ลบภาพทิ้งได้ระหว่างทาง."""
    try:
        return int(os.path.splitext(filename)[0]) - 1
    except ValueError:                                # pragma: no cover - กัน regex เปลี่ยน
        return -1


# ════════════════════════════════════════════════════════════════════
# รายการชุดภาพ
# ════════════════════════════════════════════════════════════════════
def _dir_bytes(path, files):
    total = 0
    for f in files:
        try:
            total += os.path.getsize(os.path.join(path, f))
        except OSError:
            pass
    return total


def session_brief(name):
    path = session_dir(name)
    files = list_frames(path)
    meta = _read_json(os.path.join(path, META_FILE))
    metrics = _read_json(os.path.join(path, METRICS_FILE))
    detect = _read_json(os.path.join(path, DETECT_FILE))
    summary = (metrics.get("summary") or {}) if isinstance(metrics, dict) else {}
    ng = sum(1 for v in detect.values() if isinstance(v, dict) and v.get("verdict") == "ng")
    return {
        "name": name,
        "frames": len(files),
        "mb": round(_dir_bytes(path, files) / (1024.0 * 1024.0), 1),
        "started_at": meta.get("started_at"),
        "exposure_us": meta.get("exposure_us"),
        "gain_db": meta.get("gain_db"),
        "size": meta.get("size"),
        "seconds": meta.get("seconds"),
        "saved": meta.get("saved"),
        "dropped": meta.get("dropped"),
        "finished_reason": meta.get("finished_reason"),
        "metrics_ready": bool(summary) and metrics.get("version") == METRICS_VERSION,
        "summary": summary,
        "detected": len(detect),
        "ng": ng,
    }


def list_sessions():
    root = burst_root()
    try:
        names = [d for d in os.listdir(root)
                 if _NAME_RE.match(d) and os.path.isdir(os.path.join(root, d))]
    except OSError:
        return []
    out = []
    for n in sorted(names, reverse=True):
        try:
            out.append(session_brief(n))
        except Exception as e:                        # pragma: no cover
            logger.warning("[hik-burst] อ่านชุด %s ไม่ได้: %s", n, e)
    return out


def delete_session(name):
    path = session_dir(name)
    shutil.rmtree(path)
    logger.info("[hik-burst] ลบชุดภาพ %s", name)
    return True


def delete_frames(name, files):
    """ลบภาพทีละใบ + ล้าง thumbnail/ผลตรวจของใบนั้นให้ตรงกัน.
    ไม่แตะ ``metrics.json`` — รายการที่ไม่มีไฟล์แล้วจะถูกข้ามตอนอ่าน จึงไม่
    ต้องเขียนไฟล์ใหญ่ใหม่ทุกครั้งที่ลบ 1 ภาพ."""
    path = session_dir(name)
    removed = []
    for f in files or []:
        try:
            fp = frame_path(name, f)
        except ValueError:
            continue
        try:
            os.remove(fp)
            removed.append(f)
        except OSError:
            continue
        try:
            os.remove(os.path.join(path, THUMB_DIR, f))
        except OSError:
            pass
    if removed:
        det = _read_json(os.path.join(path, DETECT_FILE))
        if isinstance(det, dict) and any(f in det for f in removed):
            for f in removed:
                det.pop(f, None)
            _write_json(os.path.join(path, DETECT_FILE), det)
        logger.info("[hik-burst] ลบ %d ภาพจากชุด %s", len(removed), name)
    return removed


# ════════════════════════════════════════════════════════════════════
# ชั้นวัดผล — ความคม / ทิศทางความเบลอ / ความเร็ว / ระยะเบลอ
# ════════════════════════════════════════════════════════════════════
def _gray(path):
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    return img


def _scores(gray):
    """ความคม (Tenengrad) + พลังงานขอบแยกแกน.

    ทำไมไม่ใช้ variance ของ Laplacian อย่างเดียวเหมือนที่อื่นในโปรเจกต์:
    Laplacian เป็น **isotropic** ⇒ แยก "เบลอเพราะเคลื่อนที่" (เบลอแกนเดียว)
    ออกจาก "โฟกัสหลุด" (เบลอทั้งสองแกน) ไม่ได้เลย ซึ่งคือคำถามหลักของหน้านี้.
    """
    g = gray.astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    sharp = float(np.mean(gx * gx + gy * gy))
    return sharp, float(np.mean(np.abs(gx))), float(np.mean(np.abs(gy)))


def _background(path, files, scale=ANALYSIS_SCALE, sample=BG_SAMPLE):
    """ฉากหลังโดยประมาณ = ค่ามัธยฐานของเฟรมที่สุ่มมา (วัตถุที่วิ่งผ่านจะถูก
    มัธยฐานกลืนหายไป เหลือแต่ของที่นิ่ง)."""
    if not files:
        return None
    step = max(1, len(files) // max(1, sample))
    stack = []
    for f in files[::step][:sample]:
        g = _gray(os.path.join(path, f))
        if g is None:
            continue
        stack.append(cv2.resize(g, (0, 0), fx=scale, fy=scale,
                                interpolation=cv2.INTER_AREA))
    if len(stack) < 3:
        return None
    shapes = {s.shape for s in stack}
    if len(shapes) != 1:                              # ROI ถูกเปลี่ยนกลางชุด
        return None
    return np.median(np.stack(stack), axis=0).astype(np.uint8)


def _moving_rect(g_small, bg):
    """กรอบของ "ของที่ไม่ได้อยู่ในฉากหลัง" (พิกัดบนภาพย่อ) — None เมื่อไม่พบ."""
    if bg is None or g_small.shape != bg.shape:
        return None
    diff = cv2.absdiff(g_small, bg)
    diff = cv2.GaussianBlur(diff, (5, 5), 0)
    otsu, _ = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = (diff >= max(MIN_DIFF_THR, float(otsu))).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, _lbl, stats, _c = cv2.connectedComponentsWithStats(mask, 8)
    if n <= 1:
        return None
    areas = stats[1:, cv2.CC_STAT_AREA]
    i = int(np.argmax(areas)) + 1
    frac = float(stats[i, cv2.CC_STAT_AREA]) / float(mask.size)
    if frac < MIN_MOVE_FRAC or frac > MAX_MOVE_FRAC:
        return None
    x, y, w, h = (int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP]),
                  int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT]))
    pad = 4
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1 = min(mask.shape[1], x + w + pad)
    y1 = min(mask.shape[0], y + h + pad)
    return (x0, y0, x1 - x0, y1 - y0)


def _union(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = min(ax, bx), min(ay, by)
    x1, y1 = max(ax + aw, bx + bw), max(ay + ah, by + bh)
    return (x0, y0, x1 - x0, y1 - y0)


def _centre_shift(a, b, scale=ANALYSIS_SCALE):
    """ระยะที่ **จุดกึ่งกลางกรอบวัตถุ** ขยับ (พิกเซลของภาพเต็ม).
    หยาบกว่า phase correlation มาก แต่ได้มาคนละทาง จึงใช้เป็นตัวตรวจซ้ำได้."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return math.hypot((bx + bw / 2.0) - (ax + aw / 2.0),
                      (by + bh / 2.0) - (ay + ah / 2.0)) / scale


def _shift(prev_small, cur_small, rect, expect_px=None, scale=ANALYSIS_SCALE):
    """ระยะที่เนื้อหาในกรอบเลื่อนไประหว่างสองเฟรม (พิกเซลของภาพเต็ม).

    คืน ``None`` เมื่อคะแนนจับคู่ต่ำ **หรือ** ค่าที่ได้ขัดกับ ``expect_px``
    (ระยะที่วัดจากกรอบวัตถุ) — เดาไม่ได้ก็ไม่เดา (กฎเหล็กข้อ 2).
    """
    x, y, w, h = rect
    a = prev_small[y:y + h, x:x + w]
    b = cur_small[y:y + h, x:x + w]
    if a.shape != b.shape or min(a.shape[:2]) < MIN_PC_SIDE:
        return None
    af = a.astype(np.float32)
    bf = b.astype(np.float32)
    try:
        win = cv2.createHanningWindow((af.shape[1], af.shape[0]), cv2.CV_32F)
        (dx, dy), resp = cv2.phaseCorrelate(af, bf, win)
    except Exception:
        return None
    if resp is None or float(resp) < MIN_PC_RESPONSE:
        return None
    shift = math.hypot(float(dx), float(dy)) / scale
    if expect_px is not None:
        tol = max(PC_TOL_PX, PC_TOL_FRAC * float(expect_px))
        if abs(shift - float(expect_px)) > tol:
            return None
    return shift


def _thumb(path, filename, gray_or_bgr, width):
    tdir = os.path.join(path, THUMB_DIR)
    try:
        os.makedirs(tdir, exist_ok=True)
        h, w = gray_or_bgr.shape[:2]
        if w > width:
            small = cv2.resize(gray_or_bgr, (width, max(1, int(h * width / float(w)))),
                               interpolation=cv2.INTER_AREA)
        else:
            small = gray_or_bgr
        cv2.imwrite(os.path.join(tdir, filename), small,
                    [int(cv2.IMWRITE_JPEG_QUALITY), 78])
    except Exception:                                 # pragma: no cover
        pass


def compute_metrics(name, job=None):
    """วัดทุกเฟรมของชุด แล้วเขียน ``metrics.json`` + สร้าง thumbnail."""
    if cv2 is None:
        raise RuntimeError("ไม่มี OpenCV — วัดความคมไม่ได้")
    path = session_dir(name)
    files = list_frames(path)
    if not files:
        raise RuntimeError("ชุดนี้ไม่มีภาพเหลืออยู่")

    meta = _read_json(os.path.join(path, META_FILE))
    ts = meta.get("frame_ts") or []
    exposure_us = meta.get("exposure_us")
    mm_per_px = _cfg("HIK_BURST_MM_PER_PX")
    thumb_w = int(_cfg("HIK_BURST_THUMB_WIDTH", 260) or 260)

    if job is not None:
        job.total = len(files) + 1
    bg = _background(path, files)
    if job is not None:
        job.done = 1

    frames = {}
    prev_small = prev_rect = None
    prev_ts = None
    for i, fn in enumerate(files):
        if job is not None and job.cancelled:
            break
        g = _gray(os.path.join(path, fn))
        if g is None:
            if job is not None:
                job.done = i + 2
            continue
        small = cv2.resize(g, (0, 0), fx=ANALYSIS_SCALE, fy=ANALYSIS_SCALE,
                           interpolation=cv2.INTER_AREA)
        rect = _moving_rect(small, bg)

        if rect is not None:
            x, y, w, h = rect
            inv = int(1.0 / ANALYSIS_SCALE)
            fx0, fy0 = x * inv, y * inv
            fx1 = min(g.shape[1], (x + w) * inv)
            fy1 = min(g.shape[0], (y + h) * inv)
            crop = g[fy0:fy1, fx0:fx1]
            roi_src = "moving"
            roi = [fx0, fy0, int(fx1 - fx0), int(fy1 - fy0)]
        else:
            crop = g
            roi_src = "frame"
            roi = None
        if crop.size == 0 or min(crop.shape[:2]) < 8:
            crop, roi_src, roi = g, "frame", None

        sharp, sx, sy = _scores(crop)
        rec = {"sharp": round(sharp, 1), "sx": round(sx, 3), "sy": round(sy, 3),
               "ratio": round(sx / sy, 3) if sy > 1e-6 else None,
               "mean": round(float(np.mean(crop)), 1),
               "roi": roi, "roi_src": roi_src}

        idx = _frame_index(fn)
        cur_ts = ts[idx] if 0 <= idx < len(ts) else None
        shift = None
        if prev_small is not None and rect is not None and prev_rect is not None:
            win = _union(prev_rect, rect)
            wx, wy, ww, wh = win
            if (ww * wh) / float(small.size) <= MAX_MOVE_FRAC:
                shift = _shift(prev_small, small, win,
                               expect_px=_centre_shift(prev_rect, rect))
        if shift is not None:
            rec["shift_px"] = round(shift, 2)
            if cur_ts is not None and prev_ts is not None and cur_ts > prev_ts:
                dt = cur_ts - prev_ts
                rec["dt_ms"] = round(dt * 1000.0, 2)
                speed = shift / dt
                rec["speed_px_s"] = round(speed, 1)
                if exposure_us:
                    rec["blur_px"] = round(speed * (float(exposure_us) / 1e6), 2)
                if mm_per_px:
                    rec["speed_mm_s"] = round(speed * float(mm_per_px), 1)

        frames[fn] = rec
        _thumb(path, fn, g, thumb_w)
        prev_small, prev_rect, prev_ts = small, rect, cur_ts
        if job is not None:
            job.done = i + 2

    data = {"version": METRICS_VERSION, "count": len(frames),
            "computed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mm_per_px": mm_per_px, "exposure_us": exposure_us,
            "frames": frames}
    data["summary"] = _summarize(frames, exposure_us, mm_per_px)
    _annotate_directions(frames, data["summary"])
    _write_json(os.path.join(path, METRICS_FILE), data)
    return data


def _median(vals):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def _summarize(frames, exposure_us, mm_per_px):
    if not frames:
        return {}
    best_file = max(frames, key=lambda k: frames[k].get("sharp") or 0.0)
    best_sharp = frames[best_file].get("sharp") or 0.0
    moving = [v for v in frames.values() if v.get("roi_src") == "moving"]
    speeds = [v.get("speed_px_s") for v in frames.values()]
    blurs = [v.get("blur_px") for v in frames.values()]
    dts = [v.get("dt_ms") for v in frames.values()]

    # ฐานของ "อัตราส่วนแกน" ต้องมาจากภาพที่คมที่สุดในชุดเดียวกัน ไม่ใช่ค่าคงที่ 1.0 —
    # ฉากที่มีลายตั้งเยอะจะได้ sx/sy สูงกว่า 1 อยู่แล้วโดยไม่เกี่ยวกับความเบลอ
    sharp_ratios = [v.get("ratio") for v in frames.values()
                    if v.get("ratio") and (v.get("sharp") or 0) >= best_sharp * SHARP_REL_OK]
    base_ratio = _median(sharp_ratios)

    speed_med = _median(speeds)
    mean_med = _median([v.get("mean") for v in frames.values()])
    out = {
        "mean_median": round(mean_med, 1) if mean_med is not None else None,
        "best_file": best_file,
        "best_sharp": best_sharp,
        "moving_frames": len(moving),
        "total_frames": len(frames),
        "base_ratio": round(base_ratio, 3) if base_ratio else None,
        "exposure_us": exposure_us,
        "dt_ms_median": round(_median(dts), 2) if _median(dts) else None,
        "speed_px_s": round(speed_med, 1) if speed_med else None,
        "blur_px_median": round(_median(blurs), 2) if _median(blurs) is not None else None,
        "blur_px_min": round(min([b for b in blurs if b is not None]), 2) if any(
            b is not None for b in blurs) else None,
    }
    if out["dt_ms_median"]:
        out["fps_measured"] = round(1000.0 / out["dt_ms_median"], 1)
    if speed_med and speed_med > 0:
        # exposure สูงสุดที่จะได้เบลอ ≤ 1 พิกเซล ที่ความเร็ว **ที่วัดได้จริง**
        max_us = 1.0 / speed_med * 1e6
        out["max_exposure_us_1px"] = round(max_us, 1)
        if exposure_us:
            # ① ลด exposure ลงเท่านี้เท่า ⇒ ภาพมืดลงเท่านี้เท่า ⇒ ต้องเพิ่มไฟเท่านี้เท่า
            #    **เพื่อให้ภาพสว่างเท่าเดิม** — ไม่ใช่ "เท่านี้แล้วใช้งานได้"
            factor = float(exposure_us) / max_us
            out["light_factor_needed"] = round(factor, 1)
            # ② ถ้าภาพ *ตอนนี้* ก็มืดเกินไปอยู่แล้ว ต้องเพิ่มอีกชั้นหนึ่ง.
            #    รายงานแยกกันโดยตั้งใจ: ตัวเลข ① อย่างเดียวทำให้เข้าใจผิดว่า
            #    "เพิ่มไฟ 21 เท่าแล้วจบ" ทั้งที่ของจริงต้องมากกว่านั้นหลายเท่า
            if mean_med and mean_med > 0.5:
                out["light_factor_usable"] = round(factor * TARGET_MEAN / mean_med, 1)
                out["target_mean"] = TARGET_MEAN
        if mm_per_px:
            out["speed_mm_s"] = round(speed_med * float(mm_per_px), 1)
    return out


def _annotate_directions(frames, summary):
    """ติดป้าย "เบลอแบบไหน" เฉพาะเมื่อหลักฐานชัดพอ — ไม่ชัดให้เว้นว่างไว้."""
    best = summary.get("best_sharp") or 0.0
    base = summary.get("base_ratio")
    for v in frames.values():
        sharp = v.get("sharp") or 0.0
        if best > 0 and sharp >= best * SHARP_REL_OK:
            v["blur_kind"] = "sharp"
            continue
        r = v.get("ratio")
        if not base or not r:
            v["blur_kind"] = None
            continue
        if r < base * DIR_RATIO_MARGIN:
            v["blur_kind"] = "motion_x"               # ขอบแนวตั้งหายไป = เบลอแนวนอน
        elif r > base / DIR_RATIO_MARGIN:
            v["blur_kind"] = "motion_y"
        else:
            v["blur_kind"] = "isotropic"              # เบลอทั้งสองแกน = น่าจะโฟกัส
    return frames


def load_metrics(name):
    data = _read_json(os.path.join(session_dir(name), METRICS_FILE))
    if not isinstance(data, dict) or data.get("version") != METRICS_VERSION:
        return None
    return data


def load_detect(name):
    data = _read_json(os.path.join(session_dir(name), DETECT_FILE))
    return data if isinstance(data, dict) else {}


def session_detail(name, sort="sharp", limit=0, offset=0):
    path = session_dir(name)
    files = list_frames(path)
    meta = _read_json(os.path.join(path, META_FILE))
    metrics = load_metrics(name) or {}
    fm = metrics.get("frames") or {}
    det = load_detect(name)

    rows = []
    for f in files:
        row = {"file": f}
        row.update(fm.get(f) or {})
        d = det.get(f)
        if isinstance(d, dict):
            row["verdict"] = d.get("verdict")
            row["dent_count"] = d.get("dent_count")
            row["max_confidence"] = d.get("max_confidence")
            row["infer_ms"] = d.get("infer_ms")
        rows.append(row)

    if sort == "sharp" and fm:
        rows.sort(key=lambda r: r.get("sharp") or -1.0, reverse=True)
    elif sort == "blur" and fm:
        rows.sort(key=lambda r: (r.get("blur_px") is None, r.get("blur_px") or 0.0))
    else:
        rows.sort(key=lambda r: r["file"])

    total = len(rows)
    if limit:
        rows = rows[offset:offset + limit]
    return {"name": name, "meta": meta, "summary": metrics.get("summary") or {},
            "metrics_ready": bool(fm), "total": total, "frames": rows,
            "free_mb": free_mb()}


def top_sharp_files(name, count):
    """ไฟล์ที่คมที่สุด N ใบ — ใช้เลือกภาพไปตรวจอัตโนมัติหลังถ่ายเสร็จ.
    ถ้ายังไม่ได้วัด จะคืนลิสต์ว่าง (ไม่เดาโดยหยิบใบแรก ๆ มามั่ว)."""
    metrics = load_metrics(name)
    if not metrics:
        return []
    path = session_dir(name)
    have = set(list_frames(path))
    fm = {k: v for k, v in (metrics.get("frames") or {}).items() if k in have}
    ranked = sorted(fm, key=lambda k: fm[k].get("sharp") or 0.0, reverse=True)
    return ranked[:max(0, int(count))]


def run_detect(name, files, detect_fn, job=None):
    """ตรวจภาพที่ระบุด้วยฟังก์ชันที่ถูกฉีดเข้ามา แล้วเก็บผลลง ``detect.json``.

    ``detect_fn(path) -> dict`` — ``app.py`` เป็นคนรู้จักโมเดล ไม่ใช่โมดูลนี้.
    ผลเขียนลงดิสก์ระหว่างทางเป็นระยะ ⇒ ปิดหน้าเว็บกลางคันแล้วผลที่ตรวจไปแล้วไม่หาย.
    """
    path = session_dir(name)
    have = set(list_frames(path))
    todo = [f for f in (files or []) if f in have]
    det = load_detect(name)
    if job is not None:
        job.total = len(todo)
        job.done = 0
    for i, f in enumerate(todo):
        if job is not None and job.cancelled:
            break
        try:
            det[f] = detect_fn(os.path.join(path, f))
        except Exception as e:
            det[f] = {"error": str(e)}
            logger.warning("[hik-burst] ตรวจ %s/%s ไม่สำเร็จ: %s", name, f, e)
        if job is not None:
            job.done = i + 1
        if (i + 1) % 10 == 0:
            _write_json(os.path.join(path, DETECT_FILE), det)
    _write_json(os.path.join(path, DETECT_FILE), det)
    return det


# ════════════════════════════════════════════════════════════════════
# งานเบื้องหลัง — ทีละงานเท่านั้น (วัดผล/ตรวจ กินซีพียูทั้งคู่ และแข่งกับ
# เธรดจับภาพของกล้อง ⇒ ปล่อยให้ชนกันเองไม่ได้)
# ════════════════════════════════════════════════════════════════════
class Job(object):
    def __init__(self, kind, session):
        self.kind = kind
        self.session = session
        self.total = 0
        self.done = 0
        self.error = None
        self.cancelled = False
        self.started = time.time()
        self.finished = None

    def status(self):
        return {"kind": self.kind, "session": self.session,
                "done": self.done, "total": self.total,
                "running": self.finished is None and not self.cancelled,
                "cancelled": self.cancelled, "error": self.error,
                "elapsed_s": round((self.finished or time.time()) - self.started, 1)}


_job = None
_job_lock = threading.Lock()


def job_status():
    j = _job
    return j.status() if j is not None else None


def job_running_on(session):
    """งานที่กำลังทำอยู่กับชุดนี้หรือไม่ — ใช้ก่อนลบ ไม่งั้นงานที่ยังวิ่งอยู่จะ
    **สร้างโฟลเดอร์กลับขึ้นมาใหม่** หลังลบไปแล้ว (เจอจริงตอนทดสอบด้วยเบราว์เซอร์:
    ลบชุดทิ้งแล้วเหลือ `_thumbs/` + `metrics.json` เป็นซากที่ไม่มีภาพสักใบ)."""
    j = _job
    return bool(j is not None and j.finished is None and not j.cancelled
                and j.session == session)


def cancel_job(wait=0.0):
    """สั่งยกเลิกงานเบื้องหลัง — ``wait`` = รอให้หยุดจริงกี่วินาที (0 = ไม่รอ)."""
    j = _job
    if j is None:
        return False
    j.cancelled = True
    deadline = time.time() + float(wait or 0)
    while wait and j.finished is None and time.time() < deadline:
        time.sleep(0.05)
    return j.finished is not None or not wait


def start_job(kind, session, fn):
    """เริ่มงานเบื้องหลัง — คืน ``(ok, message)``. ปฏิเสธถ้ามีงานค้างอยู่."""
    global _job
    with _job_lock:
        if _job is not None and _job.finished is None and not _job.cancelled:
            return False, "มีงานทำอยู่ (%s) — รอให้เสร็จก่อน" % _job.kind
        job = Job(kind, session)
        _job = job

    def _run():
        try:
            fn(job)
        except Exception as e:
            job.error = str(e)
            logger.warning("[hik-burst] งาน %s ล้มเหลว: %s", kind, e)
        finally:
            job.finished = time.time()

    threading.Thread(target=_run, daemon=True).start()
    return True, None
