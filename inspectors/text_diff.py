"""
Character- and line-level diff helpers for the label inspection report.

The label pipeline uses these to tell the operator *exactly* what
differs between the master artwork text and the OCR output of the
captured photo, instead of only emitting a Levenshtein distance number.

Two shapes are produced:

``char_diff(expected, found)`` returns a list of ops describing the
character-level transformation::

    [
      {"op": "equal",  "text": "ไก่ย่าง"},
      {"op": "delete", "text": "ทอด"},      # only in `expected`
      {"op": "insert", "text": "ผัด"},      # only in `found`
      {"op": "replace", "a": "...", "b": "..."},  # both sides differ
    ]

``line_diff(master_text, captured_text)`` returns a list of unified
line-level ops with the same vocabulary, used for the side-by-side
"Master vs OCR" panel.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import List


def char_diff(expected: str, found: str) -> List[dict]:
    expected = expected or ""
    found = found or ""
    if expected == found:
        return [{"op": "equal", "text": expected}] if expected else []

    sm = SequenceMatcher(a=expected, b=found, autojunk=False)
    out: List[dict] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        a_chunk = expected[i1:i2]
        b_chunk = found[j1:j2]
        if tag == "equal":
            out.append({"op": "equal", "text": a_chunk})
        elif tag == "delete":
            out.append({"op": "delete", "text": a_chunk})
        elif tag == "insert":
            out.append({"op": "insert", "text": b_chunk})
        elif tag == "replace":
            out.append({"op": "replace", "a": a_chunk, "b": b_chunk})
    return out


def line_diff(master_text: str, captured_text: str) -> List[dict]:
    """
    Line-level unified diff.

    Each op is one of::

        {"op": "equal",   "text": "..."}
        {"op": "delete",  "text": "..."}   # only in master
        {"op": "insert",  "text": "..."}   # only in captured (OCR)
        {"op": "replace", "a": "...", "b": "..."}  # both lines, character-diffed
    """
    a_lines = [l for l in (master_text or "").splitlines() if l.strip()]
    b_lines = [l for l in (captured_text or "").splitlines() if l.strip()]

    sm = SequenceMatcher(a=a_lines, b=b_lines, autojunk=False)
    out: List[dict] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for line in a_lines[i1:i2]:
                out.append({"op": "equal", "text": line})
        elif tag == "delete":
            for line in a_lines[i1:i2]:
                out.append({"op": "delete", "text": line})
        elif tag == "insert":
            for line in b_lines[j1:j2]:
                out.append({"op": "insert", "text": line})
        elif tag == "replace":
            # Pair up replaced lines so the UI can show side-by-side char diffs.
            a_chunk = a_lines[i1:i2]
            b_chunk = b_lines[j1:j2]
            paired = min(len(a_chunk), len(b_chunk))
            for k in range(paired):
                out.append({"op": "replace", "a": a_chunk[k], "b": b_chunk[k]})
            for line in a_chunk[paired:]:
                out.append({"op": "delete", "text": line})
            for line in b_chunk[paired:]:
                out.append({"op": "insert", "text": line})
    return out
