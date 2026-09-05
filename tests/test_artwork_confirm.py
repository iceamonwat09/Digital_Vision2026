# -*- coding: utf-8 -*-
"""โหมดทดลอง "อ่านซ้ำ 2 รอบ แล้วยืนยันผล" (ช่องติ๊ก default ปิด).

ข้อมูลใน ``tests/data/artwork_confirm/`` คือ **ผลจริงจากสถานี** 4 รอบ x 2 โซน
ของแผงโภชนาการ John West ที่ต่างกันจริงข้อเดียว: Sodium **20% (z1) vs 24% (b2)**

    รอบ  โหมดการอ่าน      defect ที่ระบบฟ้อง
     1   เสีย + ดี            7   (จริง 1 · ปลอม 6)
     2   เสีย + เสีย          1   (จริงล้วน)
     3   ดี   + ดี            1   (จริงล้วน)
     4   ดี   + เสีย          6   (จริง 1 · ปลอม 5)

โหมด "เสีย" ของ Gemini เพี้ยนแบบเดิมเป๊ะทุกครั้ง (٧→V · ١→ا · ٠→หาย ·
٤٧٥ ملجم→ملجم٤٧٥) ⇒ เมื่อสองฝั่งเพี้ยนพร้อมกัน ความเพี้ยนหักล้างกันและผล
เทียบยังถูก. **defect ปลอมเกิดจาก "เพี้ยนไม่พร้อมกัน" ล้วน ๆ**
"""
import os

import pytest

from artwork_check import checks, confirm

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "data", "artwork_confirm")
ZONES = [{"id": "z1", "type": "panel", "group": "A", "label": "z1"},
         {"id": "b2", "type": "panel", "group": "A", "label": "b2"}]
ROUNDS = (1, 2, 3, 4)
REAL = "24%"          # ความต่างจริงข้อเดียวบนแผ่น


def _read(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return f.read()


def defects_of(r):
    return checks.check_group_consistency(
        ZONES, {"z1": _read("r%d_z1.txt" % r), "b2": _read("r%d_b2.txt" % r)})


def founds(ds):
    return sorted(d.get("found", "") for d in ds)


# ── ข้อมูลจริงต้องยังเป็นอย่างที่วัดไว้ (กันไฟล์ fixture ถูกแก้เงียบ) ──

@pytest.mark.parametrize("r,n", [(1, 7), (2, 1), (3, 1), (4, 6)])
def test_the_recorded_rounds_still_produce_what_was_measured(r, n):
    assert len(defects_of(r)) == n


def test_both_sides_wrong_the_same_way_gives_the_right_answer():
    """หัวใจของหลักการ: ไม่ต้องอ่านแม่น ต้องอ่าน "เหมือนกัน".

    รอบ 2 อ่านเพี้ยนทั้งสองฝั่ง แต่เพี้ยนแบบเดียวกัน ⇒ ความเพี้ยนหักล้าง
    และเหลือเฉพาะความต่างจริง — เท่ากับรอบ 3 ที่อ่านถูกทั้งสองฝั่ง.
    """
    assert founds(defects_of(2)) == founds(defects_of(3)) == [REAL]


# ── ตัวกรอง ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("a,b", [(a, b) for i, a in enumerate(ROUNDS)
                                 for b in ROUNDS[i + 1:]])
def test_every_pair_of_real_reads_leaves_only_the_real_difference(a, b):
    """วัดครบทุกคู่ที่เป็นไปได้ (6 คู่) — ต้องเหลือ 24% ตัวเดียวทุกคู่."""
    ok, un = confirm.confirm([defects_of(a), defects_of(b)])
    assert founds(ok) == [REAL]
    assert len(ok) + len(un) >= len(defects_of(a))       # ไม่มีอะไรหายไปเฉย ๆ


def test_nothing_is_dropped_silently():
    """รายการที่ตกไปต้องอยู่ใน "ยังไม่ยืนยัน" ครบถ้วน — ไม่ใช่หายเงียบ."""
    d1, d4 = defects_of(1), defects_of(4)
    ok, un = confirm.confirm([d1, d4])
    keys = {confirm.defect_key(d) for d in ok} | {confirm.defect_key(d) for d in un}
    for d in d1 + d4:
        assert confirm.defect_key(d) in keys


def test_zone_must_be_part_of_the_key():
    """กับดักที่วัดเจอ: รอบ 1 กับรอบ 4 ให้ชุด "ข้อความ" defect เหมือนกันเป๊ะ
    แต่ **คนละโซน** (เพราะสลับกันว่าฝั่งไหนอ่านเพี้ยน).

    ถ้าคีย์ดูแค่ข้อความจะเหลือ 6 รายการ — ต้องดูโซนด้วยถึงจะเหลือ 1.
    """
    d1, d4 = defects_of(1), defects_of(4)
    by_text = {d.get("found", "") for d in d1} & {d.get("found", "") for d in d4}
    assert len(by_text) > 1                     # ถ้าดูแค่ข้อความ = กรองไม่ออก
    ok, _ = confirm.confirm([d1, d4])
    assert founds(ok) == [REAL]                 # ดูโซนด้วย = เหลือของจริง


def test_trailing_punctuation_does_not_break_the_match():
    """วัดจริง: `24%` รอบหนึ่งกลายเป็น `24% |` อีกรอบ — ถ้าไม่ตัดท้าย
    ความต่าง **จริง** จะถูกทิ้ง ซึ่งเป็น false negative ที่อันตรากว่า."""
    a = [{"class": "MISMATCH_PANELS", "zone_id": "b2", "found": "24%"}]
    b = [{"class": "MISMATCH_PANELS", "zone_id": "b2", "found": "24% |"}]
    ok, un = confirm.confirm([a, b])
    assert len(ok) == 1 and not un


def test_unreadable_is_reported_even_when_it_appears_once():
    """"อ่านไม่ได้" คือคำเตือนว่าระบบตรวจไม่ได้ — เอาไปตัดกันจะกลายเป็น
    การซ่อนความไม่รู้ ซึ่งตรงข้ามกับกฎเหล็กข้อ 2 พอดี ⇒ ใช้ union."""
    a = [{"class": "UNREADABLE", "zone_id": "z1", "found": ""}]
    b = []
    ok, un = confirm.confirm([a, b])
    assert len(ok) == 1 and ok[0]["class"] == "UNREADABLE"
    assert not un
    # โผล่เฉพาะรอบหลังก็ต้องรายงาน
    ok2, _ = confirm.confirm([[], a])
    assert len(ok2) == 1


def test_one_round_changes_nothing():
    """รอบเดียว = ไม่มีอะไรให้ยืนยัน ⇒ คืนผลเดิมครบ (ห้ามทิ้ง)."""
    d = defects_of(1)
    ok, un = confirm.confirm([d])
    assert ok == d and un == []


def test_confirmed_defects_are_the_same_objects_not_new_ones():
    """โหมดนี้ต้องเป็น **ตัวกรอง** ไม่ใช่ตัวสร้าง — ผลที่แสดงกับผู้ใช้จึง
    หน้าตาเหมือนเดิมทุกประการ (ข้อกำหนดของผู้ใช้)."""
    d1, d2 = defects_of(1), defects_of(2)
    ok, _ = confirm.confirm([d1, d2])
    for d in ok:
        assert any(d is x for x in d1), "defect ต้องเป็นออบเจ็กต์เดิมจากรอบแรก"


def test_summary_lists_what_was_dropped():
    ok, un = confirm.confirm([defects_of(1), defects_of(2)])
    s = confirm.summary(ok, un, 2)
    assert s["rounds"] == 2 and s["confirmed"] == len(ok)
    assert s["unconfirmed"] == len(un) == len(s["items"])
    assert all({"class", "zone_id", "found"} <= set(i) for i in s["items"])


# ── ต่อเข้า pipeline / route / หน้าเว็บ ──────────────────────────────

def test_default_is_off_everywhere():
    """กฎเหล็กข้อ 1: ไม่ติ๊ก = ทางเดิมเป๊ะ."""
    import inspect as _i
    from artwork_check import pipeline
    sig = _i.signature(pipeline.run_inspection)
    assert sig.parameters["confirm_reads"].default is False


def test_route_reads_the_flag_and_passes_it_on():
    import os as _os
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    with open(_os.path.join(root, "artwork_check", "routes.py"),
              encoding="utf-8") as f:
        src = f.read()
    assert 'body.get("confirm_reads")' in src
    assert "confirm_reads=confirm_reads" in src


def test_the_page_has_the_checkbox_and_sends_it():
    import os as _os
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    with open(_os.path.join(root, "templates", "artwork_check.html"),
              encoding="utf-8") as f:
        html = f.read()
    assert 'id="awConfirmReads"' in html
    with open(_os.path.join(root, "static", "js", "artwork_check.js"),
              encoding="utf-8") as f:
        js = f.read()
    assert "confirm_reads: confirmReadsOn()" in js
    # ต้องส่งเฉพาะปุ่มส่งตรวจสอบ — แท็บแปลไม่ตัดสิน defect จึงไม่เกี่ยว
    assert js.count("confirm_reads:") == 1
    # จำค่าไว้ในเซสชันเหมือนช่องติ๊กอื่น
    assert "confirmReads: confirmReadsOn()" in js
    assert '$("awConfirmReads").checked = !!s.confirmReads' in js


def test_the_dropped_items_are_shown_on_the_report():
    """การกรองที่มองไม่เห็น = การซ่อนข้อมูลจากผู้ตรวจ (กฎเหล็กข้อ 2)."""
    import os as _os
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    with open(_os.path.join(root, "static", "js", "artwork_check.js"),
              encoding="utf-8") as f:
        js = f.read()
    i = js.index("function confirmHtml(cf)")
    block = js[i:js.index("window.awConfirmHtml", i)]
    assert "cf.items" in block                      # ลงรายการที่ตกไป
    assert "ยังยืนยันไม่ได้" in block
    assert "html += confirmHtml(rep.confirm);" in js
