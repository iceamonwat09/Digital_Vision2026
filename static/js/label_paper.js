/* Label Paper Inspection — front-end controller.
 * Handles SKU dropdown, file → Cropper.js → POST /api/label_paper/inspect.
 * Independent from the YOLO live-detection page; nothing here touches /api/detection/*.
 */
(function () {
  'use strict';

  const fileInput  = document.getElementById('fileInput');
  const cropBox    = document.getElementById('cropBox');
  const inspectBtn = document.getElementById('inspectBtn');
  const rotateBtn  = document.getElementById('rotateBtn');
  const resetBtn   = document.getElementById('resetBtn');
  const skuSelect  = document.getElementById('skuSelect');
  const resultArea = document.getElementById('resultArea');

  let cropper = null;

  // ── Load SKU list ────────────────────────────────────────────────
  fetch('/api/label_paper/skus')
    .then(r => r.json())
    .then(data => {
      skuSelect.innerHTML = '';
      const skus = (data && data.skus) || [];
      if (skus.length === 0) {
        skuSelect.innerHTML = '<option value="">— ยังไม่มี SKU (เพิ่ม spec.json ที่ data/label_paper/skus/) —</option>';
        return;
      }
      const blank = document.createElement('option');
      blank.value = '';
      blank.textContent = '— เลือก SKU —';
      skuSelect.appendChild(blank);
      skus.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s.sku_code;
        opt.textContent = `${s.sku_code} — ${s.display_name}` +
                          (s.has_master_pdf ? '' : '  (ยังไม่มี master PDF)');
        skuSelect.appendChild(opt);
      });
    })
    .catch(err => {
      skuSelect.innerHTML = `<option value="">โหลด SKU ไม่สำเร็จ: ${err}</option>`;
    });

  // ── File picker → Cropper ────────────────────────────────────────
  fileInput.addEventListener('change', (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;

    cropBox.innerHTML = '';
    const img = document.createElement('img');
    img.id = 'cropImg';
    img.src = URL.createObjectURL(file);
    img.style.maxWidth = '100%';
    img.style.display = 'block';
    cropBox.appendChild(img);

    if (cropper) { cropper.destroy(); cropper = null; }
    cropper = new Cropper(img, {
      viewMode: 1,
      autoCropArea: 0.85,
      background: false,
      movable: true,
      zoomable: true,
      rotatable: true,
    });

    inspectBtn.disabled = false;
    rotateBtn.disabled  = false;
    resetBtn.disabled   = false;
  });

  rotateBtn.addEventListener('click', () => { if (cropper) cropper.rotate(90); });
  resetBtn.addEventListener('click',  () => { if (cropper) cropper.reset();    });

  // ── Inspect ─────────────────────────────────────────────────────
  inspectBtn.addEventListener('click', () => {
    if (!cropper) return;
    const sku = skuSelect.value;
    if (!sku) { alert('กรุณาเลือก SKU ก่อน'); return; }

    inspectBtn.disabled = true;
    inspectBtn.textContent = 'กำลังตรวจสอบ...';
    resultArea.innerHTML = '<div class="lp-empty">กำลังส่งภาพและประมวลผล...</div>';

    cropper.getCroppedCanvas({ maxWidth: 2048, maxHeight: 2048 })
      .toBlob(async (blob) => {
        if (!blob) {
          resultArea.innerHTML = '<div class="lp-empty">ครอปภาพไม่สำเร็จ</div>';
          finish();
          return;
        }
        const fd = new FormData();
        fd.append('sku_code', sku);
        fd.append('image', blob, 'crop.jpg');
        try {
          const res = await fetch('/api/label_paper/inspect', { method: 'POST', body: fd });
          const json = await res.json();
          if (!res.ok) {
            resultArea.innerHTML = `<div class="lp-verdict lp-v-fail">${escapeHtml(json.error || 'error')}</div>`;
          } else {
            resultArea.innerHTML = renderReport(json);
          }
        } catch (err) {
          resultArea.innerHTML = `<div class="lp-verdict lp-v-fail">network error: ${escapeHtml(String(err))}</div>`;
        } finally {
          finish();
        }
      }, 'image/jpeg', 0.92);
  });

  function finish() {
    inspectBtn.disabled = false;
    inspectBtn.textContent = 'ส่งตรวจสอบ →';
  }

  // ── Render report ───────────────────────────────────────────────
  function severityTag(sev) {
    const cls = ({
      ok:'lp-tag-ok', minor:'lp-tag-minor',
      warning:'lp-tag-warning', critical:'lp-tag-critical',
    })[sev] || 'lp-tag-minor';
    return `<span class="lp-tag ${cls}">${sev}</span>`;
  }

  function renderReport(r) {
    const vClass = r.verdict === 'PASS' ? 'lp-v-pass'
                 : r.verdict === 'WARN' ? 'lp-v-warn'
                                        : 'lp-v-fail';
    let html = `<div class="lp-verdict ${vClass}">ผลรวม: ${r.verdict}</div>`;

    if (r.stub_mode) {
      html += `<div class="lp-stub">⚠️ OCR ส่วนตัวอักษรกำลังทำงานใน <b>STUB MODE</b> — Vertex Document AI ยังไม่ถูกเรียก<br>
              ตั้งค่า env <code>VERTEX_ENABLED=true</code> และใส่ credentials เพื่อใช้งานจริง (ส่วนตรวจสี/pixel ทำงานเต็มแล้ว)</div>`;
    }

    html += renderPixelInspection(r.pixel_inspection || {});

    html += `<h4 style="margin:14px 0 6px;">ผลตรวจตัวอักษร (Field-aware)</h4>`;
    if (r.field_results && r.field_results.length) {
      html += '<table class="lp-tbl"><tr><th>Field</th><th>คาดหวัง</th><th>พบ</th><th>Δ</th><th>วิธี</th><th>สถานะ</th></tr>';
      for (const f of r.field_results) {
        html += `<tr>
          <td><b>${escapeHtml(f.name)}</b>${f.critical ? ' <span style="color:#c62828;font-size:11px;">[critical]</span>' : ''}</td>
          <td>${escapeHtml(f.expected)}</td>
          <td>${escapeHtml(f.found || '—')}</td>
          <td>${f.distance}</td>
          <td>${escapeHtml(f.method)}</td>
          <td>${severityTag(f.severity)}</td>
        </tr>`;
      }
      html += '</table>';
    } else {
      html += '<div class="lp-empty" style="padding:14px;">spec.json ของ SKU นี้ไม่มี field</div>';
    }

    html += `<h4 style="margin:18px 0 6px;">ผลตรวจสี (ΔE)</h4>`;
    if (r.color_results && r.color_results.length) {
      html += '<table class="lp-tbl"><tr><th>สี</th><th>Master</th><th>พบ</th><th>ΔE</th><th>tol</th><th>สถานะ</th></tr>';
      for (const c of r.color_results) {
        const sev = c.passed ? 'ok'
                  : (c.delta_e > c.tolerance * 2 ? 'critical' : 'warning');
        html += `<tr>
          <td>${escapeHtml(c.name)}</td>
          <td><span class="lp-swatch" style="background:${escapeAttr(c.expected_hex)}"></span>${escapeHtml(c.expected_hex)}</td>
          <td><span class="lp-swatch" style="background:${escapeAttr(c.found_hex)}"></span>${escapeHtml(c.found_hex)}</td>
          <td>${c.delta_e}</td>
          <td>${c.tolerance}</td>
          <td>${severityTag(sev)}</td>
        </tr>`;
      }
      html += '</table>';
    } else {
      html += '<div class="lp-empty" style="padding:14px;">spec.json ของ SKU นี้ไม่มีสีต้นแบบ</div>';
    }

    if (r.gemini && r.gemini.verdict && r.gemini.verdict !== 'not_needed') {
      html += `<h4 style="margin:18px 0 6px;">Gemini context</h4>
               <div style="font-size:13px;color:#37474f;">${escapeHtml(JSON.stringify(r.gemini))}</div>`;
    }

    if (r.ocr_text) {
      html += `<details>
        <summary>OCR text (raw)</summary>
        <pre class="lp-pre">${escapeHtml(r.ocr_text)}</pre>
      </details>`;
    }
    return html;
  }

  function renderPixelInspection(p) {
    if (!p || Object.keys(p).length === 0) return '';

    let html = `<h4 style="margin:14px 0 6px;">ผลตรวจระดับ pixel (ΔE2000 map)</h4>`;

    if (!p.enabled) {
      const reason = p.note || p.verdict || 'ไม่ได้เปิดใช้งาน';
      return html + `<div class="lp-empty" style="padding:14px;">${escapeHtml(reason)}</div>`;
    }

    const pvClass = p.verdict === 'PASS' ? 'lp-v-pass'
                  : p.verdict === 'WARN' ? 'lp-v-warn'
                                         : 'lp-v-fail';
    const passRate = (typeof p.pass_rate === 'number') ? p.pass_rate.toFixed(2) : p.pass_rate;
    const peak = (typeof p.de_peak === 'number') ? p.de_peak.toFixed(1) : p.de_peak;
    const mean = (typeof p.de_mean === 'number') ? p.de_mean.toFixed(2) : p.de_mean;
    const p95  = (typeof p.de_p95  === 'number') ? p.de_p95.toFixed(1)  : p.de_p95;
    const p99  = (typeof p.de_p99  === 'number') ? p.de_p99.toFixed(1)  : p.de_p99;

    html += `
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px;">
        <span class="lp-verdict ${pvClass}" style="font-size:14px;padding:4px 10px;">pixel: ${escapeHtml(p.verdict)}</span>
        <span style="font-size:13px;color:#37474f;">
          pass-rate <b>${passRate}%</b> &nbsp;|&nbsp;
          peak ΔE <b>${peak}</b> &nbsp;|&nbsp;
          mean ΔE <b>${mean}</b> &nbsp;|&nbsp;
          p95 <b>${p95}</b> &nbsp;|&nbsp; p99 <b>${p99}</b>
          &nbsp;|&nbsp; tol ${p.tolerance}
        </span>
      </div>`;

    if (p.align_info && p.align_info.ok === false) {
      html += `<div class="lp-stub">⚠️ ECC alignment ไม่ converge — ผลอาจเพี้ยน ลองครอปให้แนบขอบฉลากให้แม่นขึ้นหรือเพิ่มแสง</div>`;
    }

    if (p.heatmap_png_b64) {
      html += `<img src="data:image/png;base64,${p.heatmap_png_b64}"
                   style="max-width:100%;border:1px solid #cfd8dc;border-radius:6px;display:block;margin:6px 0;">`;
    }

    if (p.defects && p.defects.length) {
      html += `<table class="lp-tbl"><tr>
        <th>#</th><th>ตำแหน่ง (x,y,w,h)</th><th>พื้นที่ (px)</th>
        <th>peak ΔE</th><th>mean ΔE</th><th>Master → พบ</th><th>สถานะ</th></tr>`;
      p.defects.forEach((d, i) => {
        html += `<tr>
          <td>${i + 1}</td>
          <td>${d.bbox.join(', ')}</td>
          <td>${d.area_px}</td>
          <td><b>${d.peak_de}</b></td>
          <td>${d.mean_de}</td>
          <td>
            <span class="lp-swatch" style="background:${escapeAttr(d.master_hex)}"></span>${escapeHtml(d.master_hex)}
            &nbsp;→&nbsp;
            <span class="lp-swatch" style="background:${escapeAttr(d.found_hex)}"></span>${escapeHtml(d.found_hex)}
          </td>
          <td>${severityTag(d.severity)}</td>
        </tr>`;
      });
      html += '</table>';
    } else {
      html += `<div class="lp-empty" style="padding:10px;color:#2e7d32;">
                ✓ ไม่พบจุดที่ ΔE เกิน tolerance (${p.tolerance}) — สีตรง master ทั้งภาพ</div>`;
    }
    return html;
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, ch => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[ch]));
  }
  function escapeAttr(s) {
    return escapeHtml(s).replace(/`/g, '');
  }
})();
