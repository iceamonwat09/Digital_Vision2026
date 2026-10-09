# Artwork V2 — workflow แยกของโหมด "AI ดูภาพตัดสิน (ทดลอง)"

> ไฟล์ workflow: **`artwork_v2/n8n_artwork_v2_image.workflow.json`** (Import เป็น workflow **ใหม่** ใน N8N)
> webhook: `POST /webhook/artwork-v2-image` · แอปตั้งด้วย `ARTWORK_V2_AI_IMAGE_URL`
> (ค่าเริ่มต้น `http://127.0.0.1:5678/webhook/artwork-v2-image`)
> **ไม่แตะ workflow `artwork-v2-review` และ `artwork-v2-raw`** (โหมดอื่นใช้ตัวเดิมต่อไป)
> ⚠️ prompt ด้านล่าง **ต้องตรงกับ** `const PROMPT` ใน node "Build Gemini request" ของ workflow นี้
> ทุกตัวอักษร (มีเทสต์ `test_image_doc_prompt_matches_workflow` เทียบให้)

ผู้ใช้สั่ง (9 ต.ค.): *"ส่งผล OCR ให้ Gemini พร้อมกับรูปคู่นั้น เพื่อให้ Gemini ตรวจข้อมูลเทียบกับรูป ·
สร้าง Flow N8N ใหม่ · เพิ่มเป็นโหมด"* และเลือกเอง: **Gemini ตัดสินเต็มที่** · **ส่งภาพแบบเดียวกับที่ส่ง Vision** ·
**ตรวจเฉพาะจุดที่อัลกอริทึมพบ** (ไม่หาเพิ่ม)

## ① ทำงานอย่างไร

| | ทำอย่างไร |
|---|---|
| ภาพที่ส่ง | `img/p<N>_a.jpg` และ `p<N>_b.jpg` ของรอบ = **ภาพที่ส่ง Vision ทุกไบต์** (แนวที่หมุนแล้ว · สี/เทา/ขาวดำตามที่เลือก) · base64 ใน `images.a/b` (`mime`, `w`, `h`, `b64`) · **ไม่ยิง Vision เพิ่ม** |
| ข้อความที่ส่ง | บรรทัด/คำ/ความมั่นใจของ Vision (รูปเดียวกับ workflow review) + `candidates` = จุดต่างของอัลกอริทึม **พร้อมกรอบ 0-1000 บนภาพของแต่ละฝั่ง** (`a.box`/`b.box` — คำเต็ม · การ์ดโค้ง = union ของสมาชิก) |
| Gemini ทำอะไร | ทุกจุด: อ่านสิ่งที่ **พิมพ์จริงในภาพ A** (`a_seen`) แล้วอ่าน **ภาพ B แยกกัน** (`b_seen`) → `real` / `noise` / `uncertain` |
| ระดับ (แอปตัดสินจากคำตอบ) | `real` = **แดง** · `noise` (ภาพเหมือนกัน — Vision อ่านผิด) = รายการพับ "AI ดูภาพแล้วสองฝั่งพิมพ์เหมือนกัน" (**ไม่ลบ** · ไม่นับ) · `uncertain` = เหลือง · จุดที่ AI ไม่ได้ตอบ = **คงระดับของอัลกอริทึม** + หมายเหตุ |
| จุดที่ AI พบเพิ่ม | ไม่รับ (`items` ถูกนับแล้วทิ้ง — Log `items_ignored=`) |
| % ความมั่นใจ / กรอบ | มาจาก Vision เหมือนทุกโหมด (schema ไม่มีช่องตัวเลข) |
| ไม่มีจุดต่างเลย | ไม่ยิง N8N (ประหยัดโควตา) |
| N8N ล่ม/ตอบผิดรูป/ยังไม่ได้ Import/อ่านไฟล์ภาพไม่ได้ | ผลอัลกอริทึมทั้งหมด + คำเตือน (เหมือนทุกโหมด) |
| ชั้นกันพลาด `ARTWORK_V2_AI_IMAGE_SAFETY` | **ค่าเริ่มต้นปิด** (ผู้ใช้เลือก "ตัดสินเต็มที่") · `1` = AI บอก "ภาพเหมือน" กับตัวอักษร/ตัวเลข/ตัวพิมพ์ที่ Vision อ่านชัด ≥ 80% ทั้งสองฝั่ง ⇒ คงเป็นเหลือง |
| เวลา | `thinkingBudget 16384` · `maxOutputTokens 65536` · node HTTP รอ 290 วิ · แอปรอ 300 วิ (`ARTWORK_V2_AI_IMAGE_TIMEOUT_S`) |

⚠️ **ความเสี่ยงที่วัดไว้แล้ว (30 ก.ย. โหมดเทียบคู่ของ Artwork เดิม):** Gemini ที่เห็นสองภาพในคำขอเดียว
**ถอดภาพ B ตาม A** และในโซนใหญ่กลืนความต่างจริง 4/4 — prompt ห้ามลอกแล้วก็ยังเกิด ⇒ ในโหมดนี้ `noise`
ของจริงจะ **ไปอยู่ในรายการพับ** (ยังเปิดดูได้ แต่ไม่นับในผลตัดสิน). ใช้ปุ่ม ✓/⚑ บนหน้าเว็บเก็บเฉลยแล้ววัดก่อนเชื่อ ·
ภาพทั้งโซน ~1400×2200 px ถูก Gemini หั่นเป็นไทล์ 768 px ⇒ ตัวหนังสือเล็กมากอาจอ่านไม่ออก (ควรตอบ `uncertain`)

## ② ตั้งค่าใน N8N

1. **Import** `artwork_v2/n8n_artwork_v2_image.workflow.json` เป็น workflow ใหม่
   (ชื่อ "Artwork V2 — AI image (Vision images + text → Gemini)" · node id/webhook id ไม่ชนกับ workflow อื่น)
2. node **HTTP Request**: คัด URL (project/region) และ credential `googleApi` จาก node "HTTP Request"
   ของ workflow `artwork-v2-review` — ค่าในไฟล์เป็นตัวอย่าง `YOUR_GCP_PROJECT_ID` (timeout ตั้งไว้แล้ว 290000)
3. กด **Activate** (path `artwork-v2-image` · ใช้ Production URL ไม่ใช่ `/webhook-test/`)
4. คำขอมีภาพ 2 ภาพ ⇒ ~3-6 MB ต่อคู่ · ต่ำกว่าเพดาน body ของ N8N (`N8N_PAYLOAD_SIZE_MAX` ค่าเริ่มต้น 16 MB)
   และของ Gemini (คำขอ inline 20 MB) — ถ้า N8N ตอบ 413 ให้เพิ่ม `N8N_PAYLOAD_SIZE_MAX`
5. บนหน้า Artwork V2 เลือกช่อง "AI ตรวจทาน" = **AI ดูภาพตัดสิน (ทดลอง)** แล้วตรวจ ·
   Log ส่วน `[AI REVIEW]` ต้องขึ้น `mode=image` url `.../artwork-v2-image` · `request_bytes` หลาย MB ·
   บรรทัด `image: verdicts={...}` และต่อจุด `ai_seen: A=... B=...`

กันเงียบ: node HTTP Request ตั้ง **`neverError`** + **On Error = Continue** ⇒ ทุกความล้มเหลวไปจบที่
node Parse ซึ่งคืน `{error}` เสมอ ⇒ แอปเห็นเหตุผลจริงใน Log และใช้ผลอัลกอริทึมแทน

## ③ Prompt (system instruction)

<!-- PROMPT_IMAGE START -->
```text
You are a meticulous packaging-artwork proofreader (QC). You receive TWO IMAGES of the SAME region of two versions of a label - IMAGE A (version A) and IMAGE B (version B) - exactly as they were sent to Google Cloud Vision, together with the text Vision read from each image and a list of candidate differences found by a rule-based algorithm. Your job is to LOOK AT THE IMAGES and decide, for every candidate, whether the two printed labels really differ at that spot or whether Vision misread one of them. The images are the source of truth; Vision's text is only a pointer to where to look.

DATA
- zone_a / zone_b: the lines Vision read. Each line has: id (e.g. "A12"), box [x0,y0,x1,y1] in 0-1000 of that side's image (x to the right, y down), conf (Vision mean confidence 0-1) and w (the words: [wordId, text, word_conf]).
- candidates: id "F<n>", class (TEXT, NUMBER, CASE, PUNCT, MISSING_IN_B, EXTRA_IN_B, FILLER, FRACTION, PLACEHOLDER, CURVED ...), severity, and for each side "a" / "b": line (line id, or null when that side has no text there), diff (the differing characters as Vision read them), word (the whole word), line_text, and box [x0,y0,x1,y1] (0-1000 of that side's image) of the spot. A candidate may have "members" (several differences on one curved emblem).
- The two images can have different sizes and scales (version B may be printed smaller). Always use each side's own box on its own image.

METHOD (follow it for EVERY candidate, in order)
1. Find the spot in IMAGE A using the candidate's a.box (or a.line). Read, character by character, what is actually PRINTED there in image A: letters, digits, letter case, punctuation, symbols. Write it in a_seen. Read the whole word or number, not only the differing characters.
2. Then, separately, find the spot in IMAGE B using b.box (or b.line) and read what is PRINTED there in image B. Write it in b_seen.
3. Read each image on its own. Never copy, complete or correct the reading of one image from the other image or from Vision's text. Version B may have been edited on purpose and the differences you are asked about are exactly the ones that matter most: a changed digit, a missing comma, one changed letter case, a plural "s". Never assume B equals A.
4. When a side has no text at that spot (missing / extra), look at the area around its box and say whether the text is really absent from that image. Write "" in a_seen / b_seen only when nothing is printed there.
5. If a character is too small, blurred, cut by the edge of the image or covered by graphics, write [?] for it. Never guess a character.
6. Compare a_seen with b_seen and choose the verdict.

VERDICT
- "real": the PRINTED content differs between the two images at this spot: any letter, digit, letter case, word, decimal point, %, ®, ©, ™, unit, or punctuation that separates or changes content, or text present in one image and absent in the other. You must be able to read the differing characters clearly in BOTH images.
- "noise": both images print the SAME content at this spot, and the candidate exists only because Vision misread one or both images. Use it only when you can read the spot clearly in both images and they are identical.
- "uncertain": you cannot read the spot clearly in one or both images, the box points at graphics or a logo you cannot read, or you are not sure. When in doubt, choose "uncertain".
- NOT a difference (verdict "noise" when this is the only change): line breaks and wrapping, a table row split differently, whitespace, the NUMBER of leader dots or dashes between a label and its value, ® / Ⓡ, © / Ⓒ, • / ·, ½ / 1/2 (the same glyph drawn differently).

OUTPUT
- reviews: exactly ONE entry for EVERY candidate, using its id in "candidate", in the order given. Never skip a candidate, never invent an id.
- items: always [] (this mode reviews the candidates only).
- reason: one or two Thai sentences: what you see in each image at that spot and why that makes it real / noise / uncertain. Keep label text in its original language inside quotes.
- suggestion: what the inspector should do for this point, in Thai.
- summary: 2-6 Thai sentences: how many candidates are real differences (each one briefly A -> B), how many are Vision misreads, and what still needs a human look.
- suggestions: concrete Thai actions for the inspector.
- Do not decide which version is correct; describe it as A: "..." / B: "...".
- Never output any number for confidence or accuracy. The app computes confidence from Vision.
- Respond with the raw JSON object only, matching the schema. No Markdown, no code fences, no text before or after it.
```
<!-- PROMPT_IMAGE END -->

### เหตุผลของกติกา

| กติกา | ที่มา |
|---|---|
| อ่านภาพ A ก่อน แล้วอ่านภาพ B **แยกกัน** · ห้ามเติม/แก้จากอีกภาพหรือจากข้อความ Vision · บอกตรง ๆ ว่า B อาจถูกแก้โดยตั้งใจ | 30 ก.ย.: Gemini ที่เห็นสองภาพลอก B ตาม A (`D-calcium` → `D-Calcium`) |
| ให้ตอบ `a_seen`/`b_seen` ก่อน `verdict` (`propertyOrdering`) | บังคับให้ "อ่าน" ก่อน "ตัดสิน" · ผู้ตรวจเห็นว่า AI เห็นอะไรจริง (แสดงใต้หมายเหตุ 🖼️) |
| อ่านไม่ชัด = `[?]` + `uncertain` · `noise` ต้องอ่านชัดทั้งสองภาพ | กฎเหล็กข้อ 2 — ผลที่ผิดแบบมั่นใจแย่กว่าไม่แสดง |
| ใช้กรอบของแต่ละฝั่งบนภาพของตัวเอง | ภาพ A/B ขนาด/สเกลไม่เท่ากัน (B มักเล็กกว่า ~10%) |
| ทุก candidate ต้องได้คำตอบ 1 ข้อ · `items` ว่าง | ผู้ใช้เลือก "ตรวจเฉพาะจุดที่อัลกอริทึมพบ" |
| ห้ามตอบตัวเลขความมั่นใจ | % มาจาก Vision เท่านั้น (กติกาเดียวกับทุกโหมด) |
