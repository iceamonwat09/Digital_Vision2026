"""
diagnose_exposure.py — ทดสอบว่ากล้องของเครื่องนี้ "คุมความสว่างผ่าน OpenCV ได้จริงไหม"
และ "knob ไหนใช้ได้" ก่อนจะสร้าง UI ปรับค่า.

ทำไมต้องมี / บทเรียนจากเวอร์ชันแรก
------------------------------------
กล้อง USB + backend Windows (MSMF/DSHOW) ตอบสนองคำสั่งปรับกล้องไม่เหมือนกัน — บางตัว
set แล้วภาพเปลี่ยนจริง บางตัว "เมินเงียบๆ". และการสลับไป **manual exposure** ทำให้กล้อง
UVC หลายรุ่นเฟรมดำชั่วขณะ → ถ้า warmup ไม่พอจะวัดความสว่างเพี้ยน (อ่านได้ 0).

เวอร์ชันนี้จึง:
  • วัด **baseline** ก่อน (ยืนยันว่ากล้องให้ภาพปกติ ไม่ดำ — ถ้า baseline=0 แปลว่า
    การวัด/ฉากมีปัญหา ไม่ใช่ knob)
  • เทสต์ knob ที่ไม่เกี่ยว exposure (BRIGHTNESS/GAIN/GAMMA/CONTRAST) ใน session สะอาด
    (โหมด auto ปกติ ไม่ยุ่ง exposure) → ไม่ปนสถานะดำ
  • เทสต์ EXPOSURE แยก session + warmup นาน
  • วัดความสว่างแบบเฉลี่ยหลายเฟรม + warmup นาน (กันเฟรม transition)
เกณฑ์ตัดสิน = **ภาพสว่างจริงเปลี่ยนไหม** (ไม่เชื่อแค่ค่า get).

วิธีใช้ (บนเครื่องสถานี — ปิด app.py ก่อน ไม่งั้นกล้องถูกจอง)
    py -3.9 diagnose_exposure.py
    py -3.9 diagnose_exposure.py --index 0
"""

import argparse
import time

import cv2

try:
    import config
    DEFAULT_INDEX = getattr(config, "CAMERA_INDEX", 0)
    W, H = getattr(config, "CAMERA_WIDTH", 640), getattr(config, "CAMERA_HEIGHT", 480)
except Exception:
    DEFAULT_INDEX, W, H = 0, 640, 480

BACKENDS = [("MSMF", cv2.CAP_MSMF), ("DSHOW", cv2.CAP_DSHOW), ("Default", None)]

# ระดับที่ถือว่า "ภาพสว่างเปลี่ยนจริง" (mean gray 0-255)
DELTA = 6.0


def _open(index, backend):
    cap = cv2.VideoCapture(index) if backend is None else cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, H)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    # warmup ยาวหลังเปิด (auto-exposure ปรับตัวเสร็จ)
    for _ in range(25):
        cap.read()
    time.sleep(0.4)
    ok, f = cap.read()
    if not ok or f is None:
        cap.release()
        return None
    return cap


def _measure(cap):
    """ความสว่างเฉลี่ย (0-255) — warmup นาน + เฉลี่ยหลายเฟรม กันเฟรม transition/ดำ."""
    for _ in range(20):
        cap.read()
    time.sleep(0.35)
    vals = []
    for _ in range(6):
        ok, f = cap.read()
        if ok and f is not None:
            vals.append(float(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).mean()))
        time.sleep(0.03)
    return round(sum(vals) / len(vals), 1) if vals else None


def _sweep(cap, prop, values):
    """set prop ทีละค่า → คืน [(set, get, brightness), ...]."""
    out = []
    for v in values:
        cap.set(prop, v)
        got = round(cap.get(prop), 1)
        out.append((v, got, _measure(cap)))
    return out


def _report(title, rows, baseline):
    print(f"\n  ── {title} ──")
    print(f"    baseline (ไม่แตะ) ความสว่าง = {baseline}")
    brights = []
    for setv, got, b in rows:
        print(f"    set={setv:>5}  get={got:<8}  ความสว่าง={b}")
        if b is not None:
            brights.append(b)
    if len(brights) >= 2 and (max(brights) - min(brights)) >= DELTA:
        print(f"    ✅ คุมได้จริง (ช่วง {min(brights)}–{max(brights)})")
        return True
    print("    ❌ ภาพแทบไม่เปลี่ยน → knob นี้ไม่มีผลผ่าน OpenCV")
    return False


def test_backend(index, name, backend):
    print(f"\n{'='*66}\n  BACKEND: {name}\n{'='*66}")
    v = {"brightness": False, "gain": False, "gamma": False, "exposure": False}

    # ── Session A: knob ที่ไม่เกี่ยว exposure (โหมด auto ปกติ) ──
    cap = _open(index, backend)
    if cap is None:
        print("  เปิดกล้อง backend นี้ไม่ได้ (ข้าม)")
        return None
    base = _measure(cap)
    print(f"  baseline ความสว่าง (auto, ไม่แตะอะไร) = {base}")
    if base == 0.0:
        print("  ⚠️ baseline=0 (ภาพดำ) — เล็งกล้องไปที่วัตถุมีแสง/เปิดไฟก่อน ผลอื่นอาจเชื่อไม่ได้")

    print("  ค่าปัจจุบัน:", {k: round(cap.get(p), 1) for k, p in {
        "BRIGHTNESS": cv2.CAP_PROP_BRIGHTNESS, "GAIN": cv2.CAP_PROP_GAIN,
        "GAMMA": cv2.CAP_PROP_GAMMA, "CONTRAST": cv2.CAP_PROP_CONTRAST}.items()})

    br0 = cap.get(cv2.CAP_PROP_BRIGHTNESS)
    v["brightness"] = _report("BRIGHTNESS (0→255)", _sweep(
        cap, cv2.CAP_PROP_BRIGHTNESS, [0, 64, 128, 192, 255]), base)
    cap.set(cv2.CAP_PROP_BRIGHTNESS, br0)

    gm0 = cap.get(cv2.CAP_PROP_GAMMA)
    v["gamma"] = _report("GAMMA", _sweep(
        cap, cv2.CAP_PROP_GAMMA, [72, 120, 300]), base)
    cap.set(cv2.CAP_PROP_GAMMA, gm0)

    gn0 = cap.get(cv2.CAP_PROP_GAIN)
    v["gain"] = _report("GAIN", _sweep(
        cap, cv2.CAP_PROP_GAIN, [0, 128, 255]), base)
    cap.set(cv2.CAP_PROP_GAIN, gn0)
    cap.release()

    # ── Session B: EXPOSURE (แยก session — manual mode ทำภาพดำชั่วขณะ) ──
    cap = _open(index, backend)
    if cap is not None:
        base2 = _measure(cap)
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)   # manual (แบบ Windows)
        ae = round(cap.get(cv2.CAP_PROP_AUTO_EXPOSURE), 2)
        for _ in range(25):                          # warmup นานหลังสลับโหมด
            cap.read()
        time.sleep(0.6)
        print(f"\n  ── EXPOSURE (ตั้ง manual: AUTO_EXPOSURE=0.25 → get={ae}) ──")
        print(f"    baseline ความสว่าง = {base2}")
        rows = _sweep(cap, cv2.CAP_PROP_EXPOSURE, [-2, -4, -6, -8])
        brights = []
        for setv, got, b in rows:
            print(f"    set={setv:>3}  get={got:<8}  ความสว่าง={b}")
            if b is not None:
                brights.append(b)
        if len(brights) >= 2 and (max(brights) - min(brights)) >= DELTA:
            v["exposure"] = True
            print(f"    ✅ EXPOSURE คุมได้จริง (ช่วง {min(brights)}–{max(brights)})")
        else:
            print("    ❌ EXPOSURE ไม่มีผล (ภาพไม่เปลี่ยน — อาจดำเพราะ manual mode)")
        cap.release()

    return v


def main():
    ap = argparse.ArgumentParser(description="Test camera brightness/exposure controllability.")
    ap.add_argument("--index", type=int, default=DEFAULT_INDEX)
    args = ap.parse_args()

    print(f"กล้อง index={args.index}  ทดสอบที่ {W}x{H}")
    print("⚠️ ปิด app.py ก่อน + เล็งกล้องไปที่วัตถุที่มีแสง (ไม่ใช่ที่มืด)")

    summary = {}
    for name, backend in BACKENDS:
        try:
            summary[name] = test_backend(args.index, name, backend)
        except Exception as e:
            print(f"  [{name}] error: {e}")
            summary[name] = None

    print(f"\n{'#'*66}\n  สรุป: knob ไหนคุมได้ (ต่อ backend)\n{'#'*66}")
    best = None
    for name, v in summary.items():
        if not v:
            print(f"  {name:<8}: เปิด/ทดสอบไม่ได้")
            continue
        ok = [k.upper() for k, works in v.items() if works]
        print(f"  {name:<8}: {'  '.join(ok) if ok else '(ไม่มี knob ไหนคุมได้)'}")
        if ok and best is None:
            best = (name, ok)

    print()
    if best:
        print(f"  ✅ ทำ Tab ปรับความสว่างได้! เริ่มจาก backend '{best[0]}' knob: {', '.join(best[1])}")
        print("     → บอก Claude ว่า backend ไหน + knob ไหนใช้ได้ ให้สร้าง Tab ต่อ")
    else:
        print("  ❌ ทุก backend ไม่มี knob ไหนคุมได้เลย → ใช้ไฟส่องภายนอก หรือซอฟต์แวร์กล้องผู้ผลิต")
    print("#" * 66)


if __name__ == "__main__":
    main()
