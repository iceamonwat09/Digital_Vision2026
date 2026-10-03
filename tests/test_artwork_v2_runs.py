"""Artwork V2 — ชุดทดสอบจากผล OCR จริงของสถานี (ชุดที่ 2, 3, …)

วางชุดข้อมูลไว้ที่ ``tests/data/artwork_v2/station_runs/<ชื่อ>/`` (ดู ``artwork_v2_fake.load_run_dir``):

* ``p1_a.json`` ``p1_b.json`` … = ผลดิบของ Vision จาก
  ``data/artwork_v2/jobs/<งาน>/runs/<รอบ>/raw/`` บนสถานี (**แม่นที่สุด** — มีความมั่นใจ
  รายตัวอักษร + มุมจริง) หรือ ``log.txt`` = ข้อความจากปุ่ม "คัดลอก Log"
* ``expect.json`` (ไม่บังคับ)::

    {"job": "20261003_004712_25d50a",
     "group": "avoderm_m1m2",                  # กลุ่มเดียวกัน (หรือ job เดียวกัน) = เทียบความแปรปรวนข้ามรอบ
     "note": "เฉลยยืนยันด้วยตาโดย …",
     "pairs": {"1": {"must":  [["CASE", "c", "C"], ...],   # ต้องเจอ (class, frag A, frag B)
                     "allow": [["PUNCT", "", "."]],         # แดงที่ยอมได้นอกเหนือ must
                     "max_yellow": 2, "max_debris": 3}}}

ไม่มีชุดข้อมูล = เทสต์ชุดข้อมูลถูกข้าม (เทสต์ตัวโหลดยังทำงานเสมอ)
"""

from __future__ import annotations

import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from artwork_v2_fake import fta, load_log, load_run_dir  # noqa: E402

from artwork_v2 import compare, config, jobs, pipeline, textmodel, vision_client  # noqa: E402

RUNS = os.path.join(os.path.dirname(__file__), "data", "artwork_v2", "station_runs")


def _datasets():
    if not os.path.isdir(RUNS):
        return []
    return sorted(d for d in os.listdir(RUNS) if os.path.isdir(os.path.join(RUNS, d)))


def _expect(name):
    p = os.path.join(RUNS, name, "expect.json")
    if not os.path.isfile(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _sig(f):
    return (f["class"], f["a"]["frag"], f["b"]["frag"])


def _run_compare(pair):
    (Wa, Ha, A), (Wb, Hb, B) = pair["A"], pair["B"]
    return compare.compare(A, B, (Wa, Ha), (Wb, Hb))


# ── ตัวโหลดต้องอ่านกลับได้เหมือนต้นฉบับ ────────────────────────────────

def test_raw_json_dir_roundtrip(tmp_path):
    spec_a = [("Sodium 475 mg 20%", 50, 100), ("OMEGA-6", 400, 400, {"angle": 38})]
    spec_b = [("Sodium 475 mg 24%", 50, 100), ("OMEGA-3", 400, 400, {"angle": 38})]
    for s, spec in (("a", spec_a), ("b", spec_b)):
        (tmp_path / ("p1_%s.json" % s)).write_text(json.dumps(fta(spec, 900, 700)), encoding="utf-8")
    ds = load_run_dir(str(tmp_path))
    assert list(ds) == [1] and ds[1]["source"] == "raw"
    assert ds[1]["A"][:2] == (900, 700)
    direct = compare.compare(textmodel.parse(fta(spec_a, 900, 700), 900, 700)["lines"],
                             textmodel.parse(fta(spec_b, 900, 700), 900, 700)["lines"],
                             (900, 700), (900, 700))
    got = _run_compare(ds[1])
    assert sorted(map(_sig, got["findings"])) == sorted(map(_sig, direct["findings"]))
    assert [f.get("curved") for f in got["findings"]] == [f.get("curved") for f in direct["findings"]]


def test_raw_json_with_full_vision_response_is_accepted(tmp_path):
    body = {"fullTextAnnotation": fta([("Fat 1.5g", 10, 10)], 300, 200)}
    for s in "ab":
        (tmp_path / ("p1_%s.json" % s)).write_text(json.dumps(body), encoding="utf-8")
    ds = load_run_dir(str(tmp_path))
    assert ds[1]["A"][2][0]["text"] == "Fat 1.5g"


@pytest.fixture
def _isolated(tmp_path, monkeypatch):
    d = tmp_path / "v2"
    monkeypatch.setattr(config, "DATA_DIR", str(d))
    monkeypatch.setattr(config, "JOBS_DIR", str(d / "jobs"))
    monkeypatch.setattr(config, "SECRET_DIR", str(d / "secret"))
    monkeypatch.setattr(config, "KEY_FILE", str(d / "secret" / "key.json"))
    monkeypatch.delenv(config.KEY_ENV, raising=False)
    os.makedirs(config.JOBS_DIR)


def test_copied_log_roundtrip(tmp_path, monkeypatch, _isolated):
    """Log จากปุ่ม "คัดลอก Log" ต้องใช้เป็นชุดทดสอบได้ — บรรทัด/กรอบ/มุม/ขนาดภาพครบ"""
    fitz = pytest.importorskip("fitz")
    from PIL import Image

    TA = ["Sodium 475 mg 20%", "Fat 1.5g", 'He said "ok" & left']
    TB = ["Sodium 475 mg 24%", "Fat 1.5g", 'He said "ok" & left']

    def fake(groups, poster=None, key=None):
        res, ids = {}, []
        for g in groups:
            for it in g:
                ids.append(it["id"])
                W, H = Image.open(io.BytesIO(it["jpeg"])).size
                T = TA if it["id"].endswith("a") else TB
                ls = [(t, 20, 30 + 60 * i, {"cw": 12, "h": 24, "angle": 15 if i == 1 else None})
                      for i, t in enumerate(T)]
                res[it["id"]] = {"ok": True, "error": "", "fta": fta(ls, W, H), "request_index": 0}
        return {"results": res, "calls": [{"index": 0, "phase": "main", "images": ids,
                                           "json_bytes": 1, "status": 200, "attempts": 1,
                                           "ms": 1, "error": "", "at": "t"}]}

    def pdf():
        doc = fitz.open()
        doc.new_page(width=400, height=300).insert_text((20, 30), "x")
        return doc.tobytes()

    monkeypatch.setattr(vision_client, "annotate", fake)
    monkeypatch.setattr(config, "REREAD_ENABLED", False)
    job = jobs.create(("a.pdf", pdf()), ("b.pdf", pdf()))["id"]
    r = pipeline.run(job, [{"a": {"page": 0, "bbox": [0, 0, 1, 1]},
                            "b": {"page": 0, "bbox": [0, 0, 1, 1]}}])
    (tmp_path / "log.txt").write_text(r["log_text"], encoding="utf-8")
    L = load_log(str(tmp_path / "log.txt"))
    p = r["pairs"][0]
    for s in ("A", "B"):
        W, H, items = L[1][s]
        assert [W, H] == p["sides"][s.lower()]["sent_px"]
        assert [t for t, _, _, _ in items] == [l["text"] for l in p["lines"][s.lower()]]
        assert [a for _, _, _, a in items] == [round(l["angle"]) if l["angle"] is not None else None
                                             for l in p["lines"][s.lower()]]
    # และโฟลเดอร์ที่มีแค่ log.txt ต้องโหลดได้ด้วย load_run_dir
    ds = load_run_dir(str(tmp_path))
    assert ds[1]["source"] == "log"
    sigs = {_sig(f) for f in _run_compare(ds[1])["findings"]}
    assert ("NUMBER", "0", "4") in sigs


# ── ชุดข้อมูลจริงจากสถานี ────────────────────────────────────────────

@pytest.mark.parametrize("name", _datasets() or [pytest.param(None, marks=pytest.mark.skip(
    reason="ยังไม่มีชุดข้อมูลใน tests/data/artwork_v2/station_runs/"))])
def test_station_dataset(name):
    ds = load_run_dir(os.path.join(RUNS, name))
    assert ds, "โฟลเดอร์ %s ไม่มี p*_a.json/p*_b.json หรือ log.txt" % name
    exp = (_expect(name).get("pairs") or {})
    for n, pair in ds.items():
        assert "A" in pair and "B" in pair, "คู่ %d ขาดฝั่งใดฝั่งหนึ่ง" % n
        r = _run_compare(pair)
        assert r["coverage"] is not None
        e = exp.get(str(n))
        if not e:
            continue
        sigs = {_sig(f) for f in r["findings"]}
        must = {tuple(x) for x in e.get("must", [])}
        allow = {tuple(x) for x in e.get("allow", [])}
        assert must <= sigs, "คู่ %d: ขาดความต่างจริง %s" % (n, sorted(must - sigs))
        bad_red = {_sig(f) for f in r["findings"]
                   if f["severity"] == "red" and not f.get("curved")} - must - allow
        assert not bad_red, "คู่ %d: แดงที่ไม่อยู่ในเฉลย %s" % (n, sorted(bad_red))
        cards = compare.collapse_curved(r["findings"])
        if "max_yellow" in e:
            yellow = [f for f in cards if f["severity"] != "red" or f["class"] == "CURVED"]
            assert len(yellow) <= e["max_yellow"], "คู่ %d: เหลือง %d > %d" % (
                n, len(yellow), e["max_yellow"])
        if "max_debris" in e:
            assert len(r["debris"]) <= e["max_debris"]


def _same_job_pairs():
    by_job = {}
    for d in _datasets():
        e = _expect(d)
        j = e.get("group") or e.get("job")
        if j:
            by_job.setdefault(j, []).append(d)
    out = []
    for names in by_job.values():
        for i in range(len(names)):
            for k in range(i + 1, len(names)):
                out.append((names[i], names[k]))
    return out


@pytest.mark.parametrize("pair", _same_job_pairs() or [pytest.param(None, marks=pytest.mark.skip(
    reason="ยังไม่มีชุดข้อมูลสองรอบของงานเดียวกัน"))])
def test_same_file_read_twice_has_no_confident_text_difference(pair):
    """ไฟล์เดียวกัน อ่านคนละรอบ ⇒ ต่างได้แค่สัญญาณรบกวน — ห้ามมีแดงชนิดตัวอักษร/ตัวเลข/
    ตัวพิมพ์บนบรรทัดตั้งตรงที่จับคู่ได้ (ความแปรปรวนข้ามรอบของ Vision)"""
    d1, d2 = (load_run_dir(os.path.join(RUNS, x)) for x in pair)
    # ความแปรปรวนที่วัดได้แล้ว (บันทึกไว้ใน expect.json ของชุดใดชุดหนึ่ง) — เทสต์ล้มเฉพาะของใหม่
    known = {}
    for x in pair:
        for n, sides in (_expect(x).get("cross_run_known") or {}).items():
            if isinstance(sides, dict):
                for s, sigs in sides.items():
                    known.setdefault((int(n), s), set()).update(tuple(t) for t in sigs)
    for n in set(d1) & set(d2):
        for s in ("A", "B"):
            r = compare.compare(d1[n][s][2], d2[n][s][2], d1[n][s][:2], d2[n][s][:2])
            bad = [_sig(f) for f in r["findings"] if f["severity"] == "red"
                   and not f.get("curved") and f["pair_method"] != "unpaired"
                   and f["class"] in ("CASE", "NUMBER", "TEXT")]
            new = sorted(set(bad) - known.get((n, s), set()))
            assert not new, "คู่ %d ฝั่ง %s: ความแปรปรวนข้ามรอบที่ยังไม่เคยบันทึก %s" % (n, s, new)
