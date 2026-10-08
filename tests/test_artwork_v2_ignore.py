"""Artwork V2 — พื้นที่ยกเว้นในโซน (``ARTWORK_V2_ZONE_IGNORE`` · 7 ต.ค. รอบ 5 · Friskies)

ที่มา: Friskies 🅱 มีป้ายของโรงพิมพ์ ``INKJET area`` / ``L5.5 CM x W2.0 CM`` ในโซน — เป็นความต่างจริง
ของไฟล์ แต่ไม่ใช่สิ่งที่ต้องตรวจ ⇒ ผู้ใช้วาด "พื้นที่ยกเว้น" ทับ แล้วจุดต่างในนั้นไปรายการพับ

สิ่งที่ล็อก (กฎเหล็ก: ไม่ลบเงียบ · ไม่กระทบงานที่ไม่ได้ใช้):
* ไม่ส่ง ``ignore`` / ปิดธง ⇒ ข้อมูลโซนเท่าเดิมทุกตัว · ผลเดิมทุกจุด
* ยกเว้นเฉพาะจุดที่ **ทุกฝั่งที่มีกรอบ** อยู่ในพื้นที่ยกเว้นของฝั่งนั้น ≥ 60% · ยกเว้นฝั่งเดียวแต่อีกฝั่ง
  มีข้อความ ⇒ ยังนับ
* จุดที่ยกเว้นอยู่ในรายการพับ ``excluded`` + Log + เหตุผลผลตัดสิน (ไม่หาย)
* พิกัดถูกต้องทุกมุมหมุน (Python ↔ JS ใช้สูตรกลับด้านกัน)
* เปิดงานเดิมได้พื้นที่ยกเว้นกลับมา
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from artwork_v2_fake import fta_from_lines  # noqa: E402

from artwork_v2 import config, jobs, keystore, pipeline, vision_client  # noqa: E402

from PIL import Image  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js", "artwork_v2.js")
KEY = "AIza" + "I" * 35
W, H = 1600, 1000


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    d = tmp_path / "v2"
    monkeypatch.setattr(config, "DATA_DIR", str(d))
    monkeypatch.setattr(config, "JOBS_DIR", str(d / "jobs"))
    monkeypatch.setattr(config, "SECRET_DIR", str(d / "secret"))
    monkeypatch.setattr(config, "KEY_FILE", str(d / "secret" / "key.json"))
    monkeypatch.delenv(config.KEY_ENV, raising=False)
    monkeypatch.setattr(config, "RETRY_WAIT_S", 0.0)
    monkeypatch.setattr(config, "ZONE_IGNORE", True)
    monkeypatch.setattr(config, "PIXEL_VERIFY", False)
    os.makedirs(config.JOBS_DIR)
    yield


ZA = {"page": 0, "bbox": [0.1, 0.1, 0.8, 0.8]}


# ── รับข้อมูลจากหน้าเว็บ ──────────────────────────────────────────────

def test_no_ignore_means_the_old_zone_record():
    p = pipeline.parse_pairs([{"a": dict(ZA), "b": dict(ZA)}])
    assert p == [{"a": {"page": 0, "bbox": [0.1, 0.1, 0.8, 0.8]}, "b": {"page": 0, "bbox": [0.1, 0.1, 0.8, 0.8]}}]
    p = pipeline.parse_pairs([{"a": dict(ZA, ignore=[]), "b": dict(ZA)}])
    assert "ignore" not in p[0]["a"]


def test_ignore_is_clipped_to_the_zone_and_outside_boxes_dropped():
    p = pipeline.parse_pairs([{"a": dict(ZA), "b": dict(ZA, ignore=[[0.05, 0.5, 0.2, 0.1],
                                                                        [0.92, 0.92, 0.05, 0.05]])}])
    assert p[0]["b"]["ignore"] == [[0.1, 0.5, 0.15, 0.1]]


@pytest.mark.parametrize("bad", ["x", [["a", 1, 2, 3]], [[0.2, 0.2, 0, 0]], [[0.2, 0.2, 0.1, 0.1]] * 21])
def test_bad_ignore_is_refused(bad):
    with pytest.raises(ValueError):
        pipeline.parse_pairs([{"a": dict(ZA), "b": dict(ZA, ignore=bad)}])


def test_flag_off_ignores_the_key(monkeypatch):
    monkeypatch.setattr(config, "ZONE_IGNORE", False)
    p = pipeline.parse_pairs([{"a": dict(ZA), "b": dict(ZA, ignore=[[0.2, 0.2, 0.1, 0.1]])}])
    assert "ignore" not in p[0]["b"]


# ── พิกัด (ทุกมุมหมุน) ─────────────────────────────────────────────────

SIDE0 = {"bbox": [0.2, 0.1, 0.5, 0.4], "sent_px": [1000, 800]}


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_page_box_round_trips_with_the_js_overlay(rot):
    sd = dict(SIDE0, rotate=rot)
    if rot in (90, 270):
        sd["sent_px"] = [800, 1000]
    ign = [0.3, 0.2, 0.1, 0.05]
    node = ("const src=require('fs').readFileSync(%r,'utf8');"
            "const i=src.indexOf('function ignSentBox');const j=src.indexOf('function svgFor');"
            "eval(src.slice(i,j));console.log(JSON.stringify(ignSentBox(%s,%s)));") % (
                JS, json.dumps(sd), json.dumps(ign))
    out = subprocess.run(["node", "-e", node], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    box = json.loads(out.stdout)
    back = pipeline._page_box(sd, box)
    assert back == pytest.approx((0.3, 0.2, 0.4, 0.25), abs=1e-9)


# ── ย้ายจุดต่างไปรายการพับ ──────────────────────────────────────────────

def _f(a_box, b_box, sev="red"):
    def side(b):
        return {"box": b, "line": 0 if b else None, "frag": "x" if b else "", "text": "x" if b else "",
                "conf": 0.9 if b else None, "span": [0, 1 if b else 0]}
    return {"severity": sev, "class": "TEXT", "notes": [], "a": side(a_box), "b": side(b_box)}


def _pr(findings, ign_a=None, ign_b=None):
    sd = {"bbox": [0.0, 0.0, 1.0, 1.0], "sent_px": [W, H]}
    pr = {"sides": {"a": dict(sd), "b": dict(sd)}, "findings": findings}
    if ign_a:
        pr["sides"]["a"]["ignore"] = ign_a
    if ign_b:
        pr["sides"]["b"]["ignore"] = ign_b
    return pr


INK = [0.5, 0.8, 0.3, 0.1]                       # สัดส่วนของหน้า = px (800,800)-(1280,900)


def test_one_sided_finding_inside_the_ignore_area_is_folded_not_deleted():
    f = _f(None, (850, 820, 1100, 870))
    pr = _pr([f], ign_b=[INK])
    assert pipeline.apply_ignore(pr) == 1
    assert pr["findings"] == [] and pr["excluded"] == [f]
    assert f["severity"] == "excluded" and f["excluded_from"] == "red"
    assert any("พื้นที่ยกเว้น" in n for n in f["notes"])


def test_text_on_the_other_side_keeps_the_finding():
    pr = _pr([_f((850, 820, 1100, 870), (850, 820, 1100, 870))], ign_b=[INK])
    assert pipeline.apply_ignore(pr) == 0 and len(pr["findings"]) == 1
    pr = _pr([_f((850, 820, 1100, 870), (850, 820, 1100, 870))], ign_a=[INK], ign_b=[INK])
    assert pipeline.apply_ignore(pr) == 1


def test_finding_mostly_outside_the_area_is_kept():
    pr = _pr([_f(None, (700, 820, 1000, 870))], ign_b=[INK])     # อยู่ในพื้นที่ 2/3
    assert pipeline.apply_ignore(pr) == 1
    pr = _pr([_f(None, (500, 820, 1000, 870))], ign_b=[INK])     # อยู่ในพื้นที่ 40%
    assert pipeline.apply_ignore(pr) == 0


def test_no_ignore_leaves_the_pair_untouched():
    f = _f(None, (850, 820, 1100, 870))
    pr = _pr([f])
    before = json.dumps(pr, sort_keys=True)
    assert pipeline.apply_ignore(pr) == 0 and json.dumps(pr, sort_keys=True) == before


# ── ทั้ง pipeline ───────────────────────────────────────────────────────

ROWS = [("Ingredients: tuna, sunflower oil, water, salt.", (100, 100, 900, 130)),
        ("Produced in Thailand for Example Foods Ltd,", (100, 160, 900, 190)),
        ("Net weight 85 g best before end", (100, 220, 700, 250))]
MARK = ("INKJET area", (850, 820, 1100, 870))


def _fake(rows_by_side):
    def fake(groups, poster=None, key=None):
        res = {}
        for gi, g in enumerate(groups):
            for it in g:
                w, h = Image.open(io.BytesIO(it["jpeg"])).size
                sx, sy = w / float(W), h / float(H)
                lns = [(t, (b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy), 0.98) for t, b in rows_by_side[it["id"][-1]]]
                res[it["id"]] = {"ok": True, "error": "", "fta": fta_from_lines(lns, w, h), "request_index": gi}
        return {"results": res, "calls": [{"index": 0, "phase": "main", "images": [], "json_bytes": 1,
                                           "status": 200, "attempts": 1, "ms": 1, "error": "", "at": "t"}]}
    return fake


def _job():
    buf = io.BytesIO()
    Image.new("RGB", (W, H), "white").save(buf, "PNG")
    return jobs.create(("a.png", buf.getvalue()), ("b.png", buf.getvalue()))["id"]


def _run(monkeypatch, ign_b):
    jid = _job()
    keystore.save(KEY)
    monkeypatch.setattr(vision_client, "annotate", _fake({"a": ROWS, "b": ROWS + [MARK]}))
    b = {"page": 0, "bbox": [0, 0, 1, 1]}
    if ign_b is not None:
        b["ignore"] = ign_b
    return jid, pipeline.run(jid, [{"a": {"page": 0, "bbox": [0, 0, 1, 1]}, "b": b}])


def test_pipeline_printer_mark_in_the_ignore_area_is_folded(monkeypatch):
    jid, r = _run(monkeypatch, [[0.5, 0.78, 0.25, 0.12]])
    p = r["pairs"][0]
    assert p["findings"] == [] and [f["b"]["frag"] for f in p["excluded"]] == ["INKJET area"]
    assert p["excluded"][0]["id"] and r["verdict"] == "PASS"
    assert any("พื้นที่ยกเว้น 1 จุด" in x for x in r["reasons"])
    assert "excluded — อยู่ในพื้นที่ยกเว้น" in r["log_text"] and "ignore (พื้นที่ยกเว้น" in r["log_text"]
    assert "ZONE_IGNORE=True" in r["log_text"]
    # เปิดงานเดิม ⇒ ได้พื้นที่ยกเว้นกลับมา
    lp = jobs.last_pairs(os.path.join(config.JOBS_DIR, jid))
    assert lp[0]["b"]["ignore"] == [[0.5, 0.78, 0.25, 0.12]] and "ignore" not in lp[0]["a"]


def test_pipeline_without_ignore_keeps_the_red_finding(monkeypatch):
    _, r = _run(monkeypatch, None)
    p = r["pairs"][0]
    assert [f["b"]["frag"] for f in p["findings"]] == ["INKJET area"] and r["verdict"] == "FAIL"
    assert not p.get("excluded") and "excluded" not in r["log_text"]


def test_pipeline_flag_off_ignores_the_area(monkeypatch):
    monkeypatch.setattr(config, "ZONE_IGNORE", False)
    _, r = _run(monkeypatch, [[0.5, 0.78, 0.25, 0.12]])
    assert r["verdict"] == "FAIL" and not r["pairs"][0].get("excluded")


def test_page_and_template_offer_the_tool_only_when_enabled(monkeypatch):
    from flask import Flask
    from artwork_v2.routes import artwork_v2_bp
    tpl = os.path.join(ROOT, "templates")
    for flag, want in ((True, True), (False, False)):
        monkeypatch.setattr(config, "ZONE_IGNORE", flag)
        app = Flask(__name__, template_folder=tpl)

        @app.context_processor
        def _ctx():
            return {"config_version": "t", "current_user": None, "auth_enabled": False,
                    "has_perm": lambda *a, **k: True}
        app.register_blueprint(artwork_v2_bp)
        html = app.test_client().get("/artwork_v2").get_data(as_text=True)
        assert ('data-mode="ign"' in html) is want
        assert ('data-zone-ignore="%d"' % int(flag)) in html


def test_findings_added_after_compare_also_respect_the_area(monkeypatch):
    """จุดที่ชั้น AI เพิ่มเข้ามา (หลังการเทียบ) ต้องผ่านพื้นที่ยกเว้นด้วย"""
    from artwork_v2 import ai_review
    real = ai_review.run_all

    def add(pairs, mode, warnings, say, fid, poster=None):
        out = real(pairs, mode, warnings, say, fid, poster)
        f = _f(None, (860, 825, 1090, 865), sev="yellow")
        f.update(id=99, source="ai")
        pairs[0]["findings"].append(f)
        return out
    monkeypatch.setattr(ai_review, "run_all", add)
    monkeypatch.setattr(vision_client, "annotate", _fake({"a": ROWS, "b": ROWS}))
    keystore.save(KEY)
    r = pipeline.run(_job(), [{"a": {"page": 0, "bbox": [0, 0, 1, 1]},
                               "b": {"page": 0, "bbox": [0, 0, 1, 1], "ignore": [[0.5, 0.78, 0.25, 0.12]]}}])
    p = r["pairs"][0]
    assert p["findings"] == [] and [f.get("id") for f in p["excluded"]] == [99]
