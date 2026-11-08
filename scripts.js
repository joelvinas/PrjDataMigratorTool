"use strict";
/* Airtable migration control plane: front end. No frameworks. All data is inserted with
   textContent (never innerHTML) so table or field names cannot inject markup. */

const $ = (id) => document.getElementById(id);
const EXPECTED_SERVER_VERSION = "1.8"; // must match APP_VERSION in server.py
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
  if (!res.ok) throw new Error(data.error || ("Request failed (" + res.status + ")"));
  return data;
}

/* ---------- status / step 1 ---------- */
async function loadStatus() {
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
  $("tokenYesNo").textContent = s.token_configured ? "yes" : "no";
  baseChoices = s.bases || [];
  if (baseChoices.length === 1 && !$("baseId").value) $("baseId").value = baseChoices[0].baseId;
  const sb = $("setupBanner");
  sb.hidden = s.token_configured;
  sb.textContent = s.token_configured ? "" : ("Setup needed: " + (s.config_problem || "No token configured."));
  if (!$("dbPath").value && s.default_db_path) $("dbPath").value = s.default_db_path;
  if (s.db_path) {
    $("dbPath").value = s.db_path;
    await connectDb();
  }
  applyReady();
}

function applyReady() {
  const dbBadge = $("dbBadge");
  dbBadge.textContent = "Database: " + (state.ready ? "connected" : "not connected");
  dbBadge.className = "badge " + (state.ready ? "ok" : "");
  for (const id of ["btnIngest", "btnRecompute", "btnProfile", "btnReloadSchema", "btnResults", "btnLog", "btnExtractDelete", "btnExtractRefresh"]) $(id).disabled = !state.ready;
  if (typeof syncReportLinks === "function") syncReportLinks();
  $("btnIngest").disabled = !state.ready;
}

function renderCounts(counts) {
  const t = $("countsTable");
  const body = t.querySelector("tbody");
  clear(body);
  const names = Object.keys(counts);
  names.forEach((n) => body.appendChild(el("tr", {}, el("td", { text: n }), el("td", { class: "num", text: fmt(counts[n]) }))));
  t.hidden = names.length === 0;
}

function renderDbResult(r) {
  state.ready = !!r.ready;
  const actions = $("dbActions");
  actions.hidden = true;
  if (r.counts) renderCounts(r.counts); else $("countsTable").hidden = true;
  if (r.exists === false) {
    setMsg("dbMessage", r.message || "That file does not exist yet.", "warn");
    $("btnCreate").textContent = "Create new database here";
    $("btnCreate").dataset.mode = "create";
    actions.hidden = false;
  } else if (r.missing_tables && r.missing_tables.length) {
    setMsg("dbMessage", "Connected, but these expected tables are missing: " + r.missing_tables.join(", "), "warn");
    $("btnCreate").textContent = "Create the missing tables";
    $("btnCreate").dataset.mode = "create";
    actions.hidden = false;
  } else if (r.column_problems && r.column_problems.length) {
    const detail = r.column_problems.map((p) => p.table + " lacks " + p.missing_columns.join(", ")).join("; ");
    setMsg("dbMessage", (r.message || "Some tables do not match the expected structure.") + " (" + detail + ")", "err");
  } else if (r.ready) {
    let msg = "Connected. All " + Object.keys(r.counts).length + " expected tables are present. Foreign keys are " + (r.foreign_keys ? "on." : "OFF.");
    if (r.created_tables && r.created_tables.length) msg = "Created " + r.created_tables.length + " table(s). " + msg;
    setMsg("dbMessage", msg, "ok");
  }
  applyReady();
}

async function connectDb() {
  showGlobalError("");
  try {
    renderDbResult(await api("POST", "/api/db/connect", { path: $("dbPath").value }));
    if (state.ready) await afterConnect();
  } catch (e) {
    state.ready = false; applyReady(); setMsg("dbMessage", e.message, "err");
  }
}
async function createDb() {
  try {
    renderDbResult(await api("POST", "/api/db/create", { path: $("dbPath").value }));
    if (state.ready) await afterConnect();
  } catch (e) { setMsg("dbMessage", e.message, "err"); }
}
async function afterConnect() {
  await Promise.all([loadSchema(), loadDepgraph(), loadLog(), loadRunbook(), loadProfileStatus(true), loadExtractFiles()]);
}

/* ---------- step 3: ingest + schema view ---------- */
function currentSource() { return document.querySelector("input[name=src]:checked").value; }

async function ingest() {
  setMsg("ingestMessage", "Working...", "");
  $("btnIngest").disabled = true;
  try {
    const src = currentSource();
    const r = await api("POST", "/api/schema/ingest", {
      base_id: $("baseId").value, source: src, schema_json: src === "json" ? $("schemaText").value : undefined,
    });
    let msg = "Ingested base " + r.base_id + ": " + r.tables + " tables, " + r.fields + " fields, " + (r.views || 0) + " views (" + r.added + " new fields, " + r.updated + " refreshed).";
    let kind = "ok";
    if (r.no_longer_in_source) { msg += " " + r.no_longer_in_source + " previously known field(s) are no longer in the source (kept, flagged below)."; kind = "warn"; }
    setMsg("ingestMessage", msg, kind);
    await Promise.all([loadSchema(), loadDepgraph(), loadLog(), loadRunbook()]);
    const c = await api("GET", "/api/db/counts");
    renderCounts(c.counts);
  } catch (e) {
    setMsg("ingestMessage", e.message, "err");
    loadLog(); loadRunbook();
  } finally { applyReady(); }
}

function typeLabel(f) {
  if (!f.linked_table_id) return f.data_type;
  return f.data_type + " → " + (f.linked_table_name || f.linked_table_id) + (f.self_link ? " (self-link)" : "") +
    (f.prefers_single_record_link === 1 ? " [single link]" : "");
}

async function loadSchema() {
  const data = await api("GET", "/api/schema");
  state.schema = data.bases;
  const box = $("schemaView");
  clear(box);
  if (!data.bases.length) box.appendChild(el("p", { class: "hint", text: "No schema ingested yet." }));
  for (const b of data.bases) {
    const nFields = b.tables.reduce((a, t) => a + t.fields.length, 0);
    const d = el("details", { open: data.bases.length === 1 }, el("summary", {}, "Base " + b.base_id, el("span", { class: "tag", text: b.tables.length + " tables, " + nFields + " fields" })));
    const inner = el("div", { class: "inner" });
    for (const t of b.tables) {
      const sum = el("summary", {}, t.table_name, el("span", { class: "tag", text: t.fields.length + " fields" }),
        t.stale ? el("span", { class: "tag stale", text: "no longer in source" }) : (t.stale_fields ? el("span", { class: "tag stale", text: t.stale_fields + " field(s) no longer in source" }) : null));
      const rows = t.fields.map((f) => el("tr", {}, el("td", { text: f.field_name }), el("td", { text: f.field_id }), el("td", { text: typeLabel(f) }),
        el("td", { class: "settings", text: f.settings || "" }),
        el("td", {}, f.stale ? el("span", { class: "tag stale", text: "no longer in source" }) : "")));
      const meta = [];
      if (t.primary_field_name) meta.push("Primary field: " + t.primary_field_name);
      if (t.description) meta.push("Description: " + t.description);
      if (t.views && t.views.length) meta.push("Views (" + t.views.length + "): " + t.views.map((v) =>
        v.view_name + " [" + v.view_type + (v.visible_field_count === null ? "" : ", " + v.visible_field_count + " fields") + "]" + (v.stale ? " (no longer in source)" : "")).join("; "));
      else if (t.details_captured) meta.push("No views reported");
      inner.appendChild(el("details", {}, sum, meta.length ? el("p", { class: "hint tablemeta", text: meta.join("  |  ") }) : null, el("table", { class: "grid compact" },
        el("thead", {}, el("tr", {}, el("th", { text: "Field" }), el("th", { text: "Field ID" }), el("th", { text: "Type / linked table" }), el("th", { text: "Settings" }), el("th", { text: "" }))),
        el("tbody", {}, rows))));
    }
    d.appendChild(inner);
    box.appendChild(d);
  }
  fillScopeSelectors();
  syncReportLinks();
}

function fillScopeSelectors() {
  const sb = $("scopeBase"), st = $("scopeTable"), rb = $("resBase"), rt = $("resTable"), cb = $("csvBase");
  const eb = $("exBase");
  const keepB = [sb.value, rb.value, cb.value, eb.value], keepT = [st.value, rt.value];
  for (const n of [sb, rb, cb, eb]) { clear(n); if (n !== sb) n.appendChild(el("option", { value: "", text: "All bases" })); }
  for (const n of [st, rt]) { clear(n); if (n === rt) n.appendChild(el("option", { value: "", text: "All tables" })); }
  for (const b of state.schema) {
    for (const n of [sb, rb, cb, eb]) n.appendChild(el("option", { value: b.base_id, text: b.base_id }));
    for (const t of b.tables) {
      if (t.stale) continue;
      for (const n of [st, rt]) n.appendChild(el("option", { value: t.table_id, text: b.base_id + " / " + t.table_name }));
    }
  }
  sb.value = keepB[0]; rb.value = keepB[1]; cb.value = keepB[2]; eb.value = keepB[3]; st.value = keepT[0]; rt.value = keepT[1];
  updateScopeVisibility();
}
function updateScopeVisibility() {
  const s = $("scope").value;
  $("scopeBase").hidden = s !== "base";
  $("scopeTable").hidden = s !== "table";
}

/* ---------- step 4: dependency graph ---------- */
function note(kind, title, items) {
  return el("div", { class: "note " + kind }, el("strong", { text: title }), items && items.length ? el("ul", {}, items.map((i) => el("li", { text: i }))) : null);
}

async function recompute() {
  setMsg("depMessage", "Working...", "");
  try {
    const r = await api("POST", "/api/depgraph/recompute", { mode: $("depMode").value });
    let msg = "Load order written for " + r.tables + " tables.";
    if (r.cycles.length) msg += " " + r.cycles.length + " cycle(s) found; " + r.runbook_rows_written + " new runbook row(s) written.";
    setMsg("depMessage", msg, r.cycles.length ? "warn" : "ok");
    await Promise.all([loadDepgraph(), loadRunbook()]);
  } catch (e) { setMsg("depMessage", e.message, "err"); }
}

async function loadDepgraph() {
  const g = await api("GET", "/api/depgraph?mode=" + encodeURIComponent($("depMode").value));
  const notes = $("depNotes");
  clear(notes);
  const names = {};
  g.stored_matrix.forEach((m) => { names[m.table_id] = m.table_name; });
  if (g.stored_matrix.length && !g.matrix_in_sync) notes.appendChild(note("info", "The saved load order is out of date with the schema or the chosen link handling. Click \"Recompute load order\" to refresh it."));
  if (!g.stored_matrix.length && state.schema.length) notes.appendChild(note("info", "No load order saved yet. Click \"Recompute load order\"."));
  for (const c of g.cycles) {
    notes.appendChild(note("err", "Cycle: " + c.tables.join(" ↔ "), c.edges.map((e) => e.child + " links to " + e.parent + " (" + e.fields.join(", ") + ")")
      .concat(["Load these tables' records first, then fill in their links in a second pass."])));
  }
  if (g.inverse_pairs.length) {
    const lines = g.inverse_pairs.map((p) => p.table_a + "." + p.field_a + " ⇄ " + p.table_b + "." + p.field_b + " — " +
      (p.child_table ? p.child_table + " is the child of " + p.parent_table + " (single-record link)" : "direction unknown (many-to-many, or the hint was not reported): load together as a cycle"));
    if (g.mode === "literal") notes.appendChild(note("warn", g.inverse_pairs.length + " two-way link pair(s) found (a link field and its inverse). Literal mode counts each pair as a 2-table cycle. If these are just Airtable's automatic inverse fields, switch to \"Resolve two-way link pairs\".", lines));
    else notes.appendChild(note("info", g.inverse_pairs.length + " two-way link pair(s). Where exactly one side is a single-record link, that side's table is ordered as the child. The rest add no ordering; populate their links in a second pass, after both tables' records exist.", lines));
  }
  if (g.self_links.length) notes.appendChild(note("info", "Self-links (ignored for ordering)", g.self_links.map((s) => s.table_name + "." + s.field_name)));
  if (g.unresolved_links.length) notes.appendChild(note("warn", "Links to tables that are not in the database (ingest the other base?)", g.unresolved_links.map((u) => u.table_name + "." + u.field_name + " → " + u.linked_table_id)));

  const selfTables = new Set(g.self_links.map((s) => s.table_id));
  const t = $("orderTable"), body = t.querySelector("tbody");
  clear(body);
  g.stored_matrix.forEach((m) => {
    const flags = [m.in_cycle ? "in a cycle" : "", selfTables.has(m.table_id) ? "self-link" : ""].filter(Boolean).join(", ");
    body.appendChild(el("tr", {}, el("td", { class: "num", text: m.load_order_sequence }), el("td", { text: m.table_name }), el("td", { text: m.table_id }),
      el("td", { class: "num", text: m.dependency_depth }), el("td", { text: m.parent_table_ids.map((p) => names[p] || p).join(", ") }), el("td", { text: flags })));
  });
  t.hidden = g.stored_matrix.length === 0;
  drawGraph(g, selfTables);
}

function drawGraph(g, selfTables) {
  const box = $("graphBox");
  clear(box);
  const nodes = g.stored_matrix;
  if (!nodes.length) { box.appendChild(el("p", { class: "hint", text: "Nothing to draw yet." })); return; }
  const NS = "http://www.w3.org/2000/svg";
  const S = (tag, attrs) => { const n = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v); return n; };
  const W = 190, H = 30, GX = 90, GY = 16, PAD = 20;
  const cols = {};
  nodes.forEach((n) => { (cols[n.dependency_depth] = cols[n.dependency_depth] || []).push(n); });
  const depths = Object.keys(cols).map(Number).sort((a, b) => a - b);
  const pos = {};
  let maxRows = 0;
  depths.forEach((d, ci) => {
    cols[d].sort((a, b) => a.load_order_sequence - b.load_order_sequence);
    maxRows = Math.max(maxRows, cols[d].length);
    cols[d].forEach((n, ri) => { pos[n.table_id] = { x: PAD + ci * (W + GX), y: PAD + ri * (H + GY) }; });
  });
  const width = PAD * 2 + depths.length * W + (depths.length - 1) * GX + 40;
  const height = PAD * 2 + maxRows * H + (maxRows - 1) * GY;
  const svg = S("svg", { width, height, viewBox: "0 0 " + width + " " + height, role: "img", "aria-label": "Table dependency graph" });
  const defs = S("defs");
  for (const [id, color] of [["arr", "#8a93a6"], ["arrc", "#b3261e"]]) {
    const m = S("marker", { id, viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" });
    m.appendChild(S("path", { d: "M0,0 L10,5 L0,10 z", fill: color }));
    defs.appendChild(m);
  }
  svg.appendChild(defs);
  const cyc = new Set(nodes.filter((n) => n.in_cycle).map((n) => n.table_id));
  for (const e of g.edges) {
    const p = pos[e.parent], c = pos[e.child];
    if (!p || !c) continue;
    const inCyc = cyc.has(e.parent) && cyc.has(e.child);
    let d;
    if (p.x === c.x) {
      const x1 = p.x + W, y1 = p.y + H / 2, y2 = c.y + H / 2;
      d = "M" + x1 + "," + y1 + " C" + (x1 + 45) + "," + y1 + " " + (x1 + 45) + "," + y2 + " " + x1 + "," + y2;
    } else {
      const x1 = p.x + W, y1 = p.y + H / 2, x2 = c.x, y2 = c.y + H / 2, mx = (x1 + x2) / 2;
      d = "M" + x1 + "," + y1 + " C" + mx + "," + y1 + " " + mx + "," + y2 + " " + x2 + "," + y2;
    }
    const path = S("path", { d, class: "edge" + (inCyc ? " cyc" : ""), "marker-end": "url(#" + (inCyc ? "arrc" : "arr") + ")" });
    const tt = S("title"); tt.textContent = e.parent + " → " + e.child + ": " + e.fields.join(", ");
    path.appendChild(tt);
    svg.appendChild(path);
  }
  nodes.forEach((n) => {
    const p = pos[n.table_id];
    const grp = S("g", { class: "node" + (n.in_cycle ? " cyc" : "") });
    grp.appendChild(S("rect", { x: p.x, y: p.y, width: W, height: H }));
    const label = n.load_order_sequence + ". " + n.table_name;
    const txt = S("text", { x: p.x + 8, y: p.y + H / 2 + 4 });
    txt.textContent = label.length > 26 ? label.slice(0, 25) + "…" : label;
    grp.appendChild(txt);
    if (selfTables.has(n.table_id)) { const s = S("text", { x: p.x + W - 18, y: p.y + H / 2 + 4 }); s.textContent = "↻"; grp.appendChild(s); }
    const tt = S("title"); tt.textContent = n.table_name + " (" + n.table_id + ")";
    grp.appendChild(tt);
    svg.appendChild(grp);
  });
  box.appendChild(svg);
}

/* ---------- base picker: dropdown from config.json, typing still allowed ---------- */
let baseChoices = [];
let baseActive = -1;
function baseMatches(filter) {
  const f = (filter || "").trim().toLowerCase();
  return f ? baseChoices.filter((b) => b.baseId.toLowerCase().includes(f) || (b.name || "").toLowerCase().includes(f)) : baseChoices;
}
function openBaseMenu(filter) {
  const menu = $("baseMenu"), list = baseMatches(filter);
  clear(menu);
  baseActive = -1;
  if (!baseChoices.length) {
    menu.appendChild(el("li", { class: "combo-empty", text: "No bases listed in config.json. Type a base ID instead." }));
  } else if (!list.length) {
    menu.appendChild(el("li", { class: "combo-empty", text: "No configured base matches. The text you typed will be used as the base ID." }));
  }
  list.forEach((b, i) => {
    const li = el("li", { role: "option", "data-id": b.baseId },
      el("span", { class: "combo-name", text: b.name || "(no name)" }), el("code", { text: b.baseId }));
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
  inp.addEventListener("input", () => openBaseMenu(inp.value));
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

/* ---------- step 4: report ---------- */
function reportUrl(kind, extra) {
  return "/api/report." + kind + "?mode=" + encodeURIComponent($("depMode").value) + (extra || "");
}
function syncReportLinks() {
  $("lnkMhtml").setAttribute("href", reportUrl("mhtml"));
  $("lnkHtml").setAttribute("href", reportUrl("html", "&download=1"));
  $("lnkOrderCsv").setAttribute("href", "/api/export/load_order.csv?mode=" + encodeURIComponent($("depMode").value) +
    "&base_id=" + encodeURIComponent($("csvBase").value));
  $("lnkProfileCsv").setAttribute("href", "/api/export/profile.csv?base_id=" + encodeURIComponent($("resBase").value) +
    "&table_id=" + encodeURIComponent($("resTable").value));
  const off = !state.ready || !state.schema.length;
  $("lnkOrderCsv").classList.toggle("disabled", off);
  for (const id of ["lnkFieldCsv", "lnkTableCsv", "lnkViewCsv"]) $(id).classList.toggle("disabled", off);
  $("lnkProfileCsv").classList.toggle("disabled", off);
  $("lnkMhtml").classList.toggle("disabled", off);
  $("lnkHtml").classList.toggle("disabled", off);
  $("btnReportPreview").disabled = off;
}
function previewReport() {
  const f = $("reportFrame");
  f.hidden = false;
  f.src = reportUrl("html", "&t=" + Date.now());
  setMsg("reportMessage", "Preview shows the current database. Use the download buttons to keep a copy.", "");
}

async function loadRunbook() {
  const data = await api("GET", "/api/runbook?status=OPEN");
  const box = $("runbookBox");
  clear(box);
  if (!data.rows.length) { box.appendChild(el("p", { class: "hint", text: "No open runbook items." })); return; }
  box.appendChild(el("table", { class: "grid compact" },
    el("thead", {}, el("tr", {}, ["Time (UTC)", "Component", "Message"].map((h) => el("th", { text: h })))),
    el("tbody", {}, data.rows.map((r) => el("tr", {}, el("td", { text: (r.timestamp || "").slice(0, 19) }), el("td", { text: r.failing_component }), el("td", { text: r.error_message }))))));
}

/* ---------- step 5: profiling ---------- */
async function startProfile() {
  setMsg("profMessage", "Starting...", "");
  try {
    const scope = $("scope").value;
    await api("POST", "/api/profile/start", { scope, base_id: $("scopeBase").value, table_id: $("scopeTable").value, cap: $("cap").value, save_records: $("saveRecords").checked, download_attachments: $("saveRecords").checked && $("downloadFiles").checked });
    setMsg("profMessage", "", "");
    await loadProfileStatus(false);
  } catch (e) { setMsg("profMessage", e.message, "err"); }
}
async function cancelProfile() {
  try { await api("POST", "/api/profile/cancel", {}); setMsg("profMessage", "Cancelling after the current request...", "warn"); }
  catch (e) { setMsg("profMessage", e.message, "err"); }
}

function statusTag(s) {
  const cls = { done: "done", failed: "fail", running: "run" }[s] || "";
  return el("span", { class: "tag " + cls, text: s });
}

function renderProgress(job) {
  const box = $("profProgress");
  clear(box);
  if (!job) return;
  const done = job.tables.filter((t) => t.status === "done").length;
  const failed = job.tables.filter((t) => t.status === "failed").length;
  const total = job.tables.length;
  const label = { running: "Running", done: "Finished", cancelled: "Cancelled", error: "Stopped by an error" }[job.status] || job.status;
  box.appendChild(el("p", {}, el("strong", { text: label }), " – " + done + " of " + total + " tables done" + (failed ? ", " + failed + " failed" : "") +
    ". API calls this run: " + job.api.calls + " (" + job.api.success + " ok, " + job.api.failure + " failed)." +
    (job.save_records ? " Saving records to JSON files." : " Not saving records.") + (job.download_files ? " Downloading attachment files." : "")));
  box.appendChild(el("div", { class: "bar" }, el("span", { style: "width:" + Math.round((100 * (done + failed)) / total) + "%" })));
  if (job.error) box.appendChild(note("err", job.error));
  const bases = {};
  job.tables.forEach((t) => {
    const b = bases[t.base_id] = bases[t.base_id] || { tables: 0, done: 0, failed: 0, pages: 0, records: 0 };
    b.tables++; b.pages += t.pages; b.records += t.records;
    if (t.status === "done") b.done++;
    if (t.status === "failed") b.failed++;
  });
  box.appendChild(el("table", { class: "grid compact prog" },
    el("thead", {}, el("tr", {}, ["Base", "Tables done", "Failed", "Pages fetched", "Records seen"].map((h, i) => el("th", { class: i ? "num" : "", text: h })))),
    el("tbody", {}, Object.keys(bases).map((id) => { const b = bases[id]; return el("tr", {}, el("td", { text: id }), el("td", { class: "num", text: b.done + " / " + b.tables }), el("td", { class: "num", text: b.failed }), el("td", { class: "num", text: fmt(b.pages) }), el("td", { class: "num", text: fmt(b.records) })); }))));
  box.appendChild(el("table", { class: "grid compact prog" },
    el("thead", {}, el("tr", {}, ["Table", "Status", "Pages", "Records", "Detail"].map((h, i) => el("th", { class: i === 2 || i === 3 ? "num" : "", text: h })))),
    el("tbody", {}, job.tables.map((t) => el("tr", {}, el("td", { text: t.table_name }), el("td", {}, statusTag(t.status)), el("td", { class: "num", text: fmt(t.pages) }), el("td", { class: "num", text: fmt(t.records) }),
      el("td", {}, t.error ? el("span", { class: "tag fail", text: t.error }) : "", t.capped_fields ? el("span", { class: "tag cap", text: t.capped_fields + " field(s) over cap" }) : "", t.saved ? el("span", { class: "tag done", text: "saved to file" }) : "",
        t.files || t.file_failed ? el("span", { class: "tag " + (t.file_failed ? "cap" : "done"), text: fmt(t.files) + " file(s), " + fmtBytes(t.file_bytes) + (t.file_failed ? ", " + t.file_failed + " failed" : "") }) : ""))))));
}

async function loadProfileStatus(quiet) {
  const { job } = await api("GET", "/api/profile/status");
  renderProgress(job);
  const running = !!job && job.status === "running";
  $("btnCancel").hidden = !running;
  $("btnProfile").disabled = !state.ready || running;
  if (running && !state.pollTimer) state.pollTimer = setInterval(() => loadProfileStatus(true).catch(() => {}), 1000);
  if (running) loadLog().catch(() => {});
  if (!running && state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
  if (job && !running && state.lastJobId === job.id && state.lastJobStatus === "running") { await Promise.all([loadResults(), loadLog(), loadRunbook(), loadExtractFiles()]); }
  if (job) { state.lastJobId = job.id; state.lastJobStatus = job.status; }
  if (job && !quiet) loadLog();
}

/* ---------- step 6: record extraction (values go to files on disk; this page only shows counts) ---------- */
const fmtBytes = (n) => (n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : n >= 1024 ? (n / 1024).toFixed(1) + " KB" : n + " B");

async function deleteExtract() {
  const base = $("exBase").value;
  const what = base ? "the extracted files for " + base : "ALL extracted record files";
  if (!window.confirm("Delete " + what + "? The database is not changed. You can extract again later.")) return;
  try {
    const r = await api("POST", "/api/extract/delete", base ? { base_id: base } : {});
    setMsg("exMessage", "Deleted " + r.removed + " file(s).", "ok");
    await loadExtractFiles();
  } catch (e) { setMsg("exMessage", e.message, "err"); }
}

async function loadExtractFiles() {
  const d = await api("GET", "/api/extract/files");
  $("exFolder").textContent = d.folder;
  document.querySelectorAll(".exFolderRef").forEach((n) => { n.textContent = d.folder; });
  const t = $("exFilesTable"), body = t.querySelector("tbody");
  clear(body);
  d.files.forEach((f) => {
    const bad = f.check.startsWith("differs");
    body.appendChild(el("tr", {}, el("td", { text: f.base_id + " / " + f.table_name }), el("td", { class: "num", text: fmt(f.record_count) }), el("td", { class: "num", text: fmt(f.fields) }),
      el("td", { class: "num", text: fmtBytes(f.bytes) }),
      el("td", { class: "num", text: f.attachments ? fmt(f.attachments.files) + " (" + fmtBytes(f.attachments.bytes) + ")" + (f.attachments.failed ? ", " + f.attachments.failed + " failed" : "") : "" }), el("td", { text: f.captured_at.replace("T", " ").replace("Z", "") }),
      el("td", {}, el("span", { class: "tag " + (bad ? "cap" : f.check === "matches profile" ? "done" : ""), text: f.check })), el("td", {}, el("code", { text: f.sha256.slice(0, 12) }))));
  });
  t.hidden = d.files.length === 0;
  setMsg("exFilesSummary", d.files.length ? d.files.length + " file(s), " + fmt(d.total_records) + " records, " + fmtBytes(d.total_bytes) + " in total." + (d.total_attachment_files || d.total_attachment_failed ? " Attachment files: " + fmt(d.total_attachment_files) + " (" + fmtBytes(d.total_attachment_bytes) + ")" + (d.total_attachment_failed ? ", " + fmt(d.total_attachment_failed) + " failed (see the index files)." : ".") : "") : "No extracted files yet.", "");
}

async function loadResults() {
  const q = "?base_id=" + encodeURIComponent($("resBase").value) + "&table_id=" + encodeURIComponent($("resTable").value);
  const data = await api("GET", "/api/profile/results" + q);
  const t = $("resultsTable"), body = t.querySelector("tbody");
  clear(body);
  data.rows.forEach((r) => {
    let card;
    if (r.cardinality_capped) card = el("span", { class: "tag cap", text: "capped (more than " + fmt(r.cardinality) + ")" });
    else card = fmt(r.cardinality);
    body.appendChild(el("tr", {}, el("td", { text: r.table_name }), el("td", {}, r.field_name, r.stale ? el("span", { class: "tag stale", text: "no longer in source" }) : ""), el("td", { text: r.data_type }),
      el("td", { class: "num", text: fmt(r.row_count) }), el("td", { class: "num", text: fmt(r.null_count) }), el("td", { class: "num", text: r.null_pct === null ? "" : r.null_pct + "%" }), el("td", { class: "num" }, card)));
  });
  t.hidden = data.rows.length === 0;
}

/* ---------- API call log ---------- */
async function loadLog() {
  const q = new URLSearchParams({ failures: $("logFail").checked ? "1" : "", table_id: $("logTable").value, purpose: $("logPurpose").value, limit: "100" });
  const data = await api("GET", "/api/calllog?" + q.toString());
  const fillSel = (sel, values, allLabel) => {
    const keep = sel.value; clear(sel); sel.appendChild(el("option", { value: "", text: allLabel }));
    values.forEach((v) => sel.appendChild(el("option", { value: v, text: v })));
    sel.value = values.includes(keep) ? keep : "";
  };
  fillSel($("logTable"), data.table_ids, "All tables");
  fillSel($("logPurpose"), data.purposes, "All purposes");
  const s = data.summary;
  setMsg("logSummary", "Matching calls: " + fmt(s.total) + " (" + fmt(s.success) + " succeeded, " + fmt(s.failure) + " failed). Showing the latest " + data.rows.length + ".", s.failure ? "warn" : "");
  const body = $("logTableEl").querySelector("tbody");
  clear(body);
  data.rows.forEach((r) => body.appendChild(el("tr", {}, el("td", { text: (r.timestamp || "").slice(0, 19) }), el("td", { text: r.purpose }), el("td", { text: r.endpoint }), el("td", { text: r.table_id || "" }),
    el("td", { class: "num", text: r.attempt }), el("td", { class: "num", text: r.http_status === null ? "none" : r.http_status }), el("td", { class: r.success ? "good" : "bad", text: r.success ? "yes" : "no" }),
    el("td", { class: "num", text: fmt(r.duration_ms) }), el("td", { class: "num", text: fmt(r.records_returned) }), el("td", { text: r.error_message || "" }))));
}

/* ---------- wiring ---------- */
function guard(fn) { return () => fn().catch((e) => showGlobalError(e.message)); }

window.addEventListener("DOMContentLoaded", () => {
  $("btnConnect").addEventListener("click", connectDb);
  $("dbPath").addEventListener("keydown", (e) => { if (e.key === "Enter") connectDb(); });
  $("btnCreate").addEventListener("click", createDb);
  document.querySelectorAll("input[name=src]").forEach((r) => r.addEventListener("change", () => { $("jsonBox").hidden = currentSource() !== "json"; }));
  $("schemaFile").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const rd = new FileReader();
    rd.onload = () => { $("schemaText").value = rd.result; };
    rd.readAsText(f);
  });
  $("btnIngest").addEventListener("click", ingest);
  $("btnReloadSchema").addEventListener("click", guard(loadSchema));
  $("btnRecompute").addEventListener("click", recompute);
  $("depMode").addEventListener("change", () => { syncReportLinks(); loadDepgraph().catch((e) => showGlobalError(e.message)); });
  $("btnReportPreview").addEventListener("click", previewReport);
  for (const id of ["lnkMhtml", "lnkHtml"]) $(id).addEventListener("click", () => setMsg("reportMessage", "Report downloaded.", "ok"));
  $("scope").addEventListener("change", updateScopeVisibility);
  $("btnProfile").addEventListener("click", startProfile);
  $("btnCancel").addEventListener("click", cancelProfile);
  $("btnExtractDelete").addEventListener("click", deleteExtract);
  $("btnExtractRefresh").addEventListener("click", guard(loadExtractFiles));
  const syncSave = () => {
    const on = $("saveRecords").checked;
    $("saveNote").hidden = !on;
    $("dlRow").hidden = !on;
    if (!on) $("downloadFiles").checked = false;
    $("dlNote").hidden = !(on && $("downloadFiles").checked);
  };
  $("saveRecords").addEventListener("change", syncSave);
  $("downloadFiles").addEventListener("change", syncSave);
  $("btnResults").addEventListener("click", guard(loadResults));
  initBaseCombo();
  $("resBase").addEventListener("change", syncReportLinks);
  $("csvBase").addEventListener("change", syncReportLinks);
  $("resTable").addEventListener("change", syncReportLinks);
  $("btnLog").addEventListener("click", guard(loadLog));
  for (const id of ["logFail", "logTable", "logPurpose"]) $(id).addEventListener("change", guard(loadLog));
  applyReady();
  loadStatus().catch((e) => showGlobalError(e.message));
});
