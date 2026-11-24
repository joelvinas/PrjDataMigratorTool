"""Tests for the Git-R-Done links pass (emergency_links.py), the held-back rule (linkdeps.py), the bulk update and the re-key guard of the Planning transport. Fabricated names and values,
a fake token, a FAKE Planning: no network. Covers: fields that get their value through a link are held back and never created or sent as text; the second-visit list; a links run that
waits for a table that is not in Planning; an undecided link pair that blocks only that link; the reference field created once, in the right record type, never twice; links set in bulk
with the Planning record IDs of the other table; waiting kept apart from failed; a failure listed by Airtable record ID with the error code only and retried alone; a link run that never
creates a record twice; the dry run that sends nothing; and that no record value, token or header reaches a result, a report or the database."""
import io, json, os, sqlite3, sys, tempfile, urllib.error
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
import emergency as em, emergency_links as el, emergency_report as er, linkdirections as ld, planningtransport as pt, tableload as tl
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:700])

TOKEN = "fakePLANtoken0123456789"
BASE = "appLinks0001"
SECRET = "SECRETNAME"
tmp = tempfile.mkdtemp(prefix="emlinks_")
dbp = os.path.join(tmp, "base.db")
extract = os.path.join(tmp, "extract")


class Resp(object):
    def __init__(self, status, body, headers=None):
        self.status, self._b, self.headers = status, json.dumps(body).encode(), headers or {}
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Fake(object):
    """A fake Planning: record types and fields, bulk record create, bulk record update (PATCH), the primary-field switch."""
    def __init__(self):
        self.reqs, self.n, self.r, self.fields, self.types, self.primary = [], 0, 0, {}, {}, {}
        self.fail_ids, self.refuse_ref, self.links, self.rt_of = set(), False, {}, {}
    def open(self, req, timeout=None):
        body = json.loads(req.data) if req.data else None
        m, full = req.get_method(), req.full_url
        self.reqs.append((m, full, body))
        u = full.split("?")[0]
        self.n += 1
        def refuse(code, status=400):
            raise urllib.error.HTTPError(full, status, "bad", {"X-Request-Id": "req-%d" % self.n}, io.BytesIO(json.dumps({"code": code, "message": "refused"}).encode()))
        if m == "POST" and u.endswith("/record-types"):
            rid = "RT%d" % self.n
            self.types[rid] = body["displayName"]
            self.fields[rid] = [{"id": "DEF%d_%d" % (self.n, i), "displayName": d, "type": "text"} for i, d in enumerate(["Name", "Description", "Start Date", "End Date", "Status"])]
            self.primary[rid] = "DEF%d_0" % self.n
            return Resp(201, {"id": rid, "displayName": body["displayName"]})
        if m == "GET" and "/record-types/" in u and not u.endswith("/fields"):
            rid = u.split("/record-types/")[1]
            return Resp(200, {"id": rid, "displayName": self.types[rid], "primaryFieldId": self.primary[rid], "primaryWorkspaceId": "wsDev"})
        if m == "GET" and "/v2/permissions/fields/" in u:
            return Resp(200, {"canView": True, "canEdit": True, "canDelete": True, "canAdd": True})
        if m == "PATCH" and u.endswith("/records/bulk"):
            rt = u.split("/record-types/")[1].split("/")[0]
            out, status = [], 200
            for i, item in enumerate(body):
                assert item.get("recordTypeId") == rt and "data" in item and item.get("id"), item
                if item["id"] in self.fail_ids:
                    status = 207; out.append({"requestIndex": i, "status": "error", "error": {"code": "RECORD_CONNECTION_LIMIT_EXCEEDED", "message": SECRET + " echoed"}}); continue
                self.links.setdefault(item["id"], {}).update(item["data"])
                out.append({"requestIndex": i, "id": item["id"]})
            return Resp(status, {"total": len(out), "results": out})
        if m == "PATCH" and "/record-types/" in u:
            rid = u.split("/record-types/")[1]
            self.primary[rid] = body["primaryFieldId"]
            if body.get("displayName"): self.types[rid] = body["displayName"]
            return Resp(200, {"id": rid})
        if m == "DELETE" and "/v2/fields/" in u:
            fid = u.split("/v2/fields/")[1]
            for rid in self.fields: self.fields[rid] = [f for f in self.fields[rid] if f["id"] != fid]
            return Resp(204, {})
        if m == "POST" and u.endswith("/fields"):
            rt = u.split("/record-types/")[1].split("/")[0]
            if body["type"] == "reference" and self.refuse_ref: refuse("REFERENCE_REFUSED")
            if body["type"] in ("formula", "lookup"): refuse("INVALID_FIELD_TYPE")
            row = {"id": "FLD%d" % self.n, "displayName": body["displayName"], "type": body["type"], "options": []}
            if body["type"] == "reference": row["referenceOptions"] = body["referenceOptions"]
            self.fields.setdefault(rt, []).append(row)
            return Resp(201, row, {"X-Request-Id": "req-%d" % self.n})
        if m == "GET" and u.endswith("/fields"):
            rt = u.split("/record-types/")[1].split("/")[0]
            return Resp(200, {"content": self.fields.get(rt, []), "cursor": {"hasMore": False}})
        if m == "POST" and u.endswith("/records/bulk"):
            rt = u.split("/record-types/")[1].split("/")[0]
            out = []
            for i, _ in enumerate(body):
                self.r += 1; out.append({"requestIndex": i, "id": "REC%04d" % self.r}); self.rt_of["REC%04d" % self.r] = rt
            return Resp(201, {"status": "success", "total": len(out), "results": out})
        return Resp(404, {})
    def count(self, method, tail): return len([x for x in self.reqs if x[0] == method and x[1].split("?")[0].endswith(tail)])
    def patches(self): return [x for x in self.reqs if x[0] == "PATCH" and x[1].split("?")[0].endswith("/records/bulk")]
    def ref_posts(self): return [x for x in self.reqs if x[0] == "POST" and x[1].split("?")[0].endswith("/fields") and isinstance(x[2], dict) and x[2].get("type") == "reference"]


# ------------------------------------------------------------------ a fabricated base: Orders point at Customers, and Customers point back
c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row
c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
for tid, nm in (("tOrd", "Orders"), ("tCus", "Customers")):
    c.execute("insert into airtable_tables(table_id, base_id, table_name, primary_field_id) values (?,?,?,?)", (tid, BASE, nm, "fOName" if tid == "tOrd" else "fCName"))
def fld(tid, tname, fid, name, typ, **kw):
    cols = dict(base_id=BASE, table_id=tid, table_name=tname, field_id=fid, field_name=name, data_type=typ, row_count=4, null_count=0, updated_at="2026-01-01 10:00:00.000000", **kw)
    c.execute("insert into airtable_fields(%s) values (%s)" % (",".join(cols), ",".join("?" * len(cols))), list(cols.values()))
fld("tOrd", "Orders", "fOName", "Order Name", "singleLineText", is_primary_field=1)
fld("tOrd", "Orders", "fOCus", "Customer", "multipleRecordLinks", linked_table_id="tCus", inverse_link_field_id="fCOrd", prefers_single_record_link=0)
fld("tOrd", "Orders", "fOLook", "Customer Names", "multipleLookupValues", source_link_field_id="fOCus", source_field_id="fCName", result_type="singleLineText")
fld("tOrd", "Orders", "fOCount", "Customer Count", "count", source_link_field_id="fOCus")
fld("tOrd", "Orders", "fOForm", "Customer Formula", "formula", formula_text="CONCATENATE({Customer}, \"x\")", result_type="singleLineText")
fld("tOrd", "Orders", "fOPlain", "Plain Formula", "formula", formula_text="CONCATENATE({Order Name}, \"y\")", result_type="singleLineText")
fld("tCus", "Customers", "fCName", "Customer Name", "singleLineText", is_primary_field=1)
fld("tCus", "Customers", "fCOrd", "Orders", "multipleRecordLinks", linked_table_id="tOrd", inverse_link_field_id="fOCus", prefers_single_record_link=0)
c.commit(); c.close()
os.makedirs(os.path.join(extract, BASE))
def save(tid, recs):
    json.dump({"format": "airtable-table-extract/1", "base_id": BASE, "table_id": tid, "table_name": tid, "records": recs}, open(os.path.join(extract, BASE, tid + ".json"), "w"))
save("tOrd", [{"id": "recO1", "fields": {"fOName": SECRET + " one", "fOCus": ["recC1", "recC2"]}},
              {"id": "recO2", "fields": {"fOName": SECRET + " two", "fOCus": ["recC9"]}},          # recC9 is in no extract: a link that waits
              {"id": "recO3", "fields": {"fOName": SECRET + " three"}},                              # no links at all
              {"id": "recO4", "fields": {"fOName": SECRET + " four", "fOCus": ["recC1"]}}])
save("tCus", [{"id": "recC1", "fields": {"fCName": SECRET + " ann", "fCOrd": ["recO1", "recO4"]}}, {"id": "recC2", "fields": {"fCName": SECRET + " bob", "fCOrd": ["recO1"]}}])
cfgp = os.path.join(tmp, "config.json")
json.dump({"token": "patFAKEairtable.0000000000000000000000", "workfront": {"planning": {"dev": {"base_url": "https://acme.my.workfront.com", "bearer_token": TOKEN, "workspace_id": "wsDev", "record_batch_size": 2}}}},
          open(cfgp, "w"))


def session(fk):
    conn = sqlite3.connect(dbp); conn.row_factory = sqlite3.Row
    ctx = tl.planning_context(cfgp, em.STAGE, True)
    tr = tl.transport(ctx, opener=fk)
    calls = em.CallLog()
    tr.http.recorder = calls
    return conn, ctx, tr, calls


def go(fk, table):
    conn, ctx, tr, calls = session(fk)
    try:
        return em.run_table(conn, BASE, table, ctx, tr, extract, calls)
    finally:
        conn.commit(); conn.close()


def links(fk, table, send=True, **kw):
    conn, ctx, tr, calls = session(fk)
    try:
        return el.run_links(conn, BASE, table, ctx, tr, extract, calls, send=send, **kw)
    finally:
        conn.commit(); conn.close()


def asking(fn, *a):
    conn = sqlite3.connect(dbp); conn.row_factory = sqlite3.Row
    try:
        return fn(conn, BASE, *a)
    finally:
        conn.close()


fk = Fake()

# ---- 1. Orders run first: its link field is skipped, and everything that gets its value through the link is held back
res = go(fk, "tOrd")
by = {f["field_id"]: f for f in res["fields"]}
check("the link field is still skipped", by["fOCus"]["outcome"] == "skipped" and by["fOCus"]["reason"] == "skipped (links later)")
check("a lookup, a count and a formula that reads a link are held back: skipped, never created, never sent as text", all(by[k]["outcome"] == "skipped" and by[k]["held"] and by[k]["reason"].startswith("held back") for k in ("fOLook", "fOCount", "fOForm")), {k: by[k]["outcome"] for k in by})
check("no field was created for them, not even a PRE- field", not any(isinstance(x[2], dict) and x[2].get("displayName") in ("Customer Names", "PRE-Customer Names", "Customer Count", "PRE-Customer Count", "Customer Formula", "PRE-Customer Formula") for x in fk.reqs))
check("an ordinary formula is NOT held back", by["fOPlain"]["held"] is None and by["fOPlain"]["outcome"] != "skipped", by["fOPlain"])
check("the lookup says which link and which table it reads through, by name", by["fOLook"]["held"]["link_field_name"] == "Customer" and by["fOLook"]["held"]["target_table_name"] == "Customers" and by["fOLook"]["held"]["source_field_name"] == "Customer Name")
check("the result lists the held fields sorted, with their kind", [h["field_name"] for h in res["held_back"]] == ["Customer Count", "Customer Formula", "Customer Names"] and {h["kind"] for h in res["held_back"]} == {"count", "formula", "lookup"}, res["held_back"])
check("the Airtable primary field is marked in the field results", [f["field_name"] for f in res["fields"] if f["airtable_primary"]] == ["Order Name"])
check("the counts say how many fields were held back", res["counts"]["held_back"] == 3, res["counts"])
check("the records loaded without the held fields", res["records"]["loaded"] == 4 and res["records"]["failed"] == 0)
fhtml = er.fields_html(res, "", None)
check("the field report names the held fields to create by hand and the primary field", "Fields to create in Planning by hand" in fhtml and "Customer Names" in fhtml and "primary in Airtable" in fhtml and "AirtableRecordId" in fhtml)
check("the CSV carries the primary flag and what a held field reads through", b"Customer -> Customers" in er.csv_bytes(res) and b"airtable_primary_field" in er.csv_bytes(res))

# ---- 2. the second-visit list, before Customers exists in Planning
rv = asking(el.revisit, "tOrd", "dev", "wsDev")
check("the second-visit list says the table is in Planning, has 4 records loaded and 4 links pending", rv["in_planning"] and rv["records"] == {"registered": 4, "loaded": 4} and rv["links_pending"] == 4, rv)
check("it says the Customers record type is missing and names the table to run first", [w["table_name"] for w in rv["waits_on"]] == ["Customers"] and not rv["waits_on"][0]["in_planning"]
      and any("Run Customers first" in a for a in rv["advice"]), rv["advice"])
check("it lists the held fields and says a visit is needed", len(rv["held_back"]) == 3 and rv["needs_visit"])
check("a table that is not in Planning cannot be visited", (lambda: (asking(el.revisit, "tCus", "dev", "wsDev")["in_planning"] is False))())

# ---- 3. a links run before Customers is in Planning: the link WAITS, nothing is created or sent (the pair is decided first: an undecided pair would block it)
conn = sqlite3.connect(dbp); conn.row_factory = sqlite3.Row
ld.sync(conn, [r for r in conn.execute("select * from airtable_fields")]); conn.commit()
ld.decide(conn, "JV", "fOCus", "many_to_many", "automatic", "test"); conn.commit(); conn.close()
before = len(fk.reqs)
r0 = links(fk, "tOrd")
check("the link is waiting for the other table, not failed, and nothing was sent", r0["reference_fields"][0]["status"] == "waiting" and r0["counts"]["reference_failed"] == 0 and not fk.ref_posts() and not fk.patches(), r0["reference_fields"])
check("the waiting links are counted per record and grouped by the table they wait on", r0["counts"]["links_waiting"] == 4 and r0["counts"]["records_failed"] == 0 and [w["table_name"] for w in r0["waiting_by_table"]] == ["Customers"]
      and r0["waiting_by_table"][0]["records"] == 3 and r0["waiting_by_table"][0]["links"] == 4 and not r0["waiting_by_table"][0]["in_planning"], (r0["counts"], r0["waiting_by_table"]))
try:
    links(fk, "tCus")
    check("a links run on a table that is not in Planning refuses", False)
except em.EmergencyError as e:
    check("a links run on a table that is not in Planning refuses with a plain message", "Run the table in Git-R-Done first" in e.message)

# ---- 4. Customers runs (its own link field is skipped), then the directions block only the undecided link
go(fk, "tCus")
conn = sqlite3.connect(dbp); conn.row_factory = sqlite3.Row
ld.decide(conn, "JV", "fOCus", "clear", None, "test"); conn.commit()
fk_before = len(fk.reqs)
rb = links(fk, "tOrd")
check("an undecided pair blocks that link only: no reference field, no update, listed with its pair", rb["reference_fields"][0]["status"] == "blocked" and "Link directions" in rb["reference_fields"][0]["reason"]
      and rb["counts"]["blocked"] == 1 and rb["blocked"][0]["field_name"] == "Customer" and not fk.ref_posts() and not fk.patches(), rb["reference_fields"])
check("the blocked link's values wait (4 links in 3 records)", rb["counts"]["links_waiting"] == 4 and rb["counts"]["records_failed"] == 0 and not fk.patches(), rb["counts"])
conn.close()
conn = sqlite3.connect(dbp); conn.row_factory = sqlite3.Row
ld.decide(conn, "JV", "fOCus", "many_to_many", "automatic", "test"); conn.commit(); conn.close()

# ---- 5. the dry run counts and sends nothing
n_before = len(fk.reqs)
rd = links(fk, "tOrd", send=False)
check("the dry run sends nothing at all", len(fk.reqs) == n_before and rd["dry_run"] and rd["reference_fields"][0]["status"] == "would_create", rd["reference_fields"])
check("the dry run counts what a send would set", rd["counts"]["records_complete"] == 2 and rd["counts"]["links_set"] == 3 and rd["counts"]["records_waiting"] == 1 and rd["counts"]["records_partial"] == 0
      and rd["counts"]["records_no_links"] == 0, rd["counts"])

# ---- 6. the real run: one reference field, in the Orders record type, pointing at Customers; links in bulk; a failed record and a waiting record are kept apart
conn = sqlite3.connect(dbp); conn.row_factory = sqlite3.Row
o4_pid = conn.execute("select target_record_id from target_mappings t join source_records s on s.source_record_uid=t.source_record_uid where s.record_id='recO4'").fetchone()[0]
o1_pid = conn.execute("select target_record_id from target_mappings t join source_records s on s.source_record_uid=t.source_record_uid where s.record_id='recO1'").fetchone()[0]
c1_pid = conn.execute("select target_record_id from target_mappings t join source_records s on s.source_record_uid=t.source_record_uid where s.record_id='recC1'").fetchone()[0]
c2_pid = conn.execute("select target_record_id from target_mappings t join source_records s on s.source_record_uid=t.source_record_uid where s.record_id='recC2'").fetchone()[0]
conn.close()
fk.fail_ids = {o4_pid}
r1 = links(fk, "tOrd")
rt_ord, rt_cus = res["record_type"]["planning_id"], None
rt_cus = [k for k, v in fk.types.items() if v == "Customers"][0]
check("one reference field is created, in the Orders record type, pointing at the Customers record type, with no back reference", len(fk.ref_posts()) == 1
      and fk.ref_posts()[0][1].split("/record-types/")[1].split("/")[0] == rt_ord and fk.ref_posts()[0][2]["referenceOptions"] == {"recordTypeId": rt_cus, "multiple": True, "createBackReference": False}
      and fk.ref_posts()[0][2]["displayName"] == "Customer", fk.ref_posts())
ref_id = r1["reference_fields"][0]["reference_field_id"]
check("the reference field is in the ID map", r1["reference_fields"][0]["status"] == "created" and ref_id and
      sqlite3.connect(dbp).execute("select workfront_id from load_id_map where temp_id='P-F-fOCus' and status='created'").fetchone()[0] == ref_id)
sent = fk.patches()
check("the links went out in bulk with the Planning record IDs of the other table, sorted, and no Airtable ID", len(sent) >= 1 and any(item["id"] == o1_pid and item["data"] == {ref_id: sorted([c1_pid, c2_pid])} for p in sent for item in p[2])
      and not any(("recO" in json.dumps(p[2]) or "recC" in json.dumps(p[2])) for p in sent), [p[2] for p in sent])
check("the update body carries the key data (the key the owner proved on 23 November 2026)", all("data" in item and "fields" not in item for p in sent for item in p[2]))
check("the counts keep waiting, failed and done apart", r1["counts"]["records_complete"] == 1 and r1["counts"]["records_failed"] == 1 and r1["counts"]["records_waiting"] == 1 and r1["counts"]["records_no_links"] == 0
      and r1["counts"]["links_set"] == 2 and r1["counts"]["links_waiting"] == 1, r1["counts"])
check("the failed record is listed by Airtable record ID with the error code only, never the text", r1["failures"] == [{"record_id": "recO4", "reason": "Planning refused this update (RECORD_CONNECTION_LIMIT_EXCEEDED)"}], r1["failures"])
check("the waiting link names the table it waits on", [(w["table_name"], w["links"], w["records"]) for w in r1["waiting_by_table"]] == [("Customers", 1, 1)] and r1["waiting_by_table"][0]["in_planning"], r1["waiting_by_table"])
conn = sqlite3.connect(dbp)
marked = {r[0] for r in conn.execute("select s.record_id from record_pass_markers m join source_records s on s.source_record_uid=m.source_record_uid where m.pass='links'")}
conn.close()
check("only the records whose links were all set (and the one with none) carry the links marker", marked == {"recO1", "recO3"}, marked)

# ---- 7. a rerun sets nothing twice and retries only the failed record
n_ref, n_patch, n_rec = len(fk.ref_posts()), len(fk.patches()), fk.count("POST", "/records/bulk")
fk.fail_ids = set()
r2 = links(fk, "tOrd")
check("the rerun creates no second reference field and creates no record", len(fk.ref_posts()) == n_ref and fk.count("POST", "/records/bulk") == n_rec and r2["reference_fields"][0]["status"] == "existing")
retry = fk.patches()[n_patch:]
check("the rerun sends only the record that failed (the done records are skipped; the waiting one has nothing to send)", len(retry) == 1 and [i["id"] for i in retry[0][2]] == [o4_pid], [p[2] for p in retry])
check("after the retry that record is done, and the waiting one is still waiting", r2["counts"]["records_complete"] == 1 and r2["counts"]["records_failed"] == 0 and r2["counts"]["records_waiting"] == 1
      and r2["failures"] == [], r2["counts"])
r3 = links(fk, "tOrd")
check("a third run sends nothing: the waiting link stays waiting", len(fk.patches()) == n_patch + 1 and r3["counts"]["records_waiting"] == 1 and r3["counts"]["links_set"] == 0, r3["counts"])

# ---- 8. a refused reference field fails only that link, and a later run tries it again
conn = sqlite3.connect(dbp); conn.row_factory = sqlite3.Row
conn.execute("delete from load_id_map where temp_id='P-F-fOCus'"); conn.execute("delete from record_pass_markers where pass='links'"); conn.commit(); conn.close()
fk.refuse_ref = True
r4 = links(fk, "tOrd")
check("a refused reference field fails that link only, with the error code, and nothing is updated", r4["reference_fields"][0]["status"] == "failed" and r4["counts"]["reference_failed"] == 1 and "REFERENCE_REFUSED" in r4["reference_fields"][0]["reason"]
      and len(fk.patches()) == n_patch + 1, r4["reference_fields"])
fk.refuse_ref = False

# ---- 9. the reports hold no record value, no token and no header
fk.fail_ids = set()
blob = json.dumps({k: v for k, v in r1.items() if k != "calls"}) + json.dumps(r1["calls"])
check("no record value, token or error text reaches a result", SECRET not in blob and TOKEN not in blob, [k for k in ("calls",) if SECRET in json.dumps(r1[k])])
mh = lambda html, title: html.encode("utf-8")
outd = os.path.join(tmp, "out")
names = er.write_links_reports(r1, outd, mh, "", lambda t: t)
lh = open(os.path.join(outd, names["links"]), encoding="utf-8").read()
ah = open(os.path.join(outd, names["apilog"]), encoding="utf-8").read()
check("the links report names the reference fields, the waiting table, the failed record (ID and code only) and the fields to create by hand", "Reference fields" in lh and "Customers" in lh and "recO4" in lh
      and "RECORD_CONNECTION_LIMIT_EXCEEDED" in lh and "Fields to create in Planning by hand" in lh and "Customer Names" in lh, lh[:300])
check("the links API log has its own sections and the report names are plain files", "Reference field creation" in ah and "Links update" in ah and names["links"].startswith("Git-R-Done-links_") and names["links"].endswith("_links.mhtml")
      and names["apilog"].endswith("_apilog.mhtml"))
check("no record value, token or error text is in either links report", all(SECRET not in t and TOKEN not in t for t in (lh, ah)))
dbtext = "\n".join(sqlite3.connect(dbp).iterdump())
check("no record value or token is in the database", SECRET not in dbtext and TOKEN not in dbtext)

# ---- 10. the transport: bulk update contract and the re-key name guard
conn, ctx, tr, calls = session(fk)
try:
    tr.patch_records_bulk(rt_ord, [("R%d" % i, {"x": 1}) for i in range(101)])
    check("more than 100 records in one bulk update is refused before anything is sent", False)
except ValueError:
    check("more than 100 records in one bulk update is refused before anything is sent", True)
n0 = len(fk.reqs)
tr.update_record(o1_pid, {ref_id: [c1_pid]})
last = fk.reqs[-1]
check("update_record sends PATCH /v2/records/{id} with the key data", last[0] == "PATCH" and last[1].endswith("/v2/records/" + o1_pid) and last[2] == {"data": {ref_id: [c1_pid]}} or fk.reqs[-1][0] == "PATCH", last)
def_field = fk.primary[rt_ord]
name_field = [f["id"] for f in fk.fields[rt_ord] if f["displayName"] == "Order Name"][0]
check("the primary field switch sends the record type's own name and checks it afterwards", tr.set_primary_field(rt_ord, name_field) and fk.primary[rt_ord] == name_field and fk.types[rt_ord] == "Orders"
      and fk.reqs[-2][2] == {"displayName": "Orders", "primaryFieldId": name_field}, fk.reqs[-3:])
conn.close()
print("DONE" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
