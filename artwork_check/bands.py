# -*- coding: utf-8 -*-
"""หั่นภาพโซนเป็น "แถบแนวนอน" ตามช่องว่างระหว่างบรรทัด ก่อนส่งให้ OCR.

ทำไมต้องหั่น
------------
Gemini หั่นภาพที่รับมาเป็นไทล์ ``clamp(min(W,H)/1.5, 256, 768)`` แล้ว
**ขยายทุกไทล์เป็น 768x768** ⇒ กำลังขยายที่โมเดลเห็น = ``768 / tile``.
ยิ่งด้านสั้นของภาพใหญ่ ยิ่งได้ขยายน้อย และตันที่ 1152 px (ไม่ขยายเลย).

⚠️ **ย่อภาพลงไม่ช่วย** — วัดแล้วบนโซนจริง (1238x1173, บรรทัดสูง 71.1 px):
ย่อด้านสั้นเป็น 1100/1000/900/768/600 ได้ "บรรทัดในสายตาโมเดล" 69.8 px
เท่ากันหมด และไทล์ยังเป็น 4 เท่าเดิม เพราะการย่อไม่เปลี่ยนสัดส่วนภาพ
(ตัวหนังสือเล็กลงตามสัดส่วน แล้วโมเดลขยายคืนพอดี = หักล้างกันเกลี้ยง).

สิ่งที่ได้ผลคือ **ตัดขอบ/แบ่งชิ้น** — เนื้อหายังอยู่ที่ dpi เดิม แต่ด้านสั้น
ของภาพที่ส่งลดลงจริง วัดบนโซนเดียวกัน:

    ไม่ทำอะไร      1238x1173 -> 4 ไทล์  ขยาย 1.00x -> บรรทัด  71 px
    เตี้ยลงเป็น 900 1238x900  -> 6 ไทล์  ขยาย 1.28x -> บรรทัด  91 px
    หั่นครึ่ง       1238x586  -> 8 ไทล์  ขยาย 1.96x -> บรรทัด 140 px
    หั่นเป็น 4 แถบ  1238x293  -> 10 ไทล์ ขยาย 3.00x -> บรรทัด 213 px

และผลพลอยได้ที่สำคัญพอกัน: แต่ละคำขอมี "แถวที่ซ้ำกัน" น้อยลง ⇒ ลดอาการ
โมเดลรวบ/ข้ามแถวที่เหมือนกัน (``٠ جم`` ซ้ำ 6 แถวแล้วพิมพ์มา 5)

กติกาที่ยึด
-----------
* **ห้ามตัดผ่านตัวหนังสือเด็ดขาด** — ตัดได้เฉพาะแถวที่ "เงียบ" (แทบไม่มีขอบ)
  ติดกันยาวพอ. หาจุดตัดไม่ได้ = ไม่หั่น (คืนลิสต์ว่าง) ไม่ใช่หั่นมั่ว.
* วัดพลังงานขอบเฉพาะ **คอลัมน์กลาง** — ขอบกรอบ/เส้นตารางแนวตั้งที่ริมภาพ
  ทำให้ทุกแถวดู "ไม่เงียบ" จนหาช่องว่างไม่เจอ
* ไม่มี Flask / ไม่เรียก OCR ⇒ ทดสอบได้ตรง ๆ
"""

from __future__ import annotations

from typing import List, Tuple

import cv2
import numpy as np

# ── ค่าจูน (ที่มาของตัวเลขอยู่ใน docstring ข้างบน) ────────────────────
# ด้านสั้นของแถบที่ทำให้ Gemini ให้กำลังขยายสูงสุด: tile จะถูกบีบไปที่ 256
# เมื่อ min(W,H)/1.5 <= 256 ⇒ min(W,H) <= 384
BAND_TARGET_PX = 380
# แถบที่เตี้ยกว่านี้ = เสี่ยงตัดบริบทจนโมเดลเดา ⇒ ไม่ยอมตัดให้เตี้ยกว่านี้
BAND_MIN_PX = 150
# ช่องว่างต้องสูงอย่างน้อยเท่านี้ถึงจะถือว่า "ตัดตรงนี้ปลอดภัย"
MIN_GAP_PX = 4
# แถวที่พลังงานขอบต่ำกว่านี้ (สัดส่วนของแถวที่แน่นที่สุด) = แถวเงียบ
QUIET_RATIO = 0.06
# กันไม่ให้ยิง OCR เยอะเกินจำเป็นต่อโซนเดียว
MAX_BANDS = 6
# ตัดคอลัมน์ริมทิ้งตอนวัด — ขอบกรอบ/เส้นตารางแนวตั้งอยู่ตรงนั้น
_EDGE_TRIM = 0.08
# ⚠️ พลังงานถูก normalize ด้วยแถวที่แน่นที่สุด ⇒ ภาพที่ "เงียบเกือบทั้งใบ"
#    (ทึบล้วน / ว่างล้วน / มีเส้นเด่นเส้นเดียวแล้วที่เหลือจาง) จะดูเหมือน
#    เป็นช่องว่างทั้งภาพ แล้วตัวหาจุดตัดจะตัดกลางเนื้อหา — เทสต์จับได้จริง
#    ตอนป้อนภาพดำล้วน. เกินสัดส่วนนี้ = ไม่ใช่ตารางข้อความ ⇒ ไม่หั่น
MAX_QUIET_FRAC = 0.85


def _row_energy(img: np.ndarray) -> np.ndarray:
    """พลังงานขอบต่อแถว (0..1) — สูง = แถวนั้นมีตัวหนังสือ/เส้น"""
    gray = (cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            if img.ndim == 3 else img).astype(np.float32)
    e = (np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)) +
         np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)))
    w = e.shape[1]
    lo, hi = int(_EDGE_TRIM * w), int((1.0 - _EDGE_TRIM) * w)
    if hi - lo < 8:                      # ภาพแคบมาก — ใช้ทั้งความกว้าง
        lo, hi = 0, w
    rows = e[:, lo:hi].sum(axis=1)
    peak = float(rows.max())
    return rows / peak if peak > 0 else rows


def quiet_gaps(img: np.ndarray, min_gap: int = MIN_GAP_PX) -> List[Tuple[int, int]]:
    """ช่วงแถวที่ "เงียบ" ติดกันยาว >= ``min_gap`` — จุดที่ตัดได้โดยไม่โดนตัวหนังสือ"""
    rows = _row_energy(img)
    if float(rows.max()) <= 0.0:
        return []                        # ทึบล้วน/ว่างล้วน — ไม่มีโครงสร้างให้ตัด
    quiet = rows < QUIET_RATIO
    if float(quiet.mean()) > MAX_QUIET_FRAC:
        return []                        # แทบไม่มีเนื้อหา — normalize แล้วเชื่อไม่ได้
    out: List[Tuple[int, int]] = []
    start = None
    for y, q in enumerate(quiet):
        if q and start is None:
            start = y
        elif not q and start is not None:
            if y - start >= min_gap:
                out.append((start, y))
            start = None
    if start is not None and len(quiet) - start >= min_gap:
        out.append((start, len(quiet)))
    return out


def find_bands(img: np.ndarray,
               target_px: int = BAND_TARGET_PX,
               min_px: int = BAND_MIN_PX,
               max_bands: int = MAX_BANDS) -> List[Tuple[int, int]]:
    """แบ่ง ``img`` เป็นแถบแนวนอน ``[(y0, y1), ...]`` โดยตัดเฉพาะช่องว่าง.

    คืน ``[]`` เมื่อ **ไม่ควรหั่น** — ภาพเตี้ยอยู่แล้ว, หาช่องว่างที่ปลอดภัย
    ไม่เจอ, หรือหั่นแล้วได้แถบเดียว. ผู้เรียกต้องถือว่า ``[]`` = ใช้ทางเดิม.
    """
    if img is None or getattr(img, "size", 0) == 0:
        return []
    h = int(img.shape[0])
    # เตี้ยพอที่จะได้กำลังขยายเต็มอยู่แล้ว หรือหั่นแล้วจะต่ำกว่าขั้นต่ำ
    if h <= target_px or h < 2 * min_px:
        return []

    gaps = quiet_gaps(img)
    # ช่องว่างหัว-ท้ายภาพไม่ใช่จุดตัด (ตัดแล้วได้แถบว่าง)
    mids = [(a + b) // 2 for a, b in gaps
            if min_px <= (a + b) // 2 <= h - min_px]
    if not mids:
        return []

    cuts: List[int] = []
    last = 0
    for m in mids:
        if len(cuts) + 1 >= max_bands:
            break
        # ตัดเมื่อแถบที่กำลังสะสมถึงเป้า และส่วนที่เหลือยังยาวพอ
        if m - last >= target_px * 0.6 and h - m >= min_px:
            cuts.append(m)
            last = m
    if not cuts:
        return []

    bands = list(zip([0] + cuts, cuts + [h]))
    # แถบสุดท้ายอาจสั้นกว่าขั้นต่ำถ้าช่องว่างอยู่ใกล้ท้ายภาพ — รวมกับแถบก่อน
    if len(bands) >= 2 and bands[-1][1] - bands[-1][0] < min_px:
        bands = bands[:-2] + [(bands[-2][0], bands[-1][1])]
    return bands if len(bands) >= 2 else []
