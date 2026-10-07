"""Artwork V2 — โหมด "ภาพที่ส่งคมสูงสุด" (3 ต.ค. 2026 · ผู้ใช้สั่ง)

กติกาที่ล็อก:
* คู่ภาพ A/B ยังอยู่ **คำขอ Vision เดียวกัน** (โมเดลรุ่นเดียวกัน) — งบไบต์ต่อภาพคิดจากสูตรเดียวกับ
  ``vision_client.pack``
* **ไม่มีทางได้ dpi ต่ำกว่าแบบมาตรฐาน** · ไม่เกินเพดาน dpi / MP · ไม่ย่อภาพ ไม่ลดคุณภาพ JPEG
* โหมด "standard" = ภาพเดิม **ทุกไบต์** (sha1 เท่าเดิม)
* ภาพถ่าย = พิกเซลต้นฉบับเหมือนเดิม (ขยายภาพ raster ไม่ได้รายละเอียดเพิ่ม)
* อ่านซ้ำแบบซูมต้องซูมจาก dpi ที่ใช้จริง — ไม่หยาบกว่ารอบหลัก
* ชั้นเทียบไม่ขึ้นกับ dpi (สองฝั่งได้ dpi ไม่เท่ากันในโหมดนี้)
"""

from __future__ import annotations

import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from artwork_v2_fake import fta, fta_from_lines, load_real  # noqa: E402

from artwork_v2 import (compare, config, imaging, jobs, keystore, pipeline,  # noqa: E402
                        textmodel, vision_client)

fitz = pytest.importorskip("fitz")
from PIL import Image  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = "AIza" + "C" * 35
DATA = os.path.join(os.path.dirname(__file__), "data", "artwork_v2", "avoderm_m1m2_lines.txt")


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


def _dense_pdf(path, w_mm=120, h_mm=60, rows=22, scale=1.0):
    """ฉลากจำลอง: ตัวหนังสือเล็กแน่น + พื้นสี (บีบอัด JPEG ยากพอให้ชนงบไบต์)"""
    mm = 72 / 25.4
    doc = fitz.open()
    pg = doc.new_page(width=w_mm * mm, height=h_mm * mm)
    pg.draw_rect(fitz.Rect(5 * mm, 5 * mm, (w_mm - 5) * mm, (h_mm - 5) * mm),
                 color=None, fill=(0.95, 0.9, 0.78))
    for i in range(rows):
        pg.insert_text((8 * mm, (9 + i * 2.2) * mm * scale),
                       "Chicken broth carrots peas 7.0%%  D-calcium pantothenate %02d" % i,
                       fontsize=5.5 * scale)
    doc.save(str(path))
    return str(path)


ZONE = [0.03, 0.05, 0.94, 0.9]


def _tiny_pdf():
    doc = fitz.open()
    doc.new_page(width=200, height=100).insert_text((10, 30), "x")
    return doc.tobytes()


# ── งบไบต์ ────────────────────────────────────────────────────────────

def test_two_images_at_budget_fit_one_request():
    b = imaging.pair_image_budget(2)
    jp = b"\xff" * b
    reqs = vision_client.pack([[{"id": "a", "jpeg": jp}, {"id": "b", "jpeg": jp}]])
    assert len(reqs) == 1 and len(reqs[0]) == 2
    # ใหญ่กว่างบไป 1% ต้องถูกแยกคำขอ ⇒ งบไม่หลวมเกินไปจนไร้ความหมาย
    jp2 = b"\xff" * int(b * 1.01)
    assert len(vision_client.pack([[{"id": "a", "jpeg": jp2}, {"id": "b", "jpeg": jp2}]])) == 2


# ── เรนเดอร์ ─────────────────────────────────────────────────────────

def test_sharp_render_beats_standard_within_budget(tmp_path):
    src = imaging.Source(_dense_pdf(tmp_path / "a.pdf"))
    std, sinfo = src.render_zone(0, ZONE)
    budget = 900_000
    img, jpg, info = imaging.render_zone_sharp(src, 0, ZONE, budget)
    assert info["sharpness"] == "max" and info["base_dpi"] == sinfo["dpi"]
    assert info["dpi"] > sinfo["dpi"] * 1.2, info["tries"]
    assert len(jpg) <= budget
    assert img.shape[1] > std.shape[1] and img.shape[0] > std.shape[0]
    # ภาพที่คืน = ภาพที่เข้ารหัสจริง (ใช้คำนวณพิกัด/วาดกรอบ)
    assert Image.open(io.BytesIO(jpg)).size == (img.shape[1], img.shape[0])
    assert info["tries"][0]["dpi"] == sinfo["dpi"] and all(t["dpi"] >= sinfo["dpi"]
                                                         for t in info["tries"])
    assert len(info["tries"]) <= config.SHARP_MAX_RENDERS


def test_sharp_render_never_below_standard_when_budget_is_tight(tmp_path):
    src = imaging.Source(_dense_pdf(tmp_path / "a.pdf"))
    std, sinfo = src.render_zone(0, ZONE)
    j0 = len(imaging.encode_jpeg(std, config.JPEG_QUALITIES[0]))
    img, jpg, info = imaging.render_zone_sharp(src, 0, ZONE, j0 + 10)
    assert info["dpi"] == sinfo["dpi"] and len(jpg) <= j0 + 10
    # งบน้อยกว่าภาพมาตรฐาน ⇒ ไม่ใช้โหมดนี้ (ผู้เรียกถอยไปทางเดิม — ไม่ลด dpi ต่ำกว่าเดิม)
    assert imaging.render_zone_sharp(src, 0, ZONE, j0 - 1) is None


def test_sharp_render_respects_dpi_and_megapixel_caps(tmp_path, monkeypatch):
    src = imaging.Source(_dense_pdf(tmp_path / "a.pdf"))
    monkeypatch.setattr(config, "PDF_ZONE_DPI_MAX", 520)
    img, jpg, info = imaging.render_zone_sharp(src, 0, ZONE, 50_000_000)
    assert info["dpi"] <= 520.5
    monkeypatch.setattr(config, "PDF_ZONE_DPI_MAX", 1200)
    monkeypatch.setattr(config, "MAX_IMAGE_MP", 3.0)
    img, jpg, info = imaging.render_zone_sharp(src, 0, ZONE, 50_000_000)
    assert img.shape[0] * img.shape[1] / 1e6 <= 3.05


def test_photo_is_never_upscaled(tmp_path):
    p = tmp_path / "a.png"
    Image.new("RGB", (800, 600), "white").save(p)
    assert imaging.render_zone_sharp(imaging.Source(str(p)), 0, ZONE, 50_000_000) is None


# ── ทั้งรอบ ───────────────────────────────────────────────────────────

def _fake(spec_a, spec_b):
    seen = []

    def fake(groups, poster=None, key=None):
        res, ids = {}, []
        for gi, g in enumerate(groups):
            for it in g:
                ids.append(it["id"])
                W, H = Image.open(io.BytesIO(it["jpeg"])).size
                seen.append((it["id"], W, H, len(it["jpeg"])))
                S = spec_a if it["id"].endswith("a") else spec_b
                lines = [(t, int(xf * W), int(yf * H), {"cw": max(4, W // 60), "h": max(8, H // 25)})
                         for t, xf, yf in S]
                res[it["id"]] = {"ok": True, "error": "", "fta": fta(lines, W, H),
                                 "request_index": gi}
        return {"results": res, "calls": [{"index": 0, "phase": "main", "images": ids,
                                           "json_bytes": 10, "status": 200, "attempts": 1,
                                           "ms": 1, "error": "", "at": "t"}]}
    fake.seen = seen
    return fake


SA = [("Sodium 475 mg 20%", 0.05, 0.10), ("Natural recipe with chicken", 0.05, 0.30)]
SB = [("Sodium 475 mg 24%", 0.05, 0.10), ("Natural recipe with chicken", 0.05, 0.30)]


def _job(tmp_path):
    a = open(_dense_pdf(tmp_path / "a.pdf"), "rb").read()
    b = open(_dense_pdf(tmp_path / "b.pdf", scale=0.9), "rb").read()
    return jobs.create(("a.pdf", a), ("b.pdf", b))["id"]


PAIRS = [{"a": {"page": 0, "bbox": ZONE}, "b": {"page": 0, "bbox": ZONE}}]


def test_pipeline_max_mode_is_sharper_and_pair_stays_in_one_request(tmp_path, monkeypatch):
    jid = _job(tmp_path)
    keystore.save(KEY)
    fk = _fake(SA, SB)
    monkeypatch.setattr(vision_client, "annotate", fk)
    rs = pipeline.run(jid, PAIRS, sharpness="standard")
    std = {s: rs["pairs"][0]["sides"][s] for s in "ab"}
    fk2 = _fake(SA, SB)
    monkeypatch.setattr(vision_client, "annotate", fk2)
    rm = pipeline.run(jid, PAIRS, sharpness="max")
    p = rm["pairs"][0]
    assert rm["sharpness"] == "max" and rs["sharpness"] == "standard"
    for s in "ab":
        mx = p["sides"][s]
        assert mx["render"]["sharpness"] == "max"
        assert mx["render"]["dpi"] > std[s]["render"]["dpi"]
        assert mx["sent_px"][0] > std[s]["sent_px"][0]
        assert mx["encode"]["downscale"] == 1.0
    # คู่หลักอยู่คำขอเดียวกัน (vision_client.pack ตัวจริง)
    main = [{"id": "p1_%s" % s, "jpeg": open(os.path.join(
        jobs.run_dir(jid, rm["run"]), "img", "p1_%s.jpg" % s), "rb").read()}
        for s in "ab"]
    assert len(vision_client.pack([main])) == 1
    assert p["same_request"] is True
    # ผลเทียบไม่เปลี่ยนเพราะ dpi
    sig = lambda r: sorted((f["class"], f["a"]["frag"], f["b"]["frag"])  # noqa: E731
                           for f in r["pairs"][0]["findings"])
    assert sig(rm) == sig(rs) == [("NUMBER", "0", "4")]
    assert "sharpness=max" in rm["log_text"] and "sharp: mode=max" in rm["log_text"]
    assert "sharpness=standard" in rs["log_text"]


def test_standard_mode_is_byte_identical_to_the_old_path(tmp_path, monkeypatch):
    jid = _job(tmp_path)
    keystore.save(KEY)
    monkeypatch.setattr(vision_client, "annotate", _fake(SA, SB))
    r = pipeline.run(jid, PAIRS, sharpness="standard")
    for s in "ab":
        img, _ = jobs.source(jid, s).render_zone(0, ZONE)
        jpeg, _, _ = imaging.fit_jpeg(img, config.MAX_IMAGE_BYTES)
        assert r["pairs"][0]["sides"][s]["sha1"] == imaging.sha1_bytes(jpeg)[:12]


LONG_A = [("Sodium 475 mg 20% per serving of natural chicken recipe", 0.04, 0.10)]
LONG_B = [("Sodium 475 mg 24% per serving of natural chicken recipe", 0.04, 0.10)]


def test_reread_zooms_from_the_dpi_actually_used(tmp_path, monkeypatch):
    """บรรทัดยาวเกือบเต็มโซน: เส้นทางเดิม (400 dpi × 2 · ด้านยาว ≤ 2400 px) ได้ครอปที่
    **หยาบกว่ารอบหลัก** ของโหมดคมสูงสุด ⇒ ต้องซูมจาก dpi ที่ใช้จริง และไม่เกินเพดาน dpi"""
    jid = _job(tmp_path)
    keystore.save(KEY)
    monkeypatch.setattr(vision_client, "annotate", _fake(LONG_A, LONG_B))
    r = pipeline.run(jid, PAIRS, sharpness="max")
    it = r["reread"]["items"][0]
    assert r["reread"]["sharpness"] == "max"
    for s in "ab":
        main = r["pairs"][0]["sides"][s]["render"]["dpi"]
        crop = it["crops"][s]
        assert crop["px"][0] > config.REREAD_MAX_SIDE, crop   # เคสที่เส้นทางเดิมจะถูกบีบ
        assert crop["dpi"] >= main - 0.5, (s, main, crop)
        assert crop["dpi"] <= max(config.PDF_ZONE_DPI_MAX, main) + 0.5, crop


def test_standard_mode_reread_is_unchanged(tmp_path, monkeypatch):
    jid = _job(tmp_path)
    keystore.save(KEY)
    monkeypatch.setattr(vision_client, "annotate", _fake(LONG_A, LONG_B))
    r = pipeline.run(jid, PAIRS, sharpness="standard")
    for s in "ab":
        crop = r["reread"]["items"][0]["crops"][s]
        assert max(crop["px"]) <= config.REREAD_MAX_SIDE + 1


def test_render_that_overshoots_the_budget_is_never_used(tmp_path, monkeypatch):
    """เป้า > งบ (บังคับให้เรนเดอร์บางครั้งเกินงบ) ⇒ ภาพที่ได้ต้องไม่เกินงบเสมอ"""
    src = imaging.Source(_dense_pdf(tmp_path / "a.pdf"))
    monkeypatch.setattr(config, "SHARP_FILL", 1.6)
    budget = 900_000
    img, jpg, info = imaging.render_zone_sharp(src, 0, ZONE, budget)
    assert any(not t["fit"] for t in info["tries"]), info["tries"]
    assert len(jpg) <= budget
    assert info["dpi"] == max(t["dpi"] for t in info["tries"] if t["fit"])


def test_unknown_mode_falls_back_to_station_default(monkeypatch):
    monkeypatch.setattr(config, "SHARPNESS", "standard")
    assert pipeline.norm_sharpness(None) == "standard"
    assert pipeline.norm_sharpness("bogus") == "standard"
    assert pipeline.norm_sharpness("MAX") == "max"


def test_default_mode_is_standard():
    # วัดบนสถานี: "max" แย่ลงทุกตัวชี้วัด (ชุด run003_max) ⇒ ค่าเริ่มต้นกลับเป็นภาพเดิม
    assert config.SHARPNESS == "standard"
    assert "SHARPNESS" in pipeline.settings_snapshot()


def test_route_passes_mode_and_page_has_selector(monkeypatch):
    from flask import Flask, g
    from artwork_v2.routes import artwork_v2_bp
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
    c = app.test_client()
    got = {}

    def fake_run(job_id, pairs, poster=None, progress=None, sharpness=None, ai_mode=None, **kw):
        got["s"] = sharpness
        return {"run": "run_001", "verdict": "PASS", "stage": {}}
    monkeypatch.setattr(pipeline, "run", fake_run)
    html = c.get("/artwork_v2").get_data(as_text=True)
    assert 'id="v2Sharp"' in html and '<option value="standard" selected>' in html
    jid = jobs.create(("a.pdf", _tiny_pdf()),
                      ("b.pdf", _tiny_pdf()))["id"]
    r = c.post("/api/artwork_v2/jobs/%s/run" % jid, json={"pairs": [], "sharpness": "standard"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert got.get("s") == "standard"
    c.post("/api/artwork_v2/jobs/%s/run" % jid, json={"pairs": []})
    assert got.get("s") is None           # ไม่ส่ง = ค่าของเครื่อง (pipeline.norm_sharpness)
    js = open(os.path.join(ROOT, "static", "js", "artwork_v2.js"), encoding="utf-8").read()
    assert 'sharpness: $("v2Sharp")' in js


# ── ชั้นเทียบไม่ขึ้นกับ dpi ────────────────────────────────────────────

def _scaled(side, k):
    W, H, ls = REAL[side]
    out = [(t, tuple(v * k for v in b), c, a) for t, b, c, a in ls]
    W2, H2 = int(round(W * k)), int(round(H * k))
    return textmodel.parse(fta_from_lines(out, W2, H2), W2, H2)["lines"], (W2, H2)


REAL = load_real(DATA, with_angle=True)


@pytest.mark.parametrize("ka,kb", [(1.0, 1.0), (1.83, 2.21), (2.4, 1.6), (3.0, 3.0)])
def test_compare_is_invariant_to_render_dpi(ka, kb):
    """โหมดคมสูงสุดให้ A/B คนละ dpi — ผลเทียบ (ชนิด/ข้อความ/ความรุนแรง/โค้ง/เศษ) ต้องเท่าเดิม"""
    def run(ka, kb):
        la, sa = _scaled("A", ka)
        lb, sb = _scaled("B", kb)
        r = compare.compare(la, lb, sa, sb)
        return (sorted((f["class"], f["a"]["frag"], f["b"]["frag"], f["severity"],
                        bool(f.get("curved"))) for f in r["findings"]),
                sorted((f["class"], f["a"]["frag"], f["b"]["frag"]) for f in r["debris"]),
                round(r["coverage"], 4))
    assert run(ka, kb) == run(1.0, 1.0)
