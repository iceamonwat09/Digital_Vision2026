#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
verify_pixdiff.py — วัดว่า "เทียบพิกเซล ฉบับเก่า ↔ ฉบับใหม่" ใช้งานได้จริงไหม
**ก่อน**ที่จะเอาไปทำเป็นหน้าเว็บ

ทำไมต้องวัดก่อนทำ UI: ตัวเลขที่สำคัญที่สุดของชั้นนี้คือ **false positive**
(บริเวณที่รายงานว่าต่างทั้งที่ไม่ต่าง). ถ้าตัวเลขนี้ไม่ดี ฟีเจอร์นี้จะทำให้
ผู้ตรวจไล่ของปลอมทั้งวันแล้วเลิกเชื่อระบบ — ต้องรู้ก่อนลงแรงทำหน้าจอ.

    :: ① self-test — ไม่ต้องมีไฟล์ของคุณเลย (สร้าง PDF ทดสอบเอง)
    py -3.9 verify_pixdiff.py --selftest

    :: ② เทียบไฟล์จริงคู่หนึ่ง (a = ฉบับใหม่, b = ฉบับเก่า/ที่อนุมัติแล้ว)
    py -3.9 verify_pixdiff.py --pair "new.pdf" "old.pdf" [--save-dir out]

    :: ③ เทียบทุกคู่ในโฟลเดอร์ที่ "ชื่อไฟล์เหมือนกัน" (โฟลเดอร์ใหม่ vs เก่า)
    py -3.9 verify_pixdiff.py --new-dir NEW --old-dir OLD [--save-dir out]

**อ่านอย่างเดียว** ไม่แตะ `data/` และไม่เกี่ยวกับผลตรวจ QC เลย (advisory)
`--save-dir` เท่านั้นที่เขียนไฟล์ และเขียนเฉพาะในโฟลเดอร์ที่ระบุ

exit code: 0 = ผ่าน · 1 = ไม่ผ่าน/พบความต่าง · 2 = รันไม่ได้
"""

from __future__ import annotations

import argparse
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
    from artwork_check import pixdiff
except Exception as e:                        # pragma: no cover
    print("import ไม่สำเร็จ: %s" % e)
    print("ต้องมี PyMuPDF + opencv — py -3.9 -m pip install -r requirements.txt")
    sys.exit(2)


# ── PDF ทดสอบที่สร้างเอง (ใช้ใน --selftest) ─────────────────────────
_LINES = [
    "TUNA IN SUNFLOWER OIL",
    "INGREDIENTS: TUNA, SUNFLOWER OIL, SALT",
    "NET WEIGHT {net} g",
    "PRODUCT OF THAILAND",
    "BEST BEFORE 12/2027",
    "NUTRITION PER 100 g",
    "Energy 170 kcal",
    "Protein 24 g",
]


def _make_pdf(path, net=170, shift_pt=0.0, page_w=842, page_h=595,
              color=None):
    """PDF 3 แผงเหมือนกัน — ความต่างที่ใส่เข้าไปจะอยู่ที่ **แผงกลางเท่านั้น**
    (col == 1) เพื่อให้เทสต์พิสูจน์ได้ว่ากรอบชี้ตรงแผงที่เปลี่ยนจริง"""
    doc = fitz.open()
    page = doc.new_page(width=page_w, height=page_h)
    for col in range(3):
        x = 40 + col * 260
        for i, t in enumerate(_LINES):
            mid = (col == 1)
            dy = shift_pt if (mid and i == 3) else 0.0
            c = color if (color and mid and i == 6) else (0, 0, 0)
            n = net if mid else 170
            page.insert_text((x, 60 + i * 16 + dy), t.format(net=n),
                             fontsize=9, fontname="helv", color=c)
    doc.save(path)
    doc.close()


def _make_panel(path, page_w, page_h, at=(60, 80), net=170):
    """ฉลาก 1 แผงขนาดจริงเท่ากันเสมอ วางบนหน้าขนาดใดก็ได้ ที่ตำแหน่งใดก็ได้
    — จำลองเคสจริง 'งานเดียวกันคนละ layout' (A4 proof vs แผ่นพิมพ์ใหญ่)"""
    doc = fitz.open()
    page = doc.new_page(width=page_w, height=page_h)
    x0, y0 = at
    page.draw_rect(fitz.Rect(x0, y0, x0 + 220, y0 + 140),
                   color=(0, 0, 0), width=1)
    for i, t in enumerate(_LINES):
        page.insert_text((x0 + 10, y0 + 20 + i * 15), t.format(net=net),
                         fontsize=8, fontname="helv")
    doc.save(path)
    doc.close()


def _fmt(res):
    if res["status"] != pixdiff.OK:
        return "ไม่เทียบ (%s)" % res["reason"]
    return ("%d บริเวณ · %d พิกเซลต่าง (%.5f%% ของหน้า)"
            % (res["region_count"], res["diff_px"], res["diff_ratio"] * 100))


class Suite:
    def __init__(self):
        self.fail = 0
        self.pass_ = 0

    def check(self, cond, name, got="", why=""):
        if cond:
            self.pass_ += 1
            print("  ✓ %s" % name)
            if got:
                print("        %s" % got)
        else:
            self.fail += 1
            print("  ✗ %s" % name)
            if got:
                print("        ได้: %s" % got)
            if why:
                print("        ความหมาย: %s" % why)
        return bool(cond)

    def info(self, name, got=""):
        print("  · %s%s" % (name, ("\n        " + got) if got else ""))


def selftest(dpi: int) -> int:
    s = Suite()
    tmp = tempfile.mkdtemp(prefix="pixdiff_")
    try:
        base = os.path.join(tmp, "base.pdf")
        same = os.path.join(tmp, "same.pdf")
        digit = os.path.join(tmp, "digit.pdf")
        shift08 = os.path.join(tmp, "shift08.pdf")
        colored = os.path.join(tmp, "color.pdf")
        a3 = os.path.join(tmp, "a3.pdf")
        _make_pdf(base)
        _make_pdf(same)                                  # เนื้อหาเหมือนเป๊ะ
        _make_pdf(digit, net=185)                        # ตัวเลขเปลี่ยน
        _make_pdf(shift08, shift_pt=0.8)                 # ขยับ 0.8pt
        _make_pdf(colored, color=(0.85, 0.1, 0.1))       # สีเพี้ยนบรรทัดเดียว
        _make_pdf(a3, page_w=1191, page_h=842)           # คนละขนาดหน้า

        print("\n① false positive — ไฟล์เนื้อหาเดียวกันต้องได้ 0 บริเวณ")
        t0 = time.time()
        r = pixdiff.compare_files(base, same, dpi=dpi)
        el = time.time() - t0
        s.check(r["status"] == pixdiff.OK and r["region_count"] == 0
                and r["diff_px"] == 0, "เทียบไฟล์ที่เหมือนกัน → 0 พิกเซล",
                got=_fmt(r),
                why="ถ้าไม่เป็น 0 = ชั้นนี้จะรายงานของปลอมทุกครั้งที่ใช้งานจริง")
        s.info("เวลาที่ใช้", "%.2f วินาที/หน้า ที่ %d DPI (ขนาด %s)"
               % (el, dpi, r.get("size")))

        print("\n② ความไว — ต้องจับความต่างที่คนตาเปล่ามองข้ามได้")
        for path, name, hint in (
                (digit, "ตัวเลขเปลี่ยน (170 → 185 g)", "คือเคสที่ QC ต้องจับให้ได้"),
                (shift08, "ข้อความขยับ 0.8pt", "เล็กกว่าที่คนสังเกตเห็น"),
                (colored, "สีของข้อความเปลี่ยน", "ชั้นตรวจข้อความมองไม่เห็นเลย")):
            r = pixdiff.compare_files(base, path, dpi=dpi)
            s.check(r["status"] == pixdiff.OK and r["region_count"] >= 1,
                    "จับได้: %s" % name, got=_fmt(r), why=hint)

        print("\n③ ตำแหน่งที่ชี้ต้องถูกจุด ไม่ใช่แค่ 'มีอะไรต่าง'")
        r = pixdiff.compare_files(base, digit, dpi=dpi)
        if r["status"] == pixdiff.OK and r["regions"]:
            x, y, w, h = r["regions"][0]["bbox"]
            # แผงกลางเริ่มที่ x=300pt จาก 842 → 0.356 · บรรทัด NET WEIGHT
            # อยู่ที่ y=92pt จาก 595 → 0.155 (ลบความสูงตัวอักษรแล้ว ~0.13)
            in_x = 0.30 <= x <= 0.65
            in_y = 0.08 <= y <= 0.25
            s.check(in_x and in_y,
                    "กรอบชี้ไปที่บรรทัด NET WEIGHT ของแผงกลางจริง",
                    got="bbox=%s (คาด x 0.30-0.65, y 0.08-0.25)"
                        % [round(float(v), 3) for v in (x, y, w, h)],
                    why="ชี้ผิดที่ = ผู้ตรวจไปแก้ของที่ไม่ผิด")
            s.check(r["region_count"] <= 2,
                    "รายงานเป็นบริเวณเดียว ไม่แตกเป็นสิบ ๆ ชิ้น",
                    got="%d บริเวณ" % r["region_count"])
            s.check(all(0.30 <= rr["bbox"][0] <= 0.70 for rr in r["regions"]),
                    "ไม่มีกรอบไปโผล่ที่แผงซ้าย/ขวาที่ไม่ได้เปลี่ยน",
                    got="x ของทุกกรอบ: %s"
                        % [round(float(rr["bbox"][0]), 3) for rr in r["regions"]],
                    why="กรอบที่ชี้แผงที่ไม่ได้แก้ = false positive เต็ม ๆ")
        else:
            s.check(False, "กรอบชี้ไปที่บรรทัดที่เปลี่ยนจริง", got=_fmt(r))

        print("\n④ ด่านความปลอดภัย — ขนาดหน้าไม่เท่ากันต้อง 'ไม่เทียบ'")
        r = pixdiff.compare_files(base, a3, dpi=dpi)
        s.check(r["status"] == pixdiff.SKIPPED
                and r["reason"] == "page_size_mismatch",
                "A4 vs A3 → ไม่เทียบ พร้อมบอกขนาดจริงทั้งสองไฟล์",
                got=r.get("message", "")[:150],
                why="ถ้าฝืนเทียบจะได้บริเวณปลอมหลักร้อย (วัดมาแล้ว 370)")
        s.check(not r["regions"] and r["diff_px"] == 0,
                "เคสไม่เทียบต้องไม่มีผลลัพธ์หลอกออกมา",
                got="regions=%d diff_px=%d" % (len(r["regions"]), r["diff_px"]))

        print("\n⑤ โหมดโซน — เทียบได้แม้หน้าคนละขนาด (เคสจริงที่พบบ่อยที่สุด)")
        # จำลองเคสจริง: แผงเดียวกันวางบน A4 กับบนแผ่นใหญ่คนละตำแหน่ง
        small = os.path.join(tmp, "panel_a4.pdf")
        big = os.path.join(tmp, "panel_big.pdf")
        big_edit = os.path.join(tmp, "panel_big_edit.pdf")
        _make_panel(small, page_w=842, page_h=595, at=(60, 80))
        _make_panel(big, page_w=2148, page_h=1290, at=(900, 700))
        _make_panel(big_edit, page_w=2148, page_h=1290, at=(900, 700), net=185)

        # โซนครอบแผงเดียวกันบนสองหน้า (สัดส่วนต่างกันเพราะหน้าคนละขนาด)
        z_small = [55 / 842.0, 60 / 595.0, 230 / 842.0, 150 / 595.0]
        z_big = [890 / 2148.0, 680 / 1290.0, 240 / 2148.0, 160 / 1290.0]

        r = pixdiff.compare_zone(small, z_small, big, z_big, dpi=dpi)
        s.check(r["status"] == pixdiff.OK and r["region_count"] == 0,
                "แผงเดียวกันบนหน้า A4 vs แผ่นใหญ่ → เทียบได้ และไม่มีความต่างปลอม",
                got="%s · คะแนนจับคู่ %.3f · เนื้อหาขยับ %s mm"
                    % (_fmt(r), r.get("match_score", 0), r.get("shift_mm")),
                why="นี่คือเคสที่โหมดทั้งหน้าทำไม่ได้ (ขนาดหน้าไม่เท่ากัน)")

        r = pixdiff.compare_zone(small, z_small, big_edit, z_big, dpi=dpi)
        s.check(r["status"] == pixdiff.OK and r["region_count"] >= 1,
                "แผงเดียวกันแต่ตัวเลขเปลี่ยน → จับได้ทั้งที่หน้าคนละขนาด",
                got=_fmt(r))
        if r["status"] == pixdiff.OK and r["regions"]:
            s.check(r["region_count"] <= 2,
                    "ยังชี้เป็นบริเวณเดียว ไม่แตกเป็นเศษ",
                    got="%d บริเวณ" % r["region_count"])

        r = pixdiff.compare_zone(small, z_small, big, [0.05, 0.05, 0.1, 0.1],
                                 dpi=dpi)
        s.check(r["status"] == pixdiff.SKIPPED and r["reason"] in
                ("align_failed", "zone_too_different", "zone_empty",
                 "zone_blank"),
                "ลากโซนผิดที่ (พื้นที่ว่าง) → ไม่รายงาน แทนที่จะชี้มั่ว",
                got="%s — %s" % (r["reason"], r.get("message", "")[:90]),
                why="กฎเหล็กข้อ 2: ไม่มั่นใจ ไม่แสดง ดีกว่าชี้ผิดจุด")

        print("\n⑥ เสถียร — รันซ้ำต้องได้เลขเดิมเป๊ะ (deterministic)")
        r1 = pixdiff.compare_files(base, digit, dpi=dpi)
        r2 = pixdiff.compare_files(base, digit, dpi=dpi)
        s.check(r1["diff_px"] == r2["diff_px"]
                and r1["region_count"] == r2["region_count"],
                "รัน 2 ครั้งได้ผลเท่ากัน",
                got="%d/%d vs %d/%d" % (r1["diff_px"], r1["region_count"],
                                        r2["diff_px"], r2["region_count"]),
                why="ไม่ deterministic = วัดซ้ำไม่ได้ ใช้กับ QC ไม่ได้")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 72)
    print("selftest: ผ่าน %d · ไม่ผ่าน %d" % (s.pass_, s.fail))
    print("=" * 72)
    return 1 if s.fail else 0


def _auto_zone(new_path, old_path, dpi):
    """หา 'กรอบที่มีหมึก' ของทั้งสองไฟล์ แล้วบอกว่าเนื้อหาขนาดจริงเท่ากันไหม.

    นี่คือคำถามที่ตัดสินว่าเทียบรายโซนได้หรือไม่ เมื่อหน้าคนละขนาด
    """
    ba, wa, ha = pixdiff.content_bbox(new_path)
    bb, wb, hb = pixdiff.content_bbox(old_path)
    print("  เนื้อหา (กรอบที่มีหมึก) — ใหม่: %s mm · เก่า: %s mm"
          % ("%.0fx%.0f" % (wa, ha) if ba else "?",
             "%.0fx%.0f" % (wb, hb) if bb else "?"))
    if not ba or not bb:
        print("  ⚠ หากรอบเนื้อหาไม่ได้ (หน้าว่าง?)")
        return None, None
    if wb and hb:
        rw, rh = wa / wb, ha / hb
        # เกณฑ์คิดจากพิกเซลจริง ไม่ใช่เลขกลม ๆ — โซนยิ่งใหญ่ยิ่งทนสเกลเพี้ยน
        # ได้น้อย (วัดแล้ว: เพี้ยน 0.2% = บริเวณปลอมนับร้อย กรองไม่ออก)
        zone_px = max(wa, ha) / 25.4 * dpi
        allow = pixdiff.scale_allowance(int(zone_px))
        if abs(rw - 1) > allow or abs(rh - 1) > allow:
            print("  ⚠ เนื้อหาขนาดจริงไม่เท่ากัน (อัตราส่วน %.3f x %.3f · "
                  "ยอมได้ ±%.3f%%) — ไฟล์หนึ่งถูกย่อ/ขยาย" % (rw, rh, allow * 100))
            print("    ⇒ เทียบพิกเซลไม่ได้ และ **กรองทีหลังไม่ได้ด้วย** "
                  "(วัดแล้ว: เพี้ยน 0.2% ให้บริเวณปลอม 54-239 จุด)")
            return None, None
        print("  ✓ เนื้อหาขนาดจริงเท่ากัน (ต่าง %.3f%% · ยอมได้ %.3f%%) — "
              "เทียบรายโซนได้" % (abs(rw - 1) * 100, allow * 100))
    return ba, bb


def compare_one(new_path: str, old_path: str, dpi: int, save_dir: str = "",
                verbose: bool = False, zone_a=None, zone_b=None,
                auto_zone: bool = False) -> dict:
    t0 = time.time()
    if auto_zone or (zone_a and zone_b):
        print("\n" + "-" * 72)
        print("ใหม่: %s" % os.path.basename(new_path))
        print("เก่า: %s" % os.path.basename(old_path))
        if auto_zone:
            zone_a, zone_b = _auto_zone(new_path, old_path, dpi)
            if not zone_a:
                return {"status": pixdiff.SKIPPED, "reason": "scale_mismatch",
                        "message": "เนื้อหาขนาดจริงไม่เท่ากัน หรือหากรอบเนื้อหาไม่ได้",
                        "regions": [], "diff_px": 0, "region_count": 0}
        res = pixdiff.compare_zone(new_path, zone_a, old_path, zone_b, dpi=dpi)
        res["elapsed_s"] = round(time.time() - t0, 2)
        if res["status"] != pixdiff.OK:
            print("  ⚠ ไม่เทียบ — %s" % (res.get("message") or res["reason"]))
            return res
        print("  จับคู่ตำแหน่งได้ (คะแนน %.3f) · เนื้อหาขยับ %s mm · โซน %s mm"
              % (res.get("match_score", 0), res.get("shift_mm"),
                 res.get("zone_size_mm")))
        print("  ผล: %s  (%.2f วิ)" % (_fmt(res), res["elapsed_s"]))
        for i, r in enumerate(res["regions"][:10 if verbose else 5], 1):
            x, y, w, h = r["px"]
            print("   %2d) %5d px  ที่ (%d,%d) ขนาด %dx%d"
                  % (i, r["area_px"], x, y, w, h))
        if res["region_count"] == 0:
            print("  ✅ ไม่พบความต่างในโซนนี้")
        return res

    res = pixdiff.compare_files(new_path, old_path, dpi=dpi)
    res["elapsed_s"] = round(time.time() - t0, 2)

    tag = os.path.basename(new_path)
    print("\n" + "-" * 72)
    print("ใหม่: %s" % tag)
    print("เก่า: %s" % os.path.basename(old_path))
    if res["status"] != pixdiff.OK:
        print("  ⚠ ไม่เทียบ — %s" % res.get("message") or res["reason"])
        return res

    print("  ผล: %s  (%.2f วิ)" % (_fmt(res), res["elapsed_s"]))
    if res["region_count"] == 0:
        print("  ✅ ไม่พบความต่างระดับพิกเซลเลย")
    else:
        top = res["regions"][:10 if verbose else 5]
        for i, r in enumerate(top, 1):
            x, y, w, h = r["px"]
            print("   %2d) %5d px  ที่ (%d,%d) ขนาด %dx%d"
                  % (i, r["area_px"], x, y, w, h))
        if res["region_count"] > len(top):
            print("   … อีก %d บริเวณ" % (res["region_count"] - len(top)))

    if save_dir and res["regions"]:
        os.makedirs(save_dir, exist_ok=True)
        img = pixdiff._render(new_path, dpi)
        out = pixdiff.draw_regions(img, res["regions"])
        name = os.path.splitext(tag)[0][:60] + "_pixdiff.png"
        path = os.path.join(save_dir, name)
        cv2.imwrite(path, out)
        print("  💾 ภาพผล: %s" % path)
    return res


def main() -> int:
    ap = argparse.ArgumentParser(
        description="วัดชั้นเทียบพิกเซล artwork ฉบับเก่า/ใหม่ (advisory)")
    ap.add_argument("--selftest", action="store_true",
                    help="ทดสอบด้วย PDF ที่สร้างเอง (ไม่ต้องมีไฟล์ของคุณ)")
    ap.add_argument("--pair", nargs=2, metavar=("NEW", "OLD"),
                    help="เทียบสองไฟล์: ฉบับใหม่ กับ ฉบับเก่า")
    ap.add_argument("--new-dir", default="", help="โฟลเดอร์ฉบับใหม่")
    ap.add_argument("--old-dir", default="", help="โฟลเดอร์ฉบับเก่า (ชื่อไฟล์ตรงกัน)")
    ap.add_argument("--dpi", type=int, default=pixdiff.PIXDIFF_DPI)
    ap.add_argument("--save-dir", default="",
                    help="เขียนภาพผล (กรอบส้ม) ลงโฟลเดอร์นี้")
    ap.add_argument("--auto-zone", action="store_true",
                    help="เทียบเฉพาะ 'กรอบที่มีหมึก' ของแต่ละไฟล์ แทนทั้งหน้า "
                         "— ใช้เมื่อหน้าสองไฟล์คนละขนาด (ไม่ต้องรู้พิกัดโซน)")
    ap.add_argument("--zone-a", default="",
                    help="โซนของไฟล์ใหม่ เป็นสัดส่วน x,y,w,h เช่น 0.05,0.1,0.4,0.3")
    ap.add_argument("--zone-b", default="",
                    help="โซนของไฟล์อ้างอิง เป็นสัดส่วน x,y,w,h")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not (args.selftest or args.pair or (args.new_dir and args.old_dir)):
        ap.print_help()
        return 2

    print("=" * 72)
    print("verify_pixdiff — เทียบ artwork ฉบับเก่า/ใหม่ ระดับพิกเซล")
    print("=" * 72)
    print("DPI %d · เกณฑ์ต่าง %d/255 · บริเวณเล็กกว่า %d px ไม่รายงาน · "
          "ขนาดหน้าต่างได้ไม่เกิน %.1f mm"
          % (args.dpi, pixdiff.DIFF_THRESHOLD, pixdiff.MIN_REGION_PX,
             pixdiff.PAGE_SIZE_TOL_MM))

    rc = 0
    if args.selftest:
        rc |= selftest(args.dpi)

    pairs = []
    if args.pair:
        # เช็คก่อนเริ่ม — ชื่อไฟล์ผิดคือสาเหตุที่พบบ่อยที่สุด และถ้าปล่อยให้
        # ไปโผล่เป็น "เรนเดอร์ไม่สำเร็จ" ผู้ใช้จะไปไล่ปัญหาที่ตัว PDF แทน
        missing = [p for p in args.pair if not os.path.isfile(p)]
        if missing:
            print("\n✗ ไม่พบไฟล์:")
            for p in missing:
                print("    %s" % p)
            print("\n  • ใส่ path ให้ครบ เช่น \"D:\\Digital 2026\\...\\ฉบับใหม่.pdf\"")
            print("  • ชื่อไฟล์ที่มีช่องว่างต้องอยู่ในเครื่องหมายคำพูด")
            print("  • ดูชื่อไฟล์จริงในโฟลเดอร์: dir *.pdf")
            return 2
        pairs.append((args.pair[0], args.pair[1]))
    if args.new_dir and args.old_dir:
        for name in sorted(os.listdir(args.new_dir)):
            a = os.path.join(args.new_dir, name)
            b = os.path.join(args.old_dir, name)
            if os.path.isfile(a) and os.path.isfile(b):
                pairs.append((a, b))
        if not pairs:
            print("\nไม่พบไฟล์ชื่อตรงกันในสองโฟลเดอร์")
            return 2

    def _parse_zone(txt, name):
        if not txt:
            return None
        parts = [p.strip() for p in txt.replace(" ", "").split(",")]
        if len(parts) != 4:
            print("\n✗ %s ต้องเป็น x,y,w,h (สัดส่วน 0..1) เช่น 0.05,0.1,0.4,0.3"
                  % name)
            return "bad"
        try:
            return [float(v) for v in parts]
        except ValueError:
            print("\n✗ %s มีค่าที่ไม่ใช่ตัวเลข: %s" % (name, txt))
            return "bad"

    za = _parse_zone(args.zone_a, "--zone-a")
    zb = _parse_zone(args.zone_b, "--zone-b")
    if za == "bad" or zb == "bad":
        return 2
    if bool(za) != bool(zb):
        print("\n✗ ต้องระบุทั้ง --zone-a และ --zone-b คู่กัน "
              "(หรือใช้ --auto-zone ให้ระบบหากรอบเนื้อหาเอง)")
        return 2

    if pairs:
        print("\n%d คู่ที่จะเทียบ%s" % (
            len(pairs),
            " (โหมดโซน: กรอบเนื้อหาอัตโนมัติ)" if args.auto_zone else
            " (โหมดโซน: พิกัดที่ระบุ)" if za else ""))
        n_diff = n_skip = 0
        for a, b in pairs:
            res = compare_one(a, b, args.dpi, args.save_dir, args.verbose,
                              zone_a=za, zone_b=zb, auto_zone=args.auto_zone)
            if res["status"] != pixdiff.OK:
                n_skip += 1
            elif res["region_count"]:
                n_diff += 1
        print("\n" + "=" * 72)
        print("สรุป: เทียบได้ %d คู่ · พบความต่าง %d · เทียบไม่ได้ %d"
              % (len(pairs) - n_skip, n_diff, n_skip))
        print("=" * 72)
        if n_diff:
            print("\n⚠️ ชั้นนี้เป็น advisory — 'พบความต่าง' ไม่ใช่ 'ผิด'")
            print("   ฉบับใหม่ที่แก้ไขตามแผนก็จะขึ้นความต่างเป็นปกติ")
            print("   หน้าที่ของมันคือ **ไม่ให้ความต่างหลุดสายตา** ไม่ใช่ตัดสินถูกผิด")
            rc |= 1
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
