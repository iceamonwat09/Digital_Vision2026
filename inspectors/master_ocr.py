"""
OCR the rendered master PDF and cache the result as ``master_ocr.json``.

Why OCR the master?
  The PDF text layer is absent on many artwork files (raster PDFs, fonts
  not tagged, Arabic/RTL text not properly extracted by PyMuPDF) and even
  when present the reading order differs from what Gemini sees.  Running
  the same OCR engine on *both* master and captured makes the comparison
  symmetric: same engine, same tokenisation, same RTL/LTR handling — so
  text differences are real differences, not engine artefacts.

Cache policy:
  ``master_ocr.json`` is written next to ``master.pdf``.
  The cached file is reused as long as the PDF has not been modified
  (mtime comparison). Pass ``force_refresh=True`` to bypass the cache,
  e.g. after re-approving a new artwork revision.
"""

from __future__ import annotations

import json
import logging
import os

from . import master_renderer
from . import ocr_n8n

logger = logging.getLogger(__name__)

_CACHE_FILENAME = "master_ocr.json"
_CACHE_VERSION  = 2   # bump when cache schema changes to auto-invalidate old files
_MIN_BLOCKS     = 5   # refuse to cache results sparser than this
_MIN_TEXT_LEN   = 20  # minimum non-whitespace characters in text field


def _is_valid_ocr(result: dict) -> bool:
    """Return True only when the OCR result is rich enough to be worth caching."""
    if result.get("stub") or result.get("parse_error"):
        return False
    if len(result.get("text", "").strip()) < _MIN_TEXT_LEN:
        return False
    if len(result.get("blocks", [])) < _MIN_BLOCKS:
        return False
    return True


# ── Cache helpers ────────────────────────────────────────────────────────────

def _cache_path(pdf_path: str) -> str:
    return os.path.join(os.path.dirname(pdf_path), _CACHE_FILENAME)


def _cache_is_fresh(pdf_path: str, cache_path: str) -> bool:
    """True when the cache file exists and is at least as new as the PDF."""
    if not os.path.isfile(cache_path):
        return False
    return os.path.getmtime(cache_path) >= os.path.getmtime(pdf_path)


def invalidate_cache(pdf_path: str) -> bool:
    """
    Delete the cached OCR result so the next call re-OCRs the master.
    Returns True if a file was deleted, False if nothing was cached.
    """
    cache = _cache_path(pdf_path)
    if os.path.isfile(cache):
        os.remove(cache)
        logger.info("Master OCR cache invalidated: %s", cache)
        return True
    return False


# ── Main public function ─────────────────────────────────────────────────────

def get_master_ocr(pdf_path: str, force_refresh: bool = False) -> dict:
    """
    Return the OCR result for the rendered master PDF, using an on-disk
    cache when fresh.

    Returns a dict with keys:
        text    (str)   — full OCR text, newline-separated
        blocks  (list)  — [{text, bbox:[x,y,w,h], conf}, ...]
        engine  (str)
        cached  (bool)  — True when the result came from cache
        stub    (bool)  — True on any failure
        error   (str)   — set when stub=True

    Never raises.
    """
    if not pdf_path or not os.path.isfile(pdf_path):
        return {
            "text": "", "blocks": [], "engine": "master_ocr",
            "stub": True, "error": "master.pdf not found", "cached": False,
        }

    if not ocr_n8n.is_enabled():
        return {
            "text": "", "blocks": [], "engine": "master_ocr",
            "stub": True, "error": "OCR backend not configured", "cached": False,
        }

    cache = _cache_path(pdf_path)

    # ── Try cache first ──────────────────────────────────────────────────────
    if not force_refresh and _cache_is_fresh(pdf_path, cache):
        try:
            with open(cache, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("_cache_version") != _CACHE_VERSION:
                logger.info("Master OCR cache version mismatch (got %s, want %d) — re-OCR",
                            data.get("_cache_version"), _CACHE_VERSION)
            elif not _is_valid_ocr(data):
                logger.info("Master OCR cache is sparse (blocks=%d, text=%d) — re-OCR",
                            len(data.get("blocks", [])), len(data.get("text", "").strip()))
            else:
                data["cached"] = True
                print(f"[MasterOCR] cache hit → {os.path.basename(cache)}")
                logger.info("Master OCR cache hit: %s", cache)
                return data
        except Exception as e:
            logger.warning("Master OCR cache read failed (%s) — will re-OCR", e)

    # ── Render PDF to JPEG ───────────────────────────────────────────────────
    print(f"[MasterOCR] rendering {os.path.basename(pdf_path)} …")
    try:
        jpeg_bytes = master_renderer.render_master_to_jpeg_bytes(pdf_path)
    except Exception as e:
        logger.error("Master OCR: PDF render failed: %s", e)
        return {
            "text": "", "blocks": [], "engine": "master_ocr",
            "stub": True, "error": f"PDF render failed: {e}", "cached": False,
        }

    # ── OCR ──────────────────────────────────────────────────────────────────
    print(f"[MasterOCR] sending {len(jpeg_bytes) // 1024} KB to OCR …")
    result = ocr_n8n.ocr_image(jpeg_bytes)
    result["cached"] = False

    if result.get("stub"):
        logger.warning("Master OCR returned stub: %s", result.get("error", ""))
        return result

    # ── Persist cache (best-effort) ──────────────────────────────────────────
    n_blocks = len(result.get("blocks", []))
    n_chars  = len(result.get("text", "").strip())
    if not _is_valid_ocr(result):
        logger.warning(
            "Master OCR result too sparse to cache (blocks=%d, text=%d chars) — "
            "using for this run only", n_blocks, n_chars)
        print(f"[MasterOCR] ⚠ result too sparse to cache (blocks={n_blocks}, chars={n_chars})")
    else:
        try:
            payload = {k: v for k, v in result.items() if k != "cached"}
            payload["_cache_version"] = _CACHE_VERSION
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            print(f"[MasterOCR] result cached → {os.path.basename(cache)} "
                  f"(blocks={n_blocks}, chars={n_chars})")
            logger.info("Master OCR cached: %s  text_len=%d  blocks=%d",
                        cache, n_chars, n_blocks)
        except Exception as e:
            logger.warning("Master OCR cache write failed: %s", e)

    return result
