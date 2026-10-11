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
* โหมด ``raw`` (ทดลอง): ส่ง **บรรทัดดิบตามที่ Vision ส่ง** (ไม่ผ่านชั้นต่อแถว/ต่อคำ ไม่มีธงหรือผล
  ของอัลกอริทึม) · AI ตัดสินเอง · กรอบ/% ยังมาจาก Vision · ผลอัลกอริทึมทั้งหมด ⇒ รายการพับ "ไว้เทียบ"
* โหมด ``image`` (ทดลอง · 9 ต.ค.): ส่ง **ภาพโซน A/B ชุดเดียวกับที่ส่ง Vision** + ข้อความ/ความมั่นใจ
  ของ Vision + จุดต่างของอัลกอริทึม (มีกรอบ) ให้ Gemini ดูภาพตัดสินทีละจุด · ต่างจริง = แดง ·
  ภาพเหมือนกัน (Vision อ่านผิด) = รายการพับ ``ai_dismissed`` (ไม่ลบ) · ไม่แน่ใจ = เหลือง ·
  จุดที่ AI ไม่ตอบ = คงระดับของอัลกอริทึม · ไม่รับจุดที่ AI "พบเพิ่ม"
* N8N ล่ม/ตอบผิดรูป ⇒ ใช้ผลอัลกอริทึมทุกรายการ + คำเตือน (ไม่มีทางได้ผลว่างเพราะ AI พัง)
"""

from __future__ import annotations

import base64
import copy
import json
import os
import re
import time
import unicodedata
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


def _norm_box(b, size) -> Optional[List[int]]:
    W, H = (size or (0, 0))
    if not b or not W or not H:
        return None
    return [int(round(b[0] / W * 1000)), int(round(b[1] / H * 1000)),
            int(round(b[2] / W * 1000)), int(round(b[3] / H * 1000))]


def _cand_box(f: dict, s: str, size) -> Optional[List[int]]:
    """กรอบของจุดต่างบนภาพฝั่ง ``s`` (0-1000) — คำเต็มก่อน · การ์ดโค้ง = union ของสมาชิก"""
    bs = []
    for g in [f] + list(f.get("members") or []):
        d = g.get(s) or {}
        b = d.get("word_box") or d.get("box")
        if b:
            bs.append(b)
    if not bs:
        return None
    return _norm_box([min(b[0] for b in bs), min(b[1] for b in bs),
                      max(b[2] for b in bs), max(b[3] for b in bs)], size)


def _candidates(findings: List[dict], sizes: Optional[dict] = None,
                crops: Optional[dict] = None) -> List[dict]:
    """``sizes`` (โหมด image) = ``{"a": (W, H), "b": (W, H)}`` ⇒ ใส่กรอบ 0-1000 บนภาพให้ทุกฝั่ง

    ``crops`` (โหมด image แบบครอป) = ``{"F<n>": {"a": {w, h, box}, "b": {...}}}`` ⇒ ``c[s]["crop"]``
    (``box`` = ตำแหน่งของจุดในภาพครอป 0-1000)"""
    out = []
    for f in findings:
        if f.get("id") is None:
            continue
        c = {"id": "F%d" % f["id"], "class": f["class"], "severity": f["severity"],
             "a": _cand_side(f, "a"), "b": _cand_side(f, "b")}
        if sizes:
            for s in ("a", "b"):
                c[s]["box"] = _cand_box(f, s, sizes.get(s))
        if crops and c["id"] in crops:
            for s in ("a", "b"):
                c[s]["crop"] = crops[c["id"]][s]
        if f.get("members"):
            c["members"] = [{"a": _cand_side(m, "a"), "b": _cand_side(m, "b")}
                            for m in f["members"]]
        out.append(c)
    return out


def build_payload(n: int, mode: str, A: List[dict], B: List[dict], size_a, size_b,
                  findings: List[dict], curved: Optional[dict] = None,
                  crops: Optional[dict] = None, blind: bool = False) -> dict:
    """ข้อมูลที่ส่งให้ N8N — มีแต่สิ่งที่ Vision อ่านได้ (+ รายการของอัลกอริทึมในโหมด assist)

    ``curved`` = ``{"A": [ดัชนีบรรทัด], "B": [...]}`` จาก ``compare.curved_lines`` ⇒ บรรทัดนั้น
    ได้ธง ``"curved": true`` (ไม่ส่ง = ไม่มีธง = รูปแบบเดิม)
    """
    if mode == "image" and blind and crops is not None:
        # โหมด image แบบไม่เห็นข้อความ (``AI_IMAGE_BLIND``) — มีแต่รหัสจุด + ตำแหน่งในภาพครอป
        # (ไม่มีบรรทัด/คำ/diff/ชนิดของจุดจาก Vision ⇒ Gemini อ่านจากพิกเซลเท่านั้น ลอกไม่ได้)
        return {"contract": "artwork-v2-image/3", "pair": n, "mode": mode, "blind": True,
                "candidates": [{"id": c["id"], "a": {"crop": c["a"].get("crop")},
                                "b": {"crop": c["b"].get("crop")}}
                               for c in _candidates(findings, None, crops) if c["id"] in crops]}
    curved = curved or {}
    p = {"contract": "artwork-v2-review/1", "pair": n, "mode": mode,
         "zone_a": _side_payload(A, "A", size_a, curved.get("A")),
         "zone_b": _side_payload(B, "B", size_b, curved.get("B"))}
    # โหมด judge/raw ไม่ส่งผลของอัลกอริทึม — ให้ AI หาเองอย่างอิสระ (ใช้ A/B เทียบสองแนวทางได้จริง)
    if mode == "image":
        p["contract"] = "artwork-v2-image/2" if crops is not None else "artwork-v2-image/1"
        p["candidates"] = _candidates(findings, {"a": size_a, "b": size_b}, crops)
    else:
        p["candidates"] = _candidates(findings) if mode == "assist" else []
    return p


def image_part(path: str, size) -> Optional[dict]:
    """ภาพโซนที่ส่ง Vision (ไฟล์ใน ``img/`` ของรอบ) → ``{mime, w, h, b64}`` · อ่านไม่ได้ = ``None``"""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if not data:
        return None
    W, H = (size or (0, 0))
    return {"mime": "image/jpeg", "w": int(W), "h": int(H),
            "b64": base64.b64encode(data).decode("ascii")}


def _load_image(path: str):
    """ภาพที่ส่ง Vision → array (BGR) · อ่าน/ถอดรหัสไม่ได้ = ``None``"""
    try:
        import cv2
        import numpy as np
        with open(path, "rb") as fh:
            data = fh.read()
        im = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None
    except Exception:                                # noqa: BLE001 — ไฟล์เสีย = ใช้ผลอัลกอริทึม
        return None
    return im


def _spot_px(f: dict, s: str, vertical: bool = False):
    """กรอบของจุดบนภาพฝั่ง ``s`` (พิกเซลของภาพที่ส่ง) + ความสูงบรรทัด

    คำเต็ม → ตัวอักษรที่ต่าง → ตำแหน่งประมาณ (``est_box`` — ฝั่งที่ไม่มีข้อความ) · การ์ดโค้ง = union
    ของสมาชิก · ไม่มีเลย = ``(None, None)`` (ไม่เดาตำแหน่ง)"""
    bs, hs = [], []
    for g in [f] + list(f.get("members") or []):
        d = g.get(s) or {}
        b = d.get("word_box") or d.get("box") or d.get("est_box")
        if b:
            bs.append(b)
            hs.append((b[2] - b[0]) if vertical else (b[3] - b[1]))   # แนวตั้ง: ความสูงตัวอักษร = ด้านแคบ
    if not bs:
        return None, None
    hs.sort()
    return ([min(b[0] for b in bs), min(b[1] for b in bs),
             max(b[2] for b in bs), max(b[3] for b in bs)], hs[len(hs) // 2])


def crop_rect(box, lh, W: int, H: int, vertical: bool = False) -> List[int]:
    """กรอบครอปรอบจุด (พิกเซล · อยู่ในภาพเสมอ) — เห็นคำข้าง ๆ และบรรทัดบน/ล่างบางส่วน

    กว้างอย่างน้อย ``AI_IMAGE_CROP_MIN_W`` · ถ้าบริบทเกินด้านยาวสูงสุดแต่ตัวจุดเองพอดี ⇒ ตัดบริบท
    ให้พอดี ``AI_IMAGE_CROP_MAX_SIDE`` (คงความละเอียดจริง ไม่ย่อ) · ``vertical`` = ข้อความแนวตั้ง ⇒ คิดในแกน
    ที่สลับกัน (บริบทตามแนวข้อความ = แนวตั้ง · บรรทัดข้างเคียง = ซ้าย/ขวา) แล้วสลับกลับ"""
    if vertical:
        r = crop_rect([box[1], box[0], box[3], box[2]], lh, H, W)
        return [r[1], r[0], r[3], r[2]]
    mx = max(64, int(config.AI_IMAGE_CROP_MAX_SIDE))
    x0, y0, x1, y1 = [float(v) for v in box]
    bw, bh = max(1.0, x1 - x0), max(1.0, y1 - y0)
    lh = max(8.0, float(lh or bh))
    cw = bw + 2 * max(4 * lh, 60.0)
    cw = max(cw, float(min(config.AI_IMAGE_CROP_MIN_W, W)))
    if cw > mx and bw + 2 * lh <= mx:
        cw = float(mx)
    ch = bh + 2 * max(1.5 * lh, 16.0)
    if ch > mx and bh + 2 * lh <= mx:
        ch = float(mx)
    cw, ch = min(cw, float(W)), min(ch, float(H))
    X0 = min(max(0.0, (x0 + x1) / 2 - cw / 2), W - cw)
    Y0 = min(max(0.0, (y0 + y1) / 2 - ch / 2), H - ch)
    return [int(round(X0)), int(round(Y0)), int(round(X0 + cw)), int(round(Y0 + ch))]


def hires_scale(box, lh, kmax: float) -> float:
    """เท่าที่ควรขยาย (เรนเดอร์ใหม่จาก PDF) ให้ตัวอักษรสูงราว ``AI_IMAGE_CROP_TARGET_LH`` px

    ไม่เกิน ``kmax`` (เพดาน dpi / ความละเอียดจริงของภาพสแกน) · ตัวจุด + ระยะเผื่อ 1 บรรทัดต้องยังพอดี
    ``AI_IMAGE_CROP_MAX_SIDE`` (ไม่งั้นต้องย่อกลับ = ไม่ได้อะไร) · ขยายน้อยกว่า ``HIRES_MIN`` ⇒ 1.0 (ไม่คุ้ม)"""
    mx = max(64, int(config.AI_IMAGE_CROP_MAX_SIDE))
    bw = max(1.0, float(box[2]) - float(box[0]))
    bh = max(1.0, float(box[3]) - float(box[1]))
    lh = max(1.0, float(lh or bh))
    k = min(float(config.AI_IMAGE_CROP_TARGET_LH) / lh, float(kmax or 1.0),
            mx / (bw + 2 * lh), mx / (bh + 2 * lh))
    return k if k >= float(config.AI_IMAGE_CROP_HIRES_MIN) else 1.0


def _rot_rel(b: List[int], rot: int) -> List[int]:
    """กรอบ 0..1000 ในครอป → กรอบในครอปที่หมุนตามเข็ม ``rot`` องศา"""
    x0, y0, x1, y1 = b
    if rot == 90:
        return [1000 - y1, x0, 1000 - y0, x1]
    if rot == 180:
        return [1000 - x1, 1000 - y1, 1000 - x0, 1000 - y0]
    if rot == 270:
        return [y0, 1000 - x1, y1, 1000 - x0]
    return list(b)


def crop_part(im, box, lh, hi=None, vertical: bool = False,
              rot: int = 0) -> Optional[Tuple[dict, dict]]:
    """ครอปรอบจุดจากภาพที่ส่ง Vision → ``(ส่วนที่ส่ง {mime, w, h, b64}, ข้อมูลใน candidate {w, h, box})``

    ``hi`` (``pipeline.HiresSide`` · PDF เท่านั้น) — ตัวอักษรเล็ก ⇒ เรนเดอร์บริเวณเดียวกันใหม่จากไฟล์ต้นฉบับ
    ที่ dpi สูงขึ้น (กรอบครอปคิดในพิกัดที่ขยายแล้ว ⇒ ด้านยาวยังไม่เกินเพดาน) · เรนเดอร์ไม่ได้ ⇒ ครอป JPEG เดิม ·
    ย่อเฉพาะเมื่อครอปยังใหญ่กว่า ``AI_IMAGE_CROP_MAX_SIDE`` (ตัวจุดเองกว้างมาก) · JPEG คุณภาพสูง ·
    ``vertical`` = ข้อความแนวตั้ง (ระยะเผื่อจากด้านแคบ) · ``rot`` = หมุนครอปตามเข็มก่อนส่ง (ให้ข้อความตั้งตรง ·
    กรอบของจุดในครอปหมุนตาม)"""
    import cv2
    H, W = im.shape[:2]
    up, c, bx = 1.0, None, [float(v) for v in box]
    if hi is not None and float(getattr(hi, "kmax", 1.0) or 1.0) > 1.0:
        kk = hires_scale(box, lh, hi.kmax)
        if kk > 1.0:
            bk = [v * kk for v in bx]
            rk = crop_rect(bk, float(lh or 0) * kk, W * kk, H * kk, vertical)
            got = hi.render([v / kk for v in rk], (rk[2] - rk[0], rk[3] - rk[1]))
            if got is not None and getattr(got, "size", 0):
                up, c, bx, r = kk, got, bk, rk
    if c is None:
        r = crop_rect(box, lh, W, H, vertical)
        c = im[r[1]:r[3], r[0]:r[2]]
    if c.size == 0:
        return None
    ch, cw = c.shape[:2]
    k = min(1.0, float(config.AI_IMAGE_CROP_MAX_SIDE) / max(cw, ch))
    if k < 1.0:
        c = cv2.resize(c, (max(1, int(round(cw * k))), max(1, int(round(ch * k)))),
                       interpolation=cv2.INTER_AREA)
    rot = int(rot or 0) % 360
    if rot in (90, 180, 270):
        c = cv2.rotate(c, {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
                           270: cv2.ROTATE_90_COUNTERCLOCKWISE}[rot])
    try:
        from . import imaging          # ตัวเข้ารหัสเดียวกับภาพที่ส่ง Vision (4:4:4 · ไม่ลดสี)
        data = imaging.encode_jpeg(c, int(config.AI_IMAGE_CROP_JPEG_Q))
    except Exception:                                # noqa: BLE001
        return None
    h2, w2 = c.shape[:2]
    rel = [max(0, min(1000, int(round((bx[0] - r[0]) / float(cw) * 1000)))),
           max(0, min(1000, int(round((bx[1] - r[1]) / float(ch) * 1000)))),
           max(0, min(1000, int(round((bx[2] - r[0]) / float(cw) * 1000)))),
           max(0, min(1000, int(round((bx[3] - r[1]) / float(ch) * 1000))))]
    if rot in (90, 180, 270):
        rel = _rot_rel(rel, rot)
    info = {"w": int(w2), "h": int(h2), "box": rel, "region": r, "scale": round(k, 4)}
    if rot in (90, 180, 270):
        info["rot"] = rot
    if up > 1.0:
        info["hires"] = round(up, 3)
        if getattr(hi, "base_dpi", None):
            info["dpi"] = round(float(hi.base_dpi) * up, 1)
    return ({"mime": "image/jpeg", "w": int(w2), "h": int(h2),
             "b64": base64.b64encode(data).decode("ascii")}, info)


def _line_rot(f: dict, s: str, lines: Optional[dict]) -> Optional[int]:
    """มุมของบรรทัดที่จุดนี้อ้างบนฝั่ง ``s`` ตามที่ Vision วัด (ปัดเป็น 0/90/180/270 · ห่างเกิน 20° ⇒ None)"""
    i = (f.get(s) or {}).get("line")
    ls = (lines or {}).get(s) or []
    if not isinstance(i, int) or not (0 <= i < len(ls)):
        return None
    a = (ls[i] or {}).get("angle")
    if a is None:
        return None
    q = int(round(float(a) / 90.0)) * 90 % 360
    d = abs(float(a) % 360 - q)
    return q if min(d, 360 - d) <= 20 else None


def spot_orient(f: dict, s: str, lines: Optional[dict]) -> Tuple[bool, int]:
    """(ข้อความแนวตั้งไหม, หมุนครอปตามเข็มกี่องศาให้ข้อความตั้งตรง) ของจุดนี้บนฝั่ง ``s``

    มุมจากบรรทัดของฝั่งนั้น → ไม่มี (ฝั่งที่ไม่มีข้อความ) ใช้ของอีกฝั่ง · ไม่มีมุมเลย ⇒ ครอปแบบเดิม (ไม่เดา) ·
    ``AI_IMAGE_CROP_VERTICAL`` ปิด ⇒ (False, 0) เสมอ"""
    if not config.AI_IMAGE_CROP_VERTICAL:
        return False, 0
    a = _line_rot(f, s, lines)
    if a is None:
        a = _line_rot(f, "b" if s == "a" else "a", lines)
    if a is None:
        # ไม่มีมุมจาก Vision ⇒ ครอปแบบเดิม (สัดส่วนกรอบใช้ตัดสินไม่ได้: ตัวอักษรเดี่ยว "1" "|" ก็สูง-แคบ)
        return False, 0
    rot = (360 - a) % 360 if config.AI_IMAGE_CROP_ROTATE else 0
    return a in (90, 270), rot


def plan_crops(findings: List[dict], ims: dict,
               hires: Optional[dict] = None,
               stats: Optional[dict] = None,
               lines: Optional[dict] = None) -> Tuple[List[dict], List[dict], dict, dict]:
    """เลือกจุดที่ส่ง (แดงก่อนเหลือง · ไม่เกิน ``AI_IMAGE_MAX_CANDIDATES``) + ครอป A/B ของแต่ละจุด

    คืน ``(จุดที่ส่ง ตามลำดับเดิม, ส่วนภาพเรียงตามจุด, ข้อมูลครอปต่อจุด, {F<n>: เหตุที่ไม่ส่ง})``"""
    cap = int(config.AI_IMAGE_MAX_CANDIDATES or 0)
    order = sorted([f for f in findings if f.get("id") is not None],
                   key=lambda f: (0 if f.get("severity") == "red" else 1, f["id"]))
    chosen, info, parts, skip = set(), {}, {}, {}
    for f in order:
        fid = "F%d" % f["id"]
        if cap and len(chosen) >= cap:
            skip[fid] = "เกินเพดาน %d จุดต่อคำขอ" % cap
            continue
        got = {}
        for s in ("a", "b"):
            vert, rot = spot_orient(f, s, lines)
            box, lh = _spot_px(f, s, vert)
            if box is None:
                skip[fid] = "ไม่มีตำแหน่งของจุดนี้บนภาพฝั่ง %s" % s.upper()
                break
            cp = crop_part(ims[s], box, lh, (hires or {}).get(s), vert, rot)
            if cp is None:
                skip[fid] = "ครอปภาพฝั่ง %s ไม่ได้" % s.upper()
                break
            got[s] = cp
        if fid in skip:
            continue
        chosen.add(fid)
        info[fid] = {s: got[s][1] for s in ("a", "b")}
        parts[fid] = got
    sent = [f for f in findings if f.get("id") is not None and "F%d" % f["id"] in chosen]
    crops = []
    for f in sent:
        fid = "F%d" % f["id"]
        for s in ("a", "b"):
            crops.append(dict(parts[fid][s][0], candidate=fid, side=s))
    pub = {k: {s: {kk: v[s][kk] for kk in ("w", "h", "box")} for s in ("a", "b")}
           for k, v in info.items()}
    hi_dpis = [v[s].get("dpi") or 0 for v in info.values() for s in ("a", "b") if v[s].get("hires")]
    if stats is not None:
        # มุมที่หมุนครอป (ไม่อยู่ใน payload — สัญญากับ N8N คงเดิม) · ใช้ตอนเก็บภาพ/แสดงใน ⓘ/Log
        stats["rots"] = {k: {s: v[s]["rot"] for s in ("a", "b") if v[s].get("rot")}
                         for k, v in info.items() if any(v[s].get("rot") for s in ("a", "b"))}
        stats["rotated_crops"] = sum(len(v) for v in stats["rots"].values())
    if stats is not None and hires is not None:
        # สถิติสำหรับ Log (ไม่อยู่ใน payload) — ครอปกี่รูปที่เรนเดอร์ใหม่จาก PDF · dpi สูงสุด
        stats["hires_crops"] = len(hi_dpis)
        stats["hires_dpi_max"] = max(hi_dpis) if hi_dpis else None
    return sent, crops, pub, skip


def save_crops(img_dir: Optional[str], n: int, crops: List[dict], info: dict,
               findings: List[dict], rots: Optional[dict] = None) -> int:
    """เก็บครอปที่ส่ง Gemini จริง (ไบต์เดียวกับใน payload) → ``img/ai<n>_F<id>_<a|b>.jpg``

    ผูกไว้ที่ ``f["ai_crop"] = {a|b: {img, w, h, box}}`` (``box`` = กรอบ 0-1000 ที่บอก AI ว่าอ่านตรงนี้) ⇒
    หน้าเว็บแสดงในหมายเหตุของแถว · เขียนไม่ได้ ⇒ ข้ามรูปนั้น (ไม่แตะผลตรวจ) · คืนจำนวนรูปที่เขียนได้ ·
    ``rots`` = {F<n>: {a|b: องศา}} ครอปที่หมุนให้ข้อความตั้งตรงแล้ว ⇒ ``rot`` ใน ``ai_crop``"""
    if not (config.AI_IMAGE_SAVE_CROPS and img_dir and os.path.isdir(img_dir)):
        return 0
    by_id = {"F%d" % f["id"]: f for f in findings if f.get("id") is not None}
    done = 0
    for c in crops:
        fid, s = c.get("candidate"), c.get("side")
        f, meta = by_id.get(fid), (info.get(fid) or {}).get(s)
        if f is None or meta is None or s not in ("a", "b"):
            continue
        name = "ai%d_%s_%s.jpg" % (int(n), fid, s)
        try:
            with open(os.path.join(img_dir, name), "wb") as fh:
                fh.write(base64.b64decode(c["b64"]))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        f.setdefault("ai_crop", {})[s] = {"img": name, "w": int(meta["w"]), "h": int(meta["h"]),
                                          "box": list(meta["box"])}
        r = ((rots or {}).get(fid) or {}).get(s)
        if r:
            f["ai_crop"][s]["rot"] = int(r)
        done += 1
    return done


def call(url: str, payload: dict, poster: Optional[Callable] = None,
         timeout: Optional[float] = None,
         url_env: str = "ARTWORK_V2_AI_REVIEW_URL") -> Tuple[Optional[dict], dict]:
    """ยิง N8N · ลองซ้ำเฉพาะความล้มเหลวชั่วคราว (ต่อไม่ติด/หมดเวลา/5xx)"""
    info = {"http": None, "ms": None, "attempts": 0, "error": "", "bytes": 0}
    if not url:
        info["error"] = "ไม่ได้ตั้ง %s" % url_env
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
                       timeout=timeout or config.AI_TIMEOUT_S)
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
            info["error"] = "AI: %s" % _clip(data.get("error"), 400)
            # usage ของคำขอที่ล้ม (เช่น MAX_TOKENS) — บอกได้ว่าหมดไปกับส่วนคิดหรือคำตอบ
            if isinstance(data.get("usage"), dict):
                info["usage"] = data["usage"]
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


# รายการพับของอัลกอริทึมที่มีอยู่แล้วตอน AI ตรวจ (pixel_same เกิดหลัง AI)
FOLDED_KEYS = ("lowmark", "debris", "relocated", "excluded")


def _alnum_key(s: str) -> str:
    """ตัวอักษร/ตัวเลขล้วน (NFKC · คงตัวพิมพ์ — ตัวพิมพ์ต่างคือความต่างจริง)"""
    return "".join(c for c in unicodedata.normalize("NFKC", s or "") if c.isalnum())


def folded_twin(f: dict, pr: dict) -> Optional[Tuple[str, dict]]:
    """จุดที่ AI พบเพิ่ม ``f`` คือจุดเดียวกับที่อัลกอริทึมพับไว้แล้วหรือไม่ · คืน ``(ชื่อรายการ, จุดนั้น)``

    ต้องครบทุกข้อ (แคบโดยตั้งใจ — ห้ามกลบความต่างของตัวอักษร/ตัวเลข):
    ① ชี้ตำแหน่งทับกัน (``overlaps``) · ② ทุกฝั่งที่ ``f`` อ้าง อยู่บรรทัดเดียวกับจุดที่พับ ·
    ③ คู่บรรทัดของจุดที่พับมีตัวอักษร/ตัวเลขเหมือนกันทุกตัว (ต่างแค่เครื่องหมาย/ช่องว่าง)"""
    for lk in FOLDED_KEYS:
        for g in pr.get(lk) or []:
            if not overlaps(f, g):
                continue
            ga, gb = g.get("a") or {}, g.get("b") or {}
            if ga.get("line") is None or gb.get("line") is None:
                continue
            if any((f.get(s) or {}).get("line") is not None
                   and f[s]["line"] != (g.get(s) or {}).get("line") for s in ("a", "b")):
                continue
            if _alnum_key(ga.get("text")) != _alnum_key(gb.get("text")):
                continue
            return lk, g
    return None


def _ai_note(ai: dict) -> str:
    return "AI: %s" % VERDICT_TH.get(ai.get("verdict"), ai.get("verdict") or "-")


def merge(mode: str, pr: dict, resp: dict, A: List[dict], B: List[dict],
          blind: bool = False) -> dict:
    """รวมคำตอบของ AI เข้ากับคู่โซน ``pr`` (แก้ ``pr`` ตรง ๆ) · คืนสถิติ"""
    st = {"items_total": 0, "items_valid": 0, "reviews_total": 0, "reviews_valid": 0,
          "invalid": [], "extra_added": 0, "extra_duplicate": 0, "extra_noise": 0,
          "items_equivalent": 0, "equivalent": [], "recovered": 0, "extra_folded": 0}
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
    for it in ([] if mode == "image" else _as_list(resp.get("items"))):
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
        # raw: ดัชนีบรรทัดเป็นของบรรทัดดิบ (ธงโค้งเป็นของบรรทัดที่อัลกอริทึมต่อแล้ว) ⇒ ไม่ใช้
        if mode != "raw" and any(f[s]["line"] in curved[s] for s in ("a", "b")
                                 if f[s]["line"] is not None):
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
            tw = folded_twin(f, pr) if config.AI_DEDUP_FOLDED else None
            if tw is not None:
                # อัลกอริทึมเห็นจุดนี้แล้วและพับไว้พร้อมเหตุผล — ไม่เพิ่มซ้ำเป็นเหลือง · แนบคำตอบ AI ไว้ที่จุดที่พับ
                st["extra_folded"] += 1
                g = tw[1]
                if not g.get("ai"):
                    g["ai"] = dict(f["ai"])
                    g.setdefault("notes", []).append(_ai_note(f["ai"]))
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
    elif mode == "raw":
        _merge_raw(pr, ai_finds, findings, st)
    elif mode == "image":
        _merge_image(pr, resp, findings, st, blind)
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
            elif (v == "real" and config.AI_JUDGE_ONESIDED_GUARD
                    and c is not None and c >= config.CONF_FAIL and _weak_onesided(f, pr)):
                f["severity"] = "yellow"
                f["notes"].append(_weak_onesided(f, pr))
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


def blind_verdict(a_seen, b_seen, said: str) -> Tuple[str, str]:
    """โหมดไม่เห็นข้อความ — **แอปตัดสินเอง** จากสิ่งที่ AI อ่านได้จากภาพ ``(คำตัดสิน, เหตุผลถ้าไม่แน่ใจ)``

    เทียบด้วยคีย์เดียวกับอัลกอริทึม (``compare.diff_key_map``: ไม่สนช่องว่าง · จุดไข่ปลา · ®/Ⓡ · ½/1/2 ·
    คงตัวพิมพ์และเครื่องหมาย) · ไม่แน่ใจเมื่อ: อ่านไม่ครบสองฝั่ง · มี ``[?]`` · ว่างทั้งคู่ · AI บอกไม่แน่ใจ ·
    คำตอบของ AI ขัดกับสิ่งที่มันอ่านได้เอง (กฎเหล็กข้อ 2 — ไม่มั่นใจ = ไม่ตัดสินแทน)"""
    if not isinstance(a_seen, str) or not isinstance(b_seen, str):
        return "uncertain", "AI ไม่ได้ส่งสิ่งที่อ่านได้ครบทั้งสองฝั่ง"
    if "[?]" in a_seen or "[?]" in b_seen:
        return "uncertain", "AI อ่านบางตัวอักษรไม่ออก ([?])"
    ka, kb = compare.diff_key_map(a_seen)[0], compare.diff_key_map(b_seen)[0]
    if not ka and not kb:
        return "uncertain", "AI ไม่เห็นข้อความที่จุดนี้ทั้งสองฝั่ง"
    app = "noise" if ka == kb else "real"
    if said == "uncertain":
        return "uncertain", "AI บอกว่าอ่านไม่ชัด"
    if said != app:
        return "uncertain", ("คำตอบของ AI (%s) ขัดกับสิ่งที่ AI อ่านได้เอง (%s)"
                             % (said, "เหมือนกัน" if app == "noise" else "ต่างกัน"))
    return app, ""


def _merge_image(pr: dict, resp: dict, findings: List[dict], st: dict,
                 blind: bool = False) -> None:
    """โหมด image — Gemini ดูภาพแล้วตัดสินจุดของอัลกอริทึมทีละจุด (ผู้ใช้เลือก "ตัดสินเต็มที่")

    * ``real`` (ภาพต่างจริง) = แดง · ``noise`` (ภาพเหมือนกัน — Vision อ่านผิด) ⇒ ``ai_dismissed``
      (ไม่ลบ · ไม่นับ) · ``uncertain`` = เหลือง
    * จุดที่ AI ไม่ได้ตอบ ⇒ คงระดับของอัลกอริทึม + หมายเหตุ (ไม่หายเงียบ)
    * ``items`` (จุดที่ AI พบเพิ่ม) ไม่ถูกใช้ — โหมดนี้ตรวจเฉพาะจุดที่อัลกอริทึมพบ
    * ``AI_IMAGE_SAFETY`` (ค่าเริ่มต้นปิด) — "ภาพเหมือน" กับตัวอักษร/ตัวเลข/ตัวพิมพ์ที่ Vision อ่านชัด ⇒ เหลือง
    """
    by_id = {"F%d" % f["id"]: f for f in findings if f.get("id") is not None}
    st["items_ignored"] = len(_as_list(resp.get("items")))
    got: Dict[str, dict] = {}
    for r in _as_list(resp.get("reviews")):
        st["reviews_total"] += 1
        if not isinstance(r, dict):
            st["invalid"].append({"what": "review", "reason": "ไม่ใช่ object"})
            continue
        cid = str(r.get("candidate") or "").strip()
        v = str(r.get("verdict") or "").strip().lower()
        if cid not in by_id or v not in VERDICTS:
            st["invalid"].append({"what": "review %s" % _clip(cid, 20),
                                  "reason": "ไม่มีจุดนี้" if cid not in by_id else "verdict ไม่รู้จัก"})
            continue
        if cid in got:
            st["invalid"].append({"what": "review %s" % cid, "reason": "ตอบจุดเดียวกันซ้ำ — ใช้คำตอบแรก"})
            continue
        st["reviews_valid"] += 1
        got[cid] = {"verdict": v, "reason": _clip(r.get("reason")),
                    "suggestion": _clip(r.get("suggestion")), "image": True,
                    "a_seen": _clip(r.get("a_seen"), 300), "b_seen": _clip(r.get("b_seen"), 300)}
        if blind:
            # คำตัดสินมาจากแอปเทียบสิ่งที่ AI อ่าน (เก็บคำตอบเดิมของ AI ไว้ดู)
            v2, why = blind_verdict(r.get("a_seen"), r.get("b_seen"), v)
            got[cid].update({"verdict": v2, "ai_verdict": v, "blind": True, "decided_why": why})
            if v2 != v:
                st["blind_overruled"] = st.get("blind_overruled", 0) + 1
    kept, dismissed = [], []
    cnt = {"real": 0, "noise": 0, "uncertain": 0, "unanswered": 0, "guarded": 0}
    for f in findings:
        ai = got.get("F%d" % f["id"]) if f.get("id") is not None else None
        if ai is None and (f.get("ai") or {}).get("not_sent"):
            # ไม่ได้ส่งให้ AI (เกินเพดาน / ไม่มีตำแหน่งบนภาพ) — หมายเหตุใส่ไว้แล้วตอนเลือกจุด
            cnt["not_sent"] = cnt.get("not_sent", 0) + 1
            kept.append(f)
            continue
        if ai is None:
            cnt["unanswered"] += 1
            f["ai"] = {"verdict": None, "reason": "", "suggestion": "", "image": True}
            # แบ่งคำขอ (``AI_IMAGE_SPLIT``) ⇒ รู้ว่าคำขอของจุดนี้เป็นอะไร (ล้ม / ตอบโดยไม่มีผลตรวจ)
            why = ((resp.get("_unanswered_why") or {}) if isinstance(resp, dict) else {}).get(
                "F%d" % f["id"]) if f.get("id") is not None else None
            if why:
                f["ai"]["unanswered_why"] = why
            f["notes"].append("AI ไม่ได้ตอบจุดนี้%s — คงระดับของอัลกอริทึม" % (" (%s)" % why if why else ""))
            kept.append(f)
            continue
        f["ai"] = ai
        v = ai["verdict"]
        cnt[v] += 1
        px = f.get("pixel") or {}
        if (v == "noise" and config.AI_IMAGE_PIXEL_FIRST and px.get("status") == "DIFF"
                and not px.get("raster")):
            # หลักฐานภาพ (PDF เวกเตอร์ · เรนเดอร์จากไฟล์ต้นฉบับ) ยืนยันว่าต่าง ⇒ AI พับไม่ได้ — AI อาจทำให้
            # อักษรเป็นรูปมาตรฐานเอง (ى→ي) · ภาพสแกน: DIFF ไม่ใช่หลักฐาน (ผิด 20/22 ที่วัดไว้) ⇒ พับได้แบบเดิม
            cnt["pixel_kept"] = cnt.get("pixel_kept", 0) + 1
            ai["pixel_kept"] = True
            f["notes"].append("AI อ่านได้เหมือนกันทั้งสองฝั่ง แต่ภาพจากไฟล์ต้นฉบับต่างกันที่จุดนี้ — ไม่พับ "
                              "คงระดับเดิม โปรดดูด้วยตา")
            kept.append(f)
            continue
        if v == "noise" and config.AI_IMAGE_ONESIDED_GUARD and _strong_onesided(f):
            # ข้อความมีฝั่งเดียวและ Vision อ่านได้ชัด — AI ที่เห็นครอป A/B ในคำขอเดียวลอกข้อความข้ามฝั่งได้
            # (F21 ของ John West: "อ่าน" A ได้ประโยคเดียวกับ B ทั้งที่ A ว่าง) ⇒ ไม่พับ คงเป็นเหลือง
            cnt["onesided_kept"] = cnt.get("onesided_kept", 0) + 1
            ai["onesided_kept"] = True
            f["severity"] = "yellow"
            f["notes"].append("AI บอกว่าเหมือนกันทั้งสองฝั่ง แต่ Vision อ่านข้อความนี้ได้ชัดเพียงฝั่งเดียว "
                              "(≥ %d%%) — AI อาจลอกข้อความข้ามฝั่ง · ไม่พับ โปรดดูด้วยตา"
                              % round(config.CONF_FAIL * 100))
            kept.append(f)
            continue
        if v == "noise" and config.AI_IMAGE_SAFETY and _hard_evidence(f):
            cnt["guarded"] += 1
            f["severity"] = "yellow"
            f["notes"].append("AI ดูภาพแล้วบอกว่าเหมือนกัน แต่ Vision อ่านตัวอักษร/ตัวเลขที่ต่างได้ชัด "
                              "(≥ %d%% ทั้งสองฝั่ง) — คงไว้ให้คนดู" % round(config.CONF_FAIL * 100))
            kept.append(f)
            continue
        if v == "noise":
            f["severity"] = "dismissed"
            f["notes"].append("AI อ่านภาพเอง (ไม่เห็นข้อความของ Vision) ได้เหมือนกันทั้งสองฝั่ง — Vision อ่านผิด "
                              "· ไม่นับในผลตัดสิน" if blind else
                              "AI ดูภาพแล้วตัดสินว่าสองฝั่งพิมพ์เหมือนกัน (Vision อ่านผิด) — ไม่นับในผลตัดสิน")
            dismissed.append(f)
            continue
        if v == "real" and config.AI_IMAGE_CURVED_YELLOW and (
                f.get("curved") or f.get("class") == "CURVED"):
            cnt["curved_yellow"] = cnt.get("curved_yellow", 0) + 1
            f["severity"] = "yellow"
            f["notes"].append("AI ดูภาพแล้วบอกว่าต่าง แต่เป็นข้อความโค้ง/เอียง (OCR และ AI อ่านไม่นิ่ง) "
                              "— คงไว้เป็นเหลือง โปรดดูด้วยตา")
        elif v == "real":
            f["severity"] = "red"
            f["notes"].append("AI อ่านภาพเอง (ไม่เห็นข้อความของ Vision) ได้ต่างกัน — ยืนยันว่าต่างจริง"
                              if blind else "AI ดูภาพแล้วยืนยันว่าต่างจริง")
        else:
            f["severity"] = "yellow"
            why = ai.get("decided_why") if blind else ""
            f["notes"].append("AI ดูภาพแล้วไม่แน่ใจ%s — โปรดดูด้วยตา" % (" (%s)" % why if why else ""))
        kept.append(f)
    pr["findings"] = kept
    pr["ai_dismissed"] = dismissed
    st["image_verdicts"] = cnt
    st["reviewed"] = len(findings) - cnt["unanswered"] - cnt.get("not_sent", 0)
    st["reviewable"] = len(findings) - cnt.get("not_sent", 0)


def _strong_onesided(f: dict) -> bool:
    """หายไป/เกินมาฝั่งเดียว ที่ Vision อ่านฝั่งที่มีข้อความได้ชัด (≥ ``CONF_FAIL``) · มีตัวอักษร/ตัวเลข ≥ 2 ตัว ·
    ไม่ใช่ข้อความโค้ง — หลักฐานของ Vision ล้วน"""
    if f.get("class") not in ("MISSING_IN_B", "EXTRA_IN_B") or f.get("curved"):
        return False
    side = f["a" if f["class"] == "MISSING_IN_B" else "b"] or {}
    if side.get("line") is None:
        return False
    try:
        conf = float(side.get("conf"))
    except (TypeError, ValueError):
        return False
    if conf < float(config.CONF_FAIL):
        return False
    k = compare.diff_key_map(side.get("frag") or side.get("text") or "")[0]
    return sum(1 for ch in k if ch.isalnum()) >= 2


def _short_onesided(f: dict) -> str:
    """หายไป/เกินมาฝั่งเดียว ที่ข้อความสั้นมาก (< 2 ตัว) หรือเครื่องหมายล้วน — วัดจากข้อความของ
    Vision ล้วน (ไม่ใช้ผลของอัลกอริทึม) · คืนหมายเหตุ (ว่าง = ไม่เข้าเกณฑ์)"""
    if f["class"] not in ("MISSING_IN_B", "EXTRA_IN_B"):
        return ""
    side = f["a" if f["class"] == "MISSING_IN_B" else "b"]
    if side.get("line") is None:
        return ""
    k = compare.diff_key_map(side.get("frag") or "")[0]
    if len(k) < 2 or compare._is_punct(k):
        return ("ข้อความสั้นมาก/เครื่องหมายล้วนที่มีอยู่ฝั่งเดียว — OCR อ่านไม่นิ่ง โปรดดูด้วยตา")
    return ""


def _merge_raw(pr: dict, ai_finds: List[dict], findings: List[dict], st: dict) -> None:
    """โหมด raw — AI ตัดสินจากข้อมูลดิบของ Vision · แอปตัดสินระดับด้วย % ของ Vision เท่านั้น

    * ``real`` + Vision ≥ ``CONF_FAIL`` = แดง · ต่ำกว่า/ไม่ทราบ = เหลือง · ``uncertain`` = เหลือง
    * ``noise`` ⇒ รายการพับ ``ai_dismissed`` (ไม่นับ)
    * ``AI_RAW_SAFETY`` — กติกาที่อิงหลักฐานของ Vision ล้วน (ดู config)
    * ผลของอัลกอริทึม **ทุกจุด** ⇒ ``algo_only`` "ไว้เทียบ" (ไม่นับ · ไม่จับคู่กับจุดของ AI เพราะ
      อ้างบรรทัดคนละชุด — อัลกอริทึมใช้บรรทัดที่ต่อแถวแล้ว, AI ใช้บรรทัดดิบ)
    """
    kept, dismissed = [], []
    safe = config.AI_RAW_SAFETY
    for f in ai_finds:
        f.pop("curved", None)          # ไม่มีธงโค้งในโหมดนี้ (เป็นผลของอัลกอริทึม)
        f["raw"] = True
        v = f["ai"]["verdict"]
        c = f["confidence"]
        sure = c is not None and c >= config.CONF_FAIL
        if v == "noise":
            if safe and _hard_evidence(f):
                f["severity"] = "yellow"
                f["notes"].append("AI บอกว่าเป็นสัญญาณรบกวน แต่ Vision อ่านตัวอักษร/ตัวเลขที่ต่างได้ชัด "
                                  "(≥ %d%% ทั้งสองฝั่ง) — คงไว้ให้คนดู" % round(config.CONF_FAIL * 100))
                kept.append(f)
                continue
            f["severity"] = "dismissed"
            f["notes"].append("AI ตัดสินว่าเป็นสัญญาณรบกวนของ OCR — ไม่นับในผลตัดสิน")
            dismissed.append(f)
            continue
        if v == "real" and sure and safe and f["class"] == "PUNCT":
            f["severity"] = "yellow"
            f["notes"].append("ต่างแค่เครื่องหมายวรรคตอน — คำตอบของ AI ไม่ใช่การอ่านซ้ำ โปรดดูด้วยตา")
        elif v == "real" and sure and safe and _short_onesided(f):
            f["severity"] = "yellow"
            f["notes"].append(_short_onesided(f))
        elif v == "real" and sure:
            f["severity"] = "red"
        else:
            f["severity"] = "yellow"
            if v == "real":
                f["notes"].append("AI บอกว่าต่างจริง แต่ Vision มั่นใจในตัวอักษรนี้ต่ำกว่า %d%%"
                                  % round(config.CONF_FAIL * 100))
        kept.append(f)
    for g in findings:
        g["notes"].append("ผลของอัลกอริทึม — ไว้เทียบกับ AI เท่านั้น ไม่นับในผลตัดสิน "
                          "(โหมด AI ตัดสินจากข้อมูลดิบ)")
    pr["findings"] = kept
    pr["ai_dismissed"] = dismissed
    pr["algo_only"] = list(findings)
    st["algo_compare"] = len(findings)


def _weak_onesided(f: dict, pr: dict) -> str:
    """จุด "หายไป/เกินมา" ที่หลักฐานไม่พอเป็นแดง — กติกาเดียวกับบรรทัดเดี่ยวของอัลกอริทึม
    คืนหมายเหตุ (ว่าง = หลักฐานพอ)

    * บรรทัดที่อ้างอยู่ใน ``reflow_lines`` ⇒ อัลกอริทึมพบข้อความนี้ในอีกฝั่งแล้ว (แค่ตัดบรรทัด/
      OCR เรียงคนละที่) — เช่น ``0`` หน้าบาร์โค้ดที่ Vision ไปรวมไว้ท้ายบรรทัดตัวเลขของอีกฝั่ง
    * ข้อความสั้นมาก (< 2 ตัว) หรือเครื่องหมายล้วน ⇒ OCR อ่านไม่นิ่ง
    """
    if f["class"] not in ("MISSING_IN_B", "EXTRA_IN_B"):
        return ""
    s = "a" if f["class"] == "MISSING_IN_B" else "b"
    side = f[s]
    if side.get("line") is None:
        return ""
    rl = (pr.get("reflow_lines") or {}).get(s.upper()) or ()
    if side["line"] in rl:
        return ("ข้อความนี้มีอยู่ในอีกฝั่ง (อัลกอริทึมพบว่าแค่ตัดบรรทัด/เรียงคนละที่) — "
                "คำว่า \"หายไป\" ของ AI ไม่มีหลักฐานจาก Vision โปรดดูด้วยตา")
    k = compare.diff_key_map(side.get("frag") or "")[0]
    if len(k) < 2 or compare._is_punct(k):
        return ("ข้อความสั้นมาก/เครื่องหมายล้วนที่มีอยู่ฝั่งเดียว — OCR อ่านไม่นิ่ง "
                "(กติกาเดียวกับอัลกอริทึม) โปรดดูด้วยตา")
    return ""


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


def _url_of(mode: str) -> str:
    """โหมด raw / image ยิง workflow แยกของตัวเอง · assist/judge ยิง workflow เดิม (artwork-v2-review)"""
    if mode == "raw":
        return config.AI_RAW_URL
    if mode == "image":
        return config.AI_IMAGE_URL
    return config.AI_REVIEW_URL


_URL_ENV = {"raw": "ARTWORK_V2_AI_RAW_URL", "image": "ARTWORK_V2_AI_IMAGE_URL"}


_USAGE_SUM = ("promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount", "totalTokenCount")


def _split_payloads(payload: dict, crops: List[dict], per: int) -> List[dict]:
    """แบ่ง payload ของโหมด image (ครอป) เป็นคำขอละ ``per`` จุด — แต่ละคำขอมีเฉพาะครอปของจุดตัวเอง"""
    cands = payload.get("candidates") or []
    per = max(1, int(per))
    out = []
    for i in range(0, len(cands), per):
        grp = cands[i:i + per]
        ids = {c["id"] for c in grp}
        p = dict(payload)
        p["candidates"] = grp
        p["crops"] = [c for c in crops if c.get("candidate") in ids]
        out.append(p)
    n = len(out)
    for i, p in enumerate(out):
        p["part"], p["parts"] = i + 1, n
    return out


def _usage_tokens(u) -> str:
    if not isinstance(u, dict):
        return "-"
    return "/".join(str(u.get(k, "?")) for k in ("promptTokenCount", "candidatesTokenCount",
                                                    "thoughtsTokenCount"))


def call_split(url: str, payloads: List[dict], poster: Optional[Callable] = None,
               timeout: Optional[float] = None,
               url_env: str = "ARTWORK_V2_AI_IMAGE_URL") -> Tuple[Optional[dict], dict]:
    """ยิงหลายคำขอ (ขนานกันไม่เกิน ``AI_IMAGE_PARALLEL``) แล้วรวมเป็นคำตอบเดียวรูปแบบเดิม

    คำขอที่ล้ม ⇒ จุดของคำขอนั้น "AI ไม่ได้ตอบ" + เหตุผล (``_unanswered_why``) · คำขออื่นยังใช้ได้ ·
    ล้มทุกคำขอ ⇒ ``(None, info)`` เหมือน ``call()`` · ``info["requests"]`` = บันทึกต่อคำขอ (ลง Log)"""
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.time()
    par = max(1, min(int(config.AI_IMAGE_PARALLEL or 1), len(payloads) or 1))

    def one(p):
        return call(url, p, poster, timeout=timeout, url_env=url_env)

    if par == 1:
        results = [one(p) for p in payloads]
    else:
        with ThreadPoolExecutor(max_workers=par) as ex:
            results = list(ex.map(one, payloads))
    merged = {"reviews": [], "items": [], "summary": "", "suggestions": [], "engine": "",
              "usage": None, "_unanswered_why": {}}
    sums: Dict[str, int] = {}
    summaries, reqs = [], []
    info = {"http": None, "ms": None, "attempts": 0, "error": "", "bytes": 0}
    ok = 0
    first_err, fail_usage = "", None
    for p, (resp, inf) in zip(payloads, results):
        ids = [c["id"] for c in p.get("candidates") or []]
        info["attempts"] += inf.get("attempts") or 0
        info["bytes"] += inf.get("bytes") or 0
        rec = {"part": p.get("part"), "ids": ids, "http": inf.get("http"), "ms": inf.get("ms"),
               "attempts": inf.get("attempts"), "bytes": inf.get("bytes")}
        if resp is None:
            rec.update(status="failed", error=inf.get("error"))
            if inf.get("usage"):
                rec["usage"] = _usage_tokens(inf["usage"])
                fail_usage = fail_usage or inf["usage"]
            first_err = first_err or inf.get("error") or ""
            for cid in ids:
                merged["_unanswered_why"][cid] = "คำขอของจุดนี้ล้ม: %s" % _clip(inf.get("error"), 160)
            reqs.append(rec)
            continue
        ok += 1
        info["http"] = inf.get("http")
        revs = _as_list(resp.get("reviews"))
        rec.update(status="ok", reviews=len(revs), engine=_clip(resp.get("engine"), 60),
                   usage=_usage_tokens(resp.get("usage")),
                   finish=_clip(resp.get("finish_reason"), 30))
        if not revs:
            # N8N ตอบ 200 แต่ไม่มีผลตรวจ — บอกว่าได้คีย์อะไรมา (ไม่เก็บเนื้อหา) ไว้ไล่ workflow
            rec["keys"] = sorted(str(k) for k in resp.keys())[:12]
            for cid in ids:
                merged["_unanswered_why"][cid] = ("N8N ตอบกลับโดยไม่มีผลตรวจ (คีย์ที่ได้: %s)"
                                                  % (", ".join(rec["keys"]) or "ว่าง"))
        reqs.append(rec)
        merged["reviews"].extend(revs)
        merged["items"].extend(_as_list(resp.get("items")))
        if str(resp.get("summary") or "").strip():
            summaries.append(str(resp["summary"]).strip())
        for sg in _sugs(resp):
            if sg not in merged["suggestions"]:
                merged["suggestions"].append(sg)
        merged["engine"] = merged["engine"] or str(resp.get("engine") or "")
        u = resp.get("usage")
        if isinstance(u, dict):
            for k in _USAGE_SUM:
                if isinstance(u.get(k), (int, float)):
                    sums[k] = sums.get(k, 0) + int(u[k])
    info["ms"] = int((time.time() - t0) * 1000)
    info["requests"] = reqs
    info["requests_failed"] = len(payloads) - ok
    info["parallel"] = par
    if not ok:
        info["error"] = "ทุกคำขอล้ม (%d/%d): %s" % (len(payloads), len(payloads), first_err)
        if fail_usage:
            info["usage"] = fail_usage
        return None, info
    merged["summary"] = " · ".join(summaries)
    merged["usage"] = sums or None
    if not merged["_unanswered_why"]:
        merged.pop("_unanswered_why")
    return merged, info


def run_all(pairs: List[dict], mode: str, warnings: List[str], say: Callable,
            next_id: int, poster: Optional[Callable] = None,
            img_dir: Optional[str] = None,
            hires: Optional[Callable] = None) -> Tuple[dict, int]:
    """ทำทุกคู่โซน · คืน ``(สรุปทั้งรอบ, id ถัดไป)`` — ใช้ ``pr["_cmp"]`` (บรรทัดที่เทียบจริง)

    ``img_dir`` (โหมด image) = โฟลเดอร์ ``img/`` ของรอบ (ภาพที่ส่ง Vision · ``sides[s]["image"]``) ·
    ``hires(pr, s)`` (โหมด image · PDF) = ตัวเรนเดอร์ครอปใหม่ที่ dpi สูง (``pipeline.HiresSide``)"""
    url = _url_of(mode)
    summary = {"mode": mode, "url": url if mode != "off" else "",
               "pairs_ok": 0, "pairs_failed": 0}
    for pr in pairs:
        if mode == "off":
            pr["ai"] = {"mode": "off", "status": "off"}
            continue
        cmp_ = pr.get("_cmp")
        vc = {s: ((pr["sides"][s].get("stats") or {}).get("conf_mean")) for s in ("a", "b")}
        raw = pr.get("_raw") if mode == "raw" else None
        if pr.get("unreadable") or not cmp_ or (mode == "raw" and not raw):
            pr["ai"] = {"mode": mode, "status": "skipped", "reason": "คู่นี้อ่านไม่ได้", "vision_conf": vc}
            continue
        if mode == "image" and not (pr.get("findings") or []):
            # ไม่มีจุดต่างให้ดูภาพ ⇒ ไม่ยิง (ประหยัดโควตา · ผลเท่าเดิม)
            pr["ai"] = {"mode": mode, "status": "skipped", "vision_conf": vc,
                        "reason": ("หลักฐานภาพตัดสินครบทุกจุดแล้ว (ภาพเหมือนกัน) — ไม่ต้องถาม AI"
                                   if pr.get("pixel_same") else "ไม่มีจุดต่างให้ตรวจกับภาพ")}
            continue
        imgs = None
        crops = crop_info = None
        cstat: dict = {}
        blind = mode == "image" and bool(config.AI_IMAGE_CROPS) and bool(config.AI_IMAGE_BLIND)
        sendable = pr.get("findings") or []
        if mode == "image" and config.AI_IMAGE_CROPS:
            # ครอปรอบแต่ละจุดจากไฟล์ JPEG เดียวกับที่ส่ง Vision (ไม่เรนเดอร์ใหม่) · A/B แยกรูป
            ims = {s: (_load_image(os.path.join(img_dir or "", pr["sides"][s].get("image") or ""))
                       if img_dir and pr["sides"][s].get("image") else None) for s in ("a", "b")}
            if ims["a"] is None or ims["b"] is None:
                summary["pairs_failed"] += 1
                pr["ai"] = {"mode": mode, "status": "failed", "error": "อ่านภาพที่ส่ง Vision ไม่ได้",
                            "vision_conf": vc}
                warnings.append("คู่ %d: AI ดูภาพไม่ได้ (อ่านไฟล์ภาพไม่ได้) — ใช้ผลของอัลกอริทึม" % pr["n"])
                continue
            hi = None
            if hires is not None:
                try:
                    hi = {s: hires(pr, s) for s in ("a", "b")}
                except Exception:                    # noqa: BLE001 — เรนเดอร์ใหม่ไม่ได้ = ครอป JPEG เดิม
                    hi = None
            sendable, crops, crop_info, skip = plan_crops(pr.get("findings") or [], ims, hi, cstat,
                                                         pr.get("lines"))
            cstat["saved"] = save_crops(img_dir, pr["n"], crops, crop_info, pr.get("findings") or [],
                                        cstat.get("rots"))
            for f in pr.get("findings") or []:
                why = skip.get("F%d" % f["id"]) if f.get("id") is not None else None
                if why:
                    f["ai"] = {"verdict": None, "reason": "", "suggestion": "", "image": True,
                               "not_sent": why}
                    f["notes"].append("AI ไม่ได้ตรวจจุดนี้ (%s) — คงระดับของอัลกอริทึม" % why)
            if not sendable:
                pr["ai"] = {"mode": mode, "status": "skipped", "vision_conf": vc,
                            "reason": "ไม่มีจุดที่ครอปภาพส่งได้", "not_sent": len(skip)}
                continue
        elif mode == "image":
            imgs = {s: (image_part(os.path.join(img_dir or "", pr["sides"][s].get("image") or ""),
                                   pr["sides"][s].get("sent_px"))
                        if img_dir and pr["sides"][s].get("image") else None) for s in ("a", "b")}
            if not imgs["a"] or not imgs["b"]:
                summary["pairs_failed"] += 1
                pr["ai"] = {"mode": mode, "status": "failed", "error": "อ่านภาพที่ส่ง Vision ไม่ได้",
                            "vision_conf": vc}
                warnings.append("คู่ %d: AI ดูภาพไม่ได้ (อ่านไฟล์ภาพไม่ได้) — ใช้ผลของอัลกอริทึม" % pr["n"])
                continue
        say("กำลังให้ AI ตรวจทานคู่ %d" % pr["n"])
        if mode == "raw":
            # บรรทัดตามที่ Vision ส่ง (textmodel) — ไม่ผ่านชั้นต่อแถว/ต่อคำ · ไม่ส่งธงโค้ง
            A, B = raw["a"], raw["b"]
            curved = None
        else:
            A, B = cmp_["lines_a"], cmp_["lines_b"]
            curved = pr.get("curved_lines")
        payload = build_payload(pr["n"], mode, A, B, tuple(pr["sides"]["a"]["sent_px"]),
                                tuple(pr["sides"]["b"]["sent_px"]), sendable,
                                curved=curved, crops=crop_info, blind=blind)
        if imgs:
            payload["images"] = imgs
        if crops is not None:
            payload["crops"] = crops
            payload["image_sizes"] = {s: {"w": int(pr["sides"][s]["sent_px"][0]),
                                          "h": int(pr["sides"][s]["sent_px"][1])} for s in ("a", "b")}
        tmo = {"raw": config.AI_RAW_TIMEOUT_S, "image": config.AI_IMAGE_TIMEOUT_S}.get(mode)
        uenv = _URL_ENV.get(mode, "ARTWORK_V2_AI_REVIEW_URL")
        split = int(config.AI_IMAGE_SPLIT or 0) if crops is not None else 0
        if split > 0 and len(payload["candidates"]) > split:
            # คำขอละ N จุด (แต่ละคำขอมีเฉพาะครอปของจุดตัวเอง) · รวมคำตอบกลับเป็นรูปเดิม
            resp, info = call_split(url, _split_payloads(payload, crops, split), poster,
                                    timeout=tmo, url_env=uenv)
        else:
            resp, info = call(url, payload, poster, timeout=tmo, url_env=uenv)
        ai = {"mode": mode, "status": "ok" if resp is not None else "failed",
              "http": info["http"], "ms": info["ms"], "attempts": info["attempts"],
              "request_bytes": info["bytes"], "error": info["error"], "vision_conf": vc,
              "candidates": len(payload["candidates"])}
        if "requests" in info:
            ai["requests"] = info["requests"]
            ai["requests_failed"] = info["requests_failed"]
            ai["parallel"] = info["parallel"]
        if crops is not None:
            ai["crops"] = len(crops)
            ai["not_sent"] = len(pr.get("findings") or []) - len(sendable)
            ai["crop_px_max"] = max([max(c["w"], c["h"]) for c in crops] or [0])
            if "hires_crops" in cstat:
                ai["crops_hires"] = cstat["hires_crops"]
                ai["crop_dpi_max"] = cstat["hires_dpi_max"]
            ai["blind"] = blind
            if config.AI_IMAGE_SAVE_CROPS:
                ai["crops_saved"] = cstat.get("saved", 0)
            if config.AI_IMAGE_CROP_VERTICAL:
                ai["crops_rotated"] = cstat.get("rotated_crops", 0)
        if resp is None:
            if info.get("usage"):
                ai["usage"] = info["usage"]
            summary["pairs_failed"] += 1
            warnings.append("คู่ %d: AI ตรวจทานไม่สำเร็จ — ใช้ผลของอัลกอริทึม (%s)"
                            % (pr["n"], info["error"]))
            pr["ai"] = ai
            continue
        # merge แก้รายการในที่ — ล้มกลางทาง ⇒ คืนผลอัลกอริทึมเดิมทุกตัวอักษร (ตามที่คำเตือนบอก)
        saved = {k: copy.deepcopy(pr[k]) for k in _MERGE_KEYS if k in pr}
        try:
            st = merge(mode, pr, resp, A, B, blind=blind)
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
        if info.get("requests_failed"):
            bad = [r for r in info["requests"] if r.get("status") != "ok"]
            warnings.append("คู่ %d: AI ล้ม %d จาก %d คำขอ (%s) — จุด %s ใช้ผลของอัลกอริทึม"
                            % (pr["n"], len(bad), len(info["requests"]), _clip(bad[0].get("error"), 160),
                               ", ".join(i for r in bad for i in r["ids"])))
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
