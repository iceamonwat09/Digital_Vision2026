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

import unicodedata
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

from . import config
from .textmodel import union

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−﹘﹣－"), "-")
_QUOTES = {ord("’"): "'", ord("‘"): "'", ord("‚"): "'", ord("“"): '"',
           ord("”"): '"', ord("„"): '"', ord("´"): "'", ord("`"): "'"}
_DROP = {0x0640, 0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0xFEFF, 0x00AD}


def _norm_char(ch: str) -> str:
    if ord(ch) in _DROP:
        return ""
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
        out.append(c.translate(_DASHES).translate(_QUOTES))
    return "".join(out)


def diff_key(text: str) -> Tuple[str, List[int]]:
    """คีย์เทียบแบบ **ไม่มีช่องว่าง** + แผนที่ตำแหน่งกลับไปยังตัวอักษรเดิม"""
    out, idx = [], []
    for i, ch in enumerate(text):
        for c in _norm_char(ch):
            if c == " ":
                continue
            out.append(c)
            idx.append(i)
    return "".join(out), idx


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


def _prep(lines: List[dict]) -> List[dict]:
    out = []
    for ln in _join_hyphenated(lines):
        dk, idx = diff_key(ln["text"])
        if not dk:
            continue
        out.append(dict(ln, dk=dk, dk_idx=idx, pk=pair_key(ln["text"]),
                        letters=has_letters(ln["text"])))
    return out


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
                          joined=True)
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


def classify(a_txt: str, b_txt: str, a_ctx: str, b_ctx: str) -> str:
    if a_txt and b_txt and a_txt.casefold() == b_txt.casefold():
        return "CASE"
    if any(c.isdigit() for c in a_txt + b_txt):
        return "NUMBER"
    if _is_punct(a_txt + b_txt):
        # เครื่องหมายที่ติดตัวเลข (1.5 vs 15 · 1,000 vs 1.000) = เรื่องตัวเลข
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


def _span_conf(line: dict, s: int, e: int) -> Optional[float]:
    chars = line["chars"]
    lo, hi = (s, e) if s < e else (max(0, s - 1), min(len(chars), s + 1))
    cs = [c["conf"] for c in chars[lo:hi] if c.get("conf") is not None]
    return min(cs) if cs else None


def _orig_span(line: dict, k0: int, k1: int) -> Tuple[int, int]:
    """ตำแหน่งในคีย์ ``[k0, k1)`` → ตำแหน่งในข้อความเดิม"""
    idx = line["dk_idx"]
    if k0 < k1:
        return idx[k0], idx[k1 - 1] + 1
    if k0 < len(idx):
        return idx[k0], idx[k0]
    end = (idx[-1] + 1) if idx else 0
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
        edge = (i1 == 0 and j1 == 0) or (i2 == na and j2 == nb)
        one_sided = not a_frag or not b_frag
        if edge and one_sided:
            raw = a_frag or b_frag
            frag = raw.strip("-")
            other_lines, other_i = (B, ib) if a_frag else (A, ia)
            nb_key = _neighbors_key(other_lines, other_i)
            if frag and (frag in nb_key or frag in nb_key.replace("-", "")):
                reflow.append({"side": "A" if a_frag else "B", "text": raw})
                continue
            # ขีดตัดคำท้ายบรรทัดล้วน ๆ (รุ่นใหม่ของ Vision ส่ง "-" จริงมา)
            if (a_frag or b_frag) == "-" and i2 == na and j2 == nb:
                reflow.append({"side": "A" if a_frag else "B", "text": "-"})
                continue
        sa = _orig_span(a, i1, i2)
        sb = _orig_span(b, j1, j2)
        a_txt, b_txt = a["text"][sa[0]:sa[1]], b["text"][sb[0]:sb[1]]
        a_ctx = a["dk"][max(0, i1 - 1):i2 + 1]
        b_ctx = b["dk"][max(0, j1 - 1):j2 + 1]
        cls = classify(a_txt, b_txt, a_ctx, b_ctx)
        ca, cb = _span_conf(a, *sa), _span_conf(b, *sb)
        finds.append({
            "class": cls,
            "word_a": _word_at(a["text"], *sa),
            "word_b": _word_at(b["text"], *sb),
            "a": {"line": ia, "text": a["text"], "span": list(sa), "frag": a_txt,
                  "box": _span_box(a, *sa), "conf": ca},
            "b": {"line": ib, "text": b["text"], "span": list(sb), "frag": b_txt,
                  "box": _span_box(b, *sb), "conf": cb},
        })
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
    if f["class"] == "PUNCT":
        return "yellow"
    confs = [c for c in (f["a"].get("conf"), f["b"].get("conf")) if c is not None]
    if not confs or min(confs) < config.CONF_FAIL:
        return "yellow"
    return "red"


def compare(lines_a: List[dict], lines_b: List[dict]) -> dict:
    A, B = _prep(lines_a), _prep(lines_b)
    pairs, ua, ub = pair_lines(A, B)
    findings: List[dict] = []
    reflow: List[dict] = []
    for ia, ib, method, score in pairs:
        f, r = diff_pair(A[ia], B[ib], A, B, ia, ib)
        for x in f:
            x["pair_method"] = method
            x["pair_score"] = score
        findings += f
        reflow += r

    reflow_lines = {"A": [], "B": []}
    for side, uns, mine, other in (("A", ua, A, B), ("B", ub, B, A)):
        # ข้อความที่อีกฝั่ง "เกินมาที่หัว/ท้ายบรรทัด" ซึ่งไปเจอในบรรทัดข้างเคียงของ
        # ฝั่งนี้ = บรรทัดนั้นแค่ถูกตัดไปอยู่บรรทัดถัดไป (เช่น "D-" ⏎ "Calcium")
        edge_texts = [e["text"].replace("-", "") for e in reflow
                      if e["side"] != side and e["text"].strip("-")]
        for i in uns:
            ln = mine[i]
            k = ln["dk"].replace("-", "")
            if _crosses_lines(ln["dk"], other) or any(k and k in t for t in edge_texts):
                reflow_lines[side].append(i)
                continue
            box = ln["box"]
            conf = ln.get("conf_mean")
            empty = {"line": None, "text": "", "span": [0, 0], "frag": "", "box": None,
                     "conf": None}
            this = {"line": i, "text": ln["text"], "span": [0, len(ln["text"])],
                    "frag": ln["text"], "box": box, "conf": conf}
            f = {"class": "MISSING_IN_B" if side == "A" else "EXTRA_IN_B",
                 "a": this if side == "A" else empty,
                 "b": empty if side == "A" else this,
                 "pair_method": "unpaired", "pair_score": None}
            # บรรทัดเครื่องหมายล้วน/สั้นมาก = OCR ไม่นิ่ง → เหลือง
            f["_short"] = len(ln["dk"]) < 2 or _is_punct(ln["dk"])
            findings.append(f)

    for f in findings:
        short = f.pop("_short", False)
        if f["pair_method"] != "unpaired":
            sev = severity(f)
        else:
            conf = f["a"]["conf"] if f["a"]["conf"] is not None else f["b"]["conf"]
            sev = "yellow" if short or conf is None or conf < config.CONF_FAIL else "red"
        f["severity"] = sev
        f["notes"] = []

    def cov(lines, uns, refl):
        total = sum(len(l["dk"]) for l in lines)
        bad = sum(len(lines[i]["dk"]) for i in uns if i not in refl)
        return (1.0 - bad / float(total)) if total else None

    cov_a = cov(A, ua, set(reflow_lines["A"]))
    cov_b = cov(B, ub, set(reflow_lines["B"]))
    covs = [c for c in (cov_a, cov_b) if c is not None]
    methods: Dict[str, int] = {}
    for p in pairs:
        methods[p[2]] = methods.get(p[2], 0) + 1
    return {
        "findings": findings,
        "lines_a": A, "lines_b": B,
        "pairs": [{"a": ia, "b": ib, "method": m, "score": s} for ia, ib, m, s in pairs],
        "unpaired_a": ua, "unpaired_b": ub,
        "reflow_edges": reflow,
        "reflow_lines": reflow_lines,
        "pair_methods": methods,
        "coverage_a": None if cov_a is None else round(cov_a, 4),
        "coverage_b": None if cov_b is None else round(cov_b, 4),
        "coverage": round(min(covs), 4) if covs else None,
    }
