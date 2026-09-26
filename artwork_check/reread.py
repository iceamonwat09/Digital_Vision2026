# -*- coding: utf-8 -*-
"""อ่านซ้ำ "เฉพาะบรรทัดที่ต่าง" (P6, 26 ก.ย. 2026) — **โหมดทดลอง ปิดเป็นค่าเริ่มต้น**

ที่มา: คู่ Friskies รอบ 1 — OCR **ทั้งสองฝั่ง** อ่านบรรทัดเกาหลีเพี้ยนคนละ
แบบ ⇒ พยาน text layer (F2) พิสูจน์ได้แค่ฝั่งของตัวเอง ⇒ การ์ดคง critical
(ถูกต้องตามกติกา แต่ผู้ตรวจต้องไล่เองว่าต่างจริงไหม) · ฝั่งที่เป็นภาพ raster
ไม่มีพยานเลย

แนวคิด: ครอปอ่านเล็ก ๆ อ่านนิ่งกว่าทั้งแผงมาก (ผลวัด 5 ก.ย.: ครอปเซลล์
``24%``/``20%`` อ่านถูกทุกรอบ ทุก pad) ⇒ ครอป **เฉพาะบรรทัดที่ต่าง** ของ
ฝั่งที่ไม่มีพยาน แล้วอ่านซ้ำ แล้วบอกผลให้คนดู

🔒 กติกา (แต่ละข้อคือกฎเหล็กข้อ 2):

* **advisory ล้วน — ไม่แตะระดับความรุนแรง ไม่ลบการ์ด** — การอ่านซ้ำเป็น
  แหล่งเดียว (engine เดิม) ⇒ ยังไม่ใช่ "สองแหล่งอิสระที่เห็นตรงกัน"
* หาตำแหน่งบรรทัดจาก ``blocks`` ของ OCR เท่านั้น — หาไม่เจอ/กำกวม ⇒ **ไม่อ่าน**
  (ไม่เดาตำแหน่ง) · ภาพที่เรนเดอร์ใหม่ต้องขนาดเท่ากับภาพที่ OCR เห็นจริง
  (``ocr_wh``) ไม่งั้นพิกัดไม่ตรง ⇒ ไม่อ่าน
* ตัดสินจาก **ช่วงที่ต่าง** ของสองฝั่ง: ครอปมีเฉพาะของอีกฝั่ง ⇒
  ``matches_other`` · มีเฉพาะของตัวเอง ⇒ ``same`` · อื่น ๆ ⇒ ``unclear``
* ใช้โควตา OCR เพิ่ม ⇒ เพดาน ``LINE_REREAD_MAX`` ครั้งต่อการตรวจ ·
  หมดเวลารวม ⇒ หยุด
"""
import logging
import time
from typing import Dict, List, Optional

from . import checks as K
from . import config
from . import highlight
from . import ocr as ocr_mod
from . import witness as W
from .pdf_ingest import apply_rotation

logger = logging.getLogger(__name__)

MIN_COVER = 0.60        # block ที่ตรงกับบรรทัดต้องครอบอย่างน้อยเท่านี้
MAX_ROWS = 4.0          # ความสูงรวม ≤ 4 เท่าของ block กลาง (บรรทัดเดียว ~ไหล 2-3 แถว)


def _key(s: str) -> str:
    return K._norm_key(s or "").replace(" ", "")


def line_box(line: str, blocks: list, ocr_wh) -> Optional[tuple]:
    """กรอบ (สัดส่วน 0..1 ของภาพที่ OCR เห็น) ของบรรทัด ``line`` จาก
    ``blocks`` — คืน ``None`` เมื่อหาไม่เจอหรือไม่มั่นใจ"""
    key = _key(line)
    if len(key) < 4 or not blocks:
        return None
    scale = highlight._infer_scale(blocks, ocr_wh)
    if scale is None:
        return None
    sx, sy = scale
    whole, parts = [], []
    for b in blocks:
        if not isinstance(b, dict) or not b.get("bbox"):
            continue
        bk = _key(b.get("text", ""))
        try:
            x, y, w, h = (float(v) for v in b["bbox"])
        except (TypeError, ValueError):
            continue
        if w <= 0 or h <= 0 or len(bk) < 3:
            continue
        fr = (x / sx, y / sy, (x + w) / sx, (y + h) / sy)
        if key in bk:
            whole.append((len(bk), fr))
        elif bk in key:
            parts.append((len(bk), fr))
    if len(whole) == 1:
        return whole[0][1]
    if whole:
        return None                             # บรรทัดเดียวกันหลายที่ = กำกวม
    if not parts or sum(n for n, _ in parts) < MIN_COVER * len(key):
        return None
    hs = sorted(fr[3] - fr[1] for _, fr in parts)
    x0 = min(fr[0] for _, fr in parts)
    y0 = min(fr[1] for _, fr in parts)
    x1 = max(fr[2] for _, fr in parts)
    y1 = max(fr[3] for _, fr in parts)
    if (y1 - y0) > MAX_ROWS * hs[len(hs) // 2]:
        return None                             # กระจายหลายแถว = ไม่ใช่บรรทัดเดียว
    return (x0, y0, x1, y1)


def judge(reread: str, own: str, other: str) -> str:
    """เทียบข้อความที่อ่านซ้ำกับ **ช่วงที่ต่าง** ของสองฝั่ง"""
    rk = _key(reread)
    if not rk:
        return "unclear"
    so, sx = K.diff_spans(own, other)
    mine = [_key(own[a:b]) for a, b in so]
    theirs = [_key(other[a:b]) for a, b in sx]
    mine = [m for m in mine if m]
    theirs = [t for t in theirs if t]
    if not mine and not theirs:
        return "unclear"
    any_mine = any(m in rk for m in mine)
    any_theirs = any(t in rk for t in theirs)
    # ช่วงของฝั่งหนึ่งว่าง (อีกฝั่งมีคำเกิน) ⇒ ตัดสินจากการ "มี/ไม่มี" ของ
    # ช่วงฝั่งที่ไม่ว่างเท่านั้น
    if not any_mine and (all(t in rk for t in theirs) if theirs else True):
        return "matches_other"
    if not any_theirs and (all(m in rk for m in mine) if mine else True):
        return "same"
    return "unclear"


def _crop(doc, zone: dict, r: dict, box: tuple):
    img = ocr_mod._render_for_ocr(doc, zone["bbox"])
    if img is None or getattr(img, "size", 0) == 0:
        return None
    ang = r.get("rotate") if isinstance(r.get("rotate"), int) else 0
    if ang:
        img = apply_rotation(img, ang)
    wh = r.get("ocr_wh") or []
    if len(wh) != 2 or [img.shape[1], img.shape[0]] != [int(wh[0]), int(wh[1])]:
        return None                             # พิกัดของ blocks ไม่ตรงภาพนี้
    H, Wd = img.shape[:2]
    x0, y0, x1, y1 = box
    ph = 0.5 * (y1 - y0)
    xa = max(0, int((x0 - 0.03) * Wd))
    xb = min(Wd, int((x1 + 0.03) * Wd) + 1)
    ya = max(0, int((y0 - ph) * H))
    yb = min(H, int((y1 + ph) * H) + 1)
    if xb - xa < 8 or yb - ya < 8:
        return None
    return img[ya:yb, xa:xb]


def apply(defects: List[dict], zones: List[dict], ocr_results: List[dict],
          docs: Dict[str, object], deadline: Optional[float] = None,
          progress=None) -> List[dict]:
    """แนบ ``reread`` ลงการ์ด ``MISMATCH_PANELS`` คู่ (พบ/เทียบกับ) ที่ยัง
    critical — ไม่แตะระดับ ไม่ลบ · ปิดธง ⇒ คืนรายการเดิม (ตัวเดียวกัน)"""
    if not config.LINE_REREAD:
        return defects
    pairs = W._pairs(zones)
    zmap = {z["id"]: z for z in zones}
    rmap = {r["zone_id"]: r for r in ocr_results}
    budget = int(config.LINE_REREAD_MAX or 0)
    out = []
    for d in defects:
        if (budget <= 0 or d.get("class") != "MISMATCH_PANELS"
                or d.get("severity") != "critical" or d.get("witness")
                or not d.get("found") or not d.get("reference")):
            out.append(d)
            continue
        z, p = d["zone_id"], pairs.get(d["zone_id"])
        refs = d.get("ref_zone_ids") or []
        if not p or (refs and refs != [p]):
            out.append(d)
            continue
        got = []
        for side, own, other in ((z, d["found"], d["reference"]),
                                 (p, d["reference"], d["found"])):
            r = rmap.get(side) or {}
            if (budget <= 0 or r.get("engine") in ("pdf-text", "none", None)
                    or r.get("witness")):
                continue
            if deadline and time.time() > deadline:
                budget = 0
                break
            box = line_box(own, r.get("blocks") or [], r.get("ocr_wh"))
            doc = docs.get(zmap.get(side, {}).get("doc", "a"))
            if box is None or doc is None:
                continue
            try:
                crop = _crop(doc, zmap[side], r, box)
            except Exception:                   # pragma: no cover - กันพังล้วน
                logger.exception("[artwork] reread: ครอปไม่สำเร็จ")
                crop = None
            if crop is None:
                continue
            budget -= 1
            txt = ocr_mod.read_image(crop).get("text", "")
            v = judge(txt, own, other)
            got.append({"zone": side, "text": txt[:300], "verdict": v})
            if progress is not None:
                progress.note("checks", "อ่านซ้ำเฉพาะบรรทัด %s → %s" % (side, v))
        if got:
            d = dict(d, reread=got)
        out.append(d)
    return out
