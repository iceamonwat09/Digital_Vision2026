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
                                           "HIK_BURST_MM_PER_PX": None}.get(n, d))
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
