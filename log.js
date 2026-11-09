"use strict";
/* API call log page. Shows the audit log kept in api_call_log (counts and statuses only, never headers, tokens or record values). */

/* ---------- API call log ---------- */
async function loadLog() {
  const q = new URLSearchParams({ failures: $("logFail").checked ? "1" : "", table_id: $("logTable").value, purpose: $("logPurpose").value, limit: "100" });
  const data = await api("GET", "/api/calllog?" + q.toString());
  const fillSel = (sel, values, allLabel) => {
    const keep = sel.value; clear(sel); sel.appendChild(el("option", { value: "", text: allLabel }));
    values.forEach((v) => sel.appendChild(el("option", { value: v, text: v })));
    sel.value = values.includes(keep) ? keep : "";
  };
  fillSel($("logTable"), data.table_ids, "All tables");
  fillSel($("logPurpose"), data.purposes, "All purposes");
  const s = data.summary;
  setMsg("logSummary", "Matching calls: " + fmt(s.total) + " (" + fmt(s.success) + " succeeded, " + fmt(s.failure) + " failed). Showing the latest " + data.rows.length + ".", s.failure ? "warn" : "");
  const body = $("logTableEl").querySelector("tbody");
  clear(body);
  data.rows.forEach((r) => body.appendChild(el("tr", {}, el("td", { text: (r.timestamp || "").slice(0, 19) }), el("td", { text: r.purpose }), el("td", { text: r.endpoint }), el("td", { text: r.table_id || "" }),
    el("td", { class: "num", text: r.attempt }), el("td", { class: "num", text: r.http_status === null ? "none" : r.http_status }), el("td", { class: r.success ? "good" : "bad", text: r.success ? "yes" : "no" }),
    el("td", { class: "num", text: fmt(r.duration_ms) }), el("td", { class: "num", text: fmt(r.records_returned) }), el("td", { text: r.error_message || "" }))));
}

/* ---------- wiring ---------- */
function guard(fn) { return () => fn().catch((e) => showGlobalError(e.message)); }

startPage("log", {
  onReadyChange() { $("btnLog").disabled = !state.ready; },
  afterConnect: loadLog,
}, () => {
  $("btnLog").addEventListener("click", guard(loadLog));
  for (const id of ["logFail", "logTable", "logPurpose"]) $(id).addEventListener("change", guard(loadLog));
});
