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

# When a word is in no dictionary, pyspellchecker offers every same-edit
# distance word. For a real single-answer typo that is a tiny set
# ("caliddd" -> {calidad}; "Phosphours" -> {phosphorus, phosphors}). For a
# word the dictionary simply cannot place — a loanword/brand ("Guar",
# "Samut") or a typo whose fix needs context ("Cude" -> code/cute/cure/
# crude/rude/... 28 candidates) — it is a large scatter. Showing 3
# arbitrary scatter words is misleading noise next to the real answer, so
# dictionary guesses are surfaced ONLY when the candidate set is small
# enough to be unambiguous; otherwise the reviewer sees just the "not in
# dictionary" flag (and the advisory AI column). Honors the project rule
# that the system never guesses a word it isn't confident about.
_SUGGEST_MAX_DICT_CANDIDATES = 2


def _suggest(word: str, vocab: set, consensus: set) -> List[str]:
    """
    Closest known forms of ``word`` from trusted, deterministic sources.
    Returns [] when nothing close/confident is found — the system never
    guesses beyond its dictionaries and the user's own approved vocabulary,
    and stays silent when the dictionary offers only an ambiguous scatter.
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

    # 2. dictionary candidates — ONLY when unambiguous. Gather the distinct
    #    edit-distance candidates across every enabled language; show them
    #    (ranked by word frequency) only if there are few. A large scatter
    #    means the dictionary can't tell which is right → suggest nothing.
    dict_cands: Dict[str, float] = {}
    for c in checks._get_spellcheckers():
        try:
            cands = c.candidates(lw) or set()
        except Exception:
            cands = set()
        for cand in cands:
            if cand.lower() == lw:
                continue
            try:
                freq = c.word_frequency[cand]
            except Exception:
                freq = 0
            dict_cands[cand] = max(dict_cands.get(cand, 0), freq)
    if 0 < len(dict_cands) <= _SUGGEST_MAX_DICT_CANDIDATES:
        for cand in sorted(dict_cands, key=lambda w: dict_cands[w],
                           reverse=True):
            if cand not in out:
                out.append(cand)

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

_SPELL_KINDS = ("typo", "truncated", "variant")


def _clean_spell() -> dict:
    """Default advisory-spell entry for an unflagged / unknown line."""
    return {"flagged": False, "suggestion": None, "kind": None,
            "reason": None}


def _missing_spell() -> dict:
    """Advisory-spell entry for a line the AI did NOT actually check —
    the model answered with a wrong-length array (alignment broken for
    the whole batch) or the chunk's request failed. The UI must render
    this as "AI ตรวจไม่ครบ", never as "✓ ไม่พบ": a silent False here is
    a lie that hides typos the dict column already caught."""
    return {"flagged": False, "suggestion": None, "kind": None,
            "reason": None, "missing": True}

def translate_lines(lines: List[str],
                    url: Optional[str] = None,
                    timeout: Optional[float] = None) -> Dict[str, list]:
    """
    Translate ``lines`` to English and run the advisory AI spell-check in
    ONE request (same webhook, same Gemini call — see
    n8n_artwork_translate.workflow.json). Returns
    {"translations": [...], "spell": [...], "spell_available": bool}.
    ``translations``/``spell`` are aligned with the input (same length,
    padded/truncated defensively). On any failure returns
    {"translations": [], "spell": [], "spell_available": False} so the
    caller can show source text without translation rather than erroring.

    ``spell_available`` tells the caller whether the N8N workflow has
    actually been updated to return a ``spell`` array at all — distinct
    from "checked and found nothing", which is what an all-False ``spell``
    list with ``spell_available: True`` means. Without this flag the UI
    cannot tell "AI checked, all clean" apart from "AI never ran".

    ``spell`` entries are {"flagged": bool, "suggestion": str|None,
    "kind": "typo"|"truncated"|"variant"|None, "reason": str|None} and
    are PURELY advisory (AI-generated) — they never feed into the
    deterministic PASS/FAIL verdict in checks.py. ``reason`` is a short
    Thai explanation of WHY the line was flagged; ``kind`` separates a
    real typo / cut-off word from a correct regional spelling variant
    (fibre/fiber). Both are None when the N8N workflow predates them.
    """
    empty = {"translations": [], "spell": [], "spell_available": False}
    target = (url if url is not None
              else config.N8N_TRANSLATE_WEBHOOK_URL).strip()
    if not target or not lines:
        return empty
    t = float(timeout if timeout is not None
              else config.N8N_TRANSLATE_TIMEOUT_S)
    try:
        resp = requests.post(target, json={"lines": lines}, timeout=t)
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("[artwork] translate webhook failed: %s", e)
        return empty

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

    if not isinstance(payload, dict):
        return empty

    out = payload.get("translations")
    if not isinstance(out, list):
        out = []
    out = [str(x) if x is not None else "" for x in out]
    if len(out) < len(lines):
        out += [""] * (len(lines) - len(out))
    out = out[:len(lines)]

    spell_raw = payload.get("spell")
    spell_available = isinstance(spell_raw, list)
    spell: List[dict] = []
    if spell_available and len(spell_raw) != len(lines):
        # Wrong-length spell array = the model skipped/merged lines, so
        # POSITIONS are untrustworthy for the whole batch (an entry that
        # "looks aligned" early on may already belong to another line).
        # Mark every line as not-checked instead of padding the tail
        # with a fake "no issue".
        spell = [_missing_spell() for _ in lines]
    elif spell_available:
        for item in spell_raw:
            if isinstance(item, dict):
                kind = item.get("kind")
                reason = item.get("reason")
                reason = str(reason).strip() if reason is not None else ""
                spell.append({
                    "flagged": bool(item.get("flagged")),
                    "suggestion": (str(item["suggestion"])
                                  if item.get("suggestion") is not None
                                  else None),
                    "kind": (kind if kind in _SPELL_KINDS else None),
                    "reason": reason or None,
                })
            else:
                spell.append(_clean_spell())
    if len(spell) < len(lines):
        spell += [_clean_spell() for _ in range(len(lines) - len(spell))]
    spell = spell[:len(lines)]

    return {"translations": out, "spell": spell,
            "spell_available": spell_available}


def translate_lines_chunked(lines: List[str],
                            chunk_size: Optional[int] = None) -> Dict:
    """
    Same contract as ``translate_lines`` plus ``chunks_total`` /
    ``chunks_failed``, but the request is split into chunks of
    ``TRANSLATE_CHUNK_LINES`` lines (default 30, env-tunable;
    0 = single request = the pre-chunking behavior, kept as a rollback
    knob). Long lists (two-file compare ≈ 140 lines) made Gemini return
    misaligned/truncated arrays — the exact per-line accuracy problem
    chunking solves. Chunks run sequentially against the same webhook.

    Failure semantics per chunk: a chunk whose translations come back
    empty contributes "" translations and ``missing`` spell entries for
    its lines (never a fake "no issue"); the caller decides whether to
    cache based on ``chunks_failed``.
    """
    size = (config.TRANSLATE_CHUNK_LINES if chunk_size is None
            else int(chunk_size))
    if size <= 0 or len(lines) <= size:
        r = translate_lines(lines)
        ok = any(t.strip() for t in r["translations"])
        return {**r, "chunks_total": 1, "chunks_failed": 0 if ok else 1}

    translations: List[str] = []
    spell: List[dict] = []
    avail_flags: List[bool] = []
    total = failed = 0
    for i in range(0, len(lines), size):
        chunk = lines[i:i + size]
        total += 1
        r = translate_lines(chunk)
        if not any(t.strip() for t in r["translations"]):
            failed += 1
            translations += [""] * len(chunk)
            spell += [_missing_spell() for _ in chunk]
            continue
        translations += r["translations"]
        spell += r["spell"]
        avail_flags.append(r["spell_available"])
    return {
        "translations": translations,
        "spell": spell,
        # AI column is "available" only when every SUCCESSFUL chunk
        # actually returned a spell array (failed chunks show per-row
        # "ตรวจไม่ครบ" regardless).
        "spell_available": bool(avail_flags) and all(avail_flags),
        "chunks_total": total,
        "chunks_failed": failed,
    }


# ── cache (per inspection, keyed by source-text hash) ─────────────────

def _hash_rows(rows: List[dict]) -> str:
    h = hashlib.sha1()
    for r in rows:
        h.update(r["src"].encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def load_cache(insp_dir: str, rows: List[dict]) -> Optional[dict]:
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
    return {"rows": data.get("rows"),
            "spell_available": bool(data.get("spell_available"))}


def save_cache(insp_dir: str, rows: List[dict],
               spell_available: bool = False) -> None:
    try:
        with open(os.path.join(insp_dir, _CACHE_NAME), "w",
                  encoding="utf-8") as f:
            json.dump({"hash": _hash_rows(rows), "rows": rows,
                      "spell_available": spell_available},
                      f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning("[artwork] could not cache translation: %s", e)


def translate_table(insp_dir: str, rows: List[dict]) -> dict:
    """
    Attach an ``en`` field and an advisory ``ai_spell`` field
    ({"flagged": bool, "suggestion": str|None, "kind": str|None,
    "reason": str|None}) to each row, using cache
    when the source text is unchanged. ``ai_spell`` is purely informational
    — it never affects the deterministic ``status``/verdict already set
    by ``build_table``. Returns
    {"rows": [...], "translated": bool, "ai_spell_available": bool,
     "note": ...}. ``ai_spell_available`` tells the UI whether the N8N
    workflow actually returned a ``spell`` array (vs. the AI check never
    having run at all — both look like "no issues" otherwise).
    """
    cached = load_cache(insp_dir, rows)
    # A cache saved while every "en" came back blank (N8N glitch) is not a
    # real translation — ignore it and retranslate instead of repeating "—"
    # forever just because the source-text hash still matches.
    if cached is not None and any(
            (r.get("en") or "").strip() for r in cached["rows"]):
        return {"rows": cached["rows"], "translated": True, "cached": True,
                "ai_spell_available": cached["spell_available"]}

    if not is_enabled():
        for r in rows:
            r["en"] = ""
            r["ai_spell"] = _clean_spell()
        return {"rows": rows, "translated": False, "ai_spell_available": False,
                "note": "ยังไม่ได้ตั้งค่า N8N_TRANSLATE_WEBHOOK_URL — "
                        "แสดงข้อความและคำแนะนำการสะกดได้ แต่ยังไม่มีคำแปล"}

    result = translate_lines_chunked([r["src"] for r in rows])
    en = result["translations"]
    spell = result["spell"]
    spell_available = result["spell_available"]
    # A same-length list of all-blank strings is still a failure (every
    # line should get *some* translation, even unchanged loanwords/codes)
    # — treat it like an empty list so we never cache it as "translated".
    if not en or not any(t.strip() for t in en):
        for r in rows:
            r["en"] = ""
            r["ai_spell"] = _clean_spell()
        return {"rows": rows, "translated": False, "ai_spell_available": False,
                "note": "เรียกบริการแปลไม่สำเร็จ — แสดงเฉพาะข้อความต้นฉบับ"}

    for r, t, sp in zip(rows, en, spell):
        r["en"] = t
        r["ai_spell"] = sp

    failed = result["chunks_failed"]
    total = result["chunks_total"]
    has_missing = any((r.get("ai_spell") or {}).get("missing") for r in rows)
    # Cache only a COMPLETE, fully-aligned result. A partial one (failed
    # chunk / misaligned spell) must stay uncached so pressing แปล again
    # re-fetches instead of freezing blanks/"ตรวจไม่ครบ" forever.
    if failed == 0 and not has_missing:
        save_cache(insp_dir, rows, spell_available=spell_available)

    if failed > 0:
        note = (f"แปลสำเร็จ {total - failed}/{total} ก้อน — "
                "บรรทัดที่คำแปลยังว่างให้กดแปลอีกครั้งเพื่อเติมส่วนที่ขาด")
    elif not spell_available:
        note = ("N8N ยังไม่คืนข้อมูล spell-check — คอลัมน์ AI ยังไม่ทำงาน "
                "(ต้องอัปเดต workflow artwork-translate ก่อน)")
    else:
        note = None
    out = {"rows": rows, "translated": True,
          "ai_spell_available": spell_available}
    if note:
        out["note"] = note
    return out
