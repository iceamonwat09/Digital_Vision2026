"""
hik_camera.py — แหล่งภาพที่ 4 ของโหมด Can Dent: **กล้องอุตสาหกรรม Hikrobot (GigE / MVS SDK)**

หลักการออกแบบ (ยึดกฎเหล็กข้อ 1 ของโปรเจกต์: ห้ามกระทบโหมดอื่น)
-----------------------------------------------------------------
* คลาส ``HikCamera`` มี **สัญญาเดียวกับ ``camera.Camera`` เป๊ะ** (``initialize`` /
  ``read_frame`` / ``release``) ⇒ ``capture_loop``/``inference_loop``/``generate_frames``
  ใน ``app.py`` **ไม่ต้องแก้แม้บรรทัดเดียว**.
* **ไม่ import MVS SDK ตอน import โมดูล** — เครื่องที่ไม่มี MVS (dev/CI/Linux) ยัง
  ``import hik_camera`` ได้ปกติ. SDK ถูกโหลดครั้งแรกตอนเรียกใช้จริงเท่านั้น.
* **ไม่มีเมธอด ``set_control``** โดยตั้งใจ — ``/api/camera/control`` (สไลเดอร์ brightness/
  contrast ของกล้อง USB) ใช้ ``hasattr(cam, "set_control")`` เป็นตัวกรอง ถ้าคลาสนี้มีเมธอด
  ชื่อเดียวกัน สไลเดอร์ของโหมด USB จะไปสั่งกล้องอุตสาหกรรมโดยไม่ตั้งใจ. กล้องนี้ปรับผ่าน
  ``set_params()`` + ``/api/camera/hik/params`` แยกของตัวเอง.

บทเรียนจากการวัดจริงบนสถานี 19 ส.ค. 2026 ที่ถูกฝังเป็นพฤติกรรมของคลาสนี้
------------------------------------------------------------------------
1. **packet size / packet delay กล้องไม่จำ** — เปิดกล้องใหม่ทีไรกลับเป็น 1500 / 400 เสมอ
   ⇒ ที่ 5MP จะได้ **15-17 fps พร้อมเฟรมหาย 2-3 ใน 60** แบบเงียบ ๆ. คลาสนี้จึง
   **ตั้ง packet size = ค่าที่เหมาะสม + delay = 0 ทุกครั้งตอนเปิดกล้อง** (วัดแล้วได้
   23.65 fps เฟรมหาย 0).
2. **ห้ามเชื่อ struct ของ SDK เป็นแหล่งความจริงเดียว** — layout ต่างกันตามรุ่น
   (บนสถานี MAC อยู่บน ``MV_CC_DEVICE_INFO`` ชั้นนอก ไม่ใช่ใน ``stGigEInfo``) ⇒ อ่าน
   ตัวตนกล้องจาก **node ของกล้อง** (``DeviceModelName`` ฯลฯ) หลังเปิดเสมอ.
3. **binning ตั้งไม่ได้บนเฟิร์มแวร์นี้** (``0x80000109``) ⇒ **ROI คือคันโยกเดียว**
   ที่ทำให้ได้ fps สูงขึ้น (ROI ครึ่งกลาง = 69 fps) — และตรงกับแผนที่อยากให้ฝากระป๋อง
   กินเฟรม ~70% อยู่แล้ว.
4. **แปลงสีด้วยตัวแปลงของ SDK** (``MV_CC_ConvertPixelType``) ไม่ใช่เดา Bayer pattern เอง
   ใน OpenCV — ชื่อ ``BayerRG`` ของ GenICam กับของ OpenCV ไม่ตรงกันเสมอไป และ
   **สลับ pattern ผิด = สีเพี้ยนทั้งภาพแบบไม่มี error** ซึ่งกระทบผลตรวจโดยตรง.
5. เฟรม 5MP = 15 MB ⇒ **ย่อก่อนส่งเข้า pipeline** (``HIK_LIVE_MAX_WIDTH``) ไม่งั้น
   ``generate_frames`` ต้อง encode JPEG ของภาพ 5MP ทุกเฟรม.
"""

import ctypes
import importlib
import json
import os
import queue
import sys
import threading
import time

import numpy as np

try:
    import cv2
except Exception:                                    # pragma: no cover - cv2 อยู่ใน requirements
    cv2 = None

try:
    import config
except Exception:                                    # pragma: no cover
    config = None

try:
    from logger import setup_logger
    logger = setup_logger(__name__)
except Exception:                                    # pragma: no cover
    import logging
    logger = logging.getLogger(__name__)


def _cfg(name, default=None):
    """อ่านค่าจาก config แบบไม่พัง (โมดูลนี้ต้อง import ได้แม้ config ยังไม่พร้อม)."""
    return getattr(config, name, default) if config is not None else default


# ════════════════════════════════════════════════════════════════════
# ① การหา/โหลด MVS SDK  (แหล่งความจริงเดียว — diagnose_hikrobot.py import จากที่นี่)
# ════════════════════════════════════════════════════════════════════
SDK_SUBPATHS = [
    os.path.join("Development", "Samples", "Python", "MvImport"),
    os.path.join("Development", "Samples", "Python", "MvImport", "MvImport"),
    os.path.join("Development", "Python", "MvImport"),
    os.path.join("Samples", "Python", "MvImport"),
]
SDK_ROOTS = [
    os.environ.get("MVCAM_SDK_PATH"),
    os.environ.get("MVCAM_COMMON_RUNENV"),
    r"C:\Program Files (x86)\MVS",
    r"C:\Program Files\MVS",
    r"C:\Program Files (x86)\Common Files\MVS",
]

_sdk_cache = {"mod": None, "info": None}


def sdk_candidates(extra=None):
    """ทางที่จะไล่หา MvImport (เรียงจากที่น่าจะใช่ที่สุด)."""
    out = []
    if extra:
        out.append(extra)
    env_extra = _cfg("HIK_SDK_PATH")
    if env_extra:
        out.append(env_extra)
    for root in SDK_ROOTS:
        if not root:
            continue
        out.append(root)                              # เผื่อ env ชี้เข้า MvImport อยู่แล้ว
        for sub in SDK_SUBPATHS:
            out.append(os.path.join(root, sub))
    seen, uniq = set(), []
    for p in out:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def load_sdk(extra_path=None, force=False):
    """
    คืน ``(module, info)`` — module มีทั้งคลาส ``MvCamera`` และค่าคงที่ทั้งหมด
    (ไฟล์ของ Hikrobot ทำ ``from CameraParams_header import *`` ไว้ให้แล้ว).
    คืน ``(None, info)`` เมื่อโหลดไม่ได้ — **ไม่โยน exception** เพื่อให้ผู้เรียกรายงาน
    สาเหตุให้ผู้ใช้ได้ แทนที่จะพังทั้งหน้า.
    """
    if _sdk_cache["mod"] is not None and not force:
        return _sdk_cache["mod"], _sdk_cache["info"]
    cands = sdk_candidates(extra_path)
    tried, errors = list(cands), []
    # ⚠️ ใส่กลับหลังไปหน้า: sys.path.insert(0, ...) ทำให้ตัว "ที่ใส่ทีหลัง" อยู่หน้าสุด
    #    ถ้าวนตามลำดับความสำคัญตรง ๆ ผลจะกลับด้าน — ทางที่ผู้ใช้ระบุเอง
    #    (config.HIK_SDK_PATH / MVCAM_SDK_PATH) จะแพ้ทางมาตรฐานที่บังเอิญมีอยู่ด้วย
    for p in reversed(cands):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
    for name in ("MvCameraControl_class", "MvCameraControl"):
        try:
            mod = importlib.import_module(name)
        except Exception as e:
            errors.append("%s: %s" % (name, e))
            continue
        if hasattr(mod, "MvCamera"):
            path = getattr(mod, "__file__", "") or ""
            # SDK ปลอมมีไว้สำหรับเทสต์เท่านั้น. ถ้ามันถูกโหลดในเครื่องที่ใช้งานจริง
            # ระบบจะ "ตรวจ" ภาพสังเคราะห์แล้วรายงานผลเหมือนของจริงทุกประการ =
            # ผลตรวจที่ผิดแบบมั่นใจ (กฎเหล็กข้อ 2) จึงต้องตะโกนออกมาให้เห็น
            is_fake = "fake_mvs" in path.replace("\\", "/")
            if is_fake:
                logger.warning("[hik] ⚠️ กำลังใช้ MVS SDK **ปลอม** สำหรับทดสอบ (%s) — "
                               "ภาพที่ได้ไม่ใช่ภาพจากกล้องจริง ห้ามใช้ตัดสินคุณภาพงาน", path)
            info = {"module": name, "file": path, "is_fake": is_fake,
                    "tried": tried, "errors": errors}
            _sdk_cache["mod"], _sdk_cache["info"] = mod, info
            return mod, info
        errors.append("%s: โหลดได้แต่ไม่มีคลาส MvCamera" % name)
    info = {"module": None, "file": None, "tried": tried, "errors": errors}
    _sdk_cache["info"] = info
    return None, info


_INSTALL_HINT = (
    "ติดตั้งโปรแกรม MVS ของ Hikrobot แล้วตรวจว่ามีโฟลเดอร์ "
    r"C:\Program Files (x86)\MVS\Development\Samples\Python\MvImport "
    "(ถ้าติดตั้งไว้ที่อื่นให้ตั้ง config.HIK_SDK_PATH). ถ้ามีโฟลเดอร์แล้วแต่ยัง import "
    "ไม่ผ่าน = หา MvCameraControl.dll ไม่เจอ → ตรวจ env MVCAM_COMMON_RUNENV และว่า "
    "Python เป็น 64-bit ตรงกับ SDK"
)


def sdk_status():
    """สรุปสถานะ SDK สำหรับหน้าเว็บ (ไม่เปิดกล้อง)."""
    mod, info = load_sdk()
    return {
        "available": mod is not None,
        "module": info.get("module"),
        "file": info.get("file"),
        "is_fake": bool(info.get("is_fake")),
        "errors": info.get("errors", []),
        "hint": None if mod is not None else _INSTALL_HINT,
    }


def _ensure_initialized(mv):
    """SDK 4.4+ ต้องเรียก MV_CC_Initialize ก่อนใช้งาน (รุ่นเก่าไม่มี = ข้ามได้)."""
    fn = getattr(mv.MvCamera, "MV_CC_Initialize", None)
    if fn is None:
        return
    try:
        fn()
    except Exception as e:                            # pragma: no cover
        logger.warning("MV_CC_Initialize ล้มเหลว: %s", e)


# ════════════════════════════════════════════════════════════════════
# ② ตัวช่วยแปลงค่า
# ════════════════════════════════════════════════════════════════════
def cstr(buf):
    """ctypes byte-array → str (ปลอดภัยกับข้อมูลที่ไม่มี null terminator)."""
    try:
        raw = bytes(bytearray(buf))
    except Exception:
        return ""
    return raw.split(b"\x00", 1)[0].decode("utf-8", "ignore").strip()


def ip_str(v):
    try:
        v = int(v) & 0xFFFFFFFF
        return "%d.%d.%d.%d" % ((v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
    except Exception:
        return ""


def mac_str(hi, lo):
    try:
        hi, lo = int(hi) & 0xFFFF, int(lo) & 0xFFFFFFFF
        b = [(hi >> 8) & 0xFF, hi & 0xFF, (lo >> 24) & 0xFF,
             (lo >> 16) & 0xFF, (lo >> 8) & 0xFF, lo & 0xFF]
        return ":".join("%02X" % x for x in b)
    except Exception:
        return ""


def pixel_names(mv):
    """map ค่าตัวเลข → ชื่อ PixelType จากตัวโมดูลเอง (ไม่ hard-code)."""
    out = {}
    for name in dir(mv):
        if name.startswith("PixelType_Gvsp_"):
            try:
                out[int(getattr(mv, name))] = name.replace("PixelType_Gvsp_", "")
            except Exception:
                pass
    return out


# ════════════════════════════════════════════════════════════════════
# ③ การอ้างถึงกล้องจากฝั่ง app  ("hik:<serial>" / "hik:#<index>" / "hik:")
# ════════════════════════════════════════════════════════════════════
def source_prefix():
    return _cfg("HIK_SOURCE_PREFIX", "hik:")


def is_hik_source(value):
    """True เมื่อค่า camera_index ที่ส่งมาจากหน้าเว็บหมายถึงกล้อง Hikrobot."""
    return isinstance(value, str) and value.lower().startswith(source_prefix().lower())


def parse_source(value):
    """
    ``"hik:DA4994130"`` → ``("serial", "DA4994130")`` ·
    ``"hik:#0"``        → ``("index", 0)`` ·
    ``"hik:"``          → ``(None, None)`` (ตัวแรกที่เจอ)
    """
    if not is_hik_source(value):
        return (None, None)
    rest = value[len(source_prefix()):].strip()
    if not rest:
        return (None, None)
    if rest.startswith("#"):
        try:
            return ("index", int(rest[1:]))
        except ValueError:
            return (None, None)
    return ("serial", rest)


def make_source(serial=None, index=None):
    if serial:
        return "%s%s" % (source_prefix(), serial)
    if index is not None:
        return "%s#%d" % (source_prefix(), index)
    return source_prefix()


# ════════════════════════════════════════════════════════════════════
# ④ ค่าตั้งที่บันทึกข้ามการรีสตาร์ต
# ════════════════════════════════════════════════════════════════════
def settings_path():
    p = _cfg("HIK_SETTINGS_FILE") or os.path.join("data", "hik_camera.json")
    if not os.path.isabs(p):
        base = _cfg("BASE_DIR") or os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(base, p)
    return p


def load_settings():
    """อ่านค่าที่ผู้ใช้บันทึกไว้ — ไฟล์เสีย/ไม่มี = คืน {} (ใช้ค่า default ของ config)."""
    try:
        with open(settings_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(params):
    """เขียนค่าลงดิสก์แบบ atomic (เขียนไฟล์ชั่วคราวแล้ว replace) — กันไฟล์พังตอนไฟดับ."""
    path = settings_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(params, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return True
    except Exception as e:
        logger.warning("บันทึกค่ากล้อง Hikrobot ไม่สำเร็จ: %s", e)
        return False


# ════════════════════════════════════════════════════════════════════
# ⑤ อ่าน/เขียน GenICam node — ทุกเมธอด "ห้ามโยน"
#    (เฟิร์มแวร์ต่างรุ่นมี node ไม่เท่ากัน: บนสถานีนี้ binning/gamma/temperature
#     เข้าไม่ถึง ⇒ ต้องคืน None แล้วให้ผู้เรียกตัดสินใจ ไม่ใช่ทำให้ทั้งระบบล้ม)
# ════════════════════════════════════════════════════════════════════
_AUTO_MAP = {"Off": 0, "Once": 1, "Continuous": 2}
_ONOFF_MAP = {"Off": 0, "On": 1}
_TRIG_SRC_MAP = {"Line0": 0, "Line1": 1, "Line2": 2, "Line3": 3,
                 "Counter0": 4, "Software": 7}
_TRIG_ACT_MAP = {"RisingEdge": 0, "FallingEdge": 1, "LevelHigh": 2, "LevelLow": 3}


class _NodeIO(object):
    def __init__(self, mv, handle, lock):
        self.mv, self.h, self.lock = mv, handle, lock

    # ── อ่าน ────────────────────────────────────────────
    def get_int(self, key):
        mv = self.mv
        for st_name, fn_name in (("MVCC_INTVALUE_EX", "MV_CC_GetIntValueEx"),
                                 ("MVCC_INTVALUE", "MV_CC_GetIntValue")):
            st_cls, fn = getattr(mv, st_name, None), getattr(self.h, fn_name, None)
            if st_cls is None or fn is None:
                continue
            try:
                st = st_cls()
                ctypes.memset(ctypes.byref(st), 0, ctypes.sizeof(st))
                with self.lock:
                    ret = fn(key, st)
                if ret == 0:
                    lo, hi = int(st.nMin), int(st.nMax)
                    # ช่วงที่เป็นขอบ int64 = "ไม่มีช่วงจริง" (node อ่านอย่างเดียว)
                    if lo <= -(2 ** 62) or hi >= 2 ** 62:
                        lo = hi = None
                    return {"value": int(st.nCurValue), "min": lo, "max": hi,
                            "inc": int(getattr(st, "nInc", 1) or 1)}
            except Exception:
                continue
        return None

    def get_float(self, key):
        st_cls = getattr(self.mv, "MVCC_FLOATVALUE", None)
        if st_cls is None:
            return None
        try:
            st = st_cls()
            ctypes.memset(ctypes.byref(st), 0, ctypes.sizeof(st))
            with self.lock:
                ret = self.h.MV_CC_GetFloatValue(key, st)
            if ret != 0:
                return None
            return {"value": float(st.fCurValue), "min": float(st.fMin),
                    "max": float(st.fMax)}
        except Exception:
            return None

    def get_bool(self, key):
        try:
            v = ctypes.c_bool(False)
            with self.lock:
                ret = self.h.MV_CC_GetBoolValue(key, v)
            if ret != 0:
                return None
            return {"value": bool(v.value)}
        except Exception:
            return None

    def get_enum(self, key, name_map=None):
        st_cls = getattr(self.mv, "MVCC_ENUMVALUE", None)
        if st_cls is None:
            return None
        try:
            st = st_cls()
            ctypes.memset(ctypes.byref(st), 0, ctypes.sizeof(st))
            with self.lock:
                ret = self.h.MV_CC_GetEnumValue(key, st)
            if ret != 0:
                return None
            cur = int(st.nCurValue)
            sup = []
            for i in range(min(int(getattr(st, "nSupportedNum", 0) or 0), 64)):
                try:
                    sup.append(int(st.nSupportValue[i]))
                except Exception:
                    break
            rev = {v: k for k, v in (name_map or {}).items()}
            sym = rev.get(cur) or self._symbolic(key, cur) or str(cur)
            # ⚠️ ห้ามใช้ชื่อ "supported" กับรายการตัวเลือก — คีย์นั้นถูกใช้เป็น
            #    boolean "กล้องมี node นี้ไหม" ในผลลัพธ์ของ get_params() ถ้าชนกันจะ
            #    ทำให้ UI ตัดสินผิดว่า node ที่ไม่มีอยู่จริง "มี" (ปุ่มที่กดแล้วเงียบ)
            return {"value": cur, "symbolic": sym, "choices": sup,
                    "options": [rev.get(v) or self._symbolic(key, v) or str(v) for v in sup]}
        except Exception:
            return None

    def _symbolic(self, key, value):
        """SDK ใหม่บอกชื่อ symbolic ได้ตรง ๆ — ดีกว่าเดา mapping เอง."""
        ent_cls = getattr(self.mv, "MVCC_ENUMENTRY", None)
        fn = getattr(self.h, "MV_CC_GetEnumEntrySymbolic", None)
        if ent_cls is None or fn is None:
            return None
        try:
            ent = ent_cls()
            ctypes.memset(ctypes.byref(ent), 0, ctypes.sizeof(ent))
            ent.nValue = int(value)
            with self.lock:
                if fn(key, ent) != 0:
                    return None
            return cstr(ent.chSymbolic) or None
        except Exception:
            return None

    def get_str(self, key):
        st_cls = getattr(self.mv, "MVCC_STRINGVALUE", None)
        if st_cls is None:
            return None
        try:
            st = st_cls()
            ctypes.memset(ctypes.byref(st), 0, ctypes.sizeof(st))
            with self.lock:
                ret = self.h.MV_CC_GetStringValue(key, st)
            if ret != 0:
                return None
            raw = getattr(st, "chCurValue", b"")
            if isinstance(raw, bytes):
                return raw.split(b"\x00", 1)[0].decode("utf-8", "ignore").strip()
            return cstr(raw)
        except Exception:
            return None

    # ── เขียน (คืน (ok, error_code)) ─────────────────────
    def set_int(self, key, value):
        for fn_name in ("MV_CC_SetIntValueEx", "MV_CC_SetIntValue"):
            fn = getattr(self.h, fn_name, None)
            if fn is None:
                continue
            try:
                with self.lock:
                    ret = fn(key, int(value))
                if ret == 0:
                    return True, 0
                last = ret
            except Exception:
                last = -1
        return False, locals().get("last", -1)

    def set_float(self, key, value):
        try:
            with self.lock:
                ret = self.h.MV_CC_SetFloatValue(key, float(value))
            return (ret == 0), ret
        except Exception:
            return False, -1

    def set_bool(self, key, value):
        try:
            with self.lock:
                ret = self.h.MV_CC_SetBoolValue(key, bool(value))
            return (ret == 0), ret
        except Exception:
            return False, -1

    def set_enum(self, key, value):
        try:
            with self.lock:
                ret = self.h.MV_CC_SetEnumValue(key, int(value))
            return (ret == 0), ret
        except Exception:
            return False, -1

    def exec_command(self, key):
        try:
            with self.lock:
                ret = self.h.MV_CC_SetCommandValue(key)
            return (ret == 0), ret
        except Exception:
            return False, -1


# ════════════════════════════════════════════════════════════════════
# ⑥ ตารางค่าที่หน้าเว็บปรับได้
#    live=True  → ตั้งได้ระหว่างสตรีม
#    live=False → GenICam ล็อก node ระหว่าง grabbing ⇒ ต้อง Stop→ตั้ง→Start
#                 (ถ้าไม่ทำ การ set จะ "ไม่ติดแบบเงียบ" แล้วผู้ใช้เห็นค่าบนจอ
#                  ไม่ตรงกับกล้องจริง = ผลตรวจกับสิ่งที่คิดว่าตั้งไว้ไม่ตรงกัน)
# ════════════════════════════════════════════════════════════════════
PARAM_SPECS = [
    {"key": "exposure_us",  "node": "ExposureTime",   "type": "float", "live": True,
     "label": "เวลารับแสง (µs)"},
    {"key": "exposure_auto", "node": "ExposureAuto",  "type": "enum",  "live": True,
     "map": _AUTO_MAP, "label": "ปรับแสงอัตโนมัติ"},
    {"key": "gain_db",      "node": "Gain",           "type": "float", "live": True,
     "label": "เกน (dB)"},
    {"key": "gain_auto",    "node": "GainAuto",       "type": "enum",  "live": True,
     "map": _AUTO_MAP, "label": "ปรับเกนอัตโนมัติ"},
    {"key": "balance_white_auto", "node": "BalanceWhiteAuto", "type": "enum", "live": True,
     "map": _AUTO_MAP, "label": "สมดุลแสงขาว"},
    {"key": "framerate_enable", "node": "AcquisitionFrameRateEnable", "type": "bool",
     "live": True, "label": "จำกัดอัตราเฟรม"},
    {"key": "framerate",    "node": "AcquisitionFrameRate", "type": "float", "live": True,
     "label": "อัตราเฟรมสูงสุด (fps)"},
    {"key": "width",        "node": "Width",          "type": "int",   "live": False,
     "label": "ความกว้าง ROI"},
    {"key": "height",       "node": "Height",         "type": "int",   "live": False,
     "label": "ความสูง ROI"},
    {"key": "offset_x",     "node": "OffsetX",        "type": "int",   "live": False,
     "label": "ตำแหน่ง X ของ ROI"},
    {"key": "offset_y",     "node": "OffsetY",        "type": "int",   "live": False,
     "label": "ตำแหน่ง Y ของ ROI"},
    {"key": "pixel_format", "node": "PixelFormat",    "type": "enum_pixel", "live": False,
     "label": "รูปแบบพิกเซล"},
    {"key": "reverse_x",    "node": "ReverseX",       "type": "bool",  "live": False,
     "label": "กลับภาพแนวนอน"},
    {"key": "reverse_y",    "node": "ReverseY",       "type": "bool",  "live": False,
     "label": "กลับภาพแนวตั้ง"},
    {"key": "trigger_mode", "node": "TriggerMode",    "type": "enum",  "live": True,
     "map": _ONOFF_MAP, "label": "โหมดทริกเกอร์"},
    {"key": "trigger_source", "node": "TriggerSource", "type": "enum", "live": True,
     "map": _TRIG_SRC_MAP, "label": "แหล่งทริกเกอร์"},
    {"key": "trigger_activation", "node": "TriggerActivation", "type": "enum", "live": True,
     "map": _TRIG_ACT_MAP, "label": "ขอบสัญญาณทริกเกอร์"},
    {"key": "packet_size",  "node": "GevSCPSPacketSize", "type": "int", "live": False,
     "label": "ขนาดแพ็กเก็ต (GigE)"},
    {"key": "packet_delay", "node": "GevSCPD",        "type": "int",   "live": True,
     "label": "หน่วงแพ็กเก็ต (GigE)"},
]
_SPEC_BY_KEY = {s["key"]: s for s in PARAM_SPECS}

# ค่าที่เป็น "รูปทรงของภาพ" — ต้องตั้งเรียงกันเป็นชุด (offset ก่อน/หลัง) ไม่ใช่ทีละตัว
_ROI_KEYS = ("width", "height", "offset_x", "offset_y")


# ════════════════════════════════════════════════════════════════════
# ⑦ enumerate อุปกรณ์
# ════════════════════════════════════════════════════════════════════
def _enum_raw(mv):
    """
    คืน ``(device_list_struct, entries)`` — ต้องถือ ``device_list_struct`` ไว้ตลอด
    ช่วงที่ยังใช้ ``entry["_info"]`` เพราะ struct เหล่านั้นอยู่ในหน่วยความจำที่ SDK ถือไว้.
    """
    lst = mv.MV_CC_DEVICE_INFO_LIST()
    ctypes.memset(ctypes.byref(lst), 0, ctypes.sizeof(lst))
    tl = int(getattr(mv, "MV_GIGE_DEVICE", 1)) | int(getattr(mv, "MV_USB_DEVICE", 4))
    ret = mv.MvCamera.MV_CC_EnumDevices(tl, lst)
    if ret != 0:
        return lst, None, "MV_CC_EnumDevices ล้มเหลว (0x%X)" % (ret & 0xFFFFFFFF)

    gige_t = int(getattr(mv, "MV_GIGE_DEVICE", 1))
    entries = []
    for i in range(int(lst.nDeviceNum)):
        d = {"index": i, "kind": "?", "model": "", "serial": "", "version": "",
             "user_name": "", "ip": "", "nic": "", "mac": "", "error": None}
        try:
            info = ctypes.cast(lst.pDeviceInfo[i],
                               ctypes.POINTER(mv.MV_CC_DEVICE_INFO)).contents
        except Exception as e:
            d["error"] = "%s: %s" % (type(e).__name__, e)
            entries.append(d)
            continue
        try:
            if int(info.nTLayerType) == gige_t:
                g = info.SpecialInfo.stGigEInfo
                # ⚠️ MAC อยู่คนละชั้นแล้วแต่รุ่น SDK — บนสถานี (MVS ที่มากับ V4.0.42)
                #    อยู่บน MV_CC_DEVICE_INFO ชั้นนอก. ถ้าอ่านแบบตายตัวจะ AttributeError
                #    แล้วข้อมูล "ทั้งแถว" หายไปกลายเป็นค่าว่าง.
                hi = getattr(g, "nMacAddrHigh", None)
                lo = getattr(g, "nMacAddrLow", None)
                if hi is None or lo is None:
                    hi = getattr(info, "nMacAddrHigh", 0)
                    lo = getattr(info, "nMacAddrLow", 0)
                d.update(kind="GigE", model=cstr(g.chModelName),
                         serial=cstr(g.chSerialNumber), version=cstr(g.chDeviceVersion),
                         user_name=cstr(g.chUserDefinedName),
                         ip=ip_str(getattr(g, "nCurrentIp", 0)),
                         nic=ip_str(getattr(g, "nNetExport", 0)),
                         mac=mac_str(hi, lo))
            else:
                u = info.SpecialInfo.stUsb3VInfo
                d.update(kind="USB3", model=cstr(u.chModelName),
                         serial=cstr(u.chSerialNumber), version=cstr(u.chDeviceVersion),
                         user_name=cstr(u.chUserDefinedName))
        except Exception as e:
            d["error"] = "%s: %s" % (type(e).__name__, e)
        try:
            acc = getattr(mv, "MV_ACCESS_Exclusive", 1)
            d["accessible"] = bool(mv.MvCamera.MV_CC_IsDeviceAccessible(info, acc))
        except Exception:
            d["accessible"] = None
        d["_info"] = info
        entries.append(d)
    return lst, entries, None


def scan_devices():
    """
    รายชื่อกล้องที่มองเห็น (ไม่เปิดกล้อง) สำหรับ dropdown ในหน้าเว็บ.
    คืน ``(devices, error)`` — ``devices`` เป็น dict ที่ JSON ได้ (ไม่มี ctypes ติดไป).
    """
    mv, info = load_sdk()
    if mv is None:
        return [], "ไม่พบ MVS SDK — " + _INSTALL_HINT
    _ensure_initialized(mv)
    try:
        lst, entries, err = _enum_raw(mv)
    except Exception as e:
        return [], "enumerate ล้มเหลว: %s" % e
    if err:
        return [], err
    out = []
    for d in entries or []:
        clean = {k: v for k, v in d.items() if k != "_info"}
        clean["source"] = make_source(serial=d.get("serial") or None,
                                      index=None if d.get("serial") else d["index"])
        out.append(clean)
    del lst
    return out, None


# ════════════════════════════════════════════════════════════════════
# ⑧ ตัวเขียนภาพชุดข้อมูล (เทรน/verify) — เขียนในเธรดแยก ห้ามหน่วงการจับภาพ
# ════════════════════════════════════════════════════════════════════
class _DatasetWriter(object):
    """
    เก็บภาพลงดิสก์แบบ "ทิ้งได้" — ถ้าคิวเต็ม (ดิสก์ตามไม่ทัน) จะ **ทิ้งเฟรมแล้วนับไว้**
    แทนที่จะบล็อกเธรดจับภาพ. การจับภาพช้าลง = ผลตรวจเปลี่ยน ซึ่งรับไม่ได้;
    ภาพชุดข้อมูลขาดไปบ้างไม่กระทบอะไร ตราบใดที่ "บอกจำนวนที่ทิ้ง" ให้ผู้ใช้เห็น.
    """

    def __init__(self, root, max_frames=2000, jpeg_quality=95, every_n=1,
                 duration_s=0, min_free_mb=2048, meta=None,
                 counters_cb=None, net_cb=None):
        self.root = root
        # ค่าตั้งกล้อง ณ วินาทีที่เริ่มเก็บ — เขียนลง meta.json ข้าง ๆ ภาพ.
        # ถ้าไม่เก็บไว้ เปิดโฟลเดอร์ดูทีหลังจะไม่มีทางรู้ว่าถ่ายที่ exposure เท่าไร
        # ⇒ ภาพที่เก็บมาเทียบข้ามรอบไม่ได้เลย (ซึ่งคือทั้งหมดของการทดสอบความเบลอ)
        self.meta = dict(meta or {})
        # อ่านตัวนับของกล้อง (ราคาถูก ไม่แตะ SDK) และสถิติเครือข่าย (แตะ SDK จึงเรียกแค่
        # ตอนเริ่ม/ตอนจบ). สองอย่างนี้คือสิ่งที่แยก **"เฟรมหายระหว่างทาง"** ออกจาก
        # **"ดิสก์เขียนไม่ทัน"** ได้ — ซึ่งวิธีแก้คนละเรื่องกันโดยสิ้นเชิง
        self.counters_cb = counters_cb
        self.net_cb = net_cb
        self.max_frames = int(max_frames)
        self.jpeg_quality = int(jpeg_quality)
        self.every_n = max(1, int(every_n))
        self.duration_s = float(duration_s or 0)
        self.min_free_mb = int(min_free_mb)
        self.dir = None
        self.saved = 0
        self.dropped = 0
        self.bytes = 0
        self.error = None
        self.finished_reason = None
        self._seen = 0
        self._t0 = 0.0
        # เวลามาถึงของแต่ละเฟรม (บันทึกใน **เธรดจับภาพ** ตอนเข้าคิว ไม่ใช่ตอนเขียนไฟล์
        # ซึ่งช้ากว่าและไม่คงที่) ⇒ ระยะห่างระหว่างเฟรมเป็นเวลาจริงของกล้อง.
        # คิวเป็น FIFO และ every_n ถูกคัดก่อนหน้านี้แล้ว ⇒ รายการที่ i คู่กับไฟล์ที่ i+1
        self._ts = []
        self._meta_done = False
        self._enc_ms = []                             # เวลา encode JPEG ต่อเฟรม
        self._write_ms = []                           # เวลาเขียนลงดิสก์ต่อเฟรม
        self._fps_series = []                         # (วินาทีที่, เฟรมที่กล้องส่งมาแล้ว)
        self._q = queue.Queue(maxsize=8)
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.dir = os.path.join(self.root, stamp)
        try:
            os.makedirs(self.dir, exist_ok=True)
        except Exception as e:
            self.error = "สร้างโฟลเดอร์ไม่สำเร็จ: %s" % e
            return False
        free_mb = self._free_mb()
        if free_mb is not None and free_mb < self.min_free_mb:
            self.error = ("พื้นที่ว่างเหลือ %d MB (ต้องการอย่างน้อย %d MB) — ไม่เริ่มบันทึก"
                          % (free_mb, self.min_free_mb))
            return False
        self._t0 = time.time()
        self.meta.setdefault("started_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        self.meta["every_n"] = self.every_n
        self.meta["jpeg_quality"] = self.jpeg_quality
        self.meta["diag_start"] = self._snapshot(net=True)
        self._write_meta()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def _snapshot(self, net=False):
        """ภาพนิ่งของตัวนับ ณ ขณะนี้ — ``net=True`` จึงจะแตะ SDK (ช้ากว่ามาก)."""
        snap = {"t": round(time.time(), 3)}
        try:
            if self.counters_cb is not None:
                snap.update(self.counters_cb() or {})
            if net and self.net_cb is not None:
                snap["net"] = self.net_cb()
        except Exception as e:                        # pragma: no cover - ไม่ยอมให้ล้มการเก็บภาพ
            snap["error"] = str(e)
        return snap

    def _write_meta(self, final=False):
        """เขียน meta.json (เขียนทับได้ — เรียกตอนเริ่มและตอนจบ)."""
        if not self.dir:
            return
        data = dict(self.meta)
        if final:
            # ตัดให้เท่าจำนวนที่ **เขียนลงดิสก์จริง** — เฟรมที่ค้างในคิวตอนสั่งหยุด
            # ไม่ได้ถูกบันทึก ถ้าไม่ตัด เวลาจะเลื่อนไปทั้งชุดโดยไม่มีใครรู้
            ts = list(self._ts)[:self.saved]
            data["frame_ts"] = [round(t, 6) for t in ts]
            data["saved"] = self.saved
            data["dropped"] = self.dropped
            data["finished_reason"] = self.finished_reason
            data["ended_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            data["diag_end"] = self._snapshot(net=True)
            data["elapsed_s"] = round(max(1e-6, time.time() - self._t0), 3)
            data["fps_series"] = list(self._fps_series)
            data["stage_ms"] = {
                "encode": round(sum(self._enc_ms) / len(self._enc_ms), 2) if self._enc_ms else None,
                "write": round(sum(self._write_ms) / len(self._write_ms), 2) if self._write_ms else None,
                "encode_max": round(max(self._enc_ms), 2) if self._enc_ms else None,
                "write_max": round(max(self._write_ms), 2) if self._write_ms else None,
            }
        try:
            with open(os.path.join(self.dir, "meta.json"), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:                        # pragma: no cover - ดิสก์/สิทธิ์
            logger.warning("[hik] เขียน meta.json ไม่สำเร็จ: %s", e)

    def _finish_meta(self):
        if self._meta_done:
            return
        self._meta_done = True
        self._write_meta(final=True)

    def _free_mb(self):
        try:
            import shutil
            return int(shutil.disk_usage(self.dir or self.root).free / (1024 * 1024))
        except Exception:
            return None

    def put(self, frame):
        """
        รับเฟรมจากเธรดจับภาพ — **ห้ามบล็อกเด็ดขาด** เพราะการจับภาพช้าลง = ผลตรวจเปลี่ยน.
        เฟรมที่คิวเต็มจะถูกทิ้งแล้วนับไว้ (ผู้ใช้ต้องเห็นตัวเลขนี้ ไม่ใช่หายเงียบ).
        """
        if self._thread is None:
            return
        self._seen += 1
        if self.every_n > 1 and (self._seen % self.every_n) != 0:
            return                                    # เก็บทุก N เฟรม (ลดภาระดิสก์)
        if self.saved >= self.max_frames:
            self._finish("ครบจำนวนเฟรมที่ตั้งไว้ (%d)" % self.max_frames)
            return
        if self.duration_s and (time.time() - self._t0) >= self.duration_s:
            self._finish("ครบเวลาที่ตั้งไว้ (%.0f วินาที)" % self.duration_s)
            return
        try:
            self._q.put_nowait(frame)
            self._ts.append(time.time())
        except queue.Full:
            self.dropped += 1

    def _finish(self, reason):
        if self.finished_reason is None:
            self.finished_reason = reason
            logger.info("[hik] หยุดเก็บภาพชุดข้อมูล: %s", reason)
        self._stop.set()

    def _run(self):
        checked = 0
        while not self._stop.is_set():
            try:
                frame = self._q.get(timeout=0.3)
            except queue.Empty:
                continue
            if cv2 is None:
                continue
            try:
                path = os.path.join(self.dir, "%05d.jpg" % (self.saved + 1))
                _t_enc = time.time()
                ok, buf = cv2.imencode(".jpg", frame,
                                       [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
                _t_wr = time.time()
                if ok:
                    with open(path, "wb") as f:
                        f.write(buf.tobytes())
                    self.saved += 1
                    self.bytes += len(buf)
                    # เก็บเวลารายขั้นไว้ตอบว่า "ช้าที่ encode หรือช้าที่ดิสก์"
                    self._enc_ms.append((_t_wr - _t_enc) * 1000.0)
                    self._write_ms.append((time.time() - _t_wr) * 1000.0)
                    if not self._fps_series or (time.time() - self._fps_series[-1]["t"]) >= 1.0:
                        self._fps_series.append(self._snapshot())
            except Exception as e:                    # ดิสก์เต็ม/สิทธิ์ ⇒ หยุดเงียบไม่ได้
                self.error = str(e)
                logger.warning("บันทึกภาพชุดข้อมูลไม่สำเร็จ: %s", e)
                self._finish("เขียนไฟล์ไม่สำเร็จ")
                self._finish_meta()
                return
            # ⚠️ เครื่องนี้เป็นสถานีที่ใช้งานจริง — ห้ามเขียนจนดิสก์เต็ม
            #    (ที่ ROI ครึ่ง 69 fps ≈ 35 MB/วินาที เต็ม 100 GB ได้ใน ~50 นาที)
            checked += 1
            if checked % 20 == 0:
                free_mb = self._free_mb()
                if free_mb is not None and free_mb < self.min_free_mb:
                    self.error = ("พื้นที่ว่างเหลือ %d MB — หยุดบันทึกเพื่อไม่ให้ดิสก์เต็ม"
                                  % free_mb)
                    logger.warning("[hik] %s", self.error)
                    self._finish("พื้นที่ดิสก์ใกล้เต็ม")
                    self._finish_meta()
                    return
        self._finish_meta()

    def stop(self):
        self._stop.set()
        t = self._thread
        self._thread = None
        if t is not None:
            t.join(timeout=2.0)
        self._finish_meta()

    def status(self):
        elapsed = max(1e-6, time.time() - self._t0) if self._t0 else 0.0
        mb = self.bytes / (1024.0 * 1024.0)
        return {"dir": self.dir, "saved": self.saved, "dropped": self.dropped,
                "max_frames": self.max_frames, "every_n": self.every_n,
                "duration_s": self.duration_s, "error": self.error,
                "elapsed_s": round(elapsed, 1) if self._t0 else 0,
                "mb": round(mb, 1), "mb_per_s": round(mb / elapsed, 1) if elapsed else 0,
                "save_fps": round(self.saved / elapsed, 1) if elapsed else 0,
                "free_mb": self._free_mb(),
                "finished_reason": self.finished_reason,
                "active": self._thread is not None and not self._stop.is_set()}


# ════════════════════════════════════════════════════════════════════
# ⑨ HikCamera — สัญญาเดียวกับ camera.Camera
# ════════════════════════════════════════════════════════════════════
class HikCamera(object):
    """
    กล้อง Hikrobot (GigE) ที่เสียบเข้า pipeline เดิมได้ทันที.

    ``read_frame()`` คืน ``(True, frame_bgr)`` / ``(False, None)`` / ``None``
    เหมือน ``camera.Camera`` ทุกประการ — เฟรมที่คืนถูก **ย่อแล้ว** ตาม
    ``HIK_LIVE_MAX_WIDTH`` และเป็น array ของตัวเอง (ไม่ใช่ view ของบัฟเฟอร์ที่ใช้ซ้ำ)
    เพราะ ``capture_loop`` เก็บเฟรมไว้ให้ ``inference_loop`` อ่านทีหลัง.
    """

    def __init__(self, camera_index=None, params=None, live_max_width=None,
                 use_saved_settings=True, stream=True, apply_on_open=True):
        self.camera_index = camera_index or source_prefix()
        self.width = None                 # เติมจริงหลังเปิด (ไว้ให้โค้ดเดิมที่อ่านค่า)
        self.height = None
        self.fps = None
        self.is_initialized = False
        self.last_error = None
        self.identity = {}

        self._mv = None
        self._h = None                    # MvCamera instance
        self._io = None
        self._lock = threading.RLock()    # SDK ใช้ handle เดียว ⇒ ต้อง serialize ทุกการเรียก
        self._grabbing = False
        self._stream = bool(stream)       # False = เปิดเพื่ออ่านค่าอย่างเดียว (ไม่สตรีม)
        # False = "ห้ามเขียนอะไรลงกล้องเลย" — ใช้ตอนอ่านค่ามาแสดงบนหน้าเว็บ.
        # ถ้าเผลอเขียน ค่าที่ช่างตั้งไว้ใน MVS จะถูกทับเงียบ ๆ แค่เพราะผู้ใช้เปิดแท็บดู.
        self._apply_on_open = bool(apply_on_open)
        self._dev_list_keepalive = None

        # ค่าที่จะใช้ตอนเปิด เรียงจากอ่อนไปแก่:
        #   config.HIK_DEFAULTS → ไฟล์ที่ผู้ใช้บันทึก → ค่าที่ส่งมาตอนสร้าง
        self._requested = dict(_cfg("HIK_DEFAULTS") or {})
        if use_saved_settings:
            self._requested.update(load_settings())
        if params:
            self._requested.update(params)

        self._live_max_width = int(live_max_width if live_max_width is not None
                                   else _cfg("HIK_LIVE_MAX_WIDTH", 1280) or 0)
        self._timeout_ms = int(_cfg("HIK_GRAB_TIMEOUT_MS", 500))

        # บัฟเฟอร์แปลงสี ใช้ซ้ำ (เฟรม 5MP = 15MB — จองใหม่ทุกเฟรมคือการเผา CPU/หน่วยความจำ)
        self._dst = None
        self._dst_size = 0
        self._conv = None                 # (struct_cls, ชื่อฟังก์ชัน) ที่จับคู่กันแล้ว
        self._pixnames = {}

        # สถิติ
        self._t_start = 0.0
        self._frames = 0
        self._timeouts = 0
        self._dropped = 0
        self._prev_num = None
        self._fps_ema = None
        self._t_last = None
        self._mean = None
        self._clip_pct = None
        self._stat_every = max(1, int(_cfg("HIK_STATS_SAMPLE_EVERY", 10)))
        self._consec_fail = 0
        self._reconnects = 0
        self._t_last_reconnect = 0.0

        # ถ่ายเฟรมเต็มความละเอียด (ใช้กับปุ่ม "ถ่าย 1 เฟรม")
        self._want_full = False
        self._full_frame = None
        self._full_evt = threading.Event()

        self._dataset = None

    # ── ①ⓐ เปิด/ปิด ────────────────────────────────────────
    def initialize(self):
        if self.is_initialized:
            self.release()
        ok = self._open()
        self.is_initialized = ok
        return ok

    def _open(self):
        mv, sdk_info = load_sdk()
        if mv is None:
            self.last_error = "ไม่พบ MVS SDK — " + _INSTALL_HINT
            logger.error("[hik] %s", self.last_error)
            return False
        self._mv = mv
        _ensure_initialized(mv)

        try:
            lst, entries, err = _enum_raw(mv)
        except Exception as e:
            self.last_error = "enumerate กล้องล้มเหลว: %s" % e
            logger.error("[hik] %s", self.last_error)
            return False
        if err or not entries:
            self.last_error = ("ไม่พบกล้อง Hikrobot (%s) — ตรวจสายแลน/ไฟเลี้ยง และว่ากล้อง "
                               "อยู่วง IP เดียวกับการ์ดแลน" % (err or "0 อุปกรณ์"))
            logger.error("[hik] %s", self.last_error)
            return False
        self._dev_list_keepalive = lst          # ต้องถือไว้ ไม่งั้น struct ถูกคืนหน่วยความจำ

        kind, want = parse_source(self.camera_index)
        target = None
        if kind == "serial":
            for d in entries:
                if d.get("serial") == want:
                    target = d
                    break
            if target is None:
                have = ", ".join(x.get("serial") or "?" for x in entries)
                self.last_error = ("ไม่พบกล้องซีเรียล '%s' (ที่เจอ: %s)" % (want, have))
                logger.error("[hik] %s", self.last_error)
                return False
        elif kind == "index":
            for d in entries:
                if d["index"] == want:
                    target = d
                    break
            if target is None:
                self.last_error = "ไม่พบกล้องลำดับที่ %s (เจอ %d ตัว)" % (want, len(entries))
                return False
        else:
            target = entries[0]

        if target.get("accessible") is False:
            self.last_error = ("กล้องถูกโปรแกรมอื่นจองอยู่ — **ปิดโปรแกรม MVS ก่อน** "
                               "(GigE เปิดได้ทีละโปรแกรม)")
            logger.error("[hik] %s", self.last_error)
            return False

        handle = mv.MvCamera()
        ret = handle.MV_CC_CreateHandle(target["_info"])
        if ret != 0:
            self.last_error = "สร้าง handle ไม่สำเร็จ (0x%X)" % (ret & 0xFFFFFFFF)
            return False
        ret = handle.MV_CC_OpenDevice(getattr(mv, "MV_ACCESS_Exclusive", 1), 0)
        if ret != 0:
            try:
                handle.MV_CC_DestroyHandle()
            except Exception:
                pass
            self.last_error = ("เปิดกล้องไม่สำเร็จ (0x%X) — สาเหตุที่พบบ่อยที่สุดคือ "
                               "**โปรแกรม MVS เปิดค้างอยู่**; รองลงมาคือกล้องคนละวง IP "
                               "กับการ์ดแลน หรือถูกเครื่องอื่นจองไว้" % (ret & 0xFFFFFFFF))
            logger.error("[hik] %s", self.last_error)
            return False

        self._h = handle
        self._io = _NodeIO(mv, handle, self._lock)
        self._pixnames = pixel_names(mv)
        self._pick_converter()
        self._read_identity(target)
        if self._apply_on_open:
            if self._stream:
                self._apply_network()
            applied = self._apply_params(self._requested, force_stopped=True)
            if applied.get("failed"):
                logger.warning("[hik] ตั้งค่าบางตัวไม่สำเร็จ: %s", applied["failed"])

        if not self._stream:
            # โหมด "อ่านค่าอย่างเดียว" — ใช้ตอนหน้าเว็บขอช่วง min/max ก่อนกด Start
            self.last_error = None
            logger.info("[hik] เปิดเพื่ออ่านค่าเท่านั้น: %s SN=%s",
                        self.identity.get("model", "?"), self.identity.get("serial", "?"))
            return True

        if not self._start_grabbing():
            self.release()
            return False

        # วอร์มอัพ: ต้องได้ภาพจริงอย่างน้อย 1 เฟรมก่อนบอกว่า "เปิดสำเร็จ"
        # (ไม่งั้นผู้ใช้กด Start แล้วเห็นจอว่างโดยไม่มีคำอธิบาย)
        trig = self._io.get_enum("TriggerMode", _ONOFF_MAP) or {}
        triggered = trig.get("symbolic") == "On"
        if not triggered:
            got = False
            for _ in range(int(_cfg("HIK_OPEN_WARMUP_FRAMES", 5))):
                res = self._grab_once()
                if res is not None:
                    got = True
                    break
            if not got:
                self.last_error = ("เปิดกล้องได้แต่ไม่มีภาพเข้ามาภายในเวลาที่รอ — "
                                   "ตรวจ packet size/Jumbo Frame ของการ์ดแลน "
                                   "และไฟร์วอลล์ของ Windows")
                logger.error("[hik] %s", self.last_error)
                self.release()
                return False
        else:
            logger.info("[hik] TriggerMode = On — ข้ามการวอร์มอัพ (รอสัญญาณทริกเกอร์)")

        self._t_start = time.time()
        self.last_error = None
        logger.info("[hik] พร้อมใช้งาน: %s SN=%s %sx%s (ส่งเข้าระบบที่กว้างสุด %s px)",
                    self.identity.get("model", "?"), self.identity.get("serial", "?"),
                    self.width, self.height, self._live_max_width or "ไม่ย่อ")
        return True

    def _pick_converter(self):
        """
        เลือกคู่ (struct, ฟังก์ชัน) ของตัวแปลงพิกเซล — **ต้องจับคู่กัน** (EX ↔ Ex)
        เพราะขนาด field ต่างกัน; ผิดคู่ = ภาพเพี้ยนแบบไม่มี error.
        """
        mv = self._mv
        for st_name, fn_name in (("MV_CC_PIXEL_CONVERT_PARAM", "MV_CC_ConvertPixelType"),
                                 ("MV_CC_PIXEL_CONVERT_PARAM_EX", "MV_CC_ConvertPixelTypeEx")):
            st = getattr(mv, st_name, None)
            if st is not None and hasattr(self._h, fn_name):
                self._conv = (st, fn_name)
                return
        self._conv = None

    def _read_identity(self, entry):
        """
        ตัวตนกล้อง: เอาจาก **node ของกล้อง** เป็นหลัก แล้วค่อยเติมจาก struct ของ SDK.
        (บนสถานี struct อ่านได้ไม่ครบทุกรุ่น — ดูหัวข้อบทเรียนด้านบนของไฟล์)
        """
        io = self._io
        ident = {
            "model": io.get_str("DeviceModelName") or entry.get("model") or "",
            "serial": io.get_str("DeviceSerialNumber") or entry.get("serial") or "",
            "firmware": (io.get_str("DeviceFirmwareVersion")
                         or io.get_str("DeviceVersion") or entry.get("version") or ""),
            "vendor": io.get_str("DeviceManufacturerName") or "",
            "user_name": io.get_str("DeviceUserID") or entry.get("user_name") or "",
            "kind": entry.get("kind", ""),
            "mac": entry.get("mac", ""),
            "nic": entry.get("nic", ""),
        }
        node_ip = io.get_int("GevCurrentIPAddress")
        ident["ip"] = ip_str(node_ip["value"]) if node_ip else entry.get("ip", "")
        self.identity = ident

    def _apply_network(self):
        """
        ⚠️ หัวใจของความถูกต้องบน GigE: **กล้องไม่จำ packet size/delay**
        เปิดใหม่ทีไรกลับเป็น 1500/400 เสมอ ⇒ 5MP จะได้ 15-17 fps และ
        **เฟรมหาย 2-3 ใน 60 แบบเงียบ** (วัดจริงบนสถานี 19 ส.ค. 2026).
        """
        io = self._io
        want = _cfg("HIK_PACKET_SIZE", "auto")
        if want is None:
            return
        size = None
        if want == "auto":
            try:
                with self._lock:
                    v = self._h.MV_CC_GetOptimalPacketSize()
                size = int(v) if v and int(v) > 0 else None
            except Exception as e:
                logger.warning("[hik] อ่าน optimal packet size ไม่ได้: %s", e)
        else:
            try:
                size = int(want)
            except (TypeError, ValueError):
                size = None
        if size:
            ok, _ = io.set_int("GevSCPSPacketSize", size)
            now = io.get_int("GevSCPSPacketSize")
            actual = now["value"] if now else None
            if ok and actual is not None and actual < size:
                logger.warning("[hik] ตั้ง packet size ได้แค่ %d (ขอ %d) — การ์ดแลนน่าจะยัง"
                               "ไม่ได้เปิด Jumbo Frame ⇒ เสี่ยงภาพแหว่ง", actual, size)
            else:
                logger.info("[hik] packet size = %s", actual)
        delay = _cfg("HIK_PACKET_DELAY", 0)
        if delay is not None:
            io.set_int("GevSCPD", int(delay))
        hb = _cfg("HIK_HEARTBEAT_MS")
        if hb:
            io.set_int("GevHeartbeatTimeout", int(hb))

    def _start_grabbing(self):
        try:
            with self._lock:
                ret = self._h.MV_CC_StartGrabbing()
        except Exception as e:
            self.last_error = "เริ่มสตรีมภาพไม่สำเร็จ: %s" % e
            return False
        if ret != 0:
            self.last_error = "MV_CC_StartGrabbing ล้มเหลว (0x%X)" % (ret & 0xFFFFFFFF)
            logger.error("[hik] %s", self.last_error)
            return False
        self._grabbing = True
        return True

    def _stop_grabbing(self):
        if not self._grabbing:
            return
        try:
            with self._lock:
                self._h.MV_CC_StopGrabbing()
        except Exception:
            pass
        self._grabbing = False

    def release(self):
        """
        ปิดกล้องและคืนทรัพยากร.

        ⚠️ **ต้องถือ lock ตลอดการปิด**: ถ้าเธรดอื่น (คำขอจากหน้าเว็บ) กำลังเรียก
        SDK ด้วย handle เดียวกันอยู่ แล้วเราไป ``MV_CC_DestroyHandle`` พร้อมกัน
        จะเป็นการใช้ pointer ที่ถูกทำลายแล้วในโค้ด C — ล้มทั้งโปรเซส ไม่ใช่แค่ exception
        ของ Python. ยอมรอไม่เกิน 1 รอบการอ่านเฟรม (HIK_GRAB_TIMEOUT_MS) ดีกว่ามาก.
        """
        with self._lock:
            self._release_locked()

    def _release_locked(self):
        self._stop_grabbing()
        if self._dataset is not None:
            self._dataset.stop()
            self._dataset = None
        h = self._h
        self._h = None
        self._io = None
        self.is_initialized = False
        if h is not None:
            try:
                h.MV_CC_CloseDevice()
            except Exception:
                pass
            try:
                h.MV_CC_DestroyHandle()
            except Exception:
                pass
            logger.info("[hik] ปล่อยกล้องแล้ว")
        self._dev_list_keepalive = None
        self._dst = None
        self._dst_size = 0

    def __del__(self):                                # pragma: no cover
        try:
            self.release()
        except Exception:
            pass

    # ── ①ⓑ จับภาพ ─────────────────────────────────────────
    def _to_bgr_view(self, out):
        """
        คืน numpy **view** (ยังไม่คัดลอก) ของเฟรมในรูป BGR.
        ⚠️ view นี้ชี้เข้าบัฟเฟอร์ของ SDK หรือบัฟเฟอร์ที่ใช้ซ้ำ ⇒ ผู้เรียกต้องคัดลอก
        ก่อน ``MV_CC_FreeImageBuffer`` และก่อนเฟรมถัดไปเสมอ.
        """
        mv = self._mv
        fi = out.stFrameInfo
        w, h = int(fi.nWidth), int(fi.nHeight)
        if w <= 0 or h <= 0:
            return None
        pt = int(fi.enPixelType)
        bgr8 = getattr(mv, "PixelType_Gvsp_BGR8_Packed", None)
        mono8 = getattr(mv, "PixelType_Gvsp_Mono8", None)

        try:
            if bgr8 is not None and pt == bgr8:
                n = w * h * 3
                buf = ctypes.cast(out.pBufAddr,
                                  ctypes.POINTER(ctypes.c_ubyte * n)).contents
                return np.frombuffer(buf, dtype=np.uint8, count=n).reshape(h, w, 3)
            if mono8 is not None and pt == mono8:
                n = w * h
                buf = ctypes.cast(out.pBufAddr,
                                  ctypes.POINTER(ctypes.c_ubyte * n)).contents
                gray = np.frombuffer(buf, dtype=np.uint8, count=n).reshape(h, w)
                if cv2 is None:
                    return None
                # โมเดลเทรนจากภาพสี ⇒ ต้องเป็น 3 ช่องเสมอ (คืนอาร์เรย์ใหม่อยู่แล้ว)
                return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        except Exception as e:
            logger.warning("[hik] อ่านบัฟเฟอร์ภาพไม่สำเร็จ: %s", e)
            return None

        # ที่เหลือ (Bayer*, YUV, RGB) → ให้ตัวแปลงของ SDK จัดการ
        if self._conv is None:
            return None
        st_cls, fn_name = self._conv
        need = w * h * 3
        if self._dst is None or self._dst_size < need:
            self._dst = (ctypes.c_ubyte * need)()
            self._dst_size = need
        try:
            prm = st_cls()
            ctypes.memset(ctypes.byref(prm), 0, ctypes.sizeof(prm))
            prm.nWidth, prm.nHeight = w, h
            prm.pSrcData = out.pBufAddr
            prm.nSrcDataLen = int(fi.nFrameLen)
            prm.enSrcPixelType = fi.enPixelType
            prm.enDstPixelType = getattr(mv, "PixelType_Gvsp_BGR8_Packed")
            prm.pDstBuffer = ctypes.cast(self._dst, ctypes.POINTER(ctypes.c_ubyte))
            prm.nDstBufferSize = self._dst_size
            if getattr(self._h, fn_name)(prm) != 0:
                return None
            return np.frombuffer(self._dst, dtype=np.uint8, count=need).reshape(h, w, 3)
        except Exception as e:
            logger.warning("[hik] แปลงสีไม่สำเร็จ: %s", e)
            return None

    def _post(self, view):
        """
        จาก view → เฟรมที่ส่งเข้า pipeline (คัดลอกเสมอ) + งานฝากอื่น ๆ
        (เก็บชุดข้อมูล / ถ่ายเฟรมเต็มความละเอียด).
        """
        # ชุดข้อมูลต้องเป็นความละเอียดเต็ม ไม่ใช่ภาพที่ย่อแล้ว (ย่อแล้วเทรนไม่ได้)
        if self._dataset is not None:
            self._dataset.put(view.copy())
        if self._want_full:
            self._full_frame = view.copy()
            self._want_full = False
            self._full_evt.set()

        h, w = view.shape[:2]
        maxw = self._live_max_width
        if maxw and w > maxw and cv2 is not None:
            # INTER_AREA คือฟิลเตอร์ที่ถูกต้องสำหรับการย่อ — ลด moiré บนลายวงแหวน
            # ของฝากระป๋อง (เหตุผลเดียวกับ _scale_for_display ใน app.py)
            scale = maxw / float(w)
            return cv2.resize(view, (maxw, max(1, int(round(h * scale)))),
                              interpolation=cv2.INTER_AREA)
        return view.copy()

    def _grab_once(self):
        """จับ 1 เฟรม → คืนเฟรมพร้อมใช้ หรือ None เมื่อไม่มีภาพ (ไม่โยนออกไป)."""
        mv, h = self._mv, self._h
        frame_cls = getattr(mv, "MV_FRAME_OUT", None) if mv else None
        if frame_cls is None or h is None:
            return None
        out = frame_cls()
        ctypes.memset(ctypes.byref(out), 0, ctypes.sizeof(out))
        frame = None
        num = None
        with self._lock:
            if self._h is None:                       # ถูก release ระหว่างรอ lock
                return None
            try:
                ret = h.MV_CC_GetImageBuffer(out, self._timeout_ms)
            except Exception as e:
                logger.warning("[hik] MV_CC_GetImageBuffer โยน: %s", e)
                self._timeouts += 1
                return None
            if ret != 0:
                self._timeouts += 1
                return None
            try:
                num = int(getattr(out.stFrameInfo, "nFrameNum", 0) or 0)
                view = self._to_bgr_view(out)
                if view is not None:
                    self.width = int(out.stFrameInfo.nWidth)
                    self.height = int(out.stFrameInfo.nHeight)
                    frame = self._post(view)          # คัดลอกออกจากบัฟเฟอร์ที่ใช้ซ้ำ
            finally:
                try:
                    h.MV_CC_FreeImageBuffer(out)
                except Exception:
                    pass

        if frame is None:
            return None

        # ── สถิติ (นอก lock) ──
        now = time.time()
        if self._prev_num is not None and num is not None and num > self._prev_num + 1:
            self._dropped += (num - self._prev_num - 1)
        self._prev_num = num
        self._frames += 1
        if self._t_last is not None:
            dt = now - self._t_last
            if dt > 0:
                inst = 1.0 / dt
                self._fps_ema = inst if self._fps_ema is None else \
                    (0.85 * self._fps_ema + 0.15 * inst)
        self._t_last = now
        if self._frames % self._stat_every == 0 and cv2 is not None:
            try:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                self._mean = float(gray.mean())
                self._clip_pct = float((gray >= 250).mean() * 100.0)
            except Exception:
                pass
        return frame

    def read_frame(self):
        """สัญญาเดียวกับ camera.Camera.read_frame()."""
        if not self.is_initialized:
            return None
        frame = self._grab_once()
        if frame is None:
            self._consec_fail += 1
            self._maybe_reconnect()
            return (False, None)
        self._consec_fail = 0
        return (True, frame)

    def _maybe_reconnect(self):
        """
        กล้อง GigE หลุดได้จริง (สายหลวม/heartbeat หมดเวลา). พยายามต่อใหม่แบบ
        **มีเพดานและมีคาบเวลา** — ไม่ใช่วนต่อรัว ๆ ซึ่งจะกลบ log และกินซีพียู.
        """
        if not _cfg("HIK_RECONNECT_ENABLED", True):
            return
        need = int(_cfg("HIK_RECONNECT_AFTER_FAILS", 40))
        if self._consec_fail < need:
            return
        now = time.time()
        if now - self._t_last_reconnect < float(_cfg("HIK_RECONNECT_COOLDOWN_S", 5.0)):
            return
        self._t_last_reconnect = now
        self._reconnects += 1
        logger.warning("[hik] ไม่มีภาพติดต่อกัน %d ครั้ง — พยายามเชื่อมต่อกล้องใหม่ (ครั้งที่ %d)",
                       self._consec_fail, self._reconnects)
        keep = dict(self._requested)
        if self._dataset is not None:
            logger.warning("[hik] การเก็บภาพชุดข้อมูลถูกหยุดเพราะต้องเชื่อมต่อกล้องใหม่ "
                           "(บันทึกไปแล้ว %d ภาพ) — ต้องกดเปิดใหม่เองถ้ายังต้องการเก็บต่อ",
                           self._dataset.saved)
        try:
            self.release()
        except Exception:
            pass
        self._requested = keep
        if self._open():
            self.is_initialized = True
            self._consec_fail = 0
            logger.info("[hik] เชื่อมต่อกล้องใหม่สำเร็จ")
        else:
            logger.error("[hik] เชื่อมต่อใหม่ไม่สำเร็จ: %s", self.last_error)

    # ── ①ⓒ ถ่ายเฟรมเต็มความละเอียด (ปุ่ม "ถ่าย 1 เฟรม") ──────
    def snap_full(self, timeout=3.0):
        """
        คืนเฟรม **ความละเอียดเต็ม** (ไม่ย่อ) เฟรมถัดไปที่กล้องส่งมา.
        ใช้กับการตรวจที่ imgsz สูง ซึ่งต้องการรายละเอียดจริง ไม่ใช่ภาพที่ย่อแล้ว.
        คืน ``None`` เมื่อไม่มีภาพภายในเวลาที่รอ (ห้ามคืนภาพเก่า — เป็นระบบ QC).
        """
        if not self.is_initialized:
            return None
        self._full_frame = None
        self._full_evt.clear()
        self._want_full = True
        if self._full_evt.wait(timeout):
            frame = self._full_frame
            self._full_frame = None
            return frame
        self._want_full = False
        return None

    # ── ①ⓓ ค่าพารามิเตอร์ ─────────────────────────────────
    def _read_node(self, spec):
        io = self._io
        node = spec["node"]
        if spec["type"] == "float":
            return io.get_float(node)
        if spec["type"] == "int":
            return io.get_int(node)
        if spec["type"] == "bool":
            return io.get_bool(node)
        if spec["type"] == "enum":
            return io.get_enum(node, spec.get("map"))
        if spec["type"] == "enum_pixel":
            raw = io.get_enum(node)
            if raw is None:
                return None
            raw["symbolic"] = self._pixnames.get(raw["value"], str(raw["value"]))
            raw["options"] = [self._pixnames.get(v, str(v)) for v in raw.get("choices", [])]
            return raw
        return None

    def get_params(self):
        """
        ค่าปัจจุบัน + **ช่วงที่ตั้งได้จริง** ของทุก knob (ให้ UI สร้างสไลเดอร์ได้ตรงกับกล้อง).
        node ที่เฟิร์มแวร์ไม่เปิดให้ (เช่น binning/gamma บนกล้องตัวนี้) จะมี
        ``supported: false`` — UI ต้องซ่อน ไม่ใช่แสดงปุ่มที่กดแล้วไม่มีอะไรเกิดขึ้น.

        ⚠️ **ถือ lock ทั้งชุด** โดยตั้งใจ: ถ้าปล่อยให้อ่านทีละ node เธรดจับภาพจะแทรก
        คิวได้ทุกครั้ง แล้วการอ่าน ~22 ค่าจะใช้เวลาถึง 22 × เวลารอเฟรม (เลวร้ายสุด
        หลายวินาที เช่นตอนอยู่ในโหมดทริกเกอร์ที่ยังไม่มีสัญญาณเข้ามา) = หน้าเว็บค้าง.
        lock เป็น RLock จึงเรียกซ้อนได้.
        """
        with self._lock:
            # ⚠️ ต้องเช็ค _io **ในล็อก** ไม่ใช่ก่อนหน้า: release() ถือล็อกแล้วตั้ง _io = None
            #    ถ้าเช็คนอกล็อกจะมีช่องให้ผ่านด่านตอนกล้องยังอยู่ แล้วพอได้ล็อกมากล้องถูกปิดไปแล้ว
            #    → AttributeError ('NoneType' has no attribute ...) ตอนผู้ใช้กด Stop พอดี
            #    กับจังหวะที่แผงตั้งค่ากำลังอ่านค่าอยู่ (เทสต์ concurrency จับเคสนี้ได้จริง)
            if self._io is None:
                return {}
            return self._get_params_locked()

    def _get_params_locked(self):
        out = {}
        for spec in PARAM_SPECS:
            info = self._read_node(spec)
            entry = {"label": spec["label"], "type": spec["type"], "live": spec["live"]}
            if info:
                entry.update(info)
            entry["supported"] = info is not None      # ตั้งทีหลังเสมอ = boolean แน่นอน
            out[spec["key"]] = entry
        # ค่าอ่านอย่างเดียวที่ผู้ใช้ต้องเห็นเพื่อจูน
        rf = self._io.get_float("ResultingFrameRate")
        if rf:
            out["resulting_framerate"] = {"label": "อัตราเฟรมที่กล้องคำนวณได้",
                                          "type": "float", "live": False,
                                          "supported": True, "readonly": True,
                                          "value": rf["value"]}
        for node, key, label in (("WidthMax", "width_max", "ความกว้างสูงสุด"),
                                 ("HeightMax", "height_max", "ความสูงสูงสุด")):
            v = self._io.get_int(node)
            if v:
                out[key] = {"label": label, "type": "int", "live": False,
                            "supported": True, "readonly": True, "value": v["value"]}
        return out

    @staticmethod
    def _align(value, lo, hi, inc):
        """ปัดค่าให้ตรงกับ increment ของ node แล้ว clamp — กล้องจะปฏิเสธค่าที่ไม่ตรง grid."""
        v = int(value)
        if inc and inc > 1 and lo is not None:
            v = lo + ((v - lo) // inc) * inc
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        return v

    def _set_one(self, key, value):
        """คืน (ok, ข้อความ, ค่าที่ตั้งได้จริง)."""
        spec = _SPEC_BY_KEY.get(key)
        if spec is None:
            return False, "ไม่รู้จักค่า '%s'" % key, None
        io, node = self._io, spec["node"]
        info = self._read_node(spec)
        if info is None:
            return False, "กล้อง/เฟิร์มแวร์นี้ไม่เปิดให้ตั้ง '%s'" % key, None

        if spec["type"] == "float":
            try:
                v = float(value)
            except (TypeError, ValueError):
                return False, "ต้องเป็นตัวเลข", None
            lo, hi = info.get("min"), info.get("max")
            clamped = min(max(v, lo), hi) if (lo is not None and hi is not None) else v
            ok, ret = io.set_float(node, clamped)
            after = self._read_node(spec) or {}
            msg = "" if ok else "ตั้งไม่สำเร็จ (0x%X)" % (ret & 0xFFFFFFFF if ret > 0 else 0)
            if ok and abs(clamped - v) > 1e-6:
                msg = "ปรับให้อยู่ในช่วงที่กล้องรับ (%s–%s)" % (lo, hi)
            return ok, msg, after.get("value")

        if spec["type"] == "int":
            try:
                v = int(value)
            except (TypeError, ValueError):
                return False, "ต้องเป็นจำนวนเต็ม", None
            aligned = self._align(v, info.get("min"), info.get("max"), info.get("inc", 1))
            ok, ret = io.set_int(node, aligned)
            after = self._read_node(spec) or {}
            msg = "" if ok else "ตั้งไม่สำเร็จ (0x%X)" % (ret & 0xFFFFFFFF if ret > 0 else 0)
            if ok and aligned != v:
                msg = "ปัดให้ตรงกับขั้นที่กล้องรับ (ขั้นละ %s)" % info.get("inc", 1)
            return ok, msg, after.get("value")

        if spec["type"] == "bool":
            ok, ret = io.set_bool(node, bool(value))
            after = self._read_node(spec) or {}
            return ok, "" if ok else "ตั้งไม่สำเร็จ", after.get("value")

        if spec["type"] in ("enum", "enum_pixel"):
            num = None
            if isinstance(value, bool):
                return False, "ต้องเป็นชื่อหรือหมายเลขของตัวเลือก", None
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                num = int(value)
            else:
                name = str(value).strip()
                if spec["type"] == "enum_pixel":
                    for val, nm in self._pixnames.items():
                        if nm.lower() == name.lower():
                            num = val
                            break
                else:
                    num = (spec.get("map") or {}).get(name)
                    if num is None:
                        for v in info.get("choices", []):
                            if (io._symbolic(node, v) or "").lower() == name.lower():
                                num = v
                                break
            if num is None:
                return False, "ตัวเลือก '%s' ไม่รู้จัก" % value, None
            choices = info.get("choices") or []
            if choices and num not in choices:
                return False, "กล้องไม่รองรับตัวเลือกนี้", None
            ok, ret = io.set_enum(node, num)
            after = self._read_node(spec) or {}
            return ok, "" if ok else "ตั้งไม่สำเร็จ", after.get("symbolic", after.get("value"))

        return False, "ชนิดค่าไม่รองรับ", None

    def _apply_roi(self, roi):
        """
        ตั้ง ROI เป็น "ชุด" ตามลำดับที่ถูกต้อง: เคลียร์ offset → ตั้งขนาด → ตั้ง offset.
        ถ้าตั้ง Width ก่อนโดยไม่เคลียร์ offset กล้องจะปฏิเสธเมื่อ offset+width เกินเซนเซอร์
        (อาการ: "ตั้งไม่ได้" ทั้งที่ค่าที่ขอถูกต้อง).
        """
        res = {}
        io = self._io
        io.set_int("OffsetX", 0)
        io.set_int("OffsetY", 0)
        for key in ("width", "height"):
            if key in roi:
                ok, msg, val = self._set_one(key, roi[key])
                res[key] = {"ok": ok, "message": msg, "value": val}
        if roi.get("center"):
            wmax = (io.get_int("WidthMax") or {}).get("value")
            hmax = (io.get_int("HeightMax") or {}).get("value")
            w = (io.get_int("Width") or {}).get("value")
            h = (io.get_int("Height") or {}).get("value")
            if None not in (wmax, hmax, w, h):
                roi = dict(roi)
                roi["offset_x"] = (wmax - w) // 2
                roi["offset_y"] = (hmax - h) // 2
        for key in ("offset_x", "offset_y"):
            if key in roi:
                ok, msg, val = self._set_one(key, roi[key])
                res[key] = {"ok": ok, "message": msg, "value": val}
        return res

    def _apply_params(self, params, force_stopped=False):
        """
        ตั้งค่าหลายตัวพร้อมกันอย่างปลอดภัย: ค่าที่ GenICam ล็อกระหว่างสตรีมจะถูกตั้ง
        ในช่วง Stop→Start ให้อัตโนมัติ (และเริ่มสตรีมกลับมาเสมอแม้มีตัวใดล้มเหลว).
        """
        result = {"applied": {}, "failed": {}, "restarted": False}
        if not params:
            return result
        with self._lock:                              # เหตุผลเดียวกับ get_params()
            if self._io is None:                      # ถูกปิดไประหว่างรอล็อก
                result["failed"] = {k: {"value": None, "message": "กล้องถูกปิดไปแล้ว"}
                                    for k in params}
                return result
            return self._apply_params_locked(params, force_stopped, result)

    def _apply_params_locked(self, params, force_stopped, result):
        roi = {k: params[k] for k in _ROI_KEYS if k in params}
        if params.get("roi_center"):
            roi["center"] = True
        others = [(k, v) for k, v in params.items()
                  if k not in _ROI_KEYS and k != "roi_center" and k in _SPEC_BY_KEY]

        need_stop = bool(roi) or any(not _SPEC_BY_KEY[k]["live"] for k, _ in others)
        was_grabbing = self._grabbing
        if need_stop and was_grabbing and not force_stopped:
            self._stop_grabbing()
            result["restarted"] = True

        try:
            # ตั้งค่าที่เปลี่ยน "รูปทรง/ฟอร์แมต" ก่อน แล้วค่อยค่าที่เหลือ —
            # เพราะช่วงที่ตั้งได้ของ exposure/framerate ขึ้นกับขนาดภาพที่เลือก
            for key in ("pixel_format", "reverse_x", "reverse_y"):
                for k, v in others:
                    if k == key:
                        ok, msg, val = self._set_one(k, v)
                        (result["applied"] if ok else result["failed"])[k] = \
                            {"value": val, "message": msg}
            if roi:
                for k, r in self._apply_roi(roi).items():
                    (result["applied"] if r["ok"] else result["failed"])[k] = \
                        {"value": r["value"], "message": r["message"]}
            for k, v in others:
                if k in ("pixel_format", "reverse_x", "reverse_y"):
                    continue
                ok, msg, val = self._set_one(k, v)
                (result["applied"] if ok else result["failed"])[k] = \
                    {"value": val, "message": msg}
        finally:
            if result["restarted"]:
                self._start_grabbing()
        return result

    def set_params(self, params):
        """ตั้งค่าจากหน้าเว็บ — จำค่าไว้ใช้ตอนเปิดกล้องครั้งต่อไปด้วย."""
        res = self._apply_params(params or {})
        for k, v in (params or {}).items():
            if k in _SPEC_BY_KEY or k == "roi_center":
                self._requested[k] = v
        return res

    # ── ①ⓔ สถิติ / ชุดข้อมูล ────────────────────────────────
    def net_stats(self):
        """แพ็กเก็ต/เฟรมที่หายในระดับเครือข่าย (GigE เท่านั้น) — 0 เท่านั้นที่ยอมรับได้."""
        mv = self._mv
        if mv is None or self._h is None:
            return None
        try:
            all_cls = getattr(mv, "MV_ALL_MATCH_INFO", None)
            det_cls = getattr(mv, "MV_MATCH_INFO_NET_DETECT", None)
            if all_cls is None or det_cls is None:
                return None
            det = det_cls()
            ctypes.memset(ctypes.byref(det), 0, ctypes.sizeof(det))
            info = all_cls()
            info.nType = getattr(mv, "MV_MATCH_TYPE_NET_DETECT", 0x00000001)
            info.pInfo = ctypes.cast(ctypes.byref(det), ctypes.c_void_p)
            info.nInfoSize = ctypes.sizeof(det)
            with self._lock:
                if self._h is None or self._h.MV_CC_GetGevAllMatchInfo(info) != 0:
                    return None
            return {"recv_frames": int(getattr(det, "nNetRecvFrameCount", 0)),
                    "lost_packets": int(getattr(det, "nLostPacketCount", 0)),
                    "lost_frames": int(getattr(det, "nLostFrameCount", 0))}
        except Exception:
            return None

    def stats(self):
        """ตัวเลขสำหรับแถบสถานะบนหน้าเว็บ — ทุกตัวคือของจริงที่วัดได้ ไม่ใช่ค่าที่ตั้งไว้."""
        elapsed = max(1e-6, time.time() - self._t_start) if self._t_start else 0.0
        st = {
            "frames": self._frames,
            "fps": round(self._fps_ema, 2) if self._fps_ema else None,
            "fps_avg": round(self._frames / elapsed, 2) if elapsed > 0 else None,
            "timeouts": self._timeouts,
            "dropped": self._dropped,
            "reconnects": self._reconnects,
            "mean_brightness": round(self._mean, 1) if self._mean is not None else None,
            "clip_pct": round(self._clip_pct, 2) if self._clip_pct is not None else None,
            "size": ("%dx%d" % (self.width, self.height)) if self.width else None,
            "sent_width": self._live_max_width or None,
            "last_error": self.last_error,
        }
        net = self.net_stats()
        if net:
            st.update(lost_packets=net["lost_packets"], lost_frames=net["lost_frames"])
        if self._dataset is not None:
            st["dataset"] = self._dataset.status()
        return st

    def start_dataset(self, root=None, max_frames=None, every_n=1, duration_s=0,
                      jpeg_quality=None, meta=None):
        if self._dataset is not None:
            return self._dataset.status()
        root = root or _cfg("HIK_DATASET_DIR") or os.path.join("data", "hik_dataset")
        if not os.path.isabs(root):
            base = _cfg("BASE_DIR") or os.path.dirname(os.path.abspath(__file__))
            root = os.path.join(base, root)
        w = _DatasetWriter(root,
                           max_frames=max_frames or _cfg("HIK_DATASET_MAX_FRAMES", 2000),
                           jpeg_quality=jpeg_quality or _cfg("HIK_DATASET_JPEG_QUALITY", 95),
                           every_n=every_n, duration_s=duration_s,
                           min_free_mb=_cfg("HIK_DATASET_MIN_FREE_MB", 2048),
                           meta=meta,
                           # อ่านตัวแปรของตัวเองล้วน ๆ — ไม่แตะ SDK ไม่ต้องรอ lock
                           # จึงเรียกถี่ได้โดยไม่ทำให้การจับภาพช้าลง
                           counters_cb=lambda: {
                               "cam_frames": self._frames,
                               "cam_dropped": self._dropped,
                               "cam_timeouts": self._timeouts,
                               "cam_fps": round(self._fps_ema, 2) if self._fps_ema else None,
                           },
                           net_cb=self.net_stats)
        if not w.start():
            return w.status()
        self._dataset = w
        logger.info("[hik] เริ่มเก็บภาพชุดข้อมูลที่ %s", w.dir)
        return w.status()

    def stop_dataset(self):
        if self._dataset is None:
            return None
        status = self._dataset.status()
        self._dataset.stop()
        self._dataset = None
        logger.info("[hik] หยุดเก็บภาพชุดข้อมูล (บันทึก %d ทิ้ง %d)",
                    status.get("saved", 0), status.get("dropped", 0))
        status["active"] = False
        return status

    def describe(self):
        """ข้อมูลครบชุดสำหรับหน้าเว็บ (อ่านในล็อกเดียว = ค่าที่รายงานเป็นภาพ ณ เวลาเดียวกัน)."""
        with self._lock:
            params = self._get_params_locked() if self._io is not None else {}
            return {"identity": self.identity, "params": params,
                    "stats": self.stats(), "source": self.camera_index}


def probe_params(source=None):
    """
    เปิดกล้อง **ชั่วคราวโดยไม่สตรีม** เพื่ออ่านค่า/ช่วงที่ตั้งได้ แล้วปิดทันที.
    ใช้ตอนหน้าเว็บยังไม่ได้กด Start (UI ต้องรู้ min/max ก่อนถึงจะสร้างสไลเดอร์ที่ถูกต้อง).
    ⚠️ GigE เปิดได้ทีละโปรแกรม ⇒ ห้ามเรียกขณะที่กล้องตัวเดียวกันกำลังสตรีมอยู่
    (ผู้เรียกต้องใช้กล้องที่เปิดอยู่แทน).
    """
    cam = HikCamera(camera_index=source, stream=False, use_saved_settings=False,
                    apply_on_open=False)
    if not cam.initialize():
        return None, cam.last_error
    try:
        return {"identity": cam.identity, "params": cam.get_params()}, None
    finally:
        cam.release()
