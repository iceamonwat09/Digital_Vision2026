// ===== ⏪ รุ่นเดิม (ก่อนเพิ่มกฎข้อ 6) — ใช้สลับเพื่อทดสอบ A/B เท่านั้น =====
// คัดลอกตรงตัวจากโหนดบนสถานีที่ผู้ใช้วางมา (25 ก.ย. 2026) · ต่างจาก
// code_in_javascript2.js แค่ 2 จุด: ไม่มีข้อ 6 ใน SYSTEM_INSTRUCTION และ PROMPT
// ไม่มีวลี "each word in the script it is printed in" · ดู docs/N8N_OCR_PROMPT.md 5.2
// ===== Code in JavaScript2 : validate input + build Gemini request =====
// แอป (inspectors/ocr_n8n.py) ส่ง POST form-urlencoded ฟิลด์ 'image_b64'
// → n8n แปลงให้อยู่ใน $json.body.image_b64
// โหนดนี้: ตรวจ base64 / ชนิดภาพ / ขนาด แล้วประกอบ request body ของ
// Gemini ไว้ให้โหนด HTTP Request ใช้ตรงๆ (อย่าลืม: temperature ต้อง = 0)

const item = $input.first().json;
const body = item.body ?? item;            // รองรับทั้ง form และ JSON body

let b64 = String(body.image_b64 ?? body.image ?? '').trim();

// เผื่อบางตัวส่งมาเป็น data URI ("data:image/jpeg;base64,....")
const dataUri = b64.match(/^data:image\/[a-z0-9.+-]+;base64,(.+)$/is);
if (dataUri) b64 = dataUri[1];
b64 = b64.replace(/\s+/g, '');
// form-urlencoded อาจแปลง '+' เป็นช่องว่างมาแล้ว — ใส่กลับ
// (requests ฝั่ง Python encode ถูกต้องอยู่แล้ว บรรทัดนี้กันเครื่องมือทดสอบอื่น)
b64 = b64.replace(/ /g, '+');

let valid = true;
let error = '';

if (!b64) {
  valid = false;
  error = "missing form field 'image_b64'";
} else if (b64.length % 4 !== 0 || !/^[A-Za-z0-9+/]+={0,2}$/.test(b64)) {
  valid = false;
  error = "'image_b64' is not valid base64";
}

// อ่าน magic bytes เพื่อบอก mimeType ที่ถูกต้องให้ Gemini
let mimeType = 'image/jpeg';
if (valid) {
  const head = Buffer.from(b64.slice(0, 32), 'base64');
  if (head[0] === 0x89 && head[1] === 0x50 && head[2] === 0x4e) {
    mimeType = 'image/png';
  } else if (head[0] === 0xff && head[1] === 0xd8) {
    mimeType = 'image/jpeg';
  } else if (head[0] === 0x52 && head[1] === 0x49 && head[2] === 0x46 &&
             head[8] === 0x57 && head[9] === 0x45) {
    mimeType = 'image/webp';
  } else {
    valid = false;
    error = 'unsupported image format (expected JPEG / PNG / WebP)';
  }
}

const sizeBytes = Math.floor(b64.length * 3 / 4);
if (valid && sizeBytes > 19 * 1024 * 1024) {
  // เพดาน inline image ของ Gemini ~20MB — กันไว้ก่อนยิงจริง
  valid = false;
  error = `image too large: ${(sizeBytes / 1048576).toFixed(1)} MB (limit ~19 MB)`;
}

// ── systemInstruction = "นิสัยถาวร" ของโมเดล ────────────────────────────
// Gemini ไม่มี system role ใน contents — กฎที่แปะหน้า user turn จะ "แข่ง"
// กับข้อความอื่นใน turn เดียวกัน ส่วน systemInstruction เป็นค่าตั้งประจำ
// request ที่โมเดลถือไว้ตลอด ⇒ กฎห้ามทั้งหลายต้องอยู่ตรงนี้
const SYSTEM_INSTRUCTION = [
  'You are a verbatim transcription engine for printed packaging artwork',
  'used in a quality-control system. Your output is compared character by',
  'character against another file to find printing defects.',
  '',
  'ABSOLUTE RULES — breaking any of these corrupts the inspection:',
  '',
  '1. NEVER correct spelling, grammar, punctuation or word forms.',
  '   A misspelling on the label IS the defect being looked for. If the',
  '   label prints "كربوهيدات" you write "كربوهيدات", never the correct',
  '   "كربوهيدرات". If it prints "Sunflow" you write "Sunflow".',
  '',
  '2. NEVER skip, merge or summarise repeated lines. Nutrition tables',
  '   contain many identical short rows (e.g. "0 g" / "٠ جم" repeated 6',
  '   times). Transcribe EVERY row separately, even when consecutive rows',
  '   are byte-for-byte identical. Omitting one silently deletes a row',
  '   from the inspection.',
  '',
  '3. NEVER convert between digit scripts or number formats. Arabic-Indic',
  '   digits (٠١٢٣٤٥٦٧٨٩) stay Arabic-Indic. ASCII digits stay ASCII.',
  '   Keep the exact separator printed: "١.٢" is not "١٢" and not "1.2".',
  '   Keep fraction glyphs as printed ("½" stays "½", "١/٢" stays "١/٢").',
  '',
  '4. NEVER add, translate, transliterate, reorder or normalise anything.',
  '   No Unicode normalisation, no removing diacritics, no expanding',
  '   abbreviations, no filling in characters you think are missing.',
  '',
  '5. If a region is too small or blurry to read with confidence,',
  '   transcribe what you can see and LOWER the confidence for that block.',
  '   Never guess a plausible word. Reporting low confidence is correct;',
  '   inventing text is a failure.',
  '',
  'Reading order: top-to-bottom, left-to-right; right-to-left within',
  'Arabic/Hebrew text. One text element per line.',
  '',
  'Answer with JSON only — no prose, no markdown fences, no explanation.',
].join('\n');

// user prompt = งานของ "ภาพใบนี้" เท่านั้น (กฎถาวรอยู่ใน systemInstruction)
const PROMPT = [
  'Transcribe ALL text visible in this image exactly as printed,',
  'character-by-character, in every language present.',
  '',
  'Return ONLY JSON:',
  '{',
  '  "text": "<all text, one element per line>",',
  '  "blocks": [',
  '    {"text": "<element>", "bbox": [x, y, w, h], "conf": 0.0-1.0}',
  '  ],',
  '  "engine": "gemini-2.5-flash"',
  '}',
  '',
  'bbox uses PIXEL coordinates of the image you were given, origin at the',
  'top-left corner. "blocks" is optional — omit it entirely rather than',
  'guessing coordinates. Wrong coordinates are worse than no coordinates.',
].join('\n');

// Structured Output: บังคับ Gemini ตอบ JSON ตาม schema เสมอ
const responseSchema = {
  type: 'OBJECT',
  properties: {
    text: { type: 'STRING' },
    blocks: {
      type: 'ARRAY',
      items: {
        type: 'OBJECT',
        properties: {
          text: { type: 'STRING' },
          bbox: { type: 'ARRAY', items: { type: 'NUMBER' } },
          conf: { type: 'NUMBER' },
        },
        required: ['text', 'conf'],
      },
    },
    engine: { type: 'STRING' },
  },
  required: ['text'],
};

const geminiRequest = !valid ? null : {
  // ⚠️ systemInstruction เป็น field "พี่น้อง" ของ contents ไม่ใช่อยู่ข้างใน
  //    และไม่มี role — ใส่ผิดที่ = ถูกเมินเงียบ ๆ ไม่มี error
  systemInstruction: { parts: [{ text: SYSTEM_INSTRUCTION }] },
  contents: [{
    role: 'user',
    parts: [
      { text: PROMPT },
      { inlineData: { mimeType, data: b64 } },
    ],
  }],
  generationConfig: {
    temperature: 0,          // ห้ามแก้ — งานถอดความต้อง deterministic
    topP: 1,
    maxOutputTokens: 16384,
    responseMimeType: 'application/json',
    responseSchema,
    // gemini-2.5-flash เปิด thinking เป็นค่า default — งาน transcription
    // ไม่ต้องคิด ปิดเพื่อให้เร็ว/ถูกลง (ลบ 2 บรรทัดนี้ถ้าใช้รุ่นที่ไม่รองรับ)
    thinkingConfig: { thinkingBudget: 0 },
  },
};

return [{
  json: {
    valid,
    error,
    mime_type: mimeType,
    size_kb: Math.round(sizeBytes / 102.4) / 10,
    gemini_request: geminiRequest,
  },
}];
