# -*- coding: utf-8 -*-
"""โหมดทดลอง "หั่นโซนเป็นแถบก่อนส่ง OCR".

ที่มา: Gemini หั่นภาพเป็นไทล์ ``clamp(min(W,H)/1.5, 256, 768)`` แล้วขยาย
ทุกไทล์เป็น 768x768 ⇒ ด้านสั้นของภาพเล็กลง = ตัวหนังสือใหญ่ขึ้นในสายตา
โมเดล. **การย่อภาพไม่ช่วย** (ตัวหนังสือเล็กลงตามสัดส่วนแล้วโมเดลขยายคืน
พอดี) — ต้อง "ตัดชิ้น" เท่านั้น. วัดบนโซนจริง: บรรทัด 71 -> 213 px.

เทสต์ชุดนี้ล็อก **กติกาความปลอดภัย** เป็นหลัก ไม่ใช่แค่ว่ามันหั่นได้:
ห้ามตัดผ่านตัวหนังสือ · หาจุดตัดไม่ได้ต้องไม่หั่น · แถบใดอ่านพลาด
ต้องทิ้งผลทั้งชุดแล้วถอยไปทางเดิม (ข้อความที่ขาดครึ่งอันตรายกว่าอ่านไม่ได้)
"""
import numpy as np
import pytest

from artwork_check import bands


# ── ตัวช่วยสร้างภาพทดสอบ ─────────────────────────────────────────────

def table_img(rows=12, row_h=70, gap=24, w=1200, top=20):
    """ภาพขาวที่มี "แถวข้อความ" (แถบดำ) คั่นด้วยช่องว่างขาว."""
    h = top * 2 + rows * row_h + (rows - 1) * gap
    img = np.full((h, w, 3), 255, np.uint8)
    y = top
    for _ in range(rows):
        # ตัวอักษรจำลอง: แท่งดำเว้นช่อง กินความกว้างช่วงกลาง
        for x in range(int(w * 0.12), int(w * 0.88), 26):
            img[y:y + row_h, x:x + 14] = 0
        y += row_h + gap
    return img


def ink_rows(img):
    """แถวที่มีหมึก (ใช้ยืนยันว่าเส้นตัดไม่ผ่านตัวหนังสือ)."""
    return set(np.where((img < 128).any(axis=(1, 2)))[0].tolist())


# ── หาจุดตัด ─────────────────────────────────────────────────────────

def test_splits_a_tall_table_into_bands():
    img = table_img()
    got = bands.find_bands(img)
    assert len(got) >= 2
    assert got[0][0] == 0 and got[-1][1] == img.shape[0]
    # ต่อกันสนิท ไม่มีรูโหว่ ไม่ทับกัน
    for (a0, a1), (b0, b1) in zip(got, got[1:]):
        assert a1 == b0


def test_never_cuts_through_text():
    """กติกาสำคัญที่สุด — เส้นตัดต้องอยู่บนแถวที่ไม่มีหมึกเลย."""
    img = table_img()
    ink = ink_rows(img)
    for y0, _ in bands.find_bands(img)[1:]:
        assert y0 not in ink, "ตัดผ่านตัวหนังสือที่ y=%d" % y0


def test_short_image_is_not_split():
    assert bands.find_bands(table_img(rows=3, row_h=60, gap=20)) == []


def test_solid_block_with_no_gaps_is_not_split():
    """ภาพทึบไม่มีช่องว่าง = หาจุดตัดปลอดภัยไม่ได้ ⇒ ห้ามหั่นมั่ว."""
    img = np.zeros((1400, 1200, 3), np.uint8)
    assert bands.find_bands(img) == []


def test_blank_image_is_not_split():
    assert bands.find_bands(np.full((1400, 1200, 3), 255, np.uint8)) == []


def test_empty_input_is_safe():
    assert bands.find_bands(None) == []
    assert bands.find_bands(np.zeros((0, 0, 3), np.uint8)) == []


@pytest.mark.parametrize("gap", [2, 3, 4, 5])
def test_hairline_gaps_are_not_cut_points(gap):
    """บรรทัดที่ชิดกันมากเหลือช่องว่างไม่กี่พิกเซล ซึ่งอาจเป็นแค่ noise ของ
    JPEG — ตัดตรงนั้นเสี่ยงเฉือนหางตัวอักษร ⇒ ต้องยอมไม่หั่น ดีกว่าหั่นเสี่ยง.
    (ช่องว่างจริงที่ปลอดภัยเริ่มที่ ~6 px ดูเทสต์ถัดไป)"""
    img = table_img(rows=14, row_h=90, gap=gap)
    assert img.shape[0] > 2 * bands.BAND_MIN_PX       # สูงพอที่จะหั่นได้ถ้ายอม
    assert bands.find_bands(img) == []


def test_a_real_gap_is_a_cut_point():
    """คู่กับเทสต์บน — ยืนยันว่าไม่ได้ปฏิเสธทุกอย่างจนฟีเจอร์ไม่ทำงาน."""
    assert len(bands.find_bands(table_img(rows=14, row_h=90, gap=8))) >= 2


def test_band_count_is_capped():
    img = table_img(rows=60)
    assert len(bands.find_bands(img)) <= bands.MAX_BANDS


def test_no_band_is_shorter_than_the_minimum():
    for rows in (8, 12, 20, 40):
        for y0, y1 in bands.find_bands(table_img(rows=rows)):
            assert y1 - y0 >= bands.BAND_MIN_PX


def test_bands_are_short_enough_to_earn_magnification():
    """เหตุผลทั้งหมดของฟีเจอร์นี้ — แถบต้องเตี้ยพอที่ Gemini จะขยายให้."""
    from artwork_check import zones as Z
    img = table_img(rows=16)
    got = bands.find_bands(img)
    assert got, "ควรหั่นได้"
    base_mag = Z.gemini_tiling(img.shape[1], img.shape[0])[1]
    for y0, y1 in got:
        mag = Z.gemini_tiling(img.shape[1], y1 - y0)[1]
        assert mag > base_mag


def test_vertical_frame_lines_do_not_hide_the_gaps():
    """ขอบกรอบแนวตั้งทำให้ทุกแถวดู "ไม่เงียบ" — ต้องตัดคอลัมน์ริมทิ้งก่อนวัด."""
    img = table_img()
    img[:, :6] = 0                       # เส้นกรอบซ้าย
    img[:, -6:] = 0                      # เส้นกรอบขวา
    assert len(bands.find_bands(img)) >= 2


# ── ชั้นอ่านจริง: กติกา "แถบใดพลาด = ทิ้งทั้งชุด" ────────────────────

class _Backend:
    """OCR ปลอม — นับจำนวนครั้งที่ถูกเรียก และสั่งให้พลาดที่แถบไหนก็ได้."""

    def __init__(self, fail_on=None, texts=None):
        self.calls = 0
        self.fail_on = fail_on
        self.texts = texts

    def __call__(self, _jpg):
        self.calls += 1
        if self.fail_on == self.calls:
            return {"text": "", "blocks": [], "error": "พังตามสั่ง"}
        t = (self.texts[self.calls - 1] if self.texts
             else "band%d" % self.calls)
        return {"text": t, "blocks": [{"text": t, "bbox": [0, 0, 1, 1],
                                       "conf": 0.9}], "engine": "n8n"}


@pytest.fixture
def patched(monkeypatch):
    from artwork_check import ocr as ocr_mod

    def _use(be):
        monkeypatch.setattr(ocr_mod.vertex_client, "ocr_image", be)
        monkeypatch.setattr(ocr_mod, "encode_jpg", lambda img: b"x")
        return ocr_mod
    return _use


def test_bands_are_read_one_by_one_and_joined(patched):
    ocr_mod = patched(_Backend(texts=["AAA", "BBB", "CCC", "DDD", "EEE"]))
    be = ocr_mod.vertex_client.ocr_image
    out, note = ocr_mod._ocr_in_bands(table_img(), "z1")
    assert out is not None
    assert be.calls >= 2
    assert out["text"].splitlines() == ["AAA", "BBB", "CCC", "DDD"][:be.calls]
    assert "หั่น" in note


def test_one_failed_band_discards_the_whole_read(patched):
    """ข้อความที่ขาดไปบางแถบ = defect ปลอมเป็นพรวนโดยไม่มีสัญญาณ
    ⇒ ต้องคืน None ให้ผู้เรียกถอยไปอ่านทั้งโซน ไม่ใช่คืนข้อความที่ขาด."""
    ocr_mod = patched(_Backend(fail_on=2))
    out, note = ocr_mod._ocr_in_bands(table_img(), "z1")
    assert out is None
    assert "แถบ 2" in note and "ทั้งโซน" in note


def test_unsplittable_image_falls_back_without_calling_the_backend(patched):
    ocr_mod = patched(_Backend())
    be = ocr_mod.vertex_client.ocr_image
    out, note = ocr_mod._ocr_in_bands(np.zeros((1400, 1200, 3), np.uint8), "z1")
    assert out is None and note == "" and be.calls == 0


def test_blocks_are_dropped_so_no_red_box_lands_in_the_wrong_place(patched):
    """bbox ของแต่ละแถบอ้างพิกัดคนละระบบ — รวมกันแล้วกรอบแดงจะผิดที่
    (กฎเหล็กข้อ 2: ไม่มีกรอบ ดีกว่ากรอบผิดตำแหน่ง)."""
    ocr_mod = patched(_Backend())
    out, note = ocr_mod._ocr_in_bands(table_img(), "z1")
    assert out["blocks"] == []
    assert "กรอบแดง" in note


def test_confidence_is_averaged_across_bands(patched):
    ocr_mod = patched(_Backend())
    out, _ = ocr_mod._ocr_in_bands(table_img(), "z1")
    assert out["conf"] == pytest.approx(0.9, abs=0.01)


# ── สวิตช์ต้อง opt-in จริง ───────────────────────────────────────────

def test_read_zone_signature_defaults_to_the_old_path():
    import inspect
    from artwork_check import ocr as ocr_mod
    for fn in (ocr_mod.read_zone, ocr_mod.read_all_zones):
        assert inspect.signature(fn).parameters["split_bands"].default is False


def test_split_bands_is_part_of_the_ocr_cache_key():
    """ไม่ใส่ = เปิด/ปิดโหมดแล้วแท็บแปลเสิร์ฟข้อความเก่าตลอดไปแบบเงียบ."""
    from artwork_check import pipeline
    z = [{"id": "z1", "type": "panel", "group": "A",
          "bbox": [0.1, 0.1, 0.3, 0.3]}]
    assert (pipeline._zones_signature(z, False, False, False)
            != pipeline._zones_signature(z, False, False, True))


def test_band_tuning_is_part_of_the_ocr_fingerprint():
    from artwork_check import pipeline
    fp = pipeline._ocr_fingerprint()
    assert fp["band_target"] == bands.BAND_TARGET_PX
    assert fp["band_min"] == bands.BAND_MIN_PX
