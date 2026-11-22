"use strict";
/* Calculated fields: the preflight of Airtable formula fields into Workfront Core calculated field expressions. The page asks the server to run the preflight and shows the
   saved answer: field names, formula and expression text, statuses and reasons. It never calls Workfront and holds no token. Everything is inserted with textContent (via el()),
   never innerHTML. */
const ACTOR_KEY = "migrationControl.reviewer";
const cfd = { setup: null, run: null, rep: null, drift: [], job: null, timer: null };
const SECTIONS = { BLOCKED_MISSING_REFERENCE: ["missGrid", "missCount"], BLOCKED_UNSUPPORTED_FUNCTION: ["unsGrid", "unsCount"], BLOCKED_DEPENDENCY: ["depGrid", "depCount"] };
const LABELS = { TRANSLATABLE: "Can be translated", BLOCKED_MISSING_REFERENCE: "Missing reference or Core name", BLOCKED_UNSUPPORTED_FUNCTION: "Unsupported function", BLOCKED_DEPENDENCY: "Depends on a blocked formula" };
const td = (text, cls) => el("td", { class: cls || null, text: text === null || text === undefined ? "" : String(text) });
const code = (text) => el("td", null, el("code", { text: text === null || text === undefined ? "" : String(text) }));
const envNow = () => $("envSel").value;
const tableNow = () => $("tableSel").value;
const actorNow = () => $("actor").value.trim();
const catNow = () => $("formSel").value;
const busy = () => !!(cfd.job && cfd.job.status === "running");
const toBase64 = (buf) => { const b = new Uint8Array(buf); let s = ""; for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000)); return btoa(s); };
const tableInfo = () => (cfd.setup ? cfd.setup.tables.find((t) => t.table_id === tableNow()) : null);

function applyButtons() {
  const why = [];
  if (!cfd.setup || !cfd.setup.db_connected) why.push("Connect a database on Setup.");
  if (!envNow()) why.push("Choose an environment.");
  if (!tableNow()) why.push("Choose a table.");
  if (!catNow()) why.push("Choose a custom form from the list (upload the list of valid forms first).");
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
  applyPush(run);
}

function applyPush(run) {
  const open = run && ["approved", "pushing", "pushed"].includes(run.state);
  $("pushCard").hidden = !open;
  if (!open) return;
  const b = busy(), env = run.environment, failed = failedItems().length;
  const why = [];
  if (b) why.push("A push is running.");
  if (!actorNow()) why.push("Enter your initials (recorded with the push).");
  if (env === "production" && $("pushPhrase").value !== cfd.setup.production_phrase) why.push("Type the production phrase.");
  if (!cfd.rep.counts.TRANSLATABLE) why.push("Nothing in this run can be translated.");
  if (cfd.drift.length) why.push("The schema or crosswalk changed since this run: run a new preflight.");
  if (!cfd.setup.forms.some((f) => f.category_id === run.category_id)) why.push("The form " + run.category_id + " is not on the uploaded list of valid forms.");
  const unfinished = cfd.rep.translatable.some((d) => d.push_step !== "verified");
  if (!unfinished) why.push("Everything in this run is already pushed and verified.");
  $("btnPush").disabled = why.length > 0;
  $("pushWrap").title = why.join("\n");
  const rwhy = why.filter((w) => !w.startsWith("Everything"));
  if (!failed) rwhy.push("No item has failed.");
  $("btnRetry").disabled = rwhy.length > 0;
  $("retryWrap").title = rwhy.join("\n");
  $("btnCheckForm").disabled = b || !cfd.setup.forms.some((f) => f.category_id === run.category_id);
  $("checkWrap").title = $("btnCheckForm").disabled ? "The form must be on the list of valid forms, and nothing may be running." : "";
  $("phraseBox").hidden = env !== "production";
  $("btnCancelPush").hidden = !b;
}

const failedItems = () => (cfd.rep ? cfd.rep.translatable.filter((d) => d.push_error) : []);

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
  $("reportTitle").textContent = "run " + run.run_id + ", " + run.environment + ", form " + (run.category_name ? run.category_name + " (" + run.category_id + ")" : run.category_id) + ", " + run.run_at + " UTC by " + run.run_by + ", " + run.state;
  const counts = $("counts");
  clear(counts);
  counts.appendChild(el("span", { class: "tag", text: fmt(rep.total) + " formula field(s)" }));
  Object.keys(LABELS).forEach((s) => counts.appendChild(el("span", { class: "tag " + (s === "TRANSLATABLE" ? "ok" : "warn"), text: LABELS[s] + ": " + fmt(rep.counts[s]) })));
  $("driftNote").hidden = !cfd.drift.length;
  $("driftNote").textContent = cfd.drift.join(" ") + (cfd.drift.length ? " Run a new preflight." : "");
  $("csvLink").href = "/api/calcfields/report.csv?run_id=" + run.run_id;

  $("okCount").textContent = "(" + fmt(rep.counts.TRANSLATABLE) + ")";
  fillTable("okGrid", rep.translatable.map((d) => [td(d.push_order, "num"), td(d.field_name + " (" + d.field_id + ")"), td(d.target_name + (d.reuse_parameter_id ? " (shared, exists as " + d.reuse_parameter_id + ")" : "")),
    td(d.data_type), td(d.display_type), code(d.source_formula), code(d.expression), td(d.reason),
    td(d.push_step && d.push_step !== "none" ? d.push_step + (d.push_error ? ": " + d.push_error : "") : (d.push_error ? "failed: " + d.push_error : ""))]));
  Object.keys(SECTIONS).forEach((s) => {
    const [grid, count] = SECTIONS[s], list = rep.blocked[s] || [];
    $(count).textContent = "(" + fmt(list.length) + ")";
    fillTable(grid, list.map((d) => [td(d.field_name + " (" + d.field_id + ")"), code(d.source_formula), td(d.reason)]));
  });
  fillTable("fnGrid", rep.functions.map((f) => [td(f.name), td(f.calls, "num"), td(f.formulas.length, "num"), td(f.translated ? "yes (provisional)" : "no: needs a rule, or has no verified equivalent")]));

  const ap = $("approveState");
  if (run.state === "approved") ap.textContent = "Approved by " + run.approved_by + " at " + run.approved_at + " UTC. Reason: " + run.approval_reason;
  else if (run.state === "superseded") ap.textContent = "A newer preflight replaced this run, so it cannot be approved.";
  else if (run.state === "pushing" || run.state === "pushed") ap.textContent = "Approved by " + run.approved_by + " at " + run.approved_at + " UTC. Reason: " + run.approval_reason + " The push has " + (run.state === "pushed" ? "finished." : "started and is not finished.");
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
    const r = await api("POST", "/api/calcfields/preflight", { environment: envNow(), base_id: t.base_id, table_id: t.table_id, category_id: catNow(), actor: actorNow() });
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

function renderForms() {
  const forms = cfd.setup.forms || [];
  $("formsWin").hidden = !forms.length;
  fillTable("formsGrid", forms.map((f) => [td(f.category_id), td(f.name)]));
  $("formsNote").textContent = forms.length ? forms.length + " valid form(s) loaded" + (cfd.setup.forms_filename ? " from " + cfd.setup.forms_filename : "") + "." : "No list loaded yet.";
  const sel = $("formSel"), keep = sel.value;
  clear(sel);
  sel.appendChild(el("option", { value: "", text: forms.length ? "Choose a form..." : "Upload the list of valid forms first" }));
  forms.forEach((f) => sel.appendChild(el("option", { value: f.category_id, text: f.name + " (" + f.category_id + ")" })));
  sel.value = forms.some((f) => f.category_id === keep) ? keep : "";
}

async function uploadForms() {
  const f = $("formsFile").files[0];
  if (!f) return;
  setMsg("formsMessage", "", ""); $("formsProblemWin").hidden = true;
  try {
    await api("POST", "/api/calcfields/forms/upload", { csv_base64: toBase64(await f.arrayBuffer()), filename: f.name });
  } catch (e) {
    setMsg("formsMessage", e.message, "err");
    const body = $("formsProblemGrid").querySelector("tbody");
    clear(body);
    (e.problems || []).forEach((p) => body.appendChild(el("tr", null, [td(p.row || "", "num"), td(p.column), td(p.message)])));
    $("formsProblemWin").hidden = !(e.problems && e.problems.length);
    return;
  }
  cfd.setup = await api("GET", "/api/calcfields/setup");
  renderForms(); applyButtons();
  setMsg("formsMessage", cfd.setup.forms.length + " form(s) are valid and loaded.", "ok");
}

async function checkForm() {
  const run = cfd.rep.run;
  if (!window.confirm("Read the form " + run.category_id + " from Workfront (" + run.environment + ")?\n\nThis only READS one form: its name and the number of fields on it. Nothing is changed.\n\nPress Cancel to send nothing.")) { $("formCheck").textContent = "Nothing was sent."; return; }
  try {
    const r = await api("POST", "/api/calcfields/form/check", { run_id: run.run_id, confirm: true });
    $("formCheck").textContent = "Found \"" + r.found_name + "\" with " + r.fields_on_form + " field(s) on it. " + (r.name_matches ? "The name matches the list." : "The name differs from the list (" + r.listed_name + ").");
  } catch (e) { $("formCheck").textContent = e.message; }
}

function stopTimer() { if (cfd.timer) { clearInterval(cfd.timer); cfd.timer = null; } }
function renderJob() {
  const j = cfd.job;
  $("btnCancelPush").hidden = !busy();
  if (!j) { $("pushProgress").textContent = ""; return; }
  const p = j.progress || {};
  $("pushProgress").textContent = j.status === "running" ? ({ create: "Creating fields", write: "Writing expressions" }[p.phase] || "Working") + (p.total ? ": " + fmt(p.done || 0) + " of " + fmt(p.total) : "") + (p.item ? " (" + p.item + ")" : "") : "";
  if (j.status === "running") return;
  const s = j.summary || {}, c = s.counts || {};
  const text = "Push " + j.status + " (run " + j.run_id + "): " + fmt(c.created || 0) + " created, " + fmt(c.reused || 0) + " existing field(s) used, " + fmt(c.attached || 0) + " added to the form, " + fmt(c.written || 0) + " expression(s) written, "
    + fmt(c.verified || 0) + " verified" + ((s.failed || []).length ? ", " + s.failed.length + " failed" : "") + "." + (j.error ? " " + j.error : "") + (s.nothing_to_do ? " Nothing was left to do." : "");
  setMsg("pushMsg", text, j.status === "done" && !(s.failed || []).length ? "ok" : "err");
  const notes = (s.form_notes || []);
  $("formNotes").hidden = !notes.length;
  $("formNotes").textContent = notes.join(" ");
}

async function pollPush() {
  cfd.job = (await api("GET", "/api/calcfields/push/status")).job;
  renderJob();
  if (!busy()) {
    stopTimer();
    if (cfd.rep && cfd.job && cfd.job.run_id === cfd.rep.run.run_id) { const r = await api("GET", "/api/calcfields/report?run_id=" + cfd.rep.run.run_id); cfd.rep = r.report; cfd.drift = r.drift || []; renderReport(); await loadRuns(); }
  }
  applyButtons();
}
function followPush() { stopTimer(); cfd.timer = setInterval(() => pollPush().catch((e) => { stopTimer(); setMsg("pushMsg", e.message, "err"); }), 1000); }

async function startPush(onlyFailed) {
  const run = cfd.rep.run;
  const ids = onlyFailed ? failedItems().map((d) => d.item_id) : null;
  const what = onlyFailed ? "Retry " + ids.length + " failed item(s)" : "Push " + cfd.rep.counts.TRANSLATABLE + " calculated field(s)";
  if (!window.confirm(what + " in Workfront Core (" + run.environment + ")?\n\nThis CREATES fields, ADDS them to the form " + run.category_id + " and WRITES their expressions. Fields already on the form are kept exactly as they are; a field that already exists with the same name and the right type is used, not created.\n\nPress Cancel to send nothing.")) { setMsg("pushMsg", "Nothing was sent.", "warn"); return; }
  setMsg("pushMsg", "", ""); $("formNotes").hidden = true;
  await api("POST", "/api/calcfields/push", { run_id: run.run_id, actor: actorNow(), confirm: true, confirm_phrase: $("pushPhrase").value, item_ids: ids });
  await pollPush(); followPush();
}

async function loadSetup() {
  cfd.setup = await api("GET", "/api/calcfields/setup");
  renderForms();
  $("displayTypeNote").textContent = cfd.setup.display_type || "";
  const env = $("envSel");
  clear(env);
  env.appendChild(el("option", { value: "", text: "Choose an environment..." }));
  cfd.setup.environments.forEach((e) => env.appendChild(el("option", { value: e, text: e })));
  const sel = $("tableSel");
  clear(sel);
  sel.appendChild(el("option", { value: "", text: cfd.setup.tables.length ? "Choose a table..." : "No table with formula fields in this database" }));
  cfd.setup.tables.forEach((t) => sel.appendChild(el("option", { value: t.table_id, text: t.table_name + " (" + t.base_id + ", " + t.formulas + " formula field" + (t.formulas === 1 ? "" : "s") + ")" })));
  applyButtons();  await pollPush();
  if (busy()) followPush();
}

function wire() {
  try { $("actor").value = localStorage.getItem(ACTOR_KEY) || ""; } catch (e) { /* none saved */ }
  ["envSel", "tableSel", "formSel", "actor", "approveReason"].forEach((id) => { $(id).addEventListener("input", applyButtons); $(id).addEventListener("change", applyButtons); });
  $("tableSel").addEventListener("change", () => { cfd.rep = null; cfd.drift = []; renderReport(); loadRuns(); });
  $("btnRun").addEventListener("click", runPreflight);
  $("btnApprove").addEventListener("click", approve);
  $("formsFile").addEventListener("change", () => { $("btnForms").disabled = !$("formsFile").files.length; });
  $("btnForms").addEventListener("click", () => uploadForms().catch((e) => setMsg("formsMessage", e.message, "err")));
  $("btnCheckForm").addEventListener("click", () => checkForm().catch((e) => { $("formCheck").textContent = e.message; }));
  $("btnPush").addEventListener("click", () => startPush(false).catch((e) => setMsg("pushMsg", e.message, "err")));
  $("btnRetry").addEventListener("click", () => startPush(true).catch((e) => setMsg("pushMsg", e.message, "err")));
  $("btnCancelPush").addEventListener("click", () => api("POST", "/api/calcfields/push/cancel", {}).then(() => { $("pushProgress").textContent = "Stopping after the current call..."; }).catch((e) => setMsg("pushMsg", e.message, "err")));
  $("pushPhrase").addEventListener("input", applyButtons);
}

startPage("calcfields", { afterConnect: () => loadSetup().catch((e) => setMsg("pageMessage", e.message, "err")) }, wire);
