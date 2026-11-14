"""Pure-function tests for fielddesc.py (the lineage-syntax field descriptions). Fabricated names only; no server or database."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import fielddesc as fd
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else x)

# ---- format and quoting
check("the documented shape", fd.format_lineage("Airtable", [("Orders", "Amount"), ("Customers", "Name")]) == "Source: Airtable. Origins: Orders.Amount; Customers.Name.")
check("a name with a period, semicolon or quote is wrapped in quotes, inner quotes doubled", fd.quote_part("1.2.3 Archive") == '"1.2.3 Archive"' and fd.quote_part("a;b") == '"a;b"'
      and fd.quote_part('say "hi"') == '"say ""hi"""' and fd.quote_part("Plain Name (USD)") == "Plain Name (USD)")
check("an empty or space-padded name is quoted", fd.quote_part("") == '""' and fd.quote_part(" x ") == '" x "')
check("control characters in a name become a space", fd.quote_part("a" + chr(9) + "b" + chr(10) + "c") == "a b c")
check("an optional free-text part follows the origins on one line", fd.format_lineage("Airtable", [("T", "F")], "Airtable description: first" + chr(10) + "second") ==
      "Source: Airtable. Origins: T.F. Airtable description: first second")
check("a migration-made field uses Migration as the source", fd.format_lineage("Migration", [("Orders", "Core object ID")], "Set by the migration and the publish automation.").startswith("Source: Migration. Origins:"))
try:
    fd.format_lineage("A.B", [("T", "F")]); bad = False
except ValueError:
    bad = True
check("a source containing a period is refused", bad)
check("no origins reads as unknown", fd.format_lineage("Airtable", []) == "Source: Airtable. Origins: unknown.")

# ---- parse: every generated description reads back exactly
CASES = [[("Orders", "Amount")], [("Orders", "Amount"), ("Customers", "Name")], [("1.2.3 Archive", "Title; main")], [("Say \"hi\"", "A.B")], [("T", "")], [("Café ☕", "Prix (€)")], [(" lead", "trail ")]]
for o in CASES:
    t = fd.format_lineage("Airtable", o, "Airtable description: note")
    p = fd.parse_lineage(t)
    check("round trip: %r" % (o,), p == {"source": "Airtable", "origins": o, "rest": "Airtable description: note"}, (t, p))
check("unknown origins round trip", fd.parse_lineage("Source: Airtable. Origins: unknown.") == {"source": "Airtable", "origins": [], "rest": ""})
for bad in ("", "Origins: T.F.", "Source: Airtable.", "Source: Airtable. Origins: T.F", "Source: Airtable. Origins: T.F;G.H.", "Source: Airtable. Origins: .F.", "Source: Airtable. Origins: T.F.extra",
            'Source: Airtable. Origins: "T.F.', "Just some text", None):
    check("not lineage: %r" % (bad,), fd.parse_lineage(bad) is None, fd.parse_lineage(bad))

# ---- origins by field type
def f(fid, table, name, typ="singleLineText", **kw):
    d = {"field_id": fid, "table_name": table, "field_name": name, "data_type": typ}
    d.update(kw)
    return d
FIELDS = [f("a1", "Orders", "Amount", "number"), f("a2", "Orders", "Customer", "multipleRecordLinks", linked_table_id="tC"), f("c1", "Customers", "Name"),
          f("a3", "Orders", "Customer Name", "multipleLookupValues", source_link_field_id="a2", source_field_id="c1"),
          f("a4", "Orders", "Total Spent", "rollup", source_link_field_id="a2", source_field_id="c1"), f("a5", "Orders", "Customer Count", "count", source_link_field_id="a2"),
          f("a6", "Orders", "Label", "formula", referenced_field_ids=json.dumps(["a1", "c1", "gone", "a1"])), f("a7", "Orders", "Bare Formula", "formula"),
          f("a8", "Orders", "Broken Lookup", "multipleLookupValues", source_link_field_id="missing", source_field_id="missing"), f("a9", "Orders", "Note", field_description="  Free text.  ")]
by = {x["field_id"]: x for x in FIELDS}
g = lambda fid, **k: fd.generate_description(by[fid], by, **k)
check("a plain field lists itself", g("a1") == "Source: Airtable. Origins: Orders.Amount.")
check("a link field lists itself", g("a2") == "Source: Airtable. Origins: Orders.Customer.")
check("a lookup lists the field it reads, then the link it reads through", g("a3") == "Source: Airtable. Origins: Customers.Name; Orders.Customer.")
check("a rollup lists the same", g("a4") == "Source: Airtable. Origins: Customers.Name; Orders.Customer.")
check("a count lists the link field only", g("a5") == "Source: Airtable. Origins: Orders.Customer.")
check("a formula lists the fields it references once each, skipping unknown ones", g("a6") == "Source: Airtable. Origins: Orders.Amount; Customers.Name.")
check("a formula with no references, or a lookup that cannot be resolved, falls back to itself", g("a7") == "Source: Airtable. Origins: Orders.Bare Formula." and g("a8") == "Source: Airtable. Origins: Orders.Broken Lookup.")
check("Airtable's own description is added only when asked", g("a9") == "Source: Airtable. Origins: Orders.Note." and g("a9", include_airtable_description=True) == "Source: Airtable. Origins: Orders.Note. Airtable description: Free text."
      and g("a1", include_airtable_description=True) == g("a1"))
check("bad referenced_field_ids JSON does not crash", fd.generate_description(f("z", "T", "F", "formula", referenced_field_ids="{not json"), {}) == "Source: Airtable. Origins: T.F.")
check("every generated description parses and validates clean", all(fd.parse_lineage(g(x["field_id"])) is not None and fd.validate_description(g(x["field_id"])) == [] for x in FIELDS))
check("deterministic", [g(x["field_id"]) for x in FIELDS] == [g(x["field_id"]) for x in FIELDS])

# ---- the validator
rules = lambda t, **k: sorted(p["rule"] for p in fd.validate_description(t, **k))
check("a good description has no problems", rules("Source: Airtable. Origins: T.F.") == [])
check("empty is an error", rules("  ") == ["EMPTY"])
check("free text is a warning, not an error", rules("Just a note") == ["NOT_LINEAGE"] and fd.validate_description("Just a note")[0]["severity"] == "warn")
check("control characters are an error", "CONTROL_CHARS" in rules("Source: Airtable. Origins: T.F." + chr(10) + "more"))
check("non-ASCII characters are a warning", rules("Source: Airtable. Origins: Café.F.") == ["NON_ASCII"])
check("no length check until a limit is confirmed", fd.DESCRIPTION_MAX_LEN is None and rules("Source: Airtable. Origins: " + "x" * 5000 + ".F.") == [])
check("a length limit can be applied once known", rules("Source: Airtable. Origins: T.F.", max_len=10) == ["TOO_LONG"] and rules("Source: Airtable. Origins: T.F.", max_len=200) == [])
sys.exit(0 if ok else 1)
