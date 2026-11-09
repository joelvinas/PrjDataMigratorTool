"use strict";
/* Setup page: database connection (step 1) and token and base (step 2). */

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
/* After a connection the only thing Setup loads is the list of bases already in the database (for the base picker). */
async function afterConnect() {
  await loadKnownBases();
}

/* ---------- wiring ---------- */
startPage("setup", {
  onStatus(s) {
    $("tokenYesNo").textContent = s.token_configured ? "yes" : "no";
    if (!$("dbPath").value && s.default_db_path) $("dbPath").value = s.default_db_path;
    if (s.db_path) $("dbPath").value = s.db_path;
  },
  onDbResult(r) { renderDbResult(r); },
  onDbError(e) { setMsg("dbMessage", e.message, "err"); },
  afterConnect,
}, () => {
  $("btnConnect").addEventListener("click", connectDb);
  $("dbPath").addEventListener("keydown", (e) => { if (e.key === "Enter") connectDb(); });
  $("btnCreate").addEventListener("click", createDb);
  initBaseCombo();
});
