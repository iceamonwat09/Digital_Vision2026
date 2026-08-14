"""
วินิจฉัย "ทำไมบางเคสไม่มีการยิง HTTP ไป N8N Artwork OCR" — สำหรับรันบนสถานี

โหมด Artwork มี **5 ทางที่ระบบจะ "ไม่ยิง" webhook เลย** (ไม่ใช่บั๊กทั้งหมด —
บางทางคือพฤติกรรมที่ถูกต้อง) สคริปต์นี้เดินเส้นทางเดียวกับ
``artwork_check.ocr.read_zone()`` แบบ **ไม่เรียก OCR จริง** แล้วบอกว่า
โซนไหนจะยิง / โซนไหนไม่ยิงเพราะอะไร:

  1. โซน type = "ignore"              → ``read_all_zones`` ข้ามตั้งแต่ต้น
  2. PDF มี text layer ≥ EMBEDDED_TEXT_MIN_CHARS ตัว → ใช้ engine "pdf-text"
     (**ทางที่พบบ่อยที่สุด และเป็นพฤติกรรมที่ถูกต้อง** — แม่นกว่า OCR และฟรี)
  3. backend ไม่เปิด (``OCR_BACKEND`` ชี้ไปที่อื่น / URL ว่าง) → engine "none"
  4. crop ว่าง (bbox ตัดออกนอกหน้า)   → engine "none"
  5. cache ``ocr_only.json`` ของแท็บ "ข้อความ + คำแปล" ยัง valid → ใช้ผลเดิม

    py -3.9 diagnose_n8n_ocr.py                  # ใช้การตรวจล่าสุด + ยิงทดสอบ
    py -3.9 diagnose_n8n_ocr.py <inspection_id>  # ระบุรายการเอง
    py -3.9 diagnose_n8n_ocr.py --no-ping        # ไม่ยิงภาพทดสอบไป N8N
    py -3.9 diagnose_n8n_ocr.py --ping-only      # เช็คแค่ว่า webhook ตอบไหม
    py -3.9 diagnose_n8n_ocr.py --scan --no-ping # กวาดทุกการตรวจ: ไฟล์ไหน
                                                 # มี text layer แต่ยังยิง OCR

ไม่แก้ไขข้อมูลใดๆ — อ่านอย่างเดียว (ไม่แตะ report.json / cache / verdict)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SEP = "=" * 72


def _host_of(url: str) -> str:
    try:
        from urllib.parse import urlparse
        u = urlparse(url)
        return f"{u.scheme}://{u.hostname}:{u.port or ''}"
    except Exception:
        return url


def _fmt(v, n: int) -> str:
    s = str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


# ── ① การตั้งค่า ────────────────────────────────────────────────────────
def show_config() -> dict:
    import config as appcfg
    from artwork_check import config as acfg
    from inspectors import ocr_n8n, vertex_client

    backend_raw = (appcfg.OCR_BACKEND or "").strip().lower()
    backend = vertex_client._resolve_backend()

    print(SEP)
    print("① การตั้งค่า OCR backend")
    print(SEP)
    print(f"  OCR_BACKEND (env, ว่าง = auto)      : {backend_raw!r}")
    print(f"  backend ที่ใช้จริง                  : {backend!r}")
    print(f"  N8N_OCR_WEBHOOK_URL                : {appcfg.N8N_OCR_WEBHOOK_URL!r}")
    print(f"  N8N_OCR_TIMEOUT_S                  : {appcfg.N8N_OCR_TIMEOUT_S}")
    print(f"  N8N_TRANSLATE_WEBHOOK_URL          : {acfg.N8N_TRANSLATE_WEBHOOK_URL!r}")
    print(f"  ocr.is_ocr_available()             : {vertex_client.is_enabled()}")
    print(f"  ARTWORK_EMBEDDED_MIN_CHARS         : {acfg.EMBEDDED_TEXT_MIN_CHARS}"
          "   ← เกินนี้ = ใช้ text layer ไม่ยิง OCR")
    print(f"  ARTWORK_OCR_DPI                    : {acfg.OCR_DPI}")
    print(f"  ARTWORK_OCR_CROP_MAX_SIDE          : {acfg.OCR_CROP_MAX_SIDE}")

    print()
    if backend != "n8n":
        print("  !! backend ไม่ใช่ 'n8n' → **ไม่มีทางยิง HTTP เลยทุกโซน**")
        if backend == "stub":
            print("     สาเหตุ: N8N_OCR_WEBHOOK_URL ว่าง (หรือถูก env ทับด้วยค่าว่าง)")
        else:
            print(f"     สาเหตุ: env OCR_BACKEND={backend_raw!r} บังคับไว้")
    elif not ocr_n8n.is_enabled():
        print("  !! URL ว่าง → ocr_image() คืน stub ทันทีโดยไม่ยิง HTTP")
    else:
        print("  ✓ backend = n8n และมี URL → โซนที่ไม่มี text layer จะยิง HTTP")

    h_ocr = _host_of(appcfg.N8N_OCR_WEBHOOK_URL)
    h_tr = _host_of(acfg.N8N_TRANSLATE_WEBHOOK_URL)
    if h_ocr != h_tr:
        print(f"  ⚠ host ของ OCR ({h_ocr}) กับ translate ({h_tr}) ไม่ตรงกัน — "
              "ตัวหนึ่งอาจยิงไปเครื่องเก่าแบบเงียบๆ (ดู CLAUDE.md หัวข้อ N8N)")
    if "localhost" in appcfg.N8N_OCR_WEBHOOK_URL:
        print("  ⚠ ใช้ 'localhost' — Windows resolve เป็น ::1 (IPv6) ก่อน ถ้า N8N "
              "ผูกเฉพาะ IPv4 จะต่อไม่ติด → ใช้ 127.0.0.1 แทน")
    if "/webhook-test/" in appcfg.N8N_OCR_WEBHOOK_URL:
        print("  ⚠ เป็น **Test URL** ของ n8n — ใช้ได้เฉพาะตอนกด 'Listen for test "
              "event' เท่านั้น นอกนั้นได้ 404 → ใช้ Production URL (/webhook/...)")
    return {"backend": backend, "url": appcfg.N8N_OCR_WEBHOOK_URL}


# ── ② ยิงภาพทดสอบผ่านเส้นทางจริง ────────────────────────────────────────
def ping(url: str) -> bool:
    """ยิงภาพ JPEG เล็กๆ ผ่าน ocr_n8n.ocr_image() = เส้นทางเดียวกับของจริง"""
    import cv2
    import numpy as np

    from inspectors import ocr_n8n

    print()
    print(SEP)
    print("② ยิงภาพทดสอบไป N8N (เส้นทางเดียวกับตอนตรวจจริง)")
    print(SEP)
    if not url.strip():
        print("  ข้าม — ไม่มี URL")
        return False

    img = np.full((120, 420, 3), 255, dtype=np.uint8)
    cv2.putText(img, "DIAGNOSE 12345", (14, 76),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)
    ok, buf = cv2.imencode(".jpg", img)
    if not ok:
        print("  !! encode JPEG ไม่สำเร็จ (ปัญหาที่ opencv ไม่ใช่ N8N)")
        return False

    res = ocr_n8n.ocr_image(buf.tobytes())
    err = res.get("error") or ""
    print(f"  engine : {res.get('engine')!r}")
    print(f"  stub   : {res.get('stub')}")
    print(f"  text   : {_fmt((res.get('text') or '').replace(chr(10), ' | '), 60)!r}")
    print(f"  blocks : {len(res.get('blocks') or [])}")
    if err:
        print(f"  error  : {err}")
        low = err.lower()
        print()
        if "refused" in low or "failed to establish" in low or "max retries" in low:
            print("  → ต่อไม่ติดเลย: N8N ไม่ได้รัน / คนละเครื่อง / port ผิด / firewall")
            print("    เช็ค: เปิด URL นี้ในเบราว์เซอร์บนสถานี ต้องไม่ขึ้น 'ต่อไม่ได้'")
        elif "404" in low:
            print("  → ต่อติดแต่ไม่มี webhook นี้: workflow ยัง **ไม่ Activate** "
                  "หรือ path ไม่ตรง (ต้องเป็น /webhook/artwork-ocr)")
        elif "timeout" in low or "timed out" in low:
            print("  → ยิงถึงแล้วแต่ N8N ตอบไม่ทัน: Gemini ช้า/โควตาเต็ม → "
                  "ดู execution log ฝั่ง n8n, เพิ่ม N8N_OCR_TIMEOUT_S")
        elif "413" in low:
            print("  → ภาพใหญ่เกิน limit ของ n8n → ตั้ง N8N_PAYLOAD_SIZE_MAX=32 "
                  "หรือลด ARTWORK_OCR_DPI")
        else:
            print("  → ดู execution log ฝั่ง n8n ประกอบ")
        return False

    print("\n  ✓ webhook ตอบกลับปกติ → ปัญหาไม่ได้อยู่ที่ 'ยิงแล้วพัง' "
          "แต่อยู่ที่ 'ไม่ได้ยิง' (ดูข้อ ③)")
    return True


# ── ③ ไล่ทีละโซนของการตรวจจริง (ไม่เรียก OCR) ─────────────────────────
def replay(rec_id: str) -> int:
    from artwork_check import config as acfg
    from artwork_check import pipeline, report
    from artwork_check.pdf_ingest import ArtworkDocument
    from inspectors import vertex_client

    d = report.inspection_dir(rec_id)
    if not os.path.isdir(d):
        print(f"\n!! ไม่พบโฟลเดอร์ของ {rec_id}")
        return 1

    rep = report.load_report(rec_id)
    zone_list = (rep or {}).get("zones") or []
    if not zone_list:
        print(f"\n!! {rec_id} ยังไม่มี report.json (ยังไม่ได้กด 'ส่งตรวจสอบ') "
              "— ไล่ทีละโซนไม่ได้ ใช้ผลข้อ ① ② ประกอบแทน")
        return 1

    print()
    print(SEP)
    print(f"③ ไล่ทีละโซน — {rec_id}  ({(rep or {}).get('filename','')})")
    print(SEP)

    enabled = vertex_client.is_enabled()
    recorded = {r["zone_id"]: r for r in (rep or {}).get("ocr", [])}
    docs = {}
    tally = {"post": 0, "pdf-text": 0, "ignore": 0, "off": 0, "empty": 0}

    for z in zone_list:
        zid, kind = z["id"], z.get("type", "")
        which = "b" if z.get("doc", "a") == "b" else "a"
        was = (recorded.get(zid) or {}).get("engine", "-")

        if kind == "ignore":
            tally["ignore"] += 1
            print(f"  {zid:6} doc={which}  ไม่ยิง — โซน type=ignore (ข้ามตั้งแต่ต้น)"
                  f"   [รายงานเดิม: {was}]")
            continue

        if which not in docs:
            try:
                base = "source" if which == "a" else "source_b"
                docs[which] = ArtworkDocument(pipeline._find_source(d, base))
            except Exception as e:
                docs[which] = e
        doc = docs[which]
        if isinstance(doc, Exception):
            print(f"  {zid:6} doc={which}  !! เปิดไฟล์ต้นฉบับไม่ได้: {doc}")
            continue

        try:
            emb = doc.embedded_text(z["bbox"])
        except Exception as e:
            emb = ""
            print(f"  {zid:6} doc={which}  (อ่าน text layer ไม่ได้: {e})")

        if len(emb) >= acfg.EMBEDDED_TEXT_MIN_CHARS:
            tally["pdf-text"] += 1
            print(f"  {zid:6} doc={which}  ไม่ยิง — มี text layer {len(emb)} ตัวอักษร "
                  f"(≥ {acfg.EMBEDDED_TEXT_MIN_CHARS}) → engine=pdf-text ✓ ปกติ"
                  f"   [รายงานเดิม: {was}]")
            print(f"         ตัวอย่าง: {_fmt(emb.replace(chr(10), ' | '), 58)!r}")
            continue

        if not enabled:
            tally["off"] += 1
            print(f"  {zid:6} doc={which}  ไม่ยิง — backend ปิดอยู่ (ดูข้อ ①) "
                  f"→ engine=none   [รายงานเดิม: {was}]")
            continue

        try:
            crop = doc.render_zone(z["bbox"], dpi=acfg.OCR_DPI,
                                   max_side=acfg.OCR_CROP_MAX_SIDE)
        except Exception as e:
            print(f"  {zid:6} doc={which}  ไม่ยิง — render โซนไม่ได้ (bbox ผิด/"
                  f"หลุดหน้า): {e}")
            print("         → ในการตรวจจริงโซนแบบนี้จะโยน error ไม่ใช่แค่ไม่ยิง")
            continue

        if crop.size == 0:
            tally["empty"] += 1
            print(f"  {zid:6} doc={which}  ไม่ยิง — crop ว่าง (bbox ตัดออกนอกหน้า) "
                  f"→ engine=none   [รายงานเดิม: {was}]")
            continue

        tally["post"] += 1
        h, w = crop.shape[:2]
        print(f"  {zid:6} doc={which}  **จะยิง HTTP** — text layer {len(emb)} ตัว "
              f"(< {acfg.EMBEDDED_TEXT_MIN_CHARS}), crop {w}x{h}px"
              f"   [รายงานเดิม: {was}]")

    print()
    print(f"  สรุป: จะยิง {tally['post']} โซน · ไม่ยิงเพราะ text layer "
          f"{tally['pdf-text']} · ignore {tally['ignore']} · backend ปิด "
          f"{tally['off']} · crop ว่าง {tally['empty']}")

    # cache ของแท็บแปล — ทางที่ 5
    cache_p = os.path.join(d, pipeline._OCR_ONLY_CACHE)
    print()
    print("  cache ของแท็บ 'ข้อความ + คำแปล' (ocr_only.json):")
    if not os.path.exists(cache_p):
        print("    ไม่มีไฟล์ → กดแท็บแปลจะ OCR ใหม่ (ยิง HTTP)")
    else:
        try:
            # ลองทั้งสองค่าของ auto-rotate เพราะ flag เป็นส่วนหนึ่งของ key
            # (ไม่ได้ถูกบันทึกใน report.json) — valid ค่าใดค่าหนึ่ง = cache hit
            hit = any(pipeline._load_ocr_cache(d, zone_list, ar) is not None
                      for ar in (False, True))
        except Exception as e:
            hit = False
            print(f"    (เทียบ signature ไม่ได้: {e})")
        if hit:
            print("    **valid → กดแท็บแปลซ้ำจะใช้ผลเดิม ไม่ยิง HTTP** (ตั้งใจ) "
                  "— ลบไฟล์นี้ถ้าต้องการบังคับ OCR ใหม่")
        else:
            print("    stale (โซน/auto-rotate เปลี่ยน) → จะ OCR ใหม่")

    if tally["post"] == 0:
        print()
        print("  ⇒ การตรวจนี้ **ไม่ควรมี HTTP ไป N8N เลย** ตามการตั้งค่าปัจจุบัน "
              "— ไม่ใช่บั๊กที่ตัวเชื่อมต่อ")
    return 0


# ── ④ กวาดทุกการตรวจ: ไฟล์ไหน "น่าจะเป็น text แต่ยังส่ง OCR" ──────────
def scan(limit: int = 200) -> int:
    """ตอบคำถาม 'มีไฟล์อื่นอีกไหมที่มี text layer แต่เรายังยิง OCR'.

    เคสที่ต้องจับคือ **โซนที่มีข้อความใน text layer แต่ไม่ถึงเกณฑ์**
    (1..EMBEDDED_TEXT_MIN_CHARS-1 ตัว) → ระบบทิ้งข้อความจริงแล้วไปเดาด้วย OCR.
    ส่วนโซนที่ได้ 0 ตัวคือ outline/กราฟิกจริง — ยิง OCR ถูกต้องแล้ว.
    """
    from artwork_check import config as acfg
    from artwork_check import pipeline, report
    from artwork_check.pdf_ingest import ArtworkDocument

    print()
    print(SEP)
    print("④ กวาดทุกการตรวจ — ไฟล์ไหนมี text layer แต่ยังยิง OCR")
    print(SEP)

    recs = report.list_inspections(limit=limit)
    if not recs:
        print("  ไม่พบรายการตรวจ")
        return 0

    suspects = []
    for rec in recs:
        rid = rec["id"]
        rep = report.load_report(rid)
        if not rep or not rep.get("zones"):
            continue
        d = report.inspection_dir(rid)
        docs, page_chars = {}, {}
        n_post = n_pdf = n_partial = 0
        worst = 0
        for z in rep["zones"]:
            if z.get("type") == "ignore":
                continue
            which = "b" if z.get("doc", "a") == "b" else "a"
            if which not in docs:
                try:
                    base = "source" if which == "a" else "source_b"
                    doc = ArtworkDocument(pipeline._find_source(d, base))
                    docs[which] = doc
                    page_chars[which] = len(doc.embedded_text())
                except Exception:
                    docs[which] = None
                    page_chars[which] = 0
            doc = docs[which]
            if doc is None:
                continue
            try:
                n = len(doc.embedded_text(z["bbox"]))
            except Exception:
                continue
            if n >= acfg.EMBEDDED_TEXT_MIN_CHARS:
                n_pdf += 1
            else:
                n_post += 1
                if n > 0:
                    n_partial += 1
                    worst = max(worst, n)

        if not (n_pdf or n_post):
            continue
        pc = max(page_chars.values()) if page_chars else 0
        flag = ""
        if n_partial:
            flag = f"  ⚠ {n_partial} โซนมีข้อความ 1-{worst} ตัว (ต่ำกว่าเกณฑ์)"
            suspects.append(rid)
        elif pc >= acfg.EMBEDDED_TEXT_MIN_CHARS and n_post:
            flag = "  ⚠ หน้ามี text layer แต่บางโซนไม่มีเลย (โซนอยู่บนกราฟิก?)"
            suspects.append(rid)
        print(f"  {rid}  {_fmt(rep.get('filename',''), 34):34} "
              f"หน้า={pc:6} ตัว · pdf-text={n_pdf:3} · ยิง OCR={n_post:3}{flag}")

    print()
    if suspects:
        print(f"  รายการที่ควรดูต่อ ({len(suspects)}): "
              + ", ".join(suspects[:10]))
        print("  → รัน `py -3.9 diagnose_n8n_ocr.py <id> --no-ping` ดูรายโซน")
        print("  → ถ้าเป็นเคส 'มีข้อความแต่ไม่ถึงเกณฑ์' แปลว่าโซนคาบเกี่ยว "
              "ข้อความจริงนิดเดียว: ลากโซนให้ครอบข้อความให้ครบ จะได้ text layer "
              "ที่แม่นแทนการเดาด้วย OCR")
    else:
        print("  ✓ ไม่พบรายการที่ 'มี text layer แต่ถูกส่งไป OCR' — "
              "ไฟล์ที่ยิง OCR คือไฟล์ outline/ภาพถ่ายจริง")
    return 0


def main() -> int:
    args = sys.argv[1:]
    no_ping = "--no-ping" in args
    ping_only = "--ping-only" in args
    do_scan = "--scan" in args
    args = [a for a in args if not a.startswith("--")]

    try:
        from artwork_check import report
    except Exception as e:
        print("!! import โหมด artwork ไม่ได้:", e)
        return 2

    cfg = show_config()
    if not no_ping:
        ping(cfg["url"])
    if ping_only:
        return 0
    if do_scan:
        return scan()

    if args:
        rec_id = args[0]
    else:
        recs = report.list_inspections(limit=1)
        if not recs:
            print("\n!! ไม่พบรายการตรวจใดๆ — อัปโหลดและกดส่งตรวจก่อน "
                  "(หรือรัน --ping-only)")
            return 1
        rec_id = recs[0]["id"]
    return replay(rec_id)


if __name__ == "__main__":
    raise SystemExit(main())
