"""
Entry point สำหรับรันแอปบน Windows Server + IIS (ผ่าน HttpPlatformHandler).

IIS จะเป็นคนเปิดโปรเซสนี้ให้เอง โดยจองพอร์ต localhost ว่างมาให้ 1 พอร์ตแล้วส่ง
มาทาง environment variable ``HTTP_PLATFORM_PORT`` จากนั้น IIS จะทำหน้าที่
reverse proxy: browser → IIS (80/443 + TLS) → waitress (127.0.0.1:<port>) → Flask.

⚠️ ไฟล์นี้ "เพิ่มใหม่" ล้วน ๆ — ไม่แก้ app.py และไม่แตะโหมดใดเลย
   การรันบนสถานีด้วย ``py -3.9 app.py`` ยังทำงานเหมือนเดิม 100%.

ต่างจาก ``app.py`` ตรงไหน
------------------------
``app.py`` เรียก ``init_system()`` (โหลดโมเดล YOLO + ต่อ DB) เฉพาะใน
``if __name__ == '__main__'`` ซึ่ง **ไม่ถูกเรียกเมื่อรันแบบ WSGI** ไฟล์นี้จึงคุม
เองว่าจะ init อะไรบ้าง ผ่าน env ``VISIONIQ_IIS_INIT``:

  * ``db``   (ค่าเริ่มต้น) — ต่อ SQL Server อย่างเดียว: Login / Artwork /
                            แดชบอร์ด / ประวัติ ใช้งานได้ครบ **ไม่โหลดโมเดล**
                            (สตาร์ตเร็ว, ไม่กิน RAM หลาย GB) เพราะเซิร์ฟเวอร์
                            ไม่มีกล้อง USB อยู่แล้ว
  * ``none`` — ไม่ init อะไรเลย (Artwork + Login ก็ยังใช้ได้ เพราะโหมดนี้เก็บ
                ไฟล์ลงดิสก์ ไม่ได้ใช้ DB) แดชบอร์ด/ประวัติ Can Dent จะขึ้น 0
  * ``full`` — เรียก ``init_system()`` เต็มรูปแบบ (โหลดโมเดล YOLO ด้วย) ใช้เมื่อ
                เซิร์ฟเวอร์มีกล้อง/ต้องการ snapshot จริง ๆ เท่านั้น

รันเองเพื่อทดสอบก่อนต่อ IIS ได้ (จะ default ไปที่พอร์ต 8000):
    .venv\\Scripts\\python.exe deploy\\wsgi_iis.py
"""

from __future__ import annotations

import os
import sys

# ให้ import ของโปรเจกต์ (config, app, auth, artwork_check) หาเจอเสมอ ไม่ว่า IIS
# จะตั้ง working directory ไว้ที่ไหน
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

import config                      # noqa: E402
import app as app_module           # noqa: E402  (นี่คือ app.py ตัวเดิม)

application = app_module.app       # ชื่อมาตรฐานของ WSGI callable
app = application                  # alias เผื่อเครื่องมืออื่นมองหาชื่อ `app`


def _init_backend() -> str:
    """เตรียม backend ตาม VISIONIQ_IIS_INIT — ล้มเหลวก็ไม่ทำให้เว็บล่ม."""
    mode = (os.getenv("VISIONIQ_IIS_INIT", "db") or "db").strip().lower()

    if mode == "full":
        try:
            app_module.init_system()
            return "full (detector + database)"
        except Exception as e:                      # noqa: BLE001
            print(f"[wsgi_iis] init_system() ล้มเหลว: {e}", flush=True)
            return "full (ล้มเหลว — เว็บยังทำงาน แต่โหมดกล้องใช้ไม่ได้)"

    if mode == "db":
        try:
            from database import Database
            db = Database()
            if db.connect():
                app_module.db = db
                return "db (ต่อ SQL Server สำเร็จ)"
            return "db (ต่อ SQL Server ไม่สำเร็จ — แดชบอร์ด/ประวัติจะว่าง)"
        except Exception as e:                      # noqa: BLE001
            print(f"[wsgi_iis] ต่อฐานข้อมูลไม่สำเร็จ: {e}", flush=True)
            return "db (ล้มเหลว — แดชบอร์ด/ประวัติจะว่าง)"

    return "none (ไม่ init อะไรเลย)"


def _banner(port: int, init_result: str) -> None:
    """พิมพ์ลง stdout ซึ่ง HttpPlatformHandler เก็บลงไฟล์ log ให้ — ใช้ยืนยันว่า
    โค้ดเวอร์ชันไหนกำลังรัน และ dependency ที่ 'หายเงียบได้' ครบหรือไม่."""
    def _has(module: str) -> str:
        # BaseException เพราะ package ที่ native binding พังบางตัวโยน exception
        # ที่ไม่ได้สืบทอดจาก Exception ตอน import — แบนเนอร์ต้องไม่ทำให้แอปไม่บูต
        try:
            __import__(module)
            return "OK"
        except BaseException:                        # noqa: BLE001
            return "MISSING"

    try:
        from auth import config as auth_config
        auth_state = f"AUTH_ENABLED={auth_config.AUTH_ENABLED}"
    except Exception as e:                           # noqa: BLE001
        auth_state = f"auth layer ไม่พร้อม: {e}"

    print("=" * 70, flush=True)
    print("  VisionIQ — IIS / waitress entry point", flush=True)
    print(f"  CONFIG_VERSION   : {config.CONFIG_VERSION}", flush=True)
    print(f"  Python           : {sys.version.split()[0]}", flush=True)
    print(f"  Project root     : {_ROOT}", flush=True)
    print(f"  Listen           : 127.0.0.1:{port}", flush=True)
    print(f"  VISIONIQ_IIS_INIT: {init_result}", flush=True)
    print(f"  {auth_state}", flush=True)
    print(f"  SQL              : {config.SQL_SERVER}/{config.SQL_DATABASE}", flush=True)
    print(f"  OCR webhook      : {config.N8N_OCR_WEBHOOK_URL or '(ไม่ได้ตั้ง)'}", flush=True)
    print("  dependency ที่ 'ขาดแล้วเงียบ':", flush=True)
    print(f"    pyodbc={_has('pyodbc')}  bcrypt={_has('bcrypt')}  "
          f"PyJWT={_has('jwt')}  PyMuPDF={_has('fitz')}", flush=True)
    print(f"    pyspellchecker={_has('spellchecker')}  "
          f"pytesseract={_has('pytesseract')}", flush=True)
    print("=" * 70, flush=True)


def _listen_kwargs(port: int):
    """เลือกที่อยู่ที่จะเปิดรับ แล้วคืน ``(kwargs สำหรับ serve(), ข้อความอธิบาย)``.

    ค่าเริ่มต้นยังเป็น loopback เท่านั้นเหมือนเดิม — ไม่เปิดออกเครือข่ายเองเด็ดขาด.

    * ตั้ง ``VISIONIQ_WSGI_HOST`` = ระบุเองตรง ๆ (เช่น ``0.0.0.0`` เมื่อรันเป็น
      Windows Service โดยไม่มี IIS อยู่ข้างหน้า) — เป็น opt-in ล้วน
    * ไม่ตั้ง + อยู่ใต้ IIS = ฟังทั้ง IPv4 และ IPv6 loopback เพราะ
      **HttpPlatformHandler ต่อไปที่ชื่อ ``localhost``** ซึ่ง Windows แปลเป็น
      ``::1`` (IPv6) ก่อน ``127.0.0.1`` เสมอ — ถ้าฟังแค่ IPv4 จะได้ HTTP 502.3
      ทั้งที่โปรเซสรันอยู่ดี ๆ (กับดักที่หาสาเหตุยากมาก)
    * ไม่ตั้ง + รันเอง = ``127.0.0.1`` เหมือนเดิมเป๊ะ
    """
    host = (os.getenv("VISIONIQ_WSGI_HOST") or "").strip()
    if host:
        return {"host": host, "port": port}, host

    if os.getenv("HTTP_PLATFORM_PORT"):
        return ({"listen": "127.0.0.1:{0} [::1]:{0}".format(port)},
                "127.0.0.1 + [::1] (dual-stack loopback)")

    return {"host": "127.0.0.1", "port": port}, "127.0.0.1"


def main() -> int:
    # IIS ส่งพอร์ตมาให้ทาง HTTP_PLATFORM_PORT; ถ้ารันเองให้ใช้ 8000
    port = int(os.getenv("HTTP_PLATFORM_PORT")
               or os.getenv("VISIONIQ_WSGI_PORT")
               or 8000)

    init_result = _init_backend()
    _banner(port, init_result)

    try:
        from waitress import serve
    except ImportError:
        print("[wsgi_iis] ไม่พบ waitress — ติดตั้งด้วย "
              "`.venv\\Scripts\\python.exe -m pip install waitress`", flush=True)
        return 2

    listen_kwargs, listen_desc = _listen_kwargs(port)
    print(f"[wsgi_iis] เปิดรับที่ {listen_desc} พอร์ต {port}", flush=True)

    common = dict(
        threads=int(os.getenv("VISIONIQ_WSGI_THREADS", "8")),
        # การตรวจ Artwork 1 ครั้งเรียก OCR ทีละโซน (โซนละไม่เกิน N8N_OCR_TIMEOUT_S
        # วินาที) รวมกันแล้วอาจนานหลายนาที — ค่า default ของ waitress คือ 120 วิ
        # ซึ่งจะตัดกลางคัน. ต้องตั้งให้ยาวกว่าที่ตรวจจริงเสมอ และให้ requestTimeout
        # ใน web.config ยาวกว่าค่านี้อีกที.
        channel_timeout=int(os.getenv("VISIONIQ_WSGI_TIMEOUT", "1200")),
        # อัปโหลด artwork จำกัดที่ 40 MB ในโค้ด (artwork_check/routes.MAX_UPLOAD_MB)
        # เผื่อไว้ที่ชั้นนี้ให้กว้างกว่า เพื่อให้ข้อความ error มาจากแอป ไม่ใช่จากเซิร์ฟเวอร์
        max_request_body_size=int(os.getenv("VISIONIQ_WSGI_MAX_BODY",
                                            str(200 * 1024 * 1024))),
        ident="VisionIQ",
    )

    try:
        serve(application, **listen_kwargs, **common)
    except OSError as e:
        # เครื่องที่ปิด IPv6 ไว้จะ bind [::1] ไม่ได้ — ถอยไปใช้ IPv4 อย่างเดียว
        # ดีกว่าปล่อยให้เว็บล่มทั้งระบบเพราะ address family เดียว
        if "listen" not in listen_kwargs:
            raise
        print(f"[wsgi_iis] เปิด dual-stack ไม่สำเร็จ ({e}) — ถอยไปใช้ 127.0.0.1 อย่างเดียว",
              flush=True)
        serve(application, host="127.0.0.1", port=port, **common)
    return 0


if __name__ == "__main__":
    sys.exit(main())
