# -*- coding: utf-8 -*-
"""กติกาชั้นเทียบข้อความ 26 ก.ย. 2026 — P1 / P2 / P4

หลักเดียวที่ทุกข้อไหลมา: **การ "ยกโทษ" ทุกกติกาต้องใช้ขอบคำ/ขอบตัวเลข +
ตำแหน่งติดกัน — ห้ามใช้ "มีอยู่ที่ไหนสักแห่ง"**

P1  พยาน (F2) แบบเข้ม — ``ARTWORK_TEXT_WITNESS_STRICT``
    วัดแล้ว: OCR ผิดทั้งสองฝั่ง ลดระดับผิด 65/11,794 → 0
P2  จับคู่การ์ด "พบเฉพาะ" ที่เว้นวรรคต่าง — ``ARTWORK_TEXT_PAIR_CHAR_FALLBACK``
    + บรรทัดที่ต่างแค่ตัวเลข — ``ARTWORK_TEXT_PAIR_NUMERIC``
P4  ยกโทษบรรทัดที่มีตัวเลขได้เฉพาะเมื่อตัวเลขไม่ถูกหั่นและค่าเท่ากัน +
    นับจำนวนสำเนา — ``ARTWORK_TEXT_NUMBER_STRICT``
    วัดแล้ว: เปลี่ยนเลขทีละตัวบนแผงจริง พลาด 14/2,021 → 0
"""
import pytest

from artwork_check import checks as K
from artwork_check import config
from artwork_check import witness as W


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    for k in ("TEXT_WITNESS", "TEXT_WITNESS_STRICT", "TEXT_PAIR_BY_RUN",
              "TEXT_PAIR_CHAR_FALLBACK", "TEXT_PAIR_NUMERIC",
              "TEXT_NUMBER_STRICT", "NUMBER_CONTEXT"):
        monkeypatch.setattr(config, k, True)
    monkeypatch.setattr(config, "TEXT_PAIR_MIN_RUN", 0.40)
    monkeypatch.setattr(config, "TEXT_PAIR_CHAR_MIN_RUN", 0.60)
    monkeypatch.setattr(config, "TEXT_PAIR_CHAR_MIN_LEN", 8)


def _zones(n=2):
    zs = [{"id": "z1", "type": "panel", "group": "A", "doc": "a",
           "bbox": [0, 0, 1, 1]},
          {"id": "b2", "type": "panel", "group": "A", "doc": "b",
           "bbox": [0, 0, 1, 1]}]
    if n == 3:
        zs.append({"id": "z3", "type": "panel", "group": "A", "doc": "a",
                   "bbox": [0, 0, 1, 1]})
    return zs


def _mm(a, b, extra=None, engine="pdf-text"):
    texts = {"z1": a, "b2": b}
    if extra is not None:
        texts["z3"] = extra
    ds = K.run_all_checks(_zones(3 if extra is not None else 2),
                          [{"zone_id": z, "text": t, "engine": engine}
                           for z, t in texts.items()])
    return [d for d in ds if d["class"].startswith("MISMATCH")]


# ══ P1 — พยานแบบเข้ม ═══════════════════════════════════════════════════

def test_dropped_first_letter_is_not_a_rewrap(monkeypatch):
    """รุ่น 25 ก.ย. ยกโทษ ``c`` ที่หายหน้าคำ เพราะ "c มีอยู่ในแผงอีกฝั่ง"
    (ตัวอักษรเดียวมีอยู่ทุกแผง) — ตัดกลางคำ = ต่างจริง"""
    w = "Chicken breast 56 g"
    assert W.agree(w, "hicken breast 56 g", w, "Crude protein") is False
    monkeypatch.setattr(config, "TEXT_WITNESS_STRICT", False)
    assert W.agree(w, "hicken breast 56 g", w, "Crude protein") is True


def test_case_difference_is_not_hidden(monkeypatch):
    w = "D-Calcium Pantothenate 5 mg"
    assert W.agree(w, "D-calcium Pantothenate 5 mg", w, "") is False
    monkeypatch.setattr(config, "TEXT_WITNESS_STRICT", False)
    assert W.agree(w, "D-calcium Pantothenate 5 mg", w, "") is True


def test_rewrap_must_be_adjacent_not_anywhere(monkeypatch):
    w = "Net weight 56 g"
    far = "Net weight 56 g\nIngredients: tuna\nper pack"
    near = "Net weight 56 g\nper pack"
    assert W.agree(w, "Net weight 56 g per pack", near, "") is True
    assert W.agree(w, "Net weight 56 g per pack", far, "") is False
    monkeypatch.setattr(config, "TEXT_WITNESS_STRICT", False)
    assert W.agree(w, "Net weight 56 g per pack", far, "") is True


def test_rewrap_cut_must_be_on_a_word_boundary():
    w = "Net weight 56 g"
    # ส่วนเกิน "per" ติดกันจริง แต่อีกฝั่งตัดกลางคำ ("56 gper")
    assert W.agree(w, "Net weight 56 gper pack", "Net weight 56 g\nper pack",
                   "") is False


def test_script_change_counts_as_a_word_boundary():
    """text layer เกาหลี/ไทยไม่มีช่องว่าง — ``กรัม제품명`` คือสองคำ"""
    b = W._bounds("กรัม제품명: 프리스키")
    assert 4 in b
    # อีกฝั่งพิมพ์ "กรัม" ไว้ท้ายบรรทัดก่อนหน้า (ตัดบรรทัดคนละที่จริง)
    w = "กรัม제품명: 프리스키"
    assert W.agree(w, "제품명: 프리스키", w, "42 กรัม\n제품명: 프리스키") is True
    # ถ้าอีกฝั่งไม่มี "กรัม" ติดกันเลย = ต่างจริง
    assert W.agree(w, "제품명: 프리스키", w, "제품명: 프리스키") is False


def test_tied_witness_lines_are_ambiguous(monkeypatch):
    """ฉลากหลายรส: สองบรรทัดคะแนนเท่ากันแต่เนื้อหาต่างกัน ⇒ ไม่ตัดสิน"""
    lines = ["Tuna 1,0% fibre 0,05%", "Tuna 1,7% fibre 0,05%"]
    assert W._best("Tuna 1,2% fibre 0,05%", lines) is None
    monkeypatch.setattr(config, "TEXT_WITNESS_STRICT", False)
    assert W._best("Tuna 1,2% fibre 0,05%", lines) == lines[0]


def test_identical_duplicate_witness_lines_are_not_ambiguous():
    lines = ["Net weight 56 g", "Net weight 56 g"]
    assert W._best("Net weight 56 g", lines) == "Net weight 56 g"


def test_extra_there_needs_word_boundaries():
    d = {"class": "MISMATCH_PANELS", "zone_id": "z1", "found": "tein 5",
         "reference": ""}
    wl = W.witness_lines("Protein 55")
    assert W.judge(d, {"b2": wl}, {"z1": "tein 5", "b2": ""}, "b2") is None
    d["found"] = "Protein 55"
    ev = W.judge(d, {"b2": wl}, {"z1": "Protein 55", "b2": ""}, "b2")
    assert ev and ev["kind"] == "extra_there"


def test_both_ocr_sides_wrong_is_not_downgraded():
    """S3 — OCR ฝั่งนี้เพี้ยน **และ** อีกฝั่งต่างจริง ⇒ ต้องไม่ลดระดับ"""
    w = "Crude protein 5.5% (Min) Crude fat 0.05%"
    mine = "Crude protien 5.5% (Min) Crude fat 0.05%"     # OCR ฝั่งนี้เพี้ยน
    other = "Crude protein 5.5% (Min) Crude fat 0.5%"     # อีกไฟล์ต่างจริง
    assert W._side_misread(mine, other, W.witness_lines(w), mine, other) is None


# ══ P2 — จับคู่การ์ด ══════════════════════════════════════════════════

STA = "제품에 별도 표시 (일월년 순) 중량: 56g (14g × 4개)"
STB = "ผลิตภัณฑ์에 별도 표시(일월년 순) 중량: 56 g (14 g × 4개)"


def test_spacing_split_pairs_by_characters(monkeypatch):
    assert K.line_run_ratio(STA, STB) < 0.40
    assert K._pair_score(STA, STB) >= 0.40
    monkeypatch.setattr(config, "TEXT_PAIR_CHAR_FALLBACK", False)
    assert K._pair_score(STA, STB) == K.line_run_ratio(STA, STB)


def test_short_fragments_never_pair_by_characters():
    """ท่อนอาหรับสั้น ``١ جم`` เคยไปจับกับ ``ملجم٤٧٥`` (ต่ำกว่า 8 ตัว)"""
    assert K._pair_score("١ جم", "ملجم٤٧٥") < 0.40


def test_word_level_pairs_keep_their_score():
    a, b = "Net weight 56 g per pack", "Net weight 58 g per pack"
    assert K._pair_score(a, b) == K.line_run_ratio(a, b)


def test_spacing_split_becomes_one_card():
    m = _mm("A line\n" + STA, "A line\n" + STB, engine="n8n")
    assert len(m) == 1 and m[0]["found"] and m[0]["reference"]


def test_numeric_only_rows_pair_when_unambiguous(monkeypatch):
    m = _mm("Sodium\n20%\nFat", "Sodium\n24%\nFat", engine="n8n")
    assert [(d["found"], d["reference"]) for d in m] == [("20%", "24%")]
    monkeypatch.setattr(config, "TEXT_PAIR_NUMERIC", False)
    assert len(_mm("Sodium\n20%\nFat", "Sodium\n24%\nFat", engine="n8n")) == 2


def test_numeric_rows_are_not_paired_when_ambiguous():
    m = _mm("Sodium\n20%\n10%", "Sodium\n24%\n14%", engine="n8n")
    assert all(not (d["found"] and d["reference"]) for d in m)
    assert len(m) == 4


# ══ P4 — ตัวเลขต้องไม่ถูกยกโทษแบบ "มีอยู่ที่ไหนสักแห่ง" ═══════════════

def test_decimal_point_is_not_forgiven(monkeypatch):
    """``59,9`` กับ ``599`` มีคีย์เดียวกัน (``_norm_key`` ตัดวรรคตอน)"""
    a = "Crude fat 59,9 %\nMoisture 80 %"
    b = "Crude fat 599 %\nMoisture 80 %"
    assert _mm(a, b)
    monkeypatch.setattr(config, "TEXT_NUMBER_STRICT", False)
    assert not _mm(a, b)


def test_number_cut_across_a_line_break_is_not_forgiven(monkeypatch):
    """``BRUTA100`` เคยไปเจอรอยต่อ ``…1,0`` + ``0,5…`` ของบรรทัดถัดไป"""
    a = "proteina bruta 1,0 %\n0,5 % fibra bruta"
    b = "proteina bruta 10,0 %\n0,5 % fibra bruta"
    assert _mm(a, b)
    monkeypatch.setattr(config, "TEXT_NUMBER_STRICT", False)
    assert not _mm(a, b)


def test_number_inside_a_bigger_number_is_not_forgiven():
    """John West: ``20%`` เคยถูกยกโทษเพราะอีกแผงมี ``2000 calorie``"""
    a = "Sodium\n20%\nbased on a 2000 calorie diet"
    b = "Sodium\n24%\nbased on a 2000 calorie diet"
    m = _mm(a, b, engine="n8n")
    assert [(d["found"], d["reference"]) for d in m] == [("20%", "24%")]


def test_multi_variety_copy_changed_is_caught(monkeypatch):
    """เปลี่ยนเลขของรสหนึ่งให้ตรงกับอีกรสพอดี ⇒ นับจำนวนสำเนาจึงเห็น"""
    x = "Tuna: proteine grezze 1,7%, fibra grezza 0,05%"
    y = "Tuna: proteine grezze 1,0%, fibra grezza 0,05%"
    a = "\n".join([x, y, y])
    b = "\n".join([x, x, y])
    assert _mm(a, b)
    monkeypatch.setattr(config, "TEXT_NUMBER_STRICT", False)
    assert not _mm(a, b)


def test_copy_hidden_inside_a_longer_variety_line_is_counted():
    """สำเนาอยู่ **ในบรรทัดยาวของรสอื่น** — นับแบบ "ทั้งบรรทัด" ไม่เห็น
    (วัดบนแผงจริง: พลาด 8/1,008) ⇒ ต้องนับแบบเดียวกันทั้งสองฝั่ง"""
    x = "proteine grezze 1,7%, fibra grezza 0,05%"
    y = "proteine grezze 1,0%, fibra grezza 0,05%"
    a = "Tuna and chicken: " + x + "\n" + y
    b = "Tuna and chicken: " + x + "\n" + x
    m = _mm(a, b)
    assert m and any(x in d["found"] + d["reference"] for d in m)


def test_surplus_card_does_not_claim_the_line_is_missing_elsewhere():
    """อีกฝั่ง **มี** บรรทัดนั้น (น้อยครั้งกว่า) ⇒ ห้ามขึ้นว่า "พบเฉพาะใน" """
    x = "proteine grezze 1,7%, fibra grezza 0,05%"
    zs = [{"id": "z1", "type": "panel", "group": "A", "bbox": [0, 0, 1, 1]},
          {"id": "z2", "type": "panel", "group": "A", "bbox": [0, 0, 1, 1]}]
    ds = K.run_all_checks(zs, [
        {"zone_id": "z1", "text": "Tuna: " + x + "\n" + x, "engine": "pdf-text"},
        {"zone_id": "z2", "text": "Tuna: " + x, "engine": "pdf-text"}])
    m = [d for d in ds if d["class"] == "MISMATCH_PANELS"]
    assert len(m) == 1 and m[0]["zone_id"] == "z1"
    assert "พบเฉพาะ" not in m[0]["message"] and "2 ครั้ง" in m[0]["message"]


def test_same_text_rewrapped_with_spacing_noise_stays_clean():
    a = ("Crude protein 5.5% (Min), Crude fat 0.05% (Min),\n"
         "Moisture 90% (Max), Net weight 56 g (14 g x 4)\n"
         "Tel 02 123 4567 · 1 000 kcal/kg")
    b = ("Crude protein 5,5% (Min), Crude fat\n0,05% (Min), Moisture 90%\n"
         "(Max), Net weight 56g(14g x 4) Tel 02 123 4567\n· 1000 kcal/kg")
    assert _mm(a, b) == []


def test_three_panel_groups_keep_the_old_rule():
    """ชั้นใหม่ scope เฉพาะกลุ่ม 2 panel (วัดแล้วเฉพาะแบบนั้น)"""
    a = "Crude fat 59,9 %\nMoisture 80 %"
    b = "Crude fat 599 %\nMoisture 80 %"
    assert not [d for d in _mm(a, b, extra=a) if d["zone_id"] == "b2"]


def test_contained_soundly_answers_three_ways():
    assert K._contained_soundly("fat 59,9 %", "Crude fat 59,9 %") is True
    assert K._contained_soundly("fat 599 %", "Crude fat 59,9 %") is False
    assert K._contained_soundly("no digits", "no digits here") is None
    assert K._contained_soundly("fat 12", "totally different") is None


def test_num_canon_keeps_decimals_and_joins_thousands():
    assert K._num_canon("59,9 % 1 000 kcal ٥٫٥") == ["59.9", "1000", "5", "5"]
    assert K._num_canon("5.5") == K._num_canon("5,5")
