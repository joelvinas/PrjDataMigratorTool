"""HTTP tests for the data type endpoints (/api/datatypes/*) and the /datatypes page. Fabricated schema only."""
import json, os, sqlite3, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))
import typesfixture as tf
B = "http://127.0.0.1:8765"; ok = True; BASE = "appTypesApi001"
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])
def raw(method, p, body=None):
    h = {"Content-Type": "application/json"} if method == "POST" else {}
    req = urllib.request.Request(B + p, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers
def get(p): s, d, h = raw("GET", p); return s, json.loads(d)
def post(p, b): s, d, h = raw("POST", p, b); return s, json.loads(d)
def A(**k): return dict({"base_id": BASE, "actor": "JV", "reason": "api test"}, **k)

def fld(i, n, t="singleLineText", o=None):
    d = {"id": "fld" + i, "name": n, "type": t}
    if o: d["options"] = o
    return d
schema = {"tables": [{"id": "tblDt0000000001", "name": "Orders", "primaryFieldId": "fldD01", "views": [], "fields": [
    fld("D01", "Order Name"), fld("D02", "Amount", "number"), fld("D03", "Status", "singleSelect", {"choices": [{"id": "selA", "name": "Open"}]}), fld("D04", "Notes", "multilineText")]}]}
post("/api/db/create", {"path": "datatypes_api.db"}); post("/api/db/connect", {"path": "datatypes_api.db"})
_, r = post("/api/schema/ingest", {"base_id": BASE, "source": "json", "schema_json": json.dumps(schema)})
check("fabricated schema ingested", r.get("tables") == 1 and r.get("fields") == 4, r)

s, d, h = raw("GET", "/datatypes"); check("the page is served, with a keyboard-focusable scrolling window", s == 200 and b"Data types" in d and b'class="tablewin"' in d and h["Content-Type"].startswith("text/html"))
s, d, h = raw("GET", "/datatypes.js"); check("the page script is served", s == 200 and b"/api/datatypes/apply" in d and "javascript" in h["Content-Type"])
s, d, h = raw("GET", "/common.js"); check("the page is in the navigation", b'"/datatypes"' in d)

# ---- before the migrations
s, rv = get("/api/datatypes/review?base_id=" + BASE)
check("review works before the migrations and says what is missing", s == 200 and rv["ready"] is False and "data_type_defaults" in rv["missing"] and len(rv["fields"]) == 4 and rv["counts"]["planning"]["unset"] == 4, rv.get("missing"))
check("the review lists the 17 Planning types and the Core lists", len(rv["planning_types"]) == 17 and rv["core_data_types"] == ["CURC", "DATE", "DTTM", "NMBR", "RICH", "TEXT"] and rv["core_display_types"] == ["CHCK", "MULT", "SLCT", "TEXT", "TXTA"])
s, e = post("/api/datatypes/apply", A(system="planning", mode="defaults")); check("saving is refused with a plain message until the migrations are applied", s == 409 and "migrations 021 and 022" in e["error"], e)
check("a bad base ID is refused", get("/api/datatypes/review?base_id=nope")[0] == 400)

# ---- apply the migrations to the test database (the server opens it per request)
c = sqlite3.connect("datatypes_api.db"); tf.apply_migrations(c); c.close()
s, rv = get("/api/datatypes/review?base_id=" + BASE); check("after the migrations the page is ready and the defaults are listed", s == 200 and rv["ready"] is True and len(rv["defaults"]) == 52)
s, e = post("/api/datatypes/apply", {"base_id": BASE, "system": "planning", "mode": "defaults"}); check("initials are required", s == 400 and "initials" in e["error"], e)
s, e = post("/api/datatypes/apply", {"base_id": BASE, "actor": "JV", "system": "planning", "mode": "defaults"}); check("a reason is required", s == 400 and "reason" in e["error"], e)
s, r = post("/api/datatypes/apply", A(system="planning", mode="defaults"))
fl = {f["field_id"]: f for f in r["review"]["fields"]}
check("applying the Planning defaults sets every field and returns the new review", s == 200 and r["changed"] == 4 and fl["fldD03"]["planning"]["type"] == "single-select" and fl["fldD02"]["planning"]["decision"]["by"] == "JV"
      and r["review"]["counts"]["planning"]["set"] == 4 and r["batch"] == "T1", r.get("error"))
s, r = post("/api/datatypes/apply", A(system="core", mode="defaults", selection={"airtable_types": ["multilineText", "number"]}))
fl = {f["field_id"]: f for f in r["review"]["fields"]}
check("Core is set separately, by selection, as a dataType and a displayType", s == 200 and r["changed"] == 2 and (fl["fldD04"]["core"]["type"], fl["fldD04"]["core"]["display_type"]) == ("TEXT", "TXTA")
      and fl["fldD01"]["core"]["state"] == "unset", r.get("error"))
s, e = post("/api/datatypes/apply", A(system="core", mode="each", changes=[{"field_id": "fldD01", "type": "TEXT"}])); check("a Core change without a displayType is refused", s == 400 and "displayType" in e["error"], e)
s, e = post("/api/datatypes/apply", A(system="planning", mode="each", changes=[{"field_id": "fldD01", "type": "Single select"}])); check("a Planning value off the list is refused with the list in the message", s == 400 and "long-text" in e["error"], e)
s, r = post("/api/datatypes/apply", A(system="planning", mode="each", changes=[{"field_id": "fldD02", "type": "currency"}]))
check("a single-field override is recorded as an override", s == 200 and {f["field_id"]: f for f in r["review"]["fields"]}["fldD02"]["planning"]["decision"]["source"] == "override")

# ---- a value off the list is cleared by a person
c = sqlite3.connect("datatypes_api.db"); c.execute("update field_names set target_type='Number' where airtable_field_id='fldD02'"); c.commit(); c.close()
s, rv = get("/api/datatypes/review?base_id=" + BASE); check("a stored value off the list shows as invalid", rv["counts"]["planning"]["invalid"] == 1)
s, r = post("/api/datatypes/clear-invalid", A()); check("clearing it empties the value and says how many", s == 200 and r["cleared"] == {"planning": 1, "core": 0} and r["review"]["counts"]["planning"] == {"set": 3, "unset": 1, "invalid": 0}, r.get("error"))
s, r = post("/api/datatypes/default", A(system="planning", airtable_type="number", type="percentage"))
check("a default can be edited, and the proposal follows", s == 200 and {f["field_id"]: f for f in r["review"]["fields"]}["fldD02"]["planning"]["proposed"]["type"] == "percentage", r.get("error"))
s, e = post("/api/datatypes/default", A(system="core", airtable_type="number", type="NMBR")); check("a Core default needs both values", s == 400 and "displayType" in e["error"], e)
c = sqlite3.connect("datatypes_api.db")
check("the history holds every change with who and why", c.execute("select count(*) from data_type_events where actor='JV' and reason='api test'").fetchone()[0] >= 8 and c.execute("select count(*) from data_type_events where event_type='clear'").fetchone()[0] == 1)
c.close()

print("ALL PASS" if ok else "FAILURES")
sys.exit(0 if ok else 1)
