"""Pure-function tests for fieldgroups.py (consolidation rules) and the merged origins in descriptions. Fabricated names only."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import fielddesc as fd, fieldgroups as fg
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else x)

def f(i, name, table="t1", typ="singleLineText", **kw):
    d = {"field_id": i, "table_id": table, "table_name": "Orders" if table == "t1" else "Other", "field_name": name, "data_type": typ}
    d.update(kw)
    return d
FIELDS = {x["field_id"]: x for x in [f("a", "Phone"), f("b", "Telephone"), f("c", "Tel No"), f("d", "Other table field", table="t2"), f("e", "Fax")]}
def reason(*a, **k):
    try:
        fg.validate_group(*a, **k)
    except ValueError as e:
        return str(e)
    return None

# ---- validation
check("a valid group returns its members with the survivor first, in the order given", fg.validate_group("b", ["a", "b", "c"], FIELDS) == ["b", "a", "c"])
check("duplicates in the list are ignored", fg.validate_group("a", ["a", "b", "a", "b"], FIELDS) == ["a", "b"])
check("the survivor must be a member", "survives" in (reason("z", ["a", "b"], FIELDS) or "") and "survives" in (reason("c", ["a", "b"], FIELDS) or ""))
check("it needs 2 to %d fields" % fg.MAX_MEMBERS, "between 2" in (reason("a", ["a"], FIELDS) or "") and "between 2" in (reason("a", ["a"] + ["f%d" % i for i in range(20)], FIELDS) or ""))
check("every field must be in the base", "not in this base" in (reason("a", ["a", "nope"], FIELDS) or ""))
check("only fields of one table can be consolidated", "same table" in (reason("a", ["a", "d"], FIELDS) or ""))
check("an abandoned field cannot be a member, and the message names it", "'Telephone' is abandoned" in (reason("a", ["a", "b"], FIELDS, abandoned_ids={"b"}) or ""))
check("a field already in a group cannot be in another", "already in a consolidation group" in (reason("a", ["a", "b"], FIELDS, grouped_ids={"a"}) or ""))
check("a table that is out of scope cannot have a group", "out of scope" in (reason("a", ["a", "b"], FIELDS, out_table_ids={"t1"}) or ""))
check("an unknown conflict rule is refused; all three are accepted", "conflict rule" in (reason("a", ["a", "b"], FIELDS, conflict_rule="maybe") or "")
      and all(reason("a", ["a", "b"], FIELDS, conflict_rule=r) is None for r in fg.CONFLICT_RULES))
check("every rule has a plain-language sentence", set(fg.RULE_TEXT) == set(fg.CONFLICT_RULES) and all(fg.RULE_TEXT.values()))
check("the default conflict rule is the cautious one: a person decides", reason("a", ["a", "b"], FIELDS) is None and fg.CONFLICT_RULES[0] == "review")

# ---- effects
G = [{"group_id": 1, "table_id": "t1", "survivor_id": "b", "member_ids": ["b", "a", "c"], "conflict_rule": "review"}, {"group_id": 2, "table_id": "t1", "survivor_id": "e", "member_ids": ["e", "d"], "conflict_rule": "survivor_wins"}]
check("merged members are every member but the survivor", set(fg.merged_ids(G)) == {"a", "c", "d"} and fg.merged_ids(G)["a"]["group_id"] == 1)
check("survivors map to their group", set(fg.survivors(G)) == {"b", "e"} and fg.survivors(G)["e"]["conflict_rule"] == "survivor_wins")
check("no groups, no effects", fg.merged_ids([]) == {} and fg.survivors([]) == {})

# ---- the survivor's description lists every origin
by = FIELDS
check("a survivor's description lists its own origin, then each merged member's", fd.generate_description(by["b"], by, merged=[by["a"], by["c"]]) == "Source: Airtable. Origins: Orders.Telephone; Orders.Phone; Orders.Tel No.")
check("without merged members it is unchanged", fd.generate_description(by["b"], by) == "Source: Airtable. Origins: Orders.Telephone.")
check("an origin is never listed twice", fd.generate_description(by["b"], by, merged=[by["a"], by["a"], by["b"]]) == "Source: Airtable. Origins: Orders.Telephone; Orders.Phone.")
lk = f("l", "Phone (from X)", typ="multipleLookupValues", source_field_id="a", source_link_field_id="b")
check("a merged lookup contributes its own sources", fd.generate_description(by["e"], dict(by, l=lk), merged=[lk]) == "Source: Airtable. Origins: Orders.Fax; Orders.Phone; Orders.Telephone.")
check("the merged description still round-trips through the parser", fd.parse_lineage(fd.generate_description(by["b"], by, merged=[by["a"], by["c"]])) is not None)
check("emoji in a merged member's name are removed too", "☕" not in fd.generate_description(by["b"], by, merged=[f("z", "Cafe " + chr(0x2615))]))
sys.exit(0 if ok else 1)
