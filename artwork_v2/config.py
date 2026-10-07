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

# 1 คู่โซน = 1 คำขอ Vision (ผู้ใช้สั่ง 4 ต.ค.) — ไม่รวมหลายคู่ในคำขอเดียว · คู่ที่ภาพรวมกัน
# ใหญ่เกินคำขอเดียว ⇒ เข้ารหัสใหม่ให้พอดี (ลดคุณภาพก่อนย่อ + เตือนเสมอ) แทนการแยกเป็น 2 คำขอ
# 0 = แบบเดิม (รวมหลายคู่ในคำขอเดียวได้ · คู่ใหญ่ถูกแยกรายภาพ)
ONE_REQUEST_PER_PAIR = _b("ARTWORK_V2_ONE_REQUEST_PER_PAIR", "1")

# กันการยิงคำขอรัว ๆ (runguard.py) — ทุกตัว 0 = ปิดด่านนั้น
RUN_GUARD = _b("ARTWORK_V2_RUN_GUARD", "1")                       # งานเดียวกันวิ่งได้ทีละรอบ
RUN_MAX_CONCURRENT = _i("ARTWORK_V2_RUN_MAX_CONCURRENT", "2")     # ทั้งเครื่องพร้อมกันไม่เกิน
RUN_COOLDOWN_S = _f("ARTWORK_V2_RUN_COOLDOWN_S", "3")             # พักหลังจบรอบ (ต่องาน)
KEY_TEST_COOLDOWN_S = _f("ARTWORK_V2_KEY_TEST_COOLDOWN_S", "5")   # ปุ่ม "ทดสอบกุญแจ"

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

# สีของภาพที่ส่งให้ Vision (ทดลอง A/B — ผู้ใช้สั่ง 7 ต.ค.) · หน้าเว็บเลือกได้ต่อรอบ (ช่อง "สีของภาพ")
#   "color" = ภาพสีเดิม **ทุกไบต์** (ค่าเริ่มต้น)
#   "gray"  = ภาพเทา (ช่องเดียว) — ข้อมูลความสว่างครบ ไม่ตัดเกณฑ์ · ไฟล์เล็กลง ~1/3
#   "bw"    = ขาวดำจริง (ตัดเกณฑ์เฉพาะที่ ``BW_BLOCK_MM`` · ``BW_C``) — เสี่ยง: จุดอักษรอาหรับ/
#             เส้นบาง ๆ อาจหายหรือติดกัน · ตัวอักษรสีอ่อนบนพื้นเข้มที่อยู่ห่างกันอาจหาย
# ใช้กับภาพรอบหลัก + ภาพอ่านซ้ำ · ชั้นหลักฐานภาพ (pixverify) เรนเดอร์จากไฟล์ต้นฉบับเอง ⇒ ไม่ได้รับผล
# ไม่ใช่ "color" + โหมดคมสูงสุด ⇒ ใช้ 400 dpi (คมสูงสุดวัดงบจากภาพสี)
COLOR_MODES = ("color", "gray", "bw")
COLOR_MODE = os.getenv("ARTWORK_V2_COLOR_MODE", "color").strip().lower()
if COLOR_MODE not in COLOR_MODES:
    COLOR_MODE = "color"
BW_BLOCK_MM = _f("ARTWORK_V2_BW_BLOCK_MM", "3.0")    # หน้าต่างตัดเกณฑ์เฉพาะที่ (mm บนชิ้นงาน)
BW_C = _f("ARTWORK_V2_BW_C", "12")                    # ระดับเทาที่ต้องเข้มกว่าค่าเฉลี่ยรอบข้างถึงจะเป็นหมึก

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
# ปิดเป็นค่าเริ่มต้น (3 ต.ค. รอบ 5 · ผู้ใช้สั่ง "ห้ามส่งซ้ำ"): การอ่านซ้ำส่งภาพครอปให้ Vision
# อีกรอบ — วัดจาก Log สถานีแล้วกิน ~90% ของภาพที่ส่งต่องาน (หลัก 2 ภาพ · อ่านซ้ำ 18 ภาพ)
# ตั้ง ``ARTWORK_V2_REREAD=1`` = กลับมาเปิดเหมือนเดิม
REREAD_ENABLED = _b("ARTWORK_V2_REREAD", "0")
REREAD_MAX = _i("ARTWORK_V2_REREAD_MAX", "12")
REREAD_SCALE = _f("ARTWORK_V2_REREAD_SCALE", "2.0")
REREAD_MAX_SIDE = _i("ARTWORK_V2_REREAD_MAX_SIDE", "2400")

# ── ตรวจทานด้วย AI (Gemini ผ่าน N8N) — ส่ง **ข้อความ** ที่ Vision อ่านได้ ไม่ส่งภาพ ─────
#   "assist" = อัลกอริทึมตัดสิน · AI อธิบาย/ให้คำแนะนำทุกจุด + หาจุดที่ขาด (ขึ้นเหลือง) ·
#              AI ลบหรือลดระดับจุดของอัลกอริทึมไม่ได้
#   "judge"  = AI ตัดสินหลัก · จุดของอัลกอริทึมที่ AI ไม่ระบุไปอยู่ในรายการพับ (ไม่นับ)
#   "raw"    = (ทดลอง · 6 ต.ค.) AI ตัดสินจาก **ข้อมูลดิบของ Vision** — บรรทัดตามที่ Vision ส่ง
#              (ไม่ต่อแถว ไม่ต่อคำตัดท้าย ไม่ส่งผล/ธงใด ๆ ของอัลกอริทึม) · Gemini คิดนานขึ้น ·
#              กรอบ/% ยังมาจาก Vision · ผลของอัลกอริทึมไปอยู่ในรายการพับ "ไว้เทียบ" (ไม่นับ)
#   "off"    = ไม่เรียก AI = ผลเดิมเป๊ะ
# % ความมั่นใจทุกจุดคิดจากค่าความมั่นใจของ Vision ตรงตัวอักษรนั้น — **ไม่ใช่ตัวเลขที่ AI บอก**
# หน้าเว็บเลือกได้ต่อรอบ (ช่อง "AI ตรวจทาน") — ค่านี้คือค่าที่เลือกไว้ตอนเปิดหน้า
# ── กติกาโครงสร้างของการเทียบ (7 ต.ค. · ข้อสรุปทีม · ``artwork_v2/structure.py``) ──────
# แดงหลอกส่วนใหญ่มาจาก Vision จัดบรรทัดต่างกันสองฝั่ง ไม่ใช่อ่านตัวอักษรผิด · ทุกธง ``0`` = เดิมเป๊ะ
GEO_PAIRING = _b("ARTWORK_V2_GEO_PAIRING", "1")          # จับคู่บรรทัดตามตำแหน่ง
RECOMPOSE = _b("ARTWORK_V2_RECOMPOSE", "1")              # ต่อชิ้นที่ถูกแยก (เท่ากันทุกตัวอักษร)
MOVED_TEXT = _b("ARTWORK_V2_MOVED_TEXT", "1")            # ข้อความย้ายที่ในบรรทัด ⇒ MOVED เหลือง
RELOCATE = _b("ARTWORK_V2_RELOCATE", "1")                # อยู่อีกบรรทัดตรงตำแหน่งเดียวกัน ⇒ พับ
BALANCED_MOVE = _b("ARTWORK_V2_BALANCED_MOVE", "1")      # หาย+เกินข้อความเดียวกัน ⇒ เหลือง
# 7 ต.ค. (รอบ 5 · Friskies): Vision รวมสองคอลัมน์ที่อยู่แถวเดียวกันเป็นบรรทัดเดียว ⇒ แยกเมื่อชิ้นหนึ่ง
# เท่ากับบรรทัดของอีกฝั่งทุกตัวอักษร **และ** อยู่ตรงตำแหน่งนั้นบนภาพ (``structure.split_merged``)
SPLIT_MERGED = _b("ARTWORK_V2_SPLIT_MERGED", "1")
# 7 ต.ค. (รอบ 5 · Friskies): "พื้นที่ยกเว้น" ที่ผู้ใช้วาดในโซน (เช่นช่องพิมพ์ inkjet · ป้ายขนาดของโรงพิมพ์) —
# จุดต่างที่อยู่ในพื้นที่นั้น **ทุกฝั่งที่มีกรอบ** ⇒ ย้ายไปรายการพับ "ยกเว้น" (ไม่ลบ · ไม่นับในผลตัดสิน)
# ``0`` = ไม่รับพื้นที่ยกเว้นจากหน้าเว็บ (เหมือนก่อนมีฟีเจอร์ทุกอย่าง) และซ่อนเครื่องมือ
ZONE_IGNORE = _b("ARTWORK_V2_ZONE_IGNORE", "1")
IGNORE_MAX = 20            # พื้นที่ยกเว้นต่อโซน
IGNORE_COVER = 0.6         # สัดส่วนของกรอบจุดต่างที่ต้องอยู่ในพื้นที่ยกเว้น
VERTICAL_UPRIGHT = _b("ARTWORK_V2_VERTICAL_UPRIGHT", "1")  # ข้อความแนวตั้งไม่ใช่ข้อความโค้ง
QUOTE_PUNCT = _b("ARTWORK_V2_QUOTE_PUNCT", "1")          # ต่างแค่ " ' | # * = เครื่องหมาย

# ── หลักฐานภาพ (7 ต.ค. · ``artwork_v2/pixverify.py``) — PDF ↔ PDF เท่านั้น ─────────────
# เรนเดอร์ไฟล์ต้นฉบับใหม่ในเครื่องรอบทุกจุดต่าง (ไม่ยิง Vision/Gemini) · หมึกเหมือนกันทุกพิกเซล ⇒
# ย้ายไปรายการพับพร้อมภาพหลักฐาน (ไม่ลบ) · ภาพต่าง ⇒ คงไว้ + ป้าย · ``0`` = เดิมเป๊ะ
PIXEL_VERIFY = _b("ARTWORK_V2_PIXEL_VERIFY", "1")
# บังคับให้ "ทั้งบรรทัด OCR" ของทั้งสองฝั่งเหมือนกันด้วย (กันกรอบที่ Vision วางผิดคำ)
PIXEL_LINE_MODE = _b("ARTWORK_V2_PIXEL_LINE_MODE", "1")
PIXEL_TIME_BUDGET_S = _f("ARTWORK_V2_PIXEL_TIME_BUDGET_S", "180")
# 7 ต.ค. (รอบ 5 · Friskies): โซนที่เป็น "ภาพสแกนล้วน" (ภาพเดียวคลุม ≥ 90% ของโซน + ไม่มี
# ข้อความ/เส้นเวกเตอร์ในโซนเลย) ⇒ เทียบที่ความละเอียดจริงของภาพนั้น + เบลอ σ 1 px ทั้งสองฝั่ง
# (เรนเดอร์ 1600 dpi จากภาพ 300 dpi = ขยายจุดรบกวน JPEG/การสแกนจนทุกจุด "ตรวจไม่ได้") ·
# PDF เวกเตอร์ไม่เข้าเงื่อนไขนี้เลย ⇒ ผลเดิมทุกไบต์ · ``0`` = เดิมเป๊ะ
PIXEL_RASTER = _b("ARTWORK_V2_PIXEL_RASTER", "1")

# 7 ต.ค. (ข้อสรุปทีมวิเคราะห์ Log ทุกชุด): AI ที่เห็นแค่ข้อความตอบ "ต่างจริง" กับสัญญาณรบกวน 14 ครั้ง
# · โหมด judge ทำของจริงหาย 3 จุด · บน John West "real" ถูกแค่ 1/11 ⇒ ค่าเริ่มต้น **ปิด**
# (ตั้ง ``ARTWORK_V2_AI_MODE=assist`` = แบบเดิม)
AI_MODES = ("assist", "judge", "raw", "off")
AI_MODE = os.getenv("ARTWORK_V2_AI_MODE", "off").strip().lower()
if AI_MODE not in AI_MODES:
    AI_MODE = "off"
# โหมด judge / raw ซ่อนจากหน้าเว็บ (ทำของจริงหายบนสถานี) · ``1`` = แสดงเหมือนเดิม · API ยังรับค่าได้
AI_EXPERIMENTAL_MODES = _b("ARTWORK_V2_AI_EXPERIMENTAL_MODES", "0")
AI_REVIEW_URL = os.getenv("ARTWORK_V2_AI_REVIEW_URL",
                          "http://127.0.0.1:5678/webhook/artwork-v2-review").strip()
AI_TIMEOUT_S = _f("ARTWORK_V2_AI_TIMEOUT_S", "180")
# โหมด raw ใช้ **workflow แยก** (artwork_v2/n8n_artwork_v2_raw.workflow.json · path artwork-v2-raw)
# — workflow artwork-v2-review เดิมไม่ถูกแตะ · ยังไม่ได้ Import ⇒ N8N ตอบ 404 ⇒ ใช้ผลอัลกอริทึม + คำเตือน
AI_RAW_URL = os.getenv("ARTWORK_V2_AI_RAW_URL",
                       "http://127.0.0.1:5678/webhook/artwork-v2-raw").strip()
# โหมด raw ให้ Gemini คิดนานขึ้น (thinkingBudget สูงสุด) ⇒ รอนานกว่า (node HTTP ของ N8N ตั้ง 290 วิ)
AI_RAW_TIMEOUT_S = _f("ARTWORK_V2_AI_RAW_TIMEOUT_S", "300")
AI_RETRIES = _i("ARTWORK_V2_AI_RETRIES", "1")
# โหมด judge: จุดที่ต่างแค่เครื่องหมายวรรคตอน ⇒ เหลืองเสมอ (กติกาเดียวกับอัลกอริทึม —
# PUNCT แดงได้เฉพาะเมื่อการอ่านซ้ำยืนยัน · คำตอบของ AI ไม่ใช่การอ่านซ้ำ) · 0 = แดงได้แบบเดิม
AI_JUDGE_PUNCT_YELLOW = _b("ARTWORK_V2_AI_JUDGE_PUNCT_YELLOW", "1")
# ── ชั้นตรวจคำตอบของ AI (6 ต.ค. — ทุกตัว 0 = พฤติกรรมเดิมเป๊ะ) ──
# AI อ้างรหัสคำคลาด (นับคำผิดในบรรทัดยาว) แต่ "ยกข้อความมาถูก" ⇒ หาคำจากข้อความที่ยกมา
# ในบรรทัดเดียวกันที่อ้าง (ตรงทั้งคำ · ห่างจากที่อ้างไม่เกิน MAX_SHIFT คำ · ต้องเจอตำแหน่งเดียว)
# หรือเป็นส่วนหนึ่งของคำที่อ้าง (เจอครั้งเดียว) — ไม่เดา: หาไม่เจอ/กำกวม ⇒ ปฏิเสธเหมือนเดิม
AI_QUOTE_RECOVER = _b("ARTWORK_V2_AI_QUOTE_RECOVER", "1")
AI_QUOTE_RECOVER_MAX_SHIFT = _i("ARTWORK_V2_AI_QUOTE_RECOVER_MAX_SHIFT", "2")
# ข้อความที่ AI อ้างสองฝั่ง "เท่ากันตามกติกาเทียบของระบบ" (ช่องว่าง · จำนวนจุดไข่ปลา ·
# ®/Ⓡ · ½/1/2 · ขีดคนละแบบ) ⇒ นับเป็นสัญญาณรบกวน ไม่ใช่การอ้างผิด
AI_EQUIV_NOISE = _b("ARTWORK_V2_AI_EQUIV_NOISE", "1")
# ส่งธง "curved" (ข้อความโค้ง/เอียง) ของแต่ละบรรทัดไปให้ AI
AI_SEND_CURVED = _b("ARTWORK_V2_AI_SEND_CURVED", "1")
# judge: จุดแดงของอัลกอริทึมที่ AI ไม่ได้ระบุ ⇒ คงไว้ในตารางเป็นเหลือง (เดิมพับลง algo_only)
AI_JUDGE_KEEP_ALGO_RED = _b("ARTWORK_V2_AI_JUDGE_KEEP_ALGO_RED", "1")
# judge: AI บอก "noise" กับตัวอักษร/ตัวเลข/ตัวพิมพ์ที่ต่าง ทั้งที่ Vision อ่านชัด (≥ CONF_FAIL
# ทั้งสองฝั่ง · ไม่ใช่ข้อความโค้ง) ⇒ คงไว้เป็นเหลือง (เดิมพับทิ้ง)
AI_JUDGE_NOISE_GUARD = _b("ARTWORK_V2_AI_JUDGE_NOISE_GUARD", "1")
# judge: AI บอก "real" บนข้อความโค้ง/เอียง ⇒ เหลือง (กติกาเดียวกับอัลกอริทึม: แดงได้เฉพาะเมื่ออ่านซ้ำยืนยัน)
AI_JUDGE_CURVED_YELLOW = _b("ARTWORK_V2_AI_JUDGE_CURVED_YELLOW", "1")
# judge: AI บอก "real" กับข้อความที่มีอยู่ "ฝั่งเดียว" (หายไป/เกินมา) ⇒ ใช้กติกาเดียวกับอัลกอริทึม:
#   บรรทัดที่อัลกอริทึมพบว่าแค่ตัดบรรทัดคนละที่ (reflow — ข้อความนี้มีอยู่ในอีกฝั่ง) หรือ
#   ข้อความสั้นมาก (< 2 ตัว) / เครื่องหมายล้วน ⇒ เหลือง ไม่ใช่แดง
#   (ฝั่งที่ "ไม่มี" ไม่มีความมั่นใจของ Vision ให้วัด — % ที่เห็นมาจากฝั่งที่มีข้อความเท่านั้น)
AI_JUDGE_ONESIDED_GUARD = _b("ARTWORK_V2_AI_JUDGE_ONESIDED_GUARD", "1")
# raw: กติกาความปลอดภัยที่อิง "หลักฐานของ Vision" ล้วน (ไม่ใช้ผลของอัลกอริทึม):
#   ต่างแค่เครื่องหมายวรรคตอน ⇒ เหลือง · หายไป/เกินมาฝั่งเดียวที่สั้นมาก/เครื่องหมายล้วน ⇒ เหลือง ·
#   AI บอก noise กับตัวอักษร/ตัวเลขที่ Vision อ่านชัดทั้งสองฝั่ง ⇒ คงไว้เป็นเหลือง (ไม่พับ)
#   0 = AI ตัดสินล้วน (เหลือแค่เกณฑ์ % ของ Vision: real + ≥ CONF_FAIL = แดง)
AI_RAW_SAFETY = _b("ARTWORK_V2_AI_RAW_SAFETY", "1")

# วิธีวาดกรอบจุดต่างบนหน้าเว็บ (แสดงผลล้วน — ไม่แตะผลตรวจ/Log/ผลตัดสิน)
#   word = เส้นบาง 1.25 px รอบ "คำเต็ม" เผื่อห่างตัวอักษร 22% ของความสูงคำ + แถบสีโปร่งบนตัวอักษรที่ต่าง
#   span = แบบเดิม (เส้น 3 px รอบเฉพาะตัวอักษรที่ต่าง เผื่อ 3 px ของภาพ — ย่อภาพแล้วเส้นทับตัวหนังสือ)
BOX_STYLE = "span" if os.getenv("ARTWORK_V2_BOX_STYLE", "word").strip().lower() == "span" else "word"

# ชี้เมาส์ที่แถวในตารางจุดต่าง ⇒ ภาพทั้ง 🅰/🅱 ซูมเข้าไปที่กรอบของจุดนั้นอย่างนุ่มนวล · เอาเมาส์ออก ⇒ ซูมออก
# คลิกแถว = ค้างการซูมไว้ (คลิกซ้ำ/Esc = ปล่อย) · แสดงผลล้วน (ไม่แตะผลตรวจ/Log/ผลตัดสิน)
#   1 = เปิด · 0 = เหมือนก่อน 4 ต.ค. รอบ 5 ทุกอย่าง (ไม่มีการซูม · คลิกแถว = เลือก + เลื่อนจอไปที่ภาพ)
HOVER_ZOOM = _b("ARTWORK_V2_HOVER_ZOOM", "1")

# รวมจุดต่างที่อยู่ "คู่บรรทัดเดียวกัน" เป็นแถวเดียวในตาราง (เช่น ``Hwy, Irwindale Park`` → ``Hwy Irwindale … USA``
# ได้ 3 จุด ⇒ 1 แถว) · แสดงผลล้วน: ไม่แตะผลตรวจ/ผลตัดสิน/Log/การเรียก Vision/AI · ทุกจุดยังอยู่ครบ
# (เลขจุด · กรอบบนภาพ · ระดับ · หมายเหตุ · คำตอบ AI ของแต่ละจุด) · ระดับของแถว = สมาชิกที่หนักที่สุด
#   1 = เปิด · 0 = ตารางเหมือนก่อน 6 ต.ค. ทุกอย่าง (1 จุด = 1 แถว)
LINE_GROUP = _b("ARTWORK_V2_LINE_GROUP", "1")

# ตารางจุดต่าง (แสดงผลล้วน — ไม่แตะผลตรวจ/ผลตัดสิน/Log/การเรียก Vision/AI):
#   SORT_SEVERITY: 1 = เรียงแดง (ต่าง) ก่อน แล้วเหลือง (ไม่มั่นใจ) · ลำดับเดิมภายในระดับเดียวกัน · 0 = ลำดับเดิม
#   TABLE_ROWS: แสดงกี่แถวแล้วเลื่อนในตาราง (หัวตารางค้างไว้) ⇒ ภาพที่ซูมยังอยู่บนจอ · 0 = ไม่จำกัดแบบเดิม
SORT_SEVERITY = _b("ARTWORK_V2_SORT_SEVERITY", "1")
TABLE_ROWS = max(0, _i("ARTWORK_V2_TABLE_ROWS", "4"))

# หมุนโซนก่อนส่งให้ Vision (7 ต.ค. · King Oscar: artwork วางตะแคง 90° บนหน้า PDF แต่ภาพถ่ายตั้งตรง)
#   ผู้ใช้หมุนจอ/ตั้งมุมของโซนบนหน้าวาดโซน (0/90/180/270 ตามเข็ม) ⇒ ภาพโซนถูกหมุนก่อนส่ง ⇒ ภาพที่ส่ง/ผลบนจอ
#   อยู่ในแนวที่คนอ่าน · โซนที่มุม 0 = ภาพเดิมทุกไบต์
#   1 = รับค่ามุมของโซน · 0 = ไม่สนค่ามุม (เหมือนก่อน 7 ต.ค. ทุกอย่าง)
ZONE_ROTATE = _b("ARTWORK_V2_ZONE_ROTATE", "1")

# ตารางจุดต่างอยู่ขวามือของภาพ 🅰/🅱 (ภาพกับตารางอยู่บนจอพร้อมกัน) · หมายเหตุพับไว้ กดเปิดทีละแถว
#   แสดงผลล้วน (ไม่แตะผลตรวจ/ผลตัดสิน/Log) · 1 = เปิด · 0 = ตารางอยู่ใต้ภาพแบบเดิม
SIDE_TABLE = _b("ARTWORK_V2_SIDE_TABLE", "1")

# เปิดหน้าแล้วพบงานที่ค้างไว้ (localStorage) — แบบเดียวกับโหมด Artwork เดิม:
#   1 = ขึ้นแถบ "💾 พบงานที่ค้างไว้" ให้กด [เปิดต่อ]/[ทิ้ง] ก่อน (ไม่กู้คืนเงียบ ๆ)
#   0 = เปิดงานล่าสุดเองทันที (พฤติกรรมก่อน 4 ต.ค. รอบ 4)
RESTORE_CONFIRM = _b("ARTWORK_V2_RESTORE_CONFIRM", "1")
# อายุของงานค้างที่ยังเสนอให้เปิดต่อ (วัน) — เท่าโหมดเดิม
RESTORE_MAX_AGE_DAYS = _f("ARTWORK_V2_RESTORE_MAX_AGE_DAYS", "7")

# จำนวนบรรทัด OCR ต่อฝั่งที่พิมพ์ลง Log (กัน Log ยาวเกินไปจนก๊อปไม่ได้)
LOG_MAX_LINES = _i("ARTWORK_V2_LOG_MAX_LINES", "400")
