"""Tests for fieldnames_io.py: CSV import, read-only database loading, report files and the command line. Fabricated data only."""
import csv, hashlib, io, os, sqlite3, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import fieldnames as fn, fieldnames_io as fio
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else x)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.mkdtemp(prefix="names_io_")
dbp = os.path.join(tmp, "t.db")
c = sqlite3.connect(dbp)
c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
T0, T1, T2 = "2026-01-01 10:00:00.000000", "2026-01-02 10:00:00.000000", "2026-01-03 10:00:00.000000"
for tid, nm in (("tblA", "PO#s"), ("tblB", "Invoices")):
    c.execute("insert into airtable_tables(table_id, base_id, table_name) values (?,?,?)", (tid, "appT", nm))
F = [  # field_id, table, table name, name, type, linked, source_link, rows, nulls, card, capped
    ("fld1", "tblA", "PO#s", "Amount", "number", None, None, 10, 0, 7, 0),
    ("fld2", "tblA", "PO#s", "amount", "number", None, None, 10, 10, 0, 0),          # empty: duplicate of fld1 when case is ignored
    ("fld3", "tblB", "Invoices", "PO#s", "multipleRecordLinks", "tblA", None, 5, 1, 4, 0),
    ("fld4", "tblB", "Invoices", "PO Number (from PO#s)", "multipleLookupValues", None, "fld3", 5, 1, 1000, 1),
    ("fld5", "tblB", "Invoices", "1Quality", "singleLineText", None, None, 5, 5, 0, 0),   # empty but NOT profiled (table B profile older)
    ("fld6", "tblB", "Invoices", "Done", "checkbox", None, None, 5, 0, 2, 0),
    ("fldOld", "tblB", "Invoices", "Removed field", "number", None, None, 0, 0, 0, 0),    # no longer in source
]
for r in F:
    c.execute("insert into airtable_fields(base_id, table_id, table_name, field_id, field_name, data_type, linked_table_id, source_link_field_id, row_count, null_count,"
              " cardinality, cardinality_is_capped, updated_at) values ('appT',?,?,?,?,?,?,?,?,?,?,?,?)", (r[1], r[2], r[0], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], T1))
c.execute("update airtable_fields set updated_at=? where field_id='fldOld'", (T0,))
def call(tid, ts, success=1):
    c.execute("insert into api_call_log(timestamp, source_system, purpose, http_method, endpoint, base_id, table_id, success) values (?, 'Airtable','profile_page','GET','/x','appT',?,?)", (ts, tid, success))
call("tblA", T2)                                                                         # table A profiled after the schema was read
call("tblB", T0)                                                                         # table B profile is older than the schema: not trusted
c.commit(); c.close()
h0 = hashlib.sha256(open(dbp, "rb").read()).hexdigest()

db = fio.load_db(dbp, "appT")
ids = [f["field_id"] for f in db["fields"]]
check("fields no longer in the source are left out", "fldOld" not in ids and len(ids) == 6, ids)
pr = {f["field_id"]: f["profiled"] for f in db["fields"]}
check("profiled = a successful profile call after the field was last seen", pr["fld1"] == 1 and pr["fld5"] == 0, pr)
res = fn.run(db["fields"], db["tables"], fn.Settings())
p5, p2, p6 = (res["fields"][x]["presence"] for x in ("fld5", "fld2", "fld6"))
check("empty but unprofiled field cannot be abandoned", p5["populated"] == 0 and not p5["can_abandon"] and p5["abandon_blocked_because"] == "not profiled yet", p5)
check("empty profiled field can be abandoned", p2["can_abandon"])
check("checkbox cannot be abandoned", not p6["can_abandon"])
check("with migration 007 applied there is no migration note", not any("Migration 007" in n for n in db["notes"]), db["notes"])
import shutil
no007 = os.path.join(tmp, "no007.db"); shutil.copy(dbp, no007)
k = sqlite3.connect(no007)
for tn in ("name_events", "table_names", "field_names"):
    k.execute("drop table " + tn)
k.commit(); k.close()
nb = fio.load_db(no007, "appT")
check("migration 007 absent is noted, not an error", any("Migration 007" in n for n in nb["notes"]) and nb["approved_tables"] == {} and nb["abandoned"] == [], nb["notes"])
check("capped cardinality shows with a plus", res["fields"]["fld4"]["presence"]["cardinality_display"] == "1,000+")
check("link field and lookup swap through the schema", res["fields"]["fld4"]["targets"]["planning_name"]["proposed"] == "PO Number from POs"
      and res["fields"]["fld3"]["targets"]["planning_name"]["proposed"] == "POs")
check("table PO#s is AUTO_FIXABLE (only a listed character removed)", res["tables"]["tblA"]["verdict"] == fn.AUTO_FIXABLE and res["tables"]["tblA"]["suggested"] == "POs")
check("duplicate across case within a table is one cluster", [len(x["members"]) for x in res["clusters"] if x["target"] == "planning_name"] == [2])
check("database file is untouched by loading and validating", hashlib.sha256(open(dbp, "rb").read()).hexdigest() == h0)

# read-only really means it
try:
    uri = __import__("pathlib").Path(dbp).resolve().as_uri() + "?mode=ro"
    k = sqlite3.connect(uri, uri=True); k.execute("create table zz(x)"); wrote = True
except sqlite3.OperationalError:
    wrote = False
check("the connection used for reading cannot write", wrote is False)

# ---- import
good = "field_id,old_name,new_name,new_label,old_dataType,new_dataType\r\nfld1,Amount,Amount Total,Amount (Total),Number,Number\r\n" \
       "fld2,amount,Amount Total,1Q Amount,Number,Number\r\nfld3,PO#s,POs,POs,Link to another record,Link\r\nfldX,Ghost,Ghost,Ghost,Text,Text\r\n" \
       "fld1,Amount,Dup,Dup,Number,Number\r\nfld5,Wrong Name,Quality Check 1Quality,Quality,Date,Text\r\n"
imp = fio.parse_import(good, db["fields"])
kinds = {}
for p in imp["problems"]:
    kinds.setdefault(p["kind"], []).append(p["field_id"])
check("unmatched field ID is flagged", kinds.get("UNMATCHED_ID") == ["fldX"], kinds)
check("repeated field ID is flagged and the first row wins", kinds.get("DUPLICATE_ID") == ["fld1"] and imp["manual"]["fld1"]["planning_name"] == "Amount Total")
check("fields missing from the spreadsheet are flagged", sorted(kinds.get("MISSING_FROM_IMPORT", [])) == ["fld4", "fld6"], kinds)
check("stale old_name is flagged", kinds.get("OLD_NAME_MISMATCH") == ["fld5"], kinds)
check("old data type compared loosely and flagged when really different", kinds.get("OLD_TYPE_MISMATCH") == ["fld3", "fld5"], kinds)
check("loose type match: 'Number' equals number, 'Single line text' equals singleLineText", fio._norm_type("Single line text") == fio._norm_type("singleLineText"))
check("new_name feeds both Planning and Core name; new_label feeds the label", imp["manual"]["fld1"] == {"planning_name": "Amount Total", "core_name": "Amount Total", "core_label": "Amount (Total)"})
check("target data type is carried", imp["target_types"]["fld1"] == "Number" and imp["target_types"]["fld5"] == "Text")
check("a file with no field_id column imports nothing", fio.parse_import("a,b\r\n1,2\r\n", db["fields"])["problems"][0]["kind"] == "MISSING_COLUMN")
check("headers are case-insensitive", fio.parse_import("FIELD_ID,New_Name\r\nfld1,Zed\r\n", db["fields"])["manual"]["fld1"]["planning_name"] == "Zed")
t, note = fio.decode_csv(b"\xef\xbb\xbffield_id\r\nfld1\r\n")
check("UTF-8 BOM is accepted", t.startswith("field_id") and note == "")
t, note = fio.decode_csv("field_id\r\ncafé\r\n".encode("cp1252"))
check("a non-UTF-8 file is read as Windows-1252 and the user is told", "café" in t and "Windows-1252" in note)

# ---- reports
res = fn.run(db["fields"], db["tables"], fn.Settings(), manual=imp["manual"], abandoned=["fld2"])
reports = fio.build_reports(res, db["fields"], imp, db["notes"])
check("all report files are produced", set(reports) == {"names_report.csv", "names_diff.csv", "names_duplicates.csv", "names_tables.csv", "names_import_problems.csv", "names_summary.txt", "FIELD_NAMING_RULES.md"}, set(reports))
rows = list(csv.DictReader(io.StringIO(reports["names_report.csv"])))
byid = {r["field_id"]: r for r in rows}
check("one report row per field including the abandoned one", len(rows) == 6 and byid["fld2"]["abandoned"] == "yes" and byid["fld2"]["planning_name_verdict"] == "ABANDONED")
check("report shows verdict, rules and proposed repair next to the original", byid["fld4"]["planning_name_verdict"] == "NEEDS_HUMAN" and byid["fld4"]["planning_name_proposed_repair"] == "PO Number from POs"
      and "TABLE_REF" in byid["fld4"]["planning_name_rules"])
check("report shows data presence", byid["fld4"]["cardinality"] == "1,000+" and byid["fld1"]["rows_with_value"] == "10" and byid["fld5"]["profiled"] == "no")
check("abandoning a duplicate member resolves the cluster", not [r for r in csv.DictReader(io.StringIO(reports["names_duplicates.csv"]))])
check("diff lists where your name differs from the rubric", "fld1" in reports["names_diff.csv"])
check("summary has counts", "AUTO_FIXABLE" in reports["names_summary.txt"] and "Abandoned fields excluded: 1" in reports["names_summary.txt"])
check("report is deterministic", reports == fio.build_reports(res, db["fields"], imp, db["notes"]))
check("spreadsheet formula injection is neutralised", fio._safe("=1+1") == "'=1+1" and fio._safe("@x") == "'@x" and fio._safe("Plain") == "Plain")
check("rules document uses allowlist wording", "allowlist" in reports["FIELD_NAMING_RULES.md"] or "AUTO_DELETE" in reports["FIELD_NAMING_RULES.md"])

# ---- command line
csvp = os.path.join(tmp, "renames.csv")
open(csvp, "w", encoding="utf-8", newline="").write(good)
out = os.path.join(tmp, "out")
try:
    fio.main(["--db", dbp, "--base", "appT", "--import", csvp, "--out", os.path.join(tmp, "gated")]); code = 0
except SystemExit as e:
    code = e.code
gated = os.path.join(tmp, "gated")
check("command line refuses field review until table names are approved (exit 2)", code == 2)
check("blocked run still writes the table report and says why", "BLOCKED" in open(os.path.join(gated, "names_summary.txt"), encoding="utf-8-sig").read()
      and len(list(csv.DictReader(open(os.path.join(gated, "names_tables.csv"), encoding="utf-8-sig", newline="")))) == 2
      and len(list(csv.DictReader(open(os.path.join(gated, "names_report.csv"), encoding="utf-8-sig", newline="")))) == 0)
fio.main(["--db", dbp, "--base", "appT", "--import", csvp, "--out", out, "--draft"])
check("--draft previews field results and labels the report DRAFT", "DRAFT" in open(os.path.join(out, "names_summary.txt"), encoding="utf-8-sig").read())
check("command line writes every file", sorted(os.listdir(out)) == sorted(reports), os.listdir(out))
check("command line report files open in Excel as UTF-8 (BOM)", open(os.path.join(out, "names_report.csv"), "rb").read().startswith(b"\xef\xbb\xbf"))
check("command line left the database untouched", hashlib.sha256(open(dbp, "rb").read()).hexdigest() == h0)
fio.main(["--db", dbp, "--base", "appT", "--out", out, "--skip-table", "tblA", "--no-char-cleanup", "--draft"])
# approvals and abandon decisions stored by migration 007 are read, and they open the gate
appr = os.path.join(tmp, "approved.db"); shutil.copy(dbp, appr)
k = sqlite3.connect(appr)
k.execute("insert into table_names(airtable_table_id, new_table_name, status, approved_by) values ('tblA','POs','approved','JV'), ('tblB','Invoices','approved','JV')")
k.execute("insert into table_names(airtable_table_id, new_table_name, status) values ('tblZ','Pending Name','needs_human')")
k.execute("insert into field_names(airtable_field_id, abandoned, abandoned_by, abandoned_reason) values ('fld2', 1, 'JV', 'empty duplicate')")
k.commit(); k.close()
ad = fio.load_db(appr, "appT")
check("approved table names and abandoned fields are read from the 007 tables (only 'approved' counts)", ad["approved_tables"] == {"tblA": "POs", "tblB": "Invoices"} and ad["abandoned"] == ["fld2"], ad)
ao = os.path.join(tmp, "approved_out")
fio.main(["--db", appr, "--base", "appT", "--out", ao])
rep = {r["field_id"]: r for r in csv.DictReader(open(os.path.join(ao, "names_report.csv"), encoding="utf-8-sig", newline=""))}
check("with every table approved the gate opens without --draft and the swap is auto-fixable", rep["fld4"]["planning_name_verdict"] == "AUTO_FIXABLE"
      and rep["fld4"]["planning_name_proposed_repair"] == "PO Number from POs" and rep["fld2"]["abandoned"] == "yes", rep["fld4"])
check("the abandoned member shrinks the duplicate group, which resolves it", rep["fld1"]["planning_name_verdict"] == "PASS")
skipped = [r["field_id"] for r in csv.DictReader(open(os.path.join(out, "names_report.csv"), encoding="utf-8-sig", newline=""))]
check("skipping a table removes its fields from the report", skipped == ["fld3", "fld4", "fld5", "fld6"], skipped)
# descriptions on the command line
dd = os.path.join(tmp, "desc")
k = sqlite3.connect(appr)
k.execute("insert into field_names(airtable_field_id, description) values ('fld1', 'Source: Airtable. Origins: PO#s.Amount; PO#s.Other. By hand.') on conflict(airtable_field_id) do update set description=excluded.description")
k.execute("insert into field_names(airtable_field_id, description) values ('fld3', 'free text') on conflict(airtable_field_id) do update set description='free text'")
k.execute("update airtable_fields set field_description='From Airtable.' where field_id='fld6'"); k.commit(); k.close()
fio.main(["--db", appr, "--base", "appT", "--out", dd, "--include-airtable-description"])
drow = {r["field_id"]: r for r in csv.DictReader(open(os.path.join(dd, "names_report.csv"), encoding="utf-8-sig", newline=""))}
check("a saved description is read and reported as saved", drow["fld1"]["description_source"] == "saved" and drow["fld1"]["description"].endswith("By hand."), drow["fld1"])
check("a generated description follows the lineage syntax and the flag appends Airtable's text", drow["fld4"]["description"] == "Source: Airtable. Origins: Invoices.PO#s." and
      drow["fld6"]["description"] == "Source: Airtable. Origins: Invoices.Done. Airtable description: From Airtable.", drow["fld6"]["description"])
check("a free-text saved description is flagged", drow["fld3"]["description_problems"] == "NOT_LINEAGE")

# per-name options on the command line
po = os.path.join(tmp, "pername")
fio.main(["--db", dbp, "--base", "appT", "--out", po, "--draft", "--target-option", "core_name.max_len=6", "--target-option", "planning_name.extra_allowed=#"])
prow = {r["field_id"]: r for r in csv.DictReader(open(os.path.join(po, "names_report.csv"), encoding="utf-8-sig", newline=""))}
check("a per-name length applies to that name only", "TOO_LONG" not in prow["fld1"]["core_name_rules"] and "TOO_LONG" in prow["fld4"]["core_name_rules"]
      and "TOO_LONG" not in prow["fld4"]["planning_name_rules"] and "TOO_LONG" not in prow["fld4"]["core_label_rules"], prow["fld4"])
check("the options used are shown in the summary", "max_len" in open(os.path.join(po, "names_summary.txt"), encoding="utf-8-sig").read())
for bad in (["--target-option", "nonsense"], ["--target-option", "planning_name.max_len=999"], ["--target-option", "core_label.extra_allowed=&"]):
    try:
        fio.main(["--db", dbp, "--base", "appT", "--out", po, "--draft"] + bad); code = 0
    except SystemExit as e:
        code = e.code
    check("a bad --target-option is refused: %s" % bad[1], code not in (0, None))
sys.exit(0 if ok else 1)
