# -*- coding: utf-8 -*-
"""ทิศของข้อความบนการ์ด "พบ / เทียบกับ" (27 ก.ย. 2026)

ที่มา: เคสสถานี Dolphin — การ์ดแสดงที่อยู่อาหรับ ``2/30 طريق سيثاكيت …``
ในย่อหน้าทิศซ้ายไปขวา ⇒ ``2/30`` ถูกวางซ้ายสุดแยกจากก้อนอาหรับ ⇒ ผู้ใช้
เข้าใจว่า "ผิดตรง /". แก้ด้วย ``dir="auto"`` (``config.CARD_TEXT_DIR_AUTO``)

สิ่งที่ล็อกไว้:
  * ค่าเริ่มต้นเปิด · ``ARTWORK_CARD_TEXT_DIR_AUTO=0`` ปิดได้
  * ธงไปถึง **ทั้งสองหน้า** (หน้าตรวจ + ประวัติ) และประกาศ **ก่อน** โหลดสคริปต์
  * เปิด ⇒ ช่อง found / ref / หลักฐานจากไฟล์ ได้ ``dir="auto"``
  * **ความต่างเดียวคือ attribute** — ไฮไลต์ ``<mark>`` · คำค้นกรอบแดง (``hl=``)
    · ข้อความ ไม่เปลี่ยนแม้แต่ตัวเดียว
  * ปิด ⇒ ไม่มี ``dir=`` เลย (HTML เดิม)
"""
import importlib
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS_PATH = os.path.join(ROOT, "static", "js", "artwork_check.js")

# รายงานจำลองที่ใช้ข้อความจริงของเคส Dolphin + การ์ดที่มีหลักฐานจากไฟล์
REPORT = {
    "verdict": "FAIL", "filename": "dolphin.pdf", "ocr": [],
    "zones": [{"id": "z2", "bbox": [0.1, 0.1, 0.3, 0.2]},
              {"id": "b5", "bbox": [0.1, 0.1, 0.3, 0.2]},
              {"id": "z1", "bbox": [0.1, 0.4, 0.3, 0.2]},
              {"id": "b4", "bbox": [0.1, 0.4, 0.3, 0.2]}],
    "defects": [
        {"class": "MISMATCH_PANELS", "severity": "critical",
         "zone_id": "z2", "ref_zone_ids": ["b5"], "message": "ไม่ตรงกัน",
         "found": "2/30 طريق سيثاكيت 1 ، موينج ساموت ساخون ،",
         "found_spans": [[10, 17], [22, 27]],
         "reference": "2/30 طريق سيتاكيت 1 . مويني ساموت ساخون.",
         "ref_spans": [[10, 17], [22, 27]]},
        {"class": "MISMATCH_PANELS", "severity": "warning",
         "zone_id": "z1", "ref_zone_ids": ["b4"], "message": "ไม่ตรงกัน",
         "found": "Net weight 85 g", "found_spans": [[11, 13]],
         "reference": "Net weight 80 g", "ref_spans": [[11, 13]],
         "witness": {"zone": "b4", "kind": "pair",
                     "file_says": "الوزن الصافي 85 غ",
                     "ocr_says": ["80"], "file_diff": ["85"]}},
    ],
}

HARNESS = r"""
const fs = require('fs');
globalThis.window = globalThis;
Date.now = () => 1;   // URL ภาพมี ?t=<เวลา> — ตรึงไว้ให้เทียบสองรอบได้
globalThis.document = {getElementById: () => null,
                       addEventListener: () => {}, querySelectorAll: () => []};
const flag = process.env.AW_FLAG;
if (flag === 'on') window.AW_CARD_DIR_AUTO = true;
if (flag === 'off') window.AW_CARD_DIR_AUTO = false;
eval(fs.readFileSync(process.env.AW_JS, 'utf8'));
const box = {innerHTML: '', querySelectorAll: () => []};
window.awRenderReport(JSON.parse(fs.readFileSync(0, 'utf8')), box);
process.stdout.write(box.innerHTML);
"""


def _render_js(flag):
    node = shutil.which("node")
    if not node:
        pytest.skip("ไม่มี node บนเครื่องนี้")
    env = dict(os.environ, AW_FLAG=flag, AW_JS=JS_PATH)
    r = subprocess.run([node, "-e", HARNESS], env=env,
                       input=json.dumps(REPORT, ensure_ascii=False),
                       capture_output=True, text=True, encoding="utf-8",
                       timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _span(html, cls):
    """แท็กเปิดของ ``<span class="cls" …>`` ทุกตัว"""
    return re.findall(r'<span class="%s"[^>]*>' % re.escape(cls), html)


# ── ค่าตั้ง ───────────────────────────────────────────────────────────

def test_flag_is_on_by_default_and_can_be_turned_off(monkeypatch):
    from artwork_check import config
    try:
        monkeypatch.delenv("ARTWORK_CARD_TEXT_DIR_AUTO", raising=False)
        assert importlib.reload(config).CARD_TEXT_DIR_AUTO is True
        for off in ("0", "false", ""):
            monkeypatch.setenv("ARTWORK_CARD_TEXT_DIR_AUTO", off)
            assert importlib.reload(config).CARD_TEXT_DIR_AUTO is False
    finally:
        monkeypatch.delenv("ARTWORK_CARD_TEXT_DIR_AUTO", raising=False)
        importlib.reload(config)


# ── ธงไปถึงทั้งสองหน้า ────────────────────────────────────────────────

@pytest.fixture
def page(monkeypatch):
    flask = pytest.importorskip("flask")
    from artwork_check import config
    from artwork_check.routes import artwork_bp

    def _get(path, flag):
        monkeypatch.setattr(config, "CARD_TEXT_DIR_AUTO", flag)
        app = flask.Flask(__name__,
                          template_folder=os.path.join(ROOT, "templates"),
                          static_folder=os.path.join(ROOT, "static"))

        @app.context_processor
        def _ctx():
            return {"config_version": "test", "current_user": None,
                    "auth_enabled": False, "has_perm": lambda *a, **k: True}

        app.register_blueprint(artwork_bp)
        with app.test_client() as c:
            r = c.get(path)
            assert r.status_code == 200
            return r.get_data(as_text=True)
    return _get


@pytest.mark.parametrize("path", ["/artwork_check", "/artwork_check/history"])
@pytest.mark.parametrize("flag,js", [(True, "true"), (False, "false")])
def test_both_pages_declare_the_flag_before_loading_the_script(page, path,
                                                               flag, js):
    html = page(path, flag)
    decl = "window.AW_CARD_DIR_AUTO = %s;" % js
    assert decl in html
    # ต้องประกาศก่อนโหลด artwork_check.js — renderReport อ่านค่าตอนวาดการ์ด
    assert html.index(decl) < html.index("js/artwork_check.js")


# ── การวาดการ์ดจริง (renderReport ตัวจริงผ่าน node) ─────────────────────

def test_on_puts_dir_auto_on_found_ref_and_file_evidence():
    html = _render_js("on")
    assert _span(html, "found") == ['<span class="found" dir="auto">'] * 2
    assert _span(html, "ref") == ['<span class="ref" dir="auto">'] * 2
    assert _span(html, "aw-evid-file") == ['<span class="aw-evid-file" dir="auto">']


def test_unset_flag_behaves_as_on():
    """หน้าเก่าที่ยังไม่ประกาศธง (แคชเบราว์เซอร์) ⇒ ใช้ค่าเริ่มต้น = เปิด"""
    assert _render_js("unset") == _render_js("on")


def test_off_emits_no_dir_attribute_at_all():
    html = _render_js("off")
    assert 'dir="auto"' not in html
    assert _span(html, "found") == ['<span class="found">'] * 2
    assert _span(html, "ref") == ['<span class="ref">'] * 2


def test_the_attribute_is_the_only_difference():
    """ไฮไลต์ · ข้อความ · คำค้นกรอบแดง (``hl=``) ต้องเท่าเดิมทุกตัวอักษร"""
    on, off = _render_js("on"), _render_js("off")
    assert on != off
    assert on.replace(' dir="auto"', "") == off


def test_marks_stay_on_the_same_letters():
    html = _render_js("on")
    marks = re.findall(r'<mark class="aw-dx">([^<]*)</mark>', html)
    assert marks[:4] == ["سيثاكيت", "موينج", "سيتاكيت", "مويني"]


def test_red_box_search_terms_are_untouched():
    on, off = _render_js("on"), _render_js("off")
    hl = lambda h: re.findall(r"hl=[^&\"']*", h)
    assert hl(on) and hl(on) == hl(off)


# ── ตำแหน่งที่ใส่ต้องเป็นช่องข้อความเท่านั้น ─────────────────────────────

def test_only_text_spans_get_the_attribute():
    """ไม่ใส่กับทั้งการ์ด/ป้าย — ป้าย "พบ:" ต้องคงทิศของหน้า (ไทย)"""
    js = open(JS_PATH, encoding="utf-8").read()
    uses = [ln.strip() for ln in js.splitlines() if "textDir()" in ln
            and "function" not in ln]
    assert len(uses) == 3
    assert any('class="found"' in u for u in uses)
    assert any('class="ref"' in u for u in uses)
    assert any('class="aw-evid-file"' in u for u in uses)
    # ฟังก์ชันต้องคืนสตริงว่างเมื่อปิดธง (ไม่ใช่ dir="ltr")
    body = js[js.index("function textDir()"):js.index("window.awTextDir")]
    assert 'AW_CARD_DIR_AUTO === false' in body and '? ""' in body
