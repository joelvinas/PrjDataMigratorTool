"use strict";
/* Re-key record types: a separate page, used after the data is validated. The page only asks the server to read or change the record types of the dev Workspace and shows the answer:
   table and field names, IDs, statuses and short reasons. No record value and no token is ever in a reply. */
const rk = { setup: null, job: null, timer: null, rows: [] };
const baseNow = () => $("baseId").value.trim();
const wsNow = () => (rk.setup && rk.setup.workspace_id) || "";
const td = (text, cls) => el("td", { class: cls || null, text: text === null || text === undefined ? "" : String(text) });
const STATUS = { eligible: "can be re-keyed", already: "already done", ineligible: "left alone", no_field: "left alone", no_primary: "left alone", unchecked: "not checked yet" };
const STATUS_ROW = { eligible: "out-ok", ineligible: "out-hold", no_field: "out-hold", no_primary: "out-hold" };

const chosen = () => Array.from(document.querySelectorAll("#rowGrid input.pick:checked")).map((c) => c.value);
const busy = () => !!(rk.job && rk.job.status === "running");

function applyState() {
  const ready = !!(rk.setup && rk.setup.configured && baseNow()) && !busy();
  $("btnCheck").disabled = !ready;
  $("btnCancel").hidden = !busy();
  const phraseOk = $("phrase").value.trim() === (rk.setup ? rk.setup.phrase : "");
  const checked = rk.rows.some((r) => r.status !== "unchecked");
  $("btnRekey").disabled = busy() || !checked || !chosen().length || !phraseOk || !$("actor").value.trim() || !$("reason").value.trim();
}

function renderRows(rows) {
  rk.rows = rows;
  const card = $("tableCard");
  card.hidden = rows.length === 0;
  const tb = $("rowGrid").querySelector("tbody");
  clear(tb);
  rows.forEach((r) => {
    const box = el("input", { type: "checkbox", class: "pick", value: r.table_id, "aria-label": "Re-key " + r.table_name });
    if (r.status === "eligible") box.checked = true; else box.disabled = true;
    box.addEventListener("change", applyState);
    tb.appendChild(el("tr", { class: STATUS_ROW[r.status] || null }, [el("td", null, [box]), td(r.table_name + " (" + r.table_id + ")"), td(r.airtable_field_name ? r.airtable_field_name + " (" + r.airtable_field_id + ")" : ""),
      td(r.current_primary_name || (r.status === "unchecked" ? "(not read yet)" : "")), td(STATUS[r.status] || r.status), td(r.reason)]));
  });
  $("decideCard").hidden = !rows.some((r) => r.status !== "unchecked");
  applySort("rowGrid");
  applyState();
}

async function loadSetup() {
  const d = await api("GET", "/api/rekey/setup?base_id=" + encodeURIComponent(baseNow()));
  rk.setup = d;
  showGlobalError("");
  $("wsId").textContent = d.workspace_id || "(not configured)";
  $("phraseText").textContent = d.phrase;
  setMsg("pageMessage", d.configured ? (baseNow() && d.counts ? fmt(d.counts.record_types) + " record type(s) of this base are recorded in the dev Workspace. Press Check to read them from Planning." : "") : d.problem, d.configured ? "" : "err");
  renderRows(d.rows || []);
  await follow();
}

function fileLink(name, label) { return el("a", { href: "/api/rekey/report?file=" + encodeURIComponent(name), text: label }); }

function renderResult(j) {
  const r = j.result;
  if (!r) { $("resultCard").hidden = false; setMsg("resultMsg", j.error || "The step did not finish.", "err"); return; }
  renderRows(r.rows || []);
  const c = r.counts || {};
  if (r.kind === "check") {
    $("resultCard").hidden = true;
    setMsg("tableMsg", fmt(c.eligible) + " can be re-keyed, " + fmt(c.already) + " already done, " + fmt(c.ineligible + c.no_field + c.no_primary) + " left alone (the reason is in the last column).", c.eligible ? "ok" : "warn");
    return;
  }
  $("resultCard").hidden = false;
  const done = (r.results || []).filter((x) => x.outcome === "rekeyed").length, failed = (r.results || []).filter((x) => x.outcome === "failed").length;
  setMsg("resultMsg", (j.status === "cancelled" ? "Stopped before it finished. " : "") + fmt(done) + " record type(s) re-keyed, " + fmt(failed) + " failed, " + fmt((r.results || []).length - done - failed) + " skipped.", failed ? "warn" : "ok");
  const p = $("files");
  clear(p);
  if (r.db_note) p.appendChild(el("div", { class: "hint", text: r.db_note }));
  if (r.file) { p.appendChild(document.createTextNode("Report with who, when and why (also saved in the out/rekey folder): ")); p.appendChild(fileLink(r.file, "re-key report (.mhtml)")); }
  const tb = $("resultGrid").querySelector("tbody");
  clear(tb);
  (r.results || []).forEach((x) => tb.appendChild(el("tr", { class: x.outcome === "failed" ? "out-fail" : x.outcome === "rekeyed" ? "out-ok" : "out-hold" }, [td(x.table_name + " (" + x.table_id + ")"), td(x.planning_field_name), td(x.outcome), td(x.detail)])));
  $("resultWin").hidden = (r.results || []).length === 0;
}

function renderJob(j) {
  rk.job = j;
  const p = j.progress || {};
  $("progress").textContent = j.status === "running" ? (p.phase === "rekey" ? "Re-keying" : "Reading Planning") + (p.total ? ": " + fmt(p.done || 0) + " of " + fmt(p.total) : "") + (p.item ? " (" + p.item + ")" : "") : "";
  if (j.status !== "running") { stopTimer(); renderResult(j); }
  applyState();
}

function stopTimer() { if (rk.timer) { clearInterval(rk.timer); rk.timer = null; } }
async function poll() { const s = await api("GET", "/api/rekey/status"); if (s.job) renderJob(s.job); }
async function follow() {
  const s = await api("GET", "/api/rekey/status");
  if (!s.job) return;
  renderJob(s.job);
  if (s.job.status === "running" && !rk.timer) rk.timer = setInterval(() => poll().catch((e) => { stopTimer(); showGlobalError(e.message); }), 1500);
}
function watch() { if (!rk.timer && rk.job && rk.job.status === "running") rk.timer = setInterval(() => poll().catch((e) => { stopTimer(); showGlobalError(e.message); }), 1500); }

async function check() {
  showGlobalError(""); $("resultCard").hidden = true; setMsg("rekeyMsg", "", "");
  await api("POST", "/api/rekey/check", { base_id: baseNow() });
  await follow(); watch();
}

async function rekey() {
  const ids = chosen();
  if (!window.confirm("Re-key " + ids.length + " record type(s) in the dev Workspace " + wsNow() + "?\n\nFor each one, the field that was the primary field in Airtable becomes the primary field in Planning. AirtableRecordId stays as an ordinary field. Your initials (" + $("actor").value.trim() + ") and your reason are recorded in the report.")) return;
  showGlobalError("");
  $("resultCard").hidden = true;
  await api("POST", "/api/rekey/run", { base_id: baseNow(), table_ids: ids, actor: $("actor").value, reason: $("reason").value, phrase: $("phrase").value, workspace_id: wsNow() });
  await follow(); watch();
}

const guarded = (fn) => () => fn().catch((e) => { showGlobalError(e.message); applyState(); });
async function afterConnect() { await loadKnownBases(); await loadSetup(); }

startPage("rekey", { afterConnect }, () => {
  initBaseCombo();
  watchSort("rowGrid");
  $("baseId").addEventListener("input", guarded(loadSetup));
  ["actor", "reason", "phrase"].forEach((id) => $(id).addEventListener("input", applyState));
  $("btnCheck").addEventListener("click", guarded(check));
  $("btnRekey").addEventListener("click", guarded(rekey));
  $("btnCancel").addEventListener("click", guarded(async () => { await api("POST", "/api/rekey/cancel", {}); $("progress").textContent = "Stopping after the current record type..."; }));
});
