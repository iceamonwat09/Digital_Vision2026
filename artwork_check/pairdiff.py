"""
โหมดทดลอง "🤝 เทียบคู่ด้วย Gemini" (ช่องติ๊ก ``pair_check`` · ค่าเริ่มต้นปิด)

ที่มา (ผลรันจริง 30 ก.ย. 2026 · PDF เทียบภาพถ่าย JPEG): FAIL 9 รายการ
**ไม่มีของจริงเลย** — 8 รายการคือ OCR อ่านสองฝั่ง *แยกกัน* แล้วเพี้ยนคนละทาง
(``0g``→``Og`` · ``1g``→``19`` · ข้อความโค้งบนตราสัญลักษณ์) ⇒ ชั้นเทียบ
ข้อความเห็นเป็น "ต่างกัน" ทุกจุด

แนวคิด: ส่งภาพโซน 🅰 กับ 🅱 ของกลุ่มเดียวกัน **ไปในคำขอเดียว** ให้ Gemini
(1) ถอดความทั้งสองภาพตามตัวอักษร และ (2) ระบุความต่างเป็น JSON
⇒ ความเพี้ยนของการอ่านมีโอกาส "ตรงกัน" สองฝั่ง (common-mode) แทนที่จะสุ่ม
คนละทาง — ตรงกับบทเรียน 5 ก.ย. *"ไม่ต้องการให้อ่านแม่น ต้องการให้อ่านเหมือนกัน"*

🔑 **ไม่ได้เชื่อคำตัดสินของ LLM ล้วน ๆ** (ออกแบบแบบผสมตามที่ตกลงกับผู้ใช้):

  ชั้น 1  ข้อความที่ Gemini ถอดมาทั้งสองฝั่ง → ``checks.py`` ตัวเดิม
          (deterministic · ตรวจย้อนได้ · ผู้ตรวจเปิดดูข้อความได้เหมือนเดิม)
  ชั้น 2  รายการความต่างที่ Gemini ระบุ → ต้อง **หาเจอในข้อความที่ถอดมา**
          ก่อนจะนับ (ไม่งั้นเป็นแค่ "ยืนยันไม่ได้" ในกล่องสรุป ไม่วาดกรอบ)

  สองชั้นเห็นตรงกัน           → คง severity เดิม (critical)
  ชั้น 1 ฟ้อง แต่ Gemini ไม่ระบุ → ``warning`` (REVIEW) — ไม่ลบทิ้ง
  Gemini ระบุ + ยืนยันได้ แต่ชั้น 1 ไม่ฟ้อง → เพิ่มเป็น ``warning``
  ความต่างด้านภาพ (ฟอนต์/สี/โลโก้) → แสดงในกล่องสรุปเท่านั้น (ชี้จุดไม่ได้)

ความปลอดภัย (กฎเหล็ก):
* ใช้เฉพาะกลุ่มที่มี **2 โซนพอดี · ไฟล์ละ 1 โซน** — กลุ่มอื่นใช้เส้นทางเดิม
* การอ่านทีละโซนแบบเดิม **ยังทำงานคู่ขนานเสมอ** ⇒ คำขอคู่ล้มเหลว / หมดเวลา
  ⇒ กลุ่มนั้นใช้ผลเดิมทันที (ไม่มีทางได้ผลแย่กว่าไม่ติ๊ก เพราะหาย) และได้ตัวเลข
  A/B ในรอบเดียว ("โหมดเดิมจะฟ้อง N · โหมดคู่ฟ้อง M")
* โมดูลนี้ไม่ import Flask · ฟังก์ชันหลัก **ไม่โยน exception**
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FutTimeout
from typing import Callable, Dict, List, Optional

from . import checks, config

logger = logging.getLogger(__name__)

KINDS = ("text", "number", "visual")
_MAX_QUOTE = 300          # ตัดคำพูดยาวผิดปกติ (กันคำตอบที่หลุดโครงสร้าง)
_MAX_DIFFS = 60           # เพดานรายการความต่างต่อคู่ (กันคำตอบวนไม่จบ)


# ── เลือกคู่ ──────────────────────────────────────────────────────────

def eligible_pairs(zone_list: List[dict]) -> List[dict]:
    """กลุ่มที่เทียบคู่ได้: **2 โซนพอดี · 🅰 1 โซน + 🅱 1 โซน** · ชนิด panel/header.

    ไม่ครอบ zoom (zoom เทียบกับ panel ในไฟล์เดียวกัน คนละเรื่อง) และไม่ครอบ
    กลุ่มที่มีหลาย panel (ใช้เสียงข้างมากของชั้นเดิมดีกว่า)
    """
    groups: Dict[str, List[dict]] = {}
    order: List[str] = []
    for z in zone_list or []:
        if z.get("type") == "ignore":
            continue
        g = str(z.get("group") or "").strip()
        if not g:
            continue
        if g not in groups:
            groups[g] = []
            order.append(g)
        groups[g].append(z)
    out = []
    for g in order:
        zs = groups[g]
        if len(zs) != 2:
            continue
        if any(z.get("type") not in ("panel", "header") for z in zs):
            continue
        a = [z for z in zs if z.get("doc", "a") == "a"]
        b = [z for z in zs if z.get("doc", "a") == "b"]
        if len(a) == 1 and len(b) == 1:
            # ⚠️ สำเนา — pipeline เขียนทับ ``rotate`` ของโซนหลังการอ่านทีละ
            #    โซน ขณะที่เธรดของคำขอคู่ยังอ่านค่านี้อยู่ (race)
            out.append({"group": g, "a": dict(a[0]), "b": dict(b[0])})
    return out


# ── คำตอบจาก N8N ─────────────────────────────────────────────────────

def _fail(msg: str, retry: bool = False) -> dict:
    return {"ok": False, "error": msg, "retry": retry}


def _clean_quote(v) -> str:
    return str(v if v is not None else "").strip()[:_MAX_QUOTE]


def _normalize_diffs(raw) -> List[dict]:
    if not isinstance(raw, list):
        return []
    out = []
    for it in raw[:_MAX_DIFFS]:
        if not isinstance(it, dict):
            continue
        a, b = _clean_quote(it.get("a")), _clean_quote(it.get("b"))
        where = _clean_quote(it.get("where"))[:200]
        kind = str(it.get("kind") or "").strip().lower()
        if kind not in KINDS:
            kind = "text"
        if not (a or b or where):
            continue
        out.append({"a": a, "b": b, "kind": kind, "where": where})
    return out


def parse_response(raw: str, ctype: str = "") -> dict:
    """แกะคำตอบของ workflow ``artwork-pair`` → สัญญาเดียว.

    คืน ``{"ok": True, "a_text", "b_text", "differences", "engine", "warning"}``
    หรือ ``{"ok": False, "error", "retry"}``

    ⚠️ **คำตอบว่าง = ล้มเหลว (ลองซ้ำได้)** ไม่ใช่ "ไม่พบข้อความ" — บทเรียน
       จากงานจริง 30 ก.ย.: N8N ตอบ HTTP 200 เนื้อหาว่าง แล้วระบบเดิมรายงานว่า
       "OCR ไม่พบข้อความ" ทั้งที่โซนมีบล็อกส่วนผสมเต็ม ๆ
    ⚠️ ต้องได้ข้อความ **ทั้งสองฝั่ง** — ชั้น 1 (deterministic) ต้องใช้ ถ้าไม่มี
       เท่ากับเหลือแต่คำตัดสินของ LLM ที่ตรวจย้อนไม่ได้ ⇒ ถือว่าล้มเหลว
    """
    from inspectors.ocr_n8n import _looks_like_html, _strip_fence

    body = (raw or "").strip()
    if not body:
        return _fail("N8N ตอบกลับว่าง (0 bytes) — ดู Executions ของ workflow "
                     "artwork-pair ใน N8N", retry=True)
    if _looks_like_html(body, ctype):
        return _fail("N8N ตอบกลับเป็นหน้าเว็บ (HTML) — workflow artwork-pair "
                     "ถูก Activate และ path ถูกต้องหรือไม่: "
                     + " ".join(body.split())[:120])
    payload = None
    for cand in (body, _strip_fence(body)):
        try:
            payload = json.loads(cand)
            break
        except ValueError:
            continue
    if payload is None:
        return _fail("N8N ตอบกลับไม่ใช่ JSON: " + " ".join(body.split())[:120])
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if isinstance(payload, dict) and "a_text" not in payload:
        for key in ("data", "result", "output", "response", "content"):
            inner = payload.get(key)
            if isinstance(inner, str):
                try:
                    inner = json.loads(_strip_fence(inner))
                except ValueError:
                    inner = None
            if isinstance(inner, dict) and "a_text" in inner:
                payload = inner
                break
    if not isinstance(payload, dict):
        return _fail("คำตอบของ N8N ไม่ใช่ object JSON")
    err = str(payload.get("error") or "").strip()
    a_text = str(payload.get("a_text") or "")
    b_text = str(payload.get("b_text") or "")
    if err and not (a_text.strip() and b_text.strip()):
        return _fail("workflow แจ้งว่า: " + err[:200], retry=False)
    if not a_text.strip() or not b_text.strip():
        return _fail("Gemini ไม่ได้ส่งข้อความของทั้งสองฝั่งมา "
                     "(ต้องมีทั้ง a_text และ b_text)")
    return {
        "ok": True,
        "a_text": a_text,
        "b_text": b_text,
        "differences": _normalize_diffs(payload.get("differences")),
        "engine": str(payload.get("engine") or "gemini").strip()[:60],
        "warning": " · ".join(x for x in (
            str(payload.get("warning") or "").strip(), err) if x)[:300],
    }


def call_pair(img_a: bytes, img_b: bytes, url: Optional[str] = None,
              timeout: Optional[float] = None) -> dict:
    """ยิงภาพสองภาพไป workflow ``artwork-pair`` — **ไม่โยน exception**.

    ลองซ้ำเฉพาะความล้มเหลวชั่วคราว: ต่อไม่ติด · timeout · 5xx · **คำตอบว่าง**
    """
    import requests

    target = (url if url is not None else config.PAIR_WEBHOOK_URL).strip()
    if not target:
        return _fail("ไม่ได้ตั้ง ARTWORK_PAIR_WEBHOOK_URL")
    t = float(timeout if timeout is not None else config.PAIR_TIMEOUT_S)
    data = {"image_a_b64": base64.b64encode(img_a).decode("ascii"),
            "image_b_b64": base64.b64encode(img_b).decode("ascii")}
    tries = max(1, int(config.PAIR_RETRIES) + 1)
    last = _fail("ยังไม่ได้ยิง")
    for attempt in range(tries):
        try:
            resp = requests.post(target, data=data, timeout=t)
            if resp.status_code >= 500:
                last = _fail("N8N ตอบ HTTP %d" % resp.status_code, retry=True)
            elif resp.status_code >= 400:
                # 404 = workflow ไม่ได้ Activate · 413 = ภาพใหญ่ไป ⇒ ยิงซ้ำ
                # กี่ครั้งก็ผลเดิม ไม่ลองซ้ำ
                return _fail("N8N ตอบ HTTP %d — %s" % (
                    resp.status_code, " ".join((resp.text or "").split())[:120]))
            else:
                last = parse_response(resp.text or "",
                                      resp.headers.get("Content-Type", ""))
        except Exception as e:                      # noqa: BLE001
            retriable = isinstance(e, requests.RequestException)
            last = _fail("ยิง N8N ไม่สำเร็จ: %s" % e, retry=retriable)
        if last.get("ok") or not last.get("retry") or attempt >= tries - 1:
            break
        time.sleep(config.PAIR_RETRY_WAIT_S * (2 ** attempt))
    last.pop("retry", None)
    return last


# ── เรนเดอร์ + ยิงทุกคู่พร้อมกัน ────────────────────────────────────────

def render_zone_jpg(doc, zone: dict, page_auto: bool = False) -> bytes:
    """ภาพของโซนแบบเดียวกับที่ OCR ทีละโซนเห็นทุกประการ (ความละเอียด ·
    ชั้นเพิ่ม DPI ของโซนเล็ก · การหมุน) ⇒ เทียบ A/B กับโหมดเดิมได้ยุติธรรม"""
    from . import ocr as ocr_mod
    from .pdf_ingest import apply_rotation, encode_jpg, resolve_rotation

    crop = ocr_mod._render_for_ocr(doc, zone["bbox"])
    if crop is None or crop.size == 0:
        raise ValueError("โซนว่าง (bbox ตัดออกนอกหน้า)")
    angle = resolve_rotation(zone.get("rotate", "default"), page_auto, crop)
    if angle:
        crop = apply_rotation(crop, angle)
    return encode_jpg(crop)


def _run_one(src_a: str, src_b: str, pair: dict, page_auto: bool,
             caller: Callable) -> dict:
    from .pdf_ingest import ArtworkDocument

    za, zb = pair["a"], pair["b"]
    base = {"group": pair["group"], "a_id": za["id"], "b_id": zb["id"]}
    t0 = time.time()
    try:
        img_a = render_zone_jpg(ArtworkDocument(src_a), za, page_auto)
        img_b = render_zone_jpg(ArtworkDocument(src_b), zb, page_auto)
        r = caller(img_a, img_b)
    except Exception as e:                          # noqa: BLE001
        r = {"ok": False, "error": "เตรียมภาพคู่ไม่สำเร็จ: %s" % e}
    r = dict(r or {})
    r.update(base)
    r["ms"] = int((time.time() - t0) * 1000)
    return r


class PairJob:
    """ยิงทุกคู่ในเบื้องหลัง — เริ่ม *ก่อน* การอ่านทีละโซน แล้วเก็บผลทีหลัง
    ⇒ เวลารวม ≈ ตัวที่นานกว่า ไม่ใช่ผลบวก"""

    def __init__(self, src_a: str, src_b: str, pairs: List[dict],
                 page_auto: bool = False, parallel: Optional[int] = None,
                 caller: Optional[Callable] = None):
        self.pairs = list(pairs or [])
        self._futs = []
        self._ex = None
        if not self.pairs:
            return
        n = config.PAIR_PARALLEL if parallel is None else parallel
        try:
            n = max(1, int(n))
        except (TypeError, ValueError):
            n = 1
        self._ex = ThreadPoolExecutor(max_workers=min(n, len(self.pairs)))
        fn = caller or call_pair
        self._futs = [self._ex.submit(_run_one, src_a, src_b, p, page_auto, fn)
                      for p in self.pairs]

    def results(self, deadline: Optional[float] = None) -> List[dict]:
        out = []
        for p, f in zip(self.pairs, self._futs):
            left = None if deadline is None else max(0.0, deadline - time.time())
            try:
                r = f.result(timeout=left)
            except _FutTimeout:
                r = {"ok": False, "group": p["group"], "a_id": p["a"]["id"],
                     "b_id": p["b"]["id"],
                     "error": "หมดเวลาการตรวจรวมก่อน Gemini ตอบ — ใช้ผลโหมดเดิม"}
            except Exception as e:                  # noqa: BLE001
                r = {"ok": False, "group": p["group"], "a_id": p["a"]["id"],
                     "b_id": p["b"]["id"], "error": "เทียบคู่ไม่สำเร็จ: %s" % e}
            out.append(r)
        if self._ex is not None:
            self._ex.shutdown(wait=False)
        return out


# ── ใช้ข้อความที่ถอดคู่แทนข้อความเดี่ยว ──────────────────────────────

def apply_texts(ocr_results: List[dict], results: List[dict]) -> List[dict]:
    """สำเนาของ ``ocr_results`` ที่โซนของคู่ที่สำเร็จใช้ข้อความจากการถอดคู่.

    เก็บข้อความเดี่ยวเดิมไว้ที่ ``indiv_text`` (ผู้ตรวจเปิดเทียบได้) · ``blocks``
    ของการอ่านเดี่ยวคงไว้ (เป็นพิกัดที่วัดจากภาพเดียวกัน ชั้นกรอบแดงยังต้อง
    พิสูจน์ด้วย Tesseract ก่อนวาดเสมอ) · ``error`` ของการอ่านเดี่ยวถูกล้าง
    เพราะข้อความที่ใช้ตัดสินตอนนี้มาจากคำขอคู่ซึ่งสำเร็จแล้ว
    """
    repl: Dict[str, tuple] = {}
    for r in results or []:
        if not r.get("ok"):
            continue
        eng = "pair:" + (r.get("engine") or "gemini")
        repl[r["a_id"]] = (r["a_text"], eng, r["b_id"])
        repl[r["b_id"]] = (r["b_text"], eng, r["a_id"])
    out = []
    for e in ocr_results or []:
        zid = e.get("zone_id")
        if zid not in repl:
            out.append(e)
            continue
        text, eng, other = repl[zid]
        n = dict(e)
        n["indiv_text"] = e.get("text", "")
        n["indiv_engine"] = e.get("engine", "")
        n["indiv_error"] = e.get("error", "") or ""
        n["text"] = text
        n["engine"] = eng
        n["conf"] = None
        n.pop("error", None)
        n["note"] = " · ".join(x for x in (
            "อ่านคู่กับ %s ในคำขอเดียว (โหมดเทียบคู่) · ข้อความจากการอ่าน"
            "เดี่ยวเดิม %d ตัวอักษร" % (other, len(e.get("text") or "")),
            e.get("note")) if x)
        out.append(n)
    return out


# ── รวมผลสองชั้น ─────────────────────────────────────────────────────

def _k(s) -> str:
    return checks._norm_key(s or "")


def _ws(s) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s or "")).strip()


def diff_matches_defect(diff: dict, d: dict) -> bool:
    """ความต่างที่ Gemini ระบุ ชี้ไปที่เดียวกับ defect ของชั้น 1 หรือไม่"""
    keys = [k for k in (_k(diff.get("a")), _k(diff.get("b"))) if len(k) >= 2]
    dks = [k for k in (_k(d.get("found")), _k(d.get("reference"))) if k]
    return any(k in dk or (len(dk) >= 2 and dk in k)
               for k in keys for dk in dks)


def _present(quote: str, text: str, raw_only: bool = False) -> bool:
    if not quote:
        return True
    if _ws(quote) and _ws(quote) in _ws(text):
        return True
    if raw_only:
        return False
    kq = _k(quote)
    return len(kq) >= 2 and kq in _k(text)


def verify_diff(diff: dict, a_text: str, b_text: str) -> bool:
    """Gemini อ้างว่าต่าง — ข้อความที่ยกมาต้อง **มีอยู่จริง** ในที่ถอดมา.

    * ทั้งสองฝั่งว่าง / เหมือนกันทุกตัวอักษร ⇒ ไม่ใช่ความต่าง
    * ฝั่งหนึ่งว่าง (= "หายไป") ⇒ คำที่อีกฝั่งยกมาต้อง *ไม่อยู่* ในฝั่งที่ว่าง
    * คีย์หลัง normalize เท่ากัน (ต่างแค่จุด/เครื่องหมาย เช่น ``1.5`` กับ
      ``15``) ⇒ ต้องเจอ **ตรงตัวอักษร** ทั้งสองฝั่ง เพราะเป็นชนิดความต่างที่
      ชั้นเดิมมองไม่เห็น จึงต้องมีหลักฐานแน่นที่สุด
    """
    a, b = diff.get("a") or "", diff.get("b") or ""
    if diff.get("kind") == "visual":
        return False
    if not (a or b) or _ws(a) == _ws(b):
        return False
    if not a:
        return _present(b, b_text) and not _present(b, a_text)
    if not b:
        return _present(a, a_text) and not _present(a, b_text)
    raw_only = _k(a) == _k(b)
    return _present(a, a_text, raw_only) and _present(b, b_text, raw_only)


def merge(base_defects: List[dict], pair_defects: List[dict],
          results: List[dict], downgrade: Optional[bool] = None):
    """คืน ``(defects, info)``.

    * โซนที่ **ไม่ได้** อยู่ในคู่ที่สำเร็จ ⇒ ใช้ ``base_defects`` เดิมทุกรายการ
    * โซนในคู่ที่สำเร็จ ⇒ ใช้ ``pair_defects`` (ชั้น 1 บนข้อความที่ถอดคู่)
      แล้วเทียบกับรายการของ Gemini (ชั้น 2) ตามตารางในหัวไฟล์
    """
    if downgrade is None:
        downgrade = config.PAIR_DOWNGRADE_UNAGREED
    ok = [r for r in results or [] if r.get("ok")]
    zone_pair = {}
    for r in ok:
        zone_pair[r["a_id"]] = r
        zone_pair[r["b_id"]] = r

    out = [d for d in base_defects or [] if d.get("zone_id") not in zone_pair]
    stat = {r["group"]: {"agreed": 0, "downgraded": 0, "added": 0,
                         "unverified": [], "visual": []} for r in ok}
    used = {id(r): set() for r in ok}

    for d in pair_defects or []:
        r = zone_pair.get(d.get("zone_id"))
        if r is None:
            continue
        d = dict(d)
        if str(d.get("class", "")).startswith("MISMATCH_"):
            hit = [i for i, df in enumerate(r.get("differences") or [])
                   if df.get("kind") != "visual" and diff_matches_defect(df, d)]
            if hit:
                used[id(r)].update(hit)
                d["pair_agree"] = True
                stat[r["group"]]["agreed"] += 1
            else:
                d["pair_agree"] = False
                if downgrade and d.get("severity") == "critical":
                    d["severity"] = "warning"
                    stat[r["group"]]["downgraded"] += 1
                d["message"] = (d.get("message", "") +
                                " · ⚠ Gemini เทียบภาพคู่แล้วไม่ได้ระบุว่าต่าง"
                                " — โปรดดูภาพยืนยัน")
        out.append(d)

    for r in ok:
        st = stat[r["group"]]
        for i, df in enumerate(r.get("differences") or []):
            if df.get("kind") == "visual":
                st["visual"].append(df)
                continue
            if i in used[id(r)]:
                continue
            if not verify_diff(df, r.get("a_text", ""), r.get("b_text", "")):
                st["unverified"].append(df)
                continue
            d = checks._defect(
                "MISMATCH_PANELS", r["a_id"],
                "กลุ่ม %s: Gemini เทียบภาพคู่แล้วพบความต่าง "
                "(ชั้นเทียบข้อความไม่ได้ฟ้อง) — โปรดดูภาพยืนยัน%s"
                % (r["group"], (" · " + df["where"]) if df.get("where") else ""),
                found=df.get("a", ""), reference=df.get("b", ""),
                ref_zone_ids=[r["b_id"]])
            d["severity"] = "warning"
            d["source"] = "pair_llm"
            d["pair_kind"] = df.get("kind", "text")
            out.append(d)
            st["added"] += 1

    pairs_info = []
    for r in results or []:
        e = {"group": r.get("group"), "a_id": r.get("a_id"),
             "b_id": r.get("b_id"), "ms": r.get("ms")}
        if not r.get("ok"):
            e.update(status="failed", error=r.get("error", ""))
        else:
            st = stat[r["group"]]
            e.update(status="ok", engine=r.get("engine", ""),
                     warning=r.get("warning", ""),
                     n_diff=len(r.get("differences") or []),
                     agreed=st["agreed"], downgraded=st["downgraded"],
                     added=st["added"], unverified=st["unverified"][:20],
                     visual=st["visual"][:20],
                     base_count=sum(1 for d in base_defects or []
                                    if d.get("zone_id") in
                                    (r["a_id"], r["b_id"])),
                     pair_count=sum(1 for d in out if d.get("zone_id") in
                                    (r["a_id"], r["b_id"])))
        pairs_info.append(e)
    info = {"pairs": pairs_info, "used": len(ok),
            "baseline_count": len(base_defects or []),
            "final_count": len(out)}
    return out, info


# ── ชั้นกันพลาดตัวพิมพ์ (ลอกภาพ 🅰 ไปใส่ 🅱) ──────────────────────────────

def independent_texts(pair_ocr: List[dict]) -> List[dict]:
    """สำเนาที่โซนของคู่ใช้ข้อความจาก **การอ่านแยกทีละโซน** แทนการถอดคู่.

    การอ่านแยกไม่เห็นอีกภาพเลย ⇒ ไม่มีทางลอกตัวอักษรจากอีกฝั่ง · โซนที่อ่าน
    แยกไม่สำเร็จ (error/ว่าง) คงข้อความโหมดคู่ไว้ ⇒ ยังเทียบได้ครึ่งหนึ่ง
    (เคสจริง: z1 หมดเวลา แต่ b4 อ่านแยกได้ ``D-calcium``)
    """
    out = []
    for e in pair_ocr or []:
        if "indiv_text" not in e:
            out.append(e)
            continue
        txt = e.get("indiv_text") or ""
        if txt.strip() and not e.get("indiv_error"):
            n = dict(e)
            n["text"] = txt
            n["engine"] = e.get("indiv_engine") or e.get("engine")
            out.append(n)
        else:
            out.append(e)
    return out


def case_guard(defects: List[dict], indep_defects: List[dict],
               results: List[dict], info: dict):
    """เพิ่ม ``MISMATCH_CASE`` ที่การอ่านแยกเห็น แต่ผลรวมโหมดคู่ไม่มี.

    ไม่ลบ/ไม่แก้รายการเดิมใด ๆ · กันซ้ำด้วย (โซน, ข้อความที่ normalize แล้ว)
    """
    ok = [r for r in results or [] if r.get("ok")]
    zone_pair = {}
    for r in ok:
        zone_pair[r["a_id"]] = r
        zone_pair[r["b_id"]] = r
    have = set()
    for d in defects or []:
        for side in ("found", "reference"):
            if d.get(side):
                have.add((d.get("zone_id"), _k(d[side])))
    out = list(defects or [])
    kept: Dict[str, int] = {}
    for d in indep_defects or []:
        if d.get("class") != "MISMATCH_CASE":
            continue
        r = zone_pair.get(d.get("zone_id"))
        if r is None:
            continue
        keys = [(d.get("zone_id"), _k(d[s])) for s in ("found", "reference")
                if d.get(s)]
        if any(k in have for k in keys):
            continue
        n = dict(d)
        n["pair_agree"] = False
        n["source"] = "indiv_case"
        n["message"] = (n.get("message", "") +
                        " · 🔠 การอ่านแยกทีละโซนเห็นตัวพิมพ์ต่าง แต่โหมดเทียบคู่"
                        "ไม่เห็น (Gemini มักถอดภาพ 🅱 ตามภาพ 🅰) — โปรดดูภาพยืนยัน")
        out.append(n)
        have.update(keys)
        kept[r["group"]] = kept.get(r["group"], 0) + 1
    info = dict(info or {})
    pairs = []
    for p in info.get("pairs") or []:
        p = dict(p)
        if p.get("status") == "ok":
            p["case_kept"] = kept.get(p.get("group"), 0)
            p["pair_count"] = sum(1 for d in out if d.get("zone_id") in
                                  (p.get("a_id"), p.get("b_id")))
        pairs.append(p)
    info["pairs"] = pairs
    info["case_kept"] = sum(kept.values())
    info["final_count"] = len(out)
    return out, info


# ── อุ่นแคช Tesseract ระหว่างรอ Gemini ──────────────────────────────────

def prewarm_highlight(zones: List[dict], render_fn: Callable,
                      lang_setting: str = "auto") -> int:
    """อ่านภาพโซนด้วย Tesseract ล่วงหน้า ⇒ การ์ด defect เปิดเร็วขึ้น.

    ⚠️ แคชของชั้นกรอบแดงใช้ **hash ของพิกเซล** เป็นกุญแจ ⇒ ``render_fn``
       ต้องคืนภาพเดียวกับที่ ``/crop`` เรนเดอร์ทุกพิกเซล ไม่งั้นอุ่นเปล่า
    ⚠️ ภาษา "auto" เลือกตามสคริปต์ของ *คำที่ค้น* ซึ่งยังไม่รู้ตอนนี้ ⇒ อุ่น
       ด้วย ``eng`` (ฉลากส่วนใหญ่) · คำภาษาอื่นยังอ่านตอนเปิดการ์ดเหมือนเดิม
    แสดงผลล้วน · ไม่โยน exception · คืนจำนวนโซนที่อุ่นสำเร็จ
    """
    try:
        from . import highlight as hl
        if not hl._tesseract_available():
            return 0
        req = "eng" if (lang_setting or "auto").lower() == "auto" \
            else lang_setting
        lang = hl._resolve_langs(req)
    except Exception:                               # noqa: BLE001
        return 0
    n = 0
    for z in zones or []:
        try:
            crop = render_fn(z)
            if crop is None or crop.size == 0:
                continue
            for psm in hl._PSM_ORDER:
                hl._tess_words(crop, lang, psm)
            n += 1
        except Exception:                           # noqa: BLE001
            logger.debug("[artwork] prewarm skipped for %s", z.get("id"),
                         exc_info=True)
    return n
