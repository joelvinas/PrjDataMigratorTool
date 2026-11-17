/* Stage 4: Map and load. Shows what the loader has recorded: where each table stands, the record map and the attachment map.
   IDs, statuses, counts and sizes only: never a record value, a file name or a link. Sorting happens on the server so it covers the whole list, not one page. */
const PAGE = 100;
const view = {
  map: { sort: "record_id", dir: "asc", offset: 0, total: 0 },
  att: { sort: "row", dir: "asc", offset: 0, total: 0 },
};
const baseNow = () => $("baseId").value.trim();
const isPlanning = () => ["dev", "uat"].includes($("envSel").value);
const common = () => "base_id=" + encodeURIComponent(baseNow()) + "&environment=" + encodeURIComponent($("envSel").value);
const planningQ = () => "&system=" + (isPlanning() ? "planning" : "core") + "&workspace_key=" + encodeURIComponent(isPlanning() ? $("wsKey").value.trim() : "");

function needBase() {
  if (!baseNow()) { setMsg("pageMessage", "Choose a base first.", "err"); return false; }
  if (isPlanning() && !$("wsKey").value.trim()) { setMsg("pageMessage", "A Planning environment needs the Planning Workspace ID (it is in config.json).", "err"); return false; }
  setMsg("pageMessage", "", "");
  return true;
}

async function loadPasses() {
  const t = $("passTable"), body = t.querySelector("tbody");
  clear(body);
  if (!needBase()) { t.hidden = true; return; }
  const d = await api("GET", "/api/records/registry?" + common() + planningQ());
  d.tables.forEach((r) => {
    const p = r.passes || {};
    body.appendChild(el("tr", {}, el("td", { "data-sort": r.table_name, text: r.table_name }), el("td", { text: r.table_id }), el("td", { class: "num", text: fmt(r.registered) }),
      el("td", { class: "num", text: fmt(p.records || 0) }), el("td", { class: "num", text: fmt(p.links || 0) }), el("td", { class: "num", text: fmt(p.attachments || 0) }),
      el("td", { class: "num", text: fmt(p.core_backfill || 0) }), el("td", { class: "num", text: fmt(r.targets || 0) })));
  });
  t.hidden = d.tables.length === 0;
  applySort("passTable");
  const a = d.attachments || {};
  setMsg("passSummary", fmt(d.registered) + " record(s) registered. Attachments in " + $("envSel").value + ": " + Object.keys(a).sort().map((k) => fmt(a[k]) + " " + k).join(", ") + ".", "");
  const sel = $("mapTable"), keep = sel.value;
  clear(sel);
  sel.appendChild(el("option", { value: "", text: "All tables" }));
  d.tables.filter((x) => x.registered).forEach((x) => sel.appendChild(el("option", { value: x.table_id, text: x.table_name + " (" + fmt(x.registered) + ")" })));
  sel.value = keep;
}

function headers(tableId, state, reload) {
  const t = $(tableId);
  Array.from(t.tHead.rows[0].cells).forEach((th) => {
    const key = th.dataset.key;
    if (!key) return;
    th.classList.add("sortable");
    th.setAttribute("tabindex", "0"); th.setAttribute("role", "button"); th.setAttribute("title", "Click to sort the whole list");
    const go = () => {
      if (state.sort === key) state.dir = state.dir === "asc" ? "desc" : "asc"; else { state.sort = key; state.dir = "asc"; }
      state.offset = 0;
      reload().catch((e) => showGlobalError(e.message));
    };
    th.addEventListener("click", go);
    th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
  });
}

function mark(tableId, state) {
  Array.from($(tableId).tHead.rows[0].cells).forEach((th) => {
    th.classList.toggle("sorted-asc", th.dataset.key === state.sort && state.dir === "asc");
    th.classList.toggle("sorted-desc", th.dataset.key === state.sort && state.dir === "desc");
    if (th.dataset.key) th.setAttribute("aria-sort", th.dataset.key === state.sort ? (state.dir === "asc" ? "ascending" : "descending") : "none");
  });
}

function pager(id, state, shown) {
  const from = state.total ? state.offset + 1 : 0;
  $(id).textContent = fmt(from) + " to " + fmt(state.offset + shown) + " of " + fmt(state.total);
}

async function loadMap() {
  const t = $("mapGrid"), body = t.querySelector("tbody"), s = view.map;
  clear(body);
  if (!needBase()) { t.hidden = true; return; }
  const q = "/api/records/view?" + common() + planningQ() + "&table_id=" + encodeURIComponent($("mapTable").value) + "&q=" + encodeURIComponent($("mapQ").value.trim()) +
    "&sort=" + s.sort + "&dir=" + s.dir + "&limit=" + PAGE + "&offset=" + s.offset;
  const d = await api("GET", q);
  s.total = d.total;
  d.rows.forEach((r) => body.appendChild(el("tr", {}, el("td", { class: "num", text: r.uid }), el("td", { text: r.table_name }), el("td", { text: r.record_id }), el("td", { text: r.targets }), el("td", { text: r.passes.join(", ") }))));
  t.hidden = d.rows.length === 0;
  mark("mapGrid", s);
  pager("mapPage", s, d.rows.length);
  $("btnMapPrev").disabled = s.offset === 0;
  $("btnMapNext").disabled = s.offset + d.rows.length >= d.total;
}

async function loadAtt() {
  const t = $("attGrid"), body = t.querySelector("tbody"), s = view.att;
  clear(body);
  if (isPlanning()) { t.hidden = true; setMsg("attSummary", "Attachments move to Core: choose a Core environment above to see them.", ""); pager("attPage", { total: 0, offset: 0 }, 0); return; }
  if (!baseNow()) { t.hidden = true; return; }
  const d = await api("GET", "/api/records/attachments?" + common() + "&status=" + encodeURIComponent($("attStatus").value) + "&sort=" + s.sort + "&dir=" + s.dir + "&limit=" + PAGE + "&offset=" + s.offset);
  s.total = d.total;
  d.rows.forEach((r) => body.appendChild(el("tr", {}, el("td", { class: "num", text: r.row_id }), el("td", { text: r.table_id }), el("td", { text: r.record_id }), el("td", { text: r.field_id }), el("td", { text: r.attachment_id }),
    el("td", {}, el("span", { class: "tag " + (r.status === "failed" ? "cap" : r.status === "uploaded" ? "fresh" : ""), text: r.status })),
    el("td", { class: "num", text: r.downloaded_bytes !== null ? fmt(r.downloaded_bytes) : r.api_size_bytes !== null ? fmt(r.api_size_bytes) + " (reported)" : "" }),
    el("td", { text: r.core_document_id || "" }), el("td", { text: r.sha256 ? r.sha256.slice(0, 12) + "..." : "", title: r.sha256 || "" }), el("td", { text: r.last_error || "" }))));
  t.hidden = d.rows.length === 0;
  setMsg("attSummary", d.total ? "" : "No attachments are recorded for " + $("envSel").value + " yet.", "");
  mark("attGrid", s);
  pager("attPage", s, d.rows.length);
  $("btnAttPrev").disabled = s.offset === 0;
  $("btnAttNext").disabled = s.offset + d.rows.length >= d.total;
}

/* ---------- where Core objects go ---------- */
const HEX32 = /^[0-9a-fA-F]{32}$/;
let coreData = null;
const TYPE_LABEL = { project: "Project (PROJ)", request: "Request (OPTASK)", task: "Task (TASK)" };

function parentOptions(type) {
  const opts = [el("option", { value: "", text: type ? "Choose..." : "Decide the type first" })];
  if (!type) return opts;
  if (coreData.parent_optional[type]) opts.push(el("option", { value: "none", text: "No parent" }));
  coreData.parent_codes[type].forEach((c) => opts.push(el("option", { value: c, text: coreData.parent_labels[c] + " (" + c + ")" })));
  return opts;
}

function renderCore(d) {
  coreData = d;
  const t = $("coreGrid"), body = t.querySelector("tbody");
  clear(body);
  d.rows.forEach((row) => {
    let r = row;
    const typeSel = el("select", { "aria-label": "What " + r.table_name + " becomes in Core" }, el("option", { value: "", text: "Not decided" }), d.object_types.map((o) => el("option", { value: o.type, text: TYPE_LABEL[o.type] })));
    typeSel.value = r.core_object_type || "";
    const parSel = el("select", { "aria-label": "Parent object type for " + r.table_name });
    const idIn = el("input", { type: "text", maxlength: "100", placeholder: "32 hexadecimal characters", "aria-label": "Parent ID for " + r.table_name, autocomplete: "off" });
    const why = el("input", { type: "text", maxlength: "500", placeholder: "why (recorded)", "aria-label": "Reason for " + r.table_name, autocomplete: "off" });
    const fillParent = () => {
      clear(parSel);
      parentOptions(typeSel.value).forEach((o) => parSel.appendChild(o));
      const opts = coreData.parent_codes[typeSel.value] || [];
      parSel.value = r.parent_decided ? (r.parent_obj_code === null ? (coreData.parent_optional[typeSel.value] ? "none" : "") : opts.includes(r.parent_obj_code) ? r.parent_obj_code : "") : "";
      idIn.disabled = parSel.value === "none" || parSel.value === "";
    };
    idIn.value = r.parent_obj_id || "";
    typeSel.addEventListener("change", () => { idIn.value = ""; r = Object.assign({}, r, { parent_decided: false }); fillParent(); });
    parSel.addEventListener("change", () => { idIn.disabled = parSel.value === "none" || parSel.value === ""; if (idIn.disabled) idIn.value = ""; });
    fillParent();
    const save = el("button", { type: "button", text: "Save" });
    save.addEventListener("click", () => saveCore(row, typeSel.value, parSel.value, idIn.value.trim(), why.value.trim()).catch((e) => setMsg("coreMessage", e.message, "err")));
    const clr = el("button", { type: "button", text: "Clear" });
    clr.addEventListener("click", () => clearCore(row, why.value.trim()).catch((e) => setMsg("coreMessage", e.message, "err")));
    const status = r.ready ? "Ready. Decided by " + (r.type_by || "?") + (r.type_at ? " on " + r.type_at.slice(0, 10) : "") + "; parent by " + (r.parent_by || "?") + "."
      : r.problem + (r.type_by ? " (type decided by " + r.type_by + ")" : "");
    body.appendChild(el("tr", { class: r.ready ? "" : "undecided" }, el("td", { "data-sort": r.table_name }, el("b", { text: "[" + r.table_name + "]" }), el("div", { class: "hint", text: r.table_id })),
      el("td", {}, typeSel), el("td", {}, parSel), el("td", {}, idIn), el("td", {}, why), el("td", { "data-sort": r.ready ? "ready" : "open", text: status }), el("td", { class: "stack" }, save, clr)));
  });
  t.hidden = d.rows.length === 0;
  setMsg("coreMessage", d.rows.length ? "" : "No table is in scope and set to land in Planning plus Core yet. Decide that on the Field names page (tables).", "");
  applySort("coreGrid");
  const h = $("coreHistory"), hb = h.querySelector("tbody");
  clear(hb);
  const names = {};
  d.rows.forEach((x) => { names[x.table_id] = x.table_name; });
  d.history.forEach((e) => hb.appendChild(el("tr", {}, el("td", { text: e.created_at }), el("td", { text: names[e.table_id] || e.table_id }), el("td", { text: (e.subject === "parent" ? "parent " : "object type ") + e.event_type }),
    el("td", { text: e.old_value || "" }), el("td", { text: e.new_value || "" }), el("td", { text: e.actor }), el("td", { text: e.reason || "" }))));
  h.hidden = d.history.length === 0;
}

async function loadCore() {
  if (!baseNow()) { $("coreGrid").hidden = true; return; }
  renderCore(await api("GET", "/api/core/targets?base_id=" + encodeURIComponent(baseNow())));
}

async function saveCore(r, type, parentCode, parentId, reason) {
  const actor = $("actor").value.trim();
  if (!actor) { setMsg("coreMessage", "Enter your initials first.", "err"); $("actor").focus(); return; }
  if (!type) { setMsg("coreMessage", "Choose what " + r.table_name + " becomes in Core first.", "err"); return; }
  if (!parentCode) { setMsg("coreMessage", "Choose the parent object type for " + r.table_name + (coreData.parent_optional[type] ? " (or 'No parent')." : "."), "err"); return; }
  if (parentCode !== "none" && !HEX32.test(parentId)) { setMsg("coreMessage", "A Workfront ID is 32 hexadecimal characters (0 to 9 and a to f). Check the parent ID for " + r.table_name + ".", "err"); return; }
  const base = baseNow();
  let d = null;
  if (type !== r.core_object_type) d = await api("POST", "/api/core/object-type", { base_id: base, table_id: r.table_id, object_type: type, actor, reason });
  d = await api("POST", "/api/core/parent", parentCode === "none" ? { base_id: base, table_id: r.table_id, none: true, actor, reason } : { base_id: base, table_id: r.table_id, parent_code: parentCode, parent_id: parentId, actor, reason });
  renderCore(d);
  setMsg("coreMessage", "Saved for " + r.table_name + ".", "ok");
}

async function clearCore(r, reason) {
  const actor = $("actor").value.trim();
  if (!actor) { setMsg("coreMessage", "Enter your initials first.", "err"); $("actor").focus(); return; }
  const base = baseNow();
  await api("POST", "/api/core/parent", { base_id: base, table_id: r.table_id, clear: true, actor, reason });
  const d = await api("POST", "/api/core/object-type", { base_id: base, table_id: r.table_id, object_type: "clear", actor, reason });
  renderCore(d);
  setMsg("coreMessage", "Cleared for " + r.table_name + ".", "ok");
}

const all = () => Promise.all([loadCore(), loadPasses(), loadMap(), loadAtt()]);
const guarded = (fn) => () => fn().catch((e) => showGlobalError(e.message));

async function afterConnect() { await loadKnownBases(); if (baseNow()) await all(); }

startPage("load", { afterConnect }, () => {
  initBaseCombo();
  sortableTable("passTable"); sortableTable("coreGrid");
  try { $("actor").value = sessionStorage.getItem("migrationControl.actor") || ""; } catch (e) { /* storage can be blocked */ }
  $("actor").addEventListener("input", () => { try { sessionStorage.setItem("migrationControl.actor", $("actor").value.trim()); } catch (e) { /* ignore */ } });
  headers("mapGrid", view.map, loadMap);
  headers("attGrid", view.att, loadAtt);
  $("baseId").addEventListener("input", guarded(all));
  $("btnRefresh").addEventListener("click", guarded(all));
  $("envSel").addEventListener("change", () => { view.map.offset = 0; view.att.offset = 0; guarded(all)(); });
  $("wsKey").addEventListener("change", () => { view.map.offset = 0; guarded(all)(); });
  $("mapTable").addEventListener("change", () => { view.map.offset = 0; guarded(loadMap)(); });
  $("btnMapSearch").addEventListener("click", () => { view.map.offset = 0; guarded(loadMap)(); });
  $("mapQ").addEventListener("keydown", (e) => { if (e.key === "Enter") { view.map.offset = 0; guarded(loadMap)(); } });
  $("btnMapPrev").addEventListener("click", () => { view.map.offset = Math.max(0, view.map.offset - PAGE); guarded(loadMap)(); });
  $("btnMapNext").addEventListener("click", () => { view.map.offset += PAGE; guarded(loadMap)(); });
  $("attStatus").addEventListener("change", () => { view.att.offset = 0; guarded(loadAtt)(); });
  $("btnAttPrev").addEventListener("click", () => { view.att.offset = Math.max(0, view.att.offset - PAGE); guarded(loadAtt)(); });
  $("btnAttNext").addEventListener("click", () => { view.att.offset += PAGE; guarded(loadAtt)(); });
});
