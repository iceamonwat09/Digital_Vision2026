"""
Configuration file for the YOLO defect detection system.
Configure camera index, model paths, and detection parameters here.
"""

import os

# Bump this whenever a config default changes so a running deployment can
# print it on startup and confirm it is actually executing the new code.
CONFIG_VERSION = "2026.07.01-frame-capture"

# ====================
# CAMERA CONFIGURATION
# ====================
# IMPORTANT: Configure your external USB webcam index here
# Default camera indices: 0 = built-in/laptop camera, 1+ = external USB webcams
# To find your external webcam index, run: python -c "import cv2; [print(f'Index {i}: {cv2.VideoCapture(i).read()[0]}') for i in range(5)]"
CAMERA_INDEX = 0  # ผลจาก test_camera.py: กล้องอยู่ที่ index 0

# ── Live stream resolution ──────────────────────────────────────────
# 640x480 @ 30fps บนกล้อง ELP 8MP (IMX179). เลือกความละเอียดต่ำสำหรับ live
# โดยตั้งใจ เพราะ sensor เป็น rolling shutter — ความละเอียดต่ำ = อ่านเฟรมเร็ว
# = วัตถุที่เคลื่อน (กระป๋องบนสายพาน) บิดเป็น "ลูกคลื่น" น้อยที่สุด. อีกทั้ง
# โมเดล live รันที่ imgsz=480 อยู่แล้ว ป้อน 720p เข้าไปก็ถูกย่อทิ้ง ไม่ได้ประโยชน์.
# งานตรวจละเอียดเป็นหน้าที่ของ snapshot (5MP) แทน.
CAMERA_WIDTH  = 640
CAMERA_HEIGHT = 480
CAMERA_FPS = 30

# FourCC ของกล้อง. ตั้งเป็น None = ใช้ฟอร์แมต default ของกล้อง (มักเป็น YUY2
# uncompressed) ซึ่ง MSMF บน Windows ถอดรหัสได้ "สะอาด" ไม่มีเฟรมแตก.
#
# ⚠️ เคยตั้งเป็น "MJPG" เพื่อปลดล็อกความละเอียดสูงบน USB 2.0 แต่บนเครื่องสถานี
# (MSMF) มันส่ง JPEG ออกมาไม่สมบูรณ์ → ภาพแตกเป็นคลื่นสีรุ้ง (เฟรมขาด) กระทบ
# ทั้งจอแสดงผลและผลตรวจ. กล้องตัวนี้ cap ที่ 720p อยู่แล้ว จึงไม่ต้องใช้ MJPG.
# ถ้าย้ายไปกล้อง/เครื่องที่ MJPG ทำงานสะอาดและต้องการ >720p ค่อยเปิดกลับเป็น "MJPG".
CAMERA_FOURCC = None

# ── Snapshot capture resolution ─────────────────────────────────────
# โหมดถ่ายรูปถ่ายครั้งเดียวต่อชัตเตอร์ จึงไม่ต้องห่วง fps — ดันความละเอียด
# ให้สูงเพื่อจับรอยบุบเล็ก/ตื้นได้แม่นที่สุด (กล้องนี้สูงสุด 3264x2448 / 8MP).
# rolling shutter ไม่กระทบเพราะตอนกดชัตเตอร์กระป๋องวางนิ่งอยู่แล้ว.
SNAPSHOT_CAMERA_WIDTH  = 2592
SNAPSHOT_CAMERA_HEIGHT = 1944

# FPS ของโหมด 5MP — กล้องรองรับ 2592x1944 ที่ 15fps (โหมดมาตรฐาน UVC).
# ตั้งให้ตรงโหมดจริงเพื่อให้ไดรเวอร์ไม่ต้องเดา และพอสำหรับการเล็งภาพนิ่ง.
SNAPSHOT_CAMERA_FPS = 15

# ── Snapshot resolution fallback ladder ─────────────────────────────
# โหมดถ่ายรูป "เปิดกล้องครั้งเดียว" ที่ความละเอียดสูงสุดที่กล้องรองรับจริง
# แล้วใช้แฮนเดิลเดียวนั้นทั้งเล็ง (ย่อแสดง) และตอนกดชัตเตอร์ — ไม่มีการ
# release→reopen กลางทาง (ซึ่งเป็นต้นเหตุ "ถ่ายไม่สำเร็จ" บนกล้อง UVC).
# ระบบไล่ลองจากคมสุดลงมา โหมดแรกที่เปิดได้ + ส่งเฟรมจริงเป็นผู้ชนะ.
# (width, height, fps) เรียงจากความละเอียดสูง → ต่ำ.
SNAPSHOT_RESOLUTION_LADDER = [
    (SNAPSHOT_CAMERA_WIDTH, SNAPSHOT_CAMERA_HEIGHT, SNAPSHOT_CAMERA_FPS),  # 2592x1944 (5MP)
    (1920, 1080, 30),  # 1080p — มาตรฐานเกือบทุกกล้อง
    (1280, 720, 30),   # 720p — รับประกันเปิดได้เกือบทุกกล้อง (ขั้นสุดท้าย)
]

# ── Snapshot quality presets (ให้ผู้ใช้เลือกได้จากหน้าเว็บ) ──────────────
# แลกระหว่าง "เล็งลื่น" (ความละเอียดต่ำ = fps สูง + ย่อภาพถูก = ลื่น) กับ
# "ภาพคม" (5MP = จับ dent เล็กได้ดีแต่ 15fps เล็งกระตุก). โหมดที่เลือกใช้
# ทั้งตอนเล็งและตอนถ่าย (แฮนเดิลเดียว ไม่ reopen).
SNAPSHOT_QUALITY_PRESETS = {
    "smooth":   (1280, 720, 30),   # ลื่นที่สุด
    "balanced": (1920, 1080, 30),  # สมดุล (ค่าเริ่มต้น)
    "sharp":    (SNAPSHOT_CAMERA_WIDTH, SNAPSHOT_CAMERA_HEIGHT, SNAPSHOT_CAMERA_FPS),  # คม 5MP
}
# Default to the smoothest mode (720p). It is the most stable across UVC stacks
# (no high-res mode negotiation) and matches cameras that cap at 720p over USB.
# Operators can step up to balanced/sharp from the dropdown when they need detail.
SNAPSHOT_QUALITY_DEFAULT = "smooth"

# Max age (seconds) of the viewfinder frame the shutter is allowed to inspect.
# If the camera freezes/unplugs, the read loop stops publishing new frames and
# the last good one goes stale — the shutter must REFUSE rather than return a
# verdict on an old image (this is a QC system). Tune up if the chosen mode runs
# at a very low fps.
SNAPSHOT_MAX_FRAME_AGE_S = 1.0

# ── Viewfinder (อาการเล็งก่อนกดชัตเตอร์) ──────────────────────────────
# หมายเหตุ: ตั้งแต่เปลี่ยนเป็นสถาปัตยกรรม "เปิดกล้องครั้งเดียว" viewfinder
# ใช้แฮนเดิลเดียวกับชัตเตอร์ (เปิดที่ความละเอียดจาก SNAPSHOT_RESOLUTION_LADDER
# แล้วย่อแสดงผลด้วย _VIEWFINDER_MAX_W ใน app.py). ค่าด้านล่างเก็บไว้เพื่อความ
# เข้ากันได้กับโค้ดเดิม แต่ปัจจุบันไม่ได้ใช้เปิดกล้องแล้ว.
VIEWFINDER_CAMERA_WIDTH  = 1280
VIEWFINDER_CAMERA_HEIGHT = 720
VIEWFINDER_CAMERA_FPS = 30

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

# Hard cap on detections returned per frame by the model's NMS. A can-inspection
# frame never holds more than a handful of objects, so capping this keeps the
# per-frame post-processing bounded and prevents low-confidence junk boxes from
# stalling the live feed (was unbounded at the old conf=0.01 debug floor).
YOLO_MAX_DET = 20

# Inference image size passed to the model. Lower = faster inference = the box
# tracks the moving can more closely (less temporal lag). 640 is the YOLO
# default; 480 roughly doubles FPS on CPU with negligible accuracy loss for
# can-body dents at this camera resolution. Drop to 320 for even more speed.
YOLO_IMGSZ = 480

# ── OpenVINO acceleration (เร่ง inference บน Intel CPU/iGPU) ──
# ⚠️ ปิดไว้ (False) เป็นค่าเริ่มต้น: บนสถานี (ultralytics 8.4.41 + openvino 2025.3.0)
# โมเดล OpenVINO ที่ export ออกมา "ตรวจไม่เจอ dent เลยทุกโหมด" (ถอดรหัส output ไม่ตรง
# เวอร์ชัน). PyTorch ทำงานถูกต้อง จึงใช้ PyTorch เป็นหลัก.
# ถ้าจะลองเปิดใหม่ในอนาคต ต้องทดสอบ pin เวอร์ชัน ultralytics/openvino ให้เข้ากันก่อน
# (และตรวจว่ายังเจอ dent เท่า PyTorch). เปิด = True เพื่อทดลองเท่านั้น.
USE_OPENVINO = False

# ── ONNX Runtime acceleration (เร่ง inference บน CPU โดยคงความแม่น FP32) ──
# ทางที่ปลอดภัยกว่า OpenVINO บนสถานีนี้ (Windows + Python 3.9): export โมเดล .pt
# เป็น .onnx (FP32 / dynamic) ครั้งเดียว แล้วรันผ่าน onnxruntime — ultralytics เป็น
# คนถอดผล (decode/NMS) เองเหมือน .pt ทุกประการ จึงได้ผลตรวจเท่าเดิมแต่เร็วขึ้น ~2 เท่า.
#
# ⚠️ ค่าเริ่มต้น = False (ปิด). ต้องรัน `python verify_onnx.py` เทียบผลตรวจ .pt vs .onnx
# ให้ผ่าน (PASS) ก่อน ค่อยเปิดเป็น True — กันเหตุ "ตรวจผิดเงียบๆ" แบบที่เคยเจอกับ OpenVINO.
#
# ข้อกำหนดเครื่อง (ติดตั้งบน interpreter เดียวกับที่รัน เช่น `py -3.9 -m pip ...`):
#   onnxruntime==1.19.2   (wheel ตัวสุดท้ายที่รองรับ Python 3.9 บน Windows; 1.20+ ตัดทิ้ง)
#   onnxslim              (ออปชัน — ใช้ลดขนาดกราฟตอน export ให้เร็วขึ้น)
# fallback: ถ้า onnxruntime ไม่ได้ติดตั้ง / export / load / smoke-test ล้มเหลว
# → ระบบกลับไปใช้ PyTorch .pt อัตโนมัติ (ของเดิมพังไม่ได้).
USE_ONNX = True

# opset ที่ใช้ตอน export ONNX. ปักไว้ที่ 17 เพื่อความเข้ากันได้กับ onnxruntime 1.19.x
# (รองรับ opset ≤ ~21). None = ปล่อยให้ ultralytics เลือก default ของมันเอง.
ONNX_OPSET = 17

# จำนวน intra-op thread ของ onnxruntime. 0 = ให้ onnxruntime ตัดสินใจเอง (= จำนวน
# physical core). บนชิป 15W ที่ throttle ง่าย (i7-1165G7) การตั้ง = 4 (เท่า physical
# core, ไม่นับ hyper-thread) บางทีนิ่ง/ร้อนน้อยกว่า. มีผลเฉพาะเมื่อ USE_ONNX=True.
ONNX_INTRA_THREADS = 0

# Snapshot inference image size. Snapshot runs the model ONCE per shutter press
# (not a live stream), so speed is irrelevant — we trade it for accuracy. With
# the high-resolution snapshot capture (SNAPSHOT_CAMERA_* = 5MP) there is real
# detail to work with, so 1280 lets the model resolve small/shallow dents that
# were lost at the downscaled live size.
SNAPSHOT_IMGSZ = 1280

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
DEFECT_LOGGING_COOLDOWN = 2.0  # (legacy) ไม่ใช้แล้ว — เปลี่ยนเป็นนับ/บันทึกแบบ edge-triggered

# จำนวนเฟรม "ว่าง" (ไม่เจออะไรเลย) ติดต่อกัน ที่ถือว่ากระป๋องผ่านพ้นไปแล้ว
# ใช้ทั้งโหมด live (USB/RTSP) — กันการนับ/บันทึกซ้ำของกระป๋องใบเดิม (1 ใบ = 1 การตรวจ).
# สูงขึ้น = ทนการกะพริบของการตรวจจับมากขึ้น แต่รับกระป๋องใบถัดไปช้าลง.
DEFECT_RESET_FRAMES = 3

# Maximum number of defects to log per frame
MAX_DEFECTS_PER_FRAME = 5

# ====================
# APPLICATION CONFIGURATION
# ====================
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5000
FLASK_DEBUG = False

# ── HTTPS (จำเป็นสำหรับโหมด STREAM / getUserMedia บนเครื่องอื่นใน LAN) ──────
# เบราว์เซอร์อนุญาตให้เข้าถึงกล้อง (getUserMedia) เฉพาะ "secure context" คือ
# HTTPS หรือ localhost เท่านั้น. เปิด USE_HTTPS=True แล้วชี้ไปที่ไฟล์ cert/key
# (สร้างได้ด้วย `python generate_cert.py`) เพื่อให้เข้าผ่าน https://<ip>:5000 ได้.
# ค่า default = ปิด → app.run ทำงานเหมือนเดิมทุกประการ (ไม่กระทบของเดิม).
USE_HTTPS = True
SSL_CERT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs", "cert.pem")
SSL_KEY_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs", "key.pem")

# Video streaming configuration
STREAM_FPS = 15  # FPS for the live-detection MJPEG stream (lower = less bandwidth)

# ── โหมดแสดงผลวิดีโอสด USB/RTSP (คุมความลื่นของภาพ vs การล็อกกรอบ) ──────────
# True  = "ลื่น" (แนะนำ): สตรีมเฟรมดิบล่าสุดที่อัตรากล้อง (≈STREAM_FPS) แล้ววาดกรอบ
#         ผลตรวจล่าสุดทับ → ภาพลื่นไม่ขึ้นกับความเร็ว inference. ข้อแลก: ตอนวัตถุ
#         ขยับเร็ว กรอบจะตามช้าเล็กน้อย (วาดบนเฟรมที่ใหม่กว่าเฟรมที่ infer — เหมือน
#         โหมดสตรีม). เหมาะกับโมเดลหนัก (เช่น bestX segmentation) ที่ infer ช้า.
# False = "ล็อกกรอบ" (พฤติกรรมเดิม): แสดงเฉพาะเฟรมที่ infer เสร็จ → กรอบล็อกเป๊ะกับ
#         เฟรมนั้น แต่ภาพอัปเดตตามอัตรา inference (กระตุกถ้าโมเดลหนัก).
LIVE_SMOOTH_VIDEO = False

# ── โหมด "Frame Capture" (ทดสอบ best-frame) — ใช้กับแหล่งภาพ USB/RTSP ──────────
# เมื่อเปิด (ผ่าน checkbox ในแผง USB): พอกระป๋อง NG ใบหนึ่งผ่านพ้นไป ระบบจะ "แช่"
# แสดง "เฟรมที่คมที่สุด" ของใบนั้น (เลือกด้วยความคมของรอยบุบ × ความมั่นใจ) ค้างไว้
# FRAME_CAPTURE_HOLD_SEC วินาที แล้วกลับไปแสดงสด. เป็นแค่การแสดงผล — ไม่กระทบการนับ/
# การบันทึก DB (ยังทำแบบเดิม). ค่าเริ่มต้นการแสดงผลคุมด้วย toggle ฝั่ง UI (ปิดไว้).
FRAME_CAPTURE_HOLD_SEC = 5

# ── Browser STREAM source (กล้องของเครื่อง Client ผ่าน getUserMedia) ──────
# โหมดที่ 3 ในข้อ "แหล่งสัญญาณภาพ": ใช้กล้องของเครื่อง Client ผ่านเบราว์เซอร์.
# สถาปัตยกรรม = per-client isolation (request/response): เบราว์เซอร์โชว์กล้อง
# ตัวเองใน <video> แล้วส่งเฟรมไป /api/stream/infer → server คืนพิกัดกรอบ (JSON)
# กลับเฉพาะ client นั้น → ทุกคนเห็นแต่กล้องตัวเอง ไม่แชร์ pipeline เดิม.
#
# ค่าจริงที่ใช้คุมการสตรีมอยู่ฝั่งเบราว์เซอร์ (ค่าคงที่ STREAM_* ใน
# templates/index.html). ค่าด้านล่างเก็บไว้เป็น "ค่าอ้างอิง/ค่าเริ่มต้นที่แนะนำ"
# ให้ตรงกัน — จูนสำหรับ 1 กล้องบนเครื่อง CPU (เช่น i7-1165G7): โมเดล live รันที่
# imgsz 480 จึงส่งกว้าง 640 พอดี + ~10 fps. อนาคต 2–3 กล้องให้ลด INFER_FPS เป็น 5–6.
STREAM_SOURCE_SENTINEL = "stream"  # camera_index พิเศษ (ใช้กับ StreamCamera/​push เดิมที่คงไว้)
STREAM_INFER_FPS = 10              # อัตราที่เบราว์เซอร์เรียก /api/stream/infer (1 กล้อง)
STREAM_JPEG_QUALITY = 0.92         # คุณภาพ JPEG ที่เบราว์เซอร์ encode ก่อนส่ง (สูง = กรอบแม่นขึ้น)
STREAM_MAX_WIDTH = 640             # ความกว้างเฟรม live ก่อนส่ง (px) — โมเดลใช้ 480
# imgsz ที่โมเดลใช้ตรวจ "สตรีมสด". ⚠️ รอยบุบ (dent) เป็นฟีเจอร์เล็ก ต้องการ
# ความละเอียด ≥480 ถึงจะตรวจเจอ — ต่ำกว่านี้ (เช่น 320) โมเดลจะมองไม่เห็นรอยบุบ
# แล้วขึ้น Good หมด. จึงล็อกให้เท่า USB live (YOLO_IMGSZ=480). อย่าลดต่ำกว่านี้.
# ถ้าต้องการเร็วขึ้นโดยคงความแม่น → ใช้ GPU/OpenVINO (ดู README) ไม่ใช่ลด imgsz.
STREAM_INFER_IMGSZ = YOLO_IMGSZ
# คุณภาพ/ความกว้างสำหรับภาพ snapshot (ถ่ายครั้งเดียว ดันคุณภาพให้สูงกว่า live).
STREAM_SNAPSHOT_JPEG_QUALITY = 0.95
STREAM_SNAPSHOT_MAX_WIDTH = 1920

# Viewfinder (snapshot aiming) stream rate. Higher than STREAM_FPS so aiming
# feels fluid — the live feed is deliberately 15fps to save bandwidth, but the
# operator aiming a can needs a smooth preview. Capped by the camera's own fps
# at the chosen resolution (e.g. 5MP tops out at 15fps regardless).
VIEWFINDER_STREAM_FPS = 30

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
    "http://172.32.201.106:5678/webhook/artwork-ocr",
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