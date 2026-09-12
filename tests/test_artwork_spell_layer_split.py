"""ชั้น dictionary ย้ายออกจากปุ่ม "ส่งตรวจสอบ" ไปอยู่ที่แท็บแปลอย่างเดียว
(2 ก.ย. 2026) + ซ่อนปุ่ม "เทียบภาพเก่า/ใหม่".

ทำไมต้องมีเทสต์ชุดนี้: การปิดชั้นตรวจเป็นการ **ลดสิ่งที่ระบบตรวจ** ⇒ ต้องมี
หลักฐานว่า (ก) ปิดแล้วไม่มี SPELL_FAIL หลุดเข้า verdict จริง (ข) รายงาน
coverage **บอกความจริง** ว่าชั้นนี้ไม่ได้ทำงาน — ไม่งั้น ✅ PASS จะถูกอ่านว่า
"ไม่มีคำผิด" ซึ่งไม่จริง (กฎเหล็กข้อ 2) และ (ค) แท็บแปลซึ่งเป็นที่ที่ผู้ใช้
ต้องไปตรวจแทน **ยังทำงานเหมือนเดิมทุกอย่าง**

เทสต์ทั้งหมด mock ตัว spellchecker เอง จึงรันได้บนเครื่องที่ไม่มี
pyspellchecker และให้ผลเหมือนกันทุกเครื่อง (ไม่พึ่ง dictionary จริง)
"""

import os
import re

import pytest

from artwork_check import checks, config, report, translate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS_PATH = os.path.join(ROOT, "static", "js", "artwork_check.js")
TPL_PATH = os.path.join(ROOT, "templates", "artwork_check.html")

# คำที่ dictionary ไม่รู้จัก (ลายเซ็นของ SPELL_FAIL) และคำที่รู้จัก
UNKNOWN_WORD = "Skipjackk"
KNOWN_WORDS = {"tuna", "water", "salt", "packed"}
TEXT = "PACKED IN WATER\n%s TUNA" % UNKNOWN_WORD


class _FakeChecker:
    """แทน pyspellchecker — รู้จักเฉพาะคำใน KNOWN_WORDS."""

    def known(self, words):
        return {w for w in words if w.lower() in KNOWN_WORDS}


@pytest.fixture
def fake_spell(monkeypatch):
    monkeypatch.setattr(checks, "_get_spellcheckers", lambda: [_FakeChecker()])


@pytest.fixture
def spell_on(monkeypatch):
    monkeypatch.setattr(config, "INSPECT_SPELL_LAYER", True)


@pytest.fixture
def spell_off(monkeypatch):
    monkeypatch.setattr(config, "INSPECT_SPELL_LAYER", False)


def _zone(zid="z1", group="A", ztype="panel"):
    return {"id": zid, "type": ztype, "group": group,
            "bbox": [0.1, 0.1, 0.2, 0.2], "label": zid}


def _ocr(zid="z1", text=TEXT):
    return {"zone_id": zid, "text": text, "engine": "pdf-text", "conf": 1.0}


# ── ① ค่าเริ่มต้น: ปุ่มส่งตรวจต้องไม่ผลิต SPELL_FAIL ──────────────────

def test_default_config_keeps_the_spell_layer_out_of_inspect():
    """ค่าเริ่มต้นของระบบ = ปุ่มส่งตรวจไม่ตรวจคำสะกด."""
    assert config.INSPECT_SPELL_LAYER is False


def test_inspect_does_not_report_spell_fail(fake_spell, spell_off):
    defects = checks.run_all_checks([_zone()], [_ocr()])
    assert not any(d["class"] == "SPELL_FAIL" for d in defects)


def test_unknown_word_alone_no_longer_forces_review(fake_spell, spell_off):
    """หัวใจของงานนี้: คำที่ dict ไม่รู้จักคำเดียว เคยดัน verdict ทั้งใบเป็น
    🟡 REVIEW ทั้งที่การเทียบไฟล์ผ่านหมด."""
    defects = checks.run_all_checks([_zone()], [_ocr()])
    assert report.compute_verdict(defects) == "PASS"


def test_flag_on_restores_the_old_behaviour_exactly(fake_spell, spell_on):
    """ตั้ง ARTWORK_INSPECT_SPELL_LAYER=1 = กลับพฤติกรรมเดิมเป๊ะ (rollback)."""
    defects = checks.run_all_checks([_zone()], [_ocr()])
    spell = [d for d in defects if d["class"] == "SPELL_FAIL"]
    assert len(spell) == 1 and spell[0]["found"] == UNKNOWN_WORD
    assert report.compute_verdict(defects) == "REVIEW"


def test_other_layers_still_run_when_spell_is_off(fake_spell, spell_off):
    """ปิดชั้นสะกดต้องไม่ไปปิดชั้นอื่น — เทียบข้ามแผงยังต้องฟ้องเหมือนเดิม."""
    zones = [_zone("z1"), _zone("z2"), _zone("z3")]
    ocr = [_ocr("z1", "NET WEIGHT 170 g"), _ocr("z2", "NET WEIGHT 170 g"),
           _ocr("z3", "NET WEIGHT 185 g")]
    defects = checks.run_all_checks(zones, ocr)
    assert any(d["class"] == "MISMATCH_PANELS" for d in defects)


def test_unreadable_layer_untouched(fake_spell, spell_off):
    """ชั้น 'อ่านไม่ชัด' เป็นอีกแหล่งของ REVIEW — ต้องไม่ถูกปิดไปด้วย."""
    ocr = [{"zone_id": "z1", "text": "", "engine": "none", "conf": 0.0,
            "error": "no backend"}]
    defects = checks.run_all_checks([_zone()], ocr)
    assert any(d["class"] == "UNREADABLE" for d in defects)


# ── ② coverage ต้องบอกความจริงว่าชั้นนี้ไม่ได้ทำงาน ────────────────────

def test_coverage_says_the_layer_moved(fake_spell, spell_off):
    cov = checks.check_coverage([_zone()], [_ocr()])
    assert cov["spelling"]["ran"] is False
    assert cov["spelling"]["reason"] == "moved_to_translate"


def test_coverage_reason_matches_run_all_checks(fake_spell, spell_on):
    """เงื่อนไขใน coverage ต้องสะท้อน run_all_checks เป๊ะทั้งสองทิศทาง —
    รายงาน coverage ที่ผิดคือ 'คำตอบที่ผิดแบบมั่นใจ' ตรงตัว."""
    cov = checks.check_coverage([_zone()], [_ocr()])
    assert cov["spelling"]["ran"] is True
    assert cov["spelling"]["reason"] == "ok"


def test_coverage_still_reports_missing_spellchecker_when_layer_on(
        monkeypatch, spell_on):
    """เปิด flag แต่ไม่มี pyspellchecker ⇒ ต้องยังฟ้อง spellchecker_missing
    (เหตุผลคนละอย่างกับ moved_to_translate — วิธีแก้คนละทาง)."""
    monkeypatch.setattr(checks, "_get_spellcheckers", lambda: [])
    cov = checks.check_coverage([_zone()], [_ocr()])
    assert cov["spelling"]["reason"] == "spellchecker_missing"


def test_other_coverage_layers_unaffected(fake_spell, spell_off):
    cov = checks.check_coverage([_zone("z1"), _zone("z2")],
                                [_ocr("z1"), _ocr("z2")])
    assert cov["cross_panel"]["ran"] is True
    assert cov["numbers"]["ran"] is True


# ── ③ แท็บแปลต้องไม่กระทบแม้แต่แถวเดียว ────────────────────────────────

def test_translate_tab_still_flags_the_same_word(fake_spell, spell_off):
    """แท็บแปลมี spell pass ของตัวเอง (ไม่ผ่าน run_all_checks) ⇒ คำเดียวกัน
    ที่หายไปจากปุ่มส่งตรวจ ต้องยังถูกจับที่นี่."""
    rows = translate.build_table([_zone()], [_ocr()], defects=[])
    hit = [r for r in rows if UNKNOWN_WORD in r["flagged"]]
    assert hit and hit[0]["status"] == "spell"


def test_check_spelling_function_must_stay(fake_spell):
    """ห้ามลบฟังก์ชันทิ้ง — แท็บแปลและเทสต์เดิมเรียกใช้ตรง ๆ."""
    d = checks.check_spelling([_zone()], {"z1": TEXT})
    assert [x["found"] for x in d] == [UNKNOWN_WORD]


# ── ④ ฝั่งหน้าเว็บต้องสอดคล้องกับฝั่ง Python ──────────────────────────
# (เทสต์ยูนิตจับ layout ไม่ได้ แต่จับ "คีย์ที่ไม่ตรงกันสองฝั่ง" ได้ ซึ่งเป็น
#  กับดักที่ repo นี้เจอซ้ำหลายรอบ)

def _js():
    with open(JS_PATH, encoding="utf-8") as f:
        return f.read()


def test_js_has_a_message_for_the_new_reason():
    """ไม่มีข้อความ = แถบ coverage ขึ้นเหตุผลว่างเปล่า."""
    js = _js()
    m = re.search(r"const COV_WHY = \{(.*?)\n  \};", js, re.S)
    assert m and "moved_to_translate" in m.group(1)


def test_js_does_not_treat_the_new_reason_as_a_gap():
    """ถ้าไม่ใส่ใน benign แถบจะขึ้น ⚠️ 'ตรวจไม่ครบทุกชั้น' ทุกใบตลอดกาล
    และหัวเรื่องจะกลายเป็น 'PASS — ไม่พบประเด็นในชั้นที่ตรวจ' เสมอ."""
    m = re.search(r"const benign = \{([^}]*)\}", _js())
    assert m and "moved_to_translate" in m.group(1)


def test_js_hides_the_dictionary_kpi_card_when_the_layer_moved():
    """การ์ดที่โชว์ 0 ค้างตลอดไป = ช่องที่ไม่มีทางเป็นค่าอื่น = สับสนกว่าเดิม."""
    js = _js()
    assert 'reason === "moved_to_translate"' in js
    assert 'cls === "SPELL_FAIL" && spellOff' in js


def test_setbusy_survives_a_missing_button():
    """กับดัก $("id") ที่ไม่มีจริง: ปุ่มที่ถูกซ่อน/ถอดออกต้องไม่ทำให้ลูป
    ตั้ง disabled ตายทั้งก้อนจนปุ่มที่เหลือค้าง."""
    m = re.search(r"function setBusy\(b\) \{(.*?)\n  \}", _js(), re.S)
    assert m, "ไม่พบ setBusy()"
    body = m.group(1)
    assert "if (el)" in body and "$(id).disabled" not in body


# ── ⑤ ปุ่มเทียบภาพ: ซ่อน ไม่ใช่ลบ ────────────────────────────────────

def test_pixdiff_button_hidden_by_default():
    assert config.PIXDIFF_UI is False


def test_pixdiff_button_stays_in_the_dom():
    """ต้องซ่อนด้วย style ไม่ใช่ {% if %} ที่ตัด element ทิ้ง — id นี้ถูกอ้าง
    ใน setBusy() และในตัวผูก event."""
    with open(TPL_PATH, encoding="utf-8") as f:
        tpl = f.read()
    i = tpl.find('id="awPixdiff"')
    assert i > 0, "ปุ่ม awPixdiff หายไปจาก template"
    block = tpl[i:i + 400]
    assert "not pixdiff_ui" in block and "display:none" in block
    # element ต้องไม่ถูกครอบด้วย {% if %} ที่ทำให้หายไปจาก DOM
    assert "{% if pixdiff_ui %}" not in tpl


def test_pixdiff_listener_is_guarded():
    assert 'if ($("awPixdiff")) $("awPixdiff").addEventListener' in _js()


def test_pixdiff_rendering_of_old_reports_is_untouched():
    """รายงานเก่าที่เคยกดเทียบภาพไว้ ต้องยังกางผลได้บนหน้าประวัติ."""
    assert "window.awPixdiffHtml = pixdiffHtml;" in _js()
