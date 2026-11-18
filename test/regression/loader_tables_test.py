"""Tests for `--table` (a run filter): the plan, dry run, run --send and records limited to chosen tables, dependency record types, refusals, and that the scope decisions are
untouched. Fabricated names and records; fake Workfront; no network."""
import base64, csv, json, os, shutil, sqlite3, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
import fieldnames_store as st, linkdirections as ld, loader
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:400])
def refused(argv, **k):
    try:
        loader.main(argv, **k); return None
    except SystemExit as e:
        return str(e.code)

TOKEN = "fakePLANtoken0123456789"
tmp = tempfile.mkdtemp(prefix="loader_tables_")
dbp = os.path.join(tmp, "base.db")
c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row
c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
for tid, nm in (("tA", "Orders"), ("tB", "Customers"), ("tC", "Notes"), ("tD", "Hidden")):
    c.execute("insert into airtable_tables(table_id, base_id, table_name) values (?,?,?)", (tid, "appL", nm))
F = [("fA1", "tA", "Orders", "Order Name", "singleLineText", None), ("fA4", "tA", "Orders", "Customer", "multipleRecordLinks", "tB"),
     ("fB1", "tB", "Customers", "Customer Name", "singleLineText", None), ("fB2", "tB", "Customers", "Note Link", "multipleRecordLinks", "tC"),
     ("fC1", "tC", "Notes", "Note Text", "singleLineText", None), ("fD1", "tD", "Hidden", "Secret Name", "singleLineText", None)]
for r in F:
    c.execute("insert into airtable_fields(base_id, table_id, table_name, field_id, field_name, data_type, linked_table_id, prefers_single_record_link, updated_at) values ('appL',?,?,?,?,?,?,1,'2026-01-01 10:00:00.000000')",
              (r[1], r[2], r[0], r[3], r[4], r[5]))
c.commit()
st.approve_tables(c, "appL", "JV", mode="unchanged"); st.approve_tables(c, "appL", "JV", mode="auto")
st.set_scope(c, "appL", "JV", [{"table_id": t, "decision": "in", "target": "planning_only"} for t in ("tA", "tB", "tC")] + [{"table_id": "tD", "decision": "out", "target": None, "reason": "not part of the rehearsal"}])
csvt = "field_id,old_name,new_name,new_label,old_dataType,new_dataType\r\n" + "".join("%s,%s,%s,%s,x,%s\r\n" % (r[0], r[3], r[3], r[3], "Link" if r[4] == "multipleRecordLinks" else "Text") for r in F[:-1])
st.import_names(c, "appL", "JV", csv_base64=base64.b64encode(csvt.encode()).decode())
st.approve_fields(c, "appL", "JV", mode="pass_and_auto")
import typesfixture; typesfixture.fill_defaults(c, "appL")      # Planning type and Core dataType and displayType from the default mapping
ld.sync(c, [r for r in c.execute("select * from airtable_fields")])
ld.decide(c, "JV", "fA4", "many_to_one", None, "orders belong to one customer")
ld.decide(c, "JV", "fB2", "many_to_one", None, "customers have one note")
SCOPE_SQL = "select * from table_scope order by 1, 2"
has_scope = "table_scope" in {r[0] for r in c.execute("select name from sqlite_master")}
before = [tuple(r) for r in c.execute(SCOPE_SQL)] if has_scope else None
c.close()
cfgp = os.path.join(tmp, "config.json")
json.dump({"workfront": {"planning": {"base_url": "https://acme.my.workfront.com", "bearer_token": TOKEN, "workspace_id": "wsDev", "record_batch_size": 2}}}, open(cfgp, "w"))
ex = os.path.join(tmp, "extract", "appL"); os.makedirs(ex)
def save(tid, recs):
    json.dump({"format": "airtable-table-extract/1", "base_id": "appL", "table_id": tid, "table_name": tid, "records": recs}, open(os.path.join(ex, tid + ".json"), "w"))
save("tA", [{"id": "recA1", "fields": {"fA1": "Order one", "fA4": ["recB1"]}}])
save("tB", [{"id": "recB1", "fields": {"fB1": "Acme", "fB2": ["recC1"]}}])
save("tC", [{"id": "recC1", "fields": {"fC1": "Note one"}}])

class Resp(object):
    def __init__(self, status, body): self.status, self._b = status, json.dumps(body).encode()
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False
class Fake(object):
    def __init__(self): self.reqs, self.n, self.r = [], 0, 0
    def open(self, req, timeout=None):
        body = json.loads(req.data) if req.data else None
        self.reqs.append((req.get_method(), req.full_url, body))
        u = req.full_url; self.n += 1
        if u.endswith("/record-types"): return Resp(201, {"id": "RT%d" % self.n, "displayName": body["displayName"]})
        if u.endswith("/fields"): return Resp(201, {"id": "FLD%d" % self.n, "displayName": body["displayName"]})
        if u.endswith("/records/bulk"):
            out = []
            for i, _ in enumerate(body):
                self.r += 1; out.append({"requestIndex": i, "id": "REC%03d" % self.r})
            return Resp(201, {"status": "ok", "total": len(out), "results": out})
        if "/v2/records/" in u and req.get_method() == "PATCH": return Resp(200, {})
        return Resp(404, {})
    def names(self): return [b["displayName"] for m, u, b in self.reqs if m == "POST" and (u.endswith("/record-types") or u.endswith("/fields"))]

def plan_rows(tables, name):
    out = os.path.join(tmp, name)
    loader.main(["plan", "--db", dbp, "--base", "appL", "--out", out] + sum([["--table", t] for t in tables], []))
    rows = {r["temp_id"]: r for r in csv.DictReader(open(os.path.join(out, "load_plan.csv"), encoding="utf-8-sig", newline=""))}
    return rows, open(os.path.join(out, "load_plan_summary.txt"), encoding="utf-8-sig").read()

full, _ = plan_rows([], "full")
one, _ = plan_rows(["tC"], "one")
check("no --table plans every in-scope table", {"P-T-tA", "P-T-tB", "P-T-tC"} <= set(full) and "P-T-tD" not in full)
check("a table with no links plans only its own items", set(one) == {t for t in full if t.endswith("tC") or t.endswith("fC1")}, sorted(one))
two, summ = plan_rows(["tB"], "two")
check("a chosen table's link target is included as a record type ONLY", "P-T-tC" in two and "P-F-fC1" not in two and "P-T-tA" not in two and "P-F-fB1" in two and "P-F-fB2" in two, sorted(two))
check("the summary names the filter and the dependency", "Limited to table(s): tB" in summ and "created only because" in summ and "tC" in summ.split("created only because")[1].splitlines()[0], summ)
both, _ = plan_rows(["tB", "tA", "tA"], "both")
check("repeated and unordered --table values give the same plan as sorted ones", list(both) == list(plan_rows(["tA", "tB"], "both2")[0]) and "P-F-fC1" not in both)
check("a filtered plan is the same every time", list(two) == list(plan_rows(["tB"], "two_again")[0]))
check("an unknown table is refused with its ID", "tZ" in (refused(["plan", "--db", dbp, "--base", "appL", "--out", os.path.join(tmp, "x"), "--table", "tZ"]) or ""))
check("an out-of-scope table is refused", "tD" in (refused(["plan", "--db", dbp, "--base", "appL", "--out", os.path.join(tmp, "x"), "--table", "tD"]) or ""))
res = loader.main(["dry-run", "--db", dbp, "--base", "appL", "--out", os.path.join(tmp, "dry"), "--table", "tC", "--env", "dev"])
check("dry-run honours --table", {r["temp_id"] for r in res["results"]} == set(one), sorted(r["temp_id"] for r in res["results"]))

# ---- run --send and records for one table
fk = Fake()
db1 = os.path.join(tmp, "run.db"); shutil.copy(dbp, db1)
loader.main(["run", "--db", db1, "--base", "appL", "--out", os.path.join(tmp, "o1"), "--config", cfgp, "--send", "--only", "planning", "--table", "tB"], opener=fk)
check("run --send creates the chosen table, its fields, and the dependency record type only", fk.names().count("Customers") == 1 and fk.names().count("Notes") == 1 and "Orders" not in fk.names()
      and "Note Text" not in fk.names() and "Customer Name" in fk.names(), fk.names())
fk = Fake()
res = loader.main(["records", "--db", db1, "--base", "appL", "--out", os.path.join(tmp, "r1"), "--config", cfgp, "--extract-dir", os.path.join(tmp, "extract"), "--send", "--table", "tB"], opener=fk)
check("records --table loads only the chosen table's records, not the dependency's", sum(len(b) for m, u, b in fk.reqs if u.endswith("/records/bulk")) == 1 and {r[0] for r in res["rows"]} == {"tB"}, res["rows"])
check("links to a table not in the run stay on hold with a plain reason, and nothing is patched", any("stay on hold" in r[-1] and "tC" in r[-1] for r in res["rows"]) and not [x for x in fk.reqs if x[0] == "PATCH"], res["rows"])
conn = sqlite3.connect(db1)
check("the dependency's records were not loaded", conn.execute("select count(*) from target_mappings").fetchone()[0] == 1)
check("scope decisions are unchanged by a filtered run", before is None or [tuple(r) for r in conn.execute(SCOPE_SQL)] == before)
conn.close()
check("run without --send still sends nothing", "Nothing was sent" in (refused(["run", "--db", db1, "--base", "appL", "--table", "tB"]) or ""))
sys.exit(0 if ok else 1)
