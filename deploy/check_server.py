"""
check_server.py — ตรวจความพร้อมของ Windows Server ก่อน/หลังต่อ IIS

รันจากรากโปรเจกต์ด้วย interpreter ตัวเดียวกับที่ IIS จะใช้ (สำคัญมาก —
ปัญหาที่เจอบ่อยที่สุดคือ "ลง package ไว้คนละ Python"):

    .venv\\Scripts\\python.exe deploy\\check_server.py

สคริปต์นี้ "อ่านอย่างเดียว" ไม่แก้ไขอะไรทั้งสิ้น (ยกเว้นทดสอบเขียนไฟล์ชั่วคราว
ในโฟลเดอร์ data/ และ logs/ แล้วลบทิ้ง). ผลลัพธ์แบ่งเป็น
    [OK]   = ผ่าน
    [WARN] = ใช้งานได้ แต่ฟีเจอร์บางอย่างจะหายไปแบบเงียบ ๆ
    [FAIL] = ต้องแก้ก่อน ไม่งั้นล็อกอิน/ตรวจ Artwork ไม่ได้

exit code: 0 = ไม่มี FAIL, 1 = มี FAIL
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_FAILS = 0
_WARNS = 0


def _say(text: str) -> None:
    """พิมพ์แบบทนคอนโซลที่ไม่ใช่ UTF-8 (cp874/cp437) — ไม่ให้สคริปต์ตายเพราะ
    พิมพ์ภาษาไทยไม่ได้."""
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(text.encode(enc, "replace").decode(enc, "replace"), flush=True)


def ok(msg: str) -> None:
    _say("  [OK]   " + msg)


def warn(msg: str) -> None:
    global _WARNS
    _WARNS += 1
    _say("  [WARN] " + msg)


def fail(msg: str) -> None:
    global _FAILS
    _FAILS += 1
    _say("  [FAIL] " + msg)


def head(title: str) -> None:
    _say("")
    _say("-" * 68)
    _say(title)
    _say("-" * 68)


# ── 1. interpreter ────────────────────────────────────────────────────

def check_python() -> None:
    head("1) Python interpreter")
    ok(f"executable : {sys.executable}")
    ok(f"version    : {sys.version.split()[0]}")
    ok(f"project    : {_ROOT}")

    # เวอร์ชันที่รองรับบน "เซิร์ฟเวอร์" (คนละชุดกับสถานีที่ล็อกไว้ที่ 3.9 เพราะ
    # onnxruntime/openvino — ซึ่ง requirements-server.txt ตัดออกหมดแล้ว)
    ver = sys.version_info[:2]
    if ver in ((3, 11), (3, 12)):
        ok(f"เวอร์ชัน {ver[0]}.{ver[1]} อยู่ในช่วงที่รองรับและยังได้ security update")
    elif ver >= (3, 13):
        fail(f"Python {ver[0]}.{ver[1]} ใหม่เกินไปสำหรับชุด package ที่ pin ไว้ "
             "(numpy 1.26 / Pillow 10.1 ไม่มีล้อ cp313) — ใช้ 3.12 แทน")
    elif ver == (3, 10):
        warn("Python 3.10 ใช้ได้ แต่ใกล้หมดอายุแล้ว — แนะนำ 3.12")
    else:
        warn(f"Python {ver[0]}.{ver[1]} หมดอายุการสนับสนุนแล้ว (ไม่มี security "
             "patch) — เซิร์ฟเวอร์ที่เปิดให้ล็อกอินผ่านเครือข่ายควรใช้ 3.12")

    in_venv = hasattr(sys, "real_prefix") or sys.prefix != getattr(
        sys, "base_prefix", sys.prefix)
    if in_venv:
        ok("รันอยู่ใน virtual environment (ตรงกับที่ web.config ควรชี้)")
    else:
        warn("ไม่ได้รันใน venv — IIS AppPool อาจมองไม่เห็น package ที่ลงไว้ "
             "(ดูหัวข้อ venv ในเอกสาร deploy)")


# ── 2. package ────────────────────────────────────────────────────────

_REQUIRED = [
    ("flask", "Flask", "เว็บเฟรมเวิร์กหลัก"),
    ("cv2", "opencv-python", "app.py import ที่หัวไฟล์"),
    ("numpy", "numpy", "app.py import ที่หัวไฟล์"),
    ("ultralytics", "ultralytics", "app.py import ที่หัวไฟล์ (ดึง torch มาด้วย)"),
    ("pyodbc", "pyodbc", "ต่อ SQL Server — ขาดแล้วล็อกอินไม่ได้"),
    ("bcrypt", "bcrypt", "แฮชรหัสผ่าน — ขาดแล้วล็อกอินไม่ได้"),
    ("jwt", "PyJWT", "โทเคนใน cookie — ขาดแล้วล็อกอินไม่ได้"),
    ("fitz", "PyMuPDF", "เปิด/เรนเดอร์ PDF ของ Artwork"),
    ("requests", "requests", "เรียก N8N webhook (OCR)"),
    ("waitress", "waitress", "WSGI server ที่ IIS เรียกใช้"),
]

_OPTIONAL = [
    ("spellchecker", "pyspellchecker",
     "ชั้นตรวจ dictionary ของ Artwork — ขาดแล้ว 'คำผิดจะขึ้นถูก' (จุดบอด QC)"),
    ("pytesseract", "pytesseract",
     "กรอบแดงชี้คำผิดบนไฟล์ outline/ภาพถ่าย (ผลตรวจไม่เปลี่ยน)"),
    ("skimage", "scikit-image", "ใช้ในโหมดฉลากกระดาษ"),
    ("Levenshtein", "python-Levenshtein", "เทียบข้อความให้เร็วขึ้น (มี fallback)"),
]


def check_packages() -> None:
    head("2) Python package")
    # จับ BaseException ไม่ใช่แค่ Exception: package ที่ติดตั้งไม่สมบูรณ์บางตัว
    # (เช่น cryptography ที่ native binding พัง) โยน exception ที่ไม่ได้สืบทอดจาก
    # Exception ออกมาตอน import — ถ้าไม่จับ สคริปต์ตรวจสุขภาพจะตายกลางคันเสียเอง
    for module, pipname, why in _REQUIRED:
        try:
            __import__(module)
            ok(f"{pipname}")
        except BaseException as e:                   # noqa: BLE001
            fail(f"{pipname} ไม่พร้อม ({type(e).__name__}: {e}) — {why}")

    for module, pipname, why in _OPTIONAL:
        try:
            __import__(module)
            ok(f"{pipname} (optional)")
        except BaseException as e:                   # noqa: BLE001
            warn(f"{pipname} ใช้ไม่ได้ ({type(e).__name__}) — {why}")


# ── 3. tesseract binary ───────────────────────────────────────────────

def check_tesseract() -> None:
    head("3) Tesseract (กรอบแดงชี้คำผิด — display-only)")
    try:
        from artwork_check import highlight
    except Exception as e:                           # noqa: BLE001
        fail(f"import artwork_check.highlight ไม่ได้: {e}")
        return

    finder = getattr(highlight, "_find_tesseract_cmd", None)
    cmd = None
    if callable(finder):
        try:
            cmd = finder()
        except Exception as e:                       # noqa: BLE001
            warn(f"หา tesseract.exe ไม่สำเร็จ: {e}")
    if not cmd:
        warn("ไม่พบ tesseract.exe — Artwork ยังตรวจได้ปกติ แต่ไฟล์ที่ไม่มี "
             "text layer จะไม่มีกรอบแดง")
        return
    ok(f"พบ tesseract: {cmd}")

    try:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = cmd
        ok(f"เวอร์ชัน: {pytesseract.get_tesseract_version()}")
        langs = list(pytesseract.get_languages(config=""))
        ok(f"ภาษาที่ติดตั้ง: {', '.join(sorted(langs)) or '(ไม่มี)'}")
        want = [s for s in os.getenv("ARTWORK_HIGHLIGHT_TESS_LANG",
                                     "eng").split("+") if s.strip()]
        missing = [w for w in want if w not in langs]
        if missing:
            warn(f"ARTWORK_HIGHLIGHT_TESS_LANG อ้างภาษาที่ยังไม่ได้ติดตั้ง: "
                 f"{', '.join(missing)} (ระบบจะกรองออกให้ แต่กรอบของภาษานั้นจะไม่มี)")
        else:
            ok("ภาษาที่ตั้งไว้ใน ARTWORK_HIGHLIGHT_TESS_LANG ติดตั้งครบ")
    except Exception as e:                           # noqa: BLE001
        warn(f"เรียก pytesseract ไม่สำเร็จ: {e}")


# ── 4. SQL Server + ตาราง auth ────────────────────────────────────────

def check_database() -> None:
    head("4) SQL Server + ตารางระบบล็อกอิน")
    try:
        import config as app_config
        import pyodbc
    except Exception as e:                           # noqa: BLE001
        fail(f"import config/pyodbc ไม่ได้: {e}")
        return

    ok(f"เป้าหมาย: {app_config.SQL_SERVER}/{app_config.SQL_DATABASE} "
       f"(user={app_config.SQL_USER})")

    drivers = [d for d in pyodbc.drivers()]
    if "SQL Server" in drivers:
        ok("พบ ODBC driver ชื่อ 'SQL Server' (โค้ดใช้ตัวนี้)")
    else:
        fail("ไม่พบ ODBC driver ชื่อ 'SQL Server' — โค้ดใน auth/store.py และ "
             f"database.py ระบุไดรเวอร์นี้ตายตัว. ที่มีอยู่: {drivers}")

    cs = (f"DRIVER={{SQL Server}};SERVER={app_config.SQL_SERVER};"
          f"DATABASE={app_config.SQL_DATABASE};UID={app_config.SQL_USER};"
          f"PWD={app_config.SQL_PASSWORD};")
    try:
        conn = pyodbc.connect(cs, timeout=5)
    except Exception as e:                           # noqa: BLE001
        fail(f"ต่อฐานข้อมูลไม่ได้: {e}")
        return
    ok("ต่อฐานข้อมูลสำเร็จ")

    try:
        cur = conn.cursor()
        for table in ("AuthRoles", "AuthPermissions", "AuthRolePermissions",
                      "AuthUsers", "AuthLoginAudit", "AuthAdminAudit"):
            cur.execute("SELECT COUNT(*) FROM sys.tables WHERE name = ?", table)
            if cur.fetchone()[0]:
                ok(f"มีตาราง {table}")
            else:
                fail(f"ไม่มีตาราง {table} — ยังไม่ได้รัน "
                     "Connection_sql\\auth_schema.sql")

        cur.execute("SELECT COUNT(*) FROM AuthUsers WHERE IsActive = 1")
        n_users = int(cur.fetchone()[0])
        if n_users:
            ok(f"มีผู้ใช้ที่เปิดใช้งานอยู่ {n_users} บัญชี")
        else:
            fail("ยังไม่มีผู้ใช้เลย — รัน `python -m auth.seed_admin "
                 "--username admin --password '<StrongPass1!>'`")

        cur.execute(
            "SELECT COUNT(DISTINCT u.UserId) FROM AuthUsers u "
            "JOIN AuthRolePermissions rp ON rp.RoleId = u.RoleId "
            "JOIN AuthPermissions p ON p.PermissionId = rp.PermissionId "
            "WHERE u.IsActive = 1 AND p.PermissionKey = 'inspect_artwork'")
        ok(f"บัญชีที่มีสิทธิ์ตรวจ Artwork: {int(cur.fetchone()[0])} บัญชี")
    except Exception as e:                           # noqa: BLE001
        fail(f"ตรวจตารางไม่สำเร็จ: {e}")
    finally:
        try:
            conn.close()
        except Exception:                            # noqa: BLE001
            pass


# ── 5. สิทธิ์เขียนโฟลเดอร์ ─────────────────────────────────────────────

def check_writable() -> None:
    head("5) สิทธิ์เขียนไฟล์ (IIS AppPool identity ต้องเขียนได้)")
    targets = [
        os.path.join(_ROOT, "logs"),
        os.path.join(_ROOT, "data", "auth"),
        os.path.join(_ROOT, "data", "artwork_check", "inspections"),
        os.path.join(_ROOT, "data", "artwork_check", "zone_templates"),
        os.path.join(_ROOT, "data", "artwork_check", "vocab"),
    ]
    for path in targets:
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".write_probe.tmp")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
            ok(f"เขียนได้: {path}")
        except Exception as e:                       # noqa: BLE001
            fail(f"เขียนไม่ได้: {path} ({e}) — ให้สิทธิ์ Modify แก่ "
                 "'IIS AppPool\\<ชื่อ AppPool>'")


# ── 6. การตั้งค่า auth ─────────────────────────────────────────────────

def check_auth_config() -> None:
    head("6) การตั้งค่าระบบล็อกอิน")
    try:
        from auth import config as ac
    except Exception as e:                           # noqa: BLE001
        fail(f"โหลด auth.config ไม่ได้: {e}")
        return

    if ac.AUTH_ENABLED:
        ok("AUTH_ENABLED = True (บังคับล็อกอิน)")
    else:
        warn("AUTH_ENABLED = False — ทุกหน้าเปิดโล่ง ไม่ต้องล็อกอิน "
             "(ตั้ง env AUTH_ENABLED=1 บนเซิร์ฟเวอร์จริง)")

    if os.getenv("AUTH_JWT_SECRET", "").strip():
        ok("AUTH_JWT_SECRET มาจาก environment variable (แนะนำ)")
    else:
        keyfile = os.path.join(_ROOT, "data", "auth", "secret.key")
        if os.path.isfile(keyfile):
            warn(f"ใช้ secret จากไฟล์ {keyfile} — ใช้ได้ แต่ควรตั้ง "
                 "AUTH_JWT_SECRET ใน web.config เพื่อไม่ให้ผูกกับไฟล์")
        else:
            warn("ยังไม่มีทั้ง AUTH_JWT_SECRET และไฟล์ secret.key — ระบบจะสร้าง "
                 "ให้ตอนบูต ถ้าเขียนไฟล์ไม่ได้ ผู้ใช้จะหลุดล็อกอินทุกครั้งที่รีสตาร์ต")

    if ac.COOKIE_SECURE:
        ok("AUTH_COOKIE_SECURE = True (ต้องเปิดเว็บผ่าน https:// เท่านั้น)")
    else:
        warn("AUTH_COOKIE_SECURE = False — ใช้ได้กับ http:// "
             "แต่ควรตั้งเป็น 1 หลังผูก HTTPS แล้ว")


# ── 7. OCR webhook ────────────────────────────────────────────────────

def check_ocr_webhook() -> None:
    head("7) OCR backend (N8N webhook)")
    try:
        import config as app_config
        from inspectors import vertex_client
    except Exception as e:                           # noqa: BLE001
        fail(f"โหลดตัวเรียก OCR ไม่ได้: {e}")
        return

    url = app_config.N8N_OCR_WEBHOOK_URL
    if not url:
        warn("ไม่ได้ตั้ง N8N_OCR_WEBHOOK_URL — ไฟล์ที่ไม่มี text layer "
             "จะตรวจไม่ได้ (ไฟล์ PDF ที่มี text layer ยังตรวจได้)")
        return
    ok(f"URL: {url}")
    ok(f"เปิดใช้งาน: {vertex_client.is_enabled()}")

    try:
        import requests
        r = requests.get(url, timeout=8)
        ok(f"ต่อถึงเซิร์ฟเวอร์ N8N ได้ (HTTP {r.status_code} — "
           "โค้ดอะไรก็ได้ ขอแค่ตอบกลับ = เครือข่ายเปิดถึง)")
    except Exception as e:                           # noqa: BLE001
        fail(f"ต่อ N8N ไม่ได้จากเซิร์ฟเวอร์นี้: {e} "
             "(เช็ค firewall / route จาก Windows Server ไปยัง N8N)")


def main() -> int:
    _say("=" * 68)
    _say("  VisionIQ — ตรวจความพร้อมเซิร์ฟเวอร์ (Login + Artwork)")
    try:
        import config as app_config
        _say(f"  CONFIG_VERSION: {app_config.CONFIG_VERSION}")
    except Exception:                                # noqa: BLE001
        pass
    _say("=" * 68)

    check_python()
    check_packages()
    check_tesseract()
    check_database()
    check_writable()
    check_auth_config()
    check_ocr_webhook()

    head("สรุป")
    if _FAILS:
        _say(f"  มี {_FAILS} รายการที่ต้องแก้ (FAIL) และ {_WARNS} รายการเตือน (WARN)")
        _say("  แก้ FAIL ให้หมดก่อน แล้วรันสคริปต์นี้ซ้ำ")
        return 1
    _say(f"  ผ่านทั้งหมด — ไม่มี FAIL (มี {_WARNS} รายการเตือน)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
