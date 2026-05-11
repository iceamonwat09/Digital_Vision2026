"""
Render a heatmap PNG showing where the captured label deviates from master.

Output is a base64-encoded PNG suitable for embedding directly into the
front-end response. Three layers are composited:

    1. The aligned captured image (faded)
    2. A JET colormap of the ΔE values where ΔE > tolerance
    3. Yellow bounding boxes + peak-ΔE labels for each clustered defect
"""

from __future__ import annotations

import base64
from typing import List, Optional

import cv2
import numpy as np


def make_heatmap_overlay(
    captured_rgb: np.ndarray,
    de_map: np.ndarray,
    tolerance: float,
    defects: List[dict],
    max_long_edge: int = 1100,
) -> Optional[str]:
    """
    Returns a base64 PNG (no data URL prefix) or None on encode failure.
    """
    if captured_rgb is None or de_map is None:
        return None

    H, W = captured_rgb.shape[:2]
    long_edge = max(H, W)
    scale = min(1.0, max_long_edge / long_edge) if long_edge > 0 else 1.0
    if scale < 1.0:
        new_w, new_h = int(W * scale), int(H * scale)
        captured_small = cv2.resize(captured_rgb, (new_w, new_h),
                                    interpolation=cv2.INTER_AREA)
        de_small = cv2.resize(de_map, (new_w, new_h),
                              interpolation=cv2.INTER_AREA)
    else:
        captured_small = captured_rgb.copy()
        de_small = de_map.copy()

    # Normalize ΔE so that 0 → cold, tolerance → mid, 3×tolerance → hot.
    norm_scale = 255.0 / max(tolerance * 3.0, 1.0)
    de_u8 = np.clip(de_small * norm_scale, 0, 255).astype(np.uint8)
    heat_bgr = cv2.applyColorMap(de_u8, cv2.COLORMAP_JET)

    captured_bgr = cv2.cvtColor(captured_small, cv2.COLOR_RGB2BGR)

    # Only paint heat where ΔE > tolerance; elsewhere keep the photo.
    mask = (de_small > tolerance).astype(np.float32)
    alpha = (0.55 * mask)[..., None]
    blended = (captured_bgr.astype(np.float32) * (1.0 - alpha)
               + heat_bgr.astype(np.float32) * alpha).astype(np.uint8)

    # Draw defect bboxes scaled to overlay coords.
    for idx, d in enumerate(defects, 1):
        x, y, w, h = d["bbox"]
        x = int(round(x * scale)); y = int(round(y * scale))
        w = int(round(w * scale)); h = int(round(h * scale))
        color = (0, 255, 255)  # cyan in BGR for high contrast
        cv2.rectangle(blended, (x, y), (x + w, y + h), color, 2)
        label = f"#{idx} {d['peak_de']:.1f}"
        cv2.putText(blended, label, (x, max(y - 6, 14)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)

    ok, buf = cv2.imencode(".png", blended)
    if not ok:
        return None
    return base64.b64encode(buf.tobytes()).decode("ascii")
