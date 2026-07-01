"""
diagnose_exposure.py — ทดสอบว่ากล้องของเครื่องนี้ "คุม exposure/ความสว่างผ่าน OpenCV
ได้จริงไหม" ก่อนจะลงทุนสร้าง UI ปรับค่า.

ทำไมต้องมี
----------
กล้อง USB + backend Windows (MSMF/DSHOW) แต่ละตัวตอบสนองคำสั่งปรับกล้องไม่เหมือนกัน —
บางตัว set ค่าแล้วภาพเปลี่ยนจริง บางตัว "เมินเงียบๆ" (get อ่านค่ากลับมาเท่าเดิม/ภาพไม่เปลี่ยน).
สคริปต์นี้ทดสอบ 2 อย่าง:
  1) set ค่าแล้ว "ค่าติดไหม" (get อ่านกลับ)
  2) **ภาพสว่างจริงเปลี่ยนไหม** (วัดความสว่างเฉลี่ยของเฟรม — นี่คือความจริงที่เชื่อได้ที่สุด)
ทำต่อทุก backend (MSMF/DSHOW/Default) แล้วสรุปว่าคุมได้ไหม + ควรใช้ backend ไหน.

วิธีใช้ (รันบนเครื่องสถานี — ปิด app.py ก่อน ไม่งั้นกล้องถูกจอง)
--------------------------------------------------------------
    py -3.9 diagnose_exposure.py
    py -3.9 diagnose_exposure.py --index 0
"""

import argparse
import time

import cv2
import numpy as np

try:
    import config
    DEFAULT_INDEX = getattr(config, "CAMERA_INDEX", 0)
    W, H = getattr(config, "CAMERA_WIDTH", 640), getattr(config, "CAMERA_HEIGHT", 480)
except Exception:
    DEFAULT_INDEX, W, H = 0, 640, 480

# คุณสมบัติกล้องที่เกี่ยวกับความสว่าง (ชื่อ → OpenCV prop)
PROPS = {
    "AUTO_EXPOSURE": cv2.CAP_PROP_AUTO_EXPOSURE,
    "EXPOSURE":      cv2.CAP_PROP_EXPOSURE,
    "BRIGHTNESS":    cv2.CAP_PROP_BRIGHTNESS,
    "GAIN":          cv2.CAP_PROP_GAIN,
    "CONTRAST":      cv2.CAP_PROP_CONTRAST,
    "GAMMA":         cv2.CAP_PROP_GAMMA,
}

BACKENDS = [("MSMF", cv2.CAP_MSMF), ("DSHOW", cv2.CAP_DSHOW), ("Default", None)]


def _mean_brightness(cap):
    """ความสว่างเฉลี่ย (0-255) ของเฟรม หลัง flush เฟรมค้างทิ้ง."""
    for _ in range(6):
        cap.read()
    time.sleep(0.15)
    ok, f = cap.read()
    if not ok or f is None:
        return None
    return round(float(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).mean()), 1)


def _open(index, backend):
    cap = cv2.VideoCapture(index) if backend is None else cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, H)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    ok, f = cap.read()
    if not ok or f is None:
        cap.release()
        return None
    return cap


def test_backend(index, name, backend):
    print(f"\n{'='*66}\n  BACKEND: {name}\n{'='*66}")
    cap = _open(index, backend)
    if cap is None:
        print("  เปิดกล้องด้วย backend นี้ไม่ได้ (ข้าม)")
        return None

    # 1) ค่าปัจจุบันของทุก prop
    print("  ค่าปัจจุบันที่อ่านได้:")
    for pname, prop in PROPS.items():
        print(f"    {pname:<13} = {cap.get(prop)}")

    verdict = {"exposure": False, "brightness": False}

    # 2) ทดสอบ EXPOSURE: ปิด auto แล้วไล่ค่า → ดูว่าภาพสว่างเปลี่ยนไหม (ความจริงที่เชื่อได้)
    print("\n  ── ทดสอบ EXPOSURE (ปิด auto แล้วไล่ค่า) ──")
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)   # manual (แบบ Windows)
    ae_back = cap.get(cv2.CAP_PROP_AUTO_EXPOSURE)
    print(f"    ตั้ง AUTO_EXPOSURE=0.25 (manual) → อ่านกลับได้ {ae_back}")
    base = _mean_brightness(cap)
    bright_by_ev = {}
    for ev in (-8, -6, -4, -2):
        cap.set(cv2.CAP_PROP_EXPOSURE, ev)
        got = cap.get(cv2.CAP_PROP_EXPOSURE)
        b = _mean_brightness(cap)
        bright_by_ev[ev] = b
        print(f"    set EXPOSURE={ev:>3}  → get={got:<8}  ความสว่างเฟรม={b}")
    vals = [v for v in bright_by_ev.values() if v is not None]
    if vals and (max(vals) - min(vals)) >= 8:     # สว่างเปลี่ยน >8 = คุมได้จริง
        verdict["exposure"] = True
        print(f"    ✅ ความสว่างเปลี่ยนตาม EXPOSURE จริง (ช่วง {min(vals)}–{max(vals)}) → คุมได้")
    else:
        print(f"    ❌ ความสว่างแทบไม่เปลี่ยน → กล้อง/backend นี้ไม่รับ EXPOSURE ผ่าน OpenCV")

    # 3) ทดสอบ BRIGHTNESS prop โดยตรง
    print("\n  ── ทดสอบ BRIGHTNESS ──")
    old = cap.get(cv2.CAP_PROP_BRIGHTNESS)
    b_lo = _set_and_measure(cap, cv2.CAP_PROP_BRIGHTNESS, old - 40 if old else 30)
    b_hi = _set_and_measure(cap, cv2.CAP_PROP_BRIGHTNESS, old + 40 if old else 200)
    if b_lo is not None and b_hi is not None and abs(b_hi - b_lo) >= 8:
        verdict["brightness"] = True
        print(f"    ✅ BRIGHTNESS คุมได้ (ต่ำ={b_lo} / สูง={b_hi})")
    else:
        print(f"    ❌ BRIGHTNESS แทบไม่เปลี่ยน (ต่ำ={b_lo} / สูง={b_hi})")
    cap.set(cv2.CAP_PROP_BRIGHTNESS, old)

    cap.release()
    return verdict


def _set_and_measure(cap, prop, value):
    cap.set(prop, value)
    return _mean_brightness(cap)


def main():
    ap = argparse.ArgumentParser(description="Test camera exposure/brightness controllability.")
    ap.add_argument("--index", type=int, default=DEFAULT_INDEX)
    args = ap.parse_args()

    print(f"กล้อง index={args.index}  ความละเอียดทดสอบ {W}x{H}")
    print("หมายเหตุ: ปิด app.py ก่อน ไม่งั้นกล้องถูกจอง / อ่านไม่ได้")

    summary = {}
    for name, backend in BACKENDS:
        try:
            summary[name] = test_backend(args.index, name, backend)
        except Exception as e:
            print(f"  [{name}] error: {e}")
            summary[name] = None

    # สรุปรวม
    print(f"\n{'#'*66}\n  สรุป: backend ไหนคุมความสว่างได้บ้าง\n{'#'*66}")
    usable = []
    for name, v in summary.items():
        if not v:
            print(f"  {name:<8}: เปิดไม่ได้ / ทดสอบไม่ได้")
            continue
        exp = "คุมได้" if v["exposure"] else "ไม่ได้"
        brt = "คุมได้" if v["brightness"] else "ไม่ได้"
        print(f"  {name:<8}: EXPOSURE={exp} | BRIGHTNESS={brt}")
        if v["exposure"] or v["brightness"]:
            usable.append(name)

    print()
    if usable:
        print(f"  ✅ ทำ UI ปรับความสว่างได้! ใช้ backend: {', '.join(usable)}")
        print("     → บอก Claude ว่าใช้ backend ไหนได้ แล้วให้สร้าง Tab ปรับค่าต่อ")
    else:
        print("  ❌ กล้องตัวนี้ไม่รับการปรับผ่าน OpenCV เลย (ทุก backend)")
        print("     → ต้องปรับผ่านซอฟต์แวร์กล้องของผู้ผลิต หรือใช้ไฟส่องภายนอกแทน")
    print("#" * 66)


if __name__ == "__main__":
    main()
