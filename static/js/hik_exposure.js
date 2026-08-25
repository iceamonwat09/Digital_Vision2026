/*
 * hik_exposure.js — โหมด "ไล่ exposure" ของแท็บกล้องอุตสาหกรรม
 *
 * ทำไมแยกไฟล์: `hik_camera.js` คุมแผงตั้งค่า + สถิติสด · `hik_burst.js` คุม
 * แกลเลอรีถ่ายรัว — ทั้งสองยาวและคุมสถานะของตัวเองอยู่แล้ว. โมดูลนี้ปิด
 * เปิดออกมาแค่ window.HikExposure และไม่แตะตัวแปรของโมดูลอื่นเลย
 *
 * โจทย์ที่หน้าจอนี้ตอบ: เบลอบนไลน์ = ความเร็ว × exposure ⇒ ต้องกด exposure ลง
 * ⇒ ภาพมืด ⇒ ไม่มีไฟก็ต้องดัน gain ⇒ แลกด้วย noise ⇒ **จุดที่โมเดลเชื่อไม่ได้
 * อยู่ตรงไหน** ซึ่งเดาไม่ได้ ต้องวัด — และต้องวัดทั้งสองด้าน (NG จริง / NG ปลอม)
 */
(function () {
    'use strict';

    var $ = function (id) { return document.getElementById(id); };
    var LINE_PX_S = 7800.0;          // 450 ใบ/นาที ที่ 0.082 mm/px (ค่าเดียวกับฝั่ง Python)
    var BLUR_TARGET_PX = 4.0;
    var TARGET_MEAN = 80.0;          // ต้องตรงกับ hik_exposure.TARGET_MEAN

    var state = { running: false, timer: null, session: null, sessions: [] };

    // ── แถบจูนเร็ว ────────────────────────────────────────────────────
    // เป้าหมาย: หน้างานจริง กล้องติดตั้งแล้ว ต้องการ "ปรับนิดเดียวแล้วถ่ายซ้ำ"
    // โดยไม่ต้องเปิดแผงตั้งค่า 19 ตัวแล้วกดบันทึกทุกครั้ง
    var TUNE_US_MIN = 50, TUNE_US_MAX = 20000;   // ช่วงที่มีความหมายกับงานนี้
    var tune = {
        us: null, gain: null, gmin: 0, gmax: 24,
        umin: TUNE_US_MIN, umax: TUNE_US_MAX,
        timer: null, lastScore: null, busy: false, ready: false
    };

    /** สไลเดอร์เป็น **log** — ช่วง 50-20,000 µs ถ้าเป็นเชิงเส้นจะจูนช่วงสั้นไม่ได้เลย */
    function posToUs(pos) {
        var r = Math.log(tune.umax / tune.umin);
        return tune.umin * Math.exp(r * (pos / 1000));
    }

    function usToPos(us) {
        var r = Math.log(tune.umax / tune.umin);
        return Math.max(0, Math.min(1000, Math.round(
            1000 * Math.log(Math.max(us, tune.umin) / tune.umin) / r)));
    }

    function blurPx(us) { return LINE_PX_S * us / 1e6; }

    function paintTune() {
        if (tune.us == null) { return; }
        var b = blurPx(tune.us);
        if ($('hikTuneUs')) { $('hikTuneUs').textContent = Math.round(tune.us); }
        if ($('hikTuneBlur')) { $('hikTuneBlur').textContent = b.toFixed(1); }
        var box = document.querySelector('.hik-tune-readout');
        if (box) {
            box.classList.toggle('blur-ok', b <= BLUR_TARGET_PX);
            box.classList.toggle('blur-bad', b > BLUR_TARGET_PX * 2);
        }
        if ($('hikTuneUsHint')) {
            $('hikTuneUsHint').textContent = Math.round(tune.umin) + '–'
                + Math.round(tune.umax) + ' µs';
        }
        if ($('hikTuneGainHint') && tune.gain != null) {
            $('hikTuneGainHint').textContent = tune.gain.toFixed(1) + ' dB (สูงสุด '
                + tune.gmax.toFixed(1) + ')';
        }
        var e = $('hikTuneExp');
        if (e && document.activeElement !== e) { e.value = usToPos(tune.us); }
        var g = $('hikTuneGain');
        if (g && tune.gain != null && document.activeElement !== g) {
            g.value = Math.round(tune.gain * 10);
        }
    }

    /**
     * ส่งค่าลงกล้องแบบหน่วง — ลากสไลเดอร์ไม่ควรยิงคำสั่งทุกพิกเซล.
     *
     * ⚠️ ``save`` ต้องเป็น false ระหว่างลาก — ไฟล์ค่าตั้ง (`HIK_SETTINGS_FILE`)
     * **ทับ `HIK_DEFAULTS` เสมอ** ถ้าเขียนทุกครั้งที่ขยับ ค่าที่ค้างไว้ตอนลากผ่าน
     * จะกลายเป็นค่าถาวรของเครื่องนั้น. บันทึกจริงตอน **ปล่อยสไลเดอร์** เท่านั้น
     */
    function pushTune(save) {
        clearTimeout(tune.timer);
        tune.timer = setTimeout(function () {
            var body = {};
            if (tune.us != null) { body.exposure_us = Math.round(tune.us); }
            if (tune.gain != null) { body.gain_db = Math.round(tune.gain * 100) / 100; }
            fetch('/api/camera/hik/params', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ params: body, save: !!save })
            }).then(function (r) { return r.json(); }).then(function (d) {
                // กล้องปัดค่าเป็นขั้นของตัวเอง ⇒ อ่านค่าที่ **ตั้งได้จริง** กลับมาแสดง
                //
                // ⚠️ **เฉพาะตอนที่ผู้ใช้ปล่อยมือแล้วเท่านั้น** (save === true) —
                // ถ้ารับค่าที่ปัดแล้วกลับมาระหว่างลาก สไลเดอร์จะกระโดดสู้กับนิ้ว
                // ผู้ใช้ (เจอจริงตอนขับเบราว์เซอร์: ลากไป 1000 µs แล้วเด้งกลับ 5000)
                if (!save) { return; }
                var ap = (d && d.applied) || {};
                if (ap.exposure_us && ap.exposure_us.value) { tune.us = ap.exposure_us.value; }
                if (ap.gain_db && ap.gain_db.value != null) { tune.gain = ap.gain_db.value; }
                paintTune();
            }).catch(function () { /* แถบสถานะสดจะบอกเองว่ากล้องหลุด */ });
        }, 220);
    }

    function loadTuneRanges() {
        fetch('/api/camera/hik/params').then(function (r) { return r.json(); })
            .then(function (d) {
                var p = (d && d.params) || {};
                var e = p.exposure_us, g = p.gain_db;
                if (e && e.supported !== false) {
                    tune.umin = Math.max(TUNE_US_MIN, e.min || TUNE_US_MIN);
                    tune.umax = Math.min(TUNE_US_MAX, e.max || TUNE_US_MAX);
                    tune.us = e.value;
                }
                if (g && g.supported !== false) {
                    tune.gmin = g.min || 0;
                    tune.gmax = g.max || 24;
                    tune.gain = g.value;
                    var gs = $('hikTuneGain');
                    if (gs) { gs.min = Math.round(tune.gmin * 10); gs.max = Math.round(tune.gmax * 10); }
                }
                tune.ready = tune.us != null;
                paintTune();
            }).catch(function () { /* ยังไม่ได้เริ่มกล้อง */ });
    }

    /**
     * ชดเชยความสว่างด้วยเกน — สูตรเดียวกับ `hik_exposure.gain_for()` ฝั่ง Python
     * (20·log10) **แก้ที่เดียวไม่พอ ต้องแก้ทั้งสองฝั่ง** — มีเทสต์เทียบไว้
     */
    function autoGain() {
        fetch('/api/camera/hik/status').then(function (r) { return r.json(); })
            .then(function (d) {
                var mean = d && d.stats && d.stats.mean_brightness;
                if (!mean || mean <= 0.5 || tune.gain == null) {
                    msgTune('ภาพมืดเกินกว่าจะคำนวณเกนได้ — เปิดรูรับแสงเลนส์ก่อน', 'bad');
                    return;
                }
                var want = tune.gain + 20 * Math.log(TARGET_MEAN / mean) / Math.LN10;
                var capped = want > tune.gmax;
                tune.gain = Math.max(tune.gmin, Math.min(tune.gmax, want));
                paintTune();
                pushTune(true);
                msgTune(capped
                    ? '⚠️ ต้องใช้เกน ' + want.toFixed(1) + ' dB แต่เพดานอยู่ที่ '
                      + tune.gmax.toFixed(1) + ' ⇒ ภาพจะยังมืดกว่าเป้า'
                    : 'ตั้งเกนเป็น ' + tune.gain.toFixed(1) + ' dB แล้ว', capped ? 'bad' : 'good');
            });
    }

    function msgTune(html, tone) {
        var el = $('hikTuneScore');
        if (!el) { return; }
        el.innerHTML = html || '';
        el.style.color = tone === 'bad' ? '#dc2626'
            : (tone === 'good' ? '#15803d' : '#64748b');
    }

    /**
     * คะแนนความคมของภาพที่เพิ่งถ่าย — ให้ตัวเลขเทียบได้แทนการ "ดูด้วยตา"
     *
     * ⚠️ ย่อ **1/2** ก่อนวัด: สัญญาณรบกวนเป็นความถี่สูง การย่อช่วยกดมันลง
     * (ไม่งั้นดันเกนขึ้นแล้วคะแนนจะ "คมขึ้น" ทั้งที่ภาพแย่ลง = ตัวเลขที่หลอก)
     * แต่ **ย่อมากกว่านี้ไม่ได้** — เบลอ 4 px บนเซนเซอร์ 2448 เหลือ ~2 px ในภาพ
     * ที่ย่อลงจอ 1280 แล้ว ถ้าย่ออีก 4 เท่าจะกลืนหายพอดีกับสิ่งที่กำลังจะวัด
     */
    function sharpness(img) {
        var w = Math.max(32, Math.round(img.naturalWidth / 2));
        var h = Math.max(32, Math.round(img.naturalHeight / 2));
        var c = document.createElement('canvas');
        c.width = w; c.height = h;
        var ctx = c.getContext('2d');
        ctx.drawImage(img, 0, 0, w, h);
        var x0 = Math.round(w * 0.2), x1 = Math.round(w * 0.8);
        var y0 = Math.round(h * 0.2), y1 = Math.round(h * 0.8);
        var d = ctx.getImageData(x0, y0, x1 - x0, y1 - y0).data;
        var cw = x1 - x0, ch = y1 - y0, sum = 0, n = 0;
        for (var y = 1; y < ch; y++) {
            for (var x = 1; x < cw; x++) {
                var i = (y * cw + x) * 4;
                var g = d[i], gx = g - d[i - 4], gy = g - d[i - cw * 4];
                sum += gx * gx + gy * gy;
                n++;
            }
        }
        return n ? sum / n : 0;
    }

    function testShot() {
        if (tune.busy) { return; }
        tune.busy = true;
        msgTune('⏳ กำลังถ่าย…');
        fetch('/api/camera/hik/shot', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ detect: false })
        }).then(function (r) { return r.json(); }).then(function (d) {
            tune.busy = false;
            if (d.status !== 'ok') { msgTune('❌ ' + (d.message || 'ถ่ายไม่สำเร็จ'), 'bad'); return; }
            var img = $('hikTuneImg');
            img.onload = function () {
                var score = sharpness(img);
                if (score < 1.0) {
                    tune.lastScore = null;
                    msgTune('⚠️ ภาพแทบไม่มีรายละเอียดให้วัด (คะแนน '
                        + score.toFixed(2) + ') — เปิดฝาเลนส์/รูรับแสง หรือเล็งให้ตรงกระป๋องก่อน',
                        'bad');
                    return;
                }
                var prev = tune.lastScore;
                var arrow = '';
                if (prev != null && prev > 0) {
                    var pct = (score / prev - 1) * 100;
                    arrow = Math.abs(pct) < 2 ? ' <b>=</b> เท่าเดิม'
                        : (pct > 0 ? ' <b style="color:#15803d">▲ ' + pct.toFixed(0) + '%</b>'
                                   : ' <b style="color:#dc2626">▼ ' + pct.toFixed(0) + '%</b>');
                }
                tune.lastScore = score;
                msgTune('คะแนนคม <b>' + score.toFixed(0) + '</b>' + arrow
                    + '<br><span class="control-hint">' + Math.round(tune.us) + ' µs · เกน '
                    + (tune.gain != null ? tune.gain.toFixed(1) : '—') + ' dB</span>');
            };
            img.src = d.image;
            img.style.display = 'block';
        }).catch(function (e) { tune.busy = false; msgTune('❌ ' + e, 'bad'); });
    }


    function msg(html, tone) {
        var el = $('hikExpMsg');
        if (!el) { return; }
        el.innerHTML = html || '';
        el.style.color = tone === 'bad' ? '#dc2626'
            : (tone === 'good' ? '#15803d' : '#64748b');
    }

    function parseList(text) {
        return (text || '').replace(/,/g, ' ').split(/\s+/)
            .map(function (v) { return parseFloat(v); })
            .filter(function (v) { return isFinite(v) && v > 0; });
    }

    /** ตัวประมาณ: ค่าที่กรอกจะให้เบลอกี่พิกเซลที่ความเร็วไลน์ — เห็นก่อนกดถ่าย */
    function refreshEstimate() {
        var el = $('hikExpEst');
        if (!el) { return; }
        var list = parseList($('hikExpList') ? $('hikExpList').value : '');
        var frames = parseInt(($('hikExpFrames') || {}).value, 10) || 5;
        if (!list.length) {
            el.innerHTML = 'ใช้ชุดมาตรฐาน · เบลอเป้าหมาย <b>' + BLUR_TARGET_PX
                + ' px</b> ที่ 450 ใบ/นาที = exposure <b>512 µs</b>';
            return;
        }
        var parts = list.slice().sort(function (a, b) { return b - a; }).map(function (us) {
            var blur = LINE_PX_S * us / 1e6;
            var cls = blur <= BLUR_TARGET_PX ? 'ok' : (blur <= BLUR_TARGET_PX * 2 ? 'warn' : 'bad');
            return '<span class="hik-exp-chip ' + cls + '">' + Math.round(us)
                + ' µs → ' + blur.toFixed(1) + ' px</span>';
        });
        el.innerHTML = parts.join(' ') + '<br><span class="control-hint">'
            + list.length + ' ขั้น × ' + frames + ' เฟรม · ใช้เวลาประมาณ '
            + Math.round(list.length * 12) + '-' + Math.round(list.length * 25) + ' วินาที</span>';
    }

    // ─────────────────────────────────────────────────── เริ่ม/ติดตามงาน
    function start() {
        if (state.running) {
            fetch('/api/camera/hik/exposure', { method: 'DELETE' })
                .then(function () { msg('สั่งยกเลิกแล้ว — ค่ากล้องถูกคืนอัตโนมัติ'); });
            return;
        }
        var body = {
            role: ($('hikExpRole') || {}).value || 'ng',
            frames: parseInt(($('hikExpFrames') || {}).value, 10) || 5,
            exposures: parseList(($('hikExpList') || {}).value)
        };
        msg('กำลังเริ่ม…');
        fetch('/api/camera/hik/exposure', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
            .then(function (res) {
                if (!res.ok || res.d.status !== 'ok') {
                    msg('❌ ' + (res.d.message || 'เริ่มไม่สำเร็จ'), 'bad');
                    return;
                }
                state.session = res.d.session;
                state.running = true;
                setButton();
                poll();
            }).catch(function (e) { msg('❌ ' + e, 'bad'); });
    }

    function setButton() {
        var b = $('hikExpBtn');
        if (!b) { return; }
        b.textContent = state.running ? '⏹️ ยกเลิกการไล่ค่า' : '🔦 เริ่มไล่ exposure';
        b.classList.toggle('btn-danger-soft', state.running);
    }

    function poll() {
        clearTimeout(state.timer);
        fetch('/api/camera/hik/exposure').then(function (r) { return r.json(); })
            .then(function (d) {
                var job = d.job;
                if (job && job.running && job.kind === 'exposure') {
                    msg('⏳ กำลังไล่ค่า ' + job.done + '/' + job.total + ' ขั้น · '
                        + job.elapsed_s + ' วิ <b>— การตรวจสดหยุดอยู่</b>');
                    state.timer = setTimeout(poll, 1200);
                    return;
                }
                state.running = false;
                setButton();
                if (job && job.error) {
                    msg('❌ ' + job.error, 'bad');
                } else {
                    msg('✅ เสร็จแล้ว — ค่ากล้องถูกคืนเป็นค่าเดิม', 'good');
                    openResults(state.session);
                }
            }).catch(function () {
                state.running = false;
                setButton();
            });
    }

    // ────────────────────────────────────────────────────── หน้าผลลัพธ์
    function cell(v, digits) {
        if (v === null || v === undefined) { return '<td class="dim">—</td>'; }
        return '<td>' + (typeof v === 'number' ? v.toFixed(digits || 0) : v) + '</td>';
    }

    function renderTable(data) {
        var rows = data.rows || [];
        var role = data.role;
        var head = '<tr><th>exposure<br>(µs)</th><th>gain<br>(dB)</th><th>สว่าง<br>/255</th>'
            + '<th>สัญญาณ<br>รบกวน σ</th><th>SNR<br>(dB)</th>'
            + '<th>' + (role === 'ng' ? 'เจอรอยบุบ' : 'NG ปลอม') + '<br>(เฟรม)</th>'
            + '<th>conf<br>สูงสุด</th><th>เบลอที่ไลน์<br>(px)</th></tr>';
        var body = rows.map(function (r) {
            if (r.error) {
                return '<tr class="bad"><td>' + Math.round(r.exposure_us)
                    + '</td><td colspan="7">' + r.error + '</td></tr>';
            }
            var hit = r.frames_with_defect;
            var good = role === 'ng' ? (r.defect_rate === 1) : (hit === 0);
            var blur = r.blur_at_line_px;
            var cls = r.gain_capped ? 'warn' : (good ? 'ok' : 'bad');
            return '<tr class="' + cls + '">'
                + '<td><b>' + Math.round(r.exposure_us) + '</b></td>'
                + '<td>' + (r.gain_db != null ? r.gain_db.toFixed(1) : '—')
                + (r.gain_capped ? ' <span class="hik-exp-cap">ชนเพดาน</span>' : '') + '</td>'
                + cell(r.mean, 0)
                + (r.moved ? '<td class="dim" title="ฉากขยับ — วัดสัญญาณรบกวนไม่ได้">— ขยับ</td>'
                    : cell(r.noise, 2))
                + cell(r.snr_db, 1)
                + '<td>' + (hit != null ? hit + '/' + (r.frames || 0) : '—') + '</td>'
                + cell(r.conf_max, 2)
                + (blur != null ? '<td class="' + (blur <= BLUR_TARGET_PX ? 'ok' : 'bad')
                    + '">' + blur.toFixed(2) + '</td>' : '<td class="dim">—</td>')
                + '</tr>';
        }).join('');
        return '<table class="hik-exp-table">' + head + body + '</table>';
    }

    function renderVerdict(data) {
        var s = data.summary || {};
        var c = data.combined;
        var role = data.role === 'ng' ? 'กระป๋องมีรอยบุบ' : 'กระป๋องดี';
        var out = '<div class="hik-exp-head">ชุด ' + data.name + ' · ' + role
            + ' · ' + (data.created || '') + '</div>';
        out += '<div class="hik-exp-line">' + (s.headline || '—') + '</div>';
        if (s.criterion) {
            out += '<div class="control-hint">เกณฑ์ผ่านของด้านนี้: ' + s.criterion + '</div>';
        }
        if (s.note_bottom) {
            out += '<div class="hik-exp-note">⚠️ ' + s.note_bottom + '</div>';
        }
        if (c) {
            out += '<div class="hik-exp-combined">🎯 ' + c.headline
                + (c.blur_at_line_px != null
                    ? ' ⇒ เบลอที่ 450 ใบ/นาที <b>' + c.blur_at_line_px.toFixed(2) + ' px</b>' : '')
                + '</div>';
        } else {
            out += '<div class="hik-exp-note">⚠️ ยังมีด้านเดียว — วัดอีกด้าน ('
                + (data.role === 'ng' ? 'กระป๋องดี' : 'กระป๋องมีรอยบุบ')
                + ') ด้วย จึงจะสรุปได้ว่า exposure ไหนใช้ได้จริง</div>';
        }
        return out;
    }

    function renderShots(data) {
        var rows = (data.rows || []).filter(function (r) { return r.image; });
        if (!rows.length) { return ''; }
        return '<div class="hik-exp-shot-grid">' + rows.map(function (r) {
            return '<figure><img loading="lazy" src="/api/camera/hik/exposures/'
                + encodeURIComponent(data.name) + '/image/' + encodeURIComponent(r.image)
                + '" alt="ภาพที่ exposure ' + Math.round(r.exposure_us) + ' ไมโครวินาที">'
                + '<figcaption>' + Math.round(r.exposure_us) + ' µs · gain '
                + (r.gain_db != null ? r.gain_db.toFixed(1) : '—') + ' dB</figcaption></figure>';
        }).join('') + '</div><p class="control-hint">ภาพเก็บที่ <b>ความละเอียดเต็ม ไม่ย่อ</b> —'
            + ' การย่อคือการเฉลี่ยพิกเซล ซึ่งจะ<b>ลบสัญญาณรบกวนทิ้ง</b> คือสิ่งที่กำลังวัดอยู่พอดี'
            + ' (กดที่ภาพเพื่อดูเต็ม)</p>';
    }

    function openResults(prefer) {
        fetch('/api/camera/hik/exposures').then(function (r) { return r.json(); })
            .then(function (d) {
                state.sessions = d.sessions || [];
                var pick = $('hikExpPick');
                if (pick) {
                    pick.innerHTML = state.sessions.map(function (s) {
                        return '<option value="' + s.name + '">' + s.name + ' · '
                            + (s.role === 'ng' ? 'NG' : 'ดี') + '</option>';
                    }).join('');
                    if (prefer) { pick.value = prefer; }
                }
                if (!state.sessions.length) {
                    msg('ยังไม่มีผลที่วัดไว้');
                    return;
                }
                show((pick && pick.value) || state.sessions[0].name);
            });
    }

    function show(name) {
        state.session = name;
        fetch('/api/camera/hik/exposures/' + encodeURIComponent(name))
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.status !== 'ok') { msg('❌ ' + (d.message || 'อ่านผลไม่ได้'), 'bad'); return; }
                var data = d.session;
                $('hikExpVerdict').innerHTML = renderVerdict(data);
                $('hikExpTableWrap').innerHTML = renderTable(data);
                $('hikExpShots').innerHTML = renderShots(data);
                $('hikExpOverlay').style.display = 'flex';
            });
    }

    window.HikExposure = {
        init: function () {
            var b = $('hikExpBtn');
            if (b) { b.addEventListener('click', start); }
            var r = $('hikExpResultBtn');
            if (r) { r.addEventListener('click', function () { openResults(state.session); }); }
            var c = $('hikExpClose');
            if (c) { c.addEventListener('click', function () { $('hikExpOverlay').style.display = 'none'; }); }
            var pick = $('hikExpPick');
            if (pick) { pick.addEventListener('change', function () { show(pick.value); }); }
            var del = $('hikExpDelete');
            if (del) {
                del.addEventListener('click', function () {
                    if (!state.session || !window.confirm('ลบชุด ' + state.session + ' ?')) { return; }
                    fetch('/api/camera/hik/exposures/' + encodeURIComponent(state.session),
                        { method: 'DELETE' }).then(function () {
                            state.session = null;
                            openResults(null);
                        });
                });
            }
            var exp = $('hikTuneExp');
            if (exp) {
                exp.addEventListener('input', function () {
                    tune.us = posToUs(parseFloat(exp.value));
                    paintTune();
                    pushTune(false);
                });
                exp.addEventListener('change', function () { pushTune(true); });
            }
            var gsl = $('hikTuneGain');
            if (gsl) {
                gsl.addEventListener('input', function () {
                    tune.gain = parseFloat(gsl.value) / 10;
                    paintTune();
                    pushTune(false);
                });
                gsl.addEventListener('change', function () { pushTune(true); });
            }
            Array.prototype.forEach.call(
                document.querySelectorAll('.hik-tune-nudge button'), function (b) {
                    b.addEventListener('click', function () {
                        if (tune.us == null) { return; }
                        tune.us = Math.max(tune.umin, Math.min(tune.umax,
                            tune.us * parseFloat(b.dataset.mul)));
                        paintTune();
                        pushTune(true);
                    });
                });
            var ag = $('hikTuneAuto');
            if (ag) { ag.addEventListener('click', autoGain); }
            var ts = $('hikTuneShot');
            if (ts) { ts.addEventListener('click', testShot); }
            var ti = $('hikTuneImg');
            if (ti) {
                ti.addEventListener('click', function () {
                    if (ti.src) { window.open(ti.src, '_blank'); }
                });
            }
            paintTune();

            ['hikExpList', 'hikExpFrames'].forEach(function (id) {
                var el = $(id);
                if (el) { el.addEventListener('input', refreshEstimate); }
            });
            refreshEstimate();
        },

        /** ปุ่มใช้ได้เฉพาะตอนกล้องทำงาน (หน้าผลเปิดดูได้ตลอด) */
        setActive: function (active) {
            var b = $('hikExpBtn');
            if (b && !state.running) { b.disabled = !active; }
            var t = $('hikTuneShot');
            if (t) { t.disabled = !active; }
            var live = $('hikTuneLive');
            // ⚠️ หน้าหลักเรียก setActive() **ทุก 1 วินาที** จาก poll ของ
            // /api/detection/status ⇒ ถ้า clearInterval/setInterval ทุกครั้ง
            // ตัวจับเวลา 2 วินาทีจะถูกล้างทิ้งก่อนได้ทำงานเสมอ = แถบสถานะไม่เคย
            // อัปเดตเลย (เจอจริงตอนขับเบราว์เซอร์). ทำงานเฉพาะตอน "สถานะเปลี่ยน"
            if (!!active === !!tune.active) { return; }
            tune.active = !!active;
            if (active) {
                loadTuneRanges();
                clearInterval(tune.poll);
                tune.poll = setInterval(function () {
                    fetch('/api/camera/hik/status').then(function (r) { return r.json(); })
                        .then(function (d) {
                            var st = (d && d.stats) || {};
                            if (!live) { return; }
                            live.textContent = 'สว่างจริง '
                                + (st.mean_brightness != null ? st.mean_brightness.toFixed(1) : '—')
                                + '/255 · พิกเซลล้น '
                                + (st.clip_pct != null ? st.clip_pct.toFixed(1) : '—') + '%'
                                + ' · กล้อง ' + (st.fps != null ? st.fps.toFixed(1) : '—') + ' fps';
                        }).catch(function () {});
                }, 2000);
            } else {
                clearInterval(tune.poll);
                if (live) { live.textContent = '— (ยังไม่ได้เริ่มกล้อง)'; }
            }
        }
    };
})();
