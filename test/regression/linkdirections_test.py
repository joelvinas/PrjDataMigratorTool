"""Tests for linkdirections.py and the 'confirmed' load order (server.compute_graph). Fabricated tables only; in-memory database, no server."""
import os, sqlite3, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import linkdirections as ld
import server as srv
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else x)

# ---- pure rules
check("suggestion: a single-record side is the child", ld.suggest(True, False, True) == "many_to_one" and ld.suggest(False, True, True) == "one_to_many")
check("suggestion: both single is one to one; both multiple or unknown is many to many", ld.suggest(True, True, True) == "one_to_one" and ld.suggest(False, False, True) == "many_to_many" and ld.suggest(None, None, True) == "many_to_many")
check("suggestion: a one-way link follows its own setting", ld.suggest(True, None, False) == "many_to_one" and ld.suggest(False, None, False) == "many_to_many")
check("ordering: one to many puts this table first, many to one the other table", ld.ordering("A", "B", "one_to_many", None) == ("B", "A") and ld.ordering("A", "B", "many_to_one", None) == ("A", "B"))
check("ordering: the tiebreaker decides one to one; automatic is non-binding; a missing tiebreaker is no decision",
      ld.ordering("A", "B", "one_to_one", "this_first") == ("B", "A") and ld.ordering("A", "B", "many_to_many", "other_first") == ("A", "B") and ld.ordering("A", "B", "one_to_one", "automatic") == "automatic" and ld.ordering("A", "B", "one_to_one", None) is None)
check("a mirrored choice is the partner's view", ld.MIRROR["one_to_many"] == "many_to_one" and ld.TIE_MIRROR["this_first"] == "other_first" and ld.TIE_MIRROR["automatic"] == "automatic")
settled, added, loops = ld.settle_automatic({("B", "A")}, [("k1", "A", "B"), ("k2", "C", "D")], lambda t: t)
check("automatic: a pair the decided edges already order is derived; an unordered one is chosen repeatably", settled == {"k1"} and added == {"k2": ("D", "C")} and not loops, (settled, added, loops))
settled, added, loops = ld.settle_automatic(set(), [("x", "A", "B"), ("y", "B", "C"), ("z", "A", "C")], lambda t: t)
check("automatic: pairs ordered only by the tool's own choices are reported as a loop for a person", set(added) == {"x", "y"} and loops == {"z"} and not settled, (settled, added, loops))

# ---- a fabricated schema: Customers <- Orders <- Items, Articles <-> Tags (many to many), Notes -> Customers (one way), Items <-> Customers (extra pair)
STAMP = "2026-01-01 00:00:00"
def make(tables, fields):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
    for tid, nm in tables.items():
        c.execute("INSERT INTO airtable_tables(table_id, base_id, table_name, updated_at) VALUES (?,?,?,?)", (tid, "appLink", nm, STAMP))
    for fid, tid, nm, link, inv, single in fields:
        c.execute("INSERT INTO airtable_fields(base_id, table_id, table_name, field_id, field_name, data_type, linked_table_id, inverse_link_field_id, prefers_single_record_link, updated_at) "
                  "VALUES ('appLink', ?, ?, ?, ?, ?, ?, ?, ?, ?)", (tid, tables[tid], fid, nm, "multipleRecordLinks" if link else "singleLineText", link, inv, single, STAMP))
    c.commit()
    return c
T = {"tC": "Customers", "tO": "Orders", "tI": "Items", "tA": "Articles", "tT": "Tags", "tN": "Notes"}
F = [("fCO", "tC", "Orders", "tO", "fOC", 0), ("fOC", "tO", "Customer", "tC", "fCO", 1),
     ("fOI", "tO", "Items", "tI", "fIO", 0), ("fIO", "tI", "Order", "tO", "fOI", 1),
     ("fAT", "tA", "Tags", "tT", "fTA", 0), ("fTA", "tT", "Articles", "tA", "fAT", 0),
     ("fNC", "tN", "Customer", "tC", None, 1),
     ("fIC", "tI", "Buyer", "tC", "fCI", 0), ("fCI", "tC", "Bought items", "tI", "fIC", 0),
     ("fTitle", "tC", "Name", None, None, None)]
c = make(T, F)
g = srv.compute_graph(c, "literal")
check("before any decision the pairs are all undecided and the order is provisional", g["provisional"] and g["directions"]["pairs"] == 5 and g["directions"]["undecided"] == 5, g["directions"])
r = ld.sync(c, srv._link_direction_rows(c))
check("sync writes one proposal row per link field, not per ordinary field", r == {"added": 9, "updated": 0} and c.execute("SELECT COUNT(*) FROM field_relationships").fetchone()[0] == 9, r)
check("sync is repeatable", ld.sync(c, srv._link_direction_rows(c)) == {"added": 0, "updated": 9})
row = {x["airtable_field_id"]: x for x in c.execute("SELECT * FROM field_relationships")}
check("proposals carry the suggestion and decide nothing", row["fOC"]["suggested_type"] == "many_to_one" and row["fCO"]["suggested_type"] == "one_to_many" and row["fAT"]["suggested_type"] == "many_to_many" and row["fNC"]["suggested_type"] == "many_to_one"
      and all(x["confirmed"] == 0 and x["relationship_type"] is None for x in row.values()))

def bad(fn_, *a):
    try:
        fn_(c, *a); return None
    except ld.DirectionError as e:
        return e.message
check("a decision needs who", "initials" in (bad(ld.decide, "", "fOC", "many_to_one", None, "") or ""))
check("an unknown direction is refused", bad(ld.decide, "JV", "fOC", "sideways", None, "") is not None)
check("one to one and many to many need a tiebreaker", "which table loads first" in (bad(ld.decide, "JV", "fAT", "many_to_many", None, "") or ""))
check("an unknown field is refused", bad(ld.decide, "JV", "fNope", "many_to_one", None, "") is not None)

ld.decide(c, "JV", "fOC", "many_to_one", None, "orders belong to one customer")
row = {x["airtable_field_id"]: x for x in c.execute("SELECT * FROM field_relationships")}
check("the choice is stored with who, when and why", row["fOC"]["confirmed"] == 1 and row["fOC"]["approved_by"] == "JV" and row["fOC"]["approved_at"] and row["fOC"]["reason"] == "orders belong to one customer" and row["fOC"]["auto_set"] == 0)
check("the partner is auto-set to the mirror view", row["fCO"]["relationship_type"] == "one_to_many" and row["fCO"]["confirmed"] == 1 and row["fCO"]["auto_set"] == 1 and row["fCO"]["approved_by"] == "JV")
ld.decide(c, "KR", "fCO", "one_to_one", "this_first", "")
row = {x["airtable_field_id"]: x for x in c.execute("SELECT * FROM field_relationships")}
check("either end can be changed: the other end follows, with the tiebreaker mirrored", row["fCO"]["auto_set"] == 0 and row["fOC"]["auto_set"] == 1 and row["fOC"]["relationship_type"] == "one_to_one" and row["fOC"]["tiebreaker"] == "other_first" and row["fOC"]["approved_by"] == "KR")
ld.decide(c, "JV", "fOC", "clear", None, "")
row = {x["airtable_field_id"]: x for x in c.execute("SELECT * FROM field_relationships")}
check("clearing returns both ends to undecided", row["fOC"]["confirmed"] == 0 and row["fCO"]["confirmed"] == 0 and row["fCO"]["relationship_type"] is None and row["fCO"]["approved_by"] is None)

# ---- the confirmed load order
def seq(g): return {m["table_name"]: m["load_order_sequence"] for m in g["matrix"]}
for fid, typ, tie in (("fOC", "many_to_one", None), ("fIO", "many_to_one", None), ("fNC", "many_to_one", None), ("fAT", "many_to_many", "this_first"), ("fIC", "many_to_one", None)):
    ld.decide(c, "JV", fid, typ, tie, "")
g = srv.compute_graph(c, "confirmed")
s = seq(g)
check("every pair decided: not provisional, no cycles", not g["provisional"] and g["directions"]["undecided"] == 0 and g["cycles"] == [], (g["directions"], g["cycles"]))
check("parents load before children", s["Customers"] < s["Orders"] < s["Items"] and s["Customers"] < s["Notes"] and s["Articles"] < s["Tags"], s)
tr = {f["field_name"] + "@" + f["table_name"]: f["treatment"] for f in g["link_fields"]}
check("the decided field orders the load and its partner is labelled the inverse", tr["Customer@Orders"] == "Orders the load" and tr["Orders@Customers"] == "Inverse of a decided pair", tr)
lit = srv.compute_graph(c, "literal")
check("literal mode is unchanged by the decisions: pairs are still cycles", len(lit["cycles"]) >= 1)

# a half-decided database stays provisional and keeps literal edges for the rest
c2 = make(T, F); ld.sync(c2, srv._link_direction_rows(c2)); ld.decide(c2, "JV", "fOC", "many_to_one", None, "")
g2 = srv.compute_graph(c2, "confirmed")
check("some pairs undecided: provisional, with the count", g2["provisional"] and g2["directions"]["undecided"] == 4 and any(cy for cy in g2["cycles"]), g2["directions"])

# ---- automatic pairs
c3 = make(T, F); ld.sync(c3, srv._link_direction_rows(c3))
for fid, typ, tie in (("fOC", "many_to_one", None), ("fIO", "many_to_one", None), ("fNC", "many_to_one", None), ("fAT", "many_to_many", "automatic"), ("fIC", "one_to_one", "automatic")):
    ld.decide(c3, "JV", fid, typ, tie, "")
d = {}
g3 = srv.compute_graph(c3, "confirmed", d)
s3 = seq(g3)
tr = {f["field_name"] + "@" + f["table_name"]: f["treatment"] for f in g3["link_fields"]}
check("an automatic pair the other decisions already settle is derived and adds no edge", tr["Buyer@Items"] == "Automatic: already ordered" and any(k.startswith("fCI") or "fCI" in k for k in d["derived_keys"]), (tr, d["derived_keys"]))
check("an automatic pair nothing else settles is chosen by the tool, repeatably, and flagged for review", len(g3["directions"]["automatic_chosen"]) == 1 and tr["Tags@Articles"] == "Automatic: order chosen, review" and s3["Articles"] < s3["Tags"], (g3["directions"], tr))
check("automatic pairs are not counted as undecided", not g3["provisional"] and g3["cycles"] == [])
ld.mark_derived(c3, d["derived_keys"], d["units"])
check("a recompute flags derived pairs on the stored rows", c3.execute("SELECT COUNT(*) FROM field_relationships WHERE derived=1").fetchone()[0] == 2 and c3.execute("SELECT COUNT(*) FROM field_relationships WHERE derived=1 AND confirmed=1").fetchone()[0] == 2)
check("the same input gives the same order", seq(srv.compute_graph(c3, "confirmed")) == s3)

# a loop of automatic pairs is surfaced, not chosen silently
T4 = {"tX": "Xs", "tY": "Ys", "tZ": "Zs"}
F4 = [("xy", "tX", "Y", "tY", "yx", 0), ("yx", "tY", "X", "tX", "xy", 0), ("yz", "tY", "Z", "tZ", "zy", 0), ("zy", "tZ", "Y", "tY", "yz", 0), ("zx", "tZ", "X", "tX", "xz", 0), ("xz", "tX", "Z", "tZ", "zx", 0)]
c4 = make(T4, F4); ld.sync(c4, srv._link_direction_rows(c4))
for fid in ("xy", "yz", "zx"):
    ld.decide(c4, "JV", fid, "many_to_many", "automatic", "")
g4 = srv.compute_graph(c4, "confirmed")
check("a loop among automatic pairs is surfaced for a person", len(g4["directions"]["automatic_loops"]) == 1 and len(g4["directions"]["automatic_chosen"]) == 2, g4["directions"])

# ---- scope and stored state in the plan input
c.execute("INSERT INTO table_scope(base_id, table_id, decision, decided_by) VALUES ('appLink', 'tN', 'out', 'JV')"); c.commit()
g5 = srv.compute_graph(c, "confirmed")
check("a link pair that touches an out-of-scope table is not a pair to decide", g5["directions"]["pairs"] == 4 and "Notes" not in seq(g5), g5["directions"])
check("field states feed the plan: decided is True, undecided is False", ld.field_states(c)["fOC"] is True and ld.field_states(c2)["fIO"] is False)
check("an older database without the columns reports no states", ld.field_states(sqlite3.connect(":memory:")) is None)

print("ALL PASS" if ok else "FAILURES")
sys.exit(0 if ok else 1)
