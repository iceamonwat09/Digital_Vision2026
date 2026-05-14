"""
N8N webhook OCR client.

POSTs a cropped label image to an N8N workflow that fronts a real OCR
engine (currently Gemini 2.5 Flash on Vertex AI). Returns the same dict
schema that ``vertex_client.ocr_image`` advertises so the rest of the
label pipeline does not need to know which backend produced the text.

Response contract expected from N8N:

    {
      "text": "<all text, newline-separated>",
      "blocks": [                                     # optional
        {"text": "...", "bbox": [x, y, w, h], "conf": 0.9},
        ...
      ],
      "engine": "gemini-2.5-flash"                    # optional
    }

When ``blocks`` is absent, downstream code still gets a usable ``text``
field — only the bbox-driven stages (Phase B+) will degrade. When the
webhook fails, an error-flagged stub dict is returned so the pipeline
finishes instead of raising.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)


def is_enabled(url: Optional[str] = None) -> bool:
    return bool((url if url is not None else config.N8N_OCR_WEBHOOK_URL).strip())


def _coerce_bbox(raw) -> Optional[list]:
    """Accept [x, y, w, h] either as a list of 4 numbers or None."""
    if not raw or not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        return [float(v) for v in raw]
    except (TypeError, ValueError):
        return None


def _normalize_blocks(raw_blocks) -> list:
    if not isinstance(raw_blocks, list):
        return []
    out = []
    for b in raw_blocks:
        if not isinstance(b, dict):
            continue
        text = str(b.get("text", "")).strip()
        if not text:
            continue
        out.append({
            "text": text,
            "bbox": _coerce_bbox(b.get("bbox")),
            "conf": float(b.get("conf", 0.0) or 0.0),
        })
    return out


def ocr_image(image_bytes: bytes,
              url: Optional[str] = None,
              timeout: Optional[float] = None) -> dict:
    """
    Send ``image_bytes`` (JPEG/PNG-encoded) to the N8N webhook.

    Returns a dict with keys ``text``, ``blocks``, ``stub``, ``engine``,
    and (on failure) ``error``. Never raises.
    """
    target = (url if url is not None else config.N8N_OCR_WEBHOOK_URL).strip()
    if not target:
        return {
            "text": "",
            "blocks": [],
            "stub": True,
            "engine": "n8n",
            "error": "N8N_OCR_WEBHOOK_URL is empty",
        }
    if not image_bytes:
        return {
            "text": "",
            "blocks": [],
            "stub": True,
            "engine": "n8n",
            "error": "empty image bytes",
        }

    files = {"image": ("crop.jpg", image_bytes, "image/jpeg")}
    t = float(timeout if timeout is not None else config.N8N_OCR_TIMEOUT_S)

    size_kb = len(image_bytes) / 1024.0
    print(f"[N8N→OCR] POST {target}")
    print(f"[N8N→OCR]   field 'image' = crop.jpg ({size_kb:.1f} KB, image/jpeg)")
    print(f"[N8N→OCR]   timeout = {t}s")
    logger.info("OCR request: %s  image=%.1fKB  timeout=%.0fs",
                target, size_kb, t)

    try:
        resp = requests.post(target, files=files, timeout=t)
        resp.raise_for_status()
        print(f"[N8N→OCR] ← HTTP {resp.status_code}  ({len(resp.content)} bytes)")
    except requests.RequestException as e:
        print(f"[N8N→OCR] ✗ request failed: {e}")
        return {
            "text": "",
            "blocks": [],
            "stub": True,
            "engine": "n8n",
            "error": f"webhook request failed: {e}",
        }

    # Workflow may return either a JSON body, or a JSON string nested
    # inside a {"data": "..."} envelope from Gemini. Be permissive.
    try:
        payload = resp.json()
    except ValueError:
        return {
            "text": resp.text or "",
            "blocks": [],
            "stub": False,
            "engine": "n8n",
            "warning": "response was not JSON; using raw text",
        }

    if isinstance(payload, list) and payload:
        payload = payload[0]

    # Gemini sometimes wraps its JSON answer in a string field.
    if isinstance(payload, dict) and "text" not in payload and "blocks" not in payload:
        for key in ("data", "result", "output", "response", "content"):
            inner = payload.get(key)
            if isinstance(inner, str):
                try:
                    inner_json = json.loads(inner)
                    if isinstance(inner_json, dict):
                        payload = inner_json
                        break
                except ValueError:
                    continue
            elif isinstance(inner, dict):
                payload = inner
                break

    text = str(payload.get("text", "") or "") if isinstance(payload, dict) else ""
    blocks = _normalize_blocks(payload.get("blocks")) if isinstance(payload, dict) else []
    engine = (
        str(payload.get("engine", "n8n")).strip()
        if isinstance(payload, dict) and payload.get("engine")
        else "n8n"
    )

    return {
        "text": text,
        "blocks": blocks,
        "stub": False,
        "engine": engine,
    }
