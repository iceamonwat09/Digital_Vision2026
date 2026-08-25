"""
ทุกไฟล์ ``.pt`` ต้องใช้ตรรกะเดียวกับ `bestX.pt` — ไม่ผูกกับชื่อไฟล์อีกต่อไป

🐛 **ที่มา:** ตรรกะทั้งชุดของ `bestX.pt` (ป้าย NG/OK · ซ่อนกรอบทั้งใบตอน NG ·
ชื่อ/สีของคลาส) ถูกเปิดด้วย ``basename(model_path) == "bestx.pt"`` และ
``detect()`` กรอง detection ด้วย "ชุดชื่อคลาสของโหมด" ⇒ โมเดลเนื้อเดียวกัน
ที่ตั้งชื่อไฟล์อื่นจะ **ตรวจไม่เจออะไรเลยแบบเงียบ ๆ**: ไม่มี error ไม่มี NG
ไม่นับ ไม่ลง DB — ผู้ใช้เห็นแค่ "ระบบไม่เจอตำหนิ".

เทสต์ชุดนี้ล็อกทั้งพฤติกรรมใหม่ **และ** ล็อกว่าของเดิม (bestX.pt / best.pt /
โหมด Label) ไม่เปลี่ยนแม้แต่จุดเดียว.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yolo_detector as yd
from modes import can_dent as can_dent_mode
from modes import label as label_mode


# ── ของปลอมที่เลียนสัญญาของ ultralytics เท่าที่ detect() ใช้จริง ────────────
class _Arr:
    """ตัวแทน tensor: ต้องรองรับ .cpu().numpy() แบบเดียวกับ ultralytics."""

    def __init__(self, a):
        self._a = np.asarray(a)

    def cpu(self):
        return self

    def numpy(self):
        return self._a


class _Boxes:
    def __init__(self, xyxy, conf, cls):
        self.xyxy = _Arr(xyxy)
        self.conf = _Arr(conf)
        self.cls = _Arr(cls)

    def __len__(self):
        return len(self.conf.numpy())


class _Result:
    def __init__(self, boxes):
        self.boxes = boxes


class FakeModel:
    """โมเดลปลอม: คืนกล่องชุดเดิมทุกครั้ง และมี ``names`` เหมือนของจริง."""

    def __init__(self, names, boxes=None):
        self.names = dict(names)
        self._boxes = boxes

    def __call__(self, frame, **kw):
        if self._boxes is None:
            return [_Result(None)]
        return [_Result(self._boxes)]


def make_detector(model_path, names, mode_config=can_dent_mode, boxes=None):
    det = yd.YOLODetector(model_path=model_path, mode_config=mode_config)
    det.model = FakeModel(names, boxes)
    det._build_class_roles()
    return det


BESTX_NAMES = {0: "dent", 1: "can"}
BEST_NAMES = {0: "dented", 1: "dented_spot", 2: "good"}


def d(class_name, bbox=(10, 10, 40, 40), conf=0.9):
    x1, y1, x2, y2 = bbox
    return {"class_id": 0, "class_name": class_name, "confidence": conf,
            "bbox": [x1, y1, x2, y2],
            "center": [(x1 + x2) // 2, (y1 + y2) // 2]}


# ════════════════════════════════════════════════════════════════════
# ① ชื่อไฟล์ต้องไม่มีผลอีกต่อไป — หัวใจของงานรอบนี้
# ════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("fname", ["bestX.pt", "bestx.pt", "best2.pt",
                                   "bestX_v2.pt", "โมเดลใหม่.pt", "yolo-seg.pt"])
def test_every_filename_gets_the_same_roles(fname):
    det = make_detector(os.path.join("weights", "can_dent", fname), BESTX_NAMES)
    assert det.class_roles == {"dent": yd.ROLE_DEFECT, "can": yd.ROLE_BODY}
    assert det.class_warnings == []


@pytest.mark.parametrize("fname", ["bestX.pt", "best2.pt", "anything.pt"])
def test_every_filename_gets_the_same_labels_and_colors(fname):
    det = make_detector(os.path.join("weights", "can_dent", fname), BESTX_NAMES)
    assert det._class_names() == {"dent": "Can Dent", "can": "Can Good"}
    assert det._colors()["dent"] == (0, 0, 220)
    assert det._colors()["can"] == (80, 200, 0)


@pytest.mark.parametrize("fname", ["bestX.pt", "best2.pt"])
def test_every_filename_gets_the_same_verdict(fname):
    det = make_detector(os.path.join("weights", "can_dent", fname), BESTX_NAMES)
    assert det.classify_frame([d("dent"), d("can")]) == "ng"
    assert det.classify_frame([d("can")]) == "ok"
    assert det.classify_frame([]) is None


def test_verdict_none_means_no_guess():
    """ไม่เจออะไร = ไม่รู้ ⇒ ต้องไม่เดาว่า OK (กฎเหล็กข้อ 2)."""
    det = make_detector("weights/can_dent/anything.pt", BESTX_NAMES)
    assert det.classify_frame([]) is None


def test_legacy_alias_still_works():
    det = make_detector("weights/can_dent/bestX.pt", BESTX_NAMES)
    assert det.classify_frame_bestx([d("dent")]) == "ng"


# ════════════════════════════════════════════════════════════════════
# ② บั๊กตาบอดเงียบ — detect() ต้องไม่ทิ้งคลาสของโมเดลทิ้ง
# ════════════════════════════════════════════════════════════════════
class _OldCanDentMode:
    """โหมด can_dent **แบบเดิมก่อนแก้** — CLASS_NAMES มีแค่ของ best.pt.

    ใช้จำลองเหตุการณ์จริงที่ทำให้เกิดบั๊ก: โมเดล dent/can ที่ไม่ได้ชื่อ
    bestX.pt เจอตัวกรองคลาสของโหมดแล้วหายทั้งหมด.
    """
    NON_DEFECT_CLASSES = {"good", "can"}
    CLASS_NAMES = {"dented": "Can Dent", "dented_spot": "Dent Area",
                   "good": "Can Good"}
    COLORS = {"good": (80, 200, 0), "dented": (0, 0, 220),
              "dented_spot": (0, 100, 255)}
    VERDICT_BADGE = True


def test_detect_keeps_classes_that_the_mode_never_listed():
    """โมเดลชื่ออื่นที่มีคลาส dent/can เคยถูกกรองทิ้งทั้งหมด → 0 detection.

    นี่คือบั๊กที่ทำให้ "เปลี่ยนชื่อไฟล์ = ระบบตาบอด" — ต้องไม่กลับมาอีก
    **แม้ในโหมดที่ไม่ได้ประกาศชื่อคลาสพวกนี้ไว้เลย**.
    """
    boxes = _Boxes([[10, 10, 40, 40], [5, 5, 90, 90]], [0.9, 0.8], [0, 1])
    det = make_detector("weights/can_dent/best2.pt", BESTX_NAMES,
                        mode_config=_OldCanDentMode, boxes=boxes)
    got = det.detect(np.zeros((100, 100, 3), np.uint8))
    assert [x["class_name"] for x in got] == ["dent", "can"]


def test_the_verdict_survives_a_mode_that_never_listed_those_classes():
    det = make_detector("weights/can_dent/best2.pt", BESTX_NAMES,
                        mode_config=_OldCanDentMode)
    assert det.classify_frame([d("dent")]) == "ng"
    assert det.classify_frame([d("can")]) == "ok"
    assert det._class_names()["dent"] == "Can Dent"      # จากพจนานุกรมกลาง


def test_detect_keeps_a_class_nobody_has_ever_heard_of():
    boxes = _Boxes([[10, 10, 40, 40]], [0.9], [0])
    det = make_detector("weights/can_dent/x.pt", {0: "scratch"}, boxes=boxes)
    got = det.detect(np.zeros((100, 100, 3), np.uint8))
    assert [x["class_name"] for x in got] == ["scratch"]


def test_detect_still_honours_the_confidence_threshold():
    """เอาตัวกรองคลาสออก ต้องไม่เผลอเอาตัวกรองความมั่นใจออกไปด้วย."""
    boxes = _Boxes([[10, 10, 40, 40], [5, 5, 90, 90]], [0.9, 0.01], [0, 1])
    det = make_detector("weights/can_dent/x.pt", BESTX_NAMES, boxes=boxes)
    det.confidence_threshold = 0.25
    got = det.detect(np.zeros((100, 100, 3), np.uint8))
    assert [x["class_name"] for x in got] == ["dent"]


# ════════════════════════════════════════════════════════════════════
# ③ คลาสที่ไม่รู้จัก = ตำหนิ + ต้อง "ดัง" ไม่ใช่เงียบ
# ════════════════════════════════════════════════════════════════════
def test_unknown_class_counts_as_a_defect():
    det = make_detector("weights/can_dent/x.pt", {0: "scratch", 1: "can"})
    assert det.role_of("scratch") == yd.ROLE_DEFECT
    assert det.classify_frame([d("scratch")]) == "ng"


def test_unknown_class_raises_a_warning_for_the_web_page():
    det = make_detector("weights/can_dent/x.pt", {0: "scratch", 1: "can"})
    assert any("scratch" in w for w in det.class_warnings)


def test_a_model_without_a_whole_object_class_says_so():
    """ไม่มีคลาส 'ทั้งใบ' ⇒ สรุป OK ไม่ได้ และ Frame Capture ตัดสินครบใบไม่ได้."""
    det = make_detector("weights/can_dent/x.pt", {0: "dent"})
    assert any("ทั้งใบ" in w for w in det.class_warnings)
    assert det.classify_frame([d("dent")]) == "ng"
    assert det.classify_frame([]) is None


def test_a_model_with_no_defect_class_says_so():
    det = make_detector("weights/can_dent/x.pt", {0: "can", 1: "good"})
    assert any("ตำหนิ" in w for w in det.class_warnings)


def test_known_models_produce_no_warnings():
    for names in (BESTX_NAMES, BEST_NAMES):
        det = make_detector("weights/can_dent/whatever.pt", names)
        assert det.class_warnings == [], names


def test_class_role_table_is_json_friendly():
    det = make_detector("weights/can_dent/x.pt", BESTX_NAMES)
    table = det.class_role_table()
    assert {"name", "label", "role"} == set(table[0])
    assert all(isinstance(v, str) for row in table for v in row.values())


# ════════════════════════════════════════════════════════════════════
# ④ การวาด — ตรรกะของ bestX ต้องใช้กับทุกโมเดลในโหมดเดียวกัน
# ════════════════════════════════════════════════════════════════════
GREEN = (80, 200, 0)
RED = (0, 0, 220)


def _has(frame, bgr):
    return bool(np.any(np.all(frame == np.array(bgr, np.uint8), axis=2)))


def test_ng_hides_the_whole_object_box_on_any_filename():
    det = make_detector("weights/can_dent/best2.pt", BESTX_NAMES)
    frame = np.zeros((200, 200, 3), np.uint8)
    out = det.draw_detections(frame, [d("dent", (20, 20, 60, 60)),
                                      d("can", (5, 5, 190, 190))])
    assert _has(out, RED), "กรอบตำหนิต้องยังอยู่"
    assert not _has(out, GREEN), "กรอบ 'ทั้งใบ' ต้องหายไปเมื่อผลเป็น NG"


def test_ok_keeps_the_whole_object_box():
    det = make_detector("weights/can_dent/best2.pt", BESTX_NAMES)
    out = det.draw_detections(np.zeros((200, 200, 3), np.uint8),
                              [d("can", (5, 5, 190, 190))])
    assert _has(out, GREEN)


def test_three_class_model_gets_the_same_treatment():
    """best.pt (dented/dented_spot/good) เดินเส้นทางเดียวกับ bestX ทุกประการ."""
    det = make_detector("weights/can_dent/best.pt", BEST_NAMES)
    assert det.role_of("dented") == yd.ROLE_DEFECT
    assert det.role_of("dented_spot") == yd.ROLE_DEFECT
    assert det.role_of("good") == yd.ROLE_BODY
    out = det.draw_detections(np.zeros((200, 200, 3), np.uint8),
                              [d("dented", (20, 20, 60, 60)),
                               d("good", (5, 5, 190, 190), conf=0.5)])
    assert not _has(out, GREEN)


def test_defect_box_is_thicker_than_the_body_box():
    """ความหนาต้องตัดสินจากบทบาท ไม่ใช่ชื่อคลาส 'dented' ตรง ๆ —
    ไม่งั้นคลาส 'dent' ของ bestX จะได้กรอบบางเท่ากระป๋องดี = เน้นผิดตัว."""
    det = make_detector("weights/can_dent/bestX.pt", BESTX_NAMES)
    box = (20, 20, 120, 120)
    ng = det.draw_detections(np.zeros((200, 200, 3), np.uint8), [d("dent", box)])
    ok = det.draw_detections(np.zeros((200, 200, 3), np.uint8), [d("can", box)])
    red_px = int(np.sum(np.all(ng == np.array(RED, np.uint8), axis=2)))
    green_px = int(np.sum(np.all(ok == np.array(GREEN, np.uint8), axis=2)))
    assert red_px > green_px, (red_px, green_px)


def test_verdict_badge_is_drawn_for_can_dent_mode():
    det = make_detector("weights/can_dent/best2.pt", BESTX_NAMES)
    frame = np.zeros((200, 400, 3), np.uint8)
    out = det.draw_detections(frame, [d("can", (5, 5, 100, 100))])
    top_right = out[0:60, 260:400]
    assert top_right.any(), "ต้องมีป้าย OK ที่มุมขวาบน"


# ════════════════════════════════════════════════════════════════════
# ⑤ โหมดอื่นต้องไม่ถูกแตะ (กฎเหล็กข้อ 1)
# ════════════════════════════════════════════════════════════════════
def test_label_mode_draws_no_verdict_badge():
    det = make_detector("weights/label/label.pt", {0: "misprint", 1: "shift"},
                        mode_config=label_mode)
    assert det.verdict_badge is False
    frame = np.zeros((200, 400, 3), np.uint8)
    out = det.draw_detections(frame, [d("misprint", (5, 5, 100, 100))])
    assert not out[0:60, 260:400].any(), "โหมด Label ต้องไม่มีป้าย NG/OK"


def test_label_mode_never_hides_a_box():
    det = make_detector("weights/label/label.pt", {0: "misprint", 1: "good"},
                        mode_config=label_mode)
    out = det.draw_detections(np.zeros((200, 200, 3), np.uint8),
                              [d("misprint", (20, 20, 60, 60)),
                               d("good", (5, 5, 190, 190))])
    assert _has(out, GREEN), "โหมด Label ไม่มีตรรกะซ่อนกรอบ"


def test_label_mode_does_not_nag_about_unknown_classes():
    """CLASS_NAMES ว่าง = 'รับทุกคลาสที่ผู้ใช้เทรนมา' โดยตั้งใจ ⇒ ห้ามเตือน."""
    det = make_detector("weights/label/label.pt", {0: "misprint", 1: "shift"},
                        mode_config=label_mode)
    assert det.class_warnings == []


def test_label_mode_uses_the_model_own_names_as_labels():
    det = make_detector("weights/label/label.pt", {0: "mis_print"},
                        mode_config=label_mode)
    assert det._class_names() == {"mis_print": "Mis Print"}


# ════════════════════════════════════════════════════════════════════
# ⑥ แหล่งความจริงของ "คลาสที่ไม่ใช่ตำหนิ"
# ════════════════════════════════════════════════════════════════════
def test_non_defect_classes_come_from_the_mode():
    det = yd.YOLODetector(model_path="x.pt", mode_config=can_dent_mode)
    assert det.non_defect_classes() == {"good", "can"}


def test_non_defect_classes_fall_back_to_the_system_default():
    det = yd.YOLODetector(model_path="x.pt", mode_config=None)
    assert det.non_defect_classes() == yd.DEFAULT_NON_DEFECT_CLASSES


def test_a_mode_can_declare_another_whole_object_class():
    class _Mode:
        NON_DEFECT_CLASSES = {"body"}
        CLASS_NAMES = {"body": "ตัวถัง", "crack": "รอยร้าว"}
        COLORS = {}
        VERDICT_BADGE = True

    det = make_detector("weights/can_dent/x.pt", {0: "crack", 1: "body"},
                        mode_config=_Mode)
    assert det.role_of("body") == yd.ROLE_BODY
    assert det.classify_frame([d("body")]) == "ok"
    assert det.class_warnings == []


def test_role_of_answers_before_the_model_is_loaded():
    """เส้นทางที่ถามบทบาทก่อนโหลดโมเดลเสร็จ ต้องไม่ได้คำตอบที่ผิด."""
    det = yd.YOLODetector(model_path="x.pt", mode_config=can_dent_mode)
    assert det.role_of("can") == yd.ROLE_BODY
    assert det.role_of("dent") == yd.ROLE_DEFECT


def test_class_maps_before_load_keep_the_legacy_shape():
    det = yd.YOLODetector(model_path="x.pt", mode_config=can_dent_mode)
    assert det._class_names() == can_dent_mode.CLASS_NAMES
    assert det._colors() == can_dent_mode.COLORS


# ════════════════════════════════════════════════════════════════════
# ⑦ โมเดลสำรอง COCO — ต้องไม่กลายเป็น "ตำหนิ" ทั้ง 80 คลาส
# ════════════════════════════════════════════════════════════════════
# 🐛 ความเสี่ยงที่เกิดจากการ **ถอดตัวกรองคลาสออก**: เดิมคลาส COCO ถูกกรองทิ้ง
# โดยบังเอิญ ⇒ ไม่มี detection. ถ้าไม่กันไว้ ระบบจะนับ "person"/"bottle" เป็น
# รอยบุบ แล้ว **บันทึกลง DB** — ผลที่ผิดแบบมั่นใจเต็มรูปแบบ.
COCO_ISH = {i: n for i, n in enumerate(
    ["person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
     "truck", "boat", "bottle"])}


def test_coco_fallback_reports_nothing_in_a_qc_mode():
    boxes = _Boxes([[10, 10, 40, 40]], [0.9], [0])
    det = make_detector("weights/can_dent/missing.pt", COCO_ISH, boxes=boxes)
    det.using_fallback_weights = True
    det._build_class_roles()
    assert det.detect(np.zeros((100, 100, 3), np.uint8)) == []


def test_coco_fallback_says_why_it_reports_nothing():
    det = make_detector("weights/can_dent/missing.pt", COCO_ISH)
    det.using_fallback_weights = True
    det._build_class_roles()
    assert det.class_warnings, "ต้องบอกผู้ใช้ ไม่ใช่เงียบ"
    assert "yolov8n" in det.class_warnings[0]


def test_label_mode_keeps_its_coco_demo_behaviour():
    """โหมด Label เคยแสดงกรอบจาก COCO ได้ ⇒ ต้องเหมือนเดิมเป๊ะ (กฎเหล็กข้อ 1)."""
    boxes = _Boxes([[10, 10, 40, 40]], [0.9], [0])
    det = make_detector("weights/label/missing.pt", COCO_ISH,
                        mode_config=label_mode, boxes=boxes)
    det.using_fallback_weights = True
    det._build_class_roles()
    got = det.detect(np.zeros((100, 100, 3), np.uint8))
    assert [x["class_name"] for x in got] == ["person"]


def test_a_long_unknown_class_list_is_trimmed_for_the_web_page():
    names = dict(COCO_ISH)
    names[99] = "can"
    det = make_detector("weights/can_dent/x.pt", names)
    assert det.class_warnings
    assert "และอีก" in det.class_warnings[0]
    assert len(det.class_warnings[0]) < 400
