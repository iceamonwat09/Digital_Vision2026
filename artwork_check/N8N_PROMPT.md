# การตั้งค่า N8N Workflow สำหรับโหมด Artwork Proof Check

> **ทางลัด:** มี workflow สำเร็จรูปพร้อม import ที่
> `n8n_artwork_ocr.workflow.json` (วิธีติดตั้งละเอียดอยู่ใน
> `N8N_WORKFLOW_SETUP.md`) — ข้อบังคับทั้ง 3 ข้อด้านล่างถูกฝังไว้ใน
> workflow นั้นแล้ว เอกสารนี้ยังจำเป็นเฉพาะเมื่อจะปรับ workflow ที่มีอยู่เดิมเอง

โหมดนี้ใช้ webhook เดิม (`N8N_OCR_WEBHOOK_URL`) และ contract เดิม
(`{"text": "...", "blocks": [...], "engine": "..."}`) — **ไม่ต้องสร้าง
workflow ใหม่** แต่ต้องปรับ node Gemini ตาม 3 ข้อบังคับนี้ ไม่งั้นระบบ
จะมองไม่เห็น typo ที่กำลังตามหา:

## 1. temperature = 0

งานถอดความ (transcription) ต้อง deterministic — ตั้งใน Generation Config
ของ node Gemini:

```json
{"temperature": 0, "topP": 1}
```

## 2. Prompt ห้ามแก้คำสะกดเด็ดขาด

LLM มีนิสัยแก้คำผิดให้เองเงียบๆ (เห็น "caliddd" แล้วถอดเป็น "calidad")
ซึ่งจะซ่อนความผิดที่โหมดนี้มีหน้าที่จับ ใช้ prompt นี้:

```
You are a verbatim transcription engine for printed packaging artwork.
Transcribe ALL text visible in the image EXACTLY as printed,
character-by-character, in every language present (English, Spanish,
Arabic, Thai, ...).

STRICT RULES:
- DO NOT correct spelling, grammar, or punctuation. If a word looks
  misspelled, transcribe the misspelling exactly as printed.
- DO NOT add, omit, translate, or normalize any character.
- Preserve digits, units, and punctuation exactly (e.g. "16.12 LBS.",
  "(7.31 kg)", "¡...!").
- Keep reading order top-to-bottom, left-to-right; right-to-left for
  Arabic. One text element per line.
- If a region is too small/blurry to read with confidence, transcribe
  what you can and lower the confidence for that block. Never guess.

Return ONLY JSON:
{
  "text": "<all text, one element per line>",
  "blocks": [
    {"text": "<element>", "bbox": [x, y, w, h], "conf": 0.0-1.0}
  ],
  "engine": "gemini-2.5-flash"
}
```

## 3. เปิด Structured Output (responseSchema)

บังคับให้ Gemini ตอบเป็น JSON ตาม schema เสมอ ป้องกัน parser ฝั่ง Python
ต้องเดา format:

```json
{
  "type": "object",
  "properties": {
    "text":   {"type": "string"},
    "blocks": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "text": {"type": "string"},
          "bbox": {"type": "array", "items": {"type": "number"}},
          "conf": {"type": "number"}
        },
        "required": ["text", "conf"]
      }
    },
    "engine": {"type": "string"}
  },
  "required": ["text"]
}
```

## หมายเหตุ

- โหมด Artwork ส่งภาพ **ทีละโซน** ที่ 450 DPI (ตั้งได้ผ่าน
  `ARTWORK_OCR_DPI`) — ภาพต่อ request เล็กกว่าโหมด Label Paper
  แต่จำนวน request ต่อไฟล์มากกว่า (ตามจำนวนโซน)
- ถ้าต้องการแยก prompt ระหว่างโหมด Label เดิมกับโหมด Artwork ให้สร้าง
  webhook path ใหม่ใน N8N แล้วชี้ env `N8N_OCR_WEBHOOK_URL` ของ
  เครื่องที่รันโหมด Artwork ไปที่ path นั้น (โค้ดไม่ต้องแก้)
- `conf` ต่อ block สำคัญ: โซนที่ conf เฉลี่ย < 0.5 จะถูกรายงานเป็น
  UNREADABLE (ขอให้คนดูเอง) แทนที่จะฟ้องผิดมั่ว
