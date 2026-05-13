"""
Configuration for Label Inspection mode.

The user trains the YOLO model externally (Roboflow + Colab) and drops the
resulting ``.pt`` file into ``weights/label/``. Class names are read from
``model.names`` at load time, so ``CLASS_NAMES`` below can stay empty until
the user pins the wording for the UI.
"""

import os

MODE_NAME = "label"
DISPLAY_NAME = "Label Inspection"

WEIGHTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "weights", "label"
)
DEFAULT_MODEL_FILE = None  # auto-pick first .pt found

# Empty dict → yolo_detector accepts every class produced by the model
# (no filtering). Populate after the user finalises class wording.
CLASS_NAMES: dict = {}
COLORS: dict = {}
