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
FLASK_PORT = 5000
FLASK_DEBUG = False
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
| `ARTWORK_SPELL_LANGS` | `en,es` | ภาษาที่ชั้น dictionary ตรวจ |

---

## การรันแอป

```bash
python app.py
```

เปิดเบราว์เซอร์ไปที่ `http://localhost:5000` (หรือ IP เครื่อง:5000)

เมนูบนสุด: **ตรวจจับสด · ตรวจฉลากกระดาษ · ตรวจ Artwork · แดชบอร์ด · ประวัติ**

---

## โหมดที่ 1 — ตรวจจับสด (YOLO)

ตรวจตำหนิบรรจุภัณฑ์แบบเรียลไทม์จากกล้อง

**หน้าใช้งาน** (`/`) จัดเป็นแผงควบคุม 3 ขั้น:
1. **โหมดตรวจสอบ** — เลือกโมเดล (`can_dent` / `label`) และไฟล์ `.pt`
2. **แหล่งสัญญาณภาพ** — กล้อง USB หรือ IP (RTSP)
3. **เริ่ม/หยุด** — Start/Stop Detection พร้อมสถิติ Active/Total

**โมเดล** เก็บไฟล์ `.pt` ในโฟลเดอร์ weights ต่อโหมด (ดู `modes/registry.py`)
สลับโมเดล/โหมดได้จาก UI ไม่ต้องรีสตาร์ต

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

### โหมดตรวจจับสด
- `POST /api/detection/start` · `POST /api/detection/stop` · `GET /api/detection/status`
- `GET /api/camera/scan` — สแกนกล้อง
- `GET /api/modes` · `GET /api/models?mode=` · `POST /api/mode/switch`
- `GET /video_feed` — MJPEG stream
- `GET /api/stats` · `GET /api/defects`

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
├── app.py                       # Flask app — entry point, ลงทะเบียนทุกโหมด + snapshot
├── config.py                    # ตั้งค่ากลาง (กล้อง, snapshot, SQL, Flask, N8N)
├── camera.py / yolo_detector.py # โหมดตรวจจับสด + จัดการ backend กล้อง (MSMF/DSHOW/V4L2)
├── diagnose_snapshot.py         # เครื่องมือทดสอบกล้องบนสถานี (ความละเอียด/backend/fourcc)
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
└── tests/                       # pytest (รวม test_artwork_checks.py)
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

### หลักการที่ยึด

- **เสถียร > ความละเอียด** — กล้องตัวนี้ตัน 720p ผ่าน MSMF; ดัน 5MP ผ่าน reopen/MJPG
  แล้วไม่เสถียร จึงเลือก 720p ที่นิ่งเป็นค่าเริ่มต้น (ตรวจ dent ยังแม่นพอ)
- **CONFIG_VERSION บน footer** — ใช้ยืนยันว่าเครื่องรันโค้ดใหม่จริง ถ้า footer ไม่เปลี่ยน
  หลัง `git pull` แปลว่ายังไม่ได้ปิด-เปิด `app.py` ใหม่ (Flask อ่าน config ตอน start เท่านั้น)

---

## สรุปการปรับปรุงล่าสุด (Changelog)

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
> test suite เดิม 27/27

---

## Test

```bash
python -m pytest tests/ -q
```

ชุดเทสต์ของโหมด Artwork (`tests/test_artwork_checks.py`) ครอบคลุม 4 ชั้นตรวจ,
ความทนต่อ OCR noise, snap-to-content, ตารางคำแปล + คำแนะนำ + cache

---

## หมายเหตุ / Acknowledgements

- ระบบนี้พัฒนาต่อยอดจากฐานระบบตรวจจับด้วย YOLO (เดิมตรวจขวดน้ำ) มาเป็นระบบตรวจ
  บรรจุภัณฑ์หลายโหมดของ Thai Union
- ฟอนต์ IBM Plex โหลดจาก Google Fonts หากเครื่องไม่มีอินเทอร์เน็ต ระบบจะ fallback
  เป็นฟอนต์ระบบที่รองรับไทยอัตโนมัติ
