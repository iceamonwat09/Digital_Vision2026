"""ใช้งานที่เคยตรวจเป็นต้นแบบตรวจ Lot ใหม่ (28 ก.ย. 2026).

ล็อก 5 เรื่อง:
  1. กรอบที่เก็บ = กรอบ "ตามที่ผู้ใช้วาด" (ก่อน pipeline เขียนทับ rotate)
  2. clone = **งานใหม่** · งานต้นแบบไม่ถูกแตะแม้แต่ไบต์เดียว
  3. คัดลอกเฉพาะ 🅰 — ไฟล์/กรอบ 🅱 ผลตรวจ ประวัติการแปล ไม่ตามมา
  4. สิทธิ์: ทุกคนใช้ต้นแบบของทุกคนได้ (ธงปิด = ตามด่านเจ้าของเดิม)
     แต่งานใหม่เป็นของคนกด และงานต้นแบบยังเปิดดูไม่ได้ถ้าไม่ใช่เจ้าของ
  5. ปิดธง = ไม่มี setup.json · ไม่มี endpoint = พฤติกรรมเดิม
"""

import hashlib
import json
import os

import cv2
import numpy as np
import pytest
from flask import Flask, g

from artwork_check import config, pipeline, report, translate
from artwork_check.routes import artwork_bp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALICE = {"sub": "1", "username": "alice", "role": "Staff", "perms": []}
BOB = {"sub": "2", "username": "bob", "role": "Staff", "perms": []}
ADMIN = {"sub": "9", "username": "root", "role": "Admin", "perms": []}
ALICE_O = {"user_id": "1", "username": "alice"}
BOB_O = {"user_id": "2", "username": "bob"}

ZA = {"id": "z1", "type": "panel", "group": "A", "doc": "a",
      "rotate": 90, "bbox": [0.1, 0.1, 0.4, 0.4], "label": "แผง 1"}
ZA2 = {"id": "z2", "type": "panel", "group": "B", "doc": "a",
       "rotate": "auto", "bbox": [0.5, 0.1, 0.4, 0.4]}
ZB = {"id": "b3", "type": "panel", "group": "A", "doc": "b",
      "rotate": "default", "bbox": [0.2, 0.2, 0.3, 0.3]}


@pytest.fixture
def store(tmp_path, monkeypatch):
    d = tmp_path / "inspections"
    d.mkdir()
    monkeypatch.setattr(config, "INSPECTIONS_DIR", str(d))
    monkeypatch.setattr(config, "HISTORY_PER_USER", True)
    monkeypatch.setattr(config, "HISTORY_ADMIN_ROLES", ("Admin",))
    monkeypatch.setattr(config, "HISTORY_TRANSLATE", True)
    monkeypatch.setattr(config, "CLONE_FROM_HISTORY", True)
    monkeypatch.setattr(config, "CLONE_SHARE_ALL", True)
    return d


def _png():
    img = np.full((300, 400, 3), 255, np.uint8)
    cv2.putText(img, "NET 170 g", (30, 150), cv2.FONT_HERSHEY_SIMPLEX, 1.5,
                (0, 0, 0), 3)
    return cv2.imencode(".png", img)[1].tobytes()


def _job(owner=ALICE_O, name="AvoDerm_Master.png", zones=(ZA, ZA2, ZB),
         settings=None, with_ref=True, with_report=True, with_tr=True):
    info = pipeline.start_inspection(_png(), name, owner=owner)
    rid = info["id"]
    d = report.inspection_dir(rid)
    if with_ref:
        pipeline.start_ref(rid, _png(), "lot_old.png")
    report.save_setup(rid, [dict(z) for z in zones],
                      settings or {"brand": "AvoDerm", "page_rot": 90,
                                   "auto_rotate": True, "pixel_check": True})
    if with_report:
        with open(os.path.join(d, "report.json"), "w") as f:
            json.dump({"id": rid, "created_at": "2026-09-01 10:00:00",
                       "filename": "source.png", "verdict": "FAIL",
                       "defects": [{"class": "MISMATCH_PANELS"}],
                       "zones": [dict(z, rotate=0) for z in zones]}, f)
    if with_tr:
        report.record_translation(rid, {"rows": [{"src": "x", "status": "ok"}],
                                        "translated": True}, by="alice")
    return rid


def _digest(d):
    out = {}
    for root, _, files in os.walk(d):
        for fn in files:
            p = os.path.join(root, fn)
            with open(p, "rb") as f:
                out[os.path.relpath(p, d)] = hashlib.sha1(f.read()).hexdigest()
    return out


# ── 1. กรอบที่เก็บ ────────────────────────────────────────────────────

def test_setup_roundtrip_keeps_user_rotation(store):
    rid = _job()
    s = report.load_setup(rid)
    assert [z["rotate"] for z in s["zones"]] == [90, "auto", "default"]
    assert s["page_rot"] == 90 and s["auto_rotate"] is True
    assert "approx_rotate" not in s


def test_setup_merge_keeps_settings_not_sent_again(store):
    rid = _job()
    # แท็บแปลไม่รู้จักช่องติ๊ก pixel ⇒ ส่งมาไม่ครบ ต้องไม่ลบค่าเดิม
    report.save_setup(rid, [ZA], {"brand": "X"})
    s = report.load_setup(rid)
    assert s["brand"] == "X" and s["pixel_check"] is True
    assert s["page_rot"] == 90


def test_legacy_setup_uses_view_rot_not_ocr_rotate(store):
    rid = _job()
    d = report.inspection_dir(rid)
    os.remove(os.path.join(d, "setup.json"))
    with open(os.path.join(d, "report.json"), "w") as f:
        json.dump({"zones": [
            {"id": "z1", "type": "panel", "group": "A", "doc": "a",
             "bbox": [0.1, 0.1, 0.3, 0.3], "rotate": 0, "view_rot": 90},
            {"id": "z2", "type": "panel", "group": "B", "doc": "a",
             "bbox": [0.5, 0.1, 0.3, 0.3], "rotate": 0}],
            "brand": "Old", "page_rot": 180}, f)
    s = report.load_setup(rid)
    # rotate ใน report = มุมที่ OCR หมุนจริง (0 บน pdf-text) — ห้ามเอามาใช้
    assert [z["rotate"] for z in s["zones"]] == [90, "default"]
    assert s["approx_rotate"] is True and s["page_rot"] == 180


def test_job_without_anything_is_not_a_source(store):
    info = pipeline.start_inspection(_png(), "x.png", owner=ALICE_O)
    assert report.load_setup(info["id"]) is None
    assert report.list_sources() == []


# ── 2-3. clone ────────────────────────────────────────────────────────

def test_clone_makes_new_job_and_never_touches_source(store):
    src = _job()
    before = _digest(report.inspection_dir(src))
    res = pipeline.clone_inspection(src, owner=BOB_O)
    assert res["id"] != src
    assert _digest(report.inspection_dir(src)) == before


def test_clone_copies_only_doc_a(store):
    src = _job()
    res = pipeline.clone_inspection(src, owner=BOB_O)
    nd = report.inspection_dir(res["id"])
    files = set(os.listdir(nd))
    assert {"source.png", "preview.png", "meta.json", "owner.json",
            "setup.json"} <= files
    for gone in ("source_b.png", "preview_b.png", "report.json",
                 "translate_log.jsonl", "translate_last.json"):
        assert gone not in files, gone
    assert [z["id"] for z in res["zones"]] == ["z1", "z2"]
    assert all(z["doc"] == "a" for z in res["zones"])
    assert [z["rotate"] for z in res["zones"]] == [90, "auto"]
    assert res["zones"][0]["group"] == "A"      # ใช้จับคู่อัตโนมัติกับ 🅱 ใหม่


def test_clone_carries_settings_owner_and_lineage(store):
    src = _job()
    res = pipeline.clone_inspection(src, owner=BOB_O)
    assert res["settings"]["page_rot"] == 90
    assert res["settings"]["pixel_check"] is True
    assert report.load_owner(res["id"])["username"] == "bob"
    meta = report.load_meta(res["id"])
    assert meta["filename"] == "AvoDerm_Master.png"
    assert meta["cloned_from"] == src
    # งานใหม่ใช้เป็นต้นแบบต่อได้ทันที แม้ยังไม่กดตรวจ
    assert len(report.load_setup(res["id"])["zones"]) == 2


def test_clone_shows_in_history_with_lineage_after_use(store):
    src = _job()
    res = pipeline.clone_inspection(src, owner=BOB_O)
    ids = {r["id"] for r in report.list_inspections(include_translate=True)}
    assert res["id"] not in ids          # ยังไม่ได้ตรวจ/แปล = ไม่ขึ้นประวัติ
    report.record_translation(res["id"], {"rows": [], "translated": False})
    row = next(r for r in report.list_inspections(include_translate=True)
               if r["id"] == res["id"])
    assert row["cloned_from"]["id"] == src
    assert row["filename"] == "AvoDerm_Master.png"


def test_clone_refuses_source_without_doc_a_zones(store):
    src = _job(zones=(ZB,))
    with pytest.raises(ValueError):
        pipeline.clone_inspection(src, owner=BOB_O)
    assert len(os.listdir(config.INSPECTIONS_DIR)) == 1   # ไม่ทิ้งงานครึ่ง ๆ


def test_clone_bad_or_missing_id(store):
    with pytest.raises(ValueError):
        pipeline.clone_inspection("../etc", owner=BOB_O)
    with pytest.raises(FileNotFoundError):
        pipeline.clone_inspection("20260101-000000-abcdef", owner=BOB_O)


def test_sources_search_and_summary_only(store):
    a = _job(name="AvoDerm_Master.png")
    _job(name="Cosma_V12.png", owner=BOB_O, settings={"brand": "Cosma"})
    rows = report.list_sources(q="avoderm")
    assert [r["id"] for r in report.list_sources(q="COSMA")] != [a]
    assert [r["id"] for r in rows] == [a]
    r = rows[0]
    assert r["zones_a"] == 2 and r["owner"] == "alice" and r["verdict"] == "FAIL"
    # ข้อมูลสรุปเท่านั้น — ไม่มีผลตรวจ/ข้อความ/กรอบ
    assert not {"defects", "zones", "ocr", "rows"} & set(r)


# ── 4-5. HTTP + สิทธิ์ + ธง ───────────────────────────────────────────

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


def test_http_everyone_can_use_everyones_template(app, store):
    src = _job(owner=ALICE_O)
    c = _as(app, BOB)
    assert [s["id"] for s in c.get("/api/artwork/sources").get_json()
            ["sources"]] == [src]
    r = c.post("/api/artwork/clone", json={"source": src})
    assert r.status_code == 200
    new = r.get_json()["id"]
    assert c.get(f"/api/artwork/{new}/preview.png").status_code == 200
    # แต่ผลตรวจของงานต้นแบบ (ของ alice) bob ยังเปิดไม่ได้
    assert c.get(f"/api/artwork/{src}/report").status_code == 403
    # และงานใหม่เป็นของ bob — alice เปิดไม่ได้
    assert _as(app, ALICE).get(f"/api/artwork/{new}/preview.png").status_code == 403


def test_http_share_off_follows_history_rules(app, store, monkeypatch):
    monkeypatch.setattr(config, "CLONE_SHARE_ALL", False)
    src = _job(owner=ALICE_O)
    c = _as(app, BOB)
    assert c.get("/api/artwork/sources").get_json()["sources"] == []
    assert c.post("/api/artwork/clone",
                  json={"source": src}).status_code == 403
    assert _as(app, ADMIN).post("/api/artwork/clone",
                                json={"source": src}).status_code == 200


def test_http_clone_errors(app, store):
    c = _as(app, ALICE)
    assert c.post("/api/artwork/clone", json={"source": "x"}).status_code == 400
    assert c.post("/api/artwork/clone",
                  json={"source": "20260101-000000-abcdef"}).status_code == 404


def test_http_inspect_saves_what_user_drew(app, store, monkeypatch):
    rid = _job(with_report=False, with_tr=False)
    os.remove(os.path.join(report.inspection_dir(rid), "setup.json"))
    monkeypatch.setattr(pipeline, "run_inspection",
                        lambda *a, **k: {"id": rid, "zones": []})
    body = {"zones": [ZA, ZB], "brand": "B1", "page_rot": 270,
            "auto_rotate": True, "pixel_check": True}
    assert _as(app, ALICE).post(f"/api/artwork/{rid}/inspect",
                                json=body).status_code == 200
    s = report.load_setup(rid)
    assert [z["rotate"] for z in s["zones"]] == [90, "default"]
    assert s["page_rot"] == 270 and s["brand"] == "B1" and s["by"] == "alice"


def test_http_translate_only_saves_setup(app, store, monkeypatch):
    rid = _job(with_report=False, with_tr=False)
    os.remove(os.path.join(report.inspection_dir(rid), "setup.json"))
    monkeypatch.setattr(pipeline, "run_ocr_only",
                        lambda rec_id, zl, **k: (zl, []))
    monkeypatch.setattr(translate, "translate_table",
                        lambda d, rows: {"rows": rows, "translated": False})
    r = _as(app, ALICE).post(f"/api/artwork/{rid}/translate",
                             json={"zones": [ZA], "page_rot": 90})
    assert r.status_code == 200
    s = report.load_setup(rid)
    assert s["zones"][0]["rotate"] == 90 and s["page_rot"] == 90


def test_http_flag_off_is_old_behaviour(app, store, monkeypatch):
    monkeypatch.setattr(config, "CLONE_FROM_HISTORY", False)
    rid = _job(with_report=False, with_tr=False)
    os.remove(os.path.join(report.inspection_dir(rid), "setup.json"))
    monkeypatch.setattr(pipeline, "run_inspection",
                        lambda *a, **k: {"id": rid, "zones": []})
    c = _as(app, ALICE)
    c.post(f"/api/artwork/{rid}/inspect", json={"zones": [ZA]})
    assert not os.path.exists(os.path.join(report.inspection_dir(rid),
                                           "setup.json"))
    assert c.get("/api/artwork/sources").status_code == 404
    assert c.post("/api/artwork/clone", json={"source": rid}).status_code == 404


# ── หน้าเว็บ ─────────────────────────────────────────────────────────

def _read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8") as f:
        return f.read()


def test_restore_sets_the_variable_that_is_actually_sent():
    """บั๊กเดิม: กู้คืนแล้วติ๊กช่อง auto-rotate บนจอ แต่ตัวแปรที่ส่งไปตรวจ
    ยังเป็น false (ตั้ง .checked ไม่ยิง change)."""
    js = _read("static/js/artwork_check.js")
    a = js.index("function restoreSession(s)")
    body = js[a:js.index("async function offerRestore()")]
    assert "autoRotate = !!s.autoRotate;" in body


def test_clone_param_is_removed_before_cloning():
    """รีเฟรชหน้าที่ยังมี ?clone= = ได้งานใหม่ซ้ำทุกครั้ง."""
    js = _read("static/js/artwork_check.js")
    a = js.index('get("clone")')
    seg = js[a:a + 400]
    assert seg.index("history.replaceState") < seg.index("cloneFrom(cloneParam)")


def test_translate_request_carries_page_rot():
    js = _read("static/js/artwork_check.js")
    a = js.index('"/translate", {')
    # 29 ก.ย.: มุมจอแยกต่อไฟล์ ⇒ ส่งผ่าน pageRotBody() (page_rot = 🅰)
    assert "pageRotBody()" in js[a:a + 500]


def test_ui_elements_exist_and_are_flagged():
    js = _read("static/js/artwork_check.js")
    main = _read("templates/artwork_check.html")
    hist = _read("templates/artwork_check_history.html")
    for i in ("awCloneBox", "awCloneQ", "awCloneSearch", "awCloneList"):
        assert 'id="%s"' % i in main and '$("%s")' % i in js
    assert "{% if clone_ui %}" in main and "{% if clone_ui %}" in hist
    assert 'id="awCloneBtn"' in hist
    # clone code ต้องอยู่หลัง guard ของหน้าประวัติ (ใช้ element ของหน้าตรวจ)
    assert js.index('if (!$("awFile")) return;') < js.index("function cloneFrom(")


def test_clone_failure_leaves_no_half_job(store, monkeypatch):
    """คัดลอกไฟล์ล้มกลางทาง ⇒ ต้องไม่เหลือโฟลเดอร์งานใหม่ที่ใช้ไม่ได้ค้าง."""
    import shutil
    src = _job()
    real = shutil.copy2
    calls = {"n": 0}

    def flaky(a, b, *k, **kw):
        calls["n"] += 1
        if calls["n"] == 2:                     # ล้มตอนคัดลอก preview
            raise OSError("disk full")
        return real(a, b, *k, **kw)

    monkeypatch.setattr(shutil, "copy2", flaky)
    with pytest.raises(OSError):
        pipeline.clone_inspection(src, owner=BOB_O)
    assert os.listdir(config.INSPECTIONS_DIR) == [src]


def test_unused_clone_is_not_listed_as_a_template(store):
    """เปิดจากต้นแบบแล้วเปลี่ยนใจ ⇒ ต้องไม่กลายเป็นต้นแบบซ้ำในรายการ."""
    src = _job()
    res = pipeline.clone_inspection(src, owner=BOB_O)
    assert [r["id"] for r in report.list_sources()] == [src]
    report.record_translation(res["id"], {"rows": [], "translated": False})
    # สองงานเกิดในวินาทีเดียวกัน ⇒ ลำดับขึ้นกับ hex สุ่มท้าย id — เทียบเป็นชุด
    got = {r["id"]: r for r in report.list_sources()}
    assert set(got) == {res["id"], src}
    assert got[res["id"]]["cloned_from"] == src
