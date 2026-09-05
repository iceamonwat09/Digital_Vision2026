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

import cv2
import numpy as np

from . import pixdiff

# ── ค่าจูน (ที่มาของทุกตัวเลขอยู่ใน docstring ข้างบน) ────────────────
DPI = 400                  # แผงโภชนาการมีตัวเลขเล็ก — 400 ให้ bbox ที่ใช้ได้จริง
SCALE_LO, SCALE_HI = 0.60, 1.70    # ช่วงสเกลที่ยอมค้นหา (ไฟล์จริงอยู่ที่ 0.784)
SCALE_STEP = 0.004
MIN_SCALE_NCC = 0.55
TEMPLATE_MARGIN_FRAC = 0.22   # ต้อง >= (1 - SCALE_LO)/2 (มีเทสต์ล็อก)       # ต่ำกว่านี้ = คนละเนื้อหา ⇒ ไม่เทียบ (เกณฑ์เดียวกับ pixdiff)
TRIM_PX = 12               # ตัดขอบทิ้งก่อนหาบริเวณ (ขอบ = ที่เดียวที่ข้อมูลไม่ทับกัน)
BLUR_SIGMA = 1.0
TOLERANCE_PX = 1
MIN_REGION_PX = 40


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


def compare(path_a: str, bbox_a, path_b: str, bbox_b,
            dpi: int = DPI, page_index: int = 0,
            trim_px: int = TRIM_PX) -> dict:
    """เหมือน :func:`compare_ex` แต่คืนเฉพาะผล (ภาพถูกทิ้ง) — ใช้เมื่อจะเก็บ
    ลง report.json ซึ่ง serialize ภาพไม่ได้."""
    res, _a, _b = compare_ex(path_a, bbox_a, path_b, bbox_b, dpi,
                             page_index, trim_px)
    return res


def compare_ex(path_a: str, bbox_a, path_b: str, bbox_b,
               dpi: int = DPI, page_index: int = 0,
               trim_px: int = TRIM_PX):
    """เทียบแผงสองแผงที่อาจคนละขนาด/คนละสี → บริเวณที่ต่างจริง.

    คืน dict แบบเดียวกับ ``pixdiff.compare_zone`` (``status`` · ``reason`` ·
    ``regions`` เป็นสัดส่วนของ **โซน a**) บวก ``scale`` · ``ncc`` · ``ecc``
    """
    a, _ = pixdiff.render_zone_mm(path_a, bbox_a, dpi, page_index)
    b, _ = pixdiff.render_zone_mm(path_b, bbox_b, dpi, page_index)
    if a is None or b is None or a.size == 0 or b.size == 0:
        return (dict(pixdiff._skip("render_failed"), scale=0.0, ncc=0.0,
                     ecc=0.0), None, None)

    ga, gb = _to_gray(a), _to_gray(b)
    scale, loc, ncc = find_scale(ga, gb)
    if ncc < MIN_SCALE_NCC:
        # ไม่มั่นใจว่าเป็นเนื้อหาเดียวกัน ⇒ ไม่รายงานดีกว่าชี้ผิดที่
        return (dict(pixdiff._skip("align_failed"), scale=scale, ncc=ncc,
                     ecc=0.0), None, None)

    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    rb = cv2.resize(b, None, fx=scale, fy=scale, interpolation=interp)
    x0, y0 = max(0, loc[0]), max(0, loc[1])
    crop = rb[y0:y0 + a.shape[0], x0:x0 + a.shape[1]]
    h = min(crop.shape[0], a.shape[0])
    w = min(crop.shape[1], a.shape[1])
    if h < 8 or w < 8:
        return (dict(pixdiff._skip("align_failed"), scale=scale, ncc=ncc,
                     ecc=0.0), None, None)
    aa, bb = a[:h, :w], crop[:h, :w]

    bb = match_colors(aa, bb)
    bb, ecc = refine_align(aa, bb)

    m = max(0, min(int(trim_px), h // 4, w // 4))
    ai = aa[m:h - m, m:w - m] if m else aa
    bi = bb[m:h - m, m:w - m] if m else bb
    res = pixdiff.compare_images(ai, bi, blur_sigma=BLUR_SIGMA,
                                 tolerance_px=TOLERANCE_PX,
                                 min_region_px=MIN_REGION_PX)
    # แปลงพิกัดกลับเป็นสัดส่วนของ "โซน a" เต็มใบ (บวกขอบที่ตัดไปคืน)
    for g in res.get("regions") or []:
        px = list(g["px"])
        px[0] += m
        px[1] += m
        g["px"] = px
        g["bbox"] = [round(px[0] / float(w), 5), round(px[1] / float(h), 5),
                     round(px[2] / float(w), 5), round(px[3] / float(h), 5)]
    res.update(scale=round(scale, 4), ncc=round(ncc, 4), ecc=round(ecc, 4),
               size=[w, h], dpi=dpi, trim_px=m,
               mm_per_px=round(25.4 / float(dpi), 4))
    # คืนภาพที่ align แล้วทั้งสองฝั่ง (พิกัดตรงกันแล้ว) เพื่อให้ผู้เรียกครอป
    # บริเวณเดียวกันจากทั้งสองไฟล์ไปอ่านข้อความได้
    return res, aa, bb


def region_center_mm(region: dict, size_px, mm_per_px: float):
    """จุดกึ่งกลางของบริเวณเป็นมิลลิเมตรจากมุมซ้ายบนของโซน."""
    px = region.get("px") or [0, 0, 0, 0]
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
                       read_region=None) -> List[dict]:
    """``(ผลจาก compare, โซน a, โซน b)`` → รายการ defect.

    ``read_region(which, px)`` = ฟังก์ชันอ่านข้อความของบริเวณหนึ่ง
    (``which`` เป็น ``"a"`` หรือ ``"b"``) — ส่ง ``None`` ได้ ถ้าไม่มี OCR
    """
    from . import checks as _checks
    out: List[dict] = []
    mmpp = float(res.get("mm_per_px") or 0.0)
    size = res.get("size") or [1, 1]
    la = zone_a.get("label") or zone_a.get("id")
    lb = zone_b.get("label") or zone_b.get("id")
    grp = zone_a.get("group") or ""
    for g in res.get("regions") or []:
        x_mm, y_mm = region_center_mm(g, size, mmpp)
        found = ref = ""
        if read_region is not None:
            try:
                found = (read_region("a", g["px"]) or "").strip()
                ref = (read_region("b", g["px"]) or "").strip()
            except Exception:                    # pragma: no cover - กันพังล้วน
                found = ref = ""
        where = "ตำแหน่ง %.1f, %.1f mm จากมุมซ้ายบนของโซน" % (x_mm, y_mm)
        if found or ref:
            msg = ("กลุ่ม %s: %s กับ %s ต่างกันที่ %s" % (grp, la, lb, where))
        else:
            msg = ("กลุ่ม %s: %s กับ %s ต่างกันที่ %s "
                   "(เทียบจากภาพ — อ่านข้อความตรงนั้นไม่ได้ โปรดดูด้วยตา)"
                   % (grp, la, lb, where))
        d = _checks._defect("MISMATCH_PANELS", zone_a["id"], msg,
                            found=found, reference=ref,
                            ref_zone_ids=[zone_b["id"]])
        # พิกัดที่ **วัดมา** ไม่ใช่ค้นหาเอา — ชั้นกรอบแดงใช้ได้ตรง ๆ
        d["pixel_bbox"] = list(g.get("bbox") or [])
        d["pixel_px"] = list(g.get("px") or [])
        out.append(d)
    return out
