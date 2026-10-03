# Artwork V2 — Prompt ให้ Gemini ตรวจทานข้อความจาก Google Vision (ผ่าน N8N)

> ไฟล์ workflow: **`artwork_v2/n8n_artwork_v2_review.workflow.json`** (Import ใน N8N)
> webhook: `POST /webhook/artwork-v2-review` · แอปตั้งด้วย `ARTWORK_V2_AI_REVIEW_URL`
> (ค่าเริ่มต้น `http://127.0.0.1:5678/webhook/artwork-v2-review`)
> ⚠️ prompt ด้านล่าง **ต้องตรงกับ** `const PROMPT` ใน node "Build Gemini request" ทุกตัวอักษร
> (มีเทสต์ `test_doc_prompt_matches_workflow_prompt` เทียบให้) — แก้ที่หนึ่งต้องแก้อีกที่

## ① ลำดับการทำงาน (ห้ามส่งซ้ำ)

```
ภาพโซน A/B ──► Google Vision (ครั้งเดียว · 2 ภาพ)
                 │ บรรทัด + คำ + กรอบ + ความมั่นใจ
                 ▼
          อัลกอริทึมเทียบ (เดิม)
                 │
                 ▼  ข้อความ + รหัสคำ (ไม่มีภาพ · ไม่มีกุญแจ)
          N8N ─► Gemini 2.5 Flash  (1 คำขอต่อคู่โซน)
                 │ ตอบด้วย "รหัสคำ" ของ Vision เท่านั้น
                 ▼
          แอปตรวจคำตอบกับข้อมูล Vision ทุกข้อ → หากรอบ/คิด % เอง → แสดงผล
```

## ② ข้อมูลที่แอปส่งให้ N8N

```json
{
  "contract": "artwork-v2-review/1", "pair": 1, "mode": "assist",
  "zone_a": [{"id": "A33", "box": [55, 735, 742, 752], "conf": 0.98,
              "words": ["D-calcium", "Pantothenate,", "Thiamine"],
              "word_conf": [0.98, 0.99, 0.99]}],
  "zone_b": [ ... ],
  "candidates": [{"id": "F3", "class": "CASE", "severity": "red",
                  "a": {"line": "A33", "diff": "c", "word": "D-calcium", "line_text": "..."},
                  "b": {"line": "B30", "diff": "C", "word": "D-Calcium", "line_text": "..."}}]
}
```

* รหัสคำ = `<รหัสบรรทัด>:<ลำดับคำเริ่มที่ 0>` เช่น `A33:0` = `D-calcium`
* `box` = กรอบบรรทัดในโซน หน่วย 0-1000 (ให้ AI รู้ว่าอะไรอยู่แถวเดียวกัน/คอลัมน์ไหน)
* `word_conf` = ความมั่นใจต่ำสุดของตัวอักษรในคำนั้น **ตามที่ Vision บอก**
* `candidates` ส่งเฉพาะโหมด `assist` — โหมด `judge` ส่งรายการว่าง (ให้ AI หาเองอย่างอิสระ)

## ③ คำตอบที่ N8N ส่งกลับ (บังคับด้วย `responseSchema`)

```json
{
  "reviews": [{"candidate": "F3", "verdict": "real", "reason": "...", "suggestion": "..."}],
  "items": [{"a_words": ["A42:2"], "a_quote": "Breed", "b_words": ["B43:2"], "b_quote": "Breeds",
             "kind": "text", "verdict": "real", "reason": "...", "suggestion": "..."}],
  "summary": "...", "suggestions": ["..."],
  "engine": "gemini-2.5-flash", "usage": {...}
}
```

**ไม่มีช่องให้ AI ใส่ตัวเลขความมั่นใจเลย** (เทสต์ตรวจว่า schema ไม่มีคำว่า `confidence`)

## ④ แอปทำอะไรกับคำตอบ (ไม่เชื่อ AI ตรง ๆ)

| ตรวจ | ไม่ผ่าน ⇒ |
|---|---|
| รหัสคำต้องมีจริง · อยู่ฝั่งที่ถูก · บรรทัดเดียวต่อฝั่ง | ไม่ใช้ข้อนั้น + บันทึกเหตุผล |
| `a_quote`/`b_quote` ต้องตรงกับข้อความ Vision ของคำที่อ้าง | ไม่ใช้ (= AI อ้างรหัสผิด/แต่งข้อความ) |
| ข้อความที่อ้างสองฝั่งต้องต่างกันจริง (ไม่นับช่องว่าง) | ไม่ใช้ |

* **กรอบ** = แอปเทียบข้อความจริงของคำที่อ้างทีละตัวอักษร แล้วเอากรอบของ **ตัวอักษรที่ต่าง** จาก Vision
  (เช่น `D-calcium`/`D-Calcium` ได้กรอบแค่ `c`/`C` — เท่ากับกรอบของอัลกอริทึมทุกพิกเซล มีเทสต์บนข้อมูลจริง)
* **% ความมั่นใจของแต่ละจุด** = ค่าต่ำสุดของความมั่นใจ Vision ในตัวอักษรที่ต่าง ทั้งสองฝั่ง
* **% ความถูกต้องของการอ้างอิง** = ข้อที่ผ่านการตรวจข้างบน ÷ ข้อที่ AI ตอบทั้งหมด (ต่อคู่โซน)

## ⑤ สองโหมด (เลือกต่อรอบที่ช่อง "AI ตรวจทาน")

| | อัลกอริทึมตัดสิน + AI เสริม (`assist`) | AI ตัดสินหลัก (`judge` · ทดลอง) |
|---|---|---|
| AI เห็นผลอัลกอริทึม | เห็น (ตรวจทีละจุด) | ไม่เห็น |
| จุดแดงของอัลกอริทึม | **คงเดิมเสมอ** — AI แค่ติดความเห็น | ใช้ของ AI แทน |
| จุดที่ AI พบเพิ่ม | เหลือง "AI พบเพิ่ม" | AI บอกต่างจริง + Vision ≥ 80% = แดง · ไม่ถึง/ไม่แน่ใจ = เหลือง |
| AI บอก "สัญญาณรบกวน" | คงระดับเดิม + แสดงความเห็น | รายการพับ (ไม่นับ) |
| จุดของอัลกอริทึมที่ AI ไม่ระบุ | — | รายการพับ "อัลกอริทึมพบแต่ AI ไม่ได้ระบุ" + บอกในเหตุผลของผลตัดสิน |
| N8N ล่ม/ตอบผิดรูป | ผลอัลกอริทึมทั้งหมด + คำเตือน | ผลอัลกอริทึมทั้งหมด + คำเตือน |

## ⑥ ตั้งค่าใน N8N

1. Import `artwork_v2/n8n_artwork_v2_review.workflow.json`
2. node **HTTP Request**: คัด URL (project/region) และ credential `googleApi` จาก workflow ที่ใช้อยู่แล้ว
   (`artwork-pair`) — ค่าในไฟล์เป็นตัวอย่าง `YOUR_GCP_PROJECT_ID`
3. กด **Activate** (path `artwork-v2-review` · ใช้ Production URL ไม่ใช่ `/webhook-test/`)
4. ทดสอบ: ตรวจงานบนหน้า Artwork V2 → กล่อง "🤖 AI ตรวจทาน" ต้องขึ้นสถิติ ไม่ใช่ "ไม่สำเร็จ"

ค่าที่ใช้: `temperature 0` (ผลซ้ำได้) · `thinkingBudget 4096` (บทเรียนโหมดคู่: 1024 คิดไม่จบ) ·
`maxOutputTokens 32768` · `responseMimeType application/json` + `responseSchema`

## ⑦ Prompt (system instruction)

ภาษาอังกฤษเพราะโมเดลทำตามกติกาเชิงเทคนิคได้แม่นกว่า · บังคับให้ **ตอบเหตุผล/คำแนะนำเป็นภาษาไทย**

<!-- PROMPT START -->
```text
You are a meticulous packaging-artwork proofreader (QC). You receive the OCR output of Google Cloud Vision for the SAME region of two versions of a label: zone_a (version A) and zone_b (version B). You never see the images. Your ONLY source of truth is the JSON data given. Do not guess what the label "should" say, do not correct spelling, do not use outside knowledge to fill in or fix text.

DATA
- zone_a / zone_b: lines as Vision returned them. Each line has: id (e.g. "A12"), box [x0,y0,x1,y1] in 0-1000 of the zone image, conf (Vision mean confidence 0-1), words (list of strings), word_conf (Vision lowest character confidence of each word, 0-1, null = unknown).
- A word is referenced as "<lineId>:<wordIndex>", the 0-based index into that line's words. Example: line "A12" with words ["Crude","Fat","2.0%"] -> "A12:2" is "2.0%".
- candidates (assist mode only): differences already found by a rule-based algorithm. id "F<n>", class, severity, and for each side: line id, the differing characters (diff), the whole word, and the line text.

WHAT COUNTS AS A DIFFERENCE (report every one)
- any change of a letter, digit or letter case (D-Calcium vs D-calcium), spelling (Sulfate vs Sulphate), singular/plural (Breed vs Breeds), added or removed words or whole lines, units, decimal points (1.5 vs 15), %, ®, ©, ™, and punctuation that separates or changes content (a missing comma between address parts).

WHAT IS NOT A DIFFERENCE (never report)
- line breaks, wrapping, hyphenation at the end of a line, a table row split into pieces, a different order of lines or blocks. Compare the CONTENT of the two zones, not their line structure: a text that exists anywhere in the other zone is not missing.
- whitespace only.

KNOWN OCR NOISE OF GOOGLE VISION (use only together with what the data shows)
- the number of leader dots or dashes ("........") varies between reads.
- ® / Ⓡ, © / Ⓒ, • / ·, ½ / 1/2 are the same printed glyph.
- the fraction ½ is read unstably as "½", "1/2", "2", "1" or dropped, and "2" can come with HIGH confidence. A difference that only involves a fraction is "uncertain" - never "real", never "noise".
- stylised logo text, curved emblem text and lone symbols at the zone edge often have low word_conf.

VERDICT of each difference
- "real": the difference is in the content and the differing words are read clearly on both sides (word_conf >= 0.80).
- "noise": the difference is explained by the known OCR noise above AND the data supports it (low word_conf, leader dots, glyph variants). Never call a change of a letter, digit or letter case "noise" when the differing words have word_conf >= 0.80 on both sides.
- "uncertain": everything in between. When in doubt, choose "uncertain".

REFERENCING (the app draws the boxes from your ids and rejects anything that does not match the data)
- a_words / b_words: ids of the word(s) that contain the difference, all from ONE line per side, contiguous, as few as possible.
- If a word is missing inside a line on one side, give on that side the neighbouring word(s) around the place where it would be, so the position is clear.
- If the text is absent from the whole other zone, give [] for that side.
- a_quote / b_quote: the exact text of the cited words joined with single spaces, copied character for character from the data (keep dots and punctuation). "" when the list is empty.
- One item per separate difference. Two differences far apart in the same line are two items.
- Never output any number for confidence or accuracy. The app computes confidence from Vision.

MODE
- assist: first review EVERY candidate in "reviews" (candidate id, verdict, reason, suggestion). Then put in "items" ONLY differences that no candidate covers. Do not repeat a candidate in items.
- judge: candidates is empty. Find all differences yourself, put them in "items", and return "reviews": [].

LANGUAGE AND STYLE
- reason, suggestion, summary and suggestions are written in Thai. Keep label text in its original language inside quotes.
- Do not decide which version is correct; describe it as A: "..." / B: "...".
- reason: one or two sentences on why it is real / noise / uncertain, citing the evidence (word_conf, glyph variant, leader dots).
- suggestion: what the inspector should do for this point.
- summary: 2-6 Thai sentences: how many real differences, each one briefly A -> B, and what needs a human look.
- suggestions: concrete Thai actions for the inspector (what to check on the file or the physical print, how to redraw a zone).
- Respond with JSON only, matching the schema.
```
<!-- PROMPT END -->

### เหตุผลของแต่ละกติกา

| กติกา | ที่มา |
|---|---|
| ข้อมูลที่ส่งไปเป็นแหล่งความจริงเดียว · ห้ามแก้คำผิด | งานนี้คือ *หา* คำผิด — LLM ที่ "อ่านให้ถูก" จะกลืนของจริง (เกิดจริงในโหมดเทียบคู่เดิม: กลืน 4/4) |
| ไม่นับการตัดบรรทัด/แถวตาราง/ลำดับบรรทัด | แหล่งความต่าง ③ ที่วัดได้บนสถานี (แถว Phosphorus · `.294` ฯลฯ) |
| ® / Ⓡ · ½ / 1/2 · จุดไข่ปลา = สัญญาณรบกวนที่รู้จัก | แหล่งความต่าง ② ④ จากชุดข้อมูล 3 ชุดของสถานี |
| เศษส่วนเป็น `uncertain` เสมอ | วัดแล้ว: Vision อ่าน `½` เป็น `2` มั่นใจกว่าอ่านถูก (0.69-0.75 vs 0.29-0.38) |
| ห้ามเรียกตัวอักษร/ตัวเลข/ตัวพิมพ์ว่า noise เมื่อ Vision มั่นใจ ≥ 0.80 | กันผลผิดแบบมั่นใจ — `D-Calcium`/`D-calcium` คือของจริง (ยืนยัน 3 หลักฐานอิสระ) |
| อ้างด้วยรหัสคำ + ยกข้อความมาตรงตัว | แอปตรวจได้ว่า AI อ้างถูกคำ · กรอบมาจาก Vision ไม่ใช่จาก AI |
| ห้ามตอบตัวเลขความมั่นใจ | ผู้ใช้กำหนด: % ต้องมาจาก Vision |
| ไม่ตัดสินว่าฝั่งไหนถูก | V2 ไม่รู้ว่าไฟล์ไหนเป็นต้นแบบ — บอก A/B ตรง ๆ |
