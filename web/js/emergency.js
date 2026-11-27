"use strict";
/* Emergency mode: one table into the Workspace of the chosen Planning stage in one click. The page only asks the server to run it and shows the answer: field names, IDs, counts, outcomes and short
   scrubbed reasons. No record value and no token is ever in a reply. */
const em = { setup: null, job: null, timer: null, revisit: null };
const baseNow = () => $("baseId").value.trim();
const tableNow = () => $("tableSel").value;
const wsNow = () => (em.setup && em.setup.workspace_id) || "";
const stageNow = () => $("stageSel").value;
const stageLabel = () => stageLabelOf(em.setup, stageNow());
const q = () => "base_id=" + encodeURIComponent(baseNow()) + "&stage=" + encodeURIComponent(stageNow());

const td = (text, cls, sort) => el("td", { class: cls || null, text: text === null || text === undefined ? "" : String(text), "data-sort": sort === undefined ? null : String(sort) });
const OUT_ROW = { clean: "out-ok", "pre-converted": "out-hold", failed: "out-fail" };

function applyGo() {
  const busy = !!(em.job && em.job.status === "running");
  $("btnGo").disabled = !(em.setup && em.setup.configured && baseNow() && tableNow() && stageNow()) || busy;
  $("stageSel").disabled = busy;
  $("btnReset").disabled = $("btnGo").disabled;
  $("btnForget").disabled = busy;
  $("btnLinksCheck").disabled = $("btnGo").disabled;
  $("btnLinks").disabled = $("btnGo").disabled || !(em.revisit && em.revisit.in_planning);
  $("btnCancel").hidden = !busy;
}

const STATE_NAME = { new: "not run yet", done: "done", again: "another pass needed" };
function renderOrder(tables) {
  $("orderCard").hidden = !tables.length;
  const tb = $("orderGrid").querySelector("tbody");
  clear(tb);
  tables.forEach((t) => tb.appendChild(el("tr", { class: t.state === "done" ? "out-ok" : t.state === "again" ? "out-hold" : null }, [td(t.position, "num"), td(t.airtable_name + " (" + t.table_id + ")"), td(STATE_NAME[t.state] || t.state),
    td(t.load_order === null || t.load_order === undefined ? "" : t.load_order, "num"), td((t.waits_on || []).join(", ")), td((t.state_reasons || []).join("; "))])));
}

/* the Table picker: green = done, yellow = another pass needed, in the order to take the tables in (the server works it out; see emergency_links.table_states and visit_order) */
function fillTables(d) {
  const sel = $("tableSel"), keep = sel.value;
  clear(sel);
  sel.appendChild(el("option", { value: "", text: baseNow() ? (d.tables.length ? "Choose a table..." : "No in-scope table in this base") : "Choose a base first" }));
  d.tables.forEach((t) => sel.appendChild(el("option", { value: t.table_id, class: t.state === "done" ? "done" : t.state === "again" ? "again" : null,
    title: (t.state_reasons || []).join("; ") || null,
    text: "[#" + t.position + "] " + t.airtable_name + " (" + t.table_id + ")" + (t.state === "done" ? " - done" : t.state === "again" ? " - another pass needed" : "") })));
  renderOrder(d.tables);
  sel.value = d.tables.some((t) => t.table_id === keep) ? keep : "";
}

async function refreshPicker() {
  if (!baseNow()) return;
  const d = await api("GET", "/api/emergency/setup?" + q());
  em.setup = d;
  fillTables(d);
  applyGo();
}

async function loadSetup() {
  const d = await api("GET", "/api/emergency/setup?" + q());
  em.setup = d;
  fillStageSelect($("stageSel"), d, d.stage);
  showGlobalError("");
  $("wsId").textContent = d.workspace_id || "(not configured)";
  setMsg("pageMessage", d.configured ? (d.tables_problem || "") : d.problem, d.configured && !d.tables_problem ? "" : d.configured ? "warn" : "err");
  fillTables(d);
  applyGo();
  await loadFormulas().catch(() => { $("formulaCard").hidden = true; });
  await loadLinks().catch(() => { $("linksCard").hidden = true; });
  await follow();
}

/* ---------- the calculated-field pre-check: which other tables to run first, and in what order ---------- */
async function loadFormulas() {
  const card = $("formulaCard");
  if (!baseNow() || !tableNow()) { card.hidden = true; return; }
  const d = await api("GET", "/api/emergency/formulas?" + q() + "&table_id=" + encodeURIComponent(tableNow()));
  const c = d.counts;
  card.hidden = c.total === 0;
  if (!c.total) return;
  setMsg("formulaMsg", fmt(c.total) + " calculated field(s): " + fmt(c.ready) + " can be built now, " + fmt(c.held_back || 0) + " held back (they read a link), " + fmt(c.waiting) + " wait for another table, " + fmt(c.blocked) + " cannot be built as formulas, "
    + fmt(c.created) + " already built, " + fmt(c.stored_as_text) + " already stored as text.", c.waiting || c.blocked || c.held_back ? "warn" : "ok");
  const adv = $("formulaAdvice");
  clear(adv);
  (d.advice || []).forEach((t) => adv.appendChild(el("div", { class: "hint", text: t })));
  const fill = (id, rows, cells) => { const tb = $(id).querySelector("tbody"); clear(tb); rows.forEach((r, i) => tb.appendChild(el("tr", null, cells(r, i)))); $(id.replace("Grid", "Win")).hidden = rows.length === 0; };
  fill("formulaWaitGrid", d.waiting_on || [], (w) => [td(w.table_name + " (" + w.table_id + ")"), td(w.formulas, "num"), td(w.loaded ? "yes" : "no"), td((w.fields || []).join(", "))]);
  fill("formulaOrderGrid", d.order || [], (o, i) => [td(i + 1, "num"), td(o.table_name + " (" + o.table_id + ")"), td(o.creates, "num"), td(o.unlocks, "num")]);
  fill("formulaBlockGrid", d.blocked || [], (b) => [td(b.name + " (" + b.field_id + ")"), td((b.reasons || []).join("; "))]);
}

/* ---------- the links card: what a second visit to this table would do (nothing is sent) ---------- */
async function loadLinks() {
  const card = $("linksCard");
  em.revisit = null;
  if (!baseNow() || !tableNow()) { card.hidden = true; applyGo(); return; }
  const d = await api("GET", "/api/emergency/revisit?" + q() + "&table_id=" + encodeURIComponent(tableNow()));
  em.revisit = d;
  card.hidden = !(d.link_fields.length || d.held_back.length);
  const rec = d.records || {};
  setMsg("linksMsg", d.in_planning
    ? fmt(d.link_fields.length) + " link field(s). Records loaded in Planning: " + fmt(rec.loaded) + " of " + fmt(rec.registered) + "; records with links still to set: " + fmt(d.links_pending === null ? 0 : d.links_pending) + ". " + (d.needs_visit ? "This table needs a visit." : "Nothing to do for now.")
    : "This table is not in Planning yet (" + stageLabel() + " Workspace). Run it with Go first.", d.needs_visit ? "warn" : "ok");
  const adv = $("linksAdvice");
  clear(adv);
  (d.advice || []).forEach((t) => adv.appendChild(el("div", { class: "hint", text: t })));
  const fill = (id, rows, cells) => { const tb = $(id).querySelector("tbody"); clear(tb); rows.forEach((r) => tb.appendChild(el("tr", null, cells(r)))); $(id.replace("Grid", "Win")).hidden = rows.length === 0; };
  fill("linksFieldGrid", d.link_fields || [], (f) => [td(f.field_name + " (" + f.field_id + ")"), td(f.target_table_name + " (" + f.target_table_id + ")"), td(f.target_in_planning ? "yes" : "no"), td(f.reference_field_exists ? "created" : "not yet"), td(f.direction)]);
  fill("linksHeldGrid", d.held_back || [], (h) => [td(h.field_name + " (" + h.field_id + ")"), td(h.kind), td(h.link_field_name && h.target_table_name ? h.link_field_name + " -> " + h.target_table_name : (h.reads || []).join(", "))]);
  applyGo();
}

const LINK_COUNTS = [["Reference fields created", "reference_created"], ["Reference fields already there", "reference_existing"], ["Reference fields failed", "reference_failed"], ["Link fields waiting for another table", "reference_waiting"],
  ["Link fields blocked (direction not decided)", "blocked"], ["Records with links still to set", "records_pending"], ["Records complete (every link set)", "records_complete"], ["Records partly set (the rest wait)", "records_partial"],
  ["Records waiting (nothing could be set yet)", "records_waiting"], ["Records failed", "records_failed"], ["Records with no links", "records_no_links"], ["Links set", "links_set"], ["Links waiting for a record not in Planning yet", "links_waiting"]];

function renderLinksResult(j) {
  const r = j.result;
  $("linksResultCard").hidden = false;
  if (!r) { setMsg("linksResultMsg", j.error || "The links run did not finish.", "err"); return; }
  const c = r.counts;
  const head = (j.status === "cancelled" ? "Stopped before it finished. " : "") + (r.dry_run ? "Check only, nothing was sent. " : "");
  setMsg("linksResultMsg", head + "Table " + r.table_name + ": " + fmt(c.links_set) + " link(s) " + (r.dry_run ? "would be set" : "set") + ", " + fmt(c.links_waiting) + " waiting, " + fmt(c.records_failed) + " record(s) failed.", c.records_failed || c.reference_failed || c.blocked ? "warn" : "ok");
  const body = $("linksCountGrid").querySelector("tbody");
  clear(body);
  LINK_COUNTS.forEach((p) => body.appendChild(el("tr", null, [td(p[0]), td(fmt(c[p[1]]), "num")])));
  const f = r.files, pf = $("linksFiles");
  clear(pf);
  if (f) {
    pf.appendChild(document.createTextNode("Reports (also saved in the out/emergency folder): "));
    [[f.links, "links report (.mhtml)"], [f.apilog, "API log report (.mhtml)"]].forEach((x, i) => { if (i) pf.appendChild(document.createTextNode(" | ")); pf.appendChild(fileLink(x[0], x[1])); });
  }
  const notes = $("linksNotes");
  clear(notes);
  (r.notes || []).forEach((n) => notes.appendChild(el("div", { class: "banner warn", text: n })));
  (r.reference_fields || []).filter((x) => x.status === "blocked" || x.status === "failed").forEach((x) => notes.appendChild(el("div", { class: "banner warn", text: x.field_name + ": " + x.reason })));
  const fill = (id, rows, cells) => { const tb = $(id).querySelector("tbody"); clear(tb); rows.forEach((x) => tb.appendChild(el("tr", null, cells(x)))); $(id.replace("Grid", "Win")).hidden = rows.length === 0; };
  fill("linksWaitGrid", r.waiting_by_table || [], (w) => [td(w.table_name + " (" + w.table_id + ")"), td(w.in_planning ? "yes" : "no"), td(w.records, "num"), td(w.links, "num")]);
  fill("linksFailGrid", r.failures || [], (x) => [td(x.record_id), td(x.reason)]);
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
  [["Fields pushed clean", c.clean], ["Fields converted to PRE- text", c["pre-converted"]], ["Fields skipped", c.skipped], ["Calculated fields built as real formulas", c.formulas_built], ["Calculated fields held back (run another table first)", c.formulas_held_back], ["Fields failed (both attempts refused)", c.failed],
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
  if (pr) notes.appendChild(el("div", { class: "hint", text: "Preparatory step: AirtableRecordId " + (pr.primary ? "is the primary field" : "is NOT the primary field") + "; " + pr.deleted.length + " default field(s) deleted, " + (pr.renamed || []).length + " renamed, " + (pr.protected || []).length + " protected (left in place)"
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
    ? ({ fields: "Creating fields", records: "Loading records", links: "Setting links", reread: "Reading the table from Airtable" }[p.phase] || "Working") + (p.total ? ": " + fmt(p.done || 0) + " of " + fmt(p.total) : "") + (p.item && p.phase === "fields" ? " (" + p.item + ")" : "") : "";
  if (j.status !== "running") {
    stopTimer();
    if (j.kind === "links") { $("resultCard").hidden = true; renderLinksResult(j); } else { $("linksResultCard").hidden = true; renderResult(j); }
    loadLinks().catch(() => {});
    refreshPicker().catch(() => {});
  }
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
  if (!window.confirm("Send the table \"" + name + "\" (" + tableNow() + ") to the " + stageLabel() + " Workspace " + wsNow() + "?\n\nThis creates a record type and fields and loads records in Workfront Planning.")) return;
  const phrase = productionPhrase(stageNow(), em.setup.production_phrase, "run");
  if (phrase === null) return;
  showGlobalError("");
  setMsg("resetMsg", "", ""); $("forgetRow").hidden = true;
  $("resultCard").hidden = true; $("linksResultCard").hidden = true;
  await api("POST", "/api/emergency/start", { base_id: baseNow(), table_id: tableNow(), stage: stageNow(), production_phrase: phrase, confirm: { table_id: tableNow(), workspace_id: wsNow() } });
  await follow();
  if (!em.timer && em.job && em.job.status === "running") em.timer = setInterval(() => poll().catch((e) => { stopTimer(); showGlobalError(e.message); }), 1500);
}

/* ---------- the links pass: a check that sends nothing, then the real run ---------- */
async function runLinks(send) {
  if (send && !window.confirm("Set the links of the table \"" + tableLabel() + "\" in the " + stageLabel() + " Workspace " + wsNow() + "?\n\nThis creates reference fields and updates records in Workfront Planning. It creates no record. A link whose other record is not in Planning yet waits.")) return;
  const phrase = send ? productionPhrase(stageNow(), em.setup.production_phrase, "links pass") : undefined;
  if (phrase === null) return;
  showGlobalError("");
  $("resultCard").hidden = true; $("linksResultCard").hidden = true;
  await api("POST", "/api/emergency/links", { base_id: baseNow(), table_id: tableNow(), stage: stageNow(), send, production_phrase: phrase, confirm: { table_id: tableNow(), workspace_id: wsNow() } });
  await follow();
  if (!em.timer && em.job && em.job.status === "running") em.timer = setInterval(() => poll().catch((e) => { stopTimer(); showGlobalError(e.message); }), 1500);
}

/* ---------- reset: delete the record type, then forget the IDs (a refusal is an answer, not an error) ---------- */
const forgotten = (c) => fmt(c.load_id_map) + " recorded item(s), " + fmt(c.mappings) + " record ID(s), " + fmt(c.markers) + " marker(s), " + fmt(c.option_ids) + " option ID(s)";
const resetBody = (send, phrase) => ({ base_id: baseNow(), table_id: tableNow(), stage: stageNow(), send, production_phrase: phrase, confirm: { table_id: tableNow(), workspace_id: wsNow() } });
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
  refreshPicker().catch(() => {});
  setMsg("resetMsg", (a ? (a.already_gone ? "The record type was already gone from Workfront. " : "Record type deleted in Workfront. ") : "No record type ID was recorded, so nothing was deleted in Workfront (if an interrupted run created one, delete it by hand). ")
    + "Forgot " + forgotten(r.cleared) + ". Press Go to build the table again.", "ok");
}

async function resetTable() {
  showGlobalError(""); setMsg("resetMsg", "", ""); $("forgetRow").hidden = true;
  const peek = await api("POST", "/api/emergency/reset", resetBody(false));        // this tool's own counts: nothing is sent
  const b = peek.before;
  if (!window.confirm("Reset the table \"" + tableLabel() + "\" in the " + stageLabel() + " Workspace " + wsNow() + "?\n\nThis tool holds " + fmt(b.map_total) + " recorded item(s), " + fmt(b.mapped_records) + " record ID(s), " + fmt(b.markers) + " marker(s) and " + fmt(b.option_ids) + " option ID(s) for it. "
    + (b.record_type ? "It will try to DELETE record type " + b.record_type + " in Workfront Planning, with its fields and records, then forget these. This cannot be undone." : "No record type ID is recorded, so nothing will be deleted in Workfront; these will be forgotten.") + linkWarn(peek))) return;
  const phrase = productionPhrase(stageNow(), em.setup.reset_phrase, "reset");
  if (phrase === null) return;
  showReset(await api("POST", "/api/emergency/reset", resetBody(true, phrase)));
}

async function forgetTable() {
  if (!window.confirm("Forget every ID this tool holds for \"" + tableLabel() + "\" in the " + stageLabel() + " Workspace " + wsNow() + "?\n\nNothing is sent to Workfront. A record type still there will cause a name clash on the next Go.")) return;
  const phrase = productionPhrase(stageNow(), em.setup.reset_phrase, "forget");
  if (phrase === null) return;
  const r = await api("POST", "/api/emergency/forget", resetBody(true, phrase));
  $("forgetRow").hidden = true;
  $("resultCard").hidden = true;
  refreshPicker().catch(() => {});
  setMsg("resetMsg", "Forgot " + forgotten(r.cleared) + ". Nothing was sent to Workfront. If the record type still exists there, delete it before pressing Go.", "ok");
}

const guarded = (fn) => () => fn().catch((e) => { showGlobalError(e.message); applyGo(); });
async function afterConnect() { await loadKnownBases(); await loadSetup(); }

startPage("emergency", { afterConnect }, () => {
  initBaseCombo();
  watchSort("fieldGrid");
  $("baseId").addEventListener("input", guarded(loadSetup));
  $("stageSel").addEventListener("change", guarded(async () => { setMsg("resetMsg", "", ""); $("forgetRow").hidden = true; $("resultCard").hidden = true; $("linksResultCard").hidden = true; await loadSetup(); }));
  $("tableSel").addEventListener("change", () => { setMsg("resetMsg", "", ""); $("forgetRow").hidden = true; applyGo(); loadFormulas().catch((e) => { $("formulaCard").hidden = true; showGlobalError(e.message); }); loadLinks().catch((e) => { $("linksCard").hidden = true; showGlobalError(e.message); }); });
  $("btnGo").addEventListener("click", guarded(go));
  $("btnLinksCheck").addEventListener("click", guarded(() => runLinks(false)));
  $("btnLinks").addEventListener("click", guarded(() => runLinks(true)));
  $("btnReset").addEventListener("click", guarded(resetTable));
  $("btnForget").addEventListener("click", guarded(forgetTable));
  $("btnCancel").addEventListener("click", guarded(async () => { await api("POST", "/api/emergency/cancel", {}); $("progress").textContent = "Stopping after the current item..."; }));
});
