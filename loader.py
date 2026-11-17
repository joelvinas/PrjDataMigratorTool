#!/usr/bin/env python3
"""The migration loader: build the load plan and dry-run it. Standard library only.

    python loader.py plan    --db db/migration.db --base appXXXXXXXXXXXXXX [--out out/loader]
    python loader.py dry-run --db db/migration.db --base appXXXXXXXXXXXXXX [--env sandbox1|sandbox2|preview|production] [--out out/loader]
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
import workfronthttp as wh

PLAN_COLUMNS = ["temp_id", "system", "kind", "parent", "group", "table_id", "airtable_field_id", "name", "label", "data_type", "position", "status", "reasons", "source", "description"]


def _csv(header, rows):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(header)
    for r in rows:
        w.writerow([fio._safe(x) for x in r])
    return out.getvalue()


def plan_csv(plan):
    return _csv(PLAN_COLUMNS, [[i["temp_id"], i["system"], i["kind"], i["parent"] or "", i["group"] or "", i["table_id"] or "", i["field_id"] or "", i["name"], i["label"] or "",
                                i["data_type"] or "", "" if i["position"] is None else i["position"], i["status"], " | ".join(i["reasons"]), i["source"], i["description"] or ""]
                               for i in plan["items"]])


def summary_text(plan, base_id, title="Load plan", sent=False):
    s = plan["summary"]
    L = ["%s for %s" % (title, base_id), "", "Items: %d" % s["total"]]
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
    creds = {s: lc.credentials(data, s, a.core_env if s == "core" else None) for s in systems}
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
            inputs = store.loader_inputs(conn, a.base)
        except store.StoreError as e:
            sys.exit(e.message)
        inputs["link_state"] = ld.field_states(conn)
        plan = lp.build_plan(inputs["review"], inputs["choices"], include_link_fields=not a.no_link_fields, link_state=inputs.get("link_state"),
                             record_counts=inputs.get("record_counts"), field_facts=inputs.get("field_facts"))
        _write(a.out, "load_plan.csv", plan_csv(plan))
        rows, lines, results = [], ["Load run for %s" % a.base, ""], {}
        for s in systems:
            try:
                tr = (ct.CoreTransport if s == "core" else pt.PlanningTransport)(wh.Http(creds[s], secrets, opener=opener))
            except ValueError as e:
                sys.exit(str(e))
            state = ls.DbState(conn, a.base, creds[s].workspace_id if s == "planning" else None)
            stuck = state.pending(envs[s].name)
            if stuck:
                lines.append("%s: %d item(s) from an earlier run were started and not finished; they are tried again: %s" % (s.title(), len(stuck), ", ".join(stuck[:10]) + (" ..." if len(stuck) > 10 else "")))
            res = lr.run_plan([i for i in plan["items"] if i["system"] == s], state, {s: tr}, envs[s], dry_run=False)
            results[s] = res
            lines += ["%s in %s%s:" % (s.title(), envs[s].name.upper(), (" (Workspace %s)" % creds[s].workspace_id) if s == "planning" else "")] + ["  %-16s %d" % (k, v) for k, v in sorted(res["counts"].items())] + [""]
            for r in res["results"]:
                d = r["detail"]
                rows.append([r["temp_id"], s, envs[s].name, r["kind"], r["name"], r["outcome"], d if not isinstance(d, dict) else "; ".join("%s=%s" % kv for kv in sorted(d.items()) if kv[1] not in (None, ""))])
    finally:
        conn.close()
    _write(a.out, "load_run.csv", lc.redact(_csv(RUN_COLUMNS, rows), secrets))
    failed = sum(r["counts"].get("failed", 0) for r in results.values())
    lines += ["%d item(s) failed. See load_run.csv for each item's outcome and the plain reason." % failed if failed else "No item failed.", "",
              "Attaching Core fields to a custom form is a manual step. Anything on hold or blocked in the plan was not sent (load_plan.csv gives the reasons)."]
    text = lc.redact("\n".join(lines), secrets)
    _write(a.out, "load_run_summary.txt", text + "\n\n" + lc.redact(summary_text(plan, a.base, sent=True), secrets))
    print(text + "\nReports written to %s" % a.out)
    return results


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
        if name == "dry-run":
            p.add_argument("--env", default="sandbox1", choices=lr.ENVIRONMENTS, help="the environment or stage label (default: sandbox1; dev and uat are the Planning stages)")
        if name == "run":
            p.add_argument("--send", action="store_true", help="actually create items in Workfront. Without it nothing is sent")
            p.add_argument("--config", default="config.json", help="the file that holds the Workfront settings and secrets (default: config.json)")
            p.add_argument("--only", default="both", choices=("both", "core", "planning"), help="which system to load (default: both)")
            p.add_argument("--core-env", default="sandbox1", choices=lc.ENVIRONMENTS, help="the Core environment (default: sandbox1)")
            p.add_argument("--planning-stage", default="dev", choices=lr.PLANNING_STAGES, help="the Planning stage, which is a Workspace (default: dev)")
            p.add_argument("--confirm", default=None, help="the exact phrase a real Core production run needs: %s" % lr.PRODUCTION_PHRASE)
    a = ap.parse_args(argv)
    if a.cmd == "config":
        try:
            st = lc.status(lc.read_config(a.config))
        except lc.ConfigError as e:
            sys.exit(str(e))
        yn = lambda b: "configured" if b else "not configured"
        lines = ["Workfront credentials in %s (yes or no only):" % a.config] + ["  Core %-12s %s" % (e, yn(st["core"][e])) for e in lc.ENVIRONMENTS] +                 ["  Planning              %s" % yn(st["planning"]), "  Planning workspace ID %s" % yn(st["planning_workspace"])]
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
    plan = lp.build_plan(inputs["review"], inputs["choices"], include_link_fields=not a.no_link_fields, link_state=inputs.get("link_state"), record_counts=inputs.get("record_counts"), field_facts=inputs.get("field_facts"))
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
