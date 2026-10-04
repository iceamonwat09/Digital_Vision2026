"""ลำดับงานหนึ่งรอบของ Artwork V2

    ตรวจโซน → เรนเดอร์ภาพโซน → เข้ารหัสให้พอดีขีด → Vision (คู่ A/B ในคำขอเดียว)
    → ประกอบบรรทัด → เทียบ → อ่านซ้ำแบบซูมเฉพาะจุดแดง (ปิดเป็นค่าเริ่มต้น)
    → AI ตรวจทานข้อความของ Vision (Gemini ผ่าน N8N) → ผลตัดสิน → Log

กติกาความปลอดภัย (กฎเหล็กของโปรเจกต์):
* ฝั่งใดอ่านไม่สำเร็จ = คู่นั้น "อ่านไม่ได้" **ห้ามเป็น PASS**
* การอ่านซ้ำ **ลดระดับได้อย่างเดียว** (แดง → เหลือง) ไม่ลบรายการ
* ทุกคำเตือน (ย่อภาพ · แยกคำขอ · เกินเพดาน) ถูกบันทึก ไม่เงียบ
"""

from __future__ import annotations

import json
import os
import shutil
import time
from difflib import SequenceMatcher
from typing import Callable, Dict, List, Optional

from . import VERSION, ai_review, compare, config, imaging, jobs, keystore, textmodel, vision_client
from . import diaglog

MAX_PAIRS = 8          # 16 ภาพ = เพดานต่อคำขอของ Vision

VERDICT_TH = {"PASS": "ผ่าน", "REVIEW": "ต้องตรวจทาน", "FAIL": "ไม่ผ่าน",
              "UNREADABLE": "อ่านไม่ได้"}


def parse_pairs(raw) -> List[dict]:
    """ตรวจรูปแบบโซนคู่จากหน้าเว็บ — ผิดรูปโยน ``ValueError`` พร้อมเหตุผล"""
    if not isinstance(raw, list) or not raw:
        raise ValueError("ยังไม่มีโซนคู่ — วาดโซนบน A และ B อย่างน้อย 1 คู่")
    if len(raw) > MAX_PAIRS:
        raise ValueError("โซนคู่ได้สูงสุด %d คู่ต่อรอบ" % MAX_PAIRS)
    out = []
    for n, p in enumerate(raw, 1):
        item = {}
        for s in ("a", "b"):
            z = (p or {}).get(s) or {}
            bb = imaging.clamp_bbox(z.get("bbox"))
            if bb is None:
                raise ValueError("คู่ที่ %d: โซนฝั่ง %s ไม่ถูกต้อง/เล็กเกินไป" % (n, s.upper()))
            try:
                page = int(z.get("page", 0))
            except (TypeError, ValueError):
                raise ValueError("คู่ที่ %d: หน้าไม่ถูกต้อง" % n)
            item[s] = {"page": page, "bbox": bb}
        out.append(item)
    return out


def _px_box_to_norm(box, zone_bbox, zw, zh, pad_x, pad_y):
    x, y, w, h = zone_bbox
    x0 = x + (box[0] - pad_x) / zw * w
    y0 = y + (box[1] - pad_y) / zh * h
    x1 = x + (box[2] + pad_x) / zw * w
    y1 = y + (box[3] + pad_y) / zh * h
    return imaging.clamp_bbox([x0, y0, x1 - x0, y1 - y0])


def verdict_of(pairs: List[dict]) -> tuple:
    reasons = []
    red = sum(1 for p in pairs for f in p.get("findings", []) if f["severity"] == "red")
    yellow = sum(1 for p in pairs for f in p.get("findings", []) if f["severity"] == "yellow")
    unread = [p["n"] for p in pairs if p.get("unreadable")]
    lowcov = [p["n"] for p in pairs if not p.get("unreadable")
              and (p.get("coverage") is None or p["coverage"] < config.COVERAGE_MIN)]
    if red:
        reasons.append("พบจุดต่างที่มั่นใจ %d จุด" % red)
    if unread:
        reasons.append("อ่านไม่ได้: คู่ที่ %s" % ", ".join(map(str, unread)))
    if yellow:
        reasons.append("จุดที่ไม่มั่นใจ %d จุด (ต้องดูด้วยตา)" % yellow)
    if lowcov:
        reasons.append("ความครอบคลุมต่ำกว่า %d%%: คู่ที่ %s"
                       % (round(config.COVERAGE_MIN * 100), ", ".join(map(str, lowcov))))
    if red:
        v = "FAIL"
    elif unread:
        v = "UNREADABLE"
    elif yellow or lowcov:
        v = "REVIEW"
    else:
        v = "PASS"
        reasons.append("ไม่พบจุดต่างในข้อความที่ตรวจ")
    return v, reasons


def _compact_lines(lines: List[dict]) -> List[dict]:
    return [{"text": l["text"], "box": [round(v, 1) for v in l["box"]] if l["box"] else None,
             "conf": None if l.get("conf_mean") is None else round(l["conf_mean"], 3),
             "conf_min": None if l.get("conf_min") is None else round(l["conf_min"], 3),
             "angle": l.get("angle"), "langs": l.get("langs"),
             "soft_hyphen": l.get("soft_hyphen"), "block_type": l.get("block_type")}
            for l in lines]


def _crop_contains(lines: List[dict], target: str) -> float:
    """ความคล้ายสูงสุดของ ``target`` กับข้อความในภาพครอป (0..1)"""
    key = compare.diff_key(target)[0].casefold()
    if not key:
        return 0.0
    stream = "".join(compare.diff_key(l["text"])[0] for l in lines).casefold()
    if key in stream:
        return 1.0
    if not stream:
        return 0.0
    sm = SequenceMatcher(None, key, stream, autojunk=False)
    m = sum(b.size for b in sm.get_matching_blocks())
    return m / float(len(key))


def norm_sharpness(v) -> str:
    """ค่าที่ไม่รู้จัก/ไม่ส่งมา = ค่าตั้งของเครื่อง (``config.SHARPNESS``)"""
    v = str(v or "").strip().lower()
    return v if v in config.SHARPNESS_MODES else config.SHARPNESS


def run(job_id: str, raw_pairs, poster: Optional[Callable] = None,
        progress: Optional[Callable] = None, sharpness: Optional[str] = None,
        ai_mode: Optional[str] = None, ai_poster: Optional[Callable] = None) -> dict:
    """ตรวจหนึ่งรอบ · ล้มก่อนยิง Vision ⇒ ลบโฟลเดอร์รอบที่เพิ่งสร้าง (ไม่ทิ้งรอบว่างค้าง
    ให้หน้าเว็บเปิดแล้วไม่เห็นอะไร) · ล้มหลังยิงแล้ว ⇒ เก็บผลดิบใน ``raw/`` ไว้ไล่ปัญหา"""
    made: Dict[str, str] = {}
    try:
        return _run(job_id, raw_pairs, poster, progress, sharpness, ai_mode, ai_poster, made)
    except Exception:
        rd = made.get("rd")
        if rd and not os.path.isfile(os.path.join(rd, "result.json")):
            try:
                if not os.listdir(os.path.join(rd, "raw")):
                    shutil.rmtree(rd, ignore_errors=True)
            except OSError:
                pass
        raise


def _run(job_id, raw_pairs, poster, progress, sharpness, ai_mode, ai_poster,
         made: Dict[str, str]) -> dict:
    t_all = time.time()
    sharp = norm_sharpness(sharpness)
    ai_mode = ai_review.norm_mode(ai_mode)
    stage: Dict[str, int] = {}
    warnings: List[str] = []
    pairs_in = parse_pairs(raw_pairs)
    meta = jobs.meta(job_id)
    rd = jobs.new_run_dir(job_id)
    made["rd"] = rd
    run_name = os.path.basename(rd)
    key, key_src = keystore.get_key()

    def say(msg):
        if progress:
            try:
                progress(msg)
            except Exception:                          # noqa: BLE001
                pass

    # ── 1) เรนเดอร์ภาพโซน ────────────────────────────────────────────
    t0 = time.time()
    say("กำลังเตรียมภาพโซน %d คู่" % len(pairs_in))
    srcs = {s: jobs.source(job_id, s) for s in ("a", "b")}
    pairs: List[dict] = []
    groups = []
    for n, p in enumerate(pairs_in, 1):
        pr = {"n": n, "sides": {}}
        grp = []
        encoded = {}
        for s in ("a", "b"):
            z = p[s]
            got = None
            if sharp == "max":
                got = imaging.render_zone_sharp(srcs[s], z["page"], z["bbox"],
                                                imaging.pair_image_budget(2))
            if got is not None:
                sent, jpeg, rinfo = got
                einfo = {"warnings": [], "downscale": 1.0,
                         "quality": config.JPEG_QUALITIES[0]}
            else:
                img, rinfo = srcs[s].render_zone(z["page"], z["bbox"])
                rinfo["sharpness"] = "standard" if sharp == "standard" else (
                    "max→standard" if srcs[s].is_pdf else "source_pixels")
                jpeg, sent, einfo = imaging.fit_jpeg(img, config.MAX_IMAGE_BYTES)
                einfo["_img"] = img
            encoded[s] = [jpeg, sent, rinfo, einfo]
        # 1 คู่ = 1 คำขอ: ภาพสองฝั่งรวมกันใหญ่เกินคำขอเดียว ⇒ เข้ารหัสใหม่ให้พอดีงบต่อภาพ
        # (ลดคุณภาพก่อน แล้วค่อยย่อ — บอกในคำเตือนเสมอ) · คู่ที่พอดีอยู่แล้วไม่ถูกแตะแม้แต่ไบต์เดียว
        if config.ONE_REQUEST_PER_PAIR and not vision_client.fits_one_request(
                [encoded[s][0] for s in ("a", "b")]):
            budget = imaging.pair_image_budget(2)
            for s in ("a", "b"):
                jpeg, sent, rinfo, einfo = encoded[s]
                if len(jpeg) <= budget:
                    continue
                src_img = einfo.get("_img")
                if src_img is None:
                    src_img = sent
                jpeg, sent, e2 = imaging.fit_jpeg(src_img, budget)
                e2["warnings"].insert(0, "ภาพคู่นี้รวมกันใหญ่เกินคำขอเดียว — เข้ารหัสใหม่ให้ส่ง"
                                         "คู่ A/B ในคำขอเดียวกัน (คุณภาพ %s)" % e2.get("quality"))
                e2["pair_refit"] = True
                encoded[s] = [jpeg, sent, rinfo, e2]
        for s in ("a", "b"):
            z = p[s]
            jpeg, sent, rinfo, einfo = encoded[s]
            einfo.pop("_img", None)
            fname = "p%d_%s.jpg" % (n, s)
            with open(os.path.join(rd, "img", fname), "wb") as f:
                f.write(jpeg)
            side = {"page": z["page"], "bbox": z["bbox"], "image": fname,
                    "render": rinfo, "encode": einfo,
                    "sent_px": [int(sent.shape[1]), int(sent.shape[0])],
                    "jpeg_bytes": len(jpeg), "sha1": imaging.sha1_bytes(jpeg)[:12]}
            for w in rinfo.get("warnings", []) + einfo.get("warnings", []):
                warnings.append("คู่ %d ฝั่ง %s: %s" % (n, s.upper(), w))
            pr["sides"][s] = side
            grp.append({"id": "p%d_%s" % (n, s), "jpeg": jpeg})
        groups.append(grp)
        pairs.append(pr)
    stage["render_ms"] = int((time.time() - t0) * 1000)

    # ── 2) Vision ────────────────────────────────────────────────────
    t0 = time.time()
    say("กำลังส่งภาพให้ Cloud Vision (%s)" % config.ENDPOINT)
    vres = vision_client.annotate(groups, poster=poster, key=key)
    stage["vision_ms"] = int((time.time() - t0) * 1000)
    calls = list(vres["calls"])

    # ── 3) ประกอบบรรทัด + เทียบ ──────────────────────────────────────
    t0 = time.time()
    for pr in pairs:
        n = pr["n"]
        parsed = {}
        req_idx = set()
        for s in ("a", "b"):
            side = pr["sides"][s]
            r = vres["results"].get("p%d_%s" % (n, s)) or {"ok": False, "error": "ไม่มีผล"}
            side["request_index"] = r.get("request_index")
            req_idx.add(r.get("request_index"))
            side["ok"] = bool(r.get("ok"))
            side["error"] = r.get("error", "")
            if r.get("ok"):
                W, H = side["sent_px"]
                with open(os.path.join(rd, "raw", "p%d_%s.json" % (n, s)), "w",
                          encoding="utf-8") as f:
                    json.dump(r.get("fta") or {}, f, ensure_ascii=False)
                parsed[s] = textmodel.parse(r.get("fta") or {}, W, H)
                st = dict(parsed[s]["stats"])
                st["skipped_boxes"] = [[round(v, 1) for v in b] for b in st["skipped_boxes"]]
                side["stats"] = st
        pr["same_request"] = len(req_idx) == 1 and None not in req_idx
        if not pr["same_request"] and all(pr["sides"][s]["ok"] for s in ("a", "b")):
            warnings.append("คู่ %d: ภาพ A/B ถูกส่งคนละคำขอ (ภาพใหญ่) — อาจถูกอ่านด้วย"
                            "โมเดลคนละรุ่นถ้า Google เปลี่ยนรุ่นระหว่างนั้น" % n)
        if len(parsed) < 2:
            pr["unreadable"] = True
            pr["findings"] = []
            pr["coverage"] = None
            continue
        cmp_ = compare.compare(parsed["a"]["lines"], parsed["b"]["lines"],
                               tuple(pr["sides"]["a"]["sent_px"]),
                               tuple(pr["sides"]["b"]["sent_px"]))
        pr["_cmp"] = cmp_
        pr["findings"] = cmp_["findings"]
        pr["debris"] = cmp_["debris"]
        pr["curved_lines"] = cmp_["curved_lines"]
        pr["coverage"] = cmp_["coverage"]
        pr["coverage_a"] = cmp_["coverage_a"]
        pr["coverage_b"] = cmp_["coverage_b"]
        pr["pair_methods"] = cmp_["pair_methods"]
        pr["reflow_edges"] = cmp_["reflow_edges"]
        pr["reflow_lines"] = cmp_["reflow_lines"]
        pr["row_merges"] = cmp_.get("row_merges", [])
        pr["unpaired"] = {"a": cmp_["unpaired_a"], "b": cmp_["unpaired_b"]}
        pr["lines"] = {"a": _compact_lines(cmp_["lines_a"]), "b": _compact_lines(cmp_["lines_b"])}
        pr["line_pairs"] = cmp_["pairs"]
        if not cmp_["lines_a"] and not cmp_["lines_b"]:
            warnings.append("คู่ %d: ไม่พบข้อความทั้งสองฝั่ง" % n)
            pr["coverage"] = None
    stage["compare_ms"] = int((time.time() - t0) * 1000)

    # ── 4) อ่านซ้ำแบบซูมเฉพาะจุดแดง ────────────────────────────────────
    t0 = time.time()
    reread_log = _reread(pairs, srcs, rd, poster, key, calls, warnings, say)
    reread_log["sharpness"] = sharp
    stage["reread_ms"] = int((time.time() - t0) * 1000)

    # ── 5) เลขจุด ────────────────────────────────────────────────────
    fid = 0
    for pr in pairs:
        if config.CURVED_GROUP_ENABLED and pr.get("findings"):
            pr["findings"] = compare.collapse_curved(pr["findings"])
        for f in pr.get("findings", []):
            fid += 1
            f["id"] = fid
        for f in pr.get("debris", []):
            fid += 1
            f["id"] = fid

    # ── 6) AI ตรวจทาน (ข้อความของ Vision → N8N/Gemini · ไม่ส่งภาพ · ไม่ยิง Vision ซ้ำ) ──
    t0 = time.time()
    ai_sum, fid = ai_review.run_all(pairs, ai_mode, warnings, say, fid, ai_poster)
    stage["ai_ms"] = int((time.time() - t0) * 1000)

    # ── 7) ผลตัดสิน + บันทึก ─────────────────────────────────────────
    for pr in pairs:
        pr.pop("_cmp", None)
        # ห้ามใช้ชื่อ ``key`` — ทับกุญแจ API ข้างบน แล้ว redact() ลบชื่อคีย์ทิ้งแทนกุญแจจริง
        for lk in ("findings", "debris", "algo_only", "ai_dismissed"):
            for f in pr.get(lk) or []:
                if "confidence" not in f:
                    f["confidence"] = ai_review.confidence(f)
        v, rs = verdict_of([pr])
        if pr.get("algo_only"):
            rs.append("อัลกอริทึมพบอีก %d จุดที่ AI ไม่ได้ระบุ (รายการพับ — ไม่นับ)"
                      % len(pr["algo_only"]))
        pr["verdict"] = v
        pr["reasons"] = rs
    verdict, reasons = verdict_of(pairs)
    n_algo = sum(len(pr.get("algo_only") or []) for pr in pairs)
    if n_algo:
        reasons.append("อัลกอริทึมพบอีก %d จุดที่ AI ไม่ได้ระบุ (รายการพับ — ไม่นับ)" % n_algo)
    stage["total_ms"] = int((time.time() - t_all) * 1000)
    result = {
        "version": VERSION, "job": job_id, "run": run_name,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "verdict": verdict, "verdict_th": VERDICT_TH[verdict], "reasons": reasons,
        "sharpness": sharp,
        "pairs": pairs, "calls": calls, "reread": reread_log, "ai": ai_sum,
        "warnings": warnings, "stage": stage,
        "key": {"source": key_src, "masked": keystore.mask(key), "length": len(key)},
        "settings": settings_snapshot(),
        "files": meta.get("files"), "owner": (meta.get("owner") or {}).get("username", ""),
    }
    log_text = diaglog.build_text(result)
    result["log_text"] = keystore.redact(log_text, key)
    blob = keystore.redact(json.dumps(result, ensure_ascii=False, default=_jsonable), key)
    with open(os.path.join(rd, "result.json"), "w", encoding="utf-8") as f:
        f.write(blob)
    with open(os.path.join(rd, "log.txt"), "w", encoding="utf-8") as f:
        f.write(result["log_text"])
    return json.loads(blob)


def _jsonable(o):
    if isinstance(o, (set, tuple)):
        return list(o)
    try:
        return float(o)
    except (TypeError, ValueError):
        return str(o)


def settings_snapshot() -> dict:
    return {k: getattr(config, k) for k in (
        "ENDPOINT", "MODEL", "LANGUAGE_HINTS", "TIMEOUT_S", "RETRIES",
        "MAX_REQUEST_BYTES", "MAX_IMAGE_BYTES", "MAX_IMAGE_MP", "JPEG_QUALITIES",
        "PDF_ZONE_DPI", "PDF_ZONE_DPI_MAX", "ZONE_MIN_LONG_SIDE",
        "SHARPNESS", "SHARP_FILL", "SHARP_MAX_RENDERS",
        "CONF_FAIL", "CONF_LOW", "COVERAGE_MIN", "PAIR_MIN_SIM", "PAIR_MIN_RUN",
        "PAIR_MAX_DIST", "ROW_MERGE_ENABLED", "ROW_MAX_ANGLE",
        "PUNCT_CAN_FAIL", "CURVED_GROUP_ENABLED", "TILT_ANGLE", "CURVED_NEIGHBOR_MAX_CHARS",
        "DEBRIS_ENABLED", "DEBRIS_CONF", "SEAM_FILLER", "CROSS_ROW_JOIN", "SYMBOL_TOKEN",
        "FRACTION_YELLOW", "REREAD_ENABLED", "REREAD_MAX", "REREAD_SCALE", "REREAD_MAX_SIDE",
        "AI_MODE", "AI_REVIEW_URL", "AI_TIMEOUT_S", "AI_RETRIES",
        "ONE_REQUEST_PER_PAIR", "RUN_GUARD", "RUN_MAX_CONCURRENT", "RUN_COOLDOWN_S")}


def _reread(pairs, srcs, rd, poster, key, calls, warnings, say) -> dict:
    """อ่านซ้ำแบบซูมเฉพาะจุดแดง · **ครอปละ 1 คู่บรรทัด** (หลายจุดในบรรทัดเดียวกัน
    ใช้ภาพซูมชุดเดียวกัน — ไม่จ่ายค่า Vision ซ้ำ) · ยืนยันทีละจุดจากผลชุดนั้น"""
    log = {"enabled": config.REREAD_ENABLED, "candidates": 0, "done": 0, "crops": 0,
           "confirmed": 0, "downgraded": 0, "skipped_cap": 0, "errors": 0, "items": []}
    reds = [(pr, f) for pr in pairs for f in pr.get("findings", []) if f["severity"] == "red"]
    log["candidates"] = len(reds)
    if not reds:
        return log
    if not config.REREAD_ENABLED:
        for _, f in reds:
            if f["class"] == "PUNCT":
                # เครื่องหมายวรรคตอนเป็นแดงได้ **เฉพาะเมื่ออ่านซ้ำยืนยันแล้ว**
                f["severity"] = "yellow"
                f["notes"].append("เครื่องหมายวรรคตอนต้องยืนยันด้วยการอ่านซ้ำ (ปิดอยู่) — ยังไม่ยืนยัน")
            elif f.get("curved"):
                # ข้อความโค้ง/เอียง — กติกาเดียวกับเครื่องหมายวรรคตอน
                f["severity"] = "yellow"
                f["notes"].append("ข้อความโค้ง/เอียงต้องยืนยันด้วยการอ่านซ้ำ (ปิดอยู่) — ยังไม่ยืนยัน")
            else:
                f["notes"].append("ไม่ได้อ่านซ้ำ (ปิดอยู่)")
        return log
    order = {"NUMBER": 0, "CASE": 1, "TEXT": 2, "PUNCT": 3, "MISSING_IN_B": 4, "EXTRA_IN_B": 4}
    reds.sort(key=lambda t: order.get(t[1]["class"], 9))

    # จัดกลุ่ม: จุดที่อยู่คู่บรรทัดเดียวกันใช้ครอปเดียวกัน
    groups_by_key: Dict[tuple, list] = {}
    order_keys: List[tuple] = []
    for pr, f in reds:
        if f["a"]["line"] is not None and f["b"]["line"] is not None:
            k = (pr["n"], f["a"]["line"], f["b"]["line"])
        else:
            k = (pr["n"], id(f))
        if k not in groups_by_key:
            groups_by_key[k] = []
            order_keys.append(k)
        groups_by_key[k].append((pr, f))

    todo, groups = [], []
    for gi, k in enumerate(order_keys):
        members = groups_by_key[k]
        if gi >= config.REREAD_MAX:
            for _, f in members:
                f["severity"] = "yellow"
                f["notes"].append("เกินเพดานอ่านซ้ำ (%d ครอป) — ยังไม่ยืนยัน" % config.REREAD_MAX)
                log["skipped_cap"] += 1
            continue
        pr, f = members[0]
        cmp_ = pr["_cmp"]
        crops = {}
        for s, other in (("a", "b"), ("b", "a")):
            side = pr["sides"][s]
            zw, zh = side["sent_px"]
            lines = cmp_["lines_" + s]
            li = f[s]["line"]
            if li is not None:
                box = lines[li]["box"]
                hgt = max(4.0, lines[li]["height"] or (box[3] - box[1]))
            else:
                # บรรทัดไม่มีคู่: เดาตำแหน่งในอีกฝั่งจากสัดส่วนในโซน (ขยายกว้าง)
                ob = f[other]["box"]
                ow, oh = pr["sides"][other]["sent_px"]
                if not ob:
                    continue
                box = (ob[0] / ow * zw, ob[1] / oh * zh, ob[2] / ow * zw, ob[3] / oh * zh)
                hgt = max(4.0, box[3] - box[1]) * 2.5
            nb = _px_box_to_norm(box, side["bbox"], zw, zh, hgt * 1.0, hgt * 0.45)
            if nb is None:
                continue
            crops[s] = nb
        if len(crops) < 2:
            for _, ff in members:
                ff["severity"] = "yellow"
                ff["notes"].append("อ่านซ้ำไม่ได้ (คำนวณกรอบครอปไม่ได้) — ยังไม่ยืนยัน")
                log["errors"] += 1
            continue
        grp = []
        item = {"classes": [ff["class"] for _, ff in members], "crops": {}}
        rid = "rr%d" % (len(todo) + 1)
        for s in ("a", "b"):
            side = pr["sides"][s]
            rnd = side.get("render") or {}
            if rnd.get("sharpness") == "max" and rnd.get("dpi"):
                # โหมดคมสูงสุด: อ่านซ้ำที่ REREAD_SCALE เท่าของ dpi ที่ใช้จริงในรอบหลัก
                # (ไม่ใช่ของ 400 dpi) · เพดาน PDF_ZONE_DPI_MAX แทนเพดานด้านยาว —
                # ไม่งั้นครอปซูมอาจได้ dpi ต่ำกว่ารอบหลัก = "ซูม" ที่หยาบกว่าเดิม
                img, info = srcs[s].render_zone(side["page"], crops[s],
                                                scale=config.REREAD_SCALE,
                                                base_dpi=float(rnd["dpi"]),
                                                dpi_cap=max(float(config.PDF_ZONE_DPI_MAX),
                                                            float(rnd["dpi"])))
            else:
                img, info = srcs[s].render_zone(side["page"], crops[s],
                                                scale=config.REREAD_SCALE,
                                                max_side=config.REREAD_MAX_SIDE)
            jpeg, sent, einfo = imaging.fit_jpeg(img, config.MAX_IMAGE_BYTES)
            fname = "%s_%s.jpg" % (rid, s)
            with open(os.path.join(rd, "img", fname), "wb") as fh:
                fh.write(jpeg)
            item["crops"][s] = {"bbox": crops[s], "image": fname,
                                "px": [int(sent.shape[1]), int(sent.shape[0])],
                                "dpi": info.get("dpi"), "jpeg_bytes": len(jpeg)}
            grp.append({"id": "%s_%s" % (rid, s), "jpeg": jpeg})
        todo.append((rid, members, item))
        groups.append(grp)
    if not groups:
        return log
    log["crops"] = len(groups)
    say("อ่านซ้ำแบบซูม %d จุด (%d ครอป)" % (sum(len(m) for _, m, _ in todo), len(groups)))
    vres = vision_client.annotate(groups, poster=poster, key=key)
    for c in vres["calls"]:
        c["index"] = len(calls)
        c["phase"] = "reread:" + c["phase"]
        calls.append(c)
    for rid, members, item in todo:
        ra = vres["results"].get(rid + "_a") or {}
        rb = vres["results"].get(rid + "_b") or {}
        item["results"] = []
        if not (ra.get("ok") and rb.get("ok")):
            err = ra.get("error") or rb.get("error") or "ไม่ทราบสาเหตุ"
            for _, f in members:
                f["severity"] = "yellow"
                f["notes"].append("อ่านซ้ำไม่สำเร็จ — ยังไม่ยืนยัน (%s)" % err)
                item["results"].append("error")
                log["errors"] += 1
                log["done"] += 1
            item["error"] = err
            log["items"].append(item)
            continue
        la = textmodel.parse(ra.get("fta") or {}, *item["crops"]["a"]["px"])["lines"]
        lb = textmodel.parse(rb.get("fta") or {}, *item["crops"]["b"]["px"])["lines"]
        item["text_a"] = " / ".join(l["text"] for l in la)[:300]
        item["text_b"] = " / ".join(l["text"] for l in lb)[:300]
        rc = None
        for _, f in members:
            log["done"] += 1
            if f["class"] in ("MISSING_IN_B", "EXTRA_IN_B"):
                # ยืนยันว่า "ฝั่งที่ไม่มี" ไม่มีจริง: หาข้อความในครอปของฝั่งนั้น
                have, lack = ("a", lb) if f["class"] == "MISSING_IN_B" else ("b", la)
                sim = _crop_contains(lack, f[have]["text"])
                item["found_in_other"] = round(sim, 3)
                ok = sim < 0.8
            else:
                if rc is None:
                    rc = compare.compare(la, lb)
                    item["crop_findings"] = [{"class": x["class"], "a": x["a"]["frag"],
                                              "b": x["b"]["frag"]} for x in rc["findings"]][:10]
                # ต้องเป็นความต่าง "ชนิดเดียวกัน ที่ตำแหน่งเดียวกัน" — ไม่ใช่ความต่างของ
                # บรรทัดข้างเคียงที่ติดมาในครอป (กันยืนยันผิดตัว)
                ka = compare.diff_key(f["a"]["frag"])[0]
                kb = compare.diff_key(f["b"]["frag"])[0]
                same = [x for x in rc["findings"] if x["pair_method"] != "unpaired"
                        and x["class"] == f["class"]
                        and ((ka and compare.diff_key(x["a"]["frag"])[0] == ka)
                             or (kb and compare.diff_key(x["b"]["frag"])[0] == kb))]
                ok = bool(same)
            if ok:
                f["notes"].append("ยืนยันด้วยการอ่านซ้ำแบบซูมแล้ว")
                item["results"].append("confirmed")
                log["confirmed"] += 1
            else:
                f["severity"] = "yellow"
                f["notes"].append("อ่านซ้ำแบบซูมแล้วไม่พบความต่างเดิม — อาจเป็น OCR อ่านเพี้ยน "
                                  "(ยังแสดงไว้ ไม่ลบ)")
                item["results"].append("downgraded")
                log["downgraded"] += 1
        log["items"].append(item)
    return log
