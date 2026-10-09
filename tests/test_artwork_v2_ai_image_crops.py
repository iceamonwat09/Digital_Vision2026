"""Artwork V2 — โหมด "AI ดูภาพตัดสิน" แบบ **ครอปรอบแต่ละจุด** (9 ต.ค. รอบ 4)

ที่มา: ผลสถานีรอบแรก (AvoDerm M1↔M2) — Gemini นับภาพทั้งโซน 3682 px เป็น 258 token (≈ 768 px)
⇒ ตัวอักษร ~28 px เหลือ ~6 px · ``b_seen`` ลอกคำที่ Vision อ่านผิด ("5&-3") ⇒ ตรา OMEGA-6 ที่เหมือนกัน
ถูกตอบว่าต่างจริง. ล็อกไว้ที่นี่:

* ครอปตัดจาก **ไฟล์ JPEG เดียวกับที่ส่ง Vision** ตรงตำแหน่งของจุด · ความละเอียดจริง (ไม่ย่อเมื่อพอดี)
* 1 คำขอต่อคู่ · A/B แยกรูป · เพดานจุด (แดงก่อน) · จุดที่ไม่ส่ง = คงระดับ + หมายเหตุ (แผน ก)
* ข้อความโค้งที่ AI ตอบ real ⇒ เหลือง
* workflow ประกอบคำขอ: ครอปของแต่ละจุดเรียงตาม candidate · ไม่ครบ = ปฏิเสธ
"""
import base64
import json
import os
import shutil
import subprocess
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fitz  # noqa: E402
from artwork_v2_fake import fta  # noqa: E402

from artwork_v2 import (ai_review, compare, config, diaglog, jobs, keystore,  # noqa: E402
                        pipeline, textmodel, vision_client)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, "artwork_v2", "n8n_artwork_v2_image.workflow.json")
KEY = "AIza" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    d = tmp_path / "v2"
    monkeypatch.setattr(config, "DATA_DIR", str(d))
    monkeypatch.setattr(config, "JOBS_DIR", str(d / "jobs"))
    monkeypatch.setattr(config, "SECRET_DIR", str(d / "secret"))
    monkeypatch.setattr(config, "KEY_FILE", str(d / "secret" / "key.json"))
    monkeypatch.delenv(config.KEY_ENV, raising=False)
    monkeypatch.setattr(config, "RETRY_WAIT_S", 0.0)
    monkeypatch.setattr(config, "REREAD_ENABLED", False)
    monkeypatch.setattr(config, "AI_IMAGE_SAFETY", False)
    monkeypatch.setattr(config, "AI_IMAGE_CROPS", True)
    # ไฟล์นี้ล็อกรอบ "ครอปจาก JPEG + ข้อความ Vision" (/2) — โหมดไม่เห็นข้อความ/เรนเดอร์ใหม่มีเทสต์แยก
    monkeypatch.setattr(config, "AI_IMAGE_BLIND", False)
    monkeypatch.setattr(config, "AI_IMAGE_CROP_HIRES", False)
    monkeypatch.setattr(config, "AI_IMAGE_MAX_CANDIDATES", 40)
    monkeypatch.setattr(config, "AI_IMAGE_CURVED_YELLOW", True)
    monkeypatch.setattr(config, "AI_IMAGE_URL", "http://127.0.0.1:9/webhook/artwork-v2-image")
    os.makedirs(config.JOBS_DIR)
    yield


# ── ตัวช่วย ──────────────────────────────────────────────────────────

W, H = 3000, 1400                    # ขนาดใกล้โซนจริงของสถานี (3682×1457)


def _noise(seed):
    """ภาพลายสุ่ม (ทุกตำแหน่งไม่ซ้ำกัน) ⇒ ตรวจได้ว่าครอปมาจากตำแหน่งไหนของภาพจริง"""
    rng = np.random.RandomState(seed)
    small = rng.randint(0, 255, (H // 8, W // 8, 3)).astype(np.uint8)
    return cv2.resize(small, (W, H), interpolation=cv2.INTER_NEAREST)


def _simple(texts):
    rows = [(t, 200, 200 + i * 300, {"cw": 14, "h": 28}) for i, t in enumerate(texts)]
    return textmodel.parse(fta(rows, W, H), W, H)["lines"]


def _pr(texts_a, texts_b, img_dir=None, fake_bytes=False):
    A, B = _simple(texts_a), _simple(texts_b)
    r = compare.compare(A, B, (W, H), (W, H))
    for i, f in enumerate(r["findings"], 1):
        f["id"] = i
    sides, ims = {}, {}
    for k, s in enumerate(("a", "b")):
        sides[s] = {"sent_px": [W, H], "stats": {"conf_mean": 0.97}, "image": "p1_%s.jpg" % s}
        ims[s] = _noise(k + 1)
        if img_dir is not None:
            path = os.path.join(img_dir, "p1_%s.jpg" % s)
            if fake_bytes:
                open(path, "wb").write(b"not a jpeg")
            else:
                cv2.imwrite(path, ims[s], [cv2.IMWRITE_JPEG_QUALITY, 92])
    pr = {"n": 1, "findings": r["findings"], "curved_lines": r["curved_lines"],
          "reflow_lines": r["reflow_lines"], "_cmp": r, "_raw": {"a": A, "b": B}, "sides": sides}
    return pr, ims


class _Resp:
    def __init__(self, data, status=200):
        self.status_code = status
        self._d = data
        self.text = json.dumps(data)

    def json(self):
        return self._d


def _poster(answer, seen=None):
    def post(url, data=None, headers=None, timeout=None):
        body = json.loads(data.decode("utf-8"))
        if seen is not None:
            seen.append(body)
        return _Resp(answer(body) if callable(answer) else answer)
    return post


def _rev(cid, verdict, a="x", b="y"):
    return {"candidate": cid, "a_seen": a, "b_seen": b, "verdict": verdict,
            "reason": "เหตุผล", "suggestion": "คำแนะนำ"}


def _all(verdict):
    return lambda body: {"reviews": [_rev(c["id"], verdict) for c in body["candidates"]],
                         "items": [], "summary": "s", "suggestions": [], "engine": "gemini"}


def _go(pr, answer, tmp_path, seen=None, warns=None):
    w = [] if warns is None else warns
    return ai_review.run_all([pr], "image", w, lambda *_: None, 100, _poster(answer, seen),
                             img_dir=str(tmp_path))


TA = ["Sodium 475 mg 20%", "Fat 1.5g per serving", "Net weight 85 g"]
TB = ["Sodium 475 mg 24%", "Fat 15g per serving", "Net weight 85 g"]


def _dec(c):
    return cv2.imdecode(np.frombuffer(base64.b64decode(c["b64"]), np.uint8), cv2.IMREAD_COLOR)


# ── ① เรขาคณิตของครอป ─────────────────────────────────────────────────

def test_crop_rect_stays_inside_and_keeps_native_resolution():
    for box in ([0, 0, 40, 28], [2960, 1370, 3000, 1400], [1400, 600, 1460, 628],
                [100, 500, 2900, 528]):
        r = ai_review.crop_rect(box, 28, W, H)
        assert 0 <= r[0] < r[2] <= W and 0 <= r[1] < r[3] <= H
        assert r[0] <= box[0] and r[2] >= box[2] and r[1] <= box[1] and r[3] >= box[3] or \
            (box[2] - box[0]) + 56 > config.AI_IMAGE_CROP_MAX_SIDE
    r = ai_review.crop_rect([1400, 600, 1460, 628], 28, W, H)
    assert r[2] - r[0] >= config.AI_IMAGE_CROP_MIN_W            # เห็นคำข้าง ๆ
    assert r[2] - r[0] <= config.AI_IMAGE_CROP_MAX_SIDE          # ไม่ต้องย่อ = ตัวอักษรขนาดจริง
    assert r[1] < 600 - 28 and r[3] > 628 + 28                   # เห็นบรรทัดบน/ล่างบางส่วน


def test_long_context_is_trimmed_not_downscaled():
    """จุดกว้าง 600 px: บริบทเกิน 768 ⇒ ตัดบริบท (scale 1) ไม่ย่อทั้งครอป"""
    im = _noise(3)
    part, info = ai_review.crop_part(im, [1000, 600, 1600, 628], 28)
    assert info["scale"] == 1.0 and max(part["w"], part["h"]) <= config.AI_IMAGE_CROP_MAX_SIDE
    b = info["box"]
    assert b[0] < b[2] and b[1] < b[3] and 0 <= b[0] and b[2] <= 1000


def test_very_wide_spot_is_downscaled_to_the_cap():
    im = _noise(4)
    part, info = ai_review.crop_part(im, [100, 600, 2900, 628], 28)
    assert info["scale"] < 1.0 and max(part["w"], part["h"]) == config.AI_IMAGE_CROP_MAX_SIDE


# ── ② ข้อมูลที่ส่ง ─────────────────────────────────────────────────────

def test_sends_crops_cut_from_the_vision_images_one_request(tmp_path):
    pr, ims = _pr(TA, TB, str(tmp_path))
    seen = []
    _go(pr, _all("real"), tmp_path, seen)
    assert len(seen) == 1                                          # 1 คำขอต่อคู่
    body = seen[0]
    assert body["contract"] == "artwork-v2-image/2" and "images" not in body
    assert body["image_sizes"] == {"a": {"w": W, "h": H}, "b": {"w": W, "h": H}}
    ids = [c["id"] for c in body["candidates"]]
    assert len(ids) == len(pr["findings"]) >= 2
    assert [(c["candidate"], c["side"]) for c in body["crops"]] == \
        [(i, s) for i in ids for s in ("a", "b")]                 # A/B แยกรูป เรียงตามจุด
    for c in body["candidates"]:
        for s in ("a", "b"):
            assert set(c[s]["crop"]) == {"w", "h", "box"}
    # พิกเซลของครอป = ภาพที่ส่ง Vision ตรงตำแหน่งนั้น (เทียบกับไฟล์ที่เขียนจริง)
    for c in body["crops"]:
        f = next(g for g in pr["findings"] if "F%d" % g["id"] == c["candidate"])
        box, lh = ai_review._spot_px(f, c["side"])
        r = ai_review.crop_rect(box, lh, W, H)
        src = cv2.imread(os.path.join(str(tmp_path), "p1_%s.jpg" % c["side"]))
        ref = src[r[1]:r[3], r[0]:r[2]].astype(float)
        got = _dec(c).astype(float)
        assert got.shape == ref.shape and np.abs(got - ref).mean() < 6.0
        other = cv2.imread(os.path.join(str(tmp_path), "p1_%s.jpg" % ("b" if c["side"] == "a" else "a")))
        assert np.abs(got - other[r[1]:r[3], r[0]:r[2]].astype(float)).mean() > 40.0


def test_unreadable_jpeg_falls_back_to_algorithm(tmp_path):
    pr, _ = _pr(TA, TB, str(tmp_path), fake_bytes=True)
    before = json.dumps(pr["findings"], sort_keys=True, default=str)
    seen, warns = [], []
    st, _ = _go(pr, _all("noise"), tmp_path, seen, warns)
    assert seen == [] and pr["ai"]["status"] == "failed" and st["pairs_failed"] == 1
    assert json.dumps(pr["findings"], sort_keys=True, default=str) == before
    assert warns and "ภาพ" in warns[0]


# ── ③ เพดาน + จุดที่ส่งไม่ได้ (แผน ก) ─────────────────────────────────────

def test_cap_sends_red_first_and_keeps_the_rest(tmp_path, monkeypatch):
    pr, _ = _pr(TA, TB, str(tmp_path))
    fs = pr["findings"]
    assert len(fs) >= 2
    fs[0]["severity"], fs[1]["severity"] = "yellow", "red"
    monkeypatch.setattr(config, "AI_IMAGE_MAX_CANDIDATES", 1)
    seen = []
    _go(pr, _all("noise"), tmp_path, seen)
    assert [c["id"] for c in seen[0]["candidates"]] == ["F%d" % fs[1]["id"]]   # แดงก่อน
    assert len(seen[0]["crops"]) == 2
    kept = next(f for f in pr["findings"] if f["id"] == fs[0]["id"])
    assert kept["severity"] == "yellow"                                     # คงระดับเดิม
    assert kept["ai"]["not_sent"].startswith("เกินเพดาน 1")
    assert any("AI ไม่ได้ตรวจจุดนี้" in n for n in kept["notes"])
    assert [f["id"] for f in pr["ai_dismissed"]] == [fs[1]["id"]]
    ai = pr["ai"]
    assert ai["not_sent"] == len(fs) - 1 and ai["crops"] == 2
    assert ai["image_verdicts"]["not_sent"] == len(fs) - 1
    assert ai["reviewed"] == 1 and ai["reviewable"] == 1


def test_side_without_position_is_not_sent(tmp_path):
    pr, _ = _pr(TA, TB, str(tmp_path))
    f = pr["findings"][0]
    for k in ("box", "word_box", "est_box"):
        f["b"][k] = None
    sev = f["severity"]
    seen = []
    _go(pr, _all("noise"), tmp_path, seen)
    assert "F%d" % f["id"] not in [c["id"] for c in seen[0]["candidates"]]
    assert f in pr["findings"] and f["severity"] == sev
    assert "ฝั่ง B" in f["ai"]["not_sent"]


def test_nothing_sendable_means_no_request(tmp_path):
    pr, _ = _pr(TA, TB, str(tmp_path))
    for f in pr["findings"]:
        for k in ("box", "word_box", "est_box"):
            f["a"][k] = None
    seen = []
    st, _ = _go(pr, _all("noise"), tmp_path, seen)
    assert seen == [] and pr["ai"]["status"] == "skipped" and st["pairs_failed"] == 0
    assert all(f["severity"] in ("red", "yellow") for f in pr["findings"])
    assert all("AI ไม่ได้ตรวจจุดนี้" in " ".join(f["notes"]) for f in pr["findings"])


def test_est_box_is_used_for_the_empty_side(tmp_path):
    pr, _ = _pr(TA, TB, str(tmp_path))
    f = pr["findings"][0]
    f["b"]["box"] = f["b"]["word_box"] = None
    f["b"]["est_box"] = [1200, 480, 1300, 508]
    box, _ = ai_review._spot_px(f, "b")
    assert box == [1200, 480, 1300, 508]
    seen = []
    _go(pr, _all("real"), tmp_path, seen)
    assert "F%d" % f["id"] in [c["id"] for c in seen[0]["candidates"]]


# ── ④ ข้อความโค้ง ────────────────────────────────────────────────────

def test_real_on_curved_text_stays_yellow(tmp_path):
    pr, _ = _pr(TA, TB, str(tmp_path))
    f = pr["findings"][0]
    f["curved"] = True
    _go(pr, _all("real"), tmp_path)
    assert f["severity"] == "yellow" and any("โค้ง" in n for n in f["notes"])
    others = [g for g in pr["findings"] if g is not f]
    assert others and all(g["severity"] == "red" for g in others)
    assert pr["ai"]["image_verdicts"]["curved_yellow"] == 1


def test_curved_card_class_also_stays_yellow(tmp_path):
    pr, _ = _pr(TA, TB, str(tmp_path))
    f = pr["findings"][0]
    f["class"] = "CURVED"
    _go(pr, _all("real"), tmp_path)
    assert f["severity"] == "yellow"


def test_curved_flag_off_lets_ai_make_it_red(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AI_IMAGE_CURVED_YELLOW", False)
    pr, _ = _pr(TA, TB, str(tmp_path))
    f = pr["findings"][0]
    f["curved"] = True
    _go(pr, _all("real"), tmp_path)
    assert f["severity"] == "red"


def test_crops_off_is_the_whole_image_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AI_IMAGE_CROPS", False)
    pr, _ = _pr(TA, TB, str(tmp_path))
    seen = []
    _go(pr, _all("real"), tmp_path, seen)
    assert seen[0]["contract"] == "artwork-v2-image/1" and "crops" not in seen[0]
    assert set(seen[0]["images"]) == {"a", "b"}
    assert all("crop" not in c["a"] for c in seen[0]["candidates"])


# ── ⑤ ทั้งเส้น (pipeline จริง) ─────────────────────────────────────────

FULL = [{"a": {"page": 0, "bbox": [0, 0, 1, 1]}, "b": {"page": 0, "bbox": [0, 0, 1, 1]}}]
PA = ["Sodium 475 mg 20%", "Fat 1.5g", "Net weight 85 g"]
PB = ["Sodium 475 mg 24%", "Fat 1.5g", "Net weight 85 g"]


def _pdf(lines):
    d = fitz.open()
    p = d.new_page(width=400, height=300)
    for i, t in enumerate(lines):
        p.insert_text((20, 40 + i * 30), t, fontsize=12)
    return d.tobytes()


def _fake_annotate(groups, poster=None, key=None):
    from PIL import Image
    import io
    res, ids = {}, []
    for g in groups:
        for it in g:
            ids.append(it["id"])
            w, h = Image.open(io.BytesIO(it["jpeg"])).size
            T = PA if it["id"].endswith("a") else PB
            lines = [(t, int(0.05 * w), int(h * (0.1 + 0.8 * i / len(T))),
                      {"cw": max(4, w // 40), "h": max(8, h // 15)}) for i, t in enumerate(T)]
            res[it["id"]] = {"ok": True, "error": "", "fta": fta(lines, w, h), "request_index": 0}
    return {"results": res, "calls": [{"index": 0, "phase": "main", "images": ids,
                                       "json_bytes": 10, "status": 200, "attempts": 1, "ms": 1,
                                       "error": "", "model_requested": config.MODEL,
                                       "endpoint": config.ENDPOINT, "at": "t"}]}


def test_end_to_end_crops_come_from_the_run_images(monkeypatch):
    monkeypatch.setattr(vision_client, "annotate", _fake_annotate)
    keystore.save(KEY)
    job = jobs.create(("a.pdf", _pdf(PA)), ("b.pdf", _pdf(PB)))["id"]
    seen = []
    r = pipeline.run(job, FULL, ai_mode="image", ai_poster=_poster(_all("real"), seen))
    assert len(seen) == 1 and seen[0]["contract"] == "artwork-v2-image/2"
    rd = jobs.run_dir(job, r["run"])
    for c in seen[0]["crops"]:
        src = cv2.imread(os.path.join(rd, "img", "p1_%s.jpg" % c["side"]))
        assert src is not None and c["w"] <= src.shape[1] and c["h"] <= src.shape[0]
    pr = r["pairs"][0]
    assert r["verdict"] == "FAIL" and pr["ai"]["crops"] == len(seen[0]["crops"])
    log = r["log_text"]
    assert "image_crops: crops=" in log and "AI_IMAGE_CROPS" in log and "AI_IMAGE_CURVED_YELLOW" in log
    assert "b64" not in json.dumps(r, default=str) and KEY not in log


# ── ⑥ workflow (Code node ตัวจริงผ่าน node) ───────────────────────────

HARNESS = r"""
const fs = require('fs');
const w = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const body = JSON.parse(fs.readFileSync(0, 'utf8'));
const code = w.nodes.find((x) => x.name === 'Build Gemini request').parameters.jsCode;
const out = new Function('$input', code)({ first: () => ({ json: { body } }) });
console.log(JSON.stringify(out[0].json));
"""


def _build(body):
    if not shutil.which("node"):
        pytest.skip("ไม่มี node")
    r = subprocess.run(["node", "-e", HARNESS, "x", WF], input=json.dumps(body),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _crop(cid, side, b64):
    return {"candidate": cid, "side": side, "mime": "image/jpeg", "w": 600, "h": 120, "b64": b64}


CB = {"contract": "artwork-v2-image/2", "mode": "image",
      "zone_a": [{"id": "A0", "words": ["Fat", "20%"], "word_conf": [1, 1]}],
      "zone_b": [{"id": "B0", "words": ["Fat", "24%"], "word_conf": [1, 1]}],
      "candidates": [{"id": "F1", "a": {"crop": {"w": 600, "h": 120, "box": [1, 2, 3, 4]}},
                      "b": {"crop": {"w": 600, "h": 120, "box": [1, 2, 3, 4]}}},
                     {"id": "F2", "a": {}, "b": {}}],
      "crops": [_crop("F2", "b", "RjJC"), _crop("F1", "a", "RjFB"), _crop("F1", "b", "RjFC"),
                _crop("F2", "a", "RjJB")],
      "image_sizes": {"a": {"w": 3682, "h": 1457}, "b": {"w": 3343, "h": 1314}}}


def test_workflow_builds_crop_parts_in_candidate_order():
    b = _build(CB)
    assert b["valid"], b["error"]
    parts = b["gemini_request"]["contents"][0]["parts"]
    seq = [(p.get("text"), (p.get("inlineData") or {}).get("data")) for p in parts[:-1]]
    assert seq == [("F1 - A crop (version A):", None), (None, "RjFB"),
                   ("F1 - B crop (version B):", None), (None, "RjFC"),
                   ("F2 - A crop (version A):", None), (None, "RjJB"),
                   ("F2 - B crop (version B):", None), (None, "RjJC")]
    data = json.loads(parts[-1]["text"].split("\n", 1)[1])
    assert data["input"] == "crops" and data["image_a"] == {"w": 3682, "h": 1457}
    assert data["candidates"][0]["a"]["crop"]["box"] == [1, 2, 3, 4]
    assert "b64" not in parts[-1]["text"]


def test_workflow_rejects_incomplete_crops():
    bad = [dict(CB, crops=CB["crops"][:3]),                                    # F2 ไม่มี A
           dict(CB, crops=CB["crops"] + [dict(_crop("F1", "c", "eA"))]),      # side ผิด
           dict(CB, crops=[dict(c, mime="image/gif") for c in CB["crops"]]),
           dict(CB, candidates=[])]
    for body in bad:
        b = _build(body)
        assert b["valid"] is False and b["error"] and b["gemini_request"] is None


def test_prompt_says_look_first_and_never_copy_vision():
    import re
    code = next(n for n in json.load(open(WF, encoding="utf-8"))["nodes"]
                if n["name"] == "Build Gemini request")["parameters"]["jsCode"]
    p = re.search(r"const PROMPT = `(.*?)`;", code, re.S).group(1)
    for must in ("TWO CROPS PER CANDIDATE", "LOOK FIRST", "must come from the pixels only",
                 "Never copy Vision's text into them", "crop.box", "curved or rotated text"):
        assert must in p, must


def test_page_explains_not_sent_and_curved():
    js = open(os.path.join(ROOT, "static", "js", "artwork_v2.js"), encoding="utf-8").read()
    assert "ai.not_sent" in js and "iv.curved_yellow" in js and "iv.not_sent" in js
    assert "AI ไม่ได้ตรวจจุดนี้" in js
    assert diaglog is not None
