"""``inspectors.ocr_n8n`` — แกะคำตอบจาก N8N/Gemini และปฏิเสธของที่ไม่ใช่ผล OCR.

ที่มา: ผู้ใช้รายงานว่า "มีตัวอักษรแปลก ๆ โผล่ออกมาทั้งที่ของจริงไม่มี".
ไล่แล้วพบ 2 ทางที่ทำให้ **ขยะกลายเป็นข้อความบนฉลาก** โดยไม่มี error:

1. Gemini ครอบคำตอบด้วยรั้ว markdown ```json{...}``` → ``resp.json()`` พัง →
   โค้ดเดิมเอา **ทั้งก้อนรวมรั้ว** ไปเป็น text ⇒ คำว่า ``json`` ``text``
   ``blocks`` และวงเล็บปีกกาเข้าไปเทียบใน MISMATCH/SPELL
2. N8N/reverse-proxy คืน **หน้า HTML** (workflow ไม่ได้ Activate / path ผิด)
   → ``<!DOCTYPE html><title>Error...`` กลายเป็นข้อความบนฉลาก

ทั้งคู่ไม่เคยขึ้น error เลย — เป็น "ผลที่ผิดแบบมั่นใจ" ตรงตามกฎเหล็กข้อ 2.
"""

import json

import pytest

import config
from inspectors import ocr_n8n


WANT = "INGREDIENTS TUNA WATER SALT"


class FakeResp:
    def __init__(self, body, ctype="application/json", status=200):
        self.text = body
        self.content = body.encode()
        self.status_code = status
        self.headers = {"Content-Type": ctype}

    def json(self):
        return json.loads(self.text)      # ให้ ValueError เองเมื่อไม่ใช่ JSON

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError("HTTP %d" % self.status_code)


def call(monkeypatch, body, ctype="application/json", status=200):
    monkeypatch.setattr(ocr_n8n.requests, "post",
                        lambda *a, **k: FakeResp(body, ctype, status))
    return ocr_n8n.ocr_image(b"\xff\xd8\xffJPEG",
                             url="http://x/webhook/ocr", timeout=1)


# ── รูปแบบคำตอบที่ต้องแกะให้ได้ ──────────────────────────────────────

@pytest.mark.parametrize("body,ctype", [
    (json.dumps({"text": WANT, "blocks": []}), "application/json"),
    ("```json\n" + json.dumps({"text": WANT}) + "\n```", "text/plain"),
    ("```\n" + json.dumps({"text": WANT}) + "\n```", "text/plain"),
    (json.dumps([{"text": WANT, "blocks": []}]), "application/json"),
    (json.dumps({"data": json.dumps({"text": WANT})}), "application/json"),
    (json.dumps({"data": "```json\n" + json.dumps({"text": WANT}) + "\n```"}),
     "application/json"),
    (json.dumps({"output": {"text": WANT}}), "application/json"),
    (json.dumps({"text": json.dumps({"text": WANT})}), "application/json"),
    (json.dumps({"text": "```json\n" + json.dumps({"text": WANT}) + "\n```"}),
     "application/json"),
    (WANT, "text/plain"),
])
def test_all_real_world_shapes_yield_clean_text(monkeypatch, body, ctype):
    r = call(monkeypatch, body, ctype)
    assert r["text"].strip() == WANT
    assert not r.get("error")


def test_fenced_json_no_longer_leaks_markup(monkeypatch):
    """เคสหลักของบั๊ก: เดิมได้ '```json {\"text\": ...' ทั้งก้อน."""
    r = call(monkeypatch, "```json\n" + json.dumps({"text": WANT}) + "\n```",
             "text/plain")
    assert "```" not in r["text"]
    assert "blocks" not in r["text"]
    assert "{" not in r["text"]


def test_blocks_survive(monkeypatch):
    body = json.dumps({"text": WANT, "blocks": [
        {"text": "INGREDIENTS", "bbox": [1, 2, 3, 4], "conf": 0.9}]})
    r = call(monkeypatch, body)
    assert len(r["blocks"]) == 1
    assert r["blocks"][0]["bbox"] == [1.0, 2.0, 3.0, 4.0]


# ── ของที่ต้องปฏิเสธ ────────────────────────────────────────────────

HTML = "<!DOCTYPE html><html><body>Workflow could not be started</body></html>"


def test_html_error_page_is_rejected(monkeypatch):
    r = call(monkeypatch, HTML, "text/html")
    assert r["error"]
    assert r["text"] == ""
    assert r["stub"] is True


def test_html_detected_even_with_json_content_type(monkeypatch):
    """proxy ที่ตั้ง content-type ผิดก็ยังต้องจับได้จากเนื้อหา."""
    r = call(monkeypatch, HTML, "application/json")
    assert r["error"]


def test_strict_flag_off_restores_old_behaviour(monkeypatch):
    monkeypatch.setattr(config, "N8N_OCR_STRICT_RESPONSE", False)
    r = call(monkeypatch, HTML, "text/html")
    assert not r.get("error")
    assert r["text"] == HTML          # เดิมเป๊ะ: เอาเนื้อหาดิบมาเป็นข้อความ


def test_plain_text_is_kept_but_flagged(monkeypatch):
    """บาง workflow ตั้งให้คืน plain text จริง — ห้ามทำให้พัง แต่ต้องติดธง."""
    r = call(monkeypatch, "Error: The workflow has issues", "text/plain")
    assert r["text"] == "Error: The workflow has issues"
    assert r.get("warning")
    assert not r.get("error")


# ── ธง warning ต้องไปถึงผู้ตรวจ ไม่ใช่ถูกทิ้ง ─────────────────────────

def test_warning_reaches_zone_result(monkeypatch):
    """เดิม ocr_image ตั้ง warning ไว้แต่ไม่มีใครอ่าน → หายเงียบ."""
    from artwork_check import ocr as aocr
    import numpy as np

    class Doc:
        is_pdf = False
        def embedded_text(self, bbox): return ""
        def render_zone(self, bbox, dpi=None, max_side=None):
            return np.zeros((80, 240, 3), np.uint8)

    monkeypatch.setattr(aocr.vertex_client, "is_enabled", lambda: True)
    monkeypatch.setattr(aocr.vertex_client, "ocr_image", lambda *a, **k: {
        "text": "SOMETHING", "blocks": [], "stub": False, "engine": "n8n",
        "warning": "N8N ตอบกลับไม่ใช่ JSON"})
    out = aocr.read_zone(Doc(), {"id": "z1", "type": "panel",
                                 "bbox": [0.1, 0.1, 0.3, 0.2]})
    assert "JSON" in (out.get("note") or "")
    assert not out.get("error")       # อ่านได้ ไม่ใช่ UNREADABLE


# ── retry ───────────────────────────────────────────────────────────

class Flaky:
    def __init__(self, plan):
        self.plan, self.hits = plan, 0

    def __call__(self, *a, **k):
        code = self.plan[min(self.hits, len(self.plan) - 1)]
        self.hits += 1
        return FakeResp(json.dumps({"text": WANT}) if code == 200 else "boom",
                        status=code)


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
    monkeypatch.setattr(config, "N8N_OCR_RETRY_WAIT_S", 0.0)


@pytest.mark.parametrize("plan,retries,hits,ok", [
    ([200], 1, 1, True),              # สำเร็จครั้งแรก = ไม่ยิงซ้ำ
    ([500, 200], 1, 2, True),         # 5xx ชั่วคราว = ลองใหม่แล้วได้
    ([500, 500], 1, 2, False),
    ([404], 1, 1, False),             # workflow ไม่ active = ยิงซ้ำไร้ผล
    ([413], 1, 1, False),             # payload ใหญ่ = ยิงซ้ำไร้ผล
    ([500, 500, 200], 2, 3, True),
    ([500, 200], 0, 1, False),        # ปิด retry = พฤติกรรมเดิมเป๊ะ
])
def test_retry_only_for_transient_failures(monkeypatch, plan, retries, hits, ok):
    monkeypatch.setattr(config, "N8N_OCR_RETRIES", retries)
    f = Flaky(plan)
    monkeypatch.setattr(ocr_n8n.requests, "post", f)
    r = ocr_n8n.ocr_image(b"\xff\xd8\xffJPEG", url="http://x/w", timeout=1)
    assert f.hits == hits
    assert (not r.get("error")) is ok


def test_connection_error_retries_then_fails(monkeypatch):
    import requests
    monkeypatch.setattr(config, "N8N_OCR_RETRIES", 2)
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(ocr_n8n.requests, "post", boom)
    r = ocr_n8n.ocr_image(b"\xff\xd8\xffJPEG", url="http://x/w", timeout=1)
    assert calls["n"] == 3
    assert r["error"]


# ── helper ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,want", [
    ("```json\n{\"a\":1}\n```", '{"a":1}'),
    ("```\n{\"a\":1}\n```", '{"a":1}'),
    ("{\"a\":1}", '{"a":1}'),
    ("", ""),
    ("no fence here", "no fence here"),
])
def test_strip_fence(raw, want):
    assert ocr_n8n._strip_fence(raw) == want


@pytest.mark.parametrize("body,ctype,want", [
    (HTML, "text/html", True),
    (HTML, "application/json", True),
    ("<html><body>x</body></html>", "", True),
    ("INGREDIENTS <500 mg", "text/plain", False),   # < กลางข้อความ ≠ HTML
    ("", "application/json", False),
])
def test_looks_like_html(body, ctype, want):
    assert ocr_n8n._looks_like_html(body, ctype) is want
