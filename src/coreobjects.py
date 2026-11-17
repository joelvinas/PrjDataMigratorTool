#!/usr/bin/env python3
"""The Core object load: creates one Workfront Core object (project, request or task) for each registered Airtable record of a table that lands in Planning plus Core.
Standard library only.

Where each object goes comes from coretargets.py: the table's decided object type and its decided parent (object code and ID, kept in the database per table, never in
config.json). A table with no complete decision is skipped and reported; nothing is created for it.

Rules kept here (CLAUDE.md):
  * The object's name is the value of the table's Airtable primary field, read from the saved record file in memory. A name is never written to the database, a report, a
    log or an error message. An empty primary field gives "Untitled <record ID>"; a name over the Core limit fails that record (it is never cut).
  * Every new Core ID is written to target_mappings (system core, kind project, request or task) and the record's `records` marker for the Core environment in the same
    step, so a restart never creates it twice. Records are taken table by table in table-name order and record by record in ID order.
  * One failure never stops the run: the record is listed by Airtable ID with a scrubbed reason and tried again next run.
  * Known limit: a stop between Core's answer and writing it creates the object twice on the next run.

UNVERIFIED (to prove in the first sandbox run; marked in docs/LOADER_PLAN.md): the routes (/proj, /optask, /task) and the parent property names (programID, portfolioID,
projectID, parentID) in coretransport.py; whether a request also needs a queue topic or other required values; that custom field values are not sent here (fields are
attached to forms by hand, so values are a later step).
"""
import loadrun as lr
import recordload as rl
import recordmap as rm
import coretargets as tg

UNTITLED = "Untitled "


def object_name(record, primary_field_id, record_id):
    """The name for one object: the primary field's text, or 'Untitled <record ID>'."""
    v = ((record or {}).get("fields") or {}).get(primary_field_id) if primary_field_id else None
    if isinstance(v, (list, dict)) or v is None or str(v).strip() == "":
        return UNTITLED + str(record_id)
    return str(v).strip()


def load_core_objects(conn, base_id, extract_dir, tr, env, dry_run=True, reader=rl.read_extract):
    """Returns {tables: [{table_id, name, object_code, pending, created, failed, note, failures}], created, failed}. dry_run counts and sends nothing."""
    res = {"tables": [], "created": 0, "failed": 0}
    for t in tg.rows(conn, base_id):
        row = {"table_id": t["table_id"], "name": t["table_name"], "object_code": t["object_code"], "pending": 0, "created": 0, "failed": 0, "note": None, "failures": []}
        res["tables"].append(row)
        if not t["ready"]:
            row["note"] = "skipped: " + t["problem"]
            continue
        pend = rm.pending_records(conn, base_id, t["table_id"], "records", env, "core")
        row["pending"] = len(pend)
        if not pend or dry_run:
            continue
        try:
            doc = reader(extract_dir, base_id, t["table_id"])
        except rl.RecordLoadError as e:
            row["note"] = str(e)
            continue
        primary = (conn.execute("SELECT primary_field_id FROM airtable_tables WHERE base_id=? AND table_id=?", (base_id, t["table_id"])).fetchone() or [None])[0]
        by_id = {r.get("id"): r for r in doc["records"] if isinstance(r, dict)}
        for uid, rid in pend:
            if rid not in by_id:
                row["failed"] += 1
                row["failures"].append((rid, "the record is not in the saved file"))
                continue
            try:
                new = tr.create_object(t["object_code"], object_name(by_id[rid], primary, rid), t["parent_obj_code"], t["parent_obj_id"])
                rm.add_target(conn, uid, "core", env, "", t["core_object_type"], new)
                rm.mark_pass(conn, [uid], "records", env, "core", "")
                row["created"] += 1
            except Exception as e:
                row["failed"] += 1
                row["failures"].append((rid, lr.scrub(e)))
        res["created"] += row["created"]
        res["failed"] += row["failed"]
    return res
