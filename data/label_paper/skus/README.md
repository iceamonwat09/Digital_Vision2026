# SKU Masters — Label Paper Inspection

แต่ละ SKU เป็นโฟลเดอร์ย่อยที่มีไฟล์ 2 ตัว:

```
data/label_paper/skus/
└── <SKU_CODE>/
    ├── master.pdf      ← artwork PDF (PyMuPDF ดึง text จาก text layer)
    └── spec.json       ← นิยาม field/color ที่ต้องตรวจ
```

## spec.json schema

```jsonc
{
  "sku_code":     "SNK-CHK-060",          // ต้องตรงกับชื่อโฟลเดอร์ที่ครอบ
  "display_name": "Snack ไก่ย่าง 60g",

  "fields": [
    // exact: barcode / expiry / registration — distance ต้อง = 0
    {"name":"barcode","expected":"8851234567890","tolerance":0,
     "method":"exact","critical":true},

    // levenshtein: ข้อความทั่วไปที่พอผิดได้บ้าง
    {"name":"product_name","expected":"ไก่ย่าง","tolerance":2,
     "method":"levenshtein","critical":false},

    // regex: ตรวจรูปแบบ เช่น EXP DDMMYYYY
    {"name":"expiry_format","expected":"EXP \\d{8}","tolerance":0,
     "method":"regex","critical":true}
  ],

  "colors": [
    {"name":"brand_red","hex":"#E53935","delta_e_tolerance":8.0}
  ]
}
```

## ฟิลด์อธิบาย

| key                  | ค่า                                                          |
|----------------------|--------------------------------------------------------------|
| `method`             | `"exact"` \| `"levenshtein"` \| `"regex"`                    |
| `tolerance`          | สำหรับ Levenshtein: ระยะที่ยอมรับได้ (0 = ต้องตรงทุกตัว)        |
| `critical`           | `true` → mismatch ทำให้ verdict = FAIL ทันที                  |
| `delta_e_tolerance`  | ΔE สูงสุดที่ยังถือว่าผ่าน (typical: 6–10)                      |

## SKU ตัวอย่างที่มีให้แล้ว

| SKU | ไฟล์ที่มี | ใช้ทำอะไร |
|---|---|---|
| `SAMPLE-001`     | `spec.json` (ยังไม่มี `master.pdf`) | ดูหน้า UI / โครง spec ได้ทันที — แต่ pixel & visual diff จะถูกข้ามเพราะไม่มี master |
| `AQUA-CHUNK-140` | `master.pdf` + `spec.json`          | ตัวอย่างครบชุด ตรวจได้จริงทั้งข้อความ / สี / pixel |
