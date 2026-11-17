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

const all = () => Promise.all([loadPasses(), loadMap(), loadAtt()]);
const guarded = (fn) => () => fn().catch((e) => showGlobalError(e.message));

async function afterConnect() { await loadKnownBases(); if (baseNow()) await all(); }

startPage("load", { afterConnect }, () => {
  initBaseCombo();
  sortableTable("passTable");
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
