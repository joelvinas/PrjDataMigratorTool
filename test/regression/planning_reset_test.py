"""Tests for `loader.py planning-reset` (forget every Planning ID for one stage and Workspace) against a FAKE Workfront. Fabricated names; fake secrets; no network."""
import io, json, os, sqlite3, sys, tempfile, urllib.error
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
import loader, planningreset as pr
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
tmp = tempfile.mkdtemp(prefix="plreset_")
dbp = os.path.join(tmp, "base.db")
c = sqlite3.connect(dbp)
c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
for tid, nm in (("tA", "Orders"), ("tB", "Customers")):
    c.execute("insert into airtable_tables(table_id, base_id, table_name) values (?,?,?)", (tid, "appL", nm))
def idrow(temp, env, ws, system, kind, table, wid, name="n", status="created"):
    c.execute("insert into load_id_map(temp_id, environment, workspace_id, workspace_key, system, kind, base_id, table_id, name, workfront_id, status) values (?,?,?,?,?,?,?,?,?,?,?)",
              (temp, env, ws or None, ws or "", system, kind, "appL", table, name, wid, status))
# Planning, dev, wsDev: the rows a reset must remove
idrow("P-T-tA", "dev", "wsDev", "planning", "record_type", "tA", "RT1", "Orders")
idrow("P-T-tB", "dev", "wsDev", "planning", "record_type", "tB", "RT2", "Customers")
idrow("P-F-fA1", "dev", "wsDev", "planning", "field", "tA", "FLD1")
idrow("P-F-fB1", "dev", "wsDev", "planning", "field", "tB", "FLD2", status="failed")
# rows a reset must keep: the same temp ID in another Workspace, another stage, and Core
idrow("P-T-tA", "dev", "wsOther", "planning", "record_type", "tA", "RT9")
idrow("P-T-tA", "uat", "wsDev", "planning", "record_type", "tA", "RT8")
idrow("C-T-tA", "production", None, "core", "record_type", "tA", "CORE1")
idrow("C-F-fA1", "sandbox1", None, "core", "field", "tA", "CORE2")
uids = {}
for tid, n in (("tA", 3), ("tB", 2)):
    for i in range(n):
        cur = c.execute("insert into source_records(base_id, table_id, record_id) values ('appL',?,?)", (tid, "rec%s%d" % (tid, i)))
        uids[(tid, i)] = cur.lastrowid
def mapping(uid, system, env, ws, target):
    c.execute("insert into target_mappings(source_record_uid, system, environment, workspace_key, target_kind, target_record_id) values (?,?,?,?,?,?)", (uid, system, env, ws, "record" if system == "planning" else "project", target))
def mark(uid, p, env, ws):
    c.execute("insert into record_pass_markers(source_record_uid, pass, environment, workspace_key) values (?,?,?,?)", (uid, p, env, ws))
for (tid, i), uid in sorted(uids.items()):
    mapping(uid, "planning", "dev", "wsDev", "R%s%d" % (tid, i)); mark(uid, "records", "dev", "wsDev")
    mapping(uid, "planning", "dev", "wsOther", "O%s%d" % (tid, i)); mark(uid, "records", "dev", "wsOther")
    mapping(uid, "planning", "uat", "wsDev", "U%s%d" % (tid, i)); mark(uid, "records", "uat", "wsDev")
    mapping(uid, "core", "production", "", "C%s%d" % (tid, i)); mark(uid, "core_backfill", "production", "")
    mapping(uid, "core", "sandbox1", "", "S%s%d" % (tid, i)); mark(uid, "core_backfill", "sandbox1", "")
c.commit()
PLAN_ONLY = {"load_id_map", "target_mappings", "record_pass_markers"}
def snapshot(path):
    """Every table except the three the reset may change, so 'untouched' is proven for ALL the others (decisions, schema, profiling, source records)."""
    k = sqlite3.connect(path)
    out = {}
    for (t,) in k.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name").fetchall():
        if t not in PLAN_ONLY:
            out[t] = sorted(map(repr, k.execute('select * from "%s"' % t)))
    k.close(); return out
def core_rows(path):
    k = sqlite3.connect(path)
    r = (sorted(k.execute("select * from load_id_map where system='core'")), sorted(k.execute("select * from target_mappings where system='core'")), sorted(k.execute("select * from record_pass_markers where workspace_key=''")))
    k.close(); return r
c.close()
cfgp = os.path.join(tmp, "config.json")
json.dump({"workfront": {"planning": {"dev": {"base_url": "https://acme.my.workfront.com", "bearer_token": TOKEN, "workspace_id": "wsDev"}}}}, open(cfgp, "w"))

class Resp(object):
    def __init__(self, status, body): self.status, self._b = status, json.dumps(body).encode()
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False
class Fake(object):
    """Lists the Workspace's record types: RT1 still exists, RT2 was deleted by hand, RZ is somebody else's."""
    def __init__(self, fail=False): self.reqs, self.fail = [], fail
    def open(self, req, timeout=None):
        self.reqs.append((req.get_method(), req.full_url))
        if self.fail:
            raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b'{"code":"UNAUTHORIZED"}'))
        if req.get_method() == "GET" and "/workspaces/wsDev/record-types" in req.full_url:
            return Resp(200, {"content": [{"id": "RT1", "displayName": "Orders"}, {"id": "RZ", "displayName": "Other team"}], "cursor": {"hasMore": False}})
        return Resp(404, {})
PH = "RESET PLANNING IN DEV"
def run(*extra, fk=None, cfg=cfgp, db=dbp, out="o"):
    return loader.main(["planning-reset", "--db", db, "--out", os.path.join(tmp, out), "--config", cfg] + list(extra), opener=fk or Fake())
def summary(out):
    return open(os.path.join(tmp, out, "planning_reset_summary.txt"), encoding="utf-8-sig").read()

# ---- dry run
before = snapshot(dbp)
raw0 = open(dbp, "rb").read()
fk = Fake()
res = run(fk=fk, out="dry")
text = summary("dry")
check("a dry run counts only Planning rows of this stage and Workspace", res["counts"]["totals"] == {"load_id_map": 4, "target_mappings": 5, "record_pass_markers": 5}, res["counts"]["totals"])
check("counts are given by table and kind", {(r["table_id"], r["kind"], r["status"]): r["count"] for r in res["counts"]["load_id_map"]} == {("tA", "field", "created"): 1, ("tA", "record_type", "created"): 1, ("tB", "field", "failed"): 1, ("tB", "record_type", "created"): 1}, res["counts"]["load_id_map"])
check("a dry run changes nothing and says so", open(dbp, "rb").read() == raw0 and "dry run: nothing was changed" in text and PH in text)
check("a dry run only READS Workfront (GET, no write)", fk.reqs and all(m == "GET" for m, _ in fk.reqs), fk.reqs)
check("it lists the record types this tool created that still exist, and those already gone", [r["id"] for r in res["listing"]["existing"]] == ["RT1"] and [r["id"] for r in res["listing"]["gone"]] == ["RT2"]
      and res["listing"]["unrecorded_in_workspace"] == 1 and "RT1" in text and "RT2" in text, res["listing"])
run(fk=Fake(fail=True), out="dry2")
check("a failing Workfront read does not stop the dry run and is said plainly", "could not be read" in summary("dry2") and "Planning reset for stage DEV" in summary("dry2"))
check("the output holds no token", TOKEN not in text and TOKEN not in summary("dry2"))

# ---- refusals
for why, argv in (("no phrase", ["--send"]), ("a wrong phrase", ["--send", "--confirm", "reset planning in dev"]), ("the uat phrase for dev", ["--send", "--confirm", "RESET PLANNING IN UAT"])):
    check("--send with %s is refused and changes nothing" % why, "exact phrase" in (refused(["planning-reset", "--db", dbp, "--config", cfgp] + argv, opener=Fake()) or "") and open(dbp, "rb").read() == raw0)
check("no config file and no --workspace-id is refused", "not found" in (refused(["planning-reset", "--db", dbp, "--config", os.path.join(tmp, "nothere.json")]) or ""))
json.dump({"workfront": {"planning": {}}}, open(os.path.join(tmp, "empty.json"), "w"))
check("a config with no Workspace and no --workspace-id is refused", "Workspace ID" in (refused(["planning-reset", "--db", dbp, "--config", os.path.join(tmp, "empty.json")]) or ""))
try:
    pr.counts(sqlite3.connect(dbp), "dev", ""); blank = False
except pr.PlanningResetError:
    blank = True
check("counts() refuses a blank Workspace (it would match Core rows)", blank)

# ---- the real run
fk = Fake()
res = run("--send", "--confirm", PH, fk=fk, out="send")
check("the real run removes exactly the counted rows", res["cleared"] == {"load_id_map": 4, "target_mappings": 5, "record_pass_markers": 5}, res["cleared"])
check("a backup copy was taken first and holds the old rows", res["backup"] and os.path.isfile(res["backup"]) and sqlite3.connect(res["backup"]).execute("select count(*) from load_id_map").fetchone()[0] == 8)
check("Core rows are exactly as before", core_rows(dbp) == core_rows(res["backup"]))
check("every other table (schema, source_records, decisions, profiling) is exactly as before", snapshot(dbp) == before)
k = sqlite3.connect(dbp)
left = {"idmap": [tuple(r) for r in k.execute("select temp_id, environment, workspace_key, system from load_id_map order by 1,2,3")],
        "maps": sorted(set(tuple(r) for r in k.execute("select system, environment, workspace_key from target_mappings"))),
        "marks": sorted(set(tuple(r) for r in k.execute("select pass, environment, workspace_key from record_pass_markers")))}
k.close()
check("another Workspace, another stage and Core stay", left["idmap"] == [("C-F-fA1", "sandbox1", "", "core"), ("C-T-tA", "production", "", "core"), ("P-T-tA", "dev", "wsOther", "planning"), ("P-T-tA", "uat", "wsDev", "planning")]
      and left["maps"] == [("core", "production", ""), ("core", "sandbox1", ""), ("planning", "dev", "wsOther"), ("planning", "uat", "wsDev")]
      and left["marks"] == [("core_backfill", "production", ""), ("core_backfill", "sandbox1", ""), ("records", "dev", "wsOther"), ("records", "uat", "wsDev")], left)
check("nothing was deleted or written in Workfront", all(m == "GET" for m, _ in fk.reqs), fk.reqs)
res2 = run("--send", "--confirm", PH, out="send2")
check("running it again removes nothing more", res2["cleared"] == {"load_id_map": 0, "target_mappings": 0, "record_pass_markers": 0})

# ---- one transaction: a failure part way leaves everything in place
d2 = os.path.join(tmp, "txn.db")
src, dst = sqlite3.connect(res["backup"]), sqlite3.connect(d2)
src.backup(dst); src.close()
dst.execute("create trigger stop_it before delete on load_id_map begin select raise(abort, 'stop'); end")
dst.commit()
try:
    pr.clear(dst, "dev", "wsDev"); failed = False
except sqlite3.DatabaseError:
    failed = True
n = (dst.execute("select count(*) from target_mappings where system='planning' and workspace_key='wsDev' and environment='dev'").fetchone()[0],
     dst.execute("select count(*) from record_pass_markers where workspace_key='wsDev' and environment='dev'").fetchone()[0])
check("the delete is one transaction: when the last delete fails, the earlier ones are rolled back", failed and n == (5, 5), n)
dst.close()
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
