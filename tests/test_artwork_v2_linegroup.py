"""Artwork V2 — รวมจุดต่างในคู่บรรทัดเดียวกันเป็นแถวเดียว (6 ต.ค. · แสดงผลล้วน)

ผู้ใช้สั่ง: *"รวมจุดต่างในบรรทัดเดียวกันเป็นการ์ดเดียว"* — เคสจริง ``Hwy, Irwindale Park, CA 91706`` ↔
``Hwy Irwindale, CA 91706 USA`` ขึ้น 3 แถวแดงแยกกัน (``,`` · ``Park`` · ``USA``)

ชั้นที่ทดสอบ (ฟังก์ชันจริงจาก ``artwork_v2.js`` รันผ่าน node — ไม่ได้เขียนสูตรซ้ำ):
  ① ``lineGroups``  — จับกลุ่มเฉพาะคู่บรรทัดเดียวกัน · ไม่มีจุดไหนหายหรือซ้ำ · ระดับ = สมาชิกที่หนักที่สุด
  ② ``markedMulti`` — ไฮไลต์ทุกช่วงที่ต่างในบรรทัดเดียว (จุดแทรก · ช่วงซ้อน · escape HTML)
  ③ ``groupTarget`` — เป้าซูมของแถวกลุ่ม = กรอบที่ครอบคำของทุกสมาชิก
  ④ ธง ``ARTWORK_V2_LINE_GROUP`` + ล็อกว่า **ไม่แตะผลตรวจ/ผลตัดสิน/Log** (ฝั่งเซิร์ฟเวอร์ไม่รู้จักธงนี้เลย)
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from artwork_v2 import compare, config  # noqa: E402
from artwork_v2_fake import load_run_dir  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js", "artwork_v2.js")
HTML = os.path.join(ROOT, "templates", "artwork_v2.html")
RUNS = os.path.join(ROOT, "tests", "data", "artwork_v2", "station_runs")


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


_HARNESS = r"""
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const body = (name) => src.split('function ' + name + '(')[1].split('\n  }\n')[0];
const mk = (name, deps) => new Function(...Object.keys(deps), 'return function ' + name + '(' + body(name) + '\n}')(...Object.values(deps));
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const SEV_RANK = new Function('return ' + /const SEV_RANK = (\{[^}]*\});/.exec(src)[1])();
const lineGroups = mk('lineGroups', { SEV_RANK });
const markedMulti = mk('markedMulti', { esc });
const groupTarget = mk('groupTarget', {});
const job = JSON.parse(require('fs').readFileSync(0, 'utf8'));
console.log(JSON.stringify(job.map((c) => {
  if (c.fn === 'groups') return lineGroups(c.list);
  if (c.fn === 'mark') return markedMulti(c.text, c.spans);
  if (c.fn === 'target') return groupTarget(c.members);
  throw new Error('fn?');
})));
"""


def _run(cases):
    if not shutil.which("node"):
        pytest.skip("ไม่มี node")
    r = subprocess.run(["node", "-e", _HARNESS, "x", JS], input=json.dumps(cases),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _groups(lst):
    return _run([{"fn": "groups", "list": lst}])[0]


def _flat(rows):
    out = []
    for x in rows:
        out += [m["id"] for m in x["members"]] if x.get("group") else [x["id"]]
    return out


# ── ข้อมูลจริงจากสถานี ─────────────────────────────────────────────────

def _station_findings(name):
    ds = load_run_dir(os.path.join(RUNS, name))
    (Wa, Ha, A), (Wb, Hb, B) = ds[1]["A"], ds[1]["B"]
    fs = compare.compare(A, B, (Wa, Ha), (Wb, Hb))["findings"]
    fs = json.loads(json.dumps(fs))                     # เหมือนที่หน้าเว็บได้จาก result.json
    for i, f in enumerate(fs, 1):
        f["id"] = i
    return fs


@pytest.mark.parametrize("name", sorted(os.listdir(RUNS)) if os.path.isdir(RUNS) else [])
def test_station_irwindale_line_becomes_one_row(name):
    fs = _station_findings(name)
    rows = _groups(fs)
    grp = [x for x in rows if x.get("group")]
    assert len(grp) == 1, [[m["a"]["frag"] + "/" + m["b"]["frag"] for m in g["members"]] for g in grp]
    g = grp[0]
    assert sorted((m["a"]["frag"], m["b"]["frag"]) for m in g["members"]) == \
        sorted([(",", ""), ("Park", ""), ("", "USA")])
    assert g["severity"] == "red"
    assert g["id"] == "g%d" % g["members"][0]["id"]
    # ไม่มีจุดไหนหาย/ซ้ำ และลำดับเดิมคงอยู่ (แถวกลุ่มอยู่ตำแหน่งของสมาชิกตัวแรก)
    assert sorted(_flat(rows)) == [f["id"] for f in fs]
    assert len(rows) == len(fs) - 2
    firsts = [x["members"][0]["id"] if x.get("group") else x["id"] for x in rows]
    assert firsts == sorted(firsts)
    # สมาชิกถูกส่งกลับครบทุกฟิลด์ (ไม่ถูกตัด/แก้)
    by = {f["id"]: f for f in fs}
    for m in g["members"]:
        assert m == by[m["id"]]


# ── กติกาการจับกลุ่ม ───────────────────────────────────────────────────

def _f(i, la, lb, sev="red", ta="A line", tb="B line", **kw):
    f = {"id": i, "class": "TEXT", "severity": sev,
         "a": {"line": la, "text": ta if la is not None else "", "span": [0, 1], "frag": "x"},
         "b": {"line": lb, "text": tb if lb is not None else "", "span": [0, 1], "frag": "y"}}
    f.update(kw)
    return f


def test_only_same_line_pair_is_grouped():
    rows = _groups([_f(1, 3, 4), _f(2, 3, 5), _f(3, 3, 4), _f(4, 6, 4)])
    assert [x.get("group", False) for x in rows] == [True, False, False]
    assert [m["id"] for m in rows[0]["members"]] == [1, 3]


def test_single_member_lines_stay_as_original_objects():
    lst = [_f(1, 1, 1), _f(2, 2, 2)]
    assert _groups(lst) == lst


def test_one_sided_rows_are_never_grouped():
    lst = [_f(1, 5, None, cls="MISSING_IN_B"), _f(2, 5, None), _f(3, None, 7), _f(4, None, 7)]
    assert not any(x.get("group") for x in _groups(lst))


def test_curved_card_and_missing_id_are_never_grouped():
    curved = _f(1, 2, 2, members=[_f(9, 2, 2)])
    lst = [curved, _f(2, 2, 2), _f(None, 4, 4), _f(None, 4, 4)]
    rows = _groups(lst)
    assert not any(x.get("group") for x in rows)
    assert len(rows) == 4


def test_ai_and_algorithm_points_are_not_mixed():
    rows = _groups([_f(1, 2, 2), _f(2, 2, 2, source="ai"), _f(3, 2, 2, source="ai"), _f(4, 2, 2)])
    grp = [x for x in rows if x.get("group")]
    assert [[m["id"] for m in g["members"]] for g in grp] == [[1, 4], [2, 3]]


def test_same_line_numbers_but_different_text_are_not_grouped():
    """เลขบรรทัดตรงกันแต่ข้อความไม่ตรง (เช่นบรรทัดดิบของ AI raw กับบรรทัดของอัลกอริทึม) ⇒ คนละบรรทัด"""
    rows = _groups([_f(1, 2, 2, ta="foo"), _f(2, 2, 2, ta="bar")])
    assert not any(x.get("group") for x in rows)


@pytest.mark.parametrize("sevs,want", [
    (["yellow", "red"], "red"), (["red", "yellow"], "red"), (["yellow", "yellow"], "yellow"),
    (["debris", "yellow"], "yellow"), (["dismissed", "debris"], "debris"),
])
def test_group_severity_is_the_most_severe_member(sevs, want):
    rows = _groups([_f(i + 1, 1, 1, sev=s) for i, s in enumerate(sevs)])
    assert rows[0]["group"] and rows[0]["severity"] == want


# ── ไฮไลต์หลายช่วงในบรรทัดเดียว ──────────────────────────────────────

@pytest.mark.parametrize("text,spans,want", [
    ("Hwy, Irwindale Park, CA", [[3, 4], [15, 19]],
     'Hwy<mark class="v2-d">,</mark> Irwindale <mark class="v2-d">Park</mark>, CA'),
    ("abc", [[3, 3]], 'abc<mark class="v2-d">▏</mark>'),                 # จุดแทรกท้ายบรรทัด
    ("abc", [[1, 1], [0, 2]], '<mark class="v2-d">ab</mark>c'),          # จุดแทรกอยู่ในช่วงที่ไฮไลต์แล้ว
    ("abcdef", [[1, 4], [2, 6]], 'a<mark class="v2-d">bcd</mark><mark class="v2-d">ef</mark>'),
    ("a<b>", [[1, 4]], 'a<mark class="v2-d">&lt;b&gt;</mark>'),
    ("abc", [[5, 9]], 'abc<mark class="v2-d">▏</mark>'),                 # ช่วงเกินความยาว ⇒ ไม่ล้ม
    ("", [[0, 1]], '<span class="v2-muted">—</span>'),
])
def test_marked_multi(text, spans, want):
    assert _run([{"fn": "mark", "text": text, "spans": spans}])[0] == want


def test_marked_multi_never_loses_or_duplicates_text():
    import random
    rnd = random.Random(7)
    cases = []
    for _ in range(200):
        t = "".join(rnd.choice("ab <&>xy") for _ in range(rnd.randint(1, 20)))
        sp = []
        for _ in range(rnd.randint(0, 4)):
            a = rnd.randint(0, len(t)); b = rnd.randint(a, len(t))
            sp.append([a, b])
        cases.append({"fn": "mark", "text": t, "spans": sp})
    outs = _run(cases)
    for c, o in zip(cases, outs):
        plain = re.sub(r"<mark class=\"v2-d\">▏</mark>", "", o)
        plain = re.sub(r"</?mark[^>]*>", "", plain)
        plain = plain.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        assert plain == c["text"], (c, o)


# ── เป้าซูมของแถวกลุ่ม ────────────────────────────────────────────────

def test_group_zoom_target_is_union_of_member_words():
    m1 = {"a": {"text": "L", "word_box": [10, 5, 20, 15], "box": [12, 5, 13, 15]}, "b": {"text": "M", "word_box": None, "box": None}}
    m2 = {"a": {"text": "L", "word_box": None, "box": [40, 6, 41, 16]}, "b": {"text": "M", "word_box": [70, 2, 90, 12], "box": [70, 2, 90, 12]}}
    m3 = {"a": {"text": "L", "word_box": [25, 7, 30, 14], "box": None}, "b": {"text": "M", "word_box": None, "box": None}}
    t = _run([{"fn": "target", "members": [m1, m2, m3]}])[0]     # สมาชิกตัวท้ายเล็กกว่า ⇒ ต้องไม่หด
    assert t["a"]["word_box"] == [10, 5, 41, 16]
    assert t["b"]["word_box"] == [70, 2, 90, 12]
    assert t["a"]["text"] == "L"


def test_group_zoom_target_without_any_box_has_no_target():
    m = {"a": {"text": "L", "word_box": None, "box": None}, "b": {"text": "", "word_box": None, "box": None}}
    t = _run([{"fn": "target", "members": [m, copy.deepcopy(m)]}])[0]
    assert t["a"]["word_box"] is None and t["b"]["word_box"] is None


# ── ธง + ล็อก "แสดงผลล้วน" ──────────────────────────────────────────

def test_flag_default_on_and_reaches_the_page():
    assert config.LINE_GROUP is True
    assert 'data-line-group="{{ 1 if v2_line_group else 0 }}"' in _read(HTML)
    assert "v2_line_group=config.LINE_GROUP" in _read(os.path.join(ROOT, "artwork_v2", "routes.py"))


def test_flag_off_renders_exactly_one_row_per_point():
    js = _read(JS)
    assert 'const LINE_GROUP = root.dataset.lineGroup === "1";' in js
    assert 'if (!LINE_GROUP) return (list || []).map(findingRow).join("");' in js
    # ทุกตารางผ่าน rowsHtml (ไม่มีที่ไหนวาดแถวเองอีก)
    assert js.count(".map(findingRow)") == 1


def test_server_side_never_sees_the_flag():
    """รวมแถวเป็นเรื่องของหน้าเว็บ — ผลตรวจ ผลตัดสิน การนับจุด Log และ AI ต้องไม่รู้จักธงนี้"""
    for mod in ("compare", "pipeline", "diaglog", "ai_review", "textmodel"):
        assert "LINE_GROUP" not in _read(os.path.join(ROOT, "artwork_v2", mod + ".py")), mod


def test_zoom_and_select_use_member_ids_for_group_rows():
    js = _read(JS)
    # ภาพ: กรอบของทุกสมาชิกถูกเน้น ไม่ใช่เทียบ data-f กับ id ของแถวตรง ๆ
    assert "x.dataset.f === String(id)" not in js
    assert "el.dataset.f === String(id)" not in js
    assert js.count("ids.has(") >= 3
    assert "if (GRP[id] ? groupTarget(mem) : mem[0])" not in js     # กันเขียนผิดรูป
    assert "GRP[id] ? groupTarget(mem) : mem[0]" in js
    assert "RZ.ok.add(g)" in js
