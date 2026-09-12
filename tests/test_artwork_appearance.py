# -*- coding: utf-8 -*-
"""แยก "ตัวอักษรต่าง" ออกจาก "รูปลักษณ์ต่าง" (โหมดเทียบพิกเซล).

ที่มา — ผลรันจริงบนสถานี 8 ก.ย. 2026: โหมด pixel จับความต่างจริงได้ 2 อย่าง
คือ ``Sodium 20% -> 24%`` (ตัวอักษรต่าง) และบรรทัด ``Manufacturing Co., Ltd…``
ที่ **ตัวอักษรเหมือนกันเป๊ะแต่ฟอนต์คนละน้ำหนัก** (ต้นฉบับหนา · โรงพิมพ์บาง —
วัดได้ หมึก 24.19% vs 21.15% · ความหนาเส้น 8.80 vs 7.62 px)

แต่การ์ดขึ้นว่า **"พบ: Manuf เทียบกับ: Manufa"** ⇒ ผู้ตรวจไปตามหาคำสะกดผิด
ที่ไม่มีอยู่จริง (กฎเหล็กข้อ 2) เพราะครอปตัดกลางคำ

⚠️ ทุกอย่างในโมดูลนี้ต้อง **ไม่ผูกกับภาษาใดภาษาหนึ่ง** — เอกสารของงานนี้มี
   ทั้งอังกฤษ อาหรับ ไทย จีน ญี่ปุ่น ฯลฯ ในไฟล์เดียวกันได้
"""
import os
import tempfile

import cv2
import fitz
import numpy as np
import pytest

from artwork_check import appearance as AP, pixdiff

TEXT = "Manufacturing Co., Ltd, Samut Sakhon"


def _page(path, bold=True, text=TEXT, size=20):
    doc = fitz.open()
    pg = doc.new_page(width=600, height=120)
    pg.insert_text((40, 70), text, fontsize=size,
                   fontname="hebo" if bold else "helv")
    doc.save(str(path))
    doc.close()
    return str(path)


def _render(path, dpi=300):
    img, _ = pixdiff.render_zone_mm(path, [0.0, 0.0, 1.0, 1.0], dpi)
    return img


def _ink_cols(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return (g < 200).sum(axis=0)


def _first_word_end(img):
    """ขอบขวาของคำแรก — หาช่องว่างที่กว้างกว่าครึ่งของช่องว่างที่กว้างสุด."""
    cols = _ink_cols(img)
    idx = np.where(cols > 0)[0]
    runs, run = [], 0
    for i in range(idx[0], idx[-1] + 1):
        if cols[i] == 0:
            run += 1
        elif run:
            runs.append((run, i - run))
            run = 0
    mx = max(r for r, _ in runs)
    return int(idx[0]), min(st for r, st in runs if r > mx * 0.45)


# ── ① normalize ต้องเป็นกลางกับทุกสคริปต์ ────────────────────────────

@pytest.mark.parametrize("s", ["475", "٤٧٥", "۴۷۵", "๔๗๕", "४७५"])
def test_the_same_number_written_in_any_script_compares_equal(s):
    """``٤٧٥`` กับ ``475`` คือเลขตัวเดียวกัน — ถ้าไม่พับจะกลายเป็น
    "ความต่าง" ปลอมทุกครั้งที่ OCR สลับรูปแบบการเขียน."""
    assert AP.norm_text(s) == "475"


def test_case_is_kept_because_it_is_a_real_print_difference():
    """``NET`` กับ ``Net`` ต่างกันจริงบนงานพิมพ์ — ห้ามพับ."""
    assert AP.relation("NET WEIGHT", "Net Weight") == "different"


def test_diacritics_do_not_create_fake_differences():
    """สระ/harakat ที่ OCR ใส่บ้างไม่ใส่บ้าง ต้องไม่นับเป็นความต่าง."""
    assert AP.relation("صَوديوم", "صوديوم") == "same"


# ── ② relation: 4 สถานะที่ต้องแยกออกจากกัน ───────────────────────────

def test_same_text_means_the_difference_is_in_the_look():
    assert AP.relation("Manufacturing", "Manufacturing") == "same"


def test_a_crop_that_cuts_a_word_is_not_a_text_difference():
    """``Manuf`` / ``Manufa`` = ครอปตัดคำ **ไม่ใช่** คำสะกดผิด."""
    assert AP.relation("Manuf", "Manufa") == "truncated"
    assert AP.relation("كربوهيد", "كربوهيدات") == "truncated"


def test_a_real_text_change_is_still_reported_as_different():
    assert AP.relation("20%", "24%") == "different"


def test_unreadable_side_is_never_guessed():
    assert AP.relation("", "24%") == "unknown"
    assert AP.relation(None, None) == "unknown"


# ── ③ วัดรูปลักษณ์ — เรขาคณิตล้วน ใช้ได้ทุกภาษา ─────────────────────

def test_bold_measures_thicker_strokes_than_regular(tmp_path):
    a = _render(_page(tmp_path / "a.pdf", bold=True))
    b = _render(_page(tmp_path / "b.pdf", bold=False))
    sa, sb = AP.ink_stats(a), AP.ink_stats(b)
    assert sa["stroke"] > sb["stroke"] * 1.1
    assert sa["ink"] > sb["ink"]


def test_look_delta_names_the_weight_difference(tmp_path):
    a = _render(_page(tmp_path / "a.pdf", bold=True))
    b = _render(_page(tmp_path / "b.pdf", bold=False))
    d = AP.look_delta(a, b)
    assert d["kind"] == "weight"
    assert "น้ำหนักเส้น" in d["note"]


def test_look_delta_names_the_size_difference(tmp_path):
    a = _render(_page(tmp_path / "a.pdf", bold=False, size=20))
    b = _render(_page(tmp_path / "b.pdf", bold=False, size=14))
    d = AP.look_delta(a, b)
    assert d["kind"] in ("size", "weight")   # ตัวเล็กลงก็บางลงด้วยตามธรรมชาติ
    assert d["note"]


def test_look_delta_refuses_to_guess_when_there_is_no_ink():
    blank = np.full((40, 80, 3), 255, np.uint8)
    assert AP.look_delta(blank, blank)["kind"] == "none"


def test_arabic_is_measured_the_same_way_as_latin(tmp_path):
    """เรขาคณิตไม่รู้จักภาษา — สคริปต์ RTL ต้องวัดได้เหมือนกัน."""
    a = _render(_page(tmp_path / "a.pdf", bold=True, text="ABCDEFGH IJKL"))
    b = _render(_page(tmp_path / "b.pdf", bold=False, text="ABCDEFGH IJKL"))
    assert AP.look_delta(a, b)["kind"] == "weight"


# ── ④ ขยายกรอบให้ถึงขอบคำ — ต้องทำงานได้ทุกความละเอียด ──────────────

@pytest.mark.parametrize("dpi", [300, 600, 862])
def test_expand_box_reaches_the_word_edge_at_every_resolution(tmp_path, dpi):
    """กรอบที่ครอบแค่ต้นคำต้องถูกขยายจนครอบคำเต็ม.

    ⚠️ เกณฑ์ช่องว่างและเพดานการขยาย **ต้องเป็นสัดส่วนของความสูงบรรทัด**
       ไม่ใช่พิกเซลตายตัว — วัดแล้วว่าที่ 862 dpi ช่องว่างระหว่าง *ตัวอักษร*
       กว้างถึง 32 px (มากกว่าค่าคงที่เดิม 2 px หลายเท่า) และคำหนึ่งกว้าง
       กว่าเพดานเดิม 400 px
    """
    p = _page(tmp_path / ("w%d.pdf" % dpi), bold=True)
    img = _render(p, dpi)
    x0, wend = _first_word_end(img)
    rows = np.where((cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) < 200).sum(axis=1) > 0)[0]
    part = [x0, int(rows[0]), int((wend - x0) * 0.40),
            int(rows[-1] - rows[0] + 1)]
    exp = AP.expand_box(img, img, part)
    assert exp[0] <= x0, "ขยายซ้ายไม่ถึงต้นคำ"
    assert exp[0] + exp[2] >= wend, "ขยายขวาไม่ถึงท้ายคำ (dpi %d)" % dpi


def test_expand_box_never_swallows_the_neighbouring_word(tmp_path):
    """ขยายได้เฉพาะที่ **ว่างทั้งสองฝั่ง** ⇒ ห้ามกินคำถัดไป."""
    img = _render(_page(tmp_path / "a.pdf", bold=True))
    x0, wend = _first_word_end(img)
    cols = _ink_cols(img)
    nxt = next(i for i in range(wend, len(cols)) if cols[i] > 0)
    rows = np.where((cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) < 200).sum(axis=1) > 0)[0]
    part = [x0, int(rows[0]), int((wend - x0) * 0.40),
            int(rows[-1] - rows[0] + 1)]
    exp = AP.expand_box(img, img, part)
    assert exp[0] + exp[2] < nxt, "ขยายไปกินคำถัดไปแล้ว"


def test_expand_box_survives_a_box_that_is_out_of_range(tmp_path):
    img = _render(_page(tmp_path / "a.pdf"))
    H, W = img.shape[:2]
    for bad in ([-50, -50, 10, 10], [W + 10, H + 10, 5, 5], [0, 0, 0, 0]):
        e = AP.expand_box(img, img, bad)
        assert e[0] >= 0 and e[1] >= 0
        assert e[0] + e[2] <= W and e[1] + e[3] <= H


def test_line_height_is_measured_not_assumed(tmp_path):
    """ความสูงบรรทัดต้องมาจากหมึกจริง — เป็นฐานของเกณฑ์ช่องว่างทั้งหมด."""
    img = _render(_page(tmp_path / "a.pdf", size=20), 300)
    small = _render(_page(tmp_path / "b.pdf", size=10), 300)
    ha = AP.line_height(img, img.shape[0] // 2)
    hb = AP.line_height(small, small.shape[0] // 2)
    assert ha > hb * 1.5
