"""
YOLO-based defect detection module.
Handles YOLOv8 model loading, inference, and defect classification.
"""

import cv2
import numpy as np
import os
from typing import List, Dict, Tuple, Optional
from ultralytics import YOLO
import config
from logger import setup_logger

logger = setup_logger(__name__)

# BGR color palette
_COLORS = {
    "good":         (80,  200,  0),    # เขียว
    "dented":       (0,    0,  220),   # แดง   (กรอบหนา)
    "dented_spot":  (0,  100,  255),   # ส้ม   (corner marks)
}
_COLOR_DEFAULT = (0, 165, 255)      # ส้ม fallback


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


class YOLODetector:
    """
    YOLO-based defect detector for water bottles.
    Handles model loading, inference, and result processing.
    """
    
    def __init__(self, model_path: str = None):
        """
        Initialize YOLO detector.
        
        Args:
            model_path: Path to YOLO model. If None, uses config.MODEL_PATH
        """
        self.model_path = model_path if model_path is not None else config.MODEL_PATH
        self.model: Optional[YOLO] = None
        self.confidence_threshold = config.CONFIDENCE_THRESHOLD
        self.iou_threshold = config.IOU_THRESHOLD
        
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

            logger.info(f"Loading YOLO model: {model_path}")
            self.model = YOLO(model_path)
            logger.info("YOLO model loaded successfully")

            if hasattr(self.model, 'names'):
                logger.info(f"Model classes ({len(self.model.names)}): {list(self.model.names.values())}")

            return True

        except Exception as e:
            logger.error(f"Failed to load YOLO model: {e}", exc_info=True)
            return False
    
    def detect(self, frame: np.ndarray) -> List[Dict]:
        """
        Perform defect detection on a frame.
        
        Args:
            frame: Input frame as numpy array (BGR format)
            
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
            # Run YOLO inference (low conf=0.01 to capture ALL raw detections for debug)
            results = self.model(
                frame,
                conf=0.01,
                iou=self.iou_threshold,
                verbose=False
            )

            detections = []

            for result in results:
                boxes = result.boxes
                if boxes is not None and len(boxes) > 0:
                    for box in boxes:
                        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                        class_id   = int(box.cls[0].cpu().numpy())
                        confidence = float(box.conf[0].cpu().numpy())

                        # Debug: log every raw detection so we can see what model finds
                        raw_name = (self.model.names.get(class_id, str(class_id))
                                    if hasattr(self.model, 'names') else str(class_id))
                        logger.info(
                            f"RAW detect → class_id={class_id} name='{raw_name}' "
                            f"conf={confidence:.3f} (threshold={self.confidence_threshold})"
                        )

                        # Apply our confidence threshold
                        if confidence < self.confidence_threshold:
                            continue

                        # ใช้ชื่อ class จากโมเดลโดยตรง (ตรงกับ data.yaml เสมอ)
                        class_name = (self.model.names.get(class_id, str(class_id))
                                      if hasattr(self.model, 'names')
                                      else config.DEFECT_CLASSES.get(class_id, str(class_id)))

                        # กรองเฉพาะ class ที่กำหนดใน config
                        known = set(config.DEFECT_CLASS_NAMES.keys())
                        if known and class_name not in known:
                            logger.debug(f"  SKIP unknown class: '{class_name}'")
                            continue

                        detection = {
                            "class_id":   class_id,
                            "class_name": class_name,
                            "confidence": confidence,
                            "bbox":   [int(x1), int(y1), int(x2), int(y2)],
                            "center": [int((x1 + x2) / 2), int((y1 + y2) / 2)]
                        }
                        detections.append(detection)
            
            return detections
            
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

        # Draw good/dented first, dent_spot on top
        ordered = sorted(detections,
                         key=lambda d: 1 if d["class_name"] == "dented_spot" else 0)

        for det in ordered:
            x1, y1, x2, y2 = det["bbox"]
            class_name     = det["class_name"]
            confidence     = det["confidence"]
            color          = _COLORS.get(class_name, _COLOR_DEFAULT)

            # ── Box ──────────────────────────────────────
            if class_name == "dented_spot":
                _draw_corner_marks(frame_copy, x1, y1, x2, y2, color,
                                   thickness=2, length=18)
            elif class_name == "dented":
                cv2.rectangle(frame_copy, (x1, y1), (x2, y2), color, 3)
            else:
                cv2.rectangle(frame_copy, (x1, y1), (x2, y2), color, 2)

            # ── Label ────────────────────────────────────
            display_name = config.DEFECT_CLASS_NAMES.get(
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
        
        for det in detections:
            class_name = det["class_name"]
            display_name = config.DEFECT_CLASS_NAMES.get(
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
