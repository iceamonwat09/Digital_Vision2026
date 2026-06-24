/* Admin: manage users (assign role) + roles (tick permissions).
 * All endpoints require the manage_users permission (enforced server-side). */
(function () {
  "use strict";

  const alertBox = document.getElementById("admin-alert");
  let PERMISSIONS = [];   // [{key,label}]
  let ROLES = [];         // [{role_id,name,description,user_count,permissions[]}]
  let ALL_USERS = [];     // last-loaded users (the search box filters this)

  // Does a role grant manage_users? Used to warn before risky changes.
  function roleHasManageUsers(roleName) {
    const r = ROLES.find((x) => x.name === roleName);
    return !!(r && r.permissions.indexOf("manage_users") >= 0);
  }

  function showAlert(msg, ok) {
    alertBox.textContent = msg;
    alertBox.hidden = false;
    alertBox.style.background = ok ? "#E8F5E9" : "#FDECEA";
    alertBox.style.borderColor = ok ? "#A5D6A7" : "#F5C2C0";
    alertBox.style.color = ok ? "#2E7D32" : "#E53935";
    setTimeout(() => { alertBox.hidden = true; }, 4000);
  }

  async function api(url, method, body) {
    const opt = { method: method || "GET", headers: {} };
    if (body !== undefined) {
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
    const res = await fetch(url, opt);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
    return data;
  }

  function el(tag, attrs, children) {
    const e = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach((k) => {
      if (k === "class") e.className = attrs[k];
      else if (k === "text") e.textContent = attrs[k];
      else e.setAttribute(k, attrs[k]);
    });
    (children || []).forEach((c) => e.appendChild(c));
    return e;
  }

  // ── Users ───────────────────────────────────────────────────────────
  function renderUsers(users) {
    const tb = document.querySelector("#users-table tbody");
    tb.innerHTML = "";
    users.forEach((u) => {
      const sel = el("select", { class: "role-select" });
      ROLES.forEach((r) => {
        const o = el("option", { value: r.name, text: r.name });
        if (r.name === u.role) o.selected = true;
        sel.appendChild(o);
      });

      const saveBtn = el("button", { class: "btn btn-secondary btn-sm", text: "บันทึกบทบาท" });
      saveBtn.addEventListener("click", async () => {
        // Warn when removing manage_users from an account (could lock admins out).
        if (roleHasManageUsers(u.role) && !roleHasManageUsers(sel.value) &&
            !confirm("บทบาทใหม่ '" + sel.value + "' ไม่มีสิทธิ์จัดการผู้ใช้\n" +
                     "ยืนยันการเปลี่ยนบทบาทของ " + u.username + " ?")) return;
        try {
          await api("/api/auth/users/" + encodeURIComponent(u.username) + "/role",
                    "POST", { role: sel.value });
          showAlert("อัปเดตบทบาทของ " + u.username + " แล้ว", true);
          load();
        } catch (e) { showAlert(e.message, false); }
      });

      const editBtn = el("button", { class: "btn btn-secondary btn-sm", text: "แก้ไข" });
      editBtn.addEventListener("click", () => openEditUser(u));

      const resetBtn = el("button", { class: "btn btn-secondary btn-sm", text: "รีเซ็ตรหัสผ่าน" });
      resetBtn.addEventListener("click", () => openResetPassword(u));

      const toggle = el("button", {
        class: "btn btn-sm " + (u.is_active ? "btn-danger" : "btn-secondary"),
        text: u.is_active ? "ปิดบัญชี" : "เปิดบัญชี",
      });
      toggle.addEventListener("click", async () => {
        if (u.is_active && !confirm("ปิดการใช้งานบัญชี " + u.username + " ?\n" +
            "ผู้ใช้จะเข้าสู่ระบบไม่ได้จนกว่าจะเปิดใช้งานอีกครั้ง")) return;
        try {
          await api("/api/auth/users/" + encodeURIComponent(u.username) + "/active",
                    "POST", { active: !u.is_active });
          load();
        } catch (e) { showAlert(e.message, false); }
      });

      const actions = [saveBtn, editBtn, resetBtn, toggle];
      if (u.locked) {
        const unlockBtn = el("button", { class: "btn btn-secondary btn-sm", text: "ปลดล็อก" });
        unlockBtn.addEventListener("click", async () => {
          try {
            await api("/api/auth/users/" + encodeURIComponent(u.username) + "/unlock", "POST");
            showAlert("ปลดล็อก " + u.username + " แล้ว", true);
            load();
          } catch (e) { showAlert(e.message, false); }
        });
        actions.splice(3, 0, unlockBtn);  // before the toggle
      }

      const status = el("span", {
        class: u.locked ? "status-pill locked" : (u.is_active ? "status-pill ok" : "status-pill off"),
        text: u.locked ? "ถูกล็อก" : (u.is_active ? "ใช้งานได้" : "ปิด"),
      });

      tb.appendChild(el("tr", null, [
        el("td", { text: u.username }),
        el("td", { text: u.email || "—" }),
        el("td", null, [sel]),
        el("td", null, [status]),
        el("td", { text: u.last_login_at ? u.last_login_at.replace("T", " ").slice(0, 16) : "—" }),
        el("td", { class: "row-actions" }, actions),
      ]));
    });
  }

  // Filter the loaded users by the search box, then re-render.
  function applyUserFilter() {
    const q = (document.getElementById("user-search").value || "").trim().toLowerCase();
    const filtered = !q ? ALL_USERS : ALL_USERS.filter((u) =>
      (u.username || "").toLowerCase().indexOf(q) >= 0 ||
      (u.email || "").toLowerCase().indexOf(q) >= 0);
    renderUsers(filtered);
  }

  // ── Roles ───────────────────────────────────────────────────────────
  function permGrid(checkedKeys, namePrefix) {
    const grid = el("div", { class: "perm-grid" });
    PERMISSIONS.forEach((p) => {
      const cb = el("input", { type: "checkbox", value: p.key });
      cb.checked = checkedKeys.indexOf(p.key) >= 0;
      cb.dataset.permGroup = namePrefix;
      const lab = el("label", { class: "perm-item" }, [cb,
        el("span", { text: p.label + "  (" + p.key + ")" })]);
      grid.appendChild(lab);
    });
    return grid;
  }

  function selectedPerms(container) {
    return Array.prototype.slice
      .call(container.querySelectorAll("input[type=checkbox]:checked"))
      .map((c) => c.value);
  }

  function renderRoles() {
    const wrap = document.getElementById("roles-list");
    wrap.innerHTML = "";
    ROLES.forEach((r) => {
      const grid = permGrid(r.permissions, "role" + r.role_id);
      const head = el("div", { class: "role-head" }, [
        el("strong", { text: r.name }),
        el("span", { class: "role-count", text: r.user_count + " ผู้ใช้" }),
      ]);
      const descInput = el("input", {
        type: "text", class: "role-desc", value: r.description || "",
        placeholder: "คำอธิบาย",
      });
      const saveBtn = el("button", { class: "btn btn-primary btn-sm", text: "บันทึก" });
      saveBtn.addEventListener("click", async () => {
        try {
          await api("/api/auth/roles/" + r.role_id, "PUT",
                    { permissions: selectedPerms(grid), description: descInput.value });
          showAlert("บันทึกสิทธิ์ของ " + r.name + " แล้ว", true);
          load();
        } catch (e) { showAlert(e.message, false); }
      });
      const delBtn = el("button", { class: "btn btn-danger btn-sm", text: "ลบ" });
      delBtn.addEventListener("click", async () => {
        if (!confirm("ลบ role '" + r.name + "' ?")) return;
        try {
          await api("/api/auth/roles/" + r.role_id, "DELETE");
          showAlert("ลบ role แล้ว", true);
          load();
        } catch (e) { showAlert(e.message, false); }
      });

      wrap.appendChild(el("div", { class: "role-card" }, [
        head, descInput, grid,
        el("div", { class: "role-actions" }, [saveBtn, delBtn]),
      ]));
    });
  }

  // ── Modal helpers ────────────────────────────────────────────────────
  function openModal(modal) {
    modal.hidden = false;
    document.body.classList.add("modal-open");
  }
  function closeModal(modal) {
    modal.hidden = true;
    document.body.classList.remove("modal-open");
  }
  function wireModal(modal, openBtn, closeBtns, onReset) {
    if (openBtn) openBtn.addEventListener("click", () => {
      if (onReset) onReset();
      openModal(modal);
    });
    closeBtns.forEach((b) => b.addEventListener("click", () => closeModal(modal)));
    modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(modal); });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !modal.hidden) closeModal(modal);
    });
  }

  function wirePasswordToggles(scope) {
    scope.querySelectorAll(".pw-toggle").forEach((btn) => {
      btn.addEventListener("click", () => {
        const input = document.getElementById(btn.dataset.target);
        const show = input.type === "password";
        input.type = show ? "text" : "password";
        btn.textContent = show ? "ซ่อน" : "แสดง";
      });
    });
  }

  // ── Create handlers ─────────────────────────────────────────────────
  function wireCreate() {
    const nuModal = document.getElementById("nu-modal");
    const nuPw = document.getElementById("nu-password");
    const nuPwConfirm = document.getElementById("nu-password-confirm");
    const nuMatchMsg = document.getElementById("nu-pw-match");

    function resetUserForm() {
      ["nu-username", "nu-email", "nu-password", "nu-password-confirm"].forEach((id) => {
        document.getElementById(id).value = "";
      });
      nuMatchMsg.hidden = true;
    }

    function passwordsMatch() {
      if (!nuPwConfirm.value) { nuMatchMsg.hidden = true; return false; }
      const match = nuPw.value === nuPwConfirm.value;
      nuMatchMsg.hidden = false;
      nuMatchMsg.textContent = match ? "✓ รหัสผ่านตรงกัน" : "รหัสผ่านไม่ตรงกัน";
      nuMatchMsg.className = "field-hint " + (match ? "ok" : "no");
      return match;
    }
    nuPw.addEventListener("input", () => { if (nuPwConfirm.value) passwordsMatch(); });
    nuPwConfirm.addEventListener("input", passwordsMatch);

    wireModal(nuModal, document.getElementById("nu-open"),
      [document.getElementById("nu-close"), document.getElementById("nu-cancel")],
      resetUserForm);
    wirePasswordToggles(nuModal);

    document.getElementById("nu-create").addEventListener("click", async () => {
      if (nuPw.value !== nuPwConfirm.value) {
        passwordsMatch();
        showAlert("รหัสผ่านและการยืนยันรหัสผ่านไม่ตรงกัน", false);
        return;
      }
      try {
        await api("/api/auth/users", "POST", {
          username: document.getElementById("nu-username").value.trim(),
          email: document.getElementById("nu-email").value.trim(),
          password: nuPw.value,
          role: document.getElementById("nu-role").value,
        });
        showAlert("สร้างบัญชีแล้ว", true);
        resetUserForm();
        closeModal(nuModal);
        load();
      } catch (e) { showAlert(e.message, false); }
    });

    const nrModal = document.getElementById("nr-modal");
    const nrPerms = document.getElementById("nr-perms");

    function resetRoleForm() {
      document.getElementById("nr-name").value = "";
      document.getElementById("nr-desc").value = "";
      fillNewRolePerms();
    }

    wireModal(nrModal, document.getElementById("nr-open"),
      [document.getElementById("nr-close"), document.getElementById("nr-cancel")],
      resetRoleForm);

    document.getElementById("nr-create").addEventListener("click", async () => {
      try {
        await api("/api/auth/roles", "POST", {
          name: document.getElementById("nr-name").value.trim(),
          description: document.getElementById("nr-desc").value.trim(),
          permissions: selectedPerms(nrPerms),
        });
        showAlert("สร้าง role แล้ว", true);
        resetRoleForm();
        closeModal(nrModal);
        load();
      } catch (e) { showAlert(e.message, false); }
    });
  }

  function fillRoleDropdown() {
    const sel = document.getElementById("nu-role");
    sel.innerHTML = "";
    ROLES.forEach((r) => sel.appendChild(el("option", { value: r.name, text: r.name })));
  }

  function fillNewRolePerms() {
    const box = document.getElementById("nr-perms");
    box.innerHTML = "";
    box.appendChild(permGrid([], "newrole"));
  }

  // ── Edit user + reset password modals ────────────────────────────────
  let editTarget = null;    // username currently open in the edit modal
  let resetTarget = null;   // username currently open in the reset-pw modal

  function openEditUser(u) {
    editTarget = u.username;
    document.getElementById("eu-username").value = u.username;
    document.getElementById("eu-email").value = u.email || "";
    openModal(document.getElementById("eu-modal"));
  }

  function openResetPassword(u) {
    resetTarget = u.username;
    document.getElementById("rp-username").textContent = u.username;
    document.getElementById("rp-password").value = "";
    document.getElementById("rp-password-confirm").value = "";
    document.getElementById("rp-pw-match").hidden = true;
    openModal(document.getElementById("rp-modal"));
  }

  function wireEditAndReset() {
    const euModal = document.getElementById("eu-modal");
    wireModal(euModal, null,  // opened per-row, no dedicated open button
      [document.getElementById("eu-close"), document.getElementById("eu-cancel")]);
    document.getElementById("eu-save").addEventListener("click", async () => {
      try {
        await api("/api/auth/users/" + encodeURIComponent(editTarget), "PATCH",
                  { email: document.getElementById("eu-email").value.trim() });
        showAlert("บันทึกข้อมูลของ " + editTarget + " แล้ว", true);
        closeModal(euModal);
        load();
      } catch (e) { showAlert(e.message, false); }
    });

    const rpModal = document.getElementById("rp-modal");
    const rpPw = document.getElementById("rp-password");
    const rpPwConfirm = document.getElementById("rp-password-confirm");
    const rpMatch = document.getElementById("rp-pw-match");
    function rpPasswordsMatch() {
      if (!rpPwConfirm.value) { rpMatch.hidden = true; return false; }
      const match = rpPw.value === rpPwConfirm.value;
      rpMatch.hidden = false;
      rpMatch.textContent = match ? "✓ รหัสผ่านตรงกัน" : "รหัสผ่านไม่ตรงกัน";
      rpMatch.className = "field-hint " + (match ? "ok" : "no");
      return match;
    }
    rpPw.addEventListener("input", () => { if (rpPwConfirm.value) rpPasswordsMatch(); });
    rpPwConfirm.addEventListener("input", rpPasswordsMatch);
    wireModal(rpModal, null,
      [document.getElementById("rp-close"), document.getElementById("rp-cancel")]);
    wirePasswordToggles(rpModal);
    document.getElementById("rp-save").addEventListener("click", async () => {
      if (rpPw.value !== rpPwConfirm.value) {
        rpPasswordsMatch();
        showAlert("รหัสผ่านและการยืนยันรหัสผ่านไม่ตรงกัน", false);
        return;
      }
      try {
        await api("/api/auth/users/" + encodeURIComponent(resetTarget) + "/reset-password",
                  "POST", { password: rpPw.value });
        showAlert("ตั้งรหัสผ่านใหม่ให้ " + resetTarget + " แล้ว", true);
        closeModal(rpModal);
      } catch (e) { showAlert(e.message, false); }
    });
  }

  // ── Load all ────────────────────────────────────────────────────────
  async function load() {
    try {
      const rolesData = await api("/api/auth/roles");
      ROLES = rolesData.roles;
      PERMISSIONS = rolesData.permissions;
      const usersData = await api("/api/auth/users");
      ALL_USERS = usersData.users;
      renderRoles();
      applyUserFilter();
      fillRoleDropdown();
    } catch (e) {
      showAlert("โหลดข้อมูลไม่สำเร็จ: " + e.message, false);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    fillNewRolePerms();
    wireCreate();
    wireEditAndReset();
    document.getElementById("user-search").addEventListener("input", applyUserFilter);
    load();
  });
})();
