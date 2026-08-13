"""
Hikrobot GigE Vision / USB3 Vision industrial camera support (MVS SDK).

WHY THIS EXISTS
---------------
An RJ45 port on a Hikrobot machine-vision camera is **GigE Vision**, not an IP
camera: there is no RTSP stream and ``cv2.VideoCapture("rtsp://...")`` can never
open it. The vendor-supported path is the MVS SDK (GenICam / GigE Vision), whose
Python binding is pure ctypes — so it works on the station's Python 3.9 with no
compilation and no interpreter upgrade, and covers GigE and USB3 cameras with
the same code.

DESIGN
------
``HikCamera`` exposes exactly the interface ``camera.Camera`` does
(``initialize`` / ``read_frame`` / ``release`` / ``set_control``), so the whole
existing snapshot pipeline — ``viewfinder_loop``, ``_grab_latest_frame``,
``/api/snapshot``, the MJPEG generator and the stale-frame guard — consumes it
unchanged. This mirrors what ``camera.StreamCamera`` already does for the
browser-push source.

The SDK is an OPTIONAL dependency: if MVS is not installed, ``_load_mvs()``
returns None, this module reports "no SDK" and **every other mode of the app
keeps working exactly as before** (same failure style as pytesseract).
"""

import os
import sys
import ctypes
import importlib
import threading
from ctypes import POINTER, byref, c_ubyte, cast, memset, sizeof
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

import config
from logger import setup_logger

logger = setup_logger(__name__)

MV_OK = 0

# ── SDK discovery / loading ────────────────────────────────────────────────
# The MVS installer drops the Python binding (MvCameraControl_class.py plus its
# CameraParams_header / PixelType_header siblings) in a fixed place per OS.

_WIN_SDK_DIRS = [
    r"C:\Program Files (x86)\MVS\Development\Samples\Python\MvImport",
    r"C:\Program Files\MVS\Development\Samples\Python\MvImport",
    r"C:\Program Files (x86)\MVS\Development\Samples\Python\MvImport\MvImport",
    r"C:\Program Files (x86)\MVS\Development\Samples_Python\MvImport",
]
_WIN_RUNTIME_DIRS = [
    r"C:\Program Files (x86)\Common Files\MVS\Runtime\Win64_x64",
    r"C:\Program Files (x86)\Common Files\MVS\Runtime\Win32_i86",
]
_NIX_SDK_DIRS = [
    "/opt/MVS/Samples/64/Python/MvImport",
    "/opt/MVS/Samples/aarch64/Python/MvImport",
]

_mvs = None                  # the imported MvCameraControl_class module
_mvs_tried = False
_mvs_error = None            # human-readable reason the SDK is unavailable
_mvs_path = None             # directory the binding was imported from
_load_lock = threading.Lock()


def _candidate_sdk_dirs() -> List[str]:
    """Search order for the MvImport folder: explicit config/env first."""
    dirs = []
    for src in (os.environ.get("HIK_MVS_SDK_PATH"),
                getattr(config, "HIK_MVS_SDK_PATH", None)):
        if src:
            dirs.append(src)
    dirs.extend(_WIN_SDK_DIRS if os.name == "nt" else _NIX_SDK_DIRS)
    return dirs


def _prepare_windows_dll_path() -> None:
    """
    Make MvCameraControl.dll findable. The MVS installer normally puts its
    runtime on PATH, but a machine where MVS was installed for another user (or
    where PATH was trimmed) still has the DLL on disk — pointing at it here
    turns a confusing "DLL not found" into a working import. Best-effort only.
    """
    if os.name != "nt":
        return
    for d in _WIN_RUNTIME_DIRS:
        if not os.path.isdir(d):
            continue
        if d not in os.environ.get("PATH", ""):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        add_dll_dir = getattr(os, "add_dll_directory", None)   # py3.8+
        if add_dll_dir is not None:
            try:
                add_dll_dir(d)
            except OSError:
                pass


def _load_mvs():
    """
    Import the MVS Python binding once. Returns the module, or None if the SDK
    is not installed / failed to load. NEVER raises — a missing SDK must degrade
    to "this camera type is unavailable", not break the app.
    """
    global _mvs, _mvs_tried, _mvs_error, _mvs_path

    with _load_lock:
        if _mvs_tried:
            return _mvs
        _mvs_tried = True

        # Already importable (SDK on PYTHONPATH, or a test injected a fake).
        if "MvCameraControl_class" in sys.modules:
            _mvs = sys.modules["MvCameraControl_class"]
            _mvs_path = getattr(_mvs, "__file__", "(preloaded)")
            return _mvs

        _prepare_windows_dll_path()
        tried = []
        for d in _candidate_sdk_dirs():
            if not os.path.isdir(d):
                tried.append(f"{d} (not found)")
                continue
            if d not in sys.path:
                sys.path.insert(0, d)
            try:
                _mvs = importlib.import_module("MvCameraControl_class")
                _mvs_path = d
                logger.info(f"Hikrobot MVS SDK loaded from {d}")
                break
            except Exception as e:      # ImportError, OSError (DLL), anything
                tried.append(f"{d} ({e})")
                if d in sys.path:
                    sys.path.remove(d)

        if _mvs is None:
            _mvs_error = ("ไม่พบ MVS SDK — ติดตั้ง MVS for Windows จาก hikrobotics.com "
                          "หรือกำหนด HIK_MVS_SDK_PATH ให้ชี้ไปที่โฟลเดอร์ MvImport")
            logger.info(f"Hikrobot MVS SDK unavailable. Searched: {'; '.join(tried)}")
        else:
            # SDK 4.3+ requires an explicit init; older versions have no such
            # symbol, so this is best-effort by design.
            init = getattr(_mvs.MvCamera, "MV_CC_Initialize", None)
            if callable(init):
                try:
                    init()
                except Exception as e:
                    logger.warning(f"MV_CC_Initialize failed ({e}); continuing.")
        return _mvs


def _reset_sdk_cache():
    """Testing hook: forget the load result so the next call re-resolves."""
    global _mvs, _mvs_tried, _mvs_error, _mvs_path
    with _load_lock:
        _mvs, _mvs_tried, _mvs_error, _mvs_path = None, False, None, None


def sdk_status() -> Dict:
    """Report SDK availability for the UI / diagnostics (never raises)."""
    mvs = _load_mvs()
    return {
        "available": mvs is not None,
        "path": _mvs_path,
        "message": None if mvs is not None else _mvs_error,
    }


# ── small helpers ──────────────────────────────────────────────────────────

def _c_str(buf) -> str:
    """ctypes char array → clean str (SDK pads with NULs, sometimes garbage)."""
    try:
        raw = bytes(bytearray(buf))
    except Exception:
        return ""
    return raw.split(b"\x00", 1)[0].decode("utf-8", "ignore").strip()


def _ip_str(n: int) -> str:
    n = int(n) & 0xFFFFFFFF
    return f"{(n >> 24) & 0xFF}.{(n >> 16) & 0xFF}.{(n >> 8) & 0xFF}.{n & 0xFF}"


def _same_subnet(cam_ip: int, host_ip: int, mask: int) -> bool:
    """
    A GigE camera whose IP is on a different subnet than the NIC that found it
    is still ENUMERATED but cannot be opened — the #1 first-time GigE trap. A
    zero/absent mask means we can't tell, so don't cry wolf.
    """
    mask = int(mask) & 0xFFFFFFFF
    if not mask:
        return True
    return (int(cam_ip) & mask) == (int(host_ip) & mask)


def is_hik_key(value) -> bool:
    """True if a camera_index refers to a Hikrobot device (``hik:<serial>``)."""
    prefix = getattr(config, "HIK_SOURCE_PREFIX", "hik:")
    return isinstance(value, str) and value.startswith(prefix)


def _key_for(serial: str, index: int) -> str:
    prefix = getattr(config, "HIK_SOURCE_PREFIX", "hik:")
    return f"{prefix}{serial}" if serial else f"{prefix}#{index}"


def _device_entry(mvs, info, index: int) -> Optional[Dict]:
    """Flatten one MV_CC_DEVICE_INFO into a plain dict for the API/UI."""
    tlayer = int(info.nTLayerType)
    if tlayer == int(mvs.MV_GIGE_DEVICE):
        g = info.SpecialInfo.stGigEInfo
        serial = _c_str(g.chSerialNumber)
        cam_ip, host_ip = int(g.nCurrentIp), int(g.nNetExport)
        mask = int(getattr(g, "nCurrentSubNetMask", 0))
        reachable = _same_subnet(cam_ip, host_ip, mask)
        return {
            "key": _key_for(serial, index),
            "model": _c_str(g.chModelName),
            "serial": serial,
            "user_name": _c_str(g.chUserDefinedName),
            "version": _c_str(g.chDeviceVersion),
            "transport": "GigE",
            "ip": _ip_str(cam_ip),
            "host_ip": _ip_str(host_ip),
            "reachable": reachable,
            "hint": None if reachable else (
                f"กล้องอยู่ IP {_ip_str(cam_ip)} แต่การ์ดแลนของเครื่องอยู่ "
                f"{_ip_str(host_ip)} — คนละ subnet จึงเปิดไม่ได้. แก้โดยตั้ง IP "
                f"การ์ดแลนให้อยู่วงเดียวกับกล้อง หรือแก้ IP กล้องในโปรแกรม MVS"
            ),
        }

    if tlayer == int(getattr(mvs, "MV_USB_DEVICE", -1)):
        u = info.SpecialInfo.stUsb3VInfo
        serial = _c_str(u.chSerialNumber)
        return {
            "key": _key_for(serial, index),
            "model": _c_str(u.chModelName),
            "serial": serial,
            "user_name": _c_str(u.chUserDefinedName),
            "version": _c_str(u.chDeviceVersion),
            "transport": "USB3",
            "ip": None,
            "host_ip": None,
            "reachable": True,
            "hint": None,
        }
    return None


def scan_hik_cameras() -> List[Dict]:
    """
    Enumerate Hikrobot GigE + USB3 cameras. Returns [] when the SDK is missing
    or nothing is connected — never raises, so a scan can be wired straight to
    an endpoint.
    """
    mvs = _load_mvs()
    if mvs is None:
        return []
    try:
        dev_list = mvs.MV_CC_DEVICE_INFO_LIST()
        memset(byref(dev_list), 0, sizeof(dev_list))
        layers = int(mvs.MV_GIGE_DEVICE) | int(getattr(mvs, "MV_USB_DEVICE", 0))
        ret = mvs.MvCamera.MV_CC_EnumDevices(layers, dev_list)
        if ret != MV_OK:
            logger.warning(f"MV_CC_EnumDevices failed (0x{ret & 0xFFFFFFFF:08x})")
            return []

        found = []
        for i in range(int(dev_list.nDeviceNum)):
            info = cast(dev_list.pDeviceInfo[i],
                        POINTER(mvs.MV_CC_DEVICE_INFO)).contents
            entry = _device_entry(mvs, info, i)
            if entry is None:
                continue
            label = entry["user_name"] or entry["model"] or entry["serial"] or "Hikrobot"
            where = entry["ip"] or entry["transport"]
            entry["name"] = f"{label} ({where})" + ("" if entry["reachable"] else " ⚠ คนละ subnet")
            found.append(entry)
        return found
    except Exception as e:
        logger.error(f"Hikrobot device scan failed: {e}", exc_info=True)
        return []


# ── the camera ─────────────────────────────────────────────────────────────

class HikCamera:
    """
    Hikrobot industrial camera with the same duck-typed interface as
    ``camera.Camera``, so the existing snapshot pipeline uses it unchanged.

    ``read_frame()`` returns ``(True, bgr)`` on success and ``(False, None)`` on
    a grab timeout — the same contract a UVC camera honours when it has no new
    frame, which is what makes the existing loops idle correctly instead of
    spinning or re-inferring a duplicate.
    """

    def __init__(self, device_key=None, exposure_us=None, gain_db=None,
                 auto_exposure=None, auto_gain=None, roi=None,
                 frame_rate=None, grab_timeout_ms=None, image_node_num=None):
        self.device_key = device_key
        # Interface symmetry with Camera (logging/status code reads this).
        self.camera_index = device_key

        def _cfg(value, name, default=None):
            return value if value is not None else getattr(config, name, default)

        self.auto_exposure  = _cfg(auto_exposure, "HIK_EXPOSURE_AUTO")
        self.exposure_us    = _cfg(exposure_us, "HIK_EXPOSURE_US")
        self.auto_gain      = _cfg(auto_gain, "HIK_GAIN_AUTO")
        self.gain_db        = _cfg(gain_db, "HIK_GAIN_DB")
        self.roi            = _cfg(roi, "HIK_ROI")
        self.frame_rate     = _cfg(frame_rate, "HIK_FRAME_RATE")
        self.grab_timeout_ms = int(_cfg(grab_timeout_ms, "HIK_GRAB_TIMEOUT_MS", 1000))
        self.image_node_num = int(_cfg(image_node_num, "HIK_IMAGE_NODE_NUM", 3))

        self.is_initialized = False
        self.width = None
        self.height = None
        self.transport = None
        self.model = None
        self.serial = None
        self.last_error = None

        self._mvs = None
        self._cam = None            # MvCamera instance (the open handle)
        self._grabbing = False
        self._convert_buf = None
        self._convert_warned = False
        # VideoCapture isn't thread-safe and neither is a camera handle: Flask
        # runs threaded, so a live exposure tweak can land while the viewfinder
        # loop is mid-grab. Same guard camera.Camera uses.
        self._cap_lock = threading.Lock()

    # ── open ───────────────────────────────────────────────────────────────

    def _find_device(self, mvs):
        """Locate the requested device (or the first one) in a fresh enum."""
        dev_list = mvs.MV_CC_DEVICE_INFO_LIST()
        memset(byref(dev_list), 0, sizeof(dev_list))
        layers = int(mvs.MV_GIGE_DEVICE) | int(getattr(mvs, "MV_USB_DEVICE", 0))
        ret = mvs.MvCamera.MV_CC_EnumDevices(layers, dev_list)
        if ret != MV_OK:
            self.last_error = f"ค้นหากล้องไม่สำเร็จ (MV_CC_EnumDevices 0x{ret & 0xFFFFFFFF:08x})"
            return None, None
        count = int(dev_list.nDeviceNum)
        if count == 0:
            self.last_error = "ไม่พบกล้อง Hikrobot — ตรวจสายแลน/ไฟ PoE และเปิดโปรแกรม MVS ดูก่อน"
            return None, None

        wanted = self.device_key if is_hik_key(self.device_key) else None
        matches = []
        for i in range(count):
            info = cast(dev_list.pDeviceInfo[i],
                        POINTER(mvs.MV_CC_DEVICE_INFO)).contents
            entry = _device_entry(mvs, info, i)
            if entry is None:
                continue
            if wanted is None or entry["key"] == wanted:
                matches.append((info, entry))

        if not matches:
            self.last_error = (f"ไม่พบกล้องที่เลือก ({self.device_key}) — "
                               "กดรีเฟรชรายการกล้องแล้วลองใหม่")
            return None, None
        # With no explicit pick, don't hand back a camera we already know is
        # unreachable while a working one sits next to it in the list.
        for info, entry in matches:
            if entry["reachable"]:
                return info, entry
        self.last_error = matches[0][1]["hint"]
        return None, None

    def initialize(self) -> bool:
        """Open the camera and start streaming. Returns False (never raises) on
        any failure, leaving no handle behind."""
        if self.is_initialized:
            self.release()

        mvs = _load_mvs()
        if mvs is None:
            self.last_error = _mvs_error
            logger.error(f"Hikrobot camera unavailable: {self.last_error}")
            return False
        self._mvs = mvs

        info, entry = self._find_device(mvs)
        if info is None:
            logger.error(f"Hikrobot open failed: {self.last_error}")
            return False

        self.model = entry["model"]
        self.serial = entry["serial"]
        self.transport = entry["transport"]

        cam = mvs.MvCamera()
        ret = cam.MV_CC_CreateHandle(info)
        if ret != MV_OK:
            self.last_error = f"สร้าง handle ไม่สำเร็จ (0x{ret & 0xFFFFFFFF:08x})"
            logger.error(self.last_error)
            return False

        ret = cam.MV_CC_OpenDevice(int(getattr(mvs, "MV_ACCESS_Exclusive", 1)), 0)
        if ret != MV_OK:
            self.last_error = (f"เปิดกล้องไม่สำเร็จ (0x{ret & 0xFFFFFFFF:08x}) — "
                               "อาจถูกโปรแกรมอื่น (เช่น MVS client) เปิดค้างอยู่")
            logger.error(self.last_error)
            cam.MV_CC_DestroyHandle()
            return False

        self._cam = cam
        try:
            self._apply_settings()
            ret = cam.MV_CC_StartGrabbing()
            if ret != MV_OK:
                self.last_error = f"เริ่มรับภาพไม่สำเร็จ (MV_CC_StartGrabbing 0x{ret & 0xFFFFFFFF:08x})"
                logger.error(self.last_error)
                self.release()
                return False
            self._grabbing = True
        except Exception as e:
            self.last_error = f"ตั้งค่ากล้องไม่สำเร็จ: {e}"
            logger.error(self.last_error, exc_info=True)
            self.release()
            return False

        self.width = self._get_int("Width")
        self.height = self._get_int("Height")
        self.is_initialized = True
        logger.info(
            f"Hikrobot camera ready ({self.model or '?'} sn={self.serial or '?'}, "
            f"{self.transport}, {self.width}x{self.height}, "
            f"exposure={self._get_float('ExposureTime')}us, gain={self._get_float('Gain')}dB)"
        )
        return True

    def _apply_settings(self) -> None:
        """
        Configure the camera. Only the CRITICAL steps (done by initialize) can
        abort the open — every knob here is best-effort: a model that lacks a
        node (mono cameras have no BalanceWhite, some have no
        AcquisitionFrameRate) must still stream with its own defaults rather
        than refuse to open.
        """
        cam, mvs = self._cam, self._mvs

        # GigE: negotiating the largest packet the link supports is the single
        # biggest throughput/stability factor. Skipping it gives torn frames and
        # a fraction of the bandwidth. USB3 has no such node.
        if self.transport == "GigE" and getattr(config, "HIK_PACKET_SIZE_AUTO", True):
            try:
                size = cam.MV_CC_GetOptimalPacketSize()
                if size > 0:
                    ret = cam.MV_CC_SetIntValue("GevSCPSPacketSize", size)
                    logger.info(f"GevSCPSPacketSize set to {size} (ret={ret})")
                else:
                    logger.warning(f"MV_CC_GetOptimalPacketSize returned {size}; keeping default")
            except Exception as e:
                logger.warning(f"Packet-size negotiation skipped ({e})")

        # Free-run acquisition. Hardware trigger (photo-eye) is a later phase —
        # see docs/PLAN_LINE_DENT_INSPECTION.md §4.
        self._set_enum("TriggerMode", int(getattr(mvs, "MV_TRIGGER_MODE_OFF", 0)))

        # ROI must be widened before it is moved: setting Width while a large
        # OffsetX is still in effect fails on most GenICam cameras.
        if self.roi:
            try:
                ox, oy, w, h = (int(v) for v in self.roi)
                self._set_int("OffsetX", 0)
                self._set_int("OffsetY", 0)
                self._set_int("Width", w)
                self._set_int("Height", h)
                self._set_int("OffsetX", ox)
                self._set_int("OffsetY", oy)
            except Exception as e:
                logger.warning(f"HIK_ROI={self.roi!r} not applied ({e}); using full sensor")

        if self.auto_exposure is not None:
            self._set_enum_str("ExposureAuto", "Continuous" if self.auto_exposure else "Off")
        if self.exposure_us is not None:
            # A manual exposure only sticks once auto is off.
            if self.auto_exposure is None:
                self._set_enum_str("ExposureAuto", "Off")
            self._set_float("ExposureTime", float(self.exposure_us))

        if self.auto_gain is not None:
            self._set_enum_str("GainAuto", "Continuous" if self.auto_gain else "Off")
        if self.gain_db is not None:
            if self.auto_gain is None:
                self._set_enum_str("GainAuto", "Off")
            self._set_float("Gain", float(self.gain_db))

        if self.frame_rate:
            self._set_bool("AcquisitionFrameRateEnable", True)
            self._set_float("AcquisitionFrameRate", float(self.frame_rate))

        # A short internal queue means read_frame() hands back a FRESH frame.
        # A long one silently serves seconds-old images — fatal for a QC system
        # (and it would defeat the snapshot stale-frame guard, which measures
        # publish time, not capture time).
        try:
            cam.MV_CC_SetImageNodeNum(self.image_node_num)
        except Exception as e:
            logger.warning(f"MV_CC_SetImageNodeNum skipped ({e})")

    # ── GenICam node helpers (best-effort, never raise) ────────────────────

    def _set_int(self, node, value):
        ret = self._cam.MV_CC_SetIntValue(node, int(value))
        if ret != MV_OK:
            logger.warning(f"Set {node}={value} failed (0x{ret & 0xFFFFFFFF:08x})")
        return ret == MV_OK

    def _set_float(self, node, value):
        ret = self._cam.MV_CC_SetFloatValue(node, float(value))
        if ret != MV_OK:
            logger.warning(f"Set {node}={value} failed (0x{ret & 0xFFFFFFFF:08x})")
        return ret == MV_OK

    def _set_enum(self, node, value):
        ret = self._cam.MV_CC_SetEnumValue(node, int(value))
        if ret != MV_OK:
            logger.warning(f"Set {node}={value} failed (0x{ret & 0xFFFFFFFF:08x})")
        return ret == MV_OK

    def _set_enum_str(self, node, value):
        try:
            ret = self._cam.MV_CC_SetEnumValueByString(node, value)
        except Exception as e:
            logger.warning(f"Set {node}='{value}' raised ({e})")
            return False
        if ret != MV_OK:
            logger.warning(f"Set {node}='{value}' failed (0x{ret & 0xFFFFFFFF:08x})")
        return ret == MV_OK

    def _set_bool(self, node, value):
        ret = self._cam.MV_CC_SetBoolValue(node, bool(value))
        if ret != MV_OK:
            logger.warning(f"Set {node}={value} failed (0x{ret & 0xFFFFFFFF:08x})")
        return ret == MV_OK

    def _get_int(self, node):
        try:
            v = self._mvs.MVCC_INTVALUE()
            memset(byref(v), 0, sizeof(v))
            if self._cam.MV_CC_GetIntValue(node, v) == MV_OK:
                return int(v.nCurValue)
        except Exception:
            pass
        return None

    def _get_float(self, node):
        try:
            v = self._mvs.MVCC_FLOATVALUE()
            memset(byref(v), 0, sizeof(v))
            if self._cam.MV_CC_GetFloatValue(node, v) == MV_OK:
                return round(float(v.fCurValue), 2)
        except Exception:
            pass
        return None

    # ── frames ─────────────────────────────────────────────────────────────

    def read_frame(self) -> Optional[Tuple[bool, any]]:
        """Grab one frame as BGR. (False, None) on timeout — same contract as
        camera.Camera, so the existing loops idle instead of spinning."""
        if not self.is_initialized or self._cam is None:
            return None

        mvs = self._mvs
        frame_out = mvs.MV_FRAME_OUT()
        memset(byref(frame_out), 0, sizeof(frame_out))
        try:
            with self._cap_lock:
                ret = self._cam.MV_CC_GetImageBuffer(frame_out, self.grab_timeout_ms)
                if ret != MV_OK:
                    return (False, None)
                try:
                    bgr = self._to_bgr(frame_out)
                finally:
                    # The SDK owns this buffer and only has image_node_num of
                    # them: failing to return one stalls the stream for good.
                    self._cam.MV_CC_FreeImageBuffer(frame_out)
        except Exception as e:
            logger.error(f"Hikrobot frame grab failed: {e}")
            return (False, None)

        if bgr is None:
            return (False, None)
        return (True, bgr)

    def _to_bgr(self, frame_out):
        """
        Convert the grabbed frame to a 3-channel BGR ndarray (what YOLO and the
        whole existing pipeline expect).

        ⚠️ Colour cameras stream Bayer, and OpenCV's Bayer names do NOT map 1:1
        onto GenICam's (cv2.COLOR_BayerRG2BGR is not GenICam BayerRG). Guessing
        wrong swaps R and B across the whole image — silently, with no error,
        feeding the model colours it never trained on. So Bayer and anything
        exotic goes through the vendor's own MV_CC_ConvertPixelType, which knows
        its own convention. Only the two unambiguous layouts take a fast path.
        """
        mvs = self._mvs
        info = frame_out.stFrameInfo
        w = int(info.nWidth) or int(getattr(info, "nExtendWidth", 0) or 0)
        h = int(info.nHeight) or int(getattr(info, "nExtendHeight", 0) or 0)
        length = int(info.nFrameLen)
        if w <= 0 or h <= 0 or length <= 0:
            return None

        pixel_type = int(info.enPixelType)
        mono8 = int(getattr(mvs, "PixelType_Gvsp_Mono8", 0x01080001))
        bgr8 = int(getattr(mvs, "PixelType_Gvsp_BGR8_Packed", 0x02180015))
        rgb8 = int(getattr(mvs, "PixelType_Gvsp_RGB8_Packed", 0x02180014))

        src = np.ctypeslib.as_array(frame_out.pBufAddr, shape=(length,))

        if pixel_type == mono8 and length >= w * h:
            # Unambiguous, and YOLO needs 3 channels. cvtColor copies, so the
            # result stays valid after MV_CC_FreeImageBuffer.
            return cv2.cvtColor(src[:w * h].reshape(h, w), cv2.COLOR_GRAY2BGR)
        if pixel_type == bgr8 and length >= w * h * 3:
            return src[:w * h * 3].reshape(h, w, 3).copy()
        if pixel_type == rgb8 and length >= w * h * 3:
            return cv2.cvtColor(src[:w * h * 3].reshape(h, w, 3), cv2.COLOR_RGB2BGR)

        return self._sdk_convert_bgr(frame_out, w, h, length, pixel_type)

    def _sdk_convert_bgr(self, frame_out, w, h, length, pixel_type):
        mvs = self._mvs
        dst_len = w * h * 3
        if self._convert_buf is None or len(self._convert_buf) < dst_len:
            self._convert_buf = (c_ubyte * dst_len)()

        # Newer SDKs renamed this struct; both spellings exist in the wild.
        param_cls = getattr(mvs, "MV_CC_PIXEL_CONVERT_PARAM", None) or \
            getattr(mvs, "MV_CC_PIXEL_CONVERT_PARAM_EX")
        param = param_cls()
        memset(byref(param), 0, sizeof(param))
        param.nWidth = w
        param.nHeight = h
        param.pSrcData = frame_out.pBufAddr
        param.nSrcDataLen = length
        param.enSrcPixelType = pixel_type
        param.enDstPixelType = int(getattr(mvs, "PixelType_Gvsp_BGR8_Packed", 0x02180015))
        param.pDstBuffer = cast(self._convert_buf, POINTER(c_ubyte))
        param.nDstBufferSize = dst_len

        ret = self._cam.MV_CC_ConvertPixelType(param)
        if ret != MV_OK:
            if not self._convert_warned:      # once per session, not per frame
                self._convert_warned = True
                logger.error(
                    f"MV_CC_ConvertPixelType failed (0x{ret & 0xFFFFFFFF:08x}) for "
                    f"pixel type 0x{pixel_type:08x}. Set PixelFormat to Mono8/BGR8 "
                    "in the MVS client as a workaround."
                )
            return None
        # The buffer is reused every frame → copy before handing it out.
        return np.frombuffer(self._convert_buf, dtype=np.uint8,
                             count=dst_len).reshape(h, w, 3).copy()

    # ── live controls ──────────────────────────────────────────────────────

    # Industrial cameras expose exposure/gain, not the UVC brightness/contrast
    # knobs. Returning None for those leaves the existing sliders' behaviour
    # (and their endpoint's error message) exactly as it was.
    _CONTROL_NODES = {"exposure": "ExposureTime", "gain": "Gain"}

    def set_control(self, name, value):
        """Adjust exposure (µs) or gain (dB) on the running camera. Returns the
        value the camera reports back, or None if unsupported/not open."""
        node = self._CONTROL_NODES.get(name)
        if node is None or self._cam is None or not self.is_initialized:
            return None
        try:
            with self._cap_lock:
                # Auto would immediately override a manual value.
                self._set_enum_str("ExposureAuto" if name == "exposure" else "GainAuto", "Off")
                if self._cam.MV_CC_SetFloatValue(node, float(value)) != MV_OK:
                    return None
                actual = self._get_float(node)
            if name == "exposure":
                self.exposure_us = value
            else:
                self.gain_db = value
            return actual
        except Exception as e:
            logger.warning(f"HikCamera.set_control({name}) failed ({e}).")
            return None

    def get_info(self) -> Dict:
        """Live camera facts for the diagnostics tool / status endpoints."""
        return {
            "model": self.model,
            "serial": self.serial,
            "transport": self.transport,
            "width": self._get_int("Width") if self._cam else self.width,
            "height": self._get_int("Height") if self._cam else self.height,
            "exposure_us": self._get_float("ExposureTime") if self._cam else None,
            "gain_db": self._get_float("Gain") if self._cam else None,
            "frame_rate": self._get_float("ResultingFrameRate") if self._cam else None,
            "packet_size": self._get_int("GevSCPSPacketSize") if self._cam else None,
        }

    # ── teardown ───────────────────────────────────────────────────────────

    def release(self):
        """Stop, close and destroy the handle. Idempotent and never raises."""
        cam = self._cam
        self.is_initialized = False
        if cam is None:
            return
        for step, fn in (("MV_CC_StopGrabbing", cam.MV_CC_StopGrabbing if self._grabbing else None),
                         ("MV_CC_CloseDevice", cam.MV_CC_CloseDevice),
                         ("MV_CC_DestroyHandle", cam.MV_CC_DestroyHandle)):
            if fn is None:
                continue
            try:
                fn()
            except Exception as e:
                logger.warning(f"{step} failed during release ({e})")
        self._grabbing = False
        self._cam = None
        self._convert_buf = None
        logger.info("Hikrobot camera released")

    def __del__(self):
        try:
            self.release()
        except Exception:
            pass
