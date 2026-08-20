/*
 * hik_burst.js — โหมด "ถ่ายรัว" ของแท็บกล้องอุตสาหกรรม
 *
 * ทำไมแยกไฟล์: `hik_camera.js` คุมแผงตั้งค่า 19 ตัว + สถิติสดอยู่แล้ว
 * ส่วนนี้เป็นหน้าจอของตัวเอง (แกลเลอรี + งานเบื้องหลัง) จึงแยกโมดูลปิด
 * เปิดออกมาแค่ window.HikBurst และ **ไม่แตะตัวแปรของหน้าอื่นเลย**
 *
 * ลำดับการทำงานหลังกด "เริ่มถ่ายรัว":
 *   ถ่าย (อัตรากล้อง) → วัดความคม/ความเบลอทั้งชุด → ตรวจ N ใบที่คมที่สุด
 * ทั้ง 3 ขั้นเป็นงานคนละก้อน ต่อกันที่ฝั่งนี้ เพื่อให้ผู้ใช้เห็นความคืบหน้าทุกขั้น
 * แทนที่จะกดแล้วเงียบไปครึ่งนาที
 */
(function () {
    'use strict';

    var $ = function (id) { return document.getElementById(id); };
    var PAGE = 120;

    var state = {
        capturing: false,
        session: null,       // ชุดที่กำลังเปิดดูอยู่ในแกลเลอรี
        rows: [],
        total: 0,
        offset: 0,
        sel: {},             // ไฟล์ที่ติ๊กไว้
        autoTop: 12,
        pollTimer: null,
        jobTimer: null,
        pending: null        // ขั้นถัดไปที่ต้องทำเมื่องานปัจจุบันจบ
    };

    function msg(html, tone) {
        var el = $('hikBurstMsg');
        if (!el) { return; }
        el.innerHTML = html || '';
        el.style.color = tone === 'bad' ? '#dc2626'
            : (tone === 'good' ? '#15803d' : '#64748b');
    }

    function num(id, dflt) {
        var el = $(id);
        var v = el ? parseInt(el.value, 10) : NaN;
        return isNaN(v) ? dflt : v;
    }

    function fmt(v, digits) {
        if (v === null || v === undefined || v === '') { return '—'; }
        return (typeof v === 'number') ? v.toFixed(digits === undefined ? 1 : digits) : v;
    }

    function gb(mb) { return mb ? (mb / 1024).toFixed(1) + ' GB' : '—'; }

    // ── ① ถ่าย ──────────────────────────────────────────────
    function startBurst() {
        var btn = $('hikBurstBtn');
        var seconds = Math.max(1, Math.min(60, num('hikBurstSeconds', 10)));
        if (btn) { btn.disabled = true; }
        msg('กำลังเริ่ม…');
        fetch('/api/camera/hik/burst', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ seconds: seconds, every_n: num('hikBurstEveryN', 1) })
        })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.status !== 'ok') { throw new Error(d.message || 'เริ่มไม่สำเร็จ'); }
                state.capturing = true;
                var box = document.querySelector('.hik-burst-box');
                if (box) { box.classList.add('is-recording'); }
                if (btn) { btn.textContent = '⏹ หยุดถ่าย'; btn.disabled = false; }
                msg('🔴 กำลังถ่าย… เลื่อนวัตถุผ่านหน้ากล้องได้เลย');
                pollBurst();
            })
            .catch(function (e) {
                if (btn) { btn.disabled = false; }
                msg('❌ ' + e.message, 'bad');
            });
    }

    function stopBurst() {
        fetch('/api/camera/hik/burst', { method: 'DELETE' })
            .then(function (r) { return r.json(); })
            .then(function (d) { finishBurst(d); })
            .catch(function (e) { msg('❌ ' + e.message, 'bad'); });
    }

    function pollBurst() {
        clearTimeout(state.pollTimer);
        fetch('/api/camera/hik/burst')
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.status !== 'ok') { throw new Error(d.message || 'อ่านสถานะไม่ได้'); }
                if (d.capturing) {
                    var b = d.burst || {};
                    msg('🔴 กำลังถ่าย… <b>' + (b.saved || 0) + '</b> ภาพ · '
                        + fmt(b.mb, 0) + ' MB · ' + fmt(b.save_fps) + ' ภาพ/วิ'
                        + (b.dropped ? ' · <b style="color:#dc2626">ทิ้ง ' + b.dropped + '</b>' : ''));
                    state.pollTimer = setTimeout(pollBurst, 700);
                    return;
                }
                finishBurst(d);
            })
            .catch(function (e) { msg('❌ ' + e.message, 'bad'); resetBtn(); });
    }

    function resetBtn() {
        state.capturing = false;
        var box = document.querySelector('.hik-burst-box');
        if (box) { box.classList.remove('is-recording'); }
        var btn = $('hikBurstBtn');
        if (btn) { btn.textContent = '🎬 เริ่มถ่ายรัว'; btn.disabled = false; }
    }

    function finishBurst(d) {
        resetBtn();
        var b = d.burst || {};
        var name = d.finished;
        var drop = b.dropped
            ? ' · <b style="color:#dc2626">ทิ้ง ' + b.dropped + ' เฟรม</b> (ดิสก์ตามไม่ทัน)'
            : '';
        msg('✅ ถ่ายจบ — <b>' + (b.saved || 0) + '</b> ภาพ · ' + fmt(b.mb, 0) + ' MB'
            + drop + '<br>กำลังวัดความคม…', 'good');
        if (!name) { return; }
        openGallery(name);
        // ต่อขั้น: วัดผล → แล้วค่อยตรวจ N ใบที่คมที่สุด (ต้องวัดก่อนถึงจะรู้ว่าใบไหนคม)
        state.pending = { kind: 'detect_top', session: name };
        runMetrics(name);
    }

    // ── ② งานเบื้องหลัง (วัดผล / ตรวจ) ────────────────────────
    function runMetrics(name) {
        fetch('/api/camera/hik/bursts/' + encodeURIComponent(name) + '/metrics',
            { method: 'POST' })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.status !== 'ok') { throw new Error(d.message || 'สั่งวัดไม่สำเร็จ'); }
                pollJob();
            })
            .catch(function (e) { showJob(null, '❌ ' + e.message); state.pending = null; });
    }

    function runDetect(name, body) {
        fetch('/api/camera/hik/bursts/' + encodeURIComponent(name) + '/detect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.status !== 'ok') { throw new Error(d.message || 'สั่งตรวจไม่สำเร็จ'); }
                pollJob();
            })
            .catch(function (e) {
                showJob(null, '❌ ' + e.message);
                if (state.session) { loadSession(state.session, true); }
            });
    }

    function showJob(job, err) {
        var el = $('hikGalJob');
        if (!el) { return; }
        if (err) { el.style.display = ''; el.innerHTML = err; return; }
        if (!job || !job.running) { el.style.display = 'none'; return; }
        var label = job.kind === 'metrics' ? 'กำลังวัดความคม/ความเบลอ' : 'กำลังตรวจด้วยโมเดล';
        var pct = job.total ? Math.round(job.done * 100 / job.total) : 0;
        el.style.display = '';
        el.innerHTML = '⏳ ' + label + ' · <b>' + job.done + '/' + job.total + '</b> ('
            + pct + '%) · ' + job.elapsed_s + ' วิ '
            + '<button class="btn btn-mini" type="button" id="hikJobCancel">ยกเลิก</button>';
        var c = $('hikJobCancel');
        if (c) {
            c.addEventListener('click', function () {
                fetch('/api/camera/hik/burst-job', { method: 'DELETE' });
                state.pending = null;
            });
        }
    }

    function pollJob() {
        clearTimeout(state.jobTimer);
        fetch('/api/camera/hik/burst')
            .then(function (r) { return r.json(); })
            .then(function (d) {
                var job = d.job;
                showJob(job);
                if (job && job.running) {
                    state.jobTimer = setTimeout(pollJob, 800);
                    return;
                }
                if (job && job.error) { showJob(null, '❌ ' + job.error); }
                var next = state.pending;
                state.pending = null;
                // รีเฟรชเสมอ **ก่อน** ต่อขั้นถัดไป: ผลของขั้นที่เพิ่งเสร็จต้องขึ้นจอทันที
                if (state.session) { loadSession(state.session, true); }
                loadSessions();
                if (next && next.kind === 'detect_top' && job && !job.cancelled && !job.error) {
                    msg('✅ วัดผลเสร็จ — กำลังตรวจ ' + state.autoTop + ' ใบที่คมที่สุด', 'good');
                    runDetect(next.session, { top: state.autoTop });
                }
            })
            .catch(function () { /* เงียบได้ — เป็นแค่แถบความคืบหน้า */ });
    }

    // ── ③ แกลเลอรี ──────────────────────────────────────────
    function openGallery(name) {
        var ov = $('hikBurstOverlay');
        if (!ov) { return; }
        ov.style.display = '';
        loadSessions(name);
    }

    function loadSessions(select) {
        fetch('/api/camera/hik/bursts')
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.autodetect_top) { state.autoTop = d.autodetect_top; }
                var btn = $('hikGalDetectTopBtn');
                if (btn) { btn.textContent = '🔍 ตรวจ ' + state.autoTop + ' ใบที่คมสุด'; }
                renderSessions(d.sessions || [], d.capturing);
                var disk = $('hikGalDisk');
                if (disk) {
                    var used = (d.sessions || []).reduce(function (a, s) { return a + (s.mb || 0); }, 0);
                    disk.innerHTML = 'รวม <b>' + (used / 1024).toFixed(1) + ' GB</b> · '
                        + 'ดิสก์ว่าง ' + gb(d.free_mb);
                }
                var pick = select || state.session
                    || ((d.sessions || [])[0] || {}).name;
                if (pick) { loadSession(pick); }
            })
            .catch(function (e) { showJob(null, '❌ ' + e.message); });
    }

    function renderSessions(list, capturing) {
        var box = $('hikGalSessions');
        if (!box) { return; }
        if (!list.length) {
            box.innerHTML = '<div class="control-hint">ยังไม่มีภาพที่ถ่ายไว้</div>';
            return;
        }
        box.innerHTML = list.map(function (s) {
            var exp = s.exposure_us ? Math.round(s.exposure_us) + ' µs' : 'exposure ?';
            var blur = (s.summary && s.summary.blur_px_median !== null
                && s.summary.blur_px_median !== undefined)
                ? ' · เบลอ ~' + s.summary.blur_px_median + ' px' : '';
            return '<div class="hik-gal-sess' + (s.name === state.session ? ' active' : '')
                + '" data-name="' + s.name + '">'
                + '<b>' + (s.started_at || s.name) + (s.name === capturing ? ' 🔴' : '') + '</b>'
                + '<span>' + s.frames + ' ภาพ · ' + s.mb + ' MB · ' + exp + blur + '</span>'
                + (s.dropped ? '<span style="color:#dc2626">ทิ้ง ' + s.dropped + ' เฟรม</span>' : '')
                + '</div>';
        }).join('');
        Array.prototype.forEach.call(box.querySelectorAll('[data-name]'), function (el) {
            el.addEventListener('click', function () { loadSession(el.dataset.name); });
        });
    }

    function loadSession(name, keepOffset) {
        var sort = ($('hikGalSort') || {}).value || 'sharp';
        if (!keepOffset || state.session !== name) {
            state.offset = 0;
            state.rows = [];
            state.sel = {};
        }
        state.session = name;
        var title = $('hikGalTitle');
        if (title) { title.textContent = name; }
        var url = '/api/camera/hik/bursts/' + encodeURIComponent(name)
            + '?sort=' + encodeURIComponent(sort) + '&limit=' + PAGE + '&offset=0';
        fetch(url)
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.status !== 'ok') { throw new Error(d.message || 'เปิดชุดไม่ได้'); }
                state.rows = d.frames || [];
                state.total = d.total || 0;
                state.offset = state.rows.length;
                renderSummary(d);
                renderGrid();
                showJob(d.job);
                renderSessions_active();
            })
            .catch(function (e) { showJob(null, '❌ ' + e.message); });
    }

    function renderSessions_active() {
        Array.prototype.forEach.call(
            document.querySelectorAll('#hikGalSessions [data-name]'), function (el) {
                el.classList.toggle('active', el.dataset.name === state.session);
            });
    }

    function loadMore() {
        var sort = ($('hikGalSort') || {}).value || 'sharp';
        var url = '/api/camera/hik/bursts/' + encodeURIComponent(state.session)
            + '?sort=' + encodeURIComponent(sort) + '&limit=' + PAGE
            + '&offset=' + state.offset;
        fetch(url)
            .then(function (r) { return r.json(); })
            .then(function (d) {
                state.rows = state.rows.concat(d.frames || []);
                state.offset = state.rows.length;
                renderGrid();
            });
    }

    /**
     * แถบสรุป — ตัวเลขที่ตัดสินว่า "กล้องจับภาพที่ไม่เบลอได้ไหม"
     * ทุกค่าเป็นของที่ **วัดได้จากภาพจริง** ไม่ใช่ค่าที่ตั้งไว้ และค่าที่วัดไม่ได้
     * จะขึ้น "—" ไม่ใช่ตัวเลขที่เดามา
     */
    function renderSummary(d) {
        var el = $('hikGalSummary');
        if (!el) { return; }
        var m = d.meta || {};
        var s = d.summary || {};
        var head = '<b>' + (d.total || 0) + ' ภาพ</b>'
            + ' · exposure <b>' + (m.exposure_us ? Math.round(m.exposure_us) + ' µs' : '—') + '</b>'
            + ' · gain ' + fmt(m.gain_db) + ' dB'
            + ' · ' + (m.size || '—')
            + (s.fps_measured ? ' · <b>' + s.fps_measured + ' fps</b> (วัดจากเวลาเฟรมจริง)' : '')
            + (m.dropped ? ' · <span class="hik-gal-bad">ทิ้ง ' + m.dropped + ' เฟรม</span>' : '');

        if (!d.metrics_ready) {
            el.innerHTML = head + '<br><span class="hik-gal-warn">ยังไม่ได้วัดความคม — '
                + 'กดปุ่ม "📏 วัดความคม/ความเบลอ"</span>';
            return;
        }
        var body;
        if (!s.speed_px_s) {
            body = '<span class="hik-gal-warn">วัดความเร็วของวัตถุไม่ได้ '
                + '(พบเฟรมที่มีของเคลื่อนไหว ' + (s.moving_frames || 0) + '/' + s.total_frames + ')'
                + ' ⇒ บอกระยะเบลอเป็นตัวเลขไม่ได้</span> — '
                + 'ลองโบกวัตถุให้ผ่านกลางเฟรมและให้ฉากหลังนิ่ง แล้วถ่ายใหม่';
        } else {
            var blur = s.blur_px_median;
            var cls = blur === null || blur === undefined ? ''
                : (blur <= 1 ? 'hik-gal-good' : (blur <= 3 ? 'hik-gal-warn' : 'hik-gal-bad'));
            var verdict = blur === null || blur === undefined ? '—'
                : (blur <= 1 ? 'คม ✅' : (blur <= 3 ? 'พอใช้ ⚠️' : 'เบลอ ❌'));
            body = 'วัตถุเคลื่อนที่ <b>' + s.speed_px_s + ' px/วินาที</b>'
                + (s.speed_mm_s ? ' (' + s.speed_mm_s + ' mm/วิ)' : '')
                + ' ⇒ ที่ exposure นี้ <b class="' + cls + ' big">เบลอ ~'
                + fmt(blur, 2) + ' พิกเซล</b> · ' + verdict
                + '<br>เบลอน้อยที่สุดที่จับได้ <b>' + fmt(s.blur_px_min, 2) + ' px</b>'
                + ' · ภาพที่คมที่สุดคือ <b>' + (s.best_file || '—') + '</b>';
            if (s.max_exposure_us_1px) {
                body += '<br>📏 ที่ความเร็วนี้ exposure ต้องไม่เกิน <b>'
                    + Math.round(s.max_exposure_us_1px) + ' µs</b> จึงจะเบลอ ≤1 px';
                if (s.light_factor_needed && s.light_factor_needed > 1.2) {
                    body += '<br>💡 ต้องเพิ่มไฟอีก <b class="hik-gal-bad">~'
                        + s.light_factor_needed + ' เท่า</b> เพื่อให้ภาพ'
                        + '<b>สว่างเท่าเดิม</b>ที่ exposure สั้นลง';
                    if (s.light_factor_usable
                        && s.light_factor_usable > s.light_factor_needed * 1.2) {
                        body += ' · และ <b class="hik-gal-bad">~' + s.light_factor_usable
                            + ' เท่า</b> ถ้าอยากให้สว่างพอใช้งานจริง (~'
                            + s.target_mean + '/255 — ตอนนี้วัดได้ '
                            + fmt(s.mean_median) + ')';
                    }
                } else if (s.light_factor_needed) {
                    body += ' ⇒ <b class="hik-gal-good">exposure ปัจจุบันผ่านแล้ว</b>';
                }
            }
        }
        el.innerHTML = head + '<br>' + body;
    }

    function badge(r) {
        if (r.verdict === 'ng') { return '<span class="hik-badge ng">NG ' + (r.dent_count || 0) + '</span>'; }
        if (r.verdict === 'ok') { return '<span class="hik-badge ok">OK</span>'; }
        return '';
    }

    function kindBadge(r) {
        if (r.blur_kind === 'sharp') { return '<span class="hik-badge sharp">คม</span>'; }
        if (r.blur_kind === 'motion_x') { return '<span class="hik-badge motion">เบลอแนวนอน</span>'; }
        if (r.blur_kind === 'motion_y') { return '<span class="hik-badge motion">เบลอแนวตั้ง</span>'; }
        if (r.blur_kind === 'isotropic') { return '<span class="hik-badge iso">เบลอ 2 แกน</span>'; }
        return '';
    }

    function renderGrid() {
        var grid = $('hikGalGrid');
        if (!grid) { return; }
        var name = state.session;
        if (!state.rows.length) {
            grid.innerHTML = '<div class="control-hint">ชุดนี้ไม่มีภาพ</div>';
            $('hikGalMore').style.display = 'none';
            return;
        }
        var base = '/api/camera/hik/bursts/' + encodeURIComponent(name);
        grid.innerHTML = state.rows.map(function (r) {
            var info = '<div class="hik-card-info">'
                + '<div>' + r.file + ' ' + badge(r) + ' ' + kindBadge(r) + '</div>'
                + '<div><span class="k">คะแนนคม</span> ' + fmt(r.sharp, 0)
                + ' · <span class="k">แกน x/y</span> ' + fmt(r.ratio, 2) + '</div>'
                + '<div><span class="k">เบลอ</span> '
                + (r.blur_px === undefined || r.blur_px === null
                    ? '—' : '<b>' + r.blur_px + ' px</b>')
                + ' · <span class="k">ความเร็ว</span> '
                + (r.speed_px_s ? r.speed_px_s + ' px/s' : '—') + '</div>'
                + '</div>';
            return '<div class="hik-card' + (state.sel[r.file] ? ' sel' : '') + '" data-f="' + r.file + '">'
                + '<div class="hik-card-top">'
                + '<input type="checkbox" data-sel="' + r.file + '"'
                + (state.sel[r.file] ? ' checked' : '') + '>'
                + '<button class="hik-x" data-del="' + r.file + '" title="ลบภาพนี้">&times;</button>'
                + '</div>'
                + '<img loading="lazy" src="' + base + '/thumb/' + r.file + '" '
                + 'alt="เฟรม ' + r.file + ' จากชุดถ่ายรัว" data-zoom="' + r.file + '">'
                + info + '</div>';
        }).join('');

        Array.prototype.forEach.call(grid.querySelectorAll('[data-del]'), function (b) {
            b.addEventListener('click', function () { delFrames([b.dataset.del]); });
        });
        Array.prototype.forEach.call(grid.querySelectorAll('[data-sel]'), function (c) {
            c.addEventListener('change', function () {
                state.sel[c.dataset.sel] = c.checked;
                var card = c.closest('.hik-card');
                if (card) { card.classList.toggle('sel', c.checked); }
            });
        });
        Array.prototype.forEach.call(grid.querySelectorAll('[data-zoom]'), function (im) {
            im.addEventListener('click', function () { showFrame(im.dataset.zoom); });
        });

        var more = $('hikGalMore');
        if (more) { more.style.display = state.rows.length < state.total ? '' : 'none'; }
    }

    function showFrame(file) {
        var ov = $('hikFrameOverlay');
        if (!ov) { return; }
        var row = null;
        state.rows.forEach(function (r) { if (r.file === file) { row = r; } });
        var base = '/api/camera/hik/bursts/' + encodeURIComponent(state.session);
        $('hikFrameImg').src = base + '/frame/' + file + '?annotate=1&roi=1&t=' + Date.now();
        $('hikFrameTitle').textContent = state.session + ' · ' + file;
        var meta = row ? ('คะแนนคม ' + fmt(row.sharp, 0)
            + ' · แกน x/y ' + fmt(row.ratio, 2)
            + ' · ความสว่าง ' + fmt(row.mean)
            + ' · เลื่อนจากเฟรมก่อน ' + (row.shift_px ? row.shift_px + ' px' : '—')
            + ' · เบลอ ' + (row.blur_px === undefined || row.blur_px === null
                ? '—' : row.blur_px + ' px')
            + (row.roi_src === 'frame'
                ? ' · ⚠️ วัดจากทั้งเฟรม (ไม่พบวัตถุที่เคลื่อนไหว)'
                : (row.roi_src === 'moving' ? ' · วัดเฉพาะในกรอบสีฟ้า'
                    : ' · ยังไม่ได้วัดชุดนี้'))) : '';
        $('hikFrameMeta').textContent = meta;
        ov.style.display = '';
    }

    // ── ④ ลบ ────────────────────────────────────────────────
    function delFrames(files) {
        if (!files.length) { return; }
        fetch('/api/camera/hik/bursts/' + encodeURIComponent(state.session) + '/frames', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ files: files })
        })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.status !== 'ok') { throw new Error(d.message || 'ลบไม่สำเร็จ'); }
                var gone = {};
                (d.removed || []).forEach(function (f) { gone[f] = 1; delete state.sel[f]; });
                state.rows = state.rows.filter(function (r) { return !gone[r.file]; });
                state.total -= (d.removed || []).length;
                state.offset = state.rows.length;
                renderGrid();
                loadSessions(state.session);
            })
            .catch(function (e) { showJob(null, '❌ ' + e.message); });
    }

    function delSelected() {
        var files = Object.keys(state.sel).filter(function (f) { return state.sel[f]; });
        if (!files.length) { showJob(null, 'ยังไม่ได้เลือกภาพ'); return; }
        if (!window.confirm('ลบ ' + files.length + ' ภาพที่เลือก?')) { return; }
        delFrames(files);
    }

    function delSession() {
        if (!state.session) { return; }
        if (!window.confirm('ลบชุด "' + state.session + '" ทั้งชุด? กู้คืนไม่ได้')) { return; }
        fetch('/api/camera/hik/bursts/' + encodeURIComponent(state.session), { method: 'DELETE' })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.status !== 'ok') { throw new Error(d.message || 'ลบไม่สำเร็จ'); }
                state.session = null;
                state.rows = [];
                state.sel = {};
                loadSessions();
            })
            .catch(function (e) { showJob(null, '❌ ' + e.message); });
    }

    function selectPage() {
        var all = state.rows.every(function (r) { return state.sel[r.file]; });
        state.rows.forEach(function (r) { state.sel[r.file] = !all; });
        renderGrid();
    }

    function closeOverlay(id) {
        var ov = $(id);
        if (!ov) { return; }
        ov.style.display = 'none';
    }

    function bindOverlay(ovId, closeId) {
        var ov = $(ovId);
        var x = $(closeId);
        if (x) { x.addEventListener('click', function () { closeOverlay(ovId); }); }
        if (ov) {
            ov.addEventListener('click', function (e) {
                if (e.target === ov) { closeOverlay(ovId); }
            });
        }
    }

    window.HikBurst = {
        init: function () {
            var b = $('hikBurstBtn');
            if (b) {
                b.addEventListener('click', function () {
                    if (state.capturing) { stopBurst(); } else { startBurst(); }
                });
            }
            var g = $('hikBurstGalleryBtn');
            if (g) { g.addEventListener('click', function () { openGallery(state.session); }); }
            bindOverlay('hikBurstOverlay', 'hikGalClose');
            bindOverlay('hikFrameOverlay', 'hikFrameClose');

            var sort = $('hikGalSort');
            if (sort) {
                sort.addEventListener('change', function () {
                    if (state.session) { loadSession(state.session); }
                });
            }
            var mb = $('hikGalMoreBtn');
            if (mb) { mb.addEventListener('click', loadMore); }
            var mt = $('hikGalMetricsBtn');
            if (mt) {
                mt.addEventListener('click', function () {
                    if (state.session) { state.pending = null; runMetrics(state.session); }
                });
            }
            var dt = $('hikGalDetectTopBtn');
            if (dt) {
                dt.addEventListener('click', function () {
                    if (state.session) { runDetect(state.session, { top: state.autoTop }); }
                });
            }
            var da = $('hikGalDetectAllBtn');
            if (da) {
                da.addEventListener('click', function () {
                    if (!state.session) { return; }
                    if (!window.confirm('ตรวจทั้งชุด ' + state.total
                        + ' ภาพ? อาจใช้เวลาหลายนาที')) { return; }
                    runDetect(state.session, { all: true });
                });
            }
            var sa = $('hikGalSelAll');
            if (sa) { sa.addEventListener('click', selectPage); }
            var ds = $('hikGalDelSel');
            if (ds) { ds.addEventListener('click', delSelected); }
            var dl = $('hikGalDelAll');
            if (dl) { dl.addEventListener('click', delSession); }

            document.addEventListener('keydown', function (e) {
                if (e.key !== 'Escape') { return; }
                if (($('hikFrameOverlay') || {}).style
                    && $('hikFrameOverlay').style.display !== 'none') {
                    closeOverlay('hikFrameOverlay');
                } else {
                    closeOverlay('hikBurstOverlay');
                }
            });
        },

        /** ปุ่มถ่ายรัวใช้ได้เฉพาะตอนกล้องกำลังทำงาน (แกลเลอรีเปิดดูได้ตลอด) */
        setActive: function (active) {
            var b = $('hikBurstBtn');
            if (b && !state.capturing) { b.disabled = !active; }
        }
    };
})();
