"""วัดผลชั้นเทียบของ Artwork V2 แบบออฟไลน์ — เล่นซ้ำจากผลดิบของ Vision (ไม่ยิง Vision/AI แม้แต่ครั้งเดียว)

ตอบคำถาม "แก้แล้วดีขึ้นจริงไหม" ด้วยตัวเลข แทนการเดา:

* เล่นซ้ำตามลำดับเดียวกับ ``pipeline._run`` — ประกอบบรรทัด → เทียบ → พื้นที่ยกเว้น → ลดระดับเมื่อปิดการอ่านซ้ำ →
  พับเครื่องหมายไม่ชัด → ยุบการ์ดโค้ง → เลขจุด → หลักฐานภาพ (ถ้ามีไฟล์ PDF ต้นฉบับ) → ผลตัดสิน
  (ข้ามเฉพาะ: การอ่านซ้ำที่ต้องยิง Vision · AI)
* เทียบกับ **เฉลย** (``labels.json`` ที่ตรวจด้วยตา หรือ ``review.json`` ที่กด ✓/⚑ บนหน้าเว็บ)
  ⇒ แดง/เหลืองที่เป็นของจริงกี่จุด · Noise กี่จุด · ของจริงที่หลุด
* ``--set FLAG=V`` ตั้งค่าธง · ``--compare FLAG=V`` เล่นซ้ำสองชุด (ค่าปัจจุบัน vs ค่าที่ให้) แล้วบอกจุดที่เปลี่ยน
* ``--hints`` แนะนำ ``ARTWORK_V2_LANGUAGE_HINTS`` จากภาษาที่ Vision ตรวจพบในผลดิบ (ภาษาที่ไม่ใช่อักษรละติน + en)

ใช้::

    py -3.9 artwork_v2_eval.py data\\artwork_v2\\jobs\\<งาน>\\run_003
    py -3.9 artwork_v2_eval.py data\\artwork_v2\\jobs\\<งาน>\\run_003 --compare REFLOW_CONSERVE=0
    py -3.9 artwork_v2_eval.py tests\\data\\artwork_v2\\friskies\\raw_20261008_213541 --labels ...\\labels.json
    py -3.9 artwork_v2_eval.py data\\artwork_v2\\jobs\\<งาน>\\run_003 --hints

โฟลเดอร์ที่รับ: (ก) โฟลเดอร์รอบตรวจของสถานี (``raw/`` + ``result.json`` · ไฟล์ต้นฉบับอยู่ในโฟลเดอร์งาน
ด้านบน ⇒ ตรวจด้วยภาพได้) หรือ (ข) โฟลเดอร์ที่มี ``p<N>_a.json`` ``p<N>_b.json`` (+ ``zones.json`` +
ไฟล์ PDF ถ้ามี) · อ่านอย่างเดียว ไม่เขียนอะไรลงโฟลเดอร์ที่อ่าน (ภาพหลักฐานลงโฟลเดอร์ชั่วคราว)

exit: 0 = ไม่มีของจริงหลุด · 1 = มีของจริงหลุด (ตามเฉลย) · 2 = รันไม่ได้
"""
from __future__ import annotations

import argparse
import copy
import gzip
import json
import os
import re
import shutil
import sys
import tempfile
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from artwork_v2 import compare, config, pipeline, textmodel  # noqa: E402

LIST_KEYS = ("findings", "debris", "relocated", "pixel_same", "excluded", "lowmark")


# ── อ่านข้อมูล ───────────────────────────────────────────────────────────

def _load_json(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _fta(data: dict) -> dict:
    if "pages" not in data and "fullTextAnnotation" in data:
        return data["fullTextAnnotation"]
    return data


def load_dataset(path: str) -> dict:
    """→ {"pairs": {n: {"a": fta, "b": fta, "sides": {...}|None}}, "srcs": {"a": path, "b": path}|None,
    "review": {...}|None, "result": dict|None}"""
    raw_dir = os.path.join(path, "raw") if os.path.isdir(os.path.join(path, "raw")) else path
    pairs: dict = {}
    for name in sorted(os.listdir(raw_dir)):
        m = re.match(r"^p(\d+)_([ab])\.json(?:\.gz)?$", name)
        if m:
            pairs.setdefault(int(m.group(1)), {})[m.group(2)] = _fta(_load_json(os.path.join(raw_dir, name)))
    if not pairs:
        raise SystemExit("ไม่พบ p<N>_a.json / p<N>_b.json ใน %s" % raw_dir)
    result = None
    rp = os.path.join(path, "result.json")
    if os.path.isfile(rp):
        result = _load_json(rp)
        for p in result.get("pairs") or []:
            if p.get("n") in pairs:
                pairs[p["n"]]["sides"] = p.get("sides")
    zp = os.path.join(path, "zones.json")
    if os.path.isfile(zp):            # ชุดทดสอบ: {"<n>": {"a": {page,bbox,sent_px,rotate?,ignore?}, "b": …}}
        for n, sd in _load_json(zp).items():
            if int(n) in pairs:
                pairs[int(n)]["sides"] = sd
    srcs = None
    job = os.path.dirname(os.path.abspath(path))
    mp = os.path.join(job, "meta.json")
    if os.path.isfile(mp):
        meta = _load_json(mp)
        cand = {s: os.path.join(job, s + (meta.get("files", {}).get(s, {}).get("ext") or "")) for s in "ab"}
        if all(os.path.isfile(v) for v in cand.values()):
            srcs = cand
    if srcs is None:
        cand = {s: os.path.join(path, "src_%s.pdf" % s) for s in "ab"}
        if all(os.path.isfile(v) for v in cand.values()):
            srcs = cand
    rv = os.path.join(path, "review.json")
    review = _load_json(rv) if os.path.isfile(rv) else None
    return {"pairs": pairs, "srcs": srcs, "review": review, "result": result}


def _size(fta: dict, sides, s):
    if sides and (sides.get(s) or {}).get("sent_px"):
        return tuple(int(v) for v in sides[s]["sent_px"])
    pg = (fta.get("pages") or [{}])[0]
    return int(pg.get("width") or 0), int(pg.get("height") or 0)


# ── เล่นซ้ำ (ลำดับเดียวกับ pipeline._run) ────────────────────────────────

def replay(ds: dict, pixel: bool = True) -> dict:
    warnings: list = []
    pairs = []
    for n, d in sorted(ds["pairs"].items()):
        sides = copy.deepcopy(d.get("sides")) or {}
        pr = {"n": n, "sides": sides}
        if "a" not in d or "b" not in d:
            pr["unreadable"] = True
            pr["findings"] = []
            pairs.append(pr)
            continue
        sz = {s: _size(d[s], sides, s) for s in "ab"}
        for s in "ab":
            sides.setdefault(s, {})["sent_px"] = list(sz[s])
        parsed = {s: textmodel.parse(d[s], *sz[s]) for s in "ab"}
        cmp_ = compare.compare(parsed["a"]["lines"], parsed["b"]["lines"], sz["a"], sz["b"])
        for k in ("findings", "debris", "curved_lines", "coverage", "coverage_a", "coverage_b",
                  "reflow_edges", "reflow_lines", "row_merges", "row_splits", "pair_methods"):
            pr[k] = cmp_.get(k)
        pr["relocated"] = cmp_.get("relocated") or []
        pr["unpaired"] = {"a": cmp_["unpaired_a"], "b": cmp_["unpaired_b"]}
        pr["lines"] = {"a": pipeline._compact_lines(cmp_["lines_a"]),
                       "b": pipeline._compact_lines(cmp_["lines_b"])}
        pr["_raw"] = {"a": parsed["a"]["lines"], "b": parsed["b"]["lines"]}
        pipeline.apply_ignore(pr)
        pairs.append(pr)
    # อ่านซ้ำยิง Vision ไม่ได้ ⇒ ใช้ทางของ "ปิดการอ่านซ้ำ" เสมอ (ลดระดับ PUNCT/โค้ง + หมายเหตุ)
    was = config.REREAD_ENABLED
    config.REREAD_ENABLED = False
    try:
        pipeline._reread(pairs, None, None, None, "", [], warnings, None)
    finally:
        config.REREAD_ENABLED = was
    fid = 0
    for pr in pairs:
        if config.LOWMARK and pr.get("findings"):
            pr["findings"], lm = compare.fold_lowmark(pr["findings"])
            if lm:
                pr["lowmark"] = lm
        if config.CURVED_GROUP_ENABLED and pr.get("findings"):
            pr["findings"] = compare.collapse_curved(pr["findings"])
        for lk in ("findings", "debris", "relocated", "excluded", "lowmark"):
            for f in pr.get(lk) or []:
                fid += 1
                f["id"] = fid
    plog = None
    tmp = None
    can_pixel = (pixel and ds.get("srcs") and all(
        (p.get("sides") or {}).get(s, {}).get("bbox") for p in pairs if not p.get("unreadable") for s in "ab"))
    if can_pixel:
        from artwork_v2 import imaging, pixverify
        tmp = tempfile.mkdtemp(prefix="v2eval_")
        os.makedirs(os.path.join(tmp, "img"))
        for p in pairs:
            for s in "ab":
                p["sides"][s].setdefault("page", 0)
        srcs = {s: imaging.Source(ds["srcs"][s]) for s in "ab"}
        plog = pixverify.run(pairs, srcs, tmp, warnings, None)
    for pr in pairs:
        pr.pop("_raw", None)
    v, rs = pipeline.verdict_of(pairs)
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"pairs": pairs, "verdict": v, "reasons": rs, "pixel": plog, "warnings": warnings}


# ── เฉลย ────────────────────────────────────────────────────────────────

def key_of(f: dict) -> tuple:
    """ลายเซ็นของจุดต่างที่คงที่ข้ามการเล่นซ้ำ (ไม่ใช้เลขจุด — เลขเปลี่ยนตามธง)"""
    a, b = f.get("a") or {}, f.get("b") or {}
    if f.get("class") == "CURVED":
        return ("CURVED",) + tuple(sorted((m["a"].get("frag") or "", m["b"].get("frag") or "")
                                          for m in f.get("members") or []))
    return ((a.get("frag") or ""), (b.get("frag") or ""), (a.get("text") or ""), (b.get("text") or ""))


def labels_from_file(path: str) -> dict:
    """``labels.json`` = {"items": [{"key": [frag_a, frag_b, text_a, text_b], "truth": "REAL_DIFF"|"NO_DIFF", …}]}"""
    data = _load_json(path)
    def _t(x):
        return tuple(_t(y) for y in x) if isinstance(x, list) else x
    return {_t(it["key"]): it for it in data.get("items") or []}


def labels_from_review(ds: dict) -> dict:
    """``review.json`` ของรอบนั้น (✓ = real = ผิดจริง · ⚑ = false = ระบบแจ้งผิด) → เฉลยตามลายเซ็น"""
    rv, res = ds.get("review"), ds.get("result")
    if not rv or not res:
        return {}
    by_id = {}
    for p in res.get("pairs") or []:
        for f in p.get("findings") or []:
            by_id[str(f.get("id"))] = f
    out = {}
    for fid, it in (rv.get("items") or {}).items():
        f = by_id.get(str(fid))
        st = (it or {}).get("status")
        if f is not None and st in ("real", "false"):
            out[key_of(f)] = {"truth": "REAL_DIFF" if st == "real" else "NO_DIFF", "note": it.get("note") or ""}
    return out


def score(res: dict, labels: dict) -> dict:
    seen = set()
    table: Counter = Counter()
    unlabeled = []
    for pr in res["pairs"]:
        for lk in LIST_KEYS:
            for f in pr.get(lk) or []:
                k = key_of(f)
                lab = labels.get(k)
                truth = lab["truth"] if lab else "?"
                if lab:
                    seen.add(k)
                elif lk == "findings":
                    unlabeled.append(f)
                table[(f["severity"] if lk == "findings" else lk, truth)] += 1
    missed = []
    for k, lab in labels.items():
        if lab["truth"] != "REAL_DIFF":
            continue
        hit = any(key_of(f) == k and f["severity"] in ("red", "yellow")
                  for pr in res["pairs"] for f in pr.get("findings") or [])
        if not hit:
            missed.append((k, lab))
    return {"table": table, "missed": missed, "unlabeled": unlabeled, "seen": len(seen)}


# ── languageHints ───────────────────────────────────────────────────────

def _is_latin_char(c: str) -> bool:
    return c.isalpha() and ord(c) < 0x250       # Basic Latin … Latin Extended-B


def suggest_hints(ds: dict, min_words: int = 20) -> dict:
    """นับคำตามภาษาแรกที่ Vision ตรวจพบ · ภาษาที่ตัวอักษรส่วนใหญ่ **ไม่ใช่ละติน** และมีคำ ≥ min_words
    ⇒ แนะนำ (+ ``en`` เสมอ) — Google: ภาษาที่ใช้อักษรละตินไม่ต้องใส่ hints"""
    words: Counter = Counter()
    latin: Counter = Counter()
    total: Counter = Counter()
    for d in ds["pairs"].values():
        for s in ("a", "b"):
            for pg in (d.get(s) or {}).get("pages") or []:
                for bl in pg.get("blocks") or []:
                    for par in bl.get("paragraphs") or []:
                        for w in par.get("words") or []:
                            langs = ((w.get("property") or {}).get("detectedLanguages") or [])
                            if not langs:
                                continue
                            lc = langs[0].get("languageCode") or "?"
                            chars = [sy.get("text") or "" for sy in w.get("symbols") or []]
                            letters = [c for t in chars for c in t if c.isalpha()]
                            if not letters:
                                continue
                            words[lc] += 1
                            latin[lc] += sum(_is_latin_char(c) for c in letters)
                            total[lc] += len(letters)
    rows = []
    for lc, n in words.most_common():
        frac = latin[lc] / float(total[lc] or 1)
        rows.append({"lang": lc, "words": n, "latin_frac": round(frac, 3)})
    pick = [r["lang"] for r in rows if r["words"] >= min_words and r["latin_frac"] < 0.5]
    if pick and "en" not in pick:
        pick.append("en")
    return {"rows": rows, "hints": pick}


# ── แสดงผล ──────────────────────────────────────────────────────────────

def _short(f: dict) -> str:
    return "F%s %s %s A=%r B=%r" % (f.get("id"), f.get("severity"), f.get("class"),
                                   (f["a"].get("frag") or "")[:30], (f["b"].get("frag") or "")[:30])


def _summary(res: dict) -> Counter:
    c: Counter = Counter()
    for pr in res["pairs"]:
        for lk in LIST_KEYS:
            for f in pr.get(lk) or []:
                c[f["severity"] if lk == "findings" else lk] += 1
    return c


def _apply(sets):
    old = {}
    for kv in sets or []:
        k, _, v = kv.partition("=")
        k = k.strip().upper()
        if k.startswith("ARTWORK_V2_"):
            k = k[len("ARTWORK_V2_"):]
        if not hasattr(config, k):
            raise SystemExit("ไม่รู้จักธง %s" % k)
        cur = getattr(config, k)
        old[k] = cur
        if isinstance(cur, bool):
            val = v.strip().lower() not in ("0", "false", "no", "off", "")
        elif isinstance(cur, int):
            val = int(v)
        elif isinstance(cur, float):
            val = float(v)
        else:
            val = v
        setattr(config, k, val)
    return old


def _restore(old):
    for k, v in old.items():
        setattr(config, k, v)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("path", help="โฟลเดอร์รอบตรวจ หรือโฟลเดอร์ที่มี p<N>_a.json p<N>_b.json")
    ap.add_argument("--labels", help="labels.json (เฉลยที่ตรวจด้วยตา) — ไม่ใส่ = ใช้ review.json ของรอบนั้นถ้ามี")
    ap.add_argument("--set", action="append", default=[], metavar="FLAG=V", help="ตั้งค่าธงก่อนเล่นซ้ำ")
    ap.add_argument("--compare", action="append", default=[], metavar="FLAG=V",
                    help="เล่นซ้ำอีกชุดด้วยค่านี้ แล้วบอกจุดที่เปลี่ยน")
    ap.add_argument("--no-pixel", action="store_true", help="ไม่ตรวจด้วยภาพ (เร็วขึ้น)")
    ap.add_argument("--hints", action="store_true", help="แนะนำ languageHints จากผลดิบ")
    ap.add_argument("--json", help="บันทึกผลเป็น JSON")
    args = ap.parse_args(argv)
    try:
        ds = load_dataset(args.path)
    except SystemExit:
        raise
    except Exception as e:     # noqa: BLE001
        print("อ่านข้อมูลไม่ได้: %s" % e)
        return 2
    if args.hints:
        h = suggest_hints(ds)
        print("ภาษาที่ Vision ตรวจพบ (คำ · สัดส่วนอักษรละติน):")
        for r in h["rows"]:
            print("  %-6s %5d คำ  ละติน %.0f%%" % (r["lang"], r["words"], r["latin_frac"] * 100))
        print("แนะนำ: ARTWORK_V2_LANGUAGE_HINTS=%s" % ",".join(h["hints"]) if h["hints"]
              else "แนะนำ: ปล่อยว่าง (ไม่มีภาษาที่ไม่ใช่อักษรละตินมากพอ)")
        print("⚠️ ยังเป็นการทดลอง — ต้อง A/B บนภาพเดิมแล้วเทียบด้วยเครื่องมือนี้ก่อนใช้จริง")
        return 0
    labels = labels_from_file(args.labels) if args.labels else labels_from_review(ds)
    old = _apply(args.set)
    try:
        res = replay(ds, pixel=not args.no_pixel)
    finally:
        _restore(old)
    print("ผลตัดสิน: %s · %s" % (res["verdict"], " · ".join(res["reasons"])))
    print("นับ: %s" % dict(_summary(res)))
    if res["pixel"]:
        px = res["pixel"]
        print("หลักฐานภาพ: same=%s diff=%s (ภาพสแกน %s) unverifiable=%s" % (
            px.get("same"), px.get("diff"), px.get("diff_raster", 0), px.get("unverifiable")))
    else:
        print("หลักฐานภาพ: ไม่ได้ตรวจ (ไม่มีไฟล์ PDF ต้นฉบับ/ตำแหน่งโซน หรือ --no-pixel)")
    rc = 0
    out = {"verdict": res["verdict"], "reasons": res["reasons"], "summary": dict(_summary(res))}
    if labels:
        sc = score(res, labels)
        print("\nเทียบเฉลย (%d รายการ · พบในผลนี้ %d):" % (len(labels), sc["seen"]))
        for (sev, truth), n in sorted(sc["table"].items()):
            print("  %-12s %-10s %3d" % (sev, truth, n))
        red_real = sc["table"].get(("red", "REAL_DIFF"), 0)
        red_all = sum(v for (s, _), v in sc["table"].items() if s == "red")
        yel_real = sc["table"].get(("yellow", "REAL_DIFF"), 0)
        yel_all = sum(v for (s, _), v in sc["table"].items() if s == "yellow")
        print("  แดงเป็นของจริง %d/%d · เหลืองเป็นของจริง %d/%d" % (red_real, red_all, yel_real, yel_all))
        if sc["missed"]:
            rc = 1
            print("  ❌ ของจริงที่หลุด (ไม่เป็นแดง/เหลือง):")
            for k, lab in sc["missed"]:
                print("     %r" % (k[:2],))
        else:
            print("  ✅ ของจริงตามเฉลยเป็นแดง/เหลืองครบ")
        if sc["unlabeled"]:
            print("  ⚠️ จุดที่ไม่มีในเฉลย %d จุด (ต้องตรวจด้วยตาเพิ่ม):" % len(sc["unlabeled"]))
            for f in sc["unlabeled"][:20]:
                print("     " + _short(f))
        out["score"] = {"table": {"%s|%s" % k: v for k, v in sc["table"].items()},
                        "missed": [list(k) for k, _ in sc["missed"]], "unlabeled": len(sc["unlabeled"])}
    if args.compare:
        old = _apply(args.set + args.compare)
        try:
            res2 = replay(ds, pixel=not args.no_pixel)
        finally:
            _restore(old)
        print("\nเทียบกับ %s: %s · นับ %s" % (" ".join(args.compare), res2["verdict"], dict(_summary(res2))))

        def idx(r):
            m = {}
            for pr in r["pairs"]:
                for lk in LIST_KEYS:
                    for f in pr.get(lk) or []:
                        m[key_of(f)] = (lk, f)
            return m
        m1, m2 = idx(res), idx(res2)
        changed = 0
        for k in sorted(set(m1) | set(m2), key=str):
            s1 = (m1[k][0], m1[k][1]["severity"]) if k in m1 else None
            s2 = (m2[k][0], m2[k][1]["severity"]) if k in m2 else None
            if s1 != s2:
                changed += 1
                f = (m1.get(k) or m2.get(k))[1]
                lab = labels.get(k, {}).get("truth", "?") if labels else "?"
                print("  %s → %s  [%s] %s" % (s1, s2, lab, _short(f)))
        print("  เปลี่ยน %d จุด" % changed)
        out["compare_changed"] = changed
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    return rc


if __name__ == "__main__":
    sys.exit(main())
