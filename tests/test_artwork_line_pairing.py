# -*- coding: utf-8 -*-
"""จับคู่บรรทัดข้ามไฟล์ + ไฮไลต์ส่วนที่ต่าง + ด่านความน่าเชื่อถือของชั้นภาพ

ที่มา (ผลรันจริงบนสถานี 9 ก.ย. 2026 — คู่ PURINA ONE Treats Puree Tuna):

  ① ความต่างจริง "พลังงาน 520 vs 510 kcal" ถูกแยกเป็น **2 ใบ** ที่บอกแค่
     "ข้อความนี้พบเฉพาะในฝั่งนี้" และ **ไม่ชี้ว่าต่างตรงไหน** ⇒ ผู้ตรวจต้อง
     อ่านข้อความอาหรับยาว 2 ก้อนแล้วไล่หาเอง
     ต้นเหตุ: เกณฑ์จับคู่ใช้ระยะแก้ไข **ทั้งบรรทัด** ⇒ หัว-ท้ายที่ OCR ตัด
     คนละที่ (~88 ตัวอักษร) กินโควตาหมด ทั้งที่ต่างจริงแค่ "2" กับ "1"
     วัดได้: Levenshtein 101 · เพดาน 71 ⇒ เกินไป 30

  ② ชั้นภาพ (35 บริเวณ · ecc 0.51 · ต่าง 19.73%) **ลบผลชั้นข้อความ 7 รายการ
     ที่ตรงกับความต่างจริงพอดี** ทิ้งทั้งกลุ่ม

📊 ฐานหลักฐานของเกณฑ์ (ข้อความจริงหลายภาษาจาก text layer ของไฟล์ผู้ใช้):
     1,236 คู่ที่ควรจับ · 400 เคสที่ไม่ควรจับเลย
       Levenshtein เดิม          จับคู่ถูก  73%
       ช่วงคำติดกัน ≥ 0.40       จับคู่ถูก 100% · จับคู่ผิด 4.8%
       คำร่วมทั้งหมด ≥ 0.40      จับคู่ถูก 100% · จับคู่ผิด 13.8% ⇒ แพ้
"""
import pytest

from artwork_check import checks, config


# ── ข้อความจริงจากรายงานของสถานี (ตัดมาเฉพาะบรรทัดที่เกี่ยวข้อง) ──────
Z_ENERGY = ("للقطط. محتوى الطاقة: 520 كيلو كالوري / كغ، 7 سعرة حرارية لكل عبوة. "
            "يجب توافر المياه النظيفة والعذبة لقطتك في جميع الأوقات. إرشادات التخزين:")
B_ENERGY = ("التغذية المناسبه، نوصي بتقديم طعام متكامل ومتوازن للقطط. "
            "محتوى الطاقـة : 510 كيلو كالوري / كغ، 7 سعرة حرارية لكل عبوة. "
            "يجب توافر المياه النظيفة")
Z_ING = ("الوزن الصافي: 56 غ (4 × 14 غ). المكونات: سمك البونيتو، سمك التونة (10%)، "
         "مثخنـات قوام، بروتين متحلل (دواجن)، مستخلص السمك، توابل، دهن التونه،")
B_ING = ("الوزن الصافي: 56 غ (4 × 14 غ). المكونات : سمك البونيتو، سمك التونة (10%)، "
         "نشاء، بروتين متحلل (دواجن)، مثخنات قوام، مستخلص السمك، زيت السمك،")


def _spans_text(text, spans):
    return [text[a:b] for a, b in (spans or [])]


# ── เกณฑ์จับคู่ ──────────────────────────────────────────────────────

def test_the_pairing_flag_is_on_by_default():
    assert config.TEXT_PAIR_BY_RUN is True
    # เคสจริงบนสถานีวัดได้ 0.542 ⇒ เกณฑ์ต้องต่ำกว่านั้นพอมีระยะเผื่อ
    assert 0.30 <= config.TEXT_PAIR_MIN_RUN <= 0.50


def test_a_rewrapped_line_with_one_real_change_is_still_paired():
    """หัวใจของรอบนี้ — เกณฑ์เดิมพลาดเคสนี้บนสถานีจริง."""
    assert checks.line_run_ratio(Z_ENERGY, B_ENERGY) >= config.TEXT_PAIR_MIN_RUN


def test_the_old_metric_really_did_fail_here():
    """กันเทสต์ไร้ความหมาย — ถ้าเกณฑ์เดิมผ่านอยู่แล้ว เทสต์บนก็ไม่ได้พิสูจน์อะไร."""
    d = checks.levenshtein(Z_ENERGY.upper(), B_ENERGY.upper())
    assert d > max(len(Z_ENERGY), len(B_ENERGY)) // 2


def test_punctuation_noise_must_not_break_the_contiguous_run():
    """OCR สองฝั่งอ่าน ``المكونات:`` กับ ``المكونات :`` — ถ้าไม่ normalize
    ช่วงคำที่ติดกันจะถูกหักตรงนั้น แล้วบรรทัดส่วนผสมจะจับคู่ไม่ติด
    (เจอจริงระหว่างทำ: 0.36 < 0.40 ⇒ หลุด)."""
    assert checks.line_run_ratio(Z_ING, B_ING) >= config.TEXT_PAIR_MIN_RUN


def test_unrelated_lines_are_not_paired():
    """คนละเรื่องกันต้องไม่ถูกจับคู่ — ไม่งั้นการ์ดจะชี้ความต่างที่ไม่มีจริง."""
    assert checks.line_run_ratio(Z_ENERGY, B_ING) < config.TEXT_PAIR_MIN_RUN


def test_contiguity_beats_bag_of_words():
    """วัดความ *ติดกัน* ไม่ใช่จำนวนคำร่วม — บรรทัดคนละเรื่องบนฉลากเดียวกัน
    ใช้คำซ้ำกันแบบกระจาย (วัดได้: จับคู่ผิด 4.8% vs 13.8%)."""
    a = "ALPHA BRAVO CHARLIE DELTA ECHO FOXTROT GOLF HOTEL"
    # คำร่วม "ครบทุกคำ" แต่กระจาย = บรรทัดคนละเรื่องที่ใช้ศัพท์ชุดเดียวกัน
    scattered = "ALPHA CHARLIE ECHO GOLF"
    # คำร่วมน้อยกว่า แต่ติดกัน = บรรทัดเดียวกันที่ OCR ตัดคนละที่
    contiguous = "ZULU ALPHA BRAVO CHARLIE ZULU"
    assert checks.line_run_ratio(a, scattered) < config.TEXT_PAIR_MIN_RUN
    assert checks.line_run_ratio(a, contiguous) >= config.TEXT_PAIR_MIN_RUN


# ── ไฮไลต์ส่วนที่ต่าง ────────────────────────────────────────────────

# ข้อความ "ทั้งแผง" ของแต่ละฝั่ง — จำเป็นต่อการคัดหัว-ท้ายที่เป็นแค่การ
# ตัดบรรทัด (ท่อนนั้นไปอยู่บรรทัดข้างเคียงของอีกฝั่ง)
FULL_Z = ("لتوفير التغذية المناسبه، نوصي بتقديم طعام متكامل ومتوازن\n"
          + Z_ENERGY + "\nيرجى تخزين هذه العبوة في مكان جاف")
FULL_B = (B_ENERGY
          + "\nوالعذبة لقطتك في جميع الأوقات. إرشادات التخزين: يرجى تخزين هذه العبوة")


def test_the_highlight_points_at_the_number_and_nothing_else():
    """เคสจริงบนสถานี — การ์ดต้องชี้ที่ตัวเลข ไม่ใช่ทาแดงทั้งบรรทัด."""
    fs, rs = checks.diff_spans(Z_ENERGY, B_ENERGY, FULL_Z, FULL_B)
    assert _spans_text(Z_ENERGY, fs) == ["520"]
    assert _spans_text(B_ENERGY, rs) == ["510"]


def test_wrap_only_head_and_tail_are_never_highlighted():
    """ท่อนที่ *มีอยู่* ในอีกฝั่ง แค่ไปอยู่คนละบรรทัด ⇒ ทาแดง = ชี้ว่าต่าง
    ทั้งที่ไม่ต่าง (กฎเหล็กข้อ 2)."""
    # แผงเดียวกัน เนื้อหาเหมือนกันเป๊ะ ต่างแค่ OCR ตัดบรรทัดคนละที่
    whole = "GOLF HOTEL ALPHA BRAVO CHARLIE DELTA ECHO"
    a = "ALPHA BRAVO CHARLIE DELTA ECHO"          # ฝั่ง A ตัดหลัง HOTEL
    b = "GOLF HOTEL ALPHA BRAVO CHARLIE DELTA"    # ฝั่ง B ตัดก่อน ECHO
    fs, rs = checks.diff_spans(a, b, whole, whole)
    assert _spans_text(a, fs) == [], "ท้าย ECHO ไปอยู่บรรทัดถัดไปของอีกฝั่ง"
    assert _spans_text(b, rs) == [], "หัว GOLF HOTEL ไปอยู่บรรทัดก่อนของอีกฝั่ง"


def test_a_one_sided_head_is_still_highlighted():
    """ตัดเฉพาะที่ *มีจริงในอีกฝั่ง* — ของที่มีฝั่งเดียวต้องยังขึ้นสีเสมอ."""
    a = "ALPHA BRAVO CHARLIE"
    b = "ZULU ALPHA BRAVO CHARLIE"                # ZULU ไม่มีในฝั่ง A เลย
    fs, rs = checks.diff_spans(a, b, a, b)
    assert _spans_text(b, rs) == ["ZULU"]


def test_a_real_insertion_is_still_highlighted():
    """ตัดหัว-ท้ายที่เป็นการตัดบรรทัดออก **ต้องไม่กลืนของที่เพิ่มเข้ามาจริง**."""
    a = "PET FOOD NOT SUITABLE FOR HUMAN CONSUMPTION."
    b = "PET FOOD NOT SUITABLE FOR HUMAN CONSUMPTION . for animal use."
    fs, rs = checks.diff_spans(a, b, a, b)
    assert any("animal" in t for t in _spans_text(b, rs))


def test_found_and_reference_are_never_modified():
    """สองค่านี้ถูกใช้ค้นคำเพื่อวาดกรอบแดงบนภาพ crop — แก้แล้วกรอบพัง."""
    zones = [{"id": "z1", "type": "panel", "group": "A", "doc": "a"},
             {"id": "b2", "type": "panel", "group": "A", "doc": "b"}]
    texts = {"z1": Z_ENERGY, "b2": B_ENERGY}
    ds = checks.check_group_consistency(zones, texts)
    paired = [d for d in ds if d.get("found") and d.get("reference")]
    assert paired
    assert paired[0]["found"] == Z_ENERGY
    assert paired[0]["reference"] == B_ENERGY


def test_turning_the_flag_off_restores_the_old_behaviour(monkeypatch):
    """กฎเหล็กข้อ 1 — ต้องถอยกลับได้."""
    zones = [{"id": "z1", "type": "panel", "group": "A", "doc": "a"},
             {"id": "b2", "type": "panel", "group": "A", "doc": "b"}]
    texts = {"z1": Z_ENERGY, "b2": B_ENERGY}
    assert len(checks.check_group_consistency(zones, texts)) == 1     # จับคู่ = 1 ใบ
    monkeypatch.setattr(config, "TEXT_PAIR_BY_RUN", False)
    assert len(checks.check_group_consistency(zones, texts)) == 2     # เดิม = 2 ใบ


def test_a_bad_span_never_breaks_the_card():
    """แสดงผลล้วน — ข้อมูลเพี้ยนต้องไม่ทำให้การ์ดพัง."""
    for bad in ("", None, "‏"):
        fs, rs = checks.diff_spans(bad or "", "ABC", "", "ABC")
        assert fs == [] and rs == []


# ── ③ ชั้นภาพห้ามลบผลชั้นข้อความเมื่อคุณภาพตัวเองต่ำ ─────────────────
#
# ชั้นภาพกับชั้นข้อความ **จับคนละอย่าง** (ชั้นภาพเห็นฟอนต์หนา-บางที่ OCR
# มองไม่เห็น · ชั้นข้อความทนการที่ข้อความไหลใหม่) ⇒ ไม่มีชั้นไหนดีกว่าเสมอ
# ⇒ การให้ชั้นหนึ่ง "ลบ" อีกชั้นทิ้ง ต้องมีเงื่อนไข

from artwork_check.pipeline import _pixel_untrusted


def test_the_trust_gate_is_on_by_default():
    assert config.PIXEL_TRUST_GATE is True
    # ค่าเดียวกับที่หน้ารายงานใช้เตือนอยู่แล้ว (artwork_check.js: > 0.002)
    assert config.PIXEL_TRUST_MAX_DIFF == 0.002


def test_a_clean_pixel_result_is_still_trusted():
    """งานจริงที่ผลใช้ได้ วัดได้ ต่าง 0.014% · ecc 0.9983 — ต้องผ่านเหมือนเดิม."""
    assert _pixel_untrusted({"diff_ratio": 0.00014, "ecc": 0.9983}) is None


def test_the_station_case_is_refused_with_a_reason():
    """เคสจริง 9 ก.ย.: ต่าง 19.73% · ecc 0.5093 ⇒ ต้องไม่ลบผลชั้นข้อความ."""
    why = _pixel_untrusted({"diff_ratio": 0.197318, "ecc": 0.5093})
    assert why and "19.73" in why


def test_a_poorly_aligned_result_is_refused_even_when_the_diff_is_small():
    """ทาบไม่ติดแต่ต่างน้อย = บังเอิญ ไม่ใช่หลักฐาน."""
    assert _pixel_untrusted({"diff_ratio": 0.0001, "ecc": 0.51})


def test_turning_the_trust_gate_off_restores_the_old_behaviour(monkeypatch):
    monkeypatch.setattr(config, "PIXEL_TRUST_GATE", False)
    assert _pixel_untrusted({"diff_ratio": 0.9, "ecc": 0.1}) is None


def test_missing_numbers_never_cause_a_refusal():
    """ไม่มีข้อมูล ≠ ไม่ดี — รายงานเก่า/เส้นทางที่ไม่ได้วัด ต้องไม่ถูกปฏิเสธ."""
    assert _pixel_untrusted({}) is None
