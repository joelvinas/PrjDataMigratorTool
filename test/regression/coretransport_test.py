"""Tests for typemap.py, the new loadconfig settings, workfronthttp.py and coretransport.py. A fake opener stands in for the network; fabricated names and a fake secret only."""
import io, json, os, sys, urllib.error
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))
import loadconfig as lc, loadrun as lr, typemap as tm, workfronthttp as wh, coretransport as ct
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])
def raises(fn, *a, **k):
    try:
        fn(*a, **k); return None
    except Exception as e:
        return e

SECRET = "fakeKEY0123456789abcdef"
# ---- type mapping
check("data type texts are matched ignoring case, hyphens and spacing", [tm.kind(x) for x in ("Single-line text", "SINGLE LINE TEXT", "long_text", "Multiple selects", "Link to another record", "Checkbox", "Date time")]
      == ["text", "text", "long-text", "multi-select", "reference", "boolean", "datetime"])
check("an unknown data type is never guessed", tm.kind("Quantum") is None and tm.kind(None) is None and tm.planning_type("Quantum") == (None, None))
check("Core: text and long text use the verified displayTypes, any data type blends with them", tm.core_type("Text") == ("TEXT", "TEXT", True) and tm.core_type("Long text") == ("TEXT", "TXTA", True)
      and tm.core_type("Number") == ("NMBR", "TEXT", True) and tm.core_type("Currency") == ("CURC", "TEXT", True) and tm.core_type("Date")[0] == "DATE" and tm.core_type("Date time")[0] == "DTTM")
check("Core: select, multiple select and checkbox are mapped but marked unverified", [tm.core_type(x)[1:] for x in ("Single select", "Multiple select", "Checkbox")] == [("SLCT", False), ("MULT", False), ("CHCK", False)])
msg = lambda x: str(raises(tm.core_type, x))
check("Core: rich text cannot be created through the API and says so; a link and an unknown type are refused plainly", "custom form viewer" in msg("Rich text") and "Planning connection" in msg("Link") and "not one this loader knows" in msg("Quantum"))
check("Planning: the v2 field types", [tm.planning_type(x)[0] for x in ("Text", "Long text", "Number", "Percent", "Currency", "Date", "Date time", "Single select", "Multiple select", "Checkbox", "Link", "User", "Rich text")]
      == ["text", "long-text", "number", "percentage", "currency", "date", "date", "single-select", "multi-select", "boolean", "reference", "user", "long-text"])

# ---- config
def cfg(**core):
    return {"workfront": {"core": {"sandbox1": dict({"base_url": "https://acme.sb01.workfront.com", "api_key": SECRET}, **core)},
                          "planning": {"base_url": "https://acme.my.workfront.com", "bearer_token": SECRET + "P", "workspace_id": "ws1", "default_currency": "USD"}}}
c1 = lc.credentials(cfg(api_path="attask/api/v21.0/"), "core", "sandbox1")
check("the Core api_path is read and tidied; Planning defaults to the documented /maestro/api", c1.api_path == "/attask/api/v21.0" and lc.credentials(cfg(), "planning").api_path == "/maestro/api" and lc.credentials(cfg(), "core", "sandbox1").api_path is None)
check("other settings are kept as extras, the secret and the address are not", lc.credentials(cfg(), "planning").extra == {"default_currency": "USD"} and "api_key" not in c1.extra and SECRET not in repr(c1))
check("a local test server may use plain http; any other address must be https", lc.credentials({"workfront": {"planning": {"base_url": "http://127.0.0.1:9201", "bearer_token": SECRET}}}, "planning") is not None
      and lc.credentials({"workfront": {"planning": {"base_url": "http://example.com", "bearer_token": SECRET}}}, "planning") is None)
check("the Core transport refuses to start without an api_path, in plain words", "api_path" in str(raises(ct.CoreTransport, wh.Http(lc.credentials(cfg(), "core", "sandbox1")))))

# ---- the HTTP layer with a fake opener
class Resp(object):
    def __init__(self, status, body): self.status, self._b = status, body if isinstance(body, bytes) else json.dumps(body).encode()
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False
class Opener(object):
    def __init__(self, replies): self.replies, self.reqs = list(replies), []
    def open(self, req, timeout=None):
        self.reqs.append(req)
        r = self.replies.pop(0)
        if isinstance(r, Exception): raise r
        if r.status >= 300: raise urllib.error.HTTPError(req.full_url, r.status, "x", {}, io.BytesIO(r._b))
        return r
def http(replies, creds=None):
    waits = []
    h = wh.Http(creds or c1, opener=Opener(replies), sleep=waits.append)
    return h, waits
h, w = http([Resp(200, {"data": {"ID": "P1"}})])
check("a call goes to base + api path + route, with the secret in the header only", h.request("POST", "/param", {"name": "x"}) == {"data": {"ID": "P1"}} and h.opener.reqs[0].full_url == "https://acme.sb01.workfront.com/attask/api/v21.0/param"
      and h.opener.reqs[0].get_header("Apikey") == SECRET and SECRET not in h.opener.reqs[0].full_url and json.loads(h.opener.reqs[0].data) == {"name": "x"})
hp = wh.Http(lc.credentials(cfg(), "planning"), opener=Opener([Resp(200, {})]))
hp.request("GET", "/v2/workspaces", None, {"limit": 100, "cursor": "a b"})
check("a Planning call sends a Bearer token and a sorted, encoded query", hp.opener.reqs[0].get_header("Authorization") == "Bearer " + SECRET + "P" and hp.opener.reqs[0].full_url.endswith("/maestro/api/v2/workspaces?cursor=a+b&limit=100"))
h, w = http([Resp(503, b"busy"), Resp(429, b"slow"), Resp(200, {"ok": True})])
check("a busy server is tried again with a growing wait", h.request("GET", "/x") == {"ok": True} and w == [1, 2] and h.calls == 3)
h, w = http([Resp(503, b"a")] * 4)
e = raises(h.request, "GET", "/x")
check("after the last try it stops with the status", isinstance(e, wh.HttpError) and e.status == 503 and w == [1, 2, 4] and h.calls == 4)
h, w = http([Resp(400, ('{"error":"bad Authorization: Bearer abc.def.ghi token=%s"}' % SECRET).encode())])
e = raises(h.request, "POST", "/param", {})
check("a refusal is not tried again, and no secret survives in its message", e.status == 400 and not w and SECRET not in str(e) and "abc.def.ghi" not in str(e) and "[removed]" in str(e), str(e))
h, w = http([urllib.error.URLError("no route"), Resp(200, {"a": 1})])
check("a dropped connection is tried again", h.request("GET", "/x") == {"a": 1} and w == [1])
h, w = http([Resp(302, b"")])
check("a redirect is not followed", "redirect" in str(raises(h.request, "GET", "/x")) and not w)
check("an empty reply is None and a non-JSON reply is an error", http([Resp(204, b"")])[0].request("DELETE", "/x") is None and "not JSON" in str(raises(http([Resp(200, b"<html>")])[0].request, "GET", "/x")))

# ---- the Core transport
def item(kind="field", name="Order Total", dtype="Number", **kw):
    return dict({"temp_id": "C-F-f1", "system": "core", "kind": kind, "parent": None, "name": name, "label": "Order Total label", "data_type": dtype, "position": None, "description": None}, **kw)
h, _ = http([Resp(200, {"data": {"ID": "PARAM9", "name": "Order Total"}})])
t = ct.CoreTransport(h)
check("a field is created with name, dataType and displayType, and the new ID comes back", t.create(item(), None) == "PARAM9" and h.opener.reqs[0].full_url.endswith("/attask/api/v21.0/param")
      and json.loads(h.opener.reqs[0].data) == {"name": "Order Total", "dataType": "NMBR", "displayType": "TEXT", "label": "Order Total label"})
check("a description is sent when there is one", t.request_for(item(description="Source: Airtable. Origins: A.B."), None)[2]["description"].startswith("Source:"))
check("a long text field is a text area", t.request_for(item(dtype="Long text"), None)[2]["displayType"] == "TXTA")
o = item("option", "Open", None, position=2)
check("an option is created against its field's Core ID with its order", t.request_for(o, "PARAM9") == ("POST", "/popt", {"parameterID": "PARAM9", "label": "Open", "value": "Open", "displayOrder": 2}))
check("an option without its field, a rich text field, a form and an unknown type are refused in plain words, before any call",
      "field's Core ID" in str(raises(t.request_for, o, None)) and "custom form viewer" in str(raises(t.request_for, item(dtype="Rich text"), None)) and "by hand" in str(raises(t.request_for, item("form"), None))
      and "not one this loader knows" in str(raises(t.request_for, item(dtype="Quantum"), None)) and len(h.opener.reqs) == 1)
d = t.describe(item(), None)
check("describe shows the request and sends nothing", d["request"] == "POST /param" and d["body"]["dataType"] == "NMBR" and len(h.opener.reqs) == 1)
h, _ = http([Resp(200, {"data": {}})])
check("a reply with no ID is an error, not a silent success", "holds no ID" in str(raises(ct.CoreTransport(h).create, item(), None)))
h, _ = http([Resp(409, b'{"error":"name taken"}')])
check("a 409 is reported as a name conflict", isinstance(raises(ct.CoreTransport(h).create, item(), None), lr.NameConflict))
h, _ = http([Resp(400, b'{"error":{"message":"A field named Order Total already exists"}}')])
check("a message saying the name already exists is a name conflict too", isinstance(raises(ct.CoreTransport(h).create, item(), None), lr.NameConflict))
h, _ = http([Resp(500, b"boom")] * 4)
check("any other failure is an ordinary error", not isinstance(raises(ct.CoreTransport(h).create, item(), None), lr.NameConflict))
h, _ = http([Resp(200, {"data": [{"ID": "B", "name": "Order Total"}, {"ID": "A", "name": "Order Total"}, {"ID": "C", "name": "Order Totals"}]})])
check("finding an existing field returns the IDs whose name matches exactly, sorted", ct.CoreTransport(h).find_existing(item(), None) == ["A", "B"] and "name=Order+Total" in h.opener.reqs[0].full_url)
h, _ = http([Resp(200, {"data": [{"ID": "O1", "label": "Open"}, {"ID": "O2", "label": "Closed"}]})])
check("finding an existing option is by its field and its label", ct.CoreTransport(h).find_existing(o, "PARAM9") == ["O1"] and "parameterID=PARAM9" in h.opener.reqs[0].full_url)

# ---- the runner with this transport: a stop, a rerun and adoption
class Mem(lr.MemoryState): pass
st = Mem(); env = lr.resolve_environment("sandbox1", None, False)
h, _ = http([Resp(200, {"data": {"ID": "NEW1"}})])
r = lr.run_plan([dict(item(), status="ready", reasons=[])], st, {"core": ct.CoreTransport(h)}, env, dry_run=False)
check("the runner records the ID the Core transport returns", r["counts"] == {"created": 1} and st.get("C-F-f1", "sandbox1") == "NEW1")
st2 = Mem(); st2.begin(item(), "sandbox1")
h, _ = http([Resp(409, b"exists"), Resp(200, {"data": [{"ID": "OLD7", "name": "Order Total"}]})])
r = lr.run_plan([dict(item(), status="ready", reasons=[])], st2, {"core": ct.CoreTransport(h)}, env, dry_run=False)
check("after an interrupted attempt, a name conflict adopts the one existing field", r["counts"] == {"adopted": 1} and st2.get("C-F-f1", "sandbox1") == "OLD7", r)

print("ALL PASS" if ok else "FAILURES")
sys.exit(0 if ok else 1)
