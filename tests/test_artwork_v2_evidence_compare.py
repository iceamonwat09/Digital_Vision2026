"""Artwork V2 — ชั้นหลักฐานในการเทียบ (3 ต.ค. รอบ 4 · ไม่ยิง Vision เพิ่ม)

ที่มา: รอบ "คมสูงสุด" บนสถานี (ชุด ``station_runs/avoderm_m1m2_run003_max``) + ความแปรปรวน
ข้ามรอบของไฟล์เดียวกัน ⇒ 4 ต้นเหตุของการแจ้งเตือนปลอมที่แก้ได้ด้วยข้อมูลที่มีอยู่แล้ว:

1. ``SEAM_FILLER``    — แถวที่ต่อกลับ: จุดไข่ปลาที่รอยต่อ (``.294`` · ``(min).`` + ``..0.16%``)
                        ต้องยังเป็นเส้นตกแต่ง ไม่กลายเป็นจุดเดี่ยว
2. ``CROSS_ROW_JOIN`` — แถวที่ Vision ตัดและทิ้งจุดไข่ปลา (``Phosphorus (min)`` | ``0.16%``)
                        ต่อกลับเมื่อ **อีกฝั่งตรงทุกตัวอักษร** และรอยต่อในอีกฝั่งเป็นเส้นตกแต่ง
3. ``SYMBOL_TOKEN``   — เครื่องหมายที่เป็นคำเดี่ยว (``6286 • AvoDerm``) ไม่ใช่เรื่องตัวเลข
4. ``FRACTION_YELLOW``— ½ ที่อีกฝั่งอ่านเป็น ตัวเศษ/ตัวส่วน/หาย = FRACTION เหลืองเสมอ

ทุกชั้น: ห้ามลบ · ความต่างจริง (ตัวเลขเปลี่ยนในแถวที่ถูกตัด · ¾ แทน ½) ต้องยังแดง
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from artwork_v2_fake import fta, load_run_dir  # noqa: E402

from artwork_v2 import compare, config, textmodel  # noqa: E402

RUNS = os.path.join(os.path.dirname(__file__), "data", "artwork_v2", "station_runs")
FLAGS = ("SEAM_FILLER", "CROSS_ROW_JOIN", "SYMBOL_TOKEN", "FRACTION_YELLOW")


def L(spec, W=2000, H=600):
    return textmodel.parse(fta(spec, W, H), W, H)["lines"]


def cmp(a, b):
    return compare.compare(L(a), L(b), (2000, 600), (2000, 600))


def sigs(r):
    return sorted((f["severity"], f["class"], f["a"]["frag"], f["b"]["frag"]) for f in r["findings"])


@pytest.fixture
def flags_off(monkeypatch):
    def off(*names):
        for n in names or FLAGS:
            monkeypatch.setattr(config, n, False)
    return off


# ── 1) จุดไข่ปลาที่รอยต่อแถว ───────────────────────────────────────────

SEAM_A = [("Metabolizable Energy (calculated)", 100, 100), (".294 kcal/can", 470, 100)]
SEAM_B = [("Metabolizable Energy (calculated)", 100, 100), (".....", 450, 100),
          (".294 kcal/can", 520, 100)]


def test_seam_dot_stays_a_filler(flags_off):
    r = cmp(SEAM_A, SEAM_B)
    assert r["findings"] == [], sigs(r)
    flags_off("SEAM_FILLER")
    assert any(f["a"]["frag"] == "." for f in cmp(SEAM_A, SEAM_B)["findings"])


def test_filler_run_across_the_seam_is_one_leader():
    # ทำคีย์ทีละชิ้นต้องไม่ตัดเส้นจุดที่คร่อมรอยต่อเป็นสองท่อน ("." + "…")
    a = [("Phosphorus (min).", 100, 100), ("..0.16%", 300, 100)]
    b = [("Phosphorus (min)...........0.16%", 100, 100)]
    assert cmp(a, b)["findings"] == []


def test_a_real_decimal_after_the_seam_is_still_compared():
    # ".5" ต้นชิ้นขวาเป็นเส้นตกแต่งได้ แต่ "0.5" vs "0.6" ต้องยังแดง
    a = [("Zinc (min)......", 100, 100), ("..0.5%", 300, 100)]
    b = [("Zinc (min)......", 100, 100), ("..0.6%", 300, 100)]
    r = cmp(a, b)
    assert ("red", "NUMBER", "5", "6") in sigs(r)


# ── 2) ต่อแถวที่ถูกตัดด้วยหลักฐานจากอีกฝั่ง ─────────────────────────────

ROW_A = [("Phosphorus (min)..............0.16%", 100, 100), ("Calcium (min)........0.20%", 100, 160)]
# B: Vision ทิ้งจุดไข่ปลา ⇒ สองชิ้นห่างกัน 265 px บนแถวเดียวกัน (กว้างกว่าช่องคอลัมน์)
ROW_B = [("Phosphorus (min)", 100, 100), ("0.16%", 525, 100), ("Calcium (min)........0.20%", 100, 160)]


def test_split_row_is_joined_with_evidence_from_the_other_side(flags_off):
    r = cmp(ROW_A, ROW_B)
    assert r["findings"] == [], sigs(r)
    m = [x for x in r["row_merges"] if x.get("via") == "cross_side"]
    assert m and m[0]["side"] == "B" and m[0]["right"] == "0.16%"
    joined = [l for l in r["lines_b"] if l.get("cross_joined")]
    assert joined and any(c.get("synthetic") for c in joined[0]["chars"])
    flags_off("CROSS_ROW_JOIN")
    got = {(f["class"], f["b"]["frag"] or f["a"]["frag"]) for f in cmp(ROW_A, ROW_B)["findings"]}
    assert ("EXTRA_IN_B", "0.16%") in got


def test_split_row_with_a_real_value_change_stays_red():
    b = [("Phosphorus (min)", 100, 100), ("0.18%", 525, 100), ("Calcium (min)........0.20%", 100, 160)]
    r = cmp(ROW_A, b)
    assert not [x for x in r["row_merges"] if x.get("via")]
    assert any(f["severity"] == "red" for f in r["findings"]), sigs(r)
    assert any(f["class"] == "EXTRA_IN_B" and f["b"]["frag"] == "0.18%" for f in r["findings"])


def test_never_joins_across_columns():
    # คอลัมน์ขวามีค่าอีกตัวบนแถวเดียวกัน และไม่ใช่ค่าที่อีกฝั่งมี ⇒ ห้ามต่อ
    a = [("Phosphorus (min)..............0.16%", 100, 100), ("Iron 12 mg", 900, 100)]
    b = [("Phosphorus (min)", 100, 100), ("Iron 12 mg", 900, 100)]
    r = cmp(a, b)
    assert not [x for x in r["row_merges"] if x.get("via")]
    assert r["findings"], "แถวที่ขาดค่าต้องยังถูกรายงาน"


def test_needs_mutual_neighbours():
    # มีชิ้นอื่นคั่นกลางแถว ⇒ ไม่ใช่เพื่อนบ้านติดกัน ⇒ ไม่ต่อ
    a = [("Phosphorus (min)..............0.16%", 100, 100)]
    b = [("Phosphorus (min)", 100, 100), ("x", 400, 100), ("0.16%", 525, 100)]
    assert not [x for x in cmp(a, b)["row_merges"] if x.get("via")]


def test_no_join_without_a_leader_seam_on_the_other_side():
    # เลขใต้บาร์โค้ด "0 5290700241 0" — อีกฝั่งอ่าน "5290700241 0" (รอยต่อไม่ใช่จุดไข่ปลา)
    a = [("5290700241", 100, 100), ("0", 230, 100)]
    b = [("5290700241 0", 100, 100)]
    r = cmp(a, b)
    assert not [x for x in r["row_merges"] if x.get("via")]


def test_join_left_fragment_too():
    a = [("Ash (max)........4.0%", 100, 100)]
    b = [("Ash (max)", 100, 100), ("4.0%", 400, 100)]
    b2 = [("4.0%", 400, 100), ("Ash (max)", 100, 100)]       # ลำดับจาก Vision กลับด้าน
    for bb in (b, b2):
        r = cmp(a, bb)
        assert r["findings"] == [], sigs(r)


# ── 3) เครื่องหมายที่เป็นคำเดี่ยว ────────────────────────────────────────

def test_standalone_bullet_is_punct_not_number(flags_off):
    a = [("(866) 500-6286 AvoDerm.com", 100, 100)]
    b = [("(866) 500-6286 • AvoDerm.com", 100, 100)]
    assert [f["class"] for f in cmp(a, b)["findings"]] == ["PUNCT"]
    flags_off("SYMBOL_TOKEN")
    assert [f["class"] for f in cmp(a, b)["findings"]] == ["NUMBER"]


@pytest.mark.parametrize("a,b", [("Fat 1.5g", "Fat 15g"), ("Total 1,000 kg", "Total 1.000 kg"),
                                 ("Fat 1.5 g", "Fat 1 5 g")])
def test_punctuation_touching_digits_is_still_a_number(a, b):
    r = cmp([(a, 100, 100)], [(b, 100, 100)])
    assert r["findings"] and all(f["class"] == "NUMBER" for f in r["findings"]), sigs(r)


# ── 4) เศษส่วน ──────────────────────────────────────────────────────

FEED = "Feed adult dogs %s can per 5-10 lbs body weight per day."


@pytest.mark.parametrize("a,b", [("½-1", "1 - 1"), ("½-1", "2-1"), ("1/2 - 1", "-1"),
                                 ("2-1", "1/2 - 1"), ("1 - 1", "½-1")])
def test_fraction_misreads_are_yellow_never_red(a, b, flags_off):
    r = cmp([(FEED % a, 100, 100)], [(FEED % b, 100, 100)])
    assert [(f["class"], f["severity"]) for f in r["findings"]] == [("FRACTION", "yellow")], sigs(r)
    assert "ดูด้วยตา" in r["findings"][0]["notes"][0]
    flags_off("FRACTION_YELLOW")
    assert all(f["class"] != "FRACTION" for f in cmp([(FEED % a, 100, 100)],
                                                     [(FEED % b, 100, 100)])["findings"])


@pytest.mark.parametrize("a,b", [("½-1", "¾-1"), ("1/2 - 1", "1/3 - 1"), ("½-1", "½-2")])
def test_a_different_fraction_is_still_red(a, b):
    r = cmp([(FEED % a, 100, 100)], [(FEED % b, 100, 100)])
    assert any(f["severity"] == "red" for f in r["findings"]), sigs(r)
    assert not any(f["class"] == "FRACTION" for f in r["findings"])


def test_fraction_rule_needs_the_rest_of_the_line_to_match():
    r = cmp([(FEED % "½-1", 100, 100)], [(FEED.replace("5-10", "5-12") % "1 - 1", 100, 100)])
    assert any(f["severity"] == "red" and f["class"] == "NUMBER" for f in r["findings"]), sigs(r)


def test_dates_are_not_fractions():
    r = cmp([("EXP 06/27", 100, 100)], [("EXP 27", 100, 100)])
    assert not any(f["class"] == "FRACTION" for f in r["findings"])
    r = cmp([("Lot 7/9 B", 100, 100)], [("Lot 9 B", 100, 100)])     # 7/9 ไม่มี ½ บนฉลาก แต่ n<d
    assert all(f["severity"] == "yellow" for f in r["findings"])     # เหลือง ไม่ใช่หาย — บันทึกขอบเขตไว้


# ── ชุดข้อมูลจริง ───────────────────────────────────────────────────

def _station(name):
    p = load_run_dir(os.path.join(RUNS, name))[1]
    return compare.compare(p["A"][2], p["B"][2], p["A"][:2], p["B"][:2])


def test_station_run003_noise_drops_and_truth_stays(flags_off):
    truth = {("CASE", "c", "C"), ("TEXT", "", "s"), ("PUNCT", ",", ""), ("TEXT", "Park", ""),
             ("TEXT", "", "USA"), ("TEXT", "phate Pentahydr", "f")}
    r = _station("avoderm_m1m2_run003_max")
    got = {(f["class"], f["a"]["frag"], f["b"]["frag"]) for f in r["findings"]}
    assert truth <= got
    assert all(f["severity"] == "red" for f in r["findings"]
               if (f["class"], f["a"]["frag"], f["b"]["frag"]) in truth)
    # หายไป: แถว Phosphorus ที่ถูกตัด (2 รายการ) · จุดไข่ปลา .294 · ½ แดง
    assert not any("0.16%" in (f["a"]["frag"] + f["b"]["frag"]) for f in r["findings"])
    assert not any(f["a"]["frag"] == "." and "....." in f["b"]["frag"] for f in r["findings"])
    assert [f["severity"] for f in r["findings"] if f["class"] == "FRACTION"] == ["yellow"]
    flags_off()
    off = _station("avoderm_m1m2_run003_max")
    assert len(off["findings"]) - len(r["findings"]) == 3


def test_flags_off_gives_the_previous_findings_on_every_dataset(flags_off):
    # ตัวเลขก่อนรอบ 4 (commit 685ddcc) — ปิดทุกธงแล้วต้องได้เท่าเดิมทุกชุด
    before = {"avoderm_m1m2_run001": 7, "avoderm_m1m2_run002": 8, "avoderm_m1m2_run003_max": 17}
    flags_off()
    for name, n in before.items():
        assert len(_station(name)["findings"]) == n, name


def test_mutual_neighbour_is_required():
    # _same_row ไม่ส่งต่อ: "y" อยู่แถวเดียวกับ "0.16%" แต่ไม่ใช่แถวของ "Phosphorus (min)"
    # ⇒ เพื่อนบ้านซ้ายของ "0.16%" คือ "y" ไม่ใช่ชิ้นที่จะต่อ ⇒ ห้ามต่อ
    a = [("Phosphorus (min)..............0.16%", 100, 100)]
    b = [("Phosphorus (min)", 100, 100), ("y", 480, 116), ("0.16%", 525, 106)]
    assert not [x for x in cmp(a, b)["row_merges"] if x.get("via")]


def test_a_non_fraction_slash_is_still_a_number():
    r = cmp([("Lot 9/7 B", 100, 100)], [("Lot 7 B", 100, 100)])
    assert any(f["class"] == "NUMBER" and f["severity"] == "red" for f in r["findings"]), sigs(r)


def test_log_roundtrip_keeps_a_cross_side_join(tmp_path):
    """Log พิมพ์บรรทัดหลังต่อ (มี … สังเคราะห์) — ตัวโหลดต้องแยกกลับเป็นชิ้นดิบ แล้ว compare ต่อเอง"""
    from artwork_v2 import diaglog  # noqa: F401  (รูปแบบบรรทัดอ้างอิงจาก diaglog)
    r = cmp(ROW_A, ROW_B)
    m = [x for x in r["row_merges"] if x.get("via")][0]
    joined = [l for l in r["lines_b"] if l.get("cross_joined")][0]
    log = ["[PAIR 1] verdict=PASS", "  A: page=1 sent_px=[2000, 600]", "  B: page=1 sent_px=[2000, 600]",
           '     B: "%s" + "%s"' % (m["left"], m["right"])]
    for s, lines in (("A", r["lines_a"]), ("B", r["lines_b"])):
        for i, l in enumerate(lines):
            b = l["box"]
            log.append('   %s%03d conf=0.98 min=0.98 ang=0 box=[%d,%d,%d,%d] "%s"'
                       % (s, i, b[0], b[1], b[2], b[3], l["text"]))
    assert "…" in joined["text"]
    p = tmp_path / "log.txt"
    p.write_text("\n".join(log) + "\n", encoding="utf-8")
    ds = load_run_dir(str(tmp_path))[1]
    assert "0.16%" in [l["text"] for l in ds["B"][2]]          # แยกกลับเป็นชิ้นดิบจริง
    r2 = compare.compare(ds["A"][2], ds["B"][2], ds["A"][:2], ds["B"][:2])
    assert r2["findings"] == [] and [x for x in r2["row_merges"] if x.get("via")]
