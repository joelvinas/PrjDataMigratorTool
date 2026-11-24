"""Tests for Planning formula fields in Git-R-Done (planformula.py, emergency.py): the translator, the pre-check and the table order, and a run against a FAKE Planning that
accepts formula fields. Fabricated names; fake token; no network. Covers: the six documented shapes, DATEDIFF(start, end), ABS kept only when written, multi-selects and
ARRAYCONTAINS not supported, formulas made after every ordinary field and after the formulas they read, a formula over another table held back (never stored as text) and built
by a later run, the pre-check counts and the suggested order (the owner's example: T2, T1, T2), and that no value is ever sent to a formula field."""
import io, json, os, sqlite3, sys, tempfile, urllib.error
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
import emergency as em, planformula as pf, tableload as tl
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:600])

# ------------------------------------------------------------------ the translator
NAMES = {"fldStart": "Start Date", "fldEnd": "End Date", "fldA": "Field A", "fldB": "Field B", "fldName": "Name", "fldTags": "Colors"}
res = lambda kind, key: NAMES.get(key) if kind == "id" else (key if key in NAMES.values() else None)
tx = lambda t: pf.translate(t, res)
check("DATETIME_DIFF(end, start, 'days') is written DATEDIFF(start, end), positive, with no ABS", tx("DATETIME_DIFF({fldEnd}, {fldStart}, 'days')").expression == "DATEDIFF({Start Date},{End Date})")
check("WORKDAY_DIFF(start, end) keeps its order", tx("WORKDAY_DIFF({fldStart}, {fldEnd})").expression == "WEEKDAYDIFF({Start Date},{End Date})")
check("ABS stays when the Airtable formula uses it", tx("ABS(DATETIME_DIFF({fldStart}, {fldEnd}, 'days'))").expression == "ABS(DATEDIFF({End Date},{Start Date}))")
check("(a + b) * 0.015 keeps its brackets", tx("({fldA} + {fldB}) * 0.015").expression == "({Field A}+{Field B})*0.015", tx("({fldA} + {fldB}) * 0.015").expression)
check("SUM and CONCATENATE", tx("SUM({fldA}, {fldB})").expression == "SUM({Field A},{Field B})" and tx("CONCATENATE({fldName}, \"-\", {fldName})").expression == 'CONCAT({Name},"-",{Name})')
check("& becomes CONCAT", tx("{fldName} & \"x\"").expression == 'CONCAT({Name},"x")')
check("= BLANK() becomes ISBLANK, inside an IF", tx('IF({fldName} = BLANK(), "yes", "no")').expression == 'IF(ISBLANK({Name}),"yes","no")')
big = tx('IF(OR({fldA} > 3, AND({fldB} < 2, {fldName} = BLANK())), "hot", "cold")')
check("a complex condition: OR and AND become || and && with ISBLANK, and the && is flagged provisional", big.expression == 'IF(({Field A}>3 || ({Field B}<2 && ISBLANK({Name}))),"hot","cold")' and any("&&" in w for w in big.warnings), (big.expression, big.warnings))
check("a name reference resolves by the field's name", tx("{Field A} + 1").expression == "{Field A}+1")
check("ARRAYCONTAINS has no equivalent yet, so it is blocked", tx('ARRAYCONTAINS({fldTags}, "magenta")').expression is None and "ARRAYCONTAINS" in tx('ARRAYCONTAINS({fldTags}, "magenta")').reason)
check("a field with no target is reported by ID", tx("{fldZZ} + 1").missing == ["fldZZ"] and tx("{fldZZ} + 1").expression is None)
check("a name with a brace cannot be a reference", pf.translate("{fldA}", lambda k, v: "Bad{Name}").expression is None)
check("an unknown function is blocked, never guessed", tx("DATETIME_FORMAT({fldStart}, 'L')").expression is None)
check("return types: text, number, date; a checkbox has none", [pf.return_type(x) for x in ("singleLineText", "number", "dateTime", "checkbox")] == ["TEXT", "NUMBER", "DATE", None])
check("formulaOptions carry the date options only for DATE", pf.formula_options("x", "NUMBER") == {"formula": "x", "returnType": "NUMBER"} and "dateOptions" in pf.formula_options("x", "DATE"))
check("the translator imports nothing that talks to a network or a model", all(m not in open(os.path.join(ROOT, "src", "planformula.py"), encoding="utf-8").read() for m in ("import urllib", "import socket", "import random", "import http")))

# ------------------------------------------------------------------ fabricated bases
BASE, TOKEN = "appFormula001", "fakePLANtoken0123456789"
tmp = tempfile.mkdtemp(prefix="planformula_")
DDL = open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read()


def make_db(path, tables, fields):
    c = sqlite3.connect(path); c.row_factory = sqlite3.Row
    c.executescript(DDL)
    for tid, nm in tables:
        c.execute("insert into airtable_tables(table_id, base_id, table_name) values (?,?,?)", (tid, BASE, nm))
    for tid, fid, nm, ty, kw in fields:
        c.execute("insert into airtable_fields(base_id, table_id, table_name, field_id, field_name, data_type, row_count, null_count, formula_text, result_type, source_link_field_id, source_field_id, "
                  "linked_table_id, updated_at) values (?,?,?,?,?,?,3,0,?,?,?,?,?,'2026-01-01 10:00:00.000000')",
                  (BASE, tid, dict(tables)[tid], fid, nm, ty, kw.get("f"), kw.get("rt"), kw.get("link"), kw.get("src"), kw.get("to")))
    c.commit()
    return c


def catalog(conn):
    return em.formula_catalog(conn, BASE)

# the owner's example: T1 has 3 calculated fields needing T2; T2 has 1 needing T1
t = [("tT1", "Table One"), ("tT2", "Table Two")]
fx = [("tT1", "fldT1Link", "To Two", "multipleRecordLinks", {"to": "tT2"}), ("tT2", "fldT2Link", "To One", "multipleRecordLinks", {"to": "tT1"}),
      ("tT1", "fldT1Own", "Own Number", "number", {}), ("tT2", "fldT2Num", "Two Number", "number", {}),
      ("tT1", "fldT1R", "From Two", "rollup", {"link": "fldT1Link", "src": "fldT2Num", "rt": "number"}),
      ("tT2", "fldT2R", "From One", "rollup", {"link": "fldT2Link", "src": "fldT1Own", "rt": "number"})]
for i in (1, 2, 3):
    fx.append(("tT1", "fldT1F%d" % i, "One Calc %d" % i, "formula", {"f": "{fldT1R} * %d" % i, "rt": "number"}))
fx.append(("tT2", "fldT2F1", "Two Calc", "formula", {"f": "{fldT2R} + 1", "rt": "number"}))
c1 = make_db(os.path.join(tmp, "ex.db"), t, fx)
cat = catalog(c1)
a1 = cat.advise("tT1", {})
check("pre-check: all 3 calculated fields of Table One wait for Table Two", a1["counts"]["total"] == 3 and a1["counts"]["waiting"] == 3 and a1["counts"]["blocked"] == 0, a1["counts"])
check("pre-check names the table to load first and how many fields it frees", a1["waiting_on"] and a1["waiting_on"][0]["table_id"] == "tT2" and a1["waiting_on"][0]["formulas"] == 3 and not a1["waiting_on"][0]["loaded"], a1["waiting_on"])
check("suggested order for Table One: Table Two, then Table One (the one calculated field of Table Two waits for Table One)", [o["table_id"] for o in a1["order"]] == ["tT2", "tT1"]
      and a1["order"][0]["creates"] == 0 and a1["order"][0]["unlocks"] == 3 and a1["order"][1]["creates"] == 3, a1["order"])
a2 = cat.advise("tT2", {})
check("suggested order for Table Two: Table Two, Table One, Table Two again", [o["table_id"] for o in a2["order"]] == ["tT2", "tT1", "tT2"] and a2["order"][2]["creates"] == 1, a2["order"])
check("the advice is plain sentences naming the other table", any("Load Table Two first" in x for x in a1["advice"]) and any("more than once" in x for x in a2["advice"]), a1["advice"] + a2["advice"])
present = {"tbl:tT2": "Table Two", "fldT2Num": "Two Number"}
a3 = cat.advise("tT1", present)
check("once Table Two is in Planning the three are ready and no order is needed", a3["counts"]["ready"] == 3 and a3["counts"]["waiting"] == 0 and a3["order"] == [], a3["counts"])
check("a formula already in Planning counts as built, one stored as PRE- text as text", cat.advise("tT1", dict(present, fldT1F1="One Calc 1", fldT1F2="PRE-One Calc 2"))["counts"]["created"] == 1
      and cat.advise("tT1", dict(present, fldT1F2="PRE-One Calc 2"))["counts"]["stored_as_text"] == 1)
check("the same schema gives the same advice twice", json.dumps(cat.advise("tT2", {}), sort_keys=True) == json.dumps(catalog(c1).advise("tT2", {}), sort_keys=True))

# blocked reasons
tb = [("tB", "Blocked Table")]
fb = [("tB", "fldBTags", "Colors", "multipleSelects", {}), ("tB", "fldBN", "Qty", "number", {}), ("tB", "fldBL", "Customer", "multipleRecordLinks", {"to": "tZ"}),
      ("tB", "fldBF1", "Tag Text", "formula", {"f": '{fldBTags} & "x"', "rt": "singleLineText"}), ("tB", "fldBF2", "Big", "formula", {"f": "{fldBN} > 3", "rt": "checkbox"}),
      ("tB", "fldBF3", "Link Text", "formula", {"f": '{fldBL} & "x"', "rt": "singleLineText"}), ("tB", "fldBF4", "Contains", "formula", {"f": 'ARRAYCONTAINS({fldBTags}, "magenta")', "rt": "singleLineText"}),
      ("tB", "fldBF5", "Loop A", "formula", {"f": "{fldBF6} + 1", "rt": "number"}), ("tB", "fldBF6", "Loop B", "formula", {"f": "{fldBF5} + 1", "rt": "number"}),
      ("tB", "fldBF7", "Fine", "formula", {"f": "{fldBN} * 2", "rt": "number"})]
cb = make_db(os.path.join(tmp, "blk.db"), tb, fb)
ab = catalog(cb).advise("tB", {})
why = {b["name"]: " ".join(b["reasons"]) for b in ab["blocked"]}
check("a formula over a multi-select, a link, a checkbox result, ARRAYCONTAINS or a loop is blocked with its reason (and will be PRE- text)",
      ab["counts"]["blocked"] == 6 and ab["counts"]["ready"] == 1 and "multi-select" in why["Tag Text"] and "return type" in why["Big"] and "link" in why["Link Text"] and "ARRAYCONTAINS" in why["Contains"] and "cannot be built" in why["Loop A"], (ab["counts"], why))

# ------------------------------------------------------------------ a run against a fake Planning that accepts formula fields
class Resp(object):
    def __init__(self, status, body, headers=None):
        self.status, self._b, self.headers = status, json.dumps(body).encode(), headers or {}
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Fake(object):
    def __init__(self):
        self.reqs, self.n, self.fields, self.types, self.primary, self.refuse_types = [], 0, {}, {}, {}, set()
    def open(self, req, timeout=None):
        body = json.loads(req.data) if req.data else None
        m, full = req.get_method(), req.full_url
        self.reqs.append((m, full, body)); u = full.split("?")[0]; self.n += 1
        if m == "POST" and u.endswith("/record-types"):
            rid = "RT%d" % self.n; self.types[rid] = body["displayName"]
            self.fields[rid] = [{"id": "DEF%d_%d" % (self.n, i), "displayName": d, "type": "text"} for i, d in enumerate(["Name", "Description", "Start Date", "End Date", "Status"])]
            self.primary[rid] = "DEF%d_0" % self.n
            return Resp(201, {"id": rid, "displayName": body["displayName"]})
        if m == "GET" and "/record-types/" in u and not u.endswith("/fields"):
            rid = u.split("/record-types/")[1]
            return Resp(200, {"id": rid, "displayName": self.types[rid], "primaryFieldId": self.primary[rid], "primaryWorkspaceId": "wsDev"})
        if m == "PATCH" and "/record-types/" in u:
            self.primary[u.split("/record-types/")[1]] = body["primaryFieldId"]; return Resp(200, {})
        if m == "DELETE" and "/v2/fields/" in u:
            fid = u.split("/v2/fields/")[1]
            for rid in self.fields: self.fields[rid] = [f for f in self.fields[rid] if f["id"] != fid]
            return Resp(204, {})
        if m == "POST" and u.endswith("/fields"):
            rt = u.split("/record-types/")[1].split("/")[0]
            if body["type"] in self.refuse_types:
                raise urllib.error.HTTPError(full, 400, "bad", {}, io.BytesIO(json.dumps({"code": "INVALID_FIELD_TYPE", "message": "no"}).encode()))
            row = {"id": "FLD%d" % self.n, "displayName": body["displayName"], "type": body["type"], "options": [{"displayName": o["displayName"], "name": "key" + o["displayName"]} for o in body.get("options", [])]}
            self.fields.setdefault(rt, []).append(row)
            return Resp(201, row)
        if m == "GET" and u.endswith("/fields"):
            return Resp(200, {"content": self.fields.get(u.split("/record-types/")[1].split("/")[0], []), "cursor": {"hasMore": False}})
        if m == "POST" and u.endswith("/records/bulk"):
            return Resp(201, {"status": "success", "results": [{"requestIndex": i, "id": "REC%d_%d" % (self.n, i)} for i, _ in enumerate(body)]})
        return Resp(404, {})
    def field_posts(self): return [x for x in self.reqs if x[0] == "POST" and x[1].split("?")[0].endswith("/fields")]


tA = [("tA", "Alpha"), ("tB", "Beta")]
fA = [("tA", "fldAStart", "Start Date", "date", {}), ("tA", "fldAEnd", "End Date", "date", {}), ("tA", "fldAName", "Order Name", "singleLineText", {}), ("tA", "fldAQty", "Qty", "number", {}),
      ("tA", "fldAPrice", "Price", "number", {}), ("tA", "fldATags", "Tags", "multipleSelects", {}), ("tA", "fldALink", "To Beta", "multipleRecordLinks", {"to": "tB"}),
      ("tA", "fldARoll", "Beta Total", "rollup", {"link": "fldALink", "src": "fldBAmt", "rt": "number"}),
      ("tA", "fldAF1", "Days", "formula", {"f": "DATETIME_DIFF({fldAEnd}, {fldAStart}, 'days')", "rt": "number"}),
      ("tA", "fldAF2", "A Twice", "formula", {"f": "{fldAF1} * 2", "rt": "number"}),                      # sorts before Days, must be made after it
      ("tA", "fldAF3", "Blank Check", "formula", {"f": 'IF({fldAName} = BLANK(), "yes", "no")', "rt": "singleLineText"}),
      ("tA", "fldAF4", "Sum Percent", "formula", {"f": "({fldAQty} + {fldAPrice}) * 0.015", "rt": "number"}),
      ("tA", "fldAF5", "Tag Text", "formula", {"f": '{fldATags} & "x"', "rt": "singleLineText"}),         # reads a multi-select: PRE- text
      ("tA", "fldAF6", "Beta Plus", "formula", {"f": "{fldARoll} + 1", "rt": "number"}),                  # reads a rollup of Beta: waits
      ("tB", "fldBAmt", "Amount", "number", {}), ("tB", "fldBN", "Beta Name", "singleLineText", {})]
dbA = os.path.join(tmp, "run.db")
cA = make_db(dbA, tA, fA); cA.close()
cfgp = os.path.join(tmp, "config.json")
json.dump({"token": "patFAKEairtable.0000000000000000000000", "workfront": {"planning": {"dev": {"base_url": "https://acme.my.workfront.com", "bearer_token": TOKEN, "workspace_id": "wsDev"}}}}, open(cfgp, "w"))
extract = os.path.join(tmp, "extract")


def go(fk, table):
    conn = sqlite3.connect(dbA); conn.row_factory = sqlite3.Row
    ctx = tl.planning_context(cfgp, em.STAGE, True)
    tr = tl.transport(ctx, opener=fk)
    calls = em.CallLog(); tr.http.recorder = calls
    try:
        return em.run_table(conn, BASE, table, ctx, tr, extract, calls)
    finally:
        conn.commit(); conn.close()

c0 = sqlite3.connect(dbA); c0.row_factory = sqlite3.Row
pre0 = em.formula_precheck(c0, BASE, "tA", "dev", "wsDev"); c0.close()
check("the pre-check before any run: 4 ready, 1 waiting for Beta, 1 blocked (a multi-select)", pre0["counts"]["ready"] == 4 and pre0["counts"]["waiting"] == 1 and pre0["counts"]["blocked"] == 1
      and pre0["waiting_on"][0]["table_id"] == "tB" and [o["table_id"] for o in pre0["order"]] == ["tA", "tB", "tA"], pre0)
fk = Fake()
r = go(fk, "tA")
by = {x["field_name"]: x for x in r["fields"]}
posts = fk.field_posts()
names = [p[2]["displayName"] for p in posts]
fpost = {p[2]["displayName"]: p[2] for p in posts if p[2]["type"] == "formula"}
check("Days is created as a real formula with formulaOptions", by["Days"]["outcome"] == "clean" and by["Days"]["final_type"] == "formula"
      and fpost["Days"]["formulaOptions"] == {"formula": "DATEDIFF({Start Date},{End Date})", "returnType": "NUMBER"}, fpost.get("Days"))
check("the formula uses the Planning names of the fields, in braces", fpost["Blank Check"]["formulaOptions"]["formula"] == 'IF(ISBLANK({Order Name}),"yes","no")' and fpost["Sum Percent"]["formulaOptions"]["formula"] == "({Qty}+{Price})*0.015", fpost)
check("a formula is made after every ordinary field, and after the formula it reads", names.index("Days") < names.index("A Twice") and all(names.index(n) < names.index("Days") for n in ("Start Date", "End Date", "Order Name", "Qty", "Price"))
      and fpost["A Twice"]["formulaOptions"]["formula"] == "{Days}*2", names)
check("a formula over a multi-select is stored as PRE- text with the reason, and no formula was attempted for it", by["Tag Text"]["outcome"] == "pre-converted" and by["Tag Text"]["final_name"] == "PRE-Tag Text"
      and "multi-select" in by["Tag Text"]["reason"] and "Tag Text" not in fpost and by["Tag Text"]["attempt1"] == "not attempted", by["Tag Text"])
check("a formula that reads another table's field is held back: not created, not stored as text", by["Beta Plus"]["outcome"] == "skipped" and by["Beta Plus"]["waiting_for"] == ["Beta"]
      and "Beta Plus" not in names and "PRE-Beta Plus" not in names and "Run that table first" in by["Beta Plus"]["reason"], by["Beta Plus"])
check("the result counts the formulas and says which table to run first", r["counts"]["formulas_built"] == 4 and r["counts"]["formulas_held_back"] == 1 and any("Beta (1)" in n for n in r["notes"]), (r["counts"], r["notes"]))
check("the result lists the fields in the schema's order, not the order they were made", [x["field_name"] for x in r["fields"]] == sorted([x["field_name"] for x in r["fields"]], key=str.lower))
fk2 = Fake()
rB = go(fk2, "tB")
check("running Beta creates its fields", all(x["outcome"] == "clean" for x in rB["fields"]), [(x["field_name"], x["outcome"]) for x in rB["fields"]])
cc = sqlite3.connect(dbA); cc.row_factory = sqlite3.Row
pre = em.formula_precheck(cc, BASE, "tA", "dev", "wsDev")
check("after Beta is run the pre-check shows Beta Plus ready", pre["counts"]["ready"] == 1 and pre["counts"]["created"] == 4, pre["counts"])
cc.close()
fk3 = Fake()
fk3.types, fk3.fields, fk3.primary = dict(fk.types), {k: [dict(f) for f in v] for k, v in fk.fields.items()}, dict(fk.primary)       # Planning still holds what the first run made
r3 = go(fk3, "tA")
by3 = {x["field_name"]: x for x in r3["fields"]}
made3 = [p[2]["displayName"] for p in fk3.field_posts()]
check("a second run of Alpha builds the held-back formula and nothing else twice", by3["Beta Plus"]["outcome"] == "clean" and by3["Beta Plus"]["final_type"] == "formula" and made3 == ["Beta Plus"], made3)
check("the held-back formula reads the rollup's Planning name", [p[2] for p in fk3.field_posts()][0]["formulaOptions"]["formula"] == "{Beta Total}+1", fk3.field_posts())

# a refused formula falls back to PRE- text, as before
fk4 = Fake(); fk4.refuse_types = {"formula"}
dbA2 = os.path.join(tmp, "run2.db"); dbA_save, dbA = dbA, dbA2
cA2 = make_db(dbA2, tA, fA); cA2.close()
r4 = go(fk4, "tA")
b4 = {x["field_name"]: x for x in r4["fields"]}
check("when Planning refuses a formula it becomes PRE- text with both attempts recorded", b4["Days"]["outcome"] == "pre-converted" and b4["Days"]["attempt1"] == "refused" and b4["Days"]["attempt2"] == "created" and b4["Days"]["final_name"] == "PRE-Days", b4["Days"])
check("no value is sent to a real formula field (it is not in the loadable fields)", not any(isinstance(x[2], list) and any("Days" in json.dumps(e) for e in x[2]) for x in fk.reqs))

print("ALL PASSED" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
