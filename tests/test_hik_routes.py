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


# ════════════════════════════════════════════════════════════════════
# ②a เก็บหลักฐานว่า "เฟรมหายไปตรงไหน"
# ════════════════════════════════════════════════════════════════════
def test_burst_meta_records_everything_needed_to_diagnose(bclient):
    """ถ้าไม่เก็บตัวเลขพวกนี้ตอนถ่าย ทีหลังต้องมานั่งเดาว่าเฟรมหายเพราะอะไร"""
    assert _start_hik(bclient).status_code == 200
    try:
        name = bclient.post("/api/camera/hik/burst",
                            json={"seconds": 2}).get_json()["session"]
        time.sleep(1.2)
        bclient.delete("/api/camera/hik/burst")
        meta = json.loads((pathlib.Path(hbmod.burst_root()) / name / "meta.json")
                          .read_text(encoding="utf-8"))
        for key in ("diag_start", "diag_end", "stage_ms", "elapsed_s",
                    "framerate_enable", "framerate", "packet_size", "pixel_format"):
            assert key in meta, key
        # ตัวนับฝั่งกล้อง = ตัวที่แยก "หายระหว่างทาง" ออกจาก "ดิสก์ตามไม่ทัน"
        for key in ("cam_frames", "cam_dropped", "cam_timeouts"):
            assert key in meta["diag_start"], key
            assert key in meta["diag_end"], key
        assert isinstance(meta["diag_end"].get("net"), (dict, type(None)))
    finally:
        bclient.post("/api/detection/stop")


def test_burst_detail_endpoint_returns_a_diagnosis(bclient):
    assert _start_hik(bclient).status_code == 200
    try:
        name = bclient.post("/api/camera/hik/burst",
                            json={"seconds": 2}).get_json()["session"]
        time.sleep(1.2)
        bclient.delete("/api/camera/hik/burst")
        d = bclient.get("/api/camera/hik/bursts/%s" % name).get_json()
        assert d["diag"] is not None
        assert d["diag"]["cause"] in ("ok", "disk", "transport",
                                      "camera_rate", "framerate_cap")
    finally:
        bclient.post("/api/detection/stop")


# ════════════════════════════════════════════════════════════════════
# ②b หยุดโมเดลระหว่างถ่ายรัว — opt-in สองชั้น + คลายเองเสมอ
# ════════════════════════════════════════════════════════════════════
def test_inference_is_never_paused_by_default(bclient):
    assert appmod._inference_paused() is False


def test_request_alone_cannot_pause_when_the_config_flag_is_off(bclient, monkeypatch):
    """ชั้นที่ 1: ถ้า flag ของระบบปิด ต่อให้หน้าเว็บส่งมาก็ต้องไม่หยุดตรวจ"""
    monkeypatch.setattr(appmod.config, "HIK_BURST_PAUSE_INFERENCE", False, raising=False)
    _start_hik(bclient)
    try:
        d = bclient.post("/api/camera/hik/burst",
                         json={"seconds": 5, "pause_inference": True}).get_json()
        assert d["pause_inference"] is False
        assert appmod._inference_paused() is False
    finally:
        bclient.delete("/api/camera/hik/burst")
        bclient.post("/api/detection/stop")


def test_flag_alone_cannot_pause_without_the_request(bclient, monkeypatch):
    """ชั้นที่ 2: เปิด flag ไว้แล้วก็ยังต้องติ๊กต่อครั้ง — กันเผลอหยุดการตรวจ"""
    monkeypatch.setattr(appmod.config, "HIK_BURST_PAUSE_INFERENCE", True, raising=False)
    _start_hik(bclient)
    try:
        d = bclient.post("/api/camera/hik/burst", json={"seconds": 5}).get_json()
        assert d["pause_inference"] is False
        assert appmod._inference_paused() is False
    finally:
        bclient.delete("/api/camera/hik/burst")
        bclient.post("/api/detection/stop")


def test_both_layers_on_pauses_and_stopping_resumes(bclient, monkeypatch):
    monkeypatch.setattr(appmod.config, "HIK_BURST_PAUSE_INFERENCE", True, raising=False)
    _start_hik(bclient)
    try:
        d = bclient.post("/api/camera/hik/burst",
                         json={"seconds": 5, "pause_inference": True}).get_json()
        assert d["pause_inference"] is True
        assert appmod._inference_paused() is True
        st = bclient.get("/api/camera/hik/burst").get_json()
        assert st["paused_inference"] is True
        bclient.delete("/api/camera/hik/burst")
        assert appmod._inference_paused() is False     # คลายทันทีที่หยุดถ่าย
    finally:
        bclient.post("/api/detection/stop")


def test_pause_expires_by_itself_even_if_the_cleanup_path_never_runs(bclient, monkeypatch):
    """
    ตาข่ายนิรภัยที่สำคัญที่สุดของฟีเจอร์นี้: ถ้าเส้นทางปิดงานพังไปทั้งหมด
    (exception / กล้องหลุด / ปิดเบราว์เซอร์) ระบบต้อง **ไม่ค้างในสภาพไม่ตรวจ**
    """
    monkeypatch.setattr(appmod.config, "HIK_BURST_PAUSE_INFERENCE", True, raising=False)
    _start_hik(bclient)
    try:
        bclient.post("/api/camera/hik/burst",
                     json={"seconds": 5, "pause_inference": True})
        assert appmod._inference_paused() is True
        appmod._hik_burst_deadline = time.time() - 0.1     # จำลองว่าเลยเวลาไปแล้ว
        assert appmod._inference_paused() is False
    finally:
        bclient.delete("/api/camera/hik/burst")
        bclient.post("/api/detection/stop")


def test_listing_tells_the_page_whether_pausing_is_allowed(bclient, monkeypatch):
    monkeypatch.setattr(appmod.config, "HIK_BURST_PAUSE_INFERENCE", False, raising=False)
    assert bclient.get("/api/camera/hik/bursts").get_json()["can_pause_inference"] is False
    monkeypatch.setattr(appmod.config, "HIK_BURST_PAUSE_INFERENCE", True, raising=False)
    assert bclient.get("/api/camera/hik/bursts").get_json()["can_pause_inference"] is True


class _CountingDetector:
    """โมเดลปลอมที่นับว่าถูกเรียกกี่ครั้ง — ใช้ดูว่า 'หยุดตรวจ' หยุดจริงไหม"""

    def __init__(self):
        self.calls = 0
        self.model = object()

    def detect(self, frame, imgsz=None):
        self.calls += 1
        return []

    def draw_detections(self, frame, dets):            # pragma: no cover
        return frame

    def _class_names(self):                            # pragma: no cover
        return {}


def _wait_growth(det, seconds=0.8):
    """เพิ่มขึ้นกี่ครั้งในช่วงเวลาที่กำหนด"""
    before = det.calls
    time.sleep(seconds)
    return det.calls - before


def test_pausing_actually_stops_the_model_from_running(bclient, monkeypatch):
    """
    เทสต์ที่พิสูจน์ว่าฟีเจอร์ทำงานจริง ไม่ใช่แค่ตัวแปรเปลี่ยนค่า:
    นับจำนวนครั้งที่ `detector.detect` ถูกเรียก ก่อน/ระหว่าง/หลังการหยุด
    """
    monkeypatch.setattr(appmod.config, "HIK_BURST_PAUSE_INFERENCE", True, raising=False)
    fake = _CountingDetector()
    monkeypatch.setattr(appmod, "detector", fake, raising=False)
    assert _start_hik(bclient).status_code == 200
    try:
        assert _wait_growth(fake) > 0, "ปกติต้องมีการตรวจเกิดขึ้น"

        bclient.post("/api/camera/hik/burst",
                     json={"seconds": 6, "pause_inference": True})
        time.sleep(0.3)                                # ให้เธรดตรวจเห็นสถานะใหม่
        assert _wait_growth(fake) == 0, "ระหว่างหยุด ต้องไม่มีการตรวจเลย"

        bclient.delete("/api/camera/hik/burst")
        time.sleep(0.3)
        assert _wait_growth(fake) > 0, "หยุดถ่ายแล้วต้องกลับมาตรวจเอง"
    finally:
        bclient.post("/api/detection/stop")


def test_a_burst_without_pausing_leaves_the_model_running(bclient, monkeypatch):
    """พฤติกรรมเดิมต้องไม่เปลี่ยน — ถ่ายรัวแบบไม่ติ๊ก = ตรวจต่อตามปกติ"""
    monkeypatch.setattr(appmod.config, "HIK_BURST_PAUSE_INFERENCE", True, raising=False)
    fake = _CountingDetector()
    monkeypatch.setattr(appmod, "detector", fake, raising=False)
    _start_hik(bclient)
    try:
        bclient.post("/api/camera/hik/burst", json={"seconds": 6})
        time.sleep(0.3)
        assert _wait_growth(fake) > 0
    finally:
        bclient.delete("/api/camera/hik/burst")
        bclient.post("/api/detection/stop")


def test_paused_inference_clears_the_boxes_instead_of_leaving_stale_ones(bclient, monkeypatch):
    """
    กฎเหล็กข้อ 2: ระหว่างหยุดตรวจ ห้ามปล่อยกรอบเก่าค้างบนภาพใหม่
    (คนดูจะเชื่อว่ากรอบนั้นคือผลตรวจของเฟรมที่เห็นอยู่)
    """
    monkeypatch.setattr(appmod.config, "HIK_BURST_PAUSE_INFERENCE", True, raising=False)

    class _AlwaysNG(_CountingDetector):
        def detect(self, frame, imgsz=None):
            self.calls += 1
            return [{"class_name": "dent", "confidence": 0.9,
                     "bbox": [10, 10, 30, 30], "center": [20, 20]}]

    fake = _AlwaysNG()
    monkeypatch.setattr(appmod, "detector", fake, raising=False)
    _start_hik(bclient)
    try:
        time.sleep(0.6)
        assert appmod.latest_detections, "ก่อนหยุดต้องมีกรอบอยู่"
        bclient.post("/api/camera/hik/burst",
                     json={"seconds": 6, "pause_inference": True})
        time.sleep(0.6)
        assert appmod.latest_detections == []
        assert appmod.detection_stats["current_defects"] == 0
    finally:
        bclient.delete("/api/camera/hik/burst")
        bclient.post("/api/detection/stop")


# ── ภาพสด: โหมด smooth ต้องผูกกับ "แหล่งภาพ" ไม่ใช่ทั้งระบบ ──────────
# `generate_frames` โหมด LOCKED รีเฟรชจอตามอัตราการตรวจ — USB ไม่รู้สึกเพราะ
# ตรวจเร็วกว่าเพดานจอ แต่กล้องอุตสาหกรรมส่งเฟรมใหญ่กว่าหลายเท่าจนตรวจช้ากว่า
# ⇒ จอกระตุก. เปิด smooth เฉพาะแหล่งนี้ **ห้ามกระทบ USB/RTSP**
def _smooth_now():
    """เรียก **ฟังก์ชันตัวเดียวกับที่ generate_frames ใช้** — ไม่เขียนเงื่อนไขซ้ำ
    (เงื่อนไขที่คัดลอกมาไว้ในเทสต์จะเพี้ยนจากของจริงโดยไม่มีใครรู้)."""
    return appmod._live_smooth()


def test_smooth_live_defaults_to_locked_boxes(client):
    """
    ⚠️ **กรอบต้องล็อกกับกระป๋องเป็นค่าตั้งต้น** — เคยตั้ง default เป็น smooth
    (24 ส.ค. 2026) แล้วผู้ใช้เจอ "กรอบ Dent ตามภาพไม่ทัน" ทันที ซึ่งเป็นปัญหา
    ที่โปรเจกต์เคยตัดสินใจแก้ไปแล้ว. กรอบที่ชี้ผิดตำแหน่ง = ผลที่ผิดแบบมั่นใจ
    (กฎเหล็กข้อ 2) ⇒ ห้ามเป็นค่าตั้งต้น แม้จะแลกมากับภาพที่ลื่นกว่า
    """
    assert appmod.config.HIK_LIVE_SMOOTH_VIDEO is False
    r = client.post("/api/detection/start",
                    json={"mode": "can_dent", "camera_index": "hik:DA4994130"})
    assert r.status_code == 200, r.get_json()
    try:
        assert appmod._live_hik_camera() is not None
        assert _smooth_now() is False, "ค่าตั้งต้นต้องเป็นกรอบล็อก ไม่ใช่ภาพลื่น"
    finally:
        client.post("/api/detection/stop")


def test_smooth_live_is_on_for_the_industrial_camera(client, monkeypatch):
    monkeypatch.setattr(appmod.config, "LIVE_SMOOTH_VIDEO", False, raising=False)
    monkeypatch.setattr(appmod.config, "HIK_LIVE_SMOOTH_VIDEO", True, raising=False)
    r = client.post("/api/detection/start",
                    json={"mode": "can_dent", "camera_index": "hik:DA4994130"})
    assert r.status_code == 200, r.get_json()
    try:
        assert appmod._live_hik_camera() is not None
        assert _smooth_now() is True
    finally:
        client.post("/api/detection/stop")


def test_smooth_live_flag_does_not_leak_into_usb_or_rtsp(client, monkeypatch):
    """กล้องที่กำลังใช้ไม่ใช่ Hikrobot ⇒ ต้องได้พฤติกรรมเดิมเป๊ะ (LOCKED)."""
    monkeypatch.setattr(appmod.config, "LIVE_SMOOTH_VIDEO", False, raising=False)
    monkeypatch.setattr(appmod.config, "HIK_LIVE_SMOOTH_VIDEO", True, raising=False)
    monkeypatch.setattr(appmod, "camera", None, raising=False)
    assert appmod._live_hik_camera() is None
    assert _smooth_now() is False


def test_smooth_live_can_be_turned_off_for_the_industrial_camera(client, monkeypatch):
    """ปิด flag = กลับพฤติกรรมเดิม 100% แม้กล้องอุตสาหกรรมกำลังทำงาน."""
    monkeypatch.setattr(appmod.config, "LIVE_SMOOTH_VIDEO", False, raising=False)
    monkeypatch.setattr(appmod.config, "HIK_LIVE_SMOOTH_VIDEO", False, raising=False)
    r = client.post("/api/detection/start",
                    json={"mode": "can_dent", "camera_index": "hik:DA4994130"})
    assert r.status_code == 200, r.get_json()
    try:
        assert appmod._live_hik_camera() is not None
        assert _smooth_now() is False
    finally:
        client.post("/api/detection/stop")


# ── lock ของโมเดล: ต้อง serialize จริง ไม่ใช่แค่มีตัวแปร ────────────
def test_detector_serialises_concurrent_inference(client):
    """
    `detector` ตัวเดียวถูกเรียกจากหลายเธรด: inference_loop · ปุ่มถ่ายรูปตรวจ ·
    /api/stream/infer · งานตรวจเบื้องหลังของถ่ายรัว. บน iGPU ที่แชร์ RAM
    การยิงพร้อมกันทำให้ทั้งสองฝั่งช้าลงกว่าการเข้าคิว (ภาพสดกระตุก)
    """
    import threading
    import numpy as np
    from yolo_detector import YOLODetector

    inside = {"n": 0, "max": 0}
    guard = threading.Lock()

    class SlowModel:
        names = {0: "dent"}

        def __call__(self, *a, **k):
            with guard:
                inside["n"] += 1
                inside["max"] = max(inside["max"], inside["n"])
            time.sleep(0.03)
            with guard:
                inside["n"] -= 1
            return []

    det = YOLODetector()
    det.model = SlowModel()
    frame = np.zeros((80, 80, 3), dtype=np.uint8)
    threads = [threading.Thread(target=lambda: det.detect(frame)) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert inside["max"] == 1, "โมเดลถูกเรียกซ้อนกัน — lock ไม่ทำงาน"


# ── ช่องติ๊กสลับโหมดภาพสด (ไม่ต้องรีสตาร์ต) ─────────────────────────
def test_live_smooth_toggle_round_trip(client):
    r = client.post("/api/detection/start",
                    json={"mode": "can_dent", "camera_index": "hik:DA4994130"})
    assert r.status_code == 200, r.get_json()
    try:
        d = client.get("/api/camera/hik/live_smooth").get_json()
        assert d["smooth"] is False and d["overridden"] is False
        assert _smooth_now() is False

        d = client.post("/api/camera/hik/live_smooth",
                        json={"smooth": True}).get_json()
        assert d["smooth"] is True and d["overridden"] is True
        assert _smooth_now() is True, "การสลับต้องมีผลกับภาพสดจริง"

        d = client.post("/api/camera/hik/live_smooth",
                        json={"smooth": False}).get_json()
        assert d["smooth"] is False
        assert _smooth_now() is False

        # ส่ง null = กลับไปใช้ค่าจาก config
        d = client.post("/api/camera/hik/live_smooth",
                        json={"smooth": None}).get_json()
        assert d["overridden"] is False and d["smooth"] == d["default"]
    finally:
        appmod.hik_live_smooth_override = None
        client.post("/api/detection/stop")


def test_live_smooth_toggle_never_leaks_into_usb(client):
    """สลับไว้ตอนใช้กล้องอุตสาหกรรม แล้วกลับไป USB ต้องได้พฤติกรรมเดิมเป๊ะ"""
    try:
        client.post("/api/camera/hik/live_smooth", json={"smooth": True})
        appmod.camera = None                       # ไม่ได้ใช้กล้องอุตสาหกรรมแล้ว
        assert appmod._live_hik_camera() is None
        assert _smooth_now() is False
    finally:
        appmod.hik_live_smooth_override = None


# ── "ถ่าย 1 เฟรม" แบบ 2 เฟส ────────────────────────────────────────
# เดิมคำขอเดียวทำ จับภาพ → ตรวจ → ส่งกลับ ⇒ ผู้ใช้ไม่เห็นรูปเลยจนกว่าโมเดล
# จะเสร็จ (imgsz 1280 บนสถานี ~420 ms) ทั้งที่การ "ถ่าย" ใช้เวลาแค่ ~15 ms
@pytest.fixture
def shotclient(client, monkeypatch):
    """client + โมเดลปลอมที่นับการเรียก — เส้นทาง shot ต้องมี detector.model"""
    fake = _CountingDetector()
    monkeypatch.setattr(appmod, "detector", fake, raising=False)
    appmod.hik_shot_frame = None
    r = client.post("/api/detection/start",
                    json={"mode": "can_dent", "camera_index": "hik:DA4994130"})
    assert r.status_code == 200, r.get_json()
    client.fake_detector = fake
    yield client
    client.post("/api/detection/stop")
    appmod.hik_shot_frame = None


def test_shot_phase_one_returns_the_picture_without_running_the_model(shotclient,
                                                                      monkeypatch):
    """
    ⚠️ นับ `detector.detect` ตรง ๆ ไม่ได้ — `inference_loop` เรียกมันอยู่ตลอดเวลา
    ในเบื้องหลัง. จึงดักที่ `_hik_shot_inspect` ซึ่งเป็นทางเดียวที่เส้นทาง shot
    ใช้เรียกโมเดล: ถ้าเฟส ① แตะมัน เทสต์ต้องพังเสียงดัง
    """
    client = shotclient

    def _boom(*a, **k):                       # pragma: no cover
        raise AssertionError("เฟส ① ต้องไม่เรียกโมเดลเลย")

    monkeypatch.setattr(appmod, "_hik_shot_inspect", _boom)
    d = client.post("/api/camera/hik/shot", json={"detect": False}).get_json()
    assert d["status"] == "ok"
    assert d["image"].startswith("data:image/jpeg;base64,")
    assert d["pending_detect"] is True
    assert d.get("shot_id")
    # ⚠️ ต้องไม่มีอะไรที่ดูเหมือนผลตรวจ (กฎเหล็กข้อ 2 — ยังไม่รู้ ต้องบอกว่าไม่รู้)
    assert "verdict" not in d and "dent_count" not in d


def test_shot_phase_two_inspects_the_frame_that_was_captured(shotclient):
    client = shotclient
    if True:
        a = client.post("/api/camera/hik/shot", json={"detect": False}).get_json()
        b = client.post("/api/camera/hik/shot/inspect",
                        json={"shot_id": a["shot_id"], "imgsz": 480}).get_json()
        assert b["status"] == "ok"
        assert b["verdict"] in ("ok", "ng")
        assert b["pending_detect"] is False
        assert b["infer_imgsz"] == 480
        assert b["shot_id"] == a["shot_id"]
        assert b["capture_size"] == a["capture_size"]


def test_shot_phase_two_refuses_a_stale_shot_id(shotclient):
    """ถ่ายใหม่ระหว่างรอผลตรวจ ⇒ ต้องปฏิเสธ ไม่ใช่คืนผลของเฟรมก่อนหน้าเงียบ ๆ"""
    client = shotclient
    if True:
        a = client.post("/api/camera/hik/shot", json={"detect": False}).get_json()
        client.post("/api/camera/hik/shot", json={"detect": False})   # ถ่ายทับ
        r = client.post("/api/camera/hik/shot/inspect", json={"shot_id": a["shot_id"]})
        assert r.status_code == 409
        assert "ถ่ายภาพใหม่" in r.get_json()["message"]


def test_shot_inspect_without_a_capture_is_refused(shotclient):
    client = shotclient
    if True:
        appmod.hik_shot_frame = None
        r = client.post("/api/camera/hik/shot/inspect", json={})
        assert r.status_code == 409


def test_shot_without_detect_flag_keeps_the_old_one_call_behaviour(shotclient):
    """client เก่าที่ไม่ส่ง detect มา ต้องได้ผลครบในคำขอเดียวเหมือนเดิม"""
    client = shotclient
    if True:
        d = client.post("/api/camera/hik/shot", json={"imgsz": 480}).get_json()
        assert d["status"] == "ok"
        assert d["verdict"] in ("ok", "ng")
        assert d["pending_detect"] is False
        assert d["image"].startswith("data:image/jpeg;base64,")
