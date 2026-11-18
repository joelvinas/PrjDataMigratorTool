"""HTTP test for Stage 1 step 3: discarding tables at the ingest step. Fabricated schema only."""
import csv, io, json, os, urllib.request
B = "http://127.0.0.1:8765"; ok = True; BASE = "appDiscard00001"
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])
def call(m, p, b=None):
    req = urllib.request.Request(B + p, method=m, data=json.dumps(b).encode() if b is not None else None, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r: return r.status, r.read()
    except urllib.error.HTTPError as e: return e.code, e.read()
def post(p, b):
    s, d = call("POST", p, b); return s, json.loads(d)
def get(p):
    s, d = call("GET", p); return s, json.loads(d)
def fld(i, n, t="singleLineText", o=None):
    d = {"id": "fld" + i, "name": n, "type": t}
    if o: d["options"] = o
    return d
T1, T2, T3 = "tblDc0000000001", "tblDc0000000002", "tblDc0000000003"
schema = {"tables": [
    {"id": T1, "name": "Jobs", "primaryFieldId": "fldJ01", "views": [], "fields": [fld("J01", "Title"), fld("J02", "Client", "multipleRecordLinks", {"linkedTableId": T2}), fld("J03", "Scratch", "multipleRecordLinks", {"linkedTableId": T3})]},
    {"id": T2, "name": "Clients", "primaryFieldId": "fldK01", "views": [], "fields": [fld("K01", "Name")]},
    {"id": T3, "name": "Scratch pad", "primaryFieldId": "fldL01", "views": [], "fields": [fld("L01", "Text"), fld("L02", "Back", "multipleRecordLinks", {"linkedTableId": T1})]}]}
post("/api/db/create", {"path": "discard.db"}); post("/api/db/connect", {"path": "discard.db"})
s, r = post("/api/schema/ingest", {"base_id": BASE, "source": "json", "schema_json": json.dumps(schema)})
check("fabricated schema ingested", r.get("tables") == 3, r)
tabs = lambda: {t["table_id"]: t for b in get("/api/schema")[1]["bases"] if b["base_id"] == BASE for t in b["tables"]}
check("nothing is discarded to start with", not any(t["discarded"] for t in tabs().values()))
s, e = post("/api/schema/discard", {"base_id": BASE, "items": [{"table_id": T3, "discard": True, "note": "scratch"}]})
check("initials are required", s == 400 and "initials" in e["error"], e)
s, e = post("/api/schema/discard", {"base_id": BASE, "actor": "JV", "items": [{"table_id": T3, "discard": True, "note": "  "}]})
check("a note is required to discard", s == 400 and "note" in e["error"].lower(), e)
check("a refused request changes nothing", not tabs()[T3]["discarded"])
s, e = post("/api/schema/discard", {"base_id": BASE, "actor": "JV", "items": []})
check("an empty batch is refused", s == 400, e)
s, e = post("/api/schema/discard", {"base_id": BASE, "actor": "JV", "items": [{"table_id": "tblNope", "discard": True, "note": "x"}]})
check("an unknown table is refused", s == 400, e)

s, r = post("/api/schema/discard", {"base_id": BASE, "actor": "JV", "items": [{"table_id": T3, "discard": True, "note": "Scratch space, not business data"}]})
check("a table is discarded with a note", s == 200 and r["changed"] == 1, r)
t = tabs()[T3]
check("the schema view shows who, when and why", t["discarded"] and t["discard_note"] == "Scratch space, not business data" and t["discarded_by"] == "JV" and t["discarded_at"], t)
check("the other tables are untouched", not tabs()[T1]["discarded"] and not tabs()[T2]["discarded"])

s, g = post("/api/depgraph/recompute", {"mode": "literal"})
mine = [m for m in g["matrix"] if m["base_id"] == BASE]
check("load order leaves the discarded table out", {m["table_id"] for m in mine} == {T1, T2}, [m["table_id"] for m in mine])
check("links to it are dropped: not ordered, flagged or listed", not g["out_of_scope_links"] and not any(T3 in (e["parent"], e["child"]) for e in g["edges"]) and not any(l["linked_table_id"] == T3 or l["table_id"] == T3 for l in g["link_fields"]), g["out_of_scope_links"])
s, d = get("/api/depgraph?mode=literal")
check("the stored order does not hold it either", all(r_["table_id"] != T3 for r_ in d["stored_matrix"]), d.get("stored_matrix"))
s, body = call("GET", "/api/export/tables.csv?base_id=" + BASE)
rows = {r_["table_id"]: r_ for r_ in csv.DictReader(io.StringIO(body.decode("utf-8")))}
check("the tables download still lists the discarded table, marked, with the note",
      set(rows) == {T1, T2, T3} and rows[T3]["status"] == "discarded" and rows[T3]["discard_note"] == "Scratch space, not business data" and rows[T3]["discarded_by"] == "JV" and rows[T3]["discarded_at"]
      and rows[T1]["status"] == "kept" and rows[T1]["discard_note"] == "", rows[T3])

s, r = post("/api/profile/start", {"scope": "tables", "table_ids": [T3]})
check("profiling refuses a discarded table", s == 400 and "not in the database" in r["error"], r)
s, r = post("/api/profile/start", {"scope": "table", "table_id": T3})
check("profiling one discarded table finds nothing to profile", s == 400 and "no tables to profile" in r["error"].lower(), r)

s, r = post("/api/schema/discard", {"base_id": BASE, "actor": "JV", "items": [{"table_id": T3, "discard": False}]})
check("a discarded table can be brought back", s == 200 and not tabs()[T3]["discarded"], r)
s, g = post("/api/depgraph/recompute", {"mode": "literal"})
check("and it returns to the load order", T3 in {m["table_id"] for m in g["matrix"] if m["base_id"] == BASE})
s, body = call("GET", "/api/export/tables.csv?base_id=" + BASE)
rows = {r_["table_id"]: r_ for r_ in csv.DictReader(io.StringIO(body.decode("utf-8")))}
check("and is marked kept again", rows[T3]["status"] == "kept")
print("ALL PASS" if ok else "SOME FAILED"); raise SystemExit(0 if ok else 1)
