#!/usr/bin/env python3
"""Forget every Planning reference this tool holds for one stage and Workspace, so the structure can be created again. Standard library only.

Use it when record types were deleted in Workfront by hand (or the Workspace changed) while the database still holds their IDs: the runner would otherwise keep reusing IDs that
no longer exist ("can't find the record type").

Removed, for the given stage and Workspace only:
    load_id_map          rows with system='planning'   (record types, fields, select options)
    target_mappings      rows with system='planning'   (the new Planning record IDs)
    record_pass_markers  rows for the stage and Workspace key (a Planning marker always has a Workspace key; Core's is '')
Never touched: Core rows, source_records, the Airtable schema tables, profiling statistics, every recorded decision table, attachment_map.

A dry run (the default) changes nothing and reports counts only, by table and kind. A real run needs the typed phrase, takes a backup copy of the database first and deletes
in ONE transaction (all or nothing). Nothing is ever deleted in Workfront: `still_in_workfront()` only READS the Workspace's record types and says which of the IDs this tool
created still exist, so a person can remove them by hand.
"""
import os
import sqlite3
import time

import loadrun as lr

PLANNING_STAGES = lr.PLANNING_STAGES


class PlanningResetError(Exception):
    pass


def phrase(stage):
    return "RESET PLANNING IN %s" % str(stage).upper()


def check_target(stage, workspace_id):
    stage = str(stage or "").strip().lower()
    if stage not in PLANNING_STAGES:
        raise PlanningResetError("The stage must be one of: %s." % ", ".join(PLANNING_STAGES))
    ws = str(workspace_id or "").strip()
    if not ws:
        raise PlanningResetError("A Workspace ID is needed: a blank one would also match Core rows.")
    return stage, ws


def available(conn):
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {"load_id_map", "target_mappings", "record_pass_markers", "source_records"} <= have and "workspace_key" in {r[1] for r in conn.execute("PRAGMA table_info(load_id_map)")}


def counts(conn, stage, workspace_id):
    """What a reset would remove, as counts only. {"load_id_map": [{table_id, kind, status, count}], "target_mappings": [{table_id, target_kind, count}],
    "record_pass_markers": [{table_id, pass, count}], "totals": {...}}. Sorted, so the same database gives the same report."""
    stage, ws = check_target(stage, workspace_id)
    if not available(conn):
        raise PlanningResetError("This database does not have the loader's state tables in their current form. Apply the migrations first.")
    idmap = [{"table_id": r[0] or "", "kind": r[1], "status": r[2], "count": r[3]} for r in conn.execute(
        "SELECT COALESCE(table_id,''), kind, status, COUNT(*) FROM load_id_map WHERE system='planning' AND environment=? AND workspace_key=? GROUP BY 1,2,3 ORDER BY 1,2,3", (stage, ws))]
    maps = [{"table_id": r[0] or "", "target_kind": r[1], "count": r[2]} for r in conn.execute(
        "SELECT COALESCE(s.table_id,''), t.target_kind, COUNT(*) FROM target_mappings t LEFT JOIN source_records s ON s.source_record_uid=t.source_record_uid "
        "WHERE t.system='planning' AND t.environment=? AND t.workspace_key=? GROUP BY 1,2 ORDER BY 1,2", (stage, ws))]
    marks = [{"table_id": r[0] or "", "pass": r[1], "count": r[2]} for r in conn.execute(
        "SELECT COALESCE(s.table_id,''), m.pass, COUNT(*) FROM record_pass_markers m LEFT JOIN source_records s ON s.source_record_uid=m.source_record_uid "
        "WHERE m.environment=? AND m.workspace_key=? AND m.workspace_key!='' GROUP BY 1,2 ORDER BY 1,2", (stage, ws))]
    return {"stage": stage, "workspace_id": ws, "load_id_map": idmap, "target_mappings": maps, "record_pass_markers": marks,
            "totals": {"load_id_map": sum(x["count"] for x in idmap), "target_mappings": sum(x["count"] for x in maps), "record_pass_markers": sum(x["count"] for x in marks)}}


def record_type_ids(conn, stage, workspace_id):
    """{record type ID: (table_id, name)} this tool created (status created) in the stage and Workspace. Read BEFORE a reset, which removes the rows."""
    stage, ws = check_target(stage, workspace_id)
    return {str(r[0]): (r[1] or "", r[2]) for r in conn.execute(
        "SELECT workfront_id, table_id, name FROM load_id_map WHERE system='planning' AND kind='record_type' AND status='created' AND workfront_id IS NOT NULL AND environment=? AND workspace_key=? "
        "ORDER BY workfront_id", (stage, ws))}


def still_in_workfront(tr, ids):
    """READ ONLY. Lists the Workspace's record types and splits the IDs this tool created into those that still exist and those that are gone. Deletes nothing.
    Returns {"existing": [{id, table_id, name, displayName}], "gone": [{id, table_id, name}], "unrecorded_in_workspace": N}. Raises HttpError when the read fails."""
    live = {}
    for row in tr._pages("/v2/workspaces/%s/record-types" % tr.workspace_id):
        if isinstance(row, dict) and row.get("id"):
            live[str(row["id"])] = row.get("displayName")
    existing = [{"id": i, "table_id": ids[i][0], "name": ids[i][1], "displayName": live[i]} for i in sorted(ids) if i in live]
    gone = [{"id": i, "table_id": ids[i][0], "name": ids[i][1]} for i in sorted(ids) if i not in live]
    return {"existing": existing, "gone": gone, "unrecorded_in_workspace": len([i for i in live if i not in ids])}


def backup(conn, directory):
    """A consistent copy of the database (sqlite's online backup) in `directory`. Returns the path."""
    os.makedirs(directory, exist_ok=True)
    stamp, n = time.strftime("%Y%m%d_%H%M%S"), 0
    path = os.path.join(directory, "before_planning_reset_%s.db" % stamp)
    while os.path.exists(path):
        n += 1
        path = os.path.join(directory, "before_planning_reset_%s_%d.db" % (stamp, n))
    dest = sqlite3.connect(path)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    return path


def clear(conn, stage, workspace_id):
    """Delete the rows in ONE transaction (all or nothing). Returns the number removed per table."""
    stage, ws = check_target(stage, workspace_id)
    if not available(conn):
        raise PlanningResetError("This database does not have the loader's state tables in their current form. Apply the migrations first.")
    with conn:
        m = conn.execute("DELETE FROM record_pass_markers WHERE environment=? AND workspace_key=? AND workspace_key!=''", (stage, ws)).rowcount
        t = conn.execute("DELETE FROM target_mappings WHERE system='planning' AND environment=? AND workspace_key=?", (stage, ws)).rowcount
        i = conn.execute("DELETE FROM load_id_map WHERE system='planning' AND environment=? AND workspace_key=?", (stage, ws)).rowcount
    return {"load_id_map": i, "target_mappings": t, "record_pass_markers": m}


def report_lines(c, listing=None, listing_note=None):
    """Plain text lines for a counts() result (and the read-only Workfront listing when one was made)."""
    L = []
    for key, label, cols in (("load_id_map", "Created-item IDs (load_id_map)", ("table_id", "kind", "status")), ("target_mappings", "Planning record IDs (target_mappings)", ("table_id", "target_kind")),
                             ("record_pass_markers", "Pass markers (record_pass_markers)", ("table_id", "pass"))):
        L.append("%s: %d" % (label, c["totals"][key]))
        for r in c[key]:
            L.append("    %-22s %s  x %d" % (r["table_id"] or "(none)", " / ".join(str(r[k]) for k in cols[1:]), r["count"]))
    if listing is not None:
        L += ["", "Record types this tool created that STILL EXIST in the Workspace (nothing is deleted in Workfront; remove them by hand if you want): %d" % len(listing["existing"])]
        L += ["    %s  %s" % (r["id"], r["displayName"]) for r in listing["existing"]]
        L += ["Recorded here but no longer in the Workspace (already deleted): %d" % len(listing["gone"])]
        L += ["    %s  %s" % (r["id"], r["name"]) for r in listing["gone"]]
    elif listing_note:
        L += ["", listing_note]
    return L
