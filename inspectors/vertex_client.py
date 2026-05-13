"""
Wrappers around Vertex AI services.

Phase 1 is **stubbed**: no Vertex SDK is imported, no network calls are
made, and ``is_enabled()`` returns False until ``VERTEX_ENABLED=true`` is
set in the environment AND the real implementations are dropped in.

This keeps the rest of the pipeline runnable on a developer's laptop with
zero credentials.
"""

from __future__ import annotations

import os
from typing import List


def is_enabled() -> bool:
    return os.getenv("VERTEX_ENABLED", "false").strip().lower() == "true"


def ocr_image(image_bytes: bytes) -> dict:
    """
    Run Document AI OCR on a single cropped label image.

    Returns:
        {
          "text": str,
          "blocks": [{"text":..., "bbox":[x,y,w,h], "conf":0.93}, ...],
          "stub":  bool
        }
    """
    if not is_enabled():
        return {
            "text": (
                "[STUB OCR] Vertex Document AI is disabled.\n"
                "Set VERTEX_ENABLED=true and wire credentials to run real OCR."
            ),
            "blocks": [],
            "stub": True,
        }
    raise NotImplementedError("Document AI call not implemented in Phase 1")


def gemini_context_check(
    master_text: str,
    ocr_text: str,
    ambiguous_field_names: List[str],
) -> dict:
    """Ask Gemini whether borderline differences are acceptable or critical."""
    if not is_enabled():
        return {
            "verdict": "skipped",
            "reason": "VERTEX_ENABLED=false; Gemini context check skipped.",
            "stub": True,
        }
    raise NotImplementedError("Gemini call not implemented in Phase 1")
