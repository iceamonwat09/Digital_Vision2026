# -*- coding: utf-8 -*-
"""หมุนจอแยกต่อไฟล์ · โซนอัตโนมัติได้มุมตามต้นทาง · ซ่อนช่องติ๊กโหมดทดลอง
(29 ก.ย. 2026) — ส่วน Layout/การแสดงผลล้วน ไม่แตะตรรกะการตรวจ.

ผู้ใช้: *"กดหมุนแล้วหมุนทั้ง 🅰 ไฟล์หลัก 🅱 ชิ้นงาน ต้องการให้แยกการหมุน
เพราะบางครั้งไฟล์ A เป็นแนวตั้ง ไฟล์ B เป็นกลับหัว"* · *"ปรับโซนอัตโนมัติให้
หมุนตามโซนต้นทาง"* · *"ซ่อนช่องติ๊กโหมดทดลองทั้ง 2 โหมดเหมือนที่เคย commit"*

สิ่งที่ล็อกไว้:
  ① ช่องติ๊ก "หั่นแถบ"/"อ่านซ้ำ" ซ่อน + ถือว่าไม่ติ๊กเสมอ (เดิมเป๊ะเมื่อธงเปิด)
  ② มุมจอแยกต่อไฟล์: ปุ่มหมุนเฉพาะไฟล์ที่ทำงาน · โซนใหม่ได้มุมของไฟล์ที่วาดบน
     · ลากย้าย/ย่อขยายบนจอที่หมุนไปถูกแกน · ธงปิด = ค่าเดียวร่วมกันเหมือนเดิม
  ③ รายงาน: ภาพ 🅱 หมุนตาม ``page_rot_b`` · รายงานเก่า/ธงปิด = มุมร่วมเดิม
  ④ หากรอบคู่อัตโนมัติ: โซน 🅱 ได้มุมตาม 🅰 ต้นทาง (หักผลต่างมุมจอ) และค้นคู่
     ได้แม้ 🅱 วางกลับหัว · ธงปิด/มุมเท่ากัน = ค้นแบบเดิมทุกไบต์
  ⑤ ไม่มีอะไรแตะ defects/verdict/พิกัดโซน
"""
import json
import os
import re
import shutil
import subprocess

import numpy as np
import pytest

from artwork_check import config, report, zones as zones_mod

cv2 = pytest.importorskip("cv2")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = open(os.path.join(ROOT, "static", "js", "artwork_check.js"),
          encoding="utf-8").read()
HTML = open(os.path.join(ROOT, "templates", "artwork_check.html"),
            encoding="utf-8").read()
NODE = shutil.which("node")


def _fn(name):
    i = JS.index("function " + name + "(")
    j = JS.index("\n  }\n", i)
    return JS[i:j + 4]


# ─────────────────────────────────────────────────────────────────────
# ① ซ่อนช่องติ๊กโหมดทดลอง
# ─────────────────────────────────────────────────────────────────────

EXP_IDS = ("awSplitBands", "awConfirmReads")


@pytest.fixture
def render(monkeypatch):
    """เรนเดอร์ ``artwork_check.html`` ตัวจริงผ่าน route ตัวจริง"""
    flask = pytest.importorskip("flask")
    from artwork_check.routes import artwork_bp

    def _render(**flags):
        for k, v in flags.items():
            monkeypatch.setattr(config, k, v)
        app = flask.Flask(__name__,
                          template_folder=os.path.join(ROOT, "templates"),
                          static_folder=os.path.join(ROOT, "static"))

        @app.context_processor
        def _ctx():
            return {"config_version": "test", "current_user": None,
                    "auth_enabled": False, "has_perm": lambda *a, **k: True}

        app.register_blueprint(artwork_bp)
        with app.test_client() as c:
            r = c.get("/artwork_check")
            assert r.status_code == 200
            return r.get_data(as_text=True)
    return _render


def _row_of(html, el_id):
    i = html.index('id="%s"' % el_id)
    start = html.rfind('<div class="aw-row"', 0, i)
    open_tag = html[start:html.index(">", start) + 1]
    inp = html[html.rfind("<input", 0, i):html.index(">", i) + 1]
    return open_tag, inp


def test_defaults():
    assert config.EXPERIMENT_OCR_UI is False
    assert config.PAGE_ROT_PER_DOC is True
    assert config.ZONE_ROTATE_INHERIT is True


@pytest.mark.parametrize("el_id", EXP_IDS)
def test_experiment_rows_hidden_but_still_in_the_dom(render, el_id):
    html = render(EXPERIMENT_OCR_UI=False)
    row, inp = _row_of(html, el_id)
    assert "display:none" in row
    assert 'aria-hidden="true"' in row
    assert 'data-off="1"' in inp            # JS ต้องถือว่าไม่ติ๊ก


@pytest.mark.parametrize("el_id", EXP_IDS)
def test_experiment_flag_on_restores_the_old_rows(render, el_id):
    html = render(EXPERIMENT_OCR_UI=True)
    row, inp = _row_of(html, el_id)
    assert "display:none" not in row
    assert "aria-hidden" not in row
    assert "data-off" not in inp


def test_only_the_two_experiment_rows_are_hidden(render):
    html = render(EXPERIMENT_OCR_UI=False)
    for el_id in ("awForceOcr", "awPixelCheck"):
        row, inp = _row_of(html, el_id)
        assert "display:none" not in row, el_id
        assert "data-off" not in inp, el_id


def test_hidden_checkbox_counts_as_unticked_in_js():
    body = _fn("expChecked")
    assert "el.checked" in body and "dataset.off" in body
    for fn, el_id in (("splitBandsOn", "awSplitBands"),
                      ("confirmReadsOn", "awConfirmReads")):
        m = re.search(r"function %s\(\) \{\s*return ([^;]+);" % fn, JS)
        assert m and m.group(1) == 'expChecked("%s")' % el_id, fn


@pytest.mark.parametrize("el_id,key", [("awSplitBands", "splitBands"),
                                       ("awConfirmReads", "confirmReads")])
def test_restoring_a_session_or_template_cannot_tick_a_hidden_box(el_id, key):
    """กู้คืนงานที่ค้าง + เปิดจากงานต้นแบบ ใช้ restoreSession() ตัวเดียวกัน"""
    line = next(ln for ln in JS.splitlines()
                if '$("%s").checked = !!s.%s' % (el_id, key) in ln)
    assert '!$("%s").dataset.off' % el_id in line


# ─────────────────────────────────────────────────────────────────────
# ② มุมจอแยกต่อไฟล์ — รัน JS ตัวจริงผ่าน node
# ─────────────────────────────────────────────────────────────────────

_FNS = ("perDocRot", "docKey", "pageRotOf", "setPageRot", "paneDoc",
        "rotForNewZone", "inheritRot", "pairRotDelta", "rotForPairOf",
        "rotForProposed", "unrotPoint", "unrotDelta", "shownDims",
        "pageRotBody")


def _run_js(script, per_doc=True, inherit=True):
    if not NODE:
        pytest.skip("ไม่มี node")
    src = "\n".join(_fn(n) for n in _FNS)
    prog = (
        "var window = {AW_PAGE_ROT_PER_DOC: %s, AW_ZONE_ROTATE_INHERIT: %s};\n"
        "var PAGE_ROT = [0, 90, 180, 270];\n"
        "var rotByDoc = {a: 0, b: 0};\n"
        "var activeDoc = 'a'; var layout = 'normal'; var autoRotate = false;\n"
        "%s\n%s\n" % ("true" if per_doc else "false",
                      "true" if inherit else "false", src, script))
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_rotating_one_file_leaves_the_other_alone():
    r = _run_js("""
      activeDoc = 'b'; setPageRot(activeDoc, 180);
      console.log(JSON.stringify([pageRotOf('a'), pageRotOf('b')]));""")
    assert r == [0, 180]


def test_flag_off_keeps_one_shared_angle():
    """ธงปิด = ค่าเดียวร่วมสองไฟล์ + ไม่ส่ง page_rot_b = เดิมเป๊ะ"""
    r = _run_js("""
      activeDoc = 'b'; setPageRot(activeDoc, 90);
      console.log(JSON.stringify([pageRotOf('a'), pageRotOf('b'),
                                  pageRotBody()]));""", per_doc=False)
    assert r == [90, 90, {"page_rot": 90}]


def test_the_request_carries_both_angles():
    r = _run_js("""
      rotByDoc.a = 90; rotByDoc.b = 270;
      console.log(JSON.stringify(pageRotBody()));""")
    assert r == {"page_rot": 90, "page_rot_b": 270}


def test_a_new_zone_gets_the_angle_of_the_file_it_is_drawn_on():
    r = _run_js("""
      rotByDoc.a = 90; rotByDoc.b = 180;
      activeDoc = 'a'; var ra = rotForNewZone();
      activeDoc = 'b'; var rb = rotForNewZone();
      console.log(JSON.stringify([ra, rb]));""")
    assert r == [90, 180]


def test_in_split_each_pane_follows_its_own_file():
    r = _run_js("""
      layout = 'split'; activeDoc = 'a';
      console.log(JSON.stringify([paneDoc({doc:'a'}), paneDoc({doc:'b'})]));""")
    assert r == ["a", "b"]
    r = _run_js("""
      layout = 'normal'; activeDoc = 'b';
      console.log(JSON.stringify([paneDoc({doc:'a'})]));""")
    assert r == ["b"]      # กล่องเดียวแสดงไฟล์ที่ทำงานอยู่


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_drag_delta_is_the_derivative_of_the_point_mapping(rot):
    """ลากย้าย/ย่อขยายบนจอที่หมุน ⇒ ระยะต้องแปลงแบบเดียวกับจุดที่วาด
    (เดิมไม่แปลง ⇒ หมุน 90° แล้วลากขวา โซนเลื่อนลง = ผิดแกน)"""
    r = _run_js("""
      var W = 300, H = 200, rot = %d;
      var p0 = unrotPoint(40, 50, W, H, rot), p1 = unrotPoint(47, 61, W, H, rot);
      var d = unrotDelta(7, 11, rot);
      console.log(JSON.stringify([p1.x - p0.x, p1.y - p0.y, d.x, d.y]));""" % rot)
    assert r[0] == r[2] and r[1] == r[3]


def test_fit_uses_the_side_that_faces_the_box():
    r = _run_js("""
      console.log(JSON.stringify([shownDims(800, 500, 0), shownDims(800, 500, 90),
                                  shownDims(800, 500, 180), shownDims(800, 500, 270)]));""")
    assert r == [[800, 500], [500, 800], [800, 500], [500, 800]]


def test_drag_uses_the_zone_files_angle():
    body = JS[JS.index("function startDrag("):]
    body = body[:body.index("\n  }\n")]
    assert "rot: pageRotOf(docOfZone(zone))" in body
    mv = JS[JS.index('document.addEventListener("mousemove", (ev) => {\n    if (!drag)'):]
    mv = mv[:mv.index("\n  });\n")]
    assert "unrotDelta(" in mv and "drag.rot" in mv


def test_draw_point_uses_the_active_panes_file_angle():
    body = _fn("drawPoint")
    assert "pageRotOf(paneDoc(pane))" in body
    assert "unrotPoint(sx, sy, W, H, rot)" in body


def test_each_pane_rotates_by_its_own_file():
    body = _fn("rotatePane")
    assert "pageRotOf(paneDoc(p))" in body


def test_button_rotates_only_the_active_file():
    i = JS.index('pageRotBtn.addEventListener("click"')
    body = JS[i:i + 500]
    assert "setPageRot(activeDoc" in body
    assert "pageRotOf(activeDoc)" in body


def test_no_leftover_shared_angle_variable():
    """ตัวแปร ``pageRot`` เดิมต้องหายหมด — เหลือจุดเดียวที่อ่านค่าร่วม =
    มุมผิดไฟล์แบบเงียบ ๆ"""
    assert not re.search(r"\bpageRot\b", JS)


def test_flags_declared_before_the_script():
    i = HTML.index("window.AW_PAGE_ROT_PER_DOC")
    j = HTML.index("window.AW_ZONE_ROTATE_INHERIT")
    k = HTML.index("js/artwork_check.js")
    assert i < k and j < k


def test_pane_heads_show_their_angle():
    assert 'class="aw-pane-rot" data-doc="a"' in HTML
    assert 'class="aw-pane-rot" data-doc="b"' in HTML


# ─────────────────────────────────────────────────────────────────────
# ③ รายงาน — ภาพ 🅱 หมุนตามมุมของตัวเอง
# ─────────────────────────────────────────────────────────────────────

def test_report_b_images_use_page_rot_b_with_old_fallback():
    i = JS.index("const prb =")
    frag = JS[i:i + 400]
    assert "rep.page_rot_b === undefined" in frag and "? pr" in frag
    for name in ("preview_b.png", "overlay_b.png"):
        line = next(ln for ln in JS.splitlines() if "/" + name + '?t=" + ts' in ln)
        assert "rotQB" in line, name
    for name in ("preview.png", "overlay.png"):
        line = next(ln for ln in JS.splitlines() if "/" + name + '?t=" + ts' in ln)
        assert "rotQ" in line and "rotQB" not in line, name


def _inspect(tmp_path, monkeypatch, **kw):
    from artwork_check import pipeline
    monkeypatch.setattr(report.config, "INSPECTIONS_DIR", str(tmp_path))
    rec = "20260929-000000-aa11bb"
    d = report.inspection_dir(rec, create=True)
    with open(os.path.join(d, "source.png"), "wb") as f:
        f.write(b"x")
    cv2.imwrite(os.path.join(d, "preview.png"), np.full((50, 80, 3), 255, np.uint8))
    monkeypatch.setattr(pipeline, "ArtworkDocument", lambda *a, **k: object())
    monkeypatch.setattr(
        pipeline.ocr, "read_all_zones",
        lambda doc, zones, page_auto=False, force_ocr=False,
               split_bands=False, font_trust=None: [
            {"zone_id": z["id"], "text": "T", "engine": "stub",
             "conf": None, "rotate": 0}
            for z in zones if z.get("type") != "ignore"])
    return pipeline.run_inspection(
        rec, [{"id": "z1", "type": "panel", "group": "",
               "bbox": [0.1, 0.1, 0.2, 0.2], "rotate": "default"}], **kw)


def test_run_inspection_records_page_rot_b(tmp_path, monkeypatch):
    rep = _inspect(tmp_path, monkeypatch, page_rot=90, page_rot_b=180)
    assert rep["page_rot"] == 90 and rep["page_rot_b"] == 180


def test_not_sending_page_rot_b_writes_no_key(tmp_path, monkeypatch):
    """สคริปต์เดิม/ธงปิด ⇒ ไม่มีคีย์ ⇒ ภาพ 🅱 ใช้มุมร่วม = รายงานเดิมเป๊ะ"""
    assert "page_rot_b" not in _inspect(tmp_path, monkeypatch, page_rot=90)


@pytest.mark.parametrize("bad", [45, -90, 360, 0])
def test_a_bad_b_angle_becomes_zero(tmp_path, monkeypatch, bad):
    assert _inspect(tmp_path, monkeypatch, page_rot_b=bad)["page_rot_b"] == 0


def test_b_angle_never_touches_the_verdict_or_zones(tmp_path, monkeypatch):
    a = _inspect(tmp_path, monkeypatch, page_rot=0)
    b = _inspect(tmp_path, monkeypatch, page_rot=0, page_rot_b=270)
    assert a["defects"] == b["defects"] and a["verdict"] == b["verdict"]
    assert a["zones"] == b["zones"]


def test_route_reads_page_rot_b_only_when_the_flag_is_on(monkeypatch):
    from artwork_check import routes
    monkeypatch.setattr(config, "PAGE_ROT_PER_DOC", True)
    assert routes._page_rot_b_arg({"page_rot_b": 180}) == 180
    assert routes._page_rot_b_arg({"page_rot_b": "abc"}) == 0
    assert routes._page_rot_b_arg({"page_rot_b": 45}) == 0
    assert routes._page_rot_b_arg({}) is None
    monkeypatch.setattr(config, "PAGE_ROT_PER_DOC", False)
    assert routes._page_rot_b_arg({"page_rot_b": 180}) is None


def test_setup_keeps_b_angle_but_clone_does_not_copy_it(tmp_path, monkeypatch):
    from artwork_check import pipeline
    monkeypatch.setattr(config, "INSPECTIONS_DIR", str(tmp_path))
    src = "20260929-010000-abcdef"
    d = report.inspection_dir(src, create=True)
    cv2.imwrite(os.path.join(d, "source.png"), np.full((60, 90, 3), 255, np.uint8))
    cv2.imwrite(os.path.join(d, "preview.png"), np.full((60, 90, 3), 255, np.uint8))
    report.save_setup(src, [{"id": "z1", "type": "panel", "group": "A", "doc": "a",
                             "bbox": [0.1, 0.1, 0.3, 0.3], "rotate": 90}],
                      {"page_rot": 90, "page_rot_b": 180, "brand": "X"})
    assert report.load_setup(src)["page_rot_b"] == 180
    res = pipeline.clone_inspection(src, owner={"user_id": "1", "username": "u"})
    assert res["settings"]["page_rot"] == 90
    assert "page_rot_b" not in res["settings"]
    assert "page_rot_b" not in report.load_setup(res["id"])


# ─────────────────────────────────────────────────────────────────────
# ④ หากรอบคู่อัตโนมัติ — มุมตามต้นทาง + ค้นคู่ได้แม้ 🅱 วางคนละแนว
# ─────────────────────────────────────────────────────────────────────

def test_pair_rotation_mapping():
    r = _run_js("""
      var out = [];
      rotByDoc.a = 0; rotByDoc.b = 0;
      out.push(rotForPairOf({rotate: 90}, pairRotDelta()));        // 90
      out.push(rotForPairOf({rotate: 'default'}, pairRotDelta())); // default
      rotByDoc.a = 90; rotByDoc.b = 270;                            // delta 180
      out.push(pairRotDelta());
      out.push(rotForPairOf({rotate: 90}, pairRotDelta()));        // 270 = มุมจอ 🅱
      out.push(rotForPairOf({rotate: 'auto'}, pairRotDelta()));    // auto
      out.push(rotForPairOf({rotate: 'default'}, pairRotDelta())); // 180
      rotByDoc.a = 0; rotByDoc.b = 180;
      out.push(rotForPairOf({rotate: 180}, pairRotDelta()));       // default (0)
      console.log(JSON.stringify(out));""")
    assert r == [90, "default", 180, 270, "auto", 180, "default"]


def test_pair_inherit_flag_off_is_the_old_default():
    r = _run_js("""
      rotByDoc.a = 90; rotByDoc.b = 0;
      console.log(JSON.stringify([rotForPairOf({rotate: 90}, pairRotDelta()),
                                  pairRotDelta()]));""", inherit=False)
    assert r == ["default", 0]


def test_zone_drawn_at_the_screen_angle_pairs_to_the_b_screen_angle():
    """กรณีใช้งานจริง: โซน 🅰 วาดบนจอที่หมุน a ⇒ rotate = a · 🅱 หมุนจอ b
    ⇒ โซนคู่ต้องได้ b พอดี (ทุกคู่มุม)"""
    for a in (0, 90, 180, 270):
        for b in (0, 90, 180, 270):
            r = _run_js("""
              rotByDoc.a = %d; rotByDoc.b = %d;
              console.log(JSON.stringify(rotForPairOf({rotate: %d || 'default'},
                                                      pairRotDelta())));""" % (a, b, a))
            assert r == (b or "default"), (a, b, r)


def test_proposed_zones_get_their_files_screen_angle():
    r = _run_js("""
      rotByDoc.a = 0; rotByDoc.b = 270;
      var za = rotForProposed({id:'z1', rotate:'default'}, 'a');
      var zb = rotForProposed({id:'b1', rotate:'default'}, 'b');
      var pinned = rotForProposed({id:'b2', rotate: 90}, 'b');
      console.log(JSON.stringify([za.rotate, zb.rotate, pinned.rotate]));""")
    assert r == ["default", 270, 90]


def test_autopair_js_sends_rot_only_when_needed_and_captures_it_before_await():
    i = JS.index('$("awPairAuto").addEventListener("click"')
    body = JS[i:JS.index("// ── templates", i)]
    assert body.index("const delta = pairRotDelta();") < body.index("await api(")
    assert "rot: delta" in body
    assert "rotate: rotForPairOf(src, delta)" in body
    assert 'rotate: "default"' not in body


def test_redetect_uses_the_doc_captured_before_await():
    i = JS.index('$("awRedetect").addEventListener("click"')
    body = JS[i:i + 2000]
    assert "rotForProposed(z, doc)" in body


def _block_page():
    """หน้า 800x600 ที่มีบล็อกข้อความตรงกลาง (ตัวหนังสือจริง ⇒ หมุนแล้วหน้าตา
    ต่างจริง — ลวดลายสุ่มหมุน 180° แล้วยังได้คะแนน ~0.53 ใช้ทดสอบไม่ได้)"""
    img = np.full((600, 800, 3), 255, np.uint8)
    for i, t in enumerate(["NET WEIGHT 185 g", "INGREDIENTS: TUNA",
                           "Best before 2027"]):
        cv2.putText(img, t, (300, 230 + i * 35), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (0, 0, 0), 2)
    return img


BBOX = [290 / 800, 200 / 600, 260 / 800, 120 / 600]


def test_autopair_rot_zero_is_byte_for_byte_the_old_search():
    a = _block_page()
    b = _block_page()
    assert zones_mod.autopair_bbox(a, b, BBOX) == \
        zones_mod.autopair_bbox(a, b, BBOX, rot=0)
    # ค่าที่ไม่รองรับ = ค้นแบบเดิม
    assert zones_mod.autopair_bbox(a, b, BBOX) == \
        zones_mod.autopair_bbox(a, b, BBOX, rot=45)


def test_autopair_finds_an_upside_down_b_only_with_the_rot():
    a = _block_page()
    b = cv2.rotate(a, cv2.ROTATE_180)
    _, conf_plain = zones_mod.autopair_bbox(a, b, BBOX)
    assert conf_plain < config.AUTOPAIR_MIN_CONF      # เดิม: หาไม่เจอ (ไม่สร้างกรอบ)
    bb, conf = zones_mod.autopair_bbox(a, b, BBOX, rot=180)
    assert conf >= 0.9
    # บล็อกหมุน 180° รอบกลางหน้า ⇒ ตำแหน่งบน 🅱 = สะท้อนผ่านจุดกึ่งกลาง
    exp = [1 - BBOX[0] - BBOX[2], 1 - BBOX[1] - BBOX[3], BBOX[2], BBOX[3]]
    assert np.allclose(bb, exp, atol=0.01)


@pytest.mark.parametrize("rot,code", [(90, cv2.ROTATE_90_CLOCKWISE),
                                      (270, cv2.ROTATE_90_COUNTERCLOCKWISE)])
def test_autopair_rot_90_270_swaps_the_box_sides(rot, code):
    a = _block_page()
    b = cv2.rotate(a, code)                 # 🅱 = 🅰 หมุนตามเข็ม rot องศา
    _, conf_plain = zones_mod.autopair_bbox(a, b, BBOX)
    assert conf_plain < config.AUTOPAIR_MIN_CONF
    bb, conf = zones_mod.autopair_bbox(a, b, BBOX, rot=rot)
    assert conf >= 0.9
    Hb, Wb = b.shape[:2]
    assert abs(bb[2] * Wb - 120) <= 2 and abs(bb[3] * Hb - 260) <= 2


def test_autopair_route_ignores_rot_when_the_flag_is_off():
    src = open(os.path.join(ROOT, "artwork_check", "routes.py"), encoding="utf-8").read()
    i = src.index("def api_autopair(")
    body = src[i:src.index("\ndef ", i + 10)]
    assert "if config.ZONE_ROTATE_INHERIT:" in body
    assert "autopair_bbox(img_a, img_b, bbox, rot=rot)" in body
