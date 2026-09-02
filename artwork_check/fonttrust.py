"""
ความน่าเชื่อถือของ PDF text layer ระดับ **ฟอนต์** ไม่ใช่ระดับ **โซน**.

ทำไมต้องมีชั้นนี้ (วัดจากไฟล์จริง 2 ฉบับของงานเดียวกัน — Cosma Schlemmerbox):

1. **ความเสียหายเกาะอยู่กับฟอนต์ ไม่ใช่กระจายทั้งไฟล์**

       ฉบับ A4 : DINPro-Bold   66/396 span เสีย   · อีก 12 ฟอนต์ = 0%
       ฉบับ Original: ทุกฟอนต์ = 0%

2. **ฟอนต์ที่พังตัวเดียวกันคายขยะได้ 2 แบบ และแบบที่สองตรวจจากตัวข้อความไม่ได้เลย**

       [span 0] 'DOPLNKOVÉ KRMIVO PRE DOSPELÉ MA'   ← อักขระต้องห้าม 0 ตัว
       [span 1] 'Ï/='                                ← อักขระต้องห้าม 0 ตัว
                 ของจริงคือ "ČKY"

   `Ï`, `/`, `=` เป็นอักขระที่ถูกต้องตาม Unicode ทุกตัว ⇒ ด่านอักขระต้องห้าม
   (`ocr.garbled_reason`) จับไม่ได้ และ**ไม่มีกฎเชิงอักขระแบบไหนจับได้**
   ถ้าดูเฉพาะข้อความก้อนนั้นโดด ๆ

⇒ ทางออกคือ **ให้หลักฐานแพร่ต่อ**: ฟอนต์ที่ถูกพิสูจน์ว่าพัง "ที่ไหนสักแห่ง" ใน
เอกสาร ต้องไม่ถูกเชื่อในที่อื่นของเอกสารเดียวกันด้วย.

โมดูลนี้ไม่ import Flask/PyMuPDF — รับ span ที่อ่านมาแล้วเข้ามา จึงเทสต์ได้ตรง ๆ
"""

from __future__ import annotations

import unicodedata
from typing import Dict, Iterable, List

# ── อักขระที่ "เป็นไปไม่ได้ในข้อความจริง" ────────────────────────────────
# Cc = อักขระควบคุม · Co = Private Use · Cs = surrogate · Cn = ยังไม่กำหนด
# ⚠️ **ห้ามใส่ "Cf" เด็ดขาด** — ZWJ/ZWNJ/RLM/LRM เป็นของปกติในข้อความ
#    อาหรับ/ฮีบรู ใส่เข้าไป = ฟ้องผิดทุกฉลากที่มีภาษาเหล่านั้น
_BAD_CATEGORIES = frozenset(("Cc", "Co", "Cs", "Cn"))
_REPLACEMENT_CHAR = "�"
# อักขระควบคุมที่เป็น "ช่องว่าง" ตามปกติของข้อความ — ไม่ใช่ร่องรอยความเสียหาย
_ALLOWED_CONTROL = "\t\n\r\f\v"

# โหมดความเข้มของการแพร่หลักฐาน (วัดบนไฟล์จริง — ดูตารางใน CLAUDE.md)
#   off      = ไม่ใช้ชั้นนี้เลย (พฤติกรรมก่อน 1 ก.ย. 2026)
#   chars    = ปฏิเสธเฉพาะ span ของฟอนต์ที่พัง ที่มี "อักขระซึ่งฟอนต์ที่ไม่มี
#              ร่องรอยเสียหายในไฟล์เดียวกันไม่เคยใช้เลย"  ← แม่นที่สุดที่วัดได้
#   nonascii = ปฏิเสธ span ของฟอนต์ที่พังที่มีอักขระ non-ASCII
#   font     = ปฏิเสธทุก span ของฟอนต์ที่พัง (เข้มสุด แพงสุด)
MODES = ("off", "chars", "nonascii", "font")


def bad_glyph_count(text: str) -> int:
    """จำนวนอักขระที่ "ไม่มีทางเป็นข้อความจริง" ใน ``text`` (นับทุกครั้งที่พบ)."""
    if not text:
        return 0
    n = 0
    for ch in text:
        if ch in _ALLOWED_CONTROL:
            continue
        if ch == _REPLACEMENT_CHAR or \
                unicodedata.category(ch) in _BAD_CATEGORIES:
            n += 1
    return n


def bad_glyph_sample(text: str, limit: int = 4) -> str:
    """ตัวอย่างอักขระต้องห้ามที่พบ เป็นรหัส U+XXXX (ไม่ซ้ำ เรียงตามที่พบ)."""
    seen, out = set(), []
    for ch in text or "":
        if ch in _ALLOWED_CONTROL or ch in seen:
            continue
        if ch == _REPLACEMENT_CHAR or \
                unicodedata.category(ch) in _BAD_CATEGORIES:
            seen.add(ch)
            out.append("U+%04X" % ord(ch))
            if len(out) >= limit:
                break
    return ", ".join(out)


def analyze(spans: Iterable[dict], mode: str = "chars",
            unmappable_fonts: Iterable[str] = ()) -> dict:
    """สรุปความน่าเชื่อถือของแต่ละฟอนต์จาก span ทั้งเอกสาร.

    ``spans`` = ``[{"font": str, "text": str}, ...]`` (ลำดับไม่สำคัญ)

    คืน::

        {"mode": "chars",
         "suspect": ["DINPro-Bold"],       # ฟอนต์ที่เชื่อไม่ได้
         "why": {"DINPro-Bold": ["chars", "structure"]},
         "poisoned": "ÄÏÜĊċ…",             # อักขระที่ฟอนต์ที่เชื่อได้ไม่เคยใช้
         "fonts": {ชื่อฟอนต์: {spans, bad_spans, chars, bad_chars}}}

    **ฟอนต์ถูกจัดว่า "เชื่อไม่ได้" ด้วยหลักฐาน 2 ทางที่เป็นอิสระจากกัน:**

    ① **จากอาการ** (``chars``) — คายอักขระต้องห้ามออกมาที่ใดที่หนึ่งในเอกสาร.
       แทบผิดพลาดไม่ได้ (อักขระควบคุม/PUA ไม่มีเหตุผลจะอยู่บนฉลาก) แต่ต้อง
       "รอให้เห็นอาการ" ⇒ ฟอนต์ที่พังแบบคายเฉพาะตัวอักษรที่ดูปกติจะรอด

    ② **จากโครงสร้างไฟล์** (``structure``, ``unmappable_fonts``) — ฟอนต์ที่
       ตามสเปก PDF แล้ว **ถอดกลับเป็น Unicode ไม่ได้** (CID/Identity-H ที่ไม่มี
       ToUnicode). ไม่ต้องรออาการเลย และเป็นการชี้ที่ *ต้นเหตุ* ไม่ใช่ *ผลลัพธ์*

    วัดบนไฟล์จริง (833 บรรทัด): ①  จับ 58/70 · ①+② จับ **70/70** โดย
    ฟ้องผิดเท่ากัน (10) — ② จึงเพิ่ม recall ฟรีโดยไม่เสีย precision
    """
    if mode not in MODES:
        mode = "off"
    stats: Dict[str, dict] = {}
    for sp in spans:
        font = (sp.get("font") or "?").strip()
        text = sp.get("text") or ""
        d = stats.setdefault(font, {"spans": 0, "bad_spans": 0,
                                    "chars": 0, "bad_chars": 0})
        d["spans"] += 1
        d["chars"] += len(text)
        nb = bad_glyph_count(text)
        if nb:
            d["bad_spans"] += 1
            d["bad_chars"] += nb

    by_symptom = {f for f, d in stats.items() if d["bad_spans"]}
    # ⚠️ ชื่อฟอนต์ใน span ไม่มี subset prefix (`DINPro-Bold`) ส่วนชื่อจาก
    #    ตารางฟอนต์มี (`DBRRPG+DINPro-Bold`) — ฝั่งเรียกเป็นคนตัดให้แล้ว
    by_structure = {(f or "").strip() for f in unmappable_fonts if (f or "").strip()}
    suspect = sorted(by_symptom | by_structure)
    why = {}
    for f in suspect:
        tags = []
        if f in by_symptom:
            tags.append("chars")
        if f in by_structure:
            tags.append("structure")
        why[f] = tags
    out = {"mode": mode, "suspect": suspect, "why": why,
           "poisoned": "", "fonts": stats}
    if mode == "off" or not suspect:
        # ไม่มีฟอนต์ไหนมีหลักฐานว่าพัง ⇒ ไฟล์นี้ไม่ถูกแตะเลย (ราคา 0)
        return out

    if mode == "chars":
        # อักขระที่ **ฟอนต์ซึ่งไม่มีร่องรอยเสียหายเลย** ในไฟล์เดียวกันไม่เคยใช้
        # = "ไม่ใช่ตัวอักษรที่งานพิมพ์ใบนี้ใช้จริง" ⇒ ถ้าโผล่ในฟอนต์ที่พัง
        # แปลว่าน่าจะเป็นผลของการแมปผิด ไม่ใช่เนื้อหา
        #
        # ⚠️ ต้องเทียบกับ **ฟอนต์อื่น** ไม่ใช่กับ span ที่สะอาดของฟอนต์เดียวกัน
        #    — เพราะ span ที่ "สะอาด" ของฟอนต์ที่พังอาจเป็นขยะที่ตรวจไม่เจอ
        #    (เคสจริง `Ï/=`) ⇒ ถ้าเอามาเป็นฐานเทียบ ตัวมันเองจะลบตัวเองออก
        trusted_chars = set()
        for sp in spans:
            if (sp.get("font") or "?").strip() not in suspect:
                trusted_chars.update(sp.get("text") or "")
        poisoned = set()
        for sp in spans:
            if (sp.get("font") or "?").strip() not in suspect:
                continue
            for ch in sp.get("text") or "":
                if ord(ch) > 127 and ch not in trusted_chars:
                    poisoned.add(ch)
        out["poisoned"] = "".join(sorted(poisoned))
    return out


def span_reason(font: str, text: str, trust: dict) -> str:
    """เหตุผลว่าทำไม span นี้เชื่อไม่ได้ — ``""`` = เชื่อได้."""
    if not trust or trust.get("mode", "off") == "off":
        return ""
    font = (font or "?").strip()
    if font not in (trust.get("suspect") or ()):
        return ""
    mode = trust.get("mode")
    if mode == "font":
        hit = bool(text)
    elif mode == "nonascii":
        hit = any(ord(c) > 127 for c in text or "")
    else:                                   # chars
        poisoned = trust.get("poisoned") or ""
        hit = any(c in poisoned for c in text or "")
    if not hit:
        return ""
    tags = (trust.get("why") or {}).get(font) or ["chars"]
    if "structure" in tags and "chars" in tags:
        how = "ฟอนต์ฝังมาแบบถอดกลับเป็น Unicode ไม่ได้ และคายอักขระเสียจริง"
    elif "structure" in tags:
        how = ("ฟอนต์ฝังมาแบบ CID/Identity-H ที่ไม่มีตาราง ToUnicode ⇒ ตามสเปก "
               "PDF แล้วถอดกลับเป็นตัวอักษรไม่ได้")
    else:
        how = "พบอักขระที่แมปผิดของฟอนต์นี้ที่อื่นในไฟล์เดียวกัน"
    return "ฟอนต์ “%s” เชื่อข้อความไม่ได้ — %s" % (font, how)


def zone_reason(spans: Iterable[dict], trust: dict) -> str:
    """เหตุผลของ **โซน**: ถ้ามี span ไหนในโซนเชื่อไม่ได้ ทั้งโซนก็เชื่อไม่ได้.

    ทำไมทั้งโซน: ชั้นตรวจเทียบ *ข้อความทั้งก้อน* ของโซน — ถ้าปล่อยให้ก้อนนั้น
    มีเศษที่แมปผิดปนอยู่ ผลเทียบก็ผิดอยู่ดี และการตัดเฉพาะบางคำทิ้งจะทำให้
    ข้อความที่เหลือ "ดูสมบูรณ์" ทั้งที่หายไปบางส่วน = หลอกกว่าเดิม
    """
    if not trust or trust.get("mode", "off") == "off":
        return ""
    for sp in spans or ():
        r = span_reason(sp.get("font"), sp.get("text") or "", trust)
        if r:
            return r
    return ""


def summary(trust: dict) -> dict:
    """ย่อสำหรับใส่ในรายงาน (JSON-safe, ไม่มีข้อความยาว).

    ใส่ไว้ใน ``report.json`` เพื่อให้ผู้ตรวจ **ส่งกลับไปบอกคนทำ artwork ได้ว่า
    ฟอนต์ตัวไหนของไฟล์มีปัญหา** — ต้นเหตุจริงอยู่ที่ขั้นตอน export ไม่ใช่ที่นี่
    """
    if not trust:
        return {"mode": "off", "suspect": [], "why": {}, "checked": 0}
    fonts = trust.get("fonts") or {}
    why = trust.get("why") or {}
    return {
        "mode": trust.get("mode", "off"),
        "suspect": list(trust.get("suspect") or []),
        "why": {f: list(why.get(f) or []) for f in (trust.get("suspect") or [])},
        "checked": len(fonts),
        "evidence": {f: {"bad_spans": fonts[f]["bad_spans"],
                         "spans": fonts[f]["spans"],
                         "bad_chars": fonts[f]["bad_chars"]}
                     for f in (trust.get("suspect") or []) if f in fonts},
    }
