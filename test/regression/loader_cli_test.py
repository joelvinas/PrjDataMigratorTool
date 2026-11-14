"""End-to-end test of loader.py (plan and dry-run) against a throwaway database built through the real store functions. Fabricated names only."""
import base64, csv, hashlib, io, json, os, sqlite3, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import fieldnames_store as st, loader
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.mkdtemp(prefix="loader_cli_")
dbp = os.path.join(tmp, "l.db")
c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row; c.execute("PRAGMA foreign_keys = ON")
c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
T1 = "2026-01-01 10:00:00.000000"
for tid, nm, base in (("tA", "Orders", "appL"), ("tB", "PO#s", "appL"), ("tX", "Loose", "appM")):
    c.execute("insert into airtable_tables(table_id, base_id, table_name) values (?,?,?)", (tid, base, nm))
CH = json.dumps([{"id": "selA", "name": "Open", "color": "x"}, {"id": "selB", "name": "Café Closed", "color": "y"}, {"id": "selC", "name": "Done", "color": "z"}])
F = [("fA1", "tA", "Orders", "Order Name", "singleLineText", None, None, "appL"), ("fA2", "tA", "Orders", "Status", "singleSelect", None, CH, "appL"),
     ("fA3", "tA", "Orders", "Total", "formula", None, None, "appL"), ("fA4", "tA", "Orders", "PO", "multipleRecordLinks", "tB", None, "appL"),
     ("fB1", "tB", "PO#s", "PO Number", "singleLineText", None, None, "appL"), ("fX1", "tX", "Loose", "Anything", "singleLineText", None, None, "appM")]
for r in F:
    c.execute("insert into airtable_fields(base_id, table_id, table_name, field_id, field_name, data_type, linked_table_id, choices_json, updated_at) values (?,?,?,?,?,?,?,?,?)",
              (r[7], r[1], r[2], r[0], r[3], r[4], r[5], r[6], T1))
c.commit()

# ---- decisions, made through the same functions the page uses
st.approve_tables(c, "appL", "JV", mode="unchanged"); st.approve_tables(c, "appL", "JV", mode="auto")
st.set_scope(c, "appL", "JV", [{"table_id": "tA", "decision": "in", "target": "planning_and_core"}, {"table_id": "tB", "decision": "in", "target": "planning_only"}])
csvt = "field_id,old_name,new_name,new_label,old_dataType,new_dataType\r\nfA1,Order Name,Order Name,Order Name,Single line text,Text\r\nfA2,Status,Status,Status,Single select,Single select\r\n" \
       "fA3,Total,Total,Total,Formula,Number\r\nfA4,PO,PO,PO,Link,Link\r\nfB1,PO Number,PO Number,PO Number,Single line text,Text\r\n"
st.import_names(c, "appL", "JV", csv_base64=base64.b64encode(csvt.encode()).decode())
st.approve_fields(c, "appL", "JV", mode="pass_and_auto")
c.close()
h0 = hashlib.sha256(open(dbp, "rb").read()).hexdigest()

out = os.path.join(tmp, "out")
plan = loader.main(["plan", "--db", dbp, "--base", "appL", "--out", out])
check("the database file is untouched (opened read-only)", hashlib.sha256(open(dbp, "rb").read()).hexdigest() == h0)
check("the plan CSV opens in Excel as UTF-8", open(os.path.join(out, "load_plan.csv"), "rb").read().startswith(b"\xef\xbb\xbf"))
rows = {r["temp_id"]: r for r in csv.DictReader(open(os.path.join(out, "load_plan.csv"), encoding="utf-8-sig", newline=""))}
check("record types use the approved table names and are ready", rows["P-T-tA"]["status"] == "ready" and rows["P-T-tB"]["status"] == "ready" and rows["P-T-tB"]["name"] == "POs", rows.get("P-T-tB"))
check("only the Planning plus Core table has a Core form, on hold", "C-T-tA" in rows and "C-T-tB" not in rows and rows["C-T-tA"]["status"] == "hold")
check("a plain field is ready in Planning, and in Core for the Core table only", rows["P-F-fA1"]["status"] == "ready" and rows["C-F-fA1"]["status"] == "ready" and not any(k.startswith("C-F-fB") for k in rows)
      and rows["P-F-fB1"]["data_type"] == "Text")
check("the formula and the link are on hold with their reasons", rows["P-F-fA3"]["status"] == "hold" and "derived" in rows["P-F-fA3"]["reasons"]
      and rows["P-F-fA4"]["status"] == "hold" and "second pass" in rows["P-F-fA4"]["reasons"])
check("select options come from the schema in Airtable order, with positions", [(rows[k]["name"], rows[k]["position"]) for k in ("P-O-fA2-selA", "P-O-fA2-selB", "P-O-fA2-selC")] == [("Open", "0"), ("Café Closed", "1"), ("Done", "2")])
check("the migration's own Core-link fields are in the plan for the Core table", rows["S-P-tA-core-object-id"]["source"] == "migration" and rows["S-C-tA-sync-status"]["status"] == "ready")
check("the plan is ordered parents first in the file", list(rows).index("P-T-tA") < list(rows).index("P-F-fA1") < list(rows).index("P-O-fA2-selA"))
summ = open(os.path.join(out, "load_plan_summary.txt"), encoding="utf-8-sig").read()
check("the summary counts items and says nothing was sent", "Items:" in summ and "Nothing was sent to Workfront" in summ and "derived field" in summ)
check("plan() returns the same items the file holds", len(plan["items"]) == len(rows))

# ---- dry run
dry = loader.main(["dry-run", "--db", dbp, "--base", "appL", "--out", out])
check("a dry run defaults to the sandbox and describes every ready item", dry["environment"] == "sandbox1" and dry["dry_run"] is True
      and dry["counts"]["would_create"] == len([r for r in rows.values() if r["status"] == "ready"]), dry["counts"])
dr = list(csv.DictReader(open(os.path.join(out, "load_dry_run.csv"), encoding="utf-8-sig", newline="")))
check("the dry-run file lists each item's outcome, and items that are not ready are not described", {r["outcome"] for r in dr} == {"would_create", "not_ready"} and
      all("intent only" in r["detail"] for r in dr if r["outcome"] == "would_create"))
check("the dry-run summary names the environment", open(os.path.join(out, "load_dry_run_summary.txt"), encoding="utf-8-sig").read().startswith("Dry run in SANDBOX1"))
loader.main(["dry-run", "--db", dbp, "--base", "appL", "--out", out, "--env", "production"])
check("a production DRY run is allowed (it changes nothing) and is labelled", open(os.path.join(out, "load_dry_run_summary.txt"), encoding="utf-8-sig").read().startswith("Dry run in PRODUCTION"))
check("the database is still untouched after the dry runs", hashlib.sha256(open(dbp, "rb").read()).hexdigest() == h0)

# ---- the guards
for argv, why in ((["run", "--db", dbp, "--base", "appL"], "run"), (["run", "--db", dbp, "--base", "appL", "--env", "production"], "run in production")):
    try:
        loader.main(argv); msg = None
    except SystemExit as e:
        msg = str(e.code)
    check("'%s' is refused: creating is not implemented yet" % why, msg is not None and "not implemented" in msg, msg)
try:
    loader.main(["plan", "--db", dbp, "--base", "appNOBASE0001", "--out", out]); msg = None
except SystemExit as e:
    msg = str(e.code)
check("an unknown base is refused with a plain message", msg is not None and "No fields are stored" in msg, msg)
try:
    loader.main(["plan", "--db", os.path.join(tmp, "nope.db"), "--base", "appL", "--out", out]); msg = None
except SystemExit as e:
    msg = str(e.code)
check("a missing database is refused", msg is not None and "No such database" in msg)

# ---- nothing approved yet: everything is blocked, with the reasons
plan2 = loader.main(["plan", "--db", dbp, "--base", "appM", "--out", os.path.join(tmp, "out2")])
check("a base with nothing decided has only blocked items, each with its reasons", {i["status"] for i in plan2["items"]} == {"blocked"} and plan2["items"][0]["reasons"]
      and any("scope is not decided" in r for r in plan2["items"][0]["reasons"]))
plan3 = loader.main(["plan", "--db", dbp, "--base", "appL", "--out", os.path.join(tmp, "out3"), "--no-link-fields"])
check("the migration's link fields can be left out", not any(i["source"] == "migration" for i in plan3["items"]) and any(i["source"] == "migration" for i in plan["items"]))
sys.exit(0 if ok else 1)
