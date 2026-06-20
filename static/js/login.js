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
  function showError(msg) {
    errBox.textContent = msg;
    errBox.hidden = false;
  }
  function clearError() {
    errBox.hidden = true;
    errBox.textContent = "";
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
    return "/";
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
})();
