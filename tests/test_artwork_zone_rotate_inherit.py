# -*- coding: utf-8 -*-
"""โซนที่ "ระบบสร้างให้" ต้องได้มุมหมุนด้วย (26 ก.ย. 2026)

ที่มา (สถานี): หมุนจอ 90° → ลากโซน 🅰 (rotate=90 ถูกต้อง) → กด "หากรอบคู่
อัตโนมัติ" ⇒ โซน 🅱 ได้ ``rotate: "default"`` ตายตัว ⇒ OCR อ่านฝั่ง 🅱 แบบ
ตะแคง ตกท่อน ``........ (min.) 8%`` ⇒ การ์ด "พบเฉพาะ" ปลอม · การ์ดฝั่ง 🅱
ไม่หมุนตาม

ล็อก: ธงเปิดเป็นค่าเริ่มต้น · ปิด = ``"default"`` เดิม · คู่อัตโนมัติได้มุม
ของโซนต้นทาง · โซนที่เสนอขณะหมุนจอได้มุมของจอ · และสมมติฐานที่ทำให้การ
ลอกมุมปลอดภัย: ตัวจับคู่ค้นแบบไม่หมุน ⇒ คนละแนวจับคู่ไม่ติด
"""
import os

import cv2
import fitz
import numpy as np

from artwork_check import config, zones as Z

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = open(os.path.join(ROOT, "static", "js", "artwork_check.js"),
          encoding="utf-8").read()
TPL = open(os.path.join(ROOT, "templates", "artwork_check.html"),
           encoding="utf-8").read()
ROUTES = open(os.path.join(ROOT, "artwork_check", "routes.py"),
              encoding="utf-8").read()
CFG = open(os.path.join(ROOT, "artwork_check", "config.py"),
           encoding="utf-8").read()


def _fn(name):
    i = JS.index("function " + name + "(")
    return JS[i:JS.index("\n  }\n", i)]


def test_flag_defaults_on():
    assert '"ARTWORK_ZONE_ROTATE_INHERIT", "1"' in CFG


def test_flag_reaches_the_page_before_the_script():
    assert "zone_rotate_inherit=config.ZONE_ROTATE_INHERIT" in ROUTES
    i = TPL.index("window.AW_ZONE_ROTATE_INHERIT = {{ 'true' if "
                  "zone_rotate_inherit else 'false' }};")
    assert i < TPL.index("js/artwork_check.js")


def test_autopair_copies_the_source_rotation():
    seg = JS[JS.index('$("awPairAuto")'):]
    seg = seg[:seg.index("made++")]
    assert 'doc: "b", rotate: rotForPairOf(src), bbox: r.bbox,' in seg
    assert 'rotate: "default"' not in seg


def test_pair_rotation_falls_back_to_default():
    f = _fn("rotForPairOf")
    assert ('if (!inheritRot() || !src || src.rotate === undefined) '
            'return "default";') in f
    assert "return src.rotate;" in f
    assert ("const inheritRot = () => window.AW_ZONE_ROTATE_INHERIT "
            "!== false;") in JS


def test_proposed_zones_take_the_screen_rotation_only_when_rotated():
    f = _fn("rotForProposed")
    assert "if (!inheritRot() || !pageRot) return z;" in f
    assert ('if (z.rotate !== undefined && z.rotate !== "default") '
            'return z;') in f
    assert "Object.assign({}, z, { rotate: pageRot })" in f
    assert ".concat((res.zones || []).map(rotForProposed));" in JS


def test_hand_drawn_zones_still_use_the_screen_rotation():
    assert 'rotate: rotForNewZone() || "default",' in JS


# ── ทำไมลอกมุมของโซนต้นทางได้: ตัวจับคู่ค้นแบบไม่หมุน ──────────────────

_LINES = ["GUARANTEED ANALYSIS", "ANALYSE GARANTIE", "Crude Protein",
          "Proteines Brutes ............ (min.) 8%", "Crude Fat",
          "Matieres Grasses Brutes .... (min.) 0.5%",
          "Moisture / Humidite (max.) 84.5%"]


def _page(tmp_path, name, w, h, x0):
    d = fitz.open()
    p = d.new_page(width=w, height=h)
    for i, t in enumerate(_LINES):          # ตัวหนังสือตั้ง (ฉลากซอง)
        p.insert_text((x0 + i * 16, 330), t, fontsize=12, rotate=90)
    path = str(tmp_path / name)
    d.save(path)
    pix = fitz.open(path)[0].get_pixmap(dpi=150)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)
    return cv2.cvtColor(a[:, :, :3], cv2.COLOR_RGB2BGR)


def test_autopair_only_pairs_blocks_in_the_same_orientation(tmp_path):
    a = _page(tmp_path, "a.pdf", 420, 360, 60)
    b = _page(tmp_path, "b.pdf", 440, 380, 80)
    box = [0.12, 0.1, 0.3, 0.85]
    _, same = Z.autopair_bbox(a, b, box)
    _, turned = Z.autopair_bbox(a, cv2.rotate(b, cv2.ROTATE_90_CLOCKWISE), box)
    assert same >= config.AUTOPAIR_MIN_CONF
    assert turned < config.AUTOPAIR_MIN_CONF


def test_rotated_pair_survives_zone_sanitising():
    out = Z.sanitize_zones([
        {"id": "z1", "type": "panel", "group": "A", "doc": "a",
         "bbox": [0.1, 0.1, 0.3, 0.3], "rotate": 90},
        {"id": "b2", "type": "panel", "group": "A", "doc": "b",
         "bbox": [0.1, 0.1, 0.3, 0.3], "rotate": 90}])
    assert [z["rotate"] for z in out] == [90, 90]
