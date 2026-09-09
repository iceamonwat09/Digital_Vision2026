# -*- coding: utf-8 -*-
"""ตัวพิมพ์ใหญ่-เล็กไม่ตรงกัน — ชั้นที่สองที่ยิงในที่ที่เดิมเงียบสนิท

ที่มา (คู่ไฟล์จริงที่ผู้ใช้ส่งมา 9 ก.ย. 2026 — AvoDerm Master1/Master2):
ระบบฟ้อง 1 รายการ (``Copper Sulfate.`` vs ``Copper Sulphate Pentahydrate.``)
**แต่พลาด** ``D-Calcium Pantothenate`` vs ``D-calcium Pantothenate``

ต้นเหตุ: ทุกเส้นทางเทียบใน ``checks.py`` พับตัวพิมพ์ (``_norm_key`` และ
``_norm_flat`` ต่างก็ ``.upper()``) ⇒ ด่าน containment ในลูป ``extra`` ของ
``_vote_panels`` ยกโทษบรรทัดนี้ ⇒ ไม่เข้า ``extra`` เลย

🔬 ตัวพิมพ์ **ไม่ใช่ noise ของ OCR** (ต่างจากเครื่องหมายวรรคตอนซึ่งสองรอบ
   อ่านไม่ตรงกันเป็นประจำ) — วัด 3 ทางอิสระ รวม **1,243 คำร่วม ตัวพิมพ์ผิด 0**:
     Gemini ↔ Tesseract บนคู่ AvoDerm          237 คำ · ผิด 0
     PDF text layer (เฉลย) ↔ Tesseract 4 ไฟล์  1,006 คำ · ผิด 0
     Tesseract 300 dpi ↔ 400 dpi                497 คำ · ผิด 1 (คำจาก metadata)

⚠️ **ห้ามแก้ ``_norm_key`` ให้สนใจตัวพิมพ์ทั้งก้อน** — 16 จุดที่ใช้มันรวม
   ``check_phrases`` ซึ่งฉลากพิมพ์โลโก้เป็น ``AVODERM`` แต่คลังเก็บ ``AvoDerm``
"""
import pytest

from artwork_check import checks, config, report


# ── ข้อความจากคู่ไฟล์จริง (ตัดมาเฉพาะบรรทัดที่เกี่ยวข้อง) ─────────────
Z_ING = """INGREDIENTS: Chicken Meal, Brown Rice, Oatmeal
D-Calcium Pantothenate, Zinc Oxide, Niacin
Copper Sulfate.
Vitamin E Supplement, Biotin"""
B_ING = """INGREDIENTS: Chicken Meal, Brown Rice, Oatmeal
D-calcium Pantothenate, Zinc Oxide, Niacin
Copper Sulphate Pentahydrate.
Vitamin E Supplement, Biotin"""

AR = ("محتوى الطاقة: 520 كيلو كالوري / كغ\n"
      "المكونات: سمك البونيتو، سمك التونة (10%)، نشاء، توابل")
TH = "ส่วนประกอบ: ปลาทูน่า 60% น้ำตาล 2%\nน้ำหนักสุทธิ 185 กรัม"
EN = ("NET WEIGHT 185 g\n"
      "Distributed by ACME Co., Ltd.\n"
      "Keep in a cool dry place")


def _zones(**kw):
    return [{"id": "z1", "type": "panel", "group": "A", "doc": "a",
             "label": "Artwork"},
            {"id": "b2", "type": "panel", "group": "A", "doc": "b",
             "label": "ชิ้นงาน"}]


def _run(a, b):
    return checks.check_group_consistency(_zones(), {"z1": a, "b2": b})


def _of(defects, cls):
    return [d for d in defects if d["class"] == cls]


def _spans_text(text, spans):
    return [text[x:y] for x, y in (spans or [])]


# ── ① ค่าตั้งและตารางคลาส ────────────────────────────────────────────

def test_the_case_layer_is_on_by_default():
    """ผู้ใช้สั่งว่า "ถือว่าผิดเหมือนกัน ไม่ควรปล่อยผ่าน" """
    assert config.TEXT_CASE_SENSITIVE is True


def test_the_defect_class_exists_and_is_critical_by_default():
    cls = config.DEFECT_CLASSES["MISMATCH_CASE"]
    assert cls["severity"] == config.TEXT_CASE_SEVERITY == "critical"
    assert cls["label"]


def test_every_defect_class_has_a_colour_and_a_thai_label():
    """คลาสใหม่ต้องมีสีบน overlay และป้ายบนการ์ด ไม่งั้นขึ้นเป็นช่องว่าง"""
    from artwork_check.report import _CLASS_COLORS_BGR
    js = open("static/js/artwork_check.js", encoding="utf-8").read()
    for cls in config.DEFECT_CLASSES:
        assert cls in _CLASS_COLORS_BGR, cls
        assert cls + ":" in js, cls


def test_the_summary_counts_the_new_class():
    """``summarize`` ไล่จาก DEFECT_CLASSES ⇒ การ์ด KPI ได้มาเอง"""
    assert "MISMATCH_CASE" in report.summarize([])


# ── ② เคสจริงที่พลาด ─────────────────────────────────────────────────

def test_the_station_case_is_now_reported():
    got = _of(_run(Z_ING, B_ING), "MISMATCH_CASE")
    assert len(got) == 1
    assert got[0]["found"].startswith("D-Calcium")
    assert got[0]["reference"].startswith("D-calcium")


def test_the_existing_defect_is_untouched():
    """ชั้นนี้ *เพิ่ม* ในที่ที่เดิมเงียบ — ห้ามแตะรายการเดิมแม้แต่ใบเดียว"""
    got = _of(_run(Z_ING, B_ING), "MISMATCH_PANELS")
    assert len(got) == 1
    assert "Sulfate" in got[0]["found"]
    assert "Sulphate Pentahydrate" in got[0]["reference"]


def test_the_report_goes_from_one_defect_to_two():
    assert len(_run(Z_ING, B_ING)) == 2


def test_a_case_difference_fails_the_verdict():
    only_case = Z_ING.replace("Copper Sulfate.", "Copper Sulphate.")
    other = B_ING.replace("Copper Sulphate Pentahydrate.", "Copper Sulphate.")
    d = _run(only_case, other)
    assert [x["class"] for x in d] == ["MISMATCH_CASE"]
    assert report.compute_verdict(d) == "FAIL"


def test_the_defect_is_attributed_to_the_main_file():
    """``found`` ต้องเป็นฝั่งไฟล์หลัก (doc "a") — การ์ดวาดกรอบจากค่านี้"""
    d = _of(_run(Z_ING, B_ING), "MISMATCH_CASE")[0]
    assert d["zone_id"] == "z1"
    assert d["ref_zone_ids"] == ["b2"]


def test_the_main_file_is_found_even_when_listed_second():
    zones = list(reversed(_zones()))
    d = [x for x in checks.check_group_consistency(
        zones, {"z1": Z_ING, "b2": B_ING}) if x["class"] == "MISMATCH_CASE"]
    assert d[0]["zone_id"] == "z1"


# ── ③ ไฮไลต์ต้องชี้ที่คำ ไม่ใช่ทั้งบรรทัด ────────────────────────────

def test_the_highlight_points_at_the_word_and_nothing_else():
    d = _of(_run(Z_ING, B_ING), "MISMATCH_CASE")[0]
    assert _spans_text(d["found"], d["found_spans"]) == ["D-Calcium"]
    assert _spans_text(d["reference"], d["ref_spans"]) == ["D-calcium"]


def test_case_folded_spans_would_find_nothing():
    """ถ้า ``diff_spans`` ไม่มีโหมดสนใจตัวพิมพ์ ไฮไลต์แดงจะว่างเปล่า"""
    a, b = "D-Calcium Pantothenate", "D-calcium Pantothenate"
    assert checks.diff_spans(a, b) == ([], [])
    assert checks.diff_spans(a, b, case=True)[0] == [[0, 9]]


def test_found_and_reference_are_never_modified():
    """สองค่านี้ถูกใช้ค้นคำเพื่อวาดกรอบแดงบนภาพ crop"""
    d = _of(_run(Z_ING, B_ING), "MISMATCH_CASE")[0]
    assert d["found"] in Z_ING.splitlines()
    assert d["reference"] in B_ING.splitlines()


# ── ④ ต้องไม่ฟ้องผิด ────────────────────────────────────────────────

@pytest.mark.parametrize("text", [EN, AR, TH, Z_ING])
def test_identical_text_never_fires(text):
    assert _run(text, text) == []


@pytest.mark.parametrize("text,old,new", [
    (AR, "520", "510"),
    (TH, "185 กรัม", "186 กรัม"),
    (EN, "185 g", "186 g"),
])
def test_a_real_letter_difference_stays_a_panel_mismatch(text, old, new):
    """ความต่างจริงต้องยังเป็น MISMATCH_PANELS ไม่ใช่ถูกเปลี่ยนคลาส"""
    got = _run(text, text.replace(old, new))
    assert _of(got, "MISMATCH_PANELS")
    assert _of(got, "MISMATCH_CASE") == []


def test_punctuation_noise_is_still_forgiven():
    """สองรอบอ่านไม่ตรงกันเรื่องเครื่องหมายเป็นเรื่องปกติ — ห้ามฟ้อง"""
    assert _run(EN, EN.replace("Co., Ltd.", "Co. Ltd")) == []


def test_arabic_and_thai_are_untouched_by_construction():
    """สคริปต์ที่ไม่มีตัวพิมพ์ใหญ่-เล็ก ⇒ สองคีย์เท่ากันเสมอ"""
    for line in AR.splitlines() + TH.splitlines():
        assert checks._norm_key_cs(line) == checks._norm_key(line)


def test_the_case_key_only_differs_by_case():
    a, b = "D-Calcium Pantothenate", "D-calcium Pantothenate"
    assert checks._norm_key(a) == checks._norm_key(b)
    assert checks._norm_key_cs(a) != checks._norm_key_cs(b)
    assert checks._norm_key_cs(a).upper() == checks._norm_key(a)


def test_the_aggressive_key_is_unchanged_by_the_refactor():
    """``_norm_key`` ถูกใช้ 16 จุด — ผลต้องเท่าเดิมทุกตัวอักษร"""
    for s in ["EL - OBOUR – BLOCK", "٧٠٪", "المكونات :", "Straße",
              "粗蛋白質 Cude Protein 8.0%以上", "D-Calcium", "x × X"]:
        assert checks._norm_key(s) == checks._norm_key_cs(s).upper()


# ── ⑤ ไม่รายงานซ้ำกับชั้นหลัก ───────────────────────────────────────

def test_a_line_already_flagged_is_not_reported_twice():
    """บรรทัดที่ชั้นโหวตฟ้องไปแล้ว ต้องไม่โผล่ซ้ำเป็น MISMATCH_CASE"""
    a = "ALPHA BETA GAMMA\nSHARED LINE ONE"
    b = "Alpha Beta Gamma Delta\nSHARED LINE ONE"
    got = _run(a, b)
    keys = {(d["zone_id"], checks._norm_key(d.get("found") or ""))
            for d in got}
    assert len(keys) == len([d for d in got if d.get("found")])


def test_a_case_difference_inside_one_zone_is_not_a_panel_mismatch():
    """ต่างกันเองในโซนเดียว ไม่ใช่ "ระหว่าง panel" — ไม่ฟ้อง"""
    a = "SHARED\nD-Calcium X\nD-calcium X"
    assert _of(_run(a, a), "MISMATCH_CASE") == []


def test_only_one_defect_per_differing_line():
    a = "D-Calcium X\nSODIUM Y\nSHARED Z"
    b = "D-calcium X\nSodium Y\nSHARED Z"
    assert len(_of(_run(a, b), "MISMATCH_CASE")) == 2


# ── ⑥ ปิดแล้วได้พฤติกรรมเดิม 100% ───────────────────────────────────

def test_turning_the_flag_off_restores_the_old_behaviour(monkeypatch):
    monkeypatch.setattr(config, "TEXT_CASE_SENSITIVE", False)
    got = _run(Z_ING, B_ING)
    assert [d["class"] for d in got] == ["MISMATCH_PANELS"]


def test_the_severity_can_be_lowered_to_review(monkeypatch):
    """ผู้ใช้เปลี่ยนใจได้โดยไม่ต้องแก้โค้ด"""
    monkeypatch.setitem(config.DEFECT_CLASSES["MISMATCH_CASE"],
                        "severity", "warning")
    d = _of(_run(Z_ING, B_ING), "MISMATCH_CASE")[0]
    assert d["severity"] == "warning"


def test_a_single_panel_group_never_fires():
    zones = [{"id": "z1", "type": "panel", "group": "A", "doc": "a"}]
    assert checks.check_group_consistency(zones, {"z1": Z_ING}) == []


# ── ⑦ ด่านกันรายงานซ้ำ (เกิดขึ้นจริงเมื่อ panel เยอะพอ) ─────────────

_MANY = ["z1", "b2", "c3", "d4", "e5", "f6"]
_MANY_TEXT = {"z1": "D-Calcium X\nSHARED", "b2": "D-calcium X\nSHARED",
              "c3": "D-calcium X\nSHARED", "d4": "SHARED",
              "e5": "SHARED", "f6": "SHARED"}


def _many():
    zones = [{"id": i, "type": "panel", "group": "A", "doc": "a"}
             for i in _MANY]
    return checks.check_group_consistency(zones, _MANY_TEXT)


def test_with_enough_panels_the_main_layer_reports_the_line_itself():
    """เสียงข้างมากไม่ยกโทษแล้ว (hits+1 = 3 < majority 4) ⇒ เข้า extra จริง"""
    assert len(_of(_many(), "MISMATCH_PANELS")) == 3


def test_a_line_the_main_layer_already_reported_is_not_reported_twice():
    """กติกา "ไม่แตะ defect เดิม" — ห้ามมีทั้ง MISMATCH_PANELS และ
    MISMATCH_CASE ของบรรทัดเดียวกัน"""
    assert _of(_many(), "MISMATCH_CASE") == []


def test_the_layer_still_fires_on_a_line_the_main_layer_stayed_silent_on():
    """บรรทัดอื่นในกลุ่มเดียวกันที่ถูกยกโทษ ยังต้องถูกฟ้องตามปกติ"""
    txt = dict(_MANY_TEXT)
    for zid in ("b2", "c3", "d4", "e5", "f6"):
        txt[zid] = txt[zid].replace("SHARED", "Shared")
    zones = [{"id": i, "type": "panel", "group": "A", "doc": "a"}
             for i in _MANY]
    got = checks.check_group_consistency(zones, txt)
    case = _of(got, "MISMATCH_CASE")
    assert [d["found"] for d in case] == ["SHARED"]


# ── ⑧ ชี้เฉพาะโซนที่ต่างจริง และรูปแบบที่ต่างจริง ───────────────────

def test_only_the_disagreeing_zone_is_listed_as_reference():
    """โซนที่ตัวพิมพ์ตรงกับฝั่งที่ถูกตรวจ ต้องไม่ถูกอ้างว่าเป็นฝั่งที่ต่าง
    — ไม่งั้นผู้ตรวจไปเปิดโซนที่ถูกต้องแล้วหาไม่เจอ"""
    zones = [{"id": "z1", "type": "panel", "group": "A", "doc": "a"},
             {"id": "b2", "type": "panel", "group": "A", "doc": "b"},
             {"id": "c3", "type": "panel", "group": "A", "doc": "b"}]
    got = checks.check_group_consistency(zones, {
        "z1": "SODIUM X\nSHARED", "b2": "Sodium X\nSHARED",
        "c3": "SODIUM X\nSHARED"})
    case = _of(got, "MISMATCH_CASE")
    assert len(case) == 1
    assert case[0]["ref_zone_ids"] == ["b2"]


def test_an_extra_case_variant_inside_one_zone_is_pointed_at():
    """โซนหนึ่งพิมพ์บรรทัดเดียวกันสองรูปแบบ อีกโซนมีรูปเดียว ⇒ ต้องชี้ที่
    **รูปแบบที่มีเฉพาะฝั่งนี้** ไม่ใช่รูปแบบที่ทั้งสองฝั่งมีเหมือนกัน"""
    got = _run("SODIUM X\nSodium X\nSHARED", "SODIUM X\nSHARED")
    case = _of(got, "MISMATCH_CASE")
    assert len(case) == 1
    assert case[0]["found"] == "Sodium X"
    assert case[0]["reference"] == "SODIUM X"


def test_an_extra_case_variant_on_the_reference_side_is_pointed_at():
    """ภาพสะท้อนของเทสต์ก่อนหน้า — ฝั่งอ้างอิงเป็นตัวที่มีรูปแบบเกิน"""
    got = _run("SODIUM X\nSHARED", "SODIUM X\nSodium X\nSHARED")
    case = _of(got, "MISMATCH_CASE")
    assert len(case) == 1
    assert case[0]["found"] == "SODIUM X"
    assert case[0]["reference"] == "Sodium X"
