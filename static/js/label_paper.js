/* Label Paper Inspection — front-end controller.
 * SKU dropdown + 4-point perspective crop on a <canvas>.
 *
 * The photo is drawn onto a "work canvas" (EXIF orientation baked in,
 * long edge ≤ 4096 — matches the server's OCR_MAX_EDGE).  The user drags
 * 4 corner handles onto the label; the corners (in work-image pixel
 * coordinates, ordered TL,TR,BR,BL of the upright label) are POSTed with
 * the work image to /api/label_paper/inspect, where OpenCV does the
 * actual perspective warp at full resolution.
 *
 * Independent from the YOLO live-detection page; nothing here touches
 * /api/detection/*.
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

  const WORK_MAX_EDGE = 4096;  // keep in sync with inspectors/perspective.py
  const HANDLE_HIT_PX = 22;    // touch-friendly hit radius (display px)
  const CORNER_INSET  = 0.08;  // default corner position: 8% in from each edge
  const LOUPE_SIZE    = 140;   // loupe canvas size (px)
  const LOUPE_ZOOM    = 3;     // magnification relative to display scale

  let workCanvas = null;       // full-res, orientation-baked photo
  let viewCanvas = null;       // on-screen canvas (scaled copy + overlay)
  let viewCtx    = null;
  let loupeWrap  = null;       // loupe container div
  let loupeCanvas = null;
  let corners    = null;       // 4 [x,y] points in work-image coords (TL,TR,BR,BL)
  let dragIdx    = -1;
  let viewScale  = 1;

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

  // ── Image loading (EXIF orientation baked) ──────────────────────
  // createImageBitmap with imageOrientation:'from-image' handles EXIF in
  // Chrome/Firefox; the <img> fallback covers Safari, which also applies
  // EXIF when an HTMLImageElement is drawn to a canvas.
  function loadOriented(file) {
    if (window.createImageBitmap) {
      try {
        return createImageBitmap(file, { imageOrientation: 'from-image' })
          .catch(() => loadViaImg(file));
      } catch (e) {
        return loadViaImg(file);
      }
    }
    return loadViaImg(file);
  }

  function loadViaImg(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload  = () => { resolve(img); URL.revokeObjectURL(url); };
      img.onerror = (e) => { reject(e); URL.revokeObjectURL(url); };
      img.src = url;
    });
  }

  function buildWorkCanvas(source) {
    const w = source.width || source.naturalWidth;
    const h = source.height || source.naturalHeight;
    const scale = Math.min(1, WORK_MAX_EDGE / Math.max(w, h));
    const c = document.createElement('canvas');
    c.width  = Math.max(1, Math.round(w * scale));
    c.height = Math.max(1, Math.round(h * scale));
    c.getContext('2d').drawImage(source, 0, 0, c.width, c.height);
    return c;
  }

  // ── File picker → quad editor ────────────────────────────────────
  fileInput.addEventListener('change', async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    try {
      const source = await loadOriented(file);
      workCanvas = buildWorkCanvas(source);
      if (source.close) source.close();
    } catch (err) {
      cropBox.innerHTML = `<div class="lp-empty">เปิดไฟล์ภาพไม่สำเร็จ: ${escapeHtml(String(err))}</div>`;
      return;
    }
    corners = defaultCorners();
    setupView();
    inspectBtn.disabled = false;
    rotateBtn.disabled  = false;
    resetBtn.disabled   = false;
  });

  function defaultCorners() {
    const w = workCanvas.width, h = workCanvas.height;
    const ix = w * CORNER_INSET, iy = h * CORNER_INSET;
    return [[ix, iy], [w - ix, iy], [w - ix, h - iy], [ix, h - iy]];
  }

  // ── View canvas + loupe ──────────────────────────────────────────
  function setupView() {
    cropBox.innerHTML = '';
    cropBox.style.position = 'relative';

    viewCanvas = document.createElement('canvas');
    viewCanvas.id = 'quadCanvas';
    viewCanvas.style.display = 'block';
    viewCanvas.style.maxWidth = '100%';
    viewCanvas.style.touchAction = 'none';   // we handle drag ourselves
    cropBox.appendChild(viewCanvas);

    loupeWrap = document.createElement('div');
    loupeWrap.className = 'lp-loupe';
    loupeCanvas = document.createElement('canvas');
    loupeCanvas.width = LOUPE_SIZE;
    loupeCanvas.height = LOUPE_SIZE;
    loupeWrap.appendChild(loupeCanvas);
    loupeWrap.style.display = 'none';
    cropBox.appendChild(loupeWrap);

    const boxW = Math.max(cropBox.clientWidth - 2, 200);
    viewScale = Math.min(boxW / workCanvas.width, 560 / workCanvas.height, 1);
    viewCanvas.width  = Math.max(1, Math.round(workCanvas.width * viewScale));
    viewCanvas.height = Math.max(1, Math.round(workCanvas.height * viewScale));
    viewCtx = viewCanvas.getContext('2d');

    viewCanvas.addEventListener('pointerdown', onPointerDown);
    viewCanvas.addEventListener('pointermove', onPointerMove);
    viewCanvas.addEventListener('pointerup', onPointerUp);
    viewCanvas.addEventListener('pointercancel', onPointerUp);

    draw();
  }

  function draw() {
    if (!viewCtx) return;
    const W = viewCanvas.width, H = viewCanvas.height;
    viewCtx.clearRect(0, 0, W, H);
    viewCtx.drawImage(workCanvas, 0, 0, W, H);

    const pts = corners.map(([x, y]) => [x * viewScale, y * viewScale]);

    // Dim everything outside the quad
    viewCtx.save();
    viewCtx.fillStyle = 'rgba(0,0,0,0.45)';
    viewCtx.beginPath();
    viewCtx.rect(0, 0, W, H);
    viewCtx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 3; i >= 0; i--) viewCtx.lineTo(pts[i][0], pts[i][1]);
    viewCtx.closePath();
    viewCtx.fill('evenodd');
    viewCtx.restore();

    // Quad outline
    viewCtx.strokeStyle = '#00e5ff';
    viewCtx.lineWidth = 2;
    viewCtx.beginPath();
    viewCtx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < 4; i++) viewCtx.lineTo(pts[i][0], pts[i][1]);
    viewCtx.closePath();
    viewCtx.stroke();

    // Handles with corner numbers (1=บนซ้าย … 4=ล่างซ้าย ของฉลากตั้งตรง)
    const labels = ['1', '2', '3', '4'];
    pts.forEach((p, i) => {
      viewCtx.beginPath();
      viewCtx.arc(p[0], p[1], 9, 0, Math.PI * 2);
      viewCtx.fillStyle = (i === dragIdx) ? '#ffd600' : '#00e5ff';
      viewCtx.fill();
      viewCtx.strokeStyle = '#003c46';
      viewCtx.lineWidth = 2;
      viewCtx.stroke();
      viewCtx.fillStyle = '#003c46';
      viewCtx.font = 'bold 11px sans-serif';
      viewCtx.textAlign = 'center';
      viewCtx.textBaseline = 'middle';
      viewCtx.fillText(labels[i], p[0], p[1]);
    });
  }

  function eventPos(ev) {
    const rect = viewCanvas.getBoundingClientRect();
    // canvas CSS size can differ from its pixel size (max-width:100%)
    const sx = viewCanvas.width / rect.width;
    const sy = viewCanvas.height / rect.height;
    return [(ev.clientX - rect.left) * sx, (ev.clientY - rect.top) * sy];
  }

  function onPointerDown(ev) {
    if (!corners) return;
    const [px, py] = eventPos(ev);
    let best = -1, bestD = HANDLE_HIT_PX;
    corners.forEach(([x, y], i) => {
      const d = Math.hypot(x * viewScale - px, y * viewScale - py);
      if (d < bestD) { bestD = d; best = i; }
    });
    if (best >= 0) {
      dragIdx = best;
      viewCanvas.setPointerCapture(ev.pointerId);
      loupeWrap.style.display = 'block';
      moveCorner(px, py);
      ev.preventDefault();
    }
  }

  function onPointerMove(ev) {
    if (dragIdx < 0) return;
    const [px, py] = eventPos(ev);
    moveCorner(px, py);
    ev.preventDefault();
  }

  function onPointerUp(ev) {
    if (dragIdx < 0) return;
    dragIdx = -1;
    loupeWrap.style.display = 'none';
    try { viewCanvas.releasePointerCapture(ev.pointerId); } catch (e) { /* noop */ }
    draw();
  }

  function moveCorner(px, py) {
    const x = Math.min(Math.max(px / viewScale, 0), workCanvas.width - 1);
    const y = Math.min(Math.max(py / viewScale, 0), workCanvas.height - 1);
    corners[dragIdx] = [x, y];
    draw();
    updateLoupe(x, y, px, py);
  }

  function updateLoupe(wx, wy, vx, vy) {
    const ctx = loupeCanvas.getContext('2d');
    const srcSize = LOUPE_SIZE / (viewScale * LOUPE_ZOOM);
    ctx.imageSmoothingEnabled = false;
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, LOUPE_SIZE, LOUPE_SIZE);
    ctx.drawImage(workCanvas,
      wx - srcSize / 2, wy - srcSize / 2, srcSize, srcSize,
      0, 0, LOUPE_SIZE, LOUPE_SIZE);
    // Crosshair
    ctx.strokeStyle = '#ffd600';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(LOUPE_SIZE / 2, 0); ctx.lineTo(LOUPE_SIZE / 2, LOUPE_SIZE);
    ctx.moveTo(0, LOUPE_SIZE / 2); ctx.lineTo(LOUPE_SIZE, LOUPE_SIZE / 2);
    ctx.stroke();

    // Keep the loupe away from the finger: opposite horizontal side
    const onLeft = vx < viewCanvas.width / 2;
    loupeWrap.style.left  = onLeft ? 'auto' : '8px';
    loupeWrap.style.right = onLeft ? '8px'  : 'auto';
    loupeWrap.style.top   = '8px';
  }

  // ── Rotate / reset ───────────────────────────────────────────────
  // Rotating physically rotates the work canvas 90° CW.  Corners reset to
  // defaults — the intended flow is: rotate until the label is upright,
  // THEN drag the 4 corners.
  rotateBtn.addEventListener('click', () => {
    if (!workCanvas) return;
    const c = document.createElement('canvas');
    c.width = workCanvas.height;
    c.height = workCanvas.width;
    const ctx = c.getContext('2d');
    ctx.translate(c.width, 0);
    ctx.rotate(Math.PI / 2);
    ctx.drawImage(workCanvas, 0, 0);
    workCanvas = c;
    corners = defaultCorners();
    setupView();
  });

  resetBtn.addEventListener('click', () => {
    if (!workCanvas) return;
    corners = defaultCorners();
    draw();
  });

  // ── Inspect ─────────────────────────────────────────────────────
  inspectBtn.addEventListener('click', () => {
    if (!workCanvas || !corners) return;
    const sku = skuSelect.value;
    if (!sku) { alert('กรุณาเลือก SKU ก่อน'); return; }

    inspectBtn.disabled = true;
    inspectBtn.textContent = 'กำลังตรวจสอบ...';
    resultArea.innerHTML = '<div class="lp-empty">กำลังส่งภาพและประมวลผล...</div>';

    // Upload the full work image + corners; the server warps with OpenCV.
    workCanvas.toBlob(async (blob) => {
      if (!blob) {
        resultArea.innerHTML = '<div class="lp-empty">เตรียมภาพไม่สำเร็จ</div>';
        finish();
        return;
      }
      const fd = new FormData();
      fd.append('sku_code', sku);
      fd.append('image', blob, 'photo.jpg');
      fd.append('corners', JSON.stringify(
        corners.map(([x, y]) => [Math.round(x * 100) / 100, Math.round(y * 100) / 100])
      ));
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
    }, 'image/jpeg', 0.95);
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

    html += renderSummary(r);

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

    html += `<h4 style="margin:14px 0 6px;">ผลตรวจตัวอักษรแยกตาม Field</h4>`;
    if (r.field_results && r.field_results.length) {
      html += '<table class="lp-tbl"><tr><th>Field</th><th>Spec (คาดหวัง)</th><th>Master (OCR)</th><th>พบใน OCR</th><th>ความต่าง</th><th>Δ</th><th>สถานะ</th></tr>';
      for (const f of r.field_results) {
        const mf = f.master_found || '';
        // Highlight master_found: green = matches spec, orange = differs from spec
        const mfStyle = !mf ? 'color:#b0bec5'
                      : (mf === f.expected ? 'color:#2e7d32' : 'color:#e65100');
        const mfCell  = mf
          ? `<span style="${mfStyle}">${escapeHtml(mf)}</span>`
          : '<span style="color:#b0bec5;">—</span>';
        html += `<tr>
          <td><b>${escapeHtml(f.name)}</b>${f.critical ? ' <span style="color:#c62828;font-size:11px;">[critical]</span>' : ''}<br><span style="color:#90a4ae;font-size:11px;">${escapeHtml(f.method)}</span></td>
          <td>${escapeHtml(f.expected)}</td>
          <td>${mfCell}</td>
          <td>${escapeHtml(f.found || '—')}</td>
          <td>${renderCharDiff(f)}</td>
          <td>${f.distance}</td>
          <td>${severityTag(f.severity)}</td>
        </tr>`;
      }
      html += '</table>';
    } else {
      html += '<div class="lp-empty" style="padding:14px;">spec.json ของ SKU นี้ไม่มี field</div>';
    }

    html += renderPixelInspection(r.pixel_inspection || {});

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

  // ── Summary panel ───────────────────────────────────────────────
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
    // Fields
    const fFail = f.failed || 0, fCrit = f.critical || 0, fTot = f.total || 0;
    const fKind = fCrit > 0 ? 'bad' : (fFail > 0 ? 'warn' : (fTot > 0 ? 'ok' : ''));
    cards += card('Field ผิด', `${fFail} / ${fTot}`,
                  fCrit > 0 ? `${fCrit} critical` : (fFail > 0 ? 'ไม่ critical' : 'ตรงทั้งหมด'),
                  fKind);
    // Colors
    const cFail = c.failed || 0, cTot = c.total || 0;
    const cKind = cFail > 0 ? 'warn' : (cTot > 0 ? 'ok' : '');
    cards += card('สีผิด tol', `${cFail} / ${cTot}`,
                  cTot === 0 ? 'spec ไม่มีสี' : (cFail > 0 ? 'เกิน ΔE tolerance' : 'อยู่ใน tolerance'),
                  cKind);
    // Pixels
    if (p.enabled) {
      const pn = p.defect_count || 0, pa = p.defect_area || 0;
      const pKind = (p.verdict === 'FAIL') ? 'bad' : (p.verdict === 'WARN' ? 'warn' : 'ok');
      cards += card('Pixel ผิด', `${pn} จุด`,
                    pa > 0 ? `รวม ${pa.toLocaleString()} px` : 'ไม่พบ',
                    pKind);
    } else {
      cards += card('Pixel', '— ปิด —', 'pixel_inspection.enabled=false', '');
    }
    // Visual diff (Gemini)
    const v = s.visual_diff || {};
    if (v.enabled) {
      const vn = v.count || 0, vc = v.critical || 0;
      const vKind = vc > 0 ? 'bad' : (vn > 0 ? 'warn' : 'ok');
      cards += card('AI พบความต่าง', `${vn} รายการ`,
                    vc > 0 ? `${vc} critical` : (vn > 0 ? 'ไม่ critical' : 'ไม่พบ'),
                    vKind);
    } else {
      cards += card('AI Visual Diff', '— ปิด —', 'ตั้ง VISUAL_DIFF_ENABLED=1 + N8N URL', '');
    }
    return `<div class="lp-summary">${cards}</div>`;
  }

  // ── Gemini Visual Diff panel ───────────────────────────────────
  function renderVisualDiff(vd) {
    if (!vd || (vd.stub && !vd.error)) return '';
    if (vd.stub) {
      return `<div class="lp-vd-box">
        <div class="lp-vd-head">
          <h4>🔍 AI Visual Diff (Master ↔ Captured)</h4>
          <span class="lp-tag lp-tag-minor">SKIPPED</span>
        </div>
        <div class="lp-vd-summary">${escapeHtml(vd.error || 'ไม่ได้ทำงาน')}</div>
      </div>`;
    }
    const diffs = vd.differences || [];
    const engine = vd.engine || 'gemini';
    const summary = vd.summary || (diffs.length === 0
      ? '✓ ไม่พบความต่างของตัวอักษร'
      : `พบ ${diffs.length} รายการ`);
    const tagSeverity = (() => {
      if (!diffs.length) return 'ok';
      if (diffs.some(d => d.severity === 'critical')) return 'critical';
      if (diffs.some(d => d.severity === 'warning'))  return 'warning';
      return 'minor';
    })();

    let body = '';
    if (diffs.length) {
      body = '<ul class="lp-vd-list">' + diffs.map(d => {
        const sev = (d.severity || 'warning').toLowerCase();
        const klass = (sev === 'critical' || sev === 'warning' || sev === 'minor') ? sev : 'warning';
        const masterT  = escapeHtml(d.master_text  || '—');
        const capT     = escapeHtml(d.captured_text || '—');
        const loc      = d.location_hint ? `<div class="lp-vd-loc">📍 ${escapeHtml(d.location_hint)}</div>` : '';
        return `<li class="lp-vd-item ${klass}">
          <span class="lp-vd-type">${escapeHtml(d.type || 'diff')}</span>
          ${severityTag(klass)}
          <div style="margin-top:4px;">
            <span style="color:#546e7a;">Master:</span> <span class="lp-d-rep-a">${masterT}</span>
            &nbsp;→&nbsp;
            <span style="color:#546e7a;">พบ:</span> <span class="lp-d-rep-b">${capT}</span>
          </div>
          ${loc}
        </li>`;
      }).join('') + '</ul>';
    }

    return `<div class="lp-vd-box">
      <div class="lp-vd-head">
        <h4>🔍 AI Visual Diff (Master ↔ Captured)</h4>
        ${severityTag(tagSeverity)}
        <span class="lp-vd-summary">${escapeHtml(summary)}</span>
        <span style="font-size:11px;color:#90a4ae;">${escapeHtml(engine)}</span>
      </div>
      ${body}
    </div>`;
  }

  // ── Character-level diff inside Field table ─────────────────────
  function renderCharDiff(f) {
    if (f.passed) return '<span style="color:#2e7d32;">✓ ตรง</span>';
    const ops = f.diff || [];
    if (!ops.length) {
      // Fallback for methods (regex) or empty found
      return f.found
        ? `<span class="lp-d-rep-b">${escapeHtml(f.found)}</span>`
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

  // ── Side-by-side Master vs OCR line diff ────────────────────────
  function renderTextLineDiff(r) {
    const ops = r.text_line_diff || [];
    if (!ops.length) return '';
    let left = '', right = '';
    for (const op of ops) {
      if (op.op === 'equal') {
        left  += `<div class="lp-diff-line equal">${escapeHtml(op.text)}</div>`;
        right += `<div class="lp-diff-line equal">${escapeHtml(op.text)}</div>`;
      } else if (op.op === 'delete') {
        left  += `<div class="lp-diff-line del">− ${escapeHtml(op.text)}</div>`;
        right += `<div class="lp-diff-line gap">·</div>`;
      } else if (op.op === 'insert') {
        left  += `<div class="lp-diff-line gap">·</div>`;
        right += `<div class="lp-diff-line ins">+ ${escapeHtml(op.text)}</div>`;
      } else if (op.op === 'replace') {
        left  += `<div class="lp-diff-line rep">~ ${escapeHtml(op.a)}</div>`;
        right += `<div class="lp-diff-line rep">~ ${escapeHtml(op.b)}</div>`;
      }
    }
    return `<h4 style="margin:14px 0 6px;">เปรียบเทียบข้อความ Master ↔ OCR</h4>
            <div style="font-size:12px;color:#546e7a;margin-bottom:4px;">
              <span class="lp-d-del">แดง</span> = เฉพาะใน Master &nbsp;
              <span class="lp-d-ins">เขียว</span> = เฉพาะใน OCR &nbsp;
              <span class="lp-d-rep-b">เหลือง</span> = ทั้งคู่แต่ต่าง
            </div>
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
