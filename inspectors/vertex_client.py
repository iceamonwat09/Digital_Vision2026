"""
OCR + Gemini dispatcher used by the label inspection pipeline.

Originally a Vertex Document AI stub. Now selects an OCR backend based on
``config.OCR_BACKEND`` so other modules can keep importing
``vertex_client.ocr_image`` without caring which engine actually runs.

Backends:
    * ``stub``   — placeholder text, no network. Default when nothing is
                   configured. Used so Can Dent-only deployments don't
                   need any OCR setup.
    * ``n8n``    — POSTs the cropped image to the N8N webhook defined in
                   ``config.N8N_OCR_WEBHOOK_URL``. See ``ocr_n8n``.
    * ``vertex`` — direct Document AI call (not yet implemented).

If ``OCR_BACKEND`` is empty but an N8N webhook URL is configured, the
dispatcher auto-selects ``n8n`` for convenience.
"""

from __future__ import annotations

from typing import List

import config

from . import ocr_n8n


def _resolve_backend() -> str:
    backend = (config.OCR_BACKEND or "").strip().lower()
    if backend:
        return backend
    if ocr_n8n.is_enabled():
        return "n8n"
    return "stub"


def is_enabled() -> bool:
    """True when any real (non-stub) backend is active."""
    return _resolve_backend() != "stub"


def ocr_image(image_bytes: bytes) -> dict:
    """
    Run OCR on a single cropped label image.

    Returns:
        {
          "text":   str,
          "blocks": [{"text":..., "bbox":[x,y,w,h], "conf":0.93}, ...],
          "stub":   bool,
          "engine": str,
        }
    """
    backend = _resolve_backend()

    if backend == "n8n":
        return ocr_n8n.ocr_image(image_bytes)

    if backend == "vertex":
        # Reserved for a future direct Document AI integration.
        return {
            "text": "",
            "blocks": [],
            "stub": True,
            "engine": "vertex",
            "error": "vertex backend not implemented; falling back to stub",
        }

    return {
        "text": (
            "[STUB OCR] No OCR backend configured.\n"
            "Set OCR_BACKEND=n8n and N8N_OCR_WEBHOOK_URL to enable real OCR."
        ),
        "blocks": [],
        "stub": True,
        "engine": "stub",
    }


def gemini_context_check(
    master_text: str,
    ocr_text: str,
    ambiguous_field_names: List[str],
) -> dict:
    """
    Ask Gemini whether borderline differences are acceptable or critical.

    Still stubbed — a dedicated N8N workflow (or direct Vertex call) for
    this step is planned for Phase D. The label pipeline already handles
    a ``skipped`` verdict gracefully, so the rest of the system works.
    """
    return {
        "verdict": "skipped",
        "reason": "Gemini context check not wired up yet.",
        "stub": True,
    }
