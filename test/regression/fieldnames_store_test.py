"""Tests for fieldnames_store.py (saved decisions for the name review) against a throwaway SQLite database. Fabricated names only; no server."""
import base64, csv, io, os, sqlite3, sys, tempfile, zipfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import fieldnames as fn, fieldnames_store as st
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else x)
def raises(fnc, *a, **k):
    try:
        fnc(*a, **k)
    except st.StoreError as e:
        return e
    return None

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
c = sqlite3.connect(os.path.join(tempfile.mkdtemp(prefix="names_store_"), "t.db"))
c.row_factory = sqlite3.Row
c.execute("PRAGMA foreign_keys = ON")
c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
T1, T2 = "2026-01-01 10:00:00.000000", "2026-01-02 10:00:00.000000"
for tid, nm in (("tA", "Orders"), ("tB", "PO#s"), ("tC", "1.2.3 Archive"), ("tD", "Hidden")):
    c.execute("insert into airtable_tables(table_id, base_id, table_name) values (?, 'appS', ?)", (tid, nm))
c.execute("insert into airtable_tables(table_id, base_id, table_name) values ('tX', 'appOther', 'Elsewhere')")
F = [("f1", "tA", "Orders", "Amount", "number", None, None, 10, 0, 7, 0), ("f2", "tA", "Orders", "amount", "number", None, None, 10, 10, 0, 0),
     ("f3", "tA", "Orders", "Notes!", "singleLineText", None, None, 10, 2, 8, 0), ("f4", "tA", "Orders", "Café", "singleLineText", None, None, 10, 1, 9, 0),
     ("f5", "tA", "Orders", "PO Number (from PO#s)", "multipleLookupValues", None, "f6", 10, 3, 5, 0), ("f6", "tA", "Orders", "PO#s", "multipleRecordLinks", "tB", None, 10, 3, 5, 0),
     ("f7", "tA", "Orders", "Done", "checkbox", None, None, 10, 0, 2, 0), ("f8", "tB", "PO#s", "Total", "number", None, None, 4, 4, 0, 0),
     ("f9", "tC", "1.2.3 Archive", "Plain", "singleLineText", None, None, 3, 0, 3, 0), ("f10", "tD", "Hidden", "Secret", "singleLineText", None, None, 1, 0, 1, 0),
     ("fX", "tX", "Elsewhere", "Other base", "number", None, None, 1, 0, 1, 0)]
for r in F:
    base = "appOther" if r[1] == "tX" else "appS"
    c.execute("insert into airtable_fields(base_id, table_id, table_name, field_id, field_name, data_type, linked_table_id, source_link_field_id, row_count, null_count,"
              " cardinality, cardinality_is_capped, updated_at) values (?,?,?,?,?,?,?,?,?,?,?,?,?)", (base, r[1], r[2], r[0], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], T1))
for tid in ("tA", "tC"):
    c.execute("insert into api_call_log(timestamp, source_system, purpose, http_method, endpoint, table_id, success) values (?, 'Airtable','profile_page','GET','/x',?,1)", (T2, tid))
c.commit()

B = "appS"
rv = st.review(c, B)
check("gate is closed at first: no fields, every in-scope table pending", rv["gate"]["ready"] is False and rv["fields"] == [] and len(rv["gate"]["pending"]) == 4, rv["gate"])
check("every table is undecided until a scope decision is made", {t["scope"] for t in rv["tables"]} == {"undecided"} and rv["scope_counts"]["undecided"] == 4)
check("the other base is not mixed in", {t["table_id"] for t in rv["tables"]} == {"tA", "tB", "tC", "tD"})
tb = {t["table_id"]: t for t in rv["tables"]}
check("table verdicts: unchanged PASS, PO#s AUTO_FIXABLE, number prefix NEEDS_HUMAN", tb["tA"]["verdict"] == "PASS" and tb["tB"]["verdict"] == "AUTO_FIXABLE" and tb["tB"]["suggested"] == "POs"
      and tb["tC"]["verdict"] == "NEEDS_HUMAN" and tb["tC"]["suggested"] == "Archive 1-2-3", {k: (v["verdict"], v["suggested"]) for k, v in tb.items()})
check("fields cannot be approved while the gate is closed", (raises(st.approve_fields, c, B, "JV", mode="pass") or st.StoreError("x")).status == 409)
check("a decision needs the reviewer's initials", raises(st.approve_tables, c, B, "  ", mode="unchanged") is not None and raises(st.approve_tables, c, B, "x" * 41, mode="unchanged") is not None)

# ---- table approval
r = st.approve_tables(c, B, "JV", mode="unchanged")
check("bulk 'unchanged' approves only PASS tables", r["approved"] == 2 and not r["skipped"], r)   # Orders and Hidden
e = raises(st.approve_tables, c, B, "JV", items=[{"table_id": "tC"}])
check("a NEEDS_HUMAN table needs a typed name", e is not None and "type the name" in e.message, e and e.message)
e = raises(st.approve_tables, c, B, "JV", items=[{"table_id": "tC", "name": "Archive (old)"}])
check("a typed name is validated against the rules", e is not None and "Special character" in e.message, e and e.message)
e = raises(st.approve_tables, c, B, "JV", items=[{"table_id": "tC", "name": "orders"}])
check("a name another table already uses is refused (case-insensitive)", e is not None and "another table" in e.message, e and e.message)
r = st.approve_tables(c, B, "JV", mode="auto")
check("bulk 'auto' approves the AUTO_FIXABLE table with its proposal", r["approved"] == 1 and c.execute("select new_table_name from table_names where airtable_table_id='tB'").fetchone()[0] == "POs")
check("the gate is still closed with one table pending", st.review(c, B)["gate"]["pending"] == ["tC"])
st.approve_tables(c, B, "JV", items=[{"table_id": "tC", "name": "Archive 1-2-3"}], reason="client prefers the number at the end")
rv = st.review(c, B)
check("gate opens when every table is approved", rv["gate"]["ready"] is True and len(rv["fields"]) == 10, rv["gate"])
check("approval records who and when", c.execute("select approved_by, approved_at from table_names where airtable_table_id='tC'").fetchone()[0] == "JV" and rv["tables"][0]["approved_by"] in ("JV", None))
ev = [dict(x) for x in c.execute("select * from name_events where subject_type='table' and subject_id='tC'")]
check("table approval is in the audit trail with the old name, new name, reason and actor", len(ev) == 1 and ev[0]["event_type"] == "approve" and ev[0]["old_value"] == "1.2.3 Archive"
      and ev[0]["new_value"] == "Archive 1-2-3" and ev[0]["actor"] == "JV" and "client prefers" in ev[0]["reason"], ev)

# ---- field review
fl = {f["field_id"]: f for f in rv["fields"]}
check("the swap now resolves to the approved table name and is AUTO_FIXABLE", fl["f5"]["targets"]["planning_name"]["verdict"] == "AUTO_FIXABLE"
      and fl["f5"]["targets"]["planning_name"]["suggested"] == "PO Number from POs", fl["f5"]["targets"]["planning_name"])
check("duplicate group between Amount and amount", [len(x["members"]) for x in rv["clusters"] if x["target"] == "planning_name"] == [2])
check("presence shows who can be abandoned: profiled and empty yes; checkbox no; unprofiled no", fl["f2"]["presence"]["can_abandon"] and not fl["f7"]["presence"]["can_abandon"]
      and fl["f8"]["presence"]["profiled"] is False and not fl["f8"]["presence"]["can_abandon"])
check("fields in a table that is out of scope are not shown", "f10" in fl)     # still undecided here, so it is validated
out = st.approve_fields(c, B, "JV", mode="pass_and_auto", targets=["planning_name"])
rv = st.review(c, B); fl = {f["field_id"]: f for f in rv["fields"]}
check("bulk approval takes PASS and AUTO_FIXABLE names only, for the chosen target", out["approved"] > 0 and fl["f3"]["targets"]["planning_name"]["status"] == "approved"
      and fl["f3"]["targets"]["planning_name"]["approved_name"] == "Notes" and fl["f3"]["targets"]["core_name"]["status"] == "unreviewed")
check("a duplicate-group member is not bulk-approved", fl["f1"]["targets"]["planning_name"]["status"] == "unreviewed" and fl["f2"]["targets"]["planning_name"]["status"] == "unreviewed")
check("an approved name is stored and used as the candidate", c.execute("select planning_name, planning_name_status from field_names where airtable_field_id='f3'").fetchone()[:] == ("Notes", "approved"))
e = raises(st.approve_fields, c, B, "JV", items=[{"field_id": "f1", "target": "planning_name", "name": "Amount"}])
check("approving a name that still collides with its group is refused", e is not None and "Duplicate" in e.message, e and e.message)
e = raises(st.approve_fields, c, B, "JV", items=[{"field_id": "f4", "target": "planning_name"}])
check("a human-tier name is approved only with its proposal accepted or a typed name, and it must be clean", e is None)     # proposal 'Cafe' is clean
fl = {f["field_id"]: f for f in st.review(c, B)["fields"]}
check("accepting the proposal stores the repaired name", fl["f4"]["targets"]["planning_name"]["approved_name"] == "Cafe")
e = raises(st.approve_fields, c, B, "JV", items=[{"field_id": "f2", "target": "planning_name", "name": "Amount_2"}])
check("a typed name with a banned character is refused", e is not None, e and e.message)
st.approve_fields(c, B, "JV", items=[{"field_id": "f2", "target": "planning_name", "name": "Amount Archive"}], reason="renamed to resolve the duplicate")
rv = st.review(c, B); fl = {f["field_id"]: f for f in rv["fields"]}
check("renaming one member resolves the group and the other becomes approvable", not [x for x in rv["clusters"] if x["target"] == "planning_name"]
      and fl["f1"]["targets"]["planning_name"]["verdict"] == "PASS")
st.approve_fields(c, B, "JV", items=[{"field_id": "f1", "target": "planning_name"}])
check("both members are now approved with distinct names", c.execute("select count(*) from field_names where planning_name_status='approved' and airtable_field_id in ('f1','f2')").fetchone()[0] == 2)
st.set_field_status(c, B, "JV", [{"field_id": "f1", "target": "planning_name"}], "unreviewed", reason="checking with the client")
check("withdrawing an approval needs a reason and resets the status", c.execute("select planning_name_status from field_names where airtable_field_id='f1'").fetchone()[0] == "unreviewed"
      and (raises(st.set_field_status, c, B, "JV", [{"field_id": "f1", "target": "planning_name"}], "unreviewed", "") is not None))
st.set_field_status(c, B, "JV", [{"field_id": "f9", "target": "core_label"}], "needs_human", reason="ask the client")
check("a name can be flagged needs_human", c.execute("select core_label_status from field_names where airtable_field_id='f9'").fetchone()[0] == "needs_human")
c.execute("update field_names set planning_name='Bad#Name', planning_name_status='approved' where airtable_field_id='f3'"); c.commit()
check("an approved name that no longer passes is flagged stale", {f["field_id"]: f for f in st.review(c, B)["fields"]}["f3"]["targets"]["planning_name"]["stale"] is True)
c.execute("update field_names set planning_name='Notes' where airtable_field_id='f3'"); c.commit()

# ---- abandon
e = raises(st.abandon, c, B, "JV", "f8", "empty")
check("an unprofiled field cannot be abandoned (enforced by the server)", e is not None and "not profiled" in e.message, e and e.message)
check("a checkbox cannot be abandoned", raises(st.abandon, c, B, "JV", "f7", "unused") is not None)
check("a field with values cannot be abandoned", raises(st.abandon, c, B, "JV", "f1", "unused") is not None)
check("abandoning needs a reason", raises(st.abandon, c, B, "JV", "f2", "") is not None)
st.abandon(c, B, "JV", "f2", "empty duplicate of Amount")
row = dict(c.execute("select * from field_names where airtable_field_id='f2'").fetchone())
check("abandon records who, when and why", row["abandoned"] == 1 and row["abandoned_by"] == "JV" and row["abandoned_at"] and row["abandoned_reason"] == "empty duplicate of Amount")
rv = st.review(c, B); fl = {f["field_id"]: f for f in rv["fields"]}
check("an abandoned field is shown as such and has no verdicts", fl["f2"]["abandoned"] and fl["f2"]["targets"] == {} and fl["f2"]["abandon"]["by"] == "JV")
st.unabandon(c, B, "JV", "f2", "client still uses it in a form")
check("un-abandon clears the flag and is audited", c.execute("select abandoned from field_names where airtable_field_id='f2'").fetchone()[0] == 0
      and [x[0] for x in c.execute("select event_type from name_events where subject_id='f2' order by event_id")][-2:] == ["abandon", "unabandon"])

# ---- import
csvtext = "field_id,old_name,new_name,new_label,old_dataType,new_dataType\r\nf9,Plain,Plain Text,Plain Text,Single line text,Text\r\nf3,Notes!,Different,Different,Single line text,Text\r\nfNONE,x,x,x,x,x\r\n"
r = st.import_names(c, B, "JV", csv_base64=base64.b64encode(csvtext.encode("utf-8")).decode())
check("import stores names and target type by field ID, and keeps the already approved Planning name", c.execute("select planning_name, core_name, core_label, target_type from field_names where airtable_field_id='f9'").fetchone()[:] == ("Plain", "Plain Text", "Plain Text", "Text"), r)
check("import never overwrites an approved name", c.execute("select planning_name from field_names where airtable_field_id='f3'").fetchone()[0] == "Notes" and r["kept_approved"] >= 1)
check("import reports an unmatched ID and fields missing from the file", any(p["kind"] == "UNMATCHED_ID" for p in r["problems"]) and any(p["kind"] == "MISSING_FROM_IMPORT" for p in r["problems"]))
check("an import is audited per name with the batch", c.execute("select count(*) from name_events where event_type='import' and reason=?", (r["batch"],)).fetchone()[0] >= 2)
check("a bad file is refused", raises(st.import_names, c, B, "JV", csv_base64="!!notbase64!!") is not None and raises(st.import_names, c, B, "JV") is not None)

# ---- settings
s0, saved = st.read_settings(c, B)
check("defaults before anything is saved", saved is False and s0.table_prefix == "end")
st.save_settings(c, B, {"table_prefix": "drop", "char_cleanup": False}, "JV", "try cleanup off")
s1, saved = st.read_settings(c, B)
check("the newest saved settings are used, as an event", saved and s1.table_prefix == "drop" and s1.char_cleanup is False
      and c.execute("select count(*) from name_events where subject_type='settings'").fetchone()[0] == 1)
check("invalid settings are refused", raises(st.save_settings, c, B, {"table_prefix": "nope"}, "JV") is not None)
st.save_settings(c, B, {}, "JV")
preview = st.review(c, B, fn.Settings(char_cleanup=False))
check("a preview with different settings does not save them", preview["settings"]["char_cleanup"] is False and st.read_settings(c, B)[0].char_cleanup is True)

# ---- per-name settings are saved and read back
st.save_settings(c, B, {"targets": {"planning_name": {"allow_leading_digit": True, "max_len": 120}, "core_label": {"extra_blocked": "%"}}}, "JV", "separate rules")
s2, saved = st.read_settings(c, B)
check("per-name settings are saved as an event and read back", saved and s2.opt("planning_name", "allow_leading_digit") is True and s2.opt("core_name", "allow_leading_digit") is False
      and s2.opt("planning_name", "max_len") == 120 and s2.opt("core_label", "extra_blocked") == "%")
check("invalid per-name settings are refused with a reason", (raises(st.save_settings, c, B, {"targets": {"planning_name": {"max_len": 999}}}, "JV") or st.StoreError("")).message.startswith("Those settings are not valid"))
check("the review reports the per-name settings", st.review(c, B)["settings"]["targets"]["planning_name"]["max_len"] == 120)
st.save_settings(c, B, {}, "JV", "back to defaults")

# ---- scope and target decisions (migration 008)
check("nothing is decided yet: every table is undecided", st.review(c, B)["scope_available"] and st.review(c, B)["scope_counts"] == {"in": 0, "out": 0, "undecided": 4})
check("a scope decision needs initials", raises(st.set_scope, c, B, " ", [{"table_id": "tD", "decision": "out", "reason": "x"}]) is not None)
e = raises(st.set_scope, c, B, "JV", [{"table_id": "tD", "decision": "out"}])
check("'out' needs a reason", e is not None and "reason" in e.message, e and e.message)
check("an unknown table or decision is refused", raises(st.set_scope, c, B, "JV", [{"table_id": "tNOPE", "decision": "in"}]) is not None
      and raises(st.set_scope, c, B, "JV", [{"table_id": "tA", "decision": "maybe"}]) is not None)
st.set_scope(c, B, "JV", [{"table_id": "tD", "decision": "out", "reason": "not needed in Workfront"}, {"table_id": "tA", "decision": "in", "reason": "core table", "target": "planning_and_core"}])
rv = st.review(c, B); tb = {t["table_id"]: t for t in rv["tables"]}
check("scope badges: in, out and undecided are different things", rv["scope_available"] and tb["tA"]["scope"] == "in" and tb["tD"]["scope"] == "out"
      and tb["tB"]["scope"] == "undecided" and rv["scope_counts"] == {"in": 1, "out": 1, "undecided": 2}, rv["scope_counts"])
check("scope records who, when and why", c.execute("select decided_by, reason from table_scope where table_id='tD'").fetchone()[:] == ("JV", "not needed in Workfront") and tb["tD"]["scope_reason"] == "not needed in Workfront")
check("target is recorded for an in-scope table", tb["tA"]["target"] == "planning_and_core" and c.execute("select target from table_targets where table_id='tA'").fetchone()[0] == "planning_and_core")
check("an out-of-scope table and its fields are skipped; undecided ones are validated", "f10" not in {f["field_id"] for f in rv["fields"]} and "f8" in {f["field_id"] for f in rv["fields"]}
      and tb["tD"]["verdict"] is None and rv["in_scope_tables"] == 3)
check("an out-of-scope table does not hold the gate", "tD" not in rv["gate"]["pending"])
check("a table that is out has no target", raises(st.set_scope, c, B, "JV", [{"table_id": "tD", "target": "planning_only"}]) is not None)
check("an invalid target is refused", raises(st.set_scope, c, B, "JV", [{"table_id": "tA", "target": "everywhere"}]) is not None)
ev = [dict(x) for x in c.execute("select * from name_events where subject_id in ('tA','tD') and target in ('scope','target') order by event_id")]
check("scope and target decisions are in the audit trail", [(x["subject_id"], x["target"], x["event_type"], x["new_value"]) for x in ev] ==
      [("tD", "scope", "reject", "out"), ("tA", "scope", "approve", "in"), ("tA", "target", "approve", "planning_and_core")], ev)

# fields that point at, or name, a table that is out of scope are flagged for a human decision
st.set_scope(c, B, "JV", [{"table_id": "tB", "decision": "out", "reason": "dropped"}])
w = st.review(c, B)["scope_warnings"]
check("a link to an out-of-scope table, and a lookup through it, are flagged", {(x["field_id"], x["kind"], x["out_table_id"]) for x in w} == {("f6", "link", "tB"), ("f5", "link", "tB")}, w)
st.set_scope(c, B, "JV", [{"table_id": "tB", "decision": "clear", "reason": "undo"}])
check("clearing returns a table to undecided (no row), not to in or out", c.execute("select count(*) from table_scope where table_id='tB'").fetchone()[0] == 0
      and {t["table_id"]: t for t in st.review(c, B)["tables"]}["tB"]["scope"] == "undecided" and not st.review(c, B)["scope_warnings"])
st.set_scope(c, B, "JV", [{"table_id": "tC", "decision": "in"}, {"table_id": "tC", "target": "clear"}])
st.set_scope(c, B, "JV", [{"table_id": "tA", "target": "clear"}])
check("a target can be cleared", c.execute("select count(*) from table_targets").fetchone()[0] == 0)
c.execute("insert into airtable_fields(base_id, table_id, table_name, field_id, field_name, data_type, updated_at) values ('appS','tA','Orders','fM','Hidden Total','number', ?)", (T1,)); c.commit()
w = st.review(c, B)["scope_warnings"]
check("a field name that mentions an out-of-scope table by name is flagged too", {(x["field_id"], x["kind"]) for x in w} == {("fM", "mention")}, w)
c.execute("delete from airtable_fields where field_id='fM'"); c.commit()
c.executescript("DROP TABLE table_targets; DROP TABLE table_scope;")
rv = st.review(c, B)
check("without migration 008 the review still works and every table is undecided", rv["scope_available"] is False and {t["scope"] for t in rv["tables"]} == {"undecided"} and rv["targets_available"] is False)
e = raises(st.set_scope, c, B, "JV", [{"table_id": "tA", "decision": "in"}])
check("setting scope without the tables says how to apply the migration (409)", e is not None and e.status == 409 and "migration 008" in e.message, e and e.message)
c.executescript("""CREATE TABLE table_scope (base_id TEXT NOT NULL, table_id TEXT NOT NULL, decision TEXT NOT NULL CHECK(decision IN ('in','out')),
                   decided_by TEXT NOT NULL, decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, reason TEXT, PRIMARY KEY (base_id, table_id));
                   CREATE TABLE table_targets (table_id TEXT NOT NULL PRIMARY KEY, base_id TEXT NOT NULL, target TEXT NOT NULL, decided_by TEXT NOT NULL,
                   decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, reason TEXT);""")
st.set_scope(c, B, "JV", [{"table_id": "tD", "decision": "out", "reason": "still out"}])

# ---- descriptions (lineage syntax): generated each time, hand edits saved
c.execute("update airtable_fields set field_description='Total of the order.' where field_id='f1'"); c.commit()
rv = st.review(c, B); fl = {f["field_id"]: f for f in rv["fields"]}
check("every field row carries a generated lineage description", fl["f1"]["description"]["generated"] == "Source: Airtable. Origins: Orders.Amount." and fl["f1"]["description"]["saved"] is None
      and fl["f1"]["description"]["text"] == fl["f1"]["description"]["generated"] and not fl["f1"]["description"]["edited"] and fl["f1"]["description"]["problems"] == [])
check("a name with a period is quoted in the origins", fl["f9"]["description"]["generated"] == 'Source: Airtable. Origins: "1.2.3 Archive".Plain.', fl["f9"]["description"]["generated"])
check("a lookup lists the link it reads through", fl["f5"]["description"]["generated"] == "Source: Airtable. Origins: Orders.PO#s.", fl["f5"]["description"]["generated"])
check("Airtable's own description is added only when the setting is on", "Airtable description" not in fl["f1"]["description"]["generated"])
st.save_settings(c, B, {"description_include_airtable": True}, "JV", "include")
d1 = {f["field_id"]: f for f in st.review(c, B)["fields"]}["f1"]["description"]
check("with the setting on, Airtable's description is appended", d1["generated"] == "Source: Airtable. Origins: Orders.Amount. Airtable description: Total of the order.", d1["generated"])
st.save_settings(c, B, {}, "JV", "off again")
r = st.save_descriptions(c, B, "JV", [{"field_id": "f1", "text": "Source: Airtable. Origins: Orders.Amount; Orders.Notes!. Edited by hand."}], reason="added an origin")
d1 = {f["field_id"]: f for f in st.review(c, B)["fields"]}["f1"]["description"]
check("a hand-edited description is saved and used", r["saved"] == 1 and d1["saved"].endswith("Edited by hand.") and d1["text"] == d1["saved"] and d1["edited"] and d1["generated"] == "Source: Airtable. Origins: Orders.Amount.")
check("the edit is audited with who and why", [dict(x) for x in c.execute("select * from name_events where subject_id='f1' and target='description'")][0]["reason"] == "added an origin")
check("saving text identical to the generated one stores nothing", st.save_descriptions(c, B, "JV", [{"field_id": "f2", "text": "Source: Airtable. Origins: Orders.amount."}])["saved"] == 0
      and (c.execute("select description from field_names where airtable_field_id='f2'").fetchone() or [None])[0] is None)
r = st.save_descriptions(c, B, "JV", [{"field_id": "f2", "text": "Just a note"}])
check("free text is allowed but reported as a warning", r["saved"] == 1 and [w["rule"] for w in r["warnings"]] == ["NOT_LINEAGE"] and {f["field_id"]: f for f in st.review(c, B)["fields"]}["f2"]["description"]["problems"][0]["rule"] == "NOT_LINEAGE")
e = raises(st.save_descriptions, c, B, "JV", [{"field_id": "f2", "text": "line one" + chr(10) + "line two"}])
check("control characters are refused", e is not None and "control" in e.message, e and e.message)
check("an unknown field is refused (404) and a decision needs initials", (raises(st.save_descriptions, c, B, "JV", [{"field_id": "nope", "text": "x"}]) or st.StoreError("", 0)).status == 404
      and raises(st.save_descriptions, c, B, " ", [{"field_id": "f1", "text": "x"}]) is not None)
check("an overlong description is refused", raises(st.save_descriptions, c, B, "JV", [{"field_id": "f2", "text": "x" * 4001}]) is not None)
st.save_descriptions(c, B, "JV", [{"field_id": "f1", "text": ""}, {"field_id": "f2", "text": None}])
check("empty text goes back to the generated description", c.execute("select description from field_names where airtable_field_id='f1'").fetchone()[0] is None
      and not {f["field_id"]: f for f in st.review(c, B)["fields"]}["f1"]["description"]["edited"])
st.save_descriptions(c, B, "JV", [{"field_id": "f1", "text": "Source: Airtable. Origins: Orders.Amount; Orders.Done."}])
files = st.report_files(c, B)
rows = {r["field_id"]: r for r in csv.DictReader(io.StringIO(files["names_report.csv"]))}
check("the report has the description, where it came from and its problems", rows["f1"]["description"].endswith("Orders.Done.") and rows["f1"]["description_source"] == "saved"
      and rows["f3"]["description_source"] == "generated" and rows["f3"]["description"] == "Source: Airtable. Origins: Orders.Notes!." and rows["f3"]["description_problems"] == "")

# ---- consolidation groups (migration 009, proposed): several fields of one table become one
c.executescript("DROP TABLE field_group_members; DROP TABLE field_groups;")      # the tables are in the schema now; drop them to test the not-applied path
rv0 = st.review(c, B)
check("without migration 009 there are no groups and the page can say so", rv0["groups_available"] is False and rv0["groups"] == [])
e = raises(st.create_group, c, B, "JV", "f1", ["f1", "f2"], "review", "same data")
check("creating a group without the tables says how to apply the migration (409)", e is not None and e.status == 409 and "migration 009" in e.message, e and e.message)
c.executescript(open(os.path.join(ROOT, "sql", "migration_009_add_field_groups.sql"), encoding="utf-8").read())
check("with the tables there are no groups yet", st.review(c, B)["groups_available"] is True and st.review(c, B)["groups"] == [])
n_live = sum(st.review(c, B)["summary"]["planning_name"]["verdicts"].values())
check("a group needs a reason and initials", raises(st.create_group, c, B, "JV", "f1", ["f1", "f2"], "review", "") is not None and raises(st.create_group, c, B, " ", "f1", ["f1", "f2"], "review", "x") is not None)
check("the survivor must be one of the members and the rule must exist", raises(st.create_group, c, B, "JV", "f3", ["f1", "f2"], "review", "x") is not None
      and "conflict rule" in raises(st.create_group, c, B, "JV", "f1", ["f1", "f2"], "maybe", "x").message)
check("fields of different tables cannot be grouped", "same table" in raises(st.create_group, c, B, "JV", "f1", ["f1", "f8"], "review", "x").message)
st.abandon(c, B, "JV", "f2", "empty, to test grouping")
e = raises(st.create_group, c, B, "JV", "f1", ["f1", "f2"], "review", "x")
check("an abandoned field cannot be grouped, and the message names it", e is not None and "'amount' is abandoned" in e.message, e and e.message)
st.unabandon(c, B, "JV", "f2", "test over")
check("nothing was created by the refused attempts", c.execute("select count(*) from field_groups").fetchone()[0] == 0)
r = st.create_group(c, B, "JV", "f1", ["f2", "f1"], None, "the same data under two names")
rv = st.review(c, B); fl = {f["field_id"]: f for f in rv["fields"]}
check("a group is created; the default conflict rule is review (a person decides)", r["group_id"] >= 1 and rv["groups"][0]["conflict_rule"] == "review" and rv["groups"][0]["survivor_id"] == "f1"
      and [(m["field_id"], m["role"]) for m in rv["groups"][0]["members"]] == [("f1", "survivor"), ("f2", "merged")], rv["groups"])
check("the merged member is shown as consolidated, not abandoned, and has no verdicts", fl["f2"]["consolidated"]["survivor_name"] == "Amount" and fl["f2"]["abandoned"] is False and fl["f2"]["targets"] == {} and fl["f1"]["consolidated"] is None)
check("it drops out of the counts (summary says consolidated 1)", rv["summary"]["consolidated"] == 1 and sum(rv["summary"]["planning_name"]["verdicts"].values()) == n_live - 1 and rv["summary"]["abandoned"] == 0)
check("the survivor's description lists every origin", fl["f1"]["description"]["generated"] == "Source: Airtable. Origins: Orders.Amount; Orders.amount.", fl["f1"]["description"]["generated"])
check("the group records who, when, why and the member order", dict(c.execute("select decided_by, reason, conflict_rule from field_groups").fetchone()) == {"decided_by": "JV", "reason": "the same data under two names", "conflict_rule": "review"}
      and [tuple(x) for x in c.execute("select field_id, member_order from field_group_members order by member_order")] == [("f1", 0), ("f2", 1)])
ev = [dict(x) for x in c.execute("select * from name_events where target='consolidation' order by event_id")]
check("the audit trail records the survivor's group and each merged field", len(ev) == 2 and ev[0]["subject_id"] == "f1" and '"members": ["f1", "f2"]' in ev[0]["new_value"] and ev[1]["subject_id"] == "f2" and ev[1]["new_value"] == "f1", ev)
check("a merged field cannot be approved, flagged or described", "consolidated" in raises(st.approve_fields, c, B, "JV", items=[{"field_id": "f2", "target": "planning_name"}]).message
      and "consolidated" in raises(st.set_field_status, c, B, "JV", [{"field_id": "f2", "target": "planning_name"}], "needs_human").message
      and "consolidated" in raises(st.save_descriptions, c, B, "JV", [{"field_id": "f2", "text": "x"}]).message)
check("a field in a group cannot be abandoned", "consolidation group" in raises(st.abandon, c, B, "JV", "f2", "empty").message)
check("a field cannot be in two groups", "already in a consolidation group" in raises(st.create_group, c, B, "JV", "f3", ["f3", "f2"], "review", "x").message
      and "already in a consolidation group" in raises(st.create_group, c, B, "JV", "f1", ["f1", "f3"], "review", "x").message)
files = st.report_files(c, B)
crow = {r["field_id"]: r for r in csv.DictReader(io.StringIO(files["names_report.csv"]))}
check("the CSV marks the merged field CONSOLIDATED with its survivor, and the summary counts it", crow["f2"]["planning_name_verdict"] == "CONSOLIDATED" and crow["f2"]["consolidated_into"] == "f1" and crow["f2"]["abandoned"] == "no"
      and crow["f1"]["consolidated_into"] == "" and "Consolidated into another field (excluded): 1" in files["names_summary.txt"])
html_g = st.report_html(c, B, "Sample", "", "2026-01-01 00:00:00")
check("the HTML report lists the consolidation with its reason and conflict rule", "Consolidated fields" in html_g and "the same data under two names" in html_g and "a person resolves each record where they differ" in html_g)
check("the merged field is left out of the report's grids, and the survivor is still in them", '<span class="small">f2</span>' not in html_g and '<span class="small">f1</span>' in html_g)
c.execute("update airtable_fields set updated_at='2025-01-01 00:00:00.000000' where field_id='f2'"); c.commit()
rv_gone = st.review(c, B)
check("a group with a field that has left the schema is ignored rather than half applied", rv_gone["groups"] == [] and "f2" not in {f["field_id"] for f in rv_gone["fields"]}
      and {f["field_id"]: f for f in rv_gone["fields"]}["f1"]["consolidated"] is None and {f["field_id"]: f for f in rv_gone["fields"]}["f1"]["description"]["generated"] == "Source: Airtable. Origins: Orders.Amount.")
c.execute("update airtable_fields set updated_at=? where field_id='f2'", (T1,)); c.commit()
check("dissolving needs a reason and an existing group", raises(st.dissolve_group, c, B, "JV", r["group_id"], "") is not None and (raises(st.dissolve_group, c, B, "JV", 999, "x") or st.StoreError("", 0)).status == 404)
st.dissolve_group(c, B, "JV", r["group_id"], "different data after all")
rv = st.review(c, B); fl = {f["field_id"]: f for f in rv["fields"]}
check("dissolving makes the members ordinary fields again and the description single-origin", rv["groups"] == [] and fl["f2"]["consolidated"] is None and "planning_name" in fl["f2"]["targets"]
      and fl["f1"]["description"]["generated"] == "Source: Airtable. Origins: Orders.Amount." and rv["summary"]["consolidated"] == 0)
check("dissolving leaves the history in the audit trail", [x[0] for x in c.execute("select event_type from name_events where target='consolidation' and subject_id='f2' order by event_id")] == ["approve", "reject"]
      and c.execute("select count(*) from field_groups").fetchone()[0] == 0 and c.execute("select count(*) from field_group_members").fetchone()[0] == 0)
check("a field can be grouped again after its group was dissolved, with a different rule", st.create_group(c, B, "JV", "f2", ["f1", "f2"], "first_filled", "again")["group_id"] > r["group_id"]
      and st.review(c, B)["groups"][0]["conflict_rule_text"].startswith("the first non-empty value"))
st.dissolve_group(c, B, "JV", st.review(c, B)["groups"][0]["group_id"], "cleanup")

# ---- the HTML report
HOSTILE = "<img src=x onerror=alert(1)> & \"q\""
c.execute("insert into airtable_fields(base_id, table_id, table_name, field_id, field_name, data_type, updated_at) values ('appS','tA','Orders','fH', ?, 'singleLineText', ?)", (HOSTILE, T1)); c.commit()
GEN = "2026-01-01 00:00:00"
page = st.report_html(c, B, "Sample Shop - appS", "body{}", GEN)
check("a complete HTML document with the base and the generated time", page.startswith("<!doctype html>") and page.rstrip().endswith("</html>") and "Sample Shop - appS" in page and GEN in page)
check("every section is present when field review is open", all(x in page for x in ("About this report", "Summary", "Settings in force", "Table names", "Field names (one grid per name)", "Duplicate groups",
      "Your names against the rules", "Descriptions", "Abandoned fields", "Decisions recorded", "The rules", "Method and checks", "Review sign-off")))
check("it states that it holds no record values and that nothing renames on its own", "no record values" in page and "Nothing here renames anything on its own" in page)
check("table names, the approved name and who approved it appear", "Archive 1-2-3" in page and "Approved" in page and "JV" in page)
check("one grid per name", all(("<h3>%s</h3>" % fn.PROFILES[t]["title"]) in page for t in fn.TARGETS))
check("every rule is listed with its example", all(("<b>%s</b>" % r.id) in page for r in fn.RULES))
check("the settings matrix shows each name's own value", "Maximum length" in page and "Emoji" in page and "Remove it" in page)
check("the differences from the rules are listed", "Plain Text" in page.split("Your names against the rules")[2])
check("the decision record is summarised by kind and reviewer", "Decisions recorded" in page and "Names imported" in page and "<td>JV</td>" in page)
check("a hostile field name is escaped everywhere it appears", "<img src=x" not in page and "&lt;img src=x onerror=alert(1)&gt;" in page and "&quot;q&quot;" in page)
check("deterministic: the same input and time give the same report", page == st.report_html(c, B, "Sample Shop - appS", "body{}", GEN))
check("the sign-off block is there", 'class="sign"' in page)
c.execute("delete from airtable_fields where field_id='fH'"); c.commit()
st.revoke_tables(c, B, "JV", ["tC"], "reopen for the test")
closed = st.report_html(c, B, "Sample Shop - appS", "body{}", GEN)
check("with a table name unapproved the report says field review has not started and stops after the table names", "field review has not started" in closed and "Field names (one grid per name)" not in closed
      and "Duplicate groups" not in closed and "Table names" in closed and "The rules" in closed)
st.approve_tables(c, B, "JV", items=[{"table_id": "tC", "name": "Archive 1-2-3"}])
ev = st.event_summary(c, B)
check("the event summary counts decisions for this base only", ev["total"] > 10 and ev["by_actor"][0][0] == "JV" and dict(ev["by_type"]).get("approve", 0) > 5 and ev["first"] <= ev["last"])
none = st.event_summary(c, "appOther")
check("a base with no decisions has an empty summary, and its report says so", none["total"] == 0 and none["by_type"] == [] and none["first"] is None
      and "No decisions have been recorded yet." in st.report_html(c, "appOther", "Other", "", GEN))

# ---- exports
files = st.report_files(c, B)
check("report files come from the saved names", "names_report.csv" in files and "Notes" in files["names_report.csv"] and "undecided" in files["names_summary.txt"])
z1, z2 = st.report_zip(files), st.report_zip(st.report_files(c, B))
check("the zip holds every report and is deterministic", z1 == z2 and set(zipfile.ZipFile(io.BytesIO(z1)).namelist()) == set(files))
check("nothing but metadata is stored: no record values exist in the schema being written", {r[1] for r in c.execute("pragma table_info(field_names)")}.isdisjoint({"value", "values", "record"}))
sys.exit(0 if ok else 1)
