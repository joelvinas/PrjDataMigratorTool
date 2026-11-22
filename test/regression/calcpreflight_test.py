"""Tests for calcpreflight.py (the calculated fields preflight, report, drift check and approval). Fabricated tables, fields and names; a database made from the schema library in a
temporary folder; no server and no network."""
import ast, os, shutil, sqlite3, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
import calcpreflight as cpf
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:500])

tmp = tempfile.mkdtemp(prefix="calcpre_")
conn = sqlite3.connect(os.path.join(tmp, "t.db"))
conn.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
B, SP, ST, ENV = "appFake01", "tblSpecial", "tblStore", "sandbox1"

def field(fid, name, table, dtype="number", formula=None, rtype=None, valid=None):
    conn.execute("INSERT INTO airtable_fields (base_id, table_id, table_name, field_id, field_name, data_type, formula_text, result_type, is_valid) VALUES (?,?,?,?,?,?,?,?,?)",
                 (B, table, "T", fid, name, dtype, formula, rtype, valid))

def named(fid, core_name, status="approved", ctype=None, cdisp=None, label=None):
    conn.execute("INSERT OR REPLACE INTO field_names (airtable_field_id, core_name, core_name_status, core_label, core_target_type, core_display_type) VALUES (?,?,?,?,?,?)", (fid, core_name, status, label, ctype, cdisp))

def core(fid, name, pid, dtype="NMBR", env=ENV):
    conn.execute("INSERT INTO core_field_crosswalk (environment, airtable_field_id, workfront_name, workfront_data_type, workfront_display_type, parameter_id) VALUES (?,?,?,?, 'TEXT', ?)", (env, fid, name, dtype, pid))

# Special Order Sales
field("fldBase", "Base Price", SP); core("fldBase", "base_price", "P1")
field("fldComm", "Commission", SP, "formula", "{fldBase}*0.15", "number", 1); named("fldComm", "SalesPersonCommission")
field("fldDep", "Comm Plus One", SP, "formula", "{fldComm}+1", "number", 1); named("fldDep", "CommPlusOne")
field("fldBad", "Months", SP, "formula", "DATETIME_DIFF({fldBase},{fldBase},'months')", "number", 1); named("fldBad", "BadOne")
field("fldDepBad", "Reads Bad", SP, "formula", "{fldBad}+1", "number", 1); named("fldDepBad", "DepBad")
field("fldMiss", "Reads Ghost", SP, "formula", "{fldGhost}+1", "number", 1); named("fldMiss", "MissOne")
field("fldNoName", "No Name", SP, "formula", "{fldBase}+2", "number", 1)
field("fldLoop1", "Loop One", SP, "formula", "{fldLoop2}+1", "number", 1); named("fldLoop1", "LoopOne")
field("fldLoop2", "Loop Two", SP, "formula", "{fldLoop1}+1", "number", 1); named("fldLoop2", "LoopTwo")
field("fldBroken", "Broken", SP, "formula", "{fldBase}+1", "number", 0); named("fldBroken", "BrokenOne")
field("fldGone", "Abandoned", SP, "formula", "{fldBase}+3", "number", 1); named("fldGone", "GoneOne")
conn.execute("UPDATE field_names SET abandoned=1 WHERE airtable_field_id='fldGone'")
# In-store Sales
field("fldTot", "Total", ST); core("fldTot", "total", "P2")
field("fldComm2", "Commission", ST, "formula", "IF({fldTot}<200,{fldTot}*0.03,{fldTot}*0.07)", "number", 1); named("fldComm2", "SalesPersonCommission")
conn.commit()

def rows(run_id):
    return {r[0]: r[1:] for r in conn.execute("SELECT formula_field_id, status, reason, translated_expression, push_order, target_name, workfront_data_type, workfront_display_type, reuse_parameter_id "
                                              "FROM calc_preflight_items WHERE run_id=?", (run_id,))}

# ---- the first run
r1 = cpf.run(conn, ENV, B, SP, "CAT1", "Special Order Sales", "JV", now="2026-11-22 10:00:00")
R = rows(r1)
check("the abandoned formula is left out; the others are all there", sorted(R) == ["fldBad", "fldBroken", "fldComm", "fldDep", "fldDepBad", "fldLoop1", "fldLoop2", "fldMiss", "fldNoName"], sorted(R))
c = R["fldComm"]
check("the commission formula is translatable with its expression and type", c[0] == "TRANSLATABLE" and c[2] == "{DE:base_price}*0.15" and c[4] == "SalesPersonCommission" and c[5] == "NMBR", c)
check("a translatable item notes the displayType that is not known yet", "displayType" in c[1] and c[6] is None, c)
check("a formula that reads a formula is ordered after it", R["fldDep"][0] == "TRANSLATABLE" and R["fldDep"][3] > c[3] and R["fldDep"][2] == "{DE:SalesPersonCommission}+1", (R["fldDep"], c[3]))
check("an unsupported unit is BLOCKED_UNSUPPORTED_FUNCTION", R["fldBad"][0] == "BLOCKED_UNSUPPORTED_FUNCTION" and "months" in R["fldBad"][1] and R["fldBad"][2] is None, R["fldBad"])
check("reading a blocked formula is BLOCKED_DEPENDENCY, naming it", R["fldDepBad"][0] == "BLOCKED_DEPENDENCY" and "Months" in R["fldDepBad"][1], R["fldDepBad"])
check("a reference with no target is BLOCKED_MISSING_REFERENCE", R["fldMiss"][0] == "BLOCKED_MISSING_REFERENCE" and "fldGhost" in R["fldMiss"][1], R["fldMiss"])
check("a formula with no approved Core name is BLOCKED_MISSING_REFERENCE", R["fldNoName"][0] == "BLOCKED_MISSING_REFERENCE" and "Core name" in R["fldNoName"][1], R["fldNoName"])
check("a loop is BLOCKED_DEPENDENCY, with no place in the order", R["fldLoop1"][0] == R["fldLoop2"][0] == "BLOCKED_DEPENDENCY" and R["fldLoop1"][3] is None, (R["fldLoop1"], R["fldLoop2"]))
check("a formula Airtable reports as invalid is blocked", R["fldBroken"][0] == "BLOCKED_UNSUPPORTED_FUNCTION" and "not valid" in R["fldBroken"][1], R["fldBroken"])
run1 = conn.execute("SELECT state, run_by, run_at, category_id, category_name FROM calc_preflight_runs WHERE run_id=?", (r1,)).fetchone()
check("the run records who, when, the form and starts as a preflight", run1 == ("preflight", "JV", "2026-11-22 10:00:00", "CAT1", "Special Order Sales"), run1)

# ---- the report
rp = cpf.report(conn, r1)
check("counts per status add up", rp["counts"] == {"TRANSLATABLE": 2, "BLOCKED_MISSING_REFERENCE": 2, "BLOCKED_UNSUPPORTED_FUNCTION": 2, "BLOCKED_DEPENDENCY": 3} and rp["total"] == 9, rp["counts"])
check("the blocked lists are kept apart by status", {k: sorted(d["field_id"] for d in v) for k, v in rp["blocked"].items()} ==
      {"BLOCKED_MISSING_REFERENCE": ["fldMiss", "fldNoName"], "BLOCKED_UNSUPPORTED_FUNCTION": ["fldBad", "fldBroken"], "BLOCKED_DEPENDENCY": ["fldDepBad", "fldLoop1", "fldLoop2"]}, rp["blocked"].keys())
check("the translatable list shows each expression", [(d["field_id"], d["expression"]) for d in rp["translatable"]] == [("fldComm", "{DE:base_price}*0.15"), ("fldDep", "{DE:SalesPersonCommission}+1")], rp["translatable"])
check("the function inventory comes from the data", [(f["name"], f["translated"]) for f in rp["functions"]] == [("DATETIME_DIFF", True)] and rp["unsupported_functions"] == [], rp["functions"])
check("the report uses the saved snapshot, with field names", rp["translatable"][0]["field_name"] == "Commission")

# ---- the same input gives the same rows
r1b = cpf.run(conn, ENV, B, SP, "CAT1", "Special Order Sales", "JV", now="2026-11-22 10:05:00")
check("a repeat run gives identical items", rows(r1b) == R)
check("the earlier run is superseded", conn.execute("SELECT state FROM calc_preflight_runs WHERE run_id=?", (r1,)).fetchone()[0] == "superseded")
check("the fingerprints of the two runs match", conn.execute("SELECT COUNT(DISTINCT schema_hash), COUNT(DISTINCT crosswalk_hash) FROM calc_preflight_runs WHERE run_id IN (?,?)", (r1, r1b)).fetchone() == (1, 1))

# ---- approval, and drift
check("nothing has drifted yet", cpf.drift(conn, r1b) == [])
for who, why, word in (("", "ok", "initials"), ("JV", "", "reason")):
    try:
        cpf.approve(conn, r1b, who, why); e = ""
    except cpf.PreflightError as ex:
        e = ex.message
    check("approval needs %s" % word, word in e, e)
conn.execute("UPDATE airtable_fields SET formula_text='{fldBase}*0.20' WHERE field_id='fldComm'"); conn.commit()
check("a changed formula is drift", any("formula field changed" in d for d in cpf.drift(conn, r1b)), cpf.drift(conn, r1b))
try:
    cpf.approve(conn, r1b, "JV", "looks right"); e = ""
except cpf.PreflightError as ex:
    e = ex.message
check("approval is refused after drift, asking for a new preflight", "new preflight" in e and conn.execute("SELECT state FROM calc_preflight_runs WHERE run_id=?", (r1b,)).fetchone()[0] == "preflight", e)
r2 = cpf.run(conn, ENV, B, SP, "CAT1", "Special Order Sales", "JV", now="2026-11-22 11:00:00")
check("the new run reads the new formula", rows(r2)["fldComm"][2] == "{DE:base_price}*0.20")
ap = cpf.approve(conn, r2, "JV", "Reviewed the expressions")
st = conn.execute("SELECT state, approved_by, approved_at, approval_reason FROM calc_preflight_runs WHERE run_id=?", (r2,)).fetchone()
check("approval records who, when and why", st[0] == "approved" and st[1] == "JV" and st[2] and st[3] == "Reviewed the expressions" and ap["state"] == "approved", st)
try:
    cpf.approve(conn, r2, "JV", "again"); e = ""
except cpf.PreflightError as ex:
    e = ex.message
check("an approved run cannot be approved again", "approved" in e, e)
conn.execute("INSERT INTO core_field_crosswalk (environment, airtable_field_id, workfront_name, workfront_data_type, workfront_display_type, parameter_id) VALUES (?,?,?,?,?,?)", (ENV, "fldBase", "base_price_alt", "NMBR", "TEXT", "P7")); conn.commit()
check("a crosswalk change for a field the run uses is drift", any("crosswalk" in d for d in cpf.drift(conn, r2)), cpf.drift(conn, r2))
conn.execute("DELETE FROM core_field_crosswalk WHERE parameter_id='P7'"); conn.commit()
check("and goes away when it is undone", cpf.drift(conn, r2) == [])
conn.execute("INSERT INTO core_field_crosswalk (environment, airtable_field_id, workfront_name, workfront_data_type, workfront_display_type, parameter_id) VALUES (?,?,?,?,?,?)", (ENV, "fldOther", "unrelated", "TEXT", "TEXT", "P8")); conn.commit()
check("a crosswalk row the run does not touch is not drift", cpf.drift(conn, r2) == [])

# ---- one shared field on two forms
conn.execute("INSERT INTO calc_formula_crosswalk (environment, category_id, parameter_id, ctgypa_id, airtable_field_id, airtable_table_id, workfront_name, expression) VALUES (?,?,?,?,?,?,?,?)",
             (ENV, "CAT1", "P9", "CP1", "fldComm", SP, "SalesPersonCommission", "{DE:base_price}*0.20")); conn.commit()
r3 = cpf.run(conn, ENV, B, ST, "CAT2", "In Store Sales", "JV")
c2 = rows(r3)["fldComm2"]
check("the second form reuses the shared field and carries its own expression", c2[0] == "TRANSLATABLE" and c2[2] == "IF({DE:total}<200,{DE:total}*0.03,{DE:total}*0.07)" and c2[7] == "P9" and "shared" in c2[1], c2)
check("the two forms have different expressions for one field name", rows(r3)["fldComm2"][4] == "SalesPersonCommission" == rows(cpf.run(conn, ENV, B, SP, "CAT1", "x", "JV"))["fldComm"][4] and c2[2] != "{DE:base_price}*0.20")
r4 = cpf.run(conn, ENV, B, ST, "CAT1", "Special Order Sales", "JV")
c3 = rows(r4)["fldComm2"]
check("the same field twice on one form is a conflict", c3[0] == "BLOCKED_MISSING_REFERENCE" and c3[1].startswith(cpf.CONFLICT) and "already on this form" in c3[1], c3)
r5 = cpf.run(conn, ENV, B, SP, "CAT1", "Special Order Sales", "JV")
check("a field already placed on this form for itself is only noted", rows(r5)["fldComm"][0] == "TRANSLATABLE" and "Already placed" in rows(r5)["fldComm"][1], rows(r5)["fldComm"])

# ---- data type conflict and duplicate names
field("fldMm", "Mismatch", "tblMore", "formula", "{fldBase}+1", "number", 1); named("fldMm", "ExistingText")
core("fldElse", "ExistingText", "P20", "TEXT")
field("fldD1", "Dup One", "tblMore", "formula", "{fldBase}+1", "number", 1); named("fldD1", "SameName")
field("fldD2", "Dup Two", "tblMore", "formula", "{fldBase}+2", "number", 1); named("fldD2", "SameName")
field("fldTx", "Text One", "tblMore", "formula", '"a"&"b"', "singleLineText", 1); named("fldTx", "TextOne", cdisp="TEXT")
conn.commit()
r6 = cpf.run(conn, ENV, B, "tblMore", "CAT3", "More", "JV")
R6 = rows(r6)
check("a data type that differs from the existing field is a conflict", R6["fldMm"][0] == "BLOCKED_MISSING_REFERENCE" and "TEXT" in R6["fldMm"][1] and "NMBR" in R6["fldMm"][1], R6["fldMm"])
check("two formulas of one table with one name both conflict", R6["fldD1"][0] == R6["fldD2"][0] == "BLOCKED_MISSING_REFERENCE" and "share the name" in R6["fldD1"][1], (R6["fldD1"], R6["fldD2"]))
check("a person's displayType is used, and the dataType comes from the result type", R6["fldTx"][0] == "TRANSLATABLE" and R6["fldTx"][5] == "TEXT" and R6["fldTx"][6] == "TEXT", R6["fldTx"])

# ---- refusals
for args, word in (((conn, "nowhere", B, SP, "CAT1", "x", "JV"), "environment"), ((conn, ENV, B, SP, "bad id!", "x", "JV"), "custom form"), ((conn, ENV, B, "tblNone", "CAT1", "x", "JV"), "no formula"),
                   ((conn, ENV, B, SP, "CAT1", "x", ""), "initials")):
    try:
        cpf.run(*args); e = ""
    except cpf.PreflightError as ex:
        e = ex.message
    check("refused: " + word, word in e, e)
try:
    cpf.report(conn, 9999); e = ""
except cpf.PreflightError as ex:
    e = ex.message
check("an unknown run is refused", "no such" in e)

# ---- the module makes no network call, no model and no random number
imported = set()
for node in ast.walk(ast.parse(open(os.path.join(ROOT, "src", "calcpreflight.py"), encoding="utf-8").read())):
    if isinstance(node, ast.Import):
        imported |= {a.name.split(".")[0] for a in node.names}
    elif isinstance(node, ast.ImportFrom):
        imported.add((node.module or "").split(".")[0])
check("the preflight imports no HTTP, network or random module", imported <= {"hashlib", "json", "re", "datetime", "calclex", "calcxlate", "loadconfig"}, imported)

conn.close()
shutil.rmtree(tmp, ignore_errors=True)
print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
