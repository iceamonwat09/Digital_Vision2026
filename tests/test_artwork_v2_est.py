"""Artwork V2 — ตำแหน่งประมาณบนฝั่งที่ไม่พบข้อความ (``ARTWORK_V2_EST_BOX`` · 7 ต.ค. รอบ 5)

ผู้ใช้ขอ: ชี้แถว "หายไป/เกินมา" แล้วให้ภาพฝั่งที่ไม่มีข้อความซูมไปบริเวณนั้นด้วย (เดิมขึ้นแค่
"ฝั่งนี้ไม่พบบรรทัดที่ตรงกับอีกฝั่ง") ⇒ ประมาณจากบรรทัดที่ตรงกันข้างเคียง (``structure.Geo.estimate``)

สิ่งที่ล็อก:
* เล่นซ้ำข้อมูลสถานี: ลบบรรทัดออกจาก 🅱 ทีละบรรทัด ⇒ ตำแหน่งประมาณ **ทับตำแหน่งจริงทุกครั้ง**
* ตัวทำนายไม่สอดคล้องกัน ⇒ ไม่ให้ตำแหน่ง (ไม่เดา)
* เป็นข้อมูลแสดงผลล้วน — ไม่ใช่ ``box`` (หลักฐานภาพ/ผลตัดสินไม่เห็น) · ปิดธง = ไม่มีคีย์ใหม่เลย
"""

from __future__ import annotations

import glob
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from artwork_v2_fake import fta_from_lines, load_log  # noqa: E402

from artwork_v2 import compare, config, pixverify, structure, textmodel  # noqa: E402

D = os.path.join(os.path.dirname(__file__), "data", "artwork_v2")
LOGS = (sorted(glob.glob(os.path.join(D, "station_runs", "*", "log.txt")))
        + sorted(glob.glob(os.path.join(D, "johnwest", "*_log.txt")))
        + [os.path.join(D, "friskies", "run003_log.txt")])


@pytest.fixture(autouse=True)
def _flag(monkeypatch):
    monkeypatch.setattr(config, "EST_BOX", True)


def _parse(lines, W, H):
    return textmodel.parse(fta_from_lines(lines, W, H), W, H)["lines"]


@pytest.mark.parametrize("path", LOGS)
def test_estimate_lands_on_the_removed_line(path):
    d = load_log(path)[1]
    (Wa, Ha, la), (Wb, Hb, lb) = d["A"], d["B"]
    A = _parse(la, Wa, Ha)
    idx = [k for k in range(len(lb)) if len(lb[k][0].replace(" ", "")) >= 6]
    random.Random(1).shuffle(idx)
    tested = given = 0
    for k in idx[:8]:
        t, box = lb[k][0], lb[k][1]
        r = compare.compare(A, _parse(lb[:k] + lb[k + 1:], Wb, Hb), (Wa, Ha), (Wb, Hb))
        f = next((f for f in r["findings"] if f["class"] == "MISSING_IN_B"
                  and f["a"]["text"].replace(" ", "")[:6] == t.replace(" ", "")[:6]), None)
        if f is None:
            continue
        tested += 1
        e = f["b"].get("est_box")
        assert f["b"]["box"] is None and pixverify._box(f["b"]) is None   # ไม่ใช่กรอบที่วัดได้
        if e:
            given += 1
            iw = min(e[2], box[2]) - max(e[0], box[0])
            ih = min(e[3], box[3]) - max(e[1], box[1])
            assert iw > 0 and ih > 0, (t, e, box)
    assert tested >= 3 and given >= 0.6 * tested, (tested, given)


def test_disagreeing_neighbours_give_no_estimate():
    P = [((100, y, 300, y + 20), (100, y, 300, y + 20), 20.0) for y in (100, 150, 200, 250, 300)] + [

         ((400, 100, 600, 120), (400, 160, 600, 180), 20.0),      # คอลัมน์ขวาเลื่อน 60 px
         ((400, 200, 600, 220), (400, 260, 600, 280), 20.0),
         ((400, 300, 600, 320), (400, 360, 600, 380), 20.0)]
    g = structure.Geo(P)
    assert g.estimate((120, 150, 280, 170), 20) is not None             # ใกล้คอลัมน์ซ้ายล้วน
    assert g.estimate((330, 200, 370, 220), 20) is None                 # คู่ยึดสองคอลัมน์ขัดกัน


def test_flag_off_adds_nothing(monkeypatch):
    d = load_log(LOGS[0])[1]
    (Wa, Ha, la), (Wb, Hb, lb) = d["A"], d["B"]
    A, B = _parse(la, Wa, Ha), _parse(lb[1:], Wb, Hb)
    monkeypatch.setattr(config, "EST_BOX", False)
    r = compare.compare(A, B, (Wa, Ha), (Wb, Hb))
    assert not any("est_box" in f[s] for f in r["findings"] for s in ("a", "b"))
    monkeypatch.setattr(config, "EST_BOX", True)
    r2 = compare.compare(A, B, (Wa, Ha), (Wb, Hb))
    strip = [{s: {k: v for k, v in f[s].items() if k not in ("est_box", "est")} for s in ("a", "b")}
             for f in r2["findings"]]
    assert strip == [{s: f[s] for s in ("a", "b")} for f in r["findings"]]
    assert [f["severity"] for f in r2["findings"]] == [f["severity"] for f in r["findings"]]


def test_js_zooms_to_the_estimate_only_when_the_side_has_no_text():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "static", "js", "artwork_v2.js"), encoding="utf-8").read()
    assert "EST_BOX && sd && !sd.text && sd.est_box" in src
    assert "ตำแหน่งประมาณ" in src
