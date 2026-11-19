"""HTTP tests for the lookup list endpoints (/api/lookups/*) and the /lookups page. Fabricated schema only: the place and option names below are made up."""
import json, os, sqlite3, subprocess, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))
import typesfixture as tf
B = "http://127.0.0.1:8765"; ok = True; BASE = "appLookupApi001"
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
def A(**k): return dict({"base_id": BASE, "actor": "JV", "reason": "api test", "threshold": 5}, **k)

def choices(names): return {"choices": [{"id": "sel%03d" % i, "name": n} for i, n in enumerate(names)]}
def fld(i, n, t="singleLineText", o=None):
    d = {"id": "fld" + i, "name": n, "type": t}
    if o: d["options"] = o
    return d
PLACES = ["Alpha Town", "Beta City", "Gamma Bay", "Delta Ridge", "Echo Falls", "Alpha town", "Foxtrot Hill"]          # "Alpha Town" and "Alpha town" repeat once case is ignored
schema = {"tables": [{"id": "tblLk0000000001", "name": "Orders", "primaryFieldId": "fldL01", "views": [], "fields": [
    fld("L01", "Order Name"), fld("L02", "Ship To", "multipleSelects", choices(PLACES)), fld("L03", "Status", "singleSelect", choices(["Open", "Closed"])),
    fld("L04", "Origin", "singleSelect", choices(["Alpha Town", "Beta City", "Gamma Bay", "Delta Ridge", "Echo Falls", "Golf Bend"]))]},
    {"id": "tblLk0000000002", "name": "Visits", "primaryFieldId": "fldM01", "views": [], "fields": [fld("M01", "Visit Name"), fld("M02", "Place", "multipleSelects", choices(PLACES[:5]))]}]}
post("/api/db/create", {"path": "lookups_api.db"})
_c = sqlite3.connect("lookups_api.db"); tf.strip_drafts(_c); _c.close()   # an older database: the library now holds 021 to 025
post("/api/db/connect", {"path": "lookups_api.db"})
_, r = post("/api/schema/ingest", {"base_id": BASE, "source": "json", "schema_json": json.dumps(schema)})
check("fabricated schema ingested", r.get("tables") == 2 and r.get("fields") == 6, r)

s, d, h = raw("GET", "/lookups"); check("the page is served, with a keyboard-focusable scrolling window", s == 200 and b"Lookup lists" in d and b'class="tablewin"' in d and h["Content-Type"].startswith("text/html"))
s, d, h = raw("GET", "/lookups.js"); check("the page script is served", s == 200 and b"/api/lookups/assign" in d and "javascript" in h["Content-Type"])
s, d, h = raw("GET", "/common.js")
check("the page is in the navigation, before Load a table", b'"/lookups"' in d and d.index(b'"/lookups"') < d.index(b'"/loadtable"'))

# ---- before the migration
s, rv = get("/api/lookups/review?base_id=" + BASE + "&threshold=5")
f = {x["field_id"]: x for x in rv["fields"]}
check("review works before the migration and says what is missing", s == 200 and rv["ready"] is False and "lookup_lists" in rv["missing"] and rv["lists"] == [], rv.get("missing"))
check("only select fields are listed, with their option counts; long ones are flagged over the threshold", sorted(f) == ["fldL02", "fldL03", "fldL04", "fldM02"]
      and f["fldL02"]["option_count"] == 7 and f["fldL02"]["long"] and not f["fldL03"]["long"] and f["fldL04"]["long"] and f["fldM02"]["long"], {k: v["option_count"] for k, v in f.items()})
check("options that repeat once case is ignored are counted, and a proposed list name comes from the field name", f["fldL02"]["repeat_count"] == 1 and f["fldL04"]["repeat_count"] == 0 and f["fldL02"]["proposed_name"] == "Ship To", f["fldL02"])
check("the most options come first, and the order is stable", [x["field_id"] for x in rv["fields"]] == ["fldL02", "fldL04", "fldM02", "fldL03"], [x["field_id"] for x in rv["fields"]])
check("the threshold is counted in the summary", rv["counts"]["select_fields"] == 4 and rv["counts"]["long"] == 3 and rv["counts"]["long_not_decided"] == 3 and rv["counts"]["repeats"] == 1, rv["counts"])
check("a bad threshold or base is refused", get("/api/lookups/review?base_id=" + BASE + "&threshold=0")[0] == 400 and get("/api/lookups/review?base_id=nope")[0] == 400)
s, e = post("/api/lookups/assign", A(field_ids=["fldL02"], list_name="Places")); check("saving is refused with a plain message until the migration is applied", s == 409 and "migration 024" in e["error"], e)

# ---- connect reports 024 as pending, and the script applies it
s, r = post("/api/db/connect", {"path": "lookups_api.db"})
check("connect names migration 024 among the pending drafts", s == 200 and 24 in [p["migration"] for p in r["pending_migrations"]], r.get("pending_migrations"))
p = subprocess.run([sys.executable, os.path.join("sql", "apply_migrations.py"), "lookups_api.db"], capture_output=True, text=True)
check("the script applies the drafts", p.returncode == 0 and "024" in p.stdout, p.stdout + p.stderr)
s, rv = get("/api/lookups/review?base_id=" + BASE + "&threshold=5"); check("after the migration the page is ready", rv["ready"] is True and rv["missing"] == [])

# ---- deciding
s, e = post("/api/lookups/assign", {"base_id": BASE, "field_ids": ["fldL02"], "list_name": "Places"}); check("initials are required", s == 400 and "initials" in e["error"], e)
s, e = post("/api/lookups/assign", {"base_id": BASE, "actor": "JV", "field_ids": ["fldL02"], "list_name": "Places"}); check("a reason is required", s == 400 and "reason" in e["error"], e)
s, e = post("/api/lookups/assign", A(field_ids=["fldL01"], list_name="Places")); check("a field that is not a select field is refused", s == 404 and "select field" in e["error"], e)
s, e = post("/api/lookups/assign", A(field_ids=["fldL02"])); check("a list or a new name is required", s == 400 and "existing list" in e["error"], e)
s, e = post("/api/lookups/assign", A(field_ids=["fldL02"], list_name="Orders")); check("a name equal to a table's name is refused", s == 400 and "table Orders" in e["error"], e)
s, e = post("/api/lookups/assign", A(field_ids=["fldL02"], list_name="Plac_es!")); check("a name that breaks the Planning rules is refused, with a proposal", s == 400 and "naming rules" in e["error"] and "Proposed" in e["error"], e)
s, rv = get("/api/lookups/review?base_id=" + BASE + "&threshold=5"); check("nothing was saved by the refusals", rv["lists"] == [] and rv["counts"]["authorized"] == 0)

s, r = post("/api/lookups/assign", A(field_ids=["fldL02", "fldL04"], list_name="Places"))
f = {x["field_id"]: x for x in r["review"]["fields"]}
check("two fields are authorized into one new list in one batch, and the review comes back", s == 200 and r["created"] and r["changed"] == 2 and r["batch"] == "L1" and f["fldL02"]["list"]["name"] == "Places"
      and f["fldL04"]["list"]["by"] == "JV" and f["fldL04"]["list"]["reason"] == "api test" and r["review"]["counts"]["authorized"] == 2 and r["review"]["counts"]["long_not_decided"] == 1, r.get("error") or r["review"]["counts"])
lst = r["review"]["lists"][0]
check("the list counts the different options across its fields, ignoring case and spacing", lst["name"] == "Places" and lst["option_count"] == 7 and len(lst["field_ids"]) == 2 and lst["field_names"] == ["Orders.Ship To", "Orders.Origin"], lst)
lid = lst["list_id"]
s, e = post("/api/lookups/assign", A(field_ids=["fldM02"], list_name="places")); check("a name equal to another list is refused ignoring case", s == 400 and "lookup list Places" in e["error"], e)
s, r = post("/api/lookups/assign", A(field_ids=["fldM02", "fldL02"], list_id=lid))
check("a third field joins the existing list; a field already there is unchanged", s == 200 and not r["created"] and r["changed"] == 1 and r["unchanged"] == 1 and r["list_id"] == lid, r.get("error") or r)
s, e = post("/api/lookups/assign", A(field_ids=["fldL03"], list_id=9999)); check("an unknown list is refused", s == 404, e)
s, e = post("/api/lookups/delete", A(list_id=lid)); check("a list that fields still use cannot be deleted", s == 409 and "still use" in e["error"], e)
s, r = post("/api/lookups/rename", A(list_id=lid, name="Locations"))
check("a list can be renamed", s == 200 and r["review"]["lists"][0]["name"] == "Locations", r.get("error"))
s, e = post("/api/lookups/rename", A(list_id=lid, name="Visits")); check("a rename to a table name is refused", s == 400 and "table Visits" in e["error"], e)
s, r = post("/api/lookups/unassign", A(field_ids=["fldL04", "fldL03"]))
check("fields return to select fields; a field that never had a list is unchanged", s == 200 and r["changed"] == 1 and r["unchanged"] == 1 and {x["field_id"]: x for x in r["review"]["fields"]}["fldL04"]["list"] is None, r.get("error"))
post("/api/lookups/unassign", A(field_ids=["fldL02", "fldM02"]))
s, r = post("/api/lookups/delete", A(list_id=lid)); check("an unused list can be deleted", s == 200 and r["review"]["lists"] == [], r.get("error"))

# ---- the history
c = sqlite3.connect("lookups_api.db")
ev = [r[0] for r in c.execute("select event_type from lookup_list_events order by event_id")]
check("the history holds every change, in order, with who and why", ev == ["list_create", "assign", "assign", "assign", "list_rename", "unassign", "unassign", "unassign", "list_delete"] and
      c.execute("select count(*) from lookup_list_events where actor='JV' and reason='api test'").fetchone()[0] == len(ev), ev)
check("only keys and names are stored: no option text and no record value", not [t for t in ("lookup_lists", "field_lookup_decisions", "lookup_list_events") for col in c.execute("pragma table_info(%s)" % t) if col[1] in ("options", "choices", "value", "values")])
c.close()

print("ALL PASS" if ok else "FAILURES")
sys.exit(0 if ok else 1)
