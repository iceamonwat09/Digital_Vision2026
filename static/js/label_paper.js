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
          resultArea.innerHTML = `<div class="lp-verdict lp-v-fail">${window.LabelReport.escapeHtml(json.error || 'error')}</div>`;
        } else {
          window.LabelReport.render(resultArea, json);
        }
      } catch (err) {
        resultArea.innerHTML = `<div class="lp-verdict lp-v-fail">network error: ${window.LabelReport.escapeHtml(String(err))}</div>`;
      } finally {
        finish();
      }
    }, 'image/jpeg', 0.95);
  });

  function finish() {
    inspectBtn.disabled = false;
    inspectBtn.textContent = 'ส่งตรวจสอบ →';
  }

  // ── Refresh master OCR cache (after a new artwork revision) ──────
  const refreshMasterBtn = document.getElementById('refreshMasterBtn');
  if (refreshMasterBtn) {
    refreshMasterBtn.addEventListener('click', async () => {
      const sku = skuSelect.value;
      if (!sku) { alert('กรุณาเลือก SKU ก่อน'); return; }
      refreshMasterBtn.disabled = true;
      const original = refreshMasterBtn.textContent;
      refreshMasterBtn.textContent = 'กำลังล้าง...';
      try {
        const res = await fetch('/api/label_paper/master/refresh', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sku_code: sku }),
        });
        const json = await res.json();
        alert(res.ok
          ? (json.cache_removed ? `ล้าง cache ของ ${sku} แล้ว — ครั้งหน้าจะ OCR master ใหม่`
                                : `${sku} ไม่มี cache ค้างอยู่`)
          : `ล้างไม่สำเร็จ: ${json.error || 'error'}`);
      } catch (err) {
        alert('network error: ' + err);
      } finally {
        refreshMasterBtn.disabled = false;
        refreshMasterBtn.textContent = original;
      }
    });
  }

})();
