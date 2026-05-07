"""
Configuration file for the YOLO defect detection system.
Configure camera index, model paths, and detection parameters here.
"""

import os

# ====================
# CAMERA CONFIGURATION
# ====================
# IMPORTANT: Configure your external USB webcam index here
# Default camera indices: 0 = built-in/laptop camera, 1+ = external USB webcams
# To find your external webcam index, run: python -c "import cv2; [print(f'Index {i}: {cv2.VideoCapture(i).read()[0]}') for i in range(5)]"
CAMERA_INDEX = 0  # ผลจาก test_camera.py: กล้องอยู่ที่ index 0
CAMERA_WIDTH  = 640   # 640x480 ใช้ได้กับทุกกล้อง (notebook + USB)
CAMERA_HEIGHT = 480   # เปลี่ยนเป็น 1280x720 ได้ถ้าต้องการ HD
CAMERA_FPS = 30

# Enable this to test available cameras at startup
# Set to False for faster startup (skips camera scanning)
TEST_CAMERAS_ON_STARTUP = False

# ====================
# YOLO MODEL CONFIGURATION
# ====================
# Weights live under weights/<mode>/ — see modes/ package for per-mode config.
WEIGHTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights")

# Default mode that loads at startup (must match a name in modes/registry.py).
DEFAULT_MODE = "can_dent"

# Legacy single-model path. Kept so existing code that imports
# ``config.MODEL_PATH`` keeps working. Points at the can_dent default weights.
MODEL_PATH = os.path.join(WEIGHTS_DIR, "can_dent", "best.pt")

# Confidence threshold for detections
CONFIDENCE_THRESHOLD = 0.25

# IoU threshold for NMS (Non-Maximum Suppression)
IOU_THRESHOLD = 0.45

# ====================
# DEFECT CLASS MAPPING  (Can Dent Detection)
# ====================
# NOTE: ระบบอ่านชื่อ class จาก model.names โดยตรง (ตรงกับ data.yaml เสมอ)
# ค่าเหล่านี้ใช้เป็น fallback / backward-compat สำหรับโค้ดเดิมเท่านั้น
# Per-mode config อยู่ใน modes/<mode>.py
DEFECT_CLASSES = {
    0: "dented",
    1: "dented_spot",
    2: "good"
}

# Display names — Can Dent mode (default).
# Used as fallback when no mode_config is supplied to YOLODetector.
DEFECT_CLASS_NAMES = {
    "dented":      "Can Dent",
    "dented_spot": "Dent Area",
    "good":        "Can Good"
}

# ====================
# DATABASE CONFIGURATION  (SQL Server 2014)
# ====================
SQL_SERVER   = "172.32.0.50"       # IP Address ของ SQL Server
SQL_DATABASE = "VisionIQ"          # ชื่อ Database
SQL_USER     = "sa"                # SQL Server Login
SQL_PASSWORD = "P@ssw0rd"          # SQL Server Authentication password

# Plant / Line Information
PLANT_CODE  = "TUM1"               # รหัสโรงงาน (Thai Union Mahachai 1)
LINE_NUMBER = "LINE-01"            # รหัสสายการผลิต

# ====================
# DEFECT LOGGING CONFIGURATION
# ====================
# Cooldown period in seconds to avoid duplicate logging
DEFECT_LOGGING_COOLDOWN = 2.0  # Log at most once every 2 seconds per defect type

# Maximum number of defects to log per frame
MAX_DEFECTS_PER_FRAME = 5

# ====================
# APPLICATION CONFIGURATION
# ====================
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5000
FLASK_DEBUG = False

# Video streaming configuration
STREAM_FPS = 15  # FPS for MJPEG stream (lower = less bandwidth)

# Application directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)