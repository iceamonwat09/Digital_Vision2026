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


def _tilt(words, angle):
    """ตั้งมุมของคำ (poly_angle อ่านจากจุดที่ 0→1 ของกรอบคำ) — กรอบตัวอักษรคงแนวแกน"""
    if angle is None:
        return
    import math
    for w in words:
        vs = w["boundingBox"]["vertices"]
        x0, y0 = vs[0]["x"], vs[0]["y"]
        L = max(1.0, vs[1]["x"] - x0)
        vs[1] = {"x": x0 + L * math.cos(math.radians(angle)),
                 "y": y0 + L * math.sin(math.radians(angle))}


def line_para(text, x, y, conf=0.98, cw=10, h=20, end="LINE_BREAK", confs=None,
              drop_zero=False, angle=None):
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
    _tilt(words, angle)
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

def para_in_box(text, box, conf=0.98, end="LINE_BREAK", angle=None):
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
    _tilt(words, angle)
    return {"words": words}


def fta_from_lines(lines, W, H):
    """``lines`` = [(text, box, conf)] หรือ [(text, box, conf, angle)]
    → fta หนึ่งบรรทัดต่อหนึ่งย่อหน้า"""
    paras = [para_in_box(it[0], it[1], it[2], angle=it[3] if len(it) > 3 else None)
             for it in lines if it[0].strip()]
    return {"text": "\n".join(it[0] for it in lines),
            "pages": [{"width": W, "height": H,
                       "blocks": [{"blockType": "TEXT", "paragraphs": paras}]}]}


def load_real(path, with_angle=False):
    """อ่านไฟล์ ``tests/data/artwork_v2/*_lines.txt`` → {side: (W, H, [(text, box, conf)])}
    ``with_angle=True`` ⇒ [(text, box, conf, angle)] (มุมจริงจาก Vision)"""
    import re
    rx = re.compile(r'^\s*([AB])\d+ conf=([0-9.]+) .*?ang=([0-9.-]+) box=\[([0-9,]+)\] "(.*)"\s*$')
    sizes, out = {}, {"A": [], "B": []}
    with open(path, encoding="utf-8") as f:
        for raw in f:
            if raw.startswith("SIZE"):
                _, s, w, h = raw.split()
                sizes[s] = (int(w), int(h))
                continue
            m = rx.match(raw)
            if m:
                box = tuple(float(v) for v in m.group(4).split(","))
                item = (m.group(5), box, float(m.group(2)))
                if with_angle:
                    item += (float(m.group(3)),)
                out[m.group(1)].append(item)
    return {s: (sizes[s][0], sizes[s][1], out[s]) for s in ("A", "B")}


# ── ชุดข้อมูลจากสถานี (ชุดทดสอบชุดที่ 2, 3, …) ───────────────────────────
# วางไว้ที่ tests/data/artwork_v2/station_runs/<ชื่อ>/ ได้ 2 แบบ (แบบแรกแม่นกว่า):
#   ① ผลดิบของ Vision: p1_a.json p1_b.json … (คัดลอกจาก
#      data/artwork_v2/jobs/<งาน>/runs/<รอบ>/raw/ บนสถานี) — มีความมั่นใจรายตัวอักษร + มุมจริง
#   ② log.txt ที่กดปุ่ม "คัดลอก Log" — มีแค่ความมั่นใจรายบรรทัด และเป็นบรรทัดหลังต่อแถวแล้ว
# + expect.json (ไม่บังคับ) — ดู tests/test_artwork_v2_runs.py

def load_run_dir(path):
    """→ {n: {"A": (W, H, lines), "B": (W, H, lines), "source": "raw"|"log"}}
    ``lines`` = บรรทัดจาก ``textmodel.parse`` (พร้อมใช้กับ ``compare.compare``)"""
    import json
    import os
    import re

    from artwork_v2 import textmodel
    out = {}
    for name in sorted(os.listdir(path)):
        m = re.match(r"^p(\d+)_([ab])\.json$", name)
        if not m:
            continue
        with open(os.path.join(path, name), encoding="utf-8") as f:
            data = json.load(f)
        if "pages" not in data and "fullTextAnnotation" in data:
            data = data["fullTextAnnotation"]
        pg = (data.get("pages") or [{}])[0]
        W, H = int(pg.get("width") or 0), int(pg.get("height") or 0)
        lines = textmodel.parse(data, W, H)["lines"]
        out.setdefault(int(m.group(1)), {"source": "raw"})[m.group(2).upper()] = (W, H, lines)
    if out:
        return out
    log = os.path.join(path, "log.txt")
    if os.path.isfile(log):
        for n, sides in load_log(log).items():
            out[n] = {"source": "log"}
            for s, (W, H, items) in sides.items():
                out[n][s] = (W, H, textmodel.parse(fta_from_lines(items, W, H), W, H)["lines"])
    return out


def load_log(path):
    """อ่าน Log ที่กด "คัดลอก Log" → {n: {"A": (W, H, [(text, box, conf, angle)]), "B": …}}"""
    import re
    rx_pair = re.compile(r"^\[PAIR (\d+)\]")
    rx_side = re.compile(r"^\s+([AB]): page=.*?sent_px=\[(\d+), (\d+)\]")
    rx_line = re.compile(r'^\s+([AB])\d+ conf=([0-9.-]+) min=\S+ ang=([0-9.-]+) '
                         r'box=\[([0-9,-]+)\] "(.*)"(?: \[soft-hyphen\])?\s*$')
    rx_merge = re.compile(r'^\s+([AB]): "(.*)" \+ "(.*)"\s*$')
    rx_split = re.compile(r'^\s+([AB]): "(.*)" \u2016 "(.*)"\s*$')
    out, n, merges, splits = {}, None, {}, {}
    with open(path, encoding="utf-8") as f:
        for raw in f:
            m = rx_pair.match(raw)
            if m:
                n = int(m.group(1))
                out[n] = {"A": [0, 0, []], "B": [0, 0, []]}
                merges[n] = []
                splits[n] = []
                continue
            m = rx_split.match(raw) if n is not None else None
            if m:
                splits[n].append((m.group(1), m.group(2), m.group(3)))
                continue
            m = rx_merge.match(raw) if n is not None else None
            if m:
                merges[n].append((m.group(1), m.group(2), m.group(3)))
                continue
            if n is None:
                continue
            m = rx_side.match(raw)
            if m:
                out[n][m.group(1)][0] = int(m.group(2))
                out[n][m.group(1)][1] = int(m.group(3))
                continue
            m = rx_line.match(raw)
            if m:
                conf = 0.98 if m.group(2) == "-" else float(m.group(2))
                ang = None if m.group(3) == "-" else float(m.group(3))
                box = tuple(float(v) for v in m.group(4).split(","))
                out[n][m.group(1)][2].append((m.group(5), box, conf, ang))
    # row_splits เกิด **หลัง** การต่อแถว ⇒ ต่อชิ้นที่ถูกแยกกลับก่อน (ชิ้นซ้าย-ขวาอยู่ติดกันใน Log)
    for k, d in out.items():
        for side, left, right in reversed(splits.get(k, [])):
            ls = d[side][2]
            for idx in range(len(ls) - 1):
                if ls[idx][0] != left or ls[idx + 1][0] != right:
                    continue
                (lt, lb, lc, la), (rt, rb, rc, _ra) = ls[idx], ls[idx + 1]
                box = (min(lb[0], rb[0]), min(lb[1], rb[1]), max(lb[2], rb[2]), max(lb[3], rb[3]))
                conf = (lc * len(lt) + rc * len(rt)) / float(len(lt) + len(rt))
                ls[idx:idx + 2] = [(lt + " " + rt, box, conf, la)]
                break
    # บรรทัดใน Log เป็นบรรทัด **หลังต่อแถว** — แยกกลับเป็นชิ้นตามรายการ row_merges
    # (กรอบแบ่งตามสัดส่วนจำนวนตัวอักษร) ให้ compare ต่อเองเหมือนบนสถานี
    for k, d in out.items():
        for side, left, right in reversed(merges.get(k, [])):
            ls = d[side][2]
            for idx, (text, box, conf, ang) in enumerate(ls):
                if text not in (left + " " + right, left + " \u2026 " + right):
                    continue          # แบบที่สอง = ต่อด้วยหลักฐานจากอีกฝั่ง (… สังเคราะห์)
                cut = box[0] + (box[2] - box[0]) * len(left) / float(len(text))
                gap = 0.25 * (box[3] - box[1])
                ls[idx:idx + 1] = [(left, (box[0], box[1], cut - gap / 2, box[3]), conf, ang),
                                   (right, (cut + gap / 2, box[1], box[2], box[3]), conf, ang)]
                break
    return {k: {s: tuple(v) for s, v in d.items()} for k, d in out.items()
            if d["A"][2] or d["B"][2]}
