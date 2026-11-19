#!/usr/bin/env python3
"""The record load for Workfront Planning: moves the saved Airtable records into the record types the field load created. Standard library only.

Two passes, in this order (the owner confirmed the second pass is required):
    records  every record is created with its ordinary fields (no links). Each new Planning record is written to target_mappings and its surrogate ID gets the
             `records` pass marker, in the same step, so a stop never repeats a finished batch.
    links    every record that has a finished records pass gets its link fields set, from the registry: an Airtable record ID becomes the Planning record ID that was
             created for it. A link whose target record does not exist yet is left for a later run, never guessed; the `links` marker is written only when every link
             of that record was resolved.

Rules kept here (CLAUDE.md):
  * A record VALUE is read from the saved record file in memory, converted, sent to Workfront and forgotten. It never goes into SQLite, a log, a report, an error message
    or the browser. A conversion problem names the field ID and a short reason, never the value.
  * Deterministic: tables in table ID order, records in record ID order, batches of a fixed size.
  * Idempotent: only records with no marker are sent. A partly failed batch (HTTP 207) records its successes by requestIndex and fails only the failed records. Known limit, stated plainly: if the program stops AFTER Workfront answered a batch and BEFORE the answer is written
    here, that batch is sent again on the next run and its records exist twice. The batch size is small (settings: record_batch_size) to keep that window short.
  * Nothing is sent without the caller's explicit step, and a dry run only counts.

VERIFIED (Postman test 5, 17 November 2026): the bulk route is per record type and the body is [{"data": {fieldId: value}}] (see planningtransport.py). UNVERIFIED (to be
proven in the first DEV run; marked in docs/LOADER_PLAN.md): the value formats Planning accepts (dates as ISO text, percentages as Airtable's fraction, a link as a list of
record IDs) and the link update route.
"""
import datetime
import json
import os
import re

import loadrun as lr
import planningtransport as pt
import recordmap as rm
import typemap as tm

BATCH_DEFAULT = 50
DOC_FORMAT = "airtable-table-extract/1"
MAX_BYTES = 256 * 1024 * 1024
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


class RecordLoadError(Exception):
    pass


# ------------------------------------------------------------------ reading the saved record file (in memory only)
def read_extract(extract_dir, base_id, table_id):
    """The saved record file of one table as a dict, read whole into memory. Raises RecordLoadError with a plain message."""
    if not (re.match(r"^[A-Za-z0-9_-]+$", base_id or "") and re.match(r"^[A-Za-z0-9_-]+$", table_id or "")):
        raise RecordLoadError("A base or table ID contains characters that are not allowed in a file name.")
    path = os.path.join(extract_dir, base_id, table_id + ".json")
    if not os.path.isfile(path):
        raise RecordLoadError("There is no saved record file for table %s. Profile it with \"Also save every record to JSON files\" ticked first." % table_id)
    if os.path.getsize(path) > MAX_BYTES:
        raise RecordLoadError("The saved record file for table %s is too large to read here." % table_id)
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        raise RecordLoadError("The saved record file for table %s could not be read. Save it again by profiling the table." % table_id)
    if not (isinstance(doc, dict) and doc.get("format") == DOC_FORMAT and doc.get("table_id") == table_id and isinstance(doc.get("records"), list)):
        raise RecordLoadError("The saved record file for table %s is not in the expected format." % table_id)
    return doc


# ------------------------------------------------------------------ what each table and field became (IDs only)
def table_targets(items, state, env):
    """{table_id: {record_type, fields: [...], held: n}} for the Planning side of the plan, using the IDs the field load wrote to the state.
    A field is loadable when it is a ready ordinary field that Planning created; a link is returned with pass 'links'. Fields on hold or not created are counted in `held`."""
    options = {}
    for i in items:
        if i["system"] == "planning" and i["kind"] == "option" and i["status"] == "ready":
            key = state.get(i["temp_id"], env)
            if key:
                options.setdefault(i["parent"], {})[i["name"]] = key
    out = {}
    for i in items:
        if i["system"] != "planning" or i["kind"] != "record_type":
            continue
        rt = state.get(i["temp_id"], env)
        if rt:
            out[i["table_id"]] = {"record_type": rt, "name": i["name"], "fields": [], "held": 0}
    for i in items:
        if i["system"] != "planning" or i["kind"] != "field" or i["table_id"] not in out or i.get("source") == "migration":
            continue
        t, wid = out[i["table_id"]], state.get(i["temp_id"], env)
        k = tm.kind(i["data_type"])
        if i["status"] != "ready" or not wid or not i["field_id"] or k is None:
            t["held"] += 1
            continue
        spec = i.get("spec") or {}
        t["fields"].append({"field_id": i["field_id"], "planning_id": wid, "kind": k, "options": options.get(i["temp_id"], {}), "linked_table_id": spec.get("linked_table_id"),
                            "single": bool(spec.get("single")), "pass": i.get("pass") or "records"})
    for t in out.values():
        t["fields"].sort(key=lambda f: f["field_id"])
    return out


# ------------------------------------------------------------------ converting one value
def _empty(v):
    return v is None or v == "" or v == [] or v == {}


def raw_text(value):
    """The value as plain text exactly as Airtable gave it: a string stays as it is, anything else (a list, an object, a number, true or false) is its JSON text. Used by the
    Emergency mode PRE- fields, which keep the original value untouched."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def iso_timestamp(value, date_only):
    """Planning needs every date as yyyy-MM-ddTHH:mm:ss.SSSZ (UTC), even a date with no time (then midnight). A date field keeps its calendar day as written; a date and time is
    converted to UTC (a value with no zone is taken as UTC). None when the text is not a real date."""
    try:
        if date_only or len(value) == 10:
            d = datetime.datetime.strptime(value[:10], "%Y-%m-%d")
        else:
            v = value.strip().replace("Z", "+00:00").replace("z", "+00:00").replace(" ", "T", 1)
            d = datetime.datetime.fromisoformat(v)
            if d.tzinfo is not None:
                d = d.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (d.microsecond // 1000)


def convert(f, value):
    """(True, value for Planning), or (False, short reason with no value in it). An empty value is (True, None): it is simply left out."""
    if _empty(value):
        return True, None
    if f.get("raw"):                       # an Emergency mode PRE- text field: no conversion, no cleaning
        return True, raw_text(value)
    k = f["kind"]
    if k in ("text", "long-text", "rich-text"):
        if isinstance(value, (list, dict)):
            return False, "the value is not plain text"
        return True, str(value) if not isinstance(value, str) else value
    if k in ("number", "percentage", "currency"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, "the value is not a number"
        return True, value
    if k in ("date", "datetime"):
        if not (isinstance(value, str) and _DATE.match(value)):
            return False, "the value is not a date"
        stamp = iso_timestamp(value, k == "date")
        if stamp is None:
            return False, "the value is not a date"
        return True, stamp
    if k == "boolean":
        return True, bool(value)
    if k in ("single-select", "multi-select"):
        names = value if isinstance(value, list) else [value]
        keys = []
        for n in names:
            key = f["options"].get(n) if isinstance(n, str) else None
            if key is None:
                return False, "a select value has no matching option in Planning"
            keys.append(key)
        if k == "single-select":
            return True, keys[0] if len(keys) == 1 else None
        return True, keys
    return False, "this field type is not loaded"


def record_body(rt, fields, record, issues):
    """The create body for one Airtable record. `issues` collects (field_id, reason) pairs, never a value."""
    out = {}
    src = record.get("fields") if isinstance(record, dict) else None
    for f in fields:
        if f["pass"] != "records":
            continue
        if f.get("record_id"):                 # the primary field AirtableRecordId holds the record's own Airtable ID (an identifier, not a field value)
            if isinstance(record, dict) and record.get("id"):
                out[f["planning_id"]] = str(record["id"])
            continue
        ok, v = convert(f, (src or {}).get(f["field_id"]))
        if not ok:
            issues.append((f["field_id"], v))
        elif v is not None:
            out[f["planning_id"]] = v
    return {"recordTypeId": rt, "fields": out}


def link_values(f, record, uid_lookup, mapped, issues):
    """The Planning record IDs a link field must hold: ([ids], missing count). `mapped` is {surrogate ID: Planning record ID}."""
    src = ((record or {}).get("fields") or {}).get(f["field_id"])
    ids, missing = [], 0
    for rid in (src if isinstance(src, list) else []):
        uid = uid_lookup.get(rid) if isinstance(rid, str) else None
        pid = mapped.get(uid) if uid is not None else None
        if pid:
            ids.append(pid)
        else:
            missing += 1
    if missing:
        issues.append((f["field_id"], "%d linked record(s) have no Planning record yet" % missing))
    return sorted(set(ids)), missing


# ------------------------------------------------------------------ the passes
def _batches(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _mapped(conn, env, ws, kind="record"):
    """{surrogate ID: Planning record ID} for one environment and Workspace."""
    return {r[0]: r[1] for r in conn.execute("SELECT source_record_uid, target_record_id FROM target_mappings WHERE system='planning' AND environment=? AND workspace_key=? AND target_kind=? "
                                              "ORDER BY mapping_id", (env, ws, kind))}


def _pending_uids(conn, base_id, table_id, pass_name, env, ws):
    return rm.pending_records(conn, base_id, table_id, pass_name, env, "planning", ws)


def load_records(conn, base_id, tables, extract_dir, tr, env, ws, batch=BATCH_DEFAULT, dry_run=True, reader=read_extract, only=None, should_stop=None, progress=None):
    """The records pass. Returns {tables: [{table_id, name, pending, created, failed, problems}], created, failed, cancelled}. dry_run counts and sends nothing.
    only: the table IDs to load (default every table in `tables`). should_stop() is asked before each batch, so a stop never interrupts one; progress({table_id, name, pass,
    batch, batches, created, failed, pending}) is called after each batch. Both are for the web page's background job."""
    res = {"tables": [], "created": 0, "failed": 0, "cancelled": False}
    for tid in sorted(tables if only is None else [t for t in only if t in tables]):
        if res["cancelled"]:
            break
        t = tables[tid]
        row = {"table_id": tid, "name": t["name"], "pending": 0, "created": 0, "failed": 0, "problems": 0, "note": None, "failures": []}
        res["tables"].append(row)
        pend = _pending_uids(conn, base_id, tid, "records", env, ws)
        row["pending"] = len(pend)
        if not pend:
            continue
        if dry_run:
            continue
        try:
            doc = reader(extract_dir, base_id, tid)
        except RecordLoadError as e:
            row["note"] = str(e)
            continue
        by_id = {r.get("id"): r for r in doc["records"] if isinstance(r, dict)}
        parts = list(_batches(pend, max(1, int(batch))))
        for n, part in enumerate(parts, 1):
            if should_stop and should_stop():
                res["cancelled"] = True
                break
            issues, sendable = [], []
            for uid, rid in part:
                if rid not in by_id:
                    row["failed"] += 1
                    row["failures"].append((rid, "the record is not in the saved file"))
                    continue
                sendable.append((uid, rid, record_body(t["record_type"], t["fields"], by_id[rid], issues)))
            row["problems"] += len(issues)
            for uid_rid_body in _send_batch(tr, sendable, row):
                uid, new_id = uid_rid_body
                rm.add_target(conn, uid, "planning", env, ws, "record", new_id)
                rm.mark_pass(conn, [uid], "records", env, "planning", ws)
                row["created"] += 1
            if progress:
                progress({"table_id": tid, "name": t["name"], "pass": "records", "batch": n, "batches": len(parts), "created": row["created"], "failed": row["failed"], "pending": row["pending"]})
        res["created"] += row["created"]
        res["failed"] += row["failed"]
    return res


def _send_batch(tr, sendable, row):
    """Send a batch and yield (surrogate ID, new Planning record ID) for each record Planning created.

    The reply says, per record, whether it was created (matched by requestIndex inside the transport), so a partly failed batch (HTTP 207) keeps its successes and
    fails ONLY the failed records, each with a short scrubbed reason. A failed record is not sent again in the same run (its refusal is not going to change); it stays
    pending for the next run. Never resending a record that succeeded is what keeps a 207 from creating duplicates.
      * The whole call is refused or fails (an HTTP error): nothing is known to exist, so the batch is split and each record is sent alone, so one bad record does
        not hold up the others.
      * Planning answered but the answer cannot be matched (UnmatchedReply): the records MAY exist, so none is resent; all fail with that reason, to be checked.
    A reason never holds a record value."""
    if not sendable:
        return
    try:
        got = tr.create_records([b for _u, _r, b in sendable])
    except pt.UnmatchedReply as e:
        for _u, rid, _b in sendable:
            row["failed"] += 1
            row["failures"].append((rid, lr.scrub(e)))
        return
    except Exception as e:
        batch_error = lr.scrub(e)
        if len(sendable) == 1:
            row["failed"] += 1
            row["failures"].append((sendable[0][1], batch_error))
            return
        for item in sendable:
            for out in _send_batch(tr, [item], row):
                yield out
        return
    for (uid, rid, _b), g in zip(sendable, got):
        if isinstance(g, dict) and g.get("id") and not g.get("error"):
            yield uid, str(g["id"])
        else:
            row["failed"] += 1
            row["failures"].append((rid, lr.scrub((g or {}).get("error") if isinstance(g, dict) else None) or "Planning gave no result for this record"))


def load_links(conn, base_id, tables, extract_dir, tr, env, ws, batch=BATCH_DEFAULT, dry_run=True, reader=read_extract, only=None, should_stop=None, progress=None):
    """The links pass: set each record's link fields. A record is marked done only when every one of its links resolved.
    only: the table IDs to update (default every table in `tables`); `tables` still says which linked tables exist. should_stop() is asked every `batch` records and
    progress({table_id, name, pass, batch, batches, updated, waiting, failed, pending}) is called there, for the web page's background job."""
    res = {"tables": [], "updated": 0, "waiting": 0, "failed": 0, "cancelled": False}
    mapped = _mapped(conn, env, ws)
    for tid in sorted(tables if only is None else [t for t in only if t in tables]):
        if res["cancelled"]:
            break
        t = tables[tid]
        links = [f for f in t["fields"] if f["pass"] == "links" and f["linked_table_id"] in tables]
        row = {"table_id": tid, "name": t["name"], "pending": 0, "updated": 0, "waiting": 0, "failed": 0, "note": None, "failures": []}
        res["tables"].append(row)
        if not links:
            continue
        done_records = {r[0] for r in conn.execute("SELECT source_record_uid FROM record_pass_markers WHERE pass='records' AND environment=? AND workspace_key=?", (env, ws))}
        pend = [(u, r) for u, r in _pending_uids(conn, base_id, tid, "links", env, ws) if u in done_records]
        row["pending"] = len(pend)
        if not pend or dry_run:
            continue
        try:
            doc = reader(extract_dir, base_id, tid)
        except RecordLoadError as e:
            row["note"] = str(e)
            continue
        by_id = {r.get("id"): r for r in doc["records"] if isinstance(r, dict)}
        lookups = {}
        for f in links:
            if f["linked_table_id"] not in lookups:
                lookups[f["linked_table_id"]] = rm.uid_map(conn, base_id, f["linked_table_id"])
        size = max(1, int(batch))
        for n, (uid, rid) in enumerate(pend):
            if n % size == 0:
                if should_stop and should_stop():
                    res["cancelled"] = True
                    break
                if progress and n:
                    progress({"table_id": tid, "name": t["name"], "pass": "links", "batch": n // size, "batches": -(-len(pend) // size), "updated": row["updated"], "waiting": row["waiting"], "failed": row["failed"], "pending": row["pending"]})
            rec = by_id.get(rid)
            if rec is None:
                row["failed"] += 1
                row["failures"].append((rid, "the record is not in the saved file"))
                continue
            fields, gaps, issues = {}, 0, []
            for f in links:
                ids, missing = link_values(f, rec, lookups[f["linked_table_id"]], mapped, issues)
                gaps += missing
                if ids:
                    fields[f["planning_id"]] = ids
            if gaps:
                row["waiting"] += 1                       # a linked record is not loaded yet: this record is tried again on a later run
                continue
            try:
                if fields:
                    tr.update_record(mapped[uid], fields)
                rm.mark_pass(conn, [uid], "links", env, "planning", ws)
                row["updated"] += 1
            except Exception as e:
                row["failed"] += 1
                row["failures"].append((rid, lr.scrub(e)))
        res["updated"] += row["updated"]
        res["waiting"] += row["waiting"]
        res["failed"] += row["failed"]
    return res
