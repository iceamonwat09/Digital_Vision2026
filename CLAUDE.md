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
   - **ผลที่ผิดแบบมั่นใจ แย่กว่าไม่แสดงผล** — ใช้กับทุกชั้นที่ "ชี้จุด" ให้คนดู (เช่นกรอบแดงชี้คำผิด):
     ถ้าไม่มั่นใจให้**ไม่แสดง** ดีกว่าเดา เพราะคนจะเชื่อสิ่งที่ระบบชี้แล้วมองข้ามของจริง.
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

## ⚡ ONNX acceleration (`USE_ONNX=True` — ปัจจุบันเป็นชั้น fallback ใต้ iGPU)

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

## 🚫 OpenVINO — ค่าเริ่มต้นปิด (`USE_OPENVINO=False`) — เคยพังเงียบ

เคยเปิด `openvino==2025.3.0` บน **py3.9 (off-spec: 2025 ต้องการ Python ≥3.10)** → export/โหลด
สำเร็จ **แต่ตรวจไม่เจอทุกโหมดแบบเงียบๆ** (ไม่ error). นี่คือ failure mode ที่อันตรายที่สุด:
**ห้ามเชื่อว่าใช้ได้แค่เพราะโค้ดรันไม่ error — ต้องผ่าน `verify_onnx.py` เท่านั้น**.

## 🎮 iGPU (Iris Xe) acceleration — ✅ VERIFIED & ENABLED บนสถานี (2 ก.ค. 2026)

**สถานะ: เปิดใช้จริงใน production แล้ว** — `OPENVINO_DEVICE = "intel:gpu"`,
`CONFIG_VERSION = "2026.07.02-ov-igpu-ON"`, `openvino==2024.6.0` ติดตั้งบนสถานี
(pip ถอน 2025.3.0 ตัว off-spec ออกให้ตอนติดตั้ง).

**ตัวเลขจริงบนสถานี (จาก log `YOLO inference avg` + verify_openvino.py):**
- **bestX (seg, production) live 480: ~45-50ms/เฟรม (~20-22 FPS) ≈ เร็วขึ้น ~6 เท่า**
  จากเพดาน ONNX CPU เดิม ~280ms (~2.7 FPS). เร็วกว่าตอน verify (137ms) เพราะเฟรม
  live 640x480 ไม่มี cost ย่อภาพใหญ่. snapshot 1280: 420ms (เดิม ONNX ~1739ms).
- best.pt (detect) บน GPU: ~14ms (~70 FPS).
- **Coverage (โจทย์ตั้งต้น): จบ** — 1-2 วิในเฟรม × ~21 FPS = 20-40+ ครั้ง/ใบ (เป้า 4-5).
- verify_openvino.py (bestX): **PASS ทั้ง intel:cpu+intel:gpu × 480+1280** —
  GPU มี FP16 drift จริงแต่เล็กมาก (IoU 0.9809-0.9913, Δconf ≤0.0053, กล่อง/คลาสตรงหมด;
  CPU ตรงเป๊ะ IoU 1.0/Δconf 0.0).

**⚠️ งานค้าง (บันทึกไว้ตามจริง — ควรปิดเมื่อสะดวก):**
1. ชุดภาพ verify ตอนเปิดใช้มีแค่ 1 รูป (ต่ำกว่ามาตรฐาน ≥10-20 รูปของ repo) — ปิด loop:
   `py -3.9 dump_defect_images.py --limit 30` (ดึงภาพ NG จริงจาก DB → sample_cans)
   แล้วรัน verify_openvino.py กับ bestX อีกรอบ.
2. `best.pt` (โหมด detect) วิ่งบน GPU ด้วยแต่ยังไม่เคยผ่าน verify_openvino.py แยกของตัวเอง
   — รัน `py -3.9 verify_openvino.py --weights weights\can_dent\best.pt --images <โฟลเดอร์>`.

**Rollback:** ตั้ง `OPENVINO_DEVICE = None` + รีสตาร์ต = กลับ ONNX CPU เดิม 100% ทันที.
ถ้า GPU พังเองระหว่างรัน load_model() fallback → ONNX → PyTorch อัตโนมัติ (ดู log).

**ข้อเท็จจริงเวอร์ชัน (re-verify จาก PyPI + ซอร์ส ultralytics v8.4.41, ก.ค. 2026):**
- **`openvino==2024.6.0` = ตัวที่ถูกต้องสำหรับ py3.9** — รุ่นสุดท้ายที่มี wheel
  `cp39-win_amd64` **และ**อยู่ในช่วง `openvino>=2024.0.0` ที่ exporter ของ
  ultralytics 8.4.41 ต้องการอย่างเป็นทางการ (ไม่ต้องใช้ 2023.x ที่เสี่ยง mismatch).
- ultralytics 8.4.41 รองรับ `device="intel:gpu"` ตอน predict ในตัว (`select_device`
  ส่งผ่าน string `intel:*` ตรงๆ, OpenVINOBackend parse เป็น device_name="GPU").
- `onnxruntime-openvino` ตัดทิ้ง: รุ่นใหม่ต้อง py≥3.10, รุ่นเก่าชน onnxruntime 1.19.2.
- **⚠️ GPU plugin ของ OpenVINO default รันภายในเป็น FP16** แม้ IR เป็น FP32 →
  ความแม่นตัดสินด้วย `verify_openvino.py` เท่านั้น. ถ้า FAIL เพราะ drift → แผนสำรอง
  คือบังคับ `INFERENCE_PRECISION_HINT=f32` (ช้าลง, ต้องแก้เพิ่ม — ยังไม่ทำ).

**โครงสร้างโค้ด (เปิดใช้แล้ว):**
- `config.OPENVINO_DEVICE = None` (opt-in; ตั้ง `"intel:gpu"` เพื่อเปิด) — แยกจาก
  `USE_OPENVINO` เดิม. default None = ทุกโหมดทำงานเท่าเดิม 100%.
- `_select_backend()` เปลี่ยนเป็นคืน **candidate list**: OpenVINO@device (ถ้าตั้ง
  flag) → ONNX CPU → OpenVINO (legacy) → PyTorch; `load_model()` ไล่ลองทีละตัว
  (load + smoke test) → **fallback GPU→ONNX→PyTorch อัตโนมัติ**. flag ปิด = ลำดับเดิมเป๊ะ.
- `_maybe_openvino()`: เช็ค device มีจริงผ่าน `ov.Core().available_devices` ก่อน
  (กัน OpenVINO เงียบๆ fallback ไป AUTO/CPU เอง = ตัวเลขความเร็วหลอก) + stale guard
  (`.pt` ใหม่กว่า IR → re-export) + โหลดด้วย task จาก `_accel_task()` เหมือน ONNX.
- **`verify_openvino.py`** = ตาข่ายนิรภัย (เกณฑ์ import จาก `verify_onnx.py` ชุดเดียวกัน):
  เทียบ PyTorch vs OpenVINO ทั้ง `intel:cpu`+`intel:gpu` ที่ 480+1280 + วัดความเร็ว
  PyTorch/ONNX/OpenVINO ในรันเดียว:
  `py -3.9 verify_openvino.py --weights weights\can_dent\bestX.pt --images <โฟลเดอร์>`

**ถ้าต้องเปิดใช้ใหม่บนเครื่องอื่น (ตามลำดับ ห้ามข้าม):** (1) `py -3.9 -m pip install
"openvino==2024.6.0"` (2) เช็ค `ov.Core().available_devices` มี GPU (3) รัน
verify_openvino.py ต้อง PASS ทุก device×imgsz (4) เช็คตัวเลขความเร็วคุ้ม
(5) ตั้ง `OPENVINO_DEVICE="intel:gpu"` + รีสตาร์ต + เช็ค footer/log.

**Route B (ไม่ต้องใช้แล้ว — Route A ผ่าน):** Python 3.11 ใน venv แยก เก็บไว้เป็น
ความรู้เผื่ออนาคต (เช่นถ้าจำเป็นต้องอัป openvino/onnxruntime เกินรุ่นสุดท้ายของ py3.9).

**บังคับทุก Route:** (ก) verify เทียบ PyTorch ผ่าน (IoU ≥0.97, Δconf ≤0.05, จำนวนกล่องตรง,
เคส "GPU เจอ 0 แต่ PyTorch เจอหลายกล่อง" = FAIL) (ข) fallback อัตโนมัติกลับ CPU-ONNX→PyTorch
(ค) opt-in flag default ปิด. **⚠️ RAM single-channel = คอขวด iGPU** → คาดจริง ~120-180ms
(ไม่ใช่ 80ms) ถ้าเอาเต็มควรอัป RAM dual-channel ก่อน.

---

## 📷 กล้อง & imgsz

- Live: 640x480 @ 30fps, `CAMERA_FOURCC=None` (YUY2 — "MJPG" ทำภาพแตกบน MSMF). กล้องตัน 720p ผ่าน MSMF.
- **imgsz: live=480, snapshot=1280. ⚠️ ห้ามต่ำกว่า 480** — dent เป็นฟีเจอร์เล็ก ลดเป็น 320 = ตรวจไม่เจอเลย.
- **Exposure/Brightness** (`CAMERA_AUTO_EXPOSURE`/`CAMERA_EXPOSURE`/`CAMERA_BRIGHTNESS`, opt-in default
  None): เฉพาะกล้อง **live** (ส่งผ่าน ctor ที่ site สร้างกล้อง live; snapshot/RTSP ไม่แตะ). best-effort.
  - **⚠️ กล้องสถานีนี้: EXPOSURE/GAIN/GAMMA ตั้งไม่ได้ผ่าน OpenCV แต่ `BRIGHTNESS` (0-255) ได้** —
    พิสูจน์ด้วย `diagnose_exposure.py` (เทสต์ว่าภาพสว่างเปลี่ยนจริงต่อ knob/backend).
  - ปรับสดขณะรัน: สไลเดอร์ในแผง USB → `POST /api/camera/control` {control, value} →
    `Camera.set_control(name, value)` (brightness/contrast; มี `_cap_lock` กัน race กับ
    `capture_loop`). StreamCamera ไม่มี method นี้ (endpoint คืน error). CONTRAST = knob ทดลอง
    (อาจช่วยหรือแย่ลง — domain shift; ยังไม่ยืนยันว่ากล้องรับ) ต้องเทียบผลตรวจจริงก่อนใช้.
- `Camera` class ใช้ร่วมทั้ง live+snapshot (แยกด้วย ctor params). RTSP → `_initialize_rtsp` (ไม่ทำ exposure).

---

## 🏭 กล้องอุตสาหกรรม Hikrobot (GigE Vision) — โหมดถ่ายรูปตรวจ (ส.ค. 2026)

ผู้ใช้ได้กล้อง Hikrobot มา (ยืนยันจากรูป: **RJ45 สกรีน `LAN/POE` + ขั้วกลม I/O + ไฟ `PWR` +
เมาท์ C-mount**) เพื่อเดินตาม `docs/PLAN_LINE_DENT_INSPECTION.md`. เฟสนี้ทำ **snapshot อย่างเดียว**.

**✅ รุ่นจริงยืนยันแล้วจากโปรแกรม MVS บนสถานี (13 ส.ค. 2026) — เปิดดูภาพได้ปกติ:**

| | ค่า |
|---|---|
| รุ่น / serial | **`MV-CS050-10GC`** / `DA4994130` · เฟิร์มแวร์ `V4.0.42` |
| เซนเซอร์ | Sony **IMX264, 2/3", global shutter**, พิกเซล 3.45µm ⇒ **8.45 × 7.07 mm** |
| ความละเอียด / เฟรมเรตสูงสุด | **2448 × 2048 (5MP)** @ **24.2 fps** |
| exposure ที่ทำได้ | **15µs–10s** (โหมดปกติ) · **1–14µs** (UltraShort) — เกินพอสำหรับไลน์ที่ต้องการ ~150-200µs |
| เครือข่าย | กล้อง `172.32.1.253/24` · การ์ดแลน `172.32.1.9` · gateway `172.32.1.254` ⇒ **subnet ตรงกัน ผ่าน** |

**🔴 สิ่งที่วัดได้จากแถบสถานะ MVS แล้วต้องแก้ (อย่าปล่อยผ่าน):**
- `2448x2048 @ 23.21 fps` = **BW 930.9 Mbps = 93% ของลิงก์ 1Gbps** → **`Packets Lost: 3066`**
  (`Errors: 0` เพราะขอส่งซ้ำทัน แต่แปลว่าลิงก์ตันจริง)
- **930.9 Mbps ตรงกับ 8 bit/พิกเซลเป๊ะ ⇒ กล้องส่ง Bayer 8-bit** (ไม่ใช่ RGB) ⇒ **ทุกเฟรม
  วิ่งผ่าน `MV_CC_ConvertPixelType`** ⇒ กับดักข้อ 1 (สีสลับ) เป็นเรื่องที่ต้องพิสูจน์ด้วยตาจริง ๆ
- **กล้องเสียบอยู่บนวงเดียวกับระบบงาน** (SQL Server `172.32.0.50` อยู่ `172.32.x` เหมือนกัน)
  ⇒ ปล่อย 931 Mbps = ไปบีบเครือข่ายโรงงานด้วย ไม่ใช่แค่ตัวเอง
- **ทางแก้ที่ทำแล้ว: `HIK_FRAME_RATE = 10.0` เป็นค่า default** (10 fps = 401 Mbps = 40% ของลิงก์)
  โหมดถ่ายรูปตรวจไม่ต้องการ 23 fps อยู่แล้ว — viewfinder แค่ให้เล็งได้ ชัตเตอร์หยิบเฟรมล่าสุด.
  จะปลดเป็น `None` ได้ต่อเมื่อ **แยกการ์ดแลนให้กล้องโดยเฉพาะ** + เปิด Jumbo Frame.

**เลนส์ C-mount ที่ต้องซื้อ (คำนวณจากเซนเซอร์ตัวนี้ — `f = ระยะทำงาน × 7.07 ÷ FOV`,
FOV = เส้นผ่านศูนย์กลางฝา × 1.4):**

| เส้นผ่านศูนย์กลางฝา | 15cm | 20cm | 25cm | 30cm | 40cm | 50cm |
|---|---|---|---|---|---|---|
| 65 mm | 11.6 | 15.5 | 19.4 | 23.3 | 31.1 | 38.8 |
| 73 mm | 10.4 | 13.8 | 17.3 | 20.7 | 27.7 | 34.6 |
| 83 mm | 9.1 | 12.2 | 15.2 | 18.2 | 24.3 | 30.4 |
| 99 mm | 7.6 | 10.2 | 12.7 | 15.3 | 20.4 | 25.5 |

ได้ความละเอียดบนฝา ~**44-68 µm/พิกเซล** (ฝา 65-99mm) — ละเอียดกว่าที่ dent ระดับ QA ต้องการมาก.

**⚠️ ข้อเท็จจริงที่ต้องรู้ก่อนแตะเรื่องนี้ (เคยเข้าใจผิดกันบ่อย):**
1. **RJ45 บนกล้องอุตสาหกรรม ≠ IP camera** — เป็น **GigE Vision** ไม่มี RTSP.
   `cv2.VideoCapture("rtsp://...")` **ใช้ไม่ได้เด็ดขาด** ต้องผ่าน **MVS SDK** เท่านั้น.
2. **`UD38142B` ที่พิมพ์มากับกล่อง = รหัสเอกสาร ไม่ใช่รุ่นกล้อง** (รูปแบบเดียวกับ
   `UD24388B_GigE Line Scan Camera User Manual`). รุ่นจริงอยู่บนสติกเกอร์ตัวกล้อง (`MV-...`).
3. MVS SDK Python binding เป็น **ctypes ล้วน** → **ใช้กับ py3.9 ได้เลย ไม่ต้องคอมไพล์/อัป Python**
   และครอบทั้ง GigE + USB3 ด้วยโค้ดชุดเดียว.
   Path: `C:\Program Files (x86)\MVS\Development\Samples\Python\MvImport`
   (DLL: `...\Common Files\MVS\Runtime\Win64_x64` — `hik_camera` ใส่ให้เองถ้า PATH ไม่มี).

**โครงสร้าง:** `hik_camera.py` → `class HikCamera` มี **interface เหมือน `camera.Camera` เป๊ะ**
(`initialize`/`read_frame`/`release`/`set_control`) ⇒ `viewfinder_loop`, `_grab_latest_frame`,
`/api/snapshot`, MJPEG generator, ตัวกันภาพค้าง **ใช้โค้ดเดิม 100% ไม่ถูกแก้เลย**
(แนวเดียวกับ `StreamCamera`). แตะ `app.py` แค่ 4 จุด: `_open_camera_ladder` (early-branch),
`/api/hik/scan` (endpoint ใหม่), guard ใน `start_detection`, ขยาย whitelist ของ `/api/camera/control`.

- **`HIK_ENABLED=False` เป็นค่า default** ⇒ แท็บไม่ขึ้น ไม่มีโค้ดส่วนนี้ถูกเรียก = ทุกโหมดเดิมเท่าเดิม.
- **SDK เป็น optional dependency** — ไม่มี MVS = แท็บขึ้นข้อความบอก แต่แอปทำงานปกติ (ไม่ error)
  แพทเทิร์นเดียวกับ `pytesseract`.
- **`/api/hik/scan` แยกจาก `/api/camera/scan` โดยเจตนา** — JS ฝั่ง USB ทำ `parseInt(val)`
  ถ้าเอาคีย์ `"hik:<serial>"` ไปปนจะกลายเป็น `NaN` เงียบๆ.
- **live detection ยังถูกบล็อก (409)** — ตรรกะนับแบบ "เฟรมว่าง" ใช้ที่ความเร็วไลน์ไม่ได้
  ต้องรอ triggered mode (`PLAN_LINE_DENT_INSPECTION.md` §4). ปล่อยให้ทำงานครึ่งๆ = ตัวเลขนับผิด.

**⚠️ กับดักที่เจอจริงระหว่างทำ (อย่าทำซ้ำ):**
1. **แปลง Bayer เองด้วย cv2 = R↔B สลับแบบเงียบ** — OpenCV กับ GenICam ตั้งชื่อ Bayer pattern
   **คนละ convention** (`cv2.COLOR_BayerRG2BGR` ≠ GenICam `BayerRG`). เดาผิด = ไม่มี error
   แต่โมเดลเห็นสีที่ไม่เคยเทรน. **default จึงใช้ `MV_CC_ConvertPixelType` ของ SDK เอง**
   (ผู้ผลิตรู้ convention ตัวเอง) ช้ากว่านิดแต่ถูกแน่. Mono8 → `cv2.cvtColor(GRAY2BGR)` (ไม่กำกวม).
   จะเปิดทางลัด cv2 ต้อง **พิสูจน์ว่าได้ภาพเท่ากันก่อน**.
2. **เขียน syntax ของ Jinja ในคอมเมนต์ JS ของ `templates/*.html` = หน้าพังทั้งหน้า** —
   เจอจริงตอนทำ: คอมเมนต์อธิบายว่า element ถูกครอบด้วยเงื่อนไข Jinja ทำให้ Jinja มองเป็นแท็กจริง
   แล้ว `{% endblock %}` ท้ายไฟล์พังตาม (`TemplateSyntaxError`). **ต้อง render จริงถึงจะเจอ**
   — `node --check` ไม่จับ เพราะ JS ถูกไวยากรณ์.
3. **Jinja `config` = `app.config` ของ Flask ไม่ใช่ `config.py` ของเรา** — เขียน
   `{% if config.HIK_ENABLED %}` จะได้ Undefined (falsy) เสมอ = ฟีเจอร์ไม่โผล่แบบเงียบ.
   ต้องส่งผ่าน `@app.context_processor` (ตอนนี้ส่งชื่อ `hik_enabled`).
4. **กล้อง GigE ที่ IP คนละ subnet กับการ์ดแลน จะ enumerate เจอ แต่เปิดไม่ได้** — กับดักอันดับ 1
   ของการต่อครั้งแรก. `scan_hik_cameras()` เทียบ `nCurrentIp` กับ `nNetExport` แล้วตั้ง
   `reachable=False` + ข้อความบอกวิธีแก้ (dropdown disable ตัวนั้นไว้เลย).
5. **ไม่คืน frame buffer = ภาพหยุดถาวร** — SDK มีบัฟเฟอร์แค่ `HIK_IMAGE_NODE_NUM` ตัว
   `MV_CC_FreeImageBuffer` จึงอยู่ใน `finally` เสมอ. และต้อง **copy ก่อนคืน** ไม่งั้นเฟรมก่อนหน้าถูกทับ.
6. **ตั้ง `Width` ขณะ `OffsetX` ยังใหญ่อยู่ = fail** — ลำดับ ROI ต้อง offset=0 → width/height → offset.
7. **ตั้ง exposure/gain ขณะ Auto ยังเปิด = ค่าไม่ติด** — ต้อง `ExposureAuto=Off` ก่อนเสมอ.
8. **ไม่เจรจา `GevSCPSPacketSize` = ภาพขาด/ช้ามาก** — `MV_CC_GetOptimalPacketSize()` ทุกครั้ง
   (GigE เท่านั้น; USB3 ไม่มี node นี้) และควรเปิด **Jumbo Frame 9014** บนการ์ดแลนด้วย.
9. **🔴 `release()` ต้องถือ `_cap_lock` — ไม่งั้น process ตายทั้งตัว** (เจอตอน audit, มีเทสต์กันแล้ว).
   ปิด/ทำลาย handle ขณะอีกเธรดยังอยู่กลาง `MV_CC_GetImageBuffer` = **use-after-free ระดับ native**
   → Flask ตายยกแอป ไม่ใช่แค่ exception. **เข้าถึงได้จริง** เพราะ `api_viewfinder_stop` ทำแค่
   `join(timeout=1.0)` แต่ grab ค้างได้เต็ม `HIK_GRAB_TIMEOUT_MS` (1000ms) ตอนกล้องแช่/สายหลุด —
   ซึ่งเป็นเคสที่ระบบ snapshot ตั้งใจกันอยู่แล้วพอดี. พิสูจน์แล้ว: ก่อนแก้ `release()` คืนใน **0ms**
   และเรียก `DestroyHandle` ทับ grab; หลังแก้รอ **601ms** จน grab จบ.
   `read_frame()` จึงต้อง **เช็ค `_cam is None` ซ้ำอีกครั้งใน lock** ด้วย (ของเดิมเช็คนอก lock
   แล้ว release อาจแทรกระหว่างรอ lock). เทสต์: `test_release_waits_for_an_in_flight_grab`.

**Config (`config.py`):** `HIK_ENABLED` (default `False`; เปิดด้วย **env `HIK_ENABLED=true`**
ได้โดยไม่ต้องแก้ไฟล์ที่ git ติดตาม — env ชนะค่าในไฟล์เสมอ. แบนเนอร์ตอน start พิมพ์บอกว่าเปิด/ปิด
และเจอ MVS SDK ไหม เพื่อไม่ต้องเดาว่าทำไมแท็บไม่โผล่) · `HIK_SOURCE_PREFIX="hik:"` ·
`HIK_MVS_SDK_PATH` · `HIK_EXPOSURE_AUTO`/`HIK_EXPOSURE_US` (หน่วย **µs** ไม่ใช่ log2 แบบ UVC) ·
`HIK_GAIN_AUTO`/`HIK_GAIN_DB` · `HIK_ROI` · `HIK_FRAME_RATE` · `HIK_PACKET_SIZE_AUTO` ·
`HIK_IMAGE_NODE_NUM=3` · `HIK_GRAB_TIMEOUT_MS=1000`

**เครื่องมือ/เทสต์:**
- `py -3.9 diagnose_hik.py --save` = ตาข่ายนิรภัย (แนวเดียวกับ `verify_openvino.py`):
  เช็ค SDK, ลิสต์กล้อง+subnet, เปิด, วัด fps จริง, packet size, **lost packet**, เขียน `hik_sample.jpg`.
  **ต้องเปิดรูปดูด้วยตาเพื่อยืนยันว่าสีไม่สลับ** (กับดักข้อ 1). รายงานอย่างเดียว **ไม่แก้ IP กล้องให้เอง**.
- `tests/test_hik_camera.py` **36 ตัว** — ยัด **fake MVS module** เข้า `sys.modules` (แนวเดียวกับที่
  mock Tesseract) จึง deterministic ไม่ต้องมีกล้อง. 3 ตัวสุดท้ายที่ import `app` ใช้ `importorskip`.

**⚠️ ยังไม่ได้พิสูจน์ (ต้องทำบนสถานี):** โค้ดทั้งหมดเขียนจากเอกสาร SDK — **ยังไม่เคยรันกับกล้องจริง**.
และ **`bestX.pt` เทรนจากภาพ webcam + แสงห้อง** → กล้อง/เลนส์/แสงใหม่ = **domain shift**
⇒ **verdict จากกล้องตัวนี้ยังเชื่อเป็น QC ไม่ได้จนกว่าจะ blind test** (`PLAN_LINE_DENT_INSPECTION.md` §9).
งานที่ส่งมอบคือ *ท่อภาพที่ถูกต้อง* ไม่ใช่ *ความแม่นที่พิสูจน์แล้ว*.

---

## 🎞️ ถ่ายรัว (burst) — เครื่องมือวัด "จับทันและนิ่งพอไหม"

ปุ่ม **"🎞️ ถ่ายรัว 10 ภาพ"** ในป็อปอัป "ถ่ายรูปตรวจ" → `POST /api/snapshot/burst`
กดครั้งเดียวเก็บเฟรมติดกัน N ใบ แล้ววัด **ความคม + verdict + เวลาห่างจริง** ต่อใบ.

**เหตุผลที่ต้องมี:** กดชัตเตอร์มือ 10 ครั้ง = วัด**เวลาตอบสนองของคนกด** ไม่ใช่วัดว่ากล้อง
จับของที่เคลื่อนที่ทันไหม. burst วัดกล้องจริง ๆ และให้**ตัวเลข**แทนความรู้สึก.

- **`_collect_burst()` เก็บให้ครบก่อน แล้วค่อย infer** — ถ้า infer คั่นกลาง (~400ms ที่ imgsz 1280)
  จะพลาดช่วงที่ของวิ่งผ่านไปเกือบหมด. นี่คือหัวใจของการออกแบบ ห้ามสลับลำดับ.
- ใช้ **`viewfinder_seq`** กันเก็บเฟรมเดิมซ้ำ (กล้องค้าง = ได้น้อยใบ ไม่ใช่ใบเดิม N ครั้ง)
  และเช็ค `SNAPSHOT_MAX_FRAME_AGE_S` เหมือนชัตเตอร์เดี่ยว (กฎเหล็ก 2: ไม่ตัดสินจากภาพเก่า)
- **ความคมใช้ `_frame_sharpness()` ตัวเดิมของ Frame Capture** (variance of Laplacian) — ไม่เขียนใหม่
- **แถบความคมเทียบกันเองในชุดนั้น** ไม่ใช่ค่าสัมบูรณ์ (ค่าขึ้นกับเนื้อภาพ) · ★ ผูกกับ
  `sharpest_index` จาก server เพื่อให้มีดาวใบเดียวเสมอแม้คะแนนเท่ากัน
- **display-only 100%** — ไม่แตะการนับ ไม่เขียน DB (มีเทสต์ `test_burst_does_not_touch_counters`)
- Config: `BURST_COUNT=10` · `BURST_MAX_COUNT=30` · `BURST_TIMEOUT_S=6.0` · `BURST_THUMB_W=420`
- ⚠️ **กิน RAM ชั่วคราว = N x ขนาดเฟรม** (10 x 2448x2048x3 ≈ 150 MB บน MV-CS050-10GC)
- เทสต์: `tests/test_snapshot_burst.py` 8 ตัว (กล้องปลอมสลับเฟรมคม/เบลอ + detector ปลอม)

**⚠️ ตัวเลขที่ต้องอ่านให้เป็น:** ถ้าทุกใบใน burst คมเท่ากันหมดแต่**ค่าต่ำ** = เบลอทั้งชุด
(exposure ยาวไป ไม่ใช่จังหวะกดผิด). ถ้าคมบางใบ = จังหวะ/ตำแหน่งเป็นตัวปัญหา.

---

## 🏗️ สถาปัตยกรรมสำคัญ (app.py)

- **Live USB/RTSP** = 2 thread: `capture_loop` (อ่านกล้อง → `latest_raw_frame`) +
  `inference_loop` (infer, นับ, log DB). `generate_frames` = MJPEG generator.
  - `LIVE_SMOOTH_VIDEO`: `False`=วาดกรอบบนเฟรมที่ infer จริง (กรอบเป๊ะ, ภาพตามอัตรา infer) /
    `True`=วาดกรอบล่าสุดบนเฟรมดิบล่าสุด (ภาพลื่น, กรอบตามช้าตอนขยับ). **เปิด Frame Capture =
    บังคับ smooth อัตโนมัติ** (`smooth = frame_capture_enabled or LIVE_SMOOTH_VIDEO` ประเมินสด
    ทุก loop) — ความแม่นกรอบไปอยู่ที่เฟรมที่แช่ (re-infer แล้ว) ส่วนภาพสดแค่ monitor.
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

## 🖍️ Artwork — กรอบแดงชี้ "คำที่มีปัญหา" (display-only)

การ์ด "รายการที่พบ" วาด **กรอบแดงบนคำที่ผิดจริง** ในรูป crop. **แสดงผลอย่างเดียว 100%** —
ไม่แตะ OCR/ผลตรวจ/verdict/การนับ. โค้ดอยู่ใน `artwork_check/highlight.py` (โมดูลใหม่),
เรียกจาก `pipeline.zone_crop_jpg()` เมื่อ `/api/artwork/<id>/crop?...&hl=<คำ>&zid=<โซน>`.

**สถาปัตยกรรม 4 ชั้น** (`highlight.locate_all()` — ไล่จากแม่นสุด ชั้นแรกที่เจอชนะ):

| ชั้น | วิธี | ใช้เมื่อ | ความแม่น (ไฟล์จริง) |
|---|---|---|---|
| 1 | **PDF text-layer word box** (`pdf_ingest.zone_words()`) | โซน `engine == "pdf-text"` | **เป๊ะระดับ vector, ทุกภาษา** |
| 2 | **Tesseract** (`_tess_boxes`) | ไฟล์ outline / ภาพถ่าย | 89% (benchmark), วัดจากพิกเซลจริง + self-verify |
| 3 | `blocks[].bbox` จาก OCR backend (`_block_boxes`) | Tesseract หาไม่เจอ/ไม่มี | **พิกัดจาก LLM = การประมาณ ต้องผ่าน `_verify_boxes` ก่อน** |
| 3b | **พิสูจน์ "แถว" แทนการอ่านคำ** (`_verify_boxes_by_row`) | คำที่ Tesseract ไม่มี traineddata | วัดแถวจากคำ ASCII ข้างเคียง |
| 4 | ไม่วาด | ไม่มั่นใจ | — |

⚠️ **ลำดับนี้เคยสลับกันแล้วพัง:** เดิมให้ `blocks[].bbox` มาก่อน Tesseract และ **ไม่ตรวจสอบเลย** →
บนสถานีจริงที่ Gemini คืน bbox มา กรอบไปโผล่คนละแถวในตารางโภชนาการ (LLM ให้พิกัดแบบ
*ประมาณ* ไม่ใช่ *วัด*). เทสต์ตอนนั้นตั้ง `blocks=[]` ตลอดจึงไม่เคยเจอ — **ถ้าจะเพิ่ม/สลับชั้น
ต้องมีเทสต์ที่ป้อน bbox ที่ "เพี้ยนไปคนละแถว" ด้วยเสมอ**.

- **ชั้น ① ดีที่สุดและฟรี** — ไม่ต้อง OCR/traineddata, รองรับฮีบรู/อาหรับ/จีน/ไทยทันที.
  ตรวจด้วย `ArtworkDocument.zone_words(bbox)` → `[(text, (fx0,fy0,fx1,fy1))]` เป็น**สัดส่วนในโซน**
  → `rotate_frac_box()` (ตามการหมุนโซน) → `frac_to_px()`.
- **`_cv_box` (projection profile) = ชั้นสำรองสุดท้าย ปิดไว้ (`HIGHLIGHT_USE_PROFILE=False`)** —
  benchmark พบว่า **วาดผิดคำ ~40%** บนตารางหนาแน่น. ห้ามเปิดเป็น default.
- **วาดทุกจุดที่คำปรากฏ** (`HIGHLIGHT_MAX_BOXES=6`) — คำผิดมักพิมพ์ซ้ำหลายแถว
  (จริง: `Cude` โผล่ 3 จุดในตารางเดียว) วาดจุดเดียว = ผู้ตรวจแก้ไม่ครบ.

**⚠️ ชั้น 3b — ทำไมบางครั้ง "เจอคำผิดแต่ไม่มีกรอบแดง" (เคสจริง ส.ค. 2026):**
สถานีลง traineddata แค่ `eng` → คำอาหรับที่สะกดผิด (`كربوهيدات كلية`) **ไม่มีกรอบเลย**
ขณะที่ `24%` บนการ์ดถัดไปมีกรอบปกติ. ไล่แล้วพบว่าไม่ใช่บั๊กของการจับคู่ — ชั้น ② อ่านอาหรับ
ไม่ออก (0 กรอบ) ส่วนชั้น ③ **หา bbox เจอ (1 กรอบ) แต่ถูก `_verify_boxes(require_positive=True)`
ทิ้งทุกครั้ง** เพราะการ "อ่านซ้ำเพื่อพิสูจน์คำ" เป็นไปไม่ได้เมื่อไม่มี traineddata ของภาษานั้น
(อ่านอาหรับด้วย `eng` ได้แต่ขยะ). วัดจากเคสจำลองที่สร้างตามภาพจริง:

| ภาษาของคำ | ชั้น ② Tesseract | ชั้น ③ bbox | ผ่านพิสูจน์ | วาดจริง |
|---|---|---|---|---|
| อาหรับ (`eng` อย่างเดียว) | 0 | 1 | **0** | **0 ← บั๊ก** |
| อาหรับ (`eng+ara`) | 1 | 1 | 1 | 1 |
| `20%` / อังกฤษ | 1 | 1 | 1 | 1 |

- **ทางแก้ที่ดีที่สุด = ติดตั้ง traineddata แล้วตั้ง `ARTWORK_HIGHLIGHT_TESS_LANG=eng+ara`**
  (ตรงกับผลทดสอบเดิมของ repo: 25/25 ด้วย `eng+ara` เทียบ 23/25 ด้วย `eng`).
- **ชั้น 3b = ตาข่ายรองรับเมื่อไม่มี traineddata**: พิสูจน์ **"แถว"** แทน **"คำ"** —
  บรรทัดเดียวกันมีคำ ASCII ("Total carbohydrate 0 g 0%") ที่ Tesseract อ่านได้ ใช้พิกเซล
  ของคำนั้นล็อกย่านแนวตั้งของแถว แล้วรับ bbox ของ LLM เฉพาะที่ **กึ่งกลางตกในแถวนั้น**
  และ **ไม่สูงเกิน 1.2 เท่าของแถว**. นี่คือแกนที่ bbox ของ LLM เคยพลาดจริง (ไปโผล่คนละแถว)
  จึงเป็นการพิสูจน์ที่ตรงจุด ไม่ใช่การผ่อนเกณฑ์.
- **เงื่อนไขที่ทำให้ "ไม่วาด" (ตั้งใจให้เข้มไว้ก่อน)**: บรรทัดของคำต้องไม่ซ้ำในข้อความ backend ·
  คำ anchor ต้องยาว ≥4 ตัว ไม่ใช่ตัวเลข และปรากฏครั้งเดียวในภาพ · ใช้เฉพาะคำ **ที่ไม่ใช่ ASCII**
  (คำอังกฤษที่พิสูจน์ไม่ผ่าน = กรอบน่าสงสัยจริง ต้องคงพฤติกรรมเดิมคือไม่วาด).
- Config: `ARTWORK_HIGHLIGHT_ROW_VERIFY` (default `1`; ตั้ง `0` = กลับพฤติกรรมเดิม 100%).
- เทสต์: `tests/test_artwork_highlight.py` 11 ตัว (mock Tesseract ให้ "อ่านภาษานั้นไม่ออก"
  จึง deterministic ไม่ต้องพึ่ง binary) — รวมเคส **bbox เลื่อนไปคนละแถวต้องไม่วาด**.

**⚠️ กับดักที่เจอมาแล้ว (อย่าทำซ้ำ):**
1. **fuzzy match กับคำสั้น/CJK = กรอบผิดคำ** — จีน `灰分`(เถ้า) เคยจับกรอบเดียวกับ `水分`(ความชื้น)
   เพราะต่างกัน 1 ตัวอักษร. `_all_word_matches()` จึง **fuzzy เฉพาะคำ ascii ยาว ≥5** เท่านั้น
   (พอสำหรับ typo อังกฤษที่ตั้งใจจับ) — คำสั้น/CJK/RTL ต้อง exact/substring. **กรอบผิดแย่กว่าไม่มีกรอบ**.
2. **คืน "tier เดียว"** — ถ้ามี literal match แล้ว ห้ามเอา fuzzy มาปน (ไม่งั้นคำที่แค่คล้ายจะติดกรอบมาด้วย).
2b. **`found` ของ MISMATCH_* เป็น "ทั้งบรรทัด" ไม่ใช่คำเดียว** — จับคู่ทีละคำแล้วกระจายกรอบ
   ผิดแถวทันที (เคสจริง: `دهون كلية` ได้ 5 กรอบ เพราะ `دهون`/`كلية` ไปโผล่แถวอื่น).
   `_match_boxes()` จึงแยกทาง: คำเดียว → ทุก occurrence; **หลายคำ → ต้องเป็น run ที่
   ติดกันและอยู่บรรทัดเดียวกัน** (`_phrase_matches` + `_same_line`) แล้ว union เป็นกรอบเดียว.
   วลีเปิด fuzzy ได้ (budget 15% ของความยาว) เพราะถูกล็อกด้วย adjacency แล้ว — จำเป็นจริง:
   Tesseract อ่าน `كربوهيدرات` ตกเป็น `كربوهيدات` (หาย 1 ตัว) บ่อย.
3. **ตั้ง `TESS_LANG` เป็นภาษาที่ไม่ได้ติดตั้ง = Tesseract error ทั้ง call → กรอบหายหมดแม้แต่อังกฤษ**.
   `_resolve_langs()` จึงกรองเหลือเฉพาะภาษาที่ `get_languages()` ยืนยัน (fallback → `eng`).
4. **bbox ของ Gemini มีหลาย convention** (0..1 / 0..1000 / pixel) แยกไม่ออกถ้าไม่รู้ขนาดภาพ →
   `ocr.read_zone()` เก็บ **`ocr_wh`** (ขนาด crop ที่ OCR เห็นจริง) ให้ `_infer_scale()` ตัดสิน **ต่อโซน**
   จากพิกัดใหญ่สุดของทุก block (block เดียวใกล้มุมตัดสินไม่ได้).
5. **`_otsu()` คืน threshold 0 ได้** บนภาพ bimodal สะอาด → ต้องใช้ `gray <= thr` (ถ้าใช้ `<` ชั้น CV ตาย
   เงียบ คืน None ตลอด).
6. **ขนาด crop เป็นตัวชี้เป็นชี้ตายของ Tesseract** — โซนเล็กเรนเดอร์ที่ OCR_DPI ได้ ~490px แล้ว
   Tesseract อ่านมั่ว (0/8 คำ, อ่าน "NUTRITIONAL INFORMATION" เป็น "ANO/V/OLES") → ตกไปใช้ bbox
   ของ backend ที่คลาดเคลื่อน = กรอบผิดแถว. แก้ด้วย `CROP_MIN_SIDE=1200` (PDF เรนเดอร์ใหม่ DPI สูงขึ้น
   = ได้รายละเอียดจริง) + `_upscale_for_ocr()` (ภาพถ่าย ขยายในหน่วยความจำก่อน OCR แล้วหารพิกัดกลับ).
7. **PSM ของ Tesseract สำคัญมากกับ "ตาราง"** — default (psm 3, auto layout) อ่าน *ชื่อรายการ*
   ได้หมดแต่ **ทิ้งคอลัมน์ตัวเลขทั้งคอลัมน์** (หา `24%`/`170`/`475` ไม่เจอเลย). `_PSM_ORDER=(11,3)`
   ลอง **psm 11 (sparse text)** ก่อน แล้วค่อยถอยไป psm 3 (ดีกว่ากับข้อความยาวต่อเนื่อง เช่นบล็อกอาหรับ).
   วัดจาก 7 โซนจริง: psm3=38/44, psm11=42/44, ลองทั้งคู่=43/44.
8. **การอ่านทั้งภาพไม่เสถียรระดับ ±1 พิกเซล** — crop 1455px อ่าน `24%` เป็น `72`, crop 1456px
   อ่านถูก. แต่ **ครอปเฉพาะเซลล์ (75x38) อ่านถูกทุก psm**. จึงมี `_row_refine()`: เมื่อหาไม่เจอ
   ทั้งภาพ ให้ใช้คำข้างเคียงในบรรทัดเดียวกัน (จากข้อความ OCR ของ backend) หา**แถบแถว** แล้ว
   อ่านซ้ำเฉพาะแถบนั้น. anchor ต้องเจอ **ครั้งเดียว** ในภาพ (ไม่งั้นชี้แถวไม่ได้) และผลต้องผ่าน
   `_verify_boxes(require_positive=True)` → เป็นไปไม่ได้ที่จะไปโผล่คนละแถว.
9. **`_verify_boxes` ต้องใช้ psm 7/8 ไม่ใช่ default** — ครอปขนาดเท่าคำเดียวถ้าอ่านด้วย psm 3
   จะได้ค่าว่าง/ขยะ แล้วไป**ตัดกรอบที่ถูกต้องทิ้ง** (เคสจริง: กรอบ `24` ที่ถูกต้องถูกตัดทุกครั้ง).
10. **การ "พิสูจน์ว่าผิด" ต้องเชื่อถือได้ก่อนถึงจะใช้ตัดสิน** — อ่านซ้ำครอปแคบของอาหรับ/CJK
   ไม่น่าเชื่อถือ (กรอบอาหรับที่ถูกอ่านซ้ำได้ `Yoda كلية`) → กรอบที่ "วัดมา" (Tesseract) ของคำ
   non-ASCII จึง**เก็บไว้เสมอ** ส่วน bbox ของ LLM ต้องพิสูจน์ว่าถูกเท่านั้นถึงวาด (asymmetric).
11. **cache ผล OCR ต่อรูป** (`_WORDS_CACHE` key = hash เนื้อภาพ+lang+psm) — การ์ด defect
   หลายใบในโซนเดียวกันจะไม่ OCR ซ้ำ (วัดจริง เร็วขึ้น ~2.5 เท่า). **เป็น `OrderedDict` +
   `threading.Lock`** — Flask `threaded=True` ทำให้ 2 request ชนกันได้จริง (dict ธรรมดา
   จะ evict มั่ว/`RuntimeError` ตอน iterate); ตัด LRU ที่ `_WORDS_CACHE_MAX=12`.
12. **ตัวเลขอาหรับ-อินดิก (`٤٧٥`) ไม่ใช่ `475`** — Tesseract โหมด `ara` คืนตัวเลขเป็น
   `٠-٩` ส่วน defect ที่ฟ้องมาเป็นเลขอารบิกปกติ → จับคู่ไม่ติด **เงียบ ๆ** (ไม่มีกรอบ ไม่มี error).
   `_norm()` จึงพับ `٠-٩`+`۰-۹` (เปอร์เซีย) เป็น `0-9` ก่อนเทียบทุกครั้ง.
13. **เลือก "แถว" ด้วย substring = ชี้ผิดแถว** — `_row_refine()` เดิมหาบรรทัดด้วย
   `key in line`: คำเป้าหมาย `0` (จาก `0 g`) ไปเจอใน `10%` ของอีกบรรทัดทันที.
   ตอนนี้เทียบ **ทั้ง token** (คำสั้น) และถ้าเจอ **มากกว่า 1 บรรทัด = กำกวม → ไม่วาด**
   (กฎเหล็ก 2: ไม่มั่นใจ ไม่แสดง).
14. **คำที่ขอบโซนตัดผ่าน** — PDF text-layer คืนกรอบ **เต็มคำบนหน้ากระดาษ** แม้คำนั้นโผล่ในโซน
   แค่เสี้ยวเดียว → clamp แล้วได้แถบบางติดขอบโซน = ดูเหมือนวาดผิดที่. `frac_to_px()` จึงทิ้ง
   กรอบที่หลุดออกนอกโซน > `_MAX_CLIP_FRAC` (25%).

**⚠️ ข้อจำกัดเชิงกายภาพ (แก้ด้วยโค้ดไม่ได้ — ต้องบอกผู้ใช้):** วัดจากไฟล์จริง กรอบแดงต้องการ
ตัวอักษรในภาพ crop สูงราว **9-20 px**. โซนที่ลากเป็น **แถบกว้างทั้งแผ่น** (เช่น 1600x339)
ตัวหนังสือเล็กเหลือ ~8px → หาคำไม่เจอเลย (0/14) และ **เร่ง resolution ก็ตันที่ 6/14**
(เพราะแถบกว้างมีทั้งกราฟิก/ภาพถ่าย/หลายภาษาปนกัน). โซนเดียวกันที่ลากกระชับรอบตาราง
(1455x990) ได้ **14/14**. ที่วัดได้ชัดคือ **ด้านสั้นของ crop** (พัง: 339/487 · ผ่าน: 895/906/988/1186)
→ เกณฑ์เตือน `zones.HL_MIN_SHORT_SIDE=700` + `HL_MAX_ASPECT=4.0`.
ข้อค้นพบสวนสามัญสำนึก: **ใหญ่ขึ้นไม่ได้ดีขึ้นเสมอ** (คำสูง 32px ได้ 11/14 แพ้ 9-20px ที่ได้ 14/14)
และ **ย่อภาพ raster ลงคือหายนะ** (1/14). วัดแล้วยัง **ปฏิเสธ** 3 ไอเดียของตัวเอง: ปิด dictionary
(ไม่มีผล), Sauvola (แย่ลง), โหลดหลายภาษา (ความแม่นเท่าเดิม แค่ช้าลง ~3.5 เท่า).

**เตือนผู้ใช้ 2 จุด (advisory ล้วน — ไม่แตะ verdict/การนับ/ข้อความ OCR):**
- **ตอนจัดโซน** (ก่อนส่งตรวจ): `renderHlHint()` ใน `artwork_check.js` คำนวณขนาด crop
  ที่จะได้จาก **เรขาคณิตอย่างเดียว** (ไม่เรนเดอร์ ไม่ OCR = ฟรี) แล้วขึ้นบรรทัดเตือนใน
  แผง properties ทันทีที่เลือกโซน — ผู้ใช้แก้ได้เลยก่อนเสียเวลาตรวจ.
- **ในการ์ด "รายการที่พบ"**: `pipeline._tag_highlight_risk()` ตั้ง `z["hl_risk"]`
  (`"wide"`/`"small"`) ลง `report.json` ตอนตรวจ → JS แสดงเหตุผลว่าทำไมไม่มีกรอบแดง
  + วิธีแก้ (ลากโซนให้กระชับแล้วส่งใหม่).
- **ค่าคงที่ต้องตรงกันสองฝั่ง** (`zones.HL_*` ↔ `HL_*` ใน `artwork_check.js`) — แก้ข้างเดียวแล้ว
  คำเตือนตอนจัดโซนกับตอนดูผลจะไม่ตรงกัน.

**Tesseract (ชั้น ②) — optional dependency:**
- `_find_tesseract_cmd()` **auto-detect ให้** ตามลำดับ: env `ARTWORK_TESSERACT_CMD` → PATH →
  `C:\Program Files\Tesseract-OCR\tesseract.exe` → `%LOCALAPPDATA%\Programs\Tesseract-OCR\`.
  **ไม่ต้องตั้ง PATH เอง**.
- ติดตั้ง: UB-Mannheim installer (ติ๊ก Additional language data: Arabic/Hebrew/Chinese/Thai) +
  `py -3.9 -m pip install pytesseract`. **ไม่ติดตั้ง = ไม่มีกรอบ แต่ระบบทำงานปกติ** (ไม่ error).
- หลายภาษา: `ARTWORK_HIGHLIGHT_TESS_LANG=eng+ara+heb+chi_tra+tha`.
- **ติดตั้งที่ server เท่านั้น** — วาดกรอบฝั่ง server ส่ง JPEG ให้ client (เครื่อง client ไม่ต้องลงอะไร).

**Config (ทุกตัว opt-out ได้ — `artwork_check/config.py`):**
`HIGHLIGHT_DEFECT_WORD` (ปิดทั้งฟีเจอร์) · `HIGHLIGHT_USE_PDF_TEXT` · `HIGHLIGHT_USE_TESSERACT` ·
`HIGHLIGHT_USE_PROFILE` (default False) · `HIGHLIGHT_TESSERACT_LANG` · `HIGHLIGHT_MAX_BOXES` ·
`CROP_MIN_SIDE` (=1200 ด้านยาวขั้นต่ำของ crop ในการ์ด — ตัวชี้เป็นชี้ตายของ Tesseract, ดูกับดักข้อ 6)

**เครื่องมือ diagnose บนสถานี:** `py -3.9 diagnose_highlight.py <inspection-id>` — พิมพ์ config,
path/ภาษาของ tesseract, ชั้นที่ใช้ต่อโซน, จำนวนกรอบต่อ defect และ**อ่านซ้ำทีละกรอบ**ว่าในกรอบ
คือคำอะไร (`--save` เขียนไฟล์ `diag_<id>_<n>_<zone>.jpg` ออกมาดูด้วยตา). ใช้ตอบคำถาม
"ทำไมกรอบไม่ขึ้น/ขึ้นผิดที่" ได้โดยไม่ต้องเดา.

**ผลทดสอบ end-to-end (production path, 5 artwork จริง + 1 ภาพถ่าย):**
- Cosma/GimCat (มี text layer) → ชั้น ① **8/8** เป๊ะ · StarKist/TerraMadre/JohnWest (outline) → ชั้น ②
- รวม **25/25** เมื่อตั้ง `eng+ara` (23/25 ด้วย `eng` ล้วน — อาหรับ MISS = ไม่วาด ไม่ error)
- ภาพถ่ายกล้องจริง (Puffy Nee Nee): **9/9** รวม `Cude`/`Phosphours` ที่เป็นคำผิดจริง
- **ไม่มีกรอบวางผิดแม้แต่จุดเดียวในทุกไฟล์**
- ⚠️ อ่อนกับ **โลโก้/badge สไตล์ไลซ์บนกราฟิก** (SHINYCAT/GLUTEN FREE) — ปกติ ไม่ค่อยถูกฟ้องเป็น defect
- ⚠️ **ทุกครั้งที่ "ดูเหมือนพัง" ตอนทดสอบ = พิกัดโซนผิด ไม่ใช่เมธอด** — verify โซนด้วยการ render ดูก่อนเสมอ

---

## 🖼️ Artwork — เลย์เอาต์หน้าจอ (คอลัมน์เดียว กว้าง 1600px กึ่งกลาง)

หน้า `/artwork_check` เป็น **คอลัมน์เดียว**: ① อัปโหลด/จัดโซน แล้ว ② ผลการตรวจสอบ
อยู่ล่าง (เดิม 2 คอลัมน์ 7/5 ใน `max-width:1380px` → ① ได้แค่ ~710px ทำงานกับ artwork ใหญ่ไม่ไหว).
`.aw-wrap { max-width:1600px; margin:24px auto }` = **กว้างพอทำงาน แต่ยังอยู่กึ่งกลางจอ**
(เคยลองปลดเพดานเป็นเต็มจอ 1848px แล้ว ผู้ใช้บอกว่าชิดขอบเกินไป). บนจอ 1920 กล่องจัดโซน
กว้าง **1522px (×2.1 ของเดิม)** เว้นขอบข้างละ ~200px → A3 พอดีที่ 61% (เดิมต้องย่อเหลือ 29%).

- **ความกว้างของ panel ไม่เข้าไปในสูตรพิกัดโซนเลย** — โซนเป็นสัดส่วน 0..1, ภาพถูกกำหนดความกว้าง
  เป็น px จาก `applyZoom()` และ `.aw-stage img { max-width:none }` ⇒ กล่องกว้างขึ้น = **เห็นภาพ
  มากขึ้นที่ซูมเท่าเดิม ไม่ใช่ย่อภาพ**. ความละเอียดจึงลดลงไม่ได้เชิงโครงสร้าง.
  พิสูจน์แล้วด้วยเบราว์เซอร์จริง (เทียบก่อน/หลังแก้): วาดโซนที่ zoom 100/60/250% ได้ค่าคลาดเคลื่อน
  **0.781/0.797/0.781 px เท่ากันทุกหลัก** และ zoom mapping (60% → 1488px), wheel-zoom เท่าเดิมเป๊ะ.
- **`.main-content { max-width:1400px }` อยู่ใน `static/css/style.css` = ของกลางทุกหน้า** —
  ปลดเพดานได้เฉพาะใน `{% block extra_css %}` ของ `artwork_check.html` (render เฉพาะหน้านี้)
  **ห้ามแก้ style.css** ไม่งั้นกระทบ Live/Label/Dashboard ทั้งหมด.
- **`.results-wide` + `setResultsWide()` กลายเป็น no-op** (คอลัมน์เดียว) — คงคลาสและจุดเรียกทั้ง 3
  ไว้ตามเดิม ไม่ต้องแตะ JS.
- **แถบเครื่องมือ (`#awZoomBar`) อยู่ "นอก" กล่องภาพ** — เดิมอยู่ข้างในและ `position:sticky; top:0`
  ซึ่งตรึงได้เฉพาะแนวตั้ง ⇒ พอเลื่อนภาพไปทางขวา **ปุ่มไฟล์หลัก/ชิ้นงานหลุดออกนอกจอ**.
  ย้ายออกมาแล้วปุ่มอยู่นิ่งเสมอ (พิสูจน์แล้ว: เลื่อนภาพ 500px แถบยังอยู่ที่เดิมทุกพิกเซล).
- **⚠️ ตอนย้ายแถบออก ทำให้ `zoomRange.closest(".aw-stage-box")` คืน `null`** → wheel-zoom /
  ปุ่มพอดีความกว้าง / การลากเลื่อนภาพ **ตายเงียบพร้อมกันทั้งหมด**. ตอนนี้อ้างกล่องด้วย
  `$("awStageBox")` (id) แทน — **ห้ามกลับไปใช้ `closest()` จาก element ในแถบเครื่องมือ**.
- **ลากเมาส์เพื่อเลื่อนภาพ (pan)** — เปลี่ยนแค่ `scrollLeft/scrollTop` ของ `.aw-stage-box`
  ⇒ **scrollbar เดิมทำงานเหมือนเดิมทุกอย่าง** ไม่แตะสูตรพิกัดโซนเลย. 2 ท่า:
  - **ปุ่มซ้ายลากบนพื้นที่ว่าง** (ไม่ใช่บนโซน + ไม่ได้กด "เพิ่มโซน") — ท่านี้เดิมไม่ทำอะไรเลย
    จึงเอามาใช้ได้โดยไม่ทับของเดิม. โซนมี `stopPropagation` ใน `startDrag` อยู่แล้ว
    event จึงไม่ไหลมาถึง handler ของ pan ตอนลากย้าย/ย่อขยายโซน.
  - **ปุ่มกลาง (ล้อ) ลาก** — ใช้ได้เสมอแม้อยู่บนโซนหรือกำลังวาดโซน. ต้องมี
    `if (ev.button !== 0) return;` ทั้งใน `startDrag` และ handler `mousedown` ของ `stage`
    ไม่งั้นปุ่มกลางจะไป **ย้ายโซน/วาดโซน** แทนที่จะเลื่อนภาพ.
  - `ev.preventDefault()` ใน `panStart` **จำเป็นทั้งสองปุ่ม**: กัน native image drag ของ
    เบราว์เซอร์ (ปุ่มซ้าย) และกัน autoscroll วงกลมของ Windows (ปุ่มกลาง).
  - `canPan()` เช็ค **ทั้งแนวนอนและแนวตั้ง** — กด "พอดีความกว้าง" แล้วภาพยังสูงเกินกล่อง
    (A3 ที่ 72% = 1786x1263 ในกล่องสูง 840) ⇒ ยังต้องลากเลื่อนแนวตั้งได้.
  - `updatePannable()` (เรียกจาก `applyZoom()` + `ResizeObserver` + `window.resize`) คุม
    คลาส `.aw-pannable` = เคอร์เซอร์มือ. ขึ้นมือทั้งที่เลื่อนไม่ได้ = ผู้ใช้ลากแล้วงง.
- ปุ่ม **"⤢ พอดีความกว้าง"** (`#awZoomFit`): `floor(stageBox.clientWidth-6 / natW * 100)` clamp 30-300
  — **ปัดลงเสมอ** (ปัดขึ้น 1% = ภาพล้นกล่อง มี scrollbar แนวนอนทั้งที่กด "พอดี") และต้อง sync
  `zoomPct` + `zoomRange.value` + ป้าย % พร้อมกัน ไม่งั้นสไลเดอร์ค้างคนละค่ากับภาพ.
- ② อยู่ล่างแล้ว → `scrollToResults()` พาจอไปที่ผลตรวจหลังกด "ส่งตรวจสอบ" (ทั้งกรณีสำเร็จและ error)
  ไม่งั้นผู้ใช้ที่อยู่ตรงกล่องจัดโซนจะเหมือนกดแล้วไม่มีอะไรเกิดขึ้น.
- **ยังไม่ได้ทำ (ถ้าอยากให้ภาพ "คมขึ้น" จริงระดับพิกเซล):** `PREVIEW_DPI=150` คือเพดานของภาพใน
  ตัวแก้โซน ซูมเกิน 100% = ขยายภาพเบลอ. ต้องเพิ่มไฟล์ **display-only** แยก (เช่น `PREVIEW_DISPLAY_DPI`
  → `preview_hi.png`) **ห้ามเปลี่ยน `preview.png`** เพราะถูกใช้ต่อโดย `propose_zones` / `snap_bbox` /
  `autopair_bbox` และ `draw_overlay` (ซึ่งใช้ `putText` fontScale คงที่ 0.5 → DPI สูงขึ้น = ป้ายบน
  overlay เล็กลงเชิงสัดส่วน).

---

## 🔐 Artwork — ประวัติการตรวจ "เห็นเฉพาะของตัวเอง"

หน้า `/artwork_check/history` แสดงเฉพาะการตรวจที่ผู้ใช้คนนั้นเป็นคนอัปโหลด
(role ใน `HISTORY_ADMIN_ROLES` เห็นทั้งหมด). **ขอบเขต: โหมด Artwork เท่านั้น** —
Label Paper / Live / Dashboard / `/api/defects` ไม่ถูกแตะ และ **ไม่ต้องแก้ SQL schema เลย**.

- **เจ้าของเก็บใน `owner.json` แยกจาก `report.json`** (`{user_id, username, saved_at}`) เพราะ
  `report.json` เกิดตอนกด "ส่งตรวจสอบ" เท่านั้น แต่ระหว่างจัดโซนมี endpoint ที่ต้องเช็คสิทธิ์แล้ว
  (preview/crop/propose/snap/autopair) — ถ้ารอ report.json ช่วงนั้นจะไม่มีเจ้าของให้เทียบ.
  เขียนตอน `pipeline.start_inspection(owner=...)`; `routes.py` เป็นคนหา user จาก `g.current_user`
  → **`pipeline.py` ไม่ import Flask** (ยังเทสต์ได้ตรง ๆ).
- **ด่านเดียวคุมทุก endpoint: `@artwork_bp.before_request`** อ่าน `rec_id` จาก `request.view_args`
  ⇒ ครอบคลุม **13 route** ที่มี `<rec_id>` ทั้งหมด **รวมถึง route ที่จะเพิ่มในอนาคต**.
  ⚠️ **การกรองเฉพาะรายการ (`/api/artwork/history`) ไม่ใช่การป้องกัน** — ถ้าไม่มีด่านนี้ ใครที่รู้ id
  ก็เปิด `/api/artwork/<id>/report` ของคนอื่นได้ตรง ๆ. เทสต์ `test_http_other_user_blocked_on_every_rec_route`
  **ไล่จาก `url_map` จริง** ไม่ใช่ลิสต์ที่เขียนมือ → เพิ่ม route ใหม่แล้วลืมกัน = เทสต์แดงทันที.
- **นโยบายอยู่ที่เดียวใน `artwork_check/ownership.py`** (ไม่มี Flask): ปิด flag → ผ่านหมด ·
  ไม่มีระบบล็อกอิน (`viewer is None`) → ผ่านหมด · admin → ผ่านหมด · **บันทึกเก่าที่ไม่มี `owner.json`
  → admin เท่านั้น** · เจ้าของ → ผ่าน.
  - `viewer is None` = auth ปิด ≠ `viewer == {}` = auth เปิดแต่หาผู้ใช้ไม่เจอ (**ไม่มีสิทธิ์อะไรเลย**) —
    ต้องเช็คด้วย `is None` ห้ามใช้ความ falsy ไม่งั้นสองเคสนี้จะรวมกันเป็น "ผ่านหมด".
  - เทียบ id ต้อง `bool(oid) and bool(vid) and oid == vid` — ไม่งั้น `"" == ""` ทำให้ทุกคนเป็นเจ้าของ
    ของบันทึกที่ `user_id` ว่าง.
- **`AUTH_ENABLED=False` ต้องไม่กรอง** ไม่งั้นหน้าประวัติว่างเปล่าทั้งที่ระบบทำงานปกติ.
- **ผลข้างเคียงที่ตั้งใจ:** role `Manager`/`Viewer` (ซึ่งมี `view_history`) จะเห็นเฉพาะงานของตัวเอง
  ด้วย — `Viewer` ที่ไม่เคยอัปโหลดจะเห็นตารางว่าง. ถ้าต้องการให้เห็นทั้งหมด เพิ่มชื่อ role ใน
  `ARTWORK_HISTORY_ADMIN_ROLES` (env, คั่นด้วย comma) ไม่ต้องแก้โค้ด.
- **ผูกกับ "ชื่อ" role ตามที่ผู้ใช้เลือก** ⇒ ถ้ามีคนเปลี่ยนชื่อ role `Admin` ในหน้าจัดการผู้ใช้
  **สิทธิ์เห็นทั้งหมดจะหยุดทำงานเงียบ ๆ** ต้องมาแก้ค่าคอนฟิกให้ตรงกัน.
- **ปุ่มลบใช้ด่านเดียวกัน** (DELETE มี `rec_id`) ⇒ เจ้าของ + admin เท่านั้น. JS ไม่ต้องซ่อนปุ่ม
  เพราะรายการที่แสดง = รายการที่ลบได้อยู่แล้ว (server กรองมาให้).
- `list_inspections(limit, can_view=None)` — `can_view=None` = เส้นทางเดิมเป๊ะ. ตอนกรองมีเพดาน
  `_MAX_SCAN=2000` กันผู้ใช้ใหม่ที่ยังไม่มีบันทึกต้องไล่อ่านทั้งคลังทุกครั้ง. เพิ่ม field `owner`
  (ชื่อผู้ตรวจ) ในผลลัพธ์ = คอลัมน์ใหม่ในตาราง (JS `COLS=7` ต้องตรงกับ `<th>` ใน template).
- **Config:** `ARTWORK_HISTORY_PER_USER` (default `true`; ตั้ง `false` + รีสตาร์ต = กลับพฤติกรรมเดิม
  100% ทันที) · `ARTWORK_HISTORY_ADMIN_ROLES` (default `Admin`).
- **ตอน deploy ครั้งแรก:** บันทึกเก่าทั้งหมดจะหายจากสายตาผู้ใช้ทั่วไปทันที (เห็นได้เฉพาะ admin)
  และงานที่ค้างอยู่ระหว่างจัดโซนตอนรีสตาร์ตจะกลายเป็น "ไม่มีเจ้าของ" → เจ้าตัวเปิดต่อไม่ได้
  ต้องอัปโหลดใหม่ ⇒ **ควร deploy ตอนไม่มีคนใช้งาน**.

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
- **`pyspellchecker` = ชั้นตรวจ dictionary/คำขาด (deterministic, เชื่อถือได้)** — จับคำที่ไม่ใช่คำจริง เช่น
  `Sunflow`/`EXPIR`/`Thailan` (คำถูกตัด/สะกดผิด) ในคอลัมน์ "สถานะ". ⚠️ **ถ้า import `spellchecker`
  ไม่ได้ ชั้นนี้ถูกข้ามเงียบๆ** (`_get_spellcheckers()` คืน `[]`) → คำผิดขึ้น ✓ เหมือนไม่มีปัญหา = จุดบอด QC.
  อยู่ใน `requirements.txt` แล้ว. บนสถานีติดตั้งที่ user-site (`%APPDATA%\Python\Python39\site-packages`)
  — **`git pull`/`checkout` ไม่ลบ** (คนละที่กับโฟลเดอร์ repo). เช็ค: `py -3.9 -c "from spellchecker import SpellChecker"`.
  หมายเหตุ: คำขาดที่ "ยังเป็นคำจริง" (เช่น `Sunflower Oil`→`Sunflower`) ไม่มี checker ตัวไหนจับได้ →
  ต้องพึ่งการลากโซนให้ครบ + เทียบ panel. ส่วนคอลัมน์ AI (🤖) เป็น advisory เท่านั้น เชื่อเป็น QC ไม่ได้.
- **`pytesseract` + tesseract binary = ชั้นวาดกรอบแดง (display-only)** — รูปแบบ "หายเงียบ" เดียวกับ
  `pyspellchecker` แต่**ไม่อันตรายเท่า**: ไม่มี = ไม่มีกรอบแดงบนไฟล์ outline/ภาพถ่าย แต่ผลตรวจ QC
  เท่าเดิมทุกอย่าง (ไม่ใช่จุดบอด QC). เช็ค: `py -3.9 -c "import pytesseract; print(pytesseract.get_tesseract_version())"`.
  ต่างจาก pyspellchecker ตรงที่ **ต้องลง binary แยกจาก pip** (ดูหัวข้อ Artwork กรอบแดง).
- **⚠️ Deploy IIS ในอนาคต (ยังไม่ทำ — บันทึกไว้ก่อน):** package ที่ลง user-site ของ dev
  **IIS Application Pool identity เข้าไม่ถึง** → ชั้น dict หายเงียบใน production. วิธีแก้ตอน deploy คือทำ
  **venv ในโฟลเดอร์โปรเจกต์** (`py -3.9 -m venv .venv` + `pip install -r requirements.txt`) แล้วชี้ IIS
  FastCGI/HttpPlatform ไปที่ `.venv\Scripts\python.exe` (package อยู่กับโค้ด ทุก identity เห็นเท่ากัน).
  **ระหว่างพัฒนาบนสถานีไม่ต้องทำ venv** — จะแยกเป็น 2 environment ทำให้สับสน + ดึง accel คนละชุด.
  **⚠️ ก่อนสร้าง venv ต้อง pin accel ก่อน:** `requirements.txt` ปัจจุบัน **ไม่ตรงกับ stack ที่จูนไว้**
  (`onnxruntime==1.19.2` comment ทิ้ง, `openvino` ไม่ pin เป็น 2024.6.0) — venv สดจะได้ accel ต่างจาก
  สถานี = อาจตรวจช้าลงหรือเจอ bug OpenVINO 2025 ตรวจไม่เจอแบบเงียบๆ (ดูหัวข้อ OpenVINO ด้านบน).

---

## 🧰 สภาพแวดล้อม & repo

- HW สถานี: **i7-1165G7** (4C/8T, 15W, AVX-512), 16GB DDR4 (single-channel), Iris Xe, Win10 Pro, Python 3.9.13.
- inference bestX (seg): **iGPU (OpenVINO) ≈ 45-50ms/เฟรม (~20-22 FPS)** = ตัวจริงปัจจุบัน;
  ONNX CPU ≈ 280ms (~2.7-3 FPS) = ชั้น fallback; PyTorch ≈ 315ms = fallback สุดท้าย.
- Repo: `iceamonwat09/digital_vision2026`. Dev branch ปัจจุบัน: `claude/hikrobot-lan-camera-integration-psg74g`
  (ก่อนหน้า: `claude/artwork-ui-layout-1lnwgt`). **ห้าม push ไป main**.
- SQL Server: 172.32.0.50/VisionIQ. Defect log ผ่าน `sp_log_defect` (เก็บภาพ base64).
- Tests: `pytest tests/` — **379 ตัว** (artwork/label/barcode/กล้อง Hikrobot —
  **ยังไม่ครอบคลุม camera.py/live loop ของ USB**).
  ก่อนหน้า: `tests/test_artwork_ownership.py` 26 ตัว (สิทธิ์เห็นประวัติ artwork).
  เพิ่มล่าสุด: `tests/test_hik_camera.py` **36 ตัว** (กล้อง Hikrobot GigE — ใช้ fake MVS SDK
  จึงรันได้โดยไม่ต้องมีกล้อง; 3 ตัวที่ import `app` จะ skip ถ้าไม่มี ultralytics/pyodbc).
  **baseline ที่วัดจริง ส.ค. 2026: ก่อนแก้ 310 passed / หลังแก้ 343 passed (+8 ตัวของ burst ที่ skip ถ้าไม่มี ultralytics) — fail 5 ตัวเท่าเดิม.**
  ⚠️ `tests/test_inspection_golden.py` **fail 5 ตัวอยู่แล้ว** (pre-existing, `NameError: FieldResult`
  ในโมดูล Label Paper) — ไม่เกี่ยวกับ artwork. ยืนยันด้วย `git stash` ก่อนโทษการแก้ของตัวเอง.
- CONFIG_VERSION ปัจจุบัน: **`2026.08.13-hik-gige-snapshot`** (เช็คที่ footer ว่ารันโค้ดใหม่จริง).

---

## ✅ Checklist ก่อน commit

- [ ] flag ใหม่ default = พฤติกรรมเดิม? scope เฉพาะโหมดที่ตั้งใจ?
- [ ] fallback ครบทุกทางที่อาจล้มเหลว?
- [ ] การนับ/DB logging เดิมไม่ถูกแตะ? (ถ้าแตะ inference_loop ให้ไล่ดู)
- [ ] `python -c "import ast; ast.parse(open('app.py').read())"` ผ่าน?
      (แตะ JS ด้วย → `node --check static/js/<ไฟล์>.js`)
- [ ] แตะ JS ที่อ้าง element ใหม่ → **เพิ่ม element ใน `templates/` แล้วหรือยัง**?
      (`$("id")` ที่ไม่มีจริงจะเงียบ ไม่ error — ฟีเจอร์หายไปเฉยๆ)
- [ ] ค่าคงที่ที่ใช้ทั้ง Python และ JS แก้ครบสองฝั่งหรือยัง? (เช่น `zones.HL_*` ↔ `HL_*`)
- [ ] bump `CONFIG_VERSION` ถ้าผู้ใช้ต้อง verify?
- [ ] ถ้าแตะชั้นที่ "ชี้จุดให้คนดู" — เคสไม่มั่นใจ **ไม่แสดง** แทนที่จะเดา? (กฎเหล็ก 2)
- [ ] dependency ใหม่เป็น optional + auto-fallback? (ไม่มี = ฟีเจอร์หาย ไม่ใช่ระบบพัง)
- [ ] commit message ชัด + push ไป dev branch (ไม่ใช่ main)?
