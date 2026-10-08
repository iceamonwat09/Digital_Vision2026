"""Artwork V2 — กรอบจุดต่างบนหน้าเว็บ (4 ต.ค. · แสดงผลล้วน)

ปัญหาเดิม: กรอบเส้น 3 px (ไม่ย่อตามภาพ) รอบเฉพาะตัวอักษรที่ต่าง เผื่อแค่ 3 px ของภาพที่ส่ง
(ภาพ 3000-7000 px ถูกย่อเหลือ ~700 px ⇒ เผื่อจริง < 1 px บนจอ) ⇒ เส้นทับตัวหนังสือ และกรอบ
หุ้มแค่ ``c`` ตัวเดียวไม่พอดีกับคำ · ป้ายเลขเป็น ``<text>`` ใน SVG ⇒ ย่อตามภาพจนอ่านไม่ออก

ใหม่ (``ARTWORK_V2_BOX_STYLE=word`` ค่าเริ่มต้น): กรอบเส้นบาง 1.25 px รอบ "คำเต็ม" (``word_box``)
เผื่อตามความสูงคำ + แถบสีโปร่งบนตัวอักษรที่ต่าง (``box``) + ป้ายเลข HTML นอกกรอบ ·
``span`` = แบบเดิมทุกตัวอักษร
"""

from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from artwork_v2_fake import fta, load_run_dir  # noqa: E402

from artwork_v2 import compare, config, textmodel  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.path.join(ROOT, "tests", "data", "artwork_v2", "station_runs")
JS = os.path.join(ROOT, "static", "js", "artwork_v2.js")
HTML = os.path.join(ROOT, "templates", "artwork_v2.html")


def _line(text):
    return textmodel.parse(fta([(text, 100, 100)]), 1000, 1000)["lines"][0]


# ── ① word_box ──────────────────────────────────────────────────────

def test_word_box_covers_the_whole_word_around_a_one_letter_diff():
    ln = _line("D-calcium Pantothenate")
    s = ln["text"].index("c")
    wb, b = compare._word_box(ln, s, s + 1), compare._span_box(ln, s, s + 1)
    assert wb == compare.union(c["box"] for c in ln["chars"][0:9])        # "D-calcium"
    assert wb[0] <= b[0] and wb[2] >= b[2] and wb[1] <= b[1] and wb[3] >= b[3]
    assert wb[2] < compare.union(c["box"] for c in ln["chars"][10:11])[0]   # ไม่ล้นไปคำถัดไป


def test_word_box_spans_every_word_the_diff_touches():
    ln = _line("Copper Sulphate Pentahydrate.")
    s = ln["text"].index("phate")
    e = ln["text"].index("Pentah") + 6
    wb = compare._word_box(ln, s, e)
    assert wb == compare.union(c["box"] for c in ln["chars"][7:])


def test_insertion_point_has_no_word_box():
    """จุดแทรกอยู่ "ข้าง" คำ — ครอบคำนั้นจะชี้ว่าคำนั้นผิด (ผิดแบบมั่นใจ)"""
    ln = _line("for All Breed & Lifestages")
    k = ln["text"].index(" &")
    assert compare._word_box(ln, k, k) is None
    assert compare._span_box(ln, k, k) is not None          # จุดแทรกยังมีเครื่องหมายบาง ๆ


def test_word_box_refuses_when_text_and_chars_disagree():
    ln = dict(_line("abc def"))
    ln["text"] = ln["text"] + "x"
    assert compare._word_box(ln, 0, 1) is None


def test_station_findings_carry_word_boxes_that_contain_the_diff():
    ds = load_run_dir(os.path.join(RUNS, "avoderm_m1m2_run002"))
    (Wa, Ha, LA), (Wb, Hb, LB) = ds[1]["A"], ds[1]["B"]
    r = compare.compare(LA, LB, (Wa, Ha), (Wb, Hb))
    n = 0
    for f in r["findings"]:
        for s in "ab":
            sd = f[s]
            assert "word_box" in sd
            if sd["box"] is None or sd["span"][0] >= sd["span"][1]:
                continue
            wb, b = sd["word_box"], sd["box"]
            assert wb is not None
            assert wb[0] <= b[0] + 1e-6 and wb[2] >= b[2] - 1e-6
            n += 1
    assert n >= 8
    case = next(f for f in r["findings"] if f["class"] == "CASE")
    a = case["a"]
    assert a["word_box"][2] - a["word_box"][0] > 4 * (a["box"][2] - a["box"][0])   # c → D-calcium


def test_whole_line_missing_frames_the_line():
    A = textmodel.parse(fta([("Net weight 85 g", 50, 50), ("Made in Thailand", 50, 100)]), 900, 700)["lines"]
    B = textmodel.parse(fta([("Net weight 85 g", 50, 50)]), 900, 700)["lines"]
    r = compare.compare(A, B, (900, 700), (900, 700))
    miss = next(f for f in r["findings"] if f["class"] == "MISSING_IN_B")
    assert miss["a"]["word_box"] == miss["a"]["box"] and miss["b"]["word_box"] is None


# ── ② ค่าตั้ง + หน้าเว็บ ─────────────────────────────────────────────

def test_box_style_flag(monkeypatch):
    assert config.BOX_STYLE == "word"
    try:
        monkeypatch.setenv("ARTWORK_V2_BOX_STYLE", "span")
        assert importlib.reload(config).BOX_STYLE == "span"
        monkeypatch.setenv("ARTWORK_V2_BOX_STYLE", "อะไรก็ได้")
        assert importlib.reload(config).BOX_STYLE == "word"
    finally:
        monkeypatch.delenv("ARTWORK_V2_BOX_STYLE", raising=False)
        importlib.reload(config)


def test_page_passes_style_and_css_draws_thin_non_scaling_lines():
    html = open(HTML, encoding="utf-8").read()
    assert 'data-box-style="{{ v2_box_style }}"' in html
    m = re.search(r'#v2Root\[data-box-style="word"\] \.v2-res-stage rect\.f \{([^}]*)\}', html)
    assert m and "stroke-width:1.25" in m.group(1)
    assert re.search(r"\.v2-res-stage rect\.f \{[^}]*vector-effect:non-scaling-stroke", html)
    assert re.search(r"\.v2-res-stage rect\.d \{[^}]*stroke:none", html)     # แถบสีไม่มีเส้นทับตัวอักษร
    assert ".v2-tag {" in html and "pointer-events:none" in html.split(".v2-tag {")[1].split("}")[0]
    js = open(JS, encoding="utf-8").read()
    # แบบเดิมยังอยู่ครบ (ARTWORK_V2_BOX_STYLE=span)
    assert "const pad = 3;" in js.split("function spanFrame")[1].split("\n  }\n")[0]


_JS_HARNESS = r"""
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const body = src.split('function wordFrame')[1].split('\n  }\n')[0];
const esc = (s) => String(s);
const FRAME_PAD = Number(/const FRAME_PAD = ([0-9.]+)/.exec(src)[1]);
const wordFrame = new Function('esc', 'FRAME_PAD', 'return function wordFrame' + body + '\n}')(esc, FRAME_PAD);
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
console.log(JSON.stringify(cases.map((c) => wordFrame(c.f, 'a', 1000, 800))));
"""


def _frames(cases):
    if not shutil.which("node"):
        pytest.skip("ไม่มี node")
    r = subprocess.run(["node", "-e", _JS_HARNESS, "x", JS], input=json.dumps(cases),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _rects(svg):
    return [dict(re.findall(r'(\w+)="([^"]*)"', m)) for m in re.findall(r"<rect ([^>]*)/>", svg)]


def test_frame_sits_outside_the_glyphs_and_marks_the_diff_inside():
    wb, b = [100, 200, 300, 240], [150, 202, 170, 238]              # คำสูง 40 · ต่าง 1 ตัว
    out = _frames([{"f": {"id": 7, "severity": "red", "a": {"box": b, "word_box": wb}}}])[0]
    fr, d = _rects(out["svg"])
    assert fr["class"] == "f red" and d["class"] == "d red"
    pad = 40 * 0.22
    assert float(fr["x"]) == pytest.approx(100 - pad) and float(fr["y"]) == pytest.approx(200 - pad)
    assert float(fr["width"]) == pytest.approx(200 + 2 * pad)
    assert float(d["x"]) == 150 and float(d["width"]) == 20
    assert float(d["y"]) == 200 and float(d["height"]) == 40            # แถบสูงเท่าคำ
    assert 'class="v2-tag red"' in out["tag"] and ">7<" in out["tag"]


def test_whole_word_diff_has_frame_only_and_insertion_gets_a_marker():
    wb = [100, 200, 300, 240]
    whole, ins, old = _frames([
        {"f": {"id": 1, "severity": "red", "a": {"box": wb, "word_box": wb}}},
        {"f": {"id": 2, "severity": "yellow", "a": {"box": [199, 200, 201, 240], "word_box": None}}},
        {"f": {"id": 3, "severity": "red", "a": {"box": [150, 202, 170, 238]}}},      # ผลรุ่นก่อน
    ])
    assert len(_rects(whole["svg"])) == 1
    r = _rects(ins["svg"])
    assert [x["class"] for x in r] == ["f yellow", "d yellow ins"]
    assert len(_rects(old["svg"])) == 1                                    # ไม่มี word_box ⇒ กรอบอย่างเดียว


def test_label_goes_below_when_the_word_touches_the_top_edge():
    out = _frames([{"f": {"id": 4, "severity": "red", "a": {"box": [10, 2, 40, 22], "word_box": [10, 2, 40, 22]}}}])[0]
    assert "below" in out["tag"]
