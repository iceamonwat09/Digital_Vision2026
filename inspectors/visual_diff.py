"""
Gemini-powered visual diff between a SKU master and a captured photo.

POSTs *both* images (multipart) to the same N8N webhook used for OCR.
The workflow detects two binaries (``master`` + ``captured``) and
switches its prompt to ask Gemini for a structured list of text
differences. The response is passed through verbatim, plus a
``stub`` flag so the pipeline can fall back gracefully on errors.

Expected JSON response from N8N (schema enforced by the prompt):

    {
      "mode": "visual_diff",
      "differences": [
        {
          "type":           "extra_char | missing_char | misspelled | wrong_word | extra_mark",
          "master_text":    "...",
          "captured_text":  "...",
          "location_hint":  "...",
          "severity":       "minor | warning | critical"
        }
      ],
      "summary": "...",
      "engine":  "gemini-..."
    }
"""

from __future__ import annotations

import os
from typing import List, Optional

import requests

import config


def is_enabled() -> bool:
    """True iff a visual-diff webhook URL is configured."""
    url = (getattr(config, "N8N_VISDIFF_WEBHOOK_URL", "") or "").strip()
    return bool(url)


def _resolve_url() -> str:
    """
    Visual diff reuses the OCR webhook by default — the N8N workflow
    routes on which binaries arrived (``image`` → OCR,
    ``master`` + ``captured`` → visual diff). Allow an explicit
    override via ``N8N_VISDIFF_WEBHOOK_URL`` for the case where the
    user splits the flow later.
    """
    url = (getattr(config, "N8N_VISDIFF_WEBHOOK_URL", "") or "").strip()
    if url:
        return url
    return (getattr(config, "N8N_OCR_WEBHOOK_URL", "") or "").strip()


def _normalize_differences(raw) -> List[dict]:
    if not isinstance(raw, list):
        return []
    out = []
    for d in raw:
        if not isinstance(d, dict):
            continue
        out.append({
            "type":          str(d.get("type", "")).strip() or "unknown",
            "master_text":   str(d.get("master_text", "")),
            "captured_text": str(d.get("captured_text", "")),
            "location_hint": str(d.get("location_hint", "")),
            "severity":      str(d.get("severity", "warning")).strip().lower() or "warning",
        })
    return out


def compare_images(master_bytes: bytes,
                   captured_bytes: bytes,
                   sku_code: str = "",
                   url: Optional[str] = None,
                   timeout: Optional[float] = None) -> dict:
    """
    POST master + captured to the N8N webhook and return a normalised
    dict. Never raises — on any failure, returns a stub dict with an
    ``error`` field so the pipeline can mark the stage as SKIPPED.
    """
    target = (url if url is not None else _resolve_url()).strip()
    if not target:
        return {
            "differences": [],
            "summary": "",
            "stub": True,
            "engine": "visual_diff",
            "error": "N8N webhook URL is empty",
        }
    if not master_bytes or not captured_bytes:
        return {
            "differences": [],
            "summary": "",
            "stub": True,
            "engine": "visual_diff",
            "error": "missing master or captured image bytes",
        }

    files = {
        "master":   ("master.jpg",   master_bytes,   "image/jpeg"),
        "captured": ("captured.jpg", captured_bytes, "image/jpeg"),
    }
    data = {"sku_code": sku_code} if sku_code else {}
    t = float(
        timeout if timeout is not None
        else getattr(config, "N8N_VISDIFF_TIMEOUT_S",
                     getattr(config, "N8N_OCR_TIMEOUT_S", 60.0))
    )

    try:
        resp = requests.post(target, files=files, data=data, timeout=t)
        resp.raise_for_status()
    except requests.RequestException as e:
        return {
            "differences": [],
            "summary": "",
            "stub": True,
            "engine": "visual_diff",
            "error": f"webhook request failed: {e}",
        }

    try:
        payload = resp.json()
    except ValueError:
        return {
            "differences": [],
            "summary": "",
            "stub": True,
            "engine": "visual_diff",
            "error": "response was not JSON",
            "raw": (resp.text or "")[:500],
        }

    if isinstance(payload, list) and payload:
        payload = payload[0]
    if not isinstance(payload, dict):
        return {
            "differences": [],
            "summary": "",
            "stub": True,
            "engine": "visual_diff",
            "error": "unexpected response shape",
        }

    return {
        "differences": _normalize_differences(payload.get("differences")),
        "summary":     str(payload.get("summary", "") or ""),
        "stub":        bool(payload.get("stub", False)),
        "engine":      str(payload.get("engine", "gemini")),
        "error":       str(payload.get("error", "") or ""),
    }
