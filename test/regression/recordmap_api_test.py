"""HTTP test for /api/records/register and /api/records/registry. Fabricated schema and fabricated saved record files."""
import json, os, sqlite3, sys, urllib.request
B = "http://127.0.0.1:8765"; ok = True; BASE = "appRecMap000001"
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])
def call(method, p, b=None):
    req = urllib.request.Request(B + p, method=method, data=None if b is None else json.dumps(b).encode(), headers={"Content-Type": "application/json"} if method == "POST" else {})
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
post = lambda p, b: call("POST", p, b)
def fld(i, n, t="singleLineText"):
    return {"id": "fld" + i, "name": n, "type": t}
T1, T2, T3 = "tblRm0000000001", "tblRm0000000002", "tblRm0000000003"
schema = {"tables": [{"id": T1, "name": "Tickets", "primaryFieldId": "fldR01", "views": [], "fields": [fld("R01", "Title")]},
                     {"id": T2, "name": "Notes", "primaryFieldId": "fldS01", "views": [], "fields": [fld("S01", "Text")]},
                     {"id": T3, "name": "Dropped", "primaryFieldId": "fldT01", "views": [], "fields": [fld("T01", "Text")]}]}
post("/api/db/create", {"path": "recmap.db"}); post("/api/db/connect", {"path": "recmap.db"})
s, r = post("/api/schema/ingest", {"base_id": BASE, "source": "json", "schema_json": json.dumps(schema)})
check("fabricated schema ingested", r.get("tables") == 3, r)
post("/api/names/tables/scope", {"base_id": BASE, "actor": "JV", "items": [{"table_id": T3, "decision": "out", "reason": "test"}]})
s, r = post("/api/records/register", {"base_id": BASE})
check("with no saved files nothing is registered and each table says why", s == 200 and r["tables"] == [] and len(r["not_registered"]) == 2 and "Also save every record" in r["not_registered"][0]["reason"], r)
os.makedirs("data_extract/%s" % BASE, exist_ok=True)
def write(tid, name, records):
    doc = {"format": "airtable-table-extract/1", "base_id": BASE, "table_id": tid, "table_name": name, "captured_at": "2026-01-01T00:00:00Z", "fields": [], "records": records, "record_count": len(records)}
    json.dump(doc, open("data_extract/%s/%s.json" % (BASE, tid), "w", encoding="utf-8"))
write(T1, "Tickets", [{"id": "recT03", "fields": {"fldR01": "SECRETVALUE one"}}, {"id": "recT01", "fields": {"fldR01": "SECRETVALUE two"}}, {"id": "recT02", "fields": {}}, {"id": "bad id!", "fields": {}}])
write(T2, "Notes", [{"id": "recN01", "fields": {}}])
write(T3, "Dropped", [{"id": "recD01", "fields": {}}])
s, r = post("/api/records/register", {"base_id": BASE})
tb = {t["table_name"]: t for t in r["tables"]}
check("record IDs are registered per table; the out-of-scope table is skipped; an invalid ID is counted, not stored", s == 200 and set(tb) == {"Tickets", "Notes"} and tb["Tickets"]["new"] == 3 and tb["Tickets"]["skipped_invalid"] == 1 and r["registered_total"] == 4, r)
c = sqlite3.connect("recmap.db")
rows = [tuple(x) for x in c.execute("SELECT source_record_uid, table_id, record_id FROM source_records ORDER BY source_record_uid")]
check("records are numbered in sorted order (table by table, then record ID)", rows == [(1, T1, "recT01"), (2, T1, "recT02"), (3, T1, "recT03"), (4, T2, "recN01")], rows)
check("no record value is in the database", b"SECRETVALUE" not in open("recmap.db", "rb").read())
s, r = post("/api/records/register", {"base_id": BASE})
check("registering again changes nothing and keeps every number", s == 200 and r["totals"]["new"] == 0 and r["totals"]["already"] == 4 and [tuple(x) for x in c.execute("SELECT source_record_uid, table_id, record_id FROM source_records ORDER BY source_record_uid")] == rows, r["totals"])
s, r = post("/api/records/register", {"base_id": BASE, "table_ids": [T3]})
check("an out-of-scope table can be registered when asked for by name", s == 200 and r["tables"][0]["new"] == 1, r)
check("an unknown table is refused", post("/api/records/register", {"base_id": BASE, "table_ids": ["tblNope"]})[0] == 404)
check("a bad base ID is refused", post("/api/records/register", {"base_id": "x y"})[0] == 400)
s, r = call("GET", "/api/records/registry?base_id=" + BASE)
check("the registry view gives counts only", s == 200 and r["registered"] == 5 and "SECRETVALUE" not in json.dumps(r) and all("record_id" not in t for t in r["tables"]), r)
s, r = call("GET", "/api/records/registry?base_id=%s&environment=sandbox1" % BASE)
t1 = [t for t in r["tables"] if t["table_id"] == T1][0]
check("with an environment it adds pass, target and attachment counts", s == 200 and t1["passes"] == {"records": 0, "links": 0, "attachments": 0, "core_backfill": 0} and r["attachments"]["pending"] == 0, r)
check("an unknown environment is refused", call("GET", "/api/records/registry?base_id=%s&environment=staging" % BASE)[0] == 400)
open("data_extract/%s/%s.json" % (BASE, T2), "w").write("{SECRETVALUE bad")
s, r = post("/api/records/register", {"base_id": BASE})
check("an unreadable file is reported as not registered without echoing its content", s == 200 and any(n["table_name"] == "Notes" for n in r["not_registered"]) and "SECRETVALUE" not in json.dumps(r), r)
# ---- the Stage 4 screens
w = sqlite3.connect("recmap.db")
uids = {r[1]: r[0] for r in w.execute("SELECT source_record_uid, record_id FROM source_records")}
w.execute("INSERT INTO target_mappings(source_record_uid, system, environment, workspace_key, target_kind, target_record_id) VALUES (?,?,?,?,?,?)", (uids["recT02"], "planning", "dev", "wsDev", "record", "PLREC9"))
w.execute("INSERT INTO record_pass_markers(source_record_uid, pass, environment, workspace_key) VALUES (?,?,?,?)", (uids["recT02"], "records", "dev", "wsDev"))
w.execute("INSERT INTO attachment_map(source_record_uid, airtable_field_id, airtable_attachment_id, environment, api_size_bytes, status, last_error) VALUES (?,?,?,?,?,?,?)", (uids["recT01"], "fldR01", "attOne", "production", 20, "failed", "link expired"))
w.commit(); w.close()
s, r = call("GET", "/api/records/view?base_id=%s&environment=dev&system=planning&workspace_key=wsDev&sort=record_id&dir=desc" % BASE)
check("the record map screen is sorted on the server and shows IDs, targets and passes only", s == 200 and r["total"] == 5 and [x["record_id"] for x in r["rows"]][0] == "recT03" and "SECRETVALUE" not in json.dumps(r)
      and [x for x in r["rows"] if x["record_id"] == "recT02"][0]["targets"] == "record:PLREC9" and [x for x in r["rows"] if x["record_id"] == "recT02"][0]["passes"] == ["records"], r)
s, r = call("GET", "/api/records/view?base_id=%s&environment=dev&system=planning&workspace_key=wsDev&q=PLREC9&limit=1" % BASE)
check("it searches by Workfront ID and pages", s == 200 and r["total"] == 1 and r["rows"][0]["record_id"] == "recT02", r)
check("a Planning view without a Workspace, an unknown sort and a bad base are refused", call("GET", "/api/records/view?base_id=%s&environment=dev&system=planning" % BASE)[0] == 400
      and call("GET", "/api/records/view?base_id=%s&environment=dev&system=planning&workspace_key=wsDev&sort=x" % BASE)[0] == 400 and call("GET", "/api/records/view?base_id=x%%20y&environment=dev")[0] == 400)
s, r = call("GET", "/api/records/attachments?base_id=%s&environment=production&status=failed" % BASE)
check("the attachment screen lists the failed attachment with its short reason and no file name or link", s == 200 and r["total"] == 1 and r["rows"][0]["attachment_id"] == "attOne" and r["rows"][0]["last_error"] == "link expired" and "filename" not in r["rows"][0], r)
check("an unknown attachment status is refused", call("GET", "/api/records/attachments?base_id=%s&environment=production&status=x" % BASE)[0] == 400)
for path, word in (("/load", "Record map"), ("/load.js", "records/view")):
    with urllib.request.urlopen(B + path) as resp:
        check("%s is served" % path, resp.status == 200 and word in resp.read().decode("utf-8"))
sys.exit(0 if ok else 1)
