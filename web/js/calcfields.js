"use strict";
/* Calculated fields: the preflight of Airtable formula fields into Workfront Core calculated field expressions. The page asks the server to run the preflight and shows the
   saved answer: field names, formula and expression text, statuses and reasons. It never calls Workfront and holds no token. Everything is inserted with textContent (via el()),
   never innerHTML. */
const ACTOR_KEY = "migrationControl.reviewer";
const cfd = { setup: null, run: null, rep: null, drift: [] };
const SECTIONS = { BLOCKED_MISSING_REFERENCE: ["missGrid", "missCount"], BLOCKED_UNSUPPORTED_FUNCTION: ["unsGrid", "unsCount"], BLOCKED_DEPENDENCY: ["depGrid", "depCount"] };
const LABELS = { TRANSLATABLE: "Can be translated", BLOCKED_MISSING_REFERENCE: "Missing reference or Core name", BLOCKED_UNSUPPORTED_FUNCTION: "Unsupported function", BLOCKED_DEPENDENCY: "Depends on a blocked formula" };
const td = (text, cls) => el("td", { class: cls || null, text: text === null || text === undefined ? "" : String(text) });
const code = (text) => el("td", null, el("code", { text: text === null || text === undefined ? "" : String(text) }));
const envNow = () => $("envSel").value;
const tableNow = () => $("tableSel").value;
const actorNow = () => $("actor").value.trim();
const catNow = () => $("categoryId").value.trim();
const tableInfo = () => (cfd.setup ? cfd.setup.tables.find((t) => t.table_id === tableNow()) : null);

function applyButtons() {
  const why = [];
  if (!cfd.setup || !cfd.setup.db_connected) why.push("Connect a database on Setup.");
  if (!envNow()) why.push("Choose an environment.");
  if (!tableNow()) why.push("Choose a table.");
  if (!/^[A-Za-z0-9]{1,64}$/.test(catNow())) why.push("Enter the custom form (category) ID: letters and digits only.");
  if (!actorNow()) why.push("Enter your initials.");
  $("btnRun").disabled = why.length > 0;
  $("btnRun").title = why.join("\n");
  $("runNote").textContent = why.length ? why[0] : "";

  const run = cfd.rep && cfd.rep.run, awhy = [];
  if (!run) awhy.push("Open a run first.");
  else if (run.state !== "preflight") awhy.push("This run is " + run.state + ", so it cannot be approved.");
  else if (!cfd.rep.counts.TRANSLATABLE) awhy.push("Nothing in this run can be translated.");
  if (run && cfd.drift.length) awhy.push("The schema or crosswalk changed since this run: run a new preflight.");
  if (!actorNow()) awhy.push("Enter your initials.");
  if ($("approveReason").value.trim().length < 3) awhy.push("Give a reason.");
  $("btnApprove").disabled = awhy.length > 0;
  $("approveWrap").title = awhy.join("\n");
}

function fillTable(id, rows) {
  const body = $(id).querySelector("tbody");
  clear(body);
  rows.forEach((cells) => body.appendChild(el("tr", null, cells)));
}

function renderReport() {
  const rep = cfd.rep;
  $("reportCard").hidden = $("approveCard").hidden = !rep;
  if (!rep) return;
  const run = rep.run;
  $("reportTitle").textContent = "run " + run.run_id + ", " + run.environment + ", form " + (run.category_name ? run.category_name + " (" + run.category_id + ")" : run.category_id) + ", " + run.run_at + " UTC by " + run.run_by;
  const counts = $("counts");
  clear(counts);
  counts.appendChild(el("span", { class: "tag", text: fmt(rep.total) + " formula field(s)" }));
  Object.keys(LABELS).forEach((s) => counts.appendChild(el("span", { class: "tag " + (s === "TRANSLATABLE" ? "ok" : "warn"), text: LABELS[s] + ": " + fmt(rep.counts[s]) })));
  $("driftNote").hidden = !cfd.drift.length;
  $("driftNote").textContent = cfd.drift.join(" ") + (cfd.drift.length ? " Run a new preflight." : "");
  $("csvLink").href = "/api/calcfields/report.csv?run_id=" + run.run_id;

  $("okCount").textContent = "(" + fmt(rep.counts.TRANSLATABLE) + ")";
  fillTable("okGrid", rep.translatable.map((d) => [td(d.push_order, "num"), td(d.field_name + " (" + d.field_id + ")"), td(d.target_name + (d.reuse_parameter_id ? " (shared, exists as " + d.reuse_parameter_id + ")" : "")),
    td(d.data_type), td(d.display_type), code(d.source_formula), code(d.expression), td(d.reason)]));
  Object.keys(SECTIONS).forEach((s) => {
    const [grid, count] = SECTIONS[s], list = rep.blocked[s] || [];
    $(count).textContent = "(" + fmt(list.length) + ")";
    fillTable(grid, list.map((d) => [td(d.field_name + " (" + d.field_id + ")"), code(d.source_formula), td(d.reason)]));
  });
  fillTable("fnGrid", rep.functions.map((f) => [td(f.name), td(f.calls, "num"), td(f.formulas.length, "num"), td(f.translated ? "yes (provisional)" : "no: needs a rule, or has no verified equivalent")]));

  const ap = $("approveState");
  if (run.state === "approved") ap.textContent = "Approved by " + run.approved_by + " at " + run.approved_at + " UTC. Reason: " + run.approval_reason;
  else if (run.state === "superseded") ap.textContent = "A newer preflight replaced this run, so it cannot be approved.";
  else ap.textContent = "Waiting for approval. Nothing is created or written in Workfront by approving.";
  applyButtons();
}

async function openRun(id) {
  try {
    const r = await api("GET", "/api/calcfields/report?run_id=" + encodeURIComponent(id));
    cfd.rep = r.report; cfd.drift = r.drift || [];
    setMsg("approveMsg", "", "");
    renderReport();
  } catch (e) { setMsg("pageMessage", e.message, "err"); }
}

async function loadRuns() {
  const t = tableNow();
  $("runsCard").hidden = !t;
  if (!t) return;
  let data;
  try { data = await api("GET", "/api/calcfields/runs?table_id=" + encodeURIComponent(t)); } catch (e) { setMsg("pageMessage", e.message, "err"); return; }
  fillTable("runsGrid", data.runs.map((r) => {
    const n = r.counts || {}, blocked = (n.BLOCKED_MISSING_REFERENCE || 0) + (n.BLOCKED_UNSUPPORTED_FUNCTION || 0) + (n.BLOCKED_DEPENDENCY || 0);
    const open = el("button", { type: "button", text: "Open", "data-run": r.run_id });
    open.addEventListener("click", () => openRun(r.run_id));
    return [td(r.run_id, "num"), td(r.run_at), td(r.run_by), td(r.environment), td((r.category_name ? r.category_name + " " : "") + "(" + r.category_id + ")"),
      td(r.state + (r.approved_by ? " by " + r.approved_by : "")), td(n.TRANSLATABLE || 0, "num"), td(blocked, "num"), el("td", null, open)];
  }));
}

async function runPreflight() {
  setMsg("pageMessage", "", ""); showGlobalError("");
  try {
    const t = tableInfo();
    const r = await api("POST", "/api/calcfields/preflight", { environment: envNow(), base_id: t.base_id, table_id: t.table_id, category_id: catNow(), category_name: $("categoryName").value.trim(), actor: actorNow() });
    try { localStorage.setItem(ACTOR_KEY, actorNow()); } catch (e) { /* storage unavailable: the page still works */ }
    cfd.rep = r.report; cfd.drift = r.drift || [];
    setMsg("approveMsg", "", "");
    renderReport();
    setMsg("pageMessage", "Preflight saved as run " + r.run_id + ". Nothing was sent to Workfront.", "ok");
    await loadRuns();
  } catch (e) { setMsg("pageMessage", e.message, "err"); }
}

async function approve() {
  try {
    const r = await api("POST", "/api/calcfields/approve", { run_id: cfd.rep.run.run_id, actor: actorNow(), reason: $("approveReason").value.trim() });
    cfd.rep = r.report; cfd.drift = r.drift || [];
    renderReport();
    setMsg("approveMsg", "Run " + r.run_id + " approved. Nothing was sent to Workfront.", "ok");
    await loadRuns();
  } catch (e) { setMsg("approveMsg", e.message, "err"); }
}

async function loadSetup() {
  cfd.setup = await api("GET", "/api/calcfields/setup");
  $("displayTypeNote").textContent = cfd.setup.display_type || "";
  const env = $("envSel");
  clear(env);
  env.appendChild(el("option", { value: "", text: "Choose an environment..." }));
  cfd.setup.environments.forEach((e) => env.appendChild(el("option", { value: e, text: e })));
  const sel = $("tableSel");
  clear(sel);
  sel.appendChild(el("option", { value: "", text: cfd.setup.tables.length ? "Choose a table..." : "No table with formula fields in this database" }));
  cfd.setup.tables.forEach((t) => sel.appendChild(el("option", { value: t.table_id, text: t.table_name + " (" + t.base_id + ", " + t.formulas + " formula field" + (t.formulas === 1 ? "" : "s") + ")" })));
  applyButtons();
}

function wire() {
  try { $("actor").value = localStorage.getItem(ACTOR_KEY) || ""; } catch (e) { /* none saved */ }
  ["envSel", "tableSel", "categoryId", "actor", "approveReason"].forEach((id) => { $(id).addEventListener("input", applyButtons); $(id).addEventListener("change", applyButtons); });
  $("tableSel").addEventListener("change", () => { cfd.rep = null; cfd.drift = []; renderReport(); loadRuns(); });
  $("btnRun").addEventListener("click", runPreflight);
  $("btnApprove").addEventListener("click", approve);
}

startPage("calcfields", { afterConnect: () => loadSetup().catch((e) => setMsg("pageMessage", e.message, "err")) }, wire);
