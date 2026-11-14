"""HTTP test for /api/select-values. Fabricated schema and a fabricated saved record file."""
import json, os, sys, urllib.request
B = "http://127.0.0.1:8765"; ok = True; BASE = "appSelVals00001"
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])
def post(p, b):
    req = urllib.request.Request(B + p, method="POST", data=json.dumps(b).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
def fld(i, n, t="singleLineText", o=None):
    d = {"id": "fld" + i, "name": n, "type": t}
    if o: d["options"] = o
    return d
ch = lambda *names: {"choices": [{"id": "sel%s%d" % (n[0], i), "name": n, "color": "blueLight"} for i, n in enumerate(names)]}
schema = {"tables": [
    {"id": "tblSv0000000001", "name": "Tickets", "primaryFieldId": "fldV01", "views": [], "fields": [fld("V01", "Title"), fld("V02", "Stage", "singleSelect", ch("New", "Done")), fld("V03", "Labels", "multipleSelects", ch("Red", "Blue"))]},
    {"id": "tblSv0000000002", "name": "Notes", "primaryFieldId": "fldW01", "views": [], "fields": [fld("W01", "Text"), fld("W02", "Kind", "singleSelect", ch("A"))]},
    {"id": "tblSv0000000003", "name": "Dropped", "primaryFieldId": "fldY01", "views": [], "fields": [fld("Y01", "Text"), fld("Y02", "Kind", "singleSelect", ch("A"))]}]}
post("/api/db/create", {"path": "selvals.db"}); post("/api/db/connect", {"path": "selvals.db"})
s, r = post("/api/schema/ingest", {"base_id": BASE, "source": "json", "schema_json": json.dumps(schema)})
check("fabricated schema ingested", r.get("tables") == 3, r)
s, r = post("/api/names/tables/scope", {"base_id": BASE, "actor": "JV", "items": [{"table_id": "tblSv0000000003", "decision": "out", "reason": "test"}]})
check("one table is put out of scope", s == 200, r)
s, r = post("/api/select-values", {"base_id": BASE})
check("no saved record files: tables with a select field are listed as not checked, none guessed", s == 200 and r["tables"] == [] and len(r["not_checked"]) == 2 and "Also save every record" in r["not_checked"][0]["reason"], r)
os.makedirs("data_extract/%s" % BASE, exist_ok=True)
recs = [{"id": "recT%02d" % i, "createdTime": "x", "fields": f} for i, f in enumerate([
    {"fldV02": "New", "fldV03": ["Red"]}, {"fldV02": "SECRETVALUE"}, {"fldV02": "new"}, {"fldV02": "Done", "fldV03": ["Red", "Green"]}], 1)]
def write(tid, name, fields, records):
    doc = {"format": "airtable-table-extract/1", "base_id": BASE, "table_id": tid, "table_name": name, "captured_at": "2026-01-01T00:00:00Z", "fields": fields, "records": records, "record_count": len(records)}
    json.dump(doc, open("data_extract/%s/%s.json" % (BASE, tid), "w", encoding="utf-8"))
write("tblSv0000000001", "Tickets", [{"field_id": "fldV02"}, {"field_id": "fldV03"}], recs)
write("tblSv0000000002", "Notes", [{"field_id": "fldW02"}], [{"id": "recN01", "fields": {"fldW02": "A"}}])
write("tblSv0000000003", "Dropped", [{"field_id": "fldY02"}], [{"id": "recD01", "fields": {"fldY02": "Q"}}])
s, r = post("/api/select-values", {"base_id": BASE})
tk = {t["table_name"]: t for t in r["tables"]}
check("the out-of-scope table is not checked; the others are", s == 200 and set(tk) == {"Tickets", "Notes"} and not r["not_checked"], r.get("tables"))
st = {f["field_name"]: f for f in tk["Tickets"]["fields"]}
check("single select: unmatched, near match and counts", st["Stage"]["unmatched_values"] == 2 and st["Stage"]["near_matches"] == 1 and st["Stage"]["unmatched_records"] == 2 and st["Stage"]["unused_options"] == [], st["Stage"])
check("multiple select: an undeclared item is found", st["Labels"]["unmatched_values"] == 1 and st["Labels"]["unused_options"] == ["Blue"], st["Labels"])
check("the clean table has nothing unmatched", tk["Notes"]["fields"][0]["unmatched_values"] == 0)
check("totals add up", r["totals"]["fields"] == 3 and r["totals"]["fields_with_unmatched"] == 1 + 0 + 1 and r["totals"]["unmatched_values"] == 3 and r["totals"]["unmatched_records"] == 3, r["totals"])
blob = json.dumps(r)
check("no record value text is in the response", "SECRETVALUE" not in blob and "Green" not in blob and '"new"' not in blob)
check("record values are not in the database", b"SECRETVALUE" not in open("selvals.db", "rb").read())
s, r1 = post("/api/select-values", {"base_id": BASE, "table_id": "tblSv0000000003"})
check("one table can be asked for explicitly, even if it is out of scope", s == 200 and len(r1["tables"]) == 1, r1)
check("an unknown table is refused", post("/api/select-values", {"base_id": BASE, "table_id": "tblNope"})[0] == 404)
check("a bad base ID is refused", post("/api/select-values", {"base_id": "x y"})[0] == 400)
open("data_extract/%s/tblSv0000000002.json" % BASE, "w").write("{SECRETVALUE bad")
s, r = post("/api/select-values", {"base_id": BASE})
check("an unreadable file is reported as not checked without echoing its content", s == 200 and any(n["table_name"] == "Notes" for n in r["not_checked"]) and "SECRETVALUE" not in json.dumps(r), r)
sys.exit(0 if ok else 1)
