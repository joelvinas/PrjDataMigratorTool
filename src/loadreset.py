#!/usr/bin/env python3
"""Reset one table for a wipe and reload: delete its records in a Planning Workspace, then clear what this tool remembers about them. Standard library only.

Route and body (docs/WF_Planning_v2_API_simplified.yaml, from Adobe's v2 reference page):
    GET    /v2/record-types/{recordTypeId}/records?page=0&size=100   Page<Record>: content[], totalElements
    DELETE /v2/record-types/{recordTypeId}/records/bulk              body {"ids": [...]} (at most 100); 200 all deleted, 207 mixed: BulkResult results[] {requestIndex, id, status, error}
    GET    /v2/record-types/{id}                                     the record type (its primaryWorkspaceId is checked when present)
The record type and its fields stay. Nothing here deletes a record type, a field or a Workspace.

What is cleared locally, only for the table, the stage and the Workspace (and only once Workfront shows the record type empty):
    target_mappings, record_pass_markers   (the record registry's per-stage state)
Never touched: load_id_map (the record type and fields stay created), source_records (the same on every stage: the surrogate IDs stay stable), Core rows, attachment_map.

Rules: a dry run sends nothing at all; the Workspace ID in config.json must be the one this stage was loaded into; a typed phrase is needed to send; only counts are
reported; a record's values come back in the listing and are dropped at once (only the IDs are read).
"""
import re

import loadrun as lr
import loadstate as ls
import recordmap as rm

PAGE = 100
MAX_ROUNDS = 400            # 25,000 records per record type at 100 per call is 250 rounds; this stops a loop that makes no progress
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ResetError(Exception):
    pass


def phrase(stage):
    return "DELETE RECORDS IN %s" % str(stage).upper()


def target(conn, base_id, table_id, stage, ws):
    """(table name, Planning record type ID) for the table in this stage and Workspace. Raises ResetError with a plain reason."""
    if not _ID.match(table_id or ""):
        raise ResetError("The table ID is not a plain Airtable ID.")
    if not rm.available(conn) or not ls.available(conn):
        raise ResetError("This database does not have the loader's state tables in their current form. Apply the migrations first.")
    row = conn.execute("SELECT table_name FROM airtable_tables WHERE base_id=? AND table_id=?", (base_id, table_id)).fetchone()
    if row is None:
        raise ResetError("That table is not in the database for this base.")
    temp = "P-T-" + table_id
    rt = ls.DbState(conn, base_id, ws).get(temp, stage)
    if not rt:
        other = conn.execute("SELECT 1 FROM load_id_map WHERE temp_id=? AND environment=? AND status='created' AND workspace_key!=?", (temp, stage, ws)).fetchone()
        raise ResetError(("The %s stage was loaded into a different Workspace than the workspace_id in config.json. Nothing was changed." % stage.upper()) if other else
                         "No Planning record type is recorded for this table in the %s stage and this Workspace, so there is nothing to reset." % stage.upper())
    return row[0], rt


def local_counts(conn, base_id, table_id, stage, ws):
    """Counts only: the new Planning records this tool mapped for the table, and the pass markers, in this stage and Workspace."""
    uids = "SELECT source_record_uid FROM source_records WHERE base_id=? AND table_id=?"
    mapped = conn.execute("SELECT COUNT(*) FROM target_mappings WHERE system='planning' AND environment=? AND workspace_key=? AND source_record_uid IN (%s)" % uids, (stage, ws, base_id, table_id)).fetchone()[0]
    marks = {p: conn.execute("SELECT COUNT(*) FROM record_pass_markers WHERE pass=? AND environment=? AND workspace_key=? AND source_record_uid IN (%s)" % uids, (p, stage, ws, base_id, table_id)).fetchone()[0]
             for p in rm.PASSES}
    return {"mapped": mapped, "markers": {p: n for p, n in marks.items() if n}}


def clear_local(conn, base_id, table_id, stage, ws):
    """Delete the table's rows from target_mappings and record_pass_markers for this stage and Workspace, in one transaction. Returns the counts removed."""
    uids = "SELECT source_record_uid FROM source_records WHERE base_id=? AND table_id=?"
    with conn:
        m = conn.execute("DELETE FROM target_mappings WHERE system='planning' AND environment=? AND workspace_key=? AND source_record_uid IN (%s)" % uids, (stage, ws, base_id, table_id)).rowcount
        k = conn.execute("DELETE FROM record_pass_markers WHERE environment=? AND workspace_key=? AND workspace_key!='' AND source_record_uid IN (%s)" % uids, (stage, ws, base_id, table_id)).rowcount
    return {"mappings": m, "markers": k}


def wipe(tr, record_type_id, workspace_id):
    """Delete every record of the record type, 100 per call. Returns {deleted, failed, reasons, left, rounds}. `left` is what Workfront still lists at the end (None if unknown).
    Refuses when the record type says it belongs to a different Workspace."""
    rt = tr.get_record_type(record_type_id)
    home = rt.get("primaryWorkspaceId") if isinstance(rt, dict) else None
    if home and str(home) != str(workspace_id):
        raise ResetError("The record type belongs to a different Workspace than the workspace_id in config.json. Nothing was deleted.")
    out = {"deleted": 0, "failed": 0, "reasons": {}, "left": None, "rounds": 0}
    for _ in range(MAX_ROUNDS):
        ids, total = tr.list_record_ids(record_type_id, PAGE)
        out["left"] = total
        if not ids:
            return out
        out["rounds"] += 1
        got = tr.delete_records(record_type_id, ids)
        done = sum(1 for g in got if g.get("deleted"))
        out["deleted"] += done
        for g in got:
            if not g.get("deleted"):
                out["failed"] += 1
                out["reasons"][g.get("error") or "no reason"] = out["reasons"].get(g.get("error") or "no reason", 0) + 1
        if not done:
            return out                                  # nothing was removed this round: a repeat would only repeat the refusal
    out["left"] = None                                  # the round limit was reached, so what is left is not known
    return out
