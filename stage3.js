"use strict";
/* Stage 3: Automations. A manual recorder: a trigger, then an ordered tree of steps (actions, if / else-if / else blocks and repeating
   groups). Conditions are nested ALL / ANY groups. Text is Markdown, parsed by the server (editor-kit.js). Everything is inserted with
   textContent, never innerHTML. */

const TRIGGERS = {
  record_created: "When a record is created", record_updated: "When a record is updated", record_matches: "When a record matches conditions",
  record_enters_view: "When a record enters a view", form_submitted: "When a form is submitted", button_clicked: "When a button is clicked",
  scheduled: "At a scheduled time", webhook: "When a webhook is received", other: "Other trigger",
};
const NEEDS_TABLE = ["record_created", "record_updated", "record_matches", "record_enters_view", "form_submitted", "button_clicked"];
const ACTIONS = {
  create_record: "Create record", update_record: "Update record", find_records: "Find records", send_email: "Send email",
  send_message: "Send message", run_script: "Run script", send_request: "Send request (webhook / API call)", delay: "Wait / delay", other: "Other action",
};
const RECORD_ACTIONS = ["create_record", "update_record", "find_records"];
const PRODUCING = ["create_record", "update_record", "find_records"];
const OPS = [["equals", "is exactly"], ["notEquals", "is not"], ["contains", "contains"], ["doesNotContain", "does not contain"],
  ["isEmpty", "is empty"], ["isNotEmpty", "is not empty"], ["greaterThan", "is greater than"], ["greaterOrEqual", "is greater than or equal to"],
  ["lessThan", "is less than"], ["lessOrEqual", "is less than or equal to"], ["isAnyOf", "is any of"], ["isNoneOf", "is none of"],
  ["isBefore", "is before"], ["isAfter", "is after"]];
const NO_VALUE = ["isEmpty", "isNotEmpty"];
/* The labelled text details each action type usually has. plain = kept exactly as typed (code, JSON), not read as Markdown. */
const SETTING_DEFAULTS = {
  send_email: [["To", false], ["Subject", false], ["Body", false]],
  send_message: [["Channel or recipient", false], ["Message", false]],
  run_script: [["Input variables", true], ["Script", true]],
  send_request: [["Method", false], ["URL", false], ["Headers", true], ["Body", true]],
  delay: [["Duration", false]],
  other: [["Details", false]],
};

const S = { auto: null, trigger: null, steps: [], seq: 0, dirty: false, savedVersion: null, versions: [], collapsed: new Set(), keyLocked: false, autos: [] };
const currentBase = () => $("baseId").value.trim();
const newId = () => "n" + (++S.seq);
function markDirty() { S.dirty = true; $("btnSave").textContent = "Save as a new version" + (S.savedVersion ? " (after v" + S.savedVersion + ")" : ""); }
const newGroup = () => ({ type: "group", junction: "AND", items: [] });

/* ---------- schema lookups (from Stage 1's ingested schema) ---------- */
function baseTables() {
  const b = (state.schema || []).find((x) => x.base_id === (S.auto ? S.auto.base_id : currentBase()));
  return b ? b.tables.filter((t) => !t.stale) : [];
}
const tableName = (tid) => { const t = baseTables().find((x) => x.table_id === tid); return t ? t.table_name : (tid || ""); };
function fieldInfo(fid) {
  for (const t of baseTables()) for (const f of t.fields) if (f.field_id === fid) return { field_id: fid, field_name: f.field_name, data_type: f.data_type, table_name: t.table_name, table_id: t.table_id };
  return null;
}
const fieldName = (fid) => { const f = fieldInfo(fid); return f ? f.field_name : (fid || ""); };
/* fields of one table, or of every table when tid is blank */
function fieldOptions(tid) {
  const out = [];
  for (const t of baseTables()) {
    if (tid && t.table_id !== tid) continue;
    for (const f of t.fields) if (!f.stale) out.push({ id: f.field_id, label: tid ? f.field_name : f.field_name + " (" + t.table_name + ")", sub: t.table_name + " · " + f.data_type + " · " + f.field_id });
  }
  return out;
}
const tableOptions = () => baseTables().map((t) => ({ id: t.table_id, label: t.table_name }));

/* ---------- tree helpers ---------- */
function flat() {
  const out = [{ item: S.trigger, kind: "trigger", path: "0", order: 0 }];
  (function steps(list, prefix) {
    list.forEach((st, i) => {
      const path = (prefix ? prefix + "." : "") + (i + 1);
      out.push({ item: st, kind: "step", path, order: out.length });
      if (st.type === "conditional") st.branches.forEach((b, j) => { const bp = path + "." + (j + 1); out.push({ item: b, kind: "branch", path: bp, order: out.length }); steps(b.steps, bp); });
      else if (st.type === "repeat") steps(st.steps, path);
    });
  })(S.steps, "");
  return out;
}
const isProducing = (it) => (it.type === "action" && PRODUCING.includes(it.action_type)) || it.type === "repeat";
function earlierProducers(item) {
  const all = flat(), me = all.find((f) => f.item === item);
  return all.filter((f) => f.order < me.order && f.kind === "step" && isProducing(f.item));
}
const stepLabel = (f) => "Step " + f.path + ": " + (f.item.title || "(untitled)");
/* the table of the records a step produces (a repeat produces the records it loops over) */
function producedTable(id) {
  const f = flat().find((x) => x.item.id === id);
  if (!f) return "";
  const it = f.item;
  if (it.type === "repeat" && it.over_source) return producedTable(it.over_source) || it.table_id || "";
  return it.table_id || "";
}
function locate(id, arr, owner, cond) {
  arr = arr || S.steps;
  for (let i = 0; i < arr.length; i++) {
    const st = arr[i];
    if (st.id === id) return { arr, idx: i, owner: owner || null, cond: cond || null };
    if (st.type === "repeat") { const r = locate(id, st.steps, st, null); if (r) return r; }
    if (st.type === "conditional") for (const b of st.branches) { const r = locate(id, b.steps, b, st); if (r) return r; }
  }
  return null;
}
function makeStep(kind, actionType) {
  const id = newId();
  if (kind === "conditional") {
    return { id, type: "conditional", title: "", description_md: "", branches: [
      { id: newId(), type: "branch", label: "", is_else: false, conditions: newGroup(), steps: [] }] };
  }
  if (kind === "repeat") return { id, type: "repeat", title: "", description_md: "", over_text: "", over_source: "", table_id: "", steps: [] };
  const st = { id, type: "action", action_type: actionType, title: ACTIONS[actionType], description_md: "", table_id: "", field_refs: [], settings: [] };
  st.settings = (SETTING_DEFAULTS[actionType] || []).map(([name, plain]) => plain ? { name, format: "plain", value: "" } : { name, format: "rich", value_md: "" });
  if (actionType === "update_record") st.record_source = "";
  if (actionType === "find_records") st.conditions = newGroup();
  return st;
}
function stripBlocks(o) {
  if (Array.isArray(o)) return o.map(stripBlocks);
  if (o && typeof o === "object") { const r = {}; for (const [k, v] of Object.entries(o)) if (!k.endsWith("_blocks")) r[k] = stripBlocks(v); return r; }
  return o;
}
function describe(st) {
  if (st.type === "conditional") return st.title || "(untitled conditional)";
  return st.title || "(untitled)";
}
const kindLabel = (st) => (st.type === "action" ? ACTIONS[st.action_type] : st.type === "repeat" ? "Repeat for each" : "Conditional");

function moveStep(id, d) {
  const loc = locate(id), j = loc.idx + d;
  if (j < 0 || j >= loc.arr.length) return;
  [loc.arr[loc.idx], loc.arr[j]] = [loc.arr[j], loc.arr[loc.idx]];
  markDirty(); renderAll();
}
function outdent(id) {
  const loc = locate(id);
  if (!loc.owner) return;
  const container = loc.cond || loc.owner;                 // the repeat, or the conditional that holds this branch
  const cl = locate(container.id), item = loc.arr.splice(loc.idx, 1)[0];
  cl.arr.splice(cl.idx + 1, 0, item);
  markDirty(); renderAll();
}
function indent(id) {
  const loc = locate(id), prev = loc.arr[loc.idx - 1];
  if (!prev) return;
  let target = null;
  if (prev.type === "repeat") target = prev.steps;
  else if (prev.type === "conditional" && prev.branches.length) target = prev.branches[prev.branches.length - 1].steps;
  if (!target) return;
  target.push(loc.arr.splice(loc.idx, 1)[0]);
  S.collapsed.delete(prev.id);
  markDirty(); renderAll();
}
function removeStep(id) {
  const loc = locate(id), st = loc.arr[loc.idx];
  const n = (st.steps ? st.steps.length : 0) + (st.branches ? st.branches.reduce((a, b) => a + b.steps.length, 0) : 0);
  if (n && !window.confirm("This removes " + n + " step(s) inside it as well. Continue?")) return;
  loc.arr.splice(loc.idx, 1);
  markDirty(); renderAll();
}
function addStep(arr, value) {
  const [kind, at] = value.split(":");
  arr.push(makeStep(kind, at));
  markDirty(); renderAll();
}

/* ---------- condition groups (nested ALL / ANY) ---------- */
/* ctx: {owner, mode: "trigger" | "find" | "flow", tableId} */
function sourceSelect(cond, ctx) {
  const sel = el("select", { title: "Which record the field is read from" }, el("option", { value: "", text: "the trigger record" }),
    earlierProducers(ctx.owner).map((f) => el("option", { value: f.item.id, text: "record from " + stepLabel(f) })));
  sel.value = cond.left.source || "";
  if (sel.value !== (cond.left.source || "")) sel.value = "";
  sel.addEventListener("change", () => { cond.left.source = sel.value; markDirty(); renderAll(); });
  return sel;
}
function conditionRow(group, idx, ctx) {
  const c = group.items[idx];
  const kind = el("select", {}, el("option", { value: "field", text: "Field" }), el("option", { value: "text", text: "Other value" }));
  kind.value = c.left.kind;
  kind.addEventListener("change", () => { c.left = kind.value === "text" ? { kind: "text", text: "" } : { kind: "field", field_id: "", source: "" }; markDirty(); renderAll(); });
  const parts = [kind];
  if (c.left.kind === "field") {
    let tid = "";
    if (ctx.mode === "find") tid = ctx.tableId;
    else if (ctx.mode === "trigger") tid = S.trigger.table_id;
    else tid = c.left.source ? producedTable(c.left.source) : S.trigger.table_id;
    parts.push(picker(fieldOptions(tid || ""), c.left.field_id, "Field...", (id) => { c.left.field_id = id; markDirty(); renderAll(); }, !!c.left.field_id && !fieldInfo(c.left.field_id)));
    if (ctx.mode === "flow") parts.push(sourceSelect(c, ctx));
  } else {
    parts.push(textInput(c.left.text, (v) => { c.left.text = v; }, { placeholder: "e.g. Today, or Step 2 > Record count" }));
  }
  const op = el("select", {}, OPS.map(([v, l]) => el("option", { value: v, text: l })));
  op.value = c.operator || "equals";
  const val = textInput(c.value, (v) => { c.value = v; }, { placeholder: "Value" });
  val.hidden = NO_VALUE.includes(op.value);
  op.addEventListener("change", () => { c.operator = op.value; val.hidden = NO_VALUE.includes(op.value); markDirty(); });
  const rm = el("button", { type: "button", class: "tb", title: "Remove this condition", text: "×" });
  rm.addEventListener("click", () => { group.items.splice(idx, 1); markDirty(); renderAll(); });
  parts.push(op, val, rm);
  return el("div", { class: "row cond" }, parts);
}
function groupEditor(group, ctx, onRemove) {
  const junction = el("select", {}, el("option", { value: "AND", text: "ALL of these are true (AND)" }), el("option", { value: "OR", text: "ANY of these is true (OR)" }));
  junction.value = group.junction;
  junction.addEventListener("change", () => { group.junction = junction.value; markDirty(); });
  const addC = el("button", { type: "button", class: "tb", text: "+ Condition" });
  addC.addEventListener("click", () => { group.items.push({ type: "condition", left: { kind: "field", field_id: "", source: "" }, operator: "equals", value: "" }); markDirty(); renderAll(); });
  const addG = el("button", { type: "button", class: "tb", text: "+ Group" });
  addG.addEventListener("click", () => { group.items.push(newGroup()); markDirty(); renderAll(); });
  const head = [junction, addC, addG];
  if (onRemove) { const rg = el("button", { type: "button", class: "tb", text: "Remove group" }); rg.addEventListener("click", onRemove); head.push(rg); }
  const kids = group.items.map((it, i) => it.type === "group"
    ? groupEditor(it, ctx, () => { group.items.splice(i, 1); markDirty(); renderAll(); })
    : conditionRow(group, i, ctx));
  return el("div", { class: "cgroup" }, el("div", { class: "row" }, head), kids);
}
function conditionsBox(owner, title, ctx) {
  return el("div", { class: "logicbox display" }, el("div", { class: "logic-head", text: title }), groupEditor(owner.conditions, Object.assign({ owner }, ctx)));
}

/* ---------- trigger ---------- */
function triggerCard() {
  const t = S.trigger, box = [];
  const type = el("select", {}, Object.entries(TRIGGERS).map(([v, l]) => el("option", { value: v, text: l })));
  type.value = t.trigger_type;
  type.addEventListener("change", () => {
    t.trigger_type = type.value;
    if (t.trigger_type === "record_matches" && !t.conditions) t.conditions = newGroup();
    markDirty(); renderAll();
  });
  box.push(el("div", { class: "row" }, el("span", { class: "lbl-t", text: "Trigger" }), type));
  const tblSel = el("select", {}, el("option", { value: "", text: NEEDS_TABLE.includes(t.trigger_type) ? "(choose a table)" : "(no table)" }), tableOptions().map((o) => el("option", { value: o.id, text: o.label })));
  tblSel.value = t.table_id || "";
  tblSel.addEventListener("change", () => { t.table_id = tblSel.value; t.view_id = ""; t.watched_field_ids = []; markDirty(); renderAll(); });
  box.push(el("div", { class: "row" }, el("span", { class: "lbl-t", text: "Table" }), tblSel));
  if (t.trigger_type === "record_enters_view" || t.trigger_type === "form_submitted") {
    const views = [];
    for (const tb of baseTables()) if (!t.table_id || tb.table_id === t.table_id) for (const v of (tb.views || [])) if (!v.stale && (t.trigger_type === "record_enters_view" || v.view_type === "form")) views.push(v);
    const vs = el("select", {}, el("option", { value: "", text: "(no view chosen)" }), views.map((v) => el("option", { value: v.view_id, text: v.view_name + " [" + v.view_type + "] " + v.view_id })));
    vs.value = t.view_id || "";
    vs.addEventListener("change", () => { t.view_id = vs.value; markDirty(); });
    box.push(el("div", { class: "row" }, el("span", { class: "lbl-t", text: "View" }), vs));
  }
  if (t.trigger_type === "scheduled") box.push(el("div", { class: "row" }, labelled("Schedule (as shown in Airtable)", textInput(t.schedule_text, (v) => { t.schedule_text = v; }, { class: "wide" }))));
  if (t.trigger_type === "record_updated" || t.trigger_type === "record_matches" || t.trigger_type === "record_enters_view") {
    const chips = el("div", { class: "chips" }, t.watched_field_ids.map((fid, i) => {
      const b = el("button", { type: "button", class: "chip", title: "Remove", text: fieldName(fid) + " ×" });
      b.addEventListener("click", () => { t.watched_field_ids.splice(i, 1); markDirty(); renderAll(); });
      return b;
    }));
    box.push(el("div", { class: "row" }, el("span", { class: "lbl-t", text: "Watches fields" }), chips,
      picker(fieldOptions(t.table_id).filter((o) => !t.watched_field_ids.includes(o.id)), "", "Add a watched field...", (id) => { t.watched_field_ids.push(id); markDirty(); renderAll(); })));
  }
  box.push(richBox("Trigger description / notes", () => t.description_md, (v) => { t.description_md = v; }, 2));
  if (t.trigger_type === "record_matches") box.push(conditionsBox(t, "Runs when the record matches...", { mode: "trigger", tableId: t.table_id }));
  return el("div", { class: "fitem t-trigger" }, el("div", { class: "ibody" }, box));
}

/* ---------- steps ---------- */
function fieldRefsEditor(st) {
  const rows = st.field_refs.map((r, i) => {
    const role = el("select", {}, el("option", { value: "set", text: "Sets" }), el("option", { value: "read", text: "Reads" }), el("option", { value: "use", text: "Uses" }));
    role.value = r.role;
    role.addEventListener("change", () => { r.role = role.value; markDirty(); renderAll(); });
    const tid = r.role === "use" ? "" : st.table_id;
    const val = textInput(r.value, (v) => { r.value = v; }, { placeholder: r.role === "set" ? "Value it is set to" : "What it is used for", class: "wide" });
    const rm = el("button", { type: "button", class: "tb", title: "Remove", text: "×" });
    rm.addEventListener("click", () => { st.field_refs.splice(i, 1); markDirty(); renderAll(); });
    return el("div", { class: "row cond" }, role, picker(fieldOptions(tid || ""), r.field_id, "Field...", (id) => { r.field_id = id; markDirty(); renderAll(); }, !!r.field_id && !fieldInfo(r.field_id)), val, rm);
  });
  const add = el("button", { type: "button", class: "tb", text: "+ Add a field" });
  add.addEventListener("click", () => { st.field_refs.push({ field_id: "", role: st.action_type === "create_record" || st.action_type === "update_record" ? "set" : "use", value: "" }); markDirty(); renderAll(); });
  return el("div", { class: "logicbox" }, el("div", { class: "logic-head", text: "Fields this step sets, reads or uses" }), rows, add);
}
function settingsEditor(st) {
  const rows = st.settings.map((s, i) => {
    const name = textInput(s.name, (v) => { s.name = v; }, { placeholder: "Name" });
    const rm = el("button", { type: "button", class: "tb", title: "Remove this detail", text: "×" });
    rm.addEventListener("click", () => { st.settings.splice(i, 1); markDirty(); renderAll(); });
    let body;
    if (s.format === "plain") {
      const ta = el("textarea", { rows: 5, spellcheck: "false", class: "code", "aria-label": s.name });
      ta.value = s.value || "";
      ta.addEventListener("input", () => { s.value = ta.value; markDirty(); });
      body = el("div", { class: "rich" }, el("div", { class: "rich-label", text: "Kept exactly as typed (not read as Markdown)" }), ta);
    } else body = richBox(s.name || "Detail", () => s.value_md, (v) => { s.value_md = v; }, 2);
    return el("div", { class: "setting-row" }, el("div", { class: "row" }, name, rm), body);
  });
  const addR = el("button", { type: "button", class: "tb", text: "+ Add a detail" });
  addR.addEventListener("click", () => { st.settings.push({ name: "", format: "rich", value_md: "" }); markDirty(); renderAll(); });
  const addP = el("button", { type: "button", class: "tb", text: "+ Add code or raw text" });
  addP.addEventListener("click", () => { st.settings.push({ name: "", format: "plain", value: "" }); markDirty(); renderAll(); });
  return el("div", { class: "logicbox" }, el("div", { class: "logic-head", text: "Details (recipient, subject, message, script, request ...)" }), rows, el("div", { class: "row" }, addR, addP));
}
function tableSelect(st, label, required) {
  const sel = el("select", {}, el("option", { value: "", text: required ? "(choose a table)" : "(no table)" }), tableOptions().map((o) => el("option", { value: o.id, text: o.label })));
  sel.value = st.table_id || "";
  sel.addEventListener("change", () => { st.table_id = sel.value; markDirty(); renderAll(); });
  return el("div", { class: "row" }, el("span", { class: "lbl-t", text: label }), sel);
}
function stepBody(st, sumText) {
  const refresh = () => { sumText.textContent = describe(st); };
  const body = [labelled("Step title", textInput(st.title, (v) => { st.title = v; refresh(); }, { class: "wide" })),
    richBox("Description / notes", () => st.description_md, (v) => { st.description_md = v; }, 2)];
  if (st.type === "action") {
    const at = el("select", {}, Object.entries(ACTIONS).map(([v, l]) => el("option", { value: v, text: l })));
    at.value = st.action_type;
    at.addEventListener("change", () => {
      st.action_type = at.value;
      if (st.action_type === "update_record" && st.record_source === undefined) st.record_source = "";
      if (st.action_type === "find_records" && !st.conditions) st.conditions = newGroup();
      if (!st.settings.length) st.settings = (SETTING_DEFAULTS[st.action_type] || []).map(([name, plain]) => plain ? { name, format: "plain", value: "" } : { name, format: "rich", value_md: "" });
      markDirty(); renderAll();
    });
    body.splice(1, 0, el("div", { class: "row" }, el("span", { class: "lbl-t", text: "Action" }), at));
    body.push(tableSelect(st, "Table", RECORD_ACTIONS.includes(st.action_type)));
    if (st.action_type === "update_record") {
      const src = el("select", {}, el("option", { value: "", text: "the trigger record" }), earlierProducers(st).map((f) => el("option", { value: f.item.id, text: "the record from " + stepLabel(f) })));
      src.value = st.record_source || "";
      if (src.value !== (st.record_source || "")) src.value = "";
      src.addEventListener("change", () => { st.record_source = src.value; markDirty(); });
      body.push(el("div", { class: "row" }, el("span", { class: "lbl-t", text: "Updates" }), src));
    }
    if (st.action_type === "find_records") body.push(conditionsBox(st, "Find records where...", { mode: "find", tableId: st.table_id }));
    body.push(fieldRefsEditor(st), settingsEditor(st));
  } else if (st.type === "repeat") {
    const over = el("select", {}, el("option", { value: "", text: "(not a find step)" }), flat().filter((f) => f.kind === "step" && f.item.type === "action" && f.item.action_type === "find_records" && f.order < flat().find((x) => x.item === st).order)
      .map((f) => el("option", { value: f.item.id, text: "records found in " + stepLabel(f) })));
    over.value = st.over_source || "";
    if (over.value !== (st.over_source || "")) over.value = "";
    over.addEventListener("change", () => { st.over_source = over.value; markDirty(); renderAll(); });
    body.push(el("div", { class: "row" }, el("span", { class: "lbl-t", text: "Repeats over" }), over));
    body.push(el("div", { class: "row" }, labelled("...or describe what it repeats over", textInput(st.over_text, (v) => { st.over_text = v; }, { class: "wide" }))));
    body.push(tableSelect(st, "Table of the items", false));
  }
  return body;
}
function addMenu(arr) {
  const sel = el("select", { class: "addsel", "aria-label": "Add a step" }, el("option", { value: "", text: "+ Add a step..." }),
    el("optgroup", { label: "Actions" }, Object.entries(ACTIONS).map(([v, l]) => el("option", { value: "action:" + v, text: l }))),
    el("optgroup", { label: "Flow" }, el("option", { value: "conditional", text: "Conditional (if / else if / else)" }), el("option", { value: "repeat", text: "Repeating group (for each)" })));
  sel.addEventListener("change", () => { if (sel.value) addStep(arr, sel.value); });
  return sel;
}
function mkBtn(txt, title, fn, dis) { const b = el("button", { type: "button", class: "tb", title, text: txt, disabled: dis }); b.addEventListener("click", fn); return b; }
function stepControls(st, i, list) {
  const loc = locate(st.id), prev = loc.arr[loc.idx - 1];
  const canIn = !!prev && (prev.type === "repeat" || (prev.type === "conditional" && prev.branches.length));
  return el("div", { class: "itemctl" }, mkBtn("↑", "Move up", () => moveStep(st.id, -1), i === 0), mkBtn("↓", "Move down", () => moveStep(st.id, 1), i === list.length - 1),
    mkBtn("⇤ Out", "Move out of this block", () => outdent(st.id), !loc.owner), mkBtn("⇥ Into previous", "Move into the block above", () => indent(st.id), !canIn),
    mkBtn("Remove", "Remove this step", () => removeStep(st.id), false));
}
function branchLabel(cond, j) { const b = cond.branches[j]; return b.is_else ? "Else" : j === 0 ? "If" : "Else if"; }
function renderBranches(cond, path) {
  return cond.branches.map((b, j) => {
    const bp = path + "." + (j + 1);
    const ctl = el("div", { class: "itemctl" },
      mkBtn("↑", "Move branch up", () => { const a = cond.branches; [a[j - 1], a[j]] = [a[j], a[j - 1]]; markDirty(); renderAll(); }, j === 0 || b.is_else),
      mkBtn("↓", "Move branch down", () => { const a = cond.branches; [a[j + 1], a[j]] = [a[j], a[j + 1]]; markDirty(); renderAll(); }, j === cond.branches.length - 1 || cond.branches[j + 1].is_else),
      mkBtn("Remove branch", "Remove this branch and its steps", () => {
        if (b.steps.length && !window.confirm("This removes " + b.steps.length + " step(s) in the branch as well. Continue?")) return;
        cond.branches.splice(j, 1); markDirty(); renderAll();
      }, cond.branches.length === 1));
    const parts = [el("div", { class: "branch-head" }, el("span", { class: "pnum", text: bp }), el("span", { class: "kind", text: branchLabel(cond, j) }),
      labelled("Label (optional)", textInput(b.label, (v) => { b.label = v; }, { placeholder: "e.g. VIP customers" })), ctl)];
    if (!b.is_else) parts.push(conditionsBox(b, "Run this branch when...", { mode: "flow" }));
    else parts.push(el("p", { class: "hint", text: "Runs when no earlier branch matched." }));
    parts.push(el("div", { class: "kids" }, renderSteps(b.steps, bp)));
    parts.push(el("div", { class: "row addrow" }, el("span", { class: "hint", text: "Add to branch " + bp + ":" }), addMenu(b.steps)));
    return el("div", { class: "branchbox" + (b.is_else ? " else" : "") }, parts);
  });
}
function renderSteps(list, prefix) {
  return list.map((st, i) => {
    const path = (prefix ? prefix + "." : "") + (i + 1);
    const sumText = el("span", { class: "sumtext", text: describe(st) });
    const det = el("details", { class: "fitem t-" + st.type, open: !S.collapsed.has(st.id) },
      el("summary", {}, el("span", { class: "pnum", text: path }), el("span", { class: "kind", text: kindLabel(st) }), sumText));
    det.addEventListener("toggle", () => { if (det.open) S.collapsed.delete(st.id); else S.collapsed.add(st.id); });
    const parts = [stepControls(st, i, list)].concat(stepBody(st, sumText));
    if (st.type === "repeat") {
      parts.push(el("div", { class: "kids" }, renderSteps(st.steps, path)));
      parts.push(el("div", { class: "row addrow" }, el("span", { class: "hint", text: "Add inside step " + path + ":" }), addMenu(st.steps)));
    } else if (st.type === "conditional") {
      parts.push(el("div", { class: "kids" }, renderBranches(st, path)));
      const hasElse = st.branches.some((b) => b.is_else);
      const addIf = mkBtn("+ Else if branch", "Add another branch", () => {
        const nb = { id: newId(), type: "branch", label: "", is_else: false, conditions: newGroup(), steps: [] };
        st.branches.splice(hasElse ? st.branches.length - 1 : st.branches.length, 0, nb); markDirty(); renderAll();
      }, false);
      const addElse = mkBtn("+ Else branch", "Add the branch that runs when nothing else matched", () => {
        st.branches.push({ id: newId(), type: "branch", label: "", is_else: true, steps: [] }); markDirty(); renderAll();
      }, hasElse);
      parts.push(el("div", { class: "row addrow" }, addIf, addElse));
    }
    det.appendChild(el("div", { class: "ibody" }, parts));
    return det;
  });
}
function renderAll() {
  const tb = $("triggerBox");
  clear(tb);
  tb.appendChild(triggerCard());
  const tree = $("tree");
  clear(tree);
  if (!S.steps.length) tree.appendChild(el("p", { class: "hint", text: "No steps yet. Add the first step below." }));
  tree.append(...renderSteps(S.steps, ""));
  const root = $("rootAdd");
  clear(root);
  root.append(el("span", { class: "hint", text: "Add to the automation:" }), addMenu(S.steps));
}

/* ---------- open / new / save ---------- */
const confirmDiscard = () => !S.dirty || window.confirm("You have unsaved changes to this automation. Discard them?");
function showEditor() {
  $("editorCard").hidden = false;
  $("reportCard").hidden = false;
  $("autoName").value = S.auto.automation_name;
  $("autoKey").value = S.auto.automation_id;
  $("autoKey").readOnly = S.keyLocked;
  $("scenarioName").value = S.auto.target_scenario_name || "";
  $("enabledSrc").checked = S.auto.enabled_in_source !== false;
  $("autoScope").textContent = "Base " + S.auto.base_id;
  const d = $("autoDescBox");
  clear(d);
  d.appendChild(richBox("Automation description / purpose", () => S.auto.description_md, (v) => { S.auto.description_md = v; }, 3));
  $("versionNote").value = "";
  setMsg("saveMessage", "", "");
  $("saveProblems").hidden = true;
  S.dirty = false;
  $("btnSave").textContent = "Save as a new version" + (S.savedVersion ? " (after v" + S.savedVersion + ")" : "");
  renderAll();
  syncReportControls();
}
function startNew() {
  const name = $("newName").value.trim();
  if (!name) { setMsg("pickMessage", "Enter the automation's name first.", "err"); return; }
  if (!currentBase()) { setMsg("pickMessage", "Choose the base above first.", "err"); return; }
  const key = "auto-" + name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80);
  if (S.autos.some((a) => a.automation_id === key)) { setMsg("pickMessage", "An automation with the key " + key + " already exists. Open it from the list, or use another name.", "err"); return; }
  if (!confirmDiscard()) return;
  S.auto = { automation_id: key, automation_name: name, base_id: currentBase(), target_scenario_name: "", description_md: "", enabled_in_source: true };
  S.trigger = { id: "trigger", type: "trigger", trigger_type: "record_created", table_id: "", view_id: "", watched_field_ids: [], schedule_text: "", description_md: "" };
  S.steps = []; S.seq = 0; S.savedVersion = null; S.versions = []; S.keyLocked = false; S.collapsed = new Set();
  showEditor();
  markDirty();
  setMsg("pickMessage", "Started a new automation with the key " + key + ". You can change the key before the first save.", "ok");
}
async function openSaved() {
  const id = $("savedAutos").value;
  if (!id || !confirmDiscard()) return;
  try {
    const r = await api("GET", "/api/automations/spec?automation_id=" + encodeURIComponent(id) + "&version=" + encodeURIComponent($("openVersion").value || ""));
    loadSpec(r);
    setMsg("pickMessage", "Opened " + r.spec.automation.automation_name + ", version " + r.version_no + (r.version_no !== r.versions[0].version_no ? " (an older version: saving creates version " + (r.versions[0].version_no + 1) + ")" : "") + ".", "ok");
  } catch (e) { setMsg("pickMessage", e.message, "err"); }
}
function loadSpec(r) {
  const a = stripBlocks(r.spec.automation);
  S.auto = { automation_id: a.automation_id, automation_name: a.automation_name, base_id: a.base_id, target_scenario_name: a.target_scenario_name || "", description_md: a.description_md || "", enabled_in_source: a.enabled_in_source !== false };
  S.trigger = Object.assign({ view_id: "", watched_field_ids: [], schedule_text: "", description_md: "" }, stripBlocks(r.spec.trigger));
  S.steps = stripBlocks(r.spec.steps);
  S.seq = 0;
  for (const f of flat()) { const m = /^n(\d+)$/.exec(f.item.id); if (m) S.seq = Math.max(S.seq, Number(m[1])); }
  S.savedVersion = r.version_no; S.versions = r.versions; S.keyLocked = true; S.collapsed = new Set();
  showEditor();
  fillVersionSelect($("repVersion"), r.versions.map((v) => v.version_no), r.version_no);
  syncReportControls();
}
async function save() {
  if (!S.auto) return;
  S.auto.automation_name = $("autoName").value.trim();
  S.auto.automation_id = $("autoKey").value.trim();
  S.auto.target_scenario_name = $("scenarioName").value.trim();
  S.auto.enabled_in_source = $("enabledSrc").checked;
  $("btnSave").disabled = true;
  setMsg("saveMessage", "Saving...", "");
  $("saveProblems").hidden = true;
  try {
    const r = await api("POST", "/api/automations/save", { automation: S.auto, trigger: S.trigger, steps: S.steps, note: $("versionNote").value });
    setMsg("saveMessage", r.unchanged ? "Nothing changed since version " + r.version_no + ", so no new version was created." : "Saved as version " + r.version_no + " (" + r.items + " items).", r.unchanged ? "warn" : "ok");
    S.dirty = false;
    const spec = await api("GET", "/api/automations/spec?automation_id=" + encodeURIComponent(r.automation_id));
    S.savedVersion = spec.version_no; S.versions = spec.versions; S.keyLocked = true;
    $("autoKey").readOnly = true;
    $("versionNote").value = "";
    $("btnSave").textContent = "Save as a new version (after v" + S.savedVersion + ")";
    fillVersionSelect($("repVersion"), spec.versions.map((v) => v.version_no), spec.version_no);
    syncReportControls();
    await Promise.all([loadSaved(), runUsage()]);
  } catch (e) {
    setMsg("saveMessage", "Not saved.", "err");
    const box = $("saveProblems");
    clear(box);
    box.appendChild(el("ul", {}, (e.problems && e.problems.length ? e.problems : [e.message]).map((p) => el("li", { text: p }))));
    box.hidden = false;
  } finally { $("btnSave").disabled = !state.ready; }
}

/* ---------- lists ---------- */
function fillVersionSelect(sel, versions, selected) {
  clear(sel);
  versions.forEach((v, i) => sel.appendChild(el("option", { value: v, text: "Version " + v + (i === 0 ? " (latest)" : "") })));
  if (selected) sel.value = String(selected);
}
async function loadSaved() {
  const r = await api("GET", "/api/automations/list");
  S.autos = r.automations;
  const base = currentBase();
  const list = r.automations.filter((a) => !base || a.base_id === base);
  const sel = $("savedAutos"), keep = sel.value;
  clear(sel);
  if (!list.length) sel.appendChild(el("option", { value: "", text: base ? "(none recorded for this base yet)" : "(none recorded yet)" }));
  list.forEach((a) => sel.appendChild(el("option", { value: a.automation_id, text: a.automation_name + " [" + a.automation_id + "] - " + a.versions + " version" + (a.versions > 1 ? "s" : "") })));
  if (list.some((a) => a.automation_id === keep)) sel.value = keep;
  syncOpenVersions();
}
function syncOpenVersions() {
  const a = S.autos.find((x) => x.automation_id === $("savedAutos").value);
  const vs = [];
  if (a) for (let v = a.latest_version; v >= 1; v--) vs.push(v);
  fillVersionSelect($("openVersion"), vs, vs[0]);
  $("btnOpen").disabled = !state.ready || !a;
}

/* ---------- reports ---------- */
function reportQuery() {
  return "automation_id=" + encodeURIComponent(S.auto.automation_id) + "&version=" + encodeURIComponent($("repVersion").value) + "&target=" + encodeURIComponent($("repTarget").value);
}
function syncReportControls() {
  const has = !!(S.auto && S.savedVersion);
  $("btnRepPreview").disabled = !has;
  for (const id of ["lnkRepHtml", "lnkRepMhtml", "lnkLinksCsv"]) $(id).classList.toggle("disabled", !has);
  if (!has) { $("repFrame").hidden = true; setMsg("repStatus", S.auto ? "Save the automation once to produce documentation." : "", ""); return; }
  const q = reportQuery();
  $("lnkRepHtml").href = "/api/automations/report.html?" + q + "&download=1";
  $("lnkRepMhtml").href = "/api/automations/report.mhtml?" + q;
  $("lnkLinksCsv").href = "/api/export/automation_links.csv?automation_id=" + encodeURIComponent(S.auto.automation_id) + "&all_versions=1";
}
async function previewRep() {
  if (!S.auto || !S.savedVersion) return;
  $("repFrame").src = "/api/automations/report.html?" + reportQuery();
  $("repFrame").hidden = false;
  try {
    const r = await api("GET", "/api/automations/report/status?" + reportQuery());
    const t = $("repTarget").value;
    setMsg("repStatus", "Fields without a target mapping: DEV " + r.unmapped.dev + ", PROD " + r.unmapped.prod + "." + (t ? "" : " (Choose a Workfront Planning option above to see them in the report.)"),
      (t === "DEV" ? r.unmapped.dev : t === "PROD" ? r.unmapped.prod : 0) ? "warn" : "ok");
  } catch (e) { setMsg("repStatus", e.message, "err"); }
}

/* ---------- field usage ---------- */
function usageQuery() {
  const p = [];
  if ($("useQ").value.trim()) p.push("q=" + encodeURIComponent($("useQ").value.trim()));
  if ($("useRole").value) p.push("role=" + encodeURIComponent($("useRole").value));
  if ($("useAll").checked) p.push("all_versions=1");
  if (currentBase()) p.push("base_id=" + encodeURIComponent(currentBase()));
  return p.join("&");
}
async function runUsage() {
  const q = usageQuery();
  $("lnkUsageCsv").href = "/api/export/automation_usage.csv?" + q;
  $("lnkAllLinksCsv").href = "/api/export/automation_links.csv?all_versions=" + ($("useAll").checked ? "1" : "0");
  const r = await api("GET", "/api/automations/usage?" + q);
  const tb = $("usageTable").querySelector("tbody");
  clear(tb);
  for (const x of r.rows) {
    tb.appendChild(el("tr", {}, el("td", { text: x.field_name + " [" + x.field_id + "]" }), el("td", { text: x.table_name }), el("td", { text: x.automation_name }),
      el("td", { class: "num", text: x.version_no }), el("td", { text: r.roles[x.role] || x.role }), el("td", { text: x.item_path + (x.item_title ? " " + x.item_title : "") }),
      el("td", { class: "settings", text: x.detail }), el("td", { text: x.workfront_dev_field_id }), el("td", { text: x.workfront_prod_field_id })));
  }
  const fields = new Set(r.rows.map((x) => x.field_id)), autos = new Set(r.rows.map((x) => x.automation_id));
  $("usageScroll").hidden = !r.rows.length;
  setMsg("usageSummary", r.total ? r.total + " usage row(s): " + fields.size + " field(s) across " + autos.size + " automation(s)." + (r.truncated ? " Showing the first 2,000; the .csv has all of them." : "") : "No recorded automation matches. Record and save one above.", r.total ? "" : "warn");
}

/* ---------- page wiring ---------- */
function onReadyChange() {
  const on = state.ready;
  for (const id of ["btnSave", "btnUsage", "btnNew"]) $(id).disabled = !on;
  if (!on) $("btnOpen").disabled = true;
}
async function afterConnect() {
  await loadKnownBases();
  await loadSaved();
  await runUsage();
}
const guarded = (fn) => async () => { try { await fn(); } catch (e) { showGlobalError(e.message); } };

startPage("stage3", { onReadyChange, afterConnect }, () => {
  initBaseCombo();
  $("baseId").addEventListener("input", guarded(loadSaved));
  $("savedAutos").addEventListener("change", syncOpenVersions);
  $("btnOpen").addEventListener("click", openSaved);
  $("btnNew").addEventListener("click", startNew);
  $("btnSave").addEventListener("click", save);
  $("autoName").addEventListener("input", () => { if (S.auto) { S.auto.automation_name = $("autoName").value; markDirty(); } });
  $("autoKey").addEventListener("input", () => { if (S.auto && !S.keyLocked) { S.auto.automation_id = $("autoKey").value; markDirty(); } });
  $("scenarioName").addEventListener("input", () => { if (S.auto) { S.auto.target_scenario_name = $("scenarioName").value; markDirty(); } });
  $("enabledSrc").addEventListener("change", () => { if (S.auto) { S.auto.enabled_in_source = $("enabledSrc").checked; markDirty(); } });
  $("repVersion").addEventListener("change", syncReportControls);
  $("repTarget").addEventListener("change", syncReportControls);
  $("btnRepPreview").addEventListener("click", previewRep);
  $("btnUsage").addEventListener("click", guarded(runUsage));
  $("useQ").addEventListener("keydown", (ev) => { if (ev.key === "Enter") guarded(runUsage)(); });
  window.addEventListener("beforeunload", (ev) => { if (S.dirty) { ev.preventDefault(); ev.returnValue = ""; } });
});
