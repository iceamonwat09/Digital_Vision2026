# -*- coding: utf-8 -*-
"""กรอบแดง 2 เรื่องของ 18 ก.ย.

**C — บรรทัดที่ "ไหลข้ามแถว"** (``highlight._run_matches``)
    ``_phrase_matches`` ต้องหาคำของวลีให้ครบ *ติดกันในแถวเดียวกัน* แต่
    backend อย่าง Gemini คืนย่อหน้าที่จัดหน้าใหม่แล้วมาเป็น "หนึ่งบรรทัด"
    ขณะที่ Tesseract อ่านตามแถวจริงบนภาพ ⇒ วลีเดียวกันถูกหั่นเป็นหลายแถว
    ⇒ **0 กรอบเสมอ** ไม่ว่าอ่านถูกแค่ไหน. ชั้นใหม่ทำงาน *หลัง* ทางเดิม
    ล้มเหลวเท่านั้น จึงแตะเคสที่วันนี้สำเร็จไม่ได้เชิงโครงสร้าง

**B — บอกเหตุผลเมื่อไม่มีกรอบ** (``pipeline._tag_highlight_why``)
    ตอบเฉพาะเหตุผลที่รู้ได้ตอนส่งตรวจโดยไม่ต้องอ่านภาพ และผู้ใช้แก้ได้เอง
    (ไม่มี Tesseract / ไม่มี traineddata ของสคริปต์นั้น) — เหตุผลที่รู้ได้
    เฉพาะตอนอ่านภาพ **ไม่เดา**
"""

import pytest

from artwork_check import config, highlight as hl, pipeline


JS = open("static/js/artwork_check.js", encoding="utf-8").read()


def _words(line, per_row=99, x0=0, y0=0):
    """วางคำเป็นกล่อง (คีย์, box) — ``per_row`` = จำนวนคำต่อแถวของแผง."""
    out, x, y = [], x0, y0
    for i, w in enumerate(line.split()):
        if i and i % per_row == 0:
            x, y = x0, y + 30
        wd = 12 * len(w) + 8
        out.append((hl._norm(w), (x, y, x + wd, y + 22)))
        x += wd + 10
    return out


def _rows(words):
    return {b[1] for _, b in words}


ING = ("D-Calcium Pantothenate, Thiamine Mononitrate, Beta-carotene, "
       "Biotin, Riboflavin")
AR = ("محتوى الطاقة 520 كيلو كالوري لكل 100 غرام من المنتج الجاف")


# ── C ① ได้กรอบหนึ่งใบต่อแถวที่วลีไหลไป ───────────────────────────────

def _boxable_rows(words, min_words=2):
    """แถวที่มีคำของวลี ≥ ``min_words`` คำ — แถวที่มีคำเดียวไม่ผ่านด่าน."""
    per = {}
    for _, b in words:
        per[b[1]] = per.get(b[1], 0) + 1
    return [y for y, n in per.items() if n >= min_words]


@pytest.mark.parametrize("per_row", [2, 3, 4])
def test_a_wrapped_phrase_gets_one_box_per_row(per_row):
    ws = _words(ING, per_row)
    assert len(_rows(ws)) > 1, "fixture ต้องไหลจริง"
    assert len(hl._match_boxes(ws, ING)) == len(_boxable_rows(ws))


def test_a_row_holding_only_one_word_of_the_phrase_gets_no_box():
    """ข้อจำกัดที่ยอมรับ **โดยตั้งใจ**: แถวที่เหลือคำเดียวไม่ผ่านด่าน
    ≥2 คำ ⇒ ไม่มีกรอบ. ผ่อนด่านนี้ = เปิดทางให้คำเปล่าที่บังเอิญตรง
    (เช่น "Calcium" ในตารางโภชนาการ) ถูกวาด ซึ่งอันตรายกว่า."""
    ws = _words(ING, 3)
    tail = [b for _, b in ws][-1]
    assert len(_boxable_rows(ws)) < len(_rows(ws)), "fixture ต้องมีแถวคำเดียว"
    boxes = hl._match_boxes(ws, ING)
    assert all(b[1] != tail[1] for b in boxes)


def test_a_wrapped_phrase_in_arabic_works_the_same():
    """ชั้นนี้เป็นเรขาคณิต + สตริงล้วน ⇒ ไม่ผูกกับภาษาใดภาษาหนึ่ง."""
    ws = _words(AR, 3)
    assert len(_rows(ws)) > 1
    assert len(hl._match_boxes(ws, AR)) == len(_rows(ws))


def test_each_box_covers_only_words_of_that_phrase():
    """กรอบต้องล้อมเฉพาะคำของวลีในแถวนั้น ไม่กินคำข้างเคียงที่ไม่เกี่ยว."""
    ws = _words(ING, 3)
    extra = ("CALCIUM", (2000, 0, 2100, 22))      # คำอื่นบนแถวแรก ไกลออกไป
    boxes = hl._match_boxes(ws + [extra], ING)
    assert boxes and all(b[2] < 2000 for b in boxes)


# ── C ② ด่านกันชี้ผิด ─────────────────────────────────────────────────

def test_a_phrase_that_is_not_on_the_panel_gets_no_box():
    """แผงที่ใช้ *คำร่วมกัน* แต่ไม่มีวลีนี้ ⇒ ต้องไม่ฟ้องผิด."""
    other = ("Sodium Selenite, Thiamine Hydrochloride, Calcium Iodate, "
             "Biotin, Menadione")
    assert hl._match_boxes(_words(other, 3), ING) == []


def test_a_single_word_of_the_phrase_is_not_enough():
    """คำเดียวโดด ๆ ที่บังเอิญเป็นส่วนหนึ่งของวลี (เช่น "Calcium" ในตาราง
    โภชนาการ) ต้องไม่ถูกวาด — ไม่งั้นคือการชี้ผิดจุดแบบมั่นใจ."""
    ws = [("CALCIUM", (0, 0, 90, 22)), ("MIN", (100, 0, 140, 22)),
          ("BIOTIN", (0, 30, 80, 52)), ("MAX", (90, 30, 130, 52))]
    assert hl._match_boxes(ws, ING) == []


def test_a_short_phrase_is_never_run_matched():
    """วลีสั้นกว่าเกณฑ์ตัวอักษร ⇒ ไม่เข้าชั้นนี้เลย (เฉพาะเจาะจงไม่พอ)."""
    ws = [("AB", (0, 0, 30, 22)), ("CD", (40, 0, 70, 22))]
    assert hl._run_matches(ws, ["AB", "CD"]) == []


def test_words_must_be_adjacent_in_the_phrase_order():
    """คำครบแต่ *เรียงคนละลำดับ* = ไม่ใช่ท่อนของวลี ⇒ ไม่วาด."""
    scrambled = "Riboflavin Biotin Mononitrate Thiamine Pantothenate"
    assert hl._match_boxes(_words(scrambled, 2), ING) == []


def test_the_same_run_on_two_rows_is_ambiguous_and_drawn_nowhere():
    """ท่อนเดียวกันโผล่คนละแถว = ชี้ไม่ได้ว่าอันไหน ⇒ ตัดทิ้งทั้งคู่."""
    run = [("THIAMINE", (0, 0, 100, 22)), ("MONONITRATE", (110, 0, 240, 22))]
    dup = [("THIAMINE", (0, 60, 100, 82)), ("MONONITRATE", (110, 60, 240, 82))]
    out = hl._run_matches(run + dup, ["THIAMINE", "MONONITRATE"])
    assert out == []


def test_a_paragraph_that_wraps_past_the_cap_draws_nothing():
    """ไหลยาวเกินกว่าจะวาดได้ครบ = ย่อหน้า. วาดบางแถวแล้วตัดที่เหลือทิ้ง
    เท่ากับบอกว่าแถวที่เหลือไม่เกี่ยว ⇒ ไม่วาดเลยดีกว่า."""
    para = " ".join("word%02d" % i for i in range(40))
    ws = _words(para, 4)
    assert len(_rows(ws)) > hl._RUN_MAX
    assert hl._match_boxes(ws, para) == []


# ── C ③ ไม่แตะทางเดิม ────────────────────────────────────────────────

def test_a_non_wrapping_panel_never_reaches_the_new_layer(monkeypatch):
    """โครงสร้าง: ทางเดิมสำเร็จ ⇒ ชั้นใหม่ต้องไม่ถูกเรียกเลย."""
    def boom(*a, **k):
        raise AssertionError("ชั้น wrap-runs ต้องไม่ถูกเรียกเมื่อทางเดิมสำเร็จ")
    monkeypatch.setattr(hl, "_run_matches", boom)
    assert len(hl._match_boxes(_words(ING, 99), ING)) == 1


def test_a_single_word_target_never_reaches_the_new_layer(monkeypatch):
    monkeypatch.setattr(hl, "_run_matches",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    assert len(hl._match_boxes(_words(ING, 3), "Riboflavin")) == 1


@pytest.mark.parametrize("per_row", [2, 3, 4])
def test_the_flag_off_restores_the_old_behaviour_exactly(per_row, monkeypatch):
    monkeypatch.setattr(config, "HIGHLIGHT_WRAP_RUNS", False)
    assert hl._match_boxes(_words(ING, per_row), ING) == []


def test_the_min_words_gate_is_configurable(monkeypatch):
    """ดันเกณฑ์ขึ้นแล้วชั้นนี้ต้องเงียบ — พิสูจน์ว่าเกณฑ์ถูกใช้จริง."""
    monkeypatch.setattr(config, "HIGHLIGHT_RUN_MIN_WORDS", 99)
    assert hl._match_boxes(_words(ING, 3), ING) == []


def test_the_min_chars_gate_is_configurable(monkeypatch):
    monkeypatch.setattr(config, "HIGHLIGHT_RUN_MIN_CHARS", 9999)
    assert hl._match_boxes(_words(ING, 3), ING) == []


def test_the_pdf_text_layer_gets_the_same_fix():
    """``match_word_boxes`` (กรอบคำจาก text layer) ใช้ตัวจับคู่ตัวเดียวกัน."""
    pdf_words = [("D-Calcium", (0.0, 0.0, 0.3, 0.1)),
                 ("Pantothenate,", (0.31, 0.0, 0.7, 0.1)),
                 ("Thiamine", (0.0, 0.2, 0.3, 0.3)),
                 ("Mononitrate,", (0.31, 0.2, 0.7, 0.3))]
    got = hl.match_word_boxes(
        pdf_words, "D-Calcium Pantothenate, Thiamine Mononitrate,")
    assert len(got) == 2


# ── B ① เหตุผลที่รู้ได้ตอนส่งตรวจ ─────────────────────────────────────

def _tag(defects, engine="n8n", **patch):
    zones = [{"id": "z1", "type": "panel"}]
    ocr = [{"zone_id": "z1", "engine": engine}]
    pipeline._tag_highlight_why(defects, zones, ocr)
    return defects


def test_a_missing_tesseract_is_reported(monkeypatch):
    monkeypatch.setattr(hl, "_tesseract_available", lambda: False)
    d = _tag([{"zone_id": "z1", "found": "Manufacturing"}])[0]
    assert d["hl_why"] == "no_tesseract"


def test_a_missing_traineddata_is_reported_with_its_code(monkeypatch):
    monkeypatch.setattr(hl, "_tesseract_available", lambda: True)
    monkeypatch.setattr(hl, "missing_langs", lambda w: ["ara"])
    d = _tag([{"zone_id": "z1", "found": "صوديوم"}])[0]
    assert d["hl_why"] == "lang:ara"


def test_nothing_is_reported_when_the_language_is_installed(monkeypatch):
    monkeypatch.setattr(hl, "_tesseract_available", lambda: True)
    monkeypatch.setattr(hl, "missing_langs", lambda w: [])
    d = _tag([{"zone_id": "z1", "found": "Manufacturing"}])[0]
    assert "hl_why" not in d


def test_a_pdf_text_zone_is_never_blamed_on_tesseract(monkeypatch):
    """โซนที่อ่านจาก text layer ใช้กรอบคำของ PDF ได้ทุกสคริปต์โดยไม่ต้อง
    พึ่ง Tesseract ⇒ ไม่มีอะไรต้องเตือน."""
    monkeypatch.setattr(hl, "_tesseract_available", lambda: False)
    d = _tag([{"zone_id": "z1", "found": "صوديوم"}], engine="pdf-text")[0]
    assert "hl_why" not in d


def test_a_defect_without_found_text_gets_no_reason(monkeypatch):
    monkeypatch.setattr(hl, "_tesseract_available", lambda: False)
    d = _tag([{"zone_id": "z1", "found": ""}])[0]
    assert "hl_why" not in d


def test_a_defect_that_already_has_a_measured_box_gets_no_reason(monkeypatch):
    """กรอบที่ "วัดมา" จากโหมดพิกเซลไม่ต้องค้นคำเลย ⇒ มีกรอบอยู่แล้ว."""
    monkeypatch.setattr(hl, "_tesseract_available", lambda: False)
    d = _tag([{"zone_id": "z1", "found": "x" * 5,
               "pixel_bbox": [1, 2, 3, 4]}])[0]
    assert "hl_why" not in d


def test_a_stale_reason_from_an_earlier_run_is_cleared(monkeypatch):
    monkeypatch.setattr(hl, "_tesseract_available", lambda: True)
    monkeypatch.setattr(hl, "missing_langs", lambda w: [])
    d = _tag([{"zone_id": "z1", "found": "ok", "hl_why": "no_tesseract"}])[0]
    assert "hl_why" not in d


@pytest.mark.parametrize("flag", ["HIGHLIGHT_WHY", "HIGHLIGHT_DEFECT_WORD",
                                  "HIGHLIGHT_USE_TESSERACT"])
def test_turning_any_guard_off_removes_the_key(flag, monkeypatch):
    monkeypatch.setattr(config, flag, False)
    monkeypatch.setattr(hl, "_tesseract_available", lambda: False)
    d = _tag([{"zone_id": "z1", "found": "Manufacturing",
               "hl_why": "stale"}])[0]
    assert "hl_why" not in d


def test_tagging_never_touches_the_qc_payload(monkeypatch):
    monkeypatch.setattr(hl, "_tesseract_available", lambda: False)
    d = {"zone_id": "z1", "found": "Manufacturing", "reference": "Manufactur",
         "class": "MISMATCH_PANELS", "severity": "critical",
         "message": "ไม่ตรงกัน", "found_spans": [[0, 5]]}
    before = dict(d)
    _tag([d])
    for k, v in before.items():
        assert d[k] == v


# ── B ② "ตอบไม่ได้" ต้องไม่กลายเป็น "ไม่มี" ──────────────────────────

def test_missing_langs_is_silent_when_it_cannot_ask(monkeypatch):
    monkeypatch.setattr(hl, "installed_langs", lambda: set())
    assert hl.missing_langs("صوديوم") == []


def test_missing_langs_reports_a_script_without_traineddata(monkeypatch):
    monkeypatch.setattr(hl, "installed_langs", lambda: {"eng"})
    assert hl.missing_langs("صوديوم") == ["ara"]
    assert hl.missing_langs("Manufacturing") == []


def test_missing_langs_accepts_either_chinese_variant(monkeypatch):
    monkeypatch.setattr(hl, "installed_langs", lambda: {"eng", "chi_sim"})
    assert hl.missing_langs("碳水化合物") == []
    monkeypatch.setattr(hl, "installed_langs", lambda: {"eng"})
    assert hl.missing_langs("碳水化合物") == ["chi_tra+chi_sim"]


def test_latin_needs_english(monkeypatch):
    monkeypatch.setattr(hl, "installed_langs", lambda: {"ara"})
    assert hl.missing_langs("Manufacturing") == ["eng"]


def test_installed_langs_never_caches_a_failure(monkeypatch):
    """แคชความล้มเหลว = ทุกการเรียกหลังจากนั้นเชื่อค่าที่ผิดตลอดอายุโปรเซส
    (เจอจริงตอนทำ: ``_resolve_langs`` เลิกกรองภาษาที่ไม่ได้ติดตั้ง)."""
    monkeypatch.setattr(hl, "_avail_cache", None)
    calls = {"n": 0}

    class _Boom:
        @staticmethod
        def get_languages(config=""):
            calls["n"] += 1
            raise RuntimeError("no tesseract")

    monkeypatch.setitem(__import__("sys").modules, "pytesseract", _Boom)
    assert hl.installed_langs() == set()
    assert hl.installed_langs() == set()
    assert calls["n"] == 2, "ต้องถามใหม่ทุกครั้งจนกว่าจะได้คำตอบจริง"


# ── B ③ script_langs ต้องไม่เปลี่ยนจากการแยกโค้ดส่วนร่วมออกมา ─────────

@pytest.mark.parametrize("word,expect", [
    ("", "eng"), (None, "eng"), ("Manufacturing", "eng"), ("475", "eng"),
    ("صوديوم", "ara"), ("٤٧٥ ملجم", "ara"), ("صوديوم Sodium", "ara+eng"),
    ("โปรตีน", "tha"), ("碳水化合物", "chi_tra+chi_sim"), ("タンパク質", "jpn"),
    ("Белки", "rus"),
])
def test_script_langs_is_unchanged_by_the_refactor(word, expect):
    assert hl.script_langs(word) == expect


# ── B ④ ฝั่งหน้าเว็บ ──────────────────────────────────────────────────

#: ค่าที่ ``pipeline._tag_highlight_why`` ผลิตได้ ↔ สาขาที่ JS ต้องมีจริง.
#: ⚠️ ต้องเทียบ **ทั้งบรรทัดเงื่อนไข** ไม่ใช่แค่ว่ามีสตริงนั้นอยู่ในไฟล์ —
#: เทสต์ที่เช็คแค่สตริงจะยังเขียวอยู่ถ้ามีใครปิดสาขาด้วย ``if (false &&``
_JS_BRANCHES = [
    'if (why === "no_tesseract") {',
    'if (typeof why === "string" && why.indexOf("lang:") === 0) {',
    'if (typeof why === "string" && why.indexOf("langcfg:") === 0) {',
]


@pytest.mark.parametrize("branch", _JS_BRANCHES)
def test_the_browser_has_a_live_branch_for_every_server_value(branch):
    body = JS[JS.index("function hlWhyText(why)"):
              JS.index("window.awHlWhyText")]
    assert branch in body


def test_an_unknown_reason_renders_nothing():
    """ค่าที่ไม่รู้จักต้องคืนสตริงว่าง (ไม่เดา) แล้วถอยไปใช้ hl_risk."""
    body = JS[JS.index("function hlWhyText(why)"):
              JS.index("window.awHlWhyText")]
    assert 'return "";' in body


def test_the_card_prefers_the_server_reason_over_the_zone_hint():
    """``hl_why`` เป็นข้อจำกัดของเครื่อง — ลากโซนใหม่ไม่ช่วย ⇒ ต้องไม่ขึ้น
    คำแนะนำ "ลากโซนให้กระชับ" ของ ``hl_risk`` ทับ."""
    i = JS.index("const hlWhy = d.hl_why")
    seg = JS[i:i + 400]
    assert "if (hlWhy)" in seg
    assert "} else if (z && z.hl_risk" in seg


def test_the_reason_never_claims_the_verdict_changed():
    body = JS[JS.index("function hlWhyText(why)"):
              JS.index("window.awHlWhyText")]
    assert "PASS/FAIL ไม่ได้รับผลกระทบ" in body


# ── C ④ ปลายทางจริง: เรนเดอร์ PDF + Tesseract ตัวจริง ────────────────

def _tess_or_skip():
    if not hl._tesseract_available():
        pytest.skip("ไม่มี tesseract ในเครื่องนี้")


def _render_panel(tmp_path, body, cols, pw=200, ph=220, fs=7.2):
    """แผงที่บรรทัดยาวถูก "ไหล" ข้ามแถวตามความกว้างคอลัมน์ที่กำหนด."""
    fitz = pytest.importorskip("fitz")
    import cv2
    import numpy as np
    rows = ["INGREDIENTS Chicken Broth, Chicken, Carrots, Green Beans,",
            "Calcium (min) 0.20%   Phosphorus (min) 0.16%"]
    w = body.split()
    while w:
        take, n = [], 0
        while w and n + len(w[0]) + 1 <= cols:
            n += len(w[0]) + 1
            take.append(w.pop(0))
        rows.append(" ".join(take))
    rows += ["Supplement, Vitamin B12 Supplement, Pyridoxine"]
    doc = fitz.open()
    pg = doc.new_page(width=pw, height=ph)
    for i, t in enumerate(rows):
        pg.insert_text((14, 22 + i * fs * 1.8), t, fontsize=fs, fontname="helv")
    p = str(tmp_path / "panel.pdf")
    doc.save(p)
    doc.close()
    page = fitz.open(p)[0]
    pm = page.get_pixmap(dpi=400)
    img = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width,
                                                      pm.n)
    return cv2.cvtColor(img[:, :, :3], cv2.COLOR_RGB2BGR)


def test_end_to_end_a_wrapped_line_finally_gets_boxes(tmp_path, monkeypatch):
    """เคสจริงที่ผู้ใช้เจอ: แผงคอลัมน์แคบ ⇒ วลีไหล 3 แถว ⇒ เดิม 0 กรอบ."""
    _tess_or_skip()
    crop = _render_panel(tmp_path, ING, cols=28)
    monkeypatch.setattr(config, "HIGHLIGHT_WRAP_RUNS", False)
    hl._WORDS_CACHE.clear()
    before = hl._tess_boxes(crop, ING, "eng", "", limit=0)
    monkeypatch.setattr(config, "HIGHLIGHT_WRAP_RUNS", True)
    hl._WORDS_CACHE.clear()
    after = hl._tess_boxes(crop, ING, "eng", "", limit=0)
    assert before == []
    assert len(after) >= 2


def test_end_to_end_a_panel_that_does_not_wrap_is_unchanged(tmp_path,
                                                            monkeypatch):
    _tess_or_skip()
    crop = _render_panel(tmp_path, ING, cols=999, pw=460, ph=200)
    monkeypatch.setattr(config, "HIGHLIGHT_WRAP_RUNS", False)
    hl._WORDS_CACHE.clear()
    before = hl._tess_boxes(crop, ING, "eng", "", limit=0)
    monkeypatch.setattr(config, "HIGHLIGHT_WRAP_RUNS", True)
    hl._WORDS_CACHE.clear()
    after = hl._tess_boxes(crop, ING, "eng", "", limit=0)
    assert before and before == after


def test_end_to_end_a_phrase_absent_from_the_panel_stays_silent(tmp_path):
    _tess_or_skip()
    other = ("Sodium Selenite, Thiamine Hydrochloride, Calcium Iodate, "
             "Biotin, Menadione")
    crop = _render_panel(tmp_path, other, cols=28)
    hl._WORDS_CACHE.clear()
    assert hl._tess_boxes(crop, ING, "eng", "", limit=0) == []


# ── B ⑤ ติดตั้งแล้ว แต่ค่าตั้งตรึงภาษาอื่นไว้ (คนละทางแก้) ───────────

def test_a_pinned_language_setting_is_reported_separately(monkeypatch):
    """สถานีที่ตั้ง ``eng+ara`` ไว้ พอเจอฉลากไทยจะไม่มีกรอบเลย — ติดตั้งครบ
    แต่ค่าตั้งไม่ส่งภาษานั้นไป ⇒ แก้ค่าตั้ง ไม่ใช่ไปติดตั้งเพิ่ม."""
    monkeypatch.setattr(hl, "installed_langs", lambda: {"eng", "ara", "tha"})
    monkeypatch.setattr(hl, "_lang_cache", {})
    assert hl.unused_langs("โปรตีน", "eng+ara") == ["tha"]
    assert hl.unused_langs("صوديوم", "eng+ara") == []


def test_auto_is_never_reported_as_a_pinned_setting(monkeypatch):
    """``auto`` เลือกภาษาตามสคริปต์อยู่แล้ว ⇒ ไม่มีวันเป็นปัญหานี้ และต้อง
    **ออกตั้งแต่ต้น** ไม่ต้องไปเรียก ``_resolve_langs`` (ซึ่งเป็นงานจริง)
    — ค่าปริยายของระบบคือ auto จึงเป็นเส้นทางที่เดินบ่อยที่สุด."""
    monkeypatch.setattr(hl, "installed_langs", lambda: {"eng", "ara", "tha"})
    monkeypatch.setattr(hl, "_resolve_langs",
                        lambda *a, **k: pytest.fail("auto ต้องไม่ resolve"))
    assert hl.unused_langs("โปรตีน", "auto") == []
    assert hl.unused_langs("โปรตีน", " AUTO ") == []


def test_a_language_that_is_not_installed_is_not_double_reported(monkeypatch):
    """ไม่มี tha เลย ⇒ เป็นเรื่องของ missing_langs ไม่ใช่ของค่าตั้ง."""
    monkeypatch.setattr(hl, "installed_langs", lambda: {"eng", "ara"})
    monkeypatch.setattr(hl, "_lang_cache", {})
    assert hl.unused_langs("โปรตีน", "eng+ara") == []
    assert hl.missing_langs("โปรตีน") == ["tha"]


def test_the_pinned_setting_reaches_the_card(monkeypatch):
    monkeypatch.setattr(hl, "_tesseract_available", lambda: True)
    monkeypatch.setattr(hl, "missing_langs", lambda w: [])
    monkeypatch.setattr(hl, "unused_langs", lambda w, r: ["tha"])
    d = _tag([{"zone_id": "z1", "found": "โปรตีน"}])[0]
    assert d["hl_why"] == "langcfg:tha"


def test_a_missing_language_wins_over_a_pinned_setting(monkeypatch):
    """ยังไม่ได้ติดตั้ง = ปัญหาที่ใหญ่กว่า ⇒ ต้องรายงานอันนั้นก่อน."""
    monkeypatch.setattr(hl, "_tesseract_available", lambda: True)
    monkeypatch.setattr(hl, "missing_langs", lambda w: ["tha"])
    monkeypatch.setattr(hl, "unused_langs",
                        lambda w, r: pytest.fail("ต้องไม่ถูกเรียก"))
    d = _tag([{"zone_id": "z1", "found": "โปรตีน"}])[0]
    assert d["hl_why"] == "lang:tha"


def test_the_browser_explains_the_pinned_setting_too():
    body = JS[JS.index("function hlWhyText(why)"):
              JS.index("window.awHlWhyText")]
    assert "ARTWORK_HIGHLIGHT_TESS_LANG" in body
