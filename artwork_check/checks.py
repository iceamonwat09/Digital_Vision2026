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
import unicodedata
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


_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

# Arabic orthographic normalization — the same rule set as the standard
# Lucene/Elasticsearch ArabicNormalizer. OCR of PRINTED Arabic cannot
# reliably distinguish these forms (the same photo OCR'd twice returned
# "انظر" and "أنظر"; one real label spells الصافي/الصافى both ways), so
# treating them as different letters produces pure false MISMATCH noise.
_AR_LETTERS = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",   # hamza/madda alef forms → alef
    "ى": "ي",                                  # alef maqsura → yeh
    "ة": "ه",                                  # taa marbuta → heh
    "×": "X",              # multiplication sign ↔ letter x OCR confusion
})
# Harakat (short-vowel diacritics U+064B–U+0652), superscript alef
# (U+0670) and tatweel (U+0640) — decorative in print, OCR reads them
# in or out at random.
_AR_MARKS = re.compile("[ً-ْٰـ]")


def _norm_key(s: str) -> str:
    """
    Aggressive comparison key: Unicode NFKC (folds full-width forms,
    ligatures, Arabic presentation forms), Arabic orthography
    normalized (hamza forms → ا, ى → ي, ة → ه, harakat/tatweel
    stripped), Arabic-Indic digits → ASCII, uppercase, then keep ONLY
    letters and digits.

    Two OCR passes over the SAME printed text routinely disagree on
    punctuation: "EL - OBOUR – BLOCK" vs "ELOBOUR-BLOCK", "٧٠٪" vs
    "%٧٠%" (bidi reordering of the percent sign), en-dash vs hyphen —
    and on Arabic hamza/diacritic marks and CJK full-width Latin.
    Those are transcription noise, not print defects, so comparisons
    fall back to this key. Letter/digit differences — the defects this
    mode exists to catch — still mismatch ("CALIDDD" ≠ "CALIDAD",
    "التخزين" ≠ "التحزين"). European accents are kept (é ≠ e is a real
    spelling difference on ES/FR labels).
    """
    s = unicodedata.normalize("NFKC", s)
    s = _AR_MARKS.sub("", s)
    s = s.translate(_AR_LETTERS).translate(_AR_DIGITS).upper()
    return re.sub(r"[\W_]+", "", s)


def _key_tokens(line: str, min_len: int = 2) -> List[str]:
    """Per-word keys of a line (words shorter than min_len dropped)."""
    return [t for t in (_norm_key(w) for w in line.split())
            if len(t) >= min_len]


def _is_cjk_char(p: str) -> bool:
    """Single CJK ideograph (incl. Ext A + compatibility block)."""
    return len(p) == 1 and ("㐀" <= p <= "鿿"
                            or "豈" <= p <= "﫿")


def _composable_from(key: str, piece_keys: List[str],
                     min_piece: int = 2, frag_min: int = 6) -> bool:
    """
    True when ``key`` can be written as a concatenation of FULL line
    keys from ``piece_keys`` — plus, AT MOST ONCE, one contiguous
    fragment (>= ``frag_min`` chars) of one of those lines.

    Why: two OCR passes over the SAME printed table routinely disagree
    on line segmentation — one reads a two-column row as ONE line
    ("粗蛋白質 Cude Protein 8.0%以上 Min"), the other reads the columns
    as separate, NON-adjacent lines ("粗蛋白質 Cude Protein" … "8.0%以上
    Min"). The merged line is then not a contiguous substring of the
    other panel's text, so the plain flat/key substring forgiveness
    misses it and a false MISMATCH_PANELS is raised (seen with two
    uploads of the same photo).

    Requiring segments to be ENTIRE lines elsewhere keeps this strict:
    a real typo ("SKIPJAKTUNA") or swapped values ("PROTEIN 0.2" vs
    "PROTEIN 8.0") cannot be assembled from whole lines of the correct
    text, so genuine defects are still flagged. Two relaxations, both
    measured against real station OCR output:

    • single-character pieces are allowed only for CJK ideographs —
      OCR reads a spaced CJK table header ("品 名") as one line per
      character (even reordered), and one ideograph is a whole word.
      Single Latin letters/digits stay banned (they would let nearly
      anything be assembled).
    • one fragment >= frag_min chars of a single line — OCR sometimes
      glues the TAIL of one row onto another row ("… 8.0%以上 Min
      0.2%以上 Min"); the orphaned tail is then a fragment, not a whole
      line, on the other side. Capping it at ONE long fragment keeps
      typos/swaps unforgivable (they would need two fragments or a
      too-short one).
    """
    pieces = {p for p in piece_keys
              if len(p) >= min_piece or _is_cjk_char(p)}
    if not key:
        return False
    n = len(key)
    frag_lines = [p for p in piece_keys if len(p) >= frag_min]
    reach0 = [False] * (n + 1)   # composed without using the fragment
    reach1 = [False] * (n + 1)   # fragment already spent
    reach0[0] = True
    for i in range(n):
        if not (reach0[i] or reach1[i]):
            continue
        for p in pieces:
            if key.startswith(p, i):
                j = i + len(p)
                reach0[j] = reach0[j] or reach0[i]
                reach1[j] = reach1[j] or reach1[i]
        if reach0[i]:
            for j in range(i + frag_min, n + 1):
                frag = key[i:j]
                if any(frag in ln for ln in frag_lines):
                    reach1[j] = True
                else:
                    break   # longer frags contain this one → also absent
    return reach0[n] or reach1[n]


def _lines(text: str) -> List[str]:
    return [_norm_line(l) for l in text.splitlines() if _norm_line(l)]


def _defect(cls: str, zone_id: str, message: str,
            found: str = "", reference: str = "",
            ref_zone_ids: List[str] = None) -> dict:
    d = {
        "class": cls,
        "severity": config.DEFECT_CLASSES[cls]["severity"],
        "zone_id": zone_id,
        "message": message,
        "found": found,
        "reference": reference,
    }
    if ref_zone_ids:
        d["ref_zone_ids"] = ref_zone_ids
    return d


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
    # Drop pure-symbol / non-text lines (arrows ↑, bullets, dimension
    # ticks) — OCR picks these up when a zone is dragged a little wide,
    # and they have no textual content to verify, so they must never
    # enter the consensus vote or be flagged as a mismatch.
    zone_lines = {z["id"]: [l for l in _lines(texts[z["id"]]) if _norm_key(l)]
                  for z in panels}
    zone_flat = {z["id"]: _norm_flat(texts[z["id"]]) for z in panels}
    zone_key = {z["id"]: _norm_key(texts[z["id"]]) for z in panels}
    # per-line keys — used to forgive OCR line-merge/split noise where a
    # line equals a concatenation of WHOLE lines elsewhere (see
    # _composable_from)
    zone_line_keys = {zid: [_norm_key(l) for l in lines]
                      for zid, lines in zone_lines.items()}

    counts: Counter = Counter()
    for zid, lines in zone_lines.items():
        for line in set(lines):
            counts[line] += 1
    consensus = {l for l, c in counts.items() if c >= majority}

    # Real labels (type "panel") are the source of truth; headers come last.
    def _ref_ids_for(line: str, candidates: List[dict]) -> List[str]:
        """Panels (real labels first) that actually carry ``line``."""
        lk = _norm_key(line)
        lf = _norm_flat(line)
        hit = [c for c in candidates
               if lf in zone_flat[c["id"]] or lk in zone_key[c["id"]]]
        pool = hit or candidates
        pool = sorted(pool, key=lambda c: 0 if c.get("type") == "panel" else 1)
        return [c["id"] for c in pool]

    defects: List[dict] = []
    for z in panels:
        zid = z["id"]
        own = set(zone_lines[zid])
        flat = zone_flat[zid]
        key = zone_key[zid]
        other_zones = [o for o in panels if o["id"] != zid]
        others = [o["id"] for o in other_zones]

        # consensus lines absent here (forgive if present after re-wrap,
        # modulo OCR punctuation noise, or split across this panel's own
        # lines by a different OCR segmentation)
        missing = [l for l in consensus
                   if l not in own and _norm_flat(l) not in flat
                   and _norm_key(l) not in key
                   and not _composable_from(_norm_key(l),
                                            zone_line_keys[zid])]
        # lines only this panel has (forgive if most others contain it
        # flattened — then it was just wrapped differently elsewhere —
        # or if it is a merge of whole lines the others carry separately)
        extra = []
        for l in zone_lines[zid]:
            if l in consensus:
                continue
            lk = _norm_key(l)
            hits = sum(1 for oid in others
                       if _norm_flat(l) in zone_flat[oid]
                       or lk in zone_key[oid]
                       or _composable_from(lk, zone_line_keys[oid]))
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
                    found=line, reference=best,
                    ref_zone_ids=_ref_ids_for(best, other_zones)))
            else:
                defects.append(_defect(
                    "MISMATCH_PANELS", z["id"],
                    f"กลุ่ม {gname}: ข้อความนี้พบเฉพาะใน "
                    f"{z.get('label') or z['id']}",
                    found=line,
                    ref_zone_ids=others))
        for m in missing:
            if m not in used_missing:
                defects.append(_defect(
                    "MISMATCH_PANELS", z["id"],
                    f"กลุ่ม {gname}: ข้อความนี้หายไปจาก "
                    f"{z.get('label') or z['id']}",
                    reference=m,
                    ref_zone_ids=_ref_ids_for(m, other_zones)))

    # กลุ่ม 2 panel ข้ามไฟล์ (ไฟล์หลัก a + ไฟล์อ้างอิง b) ไม่มีเสียงข้าง
    # มากให้ตัดสิน — ความต่างจุดเดียวจึงเคยฟ้องซ้ำ 2 ใบ ("พบเฉพาะใน" ทั้ง
    # สองฝั่ง). จับคู่บรรทัดที่ใกล้เคียงกันเป็น defect เดียว ชี้ที่ไฟล์หลัก
    # พร้อมข้อความอ้างอิงจากไฟล์อ้างอิง. scope เฉพาะคู่ข้ามไฟล์เท่านั้น —
    # กลุ่ม 2 panel ภายในไฟล์เดียวพฤติกรรมเดิมทุกอย่าง.
    if n == 2 and panels[0].get("doc", "a") != panels[1].get("doc", "a"):
        defects = _pair_cross_doc_extras(gname, panels, defects)
    return defects


def _pair_cross_doc_extras(gname: str, panels: List[dict],
                           defects: List[dict]) -> List[dict]:
    """Merge complementary found-only defects of a 2-panel cross-file
    group into single found/reference defects attributed to the primary
    file. Verdict-neutral: pairs stay MISMATCH_PANELS (critical);
    unpairable lines keep their original defects."""
    prim = next(p for p in panels if p.get("doc", "a") == "a")
    ref = next(p for p in panels if p.get("doc", "a") == "b")

    def found_only(zid):
        return [d for d in defects
                if d["class"] == "MISMATCH_PANELS" and d["zone_id"] == zid
                and d.get("found") and not d.get("reference")]

    a_list, b_list = found_only(prim["id"]), found_only(ref["id"])
    if not a_list or not b_list:
        return defects

    pairs = []
    used_b: set = set()
    for da in a_list:
        best, best_d = None, None
        for idx, db in enumerate(b_list):
            if idx in used_b:
                continue
            d = levenshtein(da["found"].upper(), db["found"].upper())
            if best_d is None or d < best_d:
                best, best_d = idx, d
        if best is not None:
            db = b_list[best]
            # เกณฑ์ความใกล้เดียวกับการจับคู่ extra↔missing เดิม
            if best_d <= max(len(da["found"]), len(db["found"])) // 2:
                used_b.add(best)
                pairs.append((da, db))
    if not pairs:
        return defects

    drop = {id(d) for pair in pairs for d in pair}
    out = [d for d in defects if id(d) not in drop]
    for da, db in pairs:
        out.append(_defect(
            "MISMATCH_PANELS", prim["id"],
            f"กลุ่ม {gname}: ข้อความบนไฟล์หลักไม่ตรงกับไฟล์อ้างอิง (ชิ้นงาน)",
            found=da["found"], reference=db["found"],
            ref_zone_ids=[ref["id"]]))
    return out


def _check_zooms(gname: str, zooms: List[dict], panels: List[dict],
                 texts: Dict[str, str]) -> List[dict]:
    """
    Compare each zoom against the real-label panels.

    The ZOOM (ส่วนขยาย) is the enlarged, human-readable REFERENCE — the
    version that shows clearly what the text is meant to be. The REAL
    LABEL (type "panel") is the artwork that actually gets printed, so it
    is the SUBJECT under inspection: when a real-label line differs from
    the zoom reference, the defect is attributed to the REAL LABEL (so it
    is the thing flagged, spell-checked and translated), with the zoom as
    the reference of what it should say.
    """
    defects: List[dict] = []

    # Real labels = the printed artwork we inspect. Fall back to whatever
    # panels exist only when the group has no explicit "panel" zone.
    real = [p for p in panels if p.get("type") == "panel"] or panels
    real_ids = [p["id"] for p in real]

    panel_flat = "".join(_norm_flat(texts[p["id"]]) for p in real)
    panel_key = "".join(_norm_key(texts[p["id"]]) for p in real)
    panel_lines = []                       # (line_text, panel_id)
    for p in real:
        for pl in _lines(texts[p["id"]]):
            panel_lines.append((pl, p["id"]))

    for z in zooms:
        zid = z["id"]
        for line in _lines(texts.get(zid, "")):
            key = _norm_key(line)
            # Pure symbol / arrow / punctuation line (key collapses to
            # empty) — not text, never comparable. Skip so it can't be
            # mis-paired with a real word like "TUNA".
            if not key:
                continue
            if _norm_flat(line) in panel_flat:
                continue
            if key in panel_key:
                continue
            # OCR of the zoom may merge fields that the panel OCR split
            # into separate lines (or read RTL columns in another order)
            # — forgive when every word of the line exists in the panels.
            toks = _key_tokens(line)
            if toks and all(t in panel_key for t in toks):
                continue
            # Closest real-label line, compared on normalized keys so
            # punctuation/case noise does not inflate the distance.
            best, best_d, best_pid, best_key = None, None, None, ""
            for pl, pid in panel_lines:
                pk = _norm_key(pl)
                if not pk:
                    continue
                d = levenshtein(key, pk)
                if best_d is None or d < best_d:
                    best, best_d, best_pid, best_key = pl, d, pid, pk
            # A genuine misspelling shares MOST of its characters with its
            # match (small distance relative to length); a stray fragment
            # captured by an imprecise zone does not. Using a ratio gates
            # out short-junk-vs-real-word false matches (e.g. "↑"→"TUNA"),
            # while still catching real typos like "REDDED"→"SHREDDED".
            span = max(len(key), len(best_key)) if best is not None else 0
            close = (best is not None and span > 0
                     and best_d < span
                     and best_d <= max(2, int(span * 0.4)))
            if close:
                # The real label is misspelled relative to the zoom
                # reference → flag the REAL LABEL, show the zoom as the
                # correct text.
                defects.append(_defect(
                    "MISMATCH_ZOOM", best_pid,
                    f"กลุ่ม {gname}: ข้อความบนฉลากจริงไม่ตรงกับส่วนขยาย (อ้างอิง)",
                    found=best, reference=line,
                    ref_zone_ids=[zid]))
            else:
                # The zoom shows text that does not appear on the real
                # label at all (label may be missing it).
                defects.append(_defect(
                    "MISMATCH_ZOOM", zid,
                    f"กลุ่ม {gname}: ข้อความในส่วน zoom ไม่พบบนฉลากจริง",
                    found=line, reference="",
                    ref_zone_ids=real_ids))
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

        # barcode human-readable digits → check digit.
        # EAN-13 พิมพ์หลักแรกแยกไว้ที่โคนบาร์โค้ด — OCR ของบาร์โค้ดแนวตั้ง
        # จึงมักได้ "9" กับอีก 12 หลักเป็นคนละ run. ก่อนฟ้อง จึงลองต่อ run
        # เลขที่อยู่ติดกันในโซนเดียวกัน: ถ้าคอมโบใด check digit ผ่าน แปลว่า
        # เป็นบาร์โค้ดถูกที่ถูก OCR ตัดแยก ไม่ใช่เลขผิด (deterministic —
        # ทดสอบ segmentation ทางเลือก ไม่ใช่การเดาเลขใหม่).
        seqs = [m.replace(" ", "")
                for m in re.findall(r"\d[\d ]*\d|\d", text)]
        for i, digits in enumerate(seqs):
            if len(digits) not in (12, 13, 14) or gs1_check_digit_ok(digits):
                continue
            joined_ok = False
            for j in (i - 1, i + 1):
                if 0 <= j < len(seqs):
                    for combo in (seqs[j] + digits, digits + seqs[j]):
                        if (len(combo) in (12, 13, 14)
                                and gs1_check_digit_ok(combo)):
                            joined_ok = True
            if joined_ok:
                continue
            defects.append(_defect(
                "NUMBER_FAIL", zid,
                f"เลขบาร์โค้ด {digits} check digit ไม่ถูกต้อง "
                f"(ตามสูตร GS1 mod-10)",
                found=digits))
    return defects


# ── Layer 3: dictionary + brand vocabulary ────────────────────────────

_RE_WORD = re.compile(
    r"[A-Za-zÀ-ÖØ-öø-ÿЀ-ӿ؀-ۿ]+"
    r"(?:['’][A-Za-zÀ-ÖØ-öø-ÿЀ-ӿ؀-ۿ]+)?"
)

# URL/technical tokens ที่ _RE_WORD ตัดออกมาจากที่อยู่เว็บ/อีเมลบนฉลาก —
# ไม่ใช่คำสะกด ไม่ควรฟ้อง SPELL_FAIL (เช่น "https" จาก https://…)
_SPELL_STOPLIST = {"http", "https", "www", "mailto"}

# Scripts whose pyspellchecker dictionary is unreliable enough that a
# "not in dictionary" result is NOT trustworthy evidence of a typo, so a
# failed word must NOT raise SPELL_FAIL (would falsely push the verdict
# to REVIEW) and must NOT get an edit-distance suggestion (Arabic
# morphology makes single-edit guesses wrong — "المهدرجة"→"المدرجة" is a
# different word). These words are surfaced advisory-only in the
# translate tab as "dict ไม่รองรับคำนี้ (<script>)" and defer to the AI
# column + cross-panel comparison. Maps a script key → Thai name.
UNSUPPORTED_SCRIPT_NAMES = {"arabic": "อาหรับ"}

# Arabic Unicode blocks (base + supplement + extended-A + presentation
# forms A/B) — a word carrying any of these is Arabic script.
_AR_RANGES = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF),
              (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))


def word_script(word: str) -> Optional[str]:
    """Return an UNSUPPORTED_SCRIPT_NAMES key if ``word`` is in a script
    whose dictionary we treat as unreliable, else None (Latin/Cyrillic
    are covered by their dictionaries). Currently detects Arabic."""
    for ch in word:
        o = ord(ch)
        if any(lo <= o <= hi for lo, hi in _AR_RANGES):
            return "arabic"
    return None


def is_dict_unsupported(word: str) -> bool:
    return word_script(word) in UNSUPPORTED_SCRIPT_NAMES

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
    system must not invent words. Thai/CJK have no enabled dictionary
    and are left to the cross-panel layer; Cyrillic is covered when "ru"
    is enabled. Arabic-script words are treated as dict-unsupported (see
    UNSUPPORTED_SCRIPT_NAMES): a failed lookup does NOT raise SPELL_FAIL
    (its dictionary is too unreliable to be verdict evidence) — those
    words are surfaced advisory-only in the translate tab instead.
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
            if lw in _SPELL_STOPLIST:
                continue
            if lw in vocab:
                continue
            if any(ch.isdigit() for ch in word):
                continue
            if is_dict_unsupported(word):
                continue          # dict can't judge → never a SPELL_FAIL
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
