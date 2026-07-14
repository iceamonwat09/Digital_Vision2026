/* Artwork Proof Check — upload, zone editor, inspection results.
 * Self-contained: touches only /api/artwork/* endpoints. */
(function () {
  "use strict";

  // ── shared helpers (also used by the history page) ─────────────────
  const $ = (id) => document.getElementById(id);
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function api(path, opts) {
    return fetch(path, opts).then(async (r) => {
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
      return data;
    });
  }
  window.awApi = api;
  window.awEsc = esc;

  // ── report rendering (shared with history page via window.*) ──────
  const CLASS_LABELS = {
    MISMATCH_PANELS: "ไม่ตรงกันระหว่าง panel",
    MISMATCH_ZOOM: "zoom ไม่ตรงฉลากจริง",
    NUMBER_FAIL: "ตัวเลข/บาร์โค้ดผิด",
    PHRASE_FAIL: "วลีแบรนด์สะกดเพี้ยน",
    SPELL_FAIL: "ไม่อยู่ใน dictionary",
    UNREADABLE: "อ่านไม่ชัด",
  };

  function renderReport(rep, box) {
    const vClass = rep.verdict === "PASS" ? "aw-v-pass"
      : rep.verdict === "REVIEW" ? "aw-v-review" : "aw-v-fail";
    const vText = rep.verdict === "PASS" ? "✅ PASS — ไม่พบประเด็น"
      : rep.verdict === "REVIEW" ? "🟡 REVIEW — มีจุดให้คนยืนยัน"
      : "❌ FAIL — พบความผิดที่ต้องแก้";

    let html = '<div class="aw-verdict ' + vClass + '">' + vText + "</div>";
    html += '<div style="font-size:12px;color:#78909c;margin-top:4px;">' +
      esc(rep.filename) + (rep.brand ? " · แบรนด์ " + esc(rep.brand) : "") +
      " · ใช้เวลา " + esc(rep.elapsed_s) + " วินาที · " + esc(rep.created_at) + "</div>";

    html += '<div class="aw-summary">';
    Object.keys(CLASS_LABELS).forEach((cls) => {
      const n = (rep.summary && rep.summary[cls]) || 0;
      html += '<div class="aw-sumcard' + (n ? " hit" : "") + '">' +
        esc(CLASS_LABELS[cls]) + "<b>" + n + "</b></div>";
    });
    html += "</div>";

    // ── 2 รูปเคียงกัน: Artwork ต้นฉบับ + ผลตรวจ (overlay) ──────────
    const ts = Date.now();
    const previewUrl = "/api/artwork/" + esc(rep.id) + "/preview.png?t=" + ts;
    const overlayUrl = "/api/artwork/" + esc(rep.id) + "/overlay.png?t=" + ts;
    html += '<div class="aw-img-pair">' +
      '<div class="aw-img-card">' +
        '<div class="aw-img-label">🖼 Artwork ต้นฉบับ <span style="font-weight:400;opacity:.7;">(คลิกขยาย)</span></div>' +
        '<img src="' + previewUrl + '" alt="artwork preview" class="aw-zoomable" data-caption="Artwork ต้นฉบับ" data-src="' + previewUrl + '">' +
      '</div>' +
      '<div class="aw-img-card">' +
        '<div class="aw-img-label overlay-label">🔍 ผลตรวจ — โซนที่พบปัญหา <span style="font-weight:400;opacity:.7;">(คลิกขยาย)</span></div>' +
        '<img src="' + overlayUrl + '" alt="overlay" class="aw-zoomable" data-caption="ผลตรวจ (Overlay)" data-src="' + overlayUrl + '">' +
      '</div>' +
    '</div>';

    const zoneById = {};
    (rep.zones || []).forEach((z) => { zoneById[z.id] = z; });

    if ((rep.defects || []).length) {
      html += "<h4 style='margin:14px 0 8px;'>รายการที่พบ (" + rep.defects.length + ")</h4>";
      rep.defects.forEach((d, i) => {
        const z  = zoneById[d.zone_id];
        // หาโซนอ้างอิง (panel ที่ใช้เปรียบเทียบ) — ใช้ตัวแรกที่ bbox พร้อม
        const refZones = (d.ref_zone_ids || [])
          .map((id) => zoneById[id]).filter(Boolean);
        const refZ = refZones[0] || null;

        html += '<div class="aw-defect ' + esc(d.severity) + '">' +
          '<span class="aw-defect-class">' + esc(d.class) + "</span>" +
          "<b>" + esc(d.zone_id) + (z && z.label ? " · " + esc(z.label) : "") + "</b><br>" +
          esc(d.message);
        if (d.found)     html += '<br>พบ: <span class="found">' + esc(d.found) + "</span>";
        if (d.reference) html += ' &nbsp;เทียบกับ: <span class="ref">' + esc(d.reference) + "</span>";

        // ── 2-crop comparison (auto-load ทันที ไม่ต้องคลิก) ───────────
        if (z && refZ) {
          const qA = "x=" + z.bbox[0] + "&y=" + z.bbox[1] + "&w=" + z.bbox[2] + "&h=" + z.bbox[3];
          const qB = "x=" + refZ.bbox[0] + "&y=" + refZ.bbox[1] + "&w=" + refZ.bbox[2] + "&h=" + refZ.bbox[3];
          const cropA = "/api/artwork/" + esc(rep.id) + "/crop?" + qA;
          const cropB = "/api/artwork/" + esc(rep.id) + "/crop?" + qB;
          const labelA = d.zone_id + (z.label ? " · " + z.label : "");
          const labelB = refZ.id + (refZ.label ? " · " + refZ.label : "") + " (อ้างอิง)";
          html += '<div class="aw-img-pair" style="margin-top:8px;">' +
            '<div class="aw-img-card">' +
              '<div class="aw-img-label overlay-label">⚠ ' + esc(labelA) + '</div>' +
              '<img src="' + esc(cropA) + '" alt="' + esc(labelA) + '"' +
                ' class="aw-zoomable" data-src="' + esc(cropA) + '" data-caption="' + esc(labelA) + '">' +
            '</div>' +
            '<div class="aw-img-card">' +
              '<div class="aw-img-label">📋 ' + esc(labelB) + '</div>' +
              '<img src="' + esc(cropB) + '" alt="' + esc(labelB) + '"' +
                ' class="aw-zoomable" data-src="' + esc(cropB) + '" data-caption="' + esc(labelB) + '">' +
            '</div>' +
          '</div>';
        } else if (z) {
          // fallback: แค่โซนเดียว (ไม่มี ref zone)
          const q = "x=" + z.bbox[0] + "&y=" + z.bbox[1] + "&w=" + z.bbox[2] + "&h=" + z.bbox[3];
          const cropUrl = "/api/artwork/" + esc(rep.id) + "/crop?" + q;
          const caption = d.zone_id + (z.label ? " · " + z.label : "");
          html += '<div style="margin-top:8px;">' +
            '<img src="' + esc(cropUrl) + '" alt="crop"' +
              ' class="aw-zoomable" data-src="' + esc(cropUrl) + '" data-caption="' + esc(caption) + '"' +
              ' style="max-width:100%;border-radius:4px;cursor:zoom-in;">' +
          '</div>';
        }

        html += "</div>";
      });
    } else {
      html += '<p style="color:#2e7d32;font-size:14px;">ไม่พบข้อความที่น่าสงสัยในทุกชั้นการตรวจ</p>';
    }

    html += "<details><summary>📄 ข้อความ OCR ต่อโซน (ตรวจสอบเอง)</summary>";
    (rep.ocr || []).forEach((r) => {
      html += "<b style='font-size:12px;'>" + esc(r.zone_id) + " · engine=" + esc(r.engine) +
        (r.conf != null ? " · conf=" + esc(r.conf) : "") + "</b>" +
        '<pre class="aw-pre">' + esc(r.text || "(ว่าง)") + "</pre>";
    });
    html += "</details>";

    box.innerHTML = html;

    // wire lightbox บน aw-zoomable ทั้งหมด (รูปคู่ + crop fallback)
    box.querySelectorAll(".aw-zoomable").forEach(wireZoomable);
  }
  window.awRenderReport = renderReport;

  // ── Lightbox ───────────────────────────────────────────────────────
  let lbScale = 1;
  let lbDrag = null;
  let lbScrollStart = null;

  const lightbox  = document.getElementById("awLightbox");
  const lbImg     = document.getElementById("awLbImg");
  const lbInner   = document.getElementById("awLbInner");
  const lbPct     = document.getElementById("awLbPct");
  const lbCaption = document.getElementById("awLbCaption");

  function lbOpen(src, caption) {
    if (!lightbox || !lbImg) { window.open(src, "_blank"); return; }
    lbScale = 1;
    lbImg.src = src;
    lbImg.style.transform = "scale(1)";
    lbPct.textContent = "100%";
    lbCaption.textContent = caption || "";
    lbInner.scrollLeft = 0;
    lbInner.scrollTop  = 0;
    lightbox.classList.add("open");
    document.body.style.overflow = "hidden";
  }
  function lbClose() {
    lightbox.classList.remove("open");
    document.body.style.overflow = "";
    lbImg.src = "";
  }
  function lbSetScale(s) {
    lbScale = Math.min(8, Math.max(0.2, s));
    lbImg.style.transform = "scale(" + lbScale + ")";
    lbImg.style.transformOrigin = "top left";
    lbPct.textContent = Math.round(lbScale * 100) + "%";
  }

  if (lightbox) {
    document.getElementById("awLbClose").addEventListener("click", lbClose);
    lightbox.addEventListener("click", (e) => { if (e.target === lightbox) lbClose(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") lbClose(); });

    document.getElementById("awLbZoomIn").addEventListener("click",  () => lbSetScale(lbScale * 1.3));
    document.getElementById("awLbZoomOut").addEventListener("click", () => lbSetScale(lbScale / 1.3));
    document.getElementById("awLbReset").addEventListener("click",   () => lbSetScale(1));

    // scroll wheel = zoom
    lbInner.addEventListener("wheel", (e) => {
      e.preventDefault();
      lbSetScale(lbScale * (e.deltaY < 0 ? 1.1 : 0.9));
    }, { passive: false });

    // drag to pan
    lbInner.addEventListener("mousedown", (e) => {
      lbDrag = { x: e.clientX + lbInner.scrollLeft, y: e.clientY + lbInner.scrollTop };
    });
    document.addEventListener("mousemove", (e) => {
      if (!lbDrag) return;
      lbInner.scrollLeft = lbDrag.x - e.clientX;
      lbInner.scrollTop  = lbDrag.y - e.clientY;
    });
    document.addEventListener("mouseup", () => { lbDrag = null; });
  }

  function wireZoomable(img) {
    img.addEventListener("click", () => {
      lbOpen(img.dataset.src || img.src, img.dataset.caption || img.alt);
    });
  }
  window.awWireZoomable = wireZoomable;

  // ── text + translation table (shared with history page) ───────────
  function reEsc(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  function highlightFlagged(src, flagged) {
    let html = esc(src);
    (flagged || []).forEach((w) => {
      const re = new RegExp("(^|[^A-Za-z])(" + reEsc(esc(w)) +
                            ")(?![A-Za-z])", "g");
      html = html.replace(re, "$1<mark>$2</mark>");
    });
    return html;
  }

  function renderTextTable(result, box, onlyIssues) {
    const rows = (result && result.rows) || [];
    let html = "";
    if (result && result.note)
      html += '<div class="aw-note">' + esc(result.note) + "</div>";
    const shown = onlyIssues
      ? rows.filter((r) => r.status !== "ok" || (r.ai_spell && r.ai_spell.flagged))
      : rows;
    if (!shown.length) {
      box.innerHTML = html + '<div class="aw-empty">' +
        (onlyIssues ? "ไม่มีบรรทัดที่น่าสงสัย" : "ไม่มีข้อความให้แสดง") + "</div>";
      return;
    }
    html += '<div class="aw-ttable-scroll"><table class="aw-ttable"><colgroup>' +
      '<col style="width:9%"><col style="width:26%"><col style="width:23%">' +
      '<col style="width:20%"><col style="width:22%"></colgroup><thead><tr>' +
      "<th>โซน</th><th>ข้อความบนฉลาก</th><th>คำแปล EN</th><th>สถานะ</th>" +
      "<th>🤖 ตรวจสะกดโดย AI</th>" +
      "</tr></thead><tbody>";
    shown.forEach((r) => {
      const issue = r.status !== "ok";
      const aiFlagged = !!(r.ai_spell && r.ai_spell.flagged);
      html += '<tr class="' + (issue ? "has-issue" : (aiFlagged ? "has-ai-issue" : "")) + '">';
      html += '<td class="zone-cell">' + esc(r.zone_id) + "</td>";
      html += '<td class="src-cell">' + highlightFlagged(r.src, r.flagged) + "</td>";
      const en = r.en || "";
      html += '<td class="en-cell' + (en ? "" : " empty") + '">' +
        (en ? esc(en) : "—") + "</td>";
      let st = '<span class="aw-status-ok">✓</span>';
      if (issue) {
        if (r.status === "mismatch")
          st = '<span class="aw-status-warn">❌ ไม่ตรงกับฉลากจริง</span>';
        else
          st = '<span class="aw-status-warn">⚠️ dict: สะกดน่าสงสัย</span>';
        const sug = r.suggest || {};
        Object.keys(sug).forEach((w) => {
          if (sug[w] && sug[w].length)
            st += '<span class="aw-suggest">“' + esc(w) + "” → " +
              sug[w].map((s) => "<code>" + esc(s) + "</code>").join(" ") + "</span>";
        });
      }
      html += "<td>" + st + "</td>";
      // Advisory AI spell-check (Gemini, via the translate webhook).
      // Purely informational — never feeds the status column above.
      // Three states, NOT two: "checked, clean" must look different from
      // "AI never ran" (N8N not updated yet) — both used to render "—".
      // kind "variant" = correct regional spelling (fibre/fiber) — shown
      // as info, not warning. reason = short Thai explanation from the
      // model. Both optional (old caches / old N8N workflow omit them).
      const aiSpell = r.ai_spell || {};
      let ai;
      if (!result || !result.ai_spell_available) {
        ai = '<span class="aw-status-unavail">ยังไม่รองรับ</span>';
      } else if (aiSpell.flagged) {
        // Label by the AI's own kind. variant = correct regional spelling
        // (blue info). typo / truncated get clearer wording than the old
        // generic "น่าสงสัย". Any missing / unknown kind (old caches, old
        // N8N workflow) falls back to "น่าสงสัย" — never breaks.
        if (aiSpell.kind === "variant")
          ai = '<span class="aw-status-info">🤖 ทางเลือกการสะกด (ไม่ใช่คำผิด)</span>';
        else if (aiSpell.kind === "typo")
          ai = '<span class="aw-status-bad">🤖 สะกดผิด</span>';
        else if (aiSpell.kind === "truncated")
          ai = '<span class="aw-status-warn">🤖 คำไม่ครบ (ถูกตัด)</span>';
        else
          ai = '<span class="aw-status-warn">🤖 น่าสงสัย</span>';
        if (aiSpell.suggestion)
          ai += '<span class="aw-suggest">→ <code>' +
            esc(aiSpell.suggestion) + "</code></span>";
        if (aiSpell.reason)
          ai += '<span class="aw-ai-reason">' + esc(aiSpell.reason) + "</span>";
      } else {
        ai = '<span class="aw-status-ok">✓ ไม่พบ</span>';
        // dict flagged the line but the AI thinks it's fine — most often a
        // loanword / brand / proper noun missing from the dictionary.
        if (r.status === "spell")
          ai += '<span class="aw-ai-reason">dict ไม่รู้จักแต่ AI ว่าถูก — ' +
            "มักเป็นคำทับศัพท์/ชื่อเฉพาะ</span>";
      }
      html += "<td>" + ai + "</td></tr>";
    });
    html += "</tbody></table></div>";
    html += '<div class="aw-tlegend"><b>หมายเหตุ:</b><ul>' +
      '<li>คอลัมน์ <b>สถานะ</b> มาจากการตรวจแบบ deterministic (dictionary + เทียบข้าม panel) ' +
        'ส่วนคอลัมน์ <b>🤖</b> เป็นความเห็นของ AI ใช้ประกอบการพิจารณาเท่านั้น ไม่มีผลต่อ PASS/FAIL</li>' +
      '<li><b>⚠️ dict: สะกดน่าสงสัย</b> แต่ AI <b>✓ ไม่พบ</b> — มักเป็นคำทับศัพท์ ชื่อแบรนด์ ' +
        'หรือชื่อเฉพาะที่ไม่มีใน dictionary ไม่ใช่คำผิดเสมอไป ยืนยันด้วยตาแล้วเพิ่มเข้าคลังคำแบรนด์ได้' +
        'เพื่อไม่ให้แจ้งซ้ำ</li>' +
      '<li><b>🤖 สะกดผิด</b> / <b>🤖 คำไม่ครบ (ถูกตัด)</b> — AI คาดว่าคำนั้นสะกดผิด ' +
        'หรือถูกตัดปลาย (มีเหตุผลกำกับ) ต้องยืนยันด้วยตา</li>' +
      '<li><b>🤖 ทางเลือกการสะกด (ไม่ใช่คำผิด)</b> — คำถูกต้องแต่เป็นการสะกดตามภูมิภาค ' +
        'เช่น fibre (อังกฤษ) / fiber (อเมริกัน) ให้ยืนยันว่าตรงกับตลาดเป้าหมายของฉลาก</li>' +
      "</ul></div>";
    box.innerHTML = html;
  }
  window.awRenderTextTable = renderTextTable;

  // history page includes this file only for the renderer above
  if (!$("awFile")) return;

  // ── state ──────────────────────────────────────────────────────────
  let inspectionId = null;
  let zones = [];            // [{id,type,group,bbox:[x,y,w,h],label}]
  let selectedId = null;
  let natW = 0, natH = 0;    // preview natural size
  let zoomPct = 100;
  let busy = false;

  // ── dom ────────────────────────────────────────────────────────────
  const fileInput = $("awFile"), brandInput = $("awBrand");
  const stage = $("awStage"), stageEmpty = $("awStageEmpty");
  const previewImg = $("awPreviewImg");
  const propsBox = $("awProps");
  const resultBox = $("awResult");
  function setBusy(b) {
    busy = b;
    ["awInspect", "awAddZone", "awClearZones", "awRedetect",
     "awTemplateLoad", "awTemplateSave"].forEach((id) => {
      $(id).disabled = b || !inspectionId;
    });
  }

  // ── upload ─────────────────────────────────────────────────────────
  fileInput.addEventListener("change", async () => {
    const f = fileInput.files[0];
    if (!f) return;
    resultBox.innerHTML =
      '<div class="aw-empty"><span class="aw-spin"></span>กำลังเปิดไฟล์และเสนอโซน…</div>';
    try {
      const fd = new FormData();
      fd.append("file", f);
      const res = await api("/api/artwork/upload", { method: "POST", body: fd });
      inspectionId = res.id;
      zones = res.zones || [];
      natW = res.preview_size[0];
      natH = res.preview_size[1];
      selectedId = null;
      cancelDraw();
      // Show the result tabs right after upload so the "ข้อความ + คำแปล" tab
      // can be used WITHOUT first pressing "ส่งตรวจสอบ" (OCR-only advisory).
      showTabs(true);
      switchTab("result");
      resetTextTab();
      previewImg.src = "/api/artwork/" + inspectionId + "/preview.png?t=" + Date.now();
      previewImg.onload = () => { applyZoom(); renderZones(); };
      stage.style.display = "inline-block";
      stageEmpty.style.display = "none";
      $("awZoomBar").style.display = "";
      setBusy(false);
      resultBox.innerHTML = '<div class="aw-empty">ปรับโซนแล้วกด "ส่งตรวจสอบ"</div>';

      const warns = [];
      if (res.has_text_layer)
        warns.push("✅ ไฟล์นี้มี text layer — โซนที่อ่านได้จากตัว PDF จะแม่น 100% โดยไม่ใช้ OCR");
      if (!res.ocr_available)
        warns.push("⚠️ ยังไม่ได้ตั้งค่า N8N_OCR_WEBHOOK_URL — โซนที่ไม่มี text layer จะถูกรายงานเป็น 'อ่านไม่ได้'");
      if (!res.spell_layer_available)
        warns.push("⚠️ ยังไม่ได้ติดตั้ง pyspellchecker — ชั้นตรวจ dictionary จะถูกข้าม (pip install pyspellchecker)");
      const w = $("awEnvWarn");
      w.style.display = warns.length ? "" : "none";
      w.innerHTML = warns.map(esc).join("<br>");
    } catch (e) {
      resultBox.innerHTML = '<div class="aw-empty">เปิดไฟล์ไม่สำเร็จ: ' + esc(e.message) + "</div>";
    }
  });

  // ── zoom ───────────────────────────────────────────────────────────
  const zoomRange = $("awZoomRange");
  const zoomLabel = $("awZoomLabel");

  zoomRange.addEventListener("input", (ev) => {
    zoomPct = parseInt(ev.target.value, 10) || 100;
    zoomLabel.textContent = zoomPct + "%";
    applyZoom();
    renderZones();
  });

  // ดับเบิลคลิกที่ slider = reset 100%
  zoomRange.addEventListener("dblclick", () => {
    zoomRange.value = 100;
    zoomPct = 100;
    zoomLabel.textContent = "100%";
    applyZoom();
    renderZones();
  });

  // ปุ่ม reset
  $("awZoomReset").addEventListener("click", () => {
    zoomRange.value = 100;
    zoomPct = 100;
    zoomLabel.textContent = "100%";
    applyZoom();
    renderZones();
  });

  // scroll wheel บน stage-box = zoom (Ctrl ไม่ต้องกด)
  zoomRange.closest(".aw-stage-box").addEventListener("wheel", (ev) => {
    if (!natW) return;
    ev.preventDefault();
    const delta = ev.deltaY > 0 ? -5 : 5;
    zoomPct = Math.min(300, Math.max(30, zoomPct + delta));
    zoomRange.value = zoomPct;
    zoomLabel.textContent = zoomPct + "%";
    applyZoom();
    renderZones();
  }, { passive: false });

  function applyZoom() {
    if (!natW) return;
    previewImg.style.width = Math.round(natW * zoomPct / 100) + "px";
  }
  function dispW() { return previewImg.clientWidth || natW; }
  function dispH() { return previewImg.clientHeight || natH; }

  // ── zone rendering / editing ───────────────────────────────────────
  function renderZones() {
    stage.querySelectorAll(".aw-zone").forEach((el) => el.remove());
    const W = dispW(), H = dispH();
    zones.forEach((z) => {
      const el = document.createElement("div");
      el.className = "aw-zone t-" + z.type + (z.id === selectedId ? " selected" : "");
      el.dataset.zid = z.id;
      el.style.left = (z.bbox[0] * W) + "px";
      el.style.top = (z.bbox[1] * H) + "px";
      el.style.width = (z.bbox[2] * W) + "px";
      el.style.height = (z.bbox[3] * H) + "px";
      const tag = document.createElement("div");
      tag.className = "aw-zone-tag";
      tag.textContent = z.id + (z.group ? " [" + z.group + "]" : "") +
        (z.type !== "panel" ? " " + z.type : "");
      el.appendChild(tag);
      const handle = document.createElement("div");
      handle.className = "aw-handle";
      el.appendChild(handle);
      stage.appendChild(el);
      el.addEventListener("mousedown", (ev) => startDrag(ev, z, el, ev.target === handle));
      el.addEventListener("dblclick", (ev) => { ev.preventDefault(); snapZone(z); });
    });
    renderProps();
  }

  // ── snap-to-content: ดับเบิลคลิกโซน → server ขยับกรอบให้พอดีเนื้อหา ──
  let snapping = false;
  async function snapZone(z) {
    if (busy || snapping || !inspectionId) return;
    snapping = true;
    const el = stage.querySelector('.aw-zone[data-zid="' + z.id + '"]');
    if (el) el.style.opacity = "0.45";
    try {
      const res = await api("/api/artwork/" + inspectionId + "/snap", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bbox: z.bbox }),
      });
      if (res.bbox && res.bbox.length === 4) {
        z.bbox = res.bbox;
        selectedId = z.id;
        renderZones();
      }
    } catch (e) {
      if (el) el.style.opacity = "";   // กรอบเดิมคงอยู่ ไม่ต้องรบกวนผู้ใช้
    } finally {
      snapping = false;
    }
  }

  let drag = null;
  function startDrag(ev, zone, el, isResize) {
    if (drawMode) return;   // ปล่อยให้ event ทะลุไปที่ stage เพื่อวาดโซนใหม่
    ev.preventDefault();
    ev.stopPropagation();
    selectedId = zone.id;
    stage.querySelectorAll(".aw-zone").forEach((d) =>
      d.classList.toggle("selected", d.dataset.zid === zone.id));
    renderProps();
    drag = {
      zone, el, isResize,
      startX: ev.clientX, startY: ev.clientY,
      bbox: zone.bbox.slice(),
    };
  }
  document.addEventListener("mousemove", (ev) => {
    if (!drag) return;
    const W = dispW(), H = dispH();
    const dx = (ev.clientX - drag.startX) / W;
    const dy = (ev.clientY - drag.startY) / H;
    const b = drag.bbox;
    let nb;
    if (drag.isResize) {
      nb = [b[0], b[1],
            Math.min(Math.max(0.01, b[2] + dx), 1 - b[0]),
            Math.min(Math.max(0.01, b[3] + dy), 1 - b[1])];
    } else {
      nb = [Math.min(Math.max(0, b[0] + dx), 1 - b[2]),
            Math.min(Math.max(0, b[1] + dy), 1 - b[3]),
            b[2], b[3]];
    }
    drag.zone.bbox = nb.map((v) => Math.round(v * 1e5) / 1e5);
    drag.el.style.left = (nb[0] * W) + "px";
    drag.el.style.top = (nb[1] * H) + "px";
    drag.el.style.width = (nb[2] * W) + "px";
    drag.el.style.height = (nb[3] * H) + "px";
  });
  document.addEventListener("mouseup", () => { drag = null; });

  function selectedZone() {
    return zones.find((z) => z.id === selectedId) || null;
  }
  function renderProps() {
    const z = selectedZone();
    propsBox.style.display = z ? "" : "none";
    if (!z) return;
    $("awPropId").textContent = z.id;
    $("awPropType").value = z.type;
    $("awPropGroup").value = z.group || "";
    $("awPropLabel").value = z.label || "";
  }
  $("awPropType").addEventListener("change", () => {
    const z = selectedZone(); if (z) { z.type = $("awPropType").value; renderZones(); }
  });
  $("awPropGroup").addEventListener("input", () => {
    const z = selectedZone(); if (z) { z.group = $("awPropGroup").value.trim(); renderZones(); }
  });
  $("awPropLabel").addEventListener("input", () => {
    const z = selectedZone(); if (z) z.label = $("awPropLabel").value;
  });
  $("awPropDelete").addEventListener("click", () => {
    zones = zones.filter((z) => z.id !== selectedId);
    selectedId = null;
    renderZones();
  });

  // ── add zone by drawing a rectangle on the preview ─────────────────
  const addZoneBtn = $("awAddZone");
  let drawMode = false;       // ปุ่มถูกกด รอผู้ใช้ลากกรอบ
  let draw = null;            // {x0, y0, el} ระหว่างกำลังลาก

  function setDrawMode(on) {
    drawMode = !!on;
    addZoneBtn.classList.toggle("aw-btn-drawing", drawMode);
    addZoneBtn.textContent = drawMode
      ? "✏️ ลากกรอบบนภาพ… (Esc ยกเลิก)"
      : "+ เพิ่มโซน (ลากวาดบนภาพ)";
    stage.style.cursor = drawMode ? "crosshair" : "";
  }

  function cancelDraw() {
    if (draw) { draw.el.remove(); draw = null; }
    if (drawMode) setDrawMode(false);
  }

  addZoneBtn.addEventListener("click", () => {
    if (busy || !inspectionId) return;
    setDrawMode(!drawMode);
  });

  function drawPoint(ev) {
    const r = previewImg.getBoundingClientRect();
    return {
      x: Math.min(Math.max(ev.clientX - r.left, 0), r.width),
      y: Math.min(Math.max(ev.clientY - r.top, 0), r.height),
    };
  }
  function drawRect(ev) {
    const p = drawPoint(ev);
    return {
      x: Math.min(draw.x0, p.x), y: Math.min(draw.y0, p.y),
      w: Math.abs(p.x - draw.x0), h: Math.abs(p.y - draw.y0),
    };
  }

  stage.addEventListener("mousedown", (ev) => {
    if (!drawMode || busy || draw) return;
    ev.preventDefault();
    const p = drawPoint(ev);
    draw = { x0: p.x, y0: p.y, el: document.createElement("div") };
    draw.el.className = "aw-drawbox";
    stage.appendChild(draw.el);
  });

  document.addEventListener("mousemove", (ev) => {
    if (!draw) return;
    const q = drawRect(ev);
    draw.el.style.left = q.x + "px";
    draw.el.style.top = q.y + "px";
    draw.el.style.width = q.w + "px";
    draw.el.style.height = q.h + "px";
  });

  document.addEventListener("mouseup", (ev) => {
    if (!draw) return;
    const q = drawRect(ev);
    draw.el.remove();
    draw = null;
    setDrawMode(false);
    if (q.w < 8 || q.h < 8) return;   // คลิกเฉยๆ/กรอบจิ๋ว = ยกเลิก
    const W = dispW(), H = dispH();
    let n = zones.length + 1;
    while (zones.some((z) => z.id === "z" + n)) n++;
    const z = {
      id: "z" + n, type: "panel", group: "",
      bbox: [q.x / W, q.y / H, q.w / W, q.h / H]
        .map((v) => Math.round(v * 1e5) / 1e5),
      label: "โซน " + n,
    };
    zones.push(z);
    selectedId = z.id;
    renderZones();
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") cancelDraw();
  });

  // ── clear all zones ────────────────────────────────────────────────
  $("awClearZones").addEventListener("click", () => {
    if (busy || !zones.length) return;
    if (!confirm("ลบโซนทั้งหมด " + zones.length + " โซน?")) return;
    zones = [];
    selectedId = null;
    cancelDraw();
    renderZones();
  });

  $("awRedetect").addEventListener("click", async () => {
    if (!fileInput.files[0]) return;
    fileInput.dispatchEvent(new Event("change"));
  });

  // ── templates ──────────────────────────────────────────────────────
  async function refreshTemplates() {
    try {
      const res = await api("/api/artwork/templates");
      const sel = $("awTemplateSel");
      sel.innerHTML = '<option value="">— Template โซน (ต่อ layout โรงพิมพ์) —</option>';
      (res.templates || []).forEach((t) => {
        const o = document.createElement("option");
        o.value = t.name; o.textContent = t.name;
        sel.appendChild(o);
      });
    } catch (e) { /* non-fatal */ }
  }
  $("awTemplateLoad").addEventListener("click", async () => {
    const name = $("awTemplateSel").value;
    if (!name) return;
    try {
      const res = await api("/api/artwork/templates/" + encodeURIComponent(name));
      zones = res.zones || [];
      selectedId = null;
      renderZones();
    } catch (e) { alert("โหลด template ไม่สำเร็จ: " + e.message); }
  });
  $("awTemplateSave").addEventListener("click", async () => {
    const name = prompt("ตั้งชื่อ template (เช่น SCGP-PrintMaster-A4):",
                        $("awTemplateSel").value || "");
    if (!name) return;
    try {
      await api("/api/artwork/templates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name, zones: zones }),
      });
      await refreshTemplates();
      $("awTemplateSel").value = name;
    } catch (e) { alert("บันทึกไม่สำเร็จ: " + e.message); }
  });

  // ── inspect ────────────────────────────────────────────────────────
  $("awInspect").addEventListener("click", async () => {
    if (!inspectionId || busy) return;
    if (!zones.length) { alert("ต้องมีอย่างน้อย 1 โซน"); return; }
    setBusy(true);
    resultBox.innerHTML =
      '<div class="aw-empty"><span class="aw-spin"></span>กำลัง OCR ทีละโซนและตรวจทุกชั้น — ' +
      "โซนเยอะอาจใช้เวลาหลายสิบวินาที…</div>";
    try {
      const rep = await api("/api/artwork/" + inspectionId + "/inspect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ zones: zones, brand: brandInput.value.trim() }),
      });
      renderReport(rep, resultBox);
      showTabs(true);
      switchTab("result");
      resetTextTab();
    } catch (e) {
      resultBox.innerHTML = '<div class="aw-empty">ตรวจไม่สำเร็จ: ' + esc(e.message) + "</div>";
    } finally {
      setBusy(false);
    }
  });

  // ── result tabs: [ผลตรวจ] | [ข้อความ + คำแปล] ──────────────────────
  const resultTabs = $("awResultTabs");
  const textTab = $("awTextTab");
  const textTableWrap = $("awTextTableWrap");
  const textMsg = $("awTextMsg");
  const onlyIssuesCb = $("awTextOnlyIssues");
  let textResult = null;     // ผลล่าสุดจาก /translate (กรองโดยไม่ยิงซ้ำ)

  function showTabs(show) {
    resultTabs.style.display = show ? "" : "none";
    if (!show) { textTab.style.display = "none"; resultBox.style.display = ""; }
  }
  function switchTab(name) {
    resultTabs.querySelectorAll(".aw-tab").forEach((b) =>
      b.classList.toggle("aw-tab-active", b.dataset.awtab === name));
    const isText = name === "text";
    resultBox.style.display = isText ? "none" : "";
    textTab.style.display = isText ? "" : "none";
  }
  function resetTextTab() {
    textResult = null;
    textTableWrap.innerHTML = "";
    textMsg.textContent = "";
    onlyIssuesCb.checked = false;
  }
  resultTabs.querySelectorAll(".aw-tab").forEach((b) =>
    b.addEventListener("click", () => switchTab(b.dataset.awtab)));

  $("awTranslateBtn").addEventListener("click", async () => {
    if (!inspectionId) return;
    const btn = $("awTranslateBtn");
    btn.disabled = true;
    textMsg.innerHTML = '<span class="aw-spin"></span>กำลังสร้างตารางและแปล…';
    try {
      // Send the current zones + brand so the server can OCR on the fly when
      // no full inspection exists yet. When an inspection IS saved the server
      // ignores these and uses the stored OCR + defects instead.
      textResult = await api("/api/artwork/" + inspectionId + "/translate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ zones: zones, brand: brandInput.value.trim() }),
      });
      renderTextTable(textResult, textTableWrap, onlyIssuesCb.checked);
      if (textResult.translated)
        textMsg.textContent = textResult.cached ? "✓ แปลแล้ว (จากแคช)" : "✓ แปลเรียบร้อย";
      else
        textMsg.textContent = "";   // note แสดงในตารางอยู่แล้ว
      if (textResult.ocr_only)
        textMsg.textContent +=
          "  · ยังไม่ได้ส่งตรวจ — ตารางนี้ตรวจการสะกด/แปลเท่านั้น ยังไม่เทียบ panel (กด ‘ส่งตรวจสอบ’ เพื่อตรวจครบทุกชั้น)";
    } catch (e) {
      textMsg.textContent = "ทำงานไม่สำเร็จ: " + e.message;
    } finally {
      btn.disabled = false;
    }
  });

  onlyIssuesCb.addEventListener("change", () => {
    if (textResult) renderTextTable(textResult, textTableWrap, onlyIssuesCb.checked);
  });

  // ── vocab manager ──────────────────────────────────────────────────
  async function refreshBrands() {
    try {
      const res = await api("/api/artwork/vocab");
      const dl = $("awBrandList");
      dl.innerHTML = "";
      (res.brands || []).forEach((b) => {
        const o = document.createElement("option");
        o.value = b;
        dl.appendChild(o);
      });
    } catch (e) { /* non-fatal */ }
  }
  $("awVocabLoad").addEventListener("click", async () => {
    const brand = $("awVocabBrand").value.trim();
    if (!brand) return;
    try {
      const res = await api("/api/artwork/vocab/" + encodeURIComponent(brand));
      $("awVocabWords").value = (res.words || []).join("\n");
      $("awVocabPhrases").value = (res.phrases || []).join("\n");
      $("awVocabMsg").textContent = "โหลดแล้ว (" + (res.words || []).length +
        " คำ, " + (res.phrases || []).length + " วลี)";
    } catch (e) { $("awVocabMsg").textContent = e.message; }
  });
  $("awVocabSave").addEventListener("click", async () => {
    const brand = $("awVocabBrand").value.trim();
    if (!brand) { $("awVocabMsg").textContent = "ใส่ชื่อแบรนด์ก่อน"; return; }
    try {
      const res = await api("/api/artwork/vocab/" + encodeURIComponent(brand), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          words: $("awVocabWords").value.split("\n"),
          phrases: $("awVocabPhrases").value.split("\n"),
        }),
      });
      $("awVocabMsg").textContent = "บันทึกแล้ว ✓ (" + res.words.length +
        " คำ, " + res.phrases.length + " วลี)";
      refreshBrands();
    } catch (e) { $("awVocabMsg").textContent = e.message; }
  });

  // ── init ───────────────────────────────────────────────────────────
  refreshTemplates();
  refreshBrands();
})();
