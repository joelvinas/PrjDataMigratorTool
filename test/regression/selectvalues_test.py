"""Pure-function tests for selectvalues.py. Fabricated values only; no server or database."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import selectvalues as sv
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else x)

check("a cell holds nothing, one value or a list", sv.values_of(None) == [] and sv.values_of("") == [] and sv.values_of([]) == [] and sv.values_of("A") == ["A"] and sv.values_of(["A", "B"]) == ["A", "B"])
F = [{"field_id": "fldS", "field_name": "Status", "data_type": "singleSelect"}, {"field_id": "fldT", "field_name": "Tags", "data_type": "multipleSelects"},
     {"field_id": "fldX", "field_name": "Note", "data_type": "singleLineText"}]
CH = {"fldS": [{"id": "selA", "name": "Open"}, {"id": "selB", "name": "Closed"}, {"id": "selC", "name": "Parked"}],
      "fldT": [{"id": "selD", "name": "Red"}, {"id": "selE", "name": "Blue"}]}
def rec(i, **f): return {"id": "rec%03d" % i, "fields": f}
R = [rec(1, fldS="Open", fldT=["Red", "Blue"]), rec(2, fldS="Open"), rec(3, fldS="open "), rec(4, fldS="Ghost", fldT=["Red", "Teal"]), rec(5, fldS="Ghost"), rec(6, fldS="Closed", fldT=["Red", "Red"]), rec(7, fldX="n")]
inv = sv.inventory(R, F)
check("only select fields are read", set(inv) == {"fldS", "fldT"})
check("records are counted once per value, even when a cell lists a value twice", inv["fldT"]["values"]["Red"]["records"] == 3 and inv["fldS"]["values"]["Open"]["records"] == 2, inv["fldT"]["values"])
check("filled and total counts", inv["fldS"]["filled"] == 6 and inv["fldS"]["total"] == 7 and inv["fldT"]["filled"] == 3)
rep = {r["field_id"]: r for r in sv.table_report(R, F, CH)}
s = rep["fldS"]
check("matched, unmatched and unused options", (s["declared_options"], s["observed_values"], s["matched_values"], s["unmatched_values"], s["unmatched_records"]) == (3, 4, 2, 2, 3) and s["unused_options"] == ["Parked"], s)
check("a value differing only by case or spacing is a near match naming the declared option", s["near_matches"] == 1 and any(u["near_match_option"] == "Open" and u["near_match_option_id"] == "selA" for u in s["unmatched"]), s["unmatched"])
check("unmatched values are ordered by record count then record ID, and labelled without their text", [u["records"] for u in s["unmatched"]] == [2, 1] and [u["label"] for u in s["unmatched"]] == ["value 1", "value 2"])
check("record IDs are given so each record can be found", s["unmatched"][0]["examples"] == ["rec004", "rec005"])
check("a multiple select checks every item", rep["fldT"]["unmatched_values"] == 1 and rep["fldT"]["unmatched_records"] == 1 and rep["fldT"]["unused_options"] == [])
check("the text of an unmatched value is nowhere in the result", "Ghost" not in json.dumps(rep) and "Teal" not in json.dumps(rep) and "open " not in json.dumps(rep))
check("a field with no declared options treats every value as unmatched", sv.table_report(R, F, {})[0]["unmatched_values"] == 4)
check("the same input gives the same output", json.dumps(sv.table_report(R, F, CH), sort_keys=True) == json.dumps(sv.table_report(list(R), F, CH), sort_keys=True))
check("no records gives zero counts", sv.table_report([], F, CH)[0]["observed_values"] == 0)
sys.exit(0 if ok else 1)
