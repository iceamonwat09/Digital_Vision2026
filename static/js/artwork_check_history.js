/* Artwork Proof Check — history list + report detail.
 * Uses window.awApi / awEsc / awRenderReport from artwork_check.js. */
(function () {
  "use strict";
  const api = window.awApi, esc = window.awEsc;
  const body = document.getElementById("awHistBody");
  const detailPanel = document.getElementById("awDetailPanel");
  const detail = document.getElementById("awDetail");
  const scopeNote = document.getElementById("awScopeNote");
  const COLS = 7;

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

  function badge(v) {
    const cls = v === "PASS" ? "aw-b-pass"
      : v === "REVIEW" ? "aw-b-review" : "aw-b-fail";
    return '<span class="aw-badge ' + cls + '">' + esc(v || "?") + "</span>";
  }

  async function loadList() {
    try {
      const res = await api("/api/artwork/history?limit=100");
      const recs = res.records || [];
      renderScope(res.scope, res.username);
      if (!recs.length) {
        body.innerHTML = '<tr><td colspan="' + COLS + '" class="aw-empty">' +
          (res.scope === "own" ? "คุณยังไม่มีประวัติการตรวจ"
                               : "ยังไม่มีประวัติการตรวจ") + "</td></tr>";
        return;
      }
      /* ทุกแถวที่แสดงอยู่ = แถวที่ผู้ใช้คนนี้มีสิทธิ์ลบอยู่แล้ว (รายการถูกกรอง
         มาจาก server) จึงไม่ต้องซ่อนปุ่มลบเป็นราย ๆ — และต่อให้ซ่อน ด่านจริง
         ก็อยู่ที่ server เสมอ */
      body.innerHTML = recs.map((r) =>
        '<tr class="clickable" data-id="' + esc(r.id) + '">' +
        "<td>" + esc(r.created_at) + "</td>" +
        "<td>" + esc(r.filename) + "</td>" +
        "<td>" + esc(r.brand || "—") + "</td>" +
        "<td>" + badge(r.verdict) + "</td>" +
        "<td>" + esc(r.defect_count) + "</td>" +
        '<td class="aw-owner">' + esc(r.owner || "—") + "</td>" +
        '<td><button class="aw-btn-danger" data-del="' + esc(r.id) + '">ลบ</button></td>' +
        "</tr>").join("");

      body.querySelectorAll("tr.clickable").forEach((tr) => {
        tr.addEventListener("click", async (ev) => {
          if (ev.target.dataset.del) return;
          try {
            const rep = await api("/api/artwork/" + tr.dataset.id + "/report");
            detailPanel.style.display = "";
            window.awRenderReport(rep, detail);
            detailPanel.scrollIntoView({ behavior: "smooth" });
          } catch (e) { alert(e.message); }
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
  loadList();
})();
