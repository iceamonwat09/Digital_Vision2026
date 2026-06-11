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


def edge_mask(master_rgb: np.ndarray, dilate_px: int = 3) -> np.ndarray:
    """
    Boolean mask of pixels on/near high-contrast edges in the master.

    Sub-pixel misalignment at these edges (text, logos, die-cut lines)
    produces large ΔE that is a registration artefact, not a print defect.
    Dilating the Canny edges by ``dilate_px`` covers the few-pixel halo the
    misalignment smears across.
    """
    gray = cv2.cvtColor(master_rgb, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    if dilate_px > 0:
        k = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * dilate_px + 1, 2 * dilate_px + 1))
        edges = cv2.dilate(edges, k)
    return edges > 0


def specular_mask(rgb: np.ndarray, v_thresh: int = 245,
                  s_thresh: int = 35) -> np.ndarray:
    """
    Boolean mask of specular-glare pixels: blown-out highlights that are
    bright (HSV V high) and washed out (HSV S low). On glossy / UV-coated
    labels these reflect the light source, not the ink, so their ΔE is
    meaningless and must be excluded.
    """
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    v = hsv[..., 2]
    s = hsv[..., 1]
    return (v >= v_thresh) & (s <= s_thresh)


def build_ignore_mask(
    master_rgb: np.ndarray,
    aligned_rgb: np.ndarray,
    ignore_edges: bool = True,
    edge_dilate_px: int = 3,
    ignore_glare: bool = True,
    glare_v_thresh: int = 245,
    glare_s_thresh: int = 35,
) -> Tuple[np.ndarray, dict]:
    """
    Combine edge + glare masks into one boolean "ignore" mask plus a small
    stats dict (fraction of pixels each mask covers). Pixels that are True
    are excluded from ΔE statistics and defect clustering.
    """
    h, w = master_rgb.shape[:2]
    ignore = np.zeros((h, w), dtype=bool)
    stats = {"edge_pct": 0.0, "glare_pct": 0.0, "ignored_pct": 0.0}
    total = float(h * w) or 1.0

    if ignore_edges:
        em = edge_mask(master_rgb, edge_dilate_px)
        stats["edge_pct"] = round(100.0 * float(em.sum()) / total, 2)
        ignore |= em

    if ignore_glare:
        gm = specular_mask(aligned_rgb, glare_v_thresh, glare_s_thresh)
        stats["glare_pct"] = round(100.0 * float(gm.sum()) / total, 2)
        ignore |= gm

    stats["ignored_pct"] = round(100.0 * float(ignore.sum()) / total, 2)
    return ignore, stats


def cluster_defects(
    de_map: np.ndarray,
    master_rgb: np.ndarray,
    captured_rgb: np.ndarray,
    tolerance: float = 6.0,
    min_area_px: int = 80,
    max_defects: int = 50,
    ignore_mask: Tuple[np.ndarray, None] = None,
) -> Tuple[List[dict], np.ndarray]:
    """
    Threshold the ΔE map and group failing pixels into connected components.

    Returns ``(defects, mask)`` where each defect is a dict with bbox,
    statistics, and dominant colors taken from master vs captured.

    ``ignore_mask`` (bool HxW) zeroes out edge/glare pixels before clustering
    so registration halos and specular highlights never form a "defect".
    """
    mask = (de_map > tolerance).astype(np.uint8)
    if ignore_mask is not None:
        mask[ignore_mask] = 0

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


def map_stats(de_map: np.ndarray, tolerance: float,
              ignore_mask: Tuple[np.ndarray, None] = None) -> dict:
    """
    Summary metrics for the ΔE map. When ``ignore_mask`` is given, all
    statistics are computed over the *valid* (non-ignored) pixels only, so
    edge/glare artefacts don't drag pass_rate or peak around.
    """
    if ignore_mask is not None:
        de_valid = de_map[~ignore_mask]
        if de_valid.size == 0:        # everything masked → fall back to full map
            de_valid = de_map.ravel()
    else:
        de_valid = de_map.ravel()

    total = int(de_valid.size)
    fail = int((de_valid > tolerance).sum())
    return {
        "mean": round(float(de_valid.mean()), 3),
        "peak": round(float(de_valid.max()), 2),
        "p95":  round(float(np.percentile(de_valid, 95)), 2),
        "p99":  round(float(np.percentile(de_valid, 99)), 2),
        "pass_rate": round(100.0 * (1.0 - fail / total), 3) if total else 100.0,
        "fail_pixels": fail,
        "total_pixels": total,
        "tolerance": tolerance,
    }
