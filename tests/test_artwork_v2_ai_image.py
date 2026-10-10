"""โหมด AI "ดูภาพตัดสิน" (``image`` · 9 ต.ค. 2026) — ไม่ยิงเน็ตจริง

ผู้ใช้สั่ง: ส่งผล OCR ให้ Gemini **พร้อมกับรูปคู่นั้น** ให้ตรวจข้อมูลเทียบกับรูป · สร้าง Flow N8N ใหม่ ·
เพิ่มเป็นโหมด · เลือกเอง: Gemini ตัดสินเต็มที่ · ส่งภาพแบบเดียวกับที่ส่ง Vision · ตรวจเฉพาะจุดของอัลกอริทึม

สิ่งที่ล็อกไว้:
* ภาพที่ส่ง = ไฟล์ ``img/p<N>_<a|b>.jpg`` ของรอบ **ทุกไบต์** · ไม่ยิง Vision เพิ่ม
* candidates มีกรอบ 0-1000 บนภาพของแต่ละฝั่ง · payload ของ assist ไม่เปลี่ยน
* real = แดง · noise = รายการพับ ``ai_dismissed`` (ไม่ลบ) · uncertain = เหลือง ·
  ไม่ได้ตอบ = คงระดับเดิม · items ไม่ถูกใช้ · ``AI_IMAGE_SAFETY`` (ค่าเริ่มต้นปิด)
* N8N ล่ม/อ่านภาพไม่ได้ ⇒ ผลอัลกอริทึมทุกรายการ · ไม่มีจุดต่าง ⇒ ไม่ยิง
* workflow แยก (artwork-v2-image) · เอกสารตรงกับ workflow · workflow อื่นไม่ถูกแตะ
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from artwork_v2_fake import fta  # noqa: E402

from artwork_v2 import (ai_review, compare, config, jobs, keystore,  # noqa: E402
                        pipeline, textmodel, vision_client)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, "artwork_v2", "n8n_artwork_v2_image.workflow.json")
DOC = os.path.join(ROOT, "docs", "N8N_ARTWORK_V2_IMAGE_PROMPT.md")
WF_REVIEW = os.path.join(ROOT, "artwork_v2", "n8n_artwork_v2_review.workflow.json")
WF_RAW = os.path.join(ROOT, "artwork_v2", "n8n_artwork_v2_raw.workflow.json")
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
    monkeypatch.setattr(config, "AI_IMAGE_SAFETY", False)
    # ไฟล์นี้ล็อกเส้นทาง "ภาพทั้งโซน" (contract /1) — ครอปต่อจุดอยู่ใน test_artwork_v2_ai_image_crops.py
    monkeypatch.setattr(config, "AI_IMAGE_CROPS", False)
    monkeypatch.setattr(config, "AI_REVIEW_URL", "http://127.0.0.1:9/webhook/artwork-v2-review")
    monkeypatch.setattr(config, "AI_RAW_URL", "http://127.0.0.1:9/webhook/artwork-v2-raw")
    monkeypatch.setattr(config, "AI_IMAGE_URL", "http://127.0.0.1:9/webhook/artwork-v2-image")
    os.makedirs(config.JOBS_DIR)
    yield


# ── ตัวช่วย ──────────────────────────────────────────────────────────

def _simple(texts):
    rows = [(t, 20, 40 + i * 40, {"cw": 10, "h": 20}) for i, t in enumerate(texts)]
    return textmodel.parse(fta(rows, 1000, 1000), 1000, 1000)["lines"]


def _pr(texts_a, texts_b, img_dir=None):
    A, B = _simple(texts_a), _simple(texts_b)
    r = compare.compare(A, B, (1000, 1000), (1000, 1000))
    for i, f in enumerate(r["findings"], 1):
        f["id"] = i
    sides = {}
    for s in ("a", "b"):
        sides[s] = {"sent_px": [1000, 1000], "stats": {"conf_mean": 0.97}, "image": "p1_%s.jpg" % s}
        if img_dir is not None:
            with open(os.path.join(img_dir, "p1_%s.jpg" % s), "wb") as fh:
                fh.write(("JPEG-" + s).encode())
    return {"n": 1, "findings": r["findings"], "curved_lines": r["curved_lines"],
            "reflow_lines": r["reflow_lines"], "_cmp": r, "_raw": {"a": A, "b": B},
            "sides": sides}


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


def _rev(cid, verdict, a="", b=""):
    return {"candidate": cid, "a_seen": a, "b_seen": b, "verdict": verdict,
            "reason": "เหตุผล", "suggestion": "คำแนะนำ"}


def _ans(reviews, items=None):
    return {"reviews": reviews, "items": items or [], "summary": "สรุป", "suggestions": ["ตรวจ"],
            "engine": "gemini-2.5-flash"}


def _go(pr, answer, tmp_path, seen=None, warns=None, timeouts=None, urls=None, status=200):
    w = [] if warns is None else warns
    st, nid = ai_review.run_all([pr], "image", w, lambda *_: None, 100,
                                _poster(answer, seen, status, timeouts, urls),
                                img_dir=str(tmp_path))
    return st, nid


TA = ["Sodium 475 mg 20%", "Fat 1.5g", "Net weight 85 g"]
TB = ["Sodium 475 mg 24%", "Fat 15g", "Net weight 85 g"]


# ── ① config / โหมด ──────────────────────────────────────────────────

def test_image_mode_is_known_and_defaults_unchanged():
    assert "image" in config.AI_MODES
    assert ai_review.norm_mode("image") == "image" and ai_review.norm_mode("IMAGE ") == "image"
    assert config.AI_IMAGE_URL.endswith("/webhook/artwork-v2-image")
    assert ai_review._url_of("image") == config.AI_IMAGE_URL
    assert ai_review._url_of("raw") == config.AI_RAW_URL
    assert ai_review._url_of("assist") == ai_review._url_of("judge") == config.AI_REVIEW_URL


def test_assist_payload_is_unchanged_by_image_mode():
    """กรอบ 0-1000 ใส่ให้เฉพาะโหมด image — payload ของ workflow review เท่าเดิม"""
    pr = _pr(TA, TB)
    p = ai_review.build_payload(1, "assist", pr["_cmp"]["lines_a"], pr["_cmp"]["lines_b"],
                                (1000, 1000), (1000, 1000), pr["findings"])
    assert p["contract"] == "artwork-v2-review/1"
    assert all("box" not in c["a"] and "box" not in c["b"] for c in p["candidates"])
    q = ai_review.build_payload(1, "image", pr["_cmp"]["lines_a"], pr["_cmp"]["lines_b"],
                                (1000, 1000), (1000, 1000), pr["findings"])
    assert q["contract"] == "artwork-v2-image/1" and len(q["candidates"]) == len(pr["findings"])
    for c in q["candidates"]:
        for s in ("a", "b"):
            b = c[s]["box"]
            assert b and all(0 <= v <= 1000 for v in b) and b[0] < b[2] and b[1] < b[3]


# ── ② ข้อมูลที่ส่ง ─────────────────────────────────────────────────────

def test_sends_the_exact_image_files_and_candidates(tmp_path):
    pr = _pr(TA, TB, str(tmp_path))
    seen, to, urls = [], [], []
    _go(pr, _ans([]), tmp_path, seen, timeouts=to, urls=urls)
    assert len(seen) == 1 and urls == [config.AI_IMAGE_URL] and to == [config.AI_IMAGE_TIMEOUT_S]
    body = seen[0]
    assert body["mode"] == "image"
    for s in ("a", "b"):
        im = body["images"][s]
        assert im["mime"] == "image/jpeg" and (im["w"], im["h"]) == (1000, 1000)
        assert base64.b64decode(im["b64"]) == ("JPEG-" + s).encode()
    assert {c["id"] for c in body["candidates"]} == {"F%d" % f["id"] for f in pr["findings"]}


def test_no_findings_means_no_request(tmp_path):
    pr = _pr(TA, TA, str(tmp_path))
    assert pr["findings"] == []
    seen = []
    st, _ = _go(pr, _ans([]), tmp_path, seen)
    assert seen == [] and pr["ai"]["status"] == "skipped" and st["pairs_failed"] == 0


def test_unreadable_image_file_falls_back_to_algorithm(tmp_path):
    pr = _pr(TA, TB)                       # ไม่มีไฟล์ภาพ
    before = json.dumps(pr["findings"], sort_keys=True, default=str)
    seen, warns = [], []
    st, _ = _go(pr, _ans([]), tmp_path, seen, warns)
    assert seen == [] and pr["ai"]["status"] == "failed" and st["pairs_failed"] == 1
    assert json.dumps(pr["findings"], sort_keys=True, default=str) == before
    assert warns and "ภาพ" in warns[0]


# ── ③ รวมผล ──────────────────────────────────────────────────────────

def _ids(pr):
    num = next(f for f in pr["findings"] if "20" in (f["a"]["frag"] + f["a"]["text"])
               and "Sodium" in f["a"]["text"])
    fat = next(f for f in pr["findings"] if "Fat" in f["a"]["text"])
    return num, fat


def test_real_red_noise_folded_uncertain_yellow(tmp_path):
    pr = _pr(TA, TB, str(tmp_path))
    num, fat = _ids(pr)
    ans = _ans([_rev("F%d" % num["id"], "real", "20%", "24%"),
                _rev("F%d" % fat["id"], "noise", "1.5g", "1.5g")])
    st, _ = _go(pr, ans, tmp_path)
    assert st["pairs_ok"] == 1
    assert [f["id"] for f in pr["findings"]] == [num["id"]]
    assert num["severity"] == "red" and num["ai"]["a_seen"] == "20%" and num["ai"]["image"]
    assert [f["id"] for f in pr["ai_dismissed"]] == [fat["id"]]
    assert fat["severity"] == "dismissed" and any("เหมือนกัน" in n for n in fat["notes"])
    assert pr["ai"]["image_verdicts"]["real"] == 1 and pr["ai"]["image_verdicts"]["noise"] == 1
    assert pr["ai"]["ref_accuracy"] == 1.0


def test_uncertain_is_yellow_and_unanswered_keeps_algorithm_level(tmp_path):
    pr = _pr(TA, TB, str(tmp_path))
    num, fat = _ids(pr)
    sev = fat["severity"]
    st, _ = _go(pr, _ans([_rev("F%d" % num["id"], "uncertain")]), tmp_path)
    assert num["severity"] == "yellow"
    assert fat["severity"] == sev and fat in pr["findings"]
    assert any("ไม่ได้ตอบ" in n for n in fat["notes"]) and fat["ai"]["verdict"] is None
    assert pr["ai"]["image_verdicts"]["unanswered"] == 1
    assert pr["ai"]["reviewed"] == 1 and pr["ai"]["reviewable"] == 2


def test_yellow_can_be_raised_to_red_by_real(tmp_path):
    """ผู้ใช้เลือก "ตัดสินเต็มที่" — AI ยืนยันจากภาพว่าต่างจริง = แดง แม้อัลกอริทึมให้เหลือง"""
    pr = _pr(TA, TB, str(tmp_path))
    _, fat = _ids(pr)
    fat["severity"] = "yellow"
    _go(pr, _ans([_rev("F%d" % fat["id"], "real", "1.5g", "15g")]), tmp_path)
    assert fat["severity"] == "red"


def test_invalid_reviews_and_items_are_not_used(tmp_path):
    pr = _pr(TA, TB, str(tmp_path))
    num, fat = _ids(pr)
    ans = _ans([_rev("F999", "noise"), _rev("F%d" % num["id"], "maybe"),
                _rev("F%d" % fat["id"], "real"), _rev("F%d" % fat["id"], "noise"), "x"],
               items=[{"a_words": ["A0:3"], "a_quote": "20%", "b_words": [], "b_quote": "",
                       "verdict": "real"}])
    n0 = len(pr["findings"])
    _go(pr, ans, tmp_path)
    ai = pr["ai"]
    assert fat["severity"] == "red"                       # คำตอบแรกของจุดนั้นเท่านั้น
    assert len(pr["findings"]) == n0                      # ไม่เพิ่มจุดจาก items
    assert ai["items_ignored"] == 1 and ai["reviews_valid"] == 1 and len(ai["invalid"]) == 4
    assert ai["ref_accuracy"] == pytest.approx(0.2)


def test_safety_flag_keeps_clear_vision_difference(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AI_IMAGE_SAFETY", True)
    pr = _pr(TA, TB, str(tmp_path))
    num, _ = _ids(pr)
    _go(pr, _ans([_rev("F%d" % num["id"], "noise", "20%", "20%")]), tmp_path)
    assert num in pr["findings"] and num["severity"] == "yellow"
    assert pr["ai"]["image_verdicts"]["guarded"] == 1 and not pr.get("ai_dismissed")


def test_safety_flag_off_folds_it(tmp_path):
    pr = _pr(TA, TB, str(tmp_path))
    num, _ = _ids(pr)
    _go(pr, _ans([_rev("F%d" % num["id"], "noise", "20%", "20%")]), tmp_path)
    assert num not in pr["findings"] and num in pr["ai_dismissed"]


def test_n8n_down_keeps_algorithm_result(tmp_path):
    pr = _pr(TA, TB, str(tmp_path))
    before = json.dumps(pr["findings"], sort_keys=True, default=str)
    warns = []
    st, _ = _go(pr, {"x": 1}, tmp_path, warns=warns, status=500)
    assert pr["ai"]["status"] == "failed" and st["pairs_failed"] == 1
    assert json.dumps(pr["findings"], sort_keys=True, default=str) == before
    assert any("AI ตรวจทานไม่สำเร็จ" in w for w in warns)


def test_url_not_set_names_the_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AI_IMAGE_URL", "")
    pr = _pr(TA, TB, str(tmp_path))
    warns = []
    _go(pr, _ans([]), tmp_path, warns=warns)
    assert "ARTWORK_V2_AI_IMAGE_URL" in pr["ai"]["error"]


# ── ④ ทั้งเส้น (pipeline จริง · Vision ปลอม) ─────────────────────────

fitz = pytest.importorskip("fitz")
from PIL import Image  # noqa: E402

FULL = [{"a": {"page": 0, "bbox": [0, 0, 1, 1]}, "b": {"page": 0, "bbox": [0, 0, 1, 1]}}]
PA = ["Sodium 475 mg 20%", "Fat 1.5g", "Net weight 85 g"]
PB = ["Sodium 475 mg 24%", "Fat 1.5g", "Net weight 85 g"]


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
            T = PA if it["id"].endswith("a") else PB
            lines = [(t, int(0.05 * W), int(H * (0.1 + 0.8 * i / len(T))),
                      {"cw": max(4, W // 40), "h": max(8, H // 15)}) for i, t in enumerate(T)]
            res[it["id"]] = {"ok": True, "error": "", "fta": fta(lines, W, H), "request_index": 0}
    return {"results": res, "calls": [{"index": 0, "phase": "main", "images": ids,
                                       "json_bytes": 10, "status": 200, "attempts": 1, "ms": 1,
                                       "error": "", "model_requested": config.MODEL,
                                       "endpoint": config.ENDPOINT, "at": "t"}]}


def _run(monkeypatch, answer, seen=None):
    monkeypatch.setattr(vision_client, "annotate", _fake_annotate)
    keystore.save(KEY)
    job = jobs.create(("a.pdf", _pdf(PA)), ("b.pdf", _pdf(PB)))["id"]
    return job, pipeline.run(job, FULL, ai_mode="image", ai_poster=_poster(answer, seen))


def test_end_to_end_images_are_the_vision_images(monkeypatch):
    seen = []
    job, r = _run(monkeypatch, lambda b: _ans([_rev(c["id"], "real", "20%", "24%")
                                               for c in b["candidates"]]), seen)
    assert len(seen) == 1
    rd = jobs.run_dir(job, r["run"])
    for s in ("a", "b"):
        with open(os.path.join(rd, "img", "p1_%s.jpg" % s), "rb") as fh:
            assert base64.b64decode(seen[0]["images"][s]["b64"]) == fh.read()
    pr = r["pairs"][0]
    assert r["verdict"] == "FAIL" and pr["findings"][0]["severity"] == "red"
    assert pr["ai"]["mode"] == "image" and pr["ai"]["status"] == "ok"
    assert [c["phase"] for c in r["calls"]] == ["main"]          # ไม่ยิง Vision ซ้ำ
    log = r["log_text"]
    assert "[AI REVIEW] mode=image" in log and "image: verdicts=" in log and "ai_seen: A=" in log
    assert "AI_IMAGE_URL" in log and KEY not in log
    assert '"b64"' not in json.dumps(r, default=str)   # ภาพไม่ถูกเก็บลงผล (เช็คคีย์ — sha1 สุ่มอาจมี b64 ในตัว)


def test_end_to_end_noise_passes_with_folded_list(monkeypatch):
    job, r = _run(monkeypatch, lambda b: _ans([_rev(c["id"], "noise", "20%", "20%")
                                               for c in b["candidates"]]))
    pr = r["pairs"][0]
    assert pr["findings"] == [] and len(pr["ai_dismissed"]) == 1
    assert r["verdict"] == "PASS"


# ── ⑤ หน้าเว็บ ───────────────────────────────────────────────────────

def test_page_offers_image_mode_always():
    html = open(os.path.join(ROOT, "templates", "artwork_v2.html"), encoding="utf-8").read()
    i = html.index('value="image"')
    endif = html.rfind("{% endif %}", 0, i)
    assert endif > html.rfind("{% if v2_ai_experimental", 0, i)     # ไม่อยู่ในบล็อกที่ซ่อน
    assert "AI ดูภาพตัดสิน (ทดลอง)" in html
    js = open(os.path.join(ROOT, "static", "js", "artwork_v2.js"), encoding="utf-8").read()
    assert 'image: "AI ดูภาพตัดสิน"' in js and "ai.a_seen" in js and "image_verdicts" in js


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


def _code(path=WF, name="Build Gemini request"):
    return next(n for n in _wfjson(path)["nodes"] if n["name"] == name)["parameters"]["jsCode"]


def _prompt(path=WF):
    return re.search(r"const PROMPT = `(.*?)`;", _code(path), re.S).group(1)


IMGS = {"a": {"mime": "image/jpeg", "w": 10, "h": 20, "b64": "QUJD"},
        "b": {"mime": "image/jpeg", "w": 9, "h": 18, "b64": "REVG"}}
BODY = {"mode": "image",
        "zone_a": [{"id": "A0", "words": ["Fat", "20%"], "word_conf": [1, 1]}],
        "zone_b": [{"id": "B0", "words": ["Fat", "24%"], "word_conf": [1, 1]}],
        "candidates": [{"id": "F1", "a": {"box": [1, 2, 3, 4]}, "b": {"box": [1, 2, 3, 4]}}],
        "images": IMGS}


def test_workflow_builds_request_with_both_images():
    b = _build(BODY)
    assert b["valid"] and b["mode"] == "image"
    req = b["gemini_request"]
    assert req["systemInstruction"]["parts"][0]["text"] == _prompt()
    parts = req["contents"][0]["parts"]
    assert parts[0]["text"].startswith("IMAGE A") and parts[1]["inlineData"] == {
        "mimeType": "image/jpeg", "data": "QUJD"}
    assert parts[2]["text"].startswith("IMAGE B") and parts[3]["inlineData"]["data"] == "REVG"
    data = json.loads(parts[4]["text"].split("\n", 1)[1])
    assert data["candidates"] == BODY["candidates"] and data["image_b"] == {"w": 9, "h": 18}
    assert data["zone_a"][0]["w"] == [["A0:0", "Fat", 1], ["A0:1", "20%", 1]]
    assert "b64" not in parts[4]["text"]
    gc = req["generationConfig"]
    assert gc["temperature"] == 0 and gc["thinkingConfig"]["thinkingBudget"] == 16384
    s = json.dumps(gc["responseSchema"])
    assert "a_seen" in s and "b_seen" in s and "confidence" not in s


def test_workflow_rejects_missing_images_or_candidates():
    for bad in (dict(BODY, images={}), dict(BODY, images={"a": IMGS["a"]}),
                dict(BODY, images={"a": dict(IMGS["a"], mime="image/gif"), "b": IMGS["b"]}),
                dict(BODY, candidates=[]), {"images": IMGS}):
        b = _build(bad)
        assert b["valid"] is False and b["error"] and b["gemini_request"] is None


def test_prompt_rules():
    p = _prompt()
    for must in ("LOOK AT THE IMAGES", "Read each image on its own", "Never assume B equals A",
                 "Never guess a character", "exactly ONE entry for EVERY candidate",
                 "items: always []", "Never output any number for confidence", "Thai",
                 "use each side's own box on its own image"):
        assert must in p, must


def test_doc_prompt_matches_workflow():
    doc = open(DOC, encoding="utf-8").read()
    d = re.search(r"<!-- PROMPT_IMAGE START -->\n```text\n(.*?)\n```\n<!-- PROMPT_IMAGE END -->",
                  doc, re.S).group(1)
    assert d == _prompt()
    assert "artwork-v2-image" in doc and "ARTWORK_V2_AI_IMAGE_URL" in doc


def test_workflow_is_a_separate_importable_flow():
    w = _wfjson()
    others = [_wfjson(WF_REVIEW), _wfjson(WF_RAW)]
    hook = next(n for n in w["nodes"] if n["type"] == "n8n-nodes-base.webhook")
    assert hook["parameters"]["path"] == "artwork-v2-image"
    ids = [n["id"] for n in w["nodes"]]
    assert len(set(ids)) == len(ids)
    for o in others:
        assert w["name"] != o["name"] and not set(ids) & {n["id"] for n in o["nodes"]}
        oh = next(n for n in o["nodes"] if n["type"] == "n8n-nodes-base.webhook")
        assert hook["webhookId"] != oh["webhookId"]
    http = next(n for n in w["nodes"] if n["type"] == "n8n-nodes-base.httpRequest")
    assert http.get("onError") == "continueRegularOutput"
    assert http["parameters"]["options"]["response"]["response"]["neverError"] is True
    assert http["parameters"]["options"]["timeout"] < config.AI_IMAGE_TIMEOUT_S * 1000
    assert http["parameters"]["nodeCredentialType"] == "googleApi"
    names = {n["name"] for n in w["nodes"]}
    nxt = {s: [c["node"] for br in o["main"] for c in br] for s, o in w["connections"].items()}
    assert set(nxt) <= names and all(t in names for v in nxt.values() for t in v)

    def ends(n, seen=()):
        if n in seen:
            return False
        if not nxt.get(n):
            return n.startswith("Respond to Webhook")
        return all(ends(t, seen + (n,)) for t in nxt[n])
    assert ends("Webhook")
    # node Parse = ตัวเดียวกับ workflow review (ต่างแค่บรรทัดหัว)
    parse = lambda p: _code(p, "Parse Gemini response").split("\n", 1)[1]
    assert parse(WF) == parse(WF_REVIEW)


def test_other_workflows_do_not_know_image_mode():
    for p in (WF_REVIEW, WF_RAW):
        assert "artwork-v2-image" not in json.dumps(_wfjson(p), ensure_ascii=False)
        assert "inlineData" not in _code(p)
