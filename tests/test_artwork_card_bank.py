# -*- coding: utf-8 -*-
"""ธนาคารเคสจริง (P3, 26 ก.ย. 2026) — การ์ดบนรายงานต้องไม่เปลี่ยนเงียบ ๆ

ข้อความ OCR จริงของสถานี 10 ชุด (Friskies 4 รอบ · John West 4 รอบ · AvoDerm 2 ชุด)
เล่นซ้ำด้วย ``run_all_checks`` ตัวจริง. ล็อก 2 ชั้น:

1. **การ์ดทุกใบ** (คลาส · โซน · ระดับ · ข้อความ) ตรงกับ ``expected.json``
   ทั้งค่าปัจจุบัน และเมื่อปิดธงใหม่ทั้งหมด (= พฤติกรรมก่อน 26 ก.ย. เป๊ะ)
2. **ความจริงของงาน** — ของจริงต้องคง critical เสมอ (ไม่ขึ้นกับ expected)

แก้โค้ดแล้วเคสเปลี่ยน ⇒ ``py -3.9 verify_cards.py --show`` ดูว่าเปลี่ยนอะไร
ถ้าตั้งใจ ⇒ ``--update`` แล้วอธิบายใน commit
"""
import json
import os

import pytest

import verify_cards as VC
from artwork_check import config

EXP = json.load(open(VC.EXPECTED, encoding="utf-8"))


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    # ค่าที่ ship — ไม่ให้ env ของเครื่องที่รันเทสต์เปลี่ยนผล
    for k in ("NUMBER_CONTEXT", "TEXT_WITNESS", "FUSED_SCRIPT_NOTE",
              "TEXT_WITNESS_STRICT", "TEXT_PAIR_CHAR_FALLBACK",
              "TEXT_PAIR_NUMERIC", "TEXT_NUMBER_STRICT", "TEXT_PAIR_BY_RUN",
              "TEXT_CASE_SENSITIVE"):
        monkeypatch.setattr(config, k, True)
    monkeypatch.setattr(config, "TEXT_WITNESS_SEVERITY", "warning")


def test_bank_has_every_case_and_nothing_extra():
    assert sorted(EXP) == VC.cases()
    assert len(VC.cases()) == 10


@pytest.mark.parametrize("name", VC.cases())
def test_cards_match_expected(name):
    assert VC.cards(name) == EXP[name]["current"]


@pytest.mark.parametrize("name", VC.cases())
def test_flags_off_is_the_previous_behaviour(name):
    assert VC.cards(name, legacy=True) == EXP[name]["legacy"]


@pytest.mark.parametrize("name", VC.cases())
def test_every_case_has_a_written_truth(name):
    assert EXP[name]["truth"].strip()


# ── ความจริงของงาน (ไม่อิง expected.json) ─────────────────────────────

def _crit(name):
    return [d for d in VC.cards(name) if d["severity"] == "critical"]


@pytest.mark.parametrize("name", ["friskies1", "friskies2", "friskies3",
                                  "friskies4"])
def test_friskies_cfpr_placeholder_stays_critical(name):
    assert any("CFPR" in d["found"] + d["reference"] for d in _crit(name))


@pytest.mark.parametrize("name", ["friskies2", "friskies4"])
def test_friskies_prompt_v2_rounds_leave_only_the_real_card_critical(name):
    c = _crit(name)
    assert len(c) == 1 and "CFPR" in c[0]["found"] + c[0]["reference"]


@pytest.mark.parametrize("name", ["friskies2", "friskies4"])
def test_friskies_spacing_split_is_one_card_now(name):
    """``56g``/``56 g`` เคยแตกเป็น 2 การ์ด "พบเฉพาะ" (P2)"""
    kor = [d for d in VC.cards(name) if "별도" in d["found"] + d["reference"]]
    assert len(kor) == 1 and kor[0]["found"] and kor[0]["reference"]


@pytest.mark.parametrize("name", ["johnwest1", "johnwest2", "johnwest3",
                                  "johnwest4"])
def test_johnwest_sodium_is_one_card_with_both_values(name):
    """20% → 24%: เดิมเห็นแค่ฝั่ง 24% (``20%`` ถูกยกโทษเพราะมี ``2000``
    ในอีกแผง) — ตอนนี้เป็นการ์ดเดียวที่มีทั้งสองค่า (P4 + จับคู่ตัวเลข)"""
    na = [d for d in _crit(name)
          if d["found"].startswith("20") and d["reference"].startswith("24")]
    assert len(na) == 1


@pytest.mark.parametrize("name", ["johnwest2", "johnwest3"])
def test_johnwest_good_rounds_have_exactly_one_card(name):
    assert len(VC.cards(name)) == 1


def test_avoderm_both_real_differences_stay_critical():
    c = _crit("avoderm")
    assert any(d["class"] == "MISMATCH_CASE" for d in c)
    assert any("Copper Sulfate" in d["found"]
               and "Copper Sulphate" in d["reference"]
               for d in c if d["class"] == "MISMATCH_PANELS")
    assert len(c) == 2


def test_no_card_disappears_only_severities_or_merges_change():
    """ของใหม่ต้องไม่ **ลบ** ความต่าง — การ์ดที่หายต้องถูกรวมเข้าการ์ดอื่น
    (ข้อความของมันยังอยู่บนรายงาน)"""
    for n in VC.cases():
        now = VC.cards(n)
        texts = " ".join(d["found"] + " " + d["reference"] for d in now)
        for d in EXP[n]["legacy"]:
            for t in (d["found"], d["reference"]):
                assert not t or t in texts, (n, t)


def test_avoderm_three_groups_has_only_the_three_real_cards():
    """สถานี 26 ก.ย.: บาร์โค้ดเหมือนกันทั้งสองไฟล์ (OCR แบ่งกลุ่มตัวเลขคนละ
    แบบ) — P4 รุ่นแรกขึ้นการ์ดปลอม 3 ใบ · ที่อยู่ต่างจริง (Park/USA)"""
    c = VC.cards("avoderm3g")
    assert not [d for d in c if "52907" in d["found"] + d["reference"]
                or "00241" in d["found"] + d["reference"]]
    assert any("Irwindale Park" in d["reference"] for d in c)
    assert len(c) == 3 and all(d["severity"] == "critical" for d in c)
