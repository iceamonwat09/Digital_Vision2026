"""AI ตรวจทาน (Gemini ผ่าน N8N) ของ Artwork V2 — ไม่ยิงเน็ตจริง

สิ่งที่ล็อกไว้ (กฎเหล็กข้อ 2 — ผลที่ผิดแบบมั่นใจแย่กว่าไม่แสดง):
* ค่าเริ่มต้นใหม่: ห้ามส่งซ้ำ (อ่านซ้ำปิด) · AI เปิดแบบ "assist"
* ข้อมูลที่ส่ง = ข้อความของ Vision เท่านั้น (ไม่มีภาพ ไม่มีกุญแจ) · โหมด judge ไม่ส่งผลอัลกอริทึม
* กรอบมาจาก Vision ตรงตัวอักษรที่ต่าง ไม่ใช่ทั้งคำ · % ความมั่นใจมาจาก Vision ไม่ใช่ AI
* คำตอบที่อ้างไม่ตรงข้อมูลถูกปฏิเสธพร้อมเหตุผล (นับเป็นความถูกต้องของการอ้างอิง)
* assist: AI ลบ/ลดระดับจุดแดงไม่ได้ · judge: จุดของอัลกอริทึมที่ AI ไม่ระบุไม่หายเงียบ
* N8N ล่ม ⇒ ผลเท่าโหมดปิด AI + คำเตือน
* Code node ของ workflow ตัวจริงรันผ่าน node · prompt ในเอกสารตรงกับใน workflow
"""

from __future__ import annotations

import importlib
import io
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from artwork_v2_fake import fta, load_run_dir  # noqa: E402

from artwork_v2 import (ai_review, compare, config, jobs, keystore, pipeline,  # noqa: E402
                        textmodel, vision_client)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, "artwork_v2", "n8n_artwork_v2_review.workflow.json")
DOC = os.path.join(ROOT, "docs", "N8N_ARTWORK_V2_REVIEW_PROMPT.md")
RUNS = os.path.join(os.path.dirname(__file__), "data", "artwork_v2", "station_runs")
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
    monkeypatch.setattr(config, "AI_REVIEW_URL", "http://127.0.0.1:9/webhook/artwork-v2-review")
    os.makedirs(config.JOBS_DIR)
    yield


# ── ตัวช่วย ──────────────────────────────────────────────────────────

def _lines(texts, confs=None):
    rows = []
    for i, t in enumerate(texts):
        kw = {"cw": 10, "h": 20}
        if confs and confs.get(i) is not None:
            kw["conf"] = confs[i]
        rows.append((t, 20, 40 + i * 40, kw))
    return textmodel.parse(fta(rows, 1000, 1000), 1000, 1000)["lines"]


def _cmp(ta, tb, ca=None, cb=None):
    r = compare.compare(_lines(ta, ca), _lines(tb, cb), (1000, 1000), (1000, 1000))
    return r, r["lines_a"], r["lines_b"]


def wid(lines, side, word, nth=0):
    """รหัสคำของ Vision ของคำ ``word`` (ครั้งที่ ``nth``)"""
    k = 0
    for i, ln in enumerate(lines):
        for j, (_, _, w) in enumerate(ai_review._words(ln["text"])):
            if w == word:
                if k == nth:
                    return "%s%d:%d" % (side, i, j)
                k += 1
    raise AssertionError("ไม่พบคำ %r" % word)


def _item(a_words, a_quote, b_words, b_quote, verdict="real", **kw):
    it = {"a_words": a_words, "a_quote": a_quote, "b_words": b_words, "b_quote": b_quote,
          "kind": "text", "verdict": verdict, "reason": "เหตุผล", "suggestion": "คำแนะนำ"}
    it.update(kw)
    return it


class _Resp:
    def __init__(self, data, status=200):
        self.status_code = status
        self._d = data
        self.text = json.dumps(data) if not isinstance(data, str) else data

    def json(self):
        if isinstance(self._d, str):
            raise ValueError("not json")
        return self._d


def _poster(answer, status=200, seen=None):
    def post(url, data=None, headers=None, timeout=None):
        if seen is not None:
            seen.append(json.loads(data.decode("utf-8")))
        a = answer(json.loads(data.decode("utf-8"))) if callable(answer) else answer
        return _Resp(a, status)
    return post


# ── ① ค่าเริ่มต้นใหม่ ─────────────────────────────────────────────────

def test_new_defaults_no_resend_and_ai_assist(monkeypatch):
    """ผู้ใช้สั่ง "ห้ามส่งซ้ำ" ⇒ อ่านซ้ำปิด · AI เปิดแบบอัลกอริทึมตัดสิน + AI เสริม"""
    for k in ("ARTWORK_V2_REREAD", "ARTWORK_V2_AI_MODE"):
        monkeypatch.delenv(k, raising=False)
    fresh = importlib.reload(config)
    try:
        assert fresh.REREAD_ENABLED is False
        assert fresh.AI_MODE == "assist"
        assert fresh.AI_REVIEW_URL.endswith("/webhook/artwork-v2-review")
        assert "127.0.0.1" in fresh.AI_REVIEW_URL
    finally:
        importlib.reload(config)


def test_unknown_mode_env_means_off(monkeypatch):
    monkeypatch.setenv("ARTWORK_V2_AI_MODE", "yes-please")
    try:
        assert importlib.reload(config).AI_MODE == "off"
    finally:
        monkeypatch.delenv("ARTWORK_V2_AI_MODE")
        importlib.reload(config)


def test_norm_mode():
    assert ai_review.norm_mode("JUDGE") == "judge"
    assert ai_review.norm_mode(None) == config.AI_MODE
    assert ai_review.norm_mode("x") == config.AI_MODE


# ── ② ข้อมูลที่ส่ง ────────────────────────────────────────────────────

def test_payload_is_vision_text_only_with_word_ids():
    r, A, B = _cmp(["Sodium 475 mg 20%"], ["Sodium 475 mg 24%"], ca={0: 0.97})
    for i, f in enumerate(r["findings"], 1):
        f["id"] = i
    p = ai_review.build_payload(1, "assist", A, B, (1000, 1000), (1000, 1000), r["findings"])
    za = p["zone_a"][0]
    assert za["id"] == "A0" and za["words"] == ["Sodium", "475", "mg", "20%"]
    assert len(za["word_conf"]) == 4 and za["word_conf"][0] == pytest.approx(0.97)
    assert all(0 <= v <= 1000 for v in za["box"])
    assert p["candidates"][0]["id"] == "F1"
    assert p["candidates"][0]["a"]["diff"] == "0" and p["candidates"][0]["b"]["diff"] == "4"
    blob = json.dumps(p)
    assert "jpeg" not in blob and "b64" not in blob


def test_judge_mode_does_not_send_algorithm_findings():
    """judge = AI หาเองอย่างอิสระ — ถ้าส่งผลอัลกอริทึมไป AI จะตามรายการนั้น (เทียบสองแนวทางไม่ได้)"""
    r, A, B = _cmp(["Fat 20%"], ["Fat 24%"])
    for i, f in enumerate(r["findings"], 1):
        f["id"] = i
    p = ai_review.build_payload(1, "judge", A, B, (1000, 1000), (1000, 1000), r["findings"])
    assert r["findings"] and p["candidates"] == []


# ── ③ คำตอบ → จุดต่าง (กรอบ/% จาก Vision) ──────────────────────────────

def test_box_is_the_differing_characters_not_the_whole_word():
    r, A, B = _cmp(["D-Calcium Pantothenate"], ["D-calcium Pantothenate"],
                   cb={0: 0.91})
    algo = r["findings"][0]
    f, why = ai_review.item_to_finding(
        _item([wid(A, "A", "D-Calcium")], "D-Calcium", [wid(B, "B", "D-calcium")], "D-calcium"), A, B)
    assert why == "" and f["class"] == "CASE"
    assert f["a"]["frag"] == "C" and f["b"]["frag"] == "c"
    # กรอบเท่ากับของอัลกอริทึม (ตัวอักษรเดียวกันของ Vision) — ไม่ใช่กรอบทั้งคำ
    assert f["a"]["box"] == algo["a"]["box"] and f["b"]["box"] == algo["b"]["box"]
    assert f["a"]["word_box"] == algo["a"]["word_box"] and f["b"]["word_box"] == algo["b"]["word_box"]
    assert f["confidence"] == pytest.approx(0.91)        # ค่าต่ำสุดของ Vision ทั้งสองฝั่ง


def test_confidence_is_from_vision_even_if_ai_sends_a_number():
    r, A, B = _cmp(["Fat 20%"], ["Fat 24%"], ca={0: 0.83}, cb={0: 0.99})
    f, _ = ai_review.item_to_finding(
        _item([wid(A, "A", "20%")], "20%", [wid(B, "B", "24%")], "24%", confidence=0.99,
              accuracy=1.0), A, B)
    assert f["confidence"] == pytest.approx(0.83)
    assert f["class"] == "NUMBER"


@pytest.mark.parametrize("item,reason", [
    (_item(["A9:0"], "x", ["B0:0"], "Fat"), "ไม่มีบรรทัด"),
    (_item(["B0:0"], "Fat", ["B0:0"], "Fat"), "ไม่ใช่ของฝั่ง"),
    (_item(["A0:7"], "x", ["B0:0"], "Fat"), "ไม่มีคำ"),
    (_item(["A0:0"], "Fit", ["B0:0"], "Fat"), "ไม่ตรงกับ Vision"),
    (_item(["A0:0"], "Fat", ["B0:0"], "Fat"), "เท่ากัน"),
    (_item(["A0:0"], "Fat", ["B0:0"], "Fat", verdict="maybe"), "verdict"),
    (_item([], "", [], ""), "ไม่ได้อ้าง"),
    (_item(["A0:0", "A1:0"], "Fat Net", ["B0:0"], "Fat"), "หลายบรรทัด"),
])
def test_answers_that_do_not_match_vision_are_rejected(item, reason):
    _, A, B = _cmp(["Fat 20%", "Net 85 g"], ["Fat 24%", "Net 85 g"])
    f, why = ai_review.item_to_finding(item, A, B)
    assert f is None and reason in why


def test_whitespace_only_difference_is_rejected():
    _, A, B = _cmp(["Size 4x14 cm"], ["Size 4 x14 cm"])
    f, why = ai_review.item_to_finding(
        _item([wid(A, "A", "4x14")], "4x14", [wid(B, "B", "4"), wid(B, "B", "x14")], "4 x14"), A, B)
    assert f is None and "ช่องว่าง" in why


def test_missing_word_is_located_by_its_neighbours():
    _, A, B = _cmp(["16321 Arrow Hwy, Irwindale Park, CA"], ["16321 Arrow Hwy Irwindale, CA"])
    f, why = ai_review.item_to_finding(
        _item([wid(A, "A", "Irwindale"), wid(A, "A", "Park,")], "Irwindale Park,",
              [wid(B, "B", "Irwindale,")], "Irwindale,"), A, B)
    assert why == ""
    assert "Park" in f["a"]["frag"] and f["a"]["box"] is not None and f["b"]["box"] is not None


def test_whole_line_missing_is_missing_in_b():
    _, A, B = _cmp(["Fat 20%", "Ash (max) 4.0%"], ["Fat 20%"])
    f, why = ai_review.item_to_finding(
        _item([wid(A, "A", "Ash"), wid(A, "A", "(max)"), wid(A, "A", "4.0%")], "Ash (max) 4.0%",
              [], ""), A, B)
    assert why == "" and f["class"] == "MISSING_IN_B" and f["b"]["line"] is None
    assert f["a"]["frag"] == "Ash (max) 4.0%"


# ── ④ รวมผล assist ───────────────────────────────────────────────────

def _pr(ta, tb, ca=None, cb=None):
    r, A, B = _cmp(ta, tb, ca, cb)
    for i, f in enumerate(r["findings"], 1):
        f["id"] = i
    return {"n": 1, "findings": r["findings"]}, A, B


def test_assist_ai_cannot_downgrade_a_red():
    pr, A, B = _pr(["Fat 20%"], ["Fat 24%"])
    assert pr["findings"][0]["severity"] == "red"
    st = ai_review.merge("assist", pr, {"reviews": [{"candidate": "F1", "verdict": "noise",
                                                      "reason": "r", "suggestion": "s"}]}, A, B)
    f = pr["findings"][0]
    assert f["severity"] == "red" and f["ai"]["verdict"] == "noise"
    assert st["reviews_valid"] == 1 and st["ref_accuracy"] == 1.0


def test_assist_extra_item_is_added_as_yellow_only():
    pr, A, B = _pr(["Fat 20%", "Breed & Lifestages"], ["Fat 24%", "Breeds & Lifestages"],
                   ca={1: 0.5})
    # อัลกอริทึมเห็น Breed/Breeds เป็นเหลือง (ความมั่นใจต่ำ) — จำลองว่าอัลกอริทึมพลาดจุดนี้
    pr["findings"] = [f for f in pr["findings"] if "Breed" not in f["a"]["text"]]
    st = ai_review.merge("assist", pr, {"items": [
        _item([wid(A, "A", "Breed")], "Breed", [wid(B, "B", "Breeds")], "Breeds")]}, A, B)
    extra = [f for f in pr["findings"] if f.get("source") == "ai"]
    assert st["extra_added"] == 1 and extra[0]["severity"] == "yellow"
    assert extra[0]["b"]["frag"] == "s"


_NEW_RULES = ("AI_QUOTE_RECOVER", "AI_EQUIV_NOISE", "AI_SEND_CURVED", "AI_JUDGE_KEEP_ALGO_RED",
              "AI_JUDGE_NOISE_GUARD", "AI_JUDGE_CURVED_YELLOW")


def _old_rules(monkeypatch):
    """ปิดชั้นตรวจคำตอบของ 6 ต.ค. ทั้งหมด = พฤติกรรมเดิมเป๊ะ (เทสต์รุ่นก่อนล็อกพฤติกรรมนี้)"""
    for k in _NEW_RULES:
        monkeypatch.setattr(config, k, False)


def test_assist_duplicate_item_attaches_to_existing_and_noise_extra_is_dropped(monkeypatch):
    _old_rules(monkeypatch)
    pr, A, B = _pr(["Fat 20%", "Net 85 g"], ["Fat 24%", "Net 85g"])
    pr["findings"] = [f for f in pr["findings"] if "Fat" in f["a"]["text"]]
    st = ai_review.merge("assist", pr, {"items": [
        _item([wid(A, "A", "20%")], "20%", [wid(B, "B", "24%")], "24%"),
        _item([wid(A, "A", "Net")], "Net", [wid(B, "B", "Net")], "Net", verdict="noise"),
        _item([wid(A, "A", "85"), wid(A, "A", "g")], "85 g", [wid(B, "B", "85g")], "85g",
              verdict="noise"),
    ]}, A, B)
    assert st["extra_duplicate"] == 1 and len(pr["findings"]) == 1
    assert pr["findings"][0]["ai"]["verdict"] == "real"
    # ข้อที่ 2 อ้างข้อความเท่ากัน ⇒ ปฏิเสธ · ข้อที่ 3 ต่างแค่ช่องว่าง ⇒ ปฏิเสธ
    assert len(st["invalid"]) == 2 and st["extra_noise"] == 0
    pr2, A2, B2 = _pr(["Fat 20%", "Breed 1"], ["Fat 24%", "Breeds 1"])
    pr2["findings"] = [f for f in pr2["findings"] if "Fat" in f["a"]["text"]]
    st2 = ai_review.merge("assist", pr2, {"items": [
        _item([wid(A2, "A", "Breed")], "Breed", [wid(B2, "B", "Breeds")], "Breeds",
              verdict="noise")]}, A2, B2)
    assert st2["extra_noise"] == 1 and len(pr2["findings"]) == 1      # noise ไม่ถูกเพิ่มเป็นจุด


def test_assist_unanswered_points_are_marked_not_hidden():
    pr, A, B = _pr(["Fat 20%"], ["Fat 24%"])
    st = ai_review.merge("assist", pr, {"reviews": [{"candidate": "F99", "verdict": "real",
                                                      "reason": "x"}]}, A, B)
    assert pr["findings"][0]["ai"]["verdict"] is None
    assert st["reviewed"] == 0 and st["reviewable"] == 1
    assert st["ref_accuracy"] == 0.0 and st["invalid"]


# ── ⑤ รวมผล judge ────────────────────────────────────────────────────

def test_judge_uses_vision_confidence_for_red():
    pr, A, B = _pr(["Fat 20%", "Net 85 g"], ["Fat 24%", "Net 86 g"], cb={1: 0.6})
    ai_review.merge("judge", pr, {"items": [
        _item([wid(A, "A", "20%")], "20%", [wid(B, "B", "24%")], "24%"),
        _item([wid(A, "A", "85")], "85", [wid(B, "B", "86")], "86"),
    ]}, A, B)
    sev = {f["a"]["frag"]: f["severity"] for f in pr["findings"]}
    assert sev == {"0": "red", "5": "yellow"}


def _judge_punct(monkeypatch, flag):
    monkeypatch.setattr(config, "AI_JUDGE_PUNCT_YELLOW", flag)
    pr, A, B = _pr(["Irwindale, CA 91706", "Fat 20%"], ["Irwindale CA 91706", "Fat 24%"])
    ai_review.merge("judge", pr, {"items": [
        _item([wid(A, "A", "Irwindale,")], "Irwindale,", [wid(B, "B", "Irwindale")], "Irwindale"),
        _item([wid(A, "A", "20%")], "20%", [wid(B, "B", "24%")], "24%"),
    ]}, A, B)
    return {f["class"]: f for f in pr["findings"]}


def test_judge_punctuation_only_is_yellow_even_when_vision_is_sure(monkeypatch):
    by = _judge_punct(monkeypatch, True)
    p = by["PUNCT"]
    assert p["confidence"] is not None and p["confidence"] >= config.CONF_FAIL
    assert p["severity"] == "yellow" and any("วรรคตอน" in n for n in p["notes"])
    assert by["NUMBER"]["severity"] == "red"          # ของอื่นยังแดงตามเดิม


def test_judge_punct_flag_off_is_old_behaviour(monkeypatch):
    by = _judge_punct(monkeypatch, False)
    assert by["PUNCT"]["severity"] == "red" and by["NUMBER"]["severity"] == "red"


def test_judge_noise_is_folded_and_algorithm_only_points_are_kept_visible(monkeypatch):
    _old_rules(monkeypatch)
    pr, A, B = _pr(["Fat 20%", "Net 85 g"], ["Fat 24%", "Net 86 g"])
    ai_review.merge("judge", pr, {"items": [
        _item([wid(A, "A", "20%")], "20%", [wid(B, "B", "24%")], "24%", verdict="noise")]}, A, B)
    assert pr["findings"] == []
    assert len(pr["ai_dismissed"]) == 1 and pr["ai_dismissed"][0]["severity"] == "dismissed"
    assert len(pr["algo_only"]) == 1 and pr["algo_only"][0]["a"]["frag"] == "5"


# ── ⑥ เรียก N8N ──────────────────────────────────────────────────────

def test_call_retries_connection_error_once_then_gives_up(monkeypatch):
    import requests
    n = []

    def post(url, **kw):
        n.append(1)
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(config, "AI_RETRIES", 1)
    resp, info = ai_review.call("http://x", {"a": 1}, post)
    assert resp is None and len(n) == 2 and "ต่อ N8N ไม่ได้" in info["error"]


def test_call_does_not_retry_404_and_reports_error_key():
    n = []

    def post(url, **kw):
        n.append(1)
        return _Resp({}, 404)
    resp, info = ai_review.call("http://x", {}, post)
    assert resp is None and len(n) == 1 and "404" in info["error"]
    resp, info = ai_review.call("http://x", {}, _poster({"error": "finishReason=MAX_TOKENS"}))
    assert resp is None and "MAX_TOKENS" in info["error"]
    resp, info = ai_review.call("http://x", {}, _poster("<html>"))
    assert resp is None and "ไม่ใช่ JSON" in info["error"]
    resp, info = ai_review.call("", {}, None)
    assert resp is None and "ARTWORK_V2_AI_REVIEW_URL" in info["error"]


# ── ⑦ ทั้งรอบ ────────────────────────────────────────────────────────

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


def _fake_annotate(texts_a, texts_b):
    def fake(groups, poster=None, key=None):
        res, ids = {}, []
        for g in groups:
            for it in g:
                ids.append(it["id"])
                W, H = Image.open(io.BytesIO(it["jpeg"])).size
                T = texts_a if it["id"].endswith("a") else texts_b
                lines = [(t, int(0.05 * W), int(H * (0.1 + 0.8 * i / len(T))),
                          {"cw": max(4, W // 40), "h": max(8, H // 15)}) for i, t in enumerate(T)]
                res[it["id"]] = {"ok": True, "error": "", "fta": fta(lines, W, H),
                                 "request_index": 0}
        return {"results": res, "calls": [{"index": 0, "phase": "main", "images": ids,
                                           "json_bytes": 10, "status": 200, "attempts": 1,
                                           "ms": 1, "error": "", "model_requested": config.MODEL,
                                           "endpoint": config.ENDPOINT, "at": "t"}]}
    return fake


def _run(monkeypatch, mode, answer, seen=None, status=200):
    monkeypatch.setattr(vision_client, "annotate", _fake_annotate(TA, TB))
    keystore.save(KEY)
    job = jobs.create(("a.pdf", _pdf(TA)), ("b.pdf", _pdf(TB)))["id"]
    return pipeline.run(job, FULL, ai_mode=mode, ai_poster=_poster(answer, status, seen))


def _answer_real(payload):
    a = next(l for l in payload["zone_a"] if "20%" in l["words"])
    b = next(l for l in payload["zone_b"] if "24%" in l["words"])
    return {"reviews": [{"candidate": c["id"], "verdict": "real", "reason": "ตัวเลขต่าง",
                         "suggestion": "ตรวจไฟล์จริง"} for c in payload["candidates"]],
            "items": [] if payload["mode"] == "assist" else [
                _item(["%s:%d" % (a["id"], a["words"].index("20%"))], "20%",
                      ["%s:%d" % (b["id"], b["words"].index("24%"))], "24%")],
            "summary": "พบ 1 จุด: A \"20%\" / B \"24%\"",
            "suggestions": ["ตรวจค่า Sodium บนไฟล์จริง"], "engine": "gemini-2.5-flash"}


def test_run_assist_end_to_end_no_key_no_image_in_payload(monkeypatch):
    seen = []
    r = _run(monkeypatch, "assist", _answer_real, seen)
    assert len(seen) == 1 and seen[0]["mode"] == "assist" and seen[0]["candidates"]
    blob = json.dumps(seen[0])
    assert KEY not in blob and "jpeg" not in blob
    assert r["verdict"] == "FAIL"
    pr = r["pairs"][0]
    assert pr["ai"]["status"] == "ok" and pr["ai"]["ref_accuracy"] == 1.0
    assert pr["ai"]["suggestions"] == ["ตรวจค่า Sodium บนไฟล์จริง"]
    assert pr["findings"][0]["ai"]["verdict"] == "real"
    assert pr["findings"][0]["confidence"] is not None
    assert r["ai"]["mode"] == "assist" and r["stage"]["ai_ms"] is not None
    assert "[AI REVIEW] mode=assist" in r["log_text"] and "ref_accuracy=1.000" in r["log_text"]
    assert "ai: verdict=real" in r["log_text"] and KEY not in r["log_text"]
    # ห้ามส่งซ้ำ: ภาพไปถึง Vision แค่การอ่านหลัก
    assert [c["phase"] for c in r["calls"]] == ["main"]


def test_run_judge_end_to_end(monkeypatch):
    seen = []
    r = _run(monkeypatch, "judge", _answer_real, seen)
    assert seen[0]["candidates"] == []
    pr = r["pairs"][0]
    assert r["verdict"] == "FAIL" and pr["findings"][0]["source"] == "ai"
    assert pr["findings"][0]["severity"] == "red" and pr["algo_only"] == []


def test_run_judge_pass_keeps_algorithm_points_visible(monkeypatch):
    _old_rules(monkeypatch)
    r = _run(monkeypatch, "judge", {"reviews": [], "items": [], "summary": "ไม่พบ",
                                    "suggestions": []})
    pr = r["pairs"][0]
    assert r["verdict"] == "PASS"
    assert len(pr["algo_only"]) == 1
    assert any("AI ไม่ได้ระบุ" in x for x in r["reasons"])


def test_n8n_failure_falls_back_to_algorithm_exactly(monkeypatch):
    off = _run(monkeypatch, "off", None)
    bad = _run(monkeypatch, "assist", {"error": "Gemini ตอบไม่จบ (finishReason=MAX_TOKENS)"})
    assert bad["verdict"] == off["verdict"]
    strip = lambda fs: [(f["class"], f["severity"], f["a"]["frag"], f["b"]["frag"]) for f in fs]  # noqa: E731
    assert strip(bad["pairs"][0]["findings"]) == strip(off["pairs"][0]["findings"])
    assert bad["pairs"][0]["ai"]["status"] == "failed"
    assert any("AI ตรวจทานไม่สำเร็จ" in w for w in bad["warnings"])
    for m in ("judge",):
        j = _run(monkeypatch, m, {}, status=500)
        assert j["verdict"] == off["verdict"] and not j["pairs"][0].get("algo_only")


def test_off_mode_never_calls_n8n(monkeypatch):
    seen = []
    r = _run(monkeypatch, "off", _answer_real, seen)
    assert seen == [] and r["pairs"][0]["ai"]["status"] == "off"


def test_garbage_answer_does_not_crash_the_run(monkeypatch):
    r = _run(monkeypatch, "assist", {"reviews": "nope", "items": [1, None, {"verdict": "real"}]})
    pr = r["pairs"][0]
    assert r["verdict"] == "FAIL"
    assert pr["ai"]["status"] == "ok" and pr["ai"]["items_valid"] == 0
    assert len(pr["ai"]["invalid"]) == 3


# ── ⑧ ข้อมูลจริงจากสถานี — กรอบของ AI ตรงกับกรอบของอัลกอริทึม ────────────

def test_station_data_ai_boxes_match_algorithm_boxes():
    ds = load_run_dir(os.path.join(RUNS, "avoderm_m1m2_run002"))
    (Wa, Ha, LA), (Wb, Hb, LB) = ds[1]["A"], ds[1]["B"]
    r = compare.compare(LA, LB, (Wa, Ha), (Wb, Hb))
    A, B = r["lines_a"], r["lines_b"]
    algo = next(f for f in r["findings"] if f["class"] == "CASE")
    f, why = ai_review.item_to_finding(
        _item([wid(A, "A", "D-calcium")], "D-calcium", [wid(B, "B", "D-Calcium")], "D-Calcium"), A, B)
    assert why == ""
    assert f["a"]["box"] == algo["a"]["box"] and f["b"]["box"] == algo["b"]["box"]
    assert f["a"]["word_box"] == algo["a"]["word_box"] and f["b"]["word_box"] == algo["b"]["word_box"]
    assert f["confidence"] == pytest.approx(min(algo["a"]["conf"], algo["b"]["conf"]), abs=1e-4)
    p = ai_review.build_payload(1, "assist", A, B, (Wa, Ha), (Wb, Hb), [])
    assert 20_000 < len(json.dumps(p, ensure_ascii=False)) < 200_000


# ── ⑨ workflow ของ N8N (รัน Code node ตัวจริงผ่าน node) ──────────────────

BUILD, PARSE = "Build Gemini request", "Parse Gemini response"
HARNESS = r"""
const fs = require('fs');
const w = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const cfg = JSON.parse(fs.readFileSync(0, 'utf8'));
const code = (n) => w.nodes.find((x) => x.name === n).parameters.jsCode;
const run = (name, items) => new Function('$input', code(name))(
  { first: () => items[0], all: () => items });
const built = run(process.argv[3], [{ json: { body: cfg.body } }])[0].json;
let out = null;
if (cfg.gemini !== undefined) out = run(process.argv[4], [{ json: cfg.gemini }])[0].json;
console.log(JSON.stringify({ built, out }));
"""


def _wf(body, gemini=None):
    if not shutil.which("node"):
        pytest.skip("ไม่มี node")
    cfg = {"body": body}
    if gemini is not None:
        cfg["gemini"] = gemini
    r = subprocess.run(["node", "-e", HARNESS, "x", WF, BUILD, PARSE], input=json.dumps(cfg),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


BODY = {"mode": "assist", "zone_a": [{"id": "A0", "words": ["Fat", "20%"], "word_conf": [1, 1]}],
        "zone_b": [{"id": "B0", "words": ["Fat", "24%"], "word_conf": [1, 1]}],
        "candidates": [{"id": "F1"}]}


def _gem(obj, finish="STOP", thought=False):
    parts = [{"text": "thinking...", "thought": True}] if thought else []
    parts.append({"text": obj if isinstance(obj, str) else json.dumps(obj)})
    return {"candidates": [{"content": {"parts": parts}, "finishReason": finish}],
            "usageMetadata": {"promptTokenCount": 10}}


def test_workflow_request_is_text_only_deterministic_and_schema_bound():
    b = _wf(BODY)["built"]
    assert b["valid"] is True
    req = b["gemini_request"]
    gc = req["generationConfig"]
    assert gc["temperature"] == 0 and gc["responseMimeType"] == "application/json"
    assert set(gc["responseSchema"]["required"]) == {"reviews", "items", "summary", "suggestions"}
    assert gc["thinkingConfig"]["thinkingBudget"] >= 4096
    parts = req["contents"][0]["parts"]
    assert len(parts) == 1 and "inlineData" not in parts[0]
    data = json.loads(parts[0]["text"].split("\n", 1)[1])
    assert data["candidates"] == [{"id": "F1"}] and data["zone_a"][0]["id"] == "A0"
    item_schema = gc["responseSchema"]["properties"]["items"]["items"]
    assert "confidence" not in json.dumps(gc["responseSchema"])      # % ห้ามมาจาก AI
    assert {"a_words", "b_words", "a_quote", "b_quote"} <= set(item_schema["required"])


def test_workflow_judge_drops_candidates_and_rejects_bad_body():
    b = _wf(dict(BODY, mode="judge"))["built"]
    data = json.loads(b["gemini_request"]["contents"][0]["parts"][0]["text"].split("\n", 1)[1])
    assert data["mode"] == "judge" and data["candidates"] == []
    bad = _wf({"mode": "assist"})["built"]
    assert bad["valid"] is False and bad["error"]


def test_workflow_parse_ok_skips_thoughts_and_fences():
    ans = {"reviews": [], "items": [{"a_words": ["A0:1"]}], "summary": "s", "suggestions": ["x", 3]}
    out = _wf(BODY, _gem("```json\n" + json.dumps(ans) + "\n```", thought=True))["out"]
    assert "error" not in out and out["items"] == ans["items"] and out["suggestions"] == ["x"]
    assert out["usage"] == {"promptTokenCount": 10}


@pytest.mark.parametrize("gem,needle", [
    (_gem({"reviews": []}, finish="MAX_TOKENS"), "MAX_TOKENS"),
    (_gem("not json"), "ไม่ใช่ JSON"),
    (_gem(""), "ว่าง"),
    ({"error": {"code": 429, "message": "Resource exhausted"}}, "Resource exhausted"),
    ({"promptFeedback": {"blockReason": "SAFETY"}}, "SAFETY"),
])
def test_workflow_parse_failures_always_return_error(gem, needle):
    out = _wf(BODY, gem)["out"]
    assert needle in out["error"]


@pytest.mark.parametrize("err", [
    {"message": "timeout of 170000ms exceeded", "name": "AxiosError"},   # HTTP node onError=continue
    "The connection to the server was closed unexpectedly",
])
def test_workflow_parse_turns_http_node_errors_into_error(err):
    out = _wf(BODY, {"error": err})["out"]
    assert out["error"].startswith("Gemini error:") and "reviews" not in out


def test_workflow_is_importable_and_never_answers_silently():
    """โครงที่ n8n ต้องการตอน import + ทุกทางจบที่ Respond (ไม่ค้าง/ไม่ 500 เงียบ)"""
    w = json.load(open(WF, encoding="utf-8"))
    names = [n["name"] for n in w["nodes"]]
    assert len(names) == len(set(names))
    ids = [n["id"] for n in w["nodes"]]
    assert len(ids) == len(set(ids))
    for n in w["nodes"]:
        assert {"parameters", "id", "name", "type", "typeVersion", "position"} <= set(n)
    for src, outs in w["connections"].items():
        assert src in names
        for branch in outs["main"]:
            for c in branch:
                assert c["node"] in names
    hook = next(n for n in w["nodes"] if n["type"] == "n8n-nodes-base.webhook")
    assert hook["parameters"]["responseMode"] == "responseNode" and hook.get("webhookId")
    http = next(n for n in w["nodes"] if n["type"] == "n8n-nodes-base.httpRequest")
    assert http.get("onError") == "continueRegularOutput"
    assert http["parameters"]["options"]["response"]["response"]["neverError"] is True
    # Gemini ต้องหมดเวลาก่อนแอป ไม่งั้นแอปตัดสายก่อนได้คำตอบ {error} — โหมดที่รอนานสุด (raw)
    # assist/judge แอปเลิกรอที่ AI_TIMEOUT_S เหมือนเดิม (N8N รอนานกว่าได้ แค่ไม่มีใครรอคำตอบ)
    assert http["parameters"]["options"]["timeout"] < config.AI_RAW_TIMEOUT_S * 1000
    assert config.AI_TIMEOUT_S <= config.AI_RAW_TIMEOUT_S
    assert http["parameters"]["nodeCredentialType"] == "googleApi"
    # ทุกทางจาก Webhook ต้องไปจบที่ Respond to Webhook
    nxt = {s: [c["node"] for br in o["main"] for c in br] for s, o in w["connections"].items()}
    types = {n["name"]: n["type"] for n in w["nodes"]}
    stack, ends = [hook["name"]], set()
    while stack:
        x = stack.pop()
        if not nxt.get(x):
            ends.add(types[x])
        stack += nxt.get(x, [])
    assert ends == {"n8n-nodes-base.respondToWebhook"}


def test_doc_prompt_matches_workflow_prompt():
    w = json.load(open(WF, encoding="utf-8"))
    code = next(n for n in w["nodes"] if n["name"] == BUILD)["parameters"]["jsCode"]
    wf_prompt = re.search(r"const PROMPT = `(.*?)`;", code, re.S).group(1)
    doc = open(DOC, encoding="utf-8").read()
    doc_prompt = re.search(r"<!-- PROMPT START -->\n```text\n(.*?)\n```\n<!-- PROMPT END -->",
                           doc, re.S).group(1)
    assert doc_prompt == wf_prompt
    for rule in ("ONLY source of truth", "Never output any number for confidence",
                 "never \"real\", never \"noise\"", "a_quote / b_quote"):
        assert rule in wf_prompt
    assert "/webhook/artwork-v2-review" in doc or "artwork-v2-review" in doc
    assert next(n for n in w["nodes"] if n["name"] == "Webhook")["parameters"]["path"] == \
        "artwork-v2-review"


# ── ⑩ หน้าเว็บ ───────────────────────────────────────────────────────

def test_page_has_ai_selector_and_js_sends_mode():
    html = open(os.path.join(ROOT, "templates", "artwork_v2.html"), encoding="utf-8").read()
    js = open(os.path.join(ROOT, "static", "js", "artwork_v2.js"), encoding="utf-8").read()
    for v in config.AI_MODES:
        assert 'value="%s"' % v in html
    assert 'id="v2Ai"' in html and 'ai_mode: $("v2Ai")' in js
    assert "ความมั่นใจ (Vision)" in js and "ไม่ใช่จาก AI" in js


# ── prompt: คำข้างเคียงของคำที่หาย ต้องอ้างทั้งสองฝั่ง ─────────────────

def test_missing_word_anchor_on_both_sides_marks_the_gap_not_the_neighbour():
    """กติกาใน prompt: คำที่หายกลางบรรทัด ⇒ อ้างคำข้างเคียง 1 คำที่มีทั้งสองฝั่ง ทั้งสองฝั่ง
    (อ้างคำข้างเคียงเฉพาะฝั่งที่ขาด = แอปกรอบคำข้างเคียงว่าเป็นคำที่เปลี่ยน)"""
    _, A, B = _cmp(["Biotin, Pantothenate, D-Calcium Thiamine"], ["Biotin, Pantothenate, Thiamine"])
    good, why = ai_review.item_to_finding(
        _item([wid(A, "A", "Pantothenate,"), wid(A, "A", "D-Calcium")], "Pantothenate, D-Calcium",
              [wid(B, "B", "Pantothenate,")], "Pantothenate,"), A, B)
    assert why == ""
    assert good["a"]["frag"].strip() == "D-Calcium"
    assert good["b"]["frag"] == "" and good["b"]["word_box"] is None     # จุดแทรก ไม่ใช่คำ
    bad, _ = ai_review.item_to_finding(
        _item([wid(A, "A", "D-Calcium")], "D-Calcium",
              [wid(B, "B", "Pantothenate,")], "Pantothenate,"), A, B)
    assert bad["b"]["frag"] == "Pantothenate,"      # ⇐ เหตุที่ prompt ห้ามอ้างแบบนี้


def test_prompt_rules_from_review_are_present():
    w = json.load(open(os.path.join(ROOT, "artwork_v2", "n8n_artwork_v2_review.workflow.json"),
                       encoding="utf-8"))
    code = next(n for n in w["nodes"] if n["name"] == "Build Gemini request")["parameters"]["jsCode"]
    p = re.search(r"const PROMPT = `(.*?)`;", code, re.S).group(1)
    assert "exactly ONE neighbouring word that exists on BOTH sides" in p
    assert 'take it from the field "mode"' in p
    assert "low word_conf alone is not enough" in p
    assert "No Markdown, no code fences" in p
    assert '"type": "OBJECT"' not in p and "SCHEMA" not in p    # ไม่ใส่ schema ซ้ำใน prompt


# ── 6 ต.ค.: รหัสคำสำเร็จรูปใน node Build (Gemini คัดรหัส ไม่นับเอง) ─────────

def test_workflow_gives_gemini_ready_made_word_ids():
    body = {"mode": "judge",
            "zone_a": [{"id": "A32", "box": [1, 2, 3, 4], "conf": 0.97,
                        "words": ["Copper", "Sulphate", "Pentahydrate."], "word_conf": [0.99, 0.96]}],
            "zone_b": [{"id": "B27", "words": ["OMEGA-62"], "word_conf": [0.5], "curved": True}],
            "candidates": [{"id": "F1"}]}
    b = _wf(body)["built"]
    data = json.loads(b["gemini_request"]["contents"][0]["parts"][0]["text"].split("\n", 1)[1])
    a = data["zone_a"][0]
    assert a["w"] == [["A32:0", "Copper", 0.99], ["A32:1", "Sulphate", 0.96],
                      ["A32:2", "Pentahydrate.", None]]          # word_conf ขาด = null ไม่เดา
    assert "words" not in a and "word_conf" not in a and "curved" not in a
    assert (a["id"], a["box"], a["conf"]) == ("A32", [1, 2, 3, 4], 0.97)
    assert data["zone_b"][0]["curved"] is True and data["zone_b"][0]["box"] is None
    assert data["candidates"] == []                               # judge ไม่ส่งผลอัลกอริทึม
    assert b["gemini_request"]["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 8192


def test_workflow_ids_match_what_the_app_resolves():
    """รหัสที่ node สร้าง = รหัสที่ ai_review._resolve เข้าใจ (ทุกคำของทุกบรรทัด)"""
    _, A, B = _cmp(["Potassium Iodide), Copper Sulphate Pentahydrate.", "Fat 20%"],
                   ["Potassium Iodide), Copper Sulfate.", "Fat 24%"])
    p = ai_review.build_payload(1, "judge", A, B, (1000, 1000), (1000, 1000), [])
    data = json.loads(_wf(p)["built"]["gemini_request"]["contents"][0]["parts"][0]["text"]
                      .split("\n", 1)[1])
    for side, L, S in (("zone_a", A, "A"), ("zone_b", B, "B")):
        for ln in data[side]:
            for wid_, text, _ in ln["w"]:
                r, err = ai_review._resolve([wid_], S, L)
                assert err == "" and L[r[0]]["text"][r[1]:r[2]] == text


def test_prompt_rules_from_station_run_20261006():
    w = json.load(open(WF, encoding="utf-8"))
    code = next(n for n in w["nodes"] if n["name"] == BUILD)["parameters"]["jsCode"]
    p = re.search(r"const PROMPT = `(.*?)`;", code, re.S).group(1)
    for rule in ("copy the wordId of each cited word from w", "Never count word positions yourself",
                 "never quote only a part of a word", "EVERY cited word on both sides",
                 '"curved": true is at most "uncertain"', "never report it, not even as noise",
                 "leader dots", "Do not write confidence numbers"):
        assert rule in p, rule
    assert "word_conf (Vision lowest" not in p        # รูปแบบข้อมูลเก่า (words/word_conf) ไม่อยู่ใน prompt
