/* SkillsManager + Admin Users/Settings — depends on app.js (api, state, $, escapeHtml, setMsg) */

const SKILLS_EXTRA_COLS_KEY = "skills-extra-cols";

const skillsState = {
  agents: [],
  skills: [],
  teams: [],
  templates: [],
  schedules: [],
  dirtyAgents: new Set(),
  selectedAgents: new Set(),
  apps: [],
  users: [],
  /** Skill IDs supplémentaires affichés même s’ils sont à 0 partout */
  extraSkillIds: loadExtraSkillIds(),
  editingTemplateId: null,
};

const DOW_LABELS = { 1: "Lun", 2: "Mar", 3: "Mer", 4: "Jeu", 5: "Ven", 6: "Sam", 7: "Dim" };

function loadExtraSkillIds() {
  try {
    const raw = sessionStorage.getItem(SKILLS_EXTRA_COLS_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(arr) ? arr.map(String) : []);
  } catch {
    return new Set();
  }
}

function saveExtraSkillIds() {
  sessionStorage.setItem(
    SKILLS_EXTRA_COLS_KEY,
    JSON.stringify([...skillsState.extraSkillIds])
  );
}

function sortSkillsByName(skills) {
  return [...(skills || [])].sort((a, b) =>
    String(a.name || a.id || "").localeCompare(String(b.name || b.id || ""), "fr", {
      sensitivity: "base",
    })
  );
}

/** Skills utilisés (level ≥ 1) chez au moins un agent. */
function activeSkillIds() {
  const ids = new Set();
  for (const a of skillsState.agents || []) {
    for (const s of a.skillMap || []) {
      if (s.level != null && Number(s.level) >= 1) {
        if (s.id) ids.add(String(s.id));
      }
    }
  }
  return ids;
}

/** Colonnes dashboard = actifs ∪ extras, tri A–Z. */
function matrixSkillColumns() {
  const active = activeSkillIds();
  const want = new Set([...active, ...skillsState.extraSkillIds]);
  const byId = new Map((skillsState.skills || []).map((s) => [String(s.id), s]));
  const cols = [];
  for (const id of want) {
    const sk = byId.get(id);
    if (sk) cols.push(sk);
  }
  return sortSkillsByName(cols);
}

function renderExtrasPicker() {
  const box = $("skills-extras-list");
  if (!box) return;
  const q = ($("skills-extras-filter")?.value || "").trim().toLowerCase();
  const active = activeSkillIds();
  let inactive = sortSkillsByName(
    (skillsState.skills || []).filter((s) => !active.has(String(s.id)))
  );
  if (q) {
    inactive = inactive.filter(
      (s) =>
        String(s.name || "").toLowerCase().includes(q) ||
        String(s.id || "").toLowerCase().includes(q)
    );
  }
  if (!(skillsState.skills || []).filter((s) => !active.has(String(s.id))).length) {
    box.innerHTML = `<p class="meta">Tous les skills du catalogue sont déjà utilisés (niveau ≥ 1).</p>`;
    return;
  }
  if (!inactive.length) {
    box.innerHTML = `<p class="meta">Aucun skill ne correspond au filtre.</p>`;
    return;
  }
  box.innerHTML = inactive
    .map((s) => {
      const id = String(s.id);
      const checked = skillsState.extraSkillIds.has(id) ? " checked" : "";
      return `<label class="skills-extra-item" title="${escapeHtml(s.name)} (${escapeHtml(id)})">
        <input type="checkbox" data-extra-skill-id="${escapeHtml(id)}"${checked} />
        <span>${escapeHtml(s.name)}</span>
      </label>`;
    })
    .join("");
}

function applyExtrasSelection() {
  const box = $("skills-extras-list");
  if (!box) return;
  const next = new Set();
  box.querySelectorAll("[data-extra-skill-id]").forEach((cb) => {
    if (cb.checked) next.add(String(cb.dataset.extraSkillId));
  });
  skillsState.extraSkillIds = next;
  saveExtraSkillIds();
  renderSkillsMatrix();
  setMsg("skills-msg", `${next.size} skill(s) supplémentaire(s) affiché(s)`, "ok");
}

function setNav(view) {
  state.navView = view;
  document.querySelectorAll(".nav-btn").forEach((b) => {
    b.classList.toggle("btn-primary", b.dataset.nav === view);
    b.classList.toggle("btn-default", b.dataset.nav !== view);
  });
  const sidebar = $("apps-sidebar");
  if (sidebar) sidebar.classList.toggle("hidden", view !== "apps");
  const layout = document.querySelector(".portal-layout");
  if (layout) layout.classList.toggle("layout-full", view !== "apps");
  ["apps", "skills", "admin-users", "admin-settings"].forEach((v) => {
    const el = $(`view-${v}`);
    if (el) el.classList.toggle("hidden", v !== view);
  });
  if (view === "skills") void loadSkillsPage();
  if (view === "admin-users") void loadAdminUsers();
  if (view === "admin-settings") void loadAdminSettings();
}

function updateNavVisibility() {
  const me = state.me || {};
  const isAdmin = me.role === "admin";
  const apps = isAdmin || me.can_access_apps !== false;
  const skills = isAdmin || !!me.can_access_skills;
  document.querySelectorAll('[data-nav="apps"]').forEach((b) =>
    b.classList.toggle("hidden", !apps)
  );
  document.querySelectorAll('[data-nav="skills"]').forEach((b) =>
    b.classList.toggle("hidden", !skills)
  );
  document.querySelectorAll('[data-nav="admin-users"], [data-nav="admin-settings"]').forEach((b) =>
    b.classList.toggle("hidden", !isAdmin)
  );
  // Vue par défaut
  if (apps) setNav("apps");
  else if (skills) setNav("skills");
  else if (isAdmin) setNav("admin-users");
  else setNav("apps");
}

async function initSamlLoginButton() {
  try {
    const st = await fetch("/api/auth/saml/status").then((r) => r.json());
    const wrap = $("saml-login-wrap");
    if (wrap) wrap.classList.toggle("hidden", !st.enabled);
    const details = $("local-login-details");
    if (details && st.enabled) details.open = false;
  } catch {
    /* ignore */
  }
}

/* ——— Skills ——— */

async function loadSkillsPage() {
  setMsg("skills-msg", "Chargement…", "meta");
  try {
    const [catalog, agentsPayload, templates, schedules] = await Promise.all([
      api("/api/skills/catalog"),
      api("/api/skills/agents"),
      api("/api/skills/templates"),
      api("/api/skills/schedules"),
    ]);
    skillsState.skills = sortSkillsByName(catalog.skills || []);
    skillsState.teams = catalog.teams || [];
    skillsState.agents = agentsPayload.agents || [];
    skillsState.templates = templates || [];
    skillsState.schedules = schedules || [];
    skillsState.dirtyAgents.clear();
    const validIds = new Set(skillsState.agents.map((a) => a.id));
    skillsState.selectedAgents = new Set(
      [...skillsState.selectedAgents].filter((id) => validIds.has(id))
    );
    renderExtrasPicker();
    renderSkillsMatrix();
    renderTemplates();
    renderSchedules();
    fillScheduleSelects();
    fillBulkControls();
    updateBulkCount();
    setMsg("skills-msg", `${skillsState.agents.length} agents`, "meta");
  } catch (err) {
    setMsg("skills-msg", err.message, "error");
  }
}

function _rolesBadges(agent) {
  const roles = agent.roles || [];
  const badges = roles
    .map((r) => `<span class="role-badge role-${escapeHtml(r.toLowerCase())}">${escapeHtml(r)}</span>`)
    .join(" ");
  const prim = (agent.primarySupervisorOf || []).join(", ");
  const sec = (agent.secondarySupervisorOf || []).join(", ");
  const extra = [];
  if (prim) extra.push(`prim: ${prim}`);
  if (sec) extra.push(`sec: ${sec}`);
  const meta = extra.length
    ? `<br/><span class="meta">${escapeHtml(extra.join(" · "))}</span>`
    : "";
  return `${badges || '<span class="role-badge">Agent</span>'}${meta}`;
}

function _teamSelectHtml(agent) {
  const opts = skillsState.teams
    .map((t) => {
      const sel = t.name === agent.team ? " selected" : "";
      return `<option value="${escapeHtml(t.name)}"${sel}>${escapeHtml(t.name)}</option>`;
    })
    .join("");
  const orphan =
    agent.team && !skillsState.teams.some((t) => t.name === agent.team)
      ? `<option value="${escapeHtml(agent.team)}" selected>${escapeHtml(agent.team)}</option>`
      : "";
  return `<select class="form-control skills-team-select" data-agent-id="${escapeHtml(agent.id)}">${orphan}${opts}</select>`;
}

function filteredAgents() {
  const filter = ($("skills-agent-filter")?.value || "").toLowerCase();
  return skillsState.agents.filter(
    (a) =>
      !filter ||
      (a.name || "").toLowerCase().includes(filter) ||
      (a.id || "").toLowerCase().includes(filter) ||
      (a.rolesLabel || "").toLowerCase().includes(filter)
  );
}

function updateBulkCount() {
  const el = $("skills-bulk-count");
  if (el) el.textContent = `${skillsState.selectedAgents.size} agent(s)`;
}

function fillBulkControls() {
  const tplSel = $("skills-bulk-template");
  const fromSel = $("skills-from-agent");
  const tplOpts = (skillsState.templates || [])
    .map((t) => `<option value="${t.id}">${escapeHtml(t.name)}</option>`)
    .join("");
  if (tplSel) tplSel.innerHTML = tplOpts || `<option value="">— template —</option>`;
  if (fromSel) {
    fromSel.innerHTML = (skillsState.agents || [])
      .map(
        (a) =>
          `<option value="${escapeHtml(a.id)}">${escapeHtml(a.name)} (${escapeHtml(a.id)})</option>`
      )
      .join("");
  }
}

function renderSkillsMatrix() {
  const agents = filteredAgents();
  const cols = matrixSkillColumns();
  const head = $("skills-matrix-head");
  const body = $("skills-matrix-body");
  if (!head || !body) return;
  const allFilteredSelected =
    agents.length > 0 && agents.every((a) => skillsState.selectedAgents.has(a.id));
  const selectAllTh = `<th class="skills-select-cell">
    <input type="checkbox" id="skills-select-all" title="Tout sélectionner (filtre)" ${allFilteredSelected ? "checked" : ""} />
  </th>`;
  if (!cols.length) {
    head.innerHTML = `<tr>
      ${selectAllTh}
      <th class="skills-agent-cell">Agent</th>
      <th class="skills-roles-cell">Rôles</th>
      <th class="skills-team-cell">Team</th>
    </tr>`;
    if (!agents.length) {
      body.innerHTML = "";
    } else {
      body.innerHTML =
        agents
          .map((a) => {
            const dirty = skillsState.dirtyAgents.has(a.id) ? " is-dirty" : "";
            const checked = skillsState.selectedAgents.has(a.id) ? " checked" : "";
            return `<tr class="skills-agent-row${dirty}" data-agent-id="${escapeHtml(a.id)}">
        <td class="skills-select-cell">
          <input type="checkbox" class="skills-agent-check" data-agent-id="${escapeHtml(a.id)}"${checked} />
        </td>
        <td class="skills-agent-cell" title="${escapeHtml(a.name)} / ${escapeHtml(a.id)}">
          <span class="skills-agent-name">${escapeHtml(a.name)}</span>
          <span class="skills-agent-id">${escapeHtml(a.id)}</span>
        </td>
        <td class="skills-roles-cell">${_rolesBadges(a)}</td>
        <td class="skills-team-cell">${_teamSelectHtml(a)}</td>
      </tr>`;
          })
          .join("") +
        `<tr><td colspan="4" class="meta">Aucun skill actif (niveau ≥ 1). Ajoutez des skills supplémentaires ci-dessus.</td></tr>`;
      body.querySelectorAll(".skills-agent-check").forEach((cb) => {
        cb.addEventListener("change", () => {
          if (cb.checked) skillsState.selectedAgents.add(cb.dataset.agentId);
          else skillsState.selectedAgents.delete(cb.dataset.agentId);
          updateBulkCount();
        });
      });
      body.querySelectorAll(".skills-team-select").forEach((el) => {
        el.addEventListener("change", () => {
          skillsState.dirtyAgents.add(el.dataset.agentId);
        });
      });
    }
    $("skills-select-all")?.addEventListener("change", (e) => {
      toggleSelectFiltered(e.target.checked);
    });
    updateBulkCount();
    return;
  }
  head.innerHTML = `<tr>
    ${selectAllTh}
    <th class="skills-agent-cell">Agent</th>
    <th class="skills-roles-cell">Rôles</th>
    <th class="skills-team-cell">Team</th>
    ${cols
      .map(
        (c) =>
          `<th class="skills-skill-head" title="${escapeHtml(c.name)} (${escapeHtml(c.id)})">${escapeHtml(c.name)}</th>`
      )
      .join("")}
  </tr>`;
  body.innerHTML = agents
    .map((a) => {
      const map = {};
      for (const s of a.skillMap || []) {
        map[s.id] = s.level;
        if (s.name) map[s.name] = s.level;
      }
      const cells = cols
        .map((c) => {
          const lvl = map[c.id] ?? map[c.name];
          const val = lvl != null && lvl > 0 ? String(lvl) : "";
          return `<td class="skills-level-cell"><input type="number" class="form-control skills-level-input" min="0" max="10"
            data-agent-id="${escapeHtml(a.id)}"
            data-skill-id="${escapeHtml(c.id)}"
            data-skill-name="${escapeHtml(c.name)}"
            value="${escapeHtml(val)}"
            title="${escapeHtml(c.name)} (0 = retirer)"/></td>`;
        })
        .join("");
      const dirty = skillsState.dirtyAgents.has(a.id) ? " is-dirty" : "";
      const checked = skillsState.selectedAgents.has(a.id) ? " checked" : "";
      return `<tr class="skills-agent-row${dirty}" data-agent-id="${escapeHtml(a.id)}">
        <td class="skills-select-cell">
          <input type="checkbox" class="skills-agent-check" data-agent-id="${escapeHtml(a.id)}"${checked} />
        </td>
        <td class="skills-agent-cell" title="${escapeHtml(a.name)} / ${escapeHtml(a.id)}">
          <span class="skills-agent-name">${escapeHtml(a.name)}</span>
          <span class="skills-agent-id">${escapeHtml(a.id)}</span>
        </td>
        <td class="skills-roles-cell">${_rolesBadges(a)}</td>
        <td class="skills-team-cell">${_teamSelectHtml(a)}</td>
        ${cells}
      </tr>`;
    })
    .join("");

  const markDirty = (agentId) => {
    skillsState.dirtyAgents.add(agentId);
    body
      .querySelector(`tr[data-agent-id="${CSS.escape(agentId)}"]`)
      ?.classList.add("is-dirty");
  };
  body.querySelectorAll(".skills-level-input, .skills-team-select").forEach((el) => {
    el.addEventListener("change", () => markDirty(el.dataset.agentId));
    el.addEventListener("input", () => markDirty(el.dataset.agentId));
  });
  body.querySelectorAll(".skills-agent-check").forEach((cb) => {
    cb.addEventListener("change", () => {
      if (cb.checked) skillsState.selectedAgents.add(cb.dataset.agentId);
      else skillsState.selectedAgents.delete(cb.dataset.agentId);
      updateBulkCount();
    });
  });
  $("skills-select-all")?.addEventListener("change", (e) => {
    toggleSelectFiltered(e.target.checked);
  });
  updateBulkCount();
}

function toggleSelectFiltered(on) {
  for (const a of filteredAgents()) {
    if (on) skillsState.selectedAgents.add(a.id);
    else skillsState.selectedAgents.delete(a.id);
  }
  renderSkillsMatrix();
}

function switchSkillsTab(tab) {
  document.querySelectorAll(".skills-tab").forEach((b) => {
    const on = b.dataset.skillsTab === tab;
    b.classList.toggle("btn-primary", on);
    b.classList.toggle("btn-default", !on);
  });
  $("skills-matrix-panel")?.classList.toggle("hidden", tab !== "matrix");
  $("skills-templates-panel")?.classList.toggle("hidden", tab !== "templates");
  $("skills-schedules-panel")?.classList.toggle("hidden", tab !== "schedules");
}

async function applyTemplateToSelected() {
  const agent_ids = [...skillsState.selectedAgents];
  const template_id = Number($("skills-bulk-template")?.value);
  if (!agent_ids.length) {
    setMsg("skills-msg", "Sélectionnez au moins un agent", "warn");
    return;
  }
  if (!template_id) {
    setMsg("skills-msg", "Choisissez un template", "warn");
    return;
  }
  setMsg("skills-msg", `Application template à ${agent_ids.length} agent(s)…`, "meta");
  try {
    const res = await api("/api/skills/apply", {
      method: "POST",
      json: { agent_ids, template_id },
    });
    const failN = (res.failed || []).length;
    const okN = (res.ok || []).length;
    if (failN) {
      setMsg(
        "skills-msg",
        `Appliqué: ${okN}, échecs: ${failN} — ${(res.failed || []).map((f) => f.agent_id).join(", ")}`,
        "warn"
      );
    } else {
      setMsg("skills-msg", `Template appliqué à ${okN} agent(s)`, "ok");
    }
    await loadSkillsPage();
  } catch (err) {
    setMsg("skills-msg", err.message, "error");
  }
}

function goScheduleSelected() {
  const ids = [...skillsState.selectedAgents];
  if (!ids.length) {
    setMsg("skills-msg", "Sélectionnez au moins un agent", "warn");
    return;
  }
  fillScheduleSelects();
  const sel = $("sched-agents");
  if (sel) {
    [...sel.options].forEach((opt) => {
      opt.selected = ids.includes(opt.value);
    });
  }
  switchSkillsTab("schedules");
}

async function createTemplateFromAgent() {
  let agent_id = $("skills-from-agent")?.value;
  if (skillsState.selectedAgents.size === 1) {
    agent_id = [...skillsState.selectedAgents][0];
  }
  if (!agent_id) {
    setMsg("skills-msg", "Choisissez un agent source", "warn");
    return;
  }
  const name = prompt("Nom du nouveau template");
  if (!name) return;
  try {
    await api("/api/skills/templates/from-agent", {
      method: "POST",
      json: { agent_id, name: name.trim() },
    });
    setMsg("skills-msg", `Template « ${name.trim()} » créé`, "ok");
    await loadSkillsPage();
    switchSkillsTab("templates");
  } catch (err) {
    setMsg("skills-msg", err.message, "error");
  }
}

async function saveDirtyAgentSkills() {
  const ids = [...skillsState.dirtyAgents];
  if (!ids.length) {
    setMsg("skills-msg", "Aucune ligne modifiée", "warn");
    return;
  }
  setMsg("skills-msg", `Enregistrement de ${ids.length} agent(s)…`, "meta");
  let ok = 0;
  try {
    for (const agentId of ids) {
      const row = document.querySelector(
        `#skills-matrix-body tr[data-agent-id="${CSS.escape(agentId)}"]`
      );
      if (!row) continue;
      const visibleIds = new Set();
      const skills = [];
      row.querySelectorAll(".skills-level-input").forEach((inp) => {
        const sid = String(inp.dataset.skillId || "");
        visibleIds.add(sid);
        const level = Number(inp.value);
        if (!level || level < 1) return;
        skills.push({
          id: sid,
          name: inp.dataset.skillName,
          level: Math.min(10, Math.max(1, level)),
        });
      });
      // Préserver les skills déjà assignés hors colonnes visibles (masqués à 0)
      const agent = skillsState.agents.find((a) => a.id === agentId);
      for (const s of agent?.skillMap || []) {
        const sid = String(s.id || "");
        if (!sid || visibleIds.has(sid)) continue;
        if (s.level != null && Number(s.level) >= 1) {
          skills.push({
            id: sid,
            name: s.name || sid,
            level: Math.min(10, Math.max(1, Number(s.level))),
          });
        }
      }
      const team = row.querySelector(".skills-team-select")?.value || null;
      await api("/api/skills/agents", {
        method: "PUT",
        json: { agent_id: agentId, skills, team },
      });
      ok += 1;
      skillsState.dirtyAgents.delete(agentId);
    }
    setMsg("skills-msg", `${ok} agent(s) sauvegardé(s)`, "ok");
    await loadSkillsPage();
  } catch (err) {
    setMsg("skills-msg", err.message, "error");
  }
}

function renderTemplates() {
  const list = $("templates-list");
  if (!list) return;
  if (!skillsState.templates.length) {
    list.innerHTML = `<p class="meta">Aucun template</p>`;
    return;
  }
  list.innerHTML = skillsState.templates
    .map((t) => {
      const editing = skillsState.editingTemplateId === t.id;
      const skillsSummary = sortSkillsByName(
        (t.skills || [])
          .filter((s) => Number(s.skill_level) > 0)
          .map((s) => ({
            id: s.skill_id,
            name: s.skill_name || s.skill_id,
            level: s.skill_level,
          }))
      )
        .map(
          (s) =>
            `<span class="meta">${escapeHtml(s.name)}:${s.level}</span>`
        )
        .join(" ");
      const editor = editing ? renderTemplateEditorHtml(t) : "";
      return `<div class="panel panel-default skills-template-card" data-template-id="${t.id}">
        <div class="panel-body">
          <strong>${escapeHtml(t.name)}</strong>
          <span class="meta">${escapeHtml(t.description || "")}</span>
          <div>${skillsSummary || "<span class='meta'>pas de skills</span>"}</div>
          <div class="portal-actions" style="margin-top:0.5rem">
            <button type="button" class="btn btn-default btn-sm" data-tpl-edit="${t.id}">
              ${editing ? "Fermer" : "Éditer"}
            </button>
            <button type="button" class="btn btn-default btn-sm" data-tpl-del="${t.id}">Supprimer</button>
          </div>
          ${editor}
        </div>
      </div>`;
    })
    .join("");

  list.querySelectorAll("[data-tpl-del]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("Supprimer ce template ?")) return;
      try {
        await api(`/api/skills/templates/${btn.dataset.tplDel}`, { method: "DELETE" });
        if (skillsState.editingTemplateId === Number(btn.dataset.tplDel)) {
          skillsState.editingTemplateId = null;
        }
        await loadSkillsPage();
      } catch (err) {
        setMsg("skills-msg", err.message, "error");
      }
    });
  });
  list.querySelectorAll("[data-tpl-edit]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = Number(btn.dataset.tplEdit);
      skillsState.editingTemplateId =
        skillsState.editingTemplateId === id ? null : id;
      renderTemplates();
    });
  });
  list.querySelectorAll("[data-tpl-save]").forEach((btn) => {
    btn.addEventListener("click", () => saveTemplateEditor(Number(btn.dataset.tplSave)));
  });
}

function renderTemplateEditorHtml(t) {
  const have = new Map(
    (t.skills || []).map((s) => [String(s.skill_id), Number(s.skill_level) || 0])
  );
  const rows = sortSkillsByName(skillsState.skills)
    .map((sk) => {
      const id = String(sk.id);
      const raw = have.has(id) ? have.get(id) : 0;
      const lvl = raw > 0 ? String(raw) : "0";
      return `<label class="skills-tpl-skill-row" title="${escapeHtml(sk.name)} (${escapeHtml(id)})">
        <span class="skills-tpl-skill-name">${escapeHtml(sk.name)}</span>
        <input type="number" class="form-control skills-tpl-level" min="0" max="10"
          data-tpl-skill-level data-skill-id="${escapeHtml(id)}"
          data-skill-name="${escapeHtml(sk.name)}" value="${escapeHtml(lvl)}" />
      </label>`;
    })
    .join("");
  return `<div class="skills-tpl-editor">
    <p class="meta">Tous les skills sont éditables. Seuls ceux avec un poids &gt; 0 seront associés au template.</p>
    <div class="skills-tpl-skills-list">${rows || "<p class='meta'>Catalogue vide</p>"}</div>
    <div class="portal-actions" style="margin-top:0.5rem">
      <button type="button" class="btn btn-primary btn-sm" data-tpl-save="${t.id}">Enregistrer le template</button>
    </div>
  </div>`;
}

async function saveTemplateEditor(id) {
  const card = document.querySelector(
    `.skills-template-card[data-template-id="${CSS.escape(String(id))}"]`
  );
  if (!card) return;
  const skills = [];
  card.querySelectorAll("[data-tpl-skill-level]").forEach((inp) => {
    const level = Math.min(10, Math.max(0, Number(inp.value) || 0));
    if (level <= 0) return;
    skills.push({
      skill_id: inp.dataset.skillId,
      skill_name: inp.dataset.skillName || inp.dataset.skillId,
      skill_level: level,
    });
  });
  setMsg("skills-msg", "Enregistrement template…", "meta");
  try {
    await api(`/api/skills/templates/${id}`, {
      method: "PUT",
      json: { skills },
    });
    skillsState.editingTemplateId = null;
    setMsg("skills-msg", "Template enregistré", "ok");
    await loadSkillsPage();
  } catch (err) {
    setMsg("skills-msg", err.message, "error");
  }
}

async function createTemplate() {
  const name = prompt("Nom du template");
  if (!name) return;
  try {
    const created = await api("/api/skills/templates", {
      method: "POST",
      json: { name, description: "", skills: [] },
    });
    skillsState.editingTemplateId = created?.id || null;
    await loadSkillsPage();
  } catch (err) {
    setMsg("skills-msg", err.message, "error");
  }
}

function fillScheduleSelects() {
  const agentSel = $("sched-agents");
  const oldSel = $("sched-old");
  const newSel = $("sched-new");
  const prev = agentSel
    ? new Set([...agentSel.selectedOptions].map((o) => o.value))
    : new Set();
  if (agentSel) {
    agentSel.innerHTML = skillsState.agents
      .map(
        (a) =>
          `<option value="${escapeHtml(a.id)}">${escapeHtml(a.name)} (${escapeHtml(a.id)})</option>`
      )
      .join("");
    [...agentSel.options].forEach((opt) => {
      opt.selected = prev.has(opt.value) || skillsState.selectedAgents.has(opt.value);
    });
  }
  const tplOpts = skillsState.templates
    .map((t) => `<option value="${t.id}">${escapeHtml(t.name)}</option>`)
    .join("");
  if (oldSel) oldSel.innerHTML = tplOpts;
  if (newSel) newSel.innerHTML = tplOpts;
}

function selectedDowCsv() {
  const days = [...document.querySelectorAll(".skills-dow-btn.is-on")]
    .map((b) => Number(b.dataset.dow))
    .filter((d) => d >= 1 && d <= 7)
    .sort((a, b) => a - b);
  return days.join(",");
}

function formatScheduleWindow(s) {
  const kind = s.kind || "once";
  if (kind === "recurring") {
    const days = (s.weekdays || "")
      .split(",")
      .map((d) => DOW_LABELS[Number(d.trim())] || d.trim())
      .filter(Boolean)
      .join("·");
    return `${days || "—"} ${s.time_start || "?"}–${s.time_end || "?"}`;
  }
  return `${s.start_at || "?"} → ${s.end_at || "?"}`;
}

function toggleSchedKindFields() {
  const kind =
    document.querySelector('input[name="sched-kind"]:checked')?.value || "recurring";
  $("sched-once-fields")?.classList.toggle("hidden", kind !== "once");
  $("sched-recur-fields")?.classList.toggle("hidden", kind !== "recurring");
}

function renderSchedules() {
  const body = $("schedules-body");
  if (!body) return;
  body.innerHTML = (skillsState.schedules || [])
    .map((s) => {
      const kind = s.kind || "once";
      const active = s.active ?? s.done;
      const state =
        kind === "recurring"
          ? active
            ? "New actif"
            : "Old (hors créneau)"
          : active
            ? "appliqué"
            : "en attente";
      return `<tr>
      <td>${escapeHtml(s.agent_id)}</td>
      <td>${kind === "recurring" ? "Récurrent" : "Une fois"}</td>
      <td>${escapeHtml(formatScheduleWindow(s))}</td>
      <td>${escapeHtml(s.old_template_name)} → ${escapeHtml(s.new_template_name)}</td>
      <td>${escapeHtml(state)}</td>
      <td><button type="button" class="btn btn-default btn-sm" data-sched-del="${s.id}">Suppr.</button></td>
    </tr>`;
    })
    .join("");
  body.querySelectorAll("[data-sched-del]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await api(`/api/skills/schedules/${btn.dataset.schedDel}`, { method: "DELETE" });
        await loadSkillsPage();
      } catch (err) {
        setMsg("skills-msg", err.message, "error");
      }
    });
  });
}

async function createSchedule() {
  const agentSel = $("sched-agents");
  const agent_ids = agentSel
    ? [...agentSel.selectedOptions].map((o) => o.value)
    : [];
  const old_template_id = Number($("sched-old")?.value);
  const new_template_id = Number($("sched-new")?.value);
  const kind =
    document.querySelector('input[name="sched-kind"]:checked')?.value || "recurring";
  if (!agent_ids.length || !old_template_id || !new_template_id) {
    setMsg("skills-msg", "Sélectionnez agents et templates Old/New", "warn");
    return;
  }
  const payload = {
    agent_ids,
    old_template_id,
    new_template_id,
    kind,
    apply_now: true,
  };
  if (kind === "once") {
    const startLocal = $("sched-start")?.value;
    const endLocal = $("sched-end")?.value;
    if (!endLocal) {
      setMsg("skills-msg", "Date de fin requise", "warn");
      return;
    }
    if (startLocal) payload.start_at = new Date(startLocal).toISOString();
    payload.end_at = new Date(endLocal).toISOString();
  } else {
    const weekdays = selectedDowCsv();
    const time_start = $("sched-time-start")?.value;
    const time_end = $("sched-time-end")?.value;
    if (!weekdays || !time_start || !time_end) {
      setMsg("skills-msg", "Jours et heures requis", "warn");
      return;
    }
    payload.weekdays = weekdays;
    payload.time_start = time_start;
    payload.time_end = time_end;
    const until = $("sched-valid-until")?.value;
    if (until) payload.end_at = new Date(until).toISOString();
  }
  try {
    const rows = await api("/api/skills/schedules", { method: "POST", json: payload });
    setMsg("skills-msg", `Planification créée (${(rows || []).length} agent(s))`, "ok");
    await loadSkillsPage();
  } catch (err) {
    setMsg("skills-msg", err.message, "error");
  }
}

/* ——— Admin users ——— */

async function loadAdminUsers() {
  setMsg("users-msg", "Chargement…", "meta");
  try {
    const [users, apps, teams] = await Promise.all([
      api("/api/admin/users"),
      api("/api/admin/applications"),
      api("/api/admin/teams").catch(() => ({ teams: [] })),
    ]);
    skillsState.apps = apps || [];
    skillsState.teams = teams.teams || skillsState.teams;
    skillsState.users = users || [];
    renderUsers();
    setMsg("users-msg", `${skillsState.users.length} utilisateurs`, "meta");
  } catch (err) {
    setMsg("users-msg", err.message, "error");
  }
}

function _userRolesBadges(rolesStr) {
  const parts = String(rolesStr || "")
    .split(/[/|,;]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (!parts.length) return '<span class="meta">—</span>';
  return parts
    .map((r) => {
      const cls = r.toLowerCase().includes("supervis")
        ? "role-supervisor"
        : r.toLowerCase().includes("agent")
          ? "role-agent"
          : "";
      return `<span class="role-badge ${cls}">${escapeHtml(r)}</span>`;
    })
    .join(" ");
}

function _usersFilterQuery() {
  return ($("users-filter")?.value || "").trim().toLowerCase();
}

function _filteredUsers() {
  const q = _usersFilterQuery();
  const users = skillsState.users || [];
  if (!q) return users;
  return users.filter((u) => {
    const hay = [
      u.username,
      u.display_name,
      u.uccx_team,
      u.uccx_roles,
      u.auth_source,
      u.role,
    ]
      .map((x) => String(x || "").toLowerCase())
      .join(" ");
    return hay.includes(q);
  });
}

function renderUsers() {
  const tbody = $("users-tbody");
  if (!tbody) return;
  const users = _filteredUsers();
  if (!users.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="meta">${
      (skillsState.users || []).length ? "Aucun résultat" : "Aucun utilisateur"
    }</td></tr>`;
    return;
  }
  tbody.innerHTML = users
    .map((u) => {
      const appsN = (u.application_ids || []).length;
      return `<tr data-user-id="${u.id}">
        <td><strong>${escapeHtml(u.username)}</strong></td>
        <td>${escapeHtml(u.display_name || "—")}</td>
        <td>${escapeHtml(u.uccx_team || "—")}</td>
        <td>${_userRolesBadges(u.uccx_roles)}</td>
        <td>${u.is_active ? "oui" : "non"}</td>
        <td>${appsN}</td>
        <td>${u.can_access_skills ? "oui" : "non"}</td>
        <td class="users-actions-cell">
          <button type="button" class="btn btn-default btn-sm" data-user-edit="${u.id}">Éditer</button>
        </td>
      </tr>`;
    })
    .join("");
}

function _generateLocalPassword(length = 16) {
  const alphabet =
    "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@$%*-_";
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  let out = "";
  for (let i = 0; i < length; i++) {
    out += alphabet[bytes[i] % alphabet.length];
  }
  return out;
}

function _resetUserPasswordField() {
  const pwd = $("user-rights-password");
  if (pwd) {
    pwd.value = "";
    pwd.type = "password";
  }
  const hint = $("user-rights-pwd-hint");
  if (hint) hint.classList.add("hidden");
}

function openUserRightsModal(userId) {
  const user = (skillsState.users || []).find((u) => Number(u.id) === Number(userId));
  const modal = $("modal-user-rights");
  if (!user || !modal) return;

  const setText = (id, val) => {
    const el = $(id);
    if (el) el.textContent = val;
  };
  const setHtml = (id, html) => {
    const el = $(id);
    if (el) el.innerHTML = html;
  };
  const setCheck = (id, on) => {
    const el = $(id);
    if (el) el.checked = !!on;
  };

  const idEl = $("user-rights-id");
  if (idEl) idEl.value = String(user.id);
  setText("user-rights-username", user.username || "—");
  setText("user-rights-display", user.display_name || "—");
  setText("user-rights-team", user.uccx_team || "—");
  setHtml("user-rights-roles", _userRolesBadges(user.uccx_roles));
  setText("user-rights-auth", user.auth_source || "—");
  setText("user-rights-portal-role", user.role || "—");

  const badge = $("user-rights-pwd-badge");
  if (badge) {
    badge.innerHTML = user.has_local_password
      ? '<span class="badge badge-pwd-yes">Défini</span>'
      : '<span class="badge badge-pwd-no">SSO uniquement</span>';
  }
  _resetUserPasswordField();

  setCheck("user-rights-active", user.is_active);
  setCheck("user-rights-apps", user.can_access_apps);
  setCheck("user-rights-skills", user.can_access_skills);

  const me = typeof state !== "undefined" ? state.me : null;
  const runasBtn = $("user-rights-runas-btn");
  if (runasBtn) {
    const self = me && Number(me.id) === Number(user.id);
    const otherAdmin = user.role === "admin";
    const canRunAs = !self && !otherAdmin && !!user.is_active;
    runasBtn.disabled = !canRunAs;
    runasBtn.title = self
      ? "Impossible de RunAs sur soi-même"
      : otherAdmin
        ? "RunAs vers un admin interdit"
        : !user.is_active
          ? "Compte désactivé"
          : "Voir l’UI comme cet utilisateur";
  }

  const appIds = new Set(user.application_ids || []);
  const appsGrid = $("user-rights-apps-grid");
  if (appsGrid) {
    appsGrid.innerHTML = (skillsState.apps || []).length
      ? skillsState.apps
          .map((a) => {
            const name = a.name || a.id || "?";
            return `<label class="users-check-item" title="${escapeHtml(String(name))}">
              <input type="checkbox" data-user-app-id="${a.id}" ${
                appIds.has(a.id) ? "checked" : ""
              }/>
              <span>${escapeHtml(String(name))}</span>
            </label>`;
          })
          .join("")
      : '<span class="meta">Aucune application</span>';
  }

  const teamSet = new Set(user.skill_teams || []);
  const teamsGrid = $("user-rights-teams-grid");
  if (teamsGrid) {
    teamsGrid.innerHTML = (skillsState.teams || []).length
      ? skillsState.teams
          .map((t) => {
            const name = t.name || "?";
            return `<label class="users-check-item" title="${escapeHtml(String(name))}">
              <input type="checkbox" data-user-team-name="${escapeHtml(String(name))}" ${
                teamSet.has(name) ? "checked" : ""
              }/>
              <span>${escapeHtml(String(name))}</span>
            </label>`;
          })
          .join("")
      : '<span class="meta">Aucune team</span>';
  }

  modal.dataset.userId = String(user.id);
  modal.hidden = false;
  modal.classList.remove("hidden");
  document.body.classList.add("portal-modal-open");
}

function closeUserRightsModal() {
  const modal = $("modal-user-rights");
  if (!modal) return;
  modal.hidden = true;
  modal.classList.add("hidden");
  delete modal.dataset.userId;
  _resetUserPasswordField();
  document.body.classList.remove("portal-modal-open");
}

async function saveUserRightsModal() {
  const userId = Number($("user-rights-id")?.value || 0);
  if (!userId) return;
  const application_ids = [...document.querySelectorAll("#user-rights-apps-grid [data-user-app-id]:checked")].map(
    (el) => Number(el.dataset.userAppId)
  );
  const skill_teams = [...document.querySelectorAll("#user-rights-teams-grid [data-user-team-name]:checked")].map(
    (el) => el.dataset.userTeamName
  );
  const password = ($("user-rights-password")?.value || "").trim();
  const payload = {
    is_active: !!$("user-rights-active")?.checked,
    can_access_apps: !!$("user-rights-apps")?.checked,
    can_access_skills: !!$("user-rights-skills")?.checked,
    application_ids,
    skill_teams,
  };
  if (password) payload.password = password;
  try {
    const updated = await api(`/api/admin/users/${userId}`, {
      method: "PATCH",
      json: payload,
    });
    const idx = (skillsState.users || []).findIndex((u) => u.id === userId);
    if (idx >= 0 && updated) {
      skillsState.users[idx] = { ...skillsState.users[idx], ...updated };
    } else {
      await loadAdminUsers();
      closeUserRightsModal();
      setMsg(
        "users-msg",
        password
          ? `User #${userId} enregistré (mot de passe local défini)`
          : `User #${userId} enregistré`,
        "ok"
      );
      return;
    }
    renderUsers();
    closeUserRightsModal();
    setMsg(
      "users-msg",
      password
        ? `User #${userId} enregistré (mot de passe local défini)`
        : `User #${userId} enregistré`,
      "ok"
    );
  } catch (err) {
    setMsg("users-msg", err.message, "error");
  }
}

async function startRunAsFromModal() {
  const userId = Number($("user-rights-id")?.value || 0);
  if (!userId) return;
  const user = (skillsState.users || []).find((u) => Number(u.id) === Number(userId));
  if (!user) return;
  if (typeof startRunAs !== "function") {
    setMsg("users-msg", "RunAs indisponible", "error");
    return;
  }
  try {
    closeUserRightsModal();
    await startRunAs(userId, user.username);
  } catch (err) {
    setMsg("users-msg", err.message, "error");
  }
}

/* ——— Admin settings ——— */

function _isBoolSettingValue(val) {
  const s = String(val ?? "")
    .trim()
    .toLowerCase();
  return s === "true" || s === "false";
}

async function loadAdminSettings() {
  setMsg("settings-msg", "Chargement…", "meta");
  try {
    const data = await api("/api/admin/settings");
    const form = $("settings-form");
    if (!form) return;
    form.classList.add("settings-form");
    const byGroup = {};
    for (const row of data.settings || []) {
      (byGroup[row.group] ||= []).push(row);
    }
    form.innerHTML = Object.entries(byGroup)
      .map(([group, rows]) => {
        const fields = rows
          .map((r) => {
            if (!r.secret && _isBoolSettingValue(r.value)) {
              const checked = String(r.value).toLowerCase() === "true" ? "checked" : "";
              return `<div class="settings-bool-row">
                <input type="checkbox" id="set-${escapeHtml(r.key)}" data-setting-key="${escapeHtml(r.key)}" data-bool="1" ${checked}/>
                <label class="control-label" for="set-${escapeHtml(r.key)}" style="margin:0">${escapeHtml(r.label)} <span class="meta">(${escapeHtml(r.source)})</span></label>
              </div>`;
            }
            const type = r.secret ? "password" : "text";
            const ph = r.secret ? "********" : "";
            const displayVal = r.secret && r.value === "********" ? "" : r.value;
            return `<div class="form-group">
              <label class="control-label" for="set-${escapeHtml(r.key)}">${escapeHtml(r.label)} <span class="meta">(${escapeHtml(r.source)})</span></label>
              <input class="form-control" id="set-${escapeHtml(r.key)}" data-setting-key="${escapeHtml(r.key)}" data-secret="${r.secret ? "1" : "0"}" type="${type}" value="${escapeHtml(displayVal)}" placeholder="${ph}" autocomplete="off"/>
            </div>`;
          })
          .join("");
        return `<h4 class="h5">${escapeHtml(group)}</h4>${fields}`;
      })
      .join("");
    setMsg("settings-msg", "", "meta");
  } catch (err) {
    setMsg("settings-msg", err.message, "error");
  }
}

async function saveAdminSettings() {
  const values = {};
  document.querySelectorAll("[data-setting-key]").forEach((input) => {
    const key = input.dataset.settingKey;
    if (input.dataset.bool === "1") {
      values[key] = input.checked ? "true" : "false";
      return;
    }
    const secret = input.dataset.secret === "1";
    const val = input.value;
    if (secret && (val === "" || val === "********")) return;
    values[key] = val;
  });
  try {
    await api("/api/admin/settings", { method: "PUT", json: { values } });
    setMsg("settings-msg", "Paramètres enregistrés", "ok");
    await loadAdminSettings();
  } catch (err) {
    setMsg("settings-msg", err.message, "error");
  }
}

function wireSkillsAdmin() {
  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => setNav(btn.dataset.nav));
  });
  document.querySelectorAll(".skills-tab").forEach((btn) => {
    btn.addEventListener("click", () => switchSkillsTab(btn.dataset.skillsTab));
  });
  $("skills-refresh-btn")?.addEventListener("click", () => loadSkillsPage());
  $("skills-save-btn")?.addEventListener("click", () => saveDirtyAgentSkills());
  $("skills-agent-filter")?.addEventListener("input", () => renderSkillsMatrix());
  $("skills-extras-apply")?.addEventListener("click", () => applyExtrasSelection());
  $("skills-extras-filter")?.addEventListener("input", () => renderExtrasPicker());
  $("skills-extras-toggle")?.addEventListener("click", () => {
    const panel = $("skills-extras-panel");
    if (!panel) return;
    panel.classList.toggle("hidden");
    const btn = $("skills-extras-toggle");
    if (btn) {
      btn.textContent = panel.classList.contains("hidden")
        ? "Skills supplémentaires…"
        : "Masquer skills supplémentaires";
    }
    if (!panel.classList.contains("hidden")) {
      $("skills-extras-filter")?.focus();
    }
  });
  $("skills-bulk-apply")?.addEventListener("click", () => applyTemplateToSelected());
  $("skills-bulk-schedule")?.addEventListener("click", () => goScheduleSelected());
  $("skills-from-agent-btn")?.addEventListener("click", () => createTemplateFromAgent());
  $("template-new-btn")?.addEventListener("click", () => createTemplate());
  $("sched-create-btn")?.addEventListener("click", () => createSchedule());
  document.querySelectorAll('input[name="sched-kind"]').forEach((r) => {
    r.addEventListener("change", () => toggleSchedKindFields());
  });
  document.querySelectorAll(".skills-dow-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      btn.classList.toggle("is-on");
      btn.setAttribute("aria-pressed", btn.classList.contains("is-on") ? "true" : "false");
    });
  });
  toggleSchedKindFields();
  $("users-sync-btn")?.addEventListener("click", async () => {
    setMsg("users-msg", "Sync UCCX…", "meta");
    try {
      const res = await api("/api/admin/users/sync-uccx", { method: "POST" });
      setMsg("users-msg", `Sync: +${res.created} / ~${res.updated}`, "ok");
      await loadAdminUsers();
    } catch (err) {
      setMsg("users-msg", err.message, "error");
    }
  });
  $("users-filter")?.addEventListener("input", () => renderUsers());
  $("user-rights-save-btn")?.addEventListener("click", () => saveUserRightsModal());
  $("user-rights-pwd-generate")?.addEventListener("click", () => {
    const pwd = $("user-rights-password");
    if (!pwd) return;
    pwd.type = "text";
    pwd.value = _generateLocalPassword(16);
    const hint = $("user-rights-pwd-hint");
    if (hint) hint.classList.remove("hidden");
    pwd.focus();
    pwd.select();
  });
  $("user-rights-runas-btn")?.addEventListener("click", () => startRunAsFromModal());
  document.querySelectorAll("[data-user-modal-close]").forEach((el) => {
    el.addEventListener("click", () => closeUserRightsModal());
  });
  document.addEventListener("click", (ev) => {
    const btn = ev.target?.closest?.("[data-user-edit]");
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    const uid = Number(btn.dataset.userEdit);
    // defer so this click cannot hit the newly shown modal backdrop
    setTimeout(() => openUserRightsModal(uid), 0);
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !$("modal-user-rights")?.hidden) {
      closeUserRightsModal();
    }
  });
  $("settings-save-btn")?.addEventListener("click", () => saveAdminSettings());
}

function bootSkillsAdmin() {
  wireSkillsAdmin();
  void initSamlLoginButton();
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bootSkillsAdmin);
} else {
  bootSkillsAdmin();
}
