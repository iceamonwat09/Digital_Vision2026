"""
Configuration for the Artwork Proof Check mode.

All data lives under ``data/artwork_check/`` so the mode can be deleted
wholesale without touching any other feature.
"""

import os

MODE_NAME = "artwork_check"
DISPLAY_NAME = "Artwork Proof Check"

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = os.path.join(_ROOT, "data", "artwork_check")
INSPECTIONS_DIR = os.path.join(DATA_DIR, "inspections")
TEMPLATES_DIR = os.path.join(DATA_DIR, "zone_templates")
VOCAB_DIR = os.path.join(DATA_DIR, "vocab")

for _d in (INSPECTIONS_DIR, TEMPLATES_DIR, VOCAB_DIR):
    os.makedirs(_d, exist_ok=True)

# ── Rendering ────────────────────────────────────────────────────────
# Preview shown in the browser zone editor. Kept light so the page loads
# fast even for A3 print masters.
PREVIEW_DPI = int(os.getenv("ARTWORK_PREVIEW_DPI", "150"))
# Resolution used for the per-zone crops that are sent to OCR. Small
# ingredient text and Arabic need this much.
OCR_DPI = int(os.getenv("ARTWORK_OCR_DPI", "450"))
# Hard cap on a single OCR crop (pixels, longest side) so N8N webhook
# payloads stay under typical body-size limits.
OCR_CROP_MAX_SIDE = int(os.getenv("ARTWORK_OCR_CROP_MAX_SIDE", "3000"))

# ── Embedded PDF text ────────────────────────────────────────────────
# When a zone contains at least this many embedded-text characters the
# pipeline uses the PDF text layer instead of OCR (exact, free, offline).
# Outlined artwork (the common case) has no text layer and falls back
# to OCR automatically.
EMBEDDED_TEXT_MIN_CHARS = int(os.getenv("ARTWORK_EMBEDDED_MIN_CHARS", "12"))

# ── Checks ───────────────────────────────────────────────────────────
# Languages tried by the dictionary layer. A word passes if it is valid
# in ANY enabled language or in the brand vocabulary — labels mix
# languages on one panel, so per-word language detection is not assumed.
SPELL_LANGUAGES = [
    s.strip() for s in os.getenv(
        "ARTWORK_SPELL_LANGS", "en,es,fr,de,pt,it,ru,ar"
    ).split(",")
    if s.strip()
]
# Words shorter than this are skipped by the dictionary layer
# (unit symbols, abbreviations — too noisy to check).
SPELL_MIN_WORD_LEN = int(os.getenv("ARTWORK_SPELL_MIN_LEN", "4"))

# Relative tolerance for weight arithmetic (count × unit = total,
# lbs ↔ kg). Printed values are rounded to 2 decimals, so allow the
# rounding slack plus a little.
WEIGHT_REL_TOLERANCE = float(os.getenv("ARTWORK_WEIGHT_TOL", "0.006"))

# Fuzzy window for approved-phrase matching: a phrase counts as
# "present but misspelled" when the best window is within this many
# character edits (but not zero).
PHRASE_MAX_EDITS = int(os.getenv("ARTWORK_PHRASE_MAX_EDITS", "3"))

# ── Cross-file auto-pair (opt-in UI helper — never runs unless clicked) ─
# "วาดกรอบครั้งเดียวบนไฟล์ A → หา + สร้างกรอบคู่บนไฟล์ B อัตโนมัติ".
# ใช้ cv2.matchTemplate หา content block เดียวกันของโซน A บนไฟล์ B เพื่อ
# การันตีว่ากรอบคู่ครอบเนื้อหาเดียวกันเป๊ะ (ตัดต้นตอ false-mismatch ของ
# cross-file). เป็น helper ตอนจัดโซนเท่านั้น — ไม่แตะ OCR/ผลตรวจ/การนับ.
#
# ผลจับคู่ที่ conf ต่ำกว่าเกณฑ์นี้ = "หาไม่เจอ" → ไม่สร้างกรอบ (เตือนให้
# วาดเอง). กันการแอบสร้างกรอบผิดเงียบๆ. ไฟล์จริงที่ทดสอบ = 0.93–0.99;
# เคสจับคู่ผิด/ไม่มีบล็อกนั้น = ~0.39. 0.5 = ขอบเขตที่แยกได้สะอาด.
AUTOPAIR_MIN_CONF = float(os.getenv("ARTWORK_AUTOPAIR_MIN_CONF", "0.5"))
# ช่วงสเกลที่ลอง (matchTemplate ไม่ทน scale เอง) เผื่อไฟล์ A/B render
# คนละสเกลเล็กน้อย. list ว่าง = ลองสเกล 1.0 อย่างเดียว (เร็วสุด).
AUTOPAIR_SCALES = [
    float(s) for s in os.getenv(
        "ARTWORK_AUTOPAIR_SCALES",
        "0.85,0.90,0.95,1.0,1.05,1.10,1.18",
    ).split(",")
    if s.strip()
]

# ── Defect-card word highlight (display-only) ────────────────────────
# วาดกรอบแดงที่ "คำที่มีปัญหา" บนรูป crop ของการ์ด "รายการที่พบ".
# แสดงผลอย่างเดียว 100% — ไม่แตะ OCR/ผลตรวจ/verdict/การนับ. หาตำแหน่งคำ
# จาก OCR blocks bbox (ถ้ามี) → ไม่มีก็ใช้ projection profile (deterministic).
# หาไม่เจอ/ไม่มั่นใจ = ไม่วาด (ครอปเดิม). ตั้ง False = ปิดฟีเจอร์ทันที
# (rollback) ครอปกลับเป็นภาพเปล่าเหมือนเดิม.
HIGHLIGHT_DEFECT_WORD = os.getenv(
    "ARTWORK_HIGHLIGHT_DEFECT", "1").strip().lower() not in ("0", "false", "")
# วิธีหาตำแหน่งคำ (เรียงตามความแม่นจาก benchmark: bbox → tesseract → profile):
#  - ชั้น OCR blocks bbox: ใช้เสมอเมื่อ backend คืน bbox (ไม่ต้องตั้งค่า)
#  - Tesseract (local): แม่นสุด (~89% hit/IoU 0.95 ใน benchmark) แต่ต้องติดตั้ง
#    tesseract binary + `pip install pytesseract` บนสถานี. default เปิด แต่ถ้า
#    ไม่มี binary/lib จะข้ามเงียบ → ไม่มีกรอบ (ไม่ error).
#  - Projection profile: ไม่ต้องพึ่งอะไร แต่วาดผิดคำ ~40% (อันตรายกับ QC) →
#    default ปิด เปิดเป็น last-resort เท่านั้นถ้ายอมรับความเสี่ยง.
# ชั้น ② PDF text-layer word box: ถ้าโซนอ่านจาก text layer ของ PDF
# (engine=pdf-text) ดึงกรอบคำจาก PDF ตรงๆ — เป๊ะระดับ vector, ไม่ต้อง OCR,
# รองรับทุกภาษา (ฮีบรู/อาหรับ/จีน/ไทย) โดยไม่ต้องลง traineddata. รันก่อน
# Tesseract. default เปิด; ตั้ง 0 = ข้ามชั้นนี้ (กลับไปใช้ OCR/Tesseract).
HIGHLIGHT_USE_PDF_TEXT = os.getenv(
    "ARTWORK_HIGHLIGHT_PDF_TEXT", "1").strip().lower() not in ("0", "false", "")
HIGHLIGHT_USE_TESSERACT = os.getenv(
    "ARTWORK_HIGHLIGHT_TESSERACT", "1").strip().lower() not in ("0", "false", "")
HIGHLIGHT_USE_PROFILE = os.getenv(
    "ARTWORK_HIGHLIGHT_PROFILE", "0").strip().lower() not in ("0", "false", "")
HIGHLIGHT_TESSERACT_LANG = os.getenv("ARTWORK_HIGHLIGHT_TESS_LANG",
                                     "eng").strip() or "eng"
# จำนวนกรอบสูงสุดต่อ 1 defect: คำผิดมักพิมพ์ซ้ำหลายแถวในตารางเดียวกัน
# (เช่น "Cude" ใน Cude Protein / Cude Fat / Cude Fiber) — วาดจุดเดียวทำให้
# ผู้ตรวจแก้ไม่ครบ. 6 = เห็นครบทุกจุดในตารางปกติ แต่ไม่ท่วมรูปถ้าคำนั้น
# ปรากฏเยอะผิดปกติ. ตั้ง 1 = พฤติกรรมเดิม (กรอบเดียว), 0 = ไม่จำกัด.
HIGHLIGHT_MAX_BOXES = int(os.getenv("ARTWORK_HIGHLIGHT_MAX_BOXES", "6"))
# ขนาดด้านยาวขั้นต่ำของภาพ crop ในการ์ด defect. โซนเล็กเรนเดอร์ที่ OCR_DPI แล้ว
# ได้ภาพเล็กมาก (โซนกว้าง 78pt ที่ 450dpi = ~490px) ซึ่ง (ก) คนอ่านไม่ออก และ
# (ข) Tesseract ตาบอด — วัดจาก crop จริงของสถานี: 488px หาคำเจอ 0/8 แต่ 976px
# เจอ 6/8. ถ้าเป็น PDF จะเรนเดอร์ใหม่ที่ DPI สูงขึ้น (ได้รายละเอียดจริง ไม่ใช่
# การขยายภาพ); ถ้าเป็นรูปถ่ายจะขยายในหน่วยความจำเฉพาะตอน OCR เท่านั้น.
CROP_MIN_SIDE = int(os.getenv("ARTWORK_CROP_MIN_SIDE", "1200"))

# ── Translation (advisory tab — separate from OCR & checks) ──────────
# Optional N8N webhook that translates the already-OCR'd text to English
# for the read-only "ข้อความ + คำแปล" tab. It is NEVER used by the check
# layers and NEVER affects the PASS/FAIL verdict — it only helps a human
# read a foreign-language label. Leave empty to disable the tab's
# translate button (the table still shows source text + spell hints).
N8N_TRANSLATE_WEBHOOK_URL = os.getenv(
    "N8N_TRANSLATE_WEBHOOK_URL",
    "http://172.32.201.106:5678/webhook/artwork-translate",
).strip()
N8N_TRANSLATE_TIMEOUT_S = float(os.getenv("N8N_TRANSLATE_TIMEOUT_S", "60"))
# จำนวนบรรทัดสูงสุดต่อ 1 request แปล/AI-spell. ลิสต์ยาว (เทียบ 2 ไฟล์ =
# ~140 บรรทัด) ทำให้ Gemini ตอบ array เหลื่อม/ไม่ครบ → คอลัมน์ AI เพี้ยน.
# 30 = ขนาดที่พิสูจน์จากการใช้งานจริงว่านิ่ง (ยุคตรวจ 1-2 โซน).
# ตั้ง 0 = ส่งก้อนเดียวทั้งหมดแบบเดิม (ปุ่ม rollback).
TRANSLATE_CHUNK_LINES = int(os.getenv("ARTWORK_TRANSLATE_CHUNK_LINES", "30"))

# ── ประวัติการตรวจ: ใครเห็นของใคร ───────────────────────────────────
# True  = ผู้ใช้เห็นเฉพาะการตรวจที่ตัวเองเป็นคนอัปโหลด (ยกเว้น role ใน
#         HISTORY_ADMIN_ROLES ที่เห็นทั้งหมด)
# False = ทุกคนเห็นทุกบันทึก = พฤติกรรมก่อนมีฟีเจอร์นี้ 100% (ปุ่ม rollback)
# ⚠️ ไม่ว่าตั้งค่าไหน ถ้า AUTH_ENABLED=False (ไม่มีระบบล็อกอิน) จะไม่กรอง
#    เลย — ไม่งั้นหน้าประวัติจะว่างเปล่าเพราะไม่มีตัวตนผู้ใช้ให้เทียบ.
HISTORY_PER_USER = os.getenv(
    "ARTWORK_HISTORY_PER_USER", "true").strip().lower() not in ("0", "false", "no")
# ชื่อ role ที่เห็นประวัติของทุกคน. เก็บเป็นค่าตั้งไม่ใช่ hard-code เพื่อให้
# เพิ่ม role (เช่น "Manager") ได้โดยไม่ต้องแก้โค้ด.
# ⚠️ ผูกกับ "ชื่อ" role: ถ้ามีคนเปลี่ยนชื่อ role Admin ในหน้าจัดการผู้ใช้
#    สิทธิ์นี้จะหยุดทำงานเงียบ ๆ — ต้องมาแก้ค่านี้ให้ตรงกัน.
HISTORY_ADMIN_ROLES = tuple(
    r.strip() for r in os.getenv("ARTWORK_HISTORY_ADMIN_ROLES", "Admin").split(",")
    if r.strip()
)

# ── Defect classes (severity drives the verdict) ─────────────────────
#   critical → FAIL, warning → REVIEW, info → shown only
DEFECT_CLASSES = {
    "MISMATCH_PANELS": {"severity": "critical",
                        "label": "ข้อความไม่ตรงกันระหว่าง panel"},
    "MISMATCH_ZOOM":   {"severity": "critical",
                        "label": "ส่วน zoom ไม่ตรงกับฉลากจริง"},
    "NUMBER_FAIL":     {"severity": "critical",
                        "label": "ตัวเลข/น้ำหนัก/check digit ไม่ถูกต้อง"},
    "PHRASE_FAIL":     {"severity": "critical",
                        "label": "วลีมาตรฐานแบรนด์สะกดไม่ตรงกับที่ approve"},
    "SPELL_FAIL":      {"severity": "warning",
                        "label": "คำไม่อยู่ใน dictionary (ต้องให้คนยืนยัน)"},
    "UNREADABLE":      {"severity": "warning",
                        "label": "OCR อ่านไม่ชัด — ขอให้คนตรวจดูจุดนี้เอง"},
}
