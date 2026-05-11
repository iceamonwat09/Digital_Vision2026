"""
Field-aware text comparison.

Each spec field carries its own ``method`` and ``tolerance``:

  exact         barcodes, expiry, registration numbers — distance must be 0
  levenshtein   marketing copy, ingredient lists       — tolerance >= 0
  regex         pattern checks (e.g. EXP \\d{8})        — must match
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

from .master_loader import FieldSpec


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
    Phase 1 heuristic locator:
      - if ``expected`` is a substring of OCR text, return it
      - else return the OCR line whose length is closest to ``expected``

    Phase 2 will use Document AI bounding boxes / labels for this.
    """
    if not ocr_text:
        return ""
    if spec.expected and spec.expected in ocr_text:
        return spec.expected
    target_len = len(spec.expected)
    best, best_diff = "", 10**9
    for raw_line in ocr_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        diff = abs(len(line) - target_len)
        if diff < best_diff:
            best, best_diff = line, diff
    return best


def compare_field(spec: FieldSpec, ocr_text: str) -> FieldResult:
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

    return FieldResult(
        name=spec.name,
        expected=spec.expected,
        found=found,
        method=spec.method,
        distance=distance,
        passed=passed,
        critical=spec.critical,
        severity=_severity(distance, spec.critical, passed),
    )


def compare_all(fields: List[FieldSpec], ocr_text: str) -> List[FieldResult]:
    return [compare_field(f, ocr_text) for f in fields]


def overall_text_verdict(results: List[FieldResult]) -> str:
    sevs = {r.severity for r in results}
    if "critical" in sevs:
        return "FAIL"
    if "warning" in sevs:
        return "WARN"
    return "PASS"
