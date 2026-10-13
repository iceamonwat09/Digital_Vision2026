"""Artwork V2 — ความต่างเรื่องเดียวที่ขึ้นสองการ์ด (``SAME_CHANGE``) + รอยต่อเป็นเหลือง (``SEAM_YELLOW``) · 13 ต.ค.

ที่มา (Log สถานี ``tests/data/artwork_v2/same_change/``):
* John West run_004 — B ``٤٧٥ ملجم %24`` = A ``٤٧٥ ملجم`` ⊕ A ``20%`` ⇒ ขึ้น F11 (``%24`` เกิน) + F12 (``20%`` หาย) =
  การแก้ Sodium **เรื่องเดียว** (ต่างจริง ต้องแดงทั้งคู่)
* King Oscar run_001 — A ``…TH 2077yby certyfikowane…`` = B063 ⊕ B064 ที่ ``R`` หายตรงรอยต่อ ⇒ F7 แดง + F11 เหลือง
  ทั้งที่ข้อความเหมือนกันทุกตัวอักษร (ภาพถ่าย — ดูด้วยตาแล้ว)

สิ่งที่ล็อก:
  ① ข้อมูลสถานี — จับได้ทั้งสองเคส · ชุดข้อมูลอื่นทุกชุด **ไม่ถูกจับ และผลเท่าเดิมทุกตัวอักษร**
  ② ด่านทีละข้อ (กลายพันธุ์สังเคราะห์) — ความต่างอิสระสองเรื่อง/หน่วยเปลี่ยน/ตำแหน่งไกล/กำกวม/ส่วนที่เหลือต่าง/
     รอยต่อต่าง > 1 ตัว/ข้อความโค้ง/คู่ยึดไม่พอ ⇒ **ไม่จับ** (สองการ์ดแบบเดิม)
  ③ ธง — ปิดแล้วเท่าเดิมทุกไบต์ · แยกกันได้
  ④ pipeline + Log · AI โหมด image ตอบ "ต่าง" บนรอยต่อ ⇒ เหลือง
  ⑤ หน้าเว็บ (ฟังก์ชันจริงผ่าน node) — แถวเดียวต่อกลุ่ม · แต่ละฝั่งแสดงบรรทัดของชิ้นที่ต่าง
"""

from __future__ import annotations

import copy
import glob
import gzip
import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from artwork_v2_fake import fta, fta_from_lines, load_log, load_run_dir  # noqa: E402

from artwork_v2 import ai_review, compare, config, diaglog, pipeline, structure, textmodel  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(ROOT, "tests", "data", "artwork_v2")
JW4 = os.path.join(D, "same_change", "johnwest_run004_log.txt")
KO1 = os.path.join(D, "same_change", "kingoscar_run001_log.txt")
JS = os.path.join(ROOT, "static", "js", "artwork_v2.js")


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    for k in ("SAME_CHANGE", "SEAM_YELLOW"):
        monkeypatch.setattr(config, k, True)


def _parse(lines, W, H, rtl=False):
    return textmodel.parse(fta_from_lines(lines, W, H, rtl=rtl), W, H)["lines"]


def _log_sides(path, rtl=True):
    p = load_log(path)[1]
    return {s: (p[s][0], p[s][1], _parse(p[s][2], p[s][0], p[s][1], rtl)) for s in "AB"}


def _cmp(sd):
    (Wa, Ha, A), (Wb, Hb, B) = sd["A"], sd["B"]
    return compare.compare(copy.deepcopy(A), copy.deepcopy(B), (Wa, Ha), (Wb, Hb))


def _off(monkeypatch):
    monkeypatch.setattr(config, "SAME_CHANGE", False)
    monkeypatch.setattr(config, "SEAM_YELLOW", False)


def _dump(fs):
    return [json.dumps(f, sort_keys=True, ensure_ascii=False, default=str) for f in fs]


def _strip(fs):
    return _dump([{k: v for k, v in f.items() if k not in ("same_change", "same_change_role", "seam")}
                  for f in fs])


# ── ① ข้อมูลสถานี ───────────────────────────────────────────────────────

def test_john_west_sodium_is_one_change_and_stays_red():
    r = _cmp(_log_sides(JW4))
    assert len(r["same_change"]) == 1
    rec = r["same_change"][0]
    assert rec["kind"] == "digits" and rec["merged_side"] == "B" and rec["piece"] == "%24"
    assert rec["line"] == "20%" and rec["seam"] == "" and not rec["seam_yellow"]
    ms = [f for f in r["findings"] if f.get("same_change") == rec["group"]]
    assert sorted(f["class"] for f in ms) == ["MISSING_IN_B", "NUMBER"]
    assert all(f["severity"] == "red" for f in ms)          # การแก้จริง — ห้ามถูกลดระดับ
    assert not any(f.get("seam") for f in ms)


def test_king_oscar_seam_r_is_one_change_and_turns_yellow(monkeypatch):
    r = _cmp(_log_sides(KO1))
    assert len(r["same_change"]) == 1
    rec = r["same_change"][0]
    assert rec["kind"] == "layout" and rec["merged_side"] == "A" and rec["seam"] == "R"
    assert rec["piece"] == rec["line"] == "Numer identyfikacyjny zakładu: TH 2077" and rec["seam_yellow"]
    ms = [f for f in r["findings"] if f.get("same_change") == rec["group"]]
    assert sorted(f["class"] for f in ms) == ["EXTRA_IN_B", "NUMBER"]
    assert all(f["severity"] == "yellow" and f["seam"] for f in ms)
    assert all(any("รอยต่อ" in n and "“R”" in n for n in f["notes"]) for f in ms)
    _off(monkeypatch)
    r0 = _cmp(_log_sides(KO1))
    x0 = [f for f in r0["findings"] if f["a"]["frag"] == rec["piece"]][0]
    assert x0["severity"] == "red"                          # ก่อนแก้ = แดงหลอก (ตามที่สถานีเห็น)


def _all_station():
    for p in sorted(glob.glob(os.path.join(D, "*", "*_log.txt"))):
        for rtl in (False, True):
            for n, sd in load_log(p).items():
                yield "%s#%d rtl=%s" % (os.path.relpath(p, D), n, rtl), \
                    {s: (sd[s][0], sd[s][1], _parse(sd[s][2], sd[s][0], sd[s][1], rtl)) for s in "AB"}
    for p in sorted(glob.glob(os.path.join(D, "station_runs", "*"))):
        for n, sd in load_run_dir(p).items():
            yield "%s#%d" % (os.path.relpath(p, D), n), sd
    rp = os.path.join(D, "friskies", "raw_20261008_213541")
    sd = {}
    for f in sorted(os.listdir(rp)):
        if f.startswith("p1_") and f.endswith(".json.gz"):
            data = json.load(gzip.open(os.path.join(rp, f), "rt", encoding="utf-8"))
            data = data.get("fullTextAnnotation", data)
            pg = data["pages"][0]
            W, H = int(pg["width"]), int(pg["height"])
            sd[f[3].upper()] = (W, H, textmodel.parse(data, W, H)["lines"])
    yield "friskies/raw", sd


def test_every_station_dataset_only_the_two_known_groups(monkeypatch):
    seen = {}
    for name, sd in _all_station():
        _off(monkeypatch)
        r0 = _cmp(sd)
        monkeypatch.setattr(config, "SAME_CHANGE", True)
        monkeypatch.setattr(config, "SEAM_YELLOW", True)
        r1 = _cmp(sd)
        seen[name] = len(r1["same_change"])
        changed = [i for i, (a, b) in enumerate(zip(_strip(r0["findings"]), _strip(r1["findings"]))) if a != b]
        assert len(r0["findings"]) == len(r1["findings"]), name
        if "kingoscar" in name:
            # เฉพาะสองสมาชิกของกลุ่มรอยต่อที่เปลี่ยน (ระดับ + หมายเหตุ)
            ids = [i for i, f in enumerate(r1["findings"]) if f.get("seam")]
            assert changed == ids and len(ids) == 2, name
        else:
            assert changed == [], name
    expect = {k: (1 if ("kingoscar" in k or ("johnwest_run004" in k and "rtl=True" in k)) else 0) for k in seen}
    assert seen == expect


# ── ② ด่านทีละข้อ (สังเคราะห์) ─────────────────────────────────────────

W = H = 1000
ANCHORS = [("INGREDIENTS: TUNA WATER SALT", (50, 60, 600, 80)),
           ("NUTRITION INFORMATION PER SERVING", (50, 110, 650, 130)),
           ("Store in a cool and dry place", (50, 820, 600, 840)),
           ("Manufactured by Example Company Ltd", (50, 880, 700, 900))]


def _sides(a_lines, b_lines, angle_a=None):
    a = [(t, b, 0.98) for t, b in ANCHORS] + [(t, b, 0.98, angle_a) for t, b in a_lines]
    b = [(t, b_, 0.98) for t, b_ in ANCHORS] + [(t, b_, 0.98) for t, b_ in b_lines]
    return {"A": (W, H, _parse(a, W, H)), "B": (W, H, _parse(b, W, H))}


# John West แบบสังเคราะห์: B รวม "Energy 170 kcal" + "24%" (แถวเดียวกัน) · A แยกเป็นสองบรรทัด
JW_A = [("Energy 170 kcal", (100, 400, 300, 420)), ("20%", (330, 400, 375, 420))]
JW_B = [("Energy 170 kcal 24%", (100, 400, 375, 420))]
# King Oscar แบบสังเคราะห์: A รวมสองแถวเป็นบรรทัดเดียว (R หายตรงรอยต่อ) · B สองบรรทัด
KO_B = [("Numer zakladu TH 2077", (100, 400, 400, 420)),
        ("Ryby certyfikowane zgodnie z wymogami MSC", (100, 425, 600, 445))]
KO_A = [("Numer zakladu TH 2077yby certyfikowane zgodnie z wymogami MSC", (100, 400, 600, 445))]


def _groups(r):
    return [(x["kind"], x["merged_side"], x["piece"], x["seam"]) for x in r["same_change"]]


def test_synthetic_digit_change_groups_and_stays_red():
    r = _cmp(_sides(JW_A, JW_B))
    assert _groups(r) == [("digits", "B", "24%", "")]
    ms = [f for f in r["findings"] if f.get("same_change")]
    assert len(ms) == 2 and all(f["severity"] == "red" for f in ms)


def test_synthetic_seam_layout_groups_and_turns_yellow():
    r = _cmp(_sides(KO_A, KO_B))
    assert _groups(r) == [("layout", "A", "Numer zakladu TH 2077", "R")]
    ms = [f for f in r["findings"] if f.get("same_change")]
    assert len(ms) == 2 and all(f["severity"] == "yellow" and f["seam"] for f in ms)


def test_price_a_real_lost_first_letter_at_the_seam_is_yellow_not_red():
    """ราคาที่ผู้ใช้รับทราบ: ตัวแรกของบรรทัดล่างหายจริงตรงรอยต่อพอดี แยกจาก OCR ทำหายไม่ได้ ⇒ เหลือง (ยังอยู่ในตาราง)"""
    r = _cmp(_sides(KO_A, KO_B))
    assert all(f["severity"] == "yellow" for f in r["findings"] if f.get("same_change"))
    assert len([f for f in r["findings"] if f.get("same_change")]) == 2      # ไม่หาย ไม่พับ


@pytest.mark.parametrize("a_lines,b_lines,why", [
    # ความต่างอิสระสองเรื่องในแถวข้างเคียง (ชิ้นเกินคนละข้อความกับบรรทัดที่หาย)
    ([("Energy 170 kcal", (100, 400, 300, 420)), ("Sodium 20%", (330, 400, 450, 420))],
     [("Energy 170 kcal Fiber 2%", (100, 400, 450, 420))], "content"),
    # หน่วยเปลี่ยน — ไม่ใช่ "ต่างแค่ตัวเลข"
    ([("Energy 170 kcal", (100, 400, 300, 420)), ("20%", (330, 400, 375, 420))],
     [("Energy 170 kcal 24mg", (100, 400, 390, 420))], "unit"),
    # ชิ้นเกินสั้น (≤ 2 ตัว ⇒ ``SPLIT_MERGED`` ไม่แยก) แต่ข้อความต่าง: ตัวอักษร O แทนเลข 0 / หน่วย kg ↔ mg
    ([("Energy 170 kcal", (100, 400, 300, 420)), ("20%", (330, 400, 375, 420))],
     [("Energy 170 kcal 2O%", (100, 400, 375, 420))], "letter-for-digit"),
    ([("Energy 170 kcal", (100, 400, 300, 420)), ("mg", (330, 400, 360, 420))],
     [("Energy 170 kcal kg", (100, 400, 360, 420))], "unit-short"),
    # บรรทัดที่หายอยู่ไกล (คนละที่บนภาพ)
    ([("Energy 170 kcal", (100, 400, 300, 420)), ("20%", (700, 650, 745, 670))],
     [("Energy 170 kcal 24%", (100, 400, 375, 420))], "far"),
    # ส่วนที่เหลือของบรรทัดต่างด้วย (ไม่ใช่แค่การรวมบรรทัด)
    ([("Energy 170 kcal", (100, 400, 300, 420)), ("20%", (330, 400, 375, 420))],
     [("Energy 180 kcal 24%", (100, 400, 375, 420))], "rest"),
    # รอยต่อต่างเกิน 1 ตัว
    ([("Numer zakladu TH 2077by certyfikowane zgodnie z wymogami MSC", (100, 400, 600, 445))],
     KO_B, "seam2"),
])
def test_gates_refuse(a_lines, b_lines, why):
    r = _cmp(_sides(a_lines, b_lines))
    assert r["same_change"] == [], why
    assert not any(f.get("same_change") or f.get("seam") for f in r["findings"]), why


def test_ambiguous_two_candidates_are_not_grouped():
    # บรรทัด "20%" ซ้ำสองบรรทัดที่ตำแหน่งเดียวกัน (Vision อ่านซ้อน) ⇒ ไม่รู้ว่าคู่ไหน ⇒ ไม่จับ
    a = JW_A + [("20%", (331, 401, 376, 421))]
    r = _cmp(_sides(a, JW_B))
    assert r["same_change"] == []


def test_curved_lines_are_not_grouped():
    r = _cmp(_sides(KO_A, KO_B, angle_a=25.0))
    assert r["same_change"] == []


def test_needs_three_anchor_lines():
    a = [(t, b, 0.98) for t, b in ANCHORS[:1]] + [(t, b, 0.98) for t, b in KO_A]
    b = [(t, b_, 0.98) for t, b_ in ANCHORS[:1]] + [(t, b_, 0.98) for t, b_ in KO_B]
    r = _cmp({"A": (W, H, _parse(a, W, H)), "B": (W, H, _parse(b, W, H))})
    assert r["same_change"] == []


def test_short_piece_cases_really_reach_the_content_gate():
    """กันเทสต์ตาบอด: ถ้าไม่มีด่านข้อความ เคส ``2O%``/``kg`` ต้องถูกจับ (ด่านอื่นผ่านหมด)"""
    for a, b, f_, u in ((JW_A, [("Energy 170 kcal 2O%", (100, 400, 375, 420))], "2O%", "20%"),
                        ([("Energy 170 kcal", (100, 400, 300, 420)), ("mg", (330, 400, 360, 420))],
                         [("Energy 170 kcal kg", (100, 400, 360, 420))], "kg", "mg")):
        r = _cmp(_sides(a, b))
        A, B = r["lines_a"], r["lines_b"]
        xs = [x for x in r["findings"] if structure._merged_piece(x, A, B)]
        ys = [y for y in r["findings"] if y["pair_method"] == "unpaired" and y["a"]["text"] == u]
        assert len(xs) == 1 and len(ys) == 1 and structure._merged_piece(xs[0], A, B)[1] == f_


def test_seam_must_be_at_the_same_edge_on_both_lines():
    """ชิ้นเกินอยู่หัวบรรทัด M แต่ตัวที่ต่างอีกฝั่งอยู่ท้ายบรรทัด P = ไม่ใช่รอยต่อของการรวมบรรทัด"""
    A = [{"text": "Numer TH 2077 Ryby MSC"}]
    B = [{"text": "Ryby MSC X"}]
    x = {"class": "NUMBER", "pair_method": "content",
         "a": {"line": 0, "text": A[0]["text"], "span": [0, 13], "frag": "Numer TH 2077", "box": (0, 0, 1, 1)},
         "b": {"line": 0, "text": B[0]["text"], "span": [9, 10], "frag": "X", "box": (0, 0, 1, 1)}}
    assert structure._merged_piece(x, A, B) is None
    x["b"].update(text="X Ryby MSC", span=[0, 1])
    B[0]["text"] = "X Ryby MSC"
    assert structure._merged_piece(x, A, B) == ("a", "NumerTH2077", "X")


def test_skeleton_ignores_order_but_not_digit_count():
    assert structure._skel("%24") == structure._skel("20%")
    assert structure._skel("%245") != structure._skel("20%")
    assert structure._skel("24mg") != structure._skel("20%")


# ── ③ ธง ─────────────────────────────────────────────────────────────────

def test_flags_off_byte_identical(monkeypatch):
    for sd in (_sides(KO_A, KO_B), _sides(JW_A, JW_B), _log_sides(KO1), _log_sides(JW4)):
        _off(monkeypatch)
        r0 = _cmp(sd)
        monkeypatch.setattr(config, "GEO_PAIRING", config.GEO_PAIRING)   # ไม่แตะธงอื่น
        assert r0["same_change"] == []
        assert not any("same_change" in f or "seam" in f for f in r0["findings"])
        monkeypatch.setattr(config, "SAME_CHANGE", True)
        monkeypatch.setattr(config, "SEAM_YELLOW", True)
        r1 = _cmp(sd)
        assert len(r0["findings"]) == len(r1["findings"])


def test_seam_without_grouping_and_grouping_without_seam(monkeypatch):
    monkeypatch.setattr(config, "SAME_CHANGE", False)
    r = _cmp(_sides(KO_A, KO_B))
    assert not any("same_change" in f for f in r["findings"])
    assert sum(1 for f in r["findings"] if f.get("seam") and f["severity"] == "yellow") == 2
    monkeypatch.setattr(config, "SAME_CHANGE", True)
    monkeypatch.setattr(config, "SEAM_YELLOW", False)
    r = _cmp(_sides(KO_A, KO_B))
    ms = [f for f in r["findings"] if f.get("same_change")]
    assert len(ms) == 2 and not any(f.get("seam") for f in ms)
    assert any(f["severity"] == "red" for f in ms)


# ── ④ pipeline (หมายเหตุอ้างเลขจุด) + Log + AI โหมด image ──────────────────

def test_note_names_the_twin_id_and_log_lines():
    r = _cmp(_sides(KO_A, KO_B))
    pr = {"n": 1, "findings": r["findings"], "same_change": r["same_change"]}
    for i, f in enumerate(pr["findings"]):
        f["id"] = 10 + i
    pipeline._note_same_change(pr)
    ms = [f for f in pr["findings"] if f.get("same_change")]
    a, b = ms
    assert any(("#%d" % b["id"]) in n for n in a["notes"]) and any(("#%d" % a["id"]) in n for n in b["notes"])
    assert pr["same_change"][0]["ids"] == [a["id"], b["id"]]
    src = open(diaglog.__file__, encoding="utf-8").read()
    assert "same_change (%d) — ความต่างเดียวกันที่ขึ้นสองจุด" in src and '" same_change=%s"' in src
    assert "SAME_CHANGE" in pipeline.settings_snapshot() and "SEAM_YELLOW" in pipeline.settings_snapshot()


def test_note_skips_incomplete_group():
    r = _cmp(_sides(KO_A, KO_B))
    keep = [f for f in r["findings"] if f.get("same_change_role") != "line"]   # สมาชิกหนึ่งถูกพับไปก่อน
    pr = {"n": 1, "findings": keep, "same_change": r["same_change"]}
    for i, f in enumerate(keep):
        f["id"] = i + 1
    before = [list(f["notes"]) for f in keep]
    pipeline._note_same_change(pr)
    assert [f["notes"] for f in keep] == before


def _merge(f, verdict):
    pr = {"n": 1, "findings": [f]}
    st = {"reviews_total": 0, "reviews_valid": 0, "invalid": []}
    resp = {"reviews": [{"candidate": "F%d" % f["id"], "verdict": verdict, "reason": "", "suggestion": "",
                         "a_seen": "", "b_seen": ""}]}
    ai_review._merge_image(pr, resp, [f], st)
    return pr, st


def _seam_finding():
    r = _cmp(_sides(KO_A, KO_B))
    f = [x for x in r["findings"] if x.get("seam") and x["class"] != "EXTRA_IN_B"][0]
    f["id"] = 7
    return f


def test_ai_real_on_seam_stays_yellow(monkeypatch):
    pr, st = _merge(_seam_finding(), "real")
    f = pr["findings"][0]
    assert f["severity"] == "yellow" and st["image_verdicts"]["seam_yellow"] == 1
    assert any("รอยต่อ" in n and "AI" in n for n in f["notes"])
    f = _seam_finding()
    monkeypatch.setattr(config, "SEAM_YELLOW", False)
    f["severity"] = "red"
    pr, _ = _merge(f, "real")
    assert pr["findings"][0]["severity"] == "red"


def test_ai_real_on_a_real_digit_group_is_still_red():
    r = _cmp(_sides(JW_A, JW_B))
    f = [x for x in r["findings"] if x.get("same_change")][0]
    f["id"] = 3
    pr, _ = _merge(f, "real")
    assert pr["findings"][0]["severity"] == "red"


# ── ⑤ หน้าเว็บ ───────────────────────────────────────────────────────────

_HARNESS = r"""
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const body = (name) => src.split('function ' + name + '(')[1].split('\n  }\n')[0];
const mk = (name, deps) => new Function(...Object.keys(deps), 'return function ' + name + '(' + body(name) + '\n}')(...Object.values(deps));
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const SEV_RANK = new Function('return ' + /const SEV_RANK = (\{[^}]*\});/.exec(src)[1])();
const lineGroups = mk('lineGroups', { SEV_RANK });
const markedMulti = mk('markedMulti', { esc });
const sameSide = mk('sameSide', { markedMulti });
const job = JSON.parse(require('fs').readFileSync(0, 'utf8'));
console.log(JSON.stringify(job.map((c) => {
  if (c.fn === 'groups') return lineGroups(c.list);
  if (c.fn === 'side') return sameSide(c.ms, c.s);
  throw new Error('fn?');
})));
"""


def _js(cases):
    if not shutil.which("node"):
        pytest.skip("ไม่มี node")
    r = subprocess.run(["node", "-e", _HARNESS, "x", JS], input=json.dumps(cases),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _ui_findings(sd):
    r = _cmp(sd)
    fs = json.loads(json.dumps(r["findings"], default=list))
    for i, f in enumerate(fs):
        f["id"] = i + 1
    return fs


def test_web_group_row_uses_the_changed_line_per_side():
    src = open(JS, encoding="utf-8").read()
    assert 'const cellA = g.same ? sameSide(ms, "a")' in src and 'const cellB = g.same ? sameSide(ms, "b")' in src
    assert 'g.same ? "เรื่องเดียวกัน "' in src


def test_web_one_row_per_group_and_each_side_shows_the_changed_line():
    fs = _ui_findings(_log_sides(KO1))
    g, sa, sb = _js([{"fn": "groups", "list": fs},
                     {"fn": "side", "ms": [f for f in fs if f.get("same_change")], "s": "a"},
                     {"fn": "side", "ms": [f for f in fs if f.get("same_change")], "s": "b"}])
    rows = [x for x in g if x.get("group")]
    assert len(rows) == 1 and rows[0]["same"] is True and len(rows[0]["members"]) == 2
    assert rows[0]["severity"] == "yellow"
    flat = []
    for x in g:
        flat += [m["id"] for m in x["members"]] if x.get("group") else [x["id"]]
    assert sorted(flat) == [f["id"] for f in fs]                  # ไม่มีจุดหาย/ซ้ำ
    assert "yby certyfikowane" in sa and '<mark class="v2-d">Numer identyfikacyjny' in sa
    assert sb.startswith('<mark class="v2-d">Numer identyfikacyjny zakładu: TH 2077</mark>')


def test_web_without_server_tags_groups_exactly_as_before(monkeypatch):
    _off(monkeypatch)
    fs = _ui_findings(_log_sides(KO1))
    g = _js([{"fn": "groups", "list": fs}])[0]
    assert not any(x.get("same") for x in g if x.get("group"))
    assert [x["id"] for x in g if not x.get("group")] == [f["id"] for f in fs]


# ── ทั้งเส้น (pipeline จริง · Vision ปลอม · AI ปิด · หลักฐานภาพปิด) ──────────────

def test_end_to_end_result_and_log(tmp_path, monkeypatch):
    import io

    import fitz
    from PIL import Image

    from artwork_v2 import jobs, keystore, vision_client
    d = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", str(d))
    monkeypatch.setattr(config, "JOBS_DIR", str(d / "jobs"))
    monkeypatch.setattr(config, "SECRET_DIR", str(d / "secret"))
    monkeypatch.setattr(config, "KEY_FILE", str(d / "secret" / "key.json"))
    monkeypatch.delenv(config.KEY_ENV, raising=False)
    monkeypatch.setattr(config, "RETRY_WAIT_S", 0.0)
    monkeypatch.setattr(config, "REREAD_ENABLED", False)
    monkeypatch.setattr(config, "PIXEL_VERIFY", False)
    key = "AIza" + "Q1w2E3r4T5y6U7i8O9p0A1s2D3f4G5h6J7k"
    keystore.save(key)

    def ann(groups, poster=None, key=None):
        res, ids = {}, []
        for g in groups:
            for it in g:
                ids.append(it["id"])
                w, h = Image.open(io.BytesIO(it["jpeg"])).size
                src = ANCHORS + (KO_A if it["id"].endswith("a") else KO_B)
                ls = [(t, (b[0] * w / 1000.0, b[1] * h / 1000.0, b[2] * w / 1000.0, b[3] * h / 1000.0), 0.98)
                      for t, b in src]
                res[it["id"]] = {"ok": True, "error": "", "fta": fta_from_lines(ls, w, h), "request_index": 0}
        return {"results": res, "calls": [{"index": 0, "phase": "main", "images": ids, "json_bytes": 10,
                                           "status": 200, "attempts": 1, "ms": 1, "error": "",
                                           "model_requested": config.MODEL, "endpoint": config.ENDPOINT,
                                           "at": "t"}]}

    def pdf():
        doc = fitz.open()
        doc.new_page(width=600, height=600).insert_text((40, 60), "x", fontsize=12)
        return doc.tobytes()

    monkeypatch.setattr(vision_client, "annotate", ann)
    job = jobs.create(("a.pdf", pdf()), ("b.pdf", pdf()))["id"]
    full = [{"a": {"page": 0, "bbox": [0, 0, 1, 1]}, "b": {"page": 0, "bbox": [0, 0, 1, 1]}}]
    r = pipeline.run(job, full, ai_mode="off")
    pr = r["pairs"][0]
    assert len(pr["same_change"]) == 1
    rec = pr["same_change"][0]
    ms = [f for f in pr["findings"] if f.get("same_change") == rec["group"]]
    assert sorted(rec["ids"]) == sorted(f["id"] for f in ms) and len(ms) == 2
    a, b = ms
    assert any("#%d" % b["id"] in n for n in a["notes"]) and any("#%d" % a["id"] in n for n in b["notes"])
    assert all(f["severity"] == "yellow" for f in ms)
    log = r["log_text"]
    assert "same_change (1)" in log and "kind=layout" in log and "seam_yellow" in log
    assert " same_change=1" in log and "SAME_CHANGE=True" in log and "SEAM_YELLOW=True" in log
    assert key not in log
