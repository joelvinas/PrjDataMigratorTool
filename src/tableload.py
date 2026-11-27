#!/usr/bin/env python3
"""Load ONE table into Workfront Planning, step by step: the code the "Load a table" page and the command line share. Standard library only.

Nothing here talks HTTP itself: every call to Workfront goes through the existing transport (planningtransport.py over workfronthttp.py), and every decision is made by the
modules the command line already uses (loadplan, loadrun, loadstate, recordload, recordmap, loadreset). This module only orders them, reads the state they keep and returns
plain dicts, so the command line (`loader.py`) and the web page (`server.py`) behave the same.

The steps, each possible only when the one before is done (the state is read from the database, so a restart picks up where it stopped):
    1 check       the plan for the table: ready, hold and blocked counts with reasons, and the linked tables it depends on
    2 structure   the record type, fields and select options (dry run, then send), through load_id_map
    3 records     the records pass (the saved record file is read in memory; counts and record IDs only)
    4 links       the links pass
    5 verify      the record counts: saved file, registry, records done, Workfront's own count
    6 reset       wipe this table's records so it can be reloaded (a dry run, then a typed phrase)

Rules kept here (CLAUDE.md): no record value, no token and no file name leaves this module; a result holds table, field and record IDs, names of schema objects, counts,
statuses and reasons already scrubbed by loadrun.scrub(). A function never sends anything unless it is given send=True (a dry run reads the database and the saved file only).
"""
import os

import fieldnames_store as store
import linkdirections as ld
import loadconfig as lc
import loadplan as lp
import loadreset as rs
import loadrun as lr
import loadstate as ls
import planningtransport as pt
import recordload as rl
import recordmap as rm
import workfronthttp as wh

STEPS = ("structure", "records", "links", "reset")
FAILURE_LIMIT = 200                  # failed records listed in one result (the full count is always given)


class LoadError(Exception):
    """A plain-language problem with the request or the state. The message never holds a secret or a record value."""
    def __init__(self, message, status=400):
        super(LoadError, self).__init__(message)
        self.message, self.status = message, status


class Ctx(object):
    """The Planning settings for one run: credentials (secret hidden), Workspace, stage, the environment the runner uses and every secret value (for redaction)."""
    def __init__(self, data, creds, stage, env):
        self.data, self.creds, self.stage, self.env = data, creds, stage, env
        self.ws = creds.workspace_id
        self.secrets = lc.secret_values(data)


def planning_context(config_path, stage, send, confirm=None):
    """The settings for one Planning stage. The stages are the ones the user defined in config.json (workfront.planning, one block each); a real send to `production` also needs
    the exact confirmation phrase (`confirm`)."""
    try:
        data = lc.read_config(config_path)
    except lc.ConfigError as e:
        raise LoadError(str(e))
    stage = lc.planning_stage(stage)                  # "test" and "prod" in config.json are stored as uat and production
    defined = list(lc.planning_blocks(data))
    if stage in dict(lc._planning_keys(data)) and stage not in defined:
        raise LoadError(" ".join(p for p in lc.planning_problems(data) if " %s twice " % stage in p))
    if defined and stage not in defined:
        raise LoadError("The stage %s is not defined in %s (section workfront.planning). Defined stages: %s." % (stage or "(none)", config_path, ", ".join(defined)))
    creds = lc.credentials(data, "planning", stage)
    if creds is None or not creds.workspace_id:
        raise LoadError("Planning is not configured in %s (a Bearer token and a workspace_id are needed). Run 'config' to see what is set." % config_path)
    try:
        env = lr.resolve_environment(stage, confirm, dry_run=not send, workspace_id=creds.workspace_id)
    except ValueError as e:
        raise LoadError(str(e))
    if env.name not in lr.PLANNING_STAGES:
        raise LoadError("The Planning stage must be one of: %s." % ", ".join(lr.PLANNING_STAGES))
    return Ctx(data, creds, stage, env)


def workspace_status(ctx, tr):
    """Prove the stage's token and Workspace ID with one read of the Workspace. Returns {ok, name, workspace_id, problem, fix}; `fix` says which config.json value to change:
    'token' (refused: tokens last 24 hours), 'workspace_id' (not found) or 'base_url' (cannot be reached). Never raises, never echoes a secret."""
    out = {"ok": False, "name": None, "workspace_id": ctx.ws, "stage": ctx.stage, "problem": None, "fix": None}
    try:
        w = tr.get_workspace(ctx.ws)
    except wh.HttpError as e:
        st = e.status
        if st in (401, 403):
            out.update(fix="token", problem="Workfront refused the bearer token for stage %s. Tokens last 24 hours: paste a new bearer_token for this stage into config.json." % ctx.stage)
        elif st == 404:
            out.update(fix="workspace_id", problem="Workfront has no Workspace with the workspace_id for stage %s. Correct workspace_id in config.json." % ctx.stage)
        elif st is None:
            out.update(fix="base_url", problem="Could not reach Workfront for stage %s. Check base_url in config.json and your connection." % ctx.stage)
        else:
            out.update(fix="token", problem="Workfront answered the Workspace check for stage %s with an error (HTTP %s). Check bearer_token and workspace_id in config.json." % (ctx.stage, st))
        return out
    except Exception as e:
        out.update(fix="base_url", problem="The Workspace check for stage %s failed: %s" % (ctx.stage, lr.scrub(e)))
        return out
    name = w.get("name") or w.get("displayName")
    out.update(ok=True, name=str(name) if name else "(no name returned)")
    return out


def sharing_for(data, stage):
    """The stage's share_with and section_name from config.json, or None when neither is set. A half-set or malformed setting is a plain LoadError."""
    try:
        return lc.planning_sharing(data, stage)
    except lc.ConfigError as e:
        raise LoadError(str(e))


def transport(ctx, opener=None):
    """The Planning transport for the stage. With share_with and section_name set in config.json, every record type it creates is shared and placed in the sidebar."""
    sharing = sharing_for(ctx.data, ctx.stage)
    try:
        return pt.PlanningTransport(wh.Http(ctx.creds, ctx.secrets, opener=opener), sharing)
    except ValueError as e:
        raise LoadError(str(e))


def clean(ctx, text):
    return lc.redact(text, ctx.secrets)


def need_tables(conn):
    if not (ls.available(conn) and rm.available(conn)):
        raise LoadError("This database does not have the loader's state tables in their current form. Apply the migrations (python sql/apply_migrations.py, after a backup) first.", 409)


def build(conn, base_id, only=None, include_link_fields=True):
    """(inputs, plan) for the base, limited to the chosen tables when `only` is given (a run filter; scope is never changed). A table that is not in the base or is out of
    scope is refused. This is the one place the command line and the page build the plan."""
    try:
        inputs = store.loader_inputs(conn, base_id)
    except store.StoreError as e:
        raise LoadError(e.message, e.status)
    inputs["link_state"] = ld.field_states(conn)
    chosen = sorted(set(only or [])) or None
    if chosen:
        bad = lp.unknown_tables(inputs["review"], chosen)
        if bad:
            raise LoadError("Not a table in this plan (wrong ID, or the table is out of scope): %s." % ", ".join(bad), 404)
    plan = lp.build_plan(inputs["review"], inputs["choices"], include_link_fields=include_link_fields, link_state=inputs.get("link_state"),
                         record_counts=inputs.get("record_counts"), field_facts=inputs.get("field_facts"), only_tables=chosen)
    return inputs, plan


def run_structure(conn, base_id, items, system, tr, env, ws, dry_run, should_stop=None, log=None):
    """Create (or, in a dry run, describe) the plan's items of one system. State is kept in load_id_map. Returns (runner result, temp IDs an earlier run began and never finished)."""
    state = ls.DbState(conn, base_id, ws if system == "planning" else None)
    stuck = state.pending(env.name)
    res = lr.run_plan([i for i in items if i["system"] == system], state, {system: tr}, env, dry_run=dry_run, log=log, should_stop=should_stop)
    if system == "planning" and not dry_run and hasattr(tr, "ensure_shared"):
        for r in res["results"]:                            # a record type that already existed (reused or adopted) is checked too: shared, and in the sidebar
            if r["kind"] == "record_type" and r["outcome"] in ("already_created", "adopted") and r["detail"]:
                tr.ensure_shared(str(r["detail"]))
    if hasattr(tr, "share_results"):
        res["sharing"] = list(tr.share_results)
    return res, stuck


def register_tables(conn, base_id, tables, extract_dir):
    """Register the record IDs (and nothing else) of the saved file of each table. Returns {table_id: plain note} for a table that could not be read."""
    notes = {}
    for tid in sorted(tables):
        try:
            doc = rl.read_extract(extract_dir, base_id, tid)
            rm.register(conn, base_id, tid, [r.get("id") for r in doc["records"] if isinstance(r, dict)])
        except (rl.RecordLoadError, rm.RecordMapError) as e:
            notes[tid] = str(e)
    return notes


def run_passes(conn, base_id, tables, extract_dir, tr, env, ws, passes, batch, dry_run, only=None, should_stop=None, progress=None):
    """Run the record passes in order. Returns [(pass name, result)]; stops after a pass that was cancelled."""
    out = []
    for p in passes:
        fn = rl.load_records if p == "records" else rl.load_links
        res = fn(conn, base_id, tables, extract_dir, tr, env, ws, batch=batch, dry_run=dry_run, only=only, should_stop=should_stop, progress=progress)
        out.append((p, res))
        if res.get("cancelled"):
            break
    return out


def reset_table(conn, base_id, table_id, stage, ws, tr, send, should_stop=None, progress=None):
    """The reset of one table: its local counts, and with send=True the wipe in Workfront and, once the record type lists empty, the local memory cleared. Raises
    loadreset.ResetError with a plain reason. Returns {name, record_type, before, wipe, cleared}."""
    name, rt = rs.target(conn, base_id, table_id, stage, ws)
    out = {"name": name, "record_type": rt, "before": rs.local_counts(conn, base_id, table_id, stage, ws), "wipe": None, "cleared": None}
    if send:
        out["wipe"] = rs.wipe(tr, rt, ws, should_stop=should_stop, progress=progress)
        if out["wipe"]["left"] == 0:
            out["cleared"] = rs.clear_local(conn, base_id, table_id, stage, ws)
    return out


# ------------------------------------------------------------------ what the page shows
def table_choices(conn, base_id):
    """The in-scope tables for the picker: ID, names, scope, target and whether the table name is approved. Sorted by name, then ID."""
    try:
        rv = store.review(conn, base_id)
    except store.StoreError as e:
        raise LoadError(e.message, e.status)
    rows = [{"table_id": t["table_id"], "name": t["effective"] if t["approved"] else t["old"], "airtable_name": t["old"], "scope": t["scope"], "target": t.get("target") or "",
             "name_approved": bool(t["approved"])} for t in rv["tables"] if t["scope"] != "out"]
    return sorted(rows, key=lambda r: (r["name"].lower(), r["table_id"]))


def _marked(conn, base_id, table_id, pass_name, stage, ws):
    """The Airtable record IDs of the table that have the pass marker in this stage and Workspace."""
    return {r[0] for r in conn.execute("SELECT s.record_id FROM source_records s JOIN record_pass_markers m ON m.source_record_uid=s.source_record_uid "
                                       "WHERE s.base_id=? AND s.table_id=? AND m.pass=? AND m.environment=? AND m.workspace_key=?", (base_id, table_id, pass_name, stage, ws))}


def _registered(conn, base_id, table_id):
    return conn.execute("SELECT COUNT(*) FROM source_records WHERE base_id=? AND table_id=?", (base_id, table_id)).fetchone()[0]


def saved_file(extract_dir, base_id, table_id):
    """Read-only check of the saved record file: {exists, records, ids (a set, in memory only), note}. Counts only are ever shown."""
    try:
        doc = rl.read_extract(extract_dir, base_id, table_id)
    except rl.RecordLoadError as e:
        return {"exists": False, "records": None, "ids": set(), "note": str(e)}
    ids = {r.get("id") for r in doc["records"] if isinstance(r, dict) and isinstance(r.get("id"), str)}
    return {"exists": True, "records": len(doc["records"]), "ids": ids, "note": None, "captured_at": doc.get("captured_at") if isinstance(doc.get("captured_at"), str) else None}


def _own(plan, table_id):
    """The Planning items that belong to the table itself (a dependency record type, created empty only so a link field can be built, is not the table's own)."""
    return [i for i in plan["items"] if i["system"] == "planning" and i["table_id"] == table_id and not i.get("dependency")]


def _reasons(items):
    seen = {}
    for i in items:
        if i["status"] == "ready":
            continue
        for r in i["reasons"]:
            e = seen.setdefault((i["status"], r), {"status": i["status"], "reason": r, "count": 0, "examples": []})
            e["count"] += 1
            if len(e["examples"]) < 5 and i["name"] not in e["examples"]:
                e["examples"].append(i["name"])
    return sorted(seen.values(), key=lambda e: (0 if e["status"] == "blocked" else 1, -e["count"], e["reason"]))


def structure_state(conn, items, state, stage):
    """Counts of the table's ready Planning items by what the state says, and whether the structure is finished (every ready item has its Workfront ID)."""
    ready = [i for i in items if i["status"] == "ready"]
    created = failed = pending = 0
    for i in ready:
        if state.get(i["temp_id"], stage):
            created += 1
        else:
            st = state.status(i["temp_id"], stage)
            failed += 1 if st == "failed" else 0
            pending += 1 if st == "pending" else 0
    rt = [i for i in items if i["kind"] == "record_type"]
    return {"ready": len(ready), "created": created, "failed": failed, "unfinished": pending, "not_started": len(ready) - created - failed - pending,
            "record_type": state.get(rt[0]["temp_id"], stage) if rt else None, "done": bool(ready) and created == len(ready)}


def link_dependencies(conn, base_id, items, names, state, stage, ws, in_plan):
    """The tables this table's link fields point to, and how far each one is (IDs and counts only). Sorted by table ID."""
    by = {}
    for i in items:
        if i["kind"] != "field":
            continue
        spec = i.get("spec") or {}
        if spec.get("airtable_type") != "multipleRecordLinks" or not spec.get("linked_table_id"):
            continue
        d = by.setdefault(spec["linked_table_id"], {"table_id": spec["linked_table_id"], "link_fields": 0, "ready_links": 0})
        d["link_fields"] += 1
        d["ready_links"] += 1 if i["status"] == "ready" else 0
    out = []
    for tid in sorted(by):
        d = by[tid]
        d["name"] = names.get(tid, tid)
        d["in_plan"] = tid in in_plan
        d["record_type"] = state.get("P-T-" + tid, stage) if tid in in_plan else None
        d["registered"] = _registered(conn, base_id, tid)
        d["records_done"] = len(_marked(conn, base_id, tid, "records", stage, ws))
        d["loaded"] = bool(d["registered"]) and d["records_done"] >= d["registered"]
        out.append(d)
    return out


def check_table(conn, base_id, table_id, ctx, extract_dir):
    """Step 1 and the state of every later step for one table. Read-only. The step gates come from here, so the page and the server use the same answer:
        can_structure   nothing in the table's own plan is blocked
        can_records     the structure is finished        can_links, can_verify  the records pass is finished        can_reset  the record type exists"""
    need_tables(conn)
    inputs, plan = build(conn, base_id, [table_id])
    rv = inputs["review"]
    own = _own(plan, table_id)
    state = ls.DbState(conn, base_id, ctx.ws)
    stage = ctx.stage
    names = {t["table_id"]: t["old"] for t in rv["tables"]}
    in_plan = {t["table_id"] for t in rv["tables"] if t["scope"] != "out"}
    table = [t for t in rv["tables"] if t["table_id"] == table_id][0]
    counts = {"ready": 0, "hold": 0, "blocked": 0}
    for i in own:
        counts[i["status"]] = counts.get(i["status"], 0) + 1
    rt_item = [i for i in own if i["kind"] == "record_type"]
    blocked = counts.get("blocked", 0)
    check = {"table_id": table_id, "name": table["effective"] if table["approved"] else table["old"], "airtable_name": table["old"], "counts": counts, "total": len(own),
             "reasons": _reasons(own), "dependencies": link_dependencies(conn, base_id, own, names, state, stage, ctx.ws, in_plan),
             "dependency_tables": plan["summary"].get("dependency_tables", []), "limit_findings": plan["summary"].get("limit_findings", []),
             "can_continue": bool(rt_item) and rt_item[0]["status"] == "ready" and blocked == 0}
    structure = structure_state(conn, own, state, stage)
    f = saved_file(extract_dir, base_id, table_id)
    registered = _registered(conn, base_id, table_id)
    done_ids = _marked(conn, base_id, table_id, "records", stage, ws=ctx.ws)
    pending_records = len(rm.pending_records(conn, base_id, table_id, "records", stage, "planning", ctx.ws))
    records = {"file_exists": f["exists"], "file_records": f["records"], "file_note": f["note"], "registered": registered, "done": len(done_ids), "pending": pending_records,
               "complete": registered > 0 and pending_records == 0 and (f["records"] is None or registered >= f["records"])}
    link_items = [i for i in own if i["kind"] == "field" and i.get("pass") == "links" and i["status"] == "ready"]
    pend_links = [r for r in rm.pending_records(conn, base_id, table_id, "links", stage, "planning", ctx.ws) if r[1] in done_ids] if link_items else []
    links = {"applicable": bool(link_items), "link_fields": len(link_items), "pending": len(pend_links),
             "waiting_on": [d for d in check["dependencies"] if d["ready_links"] and not d["loaded"]],
             "done": (not link_items) or (records["complete"] and not pend_links)}
    steps = {"structure": structure, "records": records, "links": links}
    gates = {"can_structure": check["can_continue"], "can_records": check["can_continue"] and structure["done"], "can_links": check["can_continue"] and structure["done"] and records["complete"] and bool(link_items),
             "can_verify": check["can_continue"] and structure["done"] and records["complete"], "can_reset": bool(structure["record_type"])}
    return {"check": check, "steps": steps, "gates": gates, "workspace_id": ctx.ws, "stage": stage}


def need(gates, key, message):
    if not gates[key]:
        raise LoadError(message, 409)


GATE_MESSAGE = {
    "can_structure": "Step 1 is not clear: something in this table's plan is blocked. Fix what the check lists, then check again.",
    "can_records": "Step 2 is not finished: create the record type and fields first (every ready item needs its Workfront ID).",
    "can_links": "Step 3 is not finished (or this table has no link fields): finish the records pass first.",
    "can_verify": "Step 3 is not finished: load the records first.",
    "can_reset": "No Planning record type is recorded for this table in this stage and Workspace, so there is nothing to reset.",
}


def mapping(conn, base_id, table_id, ctx):
    """The Workfront ID map for the table: the record type, then each field with its new name and Workfront ID (select options counted). IDs and names of schema objects only."""
    need_tables(conn)
    inputs, plan = build(conn, base_id, [table_id])
    own = _own(plan, table_id)
    originals = {f["field_id"]: f["original"] for f in inputs["review"]["fields"]}
    table_old = [t["old"] for t in inputs["review"]["tables"] if t["table_id"] == table_id][0]
    state = ls.DbState(conn, base_id, ctx.ws)
    opts = {}
    for i in own:
        if i["kind"] == "option":
            o = opts.setdefault(i["parent"], [0, 0])
            o[1] += 1
            o[0] += 1 if state.get(i["temp_id"], ctx.stage) else 0
    rows = []
    for i in own:
        if i["kind"] == "option":
            continue
        o = opts.get(i["temp_id"])
        rows.append({"kind": "record type" if i["kind"] == "record_type" else "field", "airtable_field_id": i["field_id"] or "", "airtable_name": originals.get(i["field_id"], "") if i["field_id"] else table_old,
                     "new_name": i["name"], "plan_status": i["status"], "state": state.status(i["temp_id"], ctx.stage) or "not started", "workfront_id": state.get(i["temp_id"], ctx.stage) or "",
                     "options": "%d of %d" % tuple(o) if o else ""})
    return {"table_id": table_id, "stage": ctx.stage, "workspace_id": ctx.ws, "rows": rows}


# ------------------------------------------------------------------ the steps that run as a job
def _outcome_rows(res, items):
    by = {i["temp_id"]: i for i in items}
    rows = []
    for r in res["results"]:
        i = by[r["temp_id"]]
        d = r["detail"]
        if isinstance(d, dict):
            d = d.get("request") or ""
        rows.append({"temp_id": r["temp_id"], "kind": r["kind"], "airtable_field_id": i.get("field_id") or "", "name": r["name"], "outcome": r["outcome"], "detail": "" if d is None else str(d)})
    return rows


def step_structure(conn, base_id, table_id, ctx, tr, send, should_stop, progress):
    """Step 2: the record type, fields and select options of ONE table (and the empty record types its link fields point to), in the plan's order."""
    _inputs, plan = build(conn, base_id, [table_id])
    items = [i for i in plan["items"] if i["system"] == "planning"]
    counts = {}

    def log(temp_id, outcome, detail):
        counts[outcome] = counts.get(outcome, 0) + 1
        progress({"step": "structure", "table_id": table_id, "done": sum(counts.values()), "total": len(items), "counts": dict(counts), "item": temp_id})
    res, stuck = run_structure(conn, base_id, items, "planning", tr, ctx.env, ctx.ws, dry_run=not send, should_stop=should_stop, log=log)
    return {"step": "structure", "dry_run": not send, "counts": res["counts"], "items": _outcome_rows(res, items), "cancelled": res["cancelled"], "unfinished_before": stuck, "sharing": res.get("sharing", []),
            "dependency_tables": plan["summary"].get("dependency_tables", [])}


def _failures(rows):
    flat = [{"record_id": rid, "reason": why} for r in rows for rid, why in r["failures"]]
    return flat[:FAILURE_LIMIT], len(flat)


def _tables_for_passes(conn, base_id, ctx):
    """The loadable tables of the WHOLE plan (so a link to a table loaded earlier resolves), keyed by table ID."""
    _inputs, plan = build(conn, base_id, None)
    return rl.table_targets(plan["items"], ls.DbState(conn, base_id, ctx.ws), ctx.stage)


def step_records(conn, base_id, table_id, ctx, tr, send, extract_dir, should_stop, progress, batch=None):
    """Step 3: the records pass for one table. A dry run reads the saved file in memory and the registry and writes nothing."""
    batch = int(batch or ctx.creds.extra.get("record_batch_size") or rl.BATCH_DEFAULT)
    f = saved_file(extract_dir, base_id, table_id)
    if not f["exists"]:
        raise LoadError(f["note"], 409)
    done = _marked(conn, base_id, table_id, "records", ctx.stage, ctx.ws)
    if not send:
        return {"step": "records", "dry_run": True, "in_file": f["records"], "registered": _registered(conn, base_id, table_id), "done": len(done), "would_send": len(f["ids"] - done),
                "batch_size": batch, "batches": -(-len(f["ids"] - done) // batch) if f["ids"] - done else 0, "failures": [], "failures_total": 0, "cancelled": False}
    tables = _tables_for_passes(conn, base_id, ctx)
    if table_id not in tables:
        raise LoadError("The record type for this table is not recorded as created in this stage and Workspace. Run step 2 first.", 409)
    notes = register_tables(conn, base_id, [table_id], extract_dir)
    if table_id in notes:
        raise LoadError(notes[table_id], 409)
    out = run_passes(conn, base_id, tables, extract_dir, tr, ctx.stage, ctx.ws, ("records",), batch, dry_run=False, only=[table_id], should_stop=should_stop, progress=progress)
    res = out[0][1]
    row = res["tables"][0] if res["tables"] else {"pending": 0, "created": 0, "failed": 0, "problems": 0, "note": None, "failures": []}
    fails, total = _failures([row])
    return {"step": "records", "dry_run": False, "in_file": f["records"], "pending": row["pending"], "created": row["created"], "failed": row["failed"], "problems": row["problems"], "note": row["note"],
            "failures": fails, "failures_total": total, "cancelled": res["cancelled"], "batch_size": batch}


def step_links(conn, base_id, table_id, ctx, tr, send, extract_dir, should_stop, progress, batch=None):
    """Step 4: the links pass for one table. Links to a table whose records are not loaded yet wait, and are listed."""
    batch = int(batch or ctx.creds.extra.get("record_batch_size") or rl.BATCH_DEFAULT)
    tables = _tables_for_passes(conn, base_id, ctx)
    if table_id not in tables:
        raise LoadError("The record type for this table is not recorded as created in this stage and Workspace. Run step 2 first.", 409)
    out = run_passes(conn, base_id, tables, extract_dir, tr, ctx.stage, ctx.ws, ("links",), batch, dry_run=not send, only=[table_id], should_stop=should_stop, progress=progress)
    res = out[0][1]
    row = res["tables"][0] if res["tables"] else {"pending": 0, "updated": 0, "waiting": 0, "failed": 0, "note": None, "failures": []}
    fails, total = _failures([row])
    return {"step": "links", "dry_run": not send, "pending": row["pending"], "updated": row["updated"], "waiting": row["waiting"], "failed": row["failed"], "note": row["note"],
            "failures": fails, "failures_total": total, "cancelled": res["cancelled"], "batch_size": batch}


def step_reset(conn, base_id, table_id, ctx, tr, send, should_stop, progress):
    """Step 6: the reset. A dry run reads this tool's own counts only and sends nothing."""
    try:
        r = reset_table(conn, base_id, table_id, ctx.stage, ctx.ws, tr, send, should_stop, lambda p: progress(dict(p, step="reset", table_id=table_id)))
    except rs.ResetError as e:
        raise LoadError(str(e), 409)
    r.pop("record_type", None)
    r.update({"step": "reset", "dry_run": not send, "phrase": rs.phrase(ctx.stage), "cancelled": bool(r["wipe"] and r["wipe"].get("cancelled"))})
    return r


def verify(conn, base_id, table_id, ctx, tr, extract_dir):
    """Step 5: the record counts side by side, as counts only: the saved file, the registry, the records pass, the mapped Planning records, and Workfront's own count (one
    read of one page; the values in the reply are dropped at once). A difference is reported, never repaired."""
    need_tables(conn)
    st = check_table(conn, base_id, table_id, ctx, extract_dir)
    need(st["gates"], "can_verify", GATE_MESSAGE["can_verify"])
    f = saved_file(extract_dir, base_id, table_id)
    before = rs.local_counts(conn, base_id, table_id, ctx.stage, ctx.ws)
    counts = [("Records in the saved file", f["records"]), ("Records in this tool's registry", st["steps"]["records"]["registered"]),
              ("Records with the records pass done", st["steps"]["records"]["done"]), ("Planning records this tool mapped", before["mapped"])]
    workfront, note = None, None
    rt = st["steps"]["structure"]["record_type"]
    try:
        _ids, total = tr.list_record_ids(rt, 1)
        workfront = total
    except Exception as e:
        note = "Workfront's count could not be read: %s" % lr.scrub(e)
    counts.append(("Records Workfront reports", workfront))
    base = counts[0][1]
    diffs = [{"what": "%s differs from %s" % (counts[i][0].lower(), counts[0][0].lower()), "expected": base, "found": counts[i][1], "difference": counts[i][1] - base}
             for i in range(1, len(counts)) if counts[i][1] is not None and base is not None and counts[i][1] != base]
    return {"table_id": table_id, "counts": [{"label": k, "count": v} for k, v in counts], "differences": diffs, "match": not diffs and workfront is not None and base is not None, "note": note,
            "workfront_read": workfront is not None}
