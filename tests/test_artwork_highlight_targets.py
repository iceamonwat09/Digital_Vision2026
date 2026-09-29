# -*- coding: utf-8 -*-
"""กรอบแดงบนภาพ crop — "ยิงที่ช่วงที่ต่าง · ทั้งสองฝั่ง · หลายจุด".

ที่มา (วัดจริงด้วยบรรทัดของสถานีใน ``test_artwork_line_pairing.py`` โดยให้
Tesseract อ่านถูกทุกคำ แล้วไล่ความกว้างแผง 4 แบบ = 16 เคส):

    วิธียิง                                   ได้กรอบ
    ทั้งบรรทัด + ตัดที่ 120 (ของเดิม)            0 / 16
    ทั้งบรรทัด · ไม่ตัด · ข้ามคำวรรคตอน          4 / 16
    ยิงที่ช่วงที่ต่าง (found_spans/ref_spans)   16 / 16

เหตุผลเชิงกลไก: ``highlight._phrase_matches`` ต้องหาคำ **ติดกันในแถวเดียวกัน**
ให้ครบทุกคำ — บรรทัดจริงยาว 24-25 คำบนแผงคอลัมน์แคบซึ่งไหลหลายแถว ⇒ เป็นไป
ไม่ได้. ช่วงที่ต่างมี 1-3 คำ ⇒ หาเจอ และ **ชี้จุดที่ต่างจริง**
"""
import re

import pytest

from artwork_check import config, highlight as hl


JS = open("static/js/artwork_check.js", encoding="utf-8").read()


# ── ① ค่าคงที่สองฝั่งต้องตรงกัน ────────────────────────────────────────

def test_js_and_python_agree_on_the_target_cap():
    m = re.search(r"const HL_MAX_TARGETS = (\d+);", JS)
    assert m, "หา HL_MAX_TARGETS ใน artwork_check.js ไม่เจอ"
    assert int(m.group(1)) == config.HIGHLIGHT_MAX_TARGETS


def test_a_whole_real_line_is_no_longer_truncated_mid_word():
    # บรรทัดจริงของสถานียาว 139-143 ตัวอักษร — เพดานเดิม 120 ตัดกลางคำ
    assert config.HIGHLIGHT_TARGET_MAX_CHARS >= 143


# ── ② บั๊กคำวรรคตอนใน _phrase_matches ─────────────────────────────────

def _words(line, per_row=99):
    """วางคำเป็นกล่อง (คีย์, box) — per_row = จำนวนคำต่อแถวของแผง."""
    out, x, y = [], 0, 0
    for i, w in enumerate(line.split()):
        if i and i % per_row == 0:
            x, y = 0, y + 30
        wd = 12 * len(w) + 8
        out.append((hl._norm(w), (x, y, x + wd, y + 22)))
        x += wd + 10
    return out


LINE_WITH_SLASH = "محتوى الطاقة: 520 كيلو كالوري / 100 غرام"


def test_a_lone_punctuation_word_must_not_kill_the_whole_phrase():
    """"/" เดี่ยว ๆ normalize แล้วว่าง — เดิมทำให้ **ทุก** หน้าต่างที่คลุม
    ตำแหน่งนั้นถูกโยนทิ้ง ⇒ 0 กรอบเสมอ ไม่ว่าอ่านถูกแค่ไหน."""
    ws = _words(LINE_WITH_SLASH)
    assert any(not k for k, _ in ws), "fixture ต้องมีคำที่ normalize แล้วว่าง"
    assert len(hl._match_boxes(ws, LINE_WITH_SLASH)) == 1


def test_the_punctuation_fix_does_not_invent_a_match_for_another_line():
    ws = _words(LINE_WITH_SLASH)
    assert hl._match_boxes(ws, "إرشادات التخزين في مكان بارد وجاف") == []


# ── ③ ยิงที่ช่วงที่ต่างได้ผลจริงบนบรรทัดของสถานี ────────────────────────

Z_ENERGY = ("للقطط. محتوى الطاقة: 520 كيلو كالوري / 100 غرام. تأكد من توفر "
            "المياه النظيفة والعذبة لقطتك في جميع الأوقات. إرشادات التخزين:")
B_ENERGY = ("للقطط. محتوى الطاقة: 510 كيلو كالوري / 100 غرام. تأكد من توفر "
            "المياه النظيفة والعذبة لقطتك في جميع الأوقات. إرشادات التخزين:")


@pytest.mark.parametrize("per_row", [6, 8, 12, 99])
def test_the_whole_line_cannot_be_found_on_a_wrapping_panel(per_row, monkeypatch):
    """เหตุผลที่ต้องเปลี่ยนไปยิงที่ "ช่วงที่ต่าง" — ไม่ใช่เพราะมัน "ดีกว่าเฉย ๆ".

    ⚠️ ล็อกพฤติกรรม **ก่อนมีชั้น wrap-runs (18 ก.ย.)** ไว้โดยเจตนา: ตอนนั้น
    การยิงทั้งบรรทัดบนแผงที่ข้อความไหลข้ามแถว ให้ 0 กรอบเสมอ ซึ่งคือที่มา
    ของ ``HIGHLIGHT_BY_SPANS``. ชั้นใหม่แก้ข้อจำกัดนั้นแล้ว (ดูเทสต์ถัดไป)
    แต่ **ข้อสรุปยังเหมือนเดิม**: ช่วงที่ต่างยังแม่นกว่ามาก
    """
    monkeypatch.setattr(config, "HIGHLIGHT_WRAP_RUNS", False)
    if per_row == 99:
        pytest.skip("แผงที่ทั้งบรรทัดอยู่แถวเดียว = เคสที่ของเดิมยังทำได้")
    assert hl._match_boxes(_words(Z_ENERGY, per_row), Z_ENERGY) == []


@pytest.mark.parametrize("per_row", [6, 8, 12, 99])
def test_wrap_runs_box_the_wrapped_line_row_by_row(per_row):
    """ชั้น wrap-runs: แผงที่ข้อความไหล ได้กรอบ **หนึ่งใบต่อแถว** ⇒ ผลลัพธ์
    เท่ากับแผงที่ไม่ไหล (ซึ่งได้ 1 กรอบต่อ 1 แถวอยู่แล้ว) ไม่ใช่ของใหม่
    คนละเรื่อง."""
    ws = _words(Z_ENERGY, per_row)
    rows = {b[1] for _, b in ws}
    assert len(hl._match_boxes(ws, Z_ENERGY)) == len(rows)


@pytest.mark.parametrize("per_row", [6, 8, 12])
def test_the_span_is_still_far_tighter_than_the_wrapped_whole_line(per_row):
    """ถึงชั้น wrap-runs จะทำให้ทั้งบรรทัดมีกรอบแล้ว **ยิงที่ช่วงที่ต่างยัง
    ดีกว่ามาก** — 1 กรอบแคบ ๆ ที่ตัวเลข เทียบกับการล้อมทุกแถวของบรรทัด."""
    ws = _words(Z_ENERGY, per_row)
    line = hl._match_boxes(ws, Z_ENERGY)
    span = hl._match_boxes(ws, "520")
    assert len(span) == 1
    area = lambda b: (b[2] - b[0]) * (b[3] - b[1])
    assert area(span[0]) < 0.25 * sum(area(b) for b in line)


@pytest.mark.parametrize("per_row", [6, 8, 12, 99])
def test_the_differing_span_is_found_on_every_panel_width(per_row):
    from artwork_check.checks import diff_spans
    sa, _ = diff_spans(Z_ENERGY, B_ENERGY, "", "")
    spans = [Z_ENERGY[a:b] for a, b in sa]
    assert "520" in spans
    ws = _words(Z_ENERGY, per_row)
    assert len(hl._match_boxes(ws, "520")) == 1


def test_the_span_points_at_the_number_not_the_whole_line():
    """กรอบต้องแคบกว่าบรรทัดมาก ไม่งั้นก็เท่ากับล้อมทั้งบรรทัดเหมือนเดิม."""
    ws = _words(Z_ENERGY, 99)
    (box,) = hl._match_boxes(ws, "520")
    line_w = max(b[2] for _, b in ws) - min(b[0] for _, b in ws)
    assert (box[2] - box[0]) < 0.15 * line_w


# ── ④ ฝั่งอ้างอิง (ชิ้นงาน) ต้องได้ ``hl`` ด้วย ────────────────────────

def test_both_crops_get_a_highlight_parameter():
    """เดิม ``cropB`` ต่อ URL โดยไม่มี ``hl`` เลย ⇒ ไม่มีทางมีกรอบจากการค้นคำ."""
    a = re.search(r'const cropA = .*', JS).group(0)
    b = re.search(r'const cropB = .*', JS).group(0)
    assert "hlParam" in a and "boxA" in a
    assert "hlParamB" in b and "boxB" in b


def test_the_reference_side_searches_the_reference_text_and_zone():
    m = re.search(r"const hlParamB = .*?;", JS, re.S).group(0)
    assert "d.reference" in m and "d.ref_spans" in m and "refZ.id" in m
    assert "d.found" not in m, "ฝั่งอ้างอิงต้องค้นข้อความของฝั่งตัวเอง"


def test_both_sides_can_be_turned_off_back_to_the_old_behaviour():
    assert "window.AW_HL_REF_SIDE === false" in JS
    assert "window.AW_HL_BY_SPANS !== false" in JS


def test_the_flags_reach_both_pages():
    """``renderReport`` เป็นตัวเดียวกันทั้งหน้าตรวจและหน้าประวัติ."""
    for p in ("templates/artwork_check.html",
              "templates/artwork_check_history.html"):
        html = open(p, encoding="utf-8").read()
        assert "window.AW_HL_BY_SPANS" in html, p
        assert "window.AW_HL_REF_SIDE" in html, p


# ── ⑤ เซิร์ฟเวอร์: ``hl`` ซ้ำได้หลายค่า ───────────────────────────────

def test_route_reads_every_hl_value_not_just_the_first():
    src = open("artwork_check/routes.py", encoding="utf-8").read()
    assert 'request.args.getlist("hl")' in src
    assert '[:120]' not in src, "เพดาน 120 เดิมตัดกลางคำของบรรทัดจริงทุกเส้น"


def test_highlight_crop_accepts_a_list_and_unions_the_boxes(monkeypatch):
    from artwork_check import highlight as real_hl, pipeline
    seen = []

    def _locate(crop, found, *a, **k):
        seen.append(found)
        return [(0, 0, 5, 5)] if found == "520" else []

    monkeypatch.setattr(real_hl, "locate_all", _locate)
    monkeypatch.setattr(real_hl, "draw_boxes",
                        lambda crop, boxes: ("drawn", tuple(boxes)))
    monkeypatch.setattr(pipeline.report, "load_report",
                        lambda rec: {"ocr": [{"zone_id": "z1", "text": "t"}]})
    out = pipeline._highlight_crop("rec", object(), ["520", "ไม่มีในภาพ"], "z1")
    assert seen == ["520", "ไม่มีในภาพ"], "ต้องค้นครบทุกคำ ไม่ใช่หยุดที่ตัวแรก"
    assert out[0] == "drawn" and len(out[1]) == 1


def test_a_single_string_still_works_exactly_as_before(monkeypatch):
    from artwork_check import highlight as real_hl, pipeline
    seen = []
    monkeypatch.setattr(real_hl, "locate_all",
                        lambda crop, found, *a, **k: seen.append(found)
                        or [(0, 0, 5, 5)])
    monkeypatch.setattr(real_hl, "draw_boxes",
                        lambda crop, boxes: ("drawn", tuple(boxes)))
    monkeypatch.setattr(pipeline.report, "load_report",
                        lambda rec: {"ocr": [{"zone_id": "z1", "text": "t"}]})
    out = pipeline._highlight_crop("rec", object(), "520", "z1")
    assert seen == ["520"] and out[0] == "drawn"


def test_no_target_found_returns_the_plain_crop(monkeypatch):
    from artwork_check import highlight as real_hl, pipeline

    def _boom(*a, **k):        # pragma: no cover - ต้องไม่ถูกเรียก
        raise AssertionError("ไม่มีกรอบ ต้องไม่วาดอะไรเลย")

    monkeypatch.setattr(real_hl, "locate_all", lambda *a, **k: [])
    monkeypatch.setattr(real_hl, "draw_boxes", _boom)
    monkeypatch.setattr(pipeline.report, "load_report",
                        lambda rec: {"ocr": [{"zone_id": "z1", "text": "t"}]})
    plain = object()
    assert pipeline._highlight_crop("rec", plain, ["ไม่มี"], "z1") is plain


def test_an_empty_target_list_never_touches_the_report(monkeypatch):
    from artwork_check import pipeline
    monkeypatch.setattr(pipeline.report, "load_report",
                        lambda rec: (_ for _ in ()).throw(AssertionError()))
    plain = object()
    assert pipeline._highlight_crop("rec", plain, [], "z1") is plain
    assert pipeline._highlight_crop("rec", plain, "", "z1") is plain


# ── ⑥ ความเร็ว: แคชภาพ crop ที่เรนเดอร์แล้ว ───────────────────────────
# การ์ด defect หนึ่งใบขอรูป 2 ใบ และรายงานหนึ่งใบมีหลายการ์ดที่มักชี้ไปโซนเดิม
# ⇒ เดิมเรนเดอร์ PDF โซนเดียวกันซ้ำ 10-20 ครั้งต่อการเปิดรายงานหนึ่งครั้ง

class _FakeDoc:
    """นับจำนวนครั้งที่ ``render_zone`` ถูกเรียกจริง."""
    calls = 0
    is_pdf = True

    def __init__(self, *a, **k):
        pass

    def render_zone(self, bbox, dpi=0, max_side=0):
        import numpy as np
        _FakeDoc.calls += 1
        return np.zeros((1300, 1300, 3), dtype="uint8")


@pytest.fixture
def fake_render(monkeypatch):
    from artwork_check import pipeline
    _FakeDoc.calls = 0
    pipeline._CROP_CACHE.clear()
    monkeypatch.setattr(pipeline, "ArtworkDocument", _FakeDoc)
    monkeypatch.setattr(pipeline.report, "inspection_dir", lambda rec: "/tmp")
    monkeypatch.setattr(pipeline, "_find_source", lambda d, base="source": "x")
    yield pipeline
    pipeline._CROP_CACHE.clear()


def test_the_same_zone_is_rendered_once_not_once_per_card(fake_render):
    p = fake_render
    for _ in range(6):                       # 6 การ์ดชี้โซนเดียวกัน
        p._render_zone_cached("rec", [0.1, 0.1, 0.2, 0.2], 450, "a")
    assert _FakeDoc.calls == 1


def test_each_side_and_each_zone_still_renders_on_its_own(fake_render):
    p = fake_render
    p._render_zone_cached("rec", [0.1, 0.1, 0.2, 0.2], 450, "a")
    p._render_zone_cached("rec", [0.1, 0.1, 0.2, 0.2], 450, "b")   # อีกไฟล์
    p._render_zone_cached("rec", [0.5, 0.1, 0.2, 0.2], 450, "a")   # อีกโซน
    p._render_zone_cached("rec2", [0.1, 0.1, 0.2, 0.2], 450, "a")  # อีกงาน
    assert _FakeDoc.calls == 4


def test_the_cache_hands_back_a_copy_so_a_drawn_box_never_sticks(fake_render):
    p = fake_render
    first = p._render_zone_cached("rec", [0.1, 0.1, 0.2, 0.2], 450, "a")
    first[0, 0] = 255                        # ผู้เรียกวาดกรอบทับ
    second = p._render_zone_cached("rec", [0.1, 0.1, 0.2, 0.2], 450, "a")
    assert second[0, 0].tolist() == [0, 0, 0], "กรอบของการ์ดใบก่อนต้องไม่ติดมา"


def test_turning_the_cache_off_restores_a_render_every_time(fake_render,
                                                            monkeypatch):
    p = fake_render
    monkeypatch.setattr(p.config, "CROP_CACHE_MAX", 0)
    for _ in range(3):
        p._render_zone_cached("rec", [0.1, 0.1, 0.2, 0.2], 450, "a")
    assert _FakeDoc.calls == 3


def test_the_cache_is_bounded(fake_render, monkeypatch):
    p = fake_render
    monkeypatch.setattr(p.config, "CROP_CACHE_MAX", 2)
    for i in range(5):
        p._render_zone_cached("rec", [i / 10.0, 0.1, 0.2, 0.2], 450, "a")
    assert len(p._CROP_CACHE) == 2


# ── ⑦ ความเร็ว: เบราว์เซอร์เก็บภาพ crop ไว้ใช้ซ้ำได้ ───────────────────

def test_crop_urls_carry_the_report_version_not_a_timestamp():
    """ผูก ``rv`` ไว้ ⇒ URL เดิมให้ภาพเดิมเสมอ (แคชได้) และเปลี่ยนเองเมื่อ
    กด "ส่งตรวจสอบ" ซ้ำบน id เดิม. ถ้าใช้ Date.now() จะไม่มีวันโดนแคชเลย."""
    m = re.search(r'const rv = .*', JS).group(0)
    assert "rep.created_at" in m and "Date.now()" not in m
    for name in ("cropA", "cropB", "cropUrl"):
        line = re.search(r"const %s = .*" % name, JS).group(0)
        assert line.rstrip().endswith("+ rv;"), name


def test_a_crop_without_a_report_version_is_never_cached():
    src = open("artwork_check/routes.py", encoding="utf-8").read()
    assert 'fresh = 0 if not request.args.get("rv") else config.CROP_HTTP_MAX_AGE' in src
    assert "max_age=fresh" in src


def test_the_browser_cache_can_be_turned_off():
    import os
    from importlib import reload
    from artwork_check import config as cfg
    os.environ["ARTWORK_CROP_HTTP_MAX_AGE"] = "0"
    try:
        assert reload(cfg).CROP_HTTP_MAX_AGE == 0
    finally:
        del os.environ["ARTWORK_CROP_HTTP_MAX_AGE"]
        reload(cfg)


# ── ⑧ ความเร็ว: หยุดพิสูจน์กล่องเมื่อได้ครบตามที่จะใช้จริง ─────────────
# วัดบนแผงจริง (1131x1600, คำที่ซ้ำ 30 แถว): พิสูจน์ 25 กล่องเพื่อเก็บ 6
# ⇒ 2.49 วินาที. หยุดที่ 6 ⇒ 0.61 วินาที · **กรอบที่ได้ชุดเดียวกันเป๊ะ**

class _FakeTess:
    """pytesseract ปลอมที่นับจำนวนครั้งและตอบว่า "อ่านได้ตรงคำ" เสมอ."""
    def __init__(self):
        self.calls = 0

    def image_to_string(self, img, lang="eng", config=""):
        self.calls += 1
        return "520"


@pytest.fixture
def fake_tess(monkeypatch):
    import sys
    import types
    fake = _FakeTess()
    mod = types.SimpleNamespace(image_to_string=fake.image_to_string)
    monkeypatch.setitem(sys.modules, "pytesseract", mod)
    return fake


def _boxes(n):
    return [(0, i * 40, 30, i * 40 + 30) for i in range(n)]


def _crop():
    import numpy as np
    return np.zeros((1400, 200, 3), dtype="uint8")


def test_verification_stops_once_enough_boxes_survive(fake_tess):
    kept = hl._verify_boxes(_crop(), _boxes(25), "520", "eng", limit=6)
    assert len(kept) == 6
    assert fake_tess.calls == 6, "เกิน 6 = จ่ายเวลาให้กล่องที่จะถูกทิ้งอยู่ดี"


def test_stopping_early_returns_exactly_what_the_full_run_would_show(fake_tess):
    """กันไม่ให้ความเร็วมาแลกกับ "กรอบคนละชุด" (กฎเหล็กข้อ 2)."""
    full = hl._verify_boxes(_crop(), _boxes(25), "520", "eng", limit=0)
    early = hl._verify_boxes(_crop(), _boxes(25), "520", "eng", limit=6)
    assert early == full[:6]


def test_no_limit_still_verifies_everything(fake_tess):
    kept = hl._verify_boxes(_crop(), _boxes(9), "520", "eng", limit=0)
    assert len(kept) == 9 and fake_tess.calls == 9


def test_match_boxes_already_dedupes_so_the_later_dedupe_cannot_shrink_it():
    """เหตุผลที่การหยุดก่อนปลอดภัย: ``locate_all`` เรียก ``_dedupe_boxes``
    อีกรอบหลังพิสูจน์ — ถ้ามันยังตัดได้อีก การหยุดที่ max_boxes จะทำให้
    เหลือกรอบน้อยกว่าเดิม. ทั้งสองทางของ ``_match_boxes`` ตัดมาแล้ว."""
    ws = _words("ALPHA BRAVO ALPHA CHARLIE ALPHA")
    for target in ("ALPHA", "ALPHA BRAVO"):
        hits = hl._match_boxes(ws, target)
        assert hl._dedupe_boxes(hits) == hits, target
