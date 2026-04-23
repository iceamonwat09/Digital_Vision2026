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

                        if class_id not in config.DEFECT_CLASSES:
                            logger.debug(f"  SKIP class_id={class_id} not in DEFECT_CLASSES")
                            continue

                        class_name = config.DEFECT_CLASSES[class_id]
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
        
        Args:
            frame: Input frame
            detections: List of detection dictionaries
            
        Returns:
            Frame with drawn bounding boxes and labels
        """
        frame_copy = frame.copy()
        
        # Color mapping — Can Dent classes first, bottle classes kept for legacy
        colors = {
            "can_dent": (0, 0, 255),        # Red   — defect
            "can_good": (0, 255, 0),        # Green — good
            "cap":           (0, 255, 0),
            "crumbled":      (0, 0, 255),
            "label":         (255, 0, 0),
            "no-cap":        (255, 165, 0),
            "not-crumbled":  (0, 255, 255),
        }
        DEFAULT_COLOR = (0, 255, 255)   # Cyan fallback for any unknown class

        for det in detections:
            x1, y1, x2, y2 = det["bbox"]
            class_name = det["class_name"]
            confidence = det["confidence"]

            color = colors.get(class_name, DEFAULT_COLOR)
            
            # Draw bounding box
            cv2.rectangle(frame_copy, (x1, y1), (x2, y2), color, 2)
            
            # Prepare label text
            display_name = config.DEFECT_CLASS_NAMES.get(
                class_name,
                class_name.replace("_", " ").title()
            )
            label = f"{display_name}: {confidence:.2f}"
            
            # Calculate label size and position
            (label_width, label_height), baseline = cv2.getTextSize(
                label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2
            )
            
            # Draw label background
            cv2.rectangle(
                frame_copy,
                (x1, y1 - label_height - baseline - 5),
                (x1 + label_width, y1),
                color,
                -1
            )
            
            # Draw label text
            cv2.putText(
                frame_copy,
                label,
                (x1, y1 - baseline - 3),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                2
            )
        
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
