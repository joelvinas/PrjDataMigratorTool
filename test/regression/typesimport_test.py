"""Tests for the names-and-types importer: the template, header aliases, the Planning and Core types, values off the lists, and conflicts that are reported and
held instead of overwriting a person's decision. Fabricated names only; a throwaway database built through the real functions."""
import base64, csv, io, os, sqlite3, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import datatypes_store as dts, fieldnames_io as fio, fieldnames_store as st, typesfixture as tf
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])

# ---- the template
t = fio.template_csv()
rows = list(csv.reader(io.StringIO(t.lstrip("﻿"))))
check("the template opens in Excel (BOM), has the header and exactly ONE data row", t.startswith("﻿") and len(rows) == 2 and len(rows[0]) == len(rows[1]) == 9, rows)
check("the example row is obviously dummy and uses values on the lists", rows[1][0].startswith("fldEXAMPLE") and "DELETE THIS ROW" in rows[1][1] and rows[1][6:] == ["single-select", "TEXT", "SLCT"])
imp = fio.parse_import(t, [{"field_id": "f1", "field_name": "A", "table_name": "T", "data_type": "singleLineText"}], [], "appX")
check("the example row is skipped with a note, not reported as an unknown field", imp["rows"] == 0 and "TEMPLATE_EXAMPLE_ROW" in [p["kind"] for p in imp["problems"]] and not any(p["kind"] in ("UNMATCHED_ID", "BLANK_FIELD_ID") for p in imp["problems"]), imp["problems"])

# ---- a fabricated database
tmp = tempfile.mkdtemp(prefix="typesimport_")
c = sqlite3.connect(os.path.join(tmp, "i.db")); c.row_factory = sqlite3.Row; c.execute("PRAGMA foreign_keys = ON")
c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
tf.strip_drafts(c)   # the library now holds 021 to 025: go back to an older database so the "not applied yet" paths can be tested
B = "appImport001"
c.execute("insert into airtable_tables(table_id, base_id, table_name) values ('tA', ?, 'Orders')", (B,))
F = [("f1", "Title", "singleLineText"), ("f2", "Notes", "multilineText"), ("f3", "Status", "singleSelect"), ("f4", "Due", "date"), ("f5", "Amount", "number"), ("f6", "Body", "richText")]
for fid, nm, ty in F:
    c.execute("insert into airtable_fields(base_id, table_id, table_name, field_id, field_name, data_type, updated_at) values (?,?,?,?,?,?,'2026-01-01 10:00:00.000000')", (B, "tA", "Orders", fid, nm, ty))
c.commit()
st.approve_tables(c, B, "JV", mode="unchanged")
HEAD = "field_id,old_name,Planning Name,core_name,Core Label,Planning_Data_Type,core_data_type,core_display_type\r\n"
def run(body, head=HEAD):
    return st.import_names(c, B, "JV", csv_base64=base64.b64encode((head + body).encode()).decode())
def stored(fid):
    r = c.execute("select * from field_names where airtable_field_id=?", (fid,)).fetchone()
    r = dict(r) if r else {}
    return tuple(r.get(k) for k in ("planning_name", "core_name", "core_label", "target_type", "core_target_type", "core_display_type"))

# ---- before migration 022: names and Planning types still import, Core types are skipped with a plain note
r = run("f1,Title,Order Title,Order Title Core,Order Title Label,text,TEXT,TXTA\r\n")
check("without migration 022 the names and the Planning type are stored and the Core type is skipped, with a note", r["names_imported"] == 3 and stored("f1")[:4] == ("Order Title", "Order Title Core", "Order Title Label", "text")
      and stored("f1")[4:] == (None, None) and any(p["kind"] == "CORE_NEEDS_MIGRATION" for p in r["problems"]), (r["names_imported"], stored("f1"), r["problems"]))
tf.apply_migrations(c)

# ---- headers (any case, spaces, underscores), per-target names, and both Core values
r = run("f2,Notes,Notes Plan,Notes Core,Notes Label,Long Text,TEXT,TXTA\r\n")
check("Planning type 'Long Text' is not a Planning type (it needs a hyphen) and is reported, not stored", stored("f2")[3] is None and any(p["kind"] == "NEW_TYPE_NOT_ON_LIST" for p in r["problems"]) and stored("f2")[:3] == ("Notes Plan", "Notes Core", "Notes Label"), (stored("f2"), r["problems"]))
check("the Core dataType and displayType are stored in separate columns", stored("f2")[4:] == ("TEXT", "TXTA"), stored("f2"))
check("the import records who set each type, as an import", [tuple(x) for x in c.execute("select system, source, decided_by from field_type_decisions where airtable_field_id='f2'")] == [("core", "import", "JV")])
r = run("f3,Status,Status,Status,Status,single-select,TEXT,SLCT\r\n")
check("both systems' types in one row are stored and recorded", stored("f3")[3:] == ("single-select", "TEXT", "SLCT") and r["types_imported"] == 2
      and sorted(tuple(x) for x in c.execute("select system from field_type_decisions where airtable_field_id='f3'")) == [("core",), ("planning",)])

# ---- values off the lists and half a Core pair are reported and never stored
r = run("f4,Due,Due,Due,Due,Single select,TEXT,\r\nf5,Amount,Amount,Amount,Amount,number,STRING,TEXT\r\n")
kinds = sorted(p["kind"] for p in r["problems"] if p["kind"] != "OLD_TYPE_MISMATCH")
check("an incomplete Core pair, a Core value off its list and a Planning value off its list are reported and none is stored", "CORE_PAIR_INCOMPLETE" in kinds and "CORE_TYPE_NOT_ON_LIST" in kinds and "NEW_TYPE_NOT_ON_LIST" in kinds
      and stored("f4")[3:] == (None, None, None) and stored("f5")[3:] == ("number", None, None), (kinds, stored("f4"), stored("f5")))
r = run("f6,Body,Body,Body,Body,long-text,RICH,RICH\r\n")
check("RICH as a displayType is not on the list", stored("f6")[4:] == (None, None) and any(p["kind"] == "CORE_TYPE_NOT_ON_LIST" for p in r["problems"]))
r = run("f6,Body,Body,Body,Body,long-text,RICH,TXTA\r\n")
check("a RICH dataType is accepted and stored, with a plain notice to create the field by hand", stored("f6")[4:] == ("RICH", "TXTA") and any(p["kind"] == "CORE_RICH_BY_HAND" and "Custom Form" in p["detail"] for p in r["problems"]))

# ---- conflicts are reported and held, never overwritten
st.approve_fields(c, B, "JV", mode="pass_and_auto")
before = stored("f3")
r = run("f3,Status,Other Plan,Status,Status,boolean,NMBR,TEXT\r\n")
what = sorted((h["what"], h["target"]) for h in r["held"])
check("a file that disagrees with an approved name or a type a person set is reported and held", what == [("name", "planning_name"), ("type", "core"), ("type", "planning")] and r["held_count"] == 3 and stored("f3") == before, (what, stored("f3")))
h = {(x["what"], x["target"]): x for x in r["held"]}
check("each held item shows the field, the current value and the file's value", h[("name", "planning_name")]["current"] == "Status" and h[("name", "planning_name")]["file"] == "Other Plan"
      and h[("type", "core")]["current"] == "TEXT / SLCT" and h[("type", "core")]["file"] == "NMBR / TEXT" and h[("type", "planning")]["field_name"] == "Status")
r = run("f3,Status,Status,Status,Status,single-select,TEXT,SLCT\r\n")
check("a file that agrees with what is stored holds nothing and changes nothing", r["held_count"] == 0 and r["types_imported"] == 0 and r["unchanged"] >= 3 and stored("f3") == before)
n = c.execute("select count(*) from data_type_events").fetchone()[0]
run("f3,Status,Other Plan,Status,Status,boolean,NMBR,TEXT\r\n")
check("holding writes no history", c.execute("select count(*) from data_type_events").fetchone()[0] == n)
dts.apply(c, B, "JV", "person clears it", "planning", "clear", selection={"field_ids": ["f3"]})
r = run("f3,Status,Status,Status,Status,boolean,TEXT,SLCT\r\n")
check("once a person clears a type, the file can set it", stored("f3")[3] == "boolean" and r["held_count"] == 0)

# ---- the older file format still works
r = run("f5,Amount,Amount,Amount,Amount,x\r\n", head="field_id,old_name,new_name,new_label,old_dataType,new_dataType\r\n")
check("the older format (new_name, new_label, new_dataType) is still read", r["rows"] == 1 and not any(p["kind"] == "MISSING_COLUMN" for p in r["problems"]), r["problems"])

# ---- the default mapping carries the rule text and the four added types
dflt = {(d["system"], d["airtable_type"]): d for d in dts.read_defaults(c)}
check("every row the reference covers has its rule text, shown in plain words", dflt[("core", "singleSelect")]["rule_text"] and dflt[("planning", "email")]["rule_text"].startswith("No dedicated email type"))
check("aiText, manualSort, multipleLookupValues and rollup are added, and manualSort has no Planning default", all((s, a) in dflt for s in ("planning", "core") for a in ("aiText", "manualSort", "multipleLookupValues", "rollup"))
      and dflt[("planning", "manualSort")]["type"] is None and dflt[("planning", "rollup")]["type"] == "formula" and dflt[("planning", "multipleLookupValues")]["type"] == "multi-select")
check("multilineText stays TEXT / TXTA in Core, with the notice about creating RICH by hand", (dflt[("core", "multilineText")]["type"], dflt[("core", "multilineText")]["display_type"]) == ("TEXT", "TXTA")
      and "Custom Form interface" in dflt[("core", "multilineText")]["note"])
dts.set_default(c, "JV", "planning", "email", "long-text", None, "a person's edit")
c.executescript(open(os.path.join(ROOT, "sql", "migration_023_add_rule_text_and_new_types.sql"), encoding="utf-8").read().split("ALTER TABLE data_type_defaults ADD COLUMN rule_text TEXT;")[1])
check("re-running the seed part of migration 023 never overwrites a default a person edited", {(d["system"], d["airtable_type"]): d for d in dts.read_defaults(c)}[("planning", "email")]["type"] == "long-text")

print("ALL PASS" if ok else "FAILURES")
sys.exit(0 if ok else 1)
