"""ประวัติการแปล + ตัวกรองของหน้าประวัติ Artwork (28 ก.ย. 2026).

ล็อก 4 เรื่อง:
  1. ทุกครั้งที่กดแปลถูกบันทึก (รวมครั้งที่ล้ม) — แต่ครั้งที่ล้มต้องไม่ทับ
     ตารางของครั้งล่าสุดที่สำเร็จ
  2. งานที่ "แปลอย่างเดียว" ขึ้นในประวัติ · ปิดธง = เหมือนเดิมเป๊ะ
  3. ตัวกรองทุกตัวทำงาน · ค่าที่ไม่รู้จัก = ไม่กรอง (ไม่ใช่ error)
  4. ไม่แตะแคชคำแปล (translation.json) และไม่ทำให้การแปลล้ม
"""

import json
import os

import numpy as np
import pytest
from flask import Flask, g

from artwork_check import config, ownership, report, translate
from artwork_check.routes import artwork_bp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALICE = {"sub": "1", "username": "alice", "role": "Staff", "perms": []}
BOB = {"sub": "2", "username": "bob", "role": "Staff", "perms": []}
ADMIN = {"sub": "9", "username": "root", "role": "Admin", "perms": []}


@pytest.fixture
def store(tmp_path, monkeypatch):
    d = tmp_path / "inspections"
    d.mkdir()
    monkeypatch.setattr(config, "INSPECTIONS_DIR", str(d))
    monkeypatch.setattr(config, "HISTORY_PER_USER", True)
    monkeypatch.setattr(config, "HISTORY_ADMIN_ROLES", ("Admin",))
    monkeypatch.setattr(config, "HISTORY_TRANSLATE", True)
    return d


def _rec(store, rec_id, owner=None, report_json=None, filename=None):
    (store / rec_id).mkdir()
    if owner:
        report.save_owner(rec_id, owner)
    if filename is not None:
        report.save_meta(rec_id, filename)
    if report_json is not None:
        rep = {"id": rec_id, "created_at": "2026-08-11 09:00:00",
               "filename": "r.pdf", "brand": "", "verdict": "PASS",
               "defects": [], "zones": [], "ocr": []}
        rep.update(report_json)
        (store / rec_id / "report.json").write_text(
            json.dumps(rep), encoding="utf-8")
    return rec_id


def _result(n_ok=2, n_spell=1, n_ai=1, translated=True, ocr_only=True):
    rows = [{"src": "ok %d" % i, "status": "ok", "ai_spell": {}}
            for i in range(n_ok)]
    rows += [{"src": "sp %d" % i, "status": "spell", "ai_spell": {}}
             for i in range(n_spell)]
    rows += [{"src": "ai %d" % i, "status": "ok",
              "ai_spell": {"flagged": True}} for i in range(n_ai)]
    return {"rows": rows, "translated": translated, "ocr_only": ocr_only,
            "ai_spell_available": True, "enabled": True}


ALICE_O = {"user_id": "1", "username": "alice"}
BOB_O = {"user_id": "2", "username": "bob"}


# ── 1. การบันทึก ──────────────────────────────────────────────────────

def test_every_press_is_logged_and_last_table_kept(store):
    rid = _rec(store, "20260901-100000-aaaaaa", ALICE_O, filename="x.pdf")
    report.record_translation(rid, _result(), by="alice", brand="AvoDerm")
    report.record_translation(rid, _result(n_spell=3), by="bob")
    data = report.load_translations(rid)
    assert data["count"] == 2
    assert [e["by"] for e in data["log"]] == ["bob", "alice"]   # ใหม่สุดก่อน
    first = data["log"][1]
    # issues ต้องนิยามเดียวกับตัวกรอง "เฉพาะบรรทัดน่าสงสัย" ของ renderTextTable
    assert first["rows"] == 4 and first["issues"] == 2
    assert first["spell"] == 1 and first["ai_flagged"] == 1
    assert data["last"]["by"] == "bob"
    assert len(data["last"]["table"]["rows"]) == 6
    assert data["last"]["table"]["ai_spell_available"] is True


def test_failed_press_is_logged_but_does_not_erase_last_table(store):
    rid = _rec(store, "20260901-100001-aaaaaa", ALICE_O)
    report.record_translation(rid, _result(), by="alice")
    report.record_translation(rid, {"rows": []}, by="alice",
                              error="แปลไม่สำเร็จ: timeout")
    data = report.load_translations(rid)
    assert data["count"] == 2
    assert data["log"][0]["error"].startswith("แปลไม่สำเร็จ")
    assert len(data["last"]["table"]["rows"]) == 4      # ตารางเดิมยังอยู่
    s = report.translate_summary(rid)
    assert s["error"] and s["count"] == 2


def test_record_never_raises(store):
    assert report.record_translation("not-an-id", _result()) is None
    # โฟลเดอร์ไม่มีอยู่ → เขียนไม่ได้ → คืน None ไม่ raise
    assert report.record_translation("20260901-100002-ffffff",
                                      _result()) is None


def test_corrupt_log_line_is_skipped(store):
    rid = _rec(store, "20260901-100003-aaaaaa")
    report.record_translation(rid, _result())
    with open(os.path.join(store, rid, "translate_log.jsonl"), "a") as f:
        f.write('{"at": "2026-09-01 1')          # เขียนค้างครึ่งบรรทัด
    assert report.load_translations(rid)["count"] == 1


def test_log_read_is_capped(store, monkeypatch):
    monkeypatch.setattr(config, "TRANSLATE_LOG_MAX", 3)
    rid = _rec(store, "20260901-100004-aaaaaa")
    for _ in range(5):
        report.record_translation(rid, _result())
    data = report.load_translations(rid)
    assert data["count"] == 5 and len(data["log"]) == 3


# ── 2. รายการประวัติ ──────────────────────────────────────────────────

def test_translate_only_record_is_listed(store):
    rid = _rec(store, "20260901-100005-aaaaaa", ALICE_O, filename="only.pdf")
    report.record_translation(rid, _result(), by="alice", brand="Brand1")
    rows = report.list_inspections(include_translate=True)
    assert len(rows) == 1
    r = rows[0]
    assert r["kind"] == "translate" and r["filename"] == "only.pdf"
    assert r["verdict"] == "" and r["defect_count"] is None
    assert r["brand"] == "Brand1" and r["owner"] == "alice"
    assert r["translate"]["count"] == 1 and r["translate"]["issues"] == 2
    assert "_days" not in r["translate"]      # คีย์ภายในต้องไม่หลุดถึง JSON


def test_kind_both_and_inspect(store):
    a = _rec(store, "20260901-100006-aaaaaa", report_json={})
    b = _rec(store, "20260901-100007-bbbbbb", report_json={})
    report.record_translation(b, _result())
    kinds = {r["id"]: r["kind"]
             for r in report.list_inspections(include_translate=True)}
    assert kinds == {a: "inspect", b: "both"}


def test_uploaded_but_untouched_record_is_not_listed(store):
    _rec(store, "20260901-100008-aaaaaa", ALICE_O, filename="x.pdf")
    assert report.list_inspections(include_translate=True) == []


def test_legacy_translate_only_falls_back_to_time_from_id(store):
    rid = _rec(store, "20260901-100009-aaaaaa")          # ไม่มี meta.json
    report.record_translation(rid, _result())
    r = report.list_inspections(include_translate=True)[0]
    assert r["created_at"] == "2026-09-01 10:00:09" and r["filename"] == ""


def test_default_path_is_unchanged_and_ignores_translate_only(store):
    _rec(store, "20260901-100010-aaaaaa", report_json={})
    t = _rec(store, "20260901-100011-bbbbbb")
    report.record_translation(t, _result())
    rows = report.list_inspections()
    assert [r["id"] for r in rows] == ["20260901-100010-aaaaaa"]
    assert set(rows[0]) == {"id", "created_at", "filename", "brand",
                            "verdict", "defect_count", "owner"}


def test_owner_filter_still_applies_to_translate_only(store):
    _rec(store, "20260901-100012-aaaaaa", ALICE_O)
    b = _rec(store, "20260901-100013-bbbbbb", BOB_O)
    for rid in ("20260901-100012-aaaaaa", b):
        report.record_translation(rid, _result())
    rows = report.list_inspections(include_translate=True,
                                   can_view=ownership.make_filter(ALICE))
    assert [r["owner"] for r in rows] == ["alice"]


# ── 3. ตัวกรอง ───────────────────────────────────────────────────────

@pytest.fixture
def mixed(store):
    ids = {}
    ids["pass"] = _rec(store, "20260801-090000-aaaaaa", ALICE_O,
                       report_json={"created_at": "2026-08-01 09:00:00",
                                    "filename": "Cosma_V12.pdf",
                                    "brand": "Cosma", "verdict": "PASS"})
    ids["fail"] = _rec(store, "20260805-090000-bbbbbb", BOB_O,
                       report_json={"created_at": "2026-08-05 09:00:00",
                                    "filename": "Salmon.pdf",
                                    "brand": "WholeHearted", "verdict": "FAIL"})
    ids["tr"] = _rec(store, "20260810-090000-cccccc", ALICE_O,
                     filename="AvoDerm_Master.pdf")
    report.record_translation(ids["tr"], _result(n_spell=0, n_ai=0))
    report.record_translation(ids["fail"], _result())      # both + issues
    return ids


def _ids(**f):
    return {r["id"] for r in report.list_inspections(
        include_translate=True, filters=f)}


def test_filter_kind(mixed):
    assert _ids(kind="translate") == {mixed["tr"]}
    assert _ids(kind="both") == {mixed["fail"]}
    assert _ids(kind="inspect") == {mixed["pass"]}


def test_filter_verdict_excludes_translate_only(mixed):
    assert _ids(verdict="fail") == {mixed["fail"]}       # ตัวพิมพ์เล็กก็ได้
    assert _ids(verdict="PASS") == {mixed["pass"]}


def test_filter_text_search_is_case_insensitive(mixed):
    assert _ids(q="cosma") == {mixed["pass"]}
    assert _ids(q="WHOLEHEARTED") == {mixed["fail"]}      # ค้นในแบรนด์ด้วย
    assert _ids(q="avoderm") == {mixed["tr"]}


def test_filter_translation_issues(mixed):
    assert _ids(tr_issues="1") == {mixed["fail"]}


def test_filter_owner(mixed):
    assert _ids(owner="ALICE") == {mixed["pass"], mixed["tr"]}


def test_filter_date_matches_upload_day(mixed):
    assert _ids(date_from="2026-08-01", date_to="2026-08-01") == {mixed["pass"]}
    assert _ids(date_from="2026-08-02", date_to="2026-08-09") == {mixed["fail"]}
    # ตั้งแต่ 6 ส.ค.: tr อัปโหลด 10 ส.ค. · fail อัปโหลด 5 ส.ค. แต่ถูกแปลวันนี้
    assert _ids(date_from="2026-08-06") == {mixed["tr"], mixed["fail"]}


def test_filter_date_matches_translation_day(mixed):
    import time
    today = time.strftime("%Y-%m-%d")
    got = _ids(date_from=today, date_to=today)
    assert got == {mixed["tr"], mixed["fail"]}     # ทั้งสองถูกแปลวันนี้
    assert mixed["pass"] not in got


def test_filters_combine(mixed):
    assert _ids(owner="alice", kind="translate") == {mixed["tr"]}
    assert _ids(owner="bob", kind="translate") == set()


def test_unknown_filter_values_mean_no_filter(mixed):
    f = report.normalize_filters({"kind": "xx", "verdict": "maybe",
                                  "date_from": "01/08/2026", "tr_issues": "no"})
    assert f == {}
    assert len(_ids(kind="xx", verdict="maybe")) == 3


def test_flag_off_drops_kind_filters_instead_of_hiding_everything(store):
    _rec(store, "20260901-100014-aaaaaa", report_json={})
    rows = report.list_inspections(include_translate=False,
                                   filters={"kind": "translate",
                                            "tr_issues": "1"})
    assert len(rows) == 1 and "kind" not in rows[0]


def test_limit_applies_after_filter(store):
    for i in range(5):
        _rec(store, "20260901-1001%02d-aaaaaa" % i,
             report_json={"filename": "match%d.pdf" % i})
    _rec(store, "20260901-100199-bbbbbb", report_json={"filename": "zzz.pdf"})
    rows = report.list_inspections(limit=2, include_translate=True,
                                   filters={"q": "match"})
    assert len(rows) == 2


def test_list_owners_respects_visibility(mixed):
    assert report.list_owners(ownership.make_filter(ADMIN)) == ["alice", "bob"]
    assert report.list_owners(ownership.make_filter(ALICE)) == ["alice"]


# ── 4. ชั้น HTTP ─────────────────────────────────────────────────────

@pytest.fixture
def app(store):
    app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"))
    app.register_blueprint(artwork_bp)
    app.config["viewer"] = None

    @app.before_request
    def _fake_auth():
        v = app.config["viewer"]
        g.auth_enabled = v is not None
        g.current_user = v

    return app


def _as(app, viewer):
    app.config["viewer"] = viewer
    return app.test_client()


def _stub_translate(monkeypatch, fail=False):
    def fake(insp_dir, rows):
        if fail:
            raise RuntimeError("boom")
        for r in rows:
            r["en"] = "EN"
            r["ai_spell"] = {"flagged": False}
        return {"rows": rows, "translated": True, "ai_spell_available": True}
    monkeypatch.setattr(translate, "translate_table", fake)


def _inspected(store, rid, owner):
    return _rec(store, rid, owner, report_json={
        "zones": [{"id": "z1", "type": "panel", "group": "A",
                   "bbox": [0, 0, 1, 1]}],
        "ocr": [{"zone_id": "z1", "text": "Chicken\nSalt"}],
    })


def test_http_translate_records_who_pressed(app, store, monkeypatch):
    _stub_translate(monkeypatch)
    rid = _inspected(store, "20260901-110000-aaaaaa", ALICE_O)
    r = _as(app, ALICE).post(f"/api/artwork/{rid}/translate", json={})
    assert r.status_code == 200
    data = report.load_translations(rid)
    assert data["count"] == 1 and data["log"][0]["by"] == "alice"
    assert data["log"][0]["rows"] == 2 and data["log"][0]["ocr_only"] is False


def test_http_translate_response_unchanged_by_recording(app, store,
                                                        monkeypatch):
    """การบันทึกต้องไม่แก้ผลที่ส่งกลับให้หน้าตรวจ."""
    _stub_translate(monkeypatch)
    rid = _inspected(store, "20260901-110001-aaaaaa", ALICE_O)
    c = _as(app, ALICE)
    on = c.post(f"/api/artwork/{rid}/translate", json={}).get_json()
    monkeypatch.setattr(config, "HISTORY_TRANSLATE", False)
    off = c.post(f"/api/artwork/{rid}/translate", json={}).get_json()
    assert on == off
    assert report.load_translations(rid)["count"] == 1   # ครั้งที่ปิดธงไม่บันทึก


def test_http_translate_failure_is_logged(app, store, monkeypatch):
    _stub_translate(monkeypatch, fail=True)
    rid = _inspected(store, "20260901-110002-aaaaaa", ALICE_O)
    r = _as(app, ALICE).post(f"/api/artwork/{rid}/translate", json={})
    assert r.status_code == 500
    log = report.load_translations(rid)["log"]
    assert len(log) == 1 and "boom" in log[0]["error"]


def test_http_translate_does_not_touch_cache_file(app, store, monkeypatch):
    _stub_translate(monkeypatch)                  # stub ไม่เขียนแคช
    rid = _inspected(store, "20260901-110003-aaaaaa", ALICE_O)
    _as(app, ALICE).post(f"/api/artwork/{rid}/translate", json={})
    assert not os.path.exists(os.path.join(store, rid, "translation.json"))


def test_http_translations_endpoint(app, store):
    rid = _rec(store, "20260901-110004-aaaaaa", ALICE_O)
    c = _as(app, ALICE)
    assert c.get(f"/api/artwork/{rid}/translations").status_code == 404
    report.record_translation(rid, _result(), by="alice")
    body = c.get(f"/api/artwork/{rid}/translations").get_json()
    assert body["count"] == 1 and body["last"]["table"]["rows"]


def test_http_translations_blocked_for_other_user(app, store):
    rid = _rec(store, "20260901-110005-aaaaaa", ALICE_O)
    report.record_translation(rid, _result(), by="alice")
    assert _as(app, BOB).get(
        f"/api/artwork/{rid}/translations").status_code == 403


def test_http_history_filters_and_owner_list(app, store, mixed):
    body = _as(app, ADMIN).get(
        "/api/artwork/history?kind=translate").get_json()
    assert [r["id"] for r in body["records"]] == [mixed["tr"]]
    assert body["owners"] == ["alice", "bob"]
    assert body["filters"] == {"kind": "translate"}
    assert body["translate_history"] is True
    # ผู้ใช้ทั่วไป (เห็นเฉพาะของตัวเอง) ไม่ได้ตัวเลือกผู้ตรวจ
    body = _as(app, BOB).get("/api/artwork/history").get_json()
    assert "owners" not in body
    assert {r["id"] for r in body["records"]} == {mixed["fail"]}


def test_http_history_flag_off_is_old_behaviour(app, store, mixed,
                                                monkeypatch):
    monkeypatch.setattr(config, "HISTORY_TRANSLATE", False)
    body = _as(app, ADMIN).get("/api/artwork/history").get_json()
    ids = {r["id"] for r in body["records"]}
    assert ids == {mixed["pass"], mixed["fail"]}          # ไม่มีงานแปลอย่างเดียว
    assert all(set(r) == {"id", "created_at", "filename", "brand", "verdict",
                          "defect_count", "owner"} for r in body["records"])


def test_upload_writes_meta(store):
    import cv2
    from artwork_check import pipeline
    img = np.full((300, 400, 3), 255, np.uint8)
    cv2.putText(img, "HELLO", (40, 150), cv2.FONT_HERSHEY_SIMPLEX, 2,
                (0, 0, 0), 3)
    ok, buf = cv2.imencode(".png", img)
    info = pipeline.start_inspection(buf.tobytes(), "Label A.png")
    assert report.load_meta(info["id"])["filename"] == "Label A.png"


# ── 5. หน้าเว็บ ──────────────────────────────────────────────────────

def _read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8") as f:
        return f.read()


def test_history_js_column_count_matches_template():
    js = _read("static/js/artwork_check_history.js")
    html = _read("templates/artwork_check_history.html")
    assert "const COLS = TR ? 9 : 7;" in js
    assert "{{ 9 if history_translate else 7 }}" in html
    # เปิดธง: 7 คอลัมน์เดิม + ประเภท + คำแปล
    thead = html[html.index("<thead>"):html.index("</thead>")]
    assert thead.count("<th>") == 9
    assert thead.count("{% if history_translate %}<th>") == 2


def test_history_page_passes_flag_and_uses_shared_renderer():
    js = _read("static/js/artwork_check_history.js")
    html = _read("templates/artwork_check_history.html")
    assert "window.AW_HISTORY_TRANSLATE" in html
    assert "window.awRenderTextTable(" in js
    # id ทุกตัวที่ JS อ้างต้องมีจริงใน template ($("id") ที่ไม่มีจะเงียบ)
    import re
    for i in set(re.findall(r'\$\("([A-Za-z]+)"\)', js)):
        assert 'id="%s"' % i in html, "template ไม่มี #%s" % i


def test_real_upload_name_beats_stored_source_name(store, app):
    """report.json เก็บ ``source.pdf`` เสมอ — ประวัติและหัวรายงานต้องแสดง
    ชื่อที่ผู้ใช้อัปโหลด (จาก meta.json) และค้นหาด้วยชื่อนั้นได้."""
    rid = _rec(store, "20260901-120000-aaaaaa", report_json={
        "filename": "source.pdf"}, filename="Cosma_V12.pdf")
    rows = report.list_inspections(include_translate=True,
                                   filters={"q": "cosma_v12"})
    assert [r["filename"] for r in rows] == ["Cosma_V12.pdf"]
    body = _as(app, None).get(f"/api/artwork/{rid}/report").get_json()
    assert body["filename"] == "Cosma_V12.pdf"
    stored = json.loads((store / rid / "report.json").read_text())
    assert stored["filename"] == "source.pdf"          # ไม่เขียนทับไฟล์
