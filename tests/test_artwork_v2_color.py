"""Artwork V2 — เลือกสีของภาพที่ส่งให้ Vision: สี / เทา / ขาวดำ (7 ต.ค. 2026 · ผู้ใช้สั่ง A/B)

กติกาที่ล็อก:
* ``color`` (ค่าเริ่มต้น) = ภาพเดิม **ทุกไบต์** (sha1 เท่าเส้นทางก่อนมีตัวเลือก)
* ``gray``/``bw`` ⇒ JPEG ช่องเดียว · ``bw`` เป็นสองค่าก่อนเข้ารหัส
* อ่านซ้ำใช้สีเดียวกับรอบหลัก · "คมสูงสุด" + เทา/ขาวดำ ⇒ 400 dpi (งบคิดจากภาพสี)
* โหมดที่ใช้อยู่ใน result / Log / ค่าตั้ง · ค่าแปลก = ค่าของเครื่อง
"""

from __future__ import annotations

import io
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from artwork_v2 import config, imaging, jobs, keystore, pipeline, vision_client  # noqa: E402

fitz = pytest.importorskip("fitz")
from PIL import Image  # noqa: E402

from test_artwork_v2_sharp import (KEY, LONG_A, LONG_B, PAIRS, SA, SB, ZONE,  # noqa: E402,F401
                                   _fake, _isolated, _job)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sent(jid, run, name="p1_a.jpg"):
    with open(os.path.join(jobs.run_dir(jid, run), "img", name), "rb") as fh:
        return fh.read()


def test_default_is_color():
    assert config.COLOR_MODE == "color"
    assert config.COLOR_MODES == ("color", "gray", "bw")


def test_unknown_mode_falls_back_to_station_default(monkeypatch):
    monkeypatch.setattr(config, "COLOR_MODE", "color")
    assert pipeline.norm_color(None) == "color"
    assert pipeline.norm_color("purple") == "color"
    assert pipeline.norm_color(" BW ") == "bw"
    monkeypatch.setattr(config, "COLOR_MODE", "gray")
    assert pipeline.norm_color(None) == "gray"


def test_to_color_mode_color_returns_same_object():
    img = np.zeros((40, 60, 3), np.uint8)
    out, info = imaging.to_color_mode(img, "color", 400)
    assert out is img and info == {"color_mode": "color"}


def test_bw_is_binary_and_block_follows_dpi():
    rng = np.random.default_rng(0)
    img = (rng.random((300, 400, 3)) * 255).astype(np.uint8)
    out, info = imaging.to_color_mode(img, "bw", 400)
    assert set(np.unique(out)) <= {0, 255}
    assert (out[:, :, 0] == out[:, :, 1]).all() and (out[:, :, 1] == out[:, :, 2]).all()
    assert info["bw_block_px"] % 2 == 1
    assert info["bw_block_px"] == 47              # 3.0 mm ที่ 400 dpi
    _, i2 = imaging.to_color_mode(img, "bw", 1200)
    assert i2["bw_block_px"] > info["bw_block_px"]
    _, i3 = imaging.to_color_mode(img, "bw", None)   # ภาพถ่าย: 1/30 ด้านสั้น ขั้นต่ำ 15
    assert i3["bw_block_px"] == 15
    assert 0.0 <= info["ink_frac"] <= 1.0


def test_gray_keeps_luminance():
    img = np.zeros((10, 10, 3), np.uint8)
    img[:, :, 2] = 200                            # แดงล้วน (BGR)
    out, info = imaging.to_color_mode(img, "gray", 400)
    assert info == {"color_mode": "gray"}
    assert 55 <= int(out[0, 0, 0]) <= 65          # 0.299 × 200


def test_mono_jpeg_is_single_channel():
    img = np.full((50, 80, 3), 128, np.uint8)
    assert Image.open(io.BytesIO(imaging.encode_jpeg(img, 90, mono=True))).mode == "L"
    assert Image.open(io.BytesIO(imaging.encode_jpeg(img, 90))).mode == "RGB"


def test_color_mode_is_byte_identical_to_the_old_path(tmp_path, monkeypatch):
    jid = _job(tmp_path)
    keystore.save(KEY)
    monkeypatch.setattr(vision_client, "annotate", _fake(SA, SB))
    r = pipeline.run(jid, PAIRS, sharpness="standard", color_mode="color")
    rn = pipeline.run(jid, PAIRS, sharpness="standard")
    for s in "ab":
        img, _ = jobs.source(jid, s).render_zone(0, ZONE)
        jpeg, _, _ = imaging.fit_jpeg(img, config.MAX_IMAGE_BYTES)
        want = imaging.sha1_bytes(jpeg)[:12]
        assert r["pairs"][0]["sides"][s]["sha1"] == want
        assert rn["pairs"][0]["sides"][s]["sha1"] == want
        assert "color_mode" not in r["pairs"][0]["sides"][s]["render"]
    assert r["color_mode"] == rn["color_mode"] == "color"
    assert "color_mode=color" in r["log_text"] and "     color: mode=" not in r["log_text"]


@pytest.mark.parametrize("mode", ["gray", "bw"])
def test_mono_modes_send_single_channel_and_log(tmp_path, monkeypatch, mode):
    jid = _job(tmp_path)
    keystore.save(KEY)
    monkeypatch.setattr(vision_client, "annotate", _fake(SA, SB))
    r = pipeline.run(jid, PAIRS, sharpness="standard", color_mode=mode)
    assert r["color_mode"] == mode
    for s in "ab":
        sd = r["pairs"][0]["sides"][s]
        assert sd["render"]["color_mode"] == mode
        im = Image.open(io.BytesIO(_sent(jid, r["run"], "p1_%s.jpg" % s)))
        assert im.mode == "L"
    assert ("color_mode=%s" % mode) in r["log_text"]
    assert ("     color: mode=%s" % mode) in r["log_text"]
    if mode == "bw":
        assert "block=47px" in r["log_text"]
    # ผลเทียบยังจับตัวเลขที่ต่างได้ (ชั้นเทียบไม่รู้จักสี)
    assert [f["class"] for f in r["pairs"][0]["findings"]] == ["NUMBER"]


def test_sharp_max_with_mono_falls_back_to_standard_dpi(tmp_path, monkeypatch):
    jid = _job(tmp_path)
    keystore.save(KEY)
    monkeypatch.setattr(vision_client, "annotate", _fake(SA, SB))
    r = pipeline.run(jid, PAIRS, sharpness="max", color_mode="gray")
    for s in "ab":
        rd = r["pairs"][0]["sides"][s]["render"]
        assert rd["sharpness"] == "max→standard(mono)"
        assert abs(rd["dpi"] - config.PDF_ZONE_DPI) < 1.0


@pytest.mark.parametrize("mode", ["color", "bw"])
def test_reread_uses_same_color_as_main(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(config, "REREAD_ENABLED", True)
    jid = _job(tmp_path)
    keystore.save(KEY)
    monkeypatch.setattr(vision_client, "annotate", _fake(LONG_A, LONG_B))
    r = pipeline.run(jid, PAIRS, sharpness="standard", color_mode=mode)
    it = r["reread"]["items"][0]
    for s in "ab":
        im = Image.open(io.BytesIO(_sent(jid, r["run"], it["crops"][s]["image"])))
        assert im.mode == ("RGB" if mode == "color" else "L")
        if mode == "bw":
            vals = set(np.unique(np.asarray(im)))
            # JPEG ทำให้ขอบเพี้ยนเล็กน้อย — ส่วนใหญ่ยังเป็นดำ/ขาว
            arr = np.asarray(im)
            assert ((arr < 40) | (arr > 215)).mean() > 0.95, len(vals)


def test_settings_snapshot_has_color_keys():
    snap = pipeline.settings_snapshot()
    for k in ("COLOR_MODE", "BW_BLOCK_MM", "BW_C"):
        assert k in snap


def test_route_passes_color_and_page_has_selector(monkeypatch):
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

    def fake_run(job_id, pairs, **kw):
        got.update(kw)
        return {"run": "run_001", "verdict": "PASS", "stage": {}}
    monkeypatch.setattr(pipeline, "run", fake_run)
    html = c.get("/artwork_v2").get_data(as_text=True)
    assert 'id="v2Color"' in html and '<option value="color" selected>' in html
    for v in ("gray", "bw"):
        assert '<option value="%s">' % v in html
    from test_artwork_v2_sharp import _tiny_pdf
    jid = jobs.create(("a.pdf", _tiny_pdf()), ("b.pdf", _tiny_pdf()))["id"]
    r = c.post("/api/artwork_v2/jobs/%s/run" % jid, json={"pairs": [], "color_mode": "bw"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert got.get("color_mode") == "bw"
    c.post("/api/artwork_v2/jobs/%s/run" % jid, json={"pairs": []})
    assert got.get("color_mode") is None
    js = open(os.path.join(ROOT, "static", "js", "artwork_v2.js"), encoding="utf-8").read()
    assert 'color_mode: $("v2Color")' in js
