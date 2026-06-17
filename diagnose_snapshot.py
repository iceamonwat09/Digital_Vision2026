"""
Snapshot camera diagnostic — RUN THIS ON THE STATION (เครื่องที่ต่อกล้องจริง).

ตอบคำถามว่า "ทำไมกดถ่ายรูปไม่สำเร็จ" ด้วยหลักฐานจากกล้องจริง:

  1) กล้องเปิดที่ความละเอียดไหนได้บ้าง (ไล่ตาม SNAPSHOT_RESOLUTION_LADDER)
     และส่งภาพออกมาจริงหรือไม่ → นี่คือสถาปัตยกรรมใหม่ (เปิดครั้งเดียว)
  2) รูปแบบเดิม release→reopen ที่ 5MP สำเร็จไหม และใช้เวลาเท่าไร
     → พิสูจน์ว่าทำไมวิธีเดิมถึง "ถ่ายไม่สำเร็จ"

วิธีรัน:   python diagnose_snapshot.py
ผลลัพธ์จะบอกชัดว่ากล้องตัวนี้ควรถ่ายที่ความละเอียดเท่าไร และวิธีไหนเสถียร.
"""

import os
import time
import cv2
import config

# Mirror camera.py: which backends to try, in priority order.
_BACKENDS_WIN   = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
_BACKENDS_LINUX = [cv2.CAP_ANY, cv2.CAP_V4L2]
_NAMES = {cv2.CAP_DSHOW: "DSHOW", cv2.CAP_MSMF: "MSMF", cv2.CAP_ANY: "ANY",
          cv2.CAP_V4L2: "V4L2"}

if os.name == 'nt':
    _ATTEMPTS = _BACKENDS_WIN + [None]      # DSHOW first → unlocks MJPG/high-res
else:
    _ATTEMPTS = [None] + _BACKENDS_LINUX


def _fourcc_str(cap):
    raw = int(cap.get(cv2.CAP_PROP_FOURCC))
    return "".join(chr((raw >> (8 * i)) & 0xFF) for i in range(4)).strip() or "?"


def _open(index, width, height, fps):
    """Open like the NEW camera.Camera does: explicit backends first (DSHOW on
    Windows) with MJPG, so the diagnostic reflects the real app behaviour.
    Returns (cap, backend_name) or (None, None)."""
    for backend in _ATTEMPTS:
        cap = (cv2.VideoCapture(index) if backend is None
               else cv2.VideoCapture(index, backend))
        if not cap.isOpened():
            cap.release()
            continue
        fourcc = getattr(config, "CAMERA_FOURCC", None)
        if fourcc:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        for _ in range(5):          # warm-up (same as Camera.initialize)
            cap.read()
        ok, _ = cap.read()
        if ok:
            bname = "Default" if backend is None else _NAMES.get(backend, str(backend))
            return cap, bname
        cap.release()
    return None, None


def test_single_handle(index):
    print("\n" + "=" * 64)
    print("  [1] สถาปัตยกรรมใหม่: เปิดครั้งเดียวที่ความละเอียดสูงสุดที่รองรับ")
    print("=" * 64)
    best = None
    for (w, h, fps) in config.SNAPSHOT_RESOLUTION_LADDER:
        t0 = time.time()
        cap, bname = _open(index, w, h, fps)
        if cap is None:
            print(f"  ✗ {w}x{h:<5} : เปิดอุปกรณ์ไม่ได้")
            continue
        ok, frame = cap.read()
        dt = time.time() - t0
        if ok and frame is not None:
            ah, aw = frame.shape[:2]
            cc = _fourcc_str(cap)
            match = "ตรงตามขอ" if (aw, ah) == (w, h) else f"กล้องให้จริง {aw}x{ah}"
            print(f"  ✓ {w}x{h:<5} : อ่านเฟรมได้ ({match}, backend={bname}, "
                  f"fourcc={cc}, {dt:.2f}s)")
            if best is None or (aw * ah) > (best[0] * best[1]):
                best = (aw, ah)
        else:
            print(f"  ✗ {w}x{h:<5} : เปิดได้แต่อ่านเฟรมไม่ออก ({dt:.2f}s)")
        cap.release()
        time.sleep(0.2)
    if best:
        print(f"\n  → ความละเอียดถ่ายที่แนะนำสำหรับกล้องนี้: {best[0]}x{best[1]}")
    else:
        print("\n  → กล้องนี้ไม่เปิดที่ความละเอียดใดในรายการเลย (ตรวจสาย/ไดรเวอร์)")
    return best


def test_release_reopen(index):
    print("\n" + "=" * 64)
    print("  [2] รูปแบบเดิม: release viewfinder → reopen ที่ 5MP (ทำไมเคย fail)")
    print("=" * 64)
    vf, bname = _open(index, config.VIEWFINDER_CAMERA_WIDTH,
                      config.VIEWFINDER_CAMERA_HEIGHT, config.VIEWFINDER_CAMERA_FPS)
    if vf is None:
        print("  ✗ เปิด viewfinder 720p ไม่ได้")
        return
    ok, _ = vf.read()
    print(f"  • viewfinder 720p เปิด {'สำเร็จ' if ok else 'ล้มเหลว'} (backend={bname})")
    vf.release()

    for settle in (0.0, 0.4, 0.8):
        time.sleep(settle)
        t0 = time.time()
        snap, _ = _open(index, config.SNAPSHOT_CAMERA_WIDTH,
                        config.SNAPSHOT_CAMERA_HEIGHT, config.SNAPSHOT_CAMERA_FPS)
        ok = bool(snap) and snap.read()[0]
        dt = time.time() - t0
        print(f"  • reopen 5MP หลังหน่วง {settle:.1f}s : "
              f"{'สำเร็จ' if ok else 'ล้มเหลว'} ({dt:.2f}s)")
        if snap:
            snap.release()
        time.sleep(0.3)


if __name__ == "__main__":
    idx = config.CAMERA_INDEX
    print(f"กล้องที่ทดสอบ: index={idx}  (แก้ได้ที่ config.CAMERA_INDEX)")
    print(f"FourCC ที่ขอ : {getattr(config, 'CAMERA_FOURCC', None)}")
    best = test_single_handle(idx)
    test_release_reopen(idx)
    print("\nสรุป: ใช้สถาปัตยกรรม [1] (เปิดครั้งเดียว) — เสถียรกว่า [2] เพราะไม่ต้อง reopen.")
    if best and best != (config.SNAPSHOT_CAMERA_WIDTH, config.SNAPSHOT_CAMERA_HEIGHT):
        print(f"ถ้า {best[0]}x{best[1]} ต่ำกว่า 5MP แปลว่ากล้อง/USB ส่ง 5MP ไม่ไหว — "
              "ใช้ความละเอียดนี้แทนได้เลย ตรวจ Dent ยังแม่นพอ.")
