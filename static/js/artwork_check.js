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

    html += '<div class="aw-overlay-box"><img src="/api/artwork/' +
      esc(rep.id) + '/overlay.png?t=' + Date.now() + '" alt="overlay"></div>';

    const zoneById = {};
    (rep.zones || []).forEach((z) => { zoneById[z.id] = z; });

    if ((rep.defects || []).length) {
      html += "<h4 style='margin:14px 0 8px;'>รายการที่พบ (" + rep.defects.length + ")</h4>";
      rep.defects.forEach((d, i) => {
        const z = zoneById[d.zone_id];
        html += '<div class="aw-defect ' + esc(d.severity) + '">' +
          '<span class="aw-defect-class">' + esc(d.class) + "</span>" +
          "<b>" + esc(d.zone_id) + (z && z.label ? " · " + esc(z.label) : "") + "</b><br>" +
          esc(d.message);
        if (d.found) html += '<br>พบ: <span class="found">' + esc(d.found) + "</span>";
        if (d.reference) html += ' &nbsp;เทียบกับ: <span class="ref">' + esc(d.reference) + "</span>";
        if (z) {
          const q = "x=" + z.bbox[0] + "&y=" + z.bbox[1] + "&w=" + z.bbox[2] + "&h=" + z.bbox[3];
          html += '<br><span class="crop-link" data-crop="/api/artwork/' + esc(rep.id) +
            '/crop?' + q + '" data-idx="' + i + '">🔍 ดูภาพโซนนี้ความละเอียดสูง</span>' +
            '<span id="awCropSlot' + i + '"></span>';
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
    box.querySelectorAll(".crop-link").forEach((el) => {
      el.addEventListener("click", () => {
        const slot = document.getElementById("awCropSlot" + el.dataset.idx);
        if (slot && !slot.querySelector("img")) {
          const img = document.createElement("img");
          img.src = el.dataset.crop;
          slot.appendChild(img);
        }
      });
    });
  }
  window.awRenderReport = renderReport;

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
    ["awInspect", "awAddZone", "awRedetect", "awTemplateLoad",
     "awTemplateSave"].forEach((id) => {
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
      previewImg.src = "/api/artwork/" + inspectionId + "/preview.png?t=" + Date.now();
      previewImg.onload = () => { applyZoom(); renderZones(); };
      stage.style.display = "inline-block";
      stageEmpty.style.display = "none";
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
  $("awZoomRange").addEventListener("input", (ev) => {
    zoomPct = parseInt(ev.target.value, 10) || 100;
    applyZoom();
    renderZones();
  });
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
    });
    renderProps();
  }

  let drag = null;
  function startDrag(ev, zone, el, isResize) {
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

  $("awAddZone").addEventListener("click", () => {
    let n = zones.length + 1;
    while (zones.some((z) => z.id === "z" + n)) n++;
    const z = { id: "z" + n, type: "panel", group: "",
                bbox: [0.4, 0.4, 0.2, 0.12], label: "โซน " + n };
    zones.push(z);
    selectedId = z.id;
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
    } catch (e) {
      resultBox.innerHTML = '<div class="aw-empty">ตรวจไม่สำเร็จ: ' + esc(e.message) + "</div>";
    } finally {
      setBusy(false);
    }
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
