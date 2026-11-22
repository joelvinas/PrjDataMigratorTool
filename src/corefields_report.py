#!/usr/bin/env python3
"""The reports of the Core bulk field page: one MHTML per operation (scan, create, reset), always written when the operation ends, even when it made no call or failed before the
first one. Standard library only.

A report holds: who ran it, when, the environment, the outcome, the rows it touched and EVERY API attempt in order (method, path and query, the JSON sent, status, duration,
request ID, error code). For a refused call the whole answer Workfront gave is shown (cleaned of any credential). It never holds a header or a token: the HTTP layer never hands
one over, and every text passes redact() on the way out. Field names, labels and descriptions are metadata, not record values.
"""
import json
import os
import re
from html import escape


def _e(x):
    return escape("" if x is None else str(x), quote=True)


def file_stem(op, env, started_at):
    ts = re.sub(r"[^0-9]", "", started_at)
    return "CoreFields_%s_%s_%s-%s" % (op, re.sub(r"[^A-Za-z0-9]", "", env), ts[:8], ts[8:14])


def build_html(job, css, redact=lambda t: t):
    """`job` keys: op, env, actor, reason, started_at, finished_at, status, error, summary (dict), rows (list), entries (list), notes (list)."""
    op = job["op"]
    title = {"scan": "Workfront system scan", "create": "Core field creation", "reset": "Core field reset"}[op]
    s = job.get("summary") or {}
    h = ["<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>%s: %s</title><style>%s\ntd pre{white-space:pre-wrap;word-break:break-all;margin:0;max-width:60em}</style></head><body><main>" % (_e(title), _e(job["env"]), css),
         "<h1>%s</h1>" % _e(title),
         "<table class=\"kv\"><tbody>",
         "<tr><th>Environment</th><td>%s</td></tr>" % _e(job["env"]),
         "<tr><th>Run by</th><td>%s</td></tr>" % _e(job.get("actor") or "(not asked for a scan)"),
         ("<tr><th>Reason</th><td>%s</td></tr>" % _e(job["reason"])) if job.get("reason") else "",
         "<tr><th>Started (UTC)</th><td>%s</td></tr>" % _e(job["started_at"]),
         "<tr><th>Finished (UTC)</th><td>%s</td></tr>" % _e(job.get("finished_at")),
         "<tr><th>Outcome</th><td><strong>%s</strong></td></tr>" % _e(job["status"]),
         ("<tr><th>Error</th><td>%s</td></tr>" % _e(job["error"])) if job.get("error") else "",
         "".join("<tr><th>%s</th><td>%s</td></tr>" % (_e(k.replace("_", " ")), _e(v)) for k, v in sorted(s.items()) if not isinstance(v, (dict, list)) and v is not None),
         "<tr><th>API calls made</th><td>%d</td></tr>" % len(job["entries"]),
         "</tbody></table>"]
    err = s.get("error")
    if isinstance(err, dict):
        h.append("<h2>Where it stopped</h2><p>Row %s, field <strong>%s</strong>: %s</p>" % (_e(err.get("row")), _e(err.get("name")), _e(err.get("message"))))
    for n in job.get("notes") or []:
        h.append("<p class=\"note\">%s</p>" % _e(n))
    h.append("<h2>Rows (%d)</h2>" % len(job["rows"]))
    if job["rows"]:
        h.append("<table><thead><tr><th>Row</th><th>field_id</th><th>Name</th><th>Status</th><th>parameterId</th><th>lastUpdateDate</th><th>Message</th></tr></thead><tbody>")
        for r in job["rows"]:
            h.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % tuple(_e(redact(str(x))) for x in (r["n"], r["field_id"], r["name"], r["status"] or "-", r["parameterId"], r["lastUpdateDate"], r["message"])))
        h.append("</tbody></table>")
    h.append("<h2>API calls (%d)</h2>" % len(job["entries"]))
    if not job["entries"]:
        h.append("<p>No call was made to Workfront in this operation.</p>")
    else:
        h.append("<table><thead><tr><th>#</th><th>Time (UTC)</th><th>Row</th><th>Request</th><th>Sent</th><th>HTTP</th><th>ms</th><th>Request ID</th><th>Error code</th><th>Answer</th></tr></thead><tbody>")
        for e in job["entries"]:
            q = ("?" + "&".join("%s=%s" % (k, e["query"][k]) for k in sorted(e["query"]))) if e.get("query") else ""
            sent = json.dumps(e["body"], sort_keys=True, ensure_ascii=False) if e.get("body") is not None else ""
            failed = e.get("error_text") or (e.get("status") is None) or (e.get("status") and e["status"] >= 400)
            answer = redact(e.get("response_text") or e.get("error_text") or "") if failed else "OK"
            h.append("<tr class=\"%s\"><td>%s</td><td>%s</td><td>%s</td><td>%s %s%s</td><td><pre>%s</pre></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td><pre>%s</pre></td></tr>"
                     % ("bad" if failed else "", e["number"], _e(e["timestamp"]), _e(e.get("row") or ""), _e(e["method"]), _e(e["path"]), _e(redact(q)), _e(redact(sent)), _e(e["status"] if e["status"] is not None else "no answer"),
                        _e(e["duration_ms"]), _e(e.get("request_id") or ""), _e(e.get("error_code") or ""), _e(answer)))
        h.append("</tbody></table>")
    h.append("</main></body></html>")
    return "".join(h)


def write_report(job, out_dir, to_mhtml, css, redact=lambda t: t):
    """Write the MHTML and return its file name. Never raises for an empty operation."""
    os.makedirs(out_dir, exist_ok=True)
    name = file_stem(job["op"], job["env"], job["started_at"]) + ".mhtml"
    html = redact(build_html(job, css, redact))
    with open(os.path.join(out_dir, name), "wb") as f:
        f.write(to_mhtml(html, "Core fields: %s (%s)" % (job["op"], job["env"])))
    return name
