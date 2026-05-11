"""
End-to-end inspection orchestrator:

    cropped_image_bytes + master  →  InspectionReport

Stages:
    1. OCR  (Vertex Document AI — stubbed in Phase 1)
    2. Color compare  (Delta E)
    3. Field-aware text compare  (exact / Levenshtein / regex)
    4. Gemini context check  (only for ambiguous fields)
    5. Reduce to PASS / WARN / FAIL
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import List, Optional

from . import vertex_client
from .color_compare import compare_colors
from .master_loader import Master
from .text_compare import compare_all, overall_text_verdict


@dataclass
class InspectionReport:
    sku_code: str
    verdict: str                      # "PASS" | "WARN" | "FAIL"
    field_results: List[dict] = field(default_factory=list)
    color_results: List[dict] = field(default_factory=list)
    ocr_text: str = ""
    gemini: dict = field(default_factory=dict)
    stub_mode: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def inspect(
    master: Master,
    cropped_image_bytes: bytes,
    found_color_hexes: Optional[List[str]] = None,
) -> InspectionReport:
    ocr = vertex_client.ocr_image(cropped_image_bytes)
    ocr_text = ocr.get("text", "")

    color_results = compare_colors(master.colors, found_color_hexes or [])
    field_results = compare_all(master.fields, ocr_text)

    ambiguous = [r.name for r in field_results if r.severity in ("minor", "warning")]
    gemini = (
        {"verdict": "not_needed"}
        if not ambiguous
        else vertex_client.gemini_context_check(master.raw_text, ocr_text, ambiguous)
    )

    text_verdict = overall_text_verdict(field_results)
    color_fail = any(not c.passed for c in color_results)
    if text_verdict == "FAIL" or color_fail:
        verdict = "FAIL"
    elif text_verdict == "WARN":
        verdict = "WARN"
    else:
        verdict = "PASS"

    return InspectionReport(
        sku_code=master.sku_code,
        verdict=verdict,
        field_results=[r.__dict__ for r in field_results],
        color_results=[c.__dict__ for c in color_results],
        ocr_text=ocr_text,
        gemini=gemini,
        stub_mode=bool(ocr.get("stub", False)),
    )
