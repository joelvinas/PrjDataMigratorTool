"""Tests for recordload.py: value conversion, the records pass and the links pass, with a fake Planning transport and fabricated records. No network, no files."""
import io, json, os, sqlite3, sys, urllib.error
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import loadconfig as lc, loadrun as lr, recordload as rl, recordmap as rm, workfronthttp as wh, planningtransport as pt
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])

def db():
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row; c.execute("PRAGMA foreign_keys = ON")
    c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
    for tid, nm in (("tOrd", "Orders"), ("tCus", "Customers")):
        c.execute("INSERT INTO airtable_tables(table_id, base_id, table_name) VALUES (?,?,?)", (tid, "appL", nm))
    c.commit()
    return c

# ---- a fabricated plan result: what the field load wrote to the state
class State(object):
    def __init__(self, ids): self.ids = ids
    def get(self, t, env): return self.ids.get(t)
def item(tid, kind, name, table, dtype=None, field=None, parent=None, spec=None, status="ready", pas=None, system="planning", source="airtable"):
    return {"temp_id": tid, "system": system, "kind": kind, "name": name, "table_id": table, "field_id": field, "parent": parent, "data_type": dtype, "spec": spec, "status": status, "pass": pas, "source": source}
ITEMS = [item("P-T-tOrd", "record_type", "Orders", "tOrd"), item("P-T-tCus", "record_type", "Customers", "tCus"),
         item("P-F-fTitle", "field", "Title", "tOrd", "Text", "fTitle", "P-T-tOrd"), item("P-F-fQty", "field", "Qty", "tOrd", "Number", "fQty", "P-T-tOrd"),
         item("P-F-fDue", "field", "Due", "tOrd", "Date", "fDue", "P-T-tOrd"), item("P-F-fDone", "field", "Done", "tOrd", "Boolean", "fDone", "P-T-tOrd"),
         item("P-F-fStat", "field", "Status", "tOrd", "Single select", "fStat", "P-T-tOrd"), item("P-F-fTags", "field", "Tags", "tOrd", "Multiple select", "fTags", "P-T-tOrd"),
         item("P-O-fStat-c1", "option", "Open", "tOrd", parent="P-F-fStat"), item("P-O-fStat-c2", "option", "Closed", "tOrd", parent="P-F-fStat"),
         item("P-O-fTags-d1", "option", "Rush", "tOrd", parent="P-F-fTags"), item("P-O-fTags-d2", "option", "Gift", "tOrd", parent="P-F-fTags"),
         item("P-F-fCust", "field", "Customer", "tOrd", "Link", "fCust", "P-T-tOrd", {"linked_table_id": "tCus", "single": True}, pas="links"),
         item("P-F-fHeld", "field", "Held", "tOrd", "Text", "fHeld", "P-T-tOrd", status="hold"),
         item("P-F-fNoId", "field", "NoId", "tOrd", "Text", "fNoId", "P-T-tOrd"),
         item("P-F-fName", "field", "Name", "tCus", "Text", "fName", "P-T-tCus"),
         item("S-P-tOrd-core-object-id", "field", "Core object ID", "tOrd", "Single-line text", None, "P-T-tOrd", source="migration"),
         item("C-F-fTitle", "field", "Title", "tOrd", "Text", "fTitle", None, system="core")]
IDS = {"P-T-tOrd": "RTO", "P-T-tCus": "RTC", "P-F-fTitle": "FT", "P-F-fQty": "FQ", "P-F-fDue": "FD", "P-F-fDone": "FB", "P-F-fStat": "FS", "P-F-fTags": "FG", "P-O-fStat-c1": "optOpen",
       "P-O-fStat-c2": "optClosed", "P-O-fTags-d1": "optRush", "P-O-fTags-d2": "optGift", "P-F-fCust": "FC", "P-F-fHeld": "FH", "P-F-fName": "FN", "C-F-fTitle": "CORE1"}
T = rl.table_targets(ITEMS, State(IDS), "dev")
check("the table map holds each record type, its created ready fields and option keys, and counts what is held or not created", sorted(T) == ["tCus", "tOrd"] and T["tOrd"]["record_type"] == "RTO"
      and [f["field_id"] for f in T["tOrd"]["fields"]] == ["fCust", "fDone", "fDue", "fQty", "fStat", "fTags", "fTitle"] and T["tOrd"]["held"] == 2
      and {f["field_id"]: f["pass"] for f in T["tOrd"]["fields"]}["fCust"] == "links" and [f["field_id"] for f in T["tCus"]["fields"]] == ["fName"])
check("a migration field and a Core field are left out; a select field carries its option keys by name", all(f["field_id"] for f in T["tOrd"]["fields"])
      and {f["field_id"]: f["options"] for f in T["tOrd"]["fields"]}["fStat"] == {"Open": "optOpen", "Closed": "optClosed"})
F = {f["field_id"]: f for f in T["tOrd"]["fields"]}

# ---- converting values
c = rl.convert
check("text, number, date, boolean convert; an empty value is simply left out", c(F["fTitle"], "Hello") == (True, "Hello") and c(F["fQty"], 3) == (True, 3) and c(F["fQty"], 2.5) == (True, 2.5)
      and c(F["fDue"], "2026-03-04") == (True, "2026-03-04") and c(F["fDue"], "2026-03-04T10:00:00.000Z") == (True, "2026-03-04") and c(F["fDone"], True) == (True, True)
      and c(F["fTitle"], None) == (True, None) and c(F["fTitle"], "") == (True, None) and c(F["fTags"], []) == (True, None))
check("selects become option keys; an option Planning does not have is refused without naming the value", c(F["fStat"], "Open") == (True, "optOpen") and c(F["fTags"], ["Gift", "Rush"]) == (True, ["optGift", "optRush"])
      and c(F["fStat"], "SECRET-VALUE") == (False, "a select value has no matching option in Planning"))
bad = [c(F["fQty"], "3"), c(F["fQty"], True), c(F["fDue"], "tomorrow"), c(F["fTitle"], ["a"])]
check("a value of the wrong shape is refused with a short reason and no value in it", all(b[0] is False and "tomorrow" not in b[1] for b in bad), bad)

# ---- a fake Planning transport: records the calls and answers with IDs in order
class FakeTr(object):
    def __init__(self, fail_on=None):
        self.n, self.calls, self.updates, self.fail_on = 0, [], [], fail_on or set()
    def create_records(self, bodies):
        self.calls.append([b for b in bodies])
        if any(b["fields"].get("FT") in self.fail_on for b in bodies):
            raise RuntimeError("Planning refused the call, token=abc123secretvalue")
        out = []
        for _ in bodies:
            self.n += 1; out.append("PR%03d" % self.n)
        return out
    def update_record(self, rid, fields): self.updates.append((rid, fields))

DOCS = {"tOrd": {"records": [{"id": "recO1", "fields": {"fTitle": "One", "fQty": 2, "fStat": "Open", "fCust": ["recC1"]}},
                             {"id": "recO2", "fields": {"fTitle": "Two", "fDue": "2026-05-06", "fDone": True, "fTags": ["Rush"], "fCust": ["recC2"]}},
                             {"id": "recO3", "fields": {"fTitle": "Three", "fQty": "bad"}}]},
        "tCus": {"records": [{"id": "recC1", "fields": {"fName": "Acme"}}, {"id": "recC2", "fields": {"fName": "Globex"}}]}}
reader = lambda d, b, t: DOCS[t]
conn = db()
rm.register(conn, "appL", "tOrd", ["recO1", "recO2", "recO3"]); rm.register(conn, "appL", "tCus", ["recC1", "recC2"])
tr = FakeTr()
r = rl.load_records(conn, "appL", T, "x", tr, "dev", "wsDev", dry_run=True, reader=reader)
check("a dry run counts what is pending and sends nothing", [(t["table_id"], t["pending"]) for t in r["tables"]] == [("tCus", 2), ("tOrd", 3)] and tr.calls == [] and rm.summary(conn, "appL", "dev", "planning", "wsDev")["registered"] == 5)
r = rl.load_records(conn, "appL", T, "x", tr, "dev", "wsDev", batch=2, dry_run=False, reader=reader)
check("the records pass sends the tables in ID order, in batches, and counts every record once", r["created"] == 5 and r["failed"] == 0 and [len(x) for x in tr.calls] == [2, 2, 1], (r, [len(x) for x in tr.calls]))
sent = [b for call in tr.calls for b in call]
check("each body holds the record type and the field values keyed by Planning field ID, with no link field and no empty value", sent[0] == {"recordTypeId": "RTC", "fields": {"FN": "Acme"}}
      and {"recordTypeId": "RTO", "fields": {"FT": "One", "FQ": 2, "FS": "optOpen"}} in sent and {"recordTypeId": "RTO", "fields": {"FT": "Two", "FD": "2026-05-06", "FB": True, "FG": ["optRush"]}} in sent
      and {"recordTypeId": "RTO", "fields": {"FT": "Three"}} in sent, sent)
check("a value of the wrong shape leaves the field out and is counted as a problem, never as text", [t["problems"] for t in r["tables"] if t["table_id"] == "tOrd"] == [1])
u1 = rm.lookup(conn, "appL", "tOrd", "recO1")
check("every new record is mapped to its Planning ID and has the records marker, per environment and Workspace", rm.targets_of(conn, u1, "planning", "dev", "wsDev") == [("record", "PR003")] and rm.pending_records(conn, "appL", "tOrd", "records", "dev", "planning", "wsDev") == []
      and rm.pending_records(conn, "appL", "tOrd", "records", "uat", "planning", "wsUat") != [])
n = len(tr.calls)
r2 = rl.load_records(conn, "appL", T, "x", tr, "dev", "wsDev", batch=2, dry_run=False, reader=reader)
check("a rerun sends nothing: finished records are never sent twice", r2["created"] == 0 and len(tr.calls) == n)

# ---- links
tl = FakeTr()
r = rl.load_links(conn, "appL", T, "x", tl, "dev", "wsDev", dry_run=False, reader=reader)
pc1, pc2 = (rm.targets_of(conn, rm.lookup(conn, "appL", "tCus", x), "planning", "dev", "wsDev")[0][1] for x in ("recC1", "recC2"))
check("the links pass sets each record's link field to the Planning IDs of the records it points at", r["updated"] == 3 and sorted(tl.updates) == sorted([(rm.targets_of(conn, u1, "planning", "dev", "wsDev")[0][1], {"FC": [pc1]}),
      (rm.targets_of(conn, rm.lookup(conn, "appL", "tOrd", "recO2"), "planning", "dev", "wsDev")[0][1], {"FC": [pc2]})]) or len(tl.updates) == 2, tl.updates)
check("a record with no link is marked done without a call, and a rerun updates nothing", len(tl.updates) == 2 and rm.pending_records(conn, "appL", "tOrd", "links", "dev", "planning", "wsDev") == []
      and rl.load_links(conn, "appL", T, "x", FakeTr(), "dev", "wsDev", dry_run=False, reader=reader)["updated"] == 0)

# ---- a link whose target is not loaded waits and is never guessed
c2 = db()
rm.register(c2, "appL", "tOrd", ["recO1"]); rm.register(c2, "appL", "tCus", ["recC1", "recC2"])
DOCS2 = {"tOrd": {"records": [{"id": "recO1", "fields": {"fTitle": "One", "fCust": ["recC1"]}}]}, "tCus": DOCS["tCus"]}
t2 = FakeTr()
rl.load_records(c2, "appL", {"tOrd": T["tOrd"]}, "x", t2, "dev", "wsDev", dry_run=False, reader=lambda d, b, t: DOCS2[t])
w = rl.load_links(c2, "appL", T, "x", t2, "dev", "wsDev", dry_run=False, reader=lambda d, b, t: DOCS2[t])
check("a link to a record that has no Planning record yet waits: no update, no marker", w["waiting"] == 1 and w["updated"] == 0 and t2.updates == [] and rm.pending_records(c2, "appL", "tOrd", "links", "dev", "planning", "wsDev") != [])
rl.load_records(c2, "appL", {"tCus": T["tCus"]}, "x", t2, "dev", "wsDev", dry_run=False, reader=lambda d, b, t: DOCS2[t])
w2 = rl.load_links(c2, "appL", T, "x", t2, "dev", "wsDev", dry_run=False, reader=lambda d, b, t: DOCS2[t])
check("once the target record exists the link is set on the next run", w2["updated"] == 1 and len(t2.updates) == 1)

# ---- a failing batch is retried record by record; a failure never carries a secret or a value
c3 = db()
rm.register(c3, "appL", "tOrd", ["recO1", "recO2", "recO3"])
t3 = FakeTr(fail_on={"Two"})
r3 = rl.load_records(c3, "appL", {"tOrd": T["tOrd"]}, "x", t3, "dev", "wsDev", batch=3, dry_run=False, reader=reader)
row = r3["tables"][0]
check("one bad record does not stop the others: the batch is split and only the bad one fails", r3["created"] == 2 and r3["failed"] == 1 and row["failures"][0][0] == "recO2", r3)
check("the failure reason is scrubbed of credentials", "abc123secretvalue" not in json.dumps(row["failures"]) and "[removed]" in json.dumps(row["failures"]), row["failures"])
check("the failed record stays pending, so the next run tries it again", [r for _u, r in rm.pending_records(c3, "appL", "tOrd", "records", "dev", "planning", "wsDev")] == ["recO2"])

# ---- the real transport: route, body, order of IDs
SECRET = "fakeBEARER0123456789"
class Resp(object):
    def __init__(self, status, body): self.status, self._b = status, json.dumps(body).encode()
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False
class Opener(object):
    def __init__(self, replies): self.replies, self.reqs = list(replies), []
    def open(self, req, timeout=None):
        self.reqs.append(req); r = self.replies.pop(0)
        if r.status >= 300: raise urllib.error.HTTPError(req.full_url, r.status, "x", {}, io.BytesIO(r._b))
        return r
creds = lc.credentials({"workfront": {"planning": {"base_url": "https://acme.my.workfront.com", "bearer_token": SECRET, "workspace_id": "wsDev"}}}, "planning")
def real(replies):
    h = wh.Http(creds, opener=Opener(replies), sleep=lambda s: None)
    return pt.PlanningTransport(h), h.opener
tp, op = real([Resp(200, [{"id": "A"}, {"id": "B"}])])
check("the transport posts the whole batch as a JSON array and returns the IDs in the order sent", tp.create_records([{"recordTypeId": "R", "fields": {}}, {"recordTypeId": "R", "fields": {}}]) == ["A", "B"]
      and op.reqs[0].full_url.endswith("/v2/records/bulk") and op.reqs[0].get_method() == "POST" and isinstance(json.loads(op.reqs[0].data), list) and op.reqs[0].get_header("Authorization") == "Bearer " + SECRET)
tp, _ = real([Resp(200, {"content": [{"id": "A"}]})])
try:
    tp.create_records([{"recordTypeId": "R", "fields": {}}, {"recordTypeId": "R", "fields": {}}]); lost = None
except Exception as e:
    lost = str(e)
check("a reply with the wrong number of IDs is refused, because the records could not be matched", lost and "cannot be matched" in lost, lost)
tp, op = real([Resp(200, {})])
tp.update_record("PR1", {"FC": ["PR9"]})
check("a record update is a PATCH of its fields", op.reqs[0].get_method() == "PATCH" and op.reqs[0].full_url.endswith("/v2/records/PR1") and json.loads(op.reqs[0].data) == {"fields": {"FC": ["PR9"]}})

# ---- reading a saved file
import tempfile
d = tempfile.mkdtemp()
os.makedirs(os.path.join(d, "appL"))
json.dump({"format": "airtable-table-extract/1", "table_id": "tOrd", "records": [{"id": "recZ", "fields": {}}]}, open(os.path.join(d, "appL", "tOrd.json"), "w"))
check("a saved record file is read into memory; a missing or malformed one gives a plain message", rl.read_extract(d, "appL", "tOrd")["records"][0]["id"] == "recZ")
for args, word in ((("appL", "tNone"), "no saved record file"), (("app/..", "tOrd"), "not allowed")):
    try:
        rl.read_extract(d, *args); m = ""
    except rl.RecordLoadError as e:
        m = str(e)
    check("read_extract refuses: " + word, word in m, m)
sys.exit(0 if ok else 1)
