"""HTTP tests for the calculated fields endpoints (/api/calcfields/*) and the /calcfields page. Fabricated schema only: the table, field and form names are made up. The page only
reads the database; the test also checks that no token appears in any answer."""
import base64, csv, glob, io, json, os, sqlite3, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calcfake as cfk
B = "http://127.0.0.1:8765"; ok = True; BASE = "appCalcApi00001"; TOKEN = "patMOCK.0000000000000000000000"; DBF = "calcfields_api.db"; KEY = "fakeCOREkey0123456789abcdef"; FPORT = 9103
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])
SEEN = []
def raw(method, p, body=None):
    h = {"Content-Type": "application/json"} if method == "POST" else {}
    req = urllib.request.Request(B + p, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(req) as r:
            d = r.read(); SEEN.append(d); return r.status, d, r.headers
    except urllib.error.HTTPError as e:
        d = e.read(); SEEN.append(d); return e.code, d, e.headers
def get(p): s, d, h = raw("GET", p); return s, json.loads(d)
def post(p, b): s, d, h = raw("POST", p, b); return s, json.loads(d)

post("/api/db/create", {"path": DBF}); post("/api/db/connect", {"path": DBF})
c = sqlite3.connect(DBF)
def fld(fid, name, table, dtype="number", formula=None, rtype=None):
    c.execute("INSERT INTO airtable_fields (base_id, table_id, table_name, field_id, field_name, data_type, formula_text, result_type, is_valid) VALUES (?,?,?,?,?,?,?,?,1)", (BASE, table, "Special Orders" if table == "tblCalc01" else "Other", fid, name, dtype, formula, rtype))
fld("fldBase", "Base Price", "tblCalc01")
fld("fldComm", "Commission", "tblCalc01", "formula", "{fldBase}*0.15", "number")
fld("fldNeg", "Negative", "tblCalc01", "formula", "-({fldBase}+1)", "number")
fld("fldMonths", "Months", "tblCalc01", "formula", "DATETIME_DIFF({fldBase},{fldBase},'months')", "number")
fld("fldPlain", "Plain", "tblOther01")
c.execute("INSERT INTO field_names (airtable_field_id, core_name, core_name_status) VALUES ('fldComm', 'SalesPersonCommission', 'approved')")
c.execute("INSERT INTO field_names (airtable_field_id, core_name, core_name_status) VALUES ('fldNeg', 'NegativeOne', 'approved')")
c.execute("INSERT INTO field_names (airtable_field_id, core_name, core_name_status) VALUES ('fldMonths', 'MonthsOne', 'approved')")
c.execute("INSERT INTO core_field_crosswalk (environment, airtable_field_id, workfront_name, workfront_data_type, workfront_display_type, parameter_id) VALUES ('sandbox1', 'fldBase', 'base_price', 'NMBR', 'TEXT', 'P1')")
c.commit()

# ---- the page and its script
s, d, h = raw("GET", "/calcfields"); check("the page is served, with keyboard-focusable scrolling windows", s == 200 and b"Calculated fields" in d and d.count(b'class="tablewin"') >= 6 and h["Content-Type"].startswith("text/html"))
s, d, h = raw("GET", "/calcfields.js"); check("the page script is served, and asks for no Workfront route", s == 200 and b"/api/calcfields/preflight" in d and b"/api/corefields" not in d and "javascript" in h["Content-Type"])
s, d, h = raw("GET", "/common.js"); check("the page is in the navigation after Core fields in bulk", b'"/calcfields"' in d and d.index(b'"/calcfields"') > d.index(b'"/corefields"'))

# ---- setup
s, r = get("/api/calcfields/setup")
check("setup lists the table with formulas and its count, not the table without", s == 200 and r["tables"] == [{"base_id": BASE, "table_id": "tblCalc01", "table_name": "Special Orders", "formulas": 3}], r)
check("setup lists the four environments and the displayType CALC", r["environments"] == ["sandbox1", "sandbox2", "preview", "production"] and r["display_type"] == "CALC", r)

# ---- the list of valid forms
check("setup starts with no list of valid forms", r["forms"] == [] and r["production_phrase"] == "CREATE IN PRODUCTION", r)
A = dict(environment="sandbox1", base_id=BASE, table_id="tblCalc01", category_id="CAT1", actor="JV")
s, r = post("/api/calcfields/preflight", A); check("a preflight needs the list of valid forms first", s == 409 and "list of valid forms" in r["error"], (s, r))
s, d, h = raw("GET", "/api/calcfields/forms/template.csv"); check("the template is a CSV download with the header", s == 200 and d.decode().splitlines()[0] == "category_id,name" and "attachment" in h["Content-Disposition"], d[:80])
def b64(text): return base64.b64encode(text.encode()).decode()
s, r = post("/api/calcfields/forms/upload", {"csv_base64": b64("category_id,name\r\nCAT 1,Bad\r\nCAT2,\r\n"), "filename": "bad.csv"})
check("a bad list is refused with every problem and its row, and nothing loads", s == 400 and r["problem_count"] == 2 and {(x["row"], x["column"]) for x in r["problems"]} == {(2, "category_id"), (3, "name")}, r)
check("setup still has no list", get("/api/calcfields/setup")[1]["forms"] == [])
s, r = post("/api/calcfields/forms/upload", {"csv_base64": b64("category_id,name\r\nCAT1,Special Order Sales\r\nCAT2,In Store Sales\r\n"), "filename": "forms.csv"})
check("a good list loads, in order", s == 200 and [f["category_id"] for f in r["forms"]] == ["CAT1", "CAT2"] and r["filename"] == "forms.csv", r)
check("setup now returns the list", get("/api/calcfields/setup")[1]["forms"][0] == {"category_id": "CAT1", "name": "Special Order Sales"})
check("without migration 028 the list is kept in memory only and the answer says so", r["saved"] is False and c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='calc_valid_forms'").fetchone()[0] == 0, r)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))
import draftmigrations; draftmigrations.apply(c)
check("migration 028 creates calc_valid_forms", c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='calc_valid_forms'").fetchone()[0] == 1)
s, r = post("/api/calcfields/forms/upload", {"csv_base64": b64("category_id,name\r\nCAT2,In Store Sales\r\nCAT1,Special Order Sales\r\n"), "filename": "forms2.csv"})
rows = c.execute("SELECT category_id, name, position, source_filename FROM calc_valid_forms ORDER BY position").fetchall()
check("with the table the accepted list is saved, in the order uploaded", r["saved"] is True and rows == [("CAT2", "In Store Sales", 0, "forms2.csv"), ("CAT1", "Special Order Sales", 1, "forms2.csv")], rows)
s, r = post("/api/calcfields/forms/upload", {"csv_base64": b64("category_id,name\r\nCAT 1,Bad\r\n")})
check("a refused file leaves the saved list untouched", s == 400 and c.execute("SELECT COUNT(*) FROM calc_valid_forms").fetchone()[0] == 2)
c.execute("UPDATE calc_valid_forms SET name='Renamed In Db' WHERE category_id='CAT2'"); c.commit()
r = get("/api/calcfields/setup")[1]
check("setup reads the saved list (as after a restart) with its file name", [f["name"] for f in r["forms"]] == ["Renamed In Db", "Special Order Sales"] and r["forms_filename"] == "forms2.csv", r["forms"])
s, r = post("/api/calcfields/forms/upload", {"csv_base64": b64("category_id,name\r\nCAT1,Special Order Sales\r\nCAT2,In Store Sales\r\n"), "filename": "forms.csv"})
check("a new accepted list replaces the saved one", [x[0] for x in c.execute("SELECT category_id FROM calc_valid_forms ORDER BY position")] == ["CAT1", "CAT2"])

# ---- preflight
for bad, word in ((dict(A, environment="nowhere"), "environment"), (dict(A, category_id="CAT9"), "list of valid forms"), (dict(A, category_id="bad id!"), "list of valid forms"), (dict(A, actor=""), "initials"),
                  (dict(A, table_id="tblNone"), "no formula"), (dict(A, base_id="nope"), "base ID")):
    s, r = post("/api/calcfields/preflight", bad); check("preflight refused: " + word, s in (400, 404, 409), (s, r)); check("  the reason says why (%s)" % word, word in r.get("error", ""), r)
s, r = post("/api/calcfields/preflight", A)
rp = r["report"]
check("preflight returns the run, the report and no drift", s == 200 and r["run_id"] >= 1 and r["drift"] == [] and rp["total"] == 3, r)
check("counts per status", rp["counts"] == {"TRANSLATABLE": 2, "BLOCKED_MISSING_REFERENCE": 0, "BLOCKED_UNSUPPORTED_FUNCTION": 1, "BLOCKED_DEPENDENCY": 0}, rp["counts"])
check("the commission expression is {DE:base_price}*0.15 with displayType CALC", {d["field_id"]: (d["expression"], d["display_type"]) for d in rp["translatable"]}["fldComm"] == ("{DE:base_price}*0.15", "CALC"), rp["translatable"])
check("the unsupported unit is reported with a reason", rp["blocked"]["BLOCKED_UNSUPPORTED_FUNCTION"][0]["field_id"] == "fldMonths" and "months" in rp["blocked"]["BLOCKED_UNSUPPORTED_FUNCTION"][0]["reason"])
RUN = r["run_id"]

# ---- runs, report, csv
s, r = get("/api/calcfields/runs?table_id=tblCalc01")
check("the run is listed with its counts and state", s == 200 and r["runs"][0]["run_id"] == RUN and r["runs"][0]["state"] == "preflight" and r["runs"][0]["counts"]["TRANSLATABLE"] == 2 and r["runs"][0]["run_by"] == "JV", r)
s, r = get("/api/calcfields/report?run_id=%d" % RUN); check("an earlier run can be opened again", s == 200 and r["report"]["run"]["category_id"] == "CAT1" and r["report"]["run"]["category_name"] == "Special Order Sales" and r["drift"] == [])
s, d, h = raw("GET", "/api/calcfields/report.csv?run_id=%d" % RUN)
rows = list(csv.reader(io.StringIO(d.decode("utf-8"))))
check("the CSV has a header and one row per formula", s == 200 and h["Content-Type"].startswith("text/csv") and "attachment" in h["Content-Disposition"] and rows[0][:3] == ["field_id", "field_name", "status"] and len(rows) == 4, rows[:2])
check("a cell that starts with - is made safe for Excel", any(r_[-1] == "'-({DE:base_price}+1)" for r_ in rows[1:]), [r_[-1] for r_ in rows[1:]])
for p in ("/api/calcfields/report?run_id=x", "/api/calcfields/report.csv", "/api/calcfields/report?run_id=999999"):
    s, d, h = raw("GET", p); check("refused: " + p, s in (400, 404), s)
s, r = get("/api/calcfields/runs?table_id=bad%20id"); check("runs refuses a bad table ID", s == 400)

# ---- approval
for body, word in ((dict(run_id=RUN, actor="", reason="ok"), "initials"), (dict(run_id=RUN, actor="JV", reason=""), "reason"), (dict(run_id="x", actor="JV", reason="ok"), "run")):
    s, r = post("/api/calcfields/approve", body); check("approval refused: " + word, s == 400 and word in r["error"], (s, r))
c.execute("UPDATE airtable_fields SET formula_text='{fldBase}*0.20' WHERE field_id='fldComm'"); c.commit()
s, r = get("/api/calcfields/report?run_id=%d" % RUN); check("a changed formula shows as drift on the report", any("formula field changed" in x for x in r["drift"]), r["drift"])
s, r = post("/api/calcfields/approve", dict(run_id=RUN, actor="JV", reason="looks right")); check("approval after drift is refused and asks for a new preflight", s == 409 and "new preflight" in r["error"], (s, r))
s, r = post("/api/calcfields/preflight", A); RUN2 = r["run_id"]
check("a new preflight reads the new formula and replaces the old run", RUN2 > RUN and {d["field_id"]: d["expression"] for d in r["report"]["translatable"]}["fldComm"] == "{DE:base_price}*0.20")
s, r = get("/api/calcfields/runs?table_id=tblCalc01"); check("the earlier run is now superseded", {x["run_id"]: x["state"] for x in r["runs"]} == {RUN2: "preflight", RUN: "superseded"}, r["runs"])
s, r = post("/api/calcfields/approve", dict(run_id=RUN, actor="JV", reason="old")); check("a superseded run cannot be approved", s == 409, (s, r))
s, r = post("/api/calcfields/approve", dict(run_id=RUN2, actor="JV", reason="Reviewed every expression"))
check("approval records who, when and why", s == 200 and r["state"] == "approved" and r["report"]["run"]["approved_by"] == "JV" and r["report"]["run"]["approval_reason"] == "Reviewed every expression" and r["report"]["run"]["approved_at"], (s, r))
s, r = post("/api/calcfields/approve", dict(run_id=RUN2, actor="JV", reason="again")); check("an approved run cannot be approved twice", s == 409, (s, r))
check("nothing was written to the call log tables by a preflight", c.execute("SELECT COUNT(*) FROM calc_push_log").fetchone()[0] == 0 and c.execute("SELECT COUNT(*) FROM calc_formula_crosswalk").fetchone()[0] == 0)

# ---- the push, against a fake Workfront served on another port
fake = cfk.FakeWorkfront(KEY); fake.add_form("CAT1", "Special Order Sales", [{"parameterID": "X1", "displayOrder": 1, "isRequired": True, "securityLevel": "E", "parameterGroupID": "G1", "rowShared": True}])
existing = fake.add_param("salespersoncommission", "NMBR", "CALC")
srv = cfk.start(fake, FPORT); orig_cfg = cfk.set_config(FPORT, KEY)
def wait_job():
    for _ in range(100):
        s, r = get("/api/calcfields/push/status")
        if r["job"] and r["job"]["status"] != "running": return r["job"]
        time.sleep(0.1)
    raise AssertionError("push did not finish")
try:
    s, r = post("/api/calcfields/push", dict(run_id=RUN2, actor="JV")); check("a push needs the confirmation", s == 400 and "Confirm" in r["error"], (s, r))
    s, r = post("/api/calcfields/push", dict(run_id=RUN2, actor="", confirm=True)); check("a push needs initials", s == 400 and "initials" in r["error"], (s, r))
    s, r = post("/api/calcfields/push", dict(run_id=RUN2, actor="JV", confirm=True, item_ids=["x"])); check("retry items must be numbers", s == 400, (s, r))
    s, r = post("/api/calcfields/push", dict(run_id=RUN, actor="JV", confirm=True)); check("a superseded run cannot be pushed", s == 409 and "superseded" in r["error"], (s, r))
    s, r = post("/api/calcfields/push/cancel", {}); check("cancel with nothing running is refused", s == 400)
    s, r = post("/api/calcfields/form/check", dict(run_id=RUN2)); check("the form check needs the confirmation, and sent nothing", s == 400 and fake.reqs == [], (s, r))
    s, r = post("/api/calcfields/form/check", dict(run_id=RUN2, confirm=True))
    check("the form check is one read-only GET, logged against the run, and says the name matches the list", s == 200 and r["found_name"] == "Special Order Sales" and r["name_matches"] is True and r["fields_on_form"] == 1
          and [(m, pth) for m, pth, q, b in fake.reqs] == [("GET", "/CTGY/CAT1")] and c.execute("SELECT step, http_method, endpoint FROM calc_push_log WHERE run_id=?", (RUN2,)).fetchall() == [("check_form", "GET", "/CTGY/CAT1")], (r, fake.reqs))
    s, r = post("/api/calcfields/forms/upload", {"csv_base64": b64("category_id,name\r\nCAT2,In Store Sales\r\n")})
    s, r = post("/api/calcfields/form/check", dict(run_id=RUN2, confirm=True)); check("a form that is not on the list cannot be read", s == 409 and "not on the list" in r["error"], (s, r))
    s, r = post("/api/calcfields/push", dict(run_id=RUN2, actor="JV", confirm=True)); check("a form that is not on the list cannot be pushed, and nothing is sent", s == 409 and "not on the list" in r["error"] and len(fake.reqs) == 1, (s, r))
    post("/api/calcfields/forms/upload", {"csv_base64": b64("category_id,name\r\nCAT1,Special Order Sales\r\n"), "filename": "forms.csv"})
    before = len(fake.reqs)
    s, r = post("/api/calcfields/push", dict(run_id=RUN2, actor="JV", confirm=True)); check("the push starts", s == 200 and r["started"], (s, r))
    job = wait_job()
    sm = job["summary"]
    check("the push finishes: the existing field was used, one was created, both are on the form and verified", job["status"] == "done" and sm["state"] == "pushed" and sm["counts"] == {"created": 1, "reused": 1, "attached": 2, "written": 2, "verified": 2} and sm["failed"] == [], job)
    check("the existing field was not created again, and the form kept its first field's layout", sum(1 for m, pth, q, b in fake.reqs[before:] if (m, pth) == ("POST", "/param")) == 1 and fake.forms["CAT1"]["assoc"][0].get("parameterGroupID") == "G1"
          and existing in [a["parameterID"] for a in fake.forms["CAT1"]["assoc"]], fake.forms["CAT1"]["assoc"])
    s, r = get("/api/calcfields/report?run_id=%d" % RUN2)
    check("the report shows every item verified and the run pushed", s == 200 and r["report"]["run"]["state"] == "pushed" and {d["push_step"] for d in r["report"]["translatable"]} == {"verified"}, r["report"]["run"])
    backups = glob.glob(os.path.join("out", "calcfields", "FormBackup_run%d_CAT1_*.json" % RUN2))
    check("the form as first read was kept before it changed", len(backups) == 1 and json.load(open(backups[0]))["categoryParameters"][0]["parameterID"] == "X1", backups)
    s, r = post("/api/calcfields/push", dict(run_id=RUN2, actor="JV", confirm=True)); n0 = len(fake.reqs); job = wait_job()
    check("pushing a finished run again sends nothing", s == 200 and job["summary"]["nothing_to_do"] is True and len(fake.reqs) == n0, job)
    check("the push log holds every call and no key", c.execute("SELECT COUNT(*) FROM calc_push_log WHERE run_id=?", (RUN2,)).fetchone()[0] == len(fake.reqs) and KEY not in json.dumps(c.execute("SELECT * FROM calc_push_log").fetchall()))
    s, r = get("/api/calcfields/push/status"); check("the status answer never holds the key", KEY not in json.dumps(r))
    # production needs its extra phrase
    c.execute("INSERT INTO core_field_crosswalk (environment, airtable_field_id, workfront_name, workfront_data_type, workfront_display_type, parameter_id) VALUES ('production', 'fldBase', 'base_price', 'NMBR', 'TEXT', 'P1')"); c.commit()
    s, r = post("/api/calcfields/preflight", dict(A, environment="production")); RUNP = r["run_id"]; post("/api/calcfields/approve", dict(run_id=RUNP, actor="JV", reason="production test"))
    n1 = len(fake.reqs)
    s, r = post("/api/calcfields/push", dict(run_id=RUNP, actor="JV", confirm=True)); check("a production push is refused without the typed phrase, and nothing is sent", s == 400 and "CREATE IN PRODUCTION" in r["error"] and len(fake.reqs) == n1, (s, r))
finally:
    srv.shutdown(); open("config.json", "w", encoding="utf-8").write(orig_cfg)

check("no answer holds the Airtable token", all(TOKEN.encode() not in d for d in SEEN))
c.close()
print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
