"""
Geometric alignment of the captured crop to the rendered master.

Two-stage approach:
    1. Resize captured to master's HxW (the Cropper.js rectangle already gave
       us a tight bounding box, so a stretch is a fine starting point).
    2. Refine with ECC (Enhanced Correlation Coefficient) on grayscale.
       ECC tolerates lighting differences and is more robust than ORB feature
       matching for textured / photographic label artwork.

If ECC fails to converge we fall back to the bare resize and report it in
``align_info`` so the front-end can warn the operator.
"""

from __future__ import annotations

from typing import Tuple

import cv2
import numpy as np


def _ecc_refine(master_gray: np.ndarray, captured_gray: np.ndarray,
                warp_mode: int = cv2.MOTION_AFFINE,
                iters: int = 200, eps: float = 1e-4) -> Tuple[np.ndarray, bool]:
    """Try to refine alignment with ECC. Returns (warp_matrix, ok)."""
    warp_matrix = np.eye(2 if warp_mode != cv2.MOTION_HOMOGRAPHY else 3,
                         3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, iters, eps)
    try:
        cv2.findTransformECC(
            templateImage=master_gray,
            inputImage=captured_gray,
            warpMatrix=warp_matrix,
            motionType=warp_mode,
            criteria=criteria,
            inputMask=None,
            gaussFiltSize=5,
        )
        return warp_matrix, True
    except cv2.error:
        return warp_matrix, False


def align(master_rgb: np.ndarray,
          captured_rgb: np.ndarray) -> Tuple[np.ndarray, dict]:
    """
    Returns (aligned_rgb, info_dict).

    ``aligned_rgb`` has the same shape as ``master_rgb``.
    """
    H, W = master_rgb.shape[:2]
    info: dict = {"master_size": [W, H],
                  "captured_size": [captured_rgb.shape[1], captured_rgb.shape[0]]}

    resized = cv2.resize(captured_rgb, (W, H), interpolation=cv2.INTER_AREA)

    master_gray = cv2.cvtColor(master_rgb, cv2.COLOR_RGB2GRAY)
    captured_gray = cv2.cvtColor(resized, cv2.COLOR_RGB2GRAY)

    # Gentle blur helps ECC ignore print stipple / camera noise.
    master_gray = cv2.GaussianBlur(master_gray, (5, 5), 0)
    captured_gray = cv2.GaussianBlur(captured_gray, (5, 5), 0)

    warp_matrix, ok = _ecc_refine(master_gray, captured_gray)
    if ok:
        aligned = cv2.warpAffine(
            resized, warp_matrix, (W, H),
            flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_REPLICATE,
        )
        info["method"] = "resize+ECC_AFFINE"
        info["ok"] = True
    else:
        aligned = resized
        info["method"] = "resize_only"
        info["ok"] = False
        info["reason"] = "ECC failed to converge (try better crop or lighting)"

    return aligned, info
