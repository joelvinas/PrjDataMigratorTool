"""Tests for planlimits.py (documented Workfront Planning limits checked against the load plan) and its use in loadplan.build_plan. Fabricated names only."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import planlimits as pl
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])

def item(tid, kind, name, parent=None, status="ready", field_id=None, desc=None, source="airtable", system="planning"):
    return {"temp_id": tid, "system": system, "kind": kind, "parent": parent, "name": name, "status": status, "field_id": field_id, "description": desc, "source": source, "table_id": None}
def fields(rt, n, typ="singleLineText", start=0, status="ready"):
    return [item("P-F-%s-%d" % (rt, start + i), "field", "F%s%d" % (rt, start + i), "P-T-" + rt, status, "f%s%d" % (rt, start + i)) for i in range(n)], {"f%s%d" % (rt, start + i): typ for i in range(n)}
def kinds(fs): return sorted((f["limit"], f["scope"]) for f in fs)

I = [item("P-T-a", "record_type", "Orders")]; T = {}
f, t = fields("a", 21, "multilineText"); I += f; T.update(t)
check("21 long-text fields in one record type is over the limit of 20", ("long_text", "Orders") in kinds(pl.check(I, T)))
I2 = [item("P-T-a", "record_type", "Orders")]; f, t = fields("a", 20, "multilineText"); I2 += f
check("exactly 20 is allowed", pl.check(I2, t) == [])
I3 = [item("P-T-a", "record_type", "Orders")]; f, t = fields("a", 31, "multipleRecordLinks"); I3 += f
check("31 reference fields is over the limit of 30", ("reference", "Orders") in kinds(pl.check(I3, t)))
I4 = [item("P-T-a", "record_type", "Orders")]; f, t = fields("a", 28, "multipleRecordLinks"); I4 += f
r = pl.check(I4, t)
check("28 of 30 reference fields is flagged as near the limit, and says the back-reference question is open", kinds(r) == [("reference_near", "Orders")] and "Postman test 4" in r[0]["detail"])
I5 = [item("P-T-a", "record_type", "Orders")]; f, t = fields("a", 501); I5 += f
check("501 fields in one record type is over the limit of 500", ("fields", "Orders") in kinds(pl.check(I5, t)))
I6 = [item("P-T-a", "record_type", "Orders")]; f, t = fields("a", 500); I6 += f; I6.append(item("S-P-a-x", "field", "Core object type", "P-T-a", source="migration"))
check("the fields the migration adds count toward the 500", ("fields", "Orders") in kinds(pl.check(I6, t)))
I7 = [item("P-T-a", "record_type", "Orders")]; f, t = fields("a", 25, "multilineText", status="blocked"); I7 += f
check("a blocked item is not going to Workfront yet, so it is not counted", pl.check(I7, t) == [])
I8 = [item("P-T-%d" % i, "record_type", "RT%03d" % i) for i in range(101)]
check("101 record types in a Workspace is over the limit of 100", [f["limit"] for f in pl.check(I8, {})] == ["record_types"])
check("100 record types is allowed, and Core items are not counted", pl.check(I8[:100], {}) == [] and pl.check([dict(x, system="core") for x in I8], {}) == [])
I9 = [item("P-T-a", "record_type", "Orders", desc="d" * 513), item("P-T-b", "record_type", "Fine", desc="d" * 512)]
r = pl.check(I9, {})
check("a description of 513 characters is over the 512 limit, 512 is fine, and the advice is to shorten the lineage (the marker comes first)", kinds(r) == [("description", "Orders")] and "marker comes first" in r[0]["detail"])
I10 = [item("P-T-a", "record_type", "Orders"), item("P-F-a-1", "field", "Status", "P-T-a", field_id="s1")] + [item("P-O-%d" % i, "option", n, "P-F-a-1") for i, n in enumerate(["Open", "open", "OPEN", "Closed", " ", "Done "])]
r = {f["limit"]: f for f in pl.check(I10, {"s1": "singleSelect"})}
check("select options that repeat a name ignoring letter case are reported with the repeated names, and a blank option too", set(r) == {"option_duplicate", "option_blank"} and "open" in r["option_duplicate"]["detail"].lower() and r["option_duplicate"]["detail"].startswith("2 select"), r)
check("options that differ are fine", pl.check([item("P-T-a", "record_type", "Orders"), item("P-F-a-1", "field", "S", "P-T-a", field_id="s1"), item("P-O-1", "option", "A", "P-F-a-1"), item("P-O-2", "option", "B", "P-F-a-1")], {"s1": "singleSelect"}) == [])
check("the findings are sorted, so the same plan gives the same report", pl.check(I10 + I9, {"s1": "singleSelect"}) == pl.check(list(reversed(I10 + I9)), {"s1": "singleSelect"}))

# records per record type and Core history
cnt = {"tA": 25001, "tB": 25000, "tC": None}
nm = {"tA": "Big", "tB": "Edge", "tC": "Unknown"}
r = pl.check_record_counts(cnt, nm)
check("a table with more than 25,000 records is reported; exactly 25,000 and an unknown count are not", [f["scope"] for f in r] == ["Big"] and "25,001" in r[0]["detail"])
check("nothing to say when no table lands in Core", pl.check_core_history({}) == [])
r = pl.check_core_history({"t1": 0, "t2": 0}, {"t1": "A", "t2": "B"})
check("with no profiled counts the Core history assumption is reported as unchecked, not as passing", [f["limit"] for f in r] == ["core_history_unchecked"] and "Run profiling" in r[0]["detail"])
check("under 200 records across the Core tables passes silently", pl.check_core_history({"t1": 120, "t2": 79}) == [])
r = pl.check_core_history({"t1": 150, "t2": 50, "t3": 3}, {"t1": "Orders", "t2": "Tasks", "t3": "Misc"})
check("200 or more is reported with the total and the largest tables first", [f["limit"] for f in r] == ["core_history"] and "203 records" in r[0]["detail"] and r[0]["detail"].index("Orders") < r[0]["detail"].index("Tasks"), r)

# ---- through build_plan
import loadplan as lp
def tr(name): return {"candidate": name, "status": "approved", "approved_name": name}
def row(fid, table, name, typ="singleLineText"):
    return {"field_id": fid, "table_id": table, "table_name": "T", "original": name, "data_type": typ, "abandoned": False, "consolidated": None, "target_type": "Text",
            "description": {"text": "Source: Airtable. Origins: T.%s." % name}, "targets": {"planning_name": tr(name), "core_name": tr(name), "core_label": tr(name + " label")}}
RV = {"settings": None, "tables": [{"table_id": "tA", "old": "Orders", "scope": "in", "target": "planning_and_core", "approved": True, "effective": "Orders"},
                                    {"table_id": "tB", "old": "Big", "scope": "in", "target": "planning_only", "approved": True, "effective": "Big"}],
      "fields": [row("a%d" % i, "tA", "Notes%d" % i, "multilineText") for i in range(21)] + [row("b1", "tB", "Title")]}
p = lp.build_plan(RV, {}, record_counts={"tA": 250, "tB": 30000})
found = {(f["limit"], f["scope"]) for f in p["summary"]["limit_findings"]}
check("the plan reports the long-text overflow, the oversize table and the Core history together", found == {("long_text", "Orders"), ("records", "Big"), ("core_history", "Core")}, found)
check("without record counts only the structural limits are checked", {f["limit"] for f in lp.build_plan(RV, {})["summary"]["limit_findings"]} == {"long_text"})
check("a limit finding does not change any item's status", {i["status"] for i in p["items"]} == {i["status"] for i in lp.build_plan(RV, {})["items"]})

print("ALL PASS" if ok else "FAILURES")
sys.exit(0 if ok else 1)
