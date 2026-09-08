# -*- coding: utf-8 -*-
"""แยก "ตัวอักษรต่าง" ออกจาก "รูปลักษณ์ต่าง" — และวัดว่าต่างยังไง.

ที่มา (ผลรันจริงบนสถานี 8 ก.ย. 2026)
------------------------------------
โหมดเทียบพิกเซลจับความต่างจริงได้ **2 รายการ** บนคู่ไฟล์ John West:

  กลุ่ม A: ``Sodium 20% → 24%``        = ตัวอักษรต่างกันจริง
  กลุ่ม B: ``Manufacturing Co., Ltd…`` = **ตัวอักษรเหมือนกันเป๊ะ แต่ฟอนต์ต่าง**
           (ต้นฉบับ = ตัวหนา · โรงพิมพ์ = ตัวธรรมดา)
           วัดได้: หมึก 24.19% vs 21.15% · ความหนาเส้น 8.80 vs 7.62 px

แต่การ์ดกลับขึ้นว่า **"พบ: Manuf เทียบกับ: Manufa"** ซึ่ง

  1. ทำให้ผู้ตรวจไปตามหา **คำสะกดผิด** ที่ไม่มีอยู่จริง (กฎเหล็กข้อ 2)
  2. เกิดจาก ``pad = 14 px`` ตายตัว + bbox ครอบเฉพาะ *พิกเซลที่ต่าง*
     (ตัวหนา/ธรรมดาต่างกันมากสุดที่ต้นคำ) ⇒ ครอปตัดกลางคำ
  3. สองฝั่งอ่านครอป **ตำแหน่งเดียวกัน** ได้คนละอย่าง (``Manuf``/``Manufa``)
     ⇒ ยืนยันว่าเป็น **สิ่งประดิษฐ์จากการครอป** ไม่ใช่ความต่างของงาน

โมดูลนี้จึงทำ 3 อย่าง — **ทั้งหมดเป็นเรขาคณิต/Unicode ล้วน จึงใช้ได้ทุกภาษา**
(ละติน · อาหรับ · ไทย · จีน · ญี่ปุ่น · ฮีบรู · ซีริลลิก …):

  ① ``expand_box``  ขยายครอปออกจนถึง "ช่องว่างที่ว่างทั้งสองฝั่ง" = ขอบคำ
  ② ``relation``    ตัดสินว่าข้อความสองฝั่ง เหมือน / ถูกตัด / ต่างจริง
  ③ ``look_delta``  วัดว่ารูปลักษณ์ต่างยังไง (น้ำหนักเส้น · ขนาด · สี)

⚠️ **ไม่มีที่ใดในไฟล์นี้ผูกกับภาษาใดภาษาหนึ่ง** — ไม่มีรายการคำ ไม่มี
   dictionary ไม่มีการเดาสคริปต์ ใช้ได้กับเอกสารภาษาอะไรก็ตาม
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, Optional, Tuple

import cv2
import numpy as np

# ── ค่าจูน (ที่มาของตัวเลขอยู่ใน docstring ของแต่ละฟังก์ชัน) ─────────
INK_DELTA = 40          # ต่างจากพื้นหลังเกินนี้ = "มีหมึก" (0-255)
# ช่องว่างต้องกว้างเท่าไรจึงนับว่าเป็น "ขอบคำ" (ไม่ใช่ช่องว่างระหว่างตัวอักษร)
# ⚠️ **ต้องเป็นสัดส่วนของความสูงบรรทัด ไม่ใช่พิกเซลตายตัว** — วัดจริงบน
#    ข้อความเดียวกันที่ 3 ความละเอียด:
#       dpi 300: ช่องว่างตัวอักษร 2-11 px · ช่องว่างคำ 28-35 px · 0.22xสูง = 17
#       dpi 600: ช่องว่างตัวอักษร 7-22 px · ช่องว่างคำ 56-70 px · 0.22xสูง = 35
#       dpi 862: ช่องว่างตัวอักษร 10-32 px · ช่องว่างคำ 82-100 px · 0.22xสูง = 50
#    ⇒ 0.22 อยู่กลางระหว่างสองค่าเสมอ (เผื่อ ~1.5 เท่าทั้งสองด้าน)
#    ค่าคงที่ 2 px หยุดที่ **ช่องว่างระหว่างตัวอักษร** ⇒ ยังได้คำไม่ครบ
GAP_MIN_FRAC = 0.22
GAP_MIN_PX = 2          # พื้นขั้นต่ำ (กรณีวัดความสูงบรรทัดไม่ได้)
EXPAND_CAP_FRAC = 3.0   # ขยายได้ไม่เกินกี่เท่าของด้านนั้น
# ...หรือกี่เท่าของ "ความสูงบรรทัด" (แล้วแต่ค่าไหนมากกว่า)
# ⚠️ **เพดานเป็นพิกเซลตายตัวใช้ไม่ได้** — คำยาว ๆ ที่ 862 dpi กว้างเป็น
#    พันพิกเซล ⇒ เพดาน 400 px ทำให้ขยายไม่ถึงขอบคำ (วัดแล้ว: dpi 300 ถึง
#    แต่ dpi 600/862 ไม่ถึง). คำ 13 ตัวอักษรกว้างราว 7 เท่าของความสูง
#    ⇒ ตั้ง 8 เท่าเผื่อไว้ และไม่เกินครึ่งความกว้างภาพ
EXPAND_CAP_LINES = 8.0
EXPAND_CAP_IMG_FRAC = 0.5
PAD_MIN_PX = 8          # เผื่อขอบขั้นต่ำเสมอ

WEIGHT_TOL = 0.08       # ความหนาเส้นต่างเกินนี้ = คนละน้ำหนักฟอนต์
SIZE_TOL = 0.08         # ความสูงหมึกต่างเกินนี้ = คนละขนาด
COLOR_TOL = 25.0        # สีของหมึกต่างเกินนี้ (0-255 ต่อช่อง) = คนละสี

# ตัวเลขประจำสคริปต์ที่ "เป็นเลขตัวเดียวกันแต่เขียนคนละแบบ" — พับให้เป็น
# 0-9 ก่อนเทียบ ไม่งั้น ٤٧٥ กับ 475 จะถูกนับว่าเป็นคนละข้อความ
_DIGIT_MAPS = (
    ("٠", "٩"),   # Arabic-Indic  ٠-٩
    ("۰", "۹"),   # Extended Arabic-Indic (เปอร์เซีย) ۰-۹
    ("๐", "๙"),   # ไทย ๐-๙
    ("०", "९"),   # เทวนาครี ०-९
)
_WS = re.compile(r"\s+")


def norm_text(s: Optional[str]) -> str:
    """ทำข้อความให้เทียบกันได้โดยไม่ผูกกับภาษา.

    NFKC (พับรูปแบบการแสดงผลของอาหรับ/CJK ให้เป็นรูปมาตรฐาน) → ตัด
    combining mark (สระ/วรรณยุกต์/harakat) → พับเลขประจำสคริปต์เป็น 0-9 →
    ยุบช่องว่าง. **ไม่ lowercase** เพราะบางภาษาไม่มี case และการพับ case
    อาจกลืนความต่างจริง (``NET`` vs ``Net`` คือความต่างของงานพิมพ์)
    """
    if not s:
        return ""
    t = unicodedata.normalize("NFKC", str(s))
    t = "".join(c for c in t if not unicodedata.combining(c))
    for lo, hi in _DIGIT_MAPS:
        for i in range(10):
            t = t.replace(chr(ord(lo) + i), str(i))
    return _WS.sub(" ", t).strip()


def relation(a: Optional[str], b: Optional[str]) -> str:
    """ความสัมพันธ์ของข้อความสองฝั่ง → หนึ่งใน 4 ค่า.

    * ``"unknown"``   — อ่านไม่ได้อย่างน้อยหนึ่งฝั่ง ⇒ ไม่ตัดสิน
    * ``"same"``      — ตัวอักษรเหมือนกัน ⇒ ความต่างอยู่ที่ **รูปลักษณ์**
    * ``"truncated"`` — ฝั่งหนึ่งเป็นส่วนหนึ่งของอีกฝั่ง ⇒ **ครอปตัดคำ**
      (ไม่ใช่ความต่างของงาน) ⇒ ห้ามเอาไปแสดงเป็น "พบ X เทียบกับ Y"
    * ``"different"`` — ต่างกันจริง

    ทำงานกับทุกภาษาเพราะเทียบสตริงหลัง normalize ล้วน ๆ
    """
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return "unknown"
    if na == nb:
        return "same"
    # สั้นกว่าและเป็นชิ้นส่วนของอีกอัน = ครอปตัดคำ (หัว/ท้าย/กลาง)
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(short) >= 2 and short in long_:
        return "truncated"
    return "different"


# ── ② หมึกและรูปลักษณ์ — เรขาคณิตล้วน ไม่รู้จักภาษา ──────────────────
def _ink_mask(img) -> np.ndarray:
    """พิกเซลที่ "เป็นหมึก" = ต่างจากพื้นหลังเกิน :data:`INK_DELTA`.

    ใช้ค่ากลางของภาพเป็นพื้นหลัง ⇒ ใช้ได้ทั้งตัวเข้มบนพื้นอ่อนและตัวอ่อน
    บนพื้นเข้ม (ฉลากมีทั้งสองแบบ) และไม่ต้องรู้ว่าเป็นภาษาอะไร
    """
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    bg = float(np.median(g))
    return (np.abs(g.astype(np.int16) - bg) > INK_DELTA).astype(np.uint8)


def ink_stats(img) -> Dict[str, float]:
    """สถิติของหมึกในภาพ — ``ink`` (สัดส่วน) · ``stroke`` (ความหนาเส้น px)
    · ``height`` (ความสูงของแถบที่มีหมึก) · ``color`` (สีเฉลี่ยของหมึก).

    ``stroke`` ประมาณจาก *พื้นที่หมึก ÷ ครึ่งหนึ่งของเส้นรอบรูป* ซึ่งเป็น
    ค่าความหนาเฉลี่ยของเส้นในเชิงเรขาคณิต — วัดบนไฟล์จริงได้ 8.80 px
    (ตัวหนา) vs 7.62 px (ตัวธรรมดา) ของบรรทัดเดียวกัน
    """
    if img is None or getattr(img, "size", 0) == 0:
        return {}
    m = _ink_mask(img)
    area = float(m.sum())
    if area <= 0:
        return {"ink": 0.0, "stroke": 0.0, "height": 0.0, "color": None}
    er = cv2.erode(m, np.ones((3, 3), np.uint8), iterations=1)
    edge = max(1.0, (area - float(er.sum())) / 2.0)
    rows = np.where(m.sum(axis=1) > 0)[0]
    col = None
    if img.ndim == 3:
        sel = m.astype(bool)
        col = [float(img[:, :, i][sel].mean()) for i in range(3)]
    return {"ink": round(area / float(m.size), 5),
            "stroke": round(area / edge, 3),
            "height": float(rows[-1] - rows[0] + 1) if len(rows) else 0.0,
            "color": col}


def _rel(x: float, y: float) -> float:
    hi = max(abs(x), abs(y))
    return abs(x - y) / hi if hi > 1e-9 else 0.0


def look_delta(img_a, img_b) -> Dict[str, object]:
    """ต่างกันที่ **รูปลักษณ์** ยังไง → ``{"kind", "note", "a", "b"}``.

    ``kind`` เป็นหนึ่งใน ``weight`` (น้ำหนักฟอนต์) · ``size`` (ขนาด) ·
    ``color`` (สี) · ``other`` (บอกไม่ได้ว่าอะไร แต่ต่างจริง) ·
    ``none`` (วัดไม่ได้). **ไม่เดา** — ถ้าวัดไม่ได้คืน ``none``
    """
    sa, sb = ink_stats(img_a), ink_stats(img_b)
    out = {"kind": "none", "note": "", "a": sa, "b": sb}
    if not sa or not sb or not sa.get("stroke") or not sb.get("stroke"):
        return out
    dw = _rel(sa["stroke"], sb["stroke"])
    dh = _rel(sa["height"], sb["height"]) if sa["height"] and sb["height"] else 0.0
    dc = 0.0
    if sa.get("color") and sb.get("color"):
        dc = max(abs(x - y) for x, y in zip(sa["color"], sb["color"]))
    # เรียงตามความชัดของหลักฐาน — น้ำหนักเส้นวัดได้ตรงที่สุด
    if dw >= WEIGHT_TOL:
        out.update(kind="weight",
                   note="น้ำหนักเส้นต่างกัน %.0f%% (%.1f vs %.1f px) — "
                        "น่าจะเป็นตัวหนา/ตัวธรรมดาคนละแบบ"
                        % (dw * 100, sa["stroke"], sb["stroke"]))
    elif dh >= SIZE_TOL:
        out.update(kind="size",
                   note="ความสูงตัวอักษรต่างกัน %.0f%% (%.0f vs %.0f px)"
                        % (dh * 100, sa["height"], sb["height"]))
    elif dc >= COLOR_TOL:
        out.update(kind="color",
                   note="สีของตัวอักษรต่างกัน (ห่างกัน %.0f ระดับ)" % dc)
    else:
        out.update(kind="other",
                   note="ตัวอักษรเหมือนกันแต่ภาพไม่ตรงกัน — "
                        "อาจเป็นระยะห่าง/ตำแหน่ง/รายละเอียดเล็ก ๆ")
    return out


# ── ① ขยายครอปให้ถึงขอบคำ ────────────────────────────────────────────
def _gap_cols(a, b) -> np.ndarray:
    """คอลัมน์ที่ **ไม่มีหมึกทั้งสองภาพ** = ช่องว่างที่ตัดได้อย่างปลอดภัย."""
    return ((_ink_mask(a).sum(axis=0) == 0) & (_ink_mask(b).sum(axis=0) == 0))


def line_height(img, y_center: int) -> int:
    """ความสูงของ "บรรทัด" ที่จุด ``y_center`` อยู่ — วัดจากแถวที่มีหมึก.

    ใช้ตั้งเกณฑ์ช่องว่างของขอบคำให้เป็นสัดส่วน จึงทำงานได้ทุกความละเอียด
    และทุกภาษา (ดูหมึก ไม่ดูตัวอักษร)
    """
    m = _ink_mask(img)
    rows = m.sum(axis=1) > 0
    H = len(rows)
    y = max(0, min(int(y_center), H - 1))
    if not rows[y]:
        return 0
    top = y
    while top > 0 and rows[top - 1]:
        top -= 1
    bot = y
    while bot < H - 1 and rows[bot + 1]:
        bot += 1
    return bot - top + 1


def _grow(free: np.ndarray, start: int, step: int, cap: int,
          gap_min: int = GAP_MIN_PX) -> int:
    """เดินจาก ``start`` ไปทาง ``step`` จนเจอช่องว่างกว้าง GAP_MIN_PX.

    ไม่เจอภายใน ``cap`` ⇒ คืน ``cap`` (ขยายเท่าที่ยอมได้) — ดีกว่าไม่ขยาย
    เลยเพราะอย่างน้อยครอปกว้างขึ้น อ่านได้มากขึ้น
    """
    n = len(free)
    run = 0
    for d in range(1, cap + 1):
        i = start + step * d
        if i < 0 or i >= n:
            return d - 1
        if free[i]:
            run += 1
            if run >= gap_min:
                return d
        else:
            run = 0
    return cap


def expand_box(img_a, img_b, px, pad_min: int = PAD_MIN_PX):
    """ขยายกรอบ ``px = [x, y, w, h]`` ออกจนถึง **ขอบคำ** ของทั้งสองภาพ.

    ทำไมจำเป็น: กรอบที่ ``pixdiff`` คืนมาครอบเฉพาะ *พิกเซลที่ต่าง* ซึ่ง
    สำหรับความต่างของฟอนต์จะกระจุกอยู่บางส่วนของคำ ⇒ ครอปตามกรอบนั้นตรง ๆ
    ได้ข้อความไม่ครบ (วัดจริง: ``Manuf`` แทนที่จะเป็น ``Manufacturing``)

    ขยายเฉพาะที่ **ว่างทั้งสองฝั่ง** ⇒ ไม่มีทางกินคำข้าง ๆ ที่มีเนื้อหา
    และใช้ได้ทุกภาษาเพราะดูแต่หมึก ไม่ดูตัวอักษร
    """
    x, y, w, h = [int(v) for v in px]
    H, W = img_a.shape[:2]
    x = max(0, min(x, W - 1))
    y = max(0, min(y, H - 1))
    w = max(1, min(w, W - x))
    h = max(1, min(h, H - y))

    band = (slice(max(0, y - 2), min(H, y + h + 2)), slice(0, W))
    free_c = _gap_cols(img_a[band], img_b[band])
    # เกณฑ์ช่องว่าง + เพดานการขยาย = สัดส่วนของความสูงบรรทัดจริง ⇒ ไม่หยุด
    # ที่ช่องว่างระหว่างตัวอักษร และไม่ผูกกับ dpi/ขนาดฟอนต์/ภาษา
    lh = line_height(img_a, y + h // 2) or h
    gap_min = max(GAP_MIN_PX, int(GAP_MIN_FRAC * lh))
    cap_x = int(min(max(pad_min, EXPAND_CAP_FRAC * w, EXPAND_CAP_LINES * lh),
                    EXPAND_CAP_IMG_FRAC * W))
    cap_y = int(min(max(pad_min, EXPAND_CAP_FRAC * h),
                    EXPAND_CAP_IMG_FRAC * H))
    left = max(pad_min, _grow(free_c, x, -1, cap_x, gap_min))
    right = max(pad_min, _grow(free_c, x + w - 1, +1, cap_x, gap_min))

    # แนวตั้งขยายแบบเผื่อคงที่ก็พอ — บรรทัดข้างบน/ล่างมักมีหมึกอยู่แล้ว
    # การไล่หาช่องว่างแนวตั้งจึงมักหยุดทันทีและไม่ช่วยอะไร
    top = bot = max(pad_min, int(0.35 * h))

    nx = max(0, x - left)
    ny = max(0, y - top)
    return [nx, ny, min(W - nx, w + left + right), min(H - ny, h + top + bot)]


def crop(img, px):
    x, y, w, h = [int(v) for v in px]
    return img[max(0, y):y + h, max(0, x):x + w]
