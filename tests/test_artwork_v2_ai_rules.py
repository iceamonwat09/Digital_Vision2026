"""ชั้นตรวจคำตอบของ AI (6 ต.ค. 2026) — ไม่ยิงเน็ตจริง

ที่มา: ผลจริงบนสถานี (AvoDerm Master-1 ↔ Master-2) โหมด judge พลาด ``Sulphate``/``Park``
เพราะ Gemini **นับรหัสคำคลาด 1 ตำแหน่ง** ในบรรทัดยาว ทั้งที่ยกข้อความมาถูก · และคำตอบที่
"อ้างถูกแต่เป็นจุดไข่ปลา/ช่องว่าง" ถูกนับเป็นการอ้างผิด (ความถูกต้องของการอ้างอิง 55-63%)

สิ่งที่ล็อกไว้:
* กู้รหัสคำจาก **ข้อความที่ยกมา** ได้เฉพาะในบรรทัดที่อ้าง · ตรงทั้งคำ · ใกล้ · ตำแหน่งเดียว (ไม่เดา)
* สองฝั่งเท่ากันตามกติกาเทียบของระบบ ⇒ สัญญาณรบกวน (ไม่ใช่การอ้างผิด · ไม่เป็นจุด)
* judge: AI พับตัวอักษร/ตัวเลขที่ Vision อ่านชัดว่า "noise" ไม่ได้ · จุดแดงของอัลกอริทึมที่ AI
  ไม่ระบุไม่หายเงียบ · ข้อความโค้งแดงไม่ได้จากคำตอบของ AI
* ทุกธง = 0 ⇒ ผลเท่าเดิมเป๊ะ (เล่นซ้ำคำตอบจริงจาก Log สถานีได้ผลเท่าที่สถานีเห็น)
"""

from __future__ import annotations

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from artwork_v2_ai_replay_items import ASSIST_RUN005_ITEMS, JUDGE_RUN004  # noqa: E402
from artwork_v2_fake import fta, fta_from_lines, load_log  # noqa: E402

from artwork_v2 import ai_review, compare, config, pipeline, textmodel  # noqa: E402

REPLAY = os.path.join(os.path.dirname(__file__), "data", "artwork_v2", "ai_replay")
RULES = ("AI_QUOTE_RECOVER", "AI_EQUIV_NOISE", "AI_SEND_CURVED", "AI_JUDGE_KEEP_ALGO_RED",
         "AI_JUDGE_NOISE_GUARD", "AI_JUDGE_CURVED_YELLOW")


@pytest.fixture(autouse=True)
def _new_rules(monkeypatch):
    """ค่าเริ่มต้นของเครื่อง = เปิดทุกชั้น · อ่านซ้ำปิด (ค่าเริ่มต้นของสถานี)"""
    for k in RULES:
        monkeypatch.setattr(config, k, True)
    monkeypatch.setattr(config, "AI_QUOTE_RECOVER_MAX_SHIFT", 2)
    monkeypatch.setattr(config, "REREAD_ENABLED", False)
    yield


def _off(monkeypatch, *names):
    for k in names or RULES:
        monkeypatch.setattr(config, k, False)


# ── ตัวช่วย ──────────────────────────────────────────────────────────

def _lines(texts, confs=None):
    rows = []
    for i, t in enumerate(texts):
        kw = {"cw": 10, "h": 20}
        if confs and confs.get(i) is not None:
            kw["conf"] = confs[i]
        rows.append((t, 20, 40 + i * 40, kw))
    return textmodel.parse(fta(rows, 1000, 1000), 1000, 1000)["lines"]


def _pr(ta, tb, ca=None, cb=None, curved=None):
    r = compare.compare(_lines(ta, ca), _lines(tb, cb), (1000, 1000), (1000, 1000))
    for i, f in enumerate(r["findings"], 1):
        f["id"] = i
    pr = {"n": 1, "findings": r["findings"], "curved_lines": curved or {"A": [], "B": []}}
    return pr, r["lines_a"], r["lines_b"]


def _it(aw, aq, bw, bq, verdict="real"):
    return {"a_words": aw, "a_quote": aq, "b_words": bw, "b_quote": bq, "kind": "text",
            "verdict": verdict, "reason": "r", "suggestion": "s"}


SUL_A = "Potassium Iodide), Choline Chloride, Magnesium Sulfate, Copper Sulphate Pentahydrate."
SUL_B = "Potassium Iodide), Choline Chloride, Magnesium Sulfate, Copper Sulfate."
ADR_A = "16321 Arrow Hwy, Irwindale Park, CA 91706"
ADR_B = "16321 Arrow Hwy Irwindale, CA 91706 USA"


# ── ① กู้รหัสคำจากข้อความที่ยกมา ───────────────────────────────────────

def test_off_by_one_ids_with_a_correct_quote_are_recovered():
    """เคสจริง: "Copper Sulphate" = A:6,7 แต่ AI อ้าง A:7,8"""
    _, A, B = _pr([SUL_A], [SUL_B])
    f, why, kind = ai_review.check_item(
        _it(["A0:7", "A0:8"], "Copper Sulphate", ["B0:6", "B0:7"], "Copper Sulfate"), A, B)
    assert kind == "ok" and why == ""
    assert (f["a"]["frag"], f["b"]["frag"]) == ("ph", "f") and f["class"] == "TEXT"
    assert f["ai"]["recovered"]["a"] == "shift"
    assert f["a"]["box"] is not None and f["b"]["box"] is not None


def test_id_past_the_end_of_the_line_is_recovered_from_the_quote():
    """เคสจริง: AI อ้าง A:8,9 (ไม่มีคำที่ 9) แต่ยก "Sulphate Pentahydrate." มาถูก"""
    _, A, B = _pr([SUL_A], [SUL_B])
    f, why, kind = ai_review.check_item(
        _it(["A0:8", "A0:9"], "Sulphate Pentahydrate.", ["B0:7"], "Sulfate."), A, B)
    assert kind == "ok" and "Pentahydr" in f["a"]["frag"]


def test_missing_word_with_shifted_ids_is_recovered():
    """เคสจริง: "Irwindale Park," = A:3,4 แต่ AI อ้าง A:4,5"""
    _, A, B = _pr([ADR_A], [ADR_B])
    f, _, kind = ai_review.check_item(
        _it(["A0:4", "A0:5"], "Irwindale Park,", ["B0:3"], "Irwindale,"), A, B)
    assert kind == "ok" and "Park" in f["a"]["frag"]


def test_quote_inside_the_cited_word_is_narrowed_to_it():
    """AI ยกแค่ส่วนของคำ (เช่นจุดไข่ปลาของ ``(min)......``) — เจอครั้งเดียว ⇒ ใช้ได้"""
    _, A, B = _pr(["Net 185 g"], ["Net 170 g"])
    f, _, kind = ai_review.check_item(_it(["A0:1"], "85", ["B0:1"], "70"), A, B)
    assert kind == "ok" and f["ai"]["recovered"] == {"a": "substr", "b": "substr"}
    assert (f["a"]["frag"], f["b"]["frag"]) == ("85", "70")


@pytest.mark.parametrize("ta,item", [
    # คำเดียวกันอยู่สองข้างที่ห่างเท่ากัน ⇒ กำกวม
    (["Fat x Fat"], _it(["A0:1"], "Fat", ["B0:0"], "Fat")),
    # อยู่ไกลเกินเพดาน (5 คำ)
    (["Fat a b c d Oil"], _it(["A0:0"], "Oil", ["B0:0"], "Fat")),
    # ไม่มีในบรรทัดที่อ้าง (มีในบรรทัดอื่น — ห้ามข้ามบรรทัด)
    (["Fat 20%", "Oil 5%"], _it(["A0:0"], "Oil", ["B0:0"], "Fat")),
    # อ้างรหัสข้ามบรรทัด — ห้ามเลือกบรรทัดให้เอง
    (["Fat 20%", "Oil 5%"], _it(["A0:0", "A1:0"], "Oil", ["B0:0"], "Fat")),
    # ยกข้อความที่ไม่มีอยู่จริง
    (["Fat 20%"], _it(["A0:0"], "Fit", ["B0:0"], "Fat")),
    # ส่วนของคำที่เจอหลายที่
    (["Fat 2020"], _it(["A0:1"], "20", ["B0:0"], "Fat")),
])
def test_recovery_never_guesses(ta, item):
    _, A, B = _pr(ta, ["Fat 20%", "Oil 5%"])
    f, why, kind = ai_review.check_item(item, A, B)
    assert f is None and kind == "invalid" and why


def test_recovery_flag_off_is_the_old_rejection(monkeypatch):
    _off(monkeypatch, "AI_QUOTE_RECOVER")
    _, A, B = _pr([SUL_A], [SUL_B])
    f, why, _ = ai_review.check_item(
        _it(["A0:7", "A0:8"], "Copper Sulphate", ["B0:6", "B0:7"], "Copper Sulfate"), A, B)
    assert f is None and "ไม่ตรงกับ Vision" in why
    f, why, _ = ai_review.check_item(
        _it(["A0:8", "A0:9"], "Sulphate Pentahydrate.", ["B0:7"], "Sulfate."), A, B)
    assert f is None and "ไม่มีคำ" in why


def test_recovery_shift_limit_is_respected(monkeypatch):
    monkeypatch.setattr(config, "AI_QUOTE_RECOVER_MAX_SHIFT", 0)
    _, A, B = _pr([ADR_A], [ADR_B])
    f, _, kind = ai_review.check_item(
        _it(["A0:4", "A0:5"], "Irwindale Park,", ["B0:3"], "Irwindale,"), A, B)
    assert f is None and kind == "invalid"


# ── ② เท่ากันตามกติกาเทียบ = สัญญาณรบกวน ──────────────────────────────

@pytest.mark.parametrize("ta,tb,aw,aq,bw,bq", [
    ("Omega-6 Fatty Acid* (min)", "Omega-6 Fatty Acid * (min)",
     ["A0:2"], "Acid*", ["B0:2", "B0:3"], "Acid *"),                         # ช่องว่าง
    ("Crude Protein (min)...... ..7.0%", "Crude Protein (min).. ..7.0%",
     ["A0:2", "A0:3"], "(min)...... ..7.0%", ["B0:2", "B0:3"], "(min).. ..7.0%"),   # จุดไข่ปลา
    ("AvoDermⓇ Dog", "AvoDerm® Dog", ["A0:0"], "AvoDermⓇ", ["B0:0"], "AvoDerm®"),  # อักษรสมมูล
])
def test_equivalent_texts_are_noise_not_a_reference_error(ta, tb, aw, aq, bw, bq):
    pr, A, B = _pr([ta], [tb])
    f, why, kind = ai_review.check_item(_it(aw, aq, bw, bq), A, B)
    assert f is None and kind == "equivalent" and "เท่ากันตามกติกาเทียบ" in why
    st = ai_review.merge("assist", pr, {"items": [_it(aw, aq, bw, bq, "noise")]}, A, B)
    assert st["invalid"] == [] and st["items_equivalent"] == 1 and st["extra_noise"] == 1
    assert st["ref_accuracy"] == 1.0
    assert not any(f.get("source") == "ai" for f in pr["findings"])


def test_equivalence_keeps_real_differences():
    """ตัวพิมพ์/ตัวเลข/จุดทศนิยมเดี่ยว ไม่ใช่สิ่งที่สมมูลกัน"""
    for ta, tb in (("D-calcium", "D-Calcium"), ("1.5g", "15g"), ("Breed", "Breeds")):
        _, A, B = _pr([ta], [tb])
        _, _, kind = ai_review.check_item(_it(["A0:0"], ta, ["B0:0"], tb), A, B)
        assert kind == "ok", (ta, tb)


def test_equivalence_flag_off_is_the_old_behaviour(monkeypatch):
    _off(monkeypatch, "AI_EQUIV_NOISE")
    _, A, B = _pr(["Omega-6 Fatty Acid* (min)"], ["Omega-6 Fatty Acid * (min)"])
    f, why, kind = ai_review.check_item(_it(["A0:2"], "Acid*", ["B0:2", "B0:3"], "Acid *"), A, B)
    assert kind == "invalid" and "ช่องว่าง" in why


# ── ③ judge: กันพับของจริง / คงจุดแดงของอัลกอริทึม / ข้อความโค้ง ────────

def test_judge_noise_on_clearly_read_digits_is_kept_yellow(monkeypatch):
    pr, A, B = _pr(["Fat 20%"], ["Fat 24%"])
    ai_review.merge("judge", pr, {"items": [_it(["A0:1"], "20%", ["B0:1"], "24%", "noise")]}, A, B)
    assert pr["ai_dismissed"] == [] and len(pr["findings"]) == 1
    f = pr["findings"][0]
    assert f["severity"] == "yellow" and any("สัญญาณรบกวน" in n for n in f["notes"])
    _off(monkeypatch, "AI_JUDGE_NOISE_GUARD")
    pr, A, B = _pr(["Fat 20%"], ["Fat 24%"])
    ai_review.merge("judge", pr, {"items": [_it(["A0:1"], "20%", ["B0:1"], "24%", "noise")]}, A, B)
    assert len(pr["ai_dismissed"]) == 1


@pytest.mark.parametrize("ta,tb,ca,curved", [
    (["Fat 20%"], ["Fat 24%"], {0: 0.55}, None),                      # Vision ไม่มั่นใจ
    (["Fat 20%"], ["Fat 24%"], None, {"A": [0], "B": [0]}),            # ข้อความโค้ง
])
def test_judge_noise_without_hard_evidence_is_still_folded(ta, tb, ca, curved):
    pr, A, B = _pr(ta, tb, ca=ca, curved=curved)
    ai_review.merge("judge", pr, {"items": [_it(["A0:1"], "20%", ["B0:1"], "24%", "noise")]}, A, B)
    assert len(pr["ai_dismissed"]) == 1 and not [f for f in pr["findings"] if f.get("source") == "ai"]


def test_judge_keeps_algorithm_red_that_ai_did_not_mention(monkeypatch):
    pr, A, B = _pr(["Fat 20%", "Net 85 g", "Ash 4.0%"], ["Fat 24%", "Net 86 g", "Ash 4.0%"],
                   cb={1: 0.6})
    assert [f["severity"] for f in pr["findings"]] == ["red", "yellow"]
    st = ai_review.merge("judge", pr, {"items": []}, A, B)
    assert [f["severity"] for f in pr["findings"]] == ["yellow"]
    assert pr["findings"][0]["a"]["frag"] == "0" and any("คงไว้" in n for n in pr["findings"][0]["notes"])
    assert len(pr["algo_only"]) == 1 and pr["algo_only"][0]["a"]["frag"] == "5"    # เหลืองยังพับ
    assert st["algo_red_kept"] == 1
    _off(monkeypatch, "AI_JUDGE_KEEP_ALGO_RED")
    pr, A, B = _pr(["Fat 20%", "Net 85 g"], ["Fat 24%", "Net 86 g"], cb={1: 0.6})
    ai_review.merge("judge", pr, {"items": []}, A, B)
    assert pr["findings"] == [] and len(pr["algo_only"]) == 2


def test_judge_real_on_curved_text_is_yellow(monkeypatch):
    pr, A, B = _pr(["OMEGA-6"], ["OMEGA-62"], curved={"A": [0], "B": [0]})
    item = _it(["A0:0"], "OMEGA-6", ["B0:0"], "OMEGA-62")
    ai_review.merge("judge", pr, {"items": [copy.deepcopy(item)]}, A, B)
    f = next(f for f in pr["findings"] if f.get("source") == "ai")
    assert f["curved"] and f["severity"] == "yellow" and any("โค้ง" in n for n in f["notes"])
    _off(monkeypatch, "AI_JUDGE_CURVED_YELLOW")
    pr, A, B = _pr(["OMEGA-6"], ["OMEGA-62"], curved={"A": [0], "B": [0]})
    ai_review.merge("judge", pr, {"items": [copy.deepcopy(item)]}, A, B)
    assert next(f for f in pr["findings"] if f.get("source") == "ai")["severity"] == "red"


# ── ④ ข้อมูลที่ส่ง: ธงข้อความโค้ง ─────────────────────────────────────

def test_payload_marks_curved_lines(monkeypatch):
    _, A, B = _pr(["OMEGA-6", "Fat 20%"], ["OMEGA-62", "Fat 24%"])
    p = ai_review.build_payload(1, "judge", A, B, (1000, 1000), (1000, 1000), [],
                                curved={"A": [0], "B": []})
    assert p["zone_a"][0].get("curved") is True and "curved" not in p["zone_a"][1]
    assert not any("curved" in ln for ln in p["zone_b"])
    assert "curved" not in ai_review.build_payload(1, "judge", A, B, (1000, 1000), (1000, 1000),
                                                   [])["zone_a"][0]
    _off(monkeypatch, "AI_SEND_CURVED")
    p = ai_review.build_payload(1, "judge", A, B, (1000, 1000), (1000, 1000), [],
                                curved={"A": [0], "B": []})
    assert "curved" not in p["zone_a"][0]


# ── ⑤ เล่นซ้ำคำตอบจริงของ Gemini จาก Log สถานี ─────────────────────────

def _replay_pr(name):
    d = load_log(os.path.join(REPLAY, name))[1]
    L = {s: textmodel.parse(fta_from_lines(d[s][2], d[s][0], d[s][1]), d[s][0], d[s][1])["lines"]
         for s in "AB"}
    r = compare.compare(L["A"], L["B"], tuple(d["A"][:2]), tuple(d["B"][:2]))
    pr = {"n": 1, "findings": r["findings"], "curved_lines": r["curved_lines"]}
    pipeline._reread([pr], None, None, None, None, [], [], lambda *a: None)   # อ่านซ้ำปิด
    pr["findings"] = compare.collapse_curved(pr["findings"])
    for i, f in enumerate(pr["findings"], 1):
        f["id"] = i
    return pr, r["lines_a"], r["lines_b"]


def _frags(fs, sev=None):
    return sorted((f["a"].get("frag") or "").strip() + "|" + (f["b"].get("frag") or "").strip()
                  for f in fs if sev is None or f["severity"] == sev)


def test_station_judge_log_with_old_rules_reproduces_what_the_station_showed(monkeypatch):
    """ตาข่าย: ปิดทุกชั้น ⇒ ผลเท่าที่สถานีแสดงจริง (12 ใช้ได้ · 7 ปฏิเสธ · 4 พับ algo_only)"""
    _off(monkeypatch)
    pr, A, B = _replay_pr("avoderm_run004_judge.log")
    st = ai_review.merge("judge", pr, {"items": copy.deepcopy(JUDGE_RUN004)}, A, B)
    assert (st["items_valid"], len(st["invalid"]), st["ref_accuracy"]) == (12, 7, 0.6316)
    assert len(pr["findings"]) == 6 and len(pr["ai_dismissed"]) == 6
    assert _frags(pr["algo_only"]) == sorted([",|", "Park|", "phate Pentahydr|f", "|0"])


def test_station_judge_log_new_rules_catch_every_content_change():
    """ของจริง 4 บรรทัด: D-Calcium · Sulphate→Sulfate (+Pentahydrate) · Park + USA · Breeds"""
    pr, A, B = _replay_pr("avoderm_run004_judge.log")
    st = ai_review.merge("judge", pr, {"items": copy.deepcopy(JUDGE_RUN004)}, A, B)
    red = _frags(pr["findings"], "red")
    for want in ("c|C", "ph|f", "phate Pentahydr|f", "Park|", "|USA", "|s"):
        assert want in red, (want, red)
    assert st["invalid"] == [] and st["ref_accuracy"] == 1.0
    assert st["recovered"] == 3 and st["items_equivalent"] == 10
    # จุดไข่ปลา/ช่องว่างไม่เป็นจุด · คอมมาที่ AI ไม่ได้ระบุยังพับ (เหลือง) · ข้อความโค้งไม่แดง
    assert _frags(pr["algo_only"]) == sorted([",|", "|0"])
    assert all(f["severity"] != "red" for f in pr["findings"] if f.get("curved"))


def test_station_assist_log_leader_dot_quotes_are_equivalent_not_errors():
    pr, A, B = _replay_pr("avoderm_run005_assist.log")
    before = [(f["id"], f["severity"]) for f in pr["findings"]]
    st = ai_review.merge("assist", pr, {"items": copy.deepcopy(ASSIST_RUN005_ITEMS)}, A, B)
    assert st["invalid"] == [] and st["items_equivalent"] == 9 and st["extra_noise"] == 9
    # assist: AI ลบ/ลดระดับจุดของอัลกอริทึมไม่ได้
    assert [(f["id"], f["severity"]) for f in pr["findings"]] == before


def test_new_flags_are_in_the_log_settings():
    snap = pipeline.settings_snapshot()
    for k in RULES + ("AI_QUOTE_RECOVER_MAX_SHIFT",):
        assert k in snap
