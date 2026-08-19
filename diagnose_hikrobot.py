"""
diagnose_hikrobot.py — "กล้อง Hikrobot ตัวนี้พร้อมเข้าโหมดใหม่ไหม และค่าที่ควรใช้จริงคือเท่าไร"

ทำไมต้องมีสคริปต์นี้ก่อนเขียนโหมด (บทเรียนของ repo นี้)
--------------------------------------------------------
เคยพลาดมาแล้วกับ OpenVINO/pixdiff: **สรุปค่าจากทฤษฎี/ไฟล์สังเคราะห์แล้วของจริงไม่เป็นแบบนั้น**.
ค่าตั้งของกล้องอุตสาหกรรม (packet size, exposure, fps จริง, แพ็กเก็ตหาย) ขึ้นกับ
"สาย/สวิตช์/NIC/เฟิร์มแวร์/แสงหน้างาน" ล้วน ๆ — เดาไม่ได้เลย. สคริปต์นี้จึงไปวัดจาก
เครื่องจริงก่อน แล้วค่อยเอาตัวเลขมาเป็น default ของโหมดใหม่.

สิ่งที่ตรวจ (6 ชั้น)
  ① SDK      — หา MVS SDK (Python) เจอไหม โหลด DLL ได้ไหม
  ② DEVICES  — เห็นกล้องกี่ตัว รุ่น/ซีเรียล/IP/เฟิร์มแวร์ + "เปิดได้ไหม" (MVS ค้างอยู่หรือเปล่า)
  ③ NETWORK  — packet size ปัจจุบัน vs ค่าที่เหมาะสม (Jumbo frame) + heartbeat
  ④ PARAMS   — ค่าและ "ช่วงที่ตั้งได้จริง" ของทุก knob ที่โหมดใหม่จะใช้
  ⑤ GRAB     — จับเฟรมจริง: fps ที่ได้ · เฟรม/แพ็กเก็ตที่หาย · เวลาแปลง Bayer→BGR
               · ความสว่างเฉลี่ย · % พิกเซลล้น (จูนไฟ) · ความคม
  ⑥ (opt-in) --exposure-scan / --fps-test — ไล่ค่า exposure และวัด fps ที่ ROI/binning ต่าง ๆ

ค่าเริ่มต้น = **อ่านอย่างเดียว** (ไม่แก้ค่าในกล้อง ไม่เขียนไฟล์ลง data/).
ชั้น ⑥ เท่านั้นที่แก้ค่าในกล้องชั่วคราว และ **คืนค่าเดิมทุกตัวใน finally**.

วิธีใช้ (บนเครื่องสถานี — ⚠️ ปิดโปรแกรม MVS ก่อน ไม่งั้นกล้องถูกจองแบบ exclusive)
    py -3.9 diagnose_hikrobot.py                       # ตรวจครบชั้น ①-⑤
    py -3.9 diagnose_hikrobot.py --list-only           # แค่ดูว่ามีกล้องอะไรบ้าง (ไม่เปิดกล้อง)
    py -3.9 diagnose_hikrobot.py --serial DA4994130    # ระบุกล้องเมื่อมีหลายตัว
    py -3.9 diagnose_hikrobot.py --save-dir hik_shots  # เก็บภาพตัวอย่างไว้ดูด้วยตา
    py -3.9 diagnose_hikrobot.py --exposure-scan 150,200,400,800,1600,2635,5000
    py -3.9 diagnose_hikrobot.py --fps-test
    py -3.9 diagnose_hikrobot.py --json hik.json       # เก็บผลเป็นไฟล์ไว้แปะกลับมา

exit code: 0 = ผ่าน · 1 = เจอปัญหาที่ต้องแก้ · 2 = รันไม่ได้ (ไม่มี SDK / ไม่เห็นกล้อง)
"""

import argparse
import ctypes
import importlib
import json
import os
import sys
import time

try:
    import numpy as np
except Exception:                                   # numpy เป็น dependency หลักของ repo อยู่แล้ว
    np = None

try:
    import cv2
except Exception:
    cv2 = None


# ────────────────────────────────────────────────────────────────────
# ชั้น ① — หา MVS SDK
# ────────────────────────────────────────────────────────────────────
# MVS ติดตั้งไฟล์ Python ไว้หลายที่แล้วแต่รุ่น (v3 / v4.x / v4.4+) จึงไล่ทุกทางที่รู้จัก
# แทนที่จะ hard-code ทางเดียวแล้วพังบนเครื่องที่ลงคนละรุ่น.
_SDK_SUBPATHS = [
    os.path.join("Development", "Samples", "Python", "MvImport"),
    os.path.join("Development", "Samples", "Python", "MvImport", "MvImport"),
    os.path.join("Development", "Python", "MvImport"),
    os.path.join("Samples", "Python", "MvImport"),
]
_SDK_ROOTS = [
    os.environ.get("MVCAM_SDK_PATH"),
    os.environ.get("MVCAM_COMMON_RUNENV"),
    r"C:\Program Files (x86)\MVS",
    r"C:\Program Files\MVS",
    r"C:\Program Files (x86)\Common Files\MVS",
]


def _sdk_candidates(extra=None):
    """ทางที่จะไล่หา MvImport (เรียงจากที่น่าจะใช่ที่สุด)."""
    out = []
    if extra:
        out.append(extra)
    for root in _SDK_ROOTS:
        if not root:
            continue
        out.append(root)                            # เผื่อ env ชี้ตรงเข้า MvImport อยู่แล้ว
        for sub in _SDK_SUBPATHS:
            out.append(os.path.join(root, sub))
    seen, uniq = set(), []
    for p in out:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def load_sdk(extra_path=None, verbose=False):
    """
    คืน (module, ข้อมูลการโหลด) — module คือ namespace ที่มีทั้ง MvCamera และค่าคงที่
    ทั้งหมด (ไฟล์ของ Hikrobot ทำ `from CameraParams_header import *` ไว้ให้แล้ว).
    คืน (None, เหตุผล) เมื่อโหลดไม่ได้ — ไม่โยน exception ออกไป.
    """
    tried, errors = [], []
    for p in _sdk_candidates(extra_path):
        tried.append(p)
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
    for name in ("MvCameraControl_class", "MvCameraControl"):
        try:
            mod = importlib.import_module(name)
        except Exception as e:
            errors.append("%s: %s" % (name, e))
            continue
        if hasattr(mod, "MvCamera"):
            return mod, {"module": name, "file": getattr(mod, "__file__", "?"),
                         "tried": tried, "errors": errors}
        errors.append("%s: โหลดได้แต่ไม่มีคลาส MvCamera" % name)
    return None, {"module": None, "tried": tried, "errors": errors}


# ────────────────────────────────────────────────────────────────────
# ตัวช่วยอ่านค่าจากกล้อง — ทุกตัว "ห้ามโยน" (เฟิร์มแวร์ต่างรุ่นมี node ไม่เท่ากัน)
# ────────────────────────────────────────────────────────────────────
def _cstr(buf):
    """ctypes byte-array (เช่น chModelName) → str."""
    try:
        raw = bytes(bytearray(buf))
    except Exception:
        return "?"
    return raw.split(b"\x00", 1)[0].decode("utf-8", "ignore").strip()


def _ip(v):
    """uint32 → a.b.c.d"""
    try:
        v = int(v) & 0xFFFFFFFF
        return "%d.%d.%d.%d" % ((v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
    except Exception:
        return "?"


def _mac(hi, lo):
    try:
        hi, lo = int(hi) & 0xFFFF, int(lo) & 0xFFFFFFFF
        b = [(hi >> 8) & 0xFF, hi & 0xFF, (lo >> 24) & 0xFF,
             (lo >> 16) & 0xFF, (lo >> 8) & 0xFF, lo & 0xFF]
        return ":".join("%02X" % x for x in b)
    except Exception:
        return "?"


class Node(object):
    """ผลการอ่าน node หนึ่งตัว — เก็บทั้งค่าและช่วง เพื่อให้ UI ของโหมดใหม่ตั้ง min/max ได้ตรง."""

    def __init__(self, cur=None, lo=None, hi=None, sym=None, ok=False, err=None):
        self.cur, self.lo, self.hi, self.sym, self.ok, self.err = cur, lo, hi, sym, ok, err

    def __str__(self):
        if not self.ok:
            return "n/a (%s)" % (self.err or "ไม่รองรับ")
        s = self.sym if self.sym is not None else _fmt(self.cur)
        if self.lo is not None and self.hi is not None and \
                not (self.lo <= -(2 ** 62) or self.hi >= 2 ** 62):
            s += "   [%s .. %s]" % (_fmt(self.lo), _fmt(self.hi))
        return s

    def as_dict(self):
        return {"cur": self.cur, "min": self.lo, "max": self.hi,
                "symbolic": self.sym, "ok": self.ok, "error": self.err}


def _fmt(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return ("%.4f" % v).rstrip("0").rstrip(".")
    return str(v)


class Cam(object):
    """
    ห่อ MvCamera บาง ๆ ให้อ่าน/เขียนค่าได้แบบไม่พัง และแปลง error code เป็นข้อความไทย.
    (โมดูล hik_camera.py ของโหมดใหม่จะใช้แพตเทิร์นเดียวกัน แต่แยกไฟล์กัน —
     สคริปต์วินิจฉัยต้องรันได้แม้โค้ดโหมดใหม่ยังไม่มี/พัง)
    """

    def __init__(self, mv, handle):
        self.mv = mv
        self.h = handle

    # ── อ่าน ────────────────────────────────────────────────
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
                ret = fn(key, st)
                if ret == 0:
                    return Node(int(st.nCurValue), int(st.nMin), int(st.nMax), ok=True)
                last = "ret=0x%X" % (ret & 0xFFFFFFFF)
            except Exception as e:
                last = str(e)
        return Node(ok=False, err=locals().get("last", "ไม่มีฟังก์ชัน"))

    def get_float(self, key):
        mv = self.mv
        st_cls = getattr(mv, "MVCC_FLOATVALUE", None)
        if st_cls is None:
            return Node(ok=False, err="ไม่มี MVCC_FLOATVALUE")
        try:
            st = st_cls()
            ctypes.memset(ctypes.byref(st), 0, ctypes.sizeof(st))
            ret = self.h.MV_CC_GetFloatValue(key, st)
            if ret == 0:
                return Node(float(st.fCurValue), float(st.fMin), float(st.fMax), ok=True)
            return Node(ok=False, err="ret=0x%X" % (ret & 0xFFFFFFFF))
        except Exception as e:
            return Node(ok=False, err=str(e))

    def get_bool(self, key):
        try:
            v = ctypes.c_bool(False)
            ret = self.h.MV_CC_GetBoolValue(key, v)
            if ret == 0:
                return Node(bool(v.value), sym=("On" if v.value else "Off"), ok=True)
            return Node(ok=False, err="ret=0x%X" % (ret & 0xFFFFFFFF))
        except Exception as e:
            return Node(ok=False, err=str(e))

    def get_str(self, key):
        """อ่าน node ชนิดข้อความ (DeviceModelName ฯลฯ) — ใช้ยืนยันตัวตนกล้องจากตัวกล้องเอง."""
        mv = self.mv
        st_cls = getattr(mv, "MVCC_STRINGVALUE", None)
        if st_cls is None:
            return Node(ok=False, err="ไม่มี MVCC_STRINGVALUE")
        try:
            st = st_cls()
            ctypes.memset(ctypes.byref(st), 0, ctypes.sizeof(st))
            ret = self.h.MV_CC_GetStringValue(key, st)
            if ret != 0:
                return Node(ok=False, err="ret=0x%X" % (ret & 0xFFFFFFFF))
            raw = getattr(st, "chCurValue", b"")
            if isinstance(raw, bytes):
                txt = raw.split(b"\x00", 1)[0].decode("utf-8", "ignore")
            else:
                txt = _cstr(raw)
            return Node(txt, sym=txt, ok=True)
        except Exception as e:
            return Node(ok=False, err=str(e))

    def get_enum(self, key, names=None):
        mv = self.mv
        st_cls = getattr(mv, "MVCC_ENUMVALUE", None)
        if st_cls is None:
            return Node(ok=False, err="ไม่มี MVCC_ENUMVALUE")
        try:
            st = st_cls()
            ctypes.memset(ctypes.byref(st), 0, ctypes.sizeof(st))
            ret = self.h.MV_CC_GetEnumValue(key, st)
            if ret != 0:
                return Node(ok=False, err="ret=0x%X" % (ret & 0xFFFFFFFF))
            cur = int(st.nCurValue)
            sym = None
            # SDK ใหม่บอกชื่อ symbolic ได้ตรง ๆ — ใช้ได้ก็ดีที่สุด (ไม่ต้องเดา mapping)
            try:
                ent_cls = getattr(mv, "MVCC_ENUMENTRY", None)
                fn = getattr(self.h, "MV_CC_GetEnumEntrySymbolic", None)
                if ent_cls is not None and fn is not None:
                    ent = ent_cls()
                    ctypes.memset(ctypes.byref(ent), 0, ctypes.sizeof(ent))
                    ent.nValue = cur
                    if fn(key, ent) == 0:
                        sym = _cstr(ent.chSymbolic)
            except Exception:
                sym = None
            if not sym and names:
                sym = names.get(cur)
            if not sym:
                sym = str(cur)
            n_sup = int(getattr(st, "nSupportedNum", 0) or 0)
            sup = []
            for i in range(min(n_sup, 64)):
                try:
                    sup.append(int(st.nSupportValue[i]))
                except Exception:
                    break
            node = Node(cur, sym=sym, ok=True)
            node.supported = sup
            return node
        except Exception as e:
            return Node(ok=False, err=str(e))

    # ── เขียน (ใช้เฉพาะชั้น opt-in และต้องคืนค่าเดิมเสมอ) ───────
    def set_float(self, key, value):
        try:
            return self.h.MV_CC_SetFloatValue(key, float(value)) == 0
        except Exception:
            return False

    def set_enum(self, key, value):
        try:
            return self.h.MV_CC_SetEnumValue(key, int(value)) == 0
        except Exception:
            return False

    def set_int(self, key, value):
        for fn_name in ("MV_CC_SetIntValueEx", "MV_CC_SetIntValue"):
            fn = getattr(self.h, fn_name, None)
            if fn is None:
                continue
            try:
                if fn(key, int(value)) == 0:
                    return True
            except Exception:
                pass
        return False


# ────────────────────────────────────────────────────────────────────
# ชั้น ⑤ — จับเฟรม + วัด
# ────────────────────────────────────────────────────────────────────
def _pixel_names(mv):
    """สร้าง map ค่าตัวเลข → ชื่อ PixelType จากตัวโมดูลเอง (ไม่ hard-code)."""
    out = {}
    for name in dir(mv):
        if name.startswith("PixelType_Gvsp_"):
            try:
                out[int(getattr(mv, name))] = name.replace("PixelType_Gvsp_", "")
            except Exception:
                pass
    return out


def start_grab(cam):
    """เริ่มสตรีมภาพ (idempotent — เรียกซ้ำแล้ว SDK คืน error ก็ไม่เป็นไร)."""
    try:
        return cam.h.MV_CC_StartGrabbing() == 0
    except Exception:
        return False


def stop_grab(cam):
    try:
        return cam.h.MV_CC_StopGrabbing() == 0
    except Exception:
        return False


def grab_frames(mv, cam, n, timeout_ms, save_dir=None, tag="grab", quiet=False):
    """
    จับ n เฟรม แล้ววัด: fps จริง · เฟรมที่ timeout · เฟรมที่ "เลขกระโดด" (หาย) ·
    เวลาแปลงเป็น BGR · ความสว่าง/พิกเซลล้น/ความคม.
    คืน dict สรุป (ไม่โยน exception).
    """
    pix = _pixel_names(mv)
    dst_type = getattr(mv, "PixelType_Gvsp_BGR8_Packed", None)
    # struct กับฟังก์ชันของ SDK ต้อง "จับคู่กัน" (EX ↔ Ex) — ผิดคู่แล้วขนาด field
    # ไม่ตรงกัน จะได้ภาพเพี้ยนแบบเงียบ ๆ. ไล่เป็นคู่ ๆ แทนการเลือกอิสระ.
    conv_pairs = []
    for st_name, fn_name in (("MV_CC_PIXEL_CONVERT_PARAM", "MV_CC_ConvertPixelType"),
                             ("MV_CC_PIXEL_CONVERT_PARAM_EX", "MV_CC_ConvertPixelTypeEx")):
        st = getattr(mv, st_name, None)
        if st is not None and hasattr(cam.h, fn_name):
            conv_pairs.append((st, fn_name))
    frame_cls = getattr(mv, "MV_FRAME_OUT", None)
    if frame_cls is None:
        return {"error": "SDK ไม่มี MV_FRAME_OUT (รุ่นเก่าเกินไป)"}

    res = {"asked": n, "got": 0, "timeouts": 0, "dropped": 0, "convert_fail": 0,
           "size": None, "pixel_format": None, "fps": None, "convert_ms": None,
           "mean": None, "clip_pct": None, "dark_pct": None, "sharpness": None,
           "exposure_reported_us": None}
    prev_num = None
    conv_ms, means, clips, darks, sharps = [], [], [], [], []
    saved = 0
    t0 = time.time()

    for _ in range(n):
        out = frame_cls()
        ctypes.memset(ctypes.byref(out), 0, ctypes.sizeof(out))
        try:
            ret = cam.h.MV_CC_GetImageBuffer(out, int(timeout_ms))
        except Exception as e:
            res["error"] = "MV_CC_GetImageBuffer โยน: %s" % e
            break
        if ret != 0:
            res["timeouts"] += 1
            continue
        try:
            fi = out.stFrameInfo
            w, h = int(fi.nWidth), int(fi.nHeight)
            res["size"] = "%dx%d" % (w, h)
            res["pixel_format"] = pix.get(int(fi.enPixelType), str(int(fi.enPixelType)))
            num = int(getattr(fi, "nFrameNum", 0) or 0)
            if prev_num is not None and num > prev_num + 1:
                res["dropped"] += (num - prev_num - 1)
            prev_num = num
            res["got"] += 1

            bgr = None
            if conv_pairs and dst_type is not None:
                t_c = time.perf_counter()
                dst_len = w * h * 3
                dst = (ctypes.c_ubyte * dst_len)()
                ok_conv = False
                for st_cls, fn_name in conv_pairs:
                    try:
                        prm = st_cls()
                        ctypes.memset(ctypes.byref(prm), 0, ctypes.sizeof(prm))
                        prm.nWidth, prm.nHeight = w, h
                        prm.pSrcData = out.pBufAddr
                        prm.nSrcDataLen = int(fi.nFrameLen)
                        prm.enSrcPixelType = fi.enPixelType
                        prm.enDstPixelType = dst_type
                        prm.pDstBuffer = ctypes.cast(dst, ctypes.POINTER(ctypes.c_ubyte))
                        prm.nDstBufferSize = dst_len
                        if getattr(cam.h, fn_name)(prm) == 0:
                            ok_conv = True
                            break
                    except Exception:
                        continue
                if ok_conv:
                    conv_ms.append((time.perf_counter() - t_c) * 1000.0)
                    if np is not None:
                        bgr = np.frombuffer(dst, dtype=np.uint8, count=dst_len).reshape(h, w, 3)
                else:
                    res["convert_fail"] += 1

            if bgr is not None and np is not None:
                if cv2 is not None:
                    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
                else:
                    gray = bgr.mean(axis=2).astype("uint8")
                means.append(float(gray.mean()))
                clips.append(float((gray >= 250).mean() * 100.0))
                darks.append(float((gray <= 5).mean() * 100.0))
                if cv2 is not None:
                    small = cv2.resize(gray, (0, 0), fx=0.25, fy=0.25,
                                       interpolation=cv2.INTER_AREA)
                    sharps.append(float(cv2.Laplacian(small, cv2.CV_64F).var()))
                if save_dir and cv2 is not None and saved < 3:
                    try:
                        os.makedirs(save_dir, exist_ok=True)
                        path = os.path.join(save_dir, "%s_%02d.jpg" % (tag, saved + 1))
                        cv2.imwrite(path, bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
                        saved += 1
                    except Exception:
                        pass
        finally:
            try:
                cam.h.MV_CC_FreeImageBuffer(out)
            except Exception:
                pass

    dt = time.time() - t0
    if res["got"] and dt > 0:
        res["fps"] = res["got"] / dt
    if conv_ms:
        res["convert_ms"] = sum(conv_ms) / len(conv_ms)
    if means:
        res["mean"] = sum(means) / len(means)
        res["clip_pct"] = sum(clips) / len(clips)
        res["dark_pct"] = sum(darks) / len(darks)
    if sharps:
        res["sharpness"] = sum(sharps) / len(sharps)
    exp = cam.get_float("ExposureTime")
    if exp.ok:
        res["exposure_reported_us"] = exp.cur
    if save_dir and saved:
        res["saved_dir"] = os.path.abspath(save_dir)
    return res


def net_stats(mv, cam):
    """สถิติเครือข่ายของ GigE — แพ็กเก็ต/เฟรมที่หาย คือหลักฐานว่า packet size/สายมีปัญหา."""
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
        if cam.h.MV_CC_GetGevAllMatchInfo(info) != 0:
            return None
        return {"recv_frames": int(getattr(det, "nNetRecvFrameCount", 0)),
                "lost_packets": int(getattr(det, "nLostPacketCount", 0)),
                "lost_frames": int(getattr(det, "nLostFrameCount", 0)),
                "recv_bytes": int(getattr(det, "nReviceDataSize", 0))}
    except Exception:
        return None


# ────────────────────────────────────────────────────────────────────
# รายการค่าที่โหมดใหม่จะใช้ — อ่านทั้งค่าและ "ช่วงที่ตั้งได้จริง"
# ────────────────────────────────────────────────────────────────────
_AUTO_MAP = {0: "Off", 1: "Once", 2: "Continuous"}
_ONOFF = {0: "Off", 1: "On"}
_ACQ = {0: "SingleFrame", 1: "MultiFrame", 2: "Continuous"}
_TRIG_SRC = {0: "Line0", 1: "Line1", 2: "Line2", 3: "Line3", 4: "Counter0",
             7: "Software", 8: "FrequencyConverter"}
_TRIG_ACT = {0: "RisingEdge", 1: "FallingEdge", 2: "LevelHigh", 3: "LevelLow"}
_EXP_MODE = {0: "Timed", 1: "TriggerWidth"}

# (หัวข้อ, ชนิด, ชื่อ node, map ของ enum)
_PARAMS = [
    ("ขนาดภาพ",       "int",   "WidthMax",                 None),
    ("ขนาดภาพ",       "int",   "HeightMax",                None),
    ("ขนาดภาพ",       "int",   "Width",                    None),
    ("ขนาดภาพ",       "int",   "Height",                   None),
    ("ขนาดภาพ",       "int",   "OffsetX",                  None),
    ("ขนาดภาพ",       "int",   "OffsetY",                  None),
    ("ขนาดภาพ",       "int",   "BinningHorizontal",        None),
    ("ขนาดภาพ",       "int",   "BinningVertical",          None),
    ("ขนาดภาพ",       "bool",  "ReverseX",                 None),
    ("ขนาดภาพ",       "bool",  "ReverseY",                 None),
    ("ฟอร์แมต",        "enum",  "PixelFormat",              None),
    ("ฟอร์แมต",        "int",   "PayloadSize",              None),
    ("แสง",           "enum",  "ExposureAuto",             _AUTO_MAP),
    ("แสง",           "enum",  "ExposureMode",             _EXP_MODE),
    ("แสง",           "float", "ExposureTime",             None),
    ("แสง",           "enum",  "GainAuto",                 _AUTO_MAP),
    ("แสง",           "float", "Gain",                     None),
    ("แสง",           "float", "Gamma",                    None),
    ("แสง",           "bool",  "GammaEnable",              None),
    ("แสง",           "enum",  "BalanceWhiteAuto",         _AUTO_MAP),
    ("อัตราเฟรม",      "bool",  "AcquisitionFrameRateEnable", None),
    ("อัตราเฟรม",      "float", "AcquisitionFrameRate",     None),
    ("อัตราเฟรม",      "float", "ResultingFrameRate",       None),
    ("อัตราเฟรม",      "enum",  "AcquisitionMode",          _ACQ),
    ("ทริกเกอร์",       "enum",  "TriggerMode",              _ONOFF),
    ("ทริกเกอร์",       "enum",  "TriggerSource",            _TRIG_SRC),
    ("ทริกเกอร์",       "enum",  "TriggerActivation",        _TRIG_ACT),
    ("ทริกเกอร์",       "float", "TriggerDelay",             None),
    ("เครือข่าย",      "int",   "GevSCPSPacketSize",        None),
    ("เครือข่าย",      "int",   "GevSCPD",                  None),
    ("เครือข่าย",      "int",   "GevHeartbeatTimeout",      None),
    ("อุปกรณ์",        "float", "DeviceTemperature",        None),
]


def read_params(cam, mv=None):
    """อ่านทุก node ในตาราง — node ที่กล้อง/เฟิร์มแวร์ไม่มีจะขึ้น n/a ไม่ทำให้สคริปต์ล้ม."""
    out = []
    pixnames = _pixel_names(mv) if mv is not None else {}
    for group, kind, key, names in _PARAMS:
        if key == "PixelFormat" and pixnames:
            names = pixnames
        if kind == "int":
            node = cam.get_int(key)
        elif kind == "float":
            node = cam.get_float(key)
        elif kind == "bool":
            node = cam.get_bool(key)
        else:
            node = cam.get_enum(key, names)
        out.append((group, key, node))
    return out


# ────────────────────────────────────────────────────────────────────
# ชั้น ⑥ (opt-in) — ไล่ค่า exposure / วัด fps ที่ ROI-binning ต่าง ๆ
# ────────────────────────────────────────────────────────────────────
def exposure_scan(mv, cam, values, timeout_ms, save_dir=None):
    """
    ตอบคำถามที่ตัดสินงานนี้: **ไฟที่มีอยู่พอสำหรับ exposure สั้น ๆ ไหม**
    (แผน §3 ต้องการ 150-200µs เพื่อไม่ให้กระป๋องเบลอที่ 7-12 ใบ/วิ).
    คืนค่าเดิมของกล้องเสมอผ่าน finally.
    """
    rows = []
    old_auto = cam.get_enum("ExposureAuto", _AUTO_MAP)
    old_exp = cam.get_float("ExposureTime")
    try:
        cam.set_enum("ExposureAuto", 0)                 # ต้องปิด auto ก่อนค่า manual จึงจะติด
        for us in values:
            if not cam.set_float("ExposureTime", us):
                rows.append({"us": us, "error": "ตั้งค่าไม่สำเร็จ (นอกช่วงที่กล้องรับ?)"})
                continue
            grab_frames(mv, cam, 2, timeout_ms)         # ทิ้งเฟรมช่วงเปลี่ยนค่า
            r = grab_frames(mv, cam, 5, timeout_ms, save_dir=save_dir,
                            tag="exp%dus" % int(us))
            rows.append({"us": us, "mean": r.get("mean"), "clip_pct": r.get("clip_pct"),
                         "dark_pct": r.get("dark_pct"), "sharpness": r.get("sharpness"),
                         "fps": r.get("fps")})
    finally:
        if old_exp.ok:
            cam.set_float("ExposureTime", old_exp.cur)
        if old_auto.ok:
            cam.set_enum("ExposureAuto", old_auto.cur)
    return rows


def fps_test(mv, cam, timeout_ms):
    """
    วัด fps จริงของ 3 ค่าตั้ง: เต็มเฟรม · binning 2× · ROI กลางภาพครึ่งหนึ่ง
    (ROI/binning คือทางเดียวที่จะได้ fps เกิน ~24 บนกล้อง 5MP ผ่าน GigE).
    คืนค่าเดิมทุกตัวใน finally.
    """
    rows = []
    w0, h0 = cam.get_int("Width"), cam.get_int("Height")
    ox0, oy0 = cam.get_int("OffsetX"), cam.get_int("OffsetY")
    bh0, bv0 = cam.get_int("BinningHorizontal"), cam.get_int("BinningVertical")
    wmax, hmax = cam.get_int("WidthMax"), cam.get_int("HeightMax")

    def _measure(label):
        # ⚠️ Width/Height/Binning เป็น node ที่ "ล็อกระหว่าง grabbing" (TLParamsLocked)
        #    ต้อง StopGrabbing ก่อนตั้งเสมอ แล้วค่อย Start ใหม่ — ไม่งั้น set เงียบ ๆ
        #    ไม่ติด แล้วเราจะวัด fps ของค่าตั้งเดิมโดยไม่รู้ตัว (ตัวเลขหลอก)
        start_grab(cam)
        r = grab_frames(mv, cam, 30, timeout_ms)
        rr = cam.get_float("ResultingFrameRate")
        wn, hn = cam.get_int("Width"), cam.get_int("Height")
        stop_grab(cam)
        rows.append({"case": label, "size": r.get("size"), "fps": r.get("fps"),
                     "set_size": ("%sx%s" % (wn.cur, hn.cur)) if (wn.ok and hn.ok) else None,
                     "resulting": rr.cur if rr.ok else None,
                     "timeouts": r.get("timeouts"), "dropped": r.get("dropped")})

    try:
        stop_grab(cam)
        _measure("ปัจจุบัน")
        # ROI ครึ่งกลาง (ต้องตั้ง offset = 0 ก่อนย่อ/ขยาย ไม่งั้นชนขอบเซนเซอร์)
        if wmax.ok and hmax.ok:
            hw, hh = (int(wmax.cur) // 2) & ~7, (int(hmax.cur) // 2) & ~7
            cam.set_int("OffsetX", 0)
            cam.set_int("OffsetY", 0)
            if cam.set_int("Width", hw) and cam.set_int("Height", hh):
                cam.set_int("OffsetX", ((int(wmax.cur) - hw) // 2) & ~7)
                cam.set_int("OffsetY", ((int(hmax.cur) - hh) // 2) & ~7)
                _measure("ROI ครึ่งกลาง")
        # binning 2 (ต้องคืน ROI เต็มก่อน)
        cam.set_int("OffsetX", 0)
        cam.set_int("OffsetY", 0)
        if wmax.ok and hmax.ok:
            cam.set_int("Width", int(wmax.cur))
            cam.set_int("Height", int(hmax.cur))
        if cam.set_int("BinningHorizontal", 2) and cam.set_int("BinningVertical", 2):
            _measure("binning 2x2")
        else:
            # ต้องรายงาน ไม่ใช่หายไปจากตาราง — "ไม่มีแถว" ทำให้คนอ่านนึกว่าลืมวัด
            rows.append({"case": "binning 2x2", "size": "-", "fps": None,
                         "resulting": None, "timeouts": "-", "dropped": "-",
                         "note": "กล้อง/เฟิร์มแวร์นี้ไม่เปิดให้ตั้ง binning"})
    finally:
        for key, node in (("BinningHorizontal", bh0), ("BinningVertical", bv0)):
            if node.ok:
                cam.set_int(key, node.cur)
        cam.set_int("OffsetX", 0)
        cam.set_int("OffsetY", 0)
        for key, node in (("Width", w0), ("Height", h0)):
            if node.ok:
                cam.set_int(key, node.cur)
        for key, node in (("OffsetX", ox0), ("OffsetY", oy0)):
            if node.ok:
                cam.set_int(key, node.cur)
    return rows


# ────────────────────────────────────────────────────────────────────
# ชั้น ② — enumerate
# ────────────────────────────────────────────────────────────────────

# เป้าหมายความสว่างเฉลี่ยที่ "พอใช้งาน" สำหรับภาพ QC (ไม่มืดจนรายละเอียดจม
# ไม่สว่างจนล้น). ใช้เป็นตัวหารเพื่อบอกว่าต้องเพิ่มแสงอีกกี่เท่า — เป็นการ
# ประมาณเชิงเส้น (แสง ∝ exposure) ซึ่งใช้ได้ในช่วงที่ยังไม่ล้นและไม่ติดพื้นดำ.
TARGET_MEAN = 80.0
BLUR_LIMIT_US = 200.0


def _light_verdict(rows, warns, report):
    """สรุปว่า 'แสงที่มีอยู่' ห่างจากที่ต้องใช้ที่ ~200 µs กี่เท่า — ตัวเลขเดียวที่ตัดสิน
    ว่าต้องซื้อไฟหรือยัง (แผน §6 บอกว่าไฟคือตัวตัดสินความสำเร็จอันดับหนึ่ง)."""
    pts = [(r["us"], r["mean"]) for r in rows
           if r.get("mean") is not None and r.get("us")]
    if not pts:
        return
    # ใช้จุดที่สว่างที่สุดที่ยังไม่ล้น เพื่อประมาณ "ความสว่างต่อ µs"
    us, mean = max(pts, key=lambda t: t[1])
    if mean <= 0.5:
        warns.append("ทุก exposure ที่ทดสอบให้ภาพมืดเกือบสนิท — วัดอัตราส่วนแสงไม่ได้ "
                     "(เปิดฝาเลนส์/รูรับแสงหรือยัง?)")
        return
    per_us = mean / float(us)
    predicted = per_us * BLUR_LIMIT_US
    factor = TARGET_MEAN / predicted if predicted > 0 else None
    report["light"] = {"measured_us": us, "measured_mean": mean,
                       "predicted_mean_at_200us": predicted,
                       "light_factor_needed": factor}
    print("\n   📐 ประมาณจากตัวเลขข้างบน (แสง ∝ exposure):")
    print("      · ที่ %.0f µs วัดได้ %.1f/255 ⇒ ที่ %.0f µs จะได้ราว **%.1f/255**"
          % (us, mean, BLUR_LIMIT_US, predicted))
    if factor and factor > 1.5:
        print("      · ต้องเพิ่มแสงอีกราว **%.0f เท่า** จึงจะได้ ~%.0f/255 ที่ %.0f µs"
              % (factor, TARGET_MEAN, BLUR_LIMIT_US))
        print("      · ตัวช่วยที่มี (คูณกันได้): เปิดรูรับแสงเลนส์ f/8→f/2.8 ≈ 8 เท่า ·")
        print("        ไฟ LED เฉพาะทางวางใกล้ชิ้นงาน ≈ 20-50 เท่า · gain 0→24dB = 16 เท่า")
        print("        (⚠️ gain เป็นทางเลือกสุดท้าย — noise กลืน dent ตื้น ตามแผน §7)")
        warns.append("แสงที่มีอยู่ห่างจากที่ต้องใช้ที่ %.0f µs ประมาณ %.0f เท่า — "
                     "ต้องแก้เรื่องไฟ/เลนส์ก่อน ไม่ใช่แก้ที่โมเดล"
                     % (BLUR_LIMIT_US, factor))
    else:
        print("      · ✅ แสงเพียงพอสำหรับ exposure สั้นตามแผนแล้ว")


def enum_devices(mv):
    """คืน (list ของ dict, error). แต่ละ dict มี '_info' = struct ตัวจริงไว้เปิดกล้อง."""
    try:
        lst = mv.MV_CC_DEVICE_INFO_LIST()
        ctypes.memset(ctypes.byref(lst), 0, ctypes.sizeof(lst))
        tl = int(getattr(mv, "MV_GIGE_DEVICE", 1)) | int(getattr(mv, "MV_USB_DEVICE", 4))
        ret = mv.MvCamera.MV_CC_EnumDevices(tl, lst)
        if ret != 0:
            return None, "MV_CC_EnumDevices ret=0x%X" % (ret & 0xFFFFFFFF)
    except Exception as e:
        return None, "MV_CC_EnumDevices โยน: %s" % e

    gige_t = int(getattr(mv, "MV_GIGE_DEVICE", 1))
    devs = []
    for i in range(int(lst.nDeviceNum)):
        try:
            info = ctypes.cast(lst.pDeviceInfo[i],
                               ctypes.POINTER(mv.MV_CC_DEVICE_INFO)).contents
        except Exception as e:
            devs.append({"index": i, "error": str(e)})
            continue
        d = {"index": i, "kind": "?", "model": "?", "serial": "?", "version": "?",
             "user_name": "", "ip": None, "nic": None, "mac": None}
        try:
            if int(info.nTLayerType) == gige_t:
                g = info.SpecialInfo.stGigEInfo
                # ⚠️ MAC อยู่คนละชั้นแล้วแต่รุ่น SDK: บนสถานี (MVS ที่มากับ V4.0.42)
                #    อยู่บน MV_CC_DEVICE_INFO ชั้นนอก ส่วนเอกสารเก่าวางไว้ใน stGigEInfo
                #    ⇒ ต้องลองทั้งสองที่ ไม่งั้น AttributeError ทำให้ "ทั้งแถว" กลายเป็น "?"
                mac_hi = getattr(g, "nMacAddrHigh", None)
                mac_lo = getattr(g, "nMacAddrLow", None)
                if mac_hi is None or mac_lo is None:
                    mac_hi = getattr(info, "nMacAddrHigh", 0)
                    mac_lo = getattr(info, "nMacAddrLow", 0)
                d.update(kind="GigE", model=_cstr(g.chModelName),
                         serial=_cstr(g.chSerialNumber), version=_cstr(g.chDeviceVersion),
                         user_name=_cstr(g.chUserDefinedName),
                         ip=_ip(g.nCurrentIp), nic=_ip(getattr(g, "nNetExport", 0)),
                         mac=_mac(mac_hi, mac_lo))
            else:
                u = info.SpecialInfo.stUsb3VInfo
                d.update(kind="USB3", model=_cstr(u.chModelName),
                         serial=_cstr(u.chSerialNumber), version=_cstr(u.chDeviceVersion),
                         user_name=_cstr(u.chUserDefinedName))
        except Exception as e:
            # ห้ามปล่อยให้ขึ้น "?" เฉย ๆ — ถ้าโครงสร้างของ SDK รุ่นนี้ไม่ตรงกับที่เราคาด
            # ต้องบอกออกมา ไม่งั้นผู้ใช้เห็น "?" แล้วเดาไม่ถูกว่าเสียตรงไหน (กฎเหล็กข้อ 2)
            d["error"] = "%s: %s" % (type(e).__name__, e)
        # เปิดได้ไหม — ถ้า False แปลว่ามีโปรแกรมอื่น (มัก = MVS) จองอยู่
        try:
            acc = getattr(mv, "MV_ACCESS_Exclusive", 1)
            d["accessible"] = bool(mv.MvCamera.MV_CC_IsDeviceAccessible(info, acc))
        except Exception:
            d["accessible"] = None
        d["_info"] = info
        devs.append(d)
    return devs, None


# ────────────────────────────────────────────────────────────────────
# รายงาน
# ────────────────────────────────────────────────────────────────────
_LINE = "─" * 74


def head(title):
    print("\n" + _LINE)
    print(title)
    print(_LINE)


def main():
    ap = argparse.ArgumentParser(
        description="ตรวจความพร้อมกล้อง Hikrobot (MVS SDK) ก่อนเปิดโหมดใหม่ในระบบตรวจ")
    ap.add_argument("--serial", help="ระบุซีเรียลกล้อง (ค่าเริ่มต้น = ตัวแรกที่เจอ)")
    ap.add_argument("--index", type=int, default=None,
                    help="เลือกกล้องด้วยลำดับที่เจอ (ใช้เมื่ออ่านซีเรียลจาก SDK ไม่ได้)")
    ap.add_argument("--sdk-path", help="โฟลเดอร์ MvImport (ถ้าติดตั้งไว้ที่อื่น)")
    ap.add_argument("--frames", type=int, default=60, help="จำนวนเฟรมที่จับตอนวัด (default 60)")
    ap.add_argument("--timeout", type=int, default=1000, help="timeout ต่อเฟรม (ms)")
    ap.add_argument("--save-dir", help="เก็บภาพตัวอย่างเป็น .jpg ไว้ดูด้วยตา")
    ap.add_argument("--list-only", action="store_true", help="แค่แสดงกล้องที่เจอ ไม่เปิดกล้อง")
    ap.add_argument("--exposure-scan", help="ไล่ค่า exposure (µs) คั่นด้วย comma "
                                            "เช่น 150,200,400,800,1600,2635,5000")
    ap.add_argument("--set-packet", nargs="?", const="auto", default=None,
                    metavar="auto|ขนาด",
                    help="ตั้ง packet size (ค่าเริ่มต้น = ค่าที่เหมาะสม) + packet delay 0 "
                         "แล้ววัดซ้ำ เพื่อพิสูจน์ว่าแก้เฟรมหายได้จริง (คืนค่าเดิมเมื่อจบ)")
    ap.add_argument("--fps-test", action="store_true",
                    help="วัด fps ที่เต็มเฟรม / ROI ครึ่งกลาง / binning 2x2")
    ap.add_argument("--json", help="เขียนผลเป็นไฟล์ JSON (ไว้แปะกลับมาให้ดู)")
    args = ap.parse_args()

    report = {"ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    problems, warns = [], []

    print(_LINE)
    print("diagnose_hikrobot.py — ตรวจความพร้อมกล้อง Hikrobot สำหรับโหมดใหม่")
    print("python %s | %s" % (sys.version.split()[0], sys.platform))
    print(_LINE)

    # ── ① SDK ────────────────────────────────────────────
    head("① SDK — MVS Python SDK")
    mv, sdk_info = load_sdk(args.sdk_path)
    report["sdk"] = {k: v for k, v in sdk_info.items() if k != "errors"}
    if mv is None:
        print("❌ โหลด MVS Python SDK ไม่ได้")
        for e in sdk_info["errors"]:
            print("   · %s" % e)
        print("\n   ทางที่ไล่หาแล้ว:")
        for p in sdk_info["tried"][:10]:
            print("     - %s%s" % (p, "  ← มีอยู่จริง" if os.path.isdir(p) else ""))
        print("\n   วิธีแก้:")
        print("     1) ติดตั้ง MVS (Hikrobot) แล้วเช็คว่ามีโฟลเดอร์")
        print(r"        C:\Program Files (x86)\MVS\Development\Samples\Python\MvImport")
        print("     2) ถ้ามีแต่ import ไม่ผ่าน = หา MvCameraControl.dll ไม่เจอ →")
        print("        เช็ค env MVCAM_COMMON_RUNENV และว่า Python เป็น 64-bit ตรงกับ SDK")
        print("     3) ระบุเองด้วย --sdk-path <โฟลเดอร์ MvImport>")
        return 2
    print("✅ โหลดได้: module=%s" % sdk_info["module"])
    print("   ไฟล์: %s" % sdk_info["file"])
    # SDK 4.4+ ต้อง Initialize ก่อนใช้งาน (รุ่นเก่าไม่มีฟังก์ชันนี้ = ข้ามได้)
    if hasattr(mv.MvCamera, "MV_CC_Initialize"):
        try:
            mv.MvCamera.MV_CC_Initialize()
            print("   เรียก MV_CC_Initialize() แล้ว")
        except Exception as e:
            warns.append("MV_CC_Initialize ล้มเหลว: %s" % e)

    # ── ② DEVICES ────────────────────────────────────────
    head("② DEVICES — กล้องที่มองเห็น")
    devs, err = enum_devices(mv)
    if err or not devs:
        print("❌ ไม่พบกล้อง (%s)" % (err or "nDeviceNum = 0"))
        print("   เช็ค: สายแลน · ไฟเลี้ยง/PoE · กล้องกับ NIC อยู่วงเดียวกันไหม ·")
        print("        Windows Firewall บล็อก MVS อยู่หรือเปล่า")
        return 2
    report["devices"] = [{k: v for k, v in d.items() if k != "_info"} for d in devs]
    for d in devs:
        mark = "✅" if d.get("accessible") else ("❌" if d.get("accessible") is False else "❔")
        print("%s [%d] %s  SN=%s  (%s)" % (mark, d["index"], d["model"], d["serial"], d["kind"]))
        if d.get("error"):
            print("      ⚠️ อ่านข้อมูลจาก struct ของ SDK ไม่ได้: %s" % d["error"])
            print("         (ไม่กระทบการเปิดกล้อง — จะยืนยันตัวตนจาก node ของกล้องแทนหลังเปิด)")
        print("      firmware=%s%s" % (d["version"],
                                       ("  ชื่อที่ตั้งเอง=%s" % d["user_name"]) if d["user_name"] else ""))
        if d["kind"] == "GigE":
            print("      กล้อง IP=%s  ·  NIC=%s  ·  MAC=%s" % (d["ip"], d["nic"], d["mac"]))
            if d["ip"] and d["nic"] and d["ip"].rsplit(".", 1)[0] != d["nic"].rsplit(".", 1)[0]:
                warns.append("กล้อง (%s) กับ NIC (%s) คนละ subnet — อาจเห็นกล้องแต่ดึงภาพไม่ได้"
                             % (d["ip"], d["nic"]))
        if d.get("accessible") is False:
            problems.append("กล้อง SN=%s เปิดไม่ได้ (ถูกจองอยู่) — **ปิดโปรแกรม MVS ก่อน**"
                            % d["serial"])

    target = None
    if args.index is not None:
        for d in devs:
            if d["index"] == args.index:
                target = d
                break
    if target is None and args.serial and all(x["serial"] in ("?", "") for x in devs):
        warns.append("อ่านซีเรียลจาก struct ของ SDK ไม่ได้ ⇒ --serial ใช้เลือกกล้องไม่ได้ "
                     "ให้ใช้ --index แทน")
    for d in (devs if target is None else []):
        if args.serial:
            if d["serial"] == args.serial:
                target = d
                break
        elif d.get("accessible") is not False:
            target = d
            break
    if target is None:
        target = devs[0]
    print("\n→ กล้องที่จะตรวจต่อ: [%d] %s SN=%s" % (target["index"], target["model"], target["serial"]))
    report["target_serial"] = target["serial"]

    if args.list_only:
        return _finish(report, problems, warns, args)

    # ── เปิดกล้อง ────────────────────────────────────────
    handle = mv.MvCamera()
    cam = Cam(mv, handle)
    ret = handle.MV_CC_CreateHandle(target["_info"])
    if ret != 0:
        print("❌ MV_CC_CreateHandle ล้มเหลว (0x%X)" % (ret & 0xFFFFFFFF))
        return 2
    ret = handle.MV_CC_OpenDevice(getattr(mv, "MV_ACCESS_Exclusive", 1), 0)
    if ret != 0:
        print("❌ เปิดกล้องไม่สำเร็จ (0x%X)" % (ret & 0xFFFFFFFF))
        print("   สาเหตุที่พบบ่อยที่สุด: **โปรแกรม MVS ยังเปิดค้างอยู่** (GigE เปิดได้ทีละโปรแกรม)")
        print("   รองลงมา: กล้องคนละ subnet / firewall / กล้องถูกอีกเครื่องจองอยู่")
        handle.MV_CC_DestroyHandle()
        return 2
    print("✅ เปิดกล้องสำเร็จ")

    # ยืนยันตัวตนจาก "ตัวกล้อง" ไม่ใช่จาก struct ของ SDK — เชื่อถือได้กว่าและใช้ได้
    # แม้ layout ของ struct จะต่างไปตามรุ่น SDK
    ident = {}
    for label, key in (("รุ่น", "DeviceModelName"), ("ซีเรียล", "DeviceSerialNumber"),
                       ("เฟิร์มแวร์", "DeviceFirmwareVersion"), ("เวอร์ชัน", "DeviceVersion"),
                       ("ผู้ผลิต", "DeviceManufacturerName"), ("ชื่อที่ตั้งเอง", "DeviceUserID")):
        node = cam.get_str(key)
        if node.ok and node.cur:
            ident[key] = node.cur
            print("   %-12s: %s" % (label, node.cur))
    for label, key in (("IP กล้อง", "GevCurrentIPAddress"), ("subnet", "GevCurrentSubnetMask")):
        node = cam.get_int(key)
        if node.ok:
            ident[key] = _ip(node.cur)
            print("   %-12s: %s" % (label, _ip(node.cur)))
    report["identity"] = ident
    if ident.get("DeviceSerialNumber"):
        report["target_serial"] = ident["DeviceSerialNumber"]

    code = 0
    try:
        # ── ③ NETWORK ────────────────────────────────────
        head("③ NETWORK — ขนาดแพ็กเก็ต (ตัวชี้เป็นชี้ตายของ GigE 5MP)")
        cur_ps = cam.get_int("GevSCPSPacketSize")
        opt_ps = None
        try:
            v = handle.MV_CC_GetOptimalPacketSize()
            opt_ps = int(v) if v and int(v) > 0 else None
        except Exception as e:
            warns.append("MV_CC_GetOptimalPacketSize ใช้ไม่ได้: %s" % e)
        print("   packet size ปัจจุบัน : %s" % cur_ps)
        print("   packet size ที่เหมาะสม: %s" % (opt_ps if opt_ps else "อ่านไม่ได้"))
        report["network"] = {"current_packet_size": cur_ps.cur if cur_ps.ok else None,
                             "optimal_packet_size": opt_ps}
        # ⚠️ ห้ามตัดสินตรงนี้ — ถ้าใช้ --set-packet แล้ววัดได้ "เฟรมหาย 0" แปลว่า
        #    ค่าเริ่มต้น 1500 ไม่ใช่ "ปัญหาที่ต้องแก้" แต่เป็น "ค่าที่ต้องตั้งเองทุกครั้ง
        #    ตอนเปิดกล้อง" ซึ่งเป็นข้อกำหนดของโค้ด ไม่ใช่ของหน้างาน. เก็บไว้ตัดสินหลังชั้น ⑤.
        pkt_low = bool(cur_ps.ok and opt_ps and cur_ps.cur < opt_ps)
        if cur_ps.ok and opt_ps and not pkt_low:
            print("   ✅ ขนาดแพ็กเก็ตเหมาะสมแล้ว")
        scpd = cam.get_int("GevSCPD")
        hb = cam.get_int("GevHeartbeatTimeout")
        print("   packet delay (GevSCPD): %s" % scpd)
        print("   heartbeat timeout    : %s" % hb)
        if scpd.ok and scpd.cur > 0:
            print("   ℹ️  packet delay > 0 = จงใจหน่วงให้ NIC ตามทัน ⇒ **กด fps ลง**")
            print("      หลังเปิด Jumbo Frame แล้วควรลดค่านี้ลง (0 ถ้าไม่มีเฟรมหาย)")
        report["network"]["packet_delay"] = scpd.cur if scpd.ok else None

        old_ps, old_pd = cur_ps, scpd
        if args.set_packet:
            # opt-in: ตั้งค่าแล้ววัดซ้ำในรันเดียว = พิสูจน์ว่าค่านี้แก้ปัญหาได้จริง
            # ก่อนจะเอาไปเป็น default ของโหมดใหม่ (ไม่ใช่เชื่อเพราะทฤษฎีบอก)
            want = opt_ps if args.set_packet == "auto" else int(args.set_packet)
            print("\n   → ลองตั้ง packet size = %s (คืนค่าเดิมเมื่อจบ)" % want)
            ok_ps = cam.set_int("GevSCPSPacketSize", want)
            ok_pd = cam.set_int("GevSCPD", 0)
            now = cam.get_int("GevSCPSPacketSize")
            print("      ตั้งได้: packet=%s (%s) · delay=0 (%s)"
                  % (now.cur if now.ok else "?", "สำเร็จ" if ok_ps else "ไม่สำเร็จ",
                     "สำเร็จ" if ok_pd else "ไม่สำเร็จ"))
            if ok_ps and now.ok and now.cur < want:
                problems.append("กล้องยอมรับ packet size ได้แค่ %d (ขอ %d) — แปลว่า "
                                "**การ์ดแลนยังไม่ได้เปิด Jumbo Frame** (ตั้ง Jumbo Packet "
                                "= 9014 ที่ Device Manager → NIC → Advanced)" % (now.cur, want))
            report["network"]["set_packet_to"] = now.cur if now.ok else None
            pkt_applied = bool(ok_ps and now.ok and opt_ps and now.cur >= opt_ps)

        # ── ④ PARAMS ─────────────────────────────────────
        head("④ PARAMS — ค่าและช่วงที่ตั้งได้จริง (จะกลายเป็น min/max ของ UI โหมดใหม่)")
        params = read_params(cam, mv)
        report["params"] = {}
        last_group = None
        for group, key, node in params:
            if group != last_group:
                print("\n  [%s]" % group)
                last_group = group
            print("    %-26s %s" % (key, node))
            report["params"][key] = node.as_dict()

        pf = cam.get_enum("PixelFormat")
        if pf.ok and getattr(pf, "supported", None):
            names = _pixel_names(mv)
            sup = [names.get(v, str(v)) for v in pf.supported]
            print("\n    PixelFormat ที่รองรับ: %s" % ", ".join(sup))
            report["params"]["PixelFormat"]["supported_names"] = sup

        # ── ⑤ GRAB ───────────────────────────────────────
        head("⑤ GRAB — จับ %d เฟรมจริง" % args.frames)
        if not start_grab(cam):
            problems.append("MV_CC_StartGrabbing ล้มเหลว — ดึงภาพไม่ได้")
            print("❌ เริ่มสตรีมไม่สำเร็จ")
        else:
            r = grab_frames(mv, cam, args.frames, args.timeout,
                            save_dir=args.save_dir, tag="grab")
            net = net_stats(mv, cam)
            stop_grab(cam)
            report["grab"] = r
            report["net_stats"] = net
            print("   ได้เฟรม          : %d/%d  (timeout %d · เฟรมหาย %d · แปลงสีล้มเหลว %d)"
                  % (r["got"], r["asked"], r["timeouts"], r["dropped"], r["convert_fail"]))
            print("   ขนาด/ฟอร์แมต     : %s  %s" % (r["size"], r["pixel_format"]))
            print("   fps ที่ได้จริง     : %s" % (("%.2f" % r["fps"]) if r["fps"] else "-"))
            print("   เวลาแปลง→BGR     : %s ms/เฟรม"
                  % (("%.1f" % r["convert_ms"]) if r["convert_ms"] else "-"))
            print("   exposure ที่ใช้อยู่ : %s µs" % _fmt(r["exposure_reported_us"]))
            if r["mean"] is not None:
                print("   ความสว่างเฉลี่ย   : %.1f / 255   (พิกเซลล้น %.2f%% · มืดสนิท %.2f%%)"
                      % (r["mean"], r["clip_pct"], r["dark_pct"]))
            if r["sharpness"] is not None:
                print("   ความคม (Laplacian): %.1f" % r["sharpness"])
            if net:
                print("   สถิติเครือข่าย    : รับ %d เฟรม · แพ็กเก็ตหาย %d · เฟรมหาย %d"
                      % (net["recv_frames"], net["lost_packets"], net["lost_frames"]))
                if net["lost_packets"] > 0 or net["lost_frames"] > 0:
                    problems.append("แพ็กเก็ต/เฟรมหายจริง (%d/%d) — ปรับ packet size + "
                                    "GevSCPD หรือเช็คสาย/สวิตช์ ก่อนเชื่อผลตรวจใด ๆ"
                                    % (net["lost_packets"], net["lost_frames"]))
            if r["got"] == 0:
                problems.append("จับเฟรมไม่ได้เลย — ตรวจ trigger mode (ถ้าเป็น On ต้องมีทริกเกอร์) "
                                "และ packet size")
            if r["timeouts"]:
                warns.append("มีเฟรม timeout %d ครั้ง" % r["timeouts"])
            if r["dropped"]:
                problems.append("เลขเฟรมกระโดด = ภาพหายระหว่างทาง %d เฟรม" % r["dropped"])
            # ตัดสินเรื่อง packet size ตรงนี้ โดยดู "ผลจริง" ไม่ใช่ดูแค่ตัวเลขค่าตั้ง
            if pkt_low:
                if locals().get("pkt_applied") and r["got"] and not r["dropped"]:
                    warns.append(
                        "กล้อง/ไดรเวอร์ตั้งต้นที่ packet size %d เสมอ — ตั้งเป็น %d + delay 0 "
                        "แล้ววัดได้ %.1f fps เฟรมหาย 0 ⇒ **`hik_camera.py` ต้องตั้งสองค่านี้ "
                        "ทุกครั้งตอนเปิดกล้อง** (ค่านี้ไม่ถูกจำไว้ในกล้อง)"
                        % (cur_ps.cur, opt_ps, r["fps"] or 0.0))
                else:
                    problems.append(
                        "packet size = %d แต่ค่าที่เหมาะสมคือ %d ⇒ เปิด Jumbo Frame (MTU 9000) "
                        "ที่การ์ดแลนแล้วตั้งค่านี้ ไม่งั้น 5MP@24fps จะทำให้ "
                        "**แพ็กเก็ตหาย/ภาพแหว่ง** (ลองยืนยันด้วย --set-packet)"
                        % (cur_ps.cur, opt_ps))
            if r["mean"] is not None:
                if r["mean"] < 40:
                    warns.append("ภาพมืด (เฉลี่ย %.0f/255) ที่ exposure %s µs — ต้องเพิ่มไฟ "
                                 "ก่อนจะลด exposure ลงมาที่ 150-200 µs ตามแผน"
                                 % (r["mean"], _fmt(r["exposure_reported_us"])))
                if r["clip_pct"] and r["clip_pct"] > 1.0:
                    warns.append("พิกเซลล้น %.1f%% — รายละเอียดบริเวณสว่างหายถาวร ลด exposure/gain"
                                 % r["clip_pct"])

        # ── ⑥ opt-in ─────────────────────────────────────
        if args.exposure_scan:
            head("⑥ก EXPOSURE SCAN — ไฟที่มีพอสำหรับ exposure สั้นไหม")
            try:
                values = [float(x) for x in args.exposure_scan.split(",") if x.strip()]
            except ValueError:
                values = []
            if not values:
                print("   ⚠️ อ่านรายการค่าไม่ได้ (ตัวอย่าง: --exposure-scan 150,200,400,800)")
            else:
                start_grab(cam)
                rows = exposure_scan(mv, cam, values, args.timeout, save_dir=args.save_dir)
                stop_grab(cam)
                report["exposure_scan"] = rows
                print("   %-10s %-10s %-12s %-12s %s" %
                      ("exposure", "สว่างเฉลี่ย", "พิกเซลล้น%", "มืดสนิท%", "ความคม"))
                for row in rows:
                    if row.get("error"):
                        print("   %-10s %s" % ("%.0fµs" % row["us"], row["error"]))
                        continue
                    print("   %-10s %-10s %-12s %-12s %s" % (
                        "%.0fµs" % row["us"],
                        ("%.1f" % row["mean"]) if row.get("mean") is not None else "-",
                        ("%.2f" % row["clip_pct"]) if row.get("clip_pct") is not None else "-",
                        ("%.2f" % row["dark_pct"]) if row.get("dark_pct") is not None else "-",
                        ("%.1f" % row["sharpness"]) if row.get("sharpness") is not None else "-"))
                print("\n   อ่านผล: แผน §3 ต้องการ exposure ≤ 165-260 µs เพื่อไม่ให้กระป๋องเบลอ")
                print("   ที่ 7-12 ใบ/วิ. ถ้าแถวนั้น 'สว่างเฉลี่ย' ต่ำกว่า ~60 = **ไฟไม่พอ**")
                print("   (เพิ่ม gain แทนไม่ใช่ทางออก — noise จะกลืน dent ตื้น)")
                _light_verdict(rows, warns, report)

        if args.fps_test:
            head("⑥ข FPS TEST — เต็มเฟรม vs ROI vs binning")
            rows = fps_test(mv, cam, args.timeout)
            report["fps_test"] = rows
            print("   %-16s %-14s %-10s %-12s %s" %
                  ("กรณี", "ขนาด", "fps จริง", "ที่กล้องบอก", "timeout/หาย"))
            for row in rows:
                print("   %-16s %-14s %-10s %-12s %s/%s%s" % (
                    row["case"], row.get("size") or row.get("set_size") or "-",
                    ("%.2f" % row["fps"]) if row.get("fps") else "-",
                    ("%.2f" % row["resulting"]) if row.get("resulting") else "-",
                    row.get("timeouts"), row.get("dropped"),
                    ("   ← %s" % row["note"]) if row.get("note") else ""))
            print("\n   ROI/binning คือทางเดียวที่ทำให้ 5MP วิ่งเกิน ~24fps บน GigE ได้")
    finally:
        try:
            stop_grab(cam)
            # คืนค่าเครือข่ายที่ --set-packet เปลี่ยนไป (สคริปต์นี้ต้องไม่ทิ้งร่องรอย)
            if args.set_packet:
                for key, node in (("GevSCPSPacketSize", locals().get("old_ps")),
                                  ("GevSCPD", locals().get("old_pd"))):
                    if node is not None and node.ok:
                        cam.set_int(key, node.cur)
            handle.MV_CC_CloseDevice()
            handle.MV_CC_DestroyHandle()
        except Exception:
            pass
        if hasattr(mv.MvCamera, "MV_CC_Finalize"):
            try:
                mv.MvCamera.MV_CC_Finalize()
            except Exception:
                pass

    return _finish(report, problems, warns, args, code)


def _finish(report, problems, warns, args, code=0):
    head("สรุป")
    if problems:
        print("❌ ต้องแก้ %d ข้อ:" % len(problems))
        for p in problems:
            print("   · %s" % p)
    if warns:
        print("⚠️  ข้อสังเกต %d ข้อ:" % len(warns))
        for w in warns:
            print("   · %s" % w)
    if not problems and not warns:
        print("✅ ไม่พบปัญหา — กล้องพร้อมสำหรับเขียนโหมดใหม่")
    report["problems"] = problems
    report["warnings"] = warns
    if args.json:
        try:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2, default=str)
            print("\nเขียนผลลง %s แล้ว" % os.path.abspath(args.json))
        except Exception as e:
            print("\n⚠️ เขียนไฟล์ JSON ไม่สำเร็จ: %s" % e)
    print(_LINE)
    return 1 if problems else code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nยกเลิกโดยผู้ใช้")
        sys.exit(2)
