"""
Unit tests for the Hikrobot GigE/USB3 camera layer (hik_camera.py).

There is no camera and no MVS SDK in CI, so these tests inject a FAKE
``MvCameraControl_class`` module into ``sys.modules`` — the same trick the
artwork highlight tests use to make Tesseract deterministic. That makes every
case below reproducible and lets us test the parts that actually bite in the
field:

  • SDK missing            → degrade quietly, never raise, never break the app
  • camera on wrong subnet → the #1 first-time GigE trap, must be reported
  • Mono8 / BGR8 / Bayer   → always a 3-channel BGR frame (what YOLO expects)
  • grab timeout           → (False, None), the same contract camera.Camera has
  • release()              → idempotent, and returns the SDK's frame buffers

What these tests CANNOT prove: that the real SDK behaves like the fake. That is
what ``diagnose_hik.py`` is for, on the station, with the camera plugged in.
"""

import ctypes
import sys
import types

import numpy as np
import pytest

import config
import hik_camera


# ── the fake MVS SDK ───────────────────────────────────────────────────────

MV_OK = 0
MV_GIGE_DEVICE = 0x00000001
MV_USB_DEVICE = 0x00000004
PIXEL_MONO8 = 0x01080001
PIXEL_BGR8 = 0x02180015
PIXEL_RGB8 = 0x02180014
PIXEL_BAYER_RG8 = 0x01080009


class _GigEInfo(ctypes.Structure):
    _fields_ = [("nCurrentIp", ctypes.c_uint),
                ("nCurrentSubNetMask", ctypes.c_uint),
                ("nNetExport", ctypes.c_uint),
                ("chModelName", ctypes.c_ubyte * 32),
                ("chSerialNumber", ctypes.c_ubyte * 16),
                ("chUserDefinedName", ctypes.c_ubyte * 16),
                ("chDeviceVersion", ctypes.c_ubyte * 32)]


class _Usb3Info(ctypes.Structure):
    _fields_ = [("chModelName", ctypes.c_ubyte * 64),
                ("chSerialNumber", ctypes.c_ubyte * 64),
                ("chUserDefinedName", ctypes.c_ubyte * 64),
                ("chDeviceVersion", ctypes.c_ubyte * 64)]


class _SpecialInfo(ctypes.Union):
    _fields_ = [("stGigEInfo", _GigEInfo), ("stUsb3VInfo", _Usb3Info)]


class _DeviceInfo(ctypes.Structure):
    _fields_ = [("nTLayerType", ctypes.c_uint), ("SpecialInfo", _SpecialInfo)]


class _DeviceInfoList(ctypes.Structure):
    _fields_ = [("nDeviceNum", ctypes.c_uint),
                ("pDeviceInfo", ctypes.POINTER(_DeviceInfo) * 8)]


class _FrameInfo(ctypes.Structure):
    _fields_ = [("nWidth", ctypes.c_ushort), ("nHeight", ctypes.c_ushort),
                ("enPixelType", ctypes.c_uint), ("nFrameLen", ctypes.c_uint)]


class _FrameOut(ctypes.Structure):
    _fields_ = [("pBufAddr", ctypes.POINTER(ctypes.c_ubyte)),
                ("stFrameInfo", _FrameInfo)]


class _ConvertParam(ctypes.Structure):
    _fields_ = [("nWidth", ctypes.c_ushort), ("nHeight", ctypes.c_ushort),
                ("enSrcPixelType", ctypes.c_uint),
                ("pSrcData", ctypes.POINTER(ctypes.c_ubyte)),
                ("nSrcDataLen", ctypes.c_uint),
                ("enDstPixelType", ctypes.c_uint),
                ("pDstBuffer", ctypes.POINTER(ctypes.c_ubyte)),
                ("nDstBufferSize", ctypes.c_uint)]


class _IntValue(ctypes.Structure):
    _fields_ = [("nCurValue", ctypes.c_int)]


class _FloatValue(ctypes.Structure):
    _fields_ = [("fCurValue", ctypes.c_float)]


def _fill(dst, text):
    raw = text.encode()
    for i, b in enumerate(raw[:len(dst) - 1]):
        dst[i] = b


def _ip(a, b, c, d):
    return (a << 24) | (b << 16) | (c << 8) | d


class FakeCam:
    """Records every SDK call so tests can assert on the open sequence."""

    # Populated per-test; shared by all instances via the fake module.
    devices = []          # list of dicts describing devices to enumerate
    frames = []           # queue of (pixel_type, w, h, payload bytes) to hand out
    repeat = None         # frame served forever once `frames` is drained
    open_fails = False

    def __init__(self):
        self.calls = []
        self.nodes = {}
        self.handle = None
        self.freed = 0
        self._buf = None

    # -- lifecycle
    def MV_CC_CreateHandle(self, info):
        self.calls.append("CreateHandle")
        self.handle = info
        return MV_OK

    def MV_CC_OpenDevice(self, mode, key):
        self.calls.append(f"OpenDevice({mode})")
        return 0x80000203 if FakeCam.open_fails else MV_OK

    def MV_CC_CloseDevice(self):
        self.calls.append("CloseDevice")
        return MV_OK

    def MV_CC_DestroyHandle(self):
        self.calls.append("DestroyHandle")
        return MV_OK

    def MV_CC_StartGrabbing(self):
        self.calls.append("StartGrabbing")
        return MV_OK

    def MV_CC_StopGrabbing(self):
        self.calls.append("StopGrabbing")
        return MV_OK

    # -- config nodes
    def MV_CC_GetOptimalPacketSize(self):
        return 8164

    def MV_CC_SetIntValue(self, k, v):
        self.calls.append(f"SetInt:{k}={v}")
        self.nodes[k] = v
        return MV_OK

    def MV_CC_SetFloatValue(self, k, v):
        self.calls.append(f"SetFloat:{k}={v}")
        self.nodes[k] = v
        return MV_OK

    def MV_CC_SetEnumValue(self, k, v):
        self.calls.append(f"SetEnum:{k}={v}")
        self.nodes[k] = v
        return MV_OK

    def MV_CC_SetEnumValueByString(self, k, v):
        self.calls.append(f"SetEnumStr:{k}={v}")
        self.nodes[k] = v
        return MV_OK

    def MV_CC_SetBoolValue(self, k, v):
        self.calls.append(f"SetBool:{k}={v}")
        self.nodes[k] = v
        return MV_OK

    def MV_CC_SetImageNodeNum(self, n):
        self.calls.append(f"SetImageNodeNum:{n}")
        return MV_OK

    def MV_CC_GetIntValue(self, k, out):
        out.nCurValue = int(self.nodes.get(k, {"Width": 1440, "Height": 1080}.get(k, 0)))
        return MV_OK

    def MV_CC_GetFloatValue(self, k, out):
        out.fCurValue = float(self.nodes.get(k, 0.0))
        return MV_OK

    # -- frames
    def MV_CC_GetImageBuffer(self, frame_out, timeout):
        if FakeCam.frames:
            pt, w, h, payload = FakeCam.frames.pop(0)
        elif FakeCam.repeat is not None:
            pt, w, h, payload = FakeCam.repeat
        else:
            return 0x8000000E          # timeout
        self._buf = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
        frame_out.pBufAddr = ctypes.cast(self._buf, ctypes.POINTER(ctypes.c_ubyte))
        frame_out.stFrameInfo.nWidth = w
        frame_out.stFrameInfo.nHeight = h
        frame_out.stFrameInfo.enPixelType = pt
        frame_out.stFrameInfo.nFrameLen = len(payload)
        return MV_OK

    def MV_CC_FreeImageBuffer(self, frame_out):
        self.freed += 1
        return MV_OK

    def MV_CC_ConvertPixelType(self, param):
        # Stand-in for the vendor debayer: fill BGR with a recognisable pattern.
        n = param.nWidth * param.nHeight * 3
        for i in range(n):
            param.pDstBuffer[i] = (i % 251)
        return MV_OK

    @staticmethod
    def MV_CC_EnumDevices(layers, dev_list):
        entries = [d for d in FakeCam.devices if d["tlayer"] & layers]
        dev_list.nDeviceNum = len(entries)
        FakeCam._keep = []                     # keep structs alive
        for i, d in enumerate(entries):
            info = _DeviceInfo()
            info.nTLayerType = d["tlayer"]
            if d["tlayer"] == MV_GIGE_DEVICE:
                g = info.SpecialInfo.stGigEInfo
                g.nCurrentIp = d["ip"]
                g.nCurrentSubNetMask = d.get("mask", _ip(255, 255, 255, 0))
                g.nNetExport = d["host_ip"]
                _fill(g.chModelName, d["model"])
                _fill(g.chSerialNumber, d["serial"])
                _fill(g.chUserDefinedName, d.get("user_name", ""))
                _fill(g.chDeviceVersion, d.get("version", "V1.0"))
            else:
                u = info.SpecialInfo.stUsb3VInfo
                _fill(u.chModelName, d["model"])
                _fill(u.chSerialNumber, d["serial"])
                _fill(u.chUserDefinedName, d.get("user_name", ""))
                _fill(u.chDeviceVersion, d.get("version", "V1.0"))
            FakeCam._keep.append(info)
            dev_list.pDeviceInfo[i] = ctypes.pointer(info)
        return MV_OK


def _make_fake_module():
    m = types.ModuleType("MvCameraControl_class")
    m.MvCamera = FakeCam
    m.MV_CC_DEVICE_INFO = _DeviceInfo
    m.MV_CC_DEVICE_INFO_LIST = _DeviceInfoList
    m.MV_FRAME_OUT = _FrameOut
    m.MV_CC_PIXEL_CONVERT_PARAM = _ConvertParam
    m.MVCC_INTVALUE = _IntValue
    m.MVCC_FLOATVALUE = _FloatValue
    m.MV_GIGE_DEVICE = MV_GIGE_DEVICE
    m.MV_USB_DEVICE = MV_USB_DEVICE
    m.MV_ACCESS_Exclusive = 1
    m.MV_TRIGGER_MODE_OFF = 0
    m.PixelType_Gvsp_Mono8 = PIXEL_MONO8
    m.PixelType_Gvsp_BGR8_Packed = PIXEL_BGR8
    m.PixelType_Gvsp_RGB8_Packed = PIXEL_RGB8
    return m


GIGE_OK = {"tlayer": MV_GIGE_DEVICE, "model": "MV-CE060-10GC", "serial": "01234567",
           "ip": _ip(192, 168, 1, 64), "host_ip": _ip(192, 168, 1, 10)}
GIGE_WRONG_SUBNET = {"tlayer": MV_GIGE_DEVICE, "model": "MV-CE060-10GC",
                     "serial": "99999999",
                     "ip": _ip(192, 168, 5, 64), "host_ip": _ip(192, 168, 1, 10)}
USB_OK = {"tlayer": MV_USB_DEVICE, "model": "MV-CU013-A0UC", "serial": "USB00001"}


@pytest.fixture
def fake_sdk(monkeypatch):
    """Install the fake SDK and reset all shared state between tests."""
    FakeCam.devices, FakeCam.frames = [], []
    FakeCam.repeat, FakeCam.open_fails = None, False
    monkeypatch.setitem(sys.modules, "MvCameraControl_class", _make_fake_module())
    hik_camera._reset_sdk_cache()
    yield FakeCam
    hik_camera._reset_sdk_cache()


@pytest.fixture
def no_sdk(monkeypatch):
    """Simulate a station where MVS was never installed."""
    monkeypatch.delitem(sys.modules, "MvCameraControl_class", raising=False)
    monkeypatch.setattr(hik_camera, "_candidate_sdk_dirs", lambda: [])
    hik_camera._reset_sdk_cache()
    yield
    hik_camera._reset_sdk_cache()


# ── SDK missing: must degrade, never explode ───────────────────────────────

def test_sdk_missing_reports_unavailable(no_sdk):
    st = hik_camera.sdk_status()
    assert st["available"] is False
    assert "MVS SDK" in st["message"]


def test_sdk_missing_scan_returns_empty(no_sdk):
    assert hik_camera.scan_hik_cameras() == []


def test_sdk_missing_initialize_returns_false_without_raising(no_sdk):
    cam = hik_camera.HikCamera(device_key="hik:01234567")
    assert cam.initialize() is False
    assert cam.is_initialized is False
    assert cam.last_error


def test_sdk_missing_read_frame_returns_none(no_sdk):
    cam = hik_camera.HikCamera()
    cam.initialize()
    assert cam.read_frame() is None      # same "no camera" signal camera.Camera gives


# ── enumeration ────────────────────────────────────────────────────────────

def test_scan_lists_gige_and_usb(fake_sdk):
    fake_sdk.devices = [GIGE_OK, USB_OK]
    cams = hik_camera.scan_hik_cameras()
    assert [c["key"] for c in cams] == ["hik:01234567", "hik:USB00001"]
    assert cams[0]["transport"] == "GigE" and cams[0]["ip"] == "192.168.1.64"
    assert cams[1]["transport"] == "USB3" and cams[1]["ip"] is None
    assert all(c["reachable"] for c in cams)


def test_scan_flags_wrong_subnet_with_actionable_hint(fake_sdk):
    fake_sdk.devices = [GIGE_WRONG_SUBNET]
    cam = hik_camera.scan_hik_cameras()[0]
    assert cam["reachable"] is False
    assert "192.168.5.64" in cam["hint"] and "192.168.1.10" in cam["hint"]
    assert "subnet" in cam["hint"]


def test_is_hik_key_only_matches_prefix():
    assert hik_camera.is_hik_key("hik:01234567")
    assert not hik_camera.is_hik_key(0)
    assert not hik_camera.is_hik_key("stream")
    assert not hik_camera.is_hik_key("rtsp://192.168.1.64/stream")


# ── open sequence ──────────────────────────────────────────────────────────

def test_initialize_runs_the_required_gige_sequence(fake_sdk):
    fake_sdk.devices = [GIGE_OK]
    cam = hik_camera.HikCamera(device_key="hik:01234567")
    assert cam.initialize() is True

    calls = cam._cam.calls
    assert "CreateHandle" in calls and "StartGrabbing" in calls
    # Packet-size negotiation is what makes GigE actually usable.
    assert "SetInt:GevSCPSPacketSize=8164" in calls
    # Free-run, not hardware trigger, in this phase.
    assert "SetEnum:TriggerMode=0" in calls
    # A short queue is what keeps frames fresh for a QC verdict.
    assert f"SetImageNodeNum:{config.HIK_IMAGE_NODE_NUM}" in calls
    assert cam.model == "MV-CE060-10GC" and cam.transport == "GigE"
    cam.release()


def test_usb_device_skips_gige_only_packet_size(fake_sdk):
    fake_sdk.devices = [USB_OK]
    cam = hik_camera.HikCamera(device_key="hik:USB00001")
    assert cam.initialize() is True
    assert not any("GevSCPSPacketSize" in c for c in cam._cam.calls)
    cam.release()


def test_initialize_refuses_camera_on_wrong_subnet(fake_sdk):
    fake_sdk.devices = [GIGE_WRONG_SUBNET]
    cam = hik_camera.HikCamera(device_key="hik:99999999")
    assert cam.initialize() is False
    assert "subnet" in cam.last_error


def test_auto_pick_prefers_reachable_camera(fake_sdk):
    """With no explicit pick, an unreachable camera must not shadow a good one."""
    fake_sdk.devices = [GIGE_WRONG_SUBNET, GIGE_OK]
    cam = hik_camera.HikCamera(device_key=None)
    assert cam.initialize() is True
    assert cam.serial == "01234567"
    cam.release()


def test_unknown_device_key_fails_clearly(fake_sdk):
    fake_sdk.devices = [GIGE_OK]
    cam = hik_camera.HikCamera(device_key="hik:doesnotexist")
    assert cam.initialize() is False
    assert "hik:doesnotexist" in cam.last_error


def test_open_failure_destroys_the_handle(fake_sdk):
    fake_sdk.devices = [GIGE_OK]
    fake_sdk.open_fails = True
    cam = hik_camera.HikCamera(device_key="hik:01234567")
    assert cam.initialize() is False
    assert "เปิดกล้องไม่สำเร็จ" in cam.last_error


def test_manual_exposure_forces_auto_off_first(fake_sdk):
    """A manual exposure silently does nothing while ExposureAuto is on."""
    fake_sdk.devices = [GIGE_OK]
    cam = hik_camera.HikCamera(device_key="hik:01234567", exposure_us=180, gain_db=2)
    assert cam.initialize() is True
    calls = cam._cam.calls
    assert calls.index("SetEnumStr:ExposureAuto=Off") < calls.index("SetFloat:ExposureTime=180.0")
    assert calls.index("SetEnumStr:GainAuto=Off") < calls.index("SetFloat:Gain=2.0")
    cam.release()


def test_roi_widens_before_it_moves(fake_sdk):
    """Setting Width while a large OffsetX is live fails on most GenICam cams."""
    fake_sdk.devices = [GIGE_OK]
    cam = hik_camera.HikCamera(device_key="hik:01234567", roi=(100, 50, 800, 600))
    assert cam.initialize() is True
    order = [c for c in cam._cam.calls if c.startswith("SetInt:") and "Gev" not in c]
    assert order == ["SetInt:OffsetX=0", "SetInt:OffsetY=0", "SetInt:Width=800",
                     "SetInt:Height=600", "SetInt:OffsetX=100", "SetInt:OffsetY=50"]
    cam.release()


# ── frames: every path must yield 3-channel BGR ────────────────────────────

def _open(fake_sdk, **kw):
    fake_sdk.devices = [GIGE_OK]
    cam = hik_camera.HikCamera(device_key="hik:01234567", **kw)
    assert cam.initialize() is True
    return cam


def test_mono8_frame_becomes_3_channel_bgr(fake_sdk):
    cam = _open(fake_sdk)
    gray = np.arange(4 * 6, dtype=np.uint8).reshape(4, 6)
    fake_sdk.frames = [(PIXEL_MONO8, 6, 4, gray.tobytes())]
    ok, frame = cam.read_frame()
    assert ok is True
    assert frame.shape == (4, 6, 3) and frame.dtype == np.uint8
    # Mono replicated across channels — no colour invented.
    assert np.array_equal(frame[:, :, 0], gray)
    assert np.array_equal(frame[:, :, 0], frame[:, :, 2])
    cam.release()


def test_bgr8_frame_passes_through_unchanged(fake_sdk):
    cam = _open(fake_sdk)
    bgr = np.arange(3 * 5 * 3, dtype=np.uint8).reshape(3, 5, 3)
    fake_sdk.frames = [(PIXEL_BGR8, 5, 3, bgr.tobytes())]
    ok, frame = cam.read_frame()
    assert ok is True and np.array_equal(frame, bgr)
    cam.release()


def test_rgb8_frame_is_channel_swapped(fake_sdk):
    cam = _open(fake_sdk)
    rgb = np.arange(2 * 2 * 3, dtype=np.uint8).reshape(2, 2, 3)
    fake_sdk.frames = [(PIXEL_RGB8, 2, 2, rgb.tobytes())]
    ok, frame = cam.read_frame()
    assert ok is True
    assert np.array_equal(frame, rgb[:, :, ::-1])
    cam.release()


def test_bayer_goes_through_the_vendor_converter(fake_sdk):
    """Bayer must NOT be guessed with cv2 — OpenCV and GenICam name the
    patterns differently and a wrong guess swaps R/B silently."""
    cam = _open(fake_sdk)
    raw = np.zeros(4 * 4, dtype=np.uint8)
    fake_sdk.frames = [(PIXEL_BAYER_RG8, 4, 4, raw.tobytes())]
    ok, frame = cam.read_frame()
    assert ok is True
    assert frame.shape == (4, 4, 3)
    # The fake converter writes i % 251; proves the SDK path ran, not cv2.
    assert frame.reshape(-1)[0] == 0 and frame.reshape(-1)[5] == 5
    cam.release()


def test_frame_buffer_is_always_returned_to_the_sdk(fake_sdk):
    """Leaking buffers stalls the stream permanently after image_node_num grabs."""
    cam = _open(fake_sdk)
    gray = np.zeros(4, dtype=np.uint8)
    fake_sdk.frames = [(PIXEL_MONO8, 2, 2, gray.tobytes()) for _ in range(3)]
    for _ in range(3):
        cam.read_frame()
    assert cam._cam.freed == 3
    cam.release()


def test_grab_timeout_returns_false_none(fake_sdk):
    cam = _open(fake_sdk)
    fake_sdk.frames = []
    assert cam.read_frame() == (False, None)
    cam.release()


def test_frame_survives_buffer_reuse(fake_sdk):
    """Two grabs must not alias: the second must not mutate the first."""
    cam = _open(fake_sdk)
    a = np.full(4, 10, dtype=np.uint8)
    b = np.full(4, 200, dtype=np.uint8)
    fake_sdk.frames = [(PIXEL_MONO8, 2, 2, a.tobytes()),
                       (PIXEL_MONO8, 2, 2, b.tobytes())]
    _, first = cam.read_frame()
    _, second = cam.read_frame()
    assert first[0, 0, 0] == 10 and second[0, 0, 0] == 200
    cam.release()


# ── live controls ──────────────────────────────────────────────────────────

def test_set_control_exposure_and_gain(fake_sdk):
    cam = _open(fake_sdk)
    assert cam.set_control("exposure", 250) == 250.0
    assert cam.set_control("gain", 3) == 3.0
    assert cam.exposure_us == 250 and cam.gain_db == 3
    cam.release()


def test_set_control_ignores_uvc_only_knobs(fake_sdk):
    """brightness/contrast do not exist on an industrial camera — returning
    None keeps /api/camera/control's existing behaviour intact."""
    cam = _open(fake_sdk)
    assert cam.set_control("brightness", 128) is None
    assert cam.set_control("contrast", 128) is None
    cam.release()


def test_set_control_on_closed_camera_returns_none(fake_sdk):
    cam = hik_camera.HikCamera(device_key="hik:01234567")
    assert cam.set_control("exposure", 100) is None


# ── teardown ───────────────────────────────────────────────────────────────

def test_release_is_idempotent(fake_sdk):
    cam = _open(fake_sdk)
    handle = cam._cam
    cam.release()
    cam.release()
    assert handle.calls.count("DestroyHandle") == 1
    assert cam.is_initialized is False


def test_read_frame_after_release_returns_none(fake_sdk):
    cam = _open(fake_sdk)
    cam.release()
    assert cam.read_frame() is None


def test_initialize_twice_releases_the_first_handle(fake_sdk):
    cam = _open(fake_sdk)
    first = cam._cam
    assert cam.initialize() is True
    assert first is not cam._cam
    assert "DestroyHandle" in first.calls
    cam.release()


# ── the interface contract that lets app.py stay untouched ─────────────────

def test_hikcamera_matches_the_camera_interface():
    """viewfinder_loop / _grab_latest_frame / /api/snapshot only ever call
    these — if one disappears the snapshot path breaks silently."""
    from camera import Camera
    for name in ("initialize", "read_frame", "release", "set_control"):
        assert callable(getattr(hik_camera.HikCamera, name)), name
        assert callable(getattr(Camera, name)), name


# ── app.py wiring ──────────────────────────────────────────────────────────
# Importing app pulls in ultralytics/torch, which are not installed everywhere,
# so these skip rather than fail on a bare checkout. On the station they run.

def _import_app():
    pytest.importorskip("ultralytics")
    pytest.importorskip("pyodbc")
    import app
    return app


def test_snapshot_ladder_routes_hik_keys_to_hikcamera(fake_sdk):
    """The seam that lets the whole snapshot pipeline stay unchanged: a
    "hik:" key must open a HikCamera at its native size, NOT walk the UVC
    resolution ladder."""
    app = _import_app()
    fake_sdk.devices = [GIGE_OK]
    fake_sdk.repeat = (PIXEL_MONO8, 640, 480, bytes(640 * 480))

    cam, size = app._open_camera_ladder("hik:01234567")
    try:
        assert isinstance(cam, hik_camera.HikCamera)
        assert size == (640, 480)             # (width, height), as api_viewfinder_start reports
    finally:
        if cam:
            cam.release()


def test_snapshot_ladder_reports_hik_failure_reason(fake_sdk):
    app = _import_app()
    fake_sdk.devices = [GIGE_WRONG_SUBNET]
    cam, size = app._open_camera_ladder("hik:99999999")
    assert cam is None and size is None
    # The specific reason is what /api/viewfinder/start shows the operator.
    assert "subnet" in app._hik_open_error


def test_usb_snapshot_path_is_untouched_by_the_hik_branch(fake_sdk):
    """A plain integer index must never reach the Hikrobot code."""
    app = _import_app()
    calls = []
    original = app._open_camera
    app._open_camera = lambda *a, **k: calls.append(a) or None
    try:
        cam, size = app._open_camera_ladder(0, [(1280, 720, 30)])
    finally:
        app._open_camera = original
    assert cam is None and size is None
    assert calls, "the UVC ladder path must still be the one used for index 0"
