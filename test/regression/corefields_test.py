"""Tests for corefields.py and corefields_report.py (the Core bulk field creator) and draft migration 026. A fake opener stands in for Workfront; fabricated names and a fake key."""
import base64, io, json, os, re, sqlite3, sys, urllib.error, urllib.parse
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
import corefields as cf, corefields_report as rep, draftmigrations as dm, loadconfig as lc, workfronthttp as wh
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:400])

KEY = "fakeCOREkey0123456789abcdef"
API = "/attask/api/v21.0"

class Resp(object):
    def __init__(self, status, body): self.status, self._b, self.headers = status, json.dumps(body).encode(), {}
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False

class FakeCore(object):
    """An in-memory Workfront Core: PARAM create, search by name, read and delete. refuse = {name: (status, body)} makes a create fail; no_date drops lastUpdateDate from a create reply."""
    def __init__(self):
        self.params, self.n, self.reqs, self.refuse, self.no_date, self.refuse_delete = {}, 0, [], {}, False, set()
    def open(self, req, timeout=None):
        u = urllib.parse.urlparse(req.full_url)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        body = json.loads(req.data) if req.data else None
        hdr = {k.lower(): v for k, v in req.header_items()}
        self.reqs.append((req.get_method(), u.path[len(API):], q, body, hdr))
        path = u.path[len(API):]
        if hdr.get("apikey") != KEY:
            raise urllib.error.HTTPError(req.full_url, 401, "no", None, io.BytesIO(b'{"error":"bad key"}'))
        if req.get_method() == "GET" and path == "/param/search":
            hits = [dict(p) for p in self.params.values() if p["name"].casefold() == q["name"].casefold()]
            return Resp(200, {"data": [{k: v for k, v in h.items() if k in ("ID", "name", "lastUpdateDate")} for h in hits]})
        if req.get_method() == "POST" and path == "/param":
            if body["name"] in self.refuse:
                st, b = self.refuse[body["name"]]
                raise urllib.error.HTTPError(req.full_url, st, "x", None, io.BytesIO(json.dumps(b).encode()))
            self.n += 1
            pid = "P%04d" % self.n
            self.params[pid] = dict(body, ID=pid, lastUpdateDate="2026-11-2%d 10:00:00" % (self.n % 10))
            out = {"ID": pid, "name": body["name"]}
            if not self.no_date: out["lastUpdateDate"] = self.params[pid]["lastUpdateDate"]
            return Resp(201, {"data": out})
        if req.get_method() == "GET" and path.startswith("/param/"):
            p = self.params.get(path.rsplit("/", 1)[1])
            return Resp(200, {"data": {"ID": p["ID"], "lastUpdateDate": p["lastUpdateDate"]}})
        if req.get_method() == "DELETE" and path.startswith("/param/"):
            pid = path.rsplit("/", 1)[1]
            if pid in self.refuse_delete:
                raise urllib.error.HTTPError(req.full_url, 403, "no", None, io.BytesIO(b'{"error":{"class":"ForbiddenException","message":"cannot delete ' + KEY.encode() + b'"}}'))
            self.params.pop(pid, None)
            return Resp(200, {"data": {"success": True}})
        raise urllib.error.HTTPError(req.full_url, 404, "x", None, io.BytesIO(b"{}"))

def client(fake):
    creds = lc.credentials({"workfront": {"core": {"sandbox1": {"base_url": "https://acme.sb01.workfront.com", "api_path": API, "api_key": KEY}}}}, "core", "sandbox1")
    h = wh.Http(creds, {KEY}, opener=fake, sleep=lambda s: None)
    log = cf.CallLog(); h.recorder, h.record_full = log, True
    return h, log

def upload(rows, header=None):
    text = ",".join(header or cf.HEADER) + "\r\n" + "".join(",".join('"%s"' % c.replace('"', '""') for c in r) + "\r\n" for r in rows)
    return cf.parse_upload(base64.b64encode(text.encode("utf-8")).decode())

GOOD = [["fldA1", "Color Code", "Color Code", "TEXT", "TEXT", "A colour", "true"], ["fldA1", "Color Flag", "Color Flag", "TEXT", "CHCK", "", "false"],
        ["fldB2", "Budget Amount", "Budget Amount", "CURC", "TEXT", "Money", ""], ["fldC3", "Start Day", "", "DATE", "TEXT", "", "no"], ["fldD4", "Unit Count", "Unit Count", "NMBR", "TEXT", "", "TRUE"]]

# ---- reading and validating the upload
rows, problems = upload(GOOD)
check("a good file loads: five rows in file order, several fields may share a field_id, isRequired is read as a bool", not problems and [r["name"] for r in rows] == [g[1] for g in GOOD]
      and [r["field_id"] for r in rows][:2] == ["fldA1", "fldA1"] and [r["isRequired"] for r in rows] == [True, False, False, False, True] and all(r["checked"] and r["parameterId"] == "" for r in rows), (rows, problems))
def probs(rowlist, header=None):
    r, p = upload(rowlist, header); return r, p
r, p = probs(GOOD + [["fldE5", "color code", "x", "TEXT", "TEXT", "", "false"]])
check("a duplicate name is refused ignoring letter case, names the row it repeats, and the whole file is refused (no rows)", r == [] and len(p) == 1 and p[0]["column"] == "name" and "duplicate of row 2" in p[0]["message"], p)
r, p = probs([["fldA1", "A", "", "text", "TEXT", "", "true"], ["fldA2", "B", "", "TEXT", "SLCT", "", "true"], ["fldA3", "C", "", "DTTM", "CHCK", "", "false"]])
check("dataType and displayType are case-sensitive and limited to the owner's lists", [(x["row"], x["column"]) for x in p] == [(2, "dataType"), (3, "displayType"), (4, "dataType")], p)
r, p = probs([["fldA1", "Bad#Name", "", "TEXT", "TEXT", "", "x"], ["fldA2", "Fine Name", "", "TEXT", "TEXT", "", "maybe"], ["fldA3", " lead", "", "TEXT", "TEXT", "", ""], ["", "No Id", "", "TEXT", "TEXT", "", ""], ["fldA5", "   ", "", "TEXT", "TEXT", "", ""]])
check("restricted characters, edge spaces, a bad isRequired, a missing field_id and a blank name are each reported with their row", sorted((x["row"], x["column"]) for x in p)
      == [(2, "isRequired"), (2, "name"), (3, "isRequired"), (4, "name"), (5, "field_id"), (6, "name")] and "#" in [x for x in p if x["row"] == 2 and x["column"] == "name"][0]["message"], p)
check("every character Workfront rejects in a name is refused", all(upload([["f1", "A" + ch + "B", "", "TEXT", "TEXT", "", ""]])[0] == [] for ch in "'\"`~!@#$%^&*()[]{};:<>,./?\t"), None)
check("letters, digits, spaces, hyphens and underscores are fine in a name", upload([["f1", "Cost-Center_2 Code", "", "TEXT", "TEXT", "", ""]])[0] != [])
r, p = upload(GOOD, header=["field_id", "name", "label", "datatype", "displayType", "description", "isRequired"])
check("a misspelled or mis-cased header is refused in plain words", r == [] and p[0]["row"] == 0 and "datatype" in p[0]["message"] and "dataType" in p[0]["message"], p)
check("an empty file, a header with no rows and a non-base64 upload are refused", cf.parse_upload(b"")[1] and "no rows" in cf.parse_upload((",".join(cf.HEADER) + "\r\n").encode())[1][0]["message"] and cf.parse_upload("%%%not base64")[1])
check("the template is the header only and loads as 'no rows'", cf.template_csv().strip() == ",".join(cf.HEADER))
bom = cf.parse_upload(("﻿" + ",".join(cf.HEADER) + "\r\n" + ",".join(GOOD[0]) + "\r\n").encode("utf-8"))
check("an Excel UTF-8 file with a byte order mark loads", bom[1] == [] and len(bom[0]) == 1, bom[1])

# ---- the scan
fake = FakeCore()
fake.params["X1"] = {"ID": "X1", "name": "color flag", "lastUpdateDate": "2026-01-02 03:04:05"}      # an existing field: same name, different letter case
http, log = client(fake)
rows, _ = upload(GOOD)
s = cf.scan(rows, http, log)
check("the scan makes one GET per row and finds the existing field ignoring letter case", s["complete"] and s["found"] == 1 and s["scanned"] == 5 and [x[0] for x in fake.reqs] == ["GET"] * 5
      and all(x[1] == "/param/search" and x[2]["name_Mod"] == "eq" for x in fake.reqs), fake.reqs)
r2 = rows[1]
check("a found field gets parameterId and lastUpdateDate, is unchecked and marked exists; the others stay ticked and empty", (r2["parameterId"], r2["lastUpdateDate"], r2["checked"], r2["status"]) == ("X1", "2026-01-02 03:04:05", False, "exists")
      and all(r["checked"] and not r["parameterId"] for i, r in enumerate(rows) if i != 1), rows)
check("the key travels in the apiKey header only, never in the path or query", all(x[4].get("apikey") == KEY and KEY not in x[1] and KEY not in json.dumps(x[2]) for x in fake.reqs))
fake.reqs.clear(); del fake.params["X1"]
s = cf.scan(rows, http, log)
check("a rescan after the field is gone clears the row and ticks it again", rows[1]["parameterId"] == "" and rows[1]["checked"] and rows[1]["status"] == "" and s["found"] == 0, rows[1])
f2 = FakeCore(); h2, l2 = client(f2); rows2, _ = upload(GOOD)
n = {"i": 0}
orig = f2.open
def stop_after_two(req, timeout=None):
    n["i"] += 1
    if n["i"] == 3: raise urllib.error.HTTPError(req.full_url, 403, "x", None, io.BytesIO(b'{"error":"no access"}'))
    return orig(req, timeout)
f2.open = stop_after_two
s = cf.scan(rows2, h2, l2)
check("a scan that hits an error stops there, is NOT complete and says which row", not s["complete"] and s["error"]["row"] == 3 and s["scanned"] == 2 and rows2[2]["status"] == "scan failed" and rows2[3]["status"] == "", s)

# ---- create
fake = FakeCore(); http, log = client(fake); mem = {}
cw = cf.Crosswalk(None, mem)
rows, _ = upload(GOOD); cf.scan(rows, http, log); fake.reqs.clear()
fake.refuse["Budget Amount"] = (400, {"error": {"class": "DuplicateNameException", "message": "DUPLICATE_NAME_XYZ for key " + KEY}})
c = cf.create(rows, "sandbox1", http, cw, log)
posts = [x for x in fake.reqs if x[0] == "POST"]
check("create goes top to bottom and stops at the first error: two created, the third failed, the last two never sent", c["created"] == 2 and c["error"]["row"] == 3 and len(posts) == 3
      and [r["status"] for r in rows] == ["created", "created", "failed", "", ""] and not any(x[3]["name"] in ("Start Day", "Unit Count") for x in posts), (c, [r["status"] for r in rows]))
check("the body follows the owner's example: name, label, dataType, displayType, description, isRequired (a bool); a blank description is left out",
      posts[0][3] == {"name": "Color Code", "label": "Color Code", "dataType": "TEXT", "displayType": "TEXT", "description": "A colour", "isRequired": True}
      and posts[1][3] == {"name": "Color Flag", "label": "Color Flag", "dataType": "TEXT", "displayType": "CHCK", "isRequired": False}, [x[3] for x in posts])
check("a created row gets its parameterId and lastUpdateDate, is unticked and 'created'; the failed row keeps Workfront's reason without the key", rows[0]["parameterId"] == "P0001" and rows[0]["lastUpdateDate"] and not rows[0]["checked"]
      and "DUPLICATE_NAME_XYZ" in rows[2]["message"] and KEY not in rows[2]["message"] and rows[2]["checked"], rows[2])
check("one crosswalk row per success, holding what was sent and the new parameterId, in the environment", [(e["airtable_field_id"], e["workfront_name"], e["parameter_id"], e["workfront_is_required"], e["workfront_display_type"]) for e in cw.entries("sandbox1")]
      == [("fldA1", "Color Code", "P0001", 1, "TEXT"), ("fldA1", "Color Flag", "P0002", 0, "CHCK")] and cw.entries("production") == [])
check("the failed call's WHOLE answer is kept for the report, with the key removed", any("DUPLICATE_NAME_XYZ" in (e.get("response_text") or "") and KEY not in (e.get("response_text") or "") for e in log.entries), [e.get("response_text") for e in log.entries][-1:])
fake.refuse.clear(); fake.no_date = True; fake.reqs.clear()
c = cf.create(rows, "sandbox1", http, cw, log)
check("a second press continues with the rest only (nothing is created twice); a reply with no lastUpdateDate is completed by one read", c["created"] == 3 and c["error"] is None and [x[3]["name"] for x in fake.reqs if x[0] == "POST"] == ["Budget Amount", "Start Day", "Unit Count"]
      and all(r["lastUpdateDate"] for r in rows) and len([x for x in fake.reqs if x[0] == "GET"]) == 3 and len(cw.entries("sandbox1")) == 5, (c, fake.reqs))
f3 = FakeCore(); h3, l3 = client(f3); r3, _ = upload(GOOD); cw3 = cf.Crosswalk(None, {}); k = {"n": 0}
def after_first(): k["n"] += 1; return k["n"] > 1
c = cf.create(r3, "sandbox1", h3, cw3, l3, after_first)
check("Cancel ends the run after the call in progress", c["cancelled"] and c["created"] == 1 and len(f3.params) == 1, c)
f4 = FakeCore(); h4, l4 = client(f4)
r4, _ = upload(GOOD); r4[0]["checked"] = False
c = cf.create(r4, "sandbox1", h4, cf.Crosswalk(None, {}), l4)
check("an unticked row is never created", "Color Code" not in [p["name"] for p in f4.params.values()] and c["created"] == 4, c)

# ---- reset
fake = FakeCore(); http, log = client(fake); cw = cf.Crosswalk(None, {})
fake.params["X9"] = {"ID": "X9", "name": "Start Day", "lastUpdateDate": "2026-01-01 00:00:00"}       # pre-existing: not made by this tool
rows, _ = upload(GOOD); cf.scan(rows, http, log); cf.create(rows, "sandbox1", http, cw, log)
fake.reqs.clear()
fake.refuse_delete = {"P0003"}
out = cf.reset(rows, "sandbox1", http, cw, log)
dels = [x for x in fake.reqs if x[0] == "DELETE"]
check("reset deletes from the top and stops at the first error: two deleted, the third refused, the fourth never tried", out["deleted"] == 2 and out["error"]["row"] == 3 and [x[1] for x in dels] == ["/param/P0001", "/param/P0002", "/param/P0003"], (out, dels))
check("after each delete its crosswalk row is gone and the grid row is cleared; the refused one stays recorded; the key is not in the message", [e["parameter_id"] for e in cw.entries("sandbox1")] == ["P0003", "P0004"]
      and rows[0]["parameterId"] == "" and rows[0]["status"] == "deleted" and rows[2]["parameterId"] == "P0003" and rows[2]["status"] == "delete failed" and KEY not in rows[2]["message"], rows[2])
fake.refuse_delete.clear(); fake.reqs.clear()
out = cf.reset(rows, "sandbox1", http, cw, log)
check("the pre-existing field (found by the scan, not made here) is never deleted, and the crosswalk ends empty", out["deleted"] == 2 and out["skipped_not_created_here"] == 3 and "X9" in fake.params and "P0003" not in fake.params
      and "X9" not in [x[1].rsplit("/", 1)[1] for x in fake.reqs if x[0] == "DELETE"] and cw.entries("sandbox1") == [], (out, list(fake.params)))
other = cf.Crosswalk(None, {"production": [{"airtable_field_id": "fldA1", "workfront_name": "Color Code", "parameter_id": "PZ"}]})
fake.reqs.clear(); out = cf.reset(upload(GOOD)[0], "sandbox1", http, other, log)
check("a crosswalk row of another environment is not touched by a reset in this one", out["deleted"] == 0 and not [x for x in fake.reqs if x[0] == "DELETE"] and len(other.entries("production")) == 1)

# ---- the crosswalk CSV
info = {"fldA1": ("appBase1", "Sample Shop", "tblT1", "Orders", "Colour", "singleLineText")}
rows, _ = upload(GOOD[:2]); rows[0].update(parameterId="P1", lastUpdateDate="2026-11-22 10:00:00", status="created")
extra = [{"airtable_field_id": "fldZ9", "workfront_name": "Old One", "workfront_label": "", "workfront_data_type": "NMBR", "workfront_display_type": "TEXT", "workfront_description": "", "workfront_is_required": 0, "parameter_id": "P9", "last_update_date": "x"}]
text = cf.crosswalk_csv(rows, "sandbox1", extra, info).decode("utf-8")
import csv as _csv
lines = list(_csv.reader(io.StringIO(text)))
check("the crosswalk CSV has the owner's sixteen columns first, in order", lines[0][:16] == ["baseId", "baseName", "tableId", "tableName", "fieldId", "fieldName", "airtable_dataType", "environment", "workfront_name", "workfront_label",
      "workfront_dataType", "workfront_displayType", "workfront_description", "workfront_isRequired", "parameterId", "lastUpdateDate"], lines[0])
check("a row carries the Airtable facts from the schema, the environment and the new IDs; an unknown field leaves them blank; other crosswalk rows follow", lines[1][:9] == ["appBase1", "Sample Shop", "tblT1", "Orders", "fldA1", "Colour", "singleLineText", "sandbox1", "Color Code"] and lines[1][13:] == ["true", "P1", "2026-11-22 10:00:00", "created"]
      and lines[2][:8] == ["appBase1", "Sample Shop", "tblT1", "Orders", "fldA1", "Colour", "singleLineText", "sandbox1"] and lines[2][16] == "not created"
      and lines[3][:9] == ["", "", "", "", "fldZ9", "", "", "sandbox1", "Old One"] and lines[3][16] == "in crosswalk, not in this list", lines)

# ---- the report
job = {"op": "create", "env": "sandbox1", "actor": "J Tester", "reason": "", "started_at": "2026-11-22 10:00:00", "finished_at": "2026-11-22 10:00:05", "status": "stopped on error", "error": "Stopped at row 3", "summary": {"created": 2, "planned": 5,
       "error": {"row": 3, "name": "Budget Amount", "message": "HTTP 400"}}, "rows": [dict(r) for r in rows], "entries": [], "notes": ["n"]}
html = rep.build_html(job, "")
check("a report with no API call still says so (a report exists whatever the number of operations)", "No call was made to Workfront" in html and "J Tester" in html and "Where it stopped" in html)
fake = FakeCore(); http, log = client(fake); fake.refuse["B"] = (400, {"error": {"message": "DUPLICATE_NAME_XYZ " + KEY}}); r5, _ = upload([["f1", "A", "", "TEXT", "TEXT", "", ""], ["f2", "B", "", "TEXT", "TEXT", "", ""]])
cf.create(r5, "sandbox1", http, cf.Crosswalk(None, {}), log)
job.update(entries=log.entries, rows=[dict(r) for r in r5])
html = rep.build_html(job, "", lambda t: lc.redact(t, {KEY}))
check("the report lists every attempt with its request, what was sent and the whole refusal; no key and no header appears", "POST /param" in html and "DUPLICATE_NAME_XYZ" in html and KEY not in html and "apikey" not in html.lower() and "&quot;isRequired&quot;: false" in html, html[-900:])
mh = rep.write_report(job, os.path.join("tmp_cf_report"), lambda h, t: h.encode("utf-8"), "")
check("the report file is named by operation, environment and time", re.match(r"^CoreFields_create_sandbox1_20261122-100000\.mhtml$", mh) is not None, mh)
import shutil; shutil.rmtree("tmp_cf_report", ignore_errors=True)

# ---- draft migration 026
c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
check("a database from the library reports migration 026 as pending", 26 in [i["migration"] for i in dm.pending(c)] and "Core field crosswalk" in dm.describe(dm.pending(c)))
sql026 = open(os.path.join(ROOT, "sql", "migration_026_add_core_field_crosswalk.sql"), encoding="utf-8").read()
c.executescript(sql026); c.executescript(sql026)
check("migration 026 can be run twice and then is no longer pending", 26 not in [i["migration"] for i in dm.pending(c)] and cf.table_exists(c))
dbcw = cf.Crosswalk(c, {})
rows, _ = upload(GOOD[:2])
dbcw.add("sandbox1", rows[0], "P1", "d1"); dbcw.add("production", rows[0], "P7", "d2")
check("the database crosswalk keeps a row per environment; find and remove work", dbcw.persistent and dbcw.find("sandbox1", "fldA1", "Color Code")["parameter_id"] == "P1" and dbcw.find("production", "fldA1", "Color Code")["parameter_id"] == "P7"
      and (dbcw.remove("sandbox1", "P1") or True) and dbcw.entries("sandbox1") == [] and len(dbcw.entries("production")) == 1)
err = None
try: dbcw.add("production", rows[0], "P8", "d3")
except sqlite3.IntegrityError as e: err = e
check("the same field cannot be recorded twice for one environment", err is not None)
bad = None
try: c.execute("INSERT INTO core_field_crosswalk(environment, airtable_field_id, workfront_name, workfront_data_type, workfront_display_type, parameter_id) VALUES ('dev','f','n','TEXT','TEXT','p')")
except sqlite3.IntegrityError as e: bad = e
check("an environment outside the four Core environments is refused by the table", bad is not None)
c.close()
print("ALL PASSED" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
