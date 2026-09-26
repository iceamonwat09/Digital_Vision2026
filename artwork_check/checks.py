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

import functools
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

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
    return re.sub(r"[\W_]+", "", _norm_core(s).upper())


def _norm_core(s: str) -> str:
    """ส่วนที่ ``_norm_key`` และ ``_norm_key_cs`` ใช้ร่วมกัน — NFKC +
    อักขรวิธีอาหรับ + เลขอาหรับ-อินดิก (ยังไม่พับตัวพิมพ์ ยังไม่ตัดสัญลักษณ์)"""
    s = unicodedata.normalize("NFKC", s)
    s = _AR_MARKS.sub("", s)
    return s.translate(_AR_LETTERS).translate(_AR_DIGITS)


def _norm_key_cs(s: str) -> str:
    """เหมือน ``_norm_key`` ทุกอย่าง **ยกเว้นไม่พับตัวพิมพ์ใหญ่-เล็ก**

    ใช้เฉพาะ "ชั้นที่สอง" ที่ตรวจตัวพิมพ์ (``_case_only_defects``) และ
    ``diff_spans(case=True)`` เท่านั้น — ชั้นเทียบหลักยังใช้ ``_norm_key``
    เหมือนเดิมทุกจุด (ดูเหตุผลที่ ``config.TEXT_CASE_SENSITIVE``)
    """
    return re.sub(r"[\W_]+", "", _norm_core(s))


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


_RE_NUM_CANON = re.compile(r"\d+(?:[.,]\d+)*")
_RE_NUM_BEFORE = re.compile(r"\d[.,]?$")
_RE_NUM_AFTER = re.compile(r"[.,]?\d")


def _num_canon(s: str) -> List[str]:
    """ตัวเลขในข้อความ **คงจุดทศนิยม** — ``59,9`` → ``59.9`` · ``599`` → ``599``
    (``_norm_key`` ตัดวรรคตอน ⇒ สองค่านี้กลายเป็น ``599`` เท่ากัน) ·
    ช่องว่างระหว่างเลขถูกเชื่อม (``1 000`` = ``1000``) · ``,`` เท่ากับ ``.``"""
    s = re.sub(r"(?<=\d)\s+(?=\d)", "", _norm_core(s or ""))
    return [m.group().replace(",", ".") for m in _RE_NUM_CANON.finditer(s)]


@functools.lru_cache(maxsize=32)
def _keyed(text: str):
    """คีย์ทีละอักขระ + ตำแหน่งในข้อความดิบ (คีย์แบบเดียวกับ ``_norm_key``)

    แคชไว้ — ข้อความทั้งแผงถูกถามซ้ำทุกบรรทัด (แผง 31k ตัวอักษร: ไม่แคช
    32 วิ · แคช ≈ เท่าเดิม)"""
    ks, pos = [], []
    for i, c in enumerate(text or ""):
        for k in _key_char(c):
            ks.append(k)
            pos.append(i)
    return "".join(ks), tuple(pos)


@functools.lru_cache(maxsize=4096)
def _key_char(c: str) -> str:
    return _norm_key(c)


def _contained_soundly(line: str, other: str) -> Optional[bool]:
    """บรรทัด ``line`` ที่ถูกยกโทษเพราะ "มีอยู่ในอีกแผง" — ตัวเลขตรงกันจริงไหม

    ที่มา (26 ก.ย., เปลี่ยนตัวเลขทีละตัวบนแผงจริง 2,021 เคส): ชั้นเทียบหลัก
    **พลาด 14 เคส** เพราะการยกโทษแบบ "คีย์ของบรรทัดอยู่ที่ไหนสักแห่งในแผง":

    * ``bruta 1,0 %`` → ``10,0 %`` — ``BRUTA100`` ไปเจอรอยต่อข้ามบรรทัด
      (``…BRUTA10`` + ``0,5…`` ของบรรทัดถัดไป) ⇒ ตัวเลขถูกหั่นกลางตัว
    * ``(59,8 %)`` → ``(599 %)`` — ``_norm_key`` ตัดจุดทศนิยม ⇒ ``59,9`` =
      ``599`` (ในแผงมี ``59,9`` อีกบรรทัด)

    คืน ``True`` = มีตำแหน่งที่ตรงแล้ว **ตัวเลขไม่ถูกหั่น และค่าตัวเลขเท่ากัน**
    · ``False`` = ตรงแค่แบบหั่นตัวเลข/ค่าต่าง ⇒ **ห้ามยกโทษ** ·
    ``None`` = ไม่พบแบบติดกันเลย (ยกโทษมาจากการประกอบบรรทัด) ⇒ ไม่ตัดสิน

    ⚠️ ไม่แตะตัวอักษร — เฉพาะ **ตัวเลข** (ขอบคำตัวอักษรชนกับการตัดคำ
    ข้ามบรรทัด ``Pro-``/``tein`` ⇒ ไม่วัด = ไม่ทำ)
    """
    kl, _ = _keyed(line)
    if not kl or not any(c.isdigit() for c in kl):
        return None
    n_all, n_ok = _occurrences(line, other)
    if n_ok:
        return True
    return False if n_all else None


def _occurrences(line: str, other: str) -> Tuple[int, int]:
    """(ตำแหน่งที่คีย์ตรงทั้งหมด, ตำแหน่งที่ตัวเลขไม่ถูกหั่นและค่าเท่ากัน)
    — นับแบบไม่ทับกัน"""
    kl, _ = _keyed(line)
    if not kl:
        return 0, 0
    ko, pos = _keyed(other)
    want = _num_canon(line)
    n_all = n_ok = 0
    at = ko.find(kl)
    while at >= 0:
        n_all += 1
        end = at + len(kl)
        lo, hi = pos[at], pos[end - 1] + 1
        # ตัวเลขถูกหั่น = ในข้อความดิบ ติดกับตัวเลขอีกตัว (คั่นด้วย ``.``/``,``
        # ได้ไม่เกินหนึ่งตัว) — ขึ้นบรรทัด/ช่องว่างคั่น = คนละจำนวน ไม่ใช่การหั่น
        cut = ((kl[0].isdigit() and _RE_NUM_BEFORE.search(other[:lo]))
               or (kl[-1].isdigit() and _RE_NUM_AFTER.match(other[hi:])))
        if not cut and _num_canon(other[lo:hi]) == want:
            n_ok += 1
            at = ko.find(kl, end)
        else:
            at = ko.find(kl, at + 1)
    return n_all, n_ok


def _surplus_copies(lines: List[str], own: str, other: str) -> Dict[str, tuple]:
    """บรรทัด **มีตัวเลข** ที่ฝั่งนี้พิมพ์ซ้ำ **มากกว่า** ที่อีกฝั่งมี

    ที่มา (26 ก.ย.): ฉลากหลายรสพิมพ์บรรทัดเกือบเหมือนกันซ้ำ — เปลี่ยนเลขของ
    รสหนึ่งให้ไปตรงกับอีกรสพอดี (``1,0%`` → ``1,7%``) ⇒ บรรทัดใหม่ "มีอยู่"
    ในอีกแผงทุกตัวอักษร ⇒ ยกโทษ ⇒ พลาด 8/1,008 เคส. สิ่งเดียวที่เปลี่ยนคือ
    **จำนวนครั้ง** ที่บรรทัดนั้นปรากฏ · นับฝั่งตรงข้ามแบบทนการตัดบรรทัด
    (``_occurrences``) · เฉพาะบรรทัดยาว ≥ 12 ตัว (บรรทัดสั้นอย่าง ``88,0%``
    ไปโผล่ในบรรทัดอื่นได้ตามธรรมชาติ)"""
    out: Dict[str, tuple] = {}
    for l in lines:
        if l in out:
            continue
        kl, _ = _keyed(l)
        if len(kl) < 12 or not any(c.isdigit() for c in kl):
            continue
        # นับทั้งสองฝั่งด้วยวิธีเดียวกัน (รวมที่อยู่ในบรรทัดยาวของรสอื่น)
        # · อีกฝั่งต้องมีอย่างน้อยหนึ่งที่ — ไม่มีเลย = ถูกยกโทษด้วยการ
        # ประกอบบรรทัด ซึ่งไม่ใช่เรื่องของจำนวนสำเนา ⇒ ไม่แตะ
        theirs = _occurrences(l, other)[1]
        mine = _occurrences(l, own)[1] if theirs else 0
        if theirs and mine > theirs:
            out[l] = (mine, theirs)             # ลำดับเดิมของบรรทัด (dict)
    return out


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
            voted = _vote_panels(gname, readable, texts)
            defects += voted
            # ชั้นที่สอง: บรรทัดที่ต่าง **เฉพาะตัวพิมพ์ใหญ่-เล็ก** ซึ่งชั้น
            # โหวตข้างบนยกโทษให้เสมอ (ทุก key พับตัวพิมพ์) ⇒ เดิมเงียบสนิท.
            # ส่ง ``voted`` เข้าไปเพื่อไม่รายงานซ้ำบรรทัดที่ฟ้องไปแล้ว
            if config.TEXT_CASE_SENSITIVE:
                defects += _case_only_defects(gname, readable, texts, voted)

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

    # ชั้นเข้มเรื่องตัวเลข (26 ก.ย.) — เฉพาะกลุ่ม 2 panel (ดู
    # ``_contained_soundly``) · ปิด = การยกโทษแบบเดิมเป๊ะ
    num_strict = config.TEXT_NUMBER_STRICT and n == 2

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
                       if (_norm_flat(l) in zone_flat[oid]
                           or lk in zone_key[oid]
                           or _composable_from(lk, zone_line_keys[oid]))
                       and not (num_strict
                                and _contained_soundly(l, texts[oid]) is False))
            if hits + 1 < majority:
                extra.append(l)

        surplus = (_surplus_copies(zone_lines[zid], texts[zid],
                                   texts[others[0]]) if num_strict else {})
        extra += [l for l in surplus if l not in extra]

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
            elif line in surplus:
                # อีกฝั่ง **มี** บรรทัดนี้ แต่น้อยครั้งกว่า — ห้ามบอกว่า "พบเฉพาะ"
                mine, theirs = surplus[line]
                defects.append(_defect(
                    "MISMATCH_PANELS", z["id"],
                    f"กลุ่ม {gname}: บรรทัดนี้ปรากฏใน "
                    f"{z.get('label') or z['id']} {mine} ครั้ง แต่อีกฝั่ง "
                    f"{theirs} ครั้ง — ค่าของรส/รายการหนึ่งอาจถูกเปลี่ยนให้"
                    f"ตรงกับอีกรายการ",
                    found=line,
                    ref_zone_ids=others))
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
        defects = _pair_cross_doc_extras(gname, panels, defects, texts)
    return defects


def _words(text: str, case: bool = False):
    """คำของบรรทัด → ``[(คีย์เทียบ, เริ่ม, จบ)]`` โดยคีย์ใช้ ``_norm_key``

    ``case=True`` = ใช้ ``_norm_key_cs`` (ไม่พับตัวพิมพ์) — สำหรับหาช่วงที่
    ต่างของบรรทัดที่เหมือนกันทุกตัวอักษรยกเว้นตัวพิมพ์

    🔑 ใช้ normaliser ตัวเดียวกับที่ชั้นเทียบใช้อยู่แล้ว (จัดการอักขรวิธี
       อาหรับ · เลขอาหรับ-อินดิก · ตัดเครื่องหมาย) — จำเป็นจริง ไม่ใช่การ
       ผ่อนเกณฑ์: OCR สองฝั่งอ่าน ``المكونات:`` กับ ``المكونات :`` ต่างกัน
       แค่ช่องว่างหน้าโคลอน ถ้าไม่ normalize ช่วงคำที่ติดกันจะถูกหักตรงนั้น
       แล้วบรรทัดที่ควรจับคู่ได้จะหลุด (เจอจริงกับบรรทัดส่วนผสมของ PURINA ONE)

    คงตำแหน่งตัวอักษรของคำ **ในสตริงต้นฉบับ** ไว้ เพื่อให้ไฮไลต์ชี้ถูกที่
    """
    out, at = [], 0
    for w in (text or "").split():
        i = (text or "").find(w, at)
        if i < 0:                                       # ไม่ควรเกิด — กันไว้
            continue
        at = i + len(w)
        k = _norm_key_cs(w) if case else _norm_key(w)
        if k:                                           # ข้ามคำที่เป็นเครื่องหมายล้วน
            out.append((k, i, at))
    return out


def line_run_ratio(a: str, b: str) -> float:
    """สัดส่วน "ช่วงคำที่ติดกันยาวที่สุด" ของสองบรรทัด (0..1).

    🔑 ทำไมต้องวัด **ความติดกัน** ไม่ใช่จำนวนคำร่วม — การที่ OCR ตัดบรรทัด
       คนละที่ **รักษาลำดับคำที่ติดกันไว้** ส่วนบรรทัดคนละเรื่องบนฉลาก
       เดียวกันบังเอิญใช้คำซ้ำกันแบบ *กระจาย* (หน่วย ตัวเลข ชื่อแบรนด์)

    วัดบนข้อความจริงหลายภาษา 1,236 คู่ + 400 เคสที่ไม่ควรจับ:
      ช่วงติดกัน ≥ 0.40   → จับคู่ถูก 100% · ผิด 4.8%
      คำร่วมทั้งหมด ≥ 0.40 → จับคู่ถูก 100% · **ผิด 13.8%**
    """
    ka = [w[0] for w in _words(a)]
    kb = [w[0] for w in _words(b)]
    if not ka or not kb:
        return 0.0
    m = SequenceMatcher(None, ka, kb).find_longest_match(0, len(ka), 0, len(kb))
    return m.size / float(min(len(ka), len(kb)))


def _pair_score(a: str, b: str) -> float:
    """คะแนนจับคู่บรรทัด "พบเฉพาะ" สองใบ = ``line_run_ratio`` ·
    ถอยไปวัด **ช่วงอักขระติดกัน** เมื่อระดับคำไม่ผ่าน (26 ก.ย.)

    ที่มา (Friskies รอบ 2 บนสถานี): ความต่างเดียวแตกเป็น 2 การ์ด เพราะ OCR
    สองฝั่งเว้นวรรคต่างกัน (``56g``/``56 g`` · ``표시 (``/``표시(``) ⇒ คำถูก
    หั่นคนละที่ ⇒ ช่วงคำติดกัน 0.36 < 0.40 ทั้งที่อักขระติดกัน 0.93

    ถอยเฉพาะเมื่อระดับคำไม่ผ่าน ⇒ คู่ที่เดิมจับได้ได้คะแนนเดิมทุกตัว ·
    คืน ``TEXT_PAIR_MIN_RUN`` พอดี (ไม่สูงกว่า) ⇒ คู่ระดับคำชนะเสมอ
    วัดแล้ว: คู่ที่ควรจับ 56.7% → 90.1% · คู่ที่ไม่ควรจับเท่าเดิม 1.31%
    """
    w = line_run_ratio(a, b)
    if w >= config.TEXT_PAIR_MIN_RUN or not config.TEXT_PAIR_CHAR_FALLBACK:
        return w
    fa, fb = _norm_key(a).replace(" ", ""), _norm_key(b).replace(" ", "")
    if not fa or not fb:
        return w
    m = SequenceMatcher(None, fa, fb, autojunk=False).find_longest_match(
        0, len(fa), 0, len(fb))
    if (m.size >= config.TEXT_PAIR_CHAR_MIN_LEN
            and m.size / float(min(len(fa), len(fb)))
            >= config.TEXT_PAIR_CHAR_MIN_RUN):
        return config.TEXT_PAIR_MIN_RUN
    return w


def diff_spans(a: str, b: str,
               full_a: str = "", full_b: str = "",
               case: bool = False
               ) -> Tuple[List[List[int]], List[List[int]]]:
    """ช่วง **ตัวอักษร** ที่ต่างกันของสองบรรทัด — สำหรับไฮไลต์บนรายงาน

    ⚠️ **แสดงผลล้วน** — ไม่แตะ ``found``/``reference`` เด็ดขาด เพราะสองค่านั้น
       ถูกใช้ไปค้นหาคำเพื่อวาดกรอบแดงบนภาพ crop (``/crop?hl=``) ⇒ แก้แล้ว
       กรอบแดงจะหายหรือไปโผล่ผิดที่

    ⚠️ **หัว-ท้ายที่เกิดจากการตัดบรรทัดคนละที่ ต้องไม่ถูกไฮไลต์** — ข้อความ
       ท่อนนั้น *มีอยู่* ในอีกฝั่ง แค่ไปอยู่คนละบรรทัด ⇒ ทาแดงเท่ากับชี้ว่า
       "ตรงนี้ต่าง" ทั้งที่ไม่ต่าง (กฎเหล็กข้อ 2). ส่ง ``full_a``/``full_b``
       (ข้อความทั้งแผงของแต่ละฝั่ง) มาด้วยเพื่อให้คัดออกได้
    """
    nk = _norm_key_cs if case else _norm_key
    wa, wb = _words(a, case), _words(b, case)
    if not wa or not wb:
        return [], []
    ka = [w[0] for w in wa]
    kb = [w[0] for w in wb]
    ops = [op for op in SequenceMatcher(None, ka, kb).get_opcodes()
           if op[0] != "equal"]
    key_b, key_a = nk(full_b or ""), nk(full_a or "")

    def keep(text, at_edge, other_key):
        """ช่วงที่ *ติดขอบบรรทัด* และไปโผล่ในอีกฝั่งอยู่แล้ว = แค่ตัดบรรทัดต่าง"""
        if not at_edge or not other_key:
            return True
        k = nk(text)
        return not (k and k in other_key)

    sa, sb = [], []
    for idx, (tag, i1, i2, j1, j2) in enumerate(ops):
        edge = (idx == 0) or (idx == len(ops) - 1)
        if i1 < i2:
            lo, hi = wa[i1][1], wa[i2 - 1][2]
            if keep(a[lo:hi], edge and (i1 == 0 or i2 == len(ka)), key_b):
                sa.append([lo, hi])
        if j1 < j2:
            lo, hi = wb[j1][1], wb[j2 - 1][2]
            if keep(b[lo:hi], edge and (j1 == 0 or j2 == len(kb)), key_a):
                sb.append([lo, hi])
    return sa, sb


def _case_only_defects(gname: str, panels: List[dict],
                       texts: Dict[str, str],
                       existing: Optional[List[dict]] = None) -> List[dict]:
    """บรรทัดที่ **เท่ากันแบบพับตัวพิมพ์ แต่ไม่เท่ากันแบบสนใจตัวพิมพ์**

    เคสจริงที่ระบบเคยพลาด (AvoDerm Master1/Master2):
    ``D-Calcium Pantothenate`` vs ``D-calcium Pantothenate``

    **ทำไมชั้นเทียบหลักมองไม่เห็น** — ด่าน containment ใน ``_vote_panels``
    ยกโทษบรรทัดนี้ (``_norm_flat``/``_norm_key`` พับตัวพิมพ์ทั้งคู่) ⇒ นับว่า
    อีกฝั่งมีบรรทัดนี้แล้ว ⇒ ไม่เข้า ``extra`` เลย

    🔑 **เป็นการ *เพิ่ม* ในที่ที่เดิมเงียบ ไม่ใช่การเปลี่ยนผลเดิม** — ยิงเฉพาะ
       บรรทัดที่ ``_norm_key`` เท่ากัน (= ชั้นหลักถือว่าเหมือนกันแน่นอน) และ
       ข้ามบรรทัดที่ชั้นหลักฟ้องไปแล้ว ⇒ ไม่มี defect เดิมรายการไหนถูกแตะ

    ⚠️ อาหรับ/ไทย/CJK ไม่มีตัวพิมพ์ใหญ่-เล็ก ⇒ ``_norm_key_cs`` เท่ากับ
       ``_norm_key`` โดยธรรมชาติ ⇒ ชั้นนี้เงียบกับสคริปต์เหล่านั้นเสมอ
    """
    # ไฟล์หลัก (doc "a") ก่อน · ฉลากจริงก่อน header — ตัวแรกที่มีบรรทัดนั้น
    # คือฝั่งที่ถูกตรวจ (``found``) ที่เหลือเป็นฝั่งอ้างอิง
    order = sorted(panels,
                   key=lambda z: (0 if z.get("doc", "a") == "a" else 1,
                                  0 if z.get("type") == "panel" else 1))
    seen = set()
    for d in (existing or []):
        for side in ("found", "reference"):
            if d.get(side):
                seen.add((d.get("zone_id"), _norm_key(d[side])))

    # key พับตัวพิมพ์ → โซน → {คีย์สนใจตัวพิมพ์: บรรทัดตามที่ OCR อ่านได้}
    # ⚠️ ต้องเทียบเป็น **ชุดของรูปแบบต่อโซน** ไม่ใช่ไล่ทีละบรรทัด — โซนที่มี
    #    ทั้งสองรูปแบบเหมือนกันทั้งคู่ (บรรทัดซ้ำ) ไม่ใช่ "ไม่ตรงกันระหว่าง panel"
    buckets: Dict[str, Dict[str, Dict[str, str]]] = {}
    for z in order:
        for line in _lines(texts.get(z["id"], "")):
            k = _norm_key(line)
            if k:
                (buckets.setdefault(k, {}).setdefault(z["id"], {})
                 .setdefault(_norm_key_cs(line), line))

    defects: List[dict] = []
    for k, per_zone in buckets.items():
        forms = {zid: set(v) for zid, v in per_zone.items()}
        base_id = next(z["id"] for z in order if z["id"] in per_zone)
        # โซนที่ชุดรูปแบบต่างจากฝั่งที่ถูกตรวจ. ว่าง = ทุกโซนมีชุดเดียวกัน
        # (รวมกรณีโซนหนึ่งมีทั้งสองรูปแบบเหมือนกันทั้งคู่ = บรรทัดซ้ำ ซึ่ง
        # ไม่ใช่ "ไม่ตรงกันระหว่าง panel")
        other_ids = [zid for zid in per_zone
                     if zid != base_id and forms[zid] != forms[base_id]]
        if not other_ids:
            continue
        # ⚠️ ชั้นหลักรายงานบรรทัดนี้ไปแล้วหรือยัง — เกิดขึ้นจริงเมื่อกลุ่มมี
        #    panel เยอะพอที่เสียงข้างมากจะไม่ยกโทษให้ (วัดแล้วที่ 6 panel)
        #    ⇒ ต้องไม่ฟ้องซ้ำ (กติกา "ไม่แตะ defect เดิม")
        if (base_id, k) in seen:
            continue
        base_z = next(z for z in order if z["id"] == base_id)
        oid = other_ids[0]
        # รูปแบบที่ "มีเฉพาะฝั่งนี้" คือสิ่งที่ต้องชี้ให้ผู้ตรวจเห็น
        only_base = [c for c in per_zone[base_id] if c not in per_zone[oid]]
        only_other = [c for c in per_zone[oid] if c not in per_zone[base_id]]
        base_line = per_zone[base_id][
            only_base[0] if only_base else next(iter(per_zone[base_id]))]
        ref_line = per_zone[oid][
            only_other[0] if only_other else next(iter(per_zone[oid]))]
        if _norm_key_cs(base_line) == _norm_key_cs(ref_line):
            continue
        d = _defect(
            "MISMATCH_CASE", base_id,
            f"กลุ่ม {gname}: ตัวพิมพ์ใหญ่-เล็กไม่ตรงกันใน "
            f"{base_z.get('label') or base_id} "
            f"(ตัวอักษรอื่นเหมือนกันทุกตัว)",
            found=base_line, reference=ref_line,
            ref_zone_ids=other_ids)
        # ⚠️ ต้องเทียบแบบ **สนใจตัวพิมพ์** ไม่งั้นหาช่วงที่ต่างไม่เจอเลย
        #    (สองบรรทัดเท่ากันทุกประการเมื่อพับตัวพิมพ์) ⇒ ไฮไลต์แดงจะว่าง
        fs, rs = diff_spans(base_line, ref_line, case=True)
        if fs or rs:
            d["found_spans"], d["ref_spans"] = fs, rs
        defects.append(d)
    return defects


def _num_skeleton(line: str) -> str:
    """คีย์ของบรรทัดที่ตัวเลขทุกหลักถูกแทนด้วย ``#`` — ``20%`` = ``24%``"""
    k = _norm_key(line)
    return re.sub(r"\d", "#", k) if any(c.isdigit() for c in k) else ""


def _pair_numeric_rows(a_rest: List[dict], b_rest: List[dict]) -> list:
    """จับคู่บรรทัดที่ **ต่างกันแค่ตัวเลข** (``20%`` ↔ ``24%``) — ช่วงคำ/
    อักขระติดกันใช้ไม่ได้กับบรรทัดสั้นขนาดนี้ (26 ก.ย., John West: ความ
    ต่างจริงหนึ่งอย่างขึ้นเป็นสองการ์ดที่ไม่บอกว่าคู่กัน)

    ⚠️ จับเฉพาะเมื่อ **ไม่กำกวม**: รูปแบบนั้นเหลือฝั่งละหนึ่งบรรทัดพอดี —
    ตารางที่ต่างหลายแถวรูปแบบเดียวกัน ⇒ ไม่จับ (จับผิดแถว = ชี้ผิดแบบมั่นใจ)
    """
    sa: Dict[str, list] = {}
    sb: Dict[str, list] = {}
    for d in a_rest:
        k = _num_skeleton(d["found"])
        if k:
            sa.setdefault(k, []).append(d)
    for d in b_rest:
        k = _num_skeleton(d["found"])
        if k:
            sb.setdefault(k, []).append(d)
    return [(sa[k][0], sb[k][0]) for k in sa
            if len(sa[k]) == 1 and len(sb.get(k, [])) == 1]


def _pair_cross_doc_extras(gname: str, panels: List[dict],
                           defects: List[dict],
                           texts: Optional[Dict[str, str]] = None) -> List[dict]:
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
        # ── เกณฑ์จับคู่ ────────────────────────────────────────────────
        # ระยะแก้ไข **ทั้งบรรทัด** พังเมื่อ OCR สองฝั่งตัดบรรทัดคนละที่:
        # หัว-ท้ายที่ต่างกันเพราะการตัดบรรทัดกินโควตาจนหมด ทั้งที่เนื้อหา
        # ต่างจริงแค่คำเดียว (วัดบนสถานี: Lev 101 · เพดาน 71 · ต่างจริง
        # "520" vs "510") ⇒ ความต่างจริงหนึ่งอย่างถูกแยกเป็นสองใบที่ไม่ชี้
        # ว่าต่างตรงไหน. ``line_run_ratio`` วัดความติดกันซึ่งการตัดบรรทัด
        # ใหม่รักษาไว้เสมอ (ดู config.TEXT_PAIR_MIN_RUN)
        by_run = config.TEXT_PAIR_BY_RUN
        best, best_s = None, None
        for idx, db in enumerate(b_list):
            if idx in used_b:
                continue
            if by_run:
                sc = _pair_score(da["found"], db["found"])
                better = best_s is None or sc > best_s
            else:
                sc = levenshtein(da["found"].upper(), db["found"].upper())
                better = best_s is None or sc < best_s
            if better:
                best, best_s = idx, sc
        if best is not None:
            db = b_list[best]
            ok = (best_s >= config.TEXT_PAIR_MIN_RUN if by_run else
                  best_s <= max(len(da["found"]), len(db["found"])) // 2)
            if ok:
                used_b.add(best)
                pairs.append((da, db))
    if config.TEXT_PAIR_NUMERIC:
        pairs += _pair_numeric_rows(
            [d for d in a_list if not any(d is p[0] for p in pairs)],
            [d for i, d in enumerate(b_list) if i not in used_b])
    if not pairs:
        return defects

    drop = {id(d) for pair in pairs for d in pair}
    out = [d for d in defects if id(d) not in drop]
    for da, db in pairs:
        d = _defect(
            "MISMATCH_PANELS", prim["id"],
            f"กลุ่ม {gname}: ข้อความบนไฟล์หลักไม่ตรงกับไฟล์อ้างอิง (ชิ้นงาน)",
            found=da["found"], reference=db["found"],
            ref_zone_ids=[ref["id"]])
        # ⚠️ แสดงผลล้วน — ``found``/``reference`` ต้องไม่ถูกแตะ (ถูกใช้ค้นคำ
        #    เพื่อวาดกรอบแดงบนภาพ crop). ช่วงที่ต่างไปอยู่ในคีย์แยกต่างหาก
        tx = texts or {}
        fs, rs = diff_spans(da["found"], db["found"],
                            tx.get(prim["id"], ""), tx.get(ref["id"], ""))
        if fs or rs:
            d["found_spans"], d["ref_spans"] = fs, rs
        out.append(d)
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
        matches = list(_RE_DIGIT_RUN.finditer(text))
        seqs = [m.group().replace(" ", "") for m in matches]
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
            d = _defect(
                "NUMBER_FAIL", zid,
                f"เลขบาร์โค้ด {digits} check digit ไม่ถูกต้อง "
                f"(ตามสูตร GS1 mod-10)",
                found=digits)
            if config.NUMBER_CONTEXT:
                why = not_barcode_reason(text, matches[i].start(),
                                         matches[i].end())
                if why:
                    # ไม่ลบ — ลดเป็น info + บอกเหตุผล ให้คนเห็นว่าระบบข้ามเพราะอะไร
                    d["severity"] = "info"
                    d["why"] = why
            defects.append(d)
    return defects


# ── F1: เลข 12-14 หลักที่ "เห็นชัดว่าไม่ใช่บาร์โค้ด" (25 ก.ย. 2026) ─────────
# ที่มา: ฉลากหลายประเทศพิมพ์เลขทะเบียนบริษัท/เลข CFPR ยาว 12 หลัก เช่น
# ``Nestlé Products Sdn. Bhd. (200201013615)`` · ``SF-CFI2-26-172683205264``
# ⇒ ถูกนับเป็นบาร์โค้ดแล้วฟ้อง check digit ผิดทุกใบ (3 ใน 7 รายการของสถานี)
#
# ⚠️ **อนุรักษ์นิยมโดยตั้งใจ** — วัดแล้วว่ารุ่นที่เข้มกว่า ("ตรวจเฉพาะเลขที่อยู่
#    เดี่ยวบนบรรทัด") ทำให้บาร์โค้ดที่ OCR อ่านปนกับข้อความหลุดการตรวจ
#    ⇒ ที่นี่ข้ามเฉพาะเลขที่มีหลักฐานในบริบทชัดเจนเท่านั้น · บรรทัดที่มีคำ
#    EAN/UPC/GTIN/บาร์โค้ด ⇒ ตรวจเสมอ
_RE_DIGIT_RUN = re.compile(r"\d[\d ]*\d|\d")
_RE_BARCODE_KW = re.compile(
    r"\b(?:EAN|UPC|GTIN|ITF|JAN)\b|BAR\s*CODE|บาร์โค้ด|바코드|条码|條碼|バーコード",
    re.IGNORECASE)
_RE_REG_KW = re.compile(
    r"(?:\bReg(?:istration)?\b|\bNo\b\.?|\bCFPR\b|\bSdn\b|\bBhd\b|\bLtd\b|"
    r"\bTel\b|\bPhone\b|\bFax\b|\bLic(?:ense)?\b|ทะเบียน|โทร|등록|전화|许可|注册)",
    re.IGNORECASE)


def not_barcode_reason(text: str, start: int, end: int) -> str:
    """เหตุผลที่เลขช่วง ``text[start:end]`` ไม่ใช่บาร์โค้ด · ไม่แน่ใจ ⇒ ``""``"""
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    le = len(text) if le < 0 else le
    line = text[ls:le]
    if _RE_BARCODE_KW.search(line):
        return ""                                 # มีคำว่า EAN/GTIN = บาร์โค้ดแน่นอน
    before, after = text[ls:start], text[end:le]
    if before.rstrip().endswith("(") and after.lstrip().startswith(")"):
        return "อยู่ในวงเล็บ — น่าจะเป็นเลขทะเบียน ไม่ใช่บาร์โค้ด"
    if re.search(r"-\s*$", before):
        return "เป็นท้ายของรหัสที่มีขีด — ไม่ใช่บาร์โค้ด"
    if re.match(r"\s*-[A-Za-z0-9]", after):
        return "เป็นต้นของรหัสที่มีขีด — ไม่ใช่บาร์โค้ด"
    if _RE_REG_KW.search(before[-40:]):
        return "ตามหลังคำว่าเลขทะเบียน/เลขที่/โทร — ไม่ใช่บาร์โค้ด"
    return ""


# ── Layer 3: dictionary + brand vocabulary ────────────────────────────

_RE_WORD = re.compile(
    r"[A-Za-zÀ-ÖØ-öø-ÿЀ-ӿ؀-ۿ]+"
    r"(?:['’][A-Za-zÀ-ÖØ-öø-ÿЀ-ӿ؀-ۿ]+)?"
)

# URL/technical tokens ที่ _RE_WORD ตัดออกมาจากที่อยู่เว็บ/อีเมลบนฉลาก —
# ไม่ใช่คำสะกด ไม่ควรฟ้อง SPELL_FAIL (เช่น "https" จาก https://…)
_SPELL_STOPLIST = {"http", "https", "www", "mailto"}

# Scripts whose pyspellchecker dictionary is too weak to *decide* a typo,
# so a failed word must NOT raise SPELL_FAIL (would falsely push the
# verdict to REVIEW) and must NOT get an edit-distance suggestion.
#
# ⚠️ วัดจริงบนฉลาก John West ทั้งสองฉบับ (OCR ด้วย tesseract-ara แล้วกรอง
#    conf >= 85 เหลือคำอาหรับ 69 คำ) — ผลไม่ได้แปลว่า "dict ใช้ไม่ได้":
#      dict รู้จัก 57/69 = 82.6%
#      ไม่รู้จัก 12 คำ  แยกได้เป็น
#        1  คำผิดจริงที่เรากำลังตามหา  (كربوهيدات — รูปที่ถูก كربوهيدرات
#           **อยู่ใน dict**) ⇒ ชั้นนี้คือชั้นเดียวที่มีโอกาสจับมันได้ เพราะ
#           ไฟล์ทั้งสองฉบับพิมพ์คำผิดเหมือนกัน ชั้นเทียบข้ามไฟล์จึงไม่มีทางเห็น
#        4  คำจริงที่ dict ขาด (مهدرجة · للتصنيع = มีคำนำหน้า ل ·
#           المصفى · كوليسترول = ทับศัพท์)
#        7  Tesseract อ่านผิดเอง (المئوية→المثوية ฯลฯ) — ไม่ใช่ความผิด dict
#
#    ⇒ 1 จริง : 4 ปลอม ⇒ **ตัดสินไม่ได้** (ยังคง advisory ต่อไป) แต่
#      **ไม่ใช่ "ไม่มีข้อมูล"** ⇒ ข้อความบน UI ต้องบอกว่า "ไม่อยู่ใน dict"
#      ไม่ใช่ "dict ไม่รองรับ" ซึ่งผู้ตรวจอ่านว่า "ระบบไม่ได้ตรวจ"
#    ⇒ คำแนะนำยังต้องปิดสนิท: พิสูจน์แล้วว่าเดาผิด — "مهدرجة"→"مدرجة"
#      เป็นคนละคำ (Arabic morphology ทำให้ edit-distance 1 ไร้ความหมาย)
#
# These words are surfaced advisory-only in the translate tab and defer to
# the AI column + cross-panel comparison. Maps a script key → Thai name.
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


# ── engine ที่ใช้อ่านข้อความ: กลุ่มไหน "เทียบข้าม engine" ────────────────

def engine_mix_groups(zones: List[dict],
                      ocr_results: List[dict]) -> List[str]:
    """กลุ่มที่เอาข้อความจาก **สอง engine** มาเทียบกัน (text layer ปนกับ OCR).

    ทำไมต้องรู้: ชั้นเทียบข้ามแผงเทียบ *ข้อความ* ตรง ๆ. text layer ให้
    ตัวอักษรเป๊ะระดับ vector แต่ตัดบรรทัดตามลำดับใน content stream ของ PDF
    ส่วน OCR ตัดบรรทัดตามที่ตาเห็นในภาพ ⇒ ความต่างเชิงโครงสร้างของสอง engine
    (การตัดบรรทัด/ระยะห่าง/ลำดับคอลัมน์) จะปนมากับความต่างจริงของงานพิมพ์
    แล้วอ่านแยกกันไม่ออก. เป็น **ข้อมูลประกอบล้วน** — ไม่แตะ defects/verdict
    (ตัวเลือกที่ทำอะไรกับมันจริงคือ ``OCR_GROUP_ENGINE_CONSISTENCY``).

    นับเฉพาะฝั่ง OCR ที่ **อ่านข้อความออกจริง** เพราะโซนที่ OCR ล้มเหลว/ว่าง
    ไม่ได้เข้าไปในการเทียบอยู่แล้ว (``_vote_panels`` ใช้เฉพาะโซนที่มีข้อความ)
    ⇒ ถ้ารายงานว่า "ปน" ทั้งที่ไม่มีการเทียบเกิดขึ้น = คำเตือนที่ไม่มีมูล
    """
    eng = {r["zone_id"]: (r.get("engine") or "") for r in ocr_results}
    txt = {r["zone_id"]: (r.get("text") or "").strip() for r in ocr_results}
    by_group: Dict[str, List[str]] = {}
    for z in zones:
        if z.get("type") == "ignore":
            continue
        g = (z.get("group") or "").strip()
        if not g or z["id"] not in eng:
            continue
        by_group.setdefault(g, []).append(z["id"])
    out = []
    for g, zids in by_group.items():
        pdf_side = any(eng[i] == "pdf-text" and txt[i] for i in zids)
        ocr_side = any(eng[i] not in ("pdf-text", "none") and txt[i]
                       for i in zids)
        if pdf_side and ocr_side:
            out.append(g)
    return sorted(out)


# ── Coverage: ชั้นไหน "ได้ทำงานจริง" กับงานใบนี้ ──────────────────────

def check_coverage(zones: List[dict], ocr_results: List[dict]) -> dict:
    """รายงานว่าชั้นตรวจแต่ละชั้น **ได้ทำงานกับงานใบนี้จริงหรือไม่**.

    ทำไมต้องมี: ผลลัพธ์ "PASS" ไม่ได้แปลว่าตรวจครบ — ชั้นเทียบข้ามแผง
    (MISMATCH_PANELS) จะทำงานก็ต่อเมื่อมีโซนอย่างน้อย 2 โซนที่ ``group``
    ตรงกันและอ่านข้อความออกทั้งคู่. ผู้ใช้ที่ลากหลายโซนบนไฟล์เดียวจะได้
    group อัตโนมัติคนละตัว (A, B, C, …) ⇒ ชั้นนี้ **ไม่เคยทำงานเลย** แล้ว
    รายงานก็ยังขึ้น ✅ PASS เงียบ ๆ. วัดจากเคสจำลองตามงานจริง: ความต่าง
    อย่างน้ำหนักสุทธิ / ประเทศผู้ผลิต / วันหมดอายุ **ไม่มีชั้นไหนจับได้เลย**
    ถ้าไม่มีการเทียบข้ามแผง (0 defect ทั้งที่ของจริงต่างกัน).

    **ห้ามแก้ด้วยการบังคับให้ทุกโซนอยู่ group เดียวกัน** — วัดแล้วเช่นกัน:
    โซนคนละเนื้อหา (ส่วนผสม/ที่อยู่/วันหมดอายุ) ที่ถูกจับรวมกลุ่มเดียว
    ให้ defect ปลอม 6 รายการทันที. ทางที่ถูกคือ **บอกความจริง** ว่าชั้นนี้
    ไม่ได้ทำงาน แล้วให้คนตัดสินใจว่าจะจัดกลุ่มหรือไม่.

    คืน dict ที่ปลอดภัยกับ JSON ล้วน — **advisory 100%** ไม่แตะ defects
    ไม่แตะ verdict ไม่แตะการนับ.
    """
    texts = {r["zone_id"]: (r.get("text") or "") for r in ocr_results}
    active = [z for z in zones if z.get("type") != "ignore"]
    panels = [z for z in active if z.get("type") in ("panel", "header")]
    zooms = [z for z in active if z.get("type") == "zoom"]

    def readable(z):
        return bool(texts.get(z["id"], "").strip())

    # กลุ่มที่ "เทียบได้จริง" = มี panel ที่อ่านออก ≥ 2 โซนใน group เดียวกัน
    by_group: Dict[str, List[dict]] = {}
    for z in panels:
        g = z.get("group") or ""
        if g:
            by_group.setdefault(g, []).append(z)
    votable = {g: m for g, m in by_group.items()
               if len([z for z in m if readable(z)]) >= 2}
    # กลุ่มที่ตั้งไว้ตรงกันแล้ว แต่ยังเทียบไม่ได้เพราะอ่านข้อความไม่ออก
    grouped_unreadable = sorted(
        g for g, m in by_group.items()
        if len(m) >= 2 and len([z for z in m if readable(z)]) < 2)

    if votable:
        cross = {"ran": True, "reason": "ok",
                 "groups": sorted(votable),
                 "zones": sum(len(m) for m in votable.values())}
    elif len(panels) < 2:
        cross = {"ran": False, "reason": "single_zone", "groups": [],
                 "zones": 0}
    elif grouped_unreadable:
        cross = {"ran": False, "reason": "group_unreadable",
                 "groups": grouped_unreadable, "zones": 0}
    else:
        cross = {"ran": False, "reason": "no_shared_group", "groups": [],
                 "zones": 0}

    # zoom: ``check_group_consistency`` เรียก ``_check_zooms`` เมื่อกลุ่มนั้น
    # มี zoom **และ** มี panel ที่อ่านออกอย่างน้อย 1 โซน — สะท้อนเงื่อนไข
    # เดียวกันเป๊ะ ไม่ประมาณ ไม่งั้นรายงาน coverage เองจะกลายเป็นคำตอบที่ผิด
    zoom_groups = sorted({
        g for g in by_group
        if any((z.get("group") or "") == g for z in zooms)
        and any(readable(z) for z in by_group[g])
    })
    if zoom_groups:
        zoom = {"ran": True, "reason": "ok", "zones": len(zooms),
                "groups": zoom_groups}
    else:
        zoom = {"ran": False, "zones": len(zooms), "groups": [],
                "reason": "no_zoom_zone" if not zooms else "no_panel_in_group"}

    n_read = len([z for z in active if readable(z)])
    spell_ok = bool(spell_layer_available())
    # ⚠️ ต้องสะท้อนเงื่อนไขใน ``run_all_checks`` เป๊ะ — ถ้าชั้นนี้ถูกปิดแล้ว
    # ยังรายงานว่า "ทำงาน" คือคำตอบที่ผิดแบบมั่นใจ (กฎเหล็กข้อ 2) และเป็น
    # กับดักเดียวกับที่ docstring ของฟังก์ชันนี้เตือนไว้เอง
    if not config.INSPECT_SPELL_LAYER:
        spelling = {"ran": False, "zones": n_read,
                    "reason": "moved_to_translate"}
    else:
        spelling = {"ran": spell_ok and n_read > 0, "zones": n_read,
                    "reason": ("ok" if spell_ok and n_read
                               else ("spellchecker_missing" if not spell_ok
                                     else "no_readable_zone"))}
    # กลุ่มที่เทียบข้าม engine (text layer ปนกับ OCR) — ความต่างที่เห็นอาจ
    # มาจาก "วิธีอ่าน" ไม่ใช่ "งานพิมพ์". advisory ล้วน ไม่แตะ defects
    mixed = engine_mix_groups(zones, ocr_results)
    return {
        "cross_panel": cross,
        "zoom": zoom,
        "engine_mix": {"groups": mixed, "mixed": bool(mixed)},
        # ชั้นที่ทำงานทุกโซนอยู่แล้ว — รายงานไว้ให้เห็นภาพรวมว่ามีกี่โซนที่
        # มีข้อความให้ตรวจจริง (โซนที่อ่านไม่ออกจะขึ้น UNREADABLE อยู่แล้ว)
        "numbers": {"ran": n_read > 0, "zones": n_read,
                    "reason": "ok" if n_read else "no_readable_zone"},
        "spelling": spelling,
        "zones_total": len(active),
        "zones_readable": n_read,
    }


# ── Orchestrator ──────────────────────────────────────────────────────

def run_all_checks(zones: List[dict], ocr_results: List[dict],
                   vocab_words: Optional[set] = None,
                   vocab_phrases: Optional[List[str]] = None) -> List[dict]:
    texts = {r["zone_id"]: r.get("text", "") for r in ocr_results}
    defects: List[dict] = []
    defects += check_group_consistency(zones, texts)
    defects += check_numbers(zones, texts)
    # ชั้น dictionary ย้ายไปอยู่ที่แท็บ "ข้อความ + คำแปล" เท่านั้น (default) —
    # ปุ่มส่งตรวจตอบเรื่อง "สองไฟล์/สองแผงเหมือนกันไหม" ไม่ใช่เรื่องการสะกด.
    # ⚠️ ``check_spelling`` **ยังต้องอยู่** เพราะแท็บแปลและเทสต์เรียกใช้ตรง ๆ
    # (แท็บแปลมี spell pass ของตัวเองผ่าน ``_get_spellcheckers`` ⇒ การปิดตรงนี้
    #  ไม่กระทบตารางแปลแม้แต่แถวเดียว)
    if config.INSPECT_SPELL_LAYER:
        defects += check_spelling(zones, texts, vocab_words=vocab_words)
    defects += check_phrases(zones, texts, vocab_phrases or [])
    defects += check_readability(zones, ocr_results)
    # ชั้นหลังการตรวจ (25 ก.ย. 2026) — ไม่สร้างรายการใหม่ ไม่ลบรายการใด
    # แค่ลดระดับ/แนบหลักฐานของรายการที่พิสูจน์ได้ว่ามาจาก OCR อ่านเพี้ยน
    # (ดู ``witness.py``) · ปิดธงทั้งสอง = รายการเดิมทุกตัวอักษร
    if config.TEXT_WITNESS or config.FUSED_SCRIPT_NOTE:
        from . import witness as _wit
        if config.TEXT_WITNESS:
            defects = _wit.apply_witness(defects, zones, ocr_results, texts)
        if config.FUSED_SCRIPT_NOTE:
            defects = _wit.mark_fused(defects, ocr_results)
    return defects
