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

import base64
import json
import logging
import os
from typing import List, Optional

import requests

import config

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    """True iff any webhook URL is available (dedicated or OCR fallback)."""
    return bool(_resolve_url())


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
                   timeout: Optional[float] = None,
                   block_diff: Optional[dict] = None) -> dict:
    """
    POST master + captured to the N8N webhook and return a normalised
    dict. Never raises — on any failure, returns a stub dict with an
    ``error`` field so the pipeline can mark the stage as SKIPPED.

    ``block_diff`` (optional) is the compact summary produced by
    ``block_match.diff_summary``.  When provided, it is serialised as
    ``block_diff_json`` in the POST body so the N8N workflow can inject it
    into the Gemini prompt as grounding context.

    N8N workflow update required to use this field:
        In the "Build Gemini" Code node, add:
            const ctx = body.block_diff_json;
            if (ctx) {
              prompt += "\\n\\nLocal OCR pre-analysis:\\n" + ctx +
                        "\\nPlease confirm these findings and identify " +
                        "additional visual/graphical differences.";
            }
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

    master_b64   = base64.b64encode(master_bytes).decode("ascii")
    captured_b64 = base64.b64encode(captured_bytes).decode("ascii")
    t = float(
        timeout if timeout is not None
        else getattr(config, "N8N_VISDIFF_TIMEOUT_S",
                     getattr(config, "N8N_OCR_TIMEOUT_S", 60.0))
    )

    master_kb = len(master_bytes) / 1024.0
    captured_kb = len(captured_bytes) / 1024.0
    print(f"[N8N→VisualDiff] POST {target}")
    print(f"[N8N→VisualDiff]   field 'master_b64'   = {master_kb:.1f} KB → {len(master_b64)} chars base64")
    print(f"[N8N→VisualDiff]   field 'captured_b64' = {captured_kb:.1f} KB → {len(captured_b64)} chars base64")
    if sku_code:
        print(f"[N8N→VisualDiff]   field 'sku_code' = {sku_code}")
    print(f"[N8N→VisualDiff]   timeout = {t}s")
    logger.info("VisualDiff request: %s  master=%.1fKB  captured=%.1fKB  sku=%s  timeout=%.0fs",
                target, master_kb, captured_kb, sku_code or "-", t)

    data = {
        "master_b64":   master_b64,
        "captured_b64": captured_b64,
    }
    if sku_code:
        data["sku_code"] = sku_code
    if block_diff:
        try:
            data["block_diff_json"] = json.dumps(block_diff, ensure_ascii=False)
            print(f"[N8N→VisualDiff]   field 'block_diff_json' = "
                  f"m:{block_diff.get('missing_count',0)} missing, "
                  f"e:{block_diff.get('extra_count',0)} extra, "
                  f"s:{len(block_diff.get('suspect_matches',[]))} suspect")
        except (TypeError, ValueError):
            pass

    try:
        resp = requests.post(target, data=data, timeout=t)
        resp.raise_for_status()
        print(f"[N8N→VisualDiff] ← HTTP {resp.status_code}  ({len(resp.content)} bytes)")
    except requests.RequestException as e:
        print(f"[N8N→VisualDiff] ✗ request failed: {e}")
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
