"""Artwork V2 — โหมด "AI ดูภาพตัดสิน" แบบ **ไม่เห็นข้อความ** + **ครอปความละเอียดสูง** (9 ต.ค. รอบ 5)

ที่มา: สถานี 9 ต.ค. งาน John West (ครอปต่อจุด) — Gemini ตอบ ``real`` ให้ 17/17 จุดที่หลักฐานภาพบอกว่าเหมือน
และ ``a_seen``/``b_seen`` เป็นคำที่ Vision อ่านผิดเป๊ะ ⇒ ข้อความใน DATA ถูกลอกเป็นคำตอบ · ตัวอักษร
ของงานนั้นสูงแค่ 10-13 px ที่ 400 dpi. ล็อกไว้ที่นี่:

* ``AI_IMAGE_BLIND``: payload ไม่มีข้อความของ Vision เลย · แอปเทียบ ``a_seen``/``b_seen`` เอง ·
  ไม่มั่นใจ/ขัดกัน ⇒ เหลือง · workflow ตัดข้อความทิ้งซ้ำ + ใช้ prompt ชุด blind
* ``AI_IMAGE_CROP_HIRES``: ครอปตัวอักษรเล็กถูกเรนเดอร์ใหม่จาก PDF ตรงบริเวณเดียวกัน (ทุกมุมหมุน) ·
  ไม่เกินเพดาน dpi / ความละเอียดจริงของภาพสแกน · ด้านยาวไม่เกินเพดาน · เรนเดอร์ไม่ได้ = ครอป JPEG เดิม
"""
import base64
import json
import os
import re
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fitz  # noqa: E402
import test_artwork_v2_ai_image_crops as C  # noqa: E402
from artwork_v2_fake import fta  # noqa: E402

from artwork_v2 import (ai_review, config, imaging, jobs, keystore,  # noqa: E402
                        pipeline, vision_client)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "N8N_ARTWORK_V2_IMAGE_PROMPT.md")


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
    monkeypatch.setattr(config, "AI_IMAGE_BLIND", True)
    monkeypatch.setattr(config, "AI_IMAGE_CROP_HIRES", True)
    monkeypatch.setattr(config, "AI_IMAGE_MAX_CANDIDATES", 40)
    monkeypatch.setattr(config, "AI_IMAGE_CURVED_YELLOW", True)
    os.makedirs(config.JOBS_DIR)
    yield


def _answer(fn):
    """คำตอบต่อจุด: ``fn(cid) -> (a_seen, b_seen, verdict)``"""
    def ans(body):
        revs = []
        for c in body["candidates"]:
            a, b, v = fn(c["id"])
            revs.append({"candidate": c["id"], "a_seen": a, "b_seen": b, "verdict": v,
                         "reason": "r", "suggestion": "s"})
        return {"reviews": revs, "items": [], "summary": "s", "suggestions": [], "engine": "g"}
    return ans


# ── ① แอปตัดสินจากสิ่งที่ AI อ่าน ───────────────────────────────────

@pytest.mark.parametrize("a,b,said,want", [
    ("20%", "24%", "real", "real"),
    ("D-calcium", "D-Calcium", "real", "real"),          # ตัวพิมพ์ต่าง = ต่างจริง
    ("Hwy,", "Hwy", "real", "real"),                     # คอมมาหาย
    ("", "USA", "real", "real"),                         # มีฝั่งเดียว
    ("1.5g", "15g", "real", "real"),                     # จุดทศนิยม
    ("Sodium 20%", "Sodium  20%", "noise", "noise"),     # ช่องว่าง
    ("Choice®", "ChoiceⓇ", "noise", "noise"),             # อักษรสมมูล
    ("½ cup", "1/2 cup", "noise", "noise"),
    ("(min)......0.16%", "(min)...0.16%", "noise", "noise"),  # จำนวนจุดไข่ปลา
])
def test_blind_verdict_compares_readings_like_the_algorithm(a, b, said, want):
    assert ai_review.blind_verdict(a, b, said) == (want, "")


@pytest.mark.parametrize("a,b,said,why", [
    ("2[?]%", "24%", "real", "[?]"),
    ("", "", "noise", "ไม่เห็นข้อความ"),
    ("20%", "24%", "uncertain", "อ่านไม่ชัด"),
    ("20%", "24%", "noise", "ขัด"),                     # AI บอกเหมือน แต่อ่านได้ต่าง
    ("20%", "20%", "real", "ขัด"),                      # AI บอกต่าง แต่อ่านได้เหมือน (เช่นตัวหนา)
    (None, "24%", "real", "ครบ"),
    (24, "24", "real", "ครบ"),
])
def test_blind_verdict_is_uncertain_when_not_sure(a, b, said, why):
    v, w = ai_review.blind_verdict(a, b, said)
    assert v == "uncertain" and why in w


# ── ② payload ไม่มีข้อความของ Vision ─────────────────────────────────

def test_blind_payload_carries_no_vision_text(tmp_path):
    pr, _ = C._pr(C.TA, C.TB, str(tmp_path))
    seen = []
    C._go(pr, _answer(lambda cid: ("x", "x", "noise")), tmp_path, seen)
    p = seen[0]
    assert p["contract"] == "artwork-v2-image/3" and p["blind"] is True
    assert "zone_a" not in p and "zone_b" not in p
    txt = json.dumps({k: v for k, v in p.items() if k != "crops"}, ensure_ascii=False)
    for word in ("Sodium", "475", "Fat", "per serving", "line_text", "diff", "class"):
        assert word not in txt, word
    for c in p["candidates"]:
        assert set(c) == {"id", "a", "b"} and set(c["a"]) == {"crop"}
        assert set(c["a"]["crop"]) == {"w", "h", "box"}
    assert sorted(x["candidate"] for x in p["crops"]) == sorted(
        [c["id"] for c in p["candidates"]] * 2)


def test_blind_merge_red_noise_and_contradiction(tmp_path):
    pr, _ = C._pr(C.TA, C.TB, str(tmp_path))
    ids = ["F%d" % f["id"] for f in pr["findings"]]
    assert len(ids) >= 2
    plan = {ids[0]: ("20%", "24%", "real"), ids[1]: ("1.5g", "1.5g", "real")}
    for extra in ids[2:]:
        plan[extra] = ("same", "same", "noise")
    sm, _ = C._go(pr, _answer(lambda cid: plan[cid]), tmp_path)
    by = {"F%d" % f["id"]: f for f in pr["findings"] + pr["ai_dismissed"]}
    f0, f1 = by[ids[0]], by[ids[1]]
    assert f0["severity"] == "red" and f0["ai"]["blind"] and f0["ai"]["ai_verdict"] == "real"
    assert any("ไม่เห็นข้อความของ Vision" in n for n in f0["notes"])
    # AI บอกต่าง แต่อ่านได้เหมือน ⇒ ไม่แน่ใจ (ไม่แดง · ไม่พับ)
    assert f1["severity"] == "yellow" and f1["ai"]["verdict"] == "uncertain"
    assert f1 in pr["findings"] and "ขัด" in f1["ai"]["decided_why"]
    assert pr["ai"]["blind"] is True and pr["ai"]["blind_overruled"] >= 1
    for extra in ids[2:]:
        assert by[extra]["severity"] == "dismissed" and by[extra] in pr["ai_dismissed"]


def test_blind_off_sends_vision_text_like_before(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AI_IMAGE_BLIND", False)
    pr, _ = C._pr(C.TA, C.TB, str(tmp_path))
    seen = []
    C._go(pr, C._all("real"), tmp_path, seen)
    assert seen[0]["contract"] == "artwork-v2-image/2" and "zone_a" in seen[0]
    f = pr["findings"][0]
    assert f["severity"] == "red" and not f["ai"].get("blind")


def test_blind_needs_crops(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AI_IMAGE_CROPS", False)
    pr, _ = C._pr(C.TA, C.TB)
    seen = []
    imgs = {s: {"mime": "image/jpeg", "w": 1, "h": 1, "b64": "QUJD"} for s in "ab"}
    monkeypatch.setattr(ai_review, "image_part", lambda path, size: imgs["a"])
    for s in "ab":
        open(os.path.join(str(tmp_path), "p1_%s.jpg" % s), "wb").write(b"x")
    C._go(pr, C._all("real"), tmp_path, seen)
    assert seen[0]["contract"] == "artwork-v2-image/1" and "zone_a" in seen[0]


# ── ③ ครอปความละเอียดสูง (ตัวเลขล้วน) ────────────────────────────────

def test_hires_scale_targets_text_height_within_caps():
    assert ai_review.hires_scale([0, 0, 100, 12], 12, 3.0) == pytest.approx(3.0)
    assert ai_review.hires_scale([0, 0, 100, 12], 12, 2.0) == pytest.approx(2.0)   # เพดาน dpi
    assert ai_review.hires_scale([0, 0, 100, 30], 30, 3.0) == 1.0                  # ใหญ่พอแล้ว (1.2 < 1.25)
    assert ai_review.hires_scale([0, 0, 100, 12], 12, 1.0) == 1.0
    assert ai_review.hires_scale([0, 0, 700, 12], 12, 3.0) == 1.0                  # ตัวจุดกว้างเกินเพดาน
    k = ai_review.hires_scale([0, 0, 300, 12], 12, 3.0)
    assert 1.25 <= k < 3.0 and (300 + 24) * k <= config.AI_IMAGE_CROP_MAX_SIDE + 1e-6


class _FakeHi:
    def __init__(self, kmax=3.0, fail=False):
        self.kmax, self.base_dpi, self.fail, self.calls = kmax, 400.0, fail, []

    def render(self, region, out_wh):
        self.calls.append((list(region), tuple(out_wh)))
        if self.fail:
            return None
        return np.full((int(out_wh[1]), int(out_wh[0]), 3), 7, np.uint8)


def test_crop_part_uses_the_hires_render_for_small_text():
    im = C._noise(1)
    box = [1000, 500, 1100, 512]
    hi = _FakeHi()
    part, info = ai_review.crop_part(im, box, 12, hi)
    assert info["hires"] == pytest.approx(3.0) and info["dpi"] == pytest.approx(1200.0)
    assert max(info["w"], info["h"]) <= config.AI_IMAGE_CROP_MAX_SIDE and info["scale"] == 1.0
    region, out = hi.calls[0]
    assert out == (info["w"], info["h"])
    # บริเวณที่ขอเรนเดอร์ = กรอบครอป ÷ 3 ในพิกัดของภาพที่ส่ง และคลุมตัวจุด
    assert region[0] <= box[0] and region[2] >= box[2] and region[1] <= box[1] and region[3] >= box[3]
    assert (region[2] - region[0]) * 3 == pytest.approx(out[0], abs=1)
    dec = C._dec(part)
    assert abs(float(dec.mean()) - 7) < 3
    # ตำแหน่งจุดในครอปตรงกับกรอบที่ขยายแล้ว
    x0 = info["region"][0]
    assert info["box"][0] == round((box[0] * 3 - x0) / info["w"] * 1000)


def test_crop_part_falls_back_to_the_jpeg_crop():
    im = C._noise(1)
    box = [1000, 500, 1100, 512]
    plain = ai_review.crop_part(im, box, 12)
    for hi in (_FakeHi(fail=True), _FakeHi(kmax=1.0), None):
        got = ai_review.crop_part(im, box, 12, hi)
        assert got[0]["b64"] == plain[0]["b64"] and "hires" not in got[1]
    big = ai_review.crop_part(im, [1000, 500, 1100, 540], 40, _FakeHi())
    assert "hires" not in big[1]                     # ตัวอักษรใหญ่พอ ไม่เรนเดอร์ใหม่


# ── ④ เรนเดอร์จาก PDF จริงตรงบริเวณเดียวกัน (ทุกมุมหมุน) ──────────────

def _small_pdf(path):
    """หน้าเต็มไปด้วยลายที่ไม่ซ้ำ (ช่องสีสุ่ม 6 pt) + ตัวหนังสือเล็ก ⇒ ตรวจได้ว่าเรนเดอร์ตรงบริเวณไหน"""
    d = fitz.open()
    p = d.new_page(width=400, height=300)
    rng = np.random.RandomState(3)
    for y in range(0, 300, 6):
        for x in range(0, 400, 6):
            g = float(rng.randint(0, 5)) / 4
            p.draw_rect(fitz.Rect(x, y, x + 6, y + 6), color=None, fill=(g, 1 - g, 0.5))
    for i in range(14):
        p.insert_text((10 + 7 * (i % 3), 20 + i * 19), "Sodium %d mg %d%%" % (rng.randint(999), i),
                      fontsize=5)
    d.save(path)


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_hires_render_lands_on_the_same_region(tmp_path, rot):
    path = str(tmp_path / "a.pdf")
    _small_pdf(path)
    src = imaging.Source(path)
    bbox = [0.05, 0.05, 0.9, 0.9]
    img, info = src.render_zone(0, bbox)
    sent = imaging.rotate_img(img, rot)
    side = {"page": 0, "bbox": bbox, "sent_px": [sent.shape[1], sent.shape[0]], "render": info}
    if rot:
        side["rotate"] = rot
    hi = pipeline.HiresSide(src, side)
    assert hi.kmax == pytest.approx(config.AI_IMAGE_CROP_DPI_MAX / info["dpi"], rel=0.02)
    H, W = sent.shape[:2]
    region = [W * 0.2, H * 0.3, W * 0.2 + 300, H * 0.3 + 120]
    k = 2.5
    out = (int(300 * k), int(120 * k))
    got = hi.render(region, out)
    assert got is not None and (got.shape[1], got.shape[0]) == out
    small = cv2.resize(got, (300, 120), interpolation=cv2.INTER_AREA)
    ref = sent[int(region[1]):int(region[1]) + 120, int(region[0]):int(region[0]) + 300]
    err = float(np.abs(small.astype(int) - ref.astype(int)).mean())
    shifted = sent[int(region[1]) + 25:int(region[1]) + 145, int(region[0]):int(region[0]) + 300]
    assert err < 6, err
    assert float(np.abs(small.astype(int) - shifted.astype(int)).mean()) > err * 2


def test_scanned_pdf_is_not_upscaled_past_its_pixels(tmp_path):
    d = fitz.open()
    p = d.new_page(width=400, height=300)
    scan = (C._noise(2)[:1250, :1667])                 # 300 dpi บนหน้า 400×300 pt
    ok, buf = cv2.imencode(".png", scan)
    p.insert_image(p.rect, stream=buf.tobytes())
    path = str(tmp_path / "scan.pdf")
    d.save(path)
    src = imaging.Source(path)
    img, info = src.render_zone(0, [0.1, 0.1, 0.8, 0.8])
    hi = pipeline.HiresSide(src, {"page": 0, "bbox": [0.1, 0.1, 0.8, 0.8],
                                  "sent_px": [img.shape[1], img.shape[0]], "render": info})
    assert hi.raster_dpi == pytest.approx(300, rel=0.05)
    assert hi.kmax == 1.0                              # 400 dpi > ความละเอียดจริง ⇒ ไม่ขยาย


def test_photo_source_never_rerenders(tmp_path):
    path = str(tmp_path / "a.jpg")
    cv2.imwrite(path, C._noise(1))
    src = imaging.Source(path)
    hi = pipeline.HiresSide(src, {"page": 0, "bbox": [0, 0, 1, 1], "sent_px": [10, 10]})
    assert hi.kmax == 1.0


# ── ⑤ ทั้งเส้นด้วย PDF ตัวหนังสือเล็ก ───────────────────────────────

def _fake_annotate_small(groups, poster=None, key=None):
    from PIL import Image
    import io
    res, ids = {}, []
    for g in groups:
        for it in g:
            ids.append(it["id"])
            w, h = Image.open(io.BytesIO(it["jpeg"])).size
            T = C.PA if it["id"].endswith("a") else C.PB
            lines = [(t, int(0.05 * w), int(h * (0.1 + 0.8 * i / len(T))), {"cw": 7, "h": 11})
                     for i, t in enumerate(T)]
            res[it["id"]] = {"ok": True, "error": "", "fta": fta(lines, w, h), "request_index": 0}
    return {"results": res, "calls": [{"index": 0, "phase": "main", "images": ids,
                                       "json_bytes": 10, "status": 200, "attempts": 1, "ms": 1,
                                       "error": "", "model_requested": config.MODEL,
                                       "endpoint": config.ENDPOINT, "at": "t"}]}


def test_end_to_end_blind_with_hires_crops(monkeypatch):
    monkeypatch.setattr(vision_client, "annotate", _fake_annotate_small)
    keystore.save(C.KEY)
    job = jobs.create(("a.pdf", C._pdf(C.PA)), ("b.pdf", C._pdf(C.PB)))["id"]
    seen = []
    r = pipeline.run(job, C.FULL, ai_mode="image",
                     ai_poster=C._poster(_answer(lambda cid: ("20%", "24%", "real")), seen))
    assert len(seen) == 1 and seen[0]["contract"] == "artwork-v2-image/3"
    assert "Sodium" not in json.dumps({k: v for k, v in seen[0].items() if k != "crops"})
    pr = r["pairs"][0]
    assert pr["ai"]["blind"] is True and pr["ai"]["crops_hires"] >= 2
    assert pr["ai"]["crop_dpi_max"] > 400
    for c in seen[0]["crops"]:
        assert max(c["w"], c["h"]) <= config.AI_IMAGE_CROP_MAX_SIDE
    assert r["verdict"] == "FAIL"
    log = r["log_text"]
    assert "image_mode: blind=True" in log and "hires_crops=" in log and "blind: ai_said=real" in log
    assert "AI_IMAGE_BLIND" in log and "AI_IMAGE_CROP_HIRES" in log and C.KEY not in log


def test_end_to_end_hires_off_keeps_jpeg_crops(monkeypatch):
    monkeypatch.setattr(config, "AI_IMAGE_CROP_HIRES", False)
    monkeypatch.setattr(vision_client, "annotate", _fake_annotate_small)
    keystore.save(C.KEY)
    job = jobs.create(("a.pdf", C._pdf(C.PA)), ("b.pdf", C._pdf(C.PB)))["id"]
    r = pipeline.run(job, C.FULL, ai_mode="image",
                     ai_poster=C._poster(_answer(lambda cid: ("20%", "24%", "real"))))
    ai = r["pairs"][0]["ai"]
    assert "crops_hires" not in ai and ai["crops"] >= 2


# ── ⑥ workflow + prompt ──────────────────────────────────────────────

BLIND = {"contract": "artwork-v2-image/3", "mode": "image", "blind": True,
         "candidates": [{"id": "F1", "a": {"crop": {"w": 600, "h": 120, "box": [1, 2, 3, 4]}},
                         "b": {"crop": {"w": 600, "h": 120, "box": [5, 6, 7, 8]}}}],
         "crops": [C._crop("F1", "a", "RjFB"), C._crop("F1", "b", "RjFC")]}


def _blind_prompt():
    w = json.load(open(C.WF, encoding="utf-8"))
    code = next(n for n in w["nodes"] if n["name"] == "Build Gemini request")["parameters"]["jsCode"]
    return re.search(r"const PROMPT_BLIND = `(.*?)`;", code, re.S).group(1)


def test_workflow_blind_sends_only_ids_and_boxes():
    leaky = dict(BLIND, zone_a=[{"id": "A0", "words": ["Sodium"]}],
                 candidates=[dict(BLIND["candidates"][0], **{"class": "NUMBER"},
                                  a=dict(BLIND["candidates"][0]["a"], line_text="Sodium 20%"))])
    for body in (BLIND, leaky):
        b = C._build(body)
        assert b["valid"], b["error"]
        assert b["blind"] is True
        req = b["gemini_request"]
        assert req["systemInstruction"]["parts"][0]["text"] == _blind_prompt()
        parts = req["contents"][0]["parts"]
        assert [p.get("text") for p in parts[:4:2]] == ["F1 - A crop (version A):",
                                                         "F1 - B crop (version B):"]
        data = json.loads(parts[-1]["text"].split("\n", 1)[1])
        assert data == {"mode": "image", "input": "blind",
                        "candidates": [{"id": "F1", "a": {"box": [1, 2, 3, 4]},
                                        "b": {"box": [5, 6, 7, 8]}}]}
        assert "Sodium" not in parts[-1]["text"]


def test_workflow_blind_rejects_missing_crops():
    for bad in (dict(BLIND, crops=None), dict(BLIND, crops=[BLIND["crops"][0]]),
                dict(BLIND, candidates=[])):
        b = C._build(bad)
        assert b["valid"] is False and b["error"]


def test_blind_prompt_rules_and_doc_match():
    p = _blind_prompt()
    for must in ("NOT given any text", "from the pixels only", "Never assume B equals A",
                 "Never guess a character", "same extent", "exactly ONE entry for EVERY candidate",
                 "No quotes, no labels", "Never output any number for confidence"):
        assert must in p, must
    assert "Vision" not in p and "OCR result" in p
    doc = open(DOC, encoding="utf-8").read()
    d = re.search(r"<!-- PROMPT_IMAGE_BLIND START -->\n```text\n(.*?)\n```\n"
                  r"<!-- PROMPT_IMAGE_BLIND END -->", doc, re.S).group(1)
    assert d == p
    assert "ARTWORK_V2_AI_IMAGE_BLIND" in doc and "ARTWORK_V2_AI_IMAGE_CROP_HIRES" in doc


def test_page_shows_blind_and_hires():
    js = open(os.path.join(ROOT, "static", "js", "artwork_v2.js"), encoding="utf-8").read()
    assert "ไม่เห็นข้อความของ Vision" in js and "ai.crops_hires" in js and "ai.blind_overruled" in js
