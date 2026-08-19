"""
เทสต์ของ `hik_camera.py` — คลาสกล้องอุตสาหกรรม Hikrobot ที่เสียบเข้า pipeline เดิม.

รันได้ทุกเครื่อง: ใช้ **MVS SDK ปลอม** (`tests/fake_mvs/`) จึงไม่ต้องมีกล้อง/ไม่ต้องมี MVS
และไม่ต้องเป็น Windows.

สิ่งที่เทสต์ชุดนี้ "ล็อกไว้" คือบทเรียนที่ได้จากการวัดกล้องจริงบนสถานี 19 ส.ค. 2026:
  · packet size/delay ต้องถูกตั้ง **ทุกครั้ง** ตอนเปิดกล้อง (กล้องไม่จำ)
  · เฟรมที่คืนต้องเป็นสำเนาของตัวเอง ไม่ใช่ view ของบัฟเฟอร์ที่ใช้ซ้ำ
  · ค่าที่ GenICam ล็อกระหว่างสตรีมต้องหยุด-เริ่มสตรีมให้อัตโนมัติ
  · ต้องไม่มีเมธอด set_control (ไม่งั้นสไลเดอร์ของโหมด USB จะมาสั่งกล้องตัวนี้)
  · การ "อ่านค่ามาแสดง" ต้องไม่เขียนอะไรลงกล้องเลย
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE_SDK = os.path.join(ROOT, "tests", "fake_mvs")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

import hik_camera as hc                                              # noqa: E402


@pytest.fixture(autouse=True)
def fake_sdk(monkeypatch, tmp_path):
    """ชี้ให้ทุกเทสต์ใช้ SDK ปลอม + ค่าตั้งในโฟลเดอร์ชั่วคราว (ไม่แตะ data/ ของจริง)."""
    if FAKE_SDK not in sys.path:
        sys.path.insert(0, FAKE_SDK)
    import MvCameraControl_class as fake
    # คืนค่า SIM ให้เป็นค่าเริ่มต้นทุกครั้ง — เทสต์ก่อนหน้าต้องไม่รั่วมาถึงตัวถัดไป
    fake.SIM.update({"accessible": True, "open_ok": True, "packet_size": 1500,
                     "optimal_packet_size": 8164, "lost_packets": 0, "lost_frames": 0,
                     "drop_every": 0, "gray_level": 120, "max_packet_size": 9000,
                     "missing_nodes": [], "grab_fail": 0})
    hc._sdk_cache["mod"] = None
    hc._sdk_cache["info"] = None
    monkeypatch.setattr(hc.config, "HIK_SDK_PATH", FAKE_SDK, raising=False)
    monkeypatch.setattr(hc.config, "HIK_SETTINGS_FILE",
                        str(tmp_path / "hik_camera.json"), raising=False)
    monkeypatch.setattr(hc.config, "HIK_DATASET_DIR", str(tmp_path / "ds"), raising=False)
    yield fake
    hc._sdk_cache["mod"] = None
    hc._sdk_cache["info"] = None


def open_cam(**kw):
    cam = hc.HikCamera(camera_index="hik:DA4994130", **kw)
    assert cam.initialize(), cam.last_error
    return cam


# ── การอ้างถึงกล้อง ────────────────────────────────────────
def test_source_parsing_round_trip():
    assert hc.is_hik_source("hik:DA4994130")
    assert not hc.is_hik_source("rtsp://cam/stream")
    assert not hc.is_hik_source(0)
    assert hc.parse_source("hik:DA4994130") == ("serial", "DA4994130")
    assert hc.parse_source("hik:#2") == ("index", 2)
    assert hc.parse_source("hik:") == (None, None)
    assert hc.parse_source(hc.make_source(serial="ABC")) == ("serial", "ABC")
    assert hc.parse_source(hc.make_source(index=3)) == ("index", 3)


def test_scan_devices_reports_identity_and_source():
    devices, err = hc.scan_devices()
    assert err is None
    assert len(devices) == 1
    d = devices[0]
    assert d["model"] == "MV-CS050-10GC" and d["serial"] == "DA4994130"
    assert d["ip"] == "172.32.1.253" and d["nic"] == "172.32.1.9"
    assert d["mac"] == "34:BD:20:54:48:3B"          # MAC อยู่บน struct ชั้นนอก
    assert d["source"] == "hik:DA4994130"
    assert "_info" not in d                          # ต้อง JSON ได้ (ห้ามมี ctypes ติดไป)


# ── การเปิดกล้อง ─────────────────────────────────────────
def test_open_sets_packet_size_every_time():
    """
    บทเรียนอันดับหนึ่งจากสถานี: กล้อง **ไม่จำ** packet size/delay — เปิดใหม่ทีไร
    กลับเป็น 1500/400 ⇒ 5MP จะได้ 15-17 fps พร้อมเฟรมหายแบบเงียบ.
    """
    cam = open_cam()
    try:
        assert cam._io.get_int("GevSCPSPacketSize")["value"] == 8164
        assert cam._io.get_int("GevSCPD")["value"] == 0
    finally:
        cam.release()


def test_open_refuses_when_mvs_holds_the_camera(fake_sdk):
    fake_sdk.SIM["accessible"] = False
    cam = hc.HikCamera(camera_index="hik:DA4994130")
    assert cam.initialize() is False
    assert "MVS" in cam.last_error


def test_open_reports_unknown_serial_with_the_ones_it_found():
    cam = hc.HikCamera(camera_index="hik:NOT-A-REAL-SN")
    assert cam.initialize() is False
    assert "NOT-A-REAL-SN" in cam.last_error and "DA4994130" in cam.last_error


def test_open_fails_loudly_when_no_image_arrives(fake_sdk):
    """เปิดกล้องได้แต่ไม่มีภาพ = ต้องบอกและถือว่าเปิดไม่สำเร็จ ไม่ใช่ปล่อยจอว่าง."""
    fake_sdk.SIM["grab_fail"] = 999
    cam = hc.HikCamera(camera_index="hik:DA4994130")
    assert cam.initialize() is False
    assert "ไม่มีภาพ" in cam.last_error
    assert cam.is_initialized is False


def test_identity_comes_from_camera_nodes():
    cam = open_cam()
    try:
        assert cam.identity["model"] == "MV-CS050-10GC"
        assert cam.identity["serial"] == "DA4994130"
        assert cam.identity["firmware"].startswith("V4.0.42")
        assert cam.identity["ip"] == "172.32.1.253"
    finally:
        cam.release()


# ── การอ่านเฟรม ──────────────────────────────────────────
def test_read_frame_matches_camera_contract():
    cam = open_cam()
    try:
        assert cam.read_frame.__self__ is cam           # มีเมธอดจริง
        ok, frame = cam.read_frame()
        assert ok is True
        assert frame.ndim == 3 and frame.shape[2] == 3
        assert frame.dtype == np.uint8
        assert frame.shape[1] <= hc._cfg("HIK_LIVE_MAX_WIDTH", 1280)
    finally:
        cam.release()


def test_read_frame_before_initialize_returns_none():
    cam = hc.HikCamera(camera_index="hik:DA4994130")
    assert cam.read_frame() is None                    # สัญญาเดียวกับ camera.Camera


def test_frames_are_independent_copies():
    """
    ⚠️ capture_loop เก็บเฟรมไว้ให้ inference_loop อ่านทีหลัง — ถ้าเราคืน view ของ
    บัฟเฟอร์ที่ใช้ซ้ำ เฟรมที่กำลังตรวจจะถูกเขียนทับกลางคัน = ผลตรวจของภาพที่ไม่มีอยู่จริง.
    """
    cam = open_cam()
    try:
        ok1, f1 = cam.read_frame()
        ok2, f2 = cam.read_frame()
        assert ok1 and ok2
        assert f1 is not f2
        f1[:] = 7
        assert not np.array_equal(f1, f2)              # เขียนอันหนึ่งต้องไม่กระทบอีกอัน
        assert f1.base is None or not np.shares_memory(f1, f2)
    finally:
        cam.release()


def test_downscale_keeps_aspect_ratio():
    cam = open_cam()
    try:
        _, frame = cam.read_frame()
        h, w = frame.shape[:2]
        assert w == 1280
        assert abs((w / h) - (2448 / 2048)) < 0.01
        assert cam.width == 2448 and cam.height == 2048   # ขนาดจริงจากกล้องยังถูกเก็บไว้
    finally:
        cam.release()


def test_snap_full_returns_unscaled_frame():
    """ปุ่ม 'ถ่าย 1 เฟรม' ต้องได้ภาพเต็มความละเอียด ไม่ใช่ภาพที่ย่อแล้ว."""
    import threading
    cam = open_cam()
    try:
        result = {}

        def grabber():
            for _ in range(20):
                cam.read_frame()

        t = threading.Thread(target=grabber)
        t.start()
        result["frame"] = cam.snap_full(timeout=3.0)
        t.join()
        full = result["frame"]
        assert full is not None
        assert full.shape[1] == 2448 and full.shape[0] == 2048
    finally:
        cam.release()


# ── ค่าพารามิเตอร์ ───────────────────────────────────────
def test_get_params_supported_is_always_boolean(fake_sdk):
    """
    'supported' ต้องหมายถึง "กล้องมี node นี้ไหม" เสมอ — ถ้าปนกับรายการตัวเลือกของ enum
    หน้าเว็บจะแสดงปุ่มของ node ที่ไม่มีอยู่จริง (กดแล้วเงียบ).
    """
    fake_sdk.SIM["missing_nodes"] = ["ReverseX", "TriggerActivation"]
    cam = open_cam()
    try:
        params = cam.get_params()
        for key, entry in params.items():
            assert isinstance(entry["supported"], bool), key
        assert params["reverse_x"]["supported"] is False
        assert params["trigger_activation"]["supported"] is False
        assert params["exposure_us"]["supported"] is True
        assert params["exposure_us"]["min"] == pytest.approx(15.0)
        assert params["exposure_us"]["max"] == pytest.approx(40279.0)
        assert params["pixel_format"]["symbolic"] == "BayerRG8"
        assert "BayerRG8" in params["pixel_format"]["options"]
    finally:
        cam.release()


def test_set_live_param_does_not_stop_the_stream():
    cam = open_cam()
    try:
        res = cam.set_params({"exposure_us": 1234.0})
        assert res["restarted"] is False               # exposure ตั้งได้ระหว่างสตรีม
        assert res["failed"] == {}
        assert cam.get_params()["exposure_us"]["value"] == pytest.approx(1234.0)
        ok, _ = cam.read_frame()
        assert ok
    finally:
        cam.release()


def test_set_locked_param_restarts_stream_and_keeps_working():
    """
    Width/Height ถูกล็อกระหว่าง grabbing — ถ้าไม่หยุดสตรีมก่อน การ set จะไม่ติด
    "แบบเงียบ" แล้วผู้ใช้เห็นค่าบนจอไม่ตรงกับกล้องจริง.
    """
    cam = open_cam()
    try:
        res = cam.set_params({"width": 1224, "height": 1024, "roi_center": True})
        assert res["restarted"] is True
        assert res["failed"] == {}
        params = cam.get_params()
        assert params["width"]["value"] == 1224 and params["height"]["value"] == 1024
        assert params["offset_x"]["value"] == 612 and params["offset_y"]["value"] == 512
        ok, frame = cam.read_frame()
        assert ok and frame.shape[1] == 1224          # เล็กกว่าเพดานแล้วจึงไม่ถูกย่อ
    finally:
        cam.release()


def test_out_of_range_value_is_clamped_not_rejected_silently():
    cam = open_cam()
    try:
        res = cam.set_params({"exposure_us": 10_000_000})
        assert "exposure_us" in res["applied"]
        assert res["applied"]["exposure_us"]["value"] <= 40279.0
        assert res["applied"]["exposure_us"]["message"]        # ต้องบอกว่าปรับให้
    finally:
        cam.release()


def test_unknown_param_is_reported_not_ignored():
    cam = open_cam()
    try:
        res = cam.set_params({"ไม่มีค่านี้": 1})
        assert res["applied"] == {} and res["failed"] == {}    # ไม่รู้จัก = ไม่แตะกล้อง
        res2 = cam._set_one("ไม่มีค่านี้", 1)
        assert res2[0] is False and "ไม่รู้จัก" in res2[1]
    finally:
        cam.release()


def test_missing_node_cannot_be_set(fake_sdk):
    fake_sdk.SIM["missing_nodes"] = ["Gain"]
    cam = open_cam()
    try:
        res = cam.set_params({"gain_db": 5})
        assert "gain_db" in res["failed"]
        assert "ไม่เปิดให้ตั้ง" in res["failed"]["gain_db"]["message"]
    finally:
        cam.release()


# ── การแยกตัวจากโหมดอื่น ────────────────────────────────────
def test_has_no_set_control_method():
    """
    /api/camera/control (สไลเดอร์ brightness/contrast ของกล้อง USB) กรองด้วย
    hasattr(cam, "set_control") — ถ้าคลาสนี้มีเมธอดชื่อเดียวกัน สไลเดอร์ของโหมด USB
    จะไปสั่งกล้องอุตสาหกรรมโดยไม่ตั้งใจ (ละเมิดกฎเหล็กข้อ 1).
    """
    assert not hasattr(hc.HikCamera, "set_control")


def test_probe_params_never_writes_to_the_camera():
    """อ่านค่ามาแสดงบนหน้าเว็บ ต้องไม่ทับค่าที่ช่างตั้งไว้ใน MVS."""
    cam = open_cam()
    cam.set_params({"exposure_us": 222.0})
    cam.release()

    data, err = hc.probe_params("hik:DA4994130")
    assert err is None
    # ค่าที่เห็นต้องเป็น "ค่าของกล้องเอง" (SDK ปลอมตั้งต้นที่ 2635 µs ตามหน้าจอ MVS จริง)
    # ไม่ใช่ 5000 ของ config.HIK_DEFAULTS ซึ่งจะปรากฏก็ต่อเมื่อเราเผลอเขียนลงกล้องตอนอ่านค่า
    assert data["params"]["exposure_us"]["value"] == pytest.approx(2635.0)
    assert data["params"]["exposure_us"]["value"] != pytest.approx(
        hc._cfg("HIK_DEFAULTS", {}).get("exposure_us", -1))
    assert data["identity"]["serial"] == "DA4994130"
    # packet size ต้องไม่ถูกแตะตอนอ่านค่า (ยังเป็นค่าตั้งต้น 1500 ของไดรเวอร์)
    assert data["params"]["packet_size"]["value"] == 1500


# ── ค่าที่บันทึกไว้ ────────────────────────────────────────
def test_saved_settings_round_trip_and_are_applied_on_open():
    assert hc.save_settings({"exposure_us": 321.0, "gain_db": 3.0})
    assert hc.load_settings()["exposure_us"] == 321.0
    cam = open_cam()
    try:
        assert cam.get_params()["exposure_us"]["value"] == pytest.approx(321.0)
    finally:
        cam.release()


def test_broken_settings_file_falls_back_to_defaults(tmp_path, monkeypatch):
    bad = tmp_path / "broken.json"
    bad.write_text("{ ไม่ใช่ json", encoding="utf-8")
    monkeypatch.setattr(hc.config, "HIK_SETTINGS_FILE", str(bad), raising=False)
    assert hc.load_settings() == {}                    # ต้องไม่โยน
    cam = open_cam()
    cam.release()


# ── ชุดข้อมูล ─────────────────────────────────────────────
def test_dataset_saves_full_resolution_images(tmp_path):
    import glob
    import time
    cam = open_cam()
    try:
        status = cam.start_dataset()
        assert status["active"] is True
        for _ in range(6):
            cam.read_frame()
        time.sleep(1.0)
        final = cam.stop_dataset()
        files = sorted(glob.glob(os.path.join(final["dir"], "*.jpg")))
        assert files, "ไม่มีไฟล์ภาพถูกเขียนเลย"
        import cv2
        img = cv2.imread(files[0])
        assert img.shape[1] == 2448, "ภาพชุดข้อมูลต้องเป็นความละเอียดเต็ม ไม่ใช่ภาพที่ย่อแล้ว"
    finally:
        cam.release()


# ── สถิติ ────────────────────────────────────────────────
def test_stats_expose_the_numbers_needed_to_trust_the_feed(fake_sdk):
    fake_sdk.SIM["drop_every"] = 3
    fake_sdk.SIM["lost_packets"] = 4
    cam = open_cam()
    try:
        for _ in range(12):
            cam.read_frame()
        st = cam.stats()
        assert st["frames"] >= 10
        assert st["dropped"] > 0, "เลขเฟรมกระโดดต้องถูกนับ ไม่ใช่ปล่อยผ่านเงียบ"
        assert st["lost_packets"] == 4
        assert st["size"] == "2448x2048"
        assert st["sent_width"] == 1280
    finally:
        cam.release()


def test_sdk_status_reports_hint_when_missing(monkeypatch):
    monkeypatch.setattr(hc.config, "HIK_SDK_PATH", "/ทางที่ไม่มีอยู่จริง", raising=False)
    monkeypatch.setattr(hc, "SDK_ROOTS", [])
    for m in ("MvCameraControl_class", "MvCameraControl"):
        monkeypatch.delitem(sys.modules, m, raising=False)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if "fake_mvs" not in p])
    hc._sdk_cache["mod"] = None
    status = hc.sdk_status()
    assert status["available"] is False
    assert "MVS" in status["hint"]


# ── ความสอดคล้องข้ามไฟล์ (กับดักประจำของ repo นี้: แก้ข้างเดียวแล้วอีกฝั่งเงียบ) ──
def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_js_and_python_param_keys_match():
    """
    `UI_ORDER` ใน `static/js/hik_camera.js` ต้องอ้างเฉพาะคีย์ที่มีจริงใน `PARAM_SPECS`.
    ถ้าไม่ตรง ตัวควบคุมนั้นจะ **หายไปจากหน้าเว็บเงียบ ๆ** (JS ซ่อน key ที่ไม่มีข้อมูล)
    หรือส่งค่าที่ฝั่ง Python ปฏิเสธ — ทั้งสองแบบไม่มี error ให้เห็น.
    """
    import re
    js = _read(os.path.join("static", "js", "hik_camera.js"))
    block = js.split("var UI_ORDER = [", 1)[1].split("];", 1)[0]
    js_keys = re.findall(r"\['([a-z_]+)'", block)
    py_keys = [s["key"] for s in hc.PARAM_SPECS]
    assert js_keys, "อ่าน UI_ORDER จากไฟล์ JS ไม่ได้ (โครงสร้างไฟล์เปลี่ยนไป?)"
    assert set(js_keys) <= set(py_keys), \
        "JS อ้างคีย์ที่ไม่มีใน PARAM_SPECS: %s" % (set(js_keys) - set(py_keys))
    assert set(py_keys) - set(js_keys) == set(), \
        "PARAM_SPECS มีคีย์ที่หน้าเว็บไม่แสดง: %s" % (set(py_keys) - set(js_keys))


def test_every_element_id_used_by_js_exists_in_the_template():
    """
    `$("id")` ที่ไม่มีจริงจะ **เงียบ ไม่ error** — ฟีเจอร์หายไปเฉย ๆ
    (กับดักที่ CLAUDE.md เตือนไว้ และเคยเกิดมาแล้วในโหมด Artwork).
    """
    import re
    js = _read(os.path.join("static", "js", "hik_camera.js"))
    html = _read(os.path.join("templates", "index.html"))
    ids = sorted(set(re.findall(r"\$\('([A-Za-z0-9_]+)'\)", js)))
    assert ids, "ไม่พบการใช้ $() ในไฟล์ JS เลย"
    missing = [i for i in ids if ('id="%s"' % i) not in html]
    assert not missing, "JS อ้าง element ที่ไม่มีใน template: %s" % missing


def test_template_wires_the_new_tab_and_script():
    html = _read(os.path.join("templates", "index.html"))
    assert "switchSource('hik')" in html
    assert 'id="tabHik"' in html and 'id="panelHik"' in html
    assert "js/hik_camera.js" in html          # ลืมใส่ = ทั้งแท็บตายเงียบ
    assert "HikUI.init()" in html
    assert "HikUI.selectedSource()" in html    # ปุ่ม Start ต้องรู้ว่าเลือกกล้องไหน


def test_css_classes_used_by_js_exist_in_the_template():
    """คลาสที่ JS สร้างต้องมีกฎ CSS จริง ไม่งั้นแผงจะโผล่มาแบบไม่มีรูปทรง."""
    html = _read(os.path.join("templates", "index.html"))
    for cls in ("hik-row", "hik-label", "hik-input", "hik-range", "hik-note",
                "hik-bad", "hik-ident", "hik-shot-verdict"):
        assert ".%s" % cls in html, "ไม่มีกฎ CSS ของ .%s" % cls


def test_release_while_another_thread_reads_params_is_safe():
    """
    ปิดกล้องพร้อมกับที่อีกเธรดกำลังอ่านค่าอยู่ ต้องไม่ระเบิด — ในของจริงคือการเรียก SDK
    ด้วย handle ที่เพิ่งถูกทำลาย ซึ่งล้มทั้งโปรเซส (ไม่ใช่แค่ exception ของ Python).
    """
    import threading
    cam = open_cam()
    errors = []

    def reader():
        try:
            for _ in range(30):
                cam.get_params()
                cam.stats()
        except Exception as e:                        # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=reader)
    t.start()
    cam.release()
    t.join(timeout=10)
    assert not t.is_alive()
    assert errors == [], errors
    assert cam.is_initialized is False


def test_explicit_sdk_path_wins_over_standard_locations(monkeypatch, tmp_path):
    """
    ทางที่ผู้ใช้ระบุเอง (config.HIK_SDK_PATH) ต้องชนะทางมาตรฐานเสมอ.
    เดิม `sys.path.insert(0, ...)` วนตามลำดับความสำคัญทำให้ผลกลับด้าน —
    ตัวที่ใส่ทีหลัง (ความสำคัญต่ำสุด) กลับไปอยู่หน้าสุด.
    """
    other = tmp_path / "other_sdk"
    other.mkdir()
    (other / "MvCameraControl_class.py").write_text(
        "class MvCamera(object):\n    MARKER = 'ของปลอมอีกตัว'\n", encoding="utf-8")
    monkeypatch.setattr(hc, "SDK_ROOTS", [str(other)])
    monkeypatch.setattr(hc.config, "HIK_SDK_PATH", FAKE_SDK, raising=False)
    for m in ("MvCameraControl_class", "MvCameraControl"):
        monkeypatch.delitem(sys.modules, m, raising=False)
    monkeypatch.setattr(sys, "path", [p for p in sys.path
                                      if "fake_mvs" not in p and "other_sdk" not in p])
    hc._sdk_cache["mod"] = None
    mod, info = hc.load_sdk()
    assert mod is not None
    assert "fake_mvs" in info["file"], "ทางที่ระบุเองแพ้ทางมาตรฐาน (ลำดับ sys.path กลับด้าน)"
    assert not hasattr(mod.MvCamera, "MARKER")


def test_fake_sdk_is_flagged_loudly():
    """
    ถ้า SDK ปลอมถูกโหลดบนเครื่องที่ใช้งานจริง ระบบจะ 'ตรวจ' ภาพสังเคราะห์แล้วรายงานผล
    เหมือนของจริงทุกประการ = ผลที่ผิดแบบมั่นใจ ⇒ ต้องมีธงให้หน้าเว็บเอาไปเตือน.
    """
    st = hc.sdk_status()
    assert st["available"] is True
    assert st["is_fake"] is True
    js = _read(os.path.join("static", "js", "hik_camera.js"))
    assert "is_fake" in js and "SDK ปลอม" in js
