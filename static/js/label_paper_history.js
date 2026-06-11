/* Label Paper inspection history page.
 * Lists saved inspections (newest first); clicking one loads the stored
 * report and renders it with the shared window.LabelReport renderer.
 */
(function () {
  'use strict';

  const listEl = document.getElementById('histList');
  const detailEl = document.getElementById('histDetail');
  const esc = window.LabelReport.escapeHtml;

  fetch('/api/label_paper/history?limit=200')
    .then(r => r.json())
    .then(data => {
      const records = (data && data.records) || [];
      if (!records.length) {
        listEl.innerHTML = '<div class="lp-empty">ยังไม่มีประวัติการตรวจ</div>';
        return;
      }
      listEl.innerHTML = '';
      records.forEach(rec => listEl.appendChild(itemEl(rec)));
    })
    .catch(err => {
      listEl.innerHTML = `<div class="lp-empty">โหลดประวัติไม่สำเร็จ: ${esc(String(err))}</div>`;
    });

  function itemEl(rec) {
    const s = rec.summary || {};
    const f = s.fields || {}, p = s.pixels || {};
    const div = document.createElement('div');
    div.className = 'lp-hist-item';
    const when = (rec.saved_at || '').replace('T', ' ');
    const thumb = rec.has_crop
      ? `<img class="lp-hist-thumb" src="/api/label_paper/history/${encodeURIComponent(rec.id)}/crop" alt="">`
      : `<div class="lp-hist-thumb"></div>`;
    div.innerHTML = `${thumb}
      <div class="lp-hist-meta">
        <div><b>${esc(rec.sku_code || '?')}</b>
             <span class="lp-vbadge ${esc(rec.verdict)}">${esc(rec.verdict || '?')}</span></div>
        <div>${esc(when)}</div>
        <div>field ผิด ${f.failed || 0}/${f.total || 0}
             ${f.critical ? `· <span style="color:#c62828;">${f.critical} critical</span>` : ''}
             ${p.enabled ? `· pixel ${esc(p.verdict || '')}` : ''}</div>
      </div>`;
    div.addEventListener('click', () => {
      document.querySelectorAll('.lp-hist-item.active')
        .forEach(e => e.classList.remove('active'));
      div.classList.add('active');
      openDetail(rec.id);
    });
    return div;
  }

  function openDetail(id) {
    detailEl.innerHTML = '<div class="lp-empty">กำลังโหลดรายละเอียด...</div>';
    fetch(`/api/label_paper/history/${encodeURIComponent(id)}`)
      .then(r => r.json())
      .then(rep => {
        if (rep && !rep.error) {
          window.LabelReport.render(detailEl, rep);
        } else {
          detailEl.innerHTML = `<div class="lp-verdict lp-v-fail">${esc((rep && rep.error) || 'error')}</div>`;
        }
      })
      .catch(err => {
        detailEl.innerHTML = `<div class="lp-verdict lp-v-fail">network error: ${esc(String(err))}</div>`;
      });
  }
})();
