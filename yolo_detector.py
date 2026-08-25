"""
YOLO-based defect detection module.
Handles YOLOv8 model loading, inference, and defect classification.
"""

import cv2
import hashlib
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


# ── โครงสร้างที่ IR ของ OpenVINO ต้องมี — แยกตาม task ────────────────────────
# วัดจริงด้วย ultralytics 8.4.41 + openvino 2024.6.0 (มีเทสต์ล็อกไว้ใน
# tests/test_openvino_ir.py):
#     detect / pose / obb / classify → **1 output**
#     segment                        → **2 output**
#                                      กล่อง (1, 4+nc+nm, N) + prototype (1, nm, H, W)
#
# ⚠️ **ทำไมต้องเช็คตัวเลขนี้:** IR ของโมเดล segment ที่มี output เดียวจะ
# **โหลดผ่าน** (ไม่มี error ตอนโหลดเลย) แล้วไปพังตอน ultralytics ทำ ``preds[1]``
# ⇒ ``index 1 is out of bounds for dimension 0 with size 1`` ซึ่งอ่านแล้ว
# เดาต้นเหตุไม่ออก. เกิดจริงกับ `bestX.pt` บนสถานี 24-25 ส.ค. 2026 แล้วระบบ
# **ตกไป ONNX/CPU ถาวร** เพราะลายนิ้วมือถูกจดไว้แล้วว่า "IR ตรงกับ .pt"
# ⇒ ไม่มี export ใหม่อีกเลย = "ONNX ทำงานเสมอ" ทั้งที่ iGPU ใช้ได้
_IR_OUTPUTS_BY_TASK = {"segment": 2, "detect": 1, "pose": 1, "obb": 1, "classify": 1}


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
        # backend ที่ **ใช้จริง** หลังไล่ fallback เสร็จ + เหตุผลที่ตัวที่เร็วกว่าใช้ไม่ได้
        # ⚠️ เดิมรู้ได้จาก log บนคอนโซลเท่านั้น ⇒ ถ้าตกไป CPU แบบเงียบ ๆ ผู้ใช้จะเห็น
        # แค่ "ระบบช้าลง" โดยไม่รู้สาเหตุ (เกิดจริงบนสถานี 25 ส.ค.: 50 → 378 ms)
        self.backend_label = "PyTorch"
        self.backend_downgraded = False
        self.backend_note = ""
        self._accel_skips = []          # เหตุผลที่ตัวเร่งแต่ละตัวถูกข้าม

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
    def _openvino_device_status(device: str):
        """
        ตอบว่าอุปกรณ์ OpenVINO ที่ขอ (เช่น "intel:gpu" → "GPU") ใช้ได้ไหม
        **พร้อมเหตุผลที่แยกสาเหตุออกจากกัน** → ``(ok, reason)``.

        กันการที่ OpenVINO ถอยไป AUTO/CPU เองอย่างเงียบ ๆ เมื่อขอ GPU บนเครื่อง
        ที่ไม่มี (ผลตรวจยังถูก แต่ช้ากว่าเส้นทาง ONNX ของเรา และทำให้ตัวเลข
        ความเร็วบนจอ "โกหก") ⇒ เราข้าม OpenVINO ไปเลยแทน.

        ⚠️ **ทำไมต้องแยกเหตุผล:** เดิมทุกความล้มเหลวถูกรายงานเป็นข้อความเดียว
        ("ไม่พบอุปกรณ์นี้ในเครื่อง") ทั้งที่ต้นเหตุ 3 แบบนี้ **แก้คนละวิธี**:
        แพ็กเกจหาย (ลง pip) · แพ็กเกจมีแต่ไม่เห็น GPU (ไดรเวอร์/OpenCL) ·
        probe ระเบิดเอง. บอกรวม ๆ = ส่งผู้ใช้ไปแก้ของที่ไม่ได้พัง (กฎเหล็กข้อ 2)
        """
        want = device.split(":", 1)[1].upper() if ":" in device else device.upper()
        try:
            import openvino as ov
        except Exception as e:
            return False, ("ไม่ได้ติดตั้งแพ็กเกจ openvino (import ล้มเหลว: %s) — แก้ด้วย "
                           'py -3.9 -m pip install "openvino==2024.6.0"' % e)
        ver = getattr(ov, "__version__", "?")
        try:
            avail = list(ov.Core().available_devices)
        except Exception as e:
            return False, "openvino %s อ่านรายชื่ออุปกรณ์ไม่ได้ (%s)" % (ver, e)
        if any(d == want or d.startswith(want + ".") for d in avail):
            return True, "openvino %s พบ %s" % (ver, want)
        return False, ("openvino %s ติดตั้งแล้ว แต่ **ไม่เห็นอุปกรณ์ %s** "
                       "(เห็นแค่ %s) — เป็นเรื่องไดรเวอร์ Intel Graphics/OpenCL "
                       "ของเครื่อง ไม่ใช่เรื่องแพ็กเกจ" % (ver, want, avail or "ไม่มีเลย"))

    @classmethod
    def _openvino_device_available(cls, device: str) -> bool:
        """คงชื่อเดิมไว้ให้ผู้เรียกอื่น — ตัดสินอย่างเดียว ไม่เอาเหตุผล."""
        ok, why = cls._openvino_device_status(device)
        if not ok:
            logger.warning("OpenVINO ถูกข้าม: %s", why)
        return ok

    def _note_skip(self, who, why):
        """จดว่าตัวเร่งตัวไหนถูกข้าม เพราะอะไร — ใช้บอกผู้ใช้ทีหลัง."""
        try:
            self._accel_skips.append("%s: %s" % (who, why))
        except Exception:
            pass

    def _check_downgraded(self, accel):
        """
        ตั้งธง "ได้ backend ที่ช้ากว่าที่ตั้งไว้" — เทียบกับ **สิ่งที่ config ขอ**.

        ⚠️ เส้นทางที่พบบ่อยที่สุดคือ ``_maybe_openvino``/``_maybe_onnx`` คืน None
        ตั้งแต่ต้น (import ไม่ได้ / ไม่พบอุปกรณ์ / export ล้ม) ซึ่ง **ไม่ผ่าน
        except ของลูปเลย** ⇒ ถ้าดูแค่ลูปจะรายงานว่า "ปกติดี" ทั้งที่ตกไป CPU แล้ว
        """
        want_ov = bool(getattr(config, "OPENVINO_DEVICE", None)
                       or getattr(config, "USE_OPENVINO", False))
        want_onnx = bool(getattr(config, "USE_ONNX", False))
        if want_ov and accel != "OpenVINO":
            self.backend_downgraded = True
        elif want_onnx and accel not in ("ONNX", "OpenVINO"):
            self.backend_downgraded = True
        if self.backend_downgraded and not self.backend_note:
            self.backend_note = " · ".join(self._accel_skips[:3]) or "ไม่ทราบสาเหตุ"

    @staticmethod
    def _weights_hash(pt_path: str) -> Optional[str]:
        """
        ลายนิ้วมือของ **เนื้อไฟล์** ``.pt`` (sha1) — คำนวณครั้งเดียวตอนเริ่มระบบ.
        คืน ``None`` เมื่ออ่านไม่ได้ (ผู้เรียกจะถอยไปใช้ mtime แบบเดิม)
        """
        try:
            h = hashlib.sha1()
            with open(pt_path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return None

    def _export_is_stale(self, pt_path: str, export_path: str) -> bool:
        """
        ต้อง export ใหม่ไหม — ตัดสินจาก **เนื้อของ .pt** ไม่ใช่เวลาแก้ไขไฟล์.

        ⚠️ **ทำไมถึงเลิกใช้ mtime:** การ *คัดลอกไฟล์เดิมกลับมา* (กู้จากถังขยะ /
        คัดลอกจากที่สำรองไว้) ทำให้ mtime กลายเป็น "ตอนนี้" ⇒ ใหม่กว่า IR เสมอ
        ⇒ สั่ง re-export ทุกครั้งที่เปิดโปรแกรม. ถ้า export ล้มเหลว (เช่นไม่มี
        openvino ในรอบนั้น) ระบบจะ **ตกไปวิ่งบน CPU ช้ากว่า 6 เท่าอย่างเงียบ ๆ**
        — เกิดจริงบนสถานี 25 ส.ค. 2026 หลังผู้ใช้กู้ `bestX.pt` คืนมา
        (`inf` 45-50 ms → 155-378 ms).

        เนื้อไฟล์เหมือนเดิม = โมเดลตัวเดิม = export เดิมยังใช้ได้ ⇒ ไม่ต้องทำใหม่.
        เทรนใหม่จริง = เนื้อเปลี่ยน = export ใหม่ (ความถูกต้องยังคงอยู่ครบ)
        """
        if not os.path.exists(export_path):
            return True
        side = export_path + ".src"
        cur = self._weights_hash(pt_path)
        if cur is None:                       # อ่าน .pt ไม่ได้ → ใช้เกณฑ์เดิม
            try:
                return os.path.getmtime(pt_path) > os.path.getmtime(export_path)
            except Exception:
                return True
        try:
            with open(side, "r", encoding="utf-8") as f:
                if f.read().strip() == cur:
                    return False              # เนื้อเดิมเป๊ะ → export เดิมใช้ได้
        except Exception:
            pass
        # ยังไม่มี sidecar: อย่าเพิ่งด่วน re-export ถ้า export ใหม่กว่า .pt อยู่แล้ว
        # (กรณีอัปเกรดจากเวอร์ชันก่อนที่ยังไม่มีระบบ hash) — แค่จดลายนิ้วมือไว้
        try:
            if os.path.getmtime(export_path) >= os.path.getmtime(pt_path):
                self._remember_hash(export_path, cur)
                return False
        except Exception:
            pass
        return True

    @staticmethod
    def _remember_hash(export_path: str, digest: Optional[str]) -> None:
        """จดลายนิ้วมือของ .pt ที่ใช้ export ครั้งนี้ไว้ข้าง ๆ ไฟล์ export."""
        if not digest:
            return
        try:
            with open(export_path + ".src", "w", encoding="utf-8") as f:
                f.write(digest)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────
    # ตรวจ "ไฟล์ IR ใช้ได้จริงไหม" ก่อนส่งให้ ultralytics
    #
    # บทเรียนสถานี 25 ส.ค. 2026: ด่านเดิมมีแค่ ``_smoke_test`` ซึ่งจับได้ถูกต้อง
    # แต่ **จับได้ทีหลัง** — ตอนนั้นลายนิ้วมือถูกจดลง ``<xml>.src`` ไปแล้ว
    # ⇒ รอบถัดไป ``_export_is_stale`` ตอบว่า "ตรงกัน ไม่ต้อง export ใหม่"
    # ⇒ โหลด IR ที่เสียตัวเดิม → smoke test พังซ้ำ → ตกไป ONNX **ทุกครั้งตลอดไป**
    # ตรงนี้จึงเพิ่ม 3 อย่าง: ตรวจโครงสร้าง IR ก่อน · จดลายนิ้วมือ **หลัง**
    # ผ่านการตรวจเท่านั้น · ซ่อมเองได้เมื่อพบว่าเสีย
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _ov_paths(pt_path: str):
        """คืน (โฟลเดอร์ IR, ไฟล์ .xml) ของโมเดลนี้ — ชื่อเดียวกับที่ exporter ใช้."""
        stem = pt_path[:-3]
        return stem + "_openvino_model", os.path.join(
            stem + "_openvino_model", os.path.basename(stem) + ".xml")

    @staticmethod
    def _ir_output_shapes(xml_path: str):
        """
        รูปร่างของทุก output ใน IR — คืน ``None`` เมื่ออ่านไม่ได้ (ไม่ใช่ "เสีย").

        แยก "อ่านไม่ได้" ออกจาก "อ่านได้แล้วผิด" โดยตั้งใจ: ถ้าเปิดไฟล์ไม่ได้เรา
        **ไม่ตัดสิน** แล้วปล่อยให้ smoke test เป็นคนชี้ขาด — การเดาว่าเสียแล้วลบ
        IR ที่ยังดีอยู่ทิ้ง เสียหายกว่าการปล่อยผ่าน
        """
        try:
            import openvino as ov
            model = ov.Core().read_model(xml_path)
        except Exception:
            return None
        shapes = []
        for out in model.outputs:
            try:
                shapes.append(str(out.get_partial_shape()))
            except Exception:
                shapes.append("?")
        return shapes

    def _validate_ir(self, pt_path: str, ov_dir: str):
        """
        ``(ok, reason)`` — IR ตัวนี้เข้ากับ task ของ ``.pt`` ไหม.

        ตรวจ **จำนวน output เทียบกับ task** ซึ่งเป็นความผิดพลาดที่เกิดจริงและ
        เป็นชนิดที่ ultralytics ตรวจไม่เจอตอนโหลด (ดู ``_IR_OUTPUTS_BY_TASK``).
        อ่านไม่ได้/ไม่รู้ task = **ไม่ตัดสิน → ผ่าน** (smoke test เป็นด่านถัดไป)
        """
        if not getattr(config, "OPENVINO_VALIDATE_IR", True):
            return True, "ปิดการตรวจ IR ไว้ (OPENVINO_VALIDATE_IR = False)"
        _, xml = self._ov_paths(pt_path)
        if not os.path.exists(xml):
            return False, "ไม่พบไฟล์ IR (%s)" % os.path.basename(xml)
        shapes = self._ir_output_shapes(xml)
        if shapes is None:
            return True, "ข้ามการตรวจ IR (เปิดไฟล์ด้วย openvino ไม่ได้)"
        task = self._accel_task(pt_path, ov_dir)
        want = _IR_OUTPUTS_BY_TASK.get(task or "")
        if want is None:
            return True, "IR มี %d output (ไม่รู้ task จึงไม่ตัดสิน)" % len(shapes)
        if len(shapes) != want:
            return False, ("IR ของโมเดล task=%s ต้องมี %d output แต่ไฟล์นี้มี %d "
                           "(%s) ⇒ **ไฟล์ IR เสีย/ไม่ใช่ของโมเดลตัวนี้**"
                           % (task, want, len(shapes), " · ".join(shapes)))
        return True, "IR ถูกต้อง: task=%s · %d output (%s)" % (
            task, len(shapes), " · ".join(shapes))

    # ── เครื่องหมาย "IR ตัวนี้พิสูจน์แล้วว่าใช้ไม่ได้" ────────────────────────
    # กันสองอาการที่ตรงข้ามกัน: (ก) ลองใหม่ทุกครั้งจน export ใหม่ทุกบูต (ช้า)
    # (ข) ยอมแพ้ตั้งแต่ครั้งแรกจนกู้ไม่ขึ้นเอง. จึงให้ **ลองซ่อมได้ 1 รอบ**
    # แล้วถ้ายังพังซ้ำกับเนื้อ .pt เดิม ค่อยหยุดลอง (ลบโฟลเดอร์ IR = เริ่มใหม่)

    @staticmethod
    def _bad_marker_path(ov_dir: str) -> str:
        return ov_dir.rstrip("\\/") + ".badir"

    def _read_bad_marker(self, ov_dir: str):
        try:
            with open(self._bad_marker_path(ov_dir), "r", encoding="utf-8") as f:
                parts = f.read().split("\n")
            return parts[0].strip(), int(parts[1].strip()), (parts[2] if len(parts) > 2 else "")
        except Exception:
            return None, 0, ""

    def _write_bad_marker(self, ov_dir: str, digest, tries: int, reason: str) -> None:
        try:
            with open(self._bad_marker_path(ov_dir), "w", encoding="utf-8") as f:
                f.write("%s\n%d\n%s" % (digest or "", tries, (reason or "")[:400]))
        except Exception:
            pass

    def _clear_bad_marker(self, ov_dir: str) -> None:
        try:
            os.remove(self._bad_marker_path(ov_dir))
        except Exception:
            pass

    @staticmethod
    def _drop_stamp(export_path: str) -> None:
        """
        ลบลายนิ้วมือ ``<export>.src``.

        ⚠️ **จำเป็น ไม่ใช่แค่ "ไม่เขียนเพิ่ม"** — ``_export_is_stale`` เองมีสาขาที่
        **เขียน sidecar ให้อัตโนมัติ** (กรณีอัปเกรดจากเวอร์ชันที่ยังไม่มีระบบ hash
        และไฟล์ export ใหม่กว่า .pt) ⇒ แค่ "ไม่จด" ไม่พอ IR ที่เสียจะถูกประทับ
        ว่า "ตรงกับ .pt" โดยตัวตรวจ staleness เอง
        """
        try:
            os.remove(export_path + ".src")
        except Exception:
            pass

    def _ir_retry_pending(self, ov_dir: str, digest) -> bool:
        """เคยพังไป 1 ครั้งกับเนื้อ .pt ตัวนี้ → รอบนี้ต้อง **บังคับ export ใหม่**."""
        seen, tries, _ = self._read_bad_marker(ov_dir)
        return bool(digest and seen == digest and tries == 1)

    def _ir_given_up(self, ov_dir: str, digest) -> str:
        """คืนเหตุผลถ้าเคยลองซ่อม IR ของเนื้อ .pt ตัวนี้แล้วไม่สำเร็จ — ไม่งั้นคืน ""."""
        seen, tries, reason = self._read_bad_marker(ov_dir)
        if digest and seen == digest and tries >= 2:
            return reason or "เคยพิสูจน์แล้วว่า IR ของโมเดลนี้ใช้ไม่ได้"
        return ""

    def _ensure_onnx_file(self, pt_path: str) -> Optional[str]:
        """
        ไฟล์ ``.onnx`` ที่ตรงกับ ``.pt`` (export ให้ถ้ายังไม่มี) — **ไม่ขึ้นกับ
        ``USE_ONNX``** เพราะใช้เป็นวัตถุดิบของการซ่อม IR เท่านั้น
        """
        onnx_path = pt_path[:-3] + ".onnx"
        try:
            if os.path.exists(onnx_path) and not self._export_is_stale(pt_path, onnx_path):
                return onnx_path
            kwargs = dict(format="onnx", dynamic=True, half=False)
            opset = getattr(config, "ONNX_OPSET", None)
            if opset:
                kwargs["opset"] = int(opset)
            try:
                YOLO(pt_path).export(simplify=True, **kwargs)
            except Exception:
                YOLO(pt_path).export(simplify=False, **kwargs)
            if os.path.exists(onnx_path):
                self._remember_hash(onnx_path, self._weights_hash(pt_path))
                return onnx_path
        except Exception as e:
            logger.warning("ซ่อม IR: export .onnx ไม่สำเร็จ (%s)", e)
        return None

    @staticmethod
    def _sync_metadata_names(pt_path: str, meta_path: str):
        """
        ``metadata.yaml`` ต้องมี **ชื่อคลาสของ .pt ตัวนี้จริง ๆ** ก่อนจะซ่อม IR.

        ⚠️ กับดักที่จะเกิดถ้าไม่ทำ: โฟลเดอร์ IR ที่เสียมักเป็นของ **โมเดลตัวอื่น**
        (นั่นแหละสาเหตุที่ output ไม่ตรง) ⇒ ``metadata.yaml`` ในนั้นก็เป็นของ
        โมเดลตัวอื่นด้วย. ซ่อมแต่ ``.xml``/``.bin`` แล้วปล่อยชื่อคลาสเดิมไว้ =
        ระบบตรวจถูกแต่ **เรียกชื่อผิด** โดยไม่มี error เลย (กฎเหล็กข้อ 2)
        """
        try:
            import yaml
        except Exception as e:
            return False, "ไม่มีแพ็กเกจ yaml จึงตรวจชื่อคลาสไม่ได้ — ไม่ซ่อม (%s)" % e
        try:
            y = YOLO(pt_path)
            names = {int(k): str(v) for k, v in dict(y.names).items()}
            task = y.task
        except Exception as e:
            return False, "อ่านชื่อคลาสจาก .pt ไม่ได้ — ไม่ซ่อม (%s)" % e
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                md = yaml.safe_load(f) or {}
        except Exception:
            md = {}
        cur = md.get("names")
        if isinstance(cur, dict):
            cur = {int(k): str(v) for k, v in cur.items()}
        if cur == names and md.get("task") == task:
            return True, "metadata.yaml ตรงกับ .pt อยู่แล้ว"
        md["names"], md["task"] = names, task
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(md, f, allow_unicode=True, sort_keys=False)
        except Exception as e:
            return False, "เขียน metadata.yaml ไม่ได้ — ไม่ซ่อม (%s)" % e
        logger.warning("metadata.yaml ไม่ตรงกับ .pt (ชื่อคลาส/ task) — เขียนใหม่จาก %s",
                       os.path.basename(pt_path))
        return True, "เขียน metadata.yaml ใหม่จาก .pt"

    def _repair_ir_from_onnx(self, pt_path: str, ov_dir: str):
        """
        เส้นทางสำรอง: สร้าง IR **จากไฟล์ .onnx** แทนการแปลงจาก PyTorch ตรง ๆ.

        ``ultralytics.export(format="openvino")`` แปลงจาก **โมเดล PyTorch โดยตรง**
        (``ov.convert_model(model, example_input=...)``) ไม่ได้ผ่าน ONNX เลย ⇒
        ถ้าเส้นทางนั้นให้ IR ที่โครงสร้างผิด เส้นทาง ONNX → IR เป็นทางที่สอง
        ซึ่ง **พิสูจน์แล้วว่าให้ tensor เท่ากันทุกหลัก** กับ PyTorch
        (Δ สูงสุด 5e-5 บนกล่อง · 1e-8 บน prototype — มีเทสต์ล็อกตัวเลขนี้ไว้)

        ⚠️ ทับเฉพาะ ``.xml``/``.bin`` ส่วน ``metadata.yaml`` (ที่เก็บ **ชื่อคลาส**)
        ถูก *ตรวจและเขียนใหม่จาก ``.pt``* ด้วย ``_sync_metadata_names`` ก่อนเสมอ.
        วัดจริงแล้วว่า **ไม่มีไฟล์นั้น ⇒ ชื่อคลาสกลายเป็น ``class0``/``class1``**
        โดยไม่มี error เลย ⇒ ไม่มี metadata.yaml = **ไม่ซ่อม** (กฎเหล็กข้อ 2)
        """
        meta = os.path.join(ov_dir, "metadata.yaml")
        if not os.path.exists(meta):
            return False, "ไม่มี metadata.yaml (ชื่อคลาสจะหาย) — ไม่ซ่อม"
        ok_meta, why_meta = self._sync_metadata_names(pt_path, meta)
        if not ok_meta:
            return False, why_meta
        onnx_path = self._ensure_onnx_file(pt_path)
        if not onnx_path:
            return False, "ไม่มีไฟล์ .onnx ให้ใช้เป็นวัตถุดิบ"
        _, xml = self._ov_paths(pt_path)
        try:
            import openvino as ov
            logger.info("ซ่อม IR ด้วยเส้นทาง ONNX → OpenVINO: %s", onnx_path)
            ov.save_model(ov.convert_model(onnx_path), xml, compress_to_fp16=False)
        except Exception as e:
            return False, "แปลง ONNX → IR ไม่สำเร็จ (%s)" % e
        return self._validate_ir(pt_path, ov_dir)

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
        if device:
            ok, why = self._openvino_device_status(device)
            if not ok:
                logger.error("⚠️ ข้าม OpenVINO@%s — %s", device, why)
                self._note_skip("OpenVINO@%s" % device, why)
                return None
            logger.info("OpenVINO device พร้อมใช้: %s", why)
        ov_dir, ov_xml = self._ov_paths(pt_path)
        digest = self._weights_hash(pt_path)

        # เคยลองซ่อม IR ของเนื้อ .pt ตัวนี้จนหมดทางแล้ว → ข้ามทันที ไม่ต้อง
        # เสียเวลา export ซ้ำทุกครั้งที่เปิดโปรแกรม (ลบโฟลเดอร์ IR = เริ่มใหม่)
        given_up = self._ir_given_up(ov_dir, digest)
        if given_up:
            logger.error("⚠️ ข้าม OpenVINO — %s", given_up)
            self._note_skip("OpenVINO", given_up)
            return None

        try:
            stale = self._export_is_stale(pt_path, ov_xml)
            retry = self._ir_retry_pending(ov_dir, digest)
            exported = False
            if retry:
                logger.info("รอบก่อน IR ตัวนี้ใช้ไม่ได้ — บังคับ export ใหม่ 1 รอบ")
            if not os.path.isdir(ov_dir) or not os.path.exists(ov_xml) or stale or retry:
                if stale and os.path.exists(ov_xml):
                    logger.info(f"OpenVINO export ไม่ตรงกับ .pt ปัจจุบัน — export ใหม่: {pt_path}")
                else:
                    logger.info(f"Exporting OpenVINO model (one-time, FP32/dynamic): {pt_path}")
                YOLO(pt_path).export(format="openvino", dynamic=True, half=False)
                exported = True
            if not (os.path.isdir(ov_dir) and os.path.exists(ov_xml)):
                logger.warning("OpenVINO export produced no model dir; using next backend.")
                self._note_skip("OpenVINO", "export ไม่ได้สร้างโฟลเดอร์ IR")
                return None

            # ── ด่านใหม่: IR เข้ากับ task ของโมเดลไหม ────────────────────
            ok, why = self._validate_ir(pt_path, ov_dir)
            repair = bool(getattr(config, "OPENVINO_AUTO_REPAIR", True))
            if not ok and not exported and repair:
                # ยังไม่ได้ export ในรอบนี้ = กำลังใช้ IR เก่าที่เสีย → ทำใหม่
                logger.error("⚠️ IR เดิมใช้ไม่ได้ (%s) — ลบแล้ว export ใหม่", why)
                try:
                    import shutil
                    shutil.rmtree(ov_dir, ignore_errors=True)
                except Exception:
                    pass
                YOLO(pt_path).export(format="openvino", dynamic=True, half=False)
                ok, why = self._validate_ir(pt_path, ov_dir)
            if not ok and repair:
                logger.error("⚠️ IR ที่ export ใหม่ยังใช้ไม่ได้ (%s) — ลองเส้นทาง ONNX → IR", why)
                ok, why = self._repair_ir_from_onnx(pt_path, ov_dir)
            if not ok:
                # ⚠️ **ห้ามจดลายนิ้วมือ** เมื่อยังไม่ผ่าน — นี่คือบั๊กเดิมที่ทำให้
                # IR ที่เสียถูกมองว่า "ตรงกับ .pt" แล้วไม่มี export ใหม่อีกเลย
                seen, tries, _ = self._read_bad_marker(ov_dir)
                self._drop_stamp(ov_xml)
                self._write_bad_marker(ov_dir, digest,
                                       min((tries if seen == digest else 0) + 1, 2), why)
                logger.error("⚠️ ข้าม OpenVINO — %s", why)
                self._note_skip("OpenVINO", why)
                return None

            logger.info("IR ผ่านการตรวจโครงสร้าง: %s", why)
            self._remember_hash(ov_xml, digest)
            # ⚠️ **ยังไม่ล้างเครื่องหมาย "เคยพัง" ตรงนี้** — ล้างเมื่อ smoke test
            # ผ่านใน ``load_model`` เท่านั้น ไม่งั้นตัวนับจะถูกรีเซ็ตทุกบูต แล้ว
            # ระบบจะ export ใหม่ไม่รู้จบกับ IR ที่ซ่อมไม่ขึ้น
            return ov_dir
        except Exception as e:
            logger.warning(f"OpenVINO unavailable ({e}); using next backend instead.")
            self._note_skip("OpenVINO", e)
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
            stale = self._export_is_stale(pt_path, onnx_path)
            if not os.path.exists(onnx_path) or stale:
                if stale and os.path.exists(onnx_path):
                    logger.info(f"ONNX ไม่ตรงกับ .pt ปัจจุบัน — export ใหม่: {pt_path}")
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
                self._remember_hash(onnx_path, self._weights_hash(pt_path))
                return onnx_path
            logger.warning("ONNX export produced no file; using PyTorch.")
            self._note_skip("ONNX", "export ไม่ได้สร้างไฟล์")
        except Exception as e:
            logger.warning(f"ONNX unavailable ({e}); using PyTorch instead.")
            self._note_skip("ONNX", e)
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

    def _quarantine_ir(self, pt_path: str, ov_dir: str, err) -> None:
        """
        IR โหลดผ่านแต่ **ตรวจภาพจริงไม่ผ่าน** (smoke test) → เตรียม export ใหม่รอบหน้า.

        ⚠️ นี่คือครึ่งหลังของบั๊ก "ONNX ทำงานเสมอ": เดิมลายนิ้วมือ ``<xml>.src``
        ถูกจดไว้แล้วก่อนถึง smoke test ⇒ รอบถัดไป ``_export_is_stale`` ตอบว่า
        "ตรงกัน" ⇒ โหลด IR ที่เสียตัวเดิมซ้ำ ⇒ ตกไป ONNX **ทุกครั้งตลอดไป**
        โดยไม่มีอะไรพยายามซ่อมเลย.

        ลบ sidecar ⇒ รอบหน้า export ใหม่ (ซ่อมตัวเองได้) และนับครั้งไว้ —
        พังซ้ำกับเนื้อ ``.pt`` เดิมหลัง export ใหม่แล้ว = หยุดลอง (ไม่งั้นจะ
        export ใหม่ทุกบูตซึ่งช้าและไม่ช่วยอะไร)
        """
        if not getattr(config, "OPENVINO_AUTO_REPAIR", True):
            return                      # ปิดการซ่อม = พฤติกรรมเดิมทุกประการ
        try:
            if not os.path.isdir(ov_dir):
                return
            _, xml = self._ov_paths(pt_path)
            digest = self._weights_hash(pt_path)
            seen, tries, _ = self._read_bad_marker(ov_dir)
            tries = tries + 1 if seen == digest else 1
            self._write_bad_marker(ov_dir, digest, min(tries, 2),
                                   "smoke test ไม่ผ่าน: %s" % err)
            self._drop_stamp(xml)       # ⇒ รอบหน้าถูกบังคับให้ export ใหม่
            logger.error("IR ถูกกักไว้เพื่อ export ใหม่ในรอบถัดไป (ครั้งที่ %d/2)", min(tries, 2))
        except Exception:
            pass

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
                    if accel == "OpenVINO":
                        # ผ่านครบทั้งโครงสร้างและการตรวจภาพจริงแล้ว → ล้างประวัติ
                        self._clear_bad_marker(load_path)
                    break
                except Exception as accel_err:
                    if accel:
                        # ⚠️ ERROR ไม่ใช่ warning — การตกไป backend ที่ช้ากว่าคือ
                        # เรื่องที่ผู้ใช้ต้องรู้ ไม่ใช่รายละเอียดที่ซ่อนได้
                        logger.error("⚠️ %s ใช้ไม่ได้ (%s) — ตกไป backend ถัดไป "
                                     "ซึ่ง**ช้ากว่าหลายเท่า**", label, accel_err)
                        if accel == "OpenVINO":
                            self._quarantine_ir(model_path, load_path, accel_err)
                        self.backend_downgraded = True
                        if not self.backend_note:
                            self.backend_note = "%s: %s" % (label, accel_err)
                        self.model = None
                        continue
                    raise   # plain .pt itself failed → real error, surface it

            accel_label = (f"{accel}@{device}" if device else accel) or "PyTorch"
            self.backend_label = accel_label
            self._check_downgraded(accel)
            if self.backend_downgraded:
                logger.error(
                    "🐢 กำลังใช้ **%s** ซึ่งไม่ใช่ตัวที่เร็วที่สุดที่ตั้งไว้ "
                    "(%s) — inference จะช้ากว่าปกติหลายเท่า. "
                    "หาสาเหตุที่แน่นอนด้วย: py -3.9 diagnose_igpu.py "
                    "(เติม --fix เพื่อให้ซ่อม IR ให้อัตโนมัติ)",
                    accel_label, self.backend_note)
            else:
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
