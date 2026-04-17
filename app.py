"""
Flask application for YOLO-based defect detection system.
Main application file with routes, video streaming, and API endpoints.
"""

import threading
import time
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

# Setup centralized logging
logger = setup_logger(__name__)

# Initialize Flask app
app = Flask(__name__, template_folder=config.TEMPLATES_DIR, static_folder=config.STATIC_DIR)

# Pre-computed JPEG encode params (avoids re-creating each frame)
_JPEG_PARAMS = [cv2.IMWRITE_JPEG_QUALITY, 80]

# Global variables for detection system
camera = None
detector = None
db = None
detection_active = False
detection_thread = None
current_frame = None
frame_lock = threading.Lock()
detection_stats = {
    "total_detected": 0,
    "current_defects": 0,
}

# Cooldown tracking for defect logging
defect_log_cooldown = {}


def init_system():
    """Initialize detector and database only. Camera is deferred to start-detection."""
    global detector, db

    # Initialize YOLO detector
    logger.info("Initializing YOLO detector...")
    detector = YOLODetector()
    try:
        if not detector.load_model():
            logger.error("YOLO detector initialization failed!")
    except Exception as e:
        logger.error(f"YOLO detector initialization error: {e}")

    # Initialize database (non-blocking)
    logger.info("Initializing database...")
    db = Database()
    if not db.connect():
        logger.warning("Database connection failed. Defect logging will be disabled.")

    logger.info("System initialization completed!")
    return True


def detection_loop():
    """Main detection loop running in separate thread."""
    global current_frame, detection_active, detection_stats, defect_log_cooldown

    logger.info("Detection loop started")

    while detection_active:
        try:
            result = camera.read_frame()
            if result is None:
                time.sleep(0.05)
                continue

            success, frame = result
            if not success or frame is None:
                time.sleep(0.05)
                continue

            # Perform detection
            detections = detector.detect(frame)

            # Draw detections on frame
            annotated_frame = detector.draw_detections(frame, detections)

            # Update current frame (minimal lock time)
            with frame_lock:
                current_frame = annotated_frame

            # Update stats
            detection_stats["current_defects"] = len(detections)
            if detections:
                detection_stats["total_detected"] += len(detections)

            # Log defects to database (with cooldown)
            current_time = time.time()
            for det in detections:
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

            # Control frame rate
            time.sleep(1.0 / config.STREAM_FPS)

        except Exception as e:
            logger.error(f"Error in detection loop: {e}")
            time.sleep(0.1)

    logger.info("Detection loop stopped")


def generate_frames():
    """Generator function for video streaming (MJPEG)."""
    global current_frame

    # Placeholder frame (created once)
    placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(placeholder, "Waiting for camera...", (150, 240),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    _, placeholder_buf = cv2.imencode('.jpg', placeholder, _JPEG_PARAMS)
    placeholder_bytes = placeholder_buf.tobytes()

    while True:
        with frame_lock:
            frame = current_frame

        if frame is not None:
            ret, buffer = cv2.imencode('.jpg', frame, _JPEG_PARAMS)
            if ret:
                frame_bytes = buffer.tobytes()
            else:
                frame_bytes = placeholder_bytes
        else:
            frame_bytes = placeholder_bytes

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


# ── API Endpoints ──────────────────────────────────────

@app.route('/api/camera/scan', methods=['GET'])
def api_scan_cameras():
    """Scan for available cameras and return the list."""
    cameras = scan_cameras_fast(max_index=4)
    return jsonify({"cameras": cameras})


@app.route('/api/detection/start', methods=['POST'])
def start_detection():
    """Start defect detection with the selected camera."""
    global detection_active, detection_thread, camera

    if detection_active:
        return jsonify({"status": "already_running", "message": "Detection already active"}), 200

    if detector is None or detector.model is None:
        return jsonify({"status": "error", "message": "Detector not initialized"}), 500

    # Get camera index from request (default to config value)
    data = request.get_json(silent=True) or {}
    camera_index = data.get("camera_index", config.CAMERA_INDEX)

    # Initialize camera on demand
    camera = Camera(camera_index=int(camera_index))
    if not camera.initialize():
        return jsonify({"status": "error", "message": f"Failed to open camera {camera_index}"}), 500

    detection_active = True
    detection_thread = threading.Thread(target=detection_loop, daemon=True)
    detection_thread.start()

    logger.info(f"Detection started on camera {camera_index}")
    return jsonify({"status": "started", "message": f"Detection started on camera {camera_index}"})


@app.route('/api/detection/stop', methods=['POST'])
def stop_detection():
    """Stop defect detection and release camera."""
    global detection_active, camera, current_frame

    detection_active = False

    # Wait briefly for detection thread to finish, then release camera
    if camera:
        time.sleep(0.2)
        camera.release()
        camera = None

    with frame_lock:
        current_frame = None

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
        "database_connected": db.is_connected if db else False
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
