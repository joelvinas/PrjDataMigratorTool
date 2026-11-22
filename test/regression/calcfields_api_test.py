"""HTTP tests for the calculated fields endpoints (/api/calcfields/*) and the /calcfields page. Fabricated schema only: the table, field and form names are made up. The page only
reads the database; the test also checks that no token appears in any answer."""
import csv, io, json, os, sqlite3, sys, urllib.request
B = "http://127.0.0.1:8765"; ok = True; BASE = "appCalcApi00001"; TOKEN = "patMOCK.0000000000000000000000"; DBF = "calcfields_api.db"
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

# ---- preflight
A = dict(environment="sandbox1", base_id=BASE, table_id="tblCalc01", category_id="CAT1", category_name="Special Order Sales", actor="JV")
for bad, word in ((dict(A, environment="nowhere"), "environment"), (dict(A, category_id="bad id!"), "custom form"), (dict(A, actor=""), "initials"), (dict(A, table_id="tblNone"), "no formula"), (dict(A, base_id="nope"), "base ID")):
    s, r = post("/api/calcfields/preflight", bad); check("preflight refused: " + word, s == 400 or s == 404, (s, r)); check("  the reason says why (%s)" % word, word in r.get("error", ""), r)
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
s, r = get("/api/calcfields/report?run_id=%d" % RUN); check("an earlier run can be opened again", s == 200 and r["report"]["run"]["category_id"] == "CAT1" and r["drift"] == [])
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

check("no answer holds the Airtable token", all(TOKEN.encode() not in d for d in SEEN))
c.close()
print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
