"""
Field-aware text comparison.

Each spec field carries its own ``method`` and ``tolerance``:

  exact         barcodes, expiry, registration numbers — distance must be 0
  levenshtein   marketing copy, ingredient lists       — tolerance >= 0
  regex         pattern checks (e.g. EXP \\d{8})        — must match
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from .master_loader import FieldSpec
from .text_diff import char_diff


@dataclass
class FieldResult:
    name: str
    expected: str
    found: str
    method: str
    distance: int
    passed: bool
    critical: bool
    severity: str   # "ok" | "minor" | "warning" | "critical"
    diff: List[dict] = field(default_factory=list)  # char-level ops


def _levenshtein(a: str, b: str) -> int:
    try:
        import Levenshtein
        return Levenshtein.distance(a, b)
    except ImportError:
        if a == b:
            return 0
        if not a:
            return len(b)
        if not b:
            return len(a)
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                cur.append(min(
                    prev[j] + 1,
                    cur[j - 1] + 1,
                    prev[j - 1] + (ca != cb),
                ))
            prev = cur
        return prev[-1]


def _severity(distance: int, critical: bool, passed: bool) -> str:
    if passed:
        return "ok"
    if critical:
        return "critical"
    if distance <= 2:
        return "minor"
    if distance <= 5:
        return "warning"
    return "critical"


def _find_candidate(spec: FieldSpec, ocr_text: str) -> str:
    """
    Method-aware candidate locator.

    regex       → return the first line (or substring) that satisfies the
                  pattern; empty string when nothing matches.
    exact       → scan every line for an exact match first, then fall back
                  to the line with minimum Levenshtein distance.
    levenshtein → return the line with minimum Levenshtein distance.

    Phase 2 will use Document AI bounding boxes / labels for this.
    """
    if not ocr_text:
        return ""

    method = spec.method.lower()
    lines = [l.strip() for l in ocr_text.splitlines() if l.strip()]

    if method == "regex":
        for line in lines:
            if re.search(spec.expected, line):
                return line
        # Try the whole block as a single string (multi-line patterns)
        m = re.search(spec.expected, ocr_text)
        return m.group(0) if m else ""

    if method == "exact":
        # Exact line match
        for line in lines:
            if line == spec.expected:
                return line
        # Expected appears as a substring of one line
        for line in lines:
            if spec.expected in line:
                return spec.expected
        # Fall through to Levenshtein-closest

    # levenshtein (and exact fallback): pick the line closest in edit distance
    if not lines:
        return ""
    best = min(lines, key=lambda l: _levenshtein(spec.expected, l))
    return best


def compare_field(spec: FieldSpec,
                  ocr_text: str,
                  master_blocks: Optional[List[dict]] = None,
                  captured_blocks: Optional[List[dict]] = None,
                  master_img_w: int = 1,
                  master_img_h: int = 1,
                  captured_img_w: int = 1,
                  captured_img_h: int = 1) -> FieldResult:
    """
    Compare a single field spec against OCR text.

    When ``master_blocks`` and ``captured_blocks`` are supplied and both
    contain bbox coordinates, uses spatial block matching (Phase 2) to
    locate the field before falling back to the flat-text heuristic.
    """
    # Spatial block matching (Phase 2) — only when both sides have bbox data
    found: Optional[str] = None
    has_spatial = (
        master_blocks and captured_blocks
        and any(b.get("bbox") for b in master_blocks)
        and any(b.get("bbox") for b in captured_blocks)
    )
    if has_spatial:
        from . import block_match as _bm
        found = _bm.find_field_candidate(
            spec.expected, spec.method,
            master_blocks, captured_blocks,
            master_img_w, master_img_h,
            captured_img_w, captured_img_h,
        )
    if found is None:
        found = _find_candidate(spec, ocr_text)

    method = spec.method.lower()

    if method == "exact":
        passed = (found == spec.expected)
        distance = 0 if passed else max(len(spec.expected), len(found), 1)
    elif method == "levenshtein":
        distance = _levenshtein(spec.expected, found)
        passed = distance <= spec.tolerance
    elif method == "regex":
        passed = re.search(spec.expected, found) is not None
        distance = 0 if passed else 999
    else:
        passed = False
        distance = 999

    # Build a char-level diff only when it carries information — regex
    # results have no meaningful expected/found character mapping, and
    # passing fields don't need a per-character breakdown.
    diff_ops: List[dict] = []
    if method != "regex" and not passed:
        diff_ops = char_diff(spec.expected, found)

    return FieldResult(
        name=spec.name,
        expected=spec.expected,
        found=found,
        method=spec.method,
        distance=distance,
        passed=passed,
        critical=spec.critical,
        severity=_severity(distance, spec.critical, passed),
        diff=diff_ops,
    )


def compare_all(fields: List[FieldSpec],
                ocr_text: str,
                master_blocks: Optional[List[dict]] = None,
                captured_blocks: Optional[List[dict]] = None,
                master_img_w: int = 1,
                master_img_h: int = 1,
                captured_img_w: int = 1,
                captured_img_h: int = 1) -> List[FieldResult]:
    return [
        compare_field(
            f, ocr_text,
            master_blocks=master_blocks,
            captured_blocks=captured_blocks,
            master_img_w=master_img_w,
            master_img_h=master_img_h,
            captured_img_w=captured_img_w,
            captured_img_h=captured_img_h,
        )
        for f in fields
    ]


def overall_text_verdict(results: List[FieldResult]) -> str:
    sevs = {r.severity for r in results}
    if "critical" in sevs:
        return "FAIL"
    if "warning" in sevs:
        return "WARN"
    return "PASS"
