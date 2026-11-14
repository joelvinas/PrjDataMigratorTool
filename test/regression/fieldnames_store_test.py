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
check("every table is undecided while no scope store exists", rv["scope_available"] is False and {t["scope"] for t in rv["tables"]} == {"undecided"} and rv["scope_counts"]["undecided"] == 4)
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

# ---- scope (the later table_scope store): out tables are skipped and not part of the gate; undecided stays visible
c.executescript("""CREATE TABLE table_scope (base_id TEXT NOT NULL, table_id TEXT NOT NULL, decision TEXT NOT NULL CHECK(decision IN ('in','out')),
                   decided_by TEXT, decided_at TIMESTAMP, reason TEXT, PRIMARY KEY (base_id, table_id));
                   INSERT INTO table_scope VALUES ('appS','tD','out','JV','2026-02-01','not needed'), ('appS','tA','in','JV','2026-02-01','core table');""")
rv = st.review(c, B); tb = {t["table_id"]: t for t in rv["tables"]}
check("scope badges: in, out and undecided are different things", rv["scope_available"] and tb["tA"]["scope"] == "in" and tb["tD"]["scope"] == "out"
      and tb["tB"]["scope"] == "undecided" and rv["scope_counts"] == {"in": 1, "out": 1, "undecided": 2})
check("an out-of-scope table and its fields are skipped; undecided ones are validated", "f10" not in {f["field_id"] for f in rv["fields"]} and "f8" in {f["field_id"] for f in rv["fields"]}
      and tb["tD"]["verdict"] is None and rv["in_scope_tables"] == 3)

# ---- exports
files = st.report_files(c, B)
check("report files come from the saved names", "names_report.csv" in files and "Notes" in files["names_report.csv"] and "undecided" in files["names_summary.txt"])
z1, z2 = st.report_zip(files), st.report_zip(st.report_files(c, B))
check("the zip holds every report and is deterministic", z1 == z2 and set(zipfile.ZipFile(io.BytesIO(z1)).namelist()) == set(files))
check("nothing but metadata is stored: no record values exist in the schema being written", {r[1] for r in c.execute("pragma table_info(field_names)")}.isdisjoint({"value", "values", "record"}))
sys.exit(0 if ok else 1)
