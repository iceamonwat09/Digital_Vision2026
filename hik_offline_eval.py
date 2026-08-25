"""
hik_offline_eval.py — เอาภาพที่เก็บจากกล้องอุตสาหกรรม (โหมด "เก็บภาพชุดข้อมูล")
มาวัดว่า **โมเดลตรวจได้จริงแค่ไหน และไลน์วิ่งเร็วแค่ไหน** โดยไม่ต้องเชื่อตัวนับตอนรันสด

ทำไมต้องวัดแบบออฟไลน์ (เหตุผลเชิงฟิสิกส์ ไม่ใช่ความขี้เกียจ)
--------------------------------------------------------------
ตอนรันสด การนับ "1 กระป๋อง = 1 การตรวจ" ใช้ **ช่องว่างระหว่างใบ** เป็นตัวแบ่ง
(`DEFECT_RESET_FRAMES` เฟรมว่างติดกัน) และการตรวจถูกจำกัดด้วยความเร็วโมเดล
(bestX ≈ 45-50 ms/เฟรม ⇒ ~20 ครั้ง/วินาที). ที่ 7 ใบ/วินาที (คาบ 143 ms/ใบ)
**ตัวเลขไม่พอโดยโครงสร้าง** — ดู §4 ของ docs/PLAN_LINE_DENT_INSPECTION.md

แต่การ **เก็บภาพ** ไม่ติดข้อจำกัดนั้น: มันวิ่งที่อัตรากล้อง (ROI ครึ่ง = 69 fps)
⇒ ที่ 7 ใบ/วิ ได้ ~10 เฟรม/ใบ. สคริปต์นี้จึงเอาภาพชุดนั้นมา:
  ① รันโมเดลตัวจริง (เส้นทางเดียวกับ production ทั้ง backend/fallback)
  ② **จัดกลุ่มเฟรมที่ติดกันเป็น "ใบ"** แล้วรายงานว่าได้กี่เฟรมต่อใบ / กี่ใบมีรอยบุบ
  ③ เทียบเวลาที่โมเดลใช้จริงกับงบเวลาต่อใบ → บอกตรง ๆ ว่า "รันสดตามทันไหม"

วิธีใช้ (บนสถานี)
    py -3.9 hik_offline_eval.py data\\hik_dataset\\20260819_143012
    py -3.9 hik_offline_eval.py <โฟลเดอร์> --fps 69 --imgsz 480 --save-dir out_ng
    py -3.9 hik_offline_eval.py <โฟลเดอร์> --model bestX.pt --imgsz 1280 --limit 200

exit code: 0 = ผ่าน · 1 = มีข้อสังเกตที่ต้องอ่าน · 2 = รันไม่ได้
"""

import argparse
import glob
import json
import os
import statistics
import sys
import time

try:
    import cv2
except Exception as e:                                # pragma: no cover
    print("ต้องมี OpenCV: %s" % e)
    sys.exit(2)

_LINE = "─" * 74
IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp")


def head(title):
    print("\n" + _LINE)
    print(title)
    print(_LINE)


def load_detector(mode, model_file):
    """โหลดโมเดลด้วย **เส้นทางเดียวกับ app.py** — ไม่งั้นวัดคนละอย่างกับที่ใช้งานจริง."""
    from yolo_detector import YOLODetector
    from modes import registry as mode_registry
    import config

    cfg = mode_registry.get_mode_config(mode)
    path = mode_registry.resolve_model_path(mode, model_file)
    det = YOLODetector(model_path=path or config.MODEL_PATH, mode_config=cfg)
    det.load_model()
    return det, (path or config.MODEL_PATH)


def main():
    ap = argparse.ArgumentParser(
        description="วัดผลโมเดลบนภาพที่เก็บจากกล้องอุตสาหกรรม (ออฟไลน์)")
    ap.add_argument("folder", help="โฟลเดอร์ภาพจากโหมดเก็บชุดข้อมูล")
    ap.add_argument("--mode", default="can_dent")
    ap.add_argument("--model", default=None, help="ชื่อไฟล์ .pt (ค่าเริ่มต้น = ที่ระบบเลือกเอง)")
    ap.add_argument("--imgsz", type=int, default=None, help="ค่าเริ่มต้น = YOLO_IMGSZ (480)")
    ap.add_argument("--limit", type=int, default=0, help="ตรวจแค่ N ภาพแรก (0 = ทั้งหมด)")
    ap.add_argument("--fps", type=float, default=None,
                    help="อัตราเฟรมตอนเก็บภาพ (เอาไปคำนวณอัตราใบ/วินาที)")
    ap.add_argument("--gap", type=int, default=2,
                    help="เฟรมว่างติดกันกี่เฟรมจึงถือว่าเป็นใบใหม่ (ค่าเริ่มต้น 2)")
    ap.add_argument("--save-dir", help="เซฟภาพที่พบรอยบุบพร้อมกรอบไว้ดูด้วยตา")
    ap.add_argument("--pitch-mm", type=float, default=None,
                    help="ระยะห่างระหว่างกระป๋องบนสายพาน (mm) — ใช้คำนวณความเร็วสายพาน")
    ap.add_argument("--exposure-us", type=float, default=None,
                    help="exposure ที่ใช้ตอนถ่าย (µs) — ใช้คำนวณระยะเบลอจากการเคลื่อนที่")
    ap.add_argument("--blur-limit-mm", type=float, default=0.2,
                    help="ระยะเบลอสูงสุดที่ยอมรับได้ (mm) — ค่าเริ่มต้น 0.2 ตามแผน §3")
    ap.add_argument("--live-ms", type=float, default=None,
                    help="เวลา inference ต่อเฟรมตอนรันสดจริง (ms) — ค่าเริ่มต้นใช้ค่าที่วัดได้"
                         "จากการรันนี้ ซึ่งจะตรงก็ต่อเมื่อรันบนเครื่องเดียวกับที่ใช้งาน")
    ap.add_argument("--json", help="เขียนผลเป็นไฟล์ JSON")
    args = ap.parse_args()

    print(_LINE)
    print("hik_offline_eval.py — วัดผลโมเดลบนภาพจากกล้องอุตสาหกรรม")
    print(_LINE)

    if not os.path.isdir(args.folder):
        print("❌ ไม่พบโฟลเดอร์: %s" % args.folder)
        return 2
    files = sorted(f for f in glob.glob(os.path.join(args.folder, "*"))
                   if f.lower().endswith(IMG_EXT))
    if args.limit:
        files = files[:args.limit]
    if not files:
        print("❌ ไม่มีไฟล์ภาพในโฟลเดอร์นี้")
        return 2

    head("① โมเดล")
    try:
        det, model_path = load_detector(args.mode, args.model)
    except Exception as e:
        print("❌ โหลดโมเดลไม่สำเร็จ: %s" % e)
        return 2
    if det.model is None:
        print("❌ โหลดโมเดลไม่สำเร็จ (det.model is None) — ตรวจไฟล์ .pt และแพ็กเกจ")
        return 2
    import config
    imgsz = args.imgsz or getattr(config, "YOLO_IMGSZ", 480)
    # ⚠️ ต้องเป็นชุดเดียวกับที่ระบบใช้จริง — ถามจาก detector ตัวเดียวกับที่
    # โหลดมา (เดิม hard-code ไว้ ⇒ โมเดลที่ตั้งชื่อคลาส "ทั้งใบ" ต่างออกไปจะถูก
    # นับเป็นตำหนิ = รายงานคนละเรื่องกับหน้าจอของสถานี)
    # ตัว detector ปลอมในเทสต์ไม่มีเมธอดนี้ ⇒ ต้องถอยได้โดยไม่พัง
    getter = getattr(det, "non_defect_classes", None)
    non_defect = set(getter()) if callable(getter) else {"good", "can"}
    roles = getattr(det, "class_roles", None) or {}
    print("   ไฟล์โมเดล : %s" % model_path)
    if roles:
        print("   คลาส      : %s" % " · ".join(
            "%s=%s" % (c, "ตำหนิ" if r == "defect" else "ทั้งใบ")
            for c, r in roles.items()))
    print("   imgsz     : %d" % imgsz)
    print("   ภาพที่ตรวจ : %d ไฟล์" % len(files))

    head("② ตรวจทีละเฟรม")
    rows, times = [], []
    t_all = time.time()
    for i, path in enumerate(files, 1):
        img = cv2.imread(path)
        if img is None:
            rows.append({"file": os.path.basename(path), "error": "อ่านภาพไม่ได้"})
            continue
        t0 = time.perf_counter()
        dets = det.detect(img, imgsz=imgsz)
        ms = (time.perf_counter() - t0) * 1000.0
        times.append(ms)
        defects = [d for d in dets if d["class_name"] not in non_defect]
        cans = [d for d in dets if d["class_name"] in non_defect]
        rows.append({
            "file": os.path.basename(path), "ms": round(ms, 1),
            "defects": len(defects), "cans": len(cans),
            "max_conf": round(max((d["confidence"] for d in defects), default=0.0), 3),
            "size": "%dx%d" % (img.shape[1], img.shape[0]),
        })
        if args.save_dir and defects:
            try:
                os.makedirs(args.save_dir, exist_ok=True)
                cv2.imwrite(os.path.join(args.save_dir, os.path.basename(path)),
                            det.draw_detections(img, dets),
                            [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            except Exception:
                pass
        if i % 50 == 0:
            print("   ...ตรวจแล้ว %d/%d" % (i, len(files)))

    ok_rows = [r for r in rows if "error" not in r]
    if not ok_rows:
        print("❌ อ่านภาพไม่ได้เลย")
        return 2
    with_can = [r for r in ok_rows if (r["cans"] or r["defects"])]
    with_defect = [r for r in ok_rows if r["defects"]]
    print("   เฟรมที่เห็นกระป๋อง : %d/%d (%.1f%%)"
          % (len(with_can), len(ok_rows), 100.0 * len(with_can) / len(ok_rows)))
    print("   เฟรมที่พบรอยบุบ   : %d (%.1f%%)"
          % (len(with_defect), 100.0 * len(with_defect) / len(ok_rows)))
    times.sort()
    p50 = statistics.median(times) if times else 0
    p95 = times[int(len(times) * 0.95) - 1] if len(times) >= 20 else (max(times) if times else 0)
    print("   เวลา/เฟรม        : p50 %.0f ms · p95 %.0f ms · รวม %.1f วินาที"
          % (p50, p95, time.time() - t_all))

    notes = []

    head("③ จัดกลุ่มเป็น 'ใบ' (เฟรมที่ติดกัน = ใบเดียวกัน)")
    # นี่คือหัวใจ: บอกว่าตอนถ่ายจริงได้ "กี่เฟรมต่อใบ" ซึ่งเป็นตัวชี้ว่าโหมด
    # free-running มีโอกาสเห็นกระป๋องทันหรือไม่ — วัดจากภาพจริง ไม่ใช่คำนวณจากทฤษฎี
    runs, cur = [], 0
    empty = 0
    for r in ok_rows:
        present = bool(r["cans"] or r["defects"])
        if present:
            cur += 1
            empty = 0
        else:
            empty += 1
            if cur and empty >= args.gap:
                runs.append(cur)
                cur = 0
    if cur:
        runs.append(cur)

    if not runs:
        print("   ⚠️ ไม่พบกระป๋องเลยสักใบ — โมเดลไม่รู้จักภาพจากกล้องนี้ (domain shift)")
        print("      หรือ ROI/โฟกัส/แสง ยังไม่เข้าที่ · ดูภาพในโฟลเดอร์ด้วยตาก่อน")
    else:
        print("   จำนวน 'ใบ' ที่นับได้ : %d" % len(runs))
        print("   เฟรมต่อใบ          : ต่ำสุด %d · กลาง %d · สูงสุด %d"
              % (min(runs), int(statistics.median(runs)), max(runs)))
        thin = [n for n in runs if n <= 1]
        if thin:
            print("   ⚠️ มี %d ใบที่เห็นแค่ 1 เฟรม — เสี่ยงหลุดถ้ารันสด" % len(thin))
        if args.fps:
            span_s = len(ok_rows) / float(args.fps)
            rate = len(runs) / span_s if span_s > 0 else 0
            print("   ช่วงเวลาที่บันทึก    : %.1f วินาที (ที่ %.1f fps)" % (span_s, args.fps))
            print("   อัตราที่วัดได้       : **%.1f ใบ/วินาที** (คาบ %.0f ms/ใบ)"
                  % (rate, 1000.0 / rate if rate else 0))

    # ── ③ⓑ ความเร็วสายพาน + ระยะเบลอ (เติมตัวเลข P0 ที่แผน §12 ยังว่างอยู่) ──
    if args.fps and runs and args.pitch_mm:
        span_s = len(ok_rows) / float(args.fps)
        rate = len(runs) / span_s if span_s > 0 else 0
        if rate > 0:
            speed_mm_s = rate * args.pitch_mm
            print("\n   🏃 ความเร็วสายพาน (จาก %.1f ใบ/วิ × pitch %.0f mm) = "
                  "**%.0f mm/s (%.2f m/s)**" % (rate, args.pitch_mm, speed_mm_s, speed_mm_s / 1000.0))
            max_exp_us = (args.blur_limit_mm / speed_mm_s) * 1e6 if speed_mm_s else 0
            print("      exposure สูงสุดที่เบลอไม่เกิน %.2f mm = **%.0f µs**"
                  % (args.blur_limit_mm, max_exp_us))
            if args.exposure_us:
                blur_mm = speed_mm_s * (args.exposure_us / 1e6)
                mark = "✅" if blur_mm <= args.blur_limit_mm else "❌"
                print("      %s ที่ exposure %.0f µs ที่ใช้จริง ⇒ เบลอ **%.2f mm**"
                      % (mark, args.exposure_us, blur_mm))
                if blur_mm > args.blur_limit_mm:
                    notes.append(
                        "ภาพเบลอจากการเคลื่อนที่ %.2f mm (เกินเกณฑ์ %.2f mm) ที่ exposure %.0f µs — "
                        "ต้องลด exposure ลงเหลือ ≤%.0f µs ซึ่งต้องเพิ่มไฟราว %.0f เท่า "
                        "(รายละเอียดใน §6 ของแผน)"
                        % (blur_mm, args.blur_limit_mm, args.exposure_us, max_exp_us,
                           args.exposure_us / max_exp_us if max_exp_us else 0))

    head("④ รันสดจะตามทันไหม (คิดจากเวลาที่วัดได้จริง)")
    # ⚠️ ค่านี้ต้องเป็น "เวลาตอนรันสด" ไม่ใช่เวลาตอนวัดออฟไลน์เสมอไป —
    #    ถ้าเอาไฟล์ไปวัดบนเครื่องอื่น (แรงกว่า/ช้ากว่า) ข้อสรุปจะผิด จึงเปิดให้ระบุเองได้
    live_ms = args.live_ms if args.live_ms else p50
    infer_fps = 1000.0 / live_ms if live_ms else 0
    print("   โมเดลตรวจได้ ~%.1f ครั้ง/วินาที (%.0f ms/เฟรม ที่ imgsz %d%s)"
          % (infer_fps, live_ms, imgsz,
             "" if args.live_ms is None else " — ค่าที่ระบุเอง"))
    reset_frames = getattr(config, "DEFECT_RESET_FRAMES", 3)
    gap_needed_ms = reset_frames * live_ms
    print("   ตรรกะนับของโหมด live ต้องเห็น 'เฟรมว่าง' ติดกัน %d ครั้งจึงถือว่าใบผ่านไป"
          % reset_frames)
    print("   ⇒ ต้องมีช่องว่างระหว่างใบอย่างน้อย ~%.0f ms" % gap_needed_ms)
    if args.fps and runs:
        span_s = len(ok_rows) / float(args.fps)
        rate = len(runs) / span_s if span_s > 0 else 0
        if rate > 0:
            cycle_ms = 1000.0 / rate
            print("   คาบจริงของไลน์      : %.0f ms/ใบ" % cycle_ms)
            if cycle_ms < gap_needed_ms:
                notes.append(
                    "คาบของไลน์ (%.0f ms) สั้นกว่าช่องว่างที่ตัวนับต้องการ (%.0f ms) ⇒ "
                    "**การนับตอนรันสดจะรวมหลายใบเป็นใบเดียว** ใช้ตัดสินจำนวนไม่ได้ "
                    "(ต้องทำ triggered mode ตาม §4 ของแผน)" % (cycle_ms, gap_needed_ms))
            if infer_fps and rate > infer_fps / 2.0:
                notes.append(
                    "อัตราไลน์ (%.1f ใบ/วิ) เทียบกับความเร็วโมเดล (%.1f ครั้ง/วิ) ⇒ "
                    "ได้ตรวจไม่ถึง 2 ครั้งต่อใบ ความน่าเชื่อถือต่ำ" % (rate, infer_fps))
    if not with_defect and with_can:
        notes.append("เห็นกระป๋องแต่ไม่พบรอยบุบเลยสักเฟรม — ถ้าในภาพมีใบบุบจริง "
                     "แปลว่าโมเดลยังใช้กับกล้อง/แสงชุดนี้ไม่ได้ ต้องเทรนใหม่ด้วยภาพชุดนี้")

    head("สรุป")
    if notes:
        for n in notes:
            print("   ⚠️ %s" % n)
    else:
        print("   ✅ ไม่พบข้อสังเกตจากตัวเลข (ยังต้องดูภาพด้วยตาเพื่อยืนยันคุณภาพ)")
    if args.save_dir and with_defect:
        print("   ภาพที่พบรอยบุบถูกเซฟไว้ที่ %s" % os.path.abspath(args.save_dir))

    if args.json:
        try:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump({"folder": os.path.abspath(args.folder), "model": model_path,
                           "imgsz": imgsz, "frames": rows, "runs": runs,
                           "p50_ms": p50, "p95_ms": p95, "notes": notes},
                          f, ensure_ascii=False, indent=2, default=str)
            print("   เขียนผลลง %s" % os.path.abspath(args.json))
        except Exception as e:
            print("   ⚠️ เขียน JSON ไม่สำเร็จ: %s" % e)
    print(_LINE)
    return 1 if notes else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nยกเลิกโดยผู้ใช้")
        sys.exit(2)
