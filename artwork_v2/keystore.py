"""เก็บ API key ของ Cloud Vision — ฝั่งเซิร์ฟเวอร์เท่านั้น

กติกา:
* เก็บในไฟล์ ``data/artwork_v2/secret/vision_api_key.json`` (อยู่ใน .gitignore)
* environment variable ``ARTWORK_V2_VISION_API_KEY`` ชนะค่าในไฟล์เสมอ
* **ไม่มีฟังก์ชันใดคืนกุญแจเต็มไปให้หน้าเว็บ** — มีแค่ ``status()`` ที่คืน
  4 ตัวท้าย · ``get_key()`` ใช้ภายในตัวเรียก API เท่านั้น
* ``redact()`` ใช้ลบกุญแจออกจากข้อความทุกชนิดก่อนเข้า Log/หน้าเว็บ
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Optional, Tuple

from . import config

_LOCK = threading.Lock()
# รูปแบบกุญแจของ Google: ขึ้นต้น AIza ตามด้วย 35 ตัว — ใช้แค่เตือน ไม่บังคับ
_GOOGLE_KEY_RE = re.compile(r"^AIza[0-9A-Za-z_\-]{35}$")
_ANY_GOOGLE_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")


def _read_file() -> str:
    try:
        with open(config.KEY_FILE, "r", encoding="utf-8") as f:
            return str((json.load(f) or {}).get("key") or "").strip()
    except (OSError, ValueError):
        return ""


def get_key() -> Tuple[str, str]:
    """``(key, source)`` — source = ``"env"`` | ``"ui"`` | ``""``"""
    env = os.getenv(config.KEY_ENV, "").strip()
    if env:
        return env, "env"
    k = _read_file()
    return (k, "ui") if k else ("", "")


def mask(key: str) -> str:
    if not key:
        return ""
    return "••••" + key[-4:] if len(key) > 8 else "••••"


def status() -> dict:
    key, src = get_key()
    out = {"configured": bool(key), "source": src, "masked": mask(key),
           "length": len(key), "looks_like_google_key": bool(_GOOGLE_KEY_RE.match(key)),
           "endpoint": config.ENDPOINT, "model": config.MODEL,
           "env_overrides_ui": bool(os.getenv(config.KEY_ENV, "").strip()) and bool(_read_file())}
    try:
        st = os.stat(config.KEY_FILE)
        out["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
    except OSError:
        out["saved_at"] = ""
    return out


def validate(key: str) -> Optional[str]:
    """คืนข้อความผิดพลาด (ภาษาไทย) หรือ ``None`` ถ้าใช้ได้"""
    key = (key or "").strip()
    if not key:
        return "กรุณากรอก API key"
    if len(key) < 20 or len(key) > 200:
        return "ความยาว API key ผิดปกติ"
    if any(c.isspace() for c in key) or not key.isascii():
        return "API key ต้องเป็นตัวอักษรอังกฤษ/ตัวเลขต่อกัน ไม่มีช่องว่าง"
    return None


def save(key: str) -> None:
    key = (key or "").strip()
    err = validate(key)
    if err:
        raise ValueError(err)
    with _LOCK:
        os.makedirs(config.SECRET_DIR, exist_ok=True)
        tmp = config.KEY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"key": key, "saved_at": time.time()}, f)
        os.replace(tmp, config.KEY_FILE)
        try:                     # ให้เฉพาะเจ้าของไฟล์อ่านได้ (มีผลบน POSIX)
            os.chmod(config.KEY_FILE, 0o600)
        except OSError:
            pass


def delete() -> bool:
    with _LOCK:
        try:
            os.remove(config.KEY_FILE)
            return True
        except FileNotFoundError:
            return False


def redact(text, key: Optional[str] = None) -> str:
    """ลบกุญแจออกจากข้อความ — ทั้งกุญแจที่ใช้อยู่ และอะไรก็ตามที่หน้าตาเหมือน
    กุญแจของ Google (กันหลุดจาก URL ใน error ของ ``requests``)"""
    s = "" if text is None else str(text)
    k = key if key is not None else get_key()[0]
    if k and len(k) >= 8:
        s = s.replace(k, "***")
    s = _ANY_GOOGLE_KEY_RE.sub("***", s)
    s = re.sub(r"([?&]key=)[^&\s'\"]+", r"\1***", s)
    return s
