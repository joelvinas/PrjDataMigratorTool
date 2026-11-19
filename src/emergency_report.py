#!/usr/bin/env python3
"""The Emergency mode reports of one Go run: a field report (MHTML), an API log report (MHTML) and a CSV of the same field rows. Standard library only.

Same folder, same name stem: <base>_<table>_<timestamp>_fields.mhtml, ..._apilog.mhtml and ..._fields.csv. The files hold field and table names, IDs, counts, option names,
formula text and short error texts: never a record value and never a token or header (the HTTP layer never hands them over, and every text passes redact() on the way out).
Pure functions plus one writer; the caller supplies the MHTML wrapper and the stylesheet the load order report already uses, so the look is the same. Deterministic apart from
the timestamps the run recorded.
"""
import csv
import io
import json
import os
import re
from html import escape

SECTIONS = (("record_type", "Record type creation"), ("fields", "Field creation"), ("records", "Records load"))

CSV_HEADER = ["base_name", "base_id", "table_name", "table_id", "airtable_field_name", "airtable_field_id", "airtable_data_type", "records", "nulls", "cardinality", "cardinality_is_capped",
              "airtable_options_json", "formula_or_rollup_definition", "longest_value_length", "workspace_name", "workspace_id", "record_type_name", "record_type_id",
              "suggested_planning_name", "suggested_core_name", "suggested_label", "proposed_field_name", "actual_field_name", "planning_field_id", "proposed_data_type", "type_source",
              "implemented_data_type", "outcome", "reason", "attempt_1", "attempt_1_error_code", "attempt_1_error", "attempt_2_type", "attempt_2", "attempt_2_error_code", "attempt_2_error",
              "api_calls", "api_successes", "api_failures", "log_entries", "values_not_converted", "note"]


def stem(result):
    ts = re.sub(r"[^0-9]", "", result["started_at"])
    ts = ts[:8] + "-" + ts[8:12]
    safe = lambda x: re.sub(r"[^A-Za-z0-9_-]", "", x)
    return "Git-R-Done_%s_%s_%s" % (safe(result["base_id"]), safe(result["table_id"]), ts)


def log_ref(span):
    if not span:
        return ""
    return "log entry %d" % span[0] if span[0] == span[1] else "log entries %d to %d" % span


def csv_rows(result):
    rt = result["record_type"]
    n = lambda v: "" if v is None else v
    for f in sorted(result["fields"], key=lambda x: (x["field_name"].lower(), x["field_id"])):
        yield [result.get("base_name", ""), result["base_id"], result["table_name"], result["table_id"], f["field_name"], f["field_id"], f["airtable_type"], n(f["total_records"]), n(f["nulls"]),
               n(f["cardinality"]), "yes" if f["cardinality_capped"] else "no", json.dumps(f["options"], ensure_ascii=False) if f["options"] else "", f["definition"], n(f["max_length"]),
               result.get("workspace_name", ""), result["workspace_id"], rt["name"], n(rt["planning_id"]), f["suggested_planning"], f["suggested_core"], f["suggested_label"], f["sent_name"],
               f["final_name"], f["planning_field_id"], f["attempted_type"], f["attempted_from"], f["final_type"], f["outcome"], f["reason"], f["attempt1"], f["attempt1_error_code"],
               f["attempt1_error"], f["attempt2_type"], f["attempt2"], f["attempt2_error_code"], f["attempt2_error"], f["api_calls"], f["api_successes"], f["api_failures"],
               log_ref(f["log_span"]), f["values_not_converted"] or "", f["note"]]


def csv_bytes(result, redact=lambda t: t):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_HEADER)
    for r in csv_rows(result):
        w.writerow([redact(str(c)) for c in r])
    return buf.getvalue().encode("utf-8")


def _e(x):
    return escape("" if x is None else str(x), quote=True)


def _td(x, cls=""):
    return '<td%s>%s</td>' % (' class="%s"' % cls if cls else "", x)


EXTRA_CSS = """
.tablewin{overflow:auto;max-height:80vh;border:1px solid #d5d9e2;border-radius:4px;margin:8px 0}
.tablewin table{margin:0;min-width:2300px}
.tablewin th{position:sticky;top:0;background:#eef1f8;z-index:2}
.tablewin:focus-visible{outline:2px solid currentColor;outline-offset:-2px}
tr.o-clean td:first-child{border-left:5px solid #2e8b57}
tr.o-pre-converted td:first-child{border-left:5px solid #d99a00}
tr.o-skipped td:first-child{border-left:5px solid #8a93a6}
tr.o-failed td:first-child{border-left:5px solid #c0392b}
details.sec{border:1px solid #d5d9e2;border-radius:6px;margin:10px 0;padding:6px 10px;background:#fff}
details.sec>summary{cursor:pointer;font-weight:700}
.ok{color:#1d6b43}.bad{color:#a62b1f;font-weight:700}
.wrap{white-space:normal;max-width:360px;word-break:break-word}
"""


def fields_html(result, css, apilog_name=None, redact=lambda t: t):
    c = result["counts"]
    h = ['<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
         "<title>Git-R-Done field report</title><style>%s%s</style></head><body><main>" % (css, EXTRA_CSS)]
    h.append("<h1>Git-R-Done: field report</h1>")
    h.append('<p class="meta">Table <strong>%s</strong> (%s) of base %s, loaded into the Planning Workspace %s (stage %s). Run %s to %s (UTC). Field names, counts and error codes only: '
             "no record value is in this report.</p>" % (_e(result["table_name"]), _e(result["table_id"]), _e(result["base_id"]), _e(result["workspace_id"]), _e(result["stage"]),
                                                          _e(result["started_at"]), _e(result["finished_at"])))
    h.append("<h2>Summary</h2><ul>")
    h.append("<li>Fields: %d in all. <strong>%d clean</strong> (the real Planning type worked), <strong>%d converted to PRE- text</strong>, <strong>%d skipped</strong>, "
             "<strong class=\"%s\">%d failed</strong> (both attempts refused).</li>" % (c["fields_total"], c["clean"], c["pre-converted"], c["skipped"], "bad" if c["failed"] else "ok", c["failed"]))
    h.append("<li>Records: %d loaded in this run, %d already loaded by an earlier run, <strong class=\"%s\">%d failed</strong>; in the source: %s.</li>"
             % (c["records_loaded"], c["records_already_loaded"], "bad" if c["records_failed"] else "ok", c["records_failed"],
                "unknown" if result["records"]["in_source"] is None else result["records"]["in_source"]))
    if c["values_not_converted"]:
        h.append("<li>%d value(s) of clean fields could not be sent as that type and were left empty in Planning (see the column \"values not converted\").</li>" % c["values_not_converted"])
    h.append("<li>Record type: %s (%s).</li>" % (_e(result["record_type"]["outcome"]), _e(result["record_type"]["planning_id"] or "none")))
    pr = result.get("prepare")
    if pr:
        h.append("<li>Preparatory step: AirtableRecordId (%s) %s; %d default field(s) deleted, %d renamed%s.</li>" % (_e(pr["field_id"] or "none"), "is the primary field" if pr["primary"] else "is NOT the primary field",
                 len(pr["deleted"]), len(pr.get("renamed", [])), (": " + _e(", ".join(sorted(x["name"] for x in pr["deleted"])))) if pr["deleted"] else ""))
    h.append("<li>Lengths came from %s.</li>" % _e(result["scan_source"] or "no record data (unknown)"))
    h.append("<li>Database table emergency_field_results: %s</li>" % _e(result["db_note"]))
    h.append("</ul>")
    for n in result["notes"]:
        h.append('<div class="box warn">%s</div>' % _e(redact(n)))
    h.append("<h2>Fields</h2>")
    h.append('<div class="tablewin" tabindex="0" role="region" aria-label="Fields of this run"><table><thead><tr>')
    heads = ["Airtable name", "Airtable type", "Suggested Planning name", "Suggested Core name", "Suggested label", "Name sent", "Attempted Planning type", "Outcome", "Why",
             "Actual Planning name", "Actual Planning type", "Error returned", "Max length", "Cardinality", "Nulls", "Total records", "Values not converted", "Option list",
             "Formula or rollup definition", "Log", "Note"]
    h.append("".join("<th>%s</th>" % x for x in heads))
    h.append("</tr></thead><tbody>")
    for f in sorted(result["fields"], key=lambda x: (x["field_name"].lower(), x["field_id"])):
        err = ""
        if f["attempt1"] == "refused":
            err = "%s: %s" % (f["attempted_type"], f["attempt1_error"] or f["attempt1_error_code"])
        if f["attempt2"] == "refused":
            err += ("; " if err else "") + "%s: %s" % (f["attempt2_type"], f["attempt2_error"] or f["attempt2_error_code"])
        ref = log_ref(f["log_span"])
        if ref and apilog_name:
            first = f["log_span"][0]
            ref = '<a href="%s#e%d">%s</a>' % (_e(apilog_name), first, _e(ref))
        else:
            ref = _e(ref)
        if f["options"]:
            opts = "<details><summary>%d option(s)</summary>%s</details>" % (len(f["options"]), "<br>".join(_e(o) for o in f["options"]))
        else:
            opts = ""
        att = f["attempted_type"] + (" (%s)" % f["attempted_from"] if f["attempted_from"] else "")
        card = "" if f["cardinality"] is None else "%s%s" % ("at least " if f["cardinality_capped"] else "", f["cardinality"])
        h.append('<tr class="o-%s">' % _e(f["outcome"] or "skipped") + "".join([
            _td("<strong>%s</strong><br><code>%s</code>" % (_e(f["field_name"]), _e(f["field_id"]))), _td(_e(f["airtable_type"])), _td(_e(f["suggested_planning"])),
            _td(_e(f["suggested_core"])), _td(_e(f["suggested_label"])), _td(_e(f["sent_name"])), _td(_e(att), "wrap"), _td("<strong>%s</strong>" % _e(f["outcome"])),
            _td(_e(f["reason"]), "wrap"), _td(_e(f["final_name"])), _td(_e(f["final_type"])), _td(_e(redact(err)), "wrap"),
            _td("" if f["max_length"] is None else f["max_length"], "n"), _td(_e(card), "n"), _td("" if f["nulls"] is None else f["nulls"], "n"),
            _td("" if f["total_records"] is None else f["total_records"], "n"), _td(f["values_not_converted"] or "", "n"), _td(opts), _td(_e(f["definition"]), "wrap"), _td(ref),
            _td(_e(f["note"]), "wrap")]) + "</tr>")
    h.append("</tbody></table></div>")
    if result["records"]["failures"]:
        h.append("<h2>Records that failed</h2><p class=\"meta\">Airtable record IDs and a short reason only (the first %d).</p>" % len(result["records"]["failures"]))
        h.append('<div class="tablewin" tabindex="0" role="region" aria-label="Failed records"><table style="min-width:600px"><thead><tr><th>Airtable record ID</th><th>Reason</th></tr></thead><tbody>')
        for r in result["records"]["failures"]:
            h.append("<tr>%s%s</tr>" % (_td(_e(r["record_id"])), _td(_e(redact(r["reason"])), "wrap")))
        h.append("</tbody></table></div>")
    h.append("</main></body></html>")
    return "".join(h)


def apilog_html(result, css, airtable_calls=(), redact=lambda t: t):
    calls = result["calls"]
    h = ['<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
         "<title>Git-R-Done API log</title><style>%s%s</style></head><body><main>" % (css, EXTRA_CSS)]
    h.append("<h1>Git-R-Done: API log</h1>")
    h.append('<p class="meta">Every Workfront call of this run for table <strong>%s</strong> (%s), in order, including the calls that worked and the retries. Endpoints are paths '
             "without query; no header, token or request body is recorded, and the text of a failed record call is withheld because it could repeat a value. Run %s to %s (UTC).</p>"
             % (_e(result["table_name"]), _e(result["table_id"]), _e(result["started_at"]), _e(result["finished_at"])))

    def section(title, entries, id_prefix):
        failed = sum(1 for e in entries if not (e["status"] and 200 <= e["status"] < 300))
        out = ['<details class="sec"><summary>%s, %d call%s, %d failed</summary>' % (_e(title), len(entries), "" if len(entries) == 1 else "s", failed)]
        if entries:
            out.append('<div class="tablewin" tabindex="0" role="region" aria-label="%s"><table style="min-width:1000px"><thead><tr><th>No.</th><th>Time (UTC)</th><th>Endpoint</th>'
                       "<th>HTTP status</th><th>Duration (ms)</th><th>Request ID</th><th>Error code</th><th>Error message</th></tr></thead><tbody>" % _e(title))
            for e in entries:
                bad = not (e["status"] and 200 <= e["status"] < 300)
                out.append('<tr id="%s%d"%s>' % (id_prefix, e["number"], ' class="o-failed"' if bad else "") + "".join([
                    _td(e["number"], "n"), _td(_e(e["timestamp"])), _td("<code>%s %s</code>" % (_e(e["method"]), _e(e["path"]))), _td("none" if e["status"] is None else e["status"], "n"),
                    _td(e["duration_ms"], "n"), _td(_e(e["request_id"] or "")), _td(_e(e["error_code"] or "")), _td(_e(redact(e["error_text"] or "")), "wrap")]) + "</tr>")
            out.append("</tbody></table></div>")
        else:
            out.append("<p>No calls.</p>")
        out.append("</details>")
        return "".join(out)

    for key, title in SECTIONS:
        h.append(section(title, [e for e in calls if e["phase"] == key], "e"))
    other = [e for e in calls if e["phase"] not in dict(SECTIONS)]
    if other:
        h.append(section("Other calls", other, "e"))
    if airtable_calls:
        rows = list(airtable_calls)
        failed = sum(1 for r in rows if not r["success"])
        h.append('<details class="sec"><summary>Airtable re-read, %d calls, %d failed</summary><div class="tablewin" tabindex="0" role="region" aria-label="Airtable re-read">'
                 '<table style="min-width:900px"><thead><tr><th>Time (UTC)</th><th>Endpoint</th><th>Attempt</th><th>HTTP status</th><th>Duration (ms)</th><th>Error</th></tr></thead><tbody>'
                 % (len(rows), failed))
        for r in rows:
            h.append("<tr>%s</tr>" % "".join([_td(_e(r["timestamp"])), _td("<code>%s %s</code>" % (_e(r["http_method"]), _e(r["endpoint"]))), _td(r["attempt"], "n"),
                                               _td("" if r["http_status"] is None else r["http_status"], "n"), _td(r["duration_ms"], "n"), _td(_e(redact(r["error_message"] or "")), "wrap")]))
        h.append("</tbody></table></div></details>")
    h.append("</main></body></html>")
    return "".join(h)


def write_reports(result, out_dir, to_mhtml, css, airtable_calls=(), redact=lambda t: t):
    """Write the CSV and both MHTML files into out_dir. Returns {csv, fields, apilog}: the file names (no folder). Plain names only, never a path from outside."""
    os.makedirs(out_dir, exist_ok=True)
    base = stem(result)
    names = {"csv": base + "_fields.csv", "fields": base + "_fields.mhtml", "apilog": base + "_apilog.mhtml"}
    with open(os.path.join(out_dir, names["csv"]), "wb") as f:
        f.write(csv_bytes(result, redact))
    with open(os.path.join(out_dir, names["fields"]), "wb") as f:
        f.write(to_mhtml(fields_html(result, css, names["apilog"], redact), "Git-R-Done field report"))
    with open(os.path.join(out_dir, names["apilog"]), "wb") as f:
        f.write(to_mhtml(apilog_html(result, css, airtable_calls, redact), "Git-R-Done API log"))
    return names
