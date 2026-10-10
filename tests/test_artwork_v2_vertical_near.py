"""Artwork V2 — ครอปข้อความแนวตั้ง + หมุนตั้งตรง · ด่านข้อความฝั่งเดียว · ซูมบริเวณใกล้เคียง (10 ต.ค.)

ที่มา (สถานี John West run_002 · AI ดูภาพตัดสิน): F20/F21 = claim แนวตั้งที่มีเฉพาะ 🅱 ⇒ ครอปกินทั้งโซน
(ใช้ความสูงกรอบ = ความยาวประโยค เป็น "ความสูงบรรทัด") แล้วถูกย่อ · AI อ่าน F20 ผิดคนละประโยค และ "อ่าน" F21
ฝั่ง A ได้ประโยคเดียวกับ B ทั้งที่ A ว่าง (ลอกข้ามฝั่ง) · ผู้ใช้สั่ง: หมุนก่อนส่ง + แสดงภาพที่หมุนแล้ว ·
จุดฝั่งเดียวต้องซูมได้ทุกกรณี (บริเวณใกล้เคียงดีกว่าไม่ซูม)

สิ่งที่ล็อก:
* ธงปิด = ครอปเดิมทุกไบต์ (ตัวเลขกรอบตรงกับ Log สถานีเป๊ะ)
* มุมมาจาก Vision เท่านั้น — ไม่มีมุม ⇒ ไม่ถือเป็นแนวตั้ง ไม่หมุน (ตัวอักษรเดี่ยวก็สูง-แคบ)
* ครอปที่หมุนแล้ว = ภาพต้นทางหมุนจริง · กรอบของจุดหมุนตาม · สัญญากับ N8N ไม่เปลี่ยน
* ด่านฝั่งเดียว: AI บอก "เหมือน" กับข้อความที่ Vision อ่านชัดฝั่งเดียว ⇒ เหลือง ไม่พับ
* ``near_box`` เกิดเฉพาะเมื่อ ``est_box`` ไม่ผ่าน · ไม่ใช่ ``box``/``est_box`` · ธงปิด = ไม่มีคีย์
"""

from __future__ import annotations

import base64
import glob
import hashlib
import os
import subprocess
import sys

import cv2
import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from artwork_v2_fake import fta_from_lines, load_log  # noqa: E402

from artwork_v2 import ai_review, compare, config, diaglog, pixverify, structure, textmodel  # noqa: E402

JS = os.path.join(ROOT, "static", "js", "artwork_v2.js")
TPL = os.path.join(ROOT, "templates", "artwork_v2.html")
D = os.path.join(ROOT, "tests", "data", "artwork_v2")
W, H = 4255, 533
TEXT = "Free from hydrogenated oils"


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    for k in ("AI_IMAGE_CROP_VERTICAL", "AI_IMAGE_CROP_ROTATE", "AI_IMAGE_ONESIDED_GUARD", "NEAR_ZOOM",
              "EST_BOX"):
        monkeypatch.setattr(config, k, True)
    monkeypatch.setattr(config, "AI_IMAGE_CROP_HIRES", False)


def _image():
    """แถบเตี้ย-กว้างแบบ John West + ข้อความแนวตั้งอ่านบนลงล่าง (ทิศข้อความชี้ลง = มุม 90 แบบ Vision)"""
    im = np.full((H, W, 3), 245, np.uint8)
    rng = np.random.RandomState(7)
    im[:, :2000] = rng.randint(0, 255, (H, 2000, 3), dtype=np.uint8)      # ลายรอบ ๆ (ครอปผิดที่ = เห็นต่าง)
    txt = np.full((26, 270, 3), 245, np.uint8)
    cv2.putText(txt, TEXT, (2, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (30, 90, 30), 1, cv2.LINE_AA)
    im[170:440, 2521:2547] = cv2.rotate(txt, cv2.ROTATE_90_CLOCKWISE)
    return im, txt


def _f21():
    return {"id": 21, "class": "EXTRA_IN_B", "severity": "red", "notes": [],
            "a": {"line": None, "text": "", "frag": "", "est_box": [2545, 160, 2571, 430], "box": None},
            "b": {"line": 21, "text": TEXT, "frag": TEXT, "box": [2521, 170, 2547, 440],
                  "word_box": [2521, 170, 2547, 440], "conf": 0.958}}


def _lines(angle=91.0):
    return {"a": [], "b": [{}] * 21 + [{"angle": angle}]}


def _crop(f, s, lines, im=None):
    im = _image()[0] if im is None else im
    vert, rot = ai_review.spot_orient(f, s, lines)
    box, lh = ai_review._spot_px(f, s, vert)
    part, info = ai_review.crop_part(im, box, lh, None, vert, rot)
    return part, info, vert, rot


def _dec(part):
    return cv2.imdecode(np.frombuffer(base64.b64decode(part["b64"]), np.uint8), cv2.IMREAD_COLOR)


# ── ① มุมของข้อความ ─────────────────────────────────────────────────────

@pytest.mark.parametrize("angle,want", [(91.0, (True, 270)), (269.0, (True, 90)), (180.0, (False, 180)),
                                        (2.0, (False, 0)), (358.0, (False, 0)), (45.0, (False, 0)),
                                        (None, (False, 0))])
def test_orientation_comes_from_the_vision_angle(angle, want):
    assert ai_review.spot_orient(_f21(), "b", _lines(angle)) == want


def test_empty_side_borrows_the_other_sides_angle():
    assert ai_review.spot_orient(_f21(), "a", _lines()) == (True, 270)


def test_no_angle_means_no_guess_even_for_tall_boxes():
    """ตัวอักษรเดี่ยว "1"/"|" ก็สูง-แคบ — ห้ามตัดสินแนวตั้งจากรูปกรอบ"""
    f = _f21()
    assert ai_review.spot_orient(f, "b", {"a": [], "b": []}) == (False, 0)
    assert ai_review.spot_orient(f, "b", None) == (False, 0)


def test_flags_off(monkeypatch):
    monkeypatch.setattr(config, "AI_IMAGE_CROP_ROTATE", False)
    assert ai_review.spot_orient(_f21(), "b", _lines()) == (True, 0)
    monkeypatch.setattr(config, "AI_IMAGE_CROP_VERTICAL", False)
    assert ai_review.spot_orient(_f21(), "b", _lines()) == (False, 0)


# ── ② ครอป ──────────────────────────────────────────────────────────────

def test_flag_off_reproduces_the_station_crop_exactly(monkeypatch):
    """Log สถานี F21: B=768x533 box=[483, 319, 517, 826] · A=768x513 (ภาพ A สูง 513)"""
    monkeypatch.setattr(config, "AI_IMAGE_CROP_VERTICAL", False)
    part, info, _, _ = _crop(_f21(), "b", _lines())
    assert (part["w"], part["h"], info["box"]) == (768, 533, [483, 319, 517, 826])
    assert "rot" not in info


def test_vertical_crop_is_narrow_rotated_and_shows_the_text_upright():
    im, txt = _image()
    part, info, vert, rot = _crop(_f21(), "b", _lines(), im)
    assert vert and rot == 270 and info["rot"] == 270
    assert part["w"] > part["h"] and part["w"] <= config.AI_IMAGE_CROP_MAX_SIDE
    assert info["scale"] == 1.0                                       # ไม่ย่อ = ตัวอักษรขนาดจริง
    r = info["region"]
    assert r[2] - r[0] < 150 and r[3] - r[1] > 400                    # แคบรอบประโยค ไม่ใช่ทั้งโซน
    got = _dec(part).astype(float)
    want = cv2.rotate(im[r[1]:r[3], r[0]:r[2]], cv2.ROTATE_90_COUNTERCLOCKWISE).astype(float)
    assert got.shape == want.shape and np.abs(got - want).mean() < 4.0
    # ข้อความในกรอบที่บอก AI = ตัวหนังสือแนวนอนตั้งตรง (เทียบกับต้นฉบับก่อนหมุน)
    b = info["box"]
    x0, y0 = int(b[0] / 1000 * part["w"]), int(b[1] / 1000 * part["h"])
    x1, y1 = int(round(b[2] / 1000 * part["w"])), int(round(b[3] / 1000 * part["h"]))
    sub = cv2.resize(_dec(part)[y0:y1, x0:x1], (txt.shape[1], txt.shape[0]))
    assert np.abs(sub.astype(float) - txt.astype(float)).mean() < 12.0
    flipped = cv2.rotate(sub, cv2.ROTATE_180)
    assert np.abs(sub.astype(float) - txt).mean() < np.abs(flipped.astype(float) - txt).mean()


@pytest.mark.parametrize("rot", [90, 180, 270])
def test_box_rotates_with_the_crop(rot):
    im = np.zeros((300, 400, 3), np.uint8)
    im[100:140, 50:250] = 255
    rel = [125, 333, 625, 467]                                        # [50,100,250,140] ใน 400x300
    out = cv2.rotate(im, {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
                          270: cv2.ROTATE_90_COUNTERCLOCKWISE}[rot])
    b = ai_review._rot_rel(rel, rot)
    h, w = out.shape[:2]
    ys, xs = np.where(out[:, :, 0] > 0)
    got = [xs.min() / w * 1000, ys.min() / h * 1000, (xs.max() + 1) / w * 1000, (ys.max() + 1) / h * 1000]
    assert all(abs(g - e) <= 4 for g, e in zip(got, b))


def test_horizontal_findings_are_byte_identical_with_the_flags_on_or_off(monkeypatch):
    f = {"id": 1, "class": "NUMBER", "severity": "red", "notes": [],
         "a": {"line": 0, "text": "Sodium 20%", "word_box": [2300, 300, 2330, 320], "box": [2310, 300, 2320, 320]},
         "b": {"line": 0, "text": "Sodium 24%", "word_box": [2300, 300, 2330, 320], "box": [2310, 300, 2320, 320]}}
    lines = {"a": [{"angle": 0.4}], "b": [{"angle": 359.6}]}
    on = _crop(f, "b", lines)[0]["b64"]
    monkeypatch.setattr(config, "AI_IMAGE_CROP_VERTICAL", False)
    off = _crop(f, "b", lines)[0]["b64"]
    assert hashlib.sha1(on.encode()).hexdigest() == hashlib.sha1(off.encode()).hexdigest()


def test_plan_crops_keeps_the_n8n_contract_and_records_rotation(tmp_path):
    im = _image()[0]
    stats: dict = {}
    sent, crops, pub, skip = ai_review.plan_crops([_f21()], {"a": im[:513], "b": im}, None, stats, _lines())
    assert not skip and len(crops) == 2
    assert set(pub["F21"]["a"]) == {"w", "h", "box"} == set(pub["F21"]["b"])     # payload เดิม
    assert stats["rots"] == {"F21": {"a": 270, "b": 270}} and stats["rotated_crops"] == 2
    f = _f21()
    n = ai_review.save_crops(str(tmp_path), 1, crops, pub, [f], stats["rots"])
    assert n == 2 and f["ai_crop"]["b"]["rot"] == 270
    assert f["ai_crop"]["b"]["w"] > f["ai_crop"]["b"]["h"]
    src = open(diaglog.__file__, encoding="utf-8").read()
    assert '" rot=%s" % v["rot"]' in src and "image_orient: crops_rotated=" in src


# ── ③ ด่านข้อความฝั่งเดียว ───────────────────────────────────────────────

def _merge(f, verdict="noise"):
    pr = {"n": 1, "findings": [f]}
    st = {"reviews_total": 0, "reviews_valid": 0, "invalid": []}
    resp = {"reviews": [{"candidate": "F%d" % f["id"], "verdict": verdict, "reason": "", "suggestion": "",
                         "a_seen": "", "b_seen": ""}]}
    ai_review._merge_image(pr, resp, [f], st)
    return pr, st


def test_onesided_noise_is_kept_yellow():
    pr, st = _merge(_f21())
    assert [g["id"] for g in pr["findings"]] == [21] and not pr["ai_dismissed"]
    f = pr["findings"][0]
    assert f["severity"] == "yellow" and f["ai"]["onesided_kept"]
    assert any("ลอกข้อความข้ามฝั่ง" in n for n in f["notes"])
    assert st["image_verdicts"]["onesided_kept"] == 1


def test_missing_in_b_reads_the_a_side():
    f = _f21()
    f["class"] = "MISSING_IN_B"
    f["a"], f["b"] = dict(f["b"], line=3), dict(f["a"])
    pr, _ = _merge(f)
    assert pr["findings"] and pr["findings"][0]["severity"] == "yellow"


@pytest.mark.parametrize("change", ["low_conf", "punct", "curved", "flag_off", "two_sided"])
def test_guard_does_not_fire(change, monkeypatch):
    f = _f21()
    if change == "low_conf":
        f["b"]["conf"] = 0.5
    elif change == "punct":
        f["b"]["frag"] = f["b"]["text"] = "•"
    elif change == "curved":
        f["curved"] = True
    elif change == "flag_off":
        monkeypatch.setattr(config, "AI_IMAGE_ONESIDED_GUARD", False)
    else:
        f["class"] = "TEXT"
    pr, _ = _merge(f)
    assert not pr["findings"] and [g["id"] for g in pr["ai_dismissed"]] == [21]


def test_real_answer_is_untouched():
    pr, _ = _merge(_f21(), "real")
    assert pr["findings"][0]["severity"] == "red" and not pr["findings"][0]["ai"].get("onesided_kept")


# ── ④ near_box (เซิร์ฟเวอร์) ─────────────────────────────────────────────

class _G:
    def __init__(self, b):
        self.b = b

    def box(self, _):
        return self.b


def test_near_box_is_clipped_and_needs_overlap(monkeypatch):
    side: dict = {}
    compare._near_box(side, _G((-50, 10, 60, 40)), [0, 0, 1, 1], (100, 100))
    assert side["near_box"] == [0.0, 10.0, 60.0, 40.0]
    side = {}
    compare._near_box(side, _G((150, 10, 200, 40)), [0, 0, 1, 1], (100, 100))
    assert "near_box" not in side
    monkeypatch.setattr(config, "NEAR_ZOOM", False)
    side = {}
    compare._near_box(side, _G((10, 10, 60, 40)), [0, 0, 1, 1], (100, 100))
    assert side == {}


def _parse(lines, w, h):
    return textmodel.parse(fta_from_lines(lines, w, h), w, h)["lines"]


def test_near_box_steps_in_only_when_the_estimate_is_refused(monkeypatch):
    path = sorted(glob.glob(os.path.join(D, "johnwest", "*_log.txt")))[0]
    d = load_log(path)[1]
    (Wa, Ha, la), (Wb, Hb, lb) = d["A"], d["B"]
    k = next(i for i in range(len(lb)) if len(lb[i][0].replace(" ", "")) >= 10)
    A, B = _parse(la, Wa, Ha), _parse(lb[:k] + lb[k + 1:], Wb, Hb)

    def miss(r):
        return [f for f in r["findings"] if f["class"] == "MISSING_IN_B"]
    base = miss(compare.compare(A, B, (Wa, Ha), (Wb, Hb)))
    assert base and all("near_box" not in f["b"] for f in base)      # est ผ่าน ⇒ ไม่มี near
    monkeypatch.setattr(structure.Geo, "estimate", lambda self, b, h: None)
    got = miss(compare.compare(A, B, (Wa, Ha), (Wb, Hb)))
    assert got and all(f["b"].get("near_box") and not f["b"].get("est_box") for f in got)
    for f in got:
        assert f["b"]["box"] is None and pixverify._box(f["b"]) is None
        nb = f["b"]["near_box"]
        assert 0 <= nb[0] < nb[2] <= Wb and 0 <= nb[1] < nb[3] <= Hb
    monkeypatch.setattr(config, "NEAR_ZOOM", False)
    assert all("near_box" not in f["b"] for f in miss(compare.compare(A, B, (Wa, Ha), (Wb, Hb))))


# ── ⑤ หน้าเว็บ (ฟังก์ชันจริงผ่าน node) ─────────────────────────────────

def _fn(src, name):
    i = src.index("function " + name + "(")
    d, j = 0, src.index("{", i)
    for k in range(j, len(src)):
        d += src[k] == "{"
        d -= src[k] == "}"
        if not d:
            return src[i:k + 1]
    raise AssertionError(name)


def _node(code, near=True):
    src = open(JS, encoding="utf-8").read()
    pre = "const NEAR_ZOOM=%s;" % ("true" if near else "false")
    js = pre + "".join(_fn(src, n) for n in ("zoomRect", "lineVert", "nearOf")) + code
    return subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout.strip()


def test_vertical_zoom_rect_hugs_the_text():
    out = _node("const sd={box:[2521,170,2547,440]};"
                "console.log(JSON.stringify(zoomRect(sd,4255,533,0.22,3,false)));"
                "console.log(JSON.stringify(zoomRect(sd,4255,533,0.22,3,true)));")
    old, new = [eval(x) for x in out.split("\n")]
    assert new[3] - new[1] < old[3] - old[1]                          # สูงน้อยลง ⇒ ซูมได้มากขึ้น
    assert new[2] - new[0] < 200 and old[2] - old[0] > 700
    assert new[0] <= 2521 and new[2] >= 2547 and new[1] <= 170 and new[3] >= 440


def test_line_vert_uses_the_vision_angle_only():
    out = _node("const f={a:{line:null},b:{line:1}};"
                "console.log(lineVert({lines:{a:[],b:[{},{angle:91}]}},f,'a'),"
                "lineVert({lines:{a:[],b:[{},{angle:3}]}},f,'b'),"
                "lineVert({lines:{a:[],b:[]}},f,'b'));")
    assert out == "true false false"
    assert _node("console.log(lineVert({lines:{b:[{},{angle:91}]}},{b:{line:1}},'b'));", near=False) == "false"


def test_near_of_falls_back_to_zone_proportion():
    out = _node("const f={a:{text:''},b:{text:'x',word_box:[100,50,200,60]}};"
                "console.log(JSON.stringify(nearOf(f,'a',2000,1000,1000,500)));"
                "console.log(JSON.stringify(nearOf({a:{near_box:[1,2,3,4]},b:{}},'a',10,10,10,10)));"
                "console.log(JSON.stringify(nearOf({a:{},b:{}},'a',10,10,10,10)));")
    a, b, c = out.split("\n")
    assert a == '{"box":[200,100,400,120],"kind":"prop"}'
    assert b == '{"box":[1,2,3,4],"kind":"near"}'
    assert c == "null"
    assert _node("console.log(nearOf({a:{near_box:[1,2,3,4]},b:{}},'a',10,10,10,10));", near=False) == "null"


def test_page_wiring():
    src = open(JS, encoding="utf-8").read()
    tpl = open(TPL, encoding="utf-8").read()
    assert 'data-near-zoom="{{ 1 if v2_near_zoom else 0 }}"' in tpl
    assert "rect.est.near" in tpl and "rect.est.prop" in tpl
    assert "nearOf(f, st.dataset.side" in src and "lineVert(p, f, st.dataset.side)" in src
    assert 'c.rot ? " ↻" + esc(c.rot)' in src                         # ⓘ บอกว่าภาพหมุนแล้ว
    assert 'f[side].near_box' in src                                  # วาดกรอบประของ near_box


def test_vertical_text_is_zoomed_at_least_twice_and_stays_inside():
    src = open(JS, encoding="utf-8").read()
    js = ("const ZOOM={maxAbs:8,minCap:2};" + _fn(src, "zoomClamp") + "const VERT_MIN_ZOOM=2;" +
          _fn(src, "zoomForce") +
          "const v=zoomForce([3096,30,3193,526],4444,556,400,50,11,VERT_MIN_ZOOM);"
          "console.log(v.s, v.tx<=0 && v.tx>=400-v.s*400, v.ty<=0 && v.ty>=50-v.s*50);"
          "console.log(zoomForce([0,0,10,10],100,100,100,100,0.5,1).s);")
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout.split("\n")
    assert out[0] == "2 true true" and out[1] == "1"
    assert "if (vert && NEAR_ZOOM && !(to.s >= VERT_MIN_ZOOM))" in src
