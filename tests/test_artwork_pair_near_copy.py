# -*- coding: utf-8 -*-
"""จับคู่การ์ด "พบเฉพาะ" ที่เป็นสำเนาเกือบตรงกันทั้งบรรทัด (27 ก.ย. 2026)

ที่มา (สถานี, Dolphin): ที่อยู่ภาษาอาหรับที่ OCR ฝั่ง 🅱 อ่านจุดเพี้ยน 3 จุด
กระจายทั้งบรรทัด (``سيثاكيت``↔``سيتاكيت`` · ``موينج``↔``مويني`` · ``،``↔``.``)
⇒ ช่วงคำติดกัน 0.29 และช่วงอักขระติดกัน 0.33 ไม่ผ่านทั้งคู่ ⇒ ขึ้นเป็น
"พบเฉพาะ" 2 ใบที่ไม่ชี้ว่าต่างตรงไหน ทั้งที่เหมือนกันทั้งบรรทัด 93%

ล็อก: รอบใหม่ทำงาน **หลัง** รอบเดิมทุกรอบ และรับเฉพาะใบที่เหลือ ⇒ คู่เดิม
ไม่ถูกแตะ · ด่านทุกชั้น (ความยาว · สัดส่วน · จำนวนตัวที่แก้ · ไม่กำกวม
ทั้งสองฝั่ง) · ธงปิด = สองใบเหมือนเดิม · ทาง Levenshtein เดิมไม่ถูกแตะ
"""
import pytest

from artwork_check import checks as K, config

Z_LINE = "2/30 طريق سيثاكيت 1 ، موينج ساموت ساخون ،"
B_LINE = "2/30 طريق سيتاكيت 1 . مويني ساموت ساخون."
COMMON = ("المنتج : شركة تاي يونيون للتصنيع المحدودة\n"
          "ساموت ساخون 74000 ، تايلاند")


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    for k in ("TEXT_PAIR_BY_RATIO", "TEXT_PAIR_BY_RUN",
              "TEXT_PAIR_CHAR_FALLBACK", "TEXT_PAIR_NUMERIC",
              "TEXT_NUMBER_STRICT", "TEXT_WITNESS_STRICT"):
        monkeypatch.setattr(config, k, True)
    monkeypatch.setattr(config, "TEXT_PAIR_MIN_RUN", 0.40)
    monkeypatch.setattr(config, "TEXT_PAIR_RATIO_MIN", 0.85)
    monkeypatch.setattr(config, "TEXT_PAIR_RATIO_MIN_LEN", 20)
    monkeypatch.setattr(config, "TEXT_PAIR_RATIO_MAX_EDITS", 4)


def _run(a_lines, b_lines):
    zones = [{"id": "z1", "type": "panel", "group": "A", "doc": "a",
              "bbox": [0, 0, 1, 1]},
             {"id": "b2", "type": "panel", "group": "A", "doc": "b",
              "bbox": [0, 0, 1, 1]}]
    ocr = [{"zone_id": "z1", "text": COMMON + "\n" + "\n".join(a_lines)},
           {"zone_id": "b2", "text": COMMON + "\n" + "\n".join(b_lines)}]
    return K.run_all_checks(zones, ocr)


def _paired(ds):
    return [(d["found"], d["reference"]) for d in ds
            if d.get("found") and d.get("reference")]


# ── ค่าตั้ง ────────────────────────────────────────────────────────────

def test_flag_and_thresholds_ship_as_measured():
    src = open(config.__file__, encoding="utf-8").read()
    assert '"ARTWORK_TEXT_PAIR_BY_RATIO", "1"' in src
    assert '"ARTWORK_TEXT_PAIR_RATIO_MIN", "0.85"' in src
    assert '"ARTWORK_TEXT_PAIR_RATIO_MIN_LEN", "20"' in src
    assert '"ARTWORK_TEXT_PAIR_RATIO_MAX_EDITS", "4"' in src


# ── เคสสถานี ──────────────────────────────────────────────────────────

def test_the_station_lines_fail_every_earlier_pass():
    """ถ้าวันหนึ่งรอบเดิมจับคู่นี้ได้เอง เทสต์ข้างล่างจะไม่ได้ทดสอบรอบใหม่"""
    assert K._pair_score(Z_LINE, B_LINE) < config.TEXT_PAIR_MIN_RUN
    assert not K._num_skeleton(Z_LINE) == K._num_skeleton(B_LINE)


def test_station_case_becomes_one_card_with_red_on_the_letters():
    ds = _run([Z_LINE], [B_LINE])
    assert len(ds) == 1
    d = ds[0]
    assert (d["class"], d["zone_id"], d["severity"]) == (
        "MISMATCH_PANELS", "z1", "critical")
    assert (d["found"], d["reference"]) == (Z_LINE, B_LINE)
    assert d["ref_zone_ids"] == ["b2"]
    assert [d["found"][a:b] for a, b in d["found_spans"]] == [
        "سيثاكيت", "موينج"]
    assert [d["reference"][a:b] for a, b in d["ref_spans"]] == [
        "سيتاكيت", "مويني"]


def test_flag_off_is_the_old_two_cards_exactly(monkeypatch):
    monkeypatch.setattr(config, "TEXT_PAIR_BY_RATIO", False)
    ds = _run([Z_LINE], [B_LINE])
    assert [(d["zone_id"], d["found"], d.get("reference") or "")
            for d in ds] == [("z1", Z_LINE, ""), ("b2", B_LINE, "")]


def test_levenshtein_mode_is_untouched(monkeypatch):
    monkeypatch.setattr(config, "TEXT_PAIR_BY_RUN", False)
    on = _run([Z_LINE], [B_LINE])
    monkeypatch.setattr(config, "TEXT_PAIR_BY_RATIO", False)
    assert on == _run([Z_LINE], [B_LINE])
    monkeypatch.setattr(config, "TEXT_PAIR_BY_RATIO", True)

    def boom(*a, **k):
        raise AssertionError("ต้องไม่ถูกเรียกในโหมด Levenshtein")
    monkeypatch.setattr(K, "_pair_near_copies", boom)
    monkeypatch.setattr(config, "TEXT_PAIR_BY_RUN", False)
    _run([Z_LINE], [B_LINE])


# ── คู่เดิมไม่ถูกแตะ ────────────────────────────────────────────────────

def test_only_leftover_cards_reach_the_new_pass(monkeypatch):
    """บรรทัดที่รอบคำจับคู่ได้แล้ว ต้องไม่ถูกส่งเข้ารอบใหม่เลย"""
    seen = []
    real = K._pair_near_copies

    def spy(a_rest, b_rest):
        seen.append(([d["found"] for d in a_rest],
                     [d["found"] for d in b_rest]))
        return real(a_rest, b_rest)
    monkeypatch.setattr(K, "_pair_near_copies", spy)
    word_a = "Net weight 185 g drained weight 130 g per can"
    word_b = "Net weight 185 g drained weight 150 g per can"
    ds = _run([word_a, Z_LINE], [word_b, B_LINE])
    assert seen == [([Z_LINE], [B_LINE])]
    assert sorted(_paired(ds)) == sorted([(word_a, word_b), (Z_LINE, B_LINE)])


def test_word_level_pair_wins_over_a_near_copy(monkeypatch):
    """ใบที่รอบคำจับคู่กับใบอื่นได้แล้ว ต้องไม่ถูกดึงมาจับคู่ใหม่"""
    a = "Thai Union Manufacturing Co Ltd Samut Sakhon 74000 Thailand"
    b = "Thai Union Manufacturing Co Ltd Samut Sakhon 74000 Thai1and"
    on = _run([a], [b])
    monkeypatch.setattr(config, "TEXT_PAIR_BY_RATIO", False)
    assert on == _run([a], [b])


# ── ด่านกันจับคู่ผิด ────────────────────────────────────────────────────

def test_translation_in_a_close_language_is_not_a_near_copy():
    """ภาษาใกล้กันได้สัดส่วนสูง (0.86) — ด่านจำนวนตัวที่แก้ต้องกัน"""
    # บรรทัดจริงจาก text layer ของ Cosma (ทุกตัวอักษร)
    it = ("Pollo con fegato di pollo in gelatina. COMPOSIZIONE: "
          "carne di pollo (34,1%),")
    es = ("Pollo con hígado de pollo en gelatina. COMPOSICIÓN: "
          "carne de pollo (34,1 %),")
    fa = K._norm_key(it).replace(" ", "")
    fb = K._norm_key(es).replace(" ", "")
    from difflib import SequenceMatcher
    assert SequenceMatcher(None, fa, fb, autojunk=False).ratio() >= 0.85
    assert not K._near_copy(it, es)
    assert len(_run([it], [es])) == 2


def test_more_than_four_edits_is_not_a_near_copy():
    a = "Thai Union Manufacturing Company Samut Sakhon Thailand"
    b = "Thai Unian Monufacturing Campony Somut Sakhon Thailand"
    assert K.levenshtein(K._norm_key(a).replace(" ", ""),
                         K._norm_key(b).replace(" ", "")) == 5
    assert not K._near_copy(a, b)
    c = "Thai Unian Monufacturing Campony Samut Sakhon Thailand"
    assert K._near_copy(a, c)


def test_short_lines_are_never_paired_this_way(monkeypatch):
    """บรรทัดสั้นเหมือนกันโดยบังเอิญได้ง่าย — ต้องถูกกันด้วยความยาวเท่านั้น
    (คู่นี้ผ่านด่านสัดส่วนและจำนวนตัวที่แก้ทั้งคู่)"""
    a, b = "Crude Protein min", "Crude Pr0tein min"
    monkeypatch.setattr(config, "TEXT_PAIR_RATIO_MIN_LEN", 5)
    assert K._near_copy(a, b)
    monkeypatch.setattr(config, "TEXT_PAIR_RATIO_MIN_LEN", 20)
    assert not K._near_copy(a, b)


def test_low_similarity_is_not_a_near_copy(monkeypatch):
    monkeypatch.setattr(config, "TEXT_PAIR_RATIO_MAX_EDITS", 99)
    a = "Ingredients tuna vegetable oil water salt"
    b = "Imported by AM Group Misr for import export"
    assert not K._near_copy(a, b)


def test_identical_keys_are_not_near_copies():
    """ต่างแค่วรรคตอน = ชั้นหลักยกโทษอยู่แล้ว · ไม่ใช่หน้าที่ของรอบนี้"""
    assert not K._near_copy(Z_LINE, Z_LINE.replace("،", "."))


def test_ambiguous_on_the_reference_side_is_not_paired():
    b2 = B_LINE.replace("ساموت", "ساموث")
    ds = _run([Z_LINE], [B_LINE, b2])
    assert _paired(ds) == []
    assert len(ds) == 3


def test_ambiguous_on_the_main_side_is_not_paired():
    a2 = Z_LINE.replace("ساموت", "ساموث")
    ds = _run([Z_LINE, a2], [B_LINE])
    assert _paired(ds) == []
    assert len(ds) == 3


def test_two_independent_near_copies_both_pair():
    a1, b1 = Z_LINE, B_LINE
    a2 = "Samut Sakhon 74000 Thailand Mueang Samut Sakhon District"
    b2 = "Samut Sakhon 74000 Tha1land Mueang Samut Sakh0n Dlstrict"
    ds = _run([a1, a2], [b1, b2])
    assert sorted(_paired(ds)) == sorted([(a1, b1), (a2, b2)])
    assert len(ds) == 2


def test_no_line_ever_leaves_the_report():
    """จับคู่ = รวมการ์ด ไม่ใช่ลบ — ข้อความทุกบรรทัดต้องยังอยู่บนรายงาน"""
    ds = _run([Z_LINE], [B_LINE])
    shown = {t for d in ds for t in (d.get("found"), d.get("reference")) if t}
    assert {Z_LINE, B_LINE} <= shown
    assert all(d["severity"] == "critical" for d in ds)


def test_single_panel_groups_and_same_file_groups_are_untouched(monkeypatch):
    zones = [{"id": "z1", "type": "panel", "group": "A", "doc": "a",
              "bbox": [0, 0, 1, 1]},
             {"id": "z2", "type": "panel", "group": "A", "doc": "a",
              "bbox": [0, 0, 1, 1]}]
    ocr = [{"zone_id": "z1", "text": COMMON + "\n" + Z_LINE},
           {"zone_id": "z2", "text": COMMON + "\n" + B_LINE}]
    on = K.run_all_checks(zones, ocr)
    monkeypatch.setattr(config, "TEXT_PAIR_BY_RATIO", False)
    assert on == K.run_all_checks(zones, ocr)


# ── พยาน (text layer ของไฟล์ 🅱) กับการ์ดที่เพิ่งถูกรวม ─────────────────────
# ที่มา (stress บนเคสจริง Friskies): การ์ดแยก 2 ใบเคยได้หลักฐาน "ไฟล์พิมพ์
# เหมือนอีกฝั่ง" (REVIEW) แต่พอรวมเป็นใบเดียว การหาบรรทัดพยานด้วยช่วงติดกัน
# ล้มเหลว (OCR เพี้ยนกระจาย) หรือเจอบรรทัดขยะที่เสมอกัน (บรรทัด 1 ตัวอักษร)
# ⇒ กลายเป็น critical — ต้องได้หลักฐานกลับมา โดยยังตัดสินด้วย ``agree`` เท่าเดิม

TRUE_LINE = "Tapioka Modifikasi INS1442, Guaran INS412); Kakap Putih; Protein"
MISREAD = "Tapi0ka Modifikasi INS1442, Guaran INS4l2); Kokap Putih; Protcin"


def _run_w(b_ocr, witness):
    zones = [{"id": "z1", "type": "panel", "group": "A", "doc": "a",
              "bbox": [0, 0, 1, 1]},
             {"id": "b2", "type": "panel", "group": "A", "doc": "b",
              "bbox": [0, 0, 1, 1]}]
    ocr = [{"zone_id": "z1", "text": COMMON + "\n" + TRUE_LINE},
           {"zone_id": "b2", "text": COMMON + "\n" + b_ocr,
            "witness": witness}]
    return K.run_all_checks(zones, ocr)


# บรรทัด "o" = ขยะจาก text layer จริงของ Friskies ที่ชนะช่วงอักขระได้ทุกบรรทัด
WITNESS = "\n".join(["o", COMMON.replace("\n", " "), TRUE_LINE])


def test_the_friskies_junk_line_really_blocks_the_old_lookup():
    from artwork_check import witness as W
    lines = W.witness_lines(WITNESS)
    assert W._best(MISREAD, lines) is None or W._best(MISREAD, lines) == "o"


def test_merged_card_keeps_the_proof_that_the_file_prints_the_same():
    ds = _run_w(MISREAD, WITNESS)
    assert len(ds) == 1
    d = ds[0]
    assert d["severity"] == config.TEXT_WITNESS_SEVERITY
    assert d["witness"]["kind"] == "pair" and d["witness"]["zone"] == "b2"
    assert d["witness"]["file_says"] == TRUE_LINE
    assert (d["found"], d["reference"]) == (TRUE_LINE, MISREAD)


def test_a_real_change_in_the_file_stays_critical():
    """ไฟล์ 🅱 พิมพ์ต่างจริง (พยาน = สิ่งที่ OCR 🅱 อ่าน) ⇒ ห้ามลดระดับ"""
    real = TRUE_LINE.replace("INS1442", "INS1422")
    ds = _run_w(real, "\n".join(["o", COMMON.replace("\n", " "), real]))
    assert [d["severity"] for d in ds] == ["critical"]
    assert not ds[0].get("witness")


def test_real_change_plus_misread_stays_critical():
    real = TRUE_LINE.replace("INS1442", "INS1422")
    noisy = real.replace("Tapioka", "Tapi0ka").replace("Protein", "Protcin")
    ds = _run_w(noisy, "\n".join(["o", COMMON.replace("\n", " "), real]))
    assert all(d["severity"] == "critical" for d in ds)
    assert not any(d.get("witness") for d in ds)


def test_two_near_copy_witness_lines_are_ambiguous():
    from artwork_check import witness as W
    other = TRUE_LINE.replace("Protein", "Protcin")   # อีกบรรทัดที่ใกล้พอ
    assert K._near_copy(MISREAD, other) and K._near_copy(MISREAD, TRUE_LINE)
    assert W._near_line(MISREAD, [TRUE_LINE, other]) is None
    assert W._near_line(MISREAD, [TRUE_LINE, TRUE_LINE]) == TRUE_LINE


def test_witness_fallback_is_off_with_the_flag(monkeypatch):
    from artwork_check import witness as W

    def boom(*a, **k):
        raise AssertionError("ต้องไม่ถูกเรียกเมื่อปิดธง")
    monkeypatch.setattr(W, "_near_line", boom)
    monkeypatch.setattr(config, "TEXT_PAIR_BY_RATIO", False)
    ds = _run_w(MISREAD, WITNESS)
    assert len(ds) == 2


def test_witness_fallback_never_overrides_existing_proof(monkeypatch):
    """ทางเดิมหาหลักฐานได้แล้ว ⇒ ไม่ต้องถอย (ผลเดิมทุกตัวอักษร)"""
    from artwork_check import witness as W
    calls = []
    real = W._near_line
    monkeypatch.setattr(W, "_near_line",
                        lambda *a: calls.append(a) or real(*a))
    one = TRUE_LINE.replace("Kakap", "Kokap")       # เพี้ยนจุดเดียว = ทางเดิมพอ
    ds = _run_w(one, TRUE_LINE)
    assert ds[0].get("witness") and calls == []


def test_existing_pair_cards_get_no_new_proof_with_the_flag_off(monkeypatch):
    """การ์ดคู่ที่มาจากรอบเดิม (คำ/อักขระ/ตัวเลข) ต้องได้ผลเดิมเป๊ะเมื่อปิดธง
    — ยิงตรงที่ขั้นตัดสินของการ์ดคู่ ไม่ผ่านการจับคู่"""
    from artwork_check import witness as W
    lines = W.witness_lines(WITNESS)
    args = (MISREAD, TRUE_LINE, lines, MISREAD, TRUE_LINE)
    assert W._side_misread(*args) is not None
    monkeypatch.setattr(config, "TEXT_PAIR_BY_RATIO", False)
    assert W._side_misread(*args) is None
