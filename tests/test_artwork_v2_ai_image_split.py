"""Artwork V2 · โหมด image — แบ่งคำขอละจุด (``AI_IMAGE_SPLIT``) · สถานี 11 ต.ค. 2026

John West 5 จุดในคำขอเดียว: รอบหนึ่ง Gemini วนพิมพ์ซ้ำ 61,631 token ล้มทั้งคู่ · อีกรอบ N8N ตอบ 200 โดยไม่มีผลตรวจ
เลย 5/5 จุด ⇒ แยกคำขอละจุด (มีเฉพาะครอปของจุดนั้น) ยิงขนานกัน แล้วรวมคำตอบกลับเป็นรูปเดิม ·
คำขอที่ล้มกระทบเฉพาะจุดของตัวเอง · บันทึกต่อคำขอลง Log (http/ms/token/คีย์ที่ได้)
"""
import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_artwork_v2_ai_image_crops as C  # noqa: E402
from artwork_v2 import ai_review, config, diaglog  # noqa: E402

TA = ["Sodium 475 mg 20%", "Fat 1.5g per serving", "Net weight 85 g", "Protein 7 g"]
TB = ["Sodium 475 mg 24%", "Fat 15g per serving", "Net weight 86 g", "Protein 7 g"]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    d = tmp_path / "v2"
    monkeypatch.setattr(config, "JOBS_DIR", str(d / "jobs"))
    monkeypatch.setattr(config, "RETRY_WAIT_S", 0.0)
    monkeypatch.setattr(config, "AI_RETRIES", 0)
    monkeypatch.setattr(config, "AI_IMAGE_SAFETY", False)
    monkeypatch.setattr(config, "AI_IMAGE_CROPS", True)
    monkeypatch.setattr(config, "AI_IMAGE_BLIND", True)
    monkeypatch.setattr(config, "AI_IMAGE_CROP_HIRES", False)
    monkeypatch.setattr(config, "AI_IMAGE_MAX_CANDIDATES", 40)
    monkeypatch.setattr(config, "AI_IMAGE_URL", "http://127.0.0.1:9/webhook/artwork-v2-image")
    monkeypatch.setattr(config, "AI_IMAGE_SPLIT", 1)
    monkeypatch.setattr(config, "AI_IMAGE_PARALLEL", 3)
    yield


def _pr(tmp_path):
    pr, _ = C._pr(TA, TB, str(tmp_path))
    assert len(pr["findings"]) >= 3
    return pr


def _ids(pr):
    return ["F%d" % f["id"] for f in pr["findings"]]


def _real(body):
    # blind: a_seen ≠ b_seen ⇒ แอปตัดสินว่าต่าง
    return {"reviews": [C._rev(c["id"], "real", "A-" + c["id"], "B-" + c["id"]) for c in body["candidates"]],
            "items": [], "summary": "s-" + body["candidates"][0]["id"], "suggestions": ["ดูให้ดี"],
            "engine": "gemini-2.5-flash",
            "usage": {"promptTokenCount": 600, "candidatesTokenCount": 90, "thoughtsTokenCount": 400}}


def test_defaults():
    import importlib
    src = open(config.__file__, encoding="utf-8").read()
    assert '_i("ARTWORK_V2_AI_IMAGE_SPLIT", "1")' in src and '_i("ARTWORK_V2_AI_IMAGE_PARALLEL", "3")' in src
    assert importlib.import_module("artwork_v2.pipeline")  # ค่าอยู่ใน [SETTINGS]
    assert '"AI_IMAGE_SPLIT", "AI_IMAGE_PARALLEL"' in open(
        os.path.join(ROOT, "artwork_v2", "pipeline.py"), encoding="utf-8").read()


def test_one_request_per_spot_with_only_its_own_crops(tmp_path):
    pr = _pr(tmp_path)
    ids = _ids(pr)
    seen = []
    st, _ = C._go(pr, _real, tmp_path, seen)
    assert len(seen) == len(ids)
    assert sorted(b["candidates"][0]["id"] for b in seen) == sorted(ids)
    for b in seen:
        assert len(b["candidates"]) == 1 and b["contract"] == "artwork-v2-image/3"
        cid = b["candidates"][0]["id"]
        assert [(c["candidate"], c["side"]) for c in b["crops"]] == [(cid, "a"), (cid, "b")]
        assert b["parts"] == len(ids) and 1 <= b["part"] <= len(ids)
    ai = pr["ai"]
    assert st["pairs_ok"] == 1 and ai["status"] == "ok" and ai["engine"] == "gemini-2.5-flash"
    assert ai["image_verdicts"]["real"] == len(ids) and ai["image_verdicts"]["unanswered"] == 0
    assert all(f["severity"] == "red" for f in pr["findings"])
    assert ai["usage"]["candidatesTokenCount"] == 90 * len(ids)          # รวมทุกคำขอ
    assert len(ai["requests"]) == len(ids) and ai["requests_failed"] == 0
    assert ai["suggestions"] == ["ดูให้ดี"]                                # ไม่ซ้ำ


def test_one_failed_request_only_affects_its_own_spot(tmp_path):
    pr = _pr(tmp_path)
    bad = _ids(pr)[1]
    sev = {"F%d" % f["id"]: f["severity"] for f in pr["findings"]}

    def ans(body):
        if body["candidates"][0]["id"] == bad:
            return {"error": "Gemini ตอบไม่จบ (finishReason=MAX_TOKENS)", "engine": "g",
                    "usage": {"candidatesTokenCount": 2560}}
        return _real(body)
    warns = []
    st, _ = C._go(pr, ans, tmp_path, warns=warns)
    ai = pr["ai"]
    assert ai["status"] == "ok" and ai["requests_failed"] == 1
    by = {"F%d" % f["id"]: f for f in pr["findings"]}
    assert by[bad]["severity"] == sev[bad]                              # คงระดับของอัลกอริทึม
    assert "คำขอของจุดนี้ล้ม" in by[bad]["notes"][-1] and "MAX_TOKENS" in by[bad]["notes"][-1]
    assert all(by[i]["severity"] == "red" for i in by if i != bad)
    assert any(bad in w and "1 จาก" in w for w in warns)
    rq = [r for r in ai["requests"] if r["ids"] == [bad]][0]
    assert rq["status"] == "failed" and rq["usage"].startswith("?/2560")


def test_all_requests_failed_is_a_failed_pair(tmp_path):
    pr = _pr(tmp_path)
    before = json.dumps([(f["id"], f["severity"]) for f in pr["findings"]])
    st, _ = C._go(pr, {"error": "boom", "engine": "g"}, tmp_path)
    assert st["pairs_failed"] == 1 and pr["ai"]["status"] == "failed"
    assert "ทุกคำขอล้ม" in pr["ai"]["error"]
    assert json.dumps([(f["id"], f["severity"]) for f in pr["findings"]]) == before


def test_empty_200_says_what_n8n_returned(tmp_path):
    pr = _pr(tmp_path)
    first = _ids(pr)[0]

    def ans(body):
        return {"message": "Workflow was started"} if body["candidates"][0]["id"] == first else _real(body)
    C._go(pr, ans, tmp_path)
    by = {"F%d" % f["id"]: f for f in pr["findings"]}
    note = by[first]["notes"][-1]
    assert "N8N ตอบกลับโดยไม่มีผลตรวจ" in note and "message" in note
    assert by[first]["ai"]["unanswered_why"]
    rq = [r for r in pr["ai"]["requests"] if r["ids"] == [first]][0]
    assert rq["status"] == "ok" and rq["reviews"] == 0 and rq["keys"] == ["message"]


def test_split_zero_is_the_old_single_request(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AI_IMAGE_SPLIT", 0)
    pr = _pr(tmp_path)
    seen = []
    C._go(pr, _real, tmp_path, seen)
    assert len(seen) == 1 and len(seen[0]["candidates"]) == len(pr["findings"])
    assert "part" not in seen[0] and "requests" not in pr["ai"]


def test_split_two_per_request(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AI_IMAGE_SPLIT", 2)
    pr = _pr(tmp_path)
    seen = []
    C._go(pr, _real, tmp_path, seen)
    n = len(pr["findings"])
    assert len(seen) == (n + 1) // 2 and max(len(b["candidates"]) for b in seen) == 2


def test_requests_run_in_parallel(tmp_path):
    pr = _pr(tmp_path)

    def slow(body):
        time.sleep(0.4)
        return _real(body)
    t0 = time.time()
    C._go(pr, slow, tmp_path)
    took = time.time() - t0
    assert took < 0.4 * len(pr["findings"]) - 0.2                       # ไม่ใช่ทีละคำขอ
    assert pr["ai"]["parallel"] == 3


def test_log_lists_each_request():
    src = open(diaglog.__file__, encoding="utf-8").read()
    assert "image_split: requests=" in src and "req %s [%s] status=%s" in src
    assert src.index("image_split: requests=") < src.index('if x.get("status") != "ok":')


def test_workflow_http_node_always_outputs_an_item():
    w = json.load(open(C.WF, encoding="utf-8"))
    http = next(n for n in w["nodes"] if n["name"] == "HTTP Request")
    assert http.get("alwaysOutputData") is True and http.get("onError") == "continueRegularOutput"


def test_parse_turns_an_empty_item_into_an_error():
    import test_artwork_v2_ai_image_budget as B
    out = B._parse({})
    assert out["error"] and out["engine"]
