#!/usr/bin/env python3
"""The migration loader: build the load plan and dry-run it. Standard library only.

    python loader.py plan    --db db/migration.db --base appXXXXXXXXXXXXXX [--out out/loader]
    python loader.py dry-run --db db/migration.db --base appXXXXXXXXXXXXXX [--env sandbox|production] [--out out/loader]
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
import loadconfig as lc
import loadplan as lp
import loadrun as lr

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


def summary_text(plan, base_id, title="Load plan"):
    s = plan["summary"]
    L = ["%s for %s" % (title, base_id), "", "Items: %d" % s["total"]]
    L += ["  %-8s %d" % (k, v) for k, v in sorted(s["by_status"].items())]
    L += ["", "By system, kind and status:"] + ["  %s: %d" % (k, v) for k, v in s["by_system_kind_status"].items()]
    if s["reasons"]:
        L += ["", "Why items are not ready (count of items):"] + ["  %d x %s" % (v, k) for k, v in s["reasons"].items()]
    L += ["", "Tables out of scope (skipped): %d" % s["out_of_scope_tables"], "",
          "Nothing was sent to Workfront. Creating items is not implemented yet (docs/LOADER_PLAN.md)."]
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


def main(argv=None):
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
        if name != "plan":
            p.add_argument("--env", default="sandbox", choices=("sandbox", "production"), help="the environment (default: sandbox)")
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
        sys.exit("Creating items in Workfront is not implemented yet: the request shapes for Workfront Planning and Workfront Core are not verified and no credentials are "
                 "configured. Use 'plan' and 'dry-run'. See docs/LOADER_PLAN.md.")
    conn = _open(a.db)
    try:
        inputs = store.loader_inputs(conn, a.base)
    except store.StoreError as e:
        sys.exit(e.message)
    finally:
        conn.close()
    plan = lp.build_plan(inputs["review"], inputs["choices"], include_link_fields=not a.no_link_fields)
    _write(a.out, "load_plan.csv", plan_csv(plan))
    text = summary_text(plan, a.base)
    if a.cmd == "plan":
        _write(a.out, "load_plan_summary.txt", text)
        print(text + "Reports written to %s" % a.out)
        return plan
    env = lr.resolve_environment(a.env, None, dry_run=True)
    res = lr.run_plan(plan["items"], lr.MemoryState(), {"planning": lr.DryRunTransport("planning"), "core": lr.DryRunTransport("core")}, env, dry_run=True)
    _write(a.out, "load_dry_run.csv", dry_run_csv(res))
    lines = ["Dry run in %s (nothing was sent)" % env.name.upper(), ""] + ["  %-16s %d" % (k, v) for k, v in sorted(res["counts"].items())]
    _write(a.out, "load_dry_run_summary.txt", "\n".join(lines) + "\n\n" + text)
    print("\n".join(lines) + "\n\nReports written to %s" % a.out)
    return res


if __name__ == "__main__":
    main()
