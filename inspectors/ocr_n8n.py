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

import base64
import json
import logging
import os
import re
import time
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


_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?\s*```\s*$", re.S)


def _strip_fence(s: str) -> str:
    """ถอดรั้ว markdown ```json ... ``` ที่ LLM ชอบครอบคำตอบมา.

    ทำไมสำคัญ: Gemini คืน ```json{...}``` บ่อยมาก. เดิม ``resp.json()``
    พังกับรูปแบบนี้ แล้วโค้ดเอา **ทั้งสตริงรวมรั้ว** ไปใช้เป็น "ข้อความที่
    OCR อ่านได้" ⇒ คำว่า ``json`` / ``text`` / ``blocks`` และวงเล็บปีกกา
    หลุดเข้าไปเทียบใน MISMATCH/SPELL = "ตัวอักษรแปลก ๆ ที่ของจริงไม่มี".
    """
    m = _FENCE_RE.match(s or "")
    return m.group(1).strip() if m else (s or "")


def _looks_like_html(body: str, ctype: str = "") -> bool:
    """หน้า error ของ N8N/reverse-proxy — ไม่ใช่ผล OCR แน่นอน.

    เกณฑ์ตั้งให้แคบไว้ก่อน (content-type ระบุชัด หรือขึ้นต้นด้วยแท็ก HTML)
    เพราะการตัดสินผิดว่า "ไม่ใช่ข้อความ" จะทำให้โซนที่อ่านได้จริงกลายเป็น
    UNREADABLE ฟรี ๆ.
    """
    if "html" in (ctype or "").lower():
        return True
    head = (body or "").lstrip()[:200].lower()
    return head.startswith("<!doctype") or head.startswith("<html") or (
        "<body" in head and "<" == head[:1])


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

    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    t = float(timeout if timeout is not None else config.N8N_OCR_TIMEOUT_S)

    size_kb = len(image_bytes) / 1024.0
    print(f"[N8N→OCR] POST {target}")
    print(f"[N8N→OCR]   field 'image_b64' = {size_kb:.1f} KB → {len(image_b64)} chars base64")
    print(f"[N8N→OCR]   timeout = {t}s")
    logger.info("OCR request: %s  image=%.1fKB  timeout=%.0fs",
                target, size_kb, t)

    # ลองซ้ำเฉพาะความล้มเหลว "ชั่วคราว" (ต่อไม่ติด / timeout / 5xx) —
    # ไม่ลองซ้ำกับ 404 (workflow ไม่ active) หรือ 413 (payload ใหญ่ไป)
    # เพราะยิงกี่ครั้งก็ได้ผลเดิม เสียเวลาผู้ตรวจเปล่า.
    tries = max(1, int(config.N8N_OCR_RETRIES) + 1)
    resp = None
    last_err = None
    for attempt in range(tries):
        try:
            resp = requests.post(target, data={"image_b64": image_b64},
                                 timeout=t)
            if resp.status_code >= 500 and attempt < tries - 1:
                last_err = f"HTTP {resp.status_code}"
                raise requests.RequestException(last_err)
            resp.raise_for_status()
            print(f"[N8N→OCR] ← HTTP {resp.status_code}  ({len(resp.content)} bytes)")
            break
        except requests.RequestException as e:
            last_err = e
            retriable = (resp is None or resp.status_code >= 500)
            if attempt < tries - 1 and retriable:
                wait = config.N8N_OCR_RETRY_WAIT_S * (2 ** attempt)
                print(f"[N8N→OCR] ↻ ครั้งที่ {attempt + 1}/{tries} ล้มเหลว "
                      f"({e}) — รอ {wait:.1f}s แล้วลองใหม่")
                time.sleep(wait)
                resp = None
                continue
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
    raw = resp.text or ""
    try:
        payload = resp.json()
    except ValueError:
        # ① LLM ครอบคำตอบด้วยรั้ว markdown — ถอดแล้วลองใหม่ (เคสที่พบบ่อย
        #    ที่สุด และเดิมทำให้ ```json{...}``` ทั้งก้อนกลายเป็น "ข้อความ")
        stripped = _strip_fence(raw)
        payload = None
        if stripped and stripped != raw:
            try:
                payload = json.loads(stripped)
            except ValueError:
                payload = None
        if payload is None:
            ctype = resp.headers.get("Content-Type", "")
            # ② หน้า error ของ N8N/proxy — ห้ามเอาไปเป็นข้อความเด็ดขาด
            if config.N8N_OCR_STRICT_RESPONSE and _looks_like_html(raw, ctype):
                head = " ".join(raw.split())[:120]
                print("[N8N→OCR] ✗ ตอบกลับเป็น HTML ไม่ใช่ผล OCR")
                return {
                    "text": "",
                    "blocks": [],
                    "stub": True,
                    "engine": "n8n",
                    "error": ("N8N ตอบกลับเป็นหน้าเว็บ (HTML) ไม่ใช่ผล OCR "
                              "— ตรวจว่า workflow ถูก Activate และ path ถูก: "
                              + head),
                }
            # ③ ข้อความล้วนอื่น ๆ — ยังใช้เป็นข้อความเหมือนเดิม (เผื่อ
            #    workflow ตั้งให้คืน plain text) แต่ติดธงไว้ให้ผู้ตรวจเห็น
            return {
                "text": raw,
                "blocks": [],
                "stub": False,
                "engine": "n8n",
                "warning": ("N8N ตอบกลับไม่ใช่ JSON — ใช้เนื้อหาดิบเป็น"
                            "ข้อความ ผลตรวจโซนนี้อาจไม่น่าเชื่อถือ"),
            }

    if isinstance(payload, list) and payload:
        payload = payload[0]

    # Gemini sometimes wraps its JSON answer in a string field — และสตริง
    # นั้นก็มักถูกครอบด้วยรั้ว markdown อีกชั้น จึง _strip_fence ก่อนเสมอ
    if isinstance(payload, dict) and "text" not in payload and "blocks" not in payload:
        for key in ("data", "result", "output", "response", "content"):
            inner = payload.get(key)
            if isinstance(inner, str):
                try:
                    inner_json = json.loads(_strip_fence(inner))
                    if isinstance(inner_json, dict):
                        payload = inner_json
                        break
                except ValueError:
                    continue
            elif isinstance(inner, dict):
                payload = inner
                break

    # ชั้นสุดท้าย: บาง workflow ยัด JSON (หรือ JSON ในรั้ว) ไว้ใน "text" เอง
    # ⇒ ถ้าแกะแล้วเจอ dict ที่มี "text" จริง ให้ใช้ตัวใน ไม่งั้นเครื่องหมาย
    # ปีกกา/ชื่อคีย์จะกลายเป็นคำในผลตรวจ
    if isinstance(payload, dict):
        t = str(payload.get("text", "") or "")
        if t.lstrip().startswith(("{", "```")):
            try:
                inner_json = json.loads(_strip_fence(t))
            except ValueError:
                inner_json = None
            if isinstance(inner_json, dict) and "text" in inner_json:
                payload = inner_json

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
