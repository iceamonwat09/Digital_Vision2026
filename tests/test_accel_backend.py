"""
ตัวเร่งความเร็วของโมเดล: ตัวตรวจ "export ล้าสมัยไหม" และการรายงาน backend

🐛 ที่มา — สถานี 25 ส.ค. 2026: ผู้ใช้เผลอลบ `bestX.pt` แล้วกู้ **ไฟล์เดิม** กลับมา
   ⇒ mtime กลายเป็น "ตอนนี้" ⇒ ใหม่กว่า OpenVINO IR ⇒ ตัวตรวจแบบ mtime สั่ง
   re-export ทุกครั้งที่เปิดโปรแกรม. พอ export ล้มเหลว ระบบตกไปวิ่งบน CPU
   **อย่างเงียบ ๆ** — `inf` จาก 45-50 ms กลายเป็น 155-378 ms โดยไม่มีใครรู้สาเหตุ
"""
import importlib.machinery
import importlib.util
import os
import sys
import time
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _mod(name):
    """
    สร้างโมดูลปลอมที่มี ``__spec__`` ถูกต้อง.

    ⚠️ โมดูลปลอมที่ ``__spec__ is None`` ทำให้ ``importlib.util.find_spec()``
    **โยน ValueError** ⇒ ไฟล์เทสต์อื่นที่ใช้ find_spec เพื่อดูว่ามีของจริงไหม
    (เช่น `test_hik_routes.py`) จะพังทั้งไฟล์ทันทีที่ไฟล์นี้ถูก import ก่อน
    """
    m = types.ModuleType(name)
    m.__spec__ = importlib.machinery.ModuleSpec(name, loader=None)
    return m


def _stub_ultralytics():
    """ใส่ stub เฉพาะเมื่อเครื่องนั้นไม่มีของจริง — บนสถานีจะใช้ของจริงเสมอ"""
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
from yolo_detector import YOLODetector                          # noqa: E402


@pytest.fixture
def pair(tmp_path):
    """คู่ (.pt, ไฟล์ export) ที่ตรงกันอยู่แล้ว"""
    pt = tmp_path / "bestX.pt"
    exp = tmp_path / "bestX.onnx"
    pt.write_bytes(b"WEIGHTS-v1" * 1000)
    exp.write_text("export")
    det = YOLODetector.__new__(YOLODetector)
    det._remember_hash(str(exp), det._weights_hash(str(pt)))
    return det, str(pt), str(exp)


def test_matching_export_is_not_stale(pair):
    det, pt, exp = pair
    assert det._export_is_stale(pt, exp) is False


def test_restoring_the_same_file_does_not_force_a_re_export(pair):
    """
    ⚠️ เคสที่ทำให้ระบบช้าลง 6 เท่าบนสถานีจริง — กู้ไฟล์ **เดิม** กลับมา
    mtime ใหม่กว่า export แต่ **เนื้อไฟล์เหมือนเดิม** ⇒ export เดิมยังใช้ได้
    """
    det, pt, exp = pair
    data = open(pt, "rb").read()
    os.remove(pt)
    time.sleep(0.02)
    with open(pt, "wb") as f:
        f.write(data)
    os.utime(pt, None)

    assert os.path.getmtime(pt) > os.path.getmtime(exp), "ต้องจำลองว่า .pt ใหม่กว่าจริง"
    assert det._export_is_stale(pt, exp) is False, (
        "ไฟล์เนื้อเดิม = โมเดลตัวเดิม ⇒ ห้ามสั่ง export ใหม่")


def test_a_retrained_model_is_still_detected_as_stale(pair):
    """กลับด้าน: เทรนใหม่จริง (เนื้อเปลี่ยน) ต้อง export ใหม่ ไม่งั้นวิ่งโมเดลเก่า"""
    det, pt, exp = pair
    with open(pt, "wb") as f:
        f.write(b"WEIGHTS-v2" * 1000)
    assert det._export_is_stale(pt, exp) is True


def test_missing_export_is_stale(pair, tmp_path):
    det, pt, _ = pair
    assert det._export_is_stale(pt, str(tmp_path / "ไม่มีอยู่.onnx")) is True


def test_upgrading_from_a_version_without_the_sidecar_does_not_re_export(pair):
    """
    เครื่องที่อัปเกรดมาจากเวอร์ชันก่อนหน้าจะยังไม่มี sidecar — ถ้า export
    ใหม่กว่า .pt อยู่แล้วก็แปลว่ายังตรงกัน ⇒ อย่า export ใหม่ให้เสียเวลาฟรี ๆ
    (และจด sidecar ไว้ให้เลย)
    """
    det, pt, exp = pair
    os.remove(exp + ".src")
    os.utime(exp, None)
    assert det._export_is_stale(pt, exp) is False
    assert os.path.exists(exp + ".src"), "ต้องจดลายนิ้วมือไว้ให้ครั้งต่อไป"


def test_unreadable_weights_fall_back_to_mtime(tmp_path):
    """อ่าน .pt ไม่ได้ ⇒ ถอยไปใช้เกณฑ์เดิม ไม่ใช่พังหรือเดา"""
    det = YOLODetector.__new__(YOLODetector)
    assert det._weights_hash(str(tmp_path / "ไม่มี.pt")) is None
    assert det._export_is_stale(str(tmp_path / "ไม่มี.pt"),
                               str(tmp_path / "ก็ไม่มี.onnx")) is True


def test_detector_reports_which_backend_is_actually_running():
    """
    ต้องรู้ได้จาก object ว่า backend ไหนทำงานอยู่ — ไม่ใช่ต้องไปไล่อ่าน log
    (นี่คือสิ่งที่ทำให้ 'ระบบช้าลง 6 เท่า' หาสาเหตุไม่เจออยู่หลายวัน)
    """
    det = YOLODetector(model_path="ไม่มีไฟล์นี้.pt")
    assert det.backend_label == "PyTorch"          # ค่าตั้งต้นก่อนโหลด
    assert det.backend_downgraded is False
    assert det.backend_note == ""


# ── ธง "ได้ backend ที่ช้ากว่าที่ตั้งไว้" ────────────────────────────
# ⚠️ เส้นทางที่พบบ่อยที่สุดคือ _maybe_openvino/_maybe_onnx คืน None ตั้งแต่ต้น
# (import ไม่ได้ / ไม่พบอุปกรณ์ / export ล้ม) ซึ่ง **ไม่ผ่าน except ของลูปเลย**
# ⇒ ถ้าดูแค่ลูปจะรายงานว่า "ปกติดี" ทั้งที่ตกไป CPU แล้ว
def _det():
    d = YOLODetector.__new__(YOLODetector)
    d.backend_downgraded = False
    d.backend_note = ""
    d._accel_skips = []
    return d


def test_falling_back_to_pytorch_when_openvino_was_requested_is_flagged(monkeypatch):
    import config as appcfg
    monkeypatch.setattr(appcfg, "OPENVINO_DEVICE", "intel:gpu", raising=False)
    monkeypatch.setattr(appcfg, "USE_ONNX", True, raising=False)
    d = _det()
    d._note_skip("OpenVINO@intel:gpu", "ไม่พบอุปกรณ์นี้ในเครื่อง")
    d._check_downgraded("")                      # ลงเอยที่ PyTorch
    assert d.backend_downgraded is True
    assert "ไม่พบอุปกรณ์" in d.backend_note, "ต้องบอกเหตุผล ไม่ใช่แค่บอกว่าช้า"


def test_getting_the_backend_that_was_asked_for_is_not_flagged(monkeypatch):
    import config as appcfg
    monkeypatch.setattr(appcfg, "OPENVINO_DEVICE", "intel:gpu", raising=False)
    d = _det()
    d._check_downgraded("OpenVINO")
    assert d.backend_downgraded is False
    assert d.backend_note == ""


def test_onnx_is_acceptable_when_only_onnx_was_requested(monkeypatch):
    import config as appcfg
    monkeypatch.setattr(appcfg, "OPENVINO_DEVICE", None, raising=False)
    monkeypatch.setattr(appcfg, "USE_OPENVINO", False, raising=False)
    monkeypatch.setattr(appcfg, "USE_ONNX", True, raising=False)
    d = _det()
    d._check_downgraded("ONNX")
    assert d.backend_downgraded is False


def test_plain_pytorch_setup_is_not_flagged(monkeypatch):
    """ไม่ได้ขอตัวเร่งเลย ⇒ PyTorch คือสิ่งที่ตั้งใจ ไม่ใช่การถดถอย"""
    import config as appcfg
    monkeypatch.setattr(appcfg, "OPENVINO_DEVICE", None, raising=False)
    monkeypatch.setattr(appcfg, "USE_OPENVINO", False, raising=False)
    monkeypatch.setattr(appcfg, "USE_ONNX", False, raising=False)
    d = _det()
    d._check_downgraded("")
    assert d.backend_downgraded is False


# ───────────────────────────────────────────────────────────────────────────
# `_openvino_device_status` — 3 ต้นเหตุที่หน้าตาเหมือนกันบนจอ แต่แก้คนละวิธี
#
# 🐛 ที่มา — สถานี 25 ส.ค. 2026: แถบ perf ขึ้น `ONNX 🐢` แต่เหตุผลที่บันทึกไว้
#    เป็นข้อความเดียวตายตัว "ไม่พบอุปกรณ์นี้ในเครื่อง" ทั้งที่ต้นเหตุอาจเป็น
#    "ไม่ได้ลง openvino" (แก้ด้วย pip) หรือ "ลงแล้วแต่ไดรเวอร์ไม่โผล่ GPU"
#    (แก้ที่ไดรเวอร์) — คนละงานกันคนละวัน. บอกรวม ๆ = ส่งผู้ใช้ไปแก้ของที่ไม่พัง
# ───────────────────────────────────────────────────────────────────────────
def _fake_openvino(devices, version="2024.6.0"):
    ov = _mod("openvino")
    ov.__version__ = version

    class _Core:
        available_devices = list(devices)

        def get_property(self, d, k):
            return "Fake %s" % d

    ov.Core = _Core
    return ov


def test_status_says_package_missing_when_import_fails(monkeypatch):
    monkeypatch.setitem(sys.modules, "openvino", None)   # import openvino → ImportError
    ok, why = YOLODetector._openvino_device_status("intel:gpu")
    assert ok is False
    assert "openvino" in why and "pip install" in why, why
    assert "ไดรเวอร์" not in why, "แพ็กเกจหาย ≠ ไดรเวอร์มีปัญหา — ห้ามแนะนำผิดทาง"


def test_status_blames_the_driver_when_package_is_there_but_gpu_is_not(monkeypatch):
    monkeypatch.setitem(sys.modules, "openvino", _fake_openvino(["CPU"]))
    ok, why = YOLODetector._openvino_device_status("intel:gpu")
    assert ok is False
    assert "2024.6.0" in why, "ต้องบอกว่ารุ่นไหนที่ติดตั้งอยู่ ไม่งั้นเดาต่อไม่ได้"
    assert "GPU" in why and "CPU" in why, "ต้องบอกทั้งตัวที่ขอและตัวที่เห็นจริง"
    assert "pip install" not in why, "ลงแล้ว — สั่งให้ลงซ้ำคือคำแนะนำที่ผิด"


def test_status_accepts_enumerated_gpu_names(monkeypatch):
    """OpenVINO เรียก iGPU ว่า `GPU.0` เมื่อมีการ์ดหลายตัว — ต้องนับว่าใช้ได้"""
    monkeypatch.setitem(sys.modules, "openvino", _fake_openvino(["CPU", "GPU.0", "GPU.1"]))
    ok, why = YOLODetector._openvino_device_status("intel:gpu")
    assert ok is True, why


def test_status_reports_a_probe_that_blows_up(monkeypatch):
    ov = _fake_openvino(["GPU"])

    class _Boom:
        def __init__(self):
            raise RuntimeError("core init failed")

    ov.Core = _Boom
    monkeypatch.setitem(sys.modules, "openvino", ov)
    ok, why = YOLODetector._openvino_device_status("intel:gpu")
    assert ok is False
    assert "core init failed" in why, "ข้อความจริงของ error ต้องไปถึงผู้ใช้"


def test_skip_reason_reaches_the_badge(monkeypatch, tmp_path):
    """
    ด่านตรวจอุปกรณ์ต้องส่ง **เหตุผลจริง** เข้า `backend_note` ไม่ใช่ข้อความตายตัว
    (นี่คือสิ่งที่ผู้ใช้เห็นบนแถบ perf — ถ้าตายตัวก็ไล่ต่อไม่ได้)
    """
    import config as appcfg
    monkeypatch.setattr(appcfg, "OPENVINO_DEVICE", "intel:gpu", raising=False)
    monkeypatch.setitem(sys.modules, "openvino", _fake_openvino(["CPU"]))
    pt = tmp_path / "bestX.pt"
    pt.write_bytes(b"x" * 16)
    d = _det()
    assert d._maybe_openvino(str(pt)) is None
    d._check_downgraded("ONNX")
    assert d.backend_downgraded is True
    assert "ไม่เห็นอุปกรณ์" in d.backend_note, d.backend_note
