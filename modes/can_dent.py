"""
Configuration for Can Dent Detection mode.
Class names / colors / Thai display labels for the can-body inspection model.
"""

import os

MODE_NAME = "can_dent"
DISPLAY_NAME = "Can Dent Detection"

WEIGHTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "weights", "can_dent"
)
DEFAULT_MODEL_FILE = "best.pt"

# YOLO class id → raw name (kept for backward compatibility / fallback only).
# At runtime ``yolo_detector`` reads ``model.names`` directly.
CLASS_IDS = {
    0: "dented",
    1: "dented_spot",
    2: "good",
}

# Display names — class not present here will be filtered out.
CLASS_NAMES = {
    "dented":      "Can Dent",
    "dented_spot": "Dent Area",
    "good":        "Can Good",
}

# BGR colors for drawing
COLORS = {
    "good":        (80, 200,   0),   # green
    "dented":      (0,   0, 220),    # red
    "dented_spot": (0, 100, 255),    # orange
}
