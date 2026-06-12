"""
All artwork verification layers. Pure functions — unit-testable without
Flask, OCR or the filesystem.

Layer 1  cross-panel majority voting   (MISMATCH_PANELS)
Layer 1b zoom ↔ panel comparison       (MISMATCH_ZOOM)
Layer 2  arithmetic + check digits     (NUMBER_FAIL)
Layer 3  dictionary / brand vocabulary (SPELL_FAIL, PHRASE_FAIL)
Layer 4  OCR readability flags         (UNREADABLE)

Hard rule from the user: the system NEVER invents or suggests words.
Dictionary findings only say "this word is not in the dictionary".
Arithmetic findings may show the computed number — that is deterministic
math, not a suggested wording.

A defect dict:
    {"class": str, "severity": str, "zone_id": str, "message": str,
     "found": str, "reference": str}
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Optional

from . import config


# ── small helpers ─────────────────────────────────────────────────────

def levenshtein(a: str, b: str) -> int:
    try:
        import Levenshtein
        return Levenshtein.distance(a, b)
    except ImportError:
        if a == b:
            return 0
        if not a:
            return len(b)
        if not b:
            return len(a)
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                               prev[j - 1] + (ca != cb)))
            prev = cur
        return prev[-1]


def _norm_line(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _norm_flat(s: str) -> str:
    """Whole text collapsed to one whitespace-free uppercase string —
    used to forgive OCR line-wrap differences."""
    return re.sub(r"\s+", "", s).upper()


def _lines(text: str) -> List[str]:
    return [_norm_line(l) for l in text.splitlines() if _norm_line(l)]


def _defect(cls: str, zone_id: str, message: str,
            found: str = "", reference: str = "") -> dict:
    return {
        "class": cls,
        "severity": config.DEFECT_CLASSES[cls]["severity"],
        "zone_id": zone_id,
        "message": message,
        "found": found,
        "reference": reference,
    }


# ── Layer 1: cross-panel majority voting ──────────────────────────────

def check_group_consistency(zones: List[dict],
                            texts: Dict[str, str]) -> List[dict]:
    """
    Zones sharing a non-empty ``group`` must carry identical text.

    Line-level voting: a line is "consensus" when it appears in a strict
    majority of the group's *panel* zones. Panels missing a consensus
    line, or carrying a non-consensus line, are flagged. Line-wrap noise
    is forgiven by re-checking against the flattened text. Zoom zones
    are held against the panel consensus: every zoom line must exist
    somewhere in the panels (zooms legitimately show only a part).
    """
    defects: List[dict] = []
    groups: Dict[str, List[dict]] = {}
    for z in zones:
        g = z.get("group") or ""
        if g and z.get("type") != "ignore":
            groups.setdefault(g, []).append(z)

    for gname, members in groups.items():
        panels = [z for z in members if z["type"] in ("panel", "header")]
        zooms = [z for z in members if z["type"] == "zoom"]

        readable = [z for z in panels if texts.get(z["id"], "").strip()]
        if len(readable) >= 2:
            defects += _vote_panels(gname, readable, texts)

        if zooms and readable:
            defects += _check_zooms(gname, zooms, readable, texts)
    return defects


def _vote_panels(gname: str, panels: List[dict],
                 texts: Dict[str, str]) -> List[dict]:
    n = len(panels)
    majority = n // 2 + 1
    zone_lines = {z["id"]: _lines(texts[z["id"]]) for z in panels}
    zone_flat = {z["id"]: _norm_flat(texts[z["id"]]) for z in panels}

    counts: Counter = Counter()
    for zid, lines in zone_lines.items():
        for line in set(lines):
            counts[line] += 1
    consensus = {l for l, c in counts.items() if c >= majority}

    defects: List[dict] = []
    for z in panels:
        zid = z["id"]
        own = set(zone_lines[zid])
        flat = zone_flat[zid]
        others_flat = [zone_flat[o["id"]] for o in panels if o["id"] != zid]

        # consensus lines absent here (forgive if present after re-wrap)
        missing = [l for l in consensus
                   if l not in own and _norm_flat(l) not in flat]
        # lines only this panel has (forgive if most others contain it
        # flattened — then it was just wrapped differently elsewhere)
        extra = []
        for l in zone_lines[zid]:
            if l in consensus:
                continue
            hits = sum(1 for of in others_flat if _norm_flat(l) in of)
            if hits + 1 < majority:
                extra.append(l)

        used_missing = set()
        for line in extra:
            best, best_d = None, None
            for m in missing:
                if m in used_missing:
                    continue
                d = levenshtein(line.upper(), m.upper())
                if best_d is None or d < best_d:
                    best, best_d = m, d
            if best is not None and best_d <= max(len(best), len(line)) // 2:
                used_missing.add(best)
                defects.append(_defect(
                    "MISMATCH_PANELS", z["id"],
                    f"กลุ่ม {gname}: ข้อความใน {z.get('label') or z['id']} "
                    f"ไม่ตรงกับ panel เสียงข้างมาก",
                    found=line, reference=best))
            else:
                defects.append(_defect(
                    "MISMATCH_PANELS", z["id"],
                    f"กลุ่ม {gname}: ข้อความนี้พบเฉพาะใน "
                    f"{z.get('label') or z['id']}",
                    found=line))
        for m in missing:
            if m not in used_missing:
                defects.append(_defect(
                    "MISMATCH_PANELS", z["id"],
                    f"กลุ่ม {gname}: ข้อความนี้หายไปจาก "
                    f"{z.get('label') or z['id']}",
                    reference=m))
    return defects


def _check_zooms(gname: str, zooms: List[dict], panels: List[dict],
                 texts: Dict[str, str]) -> List[dict]:
    defects: List[dict] = []
    panel_flat = "".join(_norm_flat(texts[p["id"]]) for p in panels)
    panel_lines = []
    for p in panels:
        panel_lines.extend(_lines(texts[p["id"]]))

    for z in zooms:
        for line in _lines(texts.get(z["id"], "")):
            if _norm_flat(line) in panel_flat:
                continue
            best, best_d = None, None
            for pl in panel_lines:
                d = levenshtein(line.upper(), pl.upper())
                if best_d is None or d < best_d:
                    best, best_d = pl, d
            defects.append(_defect(
                "MISMATCH_ZOOM", z["id"],
                f"กลุ่ม {gname}: ข้อความในส่วน zoom ไม่พบบนฉลากจริง",
                found=line,
                reference=best if (best is not None and best_d is not None
                                   and best_d <= max(4, len(line) // 2))
                else ""))
    return defects


# ── Layer 2: numbers ──────────────────────────────────────────────────

OZ_PER_LB = 16.0
KG_PER_LB = 0.45359237
G_PER_OZ = 28.349523125

_RE_COUNT = re.compile(
    r"(\d+)\s*[x×•·*]\s*(\d+(?:\.\d+)?)\s*OZ", re.IGNORECASE)
_RE_LBS = re.compile(r"(\d+(?:\.\d+)?)\s*LBS?\b", re.IGNORECASE)
_RE_KG = re.compile(r"(\d+(?:\.\d+)?)\s*KG\b", re.IGNORECASE)
_RE_OZ = re.compile(r"(?<![x×•·*\d.])\s*(\d+(?:\.\d+)?)\s*OZ\b",
                    re.IGNORECASE)
_RE_G = re.compile(r"\(?\s*(\d+(?:\.\d+)?)\s*G\.?\s*\)?(?![A-Za-z])",
                   re.IGNORECASE)
_RE_DIGITRUN = re.compile(r"(?<!\d)(\d[\d ]{10,20}\d)(?!\d)")


def _decimals(s: str) -> int:
    return len(s.split(".")[1]) if "." in s else 0


def _tol(printed: str, value: float) -> float:
    """Half a unit of the printed precision + relative slack."""
    return 0.5 * 10 ** (-_decimals(printed)) + abs(value) * \
        config.WEIGHT_REL_TOLERANCE


def gs1_check_digit_ok(digits: str) -> bool:
    """Validate UPC-A (12), EAN-13 (13) or ITF-14 (14) check digit."""
    if not digits.isdigit() or len(digits) not in (12, 13, 14):
        return False
    payload, check = digits[:-1], int(digits[-1])
    total = 0
    for i, ch in enumerate(reversed(payload)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10 == check


def check_numbers(zones: List[dict], texts: Dict[str, str]) -> List[dict]:
    defects: List[dict] = []
    for z in zones:
        text = texts.get(z["id"], "")
        if not text:
            continue
        zid = z["id"]

        # count × unit-oz must equal the stated LBS total
        counts = _RE_COUNT.findall(text)
        lbs_list = _RE_LBS.findall(text)
        kg_list = _RE_KG.findall(text)

        if counts and lbs_list:
            n, unit = counts[0]
            calc_lbs = int(n) * float(unit) / OZ_PER_LB
            stated = lbs_list[0]
            if abs(calc_lbs - float(stated)) > _tol(stated, calc_lbs):
                defects.append(_defect(
                    "NUMBER_FAIL", zid,
                    f"น้ำหนักรวมไม่สอดคล้อง: {n} × {unit} OZ = "
                    f"{calc_lbs:.3f} LBS แต่ฉลากพิมพ์ {stated} LBS",
                    found=f"{stated} LBS",
                    reference=f"{n} × {unit} OZ = {calc_lbs:.3f} LBS"))

        # LBS ↔ kg cross-unit check
        if lbs_list and kg_list:
            lbs_s, kg_s = lbs_list[0], kg_list[0]
            calc_kg = float(lbs_s) * KG_PER_LB
            if abs(calc_kg - float(kg_s)) > _tol(kg_s, calc_kg):
                defects.append(_defect(
                    "NUMBER_FAIL", zid,
                    f"หน่วยไม่ตรงกัน: {lbs_s} LBS = {calc_kg:.3f} kg "
                    f"แต่ฉลากพิมพ์ {kg_s} kg",
                    found=f"{kg_s} kg",
                    reference=f"{lbs_s} LBS = {calc_kg:.3f} kg"))

        # OZ ↔ g cross-unit check (single-can style: "5 OZ (142 g)")
        if not counts and not lbs_list:
            oz_list = _RE_OZ.findall(text)
            g_list = _RE_G.findall(text)
            if oz_list and g_list:
                oz_s, g_s = oz_list[0], g_list[0]
                calc_g = float(oz_s) * G_PER_OZ
                if abs(calc_g - float(g_s)) > max(_tol(g_s, calc_g), 0.51):
                    defects.append(_defect(
                        "NUMBER_FAIL", zid,
                        f"หน่วยไม่ตรงกัน: {oz_s} OZ = {calc_g:.1f} g "
                        f"แต่ฉลากพิมพ์ {g_s} g",
                        found=f"{g_s} g",
                        reference=f"{oz_s} OZ = {calc_g:.1f} g"))

        # barcode human-readable digits → check digit
        for run in _RE_DIGITRUN.findall(text):
            digits = run.replace(" ", "")
            if len(digits) in (12, 13, 14) and not gs1_check_digit_ok(digits):
                defects.append(_defect(
                    "NUMBER_FAIL", zid,
                    f"เลขบาร์โค้ด {digits} check digit ไม่ถูกต้อง "
                    f"(ตามสูตร GS1 mod-10)",
                    found=digits))
    return defects


# ── Layer 3: dictionary + brand vocabulary ────────────────────────────

_RE_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+(?:['’][A-Za-zÀ-ÖØ-öø-ÿ]+)?")

_spellcheckers: Optional[list] = None


def _get_spellcheckers() -> list:
    """Lazily build pyspellchecker instances; [] if lib not installed."""
    global _spellcheckers
    if _spellcheckers is None:
        _spellcheckers = []
        try:
            from spellchecker import SpellChecker
            for lang in config.SPELL_LANGUAGES:
                try:
                    _spellcheckers.append(SpellChecker(language=lang,
                                                       distance=1))
                except Exception:
                    pass
        except ImportError:
            pass
    return _spellcheckers


def spell_layer_available() -> bool:
    return bool(_get_spellcheckers())


def check_spelling(zones: List[dict], texts: Dict[str, str],
                   vocab_words: Optional[set] = None) -> List[dict]:
    """
    Flag words found in no enabled dictionary and not in the brand
    vocabulary. NO suggestions are produced — per the project rule the
    system must not invent words. Non-Latin scripts (Thai/Arabic) are
    left to the cross-panel layer.
    """
    checkers = _get_spellcheckers()
    if not checkers:
        return []
    vocab = {w.lower() for w in (vocab_words or set())}

    # word → set of zone ids (dedupe identical findings across panels)
    unknown: Dict[str, set] = {}
    for z in zones:
        text = texts.get(z["id"], "")
        for word in _RE_WORD.findall(text):
            if len(word) < config.SPELL_MIN_WORD_LEN:
                continue
            lw = word.lower()
            if lw in vocab:
                continue
            if any(ch.isdigit() for ch in word):
                continue
            known = any(c.known([lw]) for c in checkers)
            if not known:
                unknown.setdefault(word, set()).add(z["id"])

    defects = []
    for word, zids in sorted(unknown.items()):
        zlist = ", ".join(sorted(zids))
        defects.append(_defect(
            "SPELL_FAIL", sorted(zids)[0],
            f"คำว่า “{word}” ไม่อยู่ใน dictionary "
            f"({'/'.join(config.SPELL_LANGUAGES)}) และไม่อยู่ในคลังคำแบรนด์ "
            f"(พบในโซน: {zlist}) — โปรดยืนยันด้วยตา",
            found=word))
    return defects


def check_phrases(zones: List[dict], texts: Dict[str, str],
                  phrases: List[str]) -> List[dict]:
    """
    Approved brand phrases: if a phrase appears EXACTLY (whitespace
    collapsed, case-insensitive) → pass. If a near-miss window exists
    within PHRASE_MAX_EDITS edits → PHRASE_FAIL showing the artwork's
    actual text vs the approved phrase. If nothing close exists, the
    phrase simply does not apply to this artwork (no defect).
    """
    defects: List[dict] = []
    for phrase in phrases:
        target = _norm_flat(phrase)
        if not target:
            continue
        best = None    # (dist, zone_id, window_text)
        for z in zones:
            text = texts.get(z["id"], "")
            if not text:
                continue
            flat = _norm_flat(text)
            if target in flat:
                best = (0, z["id"], phrase)
                break
            d, win = _best_window(flat, target)
            if best is None or d < best[0]:
                best = (d, z["id"], win)
        if best and 0 < best[0] <= config.PHRASE_MAX_EDITS:
            defects.append(_defect(
                "PHRASE_FAIL", best[1],
                f"วลีมาตรฐาน “{phrase}” พบในรูปที่สะกดไม่ตรงกับที่ approve "
                f"(ต่างกัน {best[0]} ตัวอักษร)",
                found=best[2], reference=phrase))
    return defects


def _best_window(flat: str, target: str):
    """Min edit distance of any window of ~len(target) inside flat."""
    n, m = len(flat), len(target)
    if n == 0 or m == 0:
        return (max(n, m), "")
    best_d, best_win = m, ""
    for size in (m, m - 1, m + 1, m - 2, m + 2):
        if size <= 0 or size > n:
            continue
        step = max(1, m // 4)
        for i in range(0, n - size + 1, step):
            win = flat[i:i + size]
            # cheap lower bound before the real distance
            if abs(size - m) >= best_d:
                continue
            d = levenshtein(win, target)
            if d < best_d:
                best_d, best_win = d, win
                if best_d == 0:
                    return (0, best_win)
    return (best_d, best_win)


# ── Layer 4: readability ──────────────────────────────────────────────

def check_readability(zones: List[dict], ocr_results: List[dict],
                      min_conf: float = 0.5) -> List[dict]:
    defects = []
    by_id = {z["id"]: z for z in zones}
    for r in ocr_results:
        z = by_id.get(r["zone_id"])
        if z is None:
            continue
        label = z.get("label") or r["zone_id"]
        if r.get("error"):
            defects.append(_defect(
                "UNREADABLE", r["zone_id"],
                f"โซน {label}: อ่านไม่ได้ — {r['error']}"))
        elif not r.get("text", "").strip():
            defects.append(_defect(
                "UNREADABLE", r["zone_id"],
                f"โซน {label}: OCR ไม่พบข้อความ — โปรดตรวจด้วยตา"))
        elif r.get("conf") is not None and r["conf"] < min_conf:
            defects.append(_defect(
                "UNREADABLE", r["zone_id"],
                f"โซน {label}: ความเชื่อมั่น OCR ต่ำ ({r['conf']:.2f}) — "
                f"ผลตรวจโซนนี้อาจไม่แม่น โปรดดูด้วยตา"))
    return defects


# ── Orchestrator ──────────────────────────────────────────────────────

def run_all_checks(zones: List[dict], ocr_results: List[dict],
                   vocab_words: Optional[set] = None,
                   vocab_phrases: Optional[List[str]] = None) -> List[dict]:
    texts = {r["zone_id"]: r.get("text", "") for r in ocr_results}
    defects: List[dict] = []
    defects += check_group_consistency(zones, texts)
    defects += check_numbers(zones, texts)
    defects += check_spelling(zones, texts, vocab_words=vocab_words)
    defects += check_phrases(zones, texts, vocab_phrases or [])
    defects += check_readability(zones, ocr_results)
    return defects
