const CAL_DAYS = [
  ["MON", "Lundi"],
  ["TUE", "Mardi"],
  ["WED", "Mercredi"],
  ["THU", "Jeudi"],
  ["FRI", "Vendredi"],
  ["SAT", "Samedi"],
  ["SUN", "Dimanche"],
];

/**
 * UCCX refuse 0000-MM-DD (XSD) et toute date passée (« Date must be a future date »).
 * Récurrence : noms `Y Label` / `En Label` (legacy [Y] / [E+N] encore lus).
 * Une seule date UCCX ; l’API avance seule à l’année suivante quand elle est passée.
 */
const YEARLY_SENTINEL = "2000"; // legacy lu depuis d’anciens essais / docs
const HOLIDAY_YEAR_SUFFIX_RE = /\s*·\s*\d{4}\s*$/;
const HOLIDAY_NAME_MAX = 50;
const FORBIDDEN_NAME_RE = /[`~!@#$%^&*()=[\]{};\\?"<>'+|:./,]/g;

/** Plages horaires par défaut (format 24 h). */
const DEFAULT_INTERVAL_START = "08:00";
const DEFAULT_INTERVAL_END = "18:00";

const EASTER_PRESETS = [
  { offset: 0, label: "Dimanche de Paques" },
  { offset: 1, label: "Lundi de Paques" },
  { offset: 39, label: "Ascension" },
  { offset: 50, label: "Lundi de Pentecote" },
];

/** Jours fériés officiels du Luxembourg (ASCII-safe pour UCCX). */
const LU_HOLIDAYS = [
  { kind: "yearly", mm: "01", dd: "01", label: "Nouvel An" },
  { kind: "easter", offset: 1, label: "Lundi de Paques" },
  { kind: "yearly", mm: "05", dd: "01", label: "Fete du Travail" },
  { kind: "easter", offset: 39, label: "Ascension" },
  { kind: "yearly", mm: "05", dd: "09", label: "Journee de l Europe" },
  { kind: "easter", offset: 50, label: "Lundi de Pentecote" },
  { kind: "yearly", mm: "06", dd: "23", label: "Fete nationale" },
  { kind: "yearly", mm: "08", dd: "15", label: "Assomption" },
  { kind: "yearly", mm: "11", dd: "01", label: "Toussaint" },
  { kind: "yearly", mm: "12", dd: "25", label: "Noel" },
  { kind: "yearly", mm: "12", dd: "26", label: "Saint-Etienne" },
];
const state = {
  token: localStorage.getItem("uccx_token") || "",
  me: null,
  apps: [],
  currentApp: null,
  uccx: null,
  activeCalendar: null,
  promptLanguages: [],
  promptLangDefault: "default",
  promptStatusCache: null,
  promptStatusInflight: null,
};

/** Sélections en attente : promptPending[param][lang] = { sourceLang, relpath, localFile } */
const promptPending = {};
const PATH_TONE_COUNT = 8;

function $(id) {
  return document.getElementById(id);
}

function must$(id) {
  const el = $(id);
  if (!el) throw new Error(`Élément #${id} introuvable — rechargez la page (Ctrl+F5)`);
  return el;
}

async function api(path, options = {}) {
  const headers = options.headers || {};
  if (state.token) headers["X-UCCX-Token"] = state.token;
  if (options.json) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.json);
  }
  const res = await fetch(path, { ...options, headers, credentials: "same-origin" });
  if (res.status === 401) {
    logout(false);
    throw new Error("Session expirée");
  }
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const detail = data?.detail || text || res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function showLogin() {
  document.body.classList.add("login-page");
  must$("login-view").classList.remove("hidden");
  must$("app-view").classList.add("hidden");
}

function showApp() {
  document.body.classList.remove("login-page");
  must$("login-view").classList.add("hidden");
  must$("app-view").classList.remove("hidden");
}

function logout(clear = true) {
  if (clear) {
    localStorage.removeItem("uccx_token");
    sessionStorage.removeItem("uccx_token_admin");
  }
  state.token = "";
  state.me = null;
  updateRunAsBanner(null);
  showLogin();
}

function updateRunAsBanner(me) {
  const banner = $("runas-banner");
  const text = $("runas-banner-text");
  if (!banner) return;
  const on = !!(me && me.impersonating);
  banner.hidden = !on;
  banner.classList.toggle("hidden", !on);
  document.body.classList.toggle("runas-active", on);
  if (text && on) {
    const who = me.username || "?";
    const by = me.impersonator ? ` (admin: ${me.impersonator})` : "";
    text.textContent = `Vue en tant que ${who}${by}`;
  }
}

async function startRunAs(userId, username) {
  const data = await api(`/api/admin/users/${userId}/impersonate`, { method: "POST" });
  if (!data?.access_token) throw new Error("Token RunAs manquant");
  if (state.token) {
    sessionStorage.setItem("uccx_token_admin", state.token);
  }
  state.token = data.access_token;
  localStorage.setItem("uccx_token", state.token);
  await afterLogin();
  if (username) {
    setMsg("users-msg", `RunAs: ${username}`, "ok");
  }
}

async function stopRunAs() {
  const adminTok = sessionStorage.getItem("uccx_token_admin");
  if (!adminTok) {
    throw new Error("Aucun token admin à restaurer");
  }
  sessionStorage.removeItem("uccx_token_admin");
  state.token = adminTok;
  localStorage.setItem("uccx_token", state.token);
  await afterLogin();
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function setMsg(id, text, cls) {
  const el = $(id);
  if (!el) return;
  el.textContent = text || "";
  el.className = cls || "meta";
}

/* ——— Dates / Pâques ——— */

function easterSunday(year) {
  // Algorithme anonyme grégorien
  const a = year % 19;
  const b = Math.floor(year / 100);
  const c = year % 100;
  const d = Math.floor(b / 4);
  const e = b % 4;
  const f = Math.floor((b + 8) / 25);
  const g = Math.floor((b - f + 1) / 3);
  const h = (19 * a + b - d - g + 15) % 30;
  const i = Math.floor(c / 4);
  const k = c % 4;
  const l = (32 + 2 * e + 2 * i - h - k) % 7;
  const m = Math.floor((a + 11 * h + 22 * l) / 451);
  const month = Math.floor((h + l - 7 * m + 114) / 31);
  const day = ((h + l - 7 * m + 114) % 31) + 1;
  return new Date(year, month - 1, day);
}

function addDays(date, n) {
  const d = new Date(date.getTime());
  d.setDate(d.getDate() + n);
  return d;
}

function toIsoDate(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function nextEasterRelative(offset) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  let y = today.getFullYear();
  let dt = addDays(easterSunday(y), offset);
  if (dt < today) {
    y += 1;
    dt = addDays(easterSunday(y), offset);
  }
  return toIsoDate(dt);
}

/** Prochaine occurrence civile d’un MM-DD (année courante ou suivante). */
function nextYearlyOccurrence(mm, dd) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  let y = today.getFullYear();
  let dt = new Date(y, Number(mm) - 1, Number(dd));
  if (dt < today) {
    y += 1;
    dt = new Date(y, Number(mm) - 1, Number(dd));
  }
  return toIsoDate(dt);
}

function ensureFutureIso(iso) {
  if (!iso || !/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso;
  const [y, m, d] = iso.split("-");
  if (y === YEARLY_SENTINEL || y === "0000") return nextYearlyOccurrence(m, d);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const dt = new Date(Number(y), Number(m) - 1, Number(d));
  if (dt < today) return nextYearlyOccurrence(m, d);
  return iso;
}

function stripHolidayYearSuffix(label) {
  return String(label || "").replace(HOLIDAY_YEAR_SUFFIX_RE, "").trim();
}

function sanitizeDayName(s, fallback = "Ferie") {
  let text = String(s || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "");
  text = text.replace(FORBIDDEN_NAME_RE, " ").replace(/\s+/g, " ").trim();
  if (!text) text = fallback;
  if (text.length > HOLIDAY_NAME_MAX) text = text.slice(0, HOLIDAY_NAME_MAX).trim();
  return text;
}

/** Parse Y/En (nouveau), [Y]/[E+N] (legacy), ignore « · YYYY ». */
function parseHolidayMeta(name) {
  const raw = name || "";
  const base = stripHolidayYearSuffix(raw);
  const isFollow = HOLIDAY_YEAR_SUFFIX_RE.test(raw);
  let m = base.match(/^\[E\+(\d+)\]\s*(.*)$/i);
  if (m) {
    return {
      kind: "easter",
      offset: Number(m[1]),
      label: sanitizeDayName(m[2]),
      isFollow,
    };
  }
  m = base.match(/^\[Y\]\s*(.*)$/i);
  if (m) {
    return {
      kind: "yearly",
      offset: null,
      label: sanitizeDayName(m[1]),
      isFollow,
    };
  }
  m = base.match(/^E(\d+)\s+(.*)$/i);
  if (m) {
    return {
      kind: "easter",
      offset: Number(m[1]),
      label: sanitizeDayName(m[2]),
      isFollow,
    };
  }
  m = base.match(/^Y\s+(.*)$/i);
  if (m) {
    return {
      kind: "yearly",
      offset: null,
      label: sanitizeDayName(m[1]),
      isFollow,
    };
  }
  return { kind: "fixed", offset: null, label: sanitizeDayName(base), isFollow: false };
}

/** Une ligne UI par férié logique ; masque les entrées legacy « · YYYY ». */
function collapseHolidaysForUi(holidays) {
  const fixed = [];
  const groups = new Map();
  for (const h of holidays || []) {
    const meta = inferKindFromHoliday(h);
    if (meta.kind === "fixed") {
      fixed.push({ name: meta.label || h.name || "", date: h.date || "" });
      continue;
    }
    const key =
      meta.kind === "easter"
        ? `easter:${meta.offset}`
        : `yearly:${(meta.label || "").trim().toLowerCase()}`;
    if (!groups.has(key)) groups.set(key, { meta, primaries: [], follows: [] });
    const g = groups.get(key);
    if (meta.isFollow) g.follows.push(h);
    else g.primaries.push(h);
  }
  const out = [...fixed];
  for (const g of groups.values()) {
    const sorted = (arr) =>
      [...arr].sort((a, b) => String(a.date || "").localeCompare(String(b.date || "")));
    const pick = sorted(g.primaries)[0] || sorted(g.follows)[0];
    if (!pick) continue;
    out.push({
      name: buildHolidayName(g.meta.kind, g.meta.offset, g.meta.label),
      date: pick.date || "",
    });
  }
  return out;
}

function buildHolidayName(kind, offset, label) {
  const clean = sanitizeDayName(label || "Ferie");
  if (kind === "easter") {
    const prefix = `E${offset} `;
    return `${prefix}${clean.slice(0, Math.max(1, HOLIDAY_NAME_MAX - prefix.length))}`.trim();
  }
  if (kind === "yearly") {
    const prefix = "Y ";
    return `${prefix}${clean.slice(0, Math.max(1, HOLIDAY_NAME_MAX - prefix.length))}`.trim();
  }
  return clean;
}
function isoToDisplay(iso, kind) {
  if (!iso || !/^\d{4}-\d{2}-\d{2}$/.test(iso)) return "";
  const [y, m, d] = iso.split("-");
  if (kind === "yearly" || y === YEARLY_SENTINEL || y === "0000") return `${d}/${m}`;
  return `${d}/${m}/${y}`;
}

function displayToIso(display, kind) {
  const s = (display || "").trim();
  let m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (m) {
    const dd = m[1].padStart(2, "0");
    const mm = m[2].padStart(2, "0");
    if (kind === "yearly") return nextYearlyOccurrence(mm, dd);
    return `${m[3]}-${mm}-${dd}`;
  }
  m = s.match(/^(\d{1,2})\/(\d{1,2})$/);
  if (m) {
    const dd = m[1].padStart(2, "0");
    const mm = m[2].padStart(2, "0");
    return nextYearlyOccurrence(mm, dd);
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) {
    if (kind === "yearly" || s.startsWith("0000-") || s.startsWith(`${YEARLY_SENTINEL}-`)) {
      return nextYearlyOccurrence(s.slice(5, 7), s.slice(8, 10));
    }
    return s;
  }
  return "";
}

function inferKindFromHoliday(h) {
  const meta = parseHolidayMeta(h.name || "");
  if (meta.kind === "easter" || meta.kind === "yearly") return meta;
  if (h.date && (h.date.startsWith(`${YEARLY_SENTINEL}-`) || h.date.startsWith("0000-"))) {
    return { kind: "yearly", offset: null, label: meta.label, isFollow: false };
  }
  return meta;
}

function attachDtsel(input) {
  if (!input || !window.dtsel?.DTS) return;
  try {
    new window.dtsel.DTS(input, {
      dateFormat: "dd/mm/yyyy",
      showTime: false,
      timeFormat: "HH:MM",
      lang: "FR",
      direction: "BOTTOM",
    });
  } catch (err) {
    console.warn("dtsel:", err);
  }
}

function formatTime24(raw, fallback) {
  const s = String(raw || "").trim();
  if (/^\d{1,2}:\d{2}$/.test(s)) {
    const [h, m] = s.split(":");
    return `${h.padStart(2, "0")}:${m}`;
  }
  if (/^\d{1,2}:\d{2}:\d{2}$/.test(s)) {
    const [h, m] = s.split(":");
    return `${h.padStart(2, "0")}:${m}`;
  }
  return fallback;
}

function attachTimePicker(input) {
  if (!input || input.dataset.dtselTime === "1") return;
  if (!window.dtsel?.DTS) {
    input.type = "time";
    input.step = "60";
    input.lang = "fr";
    return;
  }
  try {
    input.type = "text";
    input.setAttribute("inputmode", "numeric");
    input.setAttribute("autocomplete", "off");
    new window.dtsel.DTS(input, {
      showDate: false,
      showTime: true,
      timeFormat: "HH:MM",
      dateFormat: "dd/mm/yyyy",
      lang: "FR",
      direction: "BOTTOM",
    });
    input.dataset.dtselTime = "1";
  } catch (err) {
    console.warn("dtsel time:", err);
    input.type = "time";
    input.step = "60";
    input.lang = "fr";
  }
}

function attachTimePickersIn(root) {
  const scope = root || document;
  for (const el of scope.querySelectorAll(".time-picker-24h")) {
    attachTimePicker(el);
  }
}

async function loadApps() {
  const msg = $("apps-sync-msg");
  if (msg) msg.textContent = "Import UCCX…";
  try {
    state.apps = await api("/api/apps");
    if (msg) {
      msg.textContent = `${state.apps.length} application(s)`;
      msg.className = "meta ok";
    }
  } catch (err) {
    if (msg) {
      msg.textContent = `Liste locale — sync: ${err.message}`;
      msg.className = "meta warn";
    }
    throw err;
  }
  const list = $("app-list");
  list.innerHTML = "";
  for (const app of state.apps) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className =
      "btn btn-default btn-block app-link" + (state.currentApp === app.name ? " active" : "");
    btn.textContent = app.name;
    btn.title = app.description || app.name;
    btn.addEventListener("click", () => selectApp(app.name));
    list.appendChild(btn);
  }
  if (state.currentApp && !state.apps.some((a) => a.name === state.currentApp)) {
    state.currentApp = null;
    state.uccx = null;
    $("editor").classList.add("hidden");
    $("empty-state").classList.remove("hidden");
  }
}

function renderMeta() {
  const u = state.uccx;
  $("app-title").textContent = u.applicationName;
  $("app-script").textContent = u.script || "—";
  $("app-description").value = u.description || "";
  $("app-maxsession").value = u.maxsession ?? 0;
}

function displayParamValue(p) {
  const decoded = p.value == null ? "" : String(p.value);
  if (decoded !== "") return decoded;
  const raw = p.rawValue == null ? "" : String(p.rawValue).trim();
  if (!raw) return "";
  // Fallback si decode a vidé une valeur non vide (ex. guillemets seuls / sentinelle)
  if (raw.length >= 2 && raw[0] === '"' && raw[raw.length - 1] === '"') {
    return raw.slice(1, -1);
  }
  return raw;
}

function renderParams() {
  const body = $("params-body");
  body.innerHTML = "";
  const params = (state.uccx.scriptParams || []).filter((p) => p.kind === "generic");
  if (!params.length) {
    body.innerHTML = `<tr><td colspan="3" class="meta">Aucun paramètre générique</td></tr>`;
    return;
  }
  for (const p of params) {
    const tr = document.createElement("tr");
    tr.dataset.name = p.name;
    const isBool = p.type === "java.lang.Boolean";
    const isInt = p.type === "java.lang.Integer";
    const display = displayParamValue(p);
    let input;
    if (isBool) {
      const boolVal = String(display).toLowerCase() === "true" ? "true" : "false";
      input = `<select class="form-control" data-param="${escapeHtml(p.name)}">
        <option value="true" ${boolVal === "true" ? "selected" : ""}>true</option>
        <option value="false" ${boolVal === "false" ? "selected" : ""}>false</option>
      </select>`;
    } else {
      input = `<input class="form-control" type="${isInt ? "number" : "text"}" data-param="${escapeHtml(p.name)}" value="${escapeHtml(display)}" />`;
    }
    tr.innerHTML = `
      <td><code>${escapeHtml(p.name)}</code></td>
      <td class="meta">${escapeHtml(p.type.split(".").pop())}</td>
      <td>${input}</td>
    `;
    body.appendChild(tr);
  }
}

function collectParams() {
  const out = {};
  for (const el of document.querySelectorAll("#params-body [data-param]")) {
    out[el.dataset.param] = el.value;
  }
  return out;
}

function isEmptyPlayable(v) {
  const s = (v || "").trim();
  if (!s || s === "P[]" || s === "SP[]") return true;
  // P[P[]] / SP[P[]] / nests vides
  return playableToRelpath(s) === "";
}

function playableToRelpath(playable) {
  let raw = (playable || "").trim();
  if (!raw || raw === "P[]" || raw === "SP[]") return "";
  // Déplie P[P[…]] / SP[…] imbriqués
  for (let i = 0; i < 8; i++) {
    const m = raw.match(/^SP\[(.*)\]$/i) || raw.match(/^P\[(.*)\]$/i);
    if (!m) break;
    raw = (m[1] || "").trim();
    if (!raw || raw === "P[]" || raw === "SP[]") return "";
    if (!/^(SP|P)\[/i.test(raw)) {
      return raw.replace(/\\/g, "/").replace(/^\/+/, "");
    }
  }
  // Chemin nu (AppAdmin sans P[])
  if (raw && !/^(SP|P)\[/i.test(raw)) {
    return raw.replace(/\\/g, "/").replace(/^\/+/, "");
  }
  return "";
}

function normalizePlayableDisplay(v) {
  // AppAdmin UCCX stocke le chemin nu (5002.wav ou test/file.wav), pas P[…]
  // Nested empty P[P[]] → sentinelle unique P[]
  const s = (v || "").trim();
  if (!s) return "P[]";
  const rel = playableToRelpath(s);
  return rel || "P[]";
}

function normalizeUccxPlayablesInState() {
  const params = state.uccx?.scriptParams;
  if (!Array.isArray(params)) return;
  for (const p of params) {
    if (p.kind === "prompt" || String(p.type || "").includes("Playable")) {
      p.value = normalizePlayableDisplay(p.value || "P[]");
    }
  }
}

async function ensurePromptLanguages({ force = false } = {}) {
  if (!state.currentApp) return;
  if (!force && state.promptLanguages.length) return;
  try {
    const data = await api(
      `/api/apps/${encodeURIComponent(state.currentApp)}/prompt-languages`
    );
    state.promptLanguages = data.languages || [];
    state.promptLangDefault = data.default || "default";
  } catch (err) {
    console.warn("prompt-languages", err);
    if (!state.promptLanguages.length) {
      state.promptLanguages = ["default", "fr_LU", "en_US", "fr_FR", "en_GB", "de_DE"];
      state.promptLangDefault = "fr_LU";
    }
  }
}

async function fetchPromptStatus(force) {
  if (!state.currentApp) return { languages: [], params: {} };
  if (!force && state.promptStatusCache && Object.keys(state.promptStatusCache.params || {}).length) {
    return state.promptStatusCache;
  }
  if (!force && state.promptStatusInflight) return state.promptStatusInflight;
  const p = (async () => {
    try {
      const data = await api(
        `/api/apps/${encodeURIComponent(state.currentApp)}/prompt-status`
      );
      state.promptStatusCache = data;
      if (data.languages?.length) state.promptLanguages = data.languages;
      return data;
    } catch (err) {
      state.promptStatusCache = null;
      throw err;
    } finally {
      state.promptStatusInflight = null;
    }
  })();
  state.promptStatusInflight = p;
  return p;
}

let promptFilesAllCache = null;

async function fetchAllPromptFiles(force) {
  if (!state.currentApp) return [];
  if (!force && Array.isArray(promptFilesAllCache)) return promptFilesAllCache;
  const data = await api(
    `/api/apps/${encodeURIComponent(state.currentApp)}/prompt-files`
  );
  promptFilesAllCache = data.files || [];
  return promptFilesAllCache;
}

function shortPromptFilename(relpath) {
  if (!relpath) return "";
  const parts = relpath.split("/");
  return parts[parts.length - 1] || relpath;
}

function hashPathSegment(name) {
  const s = String(name || "").toLowerCase();
  let h = 0;
  for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/** Même libellé → même ton ; tous les .wav → classe fixe. */
function pathToneClass(segment) {
  if (/\.wav$/i.test(segment)) return "prompt-path-seg-wav";
  return `prompt-path-tone-${hashPathSegment(segment) % PATH_TONE_COUNT}`;
}

function coloredPathHtml(pathStr) {
  const parts = String(pathStr || "")
    .replace(/\\/g, "/")
    .split("/")
    .filter(Boolean);
  if (!parts.length) return "";
  const chips = parts
    .map((part, i) => {
      const sep = i ? `<span class="prompt-path-sep" aria-hidden="true">/</span>` : "";
      return `${sep}<span class="prompt-path-seg ${pathToneClass(part)}">${escapeHtml(part)}</span>`;
    })
    .join("");
  return `<span class="prompt-path">${chips}</span>`;
}

function getPending(paramName, lang) {
  return promptPending[paramName]?.[lang] || null;
}

function setPending(paramName, lang, data) {
  if (!promptPending[paramName]) promptPending[paramName] = {};
  const prev = promptPending[paramName][lang];
  if (prev?.blobUrl) URL.revokeObjectURL(prev.blobUrl);
  const next = { ...data };
  if (next.localFile && !next.blobUrl) {
    next.blobUrl = URL.createObjectURL(next.localFile);
  }
  if (!next.localFile) next.blobUrl = null;
  promptPending[paramName][lang] = next;
}

function clearPending(paramName, lang) {
  const prev = promptPending[paramName]?.[lang];
  if (prev?.blobUrl) URL.revokeObjectURL(prev.blobUrl);
  if (promptPending[paramName]) delete promptPending[paramName][lang];
}

function clearAllPromptPending() {
  for (const byLang of Object.values(promptPending)) {
    for (const pend of Object.values(byLang || {})) {
      if (pend?.blobUrl) URL.revokeObjectURL(pend.blobUrl);
    }
  }
  for (const k of Object.keys(promptPending)) delete promptPending[k];
}

/** ▶ si pending, ou fichier langue, ou fallback default. */
function cellCanPlay(lang, present, pending, byLang, relpath) {
  if (pending?.localFile || pending?.relpath) return true;
  if (!relpath) return false;
  if (present) return true;
  if (lang !== "default" && byLang?.default) return true;
  return false;
}

function browseLabelHtml(pending, lang, savedRelpath) {
  if (pending?.localFile) {
    const name = pending.localFile.name || "fichier";
    return `<span class="prompt-path"><span class="prompt-path-seg ${pathToneClass(name)}">${escapeHtml(name)}</span></span>`;
  }
  if (pending?.relpath) {
    const full = `${pending.sourceLang || lang}/${pending.relpath}`.replace(/\/+/g, "/");
    return coloredPathHtml(full);
  }
  if (savedRelpath) return coloredPathHtml(savedRelpath);
  return "UCCX…";
}

function closeAllPromptBrowsePanels(except) {
  for (const wrap of document.querySelectorAll(".prompt-browse-wrap.is-open")) {
    if (except && wrap === except) continue;
    wrap.classList.remove("is-open");
    const panel = wrap.querySelector(".prompt-browse-panel");
    if (panel) panel.classList.add("hidden");
  }
  for (const wrap of document.querySelectorAll(".prompt-tts-wrap.is-open")) {
    if (except && wrap === except) continue;
    wrap.classList.remove("is-open");
    const panel = wrap.querySelector(".prompt-tts-panel");
    if (panel) {
      persistTtsPanelGeom(panel);
      panel.classList.add("hidden");
    }
  }
}

/** Cache : ttsVoicesCache[`${engine}:${lang}`] ; ElevenLabs → `elevenlabs:all` */
const ttsVoicesCache = {};
/** Cache transcripts : `${param}|${lang}` → { text, source, stt_enabled } */
const transcriptCache = {};
const TTS_PANEL_GEOM_KEY = "prompt-tts-panel-geom";
const TTS_PANEL_MIN_W = 320;
const TTS_PANEL_MIN_H = 280;
const TTS_PANEL_DEFAULT_W = 460;
const TTS_PANEL_DEFAULT_H = 480;

function ttsCacheKey(engine, lang) {
  if ((engine || "").toLowerCase() === "elevenlabs") return "elevenlabs:all";
  return `${engine || "piper"}:${lang || "default"}`;
}

function clampTtsPanelGeom(geom) {
  const maxW = Math.max(TTS_PANEL_MIN_W, window.innerWidth - 16);
  const maxH = Math.max(TTS_PANEL_MIN_H, window.innerHeight - 16);
  const width = Math.min(maxW, Math.max(TTS_PANEL_MIN_W, Math.round(geom.width)));
  const height = Math.min(maxH, Math.max(TTS_PANEL_MIN_H, Math.round(geom.height)));
  const left = Math.min(
    Math.max(8, Math.round(geom.left)),
    Math.max(8, window.innerWidth - width - 8)
  );
  const top = Math.min(
    Math.max(8, Math.round(geom.top)),
    Math.max(8, window.innerHeight - height - 8)
  );
  return { left, top, width, height };
}

function defaultTtsPanelGeom() {
  const width = Math.min(TTS_PANEL_DEFAULT_W, window.innerWidth - 24);
  const height = Math.min(TTS_PANEL_DEFAULT_H, window.innerHeight - 24);
  return clampTtsPanelGeom({
    left: (window.innerWidth - width) / 2,
    top: (window.innerHeight - height) / 2,
    width,
    height,
  });
}

function readTtsPanelGeom() {
  try {
    const raw = sessionStorage.getItem(TTS_PANEL_GEOM_KEY);
    if (!raw) return null;
    const g = JSON.parse(raw);
    if (
      ![g.left, g.top, g.width, g.height].every((n) => Number.isFinite(n))
    ) {
      return null;
    }
    return clampTtsPanelGeom(g);
  } catch {
    return null;
  }
}

function applyTtsPanelGeom(panel, geom) {
  if (!panel || !geom) return;
  const g = clampTtsPanelGeom(geom);
  panel.style.left = `${g.left}px`;
  panel.style.top = `${g.top}px`;
  panel.style.width = `${g.width}px`;
  panel.style.height = `${g.height}px`;
  panel.style.right = "auto";
  panel.style.bottom = "auto";
}

function persistTtsPanelGeom(panel) {
  if (!panel) return;
  try {
    const r = panel.getBoundingClientRect();
    sessionStorage.setItem(
      TTS_PANEL_GEOM_KEY,
      JSON.stringify({
        left: r.left,
        top: r.top,
        width: r.width,
        height: r.height,
      })
    );
  } catch {
    /* ignore */
  }
}

function placeTtsPanel(panel) {
  if (!panel) return;
  applyTtsPanelGeom(panel, readTtsPanelGeom() || defaultTtsPanelGeom());
}

function wireTtsPanelChrome(panel) {
  if (!panel || panel.dataset.chromeWired === "1") return;
  panel.dataset.chromeWired = "1";

  const onPointerMove = (state, ev) => {
    if (!state.active) return;
    ev.preventDefault();
    const dx = ev.clientX - state.startX;
    const dy = ev.clientY - state.startY;
    if (state.mode === "drag") {
      applyTtsPanelGeom(panel, {
        left: state.origin.left + dx,
        top: state.origin.top + dy,
        width: state.origin.width,
        height: state.origin.height,
      });
      return;
    }
    if (state.mode === "se") {
      applyTtsPanelGeom(panel, {
        left: state.origin.left,
        top: state.origin.top,
        width: state.origin.width + dx,
        height: state.origin.height + dy,
      });
      return;
    }
    if (state.mode === "sw") {
      applyTtsPanelGeom(panel, {
        left: state.origin.left + dx,
        top: state.origin.top,
        width: state.origin.width - dx,
        height: state.origin.height + dy,
      });
    }
  };

  const endPointer = (state) => {
    if (!state.active) return;
    state.active = false;
    document.body.classList.remove("is-tts-panel-dragging");
    persistTtsPanelGeom(panel);
  };

  const startPointer = (mode, ev) => {
    if (ev.button != null && ev.button !== 0) return;
    ev.preventDefault();
    ev.stopPropagation();
    const r = panel.getBoundingClientRect();
    const state = {
      active: true,
      mode,
      startX: ev.clientX,
      startY: ev.clientY,
      origin: {
        left: r.left,
        top: r.top,
        width: r.width,
        height: r.height,
      },
    };
    document.body.classList.add("is-tts-panel-dragging");
    const move = (e) => onPointerMove(state, e);
    const up = () => {
      endPointer(state);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  };

  panel.querySelector("[data-tts-drag]")?.addEventListener("pointerdown", (ev) => {
    if (ev.target.closest("button, input, label, a, select, textarea")) return;
    startPointer("drag", ev);
  });
  panel.querySelector('[data-tts-resize="se"]')?.addEventListener("pointerdown", (ev) =>
    startPointer("se", ev)
  );
  panel.querySelector('[data-tts-resize="sw"]')?.addEventListener("pointerdown", (ev) =>
    startPointer("sw", ev)
  );
}

async function prefetchElevenlabsVoices() {
  if (!state.currentApp) return;
  try {
    await fetchTtsVoices(null, "elevenlabs", { force: true });
  } catch {
    /* notice / warn handled when opening TTS */
  }
}

function transcriptCacheKey(paramName, lang) {
  return `${paramName}|${lang}`;
}

function clearTtsCaches() {
  for (const k of Object.keys(ttsVoicesCache)) delete ttsVoicesCache[k];
}

function clearTranscriptCache() {
  for (const k of Object.keys(transcriptCache)) delete transcriptCache[k];
}

function seedTranscriptCache(transcripts) {
  clearTranscriptCache();
  for (const [paramName, byLang] of Object.entries(transcripts || {})) {
    for (const [lang, payload] of Object.entries(byLang || {})) {
      transcriptCache[transcriptCacheKey(paramName, lang)] = {
        text: payload?.text || "",
        source: payload?.source === "tts" ? "tts" : payload?.source === "stt" ? "stt" : null,
        stt_enabled: payload?.stt_enabled !== false,
      };
    }
  }
}

function getCachedSttEnabled(paramName, lang) {
  const c = transcriptCache[transcriptCacheKey(paramName, lang)];
  return c?.stt_enabled !== false;
}

async function fetchTtsVoices(lang, engine = "piper", { force = false } = {}) {
  const eng = (engine || "piper").toLowerCase();
  const key = ttsCacheKey(eng, lang);
  if (!force && ttsVoicesCache[key]) return ttsVoicesCache[key];
  if (!state.currentApp) return { voices: [], default: null, engines: [] };
  const qs = new URLSearchParams();
  // ElevenLabs : liste globale (multilingue), pas de filtre langue.
  if (eng !== "elevenlabs" && lang) qs.set("lang", lang);
  if (eng) qs.set("engine", eng);
  const data = await api(
    `/api/apps/${encodeURIComponent(state.currentApp)}/tts/voices?${qs}`
  );
  const payload = {
    voices: data.voices || [],
    default: data.default || null,
    engines: data.engines || [],
    engine: data.engine || eng,
    notice: data.notice || null,
  };
  // Ne pas figer un cache ElevenLabs vide (show_legacy / clé / perms peuvent changer).
  if (!(eng === "elevenlabs" && !(payload.voices || []).length)) {
    ttsVoicesCache[key] = payload;
  }
  return payload;
}

function showElevenlabsVoicesNotice(notice) {
  const el = $("elevenlabs-usage");
  if (!el) return;
  if (!notice) return;
  const usage = (el.dataset.usageText || el.textContent || "").trim();
  const base = usage && !usage.includes("Voice Library") && !usage.includes("multilingue")
    ? usage
    : el.dataset.usageText || "";
  if (base) {
    el.dataset.usageText = base;
    el.textContent = `${base} — ${notice}`;
  } else {
    el.textContent = notice;
  }
  el.classList.remove("hidden");
}

function formatElevenlabsUsage(data) {
  if (!data || data.available === false) return "";
  const pct = data.percent != null ? data.percent : 0;
  const used = data.used ?? 0;
  const limit = data.limit ?? 0;
  let reset = "";
  if (data.reset_at) {
    try {
      reset = new Date(data.reset_at).toLocaleDateString("fr-FR", {
        day: "numeric",
        month: "short",
      });
    } catch {
      reset = "";
    }
  }
  const tier = (data.status || data.tier || "free").toString();
  let msg = `ElevenLabs ${tier} : ${pct} % (${used} / ${limit})`;
  if (reset) msg += ` — reset ${reset}`;
  return msg;
}

async function refreshElevenlabsUsage() {
  const el = $("elevenlabs-usage");
  if (!el) return;
  const eng = $("public-tts-engine")?.value || "";
  if (eng !== "elevenlabs") {
    el.classList.add("hidden");
    el.textContent = "";
    return;
  }
  try {
    const data = await api("/api/tts/elevenlabs/usage");
    const msg = formatElevenlabsUsage(data);
    if (!msg) {
      el.classList.add("hidden");
      el.textContent = "";
      return;
    }
    el.dataset.usageText = msg;
    el.textContent = msg;
    el.classList.remove("hidden");
  } catch (err) {
    el.dataset.usageText = "";
    el.textContent = `ElevenLabs quota : ${err.message || "indisponible"}`;
    el.classList.remove("hidden");
  }
}

async function loadPublicTtsSettings() {
  const sel = $("public-tts-engine");
  if (!sel) return;
  try {
    const data = await api("/api/tts/public-settings");
    if (data.engines?.length) {
      sel.innerHTML = data.engines
        .map(
          (e) =>
            `<option value="${escapeHtml(e.id)}">${escapeHtml(e.label || e.id)}</option>`
        )
        .join("");
    }
    sel.value = data.engine || "piper";
    setMsg("public-tts-engine-msg", "", "meta");
    await refreshElevenlabsUsage();
  } catch (err) {
    setMsg("public-tts-engine-msg", err.message, "error");
  }
}

async function savePublicTtsSettings(engine) {
  const data = await api("/api/tts/public-settings", {
    method: "PUT",
    json: { engine },
  });
  return data;
}

async function fetchPromptTranscript(paramName, lang, relpath, { force = false } = {}) {
  const key = transcriptCacheKey(paramName, lang);
  if (!force && transcriptCache[key]?.text) return transcriptCache[key];
  if (!state.currentApp) throw new Error("Aucune app sélectionnée");
  const qs = new URLSearchParams({
    param_name: paramName,
    lang,
  });
  if (relpath) qs.set("relpath", relpath);
  if (force) qs.set("force", "true");
  const data = await api(
    `/api/apps/${encodeURIComponent(state.currentApp)}/prompts/transcript?${qs}`
  );
  const payload = {
    text: data.text || "",
    source: data.source === "tts" ? "tts" : data.source === "stt" ? "stt" : null,
    stt_enabled: data.stt_enabled !== false,
  };
  transcriptCache[key] = payload;
  return payload;
}

async function patchPromptSttSettings(paramName, lang, enabled, relpath = "") {
  const data = await api(
    `/api/apps/${encodeURIComponent(state.currentApp)}/prompts/stt-settings`,
    {
      method: "PATCH",
      json: {
        param_name: paramName,
        lang,
        stt_enabled: !!enabled,
        relpath: relpath || "",
      },
    }
  );
  const key = transcriptCacheKey(paramName, lang);
  const prev = transcriptCache[key] || {};
  transcriptCache[key] = {
    text: data.text ?? prev.text ?? "",
    source: data.source === "tts" ? "tts" : data.source === "stt" ? "stt" : prev.source || null,
    stt_enabled: data.stt_enabled !== false,
  };
  return transcriptCache[key];
}

let _sttSelectSeq = 0;

function findPromptLangCell(paramName, lang) {
  const row = document.querySelector(`.prompt-row[data-param="${CSS.escape(paramName)}"]`);
  return row?.querySelector(`.prompt-lang-cell[data-lang="${CSS.escape(lang)}"]`) || null;
}

function ttsLangTone(lang) {
  const s = String(lang || "x");
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h % 8;
}

function ttsChipHtml(lang, body) {
  const code = String(lang || "en").replace(/-/g, "_");
  const tone = ttsLangTone(code);
  return (
    `<span class="prompt-tts-chip" data-lang="${escapeHtml(code)}" data-tone="${tone}" contenteditable="false">` +
    `<span class="prompt-tts-chip-lang">${escapeHtml(code)}</span>` +
    `<span class="prompt-tts-chip-text" contenteditable="true">${escapeHtml(body || "")}</span>` +
    `<button type="button" class="prompt-tts-chip-remove" data-tts-chip-remove tabindex="-1" title="Retirer la balise">×</button>` +
    `</span>`
  );
}

function markupToRichHtml(raw) {
  const text = String(raw || "");
  if (!text) return "";
  const re = /\[\[([A-Za-z]{2,3}(?:_[A-Za-z0-9]+)?):([\s\S]*?)\]\]/g;
  let html = "";
  let last = 0;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) {
      html += escapeHtml(text.slice(last, m.index)).replace(/\n/g, "<br>");
    }
    html += ttsChipHtml(m[1].replace(/-/g, "_"), m[2]);
    last = m.index + m[0].length;
  }
  if (last < text.length) {
    html += escapeHtml(text.slice(last)).replace(/\n/g, "<br>");
  }
  return html;
}

function richEditorToMarkup(editor) {
  if (!editor) return "";
  let out = "";
  const walk = (node) => {
    if (!node) return;
    if (node.nodeType === Node.TEXT_NODE) {
      out += node.textContent || "";
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    if (node.classList?.contains("prompt-tts-chip")) {
      const code = (node.getAttribute("data-lang") || "en").replace(/-/g, "_");
      const body = (
        node.querySelector(".prompt-tts-chip-text")?.textContent || ""
      ).replace(/\u00a0/g, " ");
      out += `[[${code}:${body}]]`;
      return;
    }
    if (node.tagName === "BR") {
      out += "\n";
      return;
    }
    if (node.tagName === "DIV" || node.tagName === "P") {
      if (out.length && !out.endsWith("\n")) out += "\n";
    }
    for (const child of node.childNodes) walk(child);
  };
  for (const child of editor.childNodes) walk(child);
  return out.replace(/\u00a0/g, " ");
}

function setTtsRichEditorText(cellOrPanel, text) {
  const root = cellOrPanel;
  const editor = root?.querySelector?.("[data-tts-editor]");
  const ta = root?.querySelector?.("[data-tts-text]");
  const value = text || "";
  if (ta) ta.value = value;
  if (editor) {
    editor.innerHTML = markupToRichHtml(value) || "";
    editor.classList.toggle("is-empty", !value.trim());
  }
}

function syncSttTtsPanelText(cell, text, source) {
  const badge = cell?.querySelector("[data-prompt-text-source]");
  if (typeof text === "string") {
    setTtsRichEditorText(cell, text);
  }
  if (badge) {
    if (source === "tts") {
      badge.textContent = "Texte TTS";
      badge.dataset.kind = "tts";
    } else if (source === "stt") {
      badge.textContent = "Transcription STT";
      badge.dataset.kind = "stt";
    } else {
      badge.textContent = text ? "Texte" : "";
      badge.dataset.kind = text ? "edit" : "empty";
    }
  }
}

/**
 * Parse « … [[en:one]] … » → [{ lang: null|code, text }, …].
 * lang null = langue de base du pop-up.
 */
function parseLangSpans(text) {
  const raw = String(text || "");
  if (!raw) return [];
  const re = /\[\[([A-Za-z]{2,3}(?:_[A-Za-z0-9]+)?):([\s\S]*?)\]\]/g;
  const out = [];
  let last = 0;
  let m;
  while ((m = re.exec(raw)) !== null) {
    if (m.index > last) {
      out.push({ lang: null, text: raw.slice(last, m.index) });
    }
    const spanText = (m[2] || "").trim();
    if (spanText) {
      out.push({ lang: m[1].replace(/-/g, "_"), text: spanText });
    }
    last = m.index + m[0].length;
  }
  if (last < raw.length) {
    out.push({ lang: null, text: raw.slice(last) });
  }
  return out.filter((p) => (p.text || "").length > 0);
}

async function expandTextToConcatSegments({
  text,
  baseLang,
  engine,
  voice,
  voiceValue,
}) {
  const parts = parseLangSpans(text);
  if (!parts.length) return [];
  const eng = (engine || "piper").toLowerCase();
  // Edge *MultilingualNeural* / ElevenLabs : une seule voix pour tous les spans.
  const keepSameVoice =
    eng === "elevenlabs" || /multilingual/i.test(String(voice || ""));
  const voiceCache = {};
  const resolveVoice = async (targetLang) => {
    const key = `${eng}:${targetLang || "default"}`;
    if (voiceCache[key]) return voiceCache[key];
    if (
      voice &&
      (keepSameVoice || !targetLang || targetLang === baseLang)
    ) {
      voiceCache[key] = { engine: eng, voice, voiceValue };
      return voiceCache[key];
    }
    const data = await fetchTtsVoices(targetLang || baseLang, eng);
    const list = data.voices || [];
    if (!list.length) {
      throw new Error(
        `Aucune voix ${eng.toUpperCase()} pour « ${targetLang || baseLang} »`
      );
    }
    const pick =
      list.find((v) => v.default || v.id === data.default) || list[0];
    const vv = voiceEngineOptionValue(eng, pick.id);
    voiceCache[key] = {
      engine: eng,
      voice: pick.id,
      voiceValue: vv,
    };
    return voiceCache[key];
  };

  const out = [];
  for (const p of parts) {
    const partLang = p.lang || baseLang;
    const resolved = await resolveVoice(partLang);
    out.push({
      text: p.text,
      lang: partLang,
      engine: resolved.engine,
      voice: resolved.voice,
      voiceValue: resolved.voiceValue,
    });
  }
  return out;
}

/** Parse transcript « [fr_LU] …\\n[en_US] … » → [{lang, text}]. */
function parseMultiLangTranscript(text) {
  const raw = String(text || "").trim();
  if (!raw) return [];
  const re = /^\[([^\]]+)\]\s*/;
  const lines = raw.split(/\n+/);
  const out = [];
  let cur = null;
  for (const line of lines) {
    const m = line.match(re);
    if (m) {
      if (cur) out.push(cur);
      cur = { lang: m[1].trim(), text: line.slice(m[0].length) };
    } else if (cur) {
      cur.text = cur.text ? `${cur.text}\n${line}` : line;
    } else {
      out.push({ lang: "", text: line });
      cur = null;
    }
  }
  if (cur) out.push(cur);
  return out.filter((s) => (s.text || "").trim());
}

function formatMultiLangTranscript(segments) {
  return (segments || [])
    .map((s) => {
      const t = (s.text || "").trim();
      if (!t) return "";
      return s.lang ? `[${s.lang}] ${t}` : t;
    })
    .filter(Boolean)
    .join("\n");
}

function parseVoiceEngineValue(value) {
  const raw = String(value || "");
  const idx = raw.indexOf(":");
  if (idx <= 0) return { engine: "piper", voice: raw || null };
  return {
    engine: raw.slice(0, idx) || "piper",
    voice: raw.slice(idx + 1) || null,
  };
}

function voiceEngineOptionValue(engine, voiceId) {
  return `${engine || "piper"}:${voiceId || ""}`;
}

function voiceEngineOptionLabel(engine, voiceLabel, voiceId) {
  // Optgroups Piper/Kokoro suffisent — pas de suffixe moteur
  return (voiceLabel || voiceId || "—").trim();
}

const promptTtsLastSelection = {};

function ttsSelectionKey(paramName, cellLang) {
  return `${state.currentApp || ""}|${paramName}|${cellLang}`;
}

function normalizeTtsSelection(parsed, cellLang) {
  if (!parsed || typeof parsed !== "object") return null;
  // Nouveau format plat
  if (typeof parsed.text === "string" || parsed.voiceValue) {
    return {
      text: parsed.text || "",
      voiceValue: parsed.voiceValue || "",
      engine: parsed.engine || null,
      voice: parsed.voice || null,
      lang: parsed.lang || cellLang,
    };
  }
  // Ancien format multi-segments → fusionner les textes
  if (parsed.segments?.length) {
    const texts = parsed.segments.map((s) => s.text || "").filter(Boolean);
    const first = parsed.segments[0] || {};
    return {
      text: texts.join(" "),
      voiceValue: first.voiceValue || "",
      engine: first.engine || null,
      voice: first.voice || null,
      lang: cellLang,
    };
  }
  return null;
}

function loadTtsLastSelection(paramName, cellLang) {
  const key = ttsSelectionKey(paramName, cellLang);
  if (promptTtsLastSelection[key]) {
    return normalizeTtsSelection(promptTtsLastSelection[key], cellLang);
  }
  try {
    const raw = sessionStorage.getItem(`tts-sel:${key}`);
    if (!raw) return null;
    const parsed = normalizeTtsSelection(JSON.parse(raw), cellLang);
    if (parsed) {
      promptTtsLastSelection[key] = parsed;
      return parsed;
    }
  } catch {
    /* ignore */
  }
  return null;
}

function saveTtsLastSelection(paramName, cellLang, payload) {
  const key = ttsSelectionKey(paramName, cellLang);
  const data = {
    text: payload?.text || "",
    voiceValue:
      payload?.voiceValue ||
      voiceEngineOptionValue(payload?.engine, payload?.voice),
    engine: payload?.engine || null,
    voice: payload?.voice || null,
    lang: cellLang,
  };
  if (!data.text && !data.voiceValue) return;
  promptTtsLastSelection[key] = data;
  try {
    sessionStorage.setItem(`tts-sel:${key}`, JSON.stringify(data));
  } catch {
    /* ignore quota */
  }
}

async function fetchUnifiedVoiceOptions(lang) {
  let elevenErr = null;
  let edgeErr = null;
  const [piper, kokoro, edge, eleven] = await Promise.all([
    fetchTtsVoices(lang, "piper").catch(() => ({ voices: [], default: null })),
    fetchTtsVoices(lang, "kokoro").catch(() => ({ voices: [], default: null })),
    fetchTtsVoices(lang, "edge").catch((err) => {
      edgeErr = err;
      return { voices: [], default: null };
    }),
    // Cache fixe elevenlabs:all — pas de refetch au change de langue segment.
    fetchTtsVoices(null, "elevenlabs").catch((err) => {
      elevenErr = err;
      return { voices: [], default: null };
    }),
  ]);
  const options = [];
  let defaultValue = null;
  for (const v of piper.voices || []) {
    const value = voiceEngineOptionValue("piper", v.id);
    options.push({
      engine: "piper",
      value,
      label: voiceEngineOptionLabel("piper", v.label, v.id),
      group: "Piper",
    });
    if (!defaultValue && (v.default || v.id === piper.default)) {
      defaultValue = value;
    }
  }
  for (const v of kokoro.voices || []) {
    const value = voiceEngineOptionValue("kokoro", v.id);
    options.push({
      engine: "kokoro",
      value,
      label: voiceEngineOptionLabel("kokoro", v.label, v.id),
      group: "Kokoro",
    });
    if (!defaultValue && (v.default || v.id === kokoro.default)) {
      defaultValue = value;
    }
  }
  for (const v of edge.voices || []) {
    const value = voiceEngineOptionValue("edge", v.id);
    options.push({
      engine: "edge",
      value,
      label: voiceEngineOptionLabel("edge", v.label, v.id),
      group: "Edge",
    });
    if (!defaultValue && (v.default || v.id === edge.default)) {
      defaultValue = value;
    }
  }
  for (const v of eleven.voices || []) {
    const value = voiceEngineOptionValue("elevenlabs", v.id);
    const langLabel = v.lang_label || "Multilingue";
    options.push({
      engine: "elevenlabs",
      value,
      label: voiceEngineOptionLabel("elevenlabs", v.label, v.id),
      group: `ElevenLabs — ${langLabel}`,
    });
    if (!defaultValue && (v.default || v.id === eleven.default)) {
      defaultValue = value;
    }
  }
  if (edgeErr) {
    setMsg(
      "prompts-msg",
      `Edge TTS: impossible de charger les voix (${edgeErr.message})`,
      "warn"
    );
  }
  if (elevenErr) {
    setMsg(
      "prompts-msg",
      `ElevenLabs: impossible de charger les voix (${elevenErr.message})`,
      "warn"
    );
  } else if (eleven.notice) {
    showElevenlabsVoicesNotice(eleven.notice);
  }
  if (!defaultValue && options.length) defaultValue = options[0].value;
  return { options, defaultValue };
}

function unifiedVoiceSelectHtml(options, defaultValue, selectedValue) {
  if (!options?.length) {
    return `<option value="">Aucune voix installée</option>`;
  }
  const sel = selectedValue || defaultValue || "";
  const groups = {};
  for (const o of options) {
    const g = o.group || "Autre";
    (groups[g] || (groups[g] = [])).push(o);
  }
  const elGroups = Object.keys(groups)
    .filter((g) => g.startsWith("ElevenLabs"))
    .sort((a, b) => a.localeCompare(b, "fr"));
  const groupOrder = ["Piper", "Kokoro", "Edge", ...elGroups];
  const parts = [];
  for (const g of groupOrder) {
    const list = groups[g] || [];
    if (!list.length) continue;
    parts.push(`<optgroup label="${escapeHtml(g)}">`);
    for (const o of list) {
      parts.push(
        `<option value="${escapeHtml(o.value)}"${
          o.value === sel ? " selected" : ""
        }>${escapeHtml(o.label)}</option>`
      );
    }
    parts.push("</optgroup>");
  }
  return parts.join("");
}

async function synthesizePromptTtsConcat(segments, gapMs = 400) {
  if (!state.currentApp) throw new Error("Aucune app sélectionnée");
  const res = await fetch(
    `/api/apps/${encodeURIComponent(state.currentApp)}/prompts/tts-concat`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-UCCX-Token": state.token,
      },
      body: JSON.stringify({
        segments: (segments || []).map((s) => ({
          text: s.text,
          lang: s.lang,
          voice: s.voice || null,
          engine: s.engine || "piper",
        })),
        gap_ms: gapMs,
      }),
    }
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = err.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : detail?.message || `TTS concat HTTP ${res.status}`
    );
  }
  const blob = await res.blob();
  if ((segments || []).some((s) => (s.engine || "").toLowerCase() === "elevenlabs")) {
    void refreshElevenlabsUsage();
  }
  return blob;
}

async function postPromptStt({ paramName, lang, file, relpath, sourceLang }) {
  const fd = new FormData();
  fd.append("lang", lang);
  if (paramName) fd.append("param_name", paramName);
  if (file) fd.append("file", file);
  if (relpath) {
    fd.append("relpath", relpath);
    fd.append("source_lang", sourceLang || lang);
  }
  const res = await fetch(
    `/api/apps/${encodeURIComponent(state.currentApp)}/prompts/stt`,
    {
      method: "POST",
      headers: { "X-UCCX-Token": state.token },
      body: fd,
    }
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `STT HTTP ${res.status}`);
  return data;
}

/** STT dès la sélection (fichier local ou browse UCCX). */
async function transcribeSelection(paramName, lang, cell, { file, relpath, sourceLang } = {}) {
  if (!getCachedSttEnabled(paramName, lang)) return null;
  const seq = ++_sttSelectSeq;
  if (cell) {
    cell.classList.add("is-stt-running");
    cell.classList.remove("is-stt-done");
  }
  syncSttTtsPanelText(cell, "", null);
  const badge = cell?.querySelector("[data-prompt-text-source]");
  if (badge) {
    badge.textContent = "Transcription…";
    badge.dataset.kind = "loading";
  }
  try {
    const data = await postPromptStt({
      paramName,
      lang,
      file: file || null,
      relpath: relpath || "",
      sourceLang: sourceLang || lang,
    });
    if (seq !== _sttSelectSeq) return null;
    if (data.stt_enabled === false) {
      setMsg("prompts-msg", `STT désactivé pour ${paramName}/${lang}`, "meta");
      return null;
    }
    const text = data.text || "";
    const key = transcriptCacheKey(paramName, lang);
    const prev = transcriptCache[key] || { stt_enabled: true };
    transcriptCache[key] = { ...prev, text, source: "stt", stt_enabled: true };
    const pend = getPending(paramName, lang);
    if (pend) {
      setPending(paramName, lang, {
        ...pend,
        transcriptText: text,
        transcriptSource: "stt",
      });
    }
    syncSttTtsPanelText(cell, text, "stt");
    if (cell) {
      cell.classList.remove("is-stt-running");
      cell.classList.add("is-stt-done");
      setTimeout(() => cell.classList.remove("is-stt-done"), 700);
    }
    return { text, source: "stt" };
  } catch (err) {
    if (cell) cell.classList.remove("is-stt-running");
    if (badge) {
      badge.textContent = "STT échoué";
      badge.dataset.kind = "error";
    }
    console.warn("stt select", err);
    setMsg("prompts-msg", `STT ${paramName}/${lang}: ${err.message}`, "warn");
    return null;
  }
}

async function downloadPromptAudio(paramName, lang, relpath, pending) {
  if (pending?.localFile) {
    const url = URL.createObjectURL(pending.localFile);
    const a = document.createElement("a");
    a.href = url;
    a.download = pending.localFile.name || `${paramName}-${lang}.wav`;
    a.click();
    URL.revokeObjectURL(url);
    return;
  }
  const useLang = pending?.relpath ? pending.sourceLang || lang : lang;
  const useRel = pending?.relpath || relpath;
  if (!useRel) throw new Error("Aucun fichier à télécharger");
  const qs = new URLSearchParams({
    lang: useLang,
    relpath: useRel,
    download: "true",
  });
  const res = await fetch(
    `/api/apps/${encodeURIComponent(state.currentApp)}/prompt-audio?${qs}`,
    { headers: { "X-UCCX-Token": state.token } }
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Téléchargement HTTP ${res.status}`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = useRel.split("/").pop() || `${paramName}-${lang}.wav`;
  a.click();
  URL.revokeObjectURL(url);
}

async function synthesizePromptTts(lang, text, voice, engine = "piper") {
  if (!state.currentApp) throw new Error("Aucune app sélectionnée");
  const res = await fetch(
    `/api/apps/${encodeURIComponent(state.currentApp)}/prompts/tts`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-UCCX-Token": state.token,
      },
      body: JSON.stringify({
        text,
        lang,
        voice: voice || null,
        engine: engine || "piper",
      }),
    }
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = err.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : detail?.message || `TTS HTTP ${res.status}`
    );
  }
  const blob = await res.blob();
  if ((engine || "").toLowerCase() === "elevenlabs") {
    void refreshElevenlabsUsage();
  }
  return blob;
}

async function addPromptLanguage(langCode) {
  return api(`/api/apps/${encodeURIComponent(state.currentApp)}/prompt-languages`, {
    method: "POST",
    json: { lang: langCode },
  });
}

let _promptAudio = null;
let _promptAudioUrl = null;
let _promptAudioOwnsUrl = false;
let _promptAudioCell = null;

function syncPromptAudioControls(cell, mode) {
  // mode: idle | playing | paused
  const actions = cell?.querySelector(".prompt-lang-actions");
  if (!actions) return;
  const playBtn = actions.querySelector("[data-prompt-play]");
  let pauseBtn = actions.querySelector("[data-prompt-pause]");
  let stopBtn = actions.querySelector("[data-prompt-stop]");
  if (mode === "idle") {
    if (playBtn) playBtn.classList.remove("hidden");
    pauseBtn?.remove();
    stopBtn?.remove();
    cell?.classList.remove("is-playing");
    return;
  }
  if (playBtn) playBtn.classList.add("hidden");
  if (!pauseBtn) {
    pauseBtn = document.createElement("button");
    pauseBtn.type = "button";
    pauseBtn.className = "btn btn-default btn-sm";
    pauseBtn.dataset.promptPause = "1";
    pauseBtn.title = "Pause";
    actions.insertBefore(pauseBtn, actions.querySelector("[data-prompt-delete]"));
  }
  if (!stopBtn) {
    stopBtn = document.createElement("button");
    stopBtn.type = "button";
    stopBtn.className = "btn btn-default btn-sm";
    stopBtn.dataset.promptStop = "1";
    stopBtn.title = "Stop";
    stopBtn.textContent = "⏹";
    actions.insertBefore(stopBtn, actions.querySelector("[data-prompt-delete]"));
  }
  pauseBtn.textContent = mode === "paused" ? "▶" : "⏸";
  pauseBtn.title = mode === "paused" ? "Reprendre" : "Pause";
  cell?.classList.add("is-playing");
}

function stopPromptAudio() {
  if (_promptAudio) {
    _promptAudio.pause();
    _promptAudio = null;
  }
  if (_promptAudioUrl && _promptAudioOwnsUrl) {
    URL.revokeObjectURL(_promptAudioUrl);
  }
  _promptAudioUrl = null;
  _promptAudioOwnsUrl = false;
  if (_promptAudioCell) {
    syncPromptAudioControls(_promptAudioCell, "idle");
    _promptAudioCell = null;
  }
}

function pausePromptAudio() {
  if (!_promptAudio || !_promptAudioCell) return;
  if (_promptAudio.paused) {
    _promptAudio.play();
    syncPromptAudioControls(_promptAudioCell, "playing");
    setMsg("prompts-msg", "Lecture reprise", "ok");
  } else {
    _promptAudio.pause();
    syncPromptAudioControls(_promptAudioCell, "paused");
    setMsg("prompts-msg", "Lecture en pause", "meta");
  }
}

async function playBlobAudio(blobOrFileOrUrl, label, cell, { isObjectUrl = false } = {}) {
  stopPromptAudio();
  setMsg("prompts-msg", `Préécoute ${label}…`, "meta");
  if (isObjectUrl && typeof blobOrFileOrUrl === "string") {
    _promptAudioUrl = blobOrFileOrUrl;
    _promptAudioOwnsUrl = false;
    _promptAudio = new Audio(blobOrFileOrUrl);
  } else {
    _promptAudioUrl = URL.createObjectURL(blobOrFileOrUrl);
    _promptAudioOwnsUrl = true;
    _promptAudio = new Audio(_promptAudioUrl);
  }
  _promptAudioCell = cell || null;
  _promptAudio.onended = () => stopPromptAudio();
  try {
    await _promptAudio.play();
  } catch (err) {
    stopPromptAudio();
    throw new Error(
      `Lecture impossible: ${err.message || err}. ` +
        "Le navigateur ne prend peut-être pas en charge ce format — enregistrez puis réessayez."
    );
  }
  if (_promptAudioCell) syncPromptAudioControls(_promptAudioCell, "playing");
  setMsg("prompts-msg", `Préécoute ${label}`, "ok");
}

async function playPromptAudio(lang, relpath, cell) {
  if (!state.currentApp || !lang || !relpath) return;
  setMsg("prompts-msg", `Préécoute ${lang}/${shortPromptFilename(relpath)}…`, "meta");
  const url =
    `/api/apps/${encodeURIComponent(state.currentApp)}/prompt-audio` +
    `?lang=${encodeURIComponent(lang)}&relpath=${encodeURIComponent(relpath)}`;
  const res = await fetch(url, { headers: { "X-UCCX-Token": state.token } });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Audio HTTP ${res.status}`);
  }
  const blob = await res.blob();
  await playBlobAudio(blob, `${lang}/${shortPromptFilename(relpath)}`, cell);
}

/**
 * Préécoute : drop local → sélection browse → fichier langue → fallback default.
 */
async function playPromptPreview(paramName, lang, savedRelpath, cell, byLang = {}) {
  const pending = getPending(paramName, lang);
  if (pending?.localFile) {
    const label = pending.localFile.name || "fichier local";
    if (pending.blobUrl) {
      await playBlobAudio(pending.blobUrl, label, cell, { isObjectUrl: true });
    } else {
      await playBlobAudio(pending.localFile, label, cell);
    }
    return;
  }
  if (pending?.relpath) {
    await playPromptAudio(pending.sourceLang || lang, pending.relpath, cell);
    return;
  }
  if (!savedRelpath) {
    throw new Error("Aucun fichier à préécouter");
  }
  if (byLang[lang]) {
    await playPromptAudio(lang, savedRelpath, cell);
    return;
  }
  if (lang !== "default" && byLang.default) {
    setMsg("prompts-msg", `Pas de fichier ${lang} — préécoute default…`, "meta");
    await playPromptAudio("default", savedRelpath, cell);
    return;
  }
  throw new Error(`Aucun fichier pour « ${lang} » ni en default`);
}

function setParamPlayableLocal(paramName, playable) {
  if (!state.uccx?.scriptParams) return;
  const sp = state.uccx.scriptParams.find((p) => p.name === paramName);
  if (sp) sp.value = normalizePlayableDisplay(playable);
}

function ensurePlayButton(cell) {
  let actions = cell.querySelector(".prompt-lang-actions");
  if (!actions) {
    actions = document.createElement("div");
    actions.className = "prompt-lang-actions";
    cell.appendChild(actions);
  }
  if (!actions.querySelector("[data-prompt-play]")) {
    const playBtn = document.createElement("button");
    playBtn.type = "button";
    playBtn.className = "btn btn-default btn-sm prompt-lang-play";
    playBtn.dataset.promptPlay = "1";
    playBtn.title = "Préécouter";
    playBtn.textContent = "▶";
    const del = actions.querySelector("[data-prompt-delete]");
    if (del) actions.insertBefore(playBtn, del);
    else actions.appendChild(playBtn);
  }
}

/** ▶ selon data-can-play (présent / pending / fallback default). */
function syncPlayButtonVisibility(cell) {
  const canPlay = cell.dataset.canPlay === "1";
  let actions = cell.querySelector(".prompt-lang-actions");
  if (canPlay) {
    ensurePlayButton(cell);
    return;
  }
  if (!actions) return;
  actions.querySelector("[data-prompt-play]")?.remove();
  if (!actions.querySelector("[data-prompt-delete]")) actions.remove();
}

function applyPendingToCell(cell, paramName, lang, savedRelpath, byLang = {}) {
  const pending = getPending(paramName, lang);
  const hasPending = !!(pending?.localFile || pending?.relpath);
  const browseSel = cell.querySelector("[data-prompt-browse]");
  const btnLabel = browseSel?.querySelector(".prompt-browse-btn-label");
  const dropLabel = cell.querySelector(".prompt-drop-label");
  const present = cell.classList.contains("is-present");
  const canPlay = cellCanPlay(lang, present, pending, byLang, savedRelpath);
  cell.dataset.canPlay = canPlay ? "1" : "0";
  if (btnLabel) {
    btnLabel.innerHTML = browseLabelHtml(
      pending,
      lang,
      present ? savedRelpath : ""
    );
  }
  if (browseSel) {
    const title = pending?.localFile
      ? pending.localFile.name
      : pending?.relpath
        ? `${pending.sourceLang || lang}/${pending.relpath}`
        : present && savedRelpath
          ? savedRelpath
          : "Prompts UCCX";
    browseSel.title = title;
  }
  if (dropLabel) {
    dropLabel.textContent = pending?.localFile
      ? shortPromptFilename(pending.localFile.name)
      : "Drop";
  }
  if (hasPending) cell.classList.add("is-pending");
  else cell.classList.remove("is-pending");
  syncPlayButtonVisibility(cell);
}

function wireLangCell(cell, paramName, lang, relpath, byLang = {}) {
  const drop = cell.querySelector("[data-prompt-drop]");
  const fileInput = cell.querySelector(".prompt-file-input");
  const browseSel = cell.querySelector("[data-prompt-browse]");

  const setLocalPending = (file, extra = {}) => {
    if (!file) return;
    setPending(paramName, lang, {
      sourceLang: lang,
      relpath: "",
      localFile: file,
      transcriptText: extra.transcriptText || "",
      transcriptSource: extra.transcriptSource || "",
    });
    applyPendingToCell(cell, paramName, lang, relpath, byLang);
    setMsg(
      "prompts-msg",
      `${paramName}/${lang} : ${file.name} (préécoute ▶ puis Enregistrer prompts)`,
      "meta"
    );
    if (extra.skipStt) return;
    if (extra.transcriptSource === "tts" && extra.transcriptText) {
      syncSttTtsPanelText(cell, extra.transcriptText, "tts");
      return;
    }
    void transcribeSelection(paramName, lang, cell, { file });
  };

  if (drop && fileInput) {
    drop.style.cursor = "pointer";
    drop.addEventListener("click", () => fileInput.click());
    drop.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        fileInput.click();
      }
    });
    drop.setAttribute("tabindex", "0");
    drop.setAttribute("role", "button");
    fileInput.addEventListener("change", () => {
      const file = fileInput.files?.[0];
      if (file) setLocalPending(file);
    });
    drop.addEventListener("dragenter", (e) => {
      e.preventDefault();
      drop.classList.add("dragover");
    });
    drop.addEventListener("dragover", (e) => {
      e.preventDefault();
      drop.classList.add("dragover");
    });
    drop.addEventListener("dragleave", () => drop.classList.remove("dragover"));
    drop.addEventListener("drop", (e) => {
      e.preventDefault();
      drop.classList.remove("dragover");
      const file = e.dataTransfer?.files?.[0];
      if (!file) return;
      const dt = new DataTransfer();
      dt.items.add(file);
      fileInput.files = dt.files;
      setLocalPending(file);
    });
  }

  if (browseSel) {
    const btn = browseSel.querySelector("[data-prompt-browse-btn]");
    const panel = browseSel.querySelector(".prompt-browse-panel");
    const filterInput = browseSel.querySelector("[data-prompt-browse-filter]");
    const listEl = browseSel.querySelector("[data-prompt-browse-list]");

    const renderBrowseList = (files, filter) => {
      if (!listEl) return;
      const q = (filter || "").trim().toLowerCase();
      const pending = getPending(paramName, lang);
      const curRel = pending?.relpath || relpath;
      const rows = [];
      for (const f of files) {
        const srcLang = f.lang || lang;
        const fullPath = `${srcLang}/${f.relpath || ""}`.replace(/\/+/g, "/");
        if (q && !fullPath.toLowerCase().includes(q) && !(f.filename || "").toLowerCase().includes(q)) {
          continue;
        }
        const isCurrent = !!(curRel && f.relpath === curRel);
        rows.push(`
          <button type="button" class="prompt-browse-item${isCurrent ? " is-current" : ""}"
            data-src-lang="${escapeHtml(srcLang)}"
            data-relpath="${escapeHtml(f.relpath || "")}"
            title="${escapeHtml(fullPath)}">
            <span class="prompt-browse-item-path">${coloredPathHtml(fullPath)}</span>
          </button>`);
      }
      if (!rows.length) {
        listEl.innerHTML = `<div class="prompt-browse-empty">Aucun fichier</div>`;
        return;
      }
      listEl.innerHTML = rows.join("");
    };

    const openPanel = async () => {
      closeAllPromptBrowsePanels(browseSel);
      browseSel.classList.add("is-open");
      panel?.classList.remove("hidden");
      if (filterInput) {
        filterInput.value = "";
        filterInput.focus();
      }
      if (!listEl) return;
      listEl.innerHTML = `<div class="prompt-browse-empty">Chargement…</div>`;
      try {
        const files = await fetchAllPromptFiles(browseSel.dataset.loaded !== "1");
        browseSel.dataset.loaded = "1";
        browseSel._browseFiles = files;
        renderBrowseList(files, "");
      } catch (err) {
        listEl.innerHTML = `<div class="prompt-browse-empty">Erreur: ${escapeHtml(err.message)}</div>`;
        console.warn("prompt-files", err);
      }
    };

    const closePanel = () => {
      browseSel.classList.remove("is-open");
      panel?.classList.add("hidden");
    };

    btn?.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (browseSel.classList.contains("is-open")) closePanel();
      else openPanel();
    });

    filterInput?.addEventListener("input", () => {
      renderBrowseList(browseSel._browseFiles || [], filterInput.value);
    });
    filterInput?.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        closePanel();
      }
    });

    listEl?.addEventListener("click", (e) => {
      const item = e.target.closest(".prompt-browse-item");
      if (!item) return;
      e.preventDefault();
      e.stopPropagation();
      const sourceLang = item.dataset.srcLang || lang;
      const rel = item.dataset.relpath || "";
      if (!rel) return;
      closePanel();
      setPending(paramName, lang, {
        sourceLang,
        relpath: rel,
        localFile: null,
        transcriptText: "",
        transcriptSource: "",
      });
      applyPendingToCell(cell, paramName, lang, relpath, byLang);
      setMsg(
        "prompts-msg",
        `${paramName}/${lang} ← ${sourceLang}/${rel} (préécoute ▶ puis Enregistrer prompts)`,
        "meta"
      );
      void transcribeSelection(paramName, lang, cell, {
        relpath: rel,
        sourceLang,
      });
    });
  }

  const ttsWrap = cell.querySelector("[data-prompt-tts]");
  if (ttsWrap) {
    const ttsBtn = ttsWrap.querySelector("[data-prompt-tts-btn]");
    const ttsPanel = ttsWrap.querySelector(".prompt-tts-panel");
    const voiceSel = ttsWrap.querySelector("[data-tts-voice]");
    const textArea = ttsWrap.querySelector("[data-tts-text]");
    const editor = ttsWrap.querySelector("[data-tts-editor]");
    const tagLangSel = ttsWrap.querySelector("[data-tts-tag-lang]");
    const tagWrapBtn = ttsWrap.querySelector("[data-tts-tag-wrap]");
    const ttsGen = ttsWrap.querySelector("[data-prompt-tts-generate]");
    const ttsCopy = ttsWrap.querySelector("[data-prompt-tts-copy]");
    const sttToggle = ttsWrap.querySelector("[data-prompt-stt-toggle]");
    const langChoices =
      (state.promptLanguages || []).length > 0
        ? state.promptLanguages
        : [lang];

    const syncEditorToTextarea = () => {
      if (!textArea || !editor) return;
      textArea.value = richEditorToMarkup(editor);
      editor.classList.toggle("is-empty", !(textArea.value || "").trim());
    };

    const markTextEdited = () => {
      syncEditorToTextarea();
      const badge = cell.querySelector("[data-prompt-text-source]");
      if (badge) {
        badge.textContent = "Texte";
        badge.dataset.kind = "edit";
      }
    };

    const fillTagLangOptions = () => {
      if (!tagLangSel) return;
      const prefer =
        langChoices.find((l) => l !== lang && !/^default$/i.test(l)) ||
        langChoices[0] ||
        "en";
      tagLangSel.innerHTML = langChoices
        .map(
          (l) =>
            `<option value="${escapeHtml(l)}"${
              l === prefer ? " selected" : ""
            }>${escapeHtml(l)}</option>`
        )
        .join("");
    };

    const fillVoices = async (preferValue) => {
      if (!voiceSel) return;
      voiceSel.innerHTML = `<option value="">Chargement…</option>`;
      try {
        const { options, defaultValue } = await fetchUnifiedVoiceOptions(lang);
        voiceSel.innerHTML = unifiedVoiceSelectHtml(
          options,
          defaultValue,
          preferValue || ""
        );
      } catch (err) {
        voiceSel.innerHTML = `<option value="">Erreur: ${escapeHtml(err.message)}</option>`;
      }
    };

    const wrapSelectionWithLangTag = () => {
      if (!editor || !tagLangSel) return;
      const code = (tagLangSel.value || "en").replace(/-/g, "_");
      editor.focus();
      const sel = window.getSelection();
      if (!sel || sel.rangeCount === 0) return;
      const range = sel.getRangeAt(0);
      if (!editor.contains(range.commonAncestorContainer)) return;
      const selected = range.toString();
      const chip = document.createElement("span");
      chip.className = "prompt-tts-chip";
      chip.dataset.lang = code;
      chip.dataset.tone = String(ttsLangTone(code));
      chip.contentEditable = "false";
      chip.innerHTML =
        `<span class="prompt-tts-chip-lang">${escapeHtml(code)}</span>` +
        `<span class="prompt-tts-chip-text" contenteditable="true"></span>` +
        `<button type="button" class="prompt-tts-chip-remove" data-tts-chip-remove tabindex="-1" title="Retirer la balise">×</button>`;
      const textEl = chip.querySelector(".prompt-tts-chip-text");
      textEl.textContent = selected || "";
      range.deleteContents();
      range.insertNode(chip);
      // Espace après le chip pour continuer à taper
      const space = document.createTextNode("\u00a0");
      chip.after(space);
      const after = document.createRange();
      if (selected) {
        after.setStartAfter(space);
        after.collapse(true);
      } else {
        after.selectNodeContents(textEl);
        after.collapse(true);
      }
      sel.removeAllRanges();
      sel.addRange(after);
      markTextEdited();
    };

    const fillPanelFromPending = async () => {
      const pend = getPending(paramName, lang);
      const cached = transcriptCache[transcriptCacheKey(paramName, lang)];
      const text = pend?.transcriptText || cached?.text || "";
      const source = pend?.transcriptSource || cached?.source || null;
      const saved = loadTtsLastSelection(paramName, lang);
      fillTagLangOptions();
      setTtsRichEditorText(ttsPanel || ttsWrap, saved?.text || text || "");
      await fillVoices(saved?.voiceValue || "");
      const badge = cell.querySelector("[data-prompt-text-source]");
      if (badge) {
        if (source === "tts") {
          badge.textContent = "Texte TTS";
          badge.dataset.kind = "tts";
        } else if (source === "stt") {
          badge.textContent = "Transcription STT";
          badge.dataset.kind = "stt";
        } else {
          const has = !!(textArea?.value || "").trim();
          badge.textContent = has ? "Texte" : "";
          badge.dataset.kind = has ? "edit" : "empty";
        }
      }
      if (sttToggle) sttToggle.checked = getCachedSttEnabled(paramName, lang);
    };

    const openTts = async () => {
      closeAllPromptBrowsePanels(ttsWrap);
      ttsWrap.classList.add("is-open");
      if (ttsPanel) {
        const titleEl = ttsPanel.querySelector("[data-tts-panel-title]");
        if (titleEl) titleEl.textContent = `${paramName} · ${lang}`;
        ttsPanel.setAttribute("aria-label", `STT et TTS — ${paramName} · ${lang}`);
        ttsPanel.classList.remove("hidden");
        placeTtsPanel(ttsPanel);
        wireTtsPanelChrome(ttsPanel);
      }
      await fillPanelFromPending();
      editor?.focus();
    };

    const closeTts = () => {
      syncEditorToTextarea();
      persistTtsPanelGeom(ttsPanel);
      ttsWrap.classList.remove("is-open");
      ttsPanel?.classList.add("hidden");
    };

    ttsBtn?.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (ttsWrap.classList.contains("is-open")) closeTts();
      else openTts();
    });

    ttsPanel?.addEventListener("click", (e) => e.stopPropagation());
    ttsPanel?.querySelector("[data-tts-close]")?.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      closeTts();
    });
    ttsPanel?.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && e.target === ttsPanel) {
        e.preventDefault();
        closeTts();
      }
    });

    editor?.addEventListener("input", () => markTextEdited());
    editor?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        document.execCommand("insertLineBreak");
        markTextEdited();
      }
    });
    editor?.addEventListener("paste", (e) => {
      e.preventDefault();
      const plain = (e.clipboardData || window.clipboardData)?.getData("text/plain") || "";
      const html = markupToRichHtml(plain);
      document.execCommand("insertHTML", false, html || escapeHtml(plain));
      markTextEdited();
    });
    editor?.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-tts-chip-remove]");
      if (!btn || !editor.contains(btn)) return;
      e.preventDefault();
      e.stopPropagation();
      const chip = btn.closest(".prompt-tts-chip");
      if (!chip) return;
      const body = chip.querySelector(".prompt-tts-chip-text")?.textContent || "";
      const textNode = document.createTextNode(body);
      chip.replaceWith(textNode);
      markTextEdited();
    });

    tagWrapBtn?.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      wrapSelectionWithLangTag();
    });

    ttsCopy?.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      syncEditorToTextarea();
      const text = (textArea?.value || "").trim();
      if (!text) {
        setMsg("prompts-msg", "Rien à copier", "warn");
        return;
      }
      try {
        await navigator.clipboard.writeText(text);
        setMsg("prompts-msg", "Texte copié", "ok");
      } catch {
        setMsg("prompts-msg", "Copie impossible", "error");
      }
    });

    sttToggle?.addEventListener("change", async () => {
      const enabled = !!sttToggle.checked;
      sttToggle.disabled = true;
      try {
        await patchPromptSttSettings(paramName, lang, enabled, relpath);
        setMsg(
          "prompts-msg",
          enabled
            ? `STT activé pour ${paramName}/${lang}`
            : `STT désactivé pour ${paramName}/${lang}`,
          "meta"
        );
      } catch (err) {
        sttToggle.checked = !enabled;
        setMsg("prompts-msg", err.message, "error");
      } finally {
        sttToggle.disabled = false;
      }
    });

    ttsGen?.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      syncEditorToTextarea();
      const text = (textArea?.value || "").trim();
      const rawVoice = voiceSel?.value || "";
      const { engine, voice } = parseVoiceEngineValue(rawVoice);
      if (!text) {
        setMsg("prompts-msg", "Saisissez un texte à synthétiser", "warn");
        editor?.focus();
        return;
      }
      if (!voice) {
        setMsg("prompts-msg", "Choisissez une voix", "warn");
        return;
      }
      ttsGen.disabled = true;
      const prevLabel = ttsGen.textContent;
      ttsGen.textContent = "…";
      setMsg("prompts-msg", `Synthèse TTS → ${lang}…`, "meta");
      try {
        const segs = await expandTextToConcatSegments({
          text,
          baseLang: lang,
          engine,
          voice,
          voiceValue: rawVoice,
        });
        if (!segs.length) {
          throw new Error("Texte vide après parse des balises");
        }
        const blob = await synthesizePromptTtsConcat(segs, segs.length > 1 ? 10 : 0);
        const file = new File([blob], `tts-${lang}.wav`, { type: "audio/wav" });
        saveTtsLastSelection(paramName, lang, {
          text,
          voiceValue: rawVoice,
          engine,
          voice,
        });
        setLocalPending(file, {
          transcriptText: text,
          transcriptSource: "tts",
          skipStt: true,
        });
        const badge = cell.querySelector("[data-prompt-text-source]");
        if (badge) {
          badge.textContent = "Texte TTS";
          badge.dataset.kind = "tts";
        }
        await playPromptPreview(paramName, lang, relpath, cell, byLang);
        const n = segs.length;
        setMsg(
          "prompts-msg",
          n > 1
            ? `TTS prêt (${n} parties → ${lang}) — Enregistrer pour pousser UCCX`
            : `TTS prêt (${lang}) — Enregistrer pour pousser UCCX`,
          "ok"
        );
      } catch (err) {
        setMsg("prompts-msg", err.message, "error");
        alert(err.message);
      } finally {
        ttsGen.disabled = false;
        ttsGen.textContent = prevLabel || "Générer";
      }
    });
  }

  applyPendingToCell(cell, paramName, lang, relpath, byLang);

  // Délégation sur la cellule : le ▶ peut être créé après un drop (actions absentes au wire)
  cell.addEventListener("click", async (e) => {
    const t = e.target.closest("button");
    if (!t || !cell.contains(t)) return;
    if (
      !t.matches(
        "[data-prompt-play], [data-prompt-pause], [data-prompt-stop], [data-prompt-delete], [data-prompt-download]"
      )
    ) {
      return;
    }
    e.preventDefault();
    e.stopPropagation();
    if (t.matches("[data-prompt-download]")) {
      try {
        await downloadPromptAudio(paramName, lang, relpath, getPending(paramName, lang));
        setMsg("prompts-msg", `Téléchargement ${paramName}/${lang}`, "ok");
      } catch (err) {
        alert(err.message);
      }
      return;
    }
    if (t.matches("[data-prompt-play]")) {
      try {
        await playPromptPreview(paramName, lang, relpath, cell, byLang);
      } catch (err) {
        alert(err.message);
      }
      return;
    }
    if (t.matches("[data-prompt-pause]")) {
      pausePromptAudio();
      return;
    }
    if (t.matches("[data-prompt-stop]")) {
      stopPromptAudio();
      setMsg("prompts-msg", "Lecture arrêtée", "meta");
      return;
    }
    if (t.matches("[data-prompt-delete]")) {
      let msg = `Supprimer le fichier pour « ${lang} » ?`;
      if (lang === "default") {
        msg += "\n\nAttention : default est le fallback pour toutes les langues.";
      }
      if (!confirm(msg)) return;
      try {
        stopPromptAudio();
        clearPending(paramName, lang);
        await deletePromptParam(paramName, lang);
      } catch (err) {
        alert(err.message);
      }
    }
  });
}

function renderPromptLangCell(paramName, lang, present, relpath, pending, byLang = {}) {
  const hasPending = !!(pending?.localFile || pending?.relpath);
  const canPlay = cellCanPlay(lang, present, pending, byLang, relpath);
  const canDownload = !!(pending?.localFile || pending?.relpath || (present && relpath));
  const sttOn = getCachedSttEnabled(paramName, lang);
  const statusCls = [
    present ? "is-present" : "is-missing",
    hasPending ? "is-pending" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const playTitle =
    !present && !hasPending && byLang.default
      ? "Préécouter (default)"
      : "Préécouter";
  const actions = `
      <div class="prompt-lang-actions">
        ${
          canPlay
            ? `<button type="button" class="btn btn-default btn-sm prompt-lang-play" data-prompt-play title="${escapeHtml(playTitle)}">▶</button>`
            : ""
        }
        ${
          canDownload
            ? `<button type="button" class="btn btn-default btn-sm" data-prompt-download title="Télécharger le WAV">↓</button>`
            : ""
        }
        ${
          present
            ? `<button type="button" class="btn btn-default btn-sm prompt-lang-delete" data-prompt-delete title="Supprimer">×</button>`
            : ""
        }
      </div>`;
  const btnInner = browseLabelHtml(pending, lang, present ? relpath : "");
  const title =
    pending?.localFile?.name ||
    (pending?.relpath
      ? `${pending.sourceLang || lang}/${pending.relpath}`
      : present && relpath
        ? relpath
        : "Prompts UCCX");
  const dropText = pending?.localFile
    ? shortPromptFilename(pending.localFile.name)
    : "Drop";
  return `
    <div class="prompt-lang-cell ${statusCls}" data-lang="${escapeHtml(lang)}" data-relpath="${escapeHtml(relpath || "")}" data-can-play="${canPlay ? "1" : "0"}">
      <div class="prompt-lang-name">${escapeHtml(lang)}</div>
      <div class="prompt-lang-split">
        <div class="prompt-dropzone" data-prompt-drop aria-label="Déposer ou choisir un audio ${escapeHtml(lang)}">
          <input type="file" class="prompt-file-input" accept="audio/*,.wav,.mp3,.m4a,.ogg,.aiff,.flac" tabindex="-1" aria-hidden="true" />
          <span class="prompt-drop-label">${escapeHtml(dropText)}</span>
        </div>
        <div class="prompt-browse-wrap" data-prompt-browse title="${escapeHtml(title)}">
          <button type="button" class="prompt-browse-btn" data-prompt-browse-btn aria-haspopup="listbox">
            <span class="prompt-browse-btn-label">${btnInner}</span>
          </button>
          <div class="prompt-browse-panel hidden" role="listbox">
            <input type="search" class="prompt-browse-filter" data-prompt-browse-filter placeholder="Filtrer chemin / nom…" autocomplete="off" />
            <div class="prompt-browse-list" data-prompt-browse-list></div>
          </div>
        </div>
        <div class="prompt-tts-wrap" data-prompt-tts>
          <button type="button" class="prompt-tts-btn" data-prompt-tts-btn aria-haspopup="dialog" title="STT / TTS">
            STT/TTS
          </button>
          <div class="prompt-tts-panel hidden" role="dialog" aria-label="STT et TTS — ${escapeHtml(paramName)} · ${escapeHtml(lang)}">
            <div class="prompt-tts-panel-top" data-tts-drag>
              <div class="prompt-tts-panel-heading">
                <div class="prompt-tts-panel-title" data-tts-panel-title>${escapeHtml(paramName)} · ${escapeHtml(lang)}</div>
                <span class="prompt-text-source-badge" data-prompt-text-source data-kind="empty"></span>
              </div>
              <div class="prompt-tts-panel-controls">
                <label class="prompt-stt-toggle-label" title="Transcription automatique à la sélection">
                  <input type="checkbox" data-prompt-stt-toggle ${sttOn ? "checked" : ""} />
                  <span>STT auto</span>
                </label>
                <button type="button" class="btn btn-link btn-sm prompt-tts-close" data-tts-close aria-label="Fermer">×</button>
              </div>
            </div>
            <div class="prompt-tts-body" data-prompt-tts-body>
              <label class="prompt-tts-label prompt-tts-voice-label">Voix
                <select class="form-control prompt-tts-voice-engine" data-tts-voice>
                  <option value="">—</option>
                </select>
              </label>
              <div class="prompt-tts-lang-toolbar">
                <label class="prompt-tts-label prompt-tts-tag-lang-label">Langue balise
                  <select class="form-control prompt-tts-tag-lang" data-tts-tag-lang></select>
                </label>
                <button type="button" class="btn btn-default btn-sm" data-tts-tag-wrap title="Encadrer la sélection avec [[langue:…]]">Encadrer la sélection</button>
              </div>
              <label class="prompt-tts-label prompt-tts-text-label">Texte
                <div
                  class="form-control prompt-tts-editor"
                  data-tts-editor
                  contenteditable="true"
                  role="textbox"
                  aria-multiline="true"
                  data-placeholder="Texte… Sélectionnez un mot puis Encadrer."
                ></div>
                <textarea class="prompt-tts-text-sync" data-tts-text hidden maxlength="8000" aria-hidden="true" tabindex="-1"></textarea>
              </label>
            </div>
            <div class="prompt-tts-row prompt-tts-seg-actions">
              <button type="button" class="btn btn-default btn-sm" data-prompt-tts-copy>Copier</button>
              <button type="button" class="btn btn-primary btn-sm" data-prompt-tts-generate>Générer</button>
            </div>
            <div class="prompt-tts-resize-sw" data-tts-resize="sw" aria-hidden="true"></div>
            <div class="prompt-tts-resize-se" data-tts-resize="se" aria-hidden="true"></div>
          </div>
        </div>
      </div>
      ${actions}
    </div>
  `;
}

async function renderPrompts() {
  const section = $("prompts-section");
  const list = $("prompts-list");
  const prompts = (state.uccx.scriptParams || []).filter((p) => p.kind === "prompt");
  if (!prompts.length) {
    section.classList.add("hidden");
    list.innerHTML = "";
    return;
  }
  section.classList.remove("hidden");
  normalizeUccxPlayablesInState();
  await ensurePromptLanguages({ force: true });

  let status = { languages: state.promptLanguages, params: {}, transcripts: {} };
  try {
    status = await fetchPromptStatus(true);
    seedTranscriptCache(status.transcripts || {});
  } catch (err) {
    console.warn("prompt-status", err);
    setMsg("prompts-msg", `Statut langues: ${err.message}`, "warn");
  }
  const langs = status.languages?.length ? status.languages : state.promptLanguages;

  list.innerHTML = "";
  for (const p of prompts) {
    const st = status.params?.[p.name] || {};
    // p.value (state.uccx) prioritaire sur status.playable (souvent encore P[] juste après upload)
    const playable = normalizePlayableDisplay(
      !isEmptyPlayable(p.value) ? p.value : st.playable || "P[]"
    );
    if (p.value !== playable) p.value = playable;
    const relpath = playableToRelpath(playable) || st.relpath || "";
    const byLang = st.byLang || {};
    const row = document.createElement("div");
    row.className = "prompt-row";
    row.dataset.param = p.name;
    row.innerHTML = `
      <div class="prompt-head">
        <strong class="prompt-name">${escapeHtml(p.name)}</strong>
        <code class="prompt-value-label" data-prompt-value-label>${escapeHtml(playable)}</code>
      </div>
      <div class="prompt-lang-grid">
        ${langs
          .map((lang) =>
            renderPromptLangCell(
              p.name,
              lang,
              !!byLang[lang],
              relpath,
              getPending(p.name, lang),
              byLang
            )
          )
          .join("")}
      </div>
    `;
    for (const cell of row.querySelectorAll(".prompt-lang-cell")) {
      wireLangCell(cell, p.name, cell.dataset.lang, relpath, byLang);
    }
    list.appendChild(row);
  }
}

/** Valeurs Playable depuis state (normalisées) — pas le HTML du label. */
function collectPrompts() {
  const out = {};
  for (const p of state.uccx?.scriptParams || []) {
    if (p.kind !== "prompt") continue;
    out[p.name] = normalizePlayableDisplay(p.value || "P[]");
  }
  return out;
}

function renderIntervalRow(iv, idx) {
  const start = formatTime24(iv.startTime, DEFAULT_INTERVAL_START);
  const end = formatTime24(iv.endTime, DEFAULT_INTERVAL_END);
  return `
    <div class="interval-row" data-iv="${idx}">
      <div class="form-group">
        <label class="control-label">Début</label>
        <input type="text" class="form-control start time-picker-24h" placeholder="${DEFAULT_INTERVAL_START}" value="${escapeHtml(start)}" />
      </div>
      <div class="form-group">
        <label class="control-label">Fin</label>
        <input type="text" class="form-control end time-picker-24h" placeholder="${DEFAULT_INTERVAL_END}" value="${escapeHtml(end)}" />
      </div>
      <button type="button" class="btn btn-default btn-sm remove-interval" title="Supprimer la plage">Suppr.</button>
    </div>
  `;
}

const CALENDAR_TYPE_LABELS = {
  FULLTIME: "24 Hours × 7 Days",
  FIXEDHOURS: "Fixed Hours",
  FLEXIBLEHOURS: "Flexible Hours",
};

function normalizeCalType(v) {
  const t = String(v || "").toUpperCase();
  return CALENDAR_TYPE_LABELS[t] ? t : "FLEXIBLEHOURS";
}

function cloneIntervals(intervals) {
  return (intervals || []).map((iv, idx) => ({
    name: iv.name || `Time Range ${idx + 1}`,
    startTime: formatTime24(iv.startTime, DEFAULT_INTERVAL_START),
    endTime: formatTime24(iv.endTime, DEFAULT_INTERVAL_END),
  }));
}

function firstOpenDayIntervals(businessDays) {
  for (const [dow] of CAL_DAYS) {
    const ivs = (businessDays && businessDays[dow]) || [];
    if (ivs.length) return cloneIntervals(ivs);
  }
  return [
    { name: "Time Range 1", startTime: DEFAULT_INTERVAL_START, endTime: DEFAULT_INTERVAL_END },
  ];
}

function emptyBusinessDays() {
  const out = {};
  for (const [dow] of CAL_DAYS) out[dow] = [];
  return out;
}

function convertCalendarType(cal, nextType) {
  const from = normalizeCalType(cal.calendarType);
  const to = normalizeCalType(nextType);
  if (from === to) {
    cal.calendarType = to;
    return;
  }
  const bd = cal.businessDays || emptyBusinessDays();
  if (to === "FULLTIME") {
    cal.businessDays = emptyBusinessDays();
  } else if (to === "FIXEDHOURS") {
    const shared = firstOpenDayIntervals(bd);
    const next = emptyBusinessDays();
    let anyOpen = false;
    for (const [dow] of CAL_DAYS) {
      if ((bd[dow] || []).length) {
        next[dow] = cloneIntervals(shared);
        anyOpen = true;
      }
    }
    if (!anyOpen) {
      for (const [dow] of CAL_DAYS) {
        if (dow === "SAT" || dow === "SUN") continue;
        next[dow] = cloneIntervals(shared);
      }
    }
    cal.businessDays = next;
  } else {
    // FLEXIBLEHOURS
    if (from === "FULLTIME") {
      const shared = [
        { name: "Time Range 1", startTime: DEFAULT_INTERVAL_START, endTime: DEFAULT_INTERVAL_END },
      ];
      const next = emptyBusinessDays();
      for (const [dow] of CAL_DAYS) {
        if (dow === "SAT" || dow === "SUN") continue;
        next[dow] = cloneIntervals(shared);
      }
      cal.businessDays = next;
    } else {
      // FIXED → FLEXIBLE : déjà des plages par jour
      cal.businessDays = structuredClone(bd);
    }
  }
  cal.calendarType = to;
}

function readIntervalsFromEl(container) {
  const intervals = [];
  container.querySelectorAll(".interval-row").forEach((row, idx) => {
    const start = formatTime24(row.querySelector(".start").value, DEFAULT_INTERVAL_START);
    const end = formatTime24(row.querySelector(".end").value, DEFAULT_INTERVAL_END);
    if (!start || !end) return;
    intervals.push({
      name: `Time Range ${idx + 1}`,
      startTime: start,
      endTime: end,
    });
  });
  return intervals;
}

function syncBusinessDaysFromDom() {
  if (!state.activeCalendar) return;
  const calType = normalizeCalType(state.activeCalendar.calendarType);
  const businessDays = emptyBusinessDays();

  if (calType === "FULLTIME") {
    state.activeCalendar.businessDays = businessDays;
    return;
  }

  if (calType === "FIXEDHOURS") {
    const sharedEl = document.querySelector("#calendar-days .fixed-intervals");
    const shared = sharedEl
      ? readIntervalsFromEl(sharedEl)
      : firstOpenDayIntervals(state.activeCalendar.businessDays);
    for (const cb of document.querySelectorAll("#calendar-days .fixed-day-cb")) {
      const dow = cb.dataset.dow;
      businessDays[dow] = cb.checked ? cloneIntervals(shared) : [];
    }
    state.activeCalendar.businessDays = businessDays;
    return;
  }

  for (const card of document.querySelectorAll("#calendar-days .day-card")) {
    const dow = card.dataset.dow;
    if (card.querySelector(".closed").checked) {
      businessDays[dow] = [];
      continue;
    }
    businessDays[dow] = readIntervalsFromEl(card);
  }
  state.activeCalendar.businessDays = businessDays;
}

function wireSharedIntervalEditor(container) {
  const intervalsEl = container.querySelector(".fixed-intervals") || container.querySelector(".day-intervals");
  const addBtn = container.querySelector(".add-interval");
  if (!intervalsEl || !addBtn) return;

  addBtn.addEventListener("click", () => {
    const n = intervalsEl.querySelectorAll(".interval-row").length;
    intervalsEl.insertAdjacentHTML(
      "beforeend",
      renderIntervalRow(
        { startTime: DEFAULT_INTERVAL_START, endTime: DEFAULT_INTERVAL_END },
        n
      )
    );
    wireIntervalButtons(container);
    attachTimePickersIn(intervalsEl);
    syncBusinessDaysFromDom();
  });
  wireIntervalButtons(container);
  attachTimePickersIn(container);
}

function renderFixedHoursEditor(cal, daysEl) {
  const shared = firstOpenDayIntervals(cal.businessDays);
  const wrap = document.createElement("div");
  wrap.className = "fixed-hours-editor";
  const dayChecks = CAL_DAYS.map(([dow, label]) => {
    const open = ((cal.businessDays && cal.businessDays[dow]) || []).length > 0;
    return `<label class="fixed-day-toggle">
      <input type="checkbox" class="fixed-day-cb" data-dow="${dow}" ${open ? "checked" : ""} />
      ${label}
    </label>`;
  }).join("");

  wrap.innerHTML = `
    <p class="meta">Mêmes plages horaires pour tous les jours cochés.</p>
    <div class="fixed-days">${dayChecks}</div>
    <div class="fixed-intervals">
      ${shared.map((iv, idx) => renderIntervalRow(iv, idx)).join("")}
    </div>
    <div class="day-card-actions">
      <button type="button" class="btn btn-default btn-sm add-interval">+ plage</button>
    </div>
  `;
  for (const cb of wrap.querySelectorAll(".fixed-day-cb")) {
    cb.addEventListener("change", () => syncBusinessDaysFromDom());
  }
  wireSharedIntervalEditor(wrap);
  attachTimePickersIn(wrap);
  daysEl.appendChild(wrap);
}

function renderFlexibleHoursEditor(cal, daysEl) {
  for (const [dow, label] of CAL_DAYS) {
    const intervals = (cal.businessDays && cal.businessDays[dow]) || [];
    const closed = intervals.length === 0;
    const card = document.createElement("div");
    card.className = "day-card";
    card.dataset.dow = dow;
    card.innerHTML = `
      <div class="day-card-header">
        <p class="day-label">${label} <span class="meta">(${dow})</span></p>
        <label class="closed-toggle">
          <input type="checkbox" class="closed" ${closed ? "checked" : ""} />
          Fermé
        </label>
      </div>
      <div class="day-intervals ${closed ? "disabled" : ""}">
        ${closed ? "" : intervals.map((iv, idx) => renderIntervalRow(iv, idx)).join("")}
      </div>
      <div class="day-card-actions">
        <button type="button" class="btn btn-default btn-sm add-interval" ${closed ? "disabled" : ""}>+ plage</button>
      </div>
    `;

    const closedCb = card.querySelector(".closed");
    const intervalsEl = card.querySelector(".day-intervals");
    const addBtn = card.querySelector(".add-interval");

    closedCb.addEventListener("change", () => {
      intervalsEl.classList.toggle("disabled", closedCb.checked);
      addBtn.disabled = closedCb.checked;
      if (closedCb.checked) {
        intervalsEl.innerHTML = "";
      } else if (!intervalsEl.querySelector(".interval-row")) {
        intervalsEl.insertAdjacentHTML(
          "beforeend",
          renderIntervalRow(
            { startTime: DEFAULT_INTERVAL_START, endTime: DEFAULT_INTERVAL_END },
            0
          )
        );
        wireIntervalButtons(card);
        attachTimePickersIn(intervalsEl);
      }
      syncBusinessDaysFromDom();
    });

    addBtn.addEventListener("click", () => {
      const n = intervalsEl.querySelectorAll(".interval-row").length;
      intervalsEl.insertAdjacentHTML(
        "beforeend",
        renderIntervalRow(
          { startTime: DEFAULT_INTERVAL_START, endTime: DEFAULT_INTERVAL_END },
          n
        )
      );
      wireIntervalButtons(card);
      attachTimePickersIn(intervalsEl);
      syncBusinessDaysFromDom();
    });

    wireIntervalButtons(card);
    attachTimePickersIn(card);
    daysEl.appendChild(card);
  }
}

function renderCalendar(forceFromServer) {
  const section = $("calendar-section");
  const cals = state.uccx.calendars || [];
  if (!cals.length) {
    section.classList.add("hidden");
    state.activeCalendar = null;
    return;
  }
  section.classList.remove("hidden");
  if (
    forceFromServer ||
    !state.activeCalendar ||
    String(state.activeCalendar.calId) !== String(cals[0].calId)
  ) {
    state.activeCalendar = structuredClone(cals[0]);
    state.activeCalendar.holidays = collapseHolidaysForUi(
      state.activeCalendar.holidays || []
    );
    state.activeCalendar.specialDays = structuredClone(
      state.activeCalendar.specialDays || []
    );
  } else {
    state.activeCalendar.name = cals[0].name;
    state.activeCalendar.timeZone = cals[0].timeZone;
    state.activeCalendar.paramName = cals[0].paramName;
    if (forceFromServer) {
      state.activeCalendar.holidays = collapseHolidaysForUi(cals[0].holidays || []);
      state.activeCalendar.specialDays = structuredClone(cals[0].specialDays || []);
    }
  }
  const cal = state.activeCalendar;
  cal.calendarType = normalizeCalType(cal.calendarType);
  if (!Array.isArray(cal.specialDays)) cal.specialDays = [];

  $("calendar-meta").textContent =
    `${cal.name || "calendrier"} (id ${cal.calId}) — ${cal.timeZone || ""} — param ${cal.paramName || ""}`;

  const typeSel = $("calendar-type");
  if (typeSel) typeSel.value = cal.calendarType;

  const days = $("calendar-days");
  days.innerHTML = "";
  if (cal.calendarType === "FULLTIME") {
    days.innerHTML = `<p class="calendar-fulltime-msg">Ouvert 24 h / 7 j — aucune plage à configurer.</p>`;
  } else if (cal.calendarType === "FIXEDHOURS") {
    renderFixedHoursEditor(cal, days);
  } else {
    renderFlexibleHoursEditor(cal, days);
  }
  renderHolidays();
  renderSpecialDays();
}

function wireIntervalButtons(card) {
  for (const btn of card.querySelectorAll(".remove-interval")) {
    btn.onclick = () => {
      const row = btn.closest(".interval-row");
      const intervalsEl =
        card.querySelector(".day-intervals") || card.querySelector(".fixed-intervals");
      if (!intervalsEl) return;
      row.remove();
      const remaining = intervalsEl.querySelectorAll(".interval-row");
      if (remaining.length === 0) {
        const closedCb = card.querySelector(".closed");
        if (closedCb) {
          closedCb.checked = true;
          intervalsEl.classList.add("disabled");
          const addBtn = card.querySelector(".add-interval");
          if (addBtn) addBtn.disabled = true;
          intervalsEl.innerHTML = "";
        } else {
          // Fixed Hours : garder au moins une plage
          intervalsEl.insertAdjacentHTML(
            "beforeend",
            renderIntervalRow(
              { startTime: DEFAULT_INTERVAL_START, endTime: DEFAULT_INTERVAL_END },
              0
            )
          );
          wireIntervalButtons(card);
          attachTimePickersIn(intervalsEl);
        }
      }
      syncBusinessDaysFromDom();
    };
  }
}

function holidayTypeSelect(kind, offset) {
  const opts = [
    `<option value="fixed" ${kind === "fixed" ? "selected" : ""}>Date fixe</option>`,
    `<option value="yearly" ${kind === "yearly" ? "selected" : ""}>Chaque année</option>`,
  ];
  for (const p of EASTER_PRESETS) {
    const val = `easter:${p.offset}`;
    const sel = kind === "easter" && Number(offset) === p.offset ? "selected" : "";
    opts.push(`<option value="${val}" ${sel}>${escapeHtml(p.label)}</option>`);
  }
  return `<select class="form-control" data-field="kind">${opts.join("")}</select>`;
}

function renderHolidays() {
  const body = $("calendar-holidays");
  body.innerHTML = "";
  const holidays = state.activeCalendar?.holidays || [];
  for (const [idx, h] of holidays.entries()) {
    const meta = inferKindFromHoliday(h);
    const display =
      meta.kind === "easter"
        ? isoToDisplay(h.date || nextEasterRelative(meta.offset), "fixed")
        : isoToDisplay(h.date, meta.kind);
    const dateDisabled = meta.kind === "easter" ? "disabled" : "";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input class="form-control" type="text" value="${escapeHtml(meta.label)}" data-field="label" /></td>
      <td>${holidayTypeSelect(meta.kind, meta.offset)}</td>
      <td><input class="form-control holiday-date" type="text" placeholder="jj/mm/aaaa" value="${escapeHtml(display)}" data-field="date" ${dateDisabled} /></td>
      <td><button type="button" class="btn btn-default btn-sm" data-idx="${idx}">Suppr.</button></td>
    `;
    const kindSel = tr.querySelector('[data-field="kind"]');
    const dateInput = tr.querySelector('[data-field="date"]');
    kindSel.addEventListener("change", () => {
      const v = kindSel.value;
      if (v.startsWith("easter:")) {
        const off = Number(v.split(":")[1]);
        dateInput.value = isoToDisplay(nextEasterRelative(off), "fixed");
        dateInput.disabled = true;
        const lab = tr.querySelector('[data-field="label"]');
        const preset = EASTER_PRESETS.find((p) => p.offset === off);
        if (preset && (!lab.value || lab.value === "Férié")) lab.value = preset.label;
      } else {
        dateInput.disabled = false;
        if (v === "yearly") {
          // garder jj/mm
          const iso = displayToIso(dateInput.value, "yearly");
          dateInput.value = isoToDisplay(iso, "yearly");
        }
      }
    });
    tr.querySelector("button").addEventListener("click", () => {
      syncHolidaysFromDom();
      state.activeCalendar.holidays.splice(idx, 1);
      renderHolidays();
    });
    body.appendChild(tr);
    if (meta.kind !== "easter") attachDtsel(dateInput);
  }
}

function syncHolidaysFromDom() {
  if (!state.activeCalendar) return;
  const rows = [...document.querySelectorAll("#calendar-holidays tr")];
  // Anti-wipe : DOM vide alors que l'état a déjà des fériés → ne pas écraser
  if (!rows.length && (state.activeCalendar.holidays || []).length) {
    return;
  }
  state.activeCalendar.holidays = rows.map((tr) => {
    const label = tr.querySelector('[data-field="label"]').value.trim();
    const kindVal = tr.querySelector('[data-field="kind"]').value;
    const display = tr.querySelector('[data-field="date"]').value.trim();
    let kind = "fixed";
    let offset = null;
    if (kindVal === "yearly") kind = "yearly";
    else if (kindVal.startsWith("easter:")) {
      kind = "easter";
      offset = Number(kindVal.split(":")[1]);
    }
    let date;
    if (kind === "easter") {
      date = nextEasterRelative(offset);
    } else {
      date = displayToIso(display, kind);
      // UCCX refuse toute date passée sur PUT — avancer MM-DD
      date = ensureFutureIso(date);
    }
    return {
      name: buildHolidayName(kind, offset, label),
      date,
    };
  });
}

function holidayDedupKey(h) {
  const meta = parseHolidayMeta(h.name || "");
  if (meta.kind === "easter") return `easter:${meta.offset}`;
  if (meta.kind === "yearly") {
    return `yearly:${stripHolidayYearSuffix(meta.label || "").toLowerCase()}`;
  }
  return `fixed:${(h.date || "").trim()}:${(meta.label || "").trim().toLowerCase()}`;
}

function addLuxembourgHolidays() {
  if (!state.activeCalendar) return;
  syncHolidaysFromDom();
  if (!state.activeCalendar.holidays) state.activeCalendar.holidays = [];
  const existing = new Set(state.activeCalendar.holidays.map(holidayDedupKey));
  let added = 0;
  for (const spec of LU_HOLIDAYS) {
    const name = buildHolidayName(spec.kind, spec.offset, spec.label);
    const date =
      spec.kind === "easter"
        ? nextEasterRelative(spec.offset)
        : nextYearlyOccurrence(spec.mm, spec.dd);
    const entry = { name, date };
    const key = holidayDedupKey(entry);
    if (existing.has(key)) continue;
    existing.add(key);
    state.activeCalendar.holidays.push(entry);
    added += 1;
  }
  renderHolidays();
  if (added === 0) {
    setMsg("calendar-msg", "Fériés Luxembourg déjà présents", "meta");
  } else {
    setMsg("calendar-msg", `${added} férié(s) Luxembourg ajouté(s) — enregistrez pour pousser vers UCCX`, "ok");
  }
}

const MAX_SD_INTERVALS = 3;

function displayIntervalName(name, idx) {
  const n = String(name || "").trim();
  if (!n || /^Time Range \d+$/i.test(n) || /^Plage \d+$/i.test(n)) return "";
  return n;
}

function renderSpecialDayIntervalRow(iv, idx) {
  const ivName = displayIntervalName(iv.name, idx);
  return `
    <div class="interval-row special-day-interval" data-idx="${idx}">
      <input class="form-control" type="text" data-field="iv-name" placeholder="Plage ${idx + 1}" value="${escapeHtml(ivName)}" />
      <input type="text" class="form-control time-picker-24h" data-field="iv-start" placeholder="${DEFAULT_INTERVAL_START}" value="${escapeHtml(formatTime24(iv.startTime, DEFAULT_INTERVAL_START))}" />
      <input type="text" class="form-control time-picker-24h" data-field="iv-end" placeholder="${DEFAULT_INTERVAL_END}" value="${escapeHtml(formatTime24(iv.endTime, DEFAULT_INTERVAL_END))}" />
      <button type="button" class="btn btn-default btn-sm remove-sd-interval" title="Supprimer la plage">×</button>
    </div>
  `;
}

function renderSpecialDays() {
  const body = $("calendar-special-days");
  if (!body) return;
  body.innerHTML = "";
  const days = state.activeCalendar?.specialDays || [];
  for (const [idx, sd] of days.entries()) {
    const card = document.createElement("div");
    card.className = "special-day-card";
    card.dataset.idx = String(idx);
    const rawName = (sd.name || "").trim();
    const meta = rawName
      ? inferKindFromHoliday(sd)
      : { kind: "fixed", offset: null, label: "", isFollow: false };
    const intervals = (
      sd.intervals || [{ name: "", startTime: DEFAULT_INTERVAL_START, endTime: DEFAULT_INTERVAL_END }]
    ).slice(
      0,
      MAX_SD_INTERVALS
    );
    const display =
      meta.kind === "easter"
        ? isoToDisplay(sd.date || nextEasterRelative(meta.offset), "fixed")
        : isoToDisplay(sd.date, meta.kind);
    const dateDisabled = meta.kind === "easter" ? "disabled" : "";
    card.innerHTML = `
      <div class="special-day-head">
        <input class="form-control" type="text" data-field="sd-label" placeholder="Nom du jour" value="${escapeHtml(meta.label || "")}" />
        ${holidayTypeSelect(meta.kind, meta.offset).replace('data-field="kind"', 'data-field="sd-kind"')}
        <input class="form-control special-day-date" type="text" data-field="sd-date" placeholder="jj/mm/aaaa" value="${escapeHtml(display)}" ${dateDisabled} />
        <button type="button" class="btn btn-default btn-sm remove-special-day">Suppr.</button>
      </div>
      <div class="special-day-intervals">
        ${intervals.map((iv, i) => renderSpecialDayIntervalRow(iv, i)).join("")}
      </div>
      <button type="button" class="btn btn-default btn-sm add-sd-interval" ${intervals.length >= MAX_SD_INTERVALS ? "disabled" : ""}>+ Plage (max ${MAX_SD_INTERVALS})</button>
    `;
    const kindSel = card.querySelector('[data-field="sd-kind"]');
    const dateInput = card.querySelector('[data-field="sd-date"]');
    const labelInput = card.querySelector('[data-field="sd-label"]');
    kindSel.addEventListener("change", () => {
      const v = kindSel.value;
      if (v.startsWith("easter:")) {
        const off = Number(v.split(":")[1]);
        dateInput.value = isoToDisplay(nextEasterRelative(off), "fixed");
        dateInput.disabled = true;
        const preset = EASTER_PRESETS.find((p) => p.offset === off);
        if (preset && (!labelInput.value || labelInput.value === "Jour special")) {
          labelInput.value = preset.label;
        }
      } else {
        dateInput.disabled = false;
        if (v === "yearly") {
          const iso = displayToIso(dateInput.value, "yearly");
          dateInput.value = isoToDisplay(iso, "yearly");
        }
      }
    });
    card.querySelector(".remove-special-day").onclick = () => {
      syncSpecialDaysFromDom();
      state.activeCalendar.specialDays.splice(idx, 1);
      renderSpecialDays();
    };
    card.querySelector(".add-sd-interval").onclick = () => {
      syncSpecialDaysFromDom();
      const list = state.activeCalendar.specialDays[idx].intervals || [];
      if (list.length >= MAX_SD_INTERVALS) return;
      list.push({ name: "", startTime: DEFAULT_INTERVAL_START, endTime: DEFAULT_INTERVAL_END });
      state.activeCalendar.specialDays[idx].intervals = list;
      renderSpecialDays();
    };
    for (const btn of card.querySelectorAll(".remove-sd-interval")) {
      btn.onclick = () => {
        const row = btn.closest(".special-day-interval");
        const wrap = card.querySelector(".special-day-intervals");
        if (wrap.querySelectorAll(".special-day-interval").length <= 1) return;
        row.remove();
        const addBtn = card.querySelector(".add-sd-interval");
        if (addBtn) {
          addBtn.disabled =
            wrap.querySelectorAll(".special-day-interval").length >= MAX_SD_INTERVALS;
        }
      };
    }
    if (meta.kind !== "easter") attachDtsel(dateInput);
    attachTimePickersIn(card);
    body.appendChild(card);
  }
}

function syncSpecialDaysFromDom() {
  if (!state.activeCalendar) return;
  const cards = [...document.querySelectorAll("#calendar-special-days .special-day-card")];
  // Liste vide = suppression intentionnelle (y compris le dernier)
  state.activeCalendar.specialDays = cards.map((card) => {
    const label = card.querySelector('[data-field="sd-label"]').value.trim();
    const kindVal = card.querySelector('[data-field="sd-kind"]').value;
    const display = card.querySelector('[data-field="sd-date"]').value.trim();
    let kind = "fixed";
    let offset = null;
    if (kindVal === "yearly") kind = "yearly";
    else if (kindVal.startsWith("easter:")) {
      kind = "easter";
      offset = Number(kindVal.split(":")[1]);
    }
    let date;
    if (kind === "easter") {
      date = nextEasterRelative(offset);
    } else {
      date = ensureFutureIso(displayToIso(display, kind));
    }
    const intervals = [...card.querySelectorAll(".special-day-interval")]
      .slice(0, MAX_SD_INTERVALS)
      .map((row, i) => {
        const raw = row.querySelector('[data-field="iv-name"]').value.trim();
        return {
          name: raw ? sanitizeDayName(raw, `Plage ${i + 1}`) : "",
          startTime: formatTime24(
            row.querySelector('[data-field="iv-start"]').value.trim(),
            DEFAULT_INTERVAL_START
          ),
          endTime: formatTime24(
            row.querySelector('[data-field="iv-end"]').value.trim(),
            DEFAULT_INTERVAL_END
          ),
        };
      });
    return {
      name: buildHolidayName(kind, offset, label || "Jour special"),
      date,
      intervals,
    };
  });
}

function calendarEntryLabel(name, fallback) {
  const meta = parseHolidayMeta(name || "");
  const label = (meta.label || name || fallback || "").trim();
  return label || fallback || "Entrée";
}

/** UCCX : une date unique pour fériés + jours spéciaux. */
function timeToMinutes(t) {
  const hm = formatTime24(t, "00:00");
  const [h, m] = hm.split(":").map((x) => Number(x));
  return h * 60 + m;
}

function validateIntervalList(intervals, contextLabel) {
  const parsed = [];
  for (let i = 0; i < (intervals || []).length; i++) {
    const iv = intervals[i];
    const name = (iv.name || `Plage ${i + 1}`).trim();
    const startS = formatTime24(iv.startTime, DEFAULT_INTERVAL_START);
    const endS = formatTime24(iv.endTime, DEFAULT_INTERVAL_END);
    if (!iv.startTime && !iv.endTime) continue;
    const start = timeToMinutes(startS);
    const end = timeToMinutes(endS);
    if (start >= end) {
      throw new Error(
        `${contextLabel} — « ${name} » : l'heure de fin (${endS}) doit être après le début (${startS}).`
      );
    }
    parsed.push({ start, end, name });
  }
  parsed.sort((a, b) => a.start - b.start);
  for (let i = 1; i < parsed.length; i++) {
    if (parsed[i].start < parsed[i - 1].end) {
      throw new Error(
        `${contextLabel} — chevauchement entre « ${parsed[i - 1].name} » et « ${parsed[i].name} » ` +
          "(plages disjointes requises)."
      );
    }
  }
}

function validateCalendarIntervals(payload) {
  if (payload.calendarType === "FULLTIME") return;
  const bd = payload.businessDays || {};
  for (const [dow, label] of CAL_DAYS) {
    const ivs = bd[dow] || [];
    if (ivs.length) validateIntervalList(ivs, `${label} (${dow})`);
  }
  for (const sd of payload.specialDays || []) {
    validateIntervalList(
      sd.intervals || [],
      `Jour spécial « ${calendarEntryLabel(sd.name, "jour spécial")} »`
    );
  }
}

function validateCalendarDateUniqueness(holidays, specialDays) {
  const by = new Map();
  const add = (iso, text) => {
    if (!iso || !/^\d{4}-\d{2}-\d{2}$/.test(iso)) return;
    if (!by.has(iso)) by.set(iso, []);
    by.get(iso).push(text);
  };
  for (const h of holidays || []) {
    add(h.date, `Férié « ${calendarEntryLabel(h.name, "férié")} »`);
  }
  for (const sd of specialDays || []) {
    add(sd.date, `Jour spécial « ${calendarEntryLabel(sd.name, "jour spécial")} »`);
  }
  const conflicts = [...by.entries()].filter(([, labels]) => labels.length > 1);
  if (!conflicts.length) return;
  const detail = conflicts
    .map(([d, labels]) => `${d} : ${labels.join(", ")}`)
    .join(" ; ");
  throw new Error(
    `UCCX n'autorise qu'une entrée par date (fériés et jours spéciaux). Conflit : ${detail}`
  );
}

function collectCalendarPayload() {
  syncBusinessDaysFromDom();
  syncHolidaysFromDom();
  syncSpecialDaysFromDom();
  const calType = normalizeCalType(state.activeCalendar?.calendarType);
  return {
    calendarType: calType,
    businessDays:
      calType === "FULLTIME"
        ? emptyBusinessDays()
        : state.activeCalendar?.businessDays || {},
    holidays: state.activeCalendar?.holidays || [],
    specialDays: state.activeCalendar?.specialDays || [],
  };
}

function renderTriggers() {
  const body = $("triggers-body");
  const empty = $("triggers-empty");
  body.innerHTML = "";
  const triggers = state.uccx.triggers || [];
  if (!triggers.length) {
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  for (const t of triggers) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(t.directoryNumber)}</td>
      <td>${escapeHtml(t.deviceName)}</td>
      <td>${escapeHtml(t.display)}</td>
      <td>${t.triggerEnabled == null ? "—" : t.triggerEnabled ? "oui" : "non"}</td>
      <td>${escapeHtml(t.maxNumOfSessions)}</td>
      <td>${escapeHtml(t.locale)}</td>
    `;
    body.appendChild(tr);
  }
}

async function refreshAppFromUccx({ clearPending = false } = {}) {
  if (!state.currentApp) throw new Error("Aucune app sélectionnée");
  state.promptStatusCache = null;
  state.promptStatusInflight = null;
  if (clearPending) clearAllPromptPending();
  stopPromptAudio();
  const data = await api(
    `/api/apps/${encodeURIComponent(state.currentApp)}/uccx`
  );
  state.uccx = data;
  normalizeUccxPlayablesInState();
  state.promptLanguages = [];
  renderMeta();
  renderCalendar(true);
  await renderPrompts();
  renderParams();
  renderTriggers();
  return data;
}

async function selectApp(name) {
  state.currentApp = name;
  state.activeCalendar = null;
  state.promptLanguages = [];
  state.promptStatusCache = null;
  state.promptStatusInflight = null;
  clearAllPromptPending();
  stopPromptAudio();
  $("empty-state").classList.add("hidden");
  $("editor").classList.remove("hidden");
  await refreshAppFromUccx({ clearPending: false });
  await loadApps();
  void prefetchElevenlabsVoices();
}

async function putUccx(partial, msgId, okText) {
  setMsg(msgId, "Enregistrement…", "meta");
  try {
    const data = await api(`/api/apps/${encodeURIComponent(state.currentApp)}/uccx`, {
      method: "PUT",
      json: partial,
    });
    state.uccx = data;
    normalizeUccxPlayablesInState();
    renderMeta();
    renderPrompts();
    renderParams();
    renderTriggers();
    if (data.calendars?.length) {
      state.uccx.calendars = data.calendars;
    }
    setMsg(msgId, okText, "ok");
  } catch (err) {
    setMsg(msgId, err.message, "error");
  }
}

async function saveMeta() {
  await putUccx(
    {
      description: $("app-description").value,
      maxsession: Number($("app-maxsession").value),
    },
    "meta-msg",
    "Application enregistrée"
  );
}

async function saveParams() {
  await putUccx({ params: collectParams() }, "params-msg", "Paramètres enregistrés");
}

async function savePrompts() {
  setMsg("prompts-msg", "Enregistrement des prompts…", "meta");
  try {
    const pendingEntries = [];
    for (const [paramName, byLang] of Object.entries(promptPending)) {
      for (const [lang, pend] of Object.entries(byLang || {})) {
        if (pend?.localFile || pend?.relpath) pendingEntries.push({ paramName, lang, pend });
      }
    }
    for (const { paramName, lang, pend } of pendingEntries) {
      let data = null;
      if (pend.localFile) {
        data = await uploadPrompt(paramName, pend.localFile, {
          lang,
          writeDefault: false,
          overwrite: true,
          silent: true,
          transcript: pend.transcriptText || "",
          source: pend.transcriptSource || "",
        });
      } else if (pend.relpath) {
        data = await assignPrompt(paramName, lang, pend.relpath, pend.sourceLang, {
          silent: true,
          transcript: pend.transcriptText || "",
          source: pend.transcriptSource || "",
        });
      }
      clearPending(paramName, lang);
      if (data?.transcript?.text) {
        transcriptCache[transcriptCacheKey(paramName, lang)] = {
          text: data.transcript.text,
          source: data.transcript.source === "tts" ? "tts" : "stt",
          stt_enabled: data.stt_enabled !== false,
        };
      } else if (pend.transcriptText) {
        transcriptCache[transcriptCacheKey(paramName, lang)] = {
          text: pend.transcriptText,
          source: pend.transcriptSource === "tts" ? "tts" : "stt",
          stt_enabled: getCachedSttEnabled(paramName, lang),
        };
      }
    }
    await putUccx(
      { params: collectPrompts() },
      "prompts-msg",
      pendingEntries.length
        ? `Prompts enregistrés (${pendingEntries.length} sélection(s))`
        : "Prompts enregistrés"
    );
    await renderPrompts();
  } catch (err) {
    setMsg("prompts-msg", err.message, "error");
  }
}

async function saveCalendar() {
  if (!state.activeCalendar) return;
  setMsg("calendar-msg", "Enregistrement…", "meta");
  try {
    const payload = collectCalendarPayload();
    if (payload.calendarType !== "FULLTIME") {
      const open = Object.values(payload.businessDays || {}).some((ivs) => (ivs || []).length);
      if (!open) {
        throw new Error("Cochez au moins un jour avec une plage horaire");
      }
    }
    for (const h of payload.holidays) {
      if (!h.date) throw new Error(`Date invalide pour « ${h.name || "férié"} »`);
    }
    for (const sd of payload.specialDays) {
      if (!sd.date) throw new Error(`Date invalide pour le jour spécial « ${sd.name || ""} »`);
      if (!(sd.intervals || []).some((iv) => iv.startTime && iv.endTime)) {
        throw new Error(`Au moins une plage horaire pour « ${sd.name || "jour spécial"} »`);
      }
    }
    validateCalendarDateUniqueness(payload.holidays, payload.specialDays);
    validateCalendarIntervals(payload);
    const cal = await api(
      `/api/apps/${encodeURIComponent(state.currentApp)}/calendar/${encodeURIComponent(state.activeCalendar.calId)}`,
      { method: "PUT", json: payload }
    );
    const collapsed = {
      ...cal,
      holidays: collapseHolidaysForUi(cal.holidays || []),
      specialDays: cal.specialDays || [],
    };
    state.activeCalendar = collapsed;
    if (state.uccx.calendars?.length) {
      state.uccx.calendars[0] = {
        ...cal,
        holidays: cal.holidays || [],
        specialDays: cal.specialDays || [],
        paramName: state.uccx.calendars[0].paramName,
      };
    }
    renderCalendar(true);
    setMsg("calendar-msg", "Calendrier mis à jour", "ok");
  } catch (err) {
    const msg = String(err.message || err);
    const cleaned = msg.replace(/^Mise à jour calendrier échouée:\s*/i, "");
    setMsg("calendar-msg", cleaned, "error");
  }
}

function promptErrorDetail(data) {
  const d = data?.detail;
  if (!d) return "Échec";
  if (typeof d === "string") return d;
  if (d.message) return d.message;
  return JSON.stringify(d);
}

async function uploadPrompt(paramName, fileOrInput, opts = {}) {
  const file = fileOrInput?.files?.[0] || fileOrInput;
  if (!file || !state.currentApp) return null;
  const lang = opts.lang || state.promptLangDefault || "default";
  const writeDefault = !!opts.writeDefault;
  let overwrite = !!opts.overwrite;
  const silent = !!opts.silent;
  if (opts.confirmOverwrite && overwrite) {
    if (!confirm(`Remplacer le fichier pour « ${lang} » ?`)) {
      if (fileOrInput?.value !== undefined) fileOrInput.value = "";
      setMsg("prompts-msg", "Upload annulé", "warn");
      return null;
    }
  }
  if (!silent) {
    setMsg("prompts-msg", `Conversion + upload ${paramName} (${lang})…`, "meta");
  }

  const doUpload = async (ow) => {
    const fd = new FormData();
    fd.append("param_name", paramName);
    fd.append("file", file);
    fd.append("lang", lang);
    fd.append("overwrite", ow ? "true" : "false");
    fd.append("write_default", writeDefault ? "true" : "false");
    if (opts.transcript && (opts.source === "tts" || opts.source === "stt")) {
      fd.append("transcript", opts.transcript);
      fd.append("source", opts.source);
    }
    const res = await fetch(`/api/apps/${encodeURIComponent(state.currentApp)}/prompts`, {
      method: "POST",
      headers: { "X-UCCX-Token": state.token },
      body: fd,
    });
    const data = await res.json().catch(() => ({}));
    return { res, data };
  };

  let { res, data } = await doUpload(overwrite);
  if (res.status === 409 && data?.detail?.exists) {
    const langs = (data.detail.languages || []).join(", ");
    if (!confirm(`Le fichier existe déjà pour : ${langs}.\nÉcraser ?`)) {
      if (fileOrInput?.value !== undefined) fileOrInput.value = "";
      setMsg("prompts-msg", "Upload annulé", "warn");
      return null;
    }
    ({ res, data } = await doUpload(true));
  }
  if (!res.ok) throw new Error(promptErrorDetail(data) || "Upload échoué");

  const playable = normalizePlayableDisplay(data.prompt || "");
  state.promptStatusCache = null;
  state.promptStatusInflight = null;
  promptFilesAllCache = null;
  if (data.transcript?.text) {
    transcriptCache[transcriptCacheKey(paramName, lang)] = {
      text: data.transcript.text,
      source: data.transcript.source === "tts" ? "tts" : "stt",
      stt_enabled: data.stt_enabled !== false,
    };
  } else if (opts.transcript && opts.source === "tts") {
    transcriptCache[transcriptCacheKey(paramName, lang)] = {
      text: opts.transcript,
      source: "tts",
      stt_enabled: data.stt_enabled !== false,
    };
  } else {
    const prev = transcriptCache[transcriptCacheKey(paramName, lang)] || {};
    transcriptCache[transcriptCacheKey(paramName, lang)] = {
      ...prev,
      text: "",
      source: null,
      stt_enabled: data.stt_enabled !== false,
    };
  }

  if (data.application) {
    state.uccx = data.application;
    normalizeUccxPlayablesInState();
  } else if (!silent) {
    try {
      await refreshAppFromUccx();
    } catch (err) {
      console.warn("refresh after upload", err);
    }
  }
  setParamPlayableLocal(paramName, playable);
  if (!silent) {
    await renderPrompts();
    renderParams();
    const langs = (data.languages || [lang]).join(", ");
    setMsg(
      "prompts-msg",
      `${paramName} → ${playable} [${langs}] (${data.converted || "converti"})`,
      "ok"
    );
  }
  if (fileOrInput?.value !== undefined) fileOrInput.value = "";
  return data;
}

async function assignPrompt(paramName, targetLang, relpath, sourceLang, opts = {}) {
  const src = sourceLang || targetLang;
  const silent = !!opts.silent;
  if (!silent) {
    setMsg(
      "prompts-msg",
      `Assignation ${paramName} ← ${src}/${relpath} → ${targetLang}…`,
      "meta"
    );
  }
  const data = await api(`/api/apps/${encodeURIComponent(state.currentApp)}/prompts/assign`, {
    method: "POST",
    json: {
      param_name: paramName,
      lang: targetLang,
      relpath,
      source_lang: src,
      transcript: opts.transcript || "",
      transcript_source: opts.source || "",
    },
  });
  const playable = normalizePlayableDisplay(data.prompt || "");
  state.promptStatusCache = null;
  state.promptStatusInflight = null;
  promptFilesAllCache = null;
  {
    const prev = transcriptCache[transcriptCacheKey(paramName, targetLang)] || {};
    transcriptCache[transcriptCacheKey(paramName, targetLang)] = {
      ...prev,
      text: "",
      source: null,
      stt_enabled: data.stt_enabled !== false,
    };
  }
  if (data.application) {
    state.uccx = data.application;
    normalizeUccxPlayablesInState();
  }
  setParamPlayableLocal(paramName, playable);
  if (!silent) {
    await renderPrompts();
    renderParams();
    const seed = data.seed ? ` — ${data.seed}` : "";
    setMsg("prompts-msg", `${paramName} → ${playable}${seed}`, "ok");
  }
  return data;
}

async function deletePromptParam(paramName, lang) {
  setMsg("prompts-msg", `Suppression ${paramName} (${lang})…`, "meta");
  stopPromptAudio();
  const data = await api(
    `/api/apps/${encodeURIComponent(state.currentApp)}/prompts?param_name=${encodeURIComponent(paramName)}&lang=${encodeURIComponent(lang)}`,
    { method: "DELETE" }
  );
  state.promptStatusCache = null;
  state.promptStatusInflight = null;
  promptFilesAllCache = null;
  delete transcriptCache[transcriptCacheKey(paramName, lang)];
  if (data.application) {
    state.uccx = data.application;
    normalizeUccxPlayablesInState();
  }
  setParamPlayableLocal(paramName, normalizePlayableDisplay(data.value || "P[]"));
  await renderPrompts();
  renderParams();
  const extra = data.file ? ` (${data.file})` : "";
  setMsg("prompts-msg", `${paramName} → ${normalizePlayableDisplay(data.value)}${extra}`, "ok");
}

async function boot() {
  $("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("login-error");
    err.classList.add("hidden");
    const body = new URLSearchParams();
    body.set("username", $("username").value);
    body.set("password", $("password").value);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body,
      });
      const raw = await res.text();
      let data = null;
      try {
        data = raw ? JSON.parse(raw) : null;
      } catch {
        throw new Error(
          res.ok
            ? "Réponse login invalide"
            : `Échec connexion (HTTP ${res.status}) — serveur saturé ou indisponible`
        );
      }
      if (!res.ok) throw new Error(data?.detail || "Échec connexion");
      state.token = data.access_token;
      localStorage.setItem("uccx_token", state.token);
      await afterLogin();
    } catch (ex) {
      err.textContent = ex.message;
      err.classList.remove("hidden");
    }
  });

  $("logout-btn").addEventListener("click", () => logout());
  $("runas-stop-btn")?.addEventListener("click", async () => {
    try {
      await stopRunAs();
    } catch (err) {
      alert(err.message || "Impossible de quitter RunAs");
    }
  });
  $("save-meta-btn").addEventListener("click", saveMeta);
  $("save-params-btn").addEventListener("click", saveParams);
  $("save-prompts-btn").addEventListener("click", savePrompts);
  $("public-tts-engine")?.addEventListener("change", async () => {
    const sel = $("public-tts-engine");
    const eng = sel?.value || "piper";
    setMsg("public-tts-engine-msg", "Enregistrement…", "meta");
    try {
      const data = await savePublicTtsSettings(eng);
      if (sel && data.engine) sel.value = data.engine;
      setMsg(
        "public-tts-engine-msg",
        `Moteur API UCCX : ${data.engine || eng}`,
        "ok"
      );
      await refreshElevenlabsUsage();
    } catch (err) {
      setMsg("public-tts-engine-msg", err.message, "error");
      await loadPublicTtsSettings();
    }
  });
  $("add-prompt-lang-btn")?.addEventListener("click", async () => {
    const input = $("prompt-lang-new");
    const code = (input?.value || "").trim();
    if (!code) {
      setMsg("prompts-msg", "Indiquez un code langue (ex. it_IT)", "warn");
      input?.focus();
      return;
    }
    if (!state.currentApp) return;
    setMsg("prompts-msg", `Création langue ${code}…`, "meta");
    try {
      const res = await addPromptLanguage(code);
      if (input) input.value = "";
      if (res.languages?.length) state.promptLanguages = res.languages;
      state.promptStatusCache = null;
      clearTtsCaches();
      await renderPrompts();
      setMsg(
        "prompts-msg",
        res.status === "already-exists"
          ? `Langue ${res.lang} déjà présente`
          : `Langue ${res.lang} créée`,
        "ok"
      );
    } catch (err) {
      setMsg("prompts-msg", err.message, "error");
    }
  });
  $("prompt-lang-new")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      $("add-prompt-lang-btn")?.click();
    }
  });
  $("save-calendar-btn").addEventListener("click", saveCalendar);
  $("calendar-type").addEventListener("change", () => {
    if (!state.activeCalendar) return;
    syncBusinessDaysFromDom();
    syncHolidaysFromDom();
    convertCalendarType(state.activeCalendar, $("calendar-type").value);
    renderCalendar(false);
  });
  $("add-holiday").addEventListener("click", () => {
    if (!state.activeCalendar) return;
    syncHolidaysFromDom();
    if (!state.activeCalendar.holidays) state.activeCalendar.holidays = [];
    state.activeCalendar.holidays.push({ name: "Ferie", date: "" });
    renderHolidays();
  });
  $("add-lu-holidays").addEventListener("click", () => addLuxembourgHolidays());
  $("add-special-day").addEventListener("click", () => {
    if (!state.activeCalendar) return;
    syncSpecialDaysFromDom();
    if (!state.activeCalendar.specialDays) state.activeCalendar.specialDays = [];
    state.activeCalendar.specialDays.push({
      name: "",
      date: "",
      intervals: [{ name: "", startTime: DEFAULT_INTERVAL_START, endTime: DEFAULT_INTERVAL_END }],
    });
    renderSpecialDays();
  });

  document.addEventListener("click", (e) => {
    if (e.target.closest(".prompt-browse-wrap, .prompt-tts-wrap")) return;
    closeAllPromptBrowsePanels();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeAllPromptBrowsePanels();
  });

  if (state.token) {
    try {
      await afterLogin();
    } catch {
      showLogin();
    }
  } else {
    showLogin();
  }
}

async function afterLogin() {
  state.me = await api("/api/auth/me");
  showApp();
  updateRunAsBanner(state.me);
  const whoLabel = state.me.impersonating
    ? `${state.me.username} (${state.me.role}) · RunAs`
    : `${state.me.username} (${state.me.role})`;
  must$("whoami").textContent = whoLabel;
  const tasks = [loadPublicTtsSettings()];
  if (state.me.role === "admin" || state.me.can_access_apps !== false) {
    tasks.unshift(loadApps());
  }
  await Promise.all(tasks);
  if (typeof updateNavVisibility === "function") updateNavVisibility();
  if (state.currentApp) void prefetchElevenlabsVoices();
}

boot();
