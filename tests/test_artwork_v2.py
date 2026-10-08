"""เทสต์ Artwork V2 (Cloud Vision) — ไม่ยิงเน็ตจริง ใช้คำตอบปลอมตามสเปกของ Vision

ครอบคลุม: การประกอบบรรทัดจาก break · ตัวเทียบ (เคสจริงที่ห้ามพลาด) ·
การเก็บ/ซ่อน API key · ตัวเรียก API (แพ็กคำขอ · ลองซ้ำ · error ต่อภาพ) ·
การเรนเดอร์โซน · ลำดับงานทั้งรอบ · route + สิทธิ์ · การแยกขาดจากโหมดเดิม
"""

from __future__ import annotations

import io
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from artwork_v2_fake import fta, line_para  # noqa: E402

from artwork_v2 import (compare, config, imaging, jobs, keystore, pipeline,  # noqa: E402
                        textmodel, vision_client)

fitz = pytest.importorskip("fitz")
from PIL import Image  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    d = tmp_path / "v2"
    monkeypatch.setattr(config, "DATA_DIR", str(d))
    monkeypatch.setattr(config, "JOBS_DIR", str(d / "jobs"))
    monkeypatch.setattr(config, "SECRET_DIR", str(d / "secret"))
    monkeypatch.setattr(config, "KEY_FILE", str(d / "secret" / "key.json"))
    monkeypatch.delenv(config.KEY_ENV, raising=False)
    monkeypatch.setattr(config, "RETRY_WAIT_S", 0.0)
    os.makedirs(config.JOBS_DIR)
    yield


def _cmp(la, lb):
    A = textmodel.parse(fta(la), 1000, 1000)["lines"]
    B = textmodel.parse(fta(lb), 1000, 1000)["lines"]
    return compare.compare(A, B)


def _kinds(r):
    return sorted((f["class"], f["severity"]) for f in r["findings"])


# ── textmodel ─────────────────────────────────────────────────────────

def test_lines_rebuilt_from_breaks_and_spaces():
    p = textmodel.parse(fta([("Net weight 85 g", 10, 10), ("Fat 1.5g", 10, 50)]), 1000, 1000)
    assert [l["text"] for l in p["lines"]] == ["Net weight 85 g", "Fat 1.5g"]
    assert p["stats"]["lines"] == 2 and p["stats"]["breaks"] == {"LINE_BREAK": 2}


def test_eol_sure_space_inside_paragraph_splits_lines():
    para = line_para("first line", 10, 10, end="EOL_SURE_SPACE")
    para2 = line_para("second line", 10, 40)
    doc = {"pages": [{"blocks": [{"blockType": "TEXT",
                                  "paragraphs": [{"words": para["words"] + para2["words"]}]}]}]}
    lines = textmodel.parse(doc, 500, 500)["lines"]
    assert [l["text"] for l in lines] == ["first line", "second line"]


def test_hyphen_break_marks_soft_hyphen():
    doc = fta([("Pantothe", 10, 10, {"end": "HYPHEN"}), ("nate", 10, 40)])
    lines = textmodel.parse(doc, 500, 500)["lines"]
    assert lines[0]["soft_hyphen"] is True and lines[1]["soft_hyphen"] is False


def test_missing_zero_coordinates_default_to_zero():
    """JSON จริงละพิกัดที่เป็น 0 — คำชิดขอบต้องไม่หายและต้องไม่พัง"""
    doc = fta([("EDGE 12", 0, 0, {"drop_zero": True})])
    lines = textmodel.parse(doc, 500, 500)["lines"]
    assert lines[0]["text"] == "EDGE 12"
    assert lines[0]["box"][0] == 0.0 and lines[0]["box"][1] == 0.0


def test_picture_and_barcode_blocks_are_reported_not_compared():
    doc = fta([("hello", 10, 10)])
    doc["pages"][0]["blocks"].append({"blockType": "BARCODE", "boundingBox": {
        "vertices": [{"x": 5, "y": 100}, {"x": 50, "y": 100}, {"x": 50, "y": 150}, {"x": 5, "y": 150}]},
        "paragraphs": [line_para("0123456789", 5, 100)]})
    p = textmodel.parse(doc, 500, 500)
    assert [l["text"] for l in p["lines"]] == ["hello"]
    assert p["stats"]["skipped_blocks"] == 1 and p["stats"]["blocks_by_type"]["BARCODE"] == 1


def test_normalized_vertices_are_scaled():
    sym = {"text": "A", "confidence": 0.9, "boundingBox": {"normalizedVertices": [
        {"x": 0.1, "y": 0.2}, {"x": 0.2, "y": 0.2}, {"x": 0.2, "y": 0.3}, {"x": 0.1, "y": 0.3}]},
        "property": {"detectedBreak": {"type": "LINE_BREAK"}}}
    doc = {"pages": [{"blocks": [{"blockType": "TEXT", "paragraphs": [{"words": [{"symbols": [sym]}]}]}]}]}
    ln = textmodel.parse(doc, 1000, 500)["lines"][0]
    assert ln["box"] == (100.0, 100.0, 200.0, 150.0)


def test_parse_survives_garbage():
    assert textmodel.parse({}, 10, 10)["lines"] == []
    assert textmodel.parse({"pages": [{"blocks": [{}]}]}, 10, 10)["lines"] == []


# ── compare: ความต่างจริงที่ห้ามพลาด ────────────────────────────────────

def test_percent_change_is_number_red():
    r = _cmp([("Sodium 475 mg 20%", 10, 10)], [("Sodium 475 mg 24%", 10, 10)])
    assert _kinds(r) == [("NUMBER", "red")]
    f = r["findings"][0]
    assert f["word_a"] == "20%" and f["word_b"] == "24%"
    assert f["a"]["box"] and f["b"]["box"]


def test_decimal_point_difference_is_caught():
    r = _cmp([("Fat 1.5g", 10, 10)], [("Fat 15g", 10, 10)])
    assert _kinds(r) == [("NUMBER", "red")]


def test_case_difference_is_caught():
    r = _cmp([("D-Calcium Pantothenate", 10, 10)], [("D-calcium Pantothenate", 10, 10)])
    assert _kinds(r) == [("CASE", "red")]


@pytest.mark.parametrize("a,b", [
    ("Copper Sulfate.", "Copper Sulphate Pentahydrate."),
    ("Irwindale, CA 91706, USA", "Irwindale Park, CA 91706, USA"),
    ("For all Breeds", "For all Breed"),
])
def test_text_differences_from_real_jobs(a, b):
    r = _cmp([(a, 10, 10)], [(b, 10, 10)])
    assert any(f["class"] == "TEXT" for f in r["findings"])
    assert all(f["pair_method"] != "unpaired" for f in r["findings"]), "ต้องจับคู่เป็นบรรทัดเดียว"


def test_duplicate_line_missing_on_one_side_is_not_masked():
    """จุดอ่อนของระบบเดิม (set ของบรรทัด) — บรรทัดซ้ำในแผงอื่นห้ามกลบ"""
    r = _cmp([("Net weight 85 g", 10, 10), ("Net weight 85 g", 10, 500)],
             [("Net weight 85 g", 10, 10)])
    assert [f["class"] for f in r["findings"]] == ["MISSING_IN_B"]


def test_table_value_swap_is_caught_by_position():
    """ค่าตัวเลขล้วนต้องจับคู่ด้วยตำแหน่ง ไม่งั้นสลับกันเงียบ ๆ"""
    r = _cmp([("10%", 600, 100), ("20%", 600, 140)], [("20%", 600, 100), ("10%", 600, 140)])
    assert len([f for f in r["findings"] if f["class"] == "NUMBER"]) == 2


def test_line_wrap_difference_is_not_a_finding():
    r = _cmp([("Ingredients: chicken, rice,", 10, 10), ("water", 10, 40)],
             [("Ingredients: chicken,", 10, 10), ("rice, water", 10, 40)])
    assert r["findings"] == []


def test_whitespace_only_difference_is_ignored():
    r = _cmp([("Size 4x14 cm", 10, 10)], [("Size 4 x 14 cm", 10, 10)])
    assert r["findings"] == []


def test_identical_is_clean_and_full_coverage():
    t = [("Sodium 475 mg 20%", 10, 10), ("Fat 1.5g", 10, 50)]
    r = _cmp(t, t)
    assert r["findings"] == [] and r["coverage"] == 1.0


def test_arabic_indic_digits_equal_ascii():
    r = _cmp([("Sodium ٢٠%", 10, 10)], [("Sodium 20%", 10, 10)])
    assert r["findings"] == []


def test_confident_punctuation_is_red_candidate():
    """ลูกน้ำหายจริง (Hwy, ↔ Hwy) ความมั่นใจสูง = แดงได้ — แต่ต้องผ่านการอ่านซ้ำ"""
    r = _cmp([("Thai Union Co., Ltd.", 10, 10)], [("Thai Union Co. Ltd.", 10, 10)])
    assert _kinds(r) == [("PUNCT", "red")]


def test_punctuation_flag_off_keeps_yellow(monkeypatch):
    monkeypatch.setattr(config, "PUNCT_CAN_FAIL", False)
    r = _cmp([("Thai Union Co., Ltd.", 10, 10)], [("Thai Union Co. Ltd.", 10, 10)])
    assert _kinds(r) == [("PUNCT", "yellow")]


def test_low_confidence_difference_is_yellow():
    confs = [0.98] * 15 + [0.4, 0.98]
    A = textmodel.parse(fta([("Sodium 475 mg 20%", 10, 10)]), 1000, 1000)["lines"]
    B = textmodel.parse(fta([("Sodium 475 mg 24%", 10, 10, {"confs": confs})]), 1000, 1000)["lines"]
    r = compare.compare(A, B)
    assert [f["severity"] for f in r["findings"]] == ["yellow"]


def test_extra_line_in_b_is_reported():
    r = _cmp([("Net weight 85 g", 10, 10)], [("Net weight 85 g", 10, 10), ("New claim here", 10, 60)])
    assert [f["class"] for f in r["findings"]] == ["EXTRA_IN_B"]
    assert r["coverage_b"] < 1.0


def test_trailing_hyphen_from_new_model_is_reflow():
    r = _cmp([("D-Calcium Pantothe-", 10, 10), ("nate", 10, 40)],
             [("D-Calcium Pantothenate", 10, 10)])
    assert r["findings"] == []


# ── keystore ─────────────────────────────────────────────────────────

KEY = "AIza" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r"


def test_key_saved_masked_and_never_returned_whole():
    keystore.save(KEY)
    st = keystore.status()
    assert st["configured"] and st["source"] == "ui"
    assert st["masked"].endswith(KEY[-4:]) and KEY not in json.dumps(st)
    assert keystore.get_key() == (KEY, "ui")


def test_env_key_wins(monkeypatch):
    keystore.save(KEY)
    monkeypatch.setenv(config.KEY_ENV, "AIza" + "Z" * 35)
    assert keystore.get_key()[1] == "env"
    assert keystore.status()["env_overrides_ui"] is True


def test_delete_key():
    keystore.save(KEY)
    assert keystore.delete() is True
    assert keystore.status()["configured"] is False


@pytest.mark.parametrize("bad", ["", "   ", "short", "has space " + "x" * 30, "ก" * 30])
def test_invalid_key_rejected(bad):
    with pytest.raises(ValueError):
        keystore.save(bad)


def test_redact_removes_key_everywhere():
    s = "error at https://x/v1?key=%s&a=1 and %s and AIzaSyOTHERKEY0123456789abcdefghij" % (KEY, KEY)
    out = keystore.redact(s, KEY)
    assert KEY not in out and "AIzaSy" not in out


# ── vision_client ────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, data=None, text=""):
        self.status_code = status
        self._data = data
        self.text = text or json.dumps(data or {})

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


def _ok_json(n):
    return {"responses": [{"fullTextAnnotation": fta([("hi %d" % i, 10, 10)])} for i in range(n)]}


def test_request_body_and_header():
    seen = {}

    def poster(url, data, headers, timeout):
        seen.update(url=url, body=json.loads(data), headers=headers)
        return _Resp(200, _ok_json(2))

    r = vision_client.annotate([[{"id": "a", "jpeg": b"x" * 10}, {"id": "b", "jpeg": b"y" * 10}]],
                               poster=poster, key=KEY)
    assert seen["url"] == config.ENDPOINT + "/v1/images:annotate"
    assert seen["headers"]["X-Goog-Api-Key"] == KEY
    assert KEY not in json.dumps(seen["body"]) and "key=" not in seen["url"]
    feat = seen["body"]["requests"][0]["features"][0]
    assert feat == {"type": "DOCUMENT_TEXT_DETECTION", "model": config.MODEL}
    assert r["results"]["a"]["ok"] and r["results"]["b"]["ok"]
    assert r["results"]["a"]["request_index"] == r["results"]["b"]["request_index"] == 0


def test_no_key_never_calls_google():
    called = []
    r = vision_client.annotate([[{"id": "a", "jpeg": b"x"}]],
                               poster=lambda *a, **k: called.append(1), key="")
    assert not called and not r["results"]["a"]["ok"]


def test_retry_on_503_then_success():
    seq = [_Resp(503, {"error": {"message": "busy"}}), _Resp(200, _ok_json(1))]
    r = vision_client.annotate([[{"id": "a", "jpeg": b"x"}]], poster=lambda *a, **k: seq.pop(0),
                               key=KEY)
    assert r["results"]["a"]["ok"] and r["calls"][0]["attempts"] == 2


def test_no_retry_on_403_and_hint_without_key():
    n = []

    def poster(*a, **k):
        n.append(1)
        return _Resp(403, {"error": {"status": "PERMISSION_DENIED",
                                     "message": "API key %s not valid" % KEY}})

    r = vision_client.annotate([[{"id": "a", "jpeg": b"x"}]], poster=poster, key=KEY)
    assert len(n) == 1
    err = r["results"]["a"]["error"]
    assert "403" in err and KEY not in err


def test_per_image_error_inside_200():
    data = {"responses": [{"fullTextAnnotation": fta([("ok", 1, 1)])},
                          {"error": {"code": 3, "message": "Bad image data."}}]}
    r = vision_client.annotate([[{"id": "a", "jpeg": b"x"}, {"id": "b", "jpeg": b"y"}]],
                               poster=lambda *a, **k: _Resp(200, data), key=KEY)
    assert r["results"]["a"]["ok"] and not r["results"]["b"]["ok"]
    assert "Bad image data" in r["results"]["b"]["error"]


def test_connection_error_is_reported_not_raised():
    def poster(*a, **k):
        raise ConnectionError("down ?key=%s" % KEY)

    r = vision_client.annotate([[{"id": "a", "jpeg": b"x"}]], poster=poster, key=KEY)
    assert not r["results"]["a"]["ok"] and KEY not in r["results"]["a"]["error"]


def test_pack_keeps_pairs_and_splits_oversize(monkeypatch):
    monkeypatch.setattr(config, "MAX_REQUEST_BYTES", 5000)
    small = [[{"id": "p1_a", "jpeg": b"x" * 500}, {"id": "p1_b", "jpeg": b"x" * 500}],
             [{"id": "p2_a", "jpeg": b"x" * 500}, {"id": "p2_b", "jpeg": b"x" * 500}]]
    reqs = vision_client.pack(small)
    assert [[i["id"] for i in r] for r in reqs] == [["p1_a", "p1_b", "p2_a", "p2_b"]]
    big = [[{"id": "a", "jpeg": b"x" * 3000}, {"id": "b", "jpeg": b"x" * 3000}]]
    assert [[i["id"] for i in r] for r in vision_client.pack(big)] == [["a"], ["b"]]


def test_pack_respects_16_images():
    g = [[{"id": "p%d_%s" % (i, s), "jpeg": b"x"} for s in "ab"] for i in range(9)]
    reqs = vision_client.pack(g)
    assert all(len(r) <= 16 for r in reqs) and sum(len(r) for r in reqs) == 18


# ── imaging ──────────────────────────────────────────────────────────

def _pdf(lines, w=400, h=300):
    d = fitz.open()
    p = d.new_page(width=w, height=h)
    for i, t in enumerate(lines):
        p.insert_text((20, 40 + i * 30), t, fontsize=12)
    return d.tobytes()


def test_clamp_bbox():
    assert imaging.clamp_bbox([-0.1, 0.5, 0.5, 0.9]) == [0.0, 0.5, 0.4, 0.5]
    assert imaging.clamp_bbox([0.2, 0.2, 0.0, 0.5]) is None
    assert imaging.clamp_bbox(["x", 1, 1, 1]) is None
    assert imaging.clamp_bbox([float("nan"), 0, 1, 1]) is None


def test_pdf_zone_rendered_to_image_reaches_min_side(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(_pdf(["hello"]))
    img, info = imaging.Source(str(p)).render_zone(0, [0.0, 0.0, 0.2, 0.2])
    # โซน 80 pt ⇒ ต้องการ 1440 dpi แต่ติดเพดาน PDF_ZONE_DPI_MAX (1200)
    assert info["dpi"] == pytest.approx(config.PDF_ZONE_DPI_MAX, abs=0.5)
    assert max(img.shape[:2]) >= 80 / 72 * config.PDF_ZONE_DPI_MAX - 2


def test_hidden_pdf_text_has_no_ink_in_rendered_zone(tmp_path):
    """ข้อความซ่อนใน PDF ต้องไม่ติดไปกับภาพที่ส่ง (เหตุผลที่ส่งภาพ ไม่ส่ง PDF)"""
    d = fitz.open()
    pg = d.new_page(width=300, height=100)
    pg.insert_text((20, 50), "HIDDEN 185 g", fontsize=20, render_mode=3)
    p = tmp_path / "h.pdf"
    p.write_bytes(d.tobytes())
    img, _ = imaging.Source(str(p)).render_zone(0, [0, 0, 1, 1])
    assert img.min() > 240


def test_exif_rotation_is_applied(tmp_path):
    im = Image.new("RGB", (400, 200), "white")
    ex = im.getexif()
    ex[0x0112] = 6                       # หมุน 90° ตามเข็ม
    p = tmp_path / "photo.jpg"
    im.save(p, exif=ex.tobytes())
    s = imaging.Source(str(p))
    assert s.exif_orientation == 6 and s.info()["image_px"] == [200, 400]


def test_fit_jpeg_downscales_with_warning():
    import numpy as np
    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, (800, 800, 3), dtype=np.uint8)
    data, sent, info = imaging.fit_jpeg(img, 60_000)
    assert len(data) <= 60_000 and info["downscale"] < 1.0 and info["warnings"]
    assert sent.shape[0] < 800


# ── pipeline ─────────────────────────────────────────────────────────

def _fake_annotate(texts_a, texts_b, crop_a=None, crop_b=None, fail_side=None):
    def fake(groups, poster=None, key=None):
        res, ids = {}, []
        for g in groups:
            for it in g:
                ids.append(it["id"])
                W, H = Image.open(io.BytesIO(it["jpeg"])).size
                side = it["id"][-1]
                crop = it["id"].startswith("rr")
                T = (crop_a if crop and crop_a is not None else texts_a) if side == "a" else \
                    (crop_b if crop and crop_b is not None else texts_b)
                if fail_side == side and not crop:
                    res[it["id"]] = {"ok": False, "error": "Vision error 3: Bad image",
                                     "request_index": 0}
                    continue
                lines = [(t, int(0.05 * W), int(H * (0.1 + 0.8 * i / max(1, len(T)))),
                          {"cw": max(4, W // 40), "h": max(8, H // 15)}) for i, t in enumerate(T)]
                res[it["id"]] = {"ok": True, "error": "", "fta": fta(lines, W, H),
                                 "request_index": 0}
        return {"results": res, "calls": [{"index": 0, "phase": "main", "images": ids,
                                           "json_bytes": 10, "status": 200, "attempts": 1,
                                           "ms": 1, "error": "", "model_requested": config.MODEL,
                                           "endpoint": config.ENDPOINT, "at": "t"}]}
    return fake


FULL = [{"a": {"page": 0, "bbox": [0, 0, 1, 1]}, "b": {"page": 0, "bbox": [0, 0, 1, 1]}}]
TA = ["Sodium 475 mg 20%", "Fat 1.5g", "Net weight 85 g"]
TB = ["Sodium 475 mg 24%", "Fat 1.5g", "Net weight 85 g"]


def _job():
    return jobs.create(("a.pdf", _pdf(TA)), ("b.pdf", _pdf(TB)))["id"]


def test_run_fail_confirmed_by_reread(monkeypatch):
    monkeypatch.setattr(vision_client, "annotate", _fake_annotate(TA, TB))
    keystore.save(KEY)
    r = pipeline.run(_job(), FULL)
    assert r["verdict"] == "FAIL"
    f = r["pairs"][0]["findings"][0]
    assert f["severity"] == "red" and "ยืนยัน" in " ".join(f["notes"])
    assert r["reread"]["confirmed"] == 1
    # Log ต้องมีค่าที่จำเป็น และต้องไม่มีกุญแจ
    log = r["log_text"]
    for must in ("VERDICT=FAIL", "[SETTINGS]", "[FILES]", "[VISION REQUESTS]", "[PAIR 1]",
                 "render_dpi=", "sent_px=", "conf_mean=", "F1 RED NUMBER", "[REREAD]",
                 "OCR lines A", "api_key: source=ui"):
        assert must in log, must
    assert KEY not in log
    d = jobs.run_dir(r["job"], r["run"])
    for name in ("result.json", "log.txt", "img/p1_a.jpg", "img/p1_b.jpg", "raw/p1_a.json"):
        assert os.path.isfile(os.path.join(d, name)), name
    assert KEY not in open(os.path.join(d, "result.json"), encoding="utf-8").read()


def test_run_pass_when_identical(monkeypatch):
    monkeypatch.setattr(vision_client, "annotate", _fake_annotate(TA, TA))
    r = pipeline.run(_job(), FULL)
    assert r["verdict"] == "PASS" and r["pairs"][0]["coverage"] == 1.0


def test_reread_equal_downgrades_but_keeps_finding(monkeypatch):
    monkeypatch.setattr(vision_client, "annotate",
                        _fake_annotate(TA, TB, crop_a=["Sodium 475 mg 20%"],
                                       crop_b=["Sodium 475 mg 20%"]))
    r = pipeline.run(_job(), FULL)
    f = r["pairs"][0]["findings"]
    assert len(f) == 1 and f[0]["severity"] == "yellow"
    assert r["verdict"] == "REVIEW" and r["reread"]["downgraded"] == 1


def test_one_side_unreadable_is_never_pass(monkeypatch):
    monkeypatch.setattr(vision_client, "annotate", _fake_annotate(TA, TA, fail_side="b"))
    r = pipeline.run(_job(), FULL)
    assert r["verdict"] == "UNREADABLE"
    assert "Bad image" in r["log_text"]


def test_empty_text_both_sides_is_review(monkeypatch):
    monkeypatch.setattr(vision_client, "annotate", _fake_annotate([], []))
    r = pipeline.run(_job(), FULL)
    assert r["verdict"] == "REVIEW"


def test_reread_disabled_keeps_red_with_note(monkeypatch):
    monkeypatch.setattr(config, "REREAD_ENABLED", False)
    monkeypatch.setattr(vision_client, "annotate", _fake_annotate(TA, TB))
    r = pipeline.run(_job(), FULL)
    f = r["pairs"][0]["findings"][0]
    assert r["verdict"] == "FAIL" and "ไม่ได้อ่านซ้ำ" in f["notes"][0]


TPA = ["Thai Union Co., Ltd. Bangkok", "Fat 1.5g"]
TPB = ["Thai Union Co. Ltd. Bangkok", "Fat 1.5g"]


def test_punctuation_without_reread_is_never_red(monkeypatch):
    """เครื่องหมายวรรคตอนแดงได้ **เฉพาะเมื่ออ่านซ้ำยืนยันแล้ว**"""
    monkeypatch.setattr(config, "REREAD_ENABLED", False)
    monkeypatch.setattr(vision_client, "annotate", _fake_annotate(TPA, TPB))
    job = jobs.create(("a.pdf", _pdf(TPA)), ("b.pdf", _pdf(TPB)))["id"]
    r = pipeline.run(job, FULL)
    f = r["pairs"][0]["findings"]
    assert [x["class"] for x in f] == ["PUNCT"] and f[0]["severity"] == "yellow"
    assert r["verdict"] == "REVIEW"


def test_punctuation_confirmed_by_reread_is_red(monkeypatch):
    monkeypatch.setattr(vision_client, "annotate", _fake_annotate(TPA, TPB))
    job = jobs.create(("a.pdf", _pdf(TPA)), ("b.pdf", _pdf(TPB)))["id"]
    r = pipeline.run(job, FULL)
    assert r["verdict"] == "FAIL" and r["reread"]["confirmed"] == 1


def test_reread_uses_one_crop_per_line(monkeypatch):
    """3 จุดในบรรทัดเดียวกัน = ครอปเดียว (เดิมยิง Vision ซ้ำบรรทัดเดิม — Log จริง rr3/rr4)"""
    a = ["16321 Arrow Hwy, Irwindale Park, CA 91706", "Fat 1.5g"]
    b = ["16321 Arrow Hwy Irwindale, CA 91706 USA", "Fat 1.5g"]
    sent = []
    inner = _fake_annotate(a, b)

    def spy(groups, poster=None, key=None):
        sent.append([it["id"] for g in groups for it in g])
        return inner(groups, poster, key)
    monkeypatch.setattr(vision_client, "annotate", spy)
    job = jobs.create(("a.pdf", _pdf(a)), ("b.pdf", _pdf(b)))["id"]
    r = pipeline.run(job, FULL)
    reds = [f for f in r["pairs"][0]["findings"] if f["severity"] == "red"]
    assert len(reds) == 3 and r["reread"]["confirmed"] == 3
    assert r["reread"]["crops"] == 1 and sent[1] == ["rr1_a", "rr1_b"]
    assert "crops=1" in r["log_text"]


@pytest.mark.parametrize("bad", [None, [], [{"a": {"bbox": [0, 0, 1, 1]}}],
                                 [{"a": {"bbox": [0, 0, 0, 0]}, "b": {"bbox": [0, 0, 1, 1]}}],
                                 [FULL[0]] * 9])
def test_parse_pairs_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        pipeline.parse_pairs(bad)


def test_job_rejects_bad_file():
    with pytest.raises(ValueError):
        jobs.create(("a.exe", b"x"), ("b.pdf", _pdf(["x"])))
    with pytest.raises(ValueError):
        jobs.create(("a.pdf", b"not a pdf"), ("b.pdf", _pdf(["x"])))
    assert os.listdir(config.JOBS_DIR) == [], "งานที่สร้างไม่สำเร็จต้องไม่ค้าง"


# ── routes + สิทธิ์ ───────────────────────────────────────────────────

def _app(user=None, auth=False):
    from flask import Flask, g
    from artwork_v2.routes import artwork_v2_bp
    app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"),
                static_folder=os.path.join(ROOT, "static"))

    @app.before_request
    def _fake_auth():
        g.auth_enabled = auth
        g.current_user = user

    @app.context_processor
    def _ctx():
        return {"config_version": "test", "current_user": user, "auth_enabled": auth,
                "has_perm": lambda *a, **k: True}

    app.register_blueprint(artwork_v2_bp)
    return app.test_client()


STAFF = {"sub": "7", "username": "staff", "perms": ["inspect_artwork"]}
OTHER = {"sub": "8", "username": "other", "perms": ["inspect_artwork"]}
ADMIN = {"sub": "1", "username": "admin", "perms": ["inspect_artwork", "manage_users"]}


def test_page_renders():
    r = _app().get("/artwork_v2")
    assert r.status_code == 200 and "Artwork V2" in r.get_data(as_text=True)


def test_settings_requires_admin_when_auth_on():
    c = _app(STAFF, auth=True)
    assert c.post("/api/artwork_v2/settings", json={"key": KEY}).status_code == 403
    assert c.delete("/api/artwork_v2/settings").status_code == 403
    assert c.post("/api/artwork_v2/settings/test").status_code == 403
    st = c.get("/api/artwork_v2/settings").get_json()
    assert st["can_manage"] is False
    a = _app(ADMIN, auth=True)
    st = a.post("/api/artwork_v2/settings", json={"key": KEY}).get_json()
    assert st["configured"] and KEY not in json.dumps(st)


def test_settings_test_endpoint_reports_without_key(monkeypatch):
    c = _app()
    r = c.post("/api/artwork_v2/settings/test").get_json()
    assert r["ok"] is False and "API key" in r["error"]


def test_upload_run_and_artifacts(monkeypatch):
    monkeypatch.setattr(vision_client, "annotate", _fake_annotate(TA, TB))
    c = _app()
    r = c.post("/api/artwork_v2/jobs", data={
        "file_a": (io.BytesIO(_pdf(TA)), "a.pdf"), "file_b": (io.BytesIO(_pdf(TB)), "b.pdf")},
        content_type="multipart/form-data")
    assert r.status_code == 200, r.get_data(as_text=True)
    jid = r.get_json()["id"]
    assert c.get("/api/artwork_v2/jobs/%s/preview/a/0.png" % jid).status_code == 200
    assert c.get("/api/artwork_v2/jobs/%s/preview/a/5.png" % jid).status_code == 404
    res = c.post("/api/artwork_v2/jobs/%s/run" % jid, json={"pairs": FULL}).get_json()
    assert res["verdict"] == "FAIL"
    base = "/api/artwork_v2/jobs/%s/runs/%s" % (jid, res["run"])
    assert c.get(base).status_code == 200
    assert c.get(base + "/img/p1_a.jpg").status_code == 200
    assert c.get(base + "/log.txt").status_code == 200
    assert c.get(base + "/raw/p1_b.json").status_code == 200
    assert c.get(base + "/img/..%2Fresult.json").status_code == 404
    assert c.get("/api/artwork_v2/jobs/%s/runs/run_99" % jid).status_code == 404
    assert c.post("/api/artwork_v2/jobs/%s/run" % jid, json={"pairs": []}).status_code == 400


def test_job_of_other_user_is_blocked():
    c = _app(STAFF, auth=True)
    r = c.post("/api/artwork_v2/jobs", data={
        "file_a": (io.BytesIO(_pdf(TA)), "a.pdf"), "file_b": (io.BytesIO(_pdf(TB)), "b.pdf")},
        content_type="multipart/form-data")
    jid = r.get_json()["id"]
    assert _app(OTHER, auth=True).get("/api/artwork_v2/jobs/%s" % jid).status_code == 403
    assert _app(ADMIN, auth=True).get("/api/artwork_v2/jobs/%s" % jid).status_code == 200
    assert c.get("/api/artwork_v2/jobs/%s" % jid).status_code == 200
    ids = [j["id"] for j in _app(OTHER, auth=True).get("/api/artwork_v2/jobs").get_json()["jobs"]]
    assert jid not in ids
    assert c.get("/api/artwork_v2/jobs/../../etc").status_code == 404


def test_bad_job_id_is_404():
    assert _app().get("/api/artwork_v2/jobs/not_an_id").status_code == 404


# ── การแยกขาดจากระบบเดิม ──────────────────────────────────────────────

def test_v2_never_imports_old_artwork_module():
    import re
    d = os.path.join(ROOT, "artwork_v2")
    pat = re.compile(r"^\s*(from|import)\s+(artwork_check|inspectors)\b", re.M)
    for name in os.listdir(d):
        if name.endswith(".py"):
            src = open(os.path.join(d, name), encoding="utf-8").read()
            assert not pat.search(src), name


def test_v2_routes_are_permission_guarded():
    from auth import access
    assert access._required_permission("/artwork_v2") == "inspect_artwork"
    assert access._required_permission("/api/artwork_v2/jobs") == "inspect_artwork"


def test_data_dir_is_gitignored():
    gi = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
    assert "data/artwork_v2/" in gi


def test_numeric_cells_follow_zone_offset():
    """ลากโซน 🅱 เหลื่อมลงมา — ค่าตัวเลขที่ตรงกันต้องไม่ถูกฟ้องผิดแถว"""
    A = [("Fat", 100, 100), ("10%", 600, 100), ("Sodium", 100, 140), ("20%", 600, 140)]
    B = [("Fat", 100, 190), ("10%", 600, 190), ("Sodium", 100, 230), ("20%", 600, 230)]
    r = _cmp(A, B)
    assert r["findings"] == [] and r["pair_methods"].get("position") == 2


def test_hyphen_before_capital_is_kept():
    r = _cmp([("Vitamin D-", 10, 10), ("Calcium", 10, 40)], [("Vitamin D-Calcium", 10, 10)])
    assert r["findings"] == []
    # ขีดหายจริง (D-Calcium → DCalcium) ต้องถูกฟ้อง — ข้อจำกัดของ PoC: ขึ้นเป็น 2
    # รายการ (ท้ายบรรทัดต่าง + บรรทัดที่ไม่มีคู่) แทนที่จะเป็นรายการเดียว
    r = _cmp([("Vitamin D-", 10, 10), ("Calcium", 10, 40)], [("Vitamin DCalcium", 10, 10)])
    assert r["findings"], "ความต่างจริงห้ามหาย"


def test_concurrent_run_dirs_do_not_collide():
    import threading
    jid = _job()
    out = []
    ts = [threading.Thread(target=lambda: out.append(jobs.new_run_dir(jid))) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(set(out)) == 8
