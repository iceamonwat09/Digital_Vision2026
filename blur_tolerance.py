"""
blur_tolerance.py — วัดว่า **โมเดลทนความเบลอจากการเคลื่อนที่ได้กี่พิกเซล**

ทำไมต้องมีเครื่องมือนี้
------------------------
สเปกของทั้งระบบไล่มาจากตัวเลขเดียว:

    เบลอกี่ px ที่โมเดลยังเห็น → exposure สูงสุดที่ใช้ได้ → ต้องเพิ่มไฟกี่เท่า → ซื้อไฟตัวไหน

ก่อนหน้านี้ตัวเลขต้นทางนั้นเป็น **ค่าที่เดาไว้** (`--blur-limit-mm 0.2` ใน
`hik_offline_eval.py`) ไม่เคยวัดเลย. เดาผิด 3 เท่า = งบไฟผิด 3 เท่า.

วิธีวัด
--------
ใส่ **เบลอทิศทางเดียว** (เคอร์เนล 1×L ตามแนวที่ชิ้นงานวิ่ง — ซึ่งคือสิ่งที่การ
เคลื่อนที่ระหว่างชัตเตอร์เปิดทำกับภาพจริง ๆ) ลงบนภาพ NG จริง แล้วรันโมเดล
**ตัวเดียวกับที่ใช้งานจริง** ไล่ความยาว L แล้วดูว่าผลตรวจเสื่อมตรงไหน

⚠️ สิ่งที่สคริปต์นี้วัด คือ **ความทนทานของโมเดลต่อความเบลอ** เทียบกับผลของ
   ตัวมันเองที่ L=0 — **ไม่ใช่ความแม่นเทียบเฉลยที่คนติดป้าย**. ภาพที่ L=0
   โมเดลหาไม่เจออยู่แล้วจะถูกตัดออก เพราะใช้วัดการเสื่อมไม่ได้
⚠️ เบลอสังเคราะห์ ≈ เบลอจริง แต่ไม่เท่ากันเป๊ะ (ของจริงมี noise จาก exposure
   สั้นปนมาด้วย) ⇒ ใช้ **ตั้งงบประมาณ** แล้วยืนยันซ้ำด้วยภาพจริงเมื่อมีไฟแล้ว

วิธีใช้ (บนสถานี)
    py -3.9 blur_tolerance.py --images sample_cans
    py -3.9 blur_tolerance.py --images out_ng --model bestX.pt --imgsz 1280
    py -3.9 blur_tolerance.py --images out_ng --speed-mm-s 638 --mm-per-px 0.082 ^
                              --exposure-us 2003
    py -3.9 blur_tolerance.py --selftest        # ตรวจว่าตัวเครื่องมือเองทำงานถูก

exit code: 0 = วัดได้ · 1 = วัดได้แต่มีข้อสังเกต · 2 = รันไม่ได้ · 3 = สรุปไม่ได้
"""

import argparse
import glob
import json
import os
import sys

import numpy as np

try:
    import cv2
except Exception as e:                                # pragma: no cover
    print("ต้องมี OpenCV: %s" % e)
    sys.exit(2)

_LINE = "─" * 78
IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp")

# เกณฑ์ตัดสิน — เปิดเผยเป็นค่าคงที่เพื่อให้เถียงกันได้ว่าเหมาะหรือไม่
KEEP_FRAC = 0.95          # "ยังใช้ได้" = ตรวจเจอ ≥95% ของที่เจอตอนไม่เบลอ
MATCH_IOU = 0.30          # กรอบที่เบลอจะขยับ/ขยาย ⇒ เกณฑ์จับคู่ต้องหลวมกว่าปกติ
TARGET_MEAN = 80.0        # ความสว่างที่ "พอใช้งาน" — ต้องตรงกับ hik_burst.TARGET_MEAN
DEFAULT_BLURS = (0, 1, 2, 3, 4, 6, 8, 12, 16, 20)
# ⚠️ ต้องเป็นชุดเดียวกับที่ระบบใช้จริง — อ่านจาก ``modes/<mode>.py`` ถ้าอ่านได้
# (เดิม hard-code ไว้ที่นี่ ⇒ เพิ่มคลาส "ทั้งใบ" ใหม่ในโหมดแล้วเครื่องมือนี้จะ
# นับมันเป็นตำหนิ = รายงานคนละเรื่องกับหน้างานโดยไม่มีใครรู้)
NON_DEFECT = {"good", "can"}
try:                                                   # pragma: no cover
    from modes.can_dent import NON_DEFECT_CLASSES as _ND
    NON_DEFECT = {str(x).lower() for x in _ND}
except Exception:
    pass


def head(title):
    print("\n" + _LINE)
    print(title)
    print(_LINE)


# ════════════════════════════════════════════════════════════════════
# ① ชั้นภาพ — เบลอจากการเคลื่อนที่
# ════════════════════════════════════════════════════════════════════
def motion_blur(img, length_px, angle_deg=0.0):
    """
    เบลอแบบ "วัตถุเลื่อนไป L พิกเซลระหว่างที่ชัตเตอร์เปิด".

    เป็นการเฉลี่ยตามแนวเส้นตรง (ไม่ใช่ Gaussian) เพราะการเคลื่อนที่ด้วย
    ความเร็วคงที่ทำให้แต่ละจุดถูกเกลี่ยเท่า ๆ กันตลอดระยะที่เคลื่อนไป —
    ใช้ Gaussian จะได้ความนุ่มแบบ "โฟกัสหลุด" ซึ่งเป็นคนละอาการ.
    """
    if length_px is None or length_px <= 1:
        return img.copy()
    n = int(round(length_px))
    k = np.zeros((n, n), np.float32)
    k[n // 2, :] = 1.0                                # เส้นแนวนอนกลางเคอร์เนล
    if angle_deg:
        m = cv2.getRotationMatrix2D((n / 2.0 - 0.5, n / 2.0 - 0.5), angle_deg, 1.0)
        k = cv2.warpAffine(k, m, (n, n))
    s = k.sum()
    if s <= 0:                                        # pragma: no cover - กันหารศูนย์
        return img.copy()
    return cv2.filter2D(img, -1, k / s, borderType=cv2.BORDER_REPLICATE)


def rescale(img, scale):
    """จำลอง 'ถ่ายกว้างขึ้น/แคบลง' — ชิ้นงานกินพิกเซลน้อยลง/มากขึ้น."""
    if scale == 1.0:
        return img.copy()
    h, w = img.shape[:2]
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    return cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))),
                      interpolation=interp)


# ════════════════════════════════════════════════════════════════════
# ② ชั้นวัดผล — เทียบผลที่เบลอกับผลของตัวเองตอนไม่เบลอ
# ════════════════════════════════════════════════════════════════════
def _iou(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / ua if ua > 0 else 0.0


def defects_of(dets):
    return [d for d in dets or [] if d.get("class_name") not in NON_DEFECT]


def match_rate(base, test, scale=1.0):
    """
    กี่ % ของรอยบุบที่เจอตอนไม่เบลอ ที่ยัง "เจออยู่" หลังเบลอ
    คืน (สัดส่วนที่ยังเจอ, IoU เฉลี่ยของคู่ที่จับได้, ความมั่นใจเฉลี่ย)
    """
    if not base:
        return None, None, None
    used, ious, confs = set(), [], []
    for b in base:
        bb = [v * scale for v in b["bbox"]]
        best_i, best_iou = -1, 0.0
        for i, t in enumerate(test):
            if i in used:
                continue
            v = _iou(bb, t["bbox"])
            if v > best_iou:
                best_i, best_iou = i, v
        if best_iou >= MATCH_IOU:
            used.add(best_i)
            ious.append(best_iou)
            confs.append(float(test[best_i].get("confidence", 0.0)))
    return (len(ious) / float(len(base)),
            float(np.mean(ious)) if ious else 0.0,
            float(np.mean(confs)) if confs else 0.0)


def sweep(detect_fn, images, blurs=DEFAULT_BLURS, scales=(1.0,), angle=0.0,
          progress=None):
    """
    ``detect_fn(img) -> [ {class_name, confidence, bbox:[x0,y0,x1,y1]}, ... ]``

    คืน ``{scale: [ {blur, kept, iou, conf, n_base, n_found}, ... ]}``
    """
    out = {}
    for scale in scales:
        # ฐานอ้างอิงต้องวัดที่ **สเกลเดียวกัน** — ไม่งั้นความต่างของสเกลจะปนเข้ามา
        bases = []
        for img in images:
            small = rescale(img, scale)
            bases.append((small, defects_of(detect_fn(small))))
        usable = [(im, b) for im, b in bases if b]
        rows = []
        for L in blurs:
            kept, ious, confs, nb, nf = [], [], [], 0, 0
            for im, base in usable:
                blurred = motion_blur(im, L * scale, angle)
                dets = defects_of(detect_fn(blurred))
                k, i, c = match_rate(base, dets)
                if k is None:                         # pragma: no cover
                    continue
                kept.append(k)
                ious.append(i)
                confs.append(c)
                nb += len(base)
                nf += int(round(k * len(base)))
            rows.append({
                "blur": L,
                "kept": float(np.mean(kept)) if kept else None,
                "iou": float(np.mean(ious)) if ious else None,
                "conf": float(np.mean(confs)) if confs else None,
                "n_base": nb, "n_found": nf,
            })
            if progress:
                progress(scale, L)
        out[scale] = {"rows": rows, "images_used": len(usable),
                      "images_total": len(bases)}
    return out


def knee(rows, keep_frac=KEEP_FRAC):
    """
    ความเบลอสูงสุดที่ยัง "ใช้ได้" = ค่า blur ที่มากที่สุดซึ่ง **ทุกค่าตั้งแต่ 0
    ถึงตรงนั้น** ยังผ่านเกณฑ์. ใช้เงื่อนไข "ต่อเนื่อง" เพราะถ้าดูเฉพาะจุดสุดท้าย
    ที่ผ่าน จุดที่ผ่านแบบฟลุ๊คหลังจากที่พังไปแล้วจะทำให้ได้คำตอบที่ดีเกินจริง.
    คืน ``None`` เมื่อแม้แต่ค่าเบลอต่ำสุดก็ยังไม่ผ่าน (สรุปไม่ได้).
    """
    rows = sorted([r for r in rows if r.get("kept") is not None],
                  key=lambda r: r["blur"])
    if not rows:
        return None
    base = rows[0]["kept"]
    if not base:
        return None
    limit = None
    for r in rows:
        if r["kept"] >= base * keep_frac:
            limit = r["blur"]
        else:
            break
    return limit


# ════════════════════════════════════════════════════════════════════
# ②ᴮ ทิศทางที่สอง: **NG ปลอม** (เจอรอยบุบบนกระป๋องที่ไม่มีรอยบุบ)
# ════════════════════════════════════════════════════════════════════
# ⚠️ ทำไมต้องมี: ชั้น ② วัดแค่ "ยังเจอไหม" (recall) ⇒ ได้ 100% ที่เบลอ 8 px
# แล้วดูเหมือนใช้งานได้ **แต่ยังไม่ได้ตอบเลยว่าเบลอทำให้เจอของที่ไม่มีหรือเปล่า**
# การเบลอสร้างเงายืดที่หน้าตาเหมือนรอยบุบได้ตรง ๆ
#
# 📊 หลักฐานจากไลน์จริง (ภาพหน้าจอระบบ vision เดิมบนไลน์ 6, ส.ค. 2026):
#     TOT 40,062 · NG 0 · NG rate 0.00%
# ⇒ มาตรฐานที่พนักงานหน้างานคุ้นเคยคือ **แทบไม่มี NG ปลอมเลย** ไม่ใช่ "ไม่กี่ %"
# ⇒ ตัวจำกัดของงานนี้คือ **precision ไม่ใช่ recall** — กลับด้านกับที่วัดมาทั้งหมด

FP_ALLOW = 0                # ยอมให้มี "ใบที่โดนทักเพิ่ม" จากฐานที่ไม่เบลอกี่ใบ


def sweep_false(detect_fn, images, blurs=DEFAULT_BLURS, scales=(1.0,), angle=0.0,
                progress=None):
    """
    ไล่ความเบลอเดียวกันบน **ภาพกระป๋องดี** แล้วนับว่าโมเดลทักผิดกี่ใบ.

    คืน ``{scale: {"rows": [...], "images": n}}`` โดยแต่ละแถวมี
    ``fp_images`` (จำนวนใบที่โดนทัก) · ``fp_boxes`` (จำนวนกรอบรวม) ·
    ``fp_rate`` (สัดส่วนต่อใบ — เทียบกับ "NG rate %" ของไลน์ได้ตรง ๆ)

    ⚠️ แถว ``blur = 0`` คือ **ฐาน**: NG ปลอมที่มีอยู่แล้วโดยไม่เกี่ยวกับความเบลอ
    ต้องแยกออกจากที่ความเบลอเป็นคนทำให้เกิด ไม่งั้นจะโทษผิดตัว
    """
    out = {}
    for scale in scales:
        smalls = [rescale(img, scale) for img in images]
        rows = []
        for L in blurs:
            hit, boxes, confs = 0, 0, []
            for im in smalls:
                test = motion_blur(im, L * scale, angle) if L else im
                dets = defects_of(detect_fn(test))
                if dets:
                    hit += 1
                    boxes += len(dets)
                    confs.append(max(float(d.get("confidence", 0.0)) for d in dets))
            rows.append({
                "blur": L, "fp_images": hit, "fp_boxes": boxes,
                "fp_rate": (hit / float(len(smalls))) if smalls else None,
                "conf": float(np.mean(confs)) if confs else None,
            })
            if progress:
                progress(scale, L)
        out[scale] = {"rows": rows, "images": len(smalls)}
    return out


def false_knee(rows, allow=FP_ALLOW):
    """
    ความเบลอสูงสุดที่ยัง **ไม่เพิ่ม** NG ปลอมเกินฐานที่ไม่เบลอ.

    ใช้กติกา "ต่อเนื่อง" แบบเดียวกับ ``knee()`` — ระดับที่ผ่านแบบฟลุ๊คหลังจาก
    พังไปแล้วต้องไม่ถูกนับ. คืน ``None`` เมื่อไม่มีแถวให้ตัดสิน
    """
    rows = sorted([r for r in rows if r.get("fp_images") is not None],
                  key=lambda r: r["blur"])
    if not rows:
        return None
    base = rows[0]["fp_images"]
    limit = None
    for r in rows:
        if r["fp_images"] <= base + allow:
            limit = r["blur"]
        else:
            break
    return limit


def upper_bound_95(k, n):
    """
    เพดานบนของอัตราจริง (Clopper-Pearson ด้านเดียว 95%) จากที่สังเกต ``k`` ครั้ง
    ใน ``n`` ใบ.

    ⚠️ **นี่คือหัวใจของความซื่อสัตย์ของเครื่องมือนี้** — "ทดสอบ 30 ใบแล้วไม่เจอ
    NG ปลอมเลย" **ไม่ได้แปลว่าอัตราเป็น 0**. มันแปลได้แค่ว่าอัตราจริง ≤ 9.5%
    ที่ความเชื่อมั่น 95% ซึ่ง **ห่างจากมาตรฐานหน้างาน (0.0075%) อยู่ 1,270 เท่า**
    ถ้าไม่บอกข้อนี้ ผู้ใช้จะสรุปว่า "ผ่าน" จากตัวอย่างที่เล็กเกินกว่าจะพิสูจน์อะไรได้
    """
    if n <= 0:
        return None
    if k <= 0:
        return 1.0 - 0.05 ** (1.0 / n)
    if k >= n:
        return 1.0
    from math import comb

    def cdf(p):                       # P(X <= k)
        return sum(comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))

    lo, hi = k / float(n), 1.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if cdf(mid) > 0.05:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def images_needed(target_rate):
    """ต้องทดสอบกระป๋องดีกี่ใบ ถึงจะ *พิสูจน์* ได้ว่าอัตรา NG ปลอม ≤ target."""
    if not target_rate or target_rate <= 0 or target_rate >= 1:
        return None
    import math
    return int(math.ceil(math.log(0.05) / math.log(1.0 - target_rate)))


# ════════════════════════════════════════════════════════════════════
# ③ ชั้นแปลงเป็นสเปกที่ซื้อของได้
# ════════════════════════════════════════════════════════════════════
def exposure_advice(blur_px, mm_per_px, speed_mm_s, exposure_us=None,
                    mean_brightness=None):
    """เบลอกี่พิกเซล → exposure สูงสุด → ต้องเพิ่มไฟกี่เท่า."""
    if not blur_px or not mm_per_px or not speed_mm_s:
        return None
    speed_px_s = float(speed_mm_s) / float(mm_per_px)
    max_us = blur_px / speed_px_s * 1e6
    out = {"blur_px": blur_px, "speed_px_s": speed_px_s,
           "max_exposure_us": max_us}
    if exposure_us:
        # ลด exposure ลงกี่เท่า = ภาพมืดลงกี่เท่า = ต้องเพิ่มไฟกี่เท่า
        # **เพื่อให้สว่างเท่าเดิม** (ยังไม่ได้แปลว่าสว่างพอใช้งาน)
        out["light_factor_needed"] = float(exposure_us) / max_us
        if mean_brightness and mean_brightness > 0.5:
            out["light_factor_usable"] = (out["light_factor_needed"]
                                          * TARGET_MEAN / float(mean_brightness))
            out["target_mean"] = TARGET_MEAN
    return out


# ════════════════════════════════════════════════════════════════════
# ④ ตรวจตัวเอง — เครื่องมือวัดที่วัดผิดแย่กว่าไม่มีเครื่องมือ
# ════════════════════════════════════════════════════════════════════
def selftest():
    head("ตรวจตัวเครื่องมือเอง (ไม่ต้องมีโมเดล)")
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok = ok and bool(cond)
        print("  %s %s%s" % ("✅" if cond else "❌", name,
                             ("  — " + detail) if detail else ""))

    img = np.zeros((120, 200), np.uint8)
    img[50:70, 90:110] = 255
    check("เบลอ 0 px = ไม่เปลี่ยนภาพเลย",
          np.array_equal(motion_blur(img, 0), img))
    b = motion_blur(img, 21)
    xs = np.where(b.max(axis=0) > 5)[0]
    ys = np.where(b.max(axis=1) > 5)[0]
    check("เบลอแนวนอนกว้างขึ้นตามแนว x", len(xs) > 20 + 15,
          "กว้าง %d px (เดิม 20)" % len(xs))
    check("เบลอแนวนอน **ไม่** กระทบแนว y", len(ys) <= 22,
          "สูง %d px (เดิม 20)" % len(ys))
    check("เบลอไม่ทำให้ความสว่างรวมเปลี่ยน",
          abs(float(b.sum()) - float(img.sum())) / float(img.sum()) < 0.05)
    b90 = motion_blur(img, 21, angle_deg=90)
    check("หมุนเคอร์เนล 90° แล้วเบลอแนวตั้งแทน",
          len(np.where(b90.max(axis=1) > 5)[0]) > 30)

    A = {"class_name": "dent", "confidence": 0.9, "bbox": [10, 10, 30, 30]}
    check("IoU กรอบเดียวกัน = 1.0", abs(_iou(A["bbox"], A["bbox"]) - 1.0) < 1e-9)
    check("IoU กรอบที่ไม่ทับกัน = 0", _iou([0, 0, 5, 5], [50, 50, 60, 60]) == 0)
    k, i, c = match_rate([A], [A])
    check("จับคู่ตัวเองได้ 100%", k == 1.0 and i > 0.99)
    k2, _, _ = match_rate([A], [{"class_name": "dent", "confidence": .5,
                                 "bbox": [200, 200, 220, 220]}])
    check("กรอบคนละที่ = จับคู่ไม่ได้", k2 == 0.0)
    check("คลาส can/good ไม่ถูกนับเป็นตำหนิ",
          defects_of([{"class_name": "can", "confidence": 1, "bbox": [0, 0, 1, 1]}]) == [])

    rows = [{"blur": 0, "kept": 1.0}, {"blur": 1, "kept": 1.0},
            {"blur": 2, "kept": 0.98}, {"blur": 3, "kept": 0.60},
            {"blur": 4, "kept": 0.99}]
    check("หาเพดานแบบต่อเนื่อง (ไม่หลงจุดที่ผ่านหลังพังไปแล้ว)",
          knee(rows) == 2, "ได้ %s (ต้องเป็น 2 ไม่ใช่ 4)" % knee(rows))
    check("ทุกค่าไม่ผ่านเลย → สรุปไม่ได้", knee([{"blur": 0, "kept": 0.0}]) is None)

    adv = exposure_advice(3.0, 0.082, 638.0, exposure_us=2003.0, mean_brightness=20.0)
    check("exposure สูงสุด = เบลอ ÷ ความเร็วเป็นพิกเซล",
          abs(adv["max_exposure_us"] - 3.0 / (638.0 / 0.082) * 1e6) < 1e-6,
          "%.0f µs" % adv["max_exposure_us"])
    check("ตัวคูณไฟสอดคล้องกับ exposure ที่แนะนำ",
          abs(adv["light_factor_needed"] - 2003.0 / adv["max_exposure_us"]) < 1e-9,
          "%.1f เท่า" % adv["light_factor_needed"])
    check("แยก 'สว่างเท่าเดิม' กับ 'สว่างพอใช้งาน'",
          adv["light_factor_usable"] > adv["light_factor_needed"],
          "%.1f vs %.1f เท่า" % (adv["light_factor_usable"], adv["light_factor_needed"]))

    # เดินทั้งเส้นทาง: "รอยบุบ" จำลองเป็นลายเส้นคาบ 4 px ซึ่งการเฉลี่ยตามแนว
    # ยาว 4 px จะกลืนหายพอดีในทางคณิตศาสตร์ ⇒ รู้คำตอบล่วงหน้าว่าเพดานต้องอยู่
    # ก่อน 4 px (ภาพสี่เหลี่ยมทึบใช้ไม่ได้ — ขอบคมเกินกว่าที่การเบลอจะลบทิ้ง)
    fine = np.full((120, 200), 40, np.uint8)
    fine[50:70, 90:110] = np.tile(np.array([220, 220, 40, 40], np.uint8), 5)

    def fake_detect(im):
        g = im if im.ndim == 2 else cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
        if float(g[50:70, 90:110].std()) < 30:        # อ่านลายไม่ออกแล้ว
            return []
        return [{"class_name": "dent", "confidence": 0.9, "bbox": [90, 50, 110, 70]}]

    res = sweep(fake_detect, [fine], blurs=(0, 1, 2, 3, 4, 6, 10))
    lim = knee(res[1.0]["rows"])
    check("เดินทั้งเส้นทางแล้วหาเพดานของโมเดลปลอมได้ตรงจุดที่รู้คำตอบอยู่แล้ว",
          lim is not None and 1 <= lim <= 4, "เพดาน %s px (คำตอบที่ถูกคือ < 4)" % lim)
    res_all = sweep(fake_detect, [fine], blurs=(0, 20))
    check("โมเดลที่ตาบอดสนิทตอนเบลอมาก ต้องถูกจับได้",
          res_all[1.0]["rows"][-1]["kept"] == 0.0)
    print()
    return 0 if ok else 1


# ════════════════════════════════════════════════════════════════════
# ⑤ CLI
# ════════════════════════════════════════════════════════════════════
def load_images(folder, limit=0):
    files = sorted(f for f in glob.glob(os.path.join(folder, "*"))
                   if f.lower().endswith(IMG_EXT))
    if limit:
        files = files[:limit]
    imgs, names = [], []
    for f in files:
        im = cv2.imread(f)
        if im is not None:
            imgs.append(im)
            names.append(os.path.basename(f))
    return imgs, names


def main():
    ap = argparse.ArgumentParser(description="วัดว่าโมเดลทนความเบลอได้กี่พิกเซล")
    ap.add_argument("--images", help="โฟลเดอร์ภาพ NG จริง (ยิ่งหลากหลายยิ่งดี ≥20 ใบ)")
    ap.add_argument("--mode", default="can_dent")
    ap.add_argument("--model", default=None, help="ชื่อไฟล์ .pt (ค่าเริ่มต้น = ที่ระบบเลือกเอง)")
    ap.add_argument("--imgsz", type=int, default=None)
    ap.add_argument("--limit", type=int, default=0, help="ใช้แค่ N ภาพแรก")
    ap.add_argument("--blurs", default=None,
                    help="ความยาวเบลอที่จะไล่ (px) คั่นด้วย comma")
    ap.add_argument("--scales", default="1.0",
                    help="จำลองความละเอียดอื่น เช่น 1.0,0.75,0.5")
    ap.add_argument("--angle", type=float, default=0.0,
                    help="ทิศที่ชิ้นงานวิ่ง (องศา, 0 = แนวนอน)")
    ap.add_argument("--keep-frac", type=float, default=KEEP_FRAC)
    ap.add_argument("--mm-per-px", type=float, default=None)
    ap.add_argument("--speed-mm-s", type=float, default=None)
    ap.add_argument("--exposure-us", type=float, default=None)
    ap.add_argument("--mean-brightness", type=float, default=None,
                    help="ความสว่างเฉลี่ยที่วัดได้ตอนนี้ (0-255)")
    ap.add_argument("--ok-images",
                    help="โฟลเดอร์ภาพ **กระป๋องดี** — วัด NG ปลอมที่ความเบลอเดียวกัน "
                         "(ทิศทางที่ recall วัดแทนไม่ได้)")
    ap.add_argument("--fp-allow", type=int, default=FP_ALLOW,
                    help="ยอมให้มีใบที่โดนทักเพิ่มจากฐานกี่ใบ (ค่าเริ่มต้น 0 = เข้มที่สุด)")
    ap.add_argument("--fp-target", type=float, default=0.000075,
                    help="อัตรา NG ปลอมที่ต้องการพิสูจน์ (ค่าเริ่มต้น 0.0075%% = "
                         "เพดานบนของระบบเดิมบนไลน์ที่ทำได้ 0/40,062)")
    ap.add_argument("--save-dir", help="เซฟภาพที่เบลอไว้ดูด้วยตา")
    ap.add_argument("--json", help="เขียนผลเป็นไฟล์ JSON")
    ap.add_argument("--selftest", action="store_true",
                    help="ตรวจว่าตัวเครื่องมือเองทำงานถูก (ไม่ต้องมีโมเดล/ภาพ)")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.images:
        print("ต้องระบุ --images <โฟลเดอร์ภาพ NG>  (หรือ --selftest)")
        return 2

    head("① ภาพที่ใช้วัด")
    imgs, names = load_images(args.images, args.limit)
    if not imgs:
        print("ไม่พบภาพใน %s" % args.images)
        return 2
    print("  โฟลเดอร์ : %s" % args.images)
    print("  จำนวน    : %d ภาพ" % len(imgs))
    # dump_defect_images.py เขียนสะสมในโฟลเดอร์เดิม ⇒ รันซ้ำแล้วภาพจะเพิ่มขึ้นเรื่อย ๆ
    if len(imgs) > 40 and not args.limit:
        print("  ⚠️ ภาพเยอะ (โฟลเดอร์นี้สะสมจากการรัน dump_defect_images หลายครั้ง)")
        print("     ใช้ --limit 30 เพื่อให้เร็วขึ้น หรือล้างโฟลเดอร์ก่อนดึงใหม่")
    if len(imgs) < 20:
        print("  ⚠️ น้อยกว่า 20 ภาพ — ผลจะแกว่ง ใช้ตัดสินใจซื้อของยังไม่ควร")

    head("② โหลดโมเดล (เส้นทางเดียวกับ app.py)")
    try:
        from hik_offline_eval import load_detector
        det, path = load_detector(args.mode, args.model)
    except Exception as e:
        print("  โหลดโมเดลไม่สำเร็จ: %s" % e)
        return 2
    if det is None or det.model is None:
        print("  โมเดลไม่พร้อมใช้งาน")
        return 2
    print("  โมเดล : %s" % path)
    print("  imgsz : %s" % (args.imgsz or "ค่าเริ่มต้นของโหมด"))

    def detect_fn(im):
        return det.detect(im, imgsz=args.imgsz) if args.imgsz else det.detect(im)

    blurs = ([float(x) for x in args.blurs.split(",")] if args.blurs
             else list(DEFAULT_BLURS))
    scales = [float(x) for x in args.scales.split(",")]

    head("③ ไล่ความเบลอ")
    # จับเวลา 1 ครั้งจริง แล้วบอกไปเลยว่าจะใช้เวลาเท่าไร — ไม่งั้นผู้ใช้ไม่รู้ว่า
    # ต้องรอ 1 นาทีหรือ 20 นาที แล้วกด Ctrl+C ทิ้งกลางคัน (เกิดขึ้นจริงมาแล้ว)
    import time as _t
    _t0 = _t.perf_counter()
    detect_fn(imgs[0])
    per = _t.perf_counter() - _t0
    runs = len(imgs) * (len(blurs) + 1) * len(scales)
    print("  ภาพ %d × (เบลอ %d ระดับ + ฐาน) × สเกล %d = **ตรวจ %d ครั้ง**"
          % (len(imgs), len(blurs), len(scales), runs))
    print("  ครั้งละ ~%.0f ms ⇒ **ใช้เวลาราว %d นาที %d วินาที**"
          % (per * 1000, int(runs * per) // 60, int(runs * per) % 60))
    if runs * per > 600:
        print("  ⚠️ นานเกิน 10 นาที — ลด --limit (เช่น --limit 25) หรือลด --blurs ได้")
    print()
    done = {"n": 0}
    total = len(blurs) * len(scales)

    def prog(scale, L):
        done["n"] += 1
        sys.stdout.write("\r  กำลังวัด %d/%d (สเกล %.2f · เบลอ %s px)   "
                         % (done["n"], total, scale, L))
        sys.stdout.flush()

    res = sweep(detect_fn, imgs, blurs=blurs, scales=scales,
                angle=args.angle, progress=prog)
    print()

    report = {"images": args.images, "n_images": len(imgs), "model": path,
              "imgsz": args.imgsz, "keep_frac": args.keep_frac, "scales": {}}
    code = 0
    for scale in scales:
        r = res[scale]
        rows = r["rows"]
        head("④ ผล — สเกล %.2f  (ใช้ได้ %d/%d ภาพ)"
             % (scale, r["images_used"], r["images_total"]))
        if not r["images_used"]:
            print("  ❌ โมเดลไม่พบรอยบุบเลยแม้แต่ภาพที่ไม่เบลอ ⇒ วัดการเสื่อมไม่ได้")
            print("     (ภาพชุดนี้อาจไม่ใช่ NG หรือมาจากกล้อง/แสงคนละแบบกับที่โมเดลเคยเห็น)")
            code = max(code, 3)
            continue
        print("  %-10s %-12s %-12s %s" % ("เบลอ(px)", "ยังตรวจเจอ", "กรอบตรง(IoU)", "ความมั่นใจ"))
        base = rows[0]["kept"]
        for row in rows:
            pass_mark = "" if row["kept"] is None else (
                "" if row["kept"] >= base * args.keep_frac else "  ← ต่ำกว่าเกณฑ์")
            print("  %-10s %-12s %-12s %s%s" % (
                row["blur"],
                "—" if row["kept"] is None else "%.0f%%" % (row["kept"] * 100),
                "—" if row["iou"] is None else "%.2f" % row["iou"],
                "—" if row["conf"] is None else "%.2f" % row["conf"],
                pass_mark))
        lim = knee(rows, args.keep_frac)
        report["scales"][str(scale)] = {"rows": rows, "limit_px": lim,
                                        "images_used": r["images_used"]}
        print()
        if lim is None:
            print("  ❌ สรุปเพดานไม่ได้")
            code = max(code, 3)
            continue
        print("  ⇒ **โมเดลทนเบลอได้ ≤ %g พิกเซล** (ยังเจอ ≥%.0f%% ของตอนไม่เบลอ)"
              % (lim, args.keep_frac * 100))
        if lim >= max(blurs):
            print("     ⚠️ ยังไม่ถึงจุดพังในช่วงที่ไล่ — เพิ่ม --blurs ให้สูงขึ้น")
            code = max(code, 1)

        adv = exposure_advice(lim, args.mm_per_px, args.speed_mm_s,
                              args.exposure_us, args.mean_brightness)
        if adv:
            print("\n  📏 แปลงเป็นสเปกที่ซื้อของได้ (ที่ %.0f mm/s · %.4f mm/px)"
                  % (args.speed_mm_s, args.mm_per_px))
            print("     ความเร็วเป็นพิกเซล : %.0f px/วินาที" % adv["speed_px_s"])
            print("     exposure สูงสุด    : **%.0f µs**" % adv["max_exposure_us"])
            if "light_factor_needed" in adv:
                print("     ต้องเพิ่มไฟ        : **%.1f เท่า** (ให้สว่างเท่าเดิมที่ %.0f µs)"
                      % (adv["light_factor_needed"], args.exposure_us))
            if "light_factor_usable" in adv:
                print("                        : **%.1f เท่า** (ให้สว่างพอใช้งาน ~%.0f/255)"
                      % (adv["light_factor_usable"], adv["target_mean"]))
            report["scales"][str(scale)]["advice"] = adv
        else:
            print("     (ใส่ --mm-per-px --speed-mm-s --exposure-us เพื่อให้แปลงเป็น µs ให้)")

    # ── ⑤ ทิศทางที่สอง: NG ปลอมบนกระป๋องดี ────────────────────────────
    if args.ok_images:
        ok_imgs, _ = load_images(args.ok_images, args.limit)
        head("⑤ NG ปลอม — ภาพกระป๋องดีที่เบลอเท่ากัน")
        if not ok_imgs:
            print("  ไม่พบภาพใน %s" % args.ok_images)
            code = max(code, 2)
        else:
            print("  โฟลเดอร์ : %s · %d ภาพ\n" % (args.ok_images, len(ok_imgs)))
            fres = sweep_false(detect_fn, ok_imgs, blurs=blurs, scales=scales,
                               angle=args.angle)
            report["false_positive"] = {"images": args.ok_images,
                                        "n_images": len(ok_imgs), "scales": {}}
            for scale in scales:
                fr = fres[scale]
                rows = fr["rows"]
                print("  สเกล %.2f" % scale)
                print("  %-10s %-16s %-10s %s"
                      % ("เบลอ(px)", "ใบที่โดนทัก", "กรอบรวม", "ความมั่นใจสูงสุด"))
                for row in rows:
                    print("  %-10s %-16s %-10s %s" % (
                        row["blur"],
                        "%d/%d (%.1f%%)" % (row["fp_images"], fr["images"],
                                            (row["fp_rate"] or 0) * 100),
                        row["fp_boxes"],
                        "—" if row["conf"] is None else "%.2f" % row["conf"]))
                flim = false_knee(rows, args.fp_allow)
                base_fp = rows[0]["fp_images"] if rows else 0
                report["false_positive"]["scales"][str(scale)] = {
                    "rows": rows, "limit_px": flim, "base_fp_images": base_fp}
                print()
                if base_fp:
                    print("  ⚠️ มี NG ปลอม %d/%d ใบ **ตั้งแต่ยังไม่เบลอ** ⇒ ปัญหานี้ไม่ได้"
                          "เกิดจากความเบลอ" % (base_fp, fr["images"]))
                    print("     แก้ที่โมเดล/เกณฑ์ conf ก่อน — ไม่ใช่ที่ exposure หรือไฟ")
                    code = max(code, 1)
                if flim is None:
                    print("  ❌ สรุปเพดานด้าน NG ปลอมไม่ได้")
                    code = max(code, 3)
                else:
                    print("  ⇒ **เบลอได้ ≤ %g พิกเซล โดยไม่เพิ่ม NG ปลอม**" % flim)
                    lim_recall = report["scales"].get(str(scale), {}).get("limit_px")
                    if lim_recall is not None:
                        binding = min(flim, lim_recall)
                        who = "NG ปลอม" if flim <= lim_recall else "ยังตรวจเจอ (recall)"
                        print("     รวมสองด้าน: **≤ %g พิกเซล** (ตัวจำกัดคือ %s)"
                              % (binding, who))
                        report["false_positive"]["scales"][str(scale)]["binding_px"] = binding

                # ── ความซื่อสัตย์ทางสถิติ: ตัวอย่างเท่านี้พิสูจน์อะไรได้แค่ไหน ──
                worst = max(r["fp_images"] for r in rows) if rows else 0
                ub = upper_bound_95(worst, fr["images"])
                need = images_needed(args.fp_target)
                print("\n  📐 ตัวอย่าง %d ใบ พิสูจน์ได้แค่ไหน" % fr["images"])
                print("     แย่สุดที่วัดได้ %d/%d ⇒ อัตราจริง **≤ %.2f%%** (เชื่อมั่น 95%%)"
                      % (worst, fr["images"], (ub or 0) * 100))
                if need:
                    print("     เป้าหมาย %.4f%% (ระบบเดิมบนไลน์ทำได้ 0/40,062) ต้องใช้ "
                          "**≥ %s ใบ** จึงจะพิสูจน์ได้" % (args.fp_target * 100,
                                                          format(need, ",")))
                print("     ⇒ ชุดเล็กใช้ **เรียงลำดับว่าเบลอเท่าไรแย่ลง** ได้ "
                      "แต่ **ยืนยันว่าผ่านมาตรฐานหน้างานไม่ได้**")
    else:
        head("⑤ NG ปลอม — ยังไม่ได้วัด")
        print("  เครื่องมือนี้วัดแต่ \"ยังเจอไหม\" (recall) — **ยังไม่ได้ตอบว่า"
              "ความเบลอทำให้เจอของที่ไม่มีหรือเปล่า**")
        print("  ระบบ vision เดิมบนไลน์ทำได้ NG rate 0.00% (0/40,062) ⇒ ตัวจำกัดจริง"
              "ของงานนี้คือ precision")
        print("  เติม --ok-images <โฟลเดอร์ภาพกระป๋องดี> เพื่อปิดช่องนี้")
        code = max(code, 1)

    if args.save_dir and imgs:
        os.makedirs(args.save_dir, exist_ok=True)
        for L in blurs:
            cv2.imwrite(os.path.join(args.save_dir, "blur_%02d.jpg" % int(L)),
                        motion_blur(imgs[0], L, args.angle))
        print("\n  เซฟภาพตัวอย่างที่เบลอไว้ที่ %s (ดูด้วยตาว่าเบลอเท่านี้ยังอ่านออกไหม)"
              % args.save_dir)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print("  เขียนผลลง %s" % args.json)

    head("สรุป")
    print("  ตัวเลขนี้ใช้แทนค่าที่เดาไว้ใน hik_offline_eval.py (--blur-limit-mm)")
    print("  ⚠️ เบลอสังเคราะห์ ≠ เบลอจริง 100% — ใช้ตั้งงบ แล้ววัดซ้ำด้วยภาพจริงเมื่อมีไฟ")
    return code


if __name__ == "__main__":                            # pragma: no cover
    sys.exit(main())
