"""Artwork V2 (8 ต.ค. รอบ 3) — หน้าตาการ์ดผลตรวจแบบใหม่ (ARTWORK_V2_CARD_STYLE · แสดงผลล้วน)

ผู้ใช้สั่ง: *"ช่วยปรับการ์ดให้ดูสวยงาม"*

สิ่งที่ล็อก:
* ธงไปถึงหน้าเว็บ (``data-card-style``) · ปิดธง = ``classic``
* CSS ใหม่ **ทุกกฎ** ผูกกับ ``#v2Root[data-card-style="polish"]`` ⇒ ปิดธงแล้วไม่มีกฎใหม่ทำงานเลย
* JS: ปิดธงแล้ว markup เดิมทุกตัวอักษร (หัวการ์ดต่อคู่ · หัวคอลัมน์ภาพ · แถบผลตัดสิน)
* ฟังก์ชันใหม่ escape ข้อความทุกช่อง · แถบความครอบคลุมไม่เกิน 0..100% · ฝั่งที่อ่านไม่ได้ยังขึ้นข้อความ error
* ไม่แตะผลตรวจ: ฝั่งเซิร์ฟเวอร์ไม่รู้จักธงนี้นอกจากส่งให้ template
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

from artwork_v2 import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js", "artwork_v2.js")
TPL = os.path.join(ROOT, "templates", "artwork_v2.html")
SCOPE = '#v2Root[data-card-style="polish"]'


def _app():
    from flask import Flask, g
    from artwork_v2.routes import artwork_v2_bp
    app = Flask(__name__, template_folder=os.path.join(ROOT, "templates"),
                static_folder=os.path.join(ROOT, "static"))

    @app.before_request
    def _fake_auth():
        g.auth_enabled = False
        g.current_user = None

    @app.context_processor
    def _ctx():
        return {"config_version": "test", "current_user": None, "auth_enabled": False,
                "has_perm": lambda *a, **k: True}

    app.register_blueprint(artwork_v2_bp)
    return app.test_client()


def test_default_on():
    assert config.CARD_STYLE is True or os.environ.get("ARTWORK_V2_CARD_STYLE") == "0"


def test_page_attr_follows_flag(monkeypatch):
    def root_attr():
        html = _app().get("/artwork_v2").get_data(as_text=True)
        return re.search(r'<div class="v2" id="v2Root"[^>]*data-card-style="(\w+)"', html).group(1)
    monkeypatch.setattr(config, "CARD_STYLE", True)
    assert root_attr() == "polish"
    monkeypatch.setattr(config, "CARD_STYLE", False)
    assert root_attr() == "classic"


def _polish_css():
    tpl = open(TPL, encoding="utf-8").read()
    blk = "/* ── " + tpl.split("/* ── หน้าตาการ์ดผลตรวจแบบใหม่")[1].split("  .v2-legend span {")[0]
    return re.sub(r"/\*.*?\*/", "", blk, flags=re.S)


def test_every_new_css_rule_is_scoped_to_the_flag():
    css = _polish_css()
    sels = [s.strip() for s in re.findall(r"([^{}]+)\{", css)]
    assert len(sels) > 40
    for s in sels:
        if s.startswith("@media"):
            continue
        for part in s.split(","):
            assert part.strip().startswith(SCOPE), part


def test_mobile_card_rows_keep_hidden_and_todo_filter():
    css = _polish_css()
    # แถวหมายเหตุที่พับไว้ (hidden) ต้องไม่ถูก display:block บังคับให้โผล่
    assert "tr.v2-note:not([hidden]) { display:block; }" in css
    assert "tr.v2-note { display" not in css
    # ตัวกรอง "เฉพาะที่ยังไม่รีวิว" ต้องชนะกฎ grid ของแถว (selector เจาะจงกว่า/มาทีหลัง)
    i_grid = css.index("tr[data-f] { display:grid;")
    i_todo = css.index("#v2PairsRes.v2-rv-todo-only tr.v2-rv-done { display:none; }")
    assert i_todo > i_grid
    # การ์ดแถวใช้ได้เฉพาะตารางข้างภาพ (6 คอลัมน์) — ตารางแบบเดิม 7 คอลัมน์ไม่ถูกแตะ
    for line in css.splitlines():
        if "tr[data-f] > td:nth-child" in line or "tr[data-f] { display:grid" in line:
            assert '[data-side-table="1"]' in line, line


def test_js_classic_markup_unchanged():
    src = open(JS, encoding="utf-8").read()
    assert 'const CARD = root.dataset.cardStyle === "polish";' in src
    # แถบผลตัดสินเดิม / หัวการ์ดเดิม / หัวคอลัมน์เดิม ยังอยู่ครบในทางปิดธง
    assert '$("v2Verdict").innerHTML = CARD ? verdictHtml(r) : \'<div class="v2-verdict v2-v-\'' in src
    assert "'<div class=\"v2-card\" data-pn=\"' + esc(p.n) + '\" style=\"margin:12px 0\"><b>คู่ ' + p.n" in src
    assert 'function colHead(s, sd) { return CARD ? sideHead(s, sd) : (s === "a" ? "🅰 " : "🅱 ") + sideInfo(sd); }' in src
    assert src.count("colHead(\"a\", p.sides.a)") == 2 and src.count("colHead(\"b\", p.sides.b)") == 2


# ── ฟังก์ชัน JS จริง (node) ──────────────────────────────────────────

_HARNESS = r"""
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const body = (name) => src.split('function ' + name + '(')[1].split('\n  }\n')[0];
const mk = (name, deps) => new Function(...Object.keys(deps), 'return function ' + name + '(' + body(name) + '\n}')(...Object.values(deps));
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const VERDICT_ICON = { PASS: "✓", REVIEW: "!", FAIL: "✕", UNREADABLE: "?" };
const AI_MODE_TH = { assist: "อัลกอริทึมตัดสิน + AI เสริม", off: "ปิด AI" };
const job = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const sideInfo = mk('sideInfo', { esc });
const sideHead = mk('sideHead', { esc });
console.log(JSON.stringify(job.map((c) => {
  if (c.fn === 'verdict') return mk('verdictHtml', { esc, VERDICT_ICON, AI_MODE_TH })(c.r);
  if (c.fn === 'pair') return mk('pairHead', { esc, VERDICT_ICON })(c.p);
  if (c.fn === 'col') return mk('colHead', { CARD: c.card, sideHead, sideInfo })(c.s, c.sd);
  if (c.fn === 'info') return (c.s === 'a' ? '🅰 ' : '🅱 ') + sideInfo(c.sd);
  throw new Error('fn?');
})));
"""


def _node(cases):
    if not shutil.which("node"):
        pytest.skip("ไม่มี node")
    r = subprocess.run(["node", "-e", _HARNESS, "x", JS], input=json.dumps(cases),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


SD = {"ok": True, "page": 0, "sent_px": [2668, 1601], "render": {"dpi": 400}, "encode": {"quality": 92},
      "jpeg_bytes": 350000, "stats": {"lines": 14, "conf_mean": 0.976, "langs": ["en"]}}
BAD = dict(SD, ok=False, error="<b>HTTP 500</b>", stats={})


def test_js_verdict_and_pair_head_escape_and_clamp():
    evil = "<img src=x onerror=1>"
    v, p, p2, p3 = _node([
        {"fn": "verdict", "r": {"verdict": "FAIL", "verdict_th": "ไม่ผ่าน", "reasons": [evil], "run": "run_001",
                                "at": "t", "version": evil, "sharpness": "standard", "ai": {"mode": "off"}}},
        {"fn": "pair", "p": {"n": 1, "verdict": "REVIEW", "coverage": 1.7, "reasons": [evil]}},
        {"fn": "pair", "p": {"n": 2, "verdict": "PASS", "coverage": 0.42, "coverage_ignored": True}},
        {"fn": "pair", "p": {"n": 3, "verdict": "UNREADABLE"}},
    ])
    for h in (v, p):
        assert "<img" not in h and "&lt;img" in h
    assert "v2-v-FAIL" in v and "✕" in v and "รอบ run_001" in v and "AI: ปิด AI" in v and "มาตรฐาน 400 dpi" in v
    assert 'style="width:100%"' in p and "170%" in p           # แถบไม่ล้น แต่ตัวเลขบอกค่าจริง
    assert "v2-covm lo" in p2 and "42%" in p2 and "ไม่ใช้ตัดสิน" in p2
    assert "v2-covm" not in p3 and "v2-vpill v2-v-UNREADABLE" in p3


def test_js_side_head_short_line_full_title_and_error():
    good, bad, classic, old = _node([
        {"fn": "col", "card": True, "s": "a", "sd": SD},
        {"fn": "col", "card": True, "s": "b", "sd": BAD},
        {"fn": "col", "card": False, "s": "a", "sd": SD},
        {"fn": "info", "s": "a", "sd": SD},
    ])
    assert "หน้า 1 · 400 dpi · 14 บรรทัด · มั่นใจ 98%" in good
    assert 'title="2668×1601 px · 400 dpi · JPEG q92 · 342 KB · 14 บรรทัด · ความมั่นใจเฉลี่ย 0.98 · ภาษา en"' in good
    assert "🅱" in bad and "อ่านไม่ได้: &lt;b&gt;HTTP 500&lt;/b&gt;" in bad and "<b>HTTP" not in bad
    assert classic == old                                           # ปิดธง = markup เดิมทุกตัวอักษร
