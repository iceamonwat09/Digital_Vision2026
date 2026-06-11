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


def auto_white_balance_from_master(
    aligned: np.ndarray,
    master: np.ndarray,
    white_thresh: int = 235,
    min_pixels: int = 50,
    max_gain: float = 1.8,
) -> Tuple[np.ndarray, Optional[dict]]:
    """
    White-balance an *already-aligned* capture using the master's own white
    regions as the reference — an automatic, location-free version of
    white_patch_awb that needs no operator click.

    The master is rendered ground truth, so pixels that are near-white there
    are exactly the spots that should read neutral white in the photo. We
    sample the aligned capture at those positions and apply the per-channel
    gain that pulls them to ~250. Gain is clamped to ``max_gain`` so a tiny
    or mislocated white region can't blow the image out.

    Returns ``(balanced, info)``; ``info`` is None (and the image returned
    unchanged) when there isn't enough white reference to trust.
    """
    if aligned is None or master is None or aligned.shape != master.shape:
        return aligned, None

    mask = master.min(axis=2) >= white_thresh   # near-white in all channels
    n = int(mask.sum())
    if n < min_pixels:
        return aligned, None

    means = aligned[mask].reshape(-1, 3).astype(np.float32).mean(axis=0)
    scale = np.clip(250.0 / np.maximum(means, 1e-3), 1.0 / max_gain, max_gain)
    balanced = np.clip(aligned.astype(np.float32) * scale, 0, 255).astype(np.uint8)
    return balanced, {
        "applied": True,
        "white_pixels": n,
        "gain": [round(float(s), 3) for s in scale],
    }


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
