"""
Color calibration for captured photos.

Lighting and camera vary; the master rendered from PDF is the
ground truth. Calibration here aligns the captured image's overall
color statistics with the master's so that ΔE2000 reflects *real*
deviations, not just camera color cast.

Algorithms offered (cheap, no ColorChecker required):

    match_to_master(captured, master)
        Per-channel mean matching. If master is mostly blue, captured is
        scaled so its blue dominance matches the master. Skipping the
        scene-neutral assumption is critical for branded labels — they
        intentionally are NOT neutral.

    white_patch_awb(captured, patch_xywh)
        Pull a user-marked white area on the label to (250, 250, 250).
        Strongest correction when the operator can mark a known white.

    gray_world_awb(captured)
        Classic gray-world AWB. Kept for completeness; AVOID when the
        label has a dominant color — it will neutralize that brand color.

Production note:
    With fixed lighting and ``match_to_master``, expect residual ΔE
    bias around 3–8. To push below ΔE ≤ 3 you need an X-Rite
    ColorChecker in frame and a 24-patch CCM solver.
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np


def _robust_channel_means(rgb: np.ndarray, clip_percentile: float = 99.0) -> np.ndarray:
    """Per-channel means ignoring the brightest pixels (specular highlights)."""
    flat = rgb.reshape(-1, 3).astype(np.float32)
    luminance = flat.mean(axis=1)
    cutoff = np.percentile(luminance, clip_percentile)
    keep = luminance < cutoff
    if keep.sum() < 100:
        keep = np.ones(flat.shape[0], dtype=bool)
    return flat[keep].mean(axis=0)


def match_to_master(captured: np.ndarray, master: np.ndarray) -> np.ndarray:
    """Per-channel mean matching toward the master image's stats."""
    captured_means = _robust_channel_means(captured)
    master_means = _robust_channel_means(master)
    scale = master_means / np.maximum(captured_means, 1e-3)
    out = np.clip(captured.astype(np.float32) * scale, 0.0, 255.0).astype(np.uint8)
    return out


def gray_world_awb(captured: np.ndarray) -> np.ndarray:
    """
    Classic gray-world AWB. Neutralizes the global color cast under the
    assumption that the scene averages to gray. Do NOT use on branded
    labels with a single dominant color — see ``match_to_master``.
    """
    means = _robust_channel_means(captured)
    target = means.mean()
    scale = target / np.maximum(means, 1e-3)
    return np.clip(captured.astype(np.float32) * scale, 0.0, 255.0).astype(np.uint8)


def white_patch_awb(captured: np.ndarray,
                    patch_xywh: Tuple[int, int, int, int]) -> np.ndarray:
    """Pull a user-marked white patch to neutral (250, 250, 250)."""
    x, y, w, h = patch_xywh
    patch = captured[y:y + h, x:x + w].reshape(-1, 3).astype(np.float32)
    if patch.size == 0:
        return captured
    means = patch.mean(axis=0)
    scale = 250.0 / np.maximum(means, 1e-3)
    return np.clip(captured.astype(np.float32) * scale, 0.0, 255.0).astype(np.uint8)


def calibrate(captured: np.ndarray,
              master: Optional[np.ndarray] = None,
              white_patch_xywh: Optional[Tuple[int, int, int, int]] = None
              ) -> np.ndarray:
    """
    Pick the best calibration method based on what we have:
        1. explicit white-patch  →  white_patch_awb
        2. master available      →  match_to_master   (recommended default)
        3. neither                →  identity (no calibration)
    Gray-world is intentionally NOT a fallback — it harms branded labels.
    """
    if white_patch_xywh is not None:
        return white_patch_awb(captured, white_patch_xywh)
    if master is not None:
        return match_to_master(captured, master)
    return captured.copy()
