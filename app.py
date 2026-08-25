"""
Flask application for YOLO-based defect detection system.
Main application file with routes, video streaming, and API endpoints.
"""

import os
import threading
import time
import base64
import functools
from datetime import datetime
import cv2
import numpy as np
from flask import Flask, render_template, Response, jsonify, request
from werkzeug.serving import WSGIRequestHandler

import config
from logger import setup_logger
from camera import Camera, StreamCamera, scan_cameras_fast
from yolo_detector import YOLODetector
from database import Database
from modes import registry as mode_registry

# Label Paper Inspection (PDF master + manual crop + Vertex AI).
# Kept independent of the YOLO mode-switcher above on purpose.
from modes import label_paper as label_paper_cfg
from inspectors import master_loader, label_pipeline, perspective, master_ocr
from inspectors import history as label_history

# Setup centralized logging
logger = setup_logger(__name__)

# Initialize Flask app
app = Flask(__name__, template_folder=config.TEMPLATES_DIR, static_folder=config.STATIC_DIR)

# Inject CONFIG_VERSION into every template automatically
@app.context_processor
def inject_config():
    return {"config_version": config.CONFIG_VERSION}

# Fallback auth template globals. base.html references has_perm()/current_user/
# auth_enabled — if the auth layer fails to load (e.g. bcrypt/PyJWT not yet
# installed) its context processor never registers and those names would be
# Undefined, 500-ing every page. These defaults keep the app working exactly as
# before (all menus visible, no gating). When auth installs successfully its own
# context processor is registered LATER and overrides these.
@app.context_processor
def _auth_fallback():
    return {
        "current_user": None,
        "auth_enabled": False,
        "has_perm": lambda *_a, **_k: True,
    }

# Hikrobot industrial camera (GigE / MVS SDK) — แหล่งภาพที่ 4 ของโหมด Can Dent.
# import แบบกันพลาดด้วยเหตุผลเดียวกับ blueprint ของ Artwork ด้านล่าง: ถ้าโมดูลนี้
# มีปัญหา (SDK เพี้ยน/ไฟล์เสีย) ต้องกระทบแค่แท็บใหม่ ไม่ใช่ล้มทั้งแอป.
# หมายเหตุ: hik_camera **ไม่ import MVS SDK ตอน import** จึงปลอดภัยบนเครื่องที่ไม่มี MVS.
try:
    import hik_camera
except Exception as _hik_err:                       # pragma: no cover
    hik_camera = None
    logger.error(f"Hikrobot camera module unavailable: {_hik_err}")

# โหมด "ถ่ายรัว" (แกลเลอรี + ตัวเลขความเบลอ) — แยกโมดูลเพราะไม่แตะกล้องเลย
# (อ่าน/เขียนเฉพาะโฟลเดอร์ภาพของตัวเอง) และต้องเทสต์ได้โดยไม่มี MVS/โมเดล.
try:
    import hik_burst
except Exception as _hb_err:                        # pragma: no cover
    hik_burst = None
    logger.error(f"Hikrobot burst module unavailable: {_hb_err}")

# Artwork Proof Check (ตรวจสะกดคำ/ตัวเลขใน artwork ก่อนพิมพ์).
# Fully isolated blueprint — a failure here only disables that one mode
# and can never break Can Dent / Label / Label Paper.
try:
    from artwork_check.routes import artwork_bp
    app.register_blueprint(artwork_bp)
    logger.info("Artwork Proof Check mode registered (/artwork_check)")
except Exception as _aw_err:  # noqa: BLE001 — isolation by design
    logger.warning(f"Artwork Proof Check disabled: {_aw_err}")

# Authentication + RBAC (login, JWT cookies, per-route permission guard).
# Isolated like the artwork blueprint: a failure here leaves the inspection
# modes running (just without auth). When AUTH_ENABLED is false the guard is a
# no-op, so the station behaves exactly as before until you switch it on.
try:
    from auth import install_auth
    install_auth(app)
except Exception as _auth_err:  # noqa: BLE001 — never let auth wiring crash boot
    logger.error(f"Auth + RBAC disabled (wiring error): {_auth_err}")

# Pre-computed JPEG encode params (avoids re-creating each frame)
_JPEG_PARAMS = [cv2.IMWRITE_JPEG_QUALITY, 80]

# Max width (px) for transport-sized images. The high-res snapshot capture is
# decoded/detected at full resolution, but the viewfinder stream and the
# returned preview are downscaled to these so the browser stays responsive.
_VIEWFINDER_MAX_W = 960
_SNAPSHOT_DISPLAY_MAX_W = 1280

# Global variables for detection system
camera = None
detector = None
db = None
detection_active = False

# ── Decoupled capture / inference pipeline ─────────────────────────────
# Capture and inference run on separate threads so the displayed video
# stays at the camera's frame rate even when the model is slow. The
# capture thread only grabs frames; the inference thread consumes the
# latest grabbed frame at whatever rate the model can manage; the MJPEG
# generator composites the newest detections onto the newest frame.
capture_thread = None
inference_thread = None

latest_raw_frame = None           # newest raw BGR frame from the camera
raw_frame_seq = 0                 # increments on every new captured frame
raw_lock = threading.Lock()

# The inference thread publishes its detections *together with the exact frame
# they were computed on* (and that frame's seq). The MJPEG generator then draws
# each detection on its own frame — so the box always sits on the can where the
# model actually saw it, instead of being pasted onto a newer frame where the
# can has already moved on (that mismatch was the "box lags the can" bug).
latest_detections = []            # detections for latest_det_frame
latest_det_frame = None           # the exact frame those detections came from
latest_det_seq = -1               # seq of latest_det_frame (drives re-encode)
det_lock = threading.Lock()

# ── Frame Capture mode (best-frame test) ────────────────────────────────────
# inference_loop keeps the sharpest NG frame of each passing can and, when the
# can leaves, publishes it here as a ready-to-serve JPEG. generate_frames freezes
# the feed on it for config.FRAME_CAPTURE_HOLD_SEC when the mode is on. Display
# only — never affects counting or DB logging.
frame_capture_enabled = False     # toggled by the UI (POST /api/frame_capture)
# โหมดแสดงผลของภาพสด "กล้องอุตสาหกรรม": None = ใช้ค่าจาก config.HIK_LIVE_SMOOTH_VIDEO
# True/False = ผู้ใช้สลับเองจากช่องติ๊กในแผง (POST /api/camera/hik/live_smooth).
# ⚠️ แสดงผลล้วน — ไม่แตะการนับ/DB/verdict ซึ่งใช้เฟรมที่โมเดลตรวจจริงเสมอ
hik_live_smooth_override = None

# ── "ถ่าย 1 เฟรม" แบบ 2 เฟส ────────────────────────────────────────────────
# เดิมคำขอเดียวทำ จับภาพ → ตรวจ → ส่งกลับ ⇒ ผู้ใช้ต้องรอ **ทั้ง inference**
# (imgsz 1280 บนสถานี ~420 ms) ก่อนจะได้เห็นรูปเลยแม้แต่นิดเดียว. แยกเป็น
# เฟส ① คืนรูปทันที (จับภาพ ~15 ms + ย่อ/encode) เฟส ② ค่อยตรวจ ⇒ ภาพขึ้นเร็ว
# ⚠️ เก็บได้ช่องเดียว (ล่าสุดเท่านั้น) — เป็นเครื่องมือทดสอบ ไม่ใช่คิวงาน
hik_shot_lock = threading.Lock()
hik_shot_frame = None             # เฟรมความละเอียดเต็มของช็อตล่าสุด
hik_shot_id = 0                   # กันเฟส ② ไปตรวจเฟรมของช็อตก่อนหน้า

# ── โหมด "ภาพสดเป็นแค่ viewfinder" (ไม่ตรวจระหว่าง live) ────────────────────
# ใช้กับเวิร์กโฟลว์ "ถ่าย 1 เฟรมแล้วค่อยตรวจ": ระหว่างเล็งกล้องไม่ต้องการผลตรวจสด
# อยู่แล้ว และการตรวจสดกิน iGPU จนช็อตต้องรอคิว (วัดได้ 706 ms จาก 1,130 ms)
# ⚠️ ระหว่างเปิดโหมดนี้ **การนับกระป๋องและการบันทึก DB หยุดด้วย** — หน้าเว็บ
#    ต้องขึ้นคำเตือนตลอดช่วงนั้น และค่าถูกรีเซ็ตทุกครั้งที่กด Start Detection
#    เพื่อไม่ให้ค้างในสภาพ "ไม่ตรวจ" ข้ามรอบโดยไม่มีใครรู้
hik_live_detect_off = False
latest_best_jpeg = None           # annotated JPEG of the sharpest NG frame
latest_best_ts = 0.0              # time.time() when it was published
best_lock = threading.Lock()

# Candidate pool: capture_loop scores EVERY raw frame (camera rate ~30 FPS, not
# just the ~2.7 FPS that reach inference), so Frame Capture picks the sharpest of
# many more candidates. Only active while Frame Capture is on AND a defect is
# currently on screen (pool_collecting) — so empty-conveyor frames never pollute
# the pool. inference_loop resets it per can and reads it when the can leaves.
pool_lock = threading.Lock()
pool_best_frame = None
pool_best_score = -1.0
pool_collecting = False

# ── Live pipeline perf stats (USB/RTSP diagnostics badge) ────────────────────
# Pure measurement of the server-side live pipeline, mirroring the client-side
# badge STREAM mode already has. Updated by the pipeline threads, exposed as
# the additive "perf" field on /api/detection/status, rendered by the USB tab.
# Never affects capture/inference/counting/DB — every helper swallows errors.
_perf_lock = threading.Lock()
live_perf = {}      # EMA-smoothed values: cam_fps, inf_fps, inf_ms, enc_ms, jpg_kb, disp_fps
_perf_marks = {}    # last-tick timestamps per rate key (one writer thread per key)


def _perf_note(key, value, alpha=0.3):
    """EMA-smooth a diagnostic value into live_perf (same 0.7/0.3 smoothing as
    the STREAM badge). Never raises into a pipeline thread."""
    try:
        with _perf_lock:
            prev = live_perf.get(key)
            live_perf[key] = value if prev is None else prev * (1 - alpha) + value * alpha
    except Exception:
        pass


def _perf_tick(key):
    """Note one occurrence of a repeating event and store its rate (Hz) EMA.
    With several MJPEG viewers the disp_fps ticks interleave — diagnostics only,
    the numbers just read high; single-viewer (the normal case) is accurate."""
    try:
        now = time.perf_counter()
        prev = _perf_marks.get(key)
        _perf_marks[key] = now
        if prev is not None and now > prev:
            _perf_note(key, 1.0 / (now - prev))
    except Exception:
        pass


def _perf_reset():
    """Clear stats at the start of a detection session."""
    with _perf_lock:
        live_perf.clear()
    _perf_marks.clear()


def _perf_snapshot():
    """Rounded copy for the status endpoint."""
    with _perf_lock:
        return {k: round(v, 1) for k, v in live_perf.items()}


def _dent_sharpness(frame, bbox):
    """
    Focus/blur score of the dent region — variance of the Laplacian on the bbox
    crop (higher = sharper). Used to pick the clearest frame of a passing can.
    Returns 0.0 on any problem so it never breaks the inference loop.
    """
    try:
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in bbox]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 - x1 < 4 or y2 - y1 < 4:
            return 0.0
        crop = frame[y1:y2, x1:x2]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())
    except Exception:
        return 0.0


def _frame_sharpness(frame):
    """Whole-frame focus/blur score (variance of Laplacian on a downscaled gray
    copy — cheap enough to run on every captured frame). Higher = sharper. Used
    to score camera-rate candidates for Frame Capture. 0.0 on any problem."""
    try:
        h, w = frame.shape[:2]
        if w > 320:
            frame = cv2.resize(frame, (320, max(1, int(h * 320.0 / w))))
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())
    except Exception:
        return 0.0


def _can_complete(detections, frame_shape, margin=0.02):
    """
    True if the whole workpiece is fully inside the frame (not clipped at an
    edge). Judged from the non-defect 'can'/'good' box — the whole-can class,
    which is present in the raw detections even when it's hidden on an NG display.
    Returns False when there is no such box to judge from (treated as "not
    confirmed complete"). Frame Capture only — never affects detection/counting.
    """
    try:
        h, w = frame_shape[:2]
        mx, my = w * margin, h * margin
        body = [d for d in detections if d["class_name"] in _NON_DEFECT_CLASSES]
        if not body:
            return False
        for d in body:
            x1, y1, x2, y2 = d["bbox"]
            if x1 <= mx or y1 <= my or x2 >= (w - mx) or y2 >= (h - my):
                return False          # touches an edge → the can is clipped
        return True
    except Exception:
        return False


def _publish_best_capture(frame, detections):
    """Annotate the sharpest NG frame of a can and publish it for Frame Capture
    display. No-op if there's nothing to show. Never raises into the loop."""
    global latest_best_jpeg, latest_best_ts
    if frame is None:
        return
    try:
        if detector is not None and detector.model is not None:
            annotated = detector.draw_detections(frame, detections or [])
        else:
            annotated = frame
        ok, buf = cv2.imencode('.jpg', annotated, _JPEG_PARAMS)
        if ok:
            with best_lock:
                latest_best_jpeg = buf.tobytes()
                latest_best_ts = time.time()
    except Exception as e:
        logger.debug(f"Frame Capture publish failed: {e}")

detection_stats = {
    "total_detected": 0,
    "current_defects": 0,
}

# Active inspection mode + model file (mutated via /api/mode/switch)
current_mode = config.DEFAULT_MODE
current_model_file = None     # filename inside weights/<mode>/, None = auto

# Cooldown tracking for defect logging
defect_log_cooldown = {}


# ── Viewfinder (snapshot live preview) ─────────────────────────────────
# A raw-camera preview used by snapshot mode so the operator can aim before
# pressing the shutter. The camera is opened ONCE at the capture resolution
# (the highest mode it supports, picked at start) and stays open for the whole
# snapshot session; the read loop publishes full-resolution frames and the
# MJPEG generator downscales them for a smooth aim stream. The shutter then
# simply grabs the newest already-captured full-res frame — it never touches
# the camera handle, so there is no fragile mid-session release/reopen. Snapshot
# mode is mutually exclusive with live detection (single camera handle).
viewfinder_active = False
viewfinder_camera = None
viewfinder_thread = None
viewfinder_frame = None          # newest RAW (full-res) frame from the camera
viewfinder_seq = 0
vf_lock = threading.Lock()       # guards the published viewfinder frame
viewfinder_capture_size = None   # (w, h) actually opened, for status/diagnostics
viewfinder_frame_ts = 0.0        # time.monotonic() when viewfinder_frame was published
viewfinder_jpeg = None           # newest DISPLAY-downscaled JPEG bytes (encoded once,
                                 # shared by all MJPEG viewers — see generate_viewfinder)

# Serialises camera-ownership transitions (start/stop detection, start/stop
# viewfinder, mode switch). Flask runs threaded, so without this two concurrent
# requests (double-click, multiple tabs, status poller) could both pass an
# `if active` check and open two camera handles / start duplicate threads.
_cam_state_lock = threading.RLock()


def _serialized(fn):
    """Run a route body holding _cam_state_lock so camera-ownership transitions
    (start/stop detection, start/stop viewfinder, mode switch) never interleave.
    Read-only endpoints (status, scan) are intentionally NOT serialized."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _cam_state_lock:
            return fn(*args, **kwargs)
    return wrapper


def _load_detector_for(mode_name: str, model_filename=None):
    """
    Build a YOLODetector for the given mode and load its weights.
    Returns the detector instance (loaded or with model=None on failure).
    """
    mode_cfg = mode_registry.get_mode_config(mode_name)
    model_path = mode_registry.resolve_model_path(mode_name, model_filename)

    if model_path is None:
        logger.warning(
            f"No .pt file found for mode '{mode_name}' in {mode_cfg.WEIGHTS_DIR}. "
            f"Detector will run without a model (camera preview only)."
        )
        # Pass legacy MODEL_PATH so YOLODetector falls back to yolov8n if present.
        det = YOLODetector(model_path=config.MODEL_PATH, mode_config=mode_cfg)
    else:
        det = YOLODetector(model_path=model_path, mode_config=mode_cfg)

    try:
        det.load_model()
    except Exception as e:
        logger.error(f"Detector load error for mode '{mode_name}': {e}")
    return det


def init_system():
    """Initialize detector and database only. Camera is deferred to start-detection."""
    global detector, db, current_mode, current_model_file

    # Initialize YOLO detector for the default mode
    logger.info(f"Initializing YOLO detector (mode='{current_mode}')...")
    detector = _load_detector_for(current_mode, current_model_file)

    # Initialize database (non-blocking)
    logger.info("Initializing database...")
    db = Database()
    if not db.connect():
        logger.warning("Database connection failed. Defect logging will be disabled.")

    logger.info("System initialization completed!")
    return True


def capture_loop():
    """
    Capture thread: grab frames from the camera as fast as it delivers them
    and publish only the newest one. Does no inference, so the camera never
    waits on the model — this is what keeps the displayed feed smooth.
    """
    global latest_raw_frame, raw_frame_seq
    global pool_best_frame, pool_best_score

    logger.info("Capture loop started")
    while detection_active:
        try:
            result = camera.read_frame()
            if result is None:
                time.sleep(0.02)
                continue
            success, frame = result
            if not success or frame is None:
                time.sleep(0.02)
                continue
            with raw_lock:
                latest_raw_frame = frame
                raw_frame_seq += 1
            _perf_tick("cam_fps")

            # Frame Capture candidate pool: score this raw frame and keep the
            # sharpest. Only while the mode is on AND a defect is currently on
            # screen (pool_collecting, set by inference_loop) — so empty-conveyor
            # frames never win. Cheap (downscaled Laplacian); off = zero cost.
            if frame_capture_enabled and pool_collecting:
                s = _frame_sharpness(frame)
                with pool_lock:
                    if s > pool_best_score:
                        pool_best_score = s
                        pool_best_frame = frame
        except Exception as e:
            logger.error(f"Error in capture loop: {e}")
            time.sleep(0.05)
    logger.info("Capture loop stopped")


def inference_loop():
    """
    Inference thread: run the model on the newest captured frame at whatever
    rate it can sustain. Publishes the detection list (the MJPEG generator
    draws it) and handles cooldown-gated DB logging. Skips frames it has
    already processed so it never blocks waiting for new ones.
    """
    global latest_detections, latest_det_frame, latest_det_seq
    global detection_stats, defect_log_cooldown
    global pool_best_frame, pool_best_score, pool_collecting

    logger.info("Inference loop started")
    last_seq = -1
    # Per-can state for edge-triggered counting + DB logging: one physical can =
    # one inspection (not re-counted/re-logged every frame). Local to this thread,
    # so it resets automatically each time detection is (re)started.
    can_present = False
    can_counted_ng = False
    empty_streak = 0
    # Best-frame (Frame Capture): sharpest NG frame seen during the current can's
    # pass. Flushed to the global best-capture when the can leaves.
    best_score = -1.0
    best_frame = None
    best_dets = None

    paused = False
    while detection_active:
        try:
            # ── หยุดตรวจชั่วคราวระหว่าง "ถ่ายรัว" (opt-in ต่อครั้ง) ──
            # คืน CPU/แบนด์วิดท์ RAM ให้เธรดเขียนไฟล์ทั้งหมด. ล้างผลตรวจทิ้งด้วย
            # ไม่งั้น generate_frames จะวาดกรอบเก่าทับเฟรมใหม่ = ชี้จุดผิดแบบมั่นใจ
            if _inference_paused():
                if not paused:
                    paused = True
                    can_present = False
                    can_counted_ng = False
                    empty_streak = 0
                    best_score, best_frame, best_dets = -1.0, None, None
                    pool_collecting = False
                    with det_lock:
                        latest_detections = []
                    detection_stats["current_defects"] = 0
                    logger.info("[hik] หยุดการตรวจภาพสดชั่วคราว "
                                "(ถ่ายรัว / โหมด viewfinder) — การนับและ DB หยุดด้วย")
                # ⚠️ ต้องเผยแพร่เฟรมดิบต่อไป ไม่งั้นโหมด LOCKED ของ generate_frames
                # จะค้างอยู่ที่เฟรมสุดท้ายที่ตรวจ = จอนิ่งสนิททั้งที่กล้องยังทำงาน
                # (ภาพค้างจริงระหว่างถ่ายรัวมาตลอด — เพิ่งเห็นตอนทำโหมด viewfinder)
                with raw_lock:
                    vf_frame, vf_seq = latest_raw_frame, raw_frame_seq
                if vf_frame is not None and vf_seq != last_seq:
                    last_seq = vf_seq
                    with det_lock:
                        latest_detections = []
                        latest_det_frame = vf_frame
                        latest_det_seq = vf_seq
                time.sleep(0.005)
                continue
            if paused:
                paused = False
                last_seq = -1          # เริ่มนับกระป๋องใหม่จากเฟรมล่าสุด
                logger.info("[hik-burst] กลับมาตรวจตามปกติ")

            with raw_lock:
                frame = latest_raw_frame
                seq = raw_frame_seq

            if frame is None or seq == last_seq:
                time.sleep(0.005)   # nothing new yet — yield briefly
                continue
            last_seq = seq

            if detector is not None and detector.model is not None:
                _t_inf = time.perf_counter()
                detections = detector.detect(frame)
                _perf_note("inf_ms", (time.perf_counter() - _t_inf) * 1000.0)
                _perf_tick("inf_fps")
            else:
                detections = []

            # Publish the detections together with the frame they ran on so the
            # generator can draw them in sync (see latest_det_frame comment).
            with det_lock:
                latest_detections = detections
                latest_det_frame = frame
                latest_det_seq = seq

            # Per-can counting + DB logging (edge-triggered): one physical can =
            # one inspection. States: NG (defect), OK (can, no defect), or empty
            # (nothing). A new inspection begins on empty → OK/NG; the same can is
            # never re-counted/re-logged; the can is "gone" only after a few empty
            # frames (debounce). "good"/"can" are never defects.
            defects = [d for d in detections if d["class_name"] not in _NON_DEFECT_CLASSES]
            detection_stats["current_defects"] = len(defects)

            if not detections:
                pool_collecting = False   # stop pooling empty-conveyor frames
                empty_streak += 1
                if empty_streak >= config.DEFECT_RESET_FRAMES:
                    if can_present:
                        # Falling edge — the can has left. Publish the sharpest
                        # frame for Frame Capture (display only; no DB/count effect).
                        # Prefer the camera-rate raw pool (sharper), but RE-RUN
                        # detection on that exact frame so the boxes align to it —
                        # the pool frame never went through inference, and drawing
                        # boxes from a different frame put them off the can. If the
                        # re-check no longer shows a defect, fall back to the
                        # sharpest inferred frame (which definitely shows the dent).
                        sharp = None
                        if frame_capture_enabled:
                            with pool_lock:
                                sharp = pool_best_frame
                        if sharp is not None and detector is not None and detector.model is not None:
                            try:
                                sharp_dets = detector.detect(sharp)
                            except Exception:
                                sharp_dets = []
                            has_defect = any(d["class_name"] not in _NON_DEFECT_CLASSES
                                             for d in sharp_dets)
                            complete = _can_complete(
                                sharp_dets, sharp.shape,
                                getattr(config, "FRAME_CAPTURE_EDGE_MARGIN", 0.02))
                            # Publish the sharp frame only if it still shows the dent
                            # AND the whole can is in view; otherwise fall back to the
                            # best inferred frame (already completeness-weighted).
                            if has_defect and complete:
                                _publish_best_capture(sharp, sharp_dets)
                            else:
                                _publish_best_capture(best_frame, best_dets)
                        else:
                            _publish_best_capture(best_frame, best_dets)
                    can_present = False
                    best_score, best_frame, best_dets = -1.0, None, None
                    with pool_lock:          # clear pool for the next can
                        pool_best_frame, pool_best_score = None, -1.0
            else:
                empty_streak = 0
                if not can_present:          # a new can just entered the frame
                    can_present = True
                    can_counted_ng = False
                    best_score, best_frame, best_dets = -1.0, None, None
                    with pool_lock:          # fresh pool for this can
                        pool_best_frame, pool_best_score = None, -1.0
                if defects:
                    # Frame Capture: prefer frames where the WHOLE can is in view
                    # (not a half-can entering/leaving). Judged from the 'can'/'good'
                    # box we already have. Pool only complete NG frames; and in the
                    # inferred-best score a clipped can is heavily penalised so a
                    # complete frame wins whenever one exists — but a partial one can
                    # still be the fallback if the can never fits fully in view.
                    complete = _can_complete(
                        detections, frame.shape,
                        getattr(config, "FRAME_CAPTURE_EDGE_MARGIN", 0.02))
                    pool_collecting = complete
                    top = max(defects, key=lambda d: d["confidence"])
                    score = (_dent_sharpness(frame, top["bbox"]) * top["confidence"]
                             * (1.0 if complete else 0.25))
                    if score > best_score:
                        best_score = score
                        best_frame = frame
                        best_dets = detections
                    if not can_counted_ng:
                        can_counted_ng = True     # count + log this defective can ONCE
                        detection_stats["total_detected"] += 1
                        if db and db.is_connected:
                            for det in defects:
                                db.log_defect(
                                    defect_type=det["class_name"],
                                    confidence=det["confidence"],
                                    frame=frame,
                                    bbox=det["bbox"],
                                    timestamp=datetime.now()
                                )
                else:
                    pool_collecting = False   # OK frame (can, no defect) → don't pool

        except Exception as e:
            logger.error(f"Error in inference loop: {e}")
            time.sleep(0.1)

    logger.info("Inference loop stopped")


def _live_smooth():
    """
    โหมดแสดงผลของภาพสด: True = ใช้เฟรมดิบล่าสุดแล้ววาดกรอบล่าสุดทับ (ภาพลื่น),
    False = ใช้เฟรมที่โมเดลตรวจจริง (กรอบเป๊ะ แต่ภาพรีเฟรชตามอัตราการตรวจ).

    แยกออกมาเป็นฟังก์ชันเพื่อให้ **เทสต์เรียกตัวเดียวกับที่ ``generate_frames``
    ใช้จริง** — เงื่อนไขนี้เคยอยู่ในตัว loop ซึ่งเทสต์เข้าถึงไม่ได้ ต้องเขียนซ้ำ
    แล้วมีโอกาสเพี้ยนจากของจริงโดยไม่มีใครรู้.

    เหตุผลของสาขากล้องอุตสาหกรรม: เฟรมใหญ่กว่า USB หลายเท่า ⇒ อัตราการตรวจ
    ตกต่ำกว่าเพดานจอ (``STREAM_FPS``) ⇒ โหมด LOCKED ทำให้ "ภาพ" กระตุกตาม
    การตรวจ. เปิด smooth เฉพาะแหล่งนี้ — USB/RTSP/STREAM ไม่ถูกแตะ และการนับ/
    DB/verdict ยังใช้เฟรมที่โมเดลตรวจจริงเสมอ (นี่เป็นเรื่องการแสดงผลล้วน).
    """
    if frame_capture_enabled or getattr(config, "LIVE_SMOOTH_VIDEO", False):
        return True
    if _live_hik_camera() is None:
        return False
    if hik_live_smooth_override is not None:      # ผู้ใช้สลับเองจากหน้าเว็บ
        return bool(hik_live_smooth_override)
    return bool(getattr(config, "HIK_LIVE_SMOOTH_VIDEO", False))


def generate_frames():
    """
    MJPEG generator for USB/RTSP. Two display modes (config.LIVE_SMOOTH_VIDEO):

    • SMOOTH (default): base = the newest RAW frame (camera rate) with the latest
      detections overlaid. The feed stays fluid regardless of how slow inference
      is (important for heavy models like a segmentation bestX.pt). Trade-off: a
      box is drawn on a frame newer than the one it was computed on, so it trails
      slightly while the can is moving — same behaviour as the STREAM overlay.

    • LOCKED: base = the exact frame the model ran on (published together with its
      detections), so the box is pinned to the can. The feed then refreshes only
      at the inference rate — smooth boxes but a stuttery picture on heavy models.

    Either way JPEG is re-encoded only when something changed, so an idle feed
    costs nothing.
    """
    # Placeholder frame (created once)
    placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(placeholder, "Waiting for camera...", (150, 240),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    _, placeholder_buf = cv2.imencode('.jpg', placeholder, _JPEG_PARAMS)
    placeholder_bytes = placeholder_buf.tobytes()

    hold = getattr(config, "FRAME_CAPTURE_HOLD_SEC", 5)
    last_key = None
    frame_bytes = placeholder_bytes

    while True:
        # Live display mode, evaluated per iteration so it tracks the toggle:
        # Frame Capture ON forces SMOOTH live (accuracy comes from the frozen
        # best-frame instead), otherwise follow config.LIVE_SMOOTH_VIDEO (default
        # False = frame-locked, boxes pinned exactly to the inferred frame).
        smooth = _live_smooth()

        # Frame Capture: when on and a fresh best-NG capture exists, freeze the
        # feed on it for `hold` seconds, then fall back to the normal live view.
        if frame_capture_enabled:
            with best_lock:
                bjpeg = latest_best_jpeg
                bts = latest_best_ts
            if bjpeg is not None and (time.time() - bts) < hold:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + bjpeg + b'\r\n')
                time.sleep(1.0 / config.STREAM_FPS)
                continue

        if smooth:
            # Newest raw frame (fluid) + latest detections drawn on top.
            with raw_lock:
                base = latest_raw_frame
                rseq = raw_frame_seq
            with det_lock:
                detections = latest_detections
                dseq = latest_det_seq
            # Re-encode when either the picture (raw) or the boxes (det) change.
            key = (rseq, dseq)
        else:
            # Original: only the exact inferred frame, boxes pinned to it.
            with det_lock:
                base = latest_det_frame
                detections = latest_detections
                key = latest_det_seq

        if base is not None and key != last_key:
            last_key = key

            if detector is not None and detector.model is not None:
                annotated = detector.draw_detections(base, detections)
            else:
                annotated = base.copy()
                cv2.putText(annotated, "Camera Preview (No Model)",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                            (0, 165, 255), 2)

            _t_enc = time.perf_counter()
            ret, buffer = cv2.imencode('.jpg', annotated, _JPEG_PARAMS)
            if ret:
                frame_bytes = buffer.tobytes()
                # Perf badge: encode time / payload size / display refresh rate
                # (only when a NEW frame was actually encoded, like the re-encode
                # guard above — idle repeats don't count as display frames).
                _perf_note("enc_ms", (time.perf_counter() - _t_enc) * 1000.0)
                _perf_note("jpg_kb", len(frame_bytes) / 1024.0)
                _perf_tick("disp_fps")

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

        time.sleep(1.0 / config.STREAM_FPS)


def viewfinder_loop():
    """
    Viewfinder capture thread: grab raw frames (no detection) at camera rate.
    Publishes two things under vf_lock:
      • viewfinder_frame  — the RAW full-res frame (the shutter grabs this)
      • viewfinder_jpeg   — a display-downscaled JPEG, encoded ONCE here so every
        MJPEG viewer just copies the bytes instead of each re-encoding the frame.
    Also stamps viewfinder_frame_ts so the shutter can reject a stale/frozen feed.
    """
    global viewfinder_frame, viewfinder_seq, viewfinder_frame_ts, viewfinder_jpeg

    logger.info("Viewfinder loop started")
    while viewfinder_active:
        try:
            result = viewfinder_camera.read_frame() if viewfinder_camera else None
            if result is None:
                time.sleep(0.02)
                continue
            success, frame = result
            if not success or frame is None:
                time.sleep(0.02)
                continue

            # Downscale + encode the display JPEG once (was per-viewer before).
            h, w = frame.shape[:2]
            if w > _VIEWFINDER_MAX_W:
                scale = _VIEWFINDER_MAX_W / float(w)
                disp = cv2.resize(frame, (_VIEWFINDER_MAX_W, int(h * scale)),
                                  interpolation=cv2.INTER_AREA)
            else:
                disp = frame
            ret, buffer = cv2.imencode('.jpg', disp, _JPEG_PARAMS)

            with vf_lock:
                viewfinder_frame = frame
                viewfinder_frame_ts = time.monotonic()
                viewfinder_seq += 1
                if ret:
                    viewfinder_jpeg = buffer.tobytes()
        except Exception as e:
            logger.error(f"Error in viewfinder loop: {e}")
            time.sleep(0.05)
    logger.info("Viewfinder loop stopped")


def generate_viewfinder():
    """
    MJPEG generator for the viewfinder. Serves the shared JPEG bytes encoded by
    viewfinder_loop — so N concurrent viewers cost one encode, not N. Re-yields
    only when a new frame arrived (tracked by viewfinder_seq).
    """
    placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(placeholder, "Starting camera...", (170, 240),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    _, ph_buf = cv2.imencode('.jpg', placeholder, _JPEG_PARAMS)
    placeholder_bytes = ph_buf.tobytes()
    frame_bytes = placeholder_bytes
    last_seq = -1

    while viewfinder_active:
        with vf_lock:
            seq = viewfinder_seq
            jpeg = viewfinder_jpeg
        if jpeg is not None and seq != last_seq:
            last_seq = seq
            frame_bytes = jpeg
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        # Aim stream runs faster than the live feed for fluid aiming.
        time.sleep(1.0 / config.VIEWFINDER_STREAM_FPS)


# ── Routes ─────────────────────────────────────────────

@app.route('/home')
def home():
    """Neutral landing page shown right after login. Requires only a valid
    session (no specific permission), then offers menu tiles filtered to the
    user's permissions — so limited accounts are not bounced into a 403."""
    return render_template('home.html')


@app.route('/')
def index():
    """Home page - Live detection view."""
    return render_template('index.html')


@app.route('/dashboard')
def dashboard():
    """Analysis dashboard page."""
    return render_template('dashboard.html')


@app.route('/history')
def history():
    """Defect history page."""
    return render_template('history.html')


@app.route('/video_feed')
def video_feed():
    """Video streaming route (MJPEG)."""
    return Response(
        generate_frames(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )


@app.route('/api/camera/control', methods=['POST'])
def api_camera_control():
    """Adjust a camera image control (brightness/contrast, 0-255) on the fly.
    Applies to the live USB detection camera when it is running, otherwise to
    the snapshot viewfinder camera when it is open — so the operator can tune
    lighting while aiming a test shot. Never touches RTSP-config/STREAM paths
    (StreamCamera has no set_control, so it is skipped by the hasattr check)."""
    data = request.get_json(silent=True) or {}
    control = data.get("control")
    if control not in ("brightness", "contrast"):
        return jsonify({"status": "error", "message": "control ต้องเป็น brightness/contrast"}), 400
    try:
        value = int(data.get("value"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "value ต้องเป็นตัวเลข 0-255"}), 400
    value = max(0, min(255, value))
    # Prefer the live detection camera; fall back to the snapshot viewfinder.
    candidates = [camera, viewfinder_camera if viewfinder_active else None]
    for cam in candidates:
        if cam is not None and hasattr(cam, "set_control"):
            actual = cam.set_control(control, value)
            if actual is not None:
                return jsonify({"status": "ok", "control": control,
                                "value": value, "reports": actual})
    return jsonify({"status": "error",
                    "message": "ยังไม่ได้เริ่มกล้อง (กด Start หรือเปิดหน้าถ่ายรูปก่อน)"}), 409


@app.route('/api/frame_capture', methods=['POST'])
def api_frame_capture():
    """Toggle Frame Capture display mode (USB/RTSP). Display-only — the best-frame
    tracking always runs in inference_loop; this just controls whether the feed
    freezes on the sharpest NG frame."""
    global frame_capture_enabled
    data = request.get_json(silent=True) or {}
    frame_capture_enabled = bool(data.get("enabled", False))
    logger.info(f"Frame Capture mode {'ON' if frame_capture_enabled else 'OFF'}")
    return jsonify({"status": "ok", "enabled": frame_capture_enabled})


@app.route('/api/camera/hik/live_smooth', methods=['GET', 'POST'])
def api_hik_live_smooth():
    """
    สลับโหมดแสดงผลของภาพสด "กล้องอุตสาหกรรม" โดยไม่ต้องรีสตาร์ต.

    ``smooth=False`` (ค่าตั้งต้น) = ภาพคือเฟรมที่โมเดลตรวจจริง ⇒ **กรอบล็อกเป๊ะ
    กับกระป๋อง** แต่ภาพอัปเดตตามอัตราการตรวจ.
    ``smooth=True`` = ภาพคือเฟรมดิบล่าสุด ⇒ ลื่น **แต่กรอบตามไม่ทันตอนวัตถุขยับ**.

    ⚠️ **แสดงผลล้วน 100%** — การนับกระป๋อง/บันทึก DB/verdict ใช้เฟรมที่โมเดล
    ตรวจจริงเสมอ ไม่ใช่ภาพที่เห็นบนจอ ⇒ ผลตรวจไม่เปลี่ยนไม่ว่าจะเลือกโหมดไหน.
    ⚠️ มีผลเฉพาะแหล่งภาพ Hikrobot — USB/RTSP/STREAM ไม่ถูกแตะ.
    """
    global hik_live_smooth_override
    default = bool(getattr(config, "HIK_LIVE_SMOOTH_VIDEO", False))
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        if data.get("smooth") is None:
            hik_live_smooth_override = None            # กลับไปใช้ค่าจาก config
        else:
            hik_live_smooth_override = bool(data.get("smooth"))
        logger.info("[hik] โหมดภาพสด = %s",
                    "ลื่น (กรอบตามช้า)" if _live_smooth() else "กรอบล็อกกับเฟรมที่ตรวจ")
    return jsonify({"status": "ok",
                    "smooth": hik_live_smooth_override
                    if hik_live_smooth_override is not None else default,
                    "default": default,
                    "overridden": hik_live_smooth_override is not None})


@app.route('/viewfinder_feed')
def viewfinder_feed():
    """Raw viewfinder stream (MJPEG, no detection) for snapshot aiming."""
    return Response(
        generate_viewfinder(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )


# ── API Endpoints ──────────────────────────────────────

@app.route('/api/camera/scan', methods=['GET'])
def api_scan_cameras():
    """Scan for available cameras. Skips probing the camera that is already open."""
    active_idx = camera.camera_index if (camera and camera.is_initialized) else None
    skip = [active_idx] if active_idx is not None else []
    cameras = scan_cameras_fast(max_index=4, skip_indices=skip)
    # Always include the configured index as fallback so dropdown is never empty
    if not cameras:
        cameras = [{"id": config.CAMERA_INDEX, "name": f"Camera {config.CAMERA_INDEX} (default)"}]
    return jsonify({"cameras": cameras})


# ── Hikrobot (GigE) — endpoint ของแท็บ "กล้องอุตสาหกรรม" ───────────────
# ทุกเส้นทางในบล็อกนี้แตะเฉพาะกล้อง Hikrobot: ไม่มีตัวไหนเรียก Camera/StreamCamera
# หรือแก้สถานะของโหมด USB/RTSP/STREAM/Snapshot เลย.

def _hik_unavailable():
    """ตอบเหมือนกันทุก endpoint เมื่อโมดูลโหลดไม่ได้ — บอกสาเหตุ ไม่ใช่ 500 เปล่า ๆ."""
    return jsonify({
        "status": "error",
        "message": "โมดูลกล้อง Hikrobot โหลดไม่ได้ (ดู log ตอนเริ่มระบบ)",
    }), 500


def _live_hik_camera():
    """กล้อง Hikrobot ที่กำลังสตรีมอยู่ (None ถ้าโหมดปัจจุบันไม่ใช่กล้องนี้)."""
    cam = camera                                    # snapshot ตัวแปร global ครั้งเดียว
    if hik_camera is None or cam is None:
        return None
    if isinstance(cam, hik_camera.HikCamera) and cam.is_initialized:
        return cam
    return None


@app.route('/api/camera/hik/scan', methods=['GET'])
def api_hik_scan():
    """รายชื่อกล้อง Hikrobot + สถานะ SDK (ไม่เปิดกล้อง)."""
    if hik_camera is None:
        return _hik_unavailable()
    status = hik_camera.sdk_status()
    devices, err = ([], status.get("hint"))
    if status["available"]:
        devices, err = hik_camera.scan_devices()
    live = _live_hik_camera()
    return jsonify({
        "status": "ok",
        "sdk": status,
        "devices": devices,
        "error": err,
        "saved": hik_camera.load_settings(),
        "active_source": live.camera_index if live else None,
    })


@app.route('/api/camera/hik/params', methods=['GET'])
@_serialized
def api_hik_params_get():
    """
    ค่า + ช่วงที่ตั้งได้จริงของกล้อง. ถ้ากำลังสตรีมอยู่จะอ่านจากกล้องตัวที่เปิดอยู่;
    ถ้ายังไม่ได้กด Start จะเปิดกล้องชั่วคราวแบบไม่สตรีมเพื่ออ่านค่า แล้วปิดทันที.
    """
    if hik_camera is None:
        return _hik_unavailable()
    live = _live_hik_camera()
    if live is not None:
        return jsonify({"status": "ok", "live": True, **live.describe()})
    if detection_active:
        return jsonify({
            "status": "error",
            "message": "ระบบกำลังตรวจด้วยแหล่งภาพอื่นอยู่ — หยุดก่อนจึงจะอ่านค่ากล้องได้",
        }), 409
    data, err = hik_camera.probe_params(request.args.get("source") or None)
    if data is None:
        return jsonify({"status": "error", "message": err or "อ่านค่ากล้องไม่สำเร็จ"}), 500
    return jsonify({"status": "ok", "live": False, "saved": hik_camera.load_settings(), **data})


@app.route('/api/camera/hik/params', methods=['POST'])
def api_hik_params_set():
    """
    ตั้งค่ากล้อง. ถ้ากล้องกำลังสตรีมจะตั้งให้ทันที (ค่าที่ GenICam ล็อกระหว่างสตรีม
    จะถูกตั้งในช่วงหยุด-เริ่มสตรีมสั้น ๆ ให้เอง) และบันทึกไว้ใช้รอบถัดไป.
    ถ้ายังไม่ได้เริ่มกล้อง = บันทึกอย่างเดียว แล้วจะถูกใช้ตอนกด Start.
    """
    if hik_camera is None:
        return _hik_unavailable()
    body = request.get_json(silent=True) or {}
    params = body.get("params")
    if not isinstance(params, dict) or not params:
        return jsonify({"status": "error", "message": "ต้องส่ง params เป็น object"}), 400

    live = _live_hik_camera()
    applied, failed = {}, {}
    if live is not None:
        result = live.set_params(params)
        applied, failed = result.get("applied", {}), result.get("failed", {})

    saved_ok = None
    if body.get("save", True):
        store = hik_camera.load_settings()
        # เก็บเฉพาะคีย์ที่ระบบรู้จัก — กัน payload แปลกปลอมเข้าไปอยู่ในไฟล์ค่าตั้ง
        for k, v in params.items():
            if k in hik_camera._SPEC_BY_KEY or k == "roi_center":
                store[k] = v
        saved_ok = hik_camera.save_settings(store)

    return jsonify({
        "status": "ok",
        "live": live is not None,
        "applied": applied,
        "failed": failed,
        "saved": saved_ok,
        "params": live.get_params() if live is not None else None,
        "stats": live.stats() if live is not None else None,
    })


@app.route('/api/camera/hik/status', methods=['GET'])
def api_hik_status():
    """สถิติสด: fps จริง · เฟรม/แพ็กเก็ตที่หาย · ความสว่าง · การเก็บชุดข้อมูล."""
    if hik_camera is None:
        return _hik_unavailable()
    live = _live_hik_camera()
    if live is None:
        return jsonify({"status": "ok", "active": False})
    return jsonify({"status": "ok", "active": True,
                    "identity": live.identity, "stats": live.stats()})


@app.route('/api/camera/hik/dataset', methods=['POST'])
def api_hik_dataset():
    """เปิด/ปิดการเก็บภาพความละเอียดเต็มไว้เทรน (ไม่กระทบผลตรวจ/การนับ)."""
    if hik_camera is None:
        return _hik_unavailable()
    live = _live_hik_camera()
    if live is None:
        return jsonify({"status": "error",
                        "message": "ต้องเริ่มกล้องอุตสาหกรรมก่อน (กด Start Detection)"}), 409
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled", False))
    if not enabled:
        return jsonify({"status": "ok", "enabled": False, "dataset": live.stop_dataset()})
    # ตัวเขียนไฟล์มีตัวเดียวต่อกล้อง ⇒ ถ้าปล่อยให้เปิดซ้อนตอนถ่ายรัว ชุดภาพที่ผู้ใช้
    # กำลังถ่ายจะถูกตัดจบกลางคันแบบเงียบ ๆ (เห็นแค่ "จำนวนภาพน้อยกว่าที่ควร")
    if _hik_burst_session is not None:
        return jsonify({"status": "error",
                        "message": "กำลังถ่ายรัวอยู่ — รอให้จบก่อนแล้วค่อยเก็บชุดข้อมูล"}), 409

    def _num(key, default, lo, hi):
        try:
            return max(lo, min(hi, int(body.get(key, default))))
        except (TypeError, ValueError):
            return default

    status = live.start_dataset(
        max_frames=_num("max_frames", getattr(config, "HIK_DATASET_MAX_FRAMES", 2000),
                        1, 200000),
        every_n=_num("every_n", 1, 1, 100),
        duration_s=_num("duration_s", 0, 0, 3600),
    )
    if status.get("error") and not status.get("active"):
        return jsonify({"status": "error", "message": status["error"],
                        "dataset": status}), 507
    return jsonify({"status": "ok", "enabled": True, "dataset": status})


@app.route('/api/camera/hik/shot', methods=['POST'])
def api_hik_shot():
    """
    ถ่าย **1 เฟรมความละเอียดเต็ม** จากกล้องที่กำลังสตรีม แล้วตรวจที่ imgsz สูง.
    ต่างจากภาพสดตรงที่ไม่ถูกย่อ (HIK_LIVE_MAX_WIDTH) จึงใช้ประเมินว่าโมเดลได้อะไร
    เพิ่มจากรายละเอียด 5MP. **ไม่แตะการนับ/DB** — เป็นเครื่องมือทดสอบล้วน ๆ
    (คนละเส้นทางกับ /api/snapshot ซึ่งใช้ viewfinder ของกล้อง USB).
    """
    if hik_camera is None:
        return _hik_unavailable()
    if detector is None or detector.model is None:
        return jsonify({"status": "error", "message": "ยังไม่ได้โหลดโมเดล"}), 400
    live = _live_hik_camera()
    if live is None:
        return jsonify({"status": "error",
                        "message": "ต้องเริ่มกล้องอุตสาหกรรมก่อน (กด Start Detection)"}), 409
    global hik_shot_frame, hik_shot_id
    body = request.get_json(silent=True) or {}
    # detect=False ⇒ เฟส ① เท่านั้น (คืนรูปทันที ไม่รอโมเดล) — ค่าตั้งต้นของหน้าเว็บ
    # ไม่ส่ง detect มาเลย = พฤติกรรมเดิม (ถ่าย+ตรวจในคำขอเดียว) สำหรับ client เก่า
    want_detect = body.get("detect", True)
    try:
        t_cap = time.perf_counter()
        frame = live.snap_full(timeout=3.0)
        capture_ms = round((time.perf_counter() - t_cap) * 1000.0, 1)
        if frame is None:
            return jsonify({
                "status": "error",
                "message": "ไม่ได้ภาพจากกล้องภายในเวลาที่รอ — ปฏิเสธการตัดสินแทนที่จะใช้ภาพเก่า",
            }), 500

        with hik_shot_lock:
            hik_shot_id += 1
            shot_id = hik_shot_id
            hik_shot_frame = frame

        cap_h, cap_w = frame.shape[:2]
        common = {"status": "ok", "shot_id": shot_id,
                  "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                  "capture_size": f"{cap_w}x{cap_h}", "capture_ms": capture_ms}

        if not want_detect:
            # เฟส ① — รูปเปล่า ไม่มีกรอบ ไม่มี verdict. ตั้งใจ **ไม่ใส่ verdict**
            # ให้เลย: หน้าเว็บต้องขึ้นว่า "กำลังตรวจ…" ไม่ใช่ค่าที่ดูเหมือนผลตรวจ
            # (กฎเหล็กข้อ 2 — ยังไม่รู้ ต้องบอกว่ายังไม่รู้)
            t_enc = time.perf_counter()
            disp, _ = _scale_for_display(frame, [], _SNAPSHOT_DISPLAY_MAX_W)
            ret, buffer = cv2.imencode('.jpg', disp, _JPEG_PARAMS)
            if not ret:
                return jsonify({"status": "error", "message": "เข้ารหัสภาพไม่สำเร็จ"}), 500
            common["encode_ms"] = round((time.perf_counter() - t_enc) * 1000.0, 1)
            common["image"] = ("data:image/jpeg;base64,"
                               + base64.b64encode(buffer.tobytes()).decode("ascii"))
            common["pending_detect"] = True
            return jsonify(common)

        imgsz = _snapshot_imgsz(body.get("imgsz"))
        out = _hik_shot_inspect(frame, imgsz)
        out.update(common)
        return jsonify(out)
    except Exception as e:
        logger.error(f"Hikrobot shot failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"ถ่ายภาพไม่สำเร็จ: {e}"}), 500


def _hik_shot_inspect(frame, imgsz):
    """ตรวจเฟรมที่ถ่ายไว้ + วาดกรอบ + เข้ารหัสเป็น data URI (ใช้ร่วม 2 เส้นทาง)."""
    t_wait = time.perf_counter()
    detections = detector.detect(frame, imgsz=imgsz)
    total_ms = round((time.perf_counter() - t_wait) * 1000.0, 1)
    # แยก "รอคิว" ออกจาก "เวลาของโมเดล" — ไม่งั้นตัวเลขเดียวจะบอกไม่ได้ว่าช้า
    # เพราะโมเดลหนัก หรือเพราะการตรวจสดกำลังใช้ iGPU อยู่ (วิธีแก้คนละเรื่อง)
    wait_ms = float(getattr(detector, "last_wait_ms", 0.0) or 0.0)
    dents = [d for d in detections if d["class_name"] not in _NON_DEFECT_CLASSES]

    t_enc = time.perf_counter()
    disp_frame, disp_dets = _scale_for_display(frame, detections, _SNAPSHOT_DISPLAY_MAX_W)
    annotated = detector.draw_detections(disp_frame, disp_dets)
    ret, buffer = cv2.imencode('.jpg', annotated, _JPEG_PARAMS)
    if not ret:
        raise RuntimeError("เข้ารหัสภาพไม่สำเร็จ")
    return {
        "status": "ok",
        "image": "data:image/jpeg;base64," + base64.b64encode(buffer.tobytes()).decode("ascii"),
        "verdict": "ng" if dents else "ok",
        "dent_count": len(dents),
        "max_confidence": round(max((d["confidence"] for d in dents), default=0.0), 2),
        "infer_ms": round(max(0.0, total_ms - wait_ms), 1),
        "wait_ms": round(wait_ms, 1),
        "infer_imgsz": imgsz,
        "encode_ms": round((time.perf_counter() - t_enc) * 1000.0, 1),
        "pending_detect": False,
    }


@app.route('/api/camera/hik/live_detect', methods=['GET', 'POST'])
def api_hik_live_detect():
    """
    เปิด/ปิด **การตรวจภาพสด** ของกล้องอุตสาหกรรม (ภาพยังสดอยู่ตามปกติ).

    ใช้กับเวิร์กโฟลว์ "ถ่าย 1 เฟรมแล้วค่อยตรวจ" — ระหว่างเล็งกล้องไม่ต้องการ
    ผลตรวจสดอยู่แล้ว และการตรวจสดกิน iGPU จนช็อตต้องรอคิว

    ⚠️ **ปิดแล้วการนับกระป๋องและการบันทึก DB หยุดด้วย** (ใช้เส้นทางเดียวกับ
    "หยุดโมเดลระหว่างถ่ายรัว") ⇒ หน้าเว็บต้องขึ้นคำเตือนตลอดช่วงที่ปิด.
    ค่าถูกรีเซ็ตเป็น "ตรวจ" ทุกครั้งที่กด Start Detection.
    """
    global hik_live_detect_off
    if hik_camera is None:
        return _hik_unavailable()
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        hik_live_detect_off = not bool(data.get("enabled", True))
        logger.info("[hik] การตรวจภาพสด = %s",
                    "ปิด (viewfinder เท่านั้น — ไม่นับ ไม่บันทึก)"
                    if hik_live_detect_off else "เปิด")
    return jsonify({"status": "ok", "enabled": not hik_live_detect_off})


@app.route('/api/camera/hik/shot/inspect', methods=['POST'])
def api_hik_shot_inspect():
    """
    เฟส ② ของ "ถ่าย 1 เฟรม": ตรวจเฟรมที่เฟส ① เก็บไว้.

    แยกจากการถ่ายเพราะ inference ที่ imgsz 1280 บนสถานีใช้ ~420 ms ⇒ ถ้ารวม
    อยู่ในคำขอเดียว ผู้ใช้จะไม่เห็นรูปเลยจนกว่าโมเดลจะเสร็จ. เฟส ① คืนรูปใน
    ~50 ms แล้วเฟสนี้ค่อยเติมกรอบ/ผลตรวจทีหลัง.

    ``shot_id`` กันการตรวจเฟรมผิดใบ — ถ่ายใหม่ระหว่างที่เฟสนี้ยังไม่จบ จะได้ 409
    แทนที่จะเงียบ ๆ คืนผลของเฟรมก่อนหน้า (กฎเหล็กข้อ 2).
    """
    if hik_camera is None:
        return _hik_unavailable()
    if detector is None or detector.model is None:
        return jsonify({"status": "error", "message": "ยังไม่ได้โหลดโมเดล"}), 400
    body = request.get_json(silent=True) or {}
    with hik_shot_lock:
        frame = hik_shot_frame
        cur_id = hik_shot_id
    if frame is None:
        return jsonify({"status": "error", "message": "ยังไม่มีภาพที่ถ่ายไว้"}), 409
    want_id = body.get("shot_id")
    if want_id is not None and int(want_id) != cur_id:
        return jsonify({"status": "error",
                        "message": "มีการถ่ายภาพใหม่ระหว่างรอผลตรวจ — สั่งตรวจใหม่อีกครั้ง",
                        "shot_id": cur_id}), 409
    try:
        out = _hik_shot_inspect(frame, _snapshot_imgsz(body.get("imgsz")))
        out["shot_id"] = cur_id
        cap_h, cap_w = frame.shape[:2]
        out["capture_size"] = f"{cap_w}x{cap_h}"
        return jsonify(out)
    except Exception as e:
        logger.error(f"Hikrobot shot inspect failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"ตรวจภาพไม่สำเร็จ: {e}"}), 500


# ── โหมด "ถ่ายรัว" (burst) ────────────────────────────────────────────
# เครื่องมือทดสอบล้วน ๆ: **ไม่แตะการนับ / ไม่เขียน DB / ไม่แตะ verdict ของโหมดสด**
# เหมือนปุ่ม "ถ่าย 1 เฟรม" เดิม. ใช้ตัวเขียนไฟล์ตัวเดียวกับการเก็บชุดข้อมูล
# (`HikCamera.start_dataset`) จึงไม่มีเธรดใหม่มาแย่งเฟรมกับ `capture_loop`.

_hik_burst_session = None          # ชื่อชุดที่กำลังถ่ายอยู่ (None = ไม่ได้ถ่าย)
_hik_burst_deadline = 0.0          # เวลาที่ควรจบ — กันเคส "ไม่มีเฟรมเข้ามาเลย"
_hik_burst_pause_inf = False       # ผู้ใช้ขอหยุดโมเดลระหว่างถ่ายรัวรอบนี้หรือไม่


def _inference_paused():
    """
    True เฉพาะตอนที่ **กำลังถ่ายรัวอยู่จริง และผู้ใช้ติ๊กขอหยุดโมเดล**.

    ⚠️ ผูกกับ ``_hik_burst_deadline`` โดยตั้งใจ: ต่อให้เส้นทางปิดงานพังไปทั้งหมด
    (exception / กล้องหลุด / เบราว์เซอร์ปิด) การหยุดตรวจก็จะคลายเองเมื่อถึงเวลา
    ที่ตั้งไว้ + 3 วินาที — **เป็นไปไม่ได้ที่ระบบจะค้างในสภาพ "ไม่ตรวจ" ตลอดไป**
    """
    if hik_live_detect_off and _live_hik_camera() is not None:
        return True                       # ผู้ใช้สั่งให้ภาพสดเป็นแค่ viewfinder
    return bool(_hik_burst_pause_inf and _hik_burst_session is not None
                and _hik_burst_deadline and time.time() < _hik_burst_deadline)


def _hik_burst_unavailable():
    return jsonify({"status": "error",
                    "message": "โมดูลถ่ายรัวโหลดไม่ได้ (ดู log ตอนเริ่มระบบ)"}), 500


def _hik_burst_guard():
    """ด่านร่วมของทุก endpoint ของโหมดนี้ — คืน response เมื่อใช้ไม่ได้."""
    if hik_burst is None:
        return _hik_burst_unavailable()
    return None


def _burst_error(e, code=400):
    return jsonify({"status": "error", "message": str(e)}), code


def _send_jpeg(path):
    """ส่งไฟล์ภาพ — ถ้าไฟล์หายไประหว่างทางให้ตอบ 404 ไม่ใช่ 500.
    เกิดขึ้นเป็นปกติ: ผู้ใช้กดลบขณะที่ภาพย่อในแกลเลอรียังทยอยโหลดอยู่
    (เบราว์เซอร์ขอทีละใบ) — ไม่ใช่ข้อผิดพลาดของระบบ ไม่ควรพ่น stack trace."""
    from flask import send_file
    try:
        return send_file(path, mimetype="image/jpeg")
    except (FileNotFoundError, OSError):
        return _burst_error("ไม่พบภาพนี้ (อาจถูกลบไปแล้ว)", 404)


def _hik_burst_meta(live, seconds):
    """ค่าตั้งกล้อง ณ วินาทีที่กดถ่าย — ถ้าไม่เก็บไว้ ภาพที่ได้จะเทียบข้ามรอบไม่ได้
    (ซึ่งคือทั้งหมดของการทดสอบ "exposure เท่าไรถึงจะไม่เบลอ")."""
    meta = {"seconds": seconds, "source": live.camera_index,
            "identity": live.identity}
    try:
        params = live.get_params() or {}
    except Exception:                                # pragma: no cover
        params = {}

    def _val(key):
        entry = params.get(key)
        if isinstance(entry, dict) and entry.get("supported"):
            return entry.get("value")
        return None

    meta["exposure_us"] = _val("exposure_us")
    meta["gain_db"] = _val("gain_db")
    meta["exposure_auto"] = (params.get("exposure_auto") or {}).get("symbolic")
    # ค่าที่ใช้ตอบว่า "ทำไมได้ fps เท่านี้" — ถ้าไม่เก็บไว้ ต้องมานั่งเดาทีหลัง
    meta["framerate_enable"] = _val("framerate_enable")
    meta["framerate"] = _val("framerate")
    meta["packet_size"] = _val("packet_size")
    meta["packet_delay"] = _val("packet_delay")
    meta["pixel_format"] = (params.get("pixel_format") or {}).get("symbolic")
    meta["trigger_mode"] = (params.get("trigger_mode") or {}).get("symbolic")
    w, h = _val("width"), _val("height")
    if w and h:
        meta["size"] = "%dx%d" % (int(w), int(h))
    elif getattr(live, "width", None):
        meta["size"] = "%dx%d" % (live.width, live.height)
    stats = live.stats() or {}
    meta["fps_at_start"] = stats.get("fps")
    meta["mean_brightness_at_start"] = stats.get("mean_brightness")
    return meta


def _hik_burst_finalize(live):
    """ปิดชุดที่ถ่ายจบแล้วให้เรียบร้อย แล้วคืนสถานะสุดท้าย (idempotent)."""
    global _hik_burst_session, _hik_burst_deadline, _hik_burst_pause_inf
    status = live.stop_dataset() if live is not None else None
    name = _hik_burst_session
    _hik_burst_session = None
    _hik_burst_deadline = 0.0
    _hik_burst_pause_inf = False
    if name and hik_burst is not None:
        logger.info("[hik-burst] ถ่ายรัวจบ: %s", name)
    return name, status


@app.route('/api/camera/hik/burst', methods=['POST'])
def api_hik_burst_start():
    """เริ่มถ่ายรัว — เก็บทุกเฟรมที่กล้องส่งมาเป็นเวลา N วินาที."""
    global _hik_burst_session, _hik_burst_deadline, _hik_burst_pause_inf
    bad = _hik_burst_guard()
    if bad:
        return bad
    if hik_camera is None:
        return _hik_unavailable()
    live = _live_hik_camera()
    if live is None:
        return jsonify({"status": "error",
                        "message": "ต้องเริ่มกล้องอุตสาหกรรมก่อน (กด Start Detection)"}), 409
    if _hik_burst_session is not None:
        return jsonify({"status": "error", "message": "กำลังถ่ายรัวอยู่แล้ว"}), 409
    if (live.stats() or {}).get("dataset"):
        return jsonify({
            "status": "error",
            "message": "กำลังเก็บภาพชุดข้อมูลอยู่ — ปิดก่อนแล้วค่อยถ่ายรัว "
                       "(ทั้งสองอย่างใช้ตัวเขียนไฟล์ตัวเดียวกัน)",
        }), 409

    body = request.get_json(silent=True) or {}
    try:
        seconds = int(body.get("seconds", getattr(config, "HIK_BURST_DEFAULT_SECONDS", 10)))
    except (TypeError, ValueError):
        seconds = getattr(config, "HIK_BURST_DEFAULT_SECONDS", 10)
    seconds = max(1, min(int(getattr(config, "HIK_BURST_MAX_SECONDS", 60)), seconds))
    try:
        every_n = max(1, min(20, int(body.get("every_n", 1))))
    except (TypeError, ValueError):
        every_n = 1

    # โหมด "คัดใบที่ดีที่สุดต่อหน้าต่างเวลา" — เปิดได้ต่อครั้งจากหน้าเว็บ แต่ต้อง
    # ไม่เกินเพดานของ config (0 ใน config = ปิดทั้งระบบ = พฤติกรรมเดิม 100%)
    cfg_window = int(getattr(config, "HIK_BURST_WINDOW_MS", 0) or 0)
    try:
        want_window = int(body.get("window_ms") or 0)
    except (TypeError, ValueError):
        want_window = 0
    window_ms = max(0, min(5000, want_window)) if cfg_window > 0 else 0
    if window_ms:
        every_n = 1              # สองอย่างทำงานเดียวกัน — ใช้พร้อมกันได้ผลแย่ที่สุดของทั้งคู่

    status = live.start_dataset(
        root=getattr(config, "HIK_BURST_DIR", "data/hik_burst"),
        max_frames=int(getattr(config, "HIK_BURST_MAX_FRAMES", 3000)),
        every_n=every_n, duration_s=seconds,
        jpeg_quality=int(getattr(config, "HIK_BURST_JPEG_QUALITY", 95)),
        meta=_hik_burst_meta(live, seconds),
        window_ms=window_ms,
    )
    if not status or (status.get("error") and not status.get("active")):
        return jsonify({"status": "error",
                        "message": (status or {}).get("error", "เริ่มถ่ายรัวไม่สำเร็จ"),
                        "burst": status}), 507
    _hik_burst_session = os.path.basename(status.get("dir") or "")
    _hik_burst_deadline = time.time() + seconds + 3.0
    # ต้องเปิดทั้ง flag ของระบบ **และ** ติ๊กมาในคำขอ จึงจะหยุดตรวจ
    _hik_burst_pause_inf = bool(body.get("pause_inference")) and bool(
        getattr(config, "HIK_BURST_PAUSE_INFERENCE", False))
    logger.info("[hik-burst] เริ่มถ่ายรัว %d วินาที → %s", seconds, _hik_burst_session)
    return jsonify({"status": "ok", "session": _hik_burst_session,
                    "seconds": seconds, "burst": status,
                    "pause_inference": _hik_burst_pause_inf})


@app.route('/api/camera/hik/burst', methods=['GET'])
def api_hik_burst_status():
    """สถานะการถ่ายรัว + สถานะงานเบื้องหลัง (วัดผล/ตรวจ)."""
    bad = _hik_burst_guard()
    if bad:
        return bad
    live = _live_hik_camera()
    out = {"status": "ok", "session": _hik_burst_session,
           "job": hik_burst.job_status(), "free_mb": hik_burst.free_mb()}
    if _hik_burst_session is None or live is None:
        if _hik_burst_session is not None:            # กล้องหลุดกลางคัน
            _hik_burst_finalize(None)
            out["session"] = None
        out["capturing"] = False
        return jsonify(out)

    ds = (live.stats() or {}).get("dataset") or {}
    # ตัวเขียนหยุดเองเมื่อครบเวลา/ครบจำนวน/ดิสก์ใกล้เต็ม — ตรงนี้คือจุดที่เก็บกวาด.
    # `_hik_burst_deadline` กันเคสที่ **ไม่มีเฟรมเข้ามาเลย** (put() ไม่ถูกเรียก
    # ⇒ ตัวเขียนไม่มีทางรู้ว่าครบเวลาแล้ว) ซึ่งจะค้างเป็น "กำลังถ่าย" ตลอดไป
    done = (not ds) or ds.get("finished_reason") or not ds.get("active")
    if done or (_hik_burst_deadline and time.time() > _hik_burst_deadline):
        name, final = _hik_burst_finalize(live)
        out.update(capturing=False, session=None, finished=name,
                   burst=final or ds)
        return jsonify(out)
    out.update(capturing=True, burst=ds, paused_inference=_inference_paused())
    return jsonify(out)


@app.route('/api/camera/hik/burst', methods=['DELETE'])
def api_hik_burst_stop():
    """หยุดถ่ายรัวก่อนครบเวลา."""
    bad = _hik_burst_guard()
    if bad:
        return bad
    if _hik_burst_session is None:
        return jsonify({"status": "ok", "session": None, "burst": None})
    name, final = _hik_burst_finalize(_live_hik_camera())
    return jsonify({"status": "ok", "session": None, "finished": name, "burst": final})


@app.route('/api/camera/hik/bursts', methods=['GET'])
def api_hik_bursts():
    bad = _hik_burst_guard()
    if bad:
        return bad
    return jsonify({"status": "ok", "sessions": hik_burst.list_sessions(),
                    "free_mb": hik_burst.free_mb(),
                    "job": hik_burst.job_status(),
                    "capturing": _hik_burst_session,
                    "mm_per_px": getattr(config, "HIK_BURST_MM_PER_PX", None),
                    "autodetect_top": int(getattr(config, "HIK_BURST_AUTODETECT_TOP", 12)),
                    "can_pause_inference": bool(
                        getattr(config, "HIK_BURST_PAUSE_INFERENCE", False)),
                    "window_ms_default": int(
                        getattr(config, "HIK_BURST_WINDOW_MS", 0) or 0)})


@app.route('/api/camera/hik/bursts/<name>', methods=['GET'])
def api_hik_burst_detail(name):
    bad = _hik_burst_guard()
    if bad:
        return bad
    sort = request.args.get("sort", "sharp")
    try:
        limit = max(0, min(500, int(request.args.get("limit", 120))))
        offset = max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        limit, offset = 120, 0
    try:
        data = hik_burst.session_detail(name, sort=sort, limit=limit, offset=offset)
    except ValueError as e:
        return _burst_error(e, 404)
    data.update(status="ok", job=hik_burst.job_status())
    return jsonify(data)


@app.route('/api/camera/hik/bursts/<name>', methods=['DELETE'])
def api_hik_burst_delete(name):
    bad = _hik_burst_guard()
    if bad:
        return bad
    if name == _hik_burst_session:
        return jsonify({"status": "error",
                        "message": "ชุดนี้กำลังถ่ายอยู่ — หยุดก่อนแล้วค่อยลบ"}), 409
    # งานวัดผล/ตรวจที่ยังวิ่งอยู่จะเขียนไฟล์กลับลงโฟลเดอร์ที่เพิ่งลบไป (metrics.json /
    # _thumbs) ⇒ เหลือ "ซากชุด" ที่ไม่มีภาพสักใบค้างอยู่ในรายการ. หยุดงานก่อนเสมอ
    if hik_burst.job_running_on(name):
        hik_burst.cancel_job(wait=3.0)
    try:
        hik_burst.delete_session(name)
    except ValueError as e:
        return _burst_error(e, 404)
    except OSError as e:
        return _burst_error("ลบไม่สำเร็จ: %s" % e, 500)
    return jsonify({"status": "ok", "deleted": name, "free_mb": hik_burst.free_mb()})


@app.route('/api/camera/hik/bursts/<name>/frames', methods=['DELETE'])
def api_hik_burst_delete_frames(name):
    bad = _hik_burst_guard()
    if bad:
        return bad
    files = (request.get_json(silent=True) or {}).get("files") or []
    if not isinstance(files, list):
        return _burst_error("ต้องส่ง files เป็นลิสต์")
    try:
        removed = hik_burst.delete_frames(name, files[:2000])
    except ValueError as e:
        return _burst_error(e, 404)
    return jsonify({"status": "ok", "removed": removed, "count": len(removed),
                    "free_mb": hik_burst.free_mb()})


@app.route('/api/camera/hik/bursts/<name>/metrics', methods=['POST'])
def api_hik_burst_metrics(name):
    """สั่งวัดความคม/ความเบลอของทั้งชุด (งานเบื้องหลัง — 690 ภาพ ≈ 20-30 วินาที)."""
    bad = _hik_burst_guard()
    if bad:
        return bad
    try:
        hik_burst.session_dir(name)
    except ValueError as e:
        return _burst_error(e, 404)
    ok, msg = hik_burst.start_job(
        "metrics", name, lambda job: hik_burst.compute_metrics(name, job=job))
    if not ok:
        return jsonify({"status": "error", "message": msg,
                        "job": hik_burst.job_status()}), 409
    return jsonify({"status": "ok", "job": hik_burst.job_status()})


def _burst_detect_one(path):
    """ตรวจ 1 ภาพจากไฟล์ — เส้นทางเดียวกับปุ่ม "ถ่าย 1 เฟรม" (imgsz สูง)."""
    frame = cv2.imread(path)
    if frame is None:
        raise RuntimeError("อ่านไฟล์ภาพไม่ได้")
    imgsz = _snapshot_imgsz(None)
    t0 = time.perf_counter()
    detections = detector.detect(frame, imgsz=imgsz)
    infer_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    dents = [d for d in detections if d["class_name"] not in _NON_DEFECT_CLASSES]
    return {"verdict": "ng" if dents else "ok", "dent_count": len(dents),
            "max_confidence": round(max((d["confidence"] for d in dents), default=0.0), 2),
            "infer_ms": infer_ms, "imgsz": imgsz,
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


@app.route('/api/camera/hik/bursts/<name>/detect', methods=['POST'])
def api_hik_burst_detect(name):
    """ส่งภาพในชุดเข้าโมเดล — เลือกได้: ระบุไฟล์เอง / N ใบที่คมที่สุด / ทั้งชุด."""
    bad = _hik_burst_guard()
    if bad:
        return bad
    if detector is None or detector.model is None:
        return jsonify({"status": "error", "message": "ยังไม่ได้โหลดโมเดล"}), 400
    body = request.get_json(silent=True) or {}
    try:
        path = hik_burst.session_dir(name)
    except ValueError as e:
        return _burst_error(e, 404)

    if body.get("all"):
        files = hik_burst.list_frames(path)
    elif body.get("top"):
        try:
            top = max(1, min(200, int(body["top"])))
        except (TypeError, ValueError):
            top = int(getattr(config, "HIK_BURST_AUTODETECT_TOP", 12))
        files = hik_burst.top_sharp_files(name, top)
        if not files:
            return jsonify({"status": "error",
                            "message": "ยังไม่ได้วัดความคมของชุดนี้ — กดวัดผลก่อน "
                                       "(จะได้ไม่ต้องเดาว่าใบไหนคมที่สุด)"}), 409
    else:
        files = [f for f in (body.get("files") or []) if isinstance(f, str)][:500]
    if not files:
        return _burst_error("ไม่มีภาพให้ตรวจ")

    ok, msg = hik_burst.start_job(
        "detect", name,
        lambda job: hik_burst.run_detect(name, files, _burst_detect_one, job=job))
    if not ok:
        return jsonify({"status": "error", "message": msg,
                        "job": hik_burst.job_status()}), 409
    return jsonify({"status": "ok", "count": len(files), "job": hik_burst.job_status()})


@app.route('/api/camera/hik/burst-job', methods=['DELETE'])
def api_hik_burst_job_cancel():
    bad = _hik_burst_guard()
    if bad:
        return bad
    hik_burst.cancel_job()
    return jsonify({"status": "ok", "job": hik_burst.job_status()})


@app.route('/api/camera/hik/bursts/<name>/thumb/<filename>', methods=['GET'])
def api_hik_burst_thumb(name, filename):
    """ภาพย่อ — สร้างให้อัตโนมัติถ้ายังไม่มี (ปกติสร้างไว้แล้วตอนวัดผล)."""
    bad = _hik_burst_guard()
    if bad:
        return bad
    try:
        full = hik_burst.frame_path(name, filename)
    except ValueError as e:
        return _burst_error(e, 404)
    if not os.path.isfile(full):
        return _burst_error("ไม่พบภาพนี้ (อาจถูกลบไปแล้ว)", 404)
    thumb = os.path.join(os.path.dirname(full), hik_burst.THUMB_DIR, filename)
    if not os.path.isfile(thumb):
        img = cv2.imread(full, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return _burst_error("อ่านไฟล์ภาพไม่ได้", 500)
        hik_burst._thumb(os.path.dirname(full), filename, img,
                         int(getattr(config, "HIK_BURST_THUMB_WIDTH", 260)))
    return _send_jpeg(full if not os.path.isfile(thumb) else thumb)


@app.route('/api/camera/hik/bursts/<name>/frame/<filename>', methods=['GET'])
def api_hik_burst_frame(name, filename):
    """ภาพเต็ม — ``?annotate=1`` วาดกรอบผลตรวจ, ``?roi=1`` วาดกรอบบริเวณที่วัด.
    ทั้งสองอย่างเป็น **การแสดงผลล้วน** ไม่เขียนทับไฟล์ต้นฉบับ."""
    bad = _hik_burst_guard()
    if bad:
        return bad
    try:
        full = hik_burst.frame_path(name, filename)
    except ValueError as e:
        return _burst_error(e, 404)
    if not os.path.isfile(full):
        return _burst_error("ไม่พบภาพนี้ (อาจถูกลบไปแล้ว)", 404)

    want_box = request.args.get("annotate") == "1"
    want_roi = request.args.get("roi") == "1"
    if not want_box and not want_roi:
        return _send_jpeg(full)

    frame = cv2.imread(full)
    if frame is None:
        return _burst_error("อ่านไฟล์ภาพไม่ได้", 500)
    dets = []
    if want_box and detector is not None and detector.model is not None:
        try:
            dets = detector.detect(frame, imgsz=_snapshot_imgsz(None))
        except Exception as e:                        # pragma: no cover
            logger.warning("[hik-burst] ตรวจภาพเพื่อแสดงผลไม่สำเร็จ: %s", e)
    disp, disp_dets = _scale_for_display(frame, dets, _SNAPSHOT_DISPLAY_MAX_W)
    if want_roi:
        metrics = hik_burst.load_metrics(name) or {}
        rec = (metrics.get("frames") or {}).get(filename) or {}
        roi = rec.get("roi")
        if roi:
            scale = disp.shape[1] / float(frame.shape[1])
            x, y, w, h = [int(v * scale) for v in roi]
            cv2.rectangle(disp, (x, y), (x + w, y + h), (255, 200, 0), 2)
    if disp_dets:
        disp = detector.draw_detections(disp, disp_dets)
    ok, buf = cv2.imencode('.jpg', disp, _JPEG_PARAMS)
    if not ok:
        return _burst_error("เข้ารหัสภาพไม่สำเร็จ", 500)
    return Response(buf.tobytes(), mimetype="image/jpeg")


@app.route('/api/detection/start', methods=['POST'])
@_serialized
def start_detection():
    """Start defect detection with the selected camera."""
    global detection_active, capture_thread, inference_thread, camera
    global latest_raw_frame, raw_frame_seq, latest_detections
    global latest_det_frame, latest_det_seq, hik_live_detect_off
    global latest_best_jpeg, latest_best_ts
    global pool_best_frame, pool_best_score, pool_collecting

    if detection_active:
        return jsonify({"status": "already_running", "message": "Detection already active"}), 200

    if viewfinder_active:
        return jsonify({
            "status": "error",
            "message": "ปิดโหมดถ่ายรูปก่อน แล้วจึงเริ่มตรวจสด"
        }), 409

    model_ready = detector is not None and detector.model is not None
    if not model_ready:
        logger.warning("No YOLO model loaded — camera will open in preview-only mode (no detection).")

    # Get camera index from request (default to config value)
    data = request.get_json(silent=True) or {}
    camera_index_raw = data.get("camera_index", config.CAMERA_INDEX)

    # Keep RTSP/HTTP URLs as strings; convert numeric values to int
    if isinstance(camera_index_raw, str) and camera_index_raw.isdigit():
        camera_index = int(camera_index_raw)
    elif isinstance(camera_index_raw, (int, float)):
        camera_index = int(camera_index_raw)
    else:
        camera_index = camera_index_raw  # RTSP URL string

    # Initialize camera on demand. The STREAM sentinel uses a virtual camera fed
    # by frames pushed from the browser (/api/stream/push); everything else opens
    # a real USB/RTSP camera exactly as before.
    if hik_camera is not None and hik_camera.is_hik_source(camera_index):
        # กล้องอุตสาหกรรม Hikrobot (GigE). HikCamera มีสัญญาเดียวกับ Camera
        # (initialize/read_frame/release) ⇒ capture_loop/inference_loop ไม่ต้องรู้จักมัน.
        camera = hik_camera.HikCamera(camera_index=camera_index)
        if not camera.initialize():
            msg = camera.last_error or "เปิดกล้องอุตสาหกรรมไม่สำเร็จ"
            camera = None
            logger.error(f"Hikrobot camera open failed: {msg}")
            return jsonify({"status": "error", "message": msg}), 500
    elif camera_index == config.STREAM_SOURCE_SENTINEL:
        camera = StreamCamera(camera_index=camera_index)
        camera.initialize()  # never fails — just arms the push buffer
    else:
        # Live camera: pass opt-in exposure config (None = leave camera default,
        # so this changes nothing unless the operator sets it). Snapshot/viewfinder
        # create their own Camera without these → unaffected.
        camera = Camera(
            camera_index=camera_index,
            auto_exposure=getattr(config, "CAMERA_AUTO_EXPOSURE", None),
            exposure=getattr(config, "CAMERA_EXPOSURE", None),
            brightness=getattr(config, "CAMERA_BRIGHTNESS", None),
            contrast=getattr(config, "CAMERA_CONTRAST", None),
        )
        if not camera.initialize():
            available = scan_cameras_fast()
            hint = ""
            if available:
                ids = [c["id"] for c in available]
                hint = f" Available indices: {ids}. Try one of these."
            else:
                hint = " No cameras found — check connection and drivers."
            return jsonify({
                "status": "error",
                "message": f"Cannot open camera {camera_index}.{hint}",
                "available_cameras": available
            }), 500

    # Reset shared frame/detection state from any previous session
    with raw_lock:
        latest_raw_frame = None
        raw_frame_seq = 0
    with det_lock:
        latest_detections = []
        latest_det_frame = None
        latest_det_seq = -1
    with best_lock:                    # clear any stale Frame Capture from before
        latest_best_jpeg = None
        latest_best_ts = 0.0
    with pool_lock:                    # clear any stale candidate pool
        pool_best_frame = None
        pool_best_score = -1.0
    pool_collecting = False
    _perf_reset()                      # fresh perf-badge stats for this session

    # ⚠️ เริ่มรอบใหม่ = ต้องตรวจเสมอ. โหมด viewfinder หยุดการนับ/บันทึก DB
    # ถ้าปล่อยให้ค้างข้ามรอบ ผู้ใช้จะกด Start แล้วคิดว่าระบบกำลังตรวจอยู่
    # ทั้งที่ไม่ได้ตรวจเลย (จุดบอด QC — กฎเหล็กข้อ 2)
    hik_live_detect_off = False

    detection_active = True
    capture_thread = threading.Thread(target=capture_loop, daemon=True)
    inference_thread = threading.Thread(target=inference_loop, daemon=True)
    capture_thread.start()
    inference_thread.start()

    logger.info(f"Detection started on camera {camera_index}")
    return jsonify({"status": "started", "message": f"Detection started on camera {camera_index}"})


@app.route('/api/detection/stop', methods=['POST'])
@_serialized
def stop_detection():
    """Stop defect detection and release camera."""
    global detection_active, camera, capture_thread, inference_thread
    global latest_raw_frame, latest_detections, latest_det_frame, latest_det_seq

    detection_active = False

    # Wait for both worker threads to exit before releasing the camera so
    # capture_loop never reads from a released handle.
    for t in (capture_thread, inference_thread):
        if t is not None:
            t.join(timeout=1.0)
    capture_thread = None
    inference_thread = None

    if camera:
        camera.release()
        camera = None

    with raw_lock:
        latest_raw_frame = None
    with det_lock:
        latest_detections = []
        latest_det_frame = None
        latest_det_seq = -1

    logger.info("Detection stopped")
    return jsonify({"status": "stopped", "message": "Detection stopped successfully"})


@app.route('/api/stream/push', methods=['POST'])
def api_stream_push():
    """
    Receive ONE JPEG frame (raw request body) pushed from the browser camera and
    feed it to the virtual StreamCamera. No-op unless live detection is currently
    running on a StreamCamera, so it can never interfere with USB/RTSP sessions.
    """
    cam = camera  # snapshot the global once
    if not detection_active or not isinstance(cam, StreamCamera):
        return jsonify({"status": "ignored", "message": "stream source not active"}), 409
    data = request.get_data()
    if not data:
        return jsonify({"status": "error", "message": "empty frame"}), 400
    if not cam.push_jpeg(data):
        return jsonify({"status": "error", "message": "decode failed"}), 400
    return jsonify({"status": "ok"})


@app.route('/api/detection/status', methods=['GET'])
def get_detection_status():
    """Get current detection status and statistics."""
    return jsonify({
        "active": detection_active,
        "stats": detection_stats,
        # Live-pipeline perf numbers for the USB stats badge (additive field —
        # older clients simply ignore it). Empty dict until the loops warm up.
        "perf": _perf_snapshot(),
        # backend ที่ใช้จริง — ถ้าตกไปตัวที่ช้ากว่าโดยเงียบ ๆ ผู้ใช้จะเห็นแค่
        # "ระบบช้าลง" โดยไม่รู้สาเหตุ (เกิดจริงบนสถานี 25 ส.ค.: 50 → 378 ms)
        "backend": {
            "label": getattr(detector, "backend_label", None) if detector else None,
            "downgraded": bool(getattr(detector, "backend_downgraded", False)) if detector else False,
            "note": getattr(detector, "backend_note", "") if detector else "",
        },
        "camera_initialized": camera.is_initialized if camera else False,
        "detector_loaded": detector.model is not None if detector else False,
        "database_connected": db.is_connected if db else False,
        "mode": current_mode,
        "model_file": current_model_file,
    })


# ── Snapshot inspection (viewfinder + shutter) ─────────
# Flow: open the snapshot overlay → the camera opens ONCE at the highest
# resolution it supports and a downscaled viewfinder streams so the operator can
# aim → press the shutter → the newest full-resolution frame already in the
# buffer is run through the model once → annotated still + verdict. The shutter
# never reopens the camera (the old release→reopen-at-5MP step was the source of
# the "ถ่ายไม่สำเร็จ" failures on UVC cameras). Snapshot mode is mutually
# exclusive with live detection (single camera handle) and reuses the pure
# detect()/draw_detections() helpers — the live streaming threads are untouched.

# Classes that are NOT a dent defect (a "good"/"can" box is never an NG reason).
_NON_DEFECT_CLASSES = {"good", "can"}


def _parse_camera_index(camera_index_raw):
    """Normalise a request camera_index: numeric → int, else keep (RTSP URL)."""
    if isinstance(camera_index_raw, str) and camera_index_raw.isdigit():
        return int(camera_index_raw)
    if isinstance(camera_index_raw, (int, float)):
        return int(camera_index_raw)
    return camera_index_raw


def _scale_for_display(frame, detections, max_w):
    """
    Downscale a frame to ``max_w`` and scale detection boxes to match, so a
    high-res capture renders with readable box thickness and a small payload.
    Returns (display_frame, display_detections). No-op when already small.
    """
    h, w = frame.shape[:2]
    if w <= max_w:
        return frame, detections
    scale = max_w / float(w)
    # INTER_AREA is the correct (anti-aliasing) filter for shrinking — it avoids
    # the moiré/ripple artifacts that INTER_LINEAR produces on fine detail like
    # a can lid's concentric ridges.
    disp = cv2.resize(frame, (max_w, int(h * scale)), interpolation=cv2.INTER_AREA)
    scaled = []
    for d in detections:
        x1, y1, x2, y2 = d["bbox"]
        sd = dict(d)
        sd["bbox"] = [int(x1 * scale), int(y1 * scale),
                      int(x2 * scale), int(y2 * scale)]
        cx, cy = d["center"]
        sd["center"] = [int(cx * scale), int(cy * scale)]
        scaled.append(sd)
    return disp, scaled


def _open_camera(cam_index, width, height, fps, retries=3, settle=0.4):
    """Open a USB/RTSP camera at a given mode, retrying — a USB camera that was
    just released often needs a moment before it can be reopened."""
    for attempt in range(retries):
        cam = Camera(camera_index=cam_index, width=width, height=height, fps=fps)
        if cam.initialize():
            return cam
        logger.warning(f"Camera open attempt {attempt + 1}/{retries} failed "
                       f"({width}x{height}); retrying in {settle}s")
        time.sleep(settle)
    return None


def _ladder_for_quality(quality):
    """
    Build the resolution ladder for a chosen quality preset: the preset itself
    first, then every standard fallback rung with fewer pixels (so a camera that
    can't deliver the preset still opens at something lower), always ending with
    a 720p safety net. Unknown quality → the default preset.
    """
    presets = config.SNAPSHOT_QUALITY_PRESETS
    chosen = presets.get(quality, presets[config.SNAPSHOT_QUALITY_DEFAULT])
    rungs = [chosen]
    for rung in config.SNAPSHOT_RESOLUTION_LADDER:
        if rung[0] * rung[1] < chosen[0] * chosen[1] and rung not in rungs:
            rungs.append(rung)
    safety = (1280, 720, 30)
    if safety not in rungs:
        rungs.append(safety)
    return rungs


def _open_camera_ladder(cam_index, ladder=None):
    """
    Open the camera at the highest resolution it actually supports, walking the
    given ladder (defaults to config.SNAPSHOT_RESOLUTION_LADDER) and returning
    the first mode that opens AND delivers a frame. This single handle is then
    used for the whole snapshot session — both aiming and the shutter grab — so
    there is no fragile mid-session reopen. Returns (camera, (width, height)) or
    (None, None).
    """
    if ladder is None:
        ladder = config.SNAPSHOT_RESOLUTION_LADDER
    for width, height, fps in ladder:
        cam = _open_camera(cam_index, width, height, fps, retries=2)
        if cam is None:
            logger.warning(f"Viewfinder: could not open camera at {width}x{height}; "
                           "trying next mode")
            continue
        # Confirm it actually streams a frame at this mode before committing.
        result = cam.read_frame()
        if result and result[0] and result[1] is not None:
            ah, aw = result[1].shape[:2]
            logger.info(f"Snapshot camera opened at {aw}x{ah} (requested {width}x{height})")
            return cam, (aw, ah)
        logger.warning(f"Viewfinder: opened {width}x{height} but got no frame; "
                       "trying next mode")
        cam.release()
        time.sleep(0.2)
    return None, None


def _grab_latest_frame(timeout=2.0):
    """
    Return a copy of the newest viewfinder frame (full resolution), waiting up to
    ``timeout`` seconds for the read loop to publish a FRESH one. "Fresh" means
    published within config.SNAPSHOT_MAX_FRAME_AGE_S — a frozen/unplugged camera
    leaves the last good frame in the buffer, and a QC system must never grade a
    stale image, so a stale buffer is treated the same as no frame. The shutter
    never touches the camera handle. Returns a BGR frame, or None on timeout.
    """
    max_age = config.SNAPSHOT_MAX_FRAME_AGE_S
    deadline = time.time() + timeout
    while time.time() < deadline:
        with vf_lock:
            fresh = (viewfinder_frame is not None
                     and (time.monotonic() - viewfinder_frame_ts) <= max_age)
            frame = viewfinder_frame.copy() if fresh else None
        if frame is not None:
            return frame
        time.sleep(0.05)
    return None


@app.route('/api/viewfinder/start', methods=['POST'])
@_serialized
def api_viewfinder_start():
    """Open the camera and start the raw viewfinder for snapshot aiming."""
    global viewfinder_active, viewfinder_camera, viewfinder_thread
    global viewfinder_frame, viewfinder_seq, viewfinder_capture_size
    global viewfinder_jpeg, viewfinder_frame_ts

    if detection_active:
        return jsonify({
            "status": "error",
            "message": "กรุณากด Stop Detection ก่อน แล้วจึงเปิดโหมดถ่ายรูป"
        }), 409
    if viewfinder_active:
        return jsonify({"status": "already_running"}), 200

    data = request.get_json(silent=True) or {}
    camera_index = _parse_camera_index(data.get("camera_index", config.CAMERA_INDEX))
    quality = data.get("quality", config.SNAPSHOT_QUALITY_DEFAULT)

    # Open ONCE at the chosen quality (ladder fallback to lower modes). The same
    # handle serves both aiming (downscaled) and the shutter grab, so the camera
    # is never reopened mid-session. Lower quality = higher fps = smoother aim.
    cam, size = _open_camera_ladder(camera_index, _ladder_for_quality(quality))
    if cam is None:
        return jsonify({
            "status": "error",
            "message": f"เปิดกล้อง {camera_index} ไม่ได้ หรือกล้องถูกใช้งานอยู่"
        }), 500

    viewfinder_camera = cam
    viewfinder_capture_size = size
    with vf_lock:
        viewfinder_frame = None
        viewfinder_jpeg = None
        viewfinder_frame_ts = 0.0
        viewfinder_seq = 0
    viewfinder_active = True
    viewfinder_thread = threading.Thread(target=viewfinder_loop, daemon=True)
    viewfinder_thread.start()

    logger.info(f"Viewfinder started on camera {camera_index} at {size[0]}x{size[1]}")
    return jsonify({"status": "started", "capture_size": f"{size[0]}x{size[1]}"})


@app.route('/api/viewfinder/stop', methods=['POST'])
@_serialized
def api_viewfinder_stop():
    """Stop the viewfinder and release the camera."""
    global viewfinder_active, viewfinder_camera, viewfinder_thread, viewfinder_frame
    global viewfinder_capture_size, viewfinder_jpeg, viewfinder_frame_ts

    viewfinder_active = False
    if viewfinder_thread is not None:
        viewfinder_thread.join(timeout=1.0)
    viewfinder_thread = None
    if viewfinder_camera:
        viewfinder_camera.release()
        viewfinder_camera = None
    viewfinder_capture_size = None
    with vf_lock:
        viewfinder_frame = None
        viewfinder_jpeg = None
        viewfinder_frame_ts = 0.0

    logger.info("Viewfinder stopped")
    return jsonify({"status": "stopped"})


def _snapshot_imgsz(raw) -> int:
    """Resolve the snapshot inference imgsz from an optional client override.
    Clamped to [480, SNAPSHOT_IMGSZ]: 480 is the repo's hard floor (dents are
    small features — below 480 the model simply stops seeing them), and there
    is no accuracy reason to go above the default. Bad/missing input → default."""
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return config.SNAPSHOT_IMGSZ
    return max(480, min(int(config.SNAPSHOT_IMGSZ), v))


@app.route('/api/snapshot', methods=['POST'])
def api_snapshot():
    """Run the model on the newest viewfinder frame; return annotated JPEG + verdict."""
    if detector is None or detector.model is None:
        return jsonify({"status": "error", "message": "ยังไม่ได้โหลดโมเดล"}), 400

    if not viewfinder_active:
        return jsonify({
            "status": "error",
            "message": "กรุณาเปิดโหมดถ่ายรูป (viewfinder) ก่อนกดถ่าย"
        }), 409

    # Grab the newest full-resolution frame already captured by the viewfinder
    # loop. The shutter never touches the camera handle, so it cannot fail on a
    # camera reopen — it only waits briefly for the first frame after start.
    try:
        frame = _grab_latest_frame(timeout=2.0)
        if frame is None:
            return jsonify({
                "status": "error",
                "message": "ถ่ายภาพไม่สำเร็จ — ภาพจากกล้องไม่อัปเดต (กล้องค้าง/หลุด หรือยังไม่ขึ้นภาพ) "
                           "ปฏิเสธการตัดสินเพื่อกันผลตรวจจากภาพเก่า ลองใหม่อีกครั้ง"
            }), 500

        # Snapshot runs once per shutter press, so default to the high-accuracy
        # image size; the client may request a smaller imgsz (min 480) to test
        # the speed/accuracy trade-off on the same shot. Detection runs on the
        # full-resolution frame; we then downscale to a display size and scale
        # the boxes with it, so the preview has readable box thickness/text.
        imgsz = _snapshot_imgsz((request.get_json(silent=True) or {}).get("imgsz"))
        t0 = time.perf_counter()
        detections = detector.detect(frame, imgsz=imgsz)
        infer_ms = round((time.perf_counter() - t0) * 1000.0, 1)

        dents = [d for d in detections if d["class_name"] not in _NON_DEFECT_CLASSES]
        verdict = "ng" if dents else "ok"
        max_conf = max((d["confidence"] for d in dents), default=0.0)

        disp_frame, disp_dets = _scale_for_display(frame, detections, _SNAPSHOT_DISPLAY_MAX_W)
        annotated = detector.draw_detections(disp_frame, disp_dets)

        ret, buffer = cv2.imencode('.jpg', annotated, _JPEG_PARAMS)
        if not ret:
            return jsonify({"status": "error", "message": "เข้ารหัสภาพไม่สำเร็จ"}), 500
        image_b64 = "data:image/jpeg;base64," + base64.b64encode(buffer.tobytes()).decode("ascii")

        cap_h, cap_w = frame.shape[:2]
        return jsonify({
            "status": "ok",
            "image": image_b64,
            "verdict": verdict,
            "dent_count": len(dents),
            "max_confidence": round(max_conf, 2),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "line": config.LINE_NUMBER,
            "plant": config.PLANT_CODE,
            "capture_size": f"{cap_w}x{cap_h}",
            "infer_ms": infer_ms,
            "infer_imgsz": imgsz,
        })
    except Exception as e:
        logger.error(f"Snapshot failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"ถ่ายรูปไม่สำเร็จ: {e}"}), 500


@app.route('/api/stream/snapshot', methods=['POST'])
def api_stream_snapshot():
    """
    Run the model on a single high-res JPEG captured by the BROWSER camera (the
    STREAM source) and return the same JSON shape as /api/snapshot. The image
    arrives in the raw request body instead of from a server-side viewfinder, so
    the original /api/snapshot + viewfinder path stays completely untouched.
    """
    if detector is None or detector.model is None:
        return jsonify({"status": "error", "message": "ยังไม่ได้โหลดโมเดล"}), 400

    data = request.get_data()
    if not data:
        return jsonify({"status": "error", "message": "ไม่พบภาพที่ส่งมา"}), 400

    try:
        arr = np.frombuffer(data, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return jsonify({"status": "error", "message": "ถอดรหัสภาพไม่สำเร็จ"}), 400

        # Same detection path as /api/snapshot: detect on the full frame, then
        # downscale for a lightweight annotated preview. imgsz override arrives
        # as a query param because the request body carries the JPEG itself.
        imgsz = _snapshot_imgsz(request.args.get("imgsz"))
        t0 = time.perf_counter()
        detections = detector.detect(frame, imgsz=imgsz)
        infer_ms = round((time.perf_counter() - t0) * 1000.0, 1)

        dents = [d for d in detections if d["class_name"] not in _NON_DEFECT_CLASSES]
        verdict = "ng" if dents else "ok"
        max_conf = max((d["confidence"] for d in dents), default=0.0)

        disp_frame, disp_dets = _scale_for_display(frame, detections, _SNAPSHOT_DISPLAY_MAX_W)
        annotated = detector.draw_detections(disp_frame, disp_dets)

        ret, buffer = cv2.imencode('.jpg', annotated, _JPEG_PARAMS)
        if not ret:
            return jsonify({"status": "error", "message": "เข้ารหัสภาพไม่สำเร็จ"}), 500
        image_b64 = "data:image/jpeg;base64," + base64.b64encode(buffer.tobytes()).decode("ascii")

        cap_h, cap_w = frame.shape[:2]
        return jsonify({
            "status": "ok",
            "image": image_b64,
            "verdict": verdict,
            "dent_count": len(dents),
            "max_confidence": round(max_conf, 2),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "line": config.LINE_NUMBER,
            "plant": config.PLANT_CODE,
            "capture_size": f"{cap_w}x{cap_h}",
            "infer_ms": infer_ms,
            "infer_imgsz": imgsz,
        })
    except Exception as e:
        logger.error(f"Stream snapshot failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"ถ่ายรูปไม่สำเร็จ: {e}"}), 500


# ── Per-client live inference for the STREAM source ────────────────────
# Each browser posts its OWN frames here and gets ITS OWN detections back in the
# HTTP response — so clients are isolated by construction (no shared camera, no
# shared /video_feed, no global capture/inference threads). This is the
# "per-stream isolation + worker-pool + process-latest" pattern, scaled down:
# the browser throttles + keeps a single request in flight (process-latest), and
# the CPU-bound model call is offloaded to a real worker thread when running
# under gevent so it never blocks the cooperative hub (worker-pool). A lock keeps
# the single shared model instance from being entered concurrently.
_stream_infer_lock = threading.Lock()
try:
    from gevent import monkey as _gmonkey
    _GEVENT_ACTIVE = _gmonkey.is_module_patched("socket")
except Exception:
    _GEVENT_ACTIVE = False


def _stream_detect(frame, imgsz=None):
    """Run detection on a frame under a lock (the model is a single instance).
    Live STREAM uses a smaller imgsz for speed; the precise verdict comes from
    the high-res snapshot path instead."""
    with _stream_infer_lock:
        return detector.detect(frame, imgsz=imgsz)


@app.route('/api/stream/infer', methods=['POST'])
def api_stream_infer():
    """
    Receive ONE JPEG frame (raw body) from a browser camera, detect, and return
    the detections as JSON for the client to draw over its own <video>. Fully
    isolated per request — never touches the USB/RTSP global pipeline.
    """
    if detector is None or detector.model is None:
        return jsonify({"status": "error", "message": "ยังไม่ได้โหลดโมเดล"}), 400

    data = request.get_data()
    if not data:
        return jsonify({"status": "error", "message": "empty frame"}), 400

    try:
        # ── Server-side timing (diagnostics) ───────────────────────────────
        # Measure each stage so we can see where a frame spends its time —
        # decode vs inference — without changing any detection behaviour.
        _t0 = time.perf_counter()
        arr = np.frombuffer(data, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return jsonify({"status": "error", "message": "decode failed"}), 400
        _t_decoded = time.perf_counter()

        # Live STREAM favours speed (responsive boxes) over the highest accuracy.
        imgsz = getattr(config, "STREAM_INFER_IMGSZ", None)

        # Offload to a worker thread under gevent (keeps the hub responsive for
        # other clients); call inline under the plain dev server.
        if _GEVENT_ACTIVE:
            import gevent
            detections = gevent.get_hub().threadpool.apply(_stream_detect, (frame, imgsz))
        else:
            detections = _stream_detect(frame, imgsz)
        _t_infer = time.perf_counter()

        palette, names = {}, {}
        try:
            palette = detector._colors() or {}
            names = detector._class_names() or {}
        except Exception:
            pass

        out = []
        for d in detections:
            cn = d["class_name"]
            bgr = palette.get(cn, (0, 0, 220))
            out.append({
                "bbox": [int(v) for v in d["bbox"]],
                "class_name": cn,
                "confidence": round(float(d["confidence"]), 2),
                "label": names.get(cn, cn),
                "color": [int(bgr[2]), int(bgr[1]), int(bgr[0])],  # RGB for canvas
                "is_defect": cn not in _NON_DEFECT_CLASSES,
            })

        dents = [d for d in out if d["is_defect"]]
        h, w = frame.shape[:2]
        return jsonify({
            "status": "ok",
            "w": w, "h": h,
            "verdict": "ng" if dents else "ok",
            "dent_count": len(dents),
            "max_confidence": round(max((d["confidence"] for d in dents), default=0.0), 2),
            "detections": out,
            # Stage timings (ms) for client-side diagnostics. Pure measurement —
            # does not affect detection. The client subtracts `total` from the
            # round-trip to estimate network time.
            "srv_ms": {
                "decode": round((_t_decoded - _t0) * 1000, 1),
                "infer": round((_t_infer - _t_decoded) * 1000, 1),
                "total": round((time.perf_counter() - _t0) * 1000, 1),
                "imgsz": imgsz,
                "bytes": len(data),
            },
        })
    except Exception as e:
        logger.error(f"Stream infer failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"infer failed: {e}"}), 500


@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Get defect detection statistics from database."""
    if db and db.is_connected:
        stats = db.get_statistics()
        time_series = db.get_time_series_data(hours=24)
        return jsonify({"statistics": stats, "time_series": time_series})
    else:
        return jsonify({
            "statistics": {"total_defects": 0, "total_bottles": 0, "defects_by_type": {}, "recent_defects": 0},
            "time_series": []
        })


@app.route('/api/defects', methods=['GET'])
def get_defects():
    """Get defect history."""
    limit = request.args.get('limit', 100, type=int)
    skip = request.args.get('skip', 0, type=int)
    defect_type = request.args.get('type', None)

    if db and db.is_connected:
        defects = db.get_all_defects(limit=limit, skip=skip, defect_type=defect_type)
        return jsonify({"defects": defects})
    else:
        return jsonify({"defects": []})


# ── Mode / Model selection ─────────────────────────────

@app.route('/api/modes', methods=['GET'])
def api_list_modes():
    """List all inspection modes available (Can Dent, Label, ...)."""
    return jsonify({
        "modes": mode_registry.list_modes(),
        "current": current_mode,
    })


@app.route('/api/models', methods=['GET'])
def api_list_models():
    """List ``*.pt`` files for a mode (defaults to current mode)."""
    mode = request.args.get("mode", current_mode)
    try:
        files = mode_registry.discover_models(mode)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({
        "mode": mode,
        "models": files,
        "current": current_model_file if mode == current_mode else None,
    })


@app.route('/api/mode/switch', methods=['POST'])
@_serialized
def api_switch_mode():
    """Swap active mode and/or model file. Refuses while detection running."""
    global detector, current_mode, current_model_file, detection_stats

    if detection_active:
        return jsonify({
            "status": "error",
            "message": "Stop detection before switching mode."
        }), 400

    data = request.get_json(silent=True) or {}
    new_mode = data.get("mode", current_mode)
    new_file = data.get("model_file")  # may be None → auto-pick

    if new_mode not in mode_registry.AVAILABLE_MODES:
        return jsonify({
            "status": "error",
            "message": f"Unknown mode '{new_mode}'."
        }), 400

    logger.info(f"Switching mode → '{new_mode}' (file={new_file})")
    detector = _load_detector_for(new_mode, new_file)
    current_mode = new_mode
    current_model_file = new_file
    detection_stats["total_detected"] = 0
    detection_stats["current_defects"] = 0

    return jsonify({
        "status": "ok",
        "mode": current_mode,
        "model_file": current_model_file,
        "model_loaded": detector.model is not None,
    })


# ── Label Paper Inspection (PDF master + manual crop) ──
# These routes are completely independent from the YOLO detection pipeline
# above (no camera, no detection thread, no shared state).

@app.route('/label_paper')
def label_paper_page():
    """Label Paper inspection page — upload, 4-point crop, inspect."""
    return render_template('label_paper.html')


@app.route('/api/label_paper/skus', methods=['GET'])
def api_label_paper_skus():
    """List SKUs found under ``data/label_paper/skus/``."""
    skus = master_loader.list_skus(label_paper_cfg.SKUS_DIR)
    return jsonify({"skus": skus})


@app.route('/api/label_paper/inspect', methods=['POST'])
def api_label_paper_inspect():
    """
    Inspect a label photo against its SKU master.

    multipart/form-data:
        sku_code: str   — must match a directory under SKUS_DIR
        image:    file  — label photo (EXIF orientation baked by the browser)
        corners:  str   — optional JSON [[x,y],[x,y],[x,y],[x,y]] marking the
                          label quad (TL, TR, BR, BL of the upright label) in
                          the uploaded image's pixel space.  When present the
                          server perspective-warps the quad; when absent the
                          image is treated as already cropped (legacy client).
    """
    sku_code = (request.form.get("sku_code") or "").strip()
    upload = request.files.get("image")
    if not sku_code or upload is None:
        return jsonify({"error": "sku_code and image are required"}), 400

    sku_dir = os.path.join(label_paper_cfg.SKUS_DIR, sku_code)
    if not os.path.isdir(sku_dir):
        return jsonify({"error": f"SKU '{sku_code}' not found"}), 404

    try:
        master = master_loader.load_master(sku_dir)
    except Exception as e:
        logger.error(f"[label_paper] failed to load master for {sku_code}: {e}")
        return jsonify({"error": f"failed to load master: {e}"}), 500

    image_bytes = upload.read()
    if not image_bytes:
        return jsonify({"error": "empty image"}), 400

    # Perspective-warp the marked quad (when corners are sent) and produce
    # two resolutions from the same crop: high-res for OCR, bounded for ΔE.
    try:
        corners = perspective.parse_corners(request.form.get("corners"))
        ocr_bytes, pixel_bytes = perspective.prepare_inspection_images(
            image_bytes, corners)
    except ValueError as e:
        logger.warning(f"[label_paper] bad crop for {sku_code}: {e}")
        return jsonify({"error": f"invalid crop: {e}"}), 400

    try:
        report = label_pipeline.inspect(master, pixel_bytes,
                                        ocr_image_bytes=ocr_bytes)
    except Exception as e:
        logger.error(f"[label_paper] inspection failed for {sku_code}: {e}")
        return jsonify({"error": f"inspection failed: {e}"}), 500

    report_dict = report.to_dict()

    # Persist to the QC audit trail (best-effort — never blocks the response).
    rec_id = label_history.save_inspection(
        label_paper_cfg.INSPECTIONS_DIR, sku_code, ocr_bytes, report_dict)
    report_dict["record_id"] = rec_id

    return jsonify(report_dict)


@app.route('/api/label_paper/history', methods=['GET'])
def api_label_paper_history():
    """List recent label inspections (newest first)."""
    limit = request.args.get("limit", 100, type=int)
    records = label_history.list_inspections(label_paper_cfg.INSPECTIONS_DIR, limit=limit)
    return jsonify({"records": records})


@app.route('/api/label_paper/history/<rec_id>', methods=['GET'])
def api_label_paper_history_detail(rec_id):
    """Return the full stored report for one inspection record."""
    report = label_history.load_report(label_paper_cfg.INSPECTIONS_DIR, rec_id)
    if report is None:
        return jsonify({"error": "record not found"}), 404
    return jsonify(report)


@app.route('/api/label_paper/history/<rec_id>/crop', methods=['GET'])
def api_label_paper_history_crop(rec_id):
    """Serve the stored crop image for one inspection record."""
    from flask import send_file
    path = label_history.crop_path(label_paper_cfg.INSPECTIONS_DIR, rec_id)
    if path is None:
        return jsonify({"error": "crop not found"}), 404
    return send_file(path, mimetype="image/jpeg")


@app.route('/api/label_paper/master/refresh', methods=['POST'])
def api_label_paper_master_refresh():
    """
    Invalidate a SKU's cached master OCR so the next inspection re-OCRs it.
    Needed after approving a new artwork revision (new master.pdf).
    """
    data = request.get_json(silent=True) or {}
    sku_code = (data.get("sku_code") or "").strip()
    sku_dir = os.path.join(label_paper_cfg.SKUS_DIR, sku_code)
    pdf_path = os.path.join(sku_dir, "master.pdf")
    if not sku_code or not os.path.isfile(pdf_path):
        return jsonify({"error": f"SKU '{sku_code}' has no master.pdf"}), 404
    removed = master_ocr.invalidate_cache(pdf_path)
    return jsonify({"status": "ok", "sku_code": sku_code, "cache_removed": removed})


@app.route('/label_paper/history')
def label_paper_history_page():
    """Label Paper inspection history page."""
    return render_template('label_paper_history.html')


@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal error: {error}")
    return jsonify({"error": "Internal server error"}), 500


def cleanup():
    """Cleanup resources on shutdown."""
    global detection_active, camera, db

    logger.info("Shutting down...")
    detection_active = False

    if camera:
        camera.release()
    if db:
        db.disconnect()


if __name__ == '__main__':
    WSGIRequestHandler.protocol_version = "HTTP/1.1"

    try:
        init_system()
    except Exception as e:
        logger.error(f"Error during system initialization: {e}")
        logger.warning("Flask server will start but some features may be unavailable.")

    print("=" * 64)
    print(f"  CONFIG_VERSION      : {config.CONFIG_VERSION}")
    print(f"  OCR_BACKEND         : {config.OCR_BACKEND or '(auto)'}")
    print(f"  N8N_OCR_WEBHOOK_URL : {config.N8N_OCR_WEBHOOK_URL}")
    print("=" * 64)

    # HTTPS is opt-in (config.USE_HTTPS). It is required for the browser STREAM
    # source — getUserMedia only works over a secure context (HTTPS or localhost).
    # When off (default) the server starts over plain HTTP exactly as before.
    ssl_context = None
    scheme = "http"
    if getattr(config, "USE_HTTPS", False):
        if os.path.exists(config.SSL_CERT_FILE) and os.path.exists(config.SSL_KEY_FILE):
            ssl_context = (config.SSL_CERT_FILE, config.SSL_KEY_FILE)
            scheme = "https"
        else:
            logger.warning(
                "USE_HTTPS=True but cert/key not found "
                f"({config.SSL_CERT_FILE}, {config.SSL_KEY_FILE}). "
                "Run `python generate_cert.py` first. Falling back to HTTP."
            )

    try:
        logger.info(f"Starting Flask server at {scheme}://{config.FLASK_HOST}:{config.FLASK_PORT}")
        app.run(
            host=config.FLASK_HOST,
            port=config.FLASK_PORT,
            debug=config.FLASK_DEBUG,
            threaded=True,
            ssl_context=ssl_context
        )
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        cleanup()
