"""
วินิจฉัยกรอบแดงชี้คำผิดของโหมด Artwork (display-only) — สำหรับรันบนสถานี

ใช้เมื่อกรอบแดง "ไม่ขึ้น" หรือ "ขึ้นผิดตำแหน่ง" เพื่อดูว่าเกิดอะไรขึ้นจริง
โดยไม่ต้องเดา: สคริปต์เดินตามเส้นทางเดียวกับที่ระบบใช้ตอนแสดงผลการ์ด defect
แล้วรายงานว่าชั้นไหนทำงาน ได้กี่กรอบ ตำแหน่งไหน และในกรอบนั้นอ่านได้ว่าอะไร

    py -3.9 diagnose_highlight.py                 # ใช้การตรวจล่าสุด
    py -3.9 diagnose_highlight.py <inspection_id> # ระบุรายการเอง
    py -3.9 diagnose_highlight.py --save          # เซฟรูปที่วาดกรอบไว้ดูด้วย

ไม่แก้ไขข้อมูลใดๆ — อ่านอย่างเดียว (ปลอดภัยกับรายงานที่มีอยู่)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _fmt(v, n):
    s = str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def main() -> int:
    args = [a for a in sys.argv[1:]]
    save = "--save" in args
    args = [a for a in args if not a.startswith("--")]

    try:
        from artwork_check import config as acfg
        from artwork_check import highlight as hl
        from artwork_check import pipeline, report
        from artwork_check.pdf_ingest import ArtworkDocument
    except Exception as e:
        print("!! import โหมด artwork ไม่ได้:", e)
        return 2

    print("=" * 72)
    print("การตั้งค่าปัจจุบัน")
    print("=" * 72)
    print(f"  เปิดใช้ฟีเจอร์ (HIGHLIGHT_DEFECT_WORD) : {acfg.HIGHLIGHT_DEFECT_WORD}")
    print(f"  ชั้น ② PDF text layer                 : {acfg.HIGHLIGHT_USE_PDF_TEXT}")
    print(f"  ชั้น ③ Tesseract                      : {acfg.HIGHLIGHT_USE_TESSERACT}")
    print(f"  ชั้นสำรอง profile (ควรเป็น False)      : {acfg.HIGHLIGHT_USE_PROFILE}")
    print(f"  ภาษา Tesseract (env TESS_LANG)        : {acfg.HIGHLIGHT_TESSERACT_LANG!r}")
    print(f"  กรอบสูงสุดต่อ defect                   : {acfg.HIGHLIGHT_MAX_BOXES}")

    cmd = hl._find_tesseract_cmd()
    print(f"  พบ tesseract.exe ที่                   : {cmd}")
    print(f"  Tesseract ใช้งานได้                    : {hl._tesseract_available()}")
    try:
        import pytesseract
        installed = pytesseract.get_languages(config="")
    except Exception:
        installed = []
    print(f"  ภาษาที่ติดตั้งจริง                      : {installed}")
    used = hl._resolve_langs(acfg.HIGHLIGHT_TESSERACT_LANG)
    print(f"  ภาษาที่ใช้จริงหลังกรอง                  : {used!r}")
    if used != (acfg.HIGHLIGHT_TESSERACT_LANG or "eng"):
        print("   ^^ ต่างจากที่ตั้งไว้ = บางภาษายังไม่ได้ติดตั้ง (ระบบตัดออกให้กันพัง)")

    # ── เลือกการตรวจ ────────────────────────────────────────────────
    if args:
        rec_id = args[0]
    else:
        recs = report.list_inspections(limit=1)
        if not recs:
            print("\n!! ไม่พบรายการตรวจใดๆ — กรุณาอัปโหลดและกดส่งตรวจก่อน")
            return 1
        rec_id = recs[0]["id"]

    rep = report.load_report(rec_id)
    if rep is None:
        print(f"\n!! ไม่พบรายงานของ {rec_id}")
        return 1

    print()
    print("=" * 72)
    print(f"รายการตรวจ {rec_id}   ({rep.get('filename', '')})")
    print("=" * 72)

    zones = {z["id"]: z for z in rep.get("zones", [])}
    ocr_by_zone = {r["zone_id"]: r for r in rep.get("ocr", [])}

    print("\nโซนและแหล่งข้อความ:")
    for zid, z in zones.items():
        e = ocr_by_zone.get(zid, {})
        nblocks = len(e.get("blocks") or [])
        print(f"  {zid:6} doc={z.get('doc','a')} rotate={z.get('rotate',0)!s:7}"
              f" engine={_fmt(e.get('engine','-'),18):18}"
              f" blocks_bbox={nblocks:3} ocr_wh={e.get('ocr_wh')}")
        if e.get("engine") == "pdf-text":
            print("         → ใช้ชั้น ② (พิกัดคำจาก PDF โดยตรง)")
        elif nblocks:
            print("         → ใช้ชั้น ① (bbox จาก OCR backend)")
        else:
            print("         → ใช้ชั้น ③ (Tesseract)")

    defects = rep.get("defects", [])
    print(f"\ndefect ทั้งหมด {len(defects)} รายการ "
          f"(แสดงเฉพาะที่มีคำให้ชี้)")

    import cv2  # noqa: E402
    import numpy as np  # noqa: E402

    n_none = n_ok = 0
    for i, d in enumerate(defects, 1):
        found = d.get("found") or ""
        zid = d.get("zone_id")
        if not found or zid not in zones:
            continue
        z = zones[zid]
        bbox = z["bbox"]
        rot = z.get("rotate", 0)
        rot = rot if rot in (0, 90, 180, 270) else 0

        try:
            plain = pipeline.zone_crop_jpg(rec_id, bbox, doc=z.get("doc", "a"),
                                           rotate=str(rot))
            hi = pipeline.zone_crop_jpg(rec_id, bbox, doc=z.get("doc", "a"),
                                        rotate=str(rot), highlight=found,
                                        zone_id=zid)
        except Exception as e:
            print(f"\n  [{i}] {d.get('class')} zone={zid}: crop ล้มเหลว: {e}")
            continue

        a = cv2.imdecode(np.frombuffer(plain, np.uint8), cv2.IMREAD_COLOR)
        b = cv2.imdecode(np.frombuffer(hi, np.uint8), cv2.IMREAD_COLOR)
        boxes = 0
        if a is not None and b is not None and a.shape == b.shape:
            diff = cv2.absdiff(a, b).max(axis=2)
            mask = (diff > 25).astype(np.uint8) * 255
            if mask.sum():
                k = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
                mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
                nlab, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
                boxes = sum(1 for j in range(1, nlab)
                            if stats[j, cv2.CC_STAT_AREA] > 60)

        print(f"\n  [{i}] {d.get('class')}  zone={zid}  พบ={_fmt(found, 40)!r}")
        print(f"       crop {a.shape[1]}x{a.shape[0]}  →  วาดกรอบ {boxes} อัน")
        if boxes == 0:
            n_none += 1
            print("       (ไม่วาด = หาตำแหน่งคำนี้ไม่เจอ/ไม่มั่นใจ — ปลอดภัยกว่าวาดผิด)")
        else:
            n_ok += 1
            # อ่านซ้ำเฉพาะในกรอบ เพื่อดูว่าตรงคำจริงไหม
            if hl._tesseract_available():
                try:
                    import pytesseract
                    ys, xs = np.where(mask > 0)
                    x0, x1 = int(xs.min()), int(xs.max())
                    y0, y1 = int(ys.min()), int(ys.max())
                    sub = a[max(0, y0 - 3):y1 + 3, max(0, x0 - 3):x1 + 3]
                    txt = pytesseract.image_to_string(
                        cv2.cvtColor(sub, cv2.COLOR_BGR2RGB),
                        lang=used).strip().replace("\n", " ")
                    print(f"       ในบริเวณที่วาดอ่านได้ = {_fmt(txt, 60)!r}")
                except Exception:
                    pass
        if save:
            out = f"diag_{rec_id}_{i}_{zid}.jpg"
            with open(out, "wb") as f:
                f.write(hi)
            print(f"       เซฟรูป → {out}")

    print()
    print("=" * 72)
    print(f"สรุป: วาดกรอบได้ {n_ok} รายการ · ไม่วาด {n_none} รายการ")
    if n_ok == 0 and n_none:
        print("ทุกรายการไม่วาดเลย — ตรวจตามลำดับ:")
        print("  1) 'Tesseract ใช้งานได้' ด้านบนเป็น True ไหม")
        print("  2) ภาษาที่ใช้จริงครอบคลุมภาษาของคำที่ฟ้องไหม")
        print("  3) รายงานนี้ตรวจก่อนอัปเดตโค้ดหรือเปล่า (ต้องส่งตรวจใหม่)")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
