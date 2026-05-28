"""
Field-aware text comparison.

Each spec field carries its own ``method``, ``tolerance``, and optional
enhancement hints:

  method
    exact         barcodes, expiry, registration numbers — distance must be 0
    levenshtein   marketing copy, ingredient lists       — tolerance >= 0
    regex         pattern checks (e.g. EXP \\d{8})        — must match

  anchor        label text that precedes the value in the OCR output
                (e.g. "Net Weight", "Ingredients").  When present, the
                candidate search first finds the anchor line then extracts
                the value from that context — much more accurate than global
                Levenshtein-closest for short or ambiguous values.

  value_regex   regex applied to the anchor line (and its next 2 lines) to
                extract an exact value string.  group(0) is returned.
                Use lookaheads/lookbehinds for sub-group selection.
                Example: ``(?<=/)\\d+`` extracts the number after ``/``.

  normalize     pre-comparison transform applied to *both* expected and found:
                  "digits"   strip all non-digit characters  (barcodes)
                  "lower"    lower-case + strip
                  "nospace"  collapse all whitespace
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


def _normalize_text(text: str, normalize: str) -> str:
    """Apply the spec-level normalisation rule before comparison."""
    if not normalize or not text:
        return text
    if normalize == "digits":
        return re.sub(r"\D", "", text)
    if normalize == "lower":
        return text.lower().strip()
    if normalize == "nospace":
        return re.sub(r"\s+", "", text)
    return text


def _find_anchor_candidate(spec: FieldSpec, ocr_text: str) -> Optional[str]:
    """
    Anchor-guided candidate extraction.

    1. Scan OCR text for the first line matching ``spec.anchor``.
    2. If ``spec.value_regex`` is set, search for it on the anchor line and
       the following 2 lines; return group(0) of the first match.
    3. Otherwise strip the anchor label from the line (and trailing ':'),
       return the remainder.  If the remainder is empty, return the first
       non-empty line that follows.

    Returns ``None`` when the anchor is absent, so the caller can fall back.
    """
    anchor = getattr(spec, "anchor", "")
    if not anchor or not ocr_text:
        return None

    lines = [l.strip() for l in ocr_text.splitlines() if l.strip()]
    anchor_pat = re.compile(re.escape(anchor), re.IGNORECASE)
    val_re = getattr(spec, "value_regex", "")

    for i, line in enumerate(lines):
        if not anchor_pat.search(line):
            continue

        if val_re:
            # Try anchor line first, then look-ahead up to 2 more lines
            for candidate in [line] + lines[i + 1: i + 3]:
                m = re.search(val_re, candidate)
                if m:
                    return m.group(0)
            # value_regex present but nothing matched — keep scanning for anchor
            continue

        # No value_regex: return text that follows the anchor label on same line
        after = anchor_pat.sub("", line, count=1).strip().lstrip(":").strip()
        if after:
            return after

        # Anchor label only (e.g. "Ingredients:") → value is on the next line
        for j in range(i + 1, min(i + 3, len(lines))):
            nxt = lines[j].strip()
            if nxt:
                return nxt

    return None


def _find_candidate(spec: FieldSpec, ocr_text: str) -> str:
    """
    Method-aware candidate locator (flat-text fallback).

    regex       → return the first line (or substring) that satisfies the
                  pattern; empty string when nothing matches.
    exact       → scan every line for an exact match first, then fall back
                  to substring search, then Levenshtein-closest.
    levenshtein → return the line with minimum Levenshtein distance.
    """
    if not ocr_text:
        return ""

    method = spec.method.lower()
    lines = [l.strip() for l in ocr_text.splitlines() if l.strip()]

    if method == "regex":
        for line in lines:
            if re.search(spec.expected, line):
                return line
        m = re.search(spec.expected, ocr_text)
        return m.group(0) if m else ""

    if method == "exact":
        for line in lines:
            if line == spec.expected:
                return line
        for line in lines:
            if spec.expected in line:
                return spec.expected

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

    Candidate search priority:
      1. Spatial block matching  (when both sides have bbox data)
      2. Anchor-based extraction (spec.anchor defined)
      3. Flat-text heuristic     (method-aware Levenshtein / substring)

    Normalization (spec.normalize) is applied to *both* expected and found
    before the pass/fail comparison, so the raw ``found`` value is preserved
    in the result for display purposes.
    """
    found: Optional[str] = None

    # ── 1. Spatial block matching ────────────────────────────────────────────
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

    # ── 2. Anchor-based extraction ───────────────────────────────────────────
    if found is None:
        found = _find_anchor_candidate(spec, ocr_text)

    # ── 3. Flat-text fallback ────────────────────────────────────────────────
    if found is None:
        found = _find_candidate(spec, ocr_text)

    if found is None:
        found = ""

    # ── Normalise for comparison ─────────────────────────────────────────────
    norm = getattr(spec, "normalize", "")
    expected_cmp = _normalize_text(spec.expected, norm)
    found_cmp    = _normalize_text(found, norm)

    method = spec.method.lower()

    if method == "exact":
        passed   = (found_cmp == expected_cmp)
        distance = 0 if passed else max(len(expected_cmp), len(found_cmp), 1)
    elif method == "levenshtein":
        distance = _levenshtein(expected_cmp, found_cmp)
        passed   = distance <= spec.tolerance
    elif method == "regex":
        passed   = re.search(spec.expected, found_cmp) is not None
        distance = 0 if passed else 999
    else:
        passed   = False
        distance = 999

    # Char-level diff uses the raw (un-normalised) strings for readability
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
