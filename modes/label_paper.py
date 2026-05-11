"""
Configuration for Label Paper Inspection mode.

This mode is NOT YOLO-based. It uses:
  - PDF master (PyMuPDF) for ground truth text
  - User-driven manual crop in the browser (no auto-crop)
  - Vertex Document AI for OCR  (Phase 2, currently stubbed)
  - Levenshtein + field-aware compare
  - Gemini for ambiguous cases only  (Phase 2, currently stubbed)

It deliberately stays outside ``modes/registry.AVAILABLE_MODES`` so the
existing YOLO mode-switcher (Can Dent / Label) is not affected.
"""

import os

MODE_NAME = "label_paper"
DISPLAY_NAME = "Label Paper Inspection"

# Kept for symmetry only — this mode does not use YOLO weights.
WEIGHTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "weights", "label_paper"
)
DEFAULT_MODEL_FILE = None
CLASS_NAMES: dict = {}
COLORS: dict = {}

# Data layout for masters / inspections / reports
DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "label_paper"
)
SKUS_DIR = os.path.join(DATA_DIR, "skus")
INSPECTIONS_DIR = os.path.join(DATA_DIR, "inspections")
REPORTS_DIR = os.path.join(DATA_DIR, "reports")

for _d in (SKUS_DIR, INSPECTIONS_DIR, REPORTS_DIR):
    os.makedirs(_d, exist_ok=True)
