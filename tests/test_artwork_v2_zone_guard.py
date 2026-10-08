"""Artwork V2 (4 ต.ค.) — 1 คู่โซน = 1 คำขอ · กันยิงคำขอรัว · ตัวแก้โซน (zoom/เลื่อนภาพ)

ผู้ใช้สั่ง: *"1 คู่ Zone คือ 1 Request ระบบต้องป้องกันการส่ง Request รัวด้วย"*
ทุกการกด "ตรวจ" = เรียก Cloud Vision จริง ⇒ ด่านต้องอยู่ฝั่งเซิร์ฟเวอร์ ไม่พึ่งปุ่ม disable
"""

from __future__ import annotations

import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from artwork_v2_fake import fta  # noqa: E402

from artwork_v2 import (config, imaging, jobs, keystore, pipeline, runguard,  # noqa: E402
                        vision_client)

fitz = pytest.importorskip("fitz")
from PIL import Image  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    os.makedirs(config.JOBS_DIR)
    yield


def _pdf(lines, w=400, h=300):
    d = fitz.open()
    p = d.new_page(width=w, height=h)
    for i, t in enumerate(lines):
        p.insert_text((20, 40 + i * 30), t, fontsize=12)
    return d.tobytes()


TA = ["Sodium 475 mg 20%", "Fat 1.5g", "Net weight 85 g"]
TB = ["Sodium 475 mg 24%", "Fat 1.5g", "Net weight 85 g"]
PAIR = {"a": {"page": 0, "bbox": [0, 0, 1, 1]}, "b": {"page": 0, "bbox": [0, 0, 1, 1]}}


def _job():
    return jobs.create(("a.pdf", _pdf(TA)), ("b.pdf", _pdf(TB)))["id"]


# ── defaults ─────────────────────────────────────────────────────────

def test_defaults_are_on():
    assert config.ONE_REQUEST_PER_PAIR is True and config.RUN_GUARD is True
    assert config.RUN_MAX_CONCURRENT >= 1 and config.RUN_COOLDOWN_S > 0
    assert config.KEY_TEST_COOLDOWN_S > 0
    snap = pipeline.settings_snapshot()
    for k in ("ONE_REQUEST_PER_PAIR", "RUN_GUARD", "RUN_MAX_CONCURRENT", "RUN_COOLDOWN_S"):
        assert k in snap, k


# ── RunGuard ─────────────────────────────────────────────────────────

class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_same_job_while_running_is_409():
    g = runguard.RunGuard()
    g.acquire("j1", 2, 3)
    with pytest.raises(runguard.Busy) as e:
        g.acquire("j1", 2, 3)
    assert e.value.status == 409
    g.release("j1", cooldown=False)
    g.acquire("j1", 2, 3)                      # คืนสิทธิ์แล้ววิ่งได้


def test_concurrency_cap_is_429_and_other_jobs_free_after_release():
    g = runguard.RunGuard()
    g.acquire("a", 2, 0)
    g.acquire("b", 2, 0)
    with pytest.raises(runguard.Busy) as e:
        g.acquire("c", 2, 0)
    assert e.value.status == 429 and e.value.retry_after
    g.release("a")
    g.acquire("c", 2, 0)


def test_cooldown_after_run_then_allowed():
    clk = _Clock()
    g = runguard.RunGuard(clock=clk)
    g.acquire("j", 0, 3.0)
    g.release("j")
    clk.t += 1.0
    with pytest.raises(runguard.Busy) as e:
        g.acquire("j", 0, 3.0)
    assert e.value.status == 429 and 1.9 < e.value.retry_after <= 2.0
    clk.t += 2.1
    g.acquire("j", 0, 3.0)


def test_release_without_cooldown_does_not_block():
    clk = _Clock()
    g = runguard.RunGuard(clock=clk)
    g.acquire("j", 0, 3.0)
    g.release("j", cooldown=False)            # ข้อมูลผิด ⇒ ไม่ได้ยิงอะไร ⇒ ไม่ต้องพัก
    g.acquire("j", 0, 3.0)


def test_zero_disables_cap_and_cooldown():
    g = runguard.RunGuard()
    for k in "abcdefgh":
        g.acquire(k, 0, 0)
    for k in "abcdefgh":
        g.release(k)
        g.acquire(k, 0, 0)


# ── 1 คู่ = 1 คำขอ ──────────────────────────────────────────────────

def _ids(reqs):
    return [[i["id"] for i in r] for r in reqs]


def _small_groups(n):
    return [[{"id": "p%d_a" % i, "jpeg": b"x" * 500}, {"id": "p%d_b" % i, "jpeg": b"x" * 500}]
            for i in range(1, n + 1)]


def test_pack_one_request_per_pair():
    assert _ids(vision_client.pack(_small_groups(3))) == [
        ["p1_a", "p1_b"], ["p2_a", "p2_b"], ["p3_a", "p3_b"]]


def test_pack_old_behaviour_when_flag_off(monkeypatch):
    monkeypatch.setattr(config, "ONE_REQUEST_PER_PAIR", False)
    assert _ids(vision_client.pack(_small_groups(3))) == [
        ["p1_a", "p1_b", "p2_a", "p2_b", "p3_a", "p3_b"]]


def test_fits_one_request_matches_pack_limit(monkeypatch):
    monkeypatch.setattr(config, "MAX_REQUEST_BYTES", 5000)
    assert vision_client.fits_one_request([b"x" * 1000, b"x" * 1000])
    assert not vision_client.fits_one_request([b"x" * 3000, b"x" * 3000])
    b = imaging.pair_image_budget(2)
    assert vision_client.fits_one_request([b"\xff" * b, b"\xff" * b])


def _capture_annotate(store):
    def fake(groups, poster=None, key=None):
        store.append([[(it["id"], len(it["jpeg"])) for it in g] for g in groups])
        res, ids = {}, []
        for g in groups:
            for it in g:
                ids.append(it["id"])
                W, H = Image.open(io.BytesIO(it["jpeg"])).size
                T = TA if it["id"].endswith("a") else TB
                lines = [(t, int(0.05 * W), int(H * (0.1 + 0.25 * i)),
                          {"cw": max(4, W // 40), "h": max(8, H // 15)})
                         for i, t in enumerate(T)]
                res[it["id"]] = {"ok": True, "error": "", "fta": fta(lines, W, H),
                                 "request_index": 0}
        return {"results": res, "calls": [{"index": 0, "phase": "main", "images": ids,
                                           "json_bytes": 10, "status": 200, "attempts": 1,
                                           "ms": 1, "error": "",
                                           "model_requested": config.MODEL,
                                           "endpoint": config.ENDPOINT, "at": "t"}]}
    return fake


def _run_sizes(monkeypatch, pairs=1):
    got = []
    monkeypatch.setattr(vision_client, "annotate", _capture_annotate(got))
    keystore.save(KEY)
    r = pipeline.run(_job(), [PAIR] * pairs)
    return r, got[0]


def test_normal_pair_is_byte_identical_with_and_without_flag(monkeypatch):
    r1, _ = _run_sizes(monkeypatch)
    monkeypatch.setattr(config, "ONE_REQUEST_PER_PAIR", False)
    r0, _ = _run_sizes(monkeypatch)
    for s in "ab":
        assert r1["pairs"][0]["sides"][s]["sha1"] == r0["pairs"][0]["sides"][s]["sha1"]
        assert not r1["pairs"][0]["sides"][s]["encode"].get("pair_refit")


def test_oversize_pair_is_refit_into_one_request(monkeypatch):
    _, g = _run_sizes(monkeypatch)
    sizes = [n for _, n in g[0]]
    # งบคำขอเล็กจนภาพมาตรฐานทั้งคู่ใส่คำขอเดียวไม่ได้ (แต่ภาพเดียวยังใส่ได้)
    limit = (max(sizes) * 4 // 3 + 400) * 2 - 400
    monkeypatch.setattr(config, "MAX_REQUEST_BYTES", limit)
    assert not vision_client.fits_one_request([b"x" * n for n in sizes])
    r, g2 = _run_sizes(monkeypatch)
    pair = g2[0]
    assert [i for i, _ in pair] == ["p1_a", "p1_b"]
    assert vision_client.fits_one_request([b"x" * n for _, n in pair])
    assert len(vision_client.pack([[{"id": i, "jpeg": b"x" * n} for i, n in pair]])) == 1
    enc = [r["pairs"][0]["sides"][s]["encode"] for s in "ab"]
    assert any(e.get("pair_refit") for e in enc)
    assert any("คำขอเดียว" in w for w in r["warnings"])        # ไม่เงียบ


def test_oversize_pair_split_when_flag_off(monkeypatch):
    monkeypatch.setattr(config, "ONE_REQUEST_PER_PAIR", False)
    _, g = _run_sizes(monkeypatch)
    sizes = [n for _, n in g[0]]
    limit = (max(sizes) * 4 // 3 + 400) * 2 - 400
    monkeypatch.setattr(config, "MAX_REQUEST_BYTES", limit)
    _, g2 = _run_sizes(monkeypatch)
    items = [{"id": i, "jpeg": b"x" * n} for i, n in g2[0]]
    assert len(vision_client.pack([items])) == 2                 # เดิม: แยกสองคำขอ


def test_two_pairs_two_requests_end_to_end(monkeypatch):
    seen = []

    class _R:
        status_code = 200

        def __init__(self, n):
            self.n = n

        def json(self):
            return {"responses": [{"fullTextAnnotation": fta([("Fat 1.5g", 10, 10)])}
                                  for _ in range(self.n)]}

    def poster(url, data, headers, timeout):
        import json
        n = len(json.loads(data)["requests"])
        seen.append(n)
        return _R(n)

    monkeypatch.setattr(config, "REREAD_ENABLED", False)
    keystore.save(KEY)
    pipeline.run(_job(), [PAIR, PAIR], poster=poster)
    assert seen == [2, 2]


# ── route: กันยิงรัว ─────────────────────────────────────────────────

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


def test_double_submit_while_running_is_rejected(monkeypatch):
    c = _client(monkeypatch)
    jid = _job()
    calls, inner = [], {}

    def fake_run(job_id, pairs, **kw):
        calls.append(job_id)
        r = c.post("/api/artwork_v2/jobs/%s/run" % jid, json={"pairs": [PAIR]})
        inner.update(status=r.status_code, body=r.get_json())
        return {"run": "run_001", "verdict": "PASS", "stage": {}}

    monkeypatch.setattr(pipeline, "run", fake_run)
    r = c.post("/api/artwork_v2/jobs/%s/run" % jid, json={"pairs": [PAIR]})
    assert r.status_code == 200
    assert calls == [jid]                                        # ไม่ถึง pipeline รอบที่สอง
    assert inner["status"] == 409 and inner["body"]["busy"] is True
    assert inner["body"]["error"]


def test_resubmit_right_after_result_is_cooled_down(monkeypatch):
    c = _client(monkeypatch)
    jid = _job()
    calls = []
    monkeypatch.setattr(pipeline, "run", lambda job_id, pairs, **kw: (
        calls.append(1) or {"run": "run_001", "verdict": "PASS", "stage": {}}))
    assert c.post("/api/artwork_v2/jobs/%s/run" % jid, json={"pairs": [PAIR]}).status_code == 200
    r = c.post("/api/artwork_v2/jobs/%s/run" % jid, json={"pairs": [PAIR]})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert len(calls) == 1
    monkeypatch.setattr(config, "RUN_COOLDOWN_S", 0.0)
    assert c.post("/api/artwork_v2/jobs/%s/run" % jid, json={"pairs": [PAIR]}).status_code == 200


def test_invalid_pairs_do_not_start_cooldown(monkeypatch):
    c = _client(monkeypatch)
    jid = _job()
    keystore.save(KEY)
    sent = []
    monkeypatch.setattr(vision_client, "annotate", lambda *a, **k: sent.append(1))
    assert c.post("/api/artwork_v2/jobs/%s/run" % jid, json={"pairs": []}).status_code == 400
    r = c.post("/api/artwork_v2/jobs/%s/run" % jid, json={"pairs": []})
    assert r.status_code == 400                                  # ไม่ใช่ 429
    assert not sent


def test_failure_still_releases_the_job(monkeypatch):
    c = _client(monkeypatch)
    jid = _job()

    def boom(job_id, pairs, **kw):
        raise RuntimeError("x")
    monkeypatch.setattr(pipeline, "run", boom)
    monkeypatch.setattr(config, "RUN_COOLDOWN_S", 0.0)
    assert c.post("/api/artwork_v2/jobs/%s/run" % jid, json={"pairs": [PAIR]}).status_code == 500
    assert c.post("/api/artwork_v2/jobs/%s/run" % jid, json={"pairs": [PAIR]}).status_code == 500


def test_guard_off_is_old_behaviour(monkeypatch):
    c = _client(monkeypatch)
    jid = _job()
    monkeypatch.setattr(config, "RUN_GUARD", False)
    n = []

    def fake_run(job_id, pairs, **kw):
        n.append(1)
        if len(n) == 1:
            r = c.post("/api/artwork_v2/jobs/%s/run" % jid, json={"pairs": [PAIR]})
            assert r.status_code == 200
        return {"run": "run_001", "verdict": "PASS", "stage": {}}
    monkeypatch.setattr(pipeline, "run", fake_run)
    assert c.post("/api/artwork_v2/jobs/%s/run" % jid, json={"pairs": [PAIR]}).status_code == 200
    assert len(n) == 2


def test_key_test_is_rate_limited(monkeypatch):
    c = _client(monkeypatch)
    calls = []
    monkeypatch.setattr(vision_client, "annotate", lambda *a, **k: (
        calls.append(1) or {"results": {"test": {"ok": False, "error": "e"}},
                            "calls": [{"status": 403, "attempts": 1}]}))
    assert c.post("/api/artwork_v2/settings/test").status_code == 200
    r = c.post("/api/artwork_v2/settings/test")
    assert r.status_code == 429 and len(calls) == 1


# ── หน้าเว็บ: ตัวแก้โซน ──────────────────────────────────────────────

def _read(*p):
    return open(os.path.join(ROOT, *p), encoding="utf-8").read()


def test_page_has_zoom_bars_pan_mode_and_scroll_boxes():
    html = _read("templates", "artwork_v2.html")
    for must in ('id="v2BoxA"', 'id="v2BoxB"', 'data-mode="draw"', 'data-mode="pan"',
                 'class="v2-zbar"', 'data-z="fitw"', 'data-z="fitp"', 'data-z="range"',
                 ".v2-stage-box", ".v2-h-se", ".v2-zone.sel"):
        assert must in html, must
    assert html.count('class="v2-zbar"') == 2


def test_js_ids_exist_and_editor_pieces_present():
    import re
    js = _read("static", "js", "artwork_v2.js")
    html = _read("templates", "artwork_v2.html")
    made_by_js = set(re.findall(r'id="([A-Za-z0-9_]+)"', js))
    for i in sorted(set(re.findall(r'\$\("([A-Za-z0-9_]+)"\)', js)) - made_by_js):
        assert 'id="%s"' % i in html, i
    for must in ("function setZoom", "function fitPct", "function editBox", "function panStart",
                 "function removeZone", "setPointerCapture", "passive: false",
                 "ZOOM_MIN", "ZOOM_MAX", "MIN_ZONE"):
        assert must in js, must
    # ตัวแก้โซนต้องกันการกดปุ่มตรวจซ้ำระหว่างรอ (ชั้นแรก — เซิร์ฟเวอร์เป็นชั้นจริง)
    assert "S.busy" in js


def test_zoom_range_matches_js_constants():
    import re
    js = _read("static", "js", "artwork_v2.js")
    html = _read("templates", "artwork_v2.html")
    zmin = int(re.search(r"ZOOM_MIN = (\d+)", js).group(1))
    zmax = int(re.search(r"ZOOM_MAX = (\d+)", js).group(1))
    for m in re.finditer(r'data-z="range"[^>]*', html):
        assert 'min="%d"' % zmin in m.group(0) and 'max="%d"' % zmax in m.group(0)


def test_refit_leaves_the_side_that_already_fits_untouched(monkeypatch):
    many = ["Ingredient line %d: chicken, tuna, rice, 1.5g, 0.16%%" % i for i in range(9)]
    jid = jobs.create(("a.pdf", _pdf(["x"])), ("b.pdf", _pdf(many)))["id"]
    got = []
    monkeypatch.setattr(vision_client, "annotate", _capture_annotate(got))
    keystore.save(KEY)
    r0 = pipeline.run(jid, [PAIR])
    na, nb = [n for _, n in got[0][0]]
    assert na < nb
    limit = None
    for lim in range(2000, 400000, 50):              # ภาพ A พอดีงบ · B เกินงบ · รวมกันเกินคำขอ
        monkeypatch.setattr(config, "MAX_REQUEST_BYTES", lim)
        if (na <= imaging.pair_image_budget(2) < nb
                and not vision_client.fits_one_request([b"x" * na, b"x" * nb])):
            limit = lim
            break
    assert limit is not None
    r = pipeline.run(jid, [PAIR])
    sa, sb = r["pairs"][0]["sides"]["a"], r["pairs"][0]["sides"]["b"]
    assert sa["sha1"] == r0["pairs"][0]["sides"]["a"]["sha1"] and not sa["encode"].get("pair_refit")
    assert sb["encode"].get("pair_refit") and sb["jpeg_bytes"] <= imaging.pair_image_budget(2)
