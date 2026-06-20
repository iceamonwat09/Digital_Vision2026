/* Admin: manage users (assign role) + roles (tick permissions).
 * All endpoints require the manage_users permission (enforced server-side). */
(function () {
  "use strict";

  const alertBox = document.getElementById("admin-alert");
  let PERMISSIONS = [];   // [{key,label}]
  let ROLES = [];         // [{role_id,name,description,user_count,permissions[]}]

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

      const saveBtn = el("button", { class: "btn btn-secondary btn-sm", text: "บันทึก" });
      saveBtn.addEventListener("click", async () => {
        try {
          await api("/api/auth/users/" + encodeURIComponent(u.username) + "/role",
                    "POST", { role: sel.value });
          showAlert("อัปเดตบทบาทของ " + u.username + " แล้ว", true);
          load();
        } catch (e) { showAlert(e.message, false); }
      });

      const toggle = el("button", {
        class: "btn btn-sm " + (u.is_active ? "btn-secondary" : "btn-danger"),
        text: u.is_active ? "เปิดใช้งาน" : "ปิดอยู่",
      });
      toggle.addEventListener("click", async () => {
        try {
          await api("/api/auth/users/" + encodeURIComponent(u.username) + "/active",
                    "POST", { active: !u.is_active });
          load();
        } catch (e) { showAlert(e.message, false); }
      });

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
        el("td", { class: "row-actions" }, [saveBtn, toggle]),
      ]));
    });
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

  // ── Create handlers ─────────────────────────────────────────────────
  function wireCreate() {
    document.getElementById("nu-create").addEventListener("click", async () => {
      try {
        await api("/api/auth/users", "POST", {
          username: document.getElementById("nu-username").value.trim(),
          email: document.getElementById("nu-email").value.trim(),
          password: document.getElementById("nu-password").value,
          role: document.getElementById("nu-role").value,
        });
        showAlert("สร้างบัญชีแล้ว", true);
        ["nu-username", "nu-email", "nu-password"].forEach((id) => {
          document.getElementById(id).value = "";
        });
        load();
      } catch (e) { showAlert(e.message, false); }
    });

    const nrPerms = document.getElementById("nr-perms");
    document.getElementById("nr-create").addEventListener("click", async () => {
      try {
        await api("/api/auth/roles", "POST", {
          name: document.getElementById("nr-name").value.trim(),
          description: document.getElementById("nr-desc").value.trim(),
          permissions: selectedPerms(nrPerms),
        });
        showAlert("สร้าง role แล้ว", true);
        document.getElementById("nr-name").value = "";
        document.getElementById("nr-desc").value = "";
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

  // ── Load all ────────────────────────────────────────────────────────
  async function load() {
    try {
      const rolesData = await api("/api/auth/roles");
      ROLES = rolesData.roles;
      PERMISSIONS = rolesData.permissions;
      const usersData = await api("/api/auth/users");
      renderRoles();
      renderUsers(usersData.users);
      fillRoleDropdown();
    } catch (e) {
      showAlert("โหลดข้อมูลไม่สำเร็จ: " + e.message, false);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    fillNewRolePerms();
    wireCreate();
    load();
  });
})();
