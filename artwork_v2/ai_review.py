"""ตรวจทานด้วย AI (Gemini ผ่าน N8N) — ส่ง **ข้อความที่ Vision อ่านได้** ไม่ส่งภาพ ไม่ยิง Vision ซ้ำ

ลำดับ: Vision → ประกอบบรรทัด → อัลกอริทึมเทียบ → **ส่งบรรทัด/คำ (มีรหัส) ให้ N8N** →
Gemini ตอบด้วย **รหัสคำของ Vision** → แอปตรวจคำตอบกับข้อมูลจริงทุกข้อ → วาดกรอบ/คิด %

กติกา (กฎเหล็กข้อ 2 — ผลที่ผิดแบบมั่นใจแย่กว่าไม่แสดง):
* **กรอบมาจาก Vision เท่านั้น** — AI บอกแค่ "คำไหน" (รหัส) แอปหากรอบเอง และหาช่วงตัวอักษร
  ที่ต่างเองด้วยการเทียบข้อความจริงทีละตัว ⇒ กรอบแคบเท่าตัวอักษรที่ต่าง ไม่ใช่ทั้งคำ
* **% ความมั่นใจมาจาก Vision เท่านั้น** (ค่าต่ำสุดของตัวอักษรในช่วงที่ต่าง ทั้งสองฝั่ง) —
  ไม่ขอและไม่ใช้ตัวเลขจาก AI
* คำตอบที่อ้างรหัสไม่มีจริง / ยกข้อความไม่ตรงกับที่ Vision อ่าน / ข้อความที่อ้างเท่ากัน
  ⇒ **ไม่ใช้** และบันทึกเหตุผล (นับเป็น "ความถูกต้องของการอ้างอิง")
* โหมด ``assist``: AI ลบหรือลดระดับจุดของอัลกอริทึมไม่ได้ · จุดที่ AI พบเพิ่ม = เหลือง
* โหมด ``judge``: AI ตัดสินหลัก · จุดของอัลกอริทึมที่ AI ไม่ระบุ ⇒ รายการพับ ``algo_only``
* N8N ล่ม/ตอบผิดรูป ⇒ ใช้ผลอัลกอริทึมทุกรายการ + คำเตือน (ไม่มีทางได้ผลว่างเพราะ AI พัง)
"""

from __future__ import annotations

import copy
import json
import re
import time
from difflib import SequenceMatcher
from typing import Callable, Dict, List, Optional, Tuple

from . import compare, config

WORD_RE = re.compile(r"\S+")
VERDICTS = ("real", "noise", "uncertain")
VERDICT_TH = {"real": "ต่างจริง", "noise": "สัญญาณรบกวนของ OCR", "uncertain": "ไม่แน่ใจ"}
MAX_TEXT = 600
MAX_SUGGESTIONS = 12


def norm_mode(v) -> str:
    """ค่าที่ไม่รู้จัก/ไม่ส่งมา = ค่าตั้งของเครื่อง (``config.AI_MODE``)"""
    v = str(v or "").strip().lower()
    return v if v in config.AI_MODES else config.AI_MODE


def _clip(s, n=MAX_TEXT) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[:n] + "…"


def _words(text: str) -> List[Tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group()) for m in WORD_RE.finditer(text or "")]


def _side_payload(lines: List[dict], side: str, size, curved=None) -> List[dict]:
    W, H = (size or (0, 0))
    curved = set(curved or ()) if config.AI_SEND_CURVED else set()
    out = []
    for i, ln in enumerate(lines):
        ws = _words(ln.get("text") or "")
        if not ws:
            continue
        b = ln.get("box")
        box = None
        if b and W and H:
            box = [int(round(b[0] / W * 1000)), int(round(b[1] / H * 1000)),
                   int(round(b[2] / W * 1000)), int(round(b[3] / H * 1000))]
        wc = []
        for s, e, _ in ws:
            c = compare._span_conf(ln, s, e)
            wc.append(None if c is None else round(c, 2))
        cm = ln.get("conf_mean")
        row = {"id": "%s%d" % (side, i), "box": box,
               "conf": None if cm is None else round(cm, 2),
               "words": [w for _, _, w in ws], "word_conf": wc}
        if i in curved:
            row["curved"] = True        # ข้อความโค้ง/เอียง — OCR อ่านไม่นิ่ง (แอปตัดสินจากมุมของ Vision)
        out.append(row)
    return out


def _cand_side(f: dict, s: str) -> dict:
    d = f.get(s) or {}
    line = d.get("line")
    return {"line": None if line is None else "%s%d" % (s.upper(), line),
            "diff": d.get("frag") or "", "word": f.get("word_" + s) or "",
            "line_text": _clip(d.get("text"), 300)}


def _candidates(findings: List[dict]) -> List[dict]:
    out = []
    for f in findings:
        if f.get("id") is None:
            continue
        c = {"id": "F%d" % f["id"], "class": f["class"], "severity": f["severity"],
             "a": _cand_side(f, "a"), "b": _cand_side(f, "b")}
        if f.get("members"):
            c["members"] = [{"a": _cand_side(m, "a"), "b": _cand_side(m, "b")}
                            for m in f["members"]]
        out.append(c)
    return out


def build_payload(n: int, mode: str, A: List[dict], B: List[dict], size_a, size_b,
                  findings: List[dict], curved: Optional[dict] = None) -> dict:
    """ข้อมูลที่ส่งให้ N8N — มีแต่สิ่งที่ Vision อ่านได้ (+ รายการของอัลกอริทึมในโหมด assist)

    ``curved`` = ``{"A": [ดัชนีบรรทัด], "B": [...]}`` จาก ``compare.curved_lines`` ⇒ บรรทัดนั้น
    ได้ธง ``"curved": true`` (ไม่ส่ง = ไม่มีธง = รูปแบบเดิม)
    """
    curved = curved or {}
    p = {"contract": "artwork-v2-review/1", "pair": n, "mode": mode,
         "zone_a": _side_payload(A, "A", size_a, curved.get("A")),
         "zone_b": _side_payload(B, "B", size_b, curved.get("B"))}
    # โหมด judge ไม่ส่งผลของอัลกอริทึม — ให้ AI หาเองอย่างอิสระ (ใช้ A/B เทียบสองแนวทางได้จริง)
    p["candidates"] = _candidates(findings) if mode == "assist" else []
    return p


def call(url: str, payload: dict, poster: Optional[Callable] = None) -> Tuple[Optional[dict], dict]:
    """ยิง N8N · ลองซ้ำเฉพาะความล้มเหลวชั่วคราว (ต่อไม่ติด/หมดเวลา/5xx)"""
    info = {"http": None, "ms": None, "attempts": 0, "error": "", "bytes": 0}
    if not url:
        info["error"] = "ไม่ได้ตั้ง ARTWORK_V2_AI_REVIEW_URL"
        return None, info
    import requests
    transient = (requests.ConnectionError, requests.Timeout)
    if poster is None:
        poster = requests.post
    body = json.dumps(payload, ensure_ascii=False)
    info["bytes"] = len(body.encode("utf-8"))
    t0 = time.time()
    for attempt in range(max(0, int(config.AI_RETRIES)) + 1):
        info["attempts"] = attempt + 1
        try:
            r = poster(url, data=body.encode("utf-8"),
                       headers={"Content-Type": "application/json; charset=utf-8"},
                       timeout=config.AI_TIMEOUT_S)
        except Exception as e:                       # noqa: BLE001 — ต่อไม่ติด/หมดเวลา/URL ผิด
            info["error"] = "ต่อ N8N ไม่ได้: %s" % _clip(e, 200)
            if isinstance(e, transient):
                continue
            break
        info["http"] = getattr(r, "status_code", None)
        if info["http"] and info["http"] >= 500:
            info["error"] = "N8N ตอบ HTTP %s" % info["http"]
            continue
        if info["http"] != 200:
            info["error"] = "N8N ตอบ HTTP %s (workflow ไม่ได้ Activate / path ผิด?)" % info["http"]
            break
        try:
            data = r.json()
        except Exception:                            # noqa: BLE001
            info["error"] = "N8N ตอบไม่ใช่ JSON: %s" % _clip(getattr(r, "text", ""), 160)
            break
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
            data = data[0]
        if not isinstance(data, dict):
            info["error"] = "N8N ตอบผิดรูป (ไม่ใช่ object)"
            break
        if data.get("error"):
            info["error"] = "AI: %s" % _clip(data.get("error"), 300)
            break
        info["error"] = ""
        info["ms"] = int((time.time() - t0) * 1000)
        return data, info
    info["ms"] = int((time.time() - t0) * 1000)
    return None, info


# ── ตรวจคำตอบกับข้อมูล Vision ─────────────────────────────────────────

def _resolve(ids, side: str, lines: List[dict]) -> Tuple[Optional[tuple], str]:
    """รหัสคำ → ``(line, start, end)`` · ทุกคำต้องอยู่บรรทัดเดียวกันของฝั่งนั้น"""
    if not ids:
        return None, ""
    if not isinstance(ids, list):
        return None, "รหัสคำต้องเป็นรายการ"
    line = None
    spans = []
    for wid in ids:
        m = re.fullmatch(r"([AB])(\d+):(\d+)", str(wid).strip())
        if not m or m.group(1) != side:
            return None, "รหัสคำ %r ไม่ใช่ของฝั่ง %s" % (wid, side)
        li, wi = int(m.group(2)), int(m.group(3))
        if li >= len(lines):
            return None, "ไม่มีบรรทัด %s%d" % (side, li)
        ws = _words(lines[li].get("text") or "")
        if wi >= len(ws):
            return None, "ไม่มีคำ %s" % wid
        if line is None:
            line = li
        elif line != li:
            return None, "อ้างคำหลายบรรทัดในฝั่ง %s" % side
        spans.append(ws[wi][:2])
    return (line, min(s for s, _ in spans), max(e for _, e in spans)), ""


def _ws(s: str) -> str:
    return " ".join((s or "").split())


def _diff_span(ta: str, tb: str) -> Optional[Tuple[int, int, int, int]]:
    """ช่วงที่ต่าง (ไม่นับความต่างที่เป็นช่องว่างล้วน) · ไม่ต่าง = ``None``"""
    sm = SequenceMatcher(None, ta, tb, autojunk=False)
    ops = [op for op in sm.get_opcodes() if op[0] != "equal"
           and (ta[op[1]:op[2]] + tb[op[3]:op[4]]).strip()]
    if not ops:
        return None
    return ops[0][1], ops[-1][2], ops[0][3], ops[-1][4]


def _side_dict(lines, li, s, e) -> dict:
    ln = lines[li]
    return {"line": li, "text": ln["text"], "span": [s, e], "frag": ln["text"][s:e],
            "box": compare._span_box(ln, s, e), "word_box": compare._word_box(ln, s, e),
            "conf": compare._span_conf(ln, s, e)}


_EMPTY = {"line": None, "text": "", "span": [0, 0], "frag": "", "box": None, "word_box": None,
          "conf": None}


def _parse_ids(ids, side: str, lines: List[dict]) -> Optional[Tuple[int, int]]:
    """รหัสคำ → ``(บรรทัด, ดัชนีคำแรกที่อ้าง)`` โดย **ไม่เช็คว่าคำมีจริง** (ใช้ตอนกู้เท่านั้น)"""
    if not isinstance(ids, list) or not ids:
        return None
    line, idx = None, []
    for wid in ids:
        m = re.fullmatch(r"([AB])(\d+):(\d+)", str(wid).strip())
        if not m or m.group(1) != side:
            return None
        li = int(m.group(2))
        if li >= len(lines) or (line is not None and li != line):
            return None
        line = li
        idx.append(int(m.group(3)))
    return line, min(idx)


def _recover(ids, side: str, lines: List[dict], quote) -> Tuple[Optional[tuple], str]:
    """AI อ้างรหัสคำคลาด แต่ยกข้อความมาถูก ⇒ หาคำจากข้อความที่ยกมา **ในบรรทัดที่อ้างเท่านั้น**

    1) ``shift`` — ข้อความที่ยกมาตรงกับคำที่ติดกัน **ทั้งคำ** และห่างจากคำที่อ้างไม่เกิน
       ``AI_QUOTE_RECOVER_MAX_SHIFT`` คำ · ระยะใกล้สุดต้องมีตำแหน่งเดียว (กำกวม = ไม่กู้)
    2) ``substr`` — ข้อความที่ยกมาเป็นส่วนหนึ่งของคำที่อ้าง และเจอได้ตำแหน่งเดียว
       (เช่นยกแค่จุดไข่ปลาจาก ``(min)......``)

    ไม่เดา: หาไม่เจอ/เจอหลายที่ ⇒ ``(None, "")`` และใช้เหตุผลปฏิเสธเดิม
    """
    p = _parse_ids(ids, side, lines)
    q = _ws(quote)
    if p is None or not q:
        return None, ""
    li, w0 = p
    text = lines[li].get("text") or ""
    ws = _words(text)
    toks = q.split()
    n = len(toks)
    hits = [k for k in range(len(ws) - n + 1) if [w for _, _, w in ws[k:k + n]] == toks]
    near = [k for k in hits if abs(k - w0) <= max(0, int(config.AI_QUOTE_RECOVER_MAX_SHIFT))]
    if near:
        best = min(abs(k - w0) for k in near)
        at = [k for k in near if abs(k - w0) == best]
        if len(at) != 1:
            return None, ""
        k = at[0]
        return (li, ws[k][0], ws[k + n - 1][1]), "shift"
    r, err = _resolve(ids, side, lines)
    if r is None or err:
        return None, ""
    span = text[r[1]:r[2]]
    occ = [m.start() for m in re.finditer("(?=%s)" % re.escape(q), span)]
    if len(occ) != 1:
        return None, ""
    s0 = r[1] + occ[0]
    return (li, s0, s0 + len(q)), "substr"


def _side_ref(ids, quote, side: str, lines: List[dict]) -> Tuple[Optional[tuple], str, str]:
    """อ้างอิงของฝั่งหนึ่ง → ``(line, start, end)`` · ``(None, "", "")`` = ไม่ได้อ้าง ·
    ``(None, เหตุผล, "")`` = ใช้ไม่ได้ · คืน "วิธีกู้" ที่สามเมื่อกู้จากข้อความที่ยกมา"""
    r, err = _resolve(ids, side, lines)
    if r is not None and not err:
        real = lines[r[0]]["text"][r[1]:r[2]]
        if _ws(quote) == _ws(real):
            return r, "", ""
        err = "ข้อความที่ยกมาฝั่ง %s ไม่ตรงกับ Vision (%r ≠ %r)" % (
            side, _clip(quote, 60), _clip(real, 60))
    if err and config.AI_QUOTE_RECOVER:
        rr, how = _recover(ids, side, lines, quote)
        if rr is not None:
            return rr, "", how
    return None, err, ""


def check_item(it: dict, A: List[dict], B: List[dict]) -> Tuple[Optional[dict], str, str]:
    """คำตอบหนึ่งข้อของ AI → ``(จุดต่าง, เหตุผล, ชนิด)``

    ชนิด: ``ok`` (ใช้ได้) · ``invalid`` (อ้างไม่ตรงข้อมูล Vision) · ``equivalent`` (อ้างถูก
    แต่สองฝั่ง **เท่ากันตามกติกาเทียบของระบบ** — ช่องว่าง/จุดไข่ปลา/อักษรสมมูล ⇒ สัญญาณรบกวน)
    """
    if not isinstance(it, dict):
        return None, "ไม่ใช่ object", "invalid"
    verdict = str(it.get("verdict") or "").strip().lower()
    if verdict not in VERDICTS:
        return None, "verdict ไม่รู้จัก: %r" % it.get("verdict"), "invalid"
    ra, ea, ha = _side_ref(it.get("a_words"), it.get("a_quote"), "A", A)
    rb, eb, hb = _side_ref(it.get("b_words"), it.get("b_quote"), "B", B)
    if ea or eb:
        return None, ea or eb, "invalid"
    if ra is None and rb is None:
        return None, "ไม่ได้อ้างคำของฝั่งไหนเลย", "invalid"
    if ra and rb:
        ta = A[ra[0]]["text"][ra[1]:ra[2]]
        tb = B[rb[0]]["text"][rb[1]:rb[2]]
        if config.AI_EQUIV_NOISE and compare.diff_key_map(ta)[0] == compare.diff_key_map(tb)[0]:
            return None, ("ข้อความสองฝั่งเท่ากันตามกติกาเทียบของระบบ (ช่องว่าง/จุดไข่ปลา/"
                          "อักษรสมมูล) %r ≈ %r" % (_clip(ta, 40), _clip(tb, 40))), "equivalent"
        d = _diff_span(ta, tb)
        if d is None:
            return None, "ข้อความที่อ้างเท่ากันทุกตัวอักษร (ต่างแค่ช่องว่าง)", "invalid"
        i1, i2, j1, j2 = d
        a = _side_dict(A, ra[0], ra[1] + i1, ra[1] + i2)
        b = _side_dict(B, rb[0], rb[1] + j1, rb[1] + j2)
        cls = compare.classify(a["frag"], b["frag"], ta, tb)
    elif ra:
        a, b = _side_dict(A, *ra), dict(_EMPTY)
        cls = "MISSING_IN_B"
    else:
        a, b = dict(_EMPTY), _side_dict(B, *rb)
        cls = "EXTRA_IN_B"
    f = {"class": cls,
         "word_a": compare._word_at(a["text"], *a["span"]) if a["line"] is not None else "",
         "word_b": compare._word_at(b["text"], *b["span"]) if b["line"] is not None else "",
         "a": a, "b": b, "pair_method": "ai", "pair_score": None, "source": "ai", "notes": [],
         "ai": {"verdict": verdict, "reason": _clip(it.get("reason")),
                "suggestion": _clip(it.get("suggestion")), "kind": _clip(it.get("kind"), 40),
                "a_words": it.get("a_words") or [], "b_words": it.get("b_words") or []}}
    rec = {k: v for k, v in (("a", ha), ("b", hb)) if v}
    if rec:
        # รหัสที่ AI อ้างคลาด แต่ข้อความที่ยกมาตรงกับ Vision — แอปหาคำเองในบรรทัดเดียวกัน
        f["ai"]["recovered"] = rec
    f["confidence"] = confidence(f)
    return f, "", "ok"


def item_to_finding(it: dict, A: List[dict], B: List[dict]) -> Tuple[Optional[dict], str]:
    """คำตอบหนึ่งข้อของ AI → จุดต่างรูปแบบเดียวกับของอัลกอริทึม · ใช้ไม่ได้ ⇒ ``(None, เหตุผล)``"""
    f, why, _ = check_item(it, A, B)
    return f, why


def confidence(f: dict) -> Optional[float]:
    """ความมั่นใจของคำตอบ = ค่าต่ำสุดที่ Vision มั่นใจในตัวอักษรที่ต่าง (ทั้งสองฝั่ง)"""
    cs = [c for c in ((f.get("a") or {}).get("conf"), (f.get("b") or {}).get("conf"))
          if c is not None]
    if not cs:
        for m in f.get("members") or []:
            c = confidence(m)
            if c is not None:
                cs.append(c)
    return round(min(cs), 4) if cs else None


def _spans_of(f: dict, s: str) -> List[Tuple[int, int, int]]:
    out = []
    for g in [f] + list(f.get("members") or []):
        d = g.get(s) or {}
        if d.get("line") is not None and d.get("span"):
            out.append((d["line"], d["span"][0], d["span"][1]))
    return out


def overlaps(f: dict, g: dict) -> bool:
    """สองจุดชี้ตำแหน่งเดียวกัน (บรรทัดเดียวกัน + ช่วงตัวอักษรทับ/ชิดกัน) ฝั่งใดฝั่งหนึ่ง"""
    for s in ("a", "b"):
        for l1, s1, e1 in _spans_of(f, s):
            for l2, s2, e2 in _spans_of(g, s):
                if l1 == l2 and s1 <= max(e2, s2 + 1) and s2 <= max(e1, s1 + 1):
                    return True
    return False


def _ai_note(ai: dict) -> str:
    return "AI: %s" % VERDICT_TH.get(ai.get("verdict"), ai.get("verdict") or "-")


def merge(mode: str, pr: dict, resp: dict, A: List[dict], B: List[dict]) -> dict:
    """รวมคำตอบของ AI เข้ากับคู่โซน ``pr`` (แก้ ``pr`` ตรง ๆ) · คืนสถิติ"""
    st = {"items_total": 0, "items_valid": 0, "reviews_total": 0, "reviews_valid": 0,
          "invalid": [], "extra_added": 0, "extra_duplicate": 0, "extra_noise": 0,
          "items_equivalent": 0, "equivalent": [], "recovered": 0}
    findings = pr.get("findings") or []
    cl = pr.get("curved_lines") or {}
    curved = {"a": set(cl.get("A") or ()), "b": set(cl.get("B") or ())}
    by_id = {"F%d" % f["id"]: f for f in findings if f.get("id") is not None}

    if mode == "assist":
        for r in _as_list(resp.get("reviews")):
            st["reviews_total"] += 1
            if not isinstance(r, dict):
                st["invalid"].append({"what": "review", "reason": "ไม่ใช่ object"})
                continue
            f = by_id.get(str(r.get("candidate") or "").strip())
            v = str(r.get("verdict") or "").strip().lower()
            if f is None or v not in VERDICTS:
                st["invalid"].append({"what": "review %s" % _clip(r.get("candidate"), 20),
                                      "reason": "ไม่มีจุดนี้" if f is None else "verdict ไม่รู้จัก"})
                continue
            st["reviews_valid"] += 1
            f["ai"] = {"verdict": v, "reason": _clip(r.get("reason")),
                       "suggestion": _clip(r.get("suggestion"))}

    ai_finds: List[dict] = []
    for it in _as_list(resp.get("items")):
        st["items_total"] += 1
        f, why, kind = check_item(it, A, B)
        if kind == "equivalent":
            # อ้างถูก แต่สองฝั่งเท่ากันตามกติกาเทียบ ⇒ สัญญาณรบกวน (ไม่ใช่การอ้างผิด · ไม่เป็นจุด)
            st["items_equivalent"] += 1
            st["equivalent"].append({"what": "item", "reason": why,
                                     "verdict": str((it or {}).get("verdict") or "")})
            if mode == "assist":
                st["extra_noise"] += 1
            continue
        if f is None:
            st["invalid"].append({"what": "item", "reason": why,
                                  "raw": _clip(json.dumps(it, ensure_ascii=False), 300)})
            continue
        st["items_valid"] += 1
        if f["ai"].get("recovered"):
            st["recovered"] += 1
        if any(f[s]["line"] in curved[s] for s in ("a", "b") if f[s]["line"] is not None):
            f["curved"] = True
        ai_finds.append(f)

    if mode == "assist":
        for f in ai_finds:
            dup = next((g for g in findings if overlaps(f, g)), None)
            if dup is not None:
                st["extra_duplicate"] += 1
                if not dup.get("ai"):
                    dup["ai"] = dict(f["ai"])
                continue
            if f["ai"]["verdict"] == "noise":
                st["extra_noise"] += 1
                continue
            f["severity"] = "yellow"
            f["notes"].append("AI พบเพิ่ม — อัลกอริทึมไม่ได้ฟ้องจุดนี้ (ยังไม่ยืนยัน โปรดดูด้วยตา)")
            findings.append(f)
            st["extra_added"] += 1
        for f in findings:
            if f.get("ai"):
                if f.get("source") != "ai":
                    f["notes"].append(_ai_note(f["ai"]))
            else:
                f["ai"] = {"verdict": None, "reason": "", "suggestion": ""}
        pr["findings"] = findings
        st["reviewed"] = sum(1 for f in findings if f["ai"].get("verdict"))
        st["reviewable"] = len(findings)
    else:   # judge
        kept, dismissed = [], []
        for f in ai_finds:
            v = f["ai"]["verdict"]
            c = f["confidence"]
            if v == "noise" and config.AI_JUDGE_NOISE_GUARD and _hard_evidence(f):
                # Vision อ่านตัวอักษร/ตัวเลขที่ต่างได้ชัดทั้งสองฝั่ง — คำว่า "noise" ของ AI
                # ไม่มีหลักฐานรองรับ ⇒ ห้ามพับทิ้ง (ผู้ตรวจต้องเห็น)
                f["severity"] = "yellow"
                f["notes"].append("AI บอกว่าเป็นสัญญาณรบกวน แต่ Vision อ่านตัวอักษร/ตัวเลขที่ต่างได้ชัด "
                                  "(≥ %d%% ทั้งสองฝั่ง) — คงไว้ให้คนดู" % round(config.CONF_FAIL * 100))
                kept.append(f)
                continue
            if v == "noise":
                f["severity"] = "dismissed"
                f["notes"].append("AI ตัดสินว่าเป็นสัญญาณรบกวนของ OCR — ไม่นับในผลตัดสิน")
                dismissed.append(f)
                continue
            if (v == "real" and config.AI_JUDGE_CURVED_YELLOW and f.get("curved")
                    and c is not None and c >= config.CONF_FAIL):
                f["severity"] = "yellow"
                f["notes"].append("ข้อความโค้ง/เอียง — แดงได้เฉพาะเมื่อการอ่านซ้ำยืนยัน "
                                  "(คำตอบของ AI ไม่ใช่การอ่านซ้ำ) โปรดดูด้วยตา")
            elif (v == "real" and config.AI_JUDGE_PUNCT_YELLOW and f["class"] == "PUNCT"
                    and c is not None and c >= config.CONF_FAIL):
                f["severity"] = "yellow"
                f["notes"].append("ต่างแค่เครื่องหมายวรรคตอน — แดงได้เฉพาะเมื่อการอ่านซ้ำยืนยัน "
                                  "(คำตอบของ AI ไม่ใช่การอ่านซ้ำ) โปรดดูด้วยตา")
            elif v == "real" and c is not None and c >= config.CONF_FAIL:
                f["severity"] = "red"
            else:
                f["severity"] = "yellow"
                if v == "real":
                    f["notes"].append("AI บอกว่าต่างจริง แต่ Vision มั่นใจในตัวอักษรนี้ต่ำกว่า %d%%"
                                      % round(config.CONF_FAIL * 100))
            kept.append(f)
        algo_only = [g for g in findings if not any(overlaps(f, g) for f in ai_finds)]
        if config.AI_JUDGE_KEEP_ALGO_RED:
            # จุดแดงของอัลกอริทึม = Vision อ่านชัดทั้งสองฝั่ง · AI ไม่พูดถึง ≠ AI ยืนยันว่าไม่ต่าง
            keep = [g for g in algo_only if g.get("severity") == "red"]
            for g in keep:
                g["severity"] = "yellow"
                g["notes"].append("อัลกอริทึมพบ (Vision อ่านชัด) แต่ AI ไม่ได้ระบุ — คงไว้เป็นเหลือง "
                                  "ให้คนดู (โหมด AI ตัดสินหลัก)")
            kept.extend(keep)
            st["algo_red_kept"] = len(keep)
            algo_only = [g for g in algo_only if g not in keep]
        for g in algo_only:
            g["notes"].append("อัลกอริทึมพบ แต่ AI ไม่ได้ระบุ — ไม่นับในผลตัดสิน (โหมด AI ตัดสินหลัก)")
        pr["findings"] = kept
        pr["ai_dismissed"] = dismissed
        pr["algo_only"] = algo_only
    tot = st["items_total"] + st["reviews_total"]
    ok = st["items_valid"] + st["items_equivalent"] + st["reviews_valid"]
    st["ref_accuracy"] = round(ok / float(tot), 4) if tot else None
    return st


_HARD_CLASSES = ("TEXT", "NUMBER", "CASE", "MISSING_IN_B", "EXTRA_IN_B")


def _hard_evidence(f: dict) -> bool:
    """ความต่างเป็นตัวอักษร/ตัวเลข/ตัวพิมพ์ และ Vision มั่นใจทุกฝั่งที่มีข้อความ ≥ ``CONF_FAIL``
    (ไม่ใช่ข้อความโค้ง · ไม่ใช่เศษส่วน/เครื่องหมายล้วน) — ใช้กัน AI พับของจริงทิ้งว่า "noise" """
    if f.get("curved") or f["class"] not in _HARD_CLASSES:
        return False
    frag = (f["a"].get("frag") or "") + (f["b"].get("frag") or "")
    if not any(ch.isalnum() for ch in frag):
        return False
    cs = [f[s].get("conf") for s in ("a", "b") if f[s].get("line") is not None]
    return bool(cs) and all(c is not None and c >= config.CONF_FAIL for c in cs)


def _as_list(v) -> list:
    """คำตอบที่ไม่ใช่รายการ (เช่นข้อความ) = ว่าง — ห้ามไล่ทีละตัวอักษร"""
    return v if isinstance(v, list) else []


def _sugs(resp: dict) -> List[str]:
    s = resp.get("suggestions") or []
    if isinstance(s, str):
        s = [s]
    return [_clip(x, 400) for x in s if str(x or "").strip()][:MAX_SUGGESTIONS]


_MERGE_KEYS = ("findings", "ai_dismissed", "algo_only")


def run_all(pairs: List[dict], mode: str, warnings: List[str], say: Callable,
            next_id: int, poster: Optional[Callable] = None) -> Tuple[dict, int]:
    """ทำทุกคู่โซน · คืน ``(สรุปทั้งรอบ, id ถัดไป)`` — ใช้ ``pr["_cmp"]`` (บรรทัดที่เทียบจริง)"""
    summary = {"mode": mode, "url": config.AI_REVIEW_URL if mode != "off" else "",
               "pairs_ok": 0, "pairs_failed": 0}
    for pr in pairs:
        if mode == "off":
            pr["ai"] = {"mode": "off", "status": "off"}
            continue
        cmp_ = pr.get("_cmp")
        vc = {s: ((pr["sides"][s].get("stats") or {}).get("conf_mean")) for s in ("a", "b")}
        if pr.get("unreadable") or not cmp_:
            pr["ai"] = {"mode": mode, "status": "skipped", "reason": "คู่นี้อ่านไม่ได้", "vision_conf": vc}
            continue
        say("กำลังให้ AI ตรวจทานคู่ %d" % pr["n"])
        A, B = cmp_["lines_a"], cmp_["lines_b"]
        payload = build_payload(pr["n"], mode, A, B, tuple(pr["sides"]["a"]["sent_px"]),
                                tuple(pr["sides"]["b"]["sent_px"]), pr.get("findings") or [],
                                curved=pr.get("curved_lines"))
        resp, info = call(config.AI_REVIEW_URL, payload, poster)
        ai = {"mode": mode, "status": "ok" if resp is not None else "failed",
              "http": info["http"], "ms": info["ms"], "attempts": info["attempts"],
              "request_bytes": info["bytes"], "error": info["error"], "vision_conf": vc,
              "candidates": len(payload["candidates"])}
        if resp is None:
            summary["pairs_failed"] += 1
            warnings.append("คู่ %d: AI ตรวจทานไม่สำเร็จ — ใช้ผลของอัลกอริทึม (%s)"
                            % (pr["n"], info["error"]))
            pr["ai"] = ai
            continue
        # merge แก้รายการในที่ — ล้มกลางทาง ⇒ คืนผลอัลกอริทึมเดิมทุกตัวอักษร (ตามที่คำเตือนบอก)
        saved = {k: copy.deepcopy(pr[k]) for k in _MERGE_KEYS if k in pr}
        try:
            st = merge(mode, pr, resp, A, B)
        except Exception as e:                       # noqa: BLE001 — คำตอบเพี้ยนต้องไม่ล้มทั้งรอบ
            for k in _MERGE_KEYS:
                pr.pop(k, None)
            pr.update(saved)
            summary["pairs_failed"] += 1
            ai["status"] = "failed"
            ai["error"] = "รวมผล AI ไม่ได้: %s" % _clip(e, 200)
            warnings.append("คู่ %d: %s — ใช้ผลของอัลกอริทึม" % (pr["n"], ai["error"]))
            pr["ai"] = ai
            continue
        summary["pairs_ok"] += 1
        ai.update(st)
        ai["engine"] = _clip(resp.get("engine"), 60)
        ai["usage"] = resp.get("usage") if isinstance(resp.get("usage"), dict) else None
        ai["summary"] = _clip(resp.get("summary"), 2000)
        ai["suggestions"] = _sugs(resp)
        pr["ai"] = ai
        for key in ("findings", "ai_dismissed"):
            for f in pr.get(key) or []:
                if f.get("id") is None:
                    next_id += 1
                    f["id"] = next_id
    return summary, next_id
