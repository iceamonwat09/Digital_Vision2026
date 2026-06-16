/* Artwork Proof Check — history list + report detail.
 * Uses window.awApi / awEsc / awRenderReport from artwork_check.js. */
(function () {
  "use strict";
  const api = window.awApi, esc = window.awEsc;
  const body = document.getElementById("awHistBody");
  const detailPanel = document.getElementById("awDetailPanel");
  const detail = document.getElementById("awDetail");

  function badge(v) {
    const cls = v === "PASS" ? "aw-b-pass"
      : v === "REVIEW" ? "aw-b-review" : "aw-b-fail";
    return '<span class="aw-badge ' + cls + '">' + esc(v || "?") + "</span>";
  }

  async function loadList() {
    try {
      const res = await api("/api/artwork/history?limit=100");
      const recs = res.records || [];
      if (!recs.length) {
        body.innerHTML = '<tr><td colspan="6" class="aw-empty">ยังไม่มีประวัติการตรวจ</td></tr>';
        return;
      }
      body.innerHTML = recs.map((r) =>
        '<tr class="clickable" data-id="' + esc(r.id) + '">' +
        "<td>" + esc(r.created_at) + "</td>" +
        "<td>" + esc(r.filename) + "</td>" +
        "<td>" + esc(r.brand || "—") + "</td>" +
        "<td>" + badge(r.verdict) + "</td>" +
        "<td>" + esc(r.defect_count) + "</td>" +
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
      body.innerHTML = '<tr><td colspan="6" class="aw-empty">โหลดไม่สำเร็จ: ' +
        esc(e.message) + "</td></tr>";
    }
  }
  loadList();
})();
