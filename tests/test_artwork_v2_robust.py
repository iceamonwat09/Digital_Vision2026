"""ชุดทดสอบความทนทานของตัวเทียบ Artwork V2 — ใช้ผล OCR **จริง** จาก Cloud Vision

ไฟล์ ``tests/data/artwork_v2/avoderm_m1m2_lines.txt`` = บรรทัดที่ Vision อ่านได้จริงจาก
งาน AvoDerm Master-1/Master-2 (2 ต.ค. 2026) พร้อมกรอบ — เฉลยยืนยันด้วยตาแล้ว

ไม่ได้ทดสอบ "ไฟล์นี้ไฟล์เดียว" แต่ใช้บรรทัดจริงทั้ง ~80 บรรทัดเป็นวัตถุดิบ:

① **เฉลยจริง** — ต้องจับความต่างจริงได้ครบ และไม่มีจุดที่มาจากสัญญาณรบกวนของ OCR
② **ความคงที่ (invariance)** — แปลงข้อความด้วยสัญญาณรบกวนที่ OCR ทำจริง
   (``®``/``Ⓡ`` · ``½``/``1/2`` · จำนวนจุดไข่ปลา · ตัดแถวตารางคนละแบบ) ⇒ ต้องได้ 0 จุด
③ **การกลายพันธุ์ (mutation)** — แก้ทีละบรรทัดทีละแบบ (ตัวพิมพ์ · ลบ/เปลี่ยนตัวอักษร ·
   ตัวเลข · จุดทศนิยม · ลบ/เพิ่มคำ · เพิ่มสัญลักษณ์) ⇒ ทุกกรณีต้องถูกจับ และต้องชี้
   ที่บรรทัดที่ถูกแก้เท่านั้น
"""

from __future__ import annotations

import os
import random
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from artwork_v2_fake import fta_from_lines, load_real  # noqa: E402

from artwork_v2 import compare, config, textmodel  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "data", "artwork_v2", "avoderm_m1m2_lines.txt")
REAL = load_real(DATA)


def _parse(side, lines=None, conf=None):
    W, H, ls = REAL[side]
    ls = ls if lines is None else lines
    if conf is not None:
        ls = [(t, b, conf) for t, b, _ in ls]
    return textmodel.parse(fta_from_lines(ls, W, H), W, H)["lines"]


def _cmp(a_lines, b_lines, side="A", conf=None):
    return compare.compare(_parse(side, a_lines, conf), _parse(side, b_lines, conf))


def _sig(f):
    return (f["class"], f["a"]["frag"], f["b"]["frag"])


# ── ① เฉลยจริง ───────────────────────────────────────────────────────

def test_real_avoderm_finds_every_true_difference_and_no_ocr_noise():
    r = compare.compare(_parse("A"), _parse("B"))
    got = {_sig(f) for f in r["findings"]}
    truth = {
        ("CASE", "c", "C"),                         # D-calcium / D-Calcium
        ("TEXT", "", "s"),                          # Breed / Breeds
        ("PUNCT", ",", ""),                         # Hwy, / Hwy
        ("TEXT", "Park", ""),                       # Irwindale Park / Irwindale
        ("TEXT", "", "USA"),                        # 91706 / 91706 USA
        ("TEXT", "phate Pentahydr", "f"),           # Copper Sulphate Pentahydrate / Sulfate
    }
    assert truth <= got
    # ที่เหลือได้ไม่เกิน "Choice" กับ "Choice." บนโลโก้ (ความมั่นใจจริง 0.45 ⇒ เหลือง)
    assert got - truth <= {("PUNCT", "", ".")}
    # สัญญาณรบกวนที่เคยขึ้นเหลือง 7 จุด ต้องหายหมด
    for f in r["findings"]:
        assert f["class"] != "FILLER"
        assert not ({"®", "Ⓡ", "½"} & set(f["a"]["frag"] + f["b"]["frag"]))
    assert r["coverage"] == 1.0
    # แถวตารางที่ OCR ตัดตรงจุดไข่ปลาต้องถูกต่อกลับ
    assert any(m["left"].startswith("Crude Protein") and m["right"] == "..7.0%"
               for m in r["row_merges"])


def test_row_merge_never_crosses_columns():
    """ต่อแถวแล้วต้องไม่มีบรรทัดไหนมีสองรายการ (เช่น 7.0% กับ Calcium) อยู่ด้วยกัน"""
    r = compare.compare(_parse("A"), _parse("B"))
    for ln in r["lines_a"] + r["lines_b"]:
        names = re.findall(r"(Protein|Calcium|Crude Fat|Phosphorus|Fiber|Omega-6|Moisture|"
                           r"Omega-3|Ash)", ln["text"])
        assert len(names) <= 1, ln["text"]


# ── ② ความคงที่: สัญญาณรบกวนที่ OCR ทำจริง ⇒ 0 จุด ─────────────────────

def test_self_compare_real_data_is_clean():
    for side in ("A", "B"):
        r = compare.compare(_parse(side), _parse(side))
        assert r["findings"] == [] and r["coverage"] == 1.0


def _leader_noise(text, rnd):
    def dots(m):
        return "." * rnd.randint(2, 16)
    t = re.sub(r"\.{2,}", dots, text)
    if t.startswith(".") and not t.startswith(".."):
        t = "." * rnd.randint(1, 3) + t[1:]
    return t


def _symbol_noise(text):
    t = text.replace("®", "\x00").replace("Ⓡ", "®").replace("\x00", "Ⓡ")
    return t.replace("1/2", "½")


@pytest.mark.parametrize("seed", range(8))
def test_ocr_representation_noise_is_invisible(seed):
    rnd = random.Random(seed)
    _, _, ls = REAL["A"]
    noisy = [(_symbol_noise(_leader_noise(t, rnd)), b, c) for t, b, c in ls]
    r = _cmp(ls, noisy)
    assert r["findings"] == [], [_sig(f) for f in r["findings"]]


def _split_at_leader(lines):
    """ตัดบรรทัด "ชื่อ......ค่า" เป็นสองบรรทัดบนแถวเดียวกัน (แบบที่ Vision ทำจริง)"""
    out = []
    for t, b, c in lines:
        m = re.search(r"\.{3,}", t)
        if m and any(ch.isdigit() for ch in t[m.end():]):
            cut = m.start() + (m.end() - m.start()) // 2
            frac = cut / float(len(t))
            x = b[0] + (b[2] - b[0]) * frac
            out.append((t[:cut], (b[0], b[1], x - 8, b[3]), c))
            out.append((t[cut:], (x + 8, b[1] + 1, b[2], b[3] - 1), c))
        else:
            out.append((t, b, c))
    return out


def test_table_rows_split_differently_by_ocr_are_invisible():
    _, _, ls = REAL["B"]
    split = _split_at_leader(ls)
    assert len(split) > len(ls)
    r = _cmp(ls, split, side="B")
    assert r["findings"] == [], [_sig(f) for f in r["findings"]]


def test_line_wrapped_at_different_word_is_invisible():
    _, _, ls = REAL["A"]
    i = next(k for k, (t, _, _) in enumerate(ls) if t.startswith("INGREDIENTS"))
    t0, b0, c0 = ls[i]
    t1, b1, c1 = ls[i + 1]
    head, last = t0.rsplit(" ", 1)
    wrapped = list(ls)
    wrapped[i] = (head, b0, c0)
    wrapped[i + 1] = (last + " " + t1, b1, c1)
    r = _cmp(ls, wrapped)
    assert [f for f in r["findings"] if f["severity"] == "red"] == []


# ── ③ การกลายพันธุ์: ทุกการแก้ต้องถูกจับ ที่บรรทัดนั้นเท่านั้น ──────────

def _mid_letter(t, pred):
    idx = [k for k, ch in enumerate(t) if pred(ch)]
    return idx[len(idx) // 2] if idx else None


def _m_case(t):
    k = _mid_letter(t, lambda ch: ch.isalpha() and ch.swapcase() != ch)
    return None if k is None else t[:k] + t[k].swapcase() + t[k + 1:]


def _m_delete(t):
    k = _mid_letter(t, str.isalpha)
    return None if k is None else t[:k] + t[k + 1:]


def _m_substitute(t):
    k = _mid_letter(t, str.isalpha)
    if k is None:
        return None
    rep = "x" if t[k].lower() != "x" else "z"
    rep = rep.upper() if t[k].isupper() else rep
    return t[:k] + rep + t[k + 1:]


def _m_digit(t):
    k = _mid_letter(t, str.isdigit)
    return None if k is None else t[:k] + str((int(t[k]) + 1) % 10) + t[k + 1:]


def _m_decimal(t):
    m = re.search(r"\d\.\d", t)
    return None if not m else t[:m.start() + 1] + t[m.start() + 2:]


def _m_drop_word(t):
    w = t.split(" ")
    return None if len(w) < 4 else " ".join(w[:len(w) // 2] + w[len(w) // 2 + 1:])


def _m_add_word(t):
    w = t.split(" ")
    return None if len(w) < 3 else " ".join(w[:len(w) // 2] + ["Extra"] + w[len(w) // 2:])


def _m_add_symbol(t):
    w = t.split(" ")
    return None if len(w) < 3 or not w[0].isalpha() else " ".join([w[0] + "®"] + w[1:])


MUTATIONS = {"case": _m_case, "delete": _m_delete, "substitute": _m_substitute,
             "digit": _m_digit, "decimal": _m_decimal, "drop_word": _m_drop_word,
             "add_word": _m_add_word, "add_symbol": _m_add_symbol}


def _cases():
    out = []
    for side in ("A", "B"):
        _, _, ls = REAL[side]
        for i, (t, _, _) in enumerate(ls):
            if len(t.replace(".", "")) < 6:
                continue
            for name, fn in MUTATIONS.items():
                mt = fn(t)
                if mt and mt != t:
                    out.append((side, i, name))
    return out


CASES = _cases()


def test_mutation_suite_is_large_enough():
    assert len(CASES) >= 400
    assert {n for _, _, n in CASES} == set(MUTATIONS)


@pytest.mark.parametrize("side,i,name", CASES,
                         ids=["%s%03d-%s" % c for c in CASES])
def test_every_mutation_is_caught_on_that_line_only(side, i, name):
    _, _, ls = REAL[side]
    t, b, c = ls[i]
    mt = MUTATIONS[name](t)
    mutated = list(ls)
    mutated[i] = (mt, b, c)
    r = _cmp(ls, mutated, side=side, conf=0.98)
    assert r["findings"], "พลาด: %r → %r" % (t, mt)
    for f in r["findings"]:
        # ต้องชี้ที่บรรทัดที่ถูกแก้ (หรือแถวที่ถูกต่อรวมกับมัน) เท่านั้น
        touched = (t in f["a"]["text"] or mt in f["b"]["text"]
                   or (f["a"]["text"] and f["a"]["text"] in t)
                   or (f["b"]["text"] and f["b"]["text"] in mt))
        assert touched, "ชี้ผิดบรรทัด: %r" % (_sig(f),)
    assert any(f["severity"] == "red" for f in r["findings"]), \
        "จับได้แต่ไม่มั่นใจ: %r → %r %r" % (t, mt, [_sig(f) for f in r["findings"]])


def test_decimal_point_is_never_treated_as_leader():
    a = [("Fat 1.5g", (10, 10, 200, 40), 0.98)]
    b = [("Fat 15g", (10, 10, 200, 40), 0.98)]
    r = _cmp(a, b)
    assert [f["severity"] for f in r["findings"]] == ["red"]
    assert r["findings"][0]["class"] == "NUMBER"


def test_missing_leader_entirely_is_still_reported_but_not_red():
    a = [("Crude Protein (min).........7.0%", (10, 10, 600, 40), 0.98)]
    b = [("Crude Protein (min) 7.0%", (10, 10, 600, 40), 0.98)]
    r = _cmp(a, b)
    assert [(f["class"], f["severity"]) for f in r["findings"]] == [("FILLER", "yellow")]


def test_row_merge_flag_off_restores_old_behaviour(monkeypatch):
    monkeypatch.setattr(config, "ROW_MERGE_ENABLED", False)
    r = compare.compare(_parse("A"), _parse("B"))
    assert r["row_merges"] == []
