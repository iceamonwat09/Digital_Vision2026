"""
Flask application for YOLO-based defect detection system.
Main application file with routes, video streaming, and API endpoints.
"""

import os
import threading
import time
import base64
from datetime import datetime
import cv2
import numpy as np
from flask import Flask, render_template, Response, jsonify, request
from werkzeug.serving import WSGIRequestHandler

import config
from logger import setup_logger
from camera import Camera, scan_cameras_fast
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

# Artwork Proof Check (ตรวจสะกดคำ/ตัวเลขใน artwork ก่อนพิมพ์).
# Fully isolated blueprint — a failure here only disables that one mode
# and can never break Can Dent / Label / Label Paper.
try:
    from artwork_check.routes import artwork_bp
    app.register_blueprint(artwork_bp)
    logger.info("Artwork Proof Check mode registered (/artwork_check)")
except Exception as _aw_err:  # noqa: BLE001 — isolation by design
    logger.warning(f"Artwork Proof Check disabled: {_aw_err}")

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
# A lightweight raw-camera preview used by snapshot mode so the operator can
# aim before pressing the shutter. It runs NO detection (just grabs and shows
# raw frames), so it stays smooth and never competes with the model. It owns
# the camera only while the snapshot overlay is open, and is mutually exclusive
# with live detection (single camera handle).
viewfinder_active = False
viewfinder_camera = None
viewfinder_thread = None
viewfinder_frame = None
viewfinder_seq = 0
vf_lock = threading.Lock()


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

    logger.info("Inference loop started")
    last_seq = -1

    while detection_active:
        try:
            with raw_lock:
                frame = latest_raw_frame
                seq = raw_frame_seq

            if frame is None or seq == last_seq:
                time.sleep(0.005)   # nothing new yet — yield briefly
                continue
            last_seq = seq

            if detector is not None and detector.model is not None:
                detections = detector.detect(frame)
            else:
                detections = []

            # Publish the detections together with the frame they ran on so the
            # generator can draw them in sync (see latest_det_frame comment).
            with det_lock:
                latest_detections = detections
                latest_det_frame = frame
                latest_det_seq = seq

            detection_stats["current_defects"] = len(detections)
            if detections:
                detection_stats["total_detected"] += len(detections)

            # Log defects to database (with cooldown). For bestX.pt only a
            # "dent" is a defect; "can" (good) is never logged.
            bestx_mode = detector.is_bestx_mode if detector else False
            current_time = time.time()
            for det in detections:
                if bestx_mode and det["class_name"] != "dent":
                    continue
                defect_type = det["class_name"]
                last_log_time = defect_log_cooldown.get(defect_type, 0)
                if current_time - last_log_time >= config.DEFECT_LOGGING_COOLDOWN:
                    if db and db.is_connected:
                        db.log_defect(
                            defect_type=defect_type,
                            confidence=det["confidence"],
                            frame=frame,
                            bbox=det["bbox"],
                            timestamp=datetime.now()
                        )
                        defect_log_cooldown[defect_type] = current_time

        except Exception as e:
            logger.error(f"Error in inference loop: {e}")
            time.sleep(0.1)

    logger.info("Inference loop stopped")


def generate_frames():
    """
    MJPEG generator. Draws each detection on the exact frame the model ran it
    on (published together by inference_loop), so the box stays locked to the
    can instead of trailing behind a newer frame. The displayed feed therefore
    refreshes at the inference rate; re-encodes JPEG only when a new inference
    frame has arrived, so a stalled or idle feed costs nothing.
    """
    # Placeholder frame (created once)
    placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(placeholder, "Waiting for camera...", (150, 240),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    _, placeholder_buf = cv2.imencode('.jpg', placeholder, _JPEG_PARAMS)
    placeholder_bytes = placeholder_buf.tobytes()

    last_encoded_seq = -1
    frame_bytes = placeholder_bytes

    while True:
        with det_lock:
            frame = latest_det_frame
            detections = latest_detections
            seq = latest_det_seq

        # Only redo work when a fresh inference frame (frame + its detections)
        # is available.
        if frame is not None and seq != last_encoded_seq:
            last_encoded_seq = seq

            if detector is not None and detector.model is not None:
                annotated = detector.draw_detections(frame, detections)
            else:
                annotated = frame.copy()
                cv2.putText(annotated, "Camera Preview (No Model)",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                            (0, 165, 255), 2)

            ret, buffer = cv2.imencode('.jpg', annotated, _JPEG_PARAMS)
            if ret:
                frame_bytes = buffer.tobytes()

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

        time.sleep(1.0 / config.STREAM_FPS)


def viewfinder_loop():
    """Viewfinder capture thread: grab raw frames (no detection) at camera rate."""
    global viewfinder_frame, viewfinder_seq

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
            with vf_lock:
                viewfinder_frame = frame
                viewfinder_seq += 1
        except Exception as e:
            logger.error(f"Error in viewfinder loop: {e}")
            time.sleep(0.05)
    logger.info("Viewfinder loop stopped")


def generate_viewfinder():
    """MJPEG generator for the raw viewfinder (no boxes, no model)."""
    placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(placeholder, "Starting camera...", (170, 240),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    _, ph_buf = cv2.imencode('.jpg', placeholder, _JPEG_PARAMS)
    frame_bytes = ph_buf.tobytes()
    last_seq = -1

    while viewfinder_active:
        with vf_lock:
            frame = viewfinder_frame
            seq = viewfinder_seq
        if frame is not None and seq != last_seq:
            last_seq = seq
            # Downscale the high-res (5MP) frame for a smooth aiming stream —
            # the full-res frame is kept untouched for the shutter capture.
            disp = frame
            h, w = frame.shape[:2]
            if w > _VIEWFINDER_MAX_W:
                scale = _VIEWFINDER_MAX_W / float(w)
                disp = cv2.resize(frame, (_VIEWFINDER_MAX_W, int(h * scale)),
                                  interpolation=cv2.INTER_AREA)
            ret, buffer = cv2.imencode('.jpg', disp, _JPEG_PARAMS)
            if ret:
                frame_bytes = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        time.sleep(1.0 / config.STREAM_FPS)


# ── Routes ─────────────────────────────────────────────

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


@app.route('/api/detection/start', methods=['POST'])
def start_detection():
    """Start defect detection with the selected camera."""
    global detection_active, capture_thread, inference_thread, camera
    global latest_raw_frame, raw_frame_seq, latest_detections
    global latest_det_frame, latest_det_seq

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

    # Initialize camera on demand
    camera = Camera(camera_index=camera_index)
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

    detection_active = True
    capture_thread = threading.Thread(target=capture_loop, daemon=True)
    inference_thread = threading.Thread(target=inference_loop, daemon=True)
    capture_thread.start()
    inference_thread.start()

    logger.info(f"Detection started on camera {camera_index}")
    return jsonify({"status": "started", "message": f"Detection started on camera {camera_index}"})


@app.route('/api/detection/stop', methods=['POST'])
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


@app.route('/api/detection/status', methods=['GET'])
def get_detection_status():
    """Get current detection status and statistics."""
    return jsonify({
        "active": detection_active,
        "stats": detection_stats,
        "camera_initialized": camera.is_initialized if camera else False,
        "detector_loaded": detector.model is not None if detector else False,
        "database_connected": db.is_connected if db else False,
        "mode": current_mode,
        "model_file": current_model_file,
    })


# ── Snapshot inspection (viewfinder + shutter) ─────────
# Flow: open the snapshot overlay → a raw viewfinder streams so the operator
# can aim (no detection, stays smooth) → press the shutter → the newest
# viewfinder frame is run through the model once → annotated still + verdict.
# Snapshot mode is mutually exclusive with live detection (single camera handle)
# and reuses the pure detect()/draw_detections() helpers — the live streaming
# threads are never touched.

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


@app.route('/api/viewfinder/start', methods=['POST'])
def api_viewfinder_start():
    """Open the camera and start the raw viewfinder for snapshot aiming."""
    global viewfinder_active, viewfinder_camera, viewfinder_thread
    global viewfinder_frame, viewfinder_seq

    if detection_active:
        return jsonify({
            "status": "error",
            "message": "กรุณากด Stop Detection ก่อน แล้วจึงเปิดโหมดถ่ายรูป"
        }), 409
    if viewfinder_active:
        return jsonify({"status": "already_running"}), 200

    data = request.get_json(silent=True) or {}
    camera_index = _parse_camera_index(data.get("camera_index", config.CAMERA_INDEX))

    # Open at the high snapshot resolution so the shutter captures full detail.
    # The viewfinder stream itself is downscaled for smooth aiming.
    cam = Camera(
        camera_index=camera_index,
        width=config.SNAPSHOT_CAMERA_WIDTH,
        height=config.SNAPSHOT_CAMERA_HEIGHT,
        fps=config.SNAPSHOT_CAMERA_FPS,
    )
    if not cam.initialize():
        return jsonify({
            "status": "error",
            "message": f"เปิดกล้อง {camera_index} ไม่ได้ หรือกล้องถูกใช้งานอยู่"
        }), 500

    viewfinder_camera = cam
    with vf_lock:
        viewfinder_frame = None
        viewfinder_seq = 0
    viewfinder_active = True
    viewfinder_thread = threading.Thread(target=viewfinder_loop, daemon=True)
    viewfinder_thread.start()

    logger.info(f"Viewfinder started on camera {camera_index}")
    return jsonify({"status": "started"})


@app.route('/api/viewfinder/stop', methods=['POST'])
def api_viewfinder_stop():
    """Stop the viewfinder and release the camera."""
    global viewfinder_active, viewfinder_camera, viewfinder_thread, viewfinder_frame

    viewfinder_active = False
    if viewfinder_thread is not None:
        viewfinder_thread.join(timeout=1.0)
    viewfinder_thread = None
    if viewfinder_camera:
        viewfinder_camera.release()
        viewfinder_camera = None
    with vf_lock:
        viewfinder_frame = None

    logger.info("Viewfinder stopped")
    return jsonify({"status": "stopped"})


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

    with vf_lock:
        frame = viewfinder_frame
    frame = frame.copy() if frame is not None else None
    if frame is None:
        return jsonify({
            "status": "error",
            "message": "ยังไม่มีภาพจากกล้อง รอสักครู่แล้วลองใหม่"
        }), 500

    # Snapshot runs once per shutter press, so use the high-accuracy image size
    # (favour precision over speed — unlike the live stream). Detection runs on
    # the full-resolution frame; we then downscale to a display size and scale
    # the boxes with it, so the preview has readable box thickness/text and a
    # lightweight payload.
    detections = detector.detect(frame, imgsz=config.SNAPSHOT_IMGSZ)

    dents = [d for d in detections if d["class_name"] not in _NON_DEFECT_CLASSES]
    verdict = "ng" if dents else "ok"
    max_conf = max((d["confidence"] for d in dents), default=0.0)

    disp_frame, disp_dets = _scale_for_display(frame, detections, _SNAPSHOT_DISPLAY_MAX_W)
    annotated = detector.draw_detections(disp_frame, disp_dets)

    ret, buffer = cv2.imencode('.jpg', annotated, _JPEG_PARAMS)
    if not ret:
        return jsonify({"status": "error", "message": "เข้ารหัสภาพไม่สำเร็จ"}), 500
    image_b64 = "data:image/jpeg;base64," + base64.b64encode(buffer.tobytes()).decode("ascii")

    return jsonify({
        "status": "ok",
        "image": image_b64,
        "verdict": verdict,
        "dent_count": len(dents),
        "max_confidence": round(max_conf, 2),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "line": config.LINE_NUMBER,
        "plant": config.PLANT_CODE,
    })


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

    try:
        logger.info(f"Starting Flask server at http://{config.FLASK_HOST}:{config.FLASK_PORT}")
        app.run(
            host=config.FLASK_HOST,
            port=config.FLASK_PORT,
            debug=config.FLASK_DEBUG,
            threaded=True
        )
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        cleanup()
