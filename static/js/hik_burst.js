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
        fps: null,
        megapixels: null,
        pollTimer: null,
        shapeTimer: null,
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

    // ── ตัวประมาณ "จะได้กี่ภาพ" ก่อนกดถ่าย ───────────────────
    // ผู้ใช้ตั้งเวลาเป็นวินาที แต่สิ่งที่เขาต้องแบกคือ **จำนวนภาพกับเมกะไบต์**
    // ⇒ คำนวณจาก fps ที่ **วัดได้จริง** ของกล้อง (ไม่ใช่ตัวเลขในสเปก) ให้เห็นก่อน
    var EST_KB_PER_MP = 190;                  // วัดจากไฟล์จริง: JPEG q95 ≈ 0.19 MB ต่อล้านพิกเซล

    function refreshEstimate() {
        var el = $('hikBurstEst');
        if (!el) { return; }
        var secs = Math.max(1, Math.min(60, num('hikBurstSeconds', 10)));
        var every = Math.max(1, Math.min(20, num('hikBurstEveryN', 1)));
        markPresets(every);
        var fps = state.fps, mp = state.megapixels;
        if (!fps) {
            el.className = 'hik-burst-est';
            el.innerHTML = 'กด Start Detection ก่อน แล้วระบบจะบอกว่าจะได้กี่ภาพ';
            return;
        }
        var shots = Math.round(fps * secs / every);
        var mb = mp ? Math.round(shots * mp * EST_KB_PER_MP / 1024) : null;
        var gap = every / fps;                 // เวลาระหว่างภาพที่เก็บจริง
        var warn = mb !== null && mb > 400;
        el.className = 'hik-burst-est' + (warn ? ' warn' : '');
        el.innerHTML = 'จะได้ราว <b>' + shots + '</b> ภาพ'
            + (mb !== null ? ' · <b>' + mb + '</b> MB' : '')
            + ' · ห่างกัน <b>' + Math.round(gap * 1000) + '</b> ms'
            + (every > 1 ? ' (เก็บ 1 ใน ' + every + ' เฟรม)' : '')
            + (warn ? '<br>⚠️ ไฟล์เยอะ — เพิ่ม “เก็บ 1 ใน N” เพื่อลดจำนวนภาพ '
                + 'โดยที่ <b>ความเบลอของแต่ละภาพไม่เปลี่ยน</b>' : '');
    }

    function markPresets(every) {
        Array.prototype.forEach.call(
            document.querySelectorAll('#hikEveryPresets [data-every]'), function (b) {
                b.classList.toggle('on', Number(b.dataset.every) === every);
            });
    }

    function applyShape(st) {
        if (!st) { state.fps = null; state.megapixels = null; return; }
        state.fps = st.fps || st.fps_avg || null;
        var size = (st.size || '').split('x');
        state.megapixels = (size.length === 2)
            ? (Number(size[0]) * Number(size[1]) / 1e6) : null;
    }

    /**
     * อ่าน fps + ขนาดภาพที่กล้องส่งจริง เพื่อให้ตัวประมาณไม่ใช่การเดา.
     *
     * ⚠️ **ใช้ค่าที่แถบสถานะ poll มาแล้วก่อนเสมอ** — ทุกคำขอ
     * /api/camera/hik/status ไปจบที่ `net_stats()` ซึ่งต้องจับ lock ตัวเดียวกับ
     * ที่เธรดจับภาพถือไว้ตลอดช่วงจับเฟรม+แปลงสี ⇒ poll ซ้ำซ้อน = ภาพสดสะดุด.
     * เดิมโมดูลนี้ยิงเองทุก 4 วิ **ตลอดเวลาที่กล้องเปิด** แม้ไม่ได้ดูแผงนี้เลย.
     * ยิงเองเฉพาะตอนแถบสถานะยังไม่มีค่าให้ (เช่นเพิ่งเปิดกล้อง)
     */
    function pollCameraShape() {
        var shared = (window.HikUI && window.HikUI.getStats)
            ? window.HikUI.getStats(6000) : null;
        if (shared) { applyShape(shared); refreshEstimate(); return; }
        fetch('/api/camera/hik/status')
            .then(function (r) { return r.json(); })
            .then(function (d) {
                applyShape((d && d.active && d.stats) ? d.stats : null);
            })
            .catch(function () { /* เงียบได้ — เป็นแค่ตัวประมาณ */ })
            .finally(refreshEstimate);
    }

    // ── ① ถ่าย ──────────────────────────────────────────────
    function startBurst() {
        var btn = $('hikBurstBtn');
        var seconds = Math.max(1, Math.min(60, num('hikBurstSeconds', 10)));
        if (btn) { btn.disabled = true; }
        msg('กำลังเริ่ม…');
        fetch('/api/camera/hik/burst', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                seconds: seconds,
                every_n: num('hikBurstEveryN', 1),
                pause_inference: !!($('hikBurstPause') || {}).checked
            })
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
                        + (b.dropped ? ' · <b style="color:#dc2626">ทิ้ง ' + b.dropped + '</b>' : '')
                        + (d.paused_inference
                            ? '<br><b style="color:#dc2626">⏸️ หยุดตรวจ/หยุดนับอยู่</b>' : ''));
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
        if (err) {
            el.style.display = '';
            el.className = 'hik-gal-job';
            el.innerHTML = err;
            return;
        }
        if (!job || !job.running) { el.style.display = 'none'; return; }
        var label = job.kind === 'metrics' ? 'กำลังวัดความคม/ความเบลอ' : 'กำลังตรวจด้วยโมเดล';
        var pct = job.total ? Math.round(job.done * 100 / job.total) : 0;
        el.style.display = '';
        el.className = 'hik-gal-job';
        el.innerHTML = '<span>⏳ ' + label + '</span>'
            + '<span class="hik-progress"><i style="width:' + pct + '%"></i></span>'
            + '<span><b>' + job.done + '/' + job.total + '</b> · ' + job.elapsed_s + ' วิ</span>'
            + '<button class="btn btn-secondary btn-sm" type="button" id="hikJobCancel">ยกเลิก</button>';
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
                var pr = $('hikBurstPauseRow');
                if (pr) { pr.style.display = d.can_pause_inference ? '' : 'none'; }
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
            box.innerHTML = '<div class="hik-empty">ยังไม่มีภาพที่ถ่ายไว้</div>';
            return;
        }
        box.innerHTML = list.map(function (s) {
            var sub = [s.frames + ' ภาพ', s.mb + ' MB',
                (s.exposure_us ? Math.round(s.exposure_us) + ' µs' : 'exposure ?')];
            var sm = s.summary || {};
            if (sm.motion === 'negligible') {
                sub.push('วัตถุแทบไม่ขยับ');
            } else if (sm.blur_px_median !== null && sm.blur_px_median !== undefined) {
                sub.push('เบลอ ' + sm.blur_px_median + ' px');
            }
            return '<div class="hik-gal-sess' + (s.name === state.session ? ' active' : '')
                + '" data-name="' + s.name + '">'
                + '<b>' + (s.started_at || s.name) + (s.name === capturing ? ' 🔴' : '') + '</b>'
                + '<span class="sub">' + sub.join(' · ') + '</span>'
                + (s.dropped ? '<span class="drop">⚠ ทิ้ง ' + s.dropped + ' เฟรม</span>' : '')
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

    function kpi(label, value, unit, tone) {
        return '<div class="hik-kpi ' + (tone || '') + '">'
            + '<div class="k">' + label + '</div>'
            + '<div class="v">' + value + (unit ? ' <span class="u">' + unit + '</span>' : '')
            + '</div></div>';
    }

    function blurTone(px) {
        if (px === null || px === undefined) { return 'muted'; }
        return px <= 1 ? 'good' : (px <= 3 ? 'warn' : 'bad');
    }

    /**
     * แถบสรุป — ตัวเลขที่ตัดสินว่า "กล้องจับภาพที่ไม่เบลอได้ไหม"
     * ทุกค่าเป็นของที่ **วัดได้จากภาพจริง** ไม่ใช่ค่าที่ตั้งไว้ และค่าที่วัดไม่ได้
     * จะขึ้น "—" ไม่ใช่ตัวเลขที่เดามา
     */
    function renderSummary(d) {
        var box = $('hikGalKpis');
        var note = $('hikGalSummary');
        var drop = $('hikGalDrop');
        if (!box || !note) { return; }
        var m = d.meta || {};
        var s = d.summary || {};

        var cards = [
            kpi('ภาพในชุด', d.total || 0, 'ภาพ'),
            kpi('exposure', m.exposure_us ? Math.round(m.exposure_us) : '—', 'µs'),
            kpi('gain', m.gain_db === undefined || m.gain_db === null ? '—' : fmt(m.gain_db), 'dB'),
            kpi('ขนาดภาพ', m.size || '—', '')
        ];
        if (d.metrics_ready) {
            // วัตถุขยับต่ำกว่าพื้นสัญญาณรบกวน ⇒ แสดง "—" ไม่ใช่ตัวเลขที่วัดไม่ได้จริง
            // (บนสถานีเคยขึ้น "เบลอ 0.02 px" + "exposure ≤1px = 217,391 µs" ทั้งที่
            //  วัตถุแทบไม่ขยับ = ตัวเลขที่ดูน่าเชื่อถือแต่ไม่มีความหมาย)
            var quiet = s.motion === 'negligible';
            cards.push(kpi('ความเร็ววัตถุ',
                (s.speed_px_s && !quiet) ? Math.round(s.speed_px_s) : '—', 'px/วิ',
                (s.speed_px_s && !quiet) ? '' : 'muted'));
            cards.push(kpi('เบลอ (กลาง)',
                (quiet || s.blur_px_median === null || s.blur_px_median === undefined)
                    ? '—' : fmt(s.blur_px_median, 2), 'px',
                quiet ? 'muted' : blurTone(s.blur_px_median)));
            cards.push(kpi('เบลอน้อยสุด',
                (quiet || s.blur_px_min === null || s.blur_px_min === undefined)
                    ? '—' : fmt(s.blur_px_min, 2), 'px',
                quiet ? 'muted' : blurTone(s.blur_px_min)));
            cards.push(kpi('exposure ที่เบลอ ≤1px',
                s.max_exposure_us_1px ? Math.round(s.max_exposure_us_1px) : '—', 'µs',
                s.max_exposure_us_1px ? '' : 'muted'));
            cards.push(kpi('วัตถุขยับต่อเฟรม',
                s.shift_px_median === null || s.shift_px_median === undefined
                    ? '—' : fmt(s.shift_px_median, 2), 'px', quiet ? 'warn' : ''));
        }
        box.innerHTML = cards.join('');

        // เฟรมที่ทิ้ง = กระป๋องที่หายไปจากชุดทดสอบ ⇒ ต้องเด่น ห้ามซ่อนในบรรทัดยาว ๆ
        if (drop) {
            if (m.dropped) {
                var total = (m.saved || d.total || 0) + m.dropped;
                var pct = total ? Math.round(m.dropped * 100 / total) : 0;
                drop.style.display = '';
                drop.innerHTML = '⚠️ <b>ทิ้งไป ' + m.dropped + ' เฟรม (' + pct
                    + '% ของที่กล้องส่งมา)</b> เพราะดิสก์เขียนไม่ทัน — '
                    + 'เก็บได้จริงแค่ ' + (m.saved || d.total) + ' ภาพ. '
                    + 'ตั้ง <b>"เก็บ 1 ใน N เฟรม"</b> ให้สูงขึ้น หรือลด ROI '
                    + 'แล้วถ่ายใหม่ จะได้ภาพที่กระจายทั่วช่วงเวลาแทนที่จะขาดเป็นช่วง ๆ';
            } else {
                drop.style.display = 'none';
            }
        }

        renderDiag(d.diag);

        if (!d.metrics_ready) {
            note.className = 'hik-note warn';
            note.innerHTML = 'ยังไม่ได้วัดความคมของชุดนี้ — กด <b>“📏 วัดความคม/ความเบลอ”</b> '
                + 'เพื่อให้ระบบคำนวณความเร็วของวัตถุและระยะเบลอจากภาพจริง';
            return;
        }
        if (!s.speed_px_s) {
            note.className = 'hik-note warn';
            note.innerHTML = '<b>วัดความเร็วของวัตถุไม่ได้</b> (พบเฟรมที่มีของเคลื่อนไหว '
                + (s.moving_frames || 0) + '/' + s.total_frames + ') ⇒ บอกระยะเบลอเป็นตัวเลขไม่ได้'
                + '<br>ให้วัตถุผ่าน<b>กลางเฟรม</b> · ฉากหลังต้องนิ่ง · '
                + 'วัตถุต้องกินพื้นที่มากกว่า 0.4% แต่ไม่เกิน 80% ของเฟรม แล้วถ่ายใหม่';
            return;
        }
        // วัตถุขยับน้อยกว่าพื้นสัญญาณรบกวน = **เราไม่รู้** ไม่ใช่ "ช้าและคมดี"
        // ห้ามขึ้นไฟเขียว เพราะการทดสอบนี้ยังไม่ได้ตอบคำถามเรื่องความเบลอเลย
        if (s.motion === 'negligible') {
            note.className = 'hik-note warn';
            note.innerHTML = '⚠️ <b>วัตถุแทบไม่ได้เคลื่อนที่ — การทดสอบนี้ยังไม่ได้ตอบ'
                + 'คำถามเรื่องความเบลอ</b><br>วัดได้ <b>' + fmt(s.shift_px_median, 2)
                + ' พิกเซลต่อเฟรม</b> (ต่ำกว่าเกณฑ์ที่วัดได้จริง ' + s.min_shift_px + ' px) '
                + '⇒ ตัวเลขความเร็วและระยะเบลอเป็นสัญญาณรบกวน ไม่ใช่การเคลื่อนที่'
                + '<br>ชุดนี้ยัง<b>ใช้ดูความคม/โฟกัส/แสงได้</b> — แต่ถ้าจะวัดความเบลอ '
                + 'ต้องเลื่อนวัตถุให้เร็วขึ้นมาก (ไลน์จริงที่ 450 ใบ/นาที ≈ 7,800 px/วินาที)';
            return;
        }
        var blur = s.blur_px_median;
        var tone = blurTone(blur);
        var verdict = blur <= 1 ? '✅ คมพอ — กล้องหยุดการเคลื่อนที่ได้ที่ exposure นี้'
            : (blur <= 3 ? '⚠️ พอใช้ — ยังเห็นรอยเปื้อนจากการเคลื่อนที่'
                : '❌ เบลอเกินไป — รายละเอียดรอยบุบหายไปกับการเคลื่อนที่');
        var body = '<b>' + verdict + '</b><br>'
            + 'วัตถุเคลื่อนที่ <b>' + Math.round(s.speed_px_s) + ' px/วินาที</b>'
            + (s.speed_mm_s ? ' (' + s.speed_mm_s + ' mm/วิ)' : '')
            + ' × exposure ' + Math.round(s.exposure_us) + ' µs ⇒ เบลอ <b>'
            + fmt(blur, 2) + ' พิกเซล</b> · ภาพที่คมที่สุดคือ <b>' + (s.best_file || '—') + '</b>';
        if (s.max_exposure_us_1px) {
            body += '<br>📏 ที่ความเร็วนี้ exposure ต้องไม่เกิน <b>'
                + Math.round(s.max_exposure_us_1px) + ' µs</b> จึงจะเบลอ ≤1 px';
            if (s.light_factor_needed && s.light_factor_needed > 1.2) {
                body += ' ⇒ ต้องเพิ่มไฟอีก <b>~' + s.light_factor_needed
                    + ' เท่า</b> เพื่อให้ภาพ<b>สว่างเท่าเดิม</b>';
                if (s.light_factor_usable
                    && s.light_factor_usable > s.light_factor_needed * 1.2) {
                    body += ' · และ <b>~' + s.light_factor_usable
                        + ' เท่า</b> ถ้าอยากให้สว่างพอใช้งานจริง (~' + s.target_mean
                        + '/255 — ตอนนี้วัดได้ ' + fmt(s.mean_median) + ')';
                }
            } else if (s.light_factor_needed) {
                body += ' ⇒ <b>exposure ปัจจุบันผ่านแล้ว</b>';
            }
        }
        note.className = 'hik-note ' + (tone === 'good' ? 'good' : (tone === 'warn' ? 'warn' : 'bad'));
        note.innerHTML = body;
    }

    /**
     * "เฟรมที่ควรได้ หายไปตรงไหน" — แยก 3 สาเหตุที่วิธีแก้คนละเรื่องกัน
     * เฟรมหายระหว่างทาง (เครือข่าย) · ดิสก์เขียนไม่ทัน · กล้องส่งมาช้าเอง
     */
    function renderDiag(g) {
        var el = $('hikGalDiag');
        if (!el) { return; }
        if (!g) { el.style.display = 'none'; return; }
        var tone = g.cause === 'ok' ? 'good'
            : (g.cause === 'transport' || g.cause === 'framerate_cap' ? 'bad' : 'warn');
        var icon = g.cause === 'ok' ? '✅' : (tone === 'bad' ? '⛔' : '⚠️');
        var kv = [
            ['กล้องส่งมาจริง', fmt(g.delivered_fps) + ' fps'],
            ['เพดานสาย GigE', g.gige_ceiling_fps ? '~' + g.gige_ceiling_fps + ' fps' : '—'],
            ['Jumbo Frame', g.jumbo === undefined ? '—' : (g.jumbo ? 'เปิด' : 'ปิด')],
            ['packet size', g.packet_size || '—'],
            ['เฟรมหายระหว่างทาง', g.lost_transport === null
                || g.lost_transport === undefined ? '—' : g.lost_transport],
            ['แพ็กเก็ตหาย', g.lost_packets === null
                || g.lost_packets === undefined ? '—' : g.lost_packets],
            ['ทิ้งเพราะดิสก์', g.dropped_disk],
            ['จำกัดอัตราเฟรม', g.framerate_enable
                ? ('เปิดที่ ' + fmt(g.framerate) + ' fps') : 'ปิด'],
            ['encode/เขียน (ms)', fmt((g.stage_ms || {}).encode, 1) + ' / '
                + fmt((g.stage_ms || {}).write, 1)]
        ];
        // แสดง **ทุกสาเหตุที่พบ** เรียงตามลำดับที่ควรลงมือแก้ — เคสจริงมีสองปัญหา
        // พร้อมกันได้ (กล้องส่งช้า + ดิสก์ตามไม่ทัน) ถ้าโชว์แค่ข้อแรกผู้ใช้จะแก้ไม่ครบ
        var list = (g.issues && g.issues.length) ? g.issues
            : [{ cause: g.cause, text: g.text, fix: g.fix }];
        var many = list.length > 1;
        var body = list.map(function (it, i) {
            return (many ? '<b>' + (i + 1) + '.</b> ' : '') + '<b>' + it.text + '</b>'
                + (it.fix ? '<br><span' + (many ? ' style="padding-left:16px"' : '')
                    + '>👉 ' + it.fix + '</span>' : '');
        }).join('<br>');
        el.style.display = '';
        el.className = 'hik-note ' + tone;
        el.innerHTML = icon + ' ' + body
            + '<div class="hik-diag-grid">'
            + kv.map(function (p) {
                return '<div><span>' + p[0] + '</span> <b>' + p[1] + '</b></div>';
            }).join('') + '</div>';
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

    function kv(label, value, hl) {
        return '<dt>' + label + '</dt><dd' + (hl ? ' class="hl"' : '') + '>' + value + '</dd>';
    }

    function renderGrid() {
        var grid = $('hikGalGrid');
        if (!grid) { return; }
        var name = state.session;
        if (!state.rows.length) {
            grid.innerHTML = '<div class="hik-empty">ชุดนี้ไม่มีภาพ</div>';
            $('hikGalMore').style.display = 'none';
            return;
        }
        var base = '/api/camera/hik/bursts/' + encodeURIComponent(name);
        grid.innerHTML = state.rows.map(function (r) {
            var tags = badge(r) + kindBadge(r);
            var info = '<div class="hik-card-info">'
                + '<div class="hik-card-name">' + r.file + '</div>'
                + '<dl class="hik-kv">'
                + kv('คะแนนคม', fmt(r.sharp, 0))
                + kv('แกน x/y', fmt(r.ratio, 2))
                + kv('เบลอ', (r.blur_px === undefined || r.blur_px === null)
                    ? '—' : r.blur_px + ' px', true)
                + kv('ความเร็ว', r.speed_px_s ? Math.round(r.speed_px_s) + ' px/s' : '—')
                + '</dl></div>';
            return '<div class="hik-card' + (state.sel[r.file] ? ' sel' : '') + '" data-f="' + r.file + '">'
                + '<div class="hik-shot-wrap">'
                + '<img loading="lazy" src="' + base + '/thumb/' + r.file + '" '
                + 'alt="เฟรม ' + r.file + ' จากชุดถ่ายรัว" data-zoom="' + r.file + '">'
                + '<div class="hik-card-top">'
                + '<input type="checkbox" data-sel="' + r.file + '" title="เลือกภาพนี้"'
                + (state.sel[r.file] ? ' checked' : '') + '>'
                + '<button class="hik-x" data-del="' + r.file + '" title="ลบภาพนี้">&times;</button>'
                + '</div>'
                + (tags ? '<div class="hik-card-tags">' + tags + '</div>' : '')
                + '</div>' + info + '</div>';
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
        var kpis = $('hikFrameKpis');
        if (kpis) {
            kpis.innerHTML = row ? [
                kpi('คะแนนคม', fmt(row.sharp, 0), ''),
                kpi('แกน x/y', fmt(row.ratio, 2), ''),
                kpi('ความสว่าง', fmt(row.mean), '/255'),
                kpi('เลื่อนจากเฟรมก่อน', row.shift_px ? row.shift_px : '—', 'px',
                    row.shift_px ? '' : 'muted'),
                kpi('เบลอ', (row.blur_px === undefined || row.blur_px === null)
                    ? '—' : row.blur_px, 'px', blurTone(row.blur_px))
            ].join('') : '';
        }
        $('hikFrameMeta').textContent = row
            ? (row.roi_src === 'frame'
                ? '⚠️ ตัวเลขนี้วัดจากทั้งเฟรม เพราะไม่พบวัตถุที่เคลื่อนไหว'
                : (row.roi_src === 'moving'
                    ? 'ตัวเลขวัดเฉพาะในกรอบสีฟ้า (บริเวณที่เคลื่อนไหว) ไม่ใช่ทั้งเฟรม'
                    : 'ยังไม่ได้วัดชุดนี้ — กด “📏 วัดความคม/ความเบลอ” ก่อน'))
            : '';
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
            ['hikBurstSeconds', 'hikBurstEveryN'].forEach(function (id) {
                var el = $(id);
                if (el) { el.addEventListener('input', refreshEstimate); }
            });
            Array.prototype.forEach.call(
                document.querySelectorAll('#hikEveryPresets [data-every]'), function (btn) {
                    btn.addEventListener('click', function () {
                        var inp = $('hikBurstEveryN');
                        if (inp) { inp.value = btn.dataset.every; }
                        refreshEstimate();
                    });
                });
            refreshEstimate();

            var pause = $('hikBurstPause');
            if (pause) {
                try { pause.checked = localStorage.getItem('hik.pauseInf') === '1'; }
                catch (e) { /* โหมดส่วนตัว/ปิด storage — ไม่ใช่เรื่องผิดปกติ */ }
                pause.addEventListener('change', function () {
                    try { localStorage.setItem('hik.pauseInf', pause.checked ? '1' : '0'); }
                    catch (e) { /* ไม่จำก็ไม่เป็นไร */ }
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
            clearInterval(state.shapeTimer);
            if (active) {
                pollCameraShape();
                // fps ขยับตาม ROI/exposure ที่ผู้ใช้แก้ระหว่างทาง ⇒ ตัวประมาณต้องตามด้วย
                state.shapeTimer = setInterval(pollCameraShape, 4000);
            } else {
                state.fps = null;
                refreshEstimate();
            }
        }
    };
})();
