"""
Camera module for handling external USB webcam input.
Provides camera detection, initialization, and frame capture functionality.
"""

import cv2
import os
from typing import Optional, Tuple, List, Dict
import config
from logger import setup_logger

logger = setup_logger(__name__)

# Priority-ordered backends for each OS
_BACKENDS_WIN   = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
_BACKENDS_LINUX = [cv2.CAP_ANY, cv2.CAP_V4L2]
_BACKENDS = _BACKENDS_WIN if os.name == 'nt' else _BACKENDS_LINUX

_BACKEND_NAMES = {
    cv2.CAP_DSHOW: "DirectShow",
    cv2.CAP_MSMF:  "MSMF",
    cv2.CAP_ANY:   "Auto",
    cv2.CAP_V4L2:  "V4L2",
}


def _probe_index(index: int) -> bool:
    """Return True if the camera index is readable.
    Uses VideoCapture without explicit backend first (most compatible on Windows),
    then falls back to explicit backends.
    """
    # No-backend call matches what test_camera.py does — most reliable on Windows
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
    List available cameras.  skip_indices lets callers exclude already-open cameras
    so we never probe (and briefly steal) a camera that is already in use.
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

    # Fallback: probe only indices not currently in use
    for i in range(max_index + 1):
        if i in skip:
            cameras.append({"id": i, "name": f"Camera {i} (in use)"})
            continue
        if _probe_index(i):
            cameras.append({"id": i, "name": f"Camera {i}"})
    return cameras


class Camera:
    """
    Camera handler for external USB webcam.
    Provides methods to detect available cameras and capture frames.
    """

    def __init__(self, camera_index: int = None):
        """
        Initialize camera handler.

        Args:
            camera_index: Camera index to use. If None, uses config.CAMERA_INDEX
        """
        self.camera_index = camera_index if camera_index is not None else config.CAMERA_INDEX
        self.cap: Optional[cv2.VideoCapture] = None
        self.is_initialized = False

    def initialize(self) -> bool:
        """
        Initialize the camera.
        Tries no-backend (most compatible) first, then explicit backends.
        """
        if self.is_initialized:
            self.release()

        logger.info(f"Initializing camera index={self.camera_index} ...")

        # Build try list: no-backend first (works when DSHOW/MSMF fail by index)
        attempts = [None] + _BACKENDS   # None = no explicit backend

        for backend in attempts:
            cap = (cv2.VideoCapture(self.camera_index) if backend is None
                   else cv2.VideoCapture(self.camera_index, backend))
            if not cap.isOpened():
                cap.release()
                continue

            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  config.CAMERA_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
            cap.set(cv2.CAP_PROP_FPS,          config.CAMERA_FPS)
            cap.set(cv2.CAP_PROP_BUFFERSIZE,   1)

            # Warm-up: discard initial frames
            for _ in range(5):
                cap.read()

            ret, frame = cap.read()
            if ret and frame is not None:
                self.cap = cap
                self.is_initialized = True
                bname = "Default" if backend is None else _BACKEND_NAMES.get(backend, str(backend))
                logger.info(f"Camera ready (index={self.camera_index}, backend={bname})")
                return True

            cap.release()

        # All backends failed — help the user pick the right index
        logger.error(
            f"Cannot open camera index={self.camera_index}. "
            "Is it connected? Is another app (Teams/Zoom) using it?"
        )
        available = scan_cameras_fast()
        if available:
            ids = [c["id"] for c in available]
            logger.error(f"Available camera indices: {ids}. Set CAMERA_INDEX in config.py")
        else:
            logger.error("No cameras found at all. Check cable / driver / Device Manager.")
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
