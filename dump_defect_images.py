"""
dump_defect_images.py — ดึงภาพ defect (base64) จาก SQL Server ออกมาเป็นไฟล์ .jpg
เพื่อใช้เป็นชุดภาพจริงสำหรับ verify_onnx.py / verify_openvino.py.

ทำไมต้องมี: การ verify ความแม่นต้องใช้ภาพกระป๋องจริง ≥10-20 รูป — ระบบบันทึกภาพ
ตอนเจอ NG ลง DB อยู่แล้ว (ผ่าน sp_log_defect) นี่คือแหล่งภาพ production จริง
ที่ตรง domain ที่สุด (เฟรมจากกล้องสถานีเอง). สคริปต์นี้อ่านอย่างเดียว ไม่แก้/ลบ
อะไรใน DB ทั้งสิ้น.

วิธีใช้ (บนเครื่องสถานี):
    py -3.9 dump_defect_images.py                          # 20 รูปล่าสุด → sample_cans\
    py -3.9 dump_defect_images.py --limit 50 --out sample_cans

หมายเหตุ: DB มีแต่ภาพตอนเจอ defect (NG) — ถ้าเป็นไปได้ควรเติมภาพกระป๋องดี (OK)
จากโหมด Snapshot เพิ่มอีก 3-5 รูป เพื่อให้ชุด verify มีทั้งสองแบบ.
"""

import argparse
import base64
import os
import sys

import cv2
import numpy as np

from database import Database


def _decode_b64_image(value: str):
    """พยายามถอด base64 → ภาพ. คืน ndarray หรือ None."""
    try:
        raw = base64.b64decode(value, validate=True)
        img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is not None and img.size > 0:
            return img
    except Exception:
        pass
    return None


def main():
    ap = argparse.ArgumentParser(description="Dump defect images from SQL Server to .jpg files.")
    ap.add_argument("--out", default="sample_cans", help="โฟลเดอร์ปลายทาง (default: sample_cans)")
    ap.add_argument("--limit", type=int, default=20, help="จำนวน record ล่าสุดที่ดึง (default: 20)")
    args = ap.parse_args()

    db = Database()
    if not db.connect():
        print("[ERROR] ต่อ SQL Server ไม่ได้ — ตรวจ SQL_SERVER/SQL_USER/SQL_PASSWORD ใน config.py")
        return 1

    try:
        rows = db.get_all_defects(limit=args.limit)
    finally:
        db.disconnect()

    if not rows:
        print("[WARN] ไม่พบ defect record ใน DB (หรือ sp_get_defects คืนค่าว่าง)")
        return 1

    os.makedirs(args.out, exist_ok=True)
    saved = 0
    for r in rows:
        rec_id = r.get("_id") or r.get("id") or "unknown"
        # ชื่อคอลัมน์ภาพอาจต่างกันตาม schema — หา str ยาวๆ ที่ถอดเป็นภาพได้จริง
        img = None
        for key, val in r.items():
            if isinstance(val, str) and len(val) > 1000:
                img = _decode_b64_image(val)
                if img is not None:
                    break
        if img is None:
            continue
        dtype = str(r.get("defect_type", "defect")).replace("/", "_").replace("\\", "_")
        path = os.path.join(args.out, f"db_{dtype}_{rec_id}.jpg")
        if cv2.imwrite(path, img):
            saved += 1
            print(f"  saved {path}  ({img.shape[1]}x{img.shape[0]})")

    print(f"\n[DONE] บันทึก {saved}/{len(rows)} ภาพ → {args.out}\\")
    if saved == 0:
        print("[WARN] record มีแต่ไม่พบคอลัมน์ภาพ base64 — sp_get_defects อาจไม่คืนคอลัมน์ภาพ")
        print("       แจ้งผลนี้กลับมา จะได้ปรับ query ให้ตรง schema จริง")
        return 1
    if saved < 10:
        print(f"[NOTE] ได้ {saved} ภาพ (<10) — ลองเพิ่ม --limit หรือเติมภาพจาก Snapshot ให้ครบ ≥10")
    return 0


if __name__ == "__main__":
    sys.exit(main())
