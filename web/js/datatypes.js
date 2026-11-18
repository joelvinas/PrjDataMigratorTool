"use strict";
/* Data types: the Workfront Planning type and the Workfront Core dataType and displayType of every field (rules in datatypes.py, saved decisions in
   datatypes_store.py). The tool proposes from the default mapping and a person applies, overrides or clears; every change records who, when and why.
   Everything is inserted with textContent (via el()), never innerHTML. */

const ACTOR_KEY = "migrationControl.reviewer";
const PER_PAGE = 50;
const D = { R: null, page: 0 };
const baseNow = () => $("baseId").value.trim();
const actorNow = () => $("actor").value.trim();
const reasonNow = () => $("reason").value.trim();
const STATE_TEXT = { set: ["Set", "done"], unset: ["Not set", "stale"], invalid: ["Invalid", "stale"] };

function tag(text, cls) { return el("span", { class: "tag" + (cls ? " " + cls : ""), text }); }
function say(id, text, kind) { setMsg(id, text, kind); }
function btn(text, handler, opts) {
  const b = el("button", Object.assign({ type: "button", text }, opts || {}));
  if (handler) b.addEventListener("click", handler);
  return b;
}
function select(values, current, label, blank) {
  const s = el("select", { "aria-label": label });
  if (blank !== false) s.appendChild(el("option", { value: "", text: blank || "(none)" }));
  for (const v of values) s.appendChild(el("option", { value: v, text: v }));
  s.value = current || "";
  if (current && s.value !== current) { s.appendChild(el("option", { value: current, text: current + " (not on the list)" })); s.value = current; }
  return s;
}

async function load() {
  const base = baseNow();
  if (!/^app[A-Za-z0-9]{4,}$/.test(base)) { D.R = null; render(); return; }
  try {
    D.R = await api("GET", "/api/datatypes/review?base_id=" + encodeURIComponent(base));
    D.page = 0; say("pageMessage", "");
  } catch (e) { D.R = null; say("pageMessage", e.message, "err"); }
  render();
}

/* One call for every change: the same initials and reason go with it, and the page is rebuilt from what the server returns. */
async function decide(path, body, msgId, okText) {
  if (!actorNow()) { say(msgId, "Enter your initials (top of the page) before making a change.", "err"); $("actor").focus(); return null; }
  if (!reasonNow()) { say(msgId, "Enter a short reason (top of the page): it is kept with every change.", "err"); $("reason").focus(); return null; }
  try {
    const r = await api("POST", path, Object.assign({ base_id: baseNow(), actor: actorNow(), reason: reasonNow() }, body));
    if (r.review) D.R = r.review;
    render();
    say(msgId, typeof okText === "function" ? okText(r) : okText || "Done.", "ok");
    return r;
  } catch (e) { say(msgId, e.message, "err"); return null; }
}

function render() {
  const R = D.R, ready = !!R && R.ready;
  $("notReady").hidden = !R || R.ready;
  if (R && !R.ready) $("notReadyText").textContent = "Saving types needs migrations 021 and 022 applied to this database. Still missing: " + R.missing.join(", ") + ". You can look at the current values below; nothing can be saved until then.";
  for (const id of ["btnClearInvalid", "btnDefaults", "btnValue", "btnClear"]) $(id).disabled = !ready;
  renderSummary(); renderDefaults(); renderBulkControls(); renderFields();
}

function renderSummary() {
  const strip = $("summaryStrip"); strip.replaceChildren();
  if (!D.R) return;
  for (const [sys, name] of [["planning", "Planning"], ["core", "Core"]]) {
    const c = D.R.counts[sys];
    strip.appendChild(el("div", { class: "note" }, el("b", { text: name + ": " }), el("span", { text: fmt(c.set) + " set, " + fmt(c.unset) + " not set" }),
      c.invalid ? tag(fmt(c.invalid) + " invalid", "stale") : null));
  }
}

function renderDefaults() {
  const body = $("defaultsGrid").tBodies[0]; body.replaceChildren();
  if (!D.R) return;
  const by = {};
  for (const d of D.R.defaults) (by[d.airtable_type] = by[d.airtable_type] || {})[d.system] = d;
  for (const at of Object.keys(by).sort()) {
    const p = by[at].planning || {}, c = by[at].core || {};
    const pt = select(D.R.planning_types, p.type, "Planning default for " + at), cd = select(D.R.core_data_types, c.type, "Core dataType default for " + at);
    const cp = select(D.R.core_display_types, c.display_type, "Core displayType default for " + at);
    const note = (p.note || "") + (p.note && c.note ? " / " : "") + (c.note || "");
    const rules = el("td", { class: "hint" }, [p.rule_text ? el("div", { text: "Planning: " + p.rule_text }) : null, c.rule_text ? el("div", { text: "Core: " + c.rule_text }) : null]);
    const save = btn("Save", async () => {
      const a = await decide("/api/datatypes/default", { system: "planning", airtable_type: at, type: pt.value || null }, "bulkMessage", null);
      if (a) await decide("/api/datatypes/default", { system: "core", airtable_type: at, type: cd.value || null, display_type: cp.value || null }, "bulkMessage", "Defaults for " + at + " saved.");
    }, { disabled: !D.R.ready });
    body.appendChild(el("tr", {}, el("td", { text: at }), el("td", {}, pt), el("td", {}, cd), el("td", {}, cp), el("td", { class: "hint", text: note }), rules, el("td", {}, save)));
  }
}

function fillSelect(sel, values, blankText) {
  const keep = sel.value;
  sel.replaceChildren(el("option", { value: "", text: blankText }), ...values.map(([v, t]) => el("option", { value: v, text: t })));
  sel.value = values.some(([v]) => v === keep) ? keep : "";
}
function tableList() {
  const m = new Map();
  for (const f of D.R.fields) m.set(f.table_id, f.table_name);
  return [...m.entries()].map(([id, n]) => [id, n]);
}
function bulkMatches() {
  const sys = $("bSystem").value, t = $("bTable").value, a = $("bAirtable").value, s = $("bState").value;
  return D.R.fields.filter((f) => !f.abandoned && !f.consolidated && (!t || f.table_id === t) && (!a || f.airtable_type === a) && (!s || f[sys].state === s));
}
function bulkSelection() {
  const t = $("bTable").value, a = $("bAirtable").value, s = $("bState").value;
  return { table_ids: t ? [t] : [], airtable_types: a ? [a] : [], states: s ? [s] : [] };
}
function renderBulkControls() {
  if (!D.R) { $("bMatch").textContent = ""; return; }
  fillSelect($("bTable"), tableList(), "All tables");
  fillSelect($("bAirtable"), D.R.airtable_types.map((x) => [x, x]), "All Airtable types");
  const core = $("bSystem").value === "core";
  const typeSel = $("bType"), disp = $("bDisplay");
  fillSelect(typeSel, (core ? D.R.core_data_types : D.R.planning_types).map((x) => [x, x]), core ? "Core dataType..." : "Planning type...");
  fillSelect(disp, D.R.core_display_types.map((x) => [x, x]), "Core displayType...");
  disp.hidden = !core;
  $("bMatch").textContent = fmt(bulkMatches().length) + " field(s) match.";
}

function fieldMatches(f) {
  if (f.abandoned || f.consolidated) return false;
  const t = $("fTable").value, p = $("fState").value, c = $("fCState").value, q = $("fSearch").value.trim().toLowerCase();
  if (t && f.table_id !== t) return false;
  if (p && f.planning.state !== p) return false;
  if (c && f.core.state !== c) return false;
  return !q || f.field_name.toLowerCase().includes(q) || f.field_id.toLowerCase().includes(q);
}
function decisionCell(f) {
  const bits = [];
  for (const [sys, name] of [["planning", "Planning"], ["core", "Core"]]) {
    const d = f[sys].decision;
    if (d) bits.push(el("div", { class: "hint", text: name + ": " + d.source + " by " + d.by + ", " + d.at + (d.reason ? " (" + d.reason + ")" : "") }));
  }
  return el("td", {}, bits);
}
function renderFields() {
  const body = $("fieldsGrid").tBodies[0]; body.replaceChildren();
  if (!D.R) { $("pageInfo").textContent = ""; return; }
  fillSelect($("fTable"), tableList(), "All tables");
  const list = D.R.fields.filter(fieldMatches);
  const pages = Math.max(1, Math.ceil(list.length / PER_PAGE));
  D.page = Math.min(D.page, pages - 1);
  $("pageInfo").textContent = "Page " + (D.page + 1) + " of " + pages + " (" + fmt(list.length) + " fields)";
  $("btnPrev").disabled = D.page === 0; $("btnNext").disabled = D.page >= pages - 1;
  for (const f of list.slice(D.page * PER_PAGE, (D.page + 1) * PER_PAGE)) {
    const pt = select(D.R.planning_types, f.planning.type, "Planning type for " + f.field_name);
    const cd = select(D.R.core_data_types, f.core.type, "Core dataType for " + f.field_name);
    const cp = select(D.R.core_display_types, f.core.display_type, "Core displayType for " + f.field_name);
    const hint = (x, sys) => el("div", {}, tag(STATE_TEXT[x.state][0], STATE_TEXT[x.state][1]),
      x.proposed ? el("div", { class: "hint", text: "default: " + x.proposed.type + (x.proposed.display_type ? " / " + x.proposed.display_type : "") }) : el("div", { class: "hint", text: "no default" }),
      x.note ? el("div", { class: "hint", text: x.note }) : null);
    const save = (sys) => btn("Save " + sys, () => decide("/api/datatypes/apply", { system: sys, mode: "each", changes: [{ field_id: f.field_id, type: sys === "planning" ? pt.value : cd.value, display_type: sys === "core" ? cp.value : null }] },
      "fieldsMessage", "Saved " + f.field_name + "."), { disabled: !D.R.ready });
    body.appendChild(el("tr", {}, el("td", { text: f.table_name }), el("td", {}, el("div", { text: f.field_name }), el("div", { class: "hint", text: f.field_id })), el("td", { text: f.airtable_type }),
      el("td", {}, pt, hint(f.planning), save("planning")), el("td", {}, cd, hint(f.core)), el("td", {}, cp, save("core")), decisionCell(f), el("td", {})));
  }
}

const guarded = (fn) => async () => { try { await fn(); } catch (e) { showGlobalError(e.message); } };
async function afterConnect() { await loadKnownBases(); await load(); }

startPage("datatypes", { onReadyChange() {}, afterConnect }, () => {
  initBaseCombo();
  try { $("actor").value = sessionStorage.getItem(ACTOR_KEY) || ""; } catch (e) { /* storage can be blocked */ }
  $("actor").addEventListener("input", () => { try { sessionStorage.setItem(ACTOR_KEY, actorNow()); } catch (e) { /* ignore */ } });
  $("baseId").addEventListener("input", guarded(load));
  for (const id of ["bSystem", "bTable", "bAirtable", "bState"]) $(id).addEventListener("change", () => { if (D.R) renderBulkControls(); });
  for (const id of ["fTable", "fState", "fCState"]) $(id).addEventListener("change", () => { D.page = 0; renderFields(); });
  $("fSearch").addEventListener("input", () => { D.page = 0; renderFields(); });
  $("btnPrev").addEventListener("click", () => { D.page = Math.max(0, D.page - 1); renderFields(); });
  $("btnNext").addEventListener("click", () => { D.page += 1; renderFields(); });
  const done = (r) => fmt(r.changed) + " changed, " + fmt(r.unchanged) + " already had it" + (r.skipped ? "; skipped: " + fmt(r.skipped.no_default) + " with no default, " + fmt(r.skipped.already_set) + " already set" : "") + " (batch " + r.batch + ").";
  const bulk = (mode, extra) => () => decide("/api/datatypes/apply", Object.assign({ system: $("bSystem").value, mode, selection: bulkSelection() }, extra || {}), "bulkMessage", done);
  $("btnDefaults").addEventListener("click", bulk("defaults"));
  $("btnValue").addEventListener("click", () => bulk("value", { value: { type: $("bType").value, display_type: $("bDisplay").value } })());
  $("btnClear").addEventListener("click", bulk("clear"));
  $("btnClearInvalid").addEventListener("click", () => decide("/api/datatypes/clear-invalid", {}, "pageMessage", (r) => "Cleared " + fmt(r.cleared.planning) + " Planning and " + fmt(r.cleared.core) + " Core value(s) (batch " + r.batch + ")."));
});
