"""Tests for calcpush.py (the push of calculated fields to Workfront Core) against a FAKE Workfront (calcfake.py) and a database made from the schema library in a temporary folder.
Fabricated tables, fields, forms and names; a fake key. Covers: the order of calls, reuse of an existing field with the right type, a wrong type failing only its own formula, the
whole existing form collection sent back with every setting, retry of only the failed items, the call log, a refused key, a cancel, and the refusals that send nothing."""
import ast, json, os, shutil, sqlite3, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calcpreflight as cpf, calcpush as cp, loadconfig as lc, workfronthttp as wh
from calcfake import FakeWorkfront, Opener
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:500])

KEY, API, B, TBL, ENV = "fakeCOREkey0123456789abcdef", "/attask/api/v21.0", "appFake01", "tblSpecial", "sandbox1"
tmp = tempfile.mkdtemp(prefix="calcpush_")
conn = sqlite3.connect(os.path.join(tmp, "t.db"))
conn.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
def field(fid, name, dtype="number", formula=None, rtype=None):
    conn.execute("INSERT INTO airtable_fields (base_id, table_id, table_name, field_id, field_name, data_type, formula_text, result_type, is_valid) VALUES (?,?,?,?,?,?,?,?,1)", (B, TBL, "Special Orders", fid, name, dtype, formula, rtype))
def named(fid, core):
    conn.execute("INSERT INTO field_names (airtable_field_id, core_name, core_name_status) VALUES (?,?, 'approved')", (fid, core))
field("fldBase", "Base Price"); conn.execute("INSERT INTO core_field_crosswalk (environment, airtable_field_id, workfront_name, workfront_data_type, workfront_display_type, parameter_id) VALUES (?,?,?,?,?,?)", (ENV, "fldBase", "base_price", "NMBR", "TEXT", "PB"))
field("fldComm", "Commission", "formula", "{fldBase}*0.15", "number"); named("fldComm", "SalesPersonCommission")
field("fldDep", "Comm Plus One", "formula", "{fldComm}+1", "number"); named("fldDep", "CommPlusOne")
field("fldTot", "Total", "formula", "{fldBase}+1", "number"); named("fldTot", "TotalOne")
conn.commit()
EXISTING = [{"parameterID": "X1", "displayOrder": 1, "isRequired": True, "securityLevel": "E", "parameterGroupID": "G1", "rowShared": True, "viewSecurityLevel": "V", "configurations": {"k": "v"}},
            {"parameterID": "X2", "displayOrder": 5, "isRequired": False, "securityLevel": "A", "parameterGroupID": "G2"}]

def approved_run(category="CAT1"):
    rid = cpf.run(conn, ENV, B, TBL, category, "Special Order Sales", "JV")
    cpf.approve(conn, rid, "JV", "reviewed")
    return rid
def world(assocs=EXISTING, category="CAT1"):
    f = FakeWorkfront(KEY); f.add_form(category, "Special Order Sales", [dict(a) for a in assocs]); return f
def client(fake):
    creds = lc.credentials({"workfront": {"core": {ENV: {"base_url": "https://acme.sb01.workfront.com", "api_path": API, "api_key": KEY}}}}, "core", ENV)
    return wh.Http(creds, {KEY}, opener=Opener(fake, API), sleep=lambda s: None)
def steps(run_id): return {r[0]: r[1:] for r in conn.execute("SELECT i.formula_field_id, p.step_done, p.last_error FROM calc_preflight_items i JOIN calc_push_items p ON p.item_id=i.item_id WHERE i.run_id=?", (run_id,))}
def kinds(fake): return [(m, p) for m, p, q, b in fake.reqs]

# ---- the happy path
rid = approved_run(); fake = world(); backups = []
s = cp.push(conn, rid, client(fake), ["CAT1"], backup=lambda run, raw: backups.append(raw))
check("all three formulas are pushed and verified; the run is pushed", s["state"] == "pushed" and s["counts"] == {"created": 3, "reused": 0, "attached": 3, "written": 3, "verified": 3} and s["failed"] == [] and s["fatal"] is None, s)
check("every formula's progress is saved as verified", {k: v[0] for k, v in steps(rid).items()} == {"fldComm": "verified", "fldDep": "verified", "fldTot": "verified"}, steps(rid))
k = kinds(fake)
creates = [i for i, x in enumerate(k) if x == ("POST", "/param")]
puts = [i for i, x in enumerate(k) if x[0] == "PUT" and x[1] == "/CTGY/CAT1"]
exprs = [i for i, x in enumerate(k) if x[0] == "PUT" and x[1].startswith("/CTGYPA/")]
check("fields are created first, then the form is updated ONCE, then the expressions are written (never before every field is on the form)", len(creates) == 3 and len(puts) == 1 and len(exprs) == 3 and max(creates) < puts[0] < min(exprs), k)
body = fake.reqs[puts[0]][3]["categoryParameters"]
check("the PUT sends every existing association with ALL its settings, unchanged, then the new fields last", body[:2] == [
    {"parameterID": "X1", "displayOrder": 1, "isRequired": True, "securityLevel": "E", "parameterGroupID": "G1", "rowShared": True, "viewSecurityLevel": "V", "configurations": {"k": "v"}},
    {"parameterID": "X2", "displayOrder": 5, "isRequired": False, "securityLevel": "A", "parameterGroupID": "G2"}] and [b["displayOrder"] for b in body[2:]] == [6, 7, 8], body)
check("a new field has no section and no shared row: only its order, required flag and security level", all(set(b) == {"parameterID", "displayOrder", "isRequired", "securityLevel"} for b in body[2:]), body[2:])
check("the form kept the existing fields' layout after the update", [(a["parameterID"], a.get("parameterGroupID"), a.get("rowShared")) for a in fake.forms["CAT1"]["assoc"][:2]] == [("X1", "G1", True), ("X2", "G2", None)], fake.forms["CAT1"]["assoc"][:2])
check("the form as first read went to the backup before the PUT", len(backups) == 1 and [a["parameterID"] for a in backups[0]["categoryParameters"]] == ["X1", "X2"], backups)
check("no difference was found when the form was read back", s["form_notes"] == [], s["form_notes"])
want = {"SalesPersonCommission": "{DE:base_price}*0.15", "CommPlusOne": "{DE:SalesPersonCommission}+1", "TotalOne": "{DE:base_price}+1"}
got = {fake.params[a["parameterID"]]["name"]: a["rawCustomExpression"] for a in fake.forms["CAT1"]["assoc"][2:]}
check("each expression reached its own association", got == want, got)
check("an expression is sent as exactly {rawCustomExpression: ...}", all(list(fake.reqs[i][3]) == ["rawCustomExpression"] for i in exprs))
check("a created field carries its name, dataType NMBR and displayType CALC", {p["name"]: (p["dataType"], p["displayType"]) for p in fake.params.values()} == {n: ("NMBR", "CALC") for n in want})
rows = conn.execute("SELECT category_id, workfront_name, ctgypa_id, expression, parameter_id FROM calc_formula_crosswalk ORDER BY workfront_name").fetchall()
check("each placement is recorded with its form, field, association and expression", [(r[0], r[1], bool(r[2]), r[3]) for r in rows] == [("CAT1", n, True, want[n]) for n in sorted(want)], rows)
log = conn.execute("SELECT step, http_method, endpoint, http_status, success, error_message, sent_at, received_at FROM calc_push_log WHERE run_id=? ORDER BY log_id", (rid,)).fetchall()
check("every call is logged, with method, path only and times", len(log) == len(fake.reqs) and all(r[1] and r[2].startswith("/") and "?" not in r[2] and r[6] and r[7] and r[4] == 1 for r in log), (len(log), len(fake.reqs)))
check("the log holds the steps in order and no key or header", [r[0] for r in log][:2] == ["find_param", "create_param"] and "attach_param" in [r[0] for r in log] and KEY not in json.dumps(log) and "apikey" not in json.dumps(log).lower())
check("a finished run pushed again does nothing and sends nothing", cp.push(conn, rid, client(fake), ["CAT1"])["nothing_to_do"] is True and len(fake.reqs) == len(log))

# ---- an existing field with the same name and the right type is used, not created
conn.execute("DELETE FROM calc_formula_crosswalk"); conn.execute("DELETE FROM calc_push_log"); conn.commit()
rid = approved_run("CAT2"); fake = world(category="CAT2"); pre = fake.add_param("salespersoncommission", "NMBR", "CALC")      # another letter case: Workfront treats it as the same name
s = cp.push(conn, rid, client(fake), ["CAT2"])
check("an existing field with the right type is reused: no failure, no second field", s["state"] == "pushed" and s["counts"]["reused"] == 1 and s["counts"]["created"] == 2 and s["failed"] == [], s)
check("only the missing fields were created, and the reused one is on the form", sum(1 for m, p, q, b in fake.reqs if (m, p) == ("POST", "/param")) == 2 and sum(1 for p in fake.params.values() if p["name"].casefold() == "salespersoncommission") == 1
      and pre in [a["parameterID"] for a in fake.forms["CAT2"]["assoc"]], kinds(fake))

# ---- an existing field with another type fails only its own formula; retry touches only that one
conn.execute("DELETE FROM calc_formula_crosswalk"); conn.commit()
rid = approved_run("CAT3"); fake = world(category="CAT3"); bad = fake.add_param("CommPlusOne", "TEXT", "TEXT")
s = cp.push(conn, rid, client(fake), ["CAT3"])
check("a field with another type fails only that formula, with the reason, and the rest goes on", [(f["field_id"], f["step"]) for f in s["failed"]] == [("fldDep", "create")] and "TEXT" in s["failed"][0]["message"] and "NMBR" in s["failed"][0]["message"]
      and s["counts"]["verified"] == 2 and s["state"] == "pushing", s)
check("the existing field was not changed", fake.params[bad]["dataType"] == "TEXT" and bad not in [a["parameterID"] for a in fake.forms["CAT3"]["assoc"]])
check("the failed formula keeps its message and its step", steps(rid)["fldDep"][0] == "none" and "already exists" in steps(rid)["fldDep"][1])
del fake.params[bad]; before = len(fake.reqs); item = [r[0] for r in conn.execute("SELECT item_id FROM calc_preflight_items WHERE run_id=? AND formula_field_id='fldDep'", (rid,))][0]
s2 = cp.push(conn, rid, client(fake), ["CAT3"], item_ids=[item])
mine = kinds(fake)[before:]
check("the retry finishes it and the run becomes pushed", s2["state"] == "pushed" and s2["failed"] == [] and s2["counts"]["created"] == 1 and s2["counts"]["verified"] == 1, s2)
check("the retry created one field and wrote one expression: the done formulas were not touched", mine.count(("POST", "/param")) == 1 and sum(1 for x in mine if x[1].startswith("/CTGYPA/")) == 1, mine)
check("the form still holds every field once", len([a["parameterID"] for a in fake.forms["CAT3"]["assoc"]]) == 5 and len({a["parameterID"] for a in fake.forms["CAT3"]["assoc"]}) == 5)

# ---- a field already on the form needs no form update
conn.execute("DELETE FROM calc_formula_crosswalk"); conn.commit()
rid = approved_run("CAT4"); fake = world(category="CAT4"); fake.params.clear()
p_comm = fake.add_param("SalesPersonCommission", "NMBR", "CALC"); fake.forms["CAT4"]["assoc"].append({"ID": "AX", "parameterID": p_comm, "displayOrder": 9, "isRequired": False, "securityLevel": "E"})
s = cp.push(conn, rid, client(fake), ["CAT4"])
check("a field already on the form is not added twice and keeps its association", s["state"] == "pushed" and [a["parameterID"] for a in fake.forms["CAT4"]["assoc"]].count(p_comm) == 1 and [a["ID"] for a in fake.forms["CAT4"]["assoc"] if a["parameterID"] == p_comm] == ["AX"], fake.forms["CAT4"]["assoc"])
check("its expression was written to its own association", [a for a in fake.forms["CAT4"]["assoc"] if a["parameterID"] == p_comm][0]["rawCustomExpression"] == "{DE:base_price}*0.15")

# ---- one formula refused does not stop the others; the key is never in a message or the log
conn.execute("DELETE FROM calc_formula_crosswalk"); conn.commit()
rid = approved_run("CAT5"); fake = world(category="CAT5"); fake.refuse_create["TotalOne"] = (400, {"error": {"class": "DuplicateNameException", "message": "name taken " + KEY}})
s = cp.push(conn, rid, client(fake), ["CAT5"])
check("a refused create fails that formula only, and the others are pushed", [f["field_id"] for f in s["failed"]] == ["fldTot"] and s["counts"]["verified"] == 2, s)
check("the key is never in the message or the log", KEY not in json.dumps(s) and KEY not in json.dumps(conn.execute("SELECT * FROM calc_push_log").fetchall()) and KEY not in json.dumps(conn.execute("SELECT last_error FROM calc_push_items").fetchall()))

# ---- a write that fails, and an expression Workfront reads back differently
conn.execute("DELETE FROM calc_formula_crosswalk"); conn.commit()
rid = approved_run("CAT6"); fake = world(category="CAT6"); fake.read_back = lambda e: e.replace("0.15", "0.150")
s = cp.push(conn, rid, client(fake), ["CAT6"])
check("an expression read back differently is flagged, not hidden, and the item stays at 'expression_written'", steps(rid)["fldComm"][0] == "expression_written" and "read back" in steps(rid)["fldComm"][1] and s["counts"]["verified"] == 2, steps(rid))
check("the run is still pushed (every expression was written)", s["state"] == "pushed")
conn.execute("DELETE FROM calc_formula_crosswalk"); conn.commit()
rid = approved_run("CAT7"); fake = world(category="CAT7")
fake.hook = None
first = {}
s0 = cp.push(conn, rid, client(fake), ["CAT7"], should_stop=lambda: len(fake.reqs) >= 2)
check("Cancel ends the push after the call in progress and keeps what is done", s0["cancelled"] is True and len(fake.reqs) == 2 and s0["state"] == "pushing", (s0, len(fake.reqs)))

# ---- the form changing a setting while saving is reported
conn.execute("DELETE FROM calc_formula_crosswalk"); conn.commit()
rid = approved_run("CAT8"); fake = world(category="CAT8"); fake.put_changes = {"X1": {"rowShared": False}}
s = cp.push(conn, rid, client(fake), ["CAT8"])
check("an existing field that came back changed is reported in the form notes", any("X1" in n and "rowShared" in n for n in s["form_notes"]), s["form_notes"])

# ---- the form fails to save
conn.execute("DELETE FROM calc_formula_crosswalk"); conn.commit()
rid = approved_run("CAT9"); fake = world(category="CAT9"); fake.fail_put_form = 500
s = cp.push(conn, rid, client(fake), ["CAT9"])
check("when the form cannot be saved every formula says so, none gets an expression, and the run stays open", len(s["failed"]) == 3 and all(f["step"] == "attach" for f in s["failed"]) and s["state"] == "pushing"
      and not any(x[1].startswith("/CTGYPA/") for x in kinds(fake)), s)
fake.fail_put_form = None
s = cp.push(conn, rid, client(fake), ["CAT9"])
check("and a retry then completes all three", s["state"] == "pushed" and s["counts"]["verified"] == 3 and s["failed"] == [], s)

# ---- a refused key stops everything at once
conn.execute("DELETE FROM calc_formula_crosswalk"); conn.commit()
rid = approved_run("CAT10"); fake = world(category="CAT10"); fake.key = "somethingElse"
s = cp.push(conn, rid, client(fake), ["CAT10"])
check("a refused key stops at the first call and says so", s["fatal"] and len(fake.reqs) == 1 and s["state"] == "pushing" and KEY not in s["fatal"], (s, len(fake.reqs)))

# ---- refusals that send nothing
fake = world(category="CAT11")
def refused(rid_, forms, kw, what, word):
    try:
        cp.push(conn, rid_, client(fake), forms, **kw); e = ""
    except cp.PushError as ex:
        e = ex.message
    check("refused: " + what, word in e, e)
rid = approved_run("CAT11")
refused(rid, ["OTHER"], {}, "a form not on the list", "not on the list")
refused(rid, ["CAT11"], {"item_ids": [999999]}, "an item outside the run", "not translatable items")
refused(cpf.run(conn, ENV, B, TBL, "CAT11", "x", "JV"), ["CAT11"], {}, "an unapproved run", "approved")
check("nothing was sent by any refusal", fake.reqs == [])
rid = approved_run("CAT11")
conn.execute("UPDATE airtable_fields SET formula_text='{fldBase}*0.30' WHERE field_id='fldComm'"); conn.commit()
try:
    cp.push(conn, rid, client(fake), ["CAT11"]); e = ""
except cp.PushError as ex:
    e = ex.message
check("a formula changed since the approval is refused, asking for a new preflight, and nothing is sent", "new preflight" in e and fake.reqs == [], e)
try:
    cp.read_form(client(fake), "bad id!"); e = ""
except cp.PushError as ex:
    e = ex.message
check("a form ID that is not a plain ID is never put in a URL", "plain ID" in e and fake.reqs == [])
check("read_form reads one form and writes nothing", cp.read_form(client(world(category="CAT12")), "CAT12")["name"] == "Special Order Sales")

# ---- the module sends only through the Http it is given
imported = set()
for node in ast.walk(ast.parse(open(os.path.join(ROOT, "src", "calcpush.py"), encoding="utf-8").read())):
    if isinstance(node, ast.Import):
        imported |= {a.name.split(".")[0] for a in node.names}
    elif isinstance(node, ast.ImportFrom):
        imported.add((node.module or "").split(".")[0])
check("the push imports no HTTP library of its own and no random module", imported <= {"re", "sqlite3", "datetime", "calcpreflight", "workfronthttp"}, imported)

conn.close(); shutil.rmtree(tmp, ignore_errors=True)
print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
