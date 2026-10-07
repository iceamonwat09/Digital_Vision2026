"""Artwork V2 — หลักฐานภาพ (``artwork_v2/pixverify.py`` · 7 ต.ค. ข้อสรุปทีม)

สิ่งที่ล็อก (กฎเหล็กข้อ 2 — "ภาพเหมือนกัน" ต้องไม่กลืนของจริง):
* หมึกเหมือนกัน ⇒ SAME (แม้ OCR อ่านต่าง) · หมึกต่าง ⇒ **ไม่มีทาง SAME** — รวมถึงความต่าง
  ระดับจุดเดียว (``i`` → ``ı``) และจุดทศนิยม (``1.5`` → ``15``)
* พิมพ์คนละขนาด/วางคนละที่ (B เล็กกว่า 10% · ลากโซนต่างกัน) ยังทาบได้
* โหมดบรรทัด: กรอบตกบนคำที่เหมือน แต่บรรทัดเดียวกันต่าง ⇒ ไม่ SAME
* ไม่ใช่ PDF / ปิดธง / ทาบภาพไม่ได้ ⇒ ผลเดิมทุกจุด
* ทั้งรอบ: OCR ปลอมบอกว่าต่าง แต่หมึกเหมือน ⇒ พับลง ``pixel_same`` + ภาพหลักฐาน (ไม่ลบ) ·
  ของจริงคงแดง + ป้าย "ภาพยืนยันว่าต่าง"
"""

from __future__ import annotations

import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from artwork_v2_fake import fta_from_lines  # noqa: E402

from artwork_v2 import config, imaging, jobs, keystore, pipeline, pixverify, vision_client  # noqa: E402

fitz = pytest.importorskip("fitz")
cv2 = pytest.importorskip("cv2")
from PIL import Image  # noqa: E402

KEY = "AIza" + "D" * 35
MM = 72 / 25.4
ROWS = ["Nutrition Facts  Serving size 1/2 cup (100g)",
        "Sodium 475 mg 20%   Total Fat 7 g 10%",
        "Ingredients: tuna, sunflower oil, water, salt.",
        "D-calcium pantothenate, thiamine, riboflavin 1.5 mg",
        "Produced in Thailand for John West Foods Ltd,",
        "Storage: cool, dry in ventilated place."]


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
    os.makedirs(config.JOBS_DIR)
    yield


def _pdf(path, rows=ROWS, scale=1.0, dx_mm=0.0, dy_mm=0.0):
    doc = fitz.open()
    pg = doc.new_page(width=150 * MM, height=70 * MM)
    for i, t in enumerate(rows):
        pg.insert_text(((10 + dx_mm) * MM, (14 + dy_mm + i * 7 * scale) * MM), t,
                       fontsize=7.5 * scale)
    doc.save(str(path))
    return str(path)


ZONE = [0.03, 0.08, 0.94, 0.84]


def _zone(path, bbox=ZONE):
    src = imaging.Source(path)
    img, _ = src.render_zone(0, bbox)
    return {"page": 0, "bbox": bbox, "W": img.shape[1], "H": img.shape[0]}


def _word_box(path, z, word, nth=0):
    """กรอบของคำ (พิกัดภาพโซนที่ส่ง)"""
    with fitz.open(path) as d:
        pg = d[0]
        r = pg.search_for(word)[nth]
        pw, ph = pg.rect.width, pg.rect.height
    x, y, w, h = z["bbox"]
    sx, sy = z["W"] / (w * pw), z["H"] / (h * ph)
    return [(r.x0 - x * pw) * sx, (r.y0 - y * ph) * sy, (r.x1 - x * pw) * sx, (r.y1 - y * ph) * sy]


def _line_boxes(path, z):
    """กรอบบรรทัดทั้งหมด (พิกัดภาพโซนที่ส่ง) — เหมือนบรรทัด OCR"""
    out = []
    with fitz.open(path) as d:
        pg = d[0]
        pw, ph = pg.rect.width, pg.rect.height
        for b in pg.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                r = ln["bbox"]
                x, y, w, h = z["bbox"]
                sx, sy = z["W"] / (w * pw), z["H"] / (h * ph)
                txt = "".join(s["text"] for s in ln["spans"])
                out.append((txt, [(r[0] - x * pw) * sx, (r[1] - y * ph) * sy,
                                  (r[2] - x * pw) * sx, (r[3] - y * ph) * sy]))
    return out


def _finding(pa, za, pb, zb, wa, wb, line_a=None, line_b=None):
    return {"a": {"box": _word_box(pa, za, wa), "line": line_a},
            "b": {"box": _word_box(pb, zb, wb), "line": line_b}}


def _check(tmp_path, rows_b, wa, wb, scale=1.0, bbox_b=ZONE, line_mode=False, row=None):
    pa = _pdf(tmp_path / "a.pdf")
    pb = _pdf(tmp_path / "b.pdf", rows=rows_b, scale=scale)
    za, zb = _zone(pa), _zone(pb, bbox_b)
    pc = pixverify.PairCheck(pa, pb, za, zb)
    assert pc.ok, pc.ginfo
    lines = {"a": [], "b": []}
    if line_mode:
        lines = {"a": [{"text": t, "box": b} for t, b in _line_boxes(pa, za)],
                 "b": [{"text": t, "box": b} for t, b in _line_boxes(pb, zb)]}
    f = _finding(pa, za, pb, zb, wa, wb, row if line_mode else None, row if line_mode else None)
    try:
        return pixverify.verify(pc, f, lines)[0], pc
    finally:
        pc.close()


def _edit(i, old, new):
    rows = list(ROWS)
    assert old in rows[i]
    rows[i] = rows[i].replace(old, new, 1)
    return rows


# ── ระดับจุดเดียว ──────────────────────────────────────────────────────

def test_identical_ink_is_same_even_if_ocr_disagreed(tmp_path):
    st, _ = _check(tmp_path, ROWS, "20%", "20%")
    assert st == "SAME"


@pytest.mark.parametrize("row,old,new,wa,wb,want", [
    (1, "20%", "24%", "20%", "24%", {"DIFF"}),               # ตัวเลข (Sodium ของ John West)
    (3, "1.5 mg", "15 mg", "1.5", "15", {"DIFF"}),           # จุดทศนิยมหาย
    (4, "Ltd,", "Ltd", "Ltd,", "Ltd", {"DIFF"}),             # คอมมาหาย
    # ตัวพิมพ์ใหญ่กว้างกว่า ⇒ ข้อความหลังจากนั้นเลื่อน ⇒ อาจทาบไม่ติด (UNVERIFIABLE = คงผลเดิม)
    (3, "D-calcium", "D-Calcium", "D-calcium", "D-Calcium", {"DIFF", "UNVERIFIABLE"}),
])
def test_any_real_ink_change_is_never_same(tmp_path, row, old, new, wa, wb, want):
    st, _ = _check(tmp_path, _edit(row, old, new), wa, wb)
    assert st in want and st != "SAME", (old, new, st)


def _erase_dot(path, word, nth_char):
    """ลบจุดบนตัว ``i`` ตัวเดียวด้วยสี่เหลี่ยมขาวเล็ก ๆ (แทนการแก้จุดของอักษรอาหรับ)"""
    doc = fitz.open(path)
    pg = doc[0]
    hit = pg.search_for(word)[0]
    for b in pg.get_text("rawdict")["blocks"]:
        for ln in b.get("lines", []):
            for sp in ln["spans"]:
                chars = [c for c in sp["chars"] if fitz.Rect(c["bbox"]).intersects(hit)]
                if len(chars) > nth_char and chars[nth_char]["c"] == "i":
                    r = fitz.Rect(chars[nth_char]["bbox"])
                    top = fitz.Rect(r.x0 - 0.2, r.y0, r.x1 + 0.2, r.y0 + r.height * 0.42)
                    pg.draw_rect(top, color=None, fill=(1, 1, 1))
                    out = path.replace(".pdf", "_nodot.pdf")
                    doc.save(out)
                    return out, top
    raise AssertionError("ไม่พบตัว i")


def test_single_dot_removed_is_diff(tmp_path):
    """ความต่างระดับ "จุดเดียว" (เช่น ج→ح ي→ى บนฉลากอาหรับ) ต้องไม่ถูกพับ"""
    pa = _pdf(tmp_path / "a.pdf")
    pb, top = _erase_dot(_pdf(tmp_path / "b.pdf"), "thiamine", 2)
    # หมึกที่ถูกลบจริง (จุดของตัว i) — วัดจากภาพ 1200 dpi ของต้นฉบับในกรอบที่ลบ
    with fitz.open(pa) as d:
        pix = d[0].get_pixmap(dpi=1200, clip=top, colorspace=fitz.csGRAY)
    g = __import__("numpy").frombuffer(pix.samples, "uint8")
    ink_mm2 = float((g < 128).sum()) * (25.4 / 1200) ** 2
    assert 0.0 < ink_mm2 < 0.1, ink_mm2   # ระดับเดียวกับจุดอาหรับที่วัดบน John West (0.0045–0.08 mm²)
    za, zb = _zone(pa), _zone(pb)
    pc = pixverify.PairCheck(pa, pb, za, zb)
    f = {"a": {"box": _word_box(pa, za, "thiamine,"), "line": None},
         "b": {"box": _word_box(pa, zb, "thiamine,"), "line": None}}
    st, ch = pixverify.verify(pc, f, {"a": [], "b": []})
    pc.close()
    assert st == "DIFF", [(c["status"], c["ncc"], len(c["sig"])) for c in ch]


def test_smaller_print_is_still_registered(tmp_path):
    """AvoDerm 🅱 พิมพ์เล็กกว่า 10% — ทาบด้วยสเกลจริงแล้วยัง SAME/DIFF ถูก"""
    st, pc = _check(tmp_path, ROWS, "Storage:", "Storage:", scale=0.9,
                    bbox_b=[0.03, 0.08, 0.94, 0.8])
    assert 0.85 < pc.gscale < 0.95, pc.ginfo
    assert st == "SAME"
    st, _ = _check(tmp_path, _edit(1, "20%", "24%"), "20%", "24%", scale=0.9,
                   bbox_b=[0.03, 0.08, 0.94, 0.8])
    assert st == "DIFF"


def test_zone_drawn_differently_still_registers(tmp_path):
    st, _ = _check(tmp_path, ROWS, "thiamine,", "thiamine,", bbox_b=[0.05, 0.06, 0.92, 0.86])
    assert st == "SAME"


def test_line_mode_refuses_same_when_the_line_differs_elsewhere(tmp_path):
    """กรอบของ Vision ตกบนคำที่เหมือน ("Sodium") แต่บรรทัดเดียวกันต่าง (20% → 24%)"""
    rows = _edit(1, "20%", "24%")
    st, _ = _check(tmp_path, rows, "Sodium", "Sodium", line_mode=False)
    assert st == "SAME"                                   # ดูแค่กรอบ = เห็นเหมือน
    st, _ = _check(tmp_path, rows, "Sodium", "Sodium", line_mode=True, row=1)
    assert st == "DIFF"                                   # ทั้งบรรทัด ⇒ ไม่ยอมพับ


def test_untrusted_line_index_is_unverifiable(tmp_path):
    """เลขบรรทัดที่ไม่ครอบกรอบของจุดต่าง ⇒ เชื่อไม่ได้ ⇒ ไม่พับ"""
    st, _ = _check(tmp_path, ROWS, "Sodium", "Sodium", line_mode=True, row=5)
    assert st == "UNVERIFIABLE"


def test_text_only_on_one_side_is_diff(tmp_path):
    rows = list(ROWS)
    rows[5] = "Storage: cool, dry in ventilated place. Free from hydrogenated oils"
    pa = _pdf(tmp_path / "a.pdf", rows=rows)
    pb = _pdf(tmp_path / "b.pdf")
    za, zb = _zone(pa), _zone(pb)
    pc = pixverify.PairCheck(pa, pb, za, zb)
    f = {"a": {"box": _word_box(pa, za, "hydrogenated"), "line": None},
         "b": {"box": None, "line": None}}
    st, _ = pixverify.verify(pc, f, {"a": [], "b": []})
    pc.close()
    assert st == "DIFF"


# ── ระดับรอบ (run) ───────────────────────────────────────────────────

def _pairs_for(pa, pb):
    srcs = {"a": imaging.Source(pa), "b": imaging.Source(pb)}
    za, zb = _zone(pa), _zone(pb)
    pr = {"n": 1, "sides": {"a": {"page": 0, "bbox": ZONE, "sent_px": [za["W"], za["H"]]},
                            "b": {"page": 0, "bbox": ZONE, "sent_px": [zb["W"], zb["H"]]}},
          "lines": {"a": [], "b": []},
          "findings": [dict(_finding(pa, za, pb, zb, "20%", "20%"), id=1, severity="red",
                            notes=[], **{"class": "NUMBER"})]}
    return [pr], srcs


def test_run_flag_off_is_a_no_op(tmp_path, monkeypatch):
    pa, pb = _pdf(tmp_path / "a.pdf"), _pdf(tmp_path / "b.pdf")
    pairs, srcs = _pairs_for(pa, pb)
    before = repr(pairs)
    monkeypatch.setattr(config, "PIXEL_VERIFY", False)
    log = pixverify.run(pairs, srcs, str(tmp_path), [])
    assert log["enabled"] is False and repr(pairs) == before


def test_run_photo_pair_is_left_untouched(tmp_path):
    pa = _pdf(tmp_path / "a.pdf")
    png = tmp_path / "b.png"
    Image.new("RGB", (800, 400), "white").save(png)
    pairs, srcs = _pairs_for(pa, pa)
    srcs["b"] = imaging.Source(str(png))
    before = repr(pairs)
    log = pixverify.run(pairs, srcs, str(tmp_path), [])
    assert "ไม่ใช่ PDF" in log["reason"] and repr(pairs) == before


def test_run_folds_same_with_evidence_and_never_deletes(tmp_path):
    pa, pb = _pdf(tmp_path / "a.pdf"), _pdf(tmp_path / "b.pdf")
    pairs, srcs = _pairs_for(pa, pb)
    os.makedirs(tmp_path / "img")
    log = pixverify.run(pairs, srcs, str(tmp_path), [])
    pr = pairs[0]
    assert pr["findings"] == [] and len(pr["pixel_same"]) == 1
    f = pr["pixel_same"][0]
    assert f["severity"] == "pixel_same" and f["pixel"]["was"] == "red"
    assert f["pixel"]["status"] == "SAME" and os.path.isfile(tmp_path / "img" / f["pixel"]["evidence"])
    assert log["same"] == 1 and log["diff"] == 0


def test_blank_zones_cannot_register_and_change_nothing(tmp_path):
    pa, pb = _pdf(tmp_path / "a.pdf", rows=[]), _pdf(tmp_path / "b.pdf", rows=[])
    pairs, srcs = _pairs_for(_pdf(tmp_path / "ta.pdf"), _pdf(tmp_path / "tb.pdf"))
    srcs = {"a": imaging.Source(pa), "b": imaging.Source(pb)}
    before = repr(pairs[0]["findings"])
    pixverify.run(pairs, srcs, str(tmp_path), [])
    assert repr(pairs[0]["findings"]) == before and not pairs[0].get("pixel_same")


# ── ทั้ง pipeline (Vision ปลอมที่วางกรอบตรงหมึกจริง) ──────────────────────

def _fake_vision(path_a, path_b, text_b_override=None):
    def fake(groups, poster=None, key=None):
        res = {}
        for gi, g in enumerate(groups):
            for it in g:
                W, H = Image.open(io.BytesIO(it["jpeg"])).size
                path = path_a if it["id"].endswith("a") else path_b
                z = {"page": 0, "bbox": ZONE, "W": W, "H": H}
                lines = []
                for t, b in _line_boxes(path, z):
                    if it["id"].endswith("b") and text_b_override:
                        t = text_b_override.get(t, t)
                    lines.append((t, tuple(b), 0.98))
                res[it["id"]] = {"ok": True, "error": "", "fta": fta_from_lines(lines, W, H),
                                 "request_index": gi}
        return {"results": res, "calls": [{"index": 0, "phase": "main", "images": [],
                                           "json_bytes": 10, "status": 200, "attempts": 1,
                                           "ms": 1, "error": "", "at": "t"}]}
    return fake


PAIRS = [{"a": {"page": 0, "bbox": ZONE}, "b": {"page": 0, "bbox": ZONE}}]


def _job(pa, pb):
    return jobs.create(("a.pdf", open(pa, "rb").read()), ("b.pdf", open(pb, "rb").read()))["id"]


def test_pipeline_ocr_misread_on_identical_ink_is_folded_and_passes(tmp_path, monkeypatch):
    pa, pb = _pdf(tmp_path / "a.pdf"), _pdf(tmp_path / "b.pdf")
    jid = _job(pa, pb)
    keystore.save(KEY)
    # OCR "อ่านผิด" ฝั่ง B (เหมือนจุดอาหรับเพี้ยนบนสถานี) ทั้งที่หมึกเหมือนกัน
    monkeypatch.setattr(vision_client, "annotate",
                        _fake_vision(pa, pb, {ROWS[1]: ROWS[1].replace("20%", "24%")}))
    r = pipeline.run(jid, PAIRS)
    p = r["pairs"][0]
    assert [f["severity"] for f in p["findings"]] == []
    assert len(p["pixel_same"]) == 1 and p["pixel_same"][0]["class"] == "NUMBER"
    assert r["verdict"] == "PASS"
    assert any("ภาพเหมือนกันทุกพิกเซล" in x for x in r["reasons"])
    assert "[PIXEL] enabled=True" in r["log_text"] and "pixel_same —" in r["log_text"]
    # ภาพหลักฐานเปิดผ่านเว็บได้
    from flask import Flask
    from artwork_v2.routes import artwork_v2_bp
    app = Flask(__name__)
    app.register_blueprint(artwork_v2_bp)
    ev = p["pixel_same"][0]["pixel"]["evidence"]
    with app.test_client() as c:
        resp = c.get("/api/artwork_v2/jobs/%s/runs/%s/img/%s" % (jid, r["run"], ev))
        assert resp.status_code == 200 and resp.data[:2] == b"\xff\xd8"
        assert c.get("/api/artwork_v2/jobs/%s/runs/%s/img/pv1.png" % (jid, r["run"])).status_code == 404


def test_pipeline_real_change_stays_red_with_pixel_confirmation(tmp_path, monkeypatch):
    pa = _pdf(tmp_path / "a.pdf")
    pb = _pdf(tmp_path / "b.pdf", rows=_edit(1, "20%", "24%"))
    jid = _job(pa, pb)
    keystore.save(KEY)
    monkeypatch.setattr(vision_client, "annotate", _fake_vision(pa, pb))
    r = pipeline.run(jid, PAIRS)
    p = r["pairs"][0]
    reds = [f for f in p["findings"] if f["severity"] == "red"]
    assert reds and all(f["pixel"]["status"] == "DIFF" for f in reds)
    assert any("ภาพยืนยันว่าต่าง" in n for f in reds for n in f["notes"])
    assert not p.get("pixel_same") and r["verdict"] == "FAIL"


def test_pipeline_flag_off_keeps_the_ocr_finding(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PIXEL_VERIFY", False)
    pa, pb = _pdf(tmp_path / "a.pdf"), _pdf(tmp_path / "b.pdf")
    jid = _job(pa, pb)
    keystore.save(KEY)
    monkeypatch.setattr(vision_client, "annotate",
                        _fake_vision(pa, pb, {ROWS[1]: ROWS[1].replace("20%", "24%")}))
    r = pipeline.run(jid, PAIRS)
    assert r["verdict"] == "FAIL" and not r["pairs"][0].get("pixel_same")
    assert "pixel" not in r["pairs"][0]["findings"][0]
