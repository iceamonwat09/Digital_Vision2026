"""
YOLO-based defect detection module.
Handles YOLOv8 model loading, inference, and defect classification.
"""

import cv2
import logging
import threading
import numpy as np
import os
import time
from typing import List, Dict, Tuple, Optional
from ultralytics import YOLO
import config
from logger import setup_logger

logger = setup_logger(__name__)

# Fallback BGR color palette (Can Dent). Per-mode colors override this when a
# ``mode_config`` is passed to YOLODetector.
_COLORS = {
    "good":         (80,  200,  0),    # เขียว
    "dented":       (0,    0,  220),   # แดง   (กรอบหนา)
    "dented_spot":  (0,  100,  255),   # ส้ม   (corner marks)
}
_COLOR_DEFAULT = (0, 165, 255)      # ส้ม fallback

# ── bestX.pt — two-class model: "dent" + "can" only ──────────────────────────
_BESTX_CLASS_NAMES = {
    "dent": "Can Dent",
    "can":  "Can Good",
}
_BESTX_COLORS = {
    "dent": (0,   0, 220),   # red
    "can":  (80, 200,   0),  # green
}


def _suppress_false_dent_spots(detections: list, good_conf_threshold: float = 0.90) -> list:
    """
    ลบ dented_spot ที่เป็น false positive ออก โดยใช้เงื่อนไข 3 ข้อพร้อมกัน:
      1. center ของ dented_spot อยู่ภายใน good box (containment)
      2. good.confidence >= good_conf_threshold (default 0.90)
      3. good.confidence > dented_spot.confidence
    """
    good_dets = [d for d in detections if d["class_name"] == "good"]
    if not good_dets:
        return detections

    def _inside(cx, cy, bbox):
        x1, y1, x2, y2 = bbox
        return x1 <= cx <= x2 and y1 <= cy <= y2

    suppressed = set()
    for i, det in enumerate(detections):
        if det["class_name"] != "dented_spot":
            continue
        cx, cy = det["center"]
        for g in good_dets:
            if (g["confidence"] >= good_conf_threshold
                    and g["confidence"] > det["confidence"]
                    and _inside(cx, cy, g["bbox"])):
                suppressed.add(i)
                logger.debug(
                    f"SUPPRESS dented_spot conf={det['confidence']:.2f} "
                    f"(inside good conf={g['confidence']:.2f})"
                )
                break

    result = [d for i, d in enumerate(detections) if i not in suppressed]
    if suppressed:
        logger.info(f"Suppressed {len(suppressed)} false dented_spot detection(s)")
    return result


def _draw_corner_marks(frame: np.ndarray, x1: int, y1: int, x2: int, y2: int,
                       color, thickness: int = 2, length: int = 20) -> None:
    """Draw targeting corner brackets instead of a full rectangle."""
    ln = min(length, (x2 - x1) // 3, (y2 - y1) // 3)
    pts = [
        ((x1, y1), (x1 + ln, y1)), ((x1, y1), (x1, y1 + ln)),   # TL
        ((x2, y1), (x2 - ln, y1)), ((x2, y1), (x2, y1 + ln)),   # TR
        ((x1, y2), (x1 + ln, y2)), ((x1, y2), (x1, y2 - ln)),   # BL
        ((x2, y2), (x2 - ln, y2)), ((x2, y2), (x2, y2 - ln)),   # BR
    ]
    for p1, p2 in pts:
        cv2.line(frame, p1, p2, color, thickness)


def _draw_bestx_verdict(frame: np.ndarray, verdict: Optional[str]) -> None:
    """Draw NG / OK verdict badge in the top-right corner (bestX.pt mode only).
    verdict=None → no can detected yet, so draw nothing."""
    if verdict is None:
        return
    text  = "NG" if verdict == "ng" else "OK"
    color = (0, 0, 220) if verdict == "ng" else (80, 200, 0)
    font  = cv2.FONT_HERSHEY_SIMPLEX
    scale, thick = 3.0, 6
    (tw, th), _ = cv2.getTextSize(text, font, scale, thick)
    h, w = frame.shape[:2]
    x, y = w - tw - 20, th + 20
    cv2.rectangle(frame, (x - 10, 10), (w - 10, y + 10), (0, 0, 0), cv2.FILLED)
    cv2.putText(frame, text, (x, y), font, scale, color, thick)


class YOLODetector:
    """
    YOLO-based defect detector for water bottles.
    Handles model loading, inference, and result processing.
    """
    # เกินเท่านี้ถือว่า "รอคิวนาน" — แค่ log ระดับ debug ไม่รบกวนการทำงาน
    _INFER_WAIT_WARN_MS = 50.0

    
    def __init__(self, model_path: str = None, mode_config=None):
        """
        Initialize YOLO detector.

        Args:
            model_path: Path to YOLO model. If None, uses config.MODEL_PATH
            mode_config: Optional mode module from ``modes/`` providing
                ``CLASS_NAMES`` (display labels) and ``COLORS`` (BGR tuples).
                When None, falls back to the legacy ``config`` globals so
                existing call sites keep working unchanged.
        """
        self.model_path = model_path if model_path is not None else config.MODEL_PATH
        self.model: Optional[YOLO] = None
        self.is_openvino = False   # True when inference runs through OpenVINO
        self.is_onnx = False       # True when inference runs through ONNX Runtime
        # Inference device string passed to every predict call (e.g. "intel:gpu").
        # None (default) = no device argument at all → backend default, exactly
        # the pre-existing behaviour for PyTorch/ONNX/OpenVINO-CPU.
        self.infer_device: Optional[str] = None
        self.confidence_threshold = config.CONFIDENCE_THRESHOLD
        self.iou_threshold = config.IOU_THRESHOLD
        self.mode_config = mode_config
        # Rolling inference-time accounting (throttled log every 30 frames)
        self._infer_count = 0
        self._infer_ms_accum = 0.0

        # ── ทุกการเรียกโมเดลต้องผ่าน lock ตัวเดียว ────────────────────────
        # `detector` เป็น object เดียวที่ถูกใช้จากหลายเธรดพร้อมกันจริง:
        # inference_loop (สด) · ปุ่มถ่ายรูปตรวจ · /api/stream/infer ·
        # งานตรวจเบื้องหลังของโหมดถ่ายรัว. ultralytics ไม่รับประกันว่า
        # เรียกซ้อนกันได้ และบน iGPU ที่แชร์ RAM กับซีพียู การยิงพร้อมกัน
        # ทำให้ทั้งสองฝั่งช้าลงกว่าการเข้าคิว (วัดได้เป็นภาพสดกระตุก).
        # RLock เพราะเป็นการป้องกันเชิงโครงสร้าง — เรียกซ้อนในเธรดเดียวกัน
        # ไม่ควรเกิด แต่ถ้าเกิดต้องไม่กลายเป็น deadlock ที่หยุดสายการผลิต.
        self._infer_lock = threading.RLock()
        # เวลารอคิวโมเดลของการเรียกครั้งล่าสุด (ms) — ให้ route รายงานแยกจาก
        # เวลาของโมเดลเอง ไม่งั้น "ตรวจช้า" กับ "รอคิวนาน" แยกกันไม่ออก
        self.last_wait_ms = 0.0

    @property
    def is_bestx_mode(self) -> bool:
        """True when the loaded model file is bestX.pt."""
        return os.path.basename(self.model_path).lower() == "bestx.pt"

    def _class_names(self) -> dict:
        """Active class-name → display-label mapping."""
        if self.is_bestx_mode:
            return _BESTX_CLASS_NAMES
        if self.mode_config is not None:
            return getattr(self.mode_config, "CLASS_NAMES", {}) or {}
        return config.DEFECT_CLASS_NAMES

    def _colors(self) -> dict:
        """Active class-name → BGR tuple mapping."""
        if self.is_bestx_mode:
            return _BESTX_COLORS
        if self.mode_config is not None:
            colors = getattr(self.mode_config, "COLORS", None)
            if colors:
                return colors
        return _COLORS
        
    @staticmethod
    def _openvino_device_available(device: str) -> bool:
        """
        True when the OpenVINO device in ``device`` (e.g. "intel:gpu" → "GPU")
        actually exists on this machine. Guards against OpenVINO's own silent
        fallback (asking for GPU on a machine without one quietly runs AUTO/CPU
        — accuracy-safe but slower than our ONNX path and very misleading when
        reading speed numbers), so we skip OpenVINO entirely instead.
        """
        try:
            import openvino as ov
            want = device.split(":", 1)[1].upper() if ":" in device else device.upper()
            avail = list(ov.Core().available_devices)
            ok = any(d == want or d.startswith(want + ".") for d in avail)
            if not ok:
                logger.warning(
                    f"OpenVINO device '{want}' not available (found: {avail}); "
                    "skipping OpenVINO backend.")
            return ok
        except Exception as e:
            logger.warning(f"OpenVINO device probe failed ({e}); skipping OpenVINO backend.")
            return False

    def _maybe_openvino(self, pt_path: str) -> Optional[str]:
        """
        Return a path to an OpenVINO model directory for ``pt_path`` (exporting it
        once if needed), or ``None`` to signal "fall back to the next backend".

        Enabled by either flag (both default off → returns None, behaviour
        unchanged): the legacy ``USE_OPENVINO`` (CPU) or the new
        ``OPENVINO_DEVICE`` (e.g. "intel:gpu" for the Iris Xe iGPU).

        Accuracy is preserved: we export FP32 with ``dynamic=True`` so the SAME
        model runs at both the live imgsz (480) and the snapshot imgsz (1280).
        Any failure (package missing, export error) is swallowed → the next
        backend is used, so this can never break the existing modes.
        """
        device = getattr(config, "OPENVINO_DEVICE", None)
        if not (getattr(config, "USE_OPENVINO", False) or device):
            return None
        if not pt_path.endswith(".pt") or not os.path.exists(pt_path):
            return None
        if device and not self._openvino_device_available(device):
            return None
        ov_dir = pt_path[:-3] + "_openvino_model"
        # The exported IR lives at <dir>/<stem>.xml — use its mtime for the
        # stale check (same quiet-correctness trap as ONNX: a retrained .pt
        # must never keep running behind a stale export).
        ov_xml = os.path.join(ov_dir, os.path.basename(pt_path)[:-3] + ".xml")
        try:
            stale = (os.path.exists(ov_xml)
                     and os.path.getmtime(pt_path) > os.path.getmtime(ov_xml))
            if not os.path.isdir(ov_dir) or not os.path.exists(ov_xml) or stale:
                if stale:
                    logger.info(f"OpenVINO export is older than .pt — re-exporting: {pt_path}")
                else:
                    logger.info(f"Exporting OpenVINO model (one-time, FP32/dynamic): {pt_path}")
                YOLO(pt_path).export(format="openvino", dynamic=True, half=False)
            if os.path.isdir(ov_dir) and os.path.exists(ov_xml):
                return ov_dir
            logger.warning("OpenVINO export produced no model dir; using next backend.")
        except Exception as e:
            logger.warning(f"OpenVINO unavailable ({e}); using next backend instead.")
        return None

    def _maybe_onnx(self, pt_path: str) -> Optional[str]:
        """
        Return a path to an ONNX model file for ``pt_path`` (exporting it once if
        needed), or ``None`` to signal "fall back to PyTorch".

        Accuracy is preserved: FP32 export with ``dynamic=True`` so the SAME model
        runs at both the live imgsz (480) and the snapshot imgsz (1280).
        ``ultralytics`` does its own decode/NMS on the ONNX output exactly as it
        does for the ``.pt`` model, so results match while CPU inference is faster.

        Any failure (package missing, export error, produced file missing) is
        swallowed → PyTorch is used, so this can never break the existing modes.
        Note: this only returns a *path*; the actual load + a smoke test happen in
        ``load_model``, which falls back to ``.pt`` if either fails.
        """
        if not getattr(config, "USE_ONNX", False):
            return None
        if not pt_path.endswith(".pt") or not os.path.exists(pt_path):
            return None
        onnx_path = pt_path[:-3] + ".onnx"
        try:
            # Re-export when the .onnx is missing OR older than the .pt — otherwise
            # retraining/replacing best.pt would silently keep running the stale
            # ONNX model (a quiet correctness trap).
            stale = (os.path.exists(onnx_path)
                     and os.path.getmtime(pt_path) > os.path.getmtime(onnx_path))
            if not os.path.exists(onnx_path) or stale:
                if stale:
                    logger.info(f"ONNX is older than .pt — re-exporting: {pt_path}")
                else:
                    logger.info(f"Exporting ONNX model (one-time, FP32/dynamic): {pt_path}")
                export_kwargs = dict(format="onnx", dynamic=True, half=False)
                opset = getattr(config, "ONNX_OPSET", None)
                if opset:
                    export_kwargs["opset"] = int(opset)
                # ultralytics defaults simplify=True (needs onnxslim). Try it for the
                # leaner graph, but fall back to simplify=False so a missing/flaky
                # onnxslim never costs us the ONNX export entirely.
                try:
                    YOLO(pt_path).export(simplify=True, **export_kwargs)
                except Exception as e_simpl:
                    logger.warning(f"ONNX export with simplify failed ({e_simpl}); "
                                   "retrying without simplify.")
                    YOLO(pt_path).export(simplify=False, **export_kwargs)
            if os.path.exists(onnx_path):
                return onnx_path
            logger.warning("ONNX export produced no file; using PyTorch.")
        except Exception as e:
            logger.warning(f"ONNX unavailable ({e}); using PyTorch instead.")
        return None

    def _select_backend(self, model_path: str):
        """
        Build the ordered backend-candidate list for ``model_path``.

        Returns a list of ``(load_path, label, device)`` tuples; ``load_model``
        tries them in order and the first one whose load + smoke test succeeds
        wins. ``label`` is "" and ``device`` is None for plain PyTorch, which is
        always the last entry — the final fallback can never disappear.

        Priority:
          OpenVINO on ``config.OPENVINO_DEVICE`` (e.g. iGPU — only when set)
          → ONNX Runtime (``USE_ONNX``)
          → OpenVINO default device (legacy ``USE_OPENVINO``)
          → PyTorch (.pt)

        Every accelerator is opt-in via its config flag and yields no candidate
        on any problem, so with all flags at defaults this returns the same
        ONNX → OpenVINO → .pt priority as before — behaviour unchanged.
        """
        candidates = []
        device = getattr(config, "OPENVINO_DEVICE", None)
        ov_path = self._maybe_openvino(model_path)

        if device and ov_path is not None:
            candidates.append((ov_path, "OpenVINO", device))

        onnx_path = self._maybe_onnx(model_path)
        if onnx_path is not None:
            candidates.append((onnx_path, "ONNX", None))

        if not device and ov_path is not None:
            candidates.append((ov_path, "OpenVINO", None))

        candidates.append((model_path, "", None))
        return candidates

    def _accel_task(self, pt_path: str, onnx_path: str) -> Optional[str]:
        """
        Return the ultralytics task ('detect'/'segment'/'pose'/...) that an
        accelerated backend must be loaded with.

        WHY: exporting to ONNX/OpenVINO drops the task tag, so ``YOLO(model.onnx)``
        assumes ``task='detect'``. For a *-seg model (e.g. a segmentation bestX.pt)
        that mis-decodes the output tensor → garbage boxes / no boxes. The source
        of truth is the ``.pt``. We read its task once and cache it in a tiny
        ``<onnx>.task`` sidecar so later startups don't reload the .pt.
        """
        side = onnx_path + ".task"
        try:
            if (os.path.exists(side) and os.path.exists(onnx_path)
                    and os.path.getmtime(side) >= os.path.getmtime(onnx_path)):
                with open(side, "r", encoding="utf-8") as f:
                    t = f.read().strip()
                if t:
                    return t
        except Exception:
            pass
        # Authoritative: read the task straight from the .pt, then cache it.
        try:
            t = YOLO(pt_path).task
            try:
                with open(side, "w", encoding="utf-8") as f:
                    f.write(str(t or ""))
            except Exception:
                pass
            return t or None
        except Exception as e:
            logger.warning(f"Could not determine task from {pt_path}: {e}")
            return None

    def _smoke_test(self) -> bool:
        """
        Run ONE tiny inference to confirm the loaded backend actually executes
        without raising (catches an installed-but-incompatible onnxruntime/
        OpenVINO that loads the model yet crashes on inference). Returns True on
        success, False on any exception. Does NOT validate accuracy — that is the
        job of ``verify_onnx.py`` before USE_ONNX is turned on.
        """
        try:
            # Use the real live size so this also confirms the export is dynamic
            # enough for the size the system actually runs at (a fixed-shape export
            # would raise here and correctly trigger the PyTorch fallback).
            imgsz = getattr(config, "YOLO_IMGSZ", 480)
            dummy = np.zeros((480, 640, 3), dtype=np.uint8)
            extra = {"device": self.infer_device} if self.infer_device else {}
            with self._infer_lock:
                self.model(dummy, imgsz=imgsz, verbose=False, **extra)
            return True
        except Exception as e:
            logger.warning(f"Accelerated backend smoke test failed ({e}).")
            return False

    def load_model(self) -> bool:
        """
        Load YOLO model.
        Falls back to yolov8n.pt (pretrained) if custom model file is missing.
        """
        try:
            model_path = self.model_path

            if not os.path.exists(model_path):
                logger.warning(f"Custom model not found: {model_path}")
                logger.warning(
                    "Falling back to yolov8n.pt (COCO pretrained). "
                    "Train a custom model (Step 6) for real can_dent detection."
                )
                model_path = "yolov8n.pt"   # ultralytics downloads automatically on first run

            # PyTorch 2.6+ changed weights_only default to True which breaks
            # older ultralytics. Add DetectionModel to safe globals as workaround.
            try:
                import torch
                from ultralytics.nn.tasks import DetectionModel
                if hasattr(torch.serialization, 'add_safe_globals'):
                    torch.serialization.add_safe_globals([DetectionModel])
            except Exception:
                pass

            # Best-effort thread cap for the ONNX path (see config.ONNX_INTRA_THREADS).
            # Must be set before the backend builds its thread pool. Only applied
            # when the ONNX path is in use, so the PyTorch default is untouched.
            if getattr(config, "USE_ONNX", False):
                n_threads = int(getattr(config, "ONNX_INTRA_THREADS", 0) or 0)
                if n_threads > 0:
                    os.environ["OMP_NUM_THREADS"] = str(n_threads)

            # Ordered backend candidates (e.g. OpenVINO-iGPU → ONNX → .pt). With
            # all accel flags off (default) this is [ONNX?/OpenVINO?, .pt] exactly
            # as before — the first candidate that loads AND passes the smoke
            # test wins, and plain .pt is always last so PyTorch stays the final
            # fallback.
            accel, device = "", None
            for load_path, accel, device in self._select_backend(model_path):
                self.is_onnx = accel == "ONNX"
                self.is_openvino = accel == "OpenVINO"
                self.infer_device = device

                # Exported ONNX/OpenVINO loses the task tag → YOLO() assumes
                # 'detect'. For a segmentation model that mis-decodes the output.
                # Pass the real task read from the .pt so the backend decodes
                # exactly like the .pt.
                task = self._accel_task(model_path, load_path) if accel else None

                label = f"{accel}@{device}" if device else accel
                logger.info(f"Loading YOLO model: {load_path}"
                            + (f" [{label}{(', task=' + task) if task else ''}]" if accel else ""))
                try:
                    self.model = YOLO(load_path, task=task) if task else YOLO(load_path)
                    # An accelerated backend can load yet fail at inference time
                    # on an incompatible runtime — verify with a smoke test.
                    if accel and not self._smoke_test():
                        raise RuntimeError(f"{label} backend failed smoke test")
                    break
                except Exception as accel_err:
                    if accel:
                        logger.warning(f"{label} backend unusable ({accel_err}); "
                                       "trying next backend.")
                        self.model = None
                        continue
                    raise   # plain .pt itself failed → real error, surface it

            accel_label = f"{accel}@{device}" if device else accel
            logger.info("YOLO model loaded successfully"
                        + (f" ({accel_label} acceleration)" if accel else ""))

            if hasattr(self.model, 'names'):
                logger.info(f"Model classes ({len(self.model.names)}): {list(self.model.names.values())}")

            # Log model size + device. A heavy model (yolov8m/l/x) on CPU is the
            # other half of the "bestX.pt stutters" story — if avg inference time
            # stays high after the conf/vectorise fixes, the weights themselves
            # are the bottleneck and should be retrained/exported smaller
            # (yolov8n/s, or ONNX/OpenVINO/TensorRT).
            try:
                n_params = sum(p.numel() for p in self.model.model.parameters())
                size_mb = os.path.getsize(model_path) / (1024 * 1024) \
                    if os.path.exists(model_path) else 0.0
                device = getattr(self.model, "device", "cpu")
                logger.info(
                    "Model stats: %.1fM params, %.1f MB on disk, device=%s",
                    n_params / 1e6, size_mb, device)
                if n_params > 20e6 and str(device) in ("cpu", "cpu:0"):
                    logger.warning(
                        "Heavy model (%.1fM params) running on CPU — expect low FPS. "
                        "Consider a smaller variant (yolov8n/s) or ONNX/OpenVINO export.",
                        n_params / 1e6)
            except Exception as e:
                logger.debug(f"Could not introspect model stats: {e}")

            return True

        except Exception as e:
            logger.error(f"Failed to load YOLO model: {e}", exc_info=True)
            return False

    def classify_frame_bestx(self, detections: List[Dict]) -> Optional[str]:
        """
        bestX.pt verdict logic:
          "ng"  — "dent" detected → the whole can is defective
          "ok"  — "can" detected and no dent → good can
          None  — nothing detected yet → caller shows no verdict
        Only call when is_bestx_mode is True.
        """
        if any(d["class_name"] == "dent" for d in detections):
            return "ng"
        if any(d["class_name"] == "can" for d in detections):
            return "ok"
        return None

    def detect(self, frame: np.ndarray, imgsz: int = None) -> List[Dict]:
        """
        Perform defect detection on a frame.

        Args:
            frame: Input frame as numpy array (BGR format)
            imgsz: Optional inference image size override. Defaults to
                ``config.YOLO_IMGSZ`` (the live-stream size). Snapshot mode
                passes a larger value (``config.SNAPSHOT_IMGSZ``) to favour
                accuracy over speed.

        Returns:
            List of detection dictionaries with keys:
            - class_id: YOLO class ID
            - class_name: Defect type name
            - confidence: Detection confidence score
            - bbox: Bounding box [x1, y1, x2, y2]
            - center: Bounding box center [x, y]
        """
        if self.model is None:
            logger.error("Model not loaded. Call load_model() first.")
            return []

        try:
            # Confidence passed to the model itself. Normally we let the model's
            # NMS prune at our real threshold — this is the single biggest
            # live-feed speedup, because a low floor (0.01) let NMS emit up to
            # ``max_det`` junk boxes per frame that Python then had to walk one
            # tensor at a time. The 0.01 floor is only used when DEBUG logging
            # is on (so raw detections can still be inspected).
            debug_on = logger.isEnabledFor(logging.DEBUG)
            model_conf = 0.01 if debug_on else self.confidence_threshold

            # Only the OpenVINO-device path (opt-in) ever passes a device kwarg;
            # every other backend gets the exact same call as before.
            extra = {"device": self.infer_device} if self.infer_device else {}

            # ⚠️ ถือ lock เฉพาะช่วง "คำนวณบน GPU/CPU" เท่านั้น — การถอดผล
            # (.cpu() ด้านล่าง) เป็นการย้าย tensor ที่คำนวณเสร็จแล้ว ไม่แย่ง
            # หน่วยประมวลผล จึงไม่ต้องอยู่ในคิวและไม่ควรถ่วงเธรดอื่น.
            t_wait = time.perf_counter()
            with self._infer_lock:
                wait_ms = (time.perf_counter() - t_wait) * 1000.0
                self.last_wait_ms = round(wait_ms, 1)
                t0 = time.perf_counter()
                results = self.model(
                    frame,
                    conf=model_conf,
                    iou=self.iou_threshold,
                    imgsz=imgsz if imgsz is not None else config.YOLO_IMGSZ,
                    max_det=config.YOLO_MAX_DET,
                    verbose=False,
                    **extra
                )
                infer_ms = (time.perf_counter() - t0) * 1000.0
            # ``infer_ms`` คงความหมายเดิม (เวลาของโมเดลล้วน) เพื่อไม่ให้ตัวเลข
            # บนแถบ perf และ log เดิมเปลี่ยนความหมาย — เวลารอคิวรายงานแยก
            if wait_ms > self._INFER_WAIT_WARN_MS:
                logger.debug("รอคิวโมเดล %.0f ms ก่อนตรวจ (มีอีกเธรดใช้โมเดลอยู่)", wait_ms)

            detections: List[Dict] = []
            names = getattr(self.model, "names", {})
            known = set(self._class_names().keys())

            for result in results:
                boxes = result.boxes
                if boxes is None or len(boxes) == 0:
                    continue

                # Vectorised GPU→CPU transfer: pull all coords/cls/conf in ONE
                # sync each instead of 3 per box. At conf=0.01 with many boxes
                # the old per-box .cpu() calls dominated frame time and were a
                # primary cause of stutter on the two-class bestX.pt model.
                xyxy = boxes.xyxy.cpu().numpy()
                confs = boxes.conf.cpu().numpy()
                clss  = boxes.cls.cpu().numpy().astype(int)

                for (x1, y1, x2, y2), confidence, class_id in zip(xyxy, confs, clss):
                    confidence = float(confidence)
                    class_id = int(class_id)

                    if debug_on:
                        raw_name = names.get(class_id, str(class_id)) if names else str(class_id)
                        logger.debug(
                            f"RAW detect → class_id={class_id} name='{raw_name}' "
                            f"conf={confidence:.3f} (threshold={self.confidence_threshold})"
                        )

                    # When DEBUG lowered the model floor, re-apply our real
                    # threshold here so behaviour matches production.
                    if confidence < self.confidence_threshold:
                        continue

                    # ใช้ชื่อ class จากโมเดลโดยตรง (ตรงกับ data.yaml เสมอ)
                    class_name = (names.get(class_id, str(class_id)) if names
                                  else config.DEFECT_CLASSES.get(class_id, str(class_id)))
                    # Normalize to lower-case so palette/CLASS_NAMES keys stay
                    # simple no matter how the model was annotated
                    # (Good / GOOD / good all match the same entry).
                    class_name = str(class_name).lower()

                    # กรองเฉพาะ class ที่กำหนดใน mode/config
                    # known ว่าง = accept ทุก class (เช่น Label mode ก่อนเติม wording)
                    if known and class_name not in known:
                        if debug_on:
                            logger.debug(f"  SKIP unknown class: '{class_name}'")
                        continue

                    detections.append({
                        "class_id":   class_id,
                        "class_name": class_name,
                        "confidence": confidence,
                        "bbox":   [int(x1), int(y1), int(x2), int(y2)],
                        "center": [int((x1 + x2) / 2), int((y1 + y2) / 2)],
                    })

            # Throttled timing log so slow inference (the real stutter cause on
            # heavy weights) is visible without flooding the log every frame.
            self._infer_count += 1
            self._infer_ms_accum += infer_ms
            if self._infer_count % 30 == 0:
                avg = self._infer_ms_accum / 30.0
                logger.info("YOLO inference avg %.1f ms/frame (~%.1f FPS) over last 30 frames",
                            avg, 1000.0 / avg if avg > 0 else 0.0)
                self._infer_ms_accum = 0.0

            return _suppress_false_dent_spots(detections)

        except Exception as e:
            logger.error(f"Detection error: {str(e)}")
            return []

    def draw_detections(self, frame: np.ndarray, detections: List[Dict]) -> np.ndarray:
        """
        Draw bounding boxes and labels on frame.

        Rendering rules:
          good      → solid green rectangle, thickness 2, label above
          dented    → solid red rectangle,   thickness 3, label above
          dent_spot → orange corner brackets, thickness 2, label below box
                      (drawn last so it appears on top of dented box)
        """
        frame_copy = frame.copy()
        palette = self._colors()
        name_map = self._class_names()

        # bestX.pt: when a dent exists the whole can is defective, so drop the
        # green "can" box — it must never co-exist with the NG verdict.
        bestx_verdict = self.classify_frame_bestx(detections) if self.is_bestx_mode else None
        draw_targets = detections
        if bestx_verdict == "ng":
            draw_targets = [d for d in detections if d["class_name"] != "can"]

        # Draw good/dented first, dent_spot on top
        ordered = sorted(draw_targets,
                         key=lambda d: 1 if d["class_name"] == "dented_spot" else 0)

        for det in ordered:
            x1, y1, x2, y2 = det["bbox"]
            class_name     = det["class_name"]
            confidence     = det["confidence"]
            color          = palette.get(class_name, _COLOR_DEFAULT)

            # ── Box ──────────────────────────────────────
            if class_name == "dented_spot":
                _draw_corner_marks(frame_copy, x1, y1, x2, y2, color,
                                   thickness=2, length=18)
            elif class_name == "dented":
                cv2.rectangle(frame_copy, (x1, y1), (x2, y2), color, 3)
            else:
                cv2.rectangle(frame_copy, (x1, y1), (x2, y2), color, 2)

            # ── Label ────────────────────────────────────
            display_name = name_map.get(
                class_name, class_name.replace("_", " ").title()
            )
            label = f"{display_name}: {confidence:.2f}"

            font_scale = 0.52
            font_thick = 1
            (lw, lh), baseline = cv2.getTextSize(
                label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, font_thick + 1
            )

            # dent_spot label below box (avoids overlapping with dented label above)
            if class_name == "dented_spot":
                bg_y1   = y2 + 1
                bg_y2   = y2 + lh + baseline + 6
                text_y  = y2 + lh + 3
            else:
                bg_y1   = max(0, y1 - lh - baseline - 5)
                bg_y2   = y1
                text_y  = y1 - baseline - 3

            cv2.rectangle(frame_copy, (x1, bg_y1), (x1 + lw, bg_y2), color, -1)
            cv2.putText(frame_copy, label, (x1, text_y),
                        cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255),
                        font_thick + 1)

        # bestX.pt: overlay NG / OK verdict on the frame (None → nothing drawn)
        if self.is_bestx_mode:
            _draw_bestx_verdict(frame_copy, bestx_verdict)

        return frame_copy
    
    def get_detection_summary(self, detections: List[Dict]) -> Dict:
        """
        Get summary statistics of detections.
        
        Args:
            detections: List of detection dictionaries
            
        Returns:
            Dictionary with summary statistics
        """
        summary = {
            "total_defects": len(detections),
            "defects_by_type": {}
        }
        
        name_map = self._class_names()
        for det in detections:
            class_name = det["class_name"]
            display_name = name_map.get(
                class_name,
                class_name.replace("_", " ").title()
            )
            
            if display_name not in summary["defects_by_type"]:
                summary["defects_by_type"][display_name] = 0
            summary["defects_by_type"][display_name] += 1
        
        return summary


# Demo/Test function for custom model training guidance
def get_training_instructions() -> str:
    """
    Returns instructions for training a custom YOLO model.
    This is for documentation purposes.
    """
    return """
    To train a custom YOLO model for water bottle defect detection:
    
    1. Collect and annotate images:
       - Gather images of water bottles with various defects
       - Annotate using tools like LabelImg, CVAT, or Roboflow
       - Classes: crack, scratch, missing_label, wrong_label, missing_cap, wrong_cap_color
    
    2. Organize dataset in YOLO format:
       dataset/
         train/
           images/
           labels/
         val/
           images/
           labels/
         data.yaml (with class definitions)
    
    3. Train model:
       from ultralytics import YOLO
       model = YOLO('yolov8n.pt')  # Start from pretrained
       model.train(
           data='dataset/data.yaml',
           epochs=100,
           imgsz=640,
           batch=16
       )
    
    4. Use best.pt as MODEL_PATH in config.py
    
    NOTE: The current system uses a pretrained YOLO model (YOLOv8n) for demonstration.
    For production use, train a custom model with your specific defect classes.
    """
