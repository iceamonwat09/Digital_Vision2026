"""แปลง ``fullTextAnnotation`` ของ Vision → บรรทัดที่มีกรอบระดับตัวอักษร

ข้อเท็จจริงจากสเปก (text_annotation.proto):
* ไม่มีวัตถุระดับ "บรรทัด" — ต้องประกอบเองจาก ``detectedBreak`` ของตัวอักษร
  SPACE / SURE_SPACE = เว้นวรรค · EOL_SURE_SPACE = ตัดบรรทัด ·
  HYPHEN = ตัดคำท้ายบรรทัด (ขีดไม่อยู่ในข้อความ) · LINE_BREAK = จบย่อหน้า
* JSON ละพิกัดที่เป็น 0 ⇒ ต้องเติม 0 เอง ไม่งั้นคำชิดขอบภาพหาย/พัง
* ลำดับจุดของกรอบ = มุมซ้ายบนของ "ทิศอ่านของข้อความ" ⇒ ได้มุมของคำฟรี
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Dict, List, Optional, Tuple

Box = Tuple[float, float, float, float]

SKIP_BLOCK_TYPES = {"PICTURE", "BARCODE", "RULER"}
SPACE_BREAKS = {"SPACE", "SURE_SPACE"}
LINE_BREAKS = {"EOL_SURE_SPACE", "LINE_BREAK", "HYPHEN"}


def poly_points(bp: Optional[dict], W: int, H: int) -> List[Tuple[float, float]]:
    bp = bp or {}
    vs = bp.get("vertices") or []
    if vs:
        return [(float(v.get("x", 0) or 0), float(v.get("y", 0) or 0)) for v in vs]
    nv = bp.get("normalizedVertices") or []
    return [(float(v.get("x", 0) or 0) * W, float(v.get("y", 0) or 0) * H) for v in nv]


def poly_box(bp: Optional[dict], W: int, H: int) -> Optional[Box]:
    pts = poly_points(bp, W, H)
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, y0 = max(0.0, min(xs)), max(0.0, min(ys))
    x1, y1 = min(float(W), max(xs)), min(float(H), max(ys))
    if x1 < x0 or y1 < y0:
        return None
    return (x0, y0, x1, y1)


def poly_angle(bp: Optional[dict], W: int, H: int) -> Optional[float]:
    pts = poly_points(bp, W, H)
    if len(pts) < 2:
        return None
    (x0, y0), (x1, y1) = pts[0], pts[1]
    if x0 == x1 and y0 == y1:
        return None
    return round(math.degrees(math.atan2(y1 - y0, x1 - x0)) % 360, 1)


def union(boxes) -> Optional[Box]:
    bs = [b for b in boxes if b]
    if not bs:
        return None
    return (min(b[0] for b in bs), min(b[1] for b in bs),
            max(b[2] for b in bs), max(b[3] for b in bs))


def _brk(sym: dict) -> Tuple[Optional[str], bool]:
    db = ((sym.get("property") or {}).get("detectedBreak") or {})
    return db.get("type"), bool(db.get("isPrefix"))


def _langs(prop: Optional[dict]) -> List[str]:
    out = []
    for l in ((prop or {}).get("detectedLanguages") or []):
        code = l.get("languageCode")
        if code:
            out.append(code)
    return out


def parse(fta: dict, W: int, H: int) -> dict:
    """คืน ``{"lines": [...], "stats": {...}}`` — ไม่โยน exception กับข้อมูลแปลก"""
    lines: List[dict] = []
    st = Counter()
    blocks_by_type: Counter = Counter()
    skipped_boxes: List[Box] = []
    confs: List[float] = []
    lang_counter: Counter = Counter()
    angles: List[float] = []
    page_info = []

    cur: Dict = {}

    def new_line(block_type):
        return {"chars": [], "block_type": block_type, "soft_hyphen": False,
                "word_confs": [], "angles": [], "langs": Counter()}

    def end_line():
        nonlocal cur
        if cur and cur["chars"]:
            # ตัดช่องว่างท้าย
            while cur["chars"] and cur["chars"][-1]["c"] == " ":
                cur["chars"].pop()
            if cur["chars"]:
                lines.append(cur)
        cur = {}

    for pi, page in enumerate((fta or {}).get("pages") or []):
        page_info.append({"width": page.get("width"), "height": page.get("height"),
                          "confidence": page.get("confidence"),
                          "langs": _langs(page.get("property"))})
        for code in _langs(page.get("property")):
            lang_counter[code] += 1
        for block in page.get("blocks") or []:
            btype = block.get("blockType") or "UNKNOWN"
            blocks_by_type[btype] += 1
            if btype in SKIP_BLOCK_TYPES:
                b = poly_box(block.get("boundingBox"), W, H)
                if b:
                    skipped_boxes.append(b)
                continue
            for para in block.get("paragraphs") or []:
                st["paragraphs"] += 1
                end_line()
                for word in para.get("words") or []:
                    st["words"] += 1
                    if not cur:
                        cur = new_line(btype)
                    wc = word.get("confidence")
                    if wc is not None:
                        cur["word_confs"].append(float(wc))
                    a = poly_angle(word.get("boundingBox"), W, H)
                    if a is not None:
                        cur["angles"].append(a)
                        angles.append(a)
                    for code in _langs(word.get("property")):
                        cur["langs"][code] += 1
                    wbox = poly_box(word.get("boundingBox"), W, H)
                    syms = word.get("symbols") or []
                    for si, sym in enumerate(syms):
                        st["symbols"] += 1
                        txt = sym.get("text") or ""
                        conf = sym.get("confidence")
                        conf = None if conf is None else float(conf)
                        if conf is not None:
                            confs.append(conf)
                        box = poly_box(sym.get("boundingBox"), W, H) or wbox
                        btype_brk, prefix = _brk(sym)
                        if prefix and btype_brk in SPACE_BREAKS and cur["chars"] \
                                and cur["chars"][-1]["c"] != " ":
                            cur["chars"].append({"c": " ", "box": None, "conf": None})
                        for ch in txt:
                            cur["chars"].append({"c": ch, "box": box, "conf": conf})
                        if prefix:
                            continue
                        if btype_brk in SPACE_BREAKS:
                            cur["chars"].append({"c": " ", "box": None, "conf": None})
                        elif btype_brk in LINE_BREAKS:
                            st["break_" + btype_brk] += 1
                            if btype_brk == "HYPHEN":
                                cur["soft_hyphen"] = True
                            end_line()
                            if si < len(syms) - 1:           # ตัดกลางคำ (ไม่ควรเกิด)
                                cur = new_line(btype)
                    if not cur:
                        continue
                end_line()

    out_lines = []
    for i, ln in enumerate(lines):
        chars = ln["chars"]
        boxes = [c["box"] for c in chars if c["box"]]
        bx = union(boxes)
        cconf = [c["conf"] for c in chars if c["conf"] is not None]
        heights = sorted(b[3] - b[1] for b in boxes) or [0.0]
        text = "".join(c["c"] for c in chars)
        ang = sorted(ln["angles"])
        out_lines.append({
            "i": i,
            "text": text,
            "chars": chars,
            "box": bx,
            "height": heights[len(heights) // 2],
            "center": (((bx[0] + bx[2]) / 2 / max(1, W), (bx[1] + bx[3]) / 2 / max(1, H))
                       if bx else (0.5, 0.5)),
            "conf_mean": (sum(cconf) / len(cconf)) if cconf else None,
            "conf_min": min(cconf) if cconf else None,
            "block_type": ln["block_type"],
            "soft_hyphen": ln["soft_hyphen"],
            "angle": ang[len(ang) // 2] if ang else None,
            "langs": [k for k, _ in ln["langs"].most_common(2)],
        })

    n_low = sum(1 for c in confs if c < 0.6)
    stats = {
        "pages": len(page_info),
        "page_info": page_info,
        "blocks": sum(blocks_by_type.values()),
        "blocks_by_type": dict(blocks_by_type),
        "paragraphs": st["paragraphs"],
        "words": st["words"],
        "symbols": st["symbols"],
        "lines": len(out_lines),
        "chars": sum(len(l["text"].replace(" ", "")) for l in out_lines),
        "conf_mean": round(sum(confs) / len(confs), 4) if confs else None,
        "conf_min": round(min(confs), 4) if confs else None,
        "low_conf_frac": round(n_low / len(confs), 4) if confs else None,
        "langs": [k for k, _ in lang_counter.most_common(5)],
        "breaks": {k[6:]: v for k, v in st.items() if k.startswith("break_")},
        "soft_hyphen_lines": sum(1 for l in out_lines if l["soft_hyphen"]),
        "angles": dict(Counter(int(round(a / 90.0)) * 90 % 360 for a in angles)),
        "skipped_blocks": len(skipped_boxes),
        "skipped_boxes": skipped_boxes,
        "text_len": len((fta or {}).get("text") or ""),
    }
    return {"lines": out_lines, "stats": stats}
