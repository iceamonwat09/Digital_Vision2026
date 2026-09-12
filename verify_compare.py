#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ตาข่ายนิรภัยของ **ชั้นเทียบ** — โหมด pixel (panelmatch) และโหมด confirm.

ทำไมต้องมีไฟล์นี้
------------------
repo นี้มีตาข่ายนิรภัยครบทุกชั้นแล้ว — ``verify_onnx.py`` (โมเดล) ·
``verify_openvino.py`` (backend) · ``verify_ocr.py`` (การอ่าน) ·
``pixdiff_noise_scan.py`` (noise ของพิกเซล) · ``blur_tolerance.py`` (ความเบลอ)
— **ยกเว้นชั้นเทียบ** ซึ่งเป็นชั้นที่ตัดสิน defect จริง ๆ. ผลคือทุกครั้งที่
แก้ชั้นนี้ เราตัดสินจาก "รันคู่ไฟล์เดียวแล้วดูจำนวน defect" ซึ่ง:

* **แยก false positive ออกจาก false negative ไม่ได้** (0 defect อาจแปลว่า
  "ไม่มีอะไรต่าง" หรือ "มองไม่เห็นของที่ต่าง" ก็ได้)
* **ไม่มีเฉลย** จึงเถียงกันไม่จบว่าเลขที่ได้ดีหรือแย่
* **ไม่รู้ว่าอะไรเป็นตัวแปร** — บนสถานีคู่ไฟล์เดิมให้ 0 defect รอบหนึ่ง
  และ 11 defect อีกรอบ ต่างกันแค่ "มือที่ลากโซน"

ไฟล์นี้สร้าง **เฉลย** ขึ้นมาเอง 3 ทาง แล้ววัดเป็นตัวเลขที่เอาไปพัฒนาต่อได้:

  ① SELF   ไฟล์เดียวกันเทียบตัวเอง ⇒ ทุกบริเวณที่เจอ = **ปลอม 100%**
           ไล่ "มือที่ลากโซน" (หลวม/เยื้อง 0-8 mm) โดยที่เนื้อหาเหมือนกันเป๊ะ
           ⇒ แยก "ฟ้องผิดเพราะการลาก" ออกจาก "ต่างกันจริง" ได้เด็ดขาด
  ② SEED   วาดจุดขนาดที่รู้ค่า (mm) ลงบนสำเนา แล้วเทียบกับต้นฉบับ
           ⇒ ได้ **เส้นโค้งความไว** (เล็กสุดกี่ mm² ที่ยังเห็น) + **ความคลาด
           ของตำแหน่งที่ชี้** (mm) ซึ่งเป็นสองตัวเลขที่ตัดสินว่าโหมดนี้ใช้ได้ไหม
  ③ PAIR   คู่ไฟล์จริง — ไล่การลากแบบเดียวกับ ① แล้ววัด **ความเสถียร**
           (บริเวณที่เจอตอนลากกระชับ ยังเจอไหมเมื่อลากหลวม · มีของแถมกี่อัน)
  ④ CONFIRM  โหมดอ่านซ้ำ บนผลจริง 4 รอบที่บันทึกไว้ — วัด recall/FP ทุกคู่
           และ **แยก bias ที่เสถียร** (สิ่งที่ intersection ลบไม่ได้เชิงหลักการ)

**อ่านอย่างเดียว ไม่เขียนอะไรลง ``data/`` ไม่แตะ report/cache/verdict.**

วิธีใช้
-------
    py -3.9 verify_compare.py --selftest
    py -3.9 verify_compare.py --pair NEW.pdf OLD.pdf \
            --zone-a 0.5929,0.5020,0.1727,0.2385 \
            --zone-b 0.5000,0.4560,0.2100,0.2960
    py -3.9 verify_compare.py --layers confirm

exit code: 0=ผ่าน · 1=ไม่ผ่าน · 2=รันไม่ได้ · 3=สรุปไม่ได้ (ไม่มีเฉลย)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import time
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import fitz                                     # PyMuPDF
except Exception:                                   # pragma: no cover
    fitz = None

from artwork_check import panelmatch, pixdiff       # noqa: E402

# ── เกณฑ์รับ (เขียนไว้ "ก่อน" วัด — ห้ามขยับตามผลที่ได้) ─────────────
MAX_SELF_REGIONS = 0        # ไฟล์เดียวกันเทียบตัวเอง ต้องไม่เจออะไรเลย
SEED_TARGET_MM2 = 0.30      # ต้องเห็นจุดที่เล็กถึงขนาดนี้ (= จุดทศนิยม/®)
MAX_LOCATE_ERR_MM = 1.5     # ชี้ตำแหน่งคลาดได้ไม่เกินนี้
MAX_EXTRA_ON_JITTER = 0     # ลากหลวมแล้วต้องไม่มีบริเวณแถม
MATCH_RADIUS_MM = 3.0       # ระยะที่ถือว่าเป็น "บริเวณเดียวกัน" ข้ามการลาก

_SEED_SIDES_MM = (0.2, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0)
_DRAGS = (                  # (ชื่อ, ขยาย mm ฝั่ง a, ขยาย mm ฝั่ง b, เยื้อง mm ฝั่ง a)
    ("ตรงเป๊ะ", 0.0, 0.0, 0.0),
    ("a หลวม 1mm", 1.0, 0.0, 0.0),
    ("a หลวม 3mm", 3.0, 0.0, 0.0),
    ("a หลวม 6mm", 6.0, 0.0, 0.0),
    ("b หลวม 3mm", 0.0, 3.0, 0.0),
    ("b หลวม 6mm", 0.0, 6.0, 0.0),
    ("หลวมทั้งคู่ 3mm", 3.0, 3.0, 0.0),
    ("หลวมทั้งคู่ 6mm", 6.0, 6.0, 0.0),
    ("a เยื้อง 2mm", 0.0, 0.0, 2.0),
    ("a เยื้อง 4mm", 0.0, 0.0, 4.0),
)


# ── ตัวช่วย ──────────────────────────────────────────────────────────
def _bbox_mm(path: str, bbox, page_index: int = 0) -> Tuple[float, float]:
    """ขนาดของ bbox เป็นมิลลิเมตร."""
    size = pixdiff.page_size_mm(path, page_index)
    if not size:
        return (0.0, 0.0)
    return (bbox[2] * size[0], bbox[3] * size[1])


def adjust(path: str, bbox, grow_mm: float = 0.0, shift_mm: float = 0.0,
           page_index: int = 0):
    """ขยาย/เยื้อง bbox เป็นหน่วยมิลลิเมตร (จำลอง "มือที่ลากโซน")."""
    size = pixdiff.page_size_mm(path, page_index)
    if not size:
        return list(bbox)
    gx, gy = grow_mm / size[0], grow_mm / size[1]
    sx, sy = shift_mm / size[0], shift_mm / size[1]
    x, y, w, h = [float(v) for v in bbox]
    return [max(0.0, x - gx + sx), max(0.0, y - gy + sy),
            min(1.0, w + 2 * gx), min(1.0, h + 2 * gy)]


def seed_pdf(src: str, out: str, bbox, u: float, v: float, side_mm: float,
             page_index: int = 0) -> Tuple[float, float]:
    """วาดสี่เหลี่ยมทึบขนาด ``side_mm`` ที่ตำแหน่ง (u,v) ภายในโซน.

    คืนจุดกึ่งกลางที่ **คาดว่าจะได้** เป็นมิลลิเมตรจากมุมซ้ายบนของโซน —
    เอาไปเทียบกับตำแหน่งที่ระบบรายงาน ⇒ วัดความคลาดของการชี้จุดได้
    """
    doc = fitz.open(src)
    page = doc[page_index]
    r = page.rect
    s_pt = side_mm / 25.4 * 72.0
    x0 = r.x0 + (bbox[0] + u * bbox[2]) * r.width
    y0 = r.y0 + (bbox[1] + v * bbox[3]) * r.height
    page.draw_rect(fitz.Rect(x0, y0, x0 + s_pt, y0 + s_pt),
                   color=(0, 0, 0), fill=(0, 0, 0), width=0)
    doc.save(out)
    doc.close()
    # จุดกึ่งกลางเทียบกับมุมซ้ายบนของโซน (หน่วย mm)
    off_x_pt = u * bbox[2] * r.width + s_pt / 2.0
    off_y_pt = v * bbox[3] * r.height + s_pt / 2.0
    return (off_x_pt * 25.4 / 72.0, off_y_pt * 25.4 / 72.0)


def _zone_origin_mm(path: str, bbox, page_index: int = 0):
    """มุมซ้ายบนของโซนเป็นมิลลิเมตรจากมุมหน้ากระดาษ."""
    size = pixdiff.page_size_mm(path, page_index)
    if not size:
        return (0.0, 0.0)
    return (bbox[0] * size[0], bbox[1] * size[1])


def probe(pa: str, ba, pb: str, bb, dpi: int) -> Dict[str, object]:
    """รันการเทียบหนึ่งครั้ง → ตัวเลขวินิจฉัยทั้งหมดที่มี."""
    t0 = time.time()
    res = panelmatch.compare(pa, ba, pb, bb, dpi=dpi)
    secs = time.time() - t0
    mmpp = float(res.get("mm_per_px") or (25.4 / dpi))
    regs = res.get("regions") or []
    size = res.get("size") or [1, 1]
    out = {
        "status": res.get("status"),
        "reason": res.get("reason", ""),
        "ncc": res.get("ncc"),
        "ecc": res.get("ecc"),
        "scale": res.get("scale"),
        "diff_ratio": res.get("diff_ratio"),
        "n": len(regs),
        "edge": int(res.get("edge_regions") or 0),
        "size": size,
        "mm_per_px": mmpp,
        "secs": round(secs, 2),
        "areas_mm2": [round(r["area_px"] * mmpp * mmpp, 3) for r in regs],
        "areas_px": [int(r["area_px"]) for r in regs],
        "centers_mm": [panelmatch.region_center_mm(r, size, mmpp) for r in regs],
    }
    # ⚠️ ตำแหน่งต้องเทียบใน **พิกัดหน้ากระดาษ** — โซนที่ลากหลวมทำให้จุด
    #    กำเนิดของโซนขยับ ถ้าไม่ชดเชยจะสรุปผิดว่า "ของจริงหายไป"
    ox, oy = _zone_origin_mm(pa, ba)
    out["centers_page_mm"] = [(round(c[0] + ox, 2), round(c[1] + oy, 2))
                              for c in out["centers_mm"]]
    return out


def _pct(v) -> str:
    return "—" if v is None else "%.4f%%" % (float(v) * 100.0)


def _short(xs, n=4):
    xs = list(xs)
    s = ", ".join(("%g" % x) if isinstance(x, (int, float)) else repr(x)
                  for x in xs[:n])
    return s + (", …" if len(xs) > n else "")


def _hr(title: str):
    print("\n" + "─" * 78)
    print(title)
    print("─" * 78)


# ── ชั้น ① SELF — ไฟล์เดียวกันเทียบตัวเอง (ทุกบริเวณ = ปลอม) ────────
def layer_self(path: str, bbox, dpi: int) -> Dict[str, object]:
    _hr("① SELF — ไฟล์เดียวกันเทียบตัวเอง  (เฉลย: ต้องได้ 0 บริเวณเสมอ)")
    print("   ทุกบริเวณที่เจอในชั้นนี้คือ **ฟ้องผิด 100%** เพราะเนื้อหาเหมือนกันเป๊ะ")
    print("   ตัวแปรเดียวที่เปลี่ยนคือ 'มือที่ลากโซน'\n")
    print("   %-18s %-9s %6s %6s %8s %10s %5s %6s  %s"
          % ("การลากโซน", "สถานะ", "NCC", "ECC", "สเกล", "ต่าง%", "พบ",
             "ติดขอบ", "พื้นที่ (mm²)"))
    rows = []
    worst = 0
    for name, ga, gb, sh in _DRAGS:
        b_a = adjust(path, bbox, ga, sh)
        b_b = adjust(path, bbox, gb, 0.0)
        r = probe(path, b_a, path, b_b, dpi)
        rows.append(dict(r, case=name))
        bad = r["n"] > MAX_SELF_REGIONS
        worst = max(worst, r["n"])
        print("   %-18s %-9s %6s %6s %8s %10s %5d %6d  %s%s"
              % (name, r["status"], r["ncc"], r["ecc"], r["scale"],
                 _pct(r["diff_ratio"]), r["n"], r["edge"],
                 _short(r["areas_mm2"]),
                 "   <-- ฟ้องผิด" if bad else ""))
    ok = worst <= MAX_SELF_REGIONS
    print("\n   ⇒ ฟ้องผิดสูงสุด %d บริเวณ (เกณฑ์ %d)  %s"
          % (worst, MAX_SELF_REGIONS, "✅" if ok else "❌"))
    return {"ok": ok, "worst": worst, "rows": rows}


# ── ชั้น ② SEED — จุดขนาดที่รู้ค่า (เฉลยเป๊ะ) ────────────────────────
def layer_seed(path: str, bbox, dpi: int, keep: Optional[str] = None):
    _hr("② SEED — วาดจุดขนาดที่รู้ค่าลงบนสำเนา  (เฉลย: 1 บริเวณ ตำแหน่งที่รู้)")
    print("   ตอบสองคำถามที่ตัดสินว่าโหมดนี้ใช้ได้ไหม:")
    print("   • เห็นความต่างที่เล็กที่สุดกี่ mm²  • ชี้ตำแหน่งคลาดกี่ mm\n")
    print("   %-8s %-9s %6s %5s %10s %5s %11s %11s"
          % ("จุด(mm)", "สถานะ", "NCC", "พบ", "ต่าง%", "เห็น", "พื้นที่ mm²", "คลาด mm"))
    tmp = keep or tempfile.mkdtemp(prefix="vc_seed_")
    rows = []
    smallest = None
    worst_err = 0.0
    for side in _SEED_SIDES_MM:
        out = os.path.join(tmp, "seed_%.1f.pdf" % side)
        ex, ey = seed_pdf(path, out, bbox, 0.42, 0.55, side)
        r = probe(path, bbox, out, bbox, dpi)
        seen = r["n"] >= 1
        err = None
        if seen:
            # บริเวณที่ใกล้จุดที่วาดที่สุด
            d = [math.hypot(c[0] - ex, c[1] - ey) for c in r["centers_mm"]]
            i = int(min(range(len(d)), key=lambda k: d[k]))
            err = round(d[i], 2)
            seen = err <= MAX_LOCATE_ERR_MM
            if seen:
                worst_err = max(worst_err, err)
                if smallest is None:
                    smallest = side
        rows.append(dict(r, side_mm=side, expect_mm=[round(ex, 2), round(ey, 2)],
                         seen=bool(seen), err_mm=err))
        print("   %-8s %-9s %6s %5d %10s %5s %11s %11s"
              % ("%.1f" % side, r["status"], r["ncc"], r["n"],
                 _pct(r["diff_ratio"]), "✅" if seen else "❌",
                 _short(r["areas_mm2"], 2), "—" if err is None else "%.2f" % err))
        try:
            os.unlink(out)
        except OSError:
            pass
    if keep is None:
        try:
            os.rmdir(tmp)
        except OSError:
            pass
    ok = smallest is not None and smallest <= SEED_TARGET_MM2 ** 0.5 + 1e-9
    # หมายเหตุ: SEED_TARGET_MM2 เป็นพื้นที่ ⇒ ด้าน = sqrt
    print("\n   ⇒ เล็กสุดที่เห็น: %s  (เกณฑ์ ≤ %.2f mm ด้าน = %.2f mm²)  %s"
          % ("ไม่เห็นเลย" if smallest is None else "%.1f mm (%.2f mm²)"
             % (smallest, smallest ** 2),
             SEED_TARGET_MM2 ** 0.5, SEED_TARGET_MM2, "✅" if ok else "❌"))
    print("   ⇒ ความคลาดของตำแหน่งสูงสุด %.2f mm (เกณฑ์ %.1f)"
          % (worst_err, MAX_LOCATE_ERR_MM))
    return {"ok": ok, "smallest_mm": smallest, "worst_err_mm": worst_err,
            "rows": rows}


# ── ชั้น ③ PAIR — คู่ไฟล์จริง: ความต่างจริงต้องไม่ขึ้นกับวิธีลาก ────
def layer_pair(pa: str, ba, pb: str, bb, dpi: int):
    _hr("③ PAIR — คู่ไฟล์จริง  (เฉลย: ผลต้องไม่เปลี่ยนตามวิธีลากโซน)")
    ref = probe(pa, ba, pb, bb, dpi)
    print("   ฐาน (ลากกระชับ): %s · NCC %s · สเกล %s · ต่าง %s · %d บริเวณ %s"
          % (ref["status"], ref["ncc"], ref["scale"], _pct(ref["diff_ratio"]),
             ref["n"], _short(ref["areas_mm2"])))
    for c, a in zip(ref["centers_page_mm"], ref["areas_mm2"]):
        print("      • %.1f, %.1f mm   %.3f mm²" % (c[0], c[1], a))
    print()
    print("   %-18s %-9s %6s %6s %10s %5s %6s %6s %6s  %s"
          % ("การลากโซน", "สถานะ", "NCC", "ECC", "ต่าง%", "พบ", "ติดขอบ",
             "ตรงฐาน", "แถม", "พื้นที่ (mm²)"))
    rows = []
    worst_extra = 0
    lost = 0
    for name, ga, gb, sh in _DRAGS:
        b_a = adjust(pa, ba, ga, sh)
        b_b = adjust(pb, bb, gb, 0.0)
        r = probe(pa, b_a, pb, b_b, dpi)
        matched = 0
        for rc in ref["centers_page_mm"]:
            if any(math.hypot(c[0] - rc[0], c[1] - rc[1]) <= MATCH_RADIUS_MM
                   for c in r["centers_page_mm"]):
                matched += 1
        extra = max(0, r["n"] - matched)
        worst_extra = max(worst_extra, extra)
        lost = max(lost, len(ref["centers_page_mm"]) - matched)
        rows.append(dict(r, case=name, matched=matched, extra=extra))
        print("   %-18s %-9s %6s %6s %10s %5d %6d %6s %6d  %s"
              % (name, r["status"], r["ncc"], r["ecc"], _pct(r["diff_ratio"]),
                 r["n"], r["edge"],
                 "%d/%d" % (matched, len(ref["centers_page_mm"])), extra,
                 _short(r["areas_mm2"], 3)))
    ok = worst_extra <= MAX_EXTRA_ON_JITTER and lost == 0
    print("\n   ⇒ บริเวณแถมสูงสุด %d (เกณฑ์ %d) · ของจริงที่หายไปสูงสุด %d  %s"
          % (worst_extra, MAX_EXTRA_ON_JITTER, lost, "✅" if ok else "❌"))
    return {"ok": ok, "ref": ref, "worst_extra": worst_extra, "lost": lost,
            "rows": rows}


# ── ชั้น ④ CONFIRM — โหมดอ่านซ้ำ บนผลจริง 4 รอบ ─────────────────────
def layer_confirm(data_dir: str, real: str = "24%"):
    from artwork_check import checks, confirm as confirm_mod
    _hr("④ CONFIRM — โหมดอ่านซ้ำ บนผลจริง 4 รอบของสถานี  (เฉลย: %r ตัวเดียว)"
        % real)
    zones = [{"id": "z1", "type": "panel", "group": "A", "label": "z1"},
             {"id": "b2", "type": "panel", "group": "A", "label": "b2"}]

    def rd(n):
        with open(os.path.join(data_dir, n), encoding="utf-8") as f:
            return f.read()

    rounds = {}
    for i in (1, 2, 3, 4):
        try:
            rounds[i] = checks.check_group_consistency(
                zones, {"z1": rd("r%d_z1.txt" % i), "b2": rd("r%d_b2.txt" % i)})
        except OSError:
            print("   ⚠️ ไม่พบข้อมูลใน %s" % data_dir)
            return {"ok": None, "rows": []}

    print("   รอบเดี่ยว (ไม่ใช้โหมด):")
    for i, ds in sorted(rounds.items()):
        fp = [d for d in ds if confirm_mod.defect_key(d)[2] != real]
        print("      รอบ %d: %d รายการ  (จริง %d · ปลอม %d)"
              % (i, len(ds), len(ds) - len(fp), len(fp)))

    print("\n   %-8s %6s %6s %6s %6s  %s"
          % ("คู่รอบ", "ยืนยัน", "ตกไป", "recall", "FP", "ที่ยืนยันแล้ว"))
    rows = []
    worst_fp = 0
    miss = 0
    for i in (1, 2, 3, 4):
        for j in (1, 2, 3, 4):
            if j <= i:
                continue
            c, u = confirm_mod.confirm([rounds[i], rounds[j]])
            founds = [confirm_mod.defect_key(d)[2] for d in c]
            fp = [f for f in founds if f != real]
            hit = real in founds
            worst_fp = max(worst_fp, len(fp))
            miss += 0 if hit else 1
            rows.append({"pair": [i, j], "confirmed": len(c),
                         "unconfirmed": len(u), "hit": hit, "fp": len(fp),
                         "founds": founds})
            print("   %-8s %6d %6d %6s %6d  %s"
                  % ("%d+%d" % (i, j), len(c), len(u), "✅" if hit else "❌",
                     len(fp), _short(founds, 3)))

    # bias ที่เสถียร: โผล่ทุกรอบแต่ไม่ใช่ของจริง ⇒ intersection ลบไม่ได้
    keys = [set(confirm_mod.defect_key(d) for d in ds) for ds in rounds.values()]
    stable = set.intersection(*keys)
    bias = sorted(k[2] for k in stable if k[2] != real)
    print("\n   bias ที่เสถียร (โผล่ครบทุกรอบ แต่ไม่ใช่ของจริง): %s"
          % (", ".join(repr(b) for b in bias) if bias else "ไม่มี"))
    if bias:
        print("      ⇒ intersection **ลบไม่ได้เชิงหลักการ** — ต้องแก้ที่ prompt/ชั้นอื่น")
    ok = miss == 0
    print("\n   ⇒ recall %d/%d คู่ · FP สูงสุด %d/คู่  %s"
          % (len(rows) - miss, len(rows), worst_fp, "✅" if ok else "❌"))
    return {"ok": ok, "rows": rows, "stable_bias": bias, "worst_fp": worst_fp}


# ── selftest: สร้าง PDF เอง (ใช้บนสถานีได้โดยไม่ต้องมีไฟล์จริง) ─────
def _make_pdf(path: str, page_mm: Tuple[float, float], scale: float,
              tint: int = 0):
    """หน้าที่มี "แผงโภชนาการ" จำลอง — ``scale`` = ขนาดที่พิมพ์จริง."""
    W, H = [v / 25.4 * 72.0 for v in page_mm]
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    if tint:
        page.draw_rect(page.rect, color=None,
                       fill=(1.0, 1.0 - tint / 255.0, 1.0 - tint / 255.0),
                       width=0)
    x0, y0 = W * 0.30, H * 0.25
    fs = 7.0 * scale
    rows = [("Total fat", "7 g", "10%"), ("Saturated fat", "2 g", "10%"),
            ("Cholesterol", "35 mg", "12%"), ("Sodium", "475 mg", "20%"),
            ("Total carbohydrate", "0 g", "0%"), ("Protein", "26 g", "52%")]
    for i, (a, b, c) in enumerate(rows):
        y = y0 + i * fs * 1.9
        page.insert_text((x0, y), a, fontsize=fs, fontname="helv")
        page.insert_text((x0 + 90 * scale, y), b, fontsize=fs, fontname="helv")
        page.insert_text((x0 + 130 * scale, y), c, fontsize=fs, fontname="helv")
    doc.save(path)
    doc.close()


def run_selftest(dpi: int) -> int:
    tmp = tempfile.mkdtemp(prefix="vc_self_")
    a = os.path.join(tmp, "a.pdf")
    b = os.path.join(tmp, "b.pdf")
    _make_pdf(a, (210.0, 297.0), 1.00)
    _make_pdf(b, (300.0, 400.0), 1.28, tint=18)     # คนละขนาด + คนละโทนสี
    bbox_a = [0.26, 0.20, 0.46, 0.22]
    bbox_b = [0.20, 0.17, 0.44, 0.21]
    print("selftest: สร้าง PDF สองฉบับ (คนละขนาดหน้า · คนละสเกล 1.28 · คนละโทนสี)")
    r1 = layer_self(a, bbox_a, dpi)
    r2 = layer_seed(a, bbox_a, dpi)
    r3 = layer_pair(a, bbox_a, b, bbox_b, dpi)
    ok = bool(r1["ok"]) and bool(r2["ok"])
    _hr("สรุป selftest")
    print("   ① SELF  %s   ② SEED  %s   ③ PAIR(สังเคราะห์)  %s"
          % ("ผ่าน" if r1["ok"] else "ไม่ผ่าน",
             "ผ่าน" if r2["ok"] else "ไม่ผ่าน",
             "ผ่าน" if r3["ok"] else "ไม่ผ่าน"))
    print("   ⚠️ ไฟล์สังเคราะห์ **ไม่แทนไฟล์จริง** — repo นี้เคยสรุปผิดมาแล้ว")
    print("      (tol=1 ให้ 0 บริเวณบนไฟล์สังเคราะห์ แต่ 356 บนไฟล์จริง)")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pair", nargs=2, metavar=("NEW", "OLD"))
    ap.add_argument("--zone-a", help="x,y,w,h เป็นสัดส่วน 0..1")
    ap.add_argument("--zone-b", help="x,y,w,h เป็นสัดส่วน 0..1")
    ap.add_argument("--dpi", type=int, default=panelmatch.DPI)
    ap.add_argument("--layers", default="self,seed,pair,confirm")
    ap.add_argument("--confirm-data",
                    default=os.path.join(os.path.dirname(
                        os.path.abspath(__file__)),
                        "tests", "data", "artwork_confirm"))
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json", help="เขียนผลดิบเป็น JSON")
    args = ap.parse_args(argv)

    if fitz is None:
        print("❌ ต้องมี PyMuPDF (fitz)")
        return 2

    print("=" * 78)
    print("verify_compare — ตาข่ายนิรภัยของชั้นเทียบ   (DPI %d · %.4f mm/px)"
          % (args.dpi, 25.4 / args.dpi))
    print("=" * 78)

    if args.selftest:
        return run_selftest(args.dpi)

    layers = [s.strip() for s in args.layers.split(",") if s.strip()]
    out: Dict[str, object] = {"dpi": args.dpi}
    codes = []

    if args.pair:
        pa, pb = args.pair
        for p in (pa, pb):
            if not os.path.exists(p):
                print("❌ ไม่พบไฟล์: %s" % p)
                return 2
        if not (args.zone_a and args.zone_b):
            print("❌ ต้องระบุ --zone-a และ --zone-b")
            return 2
        ba = [float(v) for v in args.zone_a.split(",")]
        bb = [float(v) for v in args.zone_b.split(",")]
        wa, ha = _bbox_mm(pa, ba)
        wb, hb = _bbox_mm(pb, bb)
        print("ไฟล์ a: %s\n        โซน %.1f x %.1f mm" % (os.path.basename(pa), wa, ha))
        print("ไฟล์ b: %s\n        โซน %.1f x %.1f mm  (สเกลที่คาด %.3f)"
              % (os.path.basename(pb), wb, hb, (wa / wb) if wb else 0.0))
        if "self" in layers:
            out["self"] = layer_self(pa, ba, args.dpi)
            codes.append(out["self"]["ok"])
        if "seed" in layers:
            out["seed"] = layer_seed(pa, ba, args.dpi)
            codes.append(out["seed"]["ok"])
        if "pair" in layers:
            out["pair"] = layer_pair(pa, ba, pb, bb, args.dpi)
            codes.append(out["pair"]["ok"])

    if "confirm" in layers:
        out["confirm"] = layer_confirm(args.confirm_data)
        if out["confirm"]["ok"] is not None:
            codes.append(out["confirm"]["ok"])

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print("\nเขียน JSON: %s" % args.json)

    _hr("สรุป")
    if not codes:
        print("   สรุปไม่ได้ — ไม่มีชั้นไหนรันเลย")
        return 3
    for k in ("self", "seed", "pair", "confirm"):
        if k in out:
            v = out[k].get("ok")
            print("   %-8s %s" % (k, "✅ ผ่าน" if v else
                                  ("— สรุปไม่ได้" if v is None else "❌ ไม่ผ่าน")))
    return 0 if all(codes) else 1


if __name__ == "__main__":
    sys.exit(main())
