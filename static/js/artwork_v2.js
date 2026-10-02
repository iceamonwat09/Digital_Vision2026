/* Artwork V2 — หน้าเทียบข้อความด้วย Cloud Vision (โมดูลปิด ไม่แตะตัวแปรของหน้าอื่น) */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const root = $("v2Root");
  if (!root) return;

  const COLORS = ["#2563eb", "#db2777", "#059669", "#7c3aed", "#ea580c", "#0891b2", "#4d7c0f", "#be123c"];
  const MAX_PAIRS = 8;
  const S = { job: null, page: { a: 0, b: 0 }, pairs: [], result: null, busy: false };
  const LS_KEY = "artwork_v2.session";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  async function api(url, opts) {
    let resp;
    try {
      resp = await fetch(url, opts);
    } catch (e) {
      throw new Error("ติดต่อเซิร์ฟเวอร์ไม่ได้: " + e.message);
    }
    let data = null;
    const ct = resp.headers.get("Content-Type") || "";
    if (ct.indexOf("json") >= 0) {
      try { data = await resp.json(); } catch (e) { data = null; }
    }
    if (!resp.ok) {
      const msg = (data && data.error) || ("HTTP " + resp.status);
      throw new Error(msg);
    }
    return data;
  }

  function saveSession() {
    try {
      if (S.job) localStorage.setItem(LS_KEY, JSON.stringify({ job: S.job.id, pairs: S.pairs, page: S.page }));
    } catch (e) { /* โหมดส่วนตัว / ถูกบล็อก — ไม่เป็นไร */ }
  }

  function loadSession() {
    try { return JSON.parse(localStorage.getItem(LS_KEY) || "null"); } catch (e) { return null; }
  }

  // ── ① API key ───────────────────────────────────────────────────

  function showKeyStatus(st) {
    const el = $("v2KeyStatus");
    if (!st) return;
    const where = "endpoint " + esc(st.endpoint) + " · model " + esc(st.model);
    if (st.configured) {
      el.innerHTML = '<span class="v2-ok">✔ ตั้งค่าแล้ว</span> ' + esc(st.masked) +
        " (" + (st.source === "env" ? "จาก environment variable" : "บันทึกจากหน้านี้" +
        (st.saved_at ? " เมื่อ " + esc(st.saved_at) : "")) + ") · " + where +
        (st.looks_like_google_key ? "" : ' · <span class="v2-warn">รูปแบบไม่เหมือนกุญแจของ Google (AIza…)</span>') +
        (st.env_overrides_ui ? ' · <span class="v2-warn">ค่าใน environment ทับค่าที่บันทึกในหน้านี้</span>' : "");
    } else {
      el.innerHTML = '<span class="v2-bad">✖ ยังไม่ได้ตั้ง API key</span> · ' + where;
    }
    $("v2KeyForm").classList.toggle("v2-hidden", !st.can_manage);
    if (!st.can_manage) {
      el.innerHTML += ' · <span class="v2-muted">ตั้งค่าได้เฉพาะผู้ดูแลระบบ</span>';
    }
  }

  async function loadKey() {
    try { showKeyStatus(await api("/api/artwork_v2/settings")); }
    catch (e) { $("v2KeyStatus").textContent = "อ่านสถานะไม่ได้: " + e.message; }
  }

  $("v2KeySave").addEventListener("click", async () => {
    const v = $("v2KeyInput").value.trim();
    $("v2KeyMsg").textContent = "";
    if (!v) { $("v2KeyMsg").textContent = "กรุณากรอก API key"; return; }
    try {
      showKeyStatus(await api("/api/artwork_v2/settings", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: v }),
      }));
      $("v2KeyInput").value = "";
      $("v2KeyMsg").innerHTML = '<span class="v2-ok">บันทึกแล้ว — กด “ทดสอบกุญแจ” เพื่อยืนยันว่าใช้ได้จริง</span>';
    } catch (e) { $("v2KeyMsg").innerHTML = '<span class="v2-bad">' + esc(e.message) + "</span>"; }
  });

  $("v2KeyTest").addEventListener("click", async () => {
    const b = $("v2KeyTest");
    b.disabled = true;
    $("v2KeyMsg").innerHTML = '<span class="v2-spin"></span> กำลังทดสอบกับ Cloud Vision…';
    try {
      const r = await api("/api/artwork_v2/settings/test", { method: "POST" });
      if (r.ok) {
        $("v2KeyMsg").innerHTML = '<span class="v2-ok">✔ ใช้ได้</span> · อ่านได้ว่า “' + esc(r.read_text) +
          "” · HTTP " + esc(r.http) + " · " + esc(r.ms) + " ms";
      } else {
        $("v2KeyMsg").innerHTML = '<span class="v2-bad">✖ ใช้ไม่ได้</span> · ' + esc(r.error || r.hint || "อ่านข้อความทดสอบไม่ออก") +
          (r.http && String(r.error || "").indexOf("HTTP") < 0 ? " · HTTP " + esc(r.http) : "") + " · " + esc(r.ms) + " ms" +
          (r.read_text ? " · อ่านได้ “" + esc(r.read_text) + "”" : "");
      }
    } catch (e) { $("v2KeyMsg").innerHTML = '<span class="v2-bad">' + esc(e.message) + "</span>"; }
    b.disabled = false;
  });

  $("v2KeyDel").addEventListener("click", async () => {
    if (!confirm("ลบ API key ที่บันทึกไว้?")) return;
    try {
      showKeyStatus(await api("/api/artwork_v2/settings", { method: "DELETE" }));
      $("v2KeyMsg").textContent = "ลบแล้ว";
    } catch (e) { $("v2KeyMsg").innerHTML = '<span class="v2-bad">' + esc(e.message) + "</span>"; }
  });

  // ── ② งาน ────────────────────────────────────────────────────────
  async function loadRecent() {
    try {
      const r = await api("/api/artwork_v2/jobs");
      const sel = $("v2Recent");
      sel.innerHTML = '<option value="">— เลือก —</option>' + (r.jobs || []).map((j) =>
        '<option value="' + esc(j.id) + '">' + esc(j.created + " · " + j.a + " ↔ " + j.b +
          (j.last && j.last.verdict ? " · " + j.last.verdict : "")) + "</option>").join("");
    } catch (e) { /* ไม่มีรายการก็ไม่เป็นไร */ }
  }

  $("v2Recent").addEventListener("change", (ev) => { if (ev.target.value) openJob(ev.target.value, null); });

  $("v2Upload").addEventListener("click", async () => {
    const fa = $("v2FileA").files[0], fb = $("v2FileB").files[0];
    if (!fa || !fb) { $("v2UpMsg").textContent = "เลือกไฟล์ให้ครบทั้ง 🅰 และ 🅱"; return; }
    const fd = new FormData();
    fd.append("file_a", fa);
    fd.append("file_b", fb);
    $("v2Upload").disabled = true;
    $("v2UpMsg").innerHTML = '<span class="v2-spin"></span> กำลังอัปโหลด…';
    try {
      const m = await api("/api/artwork_v2/jobs", { method: "POST", body: fd });
      $("v2UpMsg").textContent = "";
      await openJob(m.id, []);
      loadRecent();
    } catch (e) { $("v2UpMsg").innerHTML = '<span class="v2-bad">' + esc(e.message) + "</span>"; }
    $("v2Upload").disabled = false;
  });

  function fillPages(sel, n, cur) {
    sel.innerHTML = "";
    for (let i = 0; i < n; i++) {
      const o = document.createElement("option");
      o.value = String(i); o.textContent = String(i + 1);
      sel.appendChild(o);
    }
    sel.value = String(Math.min(cur, n - 1));
    sel.disabled = n <= 1;
  }

  // ผลของงานก่อนหน้า/รุ่นโค้ดก่อนหน้าต้องไม่ค้างบนจอ — ผู้ตรวจจะเข้าใจว่าเป็นผลของงานที่เปิดอยู่
  const V2_VERSION = ($("v2Root") && $("v2Root").dataset.version) || "";

  function hideResult() {
    S.result = null;
    $("v2ResCard").classList.add("v2-hidden");
    $("v2LogCard").classList.add("v2-hidden");
    $("v2Verdict").innerHTML = "";
    $("v2Warn").innerHTML = "";
    $("v2PairsRes").innerHTML = "";
    $("v2Log").value = "";
    $("v2RunMsg").textContent = "";
  }

  async function showLastRun(jobId, run) {
    try { showResult(await api("/api/artwork_v2/jobs/" + jobId + "/runs/" + run)); }
    catch (e) { $("v2RunMsg").innerHTML = '<span class="v2-bad">เปิดผลรอบ ' + esc(run) + " ไม่ได้: " + esc(e.message) + "</span>"; }
  }

  async function openJob(id, pairs, page) {
    hideResult();
    let m;
    try { m = await api("/api/artwork_v2/jobs/" + encodeURIComponent(id)); }
    catch (e) {
      $("v2UpMsg").innerHTML = '<span class="v2-bad">เปิดงานไม่ได้: ' + esc(e.message) + "</span>";
      try { localStorage.removeItem(LS_KEY); } catch (e2) { /* ไม่เป็นไร */ }
      return;
    }
    S.job = m;
    S.pairs = Array.isArray(pairs) ? pairs : [];
    S.page = page || { a: 0, b: 0 };
    $("v2DrawCard").classList.remove("v2-hidden");
    $("v2JobLabel").textContent = "· งาน " + m.id;
    $("v2NameA").textContent = m.files.a.name;
    $("v2NameB").textContent = m.files.b.name;
    fillPages($("v2PageA"), m.files.a.pages || 1, S.page.a);
    fillPages($("v2PageB"), m.files.b.pages || 1, S.page.b);
    loadPreview("a");
    loadPreview("b");
    renderZones();
    saveSession();
    if (m.runs && m.runs.length) {
      const run = m.runs[m.runs.length - 1];
      let r = null;
      try { r = await api("/api/artwork_v2/jobs/" + m.id + "/runs/" + run); }
      catch (e) { /* รอบเก่าเปิดไม่ได้ — ไม่เป็นไร */ }
      if (!r || !S.job || S.job.id !== m.id) return;          // ผู้ใช้เปิดงานอื่นไปแล้วระหว่างรอ
      if (!V2_VERSION || r.version === V2_VERSION) { showResult(r); return; }
      // ผลจากโค้ดรุ่นก่อน — ไม่แสดงเอง เพราะจะถูกอ่านว่าเป็นผลของรุ่นปัจจุบัน
      $("v2RunMsg").innerHTML = '<span class="v2-warn">ผลรอบล่าสุด (' + esc(run) + ') ตรวจด้วยรุ่น ' +
        esc(r.version || "ไม่ทราบ") + " ไม่ใช่รุ่นปัจจุบัน " + esc(V2_VERSION) +
        ' — กด "ตรวจ" เพื่อได้ผลของรุ่นนี้</span> <button class="v2-btn" id="v2ShowOld">ดูผลเดิม</button>';
      $("v2ShowOld").addEventListener("click", () => { $("v2RunMsg").textContent = ""; showResult(r); });
    }
  }

  function loadPreview(side) {
    const img = $(side === "a" ? "v2ImgA" : "v2ImgB");
    img.onload = renderZones;
    img.src = "/api/artwork_v2/jobs/" + S.job.id + "/preview/" + side + "/" + S.page[side] + ".png";
  }

  $("v2PageA").addEventListener("change", (ev) => { S.page.a = +ev.target.value; loadPreview("a"); saveSession(); });
  $("v2PageB").addEventListener("change", (ev) => { S.page.b = +ev.target.value; loadPreview("b"); saveSession(); });

  // ── ③ วาดโซน ────────────────────────────────────────────────────
  function renderZones(draft) {
    ["a", "b"].forEach((side) => {
      const ov = document.querySelector('.v2-ov[data-side="' + side + '"]');
      ov.innerHTML = "";
      S.pairs.forEach((p, i) => {
        const z = p[side];
        if (!z || z.page !== S.page[side]) return;
        ov.appendChild(zoneEl(z.bbox, COLORS[i % COLORS.length], "คู่ " + (i + 1), false));
      });
      if (draft && draft.side === side) ov.appendChild(zoneEl(draft.bbox, "#0f172a", "", true));
    });
    const box = $("v2Pairs");
    box.innerHTML = S.pairs.map((p, i) => {
      const st = (p.a ? "🅰✔" : "🅰—") + " " + (p.b ? "🅱✔" : "🅱—");
      return '<span class="v2-chip" style="border-color:' + COLORS[i % COLORS.length] + '"><b style="color:' +
        COLORS[i % COLORS.length] + '">คู่ ' + (i + 1) + "</b> " + st +
        ' <button data-del="' + i + '" title="ลบคู่นี้">✕</button></span>';
    }).join("") || '<span class="v2-muted">ยังไม่มีโซน</span>';
    const pend = S.pairs.find((p) => !p.a || !p.b);
    $("v2RunMsg").textContent = pend ? "คู่ที่ยังไม่ครบต้องวาดอีกฝั่งก่อนกดตรวจ" : "";
  }

  function zoneEl(bb, color, label, draft) {
    const d = document.createElement("div");
    d.className = "v2-zone" + (draft ? " draft" : "");
    d.style.left = bb[0] * 100 + "%";
    d.style.top = bb[1] * 100 + "%";
    d.style.width = bb[2] * 100 + "%";
    d.style.height = bb[3] * 100 + "%";
    d.style.borderColor = color;
    if (label) {
      const s = document.createElement("span");
      s.textContent = label;
      s.style.background = color;
      d.appendChild(s);
    }
    return d;
  }

  $("v2Pairs").addEventListener("click", (ev) => {
    const i = ev.target && ev.target.dataset ? ev.target.dataset.del : undefined;
    if (i === undefined) return;
    S.pairs.splice(+i, 1);
    renderZones();
    saveSession();
  });

  $("v2Clear").addEventListener("click", () => {
    if (S.pairs.length && !confirm("ล้างโซนทั้งหมด?")) return;
    S.pairs = [];
    renderZones();
    saveSession();
  });

  function normPoint(ov, ev) {
    const r = ov.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)),
            Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height))];
  }

  document.querySelectorAll(".v2-ov").forEach((ov) => {
    let start = null;
    const side = ov.dataset.side;
    ov.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0 || !S.job) return;
      ov.setPointerCapture(ev.pointerId);
      start = normPoint(ov, ev);
      ev.preventDefault();
    });
    ov.addEventListener("pointermove", (ev) => {
      if (!start) return;
      const p = normPoint(ov, ev);
      renderZones({ side: side, bbox: rect(start, p) });
    });
    ov.addEventListener("pointerup", (ev) => {
      if (!start) return;
      const p = normPoint(ov, ev);
      const bb = rect(start, p);
      start = null;
      if (bb[2] < 0.01 || bb[3] < 0.01) { renderZones(); return; }   // คลิกเปล่า
      addZone(side, { page: S.page[side], bbox: bb.map((v) => Math.round(v * 1e5) / 1e5) });
    });
    ov.addEventListener("pointercancel", () => { start = null; renderZones(); });
  });

  function rect(a, b) {
    return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1])];
  }

  function addZone(side, z) {
    // คู่ล่าสุดที่ยังขาดฝั่งนี้ = เติมให้ครบ · ไม่มี = เริ่มคู่ใหม่
    let p = S.pairs.find((x) => !x[side]);
    if (!p) {
      if (S.pairs.length >= MAX_PAIRS) { $("v2RunMsg").textContent = "วาดได้สูงสุด " + MAX_PAIRS + " คู่"; renderZones(); return; }
      p = { a: null, b: null };
      S.pairs.push(p);
    }
    p[side] = z;
    renderZones();
    saveSession();
  }

  // ── ตรวจ ─────────────────────────────────────────────────────────
  $("v2Run").addEventListener("click", async () => {
    if (!S.job || S.busy) return;
    const ready = S.pairs.filter((p) => p.a && p.b);
    if (!ready.length) { $("v2RunMsg").textContent = "ยังไม่มีโซนคู่ที่ครบ"; return; }
    if (ready.length !== S.pairs.length && !confirm("มีคู่ที่ยังไม่ครบ — ตรวจเฉพาะคู่ที่ครบ " + ready.length + " คู่?")) return;
    S.busy = true;
    $("v2Run").disabled = true;
    const t0 = Date.now();
    const tick = setInterval(() => {
      $("v2RunMsg").innerHTML = '<span class="v2-spin"></span> กำลังตรวจ ' + ready.length + " คู่… " +
        Math.round((Date.now() - t0) / 1000) + " วินาที";
    }, 500);
    try {
      const r = await api("/api/artwork_v2/jobs/" + S.job.id + "/run", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pairs: ready }),
      });
      $("v2RunMsg").textContent = "เสร็จใน " + ((Date.now() - t0) / 1000).toFixed(1) + " วินาที";
      showResult(r);
      loadRecent();
    } catch (e) {
      $("v2RunMsg").innerHTML = '<span class="v2-bad">' + esc(e.message) + "</span>";
    } finally {
      clearInterval(tick);
      S.busy = false;
      $("v2Run").disabled = false;
    }
  });

  // ── ④ ผล ─────────────────────────────────────────────────────────
  const CLASS_TH = {
    NUMBER: "ตัวเลข", CASE: "ตัวพิมพ์ใหญ่-เล็ก", TEXT: "ข้อความ", PUNCT: "เครื่องหมาย",
    FILLER: "จุดไข่ปลา/เส้นตกแต่ง",
    MISSING_IN_B: "หายไปจาก 🅱", EXTRA_IN_B: "มีเฉพาะใน 🅱",
  };

  function marked(text, span) {
    const s = span && span.length === 2 ? span : [0, 0];
    if (s[0] >= s[1]) {
      if (!text) return '<span class="v2-muted">—</span>';
      return esc(text.slice(0, s[0])) + '<mark class="v2-d">▏</mark>' + esc(text.slice(s[0]));
    }
    return esc(text.slice(0, s[0])) + '<mark class="v2-d">' + esc(text.slice(s[0], s[1])) + "</mark>" + esc(text.slice(s[1]));
  }

  function svgFor(p, side, run) {
    const sd = p.sides[side];
    const W = sd.sent_px[0], H = sd.sent_px[1];
    let g = "";
    if ($("v2ShowSkip").checked && sd.stats) {
      (sd.stats.skipped_boxes || []).forEach((b) => {
        g += '<rect class="skip" x="' + b[0] + '" y="' + b[1] + '" width="' + (b[2] - b[0]) + '" height="' + (b[3] - b[1]) + '"/>';
      });
    }
    if ($("v2ShowOcr").checked && p.lines) {
      (p.lines[side] || []).forEach((l) => {
        if (!l.box) return;
        g += '<rect class="ocr" x="' + l.box[0] + '" y="' + l.box[1] + '" width="' + (l.box[2] - l.box[0]) + '" height="' + (l.box[3] - l.box[1]) + '"/>';
      });
    }
    (p.findings || []).forEach((f) => {
      const b = f[side].box;
      if (!b) return;
      const pad = 3;
      g += '<rect class="f ' + f.severity + '" data-f="' + f.id + '" x="' + (b[0] - pad) + '" y="' + (b[1] - pad) +
        '" width="' + (b[2] - b[0] + 2 * pad) + '" height="' + (b[3] - b[1] + 2 * pad) + '"/>' +
        '<text x="' + (b[0] - pad) + '" y="' + Math.max(12, b[1] - pad - 3) + '" fill="' +
        (f.severity === "red" ? "#dc2626" : "#b45309") + '">' + f.id + "</text>";
    });
    return '<div class="v2-res-stage"><img alt="ภาพที่ส่งให้ Vision ฝั่ง ' + side.toUpperCase() + '" src="/api/artwork_v2/jobs/' +
      esc(run.job) + "/runs/" + esc(run.run) + "/img/" + esc(sd.image) + '">' +
      '<svg viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none">' + g + "</svg></div>";
  }

  function sideInfo(sd) {
    const st = sd.stats || {};
    return (sd.ok ? "" : '<div class="v2-bad">อ่านไม่ได้: ' + esc(sd.error) + "</div>") +
      '<div class="v2-muted">' + esc(sd.sent_px[0] + "×" + sd.sent_px[1] + " px") +
      (sd.render && sd.render.dpi ? " · " + esc(sd.render.dpi) + " dpi" : "") +
      " · JPEG q" + esc(sd.encode && sd.encode.quality) + " · " + esc((sd.jpeg_bytes / 1024).toFixed(0)) + " KB" +
      (st.lines != null ? " · " + st.lines + " บรรทัด · ความมั่นใจเฉลี่ย " + (st.conf_mean != null ? st.conf_mean.toFixed(2) : "-") : "") +
      (st.langs && st.langs.length ? " · ภาษา " + esc(st.langs.join(",")) : "") + "</div>";
  }

  function showResult(r) {
    S.result = r;
    $("v2ResCard").classList.remove("v2-hidden");
    $("v2LogCard").classList.remove("v2-hidden");
    $("v2Verdict").innerHTML = '<div class="v2-verdict v2-v-' + esc(r.verdict) + '">' + esc(r.verdict) + " — " +
      esc(r.verdict_th) + "<small>" + (r.reasons || []).map(esc).join(" · ") + " · รอบ " + esc(r.run) +
      " · " + esc(r.at) + (r.version ? " · รุ่น " + esc(r.version) : "") + "</small></div>";
    $("v2Warn").innerHTML = (r.warnings && r.warnings.length)
      ? '<div class="v2-warnbox">⚠️ ' + r.warnings.map(esc).join("<br>⚠️ ") + "</div>" : "";
    renderPairs();
    $("v2Log").value = r.log_text || "";
    const base = "/api/artwork_v2/jobs/" + r.job + "/runs/" + r.run;
    $("v2DlLog").href = base + "/log.txt";
    $("v2DlJson").href = base;
    $("v2DlJson").setAttribute("download", "artwork_v2_" + r.job + "_" + r.run + "_result.json");
    $("v2RawLinks").innerHTML = "ผลดิบจาก Vision: " + (r.pairs || []).map((p) => ["a", "b"].filter((s) => p.sides[s].ok)
      .map((s) => '<a href="' + base + "/raw/p" + p.n + "_" + s + '.json">p' + p.n + s.toUpperCase() + "</a>").join(" ")).join(" ");
  }

  function renderPairs() {
    const r = S.result;
    if (!r) return;
    $("v2PairsRes").innerHTML = (r.pairs || []).map((p) => {
      const rows = (p.findings || []).map((f) =>
        '<tr class="click" data-f="' + f.id + '"><td>' + f.id + '</td><td><span class="v2-sev ' + f.severity + '">' +
        (f.severity === "red" ? "ต่าง" : "ไม่มั่นใจ") + "</span></td><td>" + esc(CLASS_TH[f.class] || f.class) +
        "</td><td>" + marked(f.a.text, f.a.span) + "</td><td>" + marked(f.b.text, f.b.span) + "</td><td>" +
        (f.a.conf != null ? f.a.conf.toFixed(2) : "-") + " / " + (f.b.conf != null ? f.b.conf.toFixed(2) : "-") +
        "</td><td>" + (f.notes || []).map(esc).join("<br>") + "</td></tr>").join("");
      return '<div class="v2-card" style="margin:12px 0"><b>คู่ ' + p.n + "</b> — " + esc(p.verdict || "") +
        (p.coverage != null ? " · จับคู่ข้อความได้ " + Math.round(p.coverage * 100) + "%" : "") +
        (p.reasons && p.reasons.length ? ' <span class="v2-muted">(' + p.reasons.map(esc).join(" · ") + ")</span>" : "") +
        '<div class="v2-panes" style="margin-top:8px"><div>🅰 ' + sideInfo(p.sides.a) + svgFor(p, "a", r) +
        "</div><div>🅱 " + sideInfo(p.sides.b) + svgFor(p, "b", r) + "</div></div>" +
        (rows ? '<div class="v2-tbl-wrap"><table class="v2-tbl"><thead><tr><th>#</th><th>ระดับ</th><th>ชนิด</th><th>🅰</th><th>🅱</th><th>ความมั่นใจ A/B</th><th>หมายเหตุ</th></tr></thead><tbody>' +
          rows + "</tbody></table></div>" : (p.unreadable ? "" : '<div class="v2-muted" style="margin-top:6px">ไม่พบจุดต่าง</div>')) +
        "</div>";
    }).join("");
  }

  $("v2PairsRes").addEventListener("click", (ev) => {
    const tr = ev.target.closest ? ev.target.closest("tr[data-f]") : null;
    if (!tr) return;
    const id = tr.dataset.f;
    document.querySelectorAll("#v2PairsRes tr.sel").forEach((x) => x.classList.remove("sel"));
    tr.classList.add("sel");
    document.querySelectorAll("#v2PairsRes rect.f").forEach((x) => x.classList.toggle("hot", x.dataset.f === id));
    const hit = document.querySelector('#v2PairsRes rect.f[data-f="' + id + '"]');
    if (hit) hit.closest(".v2-res-stage").scrollIntoView({ behavior: "smooth", block: "center" });
  });

  $("v2ShowOcr").addEventListener("change", renderPairs);
  $("v2ShowSkip").addEventListener("change", renderPairs);

  // ── ⑤ Log ────────────────────────────────────────────────────────
  $("v2CopyLog").addEventListener("click", async () => {
    const t = $("v2Log").value;
    let ok = false;
    try { await navigator.clipboard.writeText(t); ok = true; } catch (e) {
      // http (ไม่ใช่ https) ใช้ clipboard API ไม่ได้ — ถอยไปใช้ execCommand
      $("v2Log").select();
      try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
    }
    $("v2CopyMsg").textContent = ok ? "คัดลอกแล้ว (" + t.length.toLocaleString("en-US") + " ตัวอักษร)" : "คัดลอกไม่ได้ — เลือกข้อความแล้วกด Ctrl+C";
  });

  // ── เริ่มต้น ─────────────────────────────────────────────────────
  loadKey();
  loadRecent();
  const sess = loadSession();
  if (sess && sess.job) openJob(sess.job, sess.pairs || [], sess.page);
})();
