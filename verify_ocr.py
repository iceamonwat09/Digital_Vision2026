"""
verify_ocr.py — วัดว่า "เครื่องถอดความ" แต่ละตัวแม่นแค่ไหนบน artwork จริง
ก่อนตัดสินใจย้ายสถาปัตยกรรม OCR ของโหมด Artwork Proof Check.

ทำไมต้องมีไฟล์นี้
-----------------
โหมด Artwork ใช้ Gemini (ผ่าน N8N) เป็นตัวถอดความหลัก ซึ่งเป็นโมเดล generative
จึงมีโอกาส "แต่งข้อความที่ไม่มีอยู่จริง" (hallucination) แบบเงียบ ๆ — ความผิด
พลาดชนิดเดียวกับกับดัก OpenVINO ที่ repo นี้เคยเจอ: โค้ดรันไม่ error แต่ผลผิด.
สคริปต์นี้คือตาข่ายนิรภัยแบบเดียวกับ verify_onnx.py: วัดด้วยตัวเลขบนไฟล์จริง
ก่อนเชื่อ ไม่ใช่เชื่อเพราะ "ดูแล้วน่าจะดี".

**ห้ามแตะ pipeline** — สคริปต์นี้อ่านอย่างเดียว ไม่เขียนอะไรลง data/ ไม่แก้
report.json ไม่สร้าง inspection ใหม่. รันตอนไหนก็ได้ ไม่กระทบงานที่ค้างอยู่.

หลักการวัด
----------
PDF ที่ยังมี text layer ให้ "เฉลยฟรี": ข้อความที่ฝังอยู่คือสิ่งที่พิมพ์จริง
100% (ระดับ vector ไม่ใช่การอ่านภาพ). จึงใช้เป็น ground truth วัดเครื่องอื่น
ได้โดยไม่ต้องให้คนมานั่งเทียบ.

  ชั้น 1  TRIAGE       ไฟล์นี้มี text layer จริงไหม ครอบคลุมกี่ % ของหน้า
                       (จับเคส "แบนเนอร์ขึ้น ✓ แต่ใช้ไม่ได้จริง")
  ชั้น 2  GROUND TRUTH เทียบทุก engine กับ text layer → recall / precision
  ชั้น 3  NO-TEXT      ส่งโซนที่ "ไม่มีข้อความแน่นอน" เข้า engine
                       → อะไรที่คืนกลับมา = hallucination ล้วน ๆ
  ชั้น 4  CONSISTENCY  โซนเดิม เรนเดอร์ 2 DPI → engine ตอบตรงกันไหม
                       (ใช้ได้กับไฟล์ outline ที่ไม่มีเฉลย)

วิธีใช้ (รันบนเครื่องสถานี)
---------------------------
    py -3.9 verify_ocr.py --files "C:\\artwork\\*.pdf"
    py -3.9 verify_ocr.py --files a.pdf b.pdf --engines tesseract
    py -3.9 verify_ocr.py --files a.pdf --engines tesseract ^
                          --tess-lang eng+ara+tha+chi_tra
    py -3.9 verify_ocr.py --files a.pdf --engines tesseract,n8n --n8n-limit 12
    py -3.9 verify_ocr.py --files TEST --engines n8n --layers probe --n8n-limit 20

  หมายเหตุ: PDF text layer ไม่ใช่ "engine" ที่เลือกได้ — มันคือ *เฉลย*
  ที่ใช้วัด engine อื่น จึงถูกใช้อัตโนมัติเสมอเมื่อไฟล์นั้นมี

⚠ ญี่ปุ่น: ต้องระบุ psm ให้ถูกด้วย ไม่ใช่แค่ภาษา
------------------------------------------------
ฉลากญี่ปุ่นมีทั้งแนวนอนและ **แนวตั้ง (縦書き)** ซึ่งตัวอักษรเรียงลงมาโดยยัง
ตั้งตรง — ไม่ใช่ข้อความแนวนอนที่ถูกหมุน. วัดกับข้อความแนวตั้งชุดเดียวกัน
(ความแม่นระดับตัวอักษร) ได้ผลแกว่งระหว่าง 0% กับ 100% ตามคู่ lang x psm:

    jpn_vert  --tess-psm 5   ไม่หมุน   100%   <- ใช้อันนี้
    jpn_vert  --tess-psm 6   ไม่หมุน     0%
    jpn       --tess-psm 6   ไม่หมุน    89%
    jpn       --tess-psm 5   ไม่หมุน     0%
    jpn_vert  --tess-psm 6   หมุน 270  100%
    jpn       (psm ใดก็ตาม)  หมุน 270    0%   <- กับดัก

กับดัก: detect_orientation() ของ pdf_ingest มองบล็อกสูง-แคบว่า "vertical"
แล้ว resolve_rotation() หมุน 270 องศาให้ — ถูกสำหรับข้อความอังกฤษ/อาหรับที่
พิมพ์ตะแคงบนข้างกล่อง แต่ถ้าใช้ภาษา `jpn` กับข้อความญี่ปุ่นแนวตั้งที่ถูกหมุน
จะได้ 0%. ต้องใช้ `jpn_vert` เท่านั้นในเคสนั้น.

แนวนอน: `jpn+eng` (87%) ดีกว่า `jpn` ล้วน (83%) เพราะฉลากปนตัวเลข/หน่วย
ละติน — `jpn` ล้วนอ่าน "70g" เป็น "799".

⚠ ตัวเลขข้างบนวัดจากข้อความที่เรนเดอร์ด้วยฟอนต์ IPAGothic ไม่ใช่ artwork
ญี่ปุ่นจริง จึงเป็น "เพดานบน" — ฟอนต์ตกแต่ง/พื้นสีของงานจริงจะยากกว่านี้

  --engines n8n จะ **ยิง webhook จริง** (เสียเวลา/ค่าใช้จ่าย) จึงไม่อยู่ใน
  ค่าเริ่มต้น และมี --n8n-limit จำกัดจำนวนครั้งเสมอ.

Exit code: 0 = ผ่านเกณฑ์, 1 = ไม่ผ่าน, 2 = รันไม่ได้,
           3 = **สรุปไม่ได้** (ไม่มีไฟล์ไหนมี text layer ให้ใช้เป็นเฉลย).

⚠ 3 ไม่ใช่ 0: ถ้าไม่มีเฉลย สคริปต์นี้จะไม่บอกว่า "ผ่าน" เด็ดขาด — การขึ้น
PASS ทั้งที่ไม่ได้วัดอะไรเลย คือความผิดพลาดแบบเดียวกับที่โหมด Artwork ขึ้น
"PASS ไม่พบประเด็น" ทั้งที่ยังไม่ได้เทียบ panel.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import unicodedata

try:                                   # ให้ข้อความไทยไม่พังบน console Windows
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    import numpy as np
    import cv2
except ImportError as e:
    print("ต้องมี numpy + opencv (มีอยู่ในโปรเจกต์อยู่แล้ว): %s" % e)
    sys.exit(2)

try:
    import fitz                        # PyMuPDF
except ImportError:
    print("ต้องมี PyMuPDF: py -3.9 -m pip install pymupdf")
    sys.exit(2)

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from artwork_check import config as aw_config
    from artwork_check.pdf_ingest import ArtworkDocument, encode_jpg
except Exception as e:                 # pragma: no cover
    print("import artwork_check ไม่สำเร็จ (รันสคริปต์นี้ในโฟลเดอร์โปรเจกต์): %s" % e)
    sys.exit(2)


# ── เกณฑ์ผ่าน ────────────────────────────────────────────────────────────
# recall  = คำที่พิมพ์จริงแล้ว engine อ่านเจอ  (อ่านตกเท่าไร)
# precision = คำที่ engine คืนมาแล้วมีอยู่จริง (แต่งเพิ่มเท่าไร)
# สำหรับงาน QC ที่ต้องจับ typo ให้ได้ precision สำคัญกว่า: คำที่ไม่มีอยู่จริง
# หลุดเข้าไปในผลตรวจ = ชั้น dictionary จะฟ้อง defect ปลอม หรือแย่กว่านั้นคือ
# กลบคำผิดของจริง.
MIN_RECALL = 0.95
MIN_PRECISION = 0.95
# โซนที่ไม่มีข้อความ: อนุญาตให้หลุดมาได้ไม่เกินกี่ตัวอักษร (0 = เข้มสุด)
MAX_PHANTOM_CHARS = 3
# โซนเดิม 2 DPI ต้องตรงกันอย่างน้อยเท่านี้จึงถือว่า "อ่านได้จริง ไม่ใช่เดา"
MIN_SELF_AGREE = 0.80
# ความสูงตัวอักษรขั้นต่ำใน crop (พิกเซล) ที่ OCR ยังอ่านได้น่าเชื่อถือ.
# วัดจากบล็อกจริงบล็อกเดียวกัน เปลี่ยนแค่ DPI:
#     9.0 px -> recall  1.2%      17.2 px -> 93.9%
#    12.4 px -> recall 23.2%      22.1 px -> 98.5%
#                                 31.0 px -> 100%
# ต่ำกว่า ~15 px คือ "ภาพเล็กเกินไป" ไม่ใช่ "engine อ่านไม่ออก" — ต้องแยก
# สองอย่างนี้ให้ออก ไม่งั้นจะสรุปผิดว่า OCR ใช้ไม่ได้
MIN_LINE_PX = 15.0

DPI_A_FACTOR = 1.0                     # ใช้ config.OCR_DPI ตรง ๆ
DPI_B_FACTOR = 0.7                     # เรนเดอร์ที่สองสำหรับ self-consistency


# ── การทำให้ข้อความเทียบกันได้อย่างเป็นธรรม ─────────────────────────────
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def _norm(s):
    """ตัดสระ/วรรณยุกต์ที่เป็นตัวประกอบ (combining marks) ออกทั้งหมด แล้ว
    เหลือแต่ตัวอักษร+ตัวเลข.

    ⚠ ทำไมต้องตัด combining marks: ตอนทดสอบครั้งแรกใช้ regex ที่เก็บเฉพาะ
    ช่วง Latin-1 (À-ÖØ-öø-ÿ) ทำให้ Ě/Ő/Ń ของเช็ก/ฮังการี/โปแลนด์ ถูกลบจาก
    ฝั่งเฉลยแต่ไม่ถูกลบจากฝั่ง OCR → คะแนนต่ำกว่าความจริงมาก (94.6% ทั้งที่
    ของจริง 99.8%). ถ้าจะแก้บรรทัดนี้ ให้ทดสอบกับภาษาที่มีสระซ้อนก่อนเสมอ.
    """
    s = unicodedata.normalize("NFKD", str(s)).translate(_AR_DIGITS)
    s = "".join(c for c in s if not unicodedata.combining(c))
    # \w ครอบคลุมไทย/อาหรับ/จีนด้วยเมื่อใช้ str (unicode) ใน py3
    return re.sub(r"[^\w]", "", s, flags=re.UNICODE).upper()


def _words(s, min_len=2):
    out = []
    for tok in str(s).split():
        w = _norm(tok)
        if len(w) >= min_len:
            out.append(w)
    return out


def _bag_compare(truth_words, got_words):
    """เทียบแบบ multiset: คำซ้ำนับซ้ำ (ตารางโภชนาการมี '0g' หลายครั้งจริง)."""
    pool = list(got_words)
    hit = 0
    missed = []
    for w in truth_words:
        if w in pool:
            pool.remove(w)
            hit += 1
        else:
            missed.append(w)
    # pool ที่เหลือ = คำที่ engine คืนมาแต่ไม่มีในเฉลย
    return hit, missed, pool


# ── engines ─────────────────────────────────────────────────────────────

class LimitReached(Exception):
    """ครบเพดาน --n8n-limit — เป็นการ "ข้าม" ตามที่ผู้ใช้สั่ง ไม่ใช่ความ
    ล้มเหลวของ engine จึงต้องไม่ถูกนับเป็น error (ไม่งั้นสาเหตุจริงของการ
    เรียกไม่สำเร็จจะถูกข้อความนี้ทับจนวินิจฉัยไม่ได้)."""


class Engine(object):
    name = "?"
    available = False
    note = ""

    def __init__(self):
        self.ok_count = 0
        self.err_count = 0
        self.skipped = 0
        self.last_error = ""

    def read(self, crop_bgr):
        raise NotImplementedError

    def read_counted(self, crop_bgr):
        """เรียก read() แล้วนับผลสำเร็จ/ล้มเหลว — ใช้ตัดสินว่า engine นี้
        'วัดได้จริง' หรือ 'ล้มทุกครั้ง' ตอนสรุป (ไม่งั้น engine ที่ error
        ทั้งหมดจะถูกนับรวมเป็น 'ผ่าน' ไปด้วย)."""
        try:
            out = self.read(crop_bgr)
        except LimitReached:
            self.skipped = getattr(self, "skipped", 0) + 1
            raise
        except Exception as e:
            self.err_count += 1
            self.last_error = str(e)
            raise
        self.ok_count += 1
        return out


class TesseractEngine(Engine):
    name = "tesseract"

    def __init__(self, lang, psm=6):
        Engine.__init__(self)
        self.lang = lang
        self.psm = psm
        self._pt = None
        try:
            import pytesseract
            from artwork_check.highlight import _find_tesseract_cmd
            cmd = _find_tesseract_cmd()
            if cmd:
                pytesseract.pytesseract.tesseract_cmd = cmd
            ver = pytesseract.get_tesseract_version()
            have = set(str(l) for l in pytesseract.get_languages(config=""))
            want = [l for l in lang.split("+") if l]
            missing = [l for l in want if l not in have]
            self.lang = "+".join([l for l in want if l in have]) or "eng"
            self._pt = pytesseract
            self.available = True
            self.note = "v%s lang=%s" % (ver, self.lang)
            if missing:
                self.note += "  [!] ไม่พบ traineddata: %s" % ",".join(missing)
        except Exception as e:
            self.note = "ใช้ไม่ได้: %s" % e

    def read(self, crop_bgr):
        rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        return self._pt.image_to_string(rgb, lang=self.lang,
                                        config="--psm %d" % self.psm)


class N8NEngine(Engine):
    name = "n8n"

    def __init__(self, url="", timeout=0.0, limit=0):
        Engine.__init__(self)
        self.url = url
        self.timeout = timeout
        self.limit = limit
        self.calls = 0
        try:
            from inspectors import ocr_n8n
            self._m = ocr_n8n
            target = url or getattr(ocr_n8n.config, "N8N_OCR_WEBHOOK_URL", "")
            self.available = bool(str(target).strip())
            self.note = target if self.available else "ไม่ได้ตั้ง N8N_OCR_WEBHOOK_URL"
            if self.available and limit:
                self.note += "  (จำกัด %d ครั้ง)" % limit
        except Exception as e:
            self.note = "ใช้ไม่ได้: %s" % e

    def read(self, crop_bgr):
        if self.limit and self.calls >= self.limit:
            raise LimitReached("ครบเพดาน --n8n-limit")
        self.calls += 1
        jpg = encode_jpg(crop_bgr, quality=92)
        kw = {}
        if self.url:
            kw["url"] = self.url
        if self.timeout:
            kw["timeout"] = self.timeout
        r = self._m.ocr_image(jpg, **kw)
        if r.get("error"):
            raise RuntimeError(str(r["error"]))
        if r.get("stub"):
            raise RuntimeError("backend ตอบกลับเป็น stub")
        return r.get("text", "") or ""


# ── การเลือกโซนสำหรับทดสอบ ──────────────────────────────────────────────

def zones_with_text(page, min_chars, top):
    """โซนจาก text block ของ PDF เอง = มีเฉลยแน่นอน."""
    R = page.rect
    out = []
    for b in page.get_text("blocks"):
        x0, y0, x1, y1, txt = b[0], b[1], b[2], b[3], b[4]
        if len(str(txt).strip()) < min_chars:
            continue
        out.append({
            "bbox": [(x0 - R.x0) / R.width, (y0 - R.y0) / R.height,
                     (x1 - x0) / R.width, (y1 - y0) / R.height],
            "truth": str(txt).strip(),
        })
    out.sort(key=lambda z: -len(z["truth"]))
    return out[:top]


def zones_blank(page, page_img, want, min_frac=0.045):
    """โซนที่ 'ไม่มีข้อความแน่นอน': พิกเซลเกือบเรียบ (ไม่มีหมึกให้เป็นตัว
    อักษรได้) และ text layer ในกรอบนั้นว่าง.

    ใช้ได้กับทุกไฟล์ รวมไฟล์ที่ outline ทั้งใบ — เพราะตัดสินจากพิกเซล
    ไม่ได้ตัดสินจาก text layer อย่างเดียว. คืนโซนขนาดเท่าโซนจริง ๆ ที่คน
    ลาก ไม่ใช่จุดจิ๋ว เพื่อให้ engine เจอ 'ภาพเปล่าขนาดปกติ' แบบที่เจอจริง.
    """
    H, W = page_img.shape[:2]
    R = page.rect
    bw, bh = int(W * min_frac * 2), int(H * min_frac * 2)
    if bw < 24 or bh < 24:
        return []
    gray = cv2.cvtColor(page_img, cv2.COLOR_BGR2GRAY)
    found = []
    step_x = max(1, (W - bw) // 12)
    step_y = max(1, (H - bh) // 12)
    for y in range(0, max(1, H - bh), step_y):
        for x in range(0, max(1, W - bw), step_x):
            sub = gray[y:y + bh, x:x + bw]
            if sub.size == 0 or float(sub.std()) > 3.0:
                continue          # มีอะไรอยู่ในกรอบ — ไม่ใช่พื้นที่ว่าง
            bbox = [x / float(W), y / float(H), bw / float(W), bh / float(H)]
            clip = fitz.Rect(R.x0 + bbox[0] * R.width,
                             R.y0 + bbox[1] * R.height,
                             R.x0 + (bbox[0] + bbox[2]) * R.width,
                             R.y0 + (bbox[1] + bbox[3]) * R.height)
            if page.get_text("text", clip=clip).strip():
                continue
            found.append({"bbox": bbox, "truth": ""})
            if len(found) >= want:
                return found
    return found


def zones_graphic(page, want):
    """โซนที่เป็น 'ภาพ/โลโก้' — รูป raster ที่วางบนหน้า และไม่มี text layer
    ทับอยู่. ใช้ตรวจ hallucination ในเคสที่พบบ่อยที่สุด (ลากโซนคลุมโลโก้).

    ⚠ ใช้ได้เฉพาะไฟล์ที่ 'มี text layer' เท่านั้น — บนไฟล์ที่ outline ทั้งใบ
    ทุกพื้นที่ไม่มี text layer อยู่แล้ว รวมทั้งบล็อกข้อความจริง จึงแยกไม่ออก
    ว่าอันไหนเป็นภาพจริง (จะให้ผลบวกลวง). ตัวเรียกเป็นคนคุมเงื่อนไขนี้.
    """
    R = page.rect
    out = []
    for img in page.get_images(full=True):
        try:
            rects = page.get_image_rects(img[0])
        except Exception:
            rects = []
        for r in rects:
            if r.width < R.width * 0.05 or r.height < R.height * 0.05:
                continue
            if page.get_text("text", clip=r).strip():
                continue
            out.append({
                "bbox": [(r.x0 - R.x0) / R.width, (r.y0 - R.y0) / R.height,
                         r.width / R.width, r.height / R.height],
                "truth": "",
            })
            if len(out) >= want:
                return out
    return out


# ── การเรนเดอร์ (ตรงกับ production ทุกขั้น) ─────────────────────────────

MIN_SIDE_ON = True          # ตั้ง False ด้วย --no-min-side (วัดเส้นทางดิบ)


def render(ad, bbox, dpi):
    """เรนเดอร์โซนให้ **ตรงกับที่ ``ocr.read_zone()`` ทำจริง** ทุกขั้น.

    ⚠️ เคยพลาดตรงนี้มาแล้ว: หลังเพิ่ม ``OCR_CROP_MIN_SIDE`` ให้ production
    เพิ่ม DPI กับโซนเล็ก ฟังก์ชันนี้ยังเรนเดอร์ที่ ``--dpi`` แบน ๆ อยู่
    ⇒ ตาข่ายนิรภัยวัด "เส้นทางเก่า" แล้วรายงานว่า production อ่านไม่ออก
    ทั้งที่ของจริงอ่านได้ (เจอบนสถานีกับไฟล์ Cosma: ตัวอักษร 9.1px ที่
    production ขยายเป็น ~32px ไปแล้ว). **เครื่องมือวัดที่วัดผิดทางแย่กว่า
    ไม่มีเครื่องมือ** — ถ้าแก้เส้นทางเรนเดอร์ของ production ต้องแก้ที่นี่ด้วย.
    """
    crop = ad.render_zone(bbox, dpi=dpi, max_side=aw_config.OCR_CROP_MAX_SIDE)
    if crop is None or crop.size == 0:
        return None
    # ── ขั้นเดียวกับ ocr._render_for_ocr(): PDF ที่โซนเล็กกว่าเกณฑ์ให้
    #    เรนเดอร์ใหม่ที่ DPI สูงขึ้น (ภาพ raster ห้ามขยาย — ไม่มีข้อมูลเพิ่ม)
    min_side = aw_config.OCR_CROP_MIN_SIDE if MIN_SIDE_ON else 0
    if min_side and getattr(ad, "is_pdf", False):
        longest = max(crop.shape[:2])
        if longest < min_side:
            factor = min(aw_config.OCR_DPI_MAX_FACTOR,
                         min_side / float(longest))
            bigger = ad.render_zone(bbox, dpi=int(dpi * factor),
                                    max_side=aw_config.OCR_CROP_MAX_SIDE)
            if bigger is not None and bigger.size and \
                    max(bigger.shape[:2]) > longest:
                crop = bigger
    # production เข้ารหัส JPEG q92 ก่อนส่ง OCR — ต้องผ่านขั้นนี้ด้วย ไม่งั้น
    # ตัวเลขจะดีเกินจริง (ไม่มี artifact ของการบีบอัด)
    jpg = encode_jpg(crop, quality=92)
    return cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)


# ── ชั้นการทดสอบ ────────────────────────────────────────────────────────

def _hash_of(fp):
    import hashlib
    try:
        with open(fp, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()
    except OSError:
        return ""


def layer_triage(path, page):
    txt = page.get_text("text").strip()
    words = page.get_text("words")
    R = page.rect
    NX = NY = 12
    cells = 0
    for iy in range(NY):
        for ix in range(NX):
            clip = fitz.Rect(R.x0 + R.width * ix / NX,
                             R.y0 + R.height * iy / NY,
                             R.x0 + R.width * (ix + 1) / NX,
                             R.y0 + R.height * (iy + 1) / NY)
            if len(page.get_text("text", clip=clip).strip()) >= \
                    aw_config.EMBEDDED_TEXT_MIN_CHARS:
                cells += 1
    coverage = cells / float(NX * NY)
    banner = len(txt) >= aw_config.EMBEDDED_TEXT_MIN_CHARS
    return {
        "chars": len(txt),
        "words": len(words),
        "coverage": coverage,
        "banner_says_has_text": banner,
        # แบนเนอร์บอกว่ามี text layer แต่แทบไม่มีพื้นที่ไหนใช้ได้จริง
        "banner_misleading": bool(banner and coverage < 0.10),
        "page_mm": [R.width * 25.4 / 72.0, R.height * 25.4 / 72.0],
    }


def layer_groundtruth(ad, zones, engines, dpi, verbose):
    """เทียบทุก engine กับ text layer."""
    res = {}
    for eng in engines:
        res[eng.name] = {"gt": 0, "hit": 0, "got": 0, "extra": 0,
                         "zones": 0, "errors": []}
    for i, z in enumerate(zones, 1):
        img = render(ad, z["bbox"], dpi)
        if img is None:
            continue
        truth_w = _words(z["truth"])
        if not truth_w:
            continue
        nlines = len([l for l in str(z["truth"]).splitlines() if l.strip()])
        line_px = img.shape[0] / float(max(1, nlines))
        too_small = line_px < MIN_LINE_PX
        z["line_px"] = round(line_px, 1)
        z["too_small"] = bool(too_small)
        line = "   โซน %-2d %5dx%-5d เฉลย %4d คำ |" % (
            i, img.shape[1], img.shape[0], len(truth_w))
        for eng in engines:
            try:
                got = eng.read_counted(img)
            except LimitReached:
                # ผู้ใช้สั่งจำกัดโควตาเอง — ไม่ใช่ engine พัง. ต้องแยกให้ชัด
                # ทั้งข้อความและการนับ ไม่งั้นอ่านรายงานแล้วเข้าใจว่า n8n
                # ล้มเหลวทุกโซน (เจอจริงบนสถานี: ไฟล์ 3-5 ขึ้น "ERROR" ล้วน
                # ทั้งที่แค่ครบเพดาน --n8n-limit ตั้งแต่ไฟล์แรก)
                line += "  %s: ข้าม(ครบเพดาน --n8n-limit)" % eng.name
                continue
            except Exception as e:
                res[eng.name]["errors"].append("โซน %d: %s" % (i, e))
                line += "  %s: ERROR" % eng.name
                continue
            got_w = _words(got)
            hit, missed, extra = _bag_compare(truth_w, got_w)
            r = res[eng.name]
            r["gt"] += len(truth_w)
            r["hit"] += hit
            r["got"] += len(got_w)
            r["extra"] += len(extra)
            r["zones"] += 1
            line += "  %s: R %5.1f%% P %5.1f%%" % (
                eng.name,
                hit / float(len(truth_w)) * 100.0,
                (len(got_w) - len(extra)) / float(max(1, len(got_w))) * 100.0)
            if verbose and (missed or extra):
                # แสดงทั้งสองฝั่ง: "เฉลยว่า" กับ "engine อ่านได้"
                # เพราะเมื่อเฉลย (text layer ของ PDF) เสียเอง การพิมพ์แต่
                # ฝั่งเฉลยจะทำให้สรุปผิดว่า engine อ่านไม่ออก
                line += "\n        %s | เฉลยว่า : %s" % (
                    eng.name, " ".join(missed[:6]) or "-")
                line += "\n        %s | อ่านได้ : %s" % (
                    " " * len(eng.name), " ".join(extra[:6]) or "-")
        # เฉลยจาก text layer อาจเสียเอง (ฟอนต์ subset ที่แมปอักขระผิด) —
        # อาการ: recall/precision ตกพร้อมกันทั้งที่ภาพใหญ่พอ และคำใน "เฉลย"
        # ไม่เป็นคำในภาษาใดเลย. ตรวจแบบไม่เดา: นับคำเฉลยที่ยาว >=8 ตัวและ
        # มีทั้งตัวเลขและตัวอักษรปนกัน ซึ่งแทบไม่เกิดกับข้อความฉลากจริง
        odd = [w for w in truth_w
               if len(w) >= 8 and any(c.isdigit() for c in w)
               and any(c.isalpha() for c in w)]
        if odd and len(odd) >= max(2, int(len(truth_w) * 0.02)):
            line += ("\n        [?] เฉลยจาก text layer มีคำผิดรูป %d คำ "
                     "(เช่น %s)" % (len(odd), " ".join(odd[:3])))
            line += ("\n            ฟอนต์ subset อาจแมปอักขระผิด -> "
                     "เฉลยเชื่อไม่ได้ ให้ดูบรรทัด 'อ่านได้' ด้วย --verbose")
            res.setdefault("_suspect_truth", 0)
            res["_suspect_truth"] += 1

        if too_small:
            line += ("\n        [!] ตัวอักษรสูงราว %.1f px (ต่ำกว่าเกณฑ์ %.0f) "
                     "— ภาพเล็กเกินไป ไม่ใช่ engine อ่านไม่ออก"
                     % (line_px, MIN_LINE_PX))
            line += ("\n            ลอง --dpi %d"
                     % int(dpi * (MIN_LINE_PX * 1.6 / max(1.0, line_px))))
            if not MIN_SIDE_ON:
                line += ("\n            (กำลังรันด้วย --no-min-side = ปิดการ"
                         "เพิ่ม DPI ที่ production ใช้จริง — ลองรันใหม่โดย"
                         "ไม่ใส่ flag นี้)")
            elif not aw_config.OCR_CROP_MIN_SIDE:
                line += ("\n            (ARTWORK_OCR_CROP_MIN_SIDE=0 = ปิดการ"
                         "เพิ่ม DPI ให้โซนเล็ก — ตั้งเป็น 1200 จะช่วยเคสนี้)")
            else:
                line += ("\n            (เพิ่ม DPI อัตโนมัติทำงานแล้วแต่ยังไม่พอ "
                         "— เพดานคือ ARTWORK_OCR_DPI_MAX_FACTOR=%.1f เท่า)"
                         % aw_config.OCR_DPI_MAX_FACTOR)
            res.setdefault("_small_zones", 0)
            res["_small_zones"] = res.get("_small_zones", 0) + 1
        print(line)
    return res


def layer_notext(ad, zones, engines, dpi, kind):
    """โซนที่ไม่มีข้อความ — อะไรที่คืนกลับมาคือของที่ engine แต่งขึ้น."""
    res = {}
    for eng in engines:
        res[eng.name] = {"zones": 0, "phantom_chars": 0, "phantom_zones": 0,
                         "samples": []}
    for i, z in enumerate(zones, 1):
        img = render(ad, z["bbox"], dpi)
        if img is None:
            continue
        line = "   %s %-2d %5dx%-5d |" % (kind, i, img.shape[1], img.shape[0])
        for eng in engines:
            try:
                got = eng.read_counted(img)
            except LimitReached:
                line += "  %s: ข้าม(ครบเพดาน --n8n-limit)" % eng.name
                continue
            except Exception as e:
                line += "  %s: ERROR(%s)" % (eng.name, str(e)[:24])
                continue
            flat = re.sub(r"\s+", "", got or "")
            r = res[eng.name]
            r["zones"] += 1
            r["phantom_chars"] += len(flat)
            if len(flat) > MAX_PHANTOM_CHARS:
                r["phantom_zones"] += 1
                if len(r["samples"]) < 4:
                    r["samples"].append(" ".join((got or "").split())[:60])
            line += "  %s: %d ตัวอักษร" % (eng.name, len(flat))
            if len(flat) > MAX_PHANTOM_CHARS:
                line += " [!]"
        print(line)
    return res


def layer_consistency(ad, zones, engines, dpi_a, dpi_b):
    """โซนเดิม 2 DPI — ใช้ได้แม้ไม่มีเฉลย (ไฟล์ outline)."""
    res = {}
    for eng in engines:
        res[eng.name] = {"zones": 0, "agree_sum": 0.0, "low": 0}
    for i, z in enumerate(zones, 1):
        a = render(ad, z["bbox"], dpi_a)
        b = render(ad, z["bbox"], dpi_b)
        if a is None or b is None:
            continue
        line = "   โซน %-2d %5dx%-5d |" % (i, a.shape[1], a.shape[0])
        for eng in engines:
            try:
                wa = _words(eng.read_counted(a))
                wb = _words(eng.read_counted(b))
            except LimitReached:
                line += "  %s: ข้าม(ครบเพดาน --n8n-limit)" % eng.name
                continue
            except Exception as e:
                line += "  %s: ERROR(%s)" % (eng.name, str(e)[:20])
                continue
            if not wa and not wb:
                line += "  %s: (ว่างทั้งคู่)" % eng.name
                continue
            hit, _, _ = _bag_compare(wa, wb)
            r_bad = min(a.shape[0], a.shape[1]) < 40
            agree = hit / float(max(1, max(len(wa), len(wb))))
            r = res[eng.name]
            r["zones"] += 1
            r["agree_sum"] += agree
            if agree < MIN_SELF_AGREE:
                r["low"] += 1
                if r_bad:
                    r["bad_zone"] = r.get("bad_zone", 0) + 1
            line += "  %s: ตรงกัน %5.1f%%%s" % (
                eng.name, agree * 100.0,
                (" [โซนเสีย ด้านสั้น %dpx]" % min(a.shape[0], a.shape[1]))
                if (agree < MIN_SELF_AGREE and r_bad)
                else (" [!]" if agree < MIN_SELF_AGREE else ""))
        print(line)
    return res


# ── main ────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="วัดความแม่นของ engine ถอดความบน artwork จริง "
                    "(ใช้ PDF text layer เป็นเฉลย)")
    ap.add_argument("--files", nargs="+", required=True,
                    help="ไฟล์ PDF (รับ wildcard ได้ เช่น \"C:\\aw\\*.pdf\")")
    ap.add_argument("--engines", default="tesseract",
                    help="คั่นด้วย comma: tesseract,n8n "
                         "(n8n = ยิง webhook จริง; pdf text layer เป็นเฉลย "
                         "ใช้อัตโนมัติ ไม่ต้องระบุ)")
    ap.add_argument("--tess-lang", default="eng",
                    help="ภาษา Tesseract เช่น eng+ara+tha+chi_tra+deu")
    ap.add_argument("--tess-psm", type=int, default=6)
    ap.add_argument("--dpi", type=int, default=aw_config.OCR_DPI,
                    help="DPI ที่ใช้เรนเดอร์โซน (ค่าเริ่มต้น = ARTWORK_OCR_DPI)")
    ap.add_argument("--zones", type=int, default=8,
                    help="จำนวนโซนข้อความสูงสุดต่อไฟล์")
    ap.add_argument("--layers", default="all",
                    help="ชั้นที่จะรัน คั่นด้วย comma: truth,probe,consistency "
                         "(ค่าเริ่มต้น all). มีประโยชน์กับ --engines n8n ที่มี"
                         "โควตาจำกัด เช่น --layers probe จะทุ่มโควตาทั้งหมด"
                         "ไปกับการจับ hallucination และกระจายได้ทั่วทุกไฟล์")
    ap.add_argument("--no-min-side", action="store_true",
                    help="ปิดการเพิ่ม DPI ให้โซนเล็ก (ARTWORK_OCR_CROP_MIN_SIDE) "
                         "= วัดเส้นทางดิบ ไม่ใช่ที่ production ใช้จริง")
    ap.add_argument("--min-chars", type=int, default=80,
                    help="ขนาดขั้นต่ำของ text block ที่นับเป็นโซนทดสอบ")
    ap.add_argument("--probe-zones", type=int, default=3,
                    help="จำนวนโซน 'ไม่มีข้อความ' ต่อไฟล์ (ทดสอบ hallucination)")
    ap.add_argument("--n8n-url", default="", help="override webhook URL")
    ap.add_argument("--n8n-timeout", type=float, default=0.0)
    ap.add_argument("--n8n-limit", type=int, default=20,
                    help="เพดานจำนวนครั้งที่ยิง webhook (กันค่าใช้จ่ายบานปลาย)")
    ap.add_argument("--skip-consistency", action="store_true")
    ap.add_argument("--limit", type=int, default=0,
                    help="วัดไม่เกิน N ไฟล์ (0 = ไม่จำกัด)")
    ap.add_argument("--keep-duplicates", action="store_true",
                    help="ไม่ข้ามไฟล์ที่เนื้อหาซ้ำกัน (ค่าเริ่มต้นข้าม)")
    ap.add_argument("--verbose", action="store_true",
                    help="แสดงคำที่อ่านตกด้วย")
    ap.add_argument("--out", default="", help="เขียนผลเป็น JSON ไฟล์นี้")
    args = ap.parse_args()

    global MIN_SIDE_ON
    MIN_SIDE_ON = not args.no_min_side

    want = {s.strip().lower() for s in args.layers.split(",") if s.strip()}
    if "all" in want or not want:
        want = {"truth", "probe", "consistency"}
    unknown = want - {"truth", "probe", "consistency"}
    if unknown:
        print("!! --layers ไม่รู้จัก: %s (ใช้ได้: truth, probe, consistency, all)"
              % ", ".join(sorted(unknown)))
        return 2
    run_truth = "truth" in want
    run_probe = "probe" in want
    run_consistency = "consistency" in want and not args.skip_consistency

    paths = []
    for pat in args.files:
        # รับโฟลเดอร์ได้ตรง ๆ: --files TEST  =  --files "TEST\\*.pdf"
        if os.path.isdir(pat):
            paths.extend(sorted(glob.glob(os.path.join(pat, "*.pdf"))))
            paths.extend(sorted(glob.glob(os.path.join(pat, "*.PDF"))))
            continue
        hits = sorted(glob.glob(pat))
        paths.extend(hits if hits else ([pat] if os.path.exists(pat) else []))
    paths = sorted(set(paths))
    paths = [p for p in paths if p.lower().endswith(".pdf")]
    if not paths:
        print("ไม่พบไฟล์ PDF ตามที่ระบุ")
        return 2

    # ไฟล์ในคลังการตรวจชื่อ "source.pdf" เหมือนกันหมด — ต้องแยกด้วยโฟลเดอร์
    # (inspection id) ไม่งั้นอ่านผลไม่รู้ว่าบรรทัดไหนคือไฟล์ไหน
    def _label(fp):
        base = os.path.basename(fp)
        parent = os.path.basename(os.path.dirname(fp))
        if base.lower().startswith("source") and parent:
            return "%s/%s" % (parent, base)
        return base

    # คลังการตรวจมักมีไฟล์เดียวกันซ้ำหลายรายการ (ตรวจซ้ำหลายรอบ) — วัดซ้ำ
    # ไม่ได้ข้อมูลเพิ่มแต่กินเวลาเป็นสิบเท่า จึงข้ามไฟล์ที่เนื้อหาซ้ำ
    uniq, seen, dupes = [], {}, 0
    if not args.keep_duplicates:
        import hashlib
        for fp in paths:
            try:
                with open(fp, "rb") as f:
                    h = hashlib.sha1(f.read()).hexdigest()
            except OSError:
                continue
            if h in seen:
                dupes += 1
                seen[h].append(_label(fp))
                continue
            seen[h] = [_label(fp)]
            uniq.append(fp)
        if dupes:
            print("ข้ามไฟล์ที่เนื้อหาซ้ำ %d รายการ (ใช้ --keep-duplicates "
                  "ถ้าต้องการวัดทุกรายการ)" % dupes)
        paths = uniq
    if args.limit and len(paths) > args.limit:
        print("จำกัดที่ %d ไฟล์แรก (จาก %d) ตาม --limit"
              % (args.limit, len(paths)))
        paths = paths[:args.limit]

    want = [e.strip().lower() for e in args.engines.split(",") if e.strip()]
    engines = []
    if "tesseract" in want:
        engines.append(TesseractEngine(args.tess_lang, args.tess_psm))
    if "n8n" in want:
        engines.append(N8NEngine(args.n8n_url, args.n8n_timeout,
                                 args.n8n_limit))

    print("=" * 88)
    print("verify_ocr.py — วัดเครื่องถอดความบน artwork จริง")
    print("=" * 88)
    print("OCR_DPI ที่ใช้        : %d   (max_side %d, JPEG q92 เหมือน production)"
          % (args.dpi, aw_config.OCR_CROP_MAX_SIDE))
    print("EMBEDDED_TEXT_MIN_CHARS: %d" % aw_config.EMBEDDED_TEXT_MIN_CHARS)
    for e in engines:
        print("engine %-10s     : %s" % (
            e.name, e.note if e.note else ("พร้อม" if e.available else "-")))
    usable = [e for e in engines if e.available]
    if not usable:
        print("\nไม่มี engine ที่ใช้ได้เลย — ติดตั้ง pytesseract/tesseract "
              "หรือระบุ --engines n8n พร้อมตั้ง N8N_OCR_WEBHOOK_URL")
        return 2

    report = {"dpi": args.dpi, "files": [],
              "engines": [e.name for e in usable]}
    totals = {}
    for e in usable:
        totals[e.name] = {"gt": 0, "hit": 0, "got": 0, "extra": 0,
                          "phantom_zones": 0, "probe_zones": 0,
                          "agree_sum": 0.0, "agree_zones": 0, "low": 0}

    for fi, path in enumerate(paths, 1):
        name = _label(path)
        try:
            doc = fitz.open(path)
            page = doc[0]
        except Exception as e:
            print("\n[ข้าม] %s : เปิดไม่ได้ (%s)" % (name, e))
            continue
        print()
        print("#" * 88)
        print("# [%d/%d] %s" % (fi, len(paths), name))
        same = seen.get(_hash_of(path), [])
        if len(same) > 1:
            print("#   (ไฟล์เดียวกับอีก %d รายการ: %s)"
                  % (len(same) - 1, ", ".join(same[1:4])))
        print("#" * 88)

        tri = layer_triage(path, page)
        print("[1] TRIAGE  %.0f x %.0f mm | ข้อความ live %d ตัว (%d คำ) | "
              "พื้นที่ใช้ text layer ได้ %.0f%%"
              % (tri["page_mm"][0], tri["page_mm"][1], tri["chars"],
                 tri["words"], tri["coverage"] * 100.0))
        print("    แบนเนอร์ในหน้าเว็บจะขึ้นว่า: %s"
              % ("มี text layer" if tri["banner_says_has_text"]
                 else "ไม่มี text layer"))
        if tri["banner_misleading"]:
            print("    [!!] แบนเนอร์บอกว่ามี text layer แต่ใช้ได้จริงแค่ "
                  "%.0f%% ของหน้า — เกือบทุกโซนจะถูกส่ง OCR"
                  % (tri["coverage"] * 100.0))

        ad = ArtworkDocument(path)
        frec = {"file": name, "triage": tri}

        gt_zones = zones_with_text(page, args.min_chars, args.zones)
        if gt_zones and not run_truth:
            print("[2] GROUND TRUTH  ข้าม (--layers)")
        elif gt_zones:
            print("[2] GROUND TRUTH  %d โซน (เฉลยจาก text layer)"
                  % len(gt_zones))
            g = layer_groundtruth(ad, gt_zones, usable, args.dpi, args.verbose)
            frec["groundtruth"] = g
            for e in usable:
                r = g[e.name]
                for k in ("gt", "hit", "got", "extra"):
                    totals[e.name][k] += r[k]
        else:
            print("[2] GROUND TRUTH  ข้าม — ไฟล์นี้ไม่มี text block "
                  "ที่ยาวพอ (outline ทั้งใบ)")

        page_img = ad.render(72)
        probes = zones_blank(page, page_img, args.probe_zones)
        kindname = "ว่าง"
        if tri["banner_says_has_text"] and len(probes) < args.probe_zones:
            # เติมด้วยโซนภาพ/โลโก้ ได้เฉพาะไฟล์ที่มี text layer จริง
            probes += zones_graphic(page, args.probe_zones - len(probes))
            kindname = "ว่าง/ภาพ"
        if probes and not run_probe:
            print("[3] NO-TEXT PROBE  ข้าม (--layers)")
        elif probes:
            print("[3] NO-TEXT PROBE  %d โซนที่ไม่มีข้อความ "
                  "(สิ่งที่คืนกลับมา = แต่งขึ้นทั้งหมด)" % len(probes))
            n = layer_notext(ad, probes, usable, args.dpi, kindname)
            frec["notext"] = n
            for e in usable:
                totals[e.name]["phantom_zones"] += n[e.name]["phantom_zones"]
                totals[e.name]["probe_zones"] += n[e.name]["zones"]
        else:
            print("[3] NO-TEXT PROBE  ข้าม — หาพื้นที่ว่างในหน้านี้ไม่ได้")

        if run_consistency:
            cz = gt_zones if gt_zones else []
            if not cz:
                # ไฟล์ outline: ใช้โซนที่ระบบเสนอเอง = โซนแบบที่ผู้ใช้เจอจริง
                try:
                    from artwork_check import zones as zmod
                    prev = ad.render(aw_config.PREVIEW_DPI)
                    cz = zmod.propose_zones(prev)[:args.zones]
                except Exception:
                    cz = []
            if cz:
                print("[4] SELF-CONSISTENCY  %d โซน (DPI %d vs %d)"
                      % (len(cz), args.dpi, int(args.dpi * DPI_B_FACTOR)))
                c = layer_consistency(ad, cz, usable, args.dpi,
                                      int(args.dpi * DPI_B_FACTOR))
                frec["consistency"] = c
                for e in usable:
                    totals[e.name]["agree_sum"] += c[e.name]["agree_sum"]
                    totals[e.name]["agree_zones"] += c[e.name]["zones"]
                    totals[e.name]["low"] += c[e.name]["low"]

        report["files"].append(frec)
        doc.close()

    # ── สรุปรายไฟล์ (จำเป็นเมื่อทดสอบหลายไฟล์: ค่ารวมกลบไฟล์ที่มีปัญหา) ──
    if len(report["files"]) > 1:
        print()
        print("=" * 88)
        print("สรุปรายไฟล์")
        print("=" * 88)
        print("%-32s %7s %4s %6s %8s %9s %8s %s"
              % ("ไฟล์", "textลาย", "เตือน", "โซนวัด", "recall",
                 "precision", "แต่งขึ้น", "ตรงกันเอง"))
        print("-" * 88)
        for f in report["files"]:
            g = f.get("groundtruth", {})
            n = f.get("notext", {})
            c = f.get("consistency", {})
            e0 = usable[0].name
            gg = g.get(e0, {})
            nn = n.get(e0, {})
            cc = c.get(e0, {})
            rec = (gg.get("hit", 0) / float(gg["gt"])) if gg.get("gt") else None
            pre = ((gg.get("got", 0) - gg.get("extra", 0)) /
                   float(gg["got"])) if gg.get("got") else None
            agr = (cc.get("agree_sum", 0) / cc["zones"]) if cc.get("zones") else None
            cov = f["triage"]["coverage"]
            flag = "[!!]" if f["triage"]["banner_misleading"] else ""
            print("%-32s %6.0f%% %4s %6d %6s %9s %8s %9s"
                  % (f["file"][:32], cov * 100, flag, gg.get("zones", 0),
                     ("%.1f%%" % (rec * 100)) if rec is not None else "-",
                     ("%.1f%%" % (pre * 100)) if pre is not None else "-",
                     ("%d/%d" % (nn.get("phantom_zones", 0), nn.get("zones", 0))
                      if nn.get("zones") else "-"),
                     ("%.0f%%" % (agr * 100)) if agr is not None else "-"))
        if len(usable) > 1:
            print("(ตารางนี้แสดง engine แรก: %s — ดูรายละเอียด engine อื่นใน --out)"
                  % usable[0].name)

    # ── สรุป ────────────────────────────────────────────────────────────
    print()
    print("=" * 88)
    print("สรุปรวมทุกไฟล์")
    print("=" * 88)
    print("%-11s %9s %10s %13s %11s  %s" %
          ("engine", "recall", "precision", "โซนที่แต่งขึ้น", "ตรงกันเอง",
           "ผลรายตัว"))
    print("-" * 88)
    failed = []
    dead = []
    for e in usable:
        t = totals[e.name]
        rec = t["hit"] / float(t["gt"]) if t["gt"] else None
        pre = ((t["got"] - t["extra"]) / float(t["got"])) if t["got"] else None
        agr = (t["agree_sum"] / t["agree_zones"]) if t["agree_zones"] else None
        s_rec = "%.1f%%" % (rec * 100) if rec is not None else "n/a"
        s_pre = "%.1f%%" % (pre * 100) if pre is not None else "n/a"
        s_ph = ("%d/%d" % (t["phantom_zones"], t["probe_zones"])
                if t["probe_zones"] else "n/a")
        s_ag = "%.1f%%" % (agr * 100) if agr is not None else "n/a"

        why = []
        if rec is not None and rec < MIN_RECALL:
            why.append("recall < %.0f%%" % (MIN_RECALL * 100))
        if pre is not None and pre < MIN_PRECISION:
            why.append("precision < %.0f%%" % (MIN_PRECISION * 100))
        if t["probe_zones"] and t["phantom_zones"]:
            why.append("แต่งข้อความในโซนที่ไม่มีข้อความ %d โซน"
                       % t["phantom_zones"])

        # engine ที่เรียกแล้วล้มทุกครั้ง = ไม่มีข้อมูลให้ตัดสิน ห้ามนับเป็นผ่าน
        if e.ok_count == 0 and e.err_count:
            verdict = "เรียกไม่สำเร็จ"
            dead.append((e.name, e.err_count, e.last_error))
        elif e.ok_count == 0:
            verdict = "ไม่ได้เรียก"
        elif why:
            verdict = "ไม่ผ่าน"
            failed.append((e.name, why))
        elif rec is None:
            verdict = "สรุปไม่ได้"
        else:
            verdict = "ผ่าน"
        print("%-11s %9s %10s %13s %11s  %s"
              % (e.name, s_rec, s_pre, s_ph, s_ag, verdict))
        if e.err_count or e.skipped:
            print("            (สำเร็จ %d / ล้มเหลว %d / ข้ามเพราะครบเพดาน %d%s)"
                  % (e.ok_count, e.err_count, e.skipped,
                     " — %s" % e.last_error[:44] if e.last_error else ""))
    print()
    for e in usable:
        t = totals[e.name]
        if t["probe_zones"] and t["phantom_zones"]:
            for f in report["files"]:
                s = f.get("notext", {}).get(e.name, {}).get("samples") or []
                for x in s[:2]:
                    print("   [%s] แต่งขึ้นในโซนว่าง: %r" % (e.name, x))

    # self-consistency เป็น "คำใบ้" ไม่ใช่เกณฑ์ตัดสิน: โซนที่ระบบเสนอเองมัก
    # มีแถบ dieline / colour bar ปนมา ซึ่งอ่านไม่ตรงกันเป็นเรื่องปกติและเป็น
    # ความผิดของโซน ไม่ใช่ของ engine. รายงานแยกไว้ให้อ่านเอง
    for e in usable:
        t = totals[e.name]
        if t["agree_zones"] and t["low"]:
            print("   [%s] โซนที่อ่านสองรอบไม่ตรงกัน %d/%d โซน — "
                  "ตรวจว่าโซนพวกนั้นเป็นบล็อกข้อความจริงหรือแถบ dieline/colour bar"
                  % (e.name, t["low"], t["agree_zones"]))

    measured = any(totals[e.name]["gt"] > 0 and e.ok_count
                   for e in usable)
    print()
    if dead:
        print("[!] engine ที่เรียกไม่สำเร็จเลยสักครั้ง — ไม่มีข้อมูลให้ตัดสิน:")
        for nm, n, err in dead:
            print("    - %s : ล้มเหลว %d ครั้ง : %s" % (nm, n, err[:60]))
        print("    ตรวจ N8N_OCR_WEBHOOK_URL / เครือข่าย / n8n ทำงานอยู่หรือไม่")
        print()
    if failed:
        print("ผล: ไม่ผ่าน")
        for nm, why in failed:
            print("   - %s : %s" % (nm, " · ".join(why)))
    elif not measured:
        if not run_truth:
            # อย่าบอกว่า "ไม่มีไฟล์ไหนมี text layer" ทั้งที่ผู้ใช้เป็นคนสั่ง
            # ข้ามเอง — เหตุผลที่ผิดคือคำตอบที่ผิดแบบมั่นใจ (กฎเหล็ก 2)
            print("ผล: สรุปไม่ได้ — ข้ามชั้น GROUND TRUTH ไปตาม --layers")
            print("   ชั้นที่รันไปให้ข้อมูลได้ แต่ recall/precision วัดไม่ได้")
            print("   จึง **ไม่ถือว่าผ่าน** (ดูตัวเลข 'โซนที่แต่งขึ้น' ประกอบได้)")
            print("   ถ้าต้องการคำตัดสิน: รันใหม่โดยไม่ใส่ --layers")
        else:
            print("ผล: สรุปไม่ได้ — ไม่มีไฟล์ไหนมี text layer ให้ใช้เป็นเฉลย")
            print("   recall/precision วัดไม่ได้ จึง **ไม่ถือว่าผ่าน**")
            print("   ทางออก: ใส่ไฟล์ที่ยังมี text layer อย่างน้อย 1 ไฟล์เข้าไปด้วย")
            print("           (artwork ก่อน outline / ไฟล์ต้นฉบับจากกราฟิก)")
            print("           แล้วผลที่ได้จะใช้เป็นตัวแทนของ engine บนงานชุดนี้ได้")
    else:
        ok_names = [e.name for e in usable
                    if e.ok_count and totals[e.name]["gt"] > 0]
        print("ผล: ผ่าน — %s (recall >= %.0f%%, precision >= %.0f%%, "
              "ไม่แต่งข้อความในโซนว่าง)"
              % (", ".join(ok_names), MIN_RECALL * 100, MIN_PRECISION * 100))
        print("   ขอบเขตที่วัดจริง: %d คำ จากไฟล์ที่มี text layer เท่านั้น"
              % max(totals[e.name]["gt"] for e in usable))
        if dead:
            print("   ⚠ ผลนี้ครอบคลุมเฉพาะ engine ข้างต้น — %s ยังไม่ได้วัด"
                  % ", ".join(n for n, _, _ in dead))
    print()
    print("อ่านผลอย่างไร")
    print("   recall ต่ำ     = อ่านตก -> มักแก้ได้ด้วย traineddata/ภาษา หรือ DPI")
    print("   precision ต่ำ  = คืนคำที่ไม่มีอยู่จริง -> ชั้น dictionary จะฟ้อง defect ปลอม")
    print("   โซนที่แต่งขึ้น = hallucination โดยตรง อันตรายที่สุดกับงาน QC")
    print("   ตรงกันเอง ต่ำ  = อ่านไม่นิ่ง หรือโซนนั้นไม่ใช่บล็อกข้อความ")

    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump({"summary": totals, "detail": report}, f,
                          ensure_ascii=False, indent=2)
            print("\nเขียนผลละเอียดลง %s" % args.out)
        except OSError as e:
            print("\nเขียนไฟล์ผลไม่สำเร็จ: %s" % e)

    if failed:
        return 1
    return 0 if measured else 3


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nยกเลิกโดยผู้ใช้")
        sys.exit(2)
