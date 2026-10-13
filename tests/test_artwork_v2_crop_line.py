"""Artwork V2 — ครอปที่ส่ง AI (โหมด image) เห็นเฉพาะบรรทัดของจุด (``AI_IMAGE_CROP_LINE_ONLY`` · 13 ต.ค.)

ที่มา: King Oscar F3 (Log สถานี ``tests/data/artwork_v2/same_change/kingoscar_run001_log.txt``) — ``zagęszczajaca`` ↔
``zagęszczająca`` แต่ครอปเผื่อบน-ล่าง 1.5 บรรทัด (4-5 บรรทัดในครอปเดียว) ⇒ Gemini อ่าน "mielona/mielony" ของบรรทัดล่าง
(John West ก่อนหน้า: F11/F18/F22 ได้คำตอบของแถว Sodium ทั้งที่จุดอยู่คนละแถว)

สิ่งที่ล็อก:
  ① ``crop_rect`` — แนวขวางบรรทัด = กรอบที่ส่งมา + ระยะเผื่อเล็กน้อย · แนวตามบรรทัดเท่าเดิมทุกพิกเซล · ข้อความแนวตั้งสลับแกน
  ② ``_line_band`` — ใช้ความสูงตัวอักษรของบรรทัด (คำไม่มีตัวสูงไม่ถูกตัดหัว) · หักความหนาที่เกิดจากภาพเอียง · บรรทัดที่ Vision
     รวมหลายแถว/ไม่มีบรรทัด/การ์ดโค้ง ⇒ กรอบเดิม
  ③ ข้อมูลสถานี — ครอปของ F3 ไม่มีกลางบรรทัดข้างเคียง (เดิมมี 3 บรรทัด) · ภาพถ่ายเอียงที่กรอบบรรทัดซ้อนกัน
  ④ ธงปิด = ครอปเดิมทุกไบต์ · เส้นทางเรนเดอร์ใหม่จาก PDF ใช้แถบเดียวกัน · Log
"""

from __future__ import annotations

import copy
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from artwork_v2_fake import fta_from_lines, load_log  # noqa: E402

from artwork_v2 import ai_review, compare, config, diaglog, textmodel  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KO1 = os.path.join(ROOT, "tests", "data", "artwork_v2", "same_change", "kingoscar_run001_log.txt")


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setattr(config, "AI_IMAGE_CROP_LINE_ONLY", True)
    monkeypatch.setattr(config, "AI_IMAGE_CROP_LINE_PAD", 0.15)
    monkeypatch.setattr(config, "AI_IMAGE_CROP_VERTICAL", True)
    monkeypatch.setattr(config, "AI_IMAGE_CROP_ROTATE", True)


# ── ① crop_rect ─────────────────────────────────────────────────────────

def test_crop_rect_line_only_keeps_width_and_trims_height():
    box, lh = [400, 500, 460, 520], 20.0
    old = ai_review.crop_rect(box, lh, 2000, 2000)
    new = ai_review.crop_rect(box, lh, 2000, 2000, line_only=True)
    assert (old[0], old[2]) == (new[0], new[2])                 # คำข้างเคียงซ้าย-ขวาเท่าเดิม
    assert old[3] - old[1] == 20 + 2 * 30                         # เดิม: ±1.5 บรรทัด
    assert new[3] - new[1] == 20 + 2 * 3                          # ใหม่: ±0.15 บรรทัด
    assert new[1] <= 500 and new[3] >= 520                        # ตัวจุดอยู่ในครอปครบ


def test_crop_rect_line_only_vertical_swaps_axes():
    box, lh = [500, 400, 520, 460], 20.0                          # ข้อความแนวตั้ง: ความหนา = กว้าง
    r = ai_review.crop_rect(box, lh, 2000, 2000, vertical=True, line_only=True)
    assert r[2] - r[0] == 20 + 2 * 3 and r[1] <= 400 and r[3] >= 460
    h = ai_review.crop_rect(box, lh, 2000, 2000, vertical=True)
    assert (h[1], h[3]) == (r[1], r[3])


def test_crop_rect_min_pad_two_pixels():
    r = ai_review.crop_rect([10, 100, 30, 104], 4.0, 1000, 1000, line_only=True)
    assert r[3] - r[1] == 4 + 2 * 2


# ── ② _line_band ────────────────────────────────────────────────────────

def _f(line=0, s="a"):
    return {s: {"line": line}}


def test_band_uses_line_height_for_a_short_word():
    lines = {"a": [{"box": [100, 500, 700, 524], "angle": 0.0}]}
    box, lh = ai_review._line_band(_f(), "a", lines, [300, 506, 330, 520], 14.0, False)   # "ana" สูงแค่ x-height
    assert box == [300.0, 500.0, 330.0, 524.0] and lh == 24.0


def test_band_removes_tilt_thickness_and_stays_centred_on_the_word():
    # ภาพถ่าย King Oscar B056: ยาว 1,118 px เอียง 1° ⇒ กรอบหนา 64 ทั้งที่ตัวอักษรสูง ~45
    lines = {"b": [{"box": [1390, 2377, 2508, 2441], "angle": 359.0}]}
    box, lh = ai_review._line_band(_f(s="b"), "b", lines, [1880, 2392, 2000, 2428], 36.0, False)
    assert 44.0 < lh < 45.5
    assert box[1] == pytest.approx(2410 - lh / 2) and box[3] == pytest.approx(2410 + lh / 2)


def test_band_never_leaves_the_line_box():
    lines = {"a": [{"box": [100, 500, 1100, 540], "angle": 1.0}]}
    box, lh = ai_review._line_band(_f(), "a", lines, [1000, 500, 1060, 512], 12.0, False)   # คำชิดขอบบน
    assert box[1] >= 500 - 1e-6 and box[3] <= 540 + 1e-6


def test_band_merged_multiline_and_missing_line_keep_the_word_box():
    lines = {"a": [{"box": [497, 1489, 1090, 1550], "angle": 0.0}]}        # A061: สองแถวรวมเป็นบรรทัดเดียว
    wb = [500, 1490, 700, 1515]
    assert ai_review._line_band(_f(), "a", lines, wb, 25.0, False) == (wb, 25.0)
    assert ai_review._line_band(_f(line=None), "a", lines, wb, 25.0, False) == (wb, 25.0)
    assert ai_review._line_band(_f(line=9), "a", lines, wb, 25.0, False) == (wb, 25.0)
    normal = {"a": [{"box": [100, 500, 700, 524], "angle": 0.0}]}
    assert ai_review._line_band({"a": {"line": 0}, "members": [{}]}, "a", normal, [300, 506, 330, 520],
                                14.0, False) == ([300, 506, 330, 520], 14.0)              # การ์ดโค้ง = union ของสมาชิก


def test_band_vertical_text_uses_the_horizontal_extent():
    lines = {"a": [{"box": [500, 100, 524, 900], "angle": 90.0}]}
    box, lh = ai_review._line_band(_f(), "a", lines, [506, 300, 520, 340], 14.0, True)
    assert box == [500.0, 300.0, 524.0, 340.0] and lh == 24.0


# ── ③ ข้อมูลสถานี + ภาพถ่ายเอียง ─────────────────────────────────────────

def _ko():
    p = load_log(KO1)[1]
    sd = {s: (p[s][0], p[s][1], textmodel.parse(fta_from_lines(p[s][2], p[s][0], p[s][1]), p[s][0], p[s][1])["lines"])
          for s in "AB"}
    (Wa, Ha, A), (Wb, Hb, B) = sd["A"], sd["B"]
    r = compare.compare(copy.deepcopy(A), copy.deepcopy(B), (Wa, Ha), (Wb, Hb))
    fs = r["findings"]
    for i, f in enumerate(fs):
        f["id"] = i + 1
    lines = {"a": [{"box": l["box"], "angle": l.get("angle"), "text": l["text"]} for l in r["lines_a"]],
             "b": [{"box": l["box"], "angle": l.get("angle"), "text": l["text"]} for l in r["lines_b"]]}
    ims = {"a": np.zeros((Ha, Wa, 3), np.uint8), "b": np.zeros((Hb, Wb, 3), np.uint8)}
    return fs, lines, ims


def _region(f, s, lines, ims, lo):
    vert, _ = ai_review.spot_orient(f, s, lines)
    box, lh = ai_review._spot_px(f, s, vert)
    if lo:
        box, lh = ai_review._line_band(f, s, lines, box, lh, vert)
    return ai_review.crop_rect(box, lh, ims[s].shape[1], ims[s].shape[0], vert, lo)


def _centres_inside(lines, s, reg, skip):
    out = []
    for i, l in enumerate(lines[s]):
        b = l["box"]
        if i == skip or not b or b[2] < reg[0] or b[0] > reg[2]:
            continue
        cy = (b[1] + b[3]) / 2.0
        if reg[1] < cy < reg[3]:
            out.append(l["text"][:24])
    return out


def test_king_oscar_f3_crop_no_longer_shows_neighbour_lines(monkeypatch):
    fs, lines, ims = _ko()
    f3 = [f for f in fs if f["a"]["frag"] == "a" and f["b"]["frag"] == "ą"][0]
    ia = f3["a"]["line"]
    old = _region(f3, "a", lines, ims, False)
    new = _region(f3, "a", lines, ims, True)
    assert len(_centres_inside(lines, "a", old, ia)) >= 2           # เดิม: เห็นกลางบรรทัดบน/ล่าง (รวม "mielona …")
    assert any(t.startswith("mielona") for t in _centres_inside(lines, "a", old, ia))
    assert _centres_inside(lines, "a", new, ia) == []                # ใหม่: บรรทัดเดียว
    lb = lines["a"][ia]["box"]
    assert new[1] <= lb[1] and new[3] >= lb[3]                       # ตัวอักษรของบรรทัดนั้นครบความสูง


def test_tilted_photo_overlapping_line_boxes():
    """ภาพถ่ายเอียง 1°: กรอบบรรทัดของ Vision ซ้อนกัน (B055 ล่าง 2387 > B056 บน 2377) — กลางบรรทัดข้างเคียงต้องไม่อยู่ในครอป"""
    lines = {"b": [{"box": [1390, 2324, 2452, 2387], "angle": 359.0, "text": "olej"},
                   {"box": [1390, 2377, 2508, 2441], "angle": 359.0, "text": "czosnek"},
                   {"box": [1391, 2432, 2397, 2499], "angle": 359.0, "text": "mielona"}]}
    f = {"b": {"line": 1, "word_box": [1830, 2388, 2010, 2433]}}
    vert = False
    box, lh = ai_review._spot_px(f, "b", vert)
    box, lh = ai_review._line_band(f, "b", lines, box, lh, vert)
    reg = ai_review.crop_rect(box, lh, 4000, 4000, vert, True)
    # กลางบรรทัดข้างเคียง ณ ตำแหน่งคำ (เอียง 1° ⇒ เลื่อนไม่เกิน ~10 px จากกลางกรอบบรรทัด)
    assert reg[1] > (2324 + 2387) / 2.0 + 10 and reg[3] < (2432 + 2499) / 2.0 - 10
    assert reg[1] <= 2388 and reg[3] >= 2433


def test_plan_crops_marks_line_only_and_counts(monkeypatch):
    monkeypatch.setattr(config, "AI_IMAGE_CROP_MIN_W", 480)
    fs, lines, ims = _ko()
    st: dict = {}
    sent, crops, pub, skip = ai_review.plan_crops(fs, ims, None, st, lines)
    assert st["line_only_crops"] == 2 * len(sent) and crops
    f3 = [f for f in sent if f["a"]["frag"] == "a" and f["b"]["frag"] == "ą"][0]
    ca = [c for c in crops if c["candidate"] == "F%d" % f3["id"] and c["side"] == "a"][0]
    reg = _region(f3, "a", lines, ims, True)
    assert (ca["w"], ca["h"]) == (reg[2] - reg[0], reg[3] - reg[1])           # ครอปที่ส่งจริงใช้แถบบรรทัด
    assert set(pub[next(iter(pub))]["a"]) == {"w", "h", "box"}       # payload คงเดิม (ไม่มีคีย์ใหม่)


# ── ④ ธงปิด · เรนเดอร์ใหม่ · Log ───────────────────────────────────────────

def test_flag_off_is_the_old_crop_byte_for_byte(monkeypatch):
    fs, lines, ims = _ko()
    rng = np.random.default_rng(3)
    ims = {s: rng.integers(0, 255, size=im.shape, dtype=np.uint8) for s, im in ims.items()}
    monkeypatch.setattr(config, "AI_IMAGE_CROP_LINE_ONLY", False)
    st: dict = {}
    _, crops, pub, _ = ai_review.plan_crops(fs, ims, None, st, lines)
    assert "line_only_crops" not in st
    ref = []
    for f in [f for f in fs if f.get("id") is not None and "F%d" % f["id"] in pub]:
        for s in ("a", "b"):
            vert, rot = ai_review.spot_orient(f, s, lines)
            box, lh = ai_review._spot_px(f, s, vert)
            ref.append(ai_review.crop_part(ims[s], box, lh, None, vert, rot)[0]["b64"])
    assert [c["b64"] for c in crops] == ref


class _Hi:
    kmax = 3.0
    base_dpi = 400

    def __init__(self):
        self.asked = []

    def render(self, region, out_wh):
        self.asked.append((region, out_wh))
        return np.zeros((int(out_wh[1]), int(out_wh[0]), 3), np.uint8)


def test_hires_render_uses_the_same_line_band():
    im = np.zeros((2000, 2000, 3), np.uint8)
    hi = _Hi()
    _, info = ai_review.crop_part(im, [400, 500, 460, 512], 12.0, hi, False, 0, True)
    reg, wh = hi.asked[0]
    k = info["hires"]
    assert info["line_only"] and wh[1] == pytest.approx((12 + 2 * max(0.15 * 12, 2.0)) * k, abs=2)
    hi2 = _Hi()
    ai_review.crop_part(im, [400, 500, 460, 512], 12.0, hi2, False, 0, False)
    assert hi2.asked[0][1][1] > wh[1] * 3


def test_log_and_settings():
    from artwork_v2 import pipeline
    src = open(diaglog.__file__, encoding="utf-8").read()
    assert "image_line_only: crops=" in src
    snap = pipeline.settings_snapshot()
    assert snap["AI_IMAGE_CROP_LINE_ONLY"] is True and "AI_IMAGE_CROP_LINE_PAD" in snap


def test_plan_crops_applies_the_band_to_a_short_word():
    """คำเตี้ย (x-height) ในบรรทัดสูง 24 px — ครอปที่ส่งต้องสูงเท่าบรรทัด + ระยะเผื่อ ไม่ใช่เท่าคำ (ตัวสูง/สระบนไม่ถูกตัด)"""
    lines = {s: [{"box": [100, 500, 700, 524], "angle": 0.0, "text": "x"}] for s in ("a", "b")}
    f = {"id": 1, "severity": "red",
         "a": {"line": 0, "word_box": [300, 506, 330, 520]}, "b": {"line": 0, "word_box": [300, 506, 330, 520]}}
    ims = {s: np.zeros((1000, 1000, 3), np.uint8) for s in ("a", "b")}
    _, crops, _, skip = ai_review.plan_crops([f], ims, None, {}, lines)
    assert not skip and all(31 <= c["h"] <= 32 for c in crops)    # 24 + 2 × 0.15 × 24 (ปัดพิกเซล)
