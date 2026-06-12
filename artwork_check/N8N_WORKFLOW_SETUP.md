# คู่มือ Import + ตั้งค่า N8N Workflow "Artwork OCR — Gemini Verbatim Transcription"

ไฟล์ workflow พร้อม import: **`n8n_artwork_ocr.workflow.json`** (อยู่ในโฟลเดอร์นี้)

โครงสร้างตรงกับ workflow เดิมของคุณทุก node:

```
Webhook ──> Code in JavaScript2 ──> If ──true──> HTTP Request (Gemini) ──> Code in JavaScript ──> Respond to Webhook
                                       └─false─> Respond to Webhook1 (HTTP 400)
```

| Node | หน้าที่ |
|---|---|
| **Webhook** | รับ `POST` form field `image_b64` จากแอป (path: `artwork-ocr`) |
| **Code in JavaScript2** | ตรวจ base64 / ชนิดภาพ (JPEG/PNG/WebP จาก magic bytes) / ขนาด แล้วประกอบ request body ของ Gemini ทั้งก้อน (prompt + temperature 0 + responseSchema) |
| **If** | `valid == true` ? |
| **HTTP Request** | ยิง `generateContent` ไป Gemini (Vertex AI, retry อัตโนมัติ 2 ครั้ง, timeout 120s) |
| **Code in JavaScript** | แกะคำตอบ Gemini → `{text, blocks, engine}` ตาม contract ของแอป (กัน code fence, กัน non-JSON, โยน error เมื่อ Gemini ตอบ error) |
| **Respond to Webhook** | ตอบ 200 + JSON ให้แอป |
| **Respond to Webhook1** | ตอบ 400 + `{error: "..."}` เมื่อ input ใช้ไม่ได้ |

---

## ขั้นตอนที่ 1 — Import

1. เปิด N8N → เมนูซ้ายบน **Workflows** → ปุ่ม **Create Workflow** ▾ →
   **Import from File...** (หรือเปิด workflow ว่างแล้วกด `⋮` มุมขวาบน →
   Import from File)
2. เลือกไฟล์ `n8n_artwork_ocr.workflow.json`
3. จะได้ 7 nodes วางเรียงตามผังด้านบน — **อย่าเพิ่ง Activate** ต้องตั้ง
   credential และแก้ URL ก่อน (ขั้นตอนที่ 2)

## ขั้นตอนที่ 2 — ตั้งค่า node "HTTP Request" (เลือกแบบใดแบบหนึ่ง)

### แบบ A: Vertex AI + Service Account (ตรงกับ workflow เดิมของคุณ `asia-southeast1...`)

1. ดับเบิลคลิก node **HTTP Request**
2. แก้ **URL** — แทน `YOUR_GCP_PROJECT_ID` ด้วย Project ID จริงของ GCP:
   ```
   https://asia-southeast1-aiplatform.googleapis.com/v1/projects/<PROJECT_ID>/locations/asia-southeast1/publishers/google/models/gemini-2.5-flash:generateContent
   ```
3. ช่อง **Credential for Google API** → **Create new credential** →
   เลือกชนิด **Google API (Service Account)**:
   - **Service Account Email**: อีเมลของ service account
     (ต้องมี role `Vertex AI User` ใน project นั้น)
   - **Private Key**: ก๊อปทั้งก้อน `-----BEGIN PRIVATE KEY-----...` จากไฟล์
     JSON key
   - เปิด **Set up for use in HTTP Request node** แล้วใส่ scope:
     `https://www.googleapis.com/auth/cloud-platform`
4. Save

### แบบ B: Gemini API key (AI Studio — ง่ายกว่า ไม่ต้องมี GCP project)

1. สร้าง API key ที่ <https://aistudio.google.com/apikey>
2. ใน node **HTTP Request**:
   - **URL** เปลี่ยนเป็น:
     ```
     https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent
     ```
   - **Authentication** → `Generic Credential Type` →
     **Generic Auth Type** → `Header Auth` → สร้าง credential ใหม่:
     - Name: `x-goog-api-key`
     - Value: `<API key ของคุณ>`
3. ส่วน Body ไม่ต้องแก้ — โครง request เหมือนกันทั้งสองแบบ

> ต้องการเปลี่ยนรุ่นโมเดล: แก้เฉพาะท้าย URL เช่น
> `gemini-2.5-pro:generateContent` (ถ้ารุ่นนั้นไม่รองรับ
> `thinkingConfig` ให้ลบ 2 บรรทัดนั้นออกจาก Code in JavaScript2 ด้วย)

## ขั้นตอนที่ 3 — Activate + ชี้แอปมาที่ webhook

1. กด **Activate** (สวิตช์มุมขวาบน)
2. เปิด node **Webhook** → ก๊อป **Production URL** เช่น
   `https://<n8n-host>/webhook/artwork-ocr`
   - ⚠️ **Test URL** (`/webhook-test/...`) ใช้ได้เฉพาะตอนกด "Listen for
     test event" เท่านั้น — ห้ามเอาไปใส่ใน env จริง
3. ฝั่งเครื่องที่รันแอป ตั้ง env:
   ```bash
   export N8N_OCR_WEBHOOK_URL="https://<n8n-host>/webhook/artwork-ocr"
   export OCR_BACKEND=n8n          # หรือปล่อยว่าง ระบบเลือก n8n ให้เองเมื่อมี URL
   ```

## ขั้นตอนที่ 4 — ทดสอบโดยไม่ต้องเปิดแอป

```bash
IMG_B64=$(base64 -w0 zone_crop.jpg)          # macOS: base64 -i zone_crop.jpg | tr -d '\n'
curl -s -X POST "https://<n8n-host>/webhook/artwork-ocr" \
  --data-urlencode "image_b64=${IMG_B64}" | python3 -m json.tool
```

ผลที่ถูกต้อง:

```json
{
  "text": "PRODUCTO DE CALIDAD\n16.12 LBS. (7.31 kg)",
  "blocks": [
    {"text": "PRODUCTO DE CALIDAD", "bbox": [12, 8, 410, 36], "conf": 0.98}
  ],
  "engine": "gemini-2.5-flash"
}
```

ทดสอบสาขา false ด้วย: `curl -s -X POST <url> -d "image_b64=" ` →
ต้องได้ HTTP 400 + `{"error": "missing form field 'image_b64'"}`

## สิ่งที่ล็อกไว้แล้วใน workflow (อย่าแก้ถ้าไม่จำเป็น)

- **`temperature: 0, topP: 1`** — งานถอดความต้อง deterministic
- **Prompt ห้ามแก้คำสะกด** — ฝังอยู่ใน Code in JavaScript2 (ค่าคงที่
  `PROMPT`) ตรงกับ `N8N_PROMPT.md` ทุกตัวอักษร ถ้า LLM แอบแก้
  "caliddd" → "calidad" โหมด Artwork Check จะตาบอด
- **`responseSchema`** — บังคับ Gemini ตอบ JSON ตาม schema เสมอ
- **`thinkingConfig.thinkingBudget = 0`** — ปิด thinking ของ 2.5-flash
  (transcription ไม่ต้องคิด เร็วขึ้น/ถูกลง)
- **retry 2 ครั้ง + timeout 120s** ที่ node HTTP Request

## ขีดจำกัด / Troubleshooting

| อาการ | สาเหตุ / ทางแก้ |
|---|---|
| HTTP 413 หรือ "Payload Too Large" ที่ webhook | ภาพโซนใหญ่เกิน limit ของ n8n (default 16 MB) → ตั้ง env ฝั่ง n8n `N8N_PAYLOAD_SIZE_MAX=32` หรือลด `ARTWORK_OCR_DPI` ฝั่งแอป |
| 401/403 จาก Gemini | แบบ A: service account ไม่มี role `Vertex AI User` / scope ผิด • แบบ B: API key ผิดหรือหมดโควตา |
| 404 จาก Gemini | Project ID ใน URL ยังเป็น `YOUR_GCP_PROJECT_ID` หรือ region/model ไม่มีให้ใช้ — ลองเปลี่ยน `asia-southeast1` เป็น `us-central1` ทั้งสองจุดใน URL |
| 429 quota | node HTTP Request retry ให้ 2 ครั้งแล้ว ถ้ายังเจอบ่อยให้เพิ่ม quota หรือเว้นจังหวะส่งฝั่งแอป |
| ตอบ 500 + "Gemini API error: ..." | Code in JavaScript โยน error ตามที่ออกแบบ — เปิดดู execution log ใน n8n จะเห็น error object เต็มๆ จาก Google |
| `text` ว่าง + `warning: finishReason=MAX_TOKENS` | ข้อความในโซนยาวผิดปกติ → เพิ่ม `maxOutputTokens` ใน Code in JavaScript2 |
| โซน conf ต่ำถูกรายงาน UNREADABLE | พฤติกรรมที่ตั้งใจ — ภาพเบลอ/เล็กเกิน ให้คนตรวจเอง ดีกว่าฟ้องผิดมั่ว |

## ใช้ร่วมกับโหมด Label Paper เดิม

webhook นี้ใช้ contract เดียวกับโหมดเดิมทุกประการ จะ:

- **ใช้ตัวเดียวร่วมกันทั้งสองโหมด** — ชี้ `N8N_OCR_WEBHOOK_URL` มาที่
  `/webhook/artwork-ocr` ได้เลย หรือ
- **แยกคนละ workflow** — เก็บ workflow เดิมไว้ที่ path เดิม แล้วเครื่องที่รัน
  โหมด Artwork ตั้ง env ชี้มาที่ path ใหม่นี้ (โค้ดแอปไม่ต้องแก้)
