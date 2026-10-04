"""Artwork V2 (4 ต.ค. รอบ 4) — เปิดหน้าแล้ว "ถามก่อน" เปิดงานค้าง + เลือกงานเดิมแล้วได้โซนกลับมา

ผู้ใช้สั่ง: *"ทำให้เหมือนโหมดเดิม · ของเก่าก็ดึง zone มา"* ⇒
1. ไม่กู้คืนเงียบ ๆ — ขึ้นแถบ 💾 [เปิดต่อ]/[ทิ้ง] (``ARTWORK_V2_RESTORE_CONFIRM``, ``0`` = เปิดเองแบบเดิม)
2. เลือกงานจากรายการ "งานล่าสุด" ⇒ โซนกลับมา (เบราว์เซอร์นี้ก่อน · ไม่มีค่อยใช้โซนของรอบตรวจล่าสุด)
"""

from __future__ import annotations

import io
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from artwork_v2_fake import fta  # noqa: E402

from artwork_v2 import config, jobs, keystore, pipeline, runguard, vision_client  # noqa: E402

fitz = pytest.importorskip("fitz")
from PIL import Image  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js", "artwork_v2.js")
TPL = os.path.join(ROOT, "templates", "artwork_v2.html")
KEY = "AIza" + "Q1w2E3r4T5y6U7i8O9p0A1s2D3f4G5h6J7k"
TA = ["Sodium 475 mg 20%", "Net weight 85 g"]
TB = ["Sodium 475 mg 24%", "Net weight 85 g"]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    d = tmp_path / "v2"
    monkeypatch.setattr(config, "DATA_DIR", str(d))
    monkeypatch.setattr(config, "JOBS_DIR", str(d / "jobs"))
    monkeypatch.setattr(config, "SECRET_DIR", str(d / "secret"))
    monkeypatch.setattr(config, "KEY_FILE", str(d / "secret" / "key.json"))
    monkeypatch.delenv(config.KEY_ENV, raising=False)
    monkeypatch.setattr(config, "REREAD_ENABLED", False)
    os.makedirs(config.JOBS_DIR)
    yield


def _pdf(lines, pages=1):
    d = fitz.open()
    for _ in range(pages):
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
            lines = [(t, int(0.05 * W), int(H * (0.1 + 0.4 * i)), {"cw": max(4, W // 40), "h": max(8, H // 15)})
                     for i, t in enumerate(T)]
            res[it["id"]] = {"ok": True, "error": "", "fta": fta(lines, W, H), "request_index": 0}
    return {"results": res, "calls": [{"index": 0, "phase": "main", "images": ids, "json_bytes": 10,
                                       "status": 200, "attempts": 1, "ms": 1, "error": "",
                                       "model_requested": config.MODEL, "endpoint": config.ENDPOINT, "at": "t"}]}


def _client(monkeypatch):
    from flask import Flask, g
    from artwork_v2 import routes
    from artwork_v2.routes import artwork_v2_bp
    monkeypatch.setattr(routes, "_RUNS", runguard.RunGuard())
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


PAIRS = [{"a": {"page": 1, "bbox": [0.1, 0.1, 0.5, 0.3]}, "b": {"page": 0, "bbox": [0.2, 0.15, 0.6, 0.25]}},
         {"a": {"page": 0, "bbox": [0, 0, 1, 1]}, "b": {"page": 1, "bbox": [0, 0, 1, 1]}}]


# ── ฝั่งเซิร์ฟเวอร์: โซนของรอบตรวจล่าสุด ─────────────────────────────────

def test_job_returns_zones_of_the_latest_finished_run(monkeypatch):
    monkeypatch.setattr(vision_client, "annotate", _fake)
    keystore.save(KEY)
    jid = jobs.create(("a.pdf", _pdf(TA, 2)), ("b.pdf", _pdf(TB, 2)))["id"]
    c = _client(monkeypatch)
    assert c.get("/api/artwork_v2/jobs/%s" % jid).get_json()["last_pairs"] == []   # ยังไม่เคยตรวจ
    pipeline.run(jid, PAIRS[:1])
    pipeline.run(jid, PAIRS)
    got = c.get("/api/artwork_v2/jobs/%s" % jid).get_json()["last_pairs"]
    assert got == PAIRS                                     # รอบล่าสุด · หน้า/กรอบตรงที่ส่งตรวจ
    assert pipeline.parse_pairs(got)                        # ส่งกลับไปตรวจได้ทันที


def test_last_pairs_ignores_unfinished_and_broken_runs(monkeypatch):
    monkeypatch.setattr(vision_client, "annotate", _fake)
    keystore.save(KEY)
    jid = jobs.create(("a.pdf", _pdf(TA, 2)), ("b.pdf", _pdf(TB, 2)))["id"]
    pipeline.run(jid, PAIRS[:1])
    d = jobs.job_dir(jid)
    os.makedirs(os.path.join(d, "run_002", "raw"))          # รอบที่ล้มกลางทาง (ไม่มี result.json)
    assert jobs.last_pairs(d) == PAIRS[:1]
    os.makedirs(os.path.join(d, "run_003"))
    with open(os.path.join(d, "run_003", "result.json"), "w") as f:
        f.write('{"pairs": [{"sides": {"a": {"bbox": "x"}}}]}')   # ผิดรูป ⇒ ไม่เดา
    assert jobs.last_pairs(d) == []


# ── หน้าเว็บ ─────────────────────────────────────────────────────────

def test_default_asks_before_restoring():
    assert config.RESTORE_CONFIRM is True
    assert config.RESTORE_MAX_AGE_DAYS == 7


@pytest.mark.parametrize("flag,expect", [(True, "1"), (False, "0")])
def test_template_carries_the_flag(monkeypatch, flag, expect):
    monkeypatch.setattr(config, "RESTORE_CONFIRM", flag)
    html = _client(monkeypatch).get("/artwork_v2").get_data(as_text=True)
    assert 'data-restore-confirm="%s"' % expect in html
    assert 'id="v2Restore"' in html and ".v2-restore" in html


def _js():
    with open(JS, encoding="utf-8") as f:
        return f.read()


def test_page_load_does_not_open_the_job_silently():
    js = _js()
    start = js[js.index("const sess = loadSession();"):]
    block = start[:start.index("async function offerRestore")]
    # เปิดเองได้เฉพาะเมื่อปิดธง · ค่าปกติ = ขึ้นแถบถาม
    assert re.search(r'restoreConfirm === "0"\)\s*openJob\(', block)
    assert "offerRestore(sess)" in block
    offer = start[start.index("async function offerRestore"):]
    assert "v2RestoreYes" in offer and "v2RestoreNo" in offer and "clearSession()" in offer
    assert offer.index("await api(") < offer.index('bar.style.display = ""')   # ตรวจว่างานยังเปิดได้ก่อนเสนอ


def test_recent_list_brings_zones_back():
    js = _js()
    oj = js[js.index("async function openJob"):]
    oj = oj[:oj.index("S.job = m;")]
    assert "readJobZones()[m.id]" in oj and "m.last_pairs" in oj
    assert oj.index("readJobZones()") < oj.index("m.last_pairs")   # ที่แก้ในเบราว์เซอร์ล่าสุดชนะ
    assert 'openJob(ev.target.value, null)' in js


def test_tpl_and_js_ids_exist():
    with open(TPL, encoding="utf-8") as f:
        tpl = f.read()
    for i in ("v2Restore",):
        assert 'id="%s"' % i in tpl
