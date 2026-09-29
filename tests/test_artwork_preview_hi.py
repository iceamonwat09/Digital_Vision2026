"""ภาพคมตอนวาดโซน (29 ก.ย. 2026) — ``preview_hi.png`` ซ้อนทับ ``preview.png``.

ล็อก 5 เรื่อง:
  1. แสดงผลล้วน — ``preview.png`` (ที่ตัวเสนอโซน/snap/autopair/overlay อ่าน)
     ไม่ถูกแตะแม้แต่ไบต์เดียว และ ``preview_size`` ที่ส่งให้หน้าเว็บเท่าเดิม
  2. PDF เท่านั้น — ไฟล์ภาพคมเต็มความละเอียดอยู่แล้ว ⇒ ไม่สร้าง (404)
  3. เพดานพิกเซล — แผ่นพิมพ์ใหญ่ลด dpi ลง · ลดแล้วไม่คมกว่าเดิม = ไม่สร้าง
  4. แคช — สร้างครั้งเดียว · แนบ 🅱 ใหม่ต้องทิ้งภาพคมของ 🅱 ตัวเก่า
  5. ปิดธง = ไม่มีภาพคม · หน้าเว็บไม่มี overlay = เหมือนเดิมเป๊ะ
"""

import hashlib
import os
import re

import cv2
import fitz
import numpy as np
import pytest
from flask import Flask

from artwork_check import config, pipeline, report
from artwork_check.routes import artwork_bp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = open(os.path.join(ROOT, "static", "js", "artwork_check.js"), encoding="utf-8").read()
HTML = open(os.path.join(ROOT, "templates", "artwork_check.html"), encoding="utf-8").read()


@pytest.fixture
def store(tmp_path, monkeypatch):
    d = tmp_path / "inspections"
    d.mkdir()
    monkeypatch.setattr(config, "INSPECTIONS_DIR", str(d))
    monkeypatch.setattr(config, "HISTORY_PER_USER", False)
    monkeypatch.setattr(config, "PREVIEW_DPI", 150)
    monkeypatch.setattr(config, "PREVIEW_DISPLAY_DPI", 300)
    monkeypatch.setattr(config, "PREVIEW_DISPLAY_MAX_MP", 24.0)
    return d


def _pdf(w=400, h=300, text="NET WEIGHT 170 g"):
    d = fitz.open()
    p = d.new_page(width=w, height=h)
    p.insert_text((30, 80), text, fontsize=14)
    return d.tobytes()


def _png():
    img = np.full((300, 400, 3), 255, np.uint8)
    cv2.putText(img, "NET 170 g", (30, 150), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
    return cv2.imencode(".png", img)[1].tobytes()


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def test_defaults():
    assert config.PREVIEW_DISPLAY_DPI == 300
    assert config.PREVIEW_DISPLAY_MAX_MP == 24.0


# ── ① แสดงผลล้วน ─────────────────────────────────────────────────────

def test_hi_preview_is_sharper_and_leaves_preview_png_alone(store):
    info = pipeline.start_inspection(_pdf(), "a.pdf")
    rid = info["id"]
    d = report.inspection_dir(rid)
    low = os.path.join(d, "preview.png")
    before = _sha(low)
    path = pipeline.display_preview_path(rid, "a")
    assert path and os.path.basename(path) == "preview_hi.png"
    hi = cv2.imread(path)
    w, h = info["preview_size"]
    # 300 / 150 dpi · MuPDF ปัดขอบคนละทาง ⇒ คลาดได้ 1 px (ภาพคมถูกยืดเต็มกรอบเดิมอยู่แล้ว)
    assert abs(hi.shape[1] - w * 2) <= 1 and abs(hi.shape[0] - h * 2) <= 1
    assert _sha(low) == before                               # ห้ามแตะของเดิม


def test_upload_response_is_unchanged_by_the_flag(store, monkeypatch):
    on = pipeline.start_inspection(_pdf(), "a.pdf")
    monkeypatch.setattr(config, "PREVIEW_DISPLAY_DPI", 0)
    off = pipeline.start_inspection(_pdf(), "a.pdf")
    assert on["preview_size"] == off["preview_size"]
    # ไม่สร้างตอนอัปโหลด (ไม่ทำให้อัปโหลดช้าลง) — สร้างตอนถูกขอครั้งแรก
    assert not os.path.exists(os.path.join(report.inspection_dir(on["id"]), "preview_hi.png"))


# ── ② PDF เท่านั้น ───────────────────────────────────────────────────

def test_raster_files_get_no_hi_preview(store):
    rid = pipeline.start_inspection(_png(), "a.png")["id"]
    assert pipeline.display_preview_path(rid, "a") is None


def test_missing_reference_file_is_not_an_error(store):
    rid = pipeline.start_inspection(_pdf(), "a.pdf")["id"]
    assert pipeline.display_preview_path(rid, "b") is None
    assert pipeline.display_preview_path(rid, "x") is None


# ── ③ เพดานพิกเซล ────────────────────────────────────────────────────

def test_dpi_cap_for_large_sheets(store):
    # A4 ที่ 300 dpi ≈ 8.7 MP ⇒ ไม่ถูกลด
    assert pipeline.display_dpi(842, 595) == 300
    # แผ่น 757×455 mm (2148×1289 pt) ที่ 300 dpi ≈ 48 MP ⇒ ลดลงให้อยู่ใน 24 MP
    dpi = pipeline.display_dpi(2148, 1289)
    assert 150 < dpi < 300
    assert (2148 * dpi / 72) * (1289 * dpi / 72) <= 24e6 * 1.001


def test_no_hi_preview_when_the_cap_leaves_nothing_sharper(store, monkeypatch):
    monkeypatch.setattr(config, "PREVIEW_DISPLAY_MAX_MP", 0.5)
    assert pipeline.display_dpi(842, 595) == 0


def test_flag_off_means_no_hi_preview(store, monkeypatch):
    rid = pipeline.start_inspection(_pdf(), "a.pdf")["id"]
    monkeypatch.setattr(config, "PREVIEW_DISPLAY_DPI", 0)
    assert pipeline.display_dpi(842, 595) == 0
    assert pipeline.display_preview_path(rid, "a") is None
    monkeypatch.setattr(config, "PREVIEW_DISPLAY_DPI", 150)    # ≤ PREVIEW_DPI
    assert pipeline.display_preview_path(rid, "a") is None


# ── ④ แคช ────────────────────────────────────────────────────────────

def test_rendered_once_then_cached(store, monkeypatch):
    rid = pipeline.start_inspection(_pdf(), "a.pdf")["id"]
    first = pipeline.display_preview_path(rid, "a")
    calls = []
    orig = pipeline.ArtworkDocument.render
    monkeypatch.setattr(pipeline.ArtworkDocument, "render",
                        lambda self, dpi: calls.append(dpi) or orig(self, dpi))
    assert pipeline.display_preview_path(rid, "a") == first
    assert calls == []


def test_new_reference_file_drops_the_old_hi_preview(store):
    rid = pipeline.start_inspection(_pdf(), "a.pdf")["id"]
    pipeline.start_ref(rid, _pdf(text="OLD LOT"), "b.pdf")
    old = cv2.imread(pipeline.display_preview_path(rid, "b"))
    pipeline.start_ref(rid, _pdf(w=600, h=300, text="NEW LOT"), "b2.pdf")
    assert not os.path.exists(os.path.join(report.inspection_dir(rid), "preview_b_hi.png"))
    new = cv2.imread(pipeline.display_preview_path(rid, "b"))
    assert new.shape != old.shape


# ── route ────────────────────────────────────────────────────────────

@pytest.fixture
def client(store):
    app = Flask(__name__)
    app.register_blueprint(artwork_bp)
    return app.test_client()


def test_route_serves_png_for_pdf_and_404_otherwise(client):
    rid = pipeline.start_inspection(_pdf(), "a.pdf")["id"]
    r = client.get(f"/api/artwork/{rid}/preview_hi.png?doc=a")
    assert r.status_code == 200 and r.data[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get(f"/api/artwork/{rid}/preview_hi.png?doc=b").status_code == 404
    rid2 = pipeline.start_inspection(_png(), "a.png")["id"]
    assert client.get(f"/api/artwork/{rid2}/preview_hi.png").status_code == 404


def test_route_404_when_flag_off(client, monkeypatch):
    rid = pipeline.start_inspection(_pdf(), "a.pdf")["id"]
    monkeypatch.setattr(config, "PREVIEW_DISPLAY_DPI", 0)
    assert client.get(f"/api/artwork/{rid}/preview_hi.png").status_code == 404


# ── ⑤ หน้าเว็บ ───────────────────────────────────────────────────────

def test_overlay_never_takes_the_mouse_and_stays_hidden_until_loaded():
    rule = re.search(r"\.aw-stage img\.aw-hi \{([^}]*)\}", HTML).group(1)
    assert "pointer-events:none" in rule
    assert "visibility:hidden" in rule
    assert "width:100%" in rule and "height:100%" in rule
    assert ".aw-stage img.aw-hi.ready { visibility:visible; }" in HTML


def test_flag_is_passed_to_the_page():
    assert "window.AW_PREVIEW_HI = {{ 'true' if preview_hi else 'false' }};" in HTML


def test_js_only_upgrades_pdf_previews_and_follows_every_src_change():
    body = JS[JS.index("function hiUrlFor("):JS.index("function syncHi(")]
    assert "window.AW_PREVIEW_HI !== true" in body
    assert "docMeta[doc].pdf === false" in body
    sync = JS[JS.index("function syncHi("):JS.index("new MutationObserver")]
    # ภาพคมของไฟล์เก่าต้องไม่ค้างทับไฟล์ใหม่ระหว่างรอโหลด
    assert 'hi.classList.remove("ready");' in sync
    assert 'attributeFilter: ["src"]' in JS


def test_zone_geometry_never_reads_the_hi_image():
    """พิกัดโซนต้องคิดจาก previewImg / docMeta เท่านั้น — ถ้าวันหนึ่งมีโค้ดอ่าน
    naturalWidth ของภาพคม โซนจะเพี้ยน 2 เท่าแบบเงียบ ๆ"""
    assert not re.search(r"\bhi\.(naturalWidth|naturalHeight|clientWidth|getBoundingClientRect)", JS)
    assert not re.search(r"\.aw-hi[\"']\)\.(naturalWidth|getBoundingClientRect)", JS)
