"""Pure-function tests for fieldcompare.py. Fabricated values only; no server or database."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import fieldcompare as fc
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else x)

# ---- empty follows the profiler
check("missing, null, empty string and empty list are empty; zero and false are values", all(fc.is_empty(v) for v in (None, "", [], {})) and not any(fc.is_empty(v) for v in (0, False, "0", [0])))
check("a checkbox is never empty", not fc.is_empty(None, "checkbox") and not fc.is_empty(False, "checkbox"))

# ---- classify
C = fc.classify
check("both empty / only one side", C(None, "") == "both_empty" and C("x", None) == "only_a" and C(None, "x") == "only_b" and C([], ["x"]) == "only_b")
check("identical values agree, including zero and false", C("Blue", "Blue") == "agree" and C(0, 0) == "agree" and C(False, False) == "agree" and C({"a": 1, "b": 2}, {"b": 2, "a": 1}) == "agree")
check("the same list items in another order are 'reordered'", C(["a", "b"], ["b", "a"]) == "reordered" and C(["a", "b"], ["a", "b"]) == "agree")
check("a different list is a conflict", C(["a", "b"], ["a", "c"]) == "conflict" and C(["a"], ["a", "b"]) == "conflict")
check("case, spaces and number-versus-text differences are 'loose'", C("Blue", " blue ") == "loose" and C(5, "5") == "loose" and C(5.0, 5) == "agree" and C(5.0, "5") == "loose" and C(True, "true") == "loose")
check("different values conflict, and zero is not false", C("Blue", "Red") == "conflict" and C(1, 2) == "conflict" and C(0, False) == "conflict")
check("zero is not empty, so zero against a value is a conflict, not 'only one'", C(0, 5) == "conflict" and C(0, None) == "only_a")
check("checkboxes: unchecked in one and checked in the other conflict; two unchecked agree", C(False, True, "checkbox", "checkbox") == "conflict" and C(None, False, "checkbox", "checkbox") == "agree"
      and C(None, None, "checkbox", "checkbox") == "agree")
att = lambda i, name, size: {"id": i, "filename": name, "size": size, "url": "u" + i}
check("attachments are compared by file name and size, not by attachment ID", C([att("att1", "a.pdf", 10)], [att("att2", "a.pdf", 10)]) == "agree" and C([att("att1", "a.pdf", 10)], [att("att2", "a.pdf", 11)]) == "conflict")
check("unicode and emoji values compare correctly", C("Café ☕", "Café ☕") == "agree" and C("Café", "Cafe") == "conflict")

# ---- pairs and verdicts
def rec(i, **f):
    return {"id": "rec%03d" % i, "fields": f}
R = [rec(1, A="x", B="x"), rec(2, A="y", B="y"), rec(3, A="z"), rec(4, B="w"), rec(5), rec(6, A="Q", B="q"), rec(7, A=["a", "b"], B=["b", "a"]), rec(8, A="1", B="2")]
p = fc.compare_pair(R, "A", "B")
c = p["counts"]
check("every record lands in exactly one bucket", sum(c[k] for k in fc.BUCKETS) == c["total"] == 8, c)
check("the counts are right", (c["agree"], c["only_a"], c["only_b"], c["both_empty"], c["loose"], c["reordered"], c["conflict"]) == (2, 1, 1, 1, 1, 1, 1), c)
check("a_filled and b_filled", c["a_filled"] == 6 and c["b_filled"] == 6, c)
check("examples are record IDs only, by bucket", p["examples"] == {"only_a": ["rec003"], "only_b": ["rec004"], "reordered": ["rec007"], "loose": ["rec006"], "conflict": ["rec008"]}, p["examples"])
check("a conflict gives the 'conflict' verdict", p["verdict"] == "conflict" and "1 record" in p["verdict_text"])
check("examples are capped", len(fc.compare_pair([rec(i, A="a", B="b") for i in range(50)], "A", "B")["examples"]["conflict"]) == fc.EXAMPLE_CAP)
V = lambda rows: fc.compare_pair(rows, "A", "B")["verdict"]
check("verdict: identical", V([rec(1, A="x", B="x"), rec(2, A="y", B="y"), rec(3)]) == "identical")
check("verdict: agree where both are filled, but one has more", V([rec(1, A="x", B="x"), rec(2, A="y")]) == "agree")
check("verdict: agree with formatting differences only", V([rec(1, A="x", B="X")]) == "agree")
check("verdict: never filled together is complementary (safe to merge)", V([rec(1, A="x"), rec(2, B="y"), rec(3)]) == "complementary")
check("verdict: nothing filled", V([rec(1), rec(2)]) == "no_data" and V([]) == "no_data")
check("verdict: conflicts", V([rec(1, A="x", B="y")]) == "conflict")
check("the result holds no values: only counts, IDs, names and verdict text", all(v not in json.dumps(fc.compare_pair([rec(1, A="SECRET-1", B="SECRET-2")], "A", "B")) for v in ("SECRET-1", "SECRET-2")))

# ---- groups
fields = [{"field_id": "A", "field_name": "Alpha", "data_type": "singleLineText"}, {"field_id": "B", "field_name": "Beta", "data_type": "number"}, {"field_id": "C", "field_name": "Gamma", "data_type": "singleLineText"}]
g = fc.compare_group([rec(1, A="x", B=1, C="x"), rec(2, A="y"), rec(3), rec(4, B=2, C="q")], fields)
check("every pair of three fields is compared", [(x["a"]["field_id"], x["b"]["field_id"]) for x in g["pairs"]] == [("A", "B"), ("A", "C"), ("B", "C")])
check("the pair notes when the field types differ", g["pairs"][0]["types_differ"] is True and g["pairs"][1]["types_differ"] is False)
check("the histogram says how many of the fields are filled per record", g["filled_together"] == {0: 1, 1: 1, 2: 1, 3: 1}, g["filled_together"])
for bad in ([fields[0]], fields * 4):
    try:
        fc.compare_group([], bad); raised = False
    except ValueError:
        raised = True
    check("compare needs 2 to %d fields (%d given)" % (fc.MAX_FIELDS, len(bad)), raised)
check("deterministic", json.dumps(fc.compare_group(R, fields[:2]), sort_keys=True) == json.dumps(fc.compare_group(R, fields[:2]), sort_keys=True))
sys.exit(0 if ok else 1)
