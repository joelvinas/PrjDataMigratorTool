#!/usr/bin/env python3
"""Reset ONE table's Planning structure so it can be built again from scratch: delete its record type in Workfront, then forget the IDs this tool holds. Used by the Git-R-Done page
(high-speed create, check, reset and try again), which is not gated by the Load a table steps; it writes the same state (load_id_map and the record registry) as Load a table, so both pages' tables can be reset. Standard library only.

The two halves, kept apart on purpose:
    attempt   DELETE /v2/record-types/{id} (the record type, its fields and its records go with it, per Adobe). Never raises for a refusal: the reason comes back.
    forget    delete this tool's MAP of the table (load_id_map rows, the record IDs, the pass markers, the option IDs read back) for the stage and Workspace, in ONE transaction.
The map is cleared automatically only when Workfront confirms the record type is gone (deleted, or already absent). When Workfront refuses, nothing local is changed and the
person is told why; they may then choose to forget the map anyway (`forget`), for example after deleting the record type by hand. If the record type still exists in
Workfront at that point, the next load reports a name clash instead of adopting it: this tool no longer remembers having tried.

What is NOT lost, so the table can be rebuilt: the Airtable schema and profiling, approved names and data types, link directions, scope, consolidation groups, field writer
decisions, descriptions, the registry of Airtable record IDs (source_records: the same surrogate IDs on every stage) and attachment_map. Only the Workfront-side facts go.
Other tables, Core rows and other stages or Workspaces are never touched. Holds IDs, statuses and counts only: no record value.
"""
import re

import loadstate as ls

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class TableResetError(Exception):
    pass


def _have(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def check(conn, base_id, table_id):
    """The table's name. Raises TableResetError with a plain reason."""
    if not _ID.match(table_id or ""):
        raise TableResetError("The table ID is not a plain Airtable ID.")
    if not ls.available(conn):
        raise TableResetError("This database does not have the loader's state tables in their current form. Apply the migrations first.")
    row = conn.execute("SELECT table_name FROM airtable_tables WHERE base_id=? AND table_id=?", (base_id, table_id)).fetchone()
    if row is None:
        raise TableResetError("That table is not in the database for this base.")
    return row[0]


_UIDS = "SELECT source_record_uid FROM source_records WHERE base_id=? AND table_id=?"
_FIELDS = "SELECT field_id FROM airtable_fields WHERE base_id=? AND table_id=?"


def counts(conn, base_id, table_id, stage, ws):
    """What a reset would forget, as counts only: {"map": [{kind, status, count}], "map_total", "mapped_records", "markers", "option_ids", "record_type"}. Sorted."""
    rows = [{"kind": r[0], "status": r[1], "count": r[2]} for r in conn.execute(
        "SELECT kind, status, COUNT(*) FROM load_id_map WHERE system='planning' AND environment=? AND workspace_key=? AND base_id=? AND table_id=? GROUP BY 1,2 ORDER BY 1,2", (stage, ws, base_id, table_id))]
    mapped = conn.execute("SELECT COUNT(*) FROM target_mappings WHERE system='planning' AND environment=? AND workspace_key=? AND source_record_uid IN (%s)" % _UIDS, (stage, ws, base_id, table_id)).fetchone()[0]
    marks = conn.execute("SELECT COUNT(*) FROM record_pass_markers WHERE environment=? AND workspace_key=? AND workspace_key!='' AND source_record_uid IN (%s)" % _UIDS, (stage, ws, base_id, table_id)).fetchone()[0]
    opts = 0
    if _have(conn, "option_workfront_ids"):
        opts = conn.execute("SELECT COUNT(*) FROM option_workfront_ids WHERE system='planning' AND environment=? AND workspace_id=? AND airtable_field_id IN (%s)" % _FIELDS, (stage, ws, base_id, table_id)).fetchone()[0]
    return {"map": rows, "map_total": sum(r["count"] for r in rows), "mapped_records": mapped, "markers": marks, "option_ids": opts,
            "record_type": ls.DbState(conn, base_id, ws).get("P-T-" + table_id, stage)}


def inbound_links(conn, base_id, table_id, stage, ws):
    """Link fields of OTHER tables that point at this table and are recorded as created in this stage and Workspace: [{table_id, name, fields}], sorted. Deleting the record type
    may be refused because of them, or leave them pointing nowhere; this tool does not touch them."""
    return [{"table_id": r[0], "name": r[1] or r[0], "fields": r[2]} for r in conn.execute(
        "SELECT f.table_id, t.table_name, COUNT(*) FROM airtable_fields f JOIN load_id_map m ON m.airtable_field_id=f.field_id AND m.system='planning' AND m.kind='field' AND m.status='created' "
        "AND m.environment=? AND m.workspace_key=? AND m.base_id=? LEFT JOIN airtable_tables t ON t.table_id=f.table_id AND t.base_id=f.base_id "
        "WHERE f.base_id=? AND f.linked_table_id=? AND f.table_id!=? GROUP BY f.table_id, t.table_name ORDER BY f.table_id", (stage, ws, base_id, base_id, table_id, table_id))]


def forget(conn, base_id, table_id, stage, ws):
    """Delete this tool's map of the table for the stage and Workspace, in ONE transaction (all or nothing). Returns the number removed per table."""
    if not ws:
        raise TableResetError("A Workspace ID is needed: a blank one would also match Core rows.")
    with conn:
        o = 0
        if _have(conn, "option_workfront_ids"):
            o = conn.execute("DELETE FROM option_workfront_ids WHERE system='planning' AND environment=? AND workspace_id=? AND airtable_field_id IN (%s)" % _FIELDS, (stage, ws, base_id, table_id)).rowcount
        k = conn.execute("DELETE FROM record_pass_markers WHERE environment=? AND workspace_key=? AND workspace_key!='' AND source_record_uid IN (%s)" % _UIDS, (stage, ws, base_id, table_id)).rowcount
        m = conn.execute("DELETE FROM target_mappings WHERE system='planning' AND environment=? AND workspace_key=? AND source_record_uid IN (%s)" % _UIDS, (stage, ws, base_id, table_id)).rowcount
        i = conn.execute("DELETE FROM load_id_map WHERE system='planning' AND environment=? AND workspace_key=? AND base_id=? AND table_id=?", (stage, ws, base_id, table_id)).rowcount
    return {"load_id_map": i, "mappings": m, "markers": k, "option_ids": o}


def reset(conn, base_id, table_id, stage, ws, tr, send):
    """A dry run reads this tool's own counts and sends nothing. With send=True: attempt the record type delete; forget the map only when Workfront confirms it is gone.
    Returns {name, before, inbound, attempt, cleared, forgotten_without_attempt}. `attempt` is None when no record type ID is recorded (nothing to delete in Workfront; the map
    is then forgotten at once) and otherwise {"deleted", "already_gone"?, "status"?, "reason"?}. Raises TableResetError with a plain reason."""
    name = check(conn, base_id, table_id)
    before = counts(conn, base_id, table_id, stage, ws)
    if not before["map_total"] and not before["mapped_records"] and not before["markers"] and not before["option_ids"]:
        raise TableResetError("This tool holds nothing for this table in the %s stage and this Workspace, so there is nothing to reset." % stage.upper())
    out = {"name": name, "before": before, "inbound": inbound_links(conn, base_id, table_id, stage, ws), "attempt": None, "cleared": None}
    if not send:
        return out
    if before["record_type"]:
        out["attempt"] = tr.delete_record_type(before["record_type"], ws)
        if not out["attempt"]["deleted"]:
            return out                                  # refused: the map is kept, and the person decides whether to forget it
    out["cleared"] = forget(conn, base_id, table_id, stage, ws)
    return out


def forget_only(conn, base_id, table_id, stage, ws):
    """The person's choice after a refused delete. Forgets the map and sends nothing. Returns {name, before, cleared}."""
    name = check(conn, base_id, table_id)
    before = counts(conn, base_id, table_id, stage, ws)
    return {"name": name, "before": before, "cleared": forget(conn, base_id, table_id, stage, ws)}
