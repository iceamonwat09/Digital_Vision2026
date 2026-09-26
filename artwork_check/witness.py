# -*- coding: utf-8 -*-
"""พยานจาก text layer (F2) + คำที่ปนสคริปต์ (F3) — ไม่ import Flask

ที่มา (25 ก.ย. 2026, คู่ Friskies Lil Lickables vector ↔ raster):
โซนของไฟล์ vector ถูกปฏิเสธ text layer (ฟอนต์แมปช่องว่าง/วรรณยุกต์เป็น
อักขระเสีย) แล้วไปอ่านด้วย Gemini — ข้อความที่ถูกทิ้งไป **สะอาด 40/41
บรรทัด** และบอกได้ว่าไฟล์พิมพ์อะไรจริง. เมื่อ OCR ฝั่งนั้นอ่านเพี้ยน
(``리커블`` → ``리커버``) ความต่างที่ขึ้นบนรายงานไม่มีอยู่จริงบนงานพิมพ์

🔑 หลักการตัดสิน (แต่ละข้อคือบั๊กของต้นแบบรุ่นแรกที่ซ่อนของจริงได้):

* ลดระดับได้ก็ต่อเมื่อ **อีกฝั่งตรงกับพยานทั้งบรรทัด** — ไม่ใช่ "คำของอีก
  ฝั่งมีอยู่ที่ไหนสักแห่งในบรรทัดพยาน" (รุ่นแรก ``Nox`` ไปเจอใน
  ``No. xxxxxx`` แล้วซ่อน CFPR ที่ต่างจริง)
* เทียบโดย **คงเครื่องหมายวรรคตอน** ตัดแค่ช่องว่าง/ตัวพิมพ์
* หัว/ท้ายต่างกันได้ **เฉพาะ insert/delete ที่ส่วนเกินไปอยู่ในแผงของอีกฝั่ง**
  (= ตัดบรรทัดคนละที่จริง) · ``replace`` ที่หัว/ท้าย = ความต่างจริง
* พยานที่มีอักขระเสียปน ใช้ได้แบบตัดเป็นท่อน — ตัวคั่นไม่ตรงกับอะไรเลย
  ⇒ ช่วงที่ข้ามจุดเสียไม่มีทางถูกนับว่าตรงกัน

ผลวัด (เล่นซ้ำด้วยข้อความ OCR จริง + ``run_all_checks`` ตัวจริง):
  รอบ 2 ของสถานี ปลอม 3/3 → REVIEW + หลักฐาน · CFPR ยัง critical ·
  ความต่างจริงที่สร้างขึ้น **ชี้ผิด 0/294 · 0/294 · 0/49 · 0/49** ·
  Cosma ฟอนต์พังจริง **0/412**

⚠️ **ไม่ลบรายการใดทิ้ง** — ลดระดับ + แนบหลักฐานให้คนดูด้วยตาเท่านั้น

🔒 กติกาเข้ม (26 ก.ย., ``ARTWORK_TEXT_WITNESS_STRICT`` ค่าเริ่มต้นเปิด) —
รุ่น 25 ก.ย. ยังยกโทษหัว/ท้ายด้วย "ส่วนเกินมีอยู่ที่ไหนสักแห่งในแผง" ⇒
เมื่อ OCR **ทั้งสองฝั่ง** ผิด ลดระดับผิด 65/11,794 เคส (ตัวอักษรเดียวอย่าง
``c`` มีอยู่ทุกแผง ⇒ ``hicken`` = ``Chicken``). รุ่นเข้ม:
* คงตัวพิมพ์ใหญ่-เล็ก (``_flatc``)
* หัว/ท้ายต่างได้เฉพาะส่วนเกินที่อยู่ **ติดกัน** กับช่วงที่ตรงกันในแผงของ
  อีกฝั่ง (บรรทัดเดียวกันหรือข้ามการขึ้นบรรทัดครั้งเดียว) และตัดที่ **ขอบคำ**
  — จุดที่สคริปต์เปลี่ยน (``กรัม제품명``) นับเป็นขอบคำ
* บรรทัดพยานคะแนนเสมอกันหลายบรรทัดที่เนื้อหาต่างกัน (ฉลากหลายรส) = กำกวม
ผลวัด: S3 65 → **0** · S1 0/15,458 · จับ OCR เพี้ยนได้ ~87% (เดิม ~95%) ·
Cosma 0/412 · บนสถานีเท่าเดิมทุกรอบ
"""
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Dict, List, Optional

from . import checks as K
from . import config
from .fonttrust import bad_glyph_count

SEP = "⁣"                  # ตัวคั่นท่อน (INVISIBLE SEPARATOR) — ไม่มีบนฉลาก
MIN_SAME_LINE = 0.40            # = TEXT_PAIR_MIN_RUN (หาบรรทัดพยานที่ใช่)
MIN_CHAR_RUN = 0.50             # ถอยไปวัดช่วงอักขระ (text layer ไม่มีช่องว่าง)
MIN_AGREE_COVER = 0.60          # ช่วงที่ตรงกันต้องครอบอย่างน้อยเท่านี้


# ── พยาน ────────────────────────────────────────────────────────────────

def witness_lines(embedded: str) -> List[str]:
    """บรรทัดของ text layer ที่ใช้เป็นพยานได้ — อักขระเสียถูกแทนด้วยตัวคั่น

    ⚠️ ไม่ทิ้งทั้งบรรทัดที่มีอักขระเสีย: text layer เกาหลีแทนช่องว่างด้วย
    ``U+0001`` **ทุกบรรทัด** ⇒ ทิ้งทั้งบรรทัด = ไม่มีพยานเลย
    """
    out = []
    for ln in (embedded or "").splitlines():
        if not ln.strip():
            continue
        out.append("".join(c if not bad_glyph_count(c) else " %s " % SEP
                           for c in ln))
    return out


def _flat(s: str) -> str:
    return K._norm_key(s or "").replace(" ", "")


def flatp(s: str) -> str:
    """เหมือน ``_flat`` แต่ **คงเครื่องหมายวรรคตอน** — ``No.x`` ≠ ``NOX``"""
    s = unicodedata.normalize("NFKC", s or "").replace(SEP, "")
    return "".join(c for c in s.casefold() if not c.isspace())


def _score(text: str, w: str) -> float:
    """ช่วงคำติดกัน · ถอยไปช่วงอักขระติดกัน (ภาษาที่ text layer ไม่มีช่องว่าง)"""
    w = w.replace(SEP, " ")
    r = K.line_run_ratio(text, w)
    if r >= MIN_SAME_LINE:
        return r
    a, b = _flat(text), _flat(w)
    if not a or not b:
        return 0.0
    m = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(
        0, len(a), 0, len(b))
    return MIN_SAME_LINE if m.size / float(min(len(a), len(b))) >= MIN_CHAR_RUN \
        else 0.0


def _best_first(text: str, lines: List[str]) -> Optional[str]:
    """รุ่น 25 ก.ย. — เลือกบรรทัดคะแนนสูงสุดตัวแรก (เสมอกันก็เลือก)"""
    if not lines or not text:
        return None
    b = max(lines, key=lambda w: _score(text, w))
    return b if _score(text, b) >= MIN_SAME_LINE else None


def _best_unique(text: str, lines: List[str]) -> Optional[str]:
    """บรรทัดพยานที่ใช่ — **เสมอกันหลายบรรทัดที่เนื้อหาต่างกัน = กำกวม ⇒ None**

    ฉลากหลายรสชาติพิมพ์บรรทัดเกือบเหมือนกันซ้ำ (ต่างแค่ชื่อรส/ตัวเลข) ⇒
    เลือกตัวแรกไปเทียบ = อาจเอาพยานของ "อีกรส" มายืนยัน (วัดได้ 1 เคสที่ชี้ผิด)
    """
    if not lines or not text:
        return None
    scored = sorted(((_score(text, w), w) for w in lines), key=lambda x: -x[0])
    top = scored[0][0]
    if top < MIN_SAME_LINE:
        return None
    ties = {_flatc(w) for s, w in scored if s >= top - 1e-9}
    return scored[0][1] if len(ties) == 1 else None


def _best(text: str, lines: List[str]) -> Optional[str]:
    if config.TEXT_WITNESS_STRICT:
        return _best_unique(text, lines)
    return _best_first(text, lines)


# ── เทียบแบบเข้ม (26 ก.ย.): คงตัวพิมพ์ · ขอบต้องเป็นขอบคำจริง ────────────

def _flatc(s: str) -> str:
    """เหมือน ``flatp`` แต่ **คงตัวพิมพ์ใหญ่-เล็ก** (ตัวพิมพ์ต่าง = ต่างจริง)"""
    s = unicodedata.normalize("NFKC", s or "").replace(SEP, "")
    return "".join(c for c in s if not c.isspace())


def _cls(c: str) -> Optional[str]:
    o = ord(c)
    if 0x0E00 <= o <= 0x0E7F:
        return "thai"
    if 0xAC00 <= o <= 0xD7AF or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F:
        return "kor"
    if 0x0600 <= o <= 0x06FF or 0x0750 <= o <= 0x077F:
        return "ara"
    if 0x3040 <= o <= 0x30FF or 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
        return "cjk"
    if c.isalpha():
        return "lat"
    return None


def _bounds(s: str) -> set:
    """ตำแหน่ง (ในสตริง ``_flatc``) ที่เป็น **ขอบคำ**: ช่องว่าง/ตัวคั่น ·
    หัว/ท้าย · และจุดที่ **สคริปต์เปลี่ยน** (text layer ไม่มีช่องว่าง ⇒
    ``กรัม제품명`` คือสองคำ)"""
    s = unicodedata.normalize("NFKC", s or "").replace(SEP, " ")
    out, n = {0}, 0
    for tok in s.split():
        f = "".join(c for c in tok if not c.isspace())
        for k in range(1, len(f)):
            x, y = _cls(f[k - 1]), _cls(f[k])
            if x and y and x != y:
                out.add(n + k)
        n += len(f)
        out.add(n)
    return out


def _lines(panel: str) -> list:
    return [(_flatc(ln), _bounds(ln)) for ln in (panel or "").splitlines()
            if _flatc(ln)]


def _adjacent(ex: str, core: str, pool: list, lead: bool) -> bool:
    """ส่วนเกิน ``ex`` อยู่ **ติดกับ** ช่วงที่ตรงกัน ในบรรทัดเดียวกันหรือข้าม
    การขึ้นบรรทัดครั้งเดียว — และเริ่ม/จบที่ขอบคำ (ไม่ใช่ "มีอยู่ที่ไหนสักแห่ง")"""
    k = min(len(core), 20)
    key = core[:k] if lead else core[-k:]
    cands = list(pool)
    for i in range(1, len(pool)):
        a, ba = pool[i - 1]
        b, bb = pool[i]
        cands.append((a + b, ba | {len(a) + x for x in bb}))
    needle = ex + key if lead else key + ex
    for text, bnd in cands:
        at = text.find(needle)
        while at >= 0:
            if lead and at in bnd:
                return True
            if not lead and (at + len(needle)) in bnd:
                return True
            at = text.find(needle, at + 1)
    return False


def _agree_strict(w: str, o: str, w_panel: str, o_panel: str) -> bool:
    a, b = _flatc(w), _flatc(o)
    if not a or not b:
        return False
    ops = SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    ba, bb = _bounds(w), _bounds(o)
    la, lb = _lines(w_panel), _lines(o_panel)
    core = "".join(a[i1:i2] for t, i1, i2, j1, j2 in ops if t == "equal")

    def ok_edge(op, lead):
        tag, i1, i2, j1, j2 = op
        if tag == "delete":             # พยานมีเกิน → อีกฝั่งพิมพ์ไว้บรรทัดติดกัน
            ex, cut, bnd, pool = a[i1:i2], (i2 if lead else i1), ba, lb
        elif tag == "insert":
            ex, cut, bnd, pool = b[j1:j2], (j2 if lead else j1), bb, la
        else:
            return False                # replace ที่ขอบ = ต่างจริง
        if cut not in bnd:              # ตัดกลางคำ (hicken ↔ chicken) = ต่างจริง
            return False
        return _adjacent(ex, core, pool, lead)

    while ops and ops[0][0] != "equal":
        if not ok_edge(ops[0], True):
            return False
        ops.pop(0)
    while ops and ops[-1][0] != "equal":
        if not ok_edge(ops[-1], False):
            return False
        ops.pop()
    if not ops or any(op[0] != "equal" for op in ops):
        return False
    return sum(op[2] - op[1] for op in ops) >= MIN_AGREE_COVER * min(len(a), len(b))


def _contains(needle: str, hay: str) -> bool:
    """``needle`` (ทั้งบรรทัด) มีอยู่ใน ``hay`` — โหมดเข้ม: ต้องเริ่มและจบที่
    ขอบคำ ในบรรทัดเดียวกันหรือข้ามการขึ้นบรรทัดครั้งเดียว"""
    if not config.TEXT_WITNESS_STRICT:
        f = flatp(needle)
        return bool(f) and f in flatp(hay)
    f = _flatc(needle)
    if not f:
        return False
    pool = _lines(hay)
    cands = list(pool)
    for i in range(1, len(pool)):
        a, ba = pool[i - 1]
        b, bb = pool[i]
        cands.append((a + b, ba | {len(a) + x for x in bb}))
    for text, bnd in cands:
        at = text.find(f)
        while at >= 0:
            if at in bnd and (at + len(f)) in bnd:
                return True
            at = text.find(f, at + 1)
    return False


def agree(w: str, o: str, w_panel: str, o_panel: str) -> bool:
    """``o`` ตรงกับพยาน ``w`` ทุกตัวอักษรในช่วงที่ทับกัน — ต่างได้เฉพาะหัว/ท้าย
    ที่เป็นการตัดบรรทัดจริง

    โหมดเข้ม (``TEXT_WITNESS_STRICT``, ค่าเริ่มต้น): คงตัวพิมพ์ · ส่วนเกินต้อง
    อยู่ **ติดกัน** ในแผงของอีกฝั่งและตัดที่ขอบคำ. ปิด = รุ่น 25 ก.ย. เป๊ะ
    ("ส่วนเกินมีอยู่ที่ไหนสักแห่งในแผง" — วัดแล้วซ่อนของจริงได้เมื่อ OCR ของ
    ทั้งสองฝั่งผิด 65/11,794 เคส)"""
    if config.TEXT_WITNESS_STRICT:
        return _agree_strict(w, o, w_panel, o_panel)
    return _agree_loose(w, o, w_panel, o_panel)


def _agree_loose(w: str, o: str, w_panel: str, o_panel: str) -> bool:
    """รุ่น 25 ก.ย. (``ARTWORK_TEXT_WITNESS_STRICT=0``)"""
    a, b = flatp(w), flatp(o)
    if not a or not b:
        return False
    ops = SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    fa, fb = flatp(w_panel), flatp(o_panel)

    def ok_edge(op):
        tag, i1, i2, j1, j2 = op
        if tag == "delete":
            return a[i1:i2] in fb           # พยานมีเกิน → อีกฝั่งพิมพ์ไว้ที่อื่น
        if tag == "insert":
            return b[j1:j2] in fa
        return False                        # replace ที่ขอบ = ต่างจริง

    while ops and ops[0][0] != "equal":
        if not ok_edge(ops[0]):
            return False
        ops.pop(0)
    while ops and ops[-1][0] != "equal":
        if not ok_edge(ops[-1]):
            return False
        ops.pop()
    if not ops or any(op[0] != "equal" for op in ops):
        return False
    return sum(op[2] - op[1] for op in ops) >= MIN_AGREE_COVER * min(len(a), len(b))


def _show(w: str) -> str:
    return w.replace(" %s " % SEP, "▯").replace(SEP, "▯").strip()


def _side_misread(mine: str, other: str, lines: List[str],
                  mine_panel: str, other_panel: str) -> Optional[dict]:
    """ไฟล์ของฝั่ง ``mine`` พิมพ์ **เหมือนอีกฝั่งทุกตัวอักษร** แต่ OCR ของฝั่งนี้
    อ่านได้ต่างไป ⇒ คืนหลักฐาน · ไม่แน่ใจ ⇒ ``None``"""
    w = _best(mine, lines)
    if not w:
        return None
    wp = "\n".join(lines)
    if agree(w, mine, wp, mine_panel):
        return None                         # OCR ฝั่งนี้อ่านตรงกับไฟล์ = ต่างจริง
    if not agree(w, other, wp, other_panel):
        return None                         # กำกวม — ไม่ตัดสิน
    sm, so = K.diff_spans(mine, other, mine_panel, other_panel)
    return {"file_says": _show(w),
            "ocr_says": [mine[x:y] for x, y in sm],
            "file_diff": [other[x:y] for x, y in so]}


def judge(d: dict, wit: Dict[str, List[str]], texts: Dict[str, str],
          partner: str) -> Optional[dict]:
    """คืนหลักฐานเมื่อ **พิสูจน์ได้** ว่าการ์ดนี้เกิดจาก OCR อ่านเพี้ยน"""
    z = d["zone_id"]
    f, r = d.get("found") or "", d.get("reference") or ""
    if f and r:                                     # การ์ดคู่ (พบ / เทียบกับ)
        for side, mine, other, oz in ((z, f, r, partner), (partner, r, f, z)):
            if side in wit:
                j = _side_misread(mine, other, wit[side],
                                  texts.get(side, ""), texts.get(oz, ""))
                if j:
                    return dict(j, zone=side, kind="pair")
        return None
    if f and not r:                                 # การ์ด "พบเฉพาะใน z"
        if z in wit:
            w = _best(f, wit[z])
            if (w and not agree(w, f, "\n".join(wit[z]), texts.get(z, ""))
                    and _contains(w, texts.get(partner, ""))):
                return {"zone": z, "kind": "extra_here", "file_says": _show(w),
                        "ocr_says": [f], "file_diff": []}
        if partner in wit and flatp(f):
            for w in wit[partner]:
                if _contains(f, w):
                    return {"zone": partner, "kind": "extra_there",
                            "file_says": _show(w), "ocr_says": [],
                            "file_diff": []}
    return None


def _pairs(zones: List[dict]) -> Dict[str, str]:
    """โซน → โซนคู่ เฉพาะกลุ่มที่มี panel/header **2 โซนพอดี**

    กลุ่มที่ใหญ่กว่านั้นตัดสินด้วยเสียงข้างมาก — "อีกฝั่งตรงกับพยาน" มีได้
    หลายความหมาย ⇒ ไม่แตะ (ยังไม่ได้วัด)
    """
    groups: Dict[str, List[str]] = {}
    for z in zones:
        if z.get("type", "panel") in ("panel", "header") and z.get("group"):
            groups.setdefault(z["group"], []).append(z["id"])
    out = {}
    for ids in groups.values():
        if len(ids) == 2:
            out[ids[0]], out[ids[1]] = ids[1], ids[0]
    return out


def apply_witness(defects: List[dict], zones: List[dict],
                  ocr_results: List[dict], texts: Dict[str, str]) -> List[dict]:
    """ลดระดับ ``MISMATCH_PANELS`` ที่พิสูจน์ได้ว่ามาจาก OCR อ่านเพี้ยน

    ไม่มีพยานสักโซน ⇒ คืนรายการเดิม (ตัวเดียวกัน) — ไฟล์ปกติไม่ถูกแตะเลย
    """
    wit = {r["zone_id"]: witness_lines(r.get("witness"))
           for r in ocr_results if r.get("witness")}
    wit = {k: v for k, v in wit.items() if v}
    if not wit:
        return defects
    pairs = _pairs(zones)
    out = []
    for d in defects:
        if d.get("class") == "MISMATCH_PANELS":
            p = pairs.get(d["zone_id"])
            refs = d.get("ref_zone_ids") or []
            if p and (not refs or refs == [p]):
                ev = judge(d, wit, texts, p)
                if ev:
                    d = dict(d, severity=config.TEXT_WITNESS_SEVERITY,
                             witness=ev)
        out.append(d)
    return out


# ── F3: คำเดียวที่ปนอักษรไทยกับสคริปต์ที่ไม่ใช่ละติน ─────────────────────

def _script(ch: str) -> Optional[str]:
    o = ord(ch)
    if 0x0E01 <= o <= 0x0E5B:
        return "thai"
    if 0xAC00 <= o <= 0xD7AF or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F:
        return "hangul"
    if 0x3040 <= o <= 0x30FF:
        return "kana"
    if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
        return "han"
    if 0x0600 <= o <= 0x06FF or 0x0750 <= o <= 0x077F:
        if unicodedata.category(ch).startswith("L"):
            return "arabic"
    return None


_SCRIPT_TH = {"hangul": "เกาหลี", "kana": "ญี่ปุ่น", "han": "จีน",
              "arabic": "อาหรับ"}


def fused_tokens(text: str) -> List[str]:
    """คำ (คั่นด้วยช่องว่าง) ที่มีอักษรไทย **และ** ฮันกึล/คานะ/ฮั่น/อาหรับ"""
    out = []
    for tok in (text or "").split():
        s = {_script(c) for c in tok}
        if "thai" in s and s & set(_SCRIPT_TH):
            if tok not in out:
                out.append(tok)
    return out


def fused_note(text: str) -> str:
    toks = fused_tokens(text)
    if not toks:
        return ""
    other = sorted({_SCRIPT_TH[_script(c)] for t in toks for c in t
                    if _script(c) in _SCRIPT_TH})
    return ("พบคำที่อักษรไทยติดกับอักษร%s ในคำเดียว (%s) — มักเกิดจาก OCR "
            "แปลคำแทนการอ่าน โปรดดูภาพประกอบ"
            % ("/".join(other), ", ".join("“%s”" % t for t in toks[:3])))


def mark_fused(defects: List[dict], ocr_results: List[dict]) -> List[dict]:
    """ติดโน้ต ``fused`` บนการ์ดที่ข้อความ **จาก OCR** มีคำปนสคริปต์

    ไม่แตะระดับความรุนแรง · ข้อความจาก text layer ไม่นับ (text layer เกาหลี
    ไม่มีช่องว่าง ⇒ ``กรัม제품명`` ติดกันเป็นเรื่องปกติของไฟล์ ไม่ใช่การแปล)
    """
    ocr_zone = {r["zone_id"] for r in ocr_results
                if r.get("engine") not in ("pdf-text", "none")}
    out = []
    for d in defects:
        if str(d.get("class", "")).startswith("MISMATCH"):
            refs = d.get("ref_zone_ids") or []
            parts = []
            if d.get("zone_id") in ocr_zone:
                parts.append(d.get("found") or "")
            if refs and refs[0] in ocr_zone:
                parts.append(d.get("reference") or "")
            toks = fused_tokens(" ".join(parts))
            if toks:
                d = dict(d, fused=toks)
        out.append(d)
    return out
