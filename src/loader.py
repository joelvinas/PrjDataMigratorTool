#!/usr/bin/env python3
"""The migration loader: build the load plan and dry-run it. Standard library only.

    python loader.py plan    --db db/migration.db --base appXXXXXXXXXXXXXX [--out out/loader]
    python loader.py dry-run --db db/migration.db --base appXXXXXXXXXXXXXX [--env sandbox1|sandbox2|preview|production] [--out out/loader]
    python loader.py reset   --db db/migration.db --base appXXXXXXXXXXXXXX --table tblXXXXXXXXXXXXXX [--planning-stage dev|uat] [--send --confirm "DELETE RECORDS IN DEV"]
    python loader.py planning-reset --db db/migration.db [--planning-stage dev|uat|production] [--send --confirm "RESET PLANNING IN DEV"]   (database only; deletes nothing in Workfront)
    python loader.py share-record-types --db db/migration.db [--planning-stage dev|uat|production] [--send]   (share the existing record types and place them in the sidebar)
    python loader.py config  [--config config.json]     (which Workfront credentials are set: yes or no only)

The database is opened READ-ONLY and nothing is sent anywhere. `plan` lists everything the migration would create in Workfront Planning and
Workfront Core (record types, fields, select options in Airtable order, and the fields that link a Core object to its Planning record), each
with a temporary ID and a status: ready, hold (a design decision is open) or blocked (something this tool controls is missing), with the reasons.
`dry-run` walks that plan the way a real run would and says what it would create. The environment defaults to sandbox.

Creating anything in Workfront is NOT implemented yet: the request shapes for Workfront Planning and Workfront Core are not verified and no
credentials are configured. See docs/LOADER_PLAN.md.
"""
import argparse, csv, io, os, pathlib, sqlite3, sys

import fieldnames_io as fio
import fieldnames_store as store
import linkdirections as ld
import loadconfig as lc
import loadplan as lp
import loadrun as lr
import loadstate as ls
import coretransport as ct
import planningtransport as pt
import attachments as at
import coreobjects as co
import recordload as rl
import recordmap as rm
import loadreset as rs
import planningreset as plreset
import tableload as tl
import coretargets as tg
import workfronthttp as wh

PLAN_COLUMNS = ["temp_id", "system", "kind", "parent", "group", "table_id", "airtable_field_id", "name", "label", "data_type", "display_type", "position", "status", "reasons", "source", "description"]


def _csv(header, rows):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(header)
    for r in rows:
        w.writerow([fio._safe(x) for x in r])
    return out.getvalue()


def plan_csv(plan):
    return _csv(PLAN_COLUMNS, [[i["temp_id"], i["system"], i["kind"], i["parent"] or "", i["group"] or "", i["table_id"] or "", i["field_id"] or "", i["name"], i["label"] or "",
                                i["data_type"] or "", i["display_type"] or "", "" if i["position"] is None else i["position"], i["status"], " | ".join(i["reasons"]), i["source"], i["description"] or ""]
                               for i in plan["items"]])


def summary_text(plan, base_id, title="Load plan", sent=False):
    s = plan["summary"]
    L = ["%s for %s" % (title, base_id), ""]
    if s.get("only_tables"):
        L += ["Limited to table(s): %s (a run filter; the scope decisions are unchanged)." % ", ".join(s["only_tables"])]
        if s.get("dependency_tables"):
            L += ["Record type(s) created only because a chosen table links to them: %s (their records and fields are not loaded)." % ", ".join(s["dependency_tables"])]
        L += [""]
    L += ["Items: %d" % s["total"]]
    L += ["  %-8s %d" % (k, v) for k, v in sorted(s["by_status"].items())]
    L += ["", "By system, kind and status:"] + ["  %s: %d" % (k, v) for k, v in s["by_system_kind_status"].items()]
    if s["reasons"]:
        L += ["", "Why items are not ready (count of items):"] + ["  %d x %s" % (v, k) for k, v in s["reasons"].items()]
    if s.get("limit_findings"):
        L += ["", "Workfront Planning limits (documented, not yet tested; a person decides, nothing changes on its own):"] + ["  %s: %s" % (f["scope"], f["detail"]) for f in s["limit_findings"]]
    L += ["", "Tables out of scope (skipped): %d" % s["out_of_scope_tables"], "",
          ("This is the plan the run worked from; the counts above say what was sent." if sent else "Nothing was sent to Workfront by this command. Only 'run --send' creates anything (docs/LOADER_PLAN.md).")]
    return "\n".join(L) + "\n"


def dry_run_csv(res):
    return _csv(["temp_id", "system", "kind", "name", "outcome", "detail"], [[r["temp_id"], r["system"], r["kind"], r["name"], r["outcome"], r["detail"] if not isinstance(r["detail"], dict) else
                                                                           "; ".join("%s=%s" % kv for kv in sorted(r["detail"].items()) if kv[1] not in (None, ""))] for r in res["results"]])


def _chosen(a):
    """The sorted table IDs from --table, or None. (Whether they exist in the plan is checked by tableload.build.)"""
    return sorted(set(getattr(a, "tables", None) or [])) or None


def _only(a, inputs):
    """The sorted table IDs from --table (a run filter only: the scope decisions are never touched), or None for every table. An ID that is not in the base, or is out of scope, is refused."""
    chosen = sorted(set(getattr(a, "tables", None) or []))
    if not chosen:
        return None
    bad = lp.unknown_tables(inputs["review"], chosen)
    if bad:
        sys.exit("Not a table in this plan (wrong ID, or the table is out of scope): %s." % ", ".join(bad))
    return chosen


def _open(path):
    if not os.path.isfile(path):
        sys.exit("No such database: %s" % path)
    c = sqlite3.connect(pathlib.Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _write(out, name, text):
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, name), "w", encoding="utf-8-sig", newline="") as f:
        f.write(text)


class _Known(lr.MemoryState):
    """What the database already holds as created, for a dry run (nothing is written)."""
    def __init__(self, env_name, created):
        lr.MemoryState.__init__(self)
        for tid, wid in sorted(created.items()):
            self.ids[(tid, env_name)] = wid


RUN_COLUMNS = ["temp_id", "system", "environment", "kind", "name", "outcome", "detail"]


def _run(a, opener=None):
    """Create the plan's ready items in Workfront. Sends nothing without --send. Core and Planning are run one after the other, each with its own environment
    (Core: sandbox1 to production; Planning: the dev or uat Workspace). The state is kept in load_id_map, so a stopped run carries on without duplicates."""
    if not a.send:
        sys.exit("Nothing was sent. 'run' creates items in Workfront only when you add --send. Use 'plan' and 'dry-run' to see what it would do first (and docs/LOADER_PLAN.md).")
    systems = ("core", "planning") if a.only == "both" else (a.only,)
    try:
        data = lc.read_config(a.config)
    except lc.ConfigError as e:
        sys.exit(str(e))
    creds = {s: lc.credentials(data, s, a.core_env if s == "core" else a.planning_stage) for s in systems}
    missing = [("Core (%s)" % a.core_env) if s == "core" else "Planning" for s in systems if creds[s] is None]
    if missing:
        sys.exit("Not configured in %s: %s. Run 'config' to see what is set (yes or no only)." % (a.config, ", ".join(missing)))
    if "planning" in creds and not creds["planning"].workspace_id:
        sys.exit("The Planning settings in %s need a workspace_id (the Workspace to load into)." % a.config)
    stage = {"core": a.core_env, "planning": a.planning_stage}
    try:
        envs = {s: lr.resolve_environment(stage[s], a.confirm, dry_run=False, workspace_id=creds[s].workspace_id) for s in systems}
    except ValueError as e:
        sys.exit(str(e))
    secrets = lc.secret_values(data)
    if not os.path.isfile(a.db):
        sys.exit("No such database: %s" % a.db)
    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    try:
        if not ls.available(conn):
            sys.exit("This database does not have the loader's state table in its current form. Apply the migrations (python sql/apply_migrations.py, after a backup) first.")
        try:
            inputs, plan = tl.build(conn, a.base, getattr(a, "tables", None), include_link_fields=not a.no_link_fields)
        except tl.LoadError as e:
            sys.exit(e.message)
        _write(a.out, "load_plan.csv", plan_csv(plan))
        rows, lines, results = [], ["Load run for %s" % a.base, ""], {}
        sharing_problems = 0
        for s in systems:
            try:
                tr = ct.CoreTransport(wh.Http(creds[s], secrets, opener=opener)) if s == "core" else _planning_transport(data, creds[s], a.planning_stage, secrets, opener)
            except ValueError as e:
                sys.exit(str(e))
            res, stuck = tl.run_structure(conn, a.base, plan["items"], s, tr, envs[s], creds[s].workspace_id if s == "planning" else None, dry_run=False)
            if stuck:
                lines.append("%s: %d item(s) from an earlier run were started and not finished; they are tried again: %s" % (s.title(), len(stuck), ", ".join(stuck[:10]) + (" ..." if len(stuck) > 10 else "")))
            results[s] = res
            sharing_problems += len(_problems(res.get("sharing") or []))
            lines += ["%s in %s%s:" % (s.title(), envs[s].name.upper(), (" (Workspace %s)" % creds[s].workspace_id) if s == "planning" else "")] + ["  %-16s %d" % (k, v) for k, v in sorted(res["counts"].items())] + [""]
            if s == "planning":
                lines += _share_lines(res.get("sharing") or [], tr.sharing) + [""]
            for r in res["results"]:
                d = r["detail"]
                rows.append([r["temp_id"], s, envs[s].name, r["kind"], r["name"], r["outcome"], d if not isinstance(d, dict) else "; ".join("%s=%s" % kv for kv in sorted(d.items()) if kv[1] not in (None, ""))])
    finally:
        conn.close()
    _write(a.out, "load_run.csv", lc.redact(_csv(RUN_COLUMNS, rows), secrets))
    failed = sum(r["counts"].get("failed", 0) for r in results.values())
    lines += ["%d item(s) failed. See load_run.csv for each item's outcome and the plain reason." % failed if failed else "No item failed.",
              ("%d sharing or sidebar problem(s): see the PROBLEM lines above. Those record types may be invisible to other people; run 'share-record-types' after fixing the cause." % sharing_problems) if sharing_problems else "", "",
              "Attaching Core fields to a custom form is a manual step. Anything on hold or blocked in the plan was not sent (load_plan.csv gives the reasons)."]
    text = lc.redact("\n".join(lines), secrets)
    _write(a.out, "load_run_summary.txt", text + "\n\n" + lc.redact(summary_text(plan, a.base, sent=True), secrets))
    print(text + "\nReports written to %s" % a.out)
    return results

def _planning_transport(data, creds, stage, secrets, opener=None):
    """The Planning transport with the stage's share_with and section_name from config.json (None when neither is set). A half-set or malformed setting stops with a plain message."""
    try:
        sharing = lc.planning_sharing(data, stage)
    except lc.ConfigError as e:
        sys.exit(str(e))
    return pt.PlanningTransport(wh.Http(creds, secrets, opener=opener), sharing)


def _problems(results):
    return [(r["record_type_id"], p) for r in results for p in r["problems"]]


def _share_lines(results, sharing):
    """Plain lines about sharing and sidebar placement for a run's record types. A problem is never hidden: it is listed with the record type's ID."""
    if not results:
        return ["Sharing: no record type was created or reused in this run."] if sharing else []
    ok = sum(1 for r in results if r["members"] == "ok" and r["section"] == "ok")
    L = ["Sharing and sidebar placement: %d record type(s), %d shared and placed (each confirmed by reading it back), %d with a problem." % (len(results), ok, len(results) - ok)]
    for rid, why in _problems(results):
        L.append("  PROBLEM %s: %s" % (rid, why))
    for r in results:
        for n in r["notes"]:
            L.append("  note %s: %s" % (r["record_type_id"], n))
    return L


RECORD_COLUMNS = ["table_id", "table_name", "pass", "pending", "done", "waiting", "failed", "problems", "note"]
FAILURE_COLUMNS = ["table_id", "pass", "airtable_record_id", "reason"]


def _records(a, opener=None):
    """Load the saved Airtable records into the Planning record types the field load created: the records pass, then the links pass. Without --send it only counts
    what is waiting. Record values are read from the saved files in memory and sent; the reports hold table IDs, record IDs and counts only."""
    try:
        data = lc.read_config(a.config)
    except lc.ConfigError as e:
        sys.exit(str(e))
    creds = lc.credentials(data, "planning", a.planning_stage)
    if creds is None or not creds.workspace_id:
        sys.exit("Planning is not configured for stage %s in %s (a Bearer token and a workspace_id are needed). Run 'config' to see what is set." % (a.planning_stage, a.config))
    try:
        env = lr.resolve_environment(a.planning_stage, a.confirm, dry_run=not a.send, workspace_id=creds.workspace_id)
    except ValueError as e:
        sys.exit(str(e))
    secrets = lc.secret_values(data)
    if not os.path.isfile(a.db):
        sys.exit("No such database: %s" % a.db)
    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    try:
        if not (ls.available(conn) and rm.available(conn)):
            sys.exit("This database does not have the loader's state tables in their current form. Apply the migrations (python sql/apply_migrations.py, after a backup) first.")
        try:
            inputs, plan = tl.build(conn, a.base, getattr(a, "tables", None))
        except tl.LoadError as e:
            sys.exit(e.message)
        only = _chosen(a)
        state = ls.DbState(conn, a.base, creds.workspace_id)
        tables = rl.table_targets(plan["items"], state, env.name)
        link_notes = {}
        if only:                                              # a dependency record type is created empty: its records are not loaded, and links to it wait
            tables = {t: v for t, v in tables.items() if t in only}
            for t, v in sorted(tables.items()):
                gone = sorted({f["linked_table_id"] for f in v["fields"] if f["pass"] == "links" and f["linked_table_id"] not in tables})
                if gone:
                    link_notes[t] = "links to table(s) not in this run stay on hold: %s" % ", ".join(str(g) for g in gone)
        ws = creds.workspace_id
        notes = {}
        if a.send:
            notes = tl.register_tables(conn, a.base, tables, a.extract_dir)      # the record IDs of every loadable table (IDs only are read)
        tr = pt.PlanningTransport(wh.Http(creds, secrets, opener=opener))
        batch = int(creds.extra.get("record_batch_size") or rl.BATCH_DEFAULT) if a.batch is None else a.batch
        rows, failures, lines = [], [], ["Record load for %s in %s (Workspace %s)%s" % (a.base, env.name.upper(), ws, "" if a.send else ", nothing was sent"), ""]
        passes = ("records", "links") if a.pass_name == "both" else (a.pass_name,)
        for p, res in tl.run_passes(conn, a.base, tables, a.extract_dir, tr, env.name, ws, passes, batch, dry_run=not a.send):
            for t in res["tables"]:
                done = t.get("created", t.get("updated", 0))
                rows.append([t["table_id"], t["name"], p, t["pending"], done, t.get("waiting", 0), t["failed"], t.get("problems", 0), t["note"] or notes.get(t["table_id"], "") or link_notes.get(t["table_id"], "")])
                failures += [[t["table_id"], p, rid, why] for rid, why in t["failures"]]
            n_done, n_wait = sum(r[4] for r in rows if r[2] == p), sum(r[3] for r in rows if r[2] == p)
            lines.append("%s pass: %s, %d failed." % (p.title(), ("%d record(s) done, %d still waiting" % (n_done, sum(r[5] for r in rows if r[2] == p))) if a.send else ("%d record(s) registered and waiting" % n_wait),
                                                     sum(r[6] for r in rows if r[2] == p)))
        if not a.send:
            lines.append("Records are registered from the saved files only when you add --send, so a first dry run can show none waiting.")
    finally:
        conn.close()
    _write(a.out, "record_load.csv", lc.redact(_csv(RECORD_COLUMNS, rows), secrets))
    _write(a.out, "record_load_failures.csv", lc.redact(_csv(FAILURE_COLUMNS, failures), secrets))
    text = lc.redact(chr(10).join(lines + ["", "Failed records stay pending and are tried again on the next run. Reports hold table IDs, Airtable record IDs and counts only."]), secrets)
    _write(a.out, "record_load_summary.txt", text)
    print(text + chr(10) + "Reports written to %s" % a.out)
    return {"rows": rows, "failures": failures}


def _reset(a, opener=None):
    """Wipe one table's records in the Planning Workspace and clear what this tool remembers about them, so a reload starts clean. A dry run (the default) sends nothing at
    all and only reports counts. The record type and fields stay. load_id_map is never touched. Counts only; no record value is read into a report."""
    try:
        data = lc.read_config(a.config)
    except lc.ConfigError as e:
        sys.exit(str(e))
    creds = lc.credentials(data, "planning", a.planning_stage)
    if creds is None or not creds.workspace_id:
        sys.exit("Planning is not configured for stage %s in %s (a Bearer token and a workspace_id are needed). Run 'config' to see what is set." % (a.planning_stage, a.config))
    try:
        env = lr.resolve_environment(a.planning_stage, lr.PRODUCTION_PHRASE if a.confirm == rs.phrase(a.planning_stage) else None, dry_run=not a.send, workspace_id=creds.workspace_id)
    except ValueError as e:
        sys.exit(str(e))
    need = rs.phrase(env.name)
    if a.send and a.confirm != need:
        sys.exit("A reset deletes records and cannot be undone. To send it, add the exact phrase: --confirm \"%s\"" % need)
    secrets = lc.secret_values(data)
    if not os.path.isfile(a.db):
        sys.exit("No such database: %s" % a.db)
    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    ws = creds.workspace_id
    try:
        try:
            r = tl.reset_table(conn, a.base, a.table, env.name, ws, pt.PlanningTransport(wh.Http(creds, secrets, opener=opener)) if a.send else None, a.send)
        except rs.ResetError as e:
            sys.exit(str(e))
        name, before, w = r["name"], r["before"], r["wipe"]
        lines = ["Reset of table %s (%s) in %s, Workspace %s%s" % (a.table, name, env.name.upper(), ws, "" if a.send else ", dry run: nothing was sent"), "",
                 "Mapped Planning records this tool holds for the table: %d. Pass markers: %s." % (before["mapped"], ", ".join("%s %d" % kv for kv in sorted(before["markers"].items())) or "none")]
        result = {"before": before, "wipe": None, "cleared": None}
        if not a.send:
            lines += ["", "With --send --confirm \"%s\" it would:" % need,
                      "  1. delete every record of the record type in Workfront Planning, 100 per call (the record type and its fields stay); the live count is read only when it runs;",
                      "  2. then, once the record type lists empty, clear this table's rows from target_mappings and record_pass_markers for this stage and Workspace.",
                      "load_id_map is not touched, so the record type and fields stay recorded as created."]
        else:
            result["wipe"] = w
            lines += ["", "Workfront: %d record(s) deleted in %d call(s), %d refused." % (w["deleted"], w["rounds"], w["failed"])]
            for why, n in sorted(w["reasons"].items()):
                lines.append("  %d x %s" % (n, why))
            if w["left"] == 0:
                result["cleared"] = r["cleared"]
                lines += ["The record type is empty. Cleared from this tool's memory: %d mapping(s), %d pass marker(s). The next 'records --send' starts clean." % (result["cleared"]["mappings"], result["cleared"]["markers"])]
            else:
                lines += ["The record type is NOT empty (%s still listed), so this tool's memory was left as it is. Run the reset again." % ("an unknown number" if w["left"] is None else "%d" % w["left"])]
    finally:
        conn.close()
    text = lc.redact(chr(10).join(lines), secrets)
    _write(a.out, "reset_summary.txt", text)
    print(text + chr(10) + "Report written to %s" % a.out)
    return result


def _planning_reset(a, opener=None):
    """Forget every Planning reference in the database for one stage and Workspace (load_id_map, target_mappings, record_pass_markers). Core rows, source records, the Airtable
    schema and every recorded decision are not touched. A dry run (the default) changes nothing and prints counts only. A real run needs the typed phrase, takes a backup copy
    of the database first and deletes in one transaction. Nothing is deleted in Workfront: the record types this tool created that still exist there are only LISTED (a read)."""
    stage = a.planning_stage
    try:
        data = lc.read_config(a.config)
    except lc.ConfigError as e:
        data = None
        if a.send or not a.workspace_id:
            sys.exit(str(e))
    creds = lc.credentials(data, "planning", stage) if data else None
    ws = a.workspace_id or (creds.workspace_id if creds else None)
    if not ws:
        sys.exit("A Workspace ID is needed: give --workspace-id, or set workspace_id for stage %s in %s." % (stage, a.config))
    need = plreset.phrase(stage)
    if a.send and a.confirm != need:
        sys.exit("A planning reset removes this tool's memory of every Planning record type, field and record for this stage and Workspace. To send it, add the exact phrase: --confirm \"%s\"" % need)
    if not os.path.isfile(a.db):
        sys.exit("No such database: %s" % a.db)
    secrets = lc.secret_values(data) if data else set()
    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    try:
        try:
            c = plreset.counts(conn, stage, ws)
            ids = plreset.record_type_ids(conn, stage, ws)
        except plreset.PlanningResetError as e:
            sys.exit(str(e))
        listing, note = None, None
        if creds is None or creds.workspace_id != ws:
            note = "Workfront was not read: no Planning credentials in %s for stage %s with this Workspace ID." % (a.config, stage)
        else:
            try:
                listing = plreset.still_in_workfront(pt.PlanningTransport(wh.Http(creds, secrets, opener=opener)), ids)
            except (wh.HttpError, ValueError) as e:
                note = "Workfront could not be read, so the list of record types that still exist is not available: %s" % lr.scrub(e)
        lines = ["Planning reset for stage %s, Workspace %s%s" % (stage.upper(), ws, "" if a.send else ", dry run: nothing was changed"), ""] + plreset.report_lines(c, listing, note)
        result = {"counts": c, "listing": listing, "cleared": None, "backup": None}
        if not a.send:
            lines += ["", "With --send --confirm \"%s\" it would take a backup copy of the database, then remove the rows counted above in one transaction." % need,
                      "Core rows, source_records, the Airtable schema, profiling statistics and every recorded decision are not touched. Nothing is deleted in Workfront."]
        else:
            result["backup"] = plreset.backup(conn, os.path.join(a.out, "backups"))
            result["cleared"] = plreset.clear(conn, stage, ws)
            k = result["cleared"]
            lines += ["", "Backup of the database before the change: %s" % result["backup"],
                      "Removed: %d load_id_map row(s), %d target_mappings row(s), %d record_pass_markers row(s). Nothing was deleted in Workfront." % (k["load_id_map"], k["target_mappings"], k["record_pass_markers"])]
    finally:
        conn.close()
    text = lc.redact(chr(10).join(lines), secrets)
    _write(a.out, "planning_reset_summary.txt", text)
    print(text + chr(10) + "Report written to %s" % a.out)
    return result


def _share_record_types(a, opener=None):
    """One-off: share and place (in the sidebar) the record types that already exist in the id map for this stage and Workspace. Without --send it lists them and sends nothing.
    Each is checked first and only what is missing is granted; each result is verified by reading it back."""
    try:
        data = lc.read_config(a.config)
        sharing = lc.planning_sharing(data, a.planning_stage)
    except lc.ConfigError as e:
        sys.exit(str(e))
    if sharing is None:
        sys.exit("Nothing to share with: set share_with and section_name for stage %s in %s (section workfront.planning)." % (a.planning_stage, a.config))
    creds = lc.credentials(data, "planning", a.planning_stage)
    if creds is None or not creds.workspace_id:
        sys.exit("Planning is not configured for stage %s in %s (a Bearer token and a workspace_id are needed). Run 'config' to see what is set." % (a.planning_stage, a.config))
    try:
        env = lr.resolve_environment(a.planning_stage, a.confirm, dry_run=not a.send, workspace_id=creds.workspace_id)
    except ValueError as e:
        sys.exit(str(e))
    secrets = lc.secret_values(data)
    if not os.path.isfile(a.db):
        sys.exit("No such database: %s" % a.db)
    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    try:
        if not ls.available(conn):
            sys.exit("This database does not have the loader's state table in its current form. Apply the migrations first.")
        ids = plreset.record_type_ids(conn, env.name, creds.workspace_id)
    finally:
        conn.close()
    lines = ["Share record types in %s, Workspace %s%s" % (env.name.upper(), creds.workspace_id, "" if a.send else ", nothing was sent"), "",
             "%d record type(s) in the id map; section %r; %d subject(s)." % (len(ids), sharing["section_name"], len(sharing["share_with"]))]
    results = []
    if a.send:
        tr = pt.PlanningTransport(wh.Http(creds, secrets, opener=opener), sharing)
        for rid in sorted(ids):
            results.append(tr.ensure_shared(rid))
        lines += [""] + _share_lines(results, sharing)
    else:
        lines += ["  %s  %s" % (rid, ids[rid][1]) for rid in sorted(ids)] + ["", "Add --send to share them and place them in the sidebar (each result is read back and checked)."]
    text = lc.redact(chr(10).join(lines), secrets)
    _write(a.out, "share_record_types_summary.txt", text)
    print(text + chr(10) + "Report written to %s" % a.out)
    return {"results": results, "ids": ids}


CORE_OBJECT_COLUMNS = ["table_id", "table_name", "object_code", "pending", "created", "failed", "note"]


def _core_objects(a, opener=None):
    """Create one Core object per registered record, under the parent decided for its table. Without --send it only counts what is waiting. Reports hold table IDs,
    Airtable record IDs and counts only; an object's name is read from the saved file in memory and sent, never written down."""
    try:
        data = lc.read_config(a.config)
    except lc.ConfigError as e:
        sys.exit(str(e))
    creds = lc.credentials(data, "core", a.core_env)
    if creds is None:
        sys.exit("Core (%s) is not configured in %s. Run 'config' to see what is set." % (a.core_env, a.config))
    try:
        env = lr.resolve_environment(a.core_env, a.confirm, dry_run=not a.send)
    except ValueError as e:
        sys.exit(str(e))
    secrets = lc.secret_values(data)
    if not os.path.isfile(a.db):
        sys.exit("No such database: %s" % a.db)
    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    try:
        if not (rm.available(conn) and tg.available(conn)):
            sys.exit("This database does not have the record map and the Core decision tables. Apply the migrations (python sql/apply_migrations.py, after a backup) first.")
        try:
            tr = ct.CoreTransport(wh.Http(creds, secrets, opener=opener))
        except ValueError as e:
            sys.exit(str(e))
        if a.send:
            for t in tg.rows(conn, a.base):
                if t["ready"]:
                    try:
                        doc = rl.read_extract(a.extract_dir, a.base, t["table_id"])
                        rm.register(conn, a.base, t["table_id"], [r.get("id") for r in doc["records"] if isinstance(r, dict)])
                    except (rl.RecordLoadError, rm.RecordMapError):
                        pass                                  # the run below reports the table's note
        res = co.load_core_objects(conn, a.base, a.extract_dir, tr, env.name, dry_run=not a.send)
    finally:
        conn.close()
    rows = [[t["table_id"], t["name"], t["object_code"] or "", t["pending"], t["created"], t["failed"], t["note"] or ""] for t in res["tables"]]
    fails = [[t["table_id"], rid, why] for t in res["tables"] for rid, why in t["failures"]]
    _write(a.out, "core_objects.csv", lc.redact(_csv(CORE_OBJECT_COLUMNS, rows), secrets))
    _write(a.out, "core_objects_failures.csv", lc.redact(_csv(["table_id", "airtable_record_id", "reason"], fails), secrets))
    lines = ["Core objects for %s in %s%s" % (a.base, env.name.upper(), "" if a.send else ", nothing was sent"), ""]
    lines += ["  %-40s %-7s %s" % (r[1][:40], r[2] or "-", ("%d created, %d failed" % (r[4], r[5])) if a.send else ("%d waiting" % r[3])) + ("  (%s)" % r[6] if r[6] else "") for r in rows]
    lines += ["", "%d created, %d failed." % (res["created"], res["failed"]) if a.send else "Records are registered only when you add --send, so a first dry run can show none waiting.",
              "A table with no decided object type and parent is skipped: decide it on the Stage 4 page. Failed records stay pending and are tried again on the next run."]
    text = lc.redact(chr(10).join(lines), secrets)
    _write(a.out, "core_objects_summary.txt", text)
    print(text + chr(10) + "Reports written to %s" % a.out)
    return {"rows": rows, "failures": fails}


ATTACHMENT_COLUMNS = ["table_id", "table_name", "indexed", "planned", "downloaded", "failed_in_stage1", "unregistered", "missing_file"]


def _attachments(a, opener=None):
    """Plan the downloaded attachment files and upload each as a Core document on its record's Core object. Without --send nothing is sent and nothing is written. Reports hold
    table IDs, Airtable IDs and counts only: no file name and no link."""
    try:
        data = lc.read_config(a.config)
    except lc.ConfigError as e:
        sys.exit(str(e))
    creds = lc.credentials(data, "core", a.core_env)
    if creds is None:
        sys.exit("Core (%s) is not configured in %s. Run 'config' to see what is set." % (a.core_env, a.config))
    try:
        env = lr.resolve_environment(a.core_env, a.confirm, dry_run=not a.send)
    except ValueError as e:
        sys.exit(str(e))
    secrets = lc.secret_values(data)
    if not os.path.isfile(a.db):
        sys.exit("No such database: %s" % a.db)
    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    try:
        if not rm.available(conn):
            sys.exit("This database does not have the record map tables. Apply the migrations (python sql/apply_migrations.py, after a backup) first.")
        try:
            tr = ct.CoreTransport(wh.Http(creds, secrets, opener=opener))
        except ValueError as e:
            sys.exit(str(e))
        tables = [(r[0], r[1]) for r in conn.execute("SELECT table_id, table_name FROM airtable_tables WHERE base_id=? ORDER BY lower(table_name), table_id", (a.base,))]
        rows = []
        if a.send:
            for tid, name in tables:
                c = at.plan_table(conn, a.base, tid, env.name, a.extract_dir)
                if c["indexed"]:
                    rows.append([tid, name, c["indexed"], c["planned"], c["downloaded"], c["failed"], c["unregistered"], c["missing_file"]])
        res = at.upload_all(conn, a.base, env.name, tr, a.extract_dir, dry_run=not a.send)
        counts = rm.summary(conn, a.base, env.name, "core", "")["attachments"]
    finally:
        conn.close()
    _write(a.out, "attachments.csv", lc.redact(_csv(ATTACHMENT_COLUMNS, rows), secrets))
    _write(a.out, "attachments_failures.csv", lc.redact(_csv(["table_id", "airtable_record_id", "airtable_attachment_id", "reason"], [list(f) for f in res["failures"]]), secrets))
    lines = ["Attachments for %s in %s%s" % (a.base, env.name.upper(), "" if a.send else ", nothing was sent"), "",
             "In the attachment map now: " + ", ".join("%d %s" % (counts[k], k) for k in sorted(counts)) + ".", ""]
    if a.send:
        lines.append("This run: %d uploaded, %d failed, %d retried, %d waiting for their record's Core object, %d with no file to send." % (res["uploaded"], res["failed"], res["retried"], res["waiting"], res["no_file"]))
    else:
        lines.append("Would upload %d; %d wait for their record's Core object; %d have no file to send. Attachments are planned from the saved files only when you add --send." % (res["would_upload"], res["waiting"], res["no_file"]))
    lines += ["", "A record's Core object comes from 'core-objects'. A failed upload is tried again on the next run. Nothing here names a file."]
    text = lc.redact(chr(10).join(lines), secrets)
    _write(a.out, "attachments_summary.txt", text)
    print(text + chr(10) + "Reports written to %s" % a.out)
    return {"rows": rows, "result": res}


def main(argv=None, opener=None):
    ap = argparse.ArgumentParser(description="Build the migration load plan and dry-run it (read-only; nothing is sent to Workfront).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pc = sub.add_parser("config", help="say which Workfront credentials are configured (yes or no only; a value is never shown)")
    pc.add_argument("--config", default="config.json")
    for name in ("plan", "dry-run", "run"):
        p = sub.add_parser(name)
        p.add_argument("--db", required=True)
        p.add_argument("--base", required=True, help="Airtable base ID")
        p.add_argument("--out", default="out/loader")
        p.add_argument("--no-link-fields", action="store_true", help="leave out the fields that link a Core object to its Planning record")
        p.add_argument("--table", dest="tables", action="append", metavar="TABLE_ID", help="limit the run to this table (repeatable); a run filter only, scope is not changed")
        if name == "dry-run":
            p.add_argument("--env", default="sandbox1", choices=lr.ENVIRONMENTS, help="the environment or stage label (default: sandbox1; dev and uat are the Planning stages)")
        if name == "run":
            p.add_argument("--send", action="store_true", help="actually create items in Workfront. Without it nothing is sent")
            p.add_argument("--config", default="config.json", help="the file that holds the Workfront settings and secrets (default: config.json)")
            p.add_argument("--only", default="both", choices=("both", "core", "planning"), help="which system to load (default: both)")
            p.add_argument("--core-env", default="sandbox1", choices=lc.ENVIRONMENTS, help="the Core environment (default: sandbox1)")
            p.add_argument("--planning-stage", default="dev", choices=lr.PLANNING_STAGES, help="the Planning stage, which is a Workspace (default: dev)")
            p.add_argument("--confirm", default=None, help="the exact phrase a real Core production run needs: %s" % lr.PRODUCTION_PHRASE)
    pr = sub.add_parser("records", help="load the saved Airtable records into the Planning record types (records pass, then links pass)")
    pr.add_argument("--db", required=True)
    pr.add_argument("--base", required=True, help="Airtable base ID")
    pr.add_argument("--out", default="out/loader")
    pr.add_argument("--send", action="store_true", help="actually create records in Workfront. Without it nothing is sent")
    pr.add_argument("--config", default="config.json")
    pr.add_argument("--extract-dir", default="data_extract", help="where the saved record files are (default: data_extract)")
    pr.add_argument("--planning-stage", default="dev", choices=lr.PLANNING_STAGES, help="a stage defined in config.json (workfront.planning)")
    pr.add_argument("--confirm", default=None, help="the exact phrase a real send to the production stage needs: %s" % lr.PRODUCTION_PHRASE)
    pr.add_argument("--table", dest="tables", action="append", metavar="TABLE_ID", help="limit the run to this table (repeatable); a run filter only, scope is not changed")
    pr.add_argument("--pass", dest="pass_name", default="both", choices=("both", "records", "links"))
    pr.add_argument("--batch", type=int, default=None, help="records per call (default: record_batch_size in config.json, else %d)" % rl.BATCH_DEFAULT)
    pw = sub.add_parser("reset", help="delete one table's records in the Planning Workspace and clear what this tool remembers about them (dry run unless --send)")
    pw.add_argument("--db", required=True)
    pw.add_argument("--base", required=True, help="Airtable base ID")
    pw.add_argument("--table", required=True, metavar="TABLE_ID", help="the one table to reset")
    pw.add_argument("--out", default="out/loader")
    pw.add_argument("--send", action="store_true", help="actually delete records in Workfront. Without it nothing is sent")
    pw.add_argument("--confirm", default=None, help="the exact phrase a real reset needs: DELETE RECORDS IN DEV (or UAT)")
    pw.add_argument("--config", default="config.json")
    pw.add_argument("--planning-stage", default="dev", choices=lr.PLANNING_STAGES)
    ppr = sub.add_parser("planning-reset", help="forget every Planning ID this tool holds for one stage and Workspace (database only; dry run unless --send). Deletes nothing in Workfront")
    ppr.add_argument("--db", required=True)
    ppr.add_argument("--out", default="out/loader")
    ppr.add_argument("--send", action="store_true", help="really remove the rows (after a backup copy). Without it nothing is changed")
    ppr.add_argument("--confirm", default=None, help="the exact phrase a real reset needs: RESET PLANNING IN DEV (or UAT, PRODUCTION)")
    ppr.add_argument("--config", default="config.json")
    ppr.add_argument("--planning-stage", default="dev", choices=lr.PLANNING_STAGES)
    ppr.add_argument("--workspace-id", default=None, help="the Workspace to forget (default: workspace_id of the stage in config.json)")
    psr = sub.add_parser("share-record-types", help="share the record types already in the id map and place them in the sidebar section (share_with and section_name in config.json); dry run unless --send")
    psr.add_argument("--db", required=True)
    psr.add_argument("--out", default="out/loader")
    psr.add_argument("--send", action="store_true", help="actually change sharing and the sidebar in Workfront. Without it nothing is sent")
    psr.add_argument("--config", default="config.json")
    psr.add_argument("--planning-stage", default="dev", choices=lr.PLANNING_STAGES)
    psr.add_argument("--confirm", default=None, help="the exact phrase a real send to the production stage needs: %s" % lr.PRODUCTION_PHRASE)
    pc2 = sub.add_parser("core-objects", help="create one Core object (project, request or task) per record under the parent decided for its table")
    pc2.add_argument("--db", required=True)
    pc2.add_argument("--base", required=True, help="Airtable base ID")
    pc2.add_argument("--out", default="out/loader")
    pc2.add_argument("--send", action="store_true", help="actually create objects in Core. Without it nothing is sent")
    pc2.add_argument("--config", default="config.json")
    pc2.add_argument("--extract-dir", default="data_extract")
    pc2.add_argument("--core-env", default="sandbox1", choices=lc.ENVIRONMENTS)
    pc2.add_argument("--confirm", default=None, help="the exact phrase a real Core production run needs: %s" % lr.PRODUCTION_PHRASE)
    pa = sub.add_parser("attachments", help="upload the downloaded attachment files as Core documents on the Core objects their records became")
    pa.add_argument("--db", required=True)
    pa.add_argument("--base", required=True, help="Airtable base ID")
    pa.add_argument("--out", default="out/loader")
    pa.add_argument("--send", action="store_true", help="actually upload to Core. Without it nothing is sent or written")
    pa.add_argument("--config", default="config.json")
    pa.add_argument("--extract-dir", default="data_extract")
    pa.add_argument("--core-env", default="sandbox1", choices=lc.ENVIRONMENTS)
    pa.add_argument("--confirm", default=None, help="the exact phrase a real Core production run needs: %s" % lr.PRODUCTION_PHRASE)
    a = ap.parse_args(argv)
    if a.cmd == "share-record-types":
        return _share_record_types(a, opener)
    if a.cmd == "planning-reset":
        return _planning_reset(a, opener)
    if a.cmd == "attachments":
        return _attachments(a, opener)
    if a.cmd == "core-objects":
        return _core_objects(a, opener)
    if a.cmd == "records":
        return _records(a, opener)
    if a.cmd == "reset":
        return _reset(a, opener)
    if a.cmd == "config":
        try:
            st = lc.status(lc.read_config(a.config))
        except lc.ConfigError as e:
            sys.exit(str(e))
        yn = lambda b: "configured" if b else "not configured"
        lines = ["Workfront credentials in %s (yes or no only):" % a.config] + ["  Core %-12s %s" % (e, yn(st["core"][e])) for e in lc.ENVIRONMENTS] +                 ["  Planning %-10s   %s (token, address and Workspace ID)" % (s, yn(ok)) for s, ok in sorted(st["planning_stages"].items())] + ([] if st["planning_stages"] else ["  Planning              not configured"])
        print("\n".join(lines))
        return st
    if a.cmd == "run":
        return _run(a, opener)
    conn = _open(a.db)
    try:
        inputs = store.loader_inputs(conn, a.base)
        inputs["link_state"] = ld.field_states(conn)
        saved = ls.DbState(conn, a.base) if ls.available(conn) else None     # read only here: a dry run only LOOKS at what is already created
        pending = saved.pending(a.env) if (saved is not None and a.cmd != "plan") else []
        done_ids = {} if saved is None or a.cmd == "plan" else {r[0]: r[1] for r in conn.execute("SELECT temp_id, workfront_id FROM load_id_map WHERE environment=? AND base_id=? AND status='created'", (a.env, a.base))}
    except store.StoreError as e:
        sys.exit(e.message)
    finally:
        conn.close()
    plan = lp.build_plan(inputs["review"], inputs["choices"], include_link_fields=not a.no_link_fields, link_state=inputs.get("link_state"), record_counts=inputs.get("record_counts"), field_facts=inputs.get("field_facts"),
                         only_tables=_only(a, inputs))
    _write(a.out, "load_plan.csv", plan_csv(plan))
    text = summary_text(plan, a.base)
    if a.cmd == "plan":
        _write(a.out, "load_plan_summary.txt", text)
        print(text + "Reports written to %s" % a.out)
        return plan
    env = lr.resolve_environment(a.env, None, dry_run=True)
    res = lr.run_plan(plan["items"], _Known(env.name, done_ids), {"planning": lr.DryRunTransport("planning"), "core": lr.DryRunTransport("core")}, env, dry_run=True)
    _write(a.out, "load_dry_run.csv", dry_run_csv(res))
    lines = ["Dry run in %s (nothing was sent)" % env.name.upper(), ""] + ["  %-16s %d" % (k, v) for k, v in sorted(res["counts"].items())]
    if pending:
        lines += ["", "%d item(s) were started by an earlier run and never finished; a real run will try them again: %s" % (len(pending), ", ".join(pending[:10]) + (" ..." if len(pending) > 10 else ""))]
    _write(a.out, "load_dry_run_summary.txt", "\n".join(lines) + "\n\n" + text)
    print("\n".join(lines) + "\n\nReports written to %s" % a.out)
    return res


if __name__ == "__main__":
    main()
