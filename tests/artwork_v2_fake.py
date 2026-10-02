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
