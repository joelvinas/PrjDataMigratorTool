"use strict";
/* Load a table: one table into Workfront Planning, step by step. The page only asks the server to do each step (the same code loader.py runs) and shows the answer.
   Everything shown is an ID, a schema name, a count, a status or a short scrubbed reason: never a record value and never a token (the token never leaves the server).
   Nothing runs when the page opens. Every send goes through a confirmation that names the table, the stage and the Workspace ID. */
const lt = {
  setup: null,        // {configured, problem, workspace_id, stages, phrases, tables}
  st: null,           // the last check: {check, steps, gates}
  dry: {},            // which dry runs were done in this visit (a send needs its dry run first); cleared when the choice changes
  job: null,          // the job being followed
  timer: null,
  last: {},           // the last finished job per step, to draw its result again after a refresh
};
const baseNow = () => $("baseId").value.trim();
const stageNow = () => $("stageSel").value;
const tableNow = () => $("tableSel").value;
const tableRow = () => ((lt.setup && lt.setup.tables) || []).find((t) => t.table_id === tableNow());
const tableName = () => { const t = tableRow(); return t ? t.name : tableNow(); };

const OUTCOME = { created: "created", already_created: "already created", adopted: "adopted", failed: "failed", would_create: "would create", not_ready: "on hold", parent_missing: "waiting for its parent", no_transport: "no transport" };
const KIND = { record_type: "record type", field: "field", option: "select option", form: "form" };

function fillGrid(tableId, winId, rows, cells) {
  const t = $(tableId), body = t.querySelector("tbody");
  clear(body);
  rows.forEach((r) => body.appendChild(el("tr", {}, cells(r))));
  $(winId).hidden = rows.length === 0;
  applySort(tableId);
}
const td = (text, cls, sort) => el("td", { class: cls || null, text: text === null || text === undefined ? "" : String(text), "data-sort": sort === undefined ? null : String(sort) });
const countsText = (c) => Object.keys(c || {}).sort().map((k) => (OUTCOME[k] || k) + " " + fmt(c[k])).join(", ") || "nothing";

function pill(n, text, cls) { const p = $("pill" + n); p.textContent = text; p.className = "tag" + (cls ? " " + cls : ""); }

/* ---------- what is open: every button follows the state the server read from the database ---------- */
function applyGates() {
  const g = lt.st ? lt.st.gates : {}, s = lt.st ? lt.st.steps : null;
  const busy = !!(lt.job && lt.job.status === "running");
  const on = (id, v) => { $(id).disabled = !v; };
  on("btnCheck", !!baseNow() && !!tableNow() && !!lt.setup && lt.setup.configured && !busy);
  on("btnStructDry", g.can_structure && !busy);
  on("btnStructSend", g.can_structure && lt.dry.structure && !busy);
  on("btnFile", g.can_records && !busy);
  on("btnRecDry", g.can_records && s && s.records.file_exists && !busy);
  on("btnRecSend", g.can_records && s && s.records.file_exists && lt.dry.records && !busy);
  on("btnLinkDry", g.can_links && !busy);
  on("btnLinkSend", g.can_links && lt.dry.links && !busy);
  on("btnVerify", g.can_verify && !busy);
  on("btnResetDry", g.can_reset && !busy);
  $("resetPhrase").disabled = !(g.can_reset && lt.dry.reset && !busy);
  on("btnResetSend", g.can_reset && lt.dry.reset && !busy && $("resetPhrase").value === phrase());
  if (!lt.st) { [1, 2, 3, 4, 5, 6].forEach((n) => pill(n, n === 1 ? "not run" : "locked")); return; }
  const c = lt.st.check, d = (ok, open) => (ok ? ["done", "done"] : open ? ["open", "run"] : ["locked", ""]);
  pill(1, c.can_continue ? "clear" : "blocked", c.can_continue ? "done" : "fail");
  pill(2, ...d(s.structure.done, g.can_structure));
  pill(3, ...d(s.records.complete, g.can_records));
  pill(4, ...(s.links.applicable ? d(s.links.done, g.can_links) : ["nothing to do", g.can_verify ? "done" : ""]));
  pill(5, ...(g.can_verify ? ["open", "run"] : ["locked", ""]));
  pill(6, g.can_reset ? "open" : "locked", "");
}
const phrase = () => (lt.setup && lt.setup.phrases && lt.setup.phrases[stageNow()]) || "";

function resetChoice() {          // the base, stage or table changed: everything shown belongs to the old choice
  lt.st = null; lt.dry = {}; lt.last = {};
  ["reasonWin", "depWin", "depHead", "structWin", "mapWin", "recFailWin", "waitWin", "linkFailWin", "verifyWin"].forEach((id) => { $(id).hidden = true; });
  ["checkMsg", "structMsg", "fileMsg", "recMsg", "linkMsg", "verifyMsg", "resetMsg"].forEach((id) => setMsg(id, "", ""));
  $("resetPhrase").value = "";
  $("resetPhraseShown").textContent = phrase();
  const t = tableRow();
  $("tableInfo").textContent = t ? "scope: " + t.scope + "; lands in: " + (t.target === "planning_only" ? "Planning only" : t.target === "planning_and_core" ? "Planning and Core" : "not decided") + "; table name " + (t.name_approved ? "approved" : "NOT approved") : "";
  applyGates();
}

/* ---------- setup (a read; nothing is sent) ---------- */
async function loadSetup() {
  const d = await api("GET", "/api/loadtable/setup?base_id=" + encodeURIComponent(baseNow()));
  lt.setup = d;
  showGlobalError("");
  $("wsId").textContent = d.workspace_id || "(not configured)";
  setMsg("pageMessage", d.configured ? (d.tables_problem || "") : d.problem + " Add the Planning settings to config.json (see config.example.json), then reload this page.", d.configured && !d.tables_problem ? "" : d.configured ? "warn" : "err");
  const sel = $("tableSel"), keep = sel.value;
  clear(sel);
  sel.appendChild(el("option", { value: "", text: baseNow() ? (d.tables.length ? "Choose a table..." : "No in-scope table in this base") : "Choose a base first" }));
  d.tables.forEach((t) => sel.appendChild(el("option", { value: t.table_id, text: t.name + " (" + t.table_id + ") - " + (t.name_approved ? "name approved" : "name NOT approved") })));
  sel.value = d.tables.some((t) => t.table_id === keep) ? keep : "";
  resetChoice();
  await followRunningJob();
}

/* ---------- step 1 and the state of every step ---------- */
const body = (extra) => Object.assign({ base_id: baseNow(), table_id: tableNow(), stage: stageNow() }, extra || {});

async function refreshState(showCheck) {
  const st = await api("POST", "/api/loadtable/check", body());
  lt.st = st;
  if (showCheck) renderCheck(st.check);
  renderFile(st.steps.records);
  renderLinksState(st.steps.links);
  applyGates();
  return st;
}

function renderCheck(c) {
  const k = c.counts;
  setMsg("checkMsg", c.can_continue
    ? "Clear: " + fmt(k.ready) + " item(s) ready, " + fmt(k.hold) + " on hold (skipped), 0 blocked. Step 2 is open."
    : "Blocked: " + fmt(k.blocked) + " item(s) blocked (" + fmt(k.ready) + " ready, " + fmt(k.hold) + " on hold). Fix what is listed below, then check again. Nothing below opens until then.", c.can_continue ? "ok" : "err");
  fillGrid("reasonGrid", "reasonWin", c.reasons, (r) => [td(r.status), td(r.reason), td(r.count, "num", r.count), td(r.examples.join(", "))]);
  $("depHead").hidden = c.dependencies.length === 0;
  fillGrid("depGrid", "depWin", c.dependencies, (d) => [td(d.name + " (" + d.table_id + ")"), td(d.link_fields, "num", d.link_fields), td(d.record_type || "not created yet"), td(fmt(d.records_done) + " of " + fmt(d.registered), "num", d.records_done),
    td(d.loaded ? "yes" : "no - links to it wait")]);
}

function renderFile(r) {
  if (!lt.st || !lt.st.gates.can_records) { setMsg("fileMsg", "", ""); return; }
  setMsg("fileMsg", r.file_exists
    ? "Saved file found: " + fmt(r.file_records) + " record(s). Registered in this tool: " + fmt(r.registered) + "; records pass done: " + fmt(r.done) + "; still to send: " + fmt(r.pending || Math.max(0, r.file_records - r.registered)) + "."
    : (r.file_note || "There is no saved record file for this table."), r.file_exists ? "ok" : "err");
}

function renderLinksState(l) {
  if (!l.applicable) { setMsg("linkMsg", "This table has no link fields to set (or none is ready): nothing to do in this step.", ""); $("waitWin").hidden = true; return; }
  fillGrid("waitGrid", "waitWin", l.waiting_on, (w) => [td(w.name + " (" + w.table_id + ")"), td(w.ready_links, "num", w.ready_links), td(w.records_done, "num", w.records_done), td(w.registered, "num", w.registered)]);
  if (l.waiting_on.length) setMsg("linkMsg", fmt(l.waiting_on.length) + " linked table(s) are not fully loaded yet; links to their records wait for a later run.", "warn");
}

/* ---------- the Workfront ID map ---------- */
async function loadMap() {
  const m = await api("POST", "/api/loadtable/mapping", body());
  fillGrid("mapGrid", "mapWin", m.rows, (r) => [td(r.kind), td(r.airtable_field_id), td(r.airtable_name), td(r.new_name), td(r.state === "created" ? "created" : r.plan_status === "ready" ? r.state : "on hold / blocked"), td(r.workfront_id), td(r.options)]);
}

/* ---------- jobs: start, follow, cancel ---------- */
function askConfirm(what, step) {
  return new Promise((resolve) => {
    const dlg = $("confirmDlg");
    $("confirmTitle").textContent = step === "reset" ? "Delete records in Workfront" : "Send to Workfront";
    $("confirmText").textContent = what;
    $("confirmTable").textContent = tableName() + " (" + tableNow() + ")";
    $("confirmStage").textContent = stageNow().toUpperCase();
    $("confirmWs").textContent = lt.setup.workspace_id;
    const done = (v) => { dlg.close(); $("confirmYes").onclick = null; $("confirmNo").onclick = null; dlg.onclose = null; resolve(v); };
    $("confirmYes").onclick = () => done(true);
    $("confirmNo").onclick = () => done(false);
    dlg.onclose = () => resolve(false);
    dlg.showModal();
    $("confirmNo").focus();
  });
}

const WHAT = {
  structure: "Create this table's record type, fields and select options in Workfront Planning (and an empty record type for each table its links point to).",
  records: "Create this table's records in its Planning record type, in batches.",
  links: "Set the link fields of this table's records.",
  reset: "DELETE every record of this table's record type. This cannot be undone.",
};

async function startJob(step, send, extra) {
  if (send && !(await askConfirm(WHAT[step], step))) return;
  setMsg("pageMessage", "", "");
  const b = body({ step, send });
  if (send) b.confirm = { table_id: tableNow(), stage: stageNow(), workspace_id: lt.setup.workspace_id };
  Object.assign(b, extra || {});
  const r = await api("POST", "/api/loadtable/start", b);
  lt.job = { id: r.job_id, status: "running", step, send, progress: {} };
  applyGates();
  poll();
}

function progressText(j) {
  const p = j.progress || {};
  if (j.step === "structure") return p.total ? "item " + fmt(p.done) + " of " + fmt(p.total) + " (" + countsText(p.counts) + ")" : "starting";
  if (j.step === "reset") return p.round ? "call " + fmt(p.round) + ": " + fmt(p.deleted) + " deleted, " + fmt(p.failed) + " refused" : "starting";
  if (!p.table_id) return "starting";
  const done = p.pass === "links" ? "updated " + fmt(p.updated) + ", waiting " + fmt(p.waiting) : "created " + fmt(p.created);
  return "table " + (p.name || p.table_id) + " (" + p.table_id + "), batch " + fmt(p.batch) + " of " + fmt(p.batches) + ": " + done + ", failed " + fmt(p.failed);
}

function showJob(j) {
  $("jobPanel").hidden = false;
  $("jobTitle").textContent = (j.status === "running" ? "Running: " : j.status === "cancelled" ? "Stopped: " : j.status === "failed" ? "Failed: " : "Finished: ") + j.step + (j.send ? "" : " (dry run)");
  $("jobProgress").textContent = j.status === "failed" ? (j.error || "") : progressText(j);
  $("btnCancel").hidden = j.status !== "running";
  $("jobHint").hidden = j.status !== "running";
}

async function poll() {
  clearTimeout(lt.timer);
  let j;
  try { j = (await api("GET", "/api/loadtable/status")).job; } catch (e) { showGlobalError(e.message); return; }
  if (!j) { lt.job = null; $("jobPanel").hidden = true; applyGates(); return; }
  lt.job = j;
  showJob(j);
  if (j.status === "running") { lt.timer = setTimeout(poll, 350); applyGates(); return; }
  await finishJob(j);
}

async function finishJob(j) {
  const same = j.base_id === baseNow() && j.table_id === tableNow() && j.stage === stageNow();
  if (same) {
    lt.last[j.step] = j;
    if (j.send) lt.dry[j.step] = false; else if (j.status === "done") lt.dry[j.step] = true;
    try { await refreshState(false); if (j.step === "structure" || j.step === "reset") await loadMap(); } catch (e) { showGlobalError(e.message); }
    renderResult(j);       // after the state is read, so the message and the buttons change together
  }
  applyGates();
}

async function followRunningJob() {      // a page opened (or reloaded) while a step is running just follows it; it never starts one
  try {
    const j = (await api("GET", "/api/loadtable/status")).job;
    if (j && j.status === "running") { lt.job = j; showJob(j); poll(); }
  } catch (e) { /* the status call is only a convenience here */ }
}

/* ---------- showing a result ---------- */
function failMsg(j) { return j.status === "failed" ? "The step stopped: " + j.error : ""; }

function renderResult(j) {
  const r = j.result || {};
  if (j.step === "structure") {
    if (j.status === "failed") return setMsg("structMsg", failMsg(j), "err");
    const bad = (r.counts && r.counts.failed) || 0;
    setMsg("structMsg", (r.dry_run ? "Dry run (nothing was sent): " : r.cancelled ? "Stopped part way: " : "Done: ") + countsText(r.counts) + "." + (r.unfinished_before && r.unfinished_before.length ? " " + r.unfinished_before.length + " item(s) from an earlier run were started and not finished; they were tried again." : "")
      + (r.dependency_tables && r.dependency_tables.length ? " Empty record type(s) for linked table(s): " + r.dependency_tables.join(", ") + "." : "") + (bad ? " Fix the cause shown in the detail column and run it again; only what failed is tried again." : ""), bad || r.cancelled ? "warn" : "ok");
    fillGrid("structGrid", "structWin", r.items || [], (x) => [td(KIND[x.kind] || x.kind), td(x.airtable_field_id), td(x.name), td(OUTCOME[x.outcome] || x.outcome), td(x.detail)]);
  } else if (j.step === "records") {
    if (j.status === "failed") return setMsg("recMsg", failMsg(j), "err");
    if (r.dry_run) setMsg("recMsg", "Dry run (nothing was sent): " + fmt(r.in_file) + " record(s) in the saved file; " + fmt(r.registered) + " registered, " + fmt(r.done) + " already loaded; would send " + fmt(r.would_send) + " in " + fmt(r.batches) + " call(s) of up to " + fmt(r.batch_size) + ".", "ok");
    else setMsg("recMsg", (r.cancelled ? "Stopped between batches: " : "Done: ") + fmt(r.created) + " created, " + fmt(r.failed) + " failed" + (r.problems ? ", " + fmt(r.problems) + " value(s) did not fit their field and were left out" : "") + "." + (r.note ? " " + r.note : "")
      + (r.failures_total > r.failures.length ? " Showing the first " + fmt(r.failures.length) + " of " + fmt(r.failures_total) + " failures." : "") + (r.failed ? " Failed records stay pending and are tried again on the next run." : ""), r.failed || r.cancelled ? "warn" : "ok");
    fillGrid("recFailGrid", "recFailWin", r.failures || [], (x) => [td(x.record_id), td(x.reason)]);
  } else if (j.step === "links") {
    if (j.status === "failed") return setMsg("linkMsg", failMsg(j), "err");
    setMsg("linkMsg", r.dry_run ? "Dry run (nothing was sent): " + fmt(r.pending) + " record(s) are waiting for their links." : (r.cancelled ? "Stopped: " : "Done: ") + fmt(r.updated) + " updated, " + fmt(r.waiting) + " waiting for a linked record that is not loaded yet, " + fmt(r.failed) + " failed." + (r.note ? " " + r.note : ""),
      r.failed || r.waiting || r.cancelled ? "warn" : "ok");
    fillGrid("linkFailGrid", "linkFailWin", r.failures || [], (x) => [td(x.record_id), td(x.reason)]);
  } else if (j.step === "reset") {
    if (j.status === "failed") return setMsg("resetMsg", failMsg(j), "err");
    const w = r.wipe;
    if (r.dry_run) setMsg("resetMsg", "Dry run (nothing was sent): this tool holds " + fmt(r.before.mapped) + " mapped Planning record(s) for the table. The reset would delete every record of the record type in Workfront, then clear these.", "ok");
    else if (w && w.left === 0) setMsg("resetMsg", fmt(w.deleted) + " record(s) deleted. The record type is empty and this tool's memory of its records was cleared (" + fmt(r.cleared.mappings) + " mapping(s), " + fmt(r.cleared.markers) + " marker(s)). Load it again from step 3.", "ok");
    else setMsg("resetMsg", (w && w.cancelled ? "Stopped: " : "Not finished: ") + fmt(w ? w.deleted : 0) + " deleted, " + fmt(w ? w.failed : 0) + " refused; the record type is NOT confirmed empty, so this tool's memory was kept. Run the reset again.", "warn");
  }
}

/* ---------- step 5 ---------- */
async function runVerify() {
  setMsg("verifyMsg", "Reading the counts...", "");
  const v = await api("POST", "/api/loadtable/verify", body());
  fillGrid("verifyGrid", "verifyWin", v.counts, (c) => [td(c.label), td(c.count === null ? "not available" : fmt(c.count), "num", c.count === null ? -1 : c.count)]);
  setMsg("verifyMsg", v.match ? "All counts agree." : v.differences.length ? v.differences.map((d) => d.what + " by " + fmt(Math.abs(d.difference)) + " (" + fmt(d.found) + " instead of " + fmt(d.expected) + ")").join("; ") + ". Nothing was changed." : (v.note || "Workfront's count could not be compared."), v.match ? "ok" : "warn");
}

/* ---------- wiring ---------- */
const guarded = (fn) => () => fn().catch((e) => { showGlobalError(e.message); applyGates(); });
async function afterConnect() { await loadKnownBases(); await loadSetup(); }

startPage("loadtable", { afterConnect }, () => {
  initBaseCombo();
  ["reasonGrid", "depGrid", "structGrid", "mapGrid", "recFailGrid", "waitGrid", "linkFailGrid"].forEach(watchSort);
  $("baseId").addEventListener("input", guarded(loadSetup));
  $("stageSel").addEventListener("change", () => { resetChoice(); });
  $("tableSel").addEventListener("change", () => { resetChoice(); });
  $("btnCheck").addEventListener("click", guarded(async () => { showGlobalError(""); await refreshState(true); if (lt.st.steps.structure.record_type || lt.st.steps.structure.created) await loadMap(); }));
  $("btnStructDry").addEventListener("click", guarded(() => startJob("structure", false)));
  $("btnStructSend").addEventListener("click", guarded(() => startJob("structure", true)));
  $("btnFile").addEventListener("click", guarded(() => refreshState(false)));
  $("btnRecDry").addEventListener("click", guarded(() => startJob("records", false)));
  $("btnRecSend").addEventListener("click", guarded(() => startJob("records", true)));
  $("btnLinkDry").addEventListener("click", guarded(() => startJob("links", false)));
  $("btnLinkSend").addEventListener("click", guarded(() => startJob("links", true)));
  $("btnVerify").addEventListener("click", guarded(runVerify));
  $("btnResetDry").addEventListener("click", guarded(() => startJob("reset", false)));
  $("resetPhrase").addEventListener("input", applyGates);
  $("btnResetSend").addEventListener("click", guarded(() => startJob("reset", true, { phrase: $("resetPhrase").value })));
  $("btnCancel").addEventListener("click", guarded(async () => { await api("POST", "/api/loadtable/cancel", {}); $("jobProgress").textContent = "Stopping after the current batch..."; }));
});
