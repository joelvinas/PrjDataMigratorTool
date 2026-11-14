"use strict";
/* Field names: review the renamed Airtable field and table names against the naming rules (rules live in fieldnames.py, saved decisions in
   fieldnames_store.py). The page proposes and a person approves; nothing here renames anything on its own. Everything is inserted with
   textContent (via el()), never innerHTML. */

const TARGETS = [["planning_name", "Planning name"], ["core_name", "Core name"], ["core_label", "Core label"]];
const VERDICTS = { PASS: ["Passes", "done"], AUTO_FIXABLE: ["Auto-fixable", "run"], NEEDS_HUMAN: ["Needs a person", "stale"] };
const STATUS_TEXT = { unreviewed: "Unreviewed", approved: "Approved", needs_human: "Flagged" };
const RULE_TITLES = {
  EMPTY: "empty", NON_ASCII: "non-ASCII character", WHITESPACE_CHAR: "tab, new line or no-break space", SPECIAL_CHAR: "special character",
  EDGE_SPACE: "leading or trailing space", DOUBLE_SPACE: "double space", LEADING_DIGIT: "starts with a digit", TOO_LONG: "too long",
  QUARTER_STYLE: "1Q instead of Q1", RESERVED: "reserved name", TABLE_REF: "old table name", DUPLICATE: "duplicate",
};
const PER_PAGE = 50;
const ACTOR_KEY = "migrationControl.reviewer";

const N = { R: null, saved: null, target: "planning_name", page: 0, descPage: 0, open: null, timer: null };
const DESC_PER_PAGE = 25;
const baseNow = () => $("baseId").value.trim();
const actorNow = () => $("actor").value.trim();
const ruleText = (ids) => (ids || []).map((r) => RULE_TITLES[r] || r).join(", ");

function tag(text, cls) { return el("span", { class: "tag" + (cls ? " " + cls : ""), text }); }
function say(id, text, kind) { setMsg(id, text, kind); }
function btn(text, handler, opts) {
  const b = el("button", Object.assign({ type: "button", text }, opts || {}));
  if (handler) b.addEventListener("click", handler);
  return b;
}

/* ---------- settings <-> controls ----------
   One column per name. Each row is an option; "names" rows apply to the Planning and Core names, "label" rows to the Core label only. */
const NAME_TARGETS = ["planning_name", "core_name"];
const DEFAULT_MAX = { planning_name: 250, core_name: 255, core_label: 255 };
const MAX_CAP = { planning_name: 250, core_name: 1000, core_label: 1000 };
const SETTING_ROWS = [
  { key: "table_swap", label: "2. Table-name swap", type: "bool", on: ["planning_name", "core_name", "core_label"], count: "table_swap" },
  { key: "char_cleanup", label: "3. Character cleanup (special characters, spaces, 1Q to Q1 in names)", type: "bool", on: ["planning_name", "core_name", "core_label"], count: "char_cleanup" },
  { key: "leading_digits", label: "4. Leading digits: rearrange a name that starts with a digit", type: "bool", on: NAME_TARGETS, count: "leading_digits" },
  { key: "allow_leading_digit", label: "A name may start with a digit", type: "bool", on: NAME_TARGETS },
  { key: "digit_style", label: "If it must not: how to rearrange", type: "style", on: NAME_TARGETS },
  { key: "digit_word", label: "Word to put in front (when rearranging that way)", type: "text", on: NAME_TARGETS, size: "short", max: 40 },
  { key: "max_len", label: "Maximum length", type: "number", on: ["planning_name", "core_name", "core_label"] },
  { key: "extra_allowed", label: "Extra punctuation to allow (it is kept, not removed)", type: "text", on: NAME_TARGETS, size: "short", max: 40, hint: "for example &" },
  { key: "extra_blocked", label: "Extra characters to remove (besides Adobe's unsupported ones)", type: "text", on: ["core_label"], size: "short", max: 40, hint: "for example %" },
  { key: "label_keep_quarter", label: "Keep the client's quarter style (1Q)", type: "bool", on: ["core_label"] },
];
const cellId = (t, k) => "o_" + t + "_" + k;

/* The value of an option for one name: its own setting, else the general one, else the default (the same fallbacks the server uses). */
function effective(s, t, k) {
  const o = (s.targets && s.targets[t]) || {};
  if (o[k] !== undefined) return o[k];
  if (["table_swap", "char_cleanup", "leading_digits", "digit_style", "digit_word", "label_keep_quarter"].includes(k)) return s[k];
  return { max_len: DEFAULT_MAX[t], allow_leading_digit: false, extra_allowed: "", extra_blocked: "" }[k];
}
function buildSettingsGrid() {
  const body = $("settingsGrid").tBodies[0];
  body.replaceChildren();
  for (const row of SETTING_ROWS) {
    const tr = el("tr", {}, el("td", { text: row.label }));
    for (const t of ["planning_name", "core_name", "core_label"]) {
      const td = el("td", {});
      if (!row.on.includes(t)) { td.appendChild(el("span", { class: "hint", text: "-" })); tr.appendChild(td); continue; }
      let input;
      if (row.type === "bool") input = el("input", { type: "checkbox", id: cellId(t, row.key), "aria-label": row.label });
      else if (row.type === "number") input = el("input", { type: "number", id: cellId(t, row.key), min: "1", max: String(MAX_CAP[t]), class: "short", "aria-label": row.label });
      else if (row.type === "style") {
        input = el("select", { id: cellId(t, row.key), "aria-label": row.label }, el("option", { value: "move_to_end", text: "Move the number to the end" }), el("option", { value: "prefix_word", text: "Put a word in front" }));
      } else input = el("input", { type: "text", id: cellId(t, row.key), class: row.size || "", maxlength: String(row.max || 40), placeholder: row.hint || "", autocomplete: "off", "aria-label": row.label });
      input.addEventListener(row.type === "text" || row.type === "number" ? "input" : "change", schedulePreview);
      td.appendChild(input);
      if (row.count) td.appendChild(el("div", { class: "hint", id: "c_" + t + "_" + row.count }));
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
}
function controlSettings() {
  const s = { table_prefix: $("pPrefix").value, targets: {} };
  for (const t of ["planning_name", "core_name", "core_label"]) {
    const o = {};
    for (const row of SETTING_ROWS) {
      if (!row.on.includes(t)) continue;
      const c = $(cellId(t, row.key));
      o[row.key] = row.type === "bool" ? c.checked : row.type === "number" ? (parseInt(c.value, 10) || 0) : c.value.trim();
    }
    s.targets[t] = o;
  }
  /* The general values (used for table names) follow the Planning name; the label's quarter setting is its own. */
  const p = s.targets.planning_name;
  Object.assign(s, { table_swap: p.table_swap, char_cleanup: p.char_cleanup, leading_digits: p.leading_digits, digit_style: p.digit_style, digit_word: p.digit_word,
    label_keep_quarter: s.targets.core_label.label_keep_quarter });
  s.description_include_airtable = $("descAirtable").checked;
  return s;
}
function setControls(s) {
  $("pPrefix").value = s.table_prefix;
  $("descAirtable").checked = !!s.description_include_airtable;
  for (const row of SETTING_ROWS) for (const t of row.on) {
    const c = $(cellId(t, row.key)), v = effective(s, t, row.key);
    if (row.type === "bool") c.checked = !!v; else c.value = v === undefined || v === null ? "" : v;
  }
  syncWordBoxes();
}
function syncWordBoxes() {
  for (const t of NAME_TARGETS) $(cellId(t, "digit_word")).disabled = $(cellId(t, "digit_style")).value !== "prefix_word";
}
/* Two settings are the same when every option has the same effective value for every name. */
function matrix(s) {
  const m = { table_prefix: s.table_prefix, description_include_airtable: String(!!s.description_include_airtable) };
  for (const t of ["planning_name", "core_name", "core_label"]) for (const row of SETTING_ROWS) if (row.on.includes(t)) m[t + "." + row.key] = String(effective(s, t, row.key));
  return JSON.stringify(m);
}
const sameSettings = (a, b) => !!a && !!b && matrix(a) === matrix(b);
const isPreview = () => !!N.saved && !sameSettings(controlSettings(), N.saved);

/* ---------- talking to the server ---------- */
async function load() {
  const base = baseNow();
  if (!/^app[A-Za-z0-9]{4,}$/.test(base)) { N.R = null; render(); return; }
  try {
    const r = await api("GET", "/api/names/review?base_id=" + encodeURIComponent(base));
    N.saved = r.settings; N.R = r; N.page = 0; setControls(r.settings);
    say("pageMessage", "");
  } catch (e) { N.R = null; say("pageMessage", e.message, "err"); }
  render();
}
async function preview() {
  if (!N.R) return;
  try {
    N.R = await api("POST", "/api/names/preview", { base_id: baseNow(), settings: controlSettings() });
    N.page = 0; say("pageMessage", "");
  } catch (e) { say("pageMessage", e.message, "err"); }
  render();
}
function schedulePreview() {
  syncWordBoxes();
  clearTimeout(N.timer);
  N.timer = setTimeout(preview, 250);
}
/* A decision: always sends the base and the reviewer, refuses to run on unsaved preview settings, shows the server's reason on a refusal. */
async function decide(path, body, msgId, okText) {
  if (isPreview()) { say(msgId, "Save the settings first: decisions are made under saved settings only.", "err"); return null; }
  if (!actorNow()) { say(msgId, "Enter your initials (top of the page) before making a decision.", "err"); $("actor").focus(); return null; }
  try {
    const r = await api("POST", path, Object.assign({ base_id: baseNow(), actor: actorNow() }, body));
    if (r.review) { N.R = r.review; N.saved = r.review.settings; }
    N.open = null; render();
    say(msgId, typeof okText === "function" ? okText(r) : okText || "Done.", "ok");
    return r;
  } catch (e) { say(msgId, e.message, "err"); return null; }
}

/* ---------- render: everything is rebuilt from N.R ---------- */
function render() {
  const R = N.R, on = !!R;
  for (const id of ["settingsCard", "importCard", "tablesCard", "fieldsCard", "descCard", "downloadCard"]) $(id).hidden = !on && id !== "settingsCard" ? true : false;
  if (!on) { $("tablesGrid").tBodies[0].replaceChildren(); $("fieldsBody").hidden = true; $("fieldsLocked").hidden = false; $("dupCard").hidden = true; return; }
  renderSettings(); renderTables(); renderFields(); renderDuplicates(); renderDescriptions(); renderDownloads();
}

function renderSettings() {
  const R = N.R, ready = R.gate.ready;
  for (const row of SETTING_ROWS) {
    if (!row.count) continue;
    for (const t of row.on) {
      const n = $("c_" + t + "_" + row.count);
      if (n) n.textContent = ready ? "changes " + fmt(((R.summary[t] || { steps: {} }).steps || {})[row.count] || 0) + " name(s)" : "counts appear once table names are approved";
    }
  }
  $("previewNote").hidden = !isPreview();
  $("settingsState").textContent = isPreview() ? "Preview: not saved." : (R.settings_saved ? "Saved." : "Defaults (not saved yet).");
  $("btnSaveSettings").disabled = !isPreview() && R.settings_saved;
}

function scopeTag(t) {
  const label = { in: "in scope", out: "out", undecided: "undecided" }[t.scope];
  const tip = t.scope === "undecided" ? "No scope decision yet: still checked" : (t.scope_by ? "Decided by " + t.scope_by + (t.scope_reason ? ": " + t.scope_reason : "") : "");
  const n = tag(label, t.scope === "out" ? "fail" : t.scope === "in" ? "done" : "stale");
  if (tip) n.title = tip;
  return n;
}
/* Scope: who decided and the controls to decide. Undecided (no row) is different from out; the controls need migration 008. */
function scopeCell(t, locked) {
  const R = N.R, td = el("td", {}, scopeTag(t));
  if (!R.scope_available) { td.appendChild(el("div", { class: "hint", text: "Scope decisions need migration 008" })); return td; }
  const set = (decision) => decide("/api/names/tables/scope", { items: [{ table_id: t.table_id, decision, reason: "set from the page" }] }, "tablesMessage", "Scope saved.");
  const row = el("div", { class: "row" });
  row.appendChild(btn("In", () => set("in"), { disabled: locked || t.scope === "in" }));
  row.appendChild(btn("Out", () => { N.open = { kind: "scope_out", table_id: t.table_id }; render(); }, { disabled: locked || t.scope === "out" }));
  if (t.scope !== "undecided") row.appendChild(btn("Clear", () => set("clear"), { disabled: locked }));
  td.appendChild(row);
  if (t.scope === "in" && R.targets_available) {
    const sel = el("select", { "aria-label": "Where " + t.old + " lands", disabled: locked || null },
      el("option", { value: "", text: "Target not decided" }), el("option", { value: "planning_only", text: "Planning only" }), el("option", { value: "planning_and_core", text: "Planning plus Core" }));
    sel.value = t.target || "";
    sel.addEventListener("change", () => { if (sel.value) decide("/api/names/tables/scope", { items: [{ table_id: t.table_id, target: sel.value }] }, "tablesMessage", "Target saved."); });
    td.appendChild(sel);
  }
  return td;
}
function scopeOutRow(t) {
  const input = el("input", { type: "text", class: "wide", maxlength: "500", placeholder: "Why is this table out of scope? (required)", "aria-label": "Reason" });
  setTimeout(() => input.focus(), 0);
  return el("tr", { class: "reason" }, el("td", { colspan: "8" }, el("div", { class: "row" }, el("b", { text: "Take " + t.old + " out of scope" }), input,
    btn("Confirm", () => decide("/api/names/tables/scope", { items: [{ table_id: t.table_id, decision: "out", reason: input.value }] }, "tablesMessage", "Table taken out of scope."), {}),
    btn("Cancel", () => { N.open = null; render(); }))));
}
function renderScopeWarnings() {
  const box = $("scopeWarnings"), w = N.R.scope_warnings || [];
  box.replaceChildren();
  if (!w.length) return;
  box.appendChild(el("div", { class: "note warn" }, el("b", { text: w.length + " field(s) in in-scope tables link to, or name, a table that is out of scope" }),
    el("div", { text: "A link to a dropped table has nowhere to point. Decide for each: bring the table back in scope, drop the field, or change what it points at." }),
    el("div", { style: "overflow-x:auto" }, el("table", { class: "grid" }, el("thead", {}, el("tr", {}, ["Field", "In table", "Out-of-scope table", "How"].map((h) => el("th", { text: h })))),
      el("tbody", {}, w.map((x) => el("tr", {}, el("td", { text: x.field_name }), el("td", { text: x.table_name }), el("td", { text: x.out_table_name }),
        el("td", { text: x.kind === "link" ? "links to it" : "its name mentions it" }))))))));
}
function verdictTag(v) { const x = VERDICTS[v]; return x ? tag(x[0], x[1]) : tag("-"); }

function renderTables() {
  const R = N.R, body = $("tablesGrid").tBodies[0];
  const inScope = R.tables.filter((t) => t.scope !== "out");
  const pending = R.gate.pending.length;
  const note = $("gateNote");
  note.className = "note " + (pending ? "warn" : "ok");
  note.textContent = pending
    ? pending + " of " + inScope.length + " table name(s) still need approval before fields can be reviewed."
    : "All " + inScope.length + " table name(s) are approved. Field review is open.";
  const locked = isPreview();
  renderScopeWarnings();
  $("btnApproveUnchanged").disabled = locked; $("btnApproveAutoTables").disabled = locked;
  body.replaceChildren();
  for (const t of R.tables) {
    const row = el("tr", { class: t.scope === "out" ? "muted" : "" });
    row.appendChild(scopeCell(t, locked));
    row.appendChild(el("td", {}, el("div", { text: t.old }), el("div", { class: "hint", text: t.table_id })));
    row.appendChild(el("td", { class: "num", text: fmt(t.field_count) }));
    if (t.scope === "out") {
      row.appendChild(el("td", { colspan: "5", class: "hint", text: "Out of scope" + (t.scope_by ? " (" + t.scope_by + (t.scope_reason ? ": " + t.scope_reason : "") + ")" : "") + ": skipped, no name decision needed." }));
      body.appendChild(row); continue;
    }
    row.appendChild(el("td", {}, verdictTag(t.verdict), t.rules.length ? el("div", { class: "hint", text: ruleText(t.rules) }) : null));
    row.appendChild(el("td", { text: t.suggested === t.old ? "(unchanged)" : t.suggested }));
    const input = el("input", { type: "text", maxlength: "250", value: t.approved ? t.effective : (t.suggested || t.old), "aria-label": "New name for " + t.old });
    row.appendChild(el("td", {}, input));
    row.appendChild(el("td", {}, t.approved ? [tag("Approved", "done"), el("div", { class: "hint", text: (t.approved_by || "") + " " + (t.approved_at || "") })] : tag("Pending", "stale")));
    const actions = el("td", {});
    actions.appendChild(btn(t.approved ? "Approve this name" : "Approve", () => decide("/api/names/tables/approve", { items: [{ table_id: t.table_id, name: input.value }] }, "tablesMessage", "Table name approved."), { disabled: locked }));
    if (t.approved) actions.appendChild(btn("Withdraw", () => decide("/api/names/tables/revoke", { table_ids: [t.table_id], reason: "withdrawn from the page" }, "tablesMessage", "Approval withdrawn."), { disabled: locked }));
    row.appendChild(actions);
    body.appendChild(row);
    if (N.open && N.open.kind === "scope_out" && N.open.table_id === t.table_id) body.appendChild(scopeOutRow(t));
  }
}

/* ---------- field review ---------- */
function fieldMatches(f) {
  const t = f.targets[N.target];
  if (f.abandoned) return $("fAbandoned").checked;
  if (!t) return false;
  const v = $("fVerdict").value, s = $("fStatus").value, tb = $("fTable").value, q = $("fSearch").value.trim().toLowerCase();
  if (v && t.verdict !== v) return false;
  if (s && t.status !== s) return false;
  if (tb && f.table_id !== tb) return false;
  if (q && !(f.original.toLowerCase().includes(q) || f.field_id.toLowerCase().includes(q) || (t.candidate || "").toLowerCase().includes(q))) return false;
  return true;
}

function presenceCell(f) {
  const p = f.presence;
  const bits = [p.populated + " with values", p.null_count + " empty", "cardinality " + p.cardinality_display];
  return el("td", {}, el("div", { text: f.data_type }), el("div", { class: "hint", text: bits.join(" · ") }), p.profiled ? null : tag("not profiled", "stale"),
    f.target_type ? el("div", { class: "hint", text: "target: " + f.target_type }) : null);
}

/* The decision cell for one field and the selected target: a name box (pre-filled with the proposal), Approve, Flag, Withdraw, Abandon. */
function decisionCell(f, target, locked) {
  const t = f.targets[target];
  const input = el("input", { type: "text", maxlength: "300", value: t.approved_name || t.proposed || t.candidate, "aria-label": "Name to approve for " + f.original });
  const cell = el("td", {});
  cell.appendChild(input);
  const row = el("div", { class: "row" });
  row.appendChild(btn("Approve", () => decide("/api/names/fields/approve", { items: [{ field_id: f.field_id, target, name: input.value }] }, "fieldsMessage", "Name approved."), { disabled: locked }));
  row.appendChild(btn("Flag", () => decide("/api/names/fields/status", { items: [{ field_id: f.field_id, target }], status: "needs_human" }, "fieldsMessage", "Flagged for a person."), { disabled: locked || t.status === "needs_human" }));
  if (t.status === "approved") row.appendChild(btn("Withdraw", () => { N.open = { kind: "withdraw", field_id: f.field_id, target }; render(); }, { disabled: locked }));
  if (f.presence.can_abandon) row.appendChild(btn("Abandon", () => { N.open = { kind: "abandon", field_id: f.field_id }; render(); }, { disabled: locked }));
  cell.appendChild(row);
  return cell;
}

function fieldRow(f, target, locked) {
  const t = f.targets[target];
  const tr = el("tr", {});
  tr.appendChild(el("td", { text: f.table_name }));
  tr.appendChild(el("td", {}, el("div", { text: f.original }), el("div", { class: "hint", text: f.field_id })));
  tr.appendChild(presenceCell(f));
  tr.appendChild(el("td", {}, t.source === "manual" ? el("div", { text: t.candidate }) : el("span", { class: "hint", text: "(Airtable name)" }),
    t.differs_from_rubric ? el("div", { class: "hint", text: "rules alone would give: " + t.rubric_name }) : null));
  tr.appendChild(el("td", { text: t.proposed || (t.verdict === "PASS" ? "-" : "") }));
  tr.appendChild(el("td", {}, verdictTag(t.verdict), t.rules.length ? el("div", { class: "hint", text: ruleText(t.rules) }) : null,
    ...(t.reasons || []).map((x) => el("div", { class: "hint", text: x }))));
  tr.appendChild(el("td", {}, tag(STATUS_TEXT[t.status], t.status === "approved" ? "done" : t.status === "needs_human" ? "stale" : ""),
    t.approved_name ? el("div", { class: "hint", text: t.approved_name }) : null, t.stale ? tag("approved name no longer passes", "fail") : null));
  tr.appendChild(decisionCell(f, target, locked));
  return tr;
}

/* An open reason form (abandon, un-abandon, withdraw) shown under its row. */
function reasonRow(f, cols) {
  const o = N.open;
  const input = el("input", { type: "text", class: "wide", maxlength: "500", placeholder: o.kind === "abandon" ? "Why is this field being abandoned? (required)" : "Reason (required)", "aria-label": "Reason" });
  const run = () => {
    if (o.kind === "abandon") return decide("/api/names/fields/abandon", { field_id: f.field_id, reason: input.value }, "fieldsMessage", "Field abandoned and recorded.");
    if (o.kind === "unabandon") return decide("/api/names/fields/unabandon", { field_id: f.field_id, reason: input.value }, "fieldsMessage", "Field restored.");
    return decide("/api/names/fields/status", { items: [{ field_id: f.field_id, target: o.target }], status: "unreviewed", reason: input.value }, "fieldsMessage", "Approval withdrawn.");
  };
  const title = { abandon: "Abandon this field", unabandon: "Restore this abandoned field", withdraw: "Withdraw this approval" }[o.kind];
  const td = el("td", { colspan: String(cols) }, el("div", { class: "row" }, el("b", { text: title }), input, btn("Confirm", run), btn("Cancel", () => { N.open = null; render(); })));
  setTimeout(() => input.focus(), 0);
  return el("tr", { class: "reason" }, td);
}

function renderFields() {
  const R = N.R, ready = R.gate.ready;
  $("fieldsLocked").hidden = ready; $("fieldsBody").hidden = !ready;
  if (!ready) return;
  const locked = isPreview();
  const strip = $("summaryStrip"); strip.replaceChildren();
  for (const [t, name] of TARGETS) {
    const v = R.summary[t].verdicts, c = R.counts[t];
    strip.appendChild(el("div", { class: "note info" }, el("b", { text: name }),
      el("div", { text: fmt(v.PASS) + " pass · " + fmt(v.AUTO_FIXABLE) + " auto-fixable · " + fmt(v.NEEDS_HUMAN) + " need a person" }),
      el("div", { text: "approved " + fmt(c.approved) + " · flagged " + fmt(c.needs_human) + " · unreviewed " + fmt(c.unreviewed) + (c.stale ? " · " + c.stale + " no longer pass" : "") })));
  }
  const tabs = $("targetTabs"); tabs.replaceChildren();
  for (const [t, name] of TARGETS) tabs.appendChild(btn(name, () => { N.target = t; N.page = 0; render(); }, { class: t === N.target ? "primary" : "", role: "tab", "aria-selected": t === N.target ? "true" : "false" }));
  const sel = $("fTable"), keep = sel.value;
  sel.replaceChildren(el("option", { value: "", text: "All tables" }), ...R.tables.filter((x) => x.scope !== "out").map((x) => el("option", { value: x.table_id, text: x.old })));
  sel.value = keep;
  for (const id of ["btnApprovePass", "btnApproveAuto", "btnApproveAllTargets"]) $(id).disabled = locked;
  const rows = R.fields.filter(fieldMatches);
  const pages = Math.max(1, Math.ceil(rows.length / PER_PAGE));
  N.page = Math.min(N.page, pages - 1);
  $("fieldsCount").textContent = fmt(rows.length) + " of " + fmt(R.fields.length) + " fields shown";
  $("pageInfo").textContent = "Page " + (N.page + 1) + " of " + pages;
  $("btnPrev").disabled = N.page === 0; $("btnNext").disabled = N.page >= pages - 1;
  const body = $("fieldsGrid").tBodies[0]; body.replaceChildren();
  for (const f of rows.slice(N.page * PER_PAGE, (N.page + 1) * PER_PAGE)) {
    if (f.abandoned) {
      body.appendChild(el("tr", { class: "muted" }, el("td", { text: f.table_name }), el("td", {}, el("div", { text: f.original }), el("div", { class: "hint", text: f.field_id })), presenceCell(f),
        el("td", { colspan: "4", class: "hint", text: "Abandoned by " + (f.abandon.by || "?") + " on " + (f.abandon.at || "?") + ": " + (f.abandon.reason || "") }),
        el("td", {}, btn("Restore", () => { N.open = { kind: "unabandon", field_id: f.field_id }; render(); }, { disabled: locked }))));
    } else body.appendChild(fieldRow(f, N.target, locked));
    if (N.open && N.open.field_id === f.field_id) body.appendChild(reasonRow(f, 8));
  }
}

function renderDuplicates() {
  const R = N.R, card = $("dupCard"), body = $("dupBody");
  card.hidden = !R.gate.ready || !R.clusters.length;
  body.replaceChildren();
  if (card.hidden) return;
  const byId = Object.fromEntries(R.fields.map((f) => [f.field_id, f]));
  const locked = isPreview();
  for (const c of R.clusters) {
    const tname = (R.tables.find((x) => x.table_id === c.table_id) || {}).old || c.table_id;
    const label = TARGETS.find(([t]) => t === c.target)[1];
    const tbody = el("tbody", {});
    for (const m of c.members) {
      const f = byId[m.field_id];
      if (!f) continue;
      tbody.appendChild(fieldRow(f, c.target, locked));
      if (N.open && N.open.field_id === f.field_id) tbody.appendChild(reasonRow(f, 8));
    }
    body.appendChild(el("div", { class: "note warn" },
      el("b", { text: label + " in " + tname + ": " + c.members.length + " fields would share the name “" + c.members[0].final + "”" }),
      el("div", { style: "overflow-x:auto" }, el("table", { class: "grid" },
        el("thead", {}, el("tr", {}, ["Table", "Field", "Data", "Your name", "Repaired name", "Check", "Status", "Decision"].map((h) => el("th", { text: h })))), tbody))));
  }
}

/* ---------- descriptions (lineage syntax): generated each time, a hand edit is saved ---------- */
function descMatches(f) {
  const d = f.description, tb = $("dTable").value, q = $("dSearch").value.trim().toLowerCase();
  if (f.abandoned) return false;
  if (tb && f.table_id !== tb) return false;
  if ($("dEdited").checked && !d.edited) return false;
  if ($("dProblems").checked && !d.problems.length) return false;
  if (q && !(f.original.toLowerCase().includes(q) || f.field_id.toLowerCase().includes(q) || d.text.toLowerCase().includes(q))) return false;
  return true;
}
function descRow(f, locked) {
  const d = f.description;
  const box = el("textarea", { rows: "2", maxlength: "4000", "aria-label": "Description of " + f.original }, d.text);
  box.value = d.text;
  const save = btn("Save", () => decide("/api/names/descriptions", { items: [{ field_id: f.field_id, text: box.value }] }, "descMessage", (r) =>
    r.saved ? "Description saved." + (r.warnings && r.warnings.length ? " Note: " + r.warnings.map((w) => w.message).join(" ") : "") : "Nothing changed."), { disabled: true });
  const reset = btn("Reset", () => decide("/api/names/descriptions", { items: [{ field_id: f.field_id, text: "" }] }, "descMessage", "Back to the generated description."), { disabled: locked || d.saved === null });
  box.addEventListener("input", () => { save.disabled = locked || box.value === d.text; });
  const tr = el("tr", {});
  tr.appendChild(el("td", { text: f.table_name }));
  tr.appendChild(el("td", {}, el("div", { text: f.original }), el("div", { class: "hint", text: f.field_id + " \u00b7 " + f.data_type })));
  tr.appendChild(el("td", { style: "min-width:340px" }, box));
  tr.appendChild(el("td", {}, tag(d.edited ? "Edited by hand" : "Generated", d.edited ? "run" : ""), ...d.problems.map((p) => el("div", { class: "hint", text: p.message }))));
  tr.appendChild(el("td", {}, el("div", { class: "row" }, save, reset)));
  return tr;
}
function renderDescriptions() {
  const R = N.R, ready = R.gate.ready;
  $("descLocked").hidden = ready; $("descBody").hidden = !ready;
  if (!ready) return;
  const locked = isPreview();
  const sel = $("dTable"), keep = sel.value;
  sel.replaceChildren(el("option", { value: "", text: "All tables" }), ...R.tables.filter((x) => x.scope !== "out").map((x) => el("option", { value: x.table_id, text: x.old })));
  sel.value = keep;
  const rows = R.fields.filter(descMatches);
  const pages = Math.max(1, Math.ceil(rows.length / DESC_PER_PAGE));
  N.descPage = Math.min(N.descPage, pages - 1);
  $("descCount").textContent = fmt(rows.length) + " of " + fmt(R.fields.length) + " fields shown";
  $("descPageInfo").textContent = "Page " + (N.descPage + 1) + " of " + pages;
  $("btnDescPrev").disabled = N.descPage === 0; $("btnDescNext").disabled = N.descPage >= pages - 1;
  const body = $("descGrid").tBodies[0]; body.replaceChildren();
  for (const f of rows.slice(N.descPage * DESC_PER_PAGE, (N.descPage + 1) * DESC_PER_PAGE)) body.appendChild(descRow(f, locked));
}

function renderDownloads() {
  const box = $("downloads"); box.replaceChildren();
  const base = encodeURIComponent(baseNow());
  const link = (text, file, primary) => el("a", { class: "btnlink" + (primary ? " primary" : ""), href: "/api/names/report?base_id=" + base + "&file=" + file, text });
  box.appendChild(link("Download all (zip)", "all", true));
  for (const [text, file] of [["Review grid .csv", "names_report.csv"], ["Your names vs the rules .csv", "names_diff.csv"], ["Duplicate groups .csv", "names_duplicates.csv"],
    ["Table names .csv", "names_tables.csv"], ["Summary .txt", "names_summary.txt"], ["Rules .md", "FIELD_NAMING_RULES.md"]]) box.appendChild(link(text, file, false));
}

/* ---------- import ---------- */
function toBase64(buf) {
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}
async function doImport() {
  const file = $("importFile").files[0];
  const out = $("importResult"); out.replaceChildren();
  if (!file) { out.appendChild(el("div", { class: "note err", text: "Choose a CSV file first." })); return; }
  if (!actorNow()) { out.appendChild(el("div", { class: "note err", text: "Enter your initials (top of the page) first." })); return; }
  let r;
  try {
    const buf = await file.arrayBuffer();
    r = await api("POST", "/api/names/import", { base_id: baseNow(), actor: actorNow(), csv_base64: toBase64(buf) });
  } catch (e) { out.appendChild(el("div", { class: "note err", text: e.message })); return; }
  N.R = r.review; N.saved = r.review.settings; render();
  const kinds = {};
  for (const p of r.problems) kinds[p.kind] = (kinds[p.kind] || 0) + 1;
  out.appendChild(el("div", { class: "note ok", text: "Imported " + fmt(r.names_imported) + " name(s) from " + fmt(r.rows) + " row(s)" + (r.kept_approved ? "; " + fmt(r.kept_approved) + " already approved name(s) were kept." : ".") }));
  if (r.note) out.appendChild(el("div", { class: "note warn", text: r.note }));
  if (r.problem_count) {
    const shown = r.problems.slice(0, 200);
    out.appendChild(el("div", { class: "note warn" }, el("b", { text: r.problem_count + " problem(s) to look at: " + Object.entries(kinds).map(([k, n]) => k.toLowerCase().replace(/_/g, " ") + " " + n).join(", ") }),
      el("div", { style: "overflow-x:auto" }, el("table", { class: "grid" },
        el("thead", {}, el("tr", {}, ["Level", "Problem", "Field ID", "Detail"].map((h) => el("th", { text: h })))),
        el("tbody", {}, shown.map((p) => el("tr", {}, el("td", { text: p.severity }), el("td", { text: p.kind.toLowerCase().replace(/_/g, " ") }), el("td", { text: p.field_id }), el("td", { text: p.detail }))))))));
  }
}

/* ---------- page wiring ---------- */
function onReadyChange() {
  for (const id of ["btnImport", "btnSaveSettings"]) $(id).disabled = !state.ready;
}
async function afterConnect() {
  await loadKnownBases();
  await load();
}
const guarded = (fn) => async () => { try { await fn(); } catch (e) { showGlobalError(e.message); } };

startPage("names", { onReadyChange, afterConnect }, () => {
  initBaseCombo();
  try { $("actor").value = sessionStorage.getItem(ACTOR_KEY) || ""; } catch (e) { /* storage can be blocked */ }
  $("actor").addEventListener("input", () => { try { sessionStorage.setItem(ACTOR_KEY, actorNow()); } catch (e) { /* ignore */ } });
  $("baseId").addEventListener("input", guarded(load));
  buildSettingsGrid();
  $("pPrefix").addEventListener("change", schedulePreview);
  $("btnSaveSettings").addEventListener("click", async () => {
    if (!actorNow()) { say("pageMessage", "Enter your initials first.", "err"); $("actor").focus(); return; }
    try {
      N.R = await api("POST", "/api/names/settings", { base_id: baseNow(), actor: actorNow(), settings: controlSettings(), reason: "saved from the page" });
      N.saved = N.R.settings; say("pageMessage", "Settings saved.", "ok");
    } catch (e) { say("pageMessage", e.message, "err"); }
    render();
  });
  $("btnImport").addEventListener("click", guarded(doImport));
  const bulk = (path, body, msg, ok) => () => decide(path, body, msg, ok);
  const tablesOk = (r) => r.approved + " table name(s) approved" + (r.skipped && r.skipped.length ? "; " + r.skipped.length + " skipped (" + r.skipped.map((s) => s.name + ": " + s.reason).join("; ") + ")" : ".");
  $("btnApproveUnchanged").addEventListener("click", bulk("/api/names/tables/approve", { mode: "unchanged" }, "tablesMessage", tablesOk));
  $("btnApproveAutoTables").addEventListener("click", bulk("/api/names/tables/approve", { mode: "auto" }, "tablesMessage", tablesOk));
  const fieldsOk = (r) => r.approved + " name(s) approved.";
  $("btnApprovePass").addEventListener("click", () => decide("/api/names/fields/approve", { mode: "pass", targets: [N.target] }, "fieldsMessage", fieldsOk));
  $("btnApproveAuto").addEventListener("click", () => decide("/api/names/fields/approve", { mode: "auto", targets: [N.target] }, "fieldsMessage", fieldsOk));
  $("btnApproveAllTargets").addEventListener("click", () => decide("/api/names/fields/approve", { mode: "pass_and_auto" }, "fieldsMessage", fieldsOk));
  $("descAirtable").addEventListener("change", schedulePreview);
  for (const id of ["dTable", "dEdited", "dProblems"]) $(id).addEventListener("change", () => { N.descPage = 0; renderDescriptions(); });
  $("dSearch").addEventListener("input", () => { N.descPage = 0; renderDescriptions(); });
  $("btnDescPrev").addEventListener("click", () => { N.descPage = Math.max(0, N.descPage - 1); renderDescriptions(); });
  $("btnDescNext").addEventListener("click", () => { N.descPage += 1; renderDescriptions(); });
  for (const id of ["fVerdict", "fStatus", "fTable", "fAbandoned"]) $(id).addEventListener("change", () => { N.page = 0; renderFields(); });
  $("fSearch").addEventListener("input", () => { N.page = 0; renderFields(); });
  $("btnPrev").addEventListener("click", () => { N.page = Math.max(0, N.page - 1); renderFields(); });
  $("btnNext").addEventListener("click", () => { N.page += 1; renderFields(); });
});
