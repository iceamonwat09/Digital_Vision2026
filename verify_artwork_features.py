#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
verify_artwork_features.py — ตรวจว่างานรอบ "coverage + คุณภาพ OCR + N8N + UX"
**ติดตั้งอยู่บนเครื่องนี้จริงและทำงานถูกต้อง**

ต่างจาก `pytest` ตรงที่ตัวนี้ตอบคำถามว่า *"เครื่องที่กำลังจะใช้งานจริงพร้อมไหม"* —
วัดจากโค้ดที่อยู่บนดิสก์เครื่องนี้ ค่าคอนฟิกที่ resolve ได้จริง แพ็กเกจที่ติดตั้งจริง
และเรียก pipeline ตัวจริงตั้งแต่ต้นจนจบ.

    py -3.9 verify_artwork_features.py                # ทุกชั้นที่ไม่ต้องต่อเน็ต
    py -3.9 verify_artwork_features.py --n8n          # + ยิง N8N จริง (ชั้น F)
    py -3.9 verify_artwork_features.py --only B,C     # เลือกเฉพาะบางชั้น
    py -3.9 verify_artwork_features.py --verbose      # โชว์ค่าที่วัดได้ทุกข้อ

ชั้นที่ตรวจ
  A  โค้ด/คอนฟิกที่รันอยู่จริง (CONFIG_VERSION, commit, flag ใหม่, แพ็กเกจ optional)
  B  ตรรกะ QC end-to-end — สร้าง PDF 3 แผงแล้วเรียก `pipeline.run_inspection` ตัวจริง
  C  การแกะคำตอบ N8N — ยิงผ่าน HTTP จริงไปยัง mock server ในเครื่อง (ไม่ออกเน็ต)
  D  คุณภาพการอ่าน — text layer ที่เสีย + การเพิ่ม DPI ให้โซนเล็ก
  E  ค่าคงที่/องค์ประกอบฝั่ง UI ที่ "พังเงียบ" ได้ (CSS 2 template, JS↔Python, id ใน template)
  F  ยิง N8N จริง (ต้องใส่ --n8n)

**อ่านอย่างเดียว**: ไม่เขียนอะไรลง `data/` เลย (ชั้น B ใช้โฟลเดอร์ชั่วคราวแล้วลบทิ้ง)
ไม่ต้องมีกล้อง / SQL Server / เบราว์เซอร์ และไม่แตะโหมด Live / RTSP / STREAM /
Snapshot / Label เลยแม้แต่บรรทัดเดียว.

exit code: 0 = ผ่านหมด · 1 = มีข้อไม่ผ่าน · 2 = รันไม่ได้ (import/สภาพแวดล้อม)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# commit ของรอบนี้ (7b5cf61 → e45fff0) — ใช้บอกว่าเครื่องนี้ checkout ครบไหม
ROUND_COMMITS = [
    ("7b5cf61", "OCR: เพิ่ม DPI ให้โซนเล็ก + ไม่เชื่อ text layer ที่เสีย"),
    ("810deef", "merge main (login/ลงทะเบียน + N8N → 127.0.0.1)"),
    ("21c1b8b", "_ocr_fingerprint เข้า cache key + diagnose รู้จักทางที่ 6"),
    ("e39a29e", "coverage — รายงานว่าชั้นไหนได้ตรวจจริง"),
    ("d460924", "N8N: ถอดรั้ว markdown · ปฏิเสธ HTML · retry"),
    ("20e3af1", "verify_ocr: เรนเดอร์ตรง production + --layers"),
    ("008ca38", "verify_ocr: ครบโควตาไม่ใช่ ERROR"),
    ("5d2f91a", "UX: verdict ตรง coverage · พอดีทั้งหน้า · วาดต่อเนื่อง · autosave"),
    ("a99d31f", "CSS ที่ขาดบนหน้าประวัติ + เทสต์กันลืม"),
    ("e45fff0", "กันโซนเดียวพังแล้วล้มการตรวจทั้งใบ"),
]

PASS, FAIL, SKIP, INFO = "ผ่าน", "ไม่ผ่าน", "ข้าม", "ข้อมูล"


class Suite:
    """เก็บผลแต่ละข้อ แล้วพิมพ์สรุปตอนท้าย."""

    def __init__(self, verbose: bool = False):
        self.rows = []
        self.verbose = verbose
        self._section = ""

    def section(self, title: str):
        self._section = title
        print("\n" + "=" * 72)
        print(title)
        print("=" * 72)

    def _add(self, status, name, got, fix):
        self.rows.append((self._section, status, name, got, fix))
        mark = {PASS: "  ✓", FAIL: "  ✗", SKIP: "  –", INFO: "  ·"}[status]
        line = "%s %s" % (mark, name)
        if got and (status != PASS or self.verbose):
            line += "\n        ได้: %s" % got
        if status == FAIL and fix:
            line += "\n        แก้: %s" % fix
        print(line)

    def ok(self, name, got=""):
        self._add(PASS, name, got, "")

    def bad(self, name, got="", fix=""):
        self._add(FAIL, name, got, fix)

    def check(self, cond, name, got="", fix=""):
        if cond:
            self.ok(name, got)
        else:
            self.bad(name, got, fix)
        return bool(cond)

    def skip(self, name, why=""):
        self._add(SKIP, name, why, "")

    def info(self, name, got=""):
        self._add(INFO, name, got, "")

    def summary(self) -> int:
        n_pass = sum(1 for r in self.rows if r[1] == PASS)
        n_fail = sum(1 for r in self.rows if r[1] == FAIL)
        n_skip = sum(1 for r in self.rows if r[1] == SKIP)
        print("\n" + "=" * 72)
        print("สรุป: ผ่าน %d · ไม่ผ่าน %d · ข้าม %d" % (n_pass, n_fail, n_skip))
        print("=" * 72)
        if n_fail:
            print("\nข้อที่ไม่ผ่าน:")
            for sec, st, name, got, fix in self.rows:
                if st == FAIL:
                    print("  ✗ [%s] %s" % (sec.split()[0], name))
                    if got:
                        print("      ได้: %s" % got)
                    if fix:
                        print("      แก้: %s" % fix)
        else:
            print("\n✅ ครบทุกข้อที่ตรวจได้บนเครื่องนี้")
        if n_skip:
            print("\nข้อที่ข้าม (ต้องมีเงื่อนไขเพิ่ม):")
            for sec, st, name, got, _ in self.rows:
                if st == SKIP:
                    print("  – %s%s" % (name, (" — " + got) if got else ""))
        return 1 if n_fail else 0


# ─────────────────────────────────────────────────────────────────────
# ชั้น A — โค้ด/คอนฟิกที่รันอยู่จริง
# ─────────────────────────────────────────────────────────────────────
def section_a(s: Suite):
    s.section("A. โค้ดและคอนฟิกที่กำลังรันอยู่จริงบนเครื่องนี้")

    import config as appcfg
    from artwork_check import config as awcfg

    s.info("CONFIG_VERSION (ต้องตรงกับ footer บนหน้าเว็บ)", appcfg.CONFIG_VERSION)
    s.info("Python", sys.version.split()[0])

    # ── commit ของรอบนี้อยู่ครบไหม ──
    def git(*args):
        try:
            return subprocess.check_output(["git"] + list(args), cwd=ROOT,
                                           stderr=subprocess.DEVNULL). \
                decode("utf-8", "replace").strip()
        except Exception:
            return None

    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch is None:
        s.skip("commit ของรอบนี้อยู่ครบ", "ไม่มี git หรือไม่ใช่ repo")
    else:
        s.info("branch · HEAD", "%s · %s" % (branch, git("rev-parse", "--short", "HEAD")))
        missing = []
        for sha, what in ROUND_COMMITS:
            r = subprocess.call(["git", "merge-base", "--is-ancestor", sha, "HEAD"],
                                cwd=ROOT, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
            if r != 0:
                missing.append("%s (%s)" % (sha, what))
        s.check(not missing, "commit ของรอบนี้ครบทั้ง %d ตัว" % len(ROUND_COMMITS),
                got=("ขาด " + "; ".join(missing)) if missing else "ครบ",
                fix="branch นี้ยังไม่มีงานครบ — `git fetch origin && git checkout "
                    "claude/artwork-mode-station-testing-llsrp0 && git pull` "
                    "แล้วรีสตาร์ต app.py (main ยังขาด 5 commit สุดท้าย)")

    # ── flag ใหม่ 4 ตัว (ปิดได้ทุกตัว = กลับพฤติกรรมเดิม) ──
    flags = [
        ("ARTWORK_OCR_CROP_MIN_SIDE", getattr(awcfg, "OCR_CROP_MIN_SIDE", None), 1200),
        ("ARTWORK_PDFTEXT_GARBLED_CHECK", getattr(awcfg, "PDFTEXT_GARBLED_CHECK", None), True),
        ("N8N_OCR_RETRIES", getattr(appcfg, "N8N_OCR_RETRIES", None), 1),
        ("N8N_OCR_STRICT_RESPONSE", getattr(appcfg, "N8N_OCR_STRICT_RESPONSE", None), True),
    ]
    for name, val, default in flags:
        if val is None:
            s.bad("มีค่าตั้ง %s" % name, got="ไม่มีในโค้ด",
                  fix="โค้ดบนเครื่องนี้เก่ากว่ารอบนี้ — git pull แล้วรีสตาร์ต")
        elif val == default:
            s.ok("%s = %r (ค่าเริ่มต้น)" % (name, val))
        else:
            s.info("%s = %r  ⚠ ถูก override จากค่าเริ่มต้น %r" % (name, val, default))

    s.info("OCR backend ที่ resolve ได้", _resolved_backend())
    s.info("N8N_OCR_WEBHOOK_URL", appcfg.N8N_OCR_WEBHOOK_URL)

    # ── แพ็กเกจ optional: ตัวหนึ่งเป็นจุดบอด QC อีกตัวแค่ความสวยงาม ──
    try:
        from spellchecker import SpellChecker  # noqa: F401
        s.ok("pyspellchecker ติดตั้งแล้ว (ชั้น dictionary ทำงาน)")
    except Exception as e:
        s.bad("pyspellchecker ติดตั้งแล้ว", got=str(e),
              fix="py -3.9 -m pip install pyspellchecker — ถ้าไม่มี ชั้นตรวจคำสะกด"
                  "จะถูกข้าม **เงียบ ๆ** คำผิดจะขึ้น ✓ เหมือนไม่มีปัญหา (จุดบอด QC)")
    try:
        import pytesseract
        s.ok("pytesseract + tesseract binary",
             got=str(pytesseract.get_tesseract_version()))
    except Exception as e:
        s.info("pytesseract/tesseract ไม่พร้อม (ไม่ใช่จุดบอด QC — แค่ไม่มีกรอบแดง"
               "บนไฟล์ outline/ภาพถ่าย ผลตรวจเท่าเดิม)", got=str(e)[:80])


def _resolved_backend() -> str:
    try:
        from artwork_check import ocr
        fn = getattr(ocr, "_resolve_backend", None)
        if fn:
            b = fn()
            return b if isinstance(b, str) else getattr(b, "__name__", str(b))
    except Exception:
        pass
    import config as appcfg
    return appcfg.OCR_BACKEND or "(auto)"


# ─────────────────────────────────────────────────────────────────────
# ชั้น B — ตรรกะ QC end-to-end ผ่าน pipeline ตัวจริง
# ─────────────────────────────────────────────────────────────────────
PANEL_LINES = [
    "TUNA IN SUNFLOWER OIL",
    "INGREDIENTS: TUNA, SUNFLOWER OIL, SALT",
    "NET WEIGHT {net} g",
    "PRODUCT OF THAILAND",
    "BEST BEFORE 12/2027",
    "NUTRITION PER 100 g",
    "Energy 170 kcal",
    "Total fat 8 g",
    "Protein 24 g",
]


def _make_pdf(path: str, nets=(170, 185, 170)):
    """PDF 3 แผงที่มี text layer จริง — แผงกลางน้ำหนักผิด (185 vs 170)."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=842, height=595)
    for col, net in enumerate(nets):
        x = 40 + col * 260
        for i, t in enumerate(PANEL_LINES):
            page.insert_text((x, 60 + i * 16), t.format(net=net),
                             fontsize=9, fontname="helv")
    doc.save(path)
    doc.close()


def _zones(groups):
    """3 โซนวางทับ 3 แผงพอดี (สัดส่วนของหน้า)."""
    xs = [(0.035, 0.28), (0.345, 0.28), (0.655, 0.28)]
    return [{"id": "z%d" % (i + 1), "type": "panel", "group": g, "doc": "a",
             "rotate": 0, "bbox": [x, 0.07, w, 0.30], "label": "โซน %d" % (i + 1)}
            for i, ((x, w), g) in enumerate(zip(xs, groups))]


def section_b(s: Suite):
    s.section("B. ตรรกะ QC ตั้งแต่ต้นจนจบ (เรียก pipeline.run_inspection ตัวจริง)")

    try:
        import fitz  # noqa: F401
    except Exception as e:
        s.skip("ทั้งชั้น B", "ไม่มี PyMuPDF: %s" % e)
        return

    from artwork_check import config as awcfg, pipeline

    tmp = tempfile.mkdtemp(prefix="aw_verify_")
    saved_dir = awcfg.INSPECTIONS_DIR
    try:
        # ⚠️ ห้ามเขียนลงคลังจริงของผู้ใช้ — เปลี่ยนปลายทางเป็น temp ชั่วคราว
        awcfg.INSPECTIONS_DIR = os.path.join(tmp, "inspections")
        os.makedirs(awcfg.INSPECTIONS_DIR, exist_ok=True)

        pdf = os.path.join(tmp, "artwork.pdf")
        _make_pdf(pdf)
        with open(pdf, "rb") as f:
            blob = f.read()

        rec = pipeline.start_inspection(blob, "artwork.pdf")
        rid = rec["id"]
        s.check(rec.get("has_text_layer"), "PDF ทดสอบมี text layer (ไม่ต้องพึ่ง OCR)",
                got="has_text_layer=%s" % rec.get("has_text_layer"))

        # ── เคส 1: กลุ่มต่างกัน (ค่าเริ่มต้นที่ผู้ใช้ได้ตอนลากโซนใหม่) ──
        rep1 = pipeline.run_inspection(rid, _zones(["A", "B", "C"]))
        cov1 = rep1.get("coverage") or {}
        cp1 = cov1.get("cross_panel") or {}
        s.check(bool(cov1), "รายงานมีข้อมูล coverage",
                got="คีย์: %s" % ", ".join(sorted(cov1)),
                fix="โค้ดเก่ากว่า e39a29e — git pull แล้วรีสตาร์ต")
        s.check(cp1.get("ran") is False and cp1.get("reason") == "no_shared_group",
                "กลุ่มต่างกัน → ชั้นเทียบข้ามแผง 'ไม่ได้ทำงาน' และบอกเหตุผลถูก",
                got="ran=%s reason=%s" % (cp1.get("ran"), cp1.get("reason")))
        n_mm1 = (rep1.get("summary") or {}).get("MISMATCH_PANELS", 0)
        s.check(n_mm1 == 0,
                "กลุ่มต่างกัน → ไม่มีทางจับความต่างระหว่างแผงได้ (ยืนยันจุดบอดเดิม)",
                got="MISMATCH_PANELS=%d verdict=%s" % (n_mm1, rep1.get("verdict")))

        # ── เคส 2: ตั้ง 2 โซนแรกเป็นกลุ่มเดียวกัน ──
        rep2 = pipeline.run_inspection(rid, _zones(["A", "A", "C"]))
        cov2 = rep2.get("coverage") or {}
        cp2 = cov2.get("cross_panel") or {}
        s.check(cp2.get("ran") is True and "A" in (cp2.get("groups") or []),
                "ตั้งกลุ่ม A ตรงกัน → ชั้นเทียบข้ามแผง 'ทำงาน' และรายงานกลุ่มถูก",
                got="ran=%s groups=%s" % (cp2.get("ran"), cp2.get("groups")))
        n_mm2 = (rep2.get("summary") or {}).get("MISMATCH_PANELS", 0)
        s.check(n_mm2 >= 1,
                "จับความต่างจริงได้ (น้ำหนัก 170 vs 185 g)",
                got="MISMATCH_PANELS=%d verdict=%s" % (n_mm2, rep2.get("verdict")),
                fix="ถ้าเป็น 0 แปลว่าชั้นเทียบข้ามแผงไม่ทำงาน ทั้งที่ coverage บอกว่าทำงาน"
                    " — รายงาน coverage ที่โกหกอันตรายกว่าไม่มีเลย")
        found = " | ".join(d.get("found", "") for d in rep2.get("defects", [])
                           if d.get("class") == "MISMATCH_PANELS")
        s.check("185" in found or "170" in found,
                "defect ชี้ไปที่บรรทัดน้ำหนักจริง", got=found[:100])

        # ── coverage ต้องรายงานครบทุกชั้น และห้ามแตะ verdict ──
        for layer in ("cross_panel", "zoom", "numbers", "spelling"):
            s.check(layer in cov2, "coverage รายงานชั้น '%s'" % layer)
        s.check(rep2.get("verdict") in ("PASS", "REVIEW", "FAIL"),
                "verdict ยังเป็นค่าเดิม 3 แบบ (coverage เป็น advisory ไม่แตะ verdict)",
                got=str(rep2.get("verdict")))

        # ── โซนเดียวพังต้องไม่ล้มทั้งใบ (e45fff0) ──
        _check_one_bad_zone(s, pipeline, rid)

        # ── เทียบภาพเก่า/ใหม่ (advisory) ──
        _check_pixdiff(s, pipeline, tmp)
    except Exception as e:
        s.bad("ชั้น B ทำงานจนจบ", got="%s: %s" % (type(e).__name__, e),
              fix="ดู traceback ด้วย --verbose")
        if s.verbose:
            import traceback
            traceback.print_exc()
    finally:
        awcfg.INSPECTIONS_DIR = saved_dir
        shutil.rmtree(tmp, ignore_errors=True)


def _make_panel_pdf(path, page_w, page_h, at, net=170):
    """ฉลากแผงเดียว ขนาดจริงคงที่ วางบนหน้าขนาดใดก็ได้ ตำแหน่งใดก็ได้ —
    จำลองเคสจริง 'งานเดียวกันคนละ layout' (A4 proof vs แผ่นพิมพ์ใหญ่)"""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=page_w, height=page_h)
    x0, y0 = at
    page.draw_rect(fitz.Rect(x0, y0, x0 + 220, y0 + 150), color=(0, 0, 0), width=1)
    for i, t in enumerate(PANEL_LINES[:4]):
        page.insert_text((x0 + 10, y0 + 25 + i * 18), t.format(net=net),
                         fontsize=9, fontname="helv")
    doc.save(path)
    doc.close()


def _check_pixdiff(s: Suite, pipeline, tmp: str):
    """เทียบภาพเก่า/ใหม่ — ต้องเทียบได้แม้หน้าคนละขนาด และต้อง **ไม่แตะ
    ผลตรวจ QC** (ไม่เขียน report.json / ไม่เปลี่ยน verdict)"""
    from artwork_check import config as awcfg

    if not getattr(awcfg, "PIXDIFF_ENABLED", False):
        s.info("เทียบภาพเก่า/ใหม่", "ปิดอยู่ (ARTWORK_PIXDIFF_ENABLED=0)")
        return
    new_pdf = os.path.join(tmp, "pd_new.pdf")
    old_pdf = os.path.join(tmp, "pd_old.pdf")
    _make_panel_pdf(new_pdf, 842, 595, (60, 80), net=185)      # ฉบับใหม่
    _make_panel_pdf(old_pdf, 1684, 1190, (700, 500), net=170)  # ฉบับเก่า หน้าใหญ่กว่า

    with open(new_pdf, "rb") as f:
        rec = pipeline.start_inspection(f.read(), "pd_new.pdf")
    rid = rec["id"]
    with open(old_pdf, "rb") as f:
        pipeline.start_ref(rid, f.read(), "pd_old.pdf")

    zone_list = [
        {"id": "z1", "type": "panel", "group": "A", "doc": "a", "rotate": 0,
         "bbox": [50 / 842.0, 70 / 595.0, 240 / 842.0, 170 / 595.0], "label": "แผงหน้า"},
        {"id": "b1", "type": "panel", "group": "A", "doc": "b", "rotate": 0,
         "bbox": [690 / 1684.0, 490 / 1190.0, 240 / 1684.0, 170 / 1190.0],
         "label": "แผงหน้า (เก่า)"},
        {"id": "z9", "type": "panel", "group": "Z", "doc": "a", "rotate": 0,
         "bbox": [0.6, 0.6, 0.2, 0.2], "label": "โซนไม่มีคู่"},
    ]
    rep = pipeline.run_pixdiff(rid, zone_list)
    z1 = next((z for z in rep["zones"] if z["zone_id"] == "z1"), {})
    z9 = next((z for z in rep["zones"] if z["zone_id"] == "z9"), {})

    s.check(z1.get("status") == "ok",
            "เทียบแผงเดียวกันได้ แม้หน้าสองไฟล์คนละขนาด (A4 vs แผ่นใหญ่)",
            got="status=%s %s" % (z1.get("status"), z1.get("message", ""))[:110],
            fix="เคสนี้คือเคสจริงที่พบบ่อยที่สุด — โหมดเทียบทั้งหน้าทำไม่ได้")
    s.check(z1.get("region_count", 0) >= 1,
            "จับความต่างจริงได้ (น้ำหนัก 170 vs 185 g)",
            got="%d บริเวณ · สเกลที่วัดได้ %s · คะแนนจับคู่ %s"
                % (z1.get("region_count", 0), z1.get("scale_ratio"),
                   z1.get("match_score")))
    s.check(z9.get("status") == "skipped" and z9.get("reason") == "no_pair",
            "โซนที่ไม่มีคู่ถูกรายงานว่าข้าม พร้อมเหตุผล (ไม่เงียบหาย)",
            got="status=%s reason=%s" % (z9.get("status"), z9.get("reason")))

    d = pipeline.report.inspection_dir(rid)
    s.check(not os.path.exists(os.path.join(d, "report.json")),
            "เทียบภาพแล้ว **ไม่สร้าง/ไม่แตะ report.json** (แยกจากผลตรวจ QC)",
            got="พบ report.json ทั้งที่ยังไม่ได้กดส่งตรวจสอบ",
            fix="ชั้นนี้ต้องเป็น advisory ล้วน ห้ามกระทบ verdict/การนับ")
    s.check(pipeline.load_pixdiff(rid) is not None,
            "ผลถูกบันทึกแยกใน pixdiff.json และโหลดกลับได้")
    png = pipeline.pixdiff_zone_png(rid, "z1")
    s.check(bool(png) and len(png) > 1000,
            "สร้างภาพกรอบส้มชี้บริเวณที่ต่างได้ (ไม่ต้องรอ report.json)",
            got="%s bytes" % (len(png) if png else 0))


def _check_one_bad_zone(s: Suite, pipeline, rid: str):
    """โซนที่ backend โยน exception ต้องกลายเป็น UNREADABLE เฉพาะโซนนั้น
    ส่วนโซนที่เหลือและชั้นเทียบข้ามแผงต้องยังรายงานออกมาครบ."""
    from artwork_check import ocr

    class _Boom:
        @staticmethod
        def is_enabled():
            return True

        @staticmethod
        def ocr_image(*a, **k):
            raise RuntimeError("จำลอง backend ระเบิด")

    saved = ocr.vertex_client
    try:
        ocr.vertex_client = _Boom
        # โซนที่ 3 วางบนพื้นที่ว่างของหน้า → ไม่มี text layer → ต้องยิง OCR (ที่พัง)
        z = _zones(["A", "A", "C"])
        z[2]["bbox"] = [0.10, 0.60, 0.35, 0.30]
        rep = pipeline.run_inspection(rid, z)
    except Exception as e:
        s.bad("โซนเดียวพังต้องไม่ล้มการตรวจทั้งใบ",
              got="run_inspection โยน %s: %s" % (type(e).__name__, e),
              fix="ต้องมี try/except ครอบใน ocr.read_zone (commit e45fff0)")
        return
    finally:
        ocr.vertex_client = saved

    n_un = (rep.get("summary") or {}).get("UNREADABLE", 0)
    n_mm = (rep.get("summary") or {}).get("MISMATCH_PANELS", 0)
    s.check(n_un >= 1, "backend พัง → โซนนั้นกลายเป็น UNREADABLE (ไม่ใช่ HTTP 500)",
            got="UNREADABLE=%d" % n_un)
    s.check(n_mm >= 1, "โซนอื่นและชั้นเทียบข้ามแผงยังทำงานครบ",
            got="MISMATCH_PANELS=%d" % n_mm,
            fix="โซนเดียวพังไม่ควรทำให้ผู้ตรวจไม่ได้อะไรเลย")


# ─────────────────────────────────────────────────────────────────────
# ชั้น C — การแกะคำตอบ N8N (ยิง HTTP จริงไป mock server ในเครื่อง)
# ─────────────────────────────────────────────────────────────────────
FENCED = "```json\n{\"text\": \"NET WEIGHT 170 g\", \"blocks\": []}\n```"
HTML_ERR = ("<!DOCTYPE html><html><head><title>Error</title></head>"
            "<body>Workflow could not be started</body></html>")

# path → (status, content-type, body). ใช้จำลองคำตอบที่ N8N/Gemini คืนมาจริง
MOCK_CASES = {
    "/plain-json": (200, "application/json",
                    '{"text": "NET WEIGHT 170 g", "blocks": []}'),
    "/fenced": (200, "text/plain", FENCED),
    "/html": (200, "text/html", HTML_ERR),
    "/nested": (200, "application/json",
                json.dumps({"data": "```json\n{\"text\": \"INGREDIENTS TUNA\"}\n```"})),
    "/array": (200, "application/json", '[{"text": "PRODUCT OF THAILAND"}]'),
    "/text-is-json": (200, "application/json",
                      json.dumps({"text": "{\"text\": \"BEST BEFORE 12/2027\"}"})),
    "/plaintext": (200, "text/plain", "INGREDIENTS: TUNA <500 mg SALT"),
    "/conf-word": (200, "application/json",
                   '{"text": "NET 170 g", "blocks": [{"text": "NET", '
                   '"bbox": [1,2,3,4], "conf": "high"}]}'),
    "/err500": (500, "text/plain", "boom"),
    "/err404": (404, "text/plain", "not registered"),
}


class _MockOCR:
    """HTTP server ในเครื่องที่ตอบตาม path — ทำให้ชั้น C วิ่งผ่านเส้นทาง
    เดียวกับ production ทั้งหมด (requests → retry → แกะคำตอบ) โดยไม่ออกเน็ต."""

    def __init__(self):
        try:
            from http.server import BaseHTTPRequestHandler, HTTPServer
        except ImportError:                       # pragma: no cover
            raise RuntimeError("ไม่มี http.server")
        self.hits = {}
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):                    # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                outer.hits[self.path] = outer.hits.get(self.path, 0) + 1
                status, ctype, body = MOCK_CASES.get(
                    self.path, (404, "text/plain", "?"))
                raw = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):            # เงียบ
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()


def section_c(s: Suite):
    s.section("C. การแกะคำตอบ N8N (ยิง HTTP จริงไปยัง mock server ในเครื่อง)")

    from inspectors import ocr_n8n

    img = b"\xff\xd8\xff" + b"\x00" * 64        # magic JPEG พอให้ผ่านฝั่งเรา
    try:
        mock = _MockOCR()
    except Exception as e:
        s.skip("ทั้งชั้น C", str(e))
        return

    with mock as m:
        base = "http://127.0.0.1:%d" % m.port

        def call(path, timeout=10.0):
            return ocr_n8n.ocr_image(img, url=base + path, timeout=timeout)

        r = call("/plain-json")
        s.check(r["text"] == "NET WEIGHT 170 g" and not r.get("error"),
                "JSON ตรง ๆ → ได้ข้อความถูก", got=repr(r["text"])[:80])

        r = call("/fenced")
        s.check(r["text"] == "NET WEIGHT 170 g",
                "Gemini ครอบรั้ว ```json → ถอดรั้วได้ข้อความจริง",
                got=repr(r["text"])[:80],
                fix="ถ้าได้ทั้งก้อนรวมรั้ว คำว่า json/text/blocks จะกลายเป็น"
                    "'ตัวอักษรแปลก ๆ' ในผลตรวจ (commit d460924)")
        s.check(not any(k in r["text"] for k in ("json", "blocks", "{")),
                "ข้อความที่ได้ไม่มีเศษ JSON ปน", got=repr(r["text"])[:80])

        r = call("/html")
        s.check(bool(r.get("error")) and not r["text"],
                "N8N คืนหน้า HTML (workflow ไม่ Activate) → ปฏิเสธ ไม่เอาไปเป็นข้อความ",
                got="error=%s text=%r" % (str(r.get("error"))[:60], r["text"][:40]),
                fix="ตั้ง N8N_OCR_STRICT_RESPONSE=1 (ค่าเริ่มต้น)")

        r = call("/nested")
        s.check(r["text"] == "INGREDIENTS TUNA",
                "JSON ซ้อนในฟิลด์ data + รั้วซ้อน → แกะได้", got=repr(r["text"])[:80])

        r = call("/array")
        s.check(r["text"] == "PRODUCT OF THAILAND",
                "N8N คืนเป็น array → แกะได้", got=repr(r["text"])[:80])

        r = call("/text-is-json")
        s.check(r["text"] == "BEST BEFORE 12/2027",
                "JSON ซ่อนอยู่ในฟิลด์ text เอง → แกะได้", got=repr(r["text"])[:80])

        r = call("/plaintext")
        s.check(r["text"].startswith("INGREDIENTS") and r.get("warning"),
                "plain text ที่มี '<' กลางข้อความ → ใช้ได้ แต่ติดธงเตือน",
                got="text=%r warning=%s" % (r["text"][:40], bool(r.get("warning"))),
                fix="ถ้าโดนปฏิเสธ = _looks_like_html เข้มเกินไป โซนที่อ่านได้จริง"
                    "จะกลายเป็น UNREADABLE ฟรี ๆ")

        r = call("/conf-word")
        s.check(r["text"] == "NET 170 g" and r["blocks"] and r["blocks"][0]["conf"] == 0.0,
                'LLM คืน conf เป็นคำ ("high") → ไม่ระเบิด แปลงเป็น 0.0',
                got=json.dumps(r["blocks"], ensure_ascii=False)[:90],
                fix="float() เปลือยจะโยน ValueError ทะลุถึง read_zone = ล้มทั้งใบ")

        import config as appcfg
        tries = max(1, int(appcfg.N8N_OCR_RETRIES) + 1)
        m.hits.clear()
        r = call("/err500")
        s.check(m.hits.get("/err500") == tries and bool(r.get("error")),
                "HTTP 500 = ล้มชั่วคราว → ลองซ้ำตาม N8N_OCR_RETRIES",
                got="ยิงไป %d ครั้ง (คาด %d)" % (m.hits.get("/err500", 0), tries))

        m.hits.clear()
        r = call("/err404")
        s.check(m.hits.get("/err404") == 1 and bool(r.get("error")),
                "HTTP 404 (workflow ไม่ Activate) = ยิงซ้ำก็ผลเดิม → ต้องไม่ลองซ้ำ",
                got="ยิงไป %d ครั้ง (คาด 1)" % m.hits.get("/err404", 0),
                fix="ลองซ้ำกับ 404 = ทำให้ผู้ตรวจรอฟรี")

    # URL ผิดรูป — สัญญา "Never raises" ต้องเป็นจริง
    try:
        r = ocr_n8n.ocr_image(img, url="http://[::bad::url", timeout=2.0)
        s.check(bool(r.get("error")) and not r["text"],
                "URL ผิดรูป → คืน error ไม่โยน exception (สัญญา Never raises)",
                got=str(r.get("error"))[:70])
    except Exception as e:
        s.bad("URL ผิดรูป → คืน error ไม่โยน exception",
              got="โยน %s: %s" % (type(e).__name__, e),
              fix="ocr_image ต้องดัก Exception กว้าง ไม่ใช่แค่ RequestException")


# ─────────────────────────────────────────────────────────────────────
# ชั้น D — คุณภาพการอ่าน (text layer เสีย + โซนเล็ก)
# ─────────────────────────────────────────────────────────────────────
# ⚠️ ตัวอย่างต้องมี "คำยาว >= 8 ตัวอักษร" อย่างน้อย PDFTEXT_GARBLED_MIN_TOKENS
# คำ ไม่งั้นด่านนี้จะไม่ตัดสินเลย (คืน False ทั้งคู่) แล้วเทสต์จะผ่านแบบไร้ความหมาย
GOOD_TEXT = ("INGREDIENTS SUNFLOWER PRESERVATIVE ANTIOXIDANT NUTRITIONAL "
             "INFORMATION MANUFACTURED DISTRIBUTED THAILAND REFRIGERATE "
             "ANTIOXIDANTS12345 E1520 OMEGA-3 170G")
GARBLED_TEXT = ("IN2GREDIENTS SU4NFLOWER PRE5SERVATIVE ANT7IOXIDANT "
                "NUT3RITIONAL INFO9RMATION MAN2UFACTURED DIS8TRIBUTED "
                "THA5ILAND REF3RIGERATE")


def section_d(s: Suite):
    s.section("D. คุณภาพการอ่าน — text layer ที่เสีย + การเพิ่ม DPI ให้โซนเล็ก")

    from artwork_check import config as awcfg, ocr

    # ── ① ไม่เชื่อ text layer ที่ผิดรูป ──
    # ตัวอย่างต้อง "ยาวพอให้ด่านยอมตัดสิน" ก่อน ไม่งั้นทั้งสองข้อจะได้ False
    # เหมือนกันแล้วผ่านแบบไร้ความหมาย (เครื่องมือวัดที่วัดผิดทางแย่กว่าไม่มี)
    n_good = len(ocr._long_tokens(GOOD_TEXT))
    n_bad = len(ocr._long_tokens(GARBLED_TEXT))
    s.check(min(n_good, n_bad) >= awcfg.PDFTEXT_GARBLED_MIN_TOKENS,
            "ตัวอย่างทดสอบมีคำยาวมากพอให้ด่านนี้ยอมตัดสิน",
            got="คำยาว: ปกติ %d · เสีย %d (ต้อง >= %d)"
                % (n_good, n_bad, awcfg.PDFTEXT_GARBLED_MIN_TOKENS))
    s.check(ocr.text_looks_garbled(GOOD_TEXT) is False,
            "ข้อความปกติ → ไม่ถูกตัดสินว่าเสีย (false positive = 0)",
            got="garbled=%s" % ocr.text_looks_garbled(GOOD_TEXT))
    s.check(ocr.text_looks_garbled(GARBLED_TEXT) is True,
            "text layer ที่เลขแทรกกลางคำ → ถูกปฏิเสธ ไม่เอาไปเทียบด้วย conf=1.0",
            got="garbled=%s" % ocr.text_looks_garbled(GARBLED_TEXT),
            fix="ถ้าไม่จับ ขยะจาก encoding เสียจะถูกใช้เป็นข้อความบนฉลาก"
                "ด้วยความมั่นใจสูงสุด (ละเมิดกฎเหล็กข้อ 2)")
    s.check(ocr.text_looks_garbled("AWN202500022003 LOT99") is False,
            "โซนสั้น (รหัสงาน) → ไม่ตัดสิน เพราะ token น้อยกว่า %d"
            % awcfg.PDFTEXT_GARBLED_MIN_TOKENS)
    try:
        s.check(ocr.text_looks_garbled(None) is False,
                "text_looks_garbled(None) ไม่โยน exception")
    except Exception as e:
        s.bad("text_looks_garbled(None) ไม่โยน exception", got=str(e))

    # ── ② โซนเล็กต้องถูกเรนเดอร์ใหม่ที่ DPI สูงขึ้น (เฉพาะ PDF) ──
    try:
        import fitz  # noqa: F401
        import cv2
    except Exception as e:
        s.skip("การเพิ่ม DPI ให้โซนเล็ก", "ไม่มี PyMuPDF/cv2: %s" % e)
        return

    from artwork_check.pdf_ingest import ArtworkDocument

    tmp = tempfile.mkdtemp(prefix="aw_verify_d_")
    try:
        pdf = os.path.join(tmp, "a.pdf")
        _make_pdf(pdf)
        doc = ArtworkDocument(pdf)
        small = [0.035, 0.07, 0.09, 0.10]       # โซนเล็กมาก
        raw = doc.render_zone(small, dpi=awcfg.OCR_DPI,
                              max_side=awcfg.OCR_CROP_MAX_SIDE)
        used = ocr._render_for_ocr(doc, small)
        raw_long = max(raw.shape[:2])
        used_long = max(used.shape[:2])
        want = min(awcfg.OCR_CROP_MIN_SIDE,
                   int(raw_long * awcfg.OCR_DPI_MAX_FACTOR))
        s.check(used_long >= want * 0.95,
                "PDF: โซนเล็กถูกเรนเดอร์ใหม่ที่ DPI สูงขึ้นก่อนส่ง OCR",
                got="ด้านยาว %dpx → %dpx (เป้า ~%dpx)" % (raw_long, used_long, want),
                fix="ถ้าไม่ขยาย ตัวอักษรเหลือ ~9px แล้ว recall ตกเหลือ 1.2%")

        # ภาพ raster ห้ามขยาย (ขยายพิกเซลที่ไม่มีข้อมูลเพิ่ม = เบลอเปล่า ๆ)
        png = os.path.join(tmp, "a.png")
        cv2.imwrite(png, doc.render(150))
        rdoc = ArtworkDocument(png)
        rraw = rdoc.render_zone(small, dpi=awcfg.OCR_DPI,
                                max_side=awcfg.OCR_CROP_MAX_SIDE)
        rused = ocr._render_for_ocr(rdoc, small)
        s.check(rused.shape == rraw.shape,
                "ภาพ raster (ภาพถ่าย/PNG) ไม่ถูกขยาย — ตรงตามที่ออกแบบ",
                got="%s vs %s" % (rraw.shape[:2], rused.shape[:2]))
    except Exception as e:
        s.bad("การเพิ่ม DPI ให้โซนเล็ก", got="%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────
# ชั้น E — ค่าคงที่/องค์ประกอบฝั่ง UI ที่พังเงียบได้
# ─────────────────────────────────────────────────────────────────────
JS_PATH = os.path.join(ROOT, "static", "js", "artwork_check.js")
TPL_MAIN = os.path.join(ROOT, "templates", "artwork_check.html")
TPL_HIST = os.path.join(ROOT, "templates", "artwork_check_history.html")

# คลาสที่ renderReport() พ่นออกมา และหน้าประวัติเรียกใช้ฟังก์ชันเดียวกัน
# ⇒ ต้องมีกฎ CSS ครบ **ทั้งสองไฟล์** ไม่งั้นกล่องนั้นโผล่แบบไม่มีสไตล์
# บนหน้าประวัติโดยไม่มี error ให้เห็น (เคยเกิดจริง 3 คลาส)
REPORT_CLASSES = [
    "aw-verdict", "aw-v-pass", "aw-v-review", "aw-v-fail",
    "aw-cov", "aw-cov-head", "aw-cov-rows", "aw-cov-row", "aw-cov-why",
    "aw-cov-fix", "aw-summary", "aw-sumcard", "aw-defect", "aw-defect-class",
    "aw-img-pair", "aw-img-card", "aw-img-label", "aw-hl-warn", "aw-note",
    # เทียบภาพเก่า/ใหม่ (pixdiff) — renderReport เรียก pixdiffHtml ต่อ
    "aw-pd", "aw-pd-head", "aw-pd-note", "aw-pd-rows", "aw-pd-row",
    "aw-pd-dot", "aw-pd-name", "aw-pd-why", "aw-pd-fix", "aw-pd-img",
]


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def section_e(s: Suite):
    s.section("E. องค์ประกอบฝั่ง UI ที่ 'พังเงียบ' ได้ (ไม่มี error ให้เห็น)")

    for p in (JS_PATH, TPL_MAIN, TPL_HIST):
        if not os.path.exists(p):
            s.bad("มีไฟล์ %s" % os.path.relpath(p, ROOT), got="ไม่พบ")
            return
    js, main, hist = _read(JS_PATH), _read(TPL_MAIN), _read(TPL_HIST)

    # ── ① CSS ของ renderReport ต้องครบทั้ง 2 template ──
    missing_main = [c for c in REPORT_CLASSES if ("." + c) not in main]
    missing_hist = [c for c in REPORT_CLASSES if ("." + c) not in hist]
    s.check(not missing_main, "CSS ของ renderReport ครบใน artwork_check.html",
            got="ขาด " + ", ".join(missing_main))
    s.check(not missing_hist,
            "CSS ของ renderReport ครบใน artwork_check_history.html",
            got="ขาด " + ", ".join(missing_hist),
            fix="หน้าประวัติเรียก window.awRenderReport ตัวเดียวกัน แต่ CSS อยู่คนละ"
                " block — ขาดแล้วรูปจะกางเต็มจอ/ไม่มีกรอบ โดยไม่มี error")

    # ── ② element ที่ JS อ้างถึง ต้องมีจริงใน template ──
    for eid, what in [("awPixdiff", "ปุ่มเทียบภาพเก่า/ใหม่"),
                      ("awZoomFit", "ปุ่มพอดีความกว้าง"),
                      ("awZoomFitPage", "ปุ่มพอดีทั้งหน้า"),
                      ("awDrawContinuous", "ติ๊กวาดต่อเนื่อง"),
                      ("awRestore", "แถบกู้คืนโซนที่ค้างไว้"),
                      ("awGroupHint", "คำเตือนกลุ่มก่อนส่งตรวจ"),
                      ("awStageBox", "กล่องภาพ (ใช้อ้างแทน closest())")]:
        in_js = ('"%s"' % eid) in js
        in_tpl = ('id="%s"' % eid) in main
        s.check(in_js and in_tpl, "มี #%s ทั้งใน JS และ template — %s" % (eid, what),
                got="js=%s template=%s" % (in_js, in_tpl),
                fix="$(\"id\") ที่ไม่มีจริงจะเงียบ ไม่ error — ฟีเจอร์หายไปเฉย ๆ")

    # ── ③ ค่าคงที่ที่มีสองฝั่ง ต้องตรงกัน ──
    m_js = re.search(r"const\s+ZOOM_MIN\s*=\s*(\d+)", js)
    m_tpl = re.search(r'id="awZoomRange"[^>]*?min="(\d+)"', main, re.S)
    if m_js and m_tpl:
        s.check(m_js.group(1) == m_tpl.group(1),
                "ZOOM_MIN (JS) ตรงกับ min= ของ #awZoomRange (template)",
                got="JS=%s template=%s" % (m_js.group(1), m_tpl.group(1)),
                fix="ไม่ตรง = ปุ่ม 'พอดีทั้งหน้า' โกหก (ภาพยังล้นกล่อง) และ"
                    "สไลเดอร์ค้างคนละค่ากับภาพ")
    else:
        s.bad("อ่านค่า ZOOM_MIN / min= ได้", got="js=%s tpl=%s" % (bool(m_js), bool(m_tpl)))

    from artwork_check import zones as zmod
    m = re.search(r"HL_MIN_SHORT\s*=\s*(\d+)\s*,\s*HL_MAX_ASPECT\s*=\s*([\d.]+)", js)
    if m:
        s.check(int(m.group(1)) == zmod.HL_MIN_SHORT_SIDE
                and float(m.group(2)) == zmod.HL_MAX_ASPECT,
                "เกณฑ์เตือนกรอบแดง (HL_*) ตรงกันทั้ง Python และ JS",
                got="JS=%s/%s  Python=%s/%s" % (m.group(1), m.group(2),
                                                zmod.HL_MIN_SHORT_SIDE, zmod.HL_MAX_ASPECT),
                fix="ไม่ตรง = คำเตือนตอนจัดโซนกับตอนดูผลจะไม่ตรงกัน")
    else:
        s.bad("อ่านค่า HL_* ฝั่ง JS ได้")

    m = re.search(r'GROUP_LETTERS\s*=\s*"([A-Z]+)"', js)
    if m:
        s.check(m.group(1) == zmod.GROUP_LETTERS,
                "ลำดับตัวอักษรกลุ่ม (GROUP_LETTERS) ตรงกันทั้งสองฝั่ง",
                got="JS=%s Python=%s" % (m.group(1), zmod.GROUP_LETTERS))
    else:
        s.bad("อ่านค่า GROUP_LETTERS ฝั่ง JS ได้")

    # ── ④ ข้อความ/กุญแจที่ฟีเจอร์รอบนี้ต้องมี ──
    s.check("ไม่พบประเด็นในชั้นที่ตรวจ" in js,
            "verdict PASS ที่มีชั้นขาด ใช้ข้อความ 'ไม่พบประเด็นในชั้นที่ตรวจ'",
            fix="ไม่งั้นหัวเรื่องจะพูดเกินจริงว่าไม่พบประเด็น ทั้งที่บางชั้นไม่เคยทำงาน")
    s.check('"aw.session.v1"' in js or "'aw.session.v1'" in js,
            "autosave โซนลง localStorage (aw.session.v1)")
    s.check("window.awPixdiffHtml" in js,
            "renderReport เรียก pixdiffHtml ได้ (การ์ดเทียบภาพขึ้นทั้ง 2 หน้า)")
    s.check("awRenderReport" in hist,
            "หน้าประวัติเรียก window.awRenderReport ตัวเดียวกับหน้าตรวจ")

    # guard ต้องอยู่ "หลัง" การ export window.aw* ทุกตัว ไม่งั้นหน้าประวัติเรียกไม่ได้
    guard = js.find('if (!$("awFile")) return;')
    last_export = max([js.rfind("window.aw" + n) for n in
                       ("Api", "Esc", "RenderReport", "CoverageHtml",
                        "WireZoomable", "RenderTextTable", "HlRiskText")])
    s.check(guard > last_export,
            "guard `if (!$(\"awFile\")) return;` อยู่หลัง window.aw* ทุกตัว",
            got="guard@%d  export สุดท้าย@%d" % (guard, last_export),
            fix="ถ้า guard มาก่อน หน้าประวัติจะเรียก awRenderReport ไม่ได้")

    # ── ⑤ syntax ของ JS (ถ้ามี node) ──
    if shutil.which("node"):
        r = subprocess.call(["node", "--check", JS_PATH],
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        s.check(r == 0, "node --check static/js/artwork_check.js")
    else:
        s.skip("node --check artwork_check.js", "ไม่มี node บนเครื่องนี้")


# ─────────────────────────────────────────────────────────────────────
# ชั้น F — ยิง N8N จริง (ต้องใส่ --n8n)
# ─────────────────────────────────────────────────────────────────────
def section_f(s: Suite):
    s.section("F. ยิง N8N จริง (เส้นทางเดียวกับตอนตรวจจริง)")

    import config as appcfg
    from inspectors import ocr_n8n

    if not ocr_n8n.is_enabled():
        s.bad("N8N_OCR_WEBHOOK_URL ถูกตั้งค่า", got="ว่าง")
        return
    url = appcfg.N8N_OCR_WEBHOOK_URL
    s.info("ยิงไปที่", url)
    if "/webhook-test/" in url:
        s.bad("ใช้ Production URL ไม่ใช่ Test URL", got=url,
              fix="URL ที่มี /webhook-test/ ใช้ได้เฉพาะตอนกด 'Listen for test event' "
                  "— ของจริงต้องเป็น /webhook/")
    if "localhost" in url:
        s.info("⚠ URL ใช้ 'localhost'", "Windows resolve เป็น ::1 (IPv6) ก่อน — "
                                        "ถ้า N8N ผูกเฉพาะ IPv4 จะต่อไม่ติดแบบไม่มี error ที่อ่านออก "
                                        "แนะนำ 127.0.0.1")

    try:
        import cv2
        import numpy as np
    except Exception as e:
        s.skip("ยิงภาพทดสอบ", "ไม่มี cv2/numpy: %s" % e)
        return

    img = np.full((200, 700, 3), 255, dtype=np.uint8)
    cv2.putText(img, "DIAGNOSE 12345", (30, 120), cv2.FONT_HERSHEY_SIMPLEX,
                2.0, (0, 0, 0), 4, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", img)
    if not ok:
        s.bad("เข้ารหัสภาพทดสอบ")
        return

    r = ocr_n8n.ocr_image(buf.tobytes())
    if r.get("error"):
        s.bad("ยิง N8N แล้วได้ผล OCR กลับมา", got=str(r["error"])[:160],
              fix="connection refused = N8N ไม่ได้รัน · 404 = workflow ไม่ได้ Activate/"
                  "path ผิด · timeout = Gemini ช้า/โควตา · 413 = payload ใหญ่")
        return
    text = r.get("text", "")
    s.ok("ยิง N8N สำเร็จ", got="engine=%s  %d ตัวอักษร" % (r.get("engine"), len(text)))
    s.check("DIAGNOSE" in text.upper() and "12345" in text,
            "อ่านภาพทดสอบได้ถูกต้อง (เจอ 'DIAGNOSE 12345')",
            got=repr(text[:120]),
            fix="ต่อติดแต่เนื้อหาไม่ตรง = prompt สั่งให้ทำอย่างอื่น (แปล/สรุป) "
                "แทนการถอดความ — ดู docs/N8N_OCR_PROMPT.md")
    if r.get("warning"):
        s.info("คำเตือนจาก backend", str(r["warning"])[:120])
    blocks = r.get("blocks") or []
    s.info("blocks ที่ได้", "%d ก้อน%s" % (
        len(blocks), " (มี bbox → กรอบแดงใช้ได้)" if blocks else
        " — ไม่มี bbox: กรอบแดงจะพึ่ง Tesseract/text layer แทน"))


# ─────────────────────────────────────────────────────────────────────
SECTIONS = {"A": section_a, "B": section_b, "C": section_c,
            "D": section_d, "E": section_e, "F": section_f}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="ตรวจว่างานรอบ coverage/OCR/N8N/UX ครบและทำงานถูกต้องบนเครื่องนี้")
    ap.add_argument("--only", default="",
                    help="เลือกเฉพาะบางชั้น คั่นด้วย comma เช่น --only B,C")
    ap.add_argument("--n8n", action="store_true",
                    help="รวมชั้น F: ยิง N8N จริง (ต้องมี N8N รันอยู่)")
    ap.add_argument("--verbose", action="store_true",
                    help="โชว์ค่าที่วัดได้ทุกข้อ ไม่ใช่เฉพาะข้อที่ไม่ผ่าน")
    args = ap.parse_args()

    want = [c.strip().upper() for c in args.only.split(",") if c.strip()]
    if not want:
        want = ["A", "B", "C", "D", "E"] + (["F"] if args.n8n else [])
    elif args.n8n and "F" not in want:
        want.append("F")
    bad = [c for c in want if c not in SECTIONS]
    if bad:
        print("ชั้นที่ไม่รู้จัก: %s (มีให้เลือก: %s)" % (", ".join(bad),
                                                        ", ".join(sorted(SECTIONS))))
        return 2

    print("verify_artwork_features — ตรวจความครบถ้วนของงานรอบ Artwork")
    print("repo: %s" % ROOT)

    s = Suite(verbose=args.verbose)
    try:
        for c in want:
            SECTIONS[c](s)
    except ImportError as e:
        print("\n✗ import ไม่สำเร็จ: %s" % e)
        print("  เครื่องนี้ยังไม่มีแพ็กเกจที่จำเป็น — `py -3.9 -m pip install "
              "-r requirements.txt`")
        return 2

    rc = s.summary()
    if "F" not in want:
        print("\nยังไม่ได้ตรวจการต่อ N8N จริง — เพิ่ม --n8n (ต้องมี N8N รันอยู่)")
    print("ชั้นที่สคริปต์นี้ตรวจแทนไม่ได้: การลากโซนบนเบราว์เซอร์จริง · "
          "verify_ocr.py กับไฟล์ artwork จริง · โหมดที่ใช้กล้อง/SQL Server")
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
