"""ทุกคลาส CSS ที่ ``renderReport()`` พ่นออกมา ต้องมีกฎอยู่ใน **ทั้งสอง**
template — `artwork_check.html` และ `artwork_check_history.html`.

ทำไมต้องมี: หน้าประวัติเรียก ``window.awRenderReport`` **ตัวเดียวกัน** กับ
หน้าตรวจ แต่ CSS ของสองหน้าเป็นคนละ ``{% block extra_css %}`` ⇒ เพิ่มคลาส
ใหม่ในตัวเรนเดอร์แล้วใส่ CSS ข้างเดียว = แถบ/กล่องนั้นโผล่แบบไม่มีกรอบบน
หน้าประวัติ **โดยไม่มี error ให้เห็น**.

เคยเกิดจริง 2 รอบ: ``.aw-cov*`` (ตอนเพิ่มรายงาน coverage) และ
``.aw-hl-warn`` (คำเตือนโซนที่ชี้ตำแหน่งคำไม่ได้ — หลุดมาตั้งแต่ก่อนหน้านั้น
จับได้ตอนไล่ตรวจรวบยอด).
"""

import os
import re

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js", "artwork_check.js")
MAIN = os.path.join(ROOT, "templates", "artwork_check.html")
HIST = os.path.join(ROOT, "templates", "artwork_check_history.html")


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _renderer_source():
    """**เฉพาะ body ของ renderReport()** — ตัวเดียวที่หน้าประวัติเรียกใช้จริง
    (``artwork_check_history.js`` เรียก ``window.awRenderReport`` อย่างเดียว
    ไม่ได้เรียก ``awRenderTextTable``) จึงไม่รวมคลาสของตารางแปล."""
    js = _read(JS)
    a = js.index("  function renderReport(rep, box) {")
    b = js.index("  window.awRenderReport = renderReport;")
    return js[a:b]


def _emitted_classes():
    """คลาสที่ถูกเขียนลง HTML ในตัวเรนเดอร์ร่วม (class="aw-...")."""
    out = set()
    for m in re.finditer(r'class=\\?"([^"\\]*)', _renderer_source()):
        for cls in m.group(1).split():
            # ตัดเศษจากการต่อสตริงของ JS: 'aw-sumcard' + (n ? " hit" : "")
            cls = cls.strip("'\" +")
            if cls.startswith("aw-"):
                out.add(cls)
    # aw-zoomable เป็น "ตะขอให้ JS" (wireZoomable) ไม่ใช่คลาสจัดหน้าตา
    return {c for c in out if c != "aw-zoomable"}


def _css_classes(path):
    css = re.sub(r"/\*.*?\*/", "", _read(path), flags=re.S)
    return set(re.findall(r"\.(aw-[A-Za-z0-9_-]+)", css))


@pytest.mark.parametrize("cls", sorted(_emitted_classes()))
def test_every_rendered_class_is_styled_on_both_pages(cls):
    main, hist = _css_classes(MAIN), _css_classes(HIST)
    assert cls in main, (
        "renderReport พ่นคลาส .%s แต่ artwork_check.html ไม่มีกฎให้" % cls)
    assert cls in hist, (
        "renderReport พ่นคลาส .%s แต่ artwork_check_history.html ไม่มีกฎให้ "
        "— หน้าประวัติใช้ตัวเรนเดอร์เดียวกัน กล่องนี้จะโผล่แบบไม่มีกรอบ" % cls)


def test_shared_class_rules_are_identical():
    """กฎของคลาสที่ใช้ร่วมกันต้องเหมือนกันเป๊ะ ไม่งั้นหน้าประวัติจะหน้าตา
    ต่างออกไปโดยไม่ได้ตั้งใจ. ยกเว้นรายการที่ตั้งใจให้ต่าง (ดู ALLOW)."""
    ALLOW = {"aw-defect", "aw-defect-class"}   # history ย่อรูป/ระยะโดยตั้งใจ

    def rules(path, cls):
        css = re.sub(r"/\*.*?\*/", "", _read(path), flags=re.S)
        found = re.findall(r"\.%s\s*\{([^}]*)\}" % re.escape(cls), css)
        return [" ".join(x.split()) for x in found]

    for cls in sorted(_emitted_classes() - ALLOW):
        a, b = rules(MAIN, cls), rules(HIST, cls)
        if a and b:
            assert a == b, (
                "กฎของ .%s ไม่ตรงกันระหว่างสองหน้า:\n  main   : %s\n"
                "  history: %s" % (cls, a, b))


def test_guard_still_protects_history_page():
    """หน้าประวัติโหลด artwork_check.js ทั้งไฟล์ — ถ้า guard หายไป โค้ดที่
    อ้าง element ของหน้าตรวจจะพังทั้งหน้า (renderReport ไม่ถูกตั้งด้วย)."""
    js = _read(JS)
    guard = js.find('if (!$("awFile")) return;')
    assert guard > 0
    for name in ("awRenderReport", "awCoverageHtml", "awEsc", "awApi",
                 "awHlRiskText", "awRenderTextTable", "awWireZoomable"):
        pos = js.find("window.%s =" % name)
        assert 0 < pos < guard, (
            "window.%s ถูกตั้งหลัง guard — หน้าประวัติจะเรียกไม่ได้" % name)
