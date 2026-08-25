"""
diagnose_accel.py — ตอบคำถาม "ทำไมระบบไม่ใช้ iGPU" ด้วยหลักฐาน ไม่ใช่การเดา

    py -3.9 diagnose_accel.py                       # โมเดล default (config.MODEL_PATH)
    py -3.9 diagnose_accel.py --weights weights\\can_dent\\bestX.pt
    py -3.9 diagnose_accel.py --quick               # ไม่โหลดโมเดล/ไม่ export (เร็ว, อ่านอย่างเดียว)

ทำไมต้องมีไฟล์นี้
-----------------
เส้นทางเลือก backend มี 4 ชั้น (OpenVINO@iGPU → ONNX → OpenVINO default →
PyTorch) และ **ทุกชั้นถอยลงชั้นถัดไปอย่างเงียบ ๆ ได้** — ผู้ใช้จะเห็นแค่
"ระบบช้าลง" โดยไม่รู้ว่าตกไปชั้นไหนและเพราะอะไร. เกิดจริงบนสถานี 25 ส.ค. 2026
(`inf` 45-50 ms → 373 ms). สคริปต์นี้พิมพ์ **ทุกจุดตัดสิน** ของเส้นทางจริง
แล้วสรุปว่า "ต้องแก้อะไร" — ไม่ใช่ "น่าจะเป็นที่..."

⚠️ 3 ต้นเหตุที่หน้าตาเหมือนกันบนจอ แต่ **แก้คนละวิธี**:
   ① ไม่ได้ติดตั้ง openvino            → py -3.9 -m pip install "openvino==2024.6.0"
   ② ติดตั้งแล้วแต่ไม่เห็น GPU         → ไดรเวอร์ Intel Graphics / OpenCL ของเครื่อง
   ③ เห็น GPU แต่โหลด/smoke test ล้ม   → รุ่น openvino ไม่เข้ากับ py3.9/ultralytics

exit code: 0 = ใช้ backend ที่เร็วที่สุดตามที่ตั้งไว้ · 1 = ตกไปตัวที่ช้ากว่า · 2 = รันไม่ได้
"""

import argparse
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    import config
except Exception as e:                                    # pragma: no cover
    print("รัน diagnose_accel.py จากโฟลเดอร์โปรเจกต์ (import config ไม่ได้: %s)" % e)
    sys.exit(2)


def _detector_cls():
    """คลาสตัวจริง — คืน None พร้อมบอกเหตุผลเมื่อ import ไม่ได้ (แทนที่จะพ่น traceback)."""
    try:
        from yolo_detector import YOLODetector
        return YOLODetector
    except Exception as e:
        print("\n  ❌ import yolo_detector ไม่ได้ (%s)" % e)
        print("     แปลว่าแพ็กเกจหลัก (ultralytics/torch) ยังไม่ครบบนเครื่องนี้ —")
        print("     ระบบจริงก็จะรันไม่ได้เช่นกัน. แก้ตรงนั้นก่อน")
        return None


def head(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def kv(k, v):
    print("  %-26s %s" % (k, v))


# ---------------------------------------------------------------- ① ค่าตั้ง
def show_config():
    head("① ค่าตั้งที่ระบบใช้ตัดสิน (config.py)")
    kv("CONFIG_VERSION", getattr(config, "CONFIG_VERSION", "?"))
    kv("OPENVINO_DEVICE", repr(getattr(config, "OPENVINO_DEVICE", None)))
    kv("USE_OPENVINO (legacy)", getattr(config, "USE_OPENVINO", False))
    kv("USE_ONNX", getattr(config, "USE_ONNX", False))
    kv("YOLO_IMGSZ / SNAPSHOT_IMGSZ",
       "%s / %s" % (getattr(config, "YOLO_IMGSZ", "?"),
                    getattr(config, "SNAPSHOT_IMGSZ", "?")))
    dev = getattr(config, "OPENVINO_DEVICE", None)
    if not dev and not getattr(config, "USE_OPENVINO", False):
        print("\n  ⚠️ OPENVINO_DEVICE = None และ USE_OPENVINO = False")
        print("     ⇒ **ระบบไม่ได้ถูกสั่งให้ใช้ iGPU เลย** — นี่ไม่ใช่ความล้มเหลว")
        print("     ตั้ง OPENVINO_DEVICE = \"intel:gpu\" ใน config.py แล้วรีสตาร์ต")
    return dev


# ------------------------------------------------------------ ② แพ็กเกจจริง
def show_packages(dev):
    head("② แพ็กเกจที่ติดตั้งอยู่จริงบนเครื่องนี้")
    kv("python", sys.version.split()[0])
    ov_ok = False
    try:
        import openvino as ov
        ver = getattr(ov, "__version__", "?")
        kv("openvino", ver)
        if not str(ver).startswith("2024.6"):
            print("     ⚠️ py3.9 ควรใช้ **2024.6.0** (รุ่นสุดท้ายที่มี wheel cp39 และ")
            print("        อยู่ในช่วงที่ exporter ของ ultralytics รองรับ). รุ่น 2025.x")
            print("        เคย export/โหลดสำเร็จแต่ **ตรวจไม่เจออะไรเลยแบบเงียบ ๆ**")
        try:
            core = ov.Core()
            avail = list(core.available_devices)
            kv("available_devices", avail or "ไม่มีเลย")
            for d in avail:
                try:
                    kv("  └ %s" % d, core.get_property(d, "FULL_DEVICE_NAME"))
                except Exception as e:
                    kv("  └ %s" % d, "(อ่านชื่อไม่ได้: %s)" % e)
            ov_ok = True
        except Exception as e:
            kv("available_devices", "อ่านไม่ได้ (%s)" % e)
    except Exception as e:
        kv("openvino", "❌ ไม่ได้ติดตั้ง / import ไม่ได้ (%s)" % e)

    try:
        import onnxruntime as ort
        kv("onnxruntime", ort.__version__)
        kv("  └ providers", list(ort.get_available_providers()))
    except Exception as e:
        kv("onnxruntime", "❌ (%s)" % e)
    for name in ("ultralytics", "torch"):
        try:
            m = __import__(name)
            kv(name, getattr(m, "__version__", "?"))
        except Exception as e:
            kv(name, "❌ (%s)" % e)

    if dev:
        D = _detector_cls()
        if D is None:
            return False
        ok, why = D._openvino_device_status(dev)
        print()
        kv("ด่านตรวจอุปกรณ์ของโค้ดจริง", "✅ ผ่าน" if ok else "❌ ไม่ผ่าน")
        print("     %s" % why)
        return ok
    return ov_ok


# ------------------------------------------------------- ③ ไฟล์ export บนดิสก์
def _is_stale(pt_path, export_path):
    """ถามตรรกะตัวจริงว่า 'ต้อง export ใหม่ไหม' โดยไม่ต้องโหลดโมเดล."""
    D = _detector_cls()
    if D is None:
        return None
    return D._export_is_stale(D.__new__(D), pt_path, export_path)


def show_exports(pt_path):
    head("③ ไฟล์ export บนดิสก์ (ตัวที่ทำให้ 'ต้อง export ใหม่ทุกครั้ง')")
    D = _detector_cls()
    if D is None:
        return
    if not os.path.exists(pt_path):
        print("  ❌ ไม่พบ %s" % pt_path)
        return
    kv(os.path.basename(pt_path), "%.1f MB · แก้ไขล่าสุด %s"
       % (os.path.getsize(pt_path) / 1e6,
          time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(pt_path)))))
    digest = D._weights_hash(pt_path)
    kv("sha1 ของ .pt", (digest or "อ่านไม่ได้")[:16] + "…")

    onnx_path = pt_path[:-3] + ".onnx"
    ov_dir = pt_path[:-3] + "_openvino_model"
    ov_xml = os.path.join(ov_dir, os.path.basename(pt_path)[:-3] + ".xml")
    for label, path in (("ONNX", onnx_path), ("OpenVINO IR", ov_xml)):
        if not os.path.exists(path):
            kv(label, "— ยังไม่มี (จะ export ตอนเริ่มระบบ)")
            continue
        side = path + ".src"
        try:
            with open(side, "r", encoding="utf-8") as f:
                same = f.read().strip() == digest
            mark = "✅ ตรงกับ .pt" if same else "⚠️ ลายนิ้วมือไม่ตรง → จะ export ใหม่"
        except Exception:
            mark = "ยังไม่มี sidecar .src (ระบบจะจดให้รอบหน้า)"
        stale = _is_stale(pt_path, path)
        kv(label, "%s · %s · %s" % (
            time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path))),
            mark, "— ตัดสินไม่ได้" if stale is None
            else ("จะ export ใหม่" if stale else "ใช้ของเดิมได้")))


# --------------------------------------------------------- ④ เส้นทางจริง + เวลา
def show_runtime(pt_path, rounds):
    head("④ โหลดโมเดลด้วยเส้นทางเดียวกับ app.py แล้ววัดเวลาจริง")
    print("  (ขั้นนี้อาจ export ใหม่ถ้าไฟล์ไม่ตรง — เหมือนตอนเปิด app.py ทุกประการ)\n")
    D = _detector_cls()
    if D is None:
        return 2, None
    det = D(model_path=pt_path)
    t0 = time.time()
    ok = det.load_model()
    kv("load_model()", "%s · ใช้เวลา %.1f วิ" % ("สำเร็จ" if ok else "❌ ล้มเหลว", time.time() - t0))
    if not ok:
        return 2, det
    kv("backend ที่ใช้จริง", det.backend_label)
    kv("ตกไปตัวที่ช้ากว่าไหม", "🐢 ใช่" if det.backend_downgraded else "✅ ไม่")
    if det.backend_note:
        kv("เหตุผล", det.backend_note)
    for line in getattr(det, "_accel_skips", []):
        print("     ข้าม → %s" % line)

    print()
    for imgsz in (int(getattr(config, "YOLO_IMGSZ", 480)),
                  int(getattr(config, "SNAPSHOT_IMGSZ", 1280))):
        frame = (np.random.rand(imgsz, imgsz, 3) * 255).astype(np.uint8)
        det.detect(frame, imgsz=imgsz)                       # warm-up
        ts = []
        for _ in range(rounds):
            t = time.time()
            det.detect(frame, imgsz=imgsz)
            ts.append((time.time() - t) * 1000.0)
        ts.sort()
        kv("inference @ imgsz %d" % imgsz,
           "กลาง %.0f ms (ต่ำสุด %.0f · สูงสุด %.0f) = %.1f เฟรม/วิ"
           % (ts[len(ts) // 2], ts[0], ts[-1], 1000.0 / max(ts[len(ts) // 2], 1e-6)))
    return (1 if det.backend_downgraded else 0), det


# ----------------------------------------------------------------- ⑤ สรุป
def verdict(code, det, dev):
    head("⑤ สรุป")
    if code == 2 or det is None:
        print("  ❌ โหลดโมเดลไม่สำเร็จ — ดูข้อความด้านบน")
        return
    if code == 0:
        print("  ✅ ใช้ **%s** ตามที่ตั้งไว้ — ไม่มีการถอยไป backend ที่ช้ากว่า"
              % det.backend_label)
        if dev:
            print("     ยืนยันความแม่นซ้ำได้ด้วย:")
            print("       py -3.9 verify_openvino.py --weights <.pt> --images <โฟลเดอร์ภาพจริง>")
        return
    note = (det.backend_note or "").lower()
    print("  🐢 ใช้ **%s** ซึ่งช้ากว่าที่ตั้งไว้" % det.backend_label)
    print("     เหตุผลที่ระบบบันทึกไว้: %s\n" % (det.backend_note or "ไม่ทราบ"))
    if "import ล้มเหลว" in det.backend_note or "ไม่ได้ติดตั้ง" in det.backend_note:
        print("  ▶ ต้นเหตุ ①  แพ็กเกจหาย — แก้:")
        print('       py -3.9 -m pip install "openvino==2024.6.0"')
        print("     (ห้ามใช้ 2025.x บน py3.9 — off-spec และเคยตรวจไม่เจอแบบเงียบ)")
    elif "ไม่เห็นอุปกรณ์" in det.backend_note:
        print("  ▶ ต้นเหตุ ②  แพ็กเกจมีแต่มองไม่เห็น iGPU — เป็นเรื่องของเครื่อง ไม่ใช่โค้ด:")
        print("       · อัปเดตไดรเวอร์ Intel Graphics (Iris Xe) จากเว็บ Intel")
        print("       · เช็คว่า iGPU ไม่ถูกปิดใน BIOS / Device Manager")
        print("       · ถ้าเสียบจอผ่านการ์ดแยก iGPU อาจถูกปิดไว้")
    elif "smoke" in note or "failed" in note:
        print("  ▶ ต้นเหตุ ③  เห็น GPU แต่ใช้งานจริงไม่ได้ (โหลด/smoke test ล้ม)")
        print("       รุ่น openvino ไม่เข้ากับ py3.9/ultralytics — ลง 2024.6.0 แล้วลบ")
        print("       โฟลเดอร์ *_openvino_model ทิ้งเพื่อบังคับ export ใหม่")
    else:
        print("  ▶ ดูบรรทัด 'ข้าม →' ในข้อ ④ ว่าชั้นไหนถูกข้ามเพราะอะไร")


def main():
    ap = argparse.ArgumentParser(description="ทำไมระบบไม่ใช้ iGPU — ไล่ทุกจุดตัดสิน")
    ap.add_argument("--weights", default=None, help="ไฟล์ .pt (ค่าเริ่มต้น = config.MODEL_PATH)")
    ap.add_argument("--quick", action="store_true", help="ไม่โหลดโมเดล/ไม่ export")
    ap.add_argument("--rounds", type=int, default=8, help="จำนวนครั้งที่วัดเวลา (ค่าเริ่มต้น 8)")
    a = ap.parse_args()

    pt = a.weights or getattr(config, "MODEL_PATH", "")
    if pt and not os.path.isabs(pt):
        pt = os.path.join(ROOT, pt)

    dev = show_config()
    show_packages(dev)
    show_exports(pt)
    if a.quick:
        head("⑤ สรุป")
        print("  (โหมด --quick: ยังไม่ได้โหลดโมเดลจริง — รันซ้ำโดยไม่ใส่ --quick")
        print("   เพื่อดูว่า **สุดท้ายแล้วระบบใช้ backend ไหน** และเร็วแค่ไหน)")
        return 0
    code, det = show_runtime(pt, max(1, a.rounds))
    verdict(code, det, dev)
    return code


if __name__ == "__main__":
    sys.exit(main())
