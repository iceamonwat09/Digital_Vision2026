"""Artwork V2 — แยกบรรทัดที่ Vision รวมข้ามคอลัมน์ (``structure.split_merged`` · ``SPLIT_MERGED`` · 7 ต.ค. รอบ 5)

ที่มา (Friskies 🅱 B024): ``6 kcal /sachet … 100 กรัม ให้ 1 ซองต่อวันสำหรับแมวโต (…4 กก.)`` = สองคอลัมน์
ที่อยู่แถวเดียวกัน แต่ 🅰 อ่านเป็นสองบรรทัด ⇒ เดิมได้แดง 2 รายการทั้งบรรทัด (NUMBER ``ให้`` vs ``6 kcal…``
และ MISSING ``6 kcal…``) ซึ่งซ่อนความต่างจริง (``กิโลแคลอรี``/``กิโลแคลอรี่`` · ``กรัม``)

สิ่งที่ล็อก:
* แยกเมื่อชิ้นหนึ่ง **เท่ากับ** บรรทัดของอีกฝั่งทุกตัวอักษร + อยู่ตรงตำแหน่งนั้นบนภาพ + ตัดตรงช่องว่าง
  ⇒ ความต่างจริงในชิ้นที่เหลือยังแดง (ชี้ตรงตัวอักษร ไม่ใช่ทั้งบรรทัด)
* ไม่แยก: ตำแหน่งไม่ตรง (ข้อความเดียวกันพิมพ์ซ้ำที่อื่น) · ฝั่งนี้มีบรรทัดนั้นอยู่แล้ว · กลางคำ ·
  ส่วนที่เหลือสั้นเกินไป · ปิดธง
* Log มี ``row_splits`` และตัวโหลด Log ต่อชิ้นกลับ ⇒ เล่นซ้ำจาก Log ได้ผลเดิม
* ชุดข้อมูลสถานีอื่นทุกชุด: ผลเท่าเดิมทุกรายการ
"""

from __future__ import annotations

import glob
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from artwork_v2_fake import fta_from_lines, load_log  # noqa: E402

from artwork_v2 import compare, config, diaglog, textmodel  # noqa: E402

D = os.path.join(os.path.dirname(__file__), "data", "artwork_v2")
FR = os.path.join(D, "friskies", "run003_log.txt")
W, H = 1800, 900

LEFT_A = "6 kcal /sachet energy for the cat 42 kilocalorie per 100"
LEFT_B = "6 kcal /sachet energy for the cat 42 kilocalories per 100 gram"
RIGHT = "Give 1 sachet per day for adult cats (approx 4 kg)"
ANCHORS = ["Ingredients: tuna, sunflower oil, water, salt.", "Produced in Thailand for Foods Ltd,",
           "Storage: cool, dry in ventilated place.", "Net weight 85 g best before end",
           "Manufactured by Example Company Limited", "Crude protein minimum ten percent",
           "Keep refrigerated after opening the pack", "Feeding guide for adult cats daily",
           "Distributed by Example Trading Company", "Moisture maximum eighty two percent"]


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    for k in ("GEO_PAIRING", "RECOMPOSE", "RELOCATE", "SPLIT_MERGED"):
        monkeypatch.setattr(config, k, True)


def _lines(rows):
    """``rows`` = [(text, x0, y0, x1)] ⇒ บรรทัดที่ parse แล้ว (สูง 30 px)"""
    lns = [(t, (x0, y0, x1, y0 + 30), 0.98) for t, x0, y0, x1 in rows]
    return textmodel.parse(fta_from_lines(lns, W, H), W, H)["lines"]


def _anchor_rows(dx=0):
    """บรรทัดที่ตรงกันทุกตัวอักษรสองคอลัมน์ (ตัวทำนายตำแหน่ง A→B ต้องมีคู่ยึดพอ)"""
    return [(t, (60 if i % 2 == 0 else 900) + dx, 250 + 60 * (i // 2),
             (60 if i % 2 == 0 else 900) + dx + 14 * len(t)) for i, t in enumerate(ANCHORS)]


def _merged_b(right_text=RIGHT):
    t = LEFT_B + " " + right_text
    return [(t, 60, 100, 60 + 14 * len(t))]


def _two_cols_a(right_x0=None):
    x1 = 60 + 14 * len(LEFT_A)
    rx = 60 + 14 * (len(LEFT_B) + 1) if right_x0 is None else right_x0
    return [(LEFT_A, 60, 100, x1), (RIGHT, rx, 100, rx + 14 * len(RIGHT))]


def _run(rows_a, rows_b):
    return compare.compare(_lines(rows_a), _lines(rows_b), (W, H), (W, H))


def _fr(c):
    return [(f["severity"], f["class"], f["a"]["frag"], f["b"]["frag"]) for f in c["findings"]]


def test_merged_columns_are_split_and_the_real_change_is_pinpointed():
    c = _run(_two_cols_a() + _anchor_rows(), _merged_b() + _anchor_rows())
    assert [(s["side"], s["right"]) for s in c["row_splits"]] == [("B", RIGHT)]
    fr = _fr(c)
    assert not any(RIGHT[:10] in (a + b) for _, _, a, b in fr), fr      # คอลัมน์ขวาไม่ใช่ความต่าง
    assert not any(cl in ("MISSING_IN_B", "EXTRA_IN_B") for _, cl, _, _ in fr), fr
    # ความต่างจริงยังแดง และชี้ตรงจุด (s ท้าย kilocalorie · gram)
    assert any(sv == "red" and b == "s" for sv, _, _, b in fr), fr
    assert any(sv == "red" and "gram" in b for sv, _, _, b in fr), fr


def test_flag_off_keeps_the_old_whole_line_findings(monkeypatch):
    monkeypatch.setattr(config, "SPLIT_MERGED", False)
    c = _run(_two_cols_a() + _anchor_rows(), _merged_b() + _anchor_rows())
    assert c["row_splits"] == []
    assert any(cl in ("MISSING_IN_B", "EXTRA_IN_B") for _, cl, _, _ in _fr(c))


def test_no_split_when_the_matching_line_is_elsewhere_on_the_image():
    """ข้อความเดียวกันอยู่คนละที่ (เช่นพิมพ์ซ้ำในอีกแผง) — แยกแล้วจะนับซ้ำผิดตัว"""
    a = [(LEFT_A, 60, 100, 60 + 14 * len(LEFT_A)), (RIGHT, 60, 700, 60 + 14 * len(RIGHT))]
    c = _run(a + _anchor_rows(), _merged_b() + _anchor_rows())
    assert c["row_splits"] == []


def test_no_split_when_this_side_already_has_that_line():
    b = _merged_b() + [(RIGHT, 60, 850, 60 + 14 * len(RIGHT))]   # ฉบับซ้ำไกลจนตัวทำนายตำแหน่งตัดทิ้งเป็นค่าผิดปกติ
    c = _run(_two_cols_a() + _anchor_rows(), b + _anchor_rows())
    assert c["row_splits"] == []


def test_no_split_inside_a_word():
    t = LEFT_B + " " + RIGHT
    a = [(LEFT_A, 60, 100, 60 + 14 * len(LEFT_A)),
         (RIGHT[:-3], 60 + 14 * (len(LEFT_B) + 1), 100, 60 + 14 * (len(t) - 3))]
    b = [(t[:-3] + "kgx", 60, 100, 60 + 14 * len(t))]   # คีย์ขึ้นต้นด้วยชิ้นแต่รอยตัดกลางคำ
    c = compare.compare(_lines(b + _anchor_rows()), _lines(a + _anchor_rows()), (W, H), (W, H))
    assert c["row_splits"] == []


def test_no_split_when_the_rest_is_too_short():
    t = "ab " + RIGHT
    b = [(t, 60, 100, 60 + 14 * len(t))]
    a = [(RIGHT, 60 + 14 * 3, 100, 60 + 14 * len(t))]
    c = _run(a + _anchor_rows(), b + _anchor_rows())
    assert c["row_splits"] == []


def test_log_lists_splits_and_the_loader_rebuilds_the_vision_lines(tmp_path, monkeypatch):
    import io
    from PIL import Image
    from artwork_v2 import jobs, keystore, pipeline, vision_client
    d = tmp_path / "v2"
    monkeypatch.setattr(config, "DATA_DIR", str(d))
    monkeypatch.setattr(config, "JOBS_DIR", str(d / "jobs"))
    monkeypatch.setattr(config, "SECRET_DIR", str(d / "secret"))
    monkeypatch.setattr(config, "KEY_FILE", str(d / "secret" / "key.json"))
    monkeypatch.delenv(config.KEY_ENV, raising=False)
    monkeypatch.setattr(config, "PIXEL_VERIFY", False)
    os.makedirs(config.JOBS_DIR)
    rows = {"a": _two_cols_a() + _anchor_rows(), "b": _merged_b() + _anchor_rows()}

    def fake(groups, poster=None, key=None):
        res = {}
        for gi, g in enumerate(groups):
            for it in g:
                w, h = Image.open(io.BytesIO(it["jpeg"])).size
                sx, sy = w / float(W), h / float(H)
                lns = [(t, (x0 * sx, y0 * sy, x1 * sx, (y0 + 30) * sy), 0.98)
                       for t, x0, y0, x1 in rows[it["id"][-1]]]
                res[it["id"]] = {"ok": True, "error": "", "fta": fta_from_lines(lns, w, h),
                                 "request_index": gi}
        return {"results": res, "calls": [{"index": 0, "phase": "main", "images": [], "json_bytes": 1,
                                           "status": 200, "attempts": 1, "ms": 1, "error": "", "at": "t"}]}
    buf = io.BytesIO()
    Image.new("RGB", (W, H), "white").save(buf, "PNG")
    jid = jobs.create(("a.png", buf.getvalue()), ("b.png", buf.getvalue()))["id"]
    keystore.save("AIza" + "S" * 35)
    monkeypatch.setattr(vision_client, "annotate", fake)
    r = pipeline.run(jid, [{"a": {"page": 0, "bbox": [0, 0, 1, 1]}, "b": {"page": 0, "bbox": [0, 0, 1, 1]}}])
    assert r["pairs"][0]["row_splits"] and "SPLIT_MERGED=True" in r["log_text"]
    txt = r["log_text"]
    assert "row_splits (1)" in txt and "\u2016" in txt
    p = tmp_path / "log.txt"
    p.write_text(txt, encoding="utf-8")
    L = load_log(str(p))[1]
    texts = [t for t, *_ in L["B"][2]]
    assert LEFT_B + " " + RIGHT in texts and RIGHT not in texts
    # เล่นซ้ำจาก Log ได้ผลเดิม
    sides = {s: textmodel.parse(fta_from_lines(L[s][2], L[s][0], L[s][1]), L[s][0], L[s][1])["lines"]
             for s in ("A", "B")}
    c2 = compare.compare(sides["A"], sides["B"], tuple(L["A"][:2]), tuple(L["B"][:2]))
    assert len(c2["row_splits"]) == 1
    assert sorted(_fr(c2)) == sorted((f["severity"], f["class"], f["a"]["frag"], f["b"]["frag"])
                                     for f in r["pairs"][0]["findings"])


# ── ข้อมูลจริง ─────────────────────────────────────────────────────────

def _cmp_log(path, n=1):
    p = load_log(path)[n]
    sides = {s: textmodel.parse(fta_from_lines(p[s][2], p[s][0], p[s][1]), p[s][0], p[s][1])["lines"]
             for s in ("A", "B")}
    return compare.compare(sides["A"], sides["B"], tuple(p["A"][:2]), tuple(p["B"][:2]))


def test_friskies_station_line_is_split_and_the_real_thai_change_is_red():
    c = _cmp_log(FR)
    sp = [s for s in c["row_splits"] if s["right"].startswith("ให้ 1 ซอง")]
    assert sp and sp[0]["side"] == "B"
    fr = _fr(c)
    assert not any(cl == "MISSING_IN_B" and a.startswith("6 kcal") for _, cl, a, _ in fr), fr
    assert not any(b.startswith("6 kcal") for _, _, _, b in fr), fr
    assert any(sv == "red" and b == "่" for sv, _, _, b in fr), fr         # ไม้เอก (กิโลแคลอรี่)
    assert any(sv == "red" and "กรัม" in b for sv, _, _, b in fr), fr


@pytest.mark.parametrize("path", sorted(glob.glob(os.path.join(D, "station_runs", "*", "log.txt")))
                         + sorted(glob.glob(os.path.join(D, "johnwest", "*_log.txt"))))
def test_other_station_data_is_unchanged(path, monkeypatch):
    def sig(c):
        return sorted((f["severity"], f["class"], f["a"]["frag"], f["b"]["frag"])
                      for k in ("findings", "debris", "relocated") for f in c.get(k) or [])
    on = sig(_cmp_log(path))
    monkeypatch.setattr(config, "SPLIT_MERGED", False)
    assert on == sig(_cmp_log(path))
