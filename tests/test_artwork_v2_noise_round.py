"""Artwork V2 — รอบลดเหลือง (8 ต.ค. รอบ 4) · ทุกกติกาใหม่ของชั้นเทียบ + ข้อมูลจริง Friskies

* ``REFLOW_CONSERVE`` — "ตัดบรรทัดคนละที่" ต้องอนุรักษ์จำนวน (ชิ้นท้ายบรรทัดที่บังเอิญเจอในบรรทัดข้างเคียง
  เคยถูกทิ้งเงียบ ๆ แม้หายจริง — ``Pack of 12``→``Pack of 1``) · ไม่สมดุล ⇒ จุดต่าง (``REFLOW_UNBALANCED_YELLOW``)
* ``PLACEHOLDER`` — ช่องว่างรอพิมพ์ (``xxxxxx``) ↔ ข้อมูลจริง = ต่างแน่นอน (CFPR ของ Friskies เคยเป็นเหลือง)
* ``LOWMARK`` — เครื่องหมายเดี่ยวฝั่งเดียวที่ Vision ไม่มั่นใจ ⇒ รายการพับ
* ``KEEP_SUPERSCRIPT`` — ตัวยก/ตัวห้อยไม่ถูก NFKC รวมกับตัวธรรมดา (m² ≠ m2)
* ``PIXEL_RASTER_NOTE`` — DIFF บนคู่ภาพสแกนไม่ใช่หลักฐานว่าข้อความต่าง
* ข้อมูลจริง: ผลดิบ Vision ของงาน Friskies (``friskies/raw_20261008_213541``) + เฉลยที่ตรวจด้วยตา
"""

from __future__ import annotations

import glob
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from artwork_v2_fake import fta, fta_from_lines, load_log  # noqa: E402

from artwork_v2 import compare, config, textmodel  # noqa: E402

D = os.path.join(os.path.dirname(__file__), "data", "artwork_v2")
FRISKIES = os.path.join(D, "friskies", "raw_20261008_213541")
NEW = ("REFLOW_CONSERVE", "PLACEHOLDER", "LOWMARK", "KEEP_SUPERSCRIPT", "PIXEL_RASTER_NOTE")
W = H = 1000
BODY = [("Natural recipe with real chicken and vegetables", 50, 100, {}),
        ("Complete and balanced nutrition for adult dogs", 50, 140, {}),
        ("Manufactured by Example Pet Foods Company Ltd", 50, 180, {})]


@pytest.fixture(autouse=True)
def _production(monkeypatch):
    """ค่าเริ่มต้นของเครื่อง (การอ่านซ้ำปิด · ธงใหม่เปิด) — conftest ตรึงค่าเดิมให้โมดูลอื่นเท่านั้น"""
    monkeypatch.setattr(config, "REREAD_ENABLED", False)
    monkeypatch.setattr(config, "AI_MODE", "off")
    for k in NEW + ("REFLOW_UNBALANCED_YELLOW", "PLACEHOLDER_RED"):
        monkeypatch.setattr(config, k, True)


def _flags(monkeypatch, on):
    for k in NEW:
        monkeypatch.setattr(config, k, on)


def L(spec):
    return textmodel.parse(fta(BODY + spec, W, H), W, H)["lines"]


def run(sa, sb):
    return compare.compare(L(sa), L(sb), (W, H), (W, H))


def sigs(r):
    return {(f["class"], f["a"]["frag"], f["b"]["frag"], f["severity"]) for f in r["findings"]}


def lines(texts, y0=300):
    return [(t, 50, y0 + 40 * i, {}) for i, t in enumerate(texts)]


# ── REFLOW_CONSERVE ───────────────────────────────────────────────────

def test_edge_digit_dropped_is_caught_even_when_the_neighbour_has_that_digit(monkeypatch):
    a, b = lines(["Pack of 12", "Best before 2027"]), lines(["Pack of 1", "Best before 2027"])
    r = run(a, b)
    hit = [f for f in r["findings"] if f["a"]["frag"] == "2"]
    assert hit and hit[0]["reflow_unbalanced"] and hit[0]["severity"] == "yellow"
    assert any("จำนวนครั้งที่ปรากฏ" in n for n in hit[0]["notes"])
    _flags(monkeypatch, False)
    assert not run(a, b)["findings"], "ปิดธง = พฤติกรรมเดิม (หลุดเงียบ)"


def test_unbalanced_can_be_red_when_asked(monkeypatch):
    monkeypatch.setattr(config, "REFLOW_UNBALANCED_YELLOW", False)
    r = run(lines(["Pack of 12", "Best before 2027"]), lines(["Pack of 1", "Best before 2027"]))
    assert ("NUMBER", "2", "", "red") in sigs(r)


def test_true_line_wrap_is_still_silent():
    a = lines(["Ingredients chicken rice sunflower", "oil salt water fish"])
    b = lines(["Ingredients chicken rice", "sunflower oil salt water fish"])
    assert not run(a, b)["findings"]


def test_whole_word_count_is_not_fooled_by_a_digit_change_elsewhere():
    """บาร์โค้ด: ``0`` ท้ายย้ายไปต่อบรรทัด (ตัดบรรทัดจริง) ขณะที่ 20%→24% ที่อื่นเปลี่ยนจำนวนเลข 0 ในโซน"""
    a = lines(["Sodium 475 mg 20%", "5290700241", "0"])
    b = lines(["Sodium 475 mg 24%", "5290700241 0"])
    s = sigs(run(a, b))
    assert ("NUMBER", "0", "4", "red") in s
    assert not [x for x in s if "0" in (x[1], x[2]) and x[1] != x[2] and {x[1], x[2]} != {"0", "4"}]


def test_soft_punctuation_keeps_the_old_rule():
    assert compare.reflow_conserved(",", [], [{"dk": ","}])
    assert compare.reflow_conserved("-", [], [])


PANEL = ["Ingredients: chicken, rice, fish oil, salt",
         "vitamin E, vitamin D3, zinc sulphate, iron",
         "Crude Protein (min) 10% Crude Fat (min) 5%",
         "Crude Fibre (max) 1% Moisture (max) 82%",
         "Net Wt 85 g Best before see lid",
         "Store in a cool dry place. Keep refrigerated",
         "Contains: fish, soy. Made in Thailand",
         "Manufactured for Example Pet Co. Ltd"]


def _edits():
    for i, t in enumerate(PANEL):
        w = t.split(" ")
        yield i, " ".join(w[1:])
        yield i, " ".join(w[:-1])
        yield i, t[:-1]
        yield i, t[1:]
        yield i, t + "1"
        yield i, t + "s"


def test_every_edge_edit_of_a_dense_panel_is_reported(monkeypatch):
    """เดิม (ปิดธง) 23/32 ของการแก้ระดับตัวอักษรที่ขอบบรรทัดหลุดเงียบ — ตอนนี้ต้องไม่หลุดเลย"""
    silent = []
    for i, mt in _edits():
        mut = list(PANEL)
        mut[i] = mt
        if not run(lines(PANEL), lines(mut))["findings"]:
            silent.append((i, mt))
    assert not silent, silent
    _flags(monkeypatch, False)
    old = sum(not run(lines(PANEL), lines(m))["findings"]
              for m in ([*PANEL[:i], mt, *PANEL[i + 1:]] for i, mt in _edits()))
    assert old >= 10, "ชุดทดสอบต้องยังพิสูจน์ช่องโหว่เดิมได้ (%d)" % old


# ── PLACEHOLDER ───────────────────────────────────────────────────────

@pytest.mark.parametrize("w,ok", [("xxxxxx", True), ("XXXXXX", True), ("(xxxxxx),", True), ("XX/XX/XXXX", True),
                                  ("xxxx-xx", True), ("TBD", True), ("tbc", True), ("xxx", False),
                                  ("XXL", False), ("Lot", False), ("x", False), ("", False)])
def test_placeholder_words(w, ok):
    assert compare._is_placeholder(w) is ok


def test_placeholder_side_needs_real_data_on_the_other_side():
    assert compare.placeholder_side("xxxxxx", "SF-CF12-26-172683205264") == "a"
    assert compare.placeholder_side("SF-CF12-26", "XXXXXX") == "b"
    assert compare.placeholder_side("xxxxxx", "xxxxxx") is None
    assert compare.placeholder_side("xxxxxx", "ab") is None
    assert compare.placeholder_side("XL", "XXL") is None


def _cfpr(conf_b=None):
    a = [("CFPR No. SF-CF12-26-172683205264", 50, 300, {})]
    b = [("CFPR No. xxxxxx", 50, 300, {"conf": conf_b} if conf_b else {})]
    return run(a, b)


def test_placeholder_vs_code_is_red_with_a_reason():
    f = [f for f in _cfpr()["findings"] if f["class"] == "PLACEHOLDER"]
    assert len(f) == 1 and f[0]["severity"] == "red" and f[0]["placeholder"] == "b"
    assert any("ช่องว่างรอพิมพ์" in n for n in f[0]["notes"])


def test_placeholder_read_unsure_stays_yellow(monkeypatch):
    f = [f for f in _cfpr(0.5)["findings"] if f["class"] == "PLACEHOLDER"]
    assert f and f[0]["severity"] == "yellow"
    monkeypatch.setattr(config, "PLACEHOLDER_RED", False)
    assert all(f["severity"] == "yellow" for f in _cfpr()["findings"] if f["class"] == "PLACEHOLDER")


def test_placeholder_flag_off_is_the_old_class(monkeypatch):
    _flags(monkeypatch, False)
    assert not [f for f in _cfpr()["findings"] if f["class"] == "PLACEHOLDER"]


def test_size_xl_xxl_is_ordinary_text():
    r = run([("Size XXL", 50, 300, {})], [("Size XL", 50, 300, {})])
    assert r["findings"] and all(f["class"] != "PLACEHOLDER" for f in r["findings"])


# ── LOWMARK ───────────────────────────────────────────────────────────

def _punct(mark, conf, text=None, side="b"):
    t = text or ("%s Company Limited" % mark)
    s = t.index(mark)
    one = {"frag": mark, "conf": conf, "text": t, "span": [s, s + len(mark)]}
    empty = {"frag": "", "conf": None, "text": t.replace(mark, "").strip(), "span": [0, 0]}
    return {"class": "PUNCT", "severity": "yellow",
            "a": one if side == "a" else empty, "b": one if side == "b" else empty}


def test_lowmark_rules():
    assert compare.is_lowmark(_punct("•", 0.33))
    assert compare.is_lowmark(_punct("|", 0.40, side="a"))
    assert not compare.is_lowmark(_punct("•", 0.70)), "Vision มั่นใจ ⇒ ไม่พับ"
    assert not compare.is_lowmark(_punct("%", 0.30)), "เครื่องหมายสำคัญไม่พับ"
    assert not compare.is_lowmark(_punct("*", 0.30))
    assert not compare.is_lowmark(_punct(".", 0.30, text="Fat 1.5 g")), "ติดตัวเลข (จุดทศนิยม) ไม่พับ"
    f = _punct("•", 0.33)
    f["curved"] = True
    assert not compare.is_lowmark(f)
    f = _punct("•", 0.33)
    f["severity"] = "red"
    assert not compare.is_lowmark(f)
    f = _punct("•", 0.33)
    f["a"]["frag"] = ","
    assert not compare.is_lowmark(f), "มีทั้งสองฝั่ง = ไม่ใช่เครื่องหมายเดี่ยว"


def test_fold_lowmark_moves_without_deleting(monkeypatch):
    fs = [_punct("•", 0.33), _punct("•", 0.9)]
    keep, fold = compare.fold_lowmark(fs)
    assert len(keep) == 1 and len(fold) == 1
    assert fold[0]["severity"] == "lowmark" and fold[0]["lowmark_from"] == "yellow" and fold[0]["notes"]
    monkeypatch.setattr(config, "LOWMARK", False)
    keep, fold = compare.fold_lowmark([_punct("•", 0.33)])
    assert len(keep) == 1 and not fold


# ── KEEP_SUPERSCRIPT ──────────────────────────────────────────────────

def k(s):
    return compare.diff_key(s)[0]


def test_superscripts_keep_their_meaning(monkeypatch):
    assert k("10 m²") != k("10 m2")
    assert k("H₂O") != k("H2O")
    assert k("①") != k("1")
    assert k("BRAND™") == k("BRANDTM")
    assert k("AvoDermⓇ") == k("AvoDerm®"), "ตารางสมมูล Ⓡ→® ต้องยังทำงาน"
    assert k("Nº 5") == k("No 5")
    _flags(monkeypatch, False)
    assert k("10 m²") == k("10 m2")


# ── ข้อมูลจริง Friskies (ผลดิบ + เฉลยตรวจด้วยตา) ─────────────────────────

def _eval():
    import artwork_v2_eval as E
    return E


def test_friskies_every_real_difference_is_red_and_scored():
    E = _eval()
    ds = E.load_dataset(FRISKIES)
    labels = E.labels_from_file(os.path.join(FRISKIES, "labels.json"))
    res = E.replay(ds, pixel=False)
    sc = E.score(res, labels)
    assert not sc["missed"]
    assert sc["table"][("red", "REAL_DIFF")] == 3, sc["table"]
    assert not sc["table"].get(("yellow", "REAL_DIFF"))
    cls = {E.key_of(f): f["class"] for pr in res["pairs"] for f in pr["findings"]}
    cfpr = [k_ for k_, c in cls.items() if c == "PLACEHOLDER"]
    assert len(cfpr) == 1 and cfpr[0][1] == "XXXXXX"
    lm = [f for pr in res["pairs"] for f in pr.get("lowmark") or []]
    assert [f["b"]["frag"] for f in lm] == ["•"] and labels[E.key_of(lm[0])]["truth"] == "NO_DIFF"


def test_friskies_flags_off_is_the_old_result(monkeypatch):
    E = _eval()
    ds = E.load_dataset(FRISKIES)
    _flags(monkeypatch, False)
    res = E.replay(ds, pixel=False)
    sev = {E.key_of(f): (f["class"], f["severity"]) for pr in res["pairs"] for f in pr["findings"]}
    k18 = [k_ for k_ in sev if k_[1] == "XXXXXX"]
    assert sev[k18[0]] == ("NUMBER", "yellow"), "เดิม CFPR เป็นเหลือง (Vision ไม่มั่นใจรหัสฝั่ง A)"
    assert not any(pr.get("lowmark") for pr in res["pairs"])


def test_eval_tool_cli_and_language_hints(capsys):
    E = _eval()
    assert E.main([FRISKIES, "--labels", os.path.join(FRISKIES, "labels.json"), "--no-pixel"]) == 0
    out = capsys.readouterr().out
    assert "ของจริงตามเฉลยเป็นแดง/เหลืองครบ" in out
    h = E.suggest_hints(E.load_dataset(FRISKIES))
    assert h["hints"] == ["ko", "th", "en"]


# ── ทุกชุดข้อมูลสถานีที่มี Log: ของจริงไม่หาย · แดงไม่เพิ่ม · เหลืองเพิ่มได้ไม่เกิน 1 ─────

def _logs():
    return sorted(glob.glob(os.path.join(D, "**", "*log*.txt"), recursive=True))


def _run_log(p):
    (Wa, Ha, A), (Wb, Hb, B) = p["A"], p["B"]
    A = textmodel.parse(fta_from_lines(A, Wa, Ha), Wa, Ha)["lines"]
    B = textmodel.parse(fta_from_lines(B, Wb, Hb), Wb, Hb)["lines"]
    fs = compare.compare(A, B, (Wa, Ha), (Wb, Hb))["findings"]
    for f in fs:                       # การอ่านซ้ำปิด (pipeline)
        if f["severity"] == "red" and (f["class"] == "PUNCT" or f.get("curved")):
            f["severity"] = "yellow"
    keep, _ = compare.fold_lowmark(fs)
    return keep


@pytest.mark.parametrize("path", _logs(), ids=[os.path.relpath(p, D) for p in _logs()])
def test_station_logs_no_real_item_lost_and_no_new_red(path, monkeypatch):
    for n, p in load_log(path).items():
        if "A" not in p or "B" not in p:
            continue
        _flags(monkeypatch, False)
        old = _run_log(p)
        _flags(monkeypatch, True)
        new = _run_log(p)
        red = lambda fs: sum(f["severity"] == "red" for f in fs)  # noqa: E731
        yel = lambda fs: sum(f["severity"] == "yellow" for f in fs)  # noqa: E731
        assert red(new) <= red(old), (path, n)
        assert yel(new) <= yel(old) + 1, (path, n, yel(old), yel(new))
        o = {(f["a"]["frag"], f["b"]["frag"]) for f in old if f["severity"] == "red"}
        nw = {(f["a"]["frag"], f["b"]["frag"]) for f in new}
        lost = o - nw
        # หายได้เฉพาะแดงที่ได้คำอธิบายใหม่ (เช่น "1" ที่ต่อแถวได้แล้ว) — ต้องไม่ใช่ข้อความยาว
        assert all(len(a + b) <= 1 for a, b in lost), (path, n, lost)


# ── PIXEL_RASTER_NOTE ─────────────────────────────────────────────────

def test_raster_pair_diff_says_it_is_not_evidence(tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz")
    pytest.importorskip("cv2")
    from PIL import Image

    import test_artwork_v2_raster as R
    from artwork_v2 import jobs, keystore, pipeline, vision_client

    d = tmp_path / "v2"
    monkeypatch.setattr(config, "DATA_DIR", str(d))
    monkeypatch.setattr(config, "JOBS_DIR", str(d / "jobs"))
    monkeypatch.setattr(config, "SECRET_DIR", str(d / "secret"))
    monkeypatch.setattr(config, "KEY_FILE", str(d / "secret" / "key.json"))
    monkeypatch.delenv(config.KEY_ENV, raising=False)
    monkeypatch.setattr(config, "RETRY_WAIT_S", 0.0)
    monkeypatch.setattr(config, "PIXEL_VERIFY", True)
    monkeypatch.setattr(config, "PIXEL_RASTER", True)
    os.makedirs(config.JOBS_DIR)
    pa = R._vec(tmp_path / "a.pdf")
    rows = R._rows()
    with fitz.open(pa) as doc:
        hit = doc[0].search_for("chicken")
    word = [r_ for r_ in hit if abs(r_.y1 - (30 + 18 * 3)) < 6][0]
    pb = R._scan(tmp_path / "b.pdf", pa, 300, 92, erase=[(word.x0, word.y0, word.x1, word.y1)])

    def fake(groups, poster=None, key=None):
        res = {}
        for gi, g in enumerate(groups):
            for it in g:
                Wi, Hi = Image.open(io.BytesIO(it["jpeg"])).size
                ls = R._line_boxes(pa, Wi, Hi)
                if it["id"].endswith("b"):
                    ls = [(t.replace(rows[3], rows[3].replace("chicken", "chickem")), b, c) for t, b, c in ls]
                res[it["id"]] = {"ok": True, "error": "", "fta": fta_from_lines(ls, Wi, Hi), "request_index": gi}
        return {"results": res, "calls": [{"index": 0, "phase": "main", "images": [], "json_bytes": 10,
                                           "status": 200, "attempts": 1, "ms": 1, "error": "", "at": "t"}]}
    jid = jobs.create(("a.pdf", open(pa, "rb").read()), ("b.pdf", open(pb, "rb").read()))["id"]
    keystore.save("AIza" + "x" * 35)
    monkeypatch.setattr(vision_client, "annotate", fake)
    zone = [{"a": {"page": 0, "bbox": [0, 0, 1, 1]}, "b": {"page": 0, "bbox": [0, 0, 1, 1]}}]
    r = pipeline.run(jid, zone)
    f = [f for f in r["pairs"][0]["findings"] if "chicke" in f["a"]["text"]]
    assert f and f[0]["pixel"]["status"] == "DIFF" and f[0]["pixel"].get("raster")
    assert any("ภาพสแกน" in n and "ไม่ใช่หลักฐาน" in n for n in f[0]["notes"])
    assert "diff_raster=1" in r["log_text"] and "PIXEL_RASTER_NOTE=True" in r["log_text"]
    monkeypatch.setattr(config, "PIXEL_RASTER_NOTE", False)
    monkeypatch.setattr(config, "RUN_COOLDOWN_S", 0.0)
    r = pipeline.run(jid, zone)
    f = [f for f in r["pairs"][0]["findings"] if "chicke" in f["a"]["text"]]
    assert f and not f[0]["pixel"].get("raster") and "diff_raster" not in r["log_text"]


def test_unpaired_line_that_only_looks_like_a_wrap_is_reported(monkeypatch):
    """บรรทัด ``12`` ที่ไม่มีคู่ "คร่อมรอยต่อ" ``Weight 1`` ⏎ ``2 kg`` ของอีกฝั่ง — เดิมทิ้งเป็นการตัดบรรทัด
    ทั้งที่อีกฝั่งไม่มี ``12`` แยกเลย"""
    a = [("Weight 1", 50, 300, {}), ("2 kg net", 50, 340, {}), ("12", 600, 600, {})]
    b = [("Weight 1", 50, 300, {}), ("2 kg net", 50, 340, {})]
    f = [f for f in run(a, b)["findings"] if f["a"]["frag"] == "12"]
    assert f and f[0]["class"] == "MISSING_IN_B" and f[0]["reflow_unbalanced"]
    _flags(monkeypatch, False)
    assert not [f for f in run(a, b)["findings"] if f["a"]["frag"] == "12"]


def test_pipeline_folds_lowmark_and_logs_it(tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz")
    from PIL import Image

    from artwork_v2 import jobs, keystore, pipeline, vision_client

    d = tmp_path / "v2"
    for k_, v in (("DATA_DIR", d), ("JOBS_DIR", d / "jobs"), ("SECRET_DIR", d / "secret"),
                  ("KEY_FILE", d / "secret" / "key.json")):
        monkeypatch.setattr(config, k_, str(v))
    monkeypatch.delenv(config.KEY_ENV, raising=False)
    monkeypatch.setattr(config, "PIXEL_VERIFY", False)
    os.makedirs(config.JOBS_DIR)

    def fake(groups, poster=None, key=None):
        res = {}
        for g in groups:
            for it in g:
                Wi, Hi = Image.open(io.BytesIO(it["jpeg"])).size
                t = "Made by Example Pet Company Limited"
                ls = [(t, 40, 60, {}), ("Net Wt 85 g", 40, 120, {})]
                if it["id"].endswith("b"):      # เส้นประที่ขอบอ่านเป็น "•" (Vision มั่นใจ 0.33) หน้าบรรทัด
                    ls[0] = ("• " + t, 20, 60, {"confs": [0.33] + [0.98] * (len(t) + 2)})
                res[it["id"]] = {"ok": True, "error": "", "fta": fta(ls, Wi, Hi), "request_index": 0}
        return {"results": res, "calls": [{"index": 0, "phase": "main", "images": [], "json_bytes": 1,
                                           "status": 200, "attempts": 1, "ms": 1, "error": "", "at": "t"}]}

    def pdf():
        doc = fitz.open()
        doc.new_page(width=400, height=300).insert_text((20, 30), "x")
        return doc.tobytes()
    jid = jobs.create(("a.pdf", pdf()), ("b.pdf", pdf()))["id"]
    keystore.save("AIza" + "y" * 35)
    monkeypatch.setattr(vision_client, "annotate", fake)
    r = pipeline.run(jid, [{"a": {"page": 0, "bbox": [0, 0, 1, 1]}, "b": {"page": 0, "bbox": [0, 0, 1, 1]}}])
    p = r["pairs"][0]
    assert [f["b"]["frag"] for f in p.get("lowmark") or []] == ["•"]
    assert not [f for f in p["findings"] if f["b"]["frag"] == "•"]
    assert r["verdict"] == "PASS" and any("เครื่องหมายเดี่ยว" in x for x in r["reasons"])
    assert "LOWMARK=True" in r["log_text"] and "lowmark" in r["log_text"].lower()
    assert p["lowmark"][0].get("id"), "ต้องมีเลขจุด (ชี้ได้บนภาพ)"
