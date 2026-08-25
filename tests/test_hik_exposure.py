# -*- coding: utf-8 -*-
"""
โหมด "ไล่ exposure" — ตอบว่าไม่มีไฟเพิ่มจะกด exposure ลงได้ต่ำสุดแค่ไหน

โจทย์: ที่ 450 ใบ/นาที ความเบลอ = ความเร็ว × exposure ⇒ ต้องกด exposure ลง
⇒ ภาพมืดลง ⇒ ไม่มีไฟก็ต้องดัน gain ⇒ **แลกด้วยสัญญาณรบกวน** ⇒ จุดที่โมเดล
เชื่อไม่ได้อยู่ตรงไหน เดาไม่ได้ ต้องวัด — และต้องวัด **สองทิศทาง**
(ยังเจอรอยบุบจริงไหม + เจอรอยบุบที่ไม่มีไหม)
"""
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")
import hik_exposure as hx                                          # noqa: E402


# ───────────────────────────────────────────────────── กล้องปลอมเชิงฟิสิกส์
class FakeCam(object):
    """
    เซนเซอร์เชิงเส้น: ``สว่าง = k × exposure × 10^(gain/20)`` + สัญญาณรบกวนที่
    โตตาม gain — พฤติกรรมเดียวกับที่วัดได้จริงบนสถานี (101.9/255 ที่ 2165 µs, 7.7 dB)
    """

    def __init__(self, gain_max=23.98, exposure=2165.0, gain=7.7, seed=0):
        self.exposure, self.gain = exposure, gain
        self.gain_max = gain_max
        self.exposure_auto, self.gain_auto = "Off", "Off"
        self.k = 101.9 / (2165.0 * 10 ** (7.7 / 20.0))
        self.calls = []
        self.rng = np.random.RandomState(seed)
        self.shift = 0                       # >0 = ฉากขยับ (ทดสอบด่าน "ไม่นิ่ง")
        self.n_frames = 0
        self.fail_exposure_below = 0.0

    # -- API ที่ hik_exposure ใช้ --------------------------------------
    def get_params(self):
        return {
            "exposure_us": {"supported": True, "value": self.exposure,
                            "min": 15.0, "max": 9959540.0},
            "gain_db": {"supported": True, "value": self.gain,
                        "min": 0.0, "max": self.gain_max},
            "exposure_auto": {"supported": True, "symbolic": self.exposure_auto},
            "gain_auto": {"supported": True, "symbolic": self.gain_auto},
            "width": {"supported": True, "value": 2448},
            "height": {"supported": True, "value": 1024},
        }

    def set_params(self, params):
        self.calls.append(dict(params or {}))
        res = {"applied": {}, "failed": {}, "restarted": False}
        for k, v in (params or {}).items():
            if k == "exposure_us":
                if self.fail_exposure_below and v < self.fail_exposure_below:
                    res["failed"][k] = {"value": None, "message": "นอกช่วงที่กล้องรับ"}
                    continue
                self.exposure = float(v)
            elif k == "gain_db":
                self.gain = float(v)
            elif k == "exposure_auto":
                self.exposure_auto = v
            elif k == "gain_auto":
                self.gain_auto = v
            res["applied"][k] = {"value": v, "message": None}
        return res

    def mean_now(self):
        return min(255.0, self.k * self.exposure * 10 ** (self.gain / 20.0))

    def snap_full(self, timeout=3.0):
        self.n_frames += 1
        mean = self.mean_now()
        # สัญญาณรบกวนโตตาม gain — นี่คือราคาที่จ่ายเมื่อไม่มีไฟ
        sigma = 1.2 * 10 ** (self.gain / 20.0)
        # ขนาดเฟรมต้องใกล้ของจริงพอที่ชั้นวิเคราะห์ (ย่อ 1/4 แล้วเบลอ 9) จะทำงาน
        # เหมือนบนภาพ 2448x1024 จริง — เฟรมจิ๋วจะถูกเบลอจนโครงสร้างหายหมด
        img = self.rng.normal(mean, sigma, (360, 480)).clip(0, 255)
        img[60:300, 30:270] = np.clip(img[60:300, 30:270] + 25, 0, 255)   # "ตัวกระป๋อง"
        img[150:180, 150:180] = np.clip(img[150:180, 150:180] + 40, 0, 255)  # "รอยบุบ"
        if self.shift:                       # ทั้งวัตถุเลื่อน ไม่ใช่แค่ noise เลื่อน
            img = np.roll(img, self.shift * self.n_frames, axis=1)
        return np.repeat(img.astype(np.uint8)[:, :, None], 3, axis=2)


def detector_needs_snr(min_snr=18.0):
    """โมเดลปลอม: เจอรอยบุบเมื่อภาพสะอาดพอ — จำลอง "noise กลืนสัญญาณ"."""
    def _fn(frame):
        g = frame[:, :, 0].astype(np.float32)
        patch = g[150:180, 150:180]
        bg = np.concatenate([g[:40].ravel(), g[320:].ravel()])
        snr = (patch.mean() - bg.mean()) / max(bg.std(), 1e-6)
        return [{"confidence": 0.8, "class_name": "dent"}] if snr * 10 >= min_snr else []
    return _fn


class FakeJob(object):
    def __init__(self):
        self.total = self.done = 0
        self.cancelled = False


# ───────────────────────────────────────────────────────── การชดเชย gain
def test_gain_for_uses_the_standard_20log10_rule():
    # สว่าง 40 → อยากได้ 80 = 2 เท่า = +6.02 dB
    assert hx.gain_for(40.0, 7.7, target=80.0) == pytest.approx(7.7 + 6.0206, abs=1e-3)


def test_gain_for_refuses_to_guess_on_a_black_frame():
    """ภาพมืดสนิทหาร ไม่ได้ ⇒ **ไม่เดา** (คืน None) แทนที่จะให้ค่ามหาศาล"""
    assert hx.gain_for(0.0, 5.0) is None
    assert hx.gain_for(None, 5.0) is None


def test_station_numbers_reproduce_the_expected_gain():
    """ตัวเลขจากสถานี: 101.9/255 ที่ 2165 µs ⇒ ลงมา 513 µs ต้องใช้ ~18 dB"""
    mean_at_513 = 101.9 * 513.0 / 2165.0
    assert hx.gain_for(mean_at_513, 7.7) == pytest.approx(18.1, abs=0.2)


# ─────────────────────────────────────────────────── การวัดสัญญาณรบกวน
def test_noise_is_measured_across_frames_not_within_one():
    cam = FakeCam(gain=0.0)
    shots = [cam.snap_full() for _ in range(6)]
    st = hx.frame_stats(shots)
    assert st["moved"] is False
    assert st["noise"] == pytest.approx(1.2, abs=0.4)     # sigma ที่ใส่เข้าไป


def test_higher_gain_measurably_raises_noise():
    quiet = hx.frame_stats([FakeCam(gain=0.0, seed=1).snap_full() for _ in range(6)])
    loud = FakeCam(gain=18.0, seed=1)
    st = hx.frame_stats([loud.snap_full() for _ in range(6)])
    assert st["noise"] > quiet["noise"] * 4


def test_a_moving_scene_reports_no_noise_number_instead_of_a_wrong_one():
    """
    ฉากขยับ ⇒ σ ข้ามเฟรมไม่ใช่สัญญาณรบกวนอีกต่อไป ⇒ **ไม่รายงานตัวเลข**
    (กฎเหล็กข้อ 2: วัดไม่ได้ ต่างจาก วัดได้ค่าน้อย)
    """
    cam = FakeCam(gain=0.0)
    cam.shift = 15
    st = hx.frame_stats([cam.snap_full() for _ in range(6)])
    assert st["moved"] is True
    assert st["noise"] is None


def test_two_frames_are_not_enough_to_call_it_noise():
    cam = FakeCam(gain=0.0)
    st = hx.frame_stats([cam.snap_full() for _ in range(2)])
    assert st["noise"] is None


# ──────────────────────────────────────────────────────────── ตัวไล่ค่า
def test_ladder_restores_every_camera_setting_afterwards():
    cam = FakeCam(exposure=2165.0, gain=7.7)
    hx.run_ladder(cam, detector_needs_snr(), [2165, 1000, 514], frames=3)
    assert cam.exposure == pytest.approx(2165.0)
    assert cam.gain == pytest.approx(7.7)
    assert cam.exposure_auto == "Off" and cam.gain_auto == "Off"


def test_ladder_restores_settings_even_when_the_model_explodes():
    """ค่ากล้องค้างที่ขั้นสุดท้าย = ผลตรวจสดหลังจากนั้นผิดโดยผู้ใช้ไม่รู้ตัว"""
    cam = FakeCam()

    def boom(frame):
        raise RuntimeError("โมเดลพัง")

    with pytest.raises(RuntimeError):
        hx.run_ladder(cam, boom, [2165, 1000], frames=3)
    assert cam.exposure == pytest.approx(2165.0)
    assert cam.gain == pytest.approx(7.7)


def test_ladder_walks_from_bright_to_dark():
    cam = FakeCam()
    rows, _ = hx.run_ladder(cam, detector_needs_snr(), [514, 2165, 1000], frames=3)
    assert [r["exposure_us"] for r in rows] == [2165.0, 1000.0, 514.0]


def test_gain_is_raised_to_keep_brightness_near_target():
    cam = FakeCam()
    rows, _ = hx.run_ladder(cam, detector_needs_snr(), [2165, 514], frames=3,
                            target_mean=80.0)
    dark = [r for r in rows if r["exposure_us"] == 514.0][0]
    assert dark["gain_db"] > 15.0                       # ต้องดัน gain ขึ้นจริง
    assert dark["mean"] == pytest.approx(80.0, rel=0.2)


def test_hitting_the_gain_ceiling_is_reported_not_hidden():
    """gain ชนเพดาน = ภาพมืดกว่าเป้า ⇒ ผลที่ได้ไม่ใช่การเปรียบเทียบที่ยุติธรรม"""
    cam = FakeCam(gain_max=12.0)
    rows, _ = hx.run_ladder(cam, detector_needs_snr(), [250.0], frames=3)
    assert rows[0]["gain_capped"] is True


def test_every_frame_is_inspected_so_flicker_shows_up():
    """เจอ 5/5 กับ เจอ 1/5 คือคนละคำตอบ — ดูเฟรมเดียวจะมองไม่เห็นเลย"""
    seen = {"n": 0}

    def counting(frame):
        seen["n"] += 1
        return [{"confidence": 0.5, "class_name": "dent"}] if seen["n"] % 2 else []

    cam = FakeCam()
    rows, _ = hx.run_ladder(cam, counting, [2165.0], frames=4)
    assert rows[0]["frames_with_defect"] == 2
    assert rows[0]["defect_rate"] == 0.5


def test_blur_at_line_speed_is_attached_to_every_step():
    cam = FakeCam()
    rows, _ = hx.run_ladder(cam, detector_needs_snr(), [2165, 512], frames=3,
                            line_speed_px_s=7800.0)
    by_us = {r["exposure_us"]: r for r in rows}
    assert by_us[512.0]["blur_at_line_px"] == pytest.approx(3.99, abs=0.02)
    assert by_us[2165.0]["blur_at_line_px"] == pytest.approx(16.89, abs=0.02)


def test_an_exposure_the_camera_rejects_becomes_a_row_not_a_crash():
    cam = FakeCam()
    cam.fail_exposure_below = 400.0
    rows, _ = hx.run_ladder(cam, detector_needs_snr(), [2165, 257], frames=3)
    bad = [r for r in rows if r["exposure_us"] == 257.0][0]
    assert "error" in bad and "ช่วง" in bad["error"]


def test_cancelling_stops_early_and_still_restores():
    cam = FakeCam()
    job = FakeJob()

    class OneShot(object):
        def __init__(self):
            self.n = 0

        def __call__(self, frame):
            self.n += 1
            if self.n > 2:
                job.cancelled = True
            return []

    rows, _ = hx.run_ladder(cam, OneShot(), [2165, 1000, 514, 257], frames=3, job=job)
    assert len(rows) < 4
    assert cam.exposure == pytest.approx(2165.0) and cam.gain == pytest.approx(7.7)


# ─────────────────────────────────────────────────────────────── สรุปผล
def _row(us, rate, capped=False):
    return {"exposure_us": float(us), "defect_rate": rate, "gain_db": 10.0,
            "frames_with_defect": int(rate * 5), "gain_capped": capped}


def test_ng_limit_stops_at_the_first_failure_not_the_last_pass():
    """
    ขั้นที่ผ่านแบบฟลุ๊ค **หลังจากพังไปแล้ว** ต้องไม่ถูกนับ ไม่งั้นจะได้คำตอบ
    ที่ดีเกินจริง (บทเรียนเดียวกับ blur_tolerance.py)
    """
    rows = [_row(2165, 1.0), _row(1000, 1.0), _row(514, 0.4), _row(257, 1.0)]
    s = hx.summarize(rows, "ng", line_speed_px_s=7800.0, blur_target_px=4.0)
    assert s["limit_us"] == 1000.0


def test_ok_role_looks_for_false_positives_instead():
    rows = [{"exposure_us": 2165.0, "frames_with_defect": 0, "gain_db": 8.0},
            {"exposure_us": 1000.0, "frames_with_defect": 0, "gain_db": 12.0},
            {"exposure_us": 514.0, "frames_with_defect": 2, "gain_db": 18.0}]
    s = hx.summarize(rows, "ok")
    assert s["limit_us"] == 1000.0


def test_failing_at_the_brightest_step_blames_the_setup_not_the_exposure():
    rows = [_row(2165, 0.0), _row(1000, 0.0)]
    s = hx.summarize(rows, "ng")
    assert s["limit_us"] is None
    assert "ไม่ได้อยู่ที่ความสว่าง" in s["headline"]


def test_passing_every_step_says_the_limit_was_not_found_yet():
    rows = [_row(2165, 1.0), _row(1000, 1.0), _row(514, 1.0)]
    s = hx.summarize(rows, "ng", line_speed_px_s=7800.0, blur_target_px=4.0)
    assert s["limit_is_bottom"] is True
    assert "ยังไม่เจอขีดจำกัด" in s["note_bottom"]


def test_verdict_compares_blur_against_the_target():
    passing = hx.summarize([_row(2165, 1.0), _row(512, 1.0)], "ng",
                           line_speed_px_s=7800.0, blur_target_px=4.0)
    assert passing["meets_target"] is True and passing["headline"].startswith("✅")
    failing = hx.summarize([_row(2165, 1.0), _row(1000, 0.0)], "ng",
                           line_speed_px_s=7800.0, blur_target_px=4.0)
    assert failing["meets_target"] is False and failing["headline"].startswith("⚠️")


def test_a_gain_capped_step_cannot_be_the_answer():
    rows = [_row(2165, 1.0), _row(514, 1.0, capped=True)]
    assert hx.summarize(rows, "ng")["limit_us"] == 2165.0


# ───────────────────────────────────────────────── คำตอบรวมสองด้าน
def test_combine_refuses_with_only_one_side():
    one = hx.summarize([_row(2165, 1.0), _row(514, 1.0)], "ng")
    assert hx.combine(one, None) is None
    assert hx.combine(None, one) is None


def test_combine_takes_the_stricter_side():
    ng = hx.summarize([_row(2165, 1.0), _row(1000, 1.0), _row(514, 1.0)], "ng",
                      line_speed_px_s=7800.0)
    ok = hx.summarize([{"exposure_us": 2165.0, "frames_with_defect": 0, "gain_db": 8.0},
                       {"exposure_us": 1000.0, "frames_with_defect": 3, "gain_db": 12.0}],
                      "ok", line_speed_px_s=7800.0)
    out = hx.combine(ng, ok)
    assert out["limit_us"] == 2165.0                   # ด้าน "NG ปลอม" เข้มกว่า
    assert "NG ปลอม" in out["limited_by"]


def test_default_ladder_covers_the_blur_targets_that_matter():
    vals = hx.default_exposures()
    assert 512.0 in vals and 1025.0 in vals             # 4 px และ 8 px ที่ไลน์
    line = 7800.0
    for v in (512.0, 1025.0):                           # ต้อง **ไม่เกิน** เป้าที่ตั้งใจ
        assert line * v / 1e6 <= (4.0 if v == 512.0 else 8.0) + 1e-9
    assert vals == sorted(vals, reverse=True)


def test_default_ladder_starts_from_the_current_setting():
    vals = hx.default_exposures(1200.0)
    assert vals[0] == 1200.0 and all(v <= 1200.0 for v in vals)


def test_target_mean_matches_the_other_modules():
    """ค่าคงที่ตัวเดียวกันถูกใช้ 3 ที่ — เพี้ยนที่เดียว ตัวเลข 'ต้องเพิ่มไฟกี่เท่า' จะไม่ตรงกัน"""
    import hik_burst
    assert hx.TARGET_MEAN == hik_burst.TARGET_MEAN


def test_high_gain_noise_is_not_mistaken_for_a_moving_scene():
    """
    🐛 บั๊กที่เทสต์จับได้: ด่าน "ฉากนิ่งไหม" เทียบความต่างระหว่างเฟรมแบบดิบ ๆ
    ⇒ ที่ gain 18 dB สัญญาณรบกวนเองก็ทำให้ความต่างสูงเกินเกณฑ์ ⇒ ระบบจะบอกว่า
    "ฉากขยับ" แล้ว **ไม่ยอมรายงาน noise ในขั้นที่สำคัญที่สุดพอดี**.
    แก้ด้วยการเบลอก่อนเทียบ (ลบความถี่สูง = noise · คงโครงสร้าง = การเคลื่อนที่)
    """
    cam = FakeCam(gain=21.0, exposure=300.0, seed=7)   # gain เกือบชนเพดาน ฉากนิ่งสนิท
    st = hx.frame_stats([cam.snap_full() for _ in range(6)])
    assert st["moved"] is False, "สัญญาณรบกวนถูกเข้าใจผิดว่าเป็นการเคลื่อนที่"
    assert st["noise"] is not None and st["noise"] > 8.0
    # สัดส่วนต้องอยู่แถวค่าทฤษฎี 1/STATIC_BLUR ไม่ว่า gain จะเท่าไร
    assert st["structure_ratio"] == pytest.approx(1.0 / hx.STATIC_BLUR, abs=0.05)


@pytest.mark.parametrize("gain", [0.0, 6.0, 12.0, 18.0, 21.0])
def test_the_motion_check_is_independent_of_gain(gain):
    """
    หัวใจของด่านนี้: สัดส่วนต้อง **คงที่** ตลอดช่วง gain ที่ใช้จริง ไม่งั้นเกณฑ์
    เดียวจะใช้กับทุกขั้นของบันไดไม่ได้ (ซึ่งเป็นเหตุผลที่เลิกใช้ค่าสัมบูรณ์)
    """
    cam = FakeCam(gain=gain, exposure=300.0, seed=3)
    st = hx.frame_stats([cam.snap_full() for _ in range(6)])
    assert st["moved"] is False
    assert st["structure_ratio"] < hx.STATIC_STRUCTURE_MAX / 2.0


@pytest.mark.parametrize("shift", [8, 15, 30, 60])
def test_motion_is_caught_even_when_buried_in_heavy_noise(shift):
    """ที่ gain 21 dB (เกือบชนเพดาน) ยังแยกการเคลื่อนที่ตั้งแต่ ~8 px/เฟรม ขึ้นไปได้"""
    cam = FakeCam(gain=21.0, exposure=300.0, seed=3)
    cam.shift = shift
    st = hx.frame_stats([cam.snap_full() for _ in range(6)])
    assert st["moved"] is True and st["noise"] is None


@pytest.mark.parametrize("shift", [3, 5, 15])
def test_a_clean_image_catches_even_finer_motion(shift):
    cam = FakeCam(gain=0.0, exposure=300.0, seed=3)
    cam.shift = shift
    st = hx.frame_stats([cam.snap_full() for _ in range(6)])
    assert st["moved"] is True


def test_motion_too_small_to_matter_is_not_flagged_as_motion():
    """
    ขีดจำกัดที่วัดได้: ที่ noise สูงมาก การขยับ ≤5 px/เฟรม แยกไม่ออกจากสัญญาณรบกวน
    — และมันก็ทำให้ σ เพี้ยนน้อยมากเช่นกัน ⇒ ยอมรับได้ แต่ **ต้องรู้ว่ามีขีดนี้อยู่**
    """
    cam = FakeCam(gain=21.0, exposure=300.0, seed=3)
    cam.shift = 3
    assert hx.frame_stats([cam.snap_full() for _ in range(6)])["moved"] is False


def test_an_all_dark_run_does_not_blame_the_model(monkeypatch):
    """
    ภาพมืดสนิททุกขั้น = ไม่มีข้อมูลให้ตัดสิน — ถ้าไปขึ้นว่า "ตรวจโมเดล/การวางกระป๋อง"
    ผู้ใช้จะถูกส่งไปแก้ของที่ไม่ได้พัง (กฎเหล็กข้อ 2)
    """
    rows = [{"exposure_us": 2000.0, "defect_rate": 0.0, "frames_with_defect": 0,
             "mean": 0.0, "dark": True, "gain_db": 0.0},
            {"exposure_us": 1000.0, "defect_rate": 0.0, "frames_with_defect": 0,
             "mean": 0.0, "dark": True, "gain_db": 0.0}]
    s = hx.summarize(rows, "ng")
    assert s.get("all_dark") is True
    assert "มืดสนิท" in s["headline"] and "โมเดล" not in s["headline"]


def test_a_dark_step_next_to_a_usable_one_is_still_judged_normally():
    rows = [{"exposure_us": 2000.0, "defect_rate": 1.0, "frames_with_defect": 5,
             "mean": 90.0, "dark": False, "gain_db": 8.0},
            {"exposure_us": 300.0, "defect_rate": 0.0, "frames_with_defect": 0,
             "mean": 0.4, "dark": True, "gain_db": 24.0}]
    s = hx.summarize(rows, "ng")
    assert s.get("all_dark") is not True
    assert s["limit_us"] == 2000.0


def test_the_ladder_marks_dark_frames(monkeypatch):
    """ฝาเลนส์ปิด = ภาพดำสนิท ⇒ ต้องติดธง ไม่ใช่รายงานตัวเลขเหมือนวัดได้ปกติ"""
    cam = FakeCam(exposure=2165.0, gain=0.0)
    monkeypatch.setattr(cam, "snap_full",
                        lambda timeout=3.0: np.zeros((360, 480, 3), dtype=np.uint8))
    rows, _ = hx.run_ladder(cam, detector_needs_snr(), [2000.0], frames=3)
    assert rows[0]["dark"] is True
    assert hx.summarize(rows, "ng").get("all_dark") is True
