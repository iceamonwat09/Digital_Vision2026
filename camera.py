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

# Use DirectShow on Windows for faster, non-blocking camera operations
_BACKEND = cv2.CAP_DSHOW if os.name == 'nt' else cv2.CAP_ANY


def scan_cameras_fast(max_index: int = 4) -> List[Dict]:
    """
    Quickly probe camera indices and return available ones with actual names.
    Uses pygrabber on Windows for precise camera names.

    Args:
        max_index: Maximum camera index to test (inclusive) for fallback.

    Returns:
        List of dictionaries containing 'id' and 'name'.
    """
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
            logger.warning(f"Could not retrieve camera names using pygrabber: {e}. Falling back to standard OpenCV probe.")

    # Fallback to OpenCV probe
    for i in range(max_index + 1):
        cap = cv2.VideoCapture(i, _BACKEND)
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                cameras.append({"id": i, "name": f"Camera {i}"})
            cap.release()
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
        Initialize the camera with configured settings.

        Returns:
            True if initialization successful, False otherwise
        """
        if self.is_initialized:
            self.release()

        logger.info(f"Initializing camera at index {self.camera_index}")
        self.cap = cv2.VideoCapture(self.camera_index, _BACKEND)

        if not self.cap.isOpened():
            logger.error(f"Failed to open camera at index {self.camera_index}")
            return False

        # Set camera properties
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
        self.cap.set(cv2.CAP_PROP_FPS, config.CAMERA_FPS)
        # Minimize internal buffer to reduce latency
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        # Quick frame test
        ret, frame = self.cap.read()
        if not ret or frame is None:
            logger.error("Camera opened but cannot read frames.")
            self.cap.release()
            self.cap = None
            return False

        logger.info("Camera initialized successfully.")
        self.is_initialized = True
        return True

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
