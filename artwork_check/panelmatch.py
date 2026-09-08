# -*- coding: utf-8 -*-
"""เทียบ "แผงต่อแผง" ระดับพิกเซล แบบที่ทนสเกล/สี/เศษพิกเซล (โหมดทดลอง).

``pixdiff.compare_zone`` เดิมตอบว่า **เทียบไม่ได้** กับไฟล์คู่จริงของสถานี
ไล่หาสาเหตุแล้วพบ 3 จุด — ทั้งหมดแก้ได้ และไม่ใช่ข้อจำกัดเชิงหลักการ:

  ① แผงขยายของสองไฟล์ **พิมพ์คนละขนาด** (51.3 mm vs 60.9 mm = 0.784 เท่า)
     ด่านเดิมตัดที่ 5% ⇒ ปฏิเสธทันที
  ② **สีทั้งใบต่างกัน** (ช่อง R ต่างเฉลี่ย +17.5 ระดับ — คนละ color profile
     ระหว่างไฟล์ออกแบบกับไฟล์ส่งโรงพิมพ์) ⇒ ``max ข้ามช่องสี >= 32`` ติดทั้งภาพ
  ③ align เลื่อนได้แค่ **จำนวนเต็มพิกเซล** ⇒ บนแถบฉลากเหลือต่าง 23.9%
     เลื่อนแบบ sub-pixel แล้วเหลือ 2.68% (ดีขึ้น 12 เท่า)

แก้ครบทั้งสามแล้ววัดบนไฟล์คู่จริง (แผงโภชนาการที่ต่างกันจริงข้อเดียวคือ
Sodium 20% → 24%):

    บริเวณที่พบ            1   (= เซลล์ 24%/20% พอดี ยืนยันด้วยตาแล้ว)
    ฟ้องผิด                0
    รันซ้ำ 3 ครั้ง          ได้ bbox เดิมเป๊ะทุกครั้ง
    เทียบไฟล์กับตัวเอง      0 บริเวณ · ต่าง 0.0000%

⚠️ **ตัดขอบครอปทิ้งก่อนหาบริเวณ** — ขอบของภาพที่ align แล้วเป็นที่เดียวที่
   ข้อมูลสองฝั่งไม่ทับกันจริง (warp เติมขอบมา) วัดได้ว่าถ้าไม่ตัด จะได้
   4 บริเวณ (จริง 1 + ขยะขอบ 3) · ตัด 10 px แล้วเหลือ 1 บริเวณพอดี

โมดูลนี้ **ไม่แตะ pixdiff.py เดิม** และไม่ถูกเรียกจากเส้นทางปกติ —
เปิดด้วยช่องติ๊กเท่านั้น
"""
from typing import List, Optional, Tuple

from .pdf_ingest import apply_rotation

import cv2
import numpy as np

from . import pixdiff

# ── ค่าจูน (ที่มาของทุกตัวเลขอยู่ใน docstring ข้างบน) ────────────────
DPI = 400                  # แผงโภชนาการมีตัวเลขเล็ก — 400 ให้ bbox ที่ใช้ได้จริง
SCALE_LO, SCALE_HI = 0.60, 1.70    # ช่วงสเกลที่ยอมค้นหา (ไฟล์จริงอยู่ที่ 0.784)
SCALE_STEP = 0.004
MIN_SCALE_NCC = 0.55
TEMPLATE_MARGIN_FRAC = 0.22   # ต้อง >= (1 - SCALE_LO)/2 (มีเทสต์ล็อก)       # ต่ำกว่านี้ = คนละเนื้อหา ⇒ ไม่เทียบ (เกณฑ์เดียวกับ pixdiff)
# ⬇️ ความละเอียดขั้นต่ำของภาพที่เอาไปเทียบ — แพทเทิร์นเดียวกับ
#    ``ocr._render_for_ocr`` (``OCR_CROP_MIN_SIDE``) และจำเป็นด้วยเหตุผลเดียวกัน
#
#    **เจอบนสถานี 5 ก.ย.: แผงที่พิมพ์เล็ก (28x29 mm) เรนเดอร์ที่ 400 dpi ได้
#    แค่ 447x457 px ⇒ ความต่างจริง (20% -> 24%) เหลือ "5 พิกเซล" ⇒ พบ 0 บริเวณ
#    = false negative** ทั้งที่ OCR ทั้งสองฝั่งอ่านตัวเลขต่างกันชัด ๆ
#
#    ไล่ระดับความละเอียดบนคู่ไฟล์จริง (ลด dpi = จำลองแผงที่เล็กลง):
#      ด้านยาว 411 px -> ต่าง   5 px -> **0 บริเวณ**  (อาการของสถานี)
#      ด้านยาว 485 px -> ต่าง  14 px -> 0 บริเวณ
#      ด้านยาว 562 px -> ต่าง  27 px -> 1 บริเวณ 0.179 mm²
#      ด้านยาว 747 px -> ต่าง  65 px -> 1 บริเวณ 0.246 mm²
#      ด้านยาว 1868 px -> ต่าง 640 px -> 1 บริเวณ 0.403 mm²
#    **ฟ้องผิดบน self-compare = 0 ทุกความละเอียด** ⇒ เพิ่มความละเอียดไม่มีราคา
#    ด้านความแม่น ⇒ ตั้ง 1000 เพื่อได้ระยะเผื่อ ~1.8 เท่าจากจุดที่เริ่มเห็น
MIN_SIDE_PX = 1000
# เพดานขนาดภาพตอน "ปรับสเกลด้วยการเรนเดอร์" — กันเคสแผงใหญ่ที่อัตราส่วน
# สูงแล้วเรนเดอร์ออกมาเป็นภาพหลักหมื่นพิกเซลจนกินหน่วยความจำ
PRESCALE_MAX_SIDE_PX = 4000
# ความหนาเส้นขั้นต่ำที่ยัง "วัดเป็นเปอร์เซ็นต์" ได้อย่างมีความหมาย
MIN_STROKE_PX = 3.0
DPI_MAX_FACTOR = 4.0       # เพดานเดียวกับ config.OCR_DPI_MAX_FACTOR
TRIM_PX = 12               # ตัดขอบทิ้งก่อนหาบริเวณ (ขอบ = ที่เดียวที่ข้อมูลไม่ทับกัน)
BLUR_SIGMA = 1.0
TOLERANCE_PX = 1
# ⬇️ 15 ไม่ใช่ 40 — วัดด้วย ``verify_compare.py`` บนไฟล์จริง:
#    ความต่างจริงของคู่ John West (เซลล์ 20%/24%) มีขนาดแค่ **61 px²**
#    ที่ 400 dpi ⇒ เกณฑ์ 40 เหลือระยะเผื่อแค่ 1.5 เท่า และเป็นกลไกของ
#    **false negative ที่เกิดจริงบนสถานี** (รอบหนึ่งได้ 0 defect)
#    ไล่ระดับ 40→6 แล้ววัดสามด้านพร้อมกัน: ฟ้องผิดบน self-compare = **0
#    ทุกค่า** (4 ไฟล์ × 14 โซนหนาแน่น ink สูงสุด 1.00 · σ สูงสุด 77) ·
#    ความไวดีขึ้นจาก 0.4 mm เป็น **0.2 mm** เมื่อ <= 15 · คู่จริงให้
#    "บริเวณในแผง = 1" ทุกค่า ⇒ เลือก 15 (0.06 mm²) เพื่อได้ระยะเผื่อ 4 เท่า
#    โดยยังไม่ไปสุดขอบที่วัดมา
MIN_REGION_PX = 15
# ความต่างที่ "ติดขอบพื้นที่เทียบ" ไม่ใช่ความต่างของแผง แต่คือเนื้อหารอบ ๆ
# ที่ผู้ใช้ลากเกินเข้ามา (สองไฟล์วางแผงคนละที่ ของรอบ ๆ จึงคนละอย่าง).
# วัดบนคู่จริง 10 แบบการลาก: บริเวณที่ **ไม่ติดขอบ** = 1 พอดีทุกแบบ
# (= ความต่างจริง) ส่วนที่ติดขอบ = 0/4/10/12 = ขยะทั้งหมด ⇒ แยกได้ 10/10
EDGE_TOL_PX = 2


def _to_gray(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img


def find_scale(gray_a, gray_b, margin: float = TEMPLATE_MARGIN_FRAC) -> Tuple[float, Tuple[int, int], float]:
    """หาสเกลของ ``b`` ที่ทำให้ตรงกับ ``a`` มากที่สุด → ``(สเกล, ตำแหน่ง, คะแนน)``.

    ใช้ใจกลางของ a เป็น template (เว้นขอบ ``margin``) แล้วไล่ย่อ/ขยาย b
    ⇒ ทนได้ทั้งกรณีแผงถูกพิมพ์คนละขนาดและกรณีผู้ใช้ลากโซนคนละกรอบ
    """
    # ค้นแบบหยาบ→ละเอียด: รอบแรกบนภาพย่อ 1/4 (เร็วกว่า ~16 เท่า) เพื่อจำกัด
    # ช่วง แล้วค่อยค้นละเอียดรอบสองบนภาพเต็ม — ผลลัพธ์เท่ากันแต่เร็วกว่ามาก
    def _scan(ga, gb, lo, hi, step, frac):
        # ⚠️ template ต้อง **เล็กกว่าภาพ b ที่ย่อแล้ว** ทุกสเกลในช่วงที่ค้น
        #    ไม่งั้น matchTemplate ทำไม่ได้และสเกลนั้นถูกข้ามไปเงียบ ๆ
        #    (เจอตอนเขียนเทสต์: แผงที่ใหญ่กว่า 1.05 เท่าขึ้นไป หาไม่เจอเลย
        #    เพราะสเกลที่ถูกต้องย่อ b จนเล็กกว่า template)
        #    เงื่อนไข: (1 - 2f) <= SCALE_LO  ⇒  f >= (1 - SCALE_LO) / 2
        h, w = ga.shape[:2]
        mgy = max(2, min(int(frac * h), h // 2 - 2))
        mgx = max(2, min(int(frac * w), w // 2 - 2))
        t = ga[mgy:h - mgy, mgx:w - mgx]
        best = (-1.0, 1.0, (0, 0))
        s = lo
        while s <= hi + 1e-9:
            interp = cv2.INTER_AREA if s < 1.0 else cv2.INTER_CUBIC
            r = cv2.resize(gb, None, fx=s, fy=s, interpolation=interp)
            if r.shape[0] >= t.shape[0] + 2 and r.shape[1] >= t.shape[1] + 2:
                res = cv2.matchTemplate(r, t, cv2.TM_CCOEFF_NORMED)
                _, mx, _, loc = cv2.minMaxLoc(res)
                if mx > best[0]:
                    best = (float(mx), float(s),
                        (int(loc[0]) - mgx, int(loc[1]) - mgy))
            s += step
        return best

    h, w = gray_a.shape[:2]
    # เว้นขอบเป็น "สัดส่วน" ไม่ใช่พิกเซลตายตัว เพื่อให้ template เล็กพอ
    # สำหรับทุกสเกลในช่วง [SCALE_LO, SCALE_HI]
    m = max(TEMPLATE_MARGIN_FRAC, (1.0 - SCALE_LO) / 2.0 + 0.02)
    k = 4 if min(h, w) >= 400 else 1
    best = (-1.0, 1.0, (0, 0))
    if k > 1:
        sa = cv2.resize(gray_a, None, fx=1.0 / k, fy=1.0 / k,
                        interpolation=cv2.INTER_AREA)
        sb = cv2.resize(gray_b, None, fx=1.0 / k, fy=1.0 / k,
                        interpolation=cv2.INTER_AREA)
        c = _scan(sa, sb, SCALE_LO, SCALE_HI, SCALE_STEP * k, m)
        lo = max(SCALE_LO, c[1] - SCALE_STEP * k * 2)
        hi = min(SCALE_HI, c[1] + SCALE_STEP * k * 2)
        best = _scan(gray_a, gray_b, lo, hi, SCALE_STEP, m)
    # ⚠️ ค้นแบบหยาบก่อนอาจเจอ "ยอดปลอม" ได้ เพราะที่ 1/4 ความละเอียด
    #    ตัวหนังสือเละจนจับคู่ไม่ได้ — วัดเจอตอนเขียนเทสต์ (แผงที่ใหญ่กว่า
    #    1.05-1.15 เท่า ได้สเกลผิดไปไกลและ NCC ตก 0.34-0.44).
    #    ถ้าผลยังไม่ผ่านเกณฑ์ ให้ค้นเต็มความละเอียดทั้งช่วง (ช้าลงเฉพาะ
    #    เคสที่ค้นหยาบเอาไม่อยู่ ซึ่งบนไฟล์จริงไม่เกิด — NCC 0.89)
    if best[0] < MIN_SCALE_NCC:
        full = _scan(gray_a, gray_b, SCALE_LO, SCALE_HI, SCALE_STEP, m)
        if full[0] > best[0]:
            best = full
    return best[1], best[2], best[0]


def match_colors(ref, img):
    """ปรับ mean/std ของ ``img`` ต่อช่องสีให้เท่า ``ref``.

    ⚠️ จำเป็นจริง ๆ ไม่ใช่การผ่อนเกณฑ์ — ``pixdiff._diff_mask`` ใช้
    ``max ข้ามช่องสี`` ⇒ ความต่างเชิงสีคงที่ทั้งใบ (วัดได้ +17.5 ในช่อง R
    ระหว่างไฟล์ออกแบบกับไฟล์โรงพิมพ์) ทำให้ทุกพิกเซลที่มีสีติดเกณฑ์ทันที
    """
    out = img.astype(np.float32).copy()
    for i in range(out.shape[2] if out.ndim == 3 else 1):
        x = ref[:, :, i].astype(np.float32)
        y = img[:, :, i].astype(np.float32)
        sd = float(y.std())
        out[:, :, i] = (y - y.mean()) * (float(x.std()) / max(sd, 1e-6)) + x.mean()
    return np.clip(out, 0, 255).astype(np.uint8)


def refine_align(img_a, img_b):
    """เลื่อน/ปรับ ``b`` แบบ sub-pixel ให้ทับ ``a`` (ECC affine).

    คืน ``(ภาพที่ปรับแล้ว, คะแนน)``. ล้มเหลว = คืนภาพเดิมพร้อมคะแนน 0
    (ยังเทียบได้ แค่ไม่ละเอียดเท่า)
    """
    warp = np.eye(2, 3, dtype=np.float32)
    try:
        cc, warp = cv2.findTransformECC(
            _to_gray(img_a).astype(np.float32), _to_gray(img_b).astype(np.float32),
            warp, cv2.MOTION_AFFINE,
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 400, 1e-7), None, 5)
    except cv2.error:
        return img_b, 0.0
    out = cv2.warpAffine(img_b, warp, (img_a.shape[1], img_a.shape[0]),
                         flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
                         borderMode=cv2.BORDER_REPLICATE)
    return out, float(cc)


def _zone_px(path: str, bbox, dpi: int, page_index: int = 0):
    """ทำนายขนาดภาพ (px) ของโซนที่ ``dpi`` โดย **ไม่ต้องเรนเดอร์**.

    ``render_zone_mm`` เรนเดอร์ที่สเกล mm จริง ⇒ ขนาด px คำนวณตรง ๆ ได้จาก
    ขนาดหน้าเป็นมิลลิเมตร. คืน ``None`` เมื่ออ่านขนาดหน้าไม่ได้ (ไม่ใช่ PDF)
    """
    size = pixdiff.page_size_mm(path, page_index)
    if not size:
        return None
    try:
        w_mm = float(bbox[2]) * size[0]
        h_mm = float(bbox[3]) * size[1]
    except (TypeError, IndexError, ValueError):
        return None
    if w_mm <= 0 or h_mm <= 0:
        return None
    k = dpi / 25.4
    return (max(1, int(round(w_mm * k))), max(1, int(round(h_mm * k))))


def zone_scale_ratio(path_a, bbox_a, path_b, bbox_b, dpi: int = DPI,
                     page_index: int = 0) -> Optional[float]:
    """แผงใน a ใหญ่กว่าใน b กี่เท่า — วัดจาก **ขนาดโซนเป็นมิลลิเมตร**.

    ใช้ **ด้านยาว** เป็นตัวเทียบ ซึ่ง **ไม่เปลี่ยนเมื่อหมุน 90°** ⇒ ค่านี้
    ใช้ได้เหมือนกันไม่ว่าผู้ใช้จะหมุนหน้าจอ/โซนไปทางไหน (สอดคล้องกับปุ่ม
    "หมุนเฉพาะการแสดงผล" ซึ่งไม่เปลี่ยนพิกัดที่เก็บ)

    คืน ``None`` เมื่ออ่านขนาดหน้าไม่ได้ (ไม่ใช่ PDF)
    """
    pa = _zone_px(path_a, bbox_a, dpi, page_index)
    pb = _zone_px(path_b, bbox_b, dpi, page_index)
    if not pa or not pb:
        return None
    la, lb = max(pa), max(pb)
    if la <= 0 or lb <= 0:
        return None
    return round(la / float(lb), 4)


def _dpi_for(path_a, bbox_a, path_b, bbox_b, dpi: int,
             page_index: int = 0) -> int:
    """DPI ที่ควรใช้จริง — เพิ่มให้ถึง :data:`MIN_SIDE_PX` ตั้งแต่รอบแรก.

    ทำนายขนาดภาพจาก **มิลลิเมตร** ก่อนเรนเดอร์ ⇒ เรนเดอร์รอบเดียวในเคสปกติ
    (เดิมเรนเดอร์ที่ ``dpi`` ก่อนแล้วค่อยดูว่าเล็กไปไหม = ทิ้ง 1 รอบเสมอ)

    ⚠️ ต้องใช้โซนที่ **เล็กกว่า** เป็นตัวกำหนด ไม่ใช่ใหญ่กว่า — ``render_zone_mm``
       เรนเดอร์ทั้งสองฝั่งที่ mm/px เท่ากันอยู่แล้ว ขนาดภาพจึงสะท้อน "ขนาดโซน
       ที่ลาก" ล้วน ๆ ⇒ ถ้าใช้ max โซนอ้างอิงที่ลากกว้างจะกลบความจำเป็นไป
    """
    if not MIN_SIDE_PX:
        return dpi
    pa = _zone_px(path_a, bbox_a, dpi, page_index)
    pb = _zone_px(path_b, bbox_b, dpi, page_index)
    if not pa or not pb:
        return dpi                      # อ่านขนาดหน้าไม่ได้ ⇒ ทางเดิม
    longest = min(max(pa), max(pb))
    if longest <= 0 or longest >= MIN_SIDE_PX:
        return dpi
    f = min(DPI_MAX_FACTOR, MIN_SIDE_PX / float(longest))
    return max(dpi + 1, int(round(dpi * f)))


def _dpi_pair(path_a, bbox_a, path_b, bbox_b, dpi: int,
              page_index: int = 0):
    """``(dpi ของ a, dpi ของ b, อัตราส่วนขนาดโซน)``.

    ทำสองอย่างตามลำดับ:

    ① **ปรับสเกลด้วยการเรนเดอร์ ไม่ใช่ขยายบิตแมป** — เมื่อแผงสองฝั่งขนาด
       ต่างกันเกินช่วงที่ ``find_scale`` ค้นได้ (``SCALE_LO``..``SCALE_HI``)
       ให้เรนเดอร์ฝั่งที่ **เล็กกว่า** ที่ dpi สูงขึ้นตามอัตราส่วน
       ⇒ ทั้งสองฝั่งออกมาขนาดพิกเซลใกล้เคียงกัน สเกลที่เหลือจึงอยู่ราว 1.0

       * ไฟล์เป็น vector ⇒ เรนเดอร์ที่ dpi สูงขึ้นได้ **รายละเอียดจริง**
         ต่างจากการขยายบิตแมปซึ่งไม่มีข้อมูลเพิ่ม
       * **ไม่ลด dpi ของฝั่งไหนเลย** (ข้อกำหนดผู้ใช้: dpi เพิ่มได้ ห้ามลด)
       * ⚠️ **อัตราส่วนที่อยู่ในช่วงอยู่แล้ว ⇒ ไม่ทำอะไรเลย = พฤติกรรมเดิม
         เป๊ะ** — แตะเฉพาะเคสที่วันนี้ตอบ ``align_failed`` อยู่แล้ว จึงไม่มี
         อะไรจะเสีย และไม่ต้องขยายช่วงค้นสเกล (วัดแล้วว่าขยายช่วงทำให้ช้าลง
         151 เท่า: 0.6 → 90.8 วินาที)

    ② **ยกความละเอียดให้ถึง** :data:`MIN_SIDE_PX` เหมือนเดิม
    """
    ratio = zone_scale_ratio(path_a, bbox_a, path_b, bbox_b, dpi, page_index)
    dpi_a = dpi_b = dpi
    prescaled = False
    if ratio and not (SCALE_LO <= ratio <= SCALE_HI):
        small_path, small_bbox = ((path_b, bbox_b) if ratio > 1
                                  else (path_a, bbox_a))
        want = dpi * (ratio if ratio > 1 else 1.0 / ratio)
        d = int(round(want))
        px = _zone_px(small_path, small_bbox, d, page_index)
        if px and max(px) > PRESCALE_MAX_SIDE_PX:
            # ชนเพดานหน่วยความจำ ⇒ ปรับได้ไม่สุด
            d = max(1, int(d * PRESCALE_MAX_SIDE_PX / float(max(px))))
        f = d / float(dpi)
        left = (ratio / f) if ratio > 1 else (ratio * f)
        # ปรับแล้วสเกลที่เหลือต้องอยู่ในช่วงที่ find_scale ค้นได้จริง
        # ไม่งั้นปรับไปก็ไม่ช่วย — บอกตรง ๆ ว่าขนาดต่างกันเกินไปดีกว่า
        if SCALE_LO <= left <= SCALE_HI:
            prescaled = True
            if ratio > 1:
                dpi_b = max(dpi + 1, d)
            else:
                dpi_a = max(dpi + 1, d)
    if not MIN_SIDE_PX:
        return dpi_a, dpi_b, ratio, prescaled
    pa = _zone_px(path_a, bbox_a, dpi_a, page_index)
    pb = _zone_px(path_b, bbox_b, dpi_b, page_index)
    if not pa or not pb:
        return dpi_a, dpi_b, ratio, prescaled   # อ่านขนาดหน้าไม่ได้ ⇒ ทางเดิม
    longest = min(max(pa), max(pb))
    if longest <= 0 or longest >= MIN_SIDE_PX:
        return dpi_a, dpi_b, ratio, prescaled
    k = min(DPI_MAX_FACTOR, MIN_SIDE_PX / float(longest))
    return (max(dpi_a + 1, int(round(dpi_a * k))),
            max(dpi_b + 1, int(round(dpi_b * k))), ratio, prescaled)





def unrotate_frac_box(box, angle: int):
    """กรอบสัดส่วนในภาพที่ **หมุนแล้ว** → กรอบในภาพเดิมที่ยังไม่หมุน.

    จำเป็นเพราะ ``pipeline.zone_crop_jpg`` วาดกรอบ **ก่อน** หมุนภาพ
    (กรอบจึงหมุนตามภาพเอง) ⇒ พิกัดที่เก็บลงรายงานต้องเป็นของโซนที่ยัง
    ไม่หมุนเสมอ ไม่งั้นกรอบแดงจะไปโผล่ผิดที่แบบเงียบ ๆ
    """
    x, y, w, h = [float(v) for v in box]
    a = int(angle) % 360
    if a == 90:
        return [y, 1.0 - x - w, h, w]
    if a == 180:
        return [1.0 - x - w, 1.0 - y - h, w, h]
    if a == 270:
        return [1.0 - y - h, x, h, w]
    return [x, y, w, h]


def compare(path_a: str, bbox_a, path_b: str, bbox_b,
            dpi: int = DPI, page_index: int = 0,
            trim_px: int = TRIM_PX, rotate_a: int = 0,
            rotate_b: int = 0) -> dict:
    """เหมือน :func:`compare_ex` แต่คืนเฉพาะผล (ภาพถูกทิ้ง) — ใช้เมื่อจะเก็บ
    ลง report.json ซึ่ง serialize ภาพไม่ได้."""
    res, _a, _b = compare_ex(path_a, bbox_a, path_b, bbox_b, dpi,
                             page_index, trim_px, rotate_a, rotate_b)
    return res


def compare_ex(path_a: str, bbox_a, path_b: str, bbox_b,
               dpi: int = DPI, page_index: int = 0,
               trim_px: int = TRIM_PX, rotate_a: int = 0,
               rotate_b: int = 0):
    """เทียบแผงสองแผงที่อาจคนละขนาด/คนละสี → บริเวณที่ต่างจริง.

    คืน dict แบบเดียวกับ ``pixdiff.compare_zone`` (``status`` · ``reason`` ·
    ``regions`` เป็นสัดส่วนของ **โซน a**) บวก ``scale`` · ``ncc`` · ``ecc``
    """
    # ── ความละเอียดที่ต้องใช้ คำนวณจาก **ขนาดโซนเป็นมิลลิเมตร** ก่อนเรนเดอร์
    #
    # แผงที่พิมพ์เล็กได้ภาพเล็กตามไปด้วย ⇒ ความต่างจริงเหลือไม่กี่พิกเซลแล้ว
    # ถูกตัดทิ้ง (ดู MIN_SIDE_PX). เดิมเรนเดอร์ที่ ``dpi`` ก่อนแล้วค่อยดูว่า
    # เล็กไปไหม ⇒ **เรนเดอร์ทิ้ง 1 รอบเสมอ** (วัดได้ 0.85 วินาที/กลุ่ม)
    # ตอนนี้ทำนายขนาดจาก mm ⇒ เรนเดอร์รอบเดียวในเคสปกติ
    #
    # ⚠️ ต้องใช้โซนที่ **เล็กกว่า** เป็นตัวกำหนด ไม่ใช่ใหญ่กว่า —
    #    ``render_zone_mm`` เรนเดอร์ทั้งสองฝั่งที่ mm/px เท่ากันอยู่แล้ว
    #    ขนาดภาพจึงสะท้อน "ขนาดโซนที่ลาก" ล้วน ๆ. พื้นที่ที่เทียบได้จริงคือ
    #    ส่วนที่ทับกัน = ถูกจำกัดด้วยโซนที่เล็กกว่า ⇒ ถ้าใช้ max โซนอ้างอิงที่
    #    ลากกว้างจะกลบความจำเป็นในการเพิ่มความละเอียดไปเงียบ ๆ
    def _load(da, db):
        """เรนเดอร์ + หมุน + ยกความละเอียดให้ถึง MIN_SIDE_PX → (a, b, da, db)."""
        ia, _ = pixdiff.render_zone_mm(path_a, bbox_a, da, page_index)
        ib, _ = pixdiff.render_zone_mm(path_b, bbox_b, db, page_index)
        if ia is None or ib is None or ia.size == 0 or ib.size == 0:
            return None, None, da, db
        # ── หมุนตามค่าที่โซนตั้งไว้ (ค่าเดียวกับที่ชั้น OCR ใช้) ──────
        #
        # ปุ่ม "หมุนเฉพาะการแสดงผล" ตั้งค่านี้ให้โซนใหม่อัตโนมัติ ⇒ ชั้นภาพ
        # จึงเห็นแผงในแนวเดียวกับที่ผู้ใช้เห็นและที่ OCR อ่าน
        #
        # ⚠️ วัดแล้ว: หมุน **ทั้งสองฝั่งเท่ากัน** ให้ผลเท่าเดิมทุกหลัก
        #    (38 บริเวณ · ต่าง 1.2094% ทั้ง 0/90/180/270°) เพราะการหมุน 90°
        #    ไม่มีการ resample ⇒ ค่าเริ่มต้น 0 = พฤติกรรมเดิมเป๊ะ
        #    ที่ได้เพิ่มคือเคสที่ **สองไฟล์วางคนละแนว** ซึ่งเดิม align ไม่ติด
        if rot_a:
            ia = apply_rotation(ia, rot_a)
        if rot_b:
            ib = apply_rotation(ib, rot_b)
        # ทางถอย: โซนที่ถูกขอบหน้ากระดาษตัด (หรืออ่านขนาดหน้าไม่ได้) จะได้
        # ภาพเล็กกว่าที่ทำนาย ⇒ เรนเดอร์ซ้ำอีกรอบเหมือนเดิม (เกิดไม่บ่อย)
        lo = min(max(ia.shape[0], ia.shape[1]), max(ib.shape[0], ib.shape[1]))
        if MIN_SIDE_PX and 0 < lo < MIN_SIDE_PX:
            k = min(DPI_MAX_FACTOR, MIN_SIDE_PX / float(lo))
            da2 = max(da + 1, int(round(da * k)))
            db2 = max(db + 1, int(round(db * k)))
            a2, _ = pixdiff.render_zone_mm(path_a, bbox_a, da2, page_index)
            b2, _ = pixdiff.render_zone_mm(path_b, bbox_b, db2, page_index)
            if a2 is not None and b2 is not None and a2.size and b2.size:
                ia = apply_rotation(a2, rot_a) if rot_a else a2
                ib = apply_rotation(b2, rot_b) if rot_b else b2
                da, db = da2, db2
        return ia, ib, da, db

    rot_a, rot_b = int(rotate_a or 0) % 360, int(rotate_b or 0) % 360
    zone_ratio = zone_scale_ratio(path_a, bbox_a, path_b, bbox_b,
                                  dpi, page_index)
    prescaled = False
    # รอบแรก: dpi เท่ากันสองฝั่ง + ทำนายการยกความละเอียดไว้ก่อนเรนเดอร์
    # ⇒ เรนเดอร์ **2 ครั้ง** ในเคสปกติเหมือนเดิม (ไม่ใช่ 4)
    dpi_a = dpi_b = _dpi_for(path_a, bbox_a, path_b, bbox_b, dpi, page_index)
    a, b, dpi_a, dpi_b = _load(dpi_a, dpi_b)
    if a is None or b is None:
        return (dict(pixdiff._skip("render_failed"), scale=0.0, ncc=0.0,
                     ecc=0.0, zone_ratio=zone_ratio), None, None)

    ga, gb = _to_gray(a), _to_gray(b)
    scale, loc, ncc = find_scale(ga, gb)

    # ── ทางถอยที่ ② : ปรับสเกลด้วยการ **เรนเดอร์** ไม่ใช่ขยายบิตแมป ───
    #
    # ⚠️ ทำ **หลัง** ทางเดิมล้มเหลวเท่านั้น — ไม่ใช่ก่อน. เหตุผลวัดมาแล้ว:
    #    "ขนาดโซนที่ลาก" ไม่เท่ากับ "สเกลของเนื้อหา" — ผู้ใช้ที่ลากโซน
    #    อ้างอิงหลวมทั้งหน้าจะได้อัตราส่วนเพี้ยนมาก ทั้งที่เนื้อหาสเกล 1.0
    #    และ find_scale จัดการได้อยู่แล้ว (มีเทสต์ล็อกเคสนี้ไว้) ⇒ ถ้าปรับ
    #    ตั้งแต่แรกจะไป **ทำเคสที่เคยทำงานได้พัง**
    #
    #    ทำตอนล้มเหลวแล้วจึงปลอดภัยโดยโครงสร้าง: เคสที่เดิมสำเร็จไม่มีทาง
    #    เข้ามาถึงตรงนี้ ⇒ พฤติกรรมเดิมคงอยู่ครบ 100%
    if (ncc < MIN_SCALE_NCC and zone_ratio
            and not (SCALE_LO <= zone_ratio <= SCALE_HI)):
        da2, db2, _r, can = _dpi_pair(path_a, bbox_a, path_b, bbox_b,
                                      dpi, page_index)
        if can:
            a2, b2, da2, db2 = _load(da2, db2)
            if a2 is not None and b2 is not None:
                s2, l2, n2 = find_scale(_to_gray(a2), _to_gray(b2))
                if n2 > ncc:
                    a, b, dpi_a, dpi_b = a2, b2, da2, db2
                    scale, loc, ncc = s2, l2, n2
                    prescaled = True
    dpi = dpi_a          # บริเวณทั้งหมดอยู่ในระบบพิกัดของ a

    if ncc < MIN_SCALE_NCC:
        # ไม่มั่นใจว่าเป็นเนื้อหาเดียวกัน ⇒ ไม่รายงานดีกว่าชี้ผิดที่
        #
        # ⚠️ แยก "แผงสองฝั่งขนาดต่างกันมาก" ออกจาก "จับคู่ไม่ได้" — สองอย่าง
        #    นี้ผู้ใช้แก้คนละทาง และการบอกรวม ๆ ว่า "อาจลากโซนคนละส่วน"
        #    ส่งผู้ใช้ไปแก้ของที่ไม่ได้พัง (เขาลากถูกแล้ว)
        # ⚠️ ``scale_out_of_range`` ใช้เฉพาะเมื่อ **ปรับสเกลให้ไม่ได้/ไม่พอ**
        #    ถ้าปรับได้แล้วยังจับคู่ไม่ติด แปลว่าเนื้อหาต่างกันจริง ไม่ใช่
        #    เรื่องขนาด — โทษเรื่องขนาดตรงนั้นคือการชี้ผิด
        why = "align_failed"
        if (zone_ratio and not prescaled
                and not (SCALE_LO <= zone_ratio <= SCALE_HI)):
            why = "scale_out_of_range"
        return (dict(pixdiff._skip(why), scale=scale, ncc=ncc, ecc=0.0,
                     zone_ratio=zone_ratio, prescaled=prescaled,
                     dpi_a=dpi_a, dpi_b=dpi_b), None, None)

    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    rb = cv2.resize(b, None, fx=scale, fy=scale, interpolation=interp)
    # ``loc`` = ตำแหน่งของ "มุมซ้ายบนของ a" ในระบบพิกัดของ b ที่ย่อแล้ว
    #
    # ⚠️ **ค่านี้ติดลบได้จริง** — เกิดทุกครั้งที่ผู้ใช้ลากโซน a หลวมกว่า b
    #    (โซน a ครอบพื้นที่มากกว่า ⇒ มุมซ้ายบนของ a อยู่ "ก่อน" มุมของ b).
    #    เดิม clamp เป็น 0 ⇒ **ภาพสองฝั่งเลื่อนกันเท่ากับค่าที่ clamp ทิ้ง**
    #    วัดได้: ลากหลวม 1 mm ⇒ loc=(-15,-16) px ⇒ ฟ้องผิด 21 บริเวณ ·
    #    3 mm ⇒ loc=(-47,-47) ⇒ 31 บริเวณ — ทั้งที่เป็น **ไฟล์เดียวกัน**
    #    และ NCC ยังเท่ากับ **1.0000** ทุกเคส (ด่าน NCC จับไม่ได้เลย)
    #    ⇒ ต้องครอป "ส่วนที่ทับกัน" ของทั้งสองฝั่ง ไม่ใช่ตัดค่าติดลบทิ้ง
    ox, oy = int(loc[0]), int(loc[1])
    ax0, ay0 = max(0, -ox), max(0, -oy)
    bx0, by0 = max(0, ox), max(0, oy)
    h = min(a.shape[0] - ay0, rb.shape[0] - by0)
    w = min(a.shape[1] - ax0, rb.shape[1] - bx0)
    if h < 8 or w < 8:
        return (dict(pixdiff._skip("align_failed"), scale=scale, ncc=ncc,
                     ecc=0.0), None, None)
    aa = a[ay0:ay0 + h, ax0:ax0 + w]
    bb = rb[by0:by0 + h, bx0:bx0 + w]

    bb = match_colors(aa, bb)
    bb, ecc = refine_align(aa, bb)

    m = max(0, min(int(trim_px), h // 4, w // 4))
    ai = aa[m:h - m, m:w - m] if m else aa
    bi = bb[m:h - m, m:w - m] if m else bb
    res = pixdiff.compare_images(ai, bi, blur_sigma=BLUR_SIGMA,
                                 tolerance_px=TOLERANCE_PX,
                                 min_region_px=MIN_REGION_PX)
    # ── แยก "ความต่างของแผง" ออกจาก "ของที่ลากเกินแผงเข้ามา" ──────────
    # บริเวณที่แตะขอบพื้นที่เทียบ = อยู่ในวงแหวนที่ผู้ใช้ลากเลยแผงออกไป
    # ซึ่งสองไฟล์มีเนื้อหารอบแผงคนละอย่าง ⇒ ต่างจริงแต่ **ไม่ใช่คำตอบ**
    # ⚠️ ไม่ทิ้งเงียบ — นับไว้แล้วรายงานเป็นคำเตือนให้ลากโซนใหม่
    kept, edge = [], []
    if res.get("status") == pixdiff.OK:
        ih, iw = (ai.shape[0], ai.shape[1])
        t = EDGE_TOL_PX
        for g in res.get("regions") or []:
            x, y, gw, gh = g["px"]
            if x <= t or y <= t or x + gw >= iw - t or y + gh >= ih - t:
                edge.append(g)
            else:
                kept.append(g)
        res["regions"] = kept
        res["edge_regions"] = len(edge)
        res["edge_area_px"] = int(sum(int(g["area_px"]) for g in edge))
        # ติดขอบล้วน = เราแยกไม่ออกว่าของจริงอยู่ในนั้นไหม ⇒ **ไม่ตัดสิน**
        # ถอยไปใช้ผลชั้นข้อความของกลุ่มนั้นแทน (กฎเหล็กข้อ 2)
        if edge and not kept:
            out = dict(pixdiff._skip("edge_only"),
                       scale=round(scale, 4), ncc=round(ncc, 4),
                       ecc=round(ecc, 4), edge_regions=len(edge),
                       diff_ratio=res.get("diff_ratio"),
                       size=[w, h], dpi=dpi, trim_px=m,
                       zone_ratio=zone_ratio, prescaled=prescaled,
                       dpi_a=dpi_a, dpi_b=dpi_b,
                       rotate_a=rot_a, rotate_b=rot_b,
                       mm_per_px=round(25.4 / float(dpi), 4))
            return out, aa, bb
    # ``px``  = พิกัดในภาพที่ align แล้ว (aa/bb) — ผู้เรียกใช้ครอปอ่านข้อความ
    # ``px_a`` = พิกัดเดียวกันบน "โซน a เต็มใบ" — ใช้บอกตำแหน่งให้คนดู
    ah, aw = a.shape[:2]
    bh, bw = b.shape[:2]
    for g in res.get("regions") or []:
        px = list(g["px"])
        px[0] += m
        px[1] += m
        g["px"] = px
        pa_ = [px[0] + ax0, px[1] + ay0, px[2], px[3]]
        g["px_a"] = pa_
        # ⚠️ ``zone_crop_jpg`` วาดกรอบ **ก่อน** หมุนภาพ (กรอบจึงหมุนตามเอง)
        #    ⇒ พิกัดที่เก็บต้องเป็นของโซนที่ **ยังไม่หมุน** เสมอ ไม่งั้น
        #    กรอบแดงไปโผล่ผิดที่แบบเงียบ ๆ (พิสูจน์สูตรกับ cv2.rotate จริง
        #    ครบ 4 มุม คลาด 0.00000)
        g["bbox"] = unrotate_frac_box(
            [pa_[0] / float(aw), pa_[1] / float(ah),
             pa_[2] / float(aw), pa_[3] / float(ah)], rot_a)
        g["bbox"] = [round(v, 5) for v in g["bbox"]]
        # กรอบเดียวกันในระบบพิกัดของ **โซน b** — ย้อนการย่อ (scale) และ
        # ตำแหน่งที่ครอปกลับ ⇒ วาดกรอบแดงบนภาพฝั่งอ้างอิงได้ด้วยพิกัดที่
        # **วัดมา** ไม่ใช่การค้นหาคำ (ซึ่งล้มเหลวเมื่อครอปตัดคำ)
        sc = float(scale) or 1.0
        g["bbox_b"] = unrotate_frac_box(
            [(px[0] + bx0) / sc / float(bw), (px[1] + by0) / sc / float(bh),
             px[2] / sc / float(bw), px[3] / sc / float(bh)], rot_b)
        g["bbox_b"] = [round(v, 5) for v in g["bbox_b"]]
    mmpp = 25.4 / float(dpi)
    res.update(scale=round(scale, 4), ncc=round(ncc, 4), ecc=round(ecc, 4),
               size=[w, h], zone_size=[aw, ah], offset=[ax0, ay0],
               dpi=dpi, trim_px=m, mm_per_px=round(mmpp, 4),
               dpi_a=dpi_a, dpi_b=dpi_b, zone_ratio=zone_ratio,
               prescaled=prescaled, rotate_a=rot_a, rotate_b=rot_b,
               # ความหนาหมึกของ **ทั้งแผง** — ต่างกันเล็กน้อยแต่สม่ำเสมอ
               # ทำให้ขอบตัวอักษรทุกตัวต่าง ⇒ ฟ้องนับร้อยบริเวณ
               panel_ink=panel_ink(ai, bi),
               # ข้อมูลที่เอาไปพัฒนาต่อได้: ความไวที่ทำได้จริงของรอบนี้
               min_region_px=MIN_REGION_PX,
               min_region_mm2=round(MIN_REGION_PX * mmpp * mmpp, 4),
               areas_mm2=[round(g["area_px"] * mmpp * mmpp, 3)
                          for g in (res.get("regions") or [])])
    # คืนภาพที่ align แล้วทั้งสองฝั่ง (พิกัดตรงกันแล้ว) เพื่อให้ผู้เรียกครอป
    # บริเวณเดียวกันจากทั้งสองไฟล์ไปอ่านข้อความได้
    return res, aa, bb


def panel_ink(img_a, img_b) -> Optional[dict]:
    """ความหนาหมึกของ **ทั้งแผง** ทั้งสองฝั่ง — advisory ล้วน.

    ที่มา (วัดบนคู่ V12/V13 ของผู้ใช้): แผงข้อความเดียวกันที่ตัวอักษรเซ็ต
    เหมือนกันเป๊ะ (ความกว้างคำ 499 คู่ มัธยฐาน 0.9988) แต่ **หมึกหนากว่ากัน
    ทั้งแผง 4.3%** (เส้น 6.240 vs 5.981 px) ⇒ ขอบตัวอักษรทุกตัวต่าง ⇒
    ฟ้อง **109 บริเวณ** ทั้งที่ไม่มีคำไหนผิดเลย

    ⚠️ **ไม่ normalize และไม่ลบบริเวณไหนทิ้ง** — ความหนาที่ต่างกันเป็น
    defect งานพิมพ์จริงในบางเคส (กลุ่ม B ของ John West: ตัวหนา vs ตัวธรรมดา
    ซึ่งเป็นคุณค่าหลักของโหมดนี้) ⇒ แค่ **บอกตัวเลขให้ผู้ตรวจอ่าน** ว่า
    บริเวณจำนวนมากมาจากเรื่องนี้
    """
    try:
        from . import appearance
    except Exception:                       # pragma: no cover
        return None
    sa = appearance.ink_stats(img_a)
    sb = appearance.ink_stats(img_b)
    if not sa or not sb:
        return None
    ia, ib = sa.get("ink") or 0.0, sb.get("ink") or 0.0
    ka, kb = sa.get("stroke") or 0.0, sb.get("stroke") or 0.0
    if ia <= 0 or ib <= 0 or ka <= 0 or kb <= 0:
        return None
    return {"ink_a": round(ia, 5), "ink_b": round(ib, 5),
            "stroke_a": round(ka, 3), "stroke_b": round(kb, 3),
            "ink_pct": round(100.0 * (ib / ia - 1.0), 2),
            "stroke_pct": round(100.0 * (kb / ka - 1.0), 2),
            # ⚠️ **เปอร์เซ็นต์ขึ้นกับความละเอียด** — วัดคู่ V12/V13 เดียวกัน
            #    ได้ 4.3% ที่เส้นหนา 6.0 px แต่ 12.5% ที่เส้นหนา 2.5 px
            #    (ต่างกันจริง ~0.3 px เท่ากัน แต่ตัวหารเล็กลง) ⇒ เส้นที่บาง
            #    กว่า MIN_STROKE_PX คือ "อยู่ที่พื้นความละเอียด" ⇒ ห้ามยก
            #    เปอร์เซ็นต์ขึ้นพาดหัว ให้บอกค่าดิบแทน (กฎเหล็กข้อ 2)
            "reliable": bool(min(ka, kb) >= MIN_STROKE_PX)}


def region_center_mm(region: dict, size_px, mm_per_px: float):
    """จุดกึ่งกลางของบริเวณเป็นมิลลิเมตรจากมุมซ้ายบนของโซน."""
    px = region.get("px_a") or region.get("px") or [0, 0, 0, 0]
    return (round((px[0] + px[2] / 2.0) * mm_per_px, 1),
            round((px[1] + px[3] / 2.0) * mm_per_px, 1))


# ── แปลง "บริเวณที่ต่าง" เป็น defect หน้าตาเดียวกับชั้นเทียบข้อความ ──
#
# ข้อกำหนดจากผู้ใช้: โหมดทดลองต้อง **แสดงผลเหมือนเดิมทุกประการ** ⇒ ใช้คลาส
# ``MISMATCH_PANELS`` เดิม การ์ดจึงหน้าตาเหมือนเดิมทุกอย่าง
#
# ⚠️ ``found`` คือข้อความที่การ์ดโชว์และชั้นกรอบแดงใช้ค้นหา — ถ้าอ่านบริเวณ
#    นั้นไม่ได้ **ห้ามเดา** ให้ปล่อยว่างแล้วบอกตำแหน่งเป็นมิลลิเมตรแทน
#    (กฎเหล็กข้อ 2: กรอบที่ชี้ผิด แย่กว่าไม่มีกรอบ)

def regions_to_defects(res: dict, zone_a: dict, zone_b: dict,
                       read_region=None, inspect_region=None,
                       max_inspect: int = 0) -> List[dict]:
    """``(ผลจาก compare, โซน a, โซน b)`` → รายการ defect.

    ``read_region(which, px)`` = อ่านข้อความของบริเวณหนึ่ง (ทางเดิม)
    ``inspect_region(px)``     = ตรวจบริเวณหนึ่งแบบเต็ม (ทางใหม่) คืน dict
        ``{"a", "b", "relation", "look"}`` — ดู ``pipeline._pixel_compare``

    ⚠️ **ทำไมต้องมี ``relation``** (ผลรันจริง 8 ก.ย.): กลุ่มหนึ่งของสถานี
       ต่างกันเพราะ **ฟอนต์ตัวหนา vs ตัวธรรมดา** ตัวอักษรเหมือนกันเป๊ะ แต่
       การ์ดขึ้นว่า ``พบ: Manuf เทียบกับ: Manufa`` (ครอปตัดกลางคำ) ⇒ ผู้ตรวจ
       ไปตามหาคำสะกดผิดที่ไม่มีอยู่จริง = ผลที่ผิดแบบมั่นใจ (กฎเหล็กข้อ 2)
    """
    from . import checks as _checks
    out: List[dict] = []
    mmpp = float(res.get("mm_per_px") or 0.0)
    size = res.get("size") or [1, 1]
    la = zone_a.get("label") or zone_a.get("id")
    lb = zone_b.get("label") or zone_b.get("id")
    grp = zone_a.get("group") or ""
    regs = res.get("regions") or []
    # ⚠️ **เพดานจำนวนครั้งที่อ่านข้อความ** — ``pixdiff`` คืนได้ถึง 200 บริเวณ
    #    และแต่ละบริเวณอ่านสองฝั่ง ⇒ 400 ครั้ง/ใบ ที่ ~5 วินาที = 33 นาที
    #    และเผาโควตา. เกิดจริงได้เมื่อแนบไฟล์ผิดคู่/ลากครอบคนละแผง
    #    ``pixdiff`` เรียงบริเวณตามพื้นที่จากมากไปน้อยให้แล้ว ⇒ ที่ใหญ่สุด
    #    (สำคัญสุด) ได้อ่านก่อน · ที่เหลือ **ยังรายงานครบ** แต่บอกตำแหน่ง
    #    อย่างเดียว ไม่เดาข้อความ (กฎเหล็กข้อ 2)
    cap = int(max_inspect or 0)
    for i, g in enumerate(regs):
        x_mm, y_mm = region_center_mm(g, size, mmpp)
        where = "ตำแหน่ง %.1f, %.1f mm จากมุมซ้ายบนของโซน" % (x_mm, y_mm)
        found = ref = ""
        rel = "unknown"
        note = ""
        info = None
        over_cap = bool(cap) and i >= cap
        if over_cap:
            rel = "unread"
        elif inspect_region is not None:
            try:
                info = inspect_region(g["px"]) or None
            except Exception:                # pragma: no cover - กันพังล้วน
                info = None
        elif read_region is not None:
            try:
                a_txt = (read_region("a", g["px"]) or "").strip()
                b_txt = (read_region("b", g["px"]) or "").strip()
                info = {"a": a_txt, "b": b_txt}
            except Exception:                # pragma: no cover - กันพังล้วน
                info = None
        if info:
            from . import appearance as _ap
            found, ref = info.get("a", "") or "", info.get("b", "") or ""
            rel = info.get("relation") or _ap.relation(found, ref)
            note = ((info.get("look") or {}).get("note") or "")

        if rel == "different":
            msg = "กลุ่ม %s: %s กับ %s ต่างกันที่ %s" % (grp, la, lb, where)
        elif rel == "same":
            # ตัวอักษรเหมือนกัน ⇒ ความต่างอยู่ที่รูปลักษณ์ **ห้ามโชว์เป็น
            # "พบ X เทียบกับ Y"** เพราะจะอ่านเหมือนคำสะกดผิด
            msg = ("กลุ่ม %s: %s กับ %s — ตัวอักษรเหมือนกัน (\u201c%s\u201d) "
                   "แต่ภาพต่างกันที่ %s%s"
                   % (grp, la, lb, found[:60], where,
                      " · " + note if note else ""))
            found = ref = ""
        elif rel == "truncated":
            # ครอปตัดกลางคำ — ข้อความที่ได้ไม่ใช่ความต่างของงาน
            msg = ("กลุ่ม %s: %s กับ %s ต่างกันที่ %s "
                   "(อ่านข้อความตรงนั้นได้ไม่ครบ โปรดดูด้วยตา)"
                   % (grp, la, lb, where))
            found = ref = ""
        elif rel == "unread":
            # ไม่ได้อ่านเพราะ **เราตั้งเพดานไว้เอง** ไม่ใช่เพราะอ่านไม่ออก —
            # ต้องบอกตามจริง ไม่งั้นผู้ตรวจเข้าใจว่าภาพตรงนั้นอ่านไม่ได้
            msg = ("กลุ่ม %s: %s กับ %s ต่างกันที่ %s "
                   "(บริเวณที่ต่างมีจำนวนมาก — ไม่ได้อ่านข้อความตรงนี้ โปรดดูด้วยตา)"
                   % (grp, la, lb, where))
            found = ref = ""
        else:
            msg = ("กลุ่ม %s: %s กับ %s ต่างกันที่ %s "
                   "(เทียบจากภาพ — อ่านข้อความตรงนั้นไม่ได้ โปรดดูด้วยตา)"
                   % (grp, la, lb, where))
            found = ref = ""
        d = _checks._defect("MISMATCH_PANELS", zone_a["id"], msg,
                            found=found, reference=ref,
                            ref_zone_ids=[zone_b["id"]])
        # พิกัดที่ **วัดมา** ไม่ใช่ค้นหาเอา — ชั้นกรอบแดงใช้ได้ตรง ๆ ทั้งสองฝั่ง
        d["pixel_bbox"] = list(g.get("bbox") or [])
        d["pixel_bbox_b"] = list(g.get("bbox_b") or [])
        d["pixel_px"] = list(g.get("px") or [])
        d["pixel_relation"] = rel
        if note:
            d["pixel_look"] = note
        out.append(d)
    return out
