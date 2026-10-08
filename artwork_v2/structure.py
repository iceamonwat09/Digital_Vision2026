"""กติกาโครงสร้างของการเทียบข้อความ (7 ต.ค. — ข้อสรุปทีมวิเคราะห์ Log สถานีทุกชุด)

ต้นเหตุของจุดแดงหลอกส่วนใหญ่บนสถานี = ตัวเทียบเชื่อ "โครงบรรทัด" ที่ Vision ส่งมา
(รวม/แยก/ย้ายบรรทัดคนละแบบสองฝั่ง · จับคู่บรรทัดผิดแถว) ไม่ใช่ตัวอักษรที่อ่านผิด
⇒ โมดูลนี้แก้ **โครงสร้าง** เท่านั้น — ทุกกติกาอ้างว่า "ข้อความเหมือนกันทุกตัวอักษร"
หรือ "จับคู่ให้ถูกตำแหน่ง" จึงไม่มีทางทำให้ความต่างจริงหายไป (วัดบน 12 ชุดข้อมูลสถานี:
ของจริงหาย 0 · ชุดกลายพันธุ์ John West 1310 กรณียังแดงเท่าเดิม 1307)

| กติกา (ธง · ``0`` = เดิมเป๊ะ) | ทำอะไร |
|---|---|
| ``GEO_PAIRING`` | ตำแหน่ง A→B จากคู่บรรทัดที่ตรงกันทุกตัวอักษร (แก้ด้วยเพื่อนบ้าน k ตัว) · คีย์สั้น ≤ 8 ตัว ต้องอยู่ห่างตำแหน่งที่ทำนายไม่เกิน 1.5 เท่าความสูงบรรทัด · คู่ที่เหลืออยู่ "ที่เดียวกัน" ถูกจับคู่ (``geo``) |
| ``RECOMPOSE`` | ชิ้นบรรทัดที่ถูกแยก ต่อกันเมื่อคีย์ที่ต่อแล้ว **เท่ากับ** บรรทัดของอีกฝั่งทุกตัวอักษร |
| ``MOVED`` | ข้อความเดียวกันถูกลบที่หนึ่งแล้วแทรกอีกที่ในคู่บรรทัดเดียวกัน ⇒ ``MOVED`` เหลือง · ส่วนที่เหลือถูกตัดสินใหม่ (``%24`` ↔ ``%20`` ยังแดง) |
| ``RELOCATE`` | ข้อความที่เกินมาฝั่งเดียว มีอยู่ในอีกฝั่ง **ที่ตำแหน่งเดียวกันบนภาพ** ⇒ ไม่ใช่ความต่าง (พับ — ไม่ลบ) |
| ``BALANCED`` | บรรทัดหาย + บรรทัดเกินที่ข้อความเท่ากันทุกตัวอักษรในโซนเดียวกัน ⇒ เหลือง |

ข้อความแนวตั้ง (``VERTICAL_UPRIGHT``) และเครื่องหมายคำพูด (``QUOTE_PUNCT``) อยู่ใน
``compare.curved_lines`` / ``compare.classify``

**ไม่ได้ย้ายมา (ตั้งใจ):** กติกาลดระดับด้วยข้อความ (cover · ตารางสมมูลอักษรอาหรับ ·
ต่างแค่จุด · สลับทิศ · ตัวอักษร↔ตัวเลข) — วัดแล้วทำให้การแก้อักษรอาหรับจริงกลายเป็นเหลือง
(106/106 · 69/118) ⇒ คู่ PDF ใช้ชั้นหลักฐานภาพ (``pixverify``) แทน
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from . import compare as C
from . import config

SHORT = 8          # คีย์ยาวไม่เกินนี้ = "สั้น" — ข้อความอย่างเดียวบอกตำแหน่งไม่ได้
G = 1.5            # ระยะคลาดที่ยอม (เท่าความสูงบรรทัด)


# ── เรขาคณิต ─────────────────────────────────────────────────────────

def _ctr(b):
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def _h(ln: dict) -> float:
    b = ln.get("box")
    return max(4.0, (b[3] - b[1]) if b else 10.0)


def _fit1(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    vx = sum((x - mx) ** 2 for x in xs)
    if vx < 1e-9:
        return 1.0, my - mx
    s = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx
    return s, my - s * mx


class Geo:
    """A→B: สเกล+เลื่อนตามแกน (ตัดค่าผิดปกติ 4 รอบ) + แก้เฉพาะที่ด้วยเพื่อนบ้าน k ตัว"""

    def __init__(self, P):              # P = [(boxA, boxB, hA)]
        self.ok = len(P) >= 3
        self.anchors: list = []
        if not self.ok:
            return
        keep = list(range(len(P)))
        for _ in range(4):
            xa = [_ctr(P[k][0])[0] for k in keep]
            xb = [_ctr(P[k][1])[0] for k in keep]
            ya = [_ctr(P[k][0])[1] for k in keep]
            yb = [_ctr(P[k][1])[1] for k in keep]
            self.sx, self.tx = _fit1(xa, xb)
            self.sy, self.ty = _fit1(ya, yb)
            res = [self._gres(P[k]) for k in range(len(P))]
            srt = sorted(res[k] for k in keep)
            med = srt[len(srt) // 2]
            keep = [k for k in range(len(P)) if res[k] <= max(1.0, 4 * med)] or keep
        for k in keep:
            ca, cb = _ctr(P[k][0]), _ctr(P[k][1])
            g = (self.sx * ca[0] + self.tx, self.sy * ca[1] + self.ty)
            self.anchors.append((ca, (cb[0] - g[0], cb[1] - g[1])))

    def _gres(self, p):
        ca, cb = _ctr(p[0]), _ctr(p[1])
        return ((self.sx * ca[0] + self.tx - cb[0]) ** 2
                + (self.sy * ca[1] + self.ty - cb[1]) ** 2) ** .5 / p[2]

    def _local(self, c, k=4):
        if not self.anchors:
            return (0.0, 0.0)
        near = sorted(self.anchors,
                      key=lambda a: (a[0][0] - c[0]) ** 2 + (a[0][1] - c[1]) ** 2)[:k]
        dx = sorted(a[1][0] for a in near)
        dy = sorted(a[1][1] for a in near)
        return dx[len(dx) // 2], dy[len(dy) // 2]

    def box(self, b):
        lx, ly = self._local(_ctr(b))
        return (self.sx * b[0] + self.tx + lx, self.sy * b[1] + self.ty + ly,
                self.sx * b[2] + self.tx + lx, self.sy * b[3] + self.ty + ly)

    def estimate(self, b, h: float) -> Optional[dict]:
        """ตำแหน่งที่น่าจะเป็นของกรอบ ``b`` บนอีกฝั่ง — **แสดงผลล้วน** (ซูมไปดูฝั่งที่ไม่พบข้อความ)

        ให้ค่าเฉพาะเมื่อเชื่อได้: คู่ยึด ≥ 3 · สเกลสมเหตุสมผล · คู่ยึดใกล้สุด 4 คู่ให้การเลื่อน
        เฉพาะที่ไปทางเดียวกัน (ต่างจากค่ากลางไม่เกิน ``G`` เท่าความสูงบรรทัด) — ไม่งั้น ``None`` (ไม่เดา)"""
        if len(self.anchors) < 3 or not (0.3 <= self.sx <= 3.0 and 0.3 <= self.sy <= 3.0):
            return None
        c = _ctr(b)
        near = sorted(self.anchors,
                      key=lambda a: (a[0][0] - c[0]) ** 2 + (a[0][1] - c[1]) ** 2)[:4]
        lx, ly = self._local(c)
        ht = max(4.0, h * self.sy)
        spread = max(((a[1][0] - lx) ** 2 + (a[1][1] - ly) ** 2) ** .5 for a in near) / ht
        if spread > G:
            return None
        return {"box": [round(v, 1) for v in self.box(b)], "spread": round(spread, 2), "anchors": len(near)}

    def res(self, la: dict, lb: dict) -> float:
        """ระยะ (เท่าความสูงบรรทัดของ A) ระหว่างจุดกลางที่ทำนายกับจุดกลางจริงของ B"""
        if not (la.get("box") and lb.get("box")):
            return 1e9
        pa = _ctr(self.box(la["box"]))
        cb = _ctr(lb["box"])
        return ((pa[0] - cb[0]) ** 2 + (pa[1] - cb[1]) ** 2) ** .5 / _h(la)


def _inter(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _area(a) -> float:
    return max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))


def cover(a, b) -> float:
    """สัดส่วนของกรอบ a ที่อยู่ในกรอบ b"""
    return _inter(a, b) / _area(a)


def _anchor_pairs(A, B):
    pairs, _, _ = C.pair_lines(A, B)
    return [(i, j) for i, j, m, _ in pairs
            if m == "exact" and len(A[i]["dk"]) > SHORT and A[i].get("box") and B[j].get("box")]


def build_geo(A: List[dict], B: List[dict]) -> Tuple[Optional[Geo], Optional[Geo]]:
    """คู่ (A→B, B→A) · คู่บรรทัดยึดไม่ถึง 3 คู่ ⇒ ``(None, None)`` = ไม่ใช้ตำแหน่งเลย"""
    ap = _anchor_pairs(A, B)
    g = Geo([(A[i]["box"], B[j]["box"], _h(A[i])) for i, j in ap])
    gi = Geo([(B[j]["box"], A[i]["box"], _h(B[j])) for i, j in ap])
    return (g, gi) if g.ok and gi.ok else (None, None)


# ── จับคู่บรรทัดตามตำแหน่ง ────────────────────────────────────────────

def pair_lines_geo(A: List[dict], B: List[dict], g: Optional[Geo]):
    if g is None:
        return C.pair_lines(A, B)
    ua, ub = set(range(len(A))), set(range(len(B)))
    pairs = []

    def take(cands, method):
        cands.sort(key=lambda t: -t[0])
        for score, ia, ib in cands:
            if ia in ua and ib in ub:
                ua.discard(ia)
                ub.discard(ib)
                pairs.append((ia, ib, method, round(score, 4)))

    def short(i, j):
        return min(len(A[i]["dk"]), len(B[j]["dk"])) <= SHORT

    by_key: dict = {}
    for j in ub:
        by_key.setdefault(B[j]["dk"], []).append(j)
    cands = []
    for i in ua:
        if not A[i]["letters"]:
            continue
        for j in by_key.get(A[i]["dk"], []):
            d = g.res(A[i], B[j])
            if short(i, j) and d > G:
                continue
            cands.append((100.0 - min(d, 99.0), i, j))
    take(cands, "exact")

    cands = []
    for i in ua:
        if not A[i]["letters"]:
            continue
        for j in ub:
            if not B[j]["letters"]:
                continue
            s = C.similarity(A[i]["pk"], B[j]["pk"])
            if s < config.PAIR_MIN_SIM:
                continue
            d = g.res(A[i], B[j])
            if short(i, j) and d > G:
                continue
            cands.append((s - 0.25 * min(1.0, d / 40.0), i, j))
    take(cands, "content")

    cands = []
    for i in ua:
        if A[i]["letters"]:
            continue
        for j in ub:
            if B[j]["letters"]:
                continue
            d = g.res(A[i], B[j])
            if d <= G:
                cands.append((1.0 - d / 10.0, i, j))
    take(cands, "position")

    # ที่เหลือ: อยู่ "ที่เดียวกัน" บนภาพ (ความสูงบรรทัดไม่ต่างเกิน 2 เท่า)
    cands = []
    for i in ua:
        if not A[i].get("box"):
            continue
        pa = g.box(A[i]["box"])
        for j in ub:
            if not B[j].get("box"):
                continue
            bb = B[j]["box"]
            hr = max(_h(A[i]), _h(B[j])) / min(_h(A[i]), _h(B[j]))
            iou = _inter(pa, bb) / (_area(pa) + _area(bb) - _inter(pa, bb))
            d = g.res(A[i], B[j])
            if hr <= 2.0 and (iou >= 0.3 or d <= 1.0):
                cands.append((iou - d / 10.0, i, j))
    take(cands, "geo")
    return pairs, sorted(ua), sorted(ub)


# ── ต่อชิ้นบรรทัดที่ถูกแยก (ต้องเท่ากันทุกตัวอักษร) ──────────────────────

def recompose(X, Y, gXY: Geo, ux, side: str, merges) -> bool:
    """บรรทัด x ที่ไม่มีคู่ อยู่ในกรอบบรรทัด y ของอีกฝั่ง · ชิ้นทั้งหมดของ X ที่อยู่ใน y
    ต่อกันแล้ว **เท่ากับ** คีย์ของ y ⇒ Vision แยกบรรทัดเดียวเป็นหลายชิ้น ⇒ ต่อกลับ"""
    for u in sorted(ux):
        if not X[u].get("box"):
            continue
        mu = gXY.box(X[u]["box"])
        for y in range(len(Y)):
            yb = Y[y].get("box")
            if not yb or cover(mu, yb) < 0.6:
                continue
            S = [k for k in range(len(X))
                 if X[k].get("box") and cover(gXY.box(X[k]["box"]), yb) >= 0.6]
            if len(S) < 2 or u not in S:
                continue
            for order in (sorted(S, key=lambda k: X[k]["box"][0]),
                          sorted(S, key=lambda k: -X[k]["box"][0]), sorted(S)):
                if "".join(X[k]["dk"] for k in order) != Y[y]["dk"]:
                    continue
                m = X[order[0]]
                for k in order[1:]:
                    if merges is not None:
                        # ทีละสองชิ้น (รูปแบบเดียวกับการต่อแถว — ตัวโหลด Log แยกกลับได้)
                        merges.append({"side": side, "left": m["text"], "right": X[k]["text"],
                                       "via": "recompose", "evidence": Y[y]["text"]})
                    m = C._merge_two(m, X[k])
                m = C._reprep(m)
                m["recomposed"] = True
                keep = min(S)
                for k in sorted(S, reverse=True):
                    if k != keep:
                        del X[k]
                X[keep] = m
                return True
    return False


# ── แยกบรรทัดที่ Vision รวมข้ามคอลัมน์ (``SPLIT_MERGED``) ─────────────────────

def _cut_line(x: dict, t: int):
    """ตัดบรรทัดที่ตำแหน่งข้อความ ``t`` (ต้องตรงรอยต่อของตัวอักษร) ⇒ ``(ซ้าย, ขวา)`` หรือ ``None``"""
    pos, ci = 0, None
    for k, c in enumerate(x["chars"]):
        if pos == t:
            ci = k
            break
        pos += len(c["c"])
    if ci is None:
        return None

    def piece(chars):
        while chars and not chars[0]["c"].strip():
            chars = chars[1:]
        while chars and not chars[-1]["c"].strip():
            chars = chars[:-1]
        boxes = [c["box"] for c in chars if c.get("box")]
        if not chars or not boxes:
            return None
        cc = [c["conf"] for c in chars if c.get("conf") is not None]
        bx = C.union(boxes)
        cen = x.get("center") or (0.5, 0.5)
        ob = x["box"]
        ocx, ocy = (ob[0] + ob[2]) / 2.0, (ob[1] + ob[3]) / 2.0
        ncx, ncy = (bx[0] + bx[2]) / 2.0, (bx[1] + bx[3]) / 2.0
        out = dict(x)
        out.pop("parts", None)
        out.update(chars=list(chars), text="".join(c["c"] for c in chars), box=bx,
                   center=(cen[0] * ncx / ocx if ocx else cen[0], cen[1] * ncy / ocy if ocy else cen[1]),
                   conf_mean=(sum(cc) / len(cc)) if cc else None,
                   conf_min=min(cc) if cc else None, split_from=x["text"])
        return C._reprep(out)
    L, R = piece(x["chars"][:ci]), piece(x["chars"][ci:])
    return (L, R) if L is not None and R is not None and L["dk"] and R["dk"] else None


def _same_place(mb, pb) -> bool:
    """กรอบที่ทำนายจากอีกฝั่ง กับกรอบชิ้นที่ตัด อยู่แถวเดียวกันและซ้อนกันแนวนอนเกินครึ่ง"""
    h = max(4.0, pb[3] - pb[1])
    if abs(_ctr(mb)[1] - _ctr(pb)[1]) > h:
        return False
    ov = min(mb[2], pb[2]) - max(mb[0], pb[0])
    return ov >= 0.5 * min(mb[2] - mb[0], pb[2] - pb[0])


def split_merged(X, Y, gYX: Geo, side: str, splits) -> bool:
    """Vision อ่านสองคอลัมน์ที่อยู่แถวเดียวกันเป็น **บรรทัดเดียว** ในฝั่ง ``X`` แต่อีกฝั่งอ่านแยก
    (Friskies: 🅱 ``6 kcal … 100 กรัม ให้ 1 ซอง … 4 กก.)`` = 🅰 สองบรรทัดคนละคอลัมน์) ⇒
    บรรทัดที่รวมจับคู่ได้แค่คอลัมน์เดียว อีกคอลัมน์กลายเป็น "หายไป/เกินมา" แดงทั้งบรรทัด

    แยกเมื่อครบทุกข้อ (ไม่แตะตัวอักษร — แค่แบ่งบรรทัด ⇒ ความต่างจริงยังถูกเทียบครบ):
    * ต้นหรือท้ายของ x **เท่ากับ** บรรทัด y ของอีกฝั่งทุกตัวอักษร (คีย์ยาวเกิน ``SHORT``) และส่วนที่เหลือ
      มีตัวอักษร/ตัวเลข ≥ 3 ตัว
    * รอยตัดอยู่ตรงช่องว่าง (ไม่ตัดกลางคำ)
    * ไม่มีบรรทัดอื่นในฝั่ง X ที่เท่ากับ y อยู่แล้ว (กันการนับซ้ำของบรรทัดที่พิมพ์ซ้ำหลายแผง)
    * ตำแหน่งของ y ที่ทำนายมาฝั่ง X อยู่ **ที่ชิ้นนั้นพอดี** (แถวเดียวกัน · ซ้อนแนวนอนเกินครึ่ง)
    """
    ydk = {y["dk"] for y in Y}
    xdk = {x["dk"] for x in X}
    order = sorted(range(len(Y)), key=lambda j: -len(Y[j]["dk"]))
    for i, x in enumerate(X):
        if not x.get("box") or x.get("parts") or x["dk"] in ydk:
            continue
        for j in order:
            y = Y[j]
            k = y["dk"]
            if len(k) <= SHORT or not y.get("box") or k in xdk or k == x["dk"]:
                continue
            if x["dk"].startswith(k):
                head, cut = True, len(k)
                rest = x["dk"][cut:]
            elif x["dk"].endswith(k):
                head, cut = False, len(x["dk"]) - len(k)
                rest = x["dk"][:cut]
            else:
                continue
            if sum(1 for c in rest if c.isalnum()) < 3:
                continue
            t = x["dk_idx"][cut]
            if t <= 0 or not x["text"][t - 1].isspace():
                continue
            lr = _cut_line(x, t)
            if lr is None:
                continue
            piece = lr[0] if head else lr[1]
            if piece["dk"] != k or not _same_place(gYX.box(y["box"]), piece["box"]):
                continue
            X[i:i + 1] = list(lr)
            if splits is not None:
                splits.append({"side": side, "left": lr[0]["text"], "right": lr[1]["text"],
                               "evidence": y["text"]})
            return True
    return False


# ── ข้อความเดียวกันย้ายที่ในคู่บรรทัดเดียวกัน ─────────────────────────────

def mark_moved(fs: List[dict]) -> None:
    keys = [(C.diff_key_map(x["a"]["frag"])[0], C.diff_key_map(x["b"]["frag"])[0]) for x in fs]
    for k, x in enumerate(fs):
        ka, kb = keys[k]
        if bool(ka) == bool(kb):
            continue
        frag = ka or kb
        if len(frag) < 2 or C._is_punct(frag):
            continue
        for m, y in enumerate(fs):
            if m == k:
                continue
            ya, yb = keys[m]
            host = yb if ka else ya
            if frag in host:
                x["class"] = "MOVED"
                x["_cap"] = "moved"
                if ka:
                    ra, rb = ya, yb.replace(frag, "", 1)
                else:
                    ra, rb = ya.replace(frag, "", 1), yb
                if ra == rb:
                    y["class"] = "MOVED"
                    y["_cap"] = "moved"
                elif C._is_punct(ra + rb):
                    y["class"] = "PUNCT"
                    y["_cap"] = "moved_punct"
                break


# ── ข้อความที่มีอยู่ในอีกฝั่ง ตรงตำแหน่งเดียวกันบนภาพ ─────────────────────

def relocate(findings, A, B, ua, ub, g: Geo, gi: Geo):
    """คืน ``(คงไว้, พับ, บรรทัดที่ยกเลิก {"A": set, "B": set})``"""
    cancelled = {"A": set(), "B": set()}
    keep, moved = [], []
    for x in findings:
        ka = C.diff_key_map(x["a"]["frag"])[0]
        kb = C.diff_key_map(x["b"]["frag"])[0]
        done = False
        if bool(ka) != bool(kb) and len(ka or kb) >= 3 and not C._is_punct(ka or kb):
            if ka:
                abox = A[x["a"]["line"]]["box"]
                for u in ub:
                    if u in cancelled["B"] or B[u]["dk"] != ka or not B[u].get("box") or not abox:
                        continue
                    if cover(gi.box(B[u]["box"]), abox) >= 0.6:
                        cancelled["B"].add(u)
                        x["reloc_into"] = B[u]["text"]
                        done = True
                        break
            else:
                bbox = B[x["b"]["line"]]["box"]
                for u in ua:
                    if u in cancelled["A"] or A[u]["dk"] != kb or not A[u].get("box") or not bbox:
                        continue
                    if cover(g.box(A[u]["box"]), bbox) >= 0.6:
                        cancelled["A"].add(u)
                        x["reloc_into"] = A[u]["text"]
                        done = True
                        break
        if not done and bool(ka) != bool(kb):
            frag = ka or kb
            core = "".join(c for c in frag if c.isalnum())
            fb_ = x["a"]["box"] if ka else x["b"]["box"]
            if len(core) >= 3 and fb_:
                gm, others, own = (g, B, x["b"]["line"]) if ka else (gi, A, x["a"]["line"])
                mb = gm.box(fb_)
                for k, o in enumerate(others):
                    if k == own or not o.get("box"):
                        continue
                    if cover(mb, o["box"]) >= 0.5 and \
                            core in "".join(c for c in o["dk"] if c.isalnum()):
                        x["reloc_into"] = o["text"]
                        done = True
                        break
        (moved if done else keep).append(x)
    return keep, moved, cancelled


def mark_balanced(findings: List[dict]) -> None:
    miss = [f for f in findings if f["class"] == "MISSING_IN_B"]
    extra = [f for f in findings if f["class"] == "EXTRA_IN_B"]
    used = set()
    for m in miss:
        km = C.diff_key_map(m["a"]["text"])[0]
        for e in extra:
            if id(e) in used:
                continue
            if C.diff_key_map(e["b"]["text"])[0] == km:
                used.add(id(e))
                m["_cap"] = "balanced"
                e["_cap"] = "balanced"
                break


CAP_NOTE = {
    "moved": "ข้อความเดียวกันย้ายตำแหน่งในบรรทัด (มีครบทั้งสองฝั่ง) — ดูด้วยตา",
    "moved_punct": "ส่วนที่เหลือหลังข้อความย้ายที่ต่างแค่เครื่องหมาย",
    "balanced": "ข้อความเดียวกันทุกตัวอักษรอยู่อีกบรรทัดของอีกฝั่ง (OCR จัดบรรทัดต่างกัน)",
}
