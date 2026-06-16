"""
Advisory text table + EN translation for the Artwork mode.

This module is deliberately ISOLATED from the verification pipeline:

  * It runs only when the user clicks "แปล/อธิบาย" in the new tab.
  * It NEVER affects the PASS/FAIL verdict — output is read-only help
    for a human reading a foreign-language label.
  * The "คำที่ควรใช้" (suggested word) column comes from the
    DETERMINISTIC engine (pyspellchecker candidates + brand vocabulary +
    cross-panel consensus), NOT from the translation model. This keeps
    the project rule intact: the system never lets an invented word
    decide a verdict. Suggestions are clearly labelled advisory.

Translation itself is the only LLM step here. We send the text that OCR
already produced (never images again) in ONE request per file, so it is
cheap and fast, and cache the result by content hash.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Dict, List, Optional

import requests

from . import checks, config

logger = logging.getLogger(__name__)

_CACHE_NAME = "translation.json"


def is_enabled() -> bool:
    return bool(config.N8N_TRANSLATE_WEBHOOK_URL)


# ── deterministic spell suggestions (advisory only) ───────────────────

def _suggest(word: str, vocab: set, consensus: set) -> List[str]:
    """
    Closest known forms of ``word`` from trusted, deterministic sources.
    Returns [] when nothing close is found — the system never guesses
    beyond its dictionaries and the user's own approved vocabulary.
    """
    lw = word.lower()
    out: List[str] = []

    # 1. a near-identical word that the brand/panel themselves use
    for pool in (consensus, vocab):
        for cand in pool:
            cl = cand.lower()
            if cl == lw or len(cand) < 3:
                continue
            if checks.levenshtein(lw, cl) <= max(1, len(lw) // 4):
                if cand not in out:
                    out.append(cand)

    # 2. dictionary candidates (pyspellchecker, edit-distance based)
    for c in checks._get_spellcheckers():
        try:
            cands = c.candidates(lw) or set()
        except Exception:
            cands = set()
        for cand in sorted(cands):
            if cand.lower() != lw and cand not in out:
                out.append(cand)
        corr = None
        try:
            corr = c.correction(lw)
        except Exception:
            pass
        if corr and corr.lower() != lw and corr not in out:
            out.insert(0, corr)

    return out[:3]


def _consensus_words(zones: List[dict], texts: Dict[str, str]) -> set:
    """Words appearing in 2+ panels of the same group — trusted spellings
    the artwork itself agrees on, usable as suggestions."""
    from collections import Counter
    by_group: Dict[str, Counter] = {}
    for z in zones:
        g = z.get("group") or ""
        if not g or z.get("type") == "ignore":
            continue
        seen = set()
        for w in checks._RE_WORD.findall(texts.get(z["id"], "")):
            if len(w) >= 3:
                seen.add(w)
        c = by_group.setdefault(g, Counter())
        for w in seen:
            c[w] += 1
    out: set = set()
    for c in by_group.values():
        for w, n in c.items():
            if n >= 2:
                out.add(w)
    return out


def _defect_keys_by_zone(defects: Optional[List[dict]]) -> Dict[str, set]:
    """
    Map zone_id → set of normalized line keys that the verification
    pipeline already flagged as WRONG in that zone.

    Only the defect's OWN zone (zone_id) and its ``found`` text are
    marked bad. ref_zone_ids are the CORRECT reference panels — they
    must never be marked as mismatch.
    """
    out: Dict[str, set] = {}
    for d in (defects or []):
        if d.get("class") == "UNREADABLE":
            continue
        zid = d.get("zone_id")
        fk = checks._norm_key(d.get("found", ""))
        if zid and fk:
            out.setdefault(zid, set()).add(fk)
    return out


def build_table(zones: List[dict], ocr_results: List[dict],
                vocab_words: Optional[set] = None,
                defects: Optional[List[dict]] = None) -> List[dict]:
    """
    One row per source line, in zone/reading order:

        {"zone_id", "label", "src", "status", "flagged", "suggest"}

    status: "ok"       — clean line
            "spell"    — word(s) not in any dictionary / vocabulary
            "mismatch" — this line was flagged by the verification verdict
                         (cross-panel / zoom / number / phrase). NEVER ✓.
    flagged: list of suspicious words (to highlight in the UI)
    suggest: {word: [candidate, ...]}  (deterministic, may be empty)
    """
    texts = {r["zone_id"]: r.get("text", "") for r in ocr_results}
    zlabel = {z["id"]: (z.get("label") or z["id"]) for z in zones}
    vocab = {w.lower() for w in (vocab_words or set())}
    consensus = _consensus_words(zones, texts)
    checkers = checks._get_spellcheckers()
    defect_keys = _defect_keys_by_zone(defects)

    rows: List[dict] = []
    for z in zones:
        if z.get("type") == "ignore":
            continue
        zid = z["id"]
        zone_dkeys = defect_keys.get(zid, set())
        for raw in texts.get(zid, "").splitlines():
            line = raw.strip()
            if not line:
                continue
            flagged: List[str] = []
            suggest: Dict[str, List[str]] = {}
            if checkers:
                for w in checks._RE_WORD.findall(line):
                    if len(w) < config.SPELL_MIN_WORD_LEN:
                        continue
                    if any(ch.isdigit() for ch in w):
                        continue
                    lw = w.lower()
                    if lw in vocab:
                        continue
                    if any(c.known([lw]) for c in checkers):
                        continue
                    if w not in flagged:
                        flagged.append(w)
                        s = _suggest(w, vocab, consensus)
                        if s:
                            suggest[w] = s

            # Does the verdict already flag this exact line? (substring
            # both ways so a single flagged word inside a longer line, or
            # a line inside a merged OCR block, still matches.)
            rkey = checks._norm_key(line)
            mismatch = bool(rkey) and any(
                dk and (dk in rkey or rkey in dk) for dk in zone_dkeys)

            if flagged:
                status = "spell"
            elif mismatch:
                status = "mismatch"
            else:
                status = "ok"

            rows.append({
                "zone_id": zid,
                "label": zlabel.get(zid, zid),
                "src": line,
                "status": status,
                "flagged": flagged,
                "mismatch": mismatch,
                "suggest": suggest,
            })
    return rows


# ── translation via N8N (text-only, one request) ──────────────────────

def translate_lines(lines: List[str],
                    url: Optional[str] = None,
                    timeout: Optional[float] = None) -> List[str]:
    """
    Translate ``lines`` to English in one request. Returns a list aligned
    with the input (same length). On any failure returns [] so the caller
    can show source text without translation rather than erroring.
    """
    target = (url if url is not None
              else config.N8N_TRANSLATE_WEBHOOK_URL).strip()
    if not target or not lines:
        return []
    t = float(timeout if timeout is not None
              else config.N8N_TRANSLATE_TIMEOUT_S)
    try:
        resp = requests.post(target, json={"lines": lines}, timeout=t)
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("[artwork] translate webhook failed: %s", e)
        return []

    if isinstance(payload, list) and payload:
        payload = payload[0]
    # tolerate Gemini wrapping its JSON in a string field
    if isinstance(payload, dict) and "translations" not in payload:
        for key in ("data", "result", "output", "response", "content"):
            inner = payload.get(key)
            if isinstance(inner, str):
                try:
                    inner = json.loads(inner)
                except ValueError:
                    continue
            if isinstance(inner, dict) and "translations" in inner:
                payload = inner
                break

    out = payload.get("translations") if isinstance(payload, dict) else None
    if not isinstance(out, list):
        return []
    # align defensively: pad/truncate to match input length
    out = [str(x) if x is not None else "" for x in out]
    if len(out) < len(lines):
        out += [""] * (len(lines) - len(out))
    return out[:len(lines)]


# ── cache (per inspection, keyed by source-text hash) ─────────────────

def _hash_rows(rows: List[dict]) -> str:
    h = hashlib.sha1()
    for r in rows:
        h.update(r["src"].encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def load_cache(insp_dir: str, rows: List[dict]) -> Optional[List[dict]]:
    p = os.path.join(insp_dir, _CACHE_NAME)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError):
        return None
    if data.get("hash") != _hash_rows(rows):
        return None        # source text changed → cache stale
    return data.get("rows")


def save_cache(insp_dir: str, rows: List[dict]) -> None:
    try:
        with open(os.path.join(insp_dir, _CACHE_NAME), "w",
                  encoding="utf-8") as f:
            json.dump({"hash": _hash_rows(rows), "rows": rows},
                      f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning("[artwork] could not cache translation: %s", e)


def translate_table(insp_dir: str, rows: List[dict]) -> dict:
    """
    Attach an ``en`` field to each row, using cache when the source text
    is unchanged. Returns {"rows": [...], "translated": bool, "note": ...}.
    """
    cached = load_cache(insp_dir, rows)
    if cached is not None:
        return {"rows": cached, "translated": True, "cached": True}

    if not is_enabled():
        for r in rows:
            r["en"] = ""
        return {"rows": rows, "translated": False,
                "note": "ยังไม่ได้ตั้งค่า N8N_TRANSLATE_WEBHOOK_URL — "
                        "แสดงข้อความและคำแนะนำการสะกดได้ แต่ยังไม่มีคำแปล"}

    en = translate_lines([r["src"] for r in rows])
    if not en:
        for r in rows:
            r["en"] = ""
        return {"rows": rows, "translated": False,
                "note": "เรียกบริการแปลไม่สำเร็จ — แสดงเฉพาะข้อความต้นฉบับ"}

    for r, t in zip(rows, en):
        r["en"] = t
    save_cache(insp_dir, rows)
    return {"rows": rows, "translated": True}
