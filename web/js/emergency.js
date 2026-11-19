"use strict";
/* Emergency mode: one table into the dev Workspace in one click. The page only asks the server to run it and shows the answer: field names, IDs, counts, outcomes and short
   scrubbed reasons. No record value and no token is ever in a reply. */
const em = { setup: null, job: null, timer: null };
const baseNow = () => $("baseId").value.trim();
const tableNow = () => $("tableSel").value;
const wsNow = () => (em.setup && em.setup.workspace_id) || "";

const td = (text, cls, sort) => el("td", { class: cls || null, text: text === null || text === undefined ? "" : String(text), "data-sort": sort === undefined ? null : String(sort) });
const OUT_ROW = { clean: "out-ok", "pre-converted": "out-hold", failed: "out-fail" };

function applyGo() {
  const busy = !!(em.job && em.job.status === "running");
  $("btnGo").disabled = !(em.setup && em.setup.configured && baseNow() && tableNow()) || busy;
  $("btnReset").disabled = $("btnGo").disabled;
  $("btnForget").disabled = busy;
  $("btnCancel").hidden = !busy;
}

async function loadSetup() {
  const d = await api("GET", "/api/emergency/setup?base_id=" + encodeURIComponent(baseNow()));
  em.setup = d;
  showGlobalError("");
  $("wsId").textContent = d.workspace_id || "(not configured)";
  setMsg("pageMessage", d.configured ? (d.tables_problem || "") : d.problem, d.configured && !d.tables_problem ? "" : d.configured ? "warn" : "err");
  const sel = $("tableSel"), keep = sel.value;
  clear(sel);
  sel.appendChild(el("option", { value: "", text: baseNow() ? (d.tables.length ? "Choose a table..." : "No in-scope table in this base") : "Choose a base first" }));
  d.tables.forEach((t) => sel.appendChild(el("option", { value: t.table_id, text: t.airtable_name + " (" + t.table_id + ")" })));
  sel.value = d.tables.some((t) => t.table_id === keep) ? keep : "";
  applyGo();
  await follow();
}

function fileLink(name, label) { return el("a", { href: "/api/emergency/report?file=" + encodeURIComponent(name), text: label }); }

function renderResult(j) {
  const r = j.result;
  $("resultCard").hidden = false;
  if (!r) { setMsg("resultMsg", j.error || "The run did not finish.", "err"); return; }
  const c = r.counts;
  const head = j.status === "cancelled" ? "Stopped before it finished. " : "";
  const rt = r.record_type;
  setMsg("resultMsg", rt.outcome === "failed" ? "The record type could not be created, so nothing else was tried: " + rt.error
    : head + "Table " + r.table_name + " went into Workspace " + r.workspace_id + " (record type " + rt.outcome + ").", rt.outcome === "failed" || c.failed || c.records_failed ? "warn" : "ok");
  const body = $("countGrid").querySelector("tbody");
  clear(body);
  [["Fields pushed clean", c.clean], ["Fields converted to PRE- text", c["pre-converted"]], ["Fields skipped", c.skipped], ["Fields failed (both attempts refused)", c.failed],
   ["Records loaded", c.records_loaded], ["Records already loaded by an earlier run", c.records_already_loaded], ["Records failed", c.records_failed],
   ["Values that could not be sent as the field's type", c.values_not_converted]].forEach((p) => body.appendChild(el("tr", null, [td(p[0]), td(fmt(p[1]), "num")])));
  const f = r.files, p = $("files");
  clear(p);
  if (f) {
    p.appendChild(document.createTextNode("Reports (also saved in the out/emergency folder): "));
    [[f.fields, "field report (.mhtml)"], [f.apilog, "API log report (.mhtml)"], [f.csv, "field results (.csv)"]].forEach((x, i) => {
      if (i) p.appendChild(document.createTextNode(" | "));
      p.appendChild(fileLink(x[0], x[1]));
    });
  }
  const notes = $("notes");
  clear(notes);
  const pr = r.prepare;
  if (pr) notes.appendChild(el("div", { class: "hint", text: "Preparatory step: AirtableRecordId " + (pr.primary ? "is the primary field" : "is NOT the primary field") + "; " + pr.deleted.length + " default field(s) deleted"
    + (pr.deleted.length ? " (" + pr.deleted.map((x) => x.name).sort().join(", ") + ")" : "") + (pr.error ? ". " + pr.error : "") + "." }));
  (r.notes || []).forEach((n) => notes.appendChild(el("div", { class: "banner warn", text: n })));
  if (r.db_note) notes.appendChild(el("div", { class: "hint", text: r.db_note }));
  const rows = r.fields.slice().sort((a, b) => a.field_name.toLowerCase().localeCompare(b.field_name.toLowerCase()) || (a.field_id < b.field_id ? -1 : 1));
  const tb = $("fieldGrid").querySelector("tbody");
  clear(tb);
  rows.forEach((x) => {
    const err = x.attempt1 === "refused" ? x.attempted_type + ": " + (x.attempt1_error || x.attempt1_error_code) : "";
    const err2 = x.attempt2 === "refused" ? x.attempt2_type + ": " + (x.attempt2_error || x.attempt2_error_code) : "";
    tb.appendChild(el("tr", { class: OUT_ROW[x.outcome] || null }, [td(x.field_name + " (" + x.field_id + ")"), td(x.airtable_type), td(x.sent_name), td(x.attempted_type), td(x.outcome), td(x.final_name), td(x.final_type),
      td([x.reason, err, err2].filter(Boolean).join("; ")), td(x.max_length === null ? "" : x.max_length, "num", x.max_length === null ? -1 : x.max_length)]));
  });
  $("fieldWin").hidden = rows.length === 0;
  applySort("fieldGrid");
}

function renderJob(j) {
  em.job = j;
  const p = j.progress || {};
  $("progress").textContent = j.status === "running"
    ? ({ fields: "Creating fields", records: "Loading records", reread: "Reading the table from Airtable" }[p.phase] || "Working") + (p.total ? ": " + fmt(p.done || 0) + " of " + fmt(p.total) : "") + (p.item && p.phase === "fields" ? " (" + p.item + ")" : "") : "";
  if (j.status !== "running") { stopTimer(); renderResult(j); }
  applyGo();
}

function stopTimer() { if (em.timer) { clearInterval(em.timer); em.timer = null; } }
async function poll() {
  const s = await api("GET", "/api/emergency/status");
  if (s.job) renderJob(s.job);
}
async function follow() {
  const s = await api("GET", "/api/emergency/status");
  if (!s.job) return;
  renderJob(s.job);
  if (s.job.status === "running" && !em.timer) em.timer = setInterval(() => poll().catch((e) => { stopTimer(); showGlobalError(e.message); }), 1500);
}

async function go() {
  const t = (em.setup.tables || []).find((x) => x.table_id === tableNow());
  const name = t ? t.airtable_name : tableNow();
  if (!window.confirm("Send the table \"" + name + "\" (" + tableNow() + ") to the dev Workspace " + wsNow() + "?\n\nThis creates a record type and fields and loads records in Workfront Planning.")) return;
  showGlobalError("");
  setMsg("resetMsg", "", ""); $("forgetRow").hidden = true;
  $("resultCard").hidden = true;
  await api("POST", "/api/emergency/start", { base_id: baseNow(), table_id: tableNow(), confirm: { table_id: tableNow(), workspace_id: wsNow() } });
  await follow();
  if (!em.timer && em.job && em.job.status === "running") em.timer = setInterval(() => poll().catch((e) => { stopTimer(); showGlobalError(e.message); }), 1500);
}

/* ---------- reset: delete the record type, then forget the IDs (a refusal is an answer, not an error) ---------- */
const forgotten = (c) => fmt(c.load_id_map) + " recorded item(s), " + fmt(c.mappings) + " record ID(s), " + fmt(c.markers) + " marker(s), " + fmt(c.option_ids) + " option ID(s)";
const resetBody = (send) => ({ base_id: baseNow(), table_id: tableNow(), send, confirm: { table_id: tableNow(), workspace_id: wsNow() } });
const tableLabel = () => { const t = (em.setup.tables || []).find((x) => x.table_id === tableNow()); return (t ? t.airtable_name : tableNow()) + " (" + tableNow() + ")"; };
const linkWarn = (r) => (r.inbound || []).length ? "\n\nLink fields of other tables point at this one (" + r.inbound.map((x) => x.name + " x " + fmt(x.fields)).join(", ") + "); Workfront may refuse the delete because of them." : "";

function showReset(r) {
  const a = r.attempt;
  $("forgetRow").hidden = true;
  if (a && !a.deleted) {
    $("forgetRow").hidden = false;
    setMsg("resetMsg", "Workfront did NOT delete the record type" + (a.status ? " (HTTP " + a.status + ")" : "") + ": " + (a.reason || "no reason given") + " The IDs this tool holds were kept. Fix the cause and press Reset table again, delete the record type by hand in Workfront, or use \"Forget this table's IDs anyway\"." + linkWarn(r).replace(/\n\n/, " "), "err");
    return;
  }
  $("resultCard").hidden = true;
  setMsg("resetMsg", (a ? (a.already_gone ? "The record type was already gone from Workfront. " : "Record type deleted in Workfront. ") : "No record type ID was recorded, so nothing was deleted in Workfront (if an interrupted run created one, delete it by hand). ")
    + "Forgot " + forgotten(r.cleared) + ". Press Go to build the table again.", "ok");
}

async function resetTable() {
  showGlobalError(""); setMsg("resetMsg", "", ""); $("forgetRow").hidden = true;
  const peek = await api("POST", "/api/emergency/reset", resetBody(false));        // this tool's own counts: nothing is sent
  const b = peek.before;
  if (!window.confirm("Reset the table \"" + tableLabel() + "\" in the dev Workspace " + wsNow() + "?\n\nThis tool holds " + fmt(b.map_total) + " recorded item(s), " + fmt(b.mapped_records) + " record ID(s), " + fmt(b.markers) + " marker(s) and " + fmt(b.option_ids) + " option ID(s) for it. "
    + (b.record_type ? "It will try to DELETE record type " + b.record_type + " in Workfront Planning, with its fields and records, then forget these. This cannot be undone." : "No record type ID is recorded, so nothing will be deleted in Workfront; these will be forgotten.") + linkWarn(peek))) return;
  showReset(await api("POST", "/api/emergency/reset", resetBody(true)));
}

async function forgetTable() {
  if (!window.confirm("Forget every ID this tool holds for \"" + tableLabel() + "\" in the dev Workspace " + wsNow() + "?\n\nNothing is sent to Workfront. A record type still there will cause a name clash on the next Go.")) return;
  const r = await api("POST", "/api/emergency/forget", resetBody(true));
  $("forgetRow").hidden = true;
  $("resultCard").hidden = true;
  setMsg("resetMsg", "Forgot " + forgotten(r.cleared) + ". Nothing was sent to Workfront. If the record type still exists there, delete it before pressing Go.", "ok");
}

const guarded = (fn) => () => fn().catch((e) => { showGlobalError(e.message); applyGo(); });
async function afterConnect() { await loadKnownBases(); await loadSetup(); }

startPage("emergency", { afterConnect }, () => {
  initBaseCombo();
  watchSort("fieldGrid");
  $("baseId").addEventListener("input", guarded(loadSetup));
  $("tableSel").addEventListener("change", () => { setMsg("resetMsg", "", ""); $("forgetRow").hidden = true; applyGo(); });
  $("btnGo").addEventListener("click", guarded(go));
  $("btnReset").addEventListener("click", guarded(resetTable));
  $("btnForget").addEventListener("click", guarded(forgetTable));
  $("btnCancel").addEventListener("click", guarded(async () => { await api("POST", "/api/emergency/cancel", {}); $("progress").textContent = "Stopping after the current item..."; }));
});
