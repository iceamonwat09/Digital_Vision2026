"""
MvCameraControl_class.py **ปลอม** — เลียนแบบ MVS Python SDK ของ Hikrobot เท่าที่โค้ดของเราใช้.

ทำไมต้องมี: เครื่อง dev/CI ไม่มีกล้อง GigE และไม่มี MVS SDK (Windows-only) ⇒ ถ้าไม่มีตัวปลอม
ตัวนี้ โค้ดเส้นทาง Hikrobot ทั้งหมดจะ **ไม่เคยถูกรันเลยจนกว่าจะไปอยู่หน้าเครื่องสถานี**
ซึ่งเป็นวิธีที่ทำให้ bug ง่าย ๆ (พิมพ์ชื่อ field ผิด, ลืม cast) ไปโผล่ตอนกำลังทดสอบกับของจริง.

โครงสร้าง/ชื่อ field ทำตามเอกสารและไฟล์จริงของ SDK — ถ้าของจริงต่างจากนี้ สคริปต์ตัวจริง
ยังทำงานได้อยู่ดีเพราะทุกการอ่านค่าถูกครอบ try/except ไว้ (ตัวปลอมนี้ทดสอบ "ทางที่ทุกอย่างปกติ").
"""

import ctypes
import json as _json
import os as _os

# ── ค่าคงที่ ────────────────────────────────────────────
MV_GIGE_DEVICE = 1
MV_USB_DEVICE = 4
MV_ACCESS_Exclusive = 1
MV_MATCH_TYPE_NET_DETECT = 0x00000001
PixelType_Gvsp_BGR8_Packed = 0x02180014
PixelType_Gvsp_BayerRG8 = 0x01080009
PixelType_Gvsp_Mono8 = 0x01080001


# ── โครงสร้างข้อมูลอุปกรณ์ ──────────────────────────────
class MV_GIGE_DEVICE_INFO(ctypes.Structure):
    _fields_ = [
        ("nIpCfgOption", ctypes.c_uint), ("nIpCfgCurrent", ctypes.c_uint),
        ("nCurrentIp", ctypes.c_uint), ("nCurrentSubNetMask", ctypes.c_uint),
        ("nDefultGateWay", ctypes.c_uint), ("chManufacturerName", ctypes.c_ubyte * 32),
        ("chModelName", ctypes.c_ubyte * 32), ("chDeviceVersion", ctypes.c_ubyte * 32),
        ("chManufacturerSpecificInfo", ctypes.c_ubyte * 48),
        ("chSerialNumber", ctypes.c_ubyte * 16), ("chUserDefinedName", ctypes.c_ubyte * 16),
        ("nNetExport", ctypes.c_uint),
        ("nMacAddrHigh", ctypes.c_uint), ("nMacAddrLow", ctypes.c_uint),
    ]


class MV_USB3_DEVICE_INFO(ctypes.Structure):
    _fields_ = [
        ("CrtlInEndPoint", ctypes.c_ubyte), ("CrtlOutEndPoint", ctypes.c_ubyte),
        ("StreamEndPoint", ctypes.c_ubyte), ("EventEndPoint", ctypes.c_ubyte),
        ("idVendor", ctypes.c_ushort), ("idProduct", ctypes.c_ushort),
        ("chDeviceGUID", ctypes.c_ubyte * 64), ("chVendorName", ctypes.c_ubyte * 64),
        ("chModelName", ctypes.c_ubyte * 64), ("chFamilyName", ctypes.c_ubyte * 64),
        ("chDeviceVersion", ctypes.c_ubyte * 64), ("chManufacturerName", ctypes.c_ubyte * 64),
        ("chSerialNumber", ctypes.c_ubyte * 64), ("chUserDefinedName", ctypes.c_ubyte * 64),
    ]


class _SpecialInfo(ctypes.Union):
    _fields_ = [("stGigEInfo", MV_GIGE_DEVICE_INFO), ("stUsb3VInfo", MV_USB3_DEVICE_INFO)]


class MV_CC_DEVICE_INFO(ctypes.Structure):
    _fields_ = [("nMajorVer", ctypes.c_ushort), ("nMinorVer", ctypes.c_ushort),
                ("nMacAddrHigh", ctypes.c_uint), ("nMacAddrLow", ctypes.c_uint),
                ("nTLayerType", ctypes.c_uint), ("SpecialInfo", _SpecialInfo)]


class MV_CC_DEVICE_INFO_LIST(ctypes.Structure):
    _fields_ = [("nDeviceNum", ctypes.c_uint),
                ("pDeviceInfo", ctypes.POINTER(MV_CC_DEVICE_INFO) * 256)]


# ── โครงสร้างค่าพารามิเตอร์ ─────────────────────────────
class MVCC_INTVALUE(ctypes.Structure):
    _fields_ = [("nCurValue", ctypes.c_uint), ("nMax", ctypes.c_uint),
                ("nMin", ctypes.c_uint), ("nInc", ctypes.c_uint)]


class MVCC_INTVALUE_EX(ctypes.Structure):
    _fields_ = [("nCurValue", ctypes.c_int64), ("nMax", ctypes.c_int64),
                ("nMin", ctypes.c_int64), ("nInc", ctypes.c_int64)]


class MVCC_FLOATVALUE(ctypes.Structure):
    _fields_ = [("fCurValue", ctypes.c_float), ("fMax", ctypes.c_float),
                ("fMin", ctypes.c_float)]


class MVCC_ENUMVALUE(ctypes.Structure):
    _fields_ = [("nCurValue", ctypes.c_uint), ("nSupportedNum", ctypes.c_uint),
                ("nSupportValue", ctypes.c_uint * 64)]


class MVCC_STRINGVALUE(ctypes.Structure):
    _fields_ = [("chCurValue", ctypes.c_char * 256), ("nMaxLength", ctypes.c_int64),
                ("nReserved", ctypes.c_int * 2)]


class MVCC_ENUMENTRY(ctypes.Structure):
    _fields_ = [("nValue", ctypes.c_uint), ("chSymbolic", ctypes.c_ubyte * 64)]


# ── โครงสร้างเฟรม ──────────────────────────────────────
class MV_FRAME_OUT_INFO_EX(ctypes.Structure):
    _fields_ = [("nWidth", ctypes.c_ushort), ("nHeight", ctypes.c_ushort),
                ("enPixelType", ctypes.c_int), ("nFrameNum", ctypes.c_uint),
                ("nDevTimeStampHigh", ctypes.c_uint), ("nDevTimeStampLow", ctypes.c_uint),
                ("nHostTimeStamp", ctypes.c_int64), ("nFrameLen", ctypes.c_uint)]


class MV_FRAME_OUT(ctypes.Structure):
    _fields_ = [("pBufAddr", ctypes.POINTER(ctypes.c_ubyte)),
                ("stFrameInfo", MV_FRAME_OUT_INFO_EX),
                ("nRes", ctypes.c_uint * 16)]


class MV_CC_PIXEL_CONVERT_PARAM(ctypes.Structure):
    _fields_ = [("nWidth", ctypes.c_ushort), ("nHeight", ctypes.c_ushort),
                ("enSrcPixelType", ctypes.c_int),
                ("pSrcData", ctypes.POINTER(ctypes.c_ubyte)),
                ("nSrcDataLen", ctypes.c_uint), ("enDstPixelType", ctypes.c_int),
                ("pDstBuffer", ctypes.POINTER(ctypes.c_ubyte)),
                ("nDstLen", ctypes.c_uint), ("nDstBufferSize", ctypes.c_uint),
                ("nRes", ctypes.c_uint * 4)]


class MV_MATCH_INFO_NET_DETECT(ctypes.Structure):
    _fields_ = [("nReviceDataSize", ctypes.c_int64), ("nLostPacketCount", ctypes.c_int64),
                ("nLostFrameCount", ctypes.c_uint), ("nNetRecvFrameCount", ctypes.c_uint)]


class MV_ALL_MATCH_INFO(ctypes.Structure):
    _fields_ = [("nType", ctypes.c_uint), ("pInfo", ctypes.c_void_p),
                ("nInfoSize", ctypes.c_uint)]


def _fill(buf, text):
    raw = text.encode("utf-8")[:len(buf) - 1]
    for i, b in enumerate(bytearray(raw)):
        buf[i] = b
    buf[len(raw)] = 0


# ── กล้องปลอม ──────────────────────────────────────────
# ตั้งค่าให้เหมือน MV-CS050-10GC ตัวจริงบนสถานี (จากหน้าจอ MVS ที่ผู้ใช้ส่งมา)
_W, _H = 2448, 2048

# ค่าที่แก้จากภายนอกได้ เพื่อให้เทสต์จำลอง "เคสแย่" ได้ (MVS ค้าง / packet เล็ก / ภาพมืด)
SIM = {
    "accessible": True,
    "open_ok": True,
    "packet_size": 8164,
    "optimal_packet_size": 8164,
    "lost_packets": 0,
    "lost_frames": 0,
    "drop_every": 0,      # > 0 = ทำให้เลขเฟรมกระโดดทุก ๆ n เฟรม
    "gray_level": 120,    # ความสว่างของภาพปลอม
    "max_packet_size": 9000,   # เพดานที่ NIC รับได้ (1500 = ยังไม่เปิด Jumbo Frame)
}

# เทสต์รันสคริปต์เป็น subprocess จึงตั้งค่า SIM ผ่าน env ได้ (JSON)
_env = _os.environ.get("FAKE_MVS_SIM")
if _env:
    try:
        SIM.update(_json.loads(_env))
    except Exception:
        pass


class MvCamera(object):
    def __init__(self):
        self._opened = False
        self._grabbing = False
        self._num = 0
        self._i = {"Width": _W, "Height": _H, "WidthMax": _W, "HeightMax": _H,
                   "OffsetX": 0, "OffsetY": 0, "BinningHorizontal": 1,
                   "BinningVertical": 1, "PayloadSize": _W * _H,
                   "GevSCPSPacketSize": SIM["packet_size"], "GevSCPD": 0,
                   "GevHeartbeatTimeout": 3000,
                   "GevCurrentIPAddress": (172 << 24) | (32 << 16) | (1 << 8) | 253,
                   "GevCurrentSubnetMask": (255 << 24) | (255 << 16) | (255 << 8)}
        self._s = {"DeviceModelName": "MV-CS050-10GC",
                   "DeviceSerialNumber": "DA4994130",
                   "DeviceFirmwareVersion": "V4.0.42 231212 1170605",
                   "DeviceManufacturerName": "Hikrobot"}
        self._f = {"ExposureTime": 2635.0, "Gain": 0.0, "Gamma": 1.0,
                   "AcquisitionFrameRate": 20.1, "ResultingFrameRate": 23.1064,
                   "TriggerDelay": 0.0, "DeviceTemperature": 41.5}
        self._e = {"PixelFormat": PixelType_Gvsp_BayerRG8, "ExposureAuto": 0,
                   "GainAuto": 0, "BalanceWhiteAuto": 1, "TriggerMode": 0,
                   "TriggerSource": 0, "TriggerActivation": 0, "AcquisitionMode": 2,
                   "ExposureMode": 0}
        self._b = {"AcquisitionFrameRateEnable": False, "ReverseX": False,
                   "ReverseY": False, "GammaEnable": False}

    # -- static --
    @staticmethod
    def MV_CC_EnumDevices(tlayer, lst):
        info = MV_CC_DEVICE_INFO()
        info.nTLayerType = MV_GIGE_DEVICE
        g = info.SpecialInfo.stGigEInfo
        _fill(g.chManufacturerName, "Hikrobot")
        _fill(g.chModelName, "MV-CS050-10GC")
        _fill(g.chDeviceVersion, "V4.0.42 231212 1170605")
        _fill(g.chSerialNumber, "DA4994130")
        g.nCurrentIp = (172 << 24) | (32 << 16) | (1 << 8) | 253
        g.nNetExport = (172 << 24) | (32 << 16) | (1 << 8) | 9
        g.nMacAddrHigh = 0x34BD
        g.nMacAddrLow = 0x2054483B
        MvCamera._keep = info                      # กัน GC เก็บ struct ที่ยัง cast อยู่
        lst.nDeviceNum = 1
        lst.pDeviceInfo[0] = ctypes.pointer(info)
        return 0

    @staticmethod
    def MV_CC_IsDeviceAccessible(info, mode):
        return bool(SIM["accessible"])

    # -- lifecycle --
    def MV_CC_CreateHandle(self, info):
        return 0

    def MV_CC_OpenDevice(self, mode=1, key=0):
        if not SIM["open_ok"]:
            return 0x80000203
        self._opened = True
        return 0

    def MV_CC_CloseDevice(self):
        self._opened = False
        return 0

    def MV_CC_DestroyHandle(self):
        return 0

    def MV_CC_GetOptimalPacketSize(self):
        return SIM["optimal_packet_size"]

    # -- params --
    def MV_CC_GetIntValueEx(self, key, st):
        key = key.decode() if isinstance(key, bytes) else key
        if key not in self._i:
            return 0x80000107
        st.nCurValue = self._i[key]
        st.nMin, st.nMax, st.nInc = 0, max(self._i[key], _W), 1
        return 0

    MV_CC_GetIntValue = MV_CC_GetIntValueEx

    def MV_CC_SetIntValueEx(self, key, value):
        key = key.decode() if isinstance(key, bytes) else key
        if key not in self._i or self._grabbing and key in ("Width", "Height",
                                                            "BinningHorizontal",
                                                            "BinningVertical"):
            return 0x80000107
        if key == "GevSCPSPacketSize":
            # NIC ที่ยังไม่เปิด Jumbo Frame จะรับได้แค่ ~1500 — กล้องยอมรับค่าที่ตั้ง
            # แต่ค่าที่ใช้จริงถูกจำกัด (อาการเดียวกับของจริง)
            value = min(int(value), int(SIM["max_packet_size"]))
        self._i[key] = int(value)
        return 0

    MV_CC_SetIntValue = MV_CC_SetIntValueEx

    def MV_CC_GetFloatValue(self, key, st):
        key = key.decode() if isinstance(key, bytes) else key
        if key not in self._f:
            return 0x80000107
        st.fCurValue = self._f[key]
        st.fMin, st.fMax = (15.0, 40279.0) if key == "ExposureTime" else (0.0, 100.0)
        return 0

    def MV_CC_SetFloatValue(self, key, value):
        key = key.decode() if isinstance(key, bytes) else key
        if key not in self._f:
            return 0x80000107
        if key == "ExposureTime" and not (15.0 <= float(value) <= 40279.0):
            return 0x80000105
        self._f[key] = float(value)
        return 0

    def MV_CC_GetStringValue(self, key, st):
        key = key.decode() if isinstance(key, bytes) else key
        if key not in self._s:
            return 0x80000107
        st.chCurValue = self._s[key].encode()
        st.nMaxLength = 256
        return 0

    def MV_CC_GetBoolValue(self, key, out):
        key = key.decode() if isinstance(key, bytes) else key
        if key not in self._b:
            return 0x80000107
        out.value = self._b[key]
        return 0

    def MV_CC_GetEnumValue(self, key, st):
        key = key.decode() if isinstance(key, bytes) else key
        if key not in self._e:
            return 0x80000107
        st.nCurValue = self._e[key]
        sup = ([PixelType_Gvsp_BayerRG8, PixelType_Gvsp_Mono8, PixelType_Gvsp_BGR8_Packed]
               if key == "PixelFormat" else [0, 1, 2])
        st.nSupportedNum = len(sup)
        for i, v in enumerate(sup):
            st.nSupportValue[i] = v
        return 0

    def MV_CC_SetEnumValue(self, key, value):
        key = key.decode() if isinstance(key, bytes) else key
        if key not in self._e:
            return 0x80000107
        self._e[key] = int(value)
        return 0

    # -- grabbing --
    def MV_CC_StartGrabbing(self):
        self._grabbing = True
        return 0

    def MV_CC_StopGrabbing(self):
        self._grabbing = False
        return 0

    def MV_CC_GetImageBuffer(self, out, timeout):
        if not self._grabbing:
            return 0x80000004
        w, h = self._i["Width"], self._i["Height"]
        n = w * h
        # ภาพปลอม: พื้นเทาตามค่า SIM + แถบสว่างเล็กน้อย (ให้ Laplacian ไม่เป็น 0)
        buf = (ctypes.c_ubyte * n)()
        lvl = int(SIM["gray_level"])
        for y in range(0, h, 8):                     # เขียนบางแถวพอให้เร็ว
            base = y * w
            for x in range(0, w, 8):
                buf[base + x] = min(255, lvl + (40 if (x // 8) % 2 else 0))
        self._num += 1
        if SIM["drop_every"] and self._num % SIM["drop_every"] == 0:
            self._num += 1                          # จำลองเฟรมหาย (เลขกระโดด)
        self._buf = buf                              # กัน GC
        out.pBufAddr = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
        out.stFrameInfo.nWidth = w
        out.stFrameInfo.nHeight = h
        out.stFrameInfo.enPixelType = self._e["PixelFormat"]
        out.stFrameInfo.nFrameNum = self._num
        out.stFrameInfo.nFrameLen = n
        return 0

    def MV_CC_FreeImageBuffer(self, out):
        return 0

    def MV_CC_ConvertPixelType(self, prm):
        if prm.enDstPixelType != PixelType_Gvsp_BGR8_Packed:
            return 0x80000105
        n = int(prm.nWidth) * int(prm.nHeight)
        if int(prm.nDstBufferSize) < n * 3:
            return 0x80000105
        for i in range(0, n, 997):                   # เติมบางจุดพอให้สถิติไม่เป็นศูนย์
            v = prm.pSrcData[i]
            prm.pDstBuffer[i * 3] = v
            prm.pDstBuffer[i * 3 + 1] = v
            prm.pDstBuffer[i * 3 + 2] = v
        prm.nDstLen = n * 3
        return 0

    def MV_CC_GetGevAllMatchInfo(self, info):
        det = ctypes.cast(info.pInfo, ctypes.POINTER(MV_MATCH_INFO_NET_DETECT)).contents
        det.nNetRecvFrameCount = self._num
        det.nLostPacketCount = SIM["lost_packets"]
        det.nLostFrameCount = SIM["lost_frames"]
        det.nReviceDataSize = self._num * self._i["Width"] * self._i["Height"]
        return 0
