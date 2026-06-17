"""
Camera module for handling USB webcams and RTSP IP camera streams.
Provides camera detection, initialization, and frame capture functionality.
"""

import cv2
import os
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
    """Return True if the camera index is readable."""
    cap = cv2.VideoCapture(index)
    if cap.isOpened():
        ret, _ = cap.read()
        cap.release()
        if ret:
            return True

    for backend in _BACKENDS:
        cap = cv2.VideoCapture(index, backend)
        if cap.isOpened():
            ret, _ = cap.read()
            cap.release()
            if ret:
                return True
    return False


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
                 width: int = None, height: int = None, fps: int = None):
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
        self.cap: Optional[cv2.VideoCapture] = None
        self.is_initialized = False

    def initialize(self) -> bool:
        if self.is_initialized:
            self.release()
        if _is_url(self.camera_index):
            return self._initialize_rtsp()
        return self._initialize_usb()

    def _initialize_usb(self) -> bool:
        """Open a local USB/built-in camera by index."""
        logger.info(f"Initializing USB camera index={self.camera_index} ...")

        attempts = [None] + _BACKENDS

        for backend in attempts:
            cap = (cv2.VideoCapture(self.camera_index) if backend is None
                   else cv2.VideoCapture(self.camera_index, backend))
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

            for _ in range(5):
                cap.read()

            ret, frame = cap.read()
            if ret and frame is not None:
                self.cap = cap
                self.is_initialized = True
                bname = "Default" if backend is None else _BACKEND_NAMES.get(backend, str(backend))
                ah, aw = frame.shape[:2]
                logger.info(
                    f"USB camera ready (index={self.camera_index}, backend={bname}, "
                    f"requested={self.width}x{self.height}, actual={aw}x{ah})"
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
