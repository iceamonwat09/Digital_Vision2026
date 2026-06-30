"""
verify_onnx.py — เทียบผลตรวจ PyTorch (.pt) vs ONNX Runtime (.onnx) ให้ "ตรงกัน"
ก่อนเปิดใช้ ONNX ในระบบจริง (config.USE_ONNX = True).

ทำไมต้องมีไฟล์นี้
-----------------
รอบ OpenVINO ที่ผ่านมา โมเดลที่ export ออกมา "ตรวจไม่เจอ dent เลย" โดยไม่ขึ้น error
ใดๆ — เป็นความผิดพลาดแบบเงียบ (silent) ที่อันตรายที่สุดสำหรับงาน QC. สคริปต์นี้คือ
ตาข่ายนิรภัย: รันภาพชุดเดียวกันผ่านทั้งสอง backend แล้วเทียบจำนวนกล่อง/คลาส/ความมั่นใจ/
ตำแหน่ง (IoU). ต้องผ่าน (PASS) เท่านั้นจึงจะเปิด USE_ONNX ในระบบจริง.

วิธีใช้ (รันบนเครื่องสถานี ที่มีภาพกระป๋องจริง)
------------------------------------------------
    py -3.9 verify_onnx.py --images path\\to\\sample_cans
    py -3.9 verify_onnx.py --images path\\to\\sample_cans --imgsz 480 1280

ค่าเริ่มต้นใช้ weights/can_dent/best.pt และ imgsz 480+1280 (live + snapshot).
ใช้ภาพจริงอย่างน้อย ~10–20 รูปที่มีทั้งกระป๋องดี/บุบ เพื่อให้การเทียบมีความหมาย.

Exit code: 0 = PASS, 1 = FAIL/ERROR (ใช้กับ CI/สคริปต์ได้).
"""

import argparse
import glob
import os
import sys

import numpy as np

try:
    import config
except Exception:
    config = None


# ── เกณฑ์ PASS (เลือกแบบเข้มพอจะจับ "ตรวจผิด" แต่ยอมรับ noise FP32 เล็กน้อย) ──
IOU_MATCH = 0.5        # ถือว่ากล่องสองอันเป็นอันเดียวกันเมื่อ IoU ≥ ค่านี้
MIN_MEAN_IOU = 0.97    # ค่าเฉลี่ย IoU ของกล่องที่จับคู่ได้ ต้อง ≥ ค่านี้
MAX_CONF_DIFF = 0.05   # ส่วนต่าง confidence สูงสุดที่ยอมรับได้
MAX_COUNT_DRIFT = 0.05 # สัดส่วนจำนวนกล่องที่ต่างกันรวมทั้งชุด ต้อง ≤ ค่านี้


def _iou(a, b):
    """IoU ของกล่อง xyxy สองอัน."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _extract(result):
    """ดึง (bbox xyxy, conf, cls) เป็น list ของ dict จากผลลัพธ์ ultralytics หนึ่งภาพ."""
    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return []
    xyxy = boxes.xyxy.cpu().numpy()
    conf = boxes.conf.cpu().numpy()
    cls = boxes.cls.cpu().numpy().astype(int)
    out = []
    for (x1, y1, x2, y2), c, k in zip(xyxy, conf, cls):
        out.append({"bbox": [float(x1), float(y1), float(x2), float(y2)],
                    "conf": float(c), "cls": int(k)})
    return out


def _match(pt_dets, on_dets):
    """
    จับคู่กล่อง pt↔onnx แบบ greedy ตาม IoU.
    คืน (matched, n_pt_unmatched, n_on_unmatched) โดย matched = list ของ
    (iou, conf_diff, same_class).
    """
    matched = []
    used = set()
    for p in pt_dets:
        best_j, best_iou = -1, 0.0
        for j, o in enumerate(on_dets):
            if j in used:
                continue
            v = _iou(p["bbox"], o["bbox"])
            if v > best_iou:
                best_iou, best_j = v, j
        if best_j >= 0 and best_iou >= IOU_MATCH:
            used.add(best_j)
            o = on_dets[best_j]
            matched.append((best_iou, abs(p["conf"] - o["conf"]), p["cls"] == o["cls"]))
    return matched, len(pt_dets) - len(matched), len(on_dets) - len(used)


def _load_images(images_arg):
    """คืน list ของ path รูปภาพ. รองรับทั้งไฟล์เดียวและโฟลเดอร์."""
    if not images_arg:
        return []
    if os.path.isfile(images_arg):
        return [images_arg]
    exts = ("*.jpg", "*.jpeg", "*.png", "*.bmp", "*.JPG", "*.JPEG", "*.PNG")
    files = []
    for e in exts:
        files.extend(glob.glob(os.path.join(images_arg, e)))
    return sorted(set(files))


def main():
    ap = argparse.ArgumentParser(description="Verify ONNX vs PyTorch detection parity.")
    default_weights = getattr(config, "MODEL_PATH", "weights/can_dent/best.pt") if config else "weights/can_dent/best.pt"
    ap.add_argument("--weights", default=default_weights, help="path to .pt weights")
    ap.add_argument("--images", default="", help="folder or single image of real samples")
    ap.add_argument("--imgsz", type=int, nargs="+", default=[480, 1280],
                    help="imgsz values to test (live + snapshot)")
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

    try:
        from ultralytics import YOLO
    except Exception as e:
        print(f"[ERROR] import ultralytics ไม่ได้: {e}")
        return 1

    # 1) เตรียมไฟล์ ONNX (export ถ้ายังไม่มี) — FP32 / dynamic เหมือนที่ระบบใช้จริง
    onnx_path = pt_path[:-3] + ".onnx"
    if not os.path.exists(onnx_path):
        print(f"[INFO] กำลัง export ONNX (FP32/dynamic) จาก {pt_path} ...")
        export_kwargs = dict(format="onnx", dynamic=True, half=False)
        opset = getattr(config, "ONNX_OPSET", None) if config else None
        if opset:
            export_kwargs["opset"] = int(opset)
        try:
            YOLO(pt_path).export(**export_kwargs)
        except Exception as e:
            print(f"[ERROR] export ONNX ล้มเหลว: {e}")
            print("        ตรวจว่าได้ติดตั้ง: py -3.9 -m pip install onnxruntime==1.19.2 onnxslim onnx")
            return 1
    if not os.path.exists(onnx_path):
        print(f"[ERROR] export แล้วแต่ไม่พบไฟล์ ONNX: {onnx_path}")
        return 1

    # 2) โหลดทั้งสอง backend
    try:
        model_pt = YOLO(pt_path)
        model_on = YOLO(onnx_path)
    except Exception as e:
        print(f"[ERROR] โหลดโมเดลล้มเหลว: {e}")
        print("        ถ้าเป็น onnxruntime: py -3.9 -m pip install onnxruntime==1.19.2")
        return 1

    # 3) เตรียมรูป
    images = _load_images(args.images)
    structural_only = False
    if not images:
        structural_only = True
        print("[WARN] ไม่ได้ระบุ --images หรือไม่พบรูป → ทำได้แค่ 'structural smoke test'")
        print("       (รันภาพ noise สุ่ม ยืนยันว่าทั้งสอง backend รันได้+รูปแบบ output ตรงกัน")
        print("        แต่ยัง 'ไม่ใช่' การยืนยันความแม่นจริง). กรุณารันใหม่พร้อม --images <โฟลเดอร์ภาพกระป๋องจริง>")
        rng = np.random.default_rng(0)
        images = [rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8) for _ in range(3)]

    print("=" * 72)
    print(f"  PyTorch : {pt_path}")
    print(f"  ONNX    : {onnx_path}")
    print(f"  images  : {len(images)}{' (synthetic)' if structural_only else ''}")
    print(f"  imgsz   : {args.imgsz}   conf={args.conf}  iou={args.iou}  max_det={args.max_det}")
    print("=" * 72)

    overall_pass = True

    for imgsz in args.imgsz:
        tot_pt = tot_on = tot_unmatched_pt = tot_unmatched_on = 0
        ious, conf_diffs, class_mismatches = [], [], 0

        for img in images:
            try:
                r_pt = model_pt(img, imgsz=imgsz, conf=args.conf, iou=args.iou,
                                max_det=args.max_det, verbose=False)[0]
                r_on = model_on(img, imgsz=imgsz, conf=args.conf, iou=args.iou,
                                max_det=args.max_det, verbose=False)[0]
            except Exception as e:
                print(f"  [ERROR] inference ล้มเหลวที่ imgsz={imgsz}: {e}")
                return 1

            pt_dets, on_dets = _extract(r_pt), _extract(r_on)
            tot_pt += len(pt_dets)
            tot_on += len(on_dets)
            matched, um_pt, um_on = _match(pt_dets, on_dets)
            tot_unmatched_pt += um_pt
            tot_unmatched_on += um_on
            for iou_v, cdiff, same_cls in matched:
                ious.append(iou_v)
                conf_diffs.append(cdiff)
                if not same_cls:
                    class_mismatches += 1

        mean_iou = float(np.mean(ious)) if ious else (1.0 if tot_pt == 0 == tot_on else 0.0)
        max_cdiff = float(np.max(conf_diffs)) if conf_diffs else 0.0
        count_drift = abs(tot_pt - tot_on) / max(tot_pt, 1)

        # เกณฑ์ตัดสินต่อ imgsz
        ok = (
            mean_iou >= MIN_MEAN_IOU
            and max_cdiff <= MAX_CONF_DIFF
            and count_drift <= MAX_COUNT_DRIFT
            and class_mismatches == 0
        )
        # กรณีหายนะแบบ OpenVINO: PyTorch เจอเยอะ แต่ ONNX เจอ ~0
        disaster = tot_pt >= 3 and tot_on == 0
        if disaster:
            ok = False

        # เตือนกรณีภาพทดสอบไม่มีของให้ตรวจเลย → ผ่านแบบไม่มีความหมาย
        if not structural_only and tot_pt == 0:
            print(f"\n  [WARN] imgsz={imgsz}: PyTorch baseline ตรวจไม่เจออะไรเลยในภาพชุดนี้ →")
            print("         การเทียบไม่มีความหมาย. ใช้ภาพที่มีกระป๋อง/รอยบุบจริงด้วย.")
            overall_pass = False

        overall_pass = overall_pass and ok
        print(f"\n  imgsz={imgsz}:")
        print(f"    detections   PyTorch={tot_pt}  ONNX={tot_on}  (drift={count_drift:.1%})")
        print(f"    unmatched    PyTorch-only={tot_unmatched_pt}  ONNX-only={tot_unmatched_on}")
        print(f"    mean IoU     {mean_iou:.4f}   (ต้อง ≥ {MIN_MEAN_IOU})")
        print(f"    max Δconf    {max_cdiff:.4f}   (ต้อง ≤ {MAX_CONF_DIFF})")
        print(f"    class mismatch {class_mismatches}")
        if disaster:
            print("    [!] DISASTER: ONNX เจอ 0 กล่องทั้งที่ PyTorch เจอหลายกล่อง — backend ใช้ไม่ได้")
        print(f"    → {'PASS' if ok else 'FAIL'}")

    print("\n" + "=" * 72)
    if structural_only:
        print("  ผลรวม: structural smoke test เท่านั้น (ยังไม่ใช่การยืนยันความแม่นจริง)")
        print("  กรุณารันใหม่พร้อม --images <โฟลเดอร์ภาพกระป๋องจริง> ก่อนเปิด USE_ONNX")
        # ไม่ให้ผ่านแบบ "ของจริง" เพื่อกันเปิดใช้โดยยังไม่ทดสอบภาพจริง
        print("  VERDICT: INCONCLUSIVE")
        print("=" * 72)
        return 1

    print(f"  VERDICT: {'PASS ✅  → เปิด USE_ONNX = True ได้' if overall_pass else 'FAIL ❌  → อย่าเปิด USE_ONNX'}")
    print("=" * 72)
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
