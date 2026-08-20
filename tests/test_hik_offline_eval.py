"""
เทสต์ของ `hik_offline_eval.py` — เครื่องมือวัดผลโมเดลบนภาพที่เก็บจากไลน์จริง.

หัวใจที่ต้องถูกคือ **การจัดกลุ่มเฟรมเป็น "ใบ"** และ **การตัดสินว่ารันสดจะตามทันไหม**
เพราะนี่คือตัวเลขที่ผู้ใช้จะเอาไปตัดสินใจเรื่องฮาร์ดแวร์ (ทำ triggered mode หรือยัง)
— เครื่องมือวัดที่วัดผิดแย่กว่าไม่มีเครื่องมือ (บทเรียนจาก verify_ocr.py)
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

import hik_offline_eval as ev                                        # noqa: E402


class FakeDetector:
    """ตัดสินจากชื่อไฟล์: d=มีรอยบุบ · c=มีกระป๋องแต่ไม่บุบ · e=ว่าง."""

    model = object()

    def detect(self, img, imgsz=None):
        tag = getattr(img, "_tag", "e")
        if tag == "d":
            return [{"class_name": "can", "confidence": 0.9, "bbox": [0, 0, 9, 9]},
                    {"class_name": "dent", "confidence": 0.77, "bbox": [1, 1, 5, 5]}]
        if tag == "c":
            return [{"class_name": "can", "confidence": 0.9, "bbox": [0, 0, 9, 9]}]
        return []

    def draw_detections(self, img, dets):
        return img


@pytest.fixture
def folder(tmp_path):
    def make(pattern):
        """pattern เช่น "ccdee ccdee" → ไฟล์ 0001_c.jpg ... ตามลำดับ"""
        pattern = pattern.replace(" ", "")
        for i, ch in enumerate(pattern, 1):
            img = np.full((40, 60, 3), 30, dtype=np.uint8)
            cv2.imwrite(str(tmp_path / ("%04d_%s.jpg" % (i, ch))), img)
        return str(tmp_path)
    return make


@pytest.fixture(autouse=True)
def fake_model(monkeypatch):
    monkeypatch.setattr(ev, "load_detector", lambda mode, model: (FakeDetector(), "fake.pt"))
    # ให้ FakeDetector รู้ว่าไฟล์ไหนคืออะไร โดยแนบแท็กตอนอ่านภาพ
    real_imread = cv2.imread

    def tagged_imread(path, *a, **k):
        img = real_imread(path, *a, **k)
        if img is not None:
            arr = np.array(img)                       # ndarray ธรรมดาแนบ attribute ไม่ได้
            base = os.path.basename(path)
            tag = base.split("_")[-1][0] if "_" in base else "e"

            class Tagged(np.ndarray):
                pass

            out = arr.view(Tagged)
            out._tag = tag
            return out
        return img

    monkeypatch.setattr(ev.cv2, "imread", tagged_imread)


def run(argv):
    return ev.main.__wrapped__(argv) if hasattr(ev.main, "__wrapped__") else _run_with(argv)


def _run_with(argv):
    old = sys.argv
    sys.argv = ["hik_offline_eval.py"] + argv
    try:
        return ev.main()
    finally:
        sys.argv = old


def test_missing_folder_exits_2():
    assert _run_with(["/ไม่มีโฟลเดอร์นี้"]) == 2


def test_empty_folder_exits_2(tmp_path):
    assert _run_with([str(tmp_path)]) == 2


def test_groups_consecutive_frames_into_cans(folder, capsys):
    # 3 เฟรมมีกระป๋อง · 2 ว่าง · 3 มีกระป๋อง · 2 ว่าง · 3 มีกระป๋อง = 3 ใบ
    d = folder("ccc ee ccc ee ccc")
    _run_with([d])
    out = capsys.readouterr().out
    assert "จำนวน 'ใบ' ที่นับได้ : 3" in out
    assert "เฟรมต่อใบ          : ต่ำสุด 3 · กลาง 3 · สูงสุด 3" in out


def test_gap_shorter_than_threshold_merges_cans(folder, capsys):
    """ช่องว่างสั้นกว่าเกณฑ์ = ถือเป็นใบเดียวกัน — ตรงกับพฤติกรรมของตัวนับตอนรันสด."""
    d = folder("ccc e ccc")                            # ว่างแค่ 1 เฟรม (< --gap 2)
    _run_with([d])
    out = capsys.readouterr().out
    assert "จำนวน 'ใบ' ที่นับได้ : 1" in out


def test_warns_when_a_can_is_seen_in_only_one_frame(folder, capsys):
    d = folder("c ee ccc ee c")
    _run_with([d])
    out = capsys.readouterr().out
    assert "เห็นแค่ 1 เฟรม" in out


def test_line_rate_and_counting_verdict(folder, capsys):
    """
    เคสจริงที่ผู้ใช้ถาม: ไลน์ 7 ใบ/วิ. ที่ 69 fps ⇒ ~10 เฟรม/ใบ
    คาบ 143 ms/ใบ ซึ่งสั้นกว่าช่องว่างที่ตัวนับต้องการ ⇒ ต้องฟ้องว่านับไม่ได้
    """
    # 5 ใบ ใบละ 8 เฟรม + ว่าง 2 เฟรม = 50 เฟรม ที่ 69 fps ≈ 0.72 วิ ⇒ ~6.9 ใบ/วิ
    # --live-ms 50 = เวลาจริงของ bestX บน iGPU ของสถานี (FakeDetector เร็วเป็นไมโครวินาที
    # จึงต้องบอกเวลาจริง ไม่งั้นข้อสรุปจะมาจากความเร็วของตัวปลอม)
    d = folder(("cccccccc" + "ee") * 5)
    code = _run_with([d, "--fps", "69", "--live-ms", "50"])
    out = capsys.readouterr().out
    assert "จำนวน 'ใบ' ที่นับได้ : 5" in out
    assert "ใบ/วินาที" in out
    assert "การนับตอนรันสดจะรวมหลายใบเป็นใบเดียว" in out
    assert code == 1                                   # มีข้อสังเกต = ไม่ใช่ 0


def test_reports_domain_shift_when_no_defect_found(folder, capsys):
    d = folder("ccc ee ccc")
    _run_with([d])
    out = capsys.readouterr().out
    assert "ไม่พบรอยบุบเลยสักเฟรม" in out


def test_saves_annotated_images_only_for_defects(folder, tmp_path):
    d = folder("cc ee dd ee cc")
    outdir = tmp_path / "ng"
    _run_with([d, "--save-dir", str(outdir)])
    saved = sorted(os.listdir(outdir))
    assert len(saved) == 2                             # เฉพาะเฟรมที่พบรอยบุบ
    assert all("_d" in f for f in saved)


def test_json_output_has_per_frame_rows(folder, tmp_path):
    d = folder("cc ee dd")
    out_json = tmp_path / "r.json"
    _run_with([d, "--json", str(out_json)])
    import json
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert len(data["frames"]) == 6
    assert data["runs"] == [2, 2]
    assert any(r["defects"] for r in data["frames"])


def test_belt_speed_and_motion_blur(folder, capsys):
    """
    เติมตัวเลข P0 ของแผน §12 จากภาพจริง: อัตราใบ/วิ + pitch → ความเร็วสายพาน →
    exposure สูงสุดที่เบลอไม่เกินเกณฑ์ และเบลอจริงที่ exposure ที่ใช้ถ่าย
    """
    d = folder(("cccccccc" + "ee") * 5)                # ~6.9 ใบ/วิ ที่ 69 fps
    code = _run_with([d, "--fps", "69", "--pitch-mm", "100",
                      "--exposure-us", "5000", "--live-ms", "50"])
    out = capsys.readouterr().out
    assert "ความเร็วสายพาน" in out
    # 6.9 ใบ/วิ × 100 mm = ~690 mm/s ⇒ exposure สูงสุดที่เบลอ ≤0.2 mm ≈ 290 µs
    assert "mm/s" in out and "exposure สูงสุด" in out
    assert "เบลอ" in out
    assert "ภาพเบลอจากการเคลื่อนที่" in out            # 5000 µs = เบลอ ~3.4 mm ⇒ ต้องฟ้อง
    assert code == 1


def test_no_blur_warning_when_exposure_is_short_enough(folder, capsys):
    d = folder(("cccccccc" + "ee") * 5)
    _run_with([d, "--fps", "69", "--pitch-mm", "100", "--exposure-us", "200"])
    out = capsys.readouterr().out
    assert "✅ ที่ exposure 200 µs" in out
    assert "ภาพเบลอจากการเคลื่อนที่" not in out
