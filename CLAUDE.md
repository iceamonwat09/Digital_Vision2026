# CLAUDE.md — บริบทสำคัญสำหรับ AI ที่ทำงานต่อกับโปรเจกต์นี้

ระบบตรวจตำหนิบรรจุภัณฑ์ (Can Dent / ฉลาก / Artwork) — Flask + YOLO + SQL Server
รันบน **เครื่องสถานี Windows + Python 3.9** ที่ต่อกล้อง USB จริง อ่านไฟล์นี้ก่อนแก้โค้ด
เพื่อไม่พลาดกับดักที่เคยเจอมาแล้ว.

---

## 🔴 กฎเหล็ก (ยึดตลอด)

1. **ห้ามกระทบโหมดอื่น 100%** — ทุกฟีเจอร์ใหม่ต้อง opt-in (flag default = พฤติกรรมเดิม) และ
   scope เฉพาะโหมดที่ตั้งใจ. โหมดหลัก: **Live USB / RTSP / STREAM / Snapshot / Label / Artwork**.
2. **ความแม่นของการตรวจ (QC) สำคัญที่สุด** — ห้ามแลกความแม่นเพื่อความเร็ว. การเร่งความเร็ว
   ต้องคงผลตรวจให้เท่าเดิม (พิสูจน์ด้วย `verify_onnx.py` ก่อนเปิดใช้).
   - **ผลที่ผิดแบบมั่นใจ แย่กว่าไม่แสดงผล** — ใช้กับทุกชั้นที่ "ชี้จุด" ให้คนดู (เช่นกรอบแดงชี้คำผิด):
     ถ้าไม่มั่นใจให้**ไม่แสดง** ดีกว่าเดา เพราะคนจะเชื่อสิ่งที่ระบบชี้แล้วมองข้ามของจริง.
3. **วางแผน + ให้ผู้ใช้ยืนยันก่อนลงมือ** งานที่มีผลต่อโครงสร้าง/พฤติกรรม.
4. **ตรวจสอบความถูกต้องหลังทำ อย่างเป็นกลาง** (ไม่เข้าข้างตัวเอง) — ไล่ edge case + fallback.
5. Deploy: ผู้ใช้ `git pull` แล้ว **`py -3.9 app.py`** บนสถานี. ยืนยันโค้ดใหม่รันจริงด้วย
   **`CONFIG_VERSION` บน footer** (Flask อ่าน config ตอน start เท่านั้น — ต้องปิด-เปิดใหม่).

---

## 📍 สถานะปัจจุบัน — อ่านก่อนเริ่มงานต่อ (อัปเดต 17 ส.ค. 2026)

**Branch: `claude/artwork-multi-zone-errors-k8linm`** (ห้าม push main) ·
**CONFIG_VERSION: `2026.08.16-aw-ux`** · **pytest: 548 ผ่าน / 5 fail (pre-existing)**

### โจทย์ตั้งต้นของรอบนี้
ผู้ใช้ถามว่า *"ถ้าผู้ใช้จริงวาดหลายโซนแล้วส่งตรวจ จะเจอความผิดพลาดอะไรบ้าง"* และ
*"ทำไมมีตัวอักษรแปลก ๆ โผล่มาทั้งที่ของจริงไม่มี"* → ไล่แล้วเจอ **3 จุดบอดที่ทำให้ผลตรวจ
ผิดแบบเงียบ** (ไม่ error, การ์ดขึ้นปกติ, แต่ผลใช้ไม่ได้) — แก้ครบแล้วทั้ง 3:

| # | จุดบอด | แก้ที่ | หัวข้อละเอียด |
|---|---|---|---|
| 1 | โซนเล็กถูกเรนเดอร์เล็กเกินไป → OCR อ่านไม่ออก (recall 1.2%) | `OCR_CROP_MIN_SIDE` | 🔎 คุณภาพการอ่าน |
| 2 | text layer เสียแต่เชื่อ `conf=1.0` | `PDFTEXT_GARBLED_CHECK` | 🔎 คุณภาพการอ่าน |
| 3 | **PASS ทั้งที่ชั้นเทียบข้ามแผงไม่เคยทำงาน** | `checks.check_coverage()` | 🧩 PASS ไม่ได้แปลว่าตรวจครบ |
| + | Gemini ครอบ ```` ```json ```` / N8N คืนหน้า HTML → ขยะกลายเป็น "ข้อความบนฉลาก" | `ocr_n8n._strip_fence` / `_looks_like_html` | 🔌 N8N |

### ✅ ทำเสร็จแล้ว (เรียงตาม commit)
| commit | เรื่อง |
|---|---|
| `7b5cf61` | OCR: เพิ่ม DPI ให้โซนเล็ก + ไม่เชื่อ text layer ที่เสีย |
| `810deef` | merge `origin/main` (login/ลงทะเบียน + ปุ่มแปลหลัก + N8N → 127.0.0.1) |
| `21c1b8b` | `_ocr_fingerprint()` เข้า cache key + `diagnose_n8n_ocr` รู้จัก "ทางที่ 6" |
| `e39a29e` | **`coverage`** — รายงานว่าชั้นไหนได้ตรวจจริง + เตือนตอนจัดโซน |
| `d460924` | **N8N**: ถอดรั้ว markdown · ปฏิเสธหน้า HTML · retry · `docs/N8N_OCR_PROMPT.md` |
| `20e3af1` | `verify_ocr`: เรนเดอร์ให้ตรง production + `--layers` |
| `008ca38` | `verify_ocr`: "ครบโควตา" ต้องไม่แสดงเป็น ERROR |
| `5d2f91a` | UX: verdict ตรง coverage · พอดีทั้งหน้า · วาดต่อเนื่อง · autosave โซน |

### 🔬 pixel diff — **Phase 1 (วัดผลด้วย CLI) เสร็จแล้ว · ยังไม่แตะ UI**

ผู้ใช้เลือก "วัดก่อนด้วย CLI แล้วค่อยทำ UI" และ "ขนาดหน้าไม่เท่ากัน = ไม่เทียบ + บอกเหตุผล".

- **`artwork_check/pixdiff.py`** — โมดูลใหม่ ไม่ import Flask · ไม่เขียนไฟล์เอง ·
  ไม่ถูกเรียกจาก `run_inspection` ⇒ **ยังไม่กระทบอะไรทั้งสิ้น**
- **`verify_pixdiff.py`** — `--selftest` (สร้าง PDF เอง ไม่ต้องมีไฟล์จริง) ·
  `--pair NEW OLD` · `--new-dir/--old-dir` (เทียบทั้งโฟลเดอร์ตามชื่อไฟล์) ·
  `--save-dir` เขียนภาพกรอบส้ม. exit `0`/`1`(พบความต่าง)/`2`
- **`tests/test_artwork_pixdiff.py` 23 ตัว**

**ตัวเลขที่วัดได้จริง (selftest ผ่าน 10/10):** ไฟล์เนื้อหาเดียวกัน → **0 พิกเซล
false positive** · จับได้ทั้ง **ตัวเลขเปลี่ยน / ขยับ 0.8pt / สีเปลี่ยน** ·
กรอบชี้เฉพาะแผงที่แก้จริง (แผงซ้าย-ขวาที่ไม่ได้แก้ไม่มีกรอบเลย) · **deterministic
รันซ้ำได้เลขเดิม** · **0.25 วิ/หน้า** ที่ 200 DPI (A4 → 2339x1653). ตรวจด้วยตาแล้ว
กรอบล้อม `85` ของ `NET WEIGHT 185 g` พอดี (เลข `1` ที่เหมือนกันไม่ติดกรอบ).

**ด่านความปลอดภัย 3 ชั้น (ทั้งหมดคือ "ไม่มั่นใจ = ไม่เทียบ" ตามกฎเหล็กข้อ 2):**
| ด่าน | เกณฑ์ | เหตุผล |
|---|---|---|
| ขนาดหน้า | `PAGE_SIZE_TOL_MM = 0.2` | ต่างแม้เศษ mm อาจแปลว่า **จัดเนื้อหาใหม่ทั้งหน้า** ⇒ ครอปแล้วทุกตัวอักษรเลื่อน = ต่างทั้งใบ (เคยวัดได้ 370 บริเวณปลอม) |
| ขนาดพิกเซล | `PIXEL_SIZE_TOL = 2` | **ต้องกว้างกว่า `PAGE_SIZE_TOL_MM` เมื่อแปลงเป็น px ที่ `PIXDIFF_DPI`** ไม่งั้นมีเคสที่ผ่านด่าน mm แล้วไปตกด่านพิกเซล = บอกเหตุผลผิดเรื่อง (มีเทสต์ล็อกไว้) |
| ต่างทั้งใบ | `MAX_DIFF_RATIO = 0.20` | ต่างเกิน 20% ของหน้า = คนละงาน/ทั้งหน้าเลื่อน ⇒ คืน `too_different` **ไม่พ่นกรอบเป็นร้อย** แต่ยังบอก % จริง |

**กับดักที่เจอตอนทำ (แก้แล้ว):** ค่าจาก numpy (`np.int32`/`np.float64`) ที่หลุดเข้า
`regions` ทำให้ **`json.dumps` โยน `TypeError` ตอนบันทึก `report.json`** — พังหลังบ้าน
แบบไม่มีใครเห็นจนกว่าจะถึงหน้าเว็บ. cast เป็น `float()`/`int()` หมดแล้ว + มีเทสต์คุม.

### 🧩 โหมดโซน — เพราะ **2 ใน 3 คู่ไฟล์จริงเทียบทั้งหน้าไม่ได้**

วัดบนสถานีด้วยไฟล์จริง (18 ส.ค.) แล้วพบว่าโหมดทั้งหน้าไม่พอ:

| คู่ที่ทดสอบ | ผล |
|---|---|
| ไฟล์จริง vs ตัวเอง (Star kist) | **0 พิกเซล / 0.63 วิ** ⇒ false positive floor ยืนยันบนไฟล์ production จริง |
| Final printer vs Original file | ต่าง **26.9%** → `too_different` (คนละ layout จริง) |
| Cosma A4 vs Original | **296x208 vs 757.6x454.8 mm** → `page_size_mismatch` |

⇒ `compare_zone(path_a, bbox_a, path_b, bbox_b)` — เทียบ **แผงต่อแผง** โดยเรนเดอร์
ทั้งสองที่ **สเกล mm เดียวกัน** (ไม่ใช่สัดส่วนของหน้า) แล้ว align ก่อนเทียบ:

- **ต้อง align เสมอในโหมดโซน** (ต่างจากโหมดทั้งหน้าที่ตำแหน่งมีความหมายจึงไม่ align) —
  ผู้ใช้ลากโซนด้วยมือ ขอบสองฝั่งไม่มีทางตรงกัน. ใช้โซนที่เล็กกว่าเป็น template
  หาตำแหน่งด้วย NCC (`MIN_MATCH_CONF=0.55`) · **คะแนนต่ำ = ไม่รายงาน**
- **การเลื่อนไม่ถูกซ่อน** — คืนใน `shift_mm` ให้ผู้ตรวจเห็นว่าเนื้อหาขยับไปเท่าไร
- **`ZONE_TOLERANCE_PX = 1` จำเป็น ไม่ใช่การผ่อนเกณฑ์** — แผงเดียวกันบนหน้าคนละ
  ขนาดตกลงบน "เศษส่วนพิกเซล" คนละค่า ⇒ ขอบตัวอักษรต่างกันทั้งแผง. วัดได้
  **13 บริเวณปลอม → 0** เมื่อเปิด tolerance ขณะที่ตัวเลขที่เปลี่ยนยังจับได้ 1 บริเวณ.
  `_diff_mask` ทำสองทางแล้วเอาค่ามากสุด ⇒ ผลไม่ขึ้นกับว่าไฟล์ไหนเป็น a หรือ b
- **`content_bbox()`** ตอบคำถามที่ตัดสินทุกอย่าง: *เนื้อหาข้างในขนาดจริงเท่ากันไหม*
  (เท่า = วางคนละที่บนแผ่นคนละขนาด เทียบได้เลย · ไม่เท่า = ไฟล์หนึ่งถูกย่อ)
  → `verify_pixdiff.py --auto-zone` ใช้ค่านี้หาโซนให้เอง **ไม่ต้องรู้พิกัด**

> ⚠️ **จุดอันตรายที่เจอตอนเขียนเทสต์: โซนว่าง vs โซนว่าง เคยขึ้น "ไม่พบความต่าง"**
> ทั้งที่ไม่ได้ตรวจอะไรเลย (template สีขาวล้วนจับคู่ได้คะแนนเต็มกับพื้นที่ขาวที่ไหน
> ก็ได้) = **ความมั่นใจปลอม** ตรงกับกฎเหล็กข้อ 2 เป๊ะ. ปิดด้วย `MIN_INK_RATIO`
> → `zone_blank` พร้อมบอก % หมึกจริงของทั้งสองฝั่ง.

### 📊 `pixdiff_noise_scan.py` — วัด noise ด้วย "ไฟล์เดียวกันเทียบตัวเอง"

`py -3.9 pixdiff_noise_scan.py --dir TEST [--save-dir img] [--out noise.json]`
ใช้ไฟล์เดียวกันทั้งสองฝั่ง ⇒ **ทุกบริเวณที่พบ = ของปลอม 100%** (มี ground truth
โดยไม่ต้องมีคู่เก่า/ใหม่) แล้วจงใจทำให้คลาดทีละแบบ: เลื่อน 0.25/0.5/1 px
(เรนเดอร์ที่ 4 เท่าแล้วย่อ = จำลองการตกบนเศษพิกเซลจริง) · สเกลเพี้ยน 0.2%/1% ·
พร้อม **สัญญาณจริง** (วาดทับ 4x2 mm ≈ ขนาดหนึ่งคำ) ที่ระบบต้องจับให้ได้เสมอ.
ไล่ทุกคู่ของ `tolerance × min_region` แล้วบอกว่าคู่ไหนใช้ได้.

**ผลที่วัดได้ (ชุดทดสอบสังเคราะห์ 3 แบบ: ข้อความหนาแน่น / ตาราง / กราฟิก):**

| ความคลาด | tol=0 | **tol=1** | tol=2 |
|---|---|---|---|
| ตรงเป๊ะ | 0 | 0 | 0 |
| เลื่อน 0.25 px | 29-120 บริเวณ | **0** | 0 |
| เลื่อน 0.50 px | 29-120 | **0** | 0 |
| เลื่อน 1.00 px | 29-120 | **0** | 0 |
| **สเกลเพี้ยน 0.2%** | 77-96 | **54-239** | 35-206 |
| สัญญาณจริง 4x2 mm | จับได้ 6/6 | **จับได้ 6/6** | จับได้ 6/6 |

**ข้อสรุป 2 ข้อที่เปลี่ยนการออกแบบ:**
1. **`ZONE_TOLERANCE_PX = 1` คือค่าที่ถูก** — ฆ่า noise จากการเลื่อนได้ **หมดทุกไฟล์**
   ขณะที่สัญญาณขนาดหนึ่งคำยังจับได้ 100%.
2. **สเกลเพี้ยนต้องเป็น "ด่านปฏิเสธ" ไม่ใช่ "กรองทีหลัง"** — ไล่ `min_region`
   ทุกค่าแล้ว **ไม่มีค่าไหนกรอง noise จากสเกลออกได้โดยไม่ทิ้งสัญญาณจริงไปด้วย**
   (`min_region=800` ทำให้ noise เป็น 0 ก็จริง แต่สัญญาณหายหมด 0/6).
   ⚠️ ที่ต้องระวัง: **tol=1 ให้ "จำนวนบริเวณ" มากกว่า tol=0 ในเคสสเกล** (239 vs 96)
   เพราะพื้นที่ต่างถูกตัดเป็นก้อนเล็กกระจาย — ดู `diff_px` ควบคู่เสมอ อย่าดูแต่จำนวน.

⇒ `compare_zone()` จึงมีด่าน **`scale_mismatch`** ก่อนจับคู่ตำแหน่ง (align แก้ได้แค่
การเลื่อน ไม่ได้แก้การย่อ/ขยาย). เกณฑ์คิดจากพิกเซลจริงด้วย
`scale_allowance(zone_px) = ZONE_TOLERANCE_PX / zone_px` — โซน 800px ยอมได้ ±0.125%.
> ⚠️ `_ink_extent()` **คืน (0,0) เมื่อหมึกชนขอบภาพ** = วัดสเกลไม่ได้ (เนื้อหาถูกโซนตัด).
> เคยพลาดมาแล้ว: โซนที่ลากชิดขอบแผงพอดีอ่านสเกลได้ 0.93 เท่าทั้งที่เท่ากันเป๊ะ
> แล้วระบบปฏิเสธการเทียบทั้งที่เทียบได้ — มีเทสต์ล็อกไว้.

**ยืนยันบนไฟล์จริง:** คู่ Cosma → เนื้อหา **218x134 mm vs 746x447 mm (0.292x)** =
ไฟล์ A4 เป็นฉบับย่อ ~29.5% ⇒ ระบบปฏิเสธถูกต้องแล้ว (คะแนนจับคู่ 0.38 < 0.55).

**Phase 2 (ยังไม่เริ่ม):** `POST /api/artwork/<id>/pixdiff` (ต้องกดปุ่มเอง ไม่วิ่ง
ตอน "ส่งตรวจสอบ") · จับคู่โซนด้วย `group` ที่มีอยู่แล้ว (ชั้น cross-file ใช้อยู่) ·
การ์ดผล + CSS **ทั้ง 2 template** · ย้ายค่าคงที่เข้า `artwork_check/config.py` ·
เพิ่มชั้นใหม่ใน `verify_artwork_features.py`.

### 🔜 แผนเดิมของงานนี้ (บริบทตอนตัดสินใจ): **pixel diff เทียบฉบับเก่า/ใหม่**
ผู้ใช้ตอบว่า **"เก็บไว้เป็นแผนถัดไป"** (มีไฟล์ฉบับที่อนุมัติแล้ว) — ยังไม่เริ่มเขียนโค้ด.
สิ่งที่ **ทดลองไว้แล้ว** (สคริปต์ทดลองอยู่ใน scratchpad ไม่ได้ commit) :
- เรนเดอร์ PDF 2 ฉบับที่ DPI เดียวกันแล้ว `absdiff` → **deterministic 0 พิกเซล**
  (false positive 0) · จับความต่างเล็กถึง **0.8pt** ได้ · ~0.29 วิ/หน้า ที่ 300 DPI
- **ข้อจำกัดที่ต้องแก้ก่อนใช้จริง: ขนาดหน้าต้องเท่ากัน** — ย่อ/ขยายแบบ naive แล้ว
  เทียบได้ **370 บริเวณปลอม** ⇒ ต้อง align/normalize (หรือบังคับให้สองไฟล์เป็น
  หน้าขนาดเดียวกัน) ก่อนเสมอ
- จุดแข็ง: ไม่ง้อ OCR / ไม่ง้อภาษา / ไม่ง้อการลากโซน และจับได้มากกว่าข้อความ
  (ฟอนต์เปลี่ยน สีเพี้ยน โลโก้ขยับ แท่งบาร์โค้ดเปลี่ยน)
- ต้องเป็น **โหมดใหม่แยก** ไม่ใช่แทนที่ของเดิม และ **advisory ก่อน** (กฎเหล็กข้อ 1)

### ⚠️ กติกาที่รอบนี้ยึด และรอบหน้าต้องยึดต่อ
- ทุกอย่างที่เพิ่มเป็น **advisory 100%** — ไม่แตะ `defects` / `verdict` / การนับ / DB
- flag ใหม่ทุกตัว **ปิดแล้วได้พฤติกรรมเดิมเป๊ะ** (พิสูจน์ด้วย md5 ของ crop และเทสต์)
- **ไม่แตะโหมด** Live / RTSP / STREAM / Snapshot / Label เลยแม้แต่บรรทัดเดียว

### ✅ ผลทดสอบซ้ำแบบอิสระ (18 ส.ค. 2026, branch `claude/artwork-mode-station-testing-llsrp0`)

ทดสอบ **บน container Linux + Chromium จริง** (ไม่ใช่สถานี Windows) โดยรัน `app.py`
ตัวจริงพร้อม stub `ultralytics`/`pyodbc` — ดูวิธีในหัวข้อ "🧪 วิธีทดสอบ UI จริง" ด้านล่าง.
ทั้ง 6 ข้อผ่านหมด **วัดจาก DOM/สีจริง ไม่ใช่การอ่านโค้ด**:

| # | สิ่งที่วัด | ผลที่ได้จริง |
|---|---|---|
| 1 | 3 โซนคนละกลุ่ม → แถบเตือน | `aw-cov warn`, `rgb(255,248,225)` = เหลือง, "ไม่มีโซนใดตั้ง กลุ่ม ตรงกันเลย" |
| 2 | ตั้ง 2 โซนเป็นกลุ่ม `A` | แถบเทา `rgb(248,250,252)` + "ทำงาน (กลุ่ม A)" + จับ `MISMATCH_PANELS` 2 (170 vs 185 g) |
| 3 | verdict ตาม coverage | มีชั้นขาด → "✅ PASS — **ไม่พบประเด็นในชั้นที่ตรวจ**" · ไม่มีชั้นขาด → "✅ PASS — ไม่พบประเด็น" |
| 4 | autosave + F5 | แถบเขียว `rgb(232,245,233)` "💾 พบงานที่ค้างไว้ — 3 โซน" · กู้คืนได้ครบ **ทั้ง id และกลุ่ม** (`z1[A] z2[A] z3[C]`) · "ทิ้ง" ล้าง localStorage แล้วไม่เสนออีก |
| 5 | หน้าประวัติ | `.aw-img-pair` = `grid 551px 551px`, การ์ดมีกรอบ, `scrollWidth == innerWidth` (ไม่ล้นจอ), แถบ coverage แสดงครบ |
| 6 | ปุ่มซูม + วาดต่อเนื่อง | พอดีความกว้าง 86% (ยังล้นแนวตั้ง) · **พอดีทั้งหน้า 63% (ไม่ล้นทั้งสองแกน)** · ติ๊กแล้วลากรวด 3 โซนโดยกดปุ่มครั้งเดียว · Esc ออกได้ · ไม่ติ๊ก = พฤติกรรมเดิมเป๊ะ |
| + | **N8N ล่ม 1 โซน ไม่ล้มทั้งใบ** (`e45fff0`) | โซนพื้นที่ว่างยิง N8N ที่ต่อไม่ติด → `UNREADABLE` 1 รายการ **แต่ชั้นเทียบข้ามแผงยังทำงานและรายงานออกครบ** |

`diagnose_n8n_ocr.py --ping-only` แสดง `N8N_OCR_RETRIES` / `N8N_OCR_STRICT_RESPONSE`
ครบ และ retry ทำงานถูกต้อง (ต่อไม่ติด → ลองซ้ำ 1 ครั้ง แล้วรายงานสาเหตุตรงจุด).
pytest: **539 ผ่าน / 9 skipped / 5 fail** (5 ตัวเดิมของ Label Paper `NameError: FieldResult`).
ระหว่างทดสอบทั้งหมด **ไม่มี HTTP 500 และไม่มี JS error** (404 เดียวคือ `/favicon.ico` ตามปกติ).

**⚠️ ทดสอบที่นี่ไม่ได้ ต้องทำบนสถานี:** ยิง N8N จริง (`--ping-only` ข้อ ②) ·
`verify_ocr.py --layers probe` กับโฟลเดอร์ `TEST` · ทุกโหมดที่ใช้กล้อง/SQL Server.

### 🧰 `verify_artwork_features.py` — "เครื่องนี้พร้อมใช้งานจริงไหม" (รันบนสถานีได้เลย)

`py -3.9 verify_artwork_features.py [--n8n] [--only B,C] [--verbose]` — **อ่านอย่างเดียว**
(ชั้น B ใช้ temp dir ไม่แตะ `data/`), ไม่ต้องมีกล้อง/SQL Server/เบราว์เซอร์.
ต่างจาก `pytest` ตรงที่ตอบว่า *เครื่องที่จะใช้งานจริงพร้อมไหม* — วัดจากโค้ดบนดิสก์,
คอนฟิกที่ resolve ได้จริง, แพ็กเกจที่ติดตั้งจริง และเรียก `pipeline.run_inspection` ตัวจริง.
6 ชั้น: **A** commit ครบ 10 ตัว + flag ใหม่ 4 ตัว + `pyspellchecker` (จุดบอด QC ถ้าไม่มี) ·
**B** coverage/MISMATCH_PANELS/verdict จาก PDF 3 แผงที่สร้างสด + จำลอง backend ระเบิด ·
**C** แกะคำตอบ N8N 10 แบบผ่าน **mock HTTP server ในเครื่อง** (ยิงผ่าน `requests` จริง
รวม retry 500 / ไม่ retry 404 / URL ผิดรูป) · **D** text layer เสีย + เพิ่ม DPI โซนเล็ก ·
**E** ของที่ "พังเงียบ": CSS ครบ 2 template, `ZOOM_MIN`↔`min=`, `HL_*`, `GROUP_LETTERS`,
id ที่ JS อ้าง, ตำแหน่ง guard · **F** ยิง N8N จริง (`--n8n`).
exit `0`/`1`/`2`. **พิสูจน์แล้วว่าจับของจริงได้** ไม่ใช่ผ่านอย่างเดียว: รันบน worktree
ที่ `d460924` (ก่อนงาน UX) → **ไม่ผ่าน 8 ข้อ** ตรงจุด (commit ขาด, CSS หน้าประวัติ,
`#awZoomFitPage`/`#awDrawContinuous`/`#awRestore` หาย, ข้อความ verdict, autosave) ·
รันด้วย `N8N_OCR_STRICT_RESPONSE=0` → จับได้ว่าหน้า HTML จะถูกใช้เป็นข้อความ.
> ⚠️ ตัวอย่างข้อความในชั้น D ต้องมี **คำยาว ≥ 8 ตัวอักษร อย่างน้อย 8 คำ** ไม่งั้น
> `text_looks_garbled` จะไม่ยอมตัดสินเลย แล้วทั้งเคสดี/เคสเสียได้ `False` เหมือนกัน =
> ผ่านแบบไร้ความหมาย (มีข้อเช็คกันไว้ในสคริปต์แล้ว).

### 🏭 ผลรันบนสถานีจริง (18 ส.ค. 2026, Python 3.9.13, `f6602a4`)

`py -3.9 verify_artwork_features.py --n8n` → **ผ่าน 57 / ไม่ผ่าน 0** ·
footer/`CONFIG_VERSION` = `2026.08.16-aw-ux` · commit ครบ 10 ตัว · `pyspellchecker` มี.
**N8N ทำงานถูกต้อง**: ยิงจริง HTTP 200, อ่านภาพทดสอบได้ (`DIAGNOSE 12345`),
และ **คืน `blocks` พร้อม bbox 2 ก้อน**.

**⚠️ ข้อเดียวที่ยังเปิดอยู่ — tesseract binary ยังไม่ได้ติดตั้งบนสถานี**
(`pytesseract` รายงาน *"tesseract is not installed or it's not in your PATH"*).
ไม่ใช่จุดบอด QC (ผลตรวจ PASS/FAIL เท่าเดิมทุกอย่าง) แต่ทำให้ **กรอบแดงหายไปมากกว่าที่คิด**
เพราะชั้น ③ ต้องใช้ Tesseract "พิสูจน์" bbox ของ LLM:
`_verify_boxes(..., require_positive=True)` มี `except: return [] if require_positive`
⇒ **ไม่มี Tesseract = bbox ของ Gemini ถูกทิ้งทุกครั้ง** แม้ N8N จะคืน bbox มาให้แล้วก็ตาม.

| ไฟล์ | ชั้นที่ใช้ได้ตอนนี้ | กรอบแดง |
|---|---|---|
| PDF ที่มี text layer | ① PDF word box | ✅ ขึ้นปกติ |
| PDF outline / ภาพถ่าย | ② ไม่มี · ③ ถูกทิ้งเพราะพิสูจน์ไม่ได้ | ❌ **ไม่ขึ้นเลย** |

แก้ด้วยการติดตั้ง UB-Mannheim installer + `py -3.9 -m pip install pytesseract`
(+ `ARTWORK_HIGHLIGHT_TESS_LANG=eng+ara` ถ้ามีฉลากอาหรับ) — `_find_tesseract_cmd()`
หา exe เองไม่ต้องตั้ง PATH.

### 🎯 วัด hallucination ของ Gemini บนไฟล์จริง (18 ส.ค. 2026)

`verify_ocr.py --files TEST --engines n8n --layers probe --n8n-limit 25` (11 ไฟล์จริง):
**โซนที่แต่งขึ้น 0/25 · ยิงสำเร็จ 25 / ล้มเหลว 0** — ทุกโซนว่างคืน **0 ตัวอักษร**
(response 51 bytes = `{"text":"","blocks":[]}`) ⇒ **prompt ที่ใช้อยู่ผ่านข้อที่อันตรายที่สุด
ของงาน QC แล้ว ไม่ต้องแก้เรื่อง "ห้ามเดา"**. เท่ากับ Tesseract ที่เคยวัดได้ 0/36.
- ⚠️ verdict พิมพ์ว่า **"สรุปไม่ได้"** = ถูกต้องตามออกแบบ (ข้าม GROUND TRUTH ตาม
  `--layers probe`) **ไม่ใช่ความล้มเหลว** — ยังไม่ได้วัด recall/precision.
  ปิดช่องนี้ด้วยรันที่สอง: `--layers truth --n8n-limit 30`
- โควตา: 11 ไฟล์ × 3 โซน = 33 ⇒ `--n8n-limit 25` ทำให้ **8 โซนสุดท้ายถูกข้าม** (ใช้ 33 ถ้าอยากครบ)

**ข้อค้นพบจากชั้น TRIAGE ที่กระทบการใช้งานจริง** — แบนเนอร์ "✅ ไฟล์นี้มี text layer"
เป็น **ระดับหน้า** แต่ความจริงคือ:

| ไฟล์ | text layer ปกคลุมหน้า | สิ่งที่เกิดขึ้นจริง |
|---|---|---|
| 4 ไฟล์ | **0%** | outline ทั้งใบ — ทุกโซนยิง N8N |
| 3 ไฟล์ | **1-6%** | แบนเนอร์บอกว่า "มี" แต่**เกือบทุกโซนยังยิง N8N** |
| 4 ไฟล์ | 10-30% | ผสมกัน — ต้องดูรายโซน |

⇒ งานจริงส่วนใหญ่วิ่งผ่าน N8N ไม่ใช่ text layer ⇒ (ก) โควตา/เวลา Gemini คือคอขวดจริง
(ข) **การไม่มี Tesseract กระทบมากกว่าที่คิด** เพราะไฟล์ 0% พวกนี้คือกลุ่มที่กรอบแดงหายทั้งหมด.

### ⚖️ N8N (Gemini) vs Tesseract บนเฉลยชุดเดียวกัน (18 ส.ค. 2026)

**ติดตั้ง Tesseract v5.5.3 บนสถานีแล้ว** (`eng+ara+deu`) ⇒ **ช่องว่างกรอบแดงปิดแล้ว**
(ชั้น ② กลับมาใช้ได้ และชั้น ③ พิสูจน์ bbox ของ Gemini ได้แล้ว).

`verify_ocr.py --engines tesseract,n8n --layers truth --zones 4 --n8n-limit 30`
→ 13 โซนจาก 6 ไฟล์ที่มี text layer (อีก 5 ไฟล์ outline ทั้งใบ ไม่มีเฉลย):

| engine | recall | precision | ผล |
|---|---|---|---|
| **n8n (Gemini)** | **98.4%** | **95.8%** | ✅ ผ่านทั้งสองเกณฑ์ (≥95%) |
| tesseract | 92.0% | 89.6% | ❌ ไม่ผ่าน |

**ความล้มเหลวของ Tesseract กระจุกอยู่ที่ 2 โซนเท่านั้น** (โซน 3 ของไฟล์ Cosma ทั้งสองฉบับ:
75.4%/68.1% และ 78.9%/79.1% เทียบกับ n8n 96.1% และ 100%) — โซนที่เหลือสูสีกันมาก
(98.0/97.3 · 100/99.5 · 100/94.4 · 100/100). **ตั้งข้อสังเกตว่าเป็นเรื่องภาษา ไม่ใช่คุณภาพ engine**:
เฉลยของโซนนั้นมีคำโปแลนด์ (`SKŁAD` = ส่วนผสม) แต่รันนี้โหลดแค่ `eng+ara+deu`
⇒ **ยังไม่ยืนยัน** ปิด loop ด้วย `--tess-lang eng+ara+deu+pol+ces+hun+slk` (และลอง `--tess-psm 3`).

**⚠️ ตัวเลข 92% ของ Tesseract ไม่ได้แปลว่าผลตรวจแย่ลง** — production **ไม่เคยใช้ Tesseract
อ่านข้อความ** (ใช้ text layer หรือ N8N เท่านั้น ดู `ocr.read_zone`). Tesseract มีหน้าที่
**วัดตำแหน่งคำเพื่อวาดกรอบแดง** (ชั้น ②/③) ซึ่งเป็น display-only. ⇒ ข้อสรุปเชิงนโยบาย:
**ใช้ N8N เป็น engine อ่านต่อไป · Tesseract คงบทบาทชั้นกรอบแดง · อย่าเอามาแทนกัน**.

**⚠️ อ่านคอลัมน์ "เฉลย" อย่างระวัง** — `[?] เฉลยจาก text layer มีคำผิดรูป` บางครั้งเป็น
**false alarm**: รหัสงานจริงบนฉลาก (`3AAOSO34JARNTEA82W`, `5F0JU260N000002603`) หน้าตา
เหมือน token ที่ฟอนต์แมปผิดพอดี. ในไฟล์ [1] n8n อ่านรหัสพวกนี้ได้ครบ 100% ส่วน Tesseract
ตก 2 คำ (85.7%) ⇒ ตรงนั้นเฉลย**ถูก**แล้ว ไม่ใช่เฉลยเสีย.

### 🔀 กับดัก branch: `main` **ไม่มี** 5 commit สุดท้ายของรอบที่แล้ว
PR #37 ถูก merge ตอน branch อยู่ที่ `d460924` ⇒ `origin/main` (`90430b7`) ยังเป็น
**`CONFIG_VERSION = 2026.08.15-n8n-parse`** และ **ไม่มี** `20e3af1` `008ca38` `5d2f91a`
`a99d31f` `e45fff0` (verify_ocr `--layers` · UX 4 ตัว · CSS หน้าประวัติ · กันโซนเดียวล้มทั้งใบ).
⇒ **ถ้าสถานี `git pull` จาก `main` แล้ว footer จะขึ้น `2026.08.15-n8n-parse` ตลอด
ไม่ว่ารีสตาร์ตกี่รอบ** — ต้อง checkout branch ที่มีงานจริง (`claude/artwork-multi-zone-errors-k8linm`
หรือ `claude/artwork-mode-station-testing-llsrp0` ซึ่ง merge ไว้แล้ว) หรือรอ merge เข้า main.

---

## 🧪 วิธีทดสอบ UI จริงบนเครื่อง dev (ไม่มี ultralytics/กล้อง)

`app.py` import `ultralytics` → เครื่อง dev ที่ไม่มี torch รันไม่ได้. แต่โหมด Artwork
เป็น blueprint แยก จึงยกมารันเดี่ยวได้ **โดยใช้ template/static/route ตัวจริง**:

```python
# scratchpad/aw_server.py
import sys; sys.path.insert(0, "/path/to/Digital_Vision2026")
from flask import Flask
from artwork_check.routes import artwork_bp
import config as appcfg
ROOT = "/path/to/Digital_Vision2026"
app = Flask(__name__, template_folder=ROOT+"/templates", static_folder=ROOT+"/static")
@app.context_processor
def _ctx():                      # base.html ต้องการตัวแปรพวกนี้
    return {"config_version": appcfg.CONFIG_VERSION, "current_user": None,
            "auth_enabled": False, "has_perm": lambda *a, **k: True}
app.register_blueprint(artwork_bp)
app.run(host="127.0.0.1", port=5990, threaded=True)
```
แล้วขับด้วย Playwright + Chromium ที่มีอยู่ในเครื่อง
(`executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome"`).

**อีกท่าที่ตรงกับ production มากกว่า (ใช้ในรอบทดสอบ 18 ส.ค.):** รัน `app.py` **ตัวจริง**
โดย stub เฉพาะแพ็กเกจที่เครื่อง dev ไม่มี แทนที่จะยก blueprint มารันเดี่ยว —
ได้ทดสอบ `base.html`/context processor/ลำดับการลงทะเบียน blueprint ตัวจริงไปด้วย:
```bash
mkdir -p stubs                      # ultralytics.py = class YOLO ที่ raise, pyodbc.py = connect() ที่ raise
PYTHONPATH=stubs AUTH_ENABLED=0 FLASK_PORT=5000 python3 app.py
```
`load_model()` / `Database` มี fallback อยู่แล้ว จึงได้แค่ log ERROR แล้วเว็บขึ้นปกติ
(โหมด Artwork ไม่แตะทั้งสองอย่าง). `AUTH_ENABLED=0` เพราะ auth ต้องใช้ SQL Server จริง.
⚠️ ไม่มี cert ⇒ `USE_HTTPS=True` จะ fallback เป็น HTTP เอง (ตามโค้ดใน `app.py`) — ปกติ.

**กับดักที่เจอตอนเขียนเทสต์ (อย่าเสียเวลาซ้ำ):**
1. **`template_folder` ต้องเป็น path เต็ม** — Flask หา template จากโฟลเดอร์ของสคริปต์
   ไม่ใช่ `os.chdir()`.
2. **ต้อง stub `has_perm`** ไม่งั้น `base.html` โยน `UndefinedError`.
3. **รอภาพโหลดจริงก่อนวัด** — `wait_for_selector` ไม่พอ ต้อง
   `wait_for_function("i.complete && i.naturalWidth>0")` ไม่งั้นวัดขนาดได้ค่าเก่า.
4. **ต้อง `scroll_into_view_if_needed()` ก่อนลากเมาส์** — กล่องภาพสูงกว่าจอ พิกัดที่
   คำนวณจาก `bounding_box()` อาจติดลบ (อยู่นอก viewport) แล้ว mousedown ตกน้ำเงียบ ๆ
   (เสียเวลาไล่หาสาเหตุนานเพราะไม่มี error อะไรเลย).
5. **404 `/favicon.ico` เป็นเรื่องปกติ** — โปรเจกต์ไม่เคยมีไฟล์นี้ ไม่ใช่บั๊กใหม่.
6. ล้าง `data/artwork_check/inspections/2026*` หลังทดสอบ (อยู่ใน `.gitignore` git
   จึงไม่เตือน แต่ค้างไว้จะไปโผล่ในหน้าประวัติของผู้ใช้).

---

## 🧠 โมเดล (สำคัญมาก — เคยพลาดตรงนี้)

- **`best.pt`** = YOLOv8 **detection** ธรรมดา (3M params). classes: `dented`, `good`. โหมด can_dent default.
- **`bestX.pt`** = **YOLOv8m-SEGMENTATION** (27M params!) — **นี่คือโมเดล production ที่ผู้ใช้ใช้จริง**.
  classes: `dent`, `can`. output = 2 tensor `((1,38,21504),(1,32,256,256))`. verdict NG/OK ใน
  `classify_frame_bestx`. `is_bestx_mode` = เช็คชื่อไฟล์ == "bestx.pt".
- คลาส **non-defect** = `{"good", "can"}` (`_NON_DEFECT_CLASSES` ใน app.py) = "กระป๋องทั้งใบ".
  ตอน NG กล่อง `can` ถูก **ซ่อนตอนแสดงผล** แต่ยังอยู่ใน raw detections (ใช้เช็ค "ครบใบ" ได้).

---

## ⚡ ONNX acceleration (`USE_ONNX=True` — ปัจจุบันเป็นชั้น fallback ใต้ iGPU)

- Export `.pt`→`.onnx` (FP32, `dynamic=True`, opset 17) ครั้งเดียว แล้วรันผ่าน onnxruntime (~2x เร็วขึ้น).
  ultralytics ถอดผล/NMS เอง → **ผลตรวจเท่า PyTorch เป๊ะ** (verify แล้ว IoU 1.0, Δconf 0.0).
- **⚠️ Python 3.9 pin ที่ต้องรู้:**
  - `onnxruntime==1.19.2` = wheel cp39 ตัวสุดท้ายบน Windows (1.20+ ตัด py39 ทิ้ง).
  - `onnx` pip เลือก cp39 ให้เอง (~1.16/1.17). `onnxslim` (py3.8+) แนะนำติดตั้ง.
  - ติดตั้ง: `py -3.9 -m pip install onnxruntime==1.19.2 onnxslim onnx`
- **⚠️ กับดัก segmentation:** export ONNX ทิ้ง task tag → `YOLO(onnx)` เดาเป็น `detect` → ถอด output
  ของ seg ผิด = **ไม่มีกรอบเลย**. แก้แล้วใน `yolo_detector._accel_task()`: อ่าน task จาก `.pt`
  แล้วโหลด `YOLO(onnx, task='segment')` (cache ใน `<onnx>.task` sidecar).
- **`verify_onnx.py`** = ตาข่ายนิรภัย. รันเทียบ `.pt` vs `.onnx` ให้ PASS ก่อนเปิด `USE_ONNX`.
  ต้องรันต่อโมเดล: `py -3.9 verify_onnx.py --weights weights\can_dent\bestX.pt --images <โฟลเดอร์>`
- **fallback หลายชั้นใน `load_model()`**: pkg หาย/export/load/smoke-test ล้มเหลว → กลับ PyTorch อัตโนมัติ.
  stale guard: `.pt` ใหม่กว่า `.onnx` → re-export.

## 🚫 OpenVINO — ค่าเริ่มต้นปิด (`USE_OPENVINO=False`) — เคยพังเงียบ

เคยเปิด `openvino==2025.3.0` บน **py3.9 (off-spec: 2025 ต้องการ Python ≥3.10)** → export/โหลด
สำเร็จ **แต่ตรวจไม่เจอทุกโหมดแบบเงียบๆ** (ไม่ error). นี่คือ failure mode ที่อันตรายที่สุด:
**ห้ามเชื่อว่าใช้ได้แค่เพราะโค้ดรันไม่ error — ต้องผ่าน `verify_onnx.py` เท่านั้น**.

## 🎮 iGPU (Iris Xe) acceleration — ✅ VERIFIED & ENABLED บนสถานี (2 ก.ค. 2026)

**สถานะ: เปิดใช้จริงใน production แล้ว** — `OPENVINO_DEVICE = "intel:gpu"`,
`CONFIG_VERSION = "2026.07.02-ov-igpu-ON"`, `openvino==2024.6.0` ติดตั้งบนสถานี
(pip ถอน 2025.3.0 ตัว off-spec ออกให้ตอนติดตั้ง).

**ตัวเลขจริงบนสถานี (จาก log `YOLO inference avg` + verify_openvino.py):**
- **bestX (seg, production) live 480: ~45-50ms/เฟรม (~20-22 FPS) ≈ เร็วขึ้น ~6 เท่า**
  จากเพดาน ONNX CPU เดิม ~280ms (~2.7 FPS). เร็วกว่าตอน verify (137ms) เพราะเฟรม
  live 640x480 ไม่มี cost ย่อภาพใหญ่. snapshot 1280: 420ms (เดิม ONNX ~1739ms).
- best.pt (detect) บน GPU: ~14ms (~70 FPS).
- **Coverage (โจทย์ตั้งต้น): จบ** — 1-2 วิในเฟรม × ~21 FPS = 20-40+ ครั้ง/ใบ (เป้า 4-5).
- verify_openvino.py (bestX): **PASS ทั้ง intel:cpu+intel:gpu × 480+1280** —
  GPU มี FP16 drift จริงแต่เล็กมาก (IoU 0.9809-0.9913, Δconf ≤0.0053, กล่อง/คลาสตรงหมด;
  CPU ตรงเป๊ะ IoU 1.0/Δconf 0.0).

**⚠️ งานค้าง (บันทึกไว้ตามจริง — ควรปิดเมื่อสะดวก):**
1. ชุดภาพ verify ตอนเปิดใช้มีแค่ 1 รูป (ต่ำกว่ามาตรฐาน ≥10-20 รูปของ repo) — ปิด loop:
   `py -3.9 dump_defect_images.py --limit 30` (ดึงภาพ NG จริงจาก DB → sample_cans)
   แล้วรัน verify_openvino.py กับ bestX อีกรอบ.
2. `best.pt` (โหมด detect) วิ่งบน GPU ด้วยแต่ยังไม่เคยผ่าน verify_openvino.py แยกของตัวเอง
   — รัน `py -3.9 verify_openvino.py --weights weights\can_dent\best.pt --images <โฟลเดอร์>`.

**Rollback:** ตั้ง `OPENVINO_DEVICE = None` + รีสตาร์ต = กลับ ONNX CPU เดิม 100% ทันที.
ถ้า GPU พังเองระหว่างรัน load_model() fallback → ONNX → PyTorch อัตโนมัติ (ดู log).

**ข้อเท็จจริงเวอร์ชัน (re-verify จาก PyPI + ซอร์ส ultralytics v8.4.41, ก.ค. 2026):**
- **`openvino==2024.6.0` = ตัวที่ถูกต้องสำหรับ py3.9** — รุ่นสุดท้ายที่มี wheel
  `cp39-win_amd64` **และ**อยู่ในช่วง `openvino>=2024.0.0` ที่ exporter ของ
  ultralytics 8.4.41 ต้องการอย่างเป็นทางการ (ไม่ต้องใช้ 2023.x ที่เสี่ยง mismatch).
- ultralytics 8.4.41 รองรับ `device="intel:gpu"` ตอน predict ในตัว (`select_device`
  ส่งผ่าน string `intel:*` ตรงๆ, OpenVINOBackend parse เป็น device_name="GPU").
- `onnxruntime-openvino` ตัดทิ้ง: รุ่นใหม่ต้อง py≥3.10, รุ่นเก่าชน onnxruntime 1.19.2.
- **⚠️ GPU plugin ของ OpenVINO default รันภายในเป็น FP16** แม้ IR เป็น FP32 →
  ความแม่นตัดสินด้วย `verify_openvino.py` เท่านั้น. ถ้า FAIL เพราะ drift → แผนสำรอง
  คือบังคับ `INFERENCE_PRECISION_HINT=f32` (ช้าลง, ต้องแก้เพิ่ม — ยังไม่ทำ).

**โครงสร้างโค้ด (เปิดใช้แล้ว):**
- `config.OPENVINO_DEVICE = None` (opt-in; ตั้ง `"intel:gpu"` เพื่อเปิด) — แยกจาก
  `USE_OPENVINO` เดิม. default None = ทุกโหมดทำงานเท่าเดิม 100%.
- `_select_backend()` เปลี่ยนเป็นคืน **candidate list**: OpenVINO@device (ถ้าตั้ง
  flag) → ONNX CPU → OpenVINO (legacy) → PyTorch; `load_model()` ไล่ลองทีละตัว
  (load + smoke test) → **fallback GPU→ONNX→PyTorch อัตโนมัติ**. flag ปิด = ลำดับเดิมเป๊ะ.
- `_maybe_openvino()`: เช็ค device มีจริงผ่าน `ov.Core().available_devices` ก่อน
  (กัน OpenVINO เงียบๆ fallback ไป AUTO/CPU เอง = ตัวเลขความเร็วหลอก) + stale guard
  (`.pt` ใหม่กว่า IR → re-export) + โหลดด้วย task จาก `_accel_task()` เหมือน ONNX.
- **`verify_openvino.py`** = ตาข่ายนิรภัย (เกณฑ์ import จาก `verify_onnx.py` ชุดเดียวกัน):
  เทียบ PyTorch vs OpenVINO ทั้ง `intel:cpu`+`intel:gpu` ที่ 480+1280 + วัดความเร็ว
  PyTorch/ONNX/OpenVINO ในรันเดียว:
  `py -3.9 verify_openvino.py --weights weights\can_dent\bestX.pt --images <โฟลเดอร์>`

**ถ้าต้องเปิดใช้ใหม่บนเครื่องอื่น (ตามลำดับ ห้ามข้าม):** (1) `py -3.9 -m pip install
"openvino==2024.6.0"` (2) เช็ค `ov.Core().available_devices` มี GPU (3) รัน
verify_openvino.py ต้อง PASS ทุก device×imgsz (4) เช็คตัวเลขความเร็วคุ้ม
(5) ตั้ง `OPENVINO_DEVICE="intel:gpu"` + รีสตาร์ต + เช็ค footer/log.

**Route B (ไม่ต้องใช้แล้ว — Route A ผ่าน):** Python 3.11 ใน venv แยก เก็บไว้เป็น
ความรู้เผื่ออนาคต (เช่นถ้าจำเป็นต้องอัป openvino/onnxruntime เกินรุ่นสุดท้ายของ py3.9).

**บังคับทุก Route:** (ก) verify เทียบ PyTorch ผ่าน (IoU ≥0.97, Δconf ≤0.05, จำนวนกล่องตรง,
เคส "GPU เจอ 0 แต่ PyTorch เจอหลายกล่อง" = FAIL) (ข) fallback อัตโนมัติกลับ CPU-ONNX→PyTorch
(ค) opt-in flag default ปิด. **⚠️ RAM single-channel = คอขวด iGPU** → คาดจริง ~120-180ms
(ไม่ใช่ 80ms) ถ้าเอาเต็มควรอัป RAM dual-channel ก่อน.

---

## 📷 กล้อง & imgsz

- Live: 640x480 @ 30fps, `CAMERA_FOURCC=None` (YUY2 — "MJPG" ทำภาพแตกบน MSMF). กล้องตัน 720p ผ่าน MSMF.
- **imgsz: live=480, snapshot=1280. ⚠️ ห้ามต่ำกว่า 480** — dent เป็นฟีเจอร์เล็ก ลดเป็น 320 = ตรวจไม่เจอเลย.
- **Exposure/Brightness** (`CAMERA_AUTO_EXPOSURE`/`CAMERA_EXPOSURE`/`CAMERA_BRIGHTNESS`, opt-in default
  None): เฉพาะกล้อง **live** (ส่งผ่าน ctor ที่ site สร้างกล้อง live; snapshot/RTSP ไม่แตะ). best-effort.
  - **⚠️ กล้องสถานีนี้: EXPOSURE/GAIN/GAMMA ตั้งไม่ได้ผ่าน OpenCV แต่ `BRIGHTNESS` (0-255) ได้** —
    พิสูจน์ด้วย `diagnose_exposure.py` (เทสต์ว่าภาพสว่างเปลี่ยนจริงต่อ knob/backend).
  - ปรับสดขณะรัน: สไลเดอร์ในแผง USB → `POST /api/camera/control` {control, value} →
    `Camera.set_control(name, value)` (brightness/contrast; มี `_cap_lock` กัน race กับ
    `capture_loop`). StreamCamera ไม่มี method นี้ (endpoint คืน error). CONTRAST = knob ทดลอง
    (อาจช่วยหรือแย่ลง — domain shift; ยังไม่ยืนยันว่ากล้องรับ) ต้องเทียบผลตรวจจริงก่อนใช้.
- `Camera` class ใช้ร่วมทั้ง live+snapshot (แยกด้วย ctor params). RTSP → `_initialize_rtsp` (ไม่ทำ exposure).

---

## 🏗️ สถาปัตยกรรมสำคัญ (app.py)

- **Live USB/RTSP** = 2 thread: `capture_loop` (อ่านกล้อง → `latest_raw_frame`) +
  `inference_loop` (infer, นับ, log DB). `generate_frames` = MJPEG generator.
  - `LIVE_SMOOTH_VIDEO`: `False`=วาดกรอบบนเฟรมที่ infer จริง (กรอบเป๊ะ, ภาพตามอัตรา infer) /
    `True`=วาดกรอบล่าสุดบนเฟรมดิบล่าสุด (ภาพลื่น, กรอบตามช้าตอนขยับ). **เปิด Frame Capture =
    บังคับ smooth อัตโนมัติ** (`smooth = frame_capture_enabled or LIVE_SMOOTH_VIDEO` ประเมินสด
    ทุก loop) — ความแม่นกรอบไปอยู่ที่เฟรมที่แช่ (re-infer แล้ว) ส่วนภาพสดแค่ monitor.
- **STREAM** = client-side ล้วน: เบราว์เซอร์เปิดกล้องตัวเอง (`getUserMedia`, ต้อง HTTPS) →
  POST เฟรมไป `/api/stream/infer` → คืน JSON กรอบ → วาดบน canvas. **per-client isolation**
  (ไม่แชร์กล้อง/pipeline). JS อยู่ใน `templates/index.html` (ค่าคงที่ `STREAM_*`).
- **นับ 1 กระป๋อง = 1 การตรวจ (edge-triggered)**: state `none/ok/ng` ต่อกระป๋อง, นับ+log DB
  ครั้งเดียวตอน rising edge (none→ng), กระป๋อง "หายไป" หลัง `DEFECT_RESET_FRAMES` เฟรมว่าง.
  USB/RTSP อยู่ใน `inference_loop`; STREAM อยู่ใน JS (`streamInferLoop`).
- **Frame Capture** (display-only, USB/RTSP): `capture_loop` ให้คะแนนความคมทุกเฟรมดิบ
  (candidate pool) เฉพาะตอน `frame_capture_enabled AND pool_collecting`; `inference_loop`
  ตั้ง `pool_collecting = defect AND _can_complete()` (กระป๋องครบใบ), รีเซ็ต pool ต่อกระป๋อง,
  ตอนกระป๋องผ่านไป **re-infer เฟรมที่เลือก** (ให้กรอบตรง) → publish เป็น JPEG แช่ 5 วิ ใน
  `generate_frames`. **ไม่กระทบการนับ/DB**. Toggle ผ่าน `POST /api/frame_capture`.

---

## 🖍️ Artwork — กรอบแดงชี้ "คำที่มีปัญหา" (display-only)

การ์ด "รายการที่พบ" วาด **กรอบแดงบนคำที่ผิดจริง** ในรูป crop. **แสดงผลอย่างเดียว 100%** —
ไม่แตะ OCR/ผลตรวจ/verdict/การนับ. โค้ดอยู่ใน `artwork_check/highlight.py` (โมดูลใหม่),
เรียกจาก `pipeline.zone_crop_jpg()` เมื่อ `/api/artwork/<id>/crop?...&hl=<คำ>&zid=<โซน>`.

**สถาปัตยกรรม 4 ชั้น** (`highlight.locate_all()` — ไล่จากแม่นสุด ชั้นแรกที่เจอชนะ):

| ชั้น | วิธี | ใช้เมื่อ | ความแม่น (ไฟล์จริง) |
|---|---|---|---|
| 1 | **PDF text-layer word box** (`pdf_ingest.zone_words()`) | โซน `engine == "pdf-text"` | **เป๊ะระดับ vector, ทุกภาษา** |
| 2 | **Tesseract** (`_tess_boxes`) | ไฟล์ outline / ภาพถ่าย | 89% (benchmark), วัดจากพิกเซลจริง + self-verify |
| 3 | `blocks[].bbox` จาก OCR backend (`_block_boxes`) | Tesseract หาไม่เจอ/ไม่มี | **พิกัดจาก LLM = การประมาณ ต้องผ่าน `_verify_boxes` ก่อน** |
| 3b | **พิสูจน์ "แถว" แทนการอ่านคำ** (`_verify_boxes_by_row`) | คำที่ Tesseract ไม่มี traineddata | วัดแถวจากคำ ASCII ข้างเคียง |
| 4 | ไม่วาด | ไม่มั่นใจ | — |

⚠️ **ลำดับนี้เคยสลับกันแล้วพัง:** เดิมให้ `blocks[].bbox` มาก่อน Tesseract และ **ไม่ตรวจสอบเลย** →
บนสถานีจริงที่ Gemini คืน bbox มา กรอบไปโผล่คนละแถวในตารางโภชนาการ (LLM ให้พิกัดแบบ
*ประมาณ* ไม่ใช่ *วัด*). เทสต์ตอนนั้นตั้ง `blocks=[]` ตลอดจึงไม่เคยเจอ — **ถ้าจะเพิ่ม/สลับชั้น
ต้องมีเทสต์ที่ป้อน bbox ที่ "เพี้ยนไปคนละแถว" ด้วยเสมอ**.

- **ชั้น ① ดีที่สุดและฟรี** — ไม่ต้อง OCR/traineddata, รองรับฮีบรู/อาหรับ/จีน/ไทยทันที.
  ตรวจด้วย `ArtworkDocument.zone_words(bbox)` → `[(text, (fx0,fy0,fx1,fy1))]` เป็น**สัดส่วนในโซน**
  → `rotate_frac_box()` (ตามการหมุนโซน) → `frac_to_px()`.
- **`_cv_box` (projection profile) = ชั้นสำรองสุดท้าย ปิดไว้ (`HIGHLIGHT_USE_PROFILE=False`)** —
  benchmark พบว่า **วาดผิดคำ ~40%** บนตารางหนาแน่น. ห้ามเปิดเป็น default.
- **วาดทุกจุดที่คำปรากฏ** (`HIGHLIGHT_MAX_BOXES=6`) — คำผิดมักพิมพ์ซ้ำหลายแถว
  (จริง: `Cude` โผล่ 3 จุดในตารางเดียว) วาดจุดเดียว = ผู้ตรวจแก้ไม่ครบ.

**⚠️ ชั้น 3b — ทำไมบางครั้ง "เจอคำผิดแต่ไม่มีกรอบแดง" (เคสจริง ส.ค. 2026):**
สถานีลง traineddata แค่ `eng` → คำอาหรับที่สะกดผิด (`كربوهيدات كلية`) **ไม่มีกรอบเลย**
ขณะที่ `24%` บนการ์ดถัดไปมีกรอบปกติ. ไล่แล้วพบว่าไม่ใช่บั๊กของการจับคู่ — ชั้น ② อ่านอาหรับ
ไม่ออก (0 กรอบ) ส่วนชั้น ③ **หา bbox เจอ (1 กรอบ) แต่ถูก `_verify_boxes(require_positive=True)`
ทิ้งทุกครั้ง** เพราะการ "อ่านซ้ำเพื่อพิสูจน์คำ" เป็นไปไม่ได้เมื่อไม่มี traineddata ของภาษานั้น
(อ่านอาหรับด้วย `eng` ได้แต่ขยะ). วัดจากเคสจำลองที่สร้างตามภาพจริง:

| ภาษาของคำ | ชั้น ② Tesseract | ชั้น ③ bbox | ผ่านพิสูจน์ | วาดจริง |
|---|---|---|---|---|
| อาหรับ (`eng` อย่างเดียว) | 0 | 1 | **0** | **0 ← บั๊ก** |
| อาหรับ (`eng+ara`) | 1 | 1 | 1 | 1 |
| `20%` / อังกฤษ | 1 | 1 | 1 | 1 |

- **ทางแก้ที่ดีที่สุด = ติดตั้ง traineddata แล้วตั้ง `ARTWORK_HIGHLIGHT_TESS_LANG=eng+ara`**
  (ตรงกับผลทดสอบเดิมของ repo: 25/25 ด้วย `eng+ara` เทียบ 23/25 ด้วย `eng`).
- **ชั้น 3b = ตาข่ายรองรับเมื่อไม่มี traineddata**: พิสูจน์ **"แถว"** แทน **"คำ"** —
  บรรทัดเดียวกันมีคำ ASCII ("Total carbohydrate 0 g 0%") ที่ Tesseract อ่านได้ ใช้พิกเซล
  ของคำนั้นล็อกย่านแนวตั้งของแถว แล้วรับ bbox ของ LLM เฉพาะที่ **กึ่งกลางตกในแถวนั้น**
  และ **ไม่สูงเกิน 1.2 เท่าของแถว**. นี่คือแกนที่ bbox ของ LLM เคยพลาดจริง (ไปโผล่คนละแถว)
  จึงเป็นการพิสูจน์ที่ตรงจุด ไม่ใช่การผ่อนเกณฑ์.
- **เงื่อนไขที่ทำให้ "ไม่วาด" (ตั้งใจให้เข้มไว้ก่อน)**: บรรทัดของคำต้องไม่ซ้ำในข้อความ backend ·
  คำ anchor ต้องยาว ≥4 ตัว ไม่ใช่ตัวเลข และปรากฏครั้งเดียวในภาพ · ใช้เฉพาะคำ **ที่ไม่ใช่ ASCII**
  (คำอังกฤษที่พิสูจน์ไม่ผ่าน = กรอบน่าสงสัยจริง ต้องคงพฤติกรรมเดิมคือไม่วาด).
- Config: `ARTWORK_HIGHLIGHT_ROW_VERIFY` (default `1`; ตั้ง `0` = กลับพฤติกรรมเดิม 100%).
- เทสต์: `tests/test_artwork_highlight.py` 11 ตัว (mock Tesseract ให้ "อ่านภาษานั้นไม่ออก"
  จึง deterministic ไม่ต้องพึ่ง binary) — รวมเคส **bbox เลื่อนไปคนละแถวต้องไม่วาด**.

**⚠️ กับดักที่เจอมาแล้ว (อย่าทำซ้ำ):**
1. **fuzzy match กับคำสั้น/CJK = กรอบผิดคำ** — จีน `灰分`(เถ้า) เคยจับกรอบเดียวกับ `水分`(ความชื้น)
   เพราะต่างกัน 1 ตัวอักษร. `_all_word_matches()` จึง **fuzzy เฉพาะคำ ascii ยาว ≥5** เท่านั้น
   (พอสำหรับ typo อังกฤษที่ตั้งใจจับ) — คำสั้น/CJK/RTL ต้อง exact/substring. **กรอบผิดแย่กว่าไม่มีกรอบ**.
2. **คืน "tier เดียว"** — ถ้ามี literal match แล้ว ห้ามเอา fuzzy มาปน (ไม่งั้นคำที่แค่คล้ายจะติดกรอบมาด้วย).
2b. **`found` ของ MISMATCH_* เป็น "ทั้งบรรทัด" ไม่ใช่คำเดียว** — จับคู่ทีละคำแล้วกระจายกรอบ
   ผิดแถวทันที (เคสจริง: `دهون كلية` ได้ 5 กรอบ เพราะ `دهون`/`كلية` ไปโผล่แถวอื่น).
   `_match_boxes()` จึงแยกทาง: คำเดียว → ทุก occurrence; **หลายคำ → ต้องเป็น run ที่
   ติดกันและอยู่บรรทัดเดียวกัน** (`_phrase_matches` + `_same_line`) แล้ว union เป็นกรอบเดียว.
   วลีเปิด fuzzy ได้ (budget 15% ของความยาว) เพราะถูกล็อกด้วย adjacency แล้ว — จำเป็นจริง:
   Tesseract อ่าน `كربوهيدرات` ตกเป็น `كربوهيدات` (หาย 1 ตัว) บ่อย.
3. **ตั้ง `TESS_LANG` เป็นภาษาที่ไม่ได้ติดตั้ง = Tesseract error ทั้ง call → กรอบหายหมดแม้แต่อังกฤษ**.
   `_resolve_langs()` จึงกรองเหลือเฉพาะภาษาที่ `get_languages()` ยืนยัน (fallback → `eng`).
4. **bbox ของ Gemini มีหลาย convention** (0..1 / 0..1000 / pixel) แยกไม่ออกถ้าไม่รู้ขนาดภาพ →
   `ocr.read_zone()` เก็บ **`ocr_wh`** (ขนาด crop ที่ OCR เห็นจริง) ให้ `_infer_scale()` ตัดสิน **ต่อโซน**
   จากพิกัดใหญ่สุดของทุก block (block เดียวใกล้มุมตัดสินไม่ได้).
5. **`_otsu()` คืน threshold 0 ได้** บนภาพ bimodal สะอาด → ต้องใช้ `gray <= thr` (ถ้าใช้ `<` ชั้น CV ตาย
   เงียบ คืน None ตลอด).
6. **ขนาด crop เป็นตัวชี้เป็นชี้ตายของ Tesseract** — โซนเล็กเรนเดอร์ที่ OCR_DPI ได้ ~490px แล้ว
   Tesseract อ่านมั่ว (0/8 คำ, อ่าน "NUTRITIONAL INFORMATION" เป็น "ANO/V/OLES") → ตกไปใช้ bbox
   ของ backend ที่คลาดเคลื่อน = กรอบผิดแถว. แก้ด้วย `CROP_MIN_SIDE=1200` (PDF เรนเดอร์ใหม่ DPI สูงขึ้น
   = ได้รายละเอียดจริง) + `_upscale_for_ocr()` (ภาพถ่าย ขยายในหน่วยความจำก่อน OCR แล้วหารพิกัดกลับ).
7. **PSM ของ Tesseract สำคัญมากกับ "ตาราง"** — default (psm 3, auto layout) อ่าน *ชื่อรายการ*
   ได้หมดแต่ **ทิ้งคอลัมน์ตัวเลขทั้งคอลัมน์** (หา `24%`/`170`/`475` ไม่เจอเลย). `_PSM_ORDER=(11,3)`
   ลอง **psm 11 (sparse text)** ก่อน แล้วค่อยถอยไป psm 3 (ดีกว่ากับข้อความยาวต่อเนื่อง เช่นบล็อกอาหรับ).
   วัดจาก 7 โซนจริง: psm3=38/44, psm11=42/44, ลองทั้งคู่=43/44.
8. **การอ่านทั้งภาพไม่เสถียรระดับ ±1 พิกเซล** — crop 1455px อ่าน `24%` เป็น `72`, crop 1456px
   อ่านถูก. แต่ **ครอปเฉพาะเซลล์ (75x38) อ่านถูกทุก psm**. จึงมี `_row_refine()`: เมื่อหาไม่เจอ
   ทั้งภาพ ให้ใช้คำข้างเคียงในบรรทัดเดียวกัน (จากข้อความ OCR ของ backend) หา**แถบแถว** แล้ว
   อ่านซ้ำเฉพาะแถบนั้น. anchor ต้องเจอ **ครั้งเดียว** ในภาพ (ไม่งั้นชี้แถวไม่ได้) และผลต้องผ่าน
   `_verify_boxes(require_positive=True)` → เป็นไปไม่ได้ที่จะไปโผล่คนละแถว.
9. **`_verify_boxes` ต้องใช้ psm 7/8 ไม่ใช่ default** — ครอปขนาดเท่าคำเดียวถ้าอ่านด้วย psm 3
   จะได้ค่าว่าง/ขยะ แล้วไป**ตัดกรอบที่ถูกต้องทิ้ง** (เคสจริง: กรอบ `24` ที่ถูกต้องถูกตัดทุกครั้ง).
10. **การ "พิสูจน์ว่าผิด" ต้องเชื่อถือได้ก่อนถึงจะใช้ตัดสิน** — อ่านซ้ำครอปแคบของอาหรับ/CJK
   ไม่น่าเชื่อถือ (กรอบอาหรับที่ถูกอ่านซ้ำได้ `Yoda كلية`) → กรอบที่ "วัดมา" (Tesseract) ของคำ
   non-ASCII จึง**เก็บไว้เสมอ** ส่วน bbox ของ LLM ต้องพิสูจน์ว่าถูกเท่านั้นถึงวาด (asymmetric).
11. **cache ผล OCR ต่อรูป** (`_WORDS_CACHE` key = hash เนื้อภาพ+lang+psm) — การ์ด defect
   หลายใบในโซนเดียวกันจะไม่ OCR ซ้ำ (วัดจริง เร็วขึ้น ~2.5 เท่า). **เป็น `OrderedDict` +
   `threading.Lock`** — Flask `threaded=True` ทำให้ 2 request ชนกันได้จริง (dict ธรรมดา
   จะ evict มั่ว/`RuntimeError` ตอน iterate); ตัด LRU ที่ `_WORDS_CACHE_MAX=12`.
12. **ตัวเลขอาหรับ-อินดิก (`٤٧٥`) ไม่ใช่ `475`** — Tesseract โหมด `ara` คืนตัวเลขเป็น
   `٠-٩` ส่วน defect ที่ฟ้องมาเป็นเลขอารบิกปกติ → จับคู่ไม่ติด **เงียบ ๆ** (ไม่มีกรอบ ไม่มี error).
   `_norm()` จึงพับ `٠-٩`+`۰-۹` (เปอร์เซีย) เป็น `0-9` ก่อนเทียบทุกครั้ง.
13. **เลือก "แถว" ด้วย substring = ชี้ผิดแถว** — `_row_refine()` เดิมหาบรรทัดด้วย
   `key in line`: คำเป้าหมาย `0` (จาก `0 g`) ไปเจอใน `10%` ของอีกบรรทัดทันที.
   ตอนนี้เทียบ **ทั้ง token** (คำสั้น) และถ้าเจอ **มากกว่า 1 บรรทัด = กำกวม → ไม่วาด**
   (กฎเหล็ก 2: ไม่มั่นใจ ไม่แสดง).
14. **คำที่ขอบโซนตัดผ่าน** — PDF text-layer คืนกรอบ **เต็มคำบนหน้ากระดาษ** แม้คำนั้นโผล่ในโซน
   แค่เสี้ยวเดียว → clamp แล้วได้แถบบางติดขอบโซน = ดูเหมือนวาดผิดที่. `frac_to_px()` จึงทิ้ง
   กรอบที่หลุดออกนอกโซน > `_MAX_CLIP_FRAC` (25%).

**⚠️ ข้อจำกัดเชิงกายภาพ (แก้ด้วยโค้ดไม่ได้ — ต้องบอกผู้ใช้):** วัดจากไฟล์จริง กรอบแดงต้องการ
ตัวอักษรในภาพ crop สูงราว **9-20 px**. โซนที่ลากเป็น **แถบกว้างทั้งแผ่น** (เช่น 1600x339)
ตัวหนังสือเล็กเหลือ ~8px → หาคำไม่เจอเลย (0/14) และ **เร่ง resolution ก็ตันที่ 6/14**
(เพราะแถบกว้างมีทั้งกราฟิก/ภาพถ่าย/หลายภาษาปนกัน). โซนเดียวกันที่ลากกระชับรอบตาราง
(1455x990) ได้ **14/14**. ที่วัดได้ชัดคือ **ด้านสั้นของ crop** (พัง: 339/487 · ผ่าน: 895/906/988/1186)
→ เกณฑ์เตือน `zones.HL_MIN_SHORT_SIDE=700` + `HL_MAX_ASPECT=4.0`.
ข้อค้นพบสวนสามัญสำนึก: **ใหญ่ขึ้นไม่ได้ดีขึ้นเสมอ** (คำสูง 32px ได้ 11/14 แพ้ 9-20px ที่ได้ 14/14)
และ **ย่อภาพ raster ลงคือหายนะ** (1/14). วัดแล้วยัง **ปฏิเสธ** 3 ไอเดียของตัวเอง: ปิด dictionary
(ไม่มีผล), Sauvola (แย่ลง), โหลดหลายภาษา (ความแม่นเท่าเดิม แค่ช้าลง ~3.5 เท่า).

**เตือนผู้ใช้ 2 จุด (advisory ล้วน — ไม่แตะ verdict/การนับ/ข้อความ OCR):**
- **ตอนจัดโซน** (ก่อนส่งตรวจ): `renderHlHint()` ใน `artwork_check.js` คำนวณขนาด crop
  ที่จะได้จาก **เรขาคณิตอย่างเดียว** (ไม่เรนเดอร์ ไม่ OCR = ฟรี) แล้วขึ้นบรรทัดเตือนใน
  แผง properties ทันทีที่เลือกโซน — ผู้ใช้แก้ได้เลยก่อนเสียเวลาตรวจ.
- **ในการ์ด "รายการที่พบ"**: `pipeline._tag_highlight_risk()` ตั้ง `z["hl_risk"]`
  (`"wide"`/`"small"`) ลง `report.json` ตอนตรวจ → JS แสดงเหตุผลว่าทำไมไม่มีกรอบแดง
  + วิธีแก้ (ลากโซนให้กระชับแล้วส่งใหม่).
- **ค่าคงที่ต้องตรงกันสองฝั่ง** (`zones.HL_*` ↔ `HL_*` ใน `artwork_check.js`) — แก้ข้างเดียวแล้ว
  คำเตือนตอนจัดโซนกับตอนดูผลจะไม่ตรงกัน.

**Tesseract (ชั้น ②) — optional dependency:**
- `_find_tesseract_cmd()` **auto-detect ให้** ตามลำดับ: env `ARTWORK_TESSERACT_CMD` → PATH →
  `C:\Program Files\Tesseract-OCR\tesseract.exe` → `%LOCALAPPDATA%\Programs\Tesseract-OCR\`.
  **ไม่ต้องตั้ง PATH เอง**.
- ติดตั้ง: UB-Mannheim installer (ติ๊ก Additional language data: Arabic/Hebrew/Chinese/Thai) +
  `py -3.9 -m pip install pytesseract`. **ไม่ติดตั้ง = ไม่มีกรอบ แต่ระบบทำงานปกติ** (ไม่ error).
- หลายภาษา: `ARTWORK_HIGHLIGHT_TESS_LANG=eng+ara+heb+chi_tra+tha`.
- **ติดตั้งที่ server เท่านั้น** — วาดกรอบฝั่ง server ส่ง JPEG ให้ client (เครื่อง client ไม่ต้องลงอะไร).

**Config (ทุกตัว opt-out ได้ — `artwork_check/config.py`):**
`HIGHLIGHT_DEFECT_WORD` (ปิดทั้งฟีเจอร์) · `HIGHLIGHT_USE_PDF_TEXT` · `HIGHLIGHT_USE_TESSERACT` ·
`HIGHLIGHT_USE_PROFILE` (default False) · `HIGHLIGHT_TESSERACT_LANG` · `HIGHLIGHT_MAX_BOXES` ·
`CROP_MIN_SIDE` (=1200 ด้านยาวขั้นต่ำของ crop ในการ์ด — ตัวชี้เป็นชี้ตายของ Tesseract, ดูกับดักข้อ 6)

**เครื่องมือ diagnose บนสถานี:** `py -3.9 diagnose_highlight.py <inspection-id>` — พิมพ์ config,
path/ภาษาของ tesseract, ชั้นที่ใช้ต่อโซน, จำนวนกรอบต่อ defect และ**อ่านซ้ำทีละกรอบ**ว่าในกรอบ
คือคำอะไร (`--save` เขียนไฟล์ `diag_<id>_<n>_<zone>.jpg` ออกมาดูด้วยตา). ใช้ตอบคำถาม
"ทำไมกรอบไม่ขึ้น/ขึ้นผิดที่" ได้โดยไม่ต้องเดา.

**ผลทดสอบ end-to-end (production path, 5 artwork จริง + 1 ภาพถ่าย):**
- Cosma/GimCat (มี text layer) → ชั้น ① **8/8** เป๊ะ · StarKist/TerraMadre/JohnWest (outline) → ชั้น ②
- รวม **25/25** เมื่อตั้ง `eng+ara` (23/25 ด้วย `eng` ล้วน — อาหรับ MISS = ไม่วาด ไม่ error)
- ภาพถ่ายกล้องจริง (Puffy Nee Nee): **9/9** รวม `Cude`/`Phosphours` ที่เป็นคำผิดจริง
- **ไม่มีกรอบวางผิดแม้แต่จุดเดียวในทุกไฟล์**
- ⚠️ อ่อนกับ **โลโก้/badge สไตล์ไลซ์บนกราฟิก** (SHINYCAT/GLUTEN FREE) — ปกติ ไม่ค่อยถูกฟ้องเป็น defect
- ⚠️ **ทุกครั้งที่ "ดูเหมือนพัง" ตอนทดสอบ = พิกัดโซนผิด ไม่ใช่เมธอด** — verify โซนด้วยการ render ดูก่อนเสมอ

---

## 🖼️ Artwork — เลย์เอาต์หน้าจอ (คอลัมน์เดียว กว้าง 1600px กึ่งกลาง)

หน้า `/artwork_check` เป็น **คอลัมน์เดียว**: ① อัปโหลด/จัดโซน แล้ว ② ผลการตรวจสอบ
อยู่ล่าง (เดิม 2 คอลัมน์ 7/5 ใน `max-width:1380px` → ① ได้แค่ ~710px ทำงานกับ artwork ใหญ่ไม่ไหว).
`.aw-wrap { max-width:1600px; margin:24px auto }` = **กว้างพอทำงาน แต่ยังอยู่กึ่งกลางจอ**
(เคยลองปลดเพดานเป็นเต็มจอ 1848px แล้ว ผู้ใช้บอกว่าชิดขอบเกินไป). บนจอ 1920 กล่องจัดโซน
กว้าง **1522px (×2.1 ของเดิม)** เว้นขอบข้างละ ~200px → A3 พอดีที่ 61% (เดิมต้องย่อเหลือ 29%).

- **ความกว้างของ panel ไม่เข้าไปในสูตรพิกัดโซนเลย** — โซนเป็นสัดส่วน 0..1, ภาพถูกกำหนดความกว้าง
  เป็น px จาก `applyZoom()` และ `.aw-stage img { max-width:none }` ⇒ กล่องกว้างขึ้น = **เห็นภาพ
  มากขึ้นที่ซูมเท่าเดิม ไม่ใช่ย่อภาพ**. ความละเอียดจึงลดลงไม่ได้เชิงโครงสร้าง.
  พิสูจน์แล้วด้วยเบราว์เซอร์จริง (เทียบก่อน/หลังแก้): วาดโซนที่ zoom 100/60/250% ได้ค่าคลาดเคลื่อน
  **0.781/0.797/0.781 px เท่ากันทุกหลัก** และ zoom mapping (60% → 1488px), wheel-zoom เท่าเดิมเป๊ะ.
- **`.main-content { max-width:1400px }` อยู่ใน `static/css/style.css` = ของกลางทุกหน้า** —
  ปลดเพดานได้เฉพาะใน `{% block extra_css %}` ของ `artwork_check.html` (render เฉพาะหน้านี้)
  **ห้ามแก้ style.css** ไม่งั้นกระทบ Live/Label/Dashboard ทั้งหมด.
- **`.results-wide` + `setResultsWide()` กลายเป็น no-op** (คอลัมน์เดียว) — คงคลาสและจุดเรียกทั้ง 3
  ไว้ตามเดิม ไม่ต้องแตะ JS.
- **แถบเครื่องมือ (`#awZoomBar`) อยู่ "นอก" กล่องภาพ** — เดิมอยู่ข้างในและ `position:sticky; top:0`
  ซึ่งตรึงได้เฉพาะแนวตั้ง ⇒ พอเลื่อนภาพไปทางขวา **ปุ่มไฟล์หลัก/ชิ้นงานหลุดออกนอกจอ**.
  ย้ายออกมาแล้วปุ่มอยู่นิ่งเสมอ (พิสูจน์แล้ว: เลื่อนภาพ 500px แถบยังอยู่ที่เดิมทุกพิกเซล).
- **⚠️ ตอนย้ายแถบออก ทำให้ `zoomRange.closest(".aw-stage-box")` คืน `null`** → wheel-zoom /
  ปุ่มพอดีความกว้าง / การลากเลื่อนภาพ **ตายเงียบพร้อมกันทั้งหมด**. ตอนนี้อ้างกล่องด้วย
  `$("awStageBox")` (id) แทน — **ห้ามกลับไปใช้ `closest()` จาก element ในแถบเครื่องมือ**.
- **ลากเมาส์เพื่อเลื่อนภาพ (pan)** — เปลี่ยนแค่ `scrollLeft/scrollTop` ของ `.aw-stage-box`
  ⇒ **scrollbar เดิมทำงานเหมือนเดิมทุกอย่าง** ไม่แตะสูตรพิกัดโซนเลย. 2 ท่า:
  - **ปุ่มซ้ายลากบนพื้นที่ว่าง** (ไม่ใช่บนโซน + ไม่ได้กด "เพิ่มโซน") — ท่านี้เดิมไม่ทำอะไรเลย
    จึงเอามาใช้ได้โดยไม่ทับของเดิม. โซนมี `stopPropagation` ใน `startDrag` อยู่แล้ว
    event จึงไม่ไหลมาถึง handler ของ pan ตอนลากย้าย/ย่อขยายโซน.
  - **ปุ่มกลาง (ล้อ) ลาก** — ใช้ได้เสมอแม้อยู่บนโซนหรือกำลังวาดโซน. ต้องมี
    `if (ev.button !== 0) return;` ทั้งใน `startDrag` และ handler `mousedown` ของ `stage`
    ไม่งั้นปุ่มกลางจะไป **ย้ายโซน/วาดโซน** แทนที่จะเลื่อนภาพ.
  - `ev.preventDefault()` ใน `panStart` **จำเป็นทั้งสองปุ่ม**: กัน native image drag ของ
    เบราว์เซอร์ (ปุ่มซ้าย) และกัน autoscroll วงกลมของ Windows (ปุ่มกลาง).
  - `canPan()` เช็ค **ทั้งแนวนอนและแนวตั้ง** — กด "พอดีความกว้าง" แล้วภาพยังสูงเกินกล่อง
    (A3 ที่ 72% = 1786x1263 ในกล่องสูง 840) ⇒ ยังต้องลากเลื่อนแนวตั้งได้.
  - `updatePannable()` (เรียกจาก `applyZoom()` + `ResizeObserver` + `window.resize`) คุม
    คลาส `.aw-pannable` = เคอร์เซอร์มือ. ขึ้นมือทั้งที่เลื่อนไม่ได้ = ผู้ใช้ลากแล้วงง.
- ปุ่ม **"⤢ พอดีความกว้าง"** (`#awZoomFit`): `floor(stageBox.clientWidth-6 / natW * 100)` clamp 30-300
  — **ปัดลงเสมอ** (ปัดขึ้น 1% = ภาพล้นกล่อง มี scrollbar แนวนอนทั้งที่กด "พอดี") และต้อง sync
  `zoomPct` + `zoomRange.value` + ป้าย % พร้อมกัน ไม่งั้นสไลเดอร์ค้างคนละค่ากับภาพ.
- ② อยู่ล่างแล้ว → `scrollToResults()` พาจอไปที่ผลตรวจหลังกด "ส่งตรวจสอบ" (ทั้งกรณีสำเร็จและ error)
  ไม่งั้นผู้ใช้ที่อยู่ตรงกล่องจัดโซนจะเหมือนกดแล้วไม่มีอะไรเกิดขึ้น.
- **ปุ่ม "⛶ พอดีทั้งหน้า"** (`#awZoomFitPage`) = `min(กว้าง, สูง)` ต่างจาก "พอดีความกว้าง"
  ที่ยังเหลือ scroll แนวตั้ง. ⚠️ **ต้องลดพื้นล่างของซูมจาก 30% เป็น 10% ด้วย**
  (`ZOOM_MIN` ใน JS ↔ `min=` ของ `#awZoomRange` ใน template — **สองฝั่งต้องตรงกัน**):
  A3 ในกล่องมาตรฐานต้องย่อเหลือ ~26% ถ้ายัง clamp ที่ 30% ปุ่มจะ**โกหก** (ภาพยังล้นกล่อง
  ทั้งที่ปุ่มบอกว่าพอดี) และสไลเดอร์จะค้างคนละค่ากับภาพ. วัดจริงบนเบราว์เซอร์:
  พอดีความกว้าง = 32% (img 1432x860 ในกล่อง 1480x718 → ล้นแนวตั้ง) ·
  พอดีทั้งหน้า = 26% (img 1164x699 ในกล่อง 1480x707 → ไม่ล้น).
- **โหมดวาดโซนต่อเนื่อง** (`#awDrawContinuous`, default **ปิด** = เดิมเป๊ะ): ติ๊กแล้ว
  `setDrawMode()` จะค้างโหมดไว้หลังวาดแต่ละโซน. **คลิกเปล่า (กรอบ < 8px) ปิดโหมดเสมอ**
  = ทางออกที่ไม่ต้องหา Esc (Esc ยังใช้ได้ผ่าน `cancelDraw()`).
- **autosave โซนลง `localStorage`** (`aw.session.v1`, อายุ 7 วัน) — เดิมรีเฟรชหน้า = โซนหายหมด.
  เขียนจาก `renderZones()` (จุดเดียวที่ครอบคลุมทุกการเปลี่ยนโซน) แบบ debounce 400ms.
  ⚠️ **ไม่กู้คืนเงียบ ๆ เด็ดขาด** — ขึ้นแถบ `#awRestore` ให้กดยืนยันก่อน เพราะการเอาโซน
  ของงานคนละใบมาวางทับโดยผู้ใช้ไม่รู้ตัว = ส่งตรวจด้วยโซนผิดใบ (อันตรายกว่าลากใหม่มาก).
  ก่อนเสนอกู้คืนต้อง **โหลด `preview.png` ของ id นั้นให้ผ่านก่อน** (ไฟล์ถูกลบ / เป็นของ
  คนอื่นตามด่านสิทธิ์ → ไม่เสนอ).
- **ยังไม่ได้ทำ (ถ้าอยากให้ภาพ "คมขึ้น" จริงระดับพิกเซล):** `PREVIEW_DPI=150` คือเพดานของภาพใน
  ตัวแก้โซน ซูมเกิน 100% = ขยายภาพเบลอ. ต้องเพิ่มไฟล์ **display-only** แยก (เช่น `PREVIEW_DISPLAY_DPI`
  → `preview_hi.png`) **ห้ามเปลี่ยน `preview.png`** เพราะถูกใช้ต่อโดย `propose_zones` / `snap_bbox` /
  `autopair_bbox` และ `draw_overlay` (ซึ่งใช้ `putText` fontScale คงที่ 0.5 → DPI สูงขึ้น = ป้ายบน
  overlay เล็กลงเชิงสัดส่วน).

---

## 🔐 Artwork — ประวัติการตรวจ "เห็นเฉพาะของตัวเอง"

หน้า `/artwork_check/history` แสดงเฉพาะการตรวจที่ผู้ใช้คนนั้นเป็นคนอัปโหลด
(role ใน `HISTORY_ADMIN_ROLES` เห็นทั้งหมด). **ขอบเขต: โหมด Artwork เท่านั้น** —
Label Paper / Live / Dashboard / `/api/defects` ไม่ถูกแตะ และ **ไม่ต้องแก้ SQL schema เลย**.

- **เจ้าของเก็บใน `owner.json` แยกจาก `report.json`** (`{user_id, username, saved_at}`) เพราะ
  `report.json` เกิดตอนกด "ส่งตรวจสอบ" เท่านั้น แต่ระหว่างจัดโซนมี endpoint ที่ต้องเช็คสิทธิ์แล้ว
  (preview/crop/propose/snap/autopair) — ถ้ารอ report.json ช่วงนั้นจะไม่มีเจ้าของให้เทียบ.
  เขียนตอน `pipeline.start_inspection(owner=...)`; `routes.py` เป็นคนหา user จาก `g.current_user`
  → **`pipeline.py` ไม่ import Flask** (ยังเทสต์ได้ตรง ๆ).
- **ด่านเดียวคุมทุก endpoint: `@artwork_bp.before_request`** อ่าน `rec_id` จาก `request.view_args`
  ⇒ ครอบคลุม **13 route** ที่มี `<rec_id>` ทั้งหมด **รวมถึง route ที่จะเพิ่มในอนาคต**.
  ⚠️ **การกรองเฉพาะรายการ (`/api/artwork/history`) ไม่ใช่การป้องกัน** — ถ้าไม่มีด่านนี้ ใครที่รู้ id
  ก็เปิด `/api/artwork/<id>/report` ของคนอื่นได้ตรง ๆ. เทสต์ `test_http_other_user_blocked_on_every_rec_route`
  **ไล่จาก `url_map` จริง** ไม่ใช่ลิสต์ที่เขียนมือ → เพิ่ม route ใหม่แล้วลืมกัน = เทสต์แดงทันที.
- **นโยบายอยู่ที่เดียวใน `artwork_check/ownership.py`** (ไม่มี Flask): ปิด flag → ผ่านหมด ·
  ไม่มีระบบล็อกอิน (`viewer is None`) → ผ่านหมด · admin → ผ่านหมด · **บันทึกเก่าที่ไม่มี `owner.json`
  → admin เท่านั้น** · เจ้าของ → ผ่าน.
  - `viewer is None` = auth ปิด ≠ `viewer == {}` = auth เปิดแต่หาผู้ใช้ไม่เจอ (**ไม่มีสิทธิ์อะไรเลย**) —
    ต้องเช็คด้วย `is None` ห้ามใช้ความ falsy ไม่งั้นสองเคสนี้จะรวมกันเป็น "ผ่านหมด".
  - เทียบ id ต้อง `bool(oid) and bool(vid) and oid == vid` — ไม่งั้น `"" == ""` ทำให้ทุกคนเป็นเจ้าของ
    ของบันทึกที่ `user_id` ว่าง.
- **`AUTH_ENABLED=False` ต้องไม่กรอง** ไม่งั้นหน้าประวัติว่างเปล่าทั้งที่ระบบทำงานปกติ.
- **ผลข้างเคียงที่ตั้งใจ:** role `Manager`/`Viewer` (ซึ่งมี `view_history`) จะเห็นเฉพาะงานของตัวเอง
  ด้วย — `Viewer` ที่ไม่เคยอัปโหลดจะเห็นตารางว่าง. ถ้าต้องการให้เห็นทั้งหมด เพิ่มชื่อ role ใน
  `ARTWORK_HISTORY_ADMIN_ROLES` (env, คั่นด้วย comma) ไม่ต้องแก้โค้ด.
- **ผูกกับ "ชื่อ" role ตามที่ผู้ใช้เลือก** ⇒ ถ้ามีคนเปลี่ยนชื่อ role `Admin` ในหน้าจัดการผู้ใช้
  **สิทธิ์เห็นทั้งหมดจะหยุดทำงานเงียบ ๆ** ต้องมาแก้ค่าคอนฟิกให้ตรงกัน.
- **ปุ่มลบใช้ด่านเดียวกัน** (DELETE มี `rec_id`) ⇒ เจ้าของ + admin เท่านั้น. JS ไม่ต้องซ่อนปุ่ม
  เพราะรายการที่แสดง = รายการที่ลบได้อยู่แล้ว (server กรองมาให้).
- **ชื่อผู้ตรวจแสดง 2 ที่**: คอลัมน์ "ผู้ตรวจ" ในตารางประวัติ · บรรทัด `👤 <ชื่อ>` ในหัวรายงาน
  ตอนเปิดดูรายละเอียด (และหลังกด "ส่งตรวจสอบ" บนหน้าตรวจ). `routes._with_owner()` แนบ
  ชื่อ **ตอนตอบเท่านั้น** ไม่เขียนลง `report.json` ⇒ เจ้าของมีแหล่งความจริงเดียวคือ
  `owner.json` (ไฟล์เดียวกับที่ด่านสิทธิ์ใช้) ไม่มีทางไม่ตรงกัน. บันทึกเก่า = ค่าว่าง →
  UI ไม่แสดงบรรทัดนั้น (ดีกว่าโชว์ "ไม่ทราบ").
- `list_inspections(limit, can_view=None)` — `can_view=None` = เส้นทางเดิมเป๊ะ. ตอนกรองมีเพดาน
  `_MAX_SCAN=2000` กันผู้ใช้ใหม่ที่ยังไม่มีบันทึกต้องไล่อ่านทั้งคลังทุกครั้ง. เพิ่ม field `owner`
  (ชื่อผู้ตรวจ) ในผลลัพธ์ = คอลัมน์ใหม่ในตาราง (JS `COLS=7` ต้องตรงกับ `<th>` ใน template).
- **Config:** `ARTWORK_HISTORY_PER_USER` (default `true`; ตั้ง `false` + รีสตาร์ต = กลับพฤติกรรมเดิม
  100% ทันที) · `ARTWORK_HISTORY_ADMIN_ROLES` (default `Admin`).
- **ตอน deploy ครั้งแรก:** บันทึกเก่าทั้งหมดจะหายจากสายตาผู้ใช้ทั่วไปทันที (เห็นได้เฉพาะ admin)
  และงานที่ค้างอยู่ระหว่างจัดโซนตอนรีสตาร์ตจะกลายเป็น "ไม่มีเจ้าของ" → เจ้าตัวเปิดต่อไม่ได้
  ต้องอัปโหลดใหม่ ⇒ **ควร deploy ตอนไม่มีคนใช้งาน**.

---

## 🔑 Login — ปุ่ม "ลงทะเบียน" (self-service registration)

หน้า `/login` มีปุ่ม **ลงทะเบียน** เปิด modal (ฟอร์มเดียวกับ "เพิ่มผู้ใช้ใหม่" ของแอดมิน
แต่ตัด **ชื่อผู้ใช้** + **บทบาท (role)** ออก). โค้ด: `auth/registration.py` (กติกาล้วน ไม่มี Flask)
+ `POST /api/auth/register` ใน `auth/routes.py` + modal ใน `templates/login.html`/`static/js/login.js`.

- **username = email (lowercase เสมอ)** — `normalize_email()` พับเป็นตัวเล็กก่อนเทียบ/บันทึก
  ไม่งั้น `A@x.com` กับ `a@x.com` กลายเป็น 2 บัญชีบน collation ที่ case-sensitive.
- **role fix ฝั่งเซิร์ฟเวอร์** (`AUTH_REGISTER_ROLE`, default `Viewer`) — body ที่ส่ง `role` มา
  **ถูกละทิ้ง** (เทสต์ `test_client_cannot_choose_role` กันไว้). endpoint นี้ยกระดับสิทธิ์ไม่ได้.
- **โดเมนอีเมลเทียบแบบตรงทั้งโดเมน** (`@thaiunion.com` เท่านั้น) → subdomain
  (`mail.thaiunion.com`) และโดเมนหลอก (`evil-thaiunion.com`) ต้องไม่ผ่าน — มีเทสต์ทั้งคู่.
- **อีเมลยาว > 64 ตัว = ปฏิเสธ** เพราะ `AuthUsers.Username` เป็น `NVARCHAR(64)`
  (username = email) ไม่งั้นไปพังที่ SQL Server. ค่าคงที่อยู่ที่ `ac.USERNAME_MAX_LEN`
  **ต้องตรงกับ `Connection_sql/auth_schema.sql`** และกับเลข 64 ใน `login.js`.
- **`/api/auth/register` ต้องอยู่ใน `_PUBLIC_PATHS` ของ `access.py`** ไม่งั้นคนที่ยังไม่ล็อกอิน
  โดน 401 = ปุ่มลงทะเบียนใช้ไม่ได้เลย (เทสต์ `test_register_endpoint_is_public` กันไว้).
- **สิทธิ์ที่เห็นหลังล็อกอิน = สิทธิ์ของ role นั้นล้วน ๆ** — ถ้าอยากให้บัญชีที่สมัครเองเห็นแค่
  *ตรวจ Artwork* ให้ไปติ๊กสิทธิ์ของ role `Viewer` ที่หน้า `/admin/users` (การ์ด "บทบาทและสิทธิ์
  การใช้งาน") **ไม่ต้องแก้โค้ด** — และการแก้ตรงนั้นกระทบบัญชี Viewer เดิมทุกคนด้วย.
- กันสแปม: throttle ต่อ IP ในหน่วยความจำ (`AUTH_REGISTER_MAX_PER_IP_HOUR`, `0` = ปิด)
  **นับเฉพาะคำขอที่อีเมลฟอร์แมตถูก** — พิมพ์อีเมลผิดจะได้ไม่กินโควตาของคนที่สมัครจริง.
- **kill switch:** `AUTH_REGISTER_ENABLED=0` = ซ่อนปุ่ม **และ** API ตอบ 403 (ซ่อน UI อย่างเดียว
  ไม่ใช่การป้องกัน — เทสต์ยิงตรงเข้า endpoint ตอนปิด flag).
- เทสต์: `tests/test_auth_registration.py` 39 ตัว (mock `store`+bcrypt → ไม่ต้องมี SQL Server).
- **บั๊กเก่าที่แก้ไปพร้อมกัน:** `.pw-strength{display:flex}` / `.pw-rules{display:grid}` ใน
  `auth.css` **ชนะ attribute `hidden`** → แถบวัดความแข็งแรงรหัสผ่านโผล่เป็นแถบเทาว่างบนหน้า
  login ตั้งแต่ยังไม่พิมพ์อะไร. เพิ่ม `.pw-strength[hidden], .pw-rules[hidden]{display:none}`.

---

## 🔌 N8N Artwork OCR — เคส "ไม่มีการยิง HTTP ออกไปเลย"

**คนละอาการกับ "ยิงแล้วพัง"** — ที่เคยแก้ไปก่อนหน้านี้คืออาการ *ยิงแล้วไปไม่ถึง*
(`a34c4c2` ตั้ง default เป็น `localhost` ผิด → แก้กลับเป็น IP สถานี, ต่อมา `578d751`
ย้ายมา `127.0.0.1` เพราะ N8N มารันบนสถานีเอง). ถ้า **ไม่มี log `[N8N→OCR] POST` เลย**
แปลว่าโค้ดตัดสินใจไม่ยิงตั้งแต่ต้น ซึ่งมี **5 ทาง** (ส่วนใหญ่ถูกต้องตามออกแบบ ไม่ใช่บั๊ก)
บวก **1 ทางที่กลับด้าน คือยิงทั้งที่มี text layer**:

| # | เงื่อนไข | ที่เกิด | เป็นบั๊กไหม |
|---|---|---|---|
| 1 | โซน `type == "ignore"` | `ocr.read_all_zones()` ข้ามก่อนถึง `read_zone` | ไม่ (ตั้งใจ) |
| 2 | **PDF text layer ≥ `EMBEDDED_TEXT_MIN_CHARS` (12)** → `engine="pdf-text"` | `ocr.read_zone()` บรรทัดแรก | **ไม่ — และพบบ่อยสุด** (แม่นกว่า OCR + ฟรี) |
| 3 | `vertex_client.is_enabled()` เท็จ → `engine="none"` | `OCR_BACKEND` ถูกตั้งเป็นค่าอื่น หรือ URL ว่าง | ใช่ (ตั้งค่าผิด) |
| 4 | `crop.size == 0` (bbox ตัดออกนอกหน้า) | `ocr.read_zone()` | ใช่ (โซนผิด) |
| 5 | **cache `ocr_only.json` ยัง valid** | `pipeline.run_ocr_only()` (แท็บ "ข้อความ + คำแปล") | ไม่ (ตั้งใจ) |
| **6** | **มี text layer พอ แต่เป็นคำผิดรูป → ปฏิเสธแล้ว "ยิง OCR แทน"** | `ocr.read_zone()` (`PDFTEXT_GARBLED_CHECK`) | **ไม่ (ตั้งใจ) — กลับด้านกับข้อ 2** |

- **ทาง ② คือคำตอบของ "ทำไมบางไฟล์ยิง บางไฟล์ไม่ยิง"** — artwork ที่ยัง**ไม่ได้ outline**
  ตัวหนังสือ (มี text layer) จะไม่แตะ N8N เลยทั้งไฟล์ ส่วนไฟล์ outline/ภาพถ่ายจะยิงทุกโซน.
  **ไฟล์เดียวกันยังผสมกันได้** (บางโซนมี text layer บางโซนเป็นกราฟิก) → ดูรายโซนเท่านั้น.
- **ทาง ⑤ ทำให้ "กดแล้วเงียบ"** — key = hash ของ (id/type/group/bbox/doc/rotate ของทุกโซน +
  auto_rotate + **ค่าตั้ง OCR ที่เปลี่ยนผลการอ่าน** ดู `pipeline._ocr_fingerprint()`).
  ขยับโซนแม้นิดเดียว = cache หลุด. ลบ `data/artwork_check/inspections/<id>/ocr_only.json`
  = บังคับ OCR ใหม่. **`run_inspection()` (ปุ่ม "ส่งตรวจสอบ") ไม่ใช้ cache นี้** — ยิงใหม่เสมอ.
  > ⚠️ ก่อนหน้านี้ key มีแค่ layout โซน ⇒ แก้ค่า OCR แล้ว cache ไม่หลุด แท็บแปลเสิร์ฟ
  > ข้อความเก่าตลอดไปแบบเงียบ. **ถ้าเพิ่มค่าตั้งที่กระทบการอ่าน ต้องใส่ใน `_ocr_fingerprint()` ด้วย**
  > (มีเทสต์ `test_artwork_ocr_cache.py` ไล่รายชื่อคีย์กันลืม).
- **`_resolve_backend()` auto-เลือก `n8n` เมื่อ `OCR_BACKEND` ว่างและมี URL** ⇒ การตั้ง env
  `OCR_BACKEND=stub`/`vertex` ทับ จะปิดการยิงทั้งระบบแบบเงียบ ๆ (ไม่ error, ได้ข้อความ stub).

**เครื่องมือ:** `py -3.9 diagnose_n8n_ocr.py [<inspection-id>] [--no-ping|--ping-only|--scan]`
— อ่านอย่างเดียว ไม่แตะ report/cache/verdict. ทำ 4 อย่าง:
① พิมพ์ config ที่ตัดสินใจจริง (backend ที่ resolve ได้, URL ทั้ง OCR+translate, เตือนเมื่อ
host สองตัวไม่ตรงกัน / ใช้ `localhost` / ใช้ **Test URL** `/webhook-test/` ที่ตายนอกโหมด
listen) ② ยิงภาพ JPEG เล็ก ๆ ผ่าน `ocr_n8n.ocr_image()` = เส้นทางเดียวกับของจริง แล้วแปล
error ให้ (connection refused = N8N ไม่ได้รัน · 404 = workflow ไม่ได้ Activate/path ผิด ·
timeout = Gemini ช้า/โควตา · 413 = payload เกิน) ③ ไล่ทีละโซนของการตรวจจริง **โดยไม่เรียก OCR**
บอกว่าโซนไหน "จะยิง" โซนไหนไม่ยิงเพราะข้อไหนใน 5 ทาง พร้อมเทียบกับ `engine` ที่บันทึกไว้ใน
`report.json` และเช็คสถานะ cache ทาง ⑤ ④ **`--scan` กวาดทุกการตรวจ** หาไฟล์ที่
"น่าจะได้ text แต่ยังยิง OCR" — เคสที่ต้องจับคือโซนที่มีข้อความ **1..11 ตัว**
(ต่ำกว่า `EMBEDDED_TEXT_MIN_CHARS`) = ทิ้งข้อความจริงไปเดาด้วย OCR ทั้งที่โซนแค่ลากคาบเกี่ยว;
โซนที่ได้ 0 ตัว = outline/กราฟิกจริง ยิง OCR ถูกแล้ว.

**⚠️ การตัดสินเป็น "รายโซน" ไม่ใช่ "รายไฟล์"** — `embedded_text(bbox)` อ่านเฉพาะในกรอบโซน
⇒ ไฟล์ที่มี text layer เต็มหน้ายังมีโซนที่ยิง OCR ได้ (โซนวางบนโลโก้/ภาพถ่ายที่ไม่มีตัวอักษร
เป็น text) และไฟล์ outline ทั้งไฟล์จะยิงทุกโซน.

**ลำดับการไล่ที่เร็วที่สุด:** ดู log บนคอนโซลก่อน — `[artwork] zone z1 engine=... chars=...`
บอกทาง ①②③④ ครบอยู่แล้วต่อโซน ส่วน `[N8N→OCR] POST ...` คือหลักฐานว่ายิงจริง.
ถ้า log หายไปทั้งคู่ = request ไม่เคยถึง pipeline (ดูฝั่ง route/สิทธิ์แทน).

### ⚠️ "ยิงแล้ว ตอบกลับแล้ว แต่ได้ขยะ" — ที่มาของ "ตัวอักษรแปลก ๆ ที่ของจริงไม่มี"

คนละอาการกับสองข้อบน (ไม่ยิง / ยิงแล้วพัง). ตรงนี้ **HTTP 200 ไม่มี error** แต่สิ่งที่
เอาไปเทียบใน MISMATCH/SPELL เป็นขยะ. พิสูจน์ด้วย mock server ที่ตอบแบบที่ N8N ตอบจริง:

| คำตอบจาก N8N | เดิมได้อะไรเป็น "ข้อความบนฉลาก" | ตอนนี้ |
|---|---|---|
| ` ```json{"text":"..."} ``` ` (Gemini ครอบรั้ว) | **ทั้งก้อนรวมรั้ว** → คำว่า `json`/`text`/`blocks`/`{`/`}` | ✅ ถอดรั้วแล้วได้ข้อความจริง |
| `<!DOCTYPE html>...` (workflow ไม่ Activate) | `DOCTYPE html title Error Workflow could not be started` | ✅ **ปฏิเสธ → UNREADABLE** |
| plain text ล้วน | ใช้เป็นข้อความ (เงียบ) | ใช้ได้เหมือนเดิม **แต่ติดธง `note` ให้ผู้ตรวจเห็น** |

- **`_strip_fence()` คือตัวที่แก้อาการที่ผู้ใช้บ่นตรง ๆ** — LLM ครอบ ` ```json ` เป็นปกติ
  วิสัย. ถอดรั้วทั้ง 3 จุด: body ทั้งก้อน · ค่าใน `data/result/output/...` · ค่าใน `text` เอง.
  แกะได้ **12 รูปแบบ** (`tests/test_n8n_ocr_response.py` 35 ตัวคุมไว้).
- **`_looks_like_html()` ตั้งเกณฑ์แคบมากโดยตั้งใจ** (content-type มี `html` หรือขึ้นต้นด้วย
  `<!doctype`/`<html`) — การตัดสินผิดว่า "ไม่ใช่ข้อความ" ทำให้โซนที่อ่านได้จริงกลายเป็น
  UNREADABLE ฟรี ๆ. เทสต์มีเคส `INGREDIENTS <500 mg` (มี `<` กลางข้อความ) ต้องไม่โดนจับ.
- **`warning` เคยถูกตั้งแล้วทิ้ง** — `ocr_image()` ตั้ง `warning` มาตั้งแต่แรกแต่**ไม่มีใครอ่าน**
  ⇒ `read_zone()` ตอนนี้แปลงเป็น `note` ของโซน และ `renderReport()` กาง `<details>`
  "ข้อความ OCR ต่อโซน" ให้อัตโนมัติเมื่อมีโซนที่มี `note` (ไม่งั้นซ่อนอยู่ ไม่มีใครเห็น).
- **retry เฉพาะความล้มเหลวชั่วคราว** (`N8N_OCR_RETRIES=1`): ต่อไม่ติด / timeout / 5xx.
  **ไม่ลองซ้ำกับ 404** (workflow ไม่ Activate) **หรือ 413** (payload ใหญ่) เพราะยิงกี่ครั้ง
  ก็ผลเดิม = ทำให้ผู้ตรวจรอฟรี. ตั้ง `0` = ปิด = พฤติกรรมเดิมเป๊ะ.
- **`--ping-only` ตอนนี้ตรวจ "เนื้อหา" ด้วย ไม่ใช่แค่ "ต่อติดไหม"** — ภาพทดสอบมีคำว่า
  `DIAGNOSE 12345`; ถ้าผลที่ได้ไม่มีคำนี้ = prompt สั่งให้ทำอย่างอื่น (แปล/สรุป) ซึ่งเป็น
  ความผิดพลาดที่เดิมมองไม่เห็นเลยเพราะ HTTP 200.

**⚠️ "โซนเดียวพัง = การตรวจทั้งใบล่ม" — เจอตอนทดสอบเชิงโจมตี (17 ส.ค. 2026):**
`read_zone()` เรียก `vertex_client.ocr_image()` **โดยไม่มี try/except ครอบ** ⇒ ถ้า
backend โยน exception ผู้ตรวจได้ HTTP 500 แทนรายงาน — **ไม่ได้อะไรเลยแม้แต่โซนที่อ่านสำเร็จ**.
ไล่ยิงคำตอบเพี้ยน 21 แบบผ่าน `ocr_image()` พบ 2 ทางที่หลุดจริง:
- **`"conf": "high"`** (LLM คืน conf เป็นคำ) → `float()` เปลือยโยน `ValueError`
- **`requests.post` โยนสิ่งที่ไม่ใช่ `RequestException`** (URL ผิดรูป → `ValueError`)

แก้ครบแล้ว: `_normalize_blocks` แปลง conf แบบกันพัง · `ocr_image` ดัก `Exception` กว้าง
(แต่ **ลองซ้ำเฉพาะ `RequestException`** — `ValueError` ยิงกี่ครั้งก็ผลเดิม) · `read_zone`
ครอบ try/except แล้วแปลงเป็น **UNREADABLE เฉพาะโซนนั้น** · `text_looks_garbled(None)`
ไม่โยนแล้ว · `_strip_fence` ถอดรั้วซ้อนได้ 3 ชั้น.
เทสต์ล็อกไว้ใน `test_n8n_ocr_response.py` + `test_artwork_ocr_quality.py`.

**⛔ ชั้นที่โค้ดฝั่งเราแก้ไม่ได้: LLM เดาคำที่ไม่มีในภาพ (hallucination).** ถ้า Gemini คืนคำ
ที่ดูสมเหตุสมผลแต่ไม่มีบนฉลาก ไม่มีทางแยกออกจากคำจริง — ต้องแก้ที่ **prompt ใน N8N**
เท่านั้น. prompt ที่แนะนำ + เหตุผลรายบรรทัด + วิธียืนยันอยู่ใน **`docs/N8N_OCR_PROMPT.md`**
(หัวใจคือ *ห้ามแก้คำผิดให้* — prompt OCR ทั่วไปสั่งให้ "อ่านให้ถูก" ซึ่งทำลายงาน QC โดยตรง
เพราะงานนี้คือ *หาคำผิด*; และ **`temperature=0`** ไม่งั้นวัดซ้ำไม่ได้).

---

## 🔎 Artwork — คุณภาพการอ่านข้อความ (OCR) และ `verify_ocr.py`

สองอาการที่ทำให้ **ผลตรวจผิดแบบเงียบ** (ไม่ error, การ์ดขึ้นปกติ, แต่ข้อความที่เอาไปเทียบเป็นขยะ)
วัดจากไฟล์จริง 11 ไฟล์ในโฟลเดอร์ `D:\Digital 2026\Vision-Defect\TEST` แล้วแก้ทั้งคู่.

### ① โซนถูกเรนเดอร์เล็กเกินไป → OCR อ่านไม่ออก (`OCR_CROP_MIN_SIDE`)

`zone_crop_jpg()` (ภาพในการ์ด) มี `CROP_MIN_SIDE=1200` มานานแล้ว แต่ **`ocr.read_zone()`
ซึ่งเป็นตัวที่อ่านข้อความจริง กลับไม่มีขั้นต่ำเลย** — เรนเดอร์ที่ `OCR_DPI=450` ตรง ๆ.
artwork ที่ถูกย่อลงหน้า A4 (เช่น `A4-TUG5311`) ตัวหนังสือจึงเหลือ ~9px แล้ว **recall ตกเหลือ 1.2%**.

**เส้นโค้ง "ความสูงบรรทัดเป็นพิกเซล → recall" (วัดจริง ไม่ใช่ประมาณ):**

| ความสูงบรรทัดใน crop | 9.0 px | 12.4 px | 17.2 px | 22.1 px | 31.0 px |
|---|---|---|---|---|---|
| recall | **1.2%** | 23.2% | 93.9% | 98.5% | 100% |

หน้าผาอยู่ระหว่าง 12–17px → `verify_ocr.MIN_LINE_PX = 15.0` เป็นเกณฑ์เตือน.
แก้ด้วย `_render_for_ocr()`: ถ้าเป็น **PDF** และด้านยาวของ crop < `OCR_CROP_MIN_SIDE` (1200)
ให้ **เรนเดอร์ใหม่ที่ DPI สูงขึ้น** (เพดาน `OCR_DPI_MAX_FACTOR=4.0` เท่า) — ยืนยันบนสถานีแล้ว
`--dpi 1600` ได้ **97.6%** จากไฟล์เดียวกันที่เคยได้ 1.2%.
- ⚠️ **ภาพ raster (ภาพถ่าย/PNG) ห้ามขยาย** — `if not doc.is_pdf: return crop`. การ upscale
  พิกเซลที่ไม่มีข้อมูลเพิ่มไม่ช่วยอะไร (ซ้ำรอยกับดัก "ย่อภาพ raster ลงคือหายนะ" ในหัวข้อกรอบแดง).
  ฝั่งกรอบแดงมี `_upscale_for_ocr()` แยกของตัวเองอยู่แล้ว — คนละชั้น อย่าสับสน.
- ตั้ง `ARTWORK_OCR_CROP_MIN_SIDE=0` = ปิด กลับพฤติกรรมเดิม 100%.

### ② text layer มีจริงแต่พัง → เชื่อเต็ม 100% (`PDFTEXT_GARBLED_CHECK`)

ทาง ② ในตาราง N8N คืนค่า `engine="pdf-text"`, **`conf=1.0` โดยไม่เคยตรวจสอบเลย** —
ถ้า PDF ฝัง encoding มาเสีย (คำออกมาเป็น `A1b2C3`-style ปนเลขกลางคำ) ระบบจะเอา *ขยะ*
ไปเทียบใน MISMATCH/SPELL ด้วย**ความมั่นใจสูงสุด** = ละเมิดกฎเหล็กข้อ 2 เต็ม ๆ.

`ocr.text_looks_garbled()` นับ token ยาวที่ "มีทั้งเลขและตัวอักษร **และมีเลขอยู่กลางคำ**"
(`_malformed`) — เกิน `PDFTEXT_GARBLED_RATIO` (0.30) ของ token ทั้งหมด = ปฏิเสธ text layer
แล้ว **ตกไปใช้ OCR แทน** (ทาง ⑥). ถ้าไม่มี OCR backend → คืนข้อความพร้อม `error` flag
⇒ กลายเป็น **UNREADABLE** ไม่ใช่ "ผ่าน".
- **`PDFTEXT_GARBLED_MIN_TOKENS = 8` คือหัวใจกันเดา** — ต่ำกว่านี้ไม่ตัดสิน. วัดจริง:
  จับได้ **28/29** เคสเสีย, **false positive 0/35** เคสดี. ถ้าลดเลข token ลง FP จะโผล่ทันที
  เพราะโซนสั้น ๆ อย่างรหัสงาน (`AWN202500022003`) หน้าตาเหมือน token ผิดรูปพอดี.
- ตั้ง `ARTWORK_PDFTEXT_GARBLED_CHECK=0` = ปิด กลับพฤติกรรมเดิม 100%.
- **⛔ ไอเดียที่ทดสอบแล้ว "ปฏิเสธ": ใช้ ToUnicode CMap เป็นตัวชี้วัด** — ฟังดูถูกหลักการ
  (ไม่มี ToUnicode = ถอดตัวอักษรกลับไม่ได้) แต่ไฟล์ Cosma ตัวจริง **0/14 ฟอนต์มี ToUnicode
  ทั้งที่ extract ข้อความออกมาถูกเป๊ะ** ⇒ ใช้ตัดสินไม่ได้เลย. อย่าเสียเวลาทำซ้ำ.

### `verify_ocr.py` — ตาข่ายนิรภัยของ OCR (คู่กับ `verify_onnx.py`/`verify_openvino.py`)

`py -3.9 verify_ocr.py <ไฟล์.pdf|โฟลเดอร์> [--dpi 450] [--engines pdf-text,tesseract,n8n] [--verbose]`
**อ่านอย่างเดียว ไม่เขียนอะไรลง `data/` เลย.** 4 ชั้น:
1. **TRIAGE** — ไฟล์นี้มี text layer *ใช้ได้จริง* ไหม + คิด % ของหน้าที่ถูกปกคลุม
   (แบนเนอร์ "✅ ไฟล์นี้มี text layer" บนหน้าเว็บเป็น **ระดับหน้า** แต่การตัดสินจริงเป็น **ระดับโซน**
   → ยืนยันแล้วบน `AWN202500022003`: มีข้อความ 18 ตัว = แค่รหัสงาน, coverage 0%).
2. **GROUND TRUTH** — recall/precision เทียบ text layer ของ PDF (เกณฑ์ `MIN_RECALL`/
   `MIN_PRECISION` = 0.95).
3. **NO-TEXT PROBE** — ยิงโซนว่าง/กราฟิกล้วน: อะไรที่คืนมา = hallucination
   (`MAX_PHANTOM_CHARS=3`). ผลจริง: **Tesseract ไม่เคยหลอน 0/36**.
4. **SELF-CONSISTENCY** — อ่านโซนเดิมที่ 2 DPI (`DPI_B_FACTOR=0.7`) ต้องตรงกัน ≥ `MIN_SELF_AGREE` (0.80).

- **`--layers truth,probe,consistency`** เลือกรันเฉพาะบางชั้น — จำเป็นกับ `--engines n8n`
  ที่มีโควตา: เดิม `--n8n-limit 5` ถูกใช้หมดตั้งแต่ไฟล์แรก (GT 1 + probe 3 + consistency 1)
  ไฟล์ที่เหลือได้แต่ `ERROR(ครบเพดาน)`. **`--layers probe` = ทุ่มโควตาทั้งหมดไปกับการจับ
  hallucination และกระจายได้ทั่วทุกไฟล์** ซึ่งเป็นคำถามที่มักอยากรู้จริง.
- **⚠️ `render()` ต้องตรงกับ `ocr._render_for_ocr()` เป๊ะ** — เคยพลาดมาแล้ว: หลังเพิ่ม
  `OCR_CROP_MIN_SIDE` ให้ production ตัว verify ยังเรนเดอร์ที่ `--dpi` แบน ๆ ⇒ **ตาข่าย
  นิรภัยวัดเส้นทางเก่า** แล้วรายงานว่าโซนเล็กอ่านไม่ออก (ตัวอักษร 9.1px) ทั้งที่ production
  ขยายเป็น ~32px ไปแล้ว — พร้อมข้อความแนะนำที่ล้าสมัยว่า "ocr.read_zone ไม่มีการเพิ่ม DPI"
  (ซึ่งแก้ไปแล้ว) = ชี้ให้ไปแก้ของที่ไม่พัง. **เครื่องมือวัดที่วัดผิดทางแย่กว่าไม่มีเครื่องมือ**.
  ยืนยันความตรงด้วย md5 เทียบกับ `_render_for_ocr()` โดยตรง. `--no-min-side` = วัดเส้นทางดิบ.

**exit code: `0`=ผ่าน · `1`=ไม่ผ่าน · `2`=รันไม่ได้ · `3`=สรุปไม่ได้ (ไม่มี ground truth)**
- **⚠️ `3` แยกจาก `0` โดยตั้งใจ** — เคยเขียนให้พิมพ์ "ผ่านทุก engine" ทั้งที่ไม่มีเฉลยให้เทียบ
  สักตัว = คำตอบที่ผิดแบบมั่นใจ (กฎเหล็กข้อ 2). เช่นเดียวกับที่เคยพิมพ์ "ผ่าน" ทั้งที่ n8n
  error ทุก call → ตอนนี้มี verdict **แยกรายเครื่องยนต์** + `ok/err/skipped` ต่อ engine.
- ⚠️ **normalizer ต้องเป็นกลางกับทุกภาษา** — เดิมตัด diacritic เฉพาะช่วง Latin-1 ⇒ เช็ก/ฮังการี/
  โปแลนด์ถูกหักคะแนนฟรี รายงาน 94.6% ทั้งที่ของจริง 99.8%. ตอนนี้ใช้ NFKD + ตัด combining mark.

**ค่าตั้ง OCR ทุกตัวอยู่ใน `pipeline._ocr_fingerprint()`** (เข้า cache key ของแท็บแปล) —
เพิ่มค่าใหม่แล้วต้องใส่ที่นี่ด้วย ไม่งั้นแก้ค่าแล้วผลไม่เปลี่ยน (ดูทาง ⑤ ในตาราง N8N).

---

## 🧩 Artwork — "PASS ไม่ได้แปลว่าตรวจครบ" (`coverage`)

**จุดบอด QC ที่ใหญ่ที่สุดของโหมดนี้** — ผู้ใช้ลากหลายโซนบนไฟล์เดียวแล้วกดส่งตรวจ
ได้ ✅ PASS **ทั้งที่ชั้นเทียบข้ามแผงไม่เคยทำงานเลย**.

**กลไก:** `checks.check_group_consistency()` เทียบเฉพาะโซนที่ **`group` ตรงกัน** และ
`_vote_panels` จะรันก็ต่อเมื่อ `len(readable) >= 2` ในกลุ่มนั้น. แต่โซนที่ลากใหม่ได้
`group` **คนละตัว** เสมอ (`nextGroupLetter()` ใน JS ↔ `zones.seq_group()` ฝั่ง Python:
A, B, C, …) ⇒ ทุกกลุ่มมีสมาชิก 1 ตัว ⇒ **`MISMATCH_PANELS`/`MISMATCH_ZOOM` ไม่มีทางฟ้อง**.

วัดจริงด้วย `run_inspection()` (PDF 3 แผง แผงกลางน้ำหนักผิด 185 vs 170):

| การตั้งกลุ่ม | verdict | defect |
|---|---|---|
| A/B/C (ค่าเริ่มต้นที่ผู้ใช้ได้) | **PASS** | **0 — พลาดทั้งที่ของจริงต่างกัน** |
| A/A/A (ตั้งเอง) | FAIL | 1 (`MISMATCH_PANELS`) |

ความต่างที่ **ไม่มีชั้นไหนจับได้เลย** ถ้าไม่มีการเทียบข้ามแผง (วัดจากเคสจำลองตามงานจริง):
น้ำหนักสุทธิ · ประเทศผู้ผลิต · วันหมดอายุ (บาร์โค้ดรอดเพราะ `NUMBER_FAIL` เช็ค check digit).

**⛔ ทางแก้ที่ห้ามทำ: ให้ทุกโซนอยู่กลุ่มเดียวกันโดยอัตโนมัติ** — วัดแล้วเช่นกัน:
โซนคนละเนื้อหา (ส่วนผสม / ที่อยู่ / วันหมดอายุ) ที่ถูกจับรวมกลุ่ม ให้ **defect ปลอม 6 รายการ**
ทันที. การจัดกลุ่มอัตโนมัติแบบ sequential เป็น **การตัดสินใจที่อนุมัติไว้แล้ว 2026-07-20**
(แทน size-cluster heuristic ที่จับคู่ผิดเงียบ ๆ) — มันถูกต้องสำหรับ **การเทียบข้ามไฟล์**
(โซนลำดับเดียวกันของไฟล์ a/b ได้ตัวอักษรตรงกันเอง). ปัญหาอยู่ที่ **ไฟล์เดียว** เท่านั้น.
มีเทสต์ `test_forcing_one_group_creates_false_defects` กันไม่ให้ใครมาแก้ผิดทาง.

**ทางแก้ที่ใช้: บอกความจริงว่าตรวจอะไรไปบ้าง** (advisory 100% — ไม่แตะ defects/verdict/การนับ)

- **`checks.check_coverage(zones, ocr_results)`** → `report.json["coverage"]` — คำนวณ
  **หลัง** ได้ defects แล้ว. คืนต่อชั้น: `ran` + `reason` + `groups`. เหตุผลที่แยกกันชัด
  เพราะ **วิธีแก้คนละอย่าง**: `single_zone` (ไม่ต้องทำอะไร) · `no_shared_group` (ตั้งกลุ่ม) ·
  `group_unreadable` (ลากโซนใหม่) · `spellchecker_missing` (ลง pip).
- **เงื่อนไขต้องสะท้อน `check_group_consistency` เป๊ะ ไม่ใช่ประมาณ** — `header` นับเป็น panel,
  `ignore` ไม่นับ, group ว่างไม่ใช่ "กลุ่มเดียวกัน", zoom ต้องมี panel ที่อ่านออกใน **กลุ่มเดียวกัน**.
  พิสูจน์ด้วย brute force **3,072 ชุด**: ไม่มีเคสที่ coverage บอก "ไม่ได้ทำงาน" แล้วชั้นนั้นฟ้องจริง.
  > ⚠️ ถ้าแก้เงื่อนไขใน `check_group_consistency` **ต้องแก้ `check_coverage` ด้วย** —
  > รายงาน coverage ที่ผิดคือ "คำตอบที่ผิดแบบมั่นใจ" ตรงตัว (กฎเหล็ก 2) แย่กว่าไม่มีเลย.
- **UI 2 จุด** (เหมือนแพตเทิร์นของ `hl_risk`):
  - **แถบใต้ผลสรุป** (`coverageHtml()` ใน `artwork_check.js`) — ต้องอยู่ **ติดใต้ verdict**
    เพราะคนอ่าน PASS แล้วมักเลิกอ่านต่อ. เตือน (พื้นเหลือง) เฉพาะเมื่อชั้นที่ไม่ทำงาน
    "แก้ได้" — `no_zoom_zone`/`single_zone` ถือว่าปกติ ไม่ทำให้แถบเป็นสีเตือน.
  - **`renderGroupHint()`** — เตือน **ก่อน** กดส่งตรวจ ตอนจัดโซน (มี panel ≥2 แต่ไม่มี
    กลุ่มซ้ำเลย). ฝั่ง JS ยังไม่รู้ว่าโซนไหนจะอ่านออก จึงเตือนเฉพาะเคสที่ชัดเจนเท่านั้น.
- **⚠️ CSS ของ `renderReport` มี 2 ที่เสมอ** — `artwork_check.html` และ
  `artwork_check_history.html` (หน้าประวัติเรียก `window.awRenderReport` **ตัวเดียวกัน**
  แต่ CSS อยู่คนละ `{% block extra_css %}`). แก้ข้างเดียว = กล่องนั้นโผล่แบบไม่มีกรอบ
  บนหน้าประวัติ **โดยไม่มี error ให้เห็น**.
  > เกิดจริงมาแล้ว **3 คลาส**: `.aw-cov*` (ตอนเพิ่ม coverage) · `.aw-hl-warn` และ
  > `.aw-img-*` (หลุดมานานก่อนหน้านั้น — **รูป preview บนหน้าประวัติกางเป็น 4475px
  > ไม่มี `max-height` ไม่มีกรอบ ไม่เรียงคู่** จับได้ตอนไล่ตรวจรวบยอด 17 ส.ค.).
  > ตอนนี้มี **`tests/test_artwork_report_css.py` (13 ตัว)** ไล่คลาสทุกตัวที่
  > `renderReport()` พ่นออกมาแล้วยืนยันว่ามีกฎครบทั้งสองหน้า **และกฎตรงกันเป๊ะ**
  > (ยกเว้นที่ตั้งใจให้ต่างใน `ALLOW`) + เช็คว่า guard `if (!$("awFile")) return;`
  > ยังอยู่หลัง `window.aw*` ทุกตัว (ถ้า guard เลื่อนไปก่อน หน้าประวัติจะเรียกไม่ได้).
- **รายงานเก่าที่ไม่มี `coverage`** → `coverageHtml()` คืนสตริงว่าง = ไม่แสดงอะไร (ไม่พัง).
- **ข้อความ verdict เปลี่ยนตาม coverage**: PASS ที่มีชั้นขาด → **"✅ PASS — ไม่พบประเด็น
  ในชั้นที่ตรวจ"** (เดิม "ไม่พบประเด็น" เฉย ๆ = พูดเกินจริง). ทั้งแถบและหัวเรื่องคิดจาก
  `coverageGaps()` **ตัวเดียวกัน** — ห้ามเขียนเงื่อนไขซ้ำสองที่ ไม่งั้นแถบเตือนแต่หัวเรื่อง
  บอกว่าไม่พบประเด็น. **ไม่แตะค่า `verdict`** (สี/การนับ/ประวัติเหมือนเดิม).
- เทสต์: `tests/test_artwork_coverage.py` **80 ตัว** (รวม parametrize brute force).

---

## 🖥️ Entrypoints & HTTPS

- **`app.py`** = entrypoint หลัก (ผู้ใช้รัน `py -3.9 app.py`). `threaded=True`. รองรับ HTTPS.
- **`run_server.py`** = gevent (ทางเลือก, deploy หนัก). **ผู้ใช้ไม่ได้ใช้** (เคย import gevent ผิด
  interpreter). `app.py` เพียงพอแล้วหลัง STREAM เปลี่ยนเป็น request/response.
- **HTTPS** (`USE_HTTPS=True`): จำเป็นสำหรับ STREAM (`getUserMedia`). cert: `python generate_cert.py <ip>`.
  ถ้า port 5000 bind ไม่ได้ (WinError 10013) = port ถูก Windows/Hyper-V สงวน → เปลี่ยน `FLASK_PORT`.

---

## 🔧 กับดัก Windows/Python ที่เจอบ่อย

- **Dual Python**: `pip install` เปล่าอาจลงคนละ interpreter กับ `py -3.9` → ใช้ `py -3.9 -m pip install ...` เสมอ.
- **CONFIG_VERSION footer** = ตัวยืนยันว่ารันโค้ดใหม่จริง. bump ทุกครั้งที่แก้ config ที่ต้องให้ผู้ใช้ verify.
- **`pyspellchecker` = ชั้นตรวจ dictionary/คำขาด (deterministic, เชื่อถือได้)** — จับคำที่ไม่ใช่คำจริง เช่น
  `Sunflow`/`EXPIR`/`Thailan` (คำถูกตัด/สะกดผิด) ในคอลัมน์ "สถานะ". ⚠️ **ถ้า import `spellchecker`
  ไม่ได้ ชั้นนี้ถูกข้ามเงียบๆ** (`_get_spellcheckers()` คืน `[]`) → คำผิดขึ้น ✓ เหมือนไม่มีปัญหา = จุดบอด QC.
  อยู่ใน `requirements.txt` แล้ว. บนสถานีติดตั้งที่ user-site (`%APPDATA%\Python\Python39\site-packages`)
  — **`git pull`/`checkout` ไม่ลบ** (คนละที่กับโฟลเดอร์ repo). เช็ค: `py -3.9 -c "from spellchecker import SpellChecker"`.
  หมายเหตุ: คำขาดที่ "ยังเป็นคำจริง" (เช่น `Sunflower Oil`→`Sunflower`) ไม่มี checker ตัวไหนจับได้ →
  ต้องพึ่งการลากโซนให้ครบ + เทียบ panel. ส่วนคอลัมน์ AI (🤖) เป็น advisory เท่านั้น เชื่อเป็น QC ไม่ได้.
- **`pytesseract` + tesseract binary = ชั้นวาดกรอบแดง (display-only)** — รูปแบบ "หายเงียบ" เดียวกับ
  `pyspellchecker` แต่**ไม่อันตรายเท่า**: ไม่มี = ไม่มีกรอบแดงบนไฟล์ outline/ภาพถ่าย แต่ผลตรวจ QC
  เท่าเดิมทุกอย่าง (ไม่ใช่จุดบอด QC). เช็ค: `py -3.9 -c "import pytesseract; print(pytesseract.get_tesseract_version())"`.
  ต่างจาก pyspellchecker ตรงที่ **ต้องลง binary แยกจาก pip** (ดูหัวข้อ Artwork กรอบแดง).
- **⚠️ Deploy IIS ในอนาคต (ยังไม่ทำ — บันทึกไว้ก่อน):** package ที่ลง user-site ของ dev
  **IIS Application Pool identity เข้าไม่ถึง** → ชั้น dict หายเงียบใน production. วิธีแก้ตอน deploy คือทำ
  **venv ในโฟลเดอร์โปรเจกต์** (`py -3.9 -m venv .venv` + `pip install -r requirements.txt`) แล้วชี้ IIS
  FastCGI/HttpPlatform ไปที่ `.venv\Scripts\python.exe` (package อยู่กับโค้ด ทุก identity เห็นเท่ากัน).
  **ระหว่างพัฒนาบนสถานีไม่ต้องทำ venv** — จะแยกเป็น 2 environment ทำให้สับสน + ดึง accel คนละชุด.
  **⚠️ ก่อนสร้าง venv ต้อง pin accel ก่อน:** `requirements.txt` ปัจจุบัน **ไม่ตรงกับ stack ที่จูนไว้**
  (`onnxruntime==1.19.2` comment ทิ้ง, `openvino` ไม่ pin เป็น 2024.6.0) — venv สดจะได้ accel ต่างจาก
  สถานี = อาจตรวจช้าลงหรือเจอ bug OpenVINO 2025 ตรวจไม่เจอแบบเงียบๆ (ดูหัวข้อ OpenVINO ด้านบน).

---

## 🧰 สภาพแวดล้อม & repo

- HW สถานี: **i7-1165G7** (4C/8T, 15W, AVX-512), 16GB DDR4 (single-channel), Iris Xe, Win10 Pro, Python 3.9.13.
- inference bestX (seg): **iGPU (OpenVINO) ≈ 45-50ms/เฟรม (~20-22 FPS)** = ตัวจริงปัจจุบัน;
  ONNX CPU ≈ 280ms (~2.7-3 FPS) = ชั้น fallback; PyTorch ≈ 315ms = fallback สุดท้าย.
- Repo: `iceamonwat09/digital_vision2026`. Dev branch ปัจจุบัน: `claude/artwork-multi-zone-errors-k8linm`
  (ก่อนหน้า: `claude/login-registration-feature-2ndkn9`, `claude/artwork-ui-layout-1lnwgt`).
  **ห้าม push ไป main**.
- SQL Server: 172.32.0.50/VisionIQ. Defect log ผ่าน `sp_log_defect` (เก็บภาพ base64).
- **N8N รันบนเครื่องสถานีเอง** → default ของ `N8N_OCR_WEBHOOK_URL` (`config.py`) และ
  `N8N_TRANSLATE_WEBHOOK_URL` (`artwork_check/config.py`) ชี้ `http://127.0.0.1:5678/...`.
  **มี 2 ที่ ต้องแก้ให้ตรงกันเสมอ** — แก้ที่เดียวอีกตัวจะยิงไปเครื่องเก่าแบบเงียบ ๆ.
  ใช้ `127.0.0.1` ไม่ใช่ `localhost` เพราะ Windows resolve `localhost` เป็น `::1` (IPv6) ก่อน
  ถ้า N8N ผูกเฉพาะ IPv4 จะต่อไม่ติดโดยไม่มี error ที่อ่านออก. ย้ายเครื่องเมื่อไรตั้ง env ทับได้
  ไม่ต้องแก้โค้ด. ⚠️ **IP `172.32.201.106` ที่เหลือใน `generate_cert.py`/README = IP ของสถานีเอง
  สำหรับใบรับรอง HTTPS ห้ามเปลี่ยนเป็น 127.0.0.1** ไม่งั้นเครื่องอื่นเปิดเว็บไม่ได้.
- Tests: `pytest tests/` — 553 ตัว (artwork/label/barcode/auth — **ไม่ครอบคลุม camera/live loop**).
  เพิ่มล่าสุด: `tests/test_artwork_report_css.py` 13 ตัว (CSS ของ renderReport ต้องครบทั้ง 2 หน้า),
  `tests/test_n8n_ocr_response.py` 35 ตัว (แกะคำตอบ N8N + ปฏิเสธ HTML + retry),
  `tests/test_artwork_coverage.py` 80 ตัว (รายงานว่าชั้นไหนได้ตรวจจริง),
  `tests/test_artwork_ocr_quality.py` 15 ตัว (crop ขั้นต่ำ + text layer ที่เสีย),
  `tests/test_artwork_ocr_cache.py` 13 ตัว (cache ของแท็บแปลต้องหลุดเมื่อค่าตั้ง OCR เปลี่ยน),
  `tests/test_auth_registration.py` 39 ตัว (ปุ่มลงทะเบียนหน้า login),
  `tests/test_artwork_ownership.py` 30 ตัว (สิทธิ์เห็นประวัติ + ชื่อผู้ตรวจ).
  ⚠️ `tests/test_inspection_golden.py` **fail 5 ตัวอยู่แล้ว** (pre-existing, `NameError: FieldResult`
  ในโมดูล Label Paper) — ไม่เกี่ยวกับ artwork. ยืนยันด้วย `git stash` ก่อนโทษการแก้ของตัวเอง.
- CONFIG_VERSION ปัจจุบัน: **`2026.08.16-aw-ux`** (เช็คที่ footer ว่ารันโค้ดใหม่จริง).

---

## ✅ Checklist ก่อน commit

- [ ] flag ใหม่ default = พฤติกรรมเดิม? scope เฉพาะโหมดที่ตั้งใจ?
- [ ] fallback ครบทุกทางที่อาจล้มเหลว?
- [ ] การนับ/DB logging เดิมไม่ถูกแตะ? (ถ้าแตะ inference_loop ให้ไล่ดู)
- [ ] `python -c "import ast; ast.parse(open('app.py').read())"` ผ่าน?
      (แตะ JS ด้วย → `node --check static/js/<ไฟล์>.js`)
- [ ] แตะ JS ที่อ้าง element ใหม่ → **เพิ่ม element ใน `templates/` แล้วหรือยัง**?
      (`$("id")` ที่ไม่มีจริงจะเงียบ ไม่ error — ฟีเจอร์หายไปเฉยๆ)
- [ ] ค่าคงที่ที่ใช้ทั้ง Python และ JS แก้ครบสองฝั่งหรือยัง? (เช่น `zones.HL_*` ↔ `HL_*`,
      `ZOOM_MIN` ↔ `min=` ของ `#awZoomRange`)
- [ ] เพิ่มคลาส CSS ใน `renderReport()` → **ใส่ครบทั้ง `artwork_check.html` และ
      `artwork_check_history.html` แล้วหรือยัง?** (`pytest tests/test_artwork_report_css.py`)
- [ ] เพิ่มค่าตั้งที่เปลี่ยน **ผลการอ่านข้อความ** → ใส่ใน `pipeline._ocr_fingerprint()` แล้วหรือยัง?
      (ไม่ใส่ = cache แท็บ "ข้อความ + คำแปล" ไม่หลุด แก้ค่าแล้วผลไม่เปลี่ยนแบบเงียบ)
- [ ] bump `CONFIG_VERSION` ถ้าผู้ใช้ต้อง verify?
- [ ] ถ้าแตะชั้นที่ "ชี้จุดให้คนดู" — เคสไม่มั่นใจ **ไม่แสดง** แทนที่จะเดา? (กฎเหล็ก 2)
- [ ] dependency ใหม่เป็น optional + auto-fallback? (ไม่มี = ฟีเจอร์หาย ไม่ใช่ระบบพัง)
- [ ] commit message ชัด + push ไป dev branch (ไม่ใช่ main)?
