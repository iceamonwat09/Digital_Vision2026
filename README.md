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
6. [โหมดที่ 2 — ตรวจฉลากกระดาษ (ΔE2000)](#โหมดที่-2--ตรวจฉลากกระดาษ-δe2000)
7. [โหมดที่ 3 — ตรวจ Artwork (OCR + 4 ชั้น)](#โหมดที่-3--ตรวจ-artwork-ocr--4-ชั้น)
8. [การตั้งค่า N8N → Gemini](#การตั้งค่า-n8n--gemini)
9. [API Endpoints](#api-endpoints)
10. [โครงสร้างโปรเจกต์](#โครงสร้างโปรเจกต์)
11. [Test](#test)

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
# กล้อง
CAMERA_INDEX = 0            # index กล้อง USB (รัน/api/camera/scan เพื่อหา)

# SQL Server (เก็บประวัติ)
SQL_SERVER   = "172.32.0.50"
SQL_DATABASE = "VisionIQ"
SQL_USER     = "sa"
SQL_PASSWORD = "********"

# Flask
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5000
FLASK_DEBUG = False
```

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
├── app.py                       # Flask app — entry point, ลงทะเบียนทุกโหมด
├── config.py                    # ตั้งค่ากลาง (กล้อง, SQL, Flask, N8N)
├── camera.py / yolo_detector.py # โหมดตรวจจับสด
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
