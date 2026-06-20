# โหมด Login + RBAC (Authentication & Authorization)

ระบบเข้าสู่ระบบ + สิทธิ์การใช้งานสำหรับแอปตรวจสอบบรรจุภัณฑ์ VisionIQ
ออกแบบให้ **แยกอิสระ** เหมือนโหมด Artwork — ลงทะเบียนใน `app.py` ใน `try/except`
ถ้าพังก็ปิดเฉพาะตัวมัน ไม่ล้มโหมดตรวจอื่น และ **ปิด/เปิดได้ด้วย `AUTH_ENABLED`**

---

## สถาปัตยกรรม

```
auth/
├── __init__.py     install_auth(app) — entry point เดียว
├── config.py       ค่าตั้ง (TTL, cookie, lockout, password policy, permission/role)
├── passwords.py    bcrypt hash/verify + ตรวจกฎรหัสผ่าน (ใช้ทั้ง FE/BE)
├── tokens.py       JWT access (1 ชม.) + refresh (7 วัน)
├── store.py        pyodbc parameterized query (กัน SQL injection)
├── access.py       before_request guard + path→permission + template helper
├── routes.py       blueprint: /login, /api/auth/*
└── seed_admin.py   CLI สร้าง/รีเซ็ตแอดมิน (ไม่ต้องเขียน hash เอง)

Connection_sql/auth_schema.sql   ตาราง + seed roles/permissions
templates/login.html             หน้า login (standalone)
static/js/login.js               show/hide, strength meter, submit
static/css/auth.css              ธีม navy + user badge ใน navbar
```

### โครงสร้างฐานข้อมูล (RBAC)

| ตาราง | หน้าที่ |
|---|---|
| `AuthRoles` | Admin / Manager / Staff / Viewer |
| `AuthPermissions` | สิทธิ์ย่อย (map กับฟังก์ชันจริงของแอป) |
| `AuthRolePermissions` | many-to-many role ↔ permission |
| `AuthUsers` | user, **bcrypt password hash**, role, lockout, last login |
| `AuthLoginAudit` | log การ login (เวลา, IP, user-agent, สำเร็จ/ไม่สำเร็จ, เหตุผล) |

### Permission → ฟังก์ชันในแอป

| permission key | คุม |
|---|---|
| `run_live_detection` | หน้าตรวจจับสด `/`, snapshot, กล้อง, สลับโมเดล |
| `inspect_label_paper` | `/label_paper` + API |
| `inspect_artwork` | `/artwork_check` + API |
| `view_dashboard` | `/dashboard`, `/api/stats` |
| `view_history` | `/history`, `/api/defects`, ประวัติของแต่ละโหมด |
| `manage_users` | `/api/auth/users` |

ดีฟอลต์สิทธิ์ต่อ role: **Admin** = ทุกสิทธิ์, **Manager** = ทุกอย่างยกเว้น
จัดการผู้ใช้, **Staff** = ตรวจทุกโหมด + แดชบอร์ด, **Viewer** = แดชบอร์ด + ประวัติ

---

## ติดตั้ง (3 ขั้นตอน)

### 1) ติดตั้ง dependency

```bash
pip install -r requirements.txt      # เพิ่ม bcrypt + PyJWT แล้ว
```

### 2) สร้างตาราง + seed (SQL Server / VisionIQ)

ใช้ค่าการเชื่อมต่อเดียวกับที่ตั้งใน `config.py` (`SQL_SERVER/SQL_DATABASE/...`)

```bash
sqlcmd -S 172.32.0.50 -d VisionIQ -U sa -P "<password>" \
       -i Connection_sql/auth_schema.sql
```

> สคริปต์เป็น **idempotent** — รันซ้ำได้ ไม่สร้างข้อมูลซ้ำ
> (จะสร้างตารางที่ยังไม่มี + seed roles/permissions ให้ครบ)

### 3) สร้างผู้ใช้แอดมินคนแรก

```bash
python -m auth.seed_admin --username admin --password 'Str0ng!Pass'
# หรือเว้น --password ไว้ ระบบจะถามแบบซ่อนการพิมพ์
```

รหัสผ่านต้องผ่านกฎ: ยาว ≥ 8, มีพิมพ์ใหญ่/พิมพ์เล็ก/ตัวเลข/อักขระพิเศษ (`!@#$%^&*`)

จากนั้นเปิด `python app.py` → ไปที่ `http://localhost:5000/login`

---

## การตั้งค่า (ผ่าน environment variable)

| ตัวแปร | default | หน้าที่ |
|---|---|---|
| `AUTH_ENABLED` | `1` (เปิด) | ตั้ง `0` เพื่อปิดทั้งระบบ (ทุกหน้าเปิดเหมือนเดิม) — ใช้ rollback ฉุกเฉิน |
| `AUTH_JWT_SECRET` | (auto) | คีย์เซ็น JWT ถ้าไม่ตั้งจะสุ่มเก็บไว้ที่ `data/auth/secret.key` |
| `AUTH_ACCESS_TTL_MIN` | `60` | อายุ access token (นาที) |
| `AUTH_REFRESH_TTL_DAYS` | `7` | อายุ refresh token (วัน) |
| `AUTH_COOKIE_SECURE` | `0` | ตั้ง `1` เมื่อรันผ่าน **HTTPS** |
| `AUTH_MAX_FAILED` | `5` | ล็อกบัญชีหลังกรอกผิดกี่ครั้ง |
| `AUTH_LOCK_MINUTES` | `15` | ระยะเวลาล็อกชั่วคราว (นาที) |
| `AUTH_PASSWORD_MIN_LEN` | `8` | ความยาวรหัสผ่านขั้นต่ำ |

---

## API

| method · path | สิทธิ์ | หน้าที่ |
|---|---|---|
| `GET /login` | public | หน้า login |
| `POST /api/auth/login` | public | เข้าสู่ระบบ → ตั้ง httpOnly cookie |
| `POST /api/auth/logout` | public | ล้าง cookie |
| `POST /api/auth/refresh` | public (ใช้ refresh cookie) | ขอ access token ใหม่ |
| `GET /api/auth/me` | login | ข้อมูล user + permissions ปัจจุบัน |
| `GET /api/auth/policy` | public | กฎรหัสผ่าน (ให้ FE validate) |
| `GET /api/auth/users` | `manage_users` | รายชื่อผู้ใช้ |
| `POST /api/auth/users` | `manage_users` | สร้างผู้ใช้ใหม่ |

---

## หลักความปลอดภัยที่ยึด

- **ไม่เก็บ plain text** — bcrypt (salt อัตโนมัติ) เทียบด้วย `checkpw` constant-time
- **Backend ตรวจสิทธิ์ซ้ำเสมอ** — `access.py` เป็นเส้นแบ่งความปลอดภัยจริง
  การซ่อนเมนูฝั่ง frontend เป็นแค่ UX
- **กัน SQL injection** — pyodbc parameterized (`?`) ทุกจุด ไม่มี string concat
- **httpOnly cookie** — JS อ่าน token ไม่ได้ (กัน XSS ขโมย token) แนะนำกว่า localStorage
- **บัญชีถูกล็อกชั่วคราว** เมื่อกรอกผิดเกินกำหนด + บันทึก audit ทุกครั้ง
- **ข้อความ error เป็นกลาง** ("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง") กัน user enumeration
- **transparent refresh** — access หมดอายุแต่ refresh ยังดี ระบบออก access ใหม่ให้เอง
  ผู้ใช้ไม่ถูกเด้งออกกลางทาง
