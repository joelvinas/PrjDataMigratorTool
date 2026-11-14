"use strict";
/* Airtable migration control plane: shared front-end code (loaded by every page). No frameworks. All data is inserted with
   textContent (never innerHTML) so table or field names cannot inject markup. */

const $ = (id) => document.getElementById(id);
const EXPECTED_SERVER_VERSION = "1.14"; // must match APP_VERSION in server.py
const state = { ready: false, status: null, schema: [], pollTimer: null, lastJobId: null, lastJobStatus: null };

function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.appendChild(typeof kid === "string" || typeof kid === "number" ? document.createTextNode(String(kid)) : kid);
  }
  return n;
}
const clear = (n) => { while (n.firstChild) n.removeChild(n.firstChild); };
const fmt = (n) => (n === null || n === undefined ? "" : Number(n).toLocaleString());

function setMsg(id, text, kind) {
  const n = $(id);
  n.textContent = text || "";
  n.className = "msg" + (kind ? " " + kind : "");
}
function showGlobalError(text) {
  const n = $("globalError");
  n.textContent = text || "";
  n.hidden = !text;
}

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (method === "POST") {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body || {});
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new Error("Could not reach the local server. Is python server.py still running?");
  }
  let data = {};
  try { data = await res.json(); } catch (e) { /* non-JSON error */ }
  if (!res.ok) {
    const err = new Error(data.error || ("Request failed (" + res.status + ")"));
    if (Array.isArray(data.problems)) err.problems = data.problems;
    throw err;
  }
  return data;
}

/* ---------- shared: formatting ---------- */
const fmtBytes = (n) => (n >= 1073741824 ? (n / 1073741824).toFixed(2) + " GB" : n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : n >= 1024 ? (n / 1024).toFixed(1) + " KB" : n + " B");

/* ---------- base picker: dropdown from config.json, typing still allowed ---------- */
let baseChoices = [];   // what the Base ID picker offers: config.json bases plus any base already in the database
let cfgBases = [];      // bases listed in config.json (the only place a base name comes from)
let baseActive = -1;
/* One label for a base everywhere: "Name - ID" when config.json knows the name, otherwise just the ID. */
function baseLabel(id) {
  const c = cfgBases.find((b) => b.baseId === id);
  return c && c.name ? c.name + " - " + id : id;
}
function refreshBaseChoices() {
  const inDb = (state.schema || []).map((b) => b.base_id).filter((id) => !cfgBases.some((b) => b.baseId === id));
  baseChoices = cfgBases.concat(inDb.map((id) => ({ name: "", baseId: id, fromDb: true })));
}
function baseMatches(filter) {
  const f = (filter || "").trim().toLowerCase();
  return f ? baseChoices.filter((b) => b.baseId.toLowerCase().includes(f) || (b.name || "").toLowerCase().includes(f)) : baseChoices;
}
function openBaseMenu(filter) {
  const menu = $("baseMenu"), list = baseMatches(filter);
  clear(menu);
  baseActive = -1;
  if (!baseChoices.length) {
    menu.appendChild(el("li", { class: "combo-empty", text: "No bases listed in config.json or the database. Type a base ID instead." }));
  } else if (!list.length) {
    menu.appendChild(el("li", { class: "combo-empty", text: "No configured base matches. The text you typed will be used as the base ID." }));
  }
  list.forEach((b, i) => {
    const li = el("li", { role: "option", "data-id": b.baseId },
      el("span", { class: "combo-name", text: b.name || (b.fromDb ? "(in database, not in config.json)" : "(no name)") }), el("code", { text: b.baseId }));
    li.addEventListener("mousedown", (ev) => { ev.preventDefault(); chooseBase(b.baseId); });
    menu.appendChild(li);
  });
  menu.hidden = false;
  $("baseId").setAttribute("aria-expanded", "true");
}
function closeBaseMenu() {
  $("baseMenu").hidden = true;
  $("baseId").setAttribute("aria-expanded", "false");
  baseActive = -1;
}
function chooseBase(id) {
  $("baseId").value = id;
  closeBaseMenu();
  $("baseId").dispatchEvent(new Event("input", { bubbles: true }));
}
function moveBaseActive(step) {
  const items = Array.from($("baseMenu").querySelectorAll("li[data-id]"));
  if (!items.length) return;
  baseActive = (baseActive + step + items.length) % items.length;
  items.forEach((li, i) => li.classList.toggle("active", i === baseActive));
  items[baseActive].scrollIntoView({ block: "nearest" });
}
function initBaseCombo() {
  const inp = $("baseId");
  $("baseToggle").addEventListener("click", () => { if ($("baseMenu").hidden) { openBaseMenu(""); inp.focus(); } else closeBaseMenu(); });
  inp.addEventListener("focus", () => { if (!inp.value) openBaseMenu(""); });
  inp.addEventListener("input", () => { saveBase(inp.value.trim()); openBaseMenu(inp.value); });
  inp.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown") { ev.preventDefault(); if ($("baseMenu").hidden) openBaseMenu(""); moveBaseActive(1); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); moveBaseActive(-1); }
    else if (ev.key === "Enter" && !$("baseMenu").hidden && baseActive >= 0) {
      ev.preventDefault();
      const li = $("baseMenu").querySelectorAll("li[data-id]")[baseActive];
      if (li) chooseBase(li.getAttribute("data-id"));
    } else if (ev.key === "Escape") closeBaseMenu();
  });
  document.addEventListener("mousedown", (ev) => { if (!$("baseCombo").contains(ev.target)) closeBaseMenu(); });
}

/* ---------- shared: remembered base choice ----------
   Only the base ID (metadata) is kept, in sessionStorage so it carries from page to page in this tab.
   Never the token, never record values. Every access is wrapped: storage can be blocked or empty. */
const BASE_KEY = "migrationControl.baseId";
function saveBase(id) { try { sessionStorage.setItem(BASE_KEY, id || ""); } catch (e) { /* storage unavailable: the page still works */ } }
function savedBase() { try { return sessionStorage.getItem(BASE_KEY) || ""; } catch (e) { return ""; } }

/* ---------- shared: left navigation ----------
   One list of pages. A page with no href is a planned slot (shown, not a link). The log page is pinned to the bottom. */
const NAV_PAGES = [
  { key: "setup", href: "/setup", label: "Setup", sub: "Database, token, base" },
  { key: "stage1", href: "/stage1", label: "Stage 1: Data", sub: "Steps 3 to 6" },
  { key: "stage2", href: "/stage2", label: "Stage 2: Forms", sub: "Form recorder" },
  { key: "stage3", href: "/stage3", label: "Stage 3: Automations", sub: "Automation recorder" },
  { key: "names", href: "/names", label: "Field names", sub: "Naming rules and review" },
  { key: "stage4", label: "Stage 4: Map and load", sub: "Planned" },
];
const NAV_BOTTOM = [{ key: "log", href: "/log", label: "API call log", sub: "Audit trail" }];

function renderNav(current) {
  const nav = $("sidenav");
  if (!nav) return;
  clear(nav);
  const item = (p) => {
    const body = [el("span", { class: "nav-label", text: p.label }), el("span", { class: "nav-sub", text: p.sub })];
    if (!p.href) return el("li", {}, el("span", { class: "nav-link planned", "aria-disabled": "true" }, body));
    return el("li", {}, el("a", { class: "nav-link" + (p.key === current ? " current" : ""), href: p.href, "aria-current": p.key === current ? "page" : false }, body));
  };
  nav.appendChild(el("ul", { class: "nav-main" }, NAV_PAGES.map(item)));
  nav.appendChild(el("ul", { class: "nav-bottom" }, NAV_BOTTOM.map(item)));
}

/* ---------- shared: connection state in the top bar ---------- */
let pageHooks = {};
function applyReady() {
  const dbBadge = $("dbBadge");
  dbBadge.textContent = "Database: " + (state.ready ? "connected" : "not connected");
  dbBadge.className = "badge " + (state.ready ? "ok" : "");
  const notice = $("dbNotice");
  if (notice) notice.hidden = state.ready;
  if (pageHooks.onReadyChange) pageHooks.onReadyChange();
}

/* The server holds the database connection. Each page asks for the status, re-checks the connection
   (the same call the single page made) and then lets the page load its own data.
   hooks: onStatus(status), onDbResult(result), onDbError(error), onReadyChange(), afterConnect(). */
async function bootstrap(hooks) {
  pageHooks = hooks || {};
  const s = await api("GET", "/api/status");
  state.status = s;
  if (s.version !== EXPECTED_SERVER_VERSION) {
    showGlobalError("The server is running an older version of server.py than this page (" + (s.version || "unknown") +
      " instead of " + EXPECTED_SERVER_VERSION + "). Some buttons will not work. Close the window or terminal running the server " +
      "(Ctrl+C), start it again with start.bat or python server.py, then reload this page.");
  }
  const tb = $("tokenBadge");
  tb.textContent = "Token: " + (s.token_configured ? "configured" : "not configured");
  tb.className = "badge " + (s.token_configured ? "ok" : "bad");
  const sb = $("setupBanner");
  sb.hidden = s.token_configured;
  sb.textContent = s.token_configured ? "" : ("Setup needed: " + (s.config_problem || "No token configured."));
  cfgBases = s.bases || [];
  refreshBaseChoices();
  const base = $("baseId");
  if (base && !base.value) base.value = savedBase() || (cfgBases.length === 1 ? cfgBases[0].baseId : "");
  if (pageHooks.onStatus) pageHooks.onStatus(s);
  if (s.db_path) {
    try {
      const r = await api("POST", "/api/db/connect", { path: s.db_path });
      state.ready = !!r.ready;
      if (pageHooks.onDbResult) pageHooks.onDbResult(r);
    } catch (e) {
      state.ready = false;
      if (pageHooks.onDbError) pageHooks.onDbError(e);
    }
  }
  applyReady();
  if (state.ready && pageHooks.afterConnect) await pageHooks.afterConnect();
}

/* Pages call this once the document is ready. */
function startPage(key, hooks, wire) {
  window.addEventListener("DOMContentLoaded", () => {
    renderNav(key);
    if (wire) wire();
    applyReady();
    bootstrap(hooks).catch((e) => showGlobalError(e.message));
  });
}

/* Known bases for the picker: the bases already in the database (names still come from config.json). */
async function loadKnownBases() {
  const data = await api("GET", "/api/schema");
  state.schema = data.bases;
  refreshBaseChoices();
}

/* Pages with nothing of their own to run (the placeholders) are marked data-plain and start here. */
if (document.body && document.body.hasAttribute("data-plain")) startPage(document.body.dataset.page, {});

/* How old is data that was pulled? Over 4 hours is stale (yellow); over 8 hours is obsolete (red). A time the browser cannot read gives no tag. */
const AGE_STALE_HOURS = 4, AGE_OBSOLETE_HOURS = 8;
function ageInfo(iso, now) {
  const t = Date.parse(iso || "");
  if (isNaN(t)) return null;
  const hours = Math.max(0, ((now === undefined ? Date.now() : now) - t) / 3600000);
  const mins = Math.floor(hours * 60);
  const text = mins < 60 ? mins + " min ago" : hours < 48 ? Math.floor(hours) + " h " + (mins % 60) + " min ago" : Math.floor(hours / 24) + " days ago";
  if (hours > AGE_OBSOLETE_HOURS) return { level: "obsolete", text: "obsolete: pulled " + text };
  if (hours > AGE_STALE_HOURS) return { level: "stale", text: "stale: pulled " + text };
  return { level: "fresh", text: "pulled " + text };
}
function ageTag(iso) {
  const a = ageInfo(iso);
  return a ? el("span", { class: "tag age-" + a.level, text: a.text, title: "Data older than " + AGE_STALE_HOURS + " hours is stale; older than " + AGE_OBSOLETE_HOURS + " hours is obsolete. Pull it again before relying on it." }) : "";
}
