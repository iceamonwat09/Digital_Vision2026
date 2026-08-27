"""
verify_openvino.py — เทียบผลตรวจ PyTorch (.pt) vs OpenVINO (CPU / iGPU Iris Xe)
ให้ "ตรงกัน" ก่อนเปิดใช้ iGPU ในระบบจริง (config.OPENVINO_DEVICE = "intel:gpu").

ทำไมต้องมีไฟล์นี้
-----------------
บทเรียนจริงจากรอบ openvino 2025.3.0 บน Python 3.9 (off-spec): export สำเร็จ
โหลดสำเร็จ ไม่มี error ใดๆ **แต่ตรวจไม่เจอทุกโหมดแบบเงียบๆ** — failure mode ที่
อันตรายที่สุดสำหรับงาน QC. เพิ่มเติมสำหรับ iGPU: GPU plugin ของ OpenVINO
default รันภายในเป็น FP16 แม้ IR จะเป็น FP32 → ความแม่นอาจ drift โดยไม่มี
สัญญาณเตือน. สคริปต์นี้คือตาข่ายนิรภัย: รันภาพชุดเดียวกันผ่าน PyTorch (แหล่ง
ความจริง) และ OpenVINO ทุก device ที่ขอ แล้วเทียบจำนวนกล่อง/คลาส/ความมั่นใจ/
ตำแหน่ง (IoU) ด้วยเกณฑ์ชุดเดียวกับ verify_onnx.py. ต้อง PASS เท่านั้นจึงจะตั้ง
OPENVINO_DEVICE ในระบบจริง.

แถมในรันเดียว: วัดความเร็วเฉลี่ย ms/เฟรม ของ PyTorch / ONNX CPU (ถ้ามีไฟล์
.onnx อยู่แล้ว — baseline production ปัจจุบัน) / OpenVINO ต่อ device เพื่อใช้
ตัดสินความคุ้มด้วยตัวเลขจริง ไม่ใช่ความรู้สึก.

วิธีใช้ (รันบนเครื่องสถานี ที่มีภาพกระป๋องจริง)
------------------------------------------------
    py -3.9 -m pip install "openvino==2024.6.0"
    py -3.9 verify_openvino.py --weights weights\\can_dent\\bestX.pt --images path\\to\\sample_cans
    py -3.9 verify_openvino.py --weights weights\\can_dent\\bestX.pt --images path\\to\\sample_cans --devices intel:gpu

ค่าเริ่มต้นทดสอบทั้ง intel:cpu และ intel:gpu ที่ imgsz 480+1280 (live + snapshot).
ใช้ภาพจริงอย่างน้อย ~10–20 รูปที่มีทั้งกระป๋องดี/บุบ เพื่อให้การเทียบมีความหมาย.

Exit code: 0 = PASS ทุก device×imgsz, 1 = FAIL/ERROR/INCONCLUSIVE.
"""

import argparse
import os
import sys
import time

import numpy as np

try:
    import config
except Exception:
    config = None

# เกณฑ์ PASS + ตัวช่วยจับคู่กล่อง ใช้ชุดเดียวกับ verify_onnx.py (แหล่งเดียว
# ของความจริง — แก้เกณฑ์ที่นั่นที่เดียว มีผลทั้งสองสคริปต์)
from verify_onnx import (IOU_MATCH, MIN_MEAN_IOU, MAX_CONF_DIFF,   # noqa: F401
                         MAX_COUNT_DRIFT, _extract, _match, _load_images)


def _time_call(model, img, imgsz, args, device=None):
    """รัน inference 1 ครั้ง คืน (result, elapsed_ms)."""
    kwargs = dict(imgsz=imgsz, conf=args.conf, iou=args.iou,
                  max_det=args.max_det, verbose=False)
    if device:
        kwargs["device"] = device
    t0 = time.perf_counter()
    r = model(img, **kwargs)[0]
    return r, (time.perf_counter() - t0) * 1000.0


def _run_set(model, images, imgsz, args, device=None, min_timed=5):
    """
    รันทั้งชุดภาพ คืน (dets_per_image, avg_ms).
    ภาพแรกใช้เป็น warmup (compile/alloc ครั้งแรก โดยเฉพาะ GPU) — ผลตรวจของมัน
    ถูกใช้เทียบตามปกติ แต่เวลาไม่ถูกนับรวมใน avg_ms. ถ้าตัวอย่างเวลาน้อยกว่า
    ``min_timed`` (เช่นมีภาพรูปเดียว) จะวนรันภาพเดิมซ้ำจนได้เวลาครบ — ผลตรวจ
    ที่ใช้เทียบความแม่นมาจากรอบแรกของแต่ละภาพเท่านั้น ไม่ถูกกระทบ.
    """
    dets, times = [], []
    for i, img in enumerate(images):
        r, ms = _time_call(model, img, imgsz, args, device)
        dets.append(_extract(r))
        if i > 0:
            times.append(ms)
    k = 0
    while len(times) < min_timed:
        _, ms = _time_call(model, images[k % len(images)], imgsz, args, device)
        times.append(ms)
        k += 1
    avg = float(np.mean(times)) if times else float("nan")
    return dets, avg


def _compare(pt_dets_all, ov_dets_all):
    """เทียบผลตรวจสองชุด (ต่อภาพ) คืน dict สถิติรวม + ok/disaster."""
    tot_pt = tot_ov = um_pt = um_ov = 0
    ious, conf_diffs, class_mismatches = [], [], 0
    for pt_dets, ov_dets in zip(pt_dets_all, ov_dets_all):
        tot_pt += len(pt_dets)
        tot_ov += len(ov_dets)
        matched, u_pt, u_ov = _match(pt_dets, ov_dets)
        um_pt += u_pt
        um_ov += u_ov
        for iou_v, cdiff, same_cls in matched:
            ious.append(iou_v)
            conf_diffs.append(cdiff)
            if not same_cls:
                class_mismatches += 1

    mean_iou = float(np.mean(ious)) if ious else (1.0 if tot_pt == 0 == tot_ov else 0.0)
    max_cdiff = float(np.max(conf_diffs)) if conf_diffs else 0.0
    count_drift = abs(tot_pt - tot_ov) / max(tot_pt, 1)

    ok = (mean_iou >= MIN_MEAN_IOU
          and max_cdiff <= MAX_CONF_DIFF
          and count_drift <= MAX_COUNT_DRIFT
          and class_mismatches == 0)
    # กรณีหายนะแบบที่เคยเจอ: PyTorch เจอหลายกล่อง แต่ backend เจอ 0
    disaster = tot_pt >= 3 and tot_ov == 0
    if disaster:
        ok = False
    return {"tot_pt": tot_pt, "tot_ov": tot_ov, "um_pt": um_pt, "um_ov": um_ov,
            "mean_iou": mean_iou, "max_cdiff": max_cdiff,
            "count_drift": count_drift, "class_mismatches": class_mismatches,
            "ok": ok, "disaster": disaster}


def main():
    ap = argparse.ArgumentParser(
        description="Verify OpenVINO (CPU/iGPU) vs PyTorch detection parity + speed.")
    default_weights = getattr(config, "MODEL_PATH", "") if config else ""
    if not default_weights or not os.path.isfile(default_weights):
        # best.pt อาจถูกลบไปแล้ว — หยิบ .pt ตัวแรกที่มีจริงแทนการชี้ไฟล์ที่ไม่มี
        _d = os.path.join("weights", "can_dent")
        _f = sorted(f for f in os.listdir(_d) if f.endswith(".pt")) if os.path.isdir(_d) else []
        default_weights = os.path.join(_d, _f[0]) if _f else os.path.join(_d, "bestX.pt")
    ap.add_argument("--weights", default=default_weights, help="path to .pt weights")
    ap.add_argument("--images", default="", help="folder or single image of real samples")
    ap.add_argument("--imgsz", type=int, nargs="+", default=[480, 1280],
                    help="imgsz values to test (live + snapshot)")
    ap.add_argument("--devices", nargs="+", default=["intel:cpu", "intel:gpu"],
                    help="OpenVINO devices ในรูปแบบ intel:<device> เช่น intel:cpu intel:gpu")
    ap.add_argument("--conf", type=float,
                    default=getattr(config, "CONFIDENCE_THRESHOLD", 0.25) if config else 0.25)
    ap.add_argument("--iou", type=float,
                    default=getattr(config, "IOU_THRESHOLD", 0.45) if config else 0.45)
    ap.add_argument("--max-det", type=int,
                    default=getattr(config, "YOLO_MAX_DET", 20) if config else 20)
    args = ap.parse_args()

    pt_path = args.weights
    if not os.path.exists(pt_path):
        print(f"[ERROR] ไม่พบไฟล์ weights: {pt_path}")
        return 1
    if not pt_path.endswith(".pt"):
        print(f"[ERROR] --weights ต้องเป็นไฟล์ .pt: {pt_path}")
        return 1
    for d in args.devices:
        if ":" not in d or not d.lower().startswith("intel:"):
            print(f"[ERROR] รูปแบบ device ไม่ถูกต้อง: '{d}' (ต้องเป็น intel:<device> เช่น intel:gpu)")
            return 1

    # 0) OpenVINO runtime + device จริงบนเครื่องนี้
    try:
        import openvino as ov
    except Exception as e:
        print(f"[ERROR] import openvino ไม่ได้: {e}")
        print('        ติดตั้ง: py -3.9 -m pip install "openvino==2024.6.0"')
        return 1
    avail = list(ov.Core().available_devices)
    print(f"[INFO] openvino {ov.__version__} | available devices: {avail}")
    for d in args.devices:
        want = d.split(":", 1)[1].upper()
        if not any(a == want or a.startswith(want + ".") for a in avail):
            print(f"[ERROR] device '{want}' ไม่มีบนเครื่องนี้ (เจอ: {avail})")
            print("        ถ้าคาดว่าต้องมี GPU (Iris Xe) → ตรวจ Intel Graphics driver ก่อน")
            return 1

    try:
        from ultralytics import YOLO
    except Exception as e:
        print(f"[ERROR] import ultralytics ไม่ได้: {e}")
        return 1

    # 1) เตรียม OpenVINO IR (export ถ้ายังไม่มี/ค้างเก่า) — FP32/dynamic เหมือนที่แอปใช้จริง
    ov_dir = pt_path[:-3] + "_openvino_model"
    ov_xml = os.path.join(ov_dir, os.path.basename(pt_path)[:-3] + ".xml")
    stale = (os.path.exists(ov_xml)
             and os.path.getmtime(pt_path) > os.path.getmtime(ov_xml))
    if not os.path.exists(ov_xml) or stale:
        print(f"[INFO] กำลัง export OpenVINO IR (FP32/dynamic) จาก {pt_path} ...")
        try:
            YOLO(pt_path).export(format="openvino", dynamic=True, half=False)
        except Exception as e:
            print(f"[ERROR] export OpenVINO ล้มเหลว: {e}")
            return 1
    if not os.path.exists(ov_xml):
        print(f"[ERROR] export แล้วแต่ไม่พบไฟล์ IR: {ov_xml}")
        return 1

    # 2) โหลด PyTorch (แหล่งความจริง) + อ่าน task ไว้ส่งให้ทุก backend ที่ export
    #    (export ทิ้ง task tag → ไม่ส่ง task โมเดล segment จะถูกถอด output ผิด)
    try:
        model_pt = YOLO(pt_path)
        task = getattr(model_pt, "task", None)
        if task:
            print(f"[INFO] task ของโมเดล = '{task}' → โหลด OpenVINO/ONNX ด้วย task เดียวกัน")
    except Exception as e:
        print(f"[ERROR] โหลดโมเดล PyTorch ล้มเหลว: {e}")
        return 1

    # 3) เตรียมรูป
    images = _load_images(args.images)
    structural_only = False
    if not images:
        structural_only = True
        print("[WARN] ไม่ได้ระบุ --images หรือไม่พบรูป → ทำได้แค่ 'structural smoke test'")
        print("       (ยืนยันว่ารันได้+รูปแบบ output ตรงกัน แต่ยัง 'ไม่ใช่' การยืนยันความแม่นจริง)")
        rng = np.random.default_rng(0)
        images = [rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8) for _ in range(4)]

    # ONNX CPU = baseline production ปัจจุบัน — วัดเวลาเทียบด้วยถ้ามีไฟล์อยู่แล้ว
    # (ความแม่น ONNX ถูก verify แยกด้วย verify_onnx.py ไปแล้ว — ที่นี่วัดเวลาอย่างเดียว)
    onnx_path = pt_path[:-3] + ".onnx"
    model_onnx = None
    if os.path.exists(onnx_path):
        try:
            model_onnx = YOLO(onnx_path, task=task) if task else YOLO(onnx_path)
        except Exception as e:
            print(f"[WARN] โหลด ONNX ไม่ได้ ({e}) → ข้ามการวัดเวลา ONNX")

    print("=" * 72)
    print(f"  PyTorch  : {pt_path}")
    print(f"  OpenVINO : {ov_xml}")
    print(f"  ONNX     : {onnx_path if model_onnx else '(ไม่มี — ข้ามการวัดเวลา)'}")
    print(f"  images   : {len(images)}{' (synthetic)' if structural_only else ''}")
    print(f"  imgsz    : {args.imgsz}   devices={args.devices}")
    print(f"  conf={args.conf}  iou={args.iou}  max_det={args.max_det}")
    print("=" * 72)

    overall_pass = True
    speed_rows = []

    for imgsz in args.imgsz:
        # baseline PyTorch ต่อ imgsz (ผลตรวจ + เวลา)
        try:
            pt_dets_all, pt_ms = _run_set(model_pt, images, imgsz, args)
        except Exception as e:
            print(f"  [ERROR] PyTorch inference ล้มเหลวที่ imgsz={imgsz}: {e}")
            return 1
        speed_rows.append((imgsz, "PyTorch (baseline)", pt_ms))

        tot_pt = sum(len(d) for d in pt_dets_all)
        if not structural_only and tot_pt == 0:
            print(f"\n  [WARN] imgsz={imgsz}: PyTorch baseline ตรวจไม่เจออะไรเลยในภาพชุดนี้ →")
            print("         การเทียบไม่มีความหมาย. ใช้ภาพที่มีกระป๋อง/รอยบุบจริงด้วย.")
            overall_pass = False

        if model_onnx is not None:
            try:
                _, onnx_ms = _run_set(model_onnx, images, imgsz, args)
                speed_rows.append((imgsz, "ONNX CPU (prod เดิม)", onnx_ms))
            except Exception as e:
                print(f"  [WARN] วัดเวลา ONNX ล้มเหลวที่ imgsz={imgsz}: {e}")

        for dev in args.devices:
            # แยก instance ต่อ device — ultralytics ผูก device ตอน setup ครั้งแรก
            # ของ predictor, ใช้ instance เดิมสลับ device ไม่ได้
            try:
                model_ov = YOLO(ov_dir, task=task) if task else YOLO(ov_dir)
                ov_dets_all, ov_ms = _run_set(model_ov, images, imgsz, args, device=dev)
            except Exception as e:
                print(f"\n  [ERROR] OpenVINO ({dev}) inference ล้มเหลวที่ imgsz={imgsz}: {e}")
                overall_pass = False
                continue
            speed_rows.append((imgsz, f"OpenVINO {dev}", ov_ms))

            st = _compare(pt_dets_all, ov_dets_all)
            overall_pass = overall_pass and st["ok"]
            print(f"\n  imgsz={imgsz}  device={dev}:")
            print(f"    detections   PyTorch={st['tot_pt']}  OpenVINO={st['tot_ov']}"
                  f"  (drift={st['count_drift']:.1%})")
            print(f"    unmatched    PyTorch-only={st['um_pt']}  OpenVINO-only={st['um_ov']}")
            print(f"    mean IoU     {st['mean_iou']:.4f}   (ต้อง ≥ {MIN_MEAN_IOU})")
            print(f"    max Δconf    {st['max_cdiff']:.4f}   (ต้อง ≤ {MAX_CONF_DIFF})")
            print(f"    class mismatch {st['class_mismatches']}")
            if st["disaster"]:
                print("    [!] DISASTER: OpenVINO เจอ 0 กล่องทั้งที่ PyTorch เจอหลายกล่อง — backend ใช้ไม่ได้")
            print(f"    → {'PASS' if st['ok'] else 'FAIL'}")

    # 4) สรุปความเร็ว (ตัวเลขสำหรับตัดสินความคุ้ม — เทียบกับ ONNX CPU = prod เดิม)
    print("\n" + "=" * 72)
    print("  ความเร็วเฉลี่ย ms/เฟรม (ไม่รวม warmup เฟรมแรก):")
    for imgsz, label, ms in speed_rows:
        fps = 1000.0 / ms if ms and ms == ms and ms > 0 else 0.0
        print(f"    imgsz={imgsz:<5} {label:<24} {ms:7.1f} ms  (~{fps:.1f} FPS)")
    if len(images) < 10:
        print(f"    [WARN] ภาพมีแค่ {len(images)} รูป → ทั้งความแม่นและเวลาเชื่อถือได้ต่ำ")
        print("           เกณฑ์ของ repo นี้: ใช้ภาพจริง ≥10-20 รูป ผสมกระป๋องดี/บุบ ก่อนเปิดใช้จริง")

    print("\n" + "=" * 72)
    if structural_only:
        print("  ผลรวม: structural smoke test เท่านั้น (ยังไม่ใช่การยืนยันความแม่นจริง)")
        print("  กรุณารันใหม่พร้อม --images <โฟลเดอร์ภาพกระป๋องจริง> ก่อนตั้ง OPENVINO_DEVICE")
        print("  VERDICT: INCONCLUSIVE")
        print("=" * 72)
        return 1

    print("  VERDICT: "
          + ("PASS ✅  → ตั้ง OPENVINO_DEVICE = \"intel:gpu\" ใน config.py ได้ (ถ้าตัวเลขความเร็วคุ้ม)"
             if overall_pass else
             "FAIL ❌  → ห้ามตั้ง OPENVINO_DEVICE — ใช้ ONNX CPU เดิมต่อไป"))
    print("=" * 72)
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
