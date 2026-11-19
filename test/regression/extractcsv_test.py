"""Tests for extractcsv.py (one saved extract -> one CSV) and its endpoint /api/extract-csv, which needs the app on port 8765 but no database, token or Workfront. Fabricated data only."""
import base64, csv, io, json, os, sys, urllib.error, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))
import extractcsv as ec
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:400])

LONG = 'Line one, with a comma\nLine "two" has quotes\r\nPath C:\\temp\\x and a/b, plus =SUM(A1)'
DOC = {"format": "airtable-extract-v1", "base_id": "appFakeBase0001", "table_id": "tblFake000001", "table_name": "Orders: 2026/Q4 <draft>?", "captured_at": "2026-11-19T10:15:00.000Z",
       "fields": [{"field_id": "fldA", "field_name": "Title", "data_type": "singleLineText"}, {"field_id": "fldB", "field_name": "Notes", "data_type": "multilineText"},
                  {"field_id": "fldC", "field_name": "Tags", "data_type": "multipleSelects"}, {"field_id": "fldD", "field_name": "Total", "data_type": "currency"},
                  {"field_id": "fldE", "field_name": "Done", "data_type": "checkbox"}, {"field_id": "fldF", "field_name": "Title", "data_type": "singleLineText"}],
       "records": [{"id": "recAAA", "createdTime": "2026-01-02T03:04:05.000Z", "fields": {"fldA": 'He said "hi", then left', "fldB": LONG, "fldC": ["a, b", "c\"d", "e"], "fldD": 1234.50, "fldE": True, "fldF": "dup"}},
                   {"id": "recBBB", "createdTime": "2026-01-03T03:04:05.000Z", "fields": {"fldA": "Caf\u00e9 \u2013 \u00fcber", "fldD": 0.0000001, "fldE": False, "fldG": {"url": "http://x/y", "n": 1}}},
                   {"id": "recCCC", "createdTime": "2026-01-04T03:04:05.000Z", "fields": {}}],
       "record_count": 3}
raw = json.dumps(DOC).encode("utf-8")
data, s = ec.convert(ec.parse(raw))
check("the CSV starts with a byte order mark and uses CRLF", data.startswith(b"\xef\xbb\xbf") and b"\r\n" in data)
rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline="")))
check("the first rows are the extract's facts, then two blank rows", rows[:7] == [["format", "airtable-extract-v1"], ["base_id", "appFakeBase0001"], ["table_id", "tblFake000001"], ["table_name", DOC["table_name"]],
      ["captured_at", DOC["captured_at"]], ["record_count", "3"], ["field_count", "7"]] and rows[7] == [] and rows[8] == [], rows[:9])
check("field_count counts the extra field found only in a record", s["field_count"] == 7 and s["record_count"] == 3)
check("then the field_id row, the data_type row and the heading row", rows[9][:3] == ["field_id", "", "fldA"] and rows[10][2:4] == ["singleLineText", "multilineText"]
      and rows[11] == ["record_id", "createdTime", "Title", "Notes", "Tags", "Total", "Done", "Title", "fldG"] and rows[10][8] == ec.OUT_OF_LIST, rows[9:12])
body = rows[12:]
check("one row per record, one cell per column", len(body) == 3 and all(len(r) == 9 for r in rows[9:]), [len(r) for r in rows])
check("commas, quotes, slashes, backslashes and line breaks in a value come back exactly", body[0][2] == 'He said "hi", then left' and body[0][3] == LONG, (body[0][2], body[0][3]))
check("an array stays in one cell as JSON text that reads back", json.loads(body[0][4]) == ["a, b", 'c"d', "e"], body[0][4])
check("a number keeps its digits, a boolean is true or false, a missing value is empty", body[0][5] == "1234.5" and body[0][6] == "true" and body[1][6] == "false" and body[1][3] == "" and body[2][2:] == [""] * 7, (body[0][5], body[1][5]))
check("a tiny number is not rounded away and an object stays in one cell", body[1][5] == "1E-7" and json.loads(body[1][8]) == {"url": "http://x/y", "n": 1}, body[1])
check("accents survive, and two fields with the same name keep their own columns", body[1][2] == "Caf\u00e9 \u2013 \u00fcber" and body[0][7] == "dup")
check("the record ID and created time lead each row", body[0][:2] == ["recAAA", "2026-01-02T03:04:05.000Z"] and body[2][:2] == ["recCCC", "2026-01-04T03:04:05.000Z"])
check("warnings name the extra field and the shared name, with no record value", len(s["warnings"]) == 2 and not any("dup" in w or "Caf" in w for w in s["warnings"]), s["warnings"])
check("the file name has the table ID, a clean table name and the capture date, safe on Windows", s["file_name"] == "tblFake000001_Orders_2026_Q4_draft_20261119.csv"
      and not set(s["file_name"]) & set('<>:"/\\|?* '), s["file_name"])
check("conversion is deterministic", ec.convert(ec.parse(raw))[0] == data)

d2 = dict(DOC, record_count=99)
check("a record_count that differs from the records present is reported, and the facts row shows what the file said", "record_count is 99" in ec.convert(ec.parse(json.dumps(d2).encode()))[1]["warnings"][0]
      and ["record_count", "99"] in list(csv.reader(io.StringIO(ec.convert(ec.parse(json.dumps(d2).encode()))[0].decode("utf-8-sig")))))
check("a file with a byte order mark is read", ec.parse(b"\xef\xbb\xbf" + raw)["table_id"] == "tblFake000001")
check("the name falls back when there is no table ID, name or date", ec.file_name({}) == "airtable_extract.csv" and ec.file_name({"table_name": "CON"}) == "CON.csv" and ec.file_name({"table_id": "tbl1", "table_name": "../..\\x"}) == "tbl1_x.csv")
check("a long table name is cut", len(ec.file_name({"table_id": "tbl1", "table_name": "n" * 500})) <= 30 + ec.MAX_NAME_LEN + 6)
for label, blob, word in (("empty", b"", "empty"), ("not JSON", b"{oops", "not valid JSON"), ("a list", b"[1]", "not an extract"), ("no records", b'{"fields": []}', "records"), ("not UTF-8", b"\xff\xfe\x00", "UTF-8"),
                          ("a field with no ID", b'{"fields":[{"field_name":"x"}],"records":[]}', "no field_id"), ("a record that is not an object", b'{"fields":[],"records":[5]}', "not an object")):
    try:
        ec.parse(blob); check("a file that is %s is refused" % label, False)
    except ec.ExtractError as e:
        check("a file that is %s is refused with a plain reason" % label, word in str(e), str(e))
try:
    ec.parse(b'{"fields": [], "records": [{"id": "recX", "fields": {"fldA": "SECRETVALUE"}}, 7]}')
except ec.ExtractError as e:
    check("a refusal names the position, not the value", "SECRETVALUE" not in str(e) and "Entry 2" in str(e), str(e))
empty = ec.convert(ec.parse(b'{"table_id":"tblE","fields":[],"records":[]}'))
check("an extract with no fields and no records still converts", empty[1]["record_count"] == 0 and empty[1]["field_count"] == 0 and empty[1]["file_name"] == "tblE.csv")

# ---- over HTTP
B = "http://127.0.0.1:8765"
def post(body):
    req = urllib.request.Request(B + "/api/extract-csv", method="POST", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r: return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e: return e.code, e.read(), dict(e.headers)
code, blob, h = post({"json_base64": base64.b64encode(raw).decode()})
check("the endpoint returns the same CSV as a download with a clean file name and the counts", code == 200 and blob == data and h["Content-Type"].startswith("text/csv")
      and 'filename="tblFake000001_Orders_2026_Q4_draft_20261119.csv"' in h["Content-Disposition"] and h["X-Record-Count"] == "3" and h["X-Field-Count"] == "7" and len(json.loads(h["X-Warnings"])) == 2, (code, h))
code, blob, h = post({"json_base64": base64.b64encode(b"{oops").decode()})
check("a bad file is a plain 400 with an error, not a crash", code == 400 and "not valid JSON" in json.loads(blob)["error"], (code, blob))
code, blob, h = post({"json_base64": "***not base64***"})
check("a damaged upload is a plain 400", code == 400 and "could not be read" in json.loads(blob)["error"], (code, blob))
code, blob, h = post({})
check("no file is a plain 400", code == 400, (code, blob))
for path, word in (("/extractcsv", b"Convert to CSV"), ("/extractcsv.js", b"/api/extract-csv")):
    with urllib.request.urlopen(B + path) as r: page = r.read()
    check("%s is served" % path, word in page)
with urllib.request.urlopen(B + "/common.js") as r:
    check("the page is listed in the navigation under Git-R-Done", r.read().index(b'key: "extractcsv"') > 0)
sys.exit(0 if ok else 1)
