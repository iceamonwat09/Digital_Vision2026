"""เรียก Google Cloud Vision ``images:annotate`` ตรงด้วย REST (ไม่ใช้ SDK)

* ใช้ ``requests`` ที่มีอยู่แล้ว ⇒ ไม่ติดเรื่อง Python 3.9
* กุญแจส่งใน header ``X-Goog-Api-Key`` (ไม่ต่อท้าย URL) — ลดโอกาสหลุดลง Log
* รวมภาพหลายภาพในคำขอเดียว (≤ 16 ภาพ · ≤ ``MAX_REQUEST_BYTES``) โดยพยายาม
  ให้ภาพคู่ A/B อยู่คำขอเดียวกัน ⇒ ถูกอ่านด้วยโมเดลรุ่นเดียวกัน
* **ไม่โยน exception** — ทุกความล้มเหลวคืนเป็น ``error`` ต่อภาพ
* ข้อความ error ทุกข้อความผ่าน ``keystore.redact`` ก่อนออกจากโมดูลนี้
"""

from __future__ import annotations

import base64
import json
import time
from typing import Callable, Dict, List, Optional

from . import config, keystore

RETRY_HTTP = {429, 500, 502, 503, 504}
# google.rpc.Code: 4 DEADLINE_EXCEEDED · 8 RESOURCE_EXHAUSTED · 13 INTERNAL · 14 UNAVAILABLE
RETRY_RPC = {4, 8, 13, 14}

_HTTP_HINT = {
    400: "คำขอผิดรูปแบบ (ภาพเสีย/ใหญ่เกิน/ค่าพารามิเตอร์ผิด)",
    401: "API key ไม่ถูกต้อง",
    403: "ไม่มีสิทธิ์ — กุญแจผิด / ยังไม่เปิด Cloud Vision API / IP ไม่อยู่ในรายการที่อนุญาต / ยังไม่ผูก Billing",
    404: "ไม่พบ endpoint — ตรวจ ARTWORK_V2_VISION_ENDPOINT",
    413: "คำขอใหญ่เกินที่ Google รับ",
    429: "ใช้เกินโควตา/ยิงถี่เกิน",
}


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _item_cost(jpeg: bytes) -> int:
    # base64 = 4/3 เท่า + หัว JSON ของแต่ละภาพ
    return (len(jpeg) + 2) // 3 * 4 + 400


def pack(groups: List[List[dict]]) -> List[List[dict]]:
    """จัดภาพลงคำขอ · ``groups`` = ภาพที่อยากให้อยู่คำขอเดียวกัน (เช่นคู่ A/B)

    กลุ่มที่ใส่คำขอเดียวไม่ได้ (ใหญ่เกิน) จะถูกแยกเป็นรายภาพ — ผู้เรียกดูได้จาก
    ``request_index`` ของแต่ละภาพว่าคู่ไหนถูกแยก
    """
    limit = config.MAX_REQUEST_BYTES - 200
    reqs: List[List[dict]] = []
    cur: List[dict] = []
    cur_bytes = 0

    def flush():
        nonlocal cur, cur_bytes
        if cur:
            reqs.append(cur)
        cur, cur_bytes = [], 0

    for g in groups:
        g_bytes = sum(_item_cost(it["jpeg"]) for it in g)
        if g_bytes <= limit and len(g) <= config.MAX_IMAGES_PER_REQUEST:
            if (cur_bytes + g_bytes > limit
                    or len(cur) + len(g) > config.MAX_IMAGES_PER_REQUEST):
                flush()
            cur.extend(g)
            cur_bytes += g_bytes
            continue
        for it in g:                                    # กลุ่มใหญ่เกิน → แยกรายภาพ
            c = _item_cost(it["jpeg"])
            if cur and (cur_bytes + c > limit
                        or len(cur) + 1 > config.MAX_IMAGES_PER_REQUEST):
                flush()
            cur.append(it)
            cur_bytes += c
    flush()
    return reqs


def _body(items: List[dict]) -> bytes:
    reqs = []
    for it in items:
        r = {"image": {"content": _b64(it["jpeg"])},
             "features": [{"type": "DOCUMENT_TEXT_DETECTION"}]}
        if config.MODEL:
            r["features"][0]["model"] = config.MODEL
        if config.LANGUAGE_HINTS:
            r["imageContext"] = {"languageHints": list(config.LANGUAGE_HINTS)}
        reqs.append(r)
    return json.dumps({"requests": reqs}).encode("ascii")


def _err_text(resp) -> str:
    try:
        e = (resp.json() or {}).get("error") or {}
        msg = e.get("message") or ""
        st = e.get("status") or ""
        return ("%s %s" % (st, msg)).strip()
    except ValueError:
        return " ".join((resp.text or "").split())[:200]


def _post(body: bytes, key: str, poster: Callable) -> dict:
    """ยิงหนึ่งคำขอพร้อมลองซ้ำ — คืน ``{ok, status, attempts, ms, json|error}``"""
    url = config.ENDPOINT + "/v1/images:annotate"
    headers = {"Content-Type": "application/json; charset=utf-8",
               "X-Goog-Api-Key": key}
    tries = max(1, int(config.RETRIES) + 1)
    out = {"ok": False, "status": None, "attempts": 0, "ms": 0, "error": "ยังไม่ได้ยิง"}
    t0 = time.time()
    for attempt in range(tries):
        out["attempts"] = attempt + 1
        retry = False
        try:
            resp = poster(url, data=body, headers=headers, timeout=config.TIMEOUT_S)
            out["status"] = resp.status_code
            if resp.status_code == 200:
                try:
                    out.update(ok=True, json=resp.json(), error="")
                except ValueError:
                    out["error"] = "Vision ตอบ 200 แต่ไม่ใช่ JSON"
                    retry = True
            else:
                hint = _HTTP_HINT.get(resp.status_code, "")
                out["error"] = "HTTP %d %s %s" % (resp.status_code, hint, _err_text(resp))
                retry = resp.status_code in RETRY_HTTP
        except Exception as e:                          # noqa: BLE001
            out["status"] = None
            out["error"] = "ต่อ Vision ไม่สำเร็จ: %s: %s" % (type(e).__name__, e)
            retry = True
        if out["ok"] or not retry or attempt >= tries - 1:
            break
        time.sleep(config.RETRY_WAIT_S * (2 ** attempt))
    out["ms"] = int((time.time() - t0) * 1000)
    out["error"] = keystore.redact(out.get("error", ""), key)
    return out


def annotate(groups: List[List[dict]], poster: Optional[Callable] = None,
             key: Optional[str] = None) -> dict:
    """อ่านทุกภาพ · ``groups`` = ``[[{"id","jpeg"}, ...], ...]``

    คืน ``{"results": {id: {...}}, "calls": [...]}`` — ผลต่อภาพมีคีย์:
    ``ok`` · ``error`` · ``fta`` (fullTextAnnotation) · ``request_index``
    """
    if poster is None:
        import requests
        poster = requests.post
    if key is None:
        key = keystore.get_key()[0]
    results: Dict[str, dict] = {}
    calls: List[dict] = []
    all_items = [it for g in groups for it in g]
    if not key:
        for it in all_items:
            results[it["id"]] = {"ok": False, "error": "ยังไม่ได้ตั้งค่า API key",
                                 "request_index": None}
        return {"results": results, "calls": calls}

    def run(batches: List[List[dict]], phase: str):
        for items in batches:
            body = _body(items)
            r = _post(body, key, poster)
            idx = len(calls)
            calls.append({"index": idx, "phase": phase,
                          "images": [it["id"] for it in items],
                          "json_bytes": len(body), "status": r["status"],
                          "attempts": r["attempts"], "ms": r["ms"],
                          "error": r.get("error", ""),
                          "model_requested": config.MODEL,
                          "endpoint": config.ENDPOINT,
                          "at": time.strftime("%Y-%m-%d %H:%M:%S")})
            resps = ((r.get("json") or {}).get("responses") or []) if r["ok"] else []
            if r["ok"] and len(resps) != len(items):
                calls[-1]["error"] = ("จำนวนผลที่ตอบกลับ (%d) ไม่เท่าจำนวนภาพ (%d)"
                                      % (len(resps), len(items)))
            for i, it in enumerate(items):
                res = {"request_index": idx}
                if not r["ok"]:
                    res.update(ok=False, error=r["error"], retriable=True)
                elif i >= len(resps):
                    res.update(ok=False, error="ไม่มีผลของภาพนี้ในคำตอบ", retriable=True)
                else:
                    one = resps[i] or {}
                    err = one.get("error")
                    if err:
                        code = err.get("code")
                        res.update(ok=False, retriable=code in RETRY_RPC,
                                   error=keystore.redact("Vision error %s: %s"
                                                         % (code, err.get("message", "")), key))
                    else:
                        res.update(ok=True, error="",
                                   fta=one.get("fullTextAnnotation") or {})
                results[it["id"]] = res

    run(pack(groups), "main")
    # รอบเก็บตก: ภาพที่ล้มเหลวแบบลองซ้ำได้ ยิงใหม่อีกครั้ง (ทีละกลุ่มเดิม)
    redo = [[it for it in g if not results[it["id"]].get("ok")
             and results[it["id"]].get("retriable")] for g in groups]
    redo = [g for g in redo if g]
    if redo and config.RETRIES > 0 and any(c["status"] == 200 for c in calls):
        run(pack(redo), "retry-image")
    for res in results.values():
        res.pop("retriable", None)
    return {"results": results, "calls": calls}
