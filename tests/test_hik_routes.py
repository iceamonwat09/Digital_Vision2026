"""
เทสต์ระดับ HTTP ของแท็บ "กล้องอุตสาหกรรม" — ยิงเข้า route จริงของ `app.py`
ด้วย Flask test client + **MVS SDK ปลอม**.

จุดประสงค์หลักคือพิสูจน์ 2 อย่างที่เทสต์ระดับคลาสพิสูจน์แทนไม่ได้:
  1. `camera_index` ที่ขึ้นต้นด้วย "hik:" ถูกส่งไปสร้าง ``HikCamera`` จริง
     และ **เส้นทาง USB/RTSP/STREAM ยังเป็นของเดิมทุกประการ**
  2. endpoint ของโหมดอื่น (เช่น /api/camera/control ของสไลเดอร์กล้อง USB)
     **ไม่ไปแตะกล้องอุตสาหกรรม** แม้กล้องนั้นกำลังทำงานอยู่
"""

import importlib.util
import json
import os
import pathlib
import time
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE_SDK = os.path.join(ROOT, "tests", "fake_mvs")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

pytest.importorskip("numpy")
pytest.importorskip("cv2")
pytest.importorskip("flask")


def _stub_if_missing(name, module):
    """ใส่ stub เฉพาะเมื่อเครื่องนั้น *ไม่มี* ของจริง — บนสถานีจะใช้ของจริงเสมอ."""
    if importlib.util.find_spec(name) is None:
        sys.modules.setdefault(name, module)


_ul = types.ModuleType("ultralytics")


class _StubYOLO:                                     # pragma: no cover
    def __init__(self, *a, **k):
        raise RuntimeError("stub ultralytics")


_ul.YOLO = _StubYOLO
_stub_if_missing("ultralytics", _ul)

_pyodbc = types.ModuleType("pyodbc")
_pyodbc.Error = type("Error", (Exception,), {})
_pyodbc.connect = lambda *a, **k: (_ for _ in ()).throw(_pyodbc.Error("stub"))
_stub_if_missing("pyodbc", _pyodbc)

os.environ.setdefault("AUTH_ENABLED", "0")

# บาง container มี PyJWT/cryptography ของ distro ที่ import แล้ว "panic" (BaseException)
# ซึ่งหลุดจาก try/except ของ app.py — ตรวจก่อนแล้วใส่ stub ให้เฉพาะเครื่องแบบนั้น
# (บนสถานีที่ของจริงใช้ได้ จะไม่มีการแทนที่ใด ๆ)
try:
    import jwt as _jwt_probe                          # noqa: F401
except BaseException:                                 # noqa: BLE001
    _jwt_stub = types.ModuleType("jwt")
    _jwt_stub.PyJWTError = type("PyJWTError", (Exception,), {})
    _jwt_stub.ExpiredSignatureError = type("ExpiredSignatureError", (_jwt_stub.PyJWTError,), {})
    _jwt_stub.InvalidTokenError = type("InvalidTokenError", (_jwt_stub.PyJWTError,), {})
    _jwt_stub.encode = lambda *a, **k: ""
    _jwt_stub.decode = lambda *a, **k: {}
    sys.modules["jwt"] = _jwt_stub

try:
    import app as appmod
except BaseException as e:                            # noqa: BLE001 — รวม PanicException
    pytest.skip("นำเข้า app.py ไม่ได้บนเครื่องนี้: %s" % e, allow_module_level=True)

import hik_camera as hc                               # noqa: E402


@pytest.fixture
def client(monkeypatch, tmp_path):
    if FAKE_SDK not in sys.path:
        sys.path.insert(0, FAKE_SDK)
    import MvCameraControl_class as fake
    fake.SIM.update({"accessible": True, "open_ok": True, "packet_size": 1500,
                     "optimal_packet_size": 8164, "lost_packets": 0, "lost_frames": 0,
                     "drop_every": 0, "gray_level": 120, "max_packet_size": 9000,
                     "missing_nodes": [], "grab_fail": 0})
    hc._sdk_cache["mod"] = None
    hc._sdk_cache["info"] = None
    monkeypatch.setattr(hc.config, "HIK_SDK_PATH", FAKE_SDK, raising=False)
    monkeypatch.setattr(hc.config, "HIK_SETTINGS_FILE",
                        str(tmp_path / "hik.json"), raising=False)
    monkeypatch.setattr(hc.config, "HIK_DATASET_DIR", str(tmp_path / "ds"), raising=False)
    appmod.app.config["TESTING"] = True
    with appmod.app.test_client() as c:
        yield c
    # กันสถานะรั่วไปเทสต์ถัดไป (กล้องต้องถูกปล่อยเสมอ)
    try:
        c.post("/api/detection/stop")
    except Exception:
        pass
    hc._sdk_cache["mod"] = None
    hc._sdk_cache["info"] = None


def test_scan_endpoint_lists_the_camera(client):
    d = client.get("/api/camera/hik/scan").get_json()
    assert d["status"] == "ok"
    assert d["sdk"]["available"] is True
    assert d["devices"][0]["serial"] == "DA4994130"
    assert d["devices"][0]["source"] == "hik:DA4994130"


def test_params_endpoint_probes_without_streaming(client):
    d = client.get("/api/camera/hik/params").get_json()
    assert d["status"] == "ok" and d["live"] is False
    assert d["identity"]["model"] == "MV-CS050-10GC"
    assert d["params"]["exposure_us"]["min"] == pytest.approx(15.0)


def test_params_post_persists_even_when_camera_is_not_running(client):
    r = client.post("/api/camera/hik/params", json={"params": {"exposure_us": 1234}})
    d = r.get_json()
    assert d["status"] == "ok" and d["live"] is False and d["saved"] is True
    assert hc.load_settings()["exposure_us"] == 1234


def test_params_post_rejects_bad_payload(client):
    assert client.post("/api/camera/hik/params", json={}).status_code == 400
    assert client.post("/api/camera/hik/params",
                       json={"params": "ไม่ใช่ object"}).status_code == 400


def test_hik_source_starts_a_hik_camera_then_stops_cleanly(client):
    r = client.post("/api/detection/start", json={"camera_index": "hik:DA4994130"})
    assert r.status_code == 200, r.get_json()
    try:
        assert isinstance(appmod.camera, hc.HikCamera)
        assert appmod.camera.is_initialized
        st = client.get("/api/camera/hik/status").get_json()
        assert st["active"] is True
        assert st["identity"]["serial"] == "DA4994130"
    finally:
        client.post("/api/detection/stop")
    assert appmod.camera is None
    assert client.get("/api/camera/hik/status").get_json()["active"] is False


def test_usb_path_is_untouched_by_the_new_branch(client, monkeypatch):
    """
    ค่า camera_index ที่ไม่ได้ขึ้นต้นด้วย "hik:" ต้องไปทาง Camera เดิมเป๊ะ
    (ยืนยันด้วยกล้องปลอมที่เปิดไม่สำเร็จ — ข้อความ error ต้องเป็นของเส้นทางเดิม)
    """
    opened = {}

    class _FailingCamera:
        def __init__(self, camera_index=None, **kw):
            opened["index"] = camera_index

        def initialize(self):
            return False

    monkeypatch.setattr(appmod, "Camera", _FailingCamera)
    monkeypatch.setattr(appmod, "scan_cameras_fast", lambda *a, **k: [])
    r = client.post("/api/detection/start", json={"camera_index": 0})
    assert r.status_code == 500
    assert "Cannot open camera 0" in r.get_json()["message"]
    assert opened["index"] == 0                       # ไปทาง Camera เดิมจริง


def test_usb_brightness_slider_never_touches_the_hik_camera(client):
    """
    /api/camera/control กรองด้วย hasattr(cam, "set_control") — ต้องไม่มีทางไปสั่ง
    กล้องอุตสาหกรรม ไม่งั้นสไลเดอร์ของโหมด USB จะเปลี่ยนภาพของอีกโหมดโดยไม่ตั้งใจ.
    """
    client.post("/api/detection/start", json={"camera_index": "hik:DA4994130"})
    try:
        r = client.post("/api/camera/control", json={"control": "brightness", "value": 120})
        assert r.status_code == 409                   # ไม่มีกล้องที่รับ control นี้
    finally:
        client.post("/api/detection/stop")


def test_shot_and_dataset_require_a_running_camera(client):
    assert client.post("/api/camera/hik/dataset",
                       json={"enabled": True}).status_code == 409
    r = client.post("/api/camera/hik/shot", json={})
    # ไม่มีกล้อง = 409 · ไม่มีโมเดล = 400 — ทั้งคู่ต้อง "บอกเหตุผล" ไม่ใช่ 500
    assert r.status_code in (400, 409)
    assert r.get_json()["message"]


def test_dataset_toggle_while_running(client):
    client.post("/api/detection/start", json={"camera_index": "hik:DA4994130"})
    try:
        on = client.post("/api/camera/hik/dataset", json={"enabled": True}).get_json()
        assert on["dataset"]["active"] is True
        off = client.post("/api/camera/hik/dataset", json={"enabled": False}).get_json()
        assert off["dataset"]["active"] is False
    finally:
        client.post("/api/detection/stop")


def test_live_params_apply_while_streaming(client):
    client.post("/api/detection/start", json={"camera_index": "hik:DA4994130"})
    try:
        d = client.post("/api/camera/hik/params",
                        json={"params": {"exposure_us": 999}}).get_json()
        assert d["live"] is True
        assert d["applied"]["exposure_us"]["value"] == pytest.approx(999.0)
        assert d["params"]["exposure_us"]["value"] == pytest.approx(999.0)
        assert d["stats"]["frames"] >= 0
    finally:
        client.post("/api/detection/stop")


# ════════════════════════════════════════════════════════════════════
# โหมด "ถ่ายรัว" (burst) — ระดับ HTTP
# ════════════════════════════════════════════════════════════════════
import hik_burst as hbmod                             # noqa: E402


@pytest.fixture
def bclient(client, monkeypatch, tmp_path):
    """client เดิม + โฟลเดอร์ burst ชั่วคราว (ห้ามแตะ data/ ของเครื่องจริง)."""
    root = tmp_path / "burst"
    root.mkdir()
    monkeypatch.setattr(hbmod.config, "HIK_BURST_DIR", str(root), raising=False)
    monkeypatch.setattr(appmod.config, "HIK_BURST_DIR", str(root), raising=False)
    hbmod._job = None
    appmod._hik_burst_session = None
    yield client
    appmod._hik_burst_session = None


def _start_hik(c):
    return c.post("/api/detection/start", json={"camera_index": "hik:DA4994130"})


def test_burst_needs_the_camera_running(bclient):
    r = bclient.post("/api/camera/hik/burst", json={"seconds": 2})
    assert r.status_code == 409
    assert "Start Detection" in r.get_json()["message"]


def test_burst_records_frames_and_camera_settings(bclient):
    assert _start_hik(bclient).status_code == 200
    try:
        r = bclient.post("/api/camera/hik/burst", json={"seconds": 2})
        assert r.status_code == 200, r.get_json()
        name = r.get_json()["session"]
        assert name

        time.sleep(1.2)
        st = bclient.get("/api/camera/hik/burst").get_json()
        assert st["status"] == "ok"

        bclient.delete("/api/camera/hik/burst")
        meta = json.loads((pathlib.Path(hbmod.burst_root()) / name / "meta.json")
                          .read_text(encoding="utf-8"))
        # ⚠️ ถ้าไม่มี exposure ใน meta ภาพชุดนี้เทียบข้ามรอบไม่ได้เลย
        assert "exposure_us" in meta
        assert meta["seconds"] == 2
        assert isinstance(meta.get("frame_ts"), list)
        assert len(meta["frame_ts"]) >= meta["saved"]
    finally:
        bclient.post("/api/detection/stop")


def test_two_bursts_cannot_run_at_once(bclient):
    _start_hik(bclient)
    try:
        assert bclient.post("/api/camera/hik/burst", json={"seconds": 5}).status_code == 200
        assert bclient.post("/api/camera/hik/burst", json={"seconds": 5}).status_code == 409
    finally:
        bclient.delete("/api/camera/hik/burst")
        bclient.post("/api/detection/stop")


def test_dataset_toggle_is_refused_while_a_burst_runs(bclient):
    """ตัวเขียนไฟล์มีตัวเดียว — ถ้าเปิดซ้อนได้ ชุดที่กำลังถ่ายจะถูกตัดจบเงียบ ๆ."""
    _start_hik(bclient)
    try:
        bclient.post("/api/camera/hik/burst", json={"seconds": 5})
        r = bclient.post("/api/camera/hik/dataset", json={"enabled": True})
        assert r.status_code == 409
        assert "ถ่ายรัว" in r.get_json()["message"]
    finally:
        bclient.delete("/api/camera/hik/burst")
        bclient.post("/api/detection/stop")


def test_burst_is_refused_while_the_dataset_is_recording(bclient):
    _start_hik(bclient)
    try:
        bclient.post("/api/camera/hik/dataset", json={"enabled": True})
        r = bclient.post("/api/camera/hik/burst", json={"seconds": 5})
        assert r.status_code == 409
    finally:
        bclient.post("/api/camera/hik/dataset", json={"enabled": False})
        bclient.post("/api/detection/stop")


def test_seconds_are_clamped_to_the_configured_ceiling(bclient):
    _start_hik(bclient)
    try:
        d = bclient.post("/api/camera/hik/burst", json={"seconds": 9999}).get_json()
        assert d["seconds"] == appmod.config.HIK_BURST_MAX_SECONDS
    finally:
        bclient.delete("/api/camera/hik/burst")
        bclient.post("/api/detection/stop")


def _make_session(name="20260820_101010", frames=3):
    root = pathlib.Path(hbmod.burst_root())
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    import numpy as np
    import cv2
    for i in range(frames):
        img = np.full((60, 80, 3), 40 + i * 30, np.uint8)
        cv2.imwrite(str(d / ("%05d.jpg" % (i + 1))), img)
    (d / "meta.json").write_text(json.dumps(
        {"started_at": "2026-08-20 10:10:10", "exposure_us": 2000.0,
         "saved": frames, "dropped": 0}), encoding="utf-8")
    return name


def test_listing_reports_sessions_and_disk(bclient):
    _make_session()
    d = bclient.get("/api/camera/hik/bursts").get_json()
    assert d["status"] == "ok"
    assert d["sessions"][0]["frames"] == 3
    assert d["free_mb"] is None or d["free_mb"] > 0


def test_detail_lists_frames_before_metrics_exist(bclient):
    name = _make_session()
    d = bclient.get("/api/camera/hik/bursts/%s" % name).get_json()
    assert d["total"] == 3 and d["metrics_ready"] is False


@pytest.mark.parametrize("bad", ["..", "%2e%2e", "nope", "a%2Fb"])
def test_unknown_or_unsafe_session_names_are_refused(bclient, bad):
    assert bclient.get("/api/camera/hik/bursts/%s" % bad).status_code in (404, 405)


def test_frame_route_refuses_paths_outside_the_session(bclient):
    name = _make_session()
    assert bclient.get("/api/camera/hik/bursts/%s/frame/meta.json" % name).status_code == 404
    assert bclient.get("/api/camera/hik/bursts/%s/thumb/meta.json" % name).status_code == 404


def test_thumb_is_generated_on_demand(bclient):
    name = _make_session()
    r = bclient.get("/api/camera/hik/bursts/%s/thumb/00001.jpg" % name)
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    assert (pathlib.Path(hbmod.burst_root()) / name / hbmod.THUMB_DIR / "00001.jpg").exists()


def test_frame_route_serves_the_untouched_original(bclient):
    name = _make_session()
    r = bclient.get("/api/camera/hik/bursts/%s/frame/00001.jpg" % name)
    assert r.status_code == 200
    raw = (pathlib.Path(hbmod.burst_root()) / name / "00001.jpg").read_bytes()
    assert r.data == raw


def test_delete_frames_and_then_the_session(bclient):
    name = _make_session()
    r = bclient.delete("/api/camera/hik/bursts/%s/frames" % name,
                       json={"files": ["00002.jpg", "../../evil"]})
    assert r.get_json()["removed"] == ["00002.jpg"]
    assert bclient.get("/api/camera/hik/bursts/%s" % name).get_json()["total"] == 2

    assert bclient.delete("/api/camera/hik/bursts/%s" % name).status_code == 200
    assert bclient.get("/api/camera/hik/bursts/%s" % name).status_code == 404


def test_cannot_delete_the_session_being_recorded(bclient):
    _start_hik(bclient)
    try:
        name = bclient.post("/api/camera/hik/burst", json={"seconds": 5}).get_json()["session"]
        assert bclient.delete("/api/camera/hik/bursts/%s" % name).status_code == 409
    finally:
        bclient.delete("/api/camera/hik/burst")
        bclient.post("/api/detection/stop")


def test_detect_without_a_model_is_refused(bclient):
    name = _make_session()
    r = bclient.post("/api/camera/hik/bursts/%s/detect" % name, json={"all": True})
    assert r.status_code == 400
    assert "โมเดล" in r.get_json()["message"]


def test_metrics_job_runs_and_reports_progress(bclient):
    name = _make_session(frames=4)
    r = bclient.post("/api/camera/hik/bursts/%s/metrics" % name)
    assert r.status_code == 200
    for _ in range(60):
        job = bclient.get("/api/camera/hik/burst").get_json()["job"]
        if job and not job["running"]:
            break
        time.sleep(0.05)
    assert bclient.get("/api/camera/hik/bursts/%s" % name).get_json()["metrics_ready"] is True


def test_deleting_a_session_mid_job_leaves_no_ghost_folder(bclient):
    """เจอจากการขับด้วยเบราว์เซอร์จริง: กดลบทั้งชุดขณะงานวัดผลยังวิ่งอยู่ แล้วงาน
    เขียน metrics.json/_thumbs กลับลงโฟลเดอร์ที่เพิ่งลบ ⇒ เหลือ "ซากชุด" ที่ไม่มี
    ภาพสักใบค้างในรายการตลอดไป."""
    name = _make_session(frames=6)
    assert bclient.post("/api/camera/hik/bursts/%s/metrics" % name).status_code == 200
    assert bclient.delete("/api/camera/hik/bursts/%s" % name).status_code == 200
    time.sleep(0.6)
    assert not (pathlib.Path(hbmod.burst_root()) / name).exists()
    assert bclient.get("/api/camera/hik/bursts").get_json()["sessions"] == []


def test_a_frame_deleted_mid_request_answers_404_not_500(bclient):
    """ผู้ใช้กดลบขณะภาพย่อกำลังทยอยโหลด = เรื่องปกติ ต้องไม่พ่น 500 + stack trace."""
    name = _make_session(frames=2)
    missing = pathlib.Path(hbmod.burst_root()) / name / "00001.jpg"
    r = bclient.get("/api/camera/hik/bursts/%s/thumb/00001.jpg" % name)
    assert r.status_code == 200
    missing.unlink()
    (pathlib.Path(hbmod.burst_root()) / name / hbmod.THUMB_DIR / "00001.jpg").unlink()
    for url in ("thumb", "frame"):
        r = bclient.get("/api/camera/hik/bursts/%s/%s/00001.jpg" % (name, url))
        assert r.status_code == 404, (url, r.status_code)


def test_send_jpeg_turns_a_vanished_file_into_404(bclient):
    with appmod.app.test_request_context():
        resp, code = appmod._send_jpeg("/definitely/not/here.jpg")
        assert code == 404
