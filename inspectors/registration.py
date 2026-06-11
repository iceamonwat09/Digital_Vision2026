"""
Geometric alignment of the captured crop to the rendered master.

Three-stage pipeline (best available wins, falls back gracefully):

  Stage 1 — ORB feature matching + RANSAC homography
      Handles perspective distortion (camera tilted relative to label).
      Requires ≥ ``_MIN_INLIERS`` inlier keypoint pairs.  When the label
      has few textured regions (very uniform solid colour) ORB may not
      find enough features — Stage 2 is the fallback.

  Stage 2 — ECC (Enhanced Correlation Coefficient), AFFINE motion model
      Sub-pixel refinement of translation / rotation / scale.  Tolerates
      lighting differences well.  Applied after Stage 1 (or on the plain
      resize if Stage 1 failed) to correct residual misalignment.

  Stage 3 — bare resize
      Last resort when both above fail.  Still usable for coarse ΔE maps.

The chosen method is recorded in ``align_info["method"]`` so the front-end
can surface a warning when only the fallback path was taken.
"""

from __future__ import annotations

import logging
from typing import Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ── ORB / homography parameters ───────────────────────────────────────────────
_ORB_FEATURES   = 1000    # keypoints to detect per image
_LOWE_RATIO     = 0.75    # Lowe's ratio test threshold
_RANSAC_THRESH  = 5.0     # reprojection error threshold (pixels)
_MIN_INLIERS    = 10      # minimum RANSAC inliers to accept homography


# ── Stage 1: ORB + RANSAC homography ─────────────────────────────────────────

def _orb_homography(
    master_gray: np.ndarray,
    captured_gray: np.ndarray,
) -> Tuple[np.ndarray | None, int]:
    """
    Detect ORB keypoints, match with Lowe ratio test, estimate homography
    with RANSAC.

    Returns ``(H_3x3, n_inliers)`` where H is None on failure.
    """
    orb = cv2.ORB_create(nfeatures=_ORB_FEATURES)
    kp_m, des_m = orb.detectAndCompute(master_gray, None)
    kp_c, des_c = orb.detectAndCompute(captured_gray, None)

    if des_m is None or des_c is None or len(kp_m) < 4 or len(kp_c) < 4:
        return None, 0

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    raw = matcher.knnMatch(des_m, des_c, k=2)

    good = []
    for pair in raw:
        if len(pair) == 2:
            m_pt, n_pt = pair
            if m_pt.distance < _LOWE_RATIO * n_pt.distance:
                good.append(m_pt)

    if len(good) < _MIN_INLIERS:
        return None, 0

    src_pts = np.float32([kp_m[g.queryIdx].pt for g in good]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp_c[g.trainIdx].pt for g in good]).reshape(-1, 1, 2)

    H, mask = cv2.findHomography(dst_pts, src_pts,
                                  cv2.RANSAC, _RANSAC_THRESH)
    if H is None:
        return None, 0

    n_inliers = int(mask.ravel().sum()) if mask is not None else 0
    if n_inliers < _MIN_INLIERS:
        return None, n_inliers

    return H, n_inliers


# ── Stage 2: ECC affine refinement ───────────────────────────────────────────

def _ecc_refine(
    master_gray: np.ndarray,
    captured_gray: np.ndarray,
    warp_mode: int = cv2.MOTION_AFFINE,
    iters: int = 200,
    eps: float = 1e-4,
) -> Tuple[np.ndarray, bool]:
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


# ── Public entry point ────────────────────────────────────────────────────────

def align(
    master_rgb: np.ndarray,
    captured_rgb: np.ndarray,
) -> Tuple[np.ndarray, dict]:
    """
    Align ``captured_rgb`` to ``master_rgb`` using the best available method.

    Returns ``(aligned_rgb, info_dict)`` where ``aligned_rgb`` has the same
    shape as ``master_rgb``.

    ``info_dict`` keys:
        method        str   — "resize+ORB_H+ECC" | "resize+ORB_H" |
                              "resize+ECC_AFFINE" | "resize_only"
        ok            bool
        orb_inliers   int   — 0 when ORB not attempted or failed
        reason        str   — set when ok=False
    """
    H_img, W_img = master_rgb.shape[:2]
    info: dict = {
        "master_size":   [W_img, H_img],
        "captured_size": [captured_rgb.shape[1], captured_rgb.shape[0]],
        "orb_inliers":   0,
    }

    # ── Coarse resize ─────────────────────────────────────────────────────────
    resized = cv2.resize(captured_rgb, (W_img, H_img), interpolation=cv2.INTER_AREA)

    master_gray   = cv2.GaussianBlur(
        cv2.cvtColor(master_rgb, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    captured_gray = cv2.GaussianBlur(
        cv2.cvtColor(resized,    cv2.COLOR_RGB2GRAY), (5, 5), 0)

    current = resized
    method_parts = ["resize"]

    # ── Stage 1: ORB homography ───────────────────────────────────────────────
    H_homog, n_inliers = _orb_homography(master_gray, captured_gray)
    info["orb_inliers"] = n_inliers

    if H_homog is not None:
        current = cv2.warpPerspective(resized, H_homog, (W_img, H_img),
                                      flags=cv2.INTER_LINEAR,
                                      borderMode=cv2.BORDER_REPLICATE)
        method_parts.append(f"ORB_H({n_inliers})")
        # Recompute gray for ECC on the perspective-corrected image
        captured_gray = cv2.GaussianBlur(
            cv2.cvtColor(current, cv2.COLOR_RGB2GRAY), (5, 5), 0)
        logger.debug("ORB homography: %d inliers", n_inliers)
    else:
        logger.debug("ORB homography skipped (inliers=%d)", n_inliers)

    # ── Stage 2: ECC affine refinement ───────────────────────────────────────
    warp_matrix, ecc_ok = _ecc_refine(master_gray, captured_gray)
    if ecc_ok:
        current = cv2.warpAffine(
            current, warp_matrix, (W_img, H_img),
            flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_REPLICATE,
        )
        method_parts.append("ECC")

    info["method"] = "+".join(method_parts)
    info["ok"] = ecc_ok or (H_homog is not None)

    if not info["ok"]:
        info["reason"] = (
            "ORB found too few features and ECC failed to converge — "
            "try better crop, lighting, or reduce label curvature"
        )

    return current, info
