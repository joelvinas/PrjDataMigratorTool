"""HTTP test for /api/core/targets, /api/core/object-type and /api/core/parent. Fabricated schema and IDs only."""
import json, sys, urllib.request, urllib.error
B = "http://127.0.0.1:8765"; ok = True; BASE = "appCoreTgt00001"
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])
def call(method, p, b=None):
    req = urllib.request.Request(B + p, method=method, data=None if b is None else json.dumps(b).encode(), headers={"Content-Type": "application/json"} if method == "POST" else {})
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
post = lambda p, b: call("POST", p, b)
def fld(i, n): return {"id": "fld" + i, "name": n, "type": "singleLineText"}
T1, T2, T3 = "tblCt0000000001", "tblCt0000000002", "tblCt0000000003"
GUID = "fedcba9876543210fedcba9876543210"
schema = {"tables": [{"id": T1, "name": "Requests", "primaryFieldId": "fldQ01", "views": [], "fields": [fld("Q01", "Title")]},
                     {"id": T2, "name": "Programs", "primaryFieldId": "fldR01", "views": [], "fields": [fld("R01", "Name")]},
                     {"id": T3, "name": "Reference", "primaryFieldId": "fldS01", "views": [], "fields": [fld("S01", "Name")]}]}
post("/api/db/create", {"path": "coretgt.db"}); post("/api/db/connect", {"path": "coretgt.db"})
s, r = post("/api/schema/ingest", {"base_id": BASE, "source": "json", "schema_json": json.dumps(schema)})
check("fabricated schema ingested", r.get("tables") == 3, r)
post("/api/names/tables/scope", {"base_id": BASE, "actor": "JV", "items": [{"table_id": T1, "decision": "in", "target": "planning_and_core"}, {"table_id": T2, "decision": "in", "target": "planning_and_core"},
                                                                            {"table_id": T3, "decision": "in", "target": "planning_only"}]})
s, r = call("GET", "/api/core/targets?base_id=" + BASE)
check("only the Planning-plus-Core tables are listed, undecided, with the allowed parents for each object type", s == 200 and [x["table_name"] for x in r["rows"]] == ["Programs", "Requests"] and all(not x["ready"] for x in r["rows"])
      and r["parent_codes"] == {"project": ["PRGM", "PORT"], "request": ["PROJ"], "task": ["PROJ", "TASK"]} and {t["type"]: t["code"] for t in r["object_types"]} == {"project": "PROJ", "request": "OPTASK", "task": "TASK"}, r)
s, r = post("/api/core/object-type", {"base_id": BASE, "table_id": T1, "object_type": "request", "actor": "JV", "reason": "queue"})
check("the object type is decided and the table now needs a parent", s == 200 and r["result"] == "decided" and [x for x in r["rows"] if x["table_id"] == T1][0]["problem"] == "The parent is not decided.", r)
check("a decision with no initials is refused", post("/api/core/object-type", {"base_id": BASE, "table_id": T2, "object_type": "project", "actor": ""})[0] == 400)
check("a Planning-only table cannot be decided", post("/api/core/object-type", {"base_id": BASE, "table_id": T3, "object_type": "project", "actor": "JV"})[0] == 400)
s, r = post("/api/core/parent", {"base_id": BASE, "table_id": T1, "parent_code": "PRGM", "parent_id": GUID, "actor": "JV"})
check("a parent the object type does not allow is refused with a plain message", s == 400 and "goes under a project" in r.get("error", ""), r)
s, r = post("/api/core/parent", {"base_id": BASE, "table_id": T1, "parent_code": "PROJ", "parent_id": GUID, "actor": "JV", "reason": "the intake queue"})
row = [x for x in r["rows"] if x["table_id"] == T1][0]
check("the parent is saved and the table is ready, with who and why", s == 200 and row["ready"] and row["parent_obj_id"] == GUID and row["parent_by"] == "JV" and row["parent_reason"] == "the intake queue", r)
post("/api/core/object-type", {"base_id": BASE, "table_id": T2, "object_type": "project", "actor": "JV"})
s, r = post("/api/core/parent", {"base_id": BASE, "table_id": T2, "none": True, "actor": "JV", "reason": "top level"})
check("a project can be decided to have no parent", s == 200 and [x for x in r["rows"] if x["table_id"] == T2][0]["ready"], r)
s, r = post("/api/core/parent", {"base_id": BASE, "table_id": T1, "clear": True, "actor": "JV", "reason": "wrong queue"})
check("a parent can be cleared and the history keeps what it was", s == 200 and r["result"] == "cleared" and any(e["subject"] == "parent" and e["event_type"] == "clear" and e["old_value"] == "PROJ:" + GUID and e["reason"] == "wrong queue" for e in r["history"]), r["history"][:2])
check("a bad base ID is refused", call("GET", "/api/core/targets?base_id=x%20y")[0] == 400)
for path, word in (("/load", "Where Core objects go"), ("/load.js", "32 hexadecimal characters")):
    with urllib.request.urlopen(B + path) as resp:
        check("%s serves the decision screen, with the 32-character check in the page" % path, resp.status == 200 and word in resp.read().decode("utf-8"))
sys.exit(0 if ok else 1)
