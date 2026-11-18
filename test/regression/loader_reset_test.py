"""Tests for `loader.py reset` (wipe one table's records, then clear this tool's memory of them) against a FAKE Workfront. Fabricated names and records; fake secrets; no network."""
import base64, hashlib, io, json, os, shutil, sqlite3, sys, tempfile, urllib.error
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
import fieldnames_store as st, loader
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
N = 250
tmp = tempfile.mkdtemp(prefix="loader_reset_")
dbp = os.path.join(tmp, "base.db")
c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row
c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
for tid, nm in (("tA", "Orders"), ("tB", "Customers")):
    c.execute("insert into airtable_tables(table_id, base_id, table_name) values (?,?,?)", (tid, "appL", nm))
F = [("fA1", "tA", "Orders", "Order Name"), ("fB1", "tB", "Customers", "Customer Name")]
for r in F:
    c.execute("insert into airtable_fields(base_id, table_id, table_name, field_id, field_name, data_type, updated_at) values ('appL',?,?,?,?,'singleLineText','2026-01-01 10:00:00.000000')", (r[1], r[2], r[0], r[3]))
c.commit()
st.approve_tables(c, "appL", "JV", mode="unchanged"); st.approve_tables(c, "appL", "JV", mode="auto")
st.set_scope(c, "appL", "JV", [{"table_id": t, "decision": "in", "target": "planning_only"} for t in ("tA", "tB")])
csvt = "field_id,old_name,new_name,new_label,old_dataType,new_dataType\r\n" + "".join("%s,%s,%s,%s,x,Text\r\n" % (r[0], r[3], r[3], r[3]) for r in F)
st.import_names(c, "appL", "JV", csv_base64=base64.b64encode(csvt.encode()).decode())
st.approve_fields(c, "appL", "JV", mode="pass_and_auto")
c.close()
cfgp = os.path.join(tmp, "config.json")
def config(path, ws):
    json.dump({"workfront": {"planning": {"base_url": "https://acme.my.workfront.com", "bearer_token": TOKEN, "workspace_id": ws, "record_batch_size": 50}}}, open(path, "w"))
config(cfgp, "wsDev")
other_cfg = os.path.join(tmp, "other.json"); config(other_cfg, "wsOther")
ex = os.path.join(tmp, "extract", "appL"); os.makedirs(ex)
def save(tid, recs):
    json.dump({"format": "airtable-table-extract/1", "base_id": "appL", "table_id": tid, "table_name": tid, "records": recs}, open(os.path.join(ex, tid + ".json"), "w"))
save("tA", [{"id": "recA%03d" % i, "fields": {"fA1": "Order %d" % i}} for i in range(N)])
save("tB", [{"id": "recB%03d" % i, "fields": {"fB1": "Customer %d" % i}} for i in range(3)])

class Resp(object):
    def __init__(self, status, body): self.status, self._b = status, json.dumps(body).encode()
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False
class Fake(object):
    """A fake Planning: record types, fields, and a record store per record type that the bulk create fills, the listing reads and the bulk delete empties."""
    def __init__(self, refuse=None, home="wsDev"):
        self.reqs, self.n, self.r, self.store, self.refuse, self.home = [], 0, 0, {}, set(refuse or ()), home
    def open(self, req, timeout=None):
        body = json.loads(req.data) if req.data else None
        m, full = req.get_method(), req.full_url
        self.reqs.append((m, full, body))
        u, _, q = full.partition("?")
        self.n += 1
        if m == "POST" and u.endswith("/record-types"): return Resp(201, {"id": "RT%d" % self.n, "displayName": body["displayName"]})
        if m == "POST" and u.endswith("/fields"): return Resp(201, {"id": "FLD%d" % self.n, "displayName": body["displayName"]})
        if m == "POST" and u.endswith("/records/bulk"):
            rt, out = u.split("/record-types/")[1].split("/")[0], []
            for i, _ in enumerate(body):
                self.r += 1; rid = "REC%04d" % self.r; self.store.setdefault(rt, []).append(rid); out.append({"requestIndex": i, "id": rid})
            return Resp(201, {"status": "success", "total": len(out), "results": out})
        if m == "GET" and "/record-types/" in u and u.endswith("/records"):
            rt = u.split("/record-types/")[1].split("/")[0]
            rows = self.store.get(rt, [])
            return Resp(200, {"content": [{"id": r, "data": {"f": "SECRET VALUE"}} for r in rows[:100]], "totalElements": len(rows)})
        if m == "GET" and u.rsplit("/", 2)[-2] == "record-types":
            return Resp(200, {"id": u.rsplit("/", 1)[-1], "primaryWorkspaceId": self.home})
        if m == "DELETE" and u.endswith("/records/bulk"):
            rt = u.split("/record-types/")[1].split("/")[0]
            res, status = [], 200
            for i, rid in enumerate(body["ids"]):
                if rid in self.refuse:
                    status = 207; res.append({"requestIndex": i, "id": rid, "status": "error", "error": {"code": "RECORD_LOCKED", "message": "SECRET VALUE echoed"}})
                else:
                    self.store[rt].remove(rid); res.append({"requestIndex": i, "id": rid, "status": "success"})
            return Resp(status, {"status": "success", "total": len(res), "results": res})
        return Resp(404, {})
    def of(self, method, tail): return [x for x in self.reqs if x[0] == method and x[1].split("?")[0].endswith(tail)]

def fresh(name, fk=None):
    p = os.path.join(tmp, name + ".db"); shutil.copy(dbp, p)
    fk = fk or Fake()
    loader.main(["run", "--db", p, "--base", "appL", "--out", os.path.join(tmp, "o_" + name), "--config", cfgp, "--send", "--only", "planning"], opener=fk)
    loader.main(["records", "--db", p, "--base", "appL", "--out", os.path.join(tmp, "r_" + name), "--config", cfgp, "--extract-dir", os.path.join(tmp, "extract"), "--send"], opener=fk)
    return p, fk
def rows(p, sql, *a):
    c = sqlite3.connect(p); r = [tuple(x) for x in c.execute(sql, a)]; c.close(); return r
def reset(p, *extra, cfg=None, fk=None, out="x"):
    return loader.main(["reset", "--db", p, "--base", "appL", "--out", os.path.join(tmp, "rs_" + out), "--config", cfg or cfgp, "--table", "tA"] + list(extra), opener=fk or Fake())
PHRASE = "DELETE RECORDS IN DEV"
def mapped(p, t): return rows(p, "select count(*) from target_mappings m join source_records s on s.source_record_uid=m.source_record_uid where s.table_id=?", t)[0][0]

db, fk = fresh("one")
check("the load put %d mapped records for tA and 3 for tB in the fake" % N, mapped(db, "tA") == N and mapped(db, "tB") == 3 and sum(len(v) for v in fk.store.values()) == N + 3)
loadmap = rows(db, "select * from load_id_map order by temp_id, environment, workspace_key")
sources = rows(db, "select * from source_records order by 1")
h0 = hashlib.sha256(open(db, "rb").read()).hexdigest()

# ---- dry run: no network, no change
f1 = Fake()
res = reset(db, fk=f1, out="dry")
text = open(os.path.join(tmp, "rs_dry", "reset_summary.txt"), encoding="utf-8-sig").read()
check("a dry run sends nothing, writes nothing to the database and says what it would do", f1.reqs == [] and hashlib.sha256(open(db, "rb").read()).hexdigest() == h0 and "dry run: nothing was sent" in text and PHRASE in text and res["before"]["mapped"] == N, text)
check("--send without the phrase is refused and sends nothing", "DELETE RECORDS IN DEV" in (refused(["reset", "--db", db, "--base", "appL", "--table", "tA", "--config", cfgp, "--send"], opener=f1) or "") and f1.reqs == [])
check("a wrong phrase is refused", "exact phrase" in (refused(["reset", "--db", db, "--base", "appL", "--table", "tA", "--config", cfgp, "--send", "--confirm", "delete records in dev"], opener=f1) or "") and f1.reqs == [])
check("the dev phrase does not work for uat", "UAT" in (refused(["reset", "--db", db, "--base", "appL", "--table", "tA", "--config", cfgp, "--send", "--planning-stage", "uat", "--confirm", PHRASE], opener=f1) or "") and f1.reqs == [])
why = refused(["reset", "--db", db, "--base", "appL", "--table", "tA", "--config", other_cfg, "--send", "--confirm", PHRASE], opener=f1)
check("a Workspace in config.json that is not the one this stage was loaded into is refused, even in a dry run", "different Workspace" in (why or "") and f1.reqs == []
      and "different Workspace" in (refused(["reset", "--db", db, "--base", "appL", "--table", "tA", "--config", other_cfg], opener=f1) or ""), why)
check("an unknown table is refused", "not in the database" in (refused(["reset", "--db", db, "--base", "appL", "--table", "tZ", "--config", cfgp], opener=f1) or ""))
check("the Workspace the record type reports is checked too, and nothing is deleted when it differs", "different Workspace" in (refused(["reset", "--db", db, "--base", "appL", "--table", "tA", "--config", cfgp, "--send", "--confirm", PHRASE], opener=Fake(home="wsElsewhere")) or ""))

# ---- the real wipe
fk2 = fk
res = reset(db, "--send", "--confirm", PHRASE, fk=fk2, out="send")
dels = fk2.of("DELETE", "/records/bulk")
check("records are deleted 100 per call with a {ids: [...]} body", [len(b[2]["ids"]) for b in dels] == [100, 100, 50] and all(set(b[2]) == {"ids"} for b in dels), [len(b[2]["ids"]) for b in dels])
check("every delete call is on the record type of tA only; tB's records are untouched", len({b[1].split("/record-types/")[1].split("/")[0] for b in dels}) == 1 and sum(len(v) for v in fk2.store.values()) == 3)
check("the record type and its fields are not deleted", [x for x in fk2.reqs if x[0] == "DELETE" and not x[1].endswith("/records/bulk")] == [])
check("tA's mappings and markers are gone; tB's stay", mapped(db, "tA") == 0 and mapped(db, "tB") == 3
      and rows(db, "select count(*) from record_pass_markers m join source_records s on s.source_record_uid=m.source_record_uid where s.table_id='tA'")[0][0] == 0
      and rows(db, "select count(*) from record_pass_markers m join source_records s on s.source_record_uid=m.source_record_uid where s.table_id='tB'")[0][0] == 3)
check("load_id_map and source_records are exactly as before", rows(db, "select * from load_id_map order by temp_id, environment, workspace_key") == loadmap and rows(db, "select * from source_records order by 1") == sources)
check("the report holds counts only", "250 record(s) deleted in 3 call(s)" in open(os.path.join(tmp, "rs_send", "reset_summary.txt"), encoding="utf-8-sig").read() and res["cleared"]["mappings"] == N)
out_all = "".join(open(os.path.join(tmp, "rs_send", f), encoding="utf-8-sig").read() for f in os.listdir(os.path.join(tmp, "rs_send")))
check("no token, record value or Planning message text is in the report", TOKEN not in out_all and "SECRET VALUE" not in out_all and "Order 1" not in out_all)

# ---- the reload starts clean
fk2.reqs.clear()
loader.main(["records", "--db", db, "--base", "appL", "--out", os.path.join(tmp, "r_again"), "--config", cfgp, "--extract-dir", os.path.join(tmp, "extract"), "--send"], opener=fk2)
sent = sum(len(b[2]) for b in fk2.of("POST", "/records/bulk"))
check("the reload sends exactly tA's %d records again (tB is not sent twice) and creates no record type or field" % N, sent == N and mapped(db, "tA") == N and mapped(db, "tB") == 3
      and not fk2.of("POST", "/record-types") and not fk2.of("POST", "/fields"), sent)

# ---- a wipe that is refused for some records keeps the memory
db2, fk3 = fresh("two")
bad = max(fk3.store.values(), key=len)[:2]
fk3.refuse = set(bad)
res = reset(db2, "--send", "--confirm", PHRASE, fk=fk3, out="partial")
txt = open(os.path.join(tmp, "rs_partial", "reset_summary.txt"), encoding="utf-8-sig").read()
check("when Planning refuses some deletes the table is reported NOT empty and the local memory is kept", res["cleared"] is None and mapped(db2, "tA") == N and "NOT empty" in txt and "RECORD_LOCKED" in txt and "SECRET VALUE" not in txt, txt)
fk3.refuse = set()
res = reset(db2, "--send", "--confirm", PHRASE, fk=fk3, out="partial2")
check("a second reset finishes the job and then clears the memory", res["cleared"] is not None and mapped(db2, "tA") == 0)

# ---- records Planning has but this tool never mapped (a stop before the answer was written) are wiped too
db3, fk4 = fresh("three")
rt = max(fk4.store, key=lambda k: len(fk4.store[k]))
fk4.store[rt] += ["STRAY1", "STRAY2"]
rt_other = [k for k in fk4.store if k != rt][0]
reset(db3, "--send", "--confirm", PHRASE, fk=fk4, out="stray")
check("records that were never mapped are deleted as well, and the other table is untouched", fk4.store[rt] == [] and len(fk4.store[rt_other]) == 3)
sys.exit(0 if ok else 1)
