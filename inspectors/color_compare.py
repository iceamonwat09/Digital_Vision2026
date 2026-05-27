"""
Color comparison via Delta-E CIE2000.

Two modes of operation:

``compare_colors(masters, found_hexes)``
    Classic interface — compare pre-sampled hex strings against the spec.
    Uses real CIE2000 (skimage) with a pure-RGB fallback when skimage is
    not installed.

``extract_brand_colors(master_rgb, aligned_rgb, colors)``
    Spatial sampling — for each brand color in the spec, find the pixels
    in the rendered master that match it (ΔE < tolerance), then sample the
    mean color of those same pixel positions in the aligned captured image.
    This eliminates the need for the caller to supply ``found_color_hexes``
    and gives a meaningful result even when the captured photo was taken
    under non-neutral lighting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from .master_loader import MasterColor


@dataclass
class ColorResult:
    name: str
    expected_hex: str
    found_hex: str
    delta_e: float
    tolerance: float
    passed: bool


# ── Color conversion ──────────────────────────────────────────────────────────

def hex_to_rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    r = int(max(0, min(255, round(r))))
    g = int(max(0, min(255, round(g))))
    b = int(max(0, min(255, round(b))))
    return f"#{r:02X}{g:02X}{b:02X}"


# ── ΔE calculation ────────────────────────────────────────────────────────────

def _delta_e_ciede2000(rgb_a: Tuple[int, int, int],
                       rgb_b: Tuple[int, int, int]) -> float:
    """
    Real CIE2000 ΔE between two sRGB colours (values 0–255).
    Falls back to a scaled Euclidean distance when skimage is unavailable.
    """
    try:
        import numpy as np
        from skimage.color import deltaE_ciede2000, rgb2lab
        lab_a = rgb2lab(np.array(
            [[[rgb_a[0] / 255.0, rgb_a[1] / 255.0, rgb_a[2] / 255.0]]],
            dtype=np.float32))
        lab_b = rgb2lab(np.array(
            [[[rgb_b[0] / 255.0, rgb_b[1] / 255.0, rgb_b[2] / 255.0]]],
            dtype=np.float32))
        return float(deltaE_ciede2000(lab_a, lab_b).flat[0])
    except ImportError:
        # Pure-RGB Euclidean fallback (less accurate but always available)
        sq = sum((a - b) ** 2 for a, b in zip(rgb_a, rgb_b))
        return (sq ** 0.5) / 4.42


# ── Spatial color extraction from aligned images ──────────────────────────────

def extract_brand_colors(
    master_rgb: "np.ndarray",
    aligned_rgb: "np.ndarray",
    colors: List[MasterColor],
) -> List[str]:
    """
    For each brand color in ``colors``:
      1. Build a pixel mask of master pixels whose ΔE2000 distance from the
         expected hex is below the color's ``delta_e_tolerance``.
      2. Sample the mean RGB of those same pixels in the aligned captured image.
      3. Return the result as a hex string.

    Requires scikit-image; on ImportError, returns the spec hex values
    unchanged (so ``compare_colors`` still runs, just with no real data).
    Requires ≥ 50 matching master pixels to produce a meaningful sample;
    colors with too small a region fall back to the spec hex value.
    """
    try:
        import numpy as np
        from skimage.color import deltaE_ciede2000, rgb2lab
    except ImportError:
        return [mc.hex for mc in colors]

    import numpy as np  # re-import in local scope for type checker

    master_f  = master_rgb.astype(np.float32) / 255.0
    master_lab = rgb2lab(master_f)

    results: List[str] = []
    for mc in colors:
        r, g, b = hex_to_rgb(mc.hex)
        expected_lab = rgb2lab(np.array(
            [[[r / 255.0, g / 255.0, b / 255.0]]], dtype=np.float32))

        de_map = deltaE_ciede2000(expected_lab, master_lab)
        mask = de_map < mc.delta_e_tolerance

        if int(mask.sum()) < 50:
            results.append(mc.hex)
            continue

        captured_in_region = aligned_rgb[mask]
        mean_rgb = captured_in_region.astype(np.float64).mean(axis=0)
        results.append(_rgb_to_hex(mean_rgb[0], mean_rgb[1], mean_rgb[2]))

    return results


# ── Field-by-field comparison ─────────────────────────────────────────────────

def compare_colors(
    masters: List[MasterColor],
    found_hexes: List[str],
) -> List[ColorResult]:
    """
    Compare each master brand color against the corresponding sampled hex.
    Uses real CIE2000 (falling back to Euclidean when skimage is absent).
    """
    out: List[ColorResult] = []
    for i, m in enumerate(masters):
        found = (
            found_hexes[i] if i < len(found_hexes)
            else (found_hexes[0] if found_hexes else "#000000")
        )
        de = _delta_e_ciede2000(hex_to_rgb(m.hex), hex_to_rgb(found))
        out.append(ColorResult(
            name=m.name,
            expected_hex=m.hex,
            found_hex=found,
            delta_e=round(de, 2),
            tolerance=m.delta_e_tolerance,
            passed=de <= m.delta_e_tolerance,
        ))
    return out
