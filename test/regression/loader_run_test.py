"""End-to-end test of `loader.py run --send` against a FAKE Workfront (a fake opener, no network): Core and Planning transports, state in load_id_map, the second pass for links,
failure handling, reruns, the production phrase and secrets. Fabricated names and fake secrets only."""
import base64, io, json, os, shutil, sqlite3, sys, tempfile, urllib.error
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
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

CORE_KEY, PLAN_TOKEN = "fakeCOREkey0123456789", "fakePLANtoken0123456789"
tmp = tempfile.mkdtemp(prefix="loader_run_")
# ---- a database built through the real store functions
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
st.set_scope(c, "appL", "JV", [{"table_id": "tA", "decision": "in", "target": "planning_and_core"}, {"table_id": "tB", "decision": "in", "target": "planning_only"}])
csvt = ("field_id,old_name,new_name,new_label,old_dataType,new_dataType\r\nfA1,Order Name,Order Name,Order Name,x,Text\r\nfA2,Status,Status,Status,x,Single select\r\nfA4,Customer,Customer,Customer,x,Link\r\n"
        "fA5,Amount,Amount,Amount,x,Number\r\nfB1,Customer Name,Customer Name,Customer Name,x,Text\r\n")
st.import_names(c, "appL", "JV", csv_base64=base64.b64encode(csvt.encode()).decode())
st.approve_fields(c, "appL", "JV", mode="pass_and_auto")
ld.sync(c, [r for r in c.execute("select * from airtable_fields")])
ld.decide(c, "JV", "fA4", "many_to_one", None, "orders belong to one customer")
c.close()

cfgp = os.path.join(tmp, "config.json")
json.dump({"workfront": {"core": {"sandbox1": {"base_url": "https://acme.sb01.workfront.com", "api_path": "/attask/api/v21.0", "api_key": CORE_KEY},
                                  "production": {"base_url": "https://acme.my.workfront.com", "api_path": "/attask/api/v21.0", "api_key": CORE_KEY + "P"}},
                         "planning": {"base_url": "https://acme.my.workfront.com", "bearer_token": PLAN_TOKEN, "workspace_id": "wsDev"}}}, open(cfgp, "w"))

# ---- a fake Workfront
class Resp(object):
    def __init__(self, status, body): self.status, self._b = status, body if isinstance(body, bytes) else json.dumps(body).encode()
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False
class Fake(object):
    def __init__(self, fail_name=None, fail_status=500):
        self.reqs, self.n, self.fail_name, self.fail_status, self.fields = [], 0, fail_name, fail_status, {}
    def open(self, req, timeout=None):
        body = json.loads(req.data) if req.data else None
        self.reqs.append((req.get_method(), req.full_url, dict(req.header_items()), body))
        name = (body or {}).get("name") or (body or {}).get("displayName") or (body or {}).get("label")
        if self.fail_name and name == self.fail_name:
            raise urllib.error.HTTPError(req.full_url, self.fail_status, "x", {}, io.BytesIO(("{\"error\":\"boom Authorization: Bearer abc.def.ghi key=%s\"}" % CORE_KEY).encode()))
        self.n += 1
        u = req.full_url
        if u.endswith("/param"): return Resp(200, {"data": {"ID": "PRM%d" % self.n, "name": name}})
        if u.endswith("/popt"): return Resp(200, {"data": {"ID": "OPT%d" % self.n}})
        if u.endswith("/record-types"): return Resp(201, {"id": "RT%d" % self.n, "displayName": name})
        if u.endswith("/fields"):
            return Resp(201, {"id": "FLD%d" % self.n, "displayName": name, "options": [{"displayName": o["displayName"], "name": "k%d" % i} for i, o in enumerate(body.get("options") or [])]})
        return Resp(404, b"no such route")
    @property
    def paths(self): return [(m, u.split("workfront.com", 1)[1]) for m, u, h, b in self.reqs]

def fresh(name):
    p = os.path.join(tmp, name + ".db"); shutil.copy(dbp, p); return p
def run(db, fake, *extra, out=None):
    out = out or os.path.join(tmp, "out_" + os.path.basename(db))
    return loader.main(["run", "--db", db, "--base", "appL", "--out", out, "--config", cfgp, "--send"] + list(extra), opener=fake), out

# ---- guards
check("without --send nothing is sent", "Nothing was sent" in (refused(["run", "--db", dbp, "--base", "appL"]) or ""))
nocfg = os.path.join(tmp, "empty.json"); json.dump({"workfront": {}}, open(nocfg, "w"))
m = refused(["run", "--db", dbp, "--base", "appL", "--config", nocfg, "--send"], opener=Fake())
check("a system that is not configured is named, and nothing is sent", m is not None and "Not configured" in m and "Core (sandbox1)" in m and "Planning" in m, m)
fk = Fake()
m = refused(["run", "--db", fresh("prod"), "--base", "appL", "--config", cfgp, "--send", "--core-env", "production"], opener=fk)
check("a real Core production run needs the exact phrase, and nothing is sent without it", m is not None and "CREATE IN PRODUCTION" in m and not fk.reqs, m)
m = refused(["run", "--db", fresh("prod2"), "--base", "appL", "--config", cfgp, "--send", "--core-env", "production", "--confirm", "create in production"], opener=fk)
check("the phrase must match exactly", m is not None and not fk.reqs)
check("a missing database is refused", "No such database" in (refused(["run", "--db", os.path.join(tmp, "nope.db"), "--base", "appL", "--config", cfgp, "--send"], opener=Fake()) or ""))
bad = sqlite3.connect(os.path.join(tmp, "old.db")); bad.execute("create table airtable_fields(field_id)"); bad.commit(); bad.close()
check("a database without the state table in its current form is refused with the fix", "migrations" in (refused(["run", "--db", os.path.join(tmp, "old.db"), "--base", "appL", "--config", cfgp, "--send"], opener=Fake()) or ""))

# ---- a full run
db = fresh("full"); fake = Fake()
res, out = run(db, fake)
c = sqlite3.connect(db); c.row_factory = sqlite3.Row
rows = {r["temp_id"]: r for r in c.execute("select * from load_id_map")}
check("both systems ran and nothing failed", res["core"]["counts"].get("failed", 0) == 0 and res["planning"]["counts"].get("failed", 0) == 0 and res["core"]["counts"]["created"] >= 3 and res["planning"]["counts"]["created"] >= 7, (res["core"]["counts"], res["planning"]["counts"]))
check("every created item has its Workfront ID in load_id_map, with the Workspace in the key for Planning and none for Core",
      all(r["status"] == "created" and r["workfront_id"] for r in rows.values()) and rows["P-T-tA"]["workspace_key"] == "wsDev" and rows["C-F-fA1"]["workspace_key"] == "" and rows["C-F-fA1"]["environment"] == "sandbox1" and rows["P-T-tA"]["environment"] == "dev")
paths = fake.paths
check("Core calls go to /param and /popt under the configured API path; Planning to the v2 routes", ("POST", "/attask/api/v21.0/param") in paths and ("POST", "/attask/api/v21.0/popt") in paths
      and ("POST", "/maestro/api/v2/workspaces/wsDev/record-types") in paths and any(p[1].startswith("/maestro/api/v2/record-types/RT") and p[1].endswith("/fields") for p in paths))
hdr = lambda sub: [h for m_, u, h, b in fake.reqs if sub in u]
check("Core sends its API key header and Planning a Bearer token, each to its own system only", all(h.get("Apikey") == CORE_KEY and "Authorization" not in h for h in hdr("sb01")) and all(h.get("Authorization") == "Bearer " + PLAN_TOKEN and "Apikey" not in h for h in hdr("/maestro/")))
check("a Core field is created with name, dataType and displayType; a select option against its field's ID", any(b == {"name": "Order Name", "dataType": "TEXT", "displayType": "TEXT", "label": "Order Name", "description": "Source: Airtable. Origins: Orders.Order Name."} for m_, u, h, b in fake.reqs)
      and any(u.endswith("/popt") and b["parameterID"] == rows["C-F-fA2"]["workfront_id"] and b["label"] == "Open" for m_, u, h, b in fake.reqs))
planning_fields = [(u, b) for m_, u, h, b in fake.reqs if "/maestro/" in u and u.endswith("/fields")]
sel = [b for u, b in planning_fields if b["displayName"] == "Status"][0]
check("a Planning select field goes with its options, and each option's ID is recorded from the reply", sel["type"] == "single-select" and sel["options"] == [{"displayName": "Open"}, {"displayName": "Closed"}]
      and rows["P-O-fA2-selA"]["workfront_id"] == "k0" and rows["P-O-fA2-selB"]["workfront_id"] == "k1")
check("a number field carries the precision read from the schema", [b for u, b in planning_fields if b["displayName"] == "Amount"][0]["numberOptions"] == {"allowNegatives": True, "precision": 2})
link = [(i, b) for i, (u, b) in enumerate(planning_fields) if b["displayName"] == "Customer"]
check("the link is created last, pointing at the Customers record type, as its own reference field with no back reference", len(link) == 1 and link[0][0] == len(planning_fields) - 1 and
      link[0][1]["referenceOptions"] == {"recordTypeId": rows["P-T-tB"]["workfront_id"], "multiple": False, "createBackReference": False}, link)
check("every record type was created before any field", [p[1].endswith("/record-types") for p in paths if "/maestro/" in p[1]][:2] == [True, True])
check("items on hold or blocked were not sent (the derived field, the Core form)", "C-T-tA" not in rows and not any(b and b.get("displayName") == "Total" for m_, u, h, b in fake.reqs))
blob = open(os.path.join(out, "load_run.csv"), "rb").read() + open(os.path.join(out, "load_run_summary.txt"), "rb").read() + open(db, "rb").read()
check("no secret reaches the report, the summary or the database", CORE_KEY.encode() not in blob and PLAN_TOKEN.encode() not in blob)
rr = list(__import__("csv").DictReader(open(os.path.join(out, "load_run.csv"), encoding="utf-8-sig", newline="")))
check("the run report lists every item with its outcome and ID, and the summary says forms are attached by hand", {r["outcome"] for r in rr} >= {"created", "not_ready"} and "manual step" in open(os.path.join(out, "load_run_summary.txt"), encoding="utf-8-sig").read())
c.close()

# ---- a rerun creates nothing
before = len(fake.reqs)
res2, _ = run(db, fake)
check("running again sends nothing: everything is already created", len(fake.reqs) == before and res2["core"]["counts"].get("created", 0) == 0 and res2["planning"]["counts"].get("created", 0) == 0 and res2["core"]["counts"]["already_created"] >= 3)

# ---- one failure does not stop the run, and is scrubbed
db2 = fresh("fail"); fk2 = Fake(fail_name="Order Name")
res3, out3 = run(db2, fk2, "--only", "core")
c = sqlite3.connect(db2); c.row_factory = sqlite3.Row
bad = c.execute("select * from load_id_map where temp_id='C-F-fA1'").fetchone()
check("a failed field is recorded as failed with a scrubbed reason, and the other Core items still ran", res3["core"]["counts"]["failed"] == 1 and bad["status"] == "failed" and CORE_KEY not in bad["last_error"] and "abc.def.ghi" not in bad["last_error"] and res3["core"]["counts"]["created"] >= 2 and "planning" not in res3)
check("--only core touched no Planning route", not any("/maestro/" in u for m_, u, h, b in fk2.reqs))
fk3 = Fake()
res4, _ = run(db2, fk3, "--only", "core")
check("after the cause is fixed a rerun creates only the failed field (and what depended on it), nothing twice", res4["core"]["counts"]["created"] >= 1 and sum(1 for m_, u, h, b in fk3.reqs if u.endswith("/param")) == 1)
c.close()

# ---- Planning only, into the uat stage
db3 = fresh("uat"); fk4 = Fake()
res5, _ = run(db3, fk4, "--only", "planning", "--planning-stage", "uat")
c = sqlite3.connect(db3)
check("a Planning-only run into uat records its rows under uat and calls only Planning", res5["planning"]["counts"]["created"] >= 7 and c.execute("select count(*) from load_id_map where environment='uat' and system='planning'").fetchone()[0] >= 7
      and not any("sb01" in u for m_, u, h, b in fk4.reqs))
c.close()

print("ALL PASS" if ok else "FAILURES")
sys.exit(0 if ok else 1)
