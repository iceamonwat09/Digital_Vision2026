"""
เทสต์ของ `diagnose_hikrobot.py` — รันสคริปต์ตัวจริงกับ **MVS SDK ปลอม**
(`tests/fake_mvs/`) เพื่อพิสูจน์ 2 อย่าง:

  1. ทางที่ทุกอย่างปกติ → ผ่าน (exit 0) และรายงานค่าที่โหมดใหม่ต้องใช้ครบ
  2. **ทางที่มีปัญหาจริง → จับได้** (exit ≠ 0 พร้อมบอกวิธีแก้)

ข้อ 2 สำคัญกว่าข้อ 1: เครื่องมือวินิจฉัยที่ "ผ่านเสมอ" ไม่มีค่าอะไรเลย — บทเรียนเดียวกับ
`verify_artwork_features.py` ที่ต้องพิสูจน์ว่ารันบน commit เก่าแล้วไม่ผ่านจริง.

ไม่ต้องมีกล้อง / ไม่ต้องมี MVS / ไม่ต้องมี Windows.
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "diagnose_hikrobot.py")
FAKE_SDK = os.path.join(ROOT, "tests", "fake_mvs")

# ถ้าเครื่องที่รันเทสต์มี MVS จริง (สถานี Windows) เทสต์ "ไม่มี SDK" จะเจอของจริงแทน
HAS_REAL_MVS = any(os.path.isdir(p) for p in
                   (r"C:\Program Files (x86)\MVS", r"C:\Program Files\MVS"))


def run(args, sim=None):
    env = dict(os.environ)
    if sim is not None:
        env["FAKE_MVS_SIM"] = json.dumps(sim)
    else:
        env.pop("FAKE_MVS_SIM", None)
    p = subprocess.run([sys.executable, SCRIPT] + args, cwd=ROOT, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.returncode, p.stdout.decode("utf-8", "replace")


def run_fake(extra=None, sim=None):
    return run(["--sdk-path", FAKE_SDK, "--frames", "3"] + (extra or []), sim)


# ── ทางที่ปกติ ────────────────────────────────────────────
def test_healthy_camera_passes():
    code, out = run_fake()
    assert code == 0, out
    assert "MV-CS050-10GC" in out
    assert "DA4994130" in out
    # ภาพจาก SDK ปลอมมืด (เป็นแค่ "ข้อสังเกต") — แต่ต้องไม่มีข้อที่ "ต้องแก้"
    assert "ต้องแก้" not in out


def test_reports_values_the_new_mode_needs():
    """ค่าที่ UI ของโหมดใหม่ต้องเอาไปทำ min/max ต้องอยู่ในรายงานครบ."""
    code, out = run_fake()
    assert code == 0, out
    for key in ("ExposureTime", "Gain", "PixelFormat", "Width", "Height",
                "TriggerMode", "TriggerSource", "GevSCPSPacketSize",
                "AcquisitionFrameRate", "ResultingFrameRate"):
        assert key in out, "รายงานไม่มี %s" % key
    assert "15 .. 40279" in out          # ช่วง exposure ที่ตั้งได้จริง
    assert "BayerRG8" in out             # ต้องแปลชื่อ pixel format ไม่ใช่พ่นเลขดิบ


def test_json_output(tmp_path):
    out_file = tmp_path / "hik.json"
    code, out = run_fake(["--json", str(out_file)])
    assert code == 0, out
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["target_serial"] == "DA4994130"
    assert data["params"]["ExposureTime"]["max"] == pytest.approx(40279, rel=1e-3)
    assert data["grab"]["got"] == 3
    assert data["problems"] == []


def test_list_only_does_not_open_camera():
    code, out = run_fake(["--list-only"])
    assert code == 0, out
    assert "เปิดกล้องสำเร็จ" not in out


# ── ทางที่มีปัญหา — ต้องจับได้ ──────────────────────────────
def test_camera_held_by_mvs_is_reported():
    """อาการที่จะเจอบ่อยที่สุดหน้างาน: ลืมปิดโปรแกรม MVS แล้วแอปเปิดกล้องไม่ได้."""
    code, out = run_fake(sim={"accessible": False, "open_ok": False})
    assert code != 0
    assert "MVS" in out and "เปิดกล้องไม่สำเร็จ" in out


def test_small_packet_size_is_flagged():
    """packet size เล็ก = ไม่ได้เปิด Jumbo frame → 5MP@24fps จะแพ็กเก็ตหาย."""
    code, out = run_fake(sim={"packet_size": 1500, "optimal_packet_size": 8164})
    assert code == 1
    assert "Jumbo" in out


def test_lost_packets_are_flagged():
    code, out = run_fake(sim={"lost_packets": 12, "lost_frames": 2})
    assert code == 1
    assert "แพ็กเก็ต/เฟรมหายจริง" in out


def test_dropped_frames_are_flagged():
    """เลขเฟรมกระโดด = ภาพหายกลางทาง — ห้ามเงียบ (จะกลายเป็นผลตรวจที่ขาดใบ)."""
    code, out = run_fake(["--frames", "12"], sim={"drop_every": 3})
    assert code == 1
    assert "เลขเฟรมกระโดด" in out


def test_dark_image_warns_about_lighting():
    """โจทย์จริงของงานนี้: exposure สั้นแล้วภาพมืด = ไฟไม่พอ ต้องบอกก่อนไปเสียเวลาจูนโมเดล."""
    code, out = run_fake()
    assert "ภาพมืด" in out               # ภาพจาก SDK ปลอมมืดโดยธรรมชาติ
    assert "150-200" in out


@pytest.mark.skipif(HAS_REAL_MVS, reason="เครื่องนี้มี MVS จริง — เทสต์นี้ใช้ได้เฉพาะเครื่องที่ไม่มี")
def test_missing_sdk_exits_2_with_instructions(tmp_path):
    """ไม่มี SDK ต้อง 'บอกวิธีแก้' ไม่ใช่ traceback."""
    code, out = run(["--sdk-path", str(tmp_path)])
    assert code == 2
    assert "MvImport" in out and "วิธีแก้" in out
    assert "Traceback" not in out


def test_identity_is_read_from_camera_nodes():
    """
    ตัวตนกล้องต้องมาจาก **node ของกล้องเอง** ไม่ใช่แค่ struct ของ SDK.
    (บนสถานีจริง 19 ส.ค. 2026 struct อ่านไม่ออก → รายงานขึ้น '?' ทั้งแถวโดยไม่บอกเหตุผล)
    """
    code, out = run_fake()
    assert code == 0, out
    assert "รุ่น" in out and "MV-CS050-10GC" in out
    assert "ซีเรียล" in out and "DA4994130" in out


def test_struct_parse_error_is_never_silent(monkeypatch):
    """ถ้าอ่าน struct ไม่ได้ ต้องพิมพ์เหตุผลออกมา ไม่ใช่ปล่อยให้ขึ้น '?' เฉย ๆ."""
    src = os.path.join(FAKE_SDK, "MvCameraControl_class.py")
    text = open(src, encoding="utf-8").read()
    assert 'd["error"] = "%s: %s"' in open(
        os.path.join(ROOT, "diagnose_hikrobot.py"), encoding="utf-8").read()
    assert "อ่านข้อมูลจาก struct ของ SDK ไม่ได้" in open(
        os.path.join(ROOT, "diagnose_hikrobot.py"), encoding="utf-8").read()
    assert "SpecialInfo" in text          # ตัวปลอมยังคงรูปแบบเดียวกับของจริง


def test_set_packet_flags_nic_without_jumbo_frames():
    """
    เคสจริงบนสถานี: packet size ค้างที่ 1500 = ยังไม่ได้เปิด Jumbo Frame.
    ถ้าสั่งตั้งเป็น 8164 แล้วกล้องยอมรับได้แค่ค่าเดิม ต้องฟ้องว่าเป็นเรื่องของการ์ดแลน.
    """
    code, out = run_fake(["--set-packet"],
                         sim={"packet_size": 1500, "optimal_packet_size": 8164,
                              "max_packet_size": 1500})
    assert code == 1
    assert "การ์ดแลนยังไม่ได้เปิด Jumbo Frame" in out


def test_set_packet_succeeds_when_jumbo_is_on():
    """เปิด Jumbo แล้วต้องตั้งได้จริง และต้องไม่ฟ้องเรื่องการ์ดแลนอีก."""
    code, out = run_fake(["--set-packet"],
                         sim={"packet_size": 1500, "optimal_packet_size": 8164,
                              "max_packet_size": 9000})
    assert "packet=8164 (สำเร็จ)" in out
    assert "การ์ดแลนยังไม่ได้เปิด Jumbo Frame" not in out
