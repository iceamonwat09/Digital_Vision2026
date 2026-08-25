"""
diagnose_igpu.py — "ทำไม iGPU ไม่ทำงาน / ทำไม ONNX วิ่งเสมอ" ตอบด้วยหลักฐานทุกมิติ

    py -3.9 diagnose_igpu.py                       # ตรวจทุก .pt ในโฟลเดอร์ weights
    py -3.9 diagnose_igpu.py --weights weights\\can_dent\\bestX.pt
    py -3.9 diagnose_igpu.py --fix                 # ซ่อม IR ที่พิสูจน์แล้วว่าเสีย
    py -3.9 diagnose_igpu.py --quick               # อ่านอย่างเดียว ไม่โหลด/ไม่ export
    py -3.9 diagnose_igpu.py --images sample_cans  # เทียบความแม่นกับ PyTorch ด้วย

ต่างจาก `diagnose_accel.py` อย่างไร
-----------------------------------
`diagnose_accel.py` ตอบว่า **ระบบตกไป backend ไหน** (มุมมองจาก `load_model`)
ไฟล์นี้ไล่ **ทีละชั้นของกองซ้อน** เพื่อชี้ว่า *ชั้นไหน* คือตัวขวาง โดยแยก
สิ่งที่เดิมปนกันอยู่ในความล้มเหลวเดียว:

    ① ค่าตั้ง            ระบบถูกสั่งให้ใช้ iGPU หรือเปล่า
    ② แพ็กเกจ            openvino/onnxruntime/ultralytics/torch รุ่นไหน
    ③ อุปกรณ์            เห็น GPU ไหม · **compile ของจริงลงไปได้ไหม**
    ④ ไฟล์ IR            โครงสร้างตรงกับ task ของ .pt ไหม (จำนวน output/ช่อง/ชื่อคลาส)
    ⑤ รัน IR ดิบ ๆ       ข้าม ultralytics ไปเรียก OpenVINO ตรง ๆ — แยก
                        "IR/ไดรเวอร์พัง" ออกจาก "ตัวห่อของ ultralytics พัง"
    ⑥ เส้นทางจริง        `YOLODetector.load_model()` ตัวเดียวกับ app.py + เวลา
    ⑦ ความแม่น           (ถ้าใส่ --images) เทียบกับ PyTorch ด้วยเกณฑ์เดียวกับ verify_onnx

⚠️ ต้นเหตุ 5 แบบที่หน้าตาเหมือนกันบนจอ (`inf` ช้า / แถบ perf ขึ้น `ONNX 🐢`)
   แต่ **แก้คนละวิธีคนละวัน**:
     ① ไม่ได้ตั้ง OPENVINO_DEVICE       → แก้ config.py
     ② ไม่ได้ติดตั้ง openvino            → pip install "openvino==2024.6.0"
     ③ ติดตั้งแล้วแต่ไม่เห็น GPU         → ไดรเวอร์ Intel Graphics / OpenCL
     ④ **ไฟล์ IR ของโมเดลตัวนั้นเสีย**   → ลบแล้ว export ใหม่ (ข้อนี้เกิดกับ bestX.pt)
     ⑤ IR ดีแต่ compile บน GPU ไม่ผ่าน  → รุ่น openvino/ไดรเวอร์ไม่เข้ากัน

exit code: 0 = ทุกโมเดลได้ backend ที่เร็วที่สุดตามที่ตั้งไว้ · 1 = มีตัวที่ตกไปตัวช้ากว่า
           · 2 = รันไม่ได้
"""

import argparse
import os
import shutil
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    import config
except Exception as e:                                     # pragma: no cover
    print("รัน diagnose_igpu.py จากโฟลเดอร์โปรเจกต์ (import config ไม่ได้: %s)" % e)
    sys.exit(2)


# ── ยูทิลิตี้การพิมพ์ ────────────────────────────────────────────────────────
def head(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


def sub(t):
    print("\n  ── %s " % t + "─" * max(0, 66 - len(t)))


def kv(k, v):
    txt = str(v)
    if len(txt) > 160:                       # รายชื่อคลาสยาว ๆ ทำให้รายงานอ่านไม่ออก
        txt = txt[:157] + "…"
    print("  %-28s %s" % (k, txt))


def ok_mark(b):
    return "✅" if b else "❌"


def _detector_cls():
    try:
        from yolo_detector import YOLODetector
        return YOLODetector
    except Exception as e:
        print("\n  ❌ import yolo_detector ไม่ได้ (%s)" % e)
        print("     แพ็กเกจหลัก (ultralytics/torch/cv2) ยังไม่ครบ — ระบบจริงก็จะรันไม่ได้")
        return None


# ─────────────────────────────────────────────────────── ① ค่าตั้งของระบบ
def step_config():
    head("① ค่าตั้งที่ระบบใช้ตัดสิน (config.py)")
    dev = getattr(config, "OPENVINO_DEVICE", None)
    kv("CONFIG_VERSION", getattr(config, "CONFIG_VERSION", "?"))
    kv("OPENVINO_DEVICE", repr(dev))
    kv("USE_OPENVINO (legacy CPU)", getattr(config, "USE_OPENVINO", False))
    kv("USE_ONNX", getattr(config, "USE_ONNX", False))
    kv("OPENVINO_VALIDATE_IR", getattr(config, "OPENVINO_VALIDATE_IR", True))
    kv("OPENVINO_AUTO_REPAIR", getattr(config, "OPENVINO_AUTO_REPAIR", True))
    kv("imgsz live / snapshot", "%s / %s" % (getattr(config, "YOLO_IMGSZ", "?"),
                                             getattr(config, "SNAPSHOT_IMGSZ", "?")))
    print()
    print("  ลำดับที่ระบบจะไล่ลอง: " + " → ".join(
        ([("OpenVINO@%s" % dev)] if dev else [])
        + (["ONNX"] if getattr(config, "USE_ONNX", False) else [])
        + (["OpenVINO(default)"] if getattr(config, "USE_OPENVINO", False) else [])
        + ["PyTorch"]))
    if not dev:
        print("\n  ⚠️ ต้นเหตุ ① — **ระบบไม่ได้ถูกสั่งให้ใช้ iGPU เลย**")
        print("     ตั้ง OPENVINO_DEVICE = \"intel:gpu\" ใน config.py แล้วรีสตาร์ต")
    return dev


# ──────────────────────────────────────────────── ② แพ็กเกจที่ติดตั้งจริง
def step_packages():
    head("② แพ็กเกจบนเครื่องนี้ (interpreter ที่กำลังรันสคริปต์นี้)")
    kv("python", "%s  (%s)" % (sys.version.split()[0], sys.executable))
    info = {}
    try:
        import openvino as ov
        info["ov"] = getattr(ov, "__version__", "?")
        kv("openvino", info["ov"])
        if not str(info["ov"]).startswith("2024.6"):
            print("     ⚠️ Python 3.9 ควรใช้ **2024.6.0** — รุ่นสุดท้ายที่มี wheel cp39")
            print("        และอยู่ในช่วงที่ exporter ของ ultralytics รองรับ. รุ่น 2025.x")
            print("        เคย export/โหลดสำเร็จแต่ **ตรวจไม่เจออะไรเลยแบบเงียบ ๆ**")
    except Exception as e:
        kv("openvino", "❌ import ไม่ได้ (%s)" % e)
    for name, label in (("onnxruntime", "onnxruntime"), ("ultralytics", "ultralytics"),
                        ("torch", "torch"), ("onnx", "onnx"), ("onnxslim", "onnxslim")):
        try:
            m = __import__(name)
            kv(label, getattr(m, "__version__", "?"))
            if name == "onnxruntime":
                kv("  └ providers", list(m.get_available_providers()))
        except Exception as e:
            kv(label, "❌ (%s)" % e)
    return info


# ───────────────────────────────────────────── ③ อุปกรณ์ + compile ของจริง
def step_devices(dev):
    """
    เห็นชื่ออุปกรณ์ ≠ ใช้อุปกรณ์ได้.

    ``available_devices`` อ่านจากรายชื่อปลั๊กอิน — เครื่องที่ไดรเวอร์ครึ่ง ๆ กลาง ๆ
    ยัง "เห็น" GPU ได้ แล้วไปพังตอน compile. จึง **compile โมเดลจิ๋วลงไปจริง**
    เพื่อแยก ③ (ไดรเวอร์) ออกจาก ④ (ไฟล์ IR ของโมเดล)
    """
    head("③ อุปกรณ์ที่ OpenVINO มองเห็น และ compile ลงไปได้จริงไหม")
    try:
        import openvino as ov
    except Exception as e:
        kv("ผลตรวจ", "❌ ไม่มี openvino — ข้ามข้อนี้ (%s)" % e)
        return False, "ไม่ได้ติดตั้ง openvino"
    try:
        core = ov.Core()
        avail = list(core.available_devices)
    except Exception as e:
        kv("available_devices", "❌ อ่านไม่ได้ (%s)" % e)
        return False, "อ่านรายชื่ออุปกรณ์ไม่ได้ (%s)" % e
    kv("available_devices", avail or "ไม่มีเลย")
    for d in avail:
        for prop in ("FULL_DEVICE_NAME", "OPTIMIZATION_CAPABILITIES", "DEVICE_ARCHITECTURE"):
            try:
                kv("  %s · %s" % (d, prop), core.get_property(d, prop))
            except Exception:
                pass

    want = dev.split(":", 1)[1].upper() if dev and ":" in dev else (dev or "").upper()
    if not want:
        return False, "ไม่ได้ระบุอุปกรณ์"
    seen = [d for d in avail if d == want or d.startswith(want + ".")]
    print()
    kv("ต้องการอุปกรณ์", want)
    kv("เห็นในเครื่องไหม", "%s %s" % (ok_mark(bool(seen)), seen or "ไม่เห็น"))
    if not seen:
        print("\n  ⚠️ ต้นเหตุ ③ — แพ็กเกจมีแต่ **มองไม่เห็น %s**" % want)
        print("     เป็นเรื่องไดรเวอร์ Intel Graphics / OpenCL ของเครื่อง ไม่ใช่เรื่องแพ็กเกจ")
        print("     · อัปเดตไดรเวอร์ Intel Iris Xe จากเว็บ Intel")
        print("     · เช็คว่า iGPU ไม่ถูกปิดใน BIOS / Device Manager")
        return False, "ไม่เห็นอุปกรณ์ %s (เห็นแค่ %s)" % (want, avail)

    # compile โมเดลจิ๋ว (1 conv) ลง GPU จริง — ตัดเรื่องไดรเวอร์ให้ขาด
    try:
        import openvino.runtime.opset13 as opset
        p = opset.parameter([1, 3, 32, 32], ov.Type.f32, name="x")
        w = opset.constant(np.ones((4, 3, 3, 3), dtype=np.float32))
        y = opset.convolution(p, w, [1, 1], [1, 1], [1, 1], [1, 1])
        tiny = ov.Model([y], [p], "tiny")
        t0 = time.time()
        cm = core.compile_model(tiny, want)
        out = cm(np.zeros((1, 3, 32, 32), dtype=np.float32))
        kv("compile+run โมเดลจิ๋ว", "✅ ผ่าน (%.2f วิ · %d output)"
           % (time.time() - t0, len(out)))
        try:
            kv("  └ EXECUTION_DEVICES", cm.get_property("EXECUTION_DEVICES"))
        except Exception:
            pass
        return True, "อุปกรณ์ %s ใช้ได้จริง" % want
    except Exception as e:
        kv("compile+run โมเดลจิ๋ว", "❌ ล้มเหลว (%s)" % e)
        print("\n  ⚠️ ต้นเหตุ ⑤ — เห็นอุปกรณ์แต่ compile ไม่ผ่าน")
        print("     ⇒ รุ่น openvino กับไดรเวอร์ไม่เข้ากัน (ไม่ใช่เรื่องไฟล์ IR ของโมเดล)")
        return False, "compile บน %s ไม่ผ่าน (%s)" % (want, e)


# ───────────────────────────────────────────────── ④ ไฟล์ IR ของแต่ละโมเดล
def _task_from_sidecar(pt_path):
    """
    task จาก sidecar ที่ระบบจดไว้ — ใช้ในโหมด --quick ที่ห้ามโหลด .pt.

    ⚠️ sidecar เป็นของที่ **จดไว้ตอนก่อน** ⇒ ถ้าไฟล์ .pt ถูกเปลี่ยนตัว
    มันจะบอก task เก่า. โหมดปกติจึงอ่านจาก .pt เสมอ (แหล่งความจริง)
    """
    ov_dir = pt_path[:-3] + "_openvino_model"
    for side in (ov_dir + ".task", pt_path[:-3] + ".onnx.task"):
        try:
            with open(side, "r", encoding="utf-8") as f:
                t = f.read().strip()
            if t:
                return t
        except Exception:
            continue
    return None


def _pt_spec(pt_path):
    """task / nc / nm / ชื่อคลาส จาก **ตัว .pt เอง** (แหล่งความจริง)."""
    try:
        from ultralytics import YOLO
        y = YOLO(pt_path)
        h = y.model.model[-1]
        return {"task": y.task, "nc": getattr(h, "nc", None),
                "nm": getattr(h, "nm", None),
                "names": list(y.names.values())}
    except Exception as e:
        return {"task": None, "nc": None, "nm": None, "names": None, "error": str(e)}


def _ir_info(xml):
    """โครงสร้างของ IR: input · จำนวน/รูปร่างของ output — โดยไม่ต้องมีโมเดล."""
    try:
        import openvino as ov
        m = ov.Core().read_model(xml)
    except Exception as e:
        return {"error": str(e)}
    outs = []
    for o in m.outputs:
        try:
            ps = o.get_partial_shape()
            dims = []
            for i in range(len(ps)):
                d = ps[i]
                dims.append(str(d.get_length()) if d.is_static else "?")
            outs.append(dims)
        except Exception:
            outs.append(["?"])
    ins = []
    for i in m.inputs:
        try:
            ins.append(str(i.get_partial_shape()))
        except Exception:
            ins.append("?")
    return {"inputs": ins, "outputs": outs}


def _metadata(ov_dir):
    f = os.path.join(ov_dir, "metadata.yaml")
    if not os.path.exists(f):
        return None
    try:
        import yaml
        with open(f, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except Exception as e:
        return {"_error": str(e)}


def step_model_files(D, pt_path, spec, do_fix):
    """คืน (ir_ok, reason) — และซ่อมให้เมื่อ --fix."""
    ov_dir, xml = D._ov_paths(pt_path)
    onnx_path = pt_path[:-3] + ".onnx"
    digest = D._weights_hash(pt_path)
    det = D.__new__(D)

    kv(".pt", "%.1f MB · แก้ไข %s" % (
        os.path.getsize(pt_path) / 1e6,
        time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(pt_path)))))
    kv("sha1 ของเนื้อ .pt", (digest or "อ่านไม่ได้")[:16] + "…")
    kv("task (อ่านจาก .pt)", spec.get("task") or "อ่านไม่ได้ (%s)" % spec.get("error", "?"))
    kv("คลาส", spec.get("names"))
    kv("nc / nm", "%s / %s" % (spec.get("nc"), spec.get("nm")))

    for label, path in (("ONNX", onnx_path), ("OpenVINO IR", xml)):
        if not os.path.exists(path):
            kv(label, "— ยังไม่มี (จะ export ตอนเริ่มระบบ)")
            continue
        try:
            with open(path + ".src", "r", encoding="utf-8") as f:
                mark = "✅ ลายนิ้วมือตรงกับ .pt" if f.read().strip() == digest \
                       else "⚠️ ลายนิ้วมือไม่ตรง → จะ export ใหม่"
        except Exception:
            mark = "ยังไม่มี sidecar .src"
        kv(label, "%s · %s · %s" % (
            time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path))),
            mark,
            "จะ export ใหม่" if det._export_is_stale(pt_path, path) else "ใช้ของเดิมได้"))

    seen, tries, why_bad = det._read_bad_marker(ov_dir)
    if seen:
        kv("เครื่องหมาย .badir", "ลองซ่อมไปแล้ว %d/2 ครั้ง · %s" % (tries, why_bad))

    if not os.path.exists(xml):
        return False, "ยังไม่มีไฟล์ IR"

    # ── โครงสร้าง IR เทียบกับ task ของ .pt ────────────────────────────────
    info = _ir_info(xml)
    if "error" in info:
        kv("โครงสร้าง IR", "❌ อ่านไม่ได้ (%s)" % info["error"])
        return False, "อ่าน IR ไม่ได้ (%s)" % info["error"]
    kv("IR input", info["inputs"])
    kv("IR outputs", "%d ตัว: %s" % (len(info["outputs"]),
                                     " · ".join("(" + ",".join(o) + ")" for o in info["outputs"])))

    from yolo_detector import _IR_OUTPUTS_BY_TASK
    task = spec.get("task")
    want_n = _IR_OUTPUTS_BY_TASK.get(task or "")
    problems = []
    if want_n is not None:
        good = len(info["outputs"]) == want_n
        kv("จำนวน output ที่ต้องมี", "%d (task=%s) → %s" % (want_n, task, ok_mark(good)))
        if not good:
            problems.append("task=%s ต้องมี %d output แต่ IR มี %d"
                            % (task, want_n, len(info["outputs"])))

    # ช่องของ output กล่อง = 4 + nc (+ nm ถ้าเป็น segment) — จับ "IR ของโมเดลอื่น"
    nc, nm = spec.get("nc"), spec.get("nm")
    if nc is not None and info["outputs"]:
        want_ch = 4 + int(nc) + (int(nm) if task == "segment" and nm else 0)
        got = info["outputs"][0][1] if len(info["outputs"][0]) > 1 else "?"
        good = str(got) == str(want_ch)
        kv("ช่องของ output กล่อง", "ได้ %s · ต้องเป็น %d → %s" % (got, want_ch, ok_mark(good)))
        if not good and got != "?":
            problems.append("ช่องของ output กล่อง = %s แต่โมเดลนี้ต้องเป็น %d "
                            "⇒ IR **ไม่ใช่ของโมเดลตัวนี้**" % (got, want_ch))

    md = _metadata(ov_dir)
    if md is None:
        kv("metadata.yaml", "❌ ไม่มี — ชื่อคลาสจะกลายเป็น class0/class1 แบบเงียบ ๆ")
        problems.append("ไม่มี metadata.yaml (ชื่อคลาสหาย)")
    else:
        names = md.get("names")
        names = list(names.values()) if isinstance(names, dict) else names
        same = spec.get("names") is None or names == spec.get("names")
        kv("metadata.yaml", "task=%s · names=%s → %s" % (md.get("task"), names, ok_mark(same)))
        if not same:
            problems.append("ชื่อคลาสใน metadata.yaml (%s) ไม่ตรงกับ .pt (%s)"
                            % (names, spec.get("names")))

    if not problems:
        return True, "IR โครงสร้างถูกต้อง"

    print()
    for pr in problems:
        print("     ❌ %s" % pr)
    print("\n  ⚠️ ต้นเหตุ ④ — **ไฟล์ IR ของโมเดลตัวนี้เสีย** (ไม่ใช่ไดรเวอร์ ไม่ใช่แพ็กเกจ)")
    print("     OpenVINO จะ **โหลดผ่าน** แล้วไปพังตอนตรวจภาพจริง ⇒ ระบบตกไป ONNX")

    if do_fix:
        print("\n  🔧 --fix: ลบโฟลเดอร์ IR แล้ว export ใหม่…")
        shutil.rmtree(ov_dir, ignore_errors=True)
        det._clear_bad_marker(ov_dir)
        try:
            from ultralytics import YOLO
            YOLO(pt_path).export(format="openvino", dynamic=True, half=False)
        except Exception as e:
            print("     ❌ export ใหม่ล้มเหลว (%s)" % e)
        ok, why = det._validate_ir(pt_path, ov_dir)
        print("     export ใหม่ → %s %s" % (ok_mark(ok), why))
        if not ok:
            print("  🔧 --fix: ลองเส้นทางสำรอง ONNX → IR…")
            ok, why = det._repair_ir_from_onnx(pt_path, ov_dir)
            print("     ONNX → IR → %s %s" % (ok_mark(ok), why))
        if ok:
            det._remember_hash(xml, digest)
            print("     ⚠️ **ต้องยืนยันความแม่นก่อนใช้งานจริง**:")
            print("        py -3.9 verify_openvino.py --weights %s --images <ภาพ NG จริง>"
                  % pt_path)
        return ok, why
    print("     แก้ด้วย: py -3.9 diagnose_igpu.py --weights %s --fix" % pt_path)
    return False, " · ".join(problems)


# ────────────────────────────────────────────── ⑤ รัน IR ดิบ ๆ (ข้าม ultralytics)
def step_raw_ir(pt_path, dev):
    """
    เรียก OpenVINO ตรง ๆ กับ IR — ไม่ผ่าน ultralytics เลย.

    แยกสองอย่างที่เดิมปนกัน: "IR/ไดรเวอร์รันไม่ได้" กับ "ตัวห่อของ ultralytics
    ถอดผลไม่ถูก". ถ้าข้อนี้ผ่านแต่ ⑥ ไม่ผ่าน = ปัญหาอยู่ที่การถอดผล (task/หัวโมเดล)
    """
    D = _detector_cls()
    ov_dir, xml = D._ov_paths(pt_path)
    if not os.path.exists(xml):
        kv("รัน IR ดิบ", "— ไม่มีไฟล์ IR")
        return
    try:
        import openvino as ov
        core = ov.Core()
        model = core.read_model(xml)
    except Exception as e:
        kv("รัน IR ดิบ", "❌ อ่าน IR ไม่ได้ (%s)" % e)
        return
    imgsz = int(getattr(config, "YOLO_IMGSZ", 480))
    x = np.zeros((1, 3, imgsz, imgsz), dtype=np.float32)
    targets = ["CPU"]
    want = dev.split(":", 1)[1].upper() if dev and ":" in dev else (dev or "").upper()
    if want and want not in targets:
        targets.insert(0, want)
    for d in targets:
        try:
            t0 = time.time()
            cm = core.compile_model(model, d)
            comp = time.time() - t0
            t0 = time.time()
            y = list(cm(x).values())
            kv("รัน IR ดิบ บน %s" % d,
               "✅ %d output %s · compile %.1f วิ · infer %.0f ms"
               % (len(y), [tuple(t.shape) for t in y], comp, (time.time() - t0) * 1000))
        except Exception as e:
            kv("รัน IR ดิบ บน %s" % d, "❌ %s" % e)


# ───────────────────────────────────────────────── ⑥ เส้นทางจริงของ app.py
def step_real_path(pt_path, rounds):
    D = _detector_cls()
    if D is None:
        return 2, None
    det = D(model_path=pt_path)
    t0 = time.time()
    loaded = det.load_model()
    kv("load_model()", "%s · %.1f วิ" % ("สำเร็จ" if loaded else "❌ ล้มเหลว", time.time() - t0))
    if not loaded:
        return 2, det
    kv("backend ที่ใช้จริง", det.backend_label)
    kv("ตกไปตัวที่ช้ากว่าไหม", "🐢 ใช่" if det.backend_downgraded else "✅ ไม่")
    if det.backend_note:
        kv("เหตุผลที่ระบบบันทึก", det.backend_note)
    for line in getattr(det, "_accel_skips", []):
        print("     ข้าม → %s" % line)
    try:
        kv("คลาสที่โหลดได้", list(det.model.names.values()))
    except Exception:
        pass
    for imgsz in (int(getattr(config, "YOLO_IMGSZ", 480)),
                  int(getattr(config, "SNAPSHOT_IMGSZ", 1280))):
        frame = (np.random.rand(imgsz, imgsz, 3) * 255).astype(np.uint8)
        try:
            det.detect(frame, imgsz=imgsz)
            ts = []
            for _ in range(rounds):
                t = time.time()
                det.detect(frame, imgsz=imgsz)
                ts.append((time.time() - t) * 1000.0)
            ts.sort()
            kv("inference @ imgsz %d" % imgsz,
               "กลาง %.0f ms (ต่ำ %.0f · สูง %.0f) = %.1f เฟรม/วิ"
               % (ts[len(ts) // 2], ts[0], ts[-1], 1000.0 / max(ts[len(ts) // 2], 1e-6)))
        except Exception as e:
            kv("inference @ imgsz %d" % imgsz, "❌ %s" % e)
    return (1 if det.backend_downgraded else 0), det


# ──────────────────────────────────────────────────── ⑦ ความแม่น (ออปชัน)
def step_accuracy(pt_path, det, images):
    """เทียบผลตรวจของ backend ที่ได้จริง กับ PyTorch ด้วยเกณฑ์เดียวกับ verify_onnx."""
    try:
        from verify_onnx import (IOU_MATCH, MIN_MEAN_IOU, MAX_CONF_DIFF,  # noqa: F401
                                 _extract, _match, _load_images)
        from ultralytics import YOLO
    except Exception as e:
        kv("ความแม่น", "ข้าม (%s)" % e)
        return None
    paths = _load_images(images)
    if not paths:
        kv("ความแม่น", "ไม่พบภาพใน %s" % images)
        return None
    ref = YOLO(pt_path)
    imgsz = int(getattr(config, "YOLO_IMGSZ", 480))
    ious, dconf, drift = [], 0.0, 0
    for p in paths:
        a = _extract(ref(p, imgsz=imgsz, verbose=False)[0])
        b = _extract(det.model(p, imgsz=imgsz, verbose=False,
                               **({"device": det.infer_device} if det.infer_device else {}))[0])
        drift = max(drift, abs(len(a) - len(b)))
        for iou, cd in _match(a, b):
            ious.append(iou)
            dconf = max(dconf, cd)
    mean_iou = sum(ious) / len(ious) if ious else 0.0
    good = ious and mean_iou >= MIN_MEAN_IOU and dconf <= MAX_CONF_DIFF
    kv("ความแม่น vs PyTorch",
       "%s ภาพ %d · mean IoU %.4f (≥%.2f) · Δconf %.4f (≤%.2f) · กล่องต่างสูงสุด %d"
       % (ok_mark(bool(good)), len(paths), mean_iou, MIN_MEAN_IOU, dconf, MAX_CONF_DIFF, drift))
    return bool(good)


# ────────────────────────────────────────────────────────────────── สรุป
def verdict(rows, dev, dev_ok, dev_why):
    head("⑧ สรุป — ต้องแก้อะไร")
    if not dev:
        print("  ▶ ต้นเหตุ ①: ยังไม่ได้ตั้ง OPENVINO_DEVICE ⇒ ระบบไม่เคยพยายามใช้ iGPU เลย")
        print('     แก้: OPENVINO_DEVICE = "intel:gpu" ใน config.py แล้วรีสตาร์ต app.py')
        return
    if not dev_ok:
        print("  ▶ อุปกรณ์ยังใช้ไม่ได้: %s" % dev_why)
        print("     ⇒ **ทุกโมเดลจะตกไป ONNX** จนกว่าจะแก้ข้อนี้ — แก้ที่เครื่อง ไม่ใช่ที่โมเดล")
        return
    print("  ✅ ตัว iGPU เองใช้ได้ (compile ของจริงผ่าน) ⇒ ถ้ายังมีโมเดลที่ตกไป ONNX")
    print("     แปลว่าเป็นเรื่อง **เฉพาะไฟล์ IR ของโมเดลตัวนั้น** ไม่ใช่ทั้งเครื่อง\n")
    for pt, code, det, ir_ok in rows:
        name = os.path.basename(pt)
        if code == 2 or det is None:
            print("  ❌ %-14s โหลดโมเดลไม่สำเร็จ" % name)
        elif code == 0:
            print("  ✅ %-14s ใช้ **%s** ตามที่ตั้งไว้" % (name, det.backend_label))
        else:
            print("  🐢 %-14s ใช้ **%s** (ช้ากว่าที่ตั้งไว้) — %s"
                  % (name, det.backend_label, det.backend_note or "ไม่ทราบสาเหตุ"))
            if not ir_ok:
                print("       ▶ IR ของโมเดลนี้เสีย — แก้:")
                print("         py -3.9 diagnose_igpu.py --weights %s --fix" % pt)
                print("         py -3.9 verify_openvino.py --weights %s --images <ภาพ NG จริง>" % pt)
    print("\n  ⚠️ ทุกครั้งที่ IR ถูกสร้างใหม่ **ต้องรัน verify_openvino.py ก่อนใช้งานจริง** —")
    print("     'รันได้' ไม่ได้แปลว่า 'ตรวจถูก' (บทเรียน openvino 2025.3.0)")


def _weight_files(explicit):
    if explicit:
        return [w if os.path.isabs(w) else os.path.join(ROOT, w) for w in explicit]
    base = getattr(config, "MODEL_PATH", "")
    if base and not os.path.isabs(base):
        base = os.path.join(ROOT, base)
    d = os.path.dirname(base)
    if not os.path.isdir(d):
        return [base] if base else []
    return sorted(os.path.join(d, f) for f in os.listdir(d)
                  if f.lower().endswith(".pt")) or ([base] if base else [])


def main():
    ap = argparse.ArgumentParser(description="ทำไม iGPU ไม่ทำงาน — ไล่ทีละชั้นของกองซ้อน")
    ap.add_argument("--weights", action="append", default=None,
                    help="ไฟล์ .pt (ระบุซ้ำได้; ไม่ระบุ = ทุกตัวในโฟลเดอร์ weights)")
    ap.add_argument("--quick", action="store_true", help="อ่านอย่างเดียว ไม่โหลด/ไม่ export")
    ap.add_argument("--fix", action="store_true", help="ซ่อม IR ที่พบว่าเสีย (ลบ + export ใหม่)")
    ap.add_argument("--images", default=None, help="โฟลเดอร์ภาพจริงสำหรับเทียบความแม่น")
    ap.add_argument("--rounds", type=int, default=8)
    a = ap.parse_args()

    if a.fix and a.quick:
        print("--fix ใช้กับ --quick ไม่ได้ (--quick แปลว่าห้ามเขียนอะไรเลย) — เอา --quick ออก")
        return 2
    pts = _weight_files(a.weights)
    if not pts:
        print("ไม่พบไฟล์ .pt ให้ตรวจ (ระบุด้วย --weights)")
        return 2

    dev = step_config()
    step_packages()
    dev_ok, dev_why = step_devices(dev)

    D = _detector_cls()
    if D is None:
        return 2

    head("④ ไฟล์ IR ของแต่ละโมเดล — ตรงกับ task ของ .pt ไหม")
    print("  ⚠️ โมเดลที่ระบบใช้จริง = ตัวที่เลือกบนหน้าเว็บ ไม่ใช่ค่าใน config")
    print("     ⇒ ตรวจ **ทุกตัว** เพราะแต่ละ .pt มี IR ของตัวเองแยกกัน")
    ir_state = {}
    for pt in pts:
        sub(os.path.basename(pt))
        if not os.path.exists(pt):
            print("  ❌ ไม่พบไฟล์ %s" % pt)
            ir_state[pt] = False
            continue
        spec = ({"task": _task_from_sidecar(pt), "nc": None, "nm": None, "names": None}
                if a.quick else _pt_spec(pt))
        if a.quick and not spec["task"]:
            print("  (โหมด --quick: ไม่รู้ task เพราะยังไม่มี sidecar — รันโดยไม่ใส่ --quick"
                  " เพื่อให้อ่านจาก .pt ตรง ๆ)")
        ir_state[pt] = step_model_files(D, pt, spec, a.fix)[0]

    if a.quick:
        head("⑧ สรุป")
        print("  (โหมด --quick: ยังไม่ได้โหลดโมเดลจริง — รันซ้ำโดยไม่ใส่ --quick")
        print("   เพื่อดูว่าสุดท้ายระบบใช้ backend ไหนและเร็วแค่ไหน)")
        return 0

    head("⑤ รัน IR ตรง ๆ ผ่าน OpenVINO (ข้าม ultralytics)")
    for pt in pts:
        if os.path.exists(pt):
            sub(os.path.basename(pt))
            step_raw_ir(pt, dev)

    head("⑥ โหลดด้วยเส้นทางเดียวกับ app.py แล้ววัดเวลาจริง")
    rows = []
    for pt in pts:
        if not os.path.exists(pt):
            continue
        sub(os.path.basename(pt))
        code, det = step_real_path(pt, max(1, a.rounds))
        if a.images and det is not None and code != 2:
            step_accuracy(pt, det, a.images)
        rows.append((pt, code, det, ir_state.get(pt, False)))

    verdict(rows, dev, dev_ok, dev_why)
    return max([r[1] for r in rows] or [2])


if __name__ == "__main__":
    sys.exit(main())
