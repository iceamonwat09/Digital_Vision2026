"""
Camera module for handling USB webcams and RTSP IP camera streams.
Provides camera detection, initialization, and frame capture functionality.
"""

import cv2
import os
import threading
import numpy as np
from typing import Optional, Tuple, List, Dict, Union
import config
from logger import setup_logger

logger = setup_logger(__name__)

# Priority-ordered backends for each OS
_BACKENDS_WIN   = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
_BACKENDS_LINUX = [cv2.CAP_ANY, cv2.CAP_V4L2]
_BACKENDS = _BACKENDS_WIN if os.name == 'nt' else _BACKENDS_LINUX

_BACKEND_NAMES = {
    cv2.CAP_DSHOW:  "DirectShow",
    cv2.CAP_MSMF:   "MSMF",
    cv2.CAP_ANY:    "Auto",
    cv2.CAP_V4L2:   "V4L2",
    cv2.CAP_FFMPEG: "FFMPEG",
}

_RTSP_PREFIXES = ('rtsp://', 'rtsps://', 'rtmp://', 'http://', 'https://')


def _is_url(value) -> bool:
    return isinstance(value, str) and value.lower().startswith(_RTSP_PREFIXES)


def _probe_index(index: int) -> bool:
    """
    Return True if the camera index is readable.

    Defensive on purpose: a SINGLE guarded open with the platform default
    backend. The old version fell back through every backend (incl. CAP_DSHOW
    and CAP_ANY) for each index, which on some Windows + OpenCV 4.x builds spams
    the obsensor probe and throws native DSHOW C++ exceptions when probing
    non-existent indices — repeated on every page load this could abort the
    whole process. One backend, wrapped in try/except, keeps scanning safe.
    """
    cap = None
    try:
        cap = cv2.VideoCapture(index)
        if cap is not None and cap.isOpened():
            ret, _ = cap.read()
            return bool(ret)
        return False
    except Exception as e:  # cv2.error and friends — never let a probe crash us
        logger.warning(f"Probe of camera index {index} failed: {e}")
        return False
    finally:
        if cap is not None:
            cap.release()


def scan_cameras_fast(max_index: int = 4, skip_indices: list = None) -> List[Dict]:
    """
    List available local cameras.  skip_indices lets callers exclude already-open cameras.
    """
    skip = set(skip_indices or [])
    cameras = []

    if os.name == 'nt':
        try:
            from pygrabber.dshow_graph import FilterGraph
            graph = FilterGraph()
            devices = graph.get_input_devices()
            for i, name in enumerate(devices):
                cameras.append({"id": i, "name": name})
            if cameras:
                return cameras
        except Exception as e:
            logger.warning(f"pygrabber unavailable ({e}), falling back to OpenCV probe.")

    for i in range(max_index + 1):
        if i in skip:
            cameras.append({"id": i, "name": f"Camera {i} (in use)"})
            continue
        if _probe_index(i):
            cameras.append({"id": i, "name": f"Camera {i}"})
    return cameras


class Camera:
    """
    Camera handler supporting USB webcams (index) and IP cameras (RTSP URL).
    """

    def __init__(self, camera_index: Union[int, str] = None,
                 width: int = None, height: int = None, fps: int = None,
                 auto_exposure=None, exposure=None, brightness=None, contrast=None):
        raw = camera_index if camera_index is not None else config.CAMERA_INDEX
        # Normalise numeric strings ("0", "1") → int
        if isinstance(raw, str) and raw.isdigit():
            raw = int(raw)
        self.camera_index = raw
        # Per-instance capture resolution. Defaults to the live-stream config;
        # snapshot mode passes the high-res SNAPSHOT_CAMERA_* values.
        self.width  = width  if width  is not None else config.CAMERA_WIDTH
        self.height = height if height is not None else config.CAMERA_HEIGHT
        self.fps    = fps    if fps    is not None else config.CAMERA_FPS
        # Optional exposure control (opt-in). Passed only by the live-detection
        # camera; None on both means "leave the camera/driver defaults alone"
        # (so snapshot/viewfinder are never touched).
        self.auto_exposure = auto_exposure
        self.exposure = exposure
        # Live image controls (UVC 0-255), opt-in; None = leave the camera default.
        # BRIGHTNESS is verified to work on the station camera; CONTRAST is an
        # experimental knob (may help or hurt detection — must be tested).
        self.brightness = brightness
        self.contrast = contrast
        self.cap: Optional[cv2.VideoCapture] = None
        self.is_initialized = False
        # Serialize cap access: capture_loop reads while a live brightness tweak
        # may cap.set() from the Flask thread (VideoCapture isn't thread-safe).
        self._cap_lock = threading.Lock()

    def _apply_exposure(self, cap) -> None:
        """
        Apply manual/auto exposure + brightness if requested (opt-in). Best-effort:
        many USB cameras / Windows backends silently ignore or reject these — so
        every set is guarded and a failure just falls back to the camera's own
        default (never raises, never blocks initialization).
        """
        ae, ev, br, ct = self.auto_exposure, self.exposure, self.brightness, self.contrast
        if ae is None and ev is None and br is None and ct is None:
            return                       # nothing requested → leave defaults
        try:
            if ae is not None:
                # Windows DSHOW/MSMF convention (varies by driver!):
                #   0.75 ≈ auto, 0.25 ≈ manual. Must switch to manual before a
                #   manual exposure value will "stick".
                cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75 if ae else 0.25)
            if ev is not None:
                cap.set(cv2.CAP_PROP_EXPOSURE, float(ev))
            if br is not None:
                cap.set(cv2.CAP_PROP_BRIGHTNESS, float(br))
            if ct is not None:
                cap.set(cv2.CAP_PROP_CONTRAST, float(ct))
            logger.info(
                f"Camera controls applied (auto_exposure={ae}, exposure={ev}, "
                f"brightness={br}, contrast={ct}; reports "
                f"brightness={cap.get(cv2.CAP_PROP_BRIGHTNESS)}, "
                f"contrast={cap.get(cv2.CAP_PROP_CONTRAST)})."
            )
        except Exception as e:
            logger.warning(f"Camera control set failed ({e}); using defaults.")

    # ชื่อ control → OpenCV prop (ที่ปรับสดได้ผ่านสไลเดอร์ UI)
    _CONTROL_PROPS = {
        "brightness": cv2.CAP_PROP_BRIGHTNESS,
        "contrast":   cv2.CAP_PROP_CONTRAST,
    }

    def set_control(self, name, value):
        """
        Adjust an image control (brightness/contrast) on the running camera live
        (no reopen). Returns the value the camera reports back, or None if the
        control is unknown / there's no open capture. Guarded + serialized with
        capture_loop via _cap_lock so a bad value can never crash the stream.
        """
        prop = self._CONTROL_PROPS.get(name)
        cap = self.cap
        if prop is None or cap is None:
            return None
        try:
            with self._cap_lock:
                cap.set(prop, float(value))
                if name == "brightness":
                    self.brightness = value
                elif name == "contrast":
                    self.contrast = value
                return cap.get(prop)
        except Exception as e:
            logger.warning(f"set_control({name}) failed ({e}).")
            return None

    def initialize(self) -> bool:
        if self.is_initialized:
            self.release()
        if _is_url(self.camera_index):
            return self._initialize_rtsp()
        return self._initialize_usb()

    def _initialize_usb(self) -> bool:
        """Open a local USB/built-in camera by index."""
        logger.info(f"Initializing USB camera index={self.camera_index} ...")

        # Try the platform default backend FIRST. On Windows that is MSMF, which
        # is the one that reliably opens UVC cameras here; the explicit backends
        # (DSHOW, …) are only a fallback. NOTE: forcing DSHOW first was tried to
        # unlock >720p via MJPG, but on this deployment DSHOW raises native
        # "unknown C++ exception" and can't capture by index — so default-first
        # is both the stable AND the working order. (Camera tops out at 720p over
        # USB here regardless; high-res is not worth crashing for.)
        attempts = [None] + _BACKENDS

        for backend in attempts:
            try:
                cap = (cv2.VideoCapture(self.camera_index) if backend is None
                       else cv2.VideoCapture(self.camera_index, backend))
            except Exception as e:  # never let a backend's native error crash us
                logger.warning(f"Opening camera with backend {backend} raised: {e}")
                continue
            if not cap.isOpened():
                cap.release()
                continue

            # FourCC MUST be set before width/height for most UVC cameras —
            # MJPEG is what lets this 8MP camera reach high resolution over
            # USB 2.0 (YUY2 is uncompressed and bandwidth-capped to ~640x480).
            fourcc = getattr(config, "CAMERA_FOURCC", None)
            if fourcc:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            cap.set(cv2.CAP_PROP_FPS,          self.fps)
            cap.set(cv2.CAP_PROP_BUFFERSIZE,   1)
            # Opt-in exposure control (live camera only; no-op unless configured).
            self._apply_exposure(cap)

            for _ in range(5):
                cap.read()

            ret, frame = cap.read()
            if ret and frame is not None:
                self.cap = cap
                self.is_initialized = True
                bname = "Default" if backend is None else _BACKEND_NAMES.get(backend, str(backend))
                ah, aw = frame.shape[:2]
                raw_cc = int(cap.get(cv2.CAP_PROP_FOURCC))
                cc = "".join(chr((raw_cc >> (8 * i)) & 0xFF) for i in range(4)).strip()
                logger.info(
                    f"USB camera ready (index={self.camera_index}, backend={bname}, "
                    f"fourcc={cc or '?'}, requested={self.width}x{self.height}, "
                    f"actual={aw}x{ah})"
                )
                if (aw, ah) != (self.width, self.height):
                    logger.warning(
                        f"Camera delivered {aw}x{ah} (asked {self.width}x{self.height}). "
                        "If lower than expected, the camera may have ignored the mode — "
                        "check MJPEG support / USB bandwidth."
                    )
                return True

            cap.release()

        logger.error(
            f"Cannot open camera index={self.camera_index}. "
            "Is it connected? Is another app (Teams/Zoom) using it?"
        )
        available = scan_cameras_fast()
        if available:
            ids = [c["id"] for c in available]
            logger.error(f"Available camera indices: {ids}.")
        return False

    def _initialize_rtsp(self) -> bool:
        """Connect to an RTSP / IP camera stream."""
        url = self.camera_index
        logger.info(f"Connecting to IP camera: {url}")

        # Try FFMPEG backend first (best RTSP support), then default
        for backend in [cv2.CAP_FFMPEG, None]:
            cap = (cv2.VideoCapture(url) if backend is None
                   else cv2.VideoCapture(url, backend))
            if not cap.isOpened():
                cap.release()
                continue

            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            # Warm-up: flush initial buffered frames
            for _ in range(3):
                cap.read()

            ret, frame = cap.read()
            if ret and frame is not None:
                self.cap = cap
                self.is_initialized = True
                bname = _BACKEND_NAMES.get(backend, "Default") if backend else "Default"
                logger.info(f"IP camera connected (backend={bname}): {url}")
                return True

            cap.release()

        logger.error(f"Cannot connect to IP camera: {url}")
        logger.error("Check: URL format, credentials, network access, camera power.")
        return False

    def read_frame(self) -> Optional[Tuple[bool, any]]:
        """
        Read a frame from the camera.

        Returns:
            Tuple of (success, frame) or None if camera not initialized
        """
        if not self.is_initialized or self.cap is None:
            return None

        with self._cap_lock:
            ret, frame = self.cap.read()
        if not ret or frame is None:
            return (False, None)

        return (True, frame)

    def release(self):
        """Release camera resources."""
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.is_initialized = False
        logger.info("Camera released")

    def __del__(self):
        """Destructor to ensure camera is released."""
        self.release()


class StreamCamera:
    """
    Virtual camera fed by frames PUSHED from a remote browser (the STREAM source).

    Exposes the exact same interface as ``Camera`` (initialize / read_frame /
    release) so the existing capture/inference pipeline can consume it without
    any change — the only difference is that frames arrive via ``push_jpeg()``
    (called by the /api/stream/push route) instead of being pulled from local
    hardware. This keeps the USB/RTSP path and all the streaming threads
    completely untouched.

    ``read_frame()`` returns a freshly pushed frame exactly once; if no new frame
    has arrived since the last read it returns ``(False, None)`` so ``capture_loop``
    idles (the same contract a real camera honours when it has no new frame) and
    inference is not re-run on a duplicate.
    """

    def __init__(self, camera_index="stream", width: int = None, height: int = None,
                 fps: int = None):
        # camera_index kept only for interface symmetry / logging.
        self.camera_index = camera_index
        self.width  = width  if width  is not None else config.CAMERA_WIDTH
        self.height = height if height is not None else config.CAMERA_HEIGHT
        self.fps    = fps    if fps    is not None else config.CAMERA_FPS
        self.is_initialized = False
        self._lock = threading.Lock()
        self._frame = None          # newest decoded BGR frame from the browser
        self._seq = 0               # increments on every pushed frame
        self._last_read_seq = -1    # last seq handed out by read_frame()

    def initialize(self) -> bool:
        """No hardware to open — just arm the buffer and wait for pushes."""
        with self._lock:
            self._frame = None
            self._seq = 0
            self._last_read_seq = -1
        self.is_initialized = True
        logger.info("StreamCamera ready — waiting for frames pushed from the browser.")
        return True

    def push_jpeg(self, jpeg_bytes: bytes) -> bool:
        """
        Decode a JPEG pushed by the browser and store it as the newest frame.
        Returns True on success. Safe to call from the request thread.
        """
        if not self.is_initialized:
            return False
        try:
            arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception as e:
            logger.warning(f"StreamCamera: failed to decode pushed frame: {e}")
            return False
        if frame is None:
            return False
        with self._lock:
            self._frame = frame
            self._seq += 1
        return True

    def read_frame(self) -> Optional[Tuple[bool, any]]:
        """Hand out the newest pushed frame once; (False, None) if nothing new."""
        if not self.is_initialized:
            return None
        with self._lock:
            if self._frame is None or self._seq == self._last_read_seq:
                return (False, None)
            self._last_read_seq = self._seq
            return (True, self._frame)

    def release(self):
        """Drop the buffered frame and mark uninitialised."""
        with self._lock:
            self._frame = None
        self.is_initialized = False
        logger.info("StreamCamera released")

    def __del__(self):
        self.release()
