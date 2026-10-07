"""Artwork V2 — กติกาโครงสร้างของการเทียบ (``artwork_v2/structure.py`` · 7 ต.ค. ข้อสรุปทีม)

ข้อมูลจริง: Log สถานี John West 2 รอบ (``tests/data/artwork_v2/johnwest/``) + AvoDerm ทุกชุดใน repo
เฉลย John West (ดูด้วยตาบน PDF): Sodium %DV 24% (A) vs 20% (B) · ข้อความแนวตั้ง
"Free from hydrogenated oils" + "خال من الزيوت المهدرجة" มีเฉพาะ A — นอกนั้นเหมือนกัน

สิ่งที่ล็อก:
* ปิดทุกธง = ผลเดิม · เปิด = แดงหลอกลด **โดยของจริงไม่หาย/ไม่ถูกลดระดับ**
* แต่ละกติกาทำงานตามที่บอก และไม่ไปลบความต่างจริง (ตัวเลขยังแดง)
"""

from __future__ import annotations

import glob
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from artwork_v2_fake import fta_from_lines, load_log  # noqa: E402

from artwork_v2 import compare, config, textmodel  # noqa: E402

D = os.path.join(os.path.dirname(__file__), "data", "artwork_v2")
JW = sorted(glob.glob(os.path.join(D, "johnwest", "*_log.txt")))
RULES = ("GEO_PAIRING", "RECOMPOSE", "MOVED_TEXT", "RELOCATE", "BALANCED_MOVE",
         "VERTICAL_UPRIGHT", "QUOTE_PUNCT", "SPLIT_MERGED")


def _flags(monkeypatch, on):
    for k in RULES:
        monkeypatch.setattr(config, k, on)


def _sides(path):
    p = load_log(path)[1]
    return {s: (p[s][0], p[s][1], textmodel.parse(fta_from_lines(p[s][2], p[s][0], p[s][1]),
                                                  p[s][0], p[s][1])["lines"]) for s in "AB"}


def _run(sides):
    (Wa, Ha, A), (Wb, Hb, B) = sides["A"], sides["B"]
    r = compare.compare(A, B, (Wa, Ha), (Wb, Hb))
    # pipeline: อ่านซ้ำปิด ⇒ เครื่องหมาย/ข้อความโค้งเป็นเหลือง
    for f in r["findings"]:
        if f["severity"] == "red" and (f["class"] == "PUNCT" or f.get("curved")):
            f["severity"] = "yellow"
    return r


def _jw_items(f):
    a, b = f["a"].get("text") or "", f["b"].get("text") or ""
    fa, fb = f["a"].get("frag") or "", f["b"].get("frag") or ""
    wa, wb = f.get("word_a") or "", f.get("word_b") or ""
    out = set()
    if ("475" in a + b and (("4" in fa and "0" in fb) or "24" in fa or "20" in fb)) or \
            (("24%" in wa or "%24" in wa) and ("20%" in wb or "%20" in wb)):
        out.add("sodium")
    if "Free from" in fa or "hydrogenated" in fa:
        out.add("free_en")
    if "خال من" in fa or ("المهدرجة" in fa and "الزيوت" in fa):
        out.add("free_ar")
    return out


def _score(r):
    reds = [f for f in r["findings"] if f["severity"] == "red"]
    got = {}
    for f in r["findings"]:
        for it in _jw_items(f):
            if got.get(it) != "red":
                got[it] = f["severity"]
    return [f for f in reds if not _jw_items(f)], got


@pytest.mark.parametrize("path", JW, ids=[os.path.basename(p) for p in JW])
def test_john_west_false_reds_drop_and_every_real_item_turns_red(path, monkeypatch):
    sides = _sides(path)
    _flags(monkeypatch, False)
    fr0, got0 = _score(_run(sides))
    _flags(monkeypatch, True)
    fr1, got1 = _score(_run(sides))
    assert set(got0) <= set(got1), "ของจริงหาย"
    for it, sev in got0.items():                     # ไม่มีของจริงถูกลดระดับ
        assert not (sev == "red" and got1.get(it) != "red"), it
    assert got1 == {"sodium": "red", "free_en": "red", "free_ar": "red"}, got1
    assert len(fr1) < len(fr0) and len(fr1) <= 6, [(f["class"], f["a"]["frag"], f["b"]["frag"]) for f in fr1]


def _avoderm_logs():
    out = []
    for d in sorted(glob.glob(os.path.join(D, "station_runs", "*"))):
        lg = os.path.join(d, "log.txt")
        ex = json.load(open(os.path.join(d, "expect.json"), encoding="utf-8"))
        if os.path.isfile(lg) and "1" in ex.get("pairs", {}):
            out.append((lg, ex["pairs"]["1"]["must"]))
    return out


@pytest.mark.parametrize("path,must", _avoderm_logs(),
                         ids=[os.path.basename(os.path.dirname(p)) for p, _ in _avoderm_logs()])
def test_avoderm_real_differences_keep_their_severity(path, must, monkeypatch):
    sides = _sides(path)
    _flags(monkeypatch, False)
    r0 = _run(sides)
    _flags(monkeypatch, True)
    r1 = _run(sides)
    sev = lambda r: {(f["class"], f["a"]["frag"], f["b"]["frag"]): f["severity"]  # noqa: E731
                     for f in r["findings"]}
    s0, s1 = sev(r0), sev(r1)
    for m in map(tuple, must):
        assert m in s1, ("ของจริงหาย", m)
        assert not (s0.get(m) == "red" and s1[m] != "red"), ("ถูกลดระดับ", m)
    assert sum(f["severity"] == "red" for f in r1["findings"]) <= \
        sum(f["severity"] == "red" for f in r0["findings"])


# ── กติกาทีละข้อ (ข้อมูลสังเคราะห์) ───────────────────────────────────────

def _lines(items, W=1000, H=400):
    """items = [(text, box, conf[, angle])]"""
    return textmodel.parse(fta_from_lines(items, W, H), W, H)["lines"]


BODY = [("Ingredients: tuna sunflower oil water salt", (40, 20, 600, 40), 0.98),
        ("Produced in Thailand for John West Foods", (40, 60, 620, 80), 0.98),
        ("Storage cool and dry ventilated place", (40, 100, 560, 120), 0.98),
        ("Nutritional information per serving", (40, 140, 560, 160), 0.98)]


def _cmp(a_items, b_items):
    return compare.compare(_lines(BODY + a_items), _lines(BODY + b_items), (1000, 400), (1000, 400))


def test_flags_off_is_the_old_comparator(monkeypatch):
    _flags(monkeypatch, False)
    r = _cmp([("120g", (40, 300, 90, 318), 0.98)], [("26g", (700, 220, 740, 238), 0.98)])
    assert r["relocated"] == [] and "geo" not in r["pair_methods"]
    assert not any(f["class"] == "MOVED" for f in r["findings"])


def test_geo_pairing_does_not_pair_short_numbers_far_apart(monkeypatch):
    """John West F10: "120g" (น้ำหนัก) ถูกจับคู่กับ "26g" (โปรตีน) ที่อยู่ห่างกันหลายบรรทัด
    เพราะคีย์จับคู่ ``###g`` ≈ ``##g`` — ตำแหน่งต้องเป็นตัวตัดสินสำหรับคีย์สั้น"""
    a = [("120g", (40, 300, 90, 318), 0.98)]
    b = [("26g", (700, 220, 740, 238), 0.98)]

    def mispaired(r):
        return any(f["a"]["line"] is not None and f["b"]["line"] is not None and
                   "120" in f["a"]["text"] and "26" in f["b"]["text"] for f in r["findings"])
    _flags(monkeypatch, False)
    assert mispaired(_cmp(a, b))                           # ตัวเทียบเดิมจับคู่ผิดแถว
    _flags(monkeypatch, True)
    r = _cmp(a, b)
    assert not mispaired(r)
    assert {f["class"] for f in r["findings"]} == {"MISSING_IN_B", "EXTRA_IN_B"}


def test_identical_short_text_far_away_is_not_evidence_of_the_same_item(monkeypatch):
    """คีย์สั้นที่ตรงกันทุกตัว (``جم`` ↔ ``جم`` ห่าง 9.7 บรรทัดบนสถานี) ไม่ใช่หลักฐานว่าเป็นรายการเดียวกัน
    — จับคู่ข้ามตำแหน่ง = ซ่อนรายการที่หายไปจากแถวนั้นแบบเงียบ ๆ"""
    a = [("7 g", (40, 300, 90, 318), 0.98)]
    b = [("7 g", (700, 60, 740, 78), 0.98)]
    _flags(monkeypatch, False)
    assert _cmp(a, b)["findings"] == []                     # ตัวเดิม: จับคู่เงียบ ๆ
    _flags(monkeypatch, True)
    cls = sorted(f["class"] for f in _cmp(a, b)["findings"])
    assert cls == ["EXTRA_IN_B", "MISSING_IN_B"]


RECOMP_A = [("Produced for West Foods Ltd Saturated fat", (40, 300, 700, 318), 0.98),
            ("Total carbohydrate 0 g", (40, 340, 400, 358), 0.98)]


def _recomp_b(piece="Saturated fat"):
    # B: Vision อ่าน "Saturated fat" เป็นบรรทัดแยก อยู่ท้ายลำดับการอ่าน (ไม่ติดกัน) — แบบสถานี
    return [("Produced for West Foods Ltd", (40, 300, 450, 318), 0.98),
            ("Total carbohydrate 0 g", (40, 340, 400, 358), 0.98),
            (piece, (470, 300, 700, 318), 0.98)]


def test_recompose_joins_pieces_that_equal_the_other_line_exactly(monkeypatch):
    """John West F3/F15: A อ่านรวมเป็นบรรทัดเดียว · B แยกเป็นสองชิ้น ⇒ เดิมแดง 2 จุด"""
    _flags(monkeypatch, False)
    r0 = _cmp(RECOMP_A, _recomp_b())
    assert sum(f["severity"] == "red" for f in r0["findings"]) == 2
    _flags(monkeypatch, True)
    r = _cmp(RECOMP_A, _recomp_b())
    assert r["findings"] == []
    # 7 ต.ค. รอบ 5: ตอนนี้ "แยกบรรทัดที่รวมข้ามคอลัมน์" (SPLIT_MERGED) อธิบายได้ก่อน — แยก A แทนการต่อ B
    # (สถานีจริง: "Produced … Ltd," กับ "Saturated fat" อยู่คนละคอลัมน์) · ผลเหมือนกัน: ไม่มีจุดต่าง
    assert [(x["side"], x["right"]) for x in r["row_splits"]] == [("A", "Saturated fat")]
    monkeypatch.setattr(config, "SPLIT_MERGED", False)
    r = _cmp(RECOMP_A, _recomp_b())
    assert r["findings"] == []
    assert any(m.get("via") == "recompose" for m in r["row_merges"])


def test_recompose_never_hides_a_changed_piece(monkeypatch):
    _flags(monkeypatch, True)
    r = _cmp(RECOMP_A, _recomp_b("Saturated fet"))
    assert not any(m.get("via") == "recompose" for m in r["row_merges"])
    assert any(f["severity"] == "red" for f in r["findings"])


def test_moved_block_is_yellow_but_changed_numbers_stay_red(monkeypatch):
    """F12 (ข้อความทั้งก้อนย้ายจากต้นไปท้ายบรรทัด) ⇒ MOVED เหลือง · Sodium
    ``%24 mg 475`` ↔ ``%20 475mg`` ยังแดง"""
    _flags(monkeypatch, True)
    r = _cmp([("Nutrition facts Sodium label sheet", (40, 300, 500, 318), 0.98)],
             [("Sodium label sheet Nutrition facts", (40, 300, 500, 318), 0.98)])
    assert r["findings"] and all(f["class"] == "MOVED" and f["severity"] == "yellow"
                                 for f in r["findings"])
    r = _cmp([("Sodium %24 mg 475", (40, 300, 400, 318), 0.98)],
             [("Sodium %20 475mg", (40, 300, 400, 318), 0.98)])
    assert any(f["severity"] == "red" and f["class"] == "NUMBER" for f in r["findings"])


def test_vertical_claim_is_ordinary_text_not_a_curved_emblem(monkeypatch):
    """John West: claim แนวตั้ง 90° มีเฉพาะ A — เดิมถูกนับเป็น "ข้อความโค้ง" ⇒ เหลืองทุกรอบ"""
    claim = ("Free from hydrogenated oils", (900, 20, 925, 380), 0.95, 90.0)
    _flags(monkeypatch, False)
    r0 = _cmp([claim], [])
    _flags(monkeypatch, True)
    r1 = _cmp([claim], [])
    f0 = [f for f in r0["findings"] if "hydrogenated" in f["a"]["text"]]
    f1 = [f for f in r1["findings"] if "hydrogenated" in f["a"]["text"]]
    assert f0 and f0[0].get("curved")
    assert f1 and not f1[0].get("curved") and f1[0]["severity"] == "red"


def test_tilted_emblem_is_still_curved(monkeypatch):
    _flags(monkeypatch, True)
    emb = ("OMEGA-6 FATTY ACIDS", (700, 300, 900, 360), 0.9, 25.0)
    r = _cmp([emb], [("OMEGA-b FATTY ACIDS", (700, 300, 900, 360), 0.9, 25.0)])
    assert r["findings"] and all(f.get("curved") for f in r["findings"])


def test_quote_marks_around_digits_are_punctuation(monkeypatch):
    """บาร์โค้ด ``5 "000171 054326'`` vs ``5 000171 054326`` — เดิมเป็น NUMBER"""
    _flags(monkeypatch, True)
    r = _cmp([("5 000171 054326", (40, 300, 300, 318), 0.98)],
             [("5 \"000171 054326'", (40, 300, 300, 318), 0.98)])
    assert r["findings"] and all(f["class"] == "PUNCT" for f in r["findings"])
    r = _cmp([("5 000171 054326", (40, 300, 300, 318), 0.98)],
             [("5 000171 054327", (40, 300, 300, 318), 0.98)])
    assert any(f["class"] == "NUMBER" and f["severity"] == "red" for f in r["findings"])


def test_relocated_text_is_folded_not_deleted(monkeypatch):
    """ข้อความที่ "เกินมา" ในบรรทัดหนึ่ง มีอยู่ในอีกฝั่งเป็นบรรทัดแยกตรงตำแหน่งเดียวกัน"""
    a = [("170g DRAINED WEIGHT", (40, 300, 400, 318), 0.98)]
    b = [("170g WEIGHT", (40, 300, 400, 318), 0.98), ("DRAINED", (130, 300, 260, 318), 0.98)]
    _flags(monkeypatch, True)
    r = _cmp(a, b)
    assert not [f for f in r["findings"] if f["severity"] == "red"]
    moved = r["relocated"] + [f for f in r["findings"] if f.get("cap")]
    assert moved, r["findings"]
    for f in r["relocated"]:
        assert f["severity"] == "moved" and f["notes"]
