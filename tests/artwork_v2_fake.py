"""ตัวช่วยสร้าง ``fullTextAnnotation`` ปลอมตามสเปก Vision สำหรับเทสต์ Artwork V2

แต่ละบรรทัด = 1 ย่อหน้า · คำแยกด้วยช่องว่าง · ตัวอักษรสุดท้ายของบรรทัดมี
LINE_BREAK (หรือ EOL_SURE_SPACE / HYPHEN ตามที่กำหนด) · ตัวอักษรกว้าง ``cw`` px
"""

from __future__ import annotations


def _sym(ch, x, y, cw, ch_h, conf, brk=None):
    s = {"text": ch, "confidence": conf,
         "boundingBox": {"vertices": [{"x": x, "y": y}, {"x": x + cw, "y": y},
                                      {"x": x + cw, "y": y + ch_h}, {"x": x, "y": y + ch_h}]}}
    if brk:
        s["property"] = {"detectedBreak": {"type": brk}}
    return s


def line_para(text, x, y, conf=0.98, cw=10, h=20, end="LINE_BREAK", confs=None,
              drop_zero=False):
    words = []
    cx = x
    toks = text.split(" ")
    k = 0
    for wi, tok in enumerate(toks):
        syms = []
        for ci, ch in enumerate(tok):
            last_in_word = ci == len(tok) - 1
            brk = None
            if last_in_word:
                brk = end if wi == len(toks) - 1 else "SPACE"
            c = confs[k] if confs else conf
            k += 1
            syms.append(_sym(ch, cx, y, cw, h, c, brk))
            cx += cw
        cx += cw                      # ช่องว่าง
        k += 1
        xs = [s["boundingBox"]["vertices"][0]["x"] for s in syms]
        words.append({"confidence": conf, "symbols": syms,
                      "boundingBox": {"vertices": [{"x": xs[0], "y": y},
                                                   {"x": xs[-1] + cw, "y": y},
                                                   {"x": xs[-1] + cw, "y": y + h},
                                                   {"x": xs[0], "y": y + h}]}})
    para = {"words": words}
    if drop_zero:                     # JSON จริงละพิกัดที่เป็น 0
        for w in words:
            for s in w["symbols"]:
                for v in s["boundingBox"]["vertices"]:
                    for kk in ("x", "y"):
                        if v.get(kk) == 0:
                            v.pop(kk)
    return para


def fta(lines, W=1000, H=1000, block_type="TEXT"):
    """``lines`` = [(text, x, y)] หรือ [(text, x, y, kwargs)]"""
    paras = []
    for item in lines:
        text, x, y = item[0], item[1], item[2]
        kw = item[3] if len(item) > 3 else {}
        paras.append(line_para(text, x, y, **kw))
    return {"text": "\n".join(i[0] for i in lines),
            "pages": [{"width": W, "height": H, "confidence": 0.97,
                       "property": {"detectedLanguages": [{"languageCode": "en",
                                                           "confidence": 0.9}]},
                       "blocks": [{"blockType": block_type, "paragraphs": paras}]}]}


# ── ข้อมูล OCR จริง (บรรทัด + กรอบบรรทัด) → fullTextAnnotation ───────────

def para_in_box(text, box, conf=0.98, end="LINE_BREAK"):
    """บรรทัดเดียวในกรอบ ``box`` — กระจายตัวอักษรเท่า ๆ กัน (ช่องว่าง = 1 ช่อง)"""
    x0, y0, x1, y1 = box
    n = max(1, len(text))
    cw = (x1 - x0) / float(n)
    words, cur, k = [], [], 0
    toks = text.split(" ")
    for wi, tok in enumerate(toks):
        syms = []
        for ci, ch in enumerate(tok):
            brk = None
            if ci == len(tok) - 1:
                brk = end if wi == len(toks) - 1 else "SPACE"
            sx = x0 + k * cw
            syms.append(_sym(ch, sx, y0, cw, y1 - y0, conf, brk))
            k += 1
        k += 1
        if not syms:
            continue
        xs0 = syms[0]["boundingBox"]["vertices"][0]["x"]
        xs1 = syms[-1]["boundingBox"]["vertices"][1]["x"]
        words.append({"confidence": conf, "symbols": syms,
                      "boundingBox": {"vertices": [{"x": xs0, "y": y0}, {"x": xs1, "y": y0},
                                                   {"x": xs1, "y": y1}, {"x": xs0, "y": y1}]}})
    return {"words": words}


def fta_from_lines(lines, W, H):
    """``lines`` = [(text, box, conf)] → fta หนึ่งบรรทัดต่อหนึ่งย่อหน้า"""
    paras = [para_in_box(t, b, c) for t, b, c in lines if t.strip()]
    return {"text": "\n".join(t for t, _, _ in lines),
            "pages": [{"width": W, "height": H,
                       "blocks": [{"blockType": "TEXT", "paragraphs": paras}]}]}


def load_real(path):
    """อ่านไฟล์ ``tests/data/artwork_v2/*_lines.txt`` → {side: (W, H, [(text, box, conf)])}"""
    import re
    rx = re.compile(r'^\s*([AB])\d+ conf=([0-9.]+) .*?box=\[([0-9,]+)\] "(.*)"\s*$')
    sizes, out = {}, {"A": [], "B": []}
    with open(path, encoding="utf-8") as f:
        for raw in f:
            if raw.startswith("SIZE"):
                _, s, w, h = raw.split()
                sizes[s] = (int(w), int(h))
                continue
            m = rx.match(raw)
            if m:
                box = tuple(float(v) for v in m.group(3).split(","))
                out[m.group(1)].append((m.group(4), box, float(m.group(2))))
    return {s: (sizes[s][0], sizes[s][1], out[s]) for s in ("A", "B")}
