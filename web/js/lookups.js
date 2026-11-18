"use strict";
/* Lookup lists: a person authorizes a select field with a very long option list to be migrated as a record type of its own (rules and saved decisions in
   lookupstore.py). The tool only flags long lists and proposes a name; every change records who, when and why. Nothing is sent to Workfront from this page.
   Everything is inserted with textContent (via el()), never innerHTML. */

const ACTOR_KEY = "migrationControl.reviewer";
const THRESHOLD_KEY = "migrationControl.lookupThreshold";
const PER_PAGE = 50;
const D = { R: null, page: 0, ticked: new Set() };
const baseNow = () => $("baseId").value.trim();
const actorNow = () => $("actor").value.trim();
const reasonNow = () => $("reason").value.trim();
const thresholdNow = () => { const n = parseInt($("threshold").value, 10); return n >= 1 ? n : 50; };

function say(id, text, kind) { setMsg(id, text, kind); }
function tag(text, cls) { return el("span", { class: "tag" + (cls ? " " + cls : ""), text }); }
function btn(text, handler, opts) {
  const b = el("button", Object.assign({ type: "button", text }, opts || {}));
  if (handler) b.addEventListener("click", handler);
  return b;
}

async function load() {
  const base = baseNow();
  if (!/^app[A-Za-z0-9]{4,}$/.test(base)) { D.R = null; D.ticked.clear(); render(); return; }
  try {
    D.R = await api("GET", "/api/lookups/review?base_id=" + encodeURIComponent(base) + "&threshold=" + thresholdNow());
    D.page = 0; say("pageMessage", "");
  } catch (e) { D.R = null; say("pageMessage", e.message, "err"); }
  render();
}

/* One call for every change: the same initials and reason go with it, and the page is rebuilt from what the server returns. */
async function decide(path, body, msgId, okText) {
  if (!actorNow()) { say(msgId, "Enter your initials (top of the page) before making a change.", "err"); $("actor").focus(); return null; }
  if (!reasonNow()) { say(msgId, "Enter a short reason (top of the page): it is kept with every change.", "err"); $("reason").focus(); return null; }
  try {
    const r = await api("POST", path, Object.assign({ base_id: baseNow(), actor: actorNow(), reason: reasonNow(), threshold: thresholdNow() }, body));
    if (r.review) D.R = r.review;
    render();
    say(msgId, typeof okText === "function" ? okText(r) : okText || "Done.", "ok");
    return r;
  } catch (e) { say(msgId, e.message, "err"); return null; }
}

function render() {
  const R = D.R;
  $("notReady").hidden = !R || R.ready;
  if (R && !R.ready) $("notReadyText").textContent = "Saving lookup lists needs migration 024 applied to this database. Still missing: " + R.missing.join(", ") + ". Back up the file, run python sql/apply_draft_migrations.py on it, then connect again. You can look at the select fields below; nothing can be saved until then.";
  renderSummary(); renderLists(); renderFields(); renderControls();
}

function renderSummary() {
  const strip = $("summaryStrip"); strip.replaceChildren();
  if (!D.R) return;
  const c = D.R.counts;
  strip.appendChild(el("div", { class: "note" }, el("b", { text: "Select fields: " }), el("span", { text: fmt(c.select_fields) + ", of which " + fmt(c.long) + " are long lists (" + fmt(D.R.threshold) + " options or more); " + fmt(c.authorized) + " authorized as lookup lists." }),
    c.long_not_decided ? tag(fmt(c.long_not_decided) + " long, not yet decided", "stale") : null));
  if (c.repeats) strip.appendChild(el("div", { class: "note warn", text: fmt(c.repeats) + " select field(s) have options that repeat once letter case is ignored. Planning refuses that in a select field (the load stops it before sending); a lookup list is one way out. The Options column shows how many repeat." }));
}

function renderLists() {
  const body = $("listsGrid").tBodies[0]; body.replaceChildren();
  if (!D.R) return;
  const ready = D.R.ready;
  for (const l of D.R.lists) {
    const name = el("input", { type: "text", value: l.name, maxlength: "120", "aria-label": "Name of the list " + l.name, class: "wide" });
    const rename = btn("Rename", () => decide("/api/lookups/rename", { list_id: l.list_id, name: name.value }, "listsMessage", "List renamed."), { disabled: !ready });
    const del = btn("Delete", () => decide("/api/lookups/delete", { list_id: l.list_id }, "listsMessage", "List deleted."), { disabled: !ready || l.field_ids.length > 0, title: l.field_ids.length ? "Return its fields to select fields first." : "" });
    body.appendChild(el("tr", {}, el("td", {}, name), el("td", { text: l.field_names.length ? l.field_names.join("; ") : "(no field yet)" }), el("td", { class: "num", text: fmt(l.option_count) }),
      el("td", { text: l.created_by + ", " + l.created_at }), el("td", { class: "hint", text: l.reason || "" }), el("td", {}, rename, del)));
  }
  if (!D.R.lists.length) body.appendChild(el("tr", {}, el("td", { colspan: "6", class: "hint", text: "No lookup list yet. Tick fields below and authorize them." })));
}

function tableList() {
  const m = new Map();
  for (const f of D.R.fields) m.set(f.table_id, f.table_name);
  return [...m.entries()].sort((a, b) => a[1].localeCompare(b[1]));
}
function fillSelect(sel, values, blankText) {
  const keep = sel.value;
  sel.replaceChildren(el("option", { value: "", text: blankText }), ...values.map(([v, t]) => el("option", { value: v, text: t })));
  sel.value = values.some(([v]) => v === keep) ? keep : "";
}
function fieldMatches(f) {
  if (f.abandoned || f.consolidated) return false;
  const show = $("fShow").value, t = $("fTable").value, q = $("fSearch").value.trim().toLowerCase();
  if (show === "long" && !f.long) return false;
  if (show === "undecided" && !(f.long && !f.list)) return false;
  if (show === "authorized" && !f.list) return false;
  if (show === "repeats" && !f.repeat_count) return false;
  if (t && f.table_id !== t) return false;
  return !q || f.field_name.toLowerCase().includes(q) || f.field_id.toLowerCase().includes(q);
}
function pageRows() {
  const list = D.R.fields.filter(fieldMatches);
  const pages = Math.max(1, Math.ceil(list.length / PER_PAGE));
  D.page = Math.min(D.page, pages - 1);
  return { list, pages, rows: list.slice(D.page * PER_PAGE, (D.page + 1) * PER_PAGE) };
}

function optionsCell(f) {
  const bits = [el("div", { text: fmt(f.option_count) + " option(s)" })];
  if (f.long) bits.push(tag("long list", "stale"));
  if (f.repeat_count) bits.push(tag(fmt(f.repeat_count) + " repeat", "fail"));
  if (f.blank_count) bits.push(tag(fmt(f.blank_count) + " blank", "fail"));
  if (f.sample.length) bits.push(el("div", { class: "hint", text: f.sample.join(", ") + (f.option_count > f.sample.length ? ", ..." : "") }));
  return el("td", {}, bits);
}
function renderFields() {
  const body = $("fieldsGrid").tBodies[0]; body.replaceChildren();
  if (!D.R) { $("pageInfo").textContent = ""; return; }
  fillSelect($("fTable"), tableList(), "All tables");
  const { list, pages, rows } = pageRows();
  $("pageInfo").textContent = "Page " + (D.page + 1) + " of " + pages + " (" + fmt(list.length) + " fields)";
  $("btnPrev").disabled = D.page === 0; $("btnNext").disabled = D.page >= pages - 1;
  for (const f of rows) {
    const box = el("input", { type: "checkbox", "aria-label": "Tick " + f.table_name + " " + f.field_name });
    box.checked = D.ticked.has(f.field_id);
    box.addEventListener("change", () => { if (box.checked) D.ticked.add(f.field_id); else D.ticked.delete(f.field_id); renderControls(); });
    const lookup = f.list ? el("td", {}, tag(f.list.name, "done")) : el("td", { class: "hint", text: "ordinary select field" });
    const dec = f.list ? el("td", { class: "hint", text: f.list.by + ", " + f.list.at + (f.list.reason ? " (" + f.list.reason + ")" : "") }) : el("td", {});
    body.appendChild(el("tr", {}, el("td", {}, box), el("td", { text: f.table_name }), el("td", {}, el("div", { text: f.field_name }), el("div", { class: "hint", text: f.field_id })),
      el("td", { text: f.airtable_type }), optionsCell(f), lookup, dec));
  }
  if (!rows.length) body.appendChild(el("tr", {}, el("td", { colspan: "7", class: "hint", text: "No select field matches." })));
}

function renderControls() {
  const R = D.R, ready = !!R && R.ready;
  const valid = new Set(R ? R.fields.map((f) => f.field_id) : []);
  for (const id of [...D.ticked]) if (!valid.has(id)) D.ticked.delete(id);
  $("selCount").textContent = fmt(D.ticked.size) + " field(s) ticked.";
  if (R) {
    const keep = $("targetList").value;
    $("targetList").replaceChildren(el("option", { value: "", text: "A new list (name it here)" }), ...R.lists.map((l) => el("option", { value: String(l.list_id), text: l.name })));
    $("targetList").value = [...$("targetList").options].some((o) => o.value === keep) ? keep : "";
  }
  const existing = $("targetList").value !== "";
  $("newName").hidden = existing;
  if (!existing && R && !$("newName").value && D.ticked.size) {            // propose a name from the first ticked field; a person may change it
    const f = R.fields.find((x) => D.ticked.has(x.field_id));
    if (f) $("newName").value = f.proposed_name;
  }
  $("btnAssign").disabled = !ready || !D.ticked.size;
  $("btnUnassign").disabled = !ready || !D.ticked.size;
  $("tickAll").checked = false;
}

const guarded = (fn) => async () => { try { await fn(); } catch (e) { showGlobalError(e.message); } };
async function afterConnect() { await loadKnownBases(); await load(); }

startPage("lookups", { onReadyChange() {}, afterConnect }, () => {
  initBaseCombo();
  try { $("actor").value = sessionStorage.getItem(ACTOR_KEY) || ""; } catch (e) { /* storage can be blocked */ }
  try { const t = parseInt(localStorage.getItem(THRESHOLD_KEY), 10); if (t >= 1) $("threshold").value = String(t); } catch (e) { /* ignore */ }
  $("actor").addEventListener("input", () => { try { sessionStorage.setItem(ACTOR_KEY, actorNow()); } catch (e) { /* ignore */ } });
  $("baseId").addEventListener("input", guarded(load));
  $("threshold").addEventListener("change", guarded(async () => { try { localStorage.setItem(THRESHOLD_KEY, String(thresholdNow())); } catch (e) { /* ignore */ } await load(); }));
  for (const id of ["fShow", "fTable"]) $(id).addEventListener("change", () => { D.page = 0; if (D.R) renderFields(); });
  $("fSearch").addEventListener("input", () => { D.page = 0; if (D.R) renderFields(); });
  $("btnPrev").addEventListener("click", () => { D.page = Math.max(0, D.page - 1); renderFields(); });
  $("btnNext").addEventListener("click", () => { D.page += 1; renderFields(); });
  $("targetList").addEventListener("change", renderControls);
  $("tickAll").addEventListener("change", () => {
    if (!D.R) return;
    const on = $("tickAll").checked;
    for (const f of pageRows().rows) { if (on) D.ticked.add(f.field_id); else D.ticked.delete(f.field_id); }
    renderFields(); renderControls(); $("tickAll").checked = on;
  });
  $("btnAssign").addEventListener("click", async () => {
    const existing = $("targetList").value;
    const body = { field_ids: [...D.ticked] };
    if (existing) body.list_id = parseInt(existing, 10); else body.list_name = $("newName").value;
    const r = await decide("/api/lookups/assign", body, "fieldsMessage", (x) => fmt(x.changed) + " field(s) authorized as a lookup list" + (x.created ? " (new list created)" : "") + ", " + fmt(x.unchanged) + " already there (batch " + x.batch + ").");
    if (r) { D.ticked.clear(); $("newName").value = ""; renderFields(); renderControls(); }
  });
  $("btnUnassign").addEventListener("click", async () => {
    const r = await decide("/api/lookups/unassign", { field_ids: [...D.ticked] }, "fieldsMessage", (x) => fmt(x.changed) + " field(s) returned to ordinary select fields (batch " + x.batch + ").");
    if (r) { D.ticked.clear(); renderFields(); renderControls(); }
  });
});
