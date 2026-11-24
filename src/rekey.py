#!/usr/bin/env python3
"""Re-key the record types of the DEV Planning Workspace: make the Airtable primary field the record type's primary field. Standard library only.

Git-R-Done makes AirtableRecordId the primary field of every record type (its preparatory step needs a primary field other than Planning's defaults before it can delete them). A link
shows the target record by its primary field, so after a load every link would show an Airtable record ID. Once the data is validated, this step makes the field that was the primary
field in Airtable (airtable_tables.primary_field_id, or airtable_fields.is_primary_field, both stored since Stage 1) the primary field of its record type, so links show names.
AirtableRecordId stays as an ordinary field and keeps its values.

VERIFIED in a DEV Workspace by the owner's Postman run on 23 November 2026: PATCH /v2/record-types/{id} with displayName and primaryFieldId switches the primary field a second time
(from a default field to a text field) and the links then show that field's values. The displayName in that body IS the record type's name; the owner renamed a probe record type
by sending a field's name there. planningtransport.set_primary_field therefore always sends the name it just read and checks it afterwards. Only a TEXT field is proven as the new
primary field, so only a text field is re-keyed; any other type is listed with the reason and left alone.

A person decides and runs this (typed phrase, who, why); nothing is automatic. Standalone and read-only until `run` is called. No record value is read. No schema change: who, when and
why go into the report written by the caller."""
import os
import re
from html import escape

import loadrun as lr

PHRASE = "REKEY RECORD TYPES"
ACTOR_MAX, REASON_MAX = 40, 500


class RekeyError(Exception):
    def __init__(self, message, status=400):
        super(RekeyError, self).__init__(message)
        self.message, self.status = message, status


def candidates(conn, base_id, env_name, ws):
    """One row per table that has a record type in this stage and Workspace, sorted by table name: {table_id, table_name, record_type_id, airtable_field_id, airtable_field_name,
    planning_field_id, planning_field_name, status, reason}. status here is 'unchecked', or 'no_primary' / 'no_field' when it is already known that nothing can be done. Schema and ID map only."""
    rts = conn.execute("SELECT table_id, workfront_id FROM load_id_map WHERE system='planning' AND kind='record_type' AND status='created' AND workfront_id IS NOT NULL AND environment=? "
                       "AND workspace_key=? AND base_id=? AND table_id IS NOT NULL", (env_name, ws or "", base_id)).fetchall()
    out = []
    for r in rts:
        t = conn.execute("SELECT table_name, primary_field_id FROM airtable_tables WHERE base_id=? AND table_id=?", (base_id, r["table_id"])).fetchone()
        if t is None:
            continue
        pf = t["primary_field_id"]
        if not pf:
            x = conn.execute("SELECT field_id FROM airtable_fields WHERE base_id=? AND table_id=? AND is_primary_field=1 ORDER BY field_id", (base_id, r["table_id"])).fetchone()
            pf = x[0] if x else None
        row = {"table_id": r["table_id"], "table_name": t["table_name"], "record_type_id": r["workfront_id"], "airtable_field_id": pf, "airtable_field_name": "", "planning_field_id": "",
               "planning_field_name": "", "current_primary_id": "", "current_primary_name": "", "status": "unchecked", "reason": ""}
        if not pf:
            row.update(status="no_primary", reason="Airtable did not report a primary field for this table (run Stage 1 again)")
        else:
            name = conn.execute("SELECT field_name FROM airtable_fields WHERE base_id=? AND field_id=?", (base_id, pf)).fetchone()
            row["airtable_field_name"] = name[0] if name else pf
            m = conn.execute("SELECT workfront_id, name FROM load_id_map WHERE system='planning' AND kind='field' AND status='created' AND workfront_id IS NOT NULL AND environment=? "
                             "AND workspace_key=? AND base_id=? AND airtable_field_id=?", (env_name, ws or "", base_id, pf)).fetchone()
            if m is None:
                row.update(status="no_field", reason="the Airtable primary field is not in Planning (skipped, abandoned, held back or not loaded yet)")
            else:
                row.update(planning_field_id=m["workfront_id"], planning_field_name=m["name"])
        out.append(row)
    return sorted(out, key=lambda x: (x["table_name"].lower(), x["table_id"]))


def check(rows, tr, progress=None, should_stop=None):
    """Read each record type and its fields from Planning (reads only) and set each row's status: 'already' (it is the primary field), 'eligible', or 'ineligible' with the reason.
    A row that cannot be re-keyed anyway (no_primary, no_field) is not read. Returns the rows."""
    work = [r for r in rows if r["status"] == "unchecked"]
    for n, r in enumerate(work, 1):
        if should_stop and should_stop():
            break
        if progress:
            progress({"phase": "check", "done": n - 1, "total": len(work), "item": r["table_name"]})
        try:
            rt = tr.get_record_type(r["record_type_id"])
            fields = {f["id"]: f for f in tr.list_fields(r["record_type_id"])}
        except Exception as e:
            r.update(status="ineligible", reason="Planning could not be read: %s" % lr.scrub(e))
            continue
        cur = str(rt.get("primaryFieldId") or "")
        r["current_primary_id"], r["current_primary_name"] = cur, (fields.get(cur) or {}).get("name", "")
        f = fields.get(r["planning_field_id"])
        if f is None:
            r.update(status="ineligible", reason="the field is no longer in the record type (check Planning)")
        elif cur == r["planning_field_id"]:
            r.update(status="already", reason="it is already the primary field")
        elif f["type"] != "text":
            r.update(status="ineligible", reason="the field is a %s field; only a text field is re-keyed (other types are not proven as a primary field)" % f["type"])
        elif f["name"].startswith("PRE-"):
            r.update(status="ineligible", reason="the field was stored as PRE- text (raw JSON), not as the real value")
        else:
            r.update(status="eligible", reason="")
    return rows


def validate_decision(actor, reason, phrase):
    """A person's decision: initials or a short name, a reason, and the typed phrase. Raises RekeyError."""
    actor, reason = (actor or "").strip(), (reason or "").strip()
    if not actor or len(actor) > ACTOR_MAX or any(ord(c) < 32 for c in actor):
        raise RekeyError("Enter your initials or a short name (up to %d characters) before re-keying." % ACTOR_MAX)
    if not reason or len(reason) > REASON_MAX or any(ord(c) < 32 and c not in "\n\t" for c in reason):
        raise RekeyError("Give the reason, in plain text of up to %d characters (for example: data validated on <date>)." % REASON_MAX)
    if (phrase or "").strip() != PHRASE:
        raise RekeyError("Type the exact phrase %s to confirm." % PHRASE)
    return actor, reason


def run(rows, tr, table_ids, progress=None, should_stop=None):
    """Re-key the chosen record types (only rows that check() found eligible; anything else is refused with its reason). Each switch is read back by set_primary_field, which also
    confirms the record type's name is unchanged. One failure never stops the others. Returns [{table_id, table_name, record_type_id, planning_field_name, outcome, detail}]
    where outcome is rekeyed, skipped or failed, in table name order."""
    by = {r["table_id"]: r for r in rows}
    out, chosen = [], sorted(set(table_ids), key=lambda t: (t not in by, (by.get(t) or {}).get("table_name", "").lower(), t))
    for n, tid in enumerate(chosen, 1):
        r = by.get(tid)
        res = {"table_id": tid, "table_name": (r or {}).get("table_name", tid), "record_type_id": (r or {}).get("record_type_id"), "planning_field_name": (r or {}).get("planning_field_name", ""), "outcome": "", "detail": ""}
        out.append(res)
        if should_stop and should_stop():
            res.update(outcome="skipped", detail="stopped before this record type")
            continue
        if progress:
            progress({"phase": "rekey", "done": n - 1, "total": len(chosen), "item": res["table_name"]})
        if r is None or r["status"] != "eligible":
            res.update(outcome="skipped", detail=(r["reason"] or r["status"]) if r else "not a record type of this stage")
            continue
        try:
            tr.set_primary_field(r["record_type_id"], r["planning_field_id"])
            res.update(outcome="rekeyed", detail="the primary field is now %s" % r["planning_field_name"])
            r.update(status="already", reason="it is already the primary field")
        except Exception as e:
            res.update(outcome="failed", detail=lr.scrub(e))
    return out


def counts(rows):
    c = {"record_types": len(rows), "eligible": 0, "already": 0, "ineligible": 0, "no_field": 0, "no_primary": 0, "unchecked": 0}
    for r in rows:
        c[r["status"]] = c.get(r["status"], 0) + 1
    return c


# ------------------------------------------------------------------ the report of a re-key run: who, when and why, and what each record type did
def _e(x):
    return escape("" if x is None else str(x), quote=True)


def report_name(info):
    ts = re.sub(r"[^0-9]", "", info["started_at"])
    return "Rekey_%s_%s-%s.mhtml" % (re.sub(r"[^A-Za-z0-9_-]", "", info["base_id"]), ts[:8], ts[8:12])


def report_html(info, css, redact=lambda t: t):
    c = {"rekeyed": 0, "skipped": 0, "failed": 0}
    for r in info["results"]:
        c[r["outcome"]] = c.get(r["outcome"], 0) + 1
    h = ['<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Re-key record types</title><style>%s'
         ".tablewin{overflow:auto;max-height:80vh;border:1px solid #d5d9e2;border-radius:4px;margin:8px 0}.tablewin table{margin:0}.tablewin th{position:sticky;top:0;background:#eef1f8}"
         ".tablewin:focus-visible{outline:2px solid currentColor;outline-offset:-2px}.ok{color:#1d6b43}.bad{color:#a62b1f;font-weight:700}</style></head><body><main>" % css]
    h.append("<h1>Re-key record types</h1>")
    h.append('<p class="meta">Base %s (%s), Planning Workspace %s (stage %s). Run %s to %s (UTC). Decided by <strong>%s</strong>. Names, IDs and counts only.</p>'
             % (_e(info.get("base_name") or ""), _e(info["base_id"]), _e(info["workspace_id"]), _e(info["stage"]), _e(info["started_at"]), _e(info["finished_at"]), _e(info["actor"])))
    h.append("<h2>Why</h2><p>%s</p>" % _e(redact(info["reason"])))
    h.append('<h2>Summary</h2><ul><li><strong>%d re-keyed</strong>, %d skipped, <strong class="%s">%d failed</strong>. The Airtable primary field is now the primary field of each re-keyed record type; AirtableRecordId is still a field of it.</li></ul>'
             % (c["rekeyed"], c["skipped"], "bad" if c["failed"] else "ok", c["failed"]))
    h.append('<div class="tablewin" tabindex="0" role="region" aria-label="Record types"><table style="min-width:800px"><thead><tr><th>Table</th><th>Record type ID</th><th>New primary field</th><th>Primary field before</th><th>Outcome</th><th>Detail</th></tr></thead><tbody>')
    before = {r["table_id"]: r for r in info["rows"]}
    for r in info["results"]:
        b = before.get(r["table_id"]) or {}
        h.append("<tr><td>%s (%s)</td><td>%s</td><td>%s</td><td>%s</td><td><strong>%s</strong></td><td>%s</td></tr>" % (_e(r["table_name"]), _e(r["table_id"]), _e(r["record_type_id"]), _e(r["planning_field_name"]),
                                                                                                             _e(b.get("current_primary_name") or b.get("current_primary_id") or ""), _e(r["outcome"]), _e(redact(r["detail"]))))
    h.append("</tbody></table></div></main></body></html>")
    return "".join(h)


def write_report(info, out_dir, to_mhtml, css, redact=lambda t: t):
    os.makedirs(out_dir, exist_ok=True)
    name = report_name(info)
    with open(os.path.join(out_dir, name), "wb") as f:
        f.write(to_mhtml(report_html(info, css, redact), "Re-key record types"))
    return name
