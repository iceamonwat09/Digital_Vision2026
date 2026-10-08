"""Artwork V2 (8 ต.ค.) — ปุ่ม "✓ ยืนยัน" / "⚑ รายงานปัญหา" ต่อจุดต่าง + สรุปการรีวิว

ผู้ใช้สั่ง: *"แต่ละผลตรวจสอบที่พบ อยากให้มีปุ่ม กดยืนยัน กับ รายงานปัญหา เพื่อให้ระบบบ่งบอกได้ว่า
แต่ละปัญหาตรวจสอบรีวิวหรือยัง รีวิวครบหรือยัง อะไรที่ยังไม่ได้รีวิว อะไรที่พบว่าเป็นข้อผิดพลาดจริง"*

สิ่งที่ล็อก:
* การรีวิวอยู่ใน ``review.json`` แยก — **result.json / log.txt / ผลตัดสินไม่เปลี่ยนแม้แต่ไบต์เดียว**
* รีวิวได้เฉพาะจุดในตารางหลัก (``findings``) · เลขจุดที่ไม่มี / ในรายการพับ ⇒ ปฏิเสธ ไม่เขียนอะไร
* กดซ้ำ = ยกเลิก · เหตุผลเก็บเฉพาะ "รายงานปัญหา" · ผู้รีวิว + เวลา + ประวัติ
* สิทธิ์: เจ้าของงาน/ผู้ดูแลเท่านั้น (ด่านเดียวกับทุก route ที่มี job_id)
* ปิดธง ⇒ ไม่มีปุ่ม/แถบ + API 404 + Log ที่ดาวน์โหลดเหมือนเดิม
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from artwork_v2_fake import fta  # noqa: E402

from artwork_v2 import config, jobs, keystore, pipeline, review, vision_client  # noqa: E402

fitz = pytest.importorskip("fitz")
from PIL import Image  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js", "artwork_v2.js")
TPL = os.path.join(ROOT, "templates", "artwork_v2.html")
KEY = "AIza" + "Q1w2E3r4T5y6U7i8O9p0A1s2D3f4G5h6J7k"
TA = ["Sodium 475 mg 20%", "Net weight 85 g", "Made in Thailand"]
TB = ["Sodium 475 mg 24%", "Net weight 86 g", "Made in Thailand"]
FULL = [{"a": {"page": 0, "bbox": [0, 0, 1, 1]}, "b": {"page": 0, "bbox": [0, 0, 1, 1]}}]
STAFF = {"sub": "7", "username": "staff", "perms": ["inspect_artwork"]}
OTHER = {"sub": "8", "username": "other", "perms": ["inspect_artwork"]}
ADMIN = {"sub": "1", "username": "admin", "perms": ["inspect_artwork", "manage_users"]}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    d = tmp_path / "v2"
    monkeypatch.setattr(config, "DATA_DIR", str(d))
    monkeypatch.setattr(config, "JOBS_DIR", str(d / "jobs"))
    monkeypatch.setattr(config, "SECRET_DIR", str(d / "secret"))
    monkeypatch.setattr(config, "KEY_FILE", str(d / "secret" / "key.json"))
    monkeypatch.delenv(config.KEY_ENV, raising=False)
    monkeypatch.setattr(config, "REREAD_ENABLED", False)
    monkeypatch.setattr(config, "REVIEW", True)
    os.makedirs(config.JOBS_DIR)
    yield


def _pdf(lines):
    d = fitz.open()
    p = d.new_page(width=400, height=300)
    for i, t in enumerate(lines):
        p.insert_text((20, 40 + i * 30), t, fontsize=12)
    return d.tobytes()


def _fake(groups, poster=None, key=None):
    res, ids = {}, []
    for g in groups:
        for it in g:
            ids.append(it["id"])
            W, H = Image.open(io.BytesIO(it["jpeg"])).size
            T = TA if it["id"].endswith("a") else TB
            lines = [(t, int(0.05 * W), int(H * (0.1 + 0.25 * i)), {"cw": max(4, W // 40), "h": max(8, H // 15)})
                     for i, t in enumerate(T)]
            res[it["id"]] = {"ok": True, "error": "", "fta": fta(lines, W, H), "request_index": 0}
    return {"results": res, "calls": [{"index": 0, "phase": "main", "images": ids, "json_bytes": 10,
                                       "status": 200, "attempts": 1, "ms": 1, "error": "",
                                       "model_requested": config.MODEL, "endpoint": config.ENDPOINT, "at": "t"}]}


def _app(user=None, auth=False):
    from flask import Flask, g
    from artwork_v2.routes import artwork_v2_bp
    app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"),
                static_folder=os.path.join(ROOT, "static"))

    @app.before_request
    def _fake_auth():
        g.auth_enabled = auth
        g.current_user = user

    @app.context_processor
    def _ctx():
        return {"config_version": "test", "current_user": user, "auth_enabled": auth,
                "has_perm": lambda *a, **k: True}

    app.register_blueprint(artwork_v2_bp)
    return app.test_client()


def _job(monkeypatch, owner=None):
    monkeypatch.setattr(vision_client, "annotate", _fake)
    keystore.save(KEY)
    jid = jobs.create(("a.pdf", _pdf(TA)), ("b.pdf", _pdf(TB)), owner)["id"]
    res = pipeline.run(jid, FULL)
    return jid, res


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def _ids(res):
    return [f["id"] for p in res["pairs"] for f in p["findings"]]


# ── เซิร์ฟเวอร์ ──────────────────────────────────────────────────────

def test_fixture_has_reviewable_findings(monkeypatch):
    jid, res = _job(monkeypatch)
    assert res["verdict"] == "FAIL" and len(_ids(res)) >= 2


def test_nothing_reviewed_yet(monkeypatch):
    jid, res = _job(monkeypatch)
    d = review.load(jid, res["run"])
    sm = d["summary"]
    assert d["items"] == {} and d["log_text"] == ""
    assert sm["total"] == len(_ids(res)) and sm["reviewed"] == 0 and sm["complete"] is False
    assert sm["todo"] == _ids(res)
    assert set(sm["red_todo"]) == {f["id"] for p in res["pairs"] for f in p["findings"] if f["severity"] == "red"}


def test_review_does_not_touch_result_or_log(monkeypatch):
    jid, res = _job(monkeypatch)
    rd = jobs.run_dir(jid, res["run"])
    before = {n: _sha(os.path.join(rd, n)) for n in ("result.json", "log.txt")}
    c = _app()
    base = "/api/artwork_v2/jobs/%s/runs/%s" % (jid, res["run"])
    ids = _ids(res)
    r = c.post(base + "/review", json={"ids": [ids[0]], "status": "real"})
    assert r.status_code == 200, r.get_data(as_text=True)
    c.post(base + "/review", json={"ids": [ids[1]], "status": "false", "note": "OCR อ่าน 6 เป็น 5"})
    after = {n: _sha(os.path.join(rd, n)) for n in ("result.json", "log.txt")}
    assert before == after, "การรีวิวต้องไม่แก้ผลตรวจ/Log ที่บันทึกไว้"
    assert c.get(base).get_json()["verdict"] == res["verdict"]
    assert os.path.isfile(os.path.join(rd, "review.json"))


def test_full_cycle_counts_and_completion(monkeypatch):
    jid, res = _job(monkeypatch)
    c = _app()
    base = "/api/artwork_v2/jobs/%s/runs/%s/review" % (jid, res["run"])
    ids = _ids(res)
    d = c.post(base, json={"ids": [ids[0]], "status": "real"}).get_json()
    assert d["summary"]["real"] == [ids[0]] and d["summary"]["reviewed"] == 1
    assert d["summary"]["complete"] is False and ids[0] not in d["summary"]["todo"]
    d = c.post(base, json={"ids": ids[1:], "status": "false", "note": "  ระบบ\nแจ้งผิด  "}).get_json()
    assert d["summary"]["complete"] is True and d["summary"]["todo"] == []
    assert d["summary"]["false"] == ids[1:]
    assert d["items"][str(ids[1])]["note"] == "ระบบ แจ้งผิด"          # ยุบช่องว่าง/ขึ้นบรรทัด
    # GET ได้สถานะเดียวกัน (เครื่องอื่นเปิดดูเห็นตรงกัน)
    assert c.get(base).get_json()["summary"] == d["summary"]


def test_pressing_again_with_null_clears(monkeypatch):
    jid, res = _job(monkeypatch)
    c = _app()
    base = "/api/artwork_v2/jobs/%s/runs/%s/review" % (jid, res["run"])
    i = _ids(res)[0]
    c.post(base, json={"ids": [i], "status": "real"})
    d = c.post(base, json={"ids": [i], "status": None}).get_json()
    assert str(i) not in d["items"] and i in d["summary"]["todo"]
    assert [h["to"] for h in d["history"]] == ["real", None]
    assert d["history"][-1]["from"] == "real"


def test_note_only_kept_for_false_and_clipped(monkeypatch):
    jid, res = _job(monkeypatch)
    monkeypatch.setattr(config, "REVIEW_NOTE_MAX", 10)
    ids = _ids(res)
    d = review.set_status(jid, res["run"], [ids[0]], "real", note="ไม่ควรเก็บ")
    assert d["items"][str(ids[0])]["note"] == ""
    d = review.set_status(jid, res["run"], [ids[1]], "false", note="x" * 50)
    assert d["items"][str(ids[1])]["note"] == "x" * 10


def test_reviewer_name_and_time_recorded(monkeypatch):
    jid, res = _job(monkeypatch, owner={"user_id": "7", "username": "staff"})
    c = _app(STAFF, auth=True)
    i = _ids(res)[0]
    d = c.post("/api/artwork_v2/jobs/%s/runs/%s/review" % (jid, res["run"]),
               json={"ids": [i], "status": "real"}).get_json()
    it = d["items"][str(i)]
    assert it["by"] == "staff" and len(it["at"]) == 19


def test_download_log_has_review_section(monkeypatch):
    jid, res = _job(monkeypatch)
    c = _app()
    base = "/api/artwork_v2/jobs/%s/runs/%s" % (jid, res["run"])
    plain = c.get(base + "/log.txt").get_data(as_text=True)
    assert "[REVIEW]" not in plain
    ids = _ids(res)
    c.post(base + "/review", json={"ids": [ids[0]], "status": "false", "note": "เส้นตาราง"})
    r = c.get(base + "/log.txt")
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    assert body.startswith(plain) and "[REVIEW]" in body
    assert "reviewed=1/%d" % len(ids) in body and "#%d" % ids[0] in body and "เส้นตาราง" in body
    assert KEY not in body


@pytest.mark.parametrize("body", [
    {"ids": [9999], "status": "real"},                 # ไม่มีจุดนี้
    {"ids": ["1; rm"], "status": "real"},
    {"ids": [], "status": "real"},
    {"ids": "1", "status": "real"},
    {"ids": [True], "status": "real"},
    {"ids": [1], "status": "approved"},
    {"ids": [1], "status": "real", "note": 5},
    {"ids": [{"x": 1}], "status": "real"},
    [1, 2],
    "x",
])
def test_bad_bodies_are_400_and_write_nothing(monkeypatch, body):
    jid, res = _job(monkeypatch)
    c = _app()
    r = c.post("/api/artwork_v2/jobs/%s/runs/%s/review" % (jid, res["run"]), json=body)
    assert r.status_code == 400
    assert not os.path.exists(os.path.join(jobs.run_dir(jid, res["run"]), "review.json"))


def test_folded_list_ids_cannot_be_reviewed(monkeypatch):
    jid, res = _job(monkeypatch)
    rd = jobs.run_dir(jid, res["run"])
    r = jobs.read_json(os.path.join(rd, "result.json"))
    r["pairs"][0]["debris"] = [{"id": 77, "severity": "debris", "class": "PUNCT",
                                "a": {"text": "|"}, "b": {"text": ""}}]
    jobs.write_json(os.path.join(rd, "result.json"), r)
    with pytest.raises(ValueError):
        review.set_status(jid, res["run"], [77], "real")


@pytest.mark.parametrize("url", [
    "/api/artwork_v2/jobs/{j}/runs/run_999/review",
    "/api/artwork_v2/jobs/{j}/runs/..%2Frun_001/review",
    "/api/artwork_v2/jobs/{j}/runs/result.json/review",
    "/api/artwork_v2/jobs/20990101_000000_abcdef/runs/run_001/review",
    "/api/artwork_v2/jobs/..%2F..%2Fsecret/runs/run_001/review",
])
def test_bad_paths_404(monkeypatch, url):
    jid, res = _job(monkeypatch)
    c = _app()
    u = url.format(j=jid)
    assert c.get(u).status_code == 404
    assert c.post(u, json={"ids": [1], "status": "real"}).status_code == 404


def test_run_without_result_is_404(monkeypatch):
    jid, res = _job(monkeypatch)
    os.makedirs(os.path.join(jobs.job_dir(jid), "run_005", "raw"))
    c = _app()
    assert c.get("/api/artwork_v2/jobs/%s/runs/run_005/review" % jid).status_code == 404


def test_only_owner_or_admin(monkeypatch):
    jid, res = _job(monkeypatch, owner={"user_id": "7", "username": "staff"})
    u = "/api/artwork_v2/jobs/%s/runs/%s/review" % (jid, res["run"])
    i = _ids(res)[0]
    o = _app(OTHER, auth=True)
    assert o.get(u).status_code == 403
    assert o.post(u, json={"ids": [i], "status": "real"}).status_code == 403
    assert not os.path.exists(os.path.join(jobs.run_dir(jid, res["run"]), "review.json"))
    assert _app(STAFF, auth=True).post(u, json={"ids": [i], "status": "real"}).status_code == 200
    d = _app(ADMIN, auth=True).post(u, json={"ids": [i], "status": "false"}).get_json()
    assert d["items"][str(i)]["by"] == "admin" and d["history"][0]["by"] == "staff"


def test_concurrent_reviews_are_all_kept(monkeypatch):
    jid, res = _job(monkeypatch)
    ids = _ids(res)
    run = res["run"]
    errs = []

    def go(i, st):
        try:
            for _ in range(5):
                review.set_status(jid, run, [i], st)
        except Exception as e:                     # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=go, args=(i, "real" if k % 2 else "false")) for k, i in enumerate(ids)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    d = review.load(jid, run)
    assert set(d["items"]) == {str(i) for i in ids}
    assert len(d["history"]) == min(50, 5 * len(ids))
    assert not [n for n in os.listdir(jobs.run_dir(jid, run)) if n.endswith(".tmp")]


def test_broken_review_file_is_ignored_not_500(monkeypatch):
    jid, res = _job(monkeypatch)
    rd = jobs.run_dir(jid, res["run"])
    for junk in ("{not json", "[1,2]", '{"items": [1], "history": "x"}',
                 '{"items": {"1": {"status": "hack"}, "9999": {"status": "real"}}}'):
        with open(os.path.join(rd, "review.json"), "w") as f:
            f.write(junk)
        d = _app().get("/api/artwork_v2/jobs/%s/runs/%s/review" % (jid, res["run"])).get_json()
        assert d["items"] == {} and d["summary"]["reviewed"] == 0
    i = _ids(res)[0]
    d = review.set_status(jid, res["run"], [i], "real")
    assert list(d["items"]) == [str(i)]


# ── ปิดธง = เหมือนเดิม ───────────────────────────────────────────────

def test_flag_off_api_404_and_log_untouched(monkeypatch):
    jid, res = _job(monkeypatch)
    base = "/api/artwork_v2/jobs/%s/runs/%s" % (jid, res["run"])
    i = _ids(res)[0]
    review.set_status(jid, res["run"], [i], "real")          # มีข้อมูลค้างจากตอนเปิด
    monkeypatch.setattr(config, "REVIEW", False)
    c = _app()
    assert c.get(base + "/review").status_code == 404
    assert c.post(base + "/review", json={"ids": [i], "status": "real"}).status_code == 404
    with open(os.path.join(jobs.run_dir(jid, res["run"]), "log.txt"), "rb") as f:
        assert c.get(base + "/log.txt").get_data() == f.read()


def test_page_markup_follows_flag(monkeypatch):
    on = _app().get("/artwork_v2").get_data(as_text=True)
    assert 'data-review="1"' in on and 'id="v2Review"' in on
    monkeypatch.setattr(config, "REVIEW", False)
    off = _app().get("/artwork_v2").get_data(as_text=True)
    assert 'data-review="0"' in off and 'id="v2Review"' not in off


def test_default_on():
    assert config.REVIEW is True or os.environ.get("ARTWORK_V2_REVIEW") == "0"


def test_js_gated_and_buttons_do_not_select_row():
    src = open(JS, encoding="utf-8").read()
    assert 'const REVIEW = root.dataset.review === "1";' in src
    assert "if (!RV_ROW) return \"\";" in src
    # ปุ่มรีวิวต้องถูกดักก่อนการเลือกแถว/ค้างการซูม
    h = src.split('$("v2PairsRes").addEventListener("click", (ev) => {')[1]
    assert h.index(".v2-rvb") < h.index('closest("tr[data-f]")')
    assert "ev.stopPropagation();\n      sendReview(" in h
    # รายการพับไม่มีปุ่ม: folded() เรียก rowsHtml(list) โดยไม่ส่ง main
    assert "rowsHtml(list) + \"</tbody>" in src
    assert "rowsHtml(p.findings, true)" in src


def test_css_present():
    tpl = open(TPL, encoding="utf-8").read()
    for c in (".v2-rv ", ".v2-rvb", ".v2-revbar", "tr.v2-rv-real", "tr.v2-rv-false", "v2-rv-todo-only"):
        assert c in tpl, c


# ── ฟังก์ชัน JS จริง (node) ──────────────────────────────────────────

_HARNESS = r"""
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const body = (name) => src.split('function ' + name + '(')[1].split('\n  }\n')[0];
const mk = (name, deps) => new Function(...Object.keys(deps), 'return function ' + name + '(' + body(name) + '\n}')(...Object.values(deps));
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const job = JSON.parse(require('fs').readFileSync(0, 'utf8'));
console.log(JSON.stringify(job.map((c) => {
  if (c.fn === 'state') return mk('rvState', { S: { review: { items: c.items } } })(c.ids);
  if (c.fn === 'html') return mk('rvHtml', { RV_ROW: c.on, esc })(c.ids);
  throw new Error('fn?');
})));
"""


def _node(cases):
    if not shutil.which("node"):
        pytest.skip("ไม่มี node")
    r = subprocess.run(["node", "-e", _HARNESS, "x", JS], input=json.dumps(cases),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_js_row_state():
    it = {"1": {"status": "real"}, "2": {"status": "false"}, "3": {"status": "real"}}
    got = _node([{"fn": "state", "items": it, "ids": ["1"]},
                 {"fn": "state", "items": it, "ids": ["9"]},
                 {"fn": "state", "items": it, "ids": ["1", "3"]},
                 {"fn": "state", "items": it, "ids": ["1", "2"]},
                 {"fn": "state", "items": it, "ids": ["1", "9"]}])
    assert got == ["real", None, "real", "mixed", "mixed"]


def test_js_buttons_only_when_main_table():
    on, off = _node([{"fn": "html", "on": True, "ids": ["4", "5"]},
                     {"fn": "html", "on": False, "ids": ["4"]}])
    assert off == ""
    assert 'data-rv="4,5"' in on and 'data-st="real"' in on and 'data-st="false"' in on
    assert '✓<span class="v2-rvt"> ยืนยัน</span>' in on and '⚑<span class="v2-rvt"> รายงานปัญหา</span>' in on
    assert "เป็นข้อผิดพลาดจริงของงาน" in on and "ระบบแจ้งผิด" in on            # ความหมายของปุ่มอยู่ใน title เสมอ (จอแคบเห็นแค่สัญลักษณ์)
