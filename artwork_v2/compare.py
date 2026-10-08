"""เทียบข้อความสองภาพ (A ↔ B) จากบรรทัดที่ ``textmodel.parse`` ประกอบไว้

ขั้นตอน:
1. ทำคีย์ 2 แบบต่อบรรทัด
   * คีย์เทียบ (diff) — NFKC · เลขทุกระบบ → 0-9 · **คงตัวพิมพ์** · คงเครื่องหมาย
     (``1.5g`` กับ ``15g`` ต้องต่างกัน) · ไม่สนช่องว่าง (``4×14`` = ``4 × 14``)
   * คีย์จับคู่ (pair) — พับตัวพิมพ์ · ตัดเครื่องหมาย · ตัวเลข → ``#``
     (ถ้าใช้ตัวเลขจริงจับคู่ ``Fat 1.5g`` กับ ``Fat 15g`` จะไม่ถูกจับคู่ แล้ว
     ความต่างเดียวกลายเป็น "หาย" + "เกิน" สองรายการ — ทีม B วัดไว้)
2. จับคู่บรรทัดแบบ **นับซ้ำ** (บรรทัดหนึ่งใช้ได้ครั้งเดียว) — บรรทัดที่ซ้ำ
   ในแผงอื่นจะไม่กลบบรรทัดที่หายไปอีกต่อไป
   * บรรทัดที่ไม่มีตัวอักษรเลย (ตัวเลข/สัญลักษณ์ล้วน) **จับคู่ด้วยตำแหน่งเท่านั้น**
     ถ้าใช้ข้อความจับ ``##%`` ทุกแถวหน้าตาเหมือนกัน ⇒ ค่าในตารางสลับกันเงียบ ๆ
3. เทียบทีละตัวอักษรในคู่ → ช่วงที่ต่าง + กรอบตัวอักษรจริงทั้งสองฝั่ง
4. แยก "การตัดบรรทัดคนละที่" (reflow) ออกจากความต่างจริง — เช็คกับบรรทัด
   **ข้างเคียง** เท่านั้น ไม่ใช่ทั้งโซน (กันการกลบแบบเดิม)
5. ระดับ: แดง = มั่นใจ · เหลือง = ไม่มั่นใจ/เครื่องหมายวรรคตอน (OCR ไม่นิ่ง)
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

from . import config
from .textmodel import union

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−﹘﹣－"), "-")
_QUOTES = {ord("’"): "'", ord("‘"): "'", ord("‚"): "'", ord("“"): '"',
           ord("”"): '"', ord("„"): '"', ord("´"): "'", ord("`"): "'"}
_DROP = {0x0640, 0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0xFEFF, 0x00AD}

# ── ชั้น A: "อักษรที่หน้าตาเหมือนกัน แต่ OCR ใช้รหัสต่างกัน" ─────────────────
# กติกา: รวมเฉพาะสิ่งที่ **พิมพ์ออกมาเป็นรูปเดียวกันบนฉลาก** และไม่เปลี่ยนความหมาย
# · ห้ามรวมตัวพิมพ์ใหญ่-เล็ก (ของจริงที่ต้องจับ) · ห้ามรวม ² กับ 2 (m² ≠ m2)
# ใช้ **ก่อน** NFKC — ไม่งั้น NFKC เปลี่ยน Ⓡ เป็นตัว "R" ธรรมดา
EQUIV_PRE = {
    0x24C7: "®", 0x24E1: "®",          # Ⓡ ⓡ  (Vision สลับกับ ® ในไฟล์เดียวกัน — วัดแล้ว)
    0x24B8: "©", 0x24D2: "©",          # Ⓒ ⓒ
    0x2022: "•", 0x25CF: "•", 0x2219: "•", 0x30FB: "•", 0x25AA: "•",   # จุดหัวข้อ
}
# ใช้ **หลัง** NFKC — NFKC แตก ½ เป็น "1⁄2" (fraction slash) ต้องตามมาเป็น "/"
EQUIV_POST = {0x2044: "/", 0x2215: "/"}

# ── อักขระตกแต่งที่ซ้ำต่อกัน (จุดไข่ปลา/ขีดเส้น) ─────────────────────────
# OCR นับจำนวนจุดเล็ก ๆ ไม่ได้แน่นอน (วัดจริง: ไฟล์เดียวกันอ่านได้ 10/15 จุด ·
# ซูม 2400 dpi ยังได้ 5/3) ⇒ เช็ค "มี/ไม่มี" แต่ไม่นับจำนวน
FILLER = "\u2026"                       # … ตัวแทน "เส้นตกแต่ง" หนึ่งเส้น
FILLER_RUN = {".": 2, "_": 2, "·": 2, "-": 3}


def _keep_as_is(ch: str) -> bool:
    """ตัวยก/ตัวห้อย/ตัวเลขในวงกลม/ℓ — NFKC จะรวมกับตัวธรรมดา (² → 2) ซึ่งเปลี่ยนความหมาย
    (m² ≠ m2 · H₂O ≠ H2O) · ยกเว้น ™ ℠ (ความหมายเท่ากับ TM/SM) และ ª º (Nº ↔ No —
    Vision สลับสองแบบได้)"""
    if ch in "™℠ªº":
        return False
    if ch == "ℓ":                  # ℓ (ลิตร)
        return True
    dec = unicodedata.decomposition(ch)
    return dec.startswith(("<super>", "<sub>", "<circle>"))


def _norm_char(ch: str) -> str:
    if ord(ch) in _DROP:
        return ""
    ch = EQUIV_PRE.get(ord(ch), ch)      # Ⓡ → ® ก่อน (Ⓡ เป็น <circle> — ห้ามหลุดไปด่านถัดไป)
    if config.KEEP_SUPERSCRIPT and ord(ch) > 0x7F and _keep_as_is(ch):
        return ch
    n = unicodedata.normalize("NFKC", ch)
    out = []
    for c in n:
        if c.isspace():
            out.append(" ")
            continue
        if c.isdigit():
            d = unicodedata.digit(c, None)
            if d is not None:
                c = str(d)
        out.append(c.translate(_DASHES).translate(_QUOTES).translate(EQUIV_POST))
    return "".join(out)


def diff_key_map(text: str) -> Tuple[str, List[int], List[int]]:
    """คีย์เทียบแบบ **ไม่มีช่องว่าง** + ตำแหน่งเริ่ม/จบในข้อความเดิมของแต่ละตัว

    อักขระตกแต่งที่ซ้ำต่อกันถูกยุบเป็น ``FILLER`` ตัวเดียว (จุดไข่ปลาที่ต้นบรรทัด
    นับเป็นเส้นตกแต่งแม้มีจุดเดียว — ไม่มีประโยคไหนขึ้นต้นด้วยจุด) · จุดทศนิยม
    เดี่ยวกลางตัวเลข (``7.0``) ไม่ถูกแตะ
    """
    raw, idx = [], []
    for i, ch in enumerate(text):
        for c in _norm_char(ch):
            if c == " ":
                continue
            raw.append(c)
            idx.append(i)
    key, st, en = [], [], []
    n, k = len(raw), 0
    while k < n:
        c = raw[k]
        if c in FILLER_RUN:
            m = k
            while m < n and raw[m] == c:
                m += 1
            if m - k >= FILLER_RUN[c] or (k == 0 and c == "."):
                if key and key[-1] == FILLER:
                    en[-1] = idx[m - 1] + 1
                else:
                    key.append(FILLER)
                    st.append(idx[k])
                    en.append(idx[m - 1] + 1)
                k = m
                continue
        key.append(c)
        st.append(idx[k])
        en.append(idx[k] + 1)
        k += 1
    return "".join(key), st, en


def diff_key(text: str) -> Tuple[str, List[int]]:
    """คีย์เทียบ + ตำแหน่งเริ่มในข้อความเดิม (รูปแบบเดิม — ใช้ในการอ่านซ้ำ)"""
    k, st, _ = diff_key_map(text)
    return k, st


def line_key_map(ln: dict) -> Tuple[str, List[int], List[int]]:
    """คีย์เทียบของบรรทัด — บรรทัดที่ต่อจากหลายชิ้น (``parts``) ทำคีย์ **ทีละชิ้น** แล้วต่อกัน

    ถ้าทำคีย์จากข้อความที่ต่อแล้ว จุดเดี่ยวที่ต้นชิ้นขวา (``.294`` = จุดไข่ปลาที่ OCR อ่านได้
    จุดเดียว) จะกลายเป็น "จุดกลางบรรทัด" แล้วถูกเทียบเป็นเครื่องหมาย (``SEAM_FILLER``)
    """
    parts = ln.get("parts")
    if not (config.SEAM_FILLER and parts):
        return diff_key_map(ln["text"])
    key: List[str] = []
    st: List[int] = []
    en: List[int] = []
    for off, txt in parts:
        k, s0, e0 = diff_key_map(txt)
        # จุดไข่ปลาที่คร่อมรอยต่อ ("(min)." + "..0.16%") = เส้นเดียวกัน — จุดท้ายชิ้นซ้าย
        # ถูกนับเข้าเส้นตกแต่ง เหมือนตอนทำคีย์จากข้อความที่ต่อแล้ว
        if k.startswith(FILLER) and key and key[-1] in FILLER_RUN:
            key[-1] = FILLER
        for c, a, b in zip(k, s0, e0):
            if c == FILLER and key and key[-1] == FILLER:
                en[-1] = off + b
                continue
            key.append(c)
            st.append(off + a)
            en.append(off + b)
    return "".join(key), st, en


def pair_key(text: str) -> str:
    out = []
    for ch in text:
        for c in _norm_char(ch).casefold():
            if c == " ":
                out.append(" ")
            elif c.isdigit():
                out.append("#")
            elif unicodedata.category(c)[0] in ("L", "M"):
                out.append(c)
            else:
                out.append(" ")
    return " ".join("".join(out).split())


def has_letters(text: str) -> bool:
    return any(unicodedata.category(c)[0] == "L" for c in text)


def _run_ratio(ka: str, kb: str) -> float:
    ta, tb = ka.split(), kb.split()
    if not ta or not tb:
        return 0.0
    m = SequenceMatcher(None, ta, tb, autojunk=False).find_longest_match(0, len(ta), 0, len(tb))
    return m.size / float(min(len(ta), len(tb)))


def similarity(ka: str, kb: str) -> float:
    a, b = ka.replace(" ", ""), kb.replace(" ", "")
    if not a or not b:
        return 0.0
    char = SequenceMatcher(None, a, b, autojunk=False).ratio()
    run = _run_ratio(ka, kb)
    if run >= config.PAIR_MIN_RUN and min(len(ka.split()), len(kb.split())) >= 2:
        return max(char, run)
    return char


def _dist(ca, cb) -> float:
    return ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5


def _prep(lines: List[dict], side: str = "", merges: Optional[list] = None) -> List[dict]:
    rows = _merge_leader_rows(lines, side, merges)
    out = []
    for ln in _join_hyphenated(rows):
        dk, st, en = line_key_map(ln)
        if not dk:
            continue
        out.append(dict(ln, dk=dk, dk_idx=st, dk_end=en, pk=pair_key(ln["text"]),
                        letters=has_letters(ln["text"])))
    return out


# ── ชั้น B: ต่อแถวที่ OCR ตัดตรงเส้นตกแต่ง ────────────────────────────────

def _upright(ln: dict) -> bool:
    a = ln.get("angle")
    return a is None or min(a % 360, 360 - a % 360) <= config.ROW_MAX_ANGLE


def _same_row(a: dict, b: dict) -> bool:
    ba, bb = a.get("box"), b.get("box")
    if not ba or not bb:
        return False
    ha, hb = ba[3] - ba[1], bb[3] - bb[1]
    if ha <= 0 or hb <= 0 or max(ha, hb) > 2.0 * min(ha, hb):
        return False
    ov = min(ba[3], bb[3]) - max(ba[1], bb[1])
    return ov >= 0.5 * min(ha, hb)


def _merge_two(L: dict, M: dict) -> dict:
    chars = list(L["chars"]) + [{"c": " ", "box": None, "conf": None}] + list(M["chars"])
    cc = [c["conf"] for c in chars if c.get("conf") is not None]
    nl, nm = max(1, len(L["text"])), max(1, len(M["text"]))
    ca, cb = L.get("center") or (0.5, 0.5), M.get("center") or (0.5, 0.5)
    off = len(L["text"]) + 1
    parts = list(L.get("parts") or [(0, L["text"])])
    parts += [(off + o, t) for o, t in (M.get("parts") or [(0, M["text"])])]
    out = dict(L)
    out.update(chars=chars, text="".join(c["c"] for c in chars), parts=parts,
               box=union([L.get("box"), M.get("box")]),
               center=((ca[0] * nl + cb[0] * nm) / (nl + nm), (ca[1] * nl + cb[1] * nm) / (nl + nm)),
               conf_mean=(sum(cc) / len(cc)) if cc else None,
               conf_min=min(cc) if cc else None,
               soft_hyphen=M.get("soft_hyphen", False), row_merged=True)
    return out


def _merge_leader_rows(lines: List[dict], side: str = "", merges: Optional[list] = None) -> List[dict]:
    """OCR ตัดแถวตาราง ``Crude Protein (min)........`` | ``..7.0%`` เป็นสองบรรทัด
    ส่วนอีกฝั่งอ่านเป็นบรรทัดเดียว ⇒ ต่อกลับจาก **ตำแหน่งในภาพ** ไม่ใช่จากข้อความ

    ต่อเมื่อครบทุกข้อ (ไม่มีเกณฑ์ระยะห่างตายตัว — ใช้ได้ทุกขนาดภาพ/ตัวอักษร):
    * อยู่แถวเดียวกัน (ซ้อนแนวตั้ง ≥ 50% · ความสูงไม่ต่างเกิน 2 เท่า · ข้อความตั้งตรง)
    * เป็น **เพื่อนบ้านติดกันจริง** ทั้งสองทาง (ไม่มีบรรทัดอื่นคั่นบนแถวนั้น)
    * รอยต่อมี **เส้นตกแต่ง** (ฝั่งซ้ายจบด้วย หรือฝั่งขวาขึ้นต้นด้วยจุดไข่ปลา)
      — เงื่อนไขนี้คือสิ่งที่กันไม่ให้ไปต่อข้ามคอลัมน์ เพราะเส้นตกแต่ง
      มีไว้โยง "ชื่อรายการ" กับ "ค่า" ของแถวเดียวกันเสมอ
    """
    if not config.ROW_MERGE_ENABLED:
        return list(lines)
    rows = list(lines)
    while True:
        keys = [line_key_map(l)[0] for l in rows]
        n = len(rows)

        def right_of(i):
            best = None
            for j in range(n):
                if j == i or not _same_row(rows[i], rows[j]):
                    continue
                gap = rows[j]["box"][0] - rows[i]["box"][2]
                h = rows[i]["box"][3] - rows[i]["box"][1]
                if gap < -0.3 * h:
                    continue
                if best is None or gap < best[0]:
                    best = (gap, j)
            return best[1] if best else None

        def left_of(j):
            best = None
            for i in range(n):
                if i == j or not _same_row(rows[i], rows[j]):
                    continue
                gap = rows[j]["box"][0] - rows[i]["box"][2]
                h = rows[j]["box"][3] - rows[j]["box"][1]
                if gap < -0.3 * h:
                    continue
                if best is None or gap < best[0]:
                    best = (gap, i)
            return best[1] if best else None

        done = False
        for i in range(n):
            L = rows[i]
            if not L.get("box") or not keys[i] or not _upright(L):
                continue
            j = right_of(i)
            if j is None or not keys[j] or not _upright(rows[j]) or left_of(j) != i:
                continue
            if not (keys[i].endswith(FILLER) or keys[j].startswith(FILLER)):
                continue
            if merges is not None:
                merges.append({"side": side, "left": L["text"], "right": rows[j]["text"]})
            rows[i] = _merge_two(L, rows[j])
            del rows[j]
            done = True
            break
        if not done:
            return rows


def _row_neighbor(rows: List[dict], i: int, right: bool) -> Optional[int]:
    """เพื่อนบ้านติดกันบนแถวเดียวกัน (ขวา/ซ้าย) — กติกาเดียวกับ ``_merge_leader_rows``"""
    best = None
    for j in range(len(rows)):
        if j == i or not rows[j].get("box") or not _same_row(rows[i], rows[j]):
            continue
        L, R = (rows[i], rows[j]) if right else (rows[j], rows[i])
        gap = R["box"][0] - L["box"][2]
        h = rows[i]["box"][3] - rows[i]["box"][1]
        if gap < -0.3 * h:
            continue
        if best is None or gap < best[0]:
            best = (gap, j)
    return best[1] if best else None


def _reprep(ln: dict) -> dict:
    dk, st, en = line_key_map(ln)
    return dict(ln, dk=dk, dk_idx=st, dk_end=en, pk=pair_key(ln["text"]),
                letters=has_letters(ln["text"]))


def _filler_piece(L: dict, M: dict) -> dict:
    ca, cb = L.get("center") or (0.5, 0.5), M.get("center") or (0.5, 0.5)
    return {"text": FILLER, "box": None, "center": ((ca[0] + cb[0]) / 2, (ca[1] + cb[1]) / 2),
            "chars": [{"c": FILLER, "box": None, "conf": None, "synthetic": True}]}


def _cross_join(X: List[dict], Y: List[dict], pairs, ux, side: str,
                merges: Optional[list]) -> bool:
    """ต่อแถวที่ฝั่ง ``X`` ถูกตัด โดยใช้ **อีกฝั่งเป็นหลักฐาน** (``CROSS_ROW_JOIN``)

    กรณีจริง: ``Phosphorus (min)`` | ``0.16%`` — Vision ทิ้งจุดไข่ปลากลางแถว ช่องว่างจึง
    กว้างกว่าช่องว่างระหว่างคอลัมน์ (265 vs 84 px) ⇒ ใช้ระยะห่างตัดสินไม่ได้ ⇒ ต่อเมื่อ:
    * บรรทัดคู่ใน ``X`` ขาด "ส่วนหัวหรือท้าย" ที่ ``Y`` มี (``Y`` = ``X`` + ส่วนนั้น พอดี)
    * มีบรรทัด **ไม่มีคู่** ใน ``X`` อยู่ติดกันบนแถวเดียวกัน (เพื่อนบ้านของกันและกัน · ตั้งตรง)
    * ``Y`` มี **เส้นตกแต่งตรงรอยต่อ** (แถวตาราง) — ตำแหน่งนั้นคือที่ Vision ทิ้งจุดไป จึงใส่
      เส้นตกแต่งให้ ``X`` ตรงนั้นด้วย (อักขระสังเคราะห์ ไม่มีกรอบ/ความมั่นใจ)
    * ต่อแล้วคีย์ **เท่ากับ** ``Y`` ทุกตัวอักษร · ไม่เท่า = ไม่ต่อ
    """
    side_i = 0 if side == "A" else 1
    for pr in pairs:
        xi, yi = pr[side_i], pr[1 - side_i]
        kx, ky = X[xi]["dk"], Y[yi]["dk"]
        if len(ky) <= len(kx) or not _upright(X[xi]) or not X[xi].get("box"):
            continue
        if ky.startswith(kx):
            frag, right = ky[len(kx):], True
        elif ky.endswith(kx):
            frag, right = ky[:len(ky) - len(kx)], False
        else:
            continue
        core = frag.strip(FILLER)
        # รอยต่อต้องเป็น **เส้นตกแต่ง** ในอีกฝั่ง (แถวตาราง "ชื่อ……ค่า" ที่ Vision ทิ้งจุดไป) —
        # ไม่ต่อแถวทั่วไป เช่นเลขใต้บาร์โค้ด "0 5290700241 0" ที่อีกฝั่งอ่านตก "0" ตัวหน้า
        seam_fill = frag.startswith(FILLER) if right else frag.endswith(FILLER)
        if not core or not seam_fill:
            continue
        u = _row_neighbor(X, xi, right)
        if u is None or u not in ux or X[u]["dk"] != core or not _upright(X[u]):
            continue
        if _row_neighbor(X, u, not right) != xi:
            continue
        L, M = (X[xi], X[u]) if right else (X[u], X[xi])
        if not (L["dk"].endswith(FILLER) or M["dk"].startswith(FILLER)):
            L = _merge_two(L, _filler_piece(L, M))
        merged = _reprep(_merge_two(L, M))
        if merged["dk"] != ky:
            continue
        merged["cross_joined"] = True
        if merges is not None:
            merges.append({"side": side, "left": (X[xi] if right else X[u])["text"],
                           "right": (X[u] if right else X[xi])["text"],
                           "via": "cross_side", "evidence": Y[yi]["text"]})
        X[xi] = merged
        del X[u]
        return True
    return False


def _median_offset(A, B, pairs) -> Tuple[float, float]:
    """ค่ากลางของ (ตำแหน่ง B − ตำแหน่ง A) จากคู่ที่จับได้ด้วยข้อความ"""
    if not pairs:
        return (0.0, 0.0)
    dx = sorted(B[j]["center"][0] - A[i]["center"][0] for i, j, _, _ in pairs)
    dy = sorted(B[j]["center"][1] - A[i]["center"][1] for i, j, _, _ in pairs)
    return (dx[len(dx) // 2], dy[len(dy) // 2])


def _join_hyphenated(lines: List[dict]) -> List[dict]:
    """ต่อคำที่ถูกตัดท้ายบรรทัด ("Pantothe-" + "nate") เป็นบรรทัดเดียวก่อนเทียบ

    * Vision รุ่นเก่า: break ``HYPHEN`` (ขีดไม่อยู่ในข้อความ) ⇒ ต่อเสมอ
    * Vision รุ่นใหม่: ส่ง "-" จริงมา ⇒ ต่อ **และตัดขีด** เฉพาะเมื่อบรรทัดถัดไป
      ขึ้นต้นด้วยตัวพิมพ์เล็ก — "D-" + "Calcium" (ตัวใหญ่) ยังคงขีดไว้
    """
    out: List[dict] = []
    k = 0
    while k < len(lines):
        ln = lines[k]
        nxt = lines[k + 1] if k + 1 < len(lines) else None
        txt = ln["text"].rstrip()
        first = nxt["text"].lstrip()[:1] if nxt else ""
        soft = ln.get("soft_hyphen")
        dash = txt.endswith("-") and first.islower()
        if nxt and (soft or dash) and first:
            chars = list(ln["chars"])
            while chars and chars[-1]["c"] == " ":
                chars.pop()
            if dash and not soft:
                chars = chars[:-1]
            chars += list(nxt["chars"])
            merged = dict(ln)
            merged.update(chars=chars, text="".join(c["c"] for c in chars),
                          box=union([ln["box"], nxt["box"]]), soft_hyphen=False,
                          joined=True, parts=None)
            out.append(merged)
            k += 2
            continue
        out.append(ln)
        k += 1
    return out


def pair_lines(A: List[dict], B: List[dict]) -> Tuple[List[tuple], List[int], List[int]]:
    """คืน ``(คู่ [(ia, ib, วิธี, คะแนน)], A ที่ไม่มีคู่, B ที่ไม่มีคู่)``"""
    ua, ub = set(range(len(A))), set(range(len(B)))
    pairs: List[tuple] = []

    def take(cands, method):
        cands.sort(key=lambda t: -t[0])
        for score, ia, ib in cands:
            if ia in ua and ib in ub:
                ua.discard(ia)
                ub.discard(ib)
                pairs.append((ia, ib, method, round(score, 4)))

    # 1) ตรงกันทุกตัวอักษร (ไม่สนช่องว่าง) — ใกล้กันก่อน
    by_key: Dict[str, List[int]] = {}
    for j in ub:
        by_key.setdefault(B[j]["dk"], []).append(j)
    cands = []
    for i in ua:
        for j in by_key.get(A[i]["dk"], []):
            # ตัวเลข/สัญลักษณ์ล้วน **ไม่จับคู่ด้วยข้อความเลย** — ไม่งั้น "10%" ของ
            # แถวไขมันไปจับคู่กับ "10%" ของแถวโซเดียม แล้วค่าที่สลับกันหายเงียบ
            if not A[i]["letters"]:
                continue
            d = _dist(A[i]["center"], B[j]["center"])
            cands.append((2.0 - d, i, j))
    take(cands, "exact")

    # 2) มีตัวอักษร → จับคู่ด้วยเนื้อหา (+ ตำแหน่งเป็นตัวตัดสินเมื่อคะแนนใกล้กัน)
    cands = []
    for i in ua:
        if not A[i]["letters"]:
            continue
        for j in ub:
            if not B[j]["letters"]:
                continue
            s = similarity(A[i]["pk"], B[j]["pk"])
            if s >= config.PAIR_MIN_SIM:
                d = _dist(A[i]["center"], B[j]["center"])
                cands.append((s - 0.25 * d, i, j))
    take(cands, "content")

    # 3) ตัวเลข/สัญลักษณ์ล้วน → ตำแหน่งเท่านั้น · ใกล้สุดได้ก่อน
    #    ชดเชยการลากโซนเหลื่อมกันด้วยค่ากลางของระยะเลื่อนจากคู่ที่จับได้แล้ว
    off = _median_offset(A, B, pairs)
    cands = []
    for i in ua:
        if A[i]["letters"]:
            continue
        ca = (A[i]["center"][0] + off[0], A[i]["center"][1] + off[1])
        for j in ub:
            if B[j]["letters"]:
                continue
            d = _dist(ca, B[j]["center"])
            if d <= config.PAIR_MAX_DIST:
                cands.append((1.0 - d, i, j))
    take(cands, "position")
    return pairs, sorted(ua), sorted(ub)


# ── เทียบในคู่ ───────────────────────────────────────────────────────

def _is_punct(s: str) -> bool:
    return bool(s) and all(unicodedata.category(c)[0] in ("P", "S") for c in s)


_FRAC_RE = re.compile(r"(?<![0-9/])(\d)/(\d{1,2})(?![0-9/])")


def _has_vulgar(text: str) -> bool:
    return any("VULGAR FRACTION" in unicodedata.name(c, "") or c == "\u2044" for c in text)


def fraction_confusion(a: dict, b: dict, ops: tuple) -> bool:
    """ความต่างทั้งบรรทัดอธิบายได้ด้วย "เศษส่วนถูกอ่านเป็น ตัวเศษ/ตัวส่วน/หาย" เท่านั้น

    วัดจริง: ½ ตัวเดียวกัน Vision อ่านได้ ``½`` · ``1/2`` · ``2`` · ``1`` · หาย ⇒ แยกจากการแก้งาน
    จริงด้วยข้อความไม่ได้ (``½``→``1`` ของจริงก็หน้าตาเดียวกัน) ⇒ เหลืองเสมอ ให้ตาตัดสิน
    เงื่อนไขแคบ: ต้องมีเศษส่วนจริงบนฉลาก (อักษรเศษส่วน หรือ n/d หลักเดียวที่ n<d) ฝั่งใดฝั่งหนึ่ง
    และเมื่อแทนเศษส่วนด้วยตัวเศษ/ตัวส่วน/ว่าง แล้ว **คีย์ทั้งบรรทัดเท่าอีกฝั่งพอดี**
    """
    i1, i2, j1, j2 = ops
    for X, Y, x1, x2, tx in ((a["dk"], b["dk"], i1, i2, a["text"]),
                             (b["dk"], a["dk"], j1, j2, b["text"])):
        for m in _FRAC_RE.finditer(X):
            if m.end() < x1 or m.start() > x2:
                continue
            n, d = m.group(1), m.group(2)
            if not (_has_vulgar(tx) or int(n) < int(d)):
                continue
            for v in (n, d, ""):
                if X[:m.start()] + v + X[m.end():] == Y:
                    return True
    return False


def _standalone(text: str, s: int, e: int) -> bool:
    """ช่วง ``[s, e)`` เป็นคำเดี่ยว (ช่องว่าง/ต้น-ท้ายบรรทัดทั้งสองข้าง)"""
    return s < e and (s == 0 or text[s - 1].isspace()) and (e >= len(text) or text[e].isspace())


# เครื่องหมายคำพูด/ขีด/ดอกจันที่ OCR ใส่ ๆ หาย ๆ รอบตัวเลข (บาร์โค้ด ``"000171`` · ``054326'``)
QUOTE_MARKS = set("\"'\u201c\u201d\u2018\u2019\u00ab\u00bb|#*")


def classify(a_txt: str, b_txt: str, a_ctx: str, b_ctx: str, standalone: bool = False) -> str:
    if config.QUOTE_PUNCT:
        t = (a_txt + b_txt).strip()
        if t and all(c in QUOTE_MARKS or c.isspace() for c in t):
            return "PUNCT"
    if a_txt and b_txt and a_txt.casefold() == b_txt.casefold():
        return "CASE"
    if any(c.isdigit() for c in a_txt + b_txt):
        return "NUMBER"
    if _is_punct(a_txt + b_txt):
        # เครื่องหมายที่ติดตัวเลข (1.5 vs 15 · 1,000 vs 1.000) = เรื่องตัวเลข
        # · ยกเว้นเครื่องหมายที่เป็นคำเดี่ยว ("6286 • AvoDerm") — ไม่ได้ติดตัวเลขบนฉลาก
        if standalone:
            return "PUNCT"
        if any(c.isdigit() for c in a_ctx + b_ctx):
            return "NUMBER"
        return "PUNCT"
    return "TEXT"


def _span_box(line: dict, s: int, e: int) -> Optional[tuple]:
    """กรอบของตัวอักษรในช่วง ``[s, e)`` · ช่วงว่าง = เส้นบาง ๆ ตรงจุดแทรก"""
    chars = line["chars"]
    if s < e:
        return union(c["box"] for c in chars[s:e])
    left = next((chars[k]["box"] for k in range(s - 1, -1, -1) if chars[k]["box"]), None)
    right = next((chars[k]["box"] for k in range(s, len(chars)) if chars[k]["box"]), None)
    ref = left or right
    if not ref:
        return None
    x = left[2] if left else right[0]
    w = max(2.0, (ref[3] - ref[1]) * 0.15)
    return (x - w / 2, ref[1], x + w / 2, ref[3])


def _word_box(line: dict, s: int, e: int) -> Optional[tuple]:
    """กรอบของ "คำเต็ม" ที่ครอบช่วง ``[s, e)`` — ใช้วาดกรอบบาง ๆ รอบคำบนหน้าเว็บ (แสดงผลล้วน)

    ขยายไปถึงช่องว่างทั้งสองข้างแบบเดียวกับ :func:`_word_at` · ตัวอักษรที่ต่างจริงยังอยู่ใน
    ``box`` (``_span_box``) · หาไม่ได้ (ข้อความกับตัวอักษรไม่ตรงกัน / ไม่มีกรอบ) ⇒ ``None``
    · ช่วงว่าง (จุดแทรก) ⇒ ``None`` เสมอ — คำข้างจุดแทรกไม่ใช่สิ่งที่ต่าง (ครอบมันจะชี้ผิดคำ)
    """
    if s >= e:
        return None
    text = line.get("text") or ""
    chars = line.get("chars") or []
    if len(chars) != len(text):
        return None
    lo = s
    while lo > 0 and not text[lo - 1].isspace():
        lo -= 1
    hi = max(e, s)
    while hi < len(text) and not text[hi].isspace():
        hi += 1
    if lo >= hi:
        return None
    return union(c["box"] for c in chars[lo:hi])


def _span_conf(line: dict, s: int, e: int) -> Optional[float]:
    chars = line["chars"]
    lo, hi = (s, e) if s < e else (max(0, s - 1), min(len(chars), s + 1))
    cs = [c["conf"] for c in chars[lo:hi] if c.get("conf") is not None]
    return min(cs) if cs else None


def _orig_span(line: dict, k0: int, k1: int) -> Tuple[int, int]:
    """ตำแหน่งในคีย์ ``[k0, k1)`` → ตำแหน่งในข้อความเดิม"""
    idx, end_ = line["dk_idx"], line.get("dk_end")
    if k0 < k1:
        return idx[k0], (end_[k1 - 1] if end_ else idx[k1 - 1] + 1)
    if k0 < len(idx):
        return idx[k0], idx[k0]
    end = (end_[-1] if end_ else idx[-1] + 1) if idx else 0
    return end, end


def _word_at(text: str, s: int, e: int) -> str:
    """คำเต็มที่ครอบช่วง ``[s, e)`` — ให้คนอ่านเห็น ``20%`` ไม่ใช่แค่ ``0``"""
    if not text:
        return ""
    lo = s
    while lo > 0 and not text[lo - 1].isspace():
        lo -= 1
    hi = max(e, s)
    while hi < len(text) and not text[hi].isspace():
        hi += 1
    return text[lo:hi]


def _neighbors_key(lines: List[dict], i: int) -> str:
    keys = []
    for k in (i - 1, i + 1):
        if 0 <= k < len(lines):
            keys.append(lines[k]["dk"])
    return "".join(keys)


# เครื่องหมายเบาที่ Vision ใส่ ๆ หาย ๆ ท้ายบรรทัด — การตัดบรรทัดของชิ้นที่เป็นเครื่องหมายเหล่านี้ล้วน
# ใช้กติกาเดิม (วัดบน Friskies 8 ต.ค.: ไม่เว้น ⇒ คอมมาที่ Vision ทิ้งขึ้นเป็นเหลืองเพิ่ม 2 จุด ทั้งที่พิมพ์เหมือนกัน)
_SOFT_MARKS = set(",.;:'\"-·•|")


# ช่องว่างรอพิมพ์ (``PLACEHOLDER``): ``xxxxxx`` · ``XX/XX/XXXX`` · ``xxxx-xx`` · TBD/TBC/TBA
_PH_X = re.compile(r"^[xX]+(?:[-/.:][xX]+)*$")
_PH_WORDS = {"TBD", "TBC", "TBA"}
_PH_STRIP = ",;.:()[]{}\"'"


def _is_placeholder(word: str) -> bool:
    w = (word or "").strip(_PH_STRIP)
    if w.upper() in _PH_WORDS:
        return True
    return bool(_PH_X.match(w)) and sum(c in "xX" for c in w) >= 4


def placeholder_side(word_a: str, word_b: str) -> Optional[str]:
    """ฝั่งที่เป็นช่องว่างรอพิมพ์ ('a'/'b') เมื่ออีกฝั่งเป็นข้อมูลจริง (ตัวอักษร/ตัวเลข ≥ 3 ตัว) · ไม่ใช่ ⇒ None"""
    pa, pb = _is_placeholder(word_a), _is_placeholder(word_b)
    if pa == pb:
        return None
    other = (word_b if pa else word_a).strip(_PH_STRIP)
    if sum(c.isalnum() for c in other) < 3:
        return None
    return "a" if pa else "b"


def _stream_key(lines: List[dict]) -> str:
    return "".join(l["dk"] for l in lines)


def reflow_conserved(text: str, mine: List[dict], other: List[dict], whole: bool = True) -> bool:
    """การตัดบรรทัดคนละที่ "ย้าย" ข้อความไปบรรทัดอื่นเท่านั้น — จำนวนครั้งที่ข้อความนั้นปรากฏ
    ในทั้งโซนจึงต้องเท่ากันสองฝั่ง · ไม่เท่า ⇒ ข้อความหาย/เพิ่มจริง ห้ามทิ้งเป็น reflow
    (``REFLOW_CONSERVE`` · 8 ต.ค.: ``Pack of 12``→``Pack of 1`` เคยได้ PASS เพราะบรรทัดข้างเคียง
    บังเอิญมีเลข 2) · ปิดธง/เครื่องหมายเบาล้วน ⇒ True = กติกาเดิม"""
    if not config.REFLOW_CONSERVE or not text or all(c in _SOFT_MARKS for c in text):
        return True
    # ข้อความที่เป็น "คำเต็ม" ⇒ นับเป็นคำ (ความต่างจริงที่อื่นในโซน เช่น 20%→24% ต้องไม่ทำให้
    # เลข 0 เดี่ยวของบาร์โค้ดที่แค่ย้ายบรรทัดกลายเป็น "หายจริง") · ไม่ใช่คำเต็มทั้งสองฝั่ง ⇒ นับสตริง
    if whole:
        ta, tb = _token_runs(mine, text), _token_runs(other, text)
        if ta or tb:
            return ta == tb
    return _stream_key(mine).count(text) == _stream_key(other).count(text)


def _whole_words(ln: dict, k1: int, k2: int) -> bool:
    """ช่วงคีย์ ``[k1, k2)`` ของบรรทัดตรงกับ "คำเต็ม" (ขอบเป็นช่องว่าง/หัว-ท้ายบรรทัด) หรือไม่"""
    try:
        s, e = _orig_span(ln, k1, k2)
    except Exception:
        return False
    t = ln.get("text") or ""
    return (s <= 0 or t[s - 1].isspace()) and (e >= len(t) or t[e].isspace())


UNBALANCED_NOTE = ("ข้อความนี้เจอในบรรทัดข้างเคียงของอีกฝั่ง (อาจแค่ตัดบรรทัดคนละที่) แต่จำนวนครั้งที่ปรากฏ"
                   "ทั้งโซนไม่เท่ากันสองฝั่ง — อาจหาย/เพิ่มจริง โปรดดูด้วยตา")


def _token_runs(lines: List[dict], text: str) -> int:
    """จำนวนครั้งที่ ``text`` (คีย์เทียบ) ตรงกับ "คำเต็มที่ติดกัน" ในบรรทัดใดบรรทัดหนึ่ง"""
    n = 0
    for ln in lines:
        keys = [diff_key(t)[0] for t in (ln.get("text") or "").split()]
        for i in range(len(keys)):
            acc = ""
            for k in keys[i:]:
                acc += k
                if len(acc) >= len(text):
                    break
            if acc == text and keys[i]:
                n += 1
    return n


def diff_pair(a: dict, b: dict, A: List[dict], B: List[dict],
              ia: int, ib: int) -> Tuple[List[dict], List[dict]]:
    """คืน ``(findings, reflow_notes)`` ของคู่บรรทัดหนึ่งคู่"""
    sm = SequenceMatcher(None, a["dk"], b["dk"], autojunk=False)
    ops = [op for op in sm.get_opcodes() if op[0] != "equal"]
    # รวม op ที่ห่างกันไม่เกิน 1 ตัวอักษร (เช่น "20%"→"24%" ที่ได้ 2 op)
    merged: List[list] = []
    for tag, i1, i2, j1, j2 in ops:
        if merged and i1 - merged[-1][2] <= 1 and j1 - merged[-1][4] <= 1:
            merged[-1][2], merged[-1][4] = i2, j2
            merged[-1][0] = "replace"
        else:
            merged.append([tag, i1, i2, j1, j2])
    finds, reflow = [], []
    na, nb = len(a["dk"]), len(b["dk"])
    for tag, i1, i2, j1, j2 in merged:
        a_frag, b_frag = a["dk"][i1:i2], b["dk"][j1:j2]
        unbalanced = False
        edge = (i1 == 0 and j1 == 0) or (i2 == na and j2 == nb)
        one_sided = not a_frag or not b_frag
        if edge and one_sided:
            raw = a_frag or b_frag
            frag = raw.strip("-")
            other_lines, other_i = (B, ib) if a_frag else (A, ia)
            nb_key = _neighbors_key(other_lines, other_i)
            if frag and (frag in nb_key or frag in nb_key.replace("-", "")):
                if reflow_conserved(frag, A, B,
                                    whole=_whole_words(a if a_frag else b, i1 if a_frag else j1,
                                                       i2 if a_frag else j2)):
                    reflow.append({"side": "A" if a_frag else "B", "text": raw})
                    continue
                unbalanced = True
            # ขีดตัดคำท้ายบรรทัดล้วน ๆ (รุ่นใหม่ของ Vision ส่ง "-" จริงมา)
            if (a_frag or b_frag) == "-" and i2 == na and j2 == nb:
                reflow.append({"side": "A" if a_frag else "B", "text": "-"})
                continue
        sa = _orig_span(a, i1, i2)
        sb = _orig_span(b, j1, j2)
        a_txt, b_txt = a["text"][sa[0]:sa[1]], b["text"][sb[0]:sb[1]]
        a_ctx = a["dk"][max(0, i1 - 1):i2 + 1]
        b_ctx = b["dk"][max(0, j1 - 1):j2 + 1]
        if (FILLER in a_frag + b_frag
                and a_frag.replace(FILLER, "") == b_frag.replace(FILLER, "")):
            cls = "FILLER"          # มี/ไม่มีเส้นตกแต่ง — OCR ไม่นิ่ง จึงไม่ตัดสินเป็นแดง
        elif config.FRACTION_YELLOW and fraction_confusion(a, b, (i1, i2, j1, j2)):
            cls = "FRACTION"
        else:
            alone = False
            if config.SYMBOL_TOKEN and (not a_txt or not b_txt):
                t, sp, ln_ = (a_txt, sa, a) if a_txt else (b_txt, sb, b)
                alone = _is_punct(t.strip()) and _standalone(ln_["text"], *sp)
            cls = classify(a_txt, b_txt, a_ctx, b_ctx, alone)
        ca, cb = _span_conf(a, *sa), _span_conf(b, *sb)
        word_a, word_b = _word_at(a["text"], *sa), _word_at(b["text"], *sb)
        ph = None
        if config.PLACEHOLDER and a_txt and b_txt and cls in ("NUMBER", "TEXT", "CASE"):
            ph = placeholder_side(word_a, word_b)
            if ph:
                cls = "PLACEHOLDER"
        f = {
            "class": cls,
            "word_a": word_a,
            "word_b": word_b,
            "a": {"line": ia, "text": a["text"], "span": list(sa), "frag": a_txt,
                  "box": _span_box(a, *sa), "word_box": _word_box(a, *sa), "conf": ca},
            "b": {"line": ib, "text": b["text"], "span": list(sb), "frag": b_txt,
                  "box": _span_box(b, *sb), "word_box": _word_box(b, *sb), "conf": cb},
        }
        if ph:
            f["placeholder"] = ph
        if unbalanced:
            f["_unbalanced"] = True
        finds.append(f)
    return finds, reflow


def _stream(lines: List[dict]) -> Tuple[str, List[int]]:
    """ต่อคีย์ทุกบรรทัด + ตำแหน่งเริ่มของแต่ละบรรทัด (ใช้หาการตัดบรรทัดข้ามกัน)"""
    s, starts = [], []
    pos = 0
    for ln in lines:
        starts.append(pos)
        k = ln["dk"]
        s.append(k)
        pos += len(k)
    return "".join(s), starts


def _crosses_lines(frag: str, lines: List[dict]) -> bool:
    """``frag`` อยู่ในอีกฝั่งโดย **คร่อมรอยต่อบรรทัด** = แค่ตัดบรรทัดคนละที่"""
    if not frag:
        return False
    stream, starts = _stream(lines)
    bounds = set(starts[1:])
    pos = stream.find(frag)
    while pos >= 0:
        end = pos + len(frag)
        if any(pos < b < end for b in bounds):
            return True
        pos = stream.find(frag, pos + 1)
    return False


def severity(f: dict) -> str:
    """ตัดสินจาก **น้ำหนักหลักฐาน** ไม่ใช่ชนิดของความต่าง

    * ``FILLER`` = เหลืองเสมอ (OCR นับจุดเล็ก ๆ ไม่ได้)
    * ``PUNCT`` = แดงได้เมื่อ ``PUNCT_CAN_FAIL`` และความมั่นใจถึงเกณฑ์ — แต่ต้อง
      ผ่านการอ่านซ้ำแบบซูมก่อนเสมอ (ปิดการอ่านซ้ำ ⇒ ลดเป็นเหลือง ใน pipeline)
    """
    if f["class"] == "PLACEHOLDER":
        # ช่องว่างรอพิมพ์ ↔ ข้อมูลจริง = ต่างแน่นอน แม้ OCR อ่านรหัสฝั่งข้อมูลจริงไม่มั่นใจ · หลักฐานที่ต้อง
        # มั่นใจคือ "ฝั่งนี้เป็น xxxx จริง" ⇒ ใช้ความมั่นใจของฝั่งช่องว่างเทียบ CONF_LOW
        c = (f.get(f.get("placeholder") or "") or {}).get("conf")
        return "red" if config.PLACEHOLDER_RED and c is not None and c >= config.CONF_LOW else "yellow"
    if f["class"] in ("FILLER", "FRACTION"):
        return "yellow"
    if f["class"] == "PUNCT" and not config.PUNCT_CAN_FAIL:
        return "yellow"
    confs = [c for c in (f["a"].get("conf"), f["b"].get("conf")) if c is not None]
    if not confs or min(confs) < config.CONF_FAIL:
        return "yellow"
    return "red"


# ── ข้อความโค้ง/เอียง (ตรา · โลโก้) ──────────────────────────────────────

def _adist(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def dominant_angle(lines: List[dict]) -> float:
    """แนวหลักของโซน (0/90/180/270) ถ่วงด้วยจำนวนตัวอักษร — โซนที่หมุนทั้งโซน
    จึงไม่ถูกนับว่า "เอียง" ทั้งหมด"""
    w: Dict[int, int] = {}
    for ln in lines:
        a = ln.get("angle")
        if a is None:
            continue
        q = int(round(a / 90.0)) * 90 % 360
        w[q] = w.get(q, 0) + max(1, len(ln.get("dk") or ln.get("text") or ""))
    return float(max(w, key=lambda k: (w[k], -k))) if w else 0.0


def _box_gap(a, b) -> float:
    gx = max(0.0, b[0] - a[2], a[0] - b[2])
    gy = max(0.0, b[1] - a[3], a[1] - b[3])
    return max(gx, gy)


def curved_lines(lines: List[dict]) -> set:
    """``_curved_base`` + (``VERTICAL_UPRIGHT``) บรรทัดแนวตั้ง 90°±3° ที่กรอบสูง ≥ 2.5 เท่าของกว้าง
    และยาวกว่าเพื่อนบ้านสั้น = **ข้อความพิมพ์ตั้งตามปกติ** ไม่ใช่ตราโค้ง (เดิม claim แนวตั้ง
    "Free from hydrogenated oils" conf 0.95 เป็นเหลืองทุกรอบ) — บรรทัดสั้นที่เคยถูกดึงเข้ากลุ่ม
    เพราะติดบรรทัดแนวตั้งนั้น ถูกประเมินใหม่"""
    out = _curved_base(lines)
    if not config.VERTICAL_UPRIGHT or not out:
        return out
    dom = dominant_angle(lines)
    keep = set()
    for i in out:
        ln = lines[i]
        a, b = ln.get("angle"), ln.get("box")
        if a is None or not b:
            continue
        w, h = b[2] - b[0], b[3] - b[1]
        dk = ln.get("dk")
        if dk is None:
            dk = diff_key_map(ln.get("text") or "")[0]
        if abs(_adist(a, dom) - 90) <= 3 and h >= 2.5 * w and len(dk) > config.CURVED_NEIGHBOR_MAX_CHARS:
            keep.add(i)
    if not keep:
        return out
    tmp = [dict(l) for l in lines]
    for i in keep:
        tmp[i]["angle"] = dom
    return _curved_base(tmp)


def _curved_base(lines: List[dict]) -> set:
    """ดัชนีบรรทัดที่เป็น "ข้อความโค้ง/เอียง"

    * บรรทัดที่เอียงจากแนวหลักของโซนเกิน ``TILT_ANGLE``
    * + บรรทัด **สั้น** (≤ ``CURVED_NEIGHBOR_MAX_CHARS`` ตัว) ที่ตั้งตรงแต่ติดกับบรรทัด
      ที่เอียง (เช่น "&" บนตราเดียวกัน) — **ไม่ต่อทอด** (บรรทัดตั้งตรงไม่พาบรรทัดอื่นเข้ากลุ่ม)
      และบรรทัดยาวไม่เข้ากลุ่ม (กันข้อความปกติข้างตราถูกยุบเป็น "ข้อความโค้ง")
    """
    dom = dominant_angle(lines)
    tilted = {i for i, ln in enumerate(lines)
              if ln.get("angle") is not None and _adist(ln["angle"], dom) > config.TILT_ANGLE}
    out = set(tilted)
    for i, ln in enumerate(lines):
        if i in tilted or not ln.get("box"):
            continue
        dk = ln.get("dk")
        if dk is None:
            dk = diff_key_map(ln.get("text") or "")[0]
        if len(dk) > config.CURVED_NEIGHBOR_MAX_CHARS:
            continue
        h = ln.get("height") or (ln["box"][3] - ln["box"][1])
        for t in tilted:
            tb = lines[t].get("box")
            if not tb:
                continue
            ht = lines[t].get("height") or (tb[3] - tb[1])
            if _box_gap(ln["box"], tb) <= max(h, ht):
                out.add(i)
                break
    return out


# ── เครื่องหมายเดี่ยวที่ OCR ไม่มั่นใจ (``LOWMARK`` · 8 ต.ค. รอบ 4) ─────────────────────
# ไม่แตะเครื่องหมายที่มีความหมายกับข้อมูล (เชิงอรรถ · เปอร์เซ็นต์ · เครื่องหมายการค้า · หน่วย · เทียบค่า)
LOWMARK_PROTECT = set("*%®©℮#°±<>≤≥‰№")


def is_lowmark(f: dict) -> bool:
    """จุด PUNCT สีเหลืองที่มีเครื่องหมายฝั่งเดียว · Vision มั่นใจตัวเครื่องหมายนั้น < CONF_LOW · ไม่ติดตัวเลข ·
    ไม่ใช่เครื่องหมายใน ``LOWMARK_PROTECT`` · ไม่อยู่บนข้อความโค้ง (การ์ดโค้งต้องครบสมาชิก)

    วัดบน Friskies (8 ต.ค.): ``•`` ความมั่นใจ 0.33 = เส้นประไดคัทที่ขอบโซน (ตรวจด้วยตา) — คอมมาที่ Vision
    มั่นใจ 0.98 ไม่เข้าเงื่อนไข · คอมมาจริงหลัง ``Hwy`` ของ AvoDerm (0.98/0.93) ไม่เข้าเงื่อนไข"""
    if f.get("severity") != "yellow" or f.get("class") != "PUNCT" or f.get("curved"):
        return False
    fa, fb = (f["a"].get("frag") or "").strip(), (f["b"].get("frag") or "").strip()
    if bool(fa) == bool(fb):
        return False
    side = f["a"] if fa else f["b"]
    mark = fa or fb
    conf = side.get("conf")
    if conf is None or conf >= config.CONF_LOW or any(c in LOWMARK_PROTECT for c in mark):
        return False
    t = side.get("text") or ""
    s, e = side.get("span") or (0, 0)
    near_digit = (s > 0 and t[s - 1].isdigit()) or (e < len(t) and t[e].isdigit())
    return not near_digit


LOWMARK_NOTE = ("เครื่องหมายเดี่ยวที่ Vision อ่านไม่มั่นใจ (< %d%%) มีฝั่งเดียว — มักเป็นเส้นขอบ/ลายกราฟิก "
                "ไม่นับในผลตัดสิน (ดูด้วยตาได้)")


def fold_lowmark(findings: List[dict]) -> Tuple[List[dict], List[dict]]:
    """คืน ``(คงไว้, ย้ายไปรายการพับ lowmark)`` — ไม่ลบ · ปิดธง ⇒ ไม่ย้ายอะไร"""
    if not config.LOWMARK:
        return list(findings), []
    keep, fold = [], []
    for f in findings:
        if is_lowmark(f):
            f["lowmark_from"] = f["severity"]
            f["severity"] = "lowmark"
            f.setdefault("notes", []).append(LOWMARK_NOTE % round(config.CONF_LOW * 100))
            fold.append(f)
        else:
            keep.append(f)
    return keep, fold


def collapse_curved(findings: List[dict]) -> List[dict]:
    """ยุบจุดต่างที่ติดธง ``curved`` ของคู่หนึ่งเป็น **การ์ดเดียว** (class ``CURVED``)

    ไม่มีจุดไหนถูกลบ — ทุกจุดอยู่ใน ``members`` ครบพร้อมระดับ/หมายเหตุของตัวเอง ·
    การ์ดเป็นแดงเมื่อมีสมาชิกที่ยังแดงอยู่ (ซึ่งต้องผ่านการอ่านซ้ำยืนยันมาแล้ว)
    """
    members = [f for f in findings if f.get("curved")]
    if not members:
        return findings
    rest = [f for f in findings if not f.get("curved")]

    def side(s):
        texts, seen = [], set()
        for m in members:
            t = (m.get("word_" + s) or m[s].get("frag") or m[s].get("text") or "").strip()
            if t and t not in seen:
                seen.add(t)
                texts.append(t)
        cs = [m[s]["conf"] for m in members if m[s].get("conf") is not None]
        return {"line": None, "text": " · ".join(texts), "span": None, "frag": "",
                "box": union(m[s].get("box") for m in members),
                "word_box": union(m[s].get("word_box") or m[s].get("box") for m in members),
                "conf": min(cs) if cs else None}

    red = [m for m in members if m["severity"] == "red"]
    notes = ["ข้อความโค้ง/เอียง — OCR อ่านไม่นิ่ง ดูด้วยตา (%d จุด)" % len(members)]
    if red:
        notes.append("มี %d จุดที่การอ่านซ้ำแบบซูมยืนยันว่าต่างจริง" % len(red))
    card = {"class": "CURVED", "severity": "red" if red else "yellow",
            "word_a": "", "word_b": "", "a": side("a"), "b": side("b"),
            "pair_method": "group", "pair_score": None, "notes": notes,
            "members": members}
    return rest + [card]


# ── เศษอักขระ / ขอบโซน ──────────────────────────────────────────────────

def _at_edge(box, size) -> bool:
    if not box or not size:
        return False
    W, H = size
    tol = max(3.0, 0.25 * (box[3] - box[1]))
    return box[0] <= tol or box[1] <= tol or box[2] >= W - tol or box[3] >= H - tol


def is_debris(ln: dict, size=None) -> bool:
    """บรรทัดที่ไม่มีตัวอักษรหรือตัวเลขเลย และ (ความมั่นใจต่ำ หรือ ชิดขอบโซน)"""
    if any(c.isalnum() for c in ln.get("text") or ""):
        return False
    conf = ln.get("conf_mean")
    return (conf is not None and conf < config.DEBRIS_CONF) or _at_edge(ln.get("box"), size)


def compare(lines_a: List[dict], lines_b: List[dict],
            size_a=None, size_b=None) -> dict:
    """``size_a``/``size_b`` = ขนาดภาพที่ส่ง (W, H) — ใช้ตัดสิน "ชิดขอบโซน" ของเศษอักขระ
    (ไม่ส่ง = ใช้แค่เกณฑ์ความมั่นใจ)"""
    merges: List[dict] = []
    splits: List[dict] = []
    A, B = _prep(lines_a, "A", merges), _prep(lines_b, "B", merges)
    S = g = gi = None
    if config.GEO_PAIRING or config.RECOMPOSE or config.RELOCATE or config.SPLIT_MERGED:
        from . import structure as S
        g, gi = S.build_geo(A, B)
    if config.SPLIT_MERGED and g is not None:
        for _ in range(30):
            if not (S.split_merged(B, A, g, "B", splits) or S.split_merged(A, B, gi, "A", splits)):
                break
        if splits:
            g, gi = S.build_geo(A, B)

    def _pair():
        if config.GEO_PAIRING and g is not None:
            return S.pair_lines_geo(A, B, g)
        return pair_lines(A, B)

    pairs, ua, ub = _pair()
    if config.CROSS_ROW_JOIN:
        for _ in range(50):
            if not (_cross_join(B, A, pairs, set(ub), "B", merges)
                    or _cross_join(A, B, pairs, set(ua), "A", merges)):
                break
            pairs, ua, ub = _pair()
    if config.RECOMPOSE and g is not None:
        for _ in range(30):
            # บรรทัดที่อธิบายได้แล้วว่าเป็นการตัดบรรทัดต่างกัน ไม่ถูกนำไปต่อ (ไม่กินหลักฐาน)
            refl: List[dict] = []
            for ia, ib, _m, _s in pairs:
                refl += diff_pair(A[ia], B[ib], A, B, ia, ib)[1]

            def explained(i, mine, other, side):
                k = mine[i]["dk"].replace("-", "")
                edge = [e["text"].replace("-", "") for e in refl
                        if e["side"] != side and e["text"].strip("-")]
                return _crosses_lines(mine[i]["dk"], other) or any(k and k in t for t in edge)
            ub_ = {u for u in ub if not explained(u, B, A, "B")}
            ua_ = {u for u in ua if not explained(u, A, B, "A")}
            if not (S.recompose(B, A, gi, ub_, "B", merges)
                    or S.recompose(A, B, g, ua_, "A", merges)):
                break
            pairs, ua, ub = _pair()
    findings: List[dict] = []
    reflow: List[dict] = []
    for ia, ib, method, score in pairs:
        f, r = diff_pair(A[ia], B[ib], A, B, ia, ib)
        for x in f:
            x["pair_method"] = method
            x["pair_score"] = score
        if config.MOVED_TEXT:
            if S is None:
                from . import structure as S
            S.mark_moved(f)
        findings += f
        reflow += r

    relocated: List[dict] = []
    cancelled = {"A": set(), "B": set()}
    if config.RELOCATE and g is not None:
        findings, relocated, cancelled = S.relocate(findings, A, B, ua, ub, g, gi)
        for x in relocated:
            x["severity"] = "moved"
            x["notes"] = ["ข้อความนี้มีอยู่ในอีกฝั่งตรงตำแหน่งเดียวกันบนภาพ (OCR จัดบรรทัดต่างกัน) — "
                          "ไม่นับในผลตัดสิน · อีกฝั่ง: " + (x.get("reloc_into") or "")[:80]]
            x.pop("_cap", None)

    reflow_lines = {"A": [], "B": []}
    for side, uns, mine, other in (("A", ua, A, B), ("B", ub, B, A)):
        # ข้อความที่อีกฝั่ง "เกินมาที่หัว/ท้ายบรรทัด" ซึ่งไปเจอในบรรทัดข้างเคียงของ
        # ฝั่งนี้ = บรรทัดนั้นแค่ถูกตัดไปอยู่บรรทัดถัดไป (เช่น "D-" ⏎ "Calcium")
        edge_texts = [e["text"].replace("-", "") for e in reflow
                      if e["side"] != side and e["text"].strip("-")]
        for i in uns:
            ln = mine[i]
            k = ln["dk"].replace("-", "")
            crossing = i not in cancelled[side] and (
                _crosses_lines(ln["dk"], other) or any(k and k in t for t in edge_texts))
            if i in cancelled[side] or (crossing and reflow_conserved(ln["dk"], mine, other)):
                reflow_lines[side].append(i)
                continue
            box = ln["box"]
            conf = ln.get("conf_mean")
            empty = {"line": None, "text": "", "span": [0, 0], "frag": "", "box": None,
                     "word_box": None, "conf": None}
            this = {"line": i, "text": ln["text"], "span": [0, len(ln["text"])],
                    "frag": ln["text"], "box": box, "word_box": box, "conf": conf}
            f = {"class": "MISSING_IN_B" if side == "A" else "EXTRA_IN_B",
                 "a": this if side == "A" else empty,
                 "b": empty if side == "A" else this,
                 "pair_method": "unpaired", "pair_score": None}
            # บรรทัดเครื่องหมายล้วน/สั้นมาก = OCR ไม่นิ่ง → เหลือง
            f["_short"] = len(ln["dk"]) < 2 or _is_punct(ln["dk"])
            if crossing:
                f["_unbalanced"] = True
            findings.append(f)

    if config.BALANCED_MOVE:
        if S is None:
            from . import structure as S
        S.mark_balanced(findings)
    for f in findings:
        short = f.pop("_short", False)
        if f["class"] == "MOVED":
            sev = "yellow"
        elif f["pair_method"] != "unpaired":
            sev = severity(f)
        else:
            conf = f["a"]["conf"] if f["a"]["conf"] is not None else f["b"]["conf"]
            sev = "yellow" if short or conf is None or conf < config.CONF_FAIL else "red"
        f["severity"] = sev
        f["notes"] = []
        cap = f.pop("_cap", None)
        if cap:
            f["cap"] = cap
            if f["severity"] == "red":
                f["severity"] = "yellow"
            f["notes"].append(S.CAP_NOTE.get(cap, cap))
        if f.pop("_unbalanced", False):
            f["reflow_unbalanced"] = True
            if config.REFLOW_UNBALANCED_YELLOW and f["severity"] == "red" and f["class"] != "PLACEHOLDER":
                f["severity"] = "yellow"
            f["notes"].append(UNBALANCED_NOTE)
        if f["class"] == "PLACEHOLDER":
            ph = f.get("placeholder")
            f["notes"].append("ฝั่ง %s เป็นช่องว่างรอพิมพ์ (%s) แต่ฝั่ง %s เป็นข้อมูลจริง (%s) — ต่างแน่นอน "
                              "ตรวจว่าตั้งใจหรือไม่ (รหัส/เลขทะเบียน/Lot)"
                              % (ph.upper(), f["word_" + ph],
                                 "B" if ph == "a" else "A", f["word_" + ("b" if ph == "a" else "a")]))
        if f["class"] == "FRACTION":
            f["notes"].append("เศษส่วน — Vision อ่านตัวเดียวกันไม่นิ่ง (วัดแล้ว: ½ · 1/2 · 2 · 1 · หาย) "
                              "แยกจากการแก้งานจริงด้วยข้อความไม่ได้ โปรดดูด้วยตา")

    # เศษอักขระ: ทุกบรรทัดที่เกี่ยวข้องต้องเป็นเศษ ⇒ ย้ายไปรายการพับ (ไม่นับเป็นเหลือง)
    debris: List[dict] = []
    debris_lines = {"A": set(), "B": set()}
    if config.DEBRIS_ENABLED:
        keep = []
        for f in findings:
            involved = [(s, L, f[s.lower()]["line"], sz)
                        for s, L, sz in (("A", A, size_a), ("B", B, size_b))
                        if f[s.lower()]["line"] is not None]
            if involved and all(is_debris(L[i], sz) for _, L, i, sz in involved):
                f["severity"] = "debris"
                f["notes"].append("เศษอักขระ / ขอบโซน — ไม่มีตัวอักษรหรือตัวเลข และ"
                                  "ความมั่นใจต่ำหรือชิดขอบโซน (ไม่นับในผลตัดสิน)")
                debris.append(f)
                for s, _, i, _ in involved:
                    debris_lines[s].add(i)
            else:
                keep.append(f)
        findings = keep

    # ข้อความโค้ง/เอียง: ติดธงไว้ · แดงได้เฉพาะเมื่ออ่านซ้ำยืนยัน (ดู pipeline._reread)
    curved = {"A": set(), "B": set()}
    if config.CURVED_GROUP_ENABLED:
        curved = {"A": curved_lines(A), "B": curved_lines(B)}
        for f in findings:
            la, lb = f["a"]["line"], f["b"]["line"]
            if (la is not None and la in curved["A"]) or (lb is not None and lb in curved["B"]):
                f["curved"] = True

    # ตำแหน่งประมาณบนฝั่งที่ไม่พบข้อความ (หายไป/เกินมาฝั่งเดียว) — ซูมไปดูได้ · ไม่ใช้ตัดสินอะไรเลย
    if config.EST_BOX and findings:
        if S is None:
            from . import structure as S
        if g is None:
            g, gi = S.build_geo(A, B)
        if g is not None:
            for f in findings:
                for src, dst, gm, sz in (("a", "b", g, size_b), ("b", "a", gi, size_a)):
                    b = f[src].get("box")
                    if not b or f[dst].get("box") or f[dst].get("text"):
                        continue
                    e = gm.estimate(b, b[3] - b[1])
                    if e is None:
                        continue
                    eb = e["box"]
                    if sz:              # ต้องอยู่บนภาพอีกฝั่งอย่างน้อยครึ่งกรอบ
                        iw = min(eb[2], sz[0]) - max(eb[0], 0)
                        ih = min(eb[3], sz[1]) - max(eb[1], 0)
                        area = max(1e-6, (eb[2] - eb[0]) * (eb[3] - eb[1]))
                        if iw <= 0 or ih <= 0 or iw * ih < 0.5 * area:
                            continue
                    f[dst]["est_box"] = eb
                    f[dst]["est"] = {"spread": e["spread"], "anchors": e["anchors"]}

    def cov(lines, uns, refl):
        total = sum(len(l["dk"]) for l in lines)
        bad = sum(len(lines[i]["dk"]) for i in uns if i not in refl)
        return (1.0 - bad / float(total)) if total else None

    cov_a = cov(A, ua, set(reflow_lines["A"]) | debris_lines["A"])
    cov_b = cov(B, ub, set(reflow_lines["B"]) | debris_lines["B"])
    covs = [c for c in (cov_a, cov_b) if c is not None]
    methods: Dict[str, int] = {}
    for p in pairs:
        methods[p[2]] = methods.get(p[2], 0) + 1
    return {
        "findings": findings,
        "debris": debris,
        "relocated": relocated,
        "curved_lines": {"A": sorted(curved["A"]), "B": sorted(curved["B"])},
        "lines_a": A, "lines_b": B,
        "pairs": [{"a": ia, "b": ib, "method": m, "score": s} for ia, ib, m, s in pairs],
        "unpaired_a": ua, "unpaired_b": ub,
        "reflow_edges": reflow,
        "reflow_lines": reflow_lines,
        "row_merges": merges,
        "row_splits": splits,
        "pair_methods": methods,
        "coverage_a": None if cov_a is None else round(cov_a, 4),
        "coverage_b": None if cov_b is None else round(cov_b, 4),
        "coverage": round(min(covs), 4) if covs else None,
    }
