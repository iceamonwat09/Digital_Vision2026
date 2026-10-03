"""Artwork V2 — ข้อความโค้ง/เอียง (การ์ดเดียว) + เศษอักขระ / ขอบโซน

A) ``compare.curved_lines`` / ``collapse_curved`` — จุดต่างบนบรรทัดที่เอียงจากแนวหลัก
   ของโซนเกิน ``TILT_ANGLE`` (+ เศษสั้นที่ตั้งตรงติดกัน เช่น "&" บนตราเดียวกัน)
   ยุบเป็นการ์ดเดียว · **ไม่มีจุดไหนถูกลบ** · แดงได้เฉพาะเมื่อการอ่านซ้ำยืนยัน
B) ``compare.is_debris`` — บรรทัดที่ไม่มีตัวอักษร/ตัวเลข และ (ความมั่นใจต่ำ หรือ
   ชิดขอบโซน) ⇒ รายการพับ ไม่นับเป็นเหลือง
"""

from __future__ import annotations

import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from artwork_v2_fake import fta, fta_from_lines, line_para, load_real  # noqa: E402

from artwork_v2 import compare, config, jobs, keystore, pipeline, textmodel, vision_client  # noqa: E402

fitz = pytest.importorskip("fitz")
from PIL import Image  # noqa: E402

KEY = "AIza" + "B" * 35
DATA = os.path.join(os.path.dirname(__file__), "data", "artwork_v2", "avoderm_m1m2_lines.txt")


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


def _lines(spec, W=1000, H=1000):
    """spec = [(text, x, y, kwargs)] — kwargs ของ line_para (angle · conf · cw · h)"""
    return textmodel.parse(fta(spec, W, H), W, H)["lines"]


def _cmp(sa, sb, size=(1000, 1000)):
    return compare.compare(_lines(sa, *size), _lines(sb, *size), size, size)


# ตราโค้งที่ OCR อ่านไม่นิ่ง: OMEGA-6/OMEGA-3 (เอียง 38°) · "&"/"8" (ตั้งตรง ติดตรา) ·
# FATTY/FATY ACIDS (เอียง 330°) — ข้อความหลักของโซนตั้งตรง
BODY = [("Natural recipe with real chicken and vegetables", 50, 100, {}),
        ("Fat 1.5g per serving for adult dogs", 50, 140, {})]
EMB_A = [("OMEGA-6", 500, 500, {"angle": 38}), ("&", 580, 500, {}),
         ("FATTY ACIDS", 500, 560, {"angle": 330})]
EMB_B = [("OMEGA-3", 500, 500, {"angle": 38}), ("8", 580, 500, {}),
         ("FATY ACIDS", 500, 560, {"angle": 330})]


# ── A: ตรวจหาบรรทัดโค้ง/เอียง ──────────────────────────────────────────

def test_tilted_line_beyond_threshold_is_curved_but_slight_tilt_is_not():
    L = _lines(BODY + [("STAMP", 500, 500, {"angle": 11}), ("LABEL", 500, 700, {"angle": 9})])
    got = {L[i]["text"] for i in compare.curved_lines(L)}
    assert got == {"STAMP"}


def test_whole_zone_rotated_is_not_curved():
    """โซนที่ข้อความหมุน 90° ทั้งโซน ⇒ แนวหลัก = 90 ⇒ ไม่มีบรรทัดไหน "เอียง" """
    L = _lines([(t, x, y, {"angle": 90}) for t, x, y, _ in BODY] + [("NET 85 g", 50, 300, {"angle": 92})])
    assert compare.dominant_angle(L) == 90.0
    assert compare.curved_lines(L) == set()


def test_short_upright_neighbor_joins_but_long_or_far_lines_do_not():
    spec = BODY + [("OMEGA-6", 500, 500, {"angle": 38}),
                   ("&", 580, 500, {}),                         # ติดตรา ⇒ เข้ากลุ่ม
                   ("Complete and balanced nutrition", 580, 520, {}),  # ยาว ⇒ ไม่เข้า
                   ("®", 900, 900, {})]                          # ไกล ⇒ ไม่เข้า
    L = _lines(spec)
    got = {L[i]["text"] for i in compare.curved_lines(L)}
    assert got == {"OMEGA-6", "&"}


def test_neighbor_rule_does_not_chain_through_upright_lines():
    spec = [("OMEGA-6", 500, 500, {"angle": 38}), ("&", 580, 500, {}),
            ("+", 620, 500, {})]                                  # ติด "&" แต่ไม่ติดตรา
    L = _lines(spec)
    got = {L[i]["text"] for i in compare.curved_lines(L)}
    assert "+" not in got and got >= {"OMEGA-6", "&"}


def test_curved_findings_are_flagged_and_collapse_into_one_card_without_loss():
    r = _cmp(BODY + EMB_A, BODY + EMB_B)
    curved = [f for f in r["findings"] if f.get("curved")]
    assert len(curved) == 3
    assert all(f["severity"] == "red" for f in curved)     # ยังเป็นผู้สมัครแดง (รออ่านซ้ำ)
    out = compare.collapse_curved(r["findings"])
    cards = [f for f in out if f["class"] == "CURVED"]
    assert len(cards) == 1 and len(cards[0]["members"]) == 3
    assert cards[0]["members"] == curved                   # ไม่มีจุดไหนถูกลบ/แก้
    assert "OMEGA-6" in cards[0]["a"]["text"] and "OMEGA-3" in cards[0]["b"]["text"]
    assert cards[0]["a"]["box"] and cards[0]["b"]["box"]


def test_real_difference_on_upright_text_is_not_swallowed_by_the_card():
    a = BODY + EMB_A
    b = [("Natural recipe with real chicken and vegetables", 50, 100, {}),
         ("Fat 15g per serving for adult dogs", 50, 140, {})] + EMB_B
    out = compare.collapse_curved(_cmp(a, b)["findings"])
    plain = [f for f in out if f["class"] != "CURVED"]
    assert len(plain) == 1 and plain[0]["class"] == "NUMBER" and plain[0]["severity"] == "red"
    assert not plain[0].get("curved")


def test_flag_off_is_old_behavior(monkeypatch):
    monkeypatch.setattr(config, "CURVED_GROUP_ENABLED", False)
    r = _cmp(BODY + EMB_A, BODY + EMB_B)
    assert not any(f.get("curved") for f in r["findings"])
    assert r["curved_lines"] == {"A": [], "B": []}


def test_real_avoderm_with_real_angles_keeps_every_true_difference():
    """บรรทัด OCR จริงพร้อมมุมจริง (ตรา FOR HEALTHY · OMEGA-6 · ACIDS เอียง 38-78°)
    ⇒ ของจริง 4 เรื่องต้องไม่ถูกจัดเป็น "ข้อความโค้ง" และยังจับได้ครบ"""
    R = load_real(DATA, with_angle=True)

    def P(s):
        W, H, ls = R[s]
        return textmodel.parse(fta_from_lines(ls, W, H), W, H)["lines"], (W, H)
    (a, sa), (b, sb) = P("A"), P("B")
    r = compare.compare(a, b, sa, sb)
    sigs = {(f["class"], f["a"]["frag"], f["b"]["frag"]) for f in r["findings"]}
    assert {("CASE", "c", "C"), ("TEXT", "", "s"), ("TEXT", "Park", ""),
            ("TEXT", "", "USA"), ("TEXT", "phate Pentahydr", "f")} <= sigs
    assert not any(f.get("curved") for f in r["findings"])
    curved_a = {r["lines_a"][i]["text"] for i in r["curved_lines"]["A"]}
    assert {"WITH", "OMEGA-6", "ACIDS"} <= curved_a
    assert r["coverage"] == 1.0


def test_real_avoderm_mutation_on_the_emblem_is_kept_as_curved_member():
    R = load_real(DATA, with_angle=True)
    W, H, lb = R["B"]
    lb = [(t.replace("OMEGA-6", "OMEGA-3"), bx, c, ang) for t, bx, c, ang in lb]
    a = textmodel.parse(fta_from_lines(R["A"][2], *R["A"][:2]), *R["A"][:2])["lines"]
    b = textmodel.parse(fta_from_lines(lb, W, H), W, H)["lines"]
    fs = [f for f in compare.compare(a, b)["findings"] if "OMEGA" in f["a"]["text"]]
    assert len(fs) == 1 and fs[0]["curved"] and fs[0]["class"] == "NUMBER"


# ── B: เศษอักขระ / ขอบโซน ──────────────────────────────────────────────

def test_low_confidence_symbol_line_goes_to_debris():
    r = _cmp(BODY, BODY + [("|", 500, 500, {"conf": 0.40})])
    assert r["findings"] == []
    assert len(r["debris"]) == 1 and r["debris"][0]["severity"] == "debris"
    assert r["coverage"] == 1.0


def test_symbol_line_at_zone_edge_goes_to_debris_only_when_size_known():
    extra = [("~", 990, 500, {"cw": 8})]                    # ชิดขอบขวาของภาพ 1000 px
    r = _cmp(BODY, BODY + extra)
    assert r["findings"] == [] and len(r["debris"]) == 1
    A = _lines(BODY)
    B = _lines(BODY + extra)
    r2 = compare.compare(A, B)                              # ไม่รู้ขนาด ⇒ ใช้เกณฑ์ความมั่นใจอย่างเดียว
    assert len(r2["findings"]) == 1 and r2["debris"] == []


def test_confident_symbol_inside_zone_is_still_a_finding():
    r = _cmp(BODY, BODY + [("®", 500, 500, {})])
    assert len(r["findings"]) == 1 and r["debris"] == []


@pytest.mark.parametrize("txt", ["8", "g", "A1", "%5"])
def test_lines_with_letters_or_digits_are_never_debris(txt):
    r = _cmp(BODY, BODY + [(txt, 990, 500, {"conf": 0.30, "cw": 4})])
    assert r["debris"] == [] and len(r["findings"]) == 1


def test_debris_flag_off_is_old_behavior(monkeypatch):
    monkeypatch.setattr(config, "DEBRIS_ENABLED", False)
    r = _cmp(BODY, BODY + [("|", 500, 500, {"conf": 0.40})])
    assert len(r["findings"]) == 1 and r["findings"][0]["severity"] == "yellow"
    assert r["debris"] == []


# ── ทั้งรอบ (pipeline) ─────────────────────────────────────────────────

def _pdf(lines, w=400, h=300):
    doc = fitz.open()
    pg = doc.new_page(width=w, height=h)
    for i, t in enumerate(lines):
        pg.insert_text((20, 30 + i * 18), t, fontsize=11)
    return doc.tobytes()


def _fake(spec_a, spec_b, crop_a=None, crop_b=None):
    """spec = [(text, xf, yf, kwargs)] พิกัดเป็นสัดส่วนของภาพ"""
    def fake(groups, poster=None, key=None):
        res, ids = {}, []
        for g in groups:
            for it in g:
                ids.append(it["id"])
                W, H = Image.open(io.BytesIO(it["jpeg"])).size
                side = it["id"][-1]
                crop = it["id"].startswith("rr")
                S = (crop_a if crop and crop_a is not None else spec_a) if side == "a" else \
                    (crop_b if crop and crop_b is not None else spec_b)
                lines = [(t, int(xf * W), int(yf * H), dict({"cw": max(4, W // 60),
                                                              "h": max(8, H // 25)}, **kw))
                         for t, xf, yf, kw in S]
                res[it["id"]] = {"ok": True, "error": "", "fta": fta(lines, W, H),
                                 "request_index": 0}
        return {"results": res, "calls": [{"index": 0, "phase": "main", "images": ids,
                                           "json_bytes": 10, "status": 200, "attempts": 1,
                                           "ms": 1, "error": "", "model_requested": config.MODEL,
                                           "endpoint": config.ENDPOINT, "at": "t"}]}
    return fake


FULL = [{"a": {"page": 0, "bbox": [0, 0, 1, 1]}, "b": {"page": 0, "bbox": [0, 0, 1, 1]}}]
PB = [("Natural recipe with real chicken and vegetables", 0.05, 0.10, {}),
      ("Fat 1.5g per serving for adult dogs", 0.05, 0.18, {})]
PA_E = [("OMEGA-6", 0.50, 0.50, {"angle": 38}), ("&", 0.62, 0.50, {}),
        ("FATTY ACIDS", 0.50, 0.62, {"angle": 330})]
PB_E = [("OMEGA-3", 0.50, 0.50, {"angle": 38}), ("8", 0.62, 0.50, {}),
        ("FATY ACIDS", 0.50, 0.62, {"angle": 330})]


def _job():
    return jobs.create(("a.pdf", _pdf(["a"])), ("b.pdf", _pdf(["b"])))["id"]


def test_pipeline_three_curved_points_become_one_yellow_card(monkeypatch):
    # อ่านซ้ำแล้ว "ไม่ต่าง" (ครอปอ่านได้เหมือนกัน) ⇒ ลดเป็นเหลืองทั้งหมด
    same = [("OMEGA-6 & FATTY ACIDS", 0.05, 0.4, {})]
    monkeypatch.setattr(vision_client, "annotate", _fake(PB + PA_E, PB + PB_E, same, same))
    keystore.save(KEY)
    r = pipeline.run(_job(), FULL)
    f = r["pairs"][0]["findings"]
    assert [x["class"] for x in f] == ["CURVED"] and f[0]["severity"] == "yellow"
    assert len(f[0]["members"]) == 3
    assert r["verdict"] == "REVIEW"
    assert "จุดที่ไม่มั่นใจ 1 จุด" in " ".join(r["reasons"])
    log = r["log_text"]
    assert "CURVED" in log and "curved_lines" in log and log.count(" curved") >= 3


def test_pipeline_curved_is_red_only_when_reread_confirms(monkeypatch):
    monkeypatch.setattr(vision_client, "annotate",
                        _fake(PB + PA_E, PB + PB_E,
                              [(t, 0.05, 0.1 + 0.2 * i, {}) for i, (t, *_r) in enumerate(PA_E)],
                              [(t, 0.05, 0.1 + 0.2 * i, {}) for i, (t, *_r) in enumerate(PB_E)]))
    keystore.save(KEY)
    r = pipeline.run(_job(), FULL)
    card = r["pairs"][0]["findings"][0]
    assert card["class"] == "CURVED" and card["severity"] == "red"
    assert r["verdict"] == "FAIL" and r["reread"]["confirmed"] >= 1


def test_pipeline_curved_without_reread_is_never_red(monkeypatch):
    monkeypatch.setattr(config, "REREAD_ENABLED", False)
    monkeypatch.setattr(vision_client, "annotate", _fake(PB + PA_E, PB + PB_E))
    r = pipeline.run(_job(), FULL)
    card = r["pairs"][0]["findings"][0]
    assert card["class"] == "CURVED" and card["severity"] == "yellow"
    assert all(m["severity"] == "yellow" for m in card["members"])
    assert r["verdict"] == "REVIEW"


def test_pipeline_curved_flag_off_is_three_separate_findings(monkeypatch):
    monkeypatch.setattr(config, "CURVED_GROUP_ENABLED", False)
    monkeypatch.setattr(config, "REREAD_ENABLED", False)
    monkeypatch.setattr(vision_client, "annotate", _fake(PB + PA_E, PB + PB_E))
    r = pipeline.run(_job(), FULL)
    assert len(r["pairs"][0]["findings"]) == 3 and r["verdict"] == "FAIL"


def test_pipeline_debris_is_listed_but_not_counted(monkeypatch):
    monkeypatch.setattr(vision_client, "annotate",
                        _fake(PB, PB + [("|", 0.5, 0.5, {"conf": 0.4}),
                                        ("~", 0.985, 0.7, {})]))
    r = pipeline.run(_job(), FULL)
    p = r["pairs"][0]
    assert p["findings"] == [] and len(p["debris"]) == 2
    assert r["verdict"] == "PASS"
    assert "debris" in r["log_text"] and all(d.get("id") for d in p["debris"])
