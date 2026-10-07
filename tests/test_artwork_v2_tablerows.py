"""ตารางจุดต่างของ Artwork V2: เรียงแดงก่อน + แสดง N แถวแล้วเลื่อนในตาราง (7 ต.ค. · แสดงผลล้วน)

① ``bySeverity`` — แดง → เหลือง → เศษ → พับ · ลำดับเดิมภายในระดับเดียวกัน · ไม่มีแถวหาย/ซ้ำ · ธงปิด = ลำดับเดิม
② แถวกลุ่ม (รวมบรรทัดเดียวกัน) เรียงตามระดับของกลุ่ม
③ ธง ``ARTWORK_V2_SORT_SEVERITY`` / ``ARTWORK_V2_TABLE_ROWS`` ไปถึงหน้าเว็บ · ฝั่งเซิร์ฟเวอร์ไม่รู้จัก
④ ``fitRows``: ไม่บอกว่า "แสดง N" เมื่อแถวสูงจนชนเพดานจอ · เฉพาะตารางหลัก (รายการพับไม่ถูกจำกัด)
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from artwork_v2 import config  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js", "artwork_v2.js")
HTML = os.path.join(ROOT, "templates", "artwork_v2.html")


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


_HARNESS = r"""
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const body = (name) => src.split('function ' + name + '(')[1].split('\n  }\n')[0];
const mk = (name, deps) => new Function(...Object.keys(deps), 'return function ' + name + '(' + body(name) + '\n}')(...Object.values(deps));
const SEV_RANK = new Function('return ' + /const SEV_RANK = (\{[^}]*\});/.exec(src)[1])();
const job = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const lineGroups = mk('lineGroups', { SEV_RANK });
console.log(JSON.stringify(job.map((c) => {
  const bySeverity = mk('bySeverity', { SEV_RANK, SORT_SEVERITY: c.on });
  const xs = c.group ? lineGroups(c.list) : c.list;
  return bySeverity(xs).map((f) => [String(f.id), f.severity]);
})));
"""


def _run(cases):
    if not shutil.which("node"):
        pytest.skip("ไม่มี node")
    r = subprocess.run(["node", "-e", _HARNESS, "x", JS], input=json.dumps(cases),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _f(i, sev, line=None, text="t"):
    return {"id": i, "severity": sev, "class": "TEXT",
            "a": {"line": line if line is not None else i, "text": text + str(i), "span": [0, 1]},
            "b": {"line": line if line is not None else i, "text": text + str(i) + "x", "span": [0, 1]}}


MIX = [_f(1, "yellow"), _f(2, "red"), _f(3, "yellow"), _f(4, "red"), _f(5, "debris"), _f(6, "red"), _f(7, "dismissed")]


def test_red_first_then_yellow_stable_within_level():
    got = _run([{"on": True, "list": MIX}])[0]
    assert got == [["2", "red"], ["4", "red"], ["6", "red"], ["1", "yellow"], ["3", "yellow"],
                   ["5", "debris"], ["7", "dismissed"]]


def test_flag_off_keeps_original_order():
    got = _run([{"on": False, "list": MIX}])[0]
    assert [x[0] for x in got] == [str(f["id"]) for f in MIX]


def test_no_row_lost_or_duplicated_random():
    import random
    rnd = random.Random(7)
    cases = []
    for _ in range(40):
        n = rnd.randint(0, 25)
        cases.append({"on": True, "list": [_f(i, rnd.choice(["red", "yellow", "debris", "dismissed", "weird"]))
                                          for i in range(1, n + 1)]})
    for c, got in zip(cases, _run(cases)):
        assert sorted(int(x[0]) for x in got) == [f["id"] for f in c["list"]]
        rank = {"red": 4, "yellow": 3, "debris": 2, "dismissed": 1}
        rs = [rank.get(x[1], 0) for x in got]
        assert rs == sorted(rs, reverse=True)
        # ภายในระดับเดียวกัน = ลำดับเดิม
        for r in set(rs):
            ids = [int(x[0]) for x in got if rank.get(x[1], 0) == r]
            assert ids == sorted(ids)


def test_grouped_row_sorted_by_group_severity():
    # 1 (เหลือง) · 2+3 บรรทัดเดียวกัน (เหลือง+แดง ⇒ กลุ่มแดง) · 4 (เหลือง)
    lst = [_f(1, "yellow"), _f(2, "yellow", line=9, text="L"), _f(3, "red", line=9, text="L"), _f(4, "yellow")]
    lst[2]["a"]["text"] = lst[1]["a"]["text"]
    lst[2]["b"]["text"] = lst[1]["b"]["text"]
    got = _run([{"on": True, "group": True, "list": lst}])[0]
    assert got[0] == ["g2", "red"]
    assert [x[0] for x in got[1:]] == ["1", "4"]


def test_flags_default_and_reach_the_page():
    assert config.SORT_SEVERITY is True
    assert config.TABLE_ROWS == 4
    routes = _read(os.path.join(ROOT, "artwork_v2", "routes.py"))
    assert "v2_sort_severity=config.SORT_SEVERITY" in routes
    assert "v2_table_rows=config.TABLE_ROWS" in routes
    html = _read(HTML)
    assert 'data-sort-severity="{{ 1 if v2_sort_severity else 0 }}"' in html
    assert 'data-table-rows="{{ v2_table_rows }}"' in html
    assert ".v2-tbl-wrap.v2-capped thead th { position:sticky;" in html


def test_server_side_never_sees_the_flags():
    for mod in ("compare", "pipeline", "diaglog", "ai_review", "textmodel", "vision_client"):
        src = _read(os.path.join(ROOT, "artwork_v2", mod + ".py"))
        assert "SORT_SEVERITY" not in src and "TABLE_ROWS" not in src, mod


def test_every_table_goes_through_bySeverity_and_only_main_table_is_capped():
    js = _read(JS)
    assert js.count(".map(findingRow)") == 1
    assert "return bySeverity(lineGroups(list)).map(" in js
    assert 'if (!LINE_GROUP) return bySeverity(list).map(findingRow).join("");' in js
    assert js.count("v2-tbl-wrap v2-main") == 1                      # รายการพับไม่ถูกจำกัด
    assert "fitRows();" in js and "if (!TABLE_ROWS) return;" in js


def test_hint_never_claims_n_rows_when_screen_cap_hits():
    js = _read(JS)
    body = js.split("function fitRows(")[1].split("\n  }\n")[0]
    assert "h > lim ?" in body and '"ตารางมี "' in body
    assert "window.innerHeight * 0.6" in body
