"use strict";
/* Stage 1: Data (steps 3 to 6). Schema, load order, profiling, saved record files. */

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
    await Promise.all([loadSchema(), loadDepgraph(), loadRunbook()]);
  } catch (e) {
    setMsg("ingestMessage", e.message, "err");
    loadRunbook();
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
  const keepB = [sb.value, rb.value, cb.value, eb.value], keepT = [Array.from(st.selectedOptions).map((o) => o.value), rt.value];
  for (const n of [sb, rb, cb, eb]) { clear(n); if (n !== sb) n.appendChild(el("option", { value: "", text: "All bases" })); }
  for (const n of [st, rt]) { clear(n); if (n === rt) n.appendChild(el("option", { value: "", text: "All tables" })); }
  refreshBaseChoices();
  // Base lists come from the database (the bases actually ingested); names come from config.json when it has them.
  for (const b of state.schema) {
    for (const n of [sb, rb, cb, eb]) n.appendChild(el("option", { value: b.base_id, text: baseLabel(b.base_id) }));
    for (const t of b.tables) {
      if (t.stale) continue;
      for (const n of [st, rt]) n.appendChild(el("option", { value: t.table_id, text: baseLabel(b.base_id) + " / " + t.table_name }));
    }
  }
  sb.value = keepB[0]; rb.value = keepB[1]; cb.value = keepB[2]; eb.value = keepB[3]; Array.from(st.options).forEach((o) => { o.selected = keepT[0].includes(o.value); }); rt.value = keepT[1];
  updateScopeVisibility();
}
function updateScopeVisibility() {
  const s = $("scope").value;
  $("scopeBase").hidden = s !== "base";
  $("rowScopeTable").hidden = s !== "tables";
}

/* ---------- step 4: dependency graph ---------- */
function note(kind, title, items) {
  return el("div", { class: "note " + kind }, el("strong", { text: title }), items && items.length ? el("ul", {}, items.map((i) => el("li", { text: i }))) : null);
}

const MODE_HELP = {
  literal: "Literal: every link field counts as a dependency, exactly as Airtable defines it. Airtable creates a link on both tables for each relationship, so linked tables usually depend on each other and end up in cycles that load together, with a second pass to fill in the links. Pick this to see the worst case or to check the other mode against it.",
  collapse: "Resolve two-way link pairs: for each pair of linked fields that point at each other, if exactly one side is set to allow a single linked record, that table is the child and only its field is a dependency; the other side is just the reverse view. Many-to-many pairs, and pairs with no hint, stay as cycles. Pick this for the load plan: it gives the fewest cycles that are still correct.",
};
function syncModeHelp() { $("depModeHelp").textContent = MODE_HELP[$("depMode").value] || ""; }

/* ---------- download just the dependency graph (SVG or PNG), built in the browser from the drawing above ---------- */
const GRAPH_CSS = "text{font:12px system-ui,Segoe UI,Arial,sans-serif;fill:#1d2330}.node rect{fill:#fff;stroke:#7b8498;stroke-width:1.2;rx:5}" +
  ".node.cyc rect{stroke:#b3261e;stroke-width:2;fill:#fff6f5}.edge{fill:none;stroke:#8a93a6;stroke-width:1.2}.edge.cyc{stroke:#b3261e}";
function graphSvgText() {
  const src = $("graphBox").querySelector("svg");
  if (!src) throw new Error("There is no graph to download yet. Recompute the load order first.");
  const svg = src.cloneNode(true);
  svg.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  const w = Number(svg.getAttribute("width")), h = Number(svg.getAttribute("height"));
  const style = document.createElementNS("http://www.w3.org/2000/svg", "style");
  style.textContent = GRAPH_CSS;
  svg.insertBefore(style, svg.firstChild);
  const bg = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  bg.setAttribute("width", w); bg.setAttribute("height", h); bg.setAttribute("fill", "#ffffff");
  svg.insertBefore(bg, style.nextSibling);
  return { text: '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(svg), w, h };
}
function graphFileName(ext) {
  const d = new Date(), p = (n) => String(n).padStart(2, "0");
  return "DependencyGraph_" + ($("depMode").value === "literal" ? "Literal" : "Resolve") + "_" + d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + "-" + p(d.getHours()) + p(d.getMinutes()) + "." + ext;
}
function saveBlob(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
function downloadGraphSvg() {
  try { saveBlob(new Blob([graphSvgText().text], { type: "image/svg+xml" }), graphFileName("svg")); setMsg("depMessage", "", ""); }
  catch (e) { setMsg("depMessage", e.message, "err"); }
}
function downloadGraphPng() {
  let g;
  try { g = graphSvgText(); } catch (e) { setMsg("depMessage", e.message, "err"); return; }
  const scale = 2, img = new Image();
  img.onload = () => {
    const cv = document.createElement("canvas");
    cv.width = Math.min(g.w * scale, 16000); cv.height = Math.min(g.h * scale, 16000);
    const ctx = cv.getContext("2d");
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, cv.width, cv.height);
    ctx.drawImage(img, 0, 0, cv.width, cv.height);
    cv.toBlob((b) => b ? saveBlob(b, graphFileName("png")) : setMsg("depMessage", "The browser could not make a PNG of this graph. Use the SVG instead.", "err"), "image/png");
  };
  img.onerror = () => setMsg("depMessage", "The browser could not make a PNG of this graph. Use the SVG instead.", "err");
  img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(g.text);
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
    const tableIds = Array.from($("scopeTable").selectedOptions).map((o) => o.value);
    if (scope === "tables" && !tableIds.length) throw new Error("Choose at least one table (hold Ctrl or Shift to choose several).");
    await api("POST", "/api/profile/start", { scope, base_id: $("scopeBase").value, table_ids: tableIds, cap: $("cap").value, save_records: $("saveRecords").checked, download_attachments: $("saveRecords").checked && $("downloadFiles").checked });
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
  if (!running && state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
  if (job && !running && state.lastJobId === job.id && state.lastJobStatus === "running") { await Promise.all([loadResults(), loadRunbook(), loadExtractFiles(), loadAttachmentStats()]); }
  if (job) { state.lastJobId = job.id; state.lastJobStatus = job.status; }
}

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
      el("td", { class: "num", text: f.attachments ? fmt(f.attachments.files) + " (" + fmtBytes(f.attachments.bytes) + ")" + (f.attachments.failed ? ", " + f.attachments.failed + " failed" : "") : "" }), el("td", { text: f.captured_at.replace("T", " ").replace("Z", "") }), el("td", {}, ageTag(f.captured_at)),
      el("td", {}, el("span", { class: "tag " + (bad ? "cap" : f.check === "matches profile" ? "done" : ""), text: f.check })), el("td", {}, el("code", { text: f.sha256.slice(0, 12) }))));
  });
  t.hidden = d.files.length === 0;
  setMsg("exFilesSummary", d.files.length ? d.files.length + " file(s), " + fmt(d.total_records) + " records, " + fmtBytes(d.total_bytes) + " in total." + (d.total_attachment_files || d.total_attachment_failed ? " Attachment files: " + fmt(d.total_attachment_files) + " (" + fmtBytes(d.total_attachment_bytes) + ")" + (d.total_attachment_failed ? ", " + fmt(d.total_attachment_failed) + " failed (see the index files)." : ".") : "") : "No extracted files yet.", "");
}

async function loadAttachmentStats() {
  const q = "?base_id=" + encodeURIComponent($("resBase").value) + "&table_id=" + encodeURIComponent($("resTable").value);
  const d = await api("GET", "/api/attachments/stats" + q);
  const fill = (id, rows) => { const t = $(id), b = t.querySelector("tbody"); clear(b); rows.forEach((r) => b.appendChild(el("tr", {}, ...r))); t.hidden = rows.length === 0; };
  const T = d.total;
  setMsg("attSummary", T.files ? fmt(T.files) + " attachment file(s), " + fmtBytes(T.bytes) + " in total; largest " + fmtBytes(T.max_bytes) + (T.unknown_size ? "; " + fmt(T.unknown_size) + " with no size reported" : "") + "." : "No attachments counted yet (run profiling in step 5).", "");
  const bands = ["under 1 MB", "1-10 MB", "10-100 MB", "100 MB-1 GB", "1 GB or more"];
  fill("attBandTable", T.files ? bands.map((b, i) => [el("td", { text: b }), el("td", { class: "num", text: fmt(T.buckets[i]) })]) : []);
  fill("attTypeTable", d.by_type.map((r) => [el("td", { text: r.extension }), el("td", { class: "num", text: fmt(r.files) }), el("td", { class: "num", text: fmtBytes(r.bytes) }), el("td", { class: "num", text: fmtBytes(r.max_bytes) })]));
  fill("attTableTable", d.by_table.map((r) => [el("td", { text: r.base_id + " / " + r.table_name }), el("td", { class: "num", text: fmt(r.files) }), el("td", { class: "num", text: fmtBytes(r.bytes) }), el("td", { class: "num", text: fmtBytes(r.max_bytes) })]));
  $("lnkAttCsv").href = "/api/export/attachment_stats.csv" + q;
}

async function checkSelectValues() {
  const base = $("resBase").value;
  const t = $("svTable"), body = t.querySelector("tbody"), sk = $("svSkipped");
  clear(body); clear(sk); t.hidden = true;
  if (!base) { setMsg("svSummary", "Choose a base in step 5 (Results) first.", "err"); return; }
  const d = await api("POST", "/api/select-values", { base_id: base });
  d.tables.forEach((tb) => tb.fields.forEach((f) => {
    const ex = f.unmatched.map((u) => u.label + ": " + u.records + " record(s) " + u.examples.join(", ") + (u.near_match_option ? " (near match: " + u.near_match_option + ")" : "")).join("; ");
    body.appendChild(el("tr", {}, el("td", {}, tb.table_name, ageTag(tb.captured_at)), el("td", { text: f.field_name }), el("td", { class: "num", text: fmt(f.declared_options) }), el("td", { class: "num", text: fmt(f.records_total) }), el("td", { class: "num", text: fmt(f.records_total - f.records_with_value) }), el("td", { class: "num", text: fmt(f.observed_values) }),
      el("td", { class: "num" }, f.unmatched_values ? el("span", { class: "tag cap", text: fmt(f.unmatched_values) }) : "0"), el("td", { class: "num", text: fmt(f.unmatched_records) }), el("td", { class: "num", text: fmt(f.near_matches) }),
      el("td", { text: f.unused_options.join(", ") }), el("td", { text: ex })));
  }));
  t.hidden = d.totals.fields === 0;
  const T = d.totals;
  setMsg("svSummary", T.fields + " select field(s) checked: " + T.fields_with_unmatched + " hold values that are not declared options (" + fmt(T.unmatched_values) + " value(s) in " + fmt(T.unmatched_records) + " record(s); " + fmt(T.near_matches) + " near match(es))." +
    (T.tables_not_checked ? " " + T.tables_not_checked + " table(s) not checked (no usable saved file)." : ""), T.fields_with_unmatched ? "err" : "");
  d.not_checked.forEach((n) => sk.appendChild(el("p", { class: "hint", text: "Not checked: " + n.table_name + ". " + n.reason })));
  d.tables.forEach((tb) => tb.notes.forEach((n) => sk.appendChild(el("p", { class: "hint", text: tb.table_name + ": " + n }))));
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

/* ---------- wiring ---------- */
function guard(fn) { return () => fn().catch((e) => showGlobalError(e.message)); }

/* Everything on this page that needs a database is switched off until one is connected on Setup. */
function onReadyChange() {
  for (const id of ["btnIngest", "btnRecompute", "btnReloadSchema", "btnResults", "btnExtractDelete", "btnExtractRefresh"]) $(id).disabled = !state.ready;
  if (!state.pollTimer) $("btnProfile").disabled = !state.ready;
  syncReportLinks();
}

async function afterConnect() {
  await Promise.all([loadSchema(), loadDepgraph(), loadRunbook(), loadProfileStatus(true), loadExtractFiles(), loadAttachmentStats()]);
}

startPage("stage1", { onReadyChange, afterConnect }, () => {
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
  $("btnAttRefresh").addEventListener("click", guard(loadAttachmentStats));
  $("btnGraphSvg").addEventListener("click", downloadGraphSvg);
  $("btnGraphPng").addEventListener("click", downloadGraphPng);
  $("depMode").addEventListener("change", syncModeHelp);
  syncModeHelp();
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
  $("btnSelectValues").addEventListener("click", guard(checkSelectValues));
  initBaseCombo();
  $("resBase").addEventListener("change", syncReportLinks);
  $("csvBase").addEventListener("change", syncReportLinks);
  $("resTable").addEventListener("change", syncReportLinks);
});
