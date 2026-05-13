"""
Color comparison via Delta E (CIE2000).

Phase 1 ships a perceptual-Euclidean placeholder so the UI is wired up
end-to-end. The real CIE2000 implementation (with ``colormath`` or
``colour-science``) lands in Phase 2 once an X-Rite color card or
controlled lighting box is added to the capture pipeline.
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


def hex_to_rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def delta_e_placeholder(rgb_a, rgb_b) -> float:
    """
    Perceptual-ish Euclidean distance in RGB, scaled so ~max ≈ 100.
    Replace with CIE2000 in Phase 2.
    """
    sq = sum((a - b) ** 2 for a, b in zip(rgb_a, rgb_b))
    return (sq ** 0.5) / 4.42


def compare_colors(
    masters: List[MasterColor],
    found_hexes: List[str],
) -> List[ColorResult]:
    out: List[ColorResult] = []
    for i, m in enumerate(masters):
        found = found_hexes[i] if i < len(found_hexes) else (
            found_hexes[0] if found_hexes else "#000000"
        )
        de = delta_e_placeholder(hex_to_rgb(m.hex), hex_to_rgb(found))
        out.append(ColorResult(
            name=m.name,
            expected_hex=m.hex,
            found_hex=found,
            delta_e=round(de, 2),
            tolerance=m.delta_e_tolerance,
            passed=de <= m.delta_e_tolerance,
        ))
    return out
