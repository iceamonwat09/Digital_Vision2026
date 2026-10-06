"""คำตอบของ Gemini จริงจากสถานี (6 ต.ค. 2026 · AvoDerm Master-1 ↔ Master-2) ถอดจาก Log

ใช้เล่นซ้ำกับชั้นตรวจคำตอบของแอป — ข้อมูล OCR อยู่ใน ``data/artwork_v2/ai_replay/*.log``
(``reason``/``suggestion`` ตัดสั้น เพราะ Log ตัดไว้ · รหัสคำและข้อความที่ยกมาตรงกับ Log ทุกตัวอักษร)
"""


def _it(aw, aq, bw, bq, verdict, kind="text"):
    return {"a_words": aw, "a_quote": aq, "b_words": bw, "b_quote": bq, "kind": kind,
            "verdict": verdict, "reason": "(จาก Log)", "suggestion": "(จาก Log)"}


# งาน 20261006_091805_d4a95c · โหมด judge · AI ตอบ 19 ข้อ (ใช้ได้ 12 · ปฏิเสธ 7)
JUDGE_RUN004 = [
    # ── 6 ข้อที่แอปใช้ (findings) ──
    _it(["A29:0"], "D-calcium", ["B56:0"], "D-Calcium", "real", "case"),
    _it(["A42:0"], "OMEGA-b", ["B27:0"], "OMEGA-62", "uncertain", "number"),
    _it(["A44:0"], "3", ["B28:0"], "-3", "real", "punct"),
    _it([], "", ["B45:6"], "USA", "real", "extra"),
    _it(["A66:2"], "Breed", ["B64:2"], "Breeds", "real"),
    _it(["A68:0"], "5290700241", ["B67:0"], "152907002410", "real", "number"),
    # ── 6 ข้อที่ AI พับว่าเป็นจุดไข่ปลา (ai_dismissed) ──
    _it(["A12:2", "A12:3"], "(min)...... ..7.0%", ["B12:2", "B12:3"], "(min).. ..7.0%", "noise", "punct"),
    _it(["A13:1", "A13:2"], "(min).. ..0.20%", ["B13:1", "B13:2"], "(min).. .0.20%", "noise", "punct"),
    _it(["A15:1", "A15:2"], "(min).. .0.16%", ["B15:1", "B15:2"], "(min).. ..0.16%", "noise", "punct"),
    _it(["A18:1", "A18:2"], "(max)... .84.0%", ["B18:1", "B18:2"], "(max). ..84.0%", "noise", "punct"),
    _it(["A20:1", "A20:2"], "(max).. .4.0%", ["B20:1", "B20:2"], "(max).. ..4.0%", "noise", "punct"),
    _it(["A24:2", "A24:3"], "(calculated).. .294", ["B51:2", "B51:3"], "(calculated)... .294",
        "noise", "punct"),
    # ── 7 ข้อที่แอปปฏิเสธ (invalid) ──
    _it(["A17:2"], "Acid*", ["B17:2", "B17:3"], "Acid *", "real", "punct"),
    _it(["A19:2", "A19:3"], "Acid *", ["B19:2"], "Acid*", "real", "punct"),
    _it(["A23:2", "A23:3"], "(calculated).. .797", ["B50:2", "B50:3"], "(calculated). ..797",
        "noise", "punct"),
    _it(["A32:7", "A32:8"], "Copper Sulphate", ["B59:6", "B59:7"], "Copper Sulfate", "real"),
    _it(["A32:8", "A32:9"], "Sulphate Pentahydrate.", ["B59:7"], "Sulfate.", "real", "missing"),
    _it(["A60:0"], "Breeder's", ["B43:0"], "Breeder's", "noise", "punct"),
    _it(["A62:4", "A62:5"], "Irwindale Park,", ["B45:3"], "Irwindale,", "real", "missing"),
]

# งาน 20261006_092517_c1d1d2 · โหมด assist · items 9 ข้อ ถูกปฏิเสธทั้งหมด (ยกจุดไข่ปลาครึ่งคำ)
ASSIST_RUN005_ITEMS = [
    _it(["A8:2"], "..........", ["B12:2"], ".........", "noise", "punct"),
    _it(["A9:1"], "....", ["B13:1"], "..", "noise", "punct"),
    _it(["A10:2"], "..", ["B14:2"], "...", "noise", "punct"),
    _it(["A11:1"], "....", ["B15:1"], "..", "noise", "punct"),
    _it(["A14:1"], "..", ["B18:1"], "..", "noise", "punct"),
    _it(["A15:1"], "..", ["B20:1", "B20:2"], "... ...", "noise", "punct"),
    _it(["A16:3"], "......", ["B19:3"], "......", "noise", "punct"),
    _it(["A52:2"], ".", ["B23:2"], "..", "noise", "punct"),
    _it(["A53:2"], "...", ["B24:2"], "..", "noise", "punct"),
]
