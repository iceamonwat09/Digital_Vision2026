#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
pixdiff_noise_scan.py — วัด "noise" ของการเทียบพิกเซลบนไฟล์จริงของคุณเอง
โดยใช้ **ไฟล์เดียวกันเทียบกับตัวเอง** แล้วจงใจทำให้คลาดเคลื่อนทีละแบบ

ทำไมต้องทำแบบนี้: เวลาใช้งานจริง ความคลาดเคลื่อนที่หลีกเลี่ยงไม่ได้ (โซนที่
ลากด้วยมือ · ตำแหน่งบนแผ่นต่างกันเศษพิกเซล · ไฟล์ถูกย่อ) จะทำให้เกิด
"บริเวณต่างปลอม" ซึ่ง **ไม่ได้โผล่เป็นประโยค แต่โผล่เป็นเศษตัวอักษรกระจัด
กระจาย** — ต้องรู้ว่ามันหน้าตาแบบไหนและเยอะแค่ไหน ถึงจะตั้งเกณฑ์ให้ถูก.

เนื่องจากใช้ไฟล์เดียวกันทั้งสองฝั่ง **ทุกบริเวณที่พบ = ของปลอม 100%**
(ground truth ชัดเจนโดยไม่ต้องมีคู่เก่า/ใหม่) ส่วนชั้น "สัญญาณจริง" จะสร้าง
การแก้ไขจำลองขนาดเท่าคำจริงลงบนไฟล์ แล้ววัดว่ายังจับได้อยู่ไหม.

    py -3.9 pixdiff_noise_scan.py --dir "D:\\Digital 2026\\Vision-Defect\\TEST"
    py -3.9 pixdiff_noise_scan.py --dir TEST --dpi 200 --out noise.json
    py -3.9 pixdiff_noise_scan.py --dir TEST --save-dir noiseimg   (ดูภาพ noise)

**อ่านอย่างเดียว** — ไม่แตะไฟล์ต้นฉบับและไม่แตะ data/ (ไฟล์จำลองสร้างใน
โฟลเดอร์ชั่วคราวแล้วลบทิ้ง)

exit code: 0 = มีค่าตั้งที่ใช้ได้ · 1 = ไม่มีค่าตั้งไหนผ่านทุกไฟล์ · 2 = รันไม่ได้
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    import cv2
    import fitz
    import numpy as np
    from artwork_check import pixdiff
except Exception as e:                        # pragma: no cover
    print("import ไม่สำเร็จ: %s" % e)
    sys.exit(2)

SUPER = 4          # เรนเดอร์ที่ SUPER เท่า แล้วย่อลง = จำลองการวางบนเศษพิกเซล
TOLS = (0, 1, 2)   # ค่าที่จะเทียบกัน


# ── การจำลองความคลาดเคลื่อน ──────────────────────────────────────────
def _render_super(path, dpi, page_index=0):
    """เรนเดอร์ที่ความละเอียดสูง เพื่อใช้สร้างภาพที่เลื่อนเป็นเศษพิกเซล"""
    with fitz.open(path) as doc:
        page = doc[page_index]
        zoom = dpi * SUPER / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8)
        img = img.reshape(pix.height, pix.width, pix.n)
        return (cv2.cvtColor(img, cv2.COLOR_RGB2BGR) if pix.n == 3
                else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))


def _down(hi, shift_sub=0):
    """ย่อจากภาพความละเอียดสูงลงมา 1 เท่า โดยเลื่อนก่อน ``shift_sub`` พิกเซล
    ของกริดสูง (shift_sub=1 → เลื่อน 1/SUPER พิกเซลของภาพจริง)"""
    src = np.roll(hi, shift_sub, axis=1) if shift_sub else hi
    h, w = src.shape[:2]
    return cv2.resize(src, (w // SUPER, h // SUPER), interpolation=cv2.INTER_AREA)


def _subpixel_correct(shifted, shift_sub):
    """จำลองผลของ 'sub-pixel alignment' ที่แม่นกว่าปัจจุบัน — ``compare_zone``
    ตอนนี้ align ได้แค่ระดับ **จำนวนเต็มพิกเซล** (``cv2.matchTemplate`` คืน
    ตำแหน่ง int) แล้วพึ่ง ``tolerance_px`` ดูดซับเศษที่เหลือ. ฟังก์ชันนี้
    แก้ตำแหน่งด้วยระยะที่ **รู้ล่วงหน้าแน่นอน** (เพราะเราเป็นคนสร้าง shift เอง)
    ผ่าน ``warpAffine`` แบบ sub-pixel — ใช้วัดว่า *ถ้า* อัปเกรด ``_locate()``
    ให้ align ละเอียดถึงระดับเศษพิกเซลได้จริง จะลด noise ลงได้แค่ไหน
    ก่อนจะลงทุนสร้างตัวประมาณค่าจริง (phase correlation / parabolic peak)."""
    dx = -shift_sub / float(SUPER)
    m = np.float32([[1, 0, dx], [0, 1, 0]])
    return cv2.warpAffine(shifted, m, (shifted.shape[1], shifted.shape[0]),
                          flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _rescaled_pair(hi, pct):
    """จำลอง 'ไฟล์ถูกย่อ/ขยายนิดหน่อยแล้วปรับกลับ' — วัด noise จากการ resample"""
    base = _down(hi)
    h, w = base.shape[:2]
    w2, h2 = max(2, int(round(w * (1 + pct)))), max(2, int(round(h * (1 + pct))))
    grown = cv2.resize(base, (w2, h2), interpolation=cv2.INTER_CUBIC)
    back = cv2.resize(grown, (w, h), interpolation=cv2.INTER_AREA)
    return base, back


def _make_edit(src_path, dst_path, mm_w=4.0, mm_h=2.0, page_index=0):
    """สร้าง 'การแก้ไขจำลอง' ขนาดประมาณหนึ่งคำ ทับบริเวณที่มีหมึกจริง
    → ใช้เป็นสัญญาณที่ระบบ **ต้องจับให้ได้** ไม่ว่าจะตั้ง tolerance เท่าไร"""
    bbox, _w, _h = pixdiff.content_bbox(src_path, page_index)
    if not bbox:
        return None
    with fitz.open(src_path) as doc:
        page = doc[page_index]
        r = page.rect
        # วางกลางกรอบเนื้อหา = มีหมึกอยู่แล้วแน่ ๆ
        cx = r.x0 + (bbox[0] + bbox[2] / 2) * r.width
        cy = r.y0 + (bbox[1] + bbox[3] / 2) * r.height
        pw, ph = mm_w / 25.4 * 72.0, mm_h / 25.4 * 72.0
        page.draw_rect(fitz.Rect(cx, cy, cx + pw, cy + ph),
                       color=(1, 0, 0), fill=(1, 0, 0))
        doc.save(dst_path)
    return dst_path


# ── การวัด ───────────────────────────────────────────────────────────
def _measure(a, b, tol, **kw):
    res = pixdiff.compare_images(a, b, tolerance_px=tol, **kw)
    if res["status"] != pixdiff.OK:
        return {"regions": -1, "diff_px": res.get("diff_px", 0), "areas": []}
    areas = [r["area_px"] for r in res["regions"]]
    return {"regions": res["region_count"], "diff_px": res["diff_px"],
            "areas": areas, "res": res}


# ── แนวทางที่เอามาเทียบกัน (candidate) ──────────────────────────────
# ทุกตัวต้องผ่านเกณฑ์เดียวกัน: noise เป็น 0 **และ** ยังจับสัญญาณ 4x2 mm ได้
CANDIDATES = [
    ("tol=1 (ปัจจุบัน)",        dict(tolerance_px=1)),
    ("tol=2",                   dict(tolerance_px=2)),
    ("tol=1 + เบลอ 0.6",        dict(tolerance_px=1, blur_sigma=0.6)),
    ("tol=1 + เบลอ 0.8",        dict(tolerance_px=1, blur_sigma=0.8)),
    ("tol=1 + เบลอ 1.0",        dict(tolerance_px=1, blur_sigma=1.0)),
    ("tol=2 + เบลอ 1.0",        dict(tolerance_px=2, blur_sigma=1.0)),
    ("tol=1 + เกณฑ์สี 96",       dict(tolerance_px=1, threshold=96)),
    ("tol=1 + เบลอ1 + สี64",     dict(tolerance_px=1, blur_sigma=1.0, threshold=64)),
]

# ⚠️ ขนาดของ "ความต่างจริง" ที่ต้องจับให้ได้ — ไล่จากใหญ่ไปเล็กสุดที่งาน QC
# ต้องการจริง. การเบลอก่อนเทียบลบรายละเอียดเล็กทิ้ง ⇒ **ต้องพิสูจน์ว่ามันไม่ได้
# ทำให้ตาบอดต่อความต่างที่เล็กแต่สำคัญ** เช่น จุดทศนิยมหาย (1.5 -> 15),
# ตัวอักษรเดียวเปลี่ยน, เครื่องหมาย ® หาย — ซึ่งเล็กกว่า 4x2 mm มาก
SIGNAL_SIZES_MM = [
    (4.0, 2.0, "ขนาดหนึ่งคำ"),
    (2.0, 1.0, "ตัวอักษร 1-2 ตัว"),
    (1.0, 1.0, "ตัวอักษรเดียว"),
    (0.6, 0.6, "จุดทศนิยม / ®"),
    (0.3, 0.3, "เล็กสุดที่ตายังเห็น"),
]


def _signal_on_image(img, mm_w, mm_h, dpi):
    """วาด 'ความต่างจำลอง' ขนาด mm ที่กำหนดลงบนภาพที่เรนเดอร์แล้ว.

    วาดบน raster (ไม่ใช่บน PDF) โดยตั้งใจ — ได้ขนาดที่ควบคุมได้แม่นยำและ
    เร็วกว่ามาก. เป็น "ความต่างที่ชัดที่สุดเท่าที่จะเป็นไปได้" ในขนาดนั้น
    ⇒ ถ้าขนาดนี้ยังจับไม่ได้ ความต่างจริงที่จาง ๆ ยิ่งจับไม่ได้แน่นอน
    (เป็นขอบเขตบนของความไว ไม่ใช่ค่าที่คาดหวังจากงานจริง).
    """
    out = img.copy()
    h, w = out.shape[:2]
    gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
    ink = (gray < 245).astype(np.uint8)
    if not np.count_nonzero(ink):
        return None
    x, y, bw, bh = cv2.boundingRect(ink)
    px_w = max(1, int(round(mm_w / 25.4 * dpi)))
    px_h = max(1, int(round(mm_h / 25.4 * dpi)))
    cx, cy = x + bw // 2, y + bh // 2
    x0 = min(max(0, cx), w - px_w - 1)
    y0 = min(max(0, cy), h - px_h - 1)
    out[y0:y0 + px_h, x0:x0 + px_w] = (0, 0, 255)     # แดงสด = ต่างชัดที่สุด
    return out


def scan_file(path, dpi, save_dir=""):
    """คืนผลการวัดของไฟล์เดียว: noise แต่ละแบบ × tolerance และสัญญาณจริง"""
    t0 = time.time()
    hi = _render_super(path, dpi)
    base = _down(hi)
    out = {"file": os.path.basename(path), "size": list(base.shape[:2][::-1]),
           "cases": {}}

    # ① เทียบตัวเองแบบไม่แตะอะไรเลย — ต้อง 0 เสมอ
    out["cases"]["ตรงเป๊ะ"] = {t: _measure(base, base, t) for t in TOLS}

    # ② เลื่อนเป็นเศษพิกเซล (จำลองตำแหน่งบนแผ่นที่ต่างกัน / โซนลากมือ)
    for sub in (1, 2, 4):                     # 0.25px, 0.5px, 1px
        shifted = _down(hi, shift_sub=sub)
        name = "เลื่อน %.2f px" % (sub / float(SUPER))
        out["cases"][name] = {t: _measure(base, shifted, t) for t in TOLS}

    # ③ สเกลเพี้ยนเล็กน้อยแล้วปรับกลับ (จำลองไฟล์ที่ถูกย่อ/ขยาย)
    for pct, label in ((0.002, "สเกลเพี้ยน 0.2%"), (0.01, "สเกลเพี้ยน 1%")):
        a, b = _rescaled_pair(hi, pct)
        out["cases"][label] = {t: _measure(a, b, t) for t in TOLS}

    # ④ สัญญาณจริง — การแก้ไขขนาดประมาณ 1 คำ (ต้องจับได้ทุก tolerance)
    tmp = tempfile.mkdtemp(prefix="noise_")
    base_e = None
    try:
        edited = _make_edit(path, os.path.join(tmp, "edited.pdf"))
        if edited:
            hi_e = _render_super(edited, dpi)
            base_e = _down(hi_e)
            out["cases"]["สัญญาณจริง: แก้ 4x2 mm"] = {
                t: _measure(base, base_e, t) for t in TOLS}
            # สัญญาณจริง + เลื่อน 0.5px พร้อมกัน = เคสที่ยากที่สุดของงานจริง
            shifted_e = _down(hi_e, shift_sub=2)
            out["cases"]["สัญญาณ + เลื่อน 0.5px"] = {
                t: _measure(base, shifted_e, t) for t in TOLS}
        # ⑤ เทียบ "แนวทาง" ต่าง ๆ บนเคสที่ยากที่สุด: เลื่อน 0.5px (noise)
        #    กับสัญญาณ 4x2 mm (ต้องจับได้) — ตัวไหนได้ทั้งสองอย่างถึงใช้ได้
        shifted_half = _down(hi, shift_sub=2)
        cand = {}
        for label, kw in CANDIDATES:
            noise = _measure(base, shifted_half, kw.get("tolerance_px", 1),
                             **{k: v for k, v in kw.items() if k != "tolerance_px"})
            sig = (_measure(base, base_e, kw.get("tolerance_px", 1),
                            **{k: v for k, v in kw.items() if k != "tolerance_px"})
                   if base_e is not None else None)
            cand[label] = {"noise": noise["regions"], "noise_px": noise["diff_px"],
                           "signal": sig["regions"] if sig else -1,
                           "noise_areas": noise["areas"]}
        out["candidates"] = cand

        # ⑥ ถ้า align ได้ละเอียดระดับเศษพิกเซล จะเหลือ noise เท่าไร
        corrected = _subpixel_correct(shifted_half, 2)
        out["subpixel_align"] = {
            t: _measure(base, corrected, t)["regions"] for t in TOLS}

        # ⑦ ความไวต่อความต่าง "ขนาดเล็ก" — ด่านสำคัญของการเบลอ
        #    เบลอลบรายละเอียดเล็กทิ้ง ต้องพิสูจน์ว่ายังเห็นจุดทศนิยมหาย
        sens = {}
        for mm_w, mm_h, label in SIGNAL_SIZES_MM:
            probe = _signal_on_image(base, mm_w, mm_h, dpi)
            if probe is None:
                continue
            per_cand = {}
            for cl, kw in CANDIDATES:
                m = _measure(base, probe, kw.get("tolerance_px", 1),
                             **{k: v for k, v in kw.items() if k != "tolerance_px"})
                per_cand[cl] = m["regions"]
            sens["%.1fx%.1f mm (%s)" % (mm_w, mm_h, label)] = per_cand
        out["sensitivity"] = sens
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        m = out["cases"].get("เลื่อน 0.50 px", {}).get(1, {}).get("res")
        if m and m["regions"]:
            img = pixdiff.draw_regions(base, m["regions"])
            name = os.path.splitext(os.path.basename(path))[0][:50]
            cv2.imwrite(os.path.join(save_dir, name + "_noise_tol1.png"), img)

    out["elapsed_s"] = round(time.time() - t0, 1)
    return out


MINS = (40, 150, 400, 800)   # เพดานขนาดบริเวณที่จะรายงาน (px)


def _count_at_min(areas, m):
    """จำนวนบริเวณที่เหลือถ้าตัดบริเวณเล็กกว่า m ทิ้ง — คำนวณจากผลเดิมได้เลย
    ไม่ต้องเทียบภาพใหม่"""
    return sum(1 for a in areas if a >= m)


def _areas_summary(areas):
    if not areas:
        return "-"
    a = sorted(areas)
    small = sum(1 for v in a if v <= 150)
    return "n=%d กลาง=%d px ใหญ่สุด=%d px เศษเล็ก(<=150px)=%d" % (
        len(a), a[len(a) // 2], a[-1], small)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="วัด noise ของการเทียบพิกเซลด้วยไฟล์จริงเทียบกับตัวเอง")
    ap.add_argument("--dir", default="", help="โฟลเดอร์ PDF ที่จะสแกน")
    ap.add_argument("--files", nargs="*", default=[], help="ระบุไฟล์เอง")
    ap.add_argument("--dpi", type=int, default=pixdiff.PIXDIFF_DPI)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--save-dir", default="", help="เขียนภาพ noise ให้ดูด้วยตา")
    ap.add_argument("--out", default="", help="บันทึกผลดิบเป็น JSON")
    args = ap.parse_args()

    paths = list(args.files)
    if args.dir:
        paths += [os.path.join(args.dir, f) for f in sorted(os.listdir(args.dir))
                  if f.lower().endswith(".pdf")]
    paths = [p for p in paths if os.path.isfile(p)]
    if not paths:
        print("ไม่พบไฟล์ PDF — ระบุ --dir หรือ --files")
        return 2
    if args.limit:
        paths = paths[:args.limit]

    print("=" * 100)
    print("pixdiff_noise_scan — ไฟล์เดียวกันเทียบตัวเอง: ทุกบริเวณที่พบ = ของปลอม 100%")
    print("=" * 100)
    print("%d ไฟล์ · DPI %d · เกณฑ์ต่าง %d/255 · บริเวณเล็กกว่า %d px ไม่นับ"
          % (len(paths), args.dpi, pixdiff.DIFF_THRESHOLD, pixdiff.MIN_REGION_PX))
    print("ค่า tolerance ที่เทียบกัน: %s  (ปัจจุบันโหมดโซนใช้ %d)"
          % (", ".join(str(t) for t in TOLS), pixdiff.ZONE_TOLERANCE_PX))

    results = []
    for i, p in enumerate(paths, 1):
        print("\n" + "#" * 100)
        print("# [%d/%d] %s" % (i, len(paths), os.path.basename(p)))
        print("#" * 100)
        try:
            r = scan_file(p, args.dpi, args.save_dir)
        except Exception as e:
            print("  ✗ สแกนไม่สำเร็จ: %s: %s" % (type(e).__name__, e))
            continue
        results.append(r)
        print("  ขนาดที่ %d DPI: %s px · ใช้เวลา %.1f วิ"
              % (args.dpi, r["size"], r["elapsed_s"]))
        print("  %-26s %-22s %-22s %-22s" % ("กรณี", "tol=0", "tol=1", "tol=2"))
        for case, per_tol in r["cases"].items():
            cells = []
            for t in TOLS:
                m = per_tol[t]
                cells.append("%d บริเวณ/%d px" % (m["regions"], m["diff_px"]))
            print("  %-26s %-22s %-22s %-22s" % (case, cells[0], cells[1], cells[2]))
        # หน้าตาของ noise: เศษเล็ก ๆ กระจาย หรือก้อนใหญ่
        for label in ("เลื่อน 0.50 px", "สเกลเพี้ยน 0.2%"):
            m1 = r["cases"].get(label, {}).get(1)
            if m1 and m1["areas"]:
                print("  หน้าตา noise ที่ tol=1 (%s): %s"
                      % (label, _areas_summary(m1["areas"])))

        # ตัดเศษเล็กทิ้งช่วยได้แค่ไหน (ที่ tol=1)
        print("  %-26s %s" % ("ถ้าตัดบริเวณเล็กกว่า (tol=1)",
                              "  ".join("%dpx" % m for m in MINS)))
        for case, per_tol in r["cases"].items():
            areas = per_tol[1]["areas"]
            cells = "   ".join("%4d" % _count_at_min(areas, m) for m in MINS)
            print("  %-26s %s" % (case, cells))

        # แนวทางอื่นบนเคสที่ยากที่สุด (เลื่อน 0.5px)
        if r.get("candidates"):
            print("  %-26s %-14s %-14s" % ("แนวทาง (เลื่อน 0.5px)",
                                           "noise", "สัญญาณ 4x2mm"))
            for label, c in r["candidates"].items():
                ok = "✓" if (c["noise"] == 0 and c["signal"] >= 1) else " "
                print("  %s %-24s %-14s %-14s"
                      % (ok, label, "%d บริเวณ" % c["noise"],
                         "จับได้ %d" % c["signal"]))
        if r.get("subpixel_align"):
            print("  ถ้า align ละเอียดระดับเศษพิกเซลได้: %s"
                  % " · ".join("tol=%d → %d บริเวณ" % (t, n)
                               for t, n in sorted(r["subpixel_align"].items())))
        if r.get("sensitivity"):
            print("  ความไวต่อความต่างขนาดเล็ก (จำนวนบริเวณที่จับได้):")
            for size, per in r["sensitivity"].items():
                cells = " ".join("%s=%d" % (cl.replace("tol=", "t"), n)
                                 for cl, n in per.items()
                                 if "เบลอ 1.0" in cl or cl.startswith("tol=1 ("))
                print("    %-30s %s" % (size, cells))

    if not results:
        return 2

    # ── สรุปรวม: เลือก tolerance ที่ "noise เป็น 0 ทุกไฟล์ แต่สัญญาณยังจับได้" ──
    print("\n" + "=" * 100)
    print("สรุปรวมทุกไฟล์ — noise สูงสุดที่พบ (ยิ่งน้อยยิ่งดี) และสัญญาณที่ยังจับได้")
    print("=" * 100)
    noise_cases = [c for c in results[0]["cases"] if not c.startswith("สัญญาณ")]
    signal_cases = [c for c in results[0]["cases"] if c.startswith("สัญญาณ")]

    print("%-26s %-14s %-14s %-14s" % ("กรณี", "tol=0", "tol=1", "tol=2"))
    print("-" * 100)
    for case in noise_cases:
        row = []
        for t in TOLS:
            worst = max((r["cases"][case][t]["regions"] for r in results
                         if case in r["cases"]), default=0)
            n_bad = sum(1 for r in results
                        if case in r["cases"] and r["cases"][case][t]["regions"] > 0)
            row.append("%d บริเวณ (%d ไฟล์)" % (worst, n_bad))
        print("%-26s %-14s %-14s %-14s" % (case, row[0], row[1], row[2]))
    print("-" * 100)
    for case in signal_cases:
        row = []
        for t in TOLS:
            got = [r["cases"][case][t]["regions"] for r in results if case in r["cases"]]
            missed = sum(1 for v in got if v <= 0)
            row.append("จับได้ %d/%d" % (len(got) - missed, len(got)))
        print("%-26s %-14s %-14s %-14s" % (case, row[0], row[1], row[2]))

    # ── หา (tolerance, min_region) ที่ดีที่สุด ──
    print("\n" + "=" * 100)
    print("ค้นหาค่าตั้งที่ใช้ได้: noise = 0 ทุกไฟล์ **และ** ยังจับสัญญาณจริงได้ครบ")
    print("=" * 100)

    def worst_noise(t, m, cases):
        return max((_count_at_min(r["cases"][c][t]["areas"], m)
                    for r in results for c in cases if c in r["cases"]),
                   default=0)

    def signal_hits(t, m):
        got = [(r, c) for r in results for c in signal_cases if c in r["cases"]]
        ok = sum(1 for r, c in got
                 if _count_at_min(r["cases"][c][t]["areas"], m) >= 1)
        return ok, len(got)

    shift_cases = [c for c in noise_cases if c.startswith("เลื่อน") or c == "ตรงเป๊ะ"]
    scale_cases = [c for c in noise_cases if c.startswith("สเกล")]

    best = None
    print("%-10s %-10s %-16s %-16s %-14s" % ("tolerance", "min_region",
                                             "noise:เลื่อน", "noise:สเกล",
                                             "สัญญาณจริง"))
    print("-" * 100)
    for t in TOLS:
        for m in MINS:
            n_shift = worst_noise(t, m, shift_cases)
            n_scale = worst_noise(t, m, scale_cases)
            ok, tot = signal_hits(t, m)
            usable = (n_shift == 0 and ok == tot)
            if usable and best is None:
                best = (t, m, n_scale)
            print("%-10d %-10d %-16d %-16d %s%s"
                  % (t, m, n_shift, n_scale, "จับได้ %d/%d" % (ok, tot),
                     "   ← ใช้ได้" if usable else ""))

    # ── สรุปแนวทางอื่น ๆ รวมทุกไฟล์ ──
    if results[0].get("candidates"):
        print("\n" + "=" * 100)
        print("เทียบ 'แนวทาง' ทั้งหมดบนเคสที่ยากที่สุด (เลื่อน 0.5px) — ทุกไฟล์รวมกัน")
        print("=" * 100)
        print("%-24s %-18s %-18s %-16s" % ("แนวทาง", "noise สูงสุด/ไฟล์",
                                           "ไฟล์ที่ noise=0", "สัญญาณจับได้"))
        print("-" * 100)
        for label, _kw in CANDIDATES:
            ns = [r["candidates"][label]["noise"] for r in results
                  if label in r.get("candidates", {})]
            sg = [r["candidates"][label]["signal"] for r in results
                  if label in r.get("candidates", {})]
            clean = sum(1 for n in ns if n == 0)
            hit = sum(1 for s in sg if s >= 1)
            mark = "  ← ใช้ได้" if (clean == len(ns) and hit == len(sg)) else ""
            print("%-24s %-18d %-18s %-16s%s"
                  % (label, max(ns) if ns else 0,
                     "%d/%d" % (clean, len(ns)), "%d/%d" % (hit, len(sg)), mark))

        sub = {t: max((r["subpixel_align"][t] for r in results
                       if t in r.get("subpixel_align", {})), default=-1)
               for t in TOLS}
        print("\nถ้าอัปเกรด align ให้ละเอียดระดับเศษพิกเซล (sub-pixel): %s"
              % " · ".join("tol=%d → สูงสุด %d บริเวณ" % (t, n)
                           for t, n in sorted(sub.items()) if n >= 0))

    # ── ความไวต่อความต่างขนาดเล็ก: ด่านที่การเบลอต้องผ่าน ──
    if results[0].get("sensitivity"):
        print("\n" + "=" * 100)
        print("ความไว — ความต่างเล็กแค่ไหนที่ยัง 'จับได้ครบทุกไฟล์'")
        print("(ตัวเลข = จำนวนไฟล์ที่จับได้ จาก %d · ต้องได้ครบถึงจะปลอดภัย)"
              % len(results))
        print("=" * 100)
        sizes = list(results[0]["sensitivity"].keys())
        head = "%-24s" % "แนวทาง"
        for s in sizes:
            head += " %-14s" % s.split(" (")[0]
        print(head)
        print("-" * 100)
        for label, _kw in CANDIDATES:
            row = "%-24s" % label
            for s in sizes:
                hit = sum(1 for r in results
                          if r.get("sensitivity", {}).get(s, {}).get(label, 0) >= 1)
                row += " %-14s" % ("%d/%d" % (hit, len(results)))
            print(row)
        print("\nขนาดจริงเป็นพิกเซลที่ %d DPI (เทียบกับเกณฑ์ MIN_REGION_PX = %d):"
              % (args.dpi, pixdiff.MIN_REGION_PX))
        for w, h, lb in SIGNAL_SIZES_MM:
            pw = w / 25.4 * args.dpi
            ph = h / 25.4 * args.dpi
            area = pw * ph
            flag = ("  ← เล็กกว่าเกณฑ์ MIN_REGION_PX จึงถูกตัดทิ้งไม่ว่าจะตั้ง "
                    "blur เท่าไร" if area < pixdiff.MIN_REGION_PX else "")
            print("   %.1fx%.1f mm = %.1fx%.1f px = %.0f px²  (%s)%s"
                  % (w, h, pw, ph, area, lb, flag))
        print("\n⚠️ นี่คือ 'ความต่างที่ชัดที่สุด' ในแต่ละขนาด (สี่เหลี่ยมแดงทึบ)")
        print("   ความต่างจริงที่จาง ๆ จะจับได้ยากกว่านี้ ⇒ เป็นขอบเขตบนของความไว")
        print("⚠️ ถ้าขนาดเล็กจับไม่ได้ **ทุกแนวทางเหมือนกัน** แปลว่าเพดานอยู่ที่")
        print("   DPI/MIN_REGION_PX ไม่ใช่การเบลอ — แก้ด้วยการเพิ่ม DPI แทน")

    print("\n" + "=" * 100)
    if best:
        t, m, n_scale = best
        print("  ⇒ ค่าที่ใช้ได้: **tolerance = %d · min_region = %d px**" % (t, m))
        print("     (ค่าปัจจุบันในโค้ด: tolerance=%d · min_region=%d)"
              % (pixdiff.ZONE_TOLERANCE_PX, pixdiff.MIN_REGION_PX))
        if n_scale:
            print("\n  ⚠️ แต่ค่านี้ยัง **แก้ noise จากสเกลไม่ได้** (เหลือ %d บริเวณ)"
                  % n_scale)
            print("     ⇒ สเกลไม่ตรงต้องเป็น **ด่านปฏิเสธ** ไม่ใช่พยายามเทียบแล้วกรอง")
            print("     ⇒ ก่อนเทียบต้องยืนยันว่าเนื้อหาขนาดจริงเท่ากัน "
                  "(content_bbox) ไม่ใช่แค่ 'ใกล้เคียง'")
    else:
        print("  ⚠️ ไม่มีคู่ค่าไหนที่ noise การเลื่อนเป็นศูนย์พร้อมจับสัญญาณได้ครบ")
        print("     ดูตารางรายไฟล์ว่าไฟล์ไหนเป็นตัวปัญหา + ภาพใน --save-dir")

    if args.out:
        slim = [{"file": r["file"], "size": r["size"],
                 "cases": {c: {str(t): {k: v for k, v in m.items() if k != "res"}
                               for t, m in per.items()}
                           for c, per in r["cases"].items()}} for r in results]
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(slim, f, ensure_ascii=False, indent=1)
        print("\n  💾 ผลดิบ: %s" % args.out)
    return 0 if best is not None else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
