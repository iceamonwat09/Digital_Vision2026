# CLAUDE.md — บริบทสำคัญสำหรับ AI ที่ทำงานต่อกับโปรเจกต์นี้

ระบบตรวจตำหนิบรรจุภัณฑ์ (Can Dent / ฉลาก / Artwork) — Flask + YOLO + SQL Server
รันบน **เครื่องสถานี Windows + Python 3.9** ที่ต่อกล้อง USB จริง อ่านไฟล์นี้ก่อนแก้โค้ด
เพื่อไม่พลาดกับดักที่เคยเจอมาแล้ว.

---

## 🔴 กฎเหล็ก (ยึดตลอด)

1. **ห้ามกระทบโหมดอื่น 100%** — ทุกฟีเจอร์ใหม่ต้อง opt-in (flag default = พฤติกรรมเดิม) และ
   scope เฉพาะโหมดที่ตั้งใจ. โหมดหลัก: **Live USB / RTSP / STREAM / Snapshot / Label / Artwork**.
2. **ความแม่นของการตรวจ (QC) สำคัญที่สุด** — ห้ามแลกความแม่นเพื่อความเร็ว. การเร่งความเร็ว
   ต้องคงผลตรวจให้เท่าเดิม (พิสูจน์ด้วย `verify_onnx.py` ก่อนเปิดใช้).
3. **วางแผน + ให้ผู้ใช้ยืนยันก่อนลงมือ** งานที่มีผลต่อโครงสร้าง/พฤติกรรม.
4. **ตรวจสอบความถูกต้องหลังทำ อย่างเป็นกลาง** (ไม่เข้าข้างตัวเอง) — ไล่ edge case + fallback.
5. Deploy: ผู้ใช้ `git pull` แล้ว **`py -3.9 app.py`** บนสถานี. ยืนยันโค้ดใหม่รันจริงด้วย
   **`CONFIG_VERSION` บน footer** (Flask อ่าน config ตอน start เท่านั้น — ต้องปิด-เปิดใหม่).

---

## 🧠 โมเดล (สำคัญมาก — เคยพลาดตรงนี้)

- **`best.pt`** = YOLOv8 **detection** ธรรมดา (3M params). classes: `dented`, `good`. โหมด can_dent default.
- **`bestX.pt`** = **YOLOv8m-SEGMENTATION** (27M params!) — **นี่คือโมเดล production ที่ผู้ใช้ใช้จริง**.
  classes: `dent`, `can`. output = 2 tensor `((1,38,21504),(1,32,256,256))`. verdict NG/OK ใน
  `classify_frame_bestx`. `is_bestx_mode` = เช็คชื่อไฟล์ == "bestx.pt".
- คลาส **non-defect** = `{"good", "can"}` (`_NON_DEFECT_CLASSES` ใน app.py) = "กระป๋องทั้งใบ".
  ตอน NG กล่อง `can` ถูก **ซ่อนตอนแสดงผล** แต่ยังอยู่ใน raw detections (ใช้เช็ค "ครบใบ" ได้).

---

## ⚡ ONNX acceleration (เปิดใช้อยู่ — `USE_ONNX=True`)

- Export `.pt`→`.onnx` (FP32, `dynamic=True`, opset 17) ครั้งเดียว แล้วรันผ่าน onnxruntime (~2x เร็วขึ้น).
  ultralytics ถอดผล/NMS เอง → **ผลตรวจเท่า PyTorch เป๊ะ** (verify แล้ว IoU 1.0, Δconf 0.0).
- **⚠️ Python 3.9 pin ที่ต้องรู้:**
  - `onnxruntime==1.19.2` = wheel cp39 ตัวสุดท้ายบน Windows (1.20+ ตัด py39 ทิ้ง).
  - `onnx` pip เลือก cp39 ให้เอง (~1.16/1.17). `onnxslim` (py3.8+) แนะนำติดตั้ง.
  - ติดตั้ง: `py -3.9 -m pip install onnxruntime==1.19.2 onnxslim onnx`
- **⚠️ กับดัก segmentation:** export ONNX ทิ้ง task tag → `YOLO(onnx)` เดาเป็น `detect` → ถอด output
  ของ seg ผิด = **ไม่มีกรอบเลย**. แก้แล้วใน `yolo_detector._accel_task()`: อ่าน task จาก `.pt`
  แล้วโหลด `YOLO(onnx, task='segment')` (cache ใน `<onnx>.task` sidecar).
- **`verify_onnx.py`** = ตาข่ายนิรภัย. รันเทียบ `.pt` vs `.onnx` ให้ PASS ก่อนเปิด `USE_ONNX`.
  ต้องรันต่อโมเดล: `py -3.9 verify_onnx.py --weights weights\can_dent\bestX.pt --images <โฟลเดอร์>`
- **fallback หลายชั้นใน `load_model()`**: pkg หาย/export/load/smoke-test ล้มเหลว → กลับ PyTorch อัตโนมัติ.
  stale guard: `.pt` ใหม่กว่า `.onnx` → re-export.

## 🚫 OpenVINO — ปิดไว้ อย่าเปิด (`USE_OPENVINO=False`)

บน py3.9 ใช้ไม่ได้: OpenVINO 2025 + onnxruntime-openvino ต้องการ **Python ≥3.10**. เคยทำ
ตรวจไม่เจอทุกโหมดแบบเงียบๆ. iGPU (Iris Xe) ก็ติดล็อกนี้ — ถ้าจะไป iGPU ต้องอัปเกรด Python
ทั้งเครื่องก่อน (เป็นโปรเจกต์แยก เทสต์ใหม่ทุกโหมด).

---

## 📷 กล้อง & imgsz

- Live: 640x480 @ 30fps, `CAMERA_FOURCC=None` (YUY2 — "MJPG" ทำภาพแตกบน MSMF). กล้องตัน 720p ผ่าน MSMF.
- **imgsz: live=480, snapshot=1280. ⚠️ ห้ามต่ำกว่า 480** — dent เป็นฟีเจอร์เล็ก ลดเป็น 320 = ตรวจไม่เจอเลย.
- **Exposure** (`CAMERA_AUTO_EXPOSURE`/`CAMERA_EXPOSURE`, opt-in default None): แก้ motion blur
  บนสายพาน. เฉพาะกล้อง **live** (ส่งผ่าน ctor ที่ site สร้างกล้อง live เท่านั้น; snapshot/RTSP ไม่แตะ).
  best-effort + try/except (กล้อง USB หลายรุ่นไม่รับค่าผ่าน OpenCV — ดู log `camera reports=`).
- `Camera` class ใช้ร่วมทั้ง live+snapshot (แยกด้วย ctor params). RTSP → `_initialize_rtsp` (ไม่ทำ exposure).

---

## 🏗️ สถาปัตยกรรมสำคัญ (app.py)

- **Live USB/RTSP** = 2 thread: `capture_loop` (อ่านกล้อง → `latest_raw_frame`) +
  `inference_loop` (infer, นับ, log DB). `generate_frames` = MJPEG generator.
  - `LIVE_SMOOTH_VIDEO`: `False`=วาดกรอบบนเฟรมที่ infer จริง (กรอบเป๊ะ, ภาพตามอัตรา infer) /
    `True`=วาดกรอบล่าสุดบนเฟรมดิบล่าสุด (ภาพลื่น, กรอบตามช้าตอนขยับ).
- **STREAM** = client-side ล้วน: เบราว์เซอร์เปิดกล้องตัวเอง (`getUserMedia`, ต้อง HTTPS) →
  POST เฟรมไป `/api/stream/infer` → คืน JSON กรอบ → วาดบน canvas. **per-client isolation**
  (ไม่แชร์กล้อง/pipeline). JS อยู่ใน `templates/index.html` (ค่าคงที่ `STREAM_*`).
- **นับ 1 กระป๋อง = 1 การตรวจ (edge-triggered)**: state `none/ok/ng` ต่อกระป๋อง, นับ+log DB
  ครั้งเดียวตอน rising edge (none→ng), กระป๋อง "หายไป" หลัง `DEFECT_RESET_FRAMES` เฟรมว่าง.
  USB/RTSP อยู่ใน `inference_loop`; STREAM อยู่ใน JS (`streamInferLoop`).
- **Frame Capture** (display-only, USB/RTSP): `capture_loop` ให้คะแนนความคมทุกเฟรมดิบ
  (candidate pool) เฉพาะตอน `frame_capture_enabled AND pool_collecting`; `inference_loop`
  ตั้ง `pool_collecting = defect AND _can_complete()` (กระป๋องครบใบ), รีเซ็ต pool ต่อกระป๋อง,
  ตอนกระป๋องผ่านไป **re-infer เฟรมที่เลือก** (ให้กรอบตรง) → publish เป็น JPEG แช่ 5 วิ ใน
  `generate_frames`. **ไม่กระทบการนับ/DB**. Toggle ผ่าน `POST /api/frame_capture`.

---

## 🖥️ Entrypoints & HTTPS

- **`app.py`** = entrypoint หลัก (ผู้ใช้รัน `py -3.9 app.py`). `threaded=True`. รองรับ HTTPS.
- **`run_server.py`** = gevent (ทางเลือก, deploy หนัก). **ผู้ใช้ไม่ได้ใช้** (เคย import gevent ผิด
  interpreter). `app.py` เพียงพอแล้วหลัง STREAM เปลี่ยนเป็น request/response.
- **HTTPS** (`USE_HTTPS=True`): จำเป็นสำหรับ STREAM (`getUserMedia`). cert: `python generate_cert.py <ip>`.
  ถ้า port 5000 bind ไม่ได้ (WinError 10013) = port ถูก Windows/Hyper-V สงวน → เปลี่ยน `FLASK_PORT`.

---

## 🔧 กับดัก Windows/Python ที่เจอบ่อย

- **Dual Python**: `pip install` เปล่าอาจลงคนละ interpreter กับ `py -3.9` → ใช้ `py -3.9 -m pip install ...` เสมอ.
- **CONFIG_VERSION footer** = ตัวยืนยันว่ารันโค้ดใหม่จริง. bump ทุกครั้งที่แก้ config ที่ต้องให้ผู้ใช้ verify.

---

## 🧰 สภาพแวดล้อม & repo

- HW สถานี: **i7-1165G7** (4C/8T, 15W, AVX-512), 16GB DDR4 (single-channel), Iris Xe, Win10 Pro, Python 3.9.13.
- inference bestX (seg) บน ONNX CPU ≈ **280ms/เฟรม (~2.7-3 FPS)** = เพดานของ CPU นี้ (ไม่มี iGPU).
- Repo: `iceamonwat09/digital_vision2026`. Dev branch: `claude/dent-detection-camera-access-ub11gy`.
- SQL Server: 172.32.0.50/VisionIQ. Defect log ผ่าน `sp_log_defect` (เก็บภาพ base64).
- Tests: `pytest tests/` (artwork/label/barcode — ไม่ครอบคลุม camera/live loop).

---

## ✅ Checklist ก่อน commit

- [ ] flag ใหม่ default = พฤติกรรมเดิม? scope เฉพาะโหมดที่ตั้งใจ?
- [ ] fallback ครบทุกทางที่อาจล้มเหลว?
- [ ] การนับ/DB logging เดิมไม่ถูกแตะ? (ถ้าแตะ inference_loop ให้ไล่ดู)
- [ ] `python -c "import ast; ast.parse(open('app.py').read())"` ผ่าน?
- [ ] bump `CONFIG_VERSION` ถ้าผู้ใช้ต้อง verify?
- [ ] commit message ชัด + push ไป dev branch (ไม่ใช่ main)?
