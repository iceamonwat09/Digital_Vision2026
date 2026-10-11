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
| ภาพที่ส่ง (ค่าเริ่มต้น · `ARTWORK_V2_AI_IMAGE_CROPS=1`) | **ครอปรอบแต่ละจุด ฝั่งละ 1 รูป** ตัดจาก `img/p<N>_a.jpg` / `p<N>_b.jpg` ของรอบ (= ภาพที่ส่ง Vision · ไม่เรนเดอร์ใหม่) · ความละเอียดจริง (ด้านยาว ≤ `ARTWORK_V2_AI_IMAGE_CROP_MAX_SIDE` 768 px · กว้างอย่างน้อย 480 px ให้เห็นคำข้าง ๆ + บรรทัดบน/ล่าง) · JPEG q95 · `crops: [{candidate, side, mime, w, h, b64}]` + `candidates[].a/b.crop = {w, h, box}` (ตำแหน่งจุดในครอป 0-1000) + `image_sizes` · contract `artwork-v2-image/2` · ทุกคู่ = **1 คำขอ** (10 จุด = 20 รูป) · **ไม่ยิง Vision เพิ่ม** |
| ครอปความละเอียดสูง (ค่าเริ่มต้น · `ARTWORK_V2_AI_IMAGE_CROP_HIRES=1` · PDF เท่านั้น) | ตัวอักษรที่จุดสูงไม่ถึง `ARTWORK_V2_AI_IMAGE_CROP_TARGET_LH` (36 px) บนภาพที่ส่ง Vision ⇒ **เรนเดอร์บริเวณครอปใหม่จาก PDF ต้นฉบับ** ให้ตัวอักษรสูงราว 36 px · เพดาน `ARTWORK_V2_AI_IMAGE_CROP_DPI_MAX` (1200 dpi) และไม่เกินความละเอียดจริงของ PDF ที่เป็นภาพสแกน · ด้านยาวยังไม่เกิน 768 px (บริบทรอบจุดแคบลงแทน) · ขยายไม่ถึง 1.25 เท่า/ภาพถ่าย/เรนเดอร์ไม่ได้ ⇒ ครอปจาก JPEG เดิม · หมุน/สีเดียวกับภาพที่ส่ง Vision · Log `hires_crops=` `hires_dpi_max=` |
| ไม่เห็นข้อความ (ค่าเริ่มต้น · `ARTWORK_V2_AI_IMAGE_BLIND=1` · ต้องใช้ครอป) | ส่ง **ภาพครอป + ตำแหน่งจุดในครอปเท่านั้น** — ไม่มีบรรทัด/คำ/diff/ชนิดของจุดจาก Vision (contract `artwork-v2-image/3` · node Build ตัดข้อความทิ้งซ้ำอีกชั้น) ⇒ Gemini อ่าน A/B จากพิกเซลเอง (prompt ชุด `PROMPT_BLIND`) แล้ว **แอปเทียบ `a_seen`/`b_seen` เอง** ด้วยกติกาเดียวกับอัลกอริทึม (ช่องว่าง · จุดไข่ปลา · ®/Ⓡ · ½/1/2 · คงตัวพิมพ์/เครื่องหมาย) · `[?]` / ว่างทั้งคู่ / AI บอกไม่แน่ใจ / **คำตอบของ AI ขัดกับสิ่งที่มันอ่านเอง** ⇒ ไม่แน่ใจ (เหลือง) · Log ต่อจุด `blind: ai_said= app=` |
| ภาพที่ส่ง (แบบเดิม · `ARTWORK_V2_AI_IMAGE_CROPS=0`) | ภาพทั้งโซน A/B ทุกไบต์ใน `images.a/b` · contract `/1` — ⚠️ สถานี 9 ต.ค. วัดได้ว่า Gemini นับภาพละ **258 token** (≈ ย่อเหลือ 768 px) ⇒ ตัวอักษร ~28 px เหลือ ~6 px อ่านเองไม่ได้ |
| เพดาน (แผน ก) | ไม่เกิน `ARTWORK_V2_AI_IMAGE_MAX_CANDIDATES` (40) จุดต่อคำขอ · เลือกแดงก่อนเหลือง · จุดที่เกิน/ไม่มีตำแหน่งบนภาพฝั่งใดฝั่งหนึ่ง ⇒ **ไม่ส่ง · คงระดับของอัลกอริทึม** + หมายเหตุ "AI ไม่ได้ตรวจจุดนี้ (...)" (ไม่ยิงคำขอที่ 2) |
| ข้อความที่ส่ง | บรรทัด/คำ/ความมั่นใจของ Vision (รูปเดียวกับ workflow review) + `candidates` = จุดต่างของอัลกอริทึม **พร้อมกรอบ 0-1000 บนภาพของแต่ละฝั่ง** (`a.box`/`b.box` — คำเต็ม · การ์ดโค้ง = union ของสมาชิก) |
| Gemini ทำอะไร | ทุกจุด: อ่านสิ่งที่ **พิมพ์จริงในภาพ A** (`a_seen`) แล้วอ่าน **ภาพ B แยกกัน** (`b_seen`) → `real` / `noise` / `uncertain` |
| ระดับ (แอปตัดสินจากคำตอบ) | `real` = **แดง** (ยกเว้นข้อความโค้ง/เอียง ⇒ **เหลือง** — `ARTWORK_V2_AI_IMAGE_CURVED_YELLOW`, สถานี 9 ต.ค.: ตรา OMEGA-6 ที่เหมือนกันทั้งสองไฟล์ AI ตอบ real เพราะลอก "5&-3" ที่ Vision อ่านผิด) · `noise` (ภาพเหมือนกัน — Vision อ่านผิด) = รายการพับ "AI ดูภาพแล้วสองฝั่งพิมพ์เหมือนกัน" (**ไม่ลบ** · ไม่นับ) · `uncertain` = เหลือง · จุดที่ AI ไม่ได้ตอบ = **คงระดับของอัลกอริทึม** + หมายเหตุ |
| จุดที่ AI พบเพิ่ม | ไม่รับ (`items` ถูกนับแล้วทิ้ง — Log `items_ignored=`) |
| % ความมั่นใจ / กรอบ | มาจาก Vision เหมือนทุกโหมด (schema ไม่มีช่องตัวเลข) |
| ไม่มีจุดต่างเลย | ไม่ยิง N8N (ประหยัดโควตา) |
| N8N ล่ม/ตอบผิดรูป/ยังไม่ได้ Import/อ่านไฟล์ภาพไม่ได้ | ผลอัลกอริทึมทั้งหมด + คำเตือน (เหมือนทุกโหมด) |
| ชั้นกันพลาด `ARTWORK_V2_AI_IMAGE_SAFETY` | **ค่าเริ่มต้นปิด** (ผู้ใช้เลือก "ตัดสินเต็มที่") · `1` = AI บอก "ภาพเหมือน" กับตัวอักษร/ตัวเลข/ตัวพิมพ์ที่ Vision อ่านชัด ≥ 80% ทั้งสองฝั่ง ⇒ คงเป็นเหลือง |
| เวลา | `thinkingBudget 16384` · `maxOutputTokens` = 16384 + คำตอบ `min(32768, 2048 + 512 × จำนวนจุด)` (เดิม 65536 ตายตัว) · `reviews.maxItems` = จำนวนจุด · node HTTP รอ 290 วิ · แอปรอ 300 วิ (`ARTWORK_V2_AI_IMAGE_TIMEOUT_S`) |

⚠️ **ความเสี่ยงที่วัดไว้แล้ว (30 ก.ย. โหมดเทียบคู่ของ Artwork เดิม):** Gemini ที่เห็นสองภาพในคำขอเดียว
**ถอดภาพ B ตาม A** และในโซนใหญ่กลืนความต่างจริง 4/4 — prompt ห้ามลอกแล้วก็ยังเกิด ⇒ ในโหมดนี้ `noise`
ของจริงจะ **ไปอยู่ในรายการพับ** (ยังเปิดดูได้ แต่ไม่นับในผลตัดสิน). ใช้ปุ่ม ✓/⚑ บนหน้าเว็บเก็บเฉลยแล้ววัดก่อนเชื่อ ·
ภาพทั้งโซนถูก Gemini ย่อเหลือราว 768 px (258 token/ภาพ — วัดจาก `usage` บนสถานี) ⇒ จึงเปลี่ยนเป็นครอปต่อจุด ·
ครอปแยกรูป A/B และ prompt สั่ง "ดูภาพก่อน แล้วค่อยดูข้อความของ Vision" — ยังต้องวัดซ้ำบนสถานีว่า `a_seen`/`b_seen` มาจากภาพจริง

⚠️ **ทำไมต้องไม่เห็นข้อความ (สถานี 9 ต.ค. งาน John West):** แม้ครอปจะอ่านได้แล้ว Gemini ยังตอบ `real` ให้
**17/17 จุดที่หลักฐานภาพบอกว่าเหมือน** และ `a_seen`/`b_seen` เป็นคำที่ Vision อ่านผิดเป๊ะ (`و 26` · `Og`/`0 g` ·
`تحتوى`/`تحتوي`) ⇒ ข้อความใน DATA ถูกใช้เป็นคำตอบ ไม่ใช่แค่ตัวชี้ตำแหน่ง ⇒ ตัดข้อความออกจากต้นทาง ·
ตัวอักษรของงานนั้นสูงแค่ 10-13 px ที่ 400 dpi ⇒ เรนเดอร์ครอปใหม่ที่ dpi สูง

## ② ตั้งค่าใน N8N

1. **Import** `artwork_v2/n8n_artwork_v2_image.workflow.json` เป็น workflow ใหม่
   (ชื่อ "Artwork V2 — AI image (Vision images + text → Gemini)" · node id/webhook id ไม่ชนกับ workflow อื่น)
2. node **HTTP Request**: คัด URL (project/region) และ credential `googleApi` จาก node "HTTP Request"
   ของ workflow `artwork-v2-review` — ค่าในไฟล์เป็นตัวอย่าง `YOUR_GCP_PROJECT_ID` (timeout ตั้งไว้แล้ว 290000)
3. กด **Activate** (path `artwork-v2-image` · ใช้ Production URL ไม่ใช่ `/webhook-test/`)
4. คำขอมีภาพครอป 2 รูปต่อจุด (~30-150 KB/รูป ⇒ 10 จุด ≈ 1-3 MB) · ต่ำกว่าเพดาน body ของ N8N (`N8N_PAYLOAD_SIZE_MAX` ค่าเริ่มต้น 16 MB)
   และของ Gemini (คำขอ inline 20 MB) — ถ้า N8N ตอบ 413 ให้เพิ่ม `N8N_PAYLOAD_SIZE_MAX`
5. บนหน้า Artwork V2 เลือกช่อง "AI ตรวจทาน" = **AI ดูภาพตัดสิน (ทดลอง)** แล้วตรวจ ·
   Log ส่วน `[AI REVIEW]` ต้องขึ้น `mode=image` url `.../artwork-v2-image` · `request_bytes` หลาย MB ·
   บรรทัด `image: verdicts={...}` และต่อจุด `ai_seen: A=... B=...`

กันเงียบ: node HTTP Request ตั้ง **`neverError`** + **On Error = Continue** ⇒ ทุกความล้มเหลวไปจบที่
node Parse ซึ่งคืน `{error}` เสมอ ⇒ แอปเห็นเหตุผลจริงใน Log และใช้ผลอัลกอริทึมแทน

## ③ Prompt (system instruction)

<!-- PROMPT_IMAGE START -->
```text
You are a meticulous packaging-artwork proofreader (QC). You compare two versions of the same label region - version A and version B. A rule-based algorithm compared the text that Google Cloud Vision read from each version and found candidate differences. Vision often misreads exactly at those spots. Your job is to LOOK AT THE IMAGES and decide, for every candidate, whether the two printed labels really differ at that spot or whether Vision misread one of them. The images are the source of truth; Vision's text is only a pointer to where to look.

IMAGES
- Usually you receive TWO CROPS PER CANDIDATE, cut from the exact images that were sent to Vision, at their original resolution: a crop labelled "F<n> - A crop" (version A) and a crop labelled "F<n> - B crop" (version B). The candidate's a.crop.box / b.crop.box gives the spot inside that crop as [x0,y0,x1,y1] in 0-1000 of the crop (x to the right, y down). The crop also shows neighbouring words and parts of the lines above and below for context.
- Sometimes you receive instead ONE whole image per version ("IMAGE A" / "IMAGE B") and must find the spot with the candidate's a.box / b.box (0-1000 of that whole image).
- The two versions can have different sizes and scales (version B may be printed smaller). Always use each side's own box on its own image.

DATA
- zone_a / zone_b: the lines Vision read from the whole region. Each line has: id (e.g. "A12"), box [x0,y0,x1,y1] in 0-1000 of that side's whole image, conf (Vision mean confidence 0-1) and w (the words: [wordId, text, word_conf]).
- candidates: id "F<n>", class (TEXT, NUMBER, CASE, PUNCT, MISSING_IN_B, EXTRA_IN_B, FILLER, FRACTION, PLACEHOLDER, CURVED ...), severity, and for each side "a" / "b": line (line id, or null when that side has no text there), diff (the differing characters AS VISION READ THEM), word, line_text, box, and crop. A candidate may have "members" (several differences on one curved emblem).

METHOD (follow it for EVERY candidate, in order)
1. LOOK FIRST. Before you read Vision's diff, word or line_text, look at the A crop (or the spot in IMAGE A) and read, character by character, what is actually PRINTED at the spot: letters, digits, letter case, punctuation, symbols. Read the whole word or number, not only the differing characters. Write it in a_seen.
2. Then, separately, look at the B crop (or the spot in IMAGE B) and read what is PRINTED there. Write it in b_seen.
3. a_seen and b_seen must come from the pixels only. Never copy Vision's text into them: Vision's reading is often wrong exactly at these spots (for example a bent emblem read as "5&-3" or "EATTY"). Read each image on its own. Never copy, complete or correct the reading of one image from the other image or from Vision's text. Version B may have been edited on purpose and the differences you are asked about are exactly the ones that matter most: a changed digit, a missing comma, one changed letter case, a plural "s". Never assume B equals A.
4. When a side has no text at that spot (missing / extra), look at the crop around the box and say whether the text is really absent from that image. Write "" in a_seen / b_seen only when nothing is printed there.
5. If a character is too small, blurred, cut by the edge of the crop or covered by graphics, write [?] for it. Never guess a character.
6. Only now compare a_seen with b_seen and choose the verdict. Vision's text may help you locate the spot, never to decide what is printed.

VERDICT
- "real": the PRINTED content differs between the two images at this spot: any letter, digit, letter case, word, decimal point, %, ®, ©, ™, unit, or punctuation that separates or changes content, or text present in one image and absent in the other. You must be able to read the differing characters clearly in BOTH images.
- "noise": both images print the SAME content at this spot, and the candidate exists only because Vision misread one or both images. Use it only when you can read the spot clearly in both images and they are identical.
- "uncertain": you cannot read the spot clearly in one or both images, the spot is curved or rotated text you cannot read with certainty, the box points at graphics or a logo you cannot read, or you are not sure. When in doubt, choose "uncertain".
- NOT a difference (verdict "noise" when this is the only change): line breaks and wrapping, a table row split differently, whitespace, the NUMBER of leader dots or dashes between a label and its value, ® / Ⓡ, © / Ⓒ, • / ·, ½ / 1/2 (the same glyph drawn differently).

OUTPUT
- reviews: exactly ONE entry for EVERY candidate, using its id in "candidate", in the order given. Never skip a candidate, never invent an id.
- items: always [] (this mode reviews the candidates only).
- reason: one or two Thai sentences: what you see in each image at that spot and why that makes it real / noise / uncertain. Keep label text in its original language inside quotes.
- suggestion: what the inspector should do for this point, in Thai.
- summary: 2-6 Thai sentences: how many candidates are real differences (each one briefly A -> B), how many are Vision misreads, and what still needs a human look.
- suggestions: concrete Thai actions for the inspector.
- Do not decide which version is correct; describe it as A: "..." / B: "...".
- Keep every field short. a_seen / b_seen: only the text at the spot, at most 80 characters. reason and suggestion: at most two short sentences each. Never repeat a character, word or phrase over and over; if you notice you are repeating yourself, stop that field and move on to the next candidate.
- Never output any number for confidence or accuracy. The app computes confidence from Vision.
- Respond with the raw JSON object only, matching the schema. No Markdown, no code fences, no text before or after it.
```
<!-- PROMPT_IMAGE END -->

### Prompt ของโหมดไม่เห็นข้อความ (`blind: true` · `ARTWORK_V2_AI_IMAGE_BLIND=1`)

⚠️ ต้องตรงกับ `const PROMPT_BLIND` ใน node "Build Gemini request" ทุกตัวอักษร (มีเทสต์เทียบ)

<!-- PROMPT_IMAGE_BLIND START -->
```text
You are a meticulous packaging-artwork proofreader (QC) reading printed text from images. You receive two versions of the same label region - version A and version B - and, for every candidate spot, one crop from each version. You are NOT given any text: no OCR result, no expected words, no hint of what changed. Read what is printed, character by character, from the pixels only. The application compares your two readings itself.

IMAGES
- For every candidate you receive a crop labelled "F<n> - A crop" (version A) and a crop labelled "F<n> - B crop" (version B), cut at the same spot of the two versions.
- DATA gives, for each candidate, a.box / b.box: the spot inside that side's crop as [x0,y0,x1,y1] in 0-1000 of the crop (x to the right, y down). The crop also shows neighbouring words and parts of the lines above and below; read only the spot.
- The two versions can have different sizes and scales. Always use each side's own box on its own crop.

METHOD (follow it for EVERY candidate, in order)
1. Look at the A crop. Read the complete word(s) or number(s) that the box covers or touches: every letter, digit, letter case, punctuation and symbol, including punctuation attached to the word (for example "Hwy," or "24%"). If the box covers part of a word, read the whole word. Do not add words that are outside the box. Write exactly what is printed in a_seen.
2. Then look at the B crop on its own and read the box in the same way, with the same extent. Write it in b_seen.
3. Read each crop on its own. Never copy, complete or correct the reading of one crop from the other crop. Version B may have been edited on purpose and the differences that matter most are tiny: a changed digit, a missing comma, one changed letter case, a plural "s", a changed accent. Never assume B equals A.
4. Write "" in a_seen / b_seen only when nothing at all is printed inside that box.
5. If a character is too small, blurred, cut by the edge of the crop, curved or covered by graphics, write [?] for that character. Never guess a character.
6. Only after both readings, compare them and choose the verdict.

VERDICT
- "real": a_seen and b_seen differ in any letter, digit, letter case, word, decimal point, %, ®, ©, ™, unit or punctuation.
- "noise": a_seen and b_seen are identical.
- "uncertain": you could not read the box clearly in one or both crops (you wrote [?]), or the box points at graphics, a logo or curved text you cannot read with certainty. When in doubt, choose "uncertain".
- Not a difference: whitespace, line breaks, the number of leader dots or dashes, ® / Ⓡ, © / Ⓒ, • / ·, ½ / 1/2.

OUTPUT
- reviews: exactly ONE entry for EVERY candidate, using its id in "candidate", in the order given. Never skip a candidate, never invent an id.
- a_seen / b_seen: the printed text only. No quotes, no labels such as "A:", no explanations.
- items: always [].
- reason: one or two Thai sentences: what you read in each crop and how they compare. Keep label text in its original language inside quotes.
- suggestion: what the inspector should check at this point, in Thai.
- summary: 2-6 Thai sentences: how many spots read differently (each one briefly A -> B), how many read the same, and what still needs a human look.
- suggestions: concrete Thai actions for the inspector.
- Do not decide which version is correct; describe it as A: "..." / B: "...".
- Keep every field short. a_seen / b_seen: only the text at the spot, at most 80 characters. reason and suggestion: at most two short sentences each. Never repeat a character, word or phrase over and over; if you notice you are repeating yourself, stop that field and move on to the next candidate.
- Never output any number for confidence or accuracy.
- Respond with the raw JSON object only, matching the schema. No Markdown, no code fences, no text before or after it.
```
<!-- PROMPT_IMAGE_BLIND END -->

| กติกา (blind) | ที่มา |
|---|---|
| ไม่มีข้อความใด ๆ จาก Vision ใน DATA | สถานี 9 ต.ค.: AI ลอกคำที่ Vision อ่านผิดไปเป็น `a_seen`/`b_seen` ทั้งที่ภาพเหมือนกัน |
| อ่าน **ทั้งคำ** ที่กรอบแตะ + เครื่องหมายที่ติดคำ · ขอบเขตเดียวกันทั้งสองฝั่ง | แอปเทียบสิ่งที่อ่านตรง ๆ — อ่านคนละขอบเขตจะกลายเป็น "ต่าง" ปลอม |
| `a_seen`/`b_seen` เป็นข้อความล้วน (ไม่มี `A:`/เครื่องหมายคำพูด) | แอปเอาไปเทียบตัวอักษรต่อตัวอักษร |
| แอปตัดสินเอง · AI ตอบขัดกับสิ่งที่อ่าน ⇒ ไม่แน่ใจ | กฎเหล็กข้อ 2 — ผลที่ผิดแบบมั่นใจแย่กว่าไม่แสดง |

### เหตุผลของกติกา

| กติกา | ที่มา |
|---|---|
| **ดูภาพก่อน** แล้วค่อยดูข้อความของ Vision · `a_seen`/`b_seen` ต้องมาจากพิกเซลเท่านั้น | สถานี 9 ต.ค.: `b_seen` = "OMEGA-6 5&-3 FATTY ACIDS" — ลอก "5&" ที่ Vision อ่านผิด |
| อ่านภาพ A ก่อน แล้วอ่านภาพ B **แยกกัน** · ห้ามเติม/แก้จากอีกภาพหรือจากข้อความ Vision · บอกตรง ๆ ว่า B อาจถูกแก้โดยตั้งใจ | 30 ก.ย.: Gemini ที่เห็นสองภาพลอก B ตาม A (`D-calcium` → `D-Calcium`) |
| ให้ตอบ `a_seen`/`b_seen` ก่อน `verdict` (`propertyOrdering`) | บังคับให้ "อ่าน" ก่อน "ตัดสิน" · ผู้ตรวจเห็นว่า AI เห็นอะไรจริง (แสดงใต้หมายเหตุ 🖼️) |
| อ่านไม่ชัด = `[?]` + `uncertain` · `noise` ต้องอ่านชัดทั้งสองภาพ | กฎเหล็กข้อ 2 — ผลที่ผิดแบบมั่นใจแย่กว่าไม่แสดง |
| ใช้กรอบของแต่ละฝั่งบนภาพของตัวเอง | ภาพ A/B ขนาด/สเกลไม่เท่ากัน (B มักเล็กกว่า ~10%) |
| ทุก candidate ต้องได้คำตอบ 1 ข้อ · `items` ว่าง | ผู้ใช้เลือก "ตรวจเฉพาะจุดที่อัลกอริทึมพบ" |
| ห้ามตอบตัวเลขความมั่นใจ | % มาจาก Vision เท่านั้น (กติกาเดียวกับทุกโหมด) |
| ทุกช่องสั้น (`a_seen`/`b_seen` ≤ 80 ตัว · เหตุผล ≤ 2 ประโยค) · ห้ามพิมพ์ซ้ำวน + เพดานคำตอบตามจำนวนจุด | สถานี 11 ต.ค.: 5 จุดตอบ **61,631 token** (ส่วนคิดแค่ 3,891) = วนพิมพ์ซ้ำ ชน `maxOutputTokens 65536` หลังรอ 187 วิ ⇒ AI ล้มทั้งคู่ · เพดานใหม่ทำให้ล้มใน ~10-30 วิ และ node Parse บอกจำนวน token + ท้ายคำตอบ |
