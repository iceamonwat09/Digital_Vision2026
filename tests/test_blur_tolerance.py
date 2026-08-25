"""
เทสต์ของ `blur_tolerance.py` — เครื่องมือวัดว่าโมเดลทนความเบลอได้กี่พิกเซล

**เครื่องมือวัดที่วัดผิด แย่กว่าไม่มีเครื่องมือ** (บทเรียนจาก `verify_ocr.py`
ที่เคยวัดเส้นทางเก่าแล้วรายงานผิดทาง) เทสต์ชุดนี้จึงเน้นพิสูจน์ว่า:
  1. เบลอที่ใส่เป็น "เบลอจากการเคลื่อนที่" จริง (แกนเดียว) ไม่ใช่ความนุ่มทั่วไป
  2. ภาพที่โมเดลหาไม่เจอตั้งแต่ไม่เบลอ **ต้องไม่ถูกนับว่า "ยังเจอ 100%"**
  3. การหาเพดานต้องไม่หลงจุดที่ผ่านแบบฟลุ๊คหลังจากพังไปแล้ว
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

import blur_tolerance as bt                            # noqa: E402


def _box(x0, y0, x1, y1, conf=0.9, cls="dent"):
    return {"class_name": cls, "confidence": conf, "bbox": [x0, y0, x1, y1]}


def _striped(h=120, w=200):
    """ลายคาบ 4 px — การเฉลี่ยตามแนวยาว 4 px จะกลืนหายพอดี (รู้คำตอบล่วงหน้า)"""
    img = np.full((h, w), 40, np.uint8)
    img[50:70, 90:110] = np.tile(np.array([220, 220, 40, 40], np.uint8), 5)
    return img


# ── ① เบลอต้องเป็น "การเคลื่อนที่" ไม่ใช่ "โฟกัสหลุด" ──────────────
def test_zero_blur_is_a_no_op():
    img = _striped()
    assert np.array_equal(bt.motion_blur(img, 0), img)
    assert np.array_equal(bt.motion_blur(img, 1), img)


def test_blur_only_smears_along_its_own_axis():
    img = np.zeros((120, 200), np.uint8)
    img[58:62, 98:102] = 255
    b = bt.motion_blur(img, 21)
    wide = len(np.where(b.max(axis=0) > 3)[0])
    tall = len(np.where(b.max(axis=1) > 3)[0])
    assert wide > 20, "ต้องเกลี่ยตามแนว x"
    assert tall <= 6, "ต้อง **ไม่** เกลี่ยตามแนว y (ไม่งั้นเป็นโฟกัสหลุด)"


def test_rotated_kernel_smears_the_other_way():
    img = np.zeros((120, 200), np.uint8)
    img[58:62, 98:102] = 255
    b = bt.motion_blur(img, 21, angle_deg=90)
    assert len(np.where(b.max(axis=1) > 3)[0]) > 20


def test_blur_conserves_total_brightness():
    img = _striped()
    for L in (3, 7, 15):
        b = bt.motion_blur(img, L)
        assert abs(float(b.sum()) - float(img.sum())) / float(img.sum()) < 0.06


def test_blur_erases_a_pattern_at_its_known_period():
    """ลายคาบ 4 px ต้องหายไปเมื่อเฉลี่ยยาว 4 px — ยืนยันว่าเบลอ "แรงจริง" ตามฟิสิกส์"""
    img = _striped()
    before = float(img[50:70, 90:110].std())
    after = float(bt.motion_blur(img, 4)[50:70, 90:110].std())
    assert after < before * 0.25


def test_rescale_changes_size_and_keeps_content():
    img = _striped()
    small = bt.rescale(img, 0.5)
    assert small.shape[0] == 60 and small.shape[1] == 100
    assert np.array_equal(bt.rescale(img, 1.0), img)


# ── ② การจับคู่กรอบ ────────────────────────────────────────────────
def test_iou_basics():
    assert bt._iou([0, 0, 10, 10], [0, 0, 10, 10]) == pytest.approx(1.0)
    assert bt._iou([0, 0, 10, 10], [50, 50, 60, 60]) == 0.0
    assert 0.0 < bt._iou([0, 0, 10, 10], [5, 0, 15, 10]) < 1.0


def test_match_rate_pairs_each_box_only_once():
    """สองกรอบจริงต้องไม่ถูกจับคู่กับกรอบเดียวกันจนดูเหมือนเจอครบ"""
    base = [_box(0, 0, 10, 10), _box(2, 2, 12, 12)]
    kept, _, _ = bt.match_rate(base, [_box(0, 0, 10, 10)])
    assert kept == pytest.approx(0.5)


def test_match_rate_returns_none_without_a_baseline():
    """ไม่มีของให้เทียบ = สรุปไม่ได้ ⇒ ต้องเป็น None ไม่ใช่ 1.0"""
    assert bt.match_rate([], [_box(0, 0, 10, 10)]) == (None, None, None)


def test_match_rate_rejects_a_box_that_moved_away():
    kept, _, _ = bt.match_rate([_box(0, 0, 10, 10)], [_box(80, 80, 90, 90)])
    assert kept == 0.0


def test_non_defect_classes_are_never_counted():
    dets = [_box(0, 0, 5, 5, cls="can"), _box(0, 0, 5, 5, cls="good"),
            _box(0, 0, 5, 5, cls="dent")]
    assert [d["class_name"] for d in bt.defects_of(dets)] == ["dent"]


# ── ③ การหาเพดาน ──────────────────────────────────────────────────
def test_knee_is_continuous_and_ignores_a_fluke_after_the_cliff():
    rows = [{"blur": 0, "kept": 1.0}, {"blur": 1, "kept": 1.0},
            {"blur": 2, "kept": 0.98}, {"blur": 3, "kept": 0.50},
            {"blur": 4, "kept": 1.00}]
    assert bt.knee(rows) == 2


def test_knee_is_none_when_the_baseline_itself_finds_nothing():
    assert bt.knee([{"blur": 0, "kept": 0.0}, {"blur": 1, "kept": 0.0}]) is None
    assert bt.knee([]) is None


def test_knee_respects_a_looser_threshold():
    rows = [{"blur": 0, "kept": 1.0}, {"blur": 1, "kept": 0.90},
            {"blur": 2, "kept": 0.60}]
    assert bt.knee(rows, keep_frac=0.95) == 0
    assert bt.knee(rows, keep_frac=0.85) == 1


def test_knee_skips_rows_that_could_not_be_measured():
    rows = [{"blur": 0, "kept": 1.0}, {"blur": 1, "kept": None},
            {"blur": 2, "kept": 0.99}]
    assert bt.knee(rows) == 2


# ── ④ เดินทั้งเส้นทาง ──────────────────────────────────────────────
def _fake_detector(threshold=30.0):
    def detect(im):
        g = im if im.ndim == 2 else cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
        h, w = g.shape[:2]
        y0, y1 = int(h * 50 / 120.0), int(h * 70 / 120.0)
        x0, x1 = int(w * 90 / 200.0), int(w * 110 / 200.0)
        if float(g[y0:y1, x0:x1].std()) < threshold:
            return []
        return [_box(x0, y0, x1, y1)]
    return detect


def test_sweep_finds_the_limit_of_a_detector_with_a_known_limit():
    res = bt.sweep(_fake_detector(), [_striped()], blurs=(0, 1, 2, 3, 4, 6, 10))
    limit = bt.knee(res[1.0]["rows"])
    assert limit is not None
    assert 1 <= limit <= 4, "ลายคาบ 4 px ต้องพังก่อนถึงเบลอ 4 px"


def test_sweep_reports_a_blind_detector_as_zero_not_as_perfect():
    res = bt.sweep(_fake_detector(), [_striped()], blurs=(0, 30))
    assert res[1.0]["rows"][-1]["kept"] == 0.0


def test_sweep_drops_images_the_model_never_saw_a_defect_in():
    """
    **กับดักที่อันตรายที่สุดของเครื่องมือนี้**: ภาพที่โมเดลหาไม่เจอตั้งแต่ไม่เบลอ
    ถ้านับรวมเข้าไปจะได้ "ยังเจอ 100%" ทุกระดับความเบลอ = เครื่องมือบอกว่า
    โมเดลทนเบลอได้ไม่จำกัด ทั้งที่ความจริงคือมันไม่เคยเห็นอะไรเลย
    """
    blank = np.full((120, 200), 40, np.uint8)          # ไม่มีลาย = ตรวจไม่เจอ
    res = bt.sweep(_fake_detector(), [_striped(), blank], blurs=(0, 2, 30))
    assert res[1.0]["images_total"] == 2
    assert res[1.0]["images_used"] == 1
    assert res[1.0]["rows"][-1]["kept"] == 0.0         # ไม่ถูกเจือจางให้ดูดีขึ้น


def test_sweep_measures_the_baseline_at_each_scale_separately():
    res = bt.sweep(_fake_detector(), [_striped()], blurs=(0, 2), scales=(1.0, 0.5))
    assert set(res.keys()) == {1.0, 0.5}
    for scale in (1.0, 0.5):
        assert res[scale]["rows"][0]["blur"] == 0


def test_sweep_progress_callback_is_called_for_every_cell():
    seen = []
    bt.sweep(_fake_detector(), [_striped()], blurs=(0, 2, 4),
             scales=(1.0, 0.5), progress=lambda s, L: seen.append((s, L)))
    assert len(seen) == 6


# ── ⑤ แปลงเป็นสเปกที่ซื้อของได้ ────────────────────────────────────
def test_exposure_advice_matches_the_physics():
    adv = bt.exposure_advice(3.0, 0.082, 638.0)
    assert adv["speed_px_s"] == pytest.approx(638.0 / 0.082)
    assert adv["max_exposure_us"] == pytest.approx(3.0 / (638.0 / 0.082) * 1e6)


def test_light_factor_is_consistent_with_the_exposure_it_recommends():
    adv = bt.exposure_advice(3.0, 0.082, 638.0, exposure_us=2003.0)
    assert adv["light_factor_needed"] == pytest.approx(
        2003.0 / adv["max_exposure_us"])


def test_usable_light_factor_accounts_for_an_already_dark_image():
    adv = bt.exposure_advice(3.0, 0.082, 638.0, exposure_us=2003.0,
                             mean_brightness=20.0)
    assert adv["light_factor_usable"] == pytest.approx(
        adv["light_factor_needed"] * bt.TARGET_MEAN / 20.0)


def test_no_advice_when_the_inputs_are_missing():
    assert bt.exposure_advice(3.0, None, 638.0) is None
    assert bt.exposure_advice(None, 0.082, 638.0) is None
    assert bt.exposure_advice(3.0, 0.082, None) is None


def test_pitch_black_image_gets_no_usable_factor():
    adv = bt.exposure_advice(3.0, 0.082, 638.0, exposure_us=2003.0,
                             mean_brightness=0.0)
    assert "light_factor_usable" not in adv


def test_target_mean_matches_the_burst_module():
    """ค่าเดียวกันสองที่ — ถ้าไม่ตรง เครื่องมือสองตัวจะแนะนำไฟคนละจำนวนเท่า"""
    import hik_burst
    assert bt.TARGET_MEAN == hik_burst.TARGET_MEAN


# ── ⑥ ตัวเครื่องมือเองต้องผ่านการตรวจตัวเอง ────────────────────────
def test_selftest_passes(capsys):
    assert bt.selftest() == 0
    out = capsys.readouterr().out
    assert "❌" not in out
