"""
เทสต์ของโหมด "ถ่ายรัว" (`hik_burst.py`) — รันได้บนเครื่องที่ไม่มีกล้อง/ไม่มีโมเดล.

สิ่งที่เทสต์ชุดนี้ตั้งใจ **จับให้ได้** (ไม่ใช่แค่ให้ผ่าน):
  1. ชื่อชุด/ชื่อไฟล์จาก URL หลุดออกนอกโฟลเดอร์ได้ไหม (path traversal)
  2. **การวัดความคมทั้งเฟรมจะโกหก** เมื่อฉากหลังคมและนิ่ง — ต้องวัดที่วัตถุ
  3. เบลอแนวนอนต้องถูกแยกออกจาก "เบลอทั้งสองแกน" (โฟกัสหลุด)
  4. จับคู่ตำแหน่งไม่ได้ ⇒ ต้องคืน None ไม่ใช่เดาเป็นตัวเลข
  5. ยังไม่ได้วัดผล ⇒ "ตรวจ N ใบที่คมสุด" ต้องไม่หยิบใบแรก ๆ มามั่ว
"""

import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

import hik_burst as hb                                # noqa: E402


# ════════════════════════════════════════════════════════════════════
# ชุดภาพสังเคราะห์: ฉากหลัง "คมและนิ่ง" + วัตถุที่วิ่งผ่าน
# ════════════════════════════════════════════════════════════════════
W, H = 320, 240
OBJ_W, OBJ_H = 60, 60


def _background_scene():
    """ฉากหลังที่มีรายละเอียดคมและนิ่ง — สำคัญมาก เพราะนี่คือเงื่อนไขที่ทำให้
    การวัด 'ทั้งเฟรม' โกหกว่าภาพยังคมอยู่ทั้งที่วัตถุเละ."""
    rng = np.random.RandomState(7)
    bg = rng.randint(0, 255, size=(H, W), dtype=np.uint8)
    return cv2.resize(cv2.resize(bg, (W // 2, H // 2)), (W, H),
                      interpolation=cv2.INTER_NEAREST)


def _object_patch():
    """วัตถุที่มีลายละเอียด **เท่ากันทั้งสองแกน** — สำคัญ: ถ้าลายเป็นเส้นตั้ง
    อย่างเดียว ค่า sy จะเป็น 0 แล้วอัตราส่วน x/y กลายเป็นอนันต์ = เทสต์ที่วัดอะไร
    ไม่ได้เลย. ลายแบบนี้ทำให้ 'เบลอแนวนอน' แยกจาก 'เบลอสองแกน' ได้จริง."""
    rng = np.random.RandomState(11)
    return rng.randint(60, 200, size=(OBJ_H, OBJ_W)).astype(np.uint8)


def _frame(x, blur=0, blur_axis="x"):
    """เฟรมเดียว: ฉากหลังนิ่ง + วัตถุที่ตำแหน่ง x (เบลอเฉพาะวัตถุ)."""
    img = _background_scene()
    patch = _object_patch()
    if blur:
        k = max(1, int(blur) | 1)
        ker = (np.ones((1, k)) if blur_axis == "x" else np.ones((k, 1))) / float(k)
        patch = cv2.filter2D(patch, -1, ker)
    x = max(0, min(W - OBJ_W, int(x)))
    img[80:80 + OBJ_H, x:x + OBJ_W] = patch
    return img


def make_session(tmp_path, frames, exposure_us=2000.0, dt=0.02, name="20260820_120000"):
    """สร้างโฟลเดอร์ชุดภาพเหมือนที่ตัวเขียนไฟล์ของกล้องสร้างจริง."""
    root = tmp_path / "bursts"
    d = root / name
    d.mkdir(parents=True)
    ts = []
    for i, img in enumerate(frames):
        cv2.imwrite(str(d / ("%05d.jpg" % (i + 1))), img,
                    [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        ts.append(1000.0 + i * dt)
    meta = {"started_at": "2026-08-20 12:00:00", "exposure_us": exposure_us,
            "gain_db": 0.0, "size": "%dx%d" % (W, H), "seconds": 1,
            "frame_ts": ts, "saved": len(frames), "dropped": 0}
    (d / hb.META_FILE).write_text(json.dumps(meta), encoding="utf-8")
    return name, str(root)


@pytest.fixture
def burst_root(tmp_path, monkeypatch):
    root = tmp_path / "bursts"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(hb, "_cfg",
                        lambda n, d=None: {"HIK_BURST_DIR": str(root),
                                           "HIK_BURST_THUMB_WIDTH": 80,
                                           "HIK_BURST_MM_PER_PX": None,
                                           # 450 ใบ/นาที — ใช้เทียบว่าผลที่วัดได้
                                           # บอกอะไรเกี่ยวกับไลน์จริงได้บ้าง
                                           "HIK_BURST_LINE_SPEED_PX_S": 7800.0,
                                           "HIK_BURST_LINE_SPEED_MIN_RATIO": 0.25,
                                           }.get(n, d))
    return root


# ── ① ความปลอดภัยของเส้นทางไฟล์ ─────────────────────────────────────
@pytest.mark.parametrize("bad", [
    "..", ".", "../etc", "a/b", "/etc/passwd", "..\\win", "", "x" * 65, None, 5,
])
def test_session_dir_rejects_unsafe_names(burst_root, bad):
    with pytest.raises(ValueError):
        hb.session_dir(bad, must_exist=False)


def test_session_dir_rejects_missing_session(burst_root):
    with pytest.raises(ValueError):
        hb.session_dir("20260101_000000")


@pytest.mark.parametrize("bad", ["../../secret.jpg", "meta.json", "1.png", "a.jpg", "1.jpg.bak"])
def test_frame_path_rejects_non_frame_names(burst_root, tmp_path, bad):
    name, _ = make_session(tmp_path, [_frame(10)])
    with pytest.raises(ValueError):
        hb.frame_path(name, bad)


def test_frame_path_accepts_real_frame(burst_root, tmp_path):
    name, _ = make_session(tmp_path, [_frame(10)])
    assert os.path.isfile(hb.frame_path(name, "00001.jpg"))


# ── ② หัวใจ: ต้องวัดที่ "วัตถุ" ไม่ใช่ทั้งเฟรม ───────────────────────
def test_whole_frame_sharpness_hides_a_smeared_object(burst_root, tmp_path):
    """เทสต์ที่พิสูจน์ว่าทำไมต้องมีชั้นหาบริเวณที่เคลื่อนไหว.

    ถ้าวัดทั้งเฟรม ค่าความคมของภาพที่วัตถุเบลอหนักจะ **แทบไม่ต่าง** จากภาพที่คม
    (ฉากหลังกลบไปหมด) ⇒ หน้าเว็บจะบอกว่า "คม" ทั้งที่วัตถุอ่านไม่ออก.
    """
    sharp_full, _, _ = hb._scores(_frame(40))
    smear_full, _, _ = hb._scores(_frame(40, blur=15))
    assert smear_full > sharp_full * 0.90       # ทั้งเฟรม: แทบแยกไม่ออก (วัดได้ 0.97)

    s_sharp, _, _ = hb._scores(_frame(40)[80:140, 40:100])
    s_smear, _, _ = hb._scores(_frame(40, blur=15)[80:140, 40:100])
    assert s_smear < s_sharp * 0.30             # เฉพาะวัตถุ: ต่างกันชัดเจน (วัดได้ 0.10)


def test_metrics_measures_the_moving_object_not_the_frame(burst_root, tmp_path):
    frames = [_frame(20 + i * 25, blur=0 if i == 2 else 17) for i in range(6)]
    name, _ = make_session(tmp_path, frames)
    data = hb.compute_metrics(name)
    fm = data["frames"]
    assert len(fm) == 6
    assert sum(1 for v in fm.values() if v["roi_src"] == "moving") >= 4
    # ใบที่ไม่ได้เบลอต้องเป็นใบที่คมที่สุดของชุด
    assert data["summary"]["best_file"] == "00003.jpg"


def test_sharp_frame_is_flagged_sharp_and_blurred_is_not(burst_root, tmp_path):
    frames = [_frame(20 + i * 25, blur=0 if i == 2 else 17) for i in range(6)]
    name, _ = make_session(tmp_path, frames)
    fm = hb.compute_metrics(name)["frames"]
    assert fm["00003.jpg"]["blur_kind"] == "sharp"
    assert fm["00001.jpg"]["blur_kind"] != "sharp"


# ── ③ แยก "เบลอเพราะเคลื่อนที่" ออกจาก "โฟกัสหลุด" ────────────────
def test_horizontal_smear_lowers_the_x_over_y_ratio(burst_root):
    crop = _frame(40)[80:140, 40:100]
    _, sx0, sy0 = hb._scores(crop)
    smear = cv2.filter2D(crop, -1, np.ones((1, 15)) / 15.0)
    _, sx1, sy1 = hb._scores(smear)
    assert (sx1 / max(sy1, 1e-6)) < (sx0 / max(sy0, 1e-6)) * hb.DIR_RATIO_MARGIN


def test_isotropic_blur_keeps_the_ratio(burst_root):
    crop = _frame(40)[80:140, 40:100]
    _, sx0, sy0 = hb._scores(crop)
    soft = cv2.GaussianBlur(crop, (9, 9), 3)
    _, sx1, sy1 = hb._scores(soft)
    r0, r1 = sx0 / max(sy0, 1e-6), sx1 / max(sy1, 1e-6)
    assert hb.DIR_RATIO_MARGIN < (r1 / r0) < 1.0 / hb.DIR_RATIO_MARGIN


def test_direction_label_is_left_blank_when_evidence_is_weak():
    frames = {"a.jpg": {"sharp": 10.0, "ratio": None}}
    hb._annotate_directions(frames, {"best_sharp": 100.0, "base_ratio": 1.2})
    assert frames["a.jpg"]["blur_kind"] is None


# ── ④ ความเร็ว / ระยะเบลอ — และ "ไม่มั่นใจ = ไม่ตอบ" ────────────────
def test_speed_and_blur_are_derived_from_measured_shift(burst_root, tmp_path):
    step, dt, exposure = 25, 0.02, 2000.0
    frames = [_frame(20 + i * step) for i in range(6)]
    name, _ = make_session(tmp_path, frames, exposure_us=exposure, dt=dt)
    data = hb.compute_metrics(name)
    s = data["summary"]
    assert s["speed_px_s"] == pytest.approx(step / dt, rel=0.2)
    assert s["blur_px_median"] == pytest.approx(
        s["speed_px_s"] * exposure / 1e6, rel=0.05)
    # exposure สูงสุดที่เบลอ ≤1 px และ "ต้องเพิ่มไฟกี่เท่า" ต้องสอดคล้องกันเสมอ
    assert s["max_exposure_us_1px"] == pytest.approx(1e6 / s["speed_px_s"], rel=0.01)
    assert s["light_factor_needed"] == pytest.approx(
        exposure / s["max_exposure_us_1px"], rel=0.02)


def test_no_motion_means_no_speed_instead_of_a_made_up_number(burst_root, tmp_path):
    """ทุกเฟรมเหมือนกันเป๊ะ = ไม่มีวัตถุเคลื่อนไหว ⇒ ต้องไม่มีตัวเลขความเร็ว."""
    frames = [_frame(40) for _ in range(5)]
    name, _ = make_session(tmp_path, frames)
    s = hb.compute_metrics(name)["summary"]
    assert s["speed_px_s"] is None
    assert s["blur_px_median"] is None
    assert s["moving_frames"] == 0


def test_shift_alone_cannot_reject_uncorrelated_noise(burst_root):
    """บันทึกข้อเท็จจริงที่ทำให้ต้องมีตัวตรวจซ้ำ: ภาพสุ่มสองใบที่ไม่เกี่ยวกันเลย
    ยังได้คะแนน phase correlation สูงพอจะผ่านเกณฑ์ ⇒ คะแนนอย่างเดียวเชื่อไม่ได้."""
    rng = np.random.RandomState(3)
    a = rng.randint(0, 255, (64, 64)).astype(np.uint8)
    b = rng.randint(0, 255, (64, 64)).astype(np.uint8)
    assert hb._shift(a, b, (0, 0, 64, 64)) is not None


def test_shift_is_rejected_when_it_contradicts_the_object_box(burst_root):
    """ตัวตรวจซ้ำที่แก้ปัญหาข้างบน — ค่าที่ขัดกับระยะที่กรอบวัตถุขยับ = ไม่รายงาน."""
    rng = np.random.RandomState(3)
    a = rng.randint(0, 255, (64, 64)).astype(np.uint8)
    b = rng.randint(0, 255, (64, 64)).astype(np.uint8)
    assert hb._shift(a, b, (0, 0, 64, 64), expect_px=90.0) is None


def test_shift_survives_the_cross_check_on_a_real_translation(burst_root):
    a = cv2.resize(_frame(40), (0, 0), fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    b = cv2.resize(_frame(64), (0, 0), fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    # หน้าต่างต้อง "รัดวัตถุ" — ดูเทสต์ถัดไปว่าทำไม
    got = hb._shift(a, b, (18, 38, 46, 34), expect_px=24.0)
    assert got is not None and got == pytest.approx(24.0, abs=4.0)


def test_shift_declines_when_the_window_is_mostly_background(burst_root):
    """ฉากหลังที่นิ่งและกินพื้นที่ส่วนใหญ่จะ "ชนะ" การจับคู่ แล้วได้ระยะเลื่อน ≈ 0
    ทั้งที่วัตถุขยับจริง — ตัวตรวจซ้ำต้องจับได้และคืน None แทนที่จะรายงาน 0."""
    a = cv2.resize(_frame(40), (0, 0), fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    b = cv2.resize(_frame(64), (0, 0), fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    assert hb._shift(a, b, (10, 30, 80, 50), expect_px=24.0) is None


def test_centre_shift_is_measured_in_full_resolution_pixels(burst_root):
    assert hb._centre_shift((0, 0, 10, 10), (10, 0, 10, 10)) == pytest.approx(20.0)


def test_shift_returns_none_when_window_is_too_small(burst_root):
    a = np.zeros((8, 8), np.uint8)
    assert hb._shift(a, a, (0, 0, 8, 8)) is None


def test_missing_timestamps_give_shift_but_no_speed(burst_root, tmp_path):
    frames = [_frame(20 + i * 25) for i in range(4)]
    name, root = make_session(tmp_path, frames)
    meta_p = os.path.join(root, name, hb.META_FILE)
    meta = json.loads(open(meta_p, encoding="utf-8").read())
    meta.pop("frame_ts")
    open(meta_p, "w", encoding="utf-8").write(json.dumps(meta))
    fm = hb.compute_metrics(name)["frames"]
    moved = [v for v in fm.values() if v.get("shift_px")]
    assert moved, "ควรวัดระยะเลื่อนได้แม้ไม่มีเวลา"
    assert all("speed_px_s" not in v for v in fm.values())


# ── ⑤ thumbnail / metrics.json ─────────────────────────────────────
def test_metrics_writes_json_and_thumbnails(burst_root, tmp_path):
    frames = [_frame(20 + i * 25) for i in range(4)]
    name, root = make_session(tmp_path, frames)
    hb.compute_metrics(name)
    assert os.path.isfile(os.path.join(root, name, hb.METRICS_FILE))
    assert os.path.isfile(os.path.join(root, name, hb.THUMB_DIR, "00001.jpg"))
    assert hb.load_metrics(name) is not None


def test_load_metrics_rejects_an_older_format(burst_root, tmp_path):
    name, root = make_session(tmp_path, [_frame(10)])
    open(os.path.join(root, name, hb.METRICS_FILE), "w").write('{"version": 0}')
    assert hb.load_metrics(name) is None


def test_metrics_json_is_serialisable(burst_root, tmp_path):
    """ค่าที่หลุดมาจาก numpy จะทำให้ json.dumps ระเบิด — กับดักเดียวกับที่เคย
    เจอใน pixdiff (`report.json` เขียนไม่ได้แบบเงียบ ๆ)."""
    name, _ = make_session(tmp_path, [_frame(20 + i * 25) for i in range(4)])
    data = hb.compute_metrics(name)
    json.dumps(data)                                  # ต้องไม่โยน TypeError


# ── ⑥ การเรียงลำดับ / รายละเอียดชุด ────────────────────────────────
def test_detail_sorts_sharpest_first(burst_root, tmp_path):
    frames = [_frame(20 + i * 25, blur=0 if i == 3 else 17) for i in range(6)]
    name, _ = make_session(tmp_path, frames)
    hb.compute_metrics(name)
    rows = hb.session_detail(name, sort="sharp")["frames"]
    assert rows[0]["file"] == "00004.jpg"


def test_detail_sort_by_name_is_chronological(burst_root, tmp_path):
    name, _ = make_session(tmp_path, [_frame(20 + i * 25) for i in range(4)])
    hb.compute_metrics(name)
    rows = hb.session_detail(name, sort="name")["frames"]
    assert [r["file"] for r in rows] == ["00001.jpg", "00002.jpg",
                                         "00003.jpg", "00004.jpg"]


def test_detail_works_before_metrics_are_computed(burst_root, tmp_path):
    name, _ = make_session(tmp_path, [_frame(10), _frame(30)])
    d = hb.session_detail(name)
    assert d["metrics_ready"] is False
    assert d["total"] == 2


def test_detail_paginates(burst_root, tmp_path):
    name, _ = make_session(tmp_path, [_frame(10 + i * 5) for i in range(10)])
    d = hb.session_detail(name, sort="name", limit=4, offset=4)
    assert d["total"] == 10 and len(d["frames"]) == 4


# ── ⑦ การตรวจด้วยโมเดล (ฉีดฟังก์ชันเข้ามา) ─────────────────────────
def test_top_sharp_returns_nothing_without_metrics(burst_root, tmp_path):
    """ยังไม่ได้วัด = ไม่รู้ว่าใบไหนคม ⇒ ต้องคืนว่าง ไม่ใช่หยิบใบแรก ๆ มามั่ว."""
    name, _ = make_session(tmp_path, [_frame(10 + i * 20) for i in range(5)])
    assert hb.top_sharp_files(name, 3) == []


def test_top_sharp_ignores_files_already_deleted(burst_root, tmp_path):
    frames = [_frame(20 + i * 25, blur=0 if i == 1 else 17) for i in range(5)]
    name, root = make_session(tmp_path, frames)
    hb.compute_metrics(name)
    os.remove(os.path.join(root, name, "00002.jpg"))
    assert "00002.jpg" not in hb.top_sharp_files(name, 5)


def test_run_detect_writes_results(burst_root, tmp_path):
    name, root = make_session(tmp_path, [_frame(10), _frame(40)])
    hb.run_detect(name, ["00001.jpg", "00002.jpg"],
                  lambda p: {"verdict": "ng", "dent_count": 1})
    det = json.loads(open(os.path.join(root, name, hb.DETECT_FILE), encoding="utf-8").read())
    assert det["00001.jpg"]["verdict"] == "ng"
    assert len(det) == 2


def test_run_detect_records_the_error_instead_of_dying(burst_root, tmp_path):
    name, _ = make_session(tmp_path, [_frame(10), _frame(40)])

    def boom(path):
        if path.endswith("00001.jpg"):
            raise RuntimeError("โมเดลล้ม")
        return {"verdict": "ok"}

    det = hb.run_detect(name, ["00001.jpg", "00002.jpg"], boom)
    assert "โมเดลล้ม" in det["00001.jpg"]["error"]
    assert det["00002.jpg"]["verdict"] == "ok"        # ใบเดียวพัง ไม่ล้มทั้งชุด


def test_run_detect_skips_files_that_do_not_exist(burst_root, tmp_path):
    name, _ = make_session(tmp_path, [_frame(10)])
    seen = []
    hb.run_detect(name, ["00001.jpg", "09999.jpg", "../x.jpg"],
                  lambda p: seen.append(p) or {"verdict": "ok"})
    assert len(seen) == 1


def test_detect_results_show_up_in_detail(burst_root, tmp_path):
    name, _ = make_session(tmp_path, [_frame(10), _frame(40)])
    hb.run_detect(name, ["00002.jpg"], lambda p: {"verdict": "ng", "dent_count": 2})
    rows = {r["file"]: r for r in hb.session_detail(name, sort="name")["frames"]}
    assert rows["00002.jpg"]["verdict"] == "ng"
    assert rows["00001.jpg"].get("verdict") is None


# ── ⑧ การลบ ────────────────────────────────────────────────────────
def test_delete_frames_removes_files_thumbs_and_results(burst_root, tmp_path):
    name, root = make_session(tmp_path, [_frame(10), _frame(40), _frame(70)])
    hb.compute_metrics(name)
    hb.run_detect(name, ["00002.jpg"], lambda p: {"verdict": "ok"})
    removed = hb.delete_frames(name, ["00002.jpg"])
    assert removed == ["00002.jpg"]
    assert not os.path.exists(os.path.join(root, name, "00002.jpg"))
    assert not os.path.exists(os.path.join(root, name, hb.THUMB_DIR, "00002.jpg"))
    det = json.loads(open(os.path.join(root, name, hb.DETECT_FILE), encoding="utf-8").read())
    assert "00002.jpg" not in det


def test_delete_frames_ignores_unsafe_names(burst_root, tmp_path):
    name, root = make_session(tmp_path, [_frame(10)])
    outside = tmp_path / "keepme.txt"
    outside.write_text("x")
    assert hb.delete_frames(name, ["../keepme.txt", "../../keepme.txt"]) == []
    assert outside.exists()


def test_deleted_frames_disappear_from_the_listing(burst_root, tmp_path):
    name, _ = make_session(tmp_path, [_frame(10), _frame(40)])
    hb.compute_metrics(name)
    hb.delete_frames(name, ["00001.jpg"])
    d = hb.session_detail(name)
    assert d["total"] == 1 and d["frames"][0]["file"] == "00002.jpg"


def test_delete_session_removes_everything(burst_root, tmp_path):
    name, root = make_session(tmp_path, [_frame(10)])
    hb.delete_session(name)
    assert not os.path.exists(os.path.join(root, name))
    with pytest.raises(ValueError):
        hb.session_detail(name)


# ── ⑨ รายการชุด ────────────────────────────────────────────────────
def test_list_sessions_reports_size_and_settings(burst_root, tmp_path):
    make_session(tmp_path, [_frame(10), _frame(40)], exposure_us=1500.0)
    rows = hb.list_sessions()
    assert len(rows) == 1
    assert rows[0]["frames"] == 2
    assert rows[0]["exposure_us"] == 1500.0
    assert rows[0]["mb"] >= 0
    assert rows[0]["metrics_ready"] is False


def test_list_sessions_is_newest_first(burst_root, tmp_path):
    make_session(tmp_path, [_frame(10)], name="20260820_090000")
    make_session(tmp_path, [_frame(10)], name="20260820_100000")
    assert [r["name"] for r in hb.list_sessions()] == ["20260820_100000", "20260820_090000"]


def test_list_sessions_survives_a_folder_without_meta(burst_root, tmp_path):
    name, root = make_session(tmp_path, [_frame(10)])
    os.remove(os.path.join(root, name, hb.META_FILE))
    assert hb.list_sessions()[0]["frames"] == 1


# ── ⑩ งานเบื้องหลัง — ทีละงานเท่านั้น ────────────────────────────
def test_only_one_background_job_at_a_time(burst_root, tmp_path):
    import threading
    hold = threading.Event()
    hb._job = None
    ok1, _ = hb.start_job("metrics", "a", lambda job: hold.wait(2.0))
    ok2, why = hb.start_job("detect", "a", lambda job: None)
    assert ok1 is True
    assert ok2 is False and "รอให้เสร็จ" in why
    hold.set()


def test_job_status_reports_progress_and_finishes(burst_root):
    import time
    hb._job = None

    def work(job):
        job.total, job.done = 2, 2

    assert hb.start_job("metrics", "a", work)[0] is True
    for _ in range(50):
        if not (hb.job_status() or {}).get("running"):
            break
        time.sleep(0.02)
    st = hb.job_status()
    assert st["done"] == 2 and st["running"] is False and st["error"] is None


def test_job_records_the_error(burst_root):
    import time
    hb._job = None

    def boom(job):
        raise RuntimeError("พัง")

    hb.start_job("metrics", "a", boom)
    for _ in range(50):
        if not (hb.job_status() or {}).get("running"):
            break
        time.sleep(0.02)
    assert "พัง" in hb.job_status()["error"]


def test_cancelled_metrics_job_stops_early(burst_root, tmp_path):
    name, _ = make_session(tmp_path, [_frame(10 + i * 6) for i in range(8)])
    job = hb.Job("metrics", name)
    job.cancelled = True
    data = hb.compute_metrics(name, job=job)
    assert data["count"] == 0


def test_compute_metrics_refuses_an_empty_session(burst_root, tmp_path):
    d = burst_root / "20260820_130000"
    d.mkdir()
    with pytest.raises(RuntimeError):
        hb.compute_metrics("20260820_130000")


# ── ⑪ ลบขณะมีงานทำอยู่ (เจอจากการทดสอบด้วยเบราว์เซอร์จริง) ──────────
def test_job_running_on_tracks_the_session(burst_root):
    import threading
    hold = threading.Event()
    hb._job = None
    hb.start_job("metrics", "S1", lambda job: hold.wait(2.0))
    assert hb.job_running_on("S1") is True
    assert hb.job_running_on("S2") is False
    hold.set()
    hb.cancel_job(wait=2.0)


def test_cancel_job_can_wait_for_the_worker_to_stop(burst_root):
    import threading
    go = threading.Event()
    hb._job = None

    def work(job):
        while not job.cancelled and not go.is_set():
            time.sleep(0.01)

    hb.start_job("metrics", "S1", work)
    assert hb.cancel_job(wait=3.0) is True
    assert hb.job_running_on("S1") is False


def test_cancel_job_without_a_job_is_harmless(burst_root):
    hb._job = None
    assert hb.cancel_job() is False
    assert hb.job_running_on("S1") is False


# ── ⑫ "ต้องเพิ่มไฟกี่เท่า" ต้องไม่พูดน้อยกว่าความจริง ────────────────
def test_light_factor_separates_same_brightness_from_usable_brightness(burst_root, tmp_path):
    """ตัวเลข "รักษาความสว่างเดิม" อย่างเดียวทำให้เข้าใจผิดว่าเพิ่มไฟเท่านั้นแล้วจบ
    ทั้งที่ภาพตอนนี้ก็มืดเกินไปอยู่แล้ว — ต้องรายงานสองตัวแยกกัน."""
    frames = [_frame(20 + i * 25) for i in range(6)]
    name, _ = make_session(tmp_path, frames, exposure_us=2635.0)
    s = hb.compute_metrics(name)["summary"]
    assert s["mean_median"] is not None
    assert s["light_factor_usable"] == pytest.approx(
        s["light_factor_needed"] * hb.TARGET_MEAN / s["mean_median"], rel=0.02)


def test_a_pitch_black_burst_reports_no_usable_light_factor(burst_root, tmp_path):
    """ภาพดำสนิท ⇒ หารด้วยความสว่างไม่ได้ ⇒ ต้องไม่พ่นตัวเลขมหาศาลออกมา."""
    black = [np.zeros((H, W), np.uint8) for _ in range(4)]
    name, _ = make_session(tmp_path, black, exposure_us=200.0)
    s = hb.compute_metrics(name)["summary"]
    assert "light_factor_usable" not in s


def test_light_factor_matches_the_exposure_it_recommends(burst_root, tmp_path):
    name, _ = make_session(tmp_path, [_frame(20 + i * 25) for i in range(6)],
                           exposure_us=1000.0)
    s = hb.compute_metrics(name)["summary"]
    assert s["light_factor_needed"] == pytest.approx(
        1000.0 / s["max_exposure_us_1px"], rel=0.02)


# ── ⑬ วินิจฉัย "เฟรมหายไปตรงไหน" ───────────────────────────────────
# วิธีแก้ของสามสาเหตุนี้คนละเรื่องกันโดยสิ้นเชิง ⇒ ชี้ผิดสาเหตุ = ไปแก้ของที่ไม่พัง
def _meta(saved=100, dropped=0, elapsed=10.0, every_n=1, size="1224x1024",
          cam_dropped=(0, 0), lost_packets=None, framerate_enable=False,
          framerate=None, packet_size=8164, cam_fps=None):
    a = {"cam_frames": 0, "cam_dropped": cam_dropped[0], "cam_timeouts": 0,
         "cam_fps": cam_fps}
    b = {"cam_frames": saved + dropped, "cam_dropped": cam_dropped[1],
         "cam_timeouts": 0, "cam_fps": cam_fps}
    if lost_packets is not None:
        a["net"] = {"lost_packets": 0, "lost_frames": 0, "recv_frames": 0}
        b["net"] = {"lost_packets": lost_packets, "lost_frames": 0, "recv_frames": 0}
    return {"saved": saved, "dropped": dropped, "elapsed_s": elapsed,
            "every_n": every_n, "size": size, "packet_size": packet_size,
            "framerate_enable": framerate_enable, "framerate": framerate,
            "diag_start": a, "diag_end": b, "stage_ms": {"encode": 8.0, "write": 2.0}}


def test_diagnose_blames_the_network_when_frame_numbers_jump(burst_root):
    g = hb.diagnose(_meta(saved=100, dropped=0, cam_dropped=(0, 40), packet_size=1500))
    assert g["cause"] == "transport"
    assert g["lost_transport"] == 40
    assert "packet size" in g["fix"]


def test_transport_advice_does_not_tell_you_to_fix_what_is_already_right(burst_root):
    """
    เจอบนสถานี 24 ส.ค.: Jumbo เปิด + packet 8164 อยู่แล้ว แต่กล่องวินิจฉัยยังบอกให้
    "ตั้ง packet size ให้ใหญ่ขึ้นและเปิด Jumbo Frame" = ส่งผู้ใช้ไปแก้ของที่ไม่ได้พัง
    และปิดบังสาเหตุจริงซึ่งอยู่ฝั่งรับ (กฎเหล็กข้อ 2 — คำแนะนำต้องดูหลักฐานของตัวเอง)
    """
    g = hb.diagnose(_meta(saved=100, dropped=0, cam_dropped=(0, 40), packet_size=8164))
    assert g["cause"] == "transport"
    assert g["jumbo"] is True
    assert "ตั้ง packet size ให้ใหญ่ขึ้น" not in g["fix"]
    assert "8164" in g["fix"]                      # อ้างหลักฐานที่เก็บมาเอง
    assert "Receive Buffers" in g["fix"]           # ชี้ไปที่ฝั่งรับแทน


def test_transport_advice_still_names_packet_size_when_jumbo_is_off(burst_root):
    """อีกด้านของเหรียญ: packet เล็กจริง ⇒ ต้องบอกให้ตั้ง packet size + เปิด Jumbo"""
    g = hb.diagnose(_meta(saved=100, dropped=0, cam_dropped=(0, 40), packet_size=1500))
    assert g["jumbo"] is False
    assert "packet size" in g["fix"] and "Jumbo Frame" in g["fix"]
    assert "Receive Buffers" not in g["fix"]


def test_tiny_transport_loss_is_reported_as_not_worth_chasing(burst_root):
    """
    2 เฟรมจาก 683 = 0.3% — ต้องบอกสัดส่วน และบอกว่ายังไม่ใช่ปัญหาที่ต้องไล่ตอนนี้
    ไม่งั้นผู้ใช้เสียเวลากับของที่กระทบผลตรวจน้อยกว่าเรื่องอื่นมาก
    """
    g = hb.diagnose(_meta(saved=101, dropped=0, elapsed=10.0, every_n=7,
                          cam_dropped=(0, 2), packet_size=8164))
    assert g["cause"] == "transport"
    assert "0.3%" in g["text"]
    assert "ยังไม่ใช่ปัญหาที่ต้องไล่ตอนนี้" in g["fix"]


def test_big_transport_loss_is_not_downplayed(burst_root):
    """กลับด้าน: หายเยอะต้องไม่ขึ้นข้อความ 'ยังไม่ใช่ปัญหา'"""
    g = hb.diagnose(_meta(saved=100, dropped=0, cam_dropped=(0, 40), packet_size=8164))
    assert "ยังไม่ใช่ปัญหาที่ต้องไล่ตอนนี้" not in g["fix"]


def test_diagnose_blames_the_disk_when_frames_arrived_but_were_dropped(burst_root):
    # เฟรมมาถึงเต็มอัตรา (690 ใน 10 วิ) แต่เขียนได้แค่ 563 ⇒ เหลือสาเหตุเดียวคือดิสก์
    g = hb.diagnose(_meta(saved=563, dropped=127, elapsed=10.0))
    assert g["cause"] == "disk"
    assert g["dropped_disk"] == 127
    assert "18%" in g["text"]


def test_diagnose_reports_every_cause_not_just_the_first(burst_root):
    """
    เคสจริงบนสถานี: กล้องส่งมาแค่ 16.5 fps **และ** ดิสก์ทิ้ง 127 เฟรม
    ถ้ารายงานแค่ข้อเดียว ผู้ใช้จะแก้ดิสก์แล้วยังติดที่ 16.5 fps อยู่ดี
    """
    g = hb.diagnose(_meta(saved=38, dropped=127, elapsed=10.0))
    causes = [i["cause"] for i in g["issues"]]
    assert "camera_rate" in causes and "disk" in causes
    # ลำดับ = ลำดับที่ควรลงมือแก้: เฟรมที่ "ไม่เคยมี" ก่อนเฟรมที่ "เขียนไม่ทัน"
    assert causes.index("camera_rate") < causes.index("disk")
    assert g["cause"] == "camera_rate"


def test_a_healthy_burst_has_exactly_one_ok_issue(burst_root):
    g = hb.diagnose(_meta(saved=690, dropped=0, elapsed=10.0))
    assert [i["cause"] for i in g["issues"]] == ["ok"]


def test_transport_loss_outranks_disk_loss(burst_root):
    """ทั้งสองอย่างพร้อมกัน ⇒ ต้องชี้ที่เครือข่ายก่อน เพราะการลดจำนวนภาพไม่ช่วยเลย"""
    g = hb.diagnose(_meta(saved=38, dropped=127, cam_dropped=(0, 40)))
    assert g["cause"] == "transport"


def test_diagnose_spots_a_self_imposed_frame_rate_cap(burst_root):
    g = hb.diagnose(_meta(saved=165, dropped=0, elapsed=10.0,
                          framerate_enable=True, framerate=16.5))
    assert g["cause"] == "framerate_cap"
    assert "16.5" in g["text"]
    # รู้สาเหตุแน่ชัดแล้ว ⇒ ไม่ต้องเดาซ้ำว่า "กล้องช้าเอง"
    assert "camera_rate" not in [i["cause"] for i in g["issues"]]


def test_diagnose_flags_a_camera_that_is_simply_slow(burst_root):
    """ไม่มีเฟรมหาย ไม่มีการทิ้ง ไม่มีการจำกัด แต่ยังได้แค่ 16.5 fps
    = ลายเซ็นของ packet size ที่ยังเป็นค่าโรงงาน"""
    g = hb.diagnose(_meta(saved=165, dropped=0, elapsed=10.0, packet_size=1500))
    assert g["cause"] == "camera_rate"
    assert g["delivered_fps"] == pytest.approx(16.5, rel=0.01)
    assert g["gige_ceiling_fps"] > 30


def test_diagnose_says_ok_when_nothing_is_wrong(burst_root):
    g = hb.diagnose(_meta(saved=690, dropped=0, elapsed=10.0))
    assert g["cause"] == "ok"
    assert g["delivered_fps"] == pytest.approx(69.0, rel=0.01)


def test_delivered_fps_counts_the_frames_every_n_threw_away(burst_root):
    """เก็บ 1 ใน 7 แล้วได้ 10 ภาพ/วิ ⇒ กล้องส่งมาจริง 70 fps ไม่ใช่ 10"""
    g = hb.diagnose(_meta(saved=100, dropped=0, elapsed=10.0, every_n=7))
    assert g["delivered_fps"] == pytest.approx(70.0, rel=0.01)


def test_gige_ceiling_depends_on_jumbo_frames(burst_root):
    big = hb.diagnose(_meta(packet_size=8164))
    small = hb.diagnose(_meta(packet_size=1500))
    assert big["jumbo"] is True and small["jumbo"] is False
    assert big["gige_ceiling_fps"] > small["gige_ceiling_fps"]


def test_diagnose_reports_lost_packets_from_the_sdk(burst_root):
    g = hb.diagnose(_meta(saved=100, dropped=0, lost_packets=500))
    assert g["cause"] == "transport"
    assert g["lost_packets"] == 500


def test_diagnose_returns_none_when_it_cannot_tell(burst_root):
    assert hb.diagnose(None) is None
    assert hb.diagnose({}) is None
    assert hb.diagnose({"saved": 0, "dropped": 0, "elapsed_s": 10}) is None
    assert hb.diagnose({"saved": 10, "dropped": 0}) is None       # ไม่มี elapsed


def test_diagnose_survives_meta_without_the_new_fields(burst_root):
    """ชุดภาพที่ถ่ายไว้ก่อนมีระบบวินิจฉัย ต้องไม่ทำให้หน้าเว็บพัง"""
    g = hb.diagnose({"saved": 38, "dropped": 127, "elapsed_s": 10.0})
    assert g is not None
    # ไม่มี size ⇒ คำนวณเพดานสายไม่ได้ ⇒ ไม่กล่าวหาว่ากล้องช้า (ไม่เดา)
    assert "gige_ceiling_fps" not in g
    assert g["cause"] == "disk"
    assert g["lost_transport"] is None                 # ไม่รู้ ⇒ None ไม่ใช่ 0


def test_size_parsing_is_tolerant(burst_root):
    assert hb._size_of({"size": "1224x1024"}) == (1224, 1024)
    assert hb._size_of({"size": "แปลก"}) == (None, None)
    assert hb._size_of({}) == (None, None)


def test_session_detail_carries_the_diagnosis(burst_root, tmp_path):
    name, root = make_session(tmp_path, [_frame(10), _frame(40)])
    meta_p = os.path.join(root, name, hb.META_FILE)
    m = json.loads(open(meta_p, encoding="utf-8").read())
    m.update(_meta(saved=38, dropped=127))
    open(meta_p, "w", encoding="utf-8").write(json.dumps(m))
    d = hb.session_detail(name)
    assert "disk" in [i["cause"] for i in d["diag"]["issues"]]
    assert hb.session_brief(name)["diag"] is not None


# ── ⑭ "ขยับน้อยเกินกว่าจะวัดได้" ต้องไม่ถูกรายงานเป็น "ช้าและคมดี" ──
# เจอจริงบนสถานี 21 ส.ค.: วัตถุขยับ 0.53 px/เฟรม แล้วระบบขึ้นไฟเขียว "คมพอ"
# พร้อม exposure สูงสุด 217,391 µs (0.22 วินาที) — ตัวเลขไร้ความหมายแต่ดูน่าเชื่อถือ
def _still_frames(n=8):
    """กระป๋องวางนิ่ง ขยับแค่ระดับสัญญาณรบกวน"""
    return [_frame(40 + (i % 2)) for i in range(n)]


def test_a_barely_moving_object_is_reported_as_unmeasurable(burst_root, tmp_path):
    name, _ = make_session(tmp_path, _still_frames(), exposure_us=5055.0)
    s = hb.compute_metrics(name)["summary"]
    assert s["motion"] in ("negligible", "unknown")


def test_no_exposure_advice_when_the_object_barely_moved(burst_root, tmp_path):
    """ห้ามคำนวณ exposure สูงสุด/ตัวคูณไฟจากความเร็วที่เป็นสัญญาณรบกวน"""
    name, _ = make_session(tmp_path, _still_frames(), exposure_us=5055.0)
    s = hb.compute_metrics(name)["summary"]
    assert "max_exposure_us_1px" not in s
    assert "light_factor_needed" not in s


def test_real_motion_still_gets_full_advice(burst_root, tmp_path):
    """ของที่ขยับจริงต้องไม่ถูกด่านนี้ตัดทิ้ง"""
    name, _ = make_session(tmp_path, [_frame(20 + i * 25) for i in range(6)],
                           exposure_us=2000.0)
    s = hb.compute_metrics(name)["summary"]
    assert s["motion"] == "ok"
    assert s["shift_px_median"] >= hb.MIN_MEANINGFUL_SHIFT_PX
    assert s["max_exposure_us_1px"] > 0


def test_no_direction_badges_on_a_still_burst(burst_root, tmp_path):
    """ภาพนิ่งต้องไม่ได้ป้าย 'เบลอแนวตั้ง' จากความต่างเล็ก ๆ ของอัตราส่วนแกน"""
    name, _ = make_session(tmp_path, _still_frames(), exposure_us=5055.0)
    fm = hb.compute_metrics(name)["frames"]
    kinds = {v.get("blur_kind") for v in fm.values()}
    assert kinds <= {"sharp", None}, kinds


def test_direction_badges_survive_when_motion_is_real(burst_root, tmp_path):
    frames = [_frame(20 + i * 25, blur=0 if i == 2 else 17) for i in range(6)]
    name, _ = make_session(tmp_path, frames)
    fm = hb.compute_metrics(name)["frames"]
    assert any(v.get("blur_kind") in ("motion_x", "motion_y", "isotropic")
               for v in fm.values())


def test_shift_median_is_reported_so_the_user_can_judge(burst_root, tmp_path):
    name, _ = make_session(tmp_path, [_frame(20 + i * 25) for i in range(6)])
    s = hb.compute_metrics(name)["summary"]
    assert s["shift_px_median"] is not None
    assert s["min_shift_px"] == hb.MIN_MEANINGFUL_SHIFT_PX


# ── ⑮ คำแนะนำต้องตรงกับหลักฐาน ──────────────────────────────────
def test_advice_stops_blaming_packet_size_once_jumbo_is_on(burst_root):
    """
    เจอจริง: packet size = 8164 + Jumbo เปิด + ไม่มีเฟรมหาย แต่ยังได้ 9.5 fps
    ระบบเดิมยังบอกให้ "เช็ค packet size" = ส่งผู้ใช้ไปแก้ของที่ไม่ได้พัง
    """
    g = hb.diagnose(_meta(saved=190, dropped=0, elapsed=20.0, packet_size=8164))
    assert g["cause"] == "camera_rate"
    assert "packet size" not in g["fix"] or "ตั้งถูกแล้ว" in g["fix"]
    assert "หยุดโมเดล" in g["fix"]


def test_advice_still_blames_packet_size_when_it_is_the_factory_value(burst_root):
    g = hb.diagnose(_meta(saved=165, dropped=0, elapsed=10.0, packet_size=1500))
    assert g["cause"] == "camera_rate"
    assert "1500" in g["fix"]


# ── ⑭ คะแนน "ความสมบูรณ์" ของวัตถุในเฟรม ─────────────────────────
# ยืมเกณฑ์จาก app._can_complete() ของโหมด USB: กรอบต้องไม่แตะขอบเฟรม
# ⚠️ ห้ามตั้งชื่อ W/H — ชนกับขนาดเฟรมทดสอบที่บรรทัดบนสุดของไฟล์
FW, FH = 1000, 800


def test_object_touching_the_edge_scores_zero():
    """โดนขอบตัด = ใช้ตัดสินอะไรไม่ได้ ⇒ 0 เสมอ ไม่ว่าจะคมแค่ไหน"""
    assert hb.completeness([0, 300, 200, 200], FW, FH) == 0.0          # ชิดซ้าย
    assert hb.completeness([800, 300, 200, 200], FW, FH) == 0.0        # ชิดขวา
    assert hb.completeness([400, 0, 200, 200], FW, FH) == 0.0          # ชิดบน
    assert hb.completeness([400, 600, 200, 200], FW, FH) == 0.0        # ชิดล่าง


def test_edge_margin_is_two_percent_like_the_usb_mode():
    """เผื่อขอบ 2% — กรอบที่อยู่ในระยะนั้นถือว่าแตะขอบแล้ว"""
    assert hb.COMPLETE_EDGE_MARGIN == 0.02
    assert hb.completeness([19, 300, 200, 200], FW, FH) == 0.0         # x=19 < 2% ของ 1000
    assert hb.completeness([21, 300, 200, 200], FW, FH) > 0.0          # x=21 > 20 ⇒ ผ่าน


def test_centred_object_beats_an_off_centre_one():
    mid = hb.completeness([400, 300, 200, 200], FW, FH)                # กลางเป๊ะ
    off = hb.completeness([100, 100, 200, 200], FW, FH)                # มุมบนซ้าย
    assert mid > off > 0.0
    assert mid == pytest.approx(1.0, abs=0.01)


def test_partially_entered_object_scores_lower_than_a_whole_one():
    """ยังโผล่ไม่หมด (กรอบเล็กกว่าใบใหญ่สุดของชุดมาก) ⇒ คะแนนต่ำลง"""
    full = 200 * 200
    whole = hb.completeness([400, 300, 200, 200], FW, FH, max_area=full)
    part = hb.completeness([400, 300, 80, 80], FW, FH, max_area=full)
    assert whole > part
    assert part < 0.5


def test_no_object_returns_none_not_zero():
    """
    ⚠️ "หาวัตถุไม่เจอ" ต้องต่างจาก "เจอแล้วแต่ไม่สมบูรณ์" — รวมเป็นค่าเดียว
    คือการเดา (กฎเหล็กข้อ 2) และทำให้เฟรมที่วัดไม่ได้ถูกมองว่าแย่ที่สุด
    """
    assert hb.completeness(None, FW, FH) is None
    assert hb.completeness([], FW, FH) is None
    assert hb.completeness([10, 10, 0, 0], FW, FH) is None
    assert hb.completeness([10, 10, 50, 50], 0, 0) is None


def test_ranking_prefers_a_complete_frame_over_a_sharper_clipped_one():
    """หัวใจของฟีเจอร์: ภาพคมแต่โดนขอบตัด ต้องแพ้ภาพเต็มใบที่คมน้อยกว่า"""
    clipped = {"sharp": 10000.0, "complete": 0.0}
    whole = {"sharp": 4000.0, "complete": 1.0}
    assert hb.rank_key(whole, 10000.0) > hb.rank_key(clipped, 10000.0)


def test_ranking_still_uses_sharpness_among_equally_complete_frames():
    a = {"sharp": 9000.0, "complete": 1.0}
    b = {"sharp": 3000.0, "complete": 1.0}
    assert hb.rank_key(a, 9000.0) > hb.rank_key(b, 9000.0)


def test_unmeasurable_frames_rank_below_known_complete_ones_but_are_not_dropped():
    """วัดไม่ได้ ⇒ ไม่ถูกตัดทิ้ง แต่ต้องอยู่หลังเฟรมที่ 'รู้ว่าสมบูรณ์'"""
    unknown = {"sharp": 10000.0, "complete": None}
    whole = {"sharp": 10000.0, "complete": 1.0}
    clipped = {"sharp": 10000.0, "complete": 0.0}
    # เต็มใบ > วัดไม่ได้ > โดนขอบตัด (วัดไม่ได้ยังมีโอกาสใช้ได้ ต่างจากที่รู้ว่าโดนตัด)
    assert (hb.rank_key(whole, 10000.0)
            > hb.rank_key(unknown, 10000.0)
            > hb.rank_key(clipped, 10000.0))


def test_when_nothing_is_measurable_the_order_falls_back_to_sharpness():
    """ทั้งชุดหาวัตถุไม่เจอ ⇒ ต้องได้ลำดับตามความคมเหมือนเดิม ไม่ใช่ลำดับมั่ว"""
    recs = {"a": {"sharp": 100.0, "complete": None},
            "b": {"sharp": 900.0, "complete": None},
            "c": {"sharp": 500.0, "complete": None}}
    order = sorted(recs, key=lambda k: hb.rank_key(recs[k], 900.0), reverse=True)
    assert order == ["b", "c", "a"]


def test_add_completeness_uses_the_biggest_box_in_the_session_as_whole():
    """ไม่ต้องรู้ขนาดชิ้นงานล่วงหน้า — ใบใหญ่สุดของชุดคือตัวแทนของ 'เต็มใบ'"""
    frames = {
        "1.jpg": {"roi": [400, 300, 200, 200], "roi_src": "moving"},   # เต็มใบ
        "2.jpg": {"roi": [400, 300, 60, 60], "roi_src": "moving"},     # เพิ่งโผล่
        "3.jpg": {"roi": None, "roi_src": "frame"},                    # ไม่เจอวัตถุ
    }
    scored = hb._add_completeness(frames, {"size": "1000x800"})
    assert scored == 2
    assert frames["1.jpg"]["complete"] > frames["2.jpg"]["complete"]
    assert frames["3.jpg"]["complete"] is None


def test_add_completeness_without_a_frame_size_scores_nothing():
    """ไม่รู้ขนาดเฟรม = ตัดสินเรื่องขอบไม่ได้ ⇒ ไม่เดา"""
    frames = {"1.jpg": {"roi": [10, 10, 20, 20], "roi_src": "moving"}}
    assert hb._add_completeness(frames, {}) == 0


def test_end_to_end_a_sharper_clipped_frame_never_beats_a_complete_one(burst_root,
                                                                      tmp_path):
    """
    เคสที่ผู้ใช้ขอมาตรง ๆ: เดิม `top_sharp_files` เรียงตามความคมล้วน ⇒ ภาพที่
    กระป๋องโผล่มาครึ่งใบตรงขอบเฟรม (ซึ่งมักคมกว่าเพราะขอบวัตถุตัดกับฉากหลัง)
    ชนะภาพที่เห็นเต็มใบ. ทดสอบผ่าน `compute_metrics` ตัวจริงทั้งเส้นทาง
    """
    # ⚠️ ต้องมีเฟรมฉากหลังล้วนเป็นส่วนใหญ่ ไม่งั้นค่ามัธยฐาน (= ฉากหลัง) จะกลืน
    # วัตถุที่อยู่ตำแหน่งเดิมซ้ำ ๆ เข้าไปด้วย แล้วหาวัตถุไม่เจอทั้งชุด
    frames = ([_background_scene() for _ in range(6)]
              + [_frame(0), _frame(1), _frame(2)]                    # ชิดขอบ = โดนตัด
              + [_frame(W // 2 - OBJ_W // 2 + k) for k in (-1, 0, 1)])   # กลางเฟรม
    name, _ = make_session(tmp_path, frames)
    m = hb.compute_metrics(name)
    fm = m["frames"]

    empty = [fm["%05d.jpg" % i] for i in range(1, 7)]
    clipped = [fm["%05d.jpg" % i] for i in (7, 8, 9)]
    whole = [fm["%05d.jpg" % i] for i in (10, 11, 12)]
    assert all(e["complete"] is None for e in empty), "ไม่มีวัตถุ = วัดไม่ได้ ไม่ใช่ 0"
    assert all(c["complete"] == 0.0 for c in clipped), "ชิดขอบต้องได้ 0"
    assert all((w["complete"] or 0) > 0.5 for w in whole), "กลางเฟรมต้องได้คะแนนสูง"

    # ใบที่โดนขอบตัด "คมกว่า" จริง (ขอบวัตถุตัดกับฉากหลัง) — แต่ต้องไม่ถูกเลือก
    picked = hb.top_files(name, 3)
    assert set(picked) == {"00010.jpg", "00011.jpg", "00012.jpg"}, (
        "ต้องเลือกเฉพาะใบที่เห็นเต็ม ไม่ใช่ใบที่คมกว่าแต่โดนขอบตัด")


def test_gallery_can_sort_by_completeness_with_unknowns_last(burst_root, tmp_path):
    """
    "วัดไม่ได้" ต้องไปอยู่ท้ายสุด — ไม่ใช่ถูกมองเป็น 0 แล้วปนกับ "รู้ว่าไม่สมบูรณ์"
    (สองอย่างนี้ผู้ตรวจต้องแยกออกจากกันได้)
    """
    frames = ([_background_scene() for _ in range(6)]
              + [_frame(W // 2 - OBJ_W // 2), _frame(0)])
    name, _ = make_session(tmp_path, frames)
    hb.compute_metrics(name)
    rows = hb.session_detail(name, sort="complete")["frames"]
    vals = [r.get("complete") for r in rows]
    known = [v for v in vals if v is not None]
    assert known == sorted(known, reverse=True), "ที่วัดได้ต้องเรียงมาก→น้อย"
    idx_unknown = [i for i, v in enumerate(vals) if v is None]
    if idx_unknown:
        assert min(idx_unknown) >= len(known), "ที่วัดไม่ได้ต้องอยู่ท้ายสุด"


def test_clipped_frames_are_still_ordered_by_sharpness(burst_root):
    """
    🐛 เจอบนสถานี 25 ส.ค.: ทุกเฟรมโดนขอบตัด (complete = 0) ⇒ คะแนนเดิมเป็น 0
    เท่ากันหมด แล้วลำดับกลายเป็น "ตามชื่อไฟล์" — 4 ใบแรกคือ 00037-00040
    เรียงกันเป๊ะ ทั้งที่คะแนนคมคือ 435/1519/1876/1855 (ไม่ได้เรียงเลย)
    """
    recs = {"a": {"sharp": 435.0, "complete": 0.0},
            "b": {"sharp": 1876.0, "complete": 0.0},
            "c": {"sharp": 1519.0, "complete": 0.0}}
    order = sorted(recs, key=lambda k: hb.rank_key(recs[k], 1876.0), reverse=True)
    assert order == ["b", "c", "a"], "โดนขอบตัดเท่ากัน ⇒ ต้องตัดสินด้วยความคม"


def test_completeness_still_outranks_sharpness_after_the_tiebreak_fix(burst_root):
    """กันการแก้ tie-break ไปทับกฎหลัก: สมบูรณ์ต้องมาก่อนความคมเสมอ"""
    clipped_sharp = {"sharp": 99999.0, "complete": 0.0}
    whole_soft = {"sharp": 100.0, "complete": 1.0}
    assert hb.rank_key(whole_soft, 99999.0) > hb.rank_key(clipped_sharp, 99999.0)


# ── ⑮ เทียบผลกับ "ไลน์จริง" ───────────────────────────────────────
# 🐛 เจอบนสถานี 25 ส.ค.: โบกกระป๋องได้ 74 px/วิ แล้วระบบขึ้นไฟเขียว
# "กล้องหยุดการเคลื่อนที่ได้" — จริงที่ 74 px/วิ แต่ไลน์เร็วกว่า 105 เท่า
def _sum_at(speed, exposure, shift=8.0, mean=60.0):
    frames = {("%05d.jpg" % i): {
        "sharp": 1500.0, "roi_src": "moving", "complete": 0.5,
        "speed_px_s": speed, "blur_px": speed * exposure / 1e6,
        "dt_ms": 100.0, "shift_px": shift, "mean": mean, "ratio": 1.1}
        for i in range(1, 12)}
    return hb._summarize(frames, exposure_us=exposure, mm_per_px=None)


def test_a_slow_bench_test_is_not_reported_as_a_line_result(burst_root):
    s = _sum_at(74.0, 2165.0)
    assert s["blur_px_median"] < 1.0, "ที่ความเร็วที่ทดสอบ ภาพคมจริง"
    assert s["line_tested"] is False, (
        "ช้ากว่าไลน์ 105 เท่า ⇒ ห้ามถือว่าตอบคำถามเรื่องไลน์แล้ว")
    assert s["speed_ratio"] < 0.01


def test_blur_at_line_speed_is_reported_even_from_a_slow_test(burst_root):
    """ตัวเลขที่ผู้ใช้ต้องการจริง ๆ — ขึ้นกับ exposure อย่างเดียว จึงตอบได้เสมอ"""
    s = _sum_at(74.0, 2165.0)
    assert s["blur_at_line_px"] == pytest.approx(16.89, abs=0.05)
    assert s["max_exposure_us_at_line"] == pytest.approx(128.2, abs=0.5)


def test_blur_at_line_speed_works_even_when_the_object_never_moved(burst_root):
    """
    ⚠️ ต้องคำนวณนอกเงื่อนไข moving_enough — "exposure นี้จะเบลอกี่ px ที่ไลน์"
    ไม่ต้องรู้ความเร็วของวัตถุที่ทดสอบเลย
    """
    s = _sum_at(74.0, 2165.0, shift=0.3)          # ต่ำกว่าเกณฑ์ 2 px
    assert s["motion"] == "negligible"
    assert s.get("max_exposure_us_1px") is None, "ห้ามคาดจากสัญญาณรบกวน"
    assert s["blur_at_line_px"] == pytest.approx(16.89, abs=0.05)
    assert s["line_tested"] is False


def test_a_real_line_speed_test_is_accepted(burst_root):
    s = _sum_at(7800.0, 500.0, shift=100.0)
    assert s["line_tested"] is True
    assert s["speed_ratio"] == pytest.approx(1.0, abs=0.01)


def test_line_comparison_is_skipped_when_not_configured(burst_root, monkeypatch):
    """ไม่ได้ตั้งความเร็วไลน์ ⇒ ไม่เดา ไม่รายงานอะไรเกี่ยวกับไลน์เลย"""
    monkeypatch.setattr(hb, "_cfg", lambda n, d=None: None if "LINE_SPEED" in n else d)
    s = _sum_at(74.0, 2165.0)
    assert "line_speed_px_s" not in s and "blur_at_line_px" not in s


def test_disk_advice_offers_the_window_mode_only_when_it_is_off(burst_root):
    off = hb.diagnose(_meta(saved=96, dropped=98, elapsed=9.6, size="2448x2048"))
    fix = [i for i in off["issues"] if i["cause"] == "disk"][0]["fix"]
    assert "ต่อช่วงเวลา" in fix, "ยังไม่ได้เปิดโหมดหน้าต่าง ⇒ ต้องเสนอ"

    meta = _meta(saved=96, dropped=98, elapsed=9.6, size="2448x2048")
    meta["window_ms"] = 133
    on = hb.diagnose(meta)
    fix2 = [i for i in on["issues"] if i["cause"] == "disk"][0]["fix"]
    assert "ต่อช่วงเวลา" not in fix2, "เปิดอยู่แล้ว ⇒ ต้องไม่เสนอซ้ำ"
