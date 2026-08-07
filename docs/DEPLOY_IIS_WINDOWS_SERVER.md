# คู่มือ Deploy VisionIQ (ระบบ Login + ตรวจ Artwork) บน Windows Server + IIS

> เอกสารนี้เขียนสำหรับ **คนที่ยังไม่เคย deploy Flask บน IIS มาก่อน** — ไล่ทีละเมนู
> ทีละปุ่ม พร้อมบอกว่า "ทำไมต้องทำ" และ "ถ้าไม่ทำจะเจออะไร"
>
> เป้าหมาย: ผู้ใช้เปิดเบราว์เซอร์ → `https://<ชื่อเซิร์ฟเวอร์>/` → **หน้าล็อกอิน** →
> เข้าใช้ **โหมดตรวจ Artwork** ได้จากเครื่องตัวเองโดยไม่ต้องมีอะไรติดตั้งฝั่ง client
>
> เวลาที่ใช้โดยประมาณ: **2–3 ชั่วโมง** (ครึ่งหนึ่งคือรอ `pip install`)

---

## สารบัญ

| ตอน | เรื่อง |
|---|---|
| [0](#0-ภาพรวม-อ่าน-3-นาทีก่อนลงมือ) | ภาพรวม — อ่าน 3 นาทีก่อนลงมือ |
| [1](#1-ข้อเท็จจริงจากโค้ดจริง-ที่ต้องรู้ก่อน) | ข้อเท็จจริงจากโค้ดจริงที่ต้องรู้ก่อน (สำคัญที่สุด) |
| [2](#2-checklist-ของที่ต้องติดตั้งบนเซิร์ฟเวอร์) | Checklist ของที่ต้องติดตั้ง |
| [3](#3-ขั้นตอนติดตั้ง-ทีละ-step) | ขั้นตอนติดตั้ง STEP 1–17 (ละเอียดทุกคลิก) |
| [4](#4-สร้าง-role-และผู้ใช้สำหรับงาน-artwork) | สร้าง Role และผู้ใช้สำหรับงาน Artwork |
| [5](#5-รายการตรวจรับหลัง-deploy) | รายการตรวจรับหลัง deploy |
| [6](#6-ปัญหาที่พบบ่อย-อาการ--สาเหตุ--วิธีแก้) | ปัญหาที่พบบ่อย (อาการ → สาเหตุ → วิธีแก้) |
| [7](#7-อัปเดตโค้ดในอนาคต-redeploy) | อัปเดตโค้ดในอนาคต |
| [8](#8-สำรองข้อมูลและงานบำรุงรักษา) | สำรองข้อมูลและงานบำรุงรักษา |
| [9](#9-ภาคผนวก) | ภาคผนวก (ตัวแปรทั้งหมด / ทางเลือกอื่น / X-Forwarded-For) |

---

## 0. ภาพรวม — อ่าน 3 นาทีก่อนลงมือ

### 0.1 ทำไมเอา Flask ไปวางบน IIS ตรง ๆ ไม่ได้

IIS พูดภาษา HTTP และรัน .NET / FastCGI เป็นหลัก แต่แอปนี้เป็น **Python + Flask (WSGI)**
สองฝั่งคุยกันไม่รู้เรื่องโดยตรง จึงต้องมี "ตัวกลาง" 2 ชิ้น:

| ชิ้น | หน้าที่ | ติดตั้งที่ไหน |
|---|---|---|
| **HttpPlatformHandler** | โมดูลของ IIS ที่ทำหน้าที่ "เปิดโปรเซส Python ให้ + ส่งต่อ request" | ติดตั้งลง IIS (ไฟล์ .msi) |
| **waitress** | WSGI server ฝั่ง Python ที่รับ request จาก IIS แล้วส่งเข้า Flask | `pip install` ใน venv ของโปรเจกต์ |

### 0.2 ภาพรวมการไหลของ request

```
   เครื่องผู้ใช้ (เบราว์เซอร์)
        │  https://visioniq.company.local/artwork_check
        ▼
   ┌──────────────────────────────────────────────┐
   │  Windows Server                              │
   │                                              │
   │   IIS  (พอร์ต 443 + ใบรับรอง TLS)             │
   │    │                                         │
   │    │  HttpPlatformHandler  ── เปิดโปรเซสให้ ──┐│
   │    ▼                                        ││
   │   waitress  127.0.0.1:<พอร์ตที่ IIS สุ่มให้>  ◄┘│
   │    │        (deploy/wsgi_iis.py)             │
   │    ▼                                         │
   │   Flask (app.py)                             │
   │    ├── auth/         → หน้า Login + สิทธิ์     │
   │    └── artwork_check/→ โหมดตรวจ Artwork       │
   │         │            │                       │
   └─────────┼────────────┼───────────────────────┘
             │            │
             ▼            ▼
      SQL Server      N8N webhook (OCR)
      172.32.0.50     172.32.201.106:5678
      ฐาน VisionIQ    (ต้องต่อถึงจากเซิร์ฟเวอร์!)
             │
             └── ผู้ใช้/สิทธิ์: AuthUsers, AuthRoles, ...

   ไฟล์ artwork ที่อัปโหลด/ผลตรวจ → เก็บลง "ดิสก์ของเซิร์ฟเวอร์"
   ที่ C:\VisionIQ\Digital_Vision2026\data\artwork_check\
```

### 0.3 ของใหม่ที่เตรียมไว้ให้แล้วในโปรเจกต์

เอกสารนี้มาพร้อมไฟล์ช่วย deploy 4 ไฟล์ (เป็นไฟล์ใหม่ทั้งหมด — **ไม่แก้ `app.py`
และไม่กระทบการรันบนสถานีเดิมด้วย `py -3.9 app.py` แม้แต่นิดเดียว**):

| ไฟล์ | หน้าที่ |
|---|---|
| `deploy/wsgi_iis.py` | จุดเริ่มโปรเซสสำหรับ IIS (เปิด waitress + เลือกว่าจะ init อะไร) |
| `deploy/web.config.example` | ต้นแบบ `web.config` ของ IIS — คัดลอกไปแก้ path แล้วใช้ได้เลย |
| `deploy/requirements-server.txt` | รายการ package เฉพาะเซิร์ฟเวอร์ (ตัดของที่เกี่ยวกับกล้องออก) |
| `deploy/check_server.py` | สคริปต์ตรวจความพร้อม — บอกเป็นข้อ ๆ ว่าอะไร OK / WARN / FAIL |

---

## 1. ข้อเท็จจริงจากโค้ดจริงที่ต้องรู้ก่อน

> **อย่าข้ามหัวข้อนี้** — 90% ของปัญหาตอน deploy มาจากไม่รู้ 14 ข้อนี้

| # | ข้อเท็จจริง | อ้างอิงในโค้ด | ผลต่อการ deploy |
|---|---|---|---|
| 1 | **ระบบล็อกอิน + สิทธิ์มีอยู่ในโค้ดแล้ว ไม่ต้องเขียนใหม่** — หน้า `/login`, `/admin/users`, JWT cookie, ล็อกบัญชี, audit log ครบ | `auth/` ทั้งโฟลเดอร์ | งานของคุณคือ "ตั้งค่า + สร้างตารางใน DB" ไม่ใช่ "พัฒนา" |
| 2 | ผู้ใช้/สิทธิ์เก็บใน **SQL Server ฐาน `VisionIQ`** ตาราง `AuthUsers`, `AuthRoles`, `AuthPermissions`, `AuthRolePermissions`, `AuthLoginAudit`, `AuthAdminAudit` | `Connection_sql/auth_schema.sql` | ต้องรันสคริปต์ SQL นี้ก่อน ไม่งั้นล็อกอินไม่ได้ |
| 3 | เปิด/ปิดระบบล็อกอินทั้งระบบด้วยตัวแปร **`AUTH_ENABLED`** (default = เปิด) | `auth/config.py:34` | ถ้าตั้งเป็น `0` ทุกหน้าเปิดโล่งไม่ต้องล็อกอิน — ใช้เป็นทางถอยฉุกเฉิน |
| 4 | ล็อกอินแล้วได้ **cookie httpOnly 2 ใบ**: `vq_access` (อายุ 60 นาที) + `vq_refresh` (7 วัน) | `auth/config.py:37-46` | ถ้าตั้ง `AUTH_COOKIE_SECURE=1` แต่เข้าเว็บด้วย `http://` → **ล็อกอินแล้วเด้งกลับวนไม่จบ** |
| 5 | สิทธิ์ที่เกี่ยวกับ Artwork มี **2 ตัว**: `inspect_artwork` (หน้าตรวจ + API) และ `view_history` (หน้าประวัติ) | `auth/access.py:41,43,49,50` | ผู้ใช้ Artwork ต้องได้ **ทั้งสองตัว** ถ้าต้องดูประวัติย้อนหลัง |
| 6 | Role ที่ระบบแถมมา 4 อัน: `Admin` / `Manager` / `Staff` / `Viewer` — **`Staff` ไม่มี `view_history`** | `auth/config.py:73-80` | ถ้าให้ผู้ใช้เป็น Staff เขาจะกดดูประวัติ artwork ไม่ได้ → **แนะนำสร้าง role ใหม่เอง** (ตอนที่ 4) |
| 7 | หน้าแรกหลังล็อกอินคือ **`/home`** (ต้องล็อกอินอย่างเดียว ไม่ต้องมีสิทธิ์พิเศษ) ส่วน **`/` ต้องมีสิทธิ์ `run_live_detection`** | `auth/access.py:65-66`, `app.py:705` | เซิร์ฟเวอร์ไม่มีกล้อง → **อย่าให้สิทธิ์ `run_live_detection`** กับผู้ใช้ Artwork ไม่งั้นเขาจะกดเข้าหน้าที่ใช้ไม่ได้ |
| 8 | ไฟล์ artwork ที่อัปโหลดและผลตรวจ **เก็บเป็นไฟล์บนดิสก์** ที่ `data/artwork_check/inspections/<id>/` (ไม่ได้เก็บลง DB) | `artwork_check/config.py:15-21`, `pipeline.py:48-56` | โฟลเดอร์นี้ต้อง**เขียนได้**และต้องอยู่ในแผน **backup** |
| 9 | OCR ของ Artwork ยิงไปที่ **N8N webhook** ภายนอก | `config.py:368-372` | Windows Server ต้อง **route/firewall ถึง** `172.32.201.106:5678` ไม่งั้นไฟล์ที่เป็น outline จะตรวจไม่ได้ |
| 10 | ไฟล์ PDF ที่ยังมี **text layer** ตรวจได้โดยไม่ต้องพึ่ง OCR เลย | `artwork_check/ocr.py:55-58` | ถ้า N8N ล่ม ยังตรวจไฟล์กลุ่มนี้ได้ |
| 11 | **`app.py` import `cv2` และ `ultralytics` ไว้ที่หัวไฟล์** (บรรทัด 12, 19, 20) | `app.py:12-22` | เซิร์ฟเวอร์ต้องลง `opencv-python` + `ultralytics` (ซึ่งดึง **torch** ~2 GB มาด้วย) **แม้จะไม่ตรวจกระป๋องเลย** ไม่งั้น import ไม่ผ่านทั้งแอป = ล็อกอินก็ไม่ขึ้น |
| 12 | `init_system()` (โหลดโมเดล + ต่อ DB) อยู่ใน `if __name__ == '__main__'` | `app.py:1650-1657` | เวลารันแบบ WSGI บล็อกนี้ **ไม่ทำงาน** → นี่คือเหตุผลที่ต้องมี `deploy/wsgi_iis.py` มาคุมเอง |
| 13 | เพดานอัปโหลด artwork = **40 MB** ในโค้ด แต่ค่า default ของ IIS = **~28.6 MB** | `artwork_check/routes.py:23` | ต้องขยาย `maxAllowedContentLength` ของ IIS ไม่งั้นไฟล์ใหญ่โดนปัดตกเป็น **HTTP 404.13** ก่อนถึงแอป |
| 14 | โค้ดต่อ SQL ด้วยไดรเวอร์ชื่อ **`{SQL Server}`** ตายตัว (ตัวที่ติดมากับ Windows อยู่แล้ว) | `auth/store.py:48`, `database.py:59` | **ไม่ต้อง**ติดตั้ง "ODBC Driver 17/18 for SQL Server" เพิ่ม |

### ⏱️ เรื่องเวลา (timeout) — ที่ต้องตั้ง 3 ชั้นให้สอดคล้องกัน

การตรวจ Artwork 1 ครั้งจะเรียก OCR **ทีละโซน** โซนละไม่เกิน `N8N_OCR_TIMEOUT_S` (60 วิ)
ถ้าลาก 8 โซน = อาจใช้เวลาหลายนาทีต่อ 1 request ค่า default ของทุกชั้นสั้นเกินไป:

| ชั้น | ค่า default | ต้องตั้งเป็น | ตั้งที่ไหน |
|---|---|---|---|
| IIS / HttpPlatformHandler | 2 นาที | **20 นาที** | `requestTimeout="00:20:00"` ใน `web.config` |
| waitress | 2 นาที | **20 นาที** | `channel_timeout=1200` (ตั้งไว้ให้แล้วใน `wsgi_iis.py`) |
| N8N (ต่อโซน) | 60 วิ | 60 วิ (พอ) | `N8N_OCR_TIMEOUT_S` |

---

## 2. Checklist ของที่ต้องติดตั้งบนเซิร์ฟเวอร์

| # | รายการ | เวอร์ชันที่แนะนำ | จำเป็นไหม | ถ้าไม่มีจะเป็นยังไง |
|---|---|---|---|---|
| 1 | Windows Server + **IIS (Web Server role)** | 2016 ขึ้นไป | ✅ บังคับ | — (คุณเปิดแล้ว) |
| 2 | **HttpPlatformHandler** | v1.2 (x64) | ✅ บังคับ | เว็บขึ้น **HTTP 500.19** |
| 3 | **Python** (64-bit) | **3.9.13** — เลือกไว้เพื่อรองรับกล้อง/ตัวเร่งในอนาคต (❌ ห้าม 3.13 · ดู [STEP 2](#step-2--ติดตั้ง-python-3913-64-bit)) | ✅ บังคับ | — |
| 4 | **venv + package** ตาม `deploy/requirements-server.txt` | — | ✅ บังคับ | ล็อกอินไม่ได้ / ตรวจไม่ได้ |
| 5 | Git for Windows | ล่าสุด | 🟡 แนะนำ | อัปเดตโค้ดยาก (ต้องคัดลอกไฟล์เอง) |
| 6 | **Tesseract-OCR** (UB-Mannheim build) | 5.x | 🟡 แนะนำ | ไฟล์ outline/ภาพถ่าย **ไม่มีกรอบแดงชี้คำผิด** (ผลตรวจยังถูกต้องเท่าเดิม) |
| 7 | สิทธิ์เข้าถึง SQL Server `VisionIQ` | — | ✅ บังคับ | ล็อกอินไม่ได้ |
| 8 | เส้นทางเครือข่ายถึง N8N webhook | — | ✅ บังคับ (ถ้าตรวจไฟล์ outline) | ไฟล์ที่ไม่มี text layer ตรวจไม่ได้ |
| 9 | ใบรับรอง TLS (certificate) | จาก IT / CA ภายใน | 🟡 แนะนำมาก | ใช้ `http://` ได้ แต่รหัสผ่านวิ่งแบบไม่เข้ารหัส |
| 10 | ODBC Driver เพิ่มเติม | — | ❌ ไม่ต้อง | (ข้อ 14 ด้านบน) |
| 11 | onnxruntime / OpenVINO | — | ❌ ไม่ต้อง | ใช้เร่งการตรวจกระป๋องเท่านั้น เซิร์ฟเวอร์ไม่มีกล้อง |

---

## 3. ขั้นตอนติดตั้ง (ทีละ STEP)

> **ข้อตกลงเรื่อง path ในเอกสารนี้** — ทุกตัวอย่างใช้
> `C:\VisionIQ\Digital_Vision2026` เป็นที่วางโค้ด ถ้าคุณใช้ path อื่น ให้แทนที่ทุกจุด
>
> **เปิด Command Prompt แบบ Administrator ทุกครั้ง**
> (กด Start → พิมพ์ `cmd` → คลิกขวาที่ "Command Prompt" → **Run as administrator**)

---

### STEP 1 — ยืนยันว่า IIS พร้อมใช้งาน

1. กด **Win + R** → พิมพ์ `inetmgr` → กด Enter
   → หน้าต่าง **Internet Information Services (IIS) Manager** ต้องเปิดขึ้นมา
2. เปิดเบราว์เซอร์บนเซิร์ฟเวอร์ → ไปที่ `http://localhost/`
   → ต้องเห็นหน้าต้อนรับสีฟ้าของ IIS

**ถ้ายังไม่มี IIS** (ข้ามได้ถ้าเปิดแล้ว):
- เปิด **Server Manager** → เมนูบนขวา **Manage** → **Add Roles and Features**
- กด **Next** จนถึงหน้า **Server Roles** → ติ๊ก **Web Server (IIS)** → กด **Add Features**
- **Next** ต่อจนถึงหน้า **Role Services** → ตรวจว่าติ๊กครบ:
  - `Common HTTP Features` → **Static Content**, **Default Document**, **HTTP Errors**
  - `Health and Diagnostics` → **HTTP Logging**
  - `Security` → **Request Filtering**
  - `Performance` → **Static Content Compression**
- **Next** → **Install** → รอจนเสร็จ

> 💡 **ไม่ต้อง**ติ๊ก CGI / ASP.NET — เราไม่ได้ใช้ FastCGI

---

### STEP 2 — ติดตั้ง Python 3.9.13 (64-bit)

> ### 📌 ทำไมเลือก 3.9 — และต้องรู้อะไรควบคู่ไปด้วย
>
> **เหตุผลที่เลือก (การตัดสินใจของเจ้าของระบบ):** เซิร์ฟเวอร์ตัวนี้อาจ**ต้องต่อกล้อง
> ในอนาคต** ซึ่งจะต้องใช้ตัวเร่งความเร็วการตรวจชุดเดียวกับสถานี —
> `onnxruntime==1.19.2` และ `openvino==2024.6.0` เป็น**ล้อรุ่นสุดท้ายที่มี `cp39-win_amd64`**
> ถ้าตอนนี้ลง Python เวอร์ชันใหม่ วันที่ต้องเปิดกล้องจริงจะต้องรื้อ environment ใหม่ทั้งชุด
> การเริ่มที่ 3.9 เลยตั้งแต่แรกจึงทำให้เซิร์ฟเวอร์ "พร้อมต่อยอด" ได้ทันที
> (ดู [ภาคผนวก F](#f-เตรียมพร้อมสำหรับ-ใช้กล้องบนเซิร์ฟเวอร์-ในอนาคต))
>
> **สิ่งที่ต้องรับรู้ควบคู่กัน:** Python 3.9 **หมดอายุการสนับสนุนเมื่อ 31 ต.ค. 2025**
> = ไม่มี security patch ใหม่อีกแล้ว และ **`3.9.13` คือตัวสุดท้ายที่มีตัวติดตั้ง (.exe)
> สำหรับ Windows** — รุ่น 3.9.14 ถึง 3.9.25 ออกเป็น **source-only** เท่านั้น
> แปลว่าถ้าจะใช้บน Windows แบบไม่คอมไพล์เอง ก็ต้องเป็น 3.9.13 ซึ่งขาดแพตช์ความปลอดภัย
> ที่ออกหลังจากนั้นทั้งหมด
>
> **มาตรการชดเชยที่ควรทำ** (คุ้มค่าและทำได้จริง — ดูรายละเอียดใน [ภาคผนวก G](#g-มาตรการชดเชยเมื่อรันบน-python-ที่หมดอายุแล้ว)):
> เปิดเว็บเฉพาะในเครือข่ายภายใน · ไม่เปิดออกอินเทอร์เน็ต · ใช้ HTTPS ·
> อัปเดต Windows สม่ำเสมอ · จำกัดสิทธิ์ของ Application Pool

**เวอร์ชันที่ใช้ได้กับชุด package นี้ (ตรวจล้อจริงบน PyPI แล้ว):**

| เวอร์ชัน | ใช้ได้ไหม | หมายเหตุ |
|---|---|---|
| **3.9.13** | ✅ **ที่ใช้ในคู่มือนี้** | ล้อครบทุกตัว + **รองรับตัวเร่ง onnx/openvino เมื่อต้องต่อกล้อง** · แลกกับการไม่มี security patch |
| 3.11 / 3.12 | ✅ ใช้ได้ (ถ้าไม่ต้องใช้กล้อง) | ยังได้ security update — แต่ **ลงตัวเร่ง onnxruntime 1.19.2 / openvino 2024.6.0 ไม่ได้** |
| 3.13 | ❌ **ห้าม** | `numpy 1.26.4` และ `Pillow 10.1.0` ไม่มีล้อ `cp313` → ติดตั้งไม่ผ่าน |

**ขั้นตอนติดตั้ง:**

1. ดาวน์โหลด **`python-3.9.13-amd64.exe`** จาก
   `https://www.python.org/downloads/release/python-3913/`
   (เลื่อนลงล่างสุด เลือกหัวข้อ **Windows installer (64-bit)**)
   - หน้าเว็บจะขึ้นคำเตือนสีเหลืองว่า 3.9 หมดอายุแล้ว — **ถูกต้องแล้ว ไม่ใช่ความผิดพลาด**
     (เป็นข้อแลกที่ยอมรับไว้ข้างต้น)
2. คลิกขวาไฟล์ → **Run as administrator**
3. ที่หน้าแรก ให้เลือก **Customize installation** (อย่ากด Install Now)
   - ⚠️ ก่อนกด ให้ติ๊ก **Add Python 3.9 to PATH** ที่ด้านล่างด้วย
4. หน้า **Optional Features** — ติ๊กให้ครบ:
   - ☑ Documentation
   - ☑ **pip**
   - ☑ **py launcher** + ☑ **for all users (requires admin)**
   - กด **Next**
5. หน้า **Advanced Options** — ติ๊ก:
   - ☑ **Install for all users**
   - ☑ Add Python to environment variables
   - ☑ Precompile standard library
   - ช่อง **Customize install location** → พิมพ์ `C:\Python39`
   - กด **Install**
6. เสร็จแล้วกด **Close**

**ยืนยันผล** — ใน Command Prompt:
```cmd
C:\Python39\python.exe --version
```
ต้องขึ้น `Python 3.9.13`

> 💡 **เครื่องสถานีไม่ต้องแตะอะไรทั้งสิ้น** — ยังใช้ `py -3.9 app.py` ต่อไปเหมือนเดิม
> การใช้ Python เวอร์ชันเดียวกันทั้งสองเครื่องมีข้อดีเพิ่มคือ **ทดสอบที่หนึ่งแล้ว
> เชื่อผลอีกที่ได้** (ไม่ต้องกังวลว่าพฤติกรรมต่างกันเพราะเวอร์ชัน Python)

---

### STEP 3 — ติดตั้ง HttpPlatformHandler

นี่คือชิ้นส่วนที่ทำให้ IIS "รู้จัก" วิธีเปิดโปรเซส Python

1. ไปที่ `https://www.iis.net/downloads/microsoft/httpplatformhandler`
2. ดาวน์โหลด **HttpPlatformHandler 1.2** เลือกไฟล์ **x64** (`httpPlatformHandler_amd64.msi`)
3. คลิกขวา → **Install** → กด **Next** → ติ๊กยอมรับ license → **Next** → **Install** → **Finish**

**ยืนยันผล** (สำคัญมาก — ถ้าข้ามจะไปเจอ error ทีหลัง):
1. เปิด **IIS Manager** (`inetmgr`)
2. ที่แถบซ้าย (Connections) **คลิกที่ชื่อเครื่องเซิร์ฟเวอร์** (node บนสุด)
3. ที่แผงกลาง ดับเบิลคลิกไอคอน **Modules**
4. เลื่อนหา **`httpPlatformHandler`** → ต้องมีอยู่ในรายการ

> ❌ ถ้าไม่มี → เว็บจะขึ้น **HTTP Error 500.19 – Internal Server Error
> (Config Error: unrecognized element 'httpPlatform')** ทันทีที่วาง web.config

---

### STEP 4 — เอาโค้ดขึ้นเซิร์ฟเวอร์

**วิธีที่แนะนำ (ใช้ Git — อัปเดตง่ายในอนาคต):**

1. ติดตั้ง **Git for Windows** จาก `https://git-scm.com/download/win`
   (กด Next ผ่านทุกหน้าได้ ค่า default ใช้ได้หมด)
2. เปิด Command Prompt (Administrator):
```cmd
mkdir C:\VisionIQ
cd /d C:\VisionIQ
git clone https://github.com/iceamonwat09/digital_vision2026.git Digital_Vision2026
cd Digital_Vision2026
```

**วิธีทางเลือก (ไม่มี Git):** คัดลอกทั้งโฟลเดอร์โปรเจกต์จากเครื่องสถานีมาวางที่
`C:\VisionIQ\Digital_Vision2026` — แต่ **อย่าคัดลอกโฟลเดอร์ `.venv` เดิมมาด้วย**
(venv ผูกกับ path เครื่องเดิม ย้ายมาแล้วพัง)

> ⚠️ **อย่าวางโค้ดใน `C:\inetpub\wwwroot`** — ตรงนั้นเป็นของ Default Web Site
> การแยกโฟลเดอร์ทำให้สิทธิ์และการ backup ชัดเจนกว่า

---

### STEP 5 — สร้าง venv และติดตั้ง package

> **ทำไมต้อง venv?** เพราะ IIS จะรันแอปด้วยบัญชี **Application Pool Identity**
> ไม่ใช่บัญชีของคุณ — package ที่ `pip install` ลงไปที่ user-site ของคุณ
> (`%APPDATA%\Python\...`) **บัญชีนั้นมองไม่เห็น** ผลคือชั้นตรวจ dictionary
> (`pyspellchecker`) จะหายไปแบบเงียบ ๆ = คำผิดขึ้นเครื่องหมายถูก = **จุดบอด QC**
> การทำ venv ในโฟลเดอร์โปรเจกต์ทำให้ package อยู่กับโค้ด ทุก identity เห็นเท่ากัน

```cmd
cd /d C:\VisionIQ\Digital_Vision2026

REM 1) สร้าง venv
C:\Python39\python.exe -m venv .venv

REM 2) อัปเกรด pip ก่อน (กัน wheel รุ่นใหม่ติดตั้งไม่ได้)
.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel

REM 3) ติดตั้ง package ทั้งหมด  ** ใช้เวลา 10-20 นาที (torch ไฟล์ใหญ่) **
.venv\Scripts\python.exe -m pip install -r deploy\requirements-server.txt
```

**ยืนยันผล:**
```cmd
.venv\Scripts\python.exe -c "import flask, cv2, ultralytics, pyodbc, bcrypt, jwt, fitz, waitress; print('imports OK')"
```
ต้องขึ้น `imports OK`

> 💡 ถ้าติดตั้ง torch ช้าหรือหลุดกลางทาง ให้รันคำสั่ง `pip install` ซ้ำได้เลย
> (pip จะข้ามตัวที่ลงเสร็จแล้ว)

---

### STEP 6 — ติดตั้ง Tesseract-OCR (แนะนำ แต่ไม่บังคับ)

ใช้สำหรับ **กรอบแดงชี้คำที่ผิด** บนรูป crop ในการ์ด "รายการที่พบ"
ไม่ติดตั้ง = ระบบยังตรวจถูกต้องทุกอย่าง แค่ไม่มีกรอบแดงบนไฟล์ที่เป็น outline/ภาพถ่าย

1. ดาวน์โหลดจาก `https://github.com/UB-Mannheim/tesseract/wiki`
   → เลือกไฟล์ `tesseract-ocr-w64-setup-5.x.x.exe`
2. คลิกขวา → **Run as administrator** → **Next** → **I Agree**
3. หน้า **Choose Users** → เลือก **Install for anyone using this computer** → **Next**
4. หน้า **Choose Components** — ⚠️ **จุดสำคัญที่สุดของ step นี้**
   - คลิกเครื่องหมาย **+** หน้า **Additional language data (download)**
   - ติ๊กภาษาที่ฉลากของคุณมี เช่น:
     - ☑ Arabic
     - ☑ Hebrew
     - ☑ Chinese (traditional) / Chinese (simplified)
     - ☑ Thai
   - (English ติ๊กมาให้อยู่แล้ว)
   - กด **Next**
5. หน้า **Choose Install Location** → **ปล่อยค่าเดิม** `C:\Program Files\Tesseract-OCR`
   (โค้ดมีตัวหาไฟล์ `tesseract.exe` ให้อัตโนมัติที่ path นี้ — ไม่ต้องตั้ง PATH เอง)
6. **Install** → **Finish**

**ยืนยันผล:**
```cmd
cd /d C:\VisionIQ\Digital_Vision2026
.venv\Scripts\python.exe -c "import pytesseract; print(pytesseract.get_tesseract_version())"
```

> ⚠️ **กับดักที่เคยเจอมาแล้ว:** ถ้าตั้งค่า `ARTWORK_HIGHLIGHT_TESS_LANG` เป็นภาษาที่
> **ไม่ได้ติดตั้ง** จะทำให้ Tesseract error ทั้งการเรียก → **กรอบแดงหายหมดแม้แต่ภาษาอังกฤษ**
> (โค้ดมีตัวกรองภาษาที่ไม่มีออกให้แล้ว แต่ตั้งให้ตรงกับที่ติดตั้งจริงย่อมดีกว่า)

---

### STEP 7 — ตั้งค่าการเชื่อมต่อ SQL Server

⚠️ ค่าเหล่านี้ **ไม่ได้อ่านจาก environment variable** ต้องแก้ในไฟล์โดยตรง

1. เปิด `C:\VisionIQ\Digital_Vision2026\config.py` ด้วย Notepad / VS Code
2. เลื่อนไปที่ **บรรทัด 242–245** แก้ให้ตรงกับเซิร์ฟเวอร์จริง:
```python
SQL_SERVER   = "172.32.0.50"       # IP Address ของ SQL Server
SQL_DATABASE = "VisionIQ"          # ชื่อ Database
SQL_USER     = "sa"                # SQL Server Login
SQL_PASSWORD = "P@ssw0rd"          # รหัสผ่าน
```
3. บันทึกไฟล์ (**Save as UTF-8** ถ้าโปรแกรมถาม)

> 🔒 **ข้อควรระวังด้านความปลอดภัย (แจ้งไว้เพื่อการตัดสินใจ):** ไฟล์ `config.py`
> อยู่ใน git และเก็บรหัสผ่านฐานข้อมูลเป็นข้อความธรรมดา ควรพิจารณา
> (ก) เปลี่ยนจากบัญชี `sa` เป็น login เฉพาะแอปที่มีสิทธิ์เท่าที่จำเป็น และ
> (ข) หลังแก้บนเซิร์ฟเวอร์แล้ว ระวังอย่าเผลอ commit รหัสผ่านกลับขึ้น repo
> (สั่ง `git update-index --skip-worktree config.py` บนเซิร์ฟเวอร์จะช่วยกันได้ระดับหนึ่ง)

---

### STEP 8 — สร้างตารางระบบล็อกอิน + ผู้ใช้คนแรก

#### 8.1 รันสคริปต์สร้างตาราง

**วิธีที่ 1 — ผ่าน `sqlcmd`** (ถ้ามีติดตั้ง):
```cmd
cd /d C:\VisionIQ\Digital_Vision2026
sqlcmd -f 65001 -S 172.32.0.50 -d VisionIQ -U sa -P "P@ssw0rd" -i Connection_sql\auth_schema.sql
```

> ⚠️ **`-f 65001` ห้ามลืมเด็ดขาด** — ถ้าลืม ข้อความภาษาไทยในตาราง (ชื่อ role/สิทธิ์)
> จะเพี้ยนเป็นตัวอักษรขยะ (mojibake) ถ้าเผลอลืมไปแล้ว มีสคริปต์แก้ให้ที่
> `Connection_sql/fix_thai_encoding.sql`

**วิธีที่ 2 — ผ่าน SQL Server Management Studio (SSMS):**
1. เปิด SSMS → เชื่อมต่อไปที่ `172.32.0.50` (SQL Server Authentication, user `sa`)
2. เมนู **File** → **Open** → **File…** → เลือก
   `C:\VisionIQ\Digital_Vision2026\Connection_sql\auth_schema.sql`
3. ที่แถบเครื่องมือด้านบน มีกล่อง dropdown แสดงชื่อฐานข้อมูล → **เลือก `VisionIQ`**
4. กด **Execute** (หรือ **F5**)
5. ที่แท็บ **Messages** ต้องขึ้นข้อความ
   `auth_schema.sql applied. Next: python -m auth.seed_admin ...`

> สคริปต์นี้ **รันซ้ำได้ปลอดภัย** (idempotent) — ถ้าตารางมีอยู่แล้วจะไม่ทำอะไร

#### 8.2 สร้างผู้ใช้ admin คนแรก

```cmd
cd /d C:\VisionIQ\Digital_Vision2026
.venv\Scripts\python.exe -m auth.seed_admin --username admin --password "Str0ng!Pass1"
```

ต้องขึ้น: `✓ พร้อมใช้งาน: user 'admin' (role=Admin, id=1)`

**เงื่อนไขรหัสผ่าน** (บังคับทั้งฝั่งเซิร์ฟเวอร์และหน้าเว็บ):
- ยาวอย่างน้อย **8 ตัวอักษร**
- มีตัวพิมพ์ใหญ่ `A-Z` อย่างน้อย 1 ตัว
- มีตัวพิมพ์เล็ก `a-z` อย่างน้อย 1 ตัว
- มีตัวเลข `0-9` อย่างน้อย 1 ตัว
- มีอักขระพิเศษจากชุด `!@#$%^&*` อย่างน้อย 1 ตัว

> คำสั่งนี้รันซ้ำได้ — ถ้ามี user อยู่แล้วจะเป็นการ **รีเซ็ตรหัสผ่าน** ให้แทน
> (ใช้ตอนลืมรหัส admin ได้)

#### 8.3 ตรวจความพร้อมทั้งหมดด้วยสคริปต์

```cmd
.venv\Scripts\python.exe deploy\check_server.py
```

จะพิมพ์ผลเป็นข้อ ๆ — **ต้องไม่มี `[FAIL]` เหลืออยู่** ก่อนไป STEP ถัดไป

---

### STEP 9 — ทดสอบรันตรง ๆ ก่อนต่อ IIS (อย่าข้าม!)

**เหตุผล:** ถ้าตรงนี้ไม่ผ่าน แล้วไปนั่งแก้ IIS จะหาสาเหตุไม่เจอเลย
ขั้นนี้แยกให้ชัดว่าปัญหาอยู่ที่ "แอป" หรือ "IIS"

```cmd
cd /d C:\VisionIQ\Digital_Vision2026
set PYTHONIOENCODING=utf-8
set AUTH_ENABLED=1
set AUTH_COOKIE_SECURE=0
set VISIONIQ_IIS_INIT=db
.venv\Scripts\python.exe deploy\wsgi_iis.py
```

จะเห็นแบนเนอร์แบบนี้ (บอกทุกอย่างที่ต้องรู้):
```
======================================================================
  VisionIQ — IIS / waitress entry point
  CONFIG_VERSION   : 2026.08.07-aw-redbox-stable
  Python           : 3.9.13
  Listen           : 127.0.0.1:8000
  VISIONIQ_IIS_INIT: db (ต่อ SQL Server สำเร็จ)
  AUTH_ENABLED=True
  ...
  dependency ที่ 'ขาดแล้วเงียบ':
    pyodbc=OK  bcrypt=OK  PyJWT=OK  PyMuPDF=OK
    pyspellchecker=OK  pytesseract=OK
======================================================================
```

จากนั้นเปิดเบราว์เซอร์ **บนเซิร์ฟเวอร์** ไปที่:

| URL | ต้องเห็นอะไร |
|---|---|
| `http://localhost:8000/login` | หน้าล็อกอิน (มีช่องชื่อผู้ใช้/รหัสผ่าน) |
| ล็อกอินด้วย `admin` | เด้งไปหน้า `/home` แสดงเมนูแบบไทล์ |
| กดไทล์ **ตรวจ Artwork** | เข้าหน้า `/artwork_check` ได้ |
| ด้านล่างสุดของหน้า | มี `CONFIG_VERSION` แสดงอยู่ (ยืนยันว่ารันโค้ดจริง) |

ปิดโปรแกรมด้วย **Ctrl + C**

---

### STEP 10 — สร้าง Application Pool ใน IIS

1. เปิด **IIS Manager** (`inetmgr`)
2. แถบซ้าย → คลิก **Application Pools**
3. แถบขวา (Actions) → คลิก **Add Application Pool…**
4. กรอกในหน้าต่างที่เด้งขึ้น:
   - **Name:** `VisionIQPool`
   - **.NET CLR version:** เลือก **No Managed Code** ← ⚠️ สำคัญ
   - **Managed pipeline mode:** `Integrated`
   - ☑ Start application pool immediately
   - กด **OK**
5. คลิกเลือก `VisionIQPool` ที่เพิ่งสร้าง → แถบขวา คลิก **Advanced Settings…**
6. แก้ค่าต่อไปนี้ (ดับเบิลคลิกที่ค่าเพื่อแก้):

| หัวข้อ | ค่าที่ตั้ง | ทำไม |
|---|---|---|
| **General → Start Mode** | `AlwaysRunning` | ให้แอปพร้อมตลอด ไม่ต้องรอบูตตอนมีคนเข้าครั้งแรก (torch โหลดนาน) |
| **Process Model → Identity** | `ApplicationPoolIdentity` (ค่าเดิม) | จำชื่อไว้ว่า `IIS AppPool\VisionIQPool` — ใช้ตอนให้สิทธิ์โฟลเดอร์ |
| **Process Model → Idle Time-out (minutes)** | `0` | ค่าเดิม 20 นาที = ไม่มีคนใช้ 20 นาทีแล้วดับ ครั้งต่อไปต้องรอบูตใหม่ |
| **Process Model → Load User Profile** | `True` | บาง package ต้องเขียนโฟลเดอร์ temp ของ profile |
| **Recycling → Regular Time Interval (minutes)** | `0` | ค่าเดิม 1740 นาที (29 ชม.) = แอปจะรีสตาร์ตเองกลางวัน อาจตัดงานตรวจที่ค้างอยู่ |

7. กด **OK**

---

### STEP 11 — สร้าง Website

1. IIS Manager → แถบซ้าย คลิกขวาที่ **Sites** → **Add Website…**
2. กรอก:
   - **Site name:** `VisionIQ`
   - **Application pool:** กด **Select…** → เลือก `VisionIQPool` → **OK**
   - **Physical path:** `C:\VisionIQ\Digital_Vision2026`
   - **Binding**
     - Type: `http`
     - IP address: `All Unassigned`
     - Port: **`8080`** ← ใช้พอร์ตนี้ทดสอบก่อน แล้วค่อยเพิ่ม 443 ทีหลัง
     - Host name: (เว้นว่างไว้ก่อน)
   - ☑ Start Website immediately
3. กด **OK**

> ⚠️ **ถ้าอยากใช้พอร์ต 80 เลย** ต้องหยุด Default Web Site ก่อน:
> Sites → **Default Web Site** → แถบขวา **Stop**
> (หรือแก้ binding ของ Default Web Site ไปพอร์ตอื่น)

---

### STEP 12 — วางไฟล์ `web.config`

1. คัดลอกไฟล์ต้นแบบ:
```cmd
cd /d C:\VisionIQ\Digital_Vision2026
copy deploy\web.config.example web.config
```
2. สร้างค่า secret สำหรับเซ็นโทเคน (คัดลอกผลลัพธ์เก็บไว้):
```cmd
.venv\Scripts\python.exe -c "import secrets;print(secrets.token_urlsafe(48))"
```
3. เปิด `C:\VisionIQ\Digital_Vision2026\web.config` ด้วย Notepad แล้วแก้ **4 จุด**:

| จุดที่ต้องแก้ | แก้เป็นอะไร |
|---|---|
| `processPath="C:\VisionIQ\Digital_Vision2026\.venv\Scripts\python.exe"` | path จริงของ venv |
| `arguments="C:\VisionIQ\Digital_Vision2026\deploy\wsgi_iis.py"` | path จริงของสคริปต์ |
| `workingDirectory` และ `stdoutLogFile` | path จริงของโปรเจกต์ |
| `<environmentVariable name="AUTH_JWT_SECRET" value="__CHANGE_ME__" />` | ใส่ค่าที่สุ่มได้จากข้อ 2 |

4. ⚠️ **ตอนนี้ยังเป็น `http://` อยู่** → หา `AUTH_COOKIE_SECURE` แล้วตั้งเป็น **`0`**
   ก่อน (จะกลับมาเปลี่ยนเป็น `1` ใน STEP 16 หลังผูก HTTPS แล้ว)
```xml
<environmentVariable name="AUTH_COOKIE_SECURE" value="0" />
```
5. บันทึกไฟล์

> 💡 **ตำแหน่งของ `web.config` ต้องอยู่ที่รากของ Physical path** ที่ตั้งไว้ใน STEP 11
> คือ `C:\VisionIQ\Digital_Vision2026\web.config` (ไม่ใช่ในโฟลเดอร์ `deploy\`)

---

### STEP 13 — ให้สิทธิ์เขียนไฟล์แก่ Application Pool

**ทำไมต้องทำ:** แอปเขียนไฟล์อยู่ 3 ที่ ถ้าเขียนไม่ได้จะพังทันทีตอนบูตหรือตอนอัปโหลด
- `logs\` — สร้างอัตโนมัติตอน import (`logger.py:13-14`)
- `data\auth\secret.key` — คีย์เซ็นโทเคน (`auth/config.py:87-98`)
- `data\artwork_check\` — ไฟล์ artwork + ผลตรวจทั้งหมด

**ขั้นตอน:**
1. เปิด **File Explorer** → ไปที่ `C:\VisionIQ`
2. **คลิกขวา** ที่โฟลเดอร์ `Digital_Vision2026` → **Properties**
3. ไปแท็บ **Security** → กดปุ่ม **Edit…**
4. กดปุ่ม **Add…**
5. ในช่องข้อความ พิมพ์ **`IIS AppPool\VisionIQPool`** → กด **Check Names**
   → ข้อความจะเปลี่ยนเป็นขีดเส้นใต้ `VisionIQPool` → กด **OK**
   - ⚠️ ถ้าขึ้น "name not found" แปลว่าพิมพ์ผิด หรือยังไม่ได้สร้าง App Pool ใน STEP 10
   - ⚠️ ถ้าปุ่ม Locations ชี้ไปที่ Domain ให้เปลี่ยนเป็น **ชื่อเครื่องเซิร์ฟเวอร์** ก่อน
6. เลือกบรรทัด `VisionIQPool` ที่เพิ่งเพิ่ม → ในช่อง Permissions ด้านล่าง
   **ติ๊ก ☑ Modify** (ระบบจะติ๊ก Read & execute / List / Read / Write ให้เอง)
7. กด **Apply** → **OK** → **OK**

> 🔒 **แบบรัดกุมกว่า (ถ้า IT ต้องการ):** ให้ทั้งโฟลเดอร์แค่ **Read & execute**
> แล้วให้ **Modify** เฉพาะ 2 โฟลเดอร์ย่อยคือ `data\` และ `logs\`
> (ผลคือโค้ดถูกแก้ไม่ได้แม้แอปถูกเจาะ)

---

### STEP 14 — ขยายเพดานขนาดไฟล์อัปโหลด

ถ้าคุณใช้ `web.config.example` ตามต้นแบบ **ข้อนี้ทำให้แล้ว** (มี `maxAllowedContentLength`
= 83886080 ไบต์ = 80 MB) ข้ามไป STEP 15 ได้

**ถ้าอยากตั้งผ่านหน้าจอ IIS แทน:**
1. IIS Manager → แถบซ้าย เลือกเว็บไซต์ **VisionIQ**
2. แผงกลาง ดับเบิลคลิก **Request Filtering**
3. แถบขวา คลิก **Edit Feature Settings…**
4. ช่อง **Maximum allowed content length (Bytes)** → ใส่ `83886080`
5. กด **OK**

> ❌ ถ้าไม่ทำ: อัปโหลด PDF ขนาด 30–40 MB จะขึ้น **HTTP 404.13 – Content length too large**
> โดยที่แอปไม่เคยเห็น request เลย (IIS ปัดตกก่อน)

---

### STEP 15 — ทดสอบผ่าน IIS

1. IIS Manager → เลือกเว็บไซต์ **VisionIQ** → แถบขวา คลิก **Restart**
   (หรือไปที่ Application Pools → `VisionIQPool` → **Recycle…**)
2. เปิดเบราว์เซอร์ **บนเซิร์ฟเวอร์** → `http://localhost:8080/login`
3. ล็อกอินด้วย `admin` → ต้องเข้าหน้า `/home` ได้
4. เปิดจาก **เครื่องอื่นใน LAN** → `http://<ip-เซิร์ฟเวอร์>:8080/login`
   (ถ้าเปิดไม่ได้ ให้ไปทำ STEP 17 เรื่อง firewall ก่อน)

**ถ้าเปิดไม่ขึ้น — ดู log ตรงนี้เป็นอันดับแรก:**
```
C:\VisionIQ\Digital_Vision2026\logs\iis-stdout_*.log     ← ข้อความจากโปรเซส Python
C:\VisionIQ\Digital_Vision2026\logs\errors.log           ← error ของแอปเอง
C:\inetpub\logs\LogFiles\W3SVC<n>\                       ← access log ของ IIS
```

ไฟล์ `iis-stdout_*.log` จะมีแบนเนอร์เดียวกับ STEP 9 — ถ้าไม่มีไฟล์นี้เลย แปลว่า
โปรเซส Python ยังไม่เคยเริ่มด้วยซ้ำ (มักเป็นเรื่อง path ผิดใน `web.config`
หรือสิทธิ์เขียนโฟลเดอร์ `logs\`)

---

### STEP 16 — เปิด HTTPS (แนะนำอย่างยิ่ง)

**ทำไมสำคัญ:** ถ้าใช้ `http://` ธรรมดา **รหัสผ่านของผู้ใช้จะวิ่งบนเครือข่ายแบบไม่เข้ารหัส**
และ cookie ที่ใช้ยืนยันตัวตนก็ถูกดักได้

#### 16.1 นำใบรับรองเข้าเครื่อง

**กรณี A — มีใบรับรองจาก IT / CA ภายในองค์กร (แนะนำ):**
1. IIS Manager → แถบซ้าย **คลิกที่ชื่อเครื่องเซิร์ฟเวอร์** (node บนสุด)
2. แผงกลาง ดับเบิลคลิก **Server Certificates**
3. แถบขวา คลิก **Import…**
4. **Certificate file (.pfx):** เลือกไฟล์ที่ IT ให้มา → ใส่ **Password**
5. **Certificate store:** `Web Hosting` → กด **OK**

**กรณี B — ยังไม่มีใบรับรอง (ทดสอบภายในก่อน):**
1. ที่หน้า **Server Certificates** เดิม → แถบขวาคลิก **Create Self-Signed Certificate…**
2. **Friendly name:** `VisionIQ` → **Certificate store:** `Web Hosting` → **OK**
3. ⚠️ เบราว์เซอร์จะขึ้นคำเตือน "การเชื่อมต่อไม่ปลอดภัย" ทุกครั้ง —
   ใช้ชั่วคราวได้ แต่ควรขอใบรับรองจริงจาก IT

#### 16.2 ผูก binding HTTPS

1. IIS Manager → **Sites** → **VisionIQ** → แถบขวา คลิก **Bindings…**
2. กด **Add…**
   - **Type:** `https`
   - **IP address:** `All Unassigned`
   - **Port:** `443`
   - **Host name:** ชื่อ FQDN ที่ผู้ใช้จะพิมพ์ เช่น `visioniq.company.local`
     (ถ้าใช้ self-signed ให้เว้นว่าง)
   - **SSL certificate:** เลือกใบที่นำเข้าใน 16.1
   - กด **OK** → **Close**

#### 16.3 บอกแอปว่าตอนนี้เป็น HTTPS แล้ว

1. เปิด `C:\VisionIQ\Digital_Vision2026\web.config`
2. แก้บรรทัด:
```xml
<environmentVariable name="AUTH_COOKIE_SECURE" value="1" />
```
3. บันทึก → IIS Manager → Application Pools → `VisionIQPool` → **Recycle…**

> ⚠️ **ห้ามตั้ง `AUTH_COOKIE_SECURE=1` ตอนที่ผู้ใช้ยังเข้าด้วย `http://`**
> เพราะเบราว์เซอร์จะทิ้งคุกกี้ทันที ผลคือ "ล็อกอินสำเร็จแต่เด้งกลับหน้า login วนไม่จบ"

#### 16.4 (ทางเลือก) บังคับ redirect http → https

ต้องติดตั้งโมดูล **URL Rewrite** ก่อน (`https://www.iis.net/downloads/microsoft/url-rewrite`)
แล้วเพิ่มใน `web.config` ภายใน `<system.webServer>`:

```xml
<rewrite>
  <rules>
    <rule name="Redirect to HTTPS" stopProcessing="true">
      <match url="(.*)" />
      <conditions>
        <add input="{HTTPS}" pattern="^OFF$" />
      </conditions>
      <action type="Redirect" url="https://{HTTP_HOST}/{R:1}"
              redirectType="Permanent" />
    </rule>
  </rules>
</rewrite>
```

---

### STEP 17 — เปิด Firewall

1. กด Start → พิมพ์ **Windows Defender Firewall with Advanced Security** → เปิด
2. แถบซ้าย คลิก **Inbound Rules** → แถบขวา คลิก **New Rule…**
3. **Rule Type:** เลือก **Port** → **Next**
4. เลือก **TCP** → ช่อง **Specific local ports:** ใส่ `443,8080` → **Next**
5. **Action:** **Allow the connection** → **Next**
6. **Profile:** ติ๊ก ☑ **Domain** และ ☑ **Private**
   (❌ **ไม่ควรติ๊ก Public** ถ้าเซิร์ฟเวอร์อยู่ในเครือข่ายโรงงาน) → **Next**
7. **Name:** `VisionIQ Web` → **Finish**

---

## 4. สร้าง Role และผู้ใช้สำหรับงาน Artwork

ตอนนี้ระบบพร้อมแล้ว เหลือแค่จัดสิทธิ์ให้ผู้ใช้จริง — ทำผ่านหน้าเว็บทั้งหมด

### 4.1 ตารางสิทธิ์ทั้งหมดของระบบ

| คีย์สิทธิ์ | ชื่อในหน้าจอ | ปลดล็อกอะไร | ควรให้ผู้ใช้ Artwork ไหม |
|---|---|---|---|
| `inspect_artwork` | ตรวจ Artwork | หน้า `/artwork_check` + API ทั้งหมดของโหมดนี้ | ✅ **ต้องมี** |
| `view_history` | ดูประวัติการตรวจ | `/artwork_check/history`, `/history`, `/label_paper/history` | ✅ ควรมี (ถ้าต้องเปิดผลย้อนหลัง) |
| `view_dashboard` | ดูแดชบอร์ด | `/dashboard` | 🟡 แล้วแต่ (เป็นสถิติของโหมดกระป๋อง) |
| `inspect_label_paper` | ตรวจฉลากกระดาษ | `/label_paper` | ❌ ไม่ต้อง (ถ้าไม่ได้ใช้โหมดนี้) |
| `run_live_detection` | ตรวจจับสด + ถ่ายรูปตรวจ | `/` (หน้าแรกกล้อง), `/video_feed`, API กล้อง | ❌ **ไม่ควรให้** — เซิร์ฟเวอร์ไม่มีกล้อง กดไปก็ใช้ไม่ได้ |
| `manage_users` | จัดการผู้ใช้และสิทธิ์ | `/admin/users` | ❌ ให้เฉพาะผู้ดูแล |

### 4.2 สร้าง Role ใหม่ชื่อ `ArtworkUser`

> **ทำไมไม่ใช้ role `Staff` ที่มีอยู่?** เพราะ `Staff` **ไม่มี `view_history`**
> (เปิดประวัติผลตรวจ artwork ย้อนหลังไม่ได้) แต่ **มี `run_live_detection`**
> ซึ่งบนเซิร์ฟเวอร์นี้ใช้ไม่ได้อยู่ดี — ตรงข้ามกับที่ต้องการทั้งสองข้อ

1. ล็อกอินด้วยบัญชี `admin`
2. ที่เมนูด้านบน คลิก **จัดการผู้ใช้** (หรือเข้า `https://<server>/admin/users` โดยตรง)
3. ไปที่ส่วน **Role / สิทธิ์** → กดปุ่มสร้าง role ใหม่
   - **ชื่อ:** `ArtworkUser` (ใช้ได้เฉพาะ A-Z / 0-9 / `_` / เว้นวรรค ยาว 2–50 ตัว)
   - **คำอธิบาย:** `ผู้ใช้งานตรวจ Artwork (ผ่านเว็บ)`
   - **ติ๊กสิทธิ์:** ☑ `ตรวจ Artwork` ☑ `ดูประวัติการตรวจ`
   - บันทึก

### 4.3 เพิ่มผู้ใช้

ที่หน้าเดียวกัน ส่วน **ผู้ใช้**:
1. กดปุ่มเพิ่มผู้ใช้
2. กรอก **Username** / **Email** (ไม่บังคับ) / **รหัสผ่าน** (ตามเงื่อนไขใน STEP 8.2)
3. **Role:** เลือก `ArtworkUser`
4. บันทึก

### 4.4 สิ่งที่ผู้ใช้ทำได้เองหลังจากนี้

| ความสามารถ | วิธี |
|---|---|
| เปลี่ยนรหัสผ่านตัวเอง | ต้องใส่รหัสผ่านปัจจุบัน (API `POST /api/auth/me/password`) |
| ถูกล็อกเพราะใส่รหัสผิด | ผิด **5 ครั้ง** → ล็อก **15 นาที** (ปรับได้ด้วย `AUTH_MAX_FAILED` / `AUTH_LOCK_MINUTES`) |
| แอดมินปลดล็อกให้ทันที | `/admin/users` → ปุ่มปลดล็อกที่แถวของผู้ใช้คนนั้น |
| แอดมินรีเซ็ตรหัสให้ | `/admin/users` → ปุ่มรีเซ็ตรหัสผ่าน |

> 🔍 ทุกการล็อกอิน (สำเร็จ/ล้มเหลว) ถูกบันทึกลงตาราง `AuthLoginAudit`
> และทุกการแก้ผู้ใช้/สิทธิ์ลงตาราง `AuthAdminAudit` — ตรวจสอบย้อนหลังได้ด้วย SQL

---

## 5. รายการตรวจรับหลัง Deploy

ติ๊กให้ครบก่อนส่งมอบให้ผู้ใช้จริง:

- [ ] `deploy\check_server.py` รันแล้ว **ไม่มี `[FAIL]`**
- [ ] เปิด `https://<server>/login` จากเครื่องผู้ใช้ (ไม่ใช่บนเซิร์ฟเวอร์) ได้
- [ ] ล็อกอินด้วยบัญชี `ArtworkUser` ที่สร้างใหม่ผ่าน
- [ ] หน้า `/home` แสดง **เฉพาะ** ไทล์ "ตรวจ Artwork" และ "ประวัติการตรวจ"
      (ไม่มีไทล์กล้อง — ยืนยันว่า RBAC ทำงาน)
- [ ] ลองเปิด `https://<server>/dashboard` ด้วยบัญชีนั้น → ต้องขึ้นหน้า **403** (ถูกต้อง)
- [ ] อัปโหลดไฟล์ PDF จริง ขนาด **> 30 MB** ได้ (ทดสอบเพดาน IIS)
- [ ] ลากโซนแล้วกดตรวจ → ได้ผลกลับมาครบ **ไม่ timeout กลางคัน**
- [ ] การ์ด "รายการที่พบ" มี **กรอบแดง** ชี้คำที่ผิด (ถ้าติดตั้ง Tesseract แล้ว)
- [ ] คอลัมน์ "สถานะ" จับคำสะกดผิดได้ (ยืนยันว่า `pyspellchecker` ทำงาน — ถ้าคำผิดขึ้น ✓ หมด แสดงว่าชั้นนี้หาย)
- [ ] `CONFIG_VERSION` ที่ท้ายหน้าเว็บตรงกับใน `config.py`
- [ ] ปิด-เปิดเซิร์ฟเวอร์ (restart) แล้วเว็บกลับมาเองโดยไม่ต้องสั่งอะไร
- [ ] ล็อกอินค้างไว้ข้ามคืน แล้ววันรุ่งขึ้นยังใช้ได้ (refresh token 7 วันทำงาน)
- [ ] เช็คตาราง `AuthLoginAudit` มีบันทึกการล็อกอินจริง

---

## 6. ปัญหาที่พบบ่อย (อาการ → สาเหตุ → วิธีแก้)

### 6.1 กลุ่ม "เว็บไม่ขึ้นเลย"

| อาการ | สาเหตุที่เป็นไปได้ | วิธีแก้ |
|---|---|---|
| **HTTP 500.19** — `unrecognized element 'httpPlatform'` | ยังไม่ได้ติดตั้ง HttpPlatformHandler | ทำ STEP 3 แล้วตรวจใน Modules ให้เห็นชื่อจริง |
| **HTTP 500.19** — config error อื่น ๆ | XML ใน `web.config` พิมพ์ผิด (ลืมปิดแท็ก / เครื่องหมายคำพูด) | เปิดไฟล์ด้วยเบราว์เซอร์ ถ้า XML ผิดจะฟ้องบรรทัดที่ผิด |
| **HTTP 502.5** — "The process failed to start" | `processPath` ชี้ไป python ผิดที่ / venv พัง / import error | เปิด `logs\iis-stdout_*.log`; ถ้าไม่มีไฟล์เลย = path ผิดแน่นอน |
| **HTTP 503** — Service Unavailable | Application Pool หยุดทำงาน (มัก crash ซ้ำจนถูกปิด) | IIS Manager → Application Pools → ดูสถานะ → กด **Start** แล้วดู Event Viewer |
| ไม่มีไฟล์ `logs\iis-stdout_*.log` เกิดขึ้นเลย | โฟลเดอร์ `logs\` เขียนไม่ได้ / `stdoutLogEnabled` เป็น false | ทำ STEP 13 ให้สิทธิ์ Modify |
| เปิดจากเซิร์ฟเวอร์ได้ แต่เครื่องอื่นเปิดไม่ได้ | Firewall | ทำ STEP 17 |

### 6.2 กลุ่ม "ล็อกอินไม่ผ่าน"

| อาการ | สาเหตุ | วิธีแก้ |
|---|---|---|
| ล็อกอินสำเร็จ แต่**เด้งกลับหน้า login วนไม่จบ** | `AUTH_COOKIE_SECURE=1` แต่เข้าเว็บด้วย `http://` → เบราว์เซอร์ทิ้งคุกกี้ | ตั้งเป็น `0` (ถ้ายังไม่มี HTTPS) หรือทำ STEP 16 ให้ครบ |
| ขึ้น **"ระบบฐานข้อมูลไม่พร้อม (pyodbc ไม่ได้ติดตั้ง)"** | `pyodbc` ไม่ได้อยู่ใน venv ที่ IIS ใช้จริง | `.venv\Scripts\python.exe -m pip install pyodbc` แล้ว Recycle pool |
| ขึ้น **"ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"** ทั้งที่มั่นใจว่าถูก | (ก) ยังไม่ได้ seed ผู้ใช้ (ข) `bcrypt` ไม่ได้ติดตั้ง → ตรวจรหัสผ่านไม่ผ่านเสมอ | รัน `deploy\check_server.py` แล้วดูข้อ bcrypt / จำนวนผู้ใช้ |
| ขึ้น **"บัญชีถูกล็อกชั่วคราว"** | ใส่รหัสผิดครบ 5 ครั้ง | รอ 15 นาที หรือให้แอดมินกดปลดล็อกที่ `/admin/users` |
| ล็อกอินได้ปกติ แต่**หลุดทุกครั้งที่รีสตาร์ตเซิร์ฟเวอร์** | ไม่ได้ตั้ง `AUTH_JWT_SECRET` และเขียนไฟล์ `data\auth\secret.key` ไม่ได้ → ได้ secret ใหม่ทุกครั้ง | ตั้ง `AUTH_JWT_SECRET` ใน `web.config` (STEP 12) |
| ล็อกอินได้ แต่เปิดหน้าไหนก็ **403** | role ของผู้ใช้ไม่มีสิทธิ์ที่หน้านั้นต้องการ (หน้า 403 บอกคีย์สิทธิ์ให้แล้ว) | แก้สิทธิ์ role ที่ `/admin/users` (ตอนที่ 4) |
| ชื่อ role / ชื่อสิทธิ์ในหน้าจอเป็น **ตัวอักษรขยะ** | รัน `auth_schema.sql` โดยลืม `-f 65001` | รัน `Connection_sql\fix_thai_encoding.sql` (ป้ายชื่อสิทธิ์ในหน้าเว็บดึงจากโค้ดอยู่แล้ว จึงกระทบแค่ชื่อ role) |

### 6.3 กลุ่ม "ตรวจ Artwork มีปัญหา"

| อาการ | สาเหตุ | วิธีแก้ |
|---|---|---|
| **HTTP 404.13** ตอนอัปโหลดไฟล์ใหญ่ | เพดาน IIS (~28.6 MB) เล็กกว่าเพดานแอป (40 MB) | STEP 14 |
| ตรวจไปสักพักแล้ว **502.3 / หน้าค้าง** | timeout สั้นเกินไป | เพิ่ม `requestTimeout` ใน `web.config` และ `VISIONIQ_WSGI_TIMEOUT` |
| ทุกโซนขึ้น error **"ไม่ได้ตั้งค่า OCR backend"** | เซิร์ฟเวอร์ต่อ N8N ไม่ถึง หรือไม่ได้ตั้ง URL | `deploy\check_server.py` ข้อ 7 จะบอก; ตรวจ firewall/route ไปยัง `172.32.201.106:5678` |
| **ไม่มีกรอบแดง**บนการ์ด "รายการที่พบ" เลย | (ก) ไม่ได้ติดตั้ง Tesseract (ข) ตั้ง `ARTWORK_HIGHLIGHT_TESS_LANG` เป็นภาษาที่ไม่ได้ติดตั้ง | STEP 6; ตรวจด้วย `check_server.py` ข้อ 3 |
| กรอบแดงขึ้นบ้างไม่ขึ้นบ้าง เฉพาะบางโซน | **โซนถูกลากกว้างเกินไป** — ตัวหนังสือในภาพ crop เล็กกว่า ~9 px หาคำไม่เจอ (เป็นข้อจำกัดเชิงกายภาพ แก้ด้วยโค้ดไม่ได้) | ลากโซนให้กระชับรอบตาราง/บล็อกข้อความ ระบบมีคำเตือนขึ้นให้ตอนจัดโซนอยู่แล้ว |
| คำสะกดผิดขึ้นเครื่องหมาย **✓** เหมือนไม่มีปัญหา | `pyspellchecker` ไม่ได้ติดตั้ง → ชั้นตรวจ dictionary **หายเงียบ** (จุดบอด QC) | `.venv\Scripts\python.exe -m pip install pyspellchecker` แล้ว Recycle |
| กดวินิจฉัยลึกว่าทำไมกรอบไม่ขึ้น | — | `.venv\Scripts\python.exe diagnose_highlight.py <inspection-id> --save` |

### 6.4 กลุ่ม "พฤติกรรมแปลก ๆ"

| อาการ | คำอธิบาย |
|---|---|
| กดเมนู **"ตรวจจับสด"** แล้วใช้ไม่ได้ / ไม่มีภาพ | **เป็นเรื่องปกติ** — เซิร์ฟเวอร์ไม่มีกล้อง USB และ `VISIONIQ_IIS_INIT=db` ไม่โหลดโมเดล วิธีที่ถูกคือ **ไม่ให้สิทธิ์ `run_live_detection`** กับผู้ใช้บนเซิร์ฟเวอร์นี้ |
| แดชบอร์ดขึ้นเลข **0** ทั้งหมด | ตั้ง `VISIONIQ_IIS_INIT=none` (ไม่ต่อ DB) หรือต่อ DB ไม่สำเร็จ → ดู `logs\iis-stdout_*.log` |
| แอปรีสตาร์ตเองตอนกลางวัน | Application Pool recycle ตามเวลา default (1740 นาที) | ตั้ง **Regular Time Interval = 0** (STEP 10) |
| คอลัมน์ `Ip` ใน `AuthLoginAudit` เป็น `127.0.0.1` ทุกแถว | IIS proxy มาจาก localhost และไม่ได้ส่ง header IP จริงต่อ | ดู [ภาคผนวก C](#c-ทำให้-audit-log-เห็น-ip-จริงของผู้ใช้) |
| ครั้งแรกที่เปิดเว็บหลังรีสตาร์ต **ช้ามาก (30–60 วิ)** | `import ultralytics` โหลด torch | ตั้ง **Start Mode = AlwaysRunning** (STEP 10) เพื่อให้บูตล่วงหน้า |

---

## 7. อัปเดตโค้ดในอนาคต (Redeploy)

```cmd
cd /d C:\VisionIQ\Digital_Vision2026

REM 1) ดึงโค้ดใหม่
git pull

REM 2) ถ้ามี dependency ใหม่
.venv\Scripts\python.exe -m pip install -r deploy\requirements-server.txt

REM 3) ตรวจความพร้อมอีกรอบ
.venv\Scripts\python.exe deploy\check_server.py
```

**แล้วสั่งให้ IIS โหลดโค้ดใหม่:**
IIS Manager → **Application Pools** → `VisionIQPool` → แถบขวา **Recycle…**

> ⚠️ **ยืนยันเสมอว่าโค้ดใหม่รันจริง** โดยดู **`CONFIG_VERSION` ที่ท้ายหน้าเว็บ**
> Flask อ่าน config ตอนเริ่มโปรเซสเท่านั้น — ถ้าไม่ Recycle จะยังรันของเก่าอยู่

> 💡 `git pull` **ไม่ลบ** โฟลเดอร์ `.venv\`, `data\`, `logs\`, และ `web.config`
> (อยู่ใน `.gitignore` หรือเป็นไฟล์ที่สร้างเอง) — ข้อมูลผู้ใช้และผลตรวจปลอดภัย

---

## 8. สำรองข้อมูลและงานบำรุงรักษา

### 8.1 สิ่งที่ต้อง backup

| อะไร | ที่ไหน | ทำไม |
|---|---|---|
| **ฐานข้อมูล VisionIQ** | SQL Server | ผู้ใช้ / สิทธิ์ / audit / ประวัติกระป๋อง |
| `data\artwork_check\` | บนดิสก์เซิร์ฟเวอร์ | **ไฟล์ artwork + ผลตรวจทั้งหมด ไม่ได้อยู่ใน DB** |
| `data\auth\secret.key` | บนดิสก์ | ถ้าหาย ผู้ใช้ทุกคนต้องล็อกอินใหม่ (ไม่ร้ายแรง แต่ควรเก็บ) |
| `config.py` + `web.config` | บนดิสก์ | ค่าตั้งเฉพาะเครื่องนี้ (ไม่มีใน git) |

### 8.2 จัดการ log ไม่ให้โตจนเต็มดิสก์

`logger.py` เขียน log แบบ **ไม่มีการหมุนไฟล์ (no rotation)** — ไฟล์จะโตขึ้นเรื่อย ๆ:
- `logs\visioniq.log`
- `logs\errors.log`
- `logs\iis-stdout_*.log` (ไฟล์ใหม่ทุกครั้งที่โปรเซสเริ่ม — สะสมเยอะที่สุด)

**แนะนำ:** ตั้ง Scheduled Task ลบไฟล์เก่ากว่า 30 วัน เดือนละครั้ง

1. เปิด **Task Scheduler** → **Create Basic Task…**
2. **Name:** `VisionIQ log cleanup` → **Trigger:** Monthly
3. **Action:** Start a program →
   - Program: `powershell.exe`
   - Arguments:
     ```
     -Command "Get-ChildItem 'C:\VisionIQ\Digital_Vision2026\logs' -File | Where-Object {$_.LastWriteTime -lt (Get-Date).AddDays(-30)} | Remove-Item -Force"
     ```
4. **Finish**

### 8.3 ตรวจสุขภาพประจำ

| ความถี่ | ทำอะไร |
|---|---|
| รายสัปดาห์ | เปิด `logs\errors.log` ดูว่ามี exception ซ้ำ ๆ ไหม |
| รายเดือน | รัน `deploy\check_server.py` (จับกรณี package หายหลังอัปเดต Windows) |
| รายเดือน | ดูพื้นที่ดิสก์ของ `data\artwork_check\` |
| หลังทุกครั้งที่ `git pull` | เช็ค `CONFIG_VERSION` ที่ footer |

---

## 9. ภาคผนวก

### A. ตัวแปรสภาพแวดล้อมทั้งหมด (ตั้งใน `<environmentVariables>` ของ `web.config`)

#### ระบบล็อกอิน

| ตัวแปร | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `AUTH_ENABLED` | `1` | `0` = ปิดระบบล็อกอินทั้งหมด (ทุกหน้าเปิดโล่ง) — ใช้เป็นทางถอยฉุกเฉิน |
| `AUTH_JWT_SECRET` | สร้างไฟล์ให้เอง | คีย์เซ็นโทเคน — **ควรตั้งเอง** |
| `AUTH_COOKIE_SECURE` | `0` | `1` = ส่งคุกกี้เฉพาะ HTTPS |
| `AUTH_COOKIE_SAMESITE` | `Lax` | นโยบาย SameSite ของคุกกี้ |
| `AUTH_ACCESS_TTL_MIN` | `60` | อายุ access token (นาที) |
| `AUTH_REFRESH_TTL_DAYS` | `7` | อายุ refresh token (วัน) — "จำฉันไว้" |
| `AUTH_MAX_FAILED` | `5` | ใส่รหัสผิดกี่ครั้งถึงล็อก |
| `AUTH_LOCK_MINUTES` | `15` | ล็อกนานกี่นาที |
| `AUTH_PASSWORD_MIN_LEN` | `8` | ความยาวรหัสผ่านขั้นต่ำ |

#### ตัวเลือกของเซิร์ฟเวอร์ (ของใหม่จากเอกสารนี้)

| ตัวแปร | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `VISIONIQ_IIS_INIT` | `db` | `db` = ต่อ DB อย่างเดียว / `none` = ไม่ init อะไร / `full` = โหลดโมเดล YOLO ด้วย |
| `VISIONIQ_WSGI_THREADS` | `8` | จำนวน thread ของ waitress (จำนวน request พร้อมกัน) |
| `VISIONIQ_WSGI_TIMEOUT` | `1200` | timeout ต่อ connection (วินาที) |
| `VISIONIQ_WSGI_PORT` | `8000` | พอร์ตตอนรันเองเพื่อทดสอบ (IIS จะ override ด้วย `HTTP_PLATFORM_PORT`) |

#### Artwork

| ตัวแปร | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `N8N_OCR_WEBHOOK_URL` | `http://172.32.201.106:5678/webhook/artwork-ocr` | ปลายทาง OCR |
| `N8N_OCR_TIMEOUT_S` | `60` | timeout ต่อ 1 โซน |
| `N8N_TRANSLATE_WEBHOOK_URL` | `.../artwork-translate` | ปลายทางแปลภาษา (แท็บอ่านประกอบ) |
| `ARTWORK_HIGHLIGHT_TESS_LANG` | `eng` | ภาษาของ Tesseract เช่น `eng+ara+tha` — **ใส่เฉพาะที่ติดตั้งจริง** |
| `ARTWORK_TESSERACT_CMD` | (หาเอง) | path ของ `tesseract.exe` ถ้าติดตั้งที่แปลก ๆ |
| `ARTWORK_HIGHLIGHT_DEFECT` | `1` | `0` = ปิดกรอบแดงทั้งฟีเจอร์ |
| `ARTWORK_CROP_MIN_SIDE` | `1200` | ด้านยาวขั้นต่ำของภาพ crop ในการ์ด (ตัวชี้เป็นชี้ตายของ Tesseract) |
| `ARTWORK_OCR_DPI` | `450` | ความละเอียดที่เรนเดอร์โซนก่อนส่ง OCR |

> **สิ่งที่ตั้งผ่าน env ไม่ได้** — ต้องแก้ใน `config.py` โดยตรง:
> `SQL_SERVER` / `SQL_DATABASE` / `SQL_USER` / `SQL_PASSWORD`

---

### B. ทางเลือกอื่นในการวางบน IIS (เผื่อกรณีข้อจำกัดขององค์กร)

| แนวทาง | ข้อดี | ข้อเสีย | เหมาะเมื่อ |
|---|---|---|---|
| **HttpPlatformHandler + waitress** ← *ที่คู่มือนี้ใช้* | IIS ดูแลวงจรชีวิตโปรเซสให้ (เปิด/รีสตาร์ตอัตโนมัติ), ตั้งค่าที่เดียวใน `web.config`, ไม่ต้องแตะโค้ดเดิม | ต้องติดตั้งโมดูลเพิ่ม 1 ตัว | **ค่าเริ่มต้นที่แนะนำ** |
| **ARR (reverse proxy) + Windows Service** | โปรเซส Python เป็นอิสระจาก IIS 100% (รีสตาร์ต IIS ไม่กระทบงานที่ค้าง) | ชิ้นส่วนเยอะกว่า ต้องลง ARR + URL Rewrite + NSSM และดูแล service เอง | เมื่อองค์กรห้ามติดตั้ง HttpPlatformHandler |
| **wfastcgi** | เป็นของ Microsoft ดั้งเดิม | ไม่ได้พัฒนาต่อมานาน, จัดการ request ยาว ๆ ได้แย่กว่า, ตั้งค่ายุ่ง | **ไม่แนะนำสำหรับงานนี้** เพราะการตรวจ artwork เป็น request ที่ยาวหลายนาที |

---

### C. ทำให้ audit log เห็น IP จริงของผู้ใช้

โค้ดอ่าน IP จาก header `X-Forwarded-For` ก่อน แล้วค่อยตกไปใช้ `remote_addr`
(`auth/routes.py:37-41`) เมื่อ IIS เป็น proxy อยู่หน้า ถ้า header ไม่ถูกส่งต่อ
คอลัมน์ `Ip` จะเป็น `127.0.0.1`

**วิธีแก้** (ต้องมีโมดูล **URL Rewrite**):

1. IIS Manager → คลิกที่ **ชื่อเครื่องเซิร์ฟเวอร์** (node บนสุด)
2. ดับเบิลคลิก **URL Rewrite** → แถบขวา **View Server Variables…**
3. คลิก **Add…** → พิมพ์ `HTTP_X_FORWARDED_FOR` → **OK**
   *(ขั้นนี้จำเป็น — ถ้าไม่ประกาศไว้ กฎด้านล่างจะถูก IIS ปฏิเสธ)*
4. เพิ่มกฎนี้ใน `web.config` ภายใน `<system.webServer>`:

```xml
<rewrite>
  <rules>
    <rule name="Preserve client IP" stopProcessing="false">
      <match url=".*" />
      <serverVariables>
        <set name="HTTP_X_FORWARDED_FOR" value="{REMOTE_ADDR}" />
      </serverVariables>
      <action type="None" />
    </rule>
  </rules>
</rewrite>
```

5. Recycle application pool แล้วลองล็อกอินใหม่ → ตรวจด้วย SQL:
```sql
SELECT TOP 20 Username, Success, Ip, Reason, AttemptedAt
FROM AuthLoginAudit ORDER BY AttemptedAt DESC;
```

---

### D. ทำไมเซิร์ฟเวอร์ต้องลง torch ทั้งที่ไม่ตรวจกระป๋อง

`app.py` บรรทัด 19–20 import `camera` และ `yolo_detector` ไว้ที่ **ระดับโมดูล**
และ `yolo_detector.py` บรรทัด 12 `from ultralytics import YOLO` ซึ่งดึง torch ตามมา
Python จะรันบรรทัดพวกนี้ทันทีที่ import `app` ไม่ว่าจะใช้โหมดกล้องหรือไม่ —
ถ้า package หายไป **แอปทั้งตัว import ไม่ผ่าน = แม้แต่หน้า login ก็ไม่ขึ้น**

ทางเลือกที่จะตัด torch ออกได้จริงคือแยก entry point ที่ import เฉพาะ blueprint
ของ `auth` + `artwork_check` โดยไม่ผ่าน `app.py` — แต่นั่นคือ **การรื้อโครงสร้าง**
ซึ่งขัดกับกฎเหล็กข้อ 1 ของโปรเจกต์ (ห้ามกระทบโหมดอื่น) และเพิ่มความเสี่ยงที่
เซิร์ฟเวอร์กับสถานีจะมีพฤติกรรมต่างกัน จึงเลือกทางที่ปลอดภัยกว่า:
**ยอมลง torch (พื้นที่ ~2–3 GB, ไม่ได้ถูกเรียกใช้ตอนรัน)**

> ถ้าพื้นที่ดิสก์เป็นข้อจำกัดจริง ๆ ค่อยพิจารณาแยก entry point เป็นงานต่างหาก
> โดยต้องมีเทสต์ยืนยันว่าโหมดบนสถานีไม่เปลี่ยนพฤติกรรม

---

### E. คำสั่งที่ใช้บ่อย (สรุปไว้ที่เดียว)

```cmd
REM ── ตรวจความพร้อมทั้งระบบ ──────────────────────────────────
cd /d C:\VisionIQ\Digital_Vision2026
.venv\Scripts\python.exe deploy\check_server.py

REM ── รันทดสอบตรง ๆ ไม่ผ่าน IIS (แก้ปัญหาให้แคบลง) ──────────
.venv\Scripts\python.exe deploy\wsgi_iis.py

REM ── สร้าง/รีเซ็ตรหัสผ่าน admin ─────────────────────────────
.venv\Scripts\python.exe -m auth.seed_admin --username admin --password "Str0ng!Pass1"

REM ── สร้างค่า AUTH_JWT_SECRET ───────────────────────────────
.venv\Scripts\python.exe -c "import secrets;print(secrets.token_urlsafe(48))"

REM ── วินิจฉัยกรอบแดงของ Artwork ─────────────────────────────
.venv\Scripts\python.exe diagnose_highlight.py <inspection-id> --save

REM ── รีสตาร์ตแอป (โหลดโค้ด/ค่าตั้งใหม่) ────────────────────
%windir%\system32\inetsrv\appcmd.exe recycle apppool /apppool.name:"VisionIQPool"

REM ── รีสตาร์ต IIS ทั้งตัว (ใช้เมื่อจำเป็นเท่านั้น) ─────────
iisreset
```

---

### F. เตรียมพร้อมสำหรับ "ใช้กล้องบนเซิร์ฟเวอร์" ในอนาคต

เซิร์ฟเวอร์ตัวนี้ตั้งใจเลือก **Python 3.9** ไว้เพื่อเปิดทางให้ต่อกล้องได้ในภายหลัง
หัวข้อนี้คือสิ่งที่ต้องทำ **ตอนที่วันนั้นมาถึง** (ตอนนี้ยังไม่ต้องทำอะไร)

#### F.1 สิ่งที่ต้องเพิ่ม (ตามลำดับ)

**1) คัดลอกไฟล์โมเดลมาเอง — `git clone` ไม่ได้เอามาให้**

`.gitignore` กัน `*.pt` และโฟลเดอร์ `weights/` ไว้ ผลคือหลัง clone จะได้เฉพาะ
`weights/can_dent/best.pt` (ที่ถูก track ไว้ก่อนกฎนี้) — แต่ **`bestX.pt` ซึ่งเป็น
โมเดล production ตัวจริงที่สถานีใช้ (YOLOv8m-segmentation, 27M params) ไม่มีอยู่ในกิต**
ต้องคัดลอกจากเครื่องสถานีมาวางเองที่:
```
C:\VisionIQ\Digital_Vision2026\weights\can_dent\bestX.pt
```

**2) ติดตั้ง package ฝั่งกล้อง + ตัวเร่ง (นี่คือเหตุผลที่ต้องเป็น Python 3.9)**
```cmd
cd /d C:\VisionIQ\Digital_Vision2026

REM แจกแจงกล้อง USB
.venv\Scripts\python.exe -m pip install pygrabber==0.2 comtypes==1.4.16

REM ตัวเร่ง ONNX (ล้อ cp39 ตัวสุดท้าย — อัป Python แล้วลงไม่ได้)
.venv\Scripts\python.exe -m pip install onnxruntime==1.19.2 onnxslim onnx

REM ตัวเร่ง iGPU — ลงเฉพาะเมื่อเซิร์ฟเวอร์มี Intel iGPU จริง
.venv\Scripts\python.exe -m pip install "openvino==2024.6.0"
```

**3) ⚠️ ต้องผ่านสคริปต์ verify ก่อนเปิดใช้ตัวเร่ง — ห้ามข้าม**

กฎเหล็กของโปรเจกต์: *"ห้ามเชื่อว่าใช้ได้แค่เพราะโค้ดรันไม่ error"* —
เคยมีเคสจริงที่ OpenVINO โหลดสำเร็จแต่ **ตรวจไม่เจอทุกโหมดแบบเงียบ ๆ**
```cmd
.venv\Scripts\python.exe verify_onnx.py --weights weights\can_dent\bestX.pt --images <โฟลเดอร์ภาพ>
.venv\Scripts\python.exe verify_openvino.py --weights weights\can_dent\bestX.pt --images <โฟลเดอร์ภาพ>
```
ต้องขึ้น **PASS** ทุก device × imgsz ก่อนจึงเปิดใช้งานได้

**4) ตรวจ `config.py` ให้ตรงกับฮาร์ดแวร์ของเซิร์ฟเวอร์**

| ค่า | สถานี | เซิร์ฟเวอร์ |
|---|---|---|
| `USE_ONNX` (บรรทัด 201) | `True` | `True` ได้ (หลัง verify ผ่าน) |
| `OPENVINO_DEVICE` (บรรทัด 186) | `"intel:gpu"` | **ตั้งเป็น `None` ถ้าเซิร์ฟเวอร์ไม่มี Intel iGPU** |

**5) เปลี่ยนโหมด init ใน `web.config`**
```xml
<environmentVariable name="VISIONIQ_IIS_INIT" value="full" />
```
`full` = เรียก `init_system()` เต็มรูปแบบ (โหลดโมเดล + ต่อ DB)

**6) ให้สิทธิ์ `run_live_detection`** แก่ role ที่ต้องใช้ (ที่ `/admin/users`)

#### F.2 ⚠️ ความเสี่ยงที่ต้องทดสอบก่อนใช้จริง (อย่าเพิ่งเชื่อว่าจะได้)

ทั้งสามข้อนี้เป็น **ข้อจำกัดของการรันโหมดกล้องผ่าน IIS** ที่คู่มือนี้ยังไม่ได้พิสูจน์
เพราะเซิร์ฟเวอร์ปัจจุบันไม่มีกล้อง — ต้องทดสอบจริงเมื่อถึงเวลา:

| ความเสี่ยง | เพราะอะไร | ทางถอย |
|---|---|---|
| **ภาพสด (MJPEG) อาจไม่ไหลผ่าน IIS** | `/video_feed` เป็น response แบบสตรีมค้างยาว ซึ่ง reverse proxy อาจ buffer ไว้จนภาพไม่ขยับ หรือชนกับ `requestTimeout` | รันโหมดกล้องแยกด้วย `py -3.9 app.py` บนพอร์ตของตัวเอง แล้วให้ IIS ดูแลเฉพาะ Login + Artwork |
| **Application Pool อาจเข้าถึงกล้อง USB ไม่ได้** | โปรเซสของ IIS ทำงานแบบ non-interactive (session 0) การเข้าถึงกล้องผ่าน DirectShow/MSMF และการตั้งค่าความเป็นส่วนตัวของ Windows อาจบล็อก | ตั้ง Identity ของ App Pool เป็นบัญชีเฉพาะที่มีสิทธิ์เข้าถึงอุปกรณ์ หรือใช้ทางถอยข้างบน |
| **รองรับกล้องได้ครั้งละ 1 session** | `app.py` เก็บ `camera` / `detector` เป็นตัวแปร global ระดับโปรเซส — ไม่ได้แยกตามผู้ใช้ | ถ้าต้องหลายคนพร้อมกัน ให้ใช้โหมด **STREAM** (เบราว์เซอร์เปิดกล้องตัวเอง แยกกันต่อ client) ซึ่งต้อง HTTPS |

> 💡 **ข้อเสนอที่ปลอดภัยที่สุด** เมื่อถึงเวลาต้องใช้กล้อง: ให้ IIS ทำหน้าที่ **Login +
> Artwork** ต่อไปตามคู่มือนี้ แล้วรัน **โหมดกล้องเป็นโปรเซสแยก** (`py -3.9 app.py`)
> บนเครื่องที่ต่อกล้องจริง — แยกความเสี่ยงออกจากกัน และไม่ต้องเดิมพันว่า MJPEG
> จะผ่าน proxy ได้หรือไม่

---

### G. มาตรการชดเชยเมื่อรันบน Python ที่หมดอายุแล้ว

เนื่องจากเลือก Python 3.9.13 (ไม่มี security patch ตั้งแต่ ต.ค. 2025) ควรทำสิ่งเหล่านี้
เพื่อลดความเสี่ยงให้อยู่ในระดับที่ยอมรับได้:

| มาตรการ | ทำไมช่วยได้ | ระดับความสำคัญ |
|---|---|---|
| **ไม่เปิดเว็บนี้ออกอินเทอร์เน็ต** ใช้เฉพาะเครือข่ายภายในโรงงาน | ตัดผู้โจมตีจากภายนอกออกทั้งหมด — เป็นมาตรการที่ได้ผลที่สุด | 🔴 ต้องทำ |
| **Firewall เปิดเฉพาะ Domain / Private profile** (STEP 17) | ไม่เปิดพอร์ตในโปรไฟล์ Public | 🔴 ต้องทำ |
| **ให้ IIS เป็นด่านหน้า (ตามคู่มือนี้)** — waitress ผูกกับ `127.0.0.1` เท่านั้น | IIS เป็นตัวรับ TLS และแยกวิเคราะห์ HTTP request แทน ทำให้โค้ดเครือข่ายเก่าของ Python ไม่ได้สัมผัสทราฟฟิกดิบจากภายนอกโดยตรง | 🔴 ต้องทำ (ทำอยู่แล้ว) |
| **เปิด HTTPS** (STEP 16) | รหัสผ่านและคุกกี้ไม่วิ่งแบบเปิดเผย | 🔴 ต้องทำ |
| **อัปเดต Windows Server สม่ำเสมอ** | ช่องโหว่ระดับ OS/TLS ถูกปิดจากฝั่ง IIS ซึ่งเป็นด่านที่รับทราฟฟิกจริง | 🟠 ควรทำ |
| **อัปเดต package ของ Python ที่ยังออกล้อ cp39 ให้** (`bcrypt`, `PyJWT`, `requests`, `PyMuPDF`) | ช่องโหว่ส่วนใหญ่ที่กระทบแอปจริงมาจาก library เหล่านี้ ไม่ใช่ตัว interpreter | 🟠 ควรทำ ปีละครั้ง |
| **จำกัดสิทธิ์ NTFS ของ App Pool** (ให้ Modify เฉพาะ `data\` + `logs\`) | ถ้าถูกเจาะ ผู้โจมตีแก้ไขโค้ดไม่ได้ | 🟠 ควรทำ |
| **ทบทวนทุกปีว่ายังต้องใช้กล้องบนเซิร์ฟเวอร์นี้ไหม** | ถ้าคำตอบคือไม่ ให้ย้ายไป Python 3.12 ได้ทันที (โค้ดพร้อมแล้ว — ตรวจสอบความเข้ากันได้ไว้ให้แล้ว) | 🟡 แนะนำ |

> **บันทึกไว้เพื่อการตัดสินใจในอนาคต:** ซอร์สทุกไฟล์ของโปรเจกต์ compile ผ่านบน
> Python 3.11/3.12 และไม่มีการใช้ API ที่ถูกถอด (`distutils`, `imp`,
> `datetime.utcnow()`, `locale.getdefaultlocale()`) — **การย้ายเวอร์ชันในอนาคต
> ติดแค่ตัวเร่ง onnx/openvino เท่านั้น ไม่ได้ติดที่ตัวโค้ด**

---

## สรุปสั้นที่สุด

1. ติดตั้ง **Python 3.9.13** (เลือกไว้เพื่อรองรับกล้องในอนาคต — **ห้าม** 3.13) + **HttpPlatformHandler** บนเซิร์ฟเวอร์
2. `git clone` โค้ด → สร้าง **venv ในโฟลเดอร์โปรเจกต์** → `pip install -r deploy\requirements-server.txt`
3. รัน **`auth_schema.sql`** (อย่าลืม `-f 65001`) → **`python -m auth.seed_admin`**
4. ทดสอบด้วย **`deploy\wsgi_iis.py`** ให้ผ่านก่อน แล้วค่อยต่อ IIS
5. สร้าง **App Pool (No Managed Code)** → **Website** → วาง **`web.config`** → ให้สิทธิ์ **Modify**
6. ขยาย **เพดานอัปโหลด** + **timeout** → ผูก **HTTPS** → ตั้ง `AUTH_COOKIE_SECURE=1`
7. สร้าง role **`ArtworkUser`** (`inspect_artwork` + `view_history`) แล้วเพิ่มผู้ใช้

**ตรวจทุกอย่างได้ด้วยคำสั่งเดียว:** `.venv\Scripts\python.exe deploy\check_server.py`
