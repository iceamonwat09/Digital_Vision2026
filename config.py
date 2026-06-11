"""
Configuration file for the YOLO defect detection system.
Configure camera index, model paths, and detection parameters here.
"""

import os

# Bump this whenever a config default changes so a running deployment can
# print it on startup and confirm it is actually executing the new code.
CONFIG_VERSION = "2026.05.27-n8n-prod"

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

# ====================
# LABEL PAPER — OCR BACKEND
# ====================
# Selects which OCR provider inspectors.vertex_client.ocr_image() dispatches to.
#   "stub"   → no network, returns placeholder text (default; safe for Can Dent only deployments)
#   "n8n"    → POSTs the cropped image to an N8N webhook that wraps a Gemini / Vision call
#   "vertex" → direct Document AI call (not implemented yet — Phase 2+)
#
# When OCR_BACKEND is unset but N8N_OCR_WEBHOOK_URL is configured, the
# dispatcher auto-switches to "n8n" so a configured webhook works without
# an extra env flag.
OCR_BACKEND = os.getenv("OCR_BACKEND", "").strip().lower()

# N8N webhook that performs OCR (currently fronts Gemini 2.5 Flash on Vertex).
# The workflow must respond with JSON of shape:
#   { "text": "<full text>", "blocks": [ {"text": "...", "bbox": [x,y,w,h], "conf": 0.9}, ... ] }
# `blocks` is optional — when missing, only line-level text comparison is possible.
N8N_OCR_WEBHOOK_URL = os.getenv(
    "N8N_OCR_WEBHOOK_URL",
    "http://172.32.201.106:5678/webhook/3d597970-f7de-4a8c-85eb-c9ad8c1e0877",
).strip()
N8N_OCR_TIMEOUT_S = float(os.getenv("N8N_OCR_TIMEOUT_S", "60"))

# ====================
# LABEL PAPER — VISUAL DIFF (Gemini compares 2 images)
# ====================
# When the N8N workflow routes by which binaries arrived (``image`` →
# OCR, ``master`` + ``captured`` → visual diff), the visual-diff stage
# reuses the OCR webhook URL above. Set ``N8N_VISDIFF_WEBHOOK_URL`` to
# override only when the user splits visual diff into a dedicated flow.
N8N_VISDIFF_WEBHOOK_URL = os.getenv("N8N_VISDIFF_WEBHOOK_URL", "").strip()
N8N_VISDIFF_TIMEOUT_S = float(os.getenv("N8N_VISDIFF_TIMEOUT_S", "60"))

# Toggle the visual-diff pipeline stage. Default ON when the OCR webhook
# is configured (the shared-flow case). Set to "0" / "false" to disable
# without changing URLs.
def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


VISUAL_DIFF_ENABLED = _env_bool(
    "VISUAL_DIFF_ENABLED",
    default=bool(N8N_OCR_WEBHOOK_URL or N8N_VISDIFF_WEBHOOK_URL),
)