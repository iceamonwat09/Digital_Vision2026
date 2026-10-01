"""
โหมดทดลอง "🤝 เทียบคู่ด้วย Gemini" (``pair_check``) — artwork_check/pairdiff.py

เคสหลักจำลองจากงานจริง 30 ก.ย. 2026 (PDF เทียบภาพถ่าย): OCR อ่านภาพถ่าย
``0g`` เป็น ``Og`` และ ``1g`` เป็น ``19`` ⇒ โหมดเดิม FAIL ทั้งที่งานพิมพ์ตรงกัน

กติกาที่ล็อกไว้:
* ไม่ติ๊ก = ไม่ยิง ไม่แตะผลแม้แต่รายการเดียว (กฎเหล็กข้อ 1)
* เทียบคู่ล้มเหลวแบบไหนก็ตาม ⇒ ได้ผลโหมดเดิมทุกรายการ
* คำตอบว่าง = ล้มเหลว (ลองซ้ำ) ไม่ใช่ "ไม่พบข้อความ" (บทเรียน z2 ของงานเดียวกัน)
* ความต่างที่ Gemini อ้าง ต้องหาเจอในข้อความที่ถอดมาก่อนจะนับ
* defect ของชั้นข้อความที่ Gemini ไม่ยืนยัน ⇒ REVIEW ไม่ใช่ลบทิ้ง
"""

import json
import os
import re

import cv2
import numpy as np
import pytest

from artwork_check import checks, config, pairdiff, progress, report

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = open(os.path.join(ROOT, "static", "js", "artwork_check.js"),
          encoding="utf-8").read()
HTML = open(os.path.join(ROOT, "templates", "artwork_check.html"),
            encoding="utf-8").read()


def _z(zid, group, doc="a", typ="panel"):
    return {"id": zid, "type": typ, "group": group, "doc": doc,
            "bbox": [0.1, 0.1, 0.5, 0.5], "rotate": "default", "label": zid}


# ── เลือกคู่ ──────────────────────────────────────────────────────────

def test_only_groups_with_exactly_one_zone_per_file_are_paired():
    zs = [_z("z1", "A"), _z("b1", "A", "b"),               # ✅
          _z("z2", "B"), _z("z3", "B"), _z("b2", "B", "b"),  # 3 โซน
          _z("z4", "C"), _z("z5", "C"),                    # ไฟล์เดียว
          _z("z6", "D"), _z("b6", "D", "b", typ="zoom"),   # zoom
          _z("z7", "E"), _z("b7", "E", "b", typ="ignore"),  # ignore ⇒ เหลือ 1
          _z("z8", "")]
    got = pairdiff.eligible_pairs(zs)
    assert [(p["group"], p["a"]["id"], p["b"]["id"]) for p in got] == [
        ("A", "z1", "b1")]


def test_paired_zones_are_copies_so_the_pipeline_cannot_race_them():
    zs = [_z("z1", "A"), _z("b1", "A", "b")]
    p = pairdiff.eligible_pairs(zs)[0]
    zs[0]["rotate"] = 90          # pipeline เขียนทับหลังอ่านทีละโซน
    assert p["a"]["rotate"] == "default"


# ── แกะคำตอบ ─────────────────────────────────────────────────────────

GOOD = {"a_text": "Fat 0g", "b_text": "Fat 0g", "differences": [],
        "engine": "gemini-2.5-flash"}


@pytest.mark.parametrize("raw", ["", "   \n  "])
def test_an_empty_answer_is_a_retriable_failure_not_no_text(raw):
    r = pairdiff.parse_response(raw)
    assert r["ok"] is False and r["retry"] is True
    assert "ว่าง" in r["error"]


def test_html_error_page_is_rejected():
    r = pairdiff.parse_response("<!DOCTYPE html><html>Workflow could not "
                                "be started</html>", "text/html")
    assert r["ok"] is False and "HTML" in r["error"]


def test_fenced_and_nested_answers_are_unwrapped():
    fenced = "```json\n" + json.dumps(GOOD) + "\n```"
    assert pairdiff.parse_response(fenced)["ok"]
    nested = json.dumps({"data": json.dumps(GOOD)})
    r = pairdiff.parse_response(nested)
    assert r["ok"] and r["a_text"] == "Fat 0g"
    assert pairdiff.parse_response(json.dumps([GOOD]))["ok"]


def test_both_transcriptions_are_required():
    """ไม่มีข้อความสองฝั่ง = เหลือแต่คำตัดสินของ LLM ที่ตรวจย้อนไม่ได้"""
    r = pairdiff.parse_response(json.dumps(
        {"a_text": "x", "b_text": "", "differences": [{"a": "1", "b": "2"}]}))
    assert r["ok"] is False


def test_workflow_error_with_full_texts_is_kept_as_a_warning():
    r = pairdiff.parse_response(json.dumps(dict(GOOD, error="finishReason=MAX")))
    assert r["ok"] and "MAX" in r["warning"]


def test_differences_are_normalized():
    raw = json.dumps(dict(GOOD, differences=[
        {"a": "1g", "b": "7g", "kind": "NUMBER"},
        {"a": "x", "b": "y", "kind": "weird"},
        {"a": "", "b": "", "where": ""},             # ว่างทั้งหมด ⇒ ทิ้ง
        "not a dict",
        {"a": "", "b": "", "kind": "visual", "where": "logo moved"}]))
    d = pairdiff.parse_response(raw)["differences"]
    assert [x["kind"] for x in d] == ["number", "text", "visual"]


# ── ยิง N8N ──────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, text, ctype="application/json"):
        self.status_code, self.text = status, text
        self.headers = {"Content-Type": ctype}


def test_empty_200_is_retried_then_succeeds(monkeypatch):
    import requests
    seq = [_Resp(200, ""), _Resp(200, json.dumps(GOOD))]
    calls = []

    def post(url, data=None, timeout=None):
        calls.append(sorted(data))
        return seq.pop(0)
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(config, "PAIR_RETRY_WAIT_S", 0)
    r = pairdiff.call_pair(b"a", b"b", url="http://x/webhook/artwork-pair")
    assert r["ok"] and len(calls) == 2
    assert calls[0] == ["image_a_b64", "image_b_b64"]


def test_404_is_not_retried(monkeypatch):
    import requests
    calls = []
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: calls.append(1) or _Resp(404, "nope"))
    r = pairdiff.call_pair(b"a", b"b", url="http://x")
    assert r["ok"] is False and len(calls) == 1 and "404" in r["error"]


def test_call_pair_never_raises(monkeypatch):
    import requests

    def boom(*a, **k):
        raise ValueError("bad url")
    monkeypatch.setattr(requests, "post", boom)
    r = pairdiff.call_pair(b"a", b"b", url="http://x")
    assert r["ok"] is False and "bad url" in r["error"]


# ── ยืนยันความต่างที่ Gemini อ้าง ───────────────────────────────────────

A_TXT = "Total Fat 1.5g\nSodium 280mg\nCONTAINS: TUNA, EGG"
B_TXT = "Total Fat 15g\nSodium 280mg\nCONTAINS: TUNA"


def test_a_decimal_difference_must_be_found_verbatim_on_both_sides():
    """ต่างแค่จุดทศนิยม = คีย์หลัง normalize เท่ากัน ⇒ ต้องเจอตรงตัว"""
    assert pairdiff.verify_diff({"a": "1.5g", "b": "15g", "kind": "number"},
                                A_TXT, B_TXT)
    # อ้างว่า A พิมพ์ "15g" แต่ A จริงคือ "1.5g" ⇒ หลักฐานไม่ตรง
    assert not pairdiff.verify_diff({"a": "1,5g", "b": "15g", "kind": "number"},
                                    A_TXT, B_TXT)


def test_a_missing_word_must_really_be_absent_on_the_other_side():
    assert pairdiff.verify_diff({"a": ", EGG", "b": ""}, A_TXT, B_TXT)
    assert not pairdiff.verify_diff({"a": "TUNA", "b": ""}, A_TXT, B_TXT)


def test_identical_quotes_and_visual_items_are_not_text_differences():
    assert not pairdiff.verify_diff({"a": "Sodium", "b": "Sodium"}, A_TXT, B_TXT)
    assert not pairdiff.verify_diff({"a": "", "b": "", "kind": "visual",
                                     "where": "logo"}, A_TXT, B_TXT)
    assert not pairdiff.verify_diff({"a": "Protein 11g", "b": "Protein 12g"},
                                    A_TXT, B_TXT)      # ไม่มีในที่ถอดมา


# ── รวมผลสองชั้น ─────────────────────────────────────────────────────

def _mm(zid, found, ref, ref_ids=("b1",)):
    return checks._defect("MISMATCH_PANELS", zid, "m", found=found,
                          reference=ref, ref_zone_ids=list(ref_ids))


def _res(diffs, ok=True, a="Fat 1.5g", b="Fat 15g"):
    if not ok:
        return {"ok": False, "group": "A", "a_id": "z1", "b_id": "b1",
                "error": "timeout"}
    return {"ok": True, "group": "A", "a_id": "z1", "b_id": "b1",
            "a_text": a, "b_text": b, "differences": diffs, "engine": "g"}


def test_agreement_keeps_critical():
    pd_ = [_mm("z1", "Saturated Fat 7g", "Saturated Fat 0g")]
    out, info = pairdiff.merge([], pd_, [_res([{"a": "7g", "b": "0g",
                                                "kind": "number"}])])
    assert out[0]["severity"] == "critical" and out[0]["pair_agree"] is True
    assert info["pairs"][0]["agreed"] == 1


def test_text_layer_finding_not_confirmed_by_gemini_becomes_review(monkeypatch):
    pd_ = [_mm("z1", "Trans Fat 0g", "Trans Fat Og")]
    out, info = pairdiff.merge([], pd_, [_res([])], downgrade=True)
    assert len(out) == 1, "ห้ามลบทิ้ง"
    assert out[0]["severity"] == "warning" and out[0]["pair_agree"] is False
    assert "ไม่ได้ระบุว่าต่าง" in out[0]["message"]
    assert report.compute_verdict(out) == "REVIEW"
    out2, _ = pairdiff.merge([], pd_, [_res([])], downgrade=False)
    assert out2[0]["severity"] == "critical"


def test_a_verified_gemini_only_difference_is_added_as_review():
    """เคสจุดทศนิยมที่ชั้นข้อความตาบอด (F1) — Gemini เห็น + ยืนยันได้"""
    out, info = pairdiff.merge([], [], [_res([{"a": "1.5g", "b": "15g",
                                               "kind": "number",
                                               "where": "Total Fat"}])])
    assert len(out) == 1
    d = out[0]
    assert (d["class"], d["zone_id"], d["found"], d["reference"]) == (
        "MISMATCH_PANELS", "z1", "1.5g", "15g")
    assert d["severity"] == "warning" and d["source"] == "pair_llm"
    assert d["ref_zone_ids"] == ["b1"]


def test_unverifiable_and_visual_claims_are_reported_not_counted():
    out, info = pairdiff.merge([], [], [_res([
        {"a": "Protein 11g", "b": "Protein 12g", "kind": "number"},
        {"a": "", "b": "", "kind": "visual", "where": "logo shifted"}])])
    assert out == []
    p = info["pairs"][0]
    assert len(p["unverified"]) == 1 and len(p["visual"]) == 1


def test_failed_pair_keeps_every_baseline_defect():
    base = [_mm("z1", "Fat 0g", "Fat Og"),
            checks._defect("UNREADABLE", "z9", "x")]
    out, info = pairdiff.merge(base, [], [_res([], ok=False)])
    assert out == base
    assert info["used"] == 0 and info["pairs"][0]["status"] == "failed"


def test_zones_outside_the_pair_are_untouched_and_other_classes_kept():
    base = [_mm("z1", "a", "b"), _mm("z5", "x", "y", ("z6",))]
    num = checks._defect("NUMBER_FAIL", "z1", "n", found="5 OZ")
    out, _ = pairdiff.merge(base, [num], [_res([], a="x", b="x")])
    assert base[1] in out                  # กลุ่มอื่น = ของเดิม
    assert num in out                      # คลาสอื่นของคู่ = คงไว้
    assert base[0] not in out              # ใช้ผลบนข้อความที่ถอดคู่แทน


def test_apply_texts_swaps_only_successful_pairs():
    ocr = [{"zone_id": "z1", "text": "Fat 0g", "engine": "gemini"},
           {"zone_id": "b1", "text": "", "engine": "n8n", "error": "empty"},
           {"zone_id": "z2", "text": "keep", "engine": "gemini"}]
    out = pairdiff.apply_texts(ocr, [_res([], a="Fat 0g", b="Fat 0g")])
    assert out[1]["text"] == "Fat 0g" and "error" not in out[1]
    assert out[1]["indiv_text"] == "" and out[1]["engine"] == "pair:g"
    assert out[2] is ocr[2]
    assert ocr[1]["error"] == "empty"      # ของเดิมไม่ถูกแก้


# ── pipeline จริง (ไฟล์จริง · OCR/Gemini จำลอง) ─────────────────────────

INDIV = {"z1": "Saturated Fat 0g\nDietary Fiber 1g\nSodium 280mg",
         "b1": "Saturated Fat Og\nDietary Fiber 19\nSodium 280mg"}
JOINT = {"z1": "Saturated Fat 0g\nDietary Fiber 1g\nSodium 280mg",
         "b1": "Saturated Fat 0g\nDietary Fiber 1g\nSodium 280mg"}


def _run(tmp_path, monkeypatch, caller=None, **kw):
    import fitz
    from artwork_check import pipeline
    monkeypatch.setattr(config, "INSPECTIONS_DIR", str(tmp_path))
    monkeypatch.setattr(config, "PAIR_PREWARM_HL", False)
    rec = "20260930-000000-a1b2c3"
    d = report.inspection_dir(rec, create=True)
    doc = fitz.open()
    pg = doc.new_page(width=300, height=300)
    pg.insert_text((40, 80), "Saturated Fat 0g", fontsize=14)
    doc.save(os.path.join(d, "source.pdf"))
    img = np.full((300, 300, 3), 255, np.uint8)
    cv2.putText(img, "Fat 0g", (40, 90), cv2.FONT_HERSHEY_SIMPLEX, 1, 0, 2)
    cv2.imwrite(os.path.join(d, "source_b.png"), img)
    cv2.imwrite(os.path.join(d, "preview.png"), img)
    cv2.imwrite(os.path.join(d, "preview_b.png"), img)
    monkeypatch.setattr(
        pipeline.ocr, "read_all_zones",
        lambda doc, zones, page_auto=False, force_ocr=False,
               split_bands=False, font_trust=None: [
            {"zone_id": z["id"], "text": INDIV[z["id"]], "engine": "gemini",
             "conf": None, "rotate": 0}
            for z in zones if z.get("type") != "ignore"])
    calls = []

    def fake(img_a, img_b):
        calls.append((len(img_a), len(img_b)))
        if caller:
            return caller(img_a, img_b)
        return {"ok": True, "a_text": JOINT["z1"], "b_text": JOINT["b1"],
                "differences": [], "engine": "gemini-2.5-flash"}
    monkeypatch.setattr(pairdiff, "call_pair", fake)
    zs = [_z("z1", "A"), _z("b1", "A", "b")]
    rep = pipeline.run_inspection(rec, zs, **kw)
    return rep, calls


def test_unchecked_means_no_call_and_the_old_result(tmp_path, monkeypatch):
    rep, calls = _run(tmp_path, monkeypatch)
    assert calls == []
    assert rep["pair_check"] is False and rep["pair"] is None
    assert rep["verdict"] == "FAIL"
    assert all(o["engine"] == "gemini" for o in rep["ocr"])


def test_the_30_sep_false_positives_disappear(tmp_path, monkeypatch):
    rep, calls = _run(tmp_path, monkeypatch, pair_check=True)
    assert len(calls) == 1 and all(n > 100 for n in calls[0]), "ต้องส่งภาพจริงสองภาพ"
    assert rep["verdict"] == "PASS" and rep["defects"] == []
    assert rep["pair"]["baseline_count"] >= 2
    assert rep["pair"]["final_count"] == 0 and rep["pair"]["used"] == 1
    z1 = next(o for o in rep["ocr"] if o["zone_id"] == "b1")
    assert z1["engine"].startswith("pair:")
    assert z1["indiv_text"] == INDIV["b1"]


@pytest.mark.parametrize("caller", [
    lambda a, b: {"ok": False, "error": "N8N ตอบกลับว่าง (0 bytes)"},
    lambda a, b: (_ for _ in ()).throw(RuntimeError("boom")),
])
def test_any_pair_failure_falls_back_to_the_old_result(tmp_path, monkeypatch,
                                                       caller):
    base, _ = _run(tmp_path, monkeypatch)
    rep, _ = _run(tmp_path, monkeypatch, caller=caller, pair_check=True)
    strip = lambda ds: [(d["class"], d["zone_id"], d["found"], d["severity"])
                        for d in ds]
    assert strip(rep["defects"]) == strip(base["defects"])
    assert rep["pair"]["used"] == 0
    assert rep["pair"]["pairs"][0]["status"] == "failed"
    assert [o["text"] for o in rep["ocr"]] == [o["text"] for o in base["ocr"]]


# ── 🔠 ชั้นกันพลาดตัวพิมพ์ (AvoDerm 30 ก.ย. — ยืนยันด้วยตาว่า 🅱 พิมพ์ c เล็ก) ──

TRUE_A = "D-Calcium Pantothenate, Thiamine Mononitrate"
TRUE_B = "D-calcium Pantothenate, Thiamine Mononitrate"


def _case_run(tmp_path, monkeypatch, indiv_a, joint_b=TRUE_A, diffs=(),
              **kw):
    """Gemini คู่ถอด 🅱 ตาม 🅰 (``joint_b``) · การอ่านแยกของ 🅱 เห็นของจริง."""
    monkeypatch.setitem(globals(), "INDIV", {"z1": indiv_a, "b1": TRUE_B})
    monkeypatch.setitem(globals(), "JOINT", {"z1": TRUE_A, "b1": joint_b})
    caller = (lambda a, b: {"ok": True, "a_text": TRUE_A, "b_text": joint_b,
                            "differences": list(diffs),
                            "engine": "gemini-2.5-flash"})
    return _run(tmp_path, monkeypatch, caller=caller, **kw)[0]


def _cases(rep):
    return [d for d in rep["defects"] if d["class"] == "MISMATCH_CASE"]


def test_case_difference_swallowed_by_the_pair_read_is_restored(tmp_path,
                                                                  monkeypatch):
    rep = _case_run(tmp_path, monkeypatch, indiv_a=TRUE_A, pair_check=True)
    got = _cases(rep)
    assert len(got) == 1, rep["defects"]
    assert got[0]["source"] == "indiv_case" and got[0]["pair_agree"] is False
    assert got[0]["severity"] == config.TEXT_CASE_SEVERITY
    assert rep["verdict"] == "FAIL"
    assert rep["pair"]["case_kept"] == 1
    assert rep["pair"]["pairs"][0]["case_kept"] == 1
    assert rep["pair"]["final_count"] == len(rep["defects"])


def test_caught_even_when_the_main_file_zone_timed_out(tmp_path, monkeypatch):
    """เคสจริง: z1 อ่านแยกหมดเวลา (ว่าง) ⇒ ใช้ข้อความคู่ของ z1 เทียบกับ
    การอ่านแยกของ 🅱 ซึ่งไม่เห็นภาพ 🅰 จึงลอกไม่ได้."""
    rep = _case_run(tmp_path, monkeypatch, indiv_a="", pair_check=True)
    assert len(_cases(rep)) == 1, rep["defects"]
    assert rep["verdict"] == "FAIL"


def test_flag_off_keeps_the_previous_pair_result(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PAIR_CASE_GUARD", False)
    rep = _case_run(tmp_path, monkeypatch, indiv_a=TRUE_A, pair_check=True)
    assert _cases(rep) == [] and rep["verdict"] == "PASS"
    assert "case_kept" not in rep["pair"]


def test_no_duplicate_when_the_pair_read_already_saw_it(tmp_path, monkeypatch):
    rep = _case_run(tmp_path, monkeypatch, indiv_a=TRUE_A, joint_b=TRUE_B,
                    diffs=[{"a": "D-Calcium", "b": "D-calcium",
                            "kind": "text", "where": ""}], pair_check=True)
    got = _cases(rep)
    assert len(got) == 1 and got[0].get("source") != "indiv_case"
    assert rep["pair"]["case_kept"] == 0


def test_guard_only_adds_case_items_of_paired_zones():
    res = [{"ok": True, "group": "A", "a_id": "z1", "b_id": "b1"}]
    base = [{"class": "MISMATCH_PANELS", "zone_id": "z1", "found": "x",
             "reference": "y"}]
    indep = [
        {"class": "MISMATCH_PANELS", "zone_id": "z1", "found": "0g",
         "reference": "Og"},                                   # คลาสอื่น
        {"class": "MISMATCH_CASE", "zone_id": "z9", "found": "Ab",
         "reference": "ab"},                                   # นอกคู่
        {"class": "MISMATCH_CASE", "zone_id": "z1", "found": "Ab",
         "reference": "ab"},                                   # ✅
    ]
    info = {"pairs": [{"group": "A", "a_id": "z1", "b_id": "b1",
                       "status": "ok"}]}
    out, inf = pairdiff.case_guard(base, indep, res, info)
    assert out[0] is base[0] and len(out) == 2
    assert out[1]["zone_id"] == "z1" and out[1]["class"] == "MISMATCH_CASE"
    assert inf["case_kept"] == 1 and inf["final_count"] == 2


def test_independent_texts_skip_failed_individual_reads():
    rows = [{"zone_id": "z1", "text": "pair", "engine": "pair:g",
             "indiv_text": "", "indiv_engine": "n8n",
             "indiv_error": "timeout"},
            {"zone_id": "b1", "text": "pair", "engine": "pair:g",
             "indiv_text": "solo", "indiv_engine": "gemini",
             "indiv_error": ""},
            {"zone_id": "z2", "text": "plain", "engine": "gemini"}]
    got = {r["zone_id"]: r["text"] for r in pairdiff.independent_texts(rows)}
    assert got == {"z1": "pair", "b1": "solo", "z2": "plain"}


def test_js_shows_the_case_guard_count():
    assert "p.case_kept" in JS


def test_progress_step_explains_why_it_did_not_run():
    run = progress.begin("pair-x")
    keys = [s["key"] for s in progress.snapshot("pair-x")["steps"]]
    assert keys.index("confirm") < keys.index("pair") < keys.index("pixel")
    run.skip("pair", "ไม่ได้ติ๊กช่อง")
    s = next(x for x in progress.snapshot("pair-x")["steps"] if x["key"] == "pair")
    assert s["status"] == "skip" and s["detail"]


# ── อุ่นแคช Tesseract ─────────────────────────────────────────────────

def test_prewarm_reads_every_zone_with_every_psm(monkeypatch):
    from artwork_check import highlight as hl
    seen = []
    monkeypatch.setattr(hl, "_tesseract_available", lambda: True)
    monkeypatch.setattr(hl, "_resolve_langs", lambda req, word="": req)
    monkeypatch.setattr(hl, "_tess_words",
                        lambda crop, lang, psm: seen.append((lang, psm)) or [])
    img = np.zeros((20, 20, 3), np.uint8)

    def render(z):
        if z["id"] == "bad":
            raise ValueError("x")
        return img
    n = pairdiff.prewarm_highlight([_z("z1", "A"), {"id": "bad"},
                                    _z("b1", "A", "b")], render, "auto")
    assert n == 2
    assert seen == [("eng", p) for p in hl._PSM_ORDER] * 2


def test_prewarm_is_a_noop_without_tesseract(monkeypatch):
    from artwork_check import highlight as hl
    monkeypatch.setattr(hl, "_tesseract_available", lambda: False)
    assert pairdiff.prewarm_highlight([_z("z1", "A")], lambda z: 1 / 0) == 0


def test_prewarm_uses_the_exact_pixels_the_defect_card_will_use(tmp_path,
                                                                 monkeypatch):
    """แคชใช้ hash ของพิกเซลเป็นกุญแจ — ภาพที่อุ่นต้องตรงกับที่ ``/crop``
    ส่งให้ Tesseract ทุกพิกเซล ไม่งั้นอุ่นเปล่า"""
    import time
    from artwork_check import highlight as hl
    from artwork_check import pipeline
    rep, _ = _run(tmp_path, monkeypatch)
    keys = {"warm": set(), "card": set()}
    phase = {"k": "warm"}
    monkeypatch.setattr(hl, "_tesseract_available", lambda: True)
    monkeypatch.setattr(hl, "_resolve_langs", lambda req, word="": "eng")
    monkeypatch.setattr(hl, "_tess_words", lambda crop, lang, psm: keys[
        phase["k"]].add(hl._crop_key(crop)) or [])
    # ⚠️ ต้องเป็นโซนของไฟล์ **PDF** — ไฟล์ภาพไม่ขึ้นกับ dpi เลย ถ้าใช้ฝั่ง
    #    ภาพ เทสต์จะผ่านแม้อุ่นด้วยความละเอียดผิด (mutation test จับได้)
    #    และกว้างพอให้ภาพที่ 450 dpi อยู่ระหว่าง 1200-1600 px (เล็กกว่านั้น
    #    ชั้นเพิ่ม DPI ดันทุกความละเอียดไปที่ 1200 px เท่ากัน = เทสต์ตาบอด)
    zb = {"id": "z1", "bbox": [0.1, 0.1, 0.7, 0.5], "doc": "a",
          "rotate": "default"}
    pipeline._start_prewarm(rep["id"], [zb])
    for _ in range(200):
        if keys["warm"]:
            break
        time.sleep(0.01)
    time.sleep(0.05)
    phase["k"] = "card"
    pipeline.zone_crop_jpg(rep["id"], zb["bbox"], doc="a", rotate="0",
                           highlight="Fat", zone_id="z1")
    assert keys["warm"], "ไม่ได้อุ่นเลย"
    assert keys["card"], "การ์ดไม่ได้เรียก Tesseract — เทสต์ไม่ได้ทดสอบอะไร"
    assert keys["card"] <= keys["warm"]


# ── หน้าเว็บ / workflow ──────────────────────────────────────────────

def test_checkbox_exists_and_hides_behind_the_flag():
    assert 'id="awPairCheck"' in HTML
    i = HTML.index('id="awPairCheck"')
    row = HTML[HTML.rindex('<div class="aw-row"', 0, i):i + 200]
    assert "{% if not pair_ui %}display:none;{% endif %}" in row
    assert '{% if not pair_ui %} data-off="1"' in row


def test_js_sends_saves_and_restores_the_flag():
    assert "pair_check: pairCheckOn()" in JS
    assert 'return expChecked("awPairCheck")' in JS
    assert "pairCheck: pairCheckOn()" in JS                  # autosave
    assert "restorePairCheck(s.pairCheck);" in JS
    assert ": !!st.pair_check" in JS                          # clone
    assert re.search(r'"awPixelCheck", "awPairCheck"\]\.forEach', JS)
    assert "html += pairHtml(rep.pair);" in JS


def test_route_and_setup_carry_the_flag():
    src = open(os.path.join(ROOT, "artwork_check", "routes.py"),
               encoding="utf-8").read()
    assert 'pair_check = bool(body.get("pair_check"))' in src
    assert "pair_check=pair_check" in src
    assert "pair_ui=config.PAIR_COMPARE_UI" in src
    assert "pair_check" in report._SETUP_KEYS


# ── ค่าเริ่มต้นเปิด (ผู้ใช้สั่ง 1 ต.ค. 2026) ─────────────────────────────

def _page(monkeypatch, **flags):
    flask = pytest.importorskip("flask")
    from artwork_check.routes import artwork_bp
    for k, v in flags.items():
        monkeypatch.setattr(config, k, v)
    app = flask.Flask(__name__,
                      template_folder=os.path.join(ROOT, "templates"),
                      static_folder=os.path.join(ROOT, "static"))

    @app.context_processor
    def _ctx():
        return {"config_version": "test", "current_user": None,
                "auth_enabled": False, "has_perm": lambda *a, **k: True}

    app.register_blueprint(artwork_bp)
    with app.test_client() as c:
        r = c.get("/artwork_check")
        assert r.status_code == 200
        return r.get_data(as_text=True)


def _pair_input(html):
    m = re.search(r'<input type="checkbox" id="awPairCheck"[^>]*>', html)
    assert m, "ไม่พบช่องติ๊ก"
    return m.group(0)


def test_checkbox_is_ticked_by_default(monkeypatch):
    assert config.PAIR_DEFAULT_ON is True
    tag = _pair_input(_page(monkeypatch))
    assert re.search(r"\bchecked\b", tag)
    assert "data-off" not in tag


def test_flag_off_gives_the_old_unticked_box(monkeypatch):
    tag = _pair_input(_page(monkeypatch, PAIR_DEFAULT_ON=False))
    assert not re.search(r"\bchecked\b", tag)


def test_hidden_ui_is_never_ticked(monkeypatch):
    # ซ่อนช่อง = นับว่าไม่ติ๊กเสมอ แม้ค่าเริ่มต้นจะเปิด
    tag = _pair_input(_page(monkeypatch, PAIR_COMPARE_UI=False,
                            PAIR_DEFAULT_ON=True))
    assert "data-off" in tag
    assert not re.search(r"\bchecked\b", tag)


def test_api_without_the_key_stays_off():
    # ค่าเริ่มต้นเป็นเรื่องของหน้าเว็บเท่านั้น — สคริปต์ที่ไม่ส่งคีย์ต้องได้ทางเดิม
    src = open(os.path.join(ROOT, "artwork_check", "routes.py"),
               encoding="utf-8").read()
    assert 'pair_check = bool(body.get("pair_check"))' in src


def _run_restore(default_checked, value, off=False):
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("ไม่มี node")
    m = re.search(r"function restorePairCheck\(v\) \{.*?\n  \}", JS, re.S)
    assert m, "ไม่พบ restorePairCheck"
    prog = ("const el={checked:%s,defaultChecked:%s,dataset:%s};"
            "const $=(id)=>id==='awPairCheck'?el:null;%s;"
            "restorePairCheck(%s);console.log(JSON.stringify(el.checked));"
            % (json.dumps(not default_checked), json.dumps(default_checked),
               '{off:"1"}' if off else "{}", m.group(0),
               "undefined" if value is None else json.dumps(value)))
    out = subprocess.run(["node", "-e", prog], capture_output=True,
                         text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.parametrize("default_checked,value,want", [
    (True, None, True),     # autosave เก่า/ต้นแบบที่ไม่บอกค่า ⇒ ค่าเริ่มต้น
    (False, None, False),
    (True, False, False),   # ผู้ใช้เอาติ๊กออกเองแล้วรีเฟรช ⇒ เคารพค่านั้น
    (False, True, True),
])
def test_restore_uses_the_default_only_when_no_value(default_checked, value,
                                                     want):
    assert _run_restore(default_checked, value) is want


def test_restore_never_ticks_a_hidden_box():
    assert _run_restore(True, None, off=True) is False
    assert _run_restore(True, True, off=True) is False


def test_clone_does_not_carry_old_unticked_jobs_when_default_is_on():
    i = JS.index("function loadFromClone(res)")
    body = JS[i:i + 1500]
    assert re.search(r'\.defaultChecked \? undefined\s*: !!st\.pair_check',
                     body)


WF = os.path.join(ROOT, "artwork_check", "n8n_artwork_pair.workflow.json")


def test_workflow_contract():
    w = json.load(open(WF, encoding="utf-8"))
    nodes = {n["name"]: n for n in w["nodes"]}
    assert nodes["Webhook"]["parameters"]["path"] == "artwork-pair"
    assert config.PAIR_WEBHOOK_URL.endswith("/webhook/artwork-pair")
    build = nodes[BUILD]["parameters"]["jsCode"]
    for key in ("image_a_b64", "image_b_b64", "temperature: 0",
                "DO NOT correct spelling, grammar, capitalization"):
        assert key in build, key
    body = nodes["Respond to Webhook"]["parameters"]["responseBody"]
    for key in ("a_text", "b_text", "differences"):
        assert key in body
    ids = [n["id"] for n in w["nodes"]]
    assert len(ids) == len(set(ids))
    for src, conn in w["connections"].items():
        assert src in nodes
        for br in conn["main"]:
            for c in br:
                assert c["node"] in nodes, c["node"]


# ชื่อ node เดิมของ workflow ที่สถานีใช้อยู่ — ผู้ใช้วางโค้ดทับ node เดิม
# ⇒ ห้ามเปลี่ยนชื่อ (ไม่งั้นคำแนะนำ "วางทับ node ชื่อ …" ใช้ไม่ได้)
BUILD, PARSE = "Code in JavaScript2", "Code in JavaScript"

# รัน Code node ของ workflow **ตัวจริง** แบบ n8n จำลองด้วย node
HARNESS = r"""
// stdin = {body, gemini}: gemini = คำตอบ JSON ที่โมเดลเขียน (object) หรือ {error}
const fs = require('fs');
const w = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const cfg = JSON.parse(fs.readFileSync(0, 'utf8'));
const node = (n) => w.nodes.find((x) => x.name === n).parameters.jsCode;
function run(name, items) {
  const $input = { first: () => items[0], all: () => items };
  return new Function('$input', node(name))($input);
}
const built = run(process.argv[3], [{ json: { body: cfg.body } }])[0].json;
const log = { valid: built.valid, error: built.error };
if (!built.valid) { console.log(JSON.stringify({ log })); process.exit(0); }
const req = built.gemini_request;
log.images = req.contents[0].parts.filter((p) => p.inlineData).length;
log.gen = req.generationConfig;
log.prompt = req.contents[0].parts[0].text;
let resp;
try {
  const g = cfg.gemini;
  resp = g && g.error ? { error: g.error }
    : { candidates: [{ content: { parts: [{ text: JSON.stringify(g) }] }, finishReason: 'STOP' }] };
  const out = run(process.argv[4], [{ json: resp }])[0].json;
  console.log(JSON.stringify({ log, out }));
} catch (e) { console.log(JSON.stringify({ log, thrown: String(e.message) })); }
"""


def _wf(cfg):
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("ไม่มี node")
    r = subprocess.run(["node", "-e", HARNESS, "x", WF, BUILD, PARSE],
                       input=json.dumps(cfg), capture_output=True, text=True,
                       timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _b64(ext, v):
    import base64
    ok, buf = cv2.imencode(ext, np.full((20, 20, 3), v, np.uint8))
    return base64.b64encode(buf.tobytes()).decode()


BODY = {"image_a_b64": _b64(".jpg", 255), "image_b_b64": _b64(".png", 0)}


def test_workflow_is_one_request_with_both_images():
    """ผู้ใช้เลือก: เทียบคู่ = 1 คำขอต่อ 1 คู่โซน (ไม่แยกถอดความ)."""
    got = _wf({"body": BODY, "gemini": {"a_lines": ["x"], "b_lines": ["x"],
                                        "differences": []}})
    assert got["log"]["images"] == 2
    assert got["log"]["gen"]["temperature"] == 0


def test_workflow_lines_become_multiline_text():
    """schema เป็นรายการบรรทัด ⇒ ได้ข้อความหลายบรรทัดคืน (v1 ได้ทั้งแผงเป็น
    ก้อนเดียว คำติดกัน ``INSTRUCTIONSAmount``) · ช่องว่างหัว-ท้าย/บรรทัดว่างถูกตัด."""
    got = _wf({"body": BODY, "gemini": {
        "a_lines": ["FEEDING INSTRUCTIONS", " Amount per day ", ""],
        "b_lines": ["FEEDING INSTRUCTIONS", "Amount per day"],
        "differences": []}})
    out = got["out"]
    assert out["a_text"] == "FEEDING INSTRUCTIONS\nAmount per day"
    assert out["b_text"] == out["a_text"] and not out.get("error")


def test_workflow_schema_asks_for_lines_and_enough_thinking():
    got = _wf({"body": BODY, "gemini": {"a_lines": ["x"], "b_lines": ["x"],
                                        "differences": []}})
    gen = got["log"]["gen"]
    props = gen["responseSchema"]["properties"]
    assert props["a_lines"]["type"] == "ARRAY" and props["b_lines"]["type"] == "ARRAY"
    assert gen["responseSchema"]["propertyOrdering"][:2] == ["a_lines", "b_lines"]
    # 1024 ถูกใช้หมดบนงานจริง (thoughtsTokenCount 1023)
    assert gen["thinkingConfig"]["thinkingBudget"] > 1024


def test_workflow_prompt_targets_the_misses_seen_on_the_station():
    """ความต่างที่พลาดจริงบนสถานีต้องอยู่ใน prompt: ตัวพิมพ์ · ลงท้าย s · ห้ามลอก."""
    p = _wf({"body": BODY, "gemini": {"a_lines": [], "b_lines": [],
                                      "differences": []}})["log"]["prompt"]
    for key in ("D-calcium", "Breed", "PIXELS OF IMAGE B ONLY",
                "NEVER copy a line from a_lines", "INCLUDING LETTER CASE",
                "Never glue two words together", "[?]"):
        assert key in p, key


def test_workflow_still_accepts_the_old_string_schema():
    got = _wf({"body": BODY, "gemini": {"a_text": "A\nB", "b_text": "A\nC",
                                        "differences": []}})
    assert (got["out"]["a_text"], got["out"]["b_text"]) == ("A\nB", "A\nC")


def test_workflow_empty_side_is_an_error_not_a_pass():
    got = _wf({"body": BODY, "gemini": {"a_lines": ["x"], "b_lines": [],
                                        "differences": []}})
    assert got["out"]["error"]


def test_workflow_gemini_error_is_not_swallowed():
    got = _wf({"body": BODY, "gemini": {"error": {"code": 403}}})
    assert "Gemini API error" in got["thrown"]


def test_workflow_rejects_a_missing_image():
    got = _wf({"body": {"image_a_b64": BODY["image_a_b64"]}})
    assert got["log"]["valid"] is False
