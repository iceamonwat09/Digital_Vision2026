/* Login page behaviour:
 *   - show/hide password
 *   - real-time password strength + rule checklist (mirrors the backend policy
 *     in auth/passwords.py — UX only; the server always re-validates)
 *   - submit with loading state + clear error messages
 * On success the server sets httpOnly cookies; we just redirect.
 */
(function () {
  "use strict";

  const form = document.getElementById("login-form");
  const userEl = document.getElementById("username");
  const pwEl = document.getElementById("password");
  const toggleBtn = document.getElementById("toggle-pw");
  const rememberEl = document.getElementById("remember");
  const btn = document.getElementById("login-btn");
  const btnText = btn.querySelector(".btn-text");
  const btnSpin = btn.querySelector(".btn-spinner");
  const errBox = document.getElementById("auth-error");

  const strengthWrap = document.getElementById("pw-strength");
  const barFill = document.getElementById("pw-bar-fill");
  const pwLabel = document.getElementById("pw-label");
  const rulesEl = document.getElementById("pw-rules");

  // Policy defaults (overwritten by GET /api/auth/policy when reachable).
  let policy = { min_len: 8, specials: "!@#$%^&*" };

  fetch("/api/auth/policy")
    .then((r) => (r.ok ? r.json() : null))
    .then((p) => { if (p) policy = p; })
    .catch(() => {});

  // ── show / hide password ───────────────────────────────────────────
  toggleBtn.addEventListener("click", function () {
    const show = pwEl.type === "password";
    pwEl.type = show ? "text" : "password";
    toggleBtn.textContent = show ? "ซ่อน" : "แสดง";
  });

  // ── password strength ──────────────────────────────────────────────
  function evaluate(pw) {
    const specials = (policy.specials || "!@#$%^&*")
      .replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const checks = [
      { ok: pw.length >= (policy.min_len || 8),
        text: `ยาวอย่างน้อย ${policy.min_len || 8} ตัวอักษร` },
      { ok: /[A-Z]/.test(pw), text: "มีตัวพิมพ์ใหญ่ (A-Z)" },
      { ok: /[a-z]/.test(pw), text: "มีตัวพิมพ์เล็ก (a-z)" },
      { ok: /[0-9]/.test(pw), text: "มีตัวเลข (0-9)" },
      { ok: new RegExp("[" + specials + "]").test(pw),
        text: `มีอักขระพิเศษ (${policy.specials || "!@#$%^&*"})` },
    ];
    const score = checks.filter((c) => c.ok).length;
    return { checks, score };
  }

  const LEVELS = [
    { label: "อ่อนมาก", cls: "s0" },
    { label: "อ่อน", cls: "s1" },
    { label: "พอใช้", cls: "s2" },
    { label: "ดี", cls: "s3" },
    { label: "แข็งแรง", cls: "s4" },
    { label: "แข็งแรงมาก", cls: "s5" },
  ];

  pwEl.addEventListener("input", function () {
    const pw = pwEl.value;
    if (!pw) {
      strengthWrap.hidden = true;
      rulesEl.hidden = true;
      return;
    }
    const { checks, score } = evaluate(pw);
    strengthWrap.hidden = false;
    rulesEl.hidden = false;

    const lvl = LEVELS[score];
    barFill.className = "";
    barFill.classList.add(lvl.cls);
    barFill.style.width = (score / 5) * 100 + "%";
    pwLabel.textContent = lvl.label;

    rulesEl.innerHTML = "";
    checks.forEach((c) => {
      const li = document.createElement("li");
      li.className = c.ok ? "ok" : "no";
      li.textContent = (c.ok ? "✓ " : "• ") + c.text;
      rulesEl.appendChild(li);
    });
  });

  // ── error helpers ──────────────────────────────────────────────────
  const okBox = document.getElementById("auth-ok");

  function showError(msg) {
    errBox.textContent = msg;
    errBox.hidden = false;
  }
  function clearError() {
    errBox.hidden = true;
    errBox.textContent = "";
    if (okBox) { okBox.hidden = true; okBox.textContent = ""; }
  }
  function showOk(msg) {
    if (!okBox) return;
    errBox.hidden = true;
    okBox.textContent = msg;
    okBox.hidden = false;
  }
  function setLoading(on) {
    btn.disabled = on;
    btnText.hidden = on;
    btnSpin.hidden = !on;
  }

  function nextUrl() {
    const q = new URLSearchParams(window.location.search).get("next");
    // only allow same-site relative paths
    if (q && q.startsWith("/") && !q.startsWith("//")) return q;
    return "/home";
  }

  // ── submit ─────────────────────────────────────────────────────────
  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    clearError();
    const username = userEl.value.trim();
    const password = pwEl.value;
    if (!username || !password) {
      showError("กรุณากรอกชื่อผู้ใช้และรหัสผ่าน");
      return;
    }
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: username,
          password: password,
          remember: rememberEl.checked,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        window.location.href = nextUrl();
        return;
      }
      showError(data.error || "เข้าสู่ระบบไม่สำเร็จ");
    } catch (err) {
      showError("เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ ลองใหม่อีกครั้ง");
    } finally {
      setLoading(false);
    }
  });

  // ── ลงทะเบียนด้วยตนเอง ──────────────────────────────────────────────
  // The modal only exists when the server rendered it (register_enabled).
  // Every rule below is mirrored server-side in auth/registration.py — this is
  // UX only, never the gate.
  const rgModal = document.getElementById("rg-modal");
  if (!rgModal) return;

  const rgOpen = document.getElementById("register-open");
  const rgEmail = document.getElementById("rg-email");
  const rgPw = document.getElementById("rg-password");
  const rgConfirm = document.getElementById("rg-confirm");
  const rgRules = document.getElementById("rg-pw-rules");
  const rgMatch = document.getElementById("rg-pw-match");
  const rgErr = document.getElementById("rg-error");
  const rgSubmit = document.getElementById("rg-submit");

  const domains = (rgModal.dataset.domains || "")
    .split(",").map((d) => d.trim().toLowerCase()).filter(Boolean);

  function rgShowError(msg) {
    rgErr.textContent = msg;
    rgErr.hidden = false;
  }
  function rgClearError() {
    rgErr.hidden = true;
    rgErr.textContent = "";
  }

  function openModal() {
    rgEmail.value = rgPw.value = rgConfirm.value = "";
    rgRules.hidden = true;
    rgMatch.hidden = true;
    rgClearError();
    rgModal.hidden = false;
    document.body.classList.add("modal-open");
    rgEmail.focus();
  }
  function closeModal() {
    rgModal.hidden = true;
    document.body.classList.remove("modal-open");
  }

  rgOpen.addEventListener("click", openModal);
  document.getElementById("rg-close").addEventListener("click", closeModal);
  document.getElementById("rg-cancel").addEventListener("click", closeModal);
  rgModal.addEventListener("click", function (e) {
    if (e.target === rgModal) closeModal();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !rgModal.hidden) closeModal();
  });

  // show/hide for both password boxes in the modal
  rgModal.querySelectorAll(".pw-toggle[data-target]").forEach(function (b) {
    b.addEventListener("click", function () {
      const el = document.getElementById(b.dataset.target);
      const show = el.type === "password";
      el.type = show ? "text" : "password";
      b.textContent = show ? "ซ่อน" : "แสดง";
    });
  });

  rgPw.addEventListener("input", function () {
    if (!rgPw.value) { rgRules.hidden = true; return; }
    const { checks } = evaluate(rgPw.value);
    rgRules.hidden = false;
    rgRules.innerHTML = "";
    checks.forEach(function (c) {
      const li = document.createElement("li");
      li.className = c.ok ? "ok" : "no";
      li.textContent = (c.ok ? "✓ " : "• ") + c.text;
      rgRules.appendChild(li);
    });
  });

  function checkMatch() {
    if (!rgConfirm.value) { rgMatch.hidden = true; return true; }
    const same = rgPw.value === rgConfirm.value;
    rgMatch.hidden = false;
    rgMatch.className = "field-hint " + (same ? "ok" : "no");
    rgMatch.textContent = same ? "✓ รหัสผ่านตรงกัน" : "รหัสผ่านไม่ตรงกัน";
    return same;
  }
  rgConfirm.addEventListener("input", checkMatch);
  rgPw.addEventListener("input", checkMatch);

  // Same rule as auth/registration.py: exact domain match, case-insensitive.
  function emailProblem(email) {
    if (!email) return "กรุณากรอกอีเมล";
    if (!/^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/.test(email)) {
      return "รูปแบบอีเมลไม่ถูกต้อง";
    }
    const dom = email.slice(email.lastIndexOf("@") + 1);
    if (domains.length && domains.indexOf(dom) === -1) {
      return "อนุญาตเฉพาะอีเมล " +
        domains.map(function (d) { return "@" + d; }).join(" หรือ ") + " เท่านั้น";
    }
    if (email.length > 64) return "อีเมลยาวเกิน 64 ตัวอักษร";
    return "";
  }

  rgSubmit.addEventListener("click", async function () {
    rgClearError();
    const email = rgEmail.value.trim().toLowerCase();
    const problem = emailProblem(email);
    if (problem) { rgShowError(problem); rgEmail.focus(); return; }
    if (rgPw.value !== rgConfirm.value) {
      checkMatch();
      rgShowError("รหัสผ่านและการยืนยันรหัสผ่านไม่ตรงกัน");
      return;
    }
    const { checks } = evaluate(rgPw.value);
    const failed = checks.filter(function (c) { return !c.ok; });
    if (failed.length) {
      rgShowError("รหัสผ่านไม่ผ่านเงื่อนไข: " +
        failed.map(function (c) { return c.text; }).join(", "));
      return;
    }

    rgSubmit.disabled = true;
    try {
      const res = await fetch("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: email,
          password: rgPw.value,
          confirm_password: rgConfirm.value,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        closeModal();
        // Hand the new account straight to the sign-in form.
        userEl.value = data.username || email;
        showOk("สร้างบัญชีเรียบร้อยแล้ว — เข้าสู่ระบบด้วยอีเมลและรหัสผ่านที่ตั้งไว้");
        pwEl.value = "";
        pwEl.focus();
        return;
      }
      let msg = data.error || "ลงทะเบียนไม่สำเร็จ";
      if (data.details && data.details.length) msg += ": " + data.details.join(", ");
      rgShowError(msg);
    } catch (err) {
      rgShowError("เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ ลองใหม่อีกครั้ง");
    } finally {
      rgSubmit.disabled = false;
    }
  });
})();
