"""Tests for Emergency mode (emergency.py, emergency_report.py): one table into a FAKE Workfront Planning, whatever works. Fabricated names and values; fake token; no network.
Covers: real types that work, a refused type becoming PRE- text (text or long text by the longest value), arrays as raw JSON text, links and attachments skipped, the 20
long-text cap, a rerun that creates nothing twice, a failing field that never stops the run, the unaltered Airtable names, the ID map, the optional database table (draft
migration 025), the three report files, and that no record value, token or header reaches a result, a report or the database."""
import io, json, os, shutil, sqlite3, sys, tempfile, urllib.error
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
import emergency as em, emergency_report as er, tableload as tl, typesfixture
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:600])

TOKEN = "fakePLANtoken0123456789"
BASE = "appEmerg0001"
SECRET = "SECRETVALUE"
tmp = tempfile.mkdtemp(prefix="emergency_")
dbp = os.path.join(tmp, "base.db")
extract = os.path.join(tmp, "extract")


class Resp(object):
    def __init__(self, status, body, headers=None):
        self.status, self._b, self.headers = status, json.dumps(body).encode(), headers or {}
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Fake(object):
    """A fake Planning. It refuses the field types in `refuse_types` (400 with an error code), lists the fields it created, and bulk-creates records."""
    def __init__(self):
        self.reqs, self.n, self.r, self.fields, self.refuse_types, self.refuse_names, self.fail_index = [], 0, 0, {}, {"date", "formula"}, set(), set()
        self.primary, self.types, self.refuse_delete, self.refuse_primary, self.refuse_rename = {}, {}, set(), False, set()
    def open(self, req, timeout=None):
        body = json.loads(req.data) if req.data else None
        m, full = req.get_method(), req.full_url
        self.reqs.append((m, full, body))
        u = full.split("?")[0]
        self.n += 1
        def refuse(code, extra=""):
            raise urllib.error.HTTPError(full, 400, "bad", {"X-Request-Id": "req-%d" % self.n}, io.BytesIO(json.dumps({"code": code, "message": "refused " + extra}).encode()))
        if m == "POST" and u.endswith("/record-types"):
            rid = "RT%d" % self.n
            self.types[rid] = body["displayName"]
            self.fields[rid] = [{"id": "DEF%d_%d" % (self.n, i), "displayName": d, "type": "text"} for i, d in enumerate(["Name", "Description", "Start Date", "End Date", "Status"])]   # what Planning adds itself
            self.primary[rid] = "DEF%d_0" % self.n
            return Resp(201, {"id": rid, "displayName": body["displayName"]})
        if m == "GET" and "/record-types/" in u and not u.endswith("/fields"):
            rid = u.split("/record-types/")[1]
            return Resp(200, {"id": rid, "displayName": self.types[rid], "primaryFieldId": self.primary[rid], "primaryWorkspaceId": "wsDev"})
        if m == "GET" and "/v2/permissions/fields/" in u:
            return Resp(200, {"canView": True, "canEdit": True, "canDelete": False, "canAdd": True})
        if m == "PATCH" and "/v2/fields/" in u:
            fid = u.split("/v2/fields/")[1]
            if fid in self.refuse_rename: refuse("RENAME_REFUSED")
            for rid in self.fields:
                for f in self.fields[rid]:
                    if f["id"] == fid: f["displayName"] = body["displayName"]
            return Resp(200, {"id": fid})
        if m == "PATCH" and "/record-types/" in u:
            rid = u.split("/record-types/")[1]
            if self.refuse_primary: refuse("PRIMARY_REFUSED")
            self.primary[rid] = body["primaryFieldId"]
            return Resp(200, {"id": rid})
        if m == "DELETE" and "/v2/fields/" in u:
            fid = u.split("/v2/fields/")[1]
            if fid in self.refuse_delete: refuse("FIELD_IN_USE")
            for rid in self.fields: self.fields[rid] = [f for f in self.fields[rid] if f["id"] != fid]
            return Resp(204, {})
        if m == "POST" and u.endswith("/fields"):
            rt = u.split("/record-types/")[1].split("/")[0]
            if body["type"] in self.refuse_types: refuse("INVALID_FIELD_TYPE", "Bearer " + TOKEN)
            if body["displayName"] in self.refuse_names: refuse("VALIDATION_FAILED")
            row = {"id": "FLD%d" % self.n, "displayName": body["displayName"], "type": body["type"], "options": [{"displayName": o["displayName"], "name": "key" + o["displayName"]} for o in body.get("options", [])]}
            self.fields.setdefault(rt, []).append(row)
            return Resp(201, row, {"X-Request-Id": "req-%d" % self.n})
        if m == "GET" and u.endswith("/fields"):
            rt = u.split("/record-types/")[1].split("/")[0]
            return Resp(200, {"content": self.fields.get(rt, []), "cursor": {"hasMore": False}})
        if m == "POST" and u.endswith("/records/bulk"):
            out, status = [], 201
            for i, _ in enumerate(body):
                if i in self.fail_index:
                    status = 207; out.append({"requestIndex": i, "status": "error", "error": {"code": "FIELD_INVALID", "message": SECRET + " echoed"}}); continue
                self.r += 1; out.append({"requestIndex": i, "id": "REC%04d" % self.r})
            return Resp(status, {"status": "success", "total": len(out), "requestId": "bulk-%d" % self.n, "results": out})
        return Resp(404, {})
    def posts(self, tail): return [x for x in self.reqs if x[0] == "POST" and x[1].split("?")[0].endswith(tail)]


# ------------------------------------------------------------------ the fabricated base: no name is approved, no data type mapping applied
c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row
c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
c.execute("insert into airtable_tables(table_id, base_id, table_name) values ('tX', ?, 'Gadget Orders')", (BASE,))
c.execute("insert into airtable_tables(table_id, base_id, table_name) values ('tY', ?, 'Many Notes')", (BASE,))
F = [("fName", "Order Name", "singleLineText"), ("fDate", "Due Date", "date"), ("fNote", "Long Note", "multilineText"), ("fTags", "Tags", "multipleSelects"), ("fForm", "Summary Formula", "formula"),
     ("fFormBig", "Long Formula", "formula"), ("fLink", "Customer", "multipleRecordLinks"), ("fAtt", "Files", "multipleAttachments"), ("fNum", "Amount", "number"), ("fOdd", "Strange Thing", "aiText")]
for fid, nm, ty in F:
    c.execute("insert into airtable_fields(base_id, table_id, table_name, field_id, field_name, data_type, row_count, null_count, cardinality, choices_json, formula_text, updated_at) values (?,?,?,?,?,?,3,1,2,?,?,'2026-01-01 10:00:00.000000')",
              (BASE, "tX", "Gadget Orders", fid, nm, ty, json.dumps([{"id": "s1", "name": "Red"}, {"id": "s2", "name": "Blue"}]) if ty == "multipleSelects" else None, "CONCAT({Order Name}, \"x\")" if ty == "formula" else None))
for i in range(1, 23):                                       # 22 formulas whose values are long: the cap of 20 long-text fields must hold
    c.execute("insert into airtable_fields(base_id, table_id, table_name, field_id, field_name, data_type, updated_at) values (?,?,?,?,?,?,'2026-01-01 10:00:00.000000')",
              (BASE, "tY", "Many Notes", "fY%02d" % i, "Memo %02d" % i, "formula"))
c.commit(); c.close()
os.makedirs(os.path.join(extract, BASE))
def save(tid, recs):
    json.dump({"format": "airtable-table-extract/1", "base_id": BASE, "table_id": tid, "table_name": tid, "records": recs}, open(os.path.join(extract, BASE, tid + ".json"), "w"))
long_text = SECRET + " " + "x" * 300
save("tX", [{"id": "recX%d" % i, "fields": {"fName": SECRET + " order %d" % i, "fDate": "2026-03-0%d" % i, "fNote": long_text, "fTags": ["Red", "Blue"], "fForm": SECRET + " short", "fFormBig": long_text,
                                            "fLink": ["recZ1"], "fAtt": [{"id": "att1", "url": "http://x/y"}], "fNum": i * 10, "fOdd": SECRET + " odd"}} for i in range(1, 4)])
save("tY", [{"id": "recY1", "fields": {"fY%02d" % i: long_text for i in range(1, 23)}}])
cfgp = os.path.join(tmp, "config.json")
json.dump({"token": "patFAKEairtable.0000000000000000000000", "workfront": {"planning": {"dev": {"base_url": "https://acme.my.workfront.com", "bearer_token": TOKEN, "workspace_id": "wsDev", "record_batch_size": 2,
                                                                                              "default_currency": "USD"}}}}, open(cfgp, "w"))


def go(fk, table, db=dbp, **kw):
    conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
    ctx = tl.planning_context(cfgp, em.STAGE, True)
    tr = tl.transport(ctx, opener=fk)
    calls = em.CallLog()
    tr.http.recorder = calls
    try:
        return em.run_table(conn, BASE, table, ctx, tr, extract, calls, **kw), conn
    finally:
        conn.commit()


fk = Fake()
res, conn = go(fk, "tX")
by = {f["field_id"]: f for f in res["fields"]}
check("the run is on the dev stage and the Workspace comes from config.json", res["stage"] == "dev" and res["workspace_id"] == "wsDev" and em.STAGE == "dev")
check("the record type is created with the Airtable table name, unaltered", res["record_type"]["outcome"] == "created" and fk.posts("/record-types")[0][2] == {"displayName": "Gadget Orders"}, fk.posts("/record-types"))
check("fields are processed in sorted order by name", [f["field_name"] for f in res["fields"]] == sorted([f["field_name"] for f in res["fields"]], key=str.lower))
check("a text field is created with its real type and the unaltered Airtable name", by["fName"]["outcome"] == "clean" and by["fName"]["final_name"] == "Order Name" and by["fName"]["final_type"] == "text")
check("a number and a long text are clean as their real types", by["fNum"]["outcome"] == "clean" and by["fNum"]["final_type"] == "number" and by["fNote"]["outcome"] == "clean" and by["fNote"]["final_type"] == "long-text")
check("a refused date type becomes PRE- text with BOTH attempts recorded (short values: text)", by["fDate"]["outcome"] == "pre-converted" and by["fDate"]["final_name"] == "PRE-Due Date" and by["fDate"]["final_type"] == "text"
      and by["fDate"]["attempted_type"] == "date" and by["fDate"]["attempt1"] == "refused" and by["fDate"]["attempt1_error_code"] == "INVALID_FIELD_TYPE" and by["fDate"]["attempt2"] == "created" and by["fDate"]["attempt2_type"] == "text", by["fDate"])
check("a refused formula with a short longest value becomes PRE- text, one with a value over 255 characters becomes PRE- long text", by["fForm"]["final_type"] == "text" and by["fFormBig"]["final_type"] == "long-text"
      and by["fFormBig"]["final_name"] == "PRE-Long Formula" and by["fFormBig"]["max_length"] > 255 and by["fForm"]["max_length"] <= 255)
check("the real attempt used the type text even for a formula, as given", by["fForm"]["attempted_type"] == "formula" and any(isinstance(x[2], dict) and x[2].get("type") == "formula" for x in fk.reqs))
check("an array (multiple select) goes straight to a PRE- field and is never attempted as a select", by["fTags"]["final_name"] == "PRE-Tags" and by["fTags"]["attempt1"] == "not attempted" and not any(isinstance(x[2], dict) and x[2].get("type") == "multi-select" for x in fk.reqs))
check("a type nobody maps is sent as given (here Planning accepts it), the Airtable type text goes out unchanged", by["fOdd"]["attempted_type"] == "aiText" and any(isinstance(x[2], dict) and x[2].get("type") == "aiText" for x in fk.reqs), by["fOdd"])
check("links and attachments are skipped with their reason, with no field and no call", by["fLink"]["outcome"] == "skipped" and by["fLink"]["reason"] == "skipped (links later)" and by["fAtt"]["reason"] == "skipped (attachments later)"
      and not any(isinstance(x[2], dict) and x[2].get("displayName") in ("Customer", "PRE-Customer", "Files", "PRE-Files") for x in fk.reqs))
check("the counts add up: clean, PRE-converted, skipped, failed", res["counts"]["clean"] + res["counts"]["pre-converted"] + res["counts"]["skipped"] + res["counts"]["failed"] == len(res["fields"]) == 10, res["counts"])
check("the records were loaded: every record, none failed, and only fields Planning has are in the body", res["records"]["loaded"] == 3 and res["records"]["failed"] == 0)
bodies = [x[2] for x in fk.posts("/records/bulk")]
sent = [rec["data"] for b in bodies for rec in b]
ids = {f["field_id"]: f["planning_field_id"] for f in res["fields"]}
check("a PRE- array field holds the array as raw JSON text, exactly as received", all(d.get(ids["fTags"]) == '["Red","Blue"]' for d in sent), sent[:1])
check("a PRE- field keeps the value untouched (a date stays the same text)", sent[0].get(ids["fDate"]) == "2026-03-01" and sent[0].get(ids["fNum"]) == 10)
check("the skipped link and attachment fields are not in any record", not any(k in ids.values() for d in sent for k in d if k == ids["fLink"]) and ids["fLink"] == "" and ids["fAtt"] == "")

# ---- the ID map
dbc = sqlite3.connect(dbp); dbc.row_factory = sqlite3.Row
rows = {r["temp_id"]: dict(r) for r in dbc.execute("select * from load_id_map where base_id=?", (BASE,))}
check("the ID map holds the record type and every created field against its Airtable ID, in the dev stage and Workspace", rows["P-T-tX"]["workfront_id"] == res["record_type"]["planning_id"] and rows["P-T-tX"]["environment"] == "dev"
      and rows["P-T-tX"]["workspace_id"] == "wsDev" and rows["P-F-fDate"]["airtable_field_id"] == "fDate" and rows["P-F-fDate"]["name"] == "PRE-Due Date" and rows["P-F-fDate"]["workfront_id"] == ids["fDate"] and rows["P-F-fDate"]["status"] == "created")
check("the registry and the mapping hold every record, with its pass marker", dbc.execute("select count(*) from source_records where table_id='tX'").fetchone()[0] == 3
      and dbc.execute("select count(*) from target_mappings where system='planning' and environment='dev'").fetchone()[0] == 3 and dbc.execute("select count(*) from record_pass_markers where pass='records' and environment='dev'").fetchone()[0] == 3)

# ---- a rerun creates nothing twice
n_rt, n_fields, n_bulk = len(fk.posts("/record-types")), len(fk.posts("/fields")), len(fk.posts("/records/bulk"))
res2, _c = go(fk, "tX")
by2 = {f["field_id"]: f for f in res2["fields"]}
check("a rerun reuses the record type and every field and creates nothing twice", len(fk.posts("/record-types")) == n_rt and len(fk.posts("/fields")) == n_fields and res2["record_type"]["reused"], res2["counts"])
check("a rerun sends no record again", len(fk.posts("/records/bulk")) == n_bulk and res2["records"]["loaded"] == 0 and res2["records"]["already_loaded"] == 3)
check("a rerun reads what each reused field became (PRE- or clean) from Planning", by2["fDate"]["outcome"] == "pre-converted" and by2["fName"]["outcome"] == "clean" and by2["fDate"]["final_type"] == "text" and by2["fTags"]["outcome"] == "pre-converted")

# ---- the 20 long-text cap, and a failing field that never stops the run
fk2 = Fake(); fk2.refuse_names = {"Memo 05", "PRE-Memo 05"}
resy, _c = go(fk2, "tY")
longs = [f for f in resy["fields"] if f["final_type"] == "long-text"]
texts = [f for f in resy["fields"] if f["outcome"] == "pre-converted" and f["final_type"] == "text"]
check("at most 20 fields of a record type become long text; the extra is plain text and logged as possibly truncated", len(longs) == 20 and len(texts) == 1 and "possibly truncated" in texts[0]["note"], (len(longs), len(texts)))
check("a field Planning refuses in both attempts is failed, with both errors recorded, and the run still loads the records", [f["outcome"] for f in resy["fields"]].count("failed") == 1 and resy["records"]["loaded"] == 1
      and [f for f in resy["fields"] if f["outcome"] == "failed"][0]["attempt2"] == "refused", resy["counts"])

# ---- a record failing does not stop the others
fk4 = Fake(); fk4.fail_index = {1}
dbp4 = os.path.join(tmp, "base4.db"); shutil.copy(dbp, dbp4)
c4 = sqlite3.connect(dbp4); [c4.execute("delete from " + t) for t in ("load_id_map", "target_mappings", "record_pass_markers", "source_records")]; c4.commit(); c4.close()
res4, _c = go(fk4, "tX", db=dbp4)
check("a failing record is counted and listed by its record ID, and the others load", res4["records"]["failed"] >= 1 and res4["records"]["loaded"] >= 1 and all(f["record_id"].startswith("rec") for f in res4["records"]["failures"]), res4["records"])

# ---- the missing saved file: a re-read from Airtable is used and said so
dbp5 = os.path.join(tmp, "base5.db"); shutil.copy(dbp, dbp5)
c5 = sqlite3.connect(dbp5); [c5.execute("delete from " + t) for t in ("load_id_map", "target_mappings", "record_pass_markers", "source_records")]; c5.commit(); c5.close()
fetched = []
def fetch(b, t):
    fetched.append((b, t)); return {"format": "airtable-table-extract/1", "table_id": t, "records": [{"id": "recQ1", "fields": {"fName": SECRET + " reread"}}]}
conn5 = sqlite3.connect(dbp5); conn5.row_factory = sqlite3.Row
ctx5 = tl.planning_context(cfgp, "dev", True); tr5 = tl.transport(ctx5, opener=Fake()); calls5 = em.CallLog(); tr5.http.recorder = calls5
res5 = em.run_table(conn5, BASE, "tX", ctx5, tr5, os.path.join(tmp, "nowhere"), calls5, fetch_doc=fetch)
check("with no saved file the table is re-read through the supplied fetcher, and the result says so", fetched == [(BASE, "tX")] and res5.get("reread") and "re-read from Airtable" in res5["scan_source"] and res5["records"]["loaded"] == 1, res5["notes"])

# ---- the optional database table (draft migration 025)
check("a database built from the schema library already has the results table, so the field results are saved", res["db_written"] is True and "emergency_field_results" in res["db_note"], res["db_note"])
dbp6 = os.path.join(tmp, "base6.db"); shutil.copy(dbp, dbp6)
c6 = sqlite3.connect(dbp6); [c6.execute("delete from " + t) for t in ("load_id_map", "target_mappings", "record_pass_markers", "source_records")]
c6.execute("drop table emergency_field_results"); c6.commit()
res6x, conn6x = go(Fake(), "tX", db=dbp6)
check("on an older database without the results table the write is skipped, with a plain note", res6x["db_written"] is False and "emergency_field_results" in res6x["db_note"], res6x["db_note"])
[c6.execute("delete from " + t) for t in ("load_id_map", "target_mappings", "record_pass_markers", "source_records")]
c6.executescript(open(os.path.join(ROOT, "sql", "migration_025_add_emergency_field_results.sql"), encoding="utf-8").read()); c6.executescript(open(os.path.join(ROOT, "sql", "migration_025_add_emergency_field_results.sql"), encoding="utf-8").read()); c6.commit(); c6.close()
res6, conn6 = go(Fake(), "tX", db=dbp6)
conn6.commit()
n6 = conn6.execute("select count(*) from emergency_field_results").fetchone()[0]
check("migration 025 can be run twice, and with it applied one row per field is saved", res6["db_written"] and n6 == 10, (res6["db_note"], n6))
row = conn6.execute("select * from emergency_field_results where airtable_field_id='fDate'").fetchone()
check("a saved row has the outcome, the attempted type, the error code, the max length and the Planning field ID", row["outcome"] == "pre-converted" and row["attempted_planning_type"] == "date" and row["error_code"] == "INVALID_FIELD_TYPE"
      and row["max_length"] == 10 and row["planning_field_id"] and row["airtable_field_name"] == "Due Date" and row["total_records"] == 3)

# ---- the reports
out = os.path.join(tmp, "out", "emergency")
wrap = lambda html, title: ("Subject: %s\r\n\r\n" % title).encode("ascii") + html.encode("utf-8")
red = lambda t: t.replace(TOKEN, "[removed]")
names = er.write_reports(res, out, wrap, "body{}", redact=red)
check("three files with the same folder and name stem", sorted(os.listdir(out)) == sorted(names.values()) and len({n.rsplit("_", 1)[0] for n in names.values()}) == 1 and names["fields"].endswith("_fields.mhtml")
      and names["apilog"].endswith("_apilog.mhtml") and names["csv"].endswith("_fields.csv") and names["fields"].startswith("Git-R-Done_" + BASE + "_tX_"), names)
fields_html = open(os.path.join(out, names["fields"]), encoding="utf-8").read()
api_html = open(os.path.join(out, names["apilog"]), encoding="utf-8").read()
csv_text = open(os.path.join(out, names["csv"]), encoding="utf-8").read()
check("the field report has the summary, the scrolling window and a row per field with the outcome and both attempts", "tablewin" in fields_html and "Summary" in fields_html and fields_html.count("<tr class=\"o-") == 10
      and "INVALID_FIELD_TYPE" in fields_html and "PRE-Due Date" in fields_html and "Gadget Orders" in fields_html)
check("a failed or converted field points at its log entries with a relative link into the API log report", ("see log entries" in fields_html or "log entries" in fields_html) and ('href="%s#e' % names["apilog"]) in fields_html)
check("the API log report has three collapsed sections with call and failure counts, and no script", api_html.count("<details") >= 3 and "<details open" not in api_html and "<script" not in api_html.lower()
      and "Record type creation, 1 call, 0 failed" in api_html and "Field creation, " in api_html and "Records load, " in api_html, api_html[api_html.find("<details"):api_html.find("<details") + 300])
failed_calls = [e for e in res["calls"] if e["status"] and e["status"] >= 400]
check("the API log lists every call with number, time, endpoint, status, duration, request ID and error code, successes and failures", len(res["calls"]) >= 12 and failed_calls and failed_calls[0]["error_code"] == "INVALID_FIELD_TYPE"
      and failed_calls[0]["request_id"] and all(e["duration_ms"] is not None and e["timestamp"] for e in res["calls"]) and [e["number"] for e in res["calls"]] == list(range(1, len(res["calls"]) + 1)))
check("the failed field call count shown in the field-creation heading matches the log", ("Field creation, %d calls, %d failed" % (len([e for e in res["calls"] if e["phase"] == "fields"]), len([e for e in res["calls"] if e["phase"] == "fields" and not (e["status"] and 200 <= e["status"] < 300)]))) in api_html)
check("the CSV has a header and one row per field with both attempts", csv_text.splitlines()[0].startswith("base_name,base_id,table_name,table_id,airtable_field_name") and len(list(__import__("csv").reader(io.StringIO(csv_text)))) == 11 and "INVALID_FIELD_TYPE" in csv_text)
check("the option list of a select field is in the report (schema metadata)", "Red" in fields_html and any(r[11] == '["Red", "Blue"]' for r in __import__("csv").reader(io.StringIO(csv_text))))

check("the CSV carries base, table, workspace and record type, the API call counts per field, and the options as JSON", all(h in csv_text.splitlines()[0] for h in ("base_name", "workspace_name", "workspace_id", "record_type_name", "record_type_id", "proposed_data_type", "implemented_data_type", "api_calls", "api_successes", "api_failures", "cardinality_is_capped", "airtable_options_json", "longest_value_length")))
check("each field counts its own calls: a refused then accepted field has 2 calls, 1 success, 1 failure", by["fDate"]["api_calls"] == 2 and by["fDate"]["api_successes"] == 1 and by["fDate"]["api_failures"] == 1 and by["fName"]["api_calls"] == 1 and by["fLink"]["api_calls"] == 0, by["fDate"])

# ---- the preparatory step: AirtableRecordId first, as the primary field, then the default fields go, BEFORE any Airtable field is added
rt = res["record_type"]["planning_id"]
pre = res["prepare"]
order = [(x[0], x[1].split("/v2")[1].split("?")[0], x[2] if isinstance(x[2], dict) else None) for x in fk.reqs]
first_field = next(i for i, x in enumerate(order) if x[0] == "POST" and x[1].endswith("/fields"))
last_default_delete = max(i for i, x in enumerate(order) if x[0] == "DELETE")
last_field_post = max(i for i, x in enumerate(order) if x[0] == "POST" and x[1].endswith("/fields"))
check("the first field created is AirtableRecordId, as text", order[first_field][2] == {"displayName": "AirtableRecordId", "type": "text"}, order[first_field])
patch_i = next(i for i, x in enumerate(order) if x[0] == "PATCH")
check("it is made the primary field, and that comes right after it is created", order[patch_i][2] == {"displayName": "Gadget Orders", "primaryFieldId": pre["field_id"]} and patch_i > first_field and fk.primary[rt] == pre["field_id"], order[patch_i])
check("every default field is deleted, and all deletes happen BEFORE the second field is created", sorted(x["name"] for x in pre["deleted"]) == ["Description", "End Date", "Name", "Start Date", "Status"]
      and last_default_delete < [i for i, x in enumerate(order) if x[0] == "POST" and x[1].endswith("/fields")][1], pre)
check("the Airtable field named like a default (Order Name is not, but the table has none) leaves no default behind", not any(f["displayName"] in ("Description", "End Date", "Start Date", "Status") for f in fk.fields[rt]) and pre["primary"] and pre["created"] == "created")
check("the fields of the new record type are AirtableRecordId plus the run's own, nothing else", {f["displayName"] for f in fk.fields[rt]} >= {"AirtableRecordId", "Order Name", "PRE-Due Date"} and "Name" not in {f["displayName"] for f in fk.fields[rt]})
check("AirtableRecordId is not counted among the Airtable fields of the result", len(res["fields"]) == 10 and all(f["field_name"] != "AirtableRecordId" for f in res["fields"]))
check("each record carries its own Airtable record ID in AirtableRecordId", [d.get(pre["field_id"]) for d in sent] == ["recX1", "recX2", "recX3"], sent[:1])
check("the primary field is recorded in the ID map under the table", rows["P-K-tX"]["workfront_id"] == pre["field_id"] and rows["P-K-tX"]["name"] == "AirtableRecordId" and rows["P-K-tX"]["table_id"] == "tX" and rows["P-K-tX"]["airtable_field_id"] is None)
# a rerun on the same record type: nothing is created twice and the run's own fields are never deleted
n_before = len(fk.reqs)
res_b, conn_b = go(fk, "tX")
again = [x for x in fk.reqs[n_before:]]
check("a rerun creates no second AirtableRecordId and deletes nothing it made", not any(x[0] == "POST" and isinstance(x[2], dict) and x[2].get("displayName") == "AirtableRecordId" for x in again)
      and not any(x[0] == "DELETE" for x in again) and res_b["prepare"]["created"] == "already_created" and res_b["prepare"]["primary"], [x[:2] for x in again if x[0] != "GET"])
# a failing delete is a note, not the end of the run
# the primary field cannot be set: nothing is deleted, no Airtable field is added, the run says why
dbp7 = os.path.join(tmp, "base7.db"); shutil.copyfile(dbp, dbp7)
c7 = sqlite3.connect(dbp7); c7.execute("delete from load_id_map"); c7.execute("delete from target_mappings"); c7.execute("delete from record_pass_markers"); c7.commit(); c7.close()
fk7 = Fake(); fk7.refuse_primary = True
res7, _c7 = go(fk7, "tX", db=dbp7)
check("when the primary field cannot be set the run stops: no default field is deleted and no Airtable field is added",
      not res7["prepare"]["primary"] and "primary" in res7["prepare"]["error"] and not any(x[0] == "DELETE" for x in fk7.reqs) and len(fk7.posts("/fields")) == 1 and res7["fields"] == [] and res7["records"]["loaded"] == 0, res7["prepare"])
# a delete Planning refuses (a 403 in the first real run) becomes a rename: no more deletes are tried, and no name can clash with an Airtable field
dbp8 = os.path.join(tmp, "base8.db"); shutil.copyfile(dbp, dbp8)
c8 = sqlite3.connect(dbp8); c8.execute("delete from load_id_map"); c8.execute("delete from target_mappings"); c8.execute("delete from record_pass_markers"); c8.commit(); c8.close()
fk8 = Fake(); fk8.refuse_delete = {"DEF1_%d" % i for i in range(5)}
res8, _c8 = go(fk8, "tX", db=dbp8)
p8 = res8["prepare"]; rt8 = res8["record_type"]["planning_id"]
check("when every delete is refused the default fields are renamed instead, with a prefix", sorted(x["new_name"] for x in p8["renamed"]) == ["Unused default - " + n for n in ("Description", "End Date", "Name", "Start Date", "Status")] and not p8["deleted"] and not p8["failed"], p8)
check("the refusal note says what the token may do with the field (the permission flags)", "canDelete=false" in "".join(res8["notes"]) and p8["permissions"] == {"canView": True, "canEdit": True, "canDelete": False, "canAdd": True}, res8["notes"])
check("after the first refused delete no other delete is tried", len([x for x in fk8.reqs if x[0] == "DELETE"]) == 1 and len([x for x in fk8.reqs if x[0] == "PATCH" and "/v2/fields/" in x[1]]) == 5)
check("the renamed fields are in Planning under their new names, and the run still loads the table", {f["displayName"] for f in fk8.fields[rt8]} >= {"Unused default - Name", "AirtableRecordId", "Order Name"} and "Name" not in {f["displayName"] for f in fk8.fields[rt8]}
      and res8["records"]["loaded"] == 3 and any("renamed" in n for n in res8["notes"]), (p8, res8["notes"]))
# neither a delete nor a rename works: named in a note, the run goes on
dbp9 = os.path.join(tmp, "base9.db"); shutil.copyfile(dbp, dbp9)
c9 = sqlite3.connect(dbp9); c9.execute("delete from load_id_map"); c9.execute("delete from target_mappings"); c9.execute("delete from record_pass_markers"); c9.commit(); c9.close()
fk9 = Fake(); fk9.refuse_delete = {"DEF1_%d" % i for i in range(5)}; fk9.refuse_rename = {"DEF1_1"}
res9, _c9 = go(fk9, "tX", db=dbp9)
check("a field that can be neither deleted nor renamed is named in a note, and the run still loads the table", [x["name"] for x in res9["prepare"]["failed"]] == ["Description"] and len(res9["prepare"]["renamed"]) == 4
      and any("Description" in n for n in res9["notes"]) and res9["records"]["loaded"] == 3, (res9["prepare"], res9["notes"]))
# a delete Planning accepts for some fields and refuses for none: nothing is renamed
check("when deletes work nothing is renamed", not pre["renamed"] and not any(x[0] == "PATCH" and "/v2/fields/" in x[1] for x in fk.reqs))

# ---- nothing secret anywhere
everything = json.dumps(res, default=sorted) + json.dumps(resy, default=sorted) + fields_html + api_html + csv_text
check("no record value and no token is in a result, a report or the CSV", SECRET not in everything and TOKEN not in everything, [w for w in (SECRET, TOKEN) if w in everything])
dump = "\n".join(dbc.iterdump()) if False else "".join(str(tuple(r)) for t in ("load_id_map", "source_records", "target_mappings", "record_pass_markers") for r in dbc.execute("select * from " + t))
dump6 = "".join(str(tuple(r)) for r in conn6.execute("select * from emergency_field_results"))
check("no record value and no token is in the database rows the run wrote", SECRET not in dump + dump6 and TOKEN not in dump + dump6)
check("the text of a failing record call is withheld from the log (it could echo a value)", all((e["error_text"] or "").find(SECRET) < 0 for e in res4["calls"]) and any(e["phase"] == "records" for e in res4["calls"]))
shutil.rmtree(tmp, ignore_errors=True)
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
