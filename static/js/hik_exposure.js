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

    var state = { running: false, timer: null, session: null, sessions: [] };

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
        }
    };
})();
