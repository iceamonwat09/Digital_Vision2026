"""ตรวจย้อนหลัง: ภาพเดียวกันเป๊ะ Vision อ่านได้ผลเดิมไหม (ไม่ยิง Vision เพิ่มแม้แต่ครั้งเดียว)

ไล่ทุกรอบตรวจของ Artwork V2 ใน ``data/artwork_v2/jobs/<งาน>/<รอบ>/``:
``img/p<N>_<a|b>.jpg`` คือไบต์ที่ส่งให้ Vision จริง และ ``raw/p<N>_<a|b>.json``
คือผลดิบที่ Vision ตอบกลับ ⇒ จัดกลุ่มรอบที่ส่ง "ภาพเดียวกันทุกไบต์" (sha1 ของ JPEG
ตรงกัน — ข้ามงานได้) แล้วเทียบผลดิบของแต่ละรอบในกลุ่ม

ใช้ตอบคำถาม: ความต่างที่เห็นข้ามรอบ มาจาก Vision อ่านไม่คงที่ หรือจากโซนที่วาดใหม่
    py -3.9 artwork_v2_rerun_check.py
    py -3.9 artwork_v2_rerun_check.py --dir D:\\อื่น\\data\\artwork_v2\\jobs --verbose

exit: 0 = ทุกกลุ่มได้ผลเหมือนเดิม · 1 = มีกลุ่มที่ผลต่าง · 2 = ไม่มีภาพซ้ำให้เทียบ
อ่านอย่างเดียว ไม่เขียนอะไรลงโฟลเดอร์งาน
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_IMG_RE = re.compile(r"^(p\d+_[ab])\.jpg$")


def _sha1(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect(jobs_dir: str):
    """คืน {sha1: [{job, run, side, raw_path, img_path}]} เฉพาะภาพที่มีผลดิบ"""
    groups = defaultdict(list)
    if not os.path.isdir(jobs_dir):
        return groups
    for job in sorted(os.listdir(jobs_dir)):
        jd = os.path.join(jobs_dir, job)
        if not os.path.isdir(jd):
            continue
        for run in sorted(os.listdir(jd)):
            img_dir = os.path.join(jd, run, "img")
            raw_dir = os.path.join(jd, run, "raw")
            if not os.path.isdir(img_dir) or not os.path.isdir(raw_dir):
                continue
            for name in sorted(os.listdir(img_dir)):
                m = _IMG_RE.match(name)
                if not m:
                    continue
                raw_path = os.path.join(raw_dir, m.group(1) + ".json")
                if not os.path.isfile(raw_path):
                    continue        # Vision อ่านภาพนี้ไม่สำเร็จ — ไม่มีผลให้เทียบ
                img_path = os.path.join(img_dir, name)
                groups[_sha1(img_path)].append(
                    {"job": job, "run": run, "side": m.group(1),
                     "img": img_path, "raw": raw_path})
    return groups


def _words(fta: dict):
    """[(ข้อความของคำ, ความมั่นใจ)] ตามลำดับที่ Vision ส่ง"""
    out = []
    for page in fta.get("pages") or []:
        for block in page.get("blocks") or []:
            for para in block.get("paragraphs") or []:
                for w in para.get("words") or []:
                    t = "".join(s.get("text", "") for s in w.get("symbols") or [])
                    out.append((t, w.get("confidence")))
    return out


def _lines(fta: dict, img_path: str):
    """บรรทัดที่แอปประกอบจริง (textmodel.parse) — None ถ้าเปิดภาพ/โมดูลไม่ได้"""
    try:
        from PIL import Image
        from artwork_v2 import textmodel
        with Image.open(img_path) as im:
            W, H = im.size
        return [ln.get("text", "") for ln in textmodel.parse(fta, W, H)["lines"]]
    except Exception:
        return None


def _same_by_app_rules(ta: str, tb: str) -> bool:
    """ข้อความทั้งภาพเท่ากันตามกติกาเทียบของแอป (compare.diff_key_map) หรือไม่"""
    try:
        from artwork_v2 import compare as cmp
        return cmp.diff_key_map(ta)[0] == cmp.diff_key_map(tb)[0]
    except Exception:
        return False


def compare(ref: dict, other: dict) -> dict:
    """เทียบผลดิบสองรอบของภาพเดียวกัน"""
    a = json.load(open(ref["raw"], encoding="utf-8"))
    b = json.load(open(other["raw"], encoding="utf-8"))
    if a == b:
        return {"same": True, "kind": "identical"}
    res = {"same": False}
    ta, tb = (a.get("text") or ""), (b.get("text") or "")
    wa, wb = _words(a), _words(b)
    res["text_same"] = ta == tb
    res["words"] = (len(wa), len(wb))
    res["word_text_same"] = [w for w, _ in wa] == [w for w, _ in wb]
    if res["word_text_same"]:
        diffs = [abs((x or 0) - (y or 0)) for (_, x), (_, y) in zip(wa, wb)]
        res["max_conf_delta"] = round(max(diffs), 4) if diffs else 0.0
        res["kind"] = "confidence_only"     # ข้อความเหมือนกันทุกคำ ต่างแค่ความมั่นใจ/พิกัด
    elif _same_by_app_rules(ta, tb):
        # ต่างแค่อักษรที่แอปถือว่าเป็นตัวเดียวกัน (®/Ⓡ · ½/1/2 · จุดไข่ปลา · ช่องว่าง)
        # ⇒ ชั้นเทียบมองไม่เห็นความต่างนี้อยู่แล้ว ไม่ใช่หลักฐานว่าการอ่านซ้ำมีประโยชน์
        res["kind"] = "equivalent"
    else:
        res["kind"] = "text"
    la, lb = _lines(a, ref["img"]), _lines(b, other["img"])
    if la is not None and lb is not None:
        sa, sb = set(la), set(lb)
        res["lines_only_ref"] = sorted(sa - sb)
        res["lines_only_other"] = sorted(sb - sa)
    return res


def main(argv=None) -> int:
    try:                    # คอนโซล Windows (cp874/cp1252) พิมพ์ภาษาไทยแล้วล้ม
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        from artwork_v2 import config
        default_dir = config.JOBS_DIR
    except Exception:
        default_dir = os.path.join("data", "artwork_v2", "jobs")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", default=default_dir, help="โฟลเดอร์ jobs (ค่าเริ่มต้น %(default)s)")
    ap.add_argument("--verbose", action="store_true", help="แสดงบรรทัดที่ต่างทั้งหมด")
    args = ap.parse_args(argv)

    groups = collect(args.dir)
    total_imgs = sum(len(v) for v in groups.values())
    dup = {h: v for h, v in groups.items() if len(v) >= 2}
    print("โฟลเดอร์: %s" % args.dir)
    print("ภาพที่มีผลดิบ %d ภาพ · ภาพที่ถูกส่งซ้ำทุกไบต์ %d ชุด" % (total_imgs, len(dup)))
    if not dup:
        print("\n⚠️ ไม่มีภาพที่ส่งซ้ำเป๊ะ — ทุกรอบส่งภาพไม่เหมือนกัน (วาดโซนใหม่/ความคมต่าง)")
        print("   ⇒ ข้อมูลที่มีตอบไม่ได้ว่า Vision อ่านคงที่ไหม")
        return 2

    n_same = n_conf = n_equiv = n_text = 0
    for h, items in sorted(dup.items(), key=lambda kv: kv[1][0]["job"]):
        ref = items[0]
        print("\n━━ sha1 %s · %d รอบ · %s" % (h[:12], len(items), ref["side"]))
        print("   อ้างอิง: %s/%s" % (ref["job"], ref["run"]))
        for other in items[1:]:
            r = compare(ref, other)
            where = "%s/%s" % (other["job"], other["run"])
            if r["same"]:
                n_same += 1
                print("   ✅ %s — ผลดิบเหมือนกันทุกไบต์" % where)
                continue
            if r["kind"] == "confidence_only":
                n_conf += 1
                print("   🟡 %s — ข้อความเหมือนกันทุกคำ (%d คำ) ต่างแค่ความมั่นใจ/พิกัด "
                      "(ความมั่นใจต่างสูงสุด %.4f)" % (where, r["words"][0], r["max_conf_delta"]))
            elif r["kind"] == "equivalent":
                n_equiv += 1
                print("   🟢 %s — ต่างแค่อักษรที่แอปถือว่าเป็นตัวเดียวกัน (®/Ⓡ · ½ · จุดไข่ปลา) "
                      "· ผลตรวจไม่เปลี่ยน" % where)
            else:
                n_text += 1
                print("   🔴 %s — ข้อความต่าง · คำ %d vs %d" % (where, r["words"][0], r["words"][1]))
            only_a = r.get("lines_only_ref") or []
            only_b = r.get("lines_only_other") or []
            if only_a or only_b:
                lim = None if args.verbose else 6
                print("      บรรทัดที่มีเฉพาะรอบอ้างอิง %d · เฉพาะรอบนี้ %d" % (len(only_a), len(only_b)))
                for t in only_a[:lim]:
                    print("        - %s" % t)
                for t in only_b[:lim]:
                    print("        + %s" % t)
    print("\nสรุป: เหมือนทุกไบต์ %d · ข้อความเหมือนแต่ความมั่นใจต่าง %d · "
          "ต่างแค่อักษรสมมูล %d · ข้อความต่างจริง %d" % (n_same, n_conf, n_equiv, n_text))
    if n_text:
        print("⇒ Vision อ่านภาพเดียวกันได้ข้อความไม่เหมือนเดิม — การอ่าน 2 รอบแล้วโหวตมีประโยชน์")
    else:
        print("⇒ Vision คงที่กับภาพเดียวกัน — ความต่างข้ามรอบที่เคยเห็นมาจากภาพที่ส่งไม่เหมือนกัน "
              "(วาดโซนใหม่) · อ่านซ้ำภาพเดิมไม่ได้ข้อมูลเพิ่ม")
    return 1 if n_text else 0


if __name__ == "__main__":
    sys.exit(main())
