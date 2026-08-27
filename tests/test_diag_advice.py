"""
6 จุดที่ระบบเคย "รายงานผิดแบบมั่นใจ" — พบจากการรันจริงบนสถานี 26 ส.ค. 2026

ทุกข้อเป็นตระกูลเดียวกัน: ตัวเลข/คำแนะนำที่ดูน่าเชื่อถือ แต่ส่งผู้ใช้ไปแก้ของที่
ไม่ได้พัง หรือปิดบังสิ่งที่พังจริง (กฎเหล็กข้อ 2)

  ① ช่องติ๊กไม่สะท้อนค่าใน config      → config บอกเปิด แต่พฤติกรรมจริงปิด
  ② "ต้องเพิ่มไฟ N เท่า"               → ลืมนับ gain ที่ยังเหลือหัวอีก 10+ dB
  ③ "กล้องส่งช้า และไม่มีเฟรมหาย"      → ขัดกับบรรทัดบนที่บอกว่าหาย 286 เฟรม
  ④ "เจอรอยบุบ 5/5"                    → ไม่เช็คว่ากรอบอยู่ตรงไหน
  ⑤ ขั้นที่ฉากขยับ                      → ยังถูกเอาไปเทียบข้ามขั้นตามปกติ
  ⑥ ด้าน NG ที่ไม่เจออะไรเลย           → บอกให้ไปไล่โฟกัส/โมเดล ทั้งที่แค่เลือกด้านผิด
"""

import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

pytest.importorskip("numpy")
pytest.importorskip("cv2")

import hik_burst as hb                                   # noqa: E402
import hik_exposure as hx                                # noqa: E402


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# ════════════════════════════════════════════════════════════════════
# ① ช่องติ๊กต้องสะท้อนค่าใน config
# ════════════════════════════════════════════════════════════════════
def test_server_sends_the_checkbox_default_not_just_row_visibility():
    """เดิมส่งแค่ can_pause_inference (= "แสดงแถวไหม") ⇒ หน้าเว็บฝังค่าเริ่มต้นเอง."""
    app_src = _read("app.py")
    assert '"pause_inference_default"' in app_src
    assert '"window_ms_default"' in app_src


def test_the_page_ticks_the_boxes_from_the_server_default():
    js = _read("static", "js", "hik_burst.js")
    caps = js[js.index("function applyCaps("):js.index("function loadCaps(")]
    assert "pause_inference_default" in caps, "ต้องอ่านค่าเริ่มต้นจากเซิร์ฟเวอร์"
    assert re.search(r"pause\.checked\s*=", caps), "ต้องตั้ง .checked ไม่ใช่แค่โชว์แถว"
    assert re.search(r"wck\.checked\s*=\s*true", caps), "โหมดหน้าต่างก็ต้องถูกติ๊กให้"


def test_a_choice_the_user_made_is_never_overwritten_by_the_default():
    """กดปิดเองแล้วต้องไม่ถูกดีดกลับตอน poll รอบถัดไป."""
    js = _read("static", "js", "hik_burst.js")
    caps = js[js.index("function applyCaps("):js.index("function loadCaps(")]
    assert caps.count("dataset.touched") >= 3
    assert "function rememberChoice(" in js and "function seedChoice(" in js


# ════════════════════════════════════════════════════════════════════
# ② "เพิ่มไฟ" ต้องถาม gain ก่อนเสมอ
# ════════════════════════════════════════════════════════════════════
def test_gain_headroom_answers_before_buying_lights():
    """ตัวเลขจริงจากสถานี: 909 µs เบลอ 7.09 px · gain 12.6 จากเพดาน 24."""
    out = hb._light_or_gain(909, 7.09, 4.0, 12.6, 24.0)
    assert out["want_exposure_us"] == 513
    assert out["gain_add_db"] == 5.0
    assert out["gain_needed_db"] == 17.6
    assert out["gain_enough"] is True
    assert "light_factor_needed_after_gain" not in out, "gain พอ ⇒ ห้ามพูดถึงการเพิ่มไฟ"


def test_light_is_only_suggested_when_gain_really_runs_out():
    out = hb._light_or_gain(5000, 39.0, 4.0, 20.0, 24.0)
    assert out["gain_enough"] is False
    assert out["light_factor_needed_after_gain"] > 1


def test_no_advice_at_all_when_the_target_is_already_met():
    out = hb._light_or_gain(350, 2.73, 4.0, 21.1, 24.0)
    assert out == {"light_ok": True}


def test_unknown_gain_never_becomes_a_claim_about_lights():
    """ไม่รู้ gain/เพดาน ⇒ บอกได้แค่ว่าต้องชดเชยกี่ dB — ห้ามสรุปว่าต้องซื้อไฟ."""
    out = hb._light_or_gain(909, 7.09, 4.0, None, None)
    assert out["gain_add_db"] == 5.0
    assert "gain_enough" not in out
    assert "light_factor_needed_after_gain" not in out


def test_the_page_mentions_gain_before_lights():
    js = _read("static", "js", "hik_burst.js")
    assert "function brightnessAdvice(" in js
    assert "ยังไม่ต้องเพิ่มไฟ" in js
    # ข้อความ "ต้องเพิ่มไฟจริง" ต้องอยู่หลังการเช็คเพดาน gain เท่านั้น
    i_ceiling = js.index("gain_db_max")
    assert js.index("ต้องเพิ่มไฟจริง") > i_ceiling


def test_gain_and_its_ceiling_are_always_in_the_summary():
    """ทุกข้อความที่พูดว่า "เพิ่มไฟ" ต้องเช็คสองค่านี้ได้เสมอ."""
    src = _read("hik_burst.py")
    block = src[src.index('    out = {\n        # gain ปัจจุบัน'):]
    assert '"gain_db"' in block[:400] and '"gain_db_max"' in block[:400]


# ════════════════════════════════════════════════════════════════════
# ③ "กล้องส่งช้า" ต้องไม่ขัดกับ "เฟรมหายระหว่างทาง"
# ════════════════════════════════════════════════════════════════════
def _meta(saved, dropped, considered, lost, elapsed, packet=8164, size="1224x1024"):
    return {"saved": saved, "dropped": dropped, "considered": considered,
            "elapsed_s": elapsed, "packet_size": packet, "size": size, "stage_ms": {},
            "diag_start": {"cam_dropped": 0, "cam_timeouts": 0, "net": {"lost_packets": 0}},
            "diag_end": {"cam_dropped": lost, "cam_timeouts": 0, "net": {"lost_packets": 0}}}


def _causes(g):
    return [i["cause"] for i in g["issues"]]


def test_heavy_transit_loss_is_not_also_reported_as_a_slow_camera():
    """เคสจริง 26 ส.ค.: หาย 286 เฟรม (39%) แล้วระบบบอกว่า "ไม่มีเฟรมหาย"."""
    g = hb.diagnose(_meta(69, 0, 443, 286, 10.27))
    assert "transport" in _causes(g)
    assert "camera_rate" not in _causes(g), "อัตราที่มาถึงต่ำเพราะของหายกลางทาง"


def test_the_camera_really_being_slow_is_still_reported():
    g = hb.diagnose(_meta(200, 0, 200, 0, 10.0))
    assert "camera_rate" in _causes(g)
    text = [i["text"] for i in g["issues"] if i["cause"] == "camera_rate"][0]
    assert "ไม่มีเฟรมหายระหว่างทางเลย" in text


def test_a_small_loss_does_not_silence_the_slow_camera_finding():
    g = hb.diagnose(_meta(200, 0, 200, 10, 10.0))
    assert {"transport", "camera_rate"} <= set(_causes(g))
    text = [i["text"] for i in g["issues"] if i["cause"] == "camera_rate"][0]
    assert "ไม่มีเฟรมหาย" not in text, "มีเฟรมหายอยู่ ⇒ ห้ามอ้างว่าไม่มี"


def test_produced_fps_is_reported_next_to_delivered_fps():
    """ขาดตัวเลขนี้ = อ่านผลผิดทั้งหมด (กล้องเต็มสปีดแล้ว ≠ กล้องช้า)."""
    g = hb.diagnose(_meta(69, 0, 443, 286, 10.27))
    assert g["produced_fps"] == 71.0
    assert g["delivered_fps"] == 43.1
    js = _read("static", "js", "hik_burst.js")
    assert "'กล้องผลิตจริง'" in js and "'มาถึงเครื่อง'" in js


def test_produced_equals_delivered_when_nothing_is_lost():
    g = hb.diagnose(_meta(200, 0, 200, 0, 10.0))
    assert g["produced_fps"] == g["delivered_fps"]


# ════════════════════════════════════════════════════════════════════
# ④ "เจอรอยบุบ" ต้องเช็คตำแหน่งด้วย
# ════════════════════════════════════════════════════════════════════
SAME = [[100, 100, 140, 140]]
ELSEWHERE = [[600, 600, 640, 640]]


def _row(us, gain, hits, frames=5, boxes=None, moved=False, capped=False, dark=False):
    return {"exposure_us": us, "gain_db": gain, "frames": frames,
            "frames_with_defect": hits, "defect_rate": round(hits / float(frames), 3),
            "boxes": list(boxes or []), "moved": moved,
            "gain_capped": capped, "dark": dark}


def test_a_step_that_finds_something_elsewhere_does_not_count_as_passing():
    rows = [_row(1295, 9.6, 5, boxes=SAME), _row(700, 14, 5, boxes=SAME),
            _row(350, 21, 5, boxes=ELSEWHERE)]
    s = hx.summarize(rows, "ng", line_speed_px_s=7800, blur_target_px=4.0)
    assert s["limit_us"] == 700, "ขั้น 350 เจอคนละจุด ⇒ ห้ามนับว่าผ่าน"
    assert [r["boxes_match"] for r in rows] == [True, True, False]


def test_boxes_at_the_same_place_keep_passing():
    rows = [_row(1295, 9.6, 5, boxes=SAME), _row(350, 21, 5, boxes=SAME)]
    s = hx.summarize(rows, "ng", line_speed_px_s=7800, blur_target_px=4.0)
    assert s["limit_us"] == 350


def test_a_slightly_shifted_box_is_still_the_same_dent():
    """กรอบของภาพที่ noise สูงขยับ/ขยายเป็นธรรมชาติ ⇒ เกณฑ์ต้องหลวมพอ."""
    rows = [_row(1295, 9.6, 5, boxes=[[100, 100, 140, 140]]),
            _row(350, 21, 5, boxes=[[106, 104, 148, 146]])]
    s = hx.summarize(rows, "ng", line_speed_px_s=7800, blur_target_px=4.0)
    assert s["limit_us"] == 350


def test_no_reference_means_no_verdict_on_position():
    """ไม่มีขั้นไหนมีกรอบเลย ⇒ ``None`` ไม่ใช่ ``False`` (ไม่รู้ ≠ ผิด)."""
    rows = [_row(1295, 9.6, 0), _row(350, 21, 0)]
    hx.mark_box_agreement(rows)
    assert [r["boxes_match"] for r in rows] == [None, None]


def test_position_check_never_blocks_the_ok_side():
    """ด้าน OK: กรอบไหนก็เป็น NG ปลอมทั้งนั้น ตำแหน่งไม่เกี่ยว."""
    rows = [_row(1295, 9.3, 0), _row(700, 14, 0), _row(350, 21, 0)]
    s = hx.summarize(rows, "ok", line_speed_px_s=7800, blur_target_px=4.0)
    assert s["limit_us"] == 350


# ════════════════════════════════════════════════════════════════════
# ⑤ ฉากไม่นิ่ง = เทียบข้ามขั้นไม่ได้
# ════════════════════════════════════════════════════════════════════
def test_a_moving_scene_warns_at_the_round_level():
    rows = [_row(1295, 9.3, 0), _row(1025, 11.3, 0, moved=True),
            _row(700, 13.9, 0, moved=True), _row(512, 17.4, 3, boxes=ELSEWHERE),
            _row(350, 20.6, 2, moved=True, boxes=SAME), _row(256, 23.5, 0)]
    s = hx.summarize(rows, "ng", line_speed_px_s=7800, blur_target_px=4.0)
    assert s["moved_steps"] == 3
    assert "ไม่ยุติธรรม" in s["warn_moved"]


def test_a_still_round_gets_no_movement_warning():
    rows = [_row(1295, 9.6, 5, boxes=SAME), _row(350, 21, 5, boxes=SAME)]
    s = hx.summarize(rows, "ng", line_speed_px_s=7800, blur_target_px=4.0)
    assert "warn_moved" not in s


# ════════════════════════════════════════════════════════════════════
# ⑥ เลือกด้านผิด — ต้องเดาให้ถูกก่อนส่งไปไล่โฟกัส/โมเดล
# ════════════════════════════════════════════════════════════════════
def test_an_ng_run_that_finds_nothing_suspects_the_wrong_side_first():
    rows = [_row(1295, 9.3, 0), _row(700, 13.8, 0), _row(256, 23.5, 0)]
    s = hx.summarize(rows, "ng", line_speed_px_s=7800, blur_target_px=4.0)
    assert s["maybe_wrong_role"] is True
    assert "เลือกด้านผิด" in s["headline"]
    assert "โฟกัส" not in s["headline"], "อย่าส่งผู้ใช้ไปแก้ของที่ไม่ได้พังก่อน"


def test_an_ng_run_that_finds_something_but_fails_keeps_the_old_advice():
    """เจอบ้างแต่ไม่ครบ = ไม่ใช่ลายเซ็นของกระป๋องดี ⇒ คำแนะนำเดิมยังถูก."""
    rows = [_row(1295, 9.3, 2), _row(700, 13.8, 1)]
    s = hx.summarize(rows, "ng", line_speed_px_s=7800, blur_target_px=4.0)
    assert not s.get("maybe_wrong_role")
    assert "โฟกัส" in s["headline"]


def test_flipping_the_side_reuses_the_data_instead_of_reshooting():
    """ตัวเลขจริงจากสถานี: ชุดกระป๋องดีที่ติดป้าย ng → พลิกแล้วต้องได้ 256 µs."""
    rows = [_row(1295, 9.3, 0), _row(1025, 11.3, 0), _row(700, 13.8, 0),
            _row(512, 17.4, 0), _row(350, 20.6, 0), _row(256, 23.5, 0)]
    data = {"rows": rows, "role": "ng"}
    hx.resummarize(data, "ok", line_speed_px_s=7800, blur_target_px=4.0)
    assert data["role"] == "ok"
    assert data["summary"]["limit_us"] == 256
    assert data["summary"]["blur_at_line_px"] == 2.0
    assert data["summary"]["meets_target"] is True


def test_flipping_to_a_bogus_side_is_refused():
    with pytest.raises(ValueError):
        hx.resummarize({"rows": [], "role": "ng"}, "maybe")


def test_the_route_and_the_button_exist():
    app_src = _read("app.py")
    assert "/api/camera/hik/exposures/<name>/role" in app_src
    js = _read("static", "js", "hik_exposure.js")
    assert "function flipRole(" in js and "hikExpFlipRole" in js


# ════════════════════════════════════════════════════════════════════
# ⑦ ข้อความจากฝั่ง Python ใช้ **ตัวหนา** — ห้ามโผล่เป็นดอกจันบนจอ
# ════════════════════════════════════════════════════════════════════
# เห็นจริงในภาพหน้าจอของสถานี: "⇒ **ยังไม่เจอขีดจำกัด** —" และ "อยู่ที่ **ฝั่งรับ**"
def test_both_result_pages_convert_bold_markers():
    for f in ("hik_burst.js", "hik_exposure.js"):
        js = _read("static", "js", f)
        assert "function mdBold(" in js, f
        assert "<b>$1</b>" in js, f


def test_the_messages_that_carry_bold_markers_go_through_the_converter():
    js = _read("static", "js", "hik_exposure.js")
    for field in ("s.headline", "s.note_bottom", "s.warn_moved", "c.headline"):
        assert "mdBold(" + field in js, field
    burst = _read("static", "js", "hik_burst.js")
    assert "mdBold(it.text)" in burst and "mdBold(it.fix)" in burst
