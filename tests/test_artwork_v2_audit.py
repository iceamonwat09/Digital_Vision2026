"""Artwork V2 — audit ก่อนใช้งานจริง (4 ต.ค.)

บั๊กที่เจอจากการไล่โค้ด/ยิงเชิงโจมตี (ทุกตัวมีเทสต์ที่แดงเมื่อย้อนโค้ด):

1. ``pipeline.run`` ขั้น 7 ใช้ชื่อตัวแปรวน ``key`` ซึ่ง **ทับกุญแจ API** ⇒
   ``redact()`` ลบคำว่า ``ai_dismissed`` ทิ้ง (รายการที่ AI พับในโหมด judge หายจากหน้าเว็บ)
   แทนที่จะลบกุญแจจริง · Log/ผลขึ้น ``length=12`` (= ``len("ai_dismissed")``)
2. ``ai_review.run_all`` นับ ``pairs_ok`` ก่อนรวมผล ⇒ รวมผลล้ม = นับว่าสำเร็จ
   และรายการที่ ``merge`` แก้ไปครึ่งทางค้างอยู่ (ไม่ใช่ "ผลของอัลกอริทึม" ตามที่คำเตือนบอก)
3. ตรวจล้มก่อนยิง Vision ⇒ โฟลเดอร์รอบว่างค้าง ⇒ หน้าเว็บเปิดรอบล่าสุด (ไม่มีผล) แล้วเงียบ
4. ``preview_path``/``_write_json`` ใช้ไฟล์ชั่วคราวชื่อตายตัว ⇒ สองคำขอพร้อมกันเขียนทับกัน
5. ``jobs.recent`` ล้มทั้งรายการเมื่อ ``meta.json`` ผิดรูปแค่งานเดียว
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from artwork_v2_fake import fta  # noqa: E402

from artwork_v2 import (ai_review, compare, config, jobs, keystore, pipeline,  # noqa: E402
                        runguard, vision_client)

fitz = pytest.importorskip("fitz")
from PIL import Image  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = "AIza" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i"
TA = ["Sodium 475 mg 20%", "Fat 1.5g", "Net weight 85 g"]
TB = ["Sodium 475 mg 24%", "Fat 1.5g", "Net weight 85 g"]
PAIR = {"a": {"page": 0, "bbox": [0, 0, 1, 1]}, "b": {"page": 0, "bbox": [0, 0, 1, 1]}}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    d = tmp_path / "v2"
    monkeypatch.setattr(config, "DATA_DIR", str(d))
    monkeypatch.setattr(config, "JOBS_DIR", str(d / "jobs"))
    monkeypatch.setattr(config, "SECRET_DIR", str(d / "secret"))
    monkeypatch.setattr(config, "KEY_FILE", str(d / "secret" / "key.json"))
    monkeypatch.delenv(config.KEY_ENV, raising=False)
    monkeypatch.setattr(config, "RETRY_WAIT_S", 0.0)
    monkeypatch.setattr(config, "REREAD_ENABLED", False)      # ห้ามส่งซ้ำ
    os.makedirs(config.JOBS_DIR)
    yield


def _pdf(lines):
    d = fitz.open()
    p = d.new_page(width=400, height=300)
    for i, t in enumerate(lines):
        p.insert_text((20, 40 + i * 30), t, fontsize=12)
    return d.tobytes()


def _job():
    return jobs.create(("a.pdf", _pdf(TA)), ("b.pdf", _pdf(TB)))["id"]


def _fake_annotate(error_for=None):
    def fake(groups, poster=None, key=None):
        res, ids = {}, []
        for g in groups:
            for it in g:
                ids.append(it["id"])
                W, H = Image.open(io.BytesIO(it["jpeg"])).size
                T = TA if it["id"].endswith("a") else TB
                lines = [(t, int(0.05 * W), int(H * (0.1 + 0.8 * i / len(T))),
                          {"cw": max(4, W // 40), "h": max(8, H // 15)})
                         for i, t in enumerate(T)]
                res[it["id"]] = {"ok": True, "error": "", "fta": fta(lines, W, H),
                                 "request_index": 0}
        return {"results": res, "calls": [{"index": 0, "phase": "main", "images": ids,
                                           "json_bytes": 10, "status": 200, "attempts": 1,
                                           "ms": 1, "error": error_for or "",
                                           "model_requested": config.MODEL,
                                           "endpoint": config.ENDPOINT, "at": "t"}]}
    return fake


class _Resp:
    def __init__(self, data):
        self.status_code = 200
        self._d = data
        self.text = json.dumps(data)

    def json(self):
        return self._d


def _ai_poster(answer):
    def post(url, data=None, headers=None, timeout=None):
        return _Resp(answer(json.loads(data.decode("utf-8"))))
    return post


def _noise_answer(payload):
    """judge: AI บอกว่า 20%/24% เป็นสัญญาณรบกวน ⇒ จุดของอัลกอริทึมไปอยู่ ai_dismissed"""
    a = next(l for l in payload["zone_a"] if "20%" in l["words"])
    b = next(l for l in payload["zone_b"] if "24%" in l["words"])
    return {"reviews": [], "summary": "สัญญาณรบกวน", "suggestions": [],
            "items": [{"a_words": ["%s:%d" % (a["id"], a["words"].index("20%"))], "a_quote": "20%",
                       "b_words": ["%s:%d" % (b["id"], b["words"].index("24%"))], "b_quote": "24%",
                       "kind": "text", "verdict": "noise", "reason": "OCR อ่านเพี้ยน",
                       "suggestion": ""}]}


def _run(monkeypatch, mode="off", answer=None, error_for=None):
    monkeypatch.setattr(vision_client, "annotate", _fake_annotate(error_for))
    keystore.save(KEY)
    return pipeline.run(_job(), [PAIR], ai_mode=mode,
                        ai_poster=_ai_poster(answer) if answer else None)


# ── ① กุญแจ API ต้องไม่ถูกทับ ───────────────────────────────────────

def test_key_info_describes_the_real_key(monkeypatch):
    r = _run(monkeypatch)
    assert r["key"]["length"] == len(KEY)
    assert r["key"]["masked"].endswith(KEY[-4:])
    assert "length=%d" % len(KEY) in r["log_text"]


def test_judge_dismissed_list_survives_to_the_result(monkeypatch):
    """เดิม redact() ลบชื่อคีย์ ``ai_dismissed`` ⇒ รายการพับของ judge หายจากหน้าเว็บเงียบ ๆ"""
    # 6 ต.ค.: ด่านกันพับ (ตัวเลขที่ Vision อ่านชัด) จะคง 20%/24% ไว้เป็นเหลือง — เทสต์นี้ทดสอบ
    # ว่ารายการพับรอดถึงผล จึงปิดด่านนั้น (ด่านเองทดสอบใน test_artwork_v2_ai_rules.py)
    monkeypatch.setattr(config, "AI_JUDGE_NOISE_GUARD", False)
    r = _run(monkeypatch, "judge", _noise_answer)
    pr = r["pairs"][0]
    assert "***" not in pr
    assert len(pr["ai_dismissed"]) == 1
    assert pr["ai_dismissed"][0]["severity"] == "dismissed"
    assert pr["ai_dismissed"][0]["confidence"] is not None
    disk = jobs.read_json(os.path.join(jobs.job_dir(r["job"]), r["run"], "result.json"))
    assert len(disk["pairs"][0]["ai_dismissed"]) == 1


def test_key_leaked_into_a_message_is_still_redacted(monkeypatch):
    """ชั้นสุดท้ายกันกุญแจรั่ว — ต้อง redact กุญแจจริง ไม่ใช่คำอื่น"""
    r = _run(monkeypatch, error_for="boom key=%s" % KEY)
    blob = json.dumps(r, ensure_ascii=False)
    assert KEY not in blob and KEY not in r["log_text"]
    rd = os.path.join(jobs.job_dir(r["job"]), r["run"])
    for name in ("result.json", "log.txt"):
        with open(os.path.join(rd, name), encoding="utf-8") as f:
            assert KEY not in f.read()


# ── ② AI รวมผลล้ม ⇒ ผลอัลกอริทึมเดิมทุกตัวอักษร + นับว่าล้ม ─────────────

def test_merge_failure_restores_algorithm_result_and_counts_as_failed(monkeypatch):
    off = _run(monkeypatch, "off")
    real_merge = ai_review.merge

    def broken(mode, pr, resp, A, B):
        real_merge(mode, pr, resp, A, B)          # แก้รายการไปแล้ว…
        raise RuntimeError("ล้มหลังแก้รายการ")      # …แล้วค่อยล้ม

    monkeypatch.setattr(ai_review, "merge", broken)
    bad = _run(monkeypatch, "judge", _noise_answer)
    assert bad["ai"]["pairs_ok"] == 0 and bad["ai"]["pairs_failed"] == 1
    assert bad["pairs"][0]["ai"]["status"] == "failed"
    assert bad["verdict"] == off["verdict"]
    strip = lambda fs: [(f["class"], f["severity"], f["a"]["frag"], f["b"]["frag"])  # noqa: E731
                        for f in fs]
    assert strip(bad["pairs"][0]["findings"]) == strip(off["pairs"][0]["findings"])
    assert not bad["pairs"][0].get("ai_dismissed") and not bad["pairs"][0].get("algo_only")
    assert all("ai" not in f for f in bad["pairs"][0]["findings"])


# ── ③ รอบที่ล้มต้องไม่ค้างเป็นรอบว่าง ─────────────────────────────────

def test_failure_before_vision_leaves_no_run_dir(monkeypatch):
    called = []
    monkeypatch.setattr(vision_client, "annotate",
                        lambda *a, **k: called.append(1) or _fake_annotate()(*a, **k))
    keystore.save(KEY)
    jid = _job()
    bad = {"a": {"page": 5, "bbox": [0, 0, 1, 1]}, "b": {"page": 0, "bbox": [0, 0, 1, 1]}}
    with pytest.raises(ValueError):
        pipeline.run(jid, [bad])
    assert called == []
    assert [x for x in os.listdir(jobs.job_dir(jid)) if x.startswith("run_")] == []
    r = pipeline.run(jid, [PAIR])                       # รอบถัดไปยังได้เลขต่อเนื่อง
    assert r["run"] == "run_001"


def test_failure_after_vision_keeps_raw_but_is_not_listed(monkeypatch):
    monkeypatch.setattr(vision_client, "annotate", _fake_annotate())
    keystore.save(KEY)
    jid = _job()
    ok = pipeline.run(jid, [PAIR])
    monkeypatch.setattr(compare, "compare", lambda *a, **k: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        pipeline.run(jid, [PAIR])
    d = jobs.job_dir(jid)
    assert os.listdir(os.path.join(d, "run_002", "raw"))      # ผลดิบเก็บไว้ไล่ปัญหา
    assert jobs.finished_runs(d) == [ok["run"]]
    assert jobs.recent(5)[0]["last"]["run"] == ok["run"]
    c = _client(monkeypatch)
    assert c.get("/api/artwork_v2/jobs/%s" % jid).get_json()["runs"] == ["run_001"]


# ── ④ ไฟล์ชั่วคราวไม่ชนกัน ───────────────────────────────────────────

def test_tmp_names_are_unique():
    assert len({jobs._tmp("/x/p.png") for _ in range(50)}) == 50


def test_concurrent_preview_requests_produce_one_valid_png():
    jid = _job()
    errs, paths = [], []

    def go():
        try:
            paths.append(jobs.preview_path(jid, "a", 0))
        except Exception as e:                           # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=go) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errs == [] and len(set(paths)) == 1
    Image.open(paths[0]).verify()
    assert not [x for x in os.listdir(jobs.job_dir(jid)) if x.endswith(".tmp")]


# ── ⑤ รายการงานทนไฟล์ meta ผิดรูป ────────────────────────────────────

@pytest.mark.parametrize("bad", [{"id": "x"}, {"files": {"a": {}}}, [1, 2], "str"])
def test_recent_skips_malformed_meta(bad):
    good = _job()
    d = os.path.join(config.JOBS_DIR, "29991231_235959_abcdef")
    os.makedirs(d)
    with open(os.path.join(d, "meta.json"), "w") as f:
        json.dump(bad, f)
    ids = [j["id"] for j in jobs.recent(10, can_view=lambda m: True)]
    assert ids == [good]


# ── ⑥ HTTP เชิงโจมตี ─────────────────────────────────────────────────

def _client(monkeypatch):
    from flask import Flask, g
    from artwork_v2 import routes
    from artwork_v2.routes import artwork_v2_bp
    monkeypatch.setattr(routes, "_RUNS", runguard.RunGuard())
    monkeypatch.setattr(routes, "_KEY_TESTS", runguard.RunGuard())
    app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"),
                static_folder=os.path.join(ROOT, "static"))

    @app.before_request
    def _fake_auth():
        g.auth_enabled = False
        g.current_user = None

    @app.context_processor
    def _ctx():
        return {"config_version": "t", "current_user": None, "auth_enabled": False,
                "has_perm": lambda *a, **k: True}

    app.register_blueprint(artwork_v2_bp)
    return app.test_client()


@pytest.mark.parametrize("url", [
    "/api/artwork_v2/jobs/..%2F..%2Fetc",
    "/api/artwork_v2/jobs/20260101_000000_zzzzzz",
    "/api/artwork_v2/jobs/{jid}/runs/..%2F..",
    "/api/artwork_v2/jobs/{jid}/runs/run_999",
    "/api/artwork_v2/jobs/{jid}/runs/run_001/img/..%2Fresult.json",
    "/api/artwork_v2/jobs/{jid}/runs/run_001/raw/..%2F..%2Fmeta.json",
    "/api/artwork_v2/jobs/{jid}/runs/run_001/log.txt",
    "/api/artwork_v2/jobs/{jid}/preview/c/0.png",
    "/api/artwork_v2/jobs/{jid}/preview/a/9.png",
])
def test_bad_paths_never_leak_or_500(monkeypatch, url):
    c = _client(monkeypatch)
    jid = _job()
    r = c.get(url.format(jid=jid))
    assert r.status_code in (400, 404), (url, r.status_code)


@pytest.mark.parametrize("body", [None, "not json", {"pairs": "x"}, {"pairs": []},
                                  {"pairs": [{"a": {"page": 0, "bbox": [0, 0, 2, 1]}}]},
                                  {"pairs": [{"a": {"page": "x", "bbox": [0, 0, 1, 1]},
                                              "b": {"page": 0, "bbox": [0, 0, 1, 1]}}]},
                                  {"pairs": [{"a": {"page": 7, "bbox": [0, 0, 1, 1]},
                                              "b": {"page": 0, "bbox": [0, 0, 1, 1]}}]}])
def test_bad_run_bodies_are_400_without_vision_or_cooldown(monkeypatch, body):
    called = []
    monkeypatch.setattr(vision_client, "annotate", lambda *a, **k: called.append(1))
    keystore.save(KEY)
    c = _client(monkeypatch)
    jid = _job()
    url = "/api/artwork_v2/jobs/%s/run" % jid
    if isinstance(body, str):
        r = c.post(url, data=body, content_type="application/json")
    else:
        r = c.post(url, json=body)
    assert r.status_code == 400 and r.get_json()["error"]
    assert called == []
    assert [x for x in os.listdir(jobs.job_dir(jid)) if x.startswith("run_")] == []
    # ข้อมูลผิดไม่นับเวลาพัก ⇒ แก้แล้วกดใหม่ได้ทันที
    monkeypatch.setattr(vision_client, "annotate", _fake_annotate())
    assert c.post(url, json={"pairs": [PAIR]}).status_code == 200


def test_upload_read_is_capped(monkeypatch):
    """ไฟล์เกินเพดานถูกปฏิเสธโดยไม่อ่านทั้งก้อนเข้าหน่วยความจำ"""
    monkeypatch.setattr(jobs, "MAX_UPLOAD_BYTES", 1000)
    seen = []
    real = jobs.create

    def spy(fa, fb, owner=None):
        seen.extend([len(fa[1]), len(fb[1])])
        return real(fa, fb, owner)

    monkeypatch.setattr(jobs, "create", spy)
    c = _client(monkeypatch)
    r = c.post("/api/artwork_v2/jobs", content_type="multipart/form-data", data={
        "file_a": (io.BytesIO(b"%PDF" + b"x" * 50000), "a.pdf"),
        "file_b": (io.BytesIO(_pdf(TB)), "b.pdf")})
    assert r.status_code == 400 and "ใหญ่เกิน" in r.get_json()["error"]
    assert seen[0] == 1001
    assert os.listdir(config.JOBS_DIR) == []
