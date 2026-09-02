# N8N Artwork OCR — prompt + สัญญาคำตอบ (ฉบับที่ระบบนี้คาดหวัง)

> **ไฟล์นี้เป็นเอกสาร ไม่ใช่โค้ดที่รัน** — prompt จริงอยู่ใน workflow ของ N8N บนสถานี
> ระบบฝั่ง Python แก้ prompt ไม่ได้ ต้องเอาข้อความในไฟล์นี้ไปวางในโหนด Gemini เอง
> หลังแก้แล้วยืนยันด้วย `py -3.9 diagnose_n8n_ocr.py --ping-only`

---

## 1. ทำไมต้องแก้ prompt — อาการที่เกิดจริง

ผู้ใช้รายงานว่า **"มีตัวอักษรแปลก ๆ โผล่ออกมาทั้งที่ของจริงไม่มี"**
ต้นเหตุมี 2 ชั้น แก้คนละที่:

| ชั้น | อาการ | แก้ที่ไหน | สถานะ |
|---|---|---|---|
| **รูปแบบคำตอบ** | Gemini ครอบ JSON ด้วยรั้ว ` ```json ` → ฝั่ง Python แกะไม่ออก แล้วเอา**ทั้งก้อนรวมรั้ว**ไปเป็นข้อความ ⇒ คำว่า `json` `text` `blocks` `{` `}` เข้าไปเทียบใน MISMATCH/SPELL | `inspectors/ocr_n8n.py` | ✅ แก้แล้ว (ถอดรั้วให้อัตโนมัติ) |
| **เนื้อหาคำตอบ** | LLM **เดา/เติมคำที่ไม่มีในภาพ** (hallucination) โดยเฉพาะโซนที่เป็นโลโก้/กราฟิก/ภาพเบลอ | **prompt ใน N8N** | ⬅️ ไฟล์นี้ |

**ชั้นที่ 2 แก้ด้วยโค้ดฝั่งเราไม่ได้** — ถ้า LLM คืนคำที่ดูสมเหตุสมผลแต่ไม่มีในภาพ
ไม่มีทางแยกออกจากคำจริงได้เลย นี่คือเหตุผลที่ prompt สำคัญกับงาน QC มากกว่างานทั่วไป.

---

## 2. หลักการที่ prompt ต้องยึด (เรียงตามความสำคัญ)

1. **ห้ามเดา ห้ามเติม ห้ามแก้คำผิดให้** — งานนี้คือ *หาคำผิด* ถ้า LLM
   "ช่วยแก้" `Cude` เป็น `Crude` ให้ ระบบจะไม่มีวันเจอคำผิดนั้นเลย
   ⇒ นี่คือข้อที่ **ห้ามพลาดเด็ดขาด**
2. **ถ่ายทอดตามที่เห็น** รวมทั้งตัวพิมพ์ใหญ่/เล็ก เครื่องหมาย และการขึ้นบรรทัด
3. **ไม่แปล ไม่ถอดเสียง** — อาหรับต้องได้อาหรับ จีนต้องได้จีน
4. **อ่านไม่ออก = คืนค่าว่าง** ดีกว่าเดา (ตรงกับกฎเหล็กข้อ 2 ของโปรเจกต์:
   *ผลที่ผิดแบบมั่นใจ แย่กว่าไม่แสดงผล*)
5. **คืน JSON ล้วน ไม่มีรั้ว markdown ไม่มีคำอธิบาย**

---

## 3. Prompt ที่แนะนำ (วางในโหนด Gemini)

```
You are a precision OCR engine for packaging artwork quality control.
Transcribe text EXACTLY as printed in the image.

ABSOLUTE RULES — violating any of these makes the output useless:
1. NEVER guess, complete, correct, or "improve" any word. If the artwork
   shows a misspelling (e.g. "Cude", "Phosphours", "Thailan"), you MUST
   reproduce that misspelling character-for-character. Finding such
   misspellings is the entire purpose of this task.
2. NEVER translate or transliterate. Arabic stays Arabic, Chinese stays
   Chinese, Thai stays Thai. Keep the original script and digits exactly
   as printed (Arabic-Indic digits stay Arabic-Indic).
3. NEVER add text that is not visibly present, and never describe the
   image. No captions, no summaries, no commentary.
4. If a region is blurred, cut off, or unreadable, omit it. Returning
   nothing is correct; inventing plausible text is a failure.
5. Preserve line breaks as they appear. Preserve capitalization,
   punctuation, spacing, units and symbols (%, ®, ™, ℮, °C).
6. If the image contains no text at all, return {"text": "", "blocks": []}.

OUTPUT FORMAT — return raw JSON only. No markdown fences, no ```json,
no explanation before or after:
{
  "text": "<every line of text, separated by \n>",
  "blocks": [
    {"text": "<one word or short phrase>",
     "bbox": [x, y, width, height],
     "conf": 0.0-1.0}
  ]
}

bbox uses PIXEL coordinates of the image you were given, origin at the
top-left corner. "blocks" is optional — omit it entirely rather than
guessing coordinates. Wrong coordinates are worse than no coordinates.
```

### จุดที่ต่างจาก prompt ทั่วไปในอินเทอร์เน็ต และ **ทำไม**

| บรรทัด | เหตุผลเฉพาะงานนี้ |
|---|---|
| "NEVER correct any word … reproduce that misspelling" | prompt OCR ทั่วไปมักบอกให้ "อ่านให้ถูกต้อง" ซึ่ง**ทำลายงาน QC โดยตรง** |
| "Returning nothing is correct" | ให้ทางออกกับ LLM แทนที่จะบีบให้เดา |
| "no markdown fences" | กันอาการ ` ```json ` (ฝั่ง Python กันไว้อีกชั้นแล้ว แต่กันสองชั้นดีกว่า) |
| "bbox uses PIXEL coordinates … origin top-left" | LLM มีหลาย convention (0..1 / 0..1000 / pixel) — ระบบเดาให้ได้ แต่ระบุชัดดีกว่า |
| "Wrong coordinates are worse than no coordinates" | กรอบแดงที่ชี้ผิดแถวเคยเกิดจริงบนสถานี |

> ⚠️ **`temperature` ต้องตั้งเป็น `0`** ในโหนด Gemini — ค่า default (0.7-1.0)
> ทำให้คำตอบไม่คงที่ ไฟล์เดิมอ่านคนละแบบทุกครั้ง วัดซ้ำไม่ได้ = ใช้กับ QC ไม่ได้

---

## 4. สัญญาคำตอบที่ฝั่ง Python รับได้

`inspectors/ocr_n8n.py` แกะได้ **12 รูปแบบ** (มีเทสต์ `tests/test_n8n_ocr_response.py` คุม):

| รูปแบบ | ตัวอย่าง | แกะได้ |
|---|---|---|
| JSON ตรง | `{"text":"...","blocks":[]}` | ✅ |
| รั้ว markdown | ` ```json {...} ``` ` | ✅ ถอดรั้วให้ |
| N8N array | `[{"text":"..."}]` | ✅ |
| ซ้อนใน string | `{"data":"{\"text\":\"...\"}"}` | ✅ |
| ซ้อน + รั้ว | `{"data":"```json{...}```"}` | ✅ |
| ซ้อนใน object | `{"output":{"text":"..."}}` | ✅ |
| `text` เป็น JSON เอง | `{"text":"{\"text\":\"...\"}"}` | ✅ |
| plain text ล้วน | `INGREDIENTS TUNA` | ✅ (ติดธงเตือน) |
| **หน้า HTML** | `<!DOCTYPE html>...` | ❌ **ปฏิเสธ** → UNREADABLE |

**ที่ปฏิเสธคือสิ่งที่ควรปฏิเสธ** — หน้า HTML แปลว่า workflow ไม่ได้ Activate
หรือ path ผิด ไม่ใช่ผล OCR. เดิมข้อความ `<!DOCTYPE html><title>Error` ถูกนำไป
เทียบเป็นข้อความบนฉลากจริง ๆ.

---

## 5. ตรวจว่าแก้แล้วได้ผล

```bat
py -3.9 diagnose_n8n_ocr.py --ping-only
```
ยิงภาพจริงผ่านเส้นทางเดียวกับ production แล้วบอกว่าแกะคำตอบได้ไหม

```bat
py -3.9 verify_ocr.py --files "D:\Digital 2026\Vision-Defect\TEST" --engines n8n
```
วัด recall/precision เทียบ text layer ของ PDF + **ยิงโซนว่างเพื่อจับ hallucination**
(ชั้น NO-TEXT PROBE — อะไรที่คืนมาจากโซนว่าง = แต่งขึ้นทั้งหมด)

**เกณฑ์ที่ควรได้:** recall ≥ 95% · precision ≥ 95% · โซนที่แต่งขึ้น **0**
(Tesseract ทำได้ 0/36 — LLM ควรทำได้เท่ากันถ้า prompt ถูก)

> ⚠️ **โซนที่ text layer เสียเอง จะถูก "ข้าม" ไม่นำมาให้คะแนน** (ขึ้นบรรทัด
> `โซน N ข้าม — เฉลยจาก text layer ใช้ไม่ได้: …`). เดิมเครื่องมือเอาข้อความขยะจาก
> ฟอนต์ที่แมปอักขระผิดมาเป็น "เฉลย" แล้ว **หักคะแนน engine ที่อ่านถูก** ⇒ ตัวเลข
> recall/precision ที่เคยวัดไว้อาจต่ำกว่าความจริงเล็กน้อยในไฟล์ที่มีฟอนต์แบบนั้น
> ถ้าไฟล์ไหนถูกข้ามทั้งหมด สคริปต์จะบอกว่า **ไฟล์นี้ไม่มีเฉลยที่เชื่อได้เลย**

---

## 5.1 เทียบกับ Code node ที่ใช้อยู่บนสถานี (ตรวจ 18 ส.ค. 2026)

โหนด **"Code in JavaScript2"** (validate input + build Gemini request) ที่ใช้อยู่
**ใช้งานได้ ไม่ต้องรื้อ** — ตรงกับสัญญาฝั่ง Python ทุกข้อสำคัญ:

| สิ่งที่ตรวจ | สถานะ |
|---|---|
| อ่าน `$json.body.image_b64` | ✅ ตรงกับที่ `ocr_n8n.ocr_image` ส่ง (form-urlencoded) |
| `temperature: 0` | ✅ จำเป็นกับ QC (วัดซ้ำได้) |
| `responseMimeType` + `responseSchema` | ✅ **ดีกว่าใช้ prompt อย่างเดียว** — บังคับ JSON ตั้งแต่ต้นทาง จึงแทบไม่เจอรั้ว ` ```json ` |
| "DO NOT correct spelling … transcribe the misspelling exactly" | ✅ ข้อที่ห้ามพลาด มีแล้ว |
| "DO NOT translate" | ✅ |
| คีย์ `text` / `blocks` / `engine` | ✅ ตรงสัญญา (`engine` จะไปโผล่ในรายงานต่อโซน) |
| ตรวจ magic bytes → `mimeType` | ✅ ดีกว่าเดา `image/jpeg` |

**3 จุดที่ควรแก้** (เล็กน้อย ไม่กระทบของเดิม):

1. **บอก convention ของ `bbox` ให้ชัด** — prompt ปัจจุบันเขียนแค่ `[x, y, w, h]`
   ไม่ได้บอกหน่วย. ฝั่ง Python เดาให้ได้ (`highlight._infer_scale` แยก 0..1 /
   0..1000 / pixel ต่อโซน) แต่ **กรอบแดงที่ชี้ผิดแถวเคยเกิดจริงบนสถานี** จึงควร
   ระบุตรง ๆ แล้วเติมประโยคปิดท้าย:
   > `bbox uses PIXEL coordinates of the image you were given, origin at the top-left corner. "blocks" is optional — omit it entirely rather than guessing coordinates. Wrong coordinates are worse than no coordinates.`

2. **เปลี่ยน "อ่านไม่ชัด → เดาเท่าที่ได้แล้วลด conf" เป็น "ข้ามไปเลย"** —
   ปัจจุบันเขียนว่า *"transcribe what you can and lower the confidence for that
   block"* ซึ่งฟังดูปลอดภัยแต่**ไม่ปลอดภัยจริง** เพราะฝั่ง Python ใช้ `conf` แบบนี้:
   - `ocr._mean_conf(blocks)` = **ค่าเฉลี่ยทั้งโซน** → `checks.check_readability`
     ฟ้อง UNREADABLE เมื่อ **เฉลี่ย < 0.5**
   - ⇒ คำที่เดามา 1-2 คำ (conf 0.3) ปนกับคำดี 20 คำ (conf 0.95) → เฉลี่ยยังสูง
     **คำที่เดาก็ยังไหลเข้าไปเทียบใน MISMATCH/SPELL อยู่ดี** (ไม่มีการกรองรายคำ)
   - ⇒ กลับกัน ถ้า Gemini ใส่ conf ต่ำหลายบล็อก **ทั้งโซน**ที่อ่านได้ดีจะกลายเป็น
     UNREADABLE ทั้งที่ข้อความถูก
   ใช้ข้อความของหัวข้อ 3 แทน: *"If a region is blurred, cut off, or unreadable,
   omit it. Returning nothing is correct; inventing plausible text is a failure."*

3. **ทาง `valid === false` ต้องตอบเป็น HTTP error ไม่ใช่ 200** — ตอนนี้ถ้า base64
   เสีย/ภาพใหญ่เกิน โหนดคืน `{valid:false, error:"..."}`; ถ้า workflow ตอบ **200**
   พร้อม JSON ก้อนนี้ ฝั่ง Python จะเห็นแค่ "ไม่มีคีย์ text" → `text=""` →
   โซนนั้นขึ้น **UNREADABLE ว่า "OCR ไม่พบข้อความ"** ซึ่ง**บอกสาเหตุผิด**
   (`ocr_n8n` ไม่ได้อ่านคีย์ `error` จาก payload — อ่านเฉพาะ `text`/`blocks`/`engine`).
   ⇒ ให้โหนดถัดไปตอบ **HTTP 400** พร้อมข้อความ error (อย่าใช้ 5xx เพราะจะโดน retry
   ฟรี 1 รอบ) แล้วผู้ตรวจจะเห็นสาเหตุจริงบนการ์ด

**ℹ️ ข้อสังเกตเพิ่ม (ยังไม่ต้องแก้ ถ้ายังไม่เจออาการ):**
- `maxOutputTokens: 16384` — โซนที่ข้อความเยอะมาก (ตารางโภชนาการหลายภาษา) อาจถูกตัด
  กลางคัน ⇒ JSON ไม่ครบ ⇒ ฝั่ง Python แกะไม่ออก แล้วตกไปทาง "plain text + ธงเตือน".
  **อาการที่สังเกตได้:** โซนนั้นมี `note` เตือนใน "ข้อความ OCR ต่อโซน" และข้อความจบกลางคำ
  → ถ้าเจอให้เพิ่มค่านี้
- `blocks` ที่ไม่มี `bbox` จะถูกทิ้งจากชั้นกรอบแดง (`read_zone` กรอง `if b.get("bbox")`)
  — ไม่กระทบผลตรวจ QC เลย
- `thinkingConfig.thinkingBudget: 0` ใช้ได้กับงานถอดความ — ถ้าเปลี่ยนรุ่นโมเดลแล้ว
  โหนดขึ้น error ให้ลบ 2 บรรทัดนี้ตามคอมเมนต์ที่เขียนไว้แล้ว

**ยืนยันหลังแก้:** `py -3.9 verify_artwork_features.py --n8n`
(ต้องได้ `✓ อ่านภาพทดสอบได้ถูกต้อง (เจอ 'DIAGNOSE 12345')`)

---

## 6. ค่าตั้งฝั่ง Python ที่เกี่ยวข้อง (`config.py`)

| ค่า | default | ความหมาย |
|---|---|---|
| `N8N_OCR_RETRIES` | `1` | ลองซ้ำเฉพาะ **ต่อไม่ติด / timeout / 5xx** · ไม่ลองซ้ำกับ 404/413 (ยิงกี่ครั้งก็ผลเดิม) · `0` = ปิด = เดิมเป๊ะ |
| `N8N_OCR_RETRY_WAIT_S` | `1.5` | หน่วงก่อนลองใหม่ (คูณสองขึ้นทุกครั้ง) |
| `N8N_OCR_STRICT_RESPONSE` | `1` | ปฏิเสธคำตอบที่เป็นหน้า HTML · `0` = ปิด = เดิม (เชื่อทุกอย่าง) |
| `N8N_OCR_TIMEOUT_S` | `60` | Gemini ช้าได้ถ้าโซนใหญ่ |

> 💡 **โควตา:** ตั้งแต่มีด่านคุณภาพของ text layer โซนที่ฟอนต์ในไฟล์แมปอักขระผิด
> จะ **ถูกส่งมาที่ N8N แทนที่จะใช้ข้อความใน PDF** ⇒ ไฟล์ที่ฟอนต์พังจะยิง OCR
> มากกว่าเดิม (วัดจากไฟล์จริง 1 ฉบับ: 16 โซนจาก 54). มี 2 เงื่อนไขที่ทำให้เกิด:
> `ARTWORK_PDFTEXT_BAD_GLYPH_CHECK` (เจออักขระเสียจริง) และ
> `ARTWORK_PDFTEXT_FONT_STRUCTURE_CHECK` (ฟอนต์เป็น CID/Identity-H ที่ไม่มี
> ToUnicode ⇒ สงสัยตั้งแต่ก่อนเห็นอาการ) — **ตั้งเป็น 0 ได้ทั้งคู่ถ้าโควตาตึง
> แต่จะกลับไปเสี่ยงกับข้อความที่ผิดแบบมั่นใจ**.
> ถ้าอยากบังคับให้ทุกโซนอ่านด้วย OCR ทั้งใบ ใช้ช่องติ๊ก
> **"🔍 อ่านทุกโซนด้วย OCR"** บนหน้าเว็บ (ต่อการตรวจ 1 ครั้ง ไม่ใช่ค่าถาวร)
