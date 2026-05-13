"""
Per-pixel ΔE2000 map and defect clustering.

Why CIE2000?
    ΔE2000 is the current industry-standard perceptual color difference
    metric — small numbers mean "looks the same to the eye", large numbers
    mean "noticeably different". The packaging industry uses ΔE ≤ 3 for
    strict acceptance, ΔE ≤ 6 for general acceptance, > 10 for clear defects.

Pipeline:
    sRGB → Lab → ΔE2000 per pixel
    threshold > tolerance → binary mask
    morphological cleanup → connected components
    each component → bbox + peak ΔE + mean ΔE + dominant master/found color
"""

from __future__ import annotations

from typing import List, Tuple

import cv2
import numpy as np


def compute_delta_e(master_rgb: np.ndarray,
                    captured_rgb: np.ndarray) -> np.ndarray:
    """
    Return per-pixel ΔE2000 (float32, HxW) between two aligned sRGB images.
    Both inputs must be the same shape.
    """
    from skimage.color import deltaE_ciede2000, rgb2lab

    if master_rgb.shape != captured_rgb.shape:
        raise ValueError(
            f"shape mismatch: master {master_rgb.shape} vs captured {captured_rgb.shape}"
        )

    a_lab = rgb2lab(master_rgb.astype(np.float32) / 255.0)
    b_lab = rgb2lab(captured_rgb.astype(np.float32) / 255.0)
    de = deltaE_ciede2000(a_lab, b_lab)
    return de.astype(np.float32)


def _hex(rgb: Tuple[int, int, int]) -> str:
    r, g, b = (int(max(0, min(255, c))) for c in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def cluster_defects(
    de_map: np.ndarray,
    master_rgb: np.ndarray,
    captured_rgb: np.ndarray,
    tolerance: float = 6.0,
    min_area_px: int = 80,
    max_defects: int = 50,
) -> Tuple[List[dict], np.ndarray]:
    """
    Threshold the ΔE map and group failing pixels into connected components.

    Returns ``(defects, mask)`` where each defect is a dict with bbox,
    statistics, and dominant colors taken from master vs captured.
    """
    mask = (de_map > tolerance).astype(np.uint8)

    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)

    n_lbl, lbl, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)

    defects: List[dict] = []
    for i in range(1, n_lbl):
        x, y, w, h, area = (
            int(stats[i, cv2.CC_STAT_LEFT]),
            int(stats[i, cv2.CC_STAT_TOP]),
            int(stats[i, cv2.CC_STAT_WIDTH]),
            int(stats[i, cv2.CC_STAT_HEIGHT]),
            int(stats[i, cv2.CC_STAT_AREA]),
        )
        if area < min_area_px:
            continue

        region = (lbl == i)
        region_de = de_map[region]
        peak = float(region_de.max())
        mean = float(region_de.mean())

        # Dominant colors of master / captured inside the region.
        master_mean = master_rgb[region].mean(axis=0)
        captured_mean = captured_rgb[region].mean(axis=0)

        severity = (
            "critical" if peak > tolerance * 2.0
            else ("warning" if peak > tolerance * 1.25 else "minor")
        )

        defects.append({
            "bbox": [x, y, w, h],
            "area_px": area,
            "peak_de": round(peak, 2),
            "mean_de": round(mean, 2),
            "master_hex": _hex(master_mean),
            "found_hex": _hex(captured_mean),
            "severity": severity,
        })

    defects.sort(key=lambda d: -d["peak_de"])
    return defects[:max_defects], mask


def map_stats(de_map: np.ndarray, tolerance: float) -> dict:
    """Summary metrics for the ΔE map."""
    total = de_map.size
    fail = int((de_map > tolerance).sum())
    return {
        "mean": round(float(de_map.mean()), 3),
        "peak": round(float(de_map.max()), 2),
        "p95":  round(float(np.percentile(de_map, 95)), 2),
        "p99":  round(float(np.percentile(de_map, 99)), 2),
        "pass_rate": round(100.0 * (1.0 - fail / total), 3),
        "fail_pixels": fail,
        "total_pixels": int(total),
        "tolerance": tolerance,
    }
