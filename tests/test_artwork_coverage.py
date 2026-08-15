"""``checks.check_coverage`` — รายงานว่าชั้นตรวจไหน "ได้ทำงานจริง" กับงานใบนี้.

ทำไมต้องมี: ผล ✅ PASS ไม่ได้แปลว่าตรวจครบ. ชั้นเทียบข้ามแผง
(MISMATCH_PANELS) ทำงานเมื่อมีโซน ≥2 โซนที่ ``group`` ตรงกันและอ่านข้อความ
ออกทั้งคู่เท่านั้น — ผู้ใช้ที่ลากหลายโซนบนไฟล์เดียวได้กลุ่มอัตโนมัติคนละตัว
(A, B, C, …) ⇒ ชั้นนี้ไม่เคยทำงาน แต่รายงานยังขึ้น PASS เงียบ ๆ.

⚠️ **ห้ามแก้ด้วยการบังคับให้ทุกโซนอยู่กลุ่มเดียวกัน** — มีเทสต์
``test_forcing_one_group_creates_false_defects`` กันไว้ เพราะโซนคนละเนื้อหา
ที่ถูกจับรวมกลุ่มจะให้ defect ปลอมทันที (วัดจริง 6 รายการ).
"""

import itertools

import pytest

from artwork_check import checks


PANEL = "ALPHA BRAVO CHARLIE\nNET WEIGHT 170 G\nMADE IN THAILAND"
PANEL_DIFF = "ALPHA BRAVO CHARLIE\nNET WEIGHT 999 G\nMADE IN VIETNAM"
OTHER = "DISTRIBUTED BY THAI UNION\n979 RAMA III ROAD BANGKOK"


def mk(specs):
    """specs = [(group, text, type)] → (zones, ocr_results)"""
    zones, ocr = [], []
    for i, spec in enumerate(specs):
        g, txt = spec[0], spec[1]
        t = spec[2] if len(spec) > 2 else "panel"
        zid = "z%d" % (i + 1)
        zones.append({"id": zid, "type": t, "group": g, "doc": "a",
                      "bbox": [0.1, 0.1 * (i + 1), 0.3, 0.08]})
        ocr.append({"zone_id": zid, "text": txt, "engine": "pdf-text",
                    "conf": 1.0})
    return zones, ocr


def cov(specs):
    z, o = mk(specs)
    return checks.check_coverage(z, o)


# ── เคสหลัก: พฤติกรรมที่ผู้ใช้เจอจริง ──────────────────────────────

def test_auto_groups_means_cross_panel_never_runs():
    """ลาก 3 โซนบนไฟล์เดียว → กลุ่มอัตโนมัติ A/B/C → ชั้นนี้ไม่ทำงาน."""
    c = cov([("A", PANEL), ("B", PANEL), ("C", PANEL)])
    assert c["cross_panel"]["ran"] is False
    assert c["cross_panel"]["reason"] == "no_shared_group"


def test_same_group_makes_it_run():
    c = cov([("A", PANEL), ("A", PANEL), ("A", PANEL)])
    assert c["cross_panel"]["ran"] is True
    assert c["cross_panel"]["groups"] == ["A"]
    assert c["cross_panel"]["zones"] == 3


def test_single_zone_is_not_a_warning():
    """โซนเดียว = ไม่มีอะไรให้เทียบ — ต้องแยกจาก 'ลืมตั้งกลุ่ม'."""
    c = cov([("A", PANEL)])
    assert c["cross_panel"]["ran"] is False
    assert c["cross_panel"]["reason"] == "single_zone"


def test_grouped_but_unreadable():
    """ตั้งกลุ่มตรงกันแล้ว แต่โซนหนึ่งอ่านไม่ออก → ยังเทียบไม่ได้
    และเหตุผลต้องไม่ใช่ 'ไม่ได้ตั้งกลุ่ม' (คนละวิธีแก้)."""
    c = cov([("A", PANEL), ("A", "")])
    assert c["cross_panel"]["ran"] is False
    assert c["cross_panel"]["reason"] == "group_unreadable"
    assert c["cross_panel"]["groups"] == ["A"]


def test_empty_group_string_is_not_a_pair():
    """group ว่างสองโซนไม่ใช่ 'กลุ่มเดียวกัน' — ไม่งั้นโซนที่ยังไม่ตั้ง
    กลุ่มจะถูกจับคู่กันเองแล้วฟ้อง defect ปลอม."""
    c = cov([("", PANEL), ("", OTHER)])
    assert c["cross_panel"]["ran"] is False


def test_ignore_zones_do_not_count():
    c = cov([("A", PANEL), ("A", PANEL, "ignore")])
    assert c["cross_panel"]["ran"] is False
    assert c["zones_total"] == 1


def test_header_counts_as_panel_like_checks_do():
    """checks.check_group_consistency นับ type 'header' เป็น panel ด้วย —
    coverage ต้องนับเหมือนกัน ไม่งั้นรายงานจะขัดกับความจริง."""
    c = cov([("A", PANEL), ("A", PANEL, "header")])
    assert c["cross_panel"]["ran"] is True


# ── zoom ────────────────────────────────────────────────────────────

def test_zoom_runs_when_group_has_readable_panel():
    c = cov([("A", PANEL), ("A", "NET WEIGHT 170 G", "zoom")])
    assert c["zoom"]["ran"] is True
    assert c["zoom"]["zones"] == 1


def test_zoom_alone_does_not_run():
    c = cov([("A", "NET WEIGHT 170 G", "zoom")])
    assert c["zoom"]["ran"] is False
    assert c["zoom"]["reason"] == "no_panel_in_group"


def test_no_zoom_zone_is_benign():
    c = cov([("A", PANEL), ("B", PANEL)])
    assert c["zoom"]["ran"] is False
    assert c["zoom"]["reason"] == "no_zoom_zone"


# ── ชั้นที่ทำงานทุกโซน ──────────────────────────────────────────────

def test_numbers_layer_needs_readable_text():
    assert cov([("A", PANEL)])["numbers"]["ran"] is True
    assert cov([("A", "")])["numbers"]["ran"] is False


def test_spelling_reports_missing_library(monkeypatch):
    monkeypatch.setattr(checks, "spell_layer_available", lambda: False)
    c = cov([("A", PANEL)])
    assert c["spelling"]["ran"] is False
    assert c["spelling"]["reason"] == "spellchecker_missing"


def test_zone_counts():
    c = cov([("A", PANEL), ("B", ""), ("C", OTHER), ("D", PANEL, "ignore")])
    assert c["zones_total"] == 3          # ไม่นับ ignore
    assert c["zones_readable"] == 2


# ── กันการ "แก้ผิดทาง" ───────────────────────────────────────────────

def test_forcing_one_group_creates_false_defects():
    """เอกสารประกอบการตัดสินใจ: ถ้าใครคิดจะแก้ P0.1 ด้วยการให้ทุกโซนอยู่
    กลุ่มเดียวกันโดยอัตโนมัติ เทสต์นี้จะอธิบายว่าทำไมถึงห้าม."""
    zones, ocr = mk([("A", PANEL), ("A", OTHER), ("A", "BEST BEFORE 2027")])
    texts = {r["zone_id"]: r["text"] for r in ocr}
    defects = checks.check_group_consistency(zones, texts)
    assert len(defects) >= 4, (
        "โซนคนละเนื้อหาที่ถูกจับรวมกลุ่มเดียวต้องให้ defect ปลอมจำนวนมาก "
        "— นี่คือเหตุผลที่ default ต้องเป็นกลุ่มแยก")


# ── coverage ต้องไม่โกหก (ทิศที่อันตราย) ─────────────────────────────

@pytest.mark.parametrize("types", list(itertools.product(
    ("panel", "zoom", "header", "ignore"), repeat=2)))
@pytest.mark.parametrize("groups", [("A", "B"), ("A", "A"), ("", ""),
                                    ("A", "")])
def test_coverage_never_says_off_while_layer_fires(types, groups):
    """ถ้า coverage บอก 'ไม่ได้ทำงาน' แล้วชั้นนั้นยังฟ้อง defect ได้ =
    รายงานโกหกในทิศที่อันตรายที่สุด (คนเชื่อว่าไม่ได้ตรวจ ทั้งที่ตรวจแล้ว
    — หรือกลับกัน เห็นว่าตรวจแล้วทั้งที่ยังไม่ตรวจ)."""
    zones, ocr = mk([(groups[0], PANEL_DIFF, types[0]),
                     (groups[1], PANEL, types[1])])
    texts = {r["zone_id"]: r["text"] for r in ocr}
    c = checks.check_coverage(zones, ocr)
    d = checks.check_group_consistency(zones, texts)
    if any(x["class"] == "MISMATCH_PANELS" for x in d):
        assert c["cross_panel"]["ran"] is True
    if any(x["class"] == "MISMATCH_ZOOM" for x in d):
        assert c["zoom"]["ran"] is True


def test_coverage_is_json_safe():
    import json
    c = cov([("A", PANEL), ("A", PANEL, "zoom")])
    assert json.loads(json.dumps(c)) == c


def test_coverage_never_raises_on_odd_input():
    """report.json ต้องเขียนได้เสมอ — coverage เป็นแค่ advisory ห้ามทำให้
    การตรวจทั้งใบพัง."""
    assert checks.check_coverage([], []) is not None
    z = [{"id": "z1"}]                       # ไม่มี type/group/bbox
    assert checks.check_coverage(z, [{"zone_id": "z1"}]) is not None
    assert checks.check_coverage(z, []) is not None
