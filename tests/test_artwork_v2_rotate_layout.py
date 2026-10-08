"""Artwork V2 — หมุนโซน · ตัววาดโซนแบบหน้า Artwork เดิม · ตารางผลข้างภาพ (7 ต.ค. รอบ 4)

ที่มา: งาน King Oscar — artwork วางตะแคง 90° บนหน้า PDF แต่ภาพถ่ายชิ้นงานตั้งตรง ⇒ ข้อความตรงกัน
ทุกตัวอักษรแต่จับคู่ตามตำแหน่งไม่ได้ (เหลือง 48 จุด) · V2 ไม่มีการหมุนเลย

สิ่งที่ล็อก:
* ``rotate`` ของโซน (0/90/180/270 ตามเข็ม) ⇒ ภาพที่ส่ง = ภาพโซนที่หมุนแล้ว · มุม 0 / ไม่มีคีย์ / ปิดธง
  ⇒ **ภาพเดิมทุกไบต์** (sha1)
* ทุกชั้นที่แปลงพิกัดภาพที่ส่ง → หน้า (อ่านซ้ำ · หลักฐานภาพ) ต้องหมุนกลับก่อน — ไม่งั้นครอปผิดที่
  แล้ว "ยืนยัน" ด้วยภาพของบรรทัดอื่น (กฎเหล็กข้อ 2)
* หลักฐานภาพบนโซนที่หมุน: หมึกเหมือน ⇒ SAME · หมึกต่าง ⇒ ไม่มีทาง SAME
* หน้าเว็บ: ตัวแปลงพิกัดจอหมุน ↔ หน้าไม่หมุน (รันฟังก์ชันจริงผ่าน node) · ตารางข้างภาพเป็นแสดงผลล้วน
"""

from __future__ import annotations

import io
import json
import os
import random
import re
import shutil
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from artwork_v2_fake import fta  # noqa: E402

from artwork_v2 import config, imaging, jobs, keystore, pipeline, pixverify, vision_client  # noqa: E402

fitz = pytest.importorskip("fitz")
cv2 = pytest.importorskip("cv2")
from PIL import Image  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js", "artwork_v2.js")
HTML = os.path.join(ROOT, "templates", "artwork_v2.html")
KEY = "AIza" + "R" * 35
MM = 72 / 25.4
ROWS = ["Nutrition Facts Serving size 1/2 cup (100g)",
        "Sodium 475 mg 20% Total Fat 7 g 10%",
        "Ingredients: tuna, sunflower oil, water, salt.",
        "Produced in Thailand for John West Foods Ltd,"]


def _read(p):
    return open(p, encoding="utf-8").read()


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    d = tmp_path / "v2"
    monkeypatch.setattr(config, "DATA_DIR", str(d))
    monkeypatch.setattr(config, "JOBS_DIR", str(d / "jobs"))
    monkeypatch.setattr(config, "SECRET_DIR", str(d / "secret"))
    monkeypatch.setattr(config, "KEY_FILE", str(d / "secret" / "key.json"))
    monkeypatch.delenv(config.KEY_ENV, raising=False)
    monkeypatch.setattr(config, "RETRY_WAIT_S", 0.0)
    monkeypatch.setattr(config, "ZONE_ROTATE", True)
    monkeypatch.setattr(config, "PIXEL_VERIFY", False)
    os.makedirs(config.JOBS_DIR)
    keystore.save(KEY)
    yield


def _pdf(path, rows=ROWS):
    doc = fitz.open()
    pg = doc.new_page(width=150 * MM, height=60 * MM)
    for i, t in enumerate(rows):
        pg.insert_text((10 * MM, (14 + i * 9) * MM), t, fontsize=8)
    doc.save(str(path))
    return str(path)


def _job(pa, pb):
    return jobs.create(("a.pdf", open(pa, "rb").read()), ("b.pdf", open(pb, "rb").read()))["id"]


ZONE = [0.03, 0.08, 0.94, 0.84]


def _fake(texts_b=None, sent=None, crops=None):
    """Vision ปลอม: บรรทัดแนวนอนบนภาพที่ส่ง (เหมือนภาพที่หมุนจนอ่านตรงแล้ว)"""
    def fake(groups, poster=None, key=None):
        res, ids = {}, []
        for gi, g in enumerate(groups):
            for it in g:
                ids.append(it["id"])
                W, H = Image.open(io.BytesIO(it["jpeg"])).size
                if sent is not None:
                    sent.append((it["id"], W, H, it["jpeg"]))
                rows = list(ROWS)
                if it["id"].endswith("b") and texts_b:
                    rows = [texts_b.get(t, t) for t in rows]
                if it["id"].startswith("rr"):
                    if crops is not None:
                        crops.append((it["id"], W, H))
                    rows = rows[1:2]
                    lines = [(t, int(0.05 * W), int(0.3 * H), {"cw": max(3, W // 60), "h": max(8, H // 4)})
                             for t in rows]
                else:
                    lines = [(t, int(0.05 * W), int((0.08 + 0.2 * i) * H), {"cw": max(3, W // 60),
                                                                             "h": max(8, H // 14)})
                             for i, t in enumerate(rows)]
                res[it["id"]] = {"ok": True, "error": "", "fta": fta(lines, W, H), "request_index": gi}
        return {"results": res, "calls": [{"index": 0, "phase": "main", "images": ids, "json_bytes": 10,
                                           "status": 200, "attempts": 1, "ms": 1, "error": "", "at": "t"}]}
    return fake


def _pairs(ra=None, rb=None):
    a = {"page": 0, "bbox": ZONE}
    b = {"page": 0, "bbox": ZONE}
    if ra is not None:
        a["rotate"] = ra
    if rb is not None:
        b["rotate"] = rb
    return [{"a": a, "b": b}]


# ── ตัวช่วยใน imaging ──────────────────────────────────────────────────

@pytest.mark.parametrize("v,want", [(90, 90), ("180", 180), (270.0, 270), (-90, 270), (450, 90),
                                    (0, 0), (45, 0), ("x", 0), (None, 0)])
def test_norm_rot(v, want):
    assert imaging.norm_rot(v) == want


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_unrot_box_matches_cv2_rotate_pixel_for_pixel(rot):
    rng = random.Random(rot)
    H0, W0 = 37, 53
    for _ in range(40):
        img = np.zeros((H0, W0, 3), np.uint8)
        x0, y0 = rng.randrange(0, W0 - 6), rng.randrange(0, H0 - 6)
        x1, y1 = x0 + rng.randrange(2, 6), y0 + rng.randrange(2, 6)
        img[y0:y1, x0:x1] = 255
        r = imaging.rotate_img(img, rot)
        ys, xs = np.nonzero(r[:, :, 0])
        box = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)        # กรอบบนภาพที่หมุนแล้ว
        Hs, Ws = r.shape[:2]
        assert imaging.unrot_size(Ws, Hs, rot) == (W0, H0)
        assert imaging.unrot_box(box, Ws, Hs, rot) == (x0, y0, x1, y1)


def test_rotate_img_zero_is_the_same_object():
    img = np.zeros((4, 5, 3), np.uint8)
    assert imaging.rotate_img(img, 0) is img


# ── รับค่ามุมจากหน้าเว็บ ─────────────────────────────────────────────────

def test_parse_pairs_keeps_valid_rotation_only():
    p = pipeline.parse_pairs(_pairs(90, "abc"))
    assert p[0]["a"]["rotate"] == 90 and "rotate" not in p[0]["b"]
    p = pipeline.parse_pairs(_pairs(0, 45))
    assert "rotate" not in p[0]["a"] and "rotate" not in p[0]["b"]
    # ค่าอื่นจากหน้าเว็บ (rotManual) ไม่หลุดเข้าไป
    raw = _pairs(270)
    raw[0]["a"]["rotManual"] = True
    assert set(pipeline.parse_pairs(raw)[0]["a"]) == {"page", "bbox", "rotate"}


def test_parse_pairs_flag_off_ignores_rotation(monkeypatch):
    monkeypatch.setattr(config, "ZONE_ROTATE", False)
    assert pipeline.parse_pairs(_pairs(90, 180)) == pipeline.parse_pairs(_pairs())


# ── ภาพที่ส่ง ─────────────────────────────────────────────────────────

def _run(tmp_path, monkeypatch, pairs, **kw):
    pa, pb = _pdf(tmp_path / "a.pdf"), _pdf(tmp_path / "b.pdf")
    jid = _job(pa, pb)
    sent = []
    monkeypatch.setattr(vision_client, "annotate", _fake(sent=sent, **kw))
    r = pipeline.run(jid, pairs)
    return r, {s[0]: s for s in sent}, jid


def test_rotation_zero_or_missing_is_byte_identical(tmp_path, monkeypatch):
    os.makedirs(tmp_path / "x")
    os.makedirs(tmp_path / "y")
    r0, s0, _ = _run(tmp_path / "x", monkeypatch, _pairs())
    r1, s1, _ = _run(tmp_path / "y", monkeypatch, _pairs(0, 0))
    assert s0["p1_a"][3] == s1["p1_a"][3] and s0["p1_b"][3] == s1["p1_b"][3]
    assert r0["pairs"][0]["sides"]["a"]["sha1"] == r1["pairs"][0]["sides"]["a"]["sha1"]
    assert "rotate" not in r1["pairs"][0]["sides"]["a"] and "rotate:" not in r1["log_text"]


def test_flag_off_sends_unrotated_bytes(tmp_path, monkeypatch):
    os.makedirs(tmp_path / "x")
    os.makedirs(tmp_path / "y")
    _, s0, _ = _run(tmp_path / "x", monkeypatch, _pairs())
    monkeypatch.setattr(config, "ZONE_ROTATE", False)
    _, s1, _ = _run(tmp_path / "y", monkeypatch, _pairs(90, 270))
    assert s0["p1_b"][3] == s1["p1_b"][3]


@pytest.mark.parametrize("rot", [90, 180, 270])
def test_sent_image_is_the_rotated_zone(tmp_path, monkeypatch, rot):
    r, s, jid = _run(tmp_path, monkeypatch, _pairs(None, rot))
    src = imaging.Source(jobs.source(jid, "b").path)
    img, _ = src.render_zone(0, ZONE)
    want = imaging.rotate_img(img, rot)
    got = np.array(Image.open(io.BytesIO(s["p1_b"][3])).convert("RGB"))[:, :, ::-1]
    assert got.shape == want.shape
    assert float(np.abs(got.astype(int) - want.astype(int)).mean()) < 3.0      # ต่างแค่ JPEG
    side = r["pairs"][0]["sides"]["b"]
    assert side["rotate"] == rot and side["render"]["rotate"] == rot
    assert side["sent_px"] == [want.shape[1], want.shape[0]]
    assert ("rotate: %d°" % rot) in r["log_text"]
    # รอบเดิมเปิดงานแล้วได้มุมกลับมา
    assert jobs.last_pairs(os.path.join(config.JOBS_DIR, jid))[0]["b"]["rotate"] == rot
    assert "rotate" not in jobs.last_pairs(os.path.join(config.JOBS_DIR, jid))[0]["a"]


def test_sharp_mode_also_rotates(tmp_path, monkeypatch):
    pa, pb = _pdf(tmp_path / "a.pdf"), _pdf(tmp_path / "b.pdf")
    jid = _job(pa, pb)
    sent = []
    monkeypatch.setattr(vision_client, "annotate", _fake(sent=sent))
    r = pipeline.run(jid, _pairs(None, 90), sharpness="max")
    sb = r["pairs"][0]["sides"]["b"]
    W, H = Image.open(io.BytesIO([x for x in sent if x[0] == "p1_b"][0][3])).size
    assert sb["rotate"] == 90 and [W, H] == sb["sent_px"] and H > W          # โซนแนวนอน ⇒ ภาพที่ส่งแนวตั้ง


# ── อ่านซ้ำ: ครอปต้องตกบนบรรทัดเดียวกันของหน้า ─────────────────────────

@pytest.mark.parametrize("rot", [90, 180, 270])
def test_reread_crop_maps_back_through_rotation(tmp_path, monkeypatch, rot):
    monkeypatch.setattr(config, "REREAD_ENABLED", True)
    crops = []
    pa, pb = _pdf(tmp_path / "a.pdf"), _pdf(tmp_path / "b.pdf")
    jid = _job(pa, pb)
    monkeypatch.setattr(vision_client, "annotate",
                        _fake(texts_b={ROWS[1]: ROWS[1].replace("475", "457")}, crops=crops))
    r = pipeline.run(jid, _pairs(rot, rot))
    items = r["reread"]["items"]
    assert items, r["reread"]
    p = r["pairs"][0]
    reds = [f for f in p["findings"] + p.get("pixel_same", []) if f["class"] == "NUMBER"]
    assert reds
    for s in ("a", "b"):
        side = p["sides"][s]
        W, H = side["sent_px"]
        line = [l for l in p["lines"][s] if "Sodium" in l["text"]][0]
        ub = imaging.unrot_box(line["box"], W, H, rot)
        W0, H0 = imaging.unrot_size(W, H, rot)
        x, y, w, h = side["bbox"]
        cx = x + (ub[0] + ub[2]) / 2 / W0 * w
        cy = y + (ub[1] + ub[3]) / 2 / H0 * h
        cb = items[0]["crops"][s]["bbox"]
        assert cb[0] <= cx <= cb[0] + cb[2] and cb[1] <= cy <= cb[1] + cb[3], (s, cb, cx, cy)
    # ครอปซูมอยู่ในแนวเดียวกับรอบหลัก (บรรทัดยาวแนวนอน ⇒ กว้าง > สูง)
    assert all(W > H for _, W, H in crops)


# ── หลักฐานภาพบนโซนที่หมุน ──────────────────────────────────────────────

def _word_sent_box(path, bbox, Ws, Hs, rot, word):
    """กรอบคำบนภาพที่ส่ง (แนวที่หมุนแล้ว)"""
    W0, H0 = imaging.unrot_size(Ws, Hs, rot)
    with fitz.open(path) as d:
        pg = d[0]
        r = pg.search_for(word)[0]
        pw, ph = pg.rect.width, pg.rect.height
    x, y, w, h = bbox
    sx, sy = W0 / (w * pw), H0 / (h * ph)
    u0, v0, u1, v1 = (r.x0 - x * pw) * sx, (r.y0 - y * ph) * sy, (r.x1 - x * pw) * sx, (r.y1 - y * ph) * sy
    if rot == 90:
        return (H0 - v1, u0, H0 - v0, u1)
    if rot == 180:
        return (W0 - u1, H0 - v1, W0 - u0, H0 - v0)
    if rot == 270:
        return (v0, W0 - u1, v1, W0 - u0)
    return (u0, v0, u1, v1)


def _zone_dims(path, rot):
    img, _ = imaging.Source(path).render_zone(0, ZONE)
    img = imaging.rotate_img(img, rot)
    return img.shape[1], img.shape[0]


@pytest.mark.parametrize("rot", [90, 180, 270])
def test_pixverify_in_rotated_frames(tmp_path, rot):
    pa = _pdf(tmp_path / "a.pdf")
    pb = _pdf(tmp_path / "b.pdf", rows=[ROWS[0], ROWS[1].replace("20%", "24%")] + ROWS[2:])
    Wa, Ha = _zone_dims(pa, rot)
    Wb, Hb = _zone_dims(pb, rot)
    pc = pixverify.PairCheck(pa, pb, {"page": 0, "bbox": ZONE, "W": Wa, "H": Ha, "rot": rot},
                             {"page": 0, "bbox": ZONE, "W": Wb, "H": Hb, "rot": rot})
    try:
        assert pc.ok, pc.ginfo
        assert abs(pc.ginfo["scale"] - 1.0) < 0.01
        same = pc.check("A", _word_sent_box(pa, ZONE, Wa, Ha, rot, "475"))
        assert same["status"] == "SAME", same
        diff = pc.check("A", _word_sent_box(pa, ZONE, Wa, Ha, rot, "20%"))
        assert diff["status"] != "SAME", diff
    finally:
        pc.close()


def test_pixverify_rot_zero_is_the_old_path(tmp_path):
    """ไม่มี ``rot`` = ภาพครอปเดิมทุกพิกเซล"""
    pa, pb = _pdf(tmp_path / "a.pdf"), _pdf(tmp_path / "b.pdf")
    Wa, Ha = _zone_dims(pa, 0)
    z = {"page": 0, "bbox": ZONE, "W": Wa, "H": Ha}
    p1 = pixverify.PairCheck(pa, pb, dict(z), dict(z))
    p2 = pixverify.PairCheck(pa, pb, dict(z, rot=0), dict(z, rot=0))
    try:
        box = _word_sent_box(pa, ZONE, Wa, Ha, 0, "475")
        c1, c2 = p1._crops("A", box), p2._crops("A", box)
        assert np.array_equal(c1[0], c2[0]) and np.array_equal(c1[1], c2[1])
    finally:
        p1.close()
        p2.close()


# ── หน้าเว็บ: ตัวแปลงพิกัด (รันฟังก์ชันจริงผ่าน node) ─────────────────────

NODE = shutil.which("node")


def _js_fn(name):
    js = _read(JS)
    m = re.search(r"\n  function %s\(.*?\n  }\n" % name, js, re.S)
    assert m, name
    return m.group(0)


@pytest.mark.skipif(not NODE, reason="ไม่มี node")
def test_js_screen_mapping_is_consistent():
    js = _read(JS)
    body = js.split("function normPoint(ov, ev) {")[1].split("\n  }\n")[0]
    m = re.search(r"const p = (rot === 90 .*?);\n", body)
    assert m
    script = _js_fn("dispRect") + _js_fn("unrotDelta") + """
    function norm(u, v, rot) { const p = %s; return p; }
    const out = [];
    for (const rot of [0, 90, 180, 270]) {
      for (let k = 0; k < 50; k++) {
        const bb = [Math.random() * 0.5, Math.random() * 0.5, 0.05 + Math.random() * 0.4, 0.05 + Math.random() * 0.4];
        const d = dispRect(bb, rot);                  // กรอบบนจอ (สัดส่วน)
        // มุมซ้ายบน/ขวาล่างบนจอ ⇒ กลับเป็นกรอบบนหน้าเดิม
        const p1 = norm(d[0], d[1], rot), p2 = norm(d[0] + d[2], d[1] + d[3], rot);
        const back = [Math.min(p1[0], p2[0]), Math.min(p1[1], p2[1]), Math.abs(p1[0] - p2[0]), Math.abs(p1[1] - p2[1])];
        const err = Math.max(...back.map((v, i) => Math.abs(v - bb[i])));
        // เลื่อนบนจอ (du, dv) ⇒ เลื่อนบนหน้า = unrotDelta (หน่วยเดียวกันเมื่อจอเป็นสี่เหลี่ยมจัตุรัส)
        const du = 0.01, dv = -0.02;
        const q1 = norm(0.3, 0.4, rot), q2 = norm(0.3 + du, 0.4 + dv, rot);
        const ud = unrotDelta(du, dv, rot);
        const err2 = Math.max(Math.abs(q2[0] - q1[0] - ud[0]), Math.abs(q2[1] - q1[1] - ud[1]));
        out.push([rot, err, err2]);
      }
    }
    console.log(JSON.stringify(out));
    """ % m.group(1)
    res = json.loads(subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True).stdout)
    assert all(e < 1e-9 and e2 < 1e-9 for _, e, e2 in res), [x for x in res if x[1] >= 1e-9 or x[2] >= 1e-9][:3]


def test_editor_has_old_style_controls():
    html = _read(HTML)
    for must in ('id="v2RotA"', 'id="v2RotB"', 'data-z="rot"', 'id="v2DrawCont"', 'id="v2Thumb"',
                 'data-mode="pan" class="on"', ".v2-rot.on > .v2-stage", ".v2-rlab"):
        assert must in html, must
    assert html.count('data-z="rot"') == 2
    js = _read(JS)
    for must in ("function applyRot", "function rotateView", "function rotLabels", "function cycleZoneRot",
                 "function drawThumb", 'let mode = "pan";', "nz.rotate = rotOf(side)",
                 "rot: S.rot", "/^Arrow/.test(ev.key)"):
        assert must in js, must


# ── ตารางข้างภาพ: แสดงผลล้วน ────────────────────────────────────────────

def test_side_table_flag_defaults_on_and_reaches_the_page():
    assert config.SIDE_TABLE is True and config.ZONE_ROTATE is True
    assert "v2_side_table=config.SIDE_TABLE" in _read(os.path.join(ROOT, "artwork_v2", "routes.py"))
    html = _read(HTML)
    assert 'data-side-table="{{ 1 if v2_side_table else 0 }}"' in html
    assert ".v2-resgrid" in html and ".v2-side { min-width:0; position:sticky; top:64px;" in html
    # sticky ใช้ไม่ได้ถ้า .main-content ยังเลื่อนเอง (บทเรียนหน้า Artwork เดิม)
    assert ".main-content { max-width: none; overflow: visible; }" in html


def test_server_side_never_sees_side_table():
    for mod in ("compare", "pipeline", "diaglog", "ai_review", "textmodel", "vision_client", "pixverify"):
        assert "SIDE_TABLE" not in _read(os.path.join(ROOT, "artwork_v2", mod + ".py")), mod


def test_note_rows_are_not_findings():
    js = _read(JS)
    body = js.split("function sideRow(")[1].split("\n  }\n")[0]
    note = body.split("v2-note")[1]
    assert "data-f" not in note.split("</tr>")[0]                # แถวหมายเหตุ ⇒ ไม่ซูม ไม่ถูกเลือก
    assert 'hidden' in note.split("</tr>")[0]                     # พับไว้เป็นค่าเริ่มต้น
    click = js.split('$("v2PairsRes").addEventListener("click"')[1].split("\n  });\n")[0]
    assert click.index(".v2-nbtn") < click.index('closest("tr[data-f]")')   # ปุ่ม ⓘ ไม่ไปค้างการซูม
    assert "ev.stopPropagation();" in click.split('closest("tr[data-f]")')[0]


@pytest.mark.parametrize("place", [90, 270])
def test_pixverify_sideways_artwork_vs_upright_print(tmp_path, place):
    """เคส King Oscar แบบ PDF↔PDF: 🅰 วางตะแคงบนหน้า · 🅱 ตั้งตรง · ผู้ใช้หมุนโซน 🅰 ให้อ่านตรง
    ⇒ ภาพที่ส่งทั้งสองฝั่งตั้งตรง ⇒ หมึกเหมือน = SAME · หมึกต่าง = ไม่มีทาง SAME"""
    pb = _pdf(tmp_path / "b.pdf")
    pb2 = _pdf(tmp_path / "b2.pdf", rows=[ROWS[0], ROWS[1].replace("20%", "24%")] + ROWS[2:])

    def sideways(src, out):
        d = fitz.open()
        s = fitz.open(src)
        r = s[0].rect
        pg = d.new_page(width=r.height, height=r.width)
        pg.show_pdf_page(pg.rect, s, 0, rotate=place)
        d.save(str(out))
        return str(out)
    pa = sideways(pb2, tmp_path / "a.pdf")             # 🅰 = ฉบับที่แก้ 20%→24% วางตะแคง
    full = [0.0, 0.0, 1.0, 1.0]
    # มุมที่ทำให้ 🅰 ตั้งตรง: ลองทั้งสองทาง เลือกอันที่ภาพตรงกับ 🅱 (ไม่เดาทิศของ show_pdf_page)
    ib, _ = imaging.Source(pb).render_zone(0, full)
    ia, _ = imaging.Source(pa).render_zone(0, full)
    gb = cv2.cvtColor(ib, cv2.COLOR_BGR2GRAY).astype(np.float32)

    def score(r):
        x = cv2.cvtColor(imaging.rotate_img(ia, r), cv2.COLOR_BGR2GRAY).astype(np.float32)
        x = cv2.resize(x, (gb.shape[1], gb.shape[0]))
        return float(np.corrcoef(x.ravel(), gb.ravel())[0, 1])
    rot = max((90, 270), key=score)
    Wa, Ha = imaging.rotate_img(ia, rot).shape[1], imaging.rotate_img(ia, rot).shape[0]
    pc = pixverify.PairCheck(pa, pb, {"page": 0, "bbox": full, "W": Wa, "H": Ha, "rot": rot},
                             {"page": 0, "bbox": full, "W": ib.shape[1], "H": ib.shape[0]})
    try:
        assert pc.ok, pc.ginfo
        assert abs(pc.ginfo["scale"] - 1.0) < 0.02, pc.ginfo
        same = pc.check("B", _word_sent_box(pb, full, ib.shape[1], ib.shape[0], 0, "475"))
        assert same["status"] == "SAME", same
        diff = pc.check("B", _word_sent_box(pb, full, ib.shape[1], ib.shape[0], 0, "20%"))
        assert diff["status"] != "SAME", diff
    finally:
        pc.close()
