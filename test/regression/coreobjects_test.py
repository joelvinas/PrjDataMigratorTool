"""Tests for coreobjects.py, the Core transport's object calls and `loader.py core-objects`: one Core object per record under the parent decided for its table. A fake opener stands in
for Workfront; fabricated records and fake secrets only."""
import io, json, os, sqlite3, sys, tempfile, urllib.error
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
import coreobjects as co, coretargets as tg, coretransport as ct, loadconfig as lc, loader, recordmap as rm, workfronthttp as wh
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

KEY = "fakeCOREkey0123456789"
GUID = "0123456789abcdef0123456789abcdef"
GUID2 = "fedcba9876543210fedcba9876543210"
creds = lc.credentials({"workfront": {"core": {"sandbox1": {"base_url": "https://acme.sb01.workfront.com", "api_path": "/attask/api/v21.0", "api_key": KEY}}}}, "core", "sandbox1")

class Resp(object):
    def __init__(self, status, body): self.status, self._b = status, body if isinstance(body, bytes) else json.dumps(body).encode()
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False
class Fake(object):
    def __init__(self, fail_name=None):
        self.reqs, self.n, self.fail_name = [], 0, fail_name
    def open(self, req, timeout=None):
        body = json.loads(req.data) if req.data else None
        self.reqs.append((req.get_method(), req.full_url.split("workfront.com", 1)[1], dict(req.header_items()), body))
        if self.fail_name and body and body.get("name") == self.fail_name:
            raise urllib.error.HTTPError(req.full_url, 400, "x", {}, io.BytesIO(("{\"error\":\"bad Authorization: Bearer abc.def.ghi apiKey=%s\"}" % KEY).encode()))
        self.n += 1
        return Resp(200, {"data": {"ID": "%032x" % self.n, "name": body.get("name")}})
def real(fake): return ct.CoreTransport(wh.Http(creds, opener=fake, sleep=lambda s: None))

# ---- the transport: routes and parent properties
t = real(Fake())
rq = t.object_request
check("a project goes to /proj with its program or portfolio, a request to /optask with its queue project, a task to /task with its project or parent task",
      rq("PROJ", "A", "PRGM", GUID) == ("POST", "/proj", {"name": "A", "programID": GUID}) and rq("PROJ", "A", "PORT", GUID)[2] == {"name": "A", "portfolioID": GUID}
      and rq("OPTASK", "A", "PROJ", GUID) == ("POST", "/optask", {"name": "A", "projectID": GUID}) and rq("TASK", "A", "PROJ", GUID) == ("POST", "/task", {"name": "A", "projectID": GUID})
      and rq("TASK", "A", "TASK", GUID)[2] == {"name": "A", "parentID": GUID})
check("a project may have no parent", rq("PROJ", "A", None, None)[2] == {"name": "A"})
def err(fn, *a):
    try:
        fn(*a); return ""
    except ValueError as e:
        return str(e)
check("bad requests are refused before any call", "cannot go under" in err(rq, "OPTASK", "A", "PRGM", GUID) and "needs a name" in err(rq, "TASK", " ", "PROJ", GUID) and "limited to 255" in err(rq, "TASK", "x" * 256, "PROJ", GUID)
      and "not created" in err(rq, "PORT", "A", None, None) and "cannot go under" in err(rq, "TASK", "A", "PROJ", ""))
f = Fake(); t = real(f)
new = t.create_object("OPTASK", "Order one", "PROJ", GUID)
check("an object is created with the apiKey header and its new ID is returned", new == "%032x" % 1 and f.reqs[0][1] == "/attask/api/v21.0/optask" and f.reqs[0][2].get("Apikey") == KEY and f.reqs[0][3] == {"name": "Order one", "projectID": GUID})

# ---- a database with decisions
tmp = tempfile.mkdtemp(prefix="coreobjects_")
dbp = os.path.join(tmp, "base.db")
c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row; c.execute("PRAGMA foreign_keys = ON")
c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
for tid, nm, prim in (("tReq", "Requests", "fldTitle"), ("tTsk", "Tasks", "fldName"), ("tNo", "Undecided", "fldX"), ("tPl", "Planning only", "fldY")):
    c.execute("INSERT INTO airtable_tables(table_id, base_id, table_name, primary_field_id) VALUES (?,?,?,?)", (tid, "appL", nm, prim))
    c.execute("INSERT INTO table_targets(table_id, base_id, target, decided_by) VALUES (?,?,?,?)", (tid, "appL", "planning_only" if tid == "tPl" else "planning_and_core", "JV"))
c.commit()
tg.set_object_type(c, "appL", "tReq", "request", "JV"); tg.set_parent(c, "appL", "tReq", "PROJ", GUID, "JV", "queue")
tg.set_object_type(c, "appL", "tTsk", "task", "JV"); tg.set_parent(c, "appL", "tTsk", "TASK", GUID2, "JV", "parent task")
c.close()
ex = os.path.join(tmp, "extract", "appL"); os.makedirs(ex)
def save(tid, recs):
    json.dump({"format": "airtable-table-extract/1", "base_id": "appL", "table_id": tid, "table_name": tid, "records": recs}, open(os.path.join(ex, tid + ".json"), "w"))
save("tReq", [{"id": "recR1", "fields": {"fldTitle": "Order one"}}, {"id": "recR2", "fields": {"fldTitle": "  "}}, {"id": "recR3", "fields": {"fldTitle": "Order three"}}])
save("tTsk", [{"id": "recT1", "fields": {"fldName": "Pack it"}}])
save("tNo", [{"id": "recN1", "fields": {"fldX": "never"}}])
cfgp = os.path.join(tmp, "config.json")
json.dump({"workfront": {"core": {"sandbox1": {"base_url": "https://acme.sb01.workfront.com", "api_path": "/attask/api/v21.0", "api_key": KEY},
                                  "production": {"base_url": "https://acme.my.workfront.com", "api_path": "/attask/api/v21.0", "api_key": KEY + "P"}}}}, open(cfgp, "w"))
args = lambda db, *x, out=None: ["core-objects", "--db", db, "--base", "appL", "--config", cfgp, "--extract-dir", os.path.join(tmp, "extract"), "--out", out or os.path.join(tmp, "o_" + os.path.basename(db))] + list(x)

fk = Fake()
res = loader.main(args(dbp), opener=fk)
check("without --send nothing is sent and the summary says so", fk.reqs == [] and "nothing was sent" in open(os.path.join(tmp, "o_base.db", "core_objects_summary.txt"), encoding="utf-8").read())
fk = Fake()
res = loader.main(args(dbp, "--send"), opener=fk)
posts = [(r[1].rsplit("/", 1)[1], r[3]) for r in fk.reqs]
check("each record becomes one object under its table's parent, requests first (table-name order), in record ID order",
      posts == [("optask", {"name": "Order one", "projectID": GUID}), ("optask", {"name": "Untitled recR2", "projectID": GUID}), ("optask", {"name": "Order three", "projectID": GUID}), ("task", {"name": "Pack it", "parentID": GUID2})], posts)
check("a table with no decision is skipped and a Planning-only table is not listed", [r[1] for r in res["rows"]] == ["Requests", "Tasks", "Undecided"] and "skipped" in res["rows"][2][6] and "never" not in json.dumps(posts))
conn = sqlite3.connect(dbp)
check("every object is mapped to its record with kind and Core ID, and marked done for the Core environment only", conn.execute("select count(*) from target_mappings where system='core' and environment='sandbox1' and workspace_key=''").fetchone()[0] == 4
      and conn.execute("select count(*) from record_pass_markers where pass='records' and environment='sandbox1'").fetchone()[0] == 4 and {r[0] for r in conn.execute("select target_kind from target_mappings")} == {"request", "task"})
check("no name or record value is in the database or the reports", not any("Order one" in str(v) or "Pack it" in str(v) for t_ in ("source_records", "target_mappings", "record_pass_markers") for row in conn.execute("select * from " + t_) for v in row)
      and all("Order one" not in open(os.path.join(tmp, "o_base.db", f), encoding="utf-8-sig").read() for f in os.listdir(os.path.join(tmp, "o_base.db"))))
conn.close()
fk = Fake()
loader.main(args(dbp, "--send"), opener=fk)
check("a rerun creates nothing: no object is created twice", fk.reqs == [])

# ---- a failure never stops the run and never shows a secret
import shutil
db2 = os.path.join(tmp, "two.db"); shutil.copy(os.path.join(tmp, "base.db"), db2)
c2 = sqlite3.connect(db2); c2.execute("delete from target_mappings"); c2.execute("delete from record_pass_markers"); c2.commit(); c2.close()
fk = Fake(fail_name="Order three")
res = loader.main(args(db2, "--send"), opener=fk)
fails = open(os.path.join(tmp, "o_two.db", "core_objects_failures.csv"), encoding="utf-8-sig").read()
check("a failing record is listed by Airtable ID with a scrubbed reason and the others still load", res["failures"][0][1] == "recR3" and len(res["failures"]) == 1 and KEY not in fails and "abc.def.ghi" not in fails and len(fk.reqs) == 4, fails)
fk = Fake()
loader.main(args(db2, "--send"), opener=fk)
check("the next run sends only the record that failed", [r[3]["name"] for r in fk.reqs] == ["Order three"])

# ---- guards
check("a real production run needs the exact phrase", "CREATE IN PRODUCTION" in (refused(args(dbp, "--send", "--core-env", "production"), opener=Fake()) or ""))
nocfg = os.path.join(tmp, "empty.json"); json.dump({"workfront": {}}, open(nocfg, "w"))
check("Core not configured is named", "not configured" in (refused(["core-objects", "--db", dbp, "--base", "appL", "--config", nocfg, "--send"], opener=Fake()) or ""))
sys.exit(0 if ok else 1)
