"""HTTP test for /api/field-writers and its .csv export. Fabricated schema; the recorded automations and forms are written straight into the test database."""
import json, sqlite3, sys, urllib.request
B = "http://127.0.0.1:8765"; ok = True; BASE = "appWriters00001"
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])
def call(method, p, b=None, raw=False):
    req = urllib.request.Request(B + p, method=method, data=None if b is None else json.dumps(b).encode(), headers={"Content-Type": "application/json"} if method == "POST" else {})
    try:
        with urllib.request.urlopen(req) as r:
            d = r.read(); return r.status, (d if raw else json.loads(d))
    except urllib.error.HTTPError as e:
        d = e.read(); return e.code, (d if raw else json.loads(d))
post = lambda p, b: call("POST", p, b)
def fld(i, n, t="singleLineText", o=None):
    d = {"id": "fld" + i, "name": n, "type": t}
    if o: d["options"] = o
    return d
T1, T2 = "tblWr0000000001", "tblWr0000000002"
schema = {"tables": [{"id": T1, "name": "Orders", "primaryFieldId": "fldW01", "views": [], "fields": [fld("W01", "Title"), fld("W02", "Late fee", "currency", {"precision": 2, "symbol": "$"}), fld("W03", "Notes"),
                                                                                                    fld("W04", "Total", "formula", {"formula": "1", "isValid": True}), fld("W05", "Created", "createdTime", {"result": {"type": "dateTime"}})]},
                     {"id": T2, "name": "Archive", "primaryFieldId": "fldX01", "views": [], "fields": [fld("X01", "Name"), fld("X02", "Flag")]}]}
post("/api/db/create", {"path": "writers.db"}); post("/api/db/connect", {"path": "writers.db"})
s, r = post("/api/schema/ingest", {"base_id": BASE, "source": "json", "schema_json": json.dumps(schema)})
check("fabricated schema ingested", r.get("tables") == 2, r)
s, r = call("GET", "/api/field-writers?base_id=" + BASE)
check("with nothing recorded every field is plain data, formula-only or filled by Airtable, and none is Fusion-driven", s == 200 and r["summary"]["fusion_fields"] == 0 and r["summary"]["categories"]["formula_only"] == 1 and r["summary"]["categories"]["system"] == 1 and r["summary"]["categories"]["user_plain"] == 5 and r["summary"]["fields"] == 7, r["summary"])
c = sqlite3.connect("writers.db")
def spec(aid, name, ver, scenario):
    cur = c.execute("INSERT INTO automation_specs(automation_id, version_no, automation_name, base_id, trigger_type, target_scenario_name, spec_json) VALUES (?,?,?,?,?,?,?)", (aid, ver, name, BASE, "record_created", scenario, "{}"))
    c.execute("INSERT INTO automation_items(spec_id, item_id, item_order, path, item_kind, step_type, table_id) VALUES (?,?,?,?,?,?,?)", (cur.lastrowid, "s1", 1, "1", "step", "update_record", T1))
    return cur.lastrowid
old = spec("aLate", "Late fee", 1, None)
c.execute("INSERT INTO automation_field_refs(spec_id, item_id, ref_order, field_id, role) VALUES (?,?,?,?,?)", (old, "s1", 0, "fldW03", "set"))      # an older version: must not count
new = spec("aLate", "Late fee", 2, "Order sync")
for k, (fid, role) in enumerate((("fldW02", "set"), ("fldW01", "condition"), ("fldW03", "read"))):
    c.execute("INSERT INTO automation_field_refs(spec_id, item_id, ref_order, field_id, role, detail) VALUES (?,?,?,?,?,?)", (new, "s1", k, fid, role, "SECRETVALUE"))
fs = c.execute("INSERT INTO form_specs(form_id, version_no, form_name, base_id, table_id, spec_json) VALUES ('fmIntake', 1, 'Intake', ?, ?, '{}')", (BASE, T1)).lastrowid
c.execute("INSERT INTO form_items(spec_id, item_id, item_order, path, item_type, airtable_field_id) VALUES (?,?,?,?,?,?)", (fs, "i1", 1, "1", "field", "fldW02"))
c.execute("INSERT INTO form_items(spec_id, item_id, item_order, path, item_type, airtable_field_id) VALUES (?,?,?,?,?,?)", (fs, "i2", 2, "2", "field", "fldW03"))
c.commit()
s, r = call("GET", "/api/field-writers?base_id=" + BASE)
E = {e["field_name"]: e for e in r["entries"]}
check("a field an automation sets and a form fills is mixed and carries the marker with the scenario name", E["Late fee"]["category"] == "mixed" and E["Late fee"]["fusion"] and E["Late fee"]["marker"] == 'Fusion-driven: written by scenario "Order sync".' and E["Late fee"]["writers"]["form"] == ["Intake"], E["Late fee"])
check("a field the automation only reads, or the older version set, is not Fusion-driven", not E["Notes"]["fusion"] and E["Notes"]["category"] == "user_plain" and not E["Title"]["fusion"])
check("a formula needs a decision; a field Airtable fills does not", E["Total"]["needs_decision"] and E["Total"]["category"] == "formula_only" and not E["Created"]["needs_decision"] and r["summary"]["needs_decision"] == 1)
check("entries are listed by table then field name", [e["table_name"] for e in r["entries"]] == sorted([e["table_name"] for e in r["entries"]], key=str.lower))
check("no recorded detail or value reaches the response", "SECRETVALUE" not in json.dumps(r))
post("/api/names/tables/scope", {"base_id": BASE, "actor": "JV", "items": [{"table_id": T2, "decision": "out", "reason": "test"}]})
s, r = call("GET", "/api/field-writers?base_id=" + BASE)
check("a table out of scope is left out", s == 200 and r["summary"]["fields"] == 5 and not any(e["table_id"] == T2 for e in r["entries"]), r["summary"])
s, csvb = call("GET", "/api/export/field_writers.csv?base_id=" + BASE, raw=True)
text = csvb.decode("utf-8-sig") if s == 200 else ""
check("the .csv has a header, one row per field and the marker, and no recorded detail", s == 200 and text.splitlines()[0].startswith("table_name,table_id,field_name") and len(text.splitlines()) == 6 and 'scenario ""Order sync""' in text and "SECRETVALUE" not in text, text[:300])
check("a bad base ID is refused", call("GET", "/api/field-writers?base_id=x%20y")[0] == 400)
# ---- decisions
D = lambda items, **k: post("/api/field-writers/decide", dict({"base_id": BASE, "actor": "JV", "items": items}, **k))
s, r = call("GET", "/api/field-writers?base_id=" + BASE)
check("decisions are available once migration 019 is in the database", r["decisions_available"] is True and r["decisions"]["formula_only"] == ["recompute", "park"])
check("a decision without who is refused", D([{"field_id": "fldW04", "decision": "park"}], actor="")[0] == 400)
check("a decision that does not fit the field is refused", D([{"field_id": "fldW04", "decision": "fusion_owned"}])[0] == 400 and D([{"field_id": "fldW03", "decision": "park"}])[0] == 400)
check("a field of another base or an out-of-scope table is refused", D([{"field_id": "fldX01", "decision": "park"}])[0] == 404 and D([{"field_id": "fldNope", "decision": "park"}])[0] == 404)
check("nothing to save is refused", D([])[0] == 400)
s, r = D([{"field_id": "fldW04", "decision": "park"}], reason="not needed in the target")
E = {e["field_name"]: e for e in r["entries"]}
check("a decision is saved with who and why and settles the field", s == 200 and r["saved"] == ["decided"] and E["Total"]["decision"]["decided_by"] == "JV" and E["Total"]["decision"]["reason"] == "not needed in the target" and not E["Total"]["needs_decision"] and r["summary"]["needs_decision"] == 0, r.get("saved"))
c2 = sqlite3.connect("writers.db")
check("the history has the event", [tuple(x) for x in c2.execute("SELECT event_type, old_decision, new_decision, actor FROM field_writer_events")] == [("decide", None, "park", "JV")])
c2.execute("INSERT INTO automation_field_refs(spec_id, item_id, ref_order, field_id, role) VALUES (?,?,?,?,?)", (new, "s1", 9, "fldW01", "set")); c2.commit()
s, r = D([{"field_id": "fldW01", "decision": "users_also_edit"}])
E = {e["field_name"]: e for e in r["entries"]}
check("an automation-only field can be marked 'people also edit it': it becomes mixed and keeps its marker", s == 200 and E["Title"]["category"] == "mixed" and E["Title"]["base_category"] == "automation_only" and E["Title"]["marker"], E["Title"])
s, r = D([{"field_id": "fldW04", "decision": "clear"}, {"field_id": "fldW01", "decision": "clear"}])
E = {e["field_name"]: e for e in r["entries"]}
check("clearing returns both fields to needing a decision", s == 200 and r["saved"] == ["cleared", "cleared"] and E["Total"]["needs_decision"] and E["Title"]["category"] == "automation_only" and r["summary"]["needs_decision"] == 2, r["summary"])
check("the .csv shows the decision column", b"decision" in call("GET", "/api/export/field_writers.csv?base_id=" + BASE, raw=True)[1].splitlines()[0])
sys.exit(0 if ok else 1)
