# -*- coding: utf-8 -*-
"""ภาพในรายงานต้องอยู่แนวเดียวกับที่ผู้ใช้เพิ่งจัดมา (11 ก.ย. 2026).

ที่มา (ผู้ใช้): *"จากการที่เราแก้ไขเรื่องการปรับรูปให้หมุนได้ สิ่งที่ควร
เกิดขึ้นลำดับถัดไปคือ การแสดงผลรูปที่วาด Zone ในการตรวจก็ควรจะเป็นทิศทาง
เดียวกัน เพื่อให้ตรวจสอบง่าย"*

ปุ่ม **"↻ หมุนจอ"** (8 ก.ย.) ทำให้ลากโซนบนฉลากที่ตัวหนังสือตั้งทั้งใบได้
แล้ว แต่พอกดส่งตรวจ รายงานเหวี่ยงกลับไปแนวเดิม ⇒ ผู้ตรวจต้องเอียงคอดูเอง

⚠️ **แสดงผลล้วน** — หมุนตอน *เสิร์ฟ* ภาพเท่านั้น:
* ไฟล์ ``preview.png``/``overlay.png`` บนดิสก์ **ไม่ถูกแตะ** ⇒ ชั้นที่อ่าน
  ไฟล์นี้ต่อ (``propose_zones`` / ``snap_bbox`` / ``autopair``) เห็นของเดิม
* พิกัดโซนที่เก็บยังเป็นของหน้าที่ **ยังไม่หมุน** เสมอ
* ``defects`` / ``verdict`` / การนับ ไม่เกี่ยวข้องเลย
"""
import json
import os

import numpy as np
import pytest
from flask import Flask, g

from artwork_check import config, report
from artwork_check.routes import artwork_bp

cv2 = pytest.importorskip("cv2")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js", "artwork_check.js")
TPL = os.path.join(ROOT, "templates", "artwork_check.html")

# ภาพทดสอบเป็น "แถบยาวแนวนอน" ⇒ หมุน 90° แล้วรูปร่างต้องสลับ และมุมที่
# ทาสีไว้ต้องย้ายไปอยู่มุมที่ถูกต้อง (จับได้แม้หมุนผิดทาง)
W, H = 40, 12


def _mark() -> np.ndarray:
    img = np.zeros((H, W, 3), np.uint8)
    img[:, :] = (20, 20, 20)
    img[0:3, 0:3] = (0, 0, 255)          # มุมซ้ายบน = แดง
    return img


@pytest.fixture
def store(tmp_path, monkeypatch):
    d = tmp_path / "inspections"
    d.mkdir()
    monkeypatch.setattr(config, "INSPECTIONS_DIR", str(d))
    monkeypatch.setattr(config, "HISTORY_PER_USER", False)
    return d


def _make(store, rec_id="20260911-090000-aaaaaa", page_rot=0):
    rec = store / rec_id
    rec.mkdir()
    (rec / "report.json").write_text(json.dumps({
        "id": rec_id, "created_at": "2026-09-11 09:00:00",
        "filename": "a.pdf", "verdict": "PASS", "defects": [],
        "page_rot": page_rot,
    }), encoding="utf-8")
    for name in ("preview.png", "overlay.png", "preview_b.png", "overlay_b.png"):
        cv2.imwrite(str(rec / name), _mark())
    return rec_id


@pytest.fixture
def app(store):
    app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"))
    app.register_blueprint(artwork_bp)

    @app.before_request
    def _no_auth():
        g.auth_enabled = False
        g.current_user = None

    return app


def _png(resp) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(resp.data, np.uint8), cv2.IMREAD_COLOR)


def _is_the_file_itself(resp) -> bool:
    """แยก "ส่งไฟล์บนดิสก์ตรง ๆ" ออกจาก "ถอดรหัสแล้วเข้ารหัสใหม่".

    ⚠️ เทียบไบต์อย่างเดียว **ไม่พอ** — วัดแล้ว: PNG ที่ decode แล้ว encode
    ใหม่ด้วยพารามิเตอร์เดิมได้ไบต์เท่ากันเป๊ะ ⇒ เทสต์จะเขียวทั้งที่ทาง
    เดิมถูกเปลี่ยนไปแล้ว. ตัวที่แยกได้จริงคือส่วนหัวที่มีเฉพาะเส้นทาง
    ``send_from_directory`` (Last-Modified / ETag ของไฟล์จริง)
    """
    return "Last-Modified" in resp.headers and "ETag" in resp.headers


# ── ① ไม่ส่ง rot = ไฟล์เดิมทุกไบต์ ───────────────────────────────────
#
# นี่คือด่านของกฎเหล็กข้อ 1: ทางเดิมต้องไม่ถูกแตะแม้แต่ไบต์เดียว
# (ต้องเทียบ **ไบต์** ไม่ใช่พิกเซล — การ decode/encode ใหม่ให้พิกเซลเท่ากัน
#  ได้ แต่ไฟล์ต่างกัน = ไม่ใช่ "เส้นทางเดิม" อีกต่อไป)

@pytest.mark.parametrize("name", ["preview.png", "overlay.png",
                                  "preview_b.png", "overlay_b.png"])
def test_without_rot_the_file_on_disk_is_served_byte_for_byte(app, store, name):
    rec = _make(store)
    raw = (store / rec / name).read_bytes()
    r = app.test_client().get(f"/api/artwork/{rec}/{name}")
    assert r.status_code == 200
    assert r.data == raw
    assert _is_the_file_itself(r)


@pytest.mark.parametrize("bad", ["0", "45", "abc", "-90", "360", ""])
def test_a_rot_value_we_do_not_support_falls_back_to_the_plain_file(
        app, store, bad):
    rec = _make(store)
    raw = (store / rec / "preview.png").read_bytes()
    r = app.test_client().get(f"/api/artwork/{rec}/preview.png?rot={bad}")
    assert r.data == raw
    # ห้ามแม้แต่ถอดรหัสภาพแล้วเข้ารหัสใหม่ — มุมที่เราไม่รองรับต้องเดิน
    # เส้นทางเดิมทั้งเส้น (ถูกกว่า และไม่มีทางเพี้ยนจากการ re-encode)
    assert _is_the_file_itself(r)


# ── ② หมุนจริง และหมุนถูกทาง ─────────────────────────────────────────

@pytest.mark.parametrize("name", ["preview.png", "overlay.png",
                                  "preview_b.png", "overlay_b.png"])
def test_every_page_image_route_can_rotate(app, store, name):
    rec = _make(store)
    r = app.test_client().get(f"/api/artwork/{rec}/{name}?rot=90")
    assert not _is_the_file_itself(r)     # ไม่ใช่ไฟล์บนดิสก์อีกแล้ว
    assert _png(r).shape[:2] == (W, H)    # สลับด้าน = หมุนจริง


def test_rotation_is_clockwise_like_the_screen_button(app, store):
    """ทิศต้องตรงกับ ``transform: rotate(90deg)`` ของจอ ไม่งั้นภาพในรายงาน
    จะกลับหัวเมื่อเทียบกับสิ่งที่ผู้ใช้เห็นตอนลากโซน."""
    rec = _make(store)
    img = _png(app.test_client().get(f"/api/artwork/{rec}/preview.png?rot=90"))
    # มุมซ้ายบน (แดง) หมุนตามเข็ม 90° ⇒ ไปอยู่มุม **ขวาบน**
    assert tuple(img[1, -2]) == (0, 0, 255)
    assert tuple(img[1, 1]) != (0, 0, 255)


@pytest.mark.parametrize("ang", [90, 180, 270])
def test_the_served_bytes_match_apply_rotation_exactly(app, store, ang):
    """ใช้ตัวหมุนตัวเดียวกับที่ชั้น OCR/pixel ใช้ — ไม่ใช่สูตรของตัวเอง."""
    from artwork_check.pdf_ingest import apply_rotation
    rec = _make(store)
    got = _png(app.test_client().get(f"/api/artwork/{rec}/preview.png?rot={ang}"))
    want = apply_rotation(cv2.imread(str(store / rec / "preview.png")), ang)
    assert np.array_equal(got, want)


def test_rotating_never_touches_the_file_on_disk(app, store):
    """ชั้นที่อ่าน preview.png ต่อ (propose/snap/autopair) ต้องเห็นของเดิม."""
    rec = _make(store)
    before = (store / rec / "preview.png").read_bytes()
    for ang in (90, 180, 270):
        app.test_client().get(f"/api/artwork/{rec}/preview.png?rot={ang}")
    assert (store / rec / "preview.png").read_bytes() == before


# ── ③ ปิดธงแล้วต้องได้พฤติกรรมเดิม 100% ──────────────────────────────

def test_the_flag_turns_the_whole_thing_off(app, store, monkeypatch):
    monkeypatch.setattr(config, "REPORT_VIEW_ROTATE", False)
    rec = _make(store)
    raw = (store / rec / "preview.png").read_bytes()
    r = app.test_client().get(f"/api/artwork/{rec}/preview.png?rot=90")
    assert r.data == raw
    assert _is_the_file_itself(r)


def test_the_flag_defaults_to_on(monkeypatch):
    monkeypatch.delenv("ARTWORK_REPORT_VIEW_ROTATE", raising=False)
    assert config.REPORT_VIEW_ROTATE is True


# ── ④ มุมต้องเดินทางจาก "ปุ่มหมุนจอ" ไปถึงรายงาน ────────────────────

def _inspect(tmp_path, monkeypatch, **kw):
    """รัน ``run_inspection`` ตัวจริงแบบเบาที่สุด (แพทเทิร์นเดียวกับ
    ``test_run_inspection_writes_applied_rotation``) แล้วดูรายงานที่ได้."""
    from artwork_check import pipeline
    from artwork_check import report as rep_mod
    monkeypatch.setattr(rep_mod.config, "INSPECTIONS_DIR", str(tmp_path))
    rec = "20260911-000000-aa11bb"
    d = rep_mod.inspection_dir(rec, create=True)
    with open(os.path.join(d, "source.png"), "wb") as f:
        f.write(b"x")
    cv2.imwrite(os.path.join(d, "preview.png"),
                np.full((50, 80, 3), 255, np.uint8))
    monkeypatch.setattr(pipeline, "ArtworkDocument", lambda *a, **k: object())
    zone_rotate = kw.pop("zone_rotate", "default")
    ocr_rotate = kw.pop("ocr_rotate", 0)
    monkeypatch.setattr(
        pipeline.ocr, "read_all_zones",
        lambda doc, zones, page_auto=False, force_ocr=False,
               split_bands=False, font_trust=None: [
            {"zone_id": z["id"], "text": "T", "engine": "stub",
             "conf": None, "rotate": ocr_rotate}
            # ตัวจริงข้ามโซน ``ignore`` (ocr.read_all_zones) — ของปลอมต้อง
            # ข้ามด้วย ไม่งั้นเทสต์จะไม่เคยเห็นโซนที่ ``rotate`` ไม่ใช่ตัวเลข
            for z in zones if z.get("type") != "ignore"])
    extra = kw.pop("extra_zones", [])
    return pipeline.run_inspection(
        rec,
        [{"id": "z1", "type": "panel", "group": "",
          "bbox": [0.1, 0.1, 0.2, 0.2], "rotate": zone_rotate}] + extra, **kw)


@pytest.mark.parametrize("ang", [90, 180, 270])
def test_run_inspection_records_the_screen_angle(tmp_path, monkeypatch, ang):
    assert _inspect(tmp_path, monkeypatch, page_rot=ang)["page_rot"] == ang


@pytest.mark.parametrize("bad", [45, -90, "90", None, True, 360, 0])
def test_anything_that_is_not_a_right_angle_becomes_no_rotation(
        tmp_path, monkeypatch, bad):
    """ค่าที่ไม่รู้จักต้องกลายเป็น 0 (= ไม่หมุน) **ไม่ใช่ทำให้การตรวจล้ม** —
    มุมนี้มาจาก body ของคำขอ ซึ่งเราคุมไม่ได้."""
    assert _inspect(tmp_path, monkeypatch, page_rot=bad)["page_rot"] == 0


def test_not_passing_an_angle_at_all_is_the_old_behaviour(tmp_path, monkeypatch):
    """สคริปต์/เทสต์เดิมที่ไม่ส่งมุมมาต้องได้ 0 เสมอ."""
    assert _inspect(tmp_path, monkeypatch)["page_rot"] == 0


def test_the_angle_never_touches_the_zone_coordinates(tmp_path, monkeypatch):
    """หมุนจอแล้วพิกัดโซนที่บันทึกต้องเท่าเดิมทุกหลัก (กฎเหล็กข้อ 1)."""
    a = _inspect(tmp_path, monkeypatch, page_rot=0)["zones"][0]
    b = _inspect(tmp_path, monkeypatch, page_rot=90)["zones"][0]
    assert a["bbox"] == b["bbox"] == [0.1, 0.1, 0.2, 0.2]
    assert a["rotate"] == b["rotate"]


def test_the_angle_never_changes_the_verdict_or_the_defects(
        tmp_path, monkeypatch):
    a = _inspect(tmp_path, monkeypatch, page_rot=0)
    b = _inspect(tmp_path, monkeypatch, page_rot=270)
    assert a["verdict"] == b["verdict"]
    assert a["defects"] == b["defects"]
    assert a["summary"] == b["summary"]


def test_the_inspect_route_reads_page_rot_from_the_body():
    import inspect

    from artwork_check import routes
    src = inspect.getsource(routes.api_inspect)
    assert 'body.get("page_rot")' in src
    assert "page_rot=page_rot" in src


# ── ⑤ ฝั่งหน้าเว็บ ───────────────────────────────────────────────────

def _js() -> str:
    with open(JS, encoding="utf-8") as f:
        return f.read()


def test_the_browser_sends_the_screen_angle_when_inspecting():
    assert "page_rot: pageRot" in _js()


def test_the_report_asks_for_rotated_page_images():
    js = _js()
    assert '"&rot=" + pr' in js
    for name in ("preview.png", "overlay.png", "preview_b.png", "overlay_b.png"):
        line = [l for l in js.splitlines()
                if "/" + name + '?t=" + ts' in l]
        assert line, name
        assert all("rotQ" in l for l in line), name


def test_an_old_report_without_the_key_is_not_rotated():
    """รายงานเก่าไม่มี ``page_rot`` ⇒ ต้องไม่ใส่ ``&rot=`` (ไม่เดา)."""
    js = _js()
    i = js.index("const rotQ =")
    frag = js[i:i + 200]
    assert "pr === 90 || pr === 180 || pr === 270" in frag
    assert '""' in frag


# ── ⑥ ภาพตัวอย่าง "ที่ OCR จะเห็น" ย้ายมาอยู่ใต้กล่องภาพ ─────────────
#
# ผู้ใช้: *"awRotPreview อยากให้ขึ้นไปใต้ awStageBox จะได้ดูการหมุนสะดวก"*

def _tpl() -> str:
    with open(TPL, encoding="utf-8") as f:
        return f.read()


def test_rot_preview_sits_between_the_stage_box_and_the_button_row():
    tpl = _tpl()
    i_stage = tpl.index('id="awStageBox"')
    i_rot = tpl.index('id="awRotPreview"')
    i_add = tpl.index('id="awAddZone"')
    assert i_stage < i_rot < i_add


def test_rot_preview_is_no_longer_inside_the_properties_panel():
    """ถ้ายังอยู่ใน ``#awProps`` มันจะถูกซ่อนตามแผงนั้นไปด้วย."""
    tpl = _tpl()
    i_props = tpl.index('id="awProps"')
    assert tpl.index('id="awRotPreview"') < i_props


def test_rot_preview_still_hides_itself_when_no_zone_is_selected():
    """ย้ายออกนอก ``#awProps`` ได้ก็เพราะมันคุมการแสดงผลของตัวเองอยู่แล้ว."""
    js = _js()
    i = js.index("function updateRotPreview()")
    frag = js[i:i + 400]
    assert 'if (!z || !inspectionId) { box.style.display = "none"; return; }' \
        in frag


def test_the_thumbnail_and_its_caption_moved_together():
    tpl = _tpl()
    i_rot = tpl.index('id="awRotPreview"')
    frag = tpl[i_rot:i_rot + 400]
    assert 'id="awRotState"' in frag
    assert 'id="awRotThumb"' in frag


# ── ⑦ การ์ด defect หมุนตาม ``rotate`` ของโซนอยู่แล้ว (ล็อกไว้กันหาย) ──
#
# ผู้ใช้ขอข้อนี้เป็นข้อ 2 — ตรวจแล้วว่ามีมาตั้งแต่ commit โหมดหมุนระดับโซน
# เทสต์นี้จึงเป็น **ตาข่ายกันถอยหลัง** ไม่ใช่ของใหม่

def test_a_pinned_angle_survives_a_text_layer_read(tmp_path, monkeypatch):
    """เคสที่พังจริงบนสถานี: ไฟล์ PDF ที่มี text layer อ่านโดย **ไม่หมุนภาพ**
    ⇒ ``read_zone`` คืน ``rotate: 0`` ⇒ เดิมทับมุมที่ผู้ใช้ปักหมุดทิ้ง
    ⇒ การ์ดไม่เคยหมุนตามเลย (= ข้อ 2 ที่ผู้ใช้ร้องขอ)."""
    rep = _inspect(tmp_path, monkeypatch, zone_rotate=90, ocr_rotate=0)
    z = rep["zones"][0]
    assert z["rotate"] == 0          # ความจริงของชั้น OCR — ห้ามแก้
    assert z["view_rot"] == 90       # มุมสำหรับแสดงผล — ของที่ขาดไป


@pytest.mark.parametrize("ang", [90, 180, 270])
def test_every_pinned_angle_reaches_the_report(tmp_path, monkeypatch, ang):
    rep = _inspect(tmp_path, monkeypatch, zone_rotate=ang, ocr_rotate=0)
    assert rep["zones"][0]["view_rot"] == ang


def test_an_auto_zone_shows_the_angle_ocr_actually_used(tmp_path, monkeypatch):
    """โซนที่ไม่ได้ปักหมุด (auto/ตามหน้า) ⇒ ใช้มุมที่ OCR หมุนจริง
    (พฤติกรรมเดิมของการ์ด ไม่ได้เปลี่ยน)."""
    rep = _inspect(tmp_path, monkeypatch, zone_rotate="auto", ocr_rotate=270)
    assert rep["zones"][0]["view_rot"] == 270


def test_a_zone_with_no_rotation_gets_no_display_angle(tmp_path, monkeypatch):
    """ไม่มีอะไรให้หมุน ⇒ ไม่ใส่คีย์เลย ⇒ URL ของการ์ดเท่าเดิมทุกตัวอักษร."""
    rep = _inspect(tmp_path, monkeypatch, zone_rotate="default", ocr_rotate=0)
    assert "view_rot" not in rep["zones"][0]


def test_the_display_angle_never_rewrites_what_ocr_reported(
        tmp_path, monkeypatch):
    """``rotate`` ถูกใช้โดยชั้น pixel ตัดสินใจหมุนก่อนทาบภาพ + เส้นความ
    คืบหน้า ⇒ ต้องเป็นค่าที่ OCR ทำจริงเสมอ ห้ามถูกมุมแสดงผลทับ."""
    rep = _inspect(tmp_path, monkeypatch, zone_rotate=90, ocr_rotate=180)
    assert rep["zones"][0]["rotate"] == 180
    assert rep["ocr"][0]["rotate"] == 180


def test_a_zone_ocr_never_read_does_not_get_a_bogus_display_angle(
        tmp_path, monkeypatch):
    """โซน ``ignore`` ไม่ถูก OCR เลย ⇒ ``rotate`` ยังเป็นคำที่ผู้ใช้ตั้งไว้
    (``"auto"``/``"default"``) ไม่ใช่ตัวเลข. ถ้าเช็คแค่ "มีค่าไหม" จะได้
    ``view_rot: "auto"`` ลงรายงาน = ค่าที่ไม่มีความหมายกับใครเลย."""
    rep = _inspect(tmp_path, monkeypatch, extra_zones=[
        {"id": "z9", "type": "ignore", "group": "",
         "bbox": [0.6, 0.6, 0.2, 0.2], "rotate": "auto"}])
    z9 = next(z for z in rep["zones"] if z["id"] == "z9")
    assert z9["rotate"] == "auto"         # ของผู้ใช้ ไม่ถูกแตะ
    assert "view_rot" not in z9           # ไม่มีมุมที่ "วัดมา" ⇒ ไม่เดา


def test_the_flag_also_turns_off_the_card_angle(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPORT_VIEW_ROTATE", False)
    rep = _inspect(tmp_path, monkeypatch, zone_rotate=90, ocr_rotate=0)
    assert "view_rot" not in rep["zones"][0]


def test_cards_prefer_the_display_angle_over_the_ocr_angle():
    js = _js()
    i = js.index("const rotOf = (zz)")
    frag = js[i:i + 400]
    assert "zz.view_rot" in frag
    assert ": zz.rotate;" in frag        # รายงานเก่าไม่มีคีย์ ⇒ ถอยไปของเดิม


def test_defect_cards_pass_the_zone_angle_to_the_crop():
    js = _js()
    i = js.index("const rotOf = (zz)")
    frag = js[i:i + 260]
    assert "rr === 90 || rr === 180 || rr === 270" in frag
    assert '"&rotate=" + rr' in frag
    # ทั้งฝั่งหลักและฝั่งอ้างอิงต้องได้มุมของ **โซนตัวเอง**
    assert "rotOf(z)" in js and "rotOf(refZ)" in js


def test_the_saved_report_carries_the_angle_ocr_actually_used():
    """การ์ดต้องแสดง "ภาพที่ตรวจจริง" ⇒ ต้องเป็นมุมที่ resolve แล้ว."""
    import inspect

    from artwork_check import pipeline
    src = inspect.getsource(pipeline.run_inspection)
    assert 'rot_by_id = {r["zone_id"]: r.get("rotate", 0) for r in ocr_results}' \
        in src
    assert 'z["rotate"] = rot_by_id[z["id"]]' in src
