"""Artwork V2 — โหมด "AI ดูภาพตัดสิน": เก็บครอปที่ส่งจริง + หลักฐานภาพก่อน AI (10 ต.ค.)

ที่มา: สถานี 10 ต.ค. งาน John West (blind + ครอปความละเอียดสูง) — Gemini ตอบ ``20%``/``24%`` ให้ 4 จุดที่
อยู่คนละที่ · อ่านแถวข้างเคียงในครอป (F13 → ``26 g`` · F14 → ``1 g``) · และพับ 15 จุดโดยไม่ผ่านหลักฐานภาพ
(รวม F8 ``فى``/``في``) ⇒ ล็อกไว้ที่นี่:

* ``AI_IMAGE_SAVE_CROPS``: ครอปที่ส่ง = ไฟล์ ``img/ai<คู่>_F<จุด>_<a|b>.jpg`` (ไบต์เดียวกัน) · ``f["ai_crop"]``
  มีกรอบ 0-1000 ที่บอก AI · route เสิร์ฟได้ · ชื่ออื่น/path traversal ไม่ได้ · ปิดธง = ไม่เขียนอะไร
* ``AI_IMAGE_PIXEL_FIRST``: หลักฐานภาพทำงานก่อน AI ครั้งเดียว · จุดที่ภาพบอกว่าเหมือนไม่ถูกส่ง ·
  AI พับจุดที่ภาพ (เวกเตอร์) ยืนยันว่าต่างไม่ได้ · ภาพสแกน/ตรวจไม่ได้ = พับได้แบบเดิม · โหมดอื่นลำดับเดิม
"""
import base64
import json
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_artwork_v2_ai_image_crops as C  # noqa: E402
import test_artwork_v2_ai_image_blind as BL  # noqa: E402

from artwork_v2 import (ai_review, config, jobs, keystore, pipeline,  # noqa: E402
                        pixverify, routes, vision_client)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js", "artwork_v2.js")


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
    monkeypatch.setattr(config, "AI_IMAGE_CROP_HIRES", False)
    monkeypatch.setattr(config, "AI_IMAGE_MAX_CANDIDATES", 40)
    monkeypatch.setattr(config, "AI_IMAGE_CURVED_YELLOW", True)
    monkeypatch.setattr(config, "AI_IMAGE_SAVE_CROPS", True)
    monkeypatch.setattr(config, "AI_IMAGE_PIXEL_FIRST", True)
    os.makedirs(config.JOBS_DIR)
    yield


# ── ① เก็บครอปที่ส่งจริง ─────────────────────────────────────────────

def test_saved_crops_are_the_exact_bytes_sent(tmp_path):
    pr, _ = C._pr(C.TA, C.TB, str(tmp_path))
    seen = []
    C._go(pr, BL._answer(lambda cid: ("x", "y", "real")), tmp_path, seen)
    crops = seen[0]["crops"]
    assert crops
    for c in crops:
        name = "ai%d_%s_%s.jpg" % (pr["n"], c["candidate"], c["side"])
        with open(os.path.join(str(tmp_path), name), "rb") as fh:
            assert fh.read() == base64.b64decode(c["b64"])      # ไบต์เดียวกับที่ส่ง
        assert routes._IMG_RE.match(name)                       # route เสิร์ฟชื่อนี้ได้
    by = {"F%d" % f["id"]: f for f in pr["findings"] + pr.get("ai_dismissed", [])}
    for cand in seen[0]["candidates"]:
        f = by[cand["id"]]
        for s in ("a", "b"):
            ac = f["ai_crop"][s]
            assert ac["box"] == cand[s]["crop"]["box"]           # กรอบเดียวกับที่บอก AI
            assert (ac["w"], ac["h"]) == (cand[s]["crop"]["w"], cand[s]["crop"]["h"])
    assert pr["ai"]["crops_saved"] == len(crops)


def test_save_crops_off_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AI_IMAGE_SAVE_CROPS", False)
    pr, _ = C._pr(C.TA, C.TB, str(tmp_path))
    before = set(os.listdir(str(tmp_path)))
    C._go(pr, BL._answer(lambda cid: ("x", "y", "real")), tmp_path)
    assert set(os.listdir(str(tmp_path))) == before
    assert all("ai_crop" not in f for f in pr["findings"] + pr.get("ai_dismissed", []))
    assert "crops_saved" not in pr["ai"]


def test_save_crops_without_folder_is_harmless():
    f = {"id": 3, "notes": []}
    n = ai_review.save_crops(None, 1, [{"candidate": "F3", "side": "a", "b64": "eA=="}],
                             {"F3": {"a": {"w": 1, "h": 1, "box": [0, 0, 1, 1]}}}, [f])
    assert n == 0 and "ai_crop" not in f


@pytest.mark.parametrize("name,ok", [
    ("ai1_F12_a.jpg", True), ("ai3_F7_b.jpg", True), ("p1_a.jpg", True), ("pv4.jpg", True),
    ("ai1_F12_c.jpg", False), ("ai1_12_a.jpg", False), ("../ai1_F1_a.jpg", False),
    ("ai1_F1_a.jpg.png", False), ("aiX_F1_a.jpg", False)])
def test_route_name_pattern(name, ok):
    assert bool(routes._IMG_RE.match(name)) is ok


# ── ② AI พับจุดที่ภาพยืนยันว่าต่างไม่ได้ ─────────────────────────────

def _with_pixel(pr, status, raster=False):
    for f in pr["findings"]:
        f["pixel"] = {"status": status, "evidence": None, "checks": []}
        if raster:
            f["pixel"]["raster"] = True


def test_noise_cannot_fold_a_vector_pixel_diff(tmp_path):
    pr, _ = C._pr(C.TA, C.TB, str(tmp_path))
    _with_pixel(pr, "DIFF")
    sev = {f["id"]: f["severity"] for f in pr["findings"]}
    C._go(pr, BL._answer(lambda cid: ("same", "same", "noise")), tmp_path)
    assert pr["ai_dismissed"] == []
    for f in pr["findings"]:
        assert f["severity"] == sev[f["id"]]                     # คงระดับเดิม
        assert f["ai"]["pixel_kept"] is True
        assert any("ภาพจากไฟล์ต้นฉบับต่างกัน" in n for n in f["notes"])
    assert pr["ai"]["image_verdicts"]["pixel_kept"] == len(sev)


@pytest.mark.parametrize("status,raster", [("DIFF", True), ("UNVERIFIABLE", False)])
def test_noise_still_folds_when_pixel_is_not_proof(tmp_path, status, raster):
    pr, _ = C._pr(C.TA, C.TB, str(tmp_path))
    _with_pixel(pr, status, raster)
    n = len(pr["findings"])
    C._go(pr, BL._answer(lambda cid: ("same", "same", "noise")), tmp_path)
    assert len(pr["ai_dismissed"]) == n and pr["findings"] == []


def test_pixel_guard_off_folds_like_before(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "AI_IMAGE_PIXEL_FIRST", False)
    pr, _ = C._pr(C.TA, C.TB, str(tmp_path))
    _with_pixel(pr, "DIFF")
    n = len(pr["findings"])
    C._go(pr, BL._answer(lambda cid: ("same", "same", "noise")), tmp_path)
    assert len(pr["ai_dismissed"]) == n


def test_real_on_pixel_diff_is_red(tmp_path):
    pr, _ = C._pr(C.TA, C.TB, str(tmp_path))
    _with_pixel(pr, "DIFF")
    C._go(pr, BL._answer(lambda cid: ("20%", "24%", "real")), tmp_path)
    assert pr["findings"] and all(f["severity"] == "red" for f in pr["findings"])


# ── ③ ลำดับใน pipeline ──────────────────────────────────────────────

def _spy(monkeypatch, fold_first=False):
    """บันทึกลำดับการเรียก · ``fold_first`` = หลักฐานภาพปลอมพับจุดแรกของทุกคู่"""
    order, real_px, real_ai = [], pixverify.run, ai_review.run_all

    def px(pairs, *a, **k):
        order.append("pixel")
        if fold_first:
            for pr in pairs:
                fs = pr.get("findings") or []
                if fs:
                    f = fs[0]
                    f["pixel"] = {"status": "SAME", "evidence": None, "checks": [], "was": f["severity"]}
                    f["severity"] = "pixel_same"
                    pr["findings"] = fs[1:]
                    pr["pixel_same"] = [f]
            return {"enabled": True, "same": 1, "pairs": []}
        return real_px(pairs, *a, **k)

    def ai(pairs, mode, *a, **k):
        order.append("ai:" + mode)
        return real_ai(pairs, mode, *a, **k)

    monkeypatch.setattr(pixverify, "run", px)
    monkeypatch.setattr(ai_review, "run_all", ai)
    return order


def _job():
    keystore.save(C.KEY)
    return jobs.create(("a.pdf", C._pdf(C.PA)), ("b.pdf", C._pdf(C.PB)))["id"]


def test_image_mode_runs_pixel_once_before_ai(monkeypatch):
    # สองจุดต่าง (20%→24% · 1.5g→1.6g) — สายลับพับจุดแรก ⇒ อีกจุดต้องถูกส่ง AI
    monkeypatch.setattr(C, "PB", ["Sodium 475 mg 24%", "Fat 1.6g", "Net weight 85 g"])
    monkeypatch.setattr(vision_client, "annotate", BL._fake_annotate_small)
    order = _spy(monkeypatch, fold_first=True)
    seen = []
    keystore.save(C.KEY)
    job = jobs.create(("a.pdf", C._pdf(C.PA)), ("b.pdf", C._pdf(C.PB)))["id"]
    r = pipeline.run(job, C.FULL, ai_mode="image",
                     ai_poster=C._poster(BL._answer(lambda cid: ("20%", "24%", "real")), seen))
    assert order == ["pixel", "ai:image"]
    pr = r["pairs"][0]
    folded = {"F%d" % f["id"] for f in pr["pixel_same"]}
    assert folded and seen
    assert not folded & {c["id"] for c in seen[0]["candidates"]}   # ไม่ส่งจุดที่ภาพพับแล้ว
    assert pr["ai"]["pixel_first"] is True and pr["ai"]["pixel_folded"] == len(folded)
    assert "before_ai=True" in r["log_text"]


@pytest.mark.parametrize("mode", ["off", "assist"])
def test_other_modes_keep_the_old_order(monkeypatch, mode):
    monkeypatch.setattr(vision_client, "annotate", BL._fake_annotate_small)
    order = _spy(monkeypatch)
    pipeline.run(_job(), C.FULL, ai_mode=mode,
                 ai_poster=C._poster(lambda body: {"reviews": [], "items": [], "summary": "",
                                                   "suggestions": [], "engine": "g"}))
    assert order == ["ai:" + mode, "pixel"]


def test_flag_off_keeps_the_old_order_in_image_mode(monkeypatch):
    monkeypatch.setattr(config, "AI_IMAGE_PIXEL_FIRST", False)
    monkeypatch.setattr(vision_client, "annotate", BL._fake_annotate_small)
    order = _spy(monkeypatch)
    r = pipeline.run(_job(), C.FULL, ai_mode="image",
                     ai_poster=C._poster(BL._answer(lambda cid: ("20%", "24%", "real"))))
    assert order == ["ai:image", "pixel"]
    assert "pixel_first" not in r["pairs"][0]["ai"]


def test_all_folded_by_pixel_means_no_ai_call(monkeypatch):
    monkeypatch.setattr(vision_client, "annotate", BL._fake_annotate_small)
    real_px = pixverify.run

    def fold_all(pairs, *a, **k):
        for pr in pairs:
            fs = pr.get("findings") or []
            for f in fs:
                f["pixel"] = {"status": "SAME", "evidence": None, "checks": [], "was": f["severity"]}
                f["severity"] = "pixel_same"
            pr["pixel_same"], pr["findings"] = fs, []
        return {"enabled": True, "same": 1, "pairs": []}

    monkeypatch.setattr(pixverify, "run", fold_all)
    seen = []
    r = pipeline.run(_job(), C.FULL, ai_mode="image",
                     ai_poster=C._poster(BL._answer(lambda cid: ("x", "y", "real")), seen))
    assert seen == []
    ai = r["pairs"][0]["ai"]
    assert ai["status"] == "skipped" and "หลักฐานภาพตัดสินครบ" in ai["reason"]
    assert real_px is not pixverify.run


def test_end_to_end_real_pixel_then_ai_and_log(monkeypatch):
    monkeypatch.setattr(vision_client, "annotate", BL._fake_annotate_small)
    seen = []
    r = pipeline.run(_job(), C.FULL, ai_mode="image",
                     ai_poster=C._poster(BL._answer(lambda cid: ("20%", "24%", "real")), seen))
    log = r["log_text"]
    assert "before_ai=True" in log
    assert "AI_IMAGE_PIXEL_FIRST" in log and "AI_IMAGE_SAVE_CROPS" in log
    pr = r["pairs"][0]
    sent = {c["id"] for c in seen[0]["candidates"]} if seen else set()
    for f in pr.get("pixel_same") or []:
        assert "F%d" % f["id"] not in sent
    if seen:
        assert "ai_crop: A=ai1_F" in log and "image_evidence: pixel_kept=" in log
        rd = jobs.run_dir(r["job"], r["run"])
        for c in seen[0]["crops"]:
            assert os.path.isfile(os.path.join(rd, "img", "ai1_%s_%s.jpg" % (c["candidate"], c["side"])))
    assert C.KEY not in log


# ── ④ หน้าเว็บ ──────────────────────────────────────────────────────

def _fn(src, name):
    i = src.index("function " + name + "(")
    d, j = 0, src.index("{", i)
    for k in range(j, len(src)):
        d += src[k] == "{"
        d -= src[k] == "}"
        if not d:
            return src[i:k + 1]
    raise AssertionError(name)


def test_page_shows_the_crops_with_the_spot_box():
    src = open(JS, encoding="utf-8").read()
    # ทั้งตารางข้างภาพ (SIDE_TABLE) และตารางแบบเดิมต้องแสดงครอป
    assert 'confShort(f), notes + aiNote(f) + aiCrops(f), {})' in src
    assert 'notes + aiNote(f) + aiCrops(f) + "</td></tr>"' in src
    assert 'aiNote(m) + aiCrops(m) + "</div>"' in src          # แถวกลุ่ม (LINE_GROUP) — ทุกสมาชิกต้องมีภาพด้วย
    code = ("const S={result:{job:'J1',run:'run_002'}};"
            "const esc=(s)=>String(s==null?'':s).replace(/[&<>\"']/g,(c)=>'&#'+c.charCodeAt(0)+';');" +
            _fn(src, "aiCrops") +
            ";console.log(aiCrops({id:7,ai_crop:{a:{img:'ai1_F7_a.jpg',w:480,h:90,box:[100,200,300,800]},"
            "b:{img:'ai1_F7_b.jpg',w:480,h:90,box:[0,0,1000,1000]}}}));"
            "console.log('|'+aiCrops({id:8})+'|');")
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
    first, second = out.strip().split("\n")
    assert "/api/artwork_v2/jobs/J1/runs/run_002/img/ai1_F7_a.jpg" in first
    assert "ai1_F7_b.jpg" in first and "🅰" in first and "🅱" in first
    assert "left:10%;top:20%;width:20%;height:60%" in first
    assert second == "||"
    tpl = open(os.path.join(ROOT, "templates", "artwork_v2.html"), encoding="utf-8").read()
    for cls in (".v2-aic {", ".v2-aic img", ".v2-aic i"):
        assert cls in tpl
    assert "pixel_kept" in src and "crops_saved" in src and "pixel_folded" in src
