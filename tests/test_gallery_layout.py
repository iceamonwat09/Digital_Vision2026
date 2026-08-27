"""
ล็อกลำดับของแกลเลอรีถ่ายรัว/ผลไล่ exposure: **ปุ่ม → รูป → ข้อมูล**

🐛 **ที่มา (วัดบนเบราว์เซอร์จริง 25 ส.ค. 2026):** KPI + สรุป + วินิจฉัย ถูกวาง
ไว้ *เหนือ* กริดภาพ ในคอลัมน์ flex ที่ความสูงถูกกำหนดมาแล้ว (modal = 94vh) และ
กริดเป็น element เดียวที่ยืดหดได้ ⇒ ทุกบรรทัดของข้อความถูกหักออกจากพื้นที่รูป
โดยตรง. จอ 1366x768: ข้อความกิน 477px เหลือให้กริด **191px** ขณะที่การ์ด 1 ใบ
สูง 225px ⇒ มองไม่เห็นการ์ดเต็มแม้แต่ใบเดียว และยิ่งชุดมีปัญหา (ข้อความวินิจฉัย
ยาว) ยิ่งเห็นรูปน้อยลง.

เทสต์ยูนิตวัด layout ไม่ได้ (ต้องใช้เบราว์เซอร์) — แต่ล็อก **ลำดับใน DOM** และ
**คุณสมบัติของ CSS ที่ทำให้บั๊กนี้เป็นไปได้** ได้ ซึ่งพอจะกันการถอยหลัง
"""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _html():
    with open(os.path.join(ROOT, "templates", "index.html"), encoding="utf-8") as f:
        return f.read()


def _pos(html, needle):
    i = html.find(needle)
    assert i >= 0, "หา %r ในหน้าไม่เจอ" % needle
    return i


# ── ① ลำดับใน DOM ────────────────────────────────────────────────
def test_gallery_puts_the_pictures_before_the_numbers():
    h = _html()
    bar = _pos(h, 'class="hik-gal-bar"')
    grid = _pos(h, 'id="hikGalGrid"')
    kpis = _pos(h, 'id="hikGalKpis"')
    summary = _pos(h, 'id="hikGalSummary"')
    diag = _pos(h, 'id="hikGalDiag"')
    assert bar < grid < kpis < summary < diag, \
        "ลำดับต้องเป็น ปุ่ม → รูป → ข้อมูล"


def test_exposure_result_puts_the_pictures_before_the_table():
    h = _html()
    shots = _pos(h, 'id="hikExpShots"')
    table = _pos(h, 'id="hikExpTableWrap"')
    assert shots < table, "การ์ดภาพต้องมาก่อนตารางรายขั้น"


def test_the_verdict_line_stays_on_top_of_the_exposure_result():
    """คำตัดสินคือคำตอบของทั้งหน้า — ต้องเห็นโดยไม่ต้องเลื่อน."""
    h = _html()
    assert _pos(h, 'id="hikExpVerdict"') < _pos(h, 'id="hikExpShots"')


# ── ② โครงที่ทำให้ "ข้อความยาว = รูปหด" เป็นไปไม่ได้อีก ──────────
def test_the_gallery_has_exactly_one_scroll_container():
    """
    ถ้ากริดยังเป็นตัวเลื่อนของตัวเอง (overflow-y + flex:1) มันจะกลับไปเป็น
    element เดียวที่ยืดหดได้อีกครั้ง = บั๊กเดิม.
    """
    h = _html()
    grid_css = h[_pos(h, ".hik-gal-grid {"):]
    grid_css = grid_css[:grid_css.index("}")]
    assert "overflow-y" not in grid_css, "กริดต้องไม่เป็นตัวเลื่อนเอง"
    assert "flex:" not in grid_css.replace(" ", ""), "กริดต้องไม่ยืดหดตามที่เหลือ"

    scroll_css = h[_pos(h, ".hik-gal-scroll {"):]
    scroll_css = scroll_css[:scroll_css.index("}")]
    assert "overflow-y: auto" in scroll_css
    assert "min-height: 0" in scroll_css, \
        "ขาด min-height:0 ⇒ กล่องไม่ยอมหด แล้วเนื้อหาทะลุ modal ที่ overflow:hidden"


def test_the_details_block_lives_inside_the_scroll_area():
    """ข้อมูลต้อง 'เลื่อนลงไปดู' ได้ ไม่ใช่ถูกตัดหายไปนอกพื้นที่เลื่อน."""
    h = _html()
    start = _pos(h, '<div class="hik-gal-scroll">')
    end = h.index("</section>", start)
    block = h[start:end]
    for wanted in ('id="hikGalGrid"', 'id="hikGalKpis"',
                   'id="hikGalSummary"', 'id="hikGalDiag"'):
        assert wanted in block, wanted


def test_the_toolbar_is_outside_the_scroll_area():
    """ปุ่มสั่งงานต้องกดได้เสมอ ไม่ต้องเลื่อนกลับขึ้นไปหา."""
    h = _html()
    assert _pos(h, 'class="hik-gal-bar"') < _pos(h, '<div class="hik-gal-scroll">')


# ── ③ บรรทัดเตือนสั้นที่ยังต้องเห็นโดยไม่ต้องเลื่อน ──────────────
def test_the_dropped_frames_warning_still_has_a_one_line_summary_on_top():
    """
    เฟรมที่ทิ้ง = กระป๋องที่หายไปจากชุดทดสอบ. พอย้ายรายละเอียดลงล่าง คำเตือน
    จะไม่มีใครเห็นถ้าไม่มีบรรทัดสรุปไว้บนสุด.
    """
    h = _html()
    brief = _pos(h, 'id="hikGalDropBrief"')
    assert brief < _pos(h, '<div class="hik-gal-scroll">')
    css = h[_pos(h, ".hik-gal-brief {"):]
    css = css[:css.index("}")]
    assert "nowrap" in css and "ellipsis" in css, "ต้องเป็นบรรทัดเดียวเสมอ"


def test_the_javascript_fills_and_clears_the_brief_line():
    with open(os.path.join(ROOT, "static", "js", "hik_burst.js"), encoding="utf-8") as f:
        js = f.read()
    assert "hikGalDropBrief" in js
    # ต้องล้างเมื่อสลับชุด ไม่งั้นคำเตือนของชุดเก่าค้างบนหัวชุดใหม่
    assert "function resetDetails()" in js
    assert re.search(r"resetDetails\(\);", js), "ต้องถูกเรียกจริง"
