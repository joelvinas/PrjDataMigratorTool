"""Shared fixture for the Core bulk field tests (corefields_api_test.py, corefields_ui_test.py): a FAKE Workfront Core served from the test script, a fabricated database (without
draft migration 026) and the Core settings in config.json pointing at the fake. Fake key; fabricated names; no network beyond 127.0.0.1."""
import json, os, sqlite3, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEY = "fakeCOREkey0123456789abcdef"
PORT = 9102
DBF = "corefields.db"

# ------------------------------------------------------------------ a fake Workfront Core
class Fake(object):
    def __init__(self):
        self.lock, self.params, self.n, self.reqs, self.refuse = threading.Lock(), {}, 0, [], {}
    def handle(self, m, path, q, body, key):
        with self.lock:
            self.reqs.append((m, path))
            if key != KEY: return 401, {"error": "bad key"}
            if m == "GET" and path == "/param/search":
                return 200, {"data": [{k: p[k] for k in ("ID", "name", "lastUpdateDate")} for p in self.params.values() if p["name"].casefold() == q["name"].casefold()]}
            if m == "POST" and path == "/param":
                if body["name"] in self.refuse: return 400, {"error": {"class": "DuplicateNameException", "message": self.refuse[body["name"]]}}
                self.n += 1; pid = "P%04d" % self.n
                self.params[pid] = dict(body, ID=pid, lastUpdateDate="2026-11-22 09:%02d:00" % self.n)
                return 201, {"data": {"ID": pid, "lastUpdateDate": self.params[pid]["lastUpdateDate"]}}
            if m == "DELETE" and path.startswith("/param/"):
                self.params.pop(path.rsplit("/", 1)[1], None); return 200, {"data": {"success": True}}
            return 404, {}
fake = Fake()
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _do(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        u = urllib.parse.urlparse(self.path)
        code, out = fake.handle(self.command, u.path[len("/attask/api/v21.0"):], {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}, body, self.headers.get("apiKey"))
        data = json.dumps(out).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    do_GET = do_POST = do_DELETE = _do
def start():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H); srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def build():
    """The fabricated database: the library schema (no migration 026), one table and two fields."""
    if os.path.exists(DBF): os.remove(DBF)
    c = sqlite3.connect(DBF)
    c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
    c.execute("insert into airtable_tables(table_id, base_id, table_name) values ('tblOrd', 'appSampleBase001', 'Orders')")
    for fid, nm in (("fldA1", "Colour"), ("fldB2", "Budget")):
        c.execute("insert into airtable_fields(base_id, table_id, table_name, field_id, field_name, data_type, updated_at) values ('appSampleBase001','tblOrd','Orders',?,?, 'singleLineText', '2026-01-01 10:00:00.000000')", (fid, nm))
    c.commit(); c.close()


blk = lambda: {"base_url": "http://127.0.0.1:%d" % PORT, "api_path": "/attask/api/v21.0", "api_key": KEY}


def set_config():
    """Add the Core settings (sandbox1 and production, both pointing at the fake) to config.json; returns the original text so the caller can put it back."""
    orig = open("config.json", encoding="utf-8").read()
    cfg = json.loads(orig)
    cfg["workfront"] = {"core": {"sandbox1": blk(), "production": blk()}}
    json.dump(cfg, open("config.json", "w"))
    return orig

