# -*- coding: utf-8 -*-
"""จุดเช็คพอยต์ระหว่างตรวจ — เส้นความคืบหน้าที่บอก "โหมดไหนทำงาน/ตกเงื่อนไข".

ที่มา (ผู้ใช้ 8 ก.ย. 2026): ระหว่างส่งตรวจหน้าเว็บขึ้นข้อความคงที่
*"กำลัง OCR ทีละโซนและตรวจทุกชั้น…"* ซึ่งไม่ได้บอกอะไรเลย — โดยเฉพาะ
**โหมดที่ติ๊กไว้ได้ทำงานจริงไหม หรือตกเงื่อนไขไปเงียบ ๆ**

⚠️ ทุกอย่างเป็น advisory 100% — ไม่แตะ ``defects``/``verdict``/การนับ/DB
   และ **ล้มเหลวเงียบเสมอ** (บันทึกความคืบหน้าไม่สำเร็จต้องไม่ทำให้ตรวจพัง)
"""
import threading

import pytest

from artwork_check import progress as PG
from artwork_check import pipeline


@pytest.fixture(autouse=True)
def _clean():
    yield
    with PG._LOCK:
        PG._RUNS.clear()


# ── โครงของเส้น ──────────────────────────────────────────────────────

def test_the_steps_match_the_real_order_of_the_pipeline():
    """ลำดับบนเส้นต้องตรงกับลำดับที่ ``run_inspection`` ทำจริง — ไม่งั้น
    ผู้ใช้จะอ่านเส้นแล้วเข้าใจกระบวนการผิด."""
    assert [k for k, _ in PG.STEPS] == [
        "prepare", "fonttrust", "ocr", "checks", "confirm", "pixel",
        "coverage", "report"]


def test_every_step_is_visible_from_the_start():
    """โหมดที่ไม่ได้เปิดต้อง **ยังอยู่บนเส้น** (เป็น skip) ไม่ใช่หายไป —
    ผู้ใช้ต้องเห็นว่ามีขั้นนั้นและทำไมไม่ทำงาน."""
    PG.begin("r1")
    snap = PG.snapshot("r1")
    assert len(snap["steps"]) == len(PG.STEPS)
    assert all(s["status"] == PG.PENDING for s in snap["steps"])


def test_a_skipped_step_always_carries_a_reason():
    run = PG.begin("r2")
    run.skip("pixel", "ไม่ได้ติ๊กช่อง")
    s = next(x for x in PG.snapshot("r2")["steps"] if x["key"] == "pixel")
    assert s["status"] == PG.SKIP and s["detail"]


def test_starting_a_step_closes_the_previous_running_one():
    """ห้ามมีจุดกะพริบสองจุดพร้อมกันบนเส้น."""
    run = PG.begin("r3")
    run.start("ocr")
    run.start("checks")
    st = {s["key"]: s["status"] for s in PG.snapshot("r3")["steps"]}
    assert st["ocr"] == PG.OK and st["checks"] == PG.RUNNING


def test_finish_never_leaves_a_step_spinning_forever():
    run = PG.begin("r4")
    run.start("ocr")
    run.finish(PG.OK, "PASS")
    snap = PG.snapshot("r4")
    assert snap["done"] is True
    assert not any(s["status"] == PG.RUNNING for s in snap["steps"])
    assert not any(s["status"] == PG.PENDING for s in snap["steps"])


def test_a_failed_run_marks_the_remaining_steps_as_failed_not_done():
    """ตรวจพังกลางคัน ⇒ ขั้นที่ยังไม่ถึงต้องไม่ขึ้นเขียว (จะอ่านว่าผ่าน)."""
    run = PG.begin("r5")
    run.done("prepare", PG.OK)
    run.start("ocr")
    run.finish(PG.FAIL, "N8N ต่อไม่ได้")
    st = {s["key"]: s["status"] for s in PG.snapshot("r5")["steps"]}
    assert st["prepare"] == PG.OK
    assert st["checks"] == PG.FAIL and st["report"] == PG.FAIL


def test_timing_is_recorded_per_step():
    run = PG.begin("r6")
    run.start("ocr")
    run.done("ocr", PG.OK, "เสร็จ")
    s = next(x for x in PG.snapshot("r6")["steps"] if x["key"] == "ocr")
    assert isinstance(s["ms"], int) and s["ms"] >= 0


def test_notes_are_capped_so_a_huge_run_cannot_bloat_memory():
    run = PG.begin("r7")
    for i in range(500):
        run.note("ocr", "โซน %d" % i)
    s = next(x for x in PG.snapshot("r7")["steps"] if x["key"] == "ocr")
    assert 0 < len(s["notes"]) <= 40


def test_old_runs_are_dropped_so_memory_stays_bounded():
    for i in range(PG.MAX_RUNS + 8):
        PG.begin("run%d" % i)
    with PG._LOCK:
        assert len(PG._RUNS) <= PG.MAX_RUNS
    assert PG.snapshot("run0") is None


def test_snapshot_is_a_copy_not_the_live_object():
    run = PG.begin("r8")
    run.note("ocr", "หนึ่ง")
    snap = PG.snapshot("r8")
    snap["steps"][0]["status"] = "แก้มั่ว"
    snap["steps"][2]["notes"].append("สอง")
    again = PG.snapshot("r8")
    assert again["steps"][0]["status"] == PG.PENDING
    assert len(again["steps"][2]["notes"]) == 1


def test_an_unknown_id_is_not_an_error():
    assert PG.snapshot("ไม่มีอยู่จริง") is None


def test_the_null_recorder_registers_nothing_and_never_raises():
    """เส้นทางที่ไม่ต้องการความคืบหน้า (เทสต์เดิม/สคริปต์) ต้องใช้ได้เหมือนเดิม."""
    n = PG.NullRun()
    n.start("ocr"); n.note("ocr", "x"); n.skip("pixel", "y")
    n.done("ocr", PG.OK); n.finish(PG.OK)
    with PG._LOCK:
        assert not PG._RUNS


def test_two_inspections_at_once_never_mix(monkeypatch):
    """Flask รันแบบ threaded=True — สองใบพร้อมกันต้องไม่เขียนทับกัน."""
    def work(rid, label):
        run = PG.begin(rid)
        for _ in range(30):
            run.note("ocr", label)
        run.finish(PG.OK)
    ts = [threading.Thread(target=work, args=("a", "AAA")),
          threading.Thread(target=work, args=("b", "BBB"))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    for rid, label in (("a", "AAA"), ("b", "BBB")):
        notes = next(s for s in PG.snapshot(rid)["steps"]
                     if s["key"] == "ocr")["notes"]
        assert notes and all(n == label for n in notes)


# ── ตัวสรุปผลลงจุดเช็คพอยต์ (สิ่งที่ผู้ใช้อ่านจริง) ─────────────────

def test_the_ocr_step_reports_which_engine_each_zone_used():
    run = PG.begin("r9")
    pipeline._report_ocr_progress(run, [
        {"zone_id": "z1", "engine": "pdf-text", "text": "abc"},
        {"zone_id": "z2", "engine": "n8n", "text": "de"},
        {"zone_id": "z3", "engine": "n8n", "text": "", "error": "ต่อไม่ได้"},
    ], {})
    s = next(x for x in PG.snapshot("r9")["steps"] if x["key"] == "ocr")
    assert s["status"] == PG.WARN and "อ่านไม่ได้ 1 โซน" in s["detail"]
    assert "pdf-text 1" in s["detail"] and "n8n 2" in s["detail"]
    assert any("z3" in n and "ต่อไม่ได้" in n for n in s["notes"])


def test_the_pixel_step_says_why_a_group_fell_back():
    """หัวใจของสิ่งที่ผู้ใช้ขอ — รู้ว่ากลุ่มไหนตกเงื่อนไขและเพราะอะไร."""
    run = PG.begin("r10")
    pipeline._report_pixel_progress(run, {"used": 1, "pairs": [
        {"group": "A", "status": "ok", "regions": 1, "edge_regions": 0,
         "diff_ratio": 0.00014},
        {"group": "B", "status": "skipped", "reason": "not_pdf"},
        {"group": "C", "status": "ok", "regions": 0, "kept_text_layer": True},
    ]})
    s = next(x for x in PG.snapshot("r10")["steps"] if x["key"] == "pixel")
    assert "1 จาก 3" in s["detail"]
    joined = " | ".join(s["notes"])
    assert "not_pdf" in joined
    assert "คงผลชั้นข้อความ" in joined


def test_the_pixel_step_says_clearly_when_no_group_qualified():
    run = PG.begin("r11")
    pipeline._report_pixel_progress(run, {"used": 0, "pairs": []})
    s = next(x for x in PG.snapshot("r11")["steps"] if x["key"] == "pixel")
    assert s["status"] == PG.SKIP and "เงื่อนไข" in s["detail"]


def test_the_coverage_step_lists_layers_that_did_not_run():
    run = PG.begin("r12")
    pipeline._report_coverage_progress(run, {
        "panels": {"ran": True},
        "zoom": {"ran": False, "reason": "no_zoom_zone"},
        "spelling": {"ran": False, "reason": "moved_to_translate"},
    })
    s = next(x for x in PG.snapshot("r12")["steps"] if x["key"] == "coverage")
    assert s["status"] == PG.WARN and "ไม่ได้ทำงาน 2 ชั้น" in s["detail"]
    assert any("no_zoom_zone" in n for n in s["notes"])
