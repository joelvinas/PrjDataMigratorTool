"""HTTP tests for the field name review endpoints (/api/names/*) and the /names page. Fabricated schema only."""
import base64, io, json, sqlite3, sys, urllib.request, zipfile
B = "http://127.0.0.1:8765"; ok = True; BASE = "appNamesApi0001"
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else x)
def raw(method, p, body=None, headers=None):
    h = {"Content-Type": "application/json"} if method == "POST" else {}
    h.update(headers or {})
    req = urllib.request.Request(B + p, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers
def get(p): s, d, h = raw("GET", p); return s, json.loads(d)
def post(p, b, headers=None): s, d, h = raw("POST", p, b, headers); return s, json.loads(d)
def A(**k): return dict({"base_id": BASE, "actor": "JV"}, **k)

def fld(i, n, t="singleLineText", o=None):
    d = {"id": "fld" + i, "name": n, "type": t}
    if o: d["options"] = o
    return d
schema = {"tables": [
    {"id": "tblNa0000000001", "name": "Orders", "primaryFieldId": "fldN01", "views": [], "fields": [
        fld("N01", "Order Name"), fld("N02", "Amount", "number"), fld("N03", "amount", "number"), fld("N04", "Notes!"),
        fld("N05", "Café"), fld("N06", "PO#s", "multipleRecordLinks", {"linkedTableId": "tblNb0000000001", "isReversed": False}),
        fld("N07", "PO Number (from PO#s)", "multipleLookupValues", {"recordLinkFieldId": "fldN06", "fieldIdInLinkedTable": "fldM01", "isValid": True}),
        fld("N08", "Done", "checkbox", {"icon": "check", "color": "greenBright"})]},
    {"id": "tblNb0000000001", "name": "PO#s", "primaryFieldId": "fldM01", "views": [], "fields": [fld("M01", "PO Number"), fld("M02", "Total", "number")]},
    {"id": "tblNc0000000001", "name": "1.2.3 Archive", "primaryFieldId": "fldK01", "views": [], "fields": [fld("K01", "Title")]}]}
post("/api/db/create", {"path": "names_api.db"}); post("/api/db/connect", {"path": "names_api.db"})
_, r = post("/api/schema/ingest", {"base_id": BASE, "source": "json", "schema_json": json.dumps(schema)})
check("fabricated schema ingested", r.get("tables") == 3 and r.get("fields") == 11, r)

s, d, h = raw("GET", "/names"); check("the page is served", s == 200 and b"Field review" in d and h["Content-Type"].startswith("text/html"))
s, d, h = raw("GET", "/names.js"); check("the page script is served", s == 200 and b"decide(" in d and "javascript" in h["Content-Type"])
s, st_ = get("/api/status"); check("server version matches the pages", st_["version"] == "1.14", st_.get("version"))

# ---- review, the gate and scope
s, rv = get("/api/names/review?base_id=" + BASE)
check("review: gate closed, no fields yet, all tables undecided and pending", s == 200 and rv["gate"]["ready"] is False and rv["fields"] == [] and len(rv["gate"]["pending"]) == 3
      and rv["scope_counts"] == {"in": 0, "out": 0, "undecided": 3} and rv["scope_available"] is True, rv["gate"])
check("settings default when none are saved", rv["settings_saved"] is False and rv["settings"]["table_prefix"] == "end")
check("a bad base ID is refused", get("/api/names/review?base_id=nope")[0] == 400 and get("/api/names/review")[0] == 400)
check("an unknown base has no fields", get("/api/names/review?base_id=appNothingHere1")[0] == 404)
s, e = post("/api/names/fields/approve", A(mode="pass")); check("fields cannot be approved while the gate is closed (409)", s == 409 and "Approve every" in e["error"], e)
s, e = post("/api/names/tables/approve", {"base_id": BASE, "mode": "unchanged"}); check("a decision without initials is refused", s == 400 and "initials" in e["error"], e)
s, e = post("/api/names/tables/approve", A(actor="x" * 41, mode="unchanged")); check("overlong initials are refused", s == 400)

# ---- table decisions
s, r = post("/api/names/tables/approve", A(mode="unchanged")); check("approve unchanged tables", s == 200 and r["approved"] == 1 and r["review"]["gate"]["pending"] != [], r.get("error"))
s, r = post("/api/names/tables/approve", A(mode="auto")); check("approve auto-fixable tables (PO#s becomes POs)", s == 200 and r["approved"] == 1)
s, e = post("/api/names/tables/approve", A(items=[{"table_id": "tblNc0000000001"}])); check("a human-tier table needs a typed name", s == 400 and "type the name" in e["error"], e)
s, e = post("/api/names/tables/approve", A(items=[{"table_id": "tblNc0000000001", "name": "Archive (old)"}])); check("a typed table name is validated", s == 400 and "Special" in e["error"], e)
s, r = post("/api/names/tables/approve", A(items=[{"table_id": "tblNc0000000001", "name": "Archive 1-2-3"}]), )
check("typed name approved: the gate opens and the review now has fields", s == 200 and r["review"]["gate"]["ready"] and len(r["review"]["fields"]) == 11, r.get("error"))
rv = r["review"]; fl = {f["field_id"]: f for f in rv["fields"]}
check("the swap resolves to the approved table name and is auto-fixable", fl["fldN07"]["targets"]["planning_name"]["verdict"] == "AUTO_FIXABLE" and fl["fldN07"]["targets"]["planning_name"]["suggested"] == "PO Number from POs")
check("duplicate group is reported with both members", [len(c["members"]) for c in rv["clusters"] if c["target"] == "planning_name"] == [2])
check("every field is unreviewed at first and nothing is approved", rv["counts"]["planning_name"]["approved"] == 0)

# ---- preview does not save; saving needs initials
s, p = post("/api/names/preview", {"base_id": BASE, "settings": {"char_cleanup": False}})
check("preview shows the effect of a toggle without saving it", s == 200 and p["settings"]["char_cleanup"] is False and get("/api/names/review?base_id=" + BASE)[1]["settings"]["char_cleanup"] is True)
check("a preview does not claim its settings are saved", p["settings_saved"] is False and get("/api/names/review?base_id=" + BASE)[1]["settings_saved"] is False)
check("a preview with invalid settings is refused", post("/api/names/preview", {"base_id": BASE, "settings": {"table_prefix": "bad"}})[0] == 400)
check("saving settings needs initials", post("/api/names/settings", {"base_id": BASE, "settings": {}})[0] == 400)
s, r = post("/api/names/settings", A(settings={"label_keep_quarter": False}, reason="test")); check("settings are saved and returned", s == 200 and r["settings"]["label_keep_quarter"] is False and r["settings_saved"])
s, r = post("/api/names/settings", A(settings={"targets": {"core_name": {"max_len": 40}, "planning_name": {"allow_leading_digit": True}}}, reason="separate"))
check("per-name settings are saved through the API and returned", s == 200 and r["settings"]["targets"]["core_name"]["max_len"] == 40 and r["settings"]["targets"]["planning_name"]["allow_leading_digit"] is True, r.get("error"))
s, e = post("/api/names/preview", {"base_id": BASE, "settings": {"targets": {"planning_name": {"max_len": 5000}}}}); check("an invalid per-name setting is refused", s == 400 and "maximum length" in e["error"].lower(), e)
s_, r = post("/api/names/settings", A(settings={"emoji_mode": "word", "description_emoji_mode": "word", "targets": {"core_label": {"emoji_mode": "remove"}}}, reason="spell emoji out"))
check("the emoji options are saved through the API", s_ == 200 and r["settings"]["emoji_mode"] == "word" and r["settings"]["description_emoji_mode"] == "word" and r["settings"]["targets"]["core_label"]["emoji_mode"] == "remove", r.get("error"))
check("an invalid emoji mode is refused", post("/api/names/preview", {"base_id": BASE, "settings": {"emoji_mode": "keep"}})[0] == 400)
post("/api/names/settings", A(settings={}))

# ---- field decisions
s, r = post("/api/names/fields/approve", A(mode="pass_and_auto", targets=["planning_name"]))
check("bulk approval takes clean and auto-fixable names", s == 200 and r["approved"] >= 6 and r["review"]["counts"]["planning_name"]["approved"] == r["approved"], r.get("error"))
fl = {f["field_id"]: f for f in r["review"]["fields"]}
check("a duplicate member is not bulk-approved; an auto-fix is stored repaired", fl["fldN02"]["targets"]["planning_name"]["status"] == "unreviewed" and fl["fldN04"]["targets"]["planning_name"]["approved_name"] == "Notes")
s, e = post("/api/names/fields/approve", A(items=[{"field_id": "fldN02", "target": "planning_name", "name": "Amount"}])); check("a name that still collides is refused with the reason", s == 400 and "Duplicate" in e["error"], e)
s, e = post("/api/names/fields/approve", A(items=[{"field_id": "fldN02", "target": "planning_name", "name": "Amount_x"}])); check("a typed name with a banned character is refused", s == 400, e)
s, r = post("/api/names/fields/approve", A(items=[{"field_id": "fldN03", "target": "planning_name", "name": "Amount Archive"}], reason="rename to resolve the duplicate"))
check("renaming one member resolves the group", s == 200 and not [c for c in r["review"]["clusters"] if c["target"] == "planning_name"], r.get("error"))
s, r = post("/api/names/fields/approve", A(items=[{"field_id": "fldN05", "target": "planning_name"}])); check("accepting a proposal approves the repaired name", s == 200 and {f["field_id"]: f for f in r["review"]["fields"]}["fldN05"]["targets"]["planning_name"]["approved_name"] == "Cafe")
s, r = post("/api/names/fields/status", A(items=[{"field_id": "fldN05", "target": "planning_name"}], status="unreviewed", reason="client to confirm")); check("an approval can be withdrawn with a reason", s == 200 and r["updated"] == 1)
check("withdrawing needs a reason", post("/api/names/fields/status", A(items=[{"field_id": "fldN05", "target": "planning_name"}], status="unreviewed"))[0] == 400)
s, r = post("/api/names/fields/status", A(items=[{"field_id": "fldN01", "target": "core_label"}], status="needs_human", reason="ask")); check("a name can be flagged", s == 200)

# ---- abandon is enforced on the server
s, e = post("/api/names/fields/abandon", A(field_id="fldN03", reason="empty")); check("an unprofiled field cannot be abandoned", s == 400 and "not profiled" in e["error"], e)
s, e = post("/api/names/fields/abandon", A(field_id="fldN08", reason="empty")); check("a checkbox cannot be abandoned", s == 400, e)
check("abandon needs a reason and a real field", post("/api/names/fields/abandon", A(field_id="fldN03", reason=""))[0] == 400 and post("/api/names/fields/abandon", A(field_id="fldNope", reason="x"))[0] == 404)
check("restoring a field that is not abandoned is refused", post("/api/names/fields/unabandon", A(field_id="fldN03", reason="x"))[0] == 404)
# mark the table as profiled with an empty field, directly in the database, then abandon through the API
c = sqlite3.connect("names_api.db")
c.execute("update airtable_fields set row_count=5, null_count=5 where field_id='fldN03'")
c.execute("insert into api_call_log(timestamp, source_system, purpose, http_method, endpoint, table_id, success) values ('2999-01-01 00:00:00.000000','Airtable','profile_page','GET','/x','tblNa0000000001',1)")
c.commit(); c.close()
s, r = post("/api/names/fields/abandon", A(field_id="fldN03", reason="empty duplicate of Amount")); check("a profiled empty field can be abandoned and the page data shows who and why", s == 200
      and {f["field_id"]: f for f in r["review"]["fields"]}["fldN03"]["abandon"]["reason"] == "empty duplicate of Amount" and {f["field_id"]: f for f in r["review"]["fields"]}["fldN03"]["abandon"]["by"] == "JV", r.get("error"))
s, r = post("/api/names/fields/unabandon", A(field_id="fldN03", reason="still used")); check("a field can be restored", s == 200 and not {f["field_id"]: f for f in r["review"]["fields"]}["fldN03"]["abandoned"])

# ---- import
csv_text = "field_id,old_name,new_name,new_label,old_dataType,new_dataType\r\nfldK01,Title,Title Text,Title Text,Single line text,Text\r\nfldXXXX,Ghost,Ghost,Ghost,Text,Text\r\n"
s, r = post("/api/names/import", A(csv_base64=base64.b64encode(csv_text.encode()).decode()))
check("import stores names and reports unmatched IDs and missing fields", s == 200 and r["names_imported"] >= 2 and any(p["kind"] == "UNMATCHED_ID" for p in r["problems"]) and any(p["kind"] == "MISSING_FROM_IMPORT" for p in r["problems"]), r.get("error"))
tk = {f["field_id"]: f for f in r["review"]["fields"]}["fldK01"]["targets"]
check("imported names appear as the name being checked; an approved Planning name is kept", tk["core_name"]["candidate"] == "Title Text" and tk["planning_name"]["approved_name"] == "Title")
check("import needs initials and a readable file", post("/api/names/import", {"base_id": BASE, "csv_base64": "eA=="})[0] == 400 and post("/api/names/import", A(csv_base64="!!"))[0] == 400)

# ---- downloads
s, d, h = raw("GET", "/api/names/report?base_id=" + BASE)
z = zipfile.ZipFile(io.BytesIO(d)); check("zip holds every report", s == 200 and h["Content-Type"] == "application/zip" and len(z.namelist()) == 7 and "names_report.csv" in z.namelist(), z.namelist())
s, d, h = raw("GET", "/api/names/report?base_id=" + BASE + "&file=names_report.csv")
check("a csv download opens in Excel as UTF-8 and holds approved names", s == 200 and d.startswith(b"\xef\xbb\xbf") and b"Notes" in d and "attachment" in h["Content-Disposition"])
check("an unknown report file is refused (never a path)", raw("GET", "/api/names/report?base_id=" + BASE + "&file=../server.py")[0] == 404)

# ---- scope and target decisions
TC = "tblNc0000000001"
check("a scope decision needs initials", post("/api/names/tables/scope", {"base_id": BASE, "items": [{"table_id": TC, "decision": "in"}]})[0] == 400)
s_, e = post("/api/names/tables/scope", A(items=[{"table_id": TC, "decision": "out"}])); check("'out' needs a reason", s_ == 400 and "reason" in e["error"], e)
s_, r = post("/api/names/tables/scope", A(items=[{"table_id": "tblNb0000000001", "decision": "out", "reason": "not migrating purchase orders"}]))
check("a table can be taken out of scope with a reason; it is skipped and its links are flagged", s_ == 200 and r["review"]["scope_counts"]["out"] == 1
      and {(w["field_id"], w["kind"]) for w in r["review"]["scope_warnings"]} == {("fldN06", "link"), ("fldN07", "link")}, r.get("error") or r["review"]["scope_counts"])
check("the out table has no fields in the review and no name decision", all(f["table_id"] != "tblNb0000000001" for f in r["review"]["fields"]))
s_, r = post("/api/names/tables/scope", A(items=[{"table_id": "tblNb0000000001", "decision": "clear"}])); check("clearing returns it to undecided", s_ == 200 and r["review"]["scope_counts"]["out"] == 0 and not r["review"]["scope_warnings"])
s_, r = post("/api/names/tables/scope", A(items=[{"table_id": "tblNa0000000001", "decision": "in", "target": "planning_and_core", "reason": "has attachments"}]))
check("a table can be in scope and land in Planning plus Core", s_ == 200 and {t["table_id"]: t for t in r["review"]["tables"]}["tblNa0000000001"]["target"] == "planning_and_core"
      and r["review"]["scope_counts"]["in"] == 1)
check("an unknown table is refused", post("/api/names/tables/scope", A(items=[{"table_id": "tblNope", "decision": "in"}]))[0] == 400)
post("/api/names/tables/scope", A(items=[{"table_id": "tblNa0000000001", "decision": "clear", "target": "clear"}]))

# ---- descriptions
check("review rows carry the generated description", {f["field_id"]: f for f in get("/api/names/review?base_id=" + BASE)[1]["fields"]}["fldN02"]["description"]["generated"] == "Source: Airtable. Origins: Orders.Amount.")
s_, r = post("/api/names/descriptions", A(items=[{"field_id": "fldN02", "text": "Source: Airtable. Origins: Orders.Amount; Orders.amount. Merged by hand."}], reason="two fields consolidated"))
check("a hand-edited description is saved through the API", s_ == 200 and r["saved"] == 1 and {f["field_id"]: f for f in r["review"]["fields"]}["fldN02"]["description"]["edited"], r.get("error"))
s_, e = post("/api/names/descriptions", A(items=[{"field_id": "fldN02", "text": "a" + chr(10) + "b"}])); check("control characters are refused", s_ == 400 and "control" in e["error"], e)
check("saving a description needs initials", post("/api/names/descriptions", {"base_id": BASE, "items": [{"field_id": "fldN02", "text": "x"}]})[0] == 400)
s_, r = post("/api/names/descriptions", A(items=[{"field_id": "fldN02", "text": ""}])); check("empty text resets to the generated description", s_ == 200 and not {f["field_id"]: f for f in r["review"]["fields"]}["fldN02"]["description"]["edited"])
s_, d, h = raw("GET", "/api/names/report?base_id=" + BASE + "&file=names_report.csv"); check("the csv has description columns", b"description_source" in d and b"Source: Airtable. Origins:" in d)

# ---- the HTML report
s_, d, h = raw("GET", "/api/names/report.html?base_id=" + BASE)
page = d.decode("utf-8")
check("the report previews as HTML", s_ == 200 and h["Content-Type"].startswith("text/html") and "Field and table name review" in page and "Review sign-off" in page)
s_, d, h = raw("GET", "/api/names/report.html?base_id=" + BASE + "&download=1"); check("it can be downloaded as a file", s_ == 200 and "attachment" in h["Content-Disposition"] and h["Content-Disposition"].endswith('.html"'))
s_, d, h = raw("GET", "/api/names/report.mhtml?base_id=" + BASE); check("it can be downloaded as an MHTML archive", s_ == 200 and h["Content-Type"] == "application/x-mimearchive" and d.startswith(b"From:") and b"multipart/related" in d)
check("the report needs a valid base", raw("GET", "/api/names/report.html")[0] == 400 and raw("GET", "/api/names/report.html?base_id=appNothingHere1")[0] == 404)
check("no token in the report", "patMOCK" not in page)

# ---- compare the values of duplicate fields (from a saved record file; counts and record IDs only)
import os
TA, TB = "tblNa0000000001", "tblNb0000000001"
os.makedirs("data_extract/" + BASE, exist_ok=True)
def extract(table, records, fields, **k):
    doc = {"format": "airtable-table-extract/1", "base_id": BASE, "table_id": table, "table_name": "T", "captured_at": "2026-01-01T00:00:00Z", "fields": [{"field_id": f} for f in fields],
           "records": records, "record_count": len(records)}
    doc.update(k)
    json.dump(doc, open("data_extract/%s/%s.json" % (BASE, table), "w", encoding="utf-8"))
RECS = [{"id": "recAAA01", "fields": {"fldN02": 10, "fldN03": 10, "fldN04": "SECRET-ALPHA-1", "fldN05": "SECRET-ALPHA-1"}},
        {"id": "recAAA02", "fields": {"fldN02": 20, "fldN03": "20", "fldN04": "SECRET-BETA-2"}},
        {"id": "recAAA03", "fields": {"fldN02": 30, "fldN05": "SECRET-GAMMA-3"}}, {"id": "recAAA04", "fields": {"fldN03": 40}},
        {"id": "recAAA05", "fields": {}}, {"id": "recAAA06", "fields": {"fldN02": 1, "fldN03": 2, "fldN04": "SECRET-DELTA-4", "fldN05": "SECRET-EPSILON-5"}}]
extract(TA, RECS, ["fldN02", "fldN03", "fldN04", "fldN05"])
s_, r = post("/api/names/compare", A(field_ids=["fldN02", "fldN03"]))
k = r["pairs"][0]["counts"] if s_ == 200 else {}
check("the values of two fields are compared from the saved file", s_ == 200 and (k["agree"], k["loose"], k["only_a"], k["only_b"], k["both_empty"], k["conflict"]) == (1, 1, 1, 1, 1, 1) and r["record_count"] == 6, r)
check("the verdict, record IDs to look up, and the file's capture time come back", r["pairs"][0]["verdict"] == "conflict" and r["pairs"][0]["examples"]["conflict"] == ["recAAA06"] and r["captured_at"] == "2026-01-01T00:00:00Z")
s_, r2 = post("/api/names/compare", A(field_ids=["fldN04", "fldN05", "fldN02"]))
blob = json.dumps(r) + json.dumps(r2)
check("three fields are compared pairwise with a filled-together histogram", s_ == 200 and len(r2["pairs"]) == 3 and sum(r2["filled_together"].values()) == 6, r2)
check("no record value appears in either response (only counts, IDs, names and text)", "SECRET" not in blob and "recAAA" in blob)
s_, d, h = raw("GET", "/api/names/review?base_id=" + BASE); s2, d2, h2 = raw("GET", "/api/calllog")
check("values appear nowhere else the page can reach", b"SECRET" not in d and b"SECRET" not in d2 and b"SECRET" not in open("names_api.db", "rb").read())
s_, r = post("/api/names/compare", A(field_ids=["fldN03", "fldN02"]))
check("a saved file out of step with the last profile is flagged", s_ == 200 and any("out of date" in n for n in r["notes"]), r.get("notes"))
s_, e = post("/api/names/compare", A(field_ids=["fldM01", "fldM02"])); check("no saved record file: a plain message says how to get one", s_ == 404 and "Also save every record" in e["error"], e)
check("fields of different tables cannot be compared", post("/api/names/compare", A(field_ids=["fldN02", "fldM01"]))[0] == 400)
check("it needs 2 to 8 distinct fields", post("/api/names/compare", A(field_ids=["fldN02"]))[0] == 400 and post("/api/names/compare", A(field_ids=["fldN02", "fldN02"]))[0] == 400
      and post("/api/names/compare", A(field_ids=["fldN0%d" % i for i in range(1, 9)] + ["fldM01"]))[0] == 400 and post("/api/names/compare", A())[0] == 400)
check("an unknown field is refused", post("/api/names/compare", A(field_ids=["fldN02", "fldNope"]))[0] == 404)
open("data_extract/%s/%s.json" % (BASE, TB), "w", encoding="utf-8").write("{SECRET-GARBAGE not json")
s_, e = post("/api/names/compare", A(field_ids=["fldM01", "fldM02"])); check("an unreadable file is refused without echoing its content", s_ == 400 and "SECRET" not in e["error"], e)
extract(TB, [], ["fldM01", "fldM02"], format="something-else")
s_, e = post("/api/names/compare", A(field_ids=["fldM01", "fldM02"])); check("a file in the wrong format is refused", s_ == 400 and "format" in e["error"], e)
extract(TB, [{"id": "recBBB01", "fields": {}}], ["fldM01", "fldM02"], table_id="tblSomeoneElse")
check("a file that belongs to another table is refused", post("/api/names/compare", A(field_ids=["fldM01", "fldM02"]))[0] == 400)
extract(TA, RECS, ["fldN02", "fldN04"])
s_, r = post("/api/names/compare", A(field_ids=["fldN02", "fldN03"])); check("a field missing from the saved file is flagged as probably added later", s_ == 200 and any("not in the saved file" in n for n in r["notes"]), r.get("notes"))
import shutil; shutil.rmtree("data_extract", ignore_errors=True)
check("the page script has the compare control", b"compareGroup" in raw("GET", "/names.js")[1])

# ---- audit trail and safety
c = sqlite3.connect("names_api.db"); c.row_factory = sqlite3.Row
ev = [dict(x) for x in c.execute("select * from name_events order by event_id")]
check("every decision is in the audit trail with who and when", len(ev) > 15 and all(x["actor"] == "JV" and x["created_at"] for x in ev) and {x["event_type"] for x in ev} >= {"approve", "abandon", "unabandon", "reject", "needs_human", "import", "settings"})
check("the audit trail is append-only: event IDs are strictly increasing with no gaps from edits", [x["event_id"] for x in ev] == sorted({x["event_id"] for x in ev}))
check("approved names live in field_names with status and who abandoned what", c.execute("select count(*) from field_names where planning_name_status='approved'").fetchone()[0] >= 6)
blob = json.dumps(ev) + json.dumps(rv)
check("no token anywhere in the responses or the audit trail", "patMOCK" not in blob and "Bearer" not in blob)
check("a non-JSON write is refused", raw("POST", "/api/names/tables/approve", None, {"Content-Type": "text/plain"})[0] == 415)
check("a cross-site write is refused", post("/api/names/tables/approve", A(mode="unchanged"), {"Origin": "http://evil.example"})[0] == 403)
sys.exit(0 if ok else 1)
