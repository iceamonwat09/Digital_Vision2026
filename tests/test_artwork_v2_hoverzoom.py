"""Artwork V2 — ชี้เมาส์ที่แถวจุดต่าง ⇒ ภาพ 🅰/🅱 ซูมไปที่กรอบนั้น (4 ต.ค. รอบ 5 · แสดงผลล้วน)

ผู้ใช้สั่ง: *"เอาเมาส์ไปวาง Rows ที่มีปัญหา ให้รูปทั้ง 2 Zoom ไปที่ปัญหานั้น · เอาเมาส์ออกก็ Zoom ออก
· เคลื่อนไหวต้อง Smooth ที่สุด ดูมืออาชีพ"*

ชั้นที่ทดสอบที่นี่ (ฟังก์ชันจริงจาก ``artwork_v2.js`` รันผ่าน node — ไม่ได้เขียนสูตรซ้ำ):
  ① ``zoomRect``  — กรอบเป้าหมาย = กรอบที่วาดบนจอ · จุดแทรกได้บริบทกว้างพอ
  ② ``zoomView``  — กรอบอยู่กลางกล่อง · ไม่ซูมเกินความละเอียดจริงของภาพ · ใกล้ขอบภาพแล้วไม่เห็นพื้นที่ว่าง
  ③ ``zoomPath``  — ทุกเฟรมระหว่างเคลื่อนไหวไม่หลุดขอบภาพ · ไม่สั่น · จบที่เป้าพอดี
  ④ ธง ``ARTWORK_V2_HOVER_ZOOM`` — ``0`` = หน้าเว็บเหมือนเดิม (ไม่มีตัวฟังเหตุการณ์ ไม่มีป้าย/คำใบ้)
ส่วนที่ต้องมีเบราว์เซอร์จริง (กรอบตรงคำบนภาพที่ซูม · ป้ายเลขเกาะกรอบ · จังหวะเฟรม) ตรวจบน Chromium
แล้ว — ดู CLAUDE.md หัวข้อ "🔍 4 ต.ค. (รอบ 5)"
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from artwork_v2 import config, runguard  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js", "artwork_v2.js")
HTML = os.path.join(ROOT, "templates", "artwork_v2.html")


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


# ── ตัวรันฟังก์ชันจริงผ่าน node ─────────────────────────────────────

_HARNESS = r"""
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const body = (name) => src.split('function ' + name + '(')[1].split('\n  }\n')[0];
const mk = (name, deps) => new Function(...Object.keys(deps), 'return function ' + name + '(' + body(name) + '\n}')(...Object.values(deps));
const zoomClamp = mk('zoomClamp', {});
const zoomRect = mk('zoomRect', {});
const zoomView = mk('zoomView', { zoomClamp });
const zoomPath = mk('zoomPath', { zoomClamp });
const zoomEase = mk('zoomEase', {});
const ZOOM = new Function('return ' + /const ZOOM = (\{[\s\S]*?\n  \});/.exec(src)[1])();
const FRAME_PAD = Number(/const FRAME_PAD = ([0-9.]+)/.exec(src)[1]);
const job = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = job.map((c) => {
  if (c.fn === 'rect') return zoomRect(c.sd, c.W, c.H, FRAME_PAD, ZOOM.minCtx);
  if (c.fn === 'view') return zoomView(c.r, c.W, c.H, c.sw, c.sh, c.cap, ZOOM);
  if (c.fn === 'ease') return c.t.map(zoomEase);
  if (c.fn === 'cfg') return { ZOOM: ZOOM, FRAME_PAD: FRAME_PAD };
  if (c.fn === 'path') {
    const p = zoomPath(c.a, c.b, c.sw, c.sh, ZOOM);
    const n = c.n || 200;
    const frames = [];
    for (let i = 0; i <= n; i++) frames.push(p.at(zoomEase(i / n)));
    return { ms: p.ms, frames: frames };
  }
  throw new Error('fn?');
});
console.log(JSON.stringify(out));
"""


def _run(cases):
    if not shutil.which("node"):
        pytest.skip("ไม่มี node")
    r = subprocess.run(["node", "-e", _HARNESS, "x", JS], input=json.dumps(cases),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _cfg():
    return _run([{"fn": "cfg"}])[0]


W, H = 2778, 1667          # ภาพที่ส่งจริงของชุดทดสอบ (400 dpi)
SW, SH = 604.0, 604.0 * H / W


def _in_bounds(v, sw=SW, sh=SH, eps=1e-6):
    s, tx, ty = v["s"], v["tx"], v["ty"]
    return (s >= 1 - eps and tx <= eps and ty <= eps
            and tx + s * sw >= sw - 1e-3 and ty + s * sh >= sh - 1e-3)


def _screen(v, rect, sw=SW, sh=SH):
    """กรอบ (พิกัดภาพที่ส่ง) → พิกัดบนจอในกล่อง"""
    kx, ky = sw / W, sh / H
    return [v["tx"] + v["s"] * rect[0] * kx, v["ty"] + v["s"] * rect[1] * ky,
            v["tx"] + v["s"] * rect[2] * kx, v["ty"] + v["s"] * rect[3] * ky]


# ── ① กรอบเป้าหมาย ──────────────────────────────────────────────────

def test_target_is_exactly_the_frame_drawn_on_screen():
    """ซูมไปที่ "กรอบที่ผู้ใช้เห็น" ไม่ใช่กรอบอื่น — เผื่อเท่ากับ wordFrame ทุกค่า"""
    pad_k = _cfg()["FRAME_PAD"]
    wb = [1000, 800, 1400, 860]                       # คำยาว 400 สูง 60
    r = _run([{"fn": "rect", "sd": {"box": [1100, 802, 1130, 858], "word_box": wb}, "W": W, "H": H}])[0]
    pad = 60 * pad_k
    assert r == pytest.approx([1000 - pad, 800 - pad, 1400 + pad, 860 + pad])


def test_insertion_point_gets_context_around_it():
    """จุดแทรกเป็นแถบบาง ๆ — ซูมเข้าแถบนั้นตรง ๆ จะไม่เห็นคำข้างเคียงเลย"""
    c = _cfg()["ZOOM"]
    r = _run([{"fn": "rect", "sd": {"box": [1199, 800, 1201, 860], "word_box": None}, "W": W, "H": H}])[0]
    assert r[2] - r[0] == pytest.approx(60 * c["minCtx"])
    assert (r[0] + r[2]) / 2 == pytest.approx(1200)


def test_side_without_a_box_has_no_target():
    out = _run([{"fn": "rect", "sd": {"text": "", "box": None, "word_box": None}, "W": W, "H": H},
                {"fn": "rect", "sd": None, "W": W, "H": H}])
    assert out == [None, None]


def test_target_never_leaves_the_image():
    r = _run([{"fn": "rect", "sd": {"box": [0, 0, 40, 50], "word_box": [0, 0, 40, 50]}, "W": W, "H": H},
              {"fn": "rect", "sd": {"box": [W - 30, H - 50, W, H], "word_box": [W - 30, H - 50, W, H]}, "W": W, "H": H}])
    assert r[0][0] == 0 and r[0][1] == 0
    assert r[1][2] == W and r[1][3] == H


# ── ② มุมมองปลายทาง ─────────────────────────────────────────────────

def _view(rect, cap=W / SW, sw=SW, sh=SH):
    return _run([{"fn": "view", "r": rect, "W": W, "H": H, "sw": sw, "sh": sh, "cap": cap}])[0]


def test_frame_lands_in_the_middle_of_the_box():
    rect = [1200, 700, 1500, 780]
    v = _view(rect)
    x0, y0, x1, y1 = _screen(v, rect)
    assert v["s"] > 1.5
    assert (x0 + x1) / 2 == pytest.approx(SW / 2, abs=1e-6)
    assert (y0 + y1) / 2 == pytest.approx(SH / 2, abs=1e-6)


def test_zoom_never_exceeds_the_real_resolution_of_the_image():
    """เพดาน = ภาพที่ส่งแสดงที่ 1 พิกเซลภาพ : 1 พิกเซลจอ (เกินนั้น = ขยายภาพเบลอ ไม่ได้ข้อมูลเพิ่ม)"""
    c = _cfg()["ZOOM"]
    tiny = [1300, 800, 1310, 806]
    assert _view(tiny)["s"] == pytest.approx(W / SW)                     # ภาพละเอียดพอ ⇒ ตามความละเอียดจริง
    assert _view(tiny, cap=1.2)["s"] == pytest.approx(c["minCap"])        # ภาพเล็ก ⇒ อย่างน้อย 2 เท่า
    assert _view(tiny, cap=50)["s"] == pytest.approx(c["maxAbs"])         # ภาพใหญ่มาก ⇒ ไม่เกิน 8 เท่า


def test_big_frame_does_not_zoom():
    v = _view([20, 20, W - 20, H - 20])
    assert v == {"s": 1, "tx": 0, "ty": 0}


def test_tiny_gain_is_not_worth_a_wobble():
    """ซูมได้แค่ ~3% (กรอบเกือบพอดีกล่องอยู่แล้ว) ⇒ ไม่ซูม — ภาพขยับนิดเดียวดูเหมือนกระตุก ไม่ได้ช่วยให้เห็นชัดขึ้น"""
    c = _cfg()["ZOOM"]
    bw = c["fitW"] * W / 1.03
    v = _view([200, 800, 200 + bw, 860])
    assert v == {"s": 1, "tx": 0, "ty": 0}
    v2 = _view([200, 800, 200 + c["fitW"] * W / 1.10, 860])        # 10% ⇒ ซูมจริง
    assert v2["s"] == pytest.approx(1.10, rel=1e-6)


def test_wide_frame_zooms_only_to_fit():
    """กรอบกว้างเกือบทั้งภาพ (ทั้งบรรทัดหาย) ⇒ ขยายพอดีกล่อง ไม่ตัดกรอบ"""
    c = _cfg()["ZOOM"]
    rect = [300, 800, 2100, 860]
    v = _view(rect)
    x0, _, x1, _ = _screen(v, rect)
    assert v["s"] > 1
    assert x1 - x0 == pytest.approx(SW * c["fitW"], rel=1e-6)


@pytest.mark.parametrize("rect", [[0, 0, 60, 40], [W - 80, H - 40, W, H], [W - 70, 0, W, 30], [0, H - 30, 90, H]])
def test_frame_at_the_image_edge_stays_visible_without_blank_space(rect):
    v = _view(rect)
    assert v["s"] > 1 and _in_bounds(v)
    x0, y0, x1, y1 = _screen(v, rect)
    assert x0 >= -1e-6 and y0 >= -1e-6 and x1 <= SW + 1e-6 and y1 <= SH + 1e-6


# ── ③ เส้นทางระหว่างเคลื่อนไหว ──────────────────────────────────────

IDENT = {"s": 1, "tx": 0, "ty": 0}


def _path(a, b, n=200):
    return _run([{"fn": "path", "a": a, "b": b, "sw": SW, "sh": SH, "n": n}])[0]


def test_zoom_in_stays_inside_the_image_every_frame_and_ends_on_target():
    to = _view([2600, 1600, 2700, 1650])                         # ชิดมุมขวาล่าง (ยากสุด)
    p = _path(IDENT, to)
    assert all(_in_bounds(v) for v in p["frames"])
    assert p["frames"][0] == pytest.approx(IDENT) and p["frames"][-1] == to
    s = [v["s"] for v in p["frames"]]
    assert all(b >= a - 1e-9 for a, b in zip(s, s[1:]))         # ขยายขึ้นอย่างเดียว ไม่แกว่ง
    assert p["ms"] == _cfg()["ZOOM"]["inMs"]


def test_zoom_is_about_a_fixed_point_and_uniform_in_log_scale():
    """ซูมรอบจุดนิ่ง: จุดหนึ่งบนภาพไม่ขยับบนจอเลยตลอดการซูม ⇒ ตาไม่ต้องไล่ตาม · ขนาดเปลี่ยนสม่ำเสมอในสายตา"""
    to = _view([1200, 700, 1500, 780])
    p = _path(IDENT, to, n=2)
    mid = _run([{"fn": "ease", "t": [0.5]}])[0][0]
    assert mid == pytest.approx(0.5)
    assert p["frames"][1]["s"] == pytest.approx(math.sqrt(to["s"]))   # ครึ่งทาง = ค่าเฉลี่ยเรขาคณิต
    k = 1 / (to["s"] - 1)
    px, py = -to["tx"] * k, -to["ty"] * k                          # จุดนิ่ง (พิกัดกล่อง)
    assert 0 <= px <= SW and 0 <= py <= SH                         # อยู่ในกล่อง ⇒ เหตุผลที่ไม่หลุดขอบ
    for v in _path(IDENT, to, n=20)["frames"]:
        assert v["tx"] + v["s"] * px == pytest.approx(px, abs=1e-6)
        assert v["ty"] + v["s"] * py == pytest.approx(py, abs=1e-6)


def test_zoom_out_stays_inside_the_image_every_frame():
    fr = _view([10, 1600, 120, 1660])
    p = _path(fr, IDENT)
    assert all(_in_bounds(v) for v in p["frames"])
    assert p["frames"][-1] == IDENT
    s = [v["s"] for v in p["frames"]]
    assert all(b <= a + 1e-9 for a, b in zip(s, s[1:]))
    assert p["ms"] == _cfg()["ZOOM"]["outMs"]


def test_row_to_row_flies_without_jumps_and_never_shows_outside_the_image():
    c = _cfg()["ZOOM"]
    a = _view([100, 100, 300, 160])
    b = _view([2500, 1580, 2700, 1640])                          # มุมตรงข้าม = ทางไกลที่สุด
    p = _path(a, b, n=400)
    fr = p["frames"]
    assert fr[0] == pytest.approx(a) and fr[-1] == b
    assert all(_in_bounds(v) for v in fr)
    # ต่อเนื่อง: กรอบจอเลื่อนไม่เกิน ~4% ของกล่องต่อ 1/400 ของเวลา (ไม่มีกระตุก)
    worst = max(max(abs(u["tx"] - v["tx"]), abs(u["ty"] - v["ty"])) / max(u["s"], v["s"]) for u, v in zip(fr, fr[1:]))
    assert worst < 0.04 * SW
    assert c["minMs"] <= p["ms"] <= c["maxMs"]
    # ถอยออกระหว่างทาง (เห็นบริบท) แต่ไม่ถึงกับกลับเป็นภาพเต็มสำหรับระยะนี้ไม่บังคับ — ขนาดต้องไม่ต่ำกว่า 1
    assert min(v["s"] for v in fr) >= 1


def test_random_targets_all_paths_stay_inside_the_image():
    rnd = random.Random(7)
    views = [IDENT]
    for _ in range(14):
        x = rnd.uniform(0, W - 20)
        y = rnd.uniform(0, H - 10)
        w = rnd.uniform(4, 900)
        h = rnd.uniform(6, 120)
        views.append(_view([x, y, min(W, x + w), min(H, y + h)]))
    cases = [{"fn": "path", "a": a, "b": b, "sw": SW, "sh": SH, "n": 60}
             for a in views for b in views if a is not b]
    out = _run(cases)
    bad = [(i, v) for i, o in enumerate(out) for v in o["frames"] if not _in_bounds(v)
           or any(not math.isfinite(v[k]) for k in ("s", "tx", "ty"))]
    assert not bad, bad[:3]
    assert all(o["frames"][-1] == c["b"] for o, c in zip(out, cases))


def test_same_view_is_not_animated():
    v = _view([1200, 700, 1500, 780])
    assert _path(v, v)["ms"] == 0
    assert _path(IDENT, IDENT)["ms"] == 0


def test_easing_starts_and_stops_softly():
    t = [i / 100 for i in range(101)]
    e = _run([{"fn": "ease", "t": t}])[0]
    assert e[0] == 0 and e[-1] == 1
    assert all(b >= a for a, b in zip(e, e[1:]))
    assert e[1] < 0.001 and 1 - e[-2] < 0.001                     # ความเร็วต้น/ท้ายเกือบศูนย์
    assert all(e[i] + e[100 - i] == pytest.approx(1) for i in range(101))


# ── ④ หน้าเว็บ / ธง ──────────────────────────────────────────────────

def _client(monkeypatch):
    from flask import Flask, g
    from artwork_v2 import routes
    from artwork_v2.routes import artwork_v2_bp
    monkeypatch.setattr(routes, "_RUNS", runguard.RunGuard())
    app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"),
                static_folder=os.path.join(ROOT, "static"))

    @app.before_request
    def _fake_auth():
        g.auth_enabled = False
        g.current_user = None

    @app.context_processor
    def _ctx():
        return {"config_version": "t", "current_user": None, "auth_enabled": False,
                "has_perm": lambda *a, **k: True}

    app.register_blueprint(artwork_v2_bp)
    return app.test_client()


def test_flag_default_is_on():
    assert config.HOVER_ZOOM is True


@pytest.mark.parametrize("flag,expect", [(True, "1"), (False, "0")])
def test_template_carries_the_flag_and_hint(monkeypatch, flag, expect):
    monkeypatch.setattr(config, "HOVER_ZOOM", flag)
    html = _client(monkeypatch).get("/artwork_v2").get_data(as_text=True)
    assert 'data-hover-zoom="%s"' % expect in html
    assert ('id="v2ZoomHint"' in html) is flag


def test_flag_off_attaches_nothing():
    """ปิดธง ⇒ ไม่มีตัวฟังเหตุการณ์ ไม่มีป้าย/กล่องข้อความ · คลิกแถว = แบบเดิม"""
    js = _read(JS)
    assert re.search(r'const HOVER_ZOOM = \(\$\("v2Root"\) && \$\("v2Root"\)\.dataset\.hoverZoom\) === "1";', js)
    tail = js[js.index("  if (HOVER_ZOOM) {\n    const res = $(\"v2PairsRes\");"):]
    block = tail[:tail.index("\n  }\n")]
    for ev in ("pointermove", "pointerleave", "keydown", "resize", '"click"'):
        assert ev in block                                         # ตัวฟังทุกตัวอยู่ใต้ธง
    assert js.count('res.addEventListener(') == 2 and 'res.addEventListener("pointermove"' in block
    assert "(HOVER_ZOOM ? '<div class=\"v2-zbadge\"" in js
    assert "if (HOVER_ZOOM && RZ.ok.has(id)) { zoomClick(id); return; }" in js


def test_size_is_measured_from_the_svg_not_rounded_client_size():
    """clientHeight ปัด 362.45 → 362 ⇒ กรอบเพี้ยน 1.6 px ที่ขอบล่าง (เจอจริงตอนวัดบน Chromium)"""
    js = _read(JS)
    body = js.split("function zoomStage(")[1].split("\n  }\n")[0]
    assert "getBoundingClientRect" in body
    assert "clientHeight" not in body and "clientWidth" not in body


def test_number_tags_carry_their_position_for_the_zoom():
    js = _read(JS)
    body = js.split("function wordFrame(")[1].split("\n  }\n")[0]
    assert 'data-fx="' in body and 'data-fy="' in body
    assert "(fx * 100).toFixed(3)" in body and "(fy * 100).toFixed(3)" in body   # ตำแหน่งเดิมจากค่าเดียวกัน


def test_css_keeps_motion_accessible_and_frames_thin():
    html = _read(HTML)
    assert re.search(r"\.v2-res-stage img \{ transform-origin:0 0; \}", html)
    assert re.search(r"@media \(prefers-reduced-motion: reduce\) \{[^}]*\.v2-zbadge", html)
    assert re.search(r"\.v2-res-stage rect\.f \{[^}]*vector-effect:non-scaling-stroke", html)
    assert "prefers-reduced-motion: reduce" in _read(JS)           # JS ก็ข้ามแอนิเมชันด้วย


def test_display_only_no_server_side_change():
    """ซูมเป็นเรื่องหน้าเว็บล้วน — ไม่มีค่านี้ในผลตรวจ/Log/การเรียก Vision"""
    for mod in ("pipeline", "compare", "diaglog", "vision_client", "ai_review"):
        src = _read(os.path.join(ROOT, "artwork_v2", mod + ".py"))
        assert "HOVER_ZOOM" not in src
