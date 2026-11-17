"""End-to-end test of `loader.py records` (the record load) against a FAKE Workfront: the field load first, then the records pass and the links pass, a dry run, reruns, a failure,
the reports and secrets. Fabricated names, records and fake secrets only; no network."""
import base64, io, json, os, sqlite3, sys, tempfile, urllib.error
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

PLAN_TOKEN = "fakePLANtoken0123456789"
tmp = tempfile.mkdtemp(prefix="recordload_cli_")
dbp = os.path.join(tmp, "base.db")
c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row
c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
for tid, nm in (("tA", "Orders"), ("tB", "Customers")):
    c.execute("insert into airtable_tables(table_id, base_id, table_name) values (?,?,?)", (tid, "appL", nm))
CH = json.dumps([{"id": "selA", "name": "Open", "color": "x"}, {"id": "selB", "name": "Closed", "color": "y"}])
F = [("fA1", "tA", "Orders", "Order Name", "singleLineText", None, None, None, None), ("fA2", "tA", "Orders", "Status", "singleSelect", None, CH, None, None),
     ("fA4", "tA", "Orders", "Customer", "multipleRecordLinks", "tB", None, 1, None), ("fA5", "tA", "Orders", "Amount", "number", None, None, None, 2),
     ("fB1", "tB", "Customers", "Customer Name", "singleLineText", None, None, None, None)]
for r in F:
    c.execute("insert into airtable_fields(base_id, table_id, table_name, field_id, field_name, data_type, linked_table_id, choices_json, prefers_single_record_link, number_precision, updated_at) values ('appL',?,?,?,?,?,?,?,?,?,'2026-01-01 10:00:00.000000')", r[1:3] + (r[0],) + r[3:])
c.commit()
st.approve_tables(c, "appL", "JV", mode="unchanged"); st.approve_tables(c, "appL", "JV", mode="auto")
st.set_scope(c, "appL", "JV", [{"table_id": "tA", "decision": "in", "target": "planning_only"}, {"table_id": "tB", "decision": "in", "target": "planning_only"}])
csvt = ("field_id,old_name,new_name,new_label,old_dataType,new_dataType\r\nfA1,Order Name,Order Name,Order Name,x,Text\r\nfA2,Status,Status,Status,x,Single select\r\nfA4,Customer,Customer,Customer,x,Link\r\n"
        "fA5,Amount,Amount,Amount,x,Number\r\nfB1,Customer Name,Customer Name,Customer Name,x,Text\r\n")
st.import_names(c, "appL", "JV", csv_base64=base64.b64encode(csvt.encode()).decode())
st.approve_fields(c, "appL", "JV", mode="pass_and_auto")
ld.sync(c, [r for r in c.execute("select * from airtable_fields")])
ld.decide(c, "JV", "fA4", "many_to_one", None, "orders belong to one customer")
c.close()

cfgp = os.path.join(tmp, "config.json")
json.dump({"workfront": {"planning": {"base_url": "https://acme.my.workfront.com", "bearer_token": PLAN_TOKEN, "workspace_id": "wsDev", "record_batch_size": 2}}}, open(cfgp, "w"))

# ---- the saved record files (fabricated)
ex = os.path.join(tmp, "extract", "appL"); os.makedirs(ex)
def save(tid, recs):
    json.dump({"format": "airtable-table-extract/1", "base_id": "appL", "table_id": tid, "table_name": tid, "records": recs}, open(os.path.join(ex, tid + ".json"), "w"))
save("tA", [{"id": "recA1", "fields": {"fA1": "Order one", "fA2": "Open", "fA4": ["recB1"], "fA5": 12.5}}, {"id": "recA2", "fields": {"fA1": "Order two", "fA2": "Closed", "fA4": ["recB2"]}},
            {"id": "recA3", "fields": {"fA1": "Order three"}}])
save("tB", [{"id": "recB1", "fields": {"fB1": "Acme"}}, {"id": "recB2", "fields": {"fB1": "Globex"}}])

class Resp(object):
    def __init__(self, status, body): self.status, self._b = status, body if isinstance(body, bytes) else json.dumps(body).encode()
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False
class Fake(object):
    def __init__(self, fail_title=None):
        self.reqs, self.n, self.r, self.fail_title = [], 0, 0, fail_title
    def open(self, req, timeout=None):
        body = json.loads(req.data) if req.data else None
        self.reqs.append((req.get_method(), req.full_url, dict(req.header_items()), body))
        u, name = req.full_url, (body or {}).get("displayName") if isinstance(body, dict) else None
        self.n += 1
        if u.endswith("/record-types"): return Resp(201, {"id": "RT%d" % self.n, "displayName": name})
        if u.endswith("/fields"):
            return Resp(201, {"id": "FLD%d" % self.n, "displayName": name, "options": [{"displayName": o["displayName"], "name": "k%d" % i} for i, o in enumerate(body.get("options") or [])]})
        if u.endswith("/records/bulk"):
            if self.fail_title and any(self.fail_title in json.dumps(b) for b in body):
                raise urllib.error.HTTPError(u, 500, "x", {}, io.BytesIO(("{\"error\":\"boom Authorization: Bearer abc.def.ghi token=%s\"}" % PLAN_TOKEN).encode()))
            out = []
            for i, _ in enumerate(body):
                self.r += 1; out.append({"requestIndex": i, "id": "REC%03d" % self.r})
            return Resp(201, {"status": "ok", "total": len(out), "results": out})
        if "/v2/records/" in u and req.get_method() == "PATCH": return Resp(200, {})
        return Resp(404, b"no such route")
    def of(self, tail): return [x for x in self.reqs if x[1].endswith(tail) or tail in x[1]]

def prepare(name):
    import shutil
    p = os.path.join(tmp, name + ".db"); shutil.copy(dbp, p)
    loader.main(["run", "--db", p, "--base", "appL", "--out", os.path.join(tmp, "o_" + name), "--config", cfgp, "--send", "--only", "planning"], opener=Fake())
    return p
def records(db, fake, *extra, out=None):
    out = out or os.path.join(tmp, "r_" + os.path.basename(db))
    return loader.main(["records", "--db", db, "--base", "appL", "--out", out, "--config", cfgp, "--extract-dir", os.path.join(tmp, "extract")] + list(extra), opener=fake), out

db1 = prepare("one")
fk = Fake()
res, out = records(db1, fk)
check("without --send nothing is sent and the summary says so", fk.reqs == [] and "nothing was sent" in open(os.path.join(out, "record_load_summary.txt"), encoding="utf-8").read())
fk = Fake()
res, out = records(db1, fk, "--send")
bulk = fk.of("/records/bulk")
check("the records pass sends batches of the configured size, 2 at a time, in table ID order", [len(b[3]) for b in bulk] == [2, 1, 2], [len(b[3]) for b in bulk])
check("each call carries the Bearer token and no link field", all(b[2].get("Authorization") == "Bearer " + PLAN_TOKEN for b in bulk) and all(len(rec["data"]) <= 3 for b in bulk for rec in b[3]))
check("the links pass sets the customer on the two orders that have one, and nothing on the third", len(fk.of("PATCH") + [x for x in fk.reqs if x[0] == "PATCH"]) >= 2 and len([x for x in fk.reqs if x[0] == "PATCH"]) == 2, [x[:2] for x in fk.reqs if x[0] == "PATCH"])
rep = open(os.path.join(out, "record_load.csv"), encoding="utf-8-sig").read()
check("the report holds table IDs and counts only", "tA" in rep and "tB" in rep and "Order one" not in rep and "Acme" not in rep)
check("the summary and reports never hold the token or a record value", all(PLAN_TOKEN not in open(os.path.join(out, f), encoding="utf-8-sig").read() and "Globex" not in open(os.path.join(out, f), encoding="utf-8-sig").read() for f in os.listdir(out)))
conn = sqlite3.connect(db1)
check("every record is mapped and marked; no record value is in the database", conn.execute("select count(*) from target_mappings").fetchone()[0] == 5 and conn.execute("select count(*) from record_pass_markers where pass='records'").fetchone()[0] == 5
      and conn.execute("select count(*) from record_pass_markers where pass='links'").fetchone()[0] == 3
      and not any("Acme" in str(v) or "Order one" in str(v) for t in ("source_records", "target_mappings", "record_pass_markers", "load_id_map") for row in conn.execute("select * from " + t) for v in row))
conn.close()
fk = Fake()
records(db1, fk, "--send")
check("a rerun sends nothing: no record is created twice", fk.of("/records/bulk") == [] and [x for x in fk.reqs if x[0] == "PATCH"] == [])

# ---- a failure
db2 = prepare("two")
fk = Fake(fail_title="Order two")
res, out = records(db2, fk, "--send", "--pass", "records")
fails = open(os.path.join(out, "record_load_failures.csv"), encoding="utf-8-sig").read()
check("a failing record is listed by Airtable ID with a scrubbed reason; the others load", "recA2" in fails and PLAN_TOKEN not in fails and "abc.def.ghi" not in fails and len(res["failures"]) == 1, fails)
fk = Fake()
res, out = records(db2, fk, "--send", "--pass", "records")
check("the next run sends only the record that failed", sum(len(b[3]) for b in fk.of("/records/bulk")) == 1)

# ---- guards
check("a missing database is refused", "No such database" in (refused(["records", "--db", os.path.join(tmp, "nope.db"), "--base", "appL", "--config", cfgp, "--send"], opener=Fake()) or ""))
nocfg = os.path.join(tmp, "empty.json"); json.dump({"workfront": {}}, open(nocfg, "w"))
check("Planning not configured is named, and nothing is sent", "Planning is not configured" in (refused(["records", "--db", db1, "--base", "appL", "--config", nocfg, "--send"], opener=Fake()) or ""))
sys.exit(0 if ok else 1)
