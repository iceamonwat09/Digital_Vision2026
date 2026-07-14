# คู่มือ Import + ตั้งค่า N8N Workflow ของโหมด Artwork

โหมดนี้ใช้ N8N **2 workflow** (import วิธีเดียวกัน ใช้ credential Gemini ตัวเดียวกันได้):

| ไฟล์ import | path | หน้าที่ | จำเป็นไหม |
|---|---|---|---|
| `n8n_artwork_ocr.workflow.json` | `/webhook/artwork-ocr` | OCR ถอดข้อความจากภาพโซน (verbatim) | **จำเป็น** — หัวใจของการตรวจ |
| `n8n_artwork_translate.workflow.json` | `/webhook/artwork-translate` | แปลข้อความเป็น EN สำหรับแท็บ "ข้อความ + คำแปล" | ทางเลือก — ไม่ตั้งก็ตรวจได้ปกติ แค่ไม่มีคำแปล |

> ตัวแปร env ฝั่งแอป: `N8N_OCR_WEBHOOK_URL` (OCR) และ
> `N8N_TRANSLATE_WEBHOOK_URL` (แปล) — ค่า default ชี้ไปเครื่องคุณ
> `http://172.32.201.106:5678/...` อยู่แล้ว

---

# 1) Workflow OCR — "Artwork OCR — Gemini Verbatim Transcription"

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

---

# 2) Workflow แปล EN — "Artwork Translate — Gemini EN (text-only)"

ไฟล์ workflow พร้อม import: **`n8n_artwork_translate.workflow.json`**

โครงสร้าง node เหมือน workflow OCR ทุกประการ ต่างกันแค่ **รับ/ส่งเป็น
ข้อความล้วน ไม่มีรูป** จึงเร็วและถูกกว่ามาก:

```
Webhook ──> Code in JavaScript2 ──> If ──true──> HTTP Request (Gemini) ──> Code in JavaScript ──> Respond to Webhook
(artwork-translate)                    └─false─> Respond to Webhook1 (HTTP 400)
```

| Node | หน้าที่ |
|---|---|
| **Webhook** | รับ `POST` JSON `{"lines": ["บรรทัด1", "บรรทัด2", ...]}` (path: `artwork-translate`) |
| **Code in JavaScript2** | ตรวจว่า `lines` เป็น array (≤ 400 บรรทัด) แล้วประกอบ Gemini request สั่งแปลทุกบรรทัดเป็น EN **และ** สแกนคำสะกดผิดในต้นฉบับแบบ advisory — `temperature 0`, responseSchema บังคับตอบ `{"translations":[...], "spell":[...]}` โดยแต่ละ finding มี `kind` (`typo`/`truncated`/`variant`) + `reason` (คำอธิบายสั้นภาษาไทย) |
| **If / HTTP Request / Code in JavaScript** | เหมือน workflow OCR (ยิง Gemini, retry 2, แกะ JSON ทั้ง `translations`/`spell`, กัน error) |
| **Respond to Webhook** | ตอบ `{"translations": ["en1", ...], "spell": [{"flagged": false, "suggestion": null, "kind": null, "reason": null}, ...]}` เรียงตรงกับ input |

## ขั้นตอน

1. **Import** `n8n_artwork_translate.workflow.json` (วิธีเดียวกับข้อ 1)
2. **node HTTP Request** — แก้ URL แทน `YOUR_GCP_PROJECT_ID` และผูก
   **credential Gemini ตัวเดียวกับ workflow OCR** (ไม่ต้องสร้างใหม่)
3. **Activate** แล้วก๊อป Production URL — ปกติได้
   `http://<host>:5678/webhook/artwork-translate` ตรงกับค่า default
   ของ `N8N_TRANSLATE_WEBHOOK_URL` อยู่แล้ว ถ้า host/port ตรงก็ไม่ต้องตั้ง env

## ⚠️ คำแปล EN ว่างทั้งคอลัมน์ (`—`) ทั้งที่ทดสอบ 2-3 บรรทัดแล้วผ่าน

อาการ: ทดสอบยิงไม่กี่บรรทัดได้คำแปลปกติ แต่พอใช้กับฉลากจริง (บรรทัดเยอะ
เช่น 100+ บรรทัด) คำแปล EN กลับว่างหมดทุกแถว

สาเหตุ: Gemini แปลไปจนชน **`maxOutputTokens`** แล้วถูกตัดกลางคัน → JSON
ที่ตอบกลับไม่ครบ → parse ไม่ผ่าน → node คืน `translations` ว่างทั้งหมด
สังเกตได้จาก response จะมี `"warning": "model did not return a
translations array"` และ `candidatesTokenCount` เท่ากับค่า
`maxOutputTokens` พอดีเป๊ะ (ชนเพดาน)

วิธีแก้: เปิด node **Code in JavaScript2** หาบรรทัด
`maxOutputTokens: 8192,` เปลี่ยนเป็น **`maxOutputTokens: 65536,`**
(เพดานสูงสุดของ gemini-2.5-flash) แล้ว Save — รองรับฉลากที่บรรทัดเยอะ
ขึ้นมาก ค่า default ในไฟล์ template ตั้งเป็น 65536 ให้แล้ว

> ฝั่งแอป (`translate.py`) ถูกแก้ไม่ให้แคชผลแปลที่ว่างทั้งหมดแล้ว ดังนั้น
> เมื่อแก้ N8N เสร็จ แค่กด "แปล / อธิบาย (EN)" ซ้ำก็จะแปลใหม่ให้เอง
> ไม่ต้องลบไฟล์แคช

## ⚠️ ถ้าคุณมี workflow เก่าอยู่แล้ว (อัปเดตเอง ไม่ได้ import ใหม่)

โหมด "ตรวจสะกดโดย AI" ต้องการให้ **ทั้ง 5 node** ส่งต่อ field `spell`
ครบทุก node ไม่ใช่แค่ 2 node โค้ด — นี่คือจุดที่พลาดบ่อยที่สุดเวลาแก้
workflow เก่าด้วยมือ (วาง code ทับเฉพาะ node "Code in JavaScript" /
"Code in JavaScript2" แล้วลืม node Respond):

| Node | ต้องมี/ทำอะไรเพิ่มเพื่อรองรับ `spell` |
|---|---|
| **Code in JavaScript2** (validate+build) | prompt ต้องสั่งให้ Gemini คืนทั้ง `translations` **และ** `spell` พร้อม `responseSchema` ที่มี property `spell` (array of `{original, suggestion, kind, reason}`) — และคืน `lines`/`line_count` ออกมาด้วยให้ node ถัดไปใช้ align ความยาว |
| **Code in JavaScript** (parse) | ต้อง parse ทั้ง `parsed.translations` และ `parsed.spell`, normalize ให้เป็น `{flagged, suggestion, kind, reason}` เสมอ, pad/truncate ให้ยาวเท่า `line_count` |
| **Respond to Webhook** (200) | **ต้องมี `spell: $json.spell` ใน responseBody ด้วย** — แค่แก้ 2 node โค้ดแล้วลืมจุดนี้ คือสาเหตุอันดับ 1 ที่ทำให้คอลัมน์ AI ในแอปไม่มีข้อมูลเลย ถึงแม้ Gemini จะตอบ `spell` มาให้ตั้งแต่ใน node ก่อนหน้าแล้วก็ตาม |
| **Respond to Webhook1** (400, error path) | ควรมี `spell: []` ด้วย เพื่อให้ shape ตรงกันทั้งสองทาง |

ตรวจสอบ **ทุก node ทั้ง 5 ตัว** ทีละตัวหลังแก้ ไม่ใช่เชื่อว่าแก้ node
parse แล้วปลายทางต้องถูกตามไปด้วยอัตโนมัติ — n8n ไม่ propagate field
ข้าม node ให้เอง ถ้า Respond node ไม่ได้อ้างถึง field นั้นตรงๆ มันจะ
หายไปจาก response ที่แอปได้รับ

## ข้อผิดพลาดที่พบบ่อยตอนแก้ field แบบ expression (`fx`)

1. **อย่าพิมพ์ `=` นำหน้าซ้ำ** — ใน n8n field ที่เปิดโหมด expression
   (ไอคอน `fx` ติดสีแล้ว) เนื้อหาในกล่องคือ `{{ ... }}` **เปล่าๆ**
   เครื่องหมาย `=` ข้างหน้าเป็นแค่ตัวบอก UI ว่า "field นี้เป็น
   expression" ไม่ใช่ส่วนของนิพจน์ ถ้า paste ค่าที่มี `=` นำหน้าซ้ำเข้าไป
   ใน field ที่อยู่ใน expression mode อยู่แล้ว ตัว `=` นั้นจะกลายเป็น
   ส่วนหนึ่งของ string จริง ๆ ที่ evaluate ออกมา ทำให้ผลลัพธ์เพี้ยนเป็น
   `={"translations":...}` (มี `=` โผล่ปนใน JSON) แล้ว n8n จะฟ้อง
   **"Invalid JSON in 'Response Body' field"** เพราะ parse JSON ไม่ผ่าน
   - ค่าที่ถูกต้องในกล่อง `responseBody` (โหมด expression):
     ```
     {{ JSON.stringify({ translations: $json.translations, spell: $json.spell, warning: $json.warning }) }}
     ```
     **ไม่มี** `=` นำหน้า
   - ถ้า field ยังเป็นโหมดข้อความปกติ (ไอคอน `fx` ยังไม่ติด) ให้กด `fx`
     ก่อน แล้วใส่แค่ `{{ ... }}` แบบเดียวกัน — ห้ามพิมพ์ `=` เองทั้งสองกรณี
2. **preview ใน editor อาจเป็นข้อมูลเก่าที่ยัง cache อยู่** — บางครั้ง n8n
   โชว์ค่า preview/pinned data จาก execution รอบก่อนตอนแก้ field
   expression ทำให้เห็น error "Invalid JSON" หลอกๆ ทั้งที่ field ที่
   พิมพ์ไว้ถูกแล้ว วิธีเช็คให้ชัวร์: **Save workflow แล้วยิง request จริง
   เข้า webhook** (ไม่ใช่กดแค่ "Execute step" ใน editor) ถ้า response
   จริงออกมาถูก ก็ไม่ต้องสนใจ warning เก่าใน preview
3. หลังแก้ทุกครั้ง ให้ทดสอบด้วย request จริงทั้ง 2 เคส (lines ถูกต้อง /
   lines ผิด เช่นส่ง `{}`) เพื่อเช็คทั้ง `Respond to Webhook` และ
   `Respond to Webhook1` พร้อมกัน

## ทดสอบเร็ว (bash / curl)

```bash
curl -s -X POST "http://172.32.201.106:5678/webhook/artwork-translate" \
  -H "Content-Type: application/json" \
  -d '{"lines":["نوع السمك : كاتسوانوس بيلاميس","Net Weight: 200 gm","16785"]}' \
  | python3 -m json.tool
```

ผลที่ถูกต้อง — จำนวนรายการเท่ากับ input, บรรทัด EN/ตัวเลขล้วนคืนเดิม,
และมี `spell` มาด้วย:

```json
{
  "translations": ["Type of fish: Katsuwonus pelamis", "Net Weight: 200 gm", "16785"],
  "spell": [
    {"flagged": false, "suggestion": null, "kind": null, "reason": null},
    {"flagged": false, "suggestion": null, "kind": null, "reason": null},
    {"flagged": false, "suggestion": null, "kind": null, "reason": null}
  ]
}
```

## ทดสอบเร็ว (Windows PowerShell)

บน PowerShell `\` ไม่ใช่ตัวต่อบรรทัด (ใช้ได้แค่ใน bash) และ `curl` มัก
ถูก alias ไปที่ `Invoke-WebRequest` ซึ่ง escape เครื่องหมายคำพูดต่างจาก
GNU curl — ใช้วิธีนี้แทนจะชัวร์กว่า:

```powershell
$body = @{ lines = @("نوع السمك : كاتسوانوس بيلاميس", "Net Weight: 200 gm", "16785") } | ConvertTo-Json
$res = Invoke-RestMethod -Method Post `
  -Uri "http://172.32.201.106:5678/webhook/artwork-translate" `
  -ContentType "application/json; charset=utf-8" `
  -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
$res | ConvertTo-Json -Depth 10
```

หมายเหตุ:

- ถ้าอยากใช้ `curl` จริง (ไม่ใช่ alias) ต้องเรียก `curl.exe` ตรงๆ —
  แต่การ escape `"` ใน JSON ผ่าน PowerShell มักเพี้ยน (เจอ error
  `Failed to parse request body ... Expected property name or '}' in
  JSON`) จึงแนะนำให้ใช้ `Invoke-RestMethod` ด้านบนแทน
- `Invoke-RestMethod` ค่า default จะ render ผลลัพธ์เป็น table แล้วตัด
  field ที่เป็น array/object ซ้อนกันออก (`spell` มักหายจากหน้าจอ) ทั้งที่
  จริงๆ field นั้นมีค่าอยู่ — ต้อง pipe ผ่าน `| ConvertTo-Json -Depth 10`
  เสมอเพื่อดู structure เต็มๆ ก่อนสรุปว่า "ไม่มีข้อมูล"

## หมายเหตุสำคัญของแท็บ "ข้อความ + คำแปล"

- เป็น **ข้อมูลช่วยอ่านอย่างเดียว** ไม่มีผลต่อ PASS/FAIL — สอดคล้องกฎ
  "ระบบห้ามให้คำที่เดาขึ้นมาตัดสินผล"
- คอลัมน์ **"คำที่ควรใช้"** (ตรวจสะกดแบบ deterministic) เมื่อสะกดน่าสงสัย
  มาจาก engine deterministic (พจนานุกรม + คลังคำแบรนด์ + เสียงข้างมากของ
  panel กลุ่มเดียวกัน) **ไม่ใช่** ตัวแปล — เชื่อถือได้และตรวจที่มาได้
- คอลัมน์ใหม่ **"ตรวจสะกดโดย AI"** มาจากฟิลด์ `spell` ที่ Gemini คืนมา
  พร้อมกับการแปล (webhook เดียวกัน ไม่ได้สร้าง workflow ใหม่) — เป็นแค่
  **คำแนะนำ ไม่มีผลต่อ verdict เด็ดขาด** เหมือนคอลัมน์ dictionary
  เดิมทุกประการ ต่างกันที่แหล่งข้อมูล (โมเดล AI ไม่ใช่ dictionary)
- คอลัมน์นี้มี **3 สถานะ** แยกกันชัดเจน เพื่อไม่ให้ "AI ตรวจแล้วไม่พบ
  อะไร" ไปปนกับ "AI ยังไม่ได้รันเลย" (สองอย่างนี้หน้าตาคล้ายกันถ้าไม่มี
  flag แยก):
  - **"ยังไม่รองรับ"** (สีเทา) — `ai_spell_available = false` แปลว่า
    webhook ที่ตอบมาไม่มีฟิลด์ `spell` เลย (เช่น workflow ยังไม่ได้อัปเดต
    ให้ส่ง `spell` ออกมา หรือ Respond node ลืมอ้างถึง `$json.spell` —
    ดูหัวข้อ "ถ้าคุณมี workflow เก่าอยู่แล้ว" ด้านบน)
  - **"✓ ไม่พบ"** (เขียว) — `ai_spell_available = true` และ
    `flagged = false` แปลว่า AI ตรวจแล้วจริง แค่ไม่เจอจุดที่น่าสงสัยใน
    บรรทัดนั้น
  - **"🤖 สะกดผิด"** (แดง) — `flagged = true` + `kind = "typo"`
    พร้อม `suggestion` คำที่ AI เสนอแก้ และ `reason` คำอธิบายสั้นๆ ภาษาไทย
    (เช่น "Cude น่าจะเป็น Crude") — ยังเป็นคำแนะนำของ AI (มี 🤖 กำกับ)
    คนตรวจต้องยืนยันด้วยตา ไม่มีผลต่อ verdict
  - **"🤖 คำไม่ครบ (ถูกตัด)"** (เหลือง/ส้ม) — `flagged = true` +
    `kind = "truncated"` คำถูกตัดปลาย (เช่น "Ingredi" → "Ingredients")
    - **⚠️ กับดัก line-wrap:** prompt สั่งชัดว่า "บรรทัดที่จบกลางประโยค
      แต่มีเนื้อหาต่อในบรรทัดถัดไป = การตัดบรรทัดปกติ ห้าม flag เป็น
      truncated" — เพราะระบบส่ง OCR แยกทีละบรรทัด ย่อหน้ายาว (เช่น วิธีให้
      อาหาร/รายการส่วนผสม) จะถูกตัดหลายบรรทัด ถ้า prompt ไม่กันไว้ ทุก
      บรรทัดกลางย่อหน้าจะโดน flag เป็น "ถูกตัด" ผิดๆ (false positive ยับ).
      ถ้าเจออาการนี้กลับมา = prompt ใน `Code in JavaScript2` ยังไม่มีกฎ
      "LINE-WRAP IS NOT TRUNCATION"
  - **"🤖 น่าสงสัย"** (เหลือง/ส้ม) — `flagged = true` แต่ไม่มี `kind`
    (workflow/แคชเก่าที่ยังไม่ส่ง `kind`) = ป้าย fallback แบบเดิม
  - **"🤖 ทางเลือกการสะกด (ไม่ใช่คำผิด)"** (ฟ้า) — `flagged = true` +
    `kind = "variant"` แปลว่าคำนั้น**ถูกต้อง** แต่เป็นการสะกดตามภูมิภาค
    (เช่น British "fibre" กับ American "fiber") — AI จะบอกอีกรูปสะกดใน
    `suggestion` พร้อมเหตุผลใน `reason` ให้คนตรวจยืนยันว่าตรงกับตลาด
    เป้าหมายของฉลากหรือไม่ ไม่ต้องแก้ artwork ถ้าเลือกใช้รูปนั้นอยู่แล้ว
  - workflow เก่าที่ยังไม่คืน `kind`/`reason` ใช้งานต่อได้ทันที — แอปจะ
    โชว์แบบเดิม (ไม่มีคำอธิบาย) โดยไม่ error และแคชแปลเก่าก็ยังเปิดดูได้
  - ถ้าทุกแถวในตารางโชว์ "ยังไม่รองรับ" ทั้งหมด ให้สงสัยก่อนว่า
    workflow `artwork-translate` ยังไม่ได้แก้ให้ส่ง `spell` ออกมา —
    ลองยิง request ทดสอบตรงๆ ตามหัวข้อ "ทดสอบเร็ว" ด้านบน แล้วเช็คว่า
    response มี key `spell` จริงไหมก่อนสงสัยฝั่งแอป
- ระบบส่ง**ข้อความที่ OCR ได้แล้ว** ไปแปล (ไม่ส่งรูปซ้ำ) ทุกบรรทัดของ
  ทุกโซนใน **1 request** ต่อการกดปุ่มแปล 1 ครั้ง และ**แคชผล**ไว้ — กดซ้ำ
  หรือเปิดรายการเดิมไม่เสียค่า Gemini ใหม่ถ้าข้อความไม่เปลี่ยน
- ถ้าไม่ตั้งค่า `N8N_TRANSLATE_WEBHOOK_URL` หรือบริการแปลล่ม → ตารางยัง
  แสดงข้อความต้นฉบับ + ไฮไลต์คำสะกด + คำแนะนำได้ตามปกติ แค่คอลัมน์ EN ว่าง
