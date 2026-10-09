"""Artwork V2 — หลักฐานภาพบน PDF ที่เป็น "ภาพสแกนล้วน" (7 ต.ค. รอบ 5 · Friskies · ``PIXEL_RASTER``)

ที่มา: Friskies 🅱 = PDF ที่มีแต่ภาพ JPEG 300 dpi ทั้งหน้า · เดิมเรนเดอร์ 1600 dpi ⇒ ขยายจุดรบกวนของ
JPEG/การสแกน ⇒ ทุกจุด "ตรวจด้วยภาพไม่ได้" · ตอนนี้โซนที่เป็นภาพสแกนล้วนเทียบที่ความละเอียดจริง
ของภาพ + เบลอ σ 0.8 px ทั้งสองฝั่ง

สิ่งที่ล็อก (กฎเหล็กข้อ 2 — "ภาพเหมือน" ต้องไม่กลืนของจริง):
* ตรวจจับ: ภาพเดียวคลุมโซน ≥ 90% **และ** ไม่มีของเวกเตอร์ในโซนเลย · ข้อความเวกเตอร์ทับภาพพื้นหลัง
  (แบบ AvoDerm M2) ไม่ใช่ภาพสแกน · ชั้น OCR ที่มองไม่เห็นไม่นับ · สแกนหยาบกว่า 200 dpi = เส้นทางเดิม
* PDF เวกเตอร์ทั้งคู่ ⇒ ภาพครอปเท่าเดิมทุกพิกเซล (เปิด/ปิดธงเหมือนกัน)
* ภาพสแกนที่เหมือนต้นฉบับ ⇒ SAME · ลบจุด/จุดทศนิยม/เครื่องหมายทั้งตัว ⇒ **ไม่มีทาง SAME**
  (ไล่ 200/300/600 dpi × JPEG q75/q92)
* ปิดธง ⇒ ไม่มีจุดไหนถูกพับเลย (พฤติกรรมเดิม)
"""

from __future__ import annotations

import io
import os
import random
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from artwork_v2_fake import fta_from_lines  # noqa: E402

from artwork_v2 import config, imaging, jobs, keystore, pipeline, pixverify, vision_client  # noqa: E402

fitz = pytest.importorskip("fitz")
cv2 = pytest.importorskip("cv2")
from PIL import Image  # noqa: E402

KEY = "AIza" + "R" * 35
W_PT, H_PT = 420.0, 300.0
SENT = 400 / 72.0
ZONE = {"page": 0, "bbox": [0, 0, 1, 1], "W": int(W_PT * SENT), "H": int(H_PT * SENT)}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    d = tmp_path / "v2"
    monkeypatch.setattr(config, "DATA_DIR", str(d))
    monkeypatch.setattr(config, "JOBS_DIR", str(d / "jobs"))
    monkeypatch.setattr(config, "SECRET_DIR", str(d / "secret"))
    monkeypatch.setattr(config, "KEY_FILE", str(d / "secret" / "key.json"))
    monkeypatch.delenv(config.KEY_ENV, raising=False)
    monkeypatch.setattr(config, "RETRY_WAIT_S", 0.0)
    monkeypatch.setattr(config, "PIXEL_VERIFY", True)
    monkeypatch.setattr(config, "PIXEL_LINE_MODE", True)
    monkeypatch.setattr(config, "PIXEL_RASTER", True)
    os.makedirs(config.JOBS_DIR)
    yield


def _rows():
    rnd = random.Random(3)
    return ["Ingredient %d: chicken %d.%d%% fish oil i j ; : . , vit B%d"
            % (i, rnd.randint(1, 99), rnd.randint(0, 9), i) for i in range(14)]


def _vec(path, rows=None):
    d = fitz.open()
    p = d.new_page(width=W_PT, height=H_PT)
    for i, t in enumerate(rows or _rows()):
        p.insert_text((20, 30 + 18 * i), t, fontsize=9)
    d.save(str(path))
    return str(path)


def _jpeg(src, dpi, q, erase=()):
    with fitz.open(src) as s:
        pix = s[0].get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, 3).copy()
    k = dpi / 72
    for (x0, y0, x1, y1) in erase:
        a[int(y0 * k):int(np.ceil(y1 * k)), int(x0 * k):int(np.ceil(x1 * k))] = 255
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(a, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, q])
    assert ok
    return buf.tobytes()


def _scan(dst, src, dpi=300, q=92, erase=(), rect=None, extra=None, rotate=0):
    """PDF ที่มีแต่ภาพ (เหมือนไฟล์สแกน) · ``extra(page)`` = วาดเพิ่มทับ"""
    d = fitz.open()
    p = d.new_page(width=W_PT, height=H_PT)
    p.insert_image(rect or p.rect, stream=_jpeg(src, dpi, q, erase))
    if extra:
        extra(p)
    if rotate:
        p.set_rotation(rotate)
    d.save(str(dst))
    return str(dst)


def _marks(src):
    """เครื่องหมายเล็ก ๆ ทั้งตัว (จุดบน i/j · จุด · จุดทศนิยม · ; :) เป็นกรอบ pt"""
    with fitz.open(src) as d:
        pix = d[0].get_pixmap(matrix=fitz.Matrix(600 / 72, 600 / 72))
    g = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, 0]
    n, _, st, _ = cv2.connectedComponentsWithStats((g < 128).astype(np.uint8), 8)
    k = 600 / 72
    cand = [((x - 2) / k, (y - 2) / k, (x + w + 2) / k, (y + h + 2) / k)
            for x, y, w, h, a in (st[i] for i in range(1, n)) if 8 <= a <= 120 and w <= 14 and h <= 14]
    random.Random(11).shuffle(cand)
    pick = []
    for c in cand:
        if all(abs(c[0] - p[0]) > 25 or abs(c[1] - p[1]) > 12 for p in pick):
            pick.append(c)
        if len(pick) >= 30:
            break
    assert len(pick) >= 25
    return pick


def _statuses(pa, pb, marks):
    pc = pixverify.PairCheck(pa, pb, ZONE, ZONE)
    try:
        assert pc.ok, pc.ginfo
        return [pc.check("A", [m[0] * SENT - 3, m[1] * SENT - 3, m[2] * SENT + 3, m[3] * SENT + 3])["status"]
                for m in marks], pc.raster
    finally:
        pc.close()


# ── ตรวจจับโซนที่เป็นภาพสแกน ────────────────────────────────────────────

def _zr(path, rect=None):
    with fitz.open(path) as d:
        pg = d[0]
        return pixverify.zone_raster(pg, rect or tuple(pg.rect))


def test_scanned_page_is_detected_with_native_dpi(tmp_path):
    src = _vec(tmp_path / "v.pdf")
    r = _zr(_scan(tmp_path / "s.pdf", src, dpi=300))
    assert r and abs(r["dpi"] - 300) < 1 and r["cover"] == 1.0
    assert abs(_zr(_scan(tmp_path / "s6.pdf", src, dpi=600))["dpi"] - 600) < 1


def test_vector_text_over_a_background_image_is_not_raster(tmp_path):
    """AvoDerm M2: ภาพ 72 dpi คลุมโซน 92% + ข้อความ/เส้นเวกเตอร์ทับ — ถ้าถือเป็นภาพสแกน
    จะเทียบที่ 72 dpi แล้วมองไม่เห็นความต่างเล็ก ๆ เลย"""
    src = _vec(tmp_path / "v.pdf")
    assert _zr(_scan(tmp_path / "t.pdf", src, extra=lambda p: p.insert_text((30, 60), "NET 85 g"))) is None
    assert _zr(_scan(tmp_path / "l.pdf", src,
                     extra=lambda p: p.draw_line((10, 10), (100, 10)))) is None


def test_hidden_ocr_text_layer_does_not_block_detection(tmp_path):
    """ไฟล์สแกนที่ผ่าน OCR มีข้อความที่มองไม่เห็นซ้อนอยู่ — ไม่ใช่ของเวกเตอร์ที่พิมพ์"""
    src = _vec(tmp_path / "v.pdf")
    p = _scan(tmp_path / "o.pdf", src,
              extra=lambda pg: pg.insert_text((30, 60), "Ingredient 0", render_mode=3))
    assert _zr(p) is not None


def test_coarse_scan_and_partial_cover_keep_the_old_path(tmp_path):
    src = _vec(tmp_path / "v.pdf")
    assert _zr(_scan(tmp_path / "c.pdf", src, dpi=150)) is None          # หยาบกว่า RASTER_MIN_DPI
    part = fitz.Rect(0, 0, W_PT, H_PT * 0.8)     # ภาพคงสัดส่วน = 336×240 pt กลางหน้า ⇒ คลุม 64%
    assert _zr(_scan(tmp_path / "h.pdf", src, rect=part)) is None        # ภาพคลุมโซนไม่พอ
    # โซนที่อยู่ในส่วนที่ภาพคลุมทั้งหมด ⇒ เป็นภาพสแกนได้
    assert _zr(tmp_path / "h.pdf", (60, 10, 360, 230)) is not None


def test_rotated_page_uses_the_same_coordinates_as_rendering(tmp_path):
    src = _vec(tmp_path / "v.pdf")
    p = _scan(tmp_path / "r.pdf", src, rotate=90)
    assert _zr(p) is not None
    # ข้อความเวกเตอร์บนหน้าที่หมุน ต้องยังถูกนับในโซนที่ครอบมันอยู่ (พิกัดหน้าที่หมุนแล้ว)
    p2 = _scan(tmp_path / "r2.pdf", src, rotate=90,
               extra=lambda pg: pg.insert_text((30, 60), "NET 85 g"))
    with fitz.open(p2) as d:
        pg = d[0]
        hit = fitz.Rect(30, 50, 90, 64) * pg.rotation_matrix
        hit.normalize()
        assert pixverify.zone_raster(pg, tuple(hit)) is None


# ── ผลการเทียบ ────────────────────────────────────────────────────────

def test_vector_pairs_render_exactly_as_before(tmp_path, monkeypatch):
    pa = _vec(tmp_path / "a.pdf")
    pb = _vec(tmp_path / "b.pdf", rows=[r.replace("chicken", "chickn") for r in _rows()])
    box = (100, 100, 260, 160)
    out = {}
    for flag in (True, False):
        monkeypatch.setattr(config, "PIXEL_RASTER", flag)
        pc = pixverify.PairCheck(pa, pb, ZONE, ZONE)
        assert pc.raster == {"A": None, "B": None} and "raster" not in pc.ginfo
        out[flag] = [pc._crops(s, box) for s in ("A", "B")]
        pc.close()
    for (a1, b1, d1), (a0, b0, d0) in zip(out[True], out[False]):
        assert d1 == d0 and np.array_equal(a1, a0) and np.array_equal(b1, b0)


@pytest.mark.parametrize("dpi,q", [(200, 75), (300, 75), (300, 92), (600, 92)])
def test_scan_matches_its_vector_original_and_erased_marks_are_never_same(tmp_path, dpi, q):
    pa = _vec(tmp_path / "a.pdf")
    marks = _marks(pa)
    st, raster = _statuses(pa, _scan(tmp_path / "s.pdf", pa, dpi, q), marks)
    assert raster["A"] is None and abs(raster["B"]["dpi"] - dpi) < 1
    assert st.count("SAME") >= 0.9 * len(marks), st
    st, _ = _statuses(pa, _scan(tmp_path / "m.pdf", pa, dpi, q, erase=marks), marks)
    assert "SAME" not in st, st


def test_scan_vs_scan_both_capped(tmp_path):
    pa = _vec(tmp_path / "v.pdf")
    marks = _marks(pa)
    sa = _scan(tmp_path / "a.pdf", pa, 300, 92)
    st, raster = _statuses(sa, _scan(tmp_path / "b.pdf", pa, 400, 85), marks)
    assert raster["A"] and raster["B"]
    assert st.count("SAME") >= 0.9 * len(marks)
    st, _ = _statuses(sa, _scan(tmp_path / "m.pdf", pa, 400, 85, erase=marks), marks)
    assert "SAME" not in st


def test_flag_off_scan_folds_nothing(tmp_path, monkeypatch):
    """เส้นทางเดิม: เรนเดอร์ 1600 dpi จากภาพ 300 dpi ⇒ จุดรบกวนของการสแกนทำให้ไม่มีอะไร SAME"""
    monkeypatch.setattr(config, "PIXEL_RASTER", False)
    pa = _vec(tmp_path / "a.pdf")
    marks = _marks(pa)
    st, raster = _statuses(pa, _scan(tmp_path / "s.pdf", pa, 300, 92), marks)
    assert raster == {"A": None, "B": None}
    assert st.count("SAME") <= 2


# ── ทั้ง pipeline: Log + คำเตือนบอกว่าไฟล์ไหนเป็นภาพสแกน ───────────────────

def _line_boxes(path, W, H):
    out = []
    with fitz.open(path) as d:
        pg = d[0]
        sx, sy = W / pg.rect.width, H / pg.rect.height
        for b in pg.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                r = ln["bbox"]
                out.append(("".join(s["text"] for s in ln["spans"]),
                            (r[0] * sx, r[1] * sy, r[2] * sx, r[3] * sy), 0.98))
    return out


def test_pipeline_scan_misread_is_folded_and_logged(tmp_path, monkeypatch):
    pa = _vec(tmp_path / "a.pdf")
    pb = _scan(tmp_path / "b.pdf", pa, 300, 92)
    rows = _rows()

    def fake(groups, poster=None, key=None):
        res = {}
        for gi, g in enumerate(groups):
            for it in g:
                W, H = Image.open(io.BytesIO(it["jpeg"])).size
                lines = _line_boxes(pa, W, H)       # ฝั่ง B ไม่มีชั้นข้อความ — ใช้ผังของ A
                if it["id"].endswith("b"):          # OCR "อ่านผิด" ฝั่งภาพสแกน
                    lines = [(t.replace(rows[3], rows[3].replace("chicken", "chickem")), b, c)
                             for t, b, c in lines]
                res[it["id"]] = {"ok": True, "error": "", "fta": fta_from_lines(lines, W, H),
                                 "request_index": gi}
        return {"results": res, "calls": [{"index": 0, "phase": "main", "images": [],
                                           "json_bytes": 10, "status": 200, "attempts": 1,
                                           "ms": 1, "error": "", "at": "t"}]}
    jid = jobs.create(("a.pdf", open(pa, "rb").read()), ("b.pdf", open(pb, "rb").read()))["id"]
    keystore.save(KEY)
    monkeypatch.setattr(vision_client, "annotate", fake)
    r = pipeline.run(jid, [{"a": {"page": 0, "bbox": [0, 0, 1, 1]},
                            "b": {"page": 0, "bbox": [0, 0, 1, 1]}}])
    p = r["pairs"][0]
    assert not p["findings"] and len(p["pixel_same"]) == 1
    assert any("ไฟล์ B เป็นภาพสแกน 300 dpi" in w for w in r["warnings"])
    assert "raster=True" in r["log_text"] and "raster (ภาพสแกนล้วน" in r["log_text"]
    assert "PIXEL_RASTER=True" in r["log_text"]
    assert "raster_check: A=" in r["log_text"] and "B=ภาพสแกน 300 dpi" in r["log_text"]
    assert "pymupdf=" in r["log_text"]


# ── 8 ต.ค.: ภาพสแกนอยู่ฝั่ง 🅰 (สถานีรอบ Friskies สลับไฟล์) · เหตุผลใน Log ─────────

def _statuses_swapped(pa, pb, marks):
    pc = pixverify.PairCheck(pa, pb, ZONE, ZONE)
    try:
        assert pc.ok, pc.ginfo
        return [pc.check("B", [m[0] * SENT - 3, m[1] * SENT - 3, m[2] * SENT + 3, m[3] * SENT + 3])["status"]
                for m in marks], pc.raster
    finally:
        pc.close()


def test_scan_on_side_a_is_symmetric(tmp_path):
    """สถานี 8 ต.ค. ส่งไฟล์สแกนเป็น 🅰 — ต้องได้ผลเท่ากับตอนอยู่ฝั่ง 🅱 (เทสต์เดิมใช้ 🅱 เสมอ)"""
    pv = _vec(tmp_path / "v.pdf")
    marks = _marks(pv)
    st, raster = _statuses_swapped(_scan(tmp_path / "s.pdf", pv, 300, 92), pv, marks)
    assert abs(raster["A"]["dpi"] - 300) < 1 and raster["B"] is None
    assert st.count("SAME") >= 0.9 * len(marks), st
    st, _ = _statuses_swapped(_scan(tmp_path / "m.pdf", pv, 300, 92, erase=marks), pv, marks)
    assert "SAME" not in st, st


def test_raster_check_says_why(tmp_path):
    pv = _vec(tmp_path / "v.pdf")
    with fitz.open(_scan(tmp_path / "s.pdf", pv, 300, 92)) as d:
        r, why = pixverify.raster_check(d[0], tuple(d[0].rect))
        assert r and "300 dpi" in why
    with fitz.open(pv) as d:
        r, why = pixverify.raster_check(d[0], tuple(d[0].rect))
        assert r is None and "ไม่มีภาพเดียวคลุมโซน" in why
    with fitz.open(_scan(tmp_path / "c.pdf", pv, 150, 92)) as d:
        r, why = pixverify.raster_check(d[0], tuple(d[0].rect))
        assert r is None and "150 dpi" in why

    def ink(p):
        p.insert_text((40, 40), "VECTOR", fontsize=12)
    with fitz.open(_scan(tmp_path / "x.pdf", pv, 300, 92, extra=ink)) as d:
        r, why = pixverify.raster_check(d[0], tuple(d[0].rect))
        assert r is None and "fill-text" in why
    # zone_raster คือผลส่วนแรกของ raster_check เสมอ
    with fitz.open(tmp_path / "s.pdf") as d:
        assert pixverify.zone_raster(d[0], tuple(d[0].rect)) == pixverify.raster_check(d[0], tuple(d[0].rect))[0]


def test_raster_check_error_is_reported_not_swallowed(tmp_path, monkeypatch):
    """ตรวจไม่ได้ ≠ ไม่ใช่ภาพสแกน — เดิมกลืน exception เงียบ (สถานี: raster=True แต่ไม่มีบรรทัด raster)"""
    pv = _vec(tmp_path / "v.pdf")
    sc = _scan(tmp_path / "s.pdf", pv, 300, 92)

    def boom(page, rect):
        raise RuntimeError("bboxlog broke")
    monkeypatch.setattr(pixverify, "raster_check", boom)
    pc = pixverify.PairCheck(sc, pv, ZONE, ZONE)
    try:
        assert pc.raster == {"A": None, "B": None}
        assert "RuntimeError" in pc.raster_why["A"] and "bboxlog broke" in pc.raster_why["A"]
    finally:
        pc.close()


def test_flag_off_has_no_raster_check(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PIXEL_RASTER", False)
    pv = _vec(tmp_path / "v.pdf")
    pc = pixverify.PairCheck(_scan(tmp_path / "s.pdf", pv, 300, 92), pv, ZONE, ZONE)
    try:
        assert pc.raster_why == {} and "raster" not in pc.ginfo
    finally:
        pc.close()


def test_warp_guard_rejects_degenerate_stretch():
    """ECC เคยยืดแนวตั้ง 4.35 เท่า (จุดบนตัว i หลุดกรอบ) — ต้องถูกปฏิเสธ · เศษเล็ก ๆ ผ่าน"""
    ok = np.array([[1.004, 0.001, 6.3], [-0.002, 0.998, 5.8]], np.float32)
    bad = np.array([[0.97, 0.01, 6.7], [-0.007, 4.346, -59.6]], np.float32)
    shear = np.array([[1.0, 0.2, 6.0], [0.0, 1.0, 6.0]], np.float32)
    far = np.array([[1.0, 0.0, 30.0], [0.0, 1.0, 6.0]], np.float32)
    assert pixverify._warp_ok(ok, (6, 6), (44, 43), (32, 31))
    assert not pixverify._warp_ok(bad, (6, 6), (44, 43), (32, 31))
    assert not pixverify._warp_ok(shear, (6, 6), (44, 43), (32, 31))
    assert not pixverify._warp_ok(far, (6, 6), (44, 43), (32, 31))


def test_works_on_pymupdf_without_rect_get_area(tmp_path, monkeypatch):
    """สถานี (PyMuPDF 1.26.5) ไม่มี ``Rect.get_area`` ⇒ ``raster_check`` เคยโยน AttributeError ทุกครั้ง
    ชั้นภาพสแกนจึงไม่เคยทำงานบนสถานี (run_005: ``raster_check: A=ตรวจไม่ได้ (AttributeError …)``)"""
    import fitz
    monkeypatch.delattr(fitz.Rect, "get_area", raising=False)
    monkeypatch.delattr(fitz.Rect, "getArea", raising=False)
    src = _vec(str(tmp_path / "v.pdf"))
    scan = _scan(str(tmp_path / "s.pdf"), src, dpi=300)
    with fitz.open(scan) as d:
        r, why = pixverify.raster_check(d[0], tuple(d[0].rect))
    assert r is not None and r["dpi"] >= 299, why
    assert pixverify._rect_area(fitz.Rect(0, 0, 2, 3)) == 6.0
    assert pixverify._rect_area(fitz.Rect(5, 5, 2, 3)) == 0.0          # กลับด้าน/ว่าง = 0


def test_no_version_sensitive_rect_area_calls():
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "artwork_v2")
    for fn in os.listdir(root):
        if fn.endswith(".py"):
            src = open(os.path.join(root, fn), encoding="utf-8").read()
            assert ".get_area(" not in src and ".getArea(" not in src, fn


# ── 9 ต.ค.: คู่ภาพสแกนบนพื้นสีกลาง — เส้นบาง/จุดเล็กที่หายต้องไม่ได้ "ภาพเหมือน" ─────────────
# ที่มา: Friskies จริง (ฉลากพื้นเขียว) — ลบขีด "/" ของ % ฝั่งสแกน ⇒ SAME · ลบเครื่องหมายเล็ก 463 ครั้ง พลาด 52

_BG, _INK = 0.55, 0.25        # ตัวอักษรเข้มบนพื้นเทากลาง (เหมือนฉลากสีเขียวในภาพขาวดำ)


def _vec_gray(path, fs=7):
    d = fitz.open()
    p = d.new_page(width=W_PT, height=H_PT)
    p.draw_rect(p.rect, color=None, fill=(_BG,) * 3)
    rnd = random.Random(5)
    for i in range(14):
        t = "Lot %d.%d%% l1|/ fat %d%% i!l %d/%d -1l" % (
            rnd.randint(1, 99), rnd.randint(0, 9), rnd.randint(1, 99), rnd.randint(1, 9), rnd.randint(1, 9))
        p.insert_text((20, 30 + 18 * i), t, fontsize=fs, color=(_INK,) * 3)
    d.save(str(path))
    return str(path)


def _thin_marks(src, dpi=300):
    """เส้นบาง (ก้าน l/1/| · ขีด / · - · จุด) เป็นกรอบ pt — กว้าง ≤ 3 px ที่ 300 dpi"""
    with fitz.open(src) as d:
        pix = d[0].get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
    g = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, 3)[:, :, 0]
    n, _, st, _ = cv2.connectedComponentsWithStats((g < 110).astype(np.uint8), 8)
    k = dpi / 72
    cand = [((x - 1) / k, (y - 1) / k, (x + w + 1) / k, (y + h + 1) / k)
            for x, y, w, h, a in (st[i] for i in range(1, n)) if min(w, h) <= 3 and a >= 4 and max(w, h) <= 30]
    random.Random(2).shuffle(cand)
    pick = []
    for c in cand:
        if all(abs(c[0] - p[0]) > 25 or abs(c[1] - p[1]) > 12 for p in pick):
            pick.append(c)
        if len(pick) >= 30:
            break
    assert len(pick) >= 25
    return pick


def _scan_gray(dst, src, erase=(), dpi=300, q=92):
    with fitz.open(src) as s:
        pix = s[0].get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, 3).copy()
    k = dpi / 72
    for (x0, y0, x1, y1) in erase:       # ลบด้วยสีพื้น (ไม่ใช่สีขาว) — ของจริงไม่มีรอยขาว
        a[int(y0 * k):int(np.ceil(y1 * k)), int(x0 * k):int(np.ceil(x1 * k))] = int(round(_BG * 255))
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(a, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, q])
    d = fitz.open()
    p = d.new_page(width=W_PT, height=H_PT)
    p.insert_image(p.rect, stream=buf.tobytes())
    d.save(str(dst))
    return str(dst)


@pytest.mark.parametrize("fs", [7, 6])
@pytest.mark.parametrize("scan_side", ["A", "B"])
def test_thin_strokes_erased_on_a_gray_background_scan_are_never_same(tmp_path, fs, scan_side):
    v = _vec_gray(tmp_path / "v.pdf", fs)
    marks = _thin_marks(v)
    pair = (lambda s: (s, v)) if scan_side == "A" else (lambda s: (v, s))
    st, raster = _statuses(*pair(_scan_gray(tmp_path / "s.pdf", v)), marks)
    assert raster[scan_side] is not None
    assert st.count("SAME") >= 0.9 * len(marks), st          # ไม่ได้ลบอะไร ⇒ ยังพับได้
    st, _ = _statuses(*pair(_scan_gray(tmp_path / "m.pdf", v, erase=marks)), marks)
    assert "SAME" not in st, st


def test_old_raster_thresholds_did_miss_thin_strokes(tmp_path, monkeypatch):
    """ยืนยันว่าเทสต์ข้างบนจับปัญหาจริง — ปิดทั้งสองธง ⇒ เส้นบางที่หายได้ SAME"""
    monkeypatch.setattr(config, "PIXEL_RASTER_STRICT_GRAY", False)
    monkeypatch.setattr(config, "PIXEL_RASTER_INK_T", 0)
    v = _vec_gray(tmp_path / "v.pdf", 7)
    marks = _thin_marks(v)
    st, _ = _statuses(_scan_gray(tmp_path / "m.pdf", v, erase=marks), v, marks)
    assert st.count("SAME") >= 3, st


def test_strict_gray_alone_fixes_7pt(tmp_path, monkeypatch):
    """การวัดสีที่ตำแหน่งเดิม (ไม่ยอมเลื่อน) เป็นตัวแก้หลัก — แยกออกจากเกณฑ์หมึก"""
    monkeypatch.setattr(config, "PIXEL_RASTER_INK_T", 0)
    v = _vec_gray(tmp_path / "v.pdf", 7)
    marks = _thin_marks(v)
    st, _ = _statuses(_scan_gray(tmp_path / "m.pdf", v, erase=marks), v, marks)
    assert "SAME" not in st, st


@pytest.mark.parametrize("flags", [(True, 40.0), (False, 0)])
def test_raster_strictness_never_touches_vector_pairs(tmp_path, monkeypatch, flags):
    pa = _vec_gray(tmp_path / "a.pdf", 7)
    pb = _vec_gray(tmp_path / "b.pdf", 7)
    boxes = [(m[0] * SENT - 3, m[1] * SENT - 3, m[2] * SENT + 3, m[3] * SENT + 3) for m in _thin_marks(pa)[:10]]
    out = {}
    for on in (True, False):
        monkeypatch.setattr(config, "PIXEL_RASTER_STRICT_GRAY", on and flags[0])
        monkeypatch.setattr(config, "PIXEL_RASTER_INK_T", flags[1] if on else 0)
        pc = pixverify.PairCheck(pa, pb, ZONE, ZONE)
        out[on] = [(c["status"], c["inkA"], c["inkB"], len(c["sig"]))
                   for c in (pc.check("A", b) for b in boxes)]
        pc.close()
    assert out[True] == out[False]


def test_compare_defaults_are_the_old_path():
    rnd = np.random.RandomState(0)
    a = (rnd.rand(60, 90) * 255).astype(np.uint8)
    b = np.roll(a, 1, axis=1)
    c0 = pixverify._compare(a, b, 300.0)
    c1 = pixverify._compare(a, b, 300.0, gray_tol=pixverify.TOL, ink_t=pixverify.INK_T)
    assert [x["area"] for x in c0["sig"]] == [x["area"] for x in c1["sig"]] and c0["inkA"] == c1["inkA"]


def _run_number_misread(tmp_path, monkeypatch):
    pa = _vec(tmp_path / "a.pdf")
    pb = _scan(tmp_path / "b.pdf", pa, 300, 92)
    rows = _rows()
    bad = rows[3].replace("vit B3", "vit B8")         # OCR อ่านตัวเลขผิดฝั่งภาพสแกน (ภาพเหมือนกันทุกจุด)
    assert bad != rows[3]

    def fake(groups, poster=None, key=None):
        res = {}
        for gi, g in enumerate(groups):
            for it in g:
                W, H = Image.open(io.BytesIO(it["jpeg"])).size
                lines = _line_boxes(pa, W, H)
                if it["id"].endswith("b"):
                    lines = [(t.replace(rows[3], bad), b, c) for t, b, c in lines]
                res[it["id"]] = {"ok": True, "error": "", "fta": fta_from_lines(lines, W, H),
                                 "request_index": gi}
        return {"results": res, "calls": [{"index": 0, "phase": "main", "images": [],
                                           "json_bytes": 10, "status": 200, "attempts": 1,
                                           "ms": 1, "error": "", "at": "t"}]}
    jid = jobs.create(("a.pdf", open(pa, "rb").read()), ("b.pdf", open(pb, "rb").read()))["id"]
    keystore.save(KEY)
    monkeypatch.setattr(vision_client, "annotate", fake)
    return pipeline.run(jid, [{"a": {"page": 0, "bbox": [0, 0, 1, 1]},
                               "b": {"page": 0, "bbox": [0, 0, 1, 1]}}])


def test_number_on_a_scan_pair_is_never_folded_as_same(tmp_path, monkeypatch):
    r = _run_number_misread(tmp_path, monkeypatch)
    p = r["pairs"][0]
    assert not p.get("pixel_same")
    f = next(f for f in p["findings"] if f["class"] == "NUMBER")
    assert f["pixel"]["status"] == "UNVERIFIABLE" and f["pixel"].get("raster_number")
    assert any("ไม่พับ" in n for n in f["notes"])
    assert "raster_number_kept=1" in r["log_text"]
    assert "PIXEL_RASTER_KEEP_NUMBER=True" in r["log_text"]


def test_number_guard_flag_off_folds_as_before(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PIXEL_RASTER_KEEP_NUMBER", False)
    r = _run_number_misread(tmp_path, monkeypatch)
    p = r["pairs"][0]
    assert [f["class"] for f in p["pixel_same"]] == ["NUMBER"]
    assert "raster_number_kept" not in r["log_text"]


def test_lower_ink_threshold_only_on_low_contrast_crops(tmp_path, monkeypatch):
    """พื้นขาว: เกณฑ์หมึกเดิม (ลดแล้วสแกน 200 dpi q75 พับได้น้อยลง 2/30 โดยไม่ได้อะไร) · พื้นเทา: ลด"""
    pa = _vec(tmp_path / "a.pdf")
    marks = _marks(pa)
    s = _scan(tmp_path / "s.pdf", pa, 200, 75)
    out = {}
    for t in (40.0, 0):
        monkeypatch.setattr(config, "PIXEL_RASTER_INK_T", t)
        out[t] = _statuses(pa, s, marks)[0]
    assert out[40.0] == out[0]
    v = _vec_gray(tmp_path / "v.pdf", 7)
    pc = pixverify.PairCheck(v, _scan_gray(tmp_path / "g.pdf", v), ZONE, ZONE)
    m = _thin_marks(v)[0]
    pa_, pb_, _ = pc._crops("A", (m[0] * SENT - 3, m[1] * SENT - 3, m[2] * SENT + 3, m[3] * SENT + 3))
    pc.close()
    assert pixverify._contrast(pa_, pb_) < pixverify.RASTER_LOW_CONTRAST
