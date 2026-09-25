# -*- coding: utf-8 -*-
"""ซ่อนช่องติ๊กโหมดทดลองด้านการอ่าน OCR (25 ก.ย. 2026)

"🧪 หั่นโซนเป็นแถบก่อนส่ง OCR" และ "🔁 อ่านซ้ำ 2 รอบ แล้วยืนยันผล" ไม่ได้ใช้
บนสถานี ⇒ ซ่อนเป็นค่าเริ่มต้น (``config.EXPERIMENT_OCR_UI``).

สิ่งที่ล็อกไว้:
  * ซ่อนด้วย CSS — element ยังอยู่ใน DOM (JS อ้าง id ตรง ๆ)
  * ช่องที่ซ่อน **ต้องถือว่าไม่ติ๊ก** แม้งานที่ค้างใน localStorage จะจำว่าติ๊กไว้
    (ไม่งั้นโหมดทดลองทำงานอยู่ทั้งที่ผู้ใช้มองไม่เห็น)
  * ตั้ง ``ARTWORK_EXPERIMENT_OCR_UI=1`` = หน้าตาเดิมเป๊ะ
  * placeholder หน้า login
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = open(os.path.join(ROOT, "static", "js", "artwork_check.js"),
          encoding="utf-8").read()
LOGIN = open(os.path.join(ROOT, "templates", "login.html"),
             encoding="utf-8").read()

IDS = ("awSplitBands", "awConfirmReads")


@pytest.fixture
def render(monkeypatch):
    """เรนเดอร์ ``artwork_check.html`` ตัวจริงผ่าน route ตัวจริง"""
    flask = pytest.importorskip("flask")
    from artwork_check import config
    from artwork_check.routes import artwork_bp

    def _render(flag):
        monkeypatch.setattr(config, "EXPERIMENT_OCR_UI", flag)
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
    """``<div class="aw-row" ...>`` ที่ห่อ checkbox นั้น (แท็กเปิด + ตัว input)"""
    i = html.index('id="%s"' % el_id)
    start = html.rfind('<div class="aw-row"', 0, i)
    open_tag = html[start:html.index(">", start) + 1]
    inp = html[html.rfind("<input", 0, i):html.index(">", i) + 1]
    return open_tag, inp


def test_default_is_hidden():
    from artwork_check import config
    assert config.EXPERIMENT_OCR_UI is False


@pytest.mark.parametrize("el_id", IDS)
def test_hidden_by_default_but_still_in_the_dom(render, el_id):
    html = render(False)
    row, inp = _row_of(html, el_id)
    assert "display:none" in row
    assert 'aria-hidden="true"' in row
    assert 'data-off="1"' in inp            # JS ต้องถือว่าไม่ติ๊ก


@pytest.mark.parametrize("el_id", IDS)
def test_flag_on_restores_the_old_page(render, el_id):
    html = render(True)
    row, inp = _row_of(html, el_id)
    assert "display:none" not in row
    assert "aria-hidden" not in row
    assert "data-off" not in inp


def test_only_these_two_rows_are_hidden(render):
    """ช่องติ๊กอื่น (force OCR · pixel · หมุน) ต้องไม่ถูกซ่อนตามไปด้วย"""
    html = render(False)
    for el_id in ("awForceOcr", "awPixelCheck"):
        if 'id="%s"' % el_id in html:
            row, inp = _row_of(html, el_id)
            assert "display:none" not in row, el_id
            assert "data-off" not in inp, el_id


def test_hidden_checkbox_counts_as_unticked_in_js():
    body = JS[JS.index("function expChecked("):]
    body = body[:body.index("\n  }\n")]
    assert "el.checked" in body and "dataset.off" in body
    for fn, el_id in (("splitBandsOn", "awSplitBands"),
                      ("confirmReadsOn", "awConfirmReads")):
        m = re.search(r"function %s\(\) \{\s*return ([^;]+);" % fn, JS)
        assert m and m.group(1) == 'expChecked("%s")' % el_id, fn


@pytest.mark.parametrize("el_id,key", [("awSplitBands", "splitBands"),
                                       ("awConfirmReads", "confirmReads")])
def test_restoring_a_saved_session_cannot_tick_a_hidden_box(el_id, key):
    line = next(ln for ln in JS.splitlines()
                if '$("%s").checked = !!s.%s' % (el_id, key) in ln)
    assert '!$("%s").dataset.off' % el_id in line


def test_login_placeholder():
    assert 'placeholder="Username@thaiunion.com"' in LOGIN
    assert "เช่น admin" not in LOGIN
