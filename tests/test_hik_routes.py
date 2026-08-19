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
import os
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
