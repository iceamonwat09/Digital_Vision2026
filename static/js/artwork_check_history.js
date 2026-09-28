/* Artwork Proof Check — history list + report detail.
 * Uses window.awApi / awEsc / awRenderReport / awRenderTextTable from
 * artwork_check.js.
 *
 * ประวัติการแปล (28 ก.ย. 2026): เมื่อ window.AW_HISTORY_TRANSLATE เปิด
 * ตารางมีคอลัมน์ "ประเภท" + "คำแปล" และแถวที่แปลอย่างเดียว (ยังไม่ส่งตรวจ)
 * ก็ขึ้นด้วย · คลิกแถวแล้วมีแท็บ [ผลตรวจ] [ข้อความ + คำแปล] · ปิดธง =
 * หน้าตาและพฤติกรรมเดิมทุกประการ (7 คอลัมน์ ไม่มีแท็บ) */
(function () {
  "use strict";
  const api = window.awApi, esc = window.awEsc;
  const TR = !!window.AW_HISTORY_TRANSLATE;
  const $ = (id) => document.getElementById(id);
  const body = $("awHistBody");
  const detailPanel = $("awDetailPanel");
  const detail = $("awDetail");
  const scopeNote = $("awScopeNote");
  const filterForm = $("awFilter");
  const filterCount = $("awFilterCount");
  const ownerWrap = $("awOwnerWrap");
  const tabs = $("awDetailTabs");
  const trDetail = $("awTrDetail");
  const trTable = $("awTrTable");
  const trSummary = $("awTrSummary");
  const trLog = $("awTrLog");
  const trOnlyIssues = $("awTrOnlyIssues");
  const COLS = TR ? 9 : 7;
  const LIMIT = 100;
  let trResult = null;       // ตารางคำแปลที่กำลังแสดง (กรองซ้ำโดยไม่โหลดใหม่)

  /* ป้ายบอกว่ารายการนี้คือของใคร — ผู้ใช้ต้องเข้าใจได้ทันทีว่าทำไมงานของ
     เพื่อนร่วมทีมไม่อยู่ในตาราง (scope มาจาก /api/artwork/history) */
  function renderScope(scope, username) {
    if (!scopeNote) return;
    if (scope === "own") {
      scopeNote.innerHTML = "👤 กำลังแสดง <b>เฉพาะการตรวจของคุณ</b>" +
        (username ? " (" + esc(username) + ")" : "") +
        " — งานของผู้ใช้อื่นและบันทึกเก่าที่ไม่มีเจ้าของจะไม่แสดงที่นี่";
    } else {
      scopeNote.innerHTML = "🗂️ กำลังแสดง <b>การตรวจทั้งหมดของทุกผู้ใช้</b>";
    }
    scopeNote.style.display = "";
  }

  /* ตัวเลือก "ผู้ตรวจ" — มีเฉพาะคนที่เห็นงานของหลายคน (scope=all). คงค่าที่
     เลือกไว้แม้รายชื่อถูกสร้างใหม่ทุกครั้งที่โหลด */
  function renderOwners(owners) {
    if (!ownerWrap) return;
    if (!owners || !owners.length) { ownerWrap.style.display = "none"; return; }
    const sel = ownerWrap.querySelector("select");
    const cur = sel.value;
    sel.innerHTML = '<option value="">ทุกคน</option>' + owners.map((o) =>
      '<option value="' + esc(o) + '">' + esc(o) + "</option>").join("");
    if (owners.indexOf(cur) >= 0) sel.value = cur;
    ownerWrap.style.display = "";
  }

  function badge(v) {
    if (!v) return '<span class="aw-owner">—</span>';   // แปลอย่างเดียว
    const cls = v === "PASS" ? "aw-b-pass"
      : v === "REVIEW" ? "aw-b-review" : "aw-b-fail";
    return '<span class="aw-badge ' + cls + '">' + esc(v) + "</span>";
  }

  const KIND_LABEL = { inspect: "ตรวจ", translate: "แปล", both: "ตรวจ + แปล" };
  function kindBadge(k) {
    return '<span class="aw-kind aw-k-' + esc(k || "inspect") + '">' +
      esc(KIND_LABEL[k] || "ตรวจ") + "</span>";
  }

  function trCell(t) {
    if (!t) return '<span class="aw-owner">—</span>';
    let h = esc(t.count) + " ครั้ง";
    if (t.error) {
      h += ' · <span class="err">ครั้งล่าสุดไม่สำเร็จ</span>';
    } else {
      h += " · " + esc(t.rows) + " บรรทัด";
      if (t.issues) h += ' · <span class="bad">น่าสงสัย ' + esc(t.issues) + "</span>";
      if (!t.translated) h += ' · <span class="bad">ยังไม่มีคำแปล</span>';
    }
    h += '<br><span class="aw-owner">ล่าสุด ' + esc(t.last_at) +
      (t.last_by ? " · " + esc(t.last_by) : "") + "</span>";
    return '<div class="aw-trcell">' + h + "</div>";
  }

  function filterQuery() {
    if (!filterForm) return "";
    const p = new URLSearchParams();
    new FormData(filterForm).forEach((v, k) => {
      v = String(v).trim();
      if (v) p.set(k, v);
    });
    return p.toString();
  }

  function row(r) {
    let h = '<tr class="clickable" data-id="' + esc(r.id) + '" data-kind="' +
      esc(r.kind || "inspect") + '">' +
      '<td class="c-date">' + esc(r.created_at) + "</td>" +
      '<td class="c-file">' + esc(r.filename || "—") +
      (r.cloned_from ? '<span class="aw-from">📋 จากต้นแบบ ' +
        esc(r.cloned_from.created_at || r.cloned_from.id) + "</span>" : "") +
      "</td>" +
      "<td>" + esc(r.brand || "—") + "</td>";
    if (TR) h += '<td class="c-nw">' + kindBadge(r.kind) + "</td>";
    h += '<td class="c-nw">' + badge(r.verdict) + "</td>" +
      '<td class="c-num">' + (r.defect_count == null ? '<span class="aw-owner">—</span>'
                                        : esc(r.defect_count)) + "</td>";
    if (TR) h += "<td>" + trCell(r.translate) + "</td>";
    h += '<td class="aw-owner">' + esc(r.owner || "—") + "</td>" +
      '<td><button class="aw-btn-danger" data-del="' + esc(r.id) + '">ลบ</button></td>' +
      "</tr>";
    return h;
  }

  async function loadList() {
    const q = filterQuery();
    try {
      const res = await api("/api/artwork/history?limit=" + LIMIT +
                            (q ? "&" + q : ""));
      const recs = res.records || [];
      renderScope(res.scope, res.username);
      renderOwners(res.owners);
      if (filterCount) {
        filterCount.textContent = q
          ? "พบ " + recs.length + " รายการตามตัวกรอง" +
            (recs.length >= LIMIT ? " (แสดง " + LIMIT + " รายการล่าสุด)" : "")
          : "";
      }
      if (!recs.length) {
        body.innerHTML = '<tr><td colspan="' + COLS + '" class="aw-empty">' +
          (q ? "ไม่พบรายการที่ตรงกับตัวกรอง"
             : res.scope === "own" ? "คุณยังไม่มีประวัติการตรวจ"
                                   : "ยังไม่มีประวัติการตรวจ") + "</td></tr>";
        return;
      }
      /* ทุกแถวที่แสดงอยู่ = แถวที่ผู้ใช้คนนี้มีสิทธิ์ลบอยู่แล้ว (รายการถูกกรอง
         มาจาก server) จึงไม่ต้องซ่อนปุ่มลบเป็นราย ๆ — และต่อให้ซ่อน ด่านจริง
         ก็อยู่ที่ server เสมอ */
      body.innerHTML = recs.map(row).join("");

      body.querySelectorAll("tr.clickable").forEach((tr) => {
        tr.addEventListener("click", (ev) => {
          if (ev.target.dataset.del) return;
          openDetail(tr.dataset.id, tr.dataset.kind);
        });
      });
      body.querySelectorAll("[data-del]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          if (!confirm("ลบบันทึกการตรวจนี้?")) return;
          try {
            await api("/api/artwork/" + btn.dataset.del, { method: "DELETE" });
            loadList();
            detailPanel.style.display = "none";
          } catch (e) { alert(e.message); }
        });
      });
    } catch (e) {
      body.innerHTML = '<tr><td colspan="' + COLS + '" class="aw-empty">โหลดไม่สำเร็จ: ' +
        esc(e.message) + "</td></tr>";
    }
  }

  // ── รายละเอียด ──────────────────────────────────────────────────────
  function switchTab(name) {
    if (!tabs) return;
    tabs.querySelectorAll(".aw-tab").forEach((b) =>
      b.classList.toggle("aw-tab-active", b.dataset.awtab === name));
    detail.style.display = name === "text" ? "none" : "";
    if (trDetail) trDetail.style.display = name === "text" ? "" : "none";
  }
  if (tabs) tabs.querySelectorAll(".aw-tab").forEach((b) =>
    b.addEventListener("click", () => switchTab(b.dataset.awtab)));

  function logTable(log, total) {
    if (!log || !log.length) return '<div class="aw-empty">ไม่มีบันทึก</div>';
    let h = '<table class="aw-trlog"><thead><tr><th>เวลา</th><th>ผู้กด</th>' +
      "<th>บรรทัด</th><th>น่าสงสัย</th><th>ผล</th><th>หมายเหตุ</th></tr></thead><tbody>";
    log.forEach((e) => {
      let res;
      if (e.error) res = "❌ ไม่สำเร็จ";
      else if (!e.translated) res = "⚠️ ไม่มีคำแปล";
      else res = e.cached ? "✓ แปลแล้ว (จากแคช)" : "✓ แปลแล้ว";
      const note = [e.ocr_only ? "ก่อนส่งตรวจ" : "", e.error || e.note || ""]
        .filter(Boolean).join(" · ");
      h += "<tr><td>" + esc(e.at) + "</td><td>" + esc(e.by || "—") + "</td>" +
        "<td>" + (e.error ? "—" : esc(e.rows)) + "</td>" +
        "<td>" + (e.error ? "—" : esc(e.issues)) + "</td>" +
        '<td class="' + (e.error ? "err" : "") + '">' + esc(res) + "</td>" +
        "<td>" + esc(note) + "</td></tr>";
    });
    h += "</tbody></table>";
    if (total > log.length)
      h += '<div class="aw-fcount">แสดง ' + log.length + " จาก " + total + " ครั้ง</div>";
    return h;
  }

  function renderTr(data) {
    trResult = data && data.last ? data.last.table : null;
    const last = data && data.last;
    if (last) {
      trSummary.textContent = "ครั้งล่าสุดที่มีตาราง: " + (last.at || "") +
        (last.by ? " · โดย " + last.by : "") +
        " · " + (last.rows || 0) + " บรรทัด · น่าสงสัย " + (last.issues || 0) +
        (last.ocr_only ? " · แปลก่อนส่งตรวจ (ยังไม่เทียบ panel)" : "");
      window.awRenderTextTable(trResult, trTable, trOnlyIssues.checked);
    } else {
      trSummary.textContent = "";
      trTable.innerHTML = '<div class="aw-empty">ยังไม่มีตารางคำแปลที่บันทึกไว้ ' +
        "(ทุกครั้งที่กดแปลล้มเหลว)</div>";
    }
    trLog.innerHTML = logTable(data && data.log, (data && data.count) || 0);
  }
  if (trOnlyIssues) trOnlyIssues.addEventListener("change", () => {
    if (trResult) window.awRenderTextTable(trResult, trTable, trOnlyIssues.checked);
  });

  let openId = null;
  const cloneBtn = $("awCloneBtn");
  if (cloneBtn) cloneBtn.addEventListener("click", () => {
    if (!openId) return;
    // หน้าตรวจเป็นคนสร้างงานใหม่จากต้นแบบ (แล้วลบ ?clone= ออกจาก URL เอง)
    location.href = "/artwork_check?clone=" + encodeURIComponent(openId);
  });

  async function openDetail(id, kind) {
    openId = id;
    const hasReport = kind !== "translate";
    const hasTr = TR && kind !== "inspect";
    try {
      const [rep, tr] = await Promise.all([
        hasReport ? api("/api/artwork/" + id + "/report") : null,
        hasTr ? api("/api/artwork/" + id + "/translations") : null,
      ]);
      detailPanel.style.display = "";
      if (rep) window.awRenderReport(rep, detail);
      else detail.innerHTML = '<div class="aw-empty">งานนี้ยังไม่ได้กด ' +
        "“ส่งตรวจสอบ” — มีเฉพาะผลแปล</div>";
      if (tr) renderTr(tr);
      if (tabs) tabs.style.display = hasTr ? "" : "none";
      switchTab(hasReport ? "result" : "text");
      detailPanel.scrollIntoView({ behavior: "smooth" });
    } catch (e) { alert(e.message); }
  }

  if (filterForm) {
    filterForm.addEventListener("submit", (ev) => { ev.preventDefault(); loadList(); });
    $("awFilterReset").addEventListener("click", () => {
      filterForm.reset();
      loadList();
    });
  }
  loadList();
})();
