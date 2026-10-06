# Artwork V2 — Prompt ให้ Gemini ตรวจทานข้อความจาก Google Vision (ผ่าน N8N)

> ไฟล์ workflow: **`artwork_v2/n8n_artwork_v2_review.workflow.json`** (Import ใน N8N)
> webhook: `POST /webhook/artwork-v2-review` · แอปตั้งด้วย `ARTWORK_V2_AI_REVIEW_URL`
> (ค่าเริ่มต้น `http://127.0.0.1:5678/webhook/artwork-v2-review`)
> ⚠️ prompt ด้านล่าง **ต้องตรงกับ** `const PROMPT` (และ `const PROMPT_RAW` ของโหมดข้อมูลดิบ)
> ใน node "Build Gemini request" ทุกตัวอักษร
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
* `"curved": true` = บรรทัดข้อความโค้ง/เอียง (แอปตัดสินจากมุมของ Vision · `ARTWORK_V2_AI_SEND_CURVED`)

**สิ่งที่ Gemini เห็นจริง (node "Build Gemini request" แปลงให้ — 6 ต.ค.):** แต่ละบรรทัดเป็น
`{"id":"A33","box":[...],"conf":0.98,"w":[["A33:0","D-calcium",0.98],["A33:1","Pantothenate,",0.99]]}`
⇒ Gemini **คัดรหัสคำที่เขียนไว้แล้ว** ไม่ต้องนับลำดับคำเอง (ผลจริง 6 ต.ค.: นับคลาด 1 คำในบรรทัดยาว
`Copper Sulphate` อ้าง `A32:7,8` แทน `A32:6,7` ⇒ แอปปฏิเสธ ⇒ โหมด judge พลาด Sulphate/Park)
· แอปยังส่ง `words`/`word_conf` แบบเดิม (สัญญาเดิม) — node แปลงเอง

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
| รหัสคำคลาด แต่ `a_quote`/`b_quote` ถูก (`ARTWORK_V2_AI_QUOTE_RECOVER`) | แอปหาคำจากข้อความที่ยกมา **ในบรรทัดที่อ้างเท่านั้น** — ตรงทั้งคำ · ห่าง ≤ 2 คำ · ตำแหน่งเดียว (หรือเป็นส่วนของคำที่อ้าง เจอครั้งเดียว) · กำกวม/ไม่เจอ ⇒ ไม่ใช้ |
| สองฝั่งเท่ากันตามกติกาเทียบของระบบ (ช่องว่าง · จุดไข่ปลา · ®/Ⓡ · ½/1/2) (`ARTWORK_V2_AI_EQUIV_NOISE`) | นับเป็นสัญญาณรบกวน (อ้างถูก — ไม่หักความถูกต้องของการอ้างอิง · ไม่เป็นจุด) |

* **กรอบ** = แอปเทียบข้อความจริงของคำที่อ้างทีละตัวอักษร แล้วเอากรอบของ **ตัวอักษรที่ต่าง** จาก Vision
  (เช่น `D-calcium`/`D-Calcium` ได้กรอบแค่ `c`/`C` — เท่ากับกรอบของอัลกอริทึมทุกพิกเซล มีเทสต์บนข้อมูลจริง)
* **% ความมั่นใจของแต่ละจุด** = ค่าต่ำสุดของความมั่นใจ Vision ในตัวอักษรที่ต่าง ทั้งสองฝั่ง
* **% ความถูกต้องของการอ้างอิง** = ข้อที่ผ่านการตรวจข้างบน ÷ ข้อที่ AI ตอบทั้งหมด (ต่อคู่โซน)

## ⑤ โหมด (เลือกต่อรอบที่ช่อง "AI ตรวจทาน")

| | อัลกอริทึมตัดสิน + AI เสริม (`assist`) | AI ตัดสินหลัก (`judge` · ทดลอง) |
|---|---|---|
| AI เห็นผลอัลกอริทึม | เห็น (ตรวจทีละจุด) | ไม่เห็น |
| จุดแดงของอัลกอริทึม | **คงเดิมเสมอ** — AI แค่ติดความเห็น | ใช้ของ AI แทน |
| จุดที่ AI พบเพิ่ม | เหลือง "AI พบเพิ่ม" | AI บอกต่างจริง + Vision ≥ 80% = แดง · ไม่ถึง/ไม่แน่ใจ = เหลือง |
| AI บอก "สัญญาณรบกวน" | คงระดับเดิม + แสดงความเห็น | รายการพับ (ไม่นับ) — **ยกเว้น** ตัวอักษร/ตัวเลข/ตัวพิมพ์ที่ Vision อ่านชัด ≥ 80% ทั้งสองฝั่ง (ไม่ใช่ข้อความโค้ง) ⇒ เหลือง (`ARTWORK_V2_AI_JUDGE_NOISE_GUARD`) |
| AI บอก "ต่างจริง" บนข้อความโค้ง | — | เหลือง (แดงได้เฉพาะเมื่ออ่านซ้ำยืนยัน · `ARTWORK_V2_AI_JUDGE_CURVED_YELLOW`) |
| จุดของอัลกอริทึมที่ AI ไม่ระบุ | — | **จุดแดง** ⇒ คงไว้ในตารางเป็นเหลือง (`ARTWORK_V2_AI_JUDGE_KEEP_ALGO_RED`) · จุดเหลือง ⇒ รายการพับ "อัลกอริทึมพบแต่ AI ไม่ได้ระบุ" + บอกในเหตุผลของผลตัดสิน |
| N8N ล่ม/ตอบผิดรูป | ผลอัลกอริทึมทั้งหมด + คำเตือน | ผลอัลกอริทึมทั้งหมด + คำเตือน |

### ⑤-ข โหมด "AI ตัดสินจากข้อมูลดิบ" (`raw` · ทดลอง · 6 ต.ค.)

ผู้ใช้สั่ง: *"ส่งข้อมูลดิบจริง ๆ ไม่ผ่านอัลกอริทึมเลย ให้ Gemini ตรวจ · ห้ามเดา ค่อย ๆ คิด ·
มาพร้อมตำแหน่งและ % เหมือนเดิม"*

| | ทำอย่างไร |
|---|---|
| ข้อมูลที่ส่ง | **บรรทัดตามที่ Vision ส่ง** (ประกอบจาก `detectedBreak` เท่านั้น) — ไม่ต่อแถวตาราง · ไม่ต่อคำตัดท้ายบรรทัด · ไม่มีธงโค้ง · ไม่มี `candidates` |
| prompt | `PROMPT_RAW` (ด้านล่าง) — บอกตรง ๆ ว่าข้อมูลยังไม่ถูกจัด (แถวเดียวกันอาจแตกเป็นหลายบรรทัด) · ไล่ทีละขั้น A→B แล้ว B→A · ยืนยันทีละตัวอักษรก่อนเขียน · ไม่แน่ใจ = `uncertain` · ไม่พบ = ตอบว่าง |
| เวลาคิด | `thinkingBudget 24576` (สูงสุดของ 2.5 Flash) · `maxOutputTokens 65536` · แอปรอ 300 วิ (`ARTWORK_V2_AI_RAW_TIMEOUT_S`) · node HTTP 290 วิ |
| กรอบ / % | เหมือนทุกโหมด — แอปหากรอบตัวอักษรที่ต่างจาก Vision เอง · % = ความมั่นใจต่ำสุดของ Vision |
| ระดับ | ต่างจริง + Vision ≥ 80% = แดง · ไม่ถึง/ไม่แน่ใจ = เหลือง · สัญญาณรบกวน = รายการพับ |
| กติกาความปลอดภัย (`ARTWORK_V2_AI_RAW_SAFETY`, `0` = AI ล้วน) | อิงหลักฐานของ Vision เท่านั้น (ไม่ใช้ผลอัลกอริทึม): เครื่องหมายวรรคตอนล้วน ⇒ เหลือง · หายไป/เกินมาที่สั้น < 2 ตัวหรือเครื่องหมายล้วน ⇒ เหลือง · AI บอก noise ทั้งที่ Vision อ่านตัวอักษร/ตัวเลขชัด ⇒ เหลือง |
| ผลของอัลกอริทึม | **ทุกจุด** ไปอยู่ในรายการพับ "ไว้เทียบ" (ไม่นับ) · ความครอบคลุมไม่ใช้ตัดสิน (เป็นตัวชี้วัดของอัลกอริทึม) |
| N8N ล่ม/ตอบผิดรูป | ผลอัลกอริทึมทั้งหมด + คำเตือน (เหมือนทุกโหมด) |

## ⑥ ตั้งค่าใน N8N

1. Import `artwork_v2/n8n_artwork_v2_review.workflow.json`
2. node **HTTP Request**: คัด URL (project/region) และ credential `googleApi` จาก workflow ที่ใช้อยู่แล้ว
   (`artwork-pair`) — ค่าในไฟล์เป็นตัวอย่าง `YOUR_GCP_PROJECT_ID`
3. กด **Activate** (path `artwork-v2-review` · ใช้ Production URL ไม่ใช่ `/webhook-test/`)
4. ทดสอบ: ตรวจงานบนหน้า Artwork V2 → กล่อง "🤖 AI ตรวจทาน" ต้องขึ้นสถิติ ไม่ใช่ "ไม่สำเร็จ"
5. ทดสอบตรงจาก PowerShell (ไม่ต้องเปิดหน้าเว็บ — ต้องได้ JSON ที่มี `reviews`/`items` หรือ `error` ที่อ่านออก):
   ```powershell
   $b = '{"mode":"judge","zone_a":[{"id":"A0","box":[0,0,1000,100],"conf":0.98,"words":["Sodium","20%"],"word_conf":[0.98,0.97]}],"zone_b":[{"id":"B0","box":[0,0,1000,100],"conf":0.98,"words":["Sodium","24%"],"word_conf":[0.98,0.96]}],"candidates":[]}'
   Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5678/webhook/artwork-v2-review -ContentType 'application/json; charset=utf-8' -Body $b | ConvertTo-Json -Depth 6
   ```
   คาดหวัง: `items` 1 ข้อ `a_words ["A0:1"]` · `b_words ["B0:1"]` · `verdict "real"`

กันเงียบ: node HTTP Request ตั้ง **`neverError`** (HTTP ≠ 2xx ไม่ล้ม workflow) และ **On Error =
Continue** (ต่อไม่ติด/หมดเวลา 290 วิ ไม่ล้ม workflow) ⇒ ทุกความล้มเหลวไปจบที่ node Parse ซึ่งคืน
`{error}` เสมอ ⇒ แอปเห็นเหตุผลจริงใน Log และใช้ผลอัลกอริทึมแทน (หมดเวลาของ N8N 290 วิ < ของแอปโหมด raw
300 วิ · โหมด assist/judge แอปเลิกรอที่ 180 วิเหมือนเดิม)

ค่าที่ใช้: `temperature 0` (ผลซ้ำได้) · `thinkingBudget 8192` (6 ต.ค.: 4096 ถูกใช้หมด 4092/4095 บนโซนจริง
70 บรรทัด — คิดไม่ครบทุกบรรทัด · บทเรียนโหมดคู่: 1024 คิดไม่จบ) ·
`maxOutputTokens 32768` · `responseMimeType application/json` + `responseSchema`

## ⑦ Prompt (system instruction)

ภาษาอังกฤษเพราะโมเดลทำตามกติกาเชิงเทคนิคได้แม่นกว่า · บังคับให้ **ตอบเหตุผล/คำแนะนำเป็นภาษาไทย**

<!-- PROMPT START -->
```text
You are a meticulous packaging-artwork proofreader (QC). You receive the OCR output of Google Cloud Vision for the SAME region of two versions of a label: zone_a (version A) and zone_b (version B). You never see the images. Your ONLY source of truth is the JSON data given. Do not guess what the label "should" say, do not correct spelling, do not use outside knowledge to fill in or fix text.

DATA
- zone_a / zone_b: lines as Vision returned them. Each line has: id (e.g. "A12"), box [x0,y0,x1,y1] in 0-1000 of the zone image, conf (Vision mean confidence 0-1), w (the words of the line), and "curved": true when the line is curved or tilted text (an emblem or a badge).
- Each entry of w is [wordId, text, word_conf]: wordId is the ready-made reference of that word, text is the word exactly as Vision read it, word_conf is Vision's lowest character confidence in that word (0-1, null = unknown). Example: line "A12" with w [["A12:0","Crude",0.99],["A12:1","Fat",0.98],["A12:2","2.0%",0.97]] -> the word "2.0%" is "A12:2".
- candidates (assist mode only): differences already found by a rule-based algorithm. id "F<n>", class, severity, and for each side: line id, the differing characters (diff), the whole word, and the line text.

WHAT COUNTS AS A DIFFERENCE (report every one)
- any change of a letter, digit or letter case (D-Calcium vs D-calcium), spelling (Sulfate vs Sulphate), singular/plural (Breed vs Breeds), added or removed words or whole lines, units, decimal points (1.5 vs 15), %, ®, ©, ™, and punctuation that separates or changes content (a missing comma between address parts).
- Compare every pair of matching lines word by word from the first word to the last. A changed or missing word in the middle or at the end of a long line (an ingredient list, an address) is the easiest one to miss.

WHAT IS NOT A DIFFERENCE (never report it, not even as noise)
- line breaks, wrapping, hyphenation at the end of a line, a table row split into pieces, a different order of lines or blocks. Compare the CONTENT of the two zones, not their line structure: a text that exists anywhere in the other zone is not missing.
- whitespace only, including a space before or after a symbol ("Acid*" vs "Acid *").
- the number of leader dots or dashes between a label and its value ("(min)...... ..7.0%" vs "(min).. ..7.0%"). Vision never counts them the same way twice.
- ® / Ⓡ, © / Ⓒ, • / ·, ½ / 1/2: the same printed glyph.
- Before writing an item, check that a_quote and b_quote differ in something other than the cases above. If they do not, drop the item.

KNOWN OCR NOISE OF GOOGLE VISION (use only together with what the data shows)
- the fraction ½ is read unstably as "½", "1/2", "2", "1" or dropped, and "2" can come with HIGH confidence. A difference that only involves a fraction is "uncertain" - never "real", never "noise".
- stylised logo text, curved emblem text (lines with "curved": true), barcode digits and lone symbols or digits near the zone edge often have low word_conf and are read differently each time.

VERDICT of each difference
- Before choosing, look up the word_conf of EVERY cited word on both sides.
- "real": the difference is in the content and EVERY cited word on both sides has word_conf >= 0.80. If even one cited word is below 0.80 or null, the verdict cannot be "real".
- "noise": the difference is explained by the known OCR noise above AND the data supports it (low word_conf, glyph variants, curved text). Never call a change of a letter, digit or letter case "noise" when the differing words have word_conf >= 0.80 on both sides.
- "uncertain": everything in between. When in doubt, choose "uncertain".
- a difference on a line with "curved": true is at most "uncertain", whatever its word_conf.
- punctuation only (a period, comma, colon, semicolon or hyphen added or removed, every letter and digit the same): "real" when the words that carry it have word_conf >= 0.80 on both sides, otherwise "uncertain". It is "noise" only when it is one of the known OCR noises above (glyph variants, stylised logo text, curved text) - low word_conf alone is not enough.

REFERENCING (the app draws the boxes from your ids and rejects anything that does not match the data)
- a_words / b_words: copy the wordId of each cited word from w exactly as it is written. Never count word positions yourself and never build an id. All ids of one side come from ONE line, contiguous, as few as possible.
- a_quote / b_quote: the text values of exactly those w entries joined with single spaces, copied character for character (keep dots and punctuation). A wordId always means the whole word: never quote only a part of a word. "" when the list is empty.
- If a word is missing inside a line on one side, anchor it with exactly ONE neighbouring word that exists on BOTH sides: the word right before the gap (the word right after it only when there is none before). On the side that HAS the text, cite that neighbour together with the differing word(s); on the side that lacks it, cite only that neighbour. Example: A "Pantothenate, D-Calcium Thiamine" and B "Pantothenate, Thiamine" -> a_words = the ids of "Pantothenate," and "D-Calcium", b_words = the id of "Pantothenate,". Never cite the neighbour alone on one side and the differing word alone on the other: the app would then frame the neighbour as if it were the changed word.
- If the text is absent from the whole other zone, give [] for that side.
- One item per separate difference. Two differences far apart in the same line are two items. Never report the same difference twice.
- Never output any number for confidence or accuracy. The app computes confidence from Vision.

MODE (take it from the field "mode" of the data, never infer it from anything else)
- assist: first review EVERY candidate in "reviews" (candidate id, verdict, reason, suggestion). Then put in "items" ONLY differences that no candidate covers. Do not repeat a candidate in items. If candidates is empty, return "reviews": [] and put every difference you find in "items".
- judge: candidates is always empty. Find all differences yourself, put them in "items", and return "reviews": [].

LANGUAGE AND STYLE
- reason, suggestion, summary and suggestions are written in Thai. Keep label text in its original language inside quotes.
- Do not decide which version is correct; describe it as A: "..." / B: "...".
- reason: one or two sentences on why it is real / noise / uncertain, citing the evidence in words (clear or low Vision confidence, glyph variant, curved text). Do not write confidence numbers, and never call a reading clear or highly confident when a cited word is below 0.80.
- suggestion: what the inspector should do for this point.
- summary: 2-6 Thai sentences: how many real differences, each one briefly A -> B, and what needs a human look.
- suggestions: concrete Thai actions for the inspector (what to check on the file or the physical print, how to redraw a zone).
- Respond with the raw JSON object only, matching the schema. No Markdown, no code fences, no text before or after it.
```
<!-- PROMPT END -->

### Prompt ของโหมดข้อมูลดิบ (`PROMPT_RAW`)

<!-- PROMPT_RAW START -->
```text
You are a meticulous packaging-artwork proofreader (QC). You receive the RAW OCR output of Google Cloud Vision for the SAME region of two versions of a label: zone_a (version A) and zone_b (version B). Nothing has been pre-processed: no line was joined, merged, re-ordered or compared for you. You never see the images. Your ONLY source of truth is the JSON data given. Do not guess what the label "should" say, do not correct spelling, do not use outside knowledge to fill in or fix text. Accuracy matters more than speed: work slowly and verify every claim against the data before you write it.

DATA
- zone_a / zone_b: lines exactly as Vision returned them, in Vision's reading order. Each line has: id (e.g. "A12"), box [x0,y0,x1,y1] in 0-1000 of the zone image, conf (Vision mean confidence 0-1) and w (the words of the line).
- Each entry of w is [wordId, text, word_conf]: wordId is the ready-made reference of that word, text is the word exactly as Vision read it, word_conf is Vision's lowest character confidence in that word (0-1, null = unknown). Example: line "A12" with w [["A12:0","Crude",0.99],["A12:1","Fat",0.98],["A12:2","2.0%",0.97]] -> the word "2.0%" is "A12:2".
- Because the data is raw, the SAME printed text is often split differently in the two zones: a table row may be one line in A and two or three pieces in B (label, leader dots and value as separate lines), a long sentence may wrap at a different word, a word may be hyphenated at the end of a line, and blocks may come in a different order. None of this is a difference.

METHOD (follow every step, in this order)
1. Read all of zone_a and all of zone_b first.
2. For every line of zone_a, find where the same content is in zone_b: on any line, in any order, possibly spread over several lines or sharing a line with other text. Use box positions only as a hint for what sits on the same row or column.
3. Compare the matched content word by word, character by character, from the first word to the last. A changed or missing word in the middle or at the end of a long line (an ingredient list, an address) is the easiest one to miss.
4. Then do the reverse: every line of zone_b must be found in zone_a the same way. Text that exists in only one zone is a difference only after you have searched the whole other zone for it.
5. Before writing an item, re-read a_quote and b_quote character by character and confirm they really differ in something other than the cases in WHAT IS NOT A DIFFERENCE. If you cannot confirm it from the data, do not write it as "real".

WHAT COUNTS AS A DIFFERENCE (report every one)
- any change of a letter, digit or letter case (D-Calcium vs D-calcium), spelling (Sulfate vs Sulphate), singular/plural (Breed vs Breeds), added or removed words or whole lines, units, decimal points (1.5 vs 15), %, ®, ©, ™, and punctuation that separates or changes content (a missing comma between address parts).

WHAT IS NOT A DIFFERENCE (never report it, not even as noise)
- line breaks, wrapping, hyphenation at the end of a line, a table row split into pieces, a different order of lines or blocks. Compare the CONTENT of the two zones, not their line structure: a text that exists anywhere in the other zone is not missing.
- whitespace only, including a space before or after a symbol ("Acid*" vs "Acid *").
- the number of leader dots or dashes between a label and its value ("(min)...... ..7.0%" vs "(min).. ..7.0%"), and leader dots that are a separate line in one zone. Vision never counts them the same way twice.
- ® / Ⓡ, © / Ⓒ, • / ·, ½ / 1/2: the same printed glyph.

KNOWN OCR NOISE OF GOOGLE VISION (use only together with what the data shows)
- the fraction ½ is read unstably as "½", "1/2", "2", "1" or dropped, and "2" can come with HIGH confidence. A difference that only involves a fraction is "uncertain" - never "real", never "noise".
- stylised logo text, curved or tilted emblem text, barcode digits and lone symbols or digits near the zone edge often have low word_conf and are read differently each time. A line whose words are much lower in confidence than its neighbours, or whose box is tilted or out of the reading flow, is a warning sign.

VERDICT of each difference
- Before choosing, look up the word_conf of EVERY cited word on both sides.
- "real": the difference is in the content, you confirmed it in step 5, and EVERY cited word on both sides has word_conf >= 0.80. If even one cited word is below 0.80 or null, the verdict cannot be "real".
- "noise": the difference is explained by the known OCR noise above AND the data supports it (low word_conf, glyph variants). Never call a change of a letter, digit or letter case "noise" when the differing words have word_conf >= 0.80 on both sides.
- "uncertain": everything in between. When in doubt, choose "uncertain". Never guess.
- punctuation only (a period, comma, colon, semicolon or hyphen added or removed, every letter and digit the same): "real" when the words that carry it have word_conf >= 0.80 on both sides, otherwise "uncertain". It is "noise" only when it is one of the known OCR noises above - low word_conf alone is not enough.

REFERENCING (the app draws the boxes from your ids and rejects anything that does not match the data)
- a_words / b_words: copy the wordId of each cited word from w exactly as it is written. Never count word positions yourself and never build an id. All ids of one side come from ONE line, contiguous, as few as possible. When the content is split over several lines, cite only the line that holds the differing word.
- a_quote / b_quote: the text values of exactly those w entries joined with single spaces, copied character for character (keep dots and punctuation). A wordId always means the whole word: never quote only a part of a word. "" when the list is empty.
- If a word is missing inside a line on one side, anchor it with exactly ONE neighbouring word that exists on BOTH sides: the word right before the gap (the word right after it only when there is none before). On the side that HAS the text, cite that neighbour together with the differing word(s); on the side that lacks it, cite only that neighbour. Example: A "Pantothenate, D-Calcium Thiamine" and B "Pantothenate, Thiamine" -> a_words = the ids of "Pantothenate," and "D-Calcium", b_words = the id of "Pantothenate,". Never cite the neighbour alone on one side and the differing word alone on the other: the app would then frame the neighbour as if it were the changed word.
- If the text is absent from the whole other zone, give [] for that side.
- One item per separate difference. Two differences far apart in the same line are two items. Never report the same difference twice.
- Never output any number for confidence or accuracy. The app computes confidence from Vision.

OUTPUT
- Put every difference in "items" and return "reviews": [].
- If you find no difference, return "items": [] and say so in the summary. An empty answer is correct when the zones match; never invent an item to have something to report.

LANGUAGE AND STYLE
- reason, suggestion, summary and suggestions are written in Thai. Keep label text in its original language inside quotes.
- Do not decide which version is correct; describe it as A: "..." / B: "...".
- reason: one or two sentences on why it is real / noise / uncertain, citing the evidence in words (clear or low Vision confidence, glyph variant, split line). Do not write confidence numbers, and never call a reading clear or highly confident when a cited word is below 0.80.
- suggestion: what the inspector should do for this point.
- summary: 2-6 Thai sentences: how many real differences, each one briefly A -> B, and what needs a human look.
- suggestions: concrete Thai actions for the inspector (what to check on the file or the physical print, how to redraw a zone).
- Respond with the raw JSON object only, matching the schema. No Markdown, no code fences, no text before or after it.
```
<!-- PROMPT_RAW END -->

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
| คำหายกลางบรรทัด: อ้าง **คำข้างเคียง 1 คำที่มีทั้งสองฝั่ง** ทั้งสองฝั่ง | แอปหาจุดต่างเองทีละตัวอักษร — อ้างคำข้างเคียงฝั่งเดียว (ฝั่งที่ขาด) แอปจะกรอบ **คำข้างเคียงว่าเป็นคำที่เปลี่ยน** (วัดแล้ว: กรอบ `Pantothenate,` แทนจุดแทรก) · อ้างคำเดียวกันทั้งสองฝั่ง ⇒ ได้จุดแทรกที่ถูกตำแหน่ง (มีเทสต์) |
| เครื่องหมายล้วน: มั่นใจ ≥ 0.80 = `real` · ต่ำกว่า = `uncertain` (ไม่ใช่ `noise`) | ความมั่นใจต่ำแปลว่า "ไม่รู้" ไม่ใช่ "ไม่มี" — `noise` ในโหมด judge จะพับรายการทิ้ง |
| โหมดอ่านจากช่อง `mode` ไม่เดาจาก `candidates` | assist ที่อัลกอริทึมไม่พบอะไรเลยก็มี `candidates` ว่าง — สองโหมดใช้ผลต่างกันในแอป |
| ให้ **รหัสคำสำเร็จรูป** ใน `w` · ห้ามนับ/สร้างรหัสเอง · ยกทั้งคำ | ผลจริง 6 ต.ค.: Gemini นับคลาด 1 คำในบรรทัดยาว (Sulphate · Park) และยกจุดไข่ปลาครึ่งคำ (`..........` จาก `(min)..........`) ⇒ ถูกปฏิเสธ 7/19 และ 9/20 ข้อ |
| จุดไข่ปลา/ช่องว่าง/อักษรสมมูล = **ไม่ใช่ความต่างเลย** (ไม่รายงานแม้เป็น noise) | 6 ต.ค.: AI รายงาน 6-9 ข้อต่อรอบเป็นจุดไข่ปลา (เปลืองงบคิด · ดันความถูกต้องของการอ้างอิงลง) |
| `real` ต้อง **ทุกคำที่อ้าง** ≥ 0.80 · บรรทัด `curved` อย่างมากแค่ `uncertain` | 6 ต.ค.: AI ตอบ `real` + เขียนว่า "มั่นใจสูง" ให้ `-3 FATTY` (0.51) และบาร์โค้ด (0.415) — แอปกันไว้ด้วย % จาก Vision อยู่แล้ว แต่เหตุผลที่ผู้ตรวจอ่านต้องไม่โกหก |
| ไล่บรรทัดยาวทีละคำจนจบบรรทัด | ของที่หลุดบ่อยคือคำกลาง/ท้ายบรรทัดยาว (ส่วนผสม · ที่อยู่) |
| ห้าม Markdown/รั้วโค้ด | `responseMimeType` บังคับ JSON อยู่แล้ว · node Parse ถอดรั้วให้อีกชั้น — ประโยคนี้เป็นชั้นที่สาม |

**คำแนะนำที่พิจารณาแล้วไม่ทำ:** ใส่ตัวอย่าง JSON schema ท้าย prompt — Google ระบุว่าเมื่อใช้
`responseSchema` แล้ว **ไม่ควรใส่ schema ซ้ำใน prompt เพราะอาจทำให้คุณภาพคำตอบลดลง** และ
`responseSchema` บังคับชื่อ key/ชนิด/enum/required ที่ฝั่ง Gemini อยู่แล้ว (เดาชื่อ key ผิดไม่ได้)
