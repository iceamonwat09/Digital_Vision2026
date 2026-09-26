# -*- coding: utf-8 -*-
"""ธนาคารเคสจริงของชั้นเทียบข้อความ — "การ์ดที่ขึ้นบนรายงาน" ต้องไม่เปลี่ยนเงียบ ๆ

ทุกเคสใน ``tests/data/artwork_cards/*.json`` คือ **ข้อความ OCR จริงของสถานี**
(+ text layer ที่เป็นพยาน) ⇒ เล่นซ้ำด้วย ``checks.run_all_checks`` ตัวจริงได้
โดยไม่ต้องมี Gemini/กล้อง/SQL Server. ``expected.json`` เก็บการ์ดที่ควรได้
ทั้ง **ค่าปัจจุบัน** และ **เมื่อปิดธงใหม่ทั้งหมด** (= พฤติกรรมก่อน 26 ก.ย.)

    py -3.9 verify_cards.py              # เทียบกับ expected.json · exit 0/1
    py -3.9 verify_cards.py --show       # พิมพ์การ์ดทุกเคส
    py -3.9 verify_cards.py --update     # เขียน expected.json ใหม่ (ตั้งใจเท่านั้น)

⚠️ ``--update`` ต้องใช้เมื่อ **ตั้งใจ** เปลี่ยนผลเท่านั้น แล้วอธิบายใน commit ว่า
การ์ดไหนเปลี่ยนเพราะอะไร — ความจริงของแต่ละเคสเขียนไว้ใน ``truth`` ของไฟล์
"""
import argparse
import glob
import importlib
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
BANK = os.path.join(ROOT, "tests", "data", "artwork_cards")
EXPECTED = os.path.join(BANK, "expected.json")

# ธงของงาน 26 ก.ย. — ปิดทั้งหมด = พฤติกรรมของ commit ก่อนหน้าเป๊ะ
NEW_FLAGS = ("TEXT_WITNESS_STRICT", "TEXT_PAIR_CHAR_FALLBACK",
             "TEXT_PAIR_NUMERIC", "TEXT_NUMBER_STRICT")


def cases():
    return sorted(os.path.basename(p)[:-5] for p in glob.glob(BANK + "/*.json")
                  if not p.endswith("expected.json"))


def load(name):
    with open(os.path.join(BANK, name + ".json"), encoding="utf-8") as f:
        return json.load(f)


def cards(name, legacy=False):
    """การ์ดของเคส ``name`` — ``legacy`` = ปิดธงใหม่ทั้งหมด"""
    from artwork_check import checks, config
    old = {k: getattr(config, k) for k in NEW_FLAGS}
    try:
        if legacy:
            for k in NEW_FLAGS:
                setattr(config, k, False)
        c = load(name)
        ds = checks.run_all_checks(c["zones"], c["ocr"])
    finally:
        for k, v in old.items():
            setattr(config, k, v)
    return [{"class": d["class"], "zone_id": d["zone_id"],
             "severity": d["severity"], "found": d.get("found") or "",
             "reference": d.get("reference") or "",
             "witness": bool(d.get("witness"))} for d in ds]


def snapshot():
    return {n: {"current": cards(n), "legacy": cards(n, legacy=True)}
            for n in cases()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--update", action="store_true")
    a = ap.parse_args(argv)
    importlib.import_module("artwork_check.checks")
    snap = snapshot()
    if a.update:
        old = {}
        if os.path.exists(EXPECTED):
            with open(EXPECTED, encoding="utf-8") as f:
                old = json.load(f)
        out = {n: dict(v, truth=(old.get(n) or {}).get("truth", ""))
               for n, v in snap.items()}
        with open(EXPECTED, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print("เขียน %s (%d เคส)" % (EXPECTED, len(out)))
        return 0
    with open(EXPECTED, encoding="utf-8") as f:
        exp = json.load(f)
    bad = 0
    for n, v in snap.items():
        for mode in ("current", "legacy"):
            ok = exp.get(n, {}).get(mode) == v[mode]
            bad += not ok
            crit = sum(1 for d in v[mode] if d["severity"] == "critical")
            print("%s %-10s %-7s การ์ด %d · critical %d"
                  % ("✅" if ok else "❌", n, mode, len(v[mode]), crit))
            if a.show or not ok:
                for d in v[mode]:
                    print("      %-15s %-4s %-8s %-40r | %r%s"
                          % (d["class"], d["zone_id"], d["severity"],
                             d["found"][:40], d["reference"][:40],
                             "  📄" if d["witness"] else ""))
        if a.show and exp.get(n, {}).get("truth"):
            print("      ความจริง:", exp[n]["truth"])
    print("ไม่ตรง %d" % bad if bad else "ตรงทุกเคส")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
