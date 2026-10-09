/* Artwork V2 — หน้าเทียบข้อความด้วย Cloud Vision (โมดูลปิด ไม่แตะตัวแปรของหน้าอื่น) */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const root = $("v2Root");
  if (!root) return;

  const COLORS = ["#2563eb", "#db2777", "#059669", "#7c3aed", "#ea580c", "#0891b2", "#4d7c0f", "#be123c"];
  const MAX_PAIRS = 8;
  const S = { job: null, page: { a: 0, b: 0 }, rot: { a: 0, b: 0 }, pairs: [], result: null, busy: false };
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
      localStorage.setItem(LS_KEY, JSON.stringify({ job: S.job.id, pairs: S.pairs, page: S.page, rot: S.rot, t: t }));
      const all = readJobZones();
      all[S.job.id] = { pairs: S.pairs, page: S.page, rot: S.rot, t: t };
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
  // ชี้เมาส์ที่แถวในตาราง ⇒ ซูมภาพไปที่จุดนั้น (ARTWORK_V2_HOVER_ZOOM · ไม่มีค่า = ปิด = แบบเดิม)
  const HOVER_ZOOM = ($("v2Root") && $("v2Root").dataset.hoverZoom) === "1";
  // ตำแหน่งประมาณบนฝั่งที่ไม่พบข้อความ (ARTWORK_V2_EST_BOX) — แสดงผลล้วน
  const EST_BOX = ($("v2Root") && $("v2Root").dataset.estBox) === "1";

  function hideResult() {
    S.result = null;
    $("v2ResCard").classList.add("v2-hidden");
    $("v2LogCard").classList.add("v2-hidden");
    $("v2Verdict").innerHTML = "";
    $("v2Warn").innerHTML = "";
    if (HOVER_ZOOM) { RZ.pin = null; zoomReset(); }
    $("v2PairsRes").innerHTML = "";
    $("v2PairsRes").classList.remove("v2-rv-todo-only");
    if ($("v2Review")) $("v2Review").innerHTML = "";
    S.review = null;
    $("v2Log").value = "";
    $("v2RunMsg").textContent = "";
  }

  async function showLastRun(jobId, run) {
    try { showResult(await api("/api/artwork_v2/jobs/" + jobId + "/runs/" + run)); }
    catch (e) { $("v2RunMsg").innerHTML = '<span class="v2-bad">เปิดผลรอบ ' + esc(run) + " ไม่ได้: " + esc(e.message) + "</span>"; }
  }

  // มุมจอเริ่มต้นของงาน: ที่จำไว้ในเบราว์เซอร์ก่อน · ไม่มี ⇒ มุมของโซนแรกในแต่ละฝั่ง (โซนจากรอบตรวจล่าสุด)
  function rotFrom(rot, pairs) {
    const ok = (v) => [0, 90, 180, 270].indexOf(+v) >= 0;
    const out = { a: 0, b: 0 };
    ["a", "b"].forEach((s) => {
      if (rot && ok(rot[s])) { out[s] = +rot[s]; return; }
      const z = (pairs || []).map((p) => p && p[s]).find((x) => x);
      if (z && ok(z.rotate)) out[s] = +z.rotate;
    });
    return out;
  }

  async function openJob(id, pairs, page, rot) {
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
        rot = rot || saved.rot;
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
    S.rot = rotFrom(rot, pairs);
    $("v2DrawCard").classList.remove("v2-hidden");
    $("v2JobLabel").textContent = "· งาน " + m.id;
    $("v2NameA").textContent = m.files.a.name;
    $("v2NameB").textContent = m.files.b.name;
    fillPages($("v2PageA"), m.files.a.pages || 1, S.page.a);
    fillPages($("v2PageB"), m.files.b.pages || 1, S.page.b);
    loadPreview("a");
    loadPreview("b");
    syncZbar("a");
    syncZbar("b");
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
  // pan (ค่าเริ่มต้น · แบบหน้า Artwork เดิม) = ลากที่ว่างเพื่อเลื่อนภาพ · คลิก/ลากโซนเพื่อเลือก ย้าย ย่อขยาย
  // draw = ลากที่ว่างเพื่อวาดโซนใหม่ · วาดเสร็จกลับเป็น pan (ยกเว้นติ๊ก "วาดต่อเนื่อง")
  let mode = "pan";
  // พื้นที่ยกเว้นในโซน (ARTWORK_V2_ZONE_IGNORE) — เก็บใน z.ignore เป็นสัดส่วนของหน้า (เหมือน bbox)
  const ZONE_IGNORE = ($("v2Root") && $("v2Root").dataset.zoneIgnore) === "1";
  const IGNORE_MAX = parseInt(($("v2Root") && $("v2Root").dataset.ignoreMax) || "20", 10) || 20;
  let sel = null;                 // {pi, side} โซนที่เลือก (กด Delete เพื่อลบ)
  let spaceDown = false;

  const boxOf = (side) => $(side === "a" ? "v2BoxA" : "v2BoxB");
  const stageOf = (side) => $(side === "a" ? "v2StageA" : "v2StageB");
  const rotWrapOf = (side) => $(side === "a" ? "v2RotA" : "v2RotB");
  const rotOf = (side) => (S.rot && S.rot[side]) || 0;
  const imgOf = (side) => $(side === "a" ? "v2ImgA" : "v2ImgB");
  const ovOf = (side) => document.querySelector('.v2-ov[data-side="' + side + '"]');
  const zbarOf = (side) => document.querySelector('.v2-zbar[data-side="' + side + '"]');

  function natSize(side) {
    const im = imgOf(side);
    return [im.naturalWidth || 0, im.naturalHeight || 0];
  }

  // ── หมุนจอ (ต่อไฟล์ · แบบหน้า Artwork เดิม) ──
  // ภาพ+โซนหมุนด้วย CSS transform ทั้งก้อน ⇒ สูตรพิกัดโซน (สัดส่วนของหน้าที่ไม่หมุน) ไม่เปลี่ยน
  // แปลงเฉพาะพิกัดเมาส์ (normPoint) และทิศลูกศร (unrotDelta) กลับเป็นของหน้าที่ไม่หมุน
  function applyRot(side) {
    const wrap = rotWrapOf(side), st = stageOf(side), r = rotOf(side);
    if (!wrap || !st) return;
    const W = st.offsetWidth, H = st.offsetHeight;
    ["r90", "r180", "r270"].forEach((c) => wrap.classList.toggle(c, c === "r" + r));
    if (!r || !W || !H) {
      wrap.classList.remove("on");
      wrap.style.width = ""; wrap.style.height = ""; st.style.transform = "";
      rotLabels(side);
      return;
    }
    const swap = r === 90 || r === 270;
    wrap.classList.add("on");
    wrap.style.width = (swap ? H : W) + "px";
    wrap.style.height = (swap ? W : H) + "px";
    st.style.transform = r === 90 ? "translate(" + H + "px,0) rotate(90deg)"
      : r === 180 ? "translate(" + W + "px," + H + "px) rotate(180deg)"
      : "translate(0," + W + "px) rotate(270deg)";
    rotLabels(side);                                   // ป้ายบนจอหมุนเป็น px ⇒ วางใหม่ทุกครั้งที่ขนาดเปลี่ยน
  }
  // ขนาดเนื้อหาในกล่องเลื่อน (หลังหมุน)
  function shownWH(side) {
    const st = stageOf(side), wrap = rotWrapOf(side);
    if (rotOf(side) && wrap) return [wrap.offsetWidth, wrap.offsetHeight];
    return [st.offsetWidth, st.offsetHeight];
  }
  function unrotDelta(dx, dy, r) {
    if (r === 90) return [dy, -dx];
    if (r === 180) return [-dx, -dy];
    if (r === 270) return [-dy, dx];
    return [dx, dy];
  }

  function fitPct(side, whole) {
    let [w, h] = natSize(side);
    const box = boxOf(side);
    if (rotOf(side) === 90 || rotOf(side) === 270) [w, h] = [h, w];     // พอดี "ด้านที่หันเข้าหากล่อง"
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
    const old = shownWH(side);
    const ax = anchor ? anchor[0] : box.clientWidth / 2, ay = anchor ? anchor[1] : box.clientHeight / 2;
    const fx = (box.scrollLeft + ax) / (old[0] || w * z.pct / 100), fy = (box.scrollTop + ay) / (old[1] || 1);
    z.pct = pct;
    st.style.width = Math.round(w * pct / 100) + "px";
    applyRot(side);
    const now = shownWH(side);
    box.scrollLeft = fx * now[0] - ax;
    box.scrollTop = fy * now[1] - ay;
    syncZbar(side);
  }

  function syncZbar(side) {
    const bar = zbarOf(side);
    if (!bar) return;
    bar.querySelector('[data-z="range"]').value = Z[side].pct;
    bar.querySelector(".v2-zpct").textContent = Z[side].pct + "%";
    bar.querySelector('[data-z="fitw"]').classList.toggle("on", Z[side].fit === true);
    const rb = bar.querySelector('[data-z="rot"]');
    if (rb) {
      rb.querySelector(".v2-rotdeg").textContent = rotOf(side) + "°";
      rb.classList.toggle("on", rotOf(side) !== 0);
    }
  }

  // ปุ่ม ↻ = หมุนจอของไฟล์นี้ 90° ตามเข็ม · โซนของไฟล์นี้ที่ยังไม่เคยตั้งมุมเอง ⇒ ส่งภาพในแนวที่เห็นบนจอ
  function rotateView(side) {
    S.rot[side] = (rotOf(side) + 90) % 360;
    S.pairs.forEach((p) => {
      const z = p[side];
      if (!z || z.rotManual) return;
      if (S.rot[side]) z.rotate = S.rot[side]; else delete z.rotate;
    });
    setZoom(side, Z[side].fit ? fitPct(side, false) : Z[side].pct, [0, 0], Z[side].fit);
    boxOf(side).scrollLeft = 0;
    boxOf(side).scrollTop = 0;
    renderZones();
    saveSession();
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
      else if (ev.target.closest && ev.target.closest('[data-z="rot"]')) rotateView(side);
    });
    bar.querySelector('[data-z="range"]').addEventListener("dblclick", () => setZoom(side, fitPct(side, false), null, true));
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
      if (ev.target && ev.target.closest && ev.target.closest("[data-rlab],[data-igndel]")) return;   // ชิปมุมบนจอหมุน · ✕ ของพื้นที่ยกเว้น
      const onZone = !!(ev.target && ev.target.closest && ev.target.closest(".v2-zone[data-pi]"));
      const want = ev.button === 1 || (ev.button === 0 && (spaceDown || (mode === "pan" && !onZone)));
      if (!want || !S.job) return;
      if (ev.button === 0 && mode === "pan" && sel) { sel = null; renderZones(); }   // คลิกที่ว่าง = เลิกเลือก
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
    mode = m === "draw" ? "draw" : (m === "ign" && ZONE_IGNORE) ? "ign" : "pan";
    root.classList.toggle("v2-mode-pan", mode === "pan");
    root.classList.toggle("v2-mode-ign", mode === "ign");
    document.querySelectorAll("[data-mode]").forEach((b) => b.classList.toggle("on", b.dataset.mode === m));
  }
  document.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  setMode(mode);

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
    } else if (ev.key === "Escape" && (sel || mode !== "pan")) {
      sel = null;
      if (mode !== "pan") setMode("pan");
      renderZones();
    } else if (sel && /^Arrow/.test(ev.key)) {
      // ลูกศร = ขยับโซนที่เลือก 1 px บนจอ (Shift = 10 px) ตามทิศบนจอแม้จอหมุนอยู่
      const z = S.pairs[sel.pi] && S.pairs[sel.pi][sel.side];
      const st = stageOf(sel.side);
      if (!z || !st.offsetWidth) return;
      ev.preventDefault();
      const k = ev.shiftKey ? 10 : 1;
      const d = { ArrowLeft: [-k, 0], ArrowRight: [k, 0], ArrowUp: [0, -k], ArrowDown: [0, k] }[ev.key];
      if (!d) return;
      const u = unrotDelta(d[0], d[1], rotOf(sel.side));
      z.bbox = editBox(z.bbox, "move", u[0] / st.offsetWidth, u[1] / st.offsetHeight).map(r5);
      renderZones();
      saveSession();
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
        (z.ignore || []).forEach((b, k) => ov.appendChild(ignEl(b, i, k)));
      });
      if (draft && draft.side === side) ov.appendChild(draft.ign ? ignEl(draft.bbox) : zoneEl(draft.bbox, "#0f172a", "", true));
      rotLabels(side);
    });
    const box = $("v2Pairs");
    box.innerHTML = S.pairs.map((p, i) => {
      const nIgn = ((p.a && p.a.ignore) || []).length + ((p.b && p.b.ignore) || []).length;
      const st = (p.a ? "🅰✔" : "🅰—") + " " + (p.b ? "🅱✔" : "🅱—") +
        (nIgn ? ' <span title="พื้นที่ยกเว้นในคู่นี้">⛔' + nIgn + "</span>" : "");
      return '<span class="v2-chip" style="border-color:' + COLORS[i % COLORS.length] + '"><b style="color:' +
        COLORS[i % COLORS.length] + '">คู่ ' + (i + 1) + "</b> " + st +
        ' <button data-del="' + i + '" title="ลบคู่นี้">✕</button></span>';
    }).join("") || '<span class="v2-muted">ยังไม่มีโซน</span>';
    const pend = S.pairs.find((p) => !p.a || !p.b);
    if (!S.busy) $("v2RunMsg").textContent = pend ? "คู่ที่ยังไม่ครบต้องวาดอีกฝั่งก่อนกดตรวจ" : "";
    drawThumb();
  }

  // ตัวอย่าง "ภาพที่จะส่งให้ Vision" ของโซนที่เลือก (ตัดจากภาพตัวอย่างบนจอ + หมุนตามมุมของโซน)
  // แสดงผลล้วน — ภาพที่ส่งจริงเรนเดอร์ใหม่จากไฟล์ต้นฉบับที่ความละเอียดเต็ม
  function drawThumb() {
    const box = $("v2Thumb"), cv = $("v2ThumbCv");
    if (!box || !cv) return;
    const z = sel && S.pairs[sel.pi] && S.pairs[sel.pi][sel.side];
    const im = sel && imgOf(sel.side);
    if (!z || !im || !im.naturalWidth || z.page !== S.page[sel.side]) { box.classList.add("v2-hidden"); return; }
    const [nw, nh] = [im.naturalWidth, im.naturalHeight];
    const sx = z.bbox[0] * nw, sy = z.bbox[1] * nh, sw = Math.max(1, z.bbox[2] * nw), sh = Math.max(1, z.bbox[3] * nh);
    const r = z.rotate || 0, swap = r === 90 || r === 270;
    const k = Math.min(1, 420 / Math.max(swap ? sh : sw, 1), 240 / Math.max(swap ? sw : sh, 1));
    const w = Math.max(1, Math.round(sw * k)), h = Math.max(1, Math.round(sh * k));
    cv.width = swap ? h : w;
    cv.height = swap ? w : h;
    const ctx = cv.getContext("2d");
    ctx.save();
    ctx.translate(cv.width / 2, cv.height / 2);
    ctx.rotate(r * Math.PI / 180);
    ctx.drawImage(im, sx, sy, sw, sh, -w / 2, -h / 2, w, h);
    (z.ignore || []).forEach((b) => {               // พื้นที่ยกเว้น (ส่งให้ Vision เหมือนเดิม แต่ไม่นับจุดต่างในนี้)
      ctx.fillStyle = "rgba(185,28,28,.22)";
      ctx.strokeStyle = "#b91c1c";
      const rx = (b[0] * nw - sx) * k - w / 2, ry = (b[1] * nh - sy) * k - h / 2;
      ctx.fillRect(rx, ry, b[2] * nw * k, b[3] * nh * k);
      ctx.strokeRect(rx, ry, b[2] * nw * k, b[3] * nh * k);
    });
    ctx.restore();
    $("v2ThumbCap").textContent = "🔎 ภาพที่จะส่งให้ Vision — คู่ " + (sel.pi + 1) + " ฝั่ง " +
      (sel.side === "a" ? "🅰" : "🅱") + " · หมุน " + r + "° (ตัวอย่างความละเอียดต่ำ · ตัวหนังสือควรตั้งตรงอ่านได้)";
    box.classList.remove("v2-hidden");
  }

  // จอหมุน: ป้าย "คู่ N" + ชิปมุม วางบนชั้นที่ไม่หมุน (มุมซ้ายบน/ขวาบนของกรอบตามที่เห็นบนจอ)
  function dispRect(bb, r) {
    const [x, y, w, h] = bb;
    if (r === 90) return [1 - (y + h), x, h, w];
    if (r === 180) return [1 - (x + w), 1 - (y + h), w, h];
    if (r === 270) return [y, 1 - (x + w), h, w];
    return [x, y, w, h];
  }
  function rotLabels(side) {
    const wrap = rotWrapOf(side);
    if (!wrap) return;
    wrap.querySelectorAll(":scope > .v2-rlab").forEach((e) => e.remove());
    const r = rotOf(side);
    if (!r) return;
    const W = wrap.offsetWidth, H = wrap.offsetHeight;
    S.pairs.forEach((p, i) => {
      const z = p[side];
      if (!z || z.page !== S.page[side]) return;
      const [u, v, w] = dispRect(z.bbox, r);
      const col = COLORS[i % COLORS.length];
      const inside = v * H < 22;                         // ชิดขอบบน ⇒ วางในกรอบ (ไม่ถูกตัด)
      const lab = document.createElement("span");
      lab.className = "v2-rlab" + (inside ? " in" : "");
      lab.textContent = "คู่ " + (i + 1);
      lab.style.background = col;
      lab.style.left = (u * W) + "px";
      lab.style.top = (v * H) + "px";
      wrap.appendChild(lab);
      const zr = z.rotate || 0;
      const c = document.createElement("b");
      c.className = "v2-rlab v2-zrot" + (inside ? " in" : "") + (zr ? " on" : "");
      c.dataset.rlab = String(i);
      c.textContent = "↻" + zr + "°";
      c.style.color = col;
      c.title = "มุมของภาพโซนนี้ที่ส่งให้ Vision (คลิกวน 0° → 90° → 180° → 270°)";
      c.style.left = ((u + w) * W) + "px";
      c.style.top = (v * H) + "px";
      c.style.marginLeft = inside ? "-46px" : "-44px";
      wrap.appendChild(c);
    });
  }
  // ชิปบนชั้นที่ไม่หมุน — วนมุมของโซน (เหมือนชิปในกรอบ)
  function cycleZoneRot(pi, side) {
    const z = S.pairs[pi] && S.pairs[pi][side];
    if (!z) return;
    const nr = ((z.rotate || 0) + 90) % 360;
    if (nr) z.rotate = nr; else delete z.rotate;
    z.rotManual = true;
    sel = { pi: pi, side: side };
    renderZones();
    saveSession();
  }
  ["a", "b"].forEach((side) => {
    const wrap = rotWrapOf(side);
    if (!wrap) return;
    wrap.addEventListener("pointerdown", (ev) => {
      const c = ev.target && ev.target.closest ? ev.target.closest("[data-rlab]") : null;
      if (!c) return;
      ev.preventDefault();
      ev.stopPropagation();
      cycleZoneRot(+c.dataset.rlab, side);
    });
  });

  function ignEl(bb, pi, k) {
    const d = document.createElement("div");
    d.className = "v2-ign" + (pi == null ? " draft" : "");
    d.style.left = bb[0] * 100 + "%";
    d.style.top = bb[1] * 100 + "%";
    d.style.width = bb[2] * 100 + "%";
    d.style.height = bb[3] * 100 + "%";
    if (pi != null) {
      d.title = "พื้นที่ยกเว้นของคู่ " + (pi + 1) + " — จุดต่างในนี้ไม่นับในผลตัดสิน";
      const x = document.createElement("b");
      x.textContent = "✕";
      x.dataset.igndel = pi + ":" + k;
      x.title = "ลบพื้นที่ยกเว้นนี้";
      d.appendChild(x);
    }
    return d;
  }
  // กรอบยกเว้นต้องอยู่ "ในโซน" — ใช้โซนของฝั่งนี้ที่ครอบจุดกลางของกรอบ แล้วตัดให้อยู่ในโซน
  function addIgnore(side, bb) {
    const cx = bb[0] + bb[2] / 2, cy = bb[1] + bb[3] / 2;
    const pi = S.pairs.findIndex((p) => {
      const z = p[side];
      return z && z.page === S.page[side] && cx >= z.bbox[0] && cx <= z.bbox[0] + z.bbox[2] &&
        cy >= z.bbox[1] && cy <= z.bbox[1] + z.bbox[3];
    });
    if (pi < 0) return "วาดพื้นที่ยกเว้นภายในโซน (จุดกลางของกรอบต้องอยู่ในโซน)";
    const z = S.pairs[pi][side];
    if ((z.ignore || []).length >= IGNORE_MAX) return "พื้นที่ยกเว้นได้สูงสุด " + IGNORE_MAX + " กรอบต่อโซน";
    const x0 = Math.max(bb[0], z.bbox[0]), y0 = Math.max(bb[1], z.bbox[1]);
    const x1 = Math.min(bb[0] + bb[2], z.bbox[0] + z.bbox[2]), y1 = Math.min(bb[1] + bb[3], z.bbox[1] + z.bbox[3]);
    z.ignore = (z.ignore || []).concat([[x0, y0, x1 - x0, y1 - y0].map(r5)]);
    sel = { pi: pi, side: side };
    return "";
  }
  function delIgnore(side, tag) {
    const [pi, k] = String(tag).split(":").map(Number);
    const z = S.pairs[pi] && S.pairs[pi][side];
    if (!z || !z.ignore || !z.ignore[k]) return;
    z.ignore.splice(k, 1);
    if (!z.ignore.length) delete z.ignore;
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
    if (!draft && pi != null) {
      const z = S.pairs[pi] && S.pairs[pi][side];
      const r = (z && z.rotate) || 0;
      const c = document.createElement("b");
      c.className = "v2-zrot" + (r ? " on" : "");
      c.dataset.rot = "1";
      c.textContent = "↻" + r + "°";
      c.style.color = color;
      c.title = "มุมของภาพโซนนี้ที่ส่งให้ Vision (คลิกวน 0° → 90° → 180° → 270°) · ดูตัวอย่างใต้ภาพ";
      d.appendChild(c);
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

  // พิกัดเมาส์ → สัดส่วนบนหน้าที่ไม่หมุน (กรอบของ ov บนจอ = กรอบหลังหมุน)
  function normPoint(ov, ev) {
    const r = ov.getBoundingClientRect();
    const u = (ev.clientX - r.left) / r.width, v = (ev.clientY - r.top) / r.height;
    const rot = rotOf(ov.dataset.side);
    const p = rot === 90 ? [v, 1 - u] : rot === 180 ? [1 - u, 1 - v] : rot === 270 ? [1 - v, u] : [u, v];
    return [Math.min(1, Math.max(0, p[0])), Math.min(1, Math.max(0, p[1]))];
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
      if (ev.button !== 0 || !S.job || spaceDown) return;
      const t = ev.target;
      if (t && t.dataset && t.dataset.igndel) {       // ✕ บนพื้นที่ยกเว้น = ลบ
        ev.preventDefault();
        ev.stopPropagation();
        delIgnore(side, t.dataset.igndel);
        renderZones();
        saveSession();
        return;
      }
      if (mode === "ign") {                           // วาดพื้นที่ยกเว้น (ไม่แตะโซน)
        act = { kind: "ign", start: normPoint(ov, ev) };
        ov.setPointerCapture(ev.pointerId);
        ev.preventDefault();
        return;
      }
      if (t && t.dataset && t.dataset.rot) {          // ชิป ↻ = วนมุมของโซนนี้
        const zEl0 = t.closest(".v2-zone[data-pi]");
        if (zEl0) cycleZoneRot(+zEl0.dataset.pi, side);
        ev.preventDefault();
        ev.stopPropagation();
        return;
      }
      const start = normPoint(ov, ev);
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
        if (mode !== "draw") return;                  // โหมดเลือก: ที่ว่างถูกใช้เลื่อนภาพแล้ว
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
      if (act.kind === "ign") { renderZones({ side: side, bbox: rect(act.start, p), ign: true }); return; }
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
      if (a.kind === "ign") {
        if (bb[2] < 0.003 || bb[3] < 0.003) { setMode("pan"); renderZones(); return; }   // คลิกเปล่า = เลิกวาด
        const err = addIgnore(side, bb);
        if (!err && !($("v2DrawCont") && $("v2DrawCont").checked)) setMode("pan");
        renderZones();
        if (err) $("v2RunMsg").innerHTML = '<span class="v2-warn">' + esc(err) + "</span>";   // หลัง renderZones (ซึ่งล้างข้อความ)
        else saveSession();
        return;
      }
      if (bb[2] < 0.01 || bb[3] < 0.01) { setMode("pan"); renderZones(); return; }   // คลิกเปล่า = เลิกวาด
      const nz = { page: S.page[side], bbox: bb.map(r5) };
      if (rotOf(side)) nz.rotate = rotOf(side);       // โซนที่วาดขณะหมุนจอ ⇒ ส่งภาพในแนวที่เห็น
      if (!($("v2DrawCont") && $("v2DrawCont").checked)) setMode("pan");
      addZone(side, nz);
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
          ai_mode: $("v2Ai") ? $("v2Ai").value : undefined,
          color_mode: $("v2Color") ? $("v2Color").value : undefined }),
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
    CURVED: "ข้อความโค้ง/เอียง", MOVED: "ข้อความย้ายที่",
    PLACEHOLDER: "ช่องว่างรอพิมพ์ ↔ ข้อมูลจริง",
  };

  // การ์ด "ข้อความโค้ง/เอียง" รวมหลายจุด — แสดงคำของทุกสมาชิก (ไม่มีจุดไหนถูกลบ)
  function cellText(f, side) {
    if (f.class !== "CURVED") return marked(f[side].text, f[side].span);
    return (f.members || []).map((m) =>
      '<span class="v2-sev ' + m.severity + '" style="font-size:11px">' + (m.severity === "red" ? "ต่าง" : "?") +
      "</span> " + marked(m[side].text, m[side].span)).join("<br>");
  }

  const SEV_TH = { red: "ต่าง", yellow: "ไม่มั่นใจ", debris: "เศษ", dismissed: "AI: สัญญาณรบกวน",
    moved: "ย้ายที่", pixel_same: "ภาพเหมือน", excluded: "ยกเว้น", lowmark: "เครื่องหมายไม่ชัด" };
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
    if (!ai.verdict) return '<span class="v2-ai-note v2-muted">🤖 ' +
      (ai.not_sent ? "AI ไม่ได้ตรวจจุดนี้ (" + esc(ai.not_sent) + ")" : "AI ไม่ได้ตอบจุดนี้") + "</span>";
    // โหมด image: สิ่งที่ AI เห็นบนภาพแต่ละฝั่ง (คำของ AI — ไม่ใช่การอ่านของ Vision)
    const seen = ai.image && (ai.a_seen || ai.b_seen)
      ? '<span class="v2-ai-note">🖼️ AI เห็นในภาพ — A: "' + esc(ai.a_seen || "—") + '" · B: "' + esc(ai.b_seen || "—") + '"</span>' : "";
    const vth = ai.image && ai.verdict === "noise" ? "ภาพเหมือนกัน (Vision อ่านผิด)" : (AI_TH[ai.verdict] || ai.verdict);
    return '<span class="v2-ai-note">🤖 <b>' + esc(vth) + "</b>" +
      (ai.reason ? " — " + esc(ai.reason) : "") + "</span>" + seen +
      (ai.suggestion ? '<span class="v2-ai-note">💡 ' + esc(ai.suggestion) + "</span>" : "");
  }

  // หลักฐานภาพ (ARTWORK_V2_PIXEL_VERIFY): ภาพ A | B (ทาบแล้ว) | จุดที่ต่าง — เรนเดอร์จากไฟล์ต้นฉบับในเครื่อง
  function pixelEvidence(f) {
    const px = f.pixel;
    if (!px || !px.evidence || !S.result) return "";
    const url = "/api/artwork_v2/jobs/" + encodeURIComponent(S.result.job) + "/runs/" +
      encodeURIComponent(S.result.run) + "/img/" + encodeURIComponent(px.evidence);
    return '<a class="v2-pv" href="' + url + '" target="_blank" rel="noopener" title="ภาพหลักฐาน: 🅰 | 🅱 (ทาบแล้ว) | จุดที่ต่าง (กรอบแดง)">' +
      '<img loading="lazy" alt="ภาพหลักฐานจุด ' + esc(f.id) + '" src="' + url + '"></a>';
  }

  // ── ตารางข้างภาพ (ARTWORK_V2_SIDE_TABLE · แสดงผลล้วน) ──
  //  · ตารางอยู่ขวามือของภาพ 🅰/🅱 ⇒ ภาพกับตารางอยู่บนจอพร้อมกัน · คอลัมน์กระชับ (ชนิดอยู่ใต้ระดับ)
  //  · หมายเหตุ (โน้ตระบบ + ภาพหลักฐาน + คำตอบ AI) พับไว้ในแถวย่อยใต้แถว ⇒ กด ⓘ เปิดทีละแถว / "หมายเหตุทั้งหมด"
  //  · แถวย่อยไม่มี data-f ⇒ ไม่ถูกนับเป็นจุดต่าง ไม่ซูม ไม่ถูกเลือก
  const SIDE_TABLE = root.dataset.sideTable === "1";
  function sideRow(id, sevKey, sevTxt, clsHtml, aHtml, bHtml, confHtml, notesHtml, extra) {
    const has = !!notesHtml;
    return '<tr class="click v2-s-' + esc(sevKey) + (extra.cls || "") + '" data-f="' + esc(id) + '"' + (extra.attr || "") +
      "><td>" + esc(extra.label || id) + '</td><td><span class="v2-sev ' + esc(sevKey) + '">' + esc(sevTxt) +
      '</span><div class="v2-cls">' + clsHtml + "</div></td><td>" + aHtml + "</td><td>" + bHtml + "</td><td>" + confHtml +
      "</td><td>" + (has ? '<button type="button" class="v2-nbtn" data-nt="' + esc(id) +
        '" aria-expanded="false" title="เปิด/ปิดหมายเหตุของแถวนี้">ⓘ</button>' : "") + "</td></tr>" +
      (has ? '<tr class="v2-note" data-note="' + esc(id) + '" hidden><td></td><td colspan="5">' + notesHtml + "</td></tr>" : "");
  }
  function confShort(f) {
    const c = f.confidence;
    return '<span class="v2-conf' + (c != null && c < 0.8 ? " lo" : "") + '" title="ความมั่นใจของ Vision ตรงตัวอักษรที่ต่าง · A ' +
      pct(f.a.conf) + " · B " + pct(f.b.conf) + '">' + pct(c) + "</span>";
  }

  function findingRow(f) {
    const sev = SEV_TH[f.severity] || f.severity;
    const notes = (f.notes || []).filter((n) => !/^AI: /.test(n)).map(esc).join("<br>") + pixelEvidence(f);
    if (SIDE_TABLE) {
      return sideRow(f.id, f.severity, sev, esc(CLASS_TH[f.class] || f.class) +
        (f.source === "ai" ? '<span class="v2-ai-tag">AI</span>' : "") + rvHtml([f.id]),
        cellText(f, "a"), cellText(f, "b"), confShort(f), notes + aiNote(f), {});
    }
    return '<tr class="click" data-f="' + f.id + '"><td>' + f.id + '</td><td><span class="v2-sev ' + f.severity + '">' +
      sev + "</span>" + rvHtml([f.id]) + "</td><td>" + esc(CLASS_TH[f.class] || f.class) +
      (f.source === "ai" ? '<span class="v2-ai-tag">AI</span>' : "") +
      "</td><td>" + cellText(f, "a") + "</td><td>" + cellText(f, "b") + "</td><td>" + confCell(f) +
      "</td><td>" + notes + aiNote(f) + "</td></tr>";
  }
  // ── รวมจุดต่างในคู่บรรทัดเดียวกันเป็นแถวเดียว (ARTWORK_V2_LINE_GROUP · แสดงผลล้วน) ──────────
  //  · ไม่แตะผลตรวจ/ผลตัดสิน/Log — ทุกจุดยังอยู่ครบพร้อมเลขจุด กรอบบนภาพ ระดับ หมายเหตุ และคำตอบ AI ของตัวเอง
  //  · จับกลุ่มเฉพาะจุดที่ชี้ "บรรทัดเดียวกันทั้งสองฝั่ง" (เลขบรรทัด + ข้อความ A/B ตรงกัน) และมาจากแหล่งเดียวกัน
  //    (อัลกอริทึม/AI) · จุดหาย/เกินฝั่งเดียว การ์ดโค้ง และจุดที่ไม่มีเลข ไม่ถูกจับกลุ่ม
  //  · ระดับของแถว = สมาชิกที่หนักที่สุด · แถวอยู่ตำแหน่งของสมาชิกตัวแรก
  const LINE_GROUP = root.dataset.lineGroup === "1";
  const SEV_RANK = { red: 4, yellow: 3, debris: 2, moved: 2, lowmark: 2, dismissed: 1, pixel_same: 1, excluded: 1 };
  let GRP = {};                 // id ของแถวกลุ่ม ("g<id แรก>") → [id สมาชิก] — ใช้กับการซูม/เลือกแถว
  function lineGroups(list) {
    const out = [], at = {};
    (list || []).forEach((f) => {
      const a = f.a || {}, b = f.b || {};
      const k = (f.id != null && !f.members && a.line != null && b.line != null)
        ? [f.source === "ai" ? "ai" : "algo", a.line, b.line, a.text || "", b.text || ""].join("\u0001") : null;
      if (k != null && at[k] != null) { out[at[k]].push(f); return; }
      if (k != null) at[k] = out.length;
      out.push([f]);
    });
    return out.map((g) => g.length === 1 ? g[0] : {
      group: true, id: "g" + g[0].id, members: g,
      severity: g.reduce((s, m) => ((SEV_RANK[m.severity] || 0) > (SEV_RANK[s] || 0) ? m.severity : s), g[0].severity),
    });
  }

  // ข้อความทั้งบรรทัด + ไฮไลต์ทุกช่วงที่ต่าง (ช่วงว่าง = จุดแทรก ▏)
  function markedMulti(text, spans) {
    if (!text) return '<span class="v2-muted">—</span>';
    const ss = (spans || []).filter((x) => x && x.length === 2)
      .map((x) => [Math.max(0, Math.min(text.length, x[0])), Math.max(0, Math.min(text.length, Math.max(x[0], x[1])))])
      .sort((x, y) => x[0] - y[0] || x[1] - y[1]);
    let out = "", pos = 0;
    ss.forEach((x) => {
      if (x[0] < pos && x[1] <= pos) return;          // อยู่ในช่วงที่ไฮไลต์ไปแล้ว
      const st = Math.max(x[0], pos);
      out += esc(text.slice(pos, st));
      out += x[1] > st ? '<mark class="v2-d">' + esc(text.slice(st, x[1])) + "</mark>" : '<mark class="v2-d">▏</mark>';
      pos = Math.max(pos, x[1]);
    });
    return out + esc(text.slice(pos));
  }

  function groupRow(g) {
    const ms = g.members, f0 = ms[0];
    const ids = ms.map((m) => String(m.id));
    const cls = [];
    ms.forEach((m) => { const t = CLASS_TH[m.class] || m.class; if (cls.indexOf(t) < 0) cls.push(t); });
    const lo = (vals) => { const v = vals.filter((x) => x != null); return v.length ? Math.min.apply(null, v) : null; };
    const conf = confCell({ confidence: lo(ms.map((m) => m.confidence)),
                            a: { conf: lo(ms.map((m) => m.a.conf)) }, b: { conf: lo(ms.map((m) => m.b.conf)) } });
    const frag = (t) => (t ? "<code>" + esc(t) + "</code>" : '<span class="v2-muted">(ไม่มี)</span>');
    const per = ms.map((m) => {
      const notes = (m.notes || []).filter((n) => !/^AI: /.test(n)).map(esc).join("<br>");
      return '<div class="v2-gm"><b>#' + esc(m.id) + '</b> <span class="v2-sev ' + m.severity + '" style="font-size:11px">' +
        esc(SEV_TH[m.severity] || m.severity) + "</span> " + esc(CLASS_TH[m.class] || m.class) + ": " +
        frag(m.a.frag) + " → " + frag(m.b.frag) + ' <span class="v2-muted">· ' + pct(m.confidence) + "</span>" +
        (notes ? "<br>" + notes : "") + aiNote(m) + "</div>";
    }).join("");
    if (SIDE_TABLE) {
      return sideRow(g.id, g.severity, SEV_TH[g.severity] || g.severity,
        ms.length + " จุดในบรรทัดเดียวกัน" + (f0.source === "ai" ? '<span class="v2-ai-tag">AI</span>' : "") +
        "<br>" + esc(cls.join(" · ")) + rvHtml(ids),
        markedMulti(f0.a.text, ms.map((m) => m.a.span)), markedMulti(f0.b.text, ms.map((m) => m.b.span)),
        confShort({ confidence: lo(ms.map((m) => m.confidence)), a: { conf: lo(ms.map((m) => m.a.conf)) },
                    b: { conf: lo(ms.map((m) => m.b.conf)) } }),
        per, { cls: " v2-grp", attr: ' data-members="' + ids.join(",") + '"', label: ids.join("·") });
    }
    return '<tr class="click v2-grp" data-f="' + g.id + '" data-members="' + ids.join(",") + '"><td>' + ids.join("·") +
      '</td><td><span class="v2-sev ' + g.severity + '">' + (SEV_TH[g.severity] || g.severity) + "</span>" + rvHtml(ids) +
      "</td><td>" + ms.length + " จุดในบรรทัดเดียวกัน" + (f0.source === "ai" ? '<span class="v2-ai-tag">AI</span>' : "") +
      '<br><span class="v2-muted">' + esc(cls.join(" · ")) + "</span></td><td>" +
      markedMulti(f0.a.text, ms.map((m) => m.a.span)) + "</td><td>" + markedMulti(f0.b.text, ms.map((m) => m.b.span)) +
      "</td><td>" + conf + "</td><td>" + per + "</td></tr>";
  }

  // เรียงแดง (ต่าง) ก่อน แล้วเหลือง (ไม่มั่นใจ) — ลำดับเดิมภายในระดับเดียวกัน (ARTWORK_V2_SORT_SEVERITY · แสดงผลล้วน)
  const SORT_SEVERITY = root.dataset.sortSeverity === "1";
  function bySeverity(list) {
    const xs = (list || []).slice();
    if (!SORT_SEVERITY) return xs;
    return xs.map((f, i) => [f, i])
      .sort((p, q) => ((SEV_RANK[q[0].severity] || 0) - (SEV_RANK[p[0].severity] || 0)) || (p[1] - q[1]))
      .map((p) => p[0]);
  }

  function rowsHtml(list, main) {
    RV_ROW = !!(main && REVIEW);           // ปุ่มรีวิวเฉพาะตารางหลัก (รายการพับไม่นับในผลตัดสิน — ไม่ต้องรีวิว)
    try {
      if (!LINE_GROUP) return bySeverity(list).map(findingRow).join("");
      return bySeverity(lineGroups(list)).map((x) => {
        if (!x.group) return findingRow(x);
        GRP[x.id] = x.members.map((m) => String(m.id));
        return groupRow(x);
      }).join("");
    } finally { RV_ROW = false; }
  }
  // id ของแถว → ชุด id ของจุดบนภาพ (แถวเดี่ยว = ตัวเอง)
  function idsOf(id) { return new Set(id == null ? [] : (GRP[id] || [String(id)])); }
  // กรอบบนภาพของแถว (แถวกลุ่ม = กรอบของสมาชิกตัวแรกที่มีกรอบ)
  function hitOf(id) {
    for (const x of idsOf(id)) {
      const el = document.querySelector('#v2PairsRes rect.f[data-f="' + x + '"]');
      if (el) return el;
    }
    return null;
  }
  // แถวกลุ่ม ⇒ เป้าซูม = กรอบที่ครอบคำของทุกสมาชิก (ฝั่งที่ไม่มีกรอบเลย ⇒ ไม่มีเป้า เหมือนแถวเดี่ยว)
  function groupTarget(members) {
    const side = (s) => {
      let bx = null;
      members.forEach((m) => {
        const b = m[s] && (m[s].word_box || m[s].box);
        if (!b) return;
        bx = bx ? [Math.min(bx[0], b[0]), Math.min(bx[1], b[1]), Math.max(bx[2], b[2]), Math.max(bx[3], b[3])] : b.slice();
      });
      return { text: members[0][s] && members[0][s].text, word_box: bx, box: bx };
    };
    return { a: side("a"), b: side("b") };
  }

  // ── รีวิวจุดต่างโดยคน (ARTWORK_V2_REVIEW · ไม่แตะผลตรวจ/ผลตัดสิน) ─────────────────────
  //  · "✓ ยืนยัน" = เป็นข้อผิดพลาดจริงของงาน · "⚑ รายงานปัญหา" = ระบบแจ้งผิด (ไม่ใช่ข้อผิดพลาด) + เหตุผล
  //  · กดปุ่มเดิมซ้ำ = ยกเลิก (กลับเป็นยังไม่รีวิว) · แถวกลุ่ม = ทุกจุดในแถวพร้อมกัน
  //  · เก็บที่เซิร์ฟเวอร์ (run_<n>/review.json) ⇒ เห็นตรงกันทุกเครื่อง · ส่วน [REVIEW] ต่อท้าย Log
  const REVIEW = root.dataset.review === "1";
  const RV_TH = { real: "✓ ผิดจริง", false: "⚑ ระบบแจ้งผิด", mixed: "รีวิวบางจุด" };
  let RV_ROW = false;
  function rvHtml(ids) {
    if (!RV_ROW) return "";
    return '<div class="v2-rv" data-rv="' + esc(ids.join(",")) + '">' +
      '<button type="button" class="v2-rvb real" data-st="real" title="ยืนยัน — จุดนี้เป็นข้อผิดพลาดจริงของงาน (กดซ้ำ = ยกเลิก)">✓<span class="v2-rvt"> ยืนยัน</span></button>' +
      '<button type="button" class="v2-rvb false" data-st="false" title="รายงานปัญหา — ระบบแจ้งผิด ไม่ใช่ข้อผิดพลาดของงาน (กดซ้ำ = ยกเลิก)">⚑<span class="v2-rvt"> รายงานปัญหา</span></button>' +
      '<span class="v2-rvs"></span></div>';
  }
  function rvState(ids) {
    const items = (S.review && S.review.items) || {};
    const st = ids.map((x) => (items[x] || {}).status || null);
    if (st.every((x) => x === null)) return null;
    if (st.every((x) => x === st[0])) return st[0];
    return "mixed";
  }
  function rvTitle(ids) {
    const items = (S.review && S.review.items) || {};
    return ids.filter((x) => items[x]).map((x) => {
      const it = items[x];
      return (ids.length > 1 ? "#" + x + " " : "") + (RV_TH[it.status] || it.status) +
        (it.note ? " — " + it.note : "") + (it.by ? " · " + it.by : "") + (it.at ? " · " + it.at : "");
    }).join("\n");
  }
  function applyReview() {
    if (!REVIEW) return;
    document.querySelectorAll("#v2PairsRes .v2-rv").forEach((el) => {
      const ids = (el.dataset.rv || "").split(",").filter((x) => x);
      const st = rvState(ids);
      el.querySelectorAll(".v2-rvb").forEach((b) => b.classList.toggle("on", st === b.dataset.st));
      const lab = el.querySelector(".v2-rvs");
      lab.textContent = st ? RV_TH[st] : "";
      lab.title = rvTitle(ids);
      const tr = el.closest("tr");
      if (!tr) return;
      tr.classList.toggle("v2-rv-real", st === "real");
      tr.classList.toggle("v2-rv-false", st === "false");
      const done = st === "real" || st === "false";
      tr.classList.toggle("v2-rv-done", done);
      const nt = tr.nextElementSibling;
      if (nt && nt.classList.contains("v2-note")) nt.classList.toggle("v2-rv-done", done);
    });
    renderReviewBar();
  }
  function renderReviewBar(msg) {
    const box = $("v2Review");
    if (!REVIEW || !box) return;
    const sm = S.review && S.review.summary;
    if (!S.result || !sm || !sm.total) { box.innerHTML = ""; return; }
    const pctW = (n) => (100 * n / sm.total).toFixed(1) + "%";
    const todoOnly = $("v2PairsRes").classList.contains("v2-rv-todo-only");
    const reds = new Set((sm.red_todo || []).map(String));
    box.innerHTML = '<div class="v2-revbar' + (sm.complete ? " done" : "") + '"><div class="v2-revtop">' +
      "<b>" + (sm.complete ? "✅ รีวิวครบทุกจุดแล้ว" : "📝 รีวิวแล้ว " + sm.reviewed + "/" + sm.total + " จุด") + "</b>" +
      '<span class="v2-revprog" title="แดง = ผิดจริง · เทา = ระบบแจ้งผิด"><i class="r" style="width:' + pctW(sm.real.length) +
      '"></i><i class="f" style="width:' + pctW(sm.false.length) + '"></i></span>' +
      '<span>✓ ผิดจริง <b>' + sm.real.length + "</b></span>" +
      '<span>⚑ ระบบแจ้งผิด <b>' + sm.false.length + "</b></span>" +
      '<span>ยังไม่รีวิว <b>' + sm.todo.length + "</b>" + (reds.size ? ' (แดง ' + reds.size + ")" : "") + "</span>" +
      (sm.todo.length && sm.reviewed ? '<label class="v2-muted"><input type="checkbox" id="v2RevTodoOnly"' +
        (todoOnly ? " checked" : "") + "> แสดงเฉพาะที่ยังไม่รีวิว</label>" : "") +
      (msg ? '<span class="v2-revmsg">' + esc(msg) + "</span>" : "") + "</div>" +
      (sm.todo.length ? '<div class="v2-revtodo"><span class="v2-muted">ยังไม่รีวิว:</span>' +
        sm.todo.map((x) => '<button type="button" data-go="' + esc(x) + '"' + (reds.has(String(x)) ? ' class="red"' : "") +
          ' title="ไปที่จุด #' + esc(x) + '">#' + esc(x) + "</button>").join("") + "</div>" : "") +
      (sm.real.length ? '<div class="v2-revtodo"><span class="v2-muted">ผิดจริง:</span>' +
        sm.real.map((x) => '<button type="button" data-go="' + esc(x) + '" class="red">#' + esc(x) + "</button>").join("") + "</div>" : "") +
      "</div>";
    if (!sm.todo.length || !sm.reviewed) $("v2PairsRes").classList.remove("v2-rv-todo-only");
  }
  // แถวในตารางหลักที่มีจุดนี้ (แถวเดี่ยว หรือแถวกลุ่มที่มีจุดนี้เป็นสมาชิก)
  function rowOfFinding(id) {
    let tr = document.querySelector('#v2PairsRes .v2-main tr[data-f="' + CSS.escape(String(id)) + '"]');
    if (tr) return tr;
    document.querySelectorAll("#v2PairsRes .v2-main tr[data-members]").forEach((x) => {
      if (!tr && x.dataset.members.split(",").indexOf(String(id)) >= 0) tr = x;
    });
    return tr;
  }
  async function loadReview(r) {
    if (!REVIEW || !r) return;
    S.review = { items: {}, summary: null };
    try {
      const d = await api("/api/artwork_v2/jobs/" + encodeURIComponent(r.job) + "/runs/" + encodeURIComponent(r.run) + "/review");
      if (S.result !== r) return;                    // เปิดผลอื่นไปแล้วระหว่างรอ
      S.review = d;
      $("v2Log").value = (r.log_text || "") + (d.log_text || "");
      applyReview();
    } catch (e) {
      if (S.result === r) renderReviewBar("โหลดผลรีวิวไม่ได้: " + e.message);
    }
  }
  async function sendReview(el, st) {
    const r = S.result;
    if (!r || !el) return;
    const ids = (el.dataset.rv || "").split(",").filter((x) => x);
    const cur = rvState(ids);
    let status = cur === st ? null : st;             // กดปุ่มเดิมซ้ำ = ยกเลิก
    let note = null;
    if (status === "false") {
      const items = (S.review && S.review.items) || {};
      const old = ids.map((x) => (items[x] || {}).note).find((x) => x) || "";
      note = window.prompt("รายงานปัญหา: ระบบแจ้งจุด #" + ids.join(", #") +
        " ผิด เพราะอะไร? (ไม่บังคับ — เว้นว่างได้)", old);
      if (note === null) return;                     // ยกเลิก = ไม่บันทึก
    }
    const btns = el.querySelectorAll(".v2-rvb");
    btns.forEach((b) => { b.disabled = true; });
    try {
      const d = await api("/api/artwork_v2/jobs/" + encodeURIComponent(r.job) + "/runs/" + encodeURIComponent(r.run) + "/review", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: ids, status: status, note: note }),
      });
      if (S.result !== r) return;
      S.review = d;
      $("v2Log").value = (r.log_text || "") + (d.log_text || "");
      applyReview();
    } catch (e) {
      renderReviewBar("บันทึกไม่สำเร็จ: " + e.message);
    } finally {
      btns.forEach((b) => { b.disabled = false; });
    }
  }
  if (REVIEW && $("v2Review")) {
    $("v2Review").addEventListener("click", (ev) => {
      const go = ev.target.closest ? ev.target.closest("[data-go]") : null;
      if (!go) return;
      const tr = rowOfFinding(go.dataset.go);
      if (!tr) return;
      tr.scrollIntoView({ behavior: "smooth", block: "nearest" });
      if (!tr.classList.contains("sel") && !(HOVER_ZOOM && RZ.pin === tr.dataset.f)) tr.click();
    });
    $("v2Review").addEventListener("change", (ev) => {
      if (ev.target.id !== "v2RevTodoOnly") return;
      $("v2PairsRes").classList.toggle("v2-rv-todo-only", ev.target.checked);
      fitRows();
    });
  }

  const TBL_HEAD = SIDE_TABLE
    ? '<thead><tr><th>#</th><th>ระดับ</th><th>🅰</th><th>🅱</th><th title="ความมั่นใจของ Vision ตรงตัวอักษรที่ต่าง">%</th>' +
      '<th><button type="button" class="v2-nbtn v2-nall" title="เปิด/ปิดหมายเหตุทุกแถวในตารางนี้" aria-expanded="false">ⓘ</button></th></tr></thead>'
    : '<thead><tr><th>#</th><th>ระดับ</th><th>ชนิด</th><th>🅰</th><th>🅱</th><th>ความมั่นใจ (Vision)</th><th>หมายเหตุ</th></tr></thead>';

  const AI_MODE_TH = { assist: "อัลกอริทึมตัดสิน + AI เสริม", judge: "AI ตัดสินหลัก", raw: "AI ตัดสินจากข้อมูลดิบ", image: "AI ดูภาพตัดสิน", off: "ปิด AI" };
  function folded(title, list) {
    if (!list || !list.length) return "";
    return '<details class="v2-debris" style="margin-top:8px"><summary>' + esc(title) + " (" + list.length +
      ')</summary><div class="v2-tbl-wrap"><table class="v2-tbl">' + TBL_HEAD + "<tbody>" +
      rowsHtml(list) + "</tbody></table></div></details>";
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
      const ok = (ai.items_valid || 0) + (ai.items_equivalent || 0) + (ai.reviews_valid || 0);
      stat += " · AI อ้างอิงข้อมูล Vision ถูกต้อง " + ok + "/" + tot + " ข้อ (" + pct(ai.ref_accuracy) + ")";
      if (ai.recovered) stat += " · แก้รหัสคำที่ AI นับคลาดจากข้อความที่ยกมา " + ai.recovered + " ข้อ";
      if (ai.items_equivalent) stat += " · ข้อที่สองฝั่งเท่ากันตามกติกาเทียบ (นับเป็นสัญญาณรบกวน) " + ai.items_equivalent + " ข้อ";
    }
    if (ai.mode === "raw") stat += " · AI เทียบจากบรรทัดดิบของ Vision (ไม่ผ่านอัลกอริทึม) · ผลอัลกอริทึม " + (ai.algo_compare || 0) + " จุดอยู่ในรายการพับไว้เทียบ";
    if (ai.mode === "image" && ai.image_verdicts) {
      const iv = ai.image_verdicts;
      stat += " · AI ดูภาพที่ส่ง Vision แล้วตัดสิน " + (ai.reviewed || 0) + "/" + (ai.reviewable || 0) +
        " จุด: ต่างจริง " + (iv.real || 0) + " · ภาพเหมือน (พับ) " + (iv.noise || 0) + " · ไม่แน่ใจ " + (iv.uncertain || 0);
      if (iv.unanswered) stat += " · ไม่ได้ตอบ " + iv.unanswered + " (คงระดับเดิม)";
      if (iv.guarded) stat += " · ภาพเหมือนแต่ Vision อ่านชัด คงไว้ " + iv.guarded;
      if (iv.curved_yellow) stat += " · ข้อความโค้งที่ AI บอกว่าต่าง คงไว้เป็นเหลือง " + iv.curved_yellow;
      if (iv.not_sent) stat += " · ไม่ได้ส่งให้ AI " + iv.not_sent + " จุด (คงระดับเดิม)";
      if (ai.crops) stat += " · ส่งภาพครอปรอบจุด " + ai.crops + " รูป";
    }
    if (ai.algo_red_kept) stat += " · จุดแดงของอัลกอริทึมที่ AI ไม่ได้ระบุ คงไว้เป็นเหลือง " + ai.algo_red_kept + " จุด";
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

  // พื้นที่ยกเว้น (สัดส่วนของหน้า) → กรอบบนภาพที่ส่ง (px · แนวที่หมุนแล้ว) — กลับด้านของ pipeline._page_box
  function ignSentBox(sd, b) {
    const W = sd.sent_px[0], H = sd.sent_px[1], r = sd.rotate || 0;
    const W0 = (r === 90 || r === 270) ? H : W, H0 = (r === 90 || r === 270) ? W : H;
    const [zx, zy, zw, zh] = sd.bbox;
    if (!zw || !zh) return null;
    const u0 = (b[0] - zx) / zw * W0, v0 = (b[1] - zy) / zh * H0;
    const u1 = (b[0] + b[2] - zx) / zw * W0, v1 = (b[1] + b[3] - zy) / zh * H0;
    if (r === 90) return [H0 - v1, u0, H0 - v0, u1];
    if (r === 180) return [W0 - u1, H0 - v1, W0 - u0, H0 - v0];
    if (r === 270) return [v0, W0 - u1, v1, W0 - u0];
    return [u0, v0, u1, v1];
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
    // โหมด raw: แสดงบรรทัดดิบที่ส่งให้ AI (ชุดเดียวกับที่ AI ตอบ)
    const ocrLines = (p.raw_lines && p.ai && p.ai.mode === "raw") ? p.raw_lines : p.lines;
    if ($("v2ShowOcr").checked && ocrLines) {
      (ocrLines[side] || []).forEach((l) => {
        if (!l.box) return;
        g += '<rect class="ocr" x="' + l.box[0] + '" y="' + l.box[1] + '" width="' + (l.box[2] - l.box[0]) + '" height="' + (l.box[3] - l.box[1]) + '"/>';
      });
    }
    (sd.ignore || []).forEach((b) => {
      const r = ignSentBox(sd, b);
      if (r) g += '<rect class="ign" x="' + r[0] + '" y="' + r[1] + '" width="' + (r[2] - r[0]) + '" height="' + (r[3] - r[1]) + '"><title>พื้นที่ยกเว้น — จุดต่างในนี้ไม่นับ</title></rect>';
    });
    if (EST_BOX) {
      (p.findings || []).forEach((f) => {
        const e = f[side] && !f[side].text && f[side].est_box;
        if (e && f.id != null) {
          g += '<rect class="est" data-f="' + esc(f.id) + '" x="' + e[0] + '" y="' + e[1] + '" width="' + (e[2] - e[0]) +
            '" height="' + (e[3] - e[1]) + '"><title>ตำแหน่งประมาณ (ฝั่งนี้ไม่พบข้อความ)</title></rect>';
        }
      });
    }
    let tags = "";
    (p.findings || []).forEach((f) => {
      if (BOX_STYLE === "span") { g += spanFrame(f, side); return; }
      const d = wordFrame(f, side, W, H);
      g += d.svg;
      tags += d.tag;
    });
    // ข้อความซูมอยู่ "นอก" กล่องภาพ (ในตัวห่อเดียวกัน) — ภาพเตี้ย (แถบกว้าง) ⇒ ย้ายลงใต้ภาพ ไม่บัง/ไม่ถูกขอบตัด
    return (HOVER_ZOOM ? '<div class="v2-res-wrap">' : "") +
      '<div class="v2-res-stage" data-side="' + side + '" data-w="' + W + '" data-h="' + H + '">' +
      '<img alt="ภาพที่ส่งให้ Vision ฝั่ง ' + side.toUpperCase() + '" src="/api/artwork_v2/jobs/' +
      esc(run.job) + "/runs/" + esc(run.run) + "/img/" + esc(sd.image) + '">' +
      '<svg viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none">' + g + "</svg>" + tags +
      (HOVER_ZOOM ? '<div class="v2-zbadge" aria-hidden="true"></div></div><div class="v2-znote" role="status"></div></div>' : "</div>");
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
    const fx = x0 / W, fy = (above ? y0 : y1) / H;          // ตำแหน่งป้ายเป็นสัดส่วน — ใช้ตอนซูมด้วย
    const tag = '<span class="v2-tag ' + f.severity + (above ? "" : " below") + '" data-f="' + f.id +
      '" data-fx="' + fx.toFixed(5) + '" data-fy="' + fy.toFixed(5) + '" style="left:' +
      (fx * 100).toFixed(3) + "%;top:" + (fy * 100).toFixed(3) + '%">' + esc(f.id) + "</span>";
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

  // ── หน้าตาการ์ดผลตรวจแบบใหม่ (ARTWORK_V2_CARD_STYLE · แสดงผลล้วน) ──
  //  · แถบผลตัดสิน: ไอคอน + เหตุผล + ข้อมูลรอบเป็นชิป · หัวการ์ดต่อคู่: ป้ายผล + แถบความครอบคลุม
  //  · หัวคอลัมน์ภาพ: บรรทัดเดียว (รายละเอียดเต็มอยู่ใน title) · ปิดธง = markup เดิมทุกตัวอักษร
  const CARD = root.dataset.cardStyle === "polish";
  const VERDICT_ICON = { PASS: "✓", REVIEW: "!", FAIL: "✕", UNREADABLE: "?" };
  function verdictHtml(r) {
    const chips = ["รอบ " + r.run, r.at, r.version ? "รุ่น " + r.version : "",
      r.sharpness ? "ภาพที่ส่ง: " + (r.sharpness === "max" ? "คมสูงสุด" : "มาตรฐาน 400 dpi") : "",
      r.color_mode && r.color_mode !== "color" ? "สีของภาพ: " + (r.color_mode === "bw" ? "ขาวดำ" : "เทา") : "",
      r.ai && r.ai.mode ? "AI: " + (AI_MODE_TH[r.ai.mode] || r.ai.mode) : ""].filter((x) => x);
    return '<div class="v2-verdict v2-vx v2-v-' + esc(r.verdict) + '"><span class="v2-vic" aria-hidden="true">' +
      esc(VERDICT_ICON[r.verdict] || "•") + '</span><div class="v2-vbody"><div class="v2-vt">' + esc(r.verdict) +
      ' <span>' + esc(r.verdict_th) + "</span></div>" +
      ((r.reasons || []).length ? '<div class="v2-vr">' + r.reasons.map(esc).join(" · ") + "</div>" : "") +
      '<div class="v2-vmeta">' + chips.map((c) => "<span>" + esc(c) + "</span>").join("") + "</div></div></div>";
  }
  function pairHead(p) {
    const cov = p.coverage != null ? Math.round(p.coverage * 100) : null;
    return '<div class="v2-ph"><span class="v2-pnum">คู่ ' + esc(p.n) + "</span>" +
      (p.verdict ? '<span class="v2-vpill v2-v-' + esc(p.verdict) + '">' + esc(VERDICT_ICON[p.verdict] || "") + " " +
        esc(p.verdict) + "</span>" : "") +
      (cov != null ? '<span class="v2-covm' + (cov < 90 ? " lo" : "") + '" title="สัดส่วนบรรทัดที่จับคู่ข้อความ 🅰↔🅱 ได้' +
        (p.coverage_ignored ? " (อัลกอริทึม — ไม่ใช้ตัดสินในโหมดข้อมูลดิบ)" : "") + '">จับคู่ข้อความ <i><b style="width:' +
        Math.max(0, Math.min(100, cov)) + '%"></b></i> ' + cov + "%" + (p.coverage_ignored ? " *" : "") + "</span>" : "") +
      "</div>" + (p.reasons && p.reasons.length ? '<div class="v2-preason">' + p.reasons.map(esc).join(" · ") + "</div>" : "");
  }
  function sideHead(s, sd) {
    const st = sd.stats || {};
    const full = sd.sent_px[0] + "×" + sd.sent_px[1] + " px" + (sd.render && sd.render.dpi ? " · " + sd.render.dpi + " dpi" : "") +
      " · JPEG q" + (sd.encode && sd.encode.quality) + " · " + (sd.jpeg_bytes / 1024).toFixed(0) + " KB" +
      (st.lines != null ? " · " + st.lines + " บรรทัด · ความมั่นใจเฉลี่ย " + (st.conf_mean != null ? st.conf_mean.toFixed(2) : "-") : "") +
      (st.langs && st.langs.length ? " · ภาษา " + st.langs.join(",") : "");
    const short = [sd.page != null ? "หน้า " + (sd.page + 1) : "", sd.render && sd.render.dpi ? sd.render.dpi + " dpi" : "",
      st.lines != null ? st.lines + " บรรทัด" : "", st.conf_mean != null ? "มั่นใจ " + Math.round(st.conf_mean * 100) + "%" : "",
      sd.rotate ? "↻" + sd.rotate + "°" : ""].filter((x) => x).join(" · ");
    return '<div class="v2-colh"><span class="v2-sbadge ' + s + '">' + (s === "a" ? "🅰" : "🅱") + '</span><span class="v2-colm" title="' +
      esc(full) + '">' + esc(short) + "</span></div>" +
      (sd.ok ? "" : '<div class="v2-bad v2-colerr">อ่านไม่ได้: ' + esc(sd.error) + "</div>");
  }

  function showResult(r) {
    S.result = r;
    S.review = null;                                 // ผลใหม่ = เลขจุดชุดใหม่ ⇒ ไม่ใช้ผลรีวิวของผลเดิม
    $("v2ResCard").classList.remove("v2-hidden");
    $("v2LogCard").classList.remove("v2-hidden");
    $("v2Verdict").innerHTML = CARD ? verdictHtml(r) : '<div class="v2-verdict v2-v-' + esc(r.verdict) + '">' + esc(r.verdict) + " — " +
      esc(r.verdict_th) + "<small>" + (r.reasons || []).map(esc).join(" · ") + " · รอบ " + esc(r.run) +
      " · " + esc(r.at) + (r.version ? " · รุ่น " + esc(r.version) : "") +
      (r.sharpness ? " · ภาพที่ส่ง: " + (r.sharpness === "max" ? "คมสูงสุด" : "มาตรฐาน 400 dpi") : "") +
      (r.color_mode && r.color_mode !== "color" ? " · สีของภาพ: " + (r.color_mode === "bw" ? "ขาวดำ" : "เทา") : "") +
      (r.ai && r.ai.mode ? " · AI: " + esc(AI_MODE_TH[r.ai.mode] || r.ai.mode) : "") +
      "</small></div>";
    $("v2Warn").innerHTML = (r.warnings && r.warnings.length)
      ? '<div class="v2-warnbox">⚠️ ' + r.warnings.map(esc).join("<br>⚠️ ") + "</div>" : "";
    if (HOVER_ZOOM) RZ.pin = null;                 // ผลใหม่ = เลขจุดชุดใหม่ ⇒ ไม่ค้างการซูมของผลเดิม
    renderPairs();
    $("v2Log").value = r.log_text || "";
    if (REVIEW) loadReview(r);
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
    if (HOVER_ZOOM) zoomReset();
    GRP = {};
    $("v2PairsRes").innerHTML = (r.pairs || []).map((p) => {
      const rows = rowsHtml(p.findings, true);
      const debHtml = folded("อยู่ในพื้นที่ยกเว้นที่กำหนดในโซน — ไม่นับในผลตัดสิน", p.excluded) +
        folded("ภาพเหมือนกันทุกพิกเซล — OCR อ่านต่างเอง · ไม่นับในผลตัดสิน (เปิดดูภาพหลักฐานได้)", p.pixel_same) +
        folded("ข้อความมีอยู่ในอีกฝั่งตรงตำแหน่งเดียวกัน (OCR จัดบรรทัดต่างกัน) — ไม่นับในผลตัดสิน", p.relocated) +
        folded("เศษอักขระ / ขอบโซน — ไม่นับในผลตัดสิน", p.debris) +
        folded("เครื่องหมายเดี่ยวที่ OCR อ่านไม่มั่นใจ (มีฝั่งเดียว) — ไม่นับในผลตัดสิน", p.lowmark) +
        folded(p.raw_lines && p.ai && p.ai.mode === "raw"
          ? "ผลของอัลกอริทึม — ไว้เทียบกับ AI เท่านั้น ไม่นับในผลตัดสิน (โหมด AI ตัดสินจากข้อมูลดิบ)"
          : "อัลกอริทึมพบ แต่ AI ไม่ได้ระบุ — ไม่นับในผลตัดสิน (โหมด AI ตัดสินหลัก)", p.algo_only) +
        folded(p.ai && p.ai.mode === "image"
          ? "AI ดูภาพแล้วสองฝั่งพิมพ์เหมือนกัน (Vision อ่านผิด) — ไม่นับในผลตัดสิน"
          : "AI ตัดสินว่าเป็นสัญญาณรบกวนของ OCR — ไม่นับในผลตัดสิน", p.ai_dismissed);
      return (CARD ? '<div class="v2-card v2-pcard v2-pv-' + esc(p.verdict || "") + '" data-pn="' + esc(p.n) + '">' + pairHead(p) :
        '<div class="v2-card" data-pn="' + esc(p.n) + '" style="margin:12px 0"><b>คู่ ' + p.n + "</b> — " + esc(p.verdict || "") +
        (p.coverage != null ? " · จับคู่ข้อความได้ " + Math.round(p.coverage * 100) + "%" +
          (p.coverage_ignored ? " (อัลกอริทึม — ไม่ใช้ตัดสินในโหมดข้อมูลดิบ)" : "") : "") +
        (p.reasons && p.reasons.length ? ' <span class="v2-muted">(' + p.reasons.map(esc).join(" · ") + ")</span>" : "")) +
        (SIDE_TABLE ? sideLayout(p, r, rows) :
        '<div class="v2-panes" style="margin-top:8px"><div>' + colHead("a", p.sides.a) + svgFor(p, "a", r) +
        "</div><div>" + colHead("b", p.sides.b) + svgFor(p, "b", r) + "</div></div>" +
        (rows ? mainTable(rows, "", "") + '<div class="v2-tbl-more v2-muted" hidden></div>' : (p.unreadable ? "" : '<div class="v2-muted" style="margin-top:6px">ไม่พบจุดต่าง</div>'))) +
        aiBox(p) + debHtml + "</div>";
    }).join("");
    if (HOVER_ZOOM) zoomAfterRender();
    if (REVIEW) applyReview();
    fitRows();
    if (SIDE_TABLE) {
      fitSide();
      document.querySelectorAll("#v2PairsRes .v2-res-stage img").forEach((im) => im.addEventListener("load", fitSide));
    }
  }

  function colHead(s, sd) { return CARD ? sideHead(s, sd) : (s === "a" ? "🅰 " : "🅱 ") + sideInfo(sd); }

  // ตารางจุดต่างหลัก (ตัวเดียวที่ถูกจำกัดความสูง — รายการพับไม่ถูกจำกัด)
  function mainTable(rows, wrapCls, tblCls) {
    return '<div class="v2-tbl-wrap v2-main' + wrapCls + '"><table class="v2-tbl' + tblCls + '">' + TBL_HEAD +
      "<tbody>" + rows + "</tbody></table></div>";
  }
  // ภาพ 🅰 | ภาพ 🅱 | ตารางจุดต่าง (สูงเท่าภาพ แต่ไม่เกินจอ · หัวตารางค้าง)
  function sideLayout(p, r, rows) {
    const fs = p.findings || [];
    const nr = fs.filter((f) => f.severity === "red").length, ny = fs.filter((f) => f.severity === "yellow").length;
    const head = '<div class="v2-side-h"><b>จุดต่าง</b>' +
      (nr ? ' <span class="v2-sev red">ต่าง ' + nr + "</span>" : "") +
      (ny ? ' <span class="v2-sev yellow">ไม่มั่นใจ ' + ny + "</span>" : "") +
      (!nr && !ny ? ' <span class="v2-muted">ไม่มี</span>' : "") +
      '<span class="v2-muted v2-side-tip">ชี้แถว = ซูมภาพ · ⓘ = หมายเหตุ</span></div>';
    const body = rows ? mainTable(rows, " v2-side-in", " v2-tbl-side")
      : '<div class="v2-side-empty v2-muted">' + (p.unreadable ? "อ่านไม่ได้ — ไม่มีตารางจุดต่าง" : "ไม่พบจุดต่าง") + "</div>";
    return '<div class="v2-resgrid"><div class="v2-rescol">' + colHead("a", p.sides.a) + svgFor(p, "a", r) +
      '</div><div class="v2-rescol">' + colHead("b", p.sides.b) + svgFor(p, "b", r) +
      '</div><div class="v2-side">' + head + body + "</div></div>";
  }
  // ความสูงของตารางข้างภาพ = ความสูงของคอลัมน์ภาพ (ไม่ต่ำกว่า 360 px · ไม่เกินความสูงจอ)
  function fitSide() {
    if (!SIDE_TABLE) return;
    document.querySelectorAll("#v2PairsRes .v2-resgrid").forEach((g) => {
      const box = g.querySelector(".v2-side-in");
      if (!box) return;
      const cols = g.querySelectorAll(".v2-rescol");
      const stack = window.matchMedia && window.matchMedia("(max-width: 1199px)").matches;
      const nav = document.querySelector(".navbar");
      const h = stack ? window.innerHeight * 0.6
        : Math.min(Math.max.apply(null, Array.from(cols).map((c) => c.offsetHeight)),
                   window.innerHeight - (nav ? nav.offsetHeight : 0) - 24);
      const head = g.querySelector(".v2-side-h");
      const mh = Math.max(360, Math.round(h - (stack || !head ? 0 : head.offsetHeight + 6))) + "px";
      if (box.style.maxHeight !== mh) box.style.maxHeight = mh;
    });
  }

  // ตารางจุดต่างหลักแสดง TABLE_ROWS แถวแล้วเลื่อนในตาราง ⇒ ภาพที่ซูมยังอยู่บนจอ (ARTWORK_V2_TABLE_ROWS · 0 = แบบเดิม)
  //  · วัดความสูงจริงของหัวตาราง + N แถวแรก (แถวสูงไม่เท่ากัน) · เพดาน 60% ของความสูงจอ แต่ไม่ต่ำกว่า 1 แถว
  //  · ยังไม่ได้จัดวาง (ซ่อนอยู่ = สูง 0) ⇒ ไม่จำกัด แล้ววัดใหม่เมื่อขนาดเปลี่ยน
  const TABLE_ROWS = Math.max(0, parseInt(root.dataset.tableRows || "0", 10) || 0);
  function fitRows() {
    if (!TABLE_ROWS) return;
    if (SIDE_TABLE) { fitSide(); return; }            // ตารางข้างภาพ: สูงตามภาพแทนจำนวนแถว
    document.querySelectorAll("#v2PairsRes .v2-tbl-wrap.v2-main").forEach((w) => {
      const trs = w.querySelectorAll("tbody > tr");
      const head = w.querySelector("thead");
      const more = w.nextElementSibling && w.nextElementSibling.classList.contains("v2-tbl-more") ? w.nextElementSibling : null;
      const hh = head ? head.offsetHeight : 0;
      let h = hh;
      for (let i = 0; i < Math.min(TABLE_ROWS, trs.length); i++) h += trs[i].offsetHeight;
      const capped = trs.length > TABLE_ROWS && h > hh;
      const lim = Math.max(hh + (trs.length ? trs[0].offsetHeight : 0), window.innerHeight * 0.6);
      const mh = capped ? Math.ceil(Math.min(h, lim)) + 2 + "px" : "";
      if (w.style.maxHeight !== mh) w.style.maxHeight = mh;
      w.classList.toggle("v2-capped", capped);
      if (more) {
        more.hidden = !capped;
        // แถวสูงจนชนเพดานจอ ⇒ เห็นไม่ถึง N แถว — ห้ามบอกว่า "แสดง N"
        more.textContent = capped ? (h > lim ? "ตารางมี " + trs.length + " แถว" : "แสดง " + TABLE_ROWS + " จาก " + trs.length + " แถว") +
          " — เลื่อนในตารางเพื่อดูที่เหลือ" + (SORT_SEVERITY ? " (จุดต่างขึ้นก่อน ไม่มั่นใจอยู่ด้านล่าง)" : "") : "";
      }
    });
  }
  if (SIDE_TABLE && !TABLE_ROWS) window.addEventListener("resize", fitSide);
  if (TABLE_ROWS && window.ResizeObserver) {
    let fitQ = 0;
    new ResizeObserver(() => {
      if (fitQ) return;
      fitQ = requestAnimationFrame(() => { fitQ = 0; fitRows(); });
    }).observe($("v2PairsRes"));
    window.addEventListener("resize", () => {          // เพดาน 60% ของความสูงจอ — จอเปลี่ยนสูงแต่กว้างเท่าเดิม
      if (!fitQ) fitQ = requestAnimationFrame(() => { fitQ = 0; fitRows(); });
    });
  }

  $("v2PairsRes").addEventListener("click", (ev) => {
    const rb = ev.target.closest ? ev.target.closest(".v2-rvb") : null;
    if (rb) {
      // ปุ่มรีวิว: ไม่เลือกแถว ไม่ค้างการซูม
      ev.stopPropagation();
      sendReview(rb.closest(".v2-rv"), rb.dataset.st);
      return;
    }
    const nb = ev.target.closest ? ev.target.closest(".v2-nbtn") : null;
    if (nb) {
      // หมายเหตุ: เปิด/ปิดแถวย่อย — ไม่เลือกแถว ไม่ค้างการซูม
      ev.stopPropagation();
      const tbl = nb.closest("table");
      const open = nb.getAttribute("aria-expanded") !== "true";
      const btns = nb.classList.contains("v2-nall") ? tbl.querySelectorAll(".v2-nbtn") : [nb];
      btns.forEach((b) => {
        b.setAttribute("aria-expanded", open ? "true" : "false");
        b.classList.toggle("on", open);
        const row = b.dataset.nt != null ? tbl.querySelector('tr.v2-note[data-note="' + CSS.escape(b.dataset.nt) + '"]') : null;
        if (row) row.hidden = !open;
      });
      return;
    }
    const tr = ev.target.closest ? ev.target.closest("tr[data-f]") : null;
    if (!tr) return;
    const id = tr.dataset.f;
    if (HOVER_ZOOM && RZ.ok.has(id)) { zoomClick(id); return; }
    document.querySelectorAll("#v2PairsRes tr.sel").forEach((x) => x.classList.remove("sel"));
    tr.classList.add("sel");
    const ids = idsOf(id);
    document.querySelectorAll("#v2PairsRes rect.f, #v2PairsRes rect.d, #v2PairsRes .v2-tag")
      .forEach((x) => x.classList.toggle("hot", ids.has(x.dataset.f)));
    const hit = hitOf(id);
    if (hit) hit.closest(".v2-res-stage").scrollIntoView({ behavior: "smooth", block: "center" });
  });

  $("v2ShowOcr").addEventListener("change", renderPairs);
  $("v2ShowSkip").addEventListener("change", renderPairs);

  // ── ซูมตามแถว (ARTWORK_V2_HOVER_ZOOM · แสดงผลล้วน — ไม่แตะผลตรวจ/Log/ผลตัดสิน) ─────────
  // ชี้เมาส์ที่แถว ⇒ ภาพ 🅰 และ 🅱 ซูมเข้าหากรอบของจุดนั้นพร้อมกัน · เอาเมาส์ออก ⇒ ซูมกลับ · คลิก = ค้างไว้
  //  · ภาพ: CSS transform (translate+scale) ของภาพที่ส่งให้ Vision (ความละเอียดเต็ม) ⇒ ขยายแล้วยังคม
  //  · กรอบ: เปลี่ยน viewBox ของ SVG ⇒ เวกเตอร์คมทุกระดับ เส้นบางเท่าเดิม (non-scaling-stroke)
  //  · ป้ายเลข: เลื่อนตามจุดบนภาพแต่ไม่ขยาย
  //  · ไม่เคยเห็นพื้นที่นอกภาพ (บีบมุมมองให้อยู่ในภาพเสมอ) · ไม่ซูมเกินความละเอียดจริงของภาพ
  const ZOOM = {
    fillW: 0.55, fillH: 0.45,    // กรอบเป้าหมายกว้าง/สูงประมาณนี้ของกล่อง ⇒ เห็นคำข้างเคียงเป็นบริบท
    fitW: 0.94, fitH: 0.8,       // กรอบกว้างมาก (เช่นทั้งบรรทัด) ⇒ ขยายแค่พอดีกล่อง
    minGain: 1.25,
    minCtx: 3,                   // จุดแทรก/คำสั้น ⇒ มุมมองกว้างอย่างน้อย 3 เท่าของความสูงคำ
    maxAbs: 8, minCap: 2,        // เพดาน = ความละเอียดจริงของภาพ (อย่างน้อย 2 เท่า · ไม่เกิน 8 เท่า)
    enterMs: 120,                // ชี้ค้างสั้น ๆ ก่อนซูม ⇒ ลากเมาส์ผ่านหลายแถวแล้วภาพไม่กระพือ
    moveMs: 60, leaveMs: 160,
    inMs: 460, outMs: 380,       // ระยะเวลาเคลื่อนไหว (ซูมเข้า/ออก)
    minMs: 320, maxMs: 650, wijkMs: 420,
  };
  const RZ = { cur: new WeakMap(), anims: [], raf: 0, t0: 0, ms: 0, timer: 0,
              hover: null, pin: null, shown: null, ok: new Set(), quietUntil: 0, lastEl: null, vw: 0 };

  // กรอบเป้าหมาย (พิกัดภาพที่ส่ง) = กรอบที่วาดบนจอ (เผื่อเท่ากับ wordFrame) · ไม่มีกรอบ ⇒ null
  function zoomRect(sd, W, H, padK, ctx) {
    const wb = sd && (sd.word_box || sd.box);
    if (!wb || !(wb[2] >= wb[0]) || !(wb[3] >= wb[1])) return null;
    const h = Math.max(1, wb[3] - wb[1]);
    const pad = Math.max(2, h * padK);
    let x0 = wb[0] - pad, x1 = wb[2] + pad;
    if (x1 - x0 < h * ctx) {
      const c = (x0 + x1) / 2;
      x0 = c - h * ctx / 2;
      x1 = c + h * ctx / 2;
    }
    return [Math.max(0, x0), Math.max(0, wb[1] - pad), Math.min(W, x1), Math.min(H, wb[3] + pad)];
  }

  // มุมมองต้องคลุมกล่องเต็มเสมอ (ไม่มีขอบว่างนอกภาพ) · ขยาย < 1.001 = ภาพเต็มพอดี
  function zoomClamp(v, sw, sh) {
    if (!(v.s > 1.001)) return { s: 1, tx: 0, ty: 0 };
    return { s: v.s, tx: Math.min(0, Math.max(sw - v.s * sw, v.tx)), ty: Math.min(0, Math.max(sh - v.s * sh, v.ty)) };
  }

  // มุมมองที่วางกรอบเป้าหมายกลางกล่อง: จุด p (px ของกล่องตอนไม่ซูม) ไปอยู่ที่ (tx + s·p) บนจอ
  function zoomView(r, W, H, sw, sh, cap, cfg) {
    const kx = sw / W, ky = sh / H;
    const bw = Math.max(1, (r[2] - r[0]) * kx), bh = Math.max(1, (r[3] - r[1]) * ky);
    let s = Math.min(cfg.fillW * sw / bw, cfg.fillH * sh / bh);
    if (s < cfg.minGain) s = Math.min(cfg.fitW * sw / bw, cfg.fitH * sh / bh);
    s = Math.min(s, cfg.maxAbs, Math.max(cfg.minCap, cap));
    if (!(s > 1.05)) return { s: 1, tx: 0, ty: 0 };
    const cx = (r[0] + r[2]) / 2 * kx, cy = (r[1] + r[3]) / 2 * ky;
    return zoomClamp({ s: s, tx: sw / 2 - s * cx, ty: sh / 2 - s * cy }, sw, sh);
  }

  // เส้นทางจากมุมมอง a ไป b · คืน { at(e) → มุมมอง (e = 0..1 หลัง easing), ms }
  //  · ฝั่งหนึ่งเป็นภาพเต็ม ⇒ ซูมรอบ "จุดนิ่ง" (จุดบนภาพที่ไม่ขยับบนจอ) ขนาดเปลี่ยนแบบลอการิทึม
  //    (ตาเห็นความเร็วซูมสม่ำเสมอ) · จุดนิ่งอยู่ในกล่องเสมอ ⇒ ทุกเฟรมอยู่ในภาพโดยไม่ต้องบีบ
  //  · ซูมอยู่ทั้งสองฝั่ง ⇒ เส้นทางของ van Wijk & Nuij (2003) — ถอยออกพอเห็นบริบทระหว่างเลื่อนแล้วเข้าใหม่
  function zoomPath(a, b, sw, sh, cfg) {
    if (Math.abs(a.s - b.s) < 1e-9 && Math.abs(a.tx - b.tx) < 0.01 && Math.abs(a.ty - b.ty) < 0.01) {
      return { at: () => b, ms: 0 };
    }
    if (a.s <= 1 || b.s <= 1) {
      const k = 1 / (b.s - a.s), px = (a.tx - b.tx) * k, py = (a.ty - b.ty) * k;
      return {
        at: (e) => {
          if (e >= 1) return b;
          const s = a.s * Math.pow(b.s / a.s, e);
          return { s: s, tx: a.tx + (a.s - s) * px, ty: a.ty + (a.s - s) * py };
        },
        ms: b.s > a.s ? cfg.inMs : cfg.outMs,
      };
    }
    const R = Math.SQRT2;
    const ux0 = (sw / 2 - a.tx) / a.s, uy0 = (sh / 2 - a.ty) / a.s, w0 = sw / a.s;
    const ux1 = (sw / 2 - b.tx) / b.s, uy1 = (sh / 2 - b.ty) / b.s, w1 = sw / b.s;
    const dx = ux1 - ux0, dy = uy1 - uy0, d2 = dx * dx + dy * dy;
    let f, S;
    if (d2 < 1e-12) {
      S = Math.log(w1 / w0) / R;
      f = (t) => [ux0, uy0, w0 * Math.exp(R * t * S)];
    } else {
      const d1 = Math.sqrt(d2);
      const b0 = (w1 * w1 - w0 * w0 + 4 * d2) / (4 * w0 * d1);
      const b1 = (w1 * w1 - w0 * w0 - 4 * d2) / (4 * w1 * d1);
      const r0 = Math.log(Math.sqrt(b0 * b0 + 1) - b0), r1 = Math.log(Math.sqrt(b1 * b1 + 1) - b1);
      const c0 = Math.cosh(r0), s0 = Math.sinh(r0);
      S = (r1 - r0) / R;
      f = (t) => {
        const q = R * t * S + r0;
        const u = w0 / (2 * d1) * (c0 * Math.tanh(q) - s0);
        return [ux0 + u * dx, uy0 + u * dy, w0 * c0 / Math.cosh(q)];
      };
    }
    return {
      at: (e) => {
        if (e >= 1) return b;
        const p = f(e), s = sw / p[2];
        return zoomClamp({ s: s, tx: sw / 2 - s * p[0], ty: sh / 2 - s * p[1] }, sw, sh);
      },
      ms: Math.min(cfg.maxMs, Math.max(cfg.minMs, Math.abs(S) * cfg.wijkMs)),
    };
  }

  // easeInOutCubic — ออกตัวนุ่ม หยุดนุ่ม
  function zoomEase(t) {
    if (t <= 0) return 0;
    if (t >= 1) return 1;
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  }

  // ขนาดกล่องต้องเป็นค่าทศนิยมจริง (ภาพสูง 362.45 px แต่ clientHeight ปัดเป็น 362 ⇒ กรอบเพี้ยน 1.6 px ที่ขอบล่าง)
  // วัดจาก SVG ซึ่งไม่เคยถูก transform และทับพื้นที่ภาพพอดี
  function zoomStage(st) {
    const svg = st.querySelector("svg"), rb = svg ? svg.getBoundingClientRect() : { width: 0, height: 0 };
    return { st: st, img: st.querySelector("img"), svg: svg,
             tags: Array.from(st.querySelectorAll(".v2-tag")), W: +st.dataset.w, H: +st.dataset.h,
             sw: rb.width, sh: rb.height };
  }

  // วาดมุมมอง v ลงกล่องเดียว — ภาพ (transform) กับกรอบ (viewBox) มาจากค่าชุดเดียวกัน ⇒ ทับกันพอดีทุกเฟรม
  function zoomApply(z, v) {
    const full = !(v.s > 1);
    if (z.img) z.img.style.transform = full ? "" :
      "translate(" + v.tx.toFixed(3) + "px," + v.ty.toFixed(3) + "px) scale(" + v.s.toFixed(5) + ")";
    if (z.svg) z.svg.setAttribute("viewBox", full ? "0 0 " + z.W + " " + z.H :
      (-v.tx / (v.s * z.sw) * z.W).toFixed(3) + " " + (-v.ty / (v.s * z.sh) * z.H).toFixed(3) + " " +
      (z.W / v.s).toFixed(3) + " " + (z.H / v.s).toFixed(3));
    z.tags.forEach((t) => {
      if (full) { t.style.transform = ""; return; }
      const dx = v.tx + (v.s - 1) * (+t.dataset.fx || 0) * z.sw, dy = v.ty + (v.s - 1) * (+t.dataset.fy || 0) * z.sh;
      t.style.transform = "translate(" + dx.toFixed(2) + "px," + dy.toFixed(2) + "px)" +
        (t.classList.contains("below") ? "" : " translateY(-100%)");
    });
    RZ.cur.set(z.st, v);
  }

  function zoomStep(now) {
    RZ.raf = 0;
    const t = RZ.ms > 0 ? Math.max(0, Math.min(1, (now - RZ.t0) / RZ.ms)) : 1;
    const e = zoomEase(t);
    RZ.anims.forEach((a) => zoomApply(a.z, t >= 1 ? a.to : a.path.at(e)));
    if (t < 1) RZ.raf = requestAnimationFrame(zoomStep);
    else RZ.anims = [];
  }

  // ซูมทุกกล่องไปที่จุด id (null = ภาพเต็ม) · คู่อื่นที่ซูมค้างอยู่กลับเป็นภาพเต็มพร้อมกัน
  function zoomTo(id, instant) {
    RZ.shown = id;
    const ids = idsOf(id);
    const hit = hitOf(id);
    const card = hit ? hit.closest(".v2-card") : null;
    const r = S.result;
    const p = card && r ? (r.pairs || []).find((x) => String(x.n) === card.dataset.pn) : null;
    const mem = p ? (p.findings || []).filter((x) => ids.has(String(x.id))) : [];
    const f = !mem.length ? null : (GRP[id] ? groupTarget(mem) : mem[0]);
    const reduce = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    const plans = [];
    document.querySelectorAll("#v2PairsRes .v2-res-stage").forEach((st) => {
      const z = zoomStage(st);
      const mine = !!f && card.contains(st);
      let to = { s: 1, tx: 0, ty: 0 }, note = "", estNote = false;
      if (mine) {
        const sd = f[st.dataset.side];
        let rect = zoomRect(sd, z.W, z.H, FRAME_PAD, ZOOM.minCtx);
        if (!rect && EST_BOX && sd && !sd.text && sd.est_box) {
          // ไม่มีข้อความฝั่งนี้ — ซูมไปที่ "ตำแหน่งประมาณ" จากบรรทัดที่ตรงกันข้างเคียง (เห็นบริบทกว้างกว่าปกติ)
          rect = zoomRect({ box: sd.est_box }, z.W, z.H, FRAME_PAD * 2, ZOOM.minCtx * 1.5);
          if (rect) { note = ZNOTE_EST; estNote = true; }
        }
        if (!rect) note = sd && sd.text ? "จุดนี้ไม่มีตำแหน่งบนภาพฝั่งนี้" : "ฝั่งนี้ไม่พบบรรทัดที่ตรงกับอีกฝั่ง";
        else if (z.sw > 0 && z.sh > 0 && z.W > 0 && z.H > 0 && z.img && z.img.naturalWidth) {
          to = zoomView(rect, z.W, z.H, z.sw, z.sh, z.img.naturalWidth / z.sw, ZOOM);
        }
      }
      st.classList.toggle("zoomed", mine);
      st.querySelectorAll("[data-f]").forEach((el) => el.classList.toggle("hov", mine && ids.has(el.dataset.f)));
      const nt = noteOf(st), bd = st.querySelector(".v2-zbadge");
      if (nt) { nt.textContent = note; nt.classList.toggle("on", !!note); nt.classList.toggle("est", estNote); }
      if (bd) { bd.textContent = "🔍 ×" + to.s.toFixed(1); bd.classList.toggle("on", to.s > 1); }
      plans.push({ z: z, to: to, path: zoomPath(RZ.cur.get(st) || { s: 1, tx: 0, ty: 0 }, to, z.sw, z.sh, ZOOM) });
    });
    document.querySelectorAll("#v2PairsRes tr[data-f]").forEach((tr) => tr.classList.toggle("hov", !!f && tr.dataset.f === String(id)));
    if (RZ.raf) cancelAnimationFrame(RZ.raf);
    RZ.raf = 0;
    RZ.anims = plans;
    RZ.ms = instant || reduce ? 0 : Math.max(0, ...plans.map((x) => x.path.ms));
    RZ.t0 = performance.now();
    if (RZ.ms) RZ.raf = requestAnimationFrame(zoomStep);
    else zoomStep(RZ.t0);
  }

  // ชี้เมาส์เข้า/ออกแถว — หน่วงสั้น ๆ ก่อนซูม (ลากผ่านหลายแถวแล้วภาพไม่กระพือ)
  function zoomHover(id) {
    if (id === RZ.hover) return;
    RZ.hover = id;
    clearTimeout(RZ.timer);
    const want = id != null ? id : RZ.pin;
    if (want === RZ.shown) return;
    const wait = id == null ? ZOOM.leaveMs : (RZ.shown != null ? ZOOM.moveMs : ZOOM.enterMs);
    RZ.timer = setTimeout(() => zoomTo(want), wait);
  }

  // เลือกแถวแบบเดิม (แถวสีฟ้า + กรอบเส้นหนา) · id = null ⇒ ไม่เลือกอะไร
  function zoomSelect(id) {
    document.querySelectorAll("#v2PairsRes tr.sel").forEach((x) => x.classList.remove("sel"));
    const ids = idsOf(id);
    document.querySelectorAll("#v2PairsRes rect.f, #v2PairsRes rect.d, #v2PairsRes .v2-tag")
      .forEach((x) => x.classList.toggle("hot", ids.has(x.dataset.f)));
    const tr = id != null ? document.querySelector('#v2PairsRes tr[data-f="' + id + '"]') : null;
    if (tr) tr.classList.add("sel");
  }

  // คลิกแถว = ค้างการซูมไว้ (เมาส์ออกแล้วยังซูม) · คลิกแถวเดิมซ้ำ/Esc/คลิกที่อื่น = ปล่อย
  function zoomClick(id) {
    clearTimeout(RZ.timer);
    if (RZ.pin === id) { zoomUnpin(); return; }
    RZ.pin = id;
    zoomSelect(id);
    const hit = hitOf(id);
    const st = hit ? hit.closest(".v2-res-stage") : null;
    if (st) {
      const rc = st.getBoundingClientRect();
      if (rc.top < 0 || rc.bottom > window.innerHeight) {   // ภาพไม่อยู่บนจอทั้งภาพ ⇒ เลื่อนจอไปที่ภาพ (แบบเดิม)
        RZ.quietUntil = performance.now() + 800;              // ระหว่างจอเลื่อน แถวที่ผ่านใต้เมาส์ไม่นับเป็นการชี้
        st.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }
    if (RZ.shown !== id) zoomTo(id);
  }

  function zoomUnpin() {
    RZ.pin = null;
    zoomSelect(null);
    clearTimeout(RZ.timer);
    if (RZ.shown !== RZ.hover) zoomTo(RZ.hover);
  }

  // ล้างสถานะก่อนวาดผลใหม่ (กล่องเดิมถูกแทนที่แล้ว) — ไม่ล้างการค้าง (ติ๊กตัวเลือกแสดงผลแล้วยังค้างอยู่)
  function zoomReset() {
    if (RZ.raf) cancelAnimationFrame(RZ.raf);
    clearTimeout(RZ.timer);
    RZ.raf = 0;
    RZ.anims = [];
    RZ.cur = new WeakMap();
    RZ.hover = null;
    RZ.shown = null;
    RZ.lastEl = null;
    RZ.ok = new Set();
  }

  // ข้อความของกล่องภาพ (อยู่ในตัวห่อ ไม่ใช่ในกล่องภาพที่ตัดขอบ)
  function noteOf(st) {
    const w = st.parentNode;
    return w && w.classList && w.classList.contains("v2-res-wrap") ? w.querySelector(".v2-znote") : st.querySelector(".v2-znote");
  }
  // ภาพเตี้ยกว่า ZNOTE_SHORT_PX ⇒ ข้อความอยู่ใต้ภาพ (จองที่ไว้ล่วงหน้า — ชี้แถวแล้วหน้าไม่กระโดด)
  //   ภาพสูงพอ ⇒ ซ้อนบนภาพเหมือนเดิม (ไม่เสียพื้นที่ใต้ภาพทุกใบ)
  const ZNOTE_SHORT_PX = 160;
  const ZNOTE_EST = "ฝั่งนี้ไม่พบข้อความนี้ — กรอบประสีส้ม = ตำแหน่งประมาณจากบรรทัดข้างเคียง (ไม่ใช่ตำแหน่งที่วัดได้)";
  function markShortStages() {
    document.querySelectorAll("#v2PairsRes .v2-res-wrap").forEach((w) => {
      const st = w.querySelector(".v2-res-stage"), n = w.querySelector(".v2-znote");
      const h = st ? st.getBoundingClientRect().height : 0;
      if (!(h > 0) || !n) return;
      w.classList.toggle("short", h < ZNOTE_SHORT_PX);
      n.style.minHeight = "";
      if (h < ZNOTE_SHORT_PX) {       // จองที่เท่าข้อความที่ยาวที่สุดที่ความกว้างนี้ (จอแคบตัดหลายบรรทัด)
        const t = n.textContent;
        n.textContent = ZNOTE_EST;
        n.style.minHeight = Math.ceil(n.getBoundingClientRect().height) + "px";
        n.textContent = t;
      }
    });
  }

  function zoomAfterRender() {
    markShortStages();
    RZ.ok = new Set(Array.from(document.querySelectorAll("#v2PairsRes rect.f[data-f]")).map((x) => x.dataset.f));
    Object.keys(GRP).forEach((g) => { if (GRP[g].some((x) => RZ.ok.has(x))) RZ.ok.add(g); });   // แถวกลุ่ม
    // ภาพโหลดเสร็จทีหลัง (ขนาดกล่องเพิ่งรู้) ⇒ วางมุมมองที่ค้าง/ชี้อยู่ใหม่ทันที
    document.querySelectorAll("#v2PairsRes .v2-res-stage img").forEach((img) => {
      img.addEventListener("load", () => { markShortStages(); if (RZ.shown != null) zoomTo(RZ.shown, true); });
    });
    if (RZ.pin != null && RZ.ok.has(RZ.pin)) { zoomSelect(RZ.pin); zoomTo(RZ.pin, true); }
    else RZ.pin = null;
  }

  if (HOVER_ZOOM) {
    const res = $("v2PairsRes");
    const zoomRowOf = (el) => {
      const tr = el && el.closest ? el.closest("tr[data-f]") : null;
      return tr && RZ.ok.has(tr.dataset.f) ? tr.dataset.f : null;
    };
    res.addEventListener("pointermove", (ev) => {
      if (ev.pointerType === "touch" || ev.target === RZ.lastEl || performance.now() < RZ.quietUntil) return;
      RZ.lastEl = ev.target;
      zoomHover(zoomRowOf(ev.target));
    });
    res.addEventListener("pointerleave", (ev) => {
      if (ev.pointerType === "touch") return;
      RZ.lastEl = null;
      zoomHover(null);
    });
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && RZ.pin != null) zoomUnpin();
    });
    document.addEventListener("click", (ev) => {
      // คลิกที่อื่น = ปล่อย · ยกเว้นแถวในตาราง (สลับค้างเอง) ตัวเลือกการแสดงผลเหนือภาพ และแถบรีวิว (กดเลขจุด = ไปที่แถวนั้น)
      const t = ev.target && ev.target.closest ? ev.target : null;
      if (RZ.pin != null && !(t && (t.closest("#v2PairsRes tr[data-f]") || t.closest(".v2-legend") ||
                                    t.closest("#v2Review")))) zoomUnpin();
    });
    window.addEventListener("resize", () => {
      if (window.innerWidth === RZ.vw) return;               // มือถือ: แถบที่อยู่ซ่อน/แสดง = ความสูงเปลี่ยนอย่างเดียว
      RZ.vw = window.innerWidth;
      markShortStages();
      if (RZ.shown != null) zoomTo(RZ.shown, true);
    });
    RZ.vw = window.innerWidth;
  }

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
    if ($("v2Root") && $("v2Root").dataset.restoreConfirm === "0") openJob(sess.job, sess.pairs || [], sess.page, sess.rot);
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
      openJob(s.job, Array.isArray(s.pairs) ? s.pairs : [], s.page, s.rot);
    });
    $("v2RestoreNo").addEventListener("click", () => {
      bar.style.display = "none";
      clearSession();
    });
  }
})();
