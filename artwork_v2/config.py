"""ค่าตั้งของ Artwork V2 — ทุกค่าตั้งทับได้ด้วย environment variable.

ตัวเลขเกณฑ์ (ความมั่นใจ · ความครอบคลุม · การจับคู่) เป็น **ค่าเริ่มต้นสำหรับ PoC**
ยังไม่ได้ปรับจากไฟล์เฉลย — ทุกค่าที่ใช้ถูกพิมพ์ลง Log ของทุกงาน เพื่อให้ปรับ
ทีหลังจากตัวเลขจริงได้
"""

from __future__ import annotations

import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _f(name: str, default: str) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


def _i(name: str, default: str) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return int(default)


def _b(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off", "")


DATA_DIR = os.getenv("ARTWORK_V2_DATA_DIR",
                     os.path.join(_ROOT, "data", "artwork_v2"))
JOBS_DIR = os.path.join(DATA_DIR, "jobs")
SECRET_DIR = os.path.join(DATA_DIR, "secret")
KEY_FILE = os.path.join(SECRET_DIR, "vision_api_key.json")

# ── Vision API ───────────────────────────────────────────────────────
# ผู้ใช้เลือก EU (ข้อมูลประมวลผลใน EU เท่านั้น) — เปลี่ยนได้ด้วย env
ENDPOINT = os.getenv("ARTWORK_V2_VISION_ENDPOINT",
                     "https://eu-vision.googleapis.com").rstrip("/")
# "builtin/stable" = ค่าเริ่มต้นของ Google · ระบุชัด ๆ เพื่อบันทึกลง Log
MODEL = os.getenv("ARTWORK_V2_VISION_MODEL", "builtin/stable").strip()
KEY_ENV = "ARTWORK_V2_VISION_API_KEY"
TIMEOUT_S = _f("ARTWORK_V2_VISION_TIMEOUT_S", "120")
RETRIES = _i("ARTWORK_V2_VISION_RETRIES", "2")
RETRY_WAIT_S = _f("ARTWORK_V2_VISION_RETRY_WAIT_S", "1.0")
# ว่าง = ให้ Vision ตรวจภาษาเอง (เอกสาร: ค่าว่างให้ผลดีที่สุด · ใส่ผิดแย่ลงมาก)
LANGUAGE_HINTS = [s.strip() for s in os.getenv("ARTWORK_V2_LANGUAGE_HINTS", "").split(",")
                  if s.strip()]

# ── ขีดจำกัดของ Vision (เอกสารทางการ) + ระยะเผื่อ ─────────────────────
MAX_IMAGES_PER_REQUEST = 16          # images:annotate
MAX_REQUEST_BYTES = _i("ARTWORK_V2_MAX_REQUEST_BYTES", "9500000")   # ขีดจริง 10 MB
MAX_IMAGE_MP = _f("ARTWORK_V2_MAX_IMAGE_MP", "40")                   # ขีดจริง 75 MP
# ภาพเดียวต้องใส่คำขอได้เอง: base64 โต ~4/3 + หัว JSON
MAX_IMAGE_BYTES = int((MAX_REQUEST_BYTES - 4096) * 3 / 4)
JPEG_QUALITIES = [92, 88, 84, 80, 75, 70]

# ── การเรนเดอร์โซน ───────────────────────────────────────────────────
PDF_ZONE_DPI = _i("ARTWORK_V2_PDF_ZONE_DPI", "400")
PDF_ZONE_DPI_MAX = _i("ARTWORK_V2_PDF_ZONE_DPI_MAX", "1200")
# ด้านยาวขั้นต่ำของภาพที่ส่ง (เอกสาร Vision แนะนำ ≥ 1024×768 สำหรับ OCR)
ZONE_MIN_LONG_SIDE = _i("ARTWORK_V2_ZONE_MIN_LONG_SIDE", "1600")
PREVIEW_MAX_SIDE = _i("ARTWORK_V2_PREVIEW_MAX_SIDE", "2000")

# ความคมของภาพที่ส่ง (ผู้ใช้สั่ง 3 ต.ค.: "ภาพที่ส่งต้องคมชัดที่สุดเท่าที่ทำได้")
#   "max"      = ไฟล์ PDF: ไล่ dpi ขึ้นทีละฝั่งจนเต็มงบ — ภาพคู่ A/B ยังต้องอยู่คำขอเดียวกัน
#                (≤ MAX_REQUEST_BYTES) · ไม่เกิน MAX_IMAGE_MP · ไม่เกิน PDF_ZONE_DPI_MAX ·
#                ไม่ต่ำกว่าแบบมาตรฐานเด็ดขาด · ภาพถ่ายส่งพิกเซลต้นฉบับอยู่แล้ว = เหมือนเดิม
#   "standard" = 400 dpi แบบเดิมเป๊ะ (ทุกไบต์)
# หน้าเว็บเลือกได้ต่อรอบ (ช่อง "ภาพที่ส่ง") — ค่านี้คือค่าที่เลือกไว้ตอนเปิดหน้า
# ค่าเริ่มต้นกลับเป็น "standard" (3 ต.ค. รอบ 4): วัดบนสถานีแล้ว "max" แย่ลงทุกตัวชี้วัด —
# แถวตารางถูกตัดมากขึ้น · ความมั่นใจเฉลี่ย 0.974→0.957 · เหลือง 2→8 · แดงปลอม 0→1 · ช้าลง 2.6 เท่า
# (ชุดข้อมูล station_runs/avoderm_m1m2_run003_max)
SHARPNESS_MODES = ("max", "standard")
SHARPNESS = os.getenv("ARTWORK_V2_SHARPNESS", "standard").strip().lower()
if SHARPNESS not in SHARPNESS_MODES:
    SHARPNESS = "standard"
SHARP_FILL = _f("ARTWORK_V2_SHARP_FILL", "0.92")      # เป้าใช้งบไบต์กี่ส่วน (เผื่อคลาด)
SHARP_MAX_RENDERS = _i("ARTWORK_V2_SHARP_MAX_RENDERS", "4")

# ── การเทียบ ─────────────────────────────────────────────────────────
CONF_FAIL = _f("ARTWORK_V2_CONF_FAIL", "0.80")        # ต่ำกว่านี้ = ไม่มั่นใจ (เหลือง)
CONF_LOW = _f("ARTWORK_V2_CONF_LOW", "0.60")          # ใช้นับสัดส่วนตัวอักษรความมั่นใจต่ำ
COVERAGE_MIN = _f("ARTWORK_V2_COVERAGE_MIN", "0.90")  # PASS ต้องจับคู่ได้อย่างน้อยเท่านี้
PAIR_MIN_SIM = _f("ARTWORK_V2_PAIR_MIN_SIM", "0.60")  # ความคล้ายของคีย์จับคู่
PAIR_MIN_RUN = _f("ARTWORK_V2_PAIR_MIN_RUN", "0.40")  # ช่วงคำติดกัน (ค่าที่วัดในโปรเจกต์)
PAIR_MAX_DIST = _f("ARTWORK_V2_PAIR_MAX_DIST", "0.12")  # ระยะในโซน (0..1) สำหรับบรรทัดตัวเลขล้วน

# ต่อแถวตารางที่ OCR ตัดตรงจุดไข่ปลา (ดู compare._merge_leader_rows) · 0 = ปิด
ROW_MERGE_ENABLED = _b("ARTWORK_V2_ROW_MERGE", "1")
ROW_MAX_ANGLE = _f("ARTWORK_V2_ROW_MAX_ANGLE", "10")   # องศา — เกินนี้ไม่ต่อแถว
# เครื่องหมายวรรคตอนต่าง (ลูกน้ำ/จุดหาย) เป็นแดงได้ — ต้องผ่านการอ่านซ้ำแบบซูมเสมอ
PUNCT_CAN_FAIL = _b("ARTWORK_V2_PUNCT_CAN_FAIL", "1")

# ข้อความโค้ง/เอียง (ตรา/โลโก้) — จุดต่างบนบรรทัดที่เอียงจากแนวหลักของโซนเกิน
# ``TILT_ANGLE`` องศา (+ เศษสั้น ๆ ที่ตั้งตรงแต่ติดกับบรรทัดนั้น เช่น "&" บนตราเดียวกัน)
# ยุบเป็นการ์ดเดียว และแดงได้เฉพาะเมื่ออ่านซ้ำยืนยัน · 0 = พฤติกรรมเดิม
CURVED_GROUP_ENABLED = _b("ARTWORK_V2_CURVED_GROUP", "1")
TILT_ANGLE = _f("ARTWORK_V2_TILT_ANGLE", "10")
CURVED_NEIGHBOR_MAX_CHARS = _i("ARTWORK_V2_CURVED_NEIGHBOR_MAX_CHARS", "8")
# เศษอักขระ: บรรทัดที่ไม่มีตัวอักษร/ตัวเลขเลย และ (ความมั่นใจต่ำ หรือ ชิดขอบโซน)
# ⇒ ย้ายไปรายการพับ "เศษอักขระ / ขอบโซน" ไม่นับเป็นเหลือง · 0 = พฤติกรรมเดิม
DEBRIS_ENABLED = _b("ARTWORK_V2_DEBRIS", "1")
DEBRIS_CONF = _f("ARTWORK_V2_DEBRIS_CONF", "0.60")

# ── ชั้นหลักฐานในการเทียบ (3 ต.ค. รอบ 4) — ไม่ยิง Vision เพิ่ม · 0 = พฤติกรรมเดิมเป๊ะ ──
# จุดไข่ปลาที่รอยต่อของแถวที่ต่อกลับ (".294" ต้นชิ้นขวายังเป็นเส้นตกแต่ง ไม่ใช่จุดเดี่ยว)
SEAM_FILLER = _b("ARTWORK_V2_SEAM_FILLER", "1")
# ต่อแถวที่ถูกตัด **ด้วยหลักฐานจากอีกฝั่ง**: ชิ้นที่หายท้าย/หัวบรรทัดหนึ่ง ไปเจอเป็นบรรทัด
# ไม่มีคู่ที่อยู่ติดกันบนแถวเดียวกัน และตรงกับส่วนที่อีกฝั่งมี **ทุกตัวอักษร**
CROSS_ROW_JOIN = _b("ARTWORK_V2_CROSS_ROW_JOIN", "1")
# เครื่องหมายที่เป็นคำเดี่ยว (มีช่องว่างสองข้าง เช่น "6286 • AvoDerm") ไม่นับเป็นเรื่องตัวเลข
SYMBOL_TOKEN = _b("ARTWORK_V2_SYMBOL_TOKEN", "1")
# เศษส่วน (½ ¼ …) ที่อีกฝั่งอ่านเป็น ตัวเศษ/ตัวส่วน/หาย = คลาส FRACTION เหลืองเสมอ
# (วัดแล้ว: Vision อ่าน ½ ได้ 5 แบบ และอ่านเป็น "2" มั่นใจกว่าอ่านถูก)
FRACTION_YELLOW = _b("ARTWORK_V2_FRACTION_YELLOW", "1")

# ── อ่านซ้ำแบบซูม ────────────────────────────────────────────────────
REREAD_ENABLED = _b("ARTWORK_V2_REREAD", "1")
REREAD_MAX = _i("ARTWORK_V2_REREAD_MAX", "12")
REREAD_SCALE = _f("ARTWORK_V2_REREAD_SCALE", "2.0")
REREAD_MAX_SIDE = _i("ARTWORK_V2_REREAD_MAX_SIDE", "2400")

# จำนวนบรรทัด OCR ต่อฝั่งที่พิมพ์ลง Log (กัน Log ยาวเกินไปจนก๊อปไม่ได้)
LOG_MAX_LINES = _i("ARTWORK_V2_LOG_MAX_LINES", "400")
