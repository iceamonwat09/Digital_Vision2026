# Thai Union — ระบบตรวจสอบบรรจุภัณฑ์ (Packaging Inspection System)

ระบบตรวจสอบบรรจุภัณฑ์แบบครบวงจรสำหรับสายการผลิต Thai Union รวม **3 โหมด**
ไว้ในเว็บแอปเดียว โดยแต่ละโหมดทำงานแยกอิสระจากกัน (โหมดหนึ่งล่มไม่กระทบอีกโหมด):

| โหมด | งานที่ตรวจ | เทคนิคหลัก |
|---|---|---|
| **ตรวจจับสด** (Live Detection) | ตำหนิกระป๋อง/บรรจุภัณฑ์แบบเรียลไทม์จากกล้อง | YOLOv8 (Ultralytics) |
| **ตรวจฉลากกระดาษ** (Label Paper) | เทียบฉลากที่พิมพ์จริงกับ master PDF (สี ΔE2000 + ข้อความ) | PyMuPDF + scikit-image + OCR |
| **ตรวจ Artwork** (Artwork Proof Check) | สะกดคำ / ตัวเลข / ความตรงกันของ panel บนไฟล์ artwork ก่อนพิมพ์ | OCR (N8N→Gemini) + 4 ชั้นตรวจ + คำแปล |

> เดิมโปรเจกต์นี้เริ่มจากระบบตรวจขวดน้ำด้วย YOLO และถูกพัฒนาต่อเป็นระบบตรวจ
> บรรจุภัณฑ์หลายโหมดของ Thai Union ฐานข้อมูลย้ายจาก MongoDB → **SQL Server (VisionIQ)**

---

## สารบัญ

1. [สถาปัตยกรรมระบบ](#สถาปัตยกรรมระบบ)
2. [การติดตั้ง](#การติดตั้ง)
3. [การตั้งค่า (config.py)](#การตั้งค่า-configpy)
4. [การรันแอป](#การรันแอป)
5. [โหมดที่ 1 — ตรวจจับสด (YOLO)](#โหมดที่-1--ตรวจจับสด-yolo)
6. [ถ่ายรูปตรวจ (Snapshot Inspection)](#ถ่ายรูปตรวจ-snapshot-inspection)
7. [โหมดที่ 2 — ตรวจฉลากกระดาษ (ΔE2000)](#โหมดที่-2--ตรวจฉลากกระดาษ-δe2000)
8. [โหมดที่ 3 — ตรวจ Artwork (OCR + 4 ชั้น)](#โหมดที่-3--ตรวจ-artwork-ocr--4-ชั้น)
9. [การตั้งค่า N8N → Gemini](#การตั้งค่า-n8n--gemini)
10. [API Endpoints](#api-endpoints)
11. [โครงสร้างโปรเจกต์](#โครงสร้างโปรเจกต์)
12. [การปรับแต่งกล้อง & แก้ปัญหา (Camera Tuning & Troubleshooting)](#การปรับแต่งกล้อง--แก้ปัญหา-camera-tuning--troubleshooting)
13. [สรุปการปรับปรุงล่าสุด (Changelog)](#สรุปการปรับปรุงล่าสุด-changelog)
14. [Test](#test)

---

## สถาปัตยกรรมระบบ

### Technology Stack

- **Backend**: Python 3.8+, Flask, OpenCV
- **Detection**: Ultralytics YOLOv8 (โหมดตรวจจับสด)
- **Label/Artwork**: PyMuPDF (render PDF + text layer), scikit-image (ΔE CIEDE2000),
  python-Levenshtein, pyspellchecker
- **OCR / แปลภาษา**: N8N webhook ที่หน้า Gemini 2.5 Flash (Vertex AI หรือ AI Studio)
- **Database**: SQL Server 2014 (`pyodbc`) — เก็บประวัติการตรวจ
- **Frontend**: HTML5 / CSS3 / JavaScript (Jinja2 templates), Chart.js
- **Typography**: IBM Plex Sans Thai + IBM Plex Mono (มี fallback ฟอนต์ระบบเมื่อ offline)

### หลักการออกแบบสำคัญ

- **แยกโหมดเด็ดขาด** — โหมด Artwork ลงทะเบียนเป็น Flask blueprint ใน `try/except`
  ความผิดพลาดในโหมดนี้ปิดได้เฉพาะตัวมันเอง ไม่ล้มโหมด Can Dent / Label
- **ข้อมูลแต่ละโหมดแยกโฟลเดอร์** — `data/artwork_check/` ลบทิ้งทั้งก้อนได้โดยไม่กระทบฟีเจอร์อื่น
- **กฎเหล็กของโหมด Artwork**: ระบบ **ห้ามเดา/เสนอคำเองในส่วนที่ตัดสิน PASS/FAIL**
  — การตรวจบอกได้แค่ "คำนี้ไม่อยู่ในพจนานุกรม" ส่วนคำแปล/คำแนะนำเป็น advisory แยกแท็บ

---

## การติดตั้ง

### Prerequisites

1. **Python 3.8+**
2. **SQL Server** (สำหรับเก็บประวัติ — ดู `Connection_sql/`)
3. **ODBC Driver for SQL Server** (เช่น `msodbcsql17`/`18`)
4. **กล้อง USB หรือ IP (RTSP)** สำหรับโหมดตรวจจับสด
5. **N8N + Gemini** (สำหรับโหมด Artwork / ฟีเจอร์ OCR) — ดูหัวข้อ N8N

### ติดตั้ง dependencies

```bash
pip install -r requirements.txt
```

แพ็กเกจเสริม (ไม่บังคับ — ระบบทำงานได้โดยข้ามฟีเจอร์ที่เกี่ยวข้องถ้าไม่ติดตั้ง):

```bash
pip install pyspellchecker      # โหมด Artwork: ชั้นตรวจ dictionary (SPELL_FAIL)
apt-get install libzbar0 && pip install pyzbar   # ถอดบาร์โค้ด EAN-13 แม่นขึ้น
```

---

## การตั้งค่า (config.py)

แก้ไฟล์ `config.py` (ค่าที่ใช้บ่อย):

```python
# ── กล้อง (live stream) ──────────────────────────────
CAMERA_INDEX  = 0          # index กล้อง USB (รัน /api/camera/scan เพื่อหา)
CAMERA_WIDTH  = 640        # ความละเอียดสตรีมสด (640x480 ลด rolling-shutter)
CAMERA_HEIGHT = 480
CAMERA_FPS    = 30
CAMERA_FOURCC = None       # None = YUY2 (สะอาด, แนะนำ) | "MJPG" = บีบอัด (เสี่ยงภาพแตกบาง MSMF)
CAMERA_AUTO_EXPOSURE = None # None=ไม่แตะ | False=manual (ล็อกเอง) | True=auto — เฉพาะกล้อง live
CAMERA_EXPOSURE      = None # ค่ารับแสง (เช่น -6/-7/-8) — ⚠️ กล้องสถานีไม่รับ EXPOSURE ผ่าน OpenCV
CAMERA_BRIGHTNESS    = None # None=ไม่แตะ | 0-255 = ตั้งความสว่าง (knob ที่กล้องนี้คุมได้จริง)
CAMERA_CONTRAST      = None # None=ไม่แตะ | 0-255 = คอนทราสต์ (⚠️ ทดลอง ไม่การันตีตรวจดีขึ้น)
                            # ทั้งคู่ปรับสดขณะรันได้จากสไลเดอร์ในแผงกล้อง USB (POST /api/camera/control)

# ── AI acceleration (ONNX Runtime — เร่ง CPU คงความแม่น FP32) ─
USE_ONNX   = True          # export .pt→.onnx ครั้งเดียว รันผ่าน onnxruntime (~2x). fallback→PyTorch
ONNX_OPSET = 17            # ⚠️ Python 3.9: ต้อง `pip install onnxruntime==1.19.2` (1.20+ ตัด py39)

# ── การแสดงผลวิดีโอสด USB/RTSP ───────────────────────
LIVE_SMOOTH_VIDEO = False  # False=ล็อกกรอบเป๊ะ | True=ลื่น. 📌 เปิด Frame Capture = บังคับลื่นอัตโนมัติ

# ── ถ่ายรูปตรวจ (snapshot) ───────────────────────────
SNAPSHOT_IMGSZ           = 1280   # imgsz ตอน detect (สูงกว่า live เพราะรันครั้งเดียว)
SNAPSHOT_MAX_FRAME_AGE_S = 1.0    # เฟรมเก่ากว่านี้ = ปฏิเสธการถ่าย (กันตัดสินภาพค้าง)
SNAPSHOT_QUALITY_DEFAULT = "smooth"   # ค่าเริ่มต้น: smooth(720p)/balanced(1080p)/sharp(5MP)
VIEWFINDER_STREAM_FPS    = 30     # fps สตรีม viewfinder (สูงกว่า live ให้เล็งลื่น)

# ── SQL Server (เก็บประวัติ) ─────────────────────────
SQL_SERVER   = "172.32.0.50"
SQL_DATABASE = "VisionIQ"
SQL_USER     = "sa"
SQL_PASSWORD = "********"

# ── Flask ────────────────────────────────────────────
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5000   # override ได้ผ่าน env var FLASK_PORT (ไม่ต้องแก้โค้ด)
FLASK_DEBUG = False

# ── HTTPS (จำเป็นสำหรับโหมด STREAM / กล้องของ Client) ─
USE_HTTPS     = True       # getUserMedia ต้องการ secure context (https/localhost)
SSL_CERT_FILE = "certs/cert.pem"   # สร้างด้วย: python generate_cert.py <ip>
SSL_KEY_FILE  = "certs/key.pem"

# ── STREAM source (กล้องของ Client ผ่านเบราว์เซอร์) ──
STREAM_INFER_FPS   = 10    # เพดานอัตราเรียก /api/stream/infer (จริงจำกัดด้วยความเร็ว CPU)
STREAM_JPEG_QUALITY = 0.92 # คุณภาพ JPEG ที่เบราว์เซอร์ส่งขึ้น (สูง = กรอบแม่นขึ้น)
STREAM_MAX_WIDTH   = 640   # ความกว้างเฟรม live ก่อนส่ง
STREAM_INFER_IMGSZ = 480   # imgsz ตรวจสตรีมสด = เท่า USB. ⚠️ ต่ำกว่า 480 จะตรวจ dent ไม่เจอ

# ── Frame Capture (แช่ภาพเฟรมคมที่สุดเมื่อเจอ NG — USB/RTSP) ─
FRAME_CAPTURE_HOLD_SEC    = 3      # แช่ภาพกี่วินาที (สายพานถี่ให้ลดลง)
FRAME_CAPTURE_EDGE_MARGIN = 0.02   # กระป๋องต้องห่างขอบภาพเท่านี้จึงนับ "ครบใบ" (0=แค่ไม่หลุดขอบ)
```

> **CAMERA_FOURCC**: บน Windows (MSMF) การบังคับ `"MJPG"` อาจทำให้ JPEG ออกมาไม่ครบ →
> ภาพแตกเป็นคลื่นสีรุ้ง ค่าเริ่มต้นจึงเป็น `None` (YUY2 สะอาดกว่า) เปิด `"MJPG"` เฉพาะ
> เครื่องที่ทดสอบแล้วว่าให้ภาพสะอาดและต้องการความละเอียด >720p — ดูหัวข้อ
> [การปรับแต่งกล้อง & แก้ปัญหา](#การปรับแต่งกล้อง--แก้ปัญหา-camera-tuning--troubleshooting)

### Environment variables (โหมด Artwork / OCR)

ตั้งผ่าน env ได้โดยไม่ต้องแก้โค้ด:

| ตัวแปร | ค่า default | หน้าที่ |
|---|---|---|
| `N8N_OCR_WEBHOOK_URL` | `http://172.32.201.106:5678/webhook/artwork-ocr` | OCR ถอดข้อความจากภาพโซน |
| `N8N_TRANSLATE_WEBHOOK_URL` | `http://172.32.201.106:5678/webhook/artwork-translate` | แปลข้อความเป็น EN (แท็บคำแปล) |
| `OCR_BACKEND` | (ว่าง) | `n8n` / `stub` / `vertex` — ว่างไว้ระบบเลือก n8n ให้เมื่อมี URL |
| `ARTWORK_OCR_DPI` | `450` | ความละเอียดที่ render โซนส่ง OCR |
| `ARTWORK_PREVIEW_DPI` | `150` | ความละเอียด preview ในเบราว์เซอร์ |
| `ARTWORK_SPELL_LANGS` | `en,es,fr,de,pt,it,ru,ar` | ภาษาที่ชั้น dictionary ตรวจ (มีผลต่อ verdict) |

---

## การรันแอป

**ค่าเริ่มต้น / ใช้งานจริงบนสถานี — แนะนำ:**
```bash
python app.py
```
เปิดเบราว์เซอร์ไปที่ `http://localhost:5000` (หรือ `http://<ip เครื่อง>:5000` — port ตาม `FLASK_PORT`)
รองรับ HTTPS ด้วยเช่นกัน (ดูด้านล่าง) — แค่ตั้ง `USE_HTTPS = True` แล้วรันคำสั่งเดิม
`app.py` ใช้ `threaded=True` อยู่แล้ว จึงรับหลาย client พร้อมกันได้ และตั้งแต่โหมด
STREAM เปลี่ยนมาเป็น request/response สั้นๆ ต่อเฟรม (`POST /api/stream/infer`,
ไม่ใช่ connection ค้างยาวแบบ MJPEG) ภาระต่อ connection จึงลดลงมากเทียบกับตอนแรกที่ออกแบบ
`run_server.py` ไว้แก้ปัญหานี้

**ทางเลือก (gevent) — สำหรับ deploy หนักจริง/หลายกล้อง/ผู้ใช้พร้อมกันจำนวนมาก:**
```bash
python generate_cert.py 172.32.201.106   # 1) สร้าง self-signed cert (ครั้งเดียว)
# 2) ตั้ง USE_HTTPS = True ใน config.py
python run_server.py                      # 3) รันบน gevent (ทน connection ค้างจำนวนมากในโปรเซสเดียว)
```
เปิด `https://<ip>:5000` (กดผ่านหน้าเตือน self-signed ครั้งแรก)

> **`app.py` หรือ `run_server.py` ใช้ตัวไหนดี?** เดิมตั้งใจให้ `run_server.py`
> (gevent) เป็นค่าแนะนำ เพราะตอนนั้น `/video_feed` (MJPEG) เป็น connection ค้างยาว
> ทุกแท็บที่เปิดไว้ + HTTPS หลาย client ทำให้ dev server ของ Flask ค้าง/timeout จริง.
> แต่หลังจากย้ายโหมด STREAM ไปใช้ `POST /api/stream/infer` (request/response สั้นๆ
> ต่อเฟรม แทนการค้าง connection) ภาระแบบเดิมก็ลดลงมาก — สำหรับใช้งานจริงที่ผ่านมา
> (1 สถานี/กล้องเดียว) **`app.py` รันได้ผลเหมือนกัน** ไม่ต้องพึ่ง `run_server.py` ก็ได้.
> เก็บ `run_server.py` ไว้เป็นทางเลือกสำหรับ deploy ที่มีหลายกล้อง/ผู้ชม MJPEG
> (`/video_feed` ของ USB/RTSP) พร้อมกันจำนวนมาก ซึ่งยังเป็น connection ค้างยาวอยู่
> เหมือนเดิม — ถ้าใช้แล้วรันไม่ขึ้น (เช่น import error) ให้ดูข้อความ error เต็มใน
> console ก่อน ส่วนใหญ่เกิดจาก `gevent` ถูกติดตั้งคนละ Python interpreter กับตัวที่ใช้
> รัน (ดู [ตารางอาการ → สาเหตุ → วิธีแก้](#ตารางอาการ--สาเหตุ--วิธีแก้))
>
> โหมด STREAM (กล้องของ Client ผ่าน `getUserMedia`) **บังคับ HTTPS** เสมอ ไม่ว่าจะรัน
> ด้วย `app.py` หรือ `run_server.py`.

เมนูบนสุด: **ตรวจจับสด · ตรวจฉลากกระดาษ · ตรวจ Artwork · แดชบอร์ด · ประวัติ**

---

## โหมดที่ 1 — ตรวจจับสด (YOLO)

ตรวจตำหนิบรรจุภัณฑ์แบบเรียลไทม์จากกล้อง

**หน้าใช้งาน** (`/`) จัดเป็นแผงควบคุม 3 ขั้น:
1. **โหมดตรวจสอบ** — เลือกโมเดล (`can_dent` / `label`) และไฟล์ `.pt`
2. **แหล่งสัญญาณภาพ** — มี 3 แบบ: **กล้อง USB** · **กล้อง IP (RTSP)** · **สตรีม**
3. **เริ่ม/หยุด** — Start/Stop Detection พร้อมสถิติ Active/Total

**โมเดล** เก็บไฟล์ `.pt` ในโฟลเดอร์ weights ต่อโหมด (ดู `modes/registry.py`)
สลับโมเดล/โหมดได้จาก UI ไม่ต้องรีสตาร์ต

### แหล่งสัญญาณภาพ "สตรีม" — กล้องของเครื่อง Client (per-client isolation)

ให้ผู้ใช้แต่ละคนเปิด **กล้องของเครื่องตัวเอง** ผ่านเบราว์เซอร์ (`getUserMedia`) แทน
การใช้กล้องที่เสียบกับ Server โดยออกแบบให้ **แยกขาดต่อคน**:

```
[กล้อง browser ของแต่ละคน] → <video> ในเครื่องตัวเอง (ลื่น native)
   → ส่งเฟรม (throttle + single-in-flight) → POST /api/stream/infer
   → server ตรวจเฟรมนั้น (lock กันชนกันระหว่าง client; ใช้ gevent threadpool เพิ่ม
     ถ้ารันด้วย run_server.py) → คืน "พิกัดกรอบ JSON"
   → วาดกรอบบน <canvas> ทับวิดีโอของตัวเอง  →  เห็นแต่กล้องตัวเอง
```

- **แยกต่อ client โดยอัตโนมัติ** — เป็น request/response ผลกลับไปหาคนที่ส่งมาเท่านั้น
  (ไม่แชร์กล้อง/`/video_feed`/global pipeline กับ USB/RTSP)
- **บังคับ HTTPS** — `getUserMedia` ทำงานเฉพาะ secure context (ดู `generate_cert.py`)
- **Snapshot** ในโหมดสตรีมก็ใช้กล้อง Client (`POST /api/stream/snapshot`)
- มีตัวเลข **FPS/latency** มุมจอช่วยดูว่าเน็ต/เครื่องไหวแค่ไหน
- จูนได้ที่ค่าคงที่ `STREAM_*` ใน `templates/index.html` (สะท้อนใน `config.py`)

> หลักการ: นำ *per-stream isolation + worker-pool + process-latest* ของระบบ
> machine-vision มืออาชีพมาใช้แบบย่อ — รองรับ 1 กล้องตอนนี้ และขยายเป็น 2–3
> กล้องได้โดยลด `STREAM_INFER_FPS`.

### Frame Capture — แช่ภาพเฟรมที่ "คมที่สุด + ครบใบ" ต่อกระป๋อง NG (USB/RTSP)

checkbox ในแผงกล้อง USB — สำหรับสายพานที่กระป๋องวิ่งผ่าน: เมื่อกระป๋อง NG ผ่านพ้นไป
ระบบจะ **แช่แสดง "เฟรมที่ดีที่สุด" ของใบนั้นค้างไว้ `FRAME_CAPTURE_HOLD_SEC` วินาที**
แล้วกลับไปแสดงสด. เป็นการ **แสดงผลอย่างเดียว — ไม่กระทบการนับ/บันทึก DB**.

หลักการเลือก "เฟรมที่ดีที่สุด" (แก้ motion blur บนสายพาน):
1. **Candidate pooling** — `capture_loop` ให้คะแนนความคม (variance of Laplacian)
   ทุกเฟรมที่กล้องถ่าย (~อัตรากล้อง ~30fps) ไม่ใช่แค่เฟรมที่ผ่าน inference (~2–3fps)
   → เลือกจากผู้สมัครมากขึ้น ~10 เท่า
2. **ครบใบ (completeness)** — เก็บ/ให้แต้มเฉพาะเฟรมที่ **กล่องคลาส `can` (กระป๋องทั้งใบ)
   อยู่ในภาพครบ ไม่ชนขอบ** (ระยะห่าง `FRAME_CAPTURE_EDGE_MARGIN`) → ไม่ได้ภาพครึ่งใบ
   ตอนกระป๋องเข้า/ออกเฟรม
3. **กรอบตรงเป๊ะ** — พอเลือกเฟรมได้ **รัน detection ซ้ำบนเฟรมนั้น 1 ครั้ง** เพื่อให้กรอบ
   ตรงกับเฟรมที่แสดงพอดี (เฟรม pool ไม่เคยผ่าน inference มาก่อน)
4. **Fallback หลายชั้น** — ถ้า pool ว่าง/กระป๋องใหญ่กว่าเฟรมจนครบใบไม่ได้ → เลือกเฟรม
   inferred ที่ดีที่สุดเท่าที่มี (ไม่ค้าง). best.pt (ไม่มีคลาส `can`) → ข้ามเช็คครบใบ.

> จูน motion blur ที่ต้นตอได้ด้วย `CAMERA_AUTO_EXPOSURE=False` + `CAMERA_EXPOSURE=-7`
> (ล็อก exposure สั้น) **พร้อมเพิ่มไฟส่อง** — ทำให้ทุกเฟรมคมตั้งแต่ต้น (ดูหัวข้อ config).

---

## ถ่ายรูปตรวจ (Snapshot Inspection)

โหมด **"📷 ถ่ายรูปตรวจ"** (ขั้นที่ 4 บนหน้าตรวจจับสด) — แทนที่จะตรวจสตรีมต่อเนื่อง
ผู้ใช้ **เล็งกระป๋องให้นิ่ง → กดชัตเตอร์ → ระบบรันโมเดล 1 ครั้งที่ความละเอียดสูง →
แสดงผล OK/NG + ภาพพร้อมกรอบ** เหมาะกับการตรวจทีละชิ้นแบบแม่นยำ (ใช้ `imgsz` สูงกว่า
live เพราะรันครั้งเดียว ไม่ต้องห่วง fps)

> ใช้กล้องตัวเดียวกับตรวจจับสด จึง **exclusive กัน** — ต้องกด **Stop Detection** ก่อน
> จึงจะเปิดโหมดถ่ายรูปได้ และระบบบังคับเงื่อนไขนี้ให้อัตโนมัติ

### ขั้นตอนใช้งาน

```
Stop Detection → 📷 ถ่ายรูปตรวจ → (viewfinder เล็ง) → 📸 ถ่าย → ผล OK/NG + ภาพ
```

### สถาปัตยกรรม "เปิดกล้องครั้งเดียว" (Single-handle)

หัวใจที่ทำให้ถ่ายได้เสถียร: **เปิดกล้องครั้งเดียว** ตอนเข้าโหมด แล้วใช้แฮนเดิลเดียวนั้น
ทั้งการเล็ง (viewfinder) และตอนกดชัตเตอร์ — **ชัตเตอร์ไม่แตะ handle กล้องเลย** แค่หยิบ
เฟรมล่าสุดที่เธรด viewfinder จับไว้ใน buffer

> เวอร์ชันแรกใช้วิธี release กล้อง 720p แล้ว reopen ที่ 5MP ตอนกดชัตเตอร์ — แต่ไดรเวอร์
> UVC หลายตัว reopen ไม่ติด/ช้า ทำให้ **"ถ่ายไม่สำเร็จ"** จึงเปลี่ยนมาเป็น single-handle
> ที่ตัดการ reopen ทิ้งทั้งหมด

### เลือกความคมชัด ↔ ความลื่น (Quality presets)

มี dropdown ในหน้าต่างถ่ายรูป (จำค่าไว้ใน `localStorage` เปลี่ยนได้ทันที):

| ตัวเลือก | ความละเอียด | เหมาะกับ |
|---|---|---|
| **ลื่นที่สุด** (default) | 720p@30 | เล็งนิ่มสุด — ค่าเริ่มต้น เสถียรกับทุกกล้อง |
| **สมดุล** | 1080p@30 | ลื่น + คมพอจับ dent |
| **คมชัด** | 5MP@15 | ภาพคมสุด (เล็งกระตุกเพราะ fps ต่ำ) |

ระบบจะ **ไล่ลองจากที่เลือกลงมา** (ladder) — ถ้ากล้องเปิดความละเอียดนั้นไม่ได้ จะตกลง
ระดับถัดไปอัตโนมัติจนกว่าจะเปิดได้ (ไม่ค้างที่ error) ผลตรวจจะแสดง **"ความละเอียดภาพ"**
ที่ได้จริงเสมอ

### Staleness guard — กันตัดสินจากภาพเก่า (QC-safe)

ถ้ากล้องค้าง/หลุดสาย เธรด viewfinder จะหยุด publish เฟรมใหม่ → เฟรมใน buffer "เก่า"
ระบบจะ **ปฏิเสธการถ่าย** (ตอบ error ว่าภาพไม่อัปเดต) แทนการตัดสิน OK/NG จากภาพเก่า —
เพราะระบบ QC ห้ามให้ verdict จากภาพที่ไม่ใช่ปัจจุบัน เกณฑ์อายุตั้งที่
`SNAPSHOT_MAX_FRAME_AGE_S` (default `1.0` วินาที)

---

## โหมดที่ 2 — ตรวจฉลากกระดาษ (ΔE2000)

เทียบฉลากที่พิมพ์จริง (ถ่ายจากกล้อง/สแกน) กับ **master PDF** ที่ approve แล้ว

- **สี**: เทียบ per-pixel ด้วย ΔE CIEDE2000 (`scikit-image`) หลัง registration/perspective
- **ข้อความ**: OCR แล้วเทียบด้วย Levenshtein + diff
- **บาร์โค้ด**: ถอดและตรวจ check digit
- **Visual diff**: (ตัวเลือก) ให้ Gemini เทียบ 2 ภาพผ่าน N8N — เปิด/ปิดด้วย `VISUAL_DIFF_ENABLED`

master PDF ต่อ SKU เก็บใน `data/label_paper/skus/` (ดู README ในโฟลเดอร์นั้น)

---

## โหมดที่ 3 — ตรวจ Artwork (OCR + 4 ชั้น)

โหมดหลักที่พัฒนาเข้มข้น — ตรวจไฟล์ **artwork ก่อนส่งโรงพิมพ์** ว่าสะกดถูก
ตัวเลขสอดคล้อง และ panel ที่ควรเหมือนกันตรงกันจริง

### ขั้นตอนใช้งาน

```
อัปโหลด PDF/PNG/JPG → จัดโซน → ส่งตรวจ → อ่านรายงาน (+ แท็บคำแปล)
```

1. **อัปโหลดไฟล์** — ระบบ render preview และ **เสนอโซนอัตโนมัติ**
2. **จัดโซน** บน preview:
   - **ลากวาดกรอบ** บนภาพเพื่อสร้างโซนใหม่ (กดปุ่ม "เพิ่มโซน" แล้วลากเมาส์)
   - **ดับเบิลคลิกที่โซน** → ระบบขยับกรอบให้พอดีเนื้อหาเอง (snap-to-content,
     คลิกซ้ำได้ถ้ายังไม่พอดี — รองรับทั้งฉลากพื้นสว่างและพื้นเข้ม)
   - **ลบโซนทั้งหมด** ได้ในคลิกเดียว
   - กำหนด **type** ต่อโซน: `panel` (ฉลากจริง) / `zoom` (ส่วนขยาย) /
     `header` (ฟอร์มโรงพิมพ์) / `ignore` (ไม่ตรวจ)
   - **group** — โซนกลุ่มเดียวกัน = ข้อความต้องตรงกัน (ใช้โหวตหา panel ที่สะกดต่าง)
   - **บันทึก template** ต่อ layout โรงพิมพ์ — ครั้งหน้าโหลดมาใช้ซ้ำได้เลย
3. **ส่งตรวจ** — ระบบ OCR ทีละโซน (PDF text layer ถ้ามี ไม่งั้นยิง N8N→Gemini)
   แล้วรันทุกชั้นตรวจ
4. **อ่านผล** ในแผงขวา มี 2 แท็บ:
   - **🔍 ผลตรวจ** — verdict (PASS/REVIEW/FAIL), สรุปต่อชนิด, overlay ไฮไลต์โซนผิด,
     รายการ defect พร้อมภาพ crop ความละเอียดสูง
   - **📖 ข้อความ + คำแปล** — ตารางต่อบรรทัด: ข้อความบนฉลาก | คำแปล EN | สถานะ
     (ไฮไลต์คำที่สะกดน่าสงสัย + คำแนะนำ) — กดปุ่ม "แปล/อธิบาย" เพื่อแปล

### 4 ชั้นตรวจ (`artwork_check/checks.py`)

| ชั้น | คลาส defect | ตรวจอะไร |
|---|---|---|
| 1 | `MISMATCH_PANELS` | panel กลุ่มเดียวกันข้อความไม่ตรง (โหวตเสียงข้างมาก) |
| 1b | `MISMATCH_ZOOM` | ส่วน zoom ไม่ตรงกับฉลากจริง |
| 2 | `NUMBER_FAIL` | น้ำหนัก (count×oz, LBS↔kg, OZ↔g), check digit บาร์โค้ด GS1 |
| 3 | `SPELL_FAIL` / `PHRASE_FAIL` | คำไม่อยู่ใน dictionary / วลีแบรนด์สะกดเพี้ยน |
| 4 | `UNREADABLE` | OCR อ่านไม่ชัด (conf ต่ำ) — ขอให้คนดูเอง |

**ทนต่อ noise ของ OCR**: ชั้นเทียบจะให้อภัยความต่างของ *เครื่องหมาย/ช่องว่าง/
เลขอารบิก↔อารบิก* (เช่น `EL - OBOUR` vs `ELOBOUR`, `٧٠٪` vs `%70%`) แต่ยังจับ
ความต่างระดับ *ตัวอักษร/ตัวเลขจริง* (`16785` vs `16786`, `CALIDAD` vs `CALIDDD`)

### กฎเรื่องการเสนอคำ (สำคัญ)

ระบบ **ไม่เดาคำในส่วนที่ตัดสินผล** คอลัมน์ "คำที่ควรใช้" ในแท็บคำแปลมาจากแหล่ง
**deterministic** เท่านั้น (พจนานุกรม pyspellchecker + คลังคำแบรนด์ + เสียงข้างมาก
ของ panel) ไม่ใช่จากตัวแปล LLM — เป็นข้อมูล advisory ไม่มีผลต่อ PASS/FAIL

### คลังคำแบรนด์ (Master Vocabulary)

ต่อแบรนด์เก็บได้ 2 อย่าง: **คำเฉพาะ** (ไม่ถูกฟ้องว่าสะกดผิด) และ **วลีมาตรฐานที่
approve** (ถ้าบน artwork สะกดเพี้ยนจะถูกฟ้อง `PHRASE_FAIL`)

---

## การตั้งค่า N8N → Gemini

โหมด Artwork ใช้ N8N **2 workflow** มีไฟล์พร้อม import ในโฟลเดอร์ `artwork_check/`:

| ไฟล์ | path | หน้าที่ | จำเป็น |
|---|---|---|---|
| `n8n_artwork_ocr.workflow.json` | `/webhook/artwork-ocr` | OCR ถอดข้อความ (verbatim) | **ใช่** |
| `n8n_artwork_translate.workflow.json` | `/webhook/artwork-translate` | แปล EN (แท็บคำแปล) | ทางเลือก |

**คู่มือ import + ตั้ง credential + ทดสอบ ละเอียดอยู่ใน
[`artwork_check/N8N_WORKFLOW_SETUP.md`](artwork_check/N8N_WORKFLOW_SETUP.md)**
และ prompt OCR อยู่ใน [`artwork_check/N8N_PROMPT.md`](artwork_check/N8N_PROMPT.md)

จุดสำคัญที่ workflow ล็อกไว้: `temperature 0`, prompt ห้ามแก้คำสะกด, responseSchema
บังคับ JSON — ถ้า LLM แอบแก้ "caliddd"→"calidad" โหมดนี้จะตาบอด

---

## API Endpoints

### โหมดตรวจจับสด (USB / RTSP)
- `POST /api/detection/start` · `POST /api/detection/stop` · `GET /api/detection/status`
- `GET /api/camera/scan` — สแกนกล้อง
- `GET /api/modes` · `GET /api/models?mode=` · `POST /api/mode/switch`
- `GET /video_feed` — MJPEG stream (ผูก src เฉพาะตอน detection ทำงาน)
- `GET /api/stats` · `GET /api/defects`

### โหมดสตรีม (กล้องของ Client — per-client isolation)
- `POST /api/stream/infer` — รับ JPEG 1 เฟรม (raw body) → ตรวจ → คืน
  `{detections:[{bbox,label,confidence,color,is_defect}], verdict, dent_count, w, h}`
- `POST /api/stream/snapshot` — ถ่ายภาพนิ่งจากกล้อง Client → ตรวจ คืนผลแบบเดียวกับ `/api/snapshot`
- *(คงไว้ ไม่ใช้แล้ว: `POST /api/stream/push` + `StreamCamera` จากแนวทาง push เดิม)*

### ถ่ายรูปตรวจ (Snapshot)
- `POST /api/viewfinder/start` — เปิดกล้อง + viewfinder (รับ `camera_index`, `quality`)
- `POST /api/viewfinder/stop` — ปิด viewfinder + คืนกล้อง
- `GET  /viewfinder_feed` — MJPEG stream สำหรับเล็ง (encode ครั้งเดียว แชร์ทุก viewer)
- `POST /api/snapshot` — ถ่าย + ตรวจ คืน `{verdict, dent_count, image(base64), capture_size, ...}`

> ตัวจัดการ transition ของกล้อง (start/stop detection·viewfinder, mode switch) ถูก
> serialize ด้วย lock เดียว — กัน race "เปิดกล้องซ้อน" เมื่อมีหลายคำขอพร้อมกัน

### โหมดฉลากกระดาษ
- `GET /api/label_paper/skus` · `POST /api/label_paper/inspect`
- `GET /api/label_paper/history` · `.../history/<id>` · `.../history/<id>/crop`
- `POST /api/label_paper/master/refresh`

### โหมด Artwork (`artwork_check/routes.py`)
- `POST /api/artwork/upload` — อัปโหลด + เสนอโซน
- `POST /api/artwork/<id>/inspect` — ตรวจทุกชั้น
- `POST /api/artwork/<id>/snap` — ขยับกรอบโซนให้พอดีเนื้อหา
- `POST /api/artwork/<id>/translate` — สร้างตารางข้อความ + แปล EN
- `GET /api/artwork/<id>/preview.png` · `.../overlay.png` · `.../crop` · `.../report`
- `GET/POST /api/artwork/templates` · `GET /api/artwork/templates/<name>`
- `GET /api/artwork/vocab` · `GET/POST /api/artwork/vocab/<brand>`
- `GET /api/artwork/history` · `DELETE /api/artwork/<id>`

---

## โครงสร้างโปรเจกต์

```
.
├── app.py                       # Flask app — entry point หลัก (ใช้งานจริงบนสถานี), ลงทะเบียนทุกโหมด + snapshot
├── run_server.py                # entry point แบบ gevent (ทางเลือก) — deploy หนัก/หลายกล้อง/MJPEG หลาย connection
├── generate_cert.py             # สร้าง self-signed TLS cert (จำเป็นสำหรับ getUserMedia/STREAM)
├── config.py                    # ตั้งค่ากลาง (กล้อง, snapshot, SQL, Flask, HTTPS, STREAM, N8N)
├── camera.py / yolo_detector.py # โหมดตรวจจับสด + StreamCamera + จัดการ backend กล้อง (MSMF/DSHOW/V4L2)
├── diagnose_snapshot.py         # เครื่องมือทดสอบกล้องบนสถานี (ความละเอียด/backend/fourcc)
├── verify_onnx.py               # เทียบผลตรวจ .pt vs .onnx (ต้อง PASS ก่อนเปิด USE_ONNX)
├── CLAUDE.md                     # บริบทสำคัญสำหรับ AI ที่ทำงานต่อ (อ่านก่อนแก้โค้ด)
├── database.py                  # SQL Server (pyodbc)
├── modes/                       # registry + config ต่อโหมด YOLO
│   ├── registry.py · can_dent.py · label.py · label_paper.py
├── inspectors/                  # โหมดฉลากกระดาษ (ΔE2000, OCR, barcode, ...)
│   ├── ocr_n8n.py · vertex_client.py · color_compare.py · deltae_map.py · ...
├── artwork_check/               # โหมด Artwork (แยกอิสระทั้งก้อน)
│   ├── routes.py                #   Flask blueprint
│   ├── pipeline.py              #   upload → zones → inspect
│   ├── zones.py                 #   เสนอโซน / snap / template
│   ├── ocr.py                   #   อ่านข้อความต่อโซน (PDF text / N8N)
│   ├── checks.py                #   4 ชั้นตรวจ
│   ├── translate.py             #   ตารางข้อความ + แปล EN + คำแนะนำ
│   ├── vocab.py · report.py · pdf_ingest.py · config.py
│   ├── n8n_artwork_ocr.workflow.json        # import เข้า N8N
│   ├── n8n_artwork_translate.workflow.json  # import เข้า N8N
│   ├── N8N_WORKFLOW_SETUP.md · N8N_PROMPT.md
├── templates/                   # หน้าเว็บ (base, index, label_paper, artwork_check, ...)
├── static/css/style.css         # ธีม Thai Union (navy/blue) + type system
├── static/js/                   # artwork_check.js, label_paper.js, main.js, ...
├── data/                        # ข้อมูลรันไทม์ (artwork inspections, SKU masters)
└── tests/                       # pytest (artwork, barcode, perspective, pixel masks, white-balance, golden)
```

---

## การปรับแต่งกล้อง & แก้ปัญหา (Camera Tuning & Troubleshooting)

รวมองค์ความรู้จากการ deploy จริงบนเครื่อง Windows + กล้อง ELP USB (OpenCV/MSMF)

### เครื่องมือวินิจฉัย — `diagnose_snapshot.py`

รัน **บนเครื่องสถานีที่ต่อกล้องจริง** (ปิด `app.py` ก่อน ไม่งั้นกล้องถูกจอง):

```bash
python diagnose_snapshot.py
```

จะรายงานว่ากล้องเปิดที่ความละเอียดไหนได้บ้าง, ใช้ backend อะไร (DSHOW/MSMF/V4L2),
ได้ fourcc อะไร, และความละเอียดจริงที่กล้องส่งออกมา → ใช้เลือกค่าที่เหมาะกับกล้องตัวนั้น

### ตารางอาการ → สาเหตุ → วิธีแก้

| อาการ | สาเหตุ | วิธีแก้ |
|---|---|---|
| **กดถ่ายแล้ว "ถ่ายไม่สำเร็จ"** | เดิม reopen กล้องที่ 5MP ตอนชัตเตอร์ไม่ติด | ใช้สถาปัตยกรรม single-handle (เปิดครั้งเดียว) — แก้แล้วในเวอร์ชันปัจจุบัน |
| **ภาพแตกเป็นคลื่นสีรุ้ง** | บังคับ `MJPG` แล้ว MSMF ส่ง JPEG ไม่ครบ (เฟรมขาด) | ตั้ง `CAMERA_FOURCC = None` (YUY2) |
| **แอป crash หลุดเอง (ไม่มี traceback)** | `scan` กล้องไล่เปิดทุก backend → ชน DSHOW/obsensor บน OpenCV 4.x | `_probe_index` เปิด backend เดียว + `try/except` — แก้แล้ว |
| **viewfinder เล็งไม่ลื่น** | กล้องเปิด 5MP@15fps + ย่อภาพใหญ่ทุกเฟรม | เลือก quality **"ลื่นที่สุด 720p"**, encode แชร์ครั้งเดียว, `VIEWFINDER_STREAM_FPS=30` |
| **กล้องได้แค่ 720p ทั้งที่เป็น 8MP** | MSMF (default Windows) ไม่รองรับ MJPG จึงตัน USB bandwidth | ความละเอียดสูงต้องใช้ DSHOW+MJPG ที่ภาพสะอาด — ถ้ากล้อง/พอร์ตไม่ไหว ใช้ 720p ก็ตรวจ dent ได้ |
| **`python run_server.py` รันไม่ขึ้น / `ModuleNotFoundError: gevent`** | บน Windows ที่มีหลาย Python (เช่น 3.9 และ 3.14) คำสั่ง `pip install` เปล่าๆ อาจลงให้คนละตัวกับที่ใช้รัน (`py -3.9`) | ติดตั้งด้วย interpreter เดียวกับที่รัน: `py -3.9 -m pip install gevent` ไม่ใช่ `pip install gevent` เฉยๆ — หรือใช้ `python app.py` แทนได้เลย (ดู [การรันแอป](#การรันแอป)) |

### หลักการที่ยึด

- **เสถียร > ความละเอียด** — กล้องตัวนี้ตัน 720p ผ่าน MSMF; ดัน 5MP ผ่าน reopen/MJPG
  แล้วไม่เสถียร จึงเลือก 720p ที่นิ่งเป็นค่าเริ่มต้น (ตรวจ dent ยังแม่นพอ)
- **CONFIG_VERSION บน footer** — ใช้ยืนยันว่าเครื่องรันโค้ดใหม่จริง ถ้า footer ไม่เปลี่ยน
  หลัง `git pull` แปลว่ายังไม่ได้ปิด-เปิด `app.py` ใหม่ (Flask อ่าน config ตอน start เท่านั้น)

---

## สรุปการปรับปรุงล่าสุด (Changelog)

### 🎮 OpenVINO iGPU (Iris Xe) — ✅ VERIFIED & ENABLED บนสถานี (2 ก.ค. 2026)
- **ผลจริงบนสถานี**: `bestX.pt` (YOLOv8m-seg, production) live 480 →
  **~45-50ms/เฟรม (~20-22 FPS) เร็วขึ้น ~6 เท่า** จากเพดาน ONNX CPU ~280ms (~2.7 FPS);
  snapshot 1280 → 420ms (เดิม ~1739ms); `best.pt` (detect) → ~14ms (~70 FPS)
- **Coverage (โจทย์ตั้งต้น) จบ**: กระป๋องอยู่ในเฟรม 1-2 วิ × ~21 FPS = 20-40+ ครั้ง/ใบ (เป้า 4-5)
- **verify_openvino.py PASS** ทั้ง intel:cpu+intel:gpu × imgsz 480+1280 (GPU: IoU
  0.98-0.99, Δconf ≤0.0053 — FP16 drift มีจริงแต่เล็กกว่าเกณฑ์ ~10 เท่า; CPU ตรงเป๊ะ)
- **สถานะ config ปัจจุบัน**: `OPENVINO_DEVICE = "intel:gpu"` + `CONFIG_VERSION =
  "2026.07.02-ov-igpu-ON"` — rollback ได้ทันทีด้วยการตั้งกลับเป็น `None` + รีสตาร์ต
- **flag ใหม่ `config.OPENVINO_DEVICE`** (default `None` = ปิดสนิท ทุกโหมดทำงานเท่าเดิม):
  ตั้ง `"intel:gpu"` เพื่อรัน inference ผ่าน OpenVINO บน iGPU — แยกจาก `USE_OPENVINO` เดิม
- **เวอร์ชันที่ถูกต้องสำหรับ Python 3.9**: `py -3.9 -m pip install "openvino==2024.6.0"`
  (รุ่นสุดท้ายที่มี wheel cp39-win **และ**อยู่ในช่วง `openvino>=2024.0.0` ที่ ultralytics
  8.4.41 รองรับ — **ห้ามใช้ 2025+ บน py3.9**, off-spec และเคยตรวจไม่เจอเงียบๆ)
- 🛡️ **fallback อัตโนมัติหลายชั้น** — `load_model()` ไล่ลอง backend ตามลำดับ
  OpenVINO@GPU → ONNX CPU → PyTorch (load + smoke test ต่อตัว); device ไม่มีจริง /
  export / โหลด / smoke-test ล้ม → ตัวถัดไปทำงานแทน ของเดิมพังไม่ได้
- 🛡️ กัน OpenVINO เงียบๆ สลับไป AUTO/CPU เอง — เช็ค `ov.Core().available_devices`
  ก่อนใช้ + stale guard (`.pt` ใหม่กว่า IR → re-export) + โหลดด้วย task จาก `.pt`
  (กับดัก segmentation เดียวกับ ONNX)
- 🧪 **`verify_openvino.py`** — ตาข่ายนิรภัย (เกณฑ์ชุดเดียวกับ `verify_onnx.py`):
  เทียบผลตรวจ PyTorch vs OpenVINO ทั้ง `intel:cpu`+`intel:gpu` ที่ imgsz 480+1280
  พร้อมวัดความเร็ว PyTorch/ONNX/OpenVINO ในรันเดียว — **ต้อง PASS ก่อนตั้ง
  `OPENVINO_DEVICE`** (⚠️ GPU plugin default รัน FP16 ภายในแม้ IR เป็น FP32 —
  ความแม่นจึงต้องพิสูจน์ด้วยสคริปต์นี้เท่านั้น ห้ามเชื่อว่ารันได้=ใช้ได้)
  ```bash
  py -3.9 -m pip install "openvino==2024.6.0"
  py -3.9 verify_openvino.py --weights weights\can_dent\bestX.pt --images path\to\sample_cans
  ```

### 🖼️ Frame Capture + วิดีโอสด + exposure (ก.ค. 2026)
- **Frame Capture** (checkbox แผง USB) — แช่ภาพเฟรมคมที่สุด+ครบใบของกระป๋อง NG ค้าง 5 วิ
  (candidate pooling ที่อัตรากล้อง + เช็คครบใบจากกล่อง `can` + re-infer ให้กรอบตรง + fallback).
  แสดงผลอย่างเดียว ไม่กระทบการนับ/DB — ดูหัวข้อ [Frame Capture](#frame-capture--แช่ภาพเฟรมที่-คมที่สุด--ครบใบ-ต่อกระป๋อง-ng-usbrtsp)
- **`LIVE_SMOOTH_VIDEO`** — สลับการแสดงผลสด USB/RTSP: `False`=กรอบล็อกเป๊ะ (ภาพตามอัตรา
  infer, เหมาะงานที่กรอบต้องตรง) / `True`=ภาพลื่น (กรอบตามช้าตอนขยับ)
- **Exposure control** (`CAMERA_AUTO_EXPOSURE`/`CAMERA_EXPOSURE`) — opt-in ล็อก exposure สั้น
  แก้ motion blur เฉพาะกล้อง live (snapshot/RTSP ไม่กระทบ); best-effort + fallback ถ้ากล้องไม่รับ
- **แก้ bestX (segmentation) ใช้ ONNX ได้** — ONNX ทิ้ง task tag → ต้องอ่าน task จาก `.pt`
  แล้วส่ง `YOLO(onnx, task='segment')` ไม่งั้นถอด output ผิด (ไม่มีกรอบ). cache ใน `<onnx>.task`
- **นับ 1 กระป๋อง = 1 การตรวจ** (edge-triggered) — ทั้ง USB/RTSP + STREAM

### 🚀 ONNX Runtime acceleration (เร่ง inference บน CPU โดยคงความแม่น) (มิ.ย. 2026)
- **ปัญหา**: วัดด้วยตัวจับเวลาในโหมด STREAM พบว่า inference กิน **542ms = 91%** ของเวลา
  ต่อเฟรม (FPS ~1.8 บน i7-1165G7) — network/encode/decode รวมกันแค่ ~50ms. คอขวดคือ
  PyTorch CPU ล้วนๆ → กรอบตามวัตถุช้า
- **ทางแก้**: export `.pt` → `.onnx` (FP32 / `dynamic=True`) ครั้งเดียว แล้วรันผ่าน
  **onnxruntime** — เร็วกว่า PyTorch CPU ~2 เท่า (คาด ~540ms → ~250-300ms ≈ 3.5-4 FPS)
  โดย **ultralytics เป็นคนถอดผล/NMS เองเหมือน `.pt`** → ผลตรวจเท่าเดิม (ไม่ใช่ลด imgsz)
- **ทำไมปลอดภัยกว่า OpenVINO**: โมเดลยังเป็น ONNX มาตรฐาน + decode โดย ultralytics
  (ไม่ใช่ decoder แยกของ OpenVINO ที่เคยทำตรวจไม่เจอเงียบๆ)
- **เปิดทั้งระบบ** (USB/RTSP/Snapshot/STREAM ได้เร็วขึ้นพร้อมกัน) ผ่าน `config.USE_ONNX`
- 🛡️ **fallback หลายชั้น** — ถ้า `onnxruntime` ไม่ได้ติดตั้ง / export / load / **smoke-test**
  (รัน 1 เฟรมจริงตอนโหลด) ล้มเหลว → กลับไปใช้ PyTorch `.pt` อัตโนมัติ ของเดิมพังไม่ได้
- 🛡️ **กัน .onnx ค้างเก่า** — ถ้า `best.pt` ใหม่กว่า `best.onnx` (เทรนใหม่) จะ re-export ให้
- 🎭 **รองรับโมเดล segmentation** (เช่น `bestX.pt` = YOLOv8-seg) — ONNX ที่ export จะไม่มี
  แท็ก task, `YOLO()` เลยเดาเป็น `detect` → ถอด output ผิด (กรอบขยะ/ไม่มีกรอบ). แก้โดย
  อ่าน task จริงจาก `.pt` แล้วส่ง `YOLO(onnx, task=...)` (cache ใน `<onnx>.task` sidecar)
- 🧪 **`verify_onnx.py`** — สคริปต์เทียบผลตรวจ `.pt` vs `.onnx` (จำนวนกล่อง/คลาส/conf/IoU)
  ที่ imgsz 480+1280 → **ต้อง PASS ก่อนเปิด `USE_ONNX=True`** (กันซ้ำรอย OpenVINO)
- **ค่าเริ่มต้น `USE_ONNX = False`** — ไม่มีอะไรเปลี่ยนจนกว่าจะเปิดเอง หลังเทียบผลผ่านแล้ว

  วิธีเปิดใช้ (บนเครื่องสถานี Windows + Python 3.9):
  ```bash
  py -3.9 -m pip install onnxruntime==1.19.2 onnxslim onnx   # 1.19.2 = wheel สุดท้ายของ py3.9
  py -3.9 verify_onnx.py --images path\to\sample_cans        # ต้องขึ้น PASS
  #  → แล้วตั้ง USE_ONNX = True ใน config.py, รีสตาร์ตแอป
  ```
  > ℹ️ **iGPU (Iris Xe) บน Python 3.9 ทำได้แล้วผ่าน `openvino==2024.6.0`** (รุ่นสุดท้าย
  > ที่มี wheel py3.9) — ดูหัวข้อ Changelog "OpenVINO iGPU" ด้านบน. ที่ใช้ไม่ได้บน py3.9
  > คือ `openvino 2025+` และ `onnxruntime-openvino` (ต้องการ Python ≥3.10)

### 🎯 นับ "1 กระป๋อง = 1 การตรวจ" แบบ edge-triggered (มิ.ย. 2026)
- ปัญหาเดิม: นับ/บันทึก DB ทุกเฟรมที่เจอ defect — กระป๋องเดิมที่ยังไม่ขยับก็ถูกนับ/บันทึก
  ซ้ำหลายสิบ-หลายร้อยครั้งระหว่างที่อยู่หน้ากล้อง
- แก้เป็น state machine 3 สถานะ (`none` / `ok` / `ng`) ต่อกระป๋อง — นับ +1 และบันทึก DB
  **เฉพาะตอนเปลี่ยนจาก "ไม่มีอะไร" → "เจอของ" เท่านั้น** กระป๋องเดิมที่ยังอยู่หน้ากล้องจะ
  ไม่ถูกนับซ้ำ ไม่ว่าจะอยู่นานแค่ไหน
- ถือว่ากระป๋อง "หายไปแล้ว" หลังไม่เจออะไรติดต่อกัน N เฟรม (กันค่าตรวจกระพริบเฟรมเดียว
  ทำให้นับเกิน) — ปรับได้ที่ `DEFECT_RESET_FRAMES` (config.py, ใช้กับ USB/RTSP) และ
  `STREAM_RESET_FRAMES` (templates/index.html, ใช้กับ STREAM)
- ใช้ทั้ง USB/RTSP (`inference_loop()` ใน app.py) และ STREAM (`streamInferLoop()` ฝั่ง JS)
- `DEFECT_LOGGING_COOLDOWN` (เวลา-based เดิม) เลิกใช้แล้ว เหลือไว้เพื่อ backward-compat

### 🟩 STREAM: ซ่อนกรอบเขียว "Good/Can" เมื่อผล NG (มิ.ย. 2026)
- USB/RTSP ซ่อนกรอบเขียว (`can`/`good`) อยู่แล้วเมื่อ verdict เป็น NG แต่ STREAM ยังโชว์ค้าง
  ทำให้ดูเหมือนกรอบไม่ตรงกับอีกโหมด — แก้ที่ `drawStreamBoxes()` ให้กรองเหลือเฉพาะ
  defect box เมื่อ `verdict === 'ng'` เหมือน USB/RTSP

### ⚡ OpenVINO acceleration (มิ.ย. 2026) — ปิดใช้งานเป็นค่าเริ่มต้น
- โค้ดรองรับ `USE_OPENVINO = True` (export `.pt` → OpenVINO ครั้งเดียว เก็บใน
  `weights/.../<name>_openvino_model/` แล้วรัน inference แทน PyTorch) แต่ทดสอบบนสถานีจริง
  แล้วพบว่า **ตรวจ dent ไม่เจอเลยทุกโหมด** เมื่อใช้คู่ `ultralytics 8.4.41` + `openvino 2025.3.0`
  (เวอร์ชัน output decoding ไม่ตรงกัน ไม่ขึ้น error ใดๆ ตอน export/load — เงียบแต่ผิด)
- **ค่าเริ่มต้นจึงล็อกไว้ที่ `USE_OPENVINO = False`** (ใช้ PyTorch ตามปกติ, ยืนยันแม่นยำแล้ว)
  ไม่แนะนำให้เปิดใช้จนกว่าจะ pin คู่เวอร์ชัน `ultralytics`/`openvino` ที่ทดสอบแล้วว่าตรวจถูก
  และเทียบผลตรวจกับ PyTorch แบบ side-by-side ก่อน
- ถ้าจะลองใหม่: ติดตั้ง `pip install openvino onnx` แล้วตั้ง `USE_OPENVINO = True` —
  มี fallback กลับ PyTorch อัตโนมัติถ้า export ล้มเหลว แต่ไม่ครอบคลุมกรณี "export สำเร็จ
  แต่ตรวจผิด" แบบที่เจอ จึงต้องตรวจผลเองก่อนใช้งานจริง

### 🎥 แหล่งสัญญาณภาพ "สตรีม" — กล้องของ Client (มิ.ย. 2026)
- **โหมด STREAM** ในข้อ "แหล่งสัญญาณภาพ" — ผู้ใช้แต่ละคนเปิดกล้องเครื่องตัวเองผ่านเบราว์เซอร์
- **Per-client isolation** — `POST /api/stream/infer` แบบ request/response: ทุกคนเห็นแต่กล้องตัวเอง
  (รัน detect ใน lock กันชนกัน; วาดกรอบบน `<canvas>` overlay ทับ local `<video>`)
- **Snapshot จากกล้อง Client** — `POST /api/stream/snapshot`
- **HTTPS (opt-in)** — `generate_cert.py` + `USE_HTTPS` (จำเป็นสำหรับ `getUserMedia`) — ใช้ได้ทั้ง
  `python app.py` และ `python run_server.py`
- **`run_server.py` (gevent, ทางเลือก)** — ตอนออกแบบไว้แก้ dev server ค้าง/timeout เมื่อ HTTPS +
  MJPEG ค้างหลาย connection; ใช้งานจริง (request/response สั้นๆ ต่อเฟรม) พบว่า `app.py`
  เพียงพอแล้วสำหรับ 1 สถานี — ดูหัวข้อ [การรันแอป](#การรันแอป)
- **Lazy `/video_feed`** — ผูก src เฉพาะตอน detection ทำงาน (แท็บที่จอดทิ้งไม่ยึด connection)
- **ตัววัด FPS/latency** บนจอโหมดสตรีม
- ✅ กระทบเฉพาะโหมด STREAM — USB/RTSP/Snapshot/Label/Artwork **ไม่แตะ**

ชุดการปรับปรุงโหมด **ถ่ายรูปตรวจ (Snapshot) + กล้อง** (มิ.ย. 2026):

### ✨ ฟีเจอร์ใหม่
- **โหมดถ่ายรูปตรวจ (Snapshot)** — เล็ง → ชัตเตอร์ → ตรวจ 1 ครั้งความละเอียดสูง → OK/NG
- **เลือกความคมชัด/ความลื่น** — preset smooth/balanced/sharp จำค่าไว้ เปลี่ยนได้ทันที
- **`diagnose_snapshot.py`** — เครื่องมือทดสอบความสามารถกล้องบนสถานี

### 🛠️ การแก้ไขความถูกต้อง & เสถียรภาพ
- **Single-handle architecture** — เปิดกล้องครั้งเดียว ตัด reopen ที่ทำให้ "ถ่ายไม่สำเร็จ"
- **Resolution ladder + fallback** — เปิดความละเอียดไม่ได้ก็ตกลงระดับถัดไป ไม่ค้างที่ error
- **ปิด crash ตอน scan กล้อง** — probe backend เดียว + `try/except` (กัน DSHOW/obsensor abort)
- **ปิด `MJPG` เป็น `None`** — แก้ภาพแตกคลื่นสีรุ้งบน MSMF
- **Staleness guard** — ปฏิเสธการถ่ายเมื่อเฟรมเก่ากว่า 1 วิ (กันตัดสินจากภาพค้าง — QC-safe)
- **Serialize camera transitions** — lock เดียวกัน race "เปิดกล้องซ้อน" จากหลายคำขอ

### ⚡ ประสิทธิภาพ
- **Shared MJPEG encode** — viewfinder encode ครั้งเดียวต่อเฟรม แชร์ทุก viewer (เดิม encode ซ้ำต่อ client)
- **VIEWFINDER_STREAM_FPS แยกจาก live** — เล็งลื่นขึ้น (30fps) โดยไม่กระทบ bandwidth สตรีมสด

> การเปลี่ยนแปลงทั้งหมด **คง API / JSON response / UI เดิม** (frontend ไม่ต้องแก้) และผ่าน
> การทดสอบด้วย harness จำลองกล้อง (ทุก state transition, mutual-exclusion, staleness) +
> test suite เดิมทั้งหมด (ดูหัวข้อ [Test](#test))

---

## Test

```bash
python -m pytest tests/ -q
```

ชุดเทสต์ครอบคลุมทั้งโหมดฉลากกระดาษและ Artwork (~146 ฟังก์ชันทดสอบใน 6 ไฟล์):

| ไฟล์ | ครอบคลุม |
|---|---|
| `test_artwork_checks.py` | 4 ชั้นตรวจ Artwork, ความทนต่อ OCR noise, snap-to-content, ตารางคำแปล + คำแนะนำ + cache |
| `test_barcode.py` | ถอดบาร์โค้ด + ตรวจ check digit (GS1) |
| `test_perspective.py` | perspective warp / parse corners ของโหมดฉลากกระดาษ |
| `test_pixel_masks.py` | edge/glare ignore mask ก่อนคิด ΔE |
| `test_white_balance.py` | auto white-balance เทียบ master |
| `test_inspection_golden.py` | golden test ของ pipeline ตรวจฉลากแบบ end-to-end |

---

## หมายเหตุ / Acknowledgements

- ระบบนี้พัฒนาต่อยอดจากฐานระบบตรวจจับด้วย YOLO (เดิมตรวจขวดน้ำ) มาเป็นระบบตรวจ
  บรรจุภัณฑ์หลายโหมดของ Thai Union
- ฟอนต์ IBM Plex โหลดจาก Google Fonts หากเครื่องไม่มีอินเทอร์เน็ต ระบบจะ fallback
  เป็นฟอนต์ระบบที่รองรับไทยอัตโนมัติ
