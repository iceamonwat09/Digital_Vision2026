/*
 * hik_camera.js — แผงควบคุม "กล้องอุตสาหกรรม (Hikrobot)" ของโหมด Can Dent
 *
 * ทำไมแยกไฟล์ (ไม่ยัดใน index.html เหมือนแท็บอื่น):
 *   สคริปต์ในหน้านั้นยาว ~950 บรรทัดและคุมทั้ง USB/RTSP/STREAM อยู่แล้ว การเพิ่ม
 *   ตรรกะของกล้องอุตสาหกรรมเข้าไปปนจะทำให้แก้อะไรทีก็เสี่ยงกระทบโหมดเดิม (กฎเหล็กข้อ 1)
 *   ไฟล์นี้จึงเป็นโมดูลปิด เปิดออกมาแค่ window.HikUI และ **ไม่แตะตัวแปรของหน้าอื่นเลย**
 *   หน้าเว็บเป็นฝ่ายเรียกเข้ามาผ่าน init() เท่านั้น.
 *
 * ค่าทั้งหมดในแผงถูกสร้างจาก "ช่วงที่กล้องบอกจริง" (min/max/inc/ตัวเลือก) ที่ได้จาก
 * GET /api/camera/hik/params — ไม่ hard-code ตัวเลขไว้ฝั่งหน้าเว็บ เพราะกล้องคนละรุ่น
 * /คนละเฟิร์มแวร์มีช่วงไม่เท่ากัน (บนสถานีนี้ binning/gamma เข้าไม่ถึงด้วยซ้ำ) —
 * ปุ่มที่กดแล้วไม่มีอะไรเกิดขึ้นคือสิ่งที่ต้องหลีกเลี่ยง.
 */
(function () {
    'use strict';

    var $ = function (id) { return document.getElementById(id); };

    // ลำดับการแสดง + ชนิดตัวควบคุม. คีย์ที่กล้องไม่รองรับ (supported=false) จะถูกซ่อน
    // [คีย์, ชนิดตัวควบคุม, (ถ้ามี) หัวข้อกลุ่มที่เริ่มตรงนี้]
    // หัวข้อกลุ่มจะถูกพิมพ์เฉพาะเมื่อกลุ่มนั้นมีตัวควบคุมที่กล้องรองรับจริงอย่างน้อย 1 ตัว
    var UI_ORDER = [
        ['exposure_auto', 'select', 'แสงและการรับภาพ'],
        ['exposure_us', 'number'],
        ['gain_auto', 'select'],
        ['gain_db', 'number'],
        ['balance_white_auto', 'select'],
        ['framerate_enable', 'check', 'อัตราเฟรม'],
        ['framerate', 'number'],
        ['pixel_format', 'select', 'ภาพและ ROI'],
        ['width', 'number'],
        ['height', 'number'],
        ['offset_x', 'number'],
        ['offset_y', 'number'],
        ['reverse_x', 'check'],
        ['reverse_y', 'check'],
        ['trigger_mode', 'select', 'ทริกเกอร์'],
        ['trigger_source', 'select'],
        ['trigger_activation', 'select'],
        ['packet_size', 'number', 'เครือข่าย GigE'],
        ['packet_delay', 'number']
    ];

    var state = {
        params: {},        // ค่าล่าสุดที่อ่านจากกล้อง
        devices: [],
        pollTimer: null,
        loading: false,
        active: false      // ระบบกำลังตรวจด้วยกล้องนี้อยู่หรือไม่
    };

    function setHint(text, tone) {
        var el = $('hikHint');
        if (!el) { return; }
        el.textContent = text;
        el.style.color = tone === 'bad' ? '#dc2626'
            : (tone === 'good' ? '#16a34a' : '#94a3b8');
    }

    function setMsg(text, tone) {
        var el = $('hikApplyMsg');
        if (!el) { return; }
        el.innerHTML = text || '';
        el.style.color = tone === 'bad' ? '#dc2626'
            : (tone === 'good' ? '#16a34a' : '#64748b');
    }

    // ── ค้นหากล้อง ────────────────────────────────────────
    function scan() {
        var sel = $('hikCameraSelect');
        var btn = $('refreshHikBtn');
        if (!sel) { return; }
        sel.disabled = true;
        if (btn) { btn.disabled = true; }
        sel.innerHTML = '<option value="" disabled selected>กำลังค้นหา…</option>';
        setHint('กำลังค้นหากล้อง…');

        fetch('/api/camera/hik/scan')
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.status !== 'ok') { throw new Error(d.message || 'scan failed'); }
                if (!d.sdk || !d.sdk.available) {
                    sel.innerHTML = '<option value="" disabled selected>ไม่พบ MVS SDK</option>';
                    setHint('❌ ไม่พบ MVS SDK — ' + (d.sdk && d.sdk.hint ? d.sdk.hint : ''), 'bad');
                    return;
                }
                if (d.sdk.is_fake) {
                    // ต้องเห็นจากหน้าจอ ไม่ใช่เห็นแค่ใน log ฝั่งเซิร์ฟเวอร์
                    setHint('⛔ กำลังใช้ MVS SDK ปลอม (สำหรับทดสอบ) — ภาพไม่ใช่ของกล้องจริง '
                        + 'ห้ามใช้ตัดสินคุณภาพงาน', 'bad');
                }
                state.devices = d.devices || [];
                sel.innerHTML = '';
                if (!state.devices.length) {
                    sel.innerHTML = '<option value="" disabled selected>ไม่พบกล้อง</option>';
                    setHint('❌ ไม่พบกล้อง — ตรวจสายแลน/ไฟเลี้ยง และว่ากล้องอยู่วง IP เดียวกับการ์ดแลน', 'bad');
                    return;
                }
                var blocked = 0;
                state.devices.forEach(function (dev) {
                    var opt = document.createElement('option');
                    opt.value = dev.source;
                    var name = (dev.model || 'Hikrobot') + (dev.serial ? ' · ' + dev.serial : '');
                    if (dev.ip) { name += ' · ' + dev.ip; }
                    if (dev.accessible === false) { name += ' (ถูกโปรแกรมอื่นจองอยู่)'; blocked++; }
                    opt.textContent = name;
                    sel.appendChild(opt);
                });
                sel.disabled = state.active;
                if (d.sdk.is_fake) {
                    /* ข้อความเตือน SDK ปลอมสำคัญกว่า อย่าเขียนทับ */
                } else if (blocked) {
                    setHint('⚠️ กล้องถูกโปรแกรมอื่นจองอยู่ — ปิดโปรแกรม MVS ก่อน (GigE เปิดได้ทีละโปรแกรม)', 'bad');
                } else {
                    setHint('พบ ' + state.devices.length + ' กล้อง · ⚠️ ต้องปิดโปรแกรม MVS ก่อนใช้งาน', 'good');
                }
                loadParams();
            })
            .catch(function (e) {
                sel.innerHTML = '<option value="" disabled selected>ค้นหาไม่สำเร็จ</option>';
                setHint('❌ ค้นหากล้องไม่สำเร็จ: ' + e.message, 'bad');
            })
            .finally(function () { if (btn) { btn.disabled = false; } });
    }

    // ── อ่านค่า/ช่วงจากกล้อง แล้วสร้างตัวควบคุม ───────────────
    function loadParams() {
        var box = $('hikParams');
        if (!box || state.loading) { return; }
        state.loading = true;
        box.innerHTML = '<div class="hik-note">กำลังอ่านค่าจากกล้อง…</div>';
        var src = selectedSource();
        var url = '/api/camera/hik/params' + (src ? '?source=' + encodeURIComponent(src) : '');
        fetch(url)
            .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
            .then(function (res) {
                if (!res.ok || res.d.status !== 'ok') {
                    box.innerHTML = '<div class="hik-note hik-bad">อ่านค่าจากกล้องไม่ได้: '
                        + (res.d.message || '') + '</div>';
                    return;
                }
                state.params = res.d.params || {};
                renderParams(state.params, res.d.identity || {});
            })
            .catch(function (e) {
                box.innerHTML = '<div class="hik-note hik-bad">อ่านค่าจากกล้องไม่ได้: ' + e.message + '</div>';
            })
            .finally(function () { state.loading = false; });
    }

    function fmtNum(v) {
        if (v === null || v === undefined) { return ''; }
        if (typeof v !== 'number') { return String(v); }
        return (Math.round(v * 1000) / 1000).toString();
    }

    function renderParams(params, identity) {
        var box = $('hikParams');
        if (!box) { return; }
        box.innerHTML = '';

        if (identity && identity.model) {
            var head = document.createElement('div');
            head.className = 'hik-ident';
            head.textContent = identity.model
                + (identity.serial ? ' · SN ' + identity.serial : '')
                + (identity.ip ? ' · ' + identity.ip : '');
            box.appendChild(head);
        }

        var pendingGroup = null;
        UI_ORDER.forEach(function (pair) {
            var key = pair[0], kind = pair[1];
            if (pair[2]) { pendingGroup = pair[2]; }
            var p = params[key];
            if (!p || p.supported === false) { return; }   // กล้องไม่มี node นี้ → ซ่อน

            if (pendingGroup) {
                var gh = document.createElement('div');
                gh.className = 'hik-group';
                gh.textContent = pendingGroup;
                box.appendChild(gh);
                pendingGroup = null;
            }

            var row = document.createElement('div');
            row.className = 'hik-row';
            var label = document.createElement('label');
            label.className = 'hik-label';
            label.setAttribute('for', 'hik-p-' + key);
            label.appendChild(document.createTextNode(p.label || key));
            if (kind === 'number' && p.min !== null && p.min !== undefined) {
                var rng = document.createElement('span');
                rng.className = 'hik-range';
                rng.textContent = fmtNum(p.min) + ' – ' + fmtNum(p.max);
                label.appendChild(rng);
            }
            row.appendChild(label);

            var input;
            if (kind === 'select') {
                input = document.createElement('select');
                (p.options || []).forEach(function (name) {
                    var o = document.createElement('option');
                    o.value = name;
                    o.textContent = name;
                    if (name === p.symbolic) { o.selected = true; }
                    input.appendChild(o);
                });
            } else if (kind === 'check') {
                input = document.createElement('input');
                input.type = 'checkbox';
                input.checked = !!p.value;
            } else {
                input = document.createElement('input');
                input.type = 'number';
                if (p.min !== null && p.min !== undefined) { input.min = p.min; }
                if (p.max !== null && p.max !== undefined) { input.max = p.max; }
                if (p.inc) { input.step = p.inc; }
                input.value = fmtNum(p.value);
            }
            input.id = 'hik-p-' + key;
            input.className = 'hik-input';
            input.dataset.key = key;
            input.dataset.kind = kind;
            input.dataset.original = (kind === 'check') ? (p.value ? '1' : '0')
                : String(kind === 'select' ? (p.symbolic || '') : fmtNum(p.value));
            if (state.active && p.live === false) {
                input.title = 'ค่านี้จะทำให้สตรีมหยุด-เริ่มใหม่สั้น ๆ ตอนบันทึก';
            }
            row.appendChild(input);
            box.appendChild(row);
        });

        // ค่าที่อ่านอย่างเดียวแต่ต้องเห็น
        var extras = [];
        if (params.resulting_framerate) {
            extras.push('อัตราเฟรมที่กล้องคำนวณได้ ' + fmtNum(params.resulting_framerate.value) + ' fps');
        }
        if (params.width_max && params.height_max) {
            extras.push('เซนเซอร์เต็ม ' + params.width_max.value + '×' + params.height_max.value);
        }
        if (extras.length) {
            var note = document.createElement('div');
            note.className = 'hik-note';
            note.textContent = extras.join(' · ');
            box.appendChild(note);
        }
    }

    // ── รวบรวมเฉพาะค่าที่ "ผู้ใช้แก้จริง" แล้วส่ง ───────────────
    function collectChanges() {
        var out = {};
        var inputs = document.querySelectorAll('#hikParams .hik-input');
        Array.prototype.forEach.call(inputs, function (el) {
            var key = el.dataset.key, kind = el.dataset.kind;
            var now = (kind === 'check') ? (el.checked ? '1' : '0') : String(el.value);
            if (now === el.dataset.original) { return; }   // ไม่แตะ = ไม่ส่ง
            if (kind === 'check') { out[key] = el.checked; }
            else if (kind === 'select') { out[key] = el.value; }
            else if (el.value !== '') { out[key] = Number(el.value); }
        });
        return out;
    }

    function postParams(params, label) {
        if (!params || !Object.keys(params).length) {
            setMsg('ไม่มีค่าที่เปลี่ยน', null);
            return Promise.resolve();
        }
        setMsg('กำลังบันทึก…', null);
        return fetch('/api/camera/hik/params', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ params: params, save: true })
        })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.status !== 'ok') { throw new Error(d.message || 'ตั้งค่าไม่สำเร็จ'); }
                var failed = Object.keys(d.failed || {});
                var okCount = Object.keys(d.applied || {}).length;
                if (d.params) { state.params = d.params; renderParams(d.params, {}); }
                if (failed.length) {
                    var lines = failed.map(function (k) {
                        return '• ' + k + ': ' + ((d.failed[k] && d.failed[k].message) || 'ไม่สำเร็จ');
                    });
                    setMsg('⚠️ ตั้งได้ ' + okCount + ' ค่า · ไม่สำเร็จ ' + failed.length
                        + '<br>' + lines.join('<br>'), 'bad');
                } else if (!d.live) {
                    setMsg('💾 บันทึกแล้ว — จะถูกใช้ตอนกด Start Detection (ยังไม่ได้เริ่มกล้อง)', 'good');
                } else {
                    setMsg('✅ ' + (label || 'ตั้งค่าแล้ว') + ' ' + okCount + ' ค่า', 'good');
                }
            })
            .catch(function (e) { setMsg('❌ ' + e.message, 'bad'); });
    }

    function applyChanges() { return postParams(collectChanges(), 'ตั้งค่าแล้ว'); }

    // ── ROI สำเร็จรูป ─────────────────────────────────────
    // ROI คือคันโยกเดียวที่เพิ่ม fps ได้บนกล้องนี้ (binning ตั้งไม่ได้) — วัดจริงแล้ว
    // ครึ่งกลาง = 69 fps เทียบกับเต็มเฟรม 23.65 fps
    function roiPreset(kind) {
        var p = state.params || {};
        if (!p.width_max || !p.height_max) {
            setMsg('ยังไม่รู้ขนาดเซนเซอร์ — กด "อ่านค่าจากกล้อง" ก่อน', 'bad');
            return;
        }
        var wmax = p.width_max.value, hmax = p.height_max.value;
        var frac = kind === 'half' ? 0.5 : (kind === 'quarter' ? 0.25 : 1.0);
        var winc = (p.width && p.width.inc) || 1;
        var hinc = (p.height && p.height.inc) || 1;
        var w = Math.max(winc, Math.floor((wmax * frac) / winc) * winc);
        var h = Math.max(hinc, Math.floor((hmax * frac) / hinc) * hinc);
        postParams({ width: w, height: h, roi_center: true },
            'ตั้ง ROI ' + w + '×' + h + ' แล้ว');
    }

    // ── เก็บภาพชุดข้อมูล ───────────────────────────────────
    function numOf(id, dflt) {
        var el = $(id);
        var v = el ? parseInt(el.value, 10) : NaN;
        return isNaN(v) ? dflt : v;
    }

    function toggleDataset() {
        var el = $('hikDatasetToggle');
        if (!el) { return; }
        var body = { enabled: el.checked };
        if (el.checked) {
            body.duration_s = numOf('hikDsDuration', 60);
            body.every_n = numOf('hikDsEveryN', 1);
            body.max_frames = numOf('hikDsMax', 5000);
        }
        fetch('/api/camera/hik/dataset', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.status !== 'ok') { throw new Error(d.message || 'สั่งไม่สำเร็จ'); }
                var ds = d.dataset || {};
                if (d.enabled) {
                    setMsg('💾 กำลังเก็บภาพลง ' + ds.dir
                        + (ds.free_mb ? ' · ว่าง ' + Math.round(ds.free_mb / 1024) + ' GB' : ''),
                        'good');
                } else {
                    setMsg('หยุดเก็บภาพแล้ว — บันทึก <b>' + ds.saved + '</b> ภาพ ('
                        + ds.mb + ' MB · ' + ds.save_fps + ' ภาพ/วิ) · '
                        + '<b>ทิ้งเพราะดิสก์ตามไม่ทัน ' + ds.dropped + '</b>'
                        + (ds.finished_reason ? ' · ' + ds.finished_reason : ''),
                        ds.dropped ? 'bad' : 'good');
                }
            })
            .catch(function (e) { el.checked = false; setMsg('❌ ' + e.message, 'bad'); });
    }

    // ── ถ่าย 1 เฟรมความละเอียดเต็ม แล้วตรวจ ──────────────────
    /**
     * ถ่าย 1 เฟรม — **2 เฟส**: ① คืนรูปทันที ② ค่อยตรวจ
     *
     * เดิมเป็นคำขอเดียว ⇒ ผู้ใช้ไม่เห็นรูปเลยจนกว่าโมเดลจะเสร็จ (imgsz 1280
     * บนสถานี ~420 ms) ทั้งที่ตัวการ "ถ่าย" ใช้เวลาแค่ ~15 ms
     */
    function shot() {
        var btn = $('hikShotBtn');
        var imgszSel = $('hikShotImgsz');
        var imgsz = imgszSel ? Number(imgszSel.value) : undefined;
        if (btn) { btn.disabled = true; btn.textContent = 'กำลังถ่าย…'; }
        fetch('/api/camera/hik/shot', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ detect: false })
        })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.status !== 'ok') { throw new Error(d.message || 'ถ่ายไม่สำเร็จ'); }
                showShot(d);                       // ← รูปขึ้นตรงนี้ ไม่รอโมเดล
                if (btn) { btn.textContent = 'กำลังตรวจ…'; }
                return inspectShot(d.shot_id, imgsz);
            })
            .catch(function (e) { setMsg('❌ ' + e.message, 'bad'); })
            .finally(function () {
                if (btn) { btn.disabled = false; btn.textContent = '📸 ถ่าย 1 เฟรม (เต็มความละเอียด)'; }
            });
    }

    function inspectShot(shotId, imgsz) {
        return fetch('/api/camera/hik/shot/inspect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ shot_id: shotId, imgsz: imgsz })
        })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.status !== 'ok') { throw new Error(d.message || 'ตรวจไม่สำเร็จ'); }
                showShot(d);                       // เติมกรอบ + ผลตรวจทับของเดิม
            })
            .catch(function (e) {
                // ⚠️ ห้ามปล่อยให้ค้างที่ "กำลังตรวจ…" — ต้องบอกว่าตรวจไม่สำเร็จ
                var v = $('hikShotVerdict');
                if (v) {
                    v.textContent = '⚠️ ตรวจไม่สำเร็จ — ' + e.message;
                    v.className = 'hik-shot-verdict';
                }
            });
    }

    function showShot(d) {
        var ov = $('hikShotOverlay');
        if (!ov) { return; }
        if (d.image) { $('hikShotImg').src = d.image; }
        var v = $('hikShotVerdict');
        var meta = ['ภาพ ' + d.capture_size];
        if (d.capture_ms != null) { meta.push('จับภาพ ' + d.capture_ms + ' ms'); }

        if (d.pending_detect) {
            // ⚠️ ยังไม่รู้ผล — ต้องบอกว่ายังไม่รู้ ห้ามแสดงอะไรที่ดูเหมือนผลตรวจ
            v.textContent = '⏳ กำลังตรวจ…';
            v.className = 'hik-shot-verdict';
            $('hikShotMeta').textContent = meta.join(' · ');
            ov.style.display = '';
            return;
        }

        var ng = d.verdict === 'ng';
        v.textContent = ng ? ('NG — พบ ' + d.dent_count + ' จุด') : 'OK — ไม่พบรอยบุบ';
        v.className = 'hik-shot-verdict ' + (ng ? 'ng' : 'ok');
        meta.push('ตรวจที่ imgsz ' + d.infer_imgsz + ' · โมเดล ' + d.infer_ms + ' ms');
        // รอคิวนาน = การตรวจสดกำลังใช้ iGPU อยู่ (คนละเรื่องกับโมเดลหนัก)
        if (d.wait_ms > 20) { meta.push('รอคิวโมเดล ' + Math.round(d.wait_ms) + ' ms'); }
        if (d.encode_ms != null) { meta.push('แสดงผล ' + d.encode_ms + ' ms'); }
        if (ng) { meta.push('ความมั่นใจสูงสุด ' + d.max_confidence); }
        $('hikShotMeta').textContent = meta.join(' · ');
        ov.style.display = '';
    }

    // ── โหมดแสดงผลของภาพสด (กรอบล็อก vs ภาพลื่น) ─────────
    // ⚠️ แสดงผลล้วน — การนับ/บันทึก DB/verdict ใช้เฟรมที่โมเดลตรวจจริงเสมอ
    function loadSmooth() {
        fetch('/api/camera/hik/live_smooth')
            .then(function (r) { return r.json(); })
            .then(function (d) {
                var el = $('hikSmoothToggle');
                if (el && d && d.status === 'ok') { el.checked = !!d.smooth; }
            })
            .catch(function () { /* เงียบได้ — ช่องติ๊กจะคงค่าเริ่มต้นของหน้า */ });
    }

    function toggleSmooth() {
        var el = $('hikSmoothToggle');
        if (!el) { return; }
        var want = !!el.checked;
        fetch('/api/camera/hik/live_smooth', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ smooth: want })
        })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                // ให้ช่องติ๊กตรงกับสิ่งที่เซิร์ฟเวอร์ใช้จริงเสมอ ไม่ใช่สิ่งที่กดไป
                if (d && d.status === 'ok') { el.checked = !!d.smooth; }
            })
            .catch(function () { el.checked = !want; });
    }

    // ── สถิติสด ───────────────────────────────────────────
    function pollStats() {
        fetch('/api/camera/hik/status')
            .then(function (r) { return r.json(); })
            .then(function (d) {
                // แชร์ให้โมดูลอื่น (ตัวประมาณของถ่ายรัว) แทนที่จะให้มันยิงเอง —
                // ทุกคำขอสถานะต้องแย่ง lock ของกล้องกับเธรดจับภาพ
                state.lastStats = (d && d.active && d.stats) ? d.stats : null;
                state.lastStatsAt = Date.now();
                var el = $('hikStats');
                if (!el) { return; }
                if (!d.active || !d.stats) { el.style.display = 'none'; return; }
                var s = d.stats;
                var parts = [];
                parts.push('FPS ' + (s.fps !== null && s.fps !== undefined ? s.fps.toFixed(1) : '?'));
                if (s.size) { parts.push(s.size); }
                parts.push('เฟรมหาย ' + s.dropped);
                if (s.lost_packets !== undefined) { parts.push('แพ็กเก็ตหาย ' + s.lost_packets); }
                if (s.mean_brightness !== null && s.mean_brightness !== undefined) {
                    parts.push('สว่าง ' + s.mean_brightness + '/255');
                }
                if (s.clip_pct) { parts.push('ล้น ' + s.clip_pct + '%'); }
                if (s.dataset && s.dataset.active) {
                    parts.push('เก็บภาพ ' + s.dataset.saved + '/' + s.dataset.max_frames
                        + ' (' + s.dataset.mb_per_s + ' MB/s'
                        + (s.dataset.dropped ? ' · ทิ้ง ' + s.dataset.dropped : '') + ')');
                } else if (s.dataset && s.dataset.finished_reason) {
                    parts.push('เก็บภาพจบ: ' + s.dataset.saved + ' ภาพ');
                    var t = $('hikDatasetToggle');
                    if (t && t.checked) { t.checked = false; }   // ให้ปุ่มตรงกับความจริง
                }
                el.textContent = parts.join(' · ');
                el.style.display = '';
                // เตือนแบบไม่รบกวน: เฟรมหาย/แพ็กเก็ตหาย = ภาพไม่ครบ ห้ามปล่อยเงียบ
                el.style.color = (s.dropped || s.lost_packets) ? '#fca5a5' : '#e2e8f0';
            })
            .catch(function () { /* เงียบได้ — เป็นแค่แถบสถานะ */ });
    }

    function startPolling() {
        if (state.pollTimer) { return; }
        pollStats();
        state.pollTimer = setInterval(pollStats, 1000);
    }

    function stopPolling() {
        if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
        state.lastStats = null;
        state.lastStatsAt = 0;
        var el = $('hikStats');
        if (el) { el.style.display = 'none'; }
    }

    function selectedSource() {
        var sel = $('hikCameraSelect');
        return (sel && sel.value) ? sel.value : null;
    }

    // ── สาธารณะ ───────────────────────────────────────────
    window.HikUI = {
        init: function () {
            var refresh = $('refreshHikBtn');
            if (refresh) { refresh.addEventListener('click', scan); }
            var apply = $('hikApplyBtn');
            if (apply) { apply.addEventListener('click', applyChanges); }
            var reload = $('hikReloadBtn');
            if (reload) { reload.addEventListener('click', loadParams); }
            var ds = $('hikDatasetToggle');
            if (ds) { ds.addEventListener('change', toggleDataset); }
            var sm = $('hikSmoothToggle');
            if (sm) { sm.addEventListener('change', toggleSmooth); }
            loadSmooth();
            var sh = $('hikShotBtn');
            if (sh) { sh.addEventListener('click', shot); }
            var close = $('hikShotClose');
            if (close) {
                close.addEventListener('click', function () {
                    $('hikShotOverlay').style.display = 'none';
                });
            }
            var ov = $('hikShotOverlay');
            if (ov) {
                ov.addEventListener('click', function (e) {
                    if (e.target === ov) { ov.style.display = 'none'; }
                });
            }
            Array.prototype.forEach.call(
                document.querySelectorAll('#hikRoiPresets [data-roi]'),
                function (b) {
                    b.addEventListener('click', function () { roiPreset(b.dataset.roi); });
                });
        },

        /** เรียกเมื่อผู้ใช้เปิดแท็บนี้ครั้งแรก (ค้นหากล้องแบบ lazy) */
        onShow: function () {
            var sel = $('hikCameraSelect');
            if (sel && sel.dataset.scanned !== '1') {
                sel.dataset.scanned = '1';
                scan();
            }
        },

        selectedSource: selectedSource,

        /** หน้าเว็บแจ้งว่าระบบกำลังตรวจอยู่หรือไม่ เพื่อล็อกตัวควบคุมที่ห้ามแก้ */
        setActive: function (active) {
            state.active = !!active;
            var sel = $('hikCameraSelect');
            if (sel) { sel.disabled = active || sel.options.length === 0; }
            var refresh = $('refreshHikBtn');
            if (refresh) { refresh.disabled = active; }
            var ds = $('hikDatasetToggle');
            if (ds) { ds.disabled = !active; }        // เก็บภาพได้เฉพาะตอนกล้องทำงาน
            var sh = $('hikShotBtn');
            if (sh) { sh.disabled = !active; }
            if (active) { startPolling(); } else { stopPolling(); }
        },

        stopPolling: stopPolling,

        /**
         * สถิติล่าสุดที่แถบสถานะ poll มาแล้ว (null = ยังไม่มี/กล้องไม่ทำงาน).
         * มีไว้ให้โมดูลอื่นใช้ค่าร่วมกัน **แทนการยิงคำขอของตัวเอง** — ทุกคำขอ
         * /api/camera/hik/status ต้องแย่ง lock ของกล้องกับเธรดจับภาพ.
         * `maxAgeMs` = ยอมรับค่าเก่าได้ไม่เกินกี่มิลลิวินาที (0 = ไม่จำกัด)
         */
        getStats: function (maxAgeMs) {
            if (!state.lastStats) { return null; }
            if (maxAgeMs && (Date.now() - state.lastStatsAt) > maxAgeMs) { return null; }
            return state.lastStats;
        }
    };
})();
