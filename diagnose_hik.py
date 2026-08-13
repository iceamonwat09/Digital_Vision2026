"""
diagnose_hik.py — ตรวจสุขภาพกล้อง Hikrobot (GigE Vision / USB3) บนสถานี

ใช้ตอบคำถาม "ทำไมกล้องไม่ขึ้น / ภาพช้า / สีเพี้ยน" โดยไม่ต้องเดา
รันบนสถานี:

    py -3.9 diagnose_hik.py                 # รายการกล้อง + เปิดตัวแรก + วัดผล
    py -3.9 diagnose_hik.py --list          # แค่ดูรายการกล้อง (ไม่เปิด)
    py -3.9 diagnose_hik.py --device hik:XXXXXXX --frames 60 --save

⚠️ เครื่องมือนี้ "รายงานอย่างเดียว ไม่แก้ไขอะไรให้" — โดยเฉพาะจะไม่เปลี่ยน IP
   ของกล้องเอง (MV_GIGE_ForceIpEx) เพราะเป็นการแก้อุปกรณ์บนเครือข่ายจริง
   ที่ผู้ใช้ต้องเป็นคนสั่ง. ถ้าเจอปัญหาจะบอกวิธีแก้ให้ทำเอง.

⚠️ ข้อสำคัญที่สุด: ถ้าใช้ --save ให้ **เปิดไฟล์ hik_sample.jpg ดูด้วยตาจริง ๆ**
   เพราะการแปลง Bayer ที่ผิด convention จะทำให้ R กับ B สลับกันทั้งภาพ
   แบบเงียบ ๆ (ไม่มี error) ซึ่งจะทำให้โมเดลเห็นสีที่ไม่เคยเทรนมา.
"""

import argparse
import sys
import time

import cv2

import config
from hik_camera import HikCamera, scan_hik_cameras, sdk_status


def _rule(title):
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


def show_sdk():
    _rule("1) MVS SDK")
    st = sdk_status()
    if st["available"]:
        print(f"  ✅ พบ SDK ที่: {st['path']}")
        return True
    print(f"  ❌ {st['message']}")
    print("     • ติดตั้ง MVS for Windows จาก hikrobotics.com (Download Center)")
    print("     • หรือกำหนด env HIK_MVS_SDK_PATH ให้ชี้ไปโฟลเดอร์ MvImport")
    return False


def show_devices():
    _rule("2) กล้องที่พบ")
    cams = scan_hik_cameras()
    if not cams:
        print("  ❌ ไม่พบกล้องเลย")
        print("     • ไฟ PWR บนกล้องติดไหม? (ถ้าใช้ PoE ต้องเป็นสวิตช์/injector ที่จ่ายไฟจริง)")
        print("     • สายแลนเข้าพอร์ตที่ถูกต้องไหม? ลองเปิดโปรแกรม MVS client ดูก่อน")
        print("     • Firewall ของ adapter นั้นปิดหรือยัง?")
        return []
    for c in cams:
        mark = "✅" if c["reachable"] else "⚠️ "
        print(f"  {mark} {c['key']}")
        print(f"       รุ่น       : {c['model'] or '(ไม่ระบุ)'}   serial: {c['serial'] or '(ไม่ระบุ)'}")
        print(f"       เชื่อมต่อ  : {c['transport']}"
              + (f"   IP กล้อง: {c['ip']}   IP การ์ดแลน: {c['host_ip']}" if c["ip"] else ""))
        print(f"       เฟิร์มแวร์ : {c['version'] or '(ไม่ระบุ)'}")
        if c["hint"]:
            print(f"       ❌ {c['hint']}")
    return cams


def _net_stats(cam):
    """GigE lost-packet counters (MV_CC_GetAllMatchInfo). None if unavailable."""
    mvs = cam._mvs
    try:
        import ctypes
        detect = mvs.MV_MATCH_INFO_NET_DETECT()
        ctypes.memset(ctypes.byref(detect), 0, ctypes.sizeof(detect))
        info = mvs.MV_ALL_MATCH_INFO()
        ctypes.memset(ctypes.byref(info), 0, ctypes.sizeof(info))
        info.nType = int(getattr(mvs, "MV_MATCH_TYPE_NET_DETECT", 0x00000001))
        info.pInfo = ctypes.cast(ctypes.byref(detect), ctypes.c_void_p)
        info.nInfoSize = ctypes.sizeof(detect)
        if cam._cam.MV_CC_GetAllMatchInfo(info) != 0:
            return None
        return {
            "received_mb": round(int(detect.nReceiveDataSize) / 1e6, 1),
            "lost_packets": int(detect.nLostPacketCount),
            "lost_frames": int(detect.nLostFrameCount),
            "resend_packets": int(getattr(detect, "nResendPacketCount", 0)),
        }
    except Exception:
        return None


def run_capture(device_key, frames, save):
    _rule(f"3) เปิดกล้องและจับภาพ {frames} เฟรม")
    cam = HikCamera(device_key=device_key)
    if not cam.initialize():
        print(f"  ❌ เปิดกล้องไม่สำเร็จ: {cam.last_error}")
        return False

    info = cam.get_info()
    print(f"  ✅ เปิดสำเร็จ: {info['model']} (sn {info['serial']}, {info['transport']})")
    print(f"     ความละเอียด : {info['width']}x{info['height']}")
    print(f"     exposure    : {info['exposure_us']} µs")
    print(f"     gain        : {info['gain_db']} dB")
    print(f"     frame rate  : {info['frame_rate']} fps (ที่กล้องคำนวณได้)")
    if info["packet_size"]:
        print(f"     packet size : {info['packet_size']} bytes"
              + ("   (< 1500 = ยังไม่ได้เปิด Jumbo Frame บนการ์ดแลน)"
                 if info["packet_size"] < 1500 else ""))

    ok = fail = 0
    last = None
    t0 = time.perf_counter()
    for _ in range(frames):
        result = cam.read_frame()
        if result and result[0] and result[1] is not None:
            ok += 1
            last = result[1]
        else:
            fail += 1
    elapsed = time.perf_counter() - t0

    print(f"\n  จับได้ {ok}/{frames} เฟรม (พลาด/timeout {fail}) ใน {elapsed:.2f}s"
          f"  →  {ok / elapsed if elapsed else 0:.1f} fps")
    if last is not None:
        h, w = last.shape[:2]
        print(f"  รูปแบบเฟรมที่ได้: {w}x{h} × {last.shape[2]} channel (BGR uint8)")
    if fail:
        print("  ⚠️ มีเฟรมที่พลาด — สาเหตุที่พบบ่อย: ยังไม่เปิด Jumbo Frame, "
              "สายไม่ใช่ Cat5e+, หรือ exposure ยาวกว่าคาบเฟรม")

    stats = _net_stats(cam)
    if stats:
        print(f"\n  สถิติเครือข่าย: รับมา {stats['received_mb']} MB · "
              f"packet หาย {stats['lost_packets']} · เฟรมหาย {stats['lost_frames']} · "
              f"ขอส่งซ้ำ {stats['resend_packets']}")
        if stats["lost_packets"] or stats["lost_frames"]:
            print("  ⚠️ มี packet/เฟรมหาย → เปิด Jumbo Frame 9014, เพิ่ม Receive Buffers "
                  "ของการ์ดแลน, และอย่าให้กล้องแชร์สวิตช์กับทราฟฟิกอื่น")

    if save and last is not None:
        path = "hik_sample.jpg"
        cv2.imwrite(path, last)
        print(f"\n  💾 บันทึกภาพตัวอย่างที่ {path}")
        print("  👉 **เปิดไฟล์นี้ดูด้วยตา** — ถ้าสีแดง/น้ำเงินสลับกัน แปลว่าการแปลง Bayer")
        print("     ผิด convention ต้องแก้ก่อนนำไปตรวจจริง (ห้ามข้ามขั้นนี้)")

    cam.release()
    return ok > 0


def main():
    ap = argparse.ArgumentParser(description="ตรวจสุขภาพกล้อง Hikrobot")
    ap.add_argument("--device", default=None,
                    help="คีย์กล้อง เช่น hik:01234567 (ไม่ระบุ = ตัวแรกที่ใช้งานได้)")
    ap.add_argument("--frames", type=int, default=30, help="จำนวนเฟรมที่จะจับ (ค่าเริ่มต้น 30)")
    ap.add_argument("--save", action="store_true", help="บันทึก hik_sample.jpg ไว้ตรวจสีด้วยตา")
    ap.add_argument("--list", action="store_true", help="แสดงรายการกล้องอย่างเดียว")
    args = ap.parse_args()

    print("Hikrobot camera diagnostics")
    print(f"Python {sys.version.split()[0]} · CONFIG_VERSION {config.CONFIG_VERSION}")

    if not show_sdk():
        return 1
    cams = show_devices()
    if not cams:
        return 1
    if args.list:
        return 0
    return 0 if run_capture(args.device, args.frames, args.save) else 1


if __name__ == "__main__":
    sys.exit(main())
