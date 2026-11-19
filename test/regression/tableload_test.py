"""Tests for tableload.py: the steps of the "Load a table" page (check, structure, records, links, verify, reset) against a FAKE Workfront. Gating, a failed step, a stop and a
rerun, and that no token and no record value leaves a result. Fabricated names and records; fake secrets; no network."""
import base64, hashlib, json, os, shutil, sqlite3, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
import fieldnames_store as st, linkdirections as ld, loadreset as rs, tableload as tl
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:500])
def refused(fn, *a, **k):
    try:
        fn(*a, **k); return None
    except tl.LoadError as e:
        return e.message

TOKEN = "fakePLANtoken0123456789"
BASE = "appL"
tmp = tempfile.mkdtemp(prefix="tableload_")
dbp = os.path.join(tmp, "base.db")
c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row
c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
for tid, nm in (("tA", "Orders"), ("tB", "Customers"), ("tC", "Notes"), ("tD", "Hidden"), ("tE", "Pending")):
    c.execute("insert into airtable_tables(table_id, base_id, table_name) values (?,?,?)", (tid, BASE, nm))
F = [("fA1", "tA", "Orders", "Order Name", "singleLineText", None), ("fA4", "tA", "Orders", "Customer", "multipleRecordLinks", "tB"),
     ("fB1", "tB", "Customers", "Customer Name", "singleLineText", None), ("fB2", "tB", "Customers", "Note Link", "multipleRecordLinks", "tC"),
     ("fC1", "tC", "Notes", "Note Text", "singleLineText", None), ("fD1", "tD", "Hidden", "Secret Name", "singleLineText", None), ("fE1", "tE", "Pending", "Pending Name", "singleLineText", None)]
for r in F:
    c.execute("insert into airtable_fields(base_id, table_id, table_name, field_id, field_name, data_type, linked_table_id, prefers_single_record_link, updated_at) values (?,?,?,?,?,?,?,1,'2026-01-01 10:00:00.000000')",
              (BASE, r[1], r[2], r[0], r[3], r[4], r[5]))
c.commit()
st.approve_tables(c, BASE, "JV", mode="unchanged"); st.approve_tables(c, BASE, "JV", mode="auto")
st.set_scope(c, BASE, "JV", [{"table_id": t, "decision": "in", "target": "planning_only"} for t in ("tA", "tB", "tC", "tE")] + [{"table_id": "tD", "decision": "out", "target": None, "reason": "not part of the rehearsal"}])
csvt = "field_id,old_name,new_name,new_label,old_dataType,new_dataType\r\n" + "".join("%s,%s,%s,%s,x,%s\r\n" % (r[0], r[3], r[3], r[3], "Link" if r[4] == "multipleRecordLinks" else "Text") for r in F[:-2])        # tD is out of scope, and tE gets no imported data type, so its field is blocked
st.import_names(c, BASE, "JV", csv_base64=base64.b64encode(csvt.encode()).decode())
st.approve_fields(c, BASE, "JV", mode="pass_and_auto")
import typesfixture; typesfixture.fill_defaults(c, BASE); import datatypes_store as dts
for _sys in ("planning", "core"): dts.apply(c, BASE, "JV", "tE gets no type", _sys, "clear", selection={"table_ids": ["tE"]})      # Planning type and Core dataType and displayType from the default mapping
ld.sync(c, [r for r in c.execute("select * from airtable_fields")])
ld.decide(c, "JV", "fA4", "many_to_one", None, "orders belong to one customer")
ld.decide(c, "JV", "fB2", "many_to_one", None, "customers have one note")
c.close()

cfgp = os.path.join(tmp, "config.json")
json.dump({"token": "patFAKEairtable.0000000000000000000000", "workfront": {"planning": {"base_url": "https://acme.my.workfront.com", "bearer_token": TOKEN, "workspace_id": "wsDev", "record_batch_size": 2}}}, open(cfgp, "w"))
extract = os.path.join(tmp, "extract")
ex = os.path.join(extract, BASE); os.makedirs(ex)
def save(tid, recs):
    json.dump({"format": "airtable-table-extract/1", "base_id": BASE, "table_id": tid, "table_name": tid, "records": recs}, open(os.path.join(ex, tid + ".json"), "w"))
save("tA", [{"id": "recA%d" % i, "fields": {"fA1": "SECRETVALUE order %d" % i, "fA4": ["recB%d" % (i % 3 + 1)]} } for i in range(1, 6)])
save("tB", [{"id": "recB%d" % i, "fields": {"fB1": "SECRETVALUE customer %d" % i, "fB2": ["recC1"]}} for i in range(1, 4)])
save("tC", [{"id": "recC1", "fields": {"fC1": "SECRETVALUE note"}}, {"id": "recC2", "fields": {"fC1": "SECRETVALUE note 2"}}])

class Resp(object):
    def __init__(self, status, body): self.status, self._b = status, (json.dumps(body).encode() if body is not None else b"")
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False
class Fake(object):
    """A fake Planning: record types, fields, a record store per record type (bulk create, list, bulk delete), PATCH, and switches to refuse a field or fail record indexes."""
    def __init__(self):
        self.reqs, self.n, self.r, self.store, self.refuse_fields, self.fail_index, self.on_bulk = [], 0, 0, {}, set(), set(), None
        self.rt_refuse, self.gone, self.deleted, self.home = None, set(), [], "wsDev"      # record type delete: a refusal status, IDs already gone, IDs deleted, the Workspace a record type reports
    def open(self, req, timeout=None):
        body = json.loads(req.data) if req.data else None
        m, full = req.get_method(), req.full_url
        self.reqs.append((m, full, body))
        u = full.split("?")[0]
        self.n += 1
        if m == "POST" and u.endswith("/record-types"): return Resp(201, {"id": "RT%d" % self.n, "displayName": body["displayName"]})
        if m == "POST" and u.endswith("/fields"):
            if body["displayName"] in self.refuse_fields:
                import urllib.error, io
                raise urllib.error.HTTPError(full, 400, "bad", {}, io.BytesIO(json.dumps({"error": "VALIDATION_FAILED for Bearer " + TOKEN}).encode()))
            return Resp(201, {"id": "FLD%d" % self.n, "displayName": body["displayName"]})
        if m == "POST" and u.endswith("/records/bulk"):
            rt, out = u.split("/record-types/")[1].split("/")[0], []
            if self.on_bulk: self.on_bulk()
            status = 201
            for i, _ in enumerate(body):
                if i in self.fail_index:
                    status = 207; out.append({"requestIndex": i, "status": "error", "error": {"code": "FIELD_INVALID", "message": "SECRETVALUE echoed"}}); continue
                self.r += 1; rid = "REC%04d" % self.r; self.store.setdefault(rt, []).append(rid); out.append({"requestIndex": i, "id": rid})
            return Resp(status, {"status": "success", "total": len(out), "results": out})
        if m == "GET" and "/record-types/" in u and u.endswith("/records"):
            rt = u.split("/record-types/")[1].split("/")[0]; rows = self.store.get(rt, [])
            return Resp(200, {"content": [{"id": r, "data": {"f": "SECRETVALUE"}} for r in rows[:100]], "totalElements": len(rows)})
        if m in ("GET", "DELETE") and u.rsplit("/", 2)[-2] == "record-types":
            import urllib.error, io
            rid = u.rsplit("/", 1)[-1]
            if rid in self.gone or rid in self.deleted: raise urllib.error.HTTPError(full, 404, "nf", {}, io.BytesIO(b"{}"))
            if m == "DELETE":
                if self.rt_refuse: raise urllib.error.HTTPError(full, self.rt_refuse, "no", {}, io.BytesIO(json.dumps({"code": "RECORD_TYPE_IN_HIERARCHY", "message": "refused for Bearer " + TOKEN}).encode()))
                self.deleted.append(rid); return Resp(204, None)
            return Resp(200, {"id": rid, "primaryWorkspaceId": self.home})
        if m == "DELETE" and u.endswith("/records/bulk"):
            rt = u.split("/record-types/")[1].split("/")[0]; res = []
            for i, rid in enumerate(body["ids"]): self.store[rt].remove(rid); res.append({"requestIndex": i, "id": rid, "status": "success"})
            return Resp(200, {"status": "success", "total": len(res), "results": res})
        if m == "PATCH" and "/v2/records/" in u: return Resp(200, {})
        return Resp(404, {})
    def posts(self, tail): return [x for x in self.reqs if x[0] == "POST" and x[1].split("?")[0].endswith(tail)]
    def made(self, name): return [x for x in self.reqs if x[0] == "POST" and x[2] and x[2].get("displayName") == name]

fk = Fake()
ctx = tl.planning_context(cfgp, "dev", True)
tr = tl.transport(ctx, opener=fk)
conn = sqlite3.connect(dbp); conn.row_factory = sqlite3.Row
never = []                      # every result, to look for a token or a record value at the end
def keep(x): never.append(json.dumps(x, default=sorted)); return x
def state(t): return keep(tl.check_table(conn, BASE, t, ctx, extract))
def dbhash(): conn.commit(); return hashlib.sha256(open(dbp, "rb").read()).hexdigest()

# ---- the picker and step 1
tabs = tl.table_choices(conn, BASE)
check("the picker lists the in-scope tables only, with scope, target and name-approval status, sorted by name", [t["table_id"] for t in tabs] == ["tB", "tC", "tA", "tE"] and all(t["scope"] == "in" for t in tabs) and {t["table_id"]: t["name_approved"] for t in tabs} == {"tA": True, "tB": True, "tC": True, "tE": True}, tabs)
check("a table that is out of scope or unknown is refused with its ID", "tD" in (refused(tl.check_table, conn, BASE, "tD", ctx, extract) or "") and "tZ" in (refused(tl.check_table, conn, BASE, "tZ", ctx, extract) or ""))
sE = state("tE")
check("a table with a blocked field (no imported data type) is BLOCKED with reasons, and no later step is open", sE["check"]["counts"]["blocked"] >= 1 and not sE["check"]["can_continue"] and sE["check"]["reasons"] and not any(sE["gates"].values()), sE["gates"])
sC = state("tC")
check("a clear table: ready counts, no blocked item, step 2 open and the rest closed", sC["check"]["counts"] == {"ready": 2, "hold": 0, "blocked": 0} and sC["gates"] == {"can_structure": True, "can_records": False, "can_links": False, "can_verify": False, "can_reset": False}, sC["gates"])
sA = state("tA")
check("step 1 names the tables a link points to and how far each one is", [d["table_id"] for d in sA["check"]["dependencies"]] == ["tB"] and sA["check"]["dependencies"][0]["loaded"] is False and sA["check"]["dependencies"][0]["record_type"] is None and sA["check"]["dependency_tables"] == ["tB"], sA["check"]["dependencies"])
check("each step is refused until the one before is done", all(refused(tl.need, sC["gates"], k, tl.GATE_MESSAGE[k]) for k in ("can_records", "can_links", "can_verify", "can_reset")) and refused(tl.need, sC["gates"], "can_structure", "x") is None)

# ---- step 2: dry run, then send
h0 = dbhash()
events = []
res = keep(tl.step_structure(conn, BASE, "tC", ctx, tr, False, None, events.append))
check("the structure dry run sends nothing and writes nothing", fk.reqs == [] and dbhash() == h0 and res["dry_run"] and res["counts"] == {"would_create": 2}, res["counts"])
res = keep(tl.step_structure(conn, BASE, "tC", ctx, tr, True, None, events.append))
check("the send creates the record type and the field, one outcome per item", res["counts"] == {"created": 2} and [r["outcome"] for r in res["items"]] == ["created", "created"] and len(fk.posts("/record-types")) == 1 and len(fk.posts("/fields")) == 1, res)
check("progress names the step, the table and the counts", events and events[-1]["step"] == "structure" and events[-1]["table_id"] == "tC" and events[-1]["counts"] == {"created": 2} and events[-1]["done"] == 2, events)
sC = state("tC")
check("the structure is done and step 3 opens (and the reset becomes possible)", sC["steps"]["structure"]["done"] and sC["gates"]["can_records"] and sC["gates"]["can_reset"] and not sC["gates"]["can_links"], sC["gates"])
res = keep(tl.step_structure(conn, BASE, "tC", ctx, tr, True, None, events.append))
check("a rerun creates nothing twice: every item reports already created", res["counts"] == {"already_created": 2} and len(fk.posts("/record-types")) == 1, res["counts"])
mp = keep(tl.mapping(conn, BASE, "tC", ctx))
check("the ID map shows the record type and the field with their Workfront IDs", [(r["kind"], r["new_name"], bool(r["workfront_id"])) for r in mp["rows"]] == [("record type", "Notes", True), ("field", "Note Text", True)] and mp["workspace_id"] == "wsDev", mp)

# ---- a failed step, then a rerun that creates only what is missing
fk.refuse_fields = {"Order Name"}
res = keep(tl.step_structure(conn, BASE, "tA", ctx, tr, True, None, events.append))
sA = state("tA")
failed = [r for r in res["items"] if r["outcome"] == "failed"]
check("a refused field is reported as failed with a scrubbed reason; the other items still ran", len(failed) == 1 and failed[0]["name"] == "Order Name" and TOKEN not in failed[0]["detail"] and res["counts"].get("created", 0) >= 2, res["counts"])
check("a failed step keeps the next one closed", not sA["steps"]["structure"]["done"] and sA["steps"]["structure"]["failed"] == 1 and not sA["gates"]["can_records"], sA["steps"]["structure"])
check("the dependency record type (Customers) was created empty so the link field could be built", fk.made("Customers") and not fk.made("Customer Name") and sA["check"]["dependencies"][0]["record_type"], sA["check"]["dependencies"])
fk.refuse_fields = set()
n_before = len(fk.made("Orders")) + len(fk.made("Customers")) + len(fk.made("Customer"))
res = keep(tl.step_structure(conn, BASE, "tA", ctx, tr, True, None, events.append))
sA = state("tA")
check("after the cause is fixed the rerun creates only the failed field, and the step is done", res["counts"].get("created") == 1 and res["counts"].get("failed", 0) == 0 and sA["steps"]["structure"]["done"]
      and len(fk.made("Orders")) + len(fk.made("Customers")) + len(fk.made("Customer")) == n_before and len(fk.made("Order Name")) == 2, res["counts"])

# ---- a stop part way through the structure, then a rerun
keep(tl.step_structure(conn, BASE, "tB", ctx, tr, False, None, events.append))
calls = []
def stop_after_three():
    calls.append(1); return len(calls) > 3
res = keep(tl.step_structure(conn, BASE, "tB", ctx, tr, True, stop_after_three, events.append))
check("a stop is honoured between items, says so, and the item it reached was finished", res["cancelled"] and len(res["items"]) == 3 and len(fk.made("Customer Name")) == 1 and not fk.made("Note Link"), res["counts"])
check("a stop leaves nothing half done (no pending row)", conn.execute("select count(*) from load_id_map where status='pending'").fetchone()[0] == 0)
res = keep(tl.step_structure(conn, BASE, "tB", ctx, tr, True, None, events.append))
sB = state("tB")
check("a rerun after a stop finishes the structure without creating anything twice", sB["steps"]["structure"]["done"] and not res["cancelled"] and len(fk.made("Customer Name")) == 1 and len(fk.made("Customers")) == 1 and len(fk.made("Note Link")) == 1, res["counts"])

# ---- step 3: records
sC = state("tC")
check("step 3 starts with the saved file check (counts only)", sC["steps"]["records"]["file_exists"] and sC["steps"]["records"]["file_records"] == 2 and sC["steps"]["records"]["registered"] == 0 and not sC["steps"]["records"]["complete"], sC["steps"]["records"])
gone = os.path.join(extract, BASE, "tC.json"); os.rename(gone, gone + ".off")
check("a missing saved file is refused with a plain reason, before anything is sent", "no saved record file" in (refused(tl.step_records, conn, BASE, "tC", ctx, tr, False, extract, None, events.append) or ""))
os.rename(gone + ".off", gone)
h0 = dbhash(); n0 = len(fk.reqs)
res = keep(tl.step_records(conn, BASE, "tC", ctx, tr, False, extract, None, events.append))
check("the records dry run gives counts, sends nothing and writes nothing", res["dry_run"] and res["in_file"] == 2 and res["would_send"] == 2 and res["done"] == 0 and len(fk.reqs) == n0 and dbhash() == h0, res)
fk.fail_index = {1}
events = []
res = keep(tl.step_records(conn, BASE, "tC", ctx, tr, True, extract, None, events.append))
check("a partly failed batch keeps its successes and lists the failure as a record ID and a scrubbed reason", res["created"] == 1 and res["failed"] == 1 and res["failures"] == [{"record_id": "recC2", "reason": "Planning refused this record (FIELD_INVALID)"}] and "SECRETVALUE" not in json.dumps(res), res)
check("progress shows the table and the batch number", events and events[-1]["table_id"] == "tC" and events[-1]["batch"] == 1 and events[-1]["batches"] == 1 and events[-1]["created"] == 1, events)
sC = state("tC")
check("a failed record keeps step 4 and 5 closed", not sC["steps"]["records"]["complete"] and sC["steps"]["records"]["pending"] == 1 and not sC["gates"]["can_verify"], sC["steps"]["records"])
fk.fail_index = set()
res = keep(tl.step_records(conn, BASE, "tC", ctx, tr, True, extract, None, events.append))
sC = state("tC")
check("a rerun sends only the record that failed; the table then has all its records exactly once", res["created"] == 1 and sum(len(b[2]) for b in fk.posts("/records/bulk")) == 3 and len(fk.store[sC["steps"]["structure"]["record_type"]]) == 2 and sC["steps"]["records"]["complete"], res)

# a stop between batches of tB (3 records at 2 per batch), then a rerun
holder = {"n": 0}
def stop_second_batch():
    holder["n"] += 1; return holder["n"] > 1
res = keep(tl.step_records(conn, BASE, "tB", ctx, tr, True, extract, stop_second_batch, events.append))
sB = state("tB")
check("a stop between batches sends no further batch and records what was done", res["cancelled"] and res["created"] == 2 and sB["steps"]["records"]["pending"] == 1, res)
res = keep(tl.step_records(conn, BASE, "tB", ctx, tr, True, extract, None, events.append))
rtB = state("tB")["steps"]["structure"]["record_type"]
check("the rerun after a stop sends only the rest: no record is created twice", res["created"] == 1 and len(fk.store[rtB]) == 3 and state("tB")["steps"]["records"]["complete"], res)

# ---- step 4: links
sA = state("tA")
check("step 4 stays closed until Orders' own records are loaded; Customers is already loaded so nothing is waited for", not sA["gates"]["can_links"] and sA["steps"]["links"]["waiting_on"] == [], sA["steps"]["links"])
res = keep(tl.step_records(conn, BASE, "tA", ctx, tr, True, extract, None, events.append))
sA = state("tA")
check("Orders' records are loaded (5, in batches of 2) and step 4 opens", res["created"] == 5 and sA["steps"]["records"]["complete"] and sA["gates"]["can_links"], res)
res = keep(tl.step_links(conn, BASE, "tA", ctx, tr, False, extract, None, events.append))
check("the links dry run counts the records waiting for their links and sends nothing", res["dry_run"] and res["pending"] == 5 and len([x for x in fk.reqs if x[0] == "PATCH"]) == 0, res)
res = keep(tl.step_links(conn, BASE, "tA", ctx, tr, True, extract, None, events.append))
sA = state("tA")
check("the links pass sets every link; Customers' records were loaded earlier so none waits", res["updated"] == 5 and res["waiting"] == 0 and len([x for x in fk.reqs if x[0] == "PATCH"]) == 5 and sA["steps"]["links"]["done"], res)
sB = state("tB")
check("Customers' links to Notes work too, and a table with no link fields has nothing to do", sB["steps"]["links"]["applicable"] and not sC["steps"]["links"]["applicable"] and sC["steps"]["links"]["done"] and not sC["gates"]["can_links"], sB["steps"]["links"])
# a link whose target table is not loaded yet waits
db2 = os.path.join(tmp, "fresh.db"); shutil.copy(dbp, db2)
conn2 = sqlite3.connect(db2); conn2.row_factory = sqlite3.Row
fk2 = Fake(); tr2 = tl.transport(ctx, opener=fk2)
for t in ("tA", "tB", "tC"): tl.step_structure(conn2, BASE, t, ctx, tr2, True, None, lambda e: None)
conn2.execute("delete from target_mappings"); conn2.execute("delete from record_pass_markers"); conn2.commit()
tl.step_records(conn2, BASE, "tA", ctx, tr2, True, extract, None, lambda e: None)
w = tl.check_table(conn2, BASE, "tA", ctx, extract)
res = tl.step_links(conn2, BASE, "tA", ctx, tr2, True, extract, None, lambda e: None)
check("links to a table whose records are not loaded yet wait, are counted and are named", res["waiting"] == 5 and res["updated"] == 0 and [d["table_id"] for d in w["steps"]["links"]["waiting_on"]] == ["tB"], (res, w["steps"]["links"]))
conn2.close()

# ---- step 5: verify
v = keep(tl.verify(conn, BASE, "tA", ctx, tr, extract))
check("verify compares the saved file, the registry, the records pass, the mapped records and Workfront's own count", [x["count"] for x in v["counts"]] == [5, 5, 5, 5, 5] and v["match"] and v["differences"] == [], v)
fk.store[state("tA")["steps"]["structure"]["record_type"]].append("STRAY")
v = keep(tl.verify(conn, BASE, "tA", ctx, tr, extract))
check("a difference is reported as counts and never repaired", not v["match"] and v["differences"][0]["difference"] == 1 and v["differences"][0]["found"] == 6 and "STRAY" not in json.dumps(v), v)
check("verify is refused until the records pass is complete", "Step 3" in (refused(tl.verify, conn, BASE, "tE", ctx, tr, extract) or ""))

# ---- step 6: reset
h0 = dbhash(); n0 = len(fk.reqs)
res = keep(tl.step_reset(conn, BASE, "tC", ctx, None, False, None, events.append))
check("the reset dry run reads this tool's counts only and sends nothing", res["dry_run"] and res["before"]["mapped"] == 2 and res["wipe"] is None and res["phrase"] == "DELETE RECORDS IN DEV" and len(fk.reqs) == n0 and dbhash() == h0, res)
res = keep(tl.step_reset(conn, BASE, "tC", ctx, tr, True, None, events.append))
check("the reset deletes the table's records, then clears the local memory; the record type stays", res["wipe"]["deleted"] == 2 and res["wipe"]["left"] == 0 and res["cleared"]["mappings"] == 2 and state("tC")["steps"]["structure"]["done"], res)
sC = state("tC")
check("after a reset the table can be loaded again from step 3", sC["gates"]["can_records"] and not sC["steps"]["records"]["complete"] and sC["steps"]["structure"]["done"], sC["steps"])
check("a reset with no record type recorded is refused", "nothing to reset" in (refused(tl.step_reset, conn, BASE, "tE", ctx, tr, False, None, events.append) or ""))
stop = Fake(); trs = tl.transport(ctx, opener=stop)
stop.store[sC["steps"]["structure"]["record_type"]] = ["X%d" % i for i in range(250)]
calls = []
res = keep(tl.step_reset(conn, BASE, "tC", ctx, trs, True, lambda: (calls.append(1) or len(calls) > 1), events.append))
check("a stop during the wipe says so, leaves the rest in Workfront and keeps the local memory", res["cancelled"] and len(stop.store[sC["steps"]["structure"]["record_type"]]) == 150 and res["cleared"] is None, res)

# ---- reset a table's structure (tablereset.py, used by the Git-R-Done page): delete the record type, then forget the map
import tablereset as tbr
def refusedT(fn, *a):
    try:
        fn(*a); return None
    except tbr.TableResetError as e:
        return str(e)
def rows(tid): return conn.execute("select count(*) from load_id_map where table_id=? and workspace_key='wsDev' and environment='dev'", (tid,)).fetchone()[0]
def kept(): return [conn.execute("select count(*) from %s" % t).fetchone()[0] for t in ("airtable_tables", "airtable_fields", "name_events", "source_records", "field_relationships", "table_scope")]
rtB = state("tB")["steps"]["structure"]["record_type"]
fidB = conn.execute("select airtable_field_id from load_id_map where table_id='tB' and kind='field' order by 1 limit 1").fetchone()[0]
for env_, ws_ in (("dev", "wsDev"), ("uat", "wsDev"), ("dev", "wsOther")):     # one option ID read back for the stage and Workspace in use, and two that must survive
    conn.execute("insert into option_workfront_ids(airtable_field_id, choice_id, system, environment, workspace_id, workfront_option_id) values (?,?,?,?,?,?)", (fidB, "ch" + env_ + ws_, "planning", env_, ws_, "OPT"))
conn.commit()
h0, n0, k0 = dbhash(), len(fk.reqs), kept()
res = keep(tbr.reset(conn, BASE, "tB", "dev", "wsDev", None, False))
check("the table reset dry run counts what would be forgotten, names the other tables' link fields that point here, and sends and writes nothing",
      res["attempt"] is None and res["cleared"] is None and res["before"]["map_total"] == 3 and res["before"]["mapped_records"] == 3 and res["before"]["markers"] >= 3 and res["before"]["option_ids"] == 1
      and res["inbound"] == [{"table_id": "tA", "name": "Orders", "fields": 1}] and len(fk.reqs) == n0 and dbhash() == h0, res)
fk.home = "wsOther"
res = keep(tbr.reset(conn, BASE, "tB", "dev", "wsDev", tr, True))
check("a record type in another Workspace is never deleted: the attempt is refused and the map is kept", res["attempt"]["deleted"] is False and "different Workspace" in res["attempt"]["reason"] and res["cleared"] is None
      and not [x for x in fk.reqs if x[0] == "DELETE" and x[1].split("?")[0].endswith("/record-types/" + rtB)] and rows("tB") == 3)
fk.home = "wsDev"; fk.rt_refuse = 403
res = keep(tbr.reset(conn, BASE, "tB", "dev", "wsDev", tr, True))
check("when Workfront refuses the delete the result says why, with no token, and nothing local changes", res["attempt"]["deleted"] is False and res["attempt"]["status"] == 403 and "RECORD_TYPE_IN_HIERARCHY" in res["attempt"]["reason"] and TOKEN not in json.dumps(res)
      and res["cleared"] is None and rows("tB") == 3 and state("tB")["steps"]["structure"]["record_type"] == rtB and state("tB")["steps"]["records"]["complete"], res)
inbound_rows, n_f = rows("tA"), len(fk.reqs)
res = keep(tbr.forget_only(conn, BASE, "tB", "dev", "wsDev"))
check("forgetting anyway sends nothing, clears the table's map (items, record IDs, markers, option IDs) for this stage and Workspace only", res["cleared"] == {"load_id_map": 3, "mappings": 3, "markers": res["before"]["markers"], "option_ids": 1}
      and len(fk.reqs) == n_f and rows("tB") == 0 and rows("tA") == inbound_rows and rows("tC") > 0
      and conn.execute("select count(*) from option_workfront_ids").fetchone()[0] == 2 and kept() == k0, res)
check("every decision and the Airtable registry are still there, so the table can be rebuilt", kept() == k0 and not state("tB")["steps"]["structure"]["record_type"] and not state("tB")["gates"]["can_records"] and state("tB")["gates"]["can_structure"])
check("a table with nothing recorded is refused with a plain reason", "nothing to reset" in (refusedT(tbr.reset, conn, BASE, "tB", "dev", "wsDev", tr, False) or "") and "nothing to reset" in (refusedT(tbr.reset, conn, BASE, "tB", "dev", "wsDev", tr, True) or ""))
fk.rt_refuse = None
res = keep(tl.step_structure(conn, BASE, "tB", ctx, tr, True, None, events.append)); keep(tl.step_records(conn, BASE, "tB", ctx, tr, True, extract, None, events.append))
sB = state("tB"); rtB2 = sB["steps"]["structure"]["record_type"]
check("the table is rebuilt and reloaded from the kept knowledge, with a new record type", res["counts"].get("created") == 3 and rtB2 and rtB2 != rtB and sB["steps"]["records"]["complete"] and len(fk.store[rtB2]) == 3, res["counts"])
res = keep(tbr.reset(conn, BASE, "tB", "dev", "wsDev", tr, True))
check("when Workfront deletes the record type the map is forgotten in the same step", res["attempt"] == {"deleted": True, "already_gone": False} and fk.deleted == [rtB2] and res["cleared"]["load_id_map"] == 3 and res["cleared"]["mappings"] == 3 and rows("tB") == 0, res)
fk.gone.add(state("tC")["steps"]["structure"]["record_type"])
res = keep(tbr.reset(conn, BASE, "tC", "dev", "wsDev", tr, True))
check("a record type already deleted by hand counts as deleted, and the map is forgotten", res["attempt"] == {"deleted": True, "already_gone": True} and res["cleared"]["load_id_map"] == 2 and rows("tC") == 0, res)
st_ = tl.ls.DbState(conn, BASE, "wsDev")
st_.begin({"temp_id": "P-T-tE", "kind": "record_type", "system": "planning", "table_id": "tE", "name": "Pending"}, "dev"); st_.fail("P-T-tE", "dev", "refused for Bearer " + TOKEN)
n1 = len(fk.reqs)
res = keep(tbr.reset(conn, BASE, "tE", "dev", "wsDev", tr, True))
check("a table with only a failed attempt (no Workfront ID) is forgotten at once without calling Workfront", res["attempt"] is None and res["cleared"]["load_id_map"] == 1 and len(fk.reqs) == n1 and rows("tE") == 0, res)
check("the table reset never touches the Core rows or another Workspace's rows", conn.execute("select count(*) from load_id_map where system='core'").fetchone()[0] == 0 and conn.execute("select count(*) from option_workfront_ids where workspace_id='wsOther'").fetchone()[0] == 1)

# ---- what must never leave
blob = "\n".join(never)
check("no Workfront token and no record value is in any result", TOKEN not in blob and "SECRETVALUE" not in blob and "patFAKE" not in blob)
conn.close()
check("no record value is in the database", b"SECRETVALUE" not in open(dbp, "rb").read())
check("the Workspace in the context is the config's, and the token is hidden in the context's text", ctx.ws == "wsDev" and TOKEN not in repr(ctx.creds) and TOKEN not in tl.clean(ctx, "x " + TOKEN))
sys.exit(0 if ok else 1)
