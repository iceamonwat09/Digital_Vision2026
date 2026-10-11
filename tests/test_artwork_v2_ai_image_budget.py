"""Artwork V2 · โหมด image — กัน Gemini วนพิมพ์ซ้ำจนชนเพดาน (สถานี 11 ต.ค. 2026)

งาน 20261011_005715_08399e: 5 จุด → คำตอบ 61,631 token (ส่วนคิดแค่ 3,891) ชน maxOutputTokens 65536
หลังรอ 187 วิ ⇒ AI ล้ม · ล็อก: เพดานคำตอบตามจำนวนจุด · reviews ไม่เกินจำนวนจุด · prompt สั่งให้สั้น/ห้ามวน ·
node Parse บอกจำนวน token + ท้ายคำตอบ · แอปเก็บ usage ของคำขอที่ล้มลง Log
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_artwork_v2_ai_image_crops as C  # noqa: E402
from artwork_v2 import ai_review, config, diaglog  # noqa: E402

WF = C.WF
DOC = os.path.join(ROOT, "docs", "N8N_ARTWORK_V2_IMAGE_PROMPT.md")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RETRY_WAIT_S", 0.0)
    monkeypatch.setattr(config, "AI_RETRIES", 0)
    yield


def _code(name):
    w = json.load(open(WF, encoding="utf-8"))
    return next(n for n in w["nodes"] if n["name"] == name)["parameters"]["jsCode"]


def _body(n, blind=False):
    cands = [{"id": "F%d" % i, "a": {"crop": {"w": 600, "h": 120, "box": [1, 2, 3, 4]}},
              "b": {"crop": {"w": 600, "h": 120, "box": [1, 2, 3, 4]}}} for i in range(1, n + 1)]
    crops = [C._crop(c["id"], s, "QUJD") for c in cands for s in ("a", "b")]
    b = {"mode": "image", "candidates": cands, "crops": crops}
    if blind:
        b["blind"] = True
    else:
        b.update(zone_a=[{"id": "A0", "words": ["x"], "word_conf": [1]}],
                 zone_b=[{"id": "B0", "words": ["y"], "word_conf": [1]}])
    return b


# ── ① เพดานคำตอบ + schema ────────────────────────────────────────────

@pytest.mark.parametrize("n,blind", [(1, False), (5, True), (5, False), (100, True)])
def test_output_cap_scales_with_candidates(n, blind):
    b = C._build(_body(n, blind))
    assert b["valid"], b["error"]
    gc = b["gemini_request"]["generationConfig"]
    think = gc["thinkingConfig"]["thinkingBudget"]
    assert gc["maxOutputTokens"] == think + min(32768, 2048 + 512 * n)
    assert gc["maxOutputTokens"] < 65536                     # เดิมตายตัว = วนได้ถึง 61k token
    sch = gc["responseSchema"]
    assert sch["properties"]["reviews"]["maxItems"] == n
    assert sch["properties"]["suggestions"]["maxItems"] == 8


def test_station_case_would_stop_far_earlier():
    gc = C._build(_body(5, True))["gemini_request"]["generationConfig"]
    answer = gc["maxOutputTokens"] - gc["thinkingConfig"]["thinkingBudget"]
    assert answer * 10 < 61631                               # คำตอบที่วนบนสถานี


def test_schema_constant_is_not_mutated_between_requests():
    code = _code("Build Gemini request")
    assert "JSON.parse(JSON.stringify(SCHEMA))" in code and "responseSchema: schema," in code


@pytest.mark.parametrize("const", ["PROMPT", "PROMPT_BLIND"])
def test_prompts_ask_for_short_fields_and_no_repetition(const):
    p = re.search(r"const %s = `(.*?)`;" % const, _code("Build Gemini request"), re.S).group(1)
    assert "at most 80 characters" in p and "Never repeat a character, word or phrase" in p
    doc = open(DOC, encoding="utf-8").read()
    assert doc.count("Never repeat a character, word or phrase") == 2
    assert "61,631" in doc


# ── ② node Parse บอกว่าหมดไปกับอะไร ────────────────────────────────

PARSE = r"""
const fs = require('fs');
const w = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const r = JSON.parse(fs.readFileSync(0, 'utf8'));
const code = w.nodes.find((x) => x.name === 'Parse Gemini response').parameters.jsCode;
const out = new Function('$input', code)({ first: () => ({ json: r }) });
console.log(JSON.stringify(out[0].json));
"""


def _parse(r):
    if not shutil.which("node"):
        pytest.skip("ไม่มี node")
    p = subprocess.run(["node", "-e", PARSE, "x", WF], input=json.dumps(r),
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_parse_reports_tokens_and_tail_on_max_tokens():
    loop = '{"reviews":[{"candidate":"F3","a_seen":"(١٠٠غ' + "٠" * 400 + "XYZ"
    r = {"candidates": [{"finishReason": "MAX_TOKENS",
                         "content": {"parts": [{"thought": True, "text": "SECRET-THINKING"},
                                               {"text": loop}]}}],
         "usageMetadata": {"candidatesTokenCount": 61631, "thoughtsTokenCount": 3891}}
    out = _parse(r)
    e = out["error"]
    assert "MAX_TOKENS" in e and "61631" in e and "3891" in e
    assert "ท้ายคำตอบ" in e and e.endswith("XYZ") and "SECRET-THINKING" not in e
    assert len(e) <= 500 and out["usage"]["candidatesTokenCount"] == 61631


def test_parse_ok_path_unchanged():
    ans = {"reviews": [], "items": [], "summary": "s", "suggestions": ["a"]}
    out = _parse({"candidates": [{"finishReason": "STOP",
                                  "content": {"parts": [{"text": json.dumps(ans)}]}}]})
    assert "error" not in out and out["suggestions"] == ["a"] and out["finish_reason"] == "STOP"


# ── ③ แอปเก็บ usage ของคำขอที่ล้ม ─────────────────────────────────────

FAIL = {"error": "Gemini ตอบไม่จบ (finishReason=MAX_TOKENS)", "engine": "gemini",
        "usage": {"candidatesTokenCount": 61631, "thoughtsTokenCount": 3891}}


def test_call_keeps_usage_of_failed_answer():
    resp, info = ai_review.call("http://x", {"a": 1}, poster=C._poster(FAIL))
    assert resp is None and "MAX_TOKENS" in info["error"]
    assert info["usage"]["candidatesTokenCount"] == 61631
    resp, info = ai_review.call("http://x", {"a": 1}, poster=C._poster([FAIL]))
    assert info["usage"]["thoughtsTokenCount"] == 3891


def test_failed_pair_logs_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AI_IMAGE_SAFETY", False)
    monkeypatch.setattr(config, "AI_IMAGE_CROPS", True)
    monkeypatch.setattr(config, "AI_IMAGE_BLIND", False)
    monkeypatch.setattr(config, "AI_IMAGE_CROP_HIRES", False)
    monkeypatch.setattr(config, "AI_IMAGE_URL", "http://127.0.0.1:9/webhook/artwork-v2-image")
    pr, _ = C._pr(C.TA, C.TB, str(tmp_path))
    before = json.dumps(pr["findings"], sort_keys=True, default=str)
    st, _ = C._go(pr, FAIL, tmp_path)
    assert st["pairs_failed"] == 1 and pr["ai"]["status"] == "failed"
    assert pr["ai"]["usage"]["candidatesTokenCount"] == 61631
    # ผลของอัลกอริทึมเท่าเดิม (ai_crop = ภาพที่ส่งจริง ถูกเก็บก่อนยิงเสมอ — ไว้ไล่ปัญหาแม้ AI ล้ม)
    after = [{k: v for k, v in f.items() if k != "ai_crop"} for f in pr["findings"]]
    assert json.dumps(after, sort_keys=True, default=str) == before
    src = open(diaglog.__file__, encoding="utf-8").read()
    assert 'if x.get("usage"):\n                a("     usage=%s"' in src
