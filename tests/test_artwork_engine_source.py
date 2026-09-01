"""
"ข้อความของโซนนี้มาจากไหน" — text layer ของ PDF หรือ OCR — และผลของการปนกัน.

สามอย่างที่ล็อกไว้ในไฟล์นี้:

  A. ``force_ocr``     ผู้ใช้สั่งข้ามชั้น text layer ทั้งใบ (ช่องติ๊กบนหน้าเว็บ)
                       ⇒ ต้องข้ามจริง **แต่ต้องไม่ทำให้แย่ลง** เมื่อไม่มี OCR
  B. ``engine_mix``    รายงานว่ากลุ่มไหนเอาข้อความจากสอง engine มาเทียบกัน
                       (advisory ล้วน — ไม่แตะ defects/verdict)
  C. ``OCR_GROUP_ENGINE_CONSISTENCY``  ตัวเลือกที่อ่านโซน text layer ซ้ำด้วย
                       OCR เมื่อกลุ่มนั้น engine ปนกัน (default ปิด)

กติกาที่เป็นหัวใจของทั้งสามข้อ: **ห้ามทิ้งข้อความที่อ่านได้อยู่แล้วไปแลกกับ
"อ่านไม่ได้"** — ความสม่ำเสมอไม่ใช่เหตุผลที่ดีพอจะทำให้ข้อมูลแย่ลง.
"""

import numpy as np
import pytest

from artwork_check import checks, config, ocr, pipeline


class FakeDoc(object):
    """เอกสารปลอม: มี text layer ตามที่กำหนด และเรนเดอร์ภาพขาวให้ OCR."""

    def __init__(self, embedded="", is_pdf=True):
        self.embedded = embedded
        self.is_pdf = is_pdf
        self.text_calls = 0
        self.render_calls = 0

    def embedded_text(self, bbox=None):
        self.text_calls += 1
        return self.embedded

    def render_zone(self, bbox, dpi, max_side=None):
        self.render_calls += 1
        return np.full((900, 1400, 3), 255, np.uint8)


ZONE = {"id": "z1", "type": "panel", "group": "A",
        "bbox": [0.1, 0.1, 0.2, 0.3], "rotate": 0}
CLEAN_TEXT = ("DOPLŇKOVÉ KRMIVO PRO DOSPĚLÉ KOČKY Pacifický tuňák v želé. "
              "SLOŽENÍ: pacifický tuňák (47,8 %), rýže (1,0 %)")


@pytest.fixture
def ocr_on(monkeypatch):
    monkeypatch.setattr(ocr.vertex_client, "is_enabled", lambda: True)
    monkeypatch.setattr(ocr.vertex_client, "ocr_image",
                        lambda b: {"text": "FROM OCR", "blocks": [],
                                   "engine": "mock"})


@pytest.fixture
def ocr_off(monkeypatch):
    monkeypatch.setattr(ocr.vertex_client, "is_enabled", lambda: False)


# ── A. force_ocr ─────────────────────────────────────────────────────

def test_force_ocr_skips_a_perfectly_good_text_layer(ocr_on):
    doc = FakeDoc(embedded=CLEAN_TEXT)
    r = ocr.read_zone(doc, ZONE, force_ocr=True)
    assert r["engine"] == "mock" and r["text"] == "FROM OCR"
    assert doc.render_calls == 1, "ต้องไปอ่านจากภาพจริง"
    assert doc.text_calls == 0, "ไม่ต้องแตะ text layer เลย"


def test_force_ocr_says_so_in_the_result(ocr_on):
    """ผู้ตรวจต้องรู้ว่าข้อความที่เห็นมาจาก OCR ไม่ใช่จากไฟล์."""
    r = ocr.read_zone(FakeDoc(embedded=CLEAN_TEXT), ZONE, force_ocr=True)
    assert r.get("forced_ocr") is True
    assert "OCR" in r.get("note", "")


def test_force_ocr_off_is_the_old_path_exactly(ocr_on):
    doc = FakeDoc(embedded=CLEAN_TEXT)
    r = ocr.read_zone(doc, ZONE)
    assert r["engine"] == "pdf-text" and r["conf"] == 1.0
    assert doc.render_calls == 0
    assert "forced_ocr" not in r


def test_force_ocr_without_backend_keeps_the_text_layer(ocr_off):
    """⚠️ กติกาสำคัญ: สั่งบังคับแล้วบังคับไม่ได้ ต้องไม่ทำให้โซนที่อ่านได้
    อยู่แล้วกลายเป็น UNREADABLE — ของดีที่มีอยู่ต้องไม่ถูกทิ้ง."""
    r = ocr.read_zone(FakeDoc(embedded=CLEAN_TEXT), ZONE, force_ocr=True)
    assert r["engine"] == "pdf-text" and r["text"] == CLEAN_TEXT
    assert "ไม่มี OCR backend" in r.get("note", ""), "ต้องบอกว่าบังคับไม่สำเร็จ"
    assert not r.get("error"), "ไม่ใช่ความล้มเหลวของโซนนี้"


def test_force_ocr_reaches_read_all_zones(ocr_on):
    doc = FakeDoc(embedded=CLEAN_TEXT)
    out = ocr.read_all_zones(doc, [ZONE], force_ocr=True)
    assert out[0]["engine"] == "mock"


def test_force_ocr_is_part_of_the_translate_cache_key():
    """แท็บแปลมี cache ของตัวเอง — ถ้า flag ไม่อยู่ใน key ผู้ใช้จะติ๊กแล้ว
    ได้ข้อความชุดเดิมตลอดไปแบบเงียบ ๆ."""
    a = pipeline._zones_signature([ZONE], False, force_ocr=False)
    b = pipeline._zones_signature([ZONE], False, force_ocr=True)
    assert a != b


@pytest.mark.parametrize("key", ["bad_glyph", "bad_glyph_min", "group_engine"])
def test_new_settings_are_in_the_ocr_fingerprint(key):
    """checklist ของโปรเจกต์: ค่าตั้งที่เปลี่ยน "ผลการอ่าน" ต้องอยู่ใน
    fingerprint ไม่งั้นแก้ค่าแล้ว cache ไม่หลุด."""
    assert key in pipeline._ocr_fingerprint()


# ── B. engine_mix (advisory) ─────────────────────────────────────────

def _z(zid, group, ztype="panel"):
    return {"id": zid, "type": ztype, "group": group,
            "bbox": [0.1, 0.1, 0.2, 0.2]}


def _r(zid, engine, text="ข้อความ"):
    return {"zone_id": zid, "engine": engine, "text": text}


def test_mixed_group_is_reported():
    zones = [_z("z1", "A"), _z("z2", "A")]
    res = [_r("z1", "pdf-text"), _r("z2", "n8n")]
    assert checks.engine_mix_groups(zones, res) == ["A"]


def test_same_engine_group_is_not_reported():
    zones = [_z("z1", "A"), _z("z2", "A")]
    assert checks.engine_mix_groups(zones, [_r("z1", "n8n"),
                                            _r("z2", "n8n")]) == []
    assert checks.engine_mix_groups(zones, [_r("z1", "pdf-text"),
                                            _r("z2", "pdf-text")]) == []


def test_unreadable_ocr_zone_is_not_a_mix():
    """โซนที่ OCR อ่านไม่ออกไม่ได้เข้าไปในการเทียบอยู่แล้ว ⇒ ถ้ารายงานว่า
    "ปน" จะเป็นคำเตือนที่ไม่มีมูล."""
    zones = [_z("z1", "A"), _z("z2", "A")]
    res = [_r("z1", "pdf-text"), _r("z2", "none", "")]
    assert checks.engine_mix_groups(zones, res) == []


def test_zones_without_a_group_are_never_a_mix():
    """ไม่มีกลุ่ม = ไม่มีการเทียบ = ไม่มีปัญหาเรื่อง engine ปนกัน."""
    zones = [_z("z1", ""), _z("z2", "")]
    assert checks.engine_mix_groups(zones, [_r("z1", "pdf-text"),
                                            _r("z2", "n8n")]) == []


def test_ignore_zones_are_excluded():
    zones = [_z("z1", "A"), _z("z2", "A", "ignore")]
    assert checks.engine_mix_groups(zones, [_r("z1", "pdf-text"),
                                            _r("z2", "n8n")]) == []


def test_cross_file_group_counts_too():
    """เทียบฉบับเก่า/ใหม่ก็คือการเทียบข้ามแผง — ปน engine ได้เหมือนกัน."""
    za = dict(_z("z1", "A"), doc="a")
    zb = dict(_z("b1", "A"), doc="b")
    res = [_r("z1", "pdf-text"), _r("b1", "n8n")]
    assert checks.engine_mix_groups([za, zb], res) == ["A"]


def test_coverage_carries_the_engine_mix():
    zones = [_z("z1", "A"), _z("z2", "A")]
    cov = checks.check_coverage(zones, [_r("z1", "pdf-text"), _r("z2", "n8n")])
    assert cov["engine_mix"] == {"groups": ["A"], "mixed": True}
    clean = checks.check_coverage(zones, [_r("z1", "n8n"), _r("z2", "n8n")])
    assert clean["engine_mix"]["mixed"] is False


def test_engine_mix_never_creates_defects():
    """advisory ล้วน — ต้องไม่โผล่เป็น defect หรือเปลี่ยน verdict."""
    zones = [_z("z1", "A"), _z("z2", "A")]
    res = [{"zone_id": "z1", "engine": "pdf-text", "text": "TUNA 170 g"},
           {"zone_id": "z2", "engine": "n8n", "text": "TUNA 170 g"}]
    defects = checks.run_all_checks(zones, res)
    assert [d for d in defects if "engine" in d.get("message", "")] == []


# ── C. อ่านซ้ำให้ engine ตรงกันทั้งกลุ่ม (opt-in) ────────────────────

@pytest.fixture
def unify_on(monkeypatch):
    monkeypatch.setattr(config, "OCR_GROUP_ENGINE_CONSISTENCY", True)
    monkeypatch.setattr(pipeline.ocr, "is_ocr_available", lambda: True)


def _mixed_case():
    zones = [_z("z1", "A"), _z("z2", "A")]
    results = [_r("z1", "pdf-text", "ข้อความจาก PDF"),
               _r("z2", "n8n", "ข้อความจาก OCR")]
    return zones, results


def test_reread_makes_the_group_single_engine(unify_on, monkeypatch):
    monkeypatch.setattr(pipeline.ocr, "read_zone",
                        lambda doc, z, page_auto=False, force_ocr=False:
                        _r(z["id"], "n8n", "อ่านใหม่ด้วย OCR"))
    zones, results = _mixed_case()
    out = pipeline._unify_group_engines({"a": FakeDoc()}, zones, results, False)
    assert [r["engine"] for r in out] == ["n8n", "n8n"]
    assert checks.engine_mix_groups(zones, out) == []


def test_reread_is_off_by_default(monkeypatch):
    """default = พฤติกรรมเดิมเป๊ะ (กฎเหล็กข้อ 1)."""
    assert config.OCR_GROUP_ENGINE_CONSISTENCY is False
    called = []
    monkeypatch.setattr(pipeline.ocr, "read_zone",
                        lambda *a, **k: called.append(1))
    zones, results = _mixed_case()
    # เส้นทางจริงเช็ก flag ก่อนเรียก _unify_group_engines
    assert not config.OCR_GROUP_ENGINE_CONSISTENCY
    assert called == []
    assert [r["engine"] for r in results] == ["pdf-text", "n8n"]


def test_failed_reread_keeps_the_text_layer(unify_on, monkeypatch):
    """⚠️ หัวใจของข้อนี้: อ่านซ้ำแล้วพัง ต้องคง text layer เดิมไว้
    ไม่ใช่ทิ้งข้อความที่เป๊ะ 100% ไปแลกกับ "อ่านไม่ได้"."""
    monkeypatch.setattr(pipeline.ocr, "read_zone",
                        lambda doc, z, page_auto=False, force_ocr=False:
                        {"zone_id": z["id"], "engine": "none", "text": "",
                         "error": "N8N ต่อไม่ติด"})
    zones, results = _mixed_case()
    out = pipeline._unify_group_engines({"a": FakeDoc()}, zones, results, False)
    kept = [r for r in out if r["zone_id"] == "z1"][0]
    assert kept["engine"] == "pdf-text" and kept["text"] == "ข้อความจาก PDF"
    assert "ไม่สำเร็จ" in kept.get("note", "")


def test_empty_reread_also_keeps_the_text_layer(unify_on, monkeypatch):
    """อ่านซ้ำได้ "ว่าง" โดยไม่มี error ก็ยังแย่กว่าเดิม."""
    monkeypatch.setattr(pipeline.ocr, "read_zone",
                        lambda doc, z, page_auto=False, force_ocr=False:
                        _r(z["id"], "n8n", "   "))
    zones, results = _mixed_case()
    out = pipeline._unify_group_engines({"a": FakeDoc()}, zones, results, False)
    assert [r for r in out if r["zone_id"] == "z1"][0]["engine"] == "pdf-text"


def test_no_ocr_backend_means_no_reread(monkeypatch):
    monkeypatch.setattr(config, "OCR_GROUP_ENGINE_CONSISTENCY", True)
    monkeypatch.setattr(pipeline.ocr, "is_ocr_available", lambda: False)
    called = []
    monkeypatch.setattr(pipeline.ocr, "read_zone",
                        lambda *a, **k: called.append(1))
    zones, results = _mixed_case()
    out = pipeline._unify_group_engines({"a": FakeDoc()}, zones, results, False)
    assert called == [] and out == results


def test_unmixed_group_is_left_alone(unify_on, monkeypatch):
    """ไม่ปน = ไม่ต้องอ่านซ้ำ (ไม่เผาโควตา OCR ฟรี ๆ)."""
    called = []
    monkeypatch.setattr(pipeline.ocr, "read_zone",
                        lambda *a, **k: called.append(1))
    zones = [_z("z1", "A"), _z("z2", "A")]
    results = [_r("z1", "pdf-text"), _r("z2", "pdf-text")]
    out = pipeline._unify_group_engines({"a": FakeDoc()}, zones, results, False)
    assert called == [] and out == results


def test_only_the_mixed_group_is_reread(unify_on, monkeypatch):
    """กลุ่ม B ที่ engine ตรงกันอยู่แล้วต้องไม่ถูกแตะ."""
    seen = []

    def fake(doc, z, page_auto=False, force_ocr=False):
        seen.append(z["id"])
        return _r(z["id"], "n8n", "อ่านใหม่")

    monkeypatch.setattr(pipeline.ocr, "read_zone", fake)
    zones = [_z("z1", "A"), _z("z2", "A"), _z("z3", "B"), _z("z4", "B")]
    results = [_r("z1", "pdf-text"), _r("z2", "n8n"),
               _r("z3", "pdf-text"), _r("z4", "pdf-text")]
    out = pipeline._unify_group_engines({"a": FakeDoc()}, zones, results, False)
    assert seen == ["z1"], "อ่านซ้ำเฉพาะโซน text layer ของกลุ่มที่ปนเท่านั้น"
    assert [r["engine"] for r in out] == ["n8n", "n8n", "pdf-text", "pdf-text"]


def test_reference_file_zone_is_reread_against_its_own_document(unify_on,
                                                                monkeypatch):
    """โซนของไฟล์อ้างอิงต้องถูกอ่านจาก source_b ไม่ใช่ไฟล์หลัก."""
    used = []

    def fake(doc, z, page_auto=False, force_ocr=False):
        used.append(doc.tag)
        return _r(z["id"], "n8n", "อ่านใหม่")

    monkeypatch.setattr(pipeline.ocr, "read_zone", fake)
    da, db = FakeDoc(), FakeDoc()
    da.tag, db.tag = "a", "b"
    zones = [dict(_z("z1", "A"), doc="a"), dict(_z("b1", "A"), doc="b")]
    results = [_r("z1", "n8n"), _r("b1", "pdf-text")]
    pipeline._unify_group_engines({"a": da, "b": db}, zones, results, False)
    assert used == ["b"]
