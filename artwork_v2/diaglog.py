"""Log สำหรับวิเคราะห์ — ข้อความเดียวที่ผู้ใช้กด "คัดลอก" แล้วส่งให้ Claude ได้ทันที

หลัก: **ทุกค่าที่ใช้ตัดสินต้องอยู่ใน Log** (ค่าตั้ง · ไฟล์ · โซน · ภาพที่ส่ง · คำขอ ·
ผลดิบสรุป · การจับคู่ · จุดต่าง · การอ่านซ้ำ · เหตุผลของผลตัดสิน) และ **ไม่มี
API key** (ผู้เรียกผ่าน ``keystore.redact`` อีกชั้นก่อนบันทึก)
"""

from __future__ import annotations

from typing import List

from . import config


def _f(v, nd=3):
    if v is None:
        return "-"
    if isinstance(v, float):
        return ("%." + str(nd) + "f") % v
    return str(v)


def _box(b):
    return "-" if not b else "[%d,%d,%d,%d]" % tuple(int(round(x)) for x in b)


def _q(s, n=160):
    s = (s or "").replace("\n", " ⏎ ")
    return '"' + (s if len(s) <= n else s[:n] + "…") + '"'


def build_text(r: dict) -> str:
    L: List[str] = []
    a = L.append
    a("=== ARTWORK V2 · CLOUD VISION OCR · DIAGNOSTIC LOG ===")
    a("version=%s job=%s run=%s at=%s owner=%s" % (r["version"], r["job"], r["run"], r["at"],
                                                   r.get("owner") or "-"))
    a("VERDICT=%s (%s)" % (r["verdict"], r["verdict_th"]))
    for x in r["reasons"]:
        a("  - " + x)
    st = r["stage"]
    a("time_ms: render=%s vision=%s compare=%s reread=%s ai=%s total=%s" % (
        st.get("render_ms"), st.get("vision_ms"), st.get("compare_ms"),
        st.get("reread_ms"), st.get("ai_ms", "-"), st.get("total_ms")))
    a("sharpness=%s (ภาพที่ส่ง: %s)" % (
        r.get("sharpness", "standard"),
        "คมสูงสุด" if r.get("sharpness") == "max" else "มาตรฐาน 400 dpi"))
    a("color_mode=%s (สีของภาพที่ส่ง: %s)" % (
        r.get("color_mode", "color"),
        {"gray": "เทา", "bw": "ขาวดำ (ตัดเกณฑ์)"}.get(r.get("color_mode"), "สี")))
    k = r["key"]
    a("api_key: source=%s masked=%s length=%s" % (k["source"] or "NONE", k["masked"] or "-",
                                                  k["length"]))

    a("")
    a("[SETTINGS]")
    for name, val in r["settings"].items():
        a("  %s=%s" % (name, val))

    a("")
    a("[FILES]")
    for s in ("a", "b"):
        f = (r.get("files") or {}).get(s) or {}
        extra = ""
        if f.get("type") == "pdf":
            extra = "pages=%s page_mm=%s" % (f.get("pages"), (f.get("pages_mm") or [None])[0])
        else:
            extra = "image_px=%s raw_px=%s exif_orientation=%s" % (
                f.get("image_px"), f.get("raw_px"), f.get("exif_orientation"))
        a("  %s: name=%s type=%s bytes=%s sha1=%s %s" % (
            s.upper(), f.get("name"), f.get("type"), f.get("bytes"),
            (f.get("sha1") or "")[:12], extra))

    a("")
    a("[VISION REQUESTS]")
    if not r["calls"]:
        a("  (ไม่มีการยิง — ดู warnings/error)")
    for c in r["calls"]:
        a("  #%d phase=%s images=%s json_bytes=%s http=%s attempts=%s ms=%s model=%s at=%s%s" % (
            c["index"], c["phase"], ",".join(c["images"]), c["json_bytes"], c["status"],
            c["attempts"], c["ms"], c.get("model_requested"), c.get("at"),
            (" ERROR=" + c["error"]) if c.get("error") else ""))

    for p in r["pairs"]:
        a("")
        a("[PAIR %d] verdict=%s coverage=%s (A=%s B=%s) same_request=%s methods=%s" % (
            p["n"], p.get("verdict"), _f(p.get("coverage")), _f(p.get("coverage_a")),
            _f(p.get("coverage_b")), p.get("same_request"), p.get("pair_methods")))
        for s in ("a", "b"):
            sd = p["sides"][s]
            rd = sd.get("render", {})
            en = sd.get("encode", {})
            a("  %s: page=%d bbox=%s render_dpi=%s zone_mm=%s render_px=%s image_scale=%s "
              "sent_px=%s jpeg_q=%s downscale=%s jpeg_bytes=%s sha1=%s req=%s ok=%s%s" % (
                  s.upper(), sd["page"] + 1, sd["bbox"], rd.get("dpi", "-"),
                  rd.get("zone_mm", "-"), rd.get("px"), rd.get("image_scale", "-"),
                  sd["sent_px"], en.get("quality"), en.get("downscale"), sd["jpeg_bytes"],
                  sd["sha1"], sd.get("request_index"), sd.get("ok"),
                  (" ERROR=" + sd["error"]) if sd.get("error") else ""))
            if sd.get("rotate"):
                a("     rotate: %d° ตามเข็ม (ภาพที่ส่ง + พิกัดกรอบทุกตัวอยู่ในแนวที่หมุนแล้ว)" % sd["rotate"])
            if sd.get("ignore"):
                a("     ignore (พื้นที่ยกเว้น · สัดส่วนของหน้า %d กรอบ): %s" % (len(sd["ignore"]), sd["ignore"]))
            if rd.get("color_mode") in ("gray", "bw"):
                a("     color: mode=%s%s" % (rd["color_mode"], (" block=%spx C=%s ink=%.1f%%" % (
                    rd.get("bw_block_px"), rd.get("bw_c"), 100.0 * (rd.get("ink_frac") or 0)))
                    if rd["color_mode"] == "bw" else ""))
            if rd.get("sharpness"):
                tries = " ".join("%.0f:%dk%s" % (t["dpi"], t["bytes"] // 1000,
                                                 "" if t["fit"] else "(เกินงบ)")
                                 for t in rd.get("tries", []))
                a("     sharp: mode=%s base_dpi=%s → dpi=%s (×%s) budget=%sk tries=[%s]" % (
                    rd["sharpness"], rd.get("base_dpi", rd.get("dpi", "-")), rd.get("dpi", "-"),
                    rd.get("gain", 1.0), (rd.get("budget_bytes") or 0) // 1000 or "-", tries))
            stt = sd.get("stats")
            if stt:
                a("     ocr: blocks=%s by_type=%s paragraphs=%s words=%s symbols=%s lines=%s "
                  "chars=%s conf_mean=%s conf_min=%s low_conf(<0.6)=%s langs=%s breaks=%s "
                  "soft_hyphen_lines=%s angles=%s skipped_blocks=%s text_len=%s pages=%s" % (
                      stt["blocks"], stt["blocks_by_type"], stt["paragraphs"], stt["words"],
                      stt["symbols"], stt["lines"], stt["chars"], _f(stt["conf_mean"]),
                      _f(stt["conf_min"]), _f(stt["low_conf_frac"]), stt["langs"],
                      stt["breaks"], stt["soft_hyphen_lines"], stt["angles"],
                      stt["skipped_blocks"], stt["text_len"],
                      [(pg.get("width"), pg.get("height")) for pg in stt["page_info"]]))
        if p.get("unreadable"):
            a("  UNREADABLE — ฝั่งที่อ่านไม่สำเร็จทำให้ไม่ได้เทียบคู่นี้")
            continue
        un = p.get("unpaired") or {}
        rl = p.get("reflow_lines") or {}
        a("  pairing: line_pairs=%d unpaired_a=%s unpaired_b=%s reflow_lines_a=%s "
          "reflow_lines_b=%s reflow_edges=%s" % (
              len(p.get("line_pairs") or []), un.get("a"), un.get("b"), rl.get("A"),
              rl.get("B"), [e["side"] + ":" + e["text"] for e in p.get("reflow_edges") or []]))
        rm = p.get("row_merges") or []
        a("  row_merges (%d) — ต่อแถวที่ OCR ตัดตรงเส้นตกแต่ง:" % len(rm))
        for m in rm:
            a("     %s: %s + %s" % (m["side"], _q(m["left"], 80), _q(m["right"], 80)))
            if m.get("via") == "recompose":
                a("        ↳ ต่อชิ้นที่ Vision แยก — ต่อแล้วตรงกับอีกฝั่งทุกตัวอักษร: %s"
                  % _q(m.get("evidence") or "", 80))
            if m.get("via") == "cross_side":
                # บรรทัดแยก — รูปแบบบรรทัดบนต้องคงเดิม (ตัวโหลดชุดข้อมูลอ่านมัน)
                a("        ↳ ต่อด้วยหลักฐานจากอีกฝั่ง (ใส่ … แทนจุดไข่ปลาที่ OCR ทิ้ง): %s"
                  % _q(m.get("evidence") or "", 80))
        rs = p.get("row_splits") or []
        if rs:
            # รูปแบบบรรทัดนี้ต้องคงเดิม — ตัวโหลดชุดข้อมูลต่อชิ้นกลับเป็นบรรทัดที่ Vision ส่งมา
            a("  row_splits (%d) — แยกบรรทัดที่ Vision รวมข้ามคอลัมน์:" % len(rs))
            for m in rs:
                a("     %s: %s ‖ %s" % (m["side"], _q(m["left"], 400), _q(m["right"], 400)))
                a("        ↳ ชิ้นหนึ่งตรงกับอีกฝั่งทุกตัวอักษรที่ตำแหน่งเดียวกัน: %s" % _q(m.get("evidence") or "", 80))
        cl = p.get("curved_lines") or {}
        a("  curved_lines (เอียง > %s° จากแนวหลัก + เศษติดกัน): A=%s B=%s" % (
            _f(config.TILT_ANGLE, 0), cl.get("A"), cl.get("B")))

        def finding(f, ind="   "):
            a("%sF%s %s %s method=%s score=%s%s" % (
                ind, f.get("id", "-"), f["severity"].upper(), f["class"], f.get("pair_method"),
                _f(f.get("pair_score")), " curved" if f.get("curved") else ""))
            for s_ in ("a", "b"):
                a("%s   %s line=%s frag=%s word=%s conf=%s box=%s" % (
                    ind, s_.upper(), f[s_]["line"], _q(f[s_]["frag"], 60),
                    _q(f.get("word_" + s_), 60), _f(f[s_]["conf"]), _box(f[s_]["box"])))
                a("%s     text=%s" % (ind, _q(f[s_]["text"])))
            for n in f.get("notes") or []:
                a("%s   note: %s" % (ind, n))
            ai = f.get("ai") or {}
            if ai.get("verdict"):
                a("%s   ai: verdict=%s confidence(vision)=%s words=%s/%s" % (
                    ind, ai["verdict"], _f(f.get("confidence")), ai.get("a_words", "-"),
                    ai.get("b_words", "-")))
                if ai.get("reason"):
                    a("%s   ai_reason: %s" % (ind, _q(ai["reason"], 300)))
                if ai.get("suggestion"):
                    a("%s   ai_suggestion: %s" % (ind, _q(ai["suggestion"], 300)))
            for m in f.get("members") or []:
                finding(m, ind + "    · ")

        a("  findings (%d):" % len(p.get("findings") or []))
        for f in p.get("findings") or []:
            finding(f)
        a("  debris — เศษอักขระ / ขอบโซน ไม่นับในผลตัดสิน (%d):" % len(p.get("debris") or []))
        for f in p.get("debris") or []:
            finding(f)
        if p.get("relocated"):
            a("  relocated — ข้อความมีอยู่ในอีกฝั่งตรงตำแหน่งเดียวกัน (OCR จัดบรรทัดต่างกัน) ไม่นับ (%d):"
              % len(p["relocated"]))
            for f in p["relocated"]:
                finding(f)
        if p.get("pixel_same"):
            a("  pixel_same — ภาพเหมือนกันทุกพิกเซล (OCR อ่านต่างเอง) ไม่นับ (%d):" % len(p["pixel_same"]))
            for f in p["pixel_same"]:
                finding(f)
        if p.get("excluded"):
            a("  excluded — อยู่ในพื้นที่ยกเว้นที่กำหนดในโซน ไม่นับ (%d):" % len(p["excluded"]))
            for f in p["excluded"]:
                finding(f)
        if p.get("lowmark"):
            a("  lowmark — เครื่องหมายเดี่ยวที่ OCR อ่านไม่มั่นใจ (ฝั่งเดียว) ไม่นับ (%d):" % len(p["lowmark"]))
            for f in p["lowmark"]:
                finding(f)
        if p.get("algo_only") is not None:
            if p.get("raw_lines"):
                a("  algo_only — ผลของอัลกอริทึม ไว้เทียบ ไม่นับ (โหมด raw · line= อ้างบรรทัด OCR lines) (%d):"
                  % len(p["algo_only"]))
            else:
                a("  algo_only — อัลกอริทึมพบแต่ AI ไม่ได้ระบุ ไม่นับ (%d):" % len(p["algo_only"]))
            for f in p["algo_only"]:
                finding(f)
        if p.get("ai_dismissed") is not None:
            a("  ai_dismissed — AI ตัดสินว่าเป็นสัญญาณรบกวน ไม่นับ (%d):" % len(p["ai_dismissed"]))
            for f in p["ai_dismissed"]:
                finding(f)
        for s in ("a", "b"):
            lines = (p.get("lines") or {}).get(s) or []
            a("  OCR lines %s (%d):" % (s.upper(), len(lines)))
            for i, ln in enumerate(lines[:config.LOG_MAX_LINES]):
                a("   %s%03d conf=%s min=%s ang=%s box=%s %s%s" % (
                    s.upper(), i, _f(ln["conf"], 2), _f(ln["conf_min"], 2),
                    _f(ln.get("angle"), 0), _box(ln["box"]), _q(ln["text"], 200),
                    " [soft-hyphen]" if ln.get("soft_hyphen") else ""))
            if len(lines) > config.LOG_MAX_LINES:
                a("   … ตัดเหลือ %d บรรทัด (ทั้งหมด %d)" % (config.LOG_MAX_LINES, len(lines)))
        for s in ("a", "b"):
            # โหมด raw: บรรทัดดิบที่ส่งให้ AI — findings/ai_dismissed (line=) อ้างชุดนี้
            # ขึ้นต้น "r" โดยตั้งใจ — ตัวโหลด Log ของชุดทดสอบอ่านเฉพาะบรรทัด "A000 conf=…"
            lines = (p.get("raw_lines") or {}).get(s) or []
            if not lines:
                continue
            a("  RAW lines %s ที่ส่งให้ AI (%d):" % (s.upper(), len(lines)))
            for i, ln in enumerate(lines[:config.LOG_MAX_LINES]):
                a("   r%s%03d conf=%s min=%s ang=%s box=%s %s" % (
                    s.upper(), i, _f(ln["conf"], 2), _f(ln["conf_min"], 2),
                    _f(ln.get("angle"), 0), _box(ln["box"]), _q(ln["text"], 200)))
            if len(lines) > config.LOG_MAX_LINES:
                a("   … ตัดเหลือ %d บรรทัด (ทั้งหมด %d)" % (config.LOG_MAX_LINES, len(lines)))

    rr = r.get("reread") or {}
    a("")
    a("[REREAD] enabled=%s candidates=%s crops=%s done=%s confirmed=%s downgraded=%s "
      "skipped_cap=%s errors=%s" % (rr.get("enabled"), rr.get("candidates"), rr.get("crops"),
                                    rr.get("done"), rr.get("confirmed"), rr.get("downgraded"),
                                    rr.get("skipped_cap"), rr.get("errors")))
    for it in rr.get("items") or []:
        a("  %s classes=%s results=%s A=%s B=%s found_in_other=%s crop_findings=%s err=%s" % (
            it["crops"]["a"]["image"].split("_")[0], it.get("classes"), it.get("results"),
            {k: it["crops"]["a"].get(k) for k in ("px", "dpi", "jpeg_bytes")},
            {k: it["crops"]["b"].get(k) for k in ("px", "dpi", "jpeg_bytes")},
            it.get("found_in_other", "-"), it.get("crop_findings", "-"), it.get("error", "-")))
        if it.get("text_a") is not None:
            a("     crop A: %s" % _q(it.get("text_a"), 200))
            a("     crop B: %s" % _q(it.get("text_b"), 200))

    px = r.get("pixel") or {}
    a("")
    a("[PIXEL] enabled=%s line_mode=%s raster=%s same=%s diff=%s unverifiable=%s skipped=%s ms=%s%s%s" % (
        px.get("enabled"), px.get("line_mode"), px.get("raster", "-"), px.get("same", 0), px.get("diff", 0),
        px.get("unverifiable", 0), px.get("skipped", 0), px.get("ms", 0),
        (" pymupdf=" + str(px["pymupdf"])) if px.get("pymupdf") else "",
        (" reason=" + px["reason"]) if px.get("reason") else ""))
    if px.get("diff_raster"):
        a("  diff_raster=%s — DIFF บนคู่ที่มีภาพสแกน ไม่ขึ้นป้าย \"ภาพยืนยันว่าต่าง\" (PIXEL_RASTER_NOTE)"
          % px["diff_raster"])
    for pl in px.get("pairs") or []:
        a("  pair %s align=%s%s" % (pl.get("n"), pl.get("align"),
                                    (" error=" + str(pl["error"])) if pl.get("error") else ""))
        if pl.get("raster_check"):
            a("    raster_check: %s" % " · ".join(
                "%s=%s" % (k.upper(), v) for k, v in sorted(pl["raster_check"].items())))
        if pl.get("raster"):
            a("    raster (ภาพสแกนล้วน — เทียบที่ความละเอียดจริง + เบลอ): %s" % ", ".join(
                "%s=%s" % (k.upper(), ("%gdpi cover=%g" % (v["dpi"], v["cover"])) if v else "-")
                for k, v in sorted(pl["raster"].items())))
        for it in pl.get("items") or []:
            a("    F%s %s checks=%s ms=%s" % (it.get("id"), it.get("status"), it.get("n_checks"), it.get("ms")))
        for e in pl.get("errors") or []:
            a("    error %s" % e)

    ar = r.get("ai") or {}
    a("")
    a("[AI REVIEW] mode=%s url=%s pairs_ok=%s pairs_failed=%s" % (
        ar.get("mode", "off"), ar.get("url") or "-", ar.get("pairs_ok", 0),
        ar.get("pairs_failed", 0)))
    for p in r["pairs"]:
        x = p.get("ai") or {}
        if not x or x.get("status") == "off":
            continue
        a("  pair %d: status=%s engine=%s http=%s ms=%s attempts=%s request_bytes=%s "
          "candidates=%s%s" % (p["n"], x.get("status"), x.get("engine", "-"), x.get("http"),
                               x.get("ms"), x.get("attempts"), x.get("request_bytes"),
                               x.get("candidates"),
                               (" ERROR=" + x["error"]) if x.get("error") else ""))
        if x.get("status") != "ok":
            continue
        vc = x.get("vision_conf") or {}
        a("     ref_accuracy=%s items=%s/%s reviews=%s/%s reviewed=%s/%s extra_added=%s "
          "extra_duplicate=%s extra_noise=%s vision_conf A=%s B=%s usage=%s" % (
              _f(x.get("ref_accuracy")), x.get("items_valid"), x.get("items_total"),
              x.get("reviews_valid"), x.get("reviews_total"), x.get("reviewed", "-"),
              x.get("reviewable", "-"), x.get("extra_added"), x.get("extra_duplicate"),
              x.get("extra_noise"), _f(vc.get("a")), _f(vc.get("b")), x.get("usage")))
        a("     recovered=%s equivalent=%s algo_red_kept=%s" % (
            x.get("recovered", "-"), x.get("items_equivalent", "-"), x.get("algo_red_kept", "-")))
        if x.get("mode") == "raw":
            a("     raw: algo_compare=%s coverage_ignored=%s (ผลของอัลกอริทึมพับไว้เทียบ · ความครอบคลุมไม่ใช้ตัดสิน)"
              % (x.get("algo_compare", "-"), p.get("coverage_ignored", False)))
        for eq in x.get("equivalent") or []:
            a("     equivalent %s (AI=%s): %s" % (eq.get("what"), eq.get("verdict"), eq.get("reason")))
        for bad in x.get("invalid") or []:
            a("     invalid %s: %s %s" % (bad.get("what"), bad.get("reason"),
                                         _q(bad.get("raw") or "", 200)))
        if x.get("summary"):
            a("     summary: %s" % _q(x["summary"], 1200))
        for sg in x.get("suggestions") or []:
            a("     suggestion: %s" % _q(sg, 400))

    a("")
    a("[WARNINGS] %d" % len(r.get("warnings") or []))
    for w in r.get("warnings") or []:
        a("  - " + w)
    a("=== END ===")
    return "\n".join(L)
