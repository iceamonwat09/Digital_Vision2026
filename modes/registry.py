"""
Mode registry — lookup table for available inspection modes.

Each mode module must expose:
    MODE_NAME, DISPLAY_NAME, WEIGHTS_DIR,
    DEFAULT_MODEL_FILE, CLASS_NAMES, COLORS
"""

import logging
import os
from importlib import import_module
from types import ModuleType
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

AVAILABLE_MODES = ["can_dent", "label"]
DEFAULT_MODE = "can_dent"


def get_mode_config(mode_name: str) -> ModuleType:
    """Return the config module for the given mode name."""
    if mode_name not in AVAILABLE_MODES:
        raise ValueError(
            f"Unknown mode '{mode_name}'. Available: {AVAILABLE_MODES}"
        )
    return import_module(f"modes.{mode_name}")


def list_modes() -> List[Dict[str, str]]:
    """Return [{name, display_name}, ...] for the UI dropdown."""
    out = []
    for m in AVAILABLE_MODES:
        cfg = get_mode_config(m)
        out.append({
            "name": cfg.MODE_NAME,
            "display_name": cfg.DISPLAY_NAME,
        })
    return out


def discover_models(mode_name: str) -> List[str]:
    """List ``*.pt`` filenames inside the mode's weights directory."""
    cfg = get_mode_config(mode_name)
    weights_dir = cfg.WEIGHTS_DIR
    if not os.path.isdir(weights_dir):
        return []
    return sorted(
        f for f in os.listdir(weights_dir)
        if f.endswith(".pt") and os.path.isfile(os.path.join(weights_dir, f))
    )


def resolve_model_path(mode_name: str, filename: Optional[str] = None) -> Optional[str]:
    """
    Return absolute path to the chosen ``.pt`` file, or ``None`` if no
    suitable file exists.

    Resolution order:
      1. ``filename`` argument if given and exists
      2. ``DEFAULT_MODEL_FILE`` declared by the mode if exists
      3. First ``.pt`` found in the weights dir
    """
    cfg = get_mode_config(mode_name)
    weights_dir = cfg.WEIGHTS_DIR

    candidates = []
    if filename:
        candidates.append(filename)
    if cfg.DEFAULT_MODEL_FILE:
        candidates.append(cfg.DEFAULT_MODEL_FILE)

    for name in candidates:
        path = os.path.join(weights_dir, name)
        if os.path.isfile(path):
            return path

    # ── ไม่มีไฟล์ที่ระบุ/ไฟล์ default ⇒ หยิบตัวแรกในโฟลเดอร์ ─────────────
    # ⚠️ **ต้อง log ว่าหยิบตัวไหน** — ตั้งแต่ตรรกะไม่ผูกกับชื่อไฟล์แล้ว โมเดล
    # ทุกตัวทำงานได้เหมือนกันหมด ⇒ การหยิบผิดตัวจะ "ดูเหมือนทำงานปกติ"
    # แต่เป็นคนละโมเดลกับที่ผู้ใช้ตั้งใจ (ผลตรวจต่างกันโดยไม่มีสัญญาณอะไรเลย)
    found = discover_models(mode_name)
    if found:
        wanted = [c for c in candidates if c]
        if wanted:
            logger.warning(
                "โหมด '%s': ไม่พบไฟล์ %s ในโฟลเดอร์ %s ⇒ ใช้ '%s' แทน "
                "(มีให้เลือก: %s)",
                mode_name, ", ".join(wanted), weights_dir, found[0], ", ".join(found))
        else:
            logger.info("โหมด '%s': เลือกโมเดล '%s' อัตโนมัติ (มีให้เลือก: %s)",
                        mode_name, found[0], ", ".join(found))
        return os.path.join(weights_dir, found[0])
    return None
