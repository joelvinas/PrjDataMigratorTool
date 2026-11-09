"use strict";
/* Stage 2: Forms. A manual form recorder: sections, fields, hidden/preset fields, headings, help text and text blocks, each
   with optional display logic (sections also skip logic). Text is Markdown; the server parses it (one parser for the preview,
   the stored spec, the link list and the reports). Everything is inserted with textContent, never innerHTML. */

const KINDS = {
  section: "Section", field: "Field", hidden_field: "Hidden / preset field",
  heading: "Heading", help_text: "Help text", text_block: "Text block",
};
const OPS = [["equals", "is exactly"], ["notEquals", "is not"], ["contains", "contains"], ["doesNotContain", "does not contain"],
  ["isEmpty", "is empty"], ["isNotEmpty", "is not empty"]];
const NO_VALUE = ["isEmpty", "isNotEmpty"];

const S = { form: null, items: [], seq: 0, dirty: false, savedVersion: null, versions: [], collapsed: new Set(), keyLocked: false, forms: [] };

const currentBase = () => $("baseId").value.trim();
const newLogic = () => ({ enabled: false, junction: "AND", conditions: [], skip_to: "" });
const newId = () => "i" + (++S.seq);
function markDirty() { S.dirty = true; $("btnSave").textContent = "Save as a new version" + (S.savedVersion ? " (after v" + S.savedVersion + ")" : ""); }

/* ---------- schema lookups (from Stage 1's ingested schema) ---------- */
function baseTables(baseId) {
  const b = (state.schema || []).find((x) => x.base_id === baseId);
  return b ? b.tables.filter((t) => !t.stale) : [];
}
function formFields() {
  if (!S.form) return [];
  const out = [];
  for (const t of baseTables(S.form.base_id)) {
    if (S.form.table_id && t.table_id !== S.form.table_id) continue;
    for (const f of t.fields) if (!f.stale) out.push({ field_id: f.field_id, field_name: f.field_name, data_type: f.data_type, table_name: t.table_name });
  }
  return out;
}
function fieldInfo(fid) {
  for (const t of baseTables(S.form ? S.form.base_id : currentBase())) {
    for (const f of t.fields) if (f.field_id === fid) return { field_id: fid, field_name: f.field_name, data_type: f.data_type, table_name: t.table_name };
  }
  return null;
}
const fieldName = (fid) => { const f = fieldInfo(fid); return f ? f.field_name : (fid || ""); };

/* ---------- tree helpers ---------- */
function flatItems() {
  const out = [];
  (function walk(list, prefix, parent) {
    list.forEach((it, i) => {
      const path = (prefix ? prefix + "." : "") + (i + 1);
      out.push({ item: it, path, parent, order: out.length });
      if (it.items) walk(it.items, path, it);
    });
  })(S.items, "", null);
  return out;
}
function locate(id, arr, parent) {
  arr = arr || S.items;
  for (let i = 0; i < arr.length; i++) {
    if (arr[i].id === id) return { arr, idx: i, parent: parent || null };
    if (arr[i].items) { const r = locate(id, arr[i].items, arr[i]); if (r) return r; }
  }
  return null;
}
function subtreeEnd(flat, item) {
  let end = flat.find((f) => f.item === item).order;
  (function walk(list) { for (const c of list) { end = Math.max(end, flat.find((f) => f.item === c).order); if (c.items) walk(c.items); } })(item.items || []);
  return end;
}
function makeItem(type) {
  const id = newId();
  switch (type) {
    case "section": return { id, type, title: "", description_md: "", display_logic: newLogic(), skip_logic: newLogic(), items: [] };
    case "field": return { id, type, field_id: "", label: "", required: false, help_md: "", display_logic: newLogic() };
    case "hidden_field": return { id, type, field_id: "", preset_value: "", preset_source: "", note: "" };
    case "heading": return { id, type, text: "", display_logic: newLogic() };
    default: return { id, type, content_md: "", display_logic: newLogic() };
  }
}
function cleanLoaded(it) {
  const o = {};
  for (const [k, v] of Object.entries(it)) {
    if (k.endsWith("_blocks")) continue;
    o[k] = k === "items" ? v.map(cleanLoaded) : v;
  }
  if (o.display_logic === undefined && o.type !== "hidden_field") o.display_logic = newLogic();
  for (const k of ["display_logic", "skip_logic"]) if (o[k]) o[k] = Object.assign(newLogic(), o[k]);
  return o;
}
function describe(it) {
  switch (it.type) {
    case "section": return it.title || "(untitled section)";
    case "field": return it.label || (it.field_id ? fieldName(it.field_id) : "(choose a field)");
    case "hidden_field": return it.field_id ? fieldName(it.field_id) + (it.preset_value ? " = " + it.preset_value : "") : "(choose a field)";
    case "heading": return it.text || "(empty heading)";
    default: { const t = (it.content_md || "").replace(/\s+/g, " ").trim(); return t ? (t.length > 70 ? t.slice(0, 67) + "..." : t) : "(empty)"; }
  }
}
function moveItem(id, d) {
  const loc = locate(id), j = loc.idx + d;
  if (j < 0 || j >= loc.arr.length) return;
  [loc.arr[loc.idx], loc.arr[j]] = [loc.arr[j], loc.arr[loc.idx]];
  markDirty(); renderTree();
}
function outdent(id) {
  const loc = locate(id);
  if (!loc.parent) return;
  const gp = locate(loc.parent.id), item = loc.arr.splice(loc.idx, 1)[0];
  gp.arr.splice(gp.idx + 1, 0, item);
  markDirty(); renderTree();
}
function indent(id) {
  const loc = locate(id), prev = loc.arr[loc.idx - 1];
  if (!prev || prev.type !== "section") return;
  prev.items.push(loc.arr.splice(loc.idx, 1)[0]);
  S.collapsed.delete(prev.id);
  markDirty(); renderTree();
}
function removeItem(id) {
  const loc = locate(id), it = loc.arr[loc.idx];
  const n = it.items ? it.items.length : 0;
  if (n && !window.confirm("This section contains " + n + " item(s) that will be removed with it. Continue?")) return;
  loc.arr.splice(loc.idx, 1);
  markDirty(); renderTree();
}
function addItem(arr, type) {
  const it = makeItem(type);
  arr.push(it);
  markDirty(); renderTree();
}

/* ---------- logic editor ---------- */
function earlierFields(item) {
  const flat = flatItems(), me = flat.find((f) => f.item === item);
  const out = [];
  for (const f of flat) {
    if (f.order >= me.order) break;
    if (f.item.type === "field" || f.item.type === "hidden_field") {
      if (!f.item.field_id) continue;
      const info = fieldInfo(f.item.field_id);
      out.push({ id: f.item.field_id, label: (info ? info.field_name : f.item.field_id) + (f.item.type === "hidden_field" ? " (hidden)" : ""),
        sub: (f.path + " · " + (info ? info.data_type : "")) });
    }
  }
  return out;
}
function laterSections(item) {
  const flat = flatItems(), end = subtreeEnd(flat, item);
  return flat.filter((f) => f.item.type === "section" && f.order > end).map((f) => ({ id: f.item.id, label: "Section " + f.path + ": " + (f.item.title || "(untitled)") }));
}
function logicBox(item, key, kind) {
  const lg = item[key];
  const box = el("div", { class: "logicbox " + kind });
  const title = kind === "display" ? "Display logic (show this only when...)" : "Skip logic (skip this section when...)";
  const on = el("input", { type: "checkbox" });
  on.checked = !!lg.enabled;
  on.addEventListener("change", () => {
    lg.enabled = on.checked;
    if (lg.enabled && !lg.conditions.length) lg.conditions.push({ field_id: "", operator: "equals", value: "" });
    markDirty(); renderTree();
  });
  box.appendChild(el("label", { class: "logic-head" }, on, " " + title));
  if (!lg.enabled) return box;
  const earlier = earlierFields(item);
  const junction = el("select", {}, el("option", { value: "AND", text: "ALL of these are true (AND)" }), el("option", { value: "OR", text: "ANY of these is true (OR)" }));
  junction.value = lg.junction;
  junction.addEventListener("change", () => { lg.junction = junction.value; markDirty(); });
  box.appendChild(el("div", { class: "row" }, junction));
  if (!earlier.length) box.appendChild(el("div", { class: "msg warn", text: "No field is placed before this item yet, so there is nothing to base a rule on. Place the controlling field (visible or hidden) above it first." }));
  lg.conditions.forEach((c, ci) => {
    const known = earlier.some((o) => o.id === c.field_id);
    const op = el("select", {}, OPS.map(([v, l]) => el("option", { value: v, text: l })));
    op.value = c.operator || "equals";
    const val = textInput(c.value, (v) => { c.value = v; }, { placeholder: "Value" });
    val.hidden = NO_VALUE.includes(op.value);
    op.addEventListener("change", () => { c.operator = op.value; val.hidden = NO_VALUE.includes(op.value); markDirty(); });
    const rm = el("button", { type: "button", class: "tb", title: "Remove this condition", text: "×" });
    rm.addEventListener("click", () => { lg.conditions.splice(ci, 1); if (!lg.conditions.length) lg.enabled = false; markDirty(); renderTree(); });
    box.appendChild(el("div", { class: "row cond" },
      picker(earlier, c.field_id, "Earlier field...", (id) => { c.field_id = id; markDirty(); renderTree(); }, !!c.field_id && !known),
      op, val, rm));
  });
  const add = el("button", { type: "button", class: "tb", text: "+ Add condition" });
  add.addEventListener("click", () => { lg.conditions.push({ field_id: "", operator: "equals", value: "" }); markDirty(); renderTree(); });
  box.appendChild(add);
  if (kind === "skip") {
    const dest = el("select", {}, el("option", { value: "", text: "then skip only this section" }),
      laterSections(item).map((o) => el("option", { value: o.id, text: "then skip to " + o.label })), el("option", { value: "END", text: "then skip to the end of the form" }));
    dest.value = lg.skip_to || "";
    if (dest.value !== (lg.skip_to || "")) dest.value = "";
    dest.addEventListener("change", () => { lg.skip_to = dest.value; markDirty(); });
    box.appendChild(el("div", { class: "row" }, dest));
  }
  return box;
}

/* ---------- item editors ---------- */
function usedFieldIds(except) {
  const s = new Set();
  for (const f of flatItems()) if (f.item !== except && f.item.field_id) s.add(f.item.field_id);
  return s;
}
function fieldOptions(item) {
  const used = usedFieldIds(item);
  return formFields().filter((f) => !used.has(f.field_id)).map((f) => ({ id: f.field_id, label: f.field_name, sub: f.table_name + " · " + f.data_type + " · " + f.field_id }));
}
function itemBody(it, sumText) {
  const body = [];
  const refresh = () => { sumText.textContent = describe(it); };
  switch (it.type) {
    case "section":
      body.push(labelled("Section title", textInput(it.title, (v) => { it.title = v; refresh(); }, { class: "wide" })));
      body.push(richBox("Section description", () => it.description_md, (v) => { it.description_md = v; }, 2));
      break;
    case "field":
      body.push(el("div", { class: "row" }, el("span", { class: "lbl-t", text: "Airtable field" }),
        picker(fieldOptions(it), it.field_id, "Search fields...", (id) => { it.field_id = id; markDirty(); renderTree(); })));
      body.push(el("div", { class: "row" }, labelled("Label shown on the form (if different from the field name)", textInput(it.label, (v) => { it.label = v; refresh(); }, { class: "wide" }))));
      { const req = el("input", { type: "checkbox" }); req.checked = !!it.required;
        req.addEventListener("change", () => { it.required = req.checked; markDirty(); });
        body.push(el("label", { class: "row" }, req, " Required")); }
      body.push(richBox("Help text under the field", () => it.help_md, (v) => { it.help_md = v; }, 2));
      break;
    case "hidden_field":
      body.push(el("p", { class: "hint", text: "A field the respondent does not see, set by the form link or a default. It is recorded so logic that depends on it makes sense." }));
      body.push(el("div", { class: "row" }, el("span", { class: "lbl-t", text: "Airtable field" }),
        picker(fieldOptions(it), it.field_id, "Search fields...", (id) => { it.field_id = id; markDirty(); renderTree(); })));
      body.push(el("div", { class: "row" },
        labelled("Preset value", textInput(it.preset_value, (v) => { it.preset_value = v; refresh(); })),
        labelled("Where the value comes from", textInput(it.preset_source, (v) => { it.preset_source = v; }, { list: "presetSources", placeholder: "e.g. URL parameter prefill_Source" }))));
      body.push(labelled("Note", textInput(it.note, (v) => { it.note = v; }, { class: "wide" })));
      break;
    case "heading":
      body.push(labelled("Heading text", textInput(it.text, (v) => { it.text = v; refresh(); }, { class: "wide" })));
      break;
    default:
      body.push(richBox(it.type === "help_text" ? "Help text" : "Text block", () => it.content_md, (v) => { it.content_md = v; refresh(); }, 4));
  }
  if (it.type !== "hidden_field") body.push(logicBox(it, "display_logic", "display"));
  if (it.type === "section") body.push(logicBox(it, "skip_logic", "skip"));
  return body;
}
function addButtons(arr, withSection) {
  const types = ["field", "hidden_field", "heading", "help_text", "text_block"].concat(withSection ? ["section"] : []);
  return types.map((t) => {
    const b = el("button", { type: "button", class: "tb add", text: "+ " + (t === "section" ? (arr === S.items ? "Section" : "Subsection") : KINDS[t]) });
    b.addEventListener("click", () => addItem(arr, t));
    return b;
  });
}
function renderItems(list, prefix, depth) {
  return list.map((it, i) => {
    const path = (prefix ? prefix + "." : "") + (i + 1);
    const sumText = el("span", { class: "sumtext", text: describe(it) });
    const det = el("details", { class: "fitem t-" + it.type, open: !S.collapsed.has(it.id) },
      el("summary", {}, el("span", { class: "pnum", text: path }), el("span", { class: "kind", text: it.type === "section" && depth ? "Subsection" : KINDS[it.type] }), sumText));
    det.addEventListener("toggle", () => { if (det.open) S.collapsed.delete(it.id); else S.collapsed.add(it.id); });
    const mk = (txt, title, fn, dis) => { const b = el("button", { type: "button", class: "tb", title, text: txt, disabled: dis }); b.addEventListener("click", fn); return b; };
    const loc = locate(it.id);
    const prev = loc.arr[loc.idx - 1];
    const ctl = el("div", { class: "itemctl" },
      mk("↑", "Move up", () => moveItem(it.id, -1), i === 0), mk("↓", "Move down", () => moveItem(it.id, 1), i === list.length - 1),
      mk("⇤ Out", "Move out of this section", () => outdent(it.id), !loc.parent),
      mk("⇥ Into previous", "Move into the section above", () => indent(it.id), !(prev && prev.type === "section")),
      mk("Remove", "Remove this item", () => removeItem(it.id), false));
    const parts = [ctl].concat(itemBody(it, sumText));
    if (it.type === "section") {
      parts.push(el("div", { class: "kids" }, renderItems(it.items, path, depth + 1)));
      parts.push(el("div", { class: "row addrow" }, el("span", { class: "hint", text: "Add to section " + path + ":" }), addButtons(it.items, true)));
    }
    det.appendChild(el("div", { class: "ibody" }, parts));
    return det;
  });
}
function renderTree() {
  const tree = $("tree");
  clear(tree);
  if (!S.items.length) tree.appendChild(el("p", { class: "hint", text: "The form is empty. Add a section or any other item below." }));
  tree.append(...renderItems(S.items, "", 0));
  const root = $("rootAdd");
  clear(root);
  root.append(el("span", { class: "hint", text: "Add to the form:" }), ...addButtons(S.items, true));
}

/* ---------- open / new / save ---------- */
function confirmDiscard() { return !S.dirty || window.confirm("You have unsaved changes to this form. Discard them?"); }
function showEditor() {
  $("editorCard").hidden = false;
  $("reportCard").hidden = false;
  $("formName").value = S.form.form_name;
  $("formKey").value = S.form.form_id;
  $("formKey").readOnly = S.keyLocked;
  const tname = S.form.table_id ? (baseTables(S.form.base_id).find((t) => t.table_id === S.form.table_id) || {}).table_name : "";
  $("formScope").textContent = "Base " + S.form.base_id + (S.form.table_id ? " · table " + (tname || "") + " (" + S.form.table_id + ")" : " · any table") +
    (S.form.view_id ? " · Airtable view " + S.form.view_id : "");
  const d = $("formDescBox");
  clear(d);
  d.appendChild(richBox("Form description (shown at the top of the form)", () => S.form.description_md, (v) => { S.form.description_md = v; }, 3));
  $("versionNote").value = "";
  setMsg("saveMessage", "", "");
  $("saveProblems").hidden = true;
  S.dirty = false;
  $("btnSave").textContent = "Save as a new version" + (S.savedVersion ? " (after v" + S.savedVersion + ")" : "");
  renderTree();
  syncReportControls();
}
function startNew(form) {
  if (!confirmDiscard()) return;
  S.form = Object.assign({ description_md: "" }, form);
  S.items = []; S.seq = 0; S.savedVersion = null; S.versions = []; S.keyLocked = false; S.collapsed = new Set();
  showEditor();
  markDirty();
}
async function openSaved() {
  const fid = $("savedForms").value;
  if (!fid || !confirmDiscard()) return;
  try {
    const r = await api("GET", "/api/forms/spec?form_id=" + encodeURIComponent(fid) + "&version=" + encodeURIComponent($("openVersion").value || ""));
    loadSpec(r);
    setMsg("pickMessage", "Opened " + r.spec.form.form_name + ", version " + r.version_no + (r.version_no !== r.versions[0].version_no ? " (an older version: saving creates version " + (r.versions[0].version_no + 1) + ")" : "") + ".", "ok");
  } catch (e) { setMsg("pickMessage", e.message, "err"); }
}
function loadSpec(r) {
  const f = r.spec.form;
  S.form = { form_id: f.form_id, form_name: f.form_name, base_id: f.base_id, table_id: f.table_id, view_id: f.view_id, description_md: f.description_md || "" };
  S.items = r.spec.items.map(cleanLoaded);
  S.seq = 0;
  for (const it of flatItems()) { const m = /^i(\d+)$/.exec(it.item.id); if (m) S.seq = Math.max(S.seq, Number(m[1])); }
  S.savedVersion = r.version_no; S.versions = r.versions; S.keyLocked = true; S.collapsed = new Set();
  showEditor();
  fillVersionSelect($("repVersion"), r.versions.map((v) => v.version_no), r.version_no);
  syncReportControls();
}
async function save() {
  if (!S.form) return;
  S.form.form_name = $("formName").value.trim();
  S.form.form_id = $("formKey").value.trim();
  $("btnSave").disabled = true;
  setMsg("saveMessage", "Saving...", "");
  $("saveProblems").hidden = true;
  try {
    const r = await api("POST", "/api/forms/save", { form: S.form, items: S.items, note: $("versionNote").value });
    if (r.unchanged) {
      setMsg("saveMessage", "Nothing changed since version " + r.version_no + ", so no new version was created.", "warn");
    } else {
      setMsg("saveMessage", "Saved as version " + r.version_no + " (" + r.items + " items).", "ok");
    }
    S.dirty = false;
    const spec = await api("GET", "/api/forms/spec?form_id=" + encodeURIComponent(r.form_id));
    S.savedVersion = spec.version_no; S.versions = spec.versions; S.keyLocked = true;
    $("formKey").readOnly = true;
    $("versionNote").value = "";
    $("btnSave").textContent = "Save as a new version (after v" + S.savedVersion + ")";
    fillVersionSelect($("repVersion"), spec.versions.map((v) => v.version_no), spec.version_no);
    syncReportControls();
    await Promise.all([loadSavedForms(), runUsage()]);
  } catch (e) {
    setMsg("saveMessage", "Not saved.", "err");
    const box = $("saveProblems");
    clear(box);
    box.appendChild(el("ul", {}, (e.problems && e.problems.length ? e.problems : [e.message]).map((p) => el("li", { text: p }))));
    box.hidden = false;
  } finally { $("btnSave").disabled = !state.ready; }
}

/* ---------- pickers on the choose-a-form card ---------- */
function fillVersionSelect(sel, versions, selected) {
  clear(sel);
  versions.forEach((v, i) => sel.appendChild(el("option", { value: v, text: "Version " + v + (i === 0 ? " (latest)" : "") })));
  if (selected) sel.value = String(selected);
}
async function loadSavedForms() {
  const r = await api("GET", "/api/forms/list");
  S.forms = r.forms;
  const base = currentBase();
  const list = r.forms.filter((f) => !base || f.base_id === base);
  const sel = $("savedForms");
  const keep = sel.value;
  clear(sel);
  if (!list.length) sel.appendChild(el("option", { value: "", text: base ? "(none recorded for this base yet)" : "(none recorded yet)" }));
  list.forEach((f) => sel.appendChild(el("option", { value: f.form_id, text: f.form_name + " [" + f.form_id + "] - " + f.versions + " version" + (f.versions > 1 ? "s" : "") })));
  if (list.some((f) => f.form_id === keep)) sel.value = keep;
  syncOpenVersions();
}
function syncOpenVersions() {
  const f = S.forms.find((x) => x.form_id === $("savedForms").value);
  const vs = [];
  if (f) for (let v = f.latest_version; v >= 1; v--) vs.push(v);
  fillVersionSelect($("openVersion"), vs, vs[0]);
  $("btnOpen").disabled = !state.ready || !f;
}
function refreshViewPick() {
  const sel = $("viewPick"), free = $("freeTable");
  clear(sel); clear(free);
  const tables = baseTables(currentBase());
  let n = 0;
  for (const t of tables) for (const v of (t.views || [])) {
    if (v.view_type !== "form" || v.stale) continue;
    n++;
    sel.appendChild(el("option", { value: v.view_id, "data-table": t.table_id, text: t.table_name + " / " + v.view_name + " [" + v.view_id + "]" }));
  }
  if (!n) sel.appendChild(el("option", { value: "", text: tables.length ? "(no form views in this base's schema)" : "(ingest this base's schema in Stage 1 first)" }));
  free.appendChild(el("option", { value: "", text: "Any table in the base" }));
  tables.forEach((t) => free.appendChild(el("option", { value: t.table_id, text: t.table_name })));
  $("btnNewView").disabled = !state.ready || !n;
  $("btnNewFree").disabled = !state.ready || !tables.length;
}
function newFromView() {
  const opt = $("viewPick").selectedOptions[0];
  if (!opt || !opt.value) return;
  const existing = S.forms.find((f) => f.form_id === opt.value);
  if (existing) {
    $("savedForms").value = existing.form_id; syncOpenVersions();
    setMsg("pickMessage", "That form view is already recorded (" + existing.versions + " version(s)). Opening the latest; saving creates a new version.", "warn");
    openSaved();
    return;
  }
  const label = opt.textContent;
  startNew({ form_id: opt.value, form_name: label.split(" / ")[1].replace(/ \[.*\]$/, ""), base_id: currentBase(), table_id: opt.dataset.table, view_id: opt.value });
  setMsg("pickMessage", "Started a new record for the Airtable form view " + opt.value + ".", "ok");
}
function newFree() {
  const name = $("freeName").value.trim();
  if (!name) { setMsg("pickMessage", "Enter a name for the form first.", "err"); return; }
  const key = "form-" + name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80);
  if (S.forms.some((f) => f.form_id === key)) { setMsg("pickMessage", "A form with the key " + key + " already exists. Open it from the list, or use another name.", "err"); return; }
  startNew({ form_id: key, form_name: name, base_id: currentBase(), table_id: $("freeTable").value || null, view_id: null });
  setMsg("pickMessage", "Started a new form with the key " + key + ". You can change the key before the first save.", "ok");
}

/* ---------- reports ---------- */
function reportQuery() {
  return "form_id=" + encodeURIComponent(S.form.form_id) + "&version=" + encodeURIComponent($("repVersion").value) + "&target=" + encodeURIComponent($("repTarget").value);
}
function syncReportControls() {
  const has = !!(S.form && S.savedVersion);
  $("btnRepPreview").disabled = !has;
  for (const id of ["lnkRepHtml", "lnkRepMhtml", "lnkLinksCsv"]) $(id).classList.toggle("disabled", !has);
  if (!has) { $("repFrame").hidden = true; setMsg("repStatus", S.form ? "Save the form once to produce documentation." : "", ""); return; }
  const q = reportQuery();
  $("lnkRepHtml").href = "/api/forms/report.html?" + q + "&download=1";
  $("lnkRepMhtml").href = "/api/forms/report.mhtml?" + q;
  $("lnkLinksCsv").href = "/api/export/form_links.csv?form_id=" + encodeURIComponent(S.form.form_id) + "&all_versions=1";
}
async function previewRep() {
  if (!S.form || !S.savedVersion) return;
  $("repFrame").src = "/api/forms/report.html?" + reportQuery();
  $("repFrame").hidden = false;
  try {
    const r = await api("GET", "/api/forms/report/status?" + reportQuery());
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
  $("lnkUsageCsv").href = "/api/export/form_usage.csv?" + q;
  $("lnkAllLinksCsv").href = "/api/export/form_links.csv?all_versions=" + ($("useAll").checked ? "1" : "0");
  const r = await api("GET", "/api/forms/usage?" + q);
  const tb = $("usageTable").querySelector("tbody");
  clear(tb);
  for (const x of r.rows) {
    tb.appendChild(el("tr", {}, el("td", { text: x.field_name + " [" + x.field_id + "]" }), el("td", { text: x.table_name }), el("td", { text: x.form_name }),
      el("td", { class: "num", text: x.version_no }), el("td", { text: r.roles[x.role] || x.role }), el("td", { text: x.item_path }), el("td", { class: "settings", text: x.detail }),
      el("td", { text: x.workfront_dev_field_id }), el("td", { text: x.workfront_prod_field_id })));
  }
  const fields = new Set(r.rows.map((x) => x.field_id)), forms = new Set(r.rows.map((x) => x.form_id));
  $("usageScroll").hidden = !r.rows.length;
  setMsg("usageSummary", r.total ? r.total + " usage row(s): " + fields.size + " field(s) across " + forms.size + " form(s)." + (r.truncated ? " Showing the first 2,000; the .csv has all of them." : "") : "No recorded form matches. Record and save a form above.", r.total ? "" : "warn");
}

/* ---------- page wiring ---------- */
function onReadyChange() {
  const on = state.ready;
  for (const id of ["btnSave", "btnUsage"]) $(id).disabled = !on;
  if (!on) for (const id of ["btnOpen", "btnNewView", "btnNewFree"]) $(id).disabled = true;
}
async function afterConnect() {
  await loadKnownBases();
  refreshViewPick();
  await loadSavedForms();
  await runUsage();
}
const guarded = (fn) => async () => { try { await fn(); } catch (e) { showGlobalError(e.message); } };

startPage("stage2", { onReadyChange, afterConnect }, () => {
  initBaseCombo();
  const dl = el("datalist", { id: "presetSources" }, ["URL parameter (prefill_...)", "Form default", "Set by an automation"].map((t) => el("option", { value: t })));
  document.body.appendChild(dl);
  $("baseId").addEventListener("input", guarded(async () => { refreshViewPick(); await loadSavedForms(); }));
  $("savedForms").addEventListener("change", syncOpenVersions);
  $("btnOpen").addEventListener("click", openSaved);
  $("btnNewView").addEventListener("click", newFromView);
  $("btnNewFree").addEventListener("click", newFree);
  $("btnSave").addEventListener("click", save);
  $("formName").addEventListener("input", () => { if (S.form) { S.form.form_name = $("formName").value; markDirty(); } });
  $("formKey").addEventListener("input", () => { if (S.form && !S.keyLocked) { S.form.form_id = $("formKey").value; markDirty(); } });
  $("repVersion").addEventListener("change", syncReportControls);
  $("repTarget").addEventListener("change", syncReportControls);
  $("btnRepPreview").addEventListener("click", previewRep);
  $("btnUsage").addEventListener("click", guarded(runUsage));
  $("useQ").addEventListener("keydown", (ev) => { if (ev.key === "Enter") guarded(runUsage)(); });
  window.addEventListener("beforeunload", (ev) => { if (S.dirty) { ev.preventDefault(); ev.returnValue = ""; } });
});
