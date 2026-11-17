"""Tests for planningtransport.py, the plan's link second pass and the runner's `requires`. A fake opener stands in for the network; fabricated names and a fake secret only."""
import io, json, os, sys, urllib.error
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import loadconfig as lc, loadplan as lp, loadrun as lr, workfronthttp as wh, planningtransport as pt
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

SECRET = "fakeBEARER0123456789"
CFG = {"workfront": {"planning": {"base_url": "https://acme.my.workfront.com", "bearer_token": SECRET, "workspace_id": "wsDev", "default_currency": "USD", "currency_by_symbol": {"EUR-sign": "EUR"}}}}
creds = lc.credentials(CFG, "planning")
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
        if r.status >= 300: raise urllib.error.HTTPError(req.full_url, r.status, "x", {}, io.BytesIO(r._b))
        return r
def T(replies):
    h = wh.Http(creds, opener=Opener(replies), sleep=lambda s: None)
    return pt.PlanningTransport(h), h.opener
def item(kind="field", name="Total", dtype="Number", spec=None, **kw):
    return dict({"temp_id": "P-F-f1", "system": "planning", "kind": kind, "parent": "P-T-t1", "name": name, "label": None, "data_type": dtype, "position": None, "description": None, "spec": spec, "requires": []}, **kw)
body = lambda t, i, parent="RT1", refs=None: t.request_for(i, parent, refs)[2]

check("the transport needs a Workspace ID from config.json", "workspace_id" in str(raises(pt.PlanningTransport, wh.Http(lc.credentials({"workfront": {"planning": {"base_url": "https://a.example.com", "bearer_token": SECRET}}}, "planning")))))
t, o = T([Resp(201, {"id": "RT9"})])
check("a record type is created in the configured Workspace, with a Bearer token", t.create(item("record_type", "Orders", None, description="Source: Airtable. Origins: Orders."), None) == "RT9"
      and o.reqs[0].full_url == "https://acme.my.workfront.com/maestro/api/v2/workspaces/wsDev/record-types" and o.reqs[0].get_header("Authorization") == "Bearer " + SECRET
      and json.loads(o.reqs[0].data) == {"displayName": "Orders", "description": "Source: Airtable. Origins: Orders."})
t, o = T([Resp(201, {"id": "F7"})])
check("a text field goes to its record type with displayName, type and description", t.create(item(dtype="Text", name="Notes", description="d"), "RT9") == "F7" and o.reqs[0].full_url.endswith("/v2/record-types/RT9/fields")
      and json.loads(o.reqs[0].data) == {"displayName": "Notes", "type": "text", "description": "d"})
t, _ = T([])
check("number and percentage carry numberOptions, with the precision only when the schema gives one", body(t, item(spec={"precision": 2})) == {"displayName": "Total", "type": "number", "numberOptions": {"allowNegatives": True, "precision": 2}}
      and body(t, item(dtype="Percent"))["numberOptions"] == {"allowNegatives": True} and body(t, item(dtype="Percent", spec={"precision": 0}))["numberOptions"]["precision"] == 0)
check("a currency field needs a code from config.json: the symbol map first, then the default, else a plain refusal", body(t, item(dtype="Currency", spec={"currency_symbol": "EUR-sign"}))["numberOptions"]["currency"] == "EUR"
      and body(t, item(dtype="Currency", spec={"currency_symbol": "$"}))["numberOptions"]["currency"] == "USD")
t2 = pt.PlanningTransport(wh.Http(lc.credentials({"workfront": {"planning": {"base_url": "https://a.example.com", "bearer_token": SECRET, "workspace_id": "w"}}}, "planning")))
check("without any currency setting a currency field is refused in plain words", "default_currency" in str(raises(t2.request_for, item(dtype="Currency"), "RT1")))
check("a date field carries dateOptions and a user field userOptions", body(t, item(dtype="Date"))["dateOptions"] == {"dateFormat": "locale"} and body(t, item(dtype="User"))["userOptions"] == {"multipleUsersAllowed": True})
check("a select field is sent with its ready options in order; with none it is refused", body(t, item(dtype="Single select", spec={"options": ["Open", "Closed"]}))["options"] == [{"displayName": "Open"}, {"displayName": "Closed"}]
      and "at least one option" in str(raises(t.request_for, item(dtype="Single select", spec={"options": []}), "RT1")))
check("a link field points at the record type it needs and is multiple unless Airtable says single, with no back reference", body(t, item(dtype="Link", spec={"single": False}), refs={"P-T-t2": "RT2"})["referenceOptions"] == {"recordTypeId": "RT2", "multiple": True, "createBackReference": False}
      and body(t, item(dtype="Link", spec={"single": True}), refs={"P-T-t2": "RT2"})["referenceOptions"]["multiple"] is False and "second pass" in str(raises(t.request_for, item(dtype="Link"), "RT1")))
check("an unknown type, a missing record type, a form and an over-long description are refused before any call", "not one this loader knows" in str(raises(t.request_for, item(dtype="Quantum"), "RT1")) and "record type's Planning ID" in str(raises(t.request_for, item(), None))
      and "only" in str(raises(t.request_for, item("form"), "RT1")) and "512" in str(raises(t.request_for, item(dtype="Text", description="x" * 513), "RT1")) and body(t, item(dtype="Text", description="x" * 512))["description"] == "x" * 512)
check("rich text is created as long text in Planning", body(t, item(dtype="Rich text"))["type"] == "long-text")
check("describe shows the request and sends nothing, and an option has no call of its own", t.describe(item(), "RT1")["request"] == "POST /v2/record-types/RT1/fields" and t.describe(item("option", "Open", None), "F1") == dict(t.describe(item("option", "Open", None), "F1")) and t.describe(item("option", "Open", None), "F1")["body"] is None)

# ---- options come back with the field
t, o = T([Resp(201, {"id": "F5", "options": [{"displayName": "Open", "name": "key-open"}, {"displayName": "Closed"}]})])
fid = t.create(item(dtype="Single select", name="Status", spec={"options": ["Open", "Closed"]}), "RT1")
check("each option's ID is read from the field's reply (its name, else its displayName), with no extra call", t.create(item("option", "Open", None), fid) == "key-open" and t.create(item("option", "closed", None), fid) == "Closed" and len(o.reqs) == 1)
t, o = T([Resp(200, {"id": "F5", "options": [{"displayName": "Open", "name": "k1"}]})])
check("after a restart the option is read back from the field", t.create(item("option", "Open", None), "F5") == "k1" and o.reqs[0].full_url.endswith("/v2/fields/F5"))
check("an option the field does not hold is an error, not a guess", "not in the field" in str(raises(t.create, item("option", "Ghost", None), "F5")))

# ---- errors and adoption
t, _ = T([Resp(403, b'{"errorCode":"FIELD_DISPLAY_NAME_NOT_UNIQUE","message":"x"}')])
check("a duplicate field name is a name conflict", isinstance(raises(t.create, item(dtype="Text"), "RT1"), lr.NameConflict))
t, _ = T([Resp(409, b"x")])
check("a 409 on a record type is a name conflict", isinstance(raises(t.create, item("record_type", "Orders", None), None), lr.NameConflict))
t, _ = T([Resp(201, {})])
check("a reply without an id is an error", "holds no ID" in str(raises(t.create, item("record_type", "Orders", None), None)))
t, o = T([Resp(200, {"content": [{"id": "B", "displayName": "Orders"}, {"id": "C", "displayName": "orders"}], "cursor": {"hasMore": True, "nextCursor": "n1"}}),
          Resp(200, {"content": [{"id": "A", "displayName": "Orders"}], "cursor": {"hasMore": False}})])
check("finding an existing record type reads every page and matches the name exactly, sorted", t.find_existing(item("record_type", "Orders", None), None) == ["A", "B"] and "cursor=n1" in o.reqs[1].full_url and "limit=100" in o.reqs[0].full_url)
t, o = T([Resp(200, {"content": [{"id": "F1", "displayName": "Total"}], "cursor": {"hasMore": False}})])
check("finding an existing field is by its record type", t.find_existing(item(), "RT1") == ["F1"] and "/v2/record-types/RT1/fields" in o.reqs[0].full_url and t.find_existing(item("option", "x", None), "F1") == [])

# ---- the plan: second pass for links
def tr(name): return {"candidate": name, "status": "approved", "approved_name": name}
def row(fid, table, name, typ="singleLineText", tt="Text"):
    return {"field_id": fid, "table_id": table, "table_name": "T", "original": name, "data_type": typ, "abandoned": False, "consolidated": None, "target_type": tt,
            "description": {"text": "Source: Airtable. Origins: T.%s." % name}, "targets": {"planning_name": tr(name), "core_name": tr(name), "core_label": tr(name + " label")}}
tbl = lambda tid, nm, scope="in", target="planning_only": {"table_id": tid, "old": nm, "scope": scope, "target": target, "approved": True, "effective": nm}
RV = {"settings": None, "tables": [tbl("tA", "Orders"), tbl("tB", "Customers"), tbl("tX", "Dropped", "out")],
      "fields": [row("a1", "tA", "Title"), row("a2", "tA", "Customer", "multipleRecordLinks", "Link"), row("a3", "tA", "Status", "singleSelect", "Single select"), row("b1", "tB", "Name"), row("b2", "tB", "Orders", "multipleRecordLinks", "Link"),
                 row("a4", "tA", "Gone", "multipleRecordLinks", "Link")]}
CH = {"a3": [{"id": "s1", "name": "Open"}, {"id": "s2", "name": "Closed"}]}
FACTS = {"a2": {"linked_table_id": "tB", "single": True}, "b2": {"linked_table_id": "tA", "single": False}, "a4": {"linked_table_id": "tX", "single": False}}
p = lp.build_plan(RV, CH, link_state={"a2": True, "b2": True, "a4": True}, field_facts=FACTS)
I = {i["temp_id"]: i for i in p["items"]}
check("a confirmed link between planned tables is ready for the second pass and needs the other record type", I["P-F-a2"]["status"] == "ready" and I["P-F-a2"]["requires"] == ["P-T-tB"] and I["P-F-a2"]["pass"] == "links"
      and I["P-F-b2"]["requires"] == ["P-T-tA"], I["P-F-a2"])
check("a link to a table that is out of scope stays on hold", I["P-F-a4"]["status"] == "hold" and not I["P-F-a4"]["requires"])
check("every link comes after every record type and ordinary field and option", [i["temp_id"] for i in p["items"]][-2:] == ["P-F-a2", "P-F-b2"] and all(i["pass"] is None for i in p["items"] if i["temp_id"] not in ("P-F-a2", "P-F-b2")))
check("an undecided direction is still blocked and no facts means still on hold", lp.build_plan(RV, CH, link_state={"a2": False}, field_facts=FACTS)["items"] and {i["temp_id"]: i for i in lp.build_plan(RV, CH, link_state={"a2": False}, field_facts=FACTS)["items"]}["P-F-a2"]["status"] == "blocked"
      and {i["temp_id"]: i for i in lp.build_plan(RV, CH, link_state={"a2": True})["items"]}["P-F-a2"]["status"] == "hold")
check("a select field's spec carries its ready options in order", I["P-F-a3"]["spec"]["options"] == ["Open", "Closed"])

# ---- the runner: parents, then links in the second pass
class Rec(object):
    def __init__(self, system): self.system, self.calls, self.n = system, [], 0
    def create(self, item, parent_id, refs=None):
        self.n += 1; self.calls.append((item["temp_id"], parent_id, dict(refs or {}))); return "wf%d" % self.n
    def describe(self, item, parent_id, refs=None): return {"refs": dict(refs or {})}
env = lr.resolve_environment("dev", None, False)
rec, st = Rec("planning"), lr.MemoryState()
res = lr.run_plan([i for i in p["items"] if i["system"] == "planning"], st, {"planning": rec}, env, dry_run=False)
calls = {c[0]: c for c in rec.calls}
check("the runner passes a link the IDs of the record types it needs, after they are created", calls["P-F-a2"][2] == {"P-T-tB": st.get("P-T-tB", "dev")} and calls["P-F-a2"][1] == st.get("P-T-tA", "dev") and [c[0] for c in rec.calls].index("P-F-a2") > [c[0] for c in rec.calls].index("P-T-tB"))
class Fail(Rec):
    def create(self, item, parent_id, refs=None):
        if item["temp_id"] == "P-T-tB": raise RuntimeError("nope")
        return Rec.create(self, item, parent_id, refs)
res = lr.run_plan([i for i in p["items"] if i["system"] == "planning"], lr.MemoryState(), {"planning": Fail("planning")}, env, dry_run=False)
out = {r["temp_id"]: r for r in res["results"]}
check("a link whose target record type failed is skipped, not guessed", out["P-F-a2"]["outcome"] == "parent_missing" and out["P-F-a2"]["detail"] == "P-T-tB" and out["P-T-tB"]["outcome"] == "failed")
dry = lr.run_plan([i for i in p["items"] if i["system"] == "planning"], lr.MemoryState(), {"planning": Rec("planning")}, env, dry_run=True)
check("a dry run shows a link's needs as 'would be created'", {r["temp_id"]: r for r in dry["results"]}["P-F-a2"]["detail"]["refs"] == {"P-T-tB": "<new P-T-tB>"})

print("ALL PASS" if ok else "FAILURES")
sys.exit(0 if ok else 1)
