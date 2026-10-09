"""AI assist: จุดที่ AI "พบเพิ่ม" แต่อัลกอริทึมพับไว้แล้ว ⇒ ไม่เพิ่มซ้ำ (``AI_DEDUP_FOLDED``)

ที่มา: สถานี 8 ต.ค. (Friskies run_005) — F35/F36/F38/F39 (AI พบเพิ่ม · เหลือง) คือจุดเดียวกับ
lowmark F32/F33/F31/F30 ⇒ ผู้ตรวจเห็นเครื่องหมายเดิมสองครั้ง (ในตาราง + ในรายการพับ)

กติกาแคบโดยตั้งใจ: ใช้เฉพาะเมื่อคู่บรรทัดของจุดที่พับมีตัวอักษร/ตัวเลขเหมือนกันทุกตัว
⇒ ความต่างของตัวอักษร/ตัวเลขที่อัลกอริทึมพลาด ยังถูกเพิ่มเป็นเหลืองเสมอ
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from artwork_v2_fake import fta  # noqa: E402

from artwork_v2 import ai_review, compare, config, textmodel  # noqa: E402


def _lines(texts):
    rows = [(t, 20, 40 + i * 40, {"cw": 10, "h": 20}) for i, t in enumerate(texts)]
    return textmodel.parse(fta(rows, 1000, 1000), 1000, 1000)["lines"]


def _wid(lines, side, word):
    for i, ln in enumerate(lines):
        for j, (_, _, w) in enumerate(ai_review._words(ln["text"])):
            if w == word:
                return "%s%d:%d" % (side, i, j)
    raise AssertionError(word)


def _item(aw, aq, bw, bq, verdict="uncertain"):
    return {"a_words": aw, "a_quote": aq, "b_words": bw, "b_quote": bq, "kind": "extra",
            "verdict": verdict, "reason": "เหตุผล", "suggestion": "คำแนะนำ"}


def _setup(ta, tb, fold=lambda f: f["class"] == "PUNCT", keep=lambda f: False):
    """เทียบจริง แล้ว **จำลองว่า** จุดที่ ``fold`` เลือกถูกพับเป็น lowmark (ไม่ขึ้นกับเกณฑ์ conf)
    และจุดที่ไม่ ``keep`` ถูกทิ้ง (จำลองว่าอัลกอริทึมพลาด)"""
    A, B = _lines(ta), _lines(tb)
    r = compare.compare(A, B, (1000, 1000), (1000, 1000))
    A, B = r["lines_a"], r["lines_b"]
    lm = [f for f in r["findings"] if fold(f)]
    assert lm, [f["class"] for f in r["findings"]]
    for i, f in enumerate(r["findings"], 1):
        f["id"] = i
    for f in lm:
        f["severity"] = "lowmark"
    return {"n": 1, "findings": [f for f in r["findings"] if not fold(f) and keep(f)],
            "lowmark": lm}, A, B


@pytest.mark.parametrize("quote_a", [False, True])
def test_ai_extra_on_a_lowmark_is_not_added_again(quote_a):
    """จุดแบบ F35/F33 ของสถานี: B มี "-" นำหน้าคำ (Vision ไม่มั่นใจ) · AI อ้างทั้งคำ"""
    pr, A, B = _setup(["Pellet 10 kg"], ["-Pellet 10 kg"])
    it = (_item([_wid(A, "A", "Pellet")], "Pellet", [_wid(B, "B", "-Pellet")], "-Pellet")
          if quote_a else _item([], "", [_wid(B, "B", "-Pellet")], "-Pellet"))
    st = ai_review.merge("assist", pr, {"items": [it]}, A, B)
    assert st["extra_added"] == 0 and st["extra_folded"] == 1
    assert pr["findings"] == []                                   # ไม่มีเหลืองซ้ำ
    g = pr["lowmark"][0]
    assert g["ai"]["verdict"] == "uncertain"                      # คำตอบ AI แนบไว้ที่จุดที่พับ
    assert any(n.startswith("AI:") for n in g["notes"])


def test_trailing_quote_like_station_f38():
    """F38/F31 ของสถานี: B มี `"` ต่อท้าย "No." """
    pr, A, B = _setup(["Plot 73 & Lot No."], ['Plot 73 & Lot No."'])
    st = ai_review.merge("assist", pr, {"items": [
        _item([_wid(A, "A", "No.")], "No.", [_wid(B, "B", 'No."')], 'No."')]}, A, B)
    assert st["extra_folded"] == 1 and st["extra_added"] == 0 and pr["findings"] == []


def test_real_digit_change_next_to_a_lowmark_is_still_added():
    """⛔ กฎเหล็ก: ตัวเลขเปลี่ยนจริงข้างเครื่องหมายที่พับ (และอัลกอริทึม "พลาด") ต้องยังขึ้นเหลือง"""
    # อัลกอริทึมรวมทั้งสองความต่างเป็นจุดเดียว ⇒ จำลองกรณีเลวร้ายสุด: จุดนั้นถูกพับทั้งจุด
    pr, A, B = _setup(["Lot 123, Bangkok"], ['Lot 124," Bangkok'], fold=lambda f: True)
    assert pr["findings"] == []                                   # จำลองว่าอัลกอริทึมพลาด 123/124
    st = ai_review.merge("assist", pr, {"items": [
        _item([_wid(A, "A", "123,")], "123,", [_wid(B, "B", '124,"')], '124,"', verdict="real")]}, A, B)
    assert st["extra_folded"] == 0 and st["extra_added"] == 1
    assert pr["findings"][0]["severity"] == "yellow"


def test_case_change_is_not_swallowed():
    """ตัวพิมพ์ต่าง (NO. ↔ No.) คือความต่างจริง — คีย์ตัวอักษรต้องไม่พับตัวพิมพ์"""
    pr, A, B = _setup(["Lot NO."], ['Lot No."'], fold=lambda f: True)
    st = ai_review.merge("assist", pr, {"items": [
        _item([_wid(A, "A", "NO.")], "NO.", [_wid(B, "B", 'No."')], 'No."', verdict="real")]}, A, B)
    assert st["extra_folded"] == 0 and st["extra_added"] == 1


def test_other_line_is_not_a_twin():
    """ทับตำแหน่งฝั่ง B แต่ฝั่ง A อ้างคนละบรรทัดกับจุดที่พับ ⇒ ไม่ใช่จุดเดียวกัน"""
    pr, A, B = _setup(["Fish 5", "Oil 3"], ["-Fish 5", "Oil 3"])
    g = pr["lowmark"][0]
    assert (g["a"]["line"], g["b"]["line"]) == (0, 0)
    st = ai_review.merge("assist", pr, {"items": [
        _item([_wid(A, "A", "Oil")], "Oil", [_wid(B, "B", "-Fish")], "-Fish")]}, A, B)
    assert st["extra_folded"] == 0 and st["extra_added"] == 1


def test_flag_off_is_old_behaviour(monkeypatch):
    monkeypatch.setattr(config, "AI_DEDUP_FOLDED", False)
    pr, A, B = _setup(["Pellet 10 kg"], ["-Pellet 10 kg"])
    st = ai_review.merge("assist", pr, {"items": [
        _item([], "", [_wid(B, "B", "-Pellet")], "-Pellet")]}, A, B)
    assert st["extra_folded"] == 0 and st["extra_added"] == 1
    assert pr["findings"][0]["severity"] == "yellow" and not pr["lowmark"][0].get("ai")


def test_judge_and_raw_are_untouched():
    """ชั้นนี้อยู่ในเส้นทาง assist เท่านั้น"""
    import inspect
    src = inspect.getsource(ai_review.merge)
    i_assist = src.index('if mode == "assist":\n        for f in ai_finds')
    i_raw = src.index('elif mode == "raw"')
    assert i_assist < src.index("folded_twin") < i_raw


def test_folded_keys_are_lists_that_exist_before_ai():
    assert set(ai_review.FOLDED_KEYS) == {"lowmark", "debris", "relocated", "excluded"}
