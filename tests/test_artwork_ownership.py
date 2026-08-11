"""ประวัติการตรวจ Artwork: ใครเห็น/เปิด/ลบของใครได้บ้าง.

ครอบคลุมทั้งชั้นนโยบาย (ownership) ชั้นเก็บข้อมูล (report) และชั้น HTTP
(before_request guard ของ blueprint) เพราะช่องโหว่ที่อันตรายที่สุดคือ
"กรองรายการแล้วแต่ลืมกัน endpoint รายละเอียด" — ต้องมีเทสต์ยิงตรงเข้า URL.
"""

import json
import os

import pytest
from flask import Flask, g

from artwork_check import config, ownership, report
from artwork_check.routes import artwork_bp

ALICE = {"sub": "1", "username": "alice", "role": "Staff", "perms": []}
BOB = {"sub": "2", "username": "bob", "role": "Staff", "perms": []}
ADMIN = {"sub": "9", "username": "root", "role": "Admin", "perms": []}


@pytest.fixture
def store(tmp_path, monkeypatch):
    """คลังประวัติชั่วคราว — ไม่แตะ data/ จริง."""
    d = tmp_path / "inspections"
    d.mkdir()
    monkeypatch.setattr(config, "INSPECTIONS_DIR", str(d))
    monkeypatch.setattr(config, "HISTORY_PER_USER", True)
    monkeypatch.setattr(config, "HISTORY_ADMIN_ROLES", ("Admin",))
    return d


def _make(store, rec_id, owner=None, verdict="PASS", filename="a.pdf"):
    """สร้างบันทึกการตรวจ 1 รายการ (มี report.json, มี/ไม่มี owner.json)."""
    rec = store / rec_id
    rec.mkdir()
    (rec / "report.json").write_text(json.dumps({
        "id": rec_id, "created_at": "2026-08-11 09:00:00",
        "filename": filename, "brand": "", "verdict": verdict, "defects": [],
    }), encoding="utf-8")
    if owner:
        report.save_owner(rec_id, owner)
    return rec_id


# ── ชั้นนโยบาย ────────────────────────────────────────────────────────

def test_owner_sees_own_record(store):
    owner = {"user_id": "1", "username": "alice"}
    assert ownership.can_access(owner, ALICE) is True


def test_other_user_denied(store):
    owner = {"user_id": "1", "username": "alice"}
    assert ownership.can_access(owner, BOB) is False


def test_admin_sees_everything(store):
    assert ownership.can_access({"user_id": "1"}, ADMIN) is True
    assert ownership.can_access(None, ADMIN) is True          # บันทึกเก่า


def test_legacy_record_hidden_from_normal_user(store):
    """บันทึกเก่าที่ไม่มี owner.json = admin เท่านั้น (การตัดสินใจของผู้ใช้)."""
    assert ownership.can_access(None, ALICE) is False


def test_no_login_system_sees_everything(store):
    """AUTH_ENABLED=False → viewer เป็น None → ต้องไม่กรองอะไรเลย
    ไม่งั้นหน้าประวัติจะว่างเปล่าทั้งที่ระบบทำงานปกติ."""
    assert ownership.can_access(None, None) is True
    assert ownership.can_access({"user_id": "1"}, None) is True
    assert ownership.scope_of(None) == "all"
    assert ownership.make_filter(None) is None


def test_feature_flag_off_restores_old_behaviour(store, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_PER_USER", False)
    assert ownership.can_access({"user_id": "1"}, BOB) is True
    assert ownership.can_access(None, BOB) is True
    assert ownership.scope_of(BOB) == "all"


def test_empty_ids_never_match(store):
    """user_id ว่างต้องไม่ทำให้ทุกคนกลายเป็นเจ้าของ."""
    assert ownership.can_access({"user_id": ""}, {"sub": "", "role": "Staff"}) is False
    assert ownership.can_access({"user_id": ""}, ALICE) is False


def test_unknown_viewer_has_no_rights(store):
    """viewer = {} (ล็อกอินเปิดแต่หาผู้ใช้ไม่เจอ) ต้องไม่ใช่ 'ไม่กรอง'."""
    assert ownership.can_access({"user_id": "1"}, {}) is False
    assert ownership.can_access(None, {}) is False


def test_admin_role_list_is_configurable(store, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_ADMIN_ROLES", ("Admin", "Manager"))
    assert ownership.is_admin({"role": "Manager"}) is True
    assert ownership.is_admin({"role": "Staff"}) is False


# ── ชั้นเก็บข้อมูล ────────────────────────────────────────────────────

def test_save_and_load_owner(store):
    rec_id = _make(store, "20260811-090000-aaaaaa",
                   owner={"user_id": "1", "username": "alice"})
    got = report.load_owner(rec_id)
    assert got["user_id"] == "1" and got["username"] == "alice"


def test_load_owner_missing_returns_none(store):
    rec_id = _make(store, "20260811-090001-bbbbbb")
    assert report.load_owner(rec_id) is None


def test_list_filters_by_owner(store):
    _make(store, "20260811-090002-aaaaaa", {"user_id": "1", "username": "alice"})
    _make(store, "20260811-090003-bbbbbb", {"user_id": "2", "username": "bob"})
    _make(store, "20260811-090004-cccccc")                    # เก่า ไม่มีเจ้าของ

    mine = report.list_inspections(can_view=ownership.make_filter(ALICE))
    assert [r["id"] for r in mine] == ["20260811-090002-aaaaaa"]
    assert mine[0]["owner"] == "alice"

    all_rows = report.list_inspections(can_view=ownership.make_filter(ADMIN))
    assert len(all_rows) == 3


def test_list_without_filter_is_unchanged(store):
    """เส้นทางเดิม (ไม่กรอง) ต้องคืนทุกบันทึกเหมือนก่อนมีฟีเจอร์นี้."""
    _make(store, "20260811-090005-aaaaaa", {"user_id": "1", "username": "alice"})
    _make(store, "20260811-090006-bbbbbb")
    rows = report.list_inspections()
    assert len(rows) == 2
    assert {"id", "created_at", "filename", "brand", "verdict",
            "defect_count", "owner"} == set(rows[0])


def test_list_limit_still_applies_when_filtering(store):
    for i in range(5):
        _make(store, f"20260811-0901{i:02d}-aaaaaa",
              {"user_id": "1", "username": "alice"})
    rows = report.list_inspections(limit=2, can_view=ownership.make_filter(ALICE))
    assert len(rows) == 2


# ── ชั้น HTTP: ด่าน before_request ────────────────────────────────────

@pytest.fixture
def app(store):
    app = Flask(__name__, template_folder=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"))
    app.register_blueprint(artwork_bp)
    app.config["viewer"] = None

    @app.before_request
    def _fake_auth():                      # จำลองสิ่งที่ auth.access ทำให้
        v = app.config["viewer"]
        g.auth_enabled = v is not None
        g.current_user = v

    return app


def _as(app, viewer):
    """เปิด test client ในนามผู้ใช้คนนั้น (None = ไม่มีระบบล็อกอิน)."""
    app.config["viewer"] = viewer
    return app.test_client()


def test_http_owner_can_read_own_report(app, store):
    rec = _make(store, "20260811-090200-aaaaaa",
                {"user_id": "1", "username": "alice"})
    r = _as(app, ALICE).get(f"/api/artwork/{rec}/report")
    assert r.status_code == 200


def test_http_other_user_blocked_on_report(app, store):
    """ยิงตรงเข้า URL ด้วย id ที่รู้ — ต้องโดนปฏิเสธ ไม่ใช่แค่ไม่เห็นในรายการ."""
    rec = _make(store, "20260811-090201-aaaaaa",
                {"user_id": "1", "username": "alice"})
    r = _as(app, BOB).get(f"/api/artwork/{rec}/report")
    assert r.status_code == 403
    assert "ผู้ใช้อื่น" in r.get_json()["error"]


def test_http_other_user_blocked_on_every_rec_route(app, store):
    """**ทุก** endpoint ที่มี <rec_id> ต้องโดนด่านเดียวกัน.

    ไล่จาก ``url_map`` จริงแทนการเขียนลิสต์เอง → route ที่เพิ่มในอนาคตจะถูก
    ตรวจอัตโนมัติ ถ้าใครเพิ่ม endpoint ที่หลุดด่าน เทสต์นี้จะแดงทันที.
    """
    rec = _make(store, "20260811-090202-aaaaaa",
                {"user_id": "1", "username": "alice"})
    c = _as(app, BOB)
    checked = 0
    for rule in app.url_map.iter_rules():
        if "rec_id" not in rule.arguments:
            continue
        methods = sorted(rule.methods - {"HEAD", "OPTIONS"})
        path = rule.build({"rec_id": rec}, append_unknown=False)[1]
        for m in methods:
            resp = c.open(path, method=m)
            assert resp.status_code == 403, \
                f"{m} {path} ไม่ได้ถูกกัน (ได้ {resp.status_code})"
            checked += 1
    # กันเทสต์ผ่านแบบว่างเปล่าถ้า url_map เปลี่ยนรูปแบบพารามิเตอร์
    assert checked >= 10, f"ตรวจได้แค่ {checked} endpoint — น้อยผิดปกติ"


def test_http_delete_blocked_leaves_record_on_disk(app, store):
    rec = _make(store, "20260811-090203-aaaaaa",
                {"user_id": "1", "username": "alice"})
    _as(app, BOB).delete(f"/api/artwork/{rec}")
    assert (store / rec).exists()


def test_http_owner_can_delete_own(app, store):
    rec = _make(store, "20260811-090204-aaaaaa",
                {"user_id": "1", "username": "alice"})
    r = _as(app, ALICE).delete(f"/api/artwork/{rec}")
    assert r.status_code == 200 and not (store / rec).exists()


def test_http_admin_can_open_legacy_record(app, store):
    rec = _make(store, "20260811-090205-cccccc")
    assert _as(app, ADMIN).get(f"/api/artwork/{rec}/report").status_code == 200
    assert _as(app, ALICE).get(f"/api/artwork/{rec}/report").status_code == 403


def test_http_no_auth_everything_open(app, store):
    """โหมดไม่มีล็อกอิน = พฤติกรรมเดิมทุกประการ."""
    rec = _make(store, "20260811-090206-aaaaaa",
                {"user_id": "1", "username": "alice"})
    c = _as(app, None)
    assert c.get(f"/api/artwork/{rec}/report").status_code == 200
    body = c.get("/api/artwork/history").get_json()
    assert body["scope"] == "all"


def test_http_history_scope_and_filtering(app, store):
    _make(store, "20260811-090207-aaaaaa", {"user_id": "1", "username": "alice"})
    _make(store, "20260811-090208-bbbbbb", {"user_id": "2", "username": "bob"})

    body = _as(app, ALICE).get("/api/artwork/history").get_json()
    assert body["scope"] == "own" and body["username"] == "alice"
    assert [r["id"] for r in body["records"]] == ["20260811-090207-aaaaaa"]

    body = _as(app, ADMIN).get("/api/artwork/history").get_json()
    assert body["scope"] == "all" and len(body["records"]) == 2


def test_http_bad_id_still_400_not_403(app, store):
    """id ผิดรูปแบบต้องได้คำตอบเดิม (400) ไม่ใช่ 403 จากด่านใหม่."""
    r = _as(app, ALICE).get("/api/artwork/not-an-id/report")
    assert r.status_code == 400


def test_http_upload_records_owner_end_to_end(app, store):
    """เส้นทางจริง: อัปโหลด → ต้องได้ owner.json ทันที และเจ้าของต้องทำงาน
    ต่อได้เลย. ถ้าพลาดข้อนี้ ผู้ใช้จะมองไม่เห็นงานของตัวเองที่เพิ่งอัปโหลด
    (failure mode ที่แย่ที่สุดของฟีเจอร์นี้)."""
    import io

    import cv2
    import numpy as np

    img = np.full((240, 360, 3), 245, np.uint8)
    cv2.rectangle(img, (40, 40), (300, 200), (40, 40, 40), 2)
    cv2.putText(img, "HIDDEN BAY", (60, 120), cv2.FONT_HERSHEY_SIMPLEX,
                0.8, (20, 20, 20), 2)
    ok, buf = cv2.imencode(".png", img)
    assert ok

    r = _as(app, ALICE).post(
        "/api/artwork/upload",
        data={"file": (io.BytesIO(buf.tobytes()), "artwork.png")},
        content_type="multipart/form-data")
    assert r.status_code == 200
    rec = r.get_json()["id"]

    owner = report.load_owner(rec)
    assert owner["user_id"] == "1" and owner["username"] == "alice"

    assert _as(app, ALICE).get(
        f"/api/artwork/{rec}/preview.png").status_code == 200
    assert _as(app, BOB).get(
        f"/api/artwork/{rec}/preview.png").status_code == 403


def test_http_upload_without_login_has_no_owner(app, store):
    """ไม่มีระบบล็อกอิน = ไม่เขียน owner.json และทุกคนยังเปิดได้เหมือนเดิม."""
    import io

    import cv2
    import numpy as np

    ok, buf = cv2.imencode(".png", np.full((120, 200, 3), 250, np.uint8))
    r = _as(app, None).post(
        "/api/artwork/upload",
        data={"file": (io.BytesIO(buf.tobytes()), "artwork.png")},
        content_type="multipart/form-data")
    assert r.status_code == 200
    rec = r.get_json()["id"]
    assert report.load_owner(rec) is None
    assert not (store / rec / "owner.json").exists()


def test_http_flag_off_restores_shared_history(app, store, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_PER_USER", False)
    rec = _make(store, "20260811-090209-aaaaaa",
                {"user_id": "1", "username": "alice"})
    c = _as(app, BOB)
    assert c.get(f"/api/artwork/{rec}/report").status_code == 200
    assert c.get("/api/artwork/history").get_json()["scope"] == "all"
