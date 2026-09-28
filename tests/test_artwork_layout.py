"""ปุ่ม ▦ Layout ของหน้าตรวจ Artwork (28 ก.ย. 2026) — แสดงผลล้วน.

โหมด "ปกติ" ต้องเป็นหน้าตาเดิมทุกประการ (ค่าเริ่มต้น + ไฟล์เดียว) ·
"🅰 | 🅱 ซ้าย-ขวา" ใช้กล่องภาพที่สอง · "ภาพ OCR ด้านขวา" ย้ายแค่ตำแหน่ง
ภาพตัวอย่าง. พฤติกรรมบนเบราว์เซอร์จริง (วาด/ลาก/ซูม/หมุนจอทั้งสองกล่อง)
ยืนยันด้วย Chromium — ไฟล์นี้ล็อก "สัญญา" ที่ถ้าหลุดจะพังแบบเงียบ.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = open(os.path.join(ROOT, "templates", "artwork_check.html"), encoding="utf-8").read()
JS = open(os.path.join(ROOT, "static", "js", "artwork_check.js"), encoding="utf-8").read()
HIST = open(os.path.join(ROOT, "templates", "artwork_check_history.html"), encoding="utf-8").read()


def _fn(name):
    i = JS.index("function " + name + "(")
    j = JS.index("\n  }\n", i)
    return JS[i:j]


# ── template ─────────────────────────────────────────────────────────
def test_layout_buttons_exist_and_start_hidden():
    m = re.search(r'<span id="awLayoutGroup"([^>]*)>(.*?)</span>\s*<span class="aw-pan-hint"',
                  HTML, re.S)
    assert m and 'style="display:none;"' in m.group(1)
    grp = m.group(2)
    for mode in ("normal", "split", "side"):
        assert 'data-layout="%s"' % mode in grp


def test_second_pane_exists_and_is_hidden_by_default():
    for el in ("awStageBoxB", "awStageRotB", "awStageB", "awPreviewImgB"):
        assert 'id="%s"' % el in HTML
    m = re.search(r'<div class="aw-pane" id="awPaneB"([^>]*)>', HTML)
    assert m and "display:none" in m.group(1)


def test_original_stage_ids_are_kept():
    # JS เดิม + เทสต์อื่นอ้าง id เหล่านี้ตรง ๆ
    for el in ("awStageBox", "awStageRot", "awStage", "awPreviewImg",
               "awStageEmpty", "awRotPreview", "awRotThumb", "awZoomBar"):
        assert 'id="%s"' % el in HTML


def test_rot_preview_still_between_stage_and_props():
    assert HTML.index('id="awStageBox"') < HTML.index('id="awRotPreview"') \
        < HTML.index('id="awProps"')


def test_new_css_is_scoped_to_layout_classes():
    """ไม่มีคลาส lay-* = ตัวห่อไม่มีสไตล์ = หน้าตาเดิม. กฎใหม่ที่แตะของเดิม
    (.aw-stage-box / .aw-pane / .aw-rot-*) ต้องอยู่ใต้ .lay-split/.lay-side."""
    css = HTML[HTML.index("<style>"):HTML.index("</style>")]
    block = css[css.index("เลย์เอาต์พื้นที่ภาพ"):css.index("@media (max-width:900px)")]
    for line in block.splitlines():
        sel = line.split("{")[0]
        if "{" not in line or not re.search(r"\.aw-(stage-box|pane(?![-\w])|rot-)", sel):
            continue
        if sel.strip() in (".aw-pane-head", ".aw-rot-empty"):
            continue                       # กฎ "ซ่อน" ของ element ใหม่ — ไม่แตะของเดิม
        assert "lay-split" in sel or "lay-side" in sel, line
    assert ".aw-pane-head { display:none; }" in css
    assert ".aw-rot-empty { display:none; }" in css


# ── JS ───────────────────────────────────────────────────────────────
def test_default_layout_is_normal_and_needs_two_files():
    assert 'let layoutPref = "normal";' in JS
    assert 'let layout = "normal";' in JS
    wl = _fn("wantedLayout")
    assert "refAttached" in wl and "docMeta.b" in wl
    assert 'return "normal";' in wl


def test_layout_pref_storage_is_guarded():
    i = JS.index("LAYOUT_KEY")
    seg = JS[i:JS.index("function wantedLayout")]
    assert "try {" in seg and "catch" in seg
    assert "LAYOUTS.indexOf(v) >= 0" in seg     # ค่ามั่วใน storage = ไม่ใช้


def test_layout_reapplied_whenever_second_file_changes():
    # แนบ 🅱 · เอา 🅱 ออก · อัปโหลด 🅰 ใหม่ · กู้คืนงาน — ทุกทางต้องเรียก
    # applyLayout() ไม่งั้นค้างอยู่ในโหมดซ้าย-ขวาทั้งที่เหลือไฟล์เดียว
    assert "applyLayout();" in _fn("uploadRef")
    assert "applyLayout();" in _fn("restoreSession")
    rm = JS[JS.index('$("awRefRemove").addEventListener'):]
    assert rm.index("applyLayout();") < rm.index("showDoc(\"a\")")
    up = JS[JS.index('fileInput.addEventListener("change"'):]
    assert up.index("applyLayout();") < up.index('showDoc("a");')


def test_pane_activation_runs_in_capture_phase():
    """คลิกกล่องไหน = ทำงานกับกล่องนั้น — ต้องมาก่อน handler ของโซน/การวาด/
    pan (capture) และห้าม render โซนใหม่ (จะลบ element ที่กำลังถูกลาก)."""
    assert 'addEventListener("mousedown", act, true)' in JS
    act = _fn("activatePane")
    assert "renderZones" not in act
    assert "bindPane(p)" in act


def test_draw_and_pan_bound_on_both_panes():
    assert 'paneA.stage.addEventListener("mousedown", onStageDrawStart)' in JS
    assert 'paneB.stage.addEventListener("mousedown", onStageDrawStart)' in JS
    assert 'allBoxes.forEach((b) => b.addEventListener("mousedown", panStart))' in JS
    assert 'b.addEventListener("wheel", onWheelZoom' in JS


def test_split_renders_zones_of_each_file_on_its_own_pane():
    rz = _fn("renderZones")
    assert 'renderPaneZones(paneA, "a")' in rz
    assert 'renderPaneZones(paneB, "b")' in rz
    assert "renderPaneZones(pane, activeDoc)" in rz     # เลย์เอาต์ปกติ = เดิม


def test_zone_quality_uses_the_zones_own_file_size():
    # กล่องที่ไม่ได้ทำงานต้องคิดคุณภาพจากขนาดไฟล์ของตัวเอง ไม่ใช่ natW ของอีกไฟล์
    assert "zoneQuality(z.bbox, doc)" in _fn("renderPaneZones")
    assert "zoneQuality(z.bbox, docOfZone(z))" in JS


def test_screen_rotation_applies_to_every_visible_pane():
    assert "livePanes().forEach" in _fn("applyPageRot")


# ── หน้าประวัติ ─────────────────────────────────────────────────────
def test_history_page_is_as_wide_as_the_inspect_page():
    assert ".main-content { max-width: none; }" in HIST
    assert re.search(r"\.aw-wrap \{ max-width: 1600px;", HIST)
    assert ".aw-tbl-scroll { overflow-x:auto; }" in HIST   # จอแคบเลื่อนในตาราง
