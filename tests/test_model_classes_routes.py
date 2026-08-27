"""
ระดับ HTTP: หน้าเว็บต้อง "เห็น" ว่าโมเดลที่เลือกมีคลาสอะไร และตัวไหนเป็นตำหนิ

⚠️ ผู้ใช้เลือกไฟล์ ``.pt`` เองจากหน้าเว็บ และตรรกะไม่ผูกกับชื่อไฟล์แล้ว ⇒
ถ้าโมเดลมีคลาสที่ระบบไม่รู้จัก ผลตรวจจะเปลี่ยนความหมายไปเงียบ ๆ
(คลาสนั้นถูกนับเป็น NG) — ข้อมูลนี้จึงต้องไปถึงหน้าจอ ไม่ใช่จมอยู่ใน log.
"""

import os
import sys

import pytest

# ใช้ bootstrap ตัวเดียวกับเทสต์ route ชุดเดิม (stub ultralytics/pyodbc/jwt)
from tests.test_hik_routes import appmod                       # noqa: E402

import yolo_detector as yd                                     # noqa: E402
from modes import can_dent as can_dent_mode                    # noqa: E402
from tests.test_model_classes import FakeModel, BESTX_NAMES    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture
def client(monkeypatch):
    try:
        from auth import access as _ac
        monkeypatch.setattr(_ac.ac, "AUTH_ENABLED", False, raising=False)
    except Exception:                                          # pragma: no cover
        pass
    appmod.app.config["TESTING"] = True
    with appmod.app.test_client() as c:
        yield c


def _install(monkeypatch, names, path="weights/can_dent/whatever.pt"):
    det = yd.YOLODetector(model_path=path, mode_config=can_dent_mode)
    det.model = FakeModel(names)
    det._build_class_roles()
    monkeypatch.setattr(appmod, "detector", det, raising=False)
    return det


def test_status_reports_the_class_roles(client, monkeypatch):
    _install(monkeypatch, BESTX_NAMES)
    d = client.get("/api/detection/status").get_json()
    got = {c["name"]: c["role"] for c in d["classes"]["items"]}
    assert got == {"dent": "defect", "can": "body"}
    assert d["classes"]["warnings"] == []


def test_status_carries_the_unknown_class_warning(client, monkeypatch):
    _install(monkeypatch, {0: "dent", 1: "can", 2: "scratch"})
    d = client.get("/api/detection/status").get_json()
    assert any("scratch" in w for w in d["classes"]["warnings"])
    roles = {c["name"]: c["role"] for c in d["classes"]["items"]}
    assert roles["scratch"] == "defect"


def test_status_survives_a_detector_without_a_model(client, monkeypatch):
    det = yd.YOLODetector(model_path="x.pt", mode_config=can_dent_mode)
    monkeypatch.setattr(appmod, "detector", det, raising=False)
    d = client.get("/api/detection/status").get_json()
    assert d["classes"] == {"items": [], "warnings": []}


def test_non_defect_set_follows_the_loaded_mode(monkeypatch):
    """``app._non_defect_classes()`` ต้องอ่านจากโหมด ไม่ใช่ค่าคงที่ในไฟล์."""
    class _Mode:
        NON_DEFECT_CLASSES = {"body"}
        CLASS_NAMES = {"body": "ตัวถัง"}
        COLORS = {}
        VERDICT_BADGE = True

    det = yd.YOLODetector(model_path="x.pt", mode_config=_Mode)
    monkeypatch.setattr(appmod, "detector", det, raising=False)
    assert appmod._non_defect_classes() == {"body"}


def test_non_defect_set_falls_back_when_there_is_no_detector(monkeypatch):
    monkeypatch.setattr(appmod, "detector", None, raising=False)
    assert appmod._non_defect_classes() == {"good", "can"}
