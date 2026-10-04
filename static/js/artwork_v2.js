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
      const err = new Error(msg);
      err.busy = !!(data && data.busy);              // เซิร์ฟเวอร์ปฏิเสธคำขอซ้ำ (409/429) — ไม่ได้ยิง Vision
      err.retryAfter = (data && data.retry_after) || 0;
      throw err;
    }
    return data;
  }

  // โซนของแต่ละงาน (จำแยกต่องาน) — เลือกงานเดิมจากรายการแล้วได้โซนที่วาดไว้กลับมา
  const LS_ZONES = "artwork_v2.zones";
  const ZONES_KEEP = 30;

  function saveSession() {
    try {
      if (!S.job) return;
      const t = Date.now();
      localStorage.setItem(LS_KEY, JSON.stringify({ job: S.job.id, pairs: S.pairs, page: S.page, t: t }));
      const all = readJobZones();
      all[S.job.id] = { pairs: S.pairs, page: S.page, t: t };
      const ids = Object.keys(all).sort((x, y) => (all[y].t || 0) - (all[x].t || 0));
      ids.slice(ZONES_KEEP).forEach((k) => { delete all[k]; });
      localStorage.setItem(LS_ZONES, JSON.stringify(all));
    } catch (e) { /* โหมดส่วนตัว / ถูกบล็อก — ไม่เป็นไร */ }
  }

  function loadSession() {
    try { return JSON.parse(localStorage.getItem(LS_KEY) || "null"); } catch (e) { return null; }
  }

  function readJobZones() {
    try {
      const v = JSON.parse(localStorage.getItem(LS_ZONES) || "{}");
      return v && typeof v === "object" && !Array.isArray(v) ? v : {};
    } catch (e) { return {}; }
  }

  function clearSession() {
    try { localStorage.removeItem(LS_KEY); } catch (e) { /* ไม่เป็นไร */ }
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
  // วิธีวาดกรอบจุดต่าง: "word" = เส้นบางรอบคำ + แถบสีบนตัวอักษรที่ต่าง · "span" = แบบเดิม
  const BOX_STYLE = ($("v2Root") && $("v2Root").dataset.boxStyle) === "span" ? "span" : "word";
  const FRAME_PAD = 0.22;   // ระยะเผื่อจากตัวอักษรถึงเส้นกรอบ = สัดส่วนของความสูงคำ

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
    if ($("v2Restore")) $("v2Restore").style.display = "none";   // เปิดงานอื่นแล้ว แถบถามเรื่องงานค้างหมดความหมาย
    let m;
    try { m = await api("/api/artwork_v2/jobs/" + encodeURIComponent(id)); }
    catch (e) {
      $("v2UpMsg").innerHTML = '<span class="v2-bad">เปิดงานไม่ได้: ' + esc(e.message) + "</span>";
      try { localStorage.removeItem(LS_KEY); } catch (e2) { /* ไม่เป็นไร */ }
      return;
    }
    if (!Array.isArray(pairs)) {
      // เปิดจากรายการ "งานล่าสุด" — โซนที่แก้ล่าสุดในเบราว์เซอร์นี้ก่อน ไม่มีค่อยใช้โซนของรอบตรวจล่าสุด
      const saved = readJobZones()[m.id];
      if (saved && Array.isArray(saved.pairs) && saved.pairs.length) {
        pairs = saved.pairs;
        page = page || saved.page;
      } else if (Array.isArray(m.last_pairs) && m.last_pairs.length) {
        pairs = m.last_pairs;
        page = page || { a: m.last_pairs[0].a.page || 0, b: m.last_pairs[0].b.page || 0 };
      } else {
        pairs = [];
      }
    }
    S.job = m;
    S.pairs = pairs;
    sel = null;
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
    img.onload = () => {
      // ภาพใหม่ (เปิดงาน/เปลี่ยนหน้า) เริ่มที่ "พอดีความกว้าง" เสมอ
      setZoom(side, fitPct(side, false), [0, 0], true);
      boxOf(side).scrollLeft = 0;
      boxOf(side).scrollTop = 0;
      renderZones();
    };
    img.src = "/api/artwork_v2/jobs/" + S.job.id + "/preview/" + side + "/" + S.page[side] + ".png";
  }

  $("v2PageA").addEventListener("change", (ev) => { S.page.a = +ev.target.value; loadPreview("a"); saveSession(); });
  $("v2PageB").addEventListener("change", (ev) => { S.page.b = +ev.target.value; loadPreview("b"); saveSession(); });

  // ── ③ วาดโซน ────────────────────────────────────────────────────
  // ซูม/เลื่อนภาพ/ย้าย-ย่อขยายโซน แบบหน้า Artwork เดิม · พิกัดโซนเป็นสัดส่วน 0..1 ของหน้าเสมอ
  // ⇒ ซูมเท่าไรก็ได้กรอบเดิม (ซูมเปลี่ยนแค่ขนาดที่แสดง ไม่แตะภาพที่ส่งให้ Vision)
  const ZOOM_MIN = 10, ZOOM_MAX = 400, MIN_ZONE = 0.005;
  const HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
  const Z = { a: { pct: 100, fit: true }, b: { pct: 100, fit: true } };
  let mode = "draw";              // draw = ลากที่ว่างเพื่อวาด · pan = ลากเพื่อเลื่อนภาพ
  let sel = null;                 // {pi, side} โซนที่เลือก (กด Delete เพื่อลบ)
  let spaceDown = false;

  const boxOf = (side) => $(side === "a" ? "v2BoxA" : "v2BoxB");
  const stageOf = (side) => $(side === "a" ? "v2StageA" : "v2StageB");
  const imgOf = (side) => $(side === "a" ? "v2ImgA" : "v2ImgB");
  const ovOf = (side) => document.querySelector('.v2-ov[data-side="' + side + '"]');
  const zbarOf = (side) => document.querySelector('.v2-zbar[data-side="' + side + '"]');

  function natSize(side) {
    const im = imgOf(side);
    return [im.naturalWidth || 0, im.naturalHeight || 0];
  }

  function fitPct(side, whole) {
    const [w, h] = natSize(side), box = boxOf(side);
    if (!w || !h || !box) return 100;
    let p = (box.clientWidth - 2) / w * 100;
    if (whole) p = Math.min(p, (box.clientHeight - 2) / h * 100);
    return Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Math.floor(p)));   // ปัดลง ⇒ ไม่ล้นกล่อง
  }

  // anchor = จุดบนกล่อง (px) ที่ต้องอยู่ที่เดิมหลังซูม (ใต้เมาส์ / กลางกล่อง)
  function setZoom(side, pct, anchor, keepFit) {
    const [w] = natSize(side), box = boxOf(side), st = stageOf(side);
    pct = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Math.round(pct)));
    const z = Z[side];
    z.fit = !!keepFit;
    if (!w || !box) { z.pct = pct; syncZbar(side); return; }
    const oldW = st.offsetWidth || w * z.pct / 100;
    const ax = anchor ? anchor[0] : box.clientWidth / 2, ay = anchor ? anchor[1] : box.clientHeight / 2;
    const fx = (box.scrollLeft + ax) / oldW, fy = (box.scrollTop + ay) / (st.offsetHeight || 1);
    z.pct = pct;
    st.style.width = Math.round(w * pct / 100) + "px";
    box.scrollLeft = fx * st.offsetWidth - ax;
    box.scrollTop = fy * st.offsetHeight - ay;
    syncZbar(side);
  }

  function syncZbar(side) {
    const bar = zbarOf(side);
    if (!bar) return;
    bar.querySelector('[data-z="range"]').value = Z[side].pct;
    bar.querySelector(".v2-zpct").textContent = Z[side].pct + "%";
    bar.querySelector('[data-z="fitw"]').classList.toggle("on", Z[side].fit === true);
  }

  function refit(side) { if (Z[side].fit) setZoom(side, fitPct(side, false), null, true); }

  document.querySelectorAll(".v2-zbar").forEach((bar) => {
    const side = bar.dataset.side;
    bar.addEventListener("click", (ev) => {
      const k = ev.target && ev.target.dataset ? ev.target.dataset.z : null;
      if (k === "in") setZoom(side, Z[side].pct * 1.25);
      else if (k === "out") setZoom(side, Z[side].pct / 1.25);
      else if (k === "fitw") setZoom(side, fitPct(side, false), null, true);
      else if (k === "fitp") setZoom(side, fitPct(side, true));
      else if (k === "100") setZoom(side, 100);
    });
    bar.querySelector('[data-z="range"]').addEventListener("input", (ev) => setZoom(side, +ev.target.value));
  });

  ["a", "b"].forEach((side) => {
    const box = boxOf(side);
    // ล้อเมาส์บนภาพ = ซูมรอบจุดใต้เมาส์ (เหมือนหน้า Artwork เดิม · ไม่ต้องกด Ctrl)
    box.addEventListener("wheel", (ev) => {
      if (!natSize(side)[0]) return;
      ev.preventDefault();
      const r = box.getBoundingClientRect();
      const f = ev.deltaY < 0 ? 1.15 : 1 / 1.15;
      setZoom(side, Z[side].pct * f, [ev.clientX - r.left, ev.clientY - r.top]);
    }, { passive: false });
    if (window.ResizeObserver) new ResizeObserver(() => refit(side)).observe(box);
  });

  // ── เลื่อนภาพ: โหมด ✋ · ปุ่มกลาง (ล้อ) ลาก · กด Space ค้างแล้วลาก ──
  let pan = null;
  function panStart(side, ev) {
    const box = boxOf(side);
    pan = { box: box, x: ev.clientX, y: ev.clientY, sl: box.scrollLeft, st: box.scrollTop, id: ev.pointerId };
    box.setPointerCapture(ev.pointerId);
    box.classList.add("panning");
    ev.preventDefault();
  }
  ["a", "b"].forEach((side) => {
    const box = boxOf(side);
    box.addEventListener("pointerdown", (ev) => {
      const want = ev.button === 1 || (ev.button === 0 && (mode === "pan" || spaceDown));
      if (!want || !S.job) return;
      ev.stopPropagation();
      panStart(side, ev);
    }, { capture: true });
    box.addEventListener("pointermove", (ev) => {
      if (!pan || pan.box !== box) return;
      box.scrollLeft = pan.sl - (ev.clientX - pan.x);
      box.scrollTop = pan.st - (ev.clientY - pan.y);
    });
    const end = () => { if (pan && pan.box === box) { pan = null; box.classList.remove("panning"); } };
    box.addEventListener("pointerup", end);
    box.addEventListener("pointercancel", end);
    box.addEventListener("auxclick", (ev) => { if (ev.button === 1) ev.preventDefault(); });
  });

  function setMode(m) {
    mode = m;
    root.classList.toggle("v2-mode-pan", m === "pan");
    document.querySelectorAll("[data-mode]").forEach((b) => b.classList.toggle("on", b.dataset.mode === m));
  }
  document.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));

  function typing(ev) {
    const t = ev.target;
    return t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
  }
  document.addEventListener("keydown", (ev) => {
    if (typing(ev) || $("v2DrawCard").classList.contains("v2-hidden")) return;
    if (ev.code === "Space") {
      if (!spaceDown) { spaceDown = true; root.classList.add("v2-space"); }
      if (ev.target === document.body) ev.preventDefault();       // กันหน้าเลื่อนลง
      return;
    }
    if ((ev.key === "Delete" || ev.key === "Backspace") && sel) {
      ev.preventDefault();
      removeZone(sel.pi, sel.side);
    } else if (ev.key === "Escape" && sel) {
      sel = null;
      renderZones();
    }
  });
  document.addEventListener("keyup", (ev) => {
    if (ev.code === "Space") { spaceDown = false; root.classList.remove("v2-space"); }
  });
  window.addEventListener("blur", () => { spaceDown = false; root.classList.remove("v2-space"); });

  function removeZone(pi, side) {
    const p = S.pairs[pi];
    if (!p) return;
    p[side] = null;
    if (!p.a && !p.b) S.pairs.splice(pi, 1);       // ลบทั้งสองฝั่ง = ลบคู่ (เลขคู่ถัดไปเลื่อนขึ้น)
    sel = null;
    renderZones();
    saveSession();
  }

  function renderZones(draft) {
    ["a", "b"].forEach((side) => {
      const ov = ovOf(side);
      ov.innerHTML = "";
      S.pairs.forEach((p, i) => {
        const z = p[side];
        if (!z || z.page !== S.page[side]) return;
        const on = sel && sel.pi === i && sel.side === side;
        ov.appendChild(zoneEl(z.bbox, COLORS[i % COLORS.length], "คู่ " + (i + 1), false, i, side, on));
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
    if (!S.busy) $("v2RunMsg").textContent = pend ? "คู่ที่ยังไม่ครบต้องวาดอีกฝั่งก่อนกดตรวจ" : "";
  }

  function zoneEl(bb, color, label, draft, pi, side, selected) {
    const d = document.createElement("div");
    d.className = "v2-zone" + (draft ? " draft" : "") + (selected ? " sel" : "");
    d.style.left = bb[0] * 100 + "%";
    d.style.top = bb[1] * 100 + "%";
    d.style.width = bb[2] * 100 + "%";
    d.style.height = bb[3] * 100 + "%";
    d.style.borderColor = color;
    if (!draft) {
      d.dataset.pi = String(pi);
      d.dataset.side = side;
      d.title = "ลากเพื่อย้าย · ลากมุม/ขอบเพื่อย่อขยาย · คลิกแล้วกด Delete เพื่อลบ";
    }
    if (label) {
      const s = document.createElement("span");
      s.textContent = label;
      s.style.background = color;
      d.appendChild(s);
    }
    if (selected) {
      HANDLES.forEach((h) => {
        const k = document.createElement("i");
        k.className = "v2-h v2-h-" + h;
        k.dataset.h = h;
        d.appendChild(k);
      });
    }
    return d;
  }

  $("v2Pairs").addEventListener("click", (ev) => {
    const i = ev.target && ev.target.dataset ? ev.target.dataset.del : undefined;
    if (i === undefined) return;
    S.pairs.splice(+i, 1);
    sel = null;
    renderZones();
    saveSession();
  });

  $("v2Clear").addEventListener("click", () => {
    if (S.pairs.length && !confirm("ล้างโซนทั้งหมด?")) return;
    S.pairs = [];
    sel = null;
    renderZones();
    saveSession();
  });

  function normPoint(ov, ev) {
    const r = ov.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)),
            Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height))];
  }

  const r5 = (v) => Math.round(v * 1e5) / 1e5;

  // ย้าย/ย่อขยายกรอบ — คืนกรอบใหม่ที่อยู่ในหน้าเสมอและไม่เล็กกว่า MIN_ZONE
  function editBox(b0, h, dx, dy) {
    let [x0, y0, x1, y1] = [b0[0], b0[1], b0[0] + b0[2], b0[1] + b0[3]];
    if (h === "move") {
      const mx = Math.min(Math.max(dx, -x0), 1 - x1), my = Math.min(Math.max(dy, -y0), 1 - y1);
      return [x0 + mx, y0 + my, b0[2], b0[3]];
    }
    if (h.indexOf("w") >= 0) x0 = Math.min(Math.max(0, x0 + dx), x1 - MIN_ZONE);
    if (h.indexOf("e") >= 0) x1 = Math.max(Math.min(1, x1 + dx), x0 + MIN_ZONE);
    if (h.indexOf("n") >= 0) y0 = Math.min(Math.max(0, y0 + dy), y1 - MIN_ZONE);
    if (h.indexOf("s") >= 0) y1 = Math.max(Math.min(1, y1 + dy), y0 + MIN_ZONE);
    return [x0, y0, x1 - x0, y1 - y0];
  }

  document.querySelectorAll(".v2-ov").forEach((ov) => {
    let act = null;        // {kind: "draw"|"edit", start, pi, h, b0, moved}
    const side = ov.dataset.side;
    ov.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0 || !S.job || mode === "pan" || spaceDown) return;
      const start = normPoint(ov, ev);
      const t = ev.target;
      const zEl = t && t.closest ? t.closest(".v2-zone[data-pi]") : null;
      if (zEl) {
        const pi = +zEl.dataset.pi;
        const z = S.pairs[pi] && S.pairs[pi][side];
        if (!z) return;
        sel = { pi: pi, side: side };
        act = { kind: "edit", start: start, pi: pi, h: (t.dataset && t.dataset.h) || "move",
                b0: z.bbox.slice(), moved: false };
        renderZones();
      } else {
        if (sel) { sel = null; renderZones(); }
        act = { kind: "draw", start: start };
      }
      ov.setPointerCapture(ev.pointerId);
      ev.preventDefault();
    });
    ov.addEventListener("pointermove", (ev) => {
      if (!act) return;
      const p = normPoint(ov, ev);
      if (act.kind === "draw") { renderZones({ side: side, bbox: rect(act.start, p) }); return; }
      const z = S.pairs[act.pi] && S.pairs[act.pi][side];
      if (!z) return;
      z.bbox = editBox(act.b0, act.h, p[0] - act.start[0], p[1] - act.start[1]).map(r5);
      act.moved = true;
      renderZones();
    });
    ov.addEventListener("pointerup", (ev) => {
      if (!act) return;
      const a = act;
      act = null;
      if (a.kind === "edit") { if (a.moved) saveSession(); return; }
      const bb = rect(a.start, normPoint(ov, ev));
      if (bb[2] < 0.01 || bb[3] < 0.01) { renderZones(); return; }   // คลิกเปล่า
      addZone(side, { page: S.page[side], bbox: bb.map(r5) });
    });
    ov.addEventListener("pointercancel", () => {
      if (act && act.kind === "edit") {
        const z = S.pairs[act.pi] && S.pairs[act.pi][side];
        if (z) z.bbox = act.b0;                       // ยกเลิกกลางคัน = คืนกรอบเดิม
      }
      act = null;
      renderZones();
    });
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
    sel = { pi: S.pairs.indexOf(p), side: side };
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
    let holdMs = 0;
    const t0 = Date.now();
    const tick = setInterval(() => {
      $("v2RunMsg").innerHTML = '<span class="v2-spin"></span> กำลังตรวจ ' + ready.length + " คู่… " +
        Math.round((Date.now() - t0) / 1000) + " วินาที";
    }, 500);
    try {
      const r = await api("/api/artwork_v2/jobs/" + S.job.id + "/run", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pairs: ready, sharpness: $("v2Sharp") ? $("v2Sharp").value : undefined,
          ai_mode: $("v2Ai") ? $("v2Ai").value : undefined }),
      });
      $("v2RunMsg").textContent = "เสร็จใน " + ((Date.now() - t0) / 1000).toFixed(1) + " วินาที";
      showResult(r);
      loadRecent();
    } catch (e) {
      if (e.busy) {
        // ไม่ใช่ความล้มเหลวของการตรวจ — คำขอนี้ไม่ถูกส่งไป Vision · ผลเดิมบนจอยังใช้ได้
        $("v2RunMsg").innerHTML = '<span class="v2-warn">⏳ ' + esc(e.message) + "</span>";
        holdMs = Math.min(30000, Math.ceil(e.retryAfter || 0) * 1000);
      } else {
        $("v2RunMsg").innerHTML = '<span class="v2-bad">' + esc(e.message) + "</span>";
      }
    } finally {
      clearInterval(tick);
      if (holdMs > 0) {
        setTimeout(() => { S.busy = false; $("v2Run").disabled = false; }, holdMs);
      } else {
        S.busy = false;
        $("v2Run").disabled = false;
      }
    }
  });

  // ── ④ ผล ─────────────────────────────────────────────────────────
  const CLASS_TH = {
    NUMBER: "ตัวเลข", CASE: "ตัวพิมพ์ใหญ่-เล็ก", TEXT: "ข้อความ", PUNCT: "เครื่องหมาย",
    FILLER: "จุดไข่ปลา/เส้นตกแต่ง", FRACTION: "เศษส่วน (OCR อ่านไม่นิ่ง)",
    MISSING_IN_B: "หายไปจาก 🅱", EXTRA_IN_B: "มีเฉพาะใน 🅱",
    CURVED: "ข้อความโค้ง/เอียง",
  };

  // การ์ด "ข้อความโค้ง/เอียง" รวมหลายจุด — แสดงคำของทุกสมาชิก (ไม่มีจุดไหนถูกลบ)
  function cellText(f, side) {
    if (f.class !== "CURVED") return marked(f[side].text, f[side].span);
    return (f.members || []).map((m) =>
      '<span class="v2-sev ' + m.severity + '" style="font-size:11px">' + (m.severity === "red" ? "ต่าง" : "?") +
      "</span> " + marked(m[side].text, m[side].span)).join("<br>");
  }

  const SEV_TH = { red: "ต่าง", yellow: "ไม่มั่นใจ", debris: "เศษ", dismissed: "AI: สัญญาณรบกวน" };
  const AI_TH = { real: "ต่างจริง", noise: "สัญญาณรบกวนของ OCR", uncertain: "ไม่แน่ใจ" };
  function pct(v) { return v == null ? "-" : Math.round(v * 100) + "%"; }

  // % ความมั่นใจ = ค่าต่ำสุดที่ Vision มั่นใจในตัวอักษรที่ต่าง (ทั้งสองฝั่ง) — ไม่ใช่ตัวเลขจาก AI
  function confCell(f) {
    const c = f.confidence;
    const lo = c != null && c < 0.8;
    return '<span class="v2-conf' + (lo ? " lo" : "") + '" title="ค่าความมั่นใจของ Vision ตรงตัวอักษรที่ต่าง">' +
      pct(c) + '</span><br><span class="v2-muted">A ' + pct(f.a.conf) + " · B " + pct(f.b.conf) + "</span>";
  }

  function aiNote(f) {
    const ai = f.ai;
    if (!ai) return "";
    if (!ai.verdict) return '<span class="v2-ai-note v2-muted">🤖 AI ไม่ได้ตอบจุดนี้</span>';
    return '<span class="v2-ai-note">🤖 <b>' + esc(AI_TH[ai.verdict] || ai.verdict) + "</b>" +
      (ai.reason ? " — " + esc(ai.reason) : "") + "</span>" +
      (ai.suggestion ? '<span class="v2-ai-note">💡 ' + esc(ai.suggestion) + "</span>" : "");
  }

  function findingRow(f) {
    const sev = SEV_TH[f.severity] || f.severity;
    const notes = (f.notes || []).filter((n) => !/^AI: /.test(n)).map(esc).join("<br>");
    return '<tr class="click" data-f="' + f.id + '"><td>' + f.id + '</td><td><span class="v2-sev ' + f.severity + '">' +
      sev + "</span></td><td>" + esc(CLASS_TH[f.class] || f.class) +
      (f.source === "ai" ? '<span class="v2-ai-tag">AI</span>' : "") +
      "</td><td>" + cellText(f, "a") + "</td><td>" + cellText(f, "b") + "</td><td>" + confCell(f) +
      "</td><td>" + notes + aiNote(f) + "</td></tr>";
  }
  const TBL_HEAD = '<thead><tr><th>#</th><th>ระดับ</th><th>ชนิด</th><th>🅰</th><th>🅱</th><th>ความมั่นใจ (Vision)</th><th>หมายเหตุ</th></tr></thead>';

  const AI_MODE_TH = { assist: "อัลกอริทึมตัดสิน + AI เสริม", judge: "AI ตัดสินหลัก", off: "ปิด AI" };
  function folded(title, list) {
    if (!list || !list.length) return "";
    return '<details class="v2-debris" style="margin-top:8px"><summary>' + esc(title) + " (" + list.length +
      ')</summary><div class="v2-tbl-wrap"><table class="v2-tbl">' + TBL_HEAD + "<tbody>" +
      list.map(findingRow).join("") + "</tbody></table></div></details>";
  }

  function aiBox(p) {
    const ai = p.ai;
    if (!ai || ai.status === "off") return "";
    const head = "<h4>🤖 AI ตรวจทาน (Gemini) — " + esc(AI_MODE_TH[ai.mode] || ai.mode) + "</h4>";
    if (ai.status !== "ok") {
      return '<div class="v2-ai">' + head + '<div class="v2-bad">' +
        esc(ai.status === "skipped" ? (ai.reason || "ข้าม") : "ไม่สำเร็จ: " + (ai.error || "-")) +
        "</div><div class=\"v2-muted\">ผลที่แสดงมาจากอัลกอริทึมทั้งหมด</div></div>";
    }
    const vc = ai.vision_conf || {};
    let stat = "ข้อมูลที่ AI ใช้ตอบ: Vision มั่นใจเฉลี่ย A " + pct(vc.a) + " · B " + pct(vc.b);
    if (ai.ref_accuracy != null) {
      const tot = (ai.items_total || 0) + (ai.reviews_total || 0);
      const ok = (ai.items_valid || 0) + (ai.reviews_valid || 0);
      stat += " · AI อ้างอิงข้อมูล Vision ถูกต้อง " + ok + "/" + tot + " ข้อ (" + pct(ai.ref_accuracy) + ")";
    }
    if (ai.mode === "assist" && ai.reviewable) stat += " · ตอบครบ " + ai.reviewed + "/" + ai.reviewable + " จุด";
    if (ai.extra_added) stat += " · พบเพิ่ม " + ai.extra_added + " จุด";
    stat += (ai.ms != null ? " · " + (ai.ms / 1000).toFixed(1) + " วินาที" : "");
    const sugs = (ai.suggestions || []).map((x) => "<li>" + esc(x) + "</li>").join("");
    const bad = (ai.invalid || []).length
      ? '<details style="margin-top:6px"><summary class="v2-muted">คำตอบที่ไม่ได้ใช้ เพราะไม่ตรงกับข้อมูล Vision (' + ai.invalid.length +
        ")</summary><ul>" + ai.invalid.map((x) => "<li>" + esc(x.what + ": " + x.reason) + "</li>").join("") + "</ul></details>" : "";
    return '<div class="v2-ai">' + head + '<div class="v2-muted">' + esc(stat) + "</div>" +
      (ai.summary ? '<div style="margin-top:6px"><b>สรุป:</b> ' + esc(ai.summary) + "</div>" : "") +
      (sugs ? '<div style="margin-top:6px"><b>ข้อเสนอแนะ:</b><ul>' + sugs + "</ul></div>" : "") +
      '<div class="v2-muted" style="margin-top:6px">ข้อความสรุป/ข้อเสนอแนะเขียนโดย AI — ตรวจทานก่อนใช้ · % ความมั่นใจทุกจุดคิดจาก Vision ไม่ใช่จาก AI</div>' +
      bad + "</div>";
  }

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
    let tags = "";
    (p.findings || []).forEach((f) => {
      if (BOX_STYLE === "span") { g += spanFrame(f, side); return; }
      const d = wordFrame(f, side, W, H);
      g += d.svg;
      tags += d.tag;
    });
    return '<div class="v2-res-stage"><img alt="ภาพที่ส่งให้ Vision ฝั่ง ' + side.toUpperCase() + '" src="/api/artwork_v2/jobs/' +
      esc(run.job) + "/runs/" + esc(run.run) + "/img/" + esc(sd.image) + '">' +
      '<svg viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none">' + g + "</svg>" + tags + "</div>";
  }

  // กรอบแบบเดิม (ARTWORK_V2_BOX_STYLE=span) — รอบตัวอักษรที่ต่าง เผื่อ 3 px ของภาพที่ส่ง
  function spanFrame(f, side) {
    const b = f[side].box;
    if (!b) return "";
    const pad = 3;
    return '<rect class="f ' + f.severity + '" data-f="' + f.id + '" x="' + (b[0] - pad) + '" y="' + (b[1] - pad) +
      '" width="' + (b[2] - b[0] + 2 * pad) + '" height="' + (b[3] - b[1] + 2 * pad) + '"/>' +
      '<text x="' + (b[0] - pad) + '" y="' + Math.max(12, b[1] - pad - 3) + '" fill="' +
      (f.severity === "red" ? "#dc2626" : "#b45309") + '">' + f.id + "</text>";
  }

  // กรอบแบบใหม่ (ค่าเริ่มต้น) — แสดงผลล้วน ไม่แตะผลตรวจ
  //  · กรอบเส้นบางรอบ "คำเต็ม" (word_box) เผื่อห่างจากตัวอักษรตามความสูงตัวอักษร ⇒ เส้นอยู่ในที่ว่าง ไม่ทับหมึก
  //  · ตัวอักษรที่ต่างจริง (box) = แถบสีโปร่งใสสูงเท่าคำ ⇒ เห็นว่าต่างตรงไหนโดยตัวหนังสือยังอ่านออก
  //  · เลขจุดต่างเป็นป้าย HTML นอกกรอบ (ขนาดตัวอักษรคงที่ ไม่ย่อตามภาพ)
  function wordFrame(f, side, W, H) {
    const sd = f[side];
    const b = sd.box, wb = sd.word_box || b;
    if (!wb) return { svg: "", tag: "" };
    const h = Math.max(1, wb[3] - wb[1]);
    const pad = Math.max(2, h * FRAME_PAD);
    const x0 = Math.max(0, wb[0] - pad), y0 = Math.max(0, wb[1] - pad);
    const x1 = Math.min(W, wb[2] + pad), y1 = Math.min(H, wb[3] + pad);
    let svg = '<rect class="f ' + f.severity + '" data-f="' + f.id + '" x="' + x0 + '" y="' + y0 +
      '" width="' + (x1 - x0) + '" height="' + (y1 - y0) + '" rx="' + (pad * 0.6) + '"/>';
    if (b) {
      const bw = b[2] - b[0];
      const narrow = bw < (b[3] - b[1]) * 0.2;            // จุดแทรก (อีกฝั่งมีตัวอักษรที่ฝั่งนี้ไม่มี)
      const w = narrow ? Math.max(2, h * 0.12) : bw;
      const cx = (b[0] + b[2]) / 2;
      // ต่างทั้งคำ/ทั้งบรรทัด ⇒ กรอบอย่างเดียวพอ · ไม่มี word_box (ผลรุ่นเก่า) ⇒ กรอบอย่างเดียว
      const whole = !narrow && (!sd.word_box || bw >= (wb[2] - wb[0]) * 0.97);
      if (!whole) {
        svg += '<rect class="d ' + f.severity + (narrow ? " ins" : "") + '" data-f="' + f.id + '" x="' +
          (narrow ? cx - w / 2 : b[0]) + '" y="' + wb[1] + '" width="' + w + '" height="' + h + '"/>';
      }
    }
    const above = y0 / H > 0.04;
    const tag = '<span class="v2-tag ' + f.severity + (above ? "" : " below") + '" data-f="' + f.id + '" style="left:' +
      (x0 / W * 100).toFixed(3) + "%;top:" + ((above ? y0 : y1) / H * 100).toFixed(3) + '%">' + esc(f.id) + "</span>";
    return { svg: svg, tag: tag };
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
      " · " + esc(r.at) + (r.version ? " · รุ่น " + esc(r.version) : "") +
      (r.sharpness ? " · ภาพที่ส่ง: " + (r.sharpness === "max" ? "คมสูงสุด" : "มาตรฐาน 400 dpi") : "") +
      (r.ai && r.ai.mode ? " · AI: " + esc(AI_MODE_TH[r.ai.mode] || r.ai.mode) : "") +
      "</small></div>";
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
      const rows = (p.findings || []).map(findingRow).join("");
      const debHtml = folded("เศษอักขระ / ขอบโซน — ไม่นับในผลตัดสิน", p.debris) +
        folded("อัลกอริทึมพบ แต่ AI ไม่ได้ระบุ — ไม่นับในผลตัดสิน (โหมด AI ตัดสินหลัก)", p.algo_only) +
        folded("AI ตัดสินว่าเป็นสัญญาณรบกวนของ OCR — ไม่นับในผลตัดสิน", p.ai_dismissed);
      return '<div class="v2-card" style="margin:12px 0"><b>คู่ ' + p.n + "</b> — " + esc(p.verdict || "") +
        (p.coverage != null ? " · จับคู่ข้อความได้ " + Math.round(p.coverage * 100) + "%" : "") +
        (p.reasons && p.reasons.length ? ' <span class="v2-muted">(' + p.reasons.map(esc).join(" · ") + ")</span>" : "") +
        '<div class="v2-panes" style="margin-top:8px"><div>🅰 ' + sideInfo(p.sides.a) + svgFor(p, "a", r) +
        "</div><div>🅱 " + sideInfo(p.sides.b) + svgFor(p, "b", r) + "</div></div>" +
        (rows ? '<div class="v2-tbl-wrap"><table class="v2-tbl">' + TBL_HEAD + "<tbody>" +
          rows + "</tbody></table></div>" : (p.unreadable ? "" : '<div class="v2-muted" style="margin-top:6px">ไม่พบจุดต่าง</div>')) +
        aiBox(p) + debHtml + "</div>";
    }).join("");
  }

  $("v2PairsRes").addEventListener("click", (ev) => {
    const tr = ev.target.closest ? ev.target.closest("tr[data-f]") : null;
    if (!tr) return;
    const id = tr.dataset.f;
    document.querySelectorAll("#v2PairsRes tr.sel").forEach((x) => x.classList.remove("sel"));
    tr.classList.add("sel");
    document.querySelectorAll("#v2PairsRes rect.f, #v2PairsRes rect.d, #v2PairsRes .v2-tag")
      .forEach((x) => x.classList.toggle("hot", x.dataset.f === id));
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
  if (sess && sess.job) {
    if ($("v2Root") && $("v2Root").dataset.restoreConfirm === "0") openJob(sess.job, sess.pairs || [], sess.page);
    else offerRestore(sess);
  }

  // งานที่ค้างไว้ ⇒ ถามก่อนเสมอ (แบบโหมด Artwork เดิม) — เปิดเงียบ ๆ แล้วผู้ตรวจอาจเข้าใจว่าเป็นงานใหม่
  async function offerRestore(s) {
    const bar = $("v2Restore");
    if (!bar) return;
    const days = parseFloat(($("v2Root") && $("v2Root").dataset.restoreDays) || "7");
    if (s.t && days > 0 && Date.now() - s.t > days * 864e5) { clearSession(); return; }
    let m;
    try { m = await api("/api/artwork_v2/jobs/" + encodeURIComponent(s.job)); }
    catch (e) { clearSession(); return; }           // งานถูกลบ / ไม่มีสิทธิ์ — ไม่เสนอ
    if (S.job) return;                              // ผู้ใช้เปิดงานอื่นไปแล้วระหว่างรอ
    const n = Array.isArray(s.pairs) ? s.pairs.filter((p) => p && (p.a || p.b)).length : 0;
    bar.innerHTML = "💾 พบงานที่ค้างไว้ — <b>" + esc(m.files.a.name) + " ↔ " + esc(m.files.b.name) +
      "</b> · " + n + " คู่โซน" + (s.t ? " (บันทึกเมื่อ " + esc(new Date(s.t).toLocaleString("th-TH")) + ")" : "") +
      ' <button class="v2-btn primary" id="v2RestoreYes">เปิดต่อ</button>' +
      ' <button class="v2-btn" id="v2RestoreNo">ทิ้ง</button>';
    bar.style.display = "";
    $("v2RestoreYes").addEventListener("click", () => {
      bar.style.display = "none";
      openJob(s.job, Array.isArray(s.pairs) ? s.pairs : [], s.page);
    });
    $("v2RestoreNo").addEventListener("click", () => {
      bar.style.display = "none";
      clearSession();
    });
  }
})();
