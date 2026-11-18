"""Shared fixture for the Load a table tests (loadtable_api_test.py, loadtable_ui_test.py): a fake Workfront Planning served from the test script, a fabricated database with
five tables, and fabricated saved record files. Fake token; fabricated names; no network beyond 127.0.0.1."""
import base64, json, os, sqlite3, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
import fieldnames_store as st, linkdirections as ld
BASE = "appLoadTab0001"
TOKEN = "fakePLANtoken0123456789"
PORT = 9101
DBF = "loadtable.db"

# ------------------------------------------------------------------ a fake Workfront Planning
class Fake(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()
    def reset(self):
        self.reqs, self.n, self.r, self.store, self.refuse_fields, self.delay, self.bad_auth, self.reject = [], 0, 0, {}, set(), 0.0, 0, False
        self.workspaces = {"wsDev": "Rehearsal Workspace", "wsProd": "Live Workspace"}      # a Workspace ID that is not here answers 404
    def handle(self, m, path, body, auth):
        with self.lock:
            self.reqs.append((m, path, body))
            if self.reject:                                               # an expired token: refused, and not counted as a mistake of the app
                return 401, {"error": "token expired"}
            if auth != "Bearer " + TOKEN:
                self.bad_auth += 1
                return 401, {"error": "no"}
            u = path.split("?")[0]
            self.n += 1
            if m == "GET" and u.rsplit("/", 2)[-2] == "workspaces":
                wid = u.rsplit("/", 1)[-1]
                return (200, {"id": wid, "name": self.workspaces[wid]}) if wid in self.workspaces else (404, {"error": "NOT_FOUND"})
            if m == "POST" and u.endswith("/record-types"): return 201, {"id": "RT%d" % self.n, "displayName": body["displayName"]}
            if m == "POST" and u.endswith("/fields"):
                if body["displayName"] in self.refuse_fields: return 400, {"error": "VALIDATION_FAILED for Bearer " + TOKEN}
                return 201, {"id": "FLD%d" % self.n, "displayName": body["displayName"]}
            if m == "POST" and u.endswith("/records/bulk"):
                rt, out = u.split("/record-types/")[1].split("/")[0], []
                for i, _ in enumerate(body):
                    self.r += 1; rid = "REC%04d" % self.r; self.store.setdefault(rt, []).append(rid); out.append({"requestIndex": i, "id": rid})
                delay = self.delay
            elif m == "GET" and "/record-types/" in u and u.endswith("/records"):
                rt = u.split("/record-types/")[1].split("/")[0]; rows = self.store.get(rt, [])
                return 200, {"content": [{"id": r, "data": {"f": "SECRETVALUE"}} for r in rows[:100]], "totalElements": len(rows)}
            elif m == "GET" and u.rsplit("/", 2)[-2] == "record-types": return 200, {"id": u.rsplit("/", 1)[-1], "primaryWorkspaceId": "wsDev"}
            elif m == "DELETE" and u.endswith("/records/bulk"):
                rt = u.split("/record-types/")[1].split("/")[0]; res = []
                for i, rid in enumerate(body["ids"]): self.store[rt].remove(rid); res.append({"requestIndex": i, "id": rid, "status": "success"})
                return 200, {"status": "success", "total": len(res), "results": res}
            elif m == "PATCH" and "/v2/records/" in u: return 200, {}
            else: return 404, {}
        time.sleep(delay)                                           # only the bulk create is slowed, outside the lock
        return 201, {"status": "success", "total": len(out), "results": out}
    def posts(self, tail): return [x for x in self.reqs if x[0] == "POST" and x[1].split("?")[0].endswith(tail)]
    def made(self, name): return [x for x in self.reqs if x[0] == "POST" and x[2] and isinstance(x[2], dict) and x[2].get("displayName") == name]
fake = Fake()
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _do(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        path = self.path[len("/maestro/api"):] if self.path.startswith("/maestro/api") else self.path
        code, out = fake.handle(self.command, path, body, self.headers.get("Authorization"))
        data = json.dumps(out).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    do_GET = do_POST = do_PATCH = do_DELETE = _do
def start():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H); srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def set_config():
    """Add the Planning settings (pointing at the fake) to config.json; returns the original text so the caller can put it back."""
    orig = open("config.json", encoding="utf-8").read()
    cfg = json.loads(orig)
    one = lambda ws: {"base_url": "http://127.0.0.1:%d" % PORT, "bearer_token": TOKEN, "workspace_id": ws, "record_batch_size": 2}
    cfg["workfront"] = {"planning": {"DEV": one("wsDev"), "UAT": one("wsMissing"), "production": one("wsProd")}}      # the user's own stage blocks; UAT points at a Workspace that does not exist
    json.dump(cfg, open("config.json", "w"))
    return orig



def build():
    """Create the fabricated database and the saved record files in the current folder."""
    if os.path.exists(DBF):
        os.remove(DBF)
    c = sqlite3.connect(DBF); c.row_factory = sqlite3.Row
    c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
    for tid, nm in (("tA", "Orders"), ("tB", "Customers"), ("tC", "Notes"), ("tD", "Hidden"), ("tE", "Pending")):
        c.execute("insert into airtable_tables(table_id, base_id, table_name) values (?,?,?)", (tid, BASE, nm))
    F = [("fA1", "tA", "Orders", "Order Name", "singleLineText", None), ("fA4", "tA", "Orders", "Customer", "multipleRecordLinks", "tB"),
         ("fB1", "tB", "Customers", "Customer Name", "singleLineText", None), ("fB2", "tB", "Customers", "Note Link", "multipleRecordLinks", "tC"),
         ("fC1", "tC", "Notes", "Note Text", "singleLineText", None), ("fD1", "tD", "Hidden", "Secret Name", "singleLineText", None), ("fE1", "tE", "Pending", "Pending Name", "singleLineText", None)]
    for r in F:
        c.execute("insert into airtable_fields(base_id, table_id, table_name, field_id, field_name, data_type, linked_table_id, prefers_single_record_link, updated_at) values (?,?,?,?,?,?,?,1,'2026-01-01 10:00:00.000000')", (BASE, r[1], r[2], r[0], r[3], r[4], r[5]))
    c.commit()
    st.approve_tables(c, BASE, "JV", mode="unchanged"); st.approve_tables(c, BASE, "JV", mode="auto")
    st.set_scope(c, BASE, "JV", [{"table_id": t, "decision": "in", "target": "planning_only"} for t in ("tA", "tB", "tC", "tE")] + [{"table_id": "tD", "decision": "out", "target": None, "reason": "not part of the rehearsal"}])
    csvt = "field_id,old_name,new_name,new_label,old_dataType,new_dataType\r\n" + "".join("%s,%s,%s,%s,x,%s\r\n" % (r[0], r[3], r[3], r[3], "Link" if r[4] == "multipleRecordLinks" else "Text") for r in F[:-2])
    st.import_names(c, BASE, "JV", csv_base64=base64.b64encode(csvt.encode()).decode())
    st.approve_fields(c, BASE, "JV", mode="pass_and_auto")
    ld.sync(c, [r for r in c.execute("select * from airtable_fields")])
    ld.decide(c, "JV", "fA4", "many_to_one", None, "orders belong to one customer")
    ld.decide(c, "JV", "fB2", "many_to_one", None, "customers have one note")
    c.close()
    ex = os.path.join("data_extract", BASE); os.makedirs(ex, exist_ok=True)
    def save(tid, recs):
        json.dump({"format": "airtable-table-extract/1", "base_id": BASE, "table_id": tid, "table_name": tid, "records": recs}, open(os.path.join(ex, tid + ".json"), "w"))
    save("tA", [{"id": "recA%d" % i, "fields": {"fA1": "SECRETVALUE order %d" % i, "fA4": ["recB%d" % (i % 3 + 1)]}} for i in range(1, 6)])
    save("tB", [{"id": "recB%d" % i, "fields": {"fB1": "SECRETVALUE customer %d" % i, "fB2": ["recC1"]}} for i in range(1, 4)])
    save("tC", [{"id": "recC1", "fields": {"fC1": "SECRETVALUE note"}}, {"id": "recC2", "fields": {"fC1": "SECRETVALUE note 2"}}])

