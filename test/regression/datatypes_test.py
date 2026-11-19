"""Tests for the data type mapping: the lists (datatypes.py), the saved decisions, the default mapping and bulk updates (datatypes_store.py), the importer's
handling of values off the list, and the load plan using each system's own type. Fabricated names only; a throwaway database built through the real functions."""
import base64, os, sqlite3, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
import datatypes as dt, datatypes_store as dts, fieldnames_io as fio, fieldnames_store as st, loadplan as lp, typesfixture as tf
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])
def refused(fn, *a, **k):
    try:
        fn(*a, **k); return None
    except st.StoreError as e:
        return e

# ---- the lists
check("Planning has exactly the 17 types in the API documentation", len(dt.PLANNING_TYPES) == 17 and dt.PLANNING_TYPES[0] == "text" and dt.PLANNING_TYPES[-1] == "record-id" and len(set(dt.PLANNING_TYPES)) == 17)
check("a Planning value is matched ignoring case and edge spaces, and nothing else is accepted", dt.planning_type(" Single-Select ") == "single-select" and dt.planning_type("Single select") is None
      and dt.planning_type("multiline") is None and dt.planning_type("") is None and dt.planning_type(None) is None)
check("a Core pair needs BOTH values on their lists", dt.core_pair("text", "txta") == ("TEXT", "TXTA") and dt.core_pair("TEXT", None) is None and dt.core_pair(None, "TEXT") is None and dt.core_pair("TEXT", "BOX") is None
      and dt.core_pair("STRING", "TEXT") is None)
check("state: unset, set (canonical only) and invalid", [dt.state("planning", x) for x in (None, "text", "Text", "Single select")] == ["unset", "set", "invalid", "invalid"]
      and [dt.state("core", *x) for x in ((None, None), ("NMBR", "TEXT"), ("NMBR", None), (None, "TEXT"), ("nmbr", "TEXT"))] == ["unset", "set", "invalid", "invalid", "invalid"])

# ---- a fabricated database
tmp = tempfile.mkdtemp(prefix="datatypes_")
c = sqlite3.connect(os.path.join(tmp, "d.db")); c.row_factory = sqlite3.Row; c.execute("PRAGMA foreign_keys = ON")
c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
tf.strip_drafts(c)   # the library now holds 021 to 025: go back to an older database so the "not applied yet" paths can be tested
B = "appTypes001"
for tid, nm in (("tA", "Orders"), ("tB", "Customers")):
    c.execute("insert into airtable_tables(table_id, base_id, table_name) values (?,?,?)", (tid, B, nm))
F = [("f1", "tA", "Orders", "Title", "singleLineText"), ("f2", "tA", "Orders", "Notes", "multilineText"), ("f3", "tA", "Orders", "Status", "singleSelect"),
     ("f4", "tA", "Orders", "Due", "dateTime"), ("f5", "tA", "Orders", "Mystery", "barcode"), ("f6", "tB", "Customers", "Name", "singleLineText"),
     ("f7", "tB", "Customers", "Owner", "singleCollaborator"), ("f8", "tB", "Customers", "Old", "singleLineText")]
for r in F:
    c.execute("insert into airtable_fields(base_id, table_id, table_name, field_id, field_name, data_type, updated_at) values (?,?,?,?,?,?,'2026-01-01 10:00:00.000000')", (B, r[1], r[2], r[0], r[3], r[4]))
c.commit()

# ---- before the migrations: the page can look, nothing can be saved
rv = dts.review(c, B)
check("before the migrations the review says what is missing, and every field is not set", rv["ready"] is False and "data_type_defaults" in rv["missing"] and "field_names.core_display_type" in rv["missing"]
      and rv["counts"]["planning"] == {"set": 0, "unset": 8, "invalid": 0} and rv["defaults"] == [])
e = refused(dts.apply, c, B, "JV", "why", "planning", "defaults")
check("saving is refused plainly until migrations 021 and 022 are applied", e and e.status == 409 and "migrations 021 and 022" in e.message, e and e.message)

tf.apply_migrations(c); tf.apply_migrations(c)
check("the migrations apply, and a second run changes nothing", dts.missing(c) == [] and c.execute("select count(*) from data_type_defaults").fetchone()[0] == 60)
check("the view shows target_type under its clear name", [r[1] for r in c.execute("pragma table_info(field_data_types)")] == ["airtable_field_id", "planning_target_type", "core_target_type", "core_display_type"])
dflt = {(d["system"], d["airtable_type"]): d for d in dts.read_defaults(c)}
check("the default mapping: Planning uses the 17 types, Core keeps dataType and displayType apart", dflt[("planning", "singleSelect")]["type"] == "single-select"
      and (dflt[("core", "multilineText")]["type"], dflt[("core", "multilineText")]["display_type"]) == ("TEXT", "TXTA") and dflt[("core", "singleSelect")]["display_type"] == "SLCT"
      and dflt[("planning", "manualSort")]["type"] is None and dflt[("planning", "autoNumber")]["type"] == "number" and dflt[("core", "formula")]["type"] is None)
check("every seeded default is on its list", all(d["type"] is None or (dt.planning_type(d["type"]) == d["type"] if s == "planning" else dt.core_pair(d["type"], d["display_type"]) == (d["type"], d["display_type"]))
      for (s, _), d in dflt.items()))

rv = dts.review(c, B)
f = {x["field_id"]: x for x in rv["fields"]}
check("the review proposes each field's default for each system, and has none for a type with no row", f["f3"]["planning"]["proposed"] == {"type": "single-select", "display_type": None}
      and f["f3"]["core"]["proposed"] == {"type": "TEXT", "display_type": "SLCT"} and f["f5"]["planning"]["proposed"] is None and rv["ready"] is True)
check("fields are in a fixed order: table, then field", [x["field_id"] for x in rv["fields"]] == ["f6", "f8", "f7", "f4", "f5", "f2", "f3", "f1"], [x["field_id"] for x in rv["fields"]])

# ---- who and why are required
check("initials and a reason are required", refused(dts.apply, c, B, "", "why", "planning", "defaults") and refused(dts.apply, c, B, "JV", "", "planning", "defaults")
      and "reason" in refused(dts.apply, c, B, "JV", "", "planning", "defaults").message and refused(dts.apply, c, B, "JV", "why", "nowhere", "defaults") is not None)

# ---- apply the defaults
r = dts.apply(c, B, "JV", "first pass", "planning", "defaults")
check("applying the Planning defaults sets every field that has one, and skips the rest", r["changed"] == 7 and r["skipped"]["no_default"] == 1 and r["batch"] == "T1", r)
row = c.execute("select target_type from field_names where airtable_field_id='f3'").fetchone()
d = c.execute("select * from field_type_decisions where airtable_field_id='f3' and system='planning'").fetchone()
check("the value is stored where the loader reads it, with who, when, why and how", row[0] == "single-select" and d["source"] == "default" and d["decided_by"] == "JV" and d["reason"] == "first pass" and d["decided_at"])
ev = c.execute("select * from data_type_events where airtable_field_id='f3'").fetchall()
check("the history records the change under the batch", len(ev) == 1 and ev[0]["event_type"] == "set" and ev[0]["old_type"] is None and ev[0]["new_type"] == "single-select" and ev[0]["batch_id"] == "T1")
r = dts.apply(c, B, "JV", "again", "planning", "defaults")
check("a second run changes nothing: fields that already have a type are kept", r["changed"] == 0 and r["skipped"]["already_set"] == 7 and c.execute("select count(*) from data_type_events").fetchone()[0] == 7)
r = dts.apply(c, B, "JV", "core pass", "core", "defaults")
cr = c.execute("select core_target_type, core_display_type from field_names where airtable_field_id='f2'").fetchone()
check("the Core defaults store the dataType and the displayType in separate columns", r["changed"] == 6 and tuple(cr) == ("TEXT", "TXTA") and r["batch"] == "T8", (r, tuple(cr)))

# ---- an override, and all-or-nothing
r = dts.apply(c, B, "AB", "this one is really a long text", "planning", "each", changes=[{"field_id": "f1", "type": "long-text"}])
check("a person can override one field; it is recorded as an override and the history keeps the old value", r["changed"] == 1
      and c.execute("select source, decided_by from field_type_decisions where airtable_field_id='f1' and system='planning'").fetchone()[:] == ("override", "AB")
      and c.execute("select event_type, old_type, new_type from data_type_events where airtable_field_id='f1' order by event_id desc limit 1").fetchone()[:] == ("change", "text", "long-text"))
before = c.execute("select count(*) from data_type_events").fetchone()[0]
e = refused(dts.apply, c, B, "AB", "bad batch", "planning", "each", changes=[{"field_id": "f2", "type": "number"}, {"field_id": "f4", "type": "Single select"}])
check("one bad value refuses the whole batch and nothing is written", e and "not a Workfront Planning data type" in e.message and c.execute("select target_type from field_names where airtable_field_id='f2'").fetchone()[0] == "long-text"
      and c.execute("select count(*) from data_type_events").fetchone()[0] == before, e and e.message)
e = refused(dts.apply, c, B, "AB", "half", "core", "each", changes=[{"field_id": "f1", "type": "TEXT"}])
check("a Core change needs both values", e and "displayType" in e.message, e and e.message)
e = refused(dts.apply, c, B, "AB", "bad", "core", "each", changes=[{"field_id": "f1", "type": "TEXT", "display_type": "BOX"}])
check("a Core displayType off the list is refused", e and "displayType 'BOX' must be one of" in e.message, e and e.message)

# ---- a bulk value over a selection, abandoned fields skipped
c.execute("update field_names set abandoned=1, abandoned_by='JV', abandoned_at='2026-01-01', abandoned_reason='unused' where airtable_field_id='f8'")
c.commit()
r = dts.apply(c, B, "JV", "all customers are text", "planning", "value", selection={"table_ids": ["tB"]}, value={"type": "text"})
check("a bulk value applies to the chosen table's active fields only (the abandoned one is skipped)", r["changed"] == 1 and r["unchanged"] == 1 and c.execute("select target_type from field_names where airtable_field_id='f7'").fetchone()[0] == "text"
      and c.execute("select count(*) from data_type_events where airtable_field_id='f8' and system='planning'").fetchone()[0] == 1, r)
r = dts.apply(c, B, "JV", "dates", "planning", "value", selection={"airtable_types": ["dateTime"]}, value={"type": "date"})
check("a selection by Airtable type works", r["unchanged"] == 1 and r["changed"] == 0)
e = refused(dts.apply, c, B, "JV", "none", "planning", "value", selection={"table_ids": ["tZ"]}, value={"type": "text"})
check("a selection that matches nothing is refused", e and "No fields match" in e.message)

# ---- values off the list are shown as invalid and cleared by a person
c.execute("update field_names set target_type='Single select', core_target_type='NMBR', core_display_type=NULL where airtable_field_id='f3'")
c.commit()
f = {x["field_id"]: x for x in dts.review(c, B)["fields"]}
check("a stored value that is not on the list, and half a Core pair, are invalid", f["f3"]["planning"]["state"] == "invalid" and f["f3"]["core"]["state"] == "invalid" and dts.review(c, B)["counts"]["planning"]["invalid"] == 1)
r = dts.clear_invalid(c, B, "JV", "the API would refuse these")
check("clearing empties both systems for invalid values, and the old values stay in the history", r["cleared"] == {"planning": 1, "core": 1} and c.execute("select target_type, core_target_type, core_display_type from field_names where airtable_field_id='f3'").fetchone()[:] == (None, None, None)
      and c.execute("select event_type, old_type, old_display_type from data_type_events where airtable_field_id='f3' and system='core' order by event_id desc limit 1").fetchone()[:] == ("clear", "NMBR", None)
      and c.execute("select count(*) from field_type_decisions where airtable_field_id='f3'").fetchone()[0] == 0, r)
check("a valid value is never touched by the clear, and a second clear does nothing", dts.clear_invalid(c, B, "JV", "again")["cleared"] == {"planning": 0, "core": 0}
      and c.execute("select target_type from field_names where airtable_field_id='f1'").fetchone()[0] == "long-text")
r = dts.apply(c, B, "JV", "empty it", "planning", "clear", selection={"field_ids": ["f2"]})
check("a person can clear a valid value on purpose", r["changed"] == 1 and c.execute("select target_type from field_names where airtable_field_id='f2'").fetchone()[0] is None)

# ---- editing a default
e = refused(dts.set_default, c, "JV", "planning", "singleLineText", "Single select", None, "x")
check("a default must be on the list too", e and "not a Workfront Planning data type" in e.message)
check("an unknown Airtable type has no default row", refused(dts.set_default, c, "JV", "planning", "noSuchType", "text", None, "x").status == 404)
dts.set_default(c, "JV", "planning", "singleLineText", "long-text", None, "every short text becomes long text")
dts.set_default(c, "JV", "core", "multilineText", "TEXT", "TXTA", "same, recorded")
dts.set_default(c, "JV", "planning", "rating", None, None, "a person must decide ratings")
ev = c.execute("select * from data_type_events where event_type='default_change' order by event_id").fetchall()
check("a default can be changed or emptied, with who and why in the history", len(ev) == 3 and ev[2]["old_type"] == "number" and ev[2]["new_type"] is None and ev[2]["airtable_type"] == "rating" and ev[2]["reason"].startswith("a person"))
check("a changed default changes the proposal, and leaves fields already set alone", {x["field_id"]: x for x in dts.review(c, B)["fields"]}["f6"]["planning"]["proposed"]["type"] == "long-text"
      and c.execute("select target_type from field_names where airtable_field_id='f6'").fetchone()[0] == "text")

# ---- the spreadsheet importer keeps only values on the list
fields = [{"field_id": "f2", "field_name": "Notes", "table_name": "Orders", "data_type": "multilineText"}, {"field_id": "f4", "field_name": "Due", "table_name": "Orders", "data_type": "dateTime"},
          {"field_id": "f5", "field_name": "Mystery", "table_name": "Orders", "data_type": "barcode"}]
imp = fio.parse_import("field_id,old_name,new_name,new_label,old_dataType,new_dataType\r\nf2,Notes,Notes,Notes,x,Long-Text\r\nf4,Due,Due,Due,x,Single select\r\nf5,Mystery,Mystery,Mystery,x,\r\n", fields, [], B)
check("a Planning type in the file is normalised; one off the list is not stored and is reported", imp["target_types"] == {"f2": "long-text"} and [p["kind"] for p in imp["problems"] if p["field_id"] == "f4"].count("NEW_TYPE_NOT_ON_LIST") == 1
      and "not a Workfront Planning data type" in [p["detail"] for p in imp["problems"] if p["kind"] == "NEW_TYPE_NOT_ON_LIST"][0], imp["problems"])
csvt = "field_id,old_name,new_name,new_label,old_dataType,new_dataType\r\nf2,Notes,Notes,Notes,x,long-text\r\nf4,Due,Due,Due,x,Single select\r\n"
st.import_names(c, B, "JV", csv_base64=base64.b64encode(csvt.encode()).decode())
check("a spreadsheet import records who set the type, as an import; a value off the list is never stored", c.execute("select source, decided_by from field_type_decisions where airtable_field_id='f2' and system='planning'").fetchone()[:] == ("import", "JV")
      and c.execute("select target_type from field_names where airtable_field_id='f4'").fetchone()[0] in (None, "date") and c.execute("select count(*) from data_type_events where source='import'").fetchone()[0] == 1)

# ---- the load plan uses each system's own type
st.approve_tables(c, B, "JV", mode="unchanged")
rvw = st.review(c, B)
f = {x["field_id"]: x for x in rvw["fields"]}
check("the names review carries the Planning type and both Core values", f["f3"]["target_type"] is None and "core_display_type" in f["f2"] and f["f2"]["core_target_type"] == "TEXT" and f["f2"]["core_display_type"] == "TXTA", f["f2"])
RV = {"tables": [{"table_id": "tA", "old": "Orders", "scope": "in", "target": "planning_and_core", "approved": True, "effective": "Orders"}], "settings": None}
def tr(n): return {"candidate": n, "status": "approved", "approved_name": n}
def prow(fid, name, tt, cd, cp, typ="singleLineText"):
    return {"field_id": fid, "table_id": "tA", "table_name": "Orders", "original": name, "data_type": typ, "abandoned": False, "consolidated": None, "target_type": tt, "core_target_type": cd, "core_display_type": cp,
            "description": {"text": "d"}, "targets": {"planning_name": tr(name), "core_name": tr(name), "core_label": tr(name + " label")}}
RV["fields"] = [prow("g1", "Both", "long-text", "TEXT", "TXTA", "multilineText"), prow("g2", "Planning only", "text", None, None), prow("g3", "Core only", None, "NMBR", "TEXT", "number"),
                prow("g4", "Off list", "Single select", "NMBR", None), prow("g5", "Pick", "single-select", "TEXT", "SLCT", "singleSelect")]
I = {i["temp_id"]: i for i in lp.build_plan(RV, {})["items"]}
check("the plan sends each system its own type: Planning the one value, Core the dataType and the displayType", (I["P-F-g1"]["data_type"], I["P-F-g1"]["display_type"]) == ("long-text", None)
      and (I["C-F-g1"]["data_type"], I["C-F-g1"]["display_type"]) == ("TEXT", "TXTA") and I["P-F-g1"]["status"] == I["C-F-g1"]["status"] == "ready")
check("a field with a Planning type but no Core type is blocked in Core only, and the reverse", I["P-F-g2"]["status"] == "ready" and I["C-F-g2"]["status"] == "blocked" and "Core data type" in I["C-F-g2"]["reasons"][0]
      and I["P-F-g3"]["status"] == "blocked" and "Planning data type" in I["P-F-g3"]["reasons"][0] and I["C-F-g3"]["status"] == "ready")
check("a stored value that is not on the list is never used and the reason says to clear it", I["P-F-g4"]["status"] == "blocked" and I["P-F-g4"]["data_type"] is None and "not one of Adobe's 17" in I["P-F-g4"]["reasons"][0]
      and I["C-F-g4"]["status"] == "blocked" and "not a valid pair" in I["C-F-g4"]["reasons"][0])
check("select fields are recognised from each system's own type", "options" in (I["P-F-g5"]["spec"] or {}) and "options" in (I["C-F-g5"]["spec"] or {}) and "options" not in (I["P-F-g1"]["spec"] or {}))

print("ALL PASS" if ok else "FAILURES")
sys.exit(0 if ok else 1)
