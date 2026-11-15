"""Tests for postmantests.py (the Postman test guide and collection) and its pages. Needs the app on port 8765 for the HTTP part. No token, no Workfront call."""
import json, os, re, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import postmantests as pt
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])

g = pt.guide()
T = g["tests"]
check("there are exactly five tests, in order, each with a purpose, steps, a way to read the result and a cleanup", [t["id"] for t in T] == [1, 2, 3, 4, 5]
      and all(t["purpose"] and t["steps"] and t["results"] and t["cleanup"] and t["also_record"] for t in T))
check("the tests are the ones the design asked for", [t["key"] for t in T] == ["record-type-name-uniqueness", "patch-on-fields", "description-on-create", "reference-field-back-reference", "bulk-create-id-order"])
# every route used must be one the saved v2 reference lists
ROUTES = {("POST", "/v2/workspaces"), ("POST", "/v2/workspaces/{id}/record-types"), ("DELETE", "/v2/workspaces/{id}"), ("DELETE", "/v2/record-types/{id}"), ("GET", "/v2/record-types/{id}"),
          ("POST", "/v2/record-types/{id}/fields"), ("GET", "/v2/record-types/{id}/fields"), ("GET", "/v2/fields/{id}"), ("PATCH", "/v2/fields/{id}"), ("DELETE", "/v2/fields/{id}"),
          ("POST", "/v2/record-types/{id}/records/bulk"), ("GET", "/v2/record-types/{id}/records")}
norm = lambda p: re.sub(r"\{\{[a-z0-9_]+\}\}", "{id}", p.split("?")[0])
used = {(s["method"], norm(s["path"])) for t in T for s in t["steps"]} | {(m, norm(p)) for t in T for m, p in t["cleanup"]}
check("every request uses a route that the saved v2 reference lists", used <= ROUTES, sorted(used - ROUTES))
check("no path or body contains a token, a password or a real address", not re.search(r"(?i)bearer|token|secret|password|@|https?://(?!\{)", json.dumps(g["tests"])), "")
body = lambda t, k: t["steps"][k]["body"]
check("test 2 sends a PATCH with only the changed property", [s["body"] for s in T[1]["steps"] if s["method"] == "PATCH"][0] == {"description": "after"})
check("test 3 sends the real marker, and descriptions of exactly 512 and 513 characters", body(T[2], 1)["description"].startswith('Fusion-driven: written by scenario "Order sync".')
      and len(pt.LONG_512) == 512 and len(pt.LONG_513) == 513 and {v[0]: v[1] for v in pt.VARIABLES}["long_description_513"] == pt.LONG_513)
check("test 4 creates the link with the back reference on, and a control with it off", [s["body"]["referenceOptions"]["createBackReference"] for s in T[3]["steps"] if s["body"] and "referenceOptions" in s["body"]] == [True, False])
check("test 5 bulk-creates a JSON array of records each with a distinct value, under the documented limit of 100", isinstance(body(T[4], 2), list) and len(body(T[4], 2)) == 5 and len(body(T[4], 2)) <= 100
      and len({json.dumps(r) for r in body(T[4], 2)}) == 5)
check("every throwaway name starts with the ZZ prefix", all(s["body"]["displayName"].lower().startswith(pt.PREFIX.lower()) for t in T for s in t["steps"] if isinstance(s["body"], dict) and "displayName" in s["body"]))
check("every variable a path uses is declared", {m for t in T for s in t["steps"] for m in re.findall(r"\{\{([a-z0-9_]+)\}\}", s["path"] + json.dumps(s["body"]))} <= {v[0] for v in pt.VARIABLES})
check("the guide is the same every time", json.dumps(pt.guide(), sort_keys=True) == json.dumps(pt.guide(), sort_keys=True) and json.dumps(pt.collection()) == json.dumps(pt.collection()))

c = pt.collection()
check("the collection is Postman v2.1, one folder per test, and holds every step and cleanup", c["info"]["schema"].endswith("v2.1.0/collection.json") and len(c["item"]) == 5
      and sum(len(f["item"]) for f in c["item"]) == sum(len(t["steps"]) + len(t["cleanup"]) for t in T))
check("the collection has variables and NO authorization anywhere", {v["key"] for v in c["variable"]} == {v[0] for v in pt.VARIABLES} and '"auth"' not in json.dumps(c) and "Authorization" not in json.dumps(c))
req = c["item"][0]["item"][1]["request"]
check("a request has the host as a variable, the path split, a JSON header and a raw JSON body", req["url"]["raw"] == "https://{{customer_domain}}/maestro/api/v2/workspaces/{{workspace_id}}/record-types"
      and req["url"]["path"][:3] == ["maestro", "api", "v2"] and req["header"][0]["value"] == "application/json" and json.loads(req["body"]["raw"]) == {"displayName": "ZZ Migration Test Name"})
check("a request with a query string splits it", [x for f in c["item"] for i in f["item"] for x in [i["request"]["url"]] if "query" in x][0]["query"] == [{"key": "limit", "value": "100"}])
check("a step that creates something saves its ID, only when the call worked", "pm.collectionVariables.set(\"rt_a_id\"" in "".join(c["item"][0]["item"][1]["event"][0]["script"]["exec"]) and "pm.response.code >= 200" in "".join(c["item"][0]["item"][1]["event"][0]["script"]["exec"]))
check("the primary field is saved from primaryFieldId, never from the record type's own ID", "j.primaryFieldId" in "".join(c["item"][4]["item"][1]["event"][0]["script"]["exec"]) and "j.id" not in "".join(c["item"][4]["item"][1]["event"][0]["script"]["exec"]))

# ---- over HTTP
B = "http://127.0.0.1:8765"
def get(p):
    with urllib.request.urlopen(B + p) as r:
        return r.status, r.read(), r.headers
s, d, h = get("/api/postman/guide")
check("the guide is served without a database or a token", s == 200 and json.loads(d)["tests"][0]["title"] == "Record type name uniqueness")
s, d, h = get("/api/postman/collection.json")
check("the collection downloads as a file", s == 200 and "attachment" in h["Content-Disposition"] and "postman_collection.json" in h["Content-Disposition"] and json.loads(d)["info"]["name"].startswith("Workfront Planning v2"))
s, d, h = get("/postman")
check("the page is served with its script and in the navigation", s == 200 and b"Postman tests" in d and get("/postman.js")[0] == 200 and b"/postman" in get("/common.js")[1])
sys.exit(0 if ok else 1)
