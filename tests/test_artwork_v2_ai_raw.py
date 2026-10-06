"""โหมด AI "ตัดสินจากข้อมูลดิบ" (``raw`` · 6 ต.ค. 2026) — ไม่ยิงเน็ตจริง

ผู้ใช้สั่ง: ส่งข้อมูลดิบของ Vision ให้ Gemini ตรวจ **ไม่ผ่านอัลกอริทึมเลย** · ห้ามเดา ค่อย ๆ คิด ·
มาพร้อมตำแหน่งและ % เหมือนเดิม ตามโครงสร้างเดิม

สิ่งที่ล็อกไว้:
* ส่ง **บรรทัดตามที่ Vision ส่ง** (ไม่ต่อแถว/ไม่ต่อคำ) · ไม่มี ``candidates`` · ไม่มีธงโค้ง
* กรอบ/% มาจาก Vision · ดัชนีบรรทัดของจุดต่างอ้าง "บรรทัดดิบ"
* real + Vision ≥ 80% = แดง · ต่ำกว่า/ไม่แน่ใจ = เหลือง · noise = พับ
* กติกาความปลอดภัยที่อิงหลักฐานของ Vision ล้วน (ปิดได้ด้วย ``AI_RAW_SAFETY``)
* ผลของอัลกอริทึม **ทุกจุด** ⇒ รายการพับ "ไว้เทียบ" · ความครอบคลุมไม่ใช้ตัดสิน
* N8N ล่ม ⇒ ผลอัลกอริทึมทุกรายการเหมือนทุกโหมด
* workflow **แยก** (artwork-v2-raw): prompt ข้อมูลดิบ + คิดนานขึ้น · เอกสารตรงกับ workflow ·
  workflow artwork-v2-review เดิมไม่ถูกแตะ · แอปยิง raw ไปที่ ``AI_RAW_URL`` เท่านั้น
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from artwork_v2_fake import fta, line_para, load_log  # noqa: E402

from artwork_v2 import (ai_review, compare, config, diaglog, jobs, keystore,  # noqa: E402
                        pipeline, textmodel, vision_client)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, "artwork_v2", "n8n_artwork_v2_raw.workflow.json")
DOC = os.path.join(ROOT, "docs", "N8N_ARTWORK_V2_RAW_PROMPT.md")
WF_REVIEW = os.path.join(ROOT, "artwork_v2", "n8n_artwork_v2_review.workflow.json")
KEY = "AIza" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    d = tmp_path / "v2"
    monkeypatch.setattr(config, "DATA_DIR", str(d))
    monkeypatch.setattr(config, "JOBS_DIR", str(d / "jobs"))
    monkeypatch.setattr(config, "SECRET_DIR", str(d / "secret"))
    monkeypatch.setattr(config, "KEY_FILE", str(d / "secret" / "key.json"))
    monkeypatch.delenv(config.KEY_ENV, raising=False)
    monkeypatch.setattr(config, "RETRY_WAIT_S", 0.0)
    monkeypatch.setattr(config, "REREAD_ENABLED", False)
    monkeypatch.setattr(config, "AI_RAW_SAFETY", True)
    monkeypatch.setattr(config, "AI_REVIEW_URL", "http://127.0.0.1:9/webhook/artwork-v2-review")
    monkeypatch.setattr(config, "AI_RAW_URL", "http://127.0.0.1:9/webhook/artwork-v2-raw")
    os.makedirs(config.JOBS_DIR)
    yield


# ── ตัวช่วย ──────────────────────────────────────────────────────────

def _lines(rows):
    """rows = [(text, x, y, kw)] → บรรทัดดิบของ textmodel (เหมือนที่ pipeline ได้จาก Vision)"""
    return textmodel.parse(fta(rows, 1000, 1000), 1000, 1000)["lines"]


def _simple(texts, confs=None):
    rows = []
    for i, t in enumerate(texts):
        kw = {"cw": 10, "h": 20}
        if confs and confs.get(i) is not None:
            kw["conf"] = confs[i]
        rows.append((t, 20, 40 + i * 40, kw))
    return _lines(rows)


def _pr(raw_a, raw_b):
    """คู่โซนแบบที่ pipeline ส่งเข้า run_all (มีทั้งผลอัลกอริทึม และบรรทัดดิบ)"""
    r = compare.compare(raw_a, raw_b, (1000, 1000), (1000, 1000))
    for i, f in enumerate(r["findings"], 1):
        f["id"] = i
    side = {"sent_px": [1000, 1000], "stats": {"conf_mean": 0.97}}
    return {"n": 1, "findings": r["findings"], "curved_lines": r["curved_lines"],
            "reflow_lines": r["reflow_lines"], "_cmp": r,
            "_raw": {"a": raw_a, "b": raw_b},
            "sides": {"a": dict(side), "b": dict(side)}}


def wid(lines, side, word, nth=0):
    k = 0
    for i, ln in enumerate(lines):
        for j, (_, _, w) in enumerate(ai_review._words(ln["text"])):
            if w == word:
                if k == nth:
                    return "%s%d:%d" % (side, i, j)
                k += 1
    raise AssertionError("ไม่พบคำ %r" % word)


def _item(a_words, a_quote, b_words, b_quote, verdict="real"):
    return {"a_words": a_words, "a_quote": a_quote, "b_words": b_words, "b_quote": b_quote,
            "kind": "text", "verdict": verdict, "reason": "เหตุผล", "suggestion": "คำแนะนำ"}


class _Resp:
    def __init__(self, data, status=200):
        self.status_code = status
        self._d = data
        self.text = json.dumps(data)

    def json(self):
        return self._d


def _poster(answer, seen=None, status=200, timeouts=None, urls=None):
    def post(url, data=None, headers=None, timeout=None):
        body = json.loads(data.decode("utf-8"))
        if urls is not None:
            urls.append(url)
        if seen is not None:
            seen.append(body)
        if timeouts is not None:
            timeouts.append(timeout)
        return _Resp(answer(body) if callable(answer) else answer, status)
    return post


def _run_all(pr, answer, seen=None, mode="raw"):
    st, _ = ai_review.run_all([pr], mode, [], lambda *_: None, 100, _poster(answer, seen))
    return st


# ── ① ข้อมูลที่ส่ง = บรรทัดดิบของ Vision ────────────────────────────────

# ตารางแถวเดียวกัน: ฝั่ง A Vision ให้ 1 บรรทัด · ฝั่ง B ให้ 3 ชิ้น (ชื่อ | จุดไข่ปลา | ค่า)
ROW_A = [("Crude Fat (min).......7.0%", 20, 40, {"cw": 10, "h": 20}),
         ("Sodium 475 mg 20%", 20, 80, {"cw": 10, "h": 20})]
ROW_B = [("Crude Fat (min)", 20, 40, {"cw": 10, "h": 20}),
         (".......", 180, 40, {"cw": 10, "h": 20}),
         ("7.0%", 260, 40, {"cw": 10, "h": 20}),
         ("Sodium 475 mg 24%", 20, 80, {"cw": 10, "h": 20})]


def test_mode_is_known_and_old_modes_unchanged():
    assert config.AI_MODES == ("assist", "judge", "raw", "off")
    assert ai_review.norm_mode("raw") == "raw" and ai_review.norm_mode("RAW ") == "raw"
    src = open(os.path.join(ROOT, "artwork_v2", "config.py"), encoding="utf-8").read()
    assert 'os.getenv("ARTWORK_V2_AI_MODE", "assist")' in src   # ค่าเริ่มต้นของเครื่องไม่เปลี่ยน


def test_raw_payload_is_vision_lines_as_returned_without_algorithm_output():
    A, B = _lines(ROW_A), _lines(ROW_B)
    assert len(B) == 4                           # Vision แยกแถวเป็น 3 ชิ้น
    pr = _pr(A, B)
    assert len(pr["_cmp"]["lines_b"]) < len(B)  # อัลกอริทึมต่อแถวแล้ว (ยืนยันว่าสองชุดต่างกันจริง)
    seen = []
    _run_all(pr, {"reviews": [], "items": [], "summary": "", "suggestions": []}, seen)
    p = seen[0]
    assert p["mode"] == "raw" and p["candidates"] == []
    assert [l["id"] for l in p["zone_b"]] == ["B0", "B1", "B2", "B3"]
    assert [" ".join(l["words"]) for l in p["zone_b"]][:3] == ["Crude Fat (min)", ".......", "7.0%"]
    assert "curved" not in json.dumps(p)
    assert KEY not in json.dumps(p)


def test_raw_ignores_curved_flags_even_when_algorithm_has_them():
    A, B = _simple(["Sodium 475 mg 20%"]), _simple(["Sodium 475 mg 24%"])
    pr = _pr(A, B)
    pr["curved_lines"] = {"A": [0], "B": [0]}
    seen = []
    ans = {"reviews": [], "items": [_item([wid(A, "A", "20%")], "20%", [wid(B, "B", "24%")], "24%")],
           "summary": "", "suggestions": []}
    _run_all(pr, ans, seen)
    assert "curved" not in json.dumps(seen[0])
    f = pr["findings"][0]
    assert not f.get("curved") and f["severity"] == "red"    # โค้งไม่ใช่เหตุผลลดระดับในโหมดนี้


def test_raw_uses_longer_timeout_only_for_raw(monkeypatch):
    A, B = _simple(["Fat 20%"]), _simple(["Fat 24%"])
    ans = {"reviews": [], "items": [], "summary": "", "suggestions": []}
    for mode, want in (("raw", config.AI_RAW_TIMEOUT_S), ("judge", config.AI_TIMEOUT_S),
                       ("assist", config.AI_TIMEOUT_S)):
        to = []
        ai_review.run_all([_pr(A, B)], mode, [], lambda *_: None, 0, _poster(ans, timeouts=to))
        assert to == [want], mode
    assert config.AI_RAW_TIMEOUT_S > config.AI_TIMEOUT_S


# ── ② ระดับของจุดต่าง (% จาก Vision) ─────────────────────────────────

def test_raw_cites_split_raw_line_and_box_comes_from_vision():
    A = _lines(ROW_A)
    rows_b = list(ROW_B)
    rows_b[2] = ("7.5%", 260, 40, {"cw": 10, "h": 20})
    B = _lines(rows_b)
    pr = _pr(A, B)
    ans = {"reviews": [], "items": [_item([wid(A, "A", "(min).......7.0%")], "(min).......7.0%",
                                          ["B2:0"], "7.5%")],
           "summary": "", "suggestions": []}
    st = _run_all(pr, ans)
    assert st["pairs_ok"] == 1
    f = pr["findings"][0]
    assert f["b"]["line"] == 2 and f["b"]["text"] == "7.5%"          # ดัชนีของบรรทัดดิบ
    assert f["b"]["box"] == compare._span_box(B[2], *f["b"]["span"])  # กรอบของ Vision
    assert f["confidence"] == ai_review.confidence(f) and f["raw"] is True


@pytest.mark.parametrize("verdict,conf,sev", [
    ("real", None, "red"), ("real", 0.5, "yellow"), ("uncertain", None, "yellow")])
def test_raw_severity_from_vision_confidence(verdict, conf, sev):
    A = _simple(["Sodium 475 mg 20%"])
    B = _simple(["Sodium 475 mg 24%"], {0: conf} if conf else None)
    pr = _pr(A, B)
    ans = {"reviews": [], "items": [_item([wid(A, "A", "20%")], "20%", [wid(B, "B", "24%")], "24%",
                                          verdict)], "summary": "", "suggestions": []}
    _run_all(pr, ans)
    f = pr["findings"][0]
    assert f["severity"] == sev and f["source"] == "ai"
    if conf:
        assert any("ต่ำกว่า 80%" in n for n in f["notes"])


def test_raw_noise_is_folded_but_clear_letters_stay_yellow(monkeypatch):
    A = _simple(["Sodium 475 mg 20%", "Logo ®"], {1: 0.5})
    B = _simple(["Sodium 475 mg 24%", "Logo Ⓡx"], {1: 0.5})
    pr = _pr(A, B)
    ans = {"reviews": [], "items": [
        _item([wid(A, "A", "20%")], "20%", [wid(B, "B", "24%")], "24%", "noise"),
        _item([wid(A, "A", "®")], "®", [wid(B, "B", "Ⓡx")], "Ⓡx", "noise")],
        "summary": "", "suggestions": []}
    _run_all(pr, ans)
    assert [f["severity"] for f in pr["findings"]] == ["yellow"]     # Vision อ่าน 20/24 ชัด
    assert len(pr["ai_dismissed"]) == 1 and pr["ai_dismissed"][0]["severity"] == "dismissed"
    monkeypatch.setattr(config, "AI_RAW_SAFETY", False)
    pr = _pr(A, B)
    _run_all(pr, ans)
    assert pr["findings"] == [] and len(pr["ai_dismissed"]) == 2       # AI ล้วน


def test_raw_punct_only_is_yellow_unless_safety_off(monkeypatch):
    A, B = _simple(["Arrow Hwy, Irwindale"]), _simple(["Arrow Hwy Irwindale"])
    ans = {"reviews": [], "items": [_item([wid(A, "A", "Hwy,")], "Hwy,", [wid(B, "B", "Hwy")], "Hwy")],
           "summary": "", "suggestions": []}
    pr = _pr(A, B)
    _run_all(pr, ans)
    f = pr["findings"][0]
    assert f["class"] == "PUNCT" and f["severity"] == "yellow"
    monkeypatch.setattr(config, "AI_RAW_SAFETY", False)
    pr = _pr(A, B)
    _run_all(pr, ans)
    assert pr["findings"][0]["severity"] == "red"


def test_raw_short_onesided_is_yellow_but_real_word_red():
    A = _simple(["NET WT 13 OZ", "0", "Made in USA"])
    B = _simple(["NET WT 13 OZ", "Made in"])
    pr = _pr(A, B)
    ans = {"reviews": [], "items": [
        _item(["A1:0"], "0", [], ""),
        _item([wid(A, "A", "in"), wid(A, "A", "USA")], "in USA", [wid(B, "B", "in")], "in")],
        "summary": "", "suggestions": []}
    _run_all(pr, ans)
    by = {f["a"]["frag"].strip(): f for f in pr["findings"]}
    assert by["0"]["class"] == "MISSING_IN_B" and by["0"]["severity"] == "yellow"
    assert any("สั้นมาก" in n for n in by["0"]["notes"])
    assert by["USA"]["severity"] == "red"


# ── ③ ผลอัลกอริทึม = รายการพับไว้เทียบ ────────────────────────────────

def test_all_algorithm_points_go_to_compare_list_not_counted():
    A, B = _simple(["Sodium 475 mg 20%", "Fat 1.5g"]), _simple(["Sodium 475 mg 24%", "Fat 15g"])
    pr = _pr(A, B)
    algo = len(pr["findings"])
    assert algo == 2
    st = _run_all(pr, {"reviews": [], "items": [], "summary": "ไม่พบ", "suggestions": []})
    assert st["pairs_ok"] == 1
    assert pr["findings"] == [] and len(pr["algo_only"]) == algo
    assert all(any("ไว้เทียบ" in n for n in g["notes"]) for g in pr["algo_only"])
    assert pr["ai"]["algo_compare"] == algo and pr["ai"]["candidates"] == 0


def test_n8n_failure_in_raw_mode_keeps_algorithm_result_exactly():
    A, B = _simple(["Sodium 475 mg 20%"]), _simple(["Sodium 475 mg 24%"])
    pr = _pr(A, B)
    before = json.dumps(pr["findings"], sort_keys=True, default=str)
    w = []
    ai_review.run_all([pr], "raw", w, lambda *_: None, 0, _poster({"x": 1}, status=503))
    assert pr["ai"]["status"] == "failed" and w
    assert json.dumps(pr["findings"], sort_keys=True, default=str) == before
    assert "algo_only" not in pr


# ── ④ ทั้งรอบ (pipeline) ─────────────────────────────────────────────

fitz = pytest.importorskip("fitz")
from PIL import Image  # noqa: E402

TA = ["Sodium 475 mg 20%", "Fat 1.5g", "Net weight 85 g"]
TB = ["Sodium 475 mg 24%", "Fat 1.5g", "Net weight 85 g"]
FULL = [{"a": {"page": 0, "bbox": [0, 0, 1, 1]}, "b": {"page": 0, "bbox": [0, 0, 1, 1]}}]


def _pdf(lines):
    d = fitz.open()
    p = d.new_page(width=400, height=300)
    for i, t in enumerate(lines):
        p.insert_text((20, 40 + i * 30), t, fontsize=12)
    return d.tobytes()


def _fake_annotate(groups, poster=None, key=None):
    res, ids = {}, []
    for g in groups:
        for it in g:
            ids.append(it["id"])
            W, H = Image.open(io.BytesIO(it["jpeg"])).size
            T = TA if it["id"].endswith("a") else TB
            lines = [(t, int(0.05 * W), int(H * (0.1 + 0.8 * i / len(T))),
                      {"cw": max(4, W // 40), "h": max(8, H // 15)}) for i, t in enumerate(T)]
            res[it["id"]] = {"ok": True, "error": "", "fta": fta(lines, W, H), "request_index": 0}
    return {"results": res, "calls": [{"index": 0, "phase": "main", "images": ids,
                                       "json_bytes": 10, "status": 200, "attempts": 1, "ms": 1,
                                       "error": "", "model_requested": config.MODEL,
                                       "endpoint": config.ENDPOINT, "at": "t"}]}


def _answer(payload):
    a = next(l for l in payload["zone_a"] if "20%" in l["words"])
    b = next(l for l in payload["zone_b"] if "24%" in l["words"])
    return {"reviews": [], "items": [_item(["%s:%d" % (a["id"], a["words"].index("20%"))], "20%",
                                           ["%s:%d" % (b["id"], b["words"].index("24%"))], "24%")],
            "summary": "พบ 1 จุด", "suggestions": ["ตรวจค่า Sodium"], "engine": "gemini-2.5-flash"}


def _run(monkeypatch, answer, seen=None, timeouts=None, status=200):
    monkeypatch.setattr(vision_client, "annotate", _fake_annotate)
    keystore.save(KEY)
    job = jobs.create(("a.pdf", _pdf(TA)), ("b.pdf", _pdf(TB)))["id"]
    return pipeline.run(job, FULL, ai_mode="raw",
                        ai_poster=_poster(answer, seen, status=status, timeouts=timeouts))


def test_run_raw_end_to_end(monkeypatch):
    seen, to = [], []
    r = _run(monkeypatch, _answer, seen, to)
    assert len(seen) == 1 and seen[0]["mode"] == "raw" and seen[0]["candidates"] == []
    assert to == [config.AI_RAW_TIMEOUT_S]
    pr = r["pairs"][0]
    assert r["verdict"] == "FAIL" and pr["findings"][0]["severity"] == "red"
    assert pr["findings"][0]["source"] == "ai" and pr["findings"][0]["confidence"] is not None
    assert pr["coverage_ignored"] is True and pr["raw_lines"]["a"]
    assert "_raw" not in pr and "_cmp" not in pr
    assert len(pr["algo_only"]) == 1
    assert any("ไว้เทียบ" in x for x in pr["reasons"])
    log = r["log_text"]
    assert "[AI REVIEW] mode=raw" in log and "raw: algo_compare=1 coverage_ignored=True" in log
    assert "RAW lines A ที่ส่งให้ AI" in log and KEY not in log
    assert [c["phase"] for c in r["calls"]] == ["main"]          # ห้ามส่งซ้ำ


def test_raw_log_lines_are_not_read_back_as_ocr_lines(monkeypatch, tmp_path):
    """ตัวโหลด Log ของชุดทดสอบอ่าน "A000 conf=…" — บรรทัดดิบ ("rA000") ต้องไม่ถูกนับซ้ำ"""
    r = _run(monkeypatch, _answer)
    assert re.search(r"^\s+rA000 conf=", r["log_text"], re.M)
    p = tmp_path / "log.txt"
    p.write_text(r["log_text"], encoding="utf-8")
    got = load_log(str(p))[1]
    for s in ("A", "B"):
        assert len(got[s][2]) == len(r["pairs"][0]["lines"][s.lower()]) == 3


def test_raw_pass_when_ai_finds_nothing_ignores_algorithm_coverage(monkeypatch):
    r = _run(monkeypatch, {"reviews": [], "items": [], "summary": "ไม่พบ", "suggestions": []})
    pr = r["pairs"][0]
    assert r["verdict"] == "PASS" and pr["findings"] == [] and len(pr["algo_only"]) == 1


def test_raw_n8n_down_end_to_end_is_algorithm_result(monkeypatch):
    r = _run(monkeypatch, {"x": 1}, status=500)
    pr = r["pairs"][0]
    assert pr["ai"]["status"] == "failed" and not pr.get("coverage_ignored")
    assert pr["findings"] and pr["findings"][0].get("source") != "ai"
    assert any("AI ตรวจทานไม่สำเร็จ" in w for w in r["warnings"])


# ── ⑤ หน้าเว็บ ───────────────────────────────────────────────────────

def test_page_offers_raw_mode_with_thai_label():
    html = open(os.path.join(ROOT, "templates", "artwork_v2.html"), encoding="utf-8").read()
    assert 'value="raw"' in html and "AI ตัดสินจากข้อมูลดิบ (ทดลอง)" in html
    js = open(os.path.join(ROOT, "static", "js", "artwork_v2.js"), encoding="utf-8").read()
    assert 'raw: "AI ตัดสินจากข้อมูลดิบ"' in js
    assert "p.raw_lines" in js and "coverage_ignored" in js


# ── ⑥ workflow ของ N8N (รัน Code node ตัวจริงผ่าน node) ──────────────────

HARNESS = r"""
const fs = require('fs');
const w = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const body = JSON.parse(fs.readFileSync(0, 'utf8'));
const code = w.nodes.find((x) => x.name === 'Build Gemini request').parameters.jsCode;
const out = new Function('$input', code)({ first: () => ({ json: { body } }) });
console.log(JSON.stringify(out[0].json));
"""


def _build(body, wf=WF):
    if not shutil.which("node"):
        pytest.skip("ไม่มี node")
    r = subprocess.run(["node", "-e", HARNESS, "x", wf], input=json.dumps(body),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _wfjson(path=WF):
    return json.load(open(path, encoding="utf-8"))


def _code(path=WF):
    return next(n for n in _wfjson(path)["nodes"]
                if n["name"] == "Build Gemini request")["parameters"]["jsCode"]


def _prompt(path=WF):
    return re.search(r"const PROMPT = `(.*?)`;", _code(path), re.S).group(1)


BODY = {"zone_a": [{"id": "A0", "words": ["Fat", "20%"], "word_conf": [1, 1], "curved": True}],
        "zone_b": [{"id": "B0", "words": ["Fat", "24%"], "word_conf": [1, 1]}],
        "candidates": [{"id": "F1"}]}


def test_raw_workflow_builds_raw_request_whatever_the_body_says():
    for mode in ("raw", "assist", "judge", None):
        b = _build(dict(BODY, mode=mode))
        req = b["gemini_request"]
        assert b["valid"] and b["mode"] == "raw"
        assert req["systemInstruction"]["parts"][0]["text"] == _prompt()
        gc = req["generationConfig"]
        assert gc["thinkingConfig"]["thinkingBudget"] == 24576 and gc["maxOutputTokens"] == 65536
        assert gc["temperature"] == 0 and "confidence" not in json.dumps(gc["responseSchema"])
        data = json.loads(req["contents"][0]["parts"][0]["text"].split("\n", 1)[1])
        # ไม่ส่งต่อผลอัลกอริทึม/ธงโค้ง แม้ body จะมีมา
        assert data["mode"] == "raw" and data["candidates"] == []
        assert "curved" not in json.dumps(data)
        assert data["zone_a"][0]["w"] == [["A0:0", "Fat", 1], ["A0:1", "20%", 1]]
    bad = _build({"mode": "raw"})
    assert bad["valid"] is False and bad["error"]


def test_raw_prompt_rules():
    p = _prompt()
    for must in ("RAW OCR output", "Nothing has been pre-processed", "work slowly",
                 "Then do the reverse", "searched the whole other zone",
                 "re-read a_quote and b_quote character by character", "Never guess",
                 "never invent an item", 'return "reviews": []', "Never count word positions",
                 "EVERY cited word on both sides has word_conf >= 0.80",
                 "Never output any number for confidence", "written in Thai",
                 "a table row may be one line in A and two or three pieces in B"):
        assert must in p, must
    assert "candidates" not in p and '"curved": true' not in p


def test_raw_doc_prompt_matches_raw_workflow():
    doc = open(DOC, encoding="utf-8").read()
    d = re.search(r"<!-- PROMPT_RAW START -->\n```text\n(.*?)\n```\n<!-- PROMPT_RAW END -->",
                  doc, re.S).group(1)
    assert d == _prompt()
    assert "artwork-v2-raw" in doc and "ARTWORK_V2_AI_RAW_URL" in doc


def test_raw_workflow_is_a_separate_importable_flow():
    w, old = _wfjson(), _wfjson(WF_REVIEW)
    hook = next(n for n in w["nodes"] if n["type"] == "n8n-nodes-base.webhook")
    assert hook["parameters"]["path"] == "artwork-v2-raw"
    assert config.AI_RAW_URL.endswith("/webhook/artwork-v2-raw") or "artwork-v2-raw" in config.AI_RAW_URL
    assert w["name"] != old["name"]
    ids = [n["id"] for n in w["nodes"]]
    assert len(set(ids)) == len(ids) and not set(ids) & {n["id"] for n in old["nodes"]}
    old_hook = next(n for n in old["nodes"] if n["type"] == "n8n-nodes-base.webhook")
    assert hook["webhookId"] != old_hook["webhookId"]
    http = next(n for n in w["nodes"] if n["type"] == "n8n-nodes-base.httpRequest")
    assert http.get("onError") == "continueRegularOutput"
    assert http["parameters"]["options"]["response"]["response"]["neverError"] is True
    assert http["parameters"]["options"]["timeout"] == 290000
    assert http["parameters"]["options"]["timeout"] < config.AI_RAW_TIMEOUT_S * 1000
    assert http["parameters"]["nodeCredentialType"] == "googleApi"
    names = {n["name"] for n in w["nodes"]}
    nxt = {s: [c["node"] for br in o["main"] for c in br] for s, o in w["connections"].items()}
    assert set(nxt) <= names and all(t in names for v in nxt.values() for t in v)
    # ทุกทางจาก Webhook ต้องไปจบที่ Respond to Webhook
    def ends(n, seen=()):
        if n in seen:
            return False
        if not nxt.get(n):
            return n.startswith("Respond to Webhook")
        return all(ends(t, seen + (n,)) for t in nxt[n])
    assert ends("Webhook")
    # node Parse = ตัวเดิมของ workflow review (ตรวจคำตอบของ Gemini ชุดเดียวกัน ถึงบรรทัดแปลงผล)
    # + ชั้นตรวจคำตอบซ้ำของ raw ต่อท้าย (6 ต.ค. รอบ 4)
    parse = lambda wf: next(n for n in wf["nodes"] if n["name"] == "Parse Gemini response")[
        "parameters"]["jsCode"].split("\n", 1)[1]
    head = parse(old).split("const arr = (x) =>")[0]
    assert parse(w).startswith(head) and "fixAnswer" in parse(w) and "fixAnswer" not in parse(old)


def test_review_workflow_is_untouched_by_raw_mode():
    """workflow เดิม (artwork-v2-review) ต้องไม่รู้จักโหมด raw เลย — ผู้ใช้ไม่ต้องแตะของเดิม"""
    code, old = _code(WF_REVIEW), _wfjson(WF_REVIEW)
    assert "PROMPT_RAW" not in code and "THINKING_BUDGET_RAW" not in code and "'raw'" not in code
    http = next(n for n in old["nodes"] if n["type"] == "n8n-nodes-base.httpRequest")
    assert http["parameters"]["options"]["timeout"] == 170000
    hook = next(n for n in old["nodes"] if n["type"] == "n8n-nodes-base.webhook")
    assert hook["parameters"]["path"] == "artwork-v2-review"
    review_doc = open(os.path.join(ROOT, "docs", "N8N_ARTWORK_V2_REVIEW_PROMPT.md"),
                      encoding="utf-8").read()
    assert "PROMPT_RAW" not in review_doc
    # body ที่บอก mode raw ⇒ workflow เดิมถือเป็น assist (ไม่ใช่คำขอแบบข้อมูลดิบ)
    assert _build(dict(BODY, mode="raw"), WF_REVIEW)["mode"] == "assist"


def test_raw_posts_to_raw_url_and_other_modes_keep_review_url():
    A, B = _simple(["Fat 20%"]), _simple(["Fat 24%"])
    ans = {"reviews": [], "items": [], "summary": "", "suggestions": []}
    for mode, want in (("raw", config.AI_RAW_URL), ("judge", config.AI_REVIEW_URL),
                       ("assist", config.AI_REVIEW_URL)):
        urls = []
        st, _ = ai_review.run_all([_pr(A, B)], mode, [], lambda *_: None, 0,
                                  _poster(ans, urls=urls))
        assert urls == [want] and st["url"] == want, mode
    assert config.AI_RAW_URL != config.AI_REVIEW_URL


def test_raw_url_not_set_says_which_setting(monkeypatch):
    monkeypatch.setattr(config, "AI_RAW_URL", "")
    A, B = _simple(["Fat 20%"]), _simple(["Fat 24%"])
    warns = []
    pr = _pr(A, B)
    ai_review.run_all([pr], "raw", warns, lambda *_: None, 0, _poster({}))
    assert pr["ai"]["status"] == "failed" and "ARTWORK_V2_AI_RAW_URL" in pr["ai"]["error"]
    assert warns and "ARTWORK_V2_AI_RAW_URL" in warns[0]


# ── ⑦ 6 ต.ค. (รอบ 4): ชั้นตรวจคำตอบซ้ำใน node Parse ของ workflow raw ─────────
# ผลสถานี: แดงปลอม 0 แต่ยังเหลือเหลืองปลอม "(calculated).." กับ "(calculated)." (จุดไข่ปลา
# นับต่าง — แอปยุบเฉพาะช่วงจุด ≥ 2) และข้อที่ถูกปฏิเสธเพราะยก "Acid *" ทั้งที่คำคือ "Acid*"

PARSE_HARNESS = r"""
const fs = require('fs');
const w = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const cfg = JSON.parse(fs.readFileSync(0, 'utf8'));
const code = (n) => w.nodes.find((x) => x.name === n).parameters.jsCode;
const built = new Function('$input', code('Build Gemini request'))(
  { first: () => ({ json: { body: cfg.body } }) })[0];
const dollar = cfg.no_dollar ? undefined : ((name) => {
  if (name !== 'Build Gemini request') throw new Error('unknown node ' + name);
  return { first: () => built };
});
const out = new Function('$input', '$', code('Parse Gemini response'))(
  { first: () => ({ json: cfg.gemini }) }, dollar)[0].json;
console.log(JSON.stringify(out));
"""


def _parse(body, items, summary="พบความต่าง", no_dollar=False):
    if not shutil.which("node"):
        pytest.skip("ไม่มี node")
    gem = {"candidates": [{"content": {"parts": [{"text": json.dumps(
        {"reviews": [], "items": items, "summary": summary, "suggestions": []})}]},
        "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 1}}
    cfg = {"body": body, "gemini": gem, "no_dollar": no_dollar}
    r = subprocess.run(["node", "-e", PARSE_HARNESS, "x", WF], input=json.dumps(cfg),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _zone(side, rows):
    return [{"id": "%s%d" % (side, i), "words": ws, "word_conf": [0.95] * len(ws)}
            for i, ws in enumerate(rows)]


def _it(aw, aq, bw, bq, verdict="uncertain", kind="punct"):
    return {"a_words": aw, "a_quote": aq, "b_words": bw, "b_quote": bq, "kind": kind,
            "verdict": verdict, "reason": "เหตุผลเดิม", "suggestion": "ดูด้วยตา"}


LEAD_BODY = {"zone_a": _zone("A", [["(calculated).."], ["1.5g.."], ["Ash", "(max)....4.0%"],
                                   ["Choice."], ["Choice…"], ["D-calcium"]]),
             "zone_b": _zone("B", [["(calculated)."], ["15g.."], ["Ash", "(max)....40%"],
                                   ["Choice"], ["Choice."], ["D-Calcium"]])}


def test_parse_dismisses_station_leader_dot_noise():
    out = _parse(LEAD_BODY, [_it(["A0:0"], "(calculated)..", ["B0:0"], "(calculated).",
                                 verdict="real")])
    it = out["items"][0]
    assert it["verdict"] == "noise" and it["reason"].startswith("[N8N]")
    assert "เหตุผลเดิม" in it["reason"]                      # เหตุผลของ AI ไม่หาย
    assert out["n8n_check"] == {"leader_noise": 1, "quote_ws_fixed": 0}
    assert "N8N ตรวจซ้ำ" in out["summary"] and out["summary"].startswith("พบความต่าง")
    # ไม่ไปยุ่งกับรหัสคำ/ข้อความที่ยกมา (แอปยังตรวจกับ Vision ตามเดิม)
    assert it["a_words"] == ["A0:0"] and it["a_quote"] == "(calculated).."


@pytest.mark.parametrize("item", [
    _it(["A1:0"], "1.5g..", ["B1:0"], "15g..", verdict="real", kind="number"),     # จุดทศนิยม
    _it(["A2:1"], "(max)....4.0%", ["B2:1"], "(max)....40%", verdict="real", kind="number"),
    _it(["A3:0"], "Choice.", ["B3:0"], "Choice", verdict="uncertain"),             # ไม่มีจุดไข่ปลา
    _it(["A4:0"], "Choice…", ["B4:0"], "Choice.", verdict="uncertain"),            # … ตัวเดียว
    _it(["A5:0"], "D-calcium", ["B5:0"], "D-Calcium", verdict="real", kind="case"),
    _it(["A0:0"], "(calculated)..", [], "", verdict="real", kind="missing"),        # ฝั่งเดียว
])
def test_parse_keeps_everything_that_is_not_only_leader_dots(item):
    out = _parse(LEAD_BODY, [item])
    assert out["items"] == [item]
    assert out["n8n_check"] == {"leader_noise": 0, "quote_ws_fixed": 0}
    assert out["summary"] == "พบความต่าง"


QUOTE_BODY = {"zone_a": _zone("A", [["Omega-6", "Fatty", "Acid*", "Content"]]),
              "zone_b": _zone("B", [["Omega-6", "Fatty", "Acid", "Content"]])}


def test_parse_fixes_whitespace_only_quote_mismatch():
    out = _parse(QUOTE_BODY, [_it(["A0:1", "A0:2"], "Fatty Acid *", ["B0:1", "B0:2"],
                                  "Fatty Acid", verdict="uncertain", kind="symbol")])
    it = out["items"][0]
    assert it["a_quote"] == "Fatty Acid*" and it["b_quote"] == "Fatty Acid"
    assert out["n8n_check"]["quote_ws_fixed"] == 1
    # ข้อความที่แก้แล้ว = ข้อความที่แอปเห็นจากรหัสเดียวกัน (กติกา _ws ของแอป)
    assert ai_review._ws(it["a_quote"]) == ai_review._ws("Fatty Acid*")


@pytest.mark.parametrize("aw,aq", [
    (["A0:1"], "Acid *"),              # รหัสชี้ "Fatty" — รหัสผิด ห้ามกลบด้วยการแก้ข้อความ
    (["A0:2"], "Acid +"),              # ตัวอักษรไม่ตรง
    (["A0:2", "B0:1"], "Acid *"),      # รหัสคนละฝั่ง/บรรทัด
    (["A0:9"], "Acid *"),              # ไม่มีคำนี้
])
def test_parse_never_launders_a_wrong_reference(aw, aq):
    item = _it(aw, aq, ["B0:2"], "Acid", kind="symbol")
    out = _parse(QUOTE_BODY, [item])
    assert out["items"] == [item] and out["n8n_check"]["quote_ws_fixed"] == 0


def test_parse_check_never_breaks_the_answer():
    # ไม่มี $ (เช่น N8N รุ่นอื่น) ⇒ แก้ข้อความที่ยกมาไม่ได้ แต่กติกาจุดไข่ปลายังทำงาน · ไม่ล้ม
    out = _parse(LEAD_BODY, [_it(["A0:0"], "(calculated)..", ["B0:0"], "(calculated).")],
                 no_dollar=True)
    assert "error" not in out and out["items"][0]["verdict"] == "noise"
    out = _parse(QUOTE_BODY, [_it(["A0:2"], "Acid *", ["B0:2"], "Acid")], no_dollar=True)
    assert out["items"][0]["a_quote"] == "Acid *"
    # ข้อที่ไม่ใช่ object / ไม่มีข้อความ ⇒ ส่งต่อเดิม
    out = _parse(LEAD_BODY, ["x", {}, None])
    assert out["items"] == ["x", {}, None]


def test_parse_leader_noise_is_dismissed_by_the_app():
    """ปลายทางจริง: ข้อที่ N8N เปลี่ยนเป็น noise ⇒ แอปพับลงรายการ ai_dismissed (ไม่หายเงียบ)"""
    A = _lines([("(calculated)..", 100, 100)])
    B = _lines([("(calculated).", 100, 100)])
    it = _parse(LEAD_BODY, [_it(["A0:0"], "(calculated)..", ["B0:0"], "(calculated).",
                                verdict="real")])["items"][0]
    f, why, kind = ai_review.check_item(it, A, B)
    assert kind == "ok", why
    pr, st = {"findings": []}, {}
    ai_review._merge_raw(pr, [f], [], st)
    assert pr["findings"] == [] and len(pr["ai_dismissed"]) == 1
