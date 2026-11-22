"""A FAKE Workfront Core for the calculated fields push tests: custom fields (PARAM), custom forms (CTGY) with their field-on-form associations (CTGYPA), and the expression of an
association. Fabricated names, a fake key. It models what the push depends on: a nested collection is REPLACED as a whole by a PUT (a setting left out of a sent association is lost),
a created field has the dataType and displayType it was given, and an association keeps its ID while its field stays on the form.

    fake = FakeWorkfront(KEY)
    fake.add_form("CAT1", "Special Order Sales", [{"parameterID": "X1", "displayOrder": 1, "parameterGroupID": "G1", "rowShared": True}])
    fake.handle(method, path, query, body, key) -> (status, body)         # path relative to the API root
    Opener(fake, api)                                                      # an urllib-style opener for workfronthttp.Http
Failure switches: refuse_create {name: (status, body)}, fail_put_form (status), fail_get_form (status), fail_expression {ctgypa: (status, body)}, read_back (function: expression -> text),
put_changes {parameterID: {key: value}} (the form changes a setting while saving), delay_hook (called before each request).
"""
import io, json, threading, urllib.error, urllib.parse


class Resp(object):
    def __init__(self, status, body):
        self.status, self._b, self.headers = status, json.dumps(body).encode(), {}
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


class FakeWorkfront(object):
    def __init__(self, key):
        self.key, self.lock = key, threading.RLock()
        self.params, self.forms, self.n_param, self.n_assoc, self.reqs = {}, {}, 0, 0, []
        self.refuse_create, self.fail_put_form, self.fail_get_form, self.fail_expression = {}, None, None, {}
        self.read_back, self.put_changes, self.hook = None, {}, None

    # ---- setup helpers
    def add_param(self, name, data_type="NMBR", display_type="CALC"):
        self.n_param += 1
        pid = "P%04d" % self.n_param
        self.params[pid] = {"ID": pid, "name": name, "dataType": data_type, "displayType": display_type, "lastUpdateDate": "2026-11-22 08:00:00"}
        return pid

    def add_form(self, cid, name, assocs=()):
        self.forms[cid] = {"ID": cid, "name": name, "assoc": []}
        for a in assocs:
            self.n_assoc += 1
            self.forms[cid]["assoc"].append(dict(a, ID="A%04d" % self.n_assoc))

    def find_assoc(self, ctgypa):
        for f in self.forms.values():
            for a in f["assoc"]:
                if a["ID"] == ctgypa:
                    return a
        return None

    # ---- the API
    def handle(self, m, path, q, body, key):
        with self.lock:
            self.reqs.append((m, path, dict(q), body))
            if self.hook:
                self.hook()
            if key != self.key:
                return 401, {"error": {"class": "InvalidKeyException", "message": "bad key"}}
            if m == "GET" and path == "/param/search":
                return 200, {"data": [dict(p) for p in self.params.values() if p["name"].casefold() == q.get("name", "").casefold()]}
            if m == "POST" and path == "/param":
                if body["name"] in self.refuse_create:
                    st, b = self.refuse_create[body["name"]]
                    return st, b
                pid = self.add_param(body["name"], body.get("dataType"), body.get("displayType"))
                return 201, {"data": {"ID": pid, "lastUpdateDate": self.params[pid]["lastUpdateDate"]}}
            if path.startswith("/CTGY/"):
                f = self.forms.get(path.rsplit("/", 1)[1])
                if f is None:
                    return 404, {"error": {"class": "ObjectNotFoundException", "message": "no such form"}}
                if m == "GET":
                    if self.fail_get_form:
                        return self.fail_get_form, {"error": {"class": "ServerError", "message": "cannot read"}}
                    return 200, {"data": {"ID": f["ID"], "name": f["name"], "categoryParameters": [dict(a) for a in f["assoc"]]}}
                if m == "PUT":
                    if self.fail_put_form:
                        return self.fail_put_form, {"error": {"class": "ServerError", "message": "cannot save"}}
                    old = {a["parameterID"]: a for a in f["assoc"]}
                    new = []
                    for e in body.get("categoryParameters") or []:
                        e = dict(e, **self.put_changes.get(e["parameterID"], {}))
                        if e["parameterID"] in old:
                            keep = {k: v for k, v in old[e["parameterID"]].items() if k in ("ID", "rawCustomExpression")}
                            new.append(dict(e, **keep))
                        else:
                            self.n_assoc += 1
                            new.append(dict(e, ID="A%04d" % self.n_assoc))
                    f["assoc"] = new                      # a collection is replaced as a whole: what was not sent is gone
                    return 200, {"data": {"ID": f["ID"]}}
            if m == "PUT" and path.startswith("/CTGYPA/"):
                cid = path.rsplit("/", 1)[1]
                if cid in self.fail_expression:
                    return self.fail_expression[cid]
                a = self.find_assoc(cid)
                if a is None:
                    return 404, {"error": {"class": "ObjectNotFoundException", "message": "no such association"}}
                a["rawCustomExpression"] = self.read_back(body["rawCustomExpression"]) if self.read_back else body["rawCustomExpression"]
                return 200, {"data": {"ID": cid}}
            return 404, {"error": {"class": "NotFound", "message": "no route"}}


class Opener(object):
    def __init__(self, fake, api):
        self.fake, self.api = fake, api

    def open(self, req, timeout=None):
        u = urllib.parse.urlparse(req.full_url)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        body = json.loads(req.data) if req.data else None
        hdr = {k.lower(): v for k, v in req.header_items()}
        status, out = self.fake.handle(req.get_method(), u.path[len(self.api):], q, body, hdr.get("apikey"))
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "x", None, io.BytesIO(json.dumps(out).encode()))
        return Resp(status, out)


# ------------------------------------------------------------------ the fake served over HTTP, for the tests that drive the real server
API = "/attask/api/v21.0"


def start(fake, port):
    """Serve `fake` on 127.0.0.1:port (the API root is /attask/api/v21.0). Returns the server; call .shutdown() when done."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _do(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n)) if n else None
            u = urllib.parse.urlparse(self.path)
            code, out = fake.handle(self.command, u.path[len(API):], {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}, body, self.headers.get("apiKey"))
            data = json.dumps(out).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        do_GET = do_POST = do_PUT = do_DELETE = _do
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def set_config(port, key, envs=("sandbox1", "production")):
    """Add the Core settings for `envs` (all pointing at the fake) to config.json in the current folder; returns the original text so the caller can put it back."""
    orig = open("config.json", encoding="utf-8").read()
    cfg = json.loads(orig)
    cfg["workfront"] = {"core": {e: {"base_url": "http://127.0.0.1:%d" % port, "api_path": API, "api_key": key} for e in envs}}
    json.dump(cfg, open("config.json", "w"))
    return orig
