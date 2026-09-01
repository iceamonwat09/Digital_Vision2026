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
# ── ขนาดขั้นต่ำของ crop ที่ "ส่งไป OCR" ───────────────────────────────
# เดิม ocr.read_zone() เรนเดอร์ที่ OCR_DPI คงที่ มีแต่เพดานบน (MAX_SIDE)
# ไม่มีพื้นล่าง ต่างจาก pipeline.zone_crop_jpg() ที่มี CROP_MIN_SIDE อยู่แล้ว
# ⇒ ภาพที่ "คนเห็น" บนการ์ด คมกว่าภาพที่ "OCR ได้รับ".
#
# ผลของช่องว่างนี้วัดได้จริง: artwork ที่ถูกย่อลง A4 (proof แบบที่โรงพิมพ์
# ส่งมาเป็นส่วนใหญ่) ทำให้บล็อก 253 คำเรนเดอร์ได้แค่ 231x337 px = ตัวอักษร
# สูง ~9 px แล้ว OCR อ่านได้ 1.2%. เรนเดอร์ใหม่ให้ด้านยาวถึง 1200 px
# (ตัวอักษร ~31 px) อ่านได้ 97.6% — เนื้อหาเดียวกัน engine เดียวกัน.
#
#   สูงตัวอักษร  9.0 px -> recall  1.2%     22.1 px -> 98.5%
#               12.4 px -> recall 23.2%     31.0 px -> 100%
#               17.2 px -> recall 93.9%
#
# ใช้ค่าเดียวกับ CROP_MIN_SIDE (1200) เพราะเป็นค่าที่ repo พิสูจน์มาแล้ว
# และวัดซ้ำในรอบนี้ว่าให้ผลเต็ม. ตั้ง 0 = ปิด = พฤติกรรมเดิมเป๊ะ (rollback).
# ⚠ เฉพาะ PDF เท่านั้น — ไฟล์ภาพไม่มีรายละเอียดเพิ่มให้ดึง การขยายจะได้
#   แค่ภาพเบลอ จึงไม่ทำ (เหมือนที่ zone_crop_jpg ทำเฉพาะ is_pdf).
OCR_CROP_MIN_SIDE = int(os.getenv("ARTWORK_OCR_CROP_MIN_SIDE", "1200"))
# เพดานการคูณ DPI กันเรนเดอร์หนักเกินไปบนโซนจิ๋ว (เท่ากับ zone_crop_jpg)
OCR_DPI_MAX_FACTOR = float(os.getenv("ARTWORK_OCR_DPI_MAX_FACTOR", "4.0"))

# ── Embedded PDF text ────────────────────────────────────────────────
# When a zone contains at least this many embedded-text characters the
# pipeline uses the PDF text layer instead of OCR (exact, free, offline).
# Outlined artwork (the common case) has no text layer and falls back
# to OCR automatically.
EMBEDDED_TEXT_MIN_CHARS = int(os.getenv("ARTWORK_EMBEDDED_MIN_CHARS", "12"))
# ── ด่านคุณภาพของ text layer (ไม่ใช่แค่ปริมาณ) ────────────────────────
# EMBEDDED_TEXT_MIN_CHARS วัดแค่ว่า "มีข้อความกี่ตัว" แต่ไม่ได้ถามว่า
# "ข้อความนั้นใช้ได้ไหม". ไฟล์จริงที่เจอ: artwork ที่ถูกย่อ/แปลงมา ฟอนต์
# subset แมปอักขระผิด ทำให้ get_text() คืนสิ่งที่หน้าตาเป็นคำแต่ไม่ใช่คำ
# ("PR3374Y0KOI", "8YCGOQMUWIQVEOKEPEVIXGISKLAD", "340CKOVE") — แล้วชั้น
# pdf-text ก็ส่งต่อด้วย conf 1.0 โดยไม่มีการตรวจสอบใด ๆ ⇒ ข้อความมั่วไหล
# เข้าชั้นตัดสิน PASS/FAIL ด้วยความมั่นใจสูงสุด (ผิดกฎเหล็กข้อ 2 ตรง ๆ).
#
# เปิดไว้ = ถ้าจับได้ว่าเสีย ให้ตกไปใช้ OCR แทน (ซึ่งอ่านภาพจริง) และถ้า
# ไม่มี OCR ให้ติดธง error เพื่อให้กลายเป็น UNREADABLE = ขอให้คนดู.
# ตั้ง 0 = ปิด = พฤติกรรมเดิมเป๊ะ (rollback).
PDFTEXT_GARBLED_CHECK = os.getenv(
    "ARTWORK_PDFTEXT_GARBLED_CHECK", "1").strip().lower() not in ("0", "false", "")
# เกณฑ์ตัดสิน "เสีย" — วัดจากไฟล์จริง 35 บล็อก (5 ไฟล์) เทียบกับบล็อกเดียว
# กันที่จำลองการเสียแบบ glyph-index mapping 29 บล็อก:
#     min_long=8  -> ฟ้องผิด 0/35   จับได้ 28/29
#     min_long=20 -> ฟ้องผิด 0/35   จับได้ 14/29
# ค่า ratio 0.20/0.30/0.40 ให้ผลเท่ากันทุกค่า = สองกลุ่มแยกกันขาด ไม่ใช่
# เส้นบาง ๆ ที่ต้องจูน. ต้องมีคำยาวอย่างน้อย min_long คำจึงจะตัดสิน —
# กันรหัสงานพิมพ์ ("5K00D111N000000301", "TUG-15974R04-0-M") ที่มีรูปแบบ
# คล้ายกันแต่มีไม่กี่คำต่อแถบ.
PDFTEXT_GARBLED_MIN_TOKENS = int(
    os.getenv("ARTWORK_PDFTEXT_GARBLED_MIN_TOKENS", "8"))
PDFTEXT_GARBLED_RATIO = float(
    os.getenv("ARTWORK_PDFTEXT_GARBLED_RATIO", "0.30"))
# ── ด่านที่สอง: อักขระที่ "เป็นไปไม่ได้ในข้อความจริง" ────────────────────
# เกณฑ์ ratio ข้างบนเดิมพันไว้กับลายเซ็นเดียว — "ตัวเลขแทรกกลางคำ" ซึ่งเป็น
# แค่ *ผลลัพธ์หนึ่ง* ที่เป็นไปได้ของ ToUnicode CMap พัง ไม่ใช่ลายเซ็นที่แน่นอน
# ของมัน. วัดจากไฟล์จริง 2 ไฟล์ (Cosma Schlemmerbox ฉบับ Original vs ฉบับ A4)
# ที่มีเนื้อหาเดียวกันเป๊ะและใช้ฟอนต์ชื่อเดียวกัน (DINPro-Bold) แต่ subset
# คนละชุด:
#
#   Original : "DOPLŇKOVÉ KRMIVO PRO DOSPĚLÉ KOČKY"        ← ถูกต้อง 100%
#   A4       : "(340ĊKOVÉ KRMIVO PR3\x04(374Ý0\x8c\x04KOÏ/=" ← พัง
#
# ⇒ ตัวตรวจ ratio เดิม **ไม่ฟ้อง** เพราะ token ที่พังมีแค่ 2 จาก 14 คำยาว
#   (14.3% < 30%) และหนึ่งใน token ที่พัง ("PEGMJMGOÀ\x04XYċ¤O\x04Z\x04ŁIlé")
#   **ไม่มีตัวเลขอยู่เลย** จึงไม่มีทางถูกจับด้วยกฎ "ตัวเลขกลางคำ" ไม่ว่าจะ
#   ตั้ง ratio ต่ำแค่ไหน.
#
# ด่านนี้จับที่ *ชนิดของอักขระ* แทน: อักขระควบคุม C0/C1 (\x04, \x8c),
# Private-Use-Area, surrogate, unassigned และ U+FFFD ไม่มีเหตุผลใดที่จะ
# ปรากฏในข้อความที่พิมพ์บนฉลากจริง ⇒ เจอแม้แต่ตัวเดียวก็พอตัดสินได้
# (ไม่ต้องมีสัดส่วน/จำนวนคำขั้นต่ำแบบ ratio ข้างบน ซึ่งจำเป็นเพราะตัวเลข
# ปรากฏในข้อความจริงได้).
# ⚠️ **ต้องไม่รวม category "Cf"** (ZWJ/ZWNJ/RLM/LRM) — ฉลากอาหรับ/ฮีบรูใช้
#    อักขระเหล่านี้ตามปกติ ถ้ารวมเข้าไปจะฟ้องผิดทุกใบ.
# ตั้ง 0 = ปิด = กลับไปมีแต่ด่าน ratio เดิมเป๊ะ (rollback).
PDFTEXT_BAD_GLYPH_CHECK = os.getenv(
    "ARTWORK_PDFTEXT_BAD_GLYPH_CHECK", "1").strip().lower() not in (
        "0", "false", "")
# จำนวนอักขระต้องห้ามขั้นต่ำที่ทำให้ตัดสินว่า "เสีย". 1 = เจอตัวเดียวก็พอ
# (ค่าเริ่มต้น — ความเสี่ยงฟ้องผิดต่ำมากเพราะอักขระกลุ่มนี้ไม่ควรมีเลย
# และราคาของการฟ้องผิดคือ "ตกไปอ่านด้วย OCR" ไม่ใช่ "ตรวจไม่ได้").
PDFTEXT_BAD_GLYPH_MIN_COUNT = int(
    os.getenv("ARTWORK_PDFTEXT_BAD_GLYPH_MIN_COUNT", "1"))

# ── หลักฐานระดับ "ฟอนต์": พังครั้งหนึ่ง = สงสัยทั้งไฟล์ ────────────────
# ด่านสองอันข้างบนตัดสิน **ทีละโซน** จากตัวข้อความ แต่ความเสียหายจริงเกิดที่
# **ฟอนต์** — วัดจากไฟล์ A4 ของ Cosma: `DINPro-Bold` เสีย 66/396 span ส่วนอีก
# 12 ฟอนต์ในไฟล์เดียวกัน **0%**. และฟอนต์ที่พังตัวเดียวกันคายขยะได้ 2 แบบ:
#
#     [span 0] 'DOPLNKOVÉ KRMIVO PRE DOSPELÉ MA'   ← อักขระต้องห้าม 0 ตัว
#     [span 1] 'Ï/='                                ← อักขระต้องห้าม 0 ตัว
#               (ของจริงคือ "ČKY")
#
# span ที่สองเป็นขยะเต็ม ๆ แต่ประกอบด้วยตัวอักษรที่ถูกต้องตาม Unicode ทุกตัว
# ⇒ **ไม่มีกฎเชิงอักขระแบบไหนจับได้** ถ้าดูเฉพาะก้อนนั้นโดด ๆ. ทางออกคือใช้
# หลักฐานจากที่อื่นในไฟล์เดียวกัน: ฟอนต์ที่พิสูจน์แล้วว่าพัง ต้องไม่ถูกเชื่อ
# ในโซนอื่นด้วย.
#
# โหมด (วัดบนไฟล์จริง — ตัวเลขในวงเล็บคือผลของไฟล์ A4):
#   "off"      ไม่ใช้ชั้นนี้เลย = พฤติกรรมก่อน 1 ก.ย. 2026
#   "chars"    ปฏิเสธเฉพาะ span ของฟอนต์ที่พัง ที่มีอักขระซึ่ง **ฟอนต์อื่นที่
#              ไม่มีร่องรอยเสียหายในไฟล์เดียวกันไม่เคยใช้เลย**
#              (จับเพิ่ม 46 span · ฟ้องผิด ~5 = precision ~90%)  ← ค่าเริ่มต้น
#   "nonascii" ปฏิเสธ span ของฟอนต์ที่พังที่มีอักขระ non-ASCII (จับครบแต่
#              ฟ้องผิดมากกว่า — เยอรมัน/ฝรั่งเศสที่ถูกต้องโดนด้วย)
#   "font"     ปฏิเสธทุก span ของฟอนต์ที่พัง (เข้มสุด ~5,100 ตัวอักษรไป OCR)
#
# 🔑 คุณสมบัติด้านความปลอดภัยที่ทำให้กล้าเปิดเป็นค่าเริ่มต้น: ชั้นนี้จะทำงาน
#    **ก็ต่อเมื่อมีฟอนต์ที่ถูกพิสูจน์ด้วยหลักฐานแข็ง (อักขระต้องห้าม) แล้ว
#    เท่านั้น** ⇒ ไฟล์ที่ฟอนต์ปกติทั้งหมด **ไม่ถูกแตะเลยแม้แต่โซนเดียว**
#    (ยืนยันกับไฟล์ Original: ฟอนต์ที่ถูกสงสัย 0 ตัว)
PDFTEXT_FONT_EVIDENCE = os.getenv(
    "ARTWORK_PDFTEXT_FONT_EVIDENCE", "chars").strip().lower() or "off"
# จำนวนหน้าสูงสุดที่ไล่เก็บหลักฐาน (โหมดนี้ตรวจหน้าแรก แต่หลักฐานจากหน้าอื่น
# ใช้ได้และหาได้ถูก ~60 ms/หน้า) — กันไฟล์หนามากทำให้ช้าโดยไม่จำเป็น
PDFTEXT_FONT_EVIDENCE_MAX_PAGES = int(
    os.getenv("ARTWORK_PDFTEXT_FONT_EVIDENCE_MAX_PAGES", "12"))
# ── หลักฐานทางที่ 2: โครงสร้างของไฟล์ (ไม่ต้องรออาการ) ──────────────────
# ฟอนต์ composite (Type0) ที่ encoding เป็น Identity-H/V และ **ไม่มี ToUnicode**
# = ตามสเปก PDF แล้วไม่มีข้อมูลใดในไฟล์ที่บอกได้ว่า glyph นั้นคือตัวอักษรอะไร
# ⇒ ตัวอ่านทุกตัวได้แต่เดา. นี่คือ *ต้นเหตุ* ของอาการทั้งหมดที่ไล่แก้กันมา
#
# วัดเทียบกันบนไฟล์จริง 833 บรรทัด (เสียจริง 70):
#   ด่านอักขระอย่างเดียว              จับ 58 · ฟ้องผิด  0
#   + หลักฐาน "จากอาการ"              จับ 70 · ฟ้องผิด 10
#   + หลักฐาน "จากโครงสร้าง" (ข้อนี้)  จับ 70 · ฟ้องผิด 10  ← เท่ากันเป๊ะ
# ⇒ ข้อนี้ **ไม่ได้เพิ่มการฟ้องผิดเลย** แต่ปิดช่องโหว่เชิงหลักการที่ใหญ่มาก:
#   ถ้าฟอนต์พังโดยบังเอิญคายแต่ตัวอักษรที่ดูปกติ ด่าน "จากอาการ" จะตาบอดสนิท
#   ส่วนข้อนี้ยังจับได้เพราะโครงสร้างไฟล์ไม่เปลี่ยนไปตามอาการ (วัดแล้ว)
# ราคา: อ่านตารางฟอนต์ ~6 ms/ไฟล์. ตั้ง 0 = ปิด = ใช้แต่หลักฐานจากอาการ
PDFTEXT_FONT_STRUCTURE_CHECK = os.getenv(
    "ARTWORK_PDFTEXT_FONT_STRUCTURE_CHECK", "1").strip().lower() not in (
        "0", "false", "")

# ── engine ต้องตรงกันภายในกลุ่มที่เอามาเทียบกัน (opt-in) ─────────────────
# ปัญหา: ถ้าโซน A ในกลุ่มเดียวกันอ่านจาก text layer (เป๊ะระดับ vector) แต่
# โซน B ตกไปใช้ OCR (~98%) การเทียบข้ามแผงจะเจอความต่าง "เชิงโครงสร้าง"
# ของสอง engine (การตัดบรรทัด/ระยะห่าง/ลำดับคอลัมน์) ทับซ้อนกับความต่างจริง
# ⇒ เสี่ยง MISMATCH ปลอม. เปิดค่านี้ = ถ้ากลุ่มไหน engine ปนกัน ให้อ่านโซน
# ที่ใช้ text layer ในกลุ่มนั้นซ้ำด้วย OCR เพื่อให้เทียบกันแบบ apples-to-apples.
#
# ⚠️ **default = ปิด** เพราะยังไม่ได้วัดบนไฟล์จริงว่าคุ้มไหม: มันแลก
#    "ความแม่นระดับตัวอักษรของ text layer" (100%) กับ "ความสม่ำเสมอของ
#    การตัดบรรทัด" และมีราคาเป็นโควตา/เวลา OCR ที่เพิ่มขึ้น. เปิดเมื่อวัดแล้ว
#    ว่าลด MISMATCH ปลอมได้จริงบนงานของตัวเอง.
# ไม่ว่าเปิดหรือปิด รายงานจะบอกเสมอว่ากลุ่มไหน engine ปนกัน (coverage.engine_mix)
OCR_GROUP_ENGINE_CONSISTENCY = os.getenv(
    "ARTWORK_OCR_GROUP_ENGINE_CONSISTENCY", "0").strip().lower() not in (
        "0", "false", "")

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
# พิสูจน์กรอบของ OCR backend ด้วย "แถว" เมื่ออ่านคำนั้นซ้ำไม่ได้ (ภาษาที่ไม่มี
# traineddata เช่นอาหรับบนเครื่องที่ลงแต่ eng). ปิด = กลับพฤติกรรมเดิมคือ
# ไม่วาดกรอบให้ภาษาเหล่านั้นเลย. มีผลเฉพาะคำที่ไม่ใช่ ASCII เท่านั้น.
# ⚠️ ทางที่ดีที่สุดยังคือติดตั้ง traineddata ของภาษานั้นแล้วตั้ง
#    ARTWORK_HIGHLIGHT_TESS_LANG=eng+ara — ตัวนี้เป็นตาข่ายรองรับเมื่อไม่มี.
HIGHLIGHT_ROW_VERIFY = os.getenv(
    "ARTWORK_HIGHLIGHT_ROW_VERIFY", "1").strip().lower() not in ("0", "false", "")
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
# ชี้ 127.0.0.1 เหมือน N8N_OCR_WEBHOOK_URL (N8N รันบนเครื่องสถานีเอง) —
# ⚠️ ต้องแก้ทั้งสองที่เสมอ ถ้าแก้ที่เดียวอีกตัวจะยิงไปเครื่องเก่าแบบเงียบ ๆ
N8N_TRANSLATE_WEBHOOK_URL = os.getenv(
    "N8N_TRANSLATE_WEBHOOK_URL",
    "http://127.0.0.1:5678/webhook/artwork-translate",
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

# ── Pixel diff (เทียบ artwork ฉบับเก่า/ใหม่) — advisory ล้วน ─────────
# ชั้นนี้ **ไม่แตะ defects / verdict / การนับ / DB** และไม่วิ่งตอน
# "ส่งตรวจสอบ" — ผู้ใช้ต้องกดปุ่มเอง. ค่าทั้งหมดวัดมาจากไฟล์ artwork จริง
# 11 ไฟล์บนสถานี (ดู pixdiff_noise_scan.py และหัวข้อ pixel diff ใน CLAUDE.md)
PIXDIFF_ENABLED = os.getenv(
    "ARTWORK_PIXDIFF_ENABLED", "1").strip().lower() not in ("0", "false", "")
# DPI ที่ใช้เทียบ. 200 = จับความต่างขนาดตัวอักษรเดียว (1x1 mm = 62 px²) ได้
# ครบทุกไฟล์. ถ้าต้องจับจุดทศนิยม/® (0.6x0.6 mm = 22 px² ที่ 200 DPI ซึ่ง
# เล็กกว่า MIN_REGION_PX) ให้ขึ้นเป็น 300 — ช้าขึ้น ~2.25 เท่า
PIXDIFF_DPI = int(os.getenv("ARTWORK_PIXDIFF_DPI", "200"))
# เพดานเวลาต่อการเทียบหนึ่งครั้ง (วินาที) — ไฟล์ใหญ่สุดที่วัดได้ใช้ ~4 วิ
PIXDIFF_TIMEOUT_S = float(os.getenv("ARTWORK_PIXDIFF_TIMEOUT_S", "120"))

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
