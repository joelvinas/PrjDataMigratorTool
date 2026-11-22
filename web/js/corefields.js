"use strict";
/* Core fields in bulk: upload a CSV, scan Workfront Core for names that exist, create the rest, reset what this tool made. The page only asks the server to do each step and
   shows the answer: field names, IDs, counts and short cleaned reasons. No token is ever in a reply. */
const cf = { setup: null, st: null, timer: null };
const envNow = () => $("envSel").value;
const busy = () => !!(cf.st && cf.st.job && cf.st.job.status === "running");
const td = (text, cls) => el("td", { class: cls || null, text: text === null || text === undefined ? "" : String(text) });
const STATUS_ROW = { created: "out-ok", exists: "out-hold", deleted: "out-hold", failed: "out-fail", "scan failed": "out-fail", "delete failed": "out-fail" };

function toBase64(buf) {
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

function applyButtons() {
  const st = cf.st, b = busy(), env = envNow();
  const haveRows = !!(st && st.rows.length);
  $("btnUpload").disabled = !(cf.setup && cf.setup.db_connected) || b || !$("csvFile").files.length;
  $("btnCheck").disabled = !haveRows || !env || b;
  $("btnAll").disabled = $("btnNone").disabled = !haveRows || b;
  $("btnCreate").disabled = !(st && st.can_create) || b || !$("actor").value.trim() || (env === "production" && $("createPhrase").value !== cf.setup.production_phrase);
  $("btnReset").disabled = !(st && st.can_reset) || b || !$("resetConfirm").checked || !$("actor").value.trim() || $("resetReason").value.trim().length < 3
    || (env === "production" && $("resetPhrase").value !== cf.setup.reset_phrase);
  $("resetConfirm").disabled = !(st && st.can_reset) || b;
  $("btnCancel").hidden = !b;
  $("createPhraseBox").hidden = env !== "production";
  $("resetPhraseBox").hidden = env !== "production";
}

function renderGrid() {
  const st = cf.st;
  $("gridCard").hidden = !st || !st.rows.length;
  if (!st) return;
  const body = $("fieldGrid").querySelector("tbody");
  clear(body);
  st.rows.forEach((r) => {
    const box = el("input", { type: "checkbox", "aria-label": "Create row " + r.n + " (" + r.name + ")" });
    box.checked = !!r.checked;
    box.disabled = !!r.parameterId || busy();
    box.addEventListener("change", guarded(async () => { cf.st = await api("POST", "/api/corefields/select", { checked: { [r.n]: box.checked } }); render(); }));
    body.appendChild(el("tr", { class: STATUS_ROW[r.status] || null }, [el("td", null, box), td(r.n, "num"), td(r.field_id), td(r.name), td(r.label), td(r.dataType), td(r.displayType), td(r.description),
      td(r.isRequired ? "true" : "false"), td(r.parameterId), td(r.lastUpdateDate), td([r.status, r.message].filter(Boolean).join(": "))]));
  });
  const n = st.rows.length, ticked = st.rows.filter((r) => r.checked).length, have = st.rows.filter((r) => r.parameterId).length;
  $("gridCount").textContent = "(" + fmt(n) + " rows, " + fmt(ticked) + " ticked, " + fmt(have) + " already in Workfront)";
  $("scanNote").textContent = st.scan_complete ? "Scan of " + st.environment + " complete (" + fmt(st.scan.found) + " already exist). Creating is unlocked." : "Not scanned yet for the chosen environment: creating is locked until CHECK FIELDS completes.";
}

function fileLink(kind, name, label) { return el("a", { href: "/api/corefields/report?file=" + encodeURIComponent(name), text: label }); }

function renderDownloads() {
  const st = cf.st, box = $("downloads");
  clear(box);
  if (!st || !st.rows.length) return;
  box.appendChild(document.createTextNode("Downloads: "));
  const parts = [el("a", { href: "/api/corefields/crosswalk.csv", text: "fields and crosswalk (.csv)" })];
  [["scan", "scan report (.mhtml)"], ["create", "create report (.mhtml)"], ["reset", "reset report (.mhtml)"]].forEach((p) => { if (st.reports[p[0]]) parts.push(fileLink(p[0], st.reports[p[0]], p[1])); });
  parts.forEach((a, i) => { if (i) box.appendChild(document.createTextNode(" | ")); box.appendChild(a); });
  $("crosswalkNote").textContent = st.crosswalk_saved ? "The crosswalk is saved in the database (core_field_crosswalk)."
    : "The crosswalk table (draft migration 026) is not applied to this database yet, so the crosswalk is kept only until the app stops. Download the CSV after each run.";
}

function renderJob() {
  const j = cf.st && cf.st.job;
  if (!j) { $("progress").textContent = ""; setMsg("resultMsg", "", ""); return; }
  const p = j.progress || {};
  $("progress").textContent = j.status === "running" ? ({ scan: "Scanning", create: "Creating", reset: "Deleting" }[p.phase] || "Working") + (p.total ? ": " + fmt(p.done || 0) + " of " + fmt(p.total) : "") + (p.item ? " (" + p.item + ")" : "") : "";
  if (j.status === "running") { setMsg("resultMsg", "", ""); return; }
  const s = j.summary || {}, what = { scan: "Scan", create: "Creation", reset: "Reset" }[j.op];
  const counts = j.op === "scan" ? fmt(s.scanned) + " name(s) checked, " + fmt(s.found) + " already exist" : j.op === "create" ? fmt(s.created) + " of " + fmt(s.planned) + " field(s) created"
    : fmt(s.deleted) + " of " + fmt(s.planned) + " field(s) deleted" + (s.skipped_not_created_here ? ", " + fmt(s.skipped_not_created_here) + " skipped (not created by this tool)" : "");
  setMsg("resultMsg", what + " in " + j.environment + ": " + j.status + ". " + counts + "." + (j.error ? " " + j.error : ""), j.status === "done" ? "ok" : "err");
}

function render() { renderGrid(); renderDownloads(); renderJob(); applyButtons(); }

async function refresh() { cf.st = await api("GET", "/api/corefields/state"); if (cf.st.environment && !envNow()) $("envSel").value = cf.st.environment; render(); }
function stopTimer() { if (cf.timer) { clearInterval(cf.timer); cf.timer = null; } }
function follow() {
  stopTimer();
  cf.timer = setInterval(() => refresh().then(() => { if (!busy()) stopTimer(); }).catch((e) => { stopTimer(); showGlobalError(e.message); }), 1000);
}

async function loadSetup() {
  cf.setup = await api("GET", "/api/corefields/setup");
  const sel = $("envSel"), keep = sel.value;
  clear(sel);
  sel.appendChild(el("option", { value: "", text: cf.setup.environments.some((e) => e.configured) ? "Choose an environment..." : "No Core environment is configured in config.json" }));
  cf.setup.environments.filter((e) => e.configured).forEach((e) => sel.appendChild(el("option", { value: e.name, text: e.name + (e.production ? " (production)" : "") })));
  sel.value = keep;
  await refresh();
  if (busy()) follow();
}

async function upload() {
  const f = $("csvFile").files[0];
  if (!f) return;
  setMsg("pageMessage", "", ""); $("problemWin").hidden = true; showGlobalError("");
  try {
    cf.st = await api("POST", "/api/corefields/upload", { csv_base64: toBase64(await f.arrayBuffer()), filename: f.name });
  } catch (e) {
    setMsg("pageMessage", e.message, "err");
    const body = $("problemGrid").querySelector("tbody");
    clear(body);
    (e.problems || []).forEach((p) => body.appendChild(el("tr", null, [td(p.row || "", "num"), td(p.column), td(p.message)])));
    $("problemWin").hidden = !(e.problems && e.problems.length);
    return;
  }
  if (envNow()) await api("POST", "/api/corefields/environment", { environment: envNow() });
  await refresh();
  setMsg("pageMessage", cf.st.rows.length + " row(s) are valid and listed below. " + (envNow() ? "" : "Choose an environment, then press CHECK FIELDS."), "ok");
  if (envNow()) await check();
}

async function check() {
  if (!envNow()) { setMsg("pageMessage", "Choose a Workfront environment first.", "err"); return; }
  const ok = window.confirm("Scan Workfront (" + envNow() + ") now?\n\nThis only READS: one search per listed field name, to see which fields already exist. Nothing is created or changed.\n\nPress Cancel to wait; creating stays locked until a scan has completed.");
  if (!ok) { setMsg("pageMessage", "No scan was made. Press CHECK FIELDS when you are ready; creating stays locked until then.", "warn"); return; }
  showGlobalError("");
  await api("POST", "/api/corefields/scan", { environment: envNow(), scan_ok: true });
  await refresh(); follow();
}

async function create() {
  const st = cf.st, n = st.rows.filter((r) => r.checked && !r.parameterId).length;
  if (!window.confirm("Create " + n + " field(s) in Workfront Core (" + envNow() + ")?\n\nThey are created from the top of the list downward, and the run stops at the first error.")) return;
  showGlobalError("");
  await api("POST", "/api/corefields/create", { environment: envNow(), actor: $("actor").value, confirm_phrase: $("createPhrase").value });
  await refresh(); follow();
}

async function reset() {
  const n = cf.st.rows.filter((r) => r.in_crosswalk).length;
  if (!window.confirm("DELETE " + n + " field(s) from Workfront Core (" + envNow() + ")?\n\nOnly fields this tool created are deleted, from the top of the list, stopping at the first error. This cannot be undone.")) return;
  showGlobalError("");
  await api("POST", "/api/corefields/reset", { environment: envNow(), actor: $("actor").value, reason: $("resetReason").value, confirm: $("resetConfirm").checked, confirm_phrase: $("resetPhrase").value });
  $("resetConfirm").checked = false;
  await refresh(); follow();
}

const guarded = (fn) => () => fn().catch((e) => { showGlobalError(e.message); refresh().catch(() => {}); });

startPage("corefields", { afterConnect: loadSetup }, () => {
  $("csvFile").addEventListener("change", applyButtons);
  ["actor", "createPhrase", "resetReason", "resetPhrase"].forEach((id) => $(id).addEventListener("input", applyButtons));
  $("resetConfirm").addEventListener("change", applyButtons);
  $("envSel").addEventListener("change", guarded(async () => {
    applyButtons();                                  // the phrase boxes follow the dropdown at once; the server then clears the old scan
    if (cf.st && cf.st.rows.length && envNow()) { cf.st = await api("POST", "/api/corefields/environment", { environment: envNow() }); render(); }
    applyButtons();
  }));
  $("btnUpload").addEventListener("click", guarded(upload));
  $("btnCheck").addEventListener("click", guarded(check));
  $("btnAll").addEventListener("click", guarded(async () => { cf.st = await api("POST", "/api/corefields/select", { all: true }); render(); }));
  $("btnNone").addEventListener("click", guarded(async () => { cf.st = await api("POST", "/api/corefields/select", { all: false }); render(); }));
  $("btnCreate").addEventListener("click", guarded(create));
  $("btnReset").addEventListener("click", guarded(reset));
  $("btnCancel").addEventListener("click", guarded(async () => { await api("POST", "/api/corefields/cancel", {}); $("progress").textContent = "Stopping after the current call..."; }));
});
