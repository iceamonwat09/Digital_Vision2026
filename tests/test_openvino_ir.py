"""
ด่านตรวจ "ไฟล์ IR ของ OpenVINO ใช้ได้จริงไหม" + การซ่อมตัวเอง

🐛 ที่มา — สถานี 24-25 ส.ค. 2026: `bestX.pt` (segmentation) มี IR ที่มี
   **output เดียว** ทั้งที่โมเดล segment ต้องมี 2 (กล่อง + prototype mask)
   ⇒ OpenVINO **โหลดผ่าน** แล้วพังตอนตรวจภาพจริงด้วยข้อความ
   `index 1 is out of bounds for dimension 0 with size 1`
   ⇒ ระบบตกไป ONNX/CPU **ถาวร** เพราะลายนิ้วมือถูกจดไว้ก่อน smoke test
   ว่า "IR ตรงกับ .pt" จึงไม่มีการ export ใหม่อีกเลย
   = อาการที่ผู้ใช้เห็น: "ONNX ทำงานเสมอทั้งที่ iGPU ควรใช้ได้"

ตัวเลขในตาราง `_IR_OUTPUTS_BY_TASK` **วัดจริง** ด้วย ultralytics 8.4.41 +
openvino 2024.6.0 (export โมเดล yolov8n ทั้ง 5 task แล้วนับ output ของ IR):
    detect 1 · segment 2 · pose 1 · obb 1 · classify 1
"""
import importlib.machinery
import importlib.util
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _mod(name):
    m = types.ModuleType(name)
    m.__spec__ = importlib.machinery.ModuleSpec(name, loader=None)
    return m


def _stub_ultralytics():
    try:
        if importlib.util.find_spec("ultralytics") is not None:
            return
    except (ImportError, ValueError):
        if "ultralytics" in sys.modules:
            return
    ul = _mod("ultralytics")

    class _YOLO:
        def __init__(self, *a, **k):
            self.names = {}
            self.task = "segment"

    ul.YOLO = _YOLO
    sys.modules["ultralytics"] = ul
    sys.modules["ultralytics.nn"] = _mod("ultralytics.nn")
    tasks = _mod("ultralytics.nn.tasks")
    tasks.DetectionModel = object
    sys.modules["ultralytics.nn.tasks"] = tasks


_stub_ultralytics()
pytest.importorskip("cv2")
import config                                                   # noqa: E402
from yolo_detector import YOLODetector, _IR_OUTPUTS_BY_TASK      # noqa: E402


@pytest.fixture
def det():
    return YOLODetector.__new__(YOLODetector)


@pytest.fixture
def model(tmp_path):
    """โฟลเดอร์จำลอง: bestX.pt + IR ที่มี metadata.yaml ครบ"""
    pt = tmp_path / "bestX.pt"
    pt.write_bytes(b"SEGMENT-WEIGHTS" * 500)
    ov_dir = tmp_path / "bestX_openvino_model"
    ov_dir.mkdir()
    (ov_dir / "bestX.xml").write_text("<net/>")
    (ov_dir / "bestX.bin").write_bytes(b"\x00")
    (ov_dir / "metadata.yaml").write_text("task: segment\nnames: {0: dent, 1: can}\n")
    return str(pt), str(ov_dir), str(ov_dir / "bestX.xml")


def _fake_ir(det, monkeypatch, shapes, task="segment"):
    monkeypatch.setattr(YOLODetector, "_ir_output_shapes", staticmethod(lambda p: shapes))
    monkeypatch.setattr(YOLODetector, "_accel_task", lambda self, a, b: task)


# ── ตารางที่วัดมา ต้องไม่ถูกแก้โดยไม่วัดใหม่ ────────────────────────────────
def test_measured_output_count_per_task_is_locked():
    assert _IR_OUTPUTS_BY_TASK["segment"] == 2, \
        "segment ต้องมี 2 output (กล่อง + prototype mask) — วัดจริงแล้ว"
    for task in ("detect", "pose", "obb", "classify"):
        assert _IR_OUTPUTS_BY_TASK[task] == 1


# ── ตัวตรวจโครงสร้าง IR ─────────────────────────────────────────────────────
def test_segment_ir_with_one_output_is_rejected(det, model, monkeypatch):
    """อาการเป๊ะของสถานี: IR ของ seg มี output เดียว"""
    pt, ov_dir, _ = model
    _fake_ir(det, monkeypatch, ["[?,84,21..]"])
    ok, why = det._validate_ir(pt, ov_dir)
    assert ok is False
    assert "2 output" in why and "1" in why


def test_segment_ir_with_two_outputs_is_accepted(det, model, monkeypatch):
    pt, ov_dir, _ = model
    _fake_ir(det, monkeypatch, ["[?,38,21..]", "[?,32,8..,8..]"])
    assert det._validate_ir(pt, ov_dir)[0] is True


def test_detect_ir_with_one_output_is_accepted(det, model, monkeypatch):
    pt, ov_dir, _ = model
    _fake_ir(det, monkeypatch, ["[?,6,21..]"], task="detect")
    assert det._validate_ir(pt, ov_dir)[0] is True


def test_unreadable_ir_is_not_judged(det, model, monkeypatch):
    """
    เปิดไฟล์ไม่ได้ = **ไม่ตัดสิน** ปล่อยให้ smoke test ชี้ขาด.
    การเดาว่าเสียแล้วลบ IR ที่ยังดีอยู่ทิ้ง เสียหายกว่าการปล่อยผ่าน
    """
    pt, ov_dir, _ = model
    _fake_ir(det, monkeypatch, None)
    assert det._validate_ir(pt, ov_dir)[0] is True


def test_unknown_task_is_not_judged(det, model, monkeypatch):
    pt, ov_dir, _ = model
    _fake_ir(det, monkeypatch, ["[?,84,21..]"], task=None)
    assert det._validate_ir(pt, ov_dir)[0] is True


def test_missing_ir_file_is_a_failure(det, model, monkeypatch):
    pt, ov_dir, xml = model
    os.remove(xml)
    _fake_ir(det, monkeypatch, ["[?,84,21..]"])
    assert det._validate_ir(pt, ov_dir)[0] is False


def test_flag_off_restores_the_old_permissive_behaviour(det, model, monkeypatch):
    pt, ov_dir, _ = model
    _fake_ir(det, monkeypatch, ["[?,84,21..]"])
    monkeypatch.setattr(config, "OPENVINO_VALIDATE_IR", False, raising=False)
    assert det._validate_ir(pt, ov_dir)[0] is True


# ── หัวใจของบั๊ก: ห้ามจดลายนิ้วมือให้ IR ที่ยังไม่ผ่านการตรวจ ─────────────────
def test_a_broken_ir_never_gets_stamped_as_matching(det, model, monkeypatch):
    """
    ถ้าจดลายนิ้วมือให้ IR ที่เสีย ⇒ `_export_is_stale` จะตอบ "ตรงกัน"
    ⇒ ไม่มี export ใหม่อีกเลย ⇒ **ONNX ทำงานเสมอ** (บั๊กที่ผู้ใช้เจอ)
    """
    pt, ov_dir, xml = model
    monkeypatch.setattr(config, "OPENVINO_DEVICE", "intel:gpu", raising=False)
    monkeypatch.setattr(config, "OPENVINO_AUTO_REPAIR", False, raising=False)
    monkeypatch.setattr(YOLODetector, "_openvino_device_status",
                        staticmethod(lambda d: (True, "ok")))
    _fake_ir(det, monkeypatch, ["[?,84,21..]"])
    det._accel_skips = []
    det.backend_downgraded = False

    assert det._maybe_openvino(pt) is None            # ต้องไม่ยอมใช้ IR ที่เสีย
    assert not os.path.exists(xml + ".src"), "ห้ามปล่อยให้ IR ที่เสียถือลายนิ้วมือ 'ตรงกับ .pt'"
    # ⇒ รอบหน้าต้อง **บังคับ export ใหม่** ไม่ใช่โหลด IR ตัวเดิมซ้ำ
    assert det._ir_retry_pending(ov_dir, det._weights_hash(pt)) is True


def test_a_good_ir_is_stamped_and_used(det, model, monkeypatch):
    pt, ov_dir, xml = model
    monkeypatch.setattr(config, "OPENVINO_DEVICE", "intel:gpu", raising=False)
    monkeypatch.setattr(YOLODetector, "_openvino_device_status",
                        staticmethod(lambda d: (True, "ok")))
    _fake_ir(det, monkeypatch, ["[?,38,21..]", "[?,32,8..,8..]"])
    det._accel_skips = []
    assert det._maybe_openvino(pt) == ov_dir
    assert os.path.exists(xml + ".src")


# ── เครื่องหมาย "เคยพังแล้ว" — ลองซ่อมได้ 1 รอบ ไม่ใช่ลองไม่รู้จบ ─────────────
def test_first_failure_still_allows_one_more_attempt(det, model):
    pt, ov_dir, _ = model
    digest = det._weights_hash(pt)
    det._write_bad_marker(ov_dir, digest, 1, "ครั้งแรก")
    assert det._ir_given_up(ov_dir, digest) == ""


def test_second_failure_stops_re_exporting_every_boot(det, model):
    pt, ov_dir, _ = model
    digest = det._weights_hash(pt)
    det._write_bad_marker(ov_dir, digest, 2, "พังซ้ำ")
    assert "พังซ้ำ" in det._ir_given_up(ov_dir, digest)


def test_a_new_model_file_gets_a_fresh_chance(det, model):
    """เทรนใหม่/เปลี่ยนไฟล์ = เนื้อเปลี่ยน ⇒ ประวัติเก่าต้องไม่ปิดโอกาส"""
    pt, ov_dir, _ = model
    det._write_bad_marker(ov_dir, "OLD-DIGEST", 2, "ของเก่า")
    assert det._ir_given_up(ov_dir, det._weights_hash(pt)) == ""


def test_giving_up_skips_openvino_without_exporting(det, model, monkeypatch):
    pt, ov_dir, _ = model
    monkeypatch.setattr(config, "OPENVINO_DEVICE", "intel:gpu", raising=False)
    monkeypatch.setattr(YOLODetector, "_openvino_device_status",
                        staticmethod(lambda d: (True, "ok")))
    det._write_bad_marker(ov_dir, det._weights_hash(pt), 2, "IR ซ่อมไม่ขึ้น")
    det._accel_skips = []
    calls = []
    monkeypatch.setattr("yolo_detector.YOLO", lambda *a, **k: calls.append(a))
    assert det._maybe_openvino(pt) is None
    assert calls == [], "เคยยอมแพ้แล้วต้องไม่ export ซ้ำทุกครั้งที่เปิดโปรแกรม"


# ── smoke test ล้ม → เตรียม export ใหม่รอบหน้า (ไม่ค้างอยู่กับ IR ที่เสีย) ────
def test_smoke_failure_clears_the_stamp_so_the_next_boot_re_exports(det, model, monkeypatch):
    pt, ov_dir, xml = model
    monkeypatch.setattr(config, "OPENVINO_AUTO_REPAIR", True, raising=False)
    det._remember_hash(xml, det._weights_hash(pt))
    assert det._ir_retry_pending(ov_dir, det._weights_hash(pt)) is False
    det._quarantine_ir(pt, ov_dir, "index 1 is out of bounds")
    assert not os.path.exists(xml + ".src")
    assert det._ir_retry_pending(ov_dir, det._weights_hash(pt)) is True
    assert "index 1" in det._read_bad_marker(ov_dir)[2]


def test_quarantine_does_nothing_when_auto_repair_is_off(det, model, monkeypatch):
    pt, ov_dir, xml = model
    monkeypatch.setattr(config, "OPENVINO_AUTO_REPAIR", False, raising=False)
    det._remember_hash(xml, det._weights_hash(pt))
    det._quarantine_ir(pt, ov_dir, "boom")
    assert os.path.exists(xml + ".src")                  # เหมือนเดิมทุกประการ
    assert det._read_bad_marker(ov_dir)[0] is None


def test_two_smoke_failures_in_a_row_stop_the_retry_loop(det, model):
    pt, ov_dir, xml = model
    det._quarantine_ir(pt, ov_dir, "รอบแรก")
    det._remember_hash(xml, det._weights_hash(pt))       # export ใหม่รอบถัดไป
    det._quarantine_ir(pt, ov_dir, "รอบสอง")
    assert det._ir_given_up(ov_dir, det._weights_hash(pt)) != ""


# ── เส้นทางซ่อมจาก ONNX ────────────────────────────────────────────────────
def test_repair_refuses_without_metadata_because_class_names_would_vanish(det, model):
    """
    วัดจริง: IR ที่ไม่มี `metadata.yaml` ทำให้ ultralytics ตั้งชื่อคลาสเป็น
    `class0/class1` **โดยไม่มี error** ⇒ ผลตรวจกลายเป็นชื่อผิดแบบเงียบ ๆ
    (กฎเหล็กข้อ 2) ⇒ ไม่มีไฟล์นี้ = ไม่ซ่อม
    """
    pt, ov_dir, _ = model
    os.remove(os.path.join(ov_dir, "metadata.yaml"))
    ok, why = det._repair_ir_from_onnx(pt, ov_dir)
    assert ok is False and "metadata.yaml" in why


def test_repair_reports_when_there_is_no_onnx_to_build_from(det, model, monkeypatch):
    pytest.importorskip("yaml")
    pt, ov_dir, _ = model
    monkeypatch.setattr(YOLODetector, "_sync_metadata_names",
                        staticmethod(lambda a, b: (True, "ok")))
    monkeypatch.setattr(YOLODetector, "_ensure_onnx_file", lambda self, p: None)
    ok, why = det._repair_ir_from_onnx(pt, ov_dir)
    assert ok is False and ".onnx" in why


def test_ensure_onnx_reuses_a_matching_file_without_exporting(det, tmp_path, monkeypatch):
    pt = tmp_path / "bestX.pt"
    pt.write_bytes(b"W" * 100)
    onnx = tmp_path / "bestX.onnx"
    onnx.write_text("graph")
    det._remember_hash(str(onnx), det._weights_hash(str(pt)))
    calls = []
    monkeypatch.setattr("yolo_detector.YOLO", lambda *a, **k: calls.append(a))
    assert det._ensure_onnx_file(str(pt)) == str(onnx)
    assert calls == []


def test_ov_paths_match_the_layout_the_exporter_writes(det):
    ov_dir, xml = det._ov_paths(os.path.join("weights", "can_dent", "bestX.pt"))
    assert ov_dir.endswith(os.path.join("can_dent", "bestX_openvino_model"))
    assert xml.endswith(os.path.join("bestX_openvino_model", "bestX.xml"))


# ── ชื่อคลาสใน metadata.yaml ต้องเป็นของ .pt ตัวนี้จริง ๆ ──────────────────
def _yolo_stub(monkeypatch, names, task="segment"):
    class _Y:
        def __init__(self, *a, **k):
            self.names = names
            self.task = task
    monkeypatch.setattr("yolo_detector.YOLO", _Y)


def test_repair_rewrites_class_names_that_belong_to_another_model(det, model, monkeypatch):
    """
    โฟลเดอร์ IR ที่เสียมักเป็นของ **โมเดลตัวอื่น** ⇒ metadata.yaml ก็เป็นของตัวอื่น.
    ซ่อมแต่ .xml/.bin แล้วปล่อยชื่อคลาสเดิม = ตรวจถูกแต่ **เรียกชื่อผิด** เงียบ ๆ
    """
    yaml = pytest.importorskip("yaml")
    pt, ov_dir, _ = model
    meta = os.path.join(ov_dir, "metadata.yaml")
    with open(meta, "w", encoding="utf-8") as f:
        f.write("task: detect\nnames: {0: person, 1: bicycle}\n")
    _yolo_stub(monkeypatch, {0: "dent", 1: "can"})
    ok, why = det._sync_metadata_names(pt, meta)
    assert ok is True
    md = yaml.safe_load(open(meta, encoding="utf-8"))
    assert md["names"] == {0: "dent", 1: "can"} and md["task"] == "segment"


def test_matching_metadata_is_left_untouched(det, model, monkeypatch):
    pytest.importorskip("yaml")
    pt, ov_dir, _ = model
    meta = os.path.join(ov_dir, "metadata.yaml")
    before = open(meta, encoding="utf-8").read()
    _yolo_stub(monkeypatch, {0: "dent", 1: "can"})
    assert det._sync_metadata_names(pt, meta)[0] is True
    assert open(meta, encoding="utf-8").read() == before


def test_repair_refuses_when_the_pt_class_names_cannot_be_read(det, model, monkeypatch):
    """อ่านชื่อคลาสไม่ได้ = **ไม่เดา ไม่ซ่อม** (ดีกว่าซ่อมแล้วชื่อผิด)"""
    pt, ov_dir, _ = model

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("โหลด .pt ไม่ได้")
    monkeypatch.setattr("yolo_detector.YOLO", _Boom)
    ok, why = det._repair_ir_from_onnx(pt, ov_dir)
    assert ok is False and "ชื่อคลาส" in why
