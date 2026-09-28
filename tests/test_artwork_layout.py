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
    block = css[css.index("/* ── เลย์เอาต์พื้นที่ภาพ"):css.index("/* เตือนว่าโซนนี้จะชี้ตำแหน่งคำ")]
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    # กฎทุกข้อในบล็อกนี้ต้องผูกกับคลาสของฟีเจอร์ใหม่ — ไม่มีข้อไหนแตะของเดิมลอย ๆ
    allowed = (".aw-layout", ".aw-lay-btn", ".aw-lay-btn.active", ".aw-pane-head",
               ".aw-rot-empty", ".aw-toolbar.lay-bar",
               ".aw-toolbar.has-layout .aw-pan-hint")
    sels = re.findall(r"([^{}]+)\{[^{}]*\}", block)
    assert len(sels) > 10
    for raw in sels:
        for sel in raw.split(","):
            sel = sel.strip()
            if not sel:
                continue
            assert (sel in allowed or "lay-split" in sel or "lay-side" in sel), sel
    assert ".aw-pane-head { display:none; }" in css
    assert ".aw-rot-empty { display:none; }" in css


def test_shared_stylesheet_is_not_touched():
    # ความกว้างหน้าอื่น ๆ ทั้งหมดมาจาก style.css — ต้องปลดเพดานใน template เท่านั้น
    css = open(os.path.join(ROOT, "static", "css", "style.css"), encoding="utf-8").read()
    assert re.search(r"\.main-content\s*\{[^}]*max-width:\s*1400px", css)


def test_side_column_is_not_sticky():
    # .main-content เลื่อนเอง (overflow-y:auto) ⇒ sticky ตรึงไม่ติด + navbar บัง
    css = HTML[HTML.index("<style>"):HTML.index("</style>")]
    rule = re.search(r"\.lay-side \.aw-rot-col \{([^}]*)\}", css).group(1)
    assert "sticky" not in rule


def test_layout_buttons_do_not_wrap_the_toolbar():
    assert ".aw-lay-btn { white-space:nowrap; }" in HTML
    assert ".aw-toolbar.has-layout .aw-pan-hint { display:none; }" in HTML
    assert 'classList.toggle("has-layout", !!(refAttached && docMeta.b))' in JS


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
    # force=true: ไฟล์ 🅱 เปลี่ยนขณะอยู่ในซ้าย-ขวา ⇒ ต้องตั้งกล่องใหม่
    # (ไม่งั้นกล่อง B ค้างภาพเก่า และ natW เป็นขนาดของไฟล์เก่า)
    assert "applyLayout(true);" in _fn("uploadRef")
    assert "applyLayout(true);" in _fn("restoreSession")
    al = _fn("applyLayout")
    assert 'if (force && want === "split") setupSplit(' in al
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
    # ห้ามสลับกล่องกลางท่าทาง (วาด/ลาก/เลื่อน) — จุดเริ่มกับพิกัดคนละไฟล์
    assert act.index("if (draw || drag || pan) return;") < act.index("bindPane(p)")
    # โซนที่เลือกค้างเป็นของอีกไฟล์ ⇒ เลิกเลือก (Delete จะไปลบฝั่งที่ไม่ได้ทำงาน)
    assert "selectedId = null;" in act


def test_wheel_activates_pane_only_over_the_image_box():
    # ล้อบนหัวกล่อง (ไม่ใช่ภาพ) ต้องไม่ซูม/สลับกล่อง
    assert 'p.box.addEventListener("wheel", act, { capture: true' in JS
    assert 'p.wrap.addEventListener("wheel"' not in JS


def test_inactive_pane_image_load_does_not_rerender_mid_gesture():
    ld = _fn("onPaneImgLoad")
    assert "if (drag || draw) { zonesStale = true; return; }" in ld
    assert "if (zonesStale && !drag && !draw) { zonesStale = false; renderZones(); }" in JS


def test_redetect_uses_the_file_captured_before_await():
    i = JS.index("const doc = activeDoc;")
    seg = JS[i:JS.index("renderZones();", i)]
    assert seg.index("const doc = activeDoc;") < seg.index("await api(")
    assert "docOfZone(z) !== doc" in seg and "activeDoc" not in seg[len("const doc = activeDoc;"):]


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
    # แผง properties: ซ้าย-ขวาใช้ไฟล์ของโซน · เลย์เอาต์อื่นคงของเดิมเป๊ะ
    assert 'zoneQuality(z.bbox, layout === "split" ? docOfZone(z) : undefined)' in JS


def test_screen_rotation_applies_to_every_visible_pane():
    assert "livePanes().forEach" in _fn("applyPageRot")


# ── หน้าประวัติ ─────────────────────────────────────────────────────
def test_history_page_is_as_wide_as_the_inspect_page():
    assert ".main-content { max-width: none; }" in HIST
    assert re.search(r"\.aw-wrap \{ max-width: 1600px;", HIST)
    assert ".aw-tbl-scroll { overflow-x:auto; }" in HIST   # จอแคบเลื่อนในตาราง
    hjs = open(os.path.join(ROOT, "static", "js", "artwork_check_history.js"),
               encoding="utf-8").read()
    assert '<td class="aw-owner">' in hjs       # ชื่อผู้ตรวจยาวตัดบรรทัดได้
