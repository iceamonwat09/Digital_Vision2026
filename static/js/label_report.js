/* Shared inspection-report renderer for Label Paper.
 * Used by both the live inspect page (label_paper.js) and the history page
 * (label_paper_history.js). Exposes:
 *
 *     window.LabelReport.render(container, report)
 *
 * which injects the report HTML into `container` and wires up the interactive
 * bits (the "show only failed" filter and the master/captured compare slider).
 *
 * UI improvements over the original inline renderer:
 *   - failed / critical fields sorted to the top + a "show only failed" toggle
 *   - decoded-barcode panel (authoritative barcode value)
 *   - master ↔ captured compare slider (drag to wipe between the two)
 *   - glare / edge mask coverage surfaced with a high-glare warning
 */
(function () {
  'use strict';

  const SEV_RANK = { critical: 3, warning: 2, minor: 1, ok: 0, '': 0 };

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
  }
  function escapeAttr(s) { return escapeHtml(s).replace(/`/g, ''); }

  function severityTag(sev) {
    const cls = ({
      ok: 'lp-tag-ok', minor: 'lp-tag-minor',
      warning: 'lp-tag-warning', critical: 'lp-tag-critical',
    })[sev] || 'lp-tag-minor';
    return `<span class="lp-tag ${cls}">${sev}</span>`;
  }

  // ── Top-level builder ────────────────────────────────────────────
  function buildHtml(r) {
    const vClass = r.verdict === 'PASS' ? 'lp-v-pass'
                 : r.verdict === 'WARN' ? 'lp-v-warn'
                                        : 'lp-v-fail';
    let html = `<div class="lp-verdict ${vClass}">ผลรวม: ${escapeHtml(r.verdict)}</div>`;
    html += renderSummary(r);
    html += renderCompare(r.pixel_inspection || {});
    html += renderBarcodes(r.barcodes || []);
    html += renderVisualDiff(r.visual_diff || {});

    if (r.stub_mode) {
      const engine = r.ocr_engine || 'stub';
      const reason = r.ocr_error
        ? `เหตุผล: <code>${escapeHtml(r.ocr_error)}</code>`
        : `ตั้งค่า env <code>OCR_BACKEND=n8n</code> และ <code>N8N_OCR_WEBHOOK_URL</code> เพื่อใช้ OCR จริง`;
      html += `<div class="lp-stub">⚠️ OCR กำลังทำงานใน <b>STUB MODE</b> (backend: <code>${escapeHtml(engine)}</code>)<br>${reason}<br>
              ผลตรวจตัวอักษรด้านล่างจะถูกข้าม — ส่วน <b>สี / pixel</b> ทำงานเต็มปกติ</div>`;
    }

    html += renderTextLineDiff(r);
    html += renderFields(r);
    html += renderPixelInspection(r.pixel_inspection || {});
    html += renderColors(r);

    if (r.gemini && r.gemini.verdict && r.gemini.verdict !== 'not_needed') {
      html += `<h4 style="margin:18px 0 6px;">Gemini context</h4>
               <div style="font-size:13px;color:#37474f;">${escapeHtml(JSON.stringify(r.gemini))}</div>`;
    }

    if (r.ocr_text || r.master_text) {
      html += `<details>
        <summary>ข้อความดิบ (Master text layer + OCR)</summary>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:6px;">
          <div><div style="font-size:11px;color:#546e7a;margin-bottom:2px;">MASTER</div>
               <pre class="lp-pre">${escapeHtml(r.master_text || '(ไม่มี text layer ใน master.pdf)')}</pre></div>
          <div><div style="font-size:11px;color:#546e7a;margin-bottom:2px;">OCR</div>
               <pre class="lp-pre">${escapeHtml(r.ocr_text || '(ว่าง)')}</pre></div>
        </div>
      </details>`;
    }
    return html;
  }

  // ── Summary cards ────────────────────────────────────────────────
  function renderSummary(r) {
    const s = r.summary || {};
    const f = s.fields || {}, c = s.colors || {}, p = s.pixels || {};
    function card(label, value, sub, kind) {
      const cls = kind ? ` ${kind}` : '';
      return `<div class="lp-sumcard${cls}"><span>${escapeHtml(label)}</span>
              <b>${escapeHtml(String(value))}</b>
              ${sub ? `<span style="font-size:11px;color:#78909c;">${escapeHtml(sub)}</span>` : ''}</div>`;
    }
    let cards = '';
    const fFail = f.failed || 0, fCrit = f.critical || 0, fTot = f.total || 0;
    const fKind = fCrit > 0 ? 'bad' : (fFail > 0 ? 'warn' : (fTot > 0 ? 'ok' : ''));
    cards += card('Field ผิด', `${fFail} / ${fTot}`,
                  fCrit > 0 ? `${fCrit} critical` : (fFail > 0 ? 'ไม่ critical' : 'ตรงทั้งหมด'), fKind);
    const cFail = c.failed || 0, cTot = c.total || 0;
    const cKind = cFail > 0 ? 'warn' : (cTot > 0 ? 'ok' : '');
    cards += card('สีผิด tol', `${cFail} / ${cTot}`,
                  cTot === 0 ? 'spec ไม่มีสี' : (cFail > 0 ? 'เกิน ΔE tolerance' : 'อยู่ใน tolerance'), cKind);
    if (p.enabled) {
      const pn = p.defect_count || 0, pa = p.defect_area || 0;
      const pKind = (p.verdict === 'FAIL') ? 'bad' : (p.verdict === 'WARN' ? 'warn' : 'ok');
      cards += card('Pixel ผิด', `${pn} จุด`, pa > 0 ? `รวม ${pa.toLocaleString()} px` : 'ไม่พบ', pKind);
    } else {
      cards += card('Pixel', '— ปิด —', 'pixel_inspection.enabled=false', '');
    }
    const v = s.visual_diff || {};
    if (v.enabled) {
      const vn = v.count || 0, vc = v.critical || 0;
      const vKind = vc > 0 ? 'bad' : (vn > 0 ? 'warn' : 'ok');
      cards += card('AI พบความต่าง', `${vn} รายการ`,
                    vc > 0 ? `${vc} critical` : (vn > 0 ? 'ไม่ critical' : 'ไม่พบ'), vKind);
    } else {
      cards += card('AI Visual Diff', '— ปิด —', 'ตั้ง VISUAL_DIFF_ENABLED=1 + N8N URL', '');
    }
    return `<div class="lp-summary">${cards}</div>`;
  }

  // ── Master ↔ captured compare slider ─────────────────────────────
  function renderCompare(p) {
    if (!p || !p.master_png_b64 || !p.aligned_png_b64) return '';
    const m = `data:image/jpeg;base64,${p.master_png_b64}`;
    const a = `data:image/jpeg;base64,${p.aligned_png_b64}`;
    return `<h4 style="margin:14px 0 6px;">เทียบภาพ Master ↔ ที่ถ่าย (เลื่อนแถบ)</h4>
      <div class="lp-cmp">
        <img class="lp-cmp-base" src="${a}" alt="captured">
        <div class="lp-cmp-top"><img src="${m}" alt="master"></div>
        <div class="lp-cmp-divider"></div>
        <input class="lp-cmp-range" type="range" min="0" max="100" value="50" aria-label="compare">
        <span class="lp-cmp-lbl lp-cmp-lbl-l">Master</span>
        <span class="lp-cmp-lbl lp-cmp-lbl-r">ถ่ายจริง</span>
      </div>`;
  }

  // ── Decoded barcodes ─────────────────────────────────────────────
  function renderBarcodes(barcodes) {
    if (!barcodes || !barcodes.length) return '';
    const rows = barcodes.map(b => `
      <li class="lp-vd-item minor">
        <span class="lp-vd-type">${escapeHtml(b.type || 'barcode')}</span>
        <b style="font-family:ui-monospace,monospace;">${escapeHtml(b.data || '')}</b>
        <span style="font-size:11px;color:#90a4ae;">&nbsp;decode: ${escapeHtml(b.engine || '')}</span>
      </li>`).join('');
    return `<div class="lp-vd-box">
      <div class="lp-vd-head"><h4>▮▮▮ บาร์โค้ดที่อ่านได้ (decode จากแท่ง)</h4>
        <span class="lp-vd-summary">สแกนได้ ${barcodes.length} รายการ — ใช้เป็นค่าจริงแทน OCR</span></div>
      <ul class="lp-vd-list">${rows}</ul></div>`;
  }

  // ── Field table (sorted, filterable) ─────────────────────────────
  function renderFields(r) {
    let html = `<div style="display:flex;align-items:center;gap:10px;margin:14px 0 6px;flex-wrap:wrap;">
        <h4 style="margin:0;">ผลตรวจตัวอักษรแยกตาม Field</h4>
        <label style="font-size:13px;color:#37474f;cursor:pointer;">
          <input type="checkbox" class="lp-only-failed"> แสดงเฉพาะที่ผิด</label></div>`;
    const fields = (r.field_results || []).slice().sort(
      (a, b) => (SEV_RANK[b.severity] || 0) - (SEV_RANK[a.severity] || 0));
    if (!fields.length) {
      return html + '<div class="lp-empty" style="padding:14px;">spec.json ของ SKU นี้ไม่มี field</div>';
    }
    html += '<table class="lp-tbl lp-fields"><tr><th>Field</th><th>Spec (คาดหวัง)</th><th>Master (OCR)</th><th>พบใน OCR</th><th>ความต่าง</th><th>Δ</th><th>สถานะ</th></tr>';
    for (const f of fields) {
      const mf = f.master_found || '';
      const mfStyle = !mf ? 'color:#b0bec5'
                    : (mf === f.expected ? 'color:#2e7d32' : 'color:#e65100');
      const mfCell = mf ? `<span style="${mfStyle}">${escapeHtml(mf)}</span>`
                        : '<span style="color:#b0bec5;">—</span>';
      html += `<tr data-passed="${f.passed ? '1' : '0'}">
        <td><b>${escapeHtml(f.name)}</b>${f.critical ? ' <span style="color:#c62828;font-size:11px;">[critical]</span>' : ''}<br><span style="color:#90a4ae;font-size:11px;">${escapeHtml(f.method)}</span></td>
        <td>${escapeHtml(f.expected)}</td>
        <td>${mfCell}</td>
        <td>${escapeHtml(f.found || '—')}</td>
        <td>${renderCharDiff(f)}</td>
        <td>${f.distance}</td>
        <td>${severityTag(f.severity)}</td>
      </tr>`;
    }
    return html + '</table>';
  }

  function renderColors(r) {
    let html = `<h4 style="margin:18px 0 6px;">ผลตรวจสี (ΔE)</h4>`;
    if (!(r.color_results && r.color_results.length)) {
      return html + '<div class="lp-empty" style="padding:14px;">spec.json ของ SKU นี้ไม่มีสีต้นแบบ</div>';
    }
    html += '<table class="lp-tbl"><tr><th>สี</th><th>Master</th><th>พบ</th><th>ΔE</th><th>tol</th><th>สถานะ</th></tr>';
    for (const c of r.color_results) {
      const sev = c.passed ? 'ok' : (c.delta_e > c.tolerance * 2 ? 'critical' : 'warning');
      html += `<tr>
        <td>${escapeHtml(c.name)}</td>
        <td><span class="lp-swatch" style="background:${escapeAttr(c.expected_hex)}"></span>${escapeHtml(c.expected_hex)}</td>
        <td><span class="lp-swatch" style="background:${escapeAttr(c.found_hex)}"></span>${escapeHtml(c.found_hex)}</td>
        <td>${c.delta_e}</td><td>${c.tolerance}</td><td>${severityTag(sev)}</td>
      </tr>`;
    }
    return html + '</table>';
  }

  function renderVisualDiff(vd) {
    if (!vd || (vd.stub && !vd.error)) return '';
    if (vd.stub) {
      return `<div class="lp-vd-box"><div class="lp-vd-head">
          <h4>🔍 AI Visual Diff (Master ↔ Captured)</h4>
          <span class="lp-tag lp-tag-minor">SKIPPED</span></div>
        <div class="lp-vd-summary">${escapeHtml(vd.error || 'ไม่ได้ทำงาน')}</div></div>`;
    }
    const diffs = vd.differences || [];
    const engine = vd.engine || 'gemini';
    const summary = vd.summary || (diffs.length === 0 ? '✓ ไม่พบความต่างของตัวอักษร' : `พบ ${diffs.length} รายการ`);
    const tagSeverity = (() => {
      if (!diffs.length) return 'ok';
      if (diffs.some(d => d.severity === 'critical')) return 'critical';
      if (diffs.some(d => d.severity === 'warning')) return 'warning';
      return 'minor';
    })();
    let body = '';
    if (diffs.length) {
      body = '<ul class="lp-vd-list">' + diffs.map(d => {
        const sev = (d.severity || 'warning').toLowerCase();
        const klass = (sev === 'critical' || sev === 'warning' || sev === 'minor') ? sev : 'warning';
        const loc = d.location_hint ? `<div class="lp-vd-loc">📍 ${escapeHtml(d.location_hint)}</div>` : '';
        return `<li class="lp-vd-item ${klass}">
          <span class="lp-vd-type">${escapeHtml(d.type || 'diff')}</span>${severityTag(klass)}
          <div style="margin-top:4px;">
            <span style="color:#546e7a;">Master:</span> <span class="lp-d-rep-a">${escapeHtml(d.master_text || '—')}</span>
            &nbsp;→&nbsp;<span style="color:#546e7a;">พบ:</span> <span class="lp-d-rep-b">${escapeHtml(d.captured_text || '—')}</span>
          </div>${loc}</li>`;
      }).join('') + '</ul>';
    }
    return `<div class="lp-vd-box"><div class="lp-vd-head">
        <h4>🔍 AI Visual Diff (Master ↔ Captured)</h4>${severityTag(tagSeverity)}
        <span class="lp-vd-summary">${escapeHtml(summary)}</span>
        <span style="font-size:11px;color:#90a4ae;">${escapeHtml(engine)}</span></div>${body}</div>`;
  }

  function renderCharDiff(f) {
    if (f.passed) return '<span style="color:#2e7d32;">✓ ตรง</span>';
    const ops = f.diff || [];
    if (!ops.length) {
      return f.found ? `<span class="lp-d-rep-b">${escapeHtml(f.found)}</span>`
                     : '<span style="color:#90a4ae;">(ไม่พบในภาพ)</span>';
    }
    return ops.map(op => {
      switch (op.op) {
        case 'equal':   return `<span class="lp-d-equal">${escapeHtml(op.text)}</span>`;
        case 'delete':  return `<span class="lp-d-del" title="ขาดหายจาก OCR">${escapeHtml(op.text)}</span>`;
        case 'insert':  return `<span class="lp-d-ins" title="เกินมาใน OCR">${escapeHtml(op.text)}</span>`;
        case 'replace': return `<span class="lp-d-rep-a" title="Master">${escapeHtml(op.a)}</span>` +
                               `<span class="lp-d-rep-b" title="OCR">${escapeHtml(op.b)}</span>`;
        default: return '';
      }
    }).join('');
  }

  function renderTextLineDiff(r) {
    const ops = r.text_line_diff || [];
    if (!ops.length) return '';
    let left = '', right = '';
    for (const op of ops) {
      if (op.op === 'equal') {
        left += `<div class="lp-diff-line equal">${escapeHtml(op.text)}</div>`;
        right += `<div class="lp-diff-line equal">${escapeHtml(op.text)}</div>`;
      } else if (op.op === 'delete') {
        left += `<div class="lp-diff-line del">− ${escapeHtml(op.text)}</div>`;
        right += `<div class="lp-diff-line gap">·</div>`;
      } else if (op.op === 'insert') {
        left += `<div class="lp-diff-line gap">·</div>`;
        right += `<div class="lp-diff-line ins">+ ${escapeHtml(op.text)}</div>`;
      } else if (op.op === 'replace') {
        left += `<div class="lp-diff-line rep">~ ${escapeHtml(op.a)}</div>`;
        right += `<div class="lp-diff-line rep">~ ${escapeHtml(op.b)}</div>`;
      }
    }
    return `<h4 style="margin:14px 0 6px;">เปรียบเทียบข้อความ Master ↔ OCR</h4>
            <div style="font-size:12px;color:#546e7a;margin-bottom:4px;">
              <span class="lp-d-del">แดง</span> = เฉพาะใน Master &nbsp;
              <span class="lp-d-ins">เขียว</span> = เฉพาะใน OCR &nbsp;
              <span class="lp-d-rep-b">เหลือง</span> = ทั้งคู่แต่ต่าง</div>
            <div class="lp-diff-grid">
              <div class="lp-diff-col"><h5>Master (PDF text layer)</h5>${left || '<div class="lp-diff-line gap">(ว่าง)</div>'}</div>
              <div class="lp-diff-col"><h5>OCR (ภาพถ่าย)</h5>${right || '<div class="lp-diff-line gap">(ว่าง)</div>'}</div>
            </div>`;
  }

  function renderPixelInspection(p) {
    if (!p || Object.keys(p).length === 0) return '';
    let html = `<h4 style="margin:14px 0 6px;">ผลตรวจระดับ pixel (ΔE2000 map)</h4>`;
    if (!p.enabled) {
      const reason = p.note || p.verdict || 'ไม่ได้เปิดใช้งาน';
      return html + `<div class="lp-empty" style="padding:14px;">${escapeHtml(reason)}</div>`;
    }
    const pvClass = p.verdict === 'PASS' ? 'lp-v-pass' : p.verdict === 'WARN' ? 'lp-v-warn' : 'lp-v-fail';
    const fx = (v, d) => (typeof v === 'number' ? v.toFixed(d) : v);
    html += `
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px;">
        <span class="lp-verdict ${pvClass}" style="font-size:14px;padding:4px 10px;">pixel: ${escapeHtml(p.verdict)}</span>
        <span style="font-size:13px;color:#37474f;">
          pass-rate <b>${fx(p.pass_rate, 2)}%</b> &nbsp;|&nbsp; peak ΔE <b>${fx(p.de_peak, 1)}</b> &nbsp;|&nbsp;
          mean ΔE <b>${fx(p.de_mean, 2)}</b> &nbsp;|&nbsp; p95 <b>${fx(p.de_p95, 1)}</b> &nbsp;|&nbsp;
          p99 <b>${fx(p.de_p99, 1)}</b> &nbsp;|&nbsp; tol ${p.tolerance}</span>
      </div>`;

    const mi = p.mask_info || {};
    if (mi.ignored_pct != null) {
      const glareWarn = (mi.glare_pct || 0) > 15
        ? ` <span style="color:#c62828;">— แสงสะท้อนสูง (${mi.glare_pct}%) ควรถ่ายใหม่ลดแสงสะท้อน</span>` : '';
      html += `<div style="font-size:12px;color:#78909c;margin-bottom:6px;">
        ไม่นับพิกเซล <b>${mi.ignored_pct}%</b> (ขอบตัวอักษร ${mi.edge_pct || 0}% / แสงสะท้อน ${mi.glare_pct || 0}%)${glareWarn}</div>`;
    }

    const wb = (p.align_info && p.align_info.white_balance) || null;
    if (wb && wb.applied) {
      html += `<div style="font-size:12px;color:#78909c;margin-bottom:6px;">
        ปรับ white-balance อัตโนมัติจากพื้นที่ขาวของ master (gain RGB ${(wb.gain || []).join(' / ')})</div>`;
    }
    if (p.align_info && p.align_info.ok === false) {
      html += `<div class="lp-stub">⚠️ ECC alignment ไม่ converge — ผลอาจเพี้ยน ลองลากมุม 4 จุดให้แนบขอบฉลากให้แม่นขึ้นหรือเพิ่มแสง</div>`;
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
        html += `<tr><td>${i + 1}</td><td>${d.bbox.join(', ')}</td><td>${d.area_px}</td>
          <td><b>${d.peak_de}</b></td><td>${d.mean_de}</td>
          <td><span class="lp-swatch" style="background:${escapeAttr(d.master_hex)}"></span>${escapeHtml(d.master_hex)}
            &nbsp;→&nbsp;<span class="lp-swatch" style="background:${escapeAttr(d.found_hex)}"></span>${escapeHtml(d.found_hex)}</td>
          <td>${severityTag(d.severity)}</td></tr>`;
      });
      html += '</table>';
    } else {
      html += `<div class="lp-empty" style="padding:10px;color:#2e7d32;">
                ✓ ไม่พบจุดที่ ΔE เกิน tolerance (${p.tolerance}) — สีตรง master ทั้งภาพ</div>`;
    }
    return html;
  }

  // ── Wire interactive elements after injection ────────────────────
  function wireEvents(container) {
    const onlyFailed = container.querySelector('.lp-only-failed');
    if (onlyFailed) {
      onlyFailed.addEventListener('change', () => {
        const rows = container.querySelectorAll('.lp-fields tr[data-passed]');
        rows.forEach(tr => {
          tr.style.display = (onlyFailed.checked && tr.getAttribute('data-passed') === '1')
            ? 'none' : '';
        });
      });
    }
    const cmp = container.querySelector('.lp-cmp');
    if (cmp) {
      const range = cmp.querySelector('.lp-cmp-range');
      const top = cmp.querySelector('.lp-cmp-top');
      const divider = cmp.querySelector('.lp-cmp-divider');
      const apply = () => {
        const v = range.value;
        top.style.width = v + '%';
        divider.style.left = v + '%';
      };
      range.addEventListener('input', apply);
      apply();
    }
  }

  window.LabelReport = {
    render(container, report) {
      container.innerHTML = buildHtml(report);
      wireEvents(container);
    },
    escapeHtml,
  };
})();
