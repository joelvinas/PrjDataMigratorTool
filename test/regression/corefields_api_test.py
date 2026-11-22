"""HTTP test of the Core bulk field endpoints (/api/corefields/...) against the running app and a FAKE Workfront Core served from this script. Fabricated fields; a fake key.
Covers the upload refusals, the scan and its pop-up confirmation, the locks (no create before a scan, none after an environment change, none in production without the phrase), a create
that stops at the first error, the crosswalk CSV, a reset that deletes every listed field with a parameterId and clears the crosswalk, the three reports (always written), the file-name guard, and that
the key never appears in a response, a report, the database or the server's log."""
import base64, json, os, sqlite3, sys, time, urllib.error, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
B = "http://127.0.0.1:8765"
ok = True
seen = []
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:500])
def call(method, p, b=None, raw=False):
    req = urllib.request.Request(B + p, method=method, data=None if b is None else json.dumps(b).encode(), headers={"Content-Type": "application/json"} if method == "POST" else {})
    try:
        with urllib.request.urlopen(req) as r: data, code, heads = r.read(), r.status, dict(r.headers)
    except urllib.error.HTTPError as e: data, code, heads = e.read(), e.code, {}
    seen.append(data.decode("utf-8", "replace"))
    return (code, data, heads) if raw else (code, json.loads(data))
post = lambda p, b=None: call("POST", p, b)

import corefields_fixture as fx
from corefields_fixture import fake, KEY, PORT, DBF, blk
srv = fx.start()
fx.build()
orig = fx.set_config()
post("/api/db/connect", {"path": DBF})

def csv_b64(rows, header="field_id,name,label,dataType,displayType,description,isRequired"):
    return base64.b64encode((header + "\r\n" + "".join(",".join(r) + "\r\n" for r in rows)).encode()).decode()
def poll(secs=30):
    end = time.time() + secs
    while time.time() < end:
        s, r = call("GET", "/api/corefields/state")
        j = r.get("job")
        if j and j["status"] != "running": return r
        time.sleep(0.05)
    return None
def rowsby(st): return {r["name"]: r for r in st["rows"]}
GOOD = [["fldA1", "Color Code", "Color Code", "TEXT", "TEXT", "A colour", "true"], ["fldA1", "Color Flag", "Color Flag", "TEXT", "CHCK", "", "false"],
        ["fldB2", "Budget Amount", "Budget Amount", "CURC", "TEXT", "", ""], ["fldB2", "Unit Count", "", "NMBR", "TEXT", "", "true"]]
try:
    s, r = call("GET", "/api/corefields/setup")
    check("setup lists the four Core environments with only the configured ones usable, and shows no key", s == 200 and [(e["name"], e["configured"]) for e in r["environments"]] == [("sandbox1", True), ("sandbox2", False), ("preview", False), ("production", True)]
          and KEY not in json.dumps(r) and r["db_connected"], (s, r))
    s, r = call("GET", "/api/corefields/template.csv", raw=True)[0:2]
    check("the template downloads (header only)", s == 200 and r.decode().strip() == "field_id,name,label,dataType,displayType,description,isRequired")

    s, r = post("/api/corefields/upload", {"csv_base64": csv_b64([["fldA1", "Dup", "", "text", "TEXT", "", "true"], ["fldA2", "dup", "", "TEXT", "SLCT", "", "true"]]), "filename": "bad.csv"})
    check("a bad file is refused with every problem listed by row and column, and nothing is loaded", s == 400 and len(r["problems"]) == 3 and {(p["row"], p["column"]) for p in r["problems"]} == {(2, "dataType"), (3, "name"), (3, "displayType")}
          and call("GET", "/api/corefields/state")[1]["rows"] == [], (s, r))
    s, r = post("/api/corefields/upload", {"csv_base64": csv_b64([], header="field_id,name,label")})
    check("a wrong header is refused", s == 400 and "header must be exactly" in json.dumps(r["problems"]), r)
    s, r = post("/api/corefields/upload", {"csv_base64": csv_b64(GOOD), "filename": "good.csv"})
    check("a good file fills the grid in file order, all ticked, with empty parameterId and lastUpdateDate; nothing has been sent", s == 200 and [x["name"] for x in r["rows"]] == [g[1] for g in GOOD] and all(x["checked"] and x["parameterId"] == "" and x["lastUpdateDate"] == "" for x in r["rows"])
          and not fake.reqs and r["can_create"] is False and r["scan_complete"] is False, (s, r))

    fake.params["X1"] = {"ID": "X1", "name": "Color Flag", "lastUpdateDate": "2026-01-02 03:04:05", "displayType": "CHCK"}      # exists already: not made by this tool
    s, r = post("/api/corefields/scan", {"environment": "sandbox1"})
    check("a scan without the pop-up confirmation is refused and sends nothing", s == 400 and "Confirm that Workfront may be scanned" in r["error"] and not fake.reqs, (s, r))
    s, r = post("/api/corefields/scan", {"environment": "sandbox2", "scan_ok": True})
    check("a scan of an environment that is not configured is refused and sends nothing", s == 400 and "not configured" in r["error"] and not fake.reqs, (s, r))
    s, r = post("/api/corefields/create", {"environment": "sandbox1", "actor": "J Tester"})
    check("creating before any scan is locked", s == 400 and "Scan Workfront first" in r["error"] and not fake.reqs, (s, r))
    s, r = post("/api/corefields/scan", {"environment": "sandbox1", "scan_ok": True})
    st = poll()
    rb = rowsby(st)
    check("the scan fills parameterId and lastUpdateDate of the existing field and unticks it; the rest stay ticked; creating unlocks", s == 200 and st["job"]["status"] == "done" and st["scan_complete"] and st["can_create"]
          and (rb["Color Flag"]["parameterId"], rb["Color Flag"]["lastUpdateDate"], rb["Color Flag"]["checked"]) == ("X1", "2026-01-02 03:04:05", False) and rb["Color Code"]["checked"] and len([q for q in fake.reqs if q[0] == "GET"]) == 4, (s, st["job"], rb))
    check("a scan report was written and can be downloaded", st["reports"].get("scan", "").endswith(".mhtml") and call("GET", "/api/corefields/report?file=" + st["reports"]["scan"], raw=True)[0] == 200)
    s, r = call("GET", "/api/corefields/report?file=../config.json", raw=True)[0:2]
    check("the report download takes a plain file name only", s == 404)

    s, r = post("/api/corefields/create", {"environment": "sandbox1", "actor": "  "})
    check("creating needs the person's name", s == 400 and "your name" in r["error"].lower(), (s, r))
    s, r = post("/api/corefields/environment", {"environment": "production"})
    check("choosing another environment clears the scan and locks creating again", s == 200 and r["scan_complete"] is False and r["can_create"] is False and all(x["parameterId"] == "" and x["checked"] for x in r["rows"]), r)
    n0 = len(fake.reqs)
    s, r = post("/api/corefields/create", {"environment": "production", "actor": "J Tester"})
    check("creating in production without a scan is refused", s == 400 and len(fake.reqs) == n0, (s, r))
    s, r = post("/api/corefields/scan", {"environment": "production", "scan_ok": True}); poll()
    s, r = post("/api/corefields/create", {"environment": "production", "actor": "J Tester"})
    check("after a production scan, creating there still needs the exact phrase", s == 400 and "CREATE IN PRODUCTION" in r["error"] and not any(q[0] == "POST" for q in fake.reqs), (s, r))
    s, r = post("/api/corefields/environment", {"environment": "sandbox1"})
    s, r = post("/api/corefields/scan", {"environment": "sandbox1", "scan_ok": True}); poll()

    fake.refuse["Budget Amount"] = "DUPLICATE_NAME_XYZ " + KEY
    s, r = post("/api/corefields/create", {"environment": "sandbox1", "actor": "J Tester"})
    st = poll(); rb = rowsby(st)
    check("create runs top to bottom, skips the unticked existing field, and stops at the first error", s == 200 and st["job"]["status"] == "stopped on error" and rb["Color Code"]["status"] == "created" and rb["Color Code"]["parameterId"] == "P0001"
          and rb["Color Flag"]["status"] == "exists" and rb["Budget Amount"]["status"] == "failed" and rb["Unit Count"]["status"] == "" and [q for q in fake.reqs if q[0] == "POST"].__len__() == 2, (st["job"], rb))
    check("the error text is shown with the key removed", "DUPLICATE_NAME_XYZ" in rb["Budget Amount"]["message"] and KEY not in json.dumps(st), rb["Budget Amount"])
    rep = st["reports"]["create"]
    s, body, heads = call("GET", "/api/corefields/report?file=" + rep, raw=True)
    import quopri
    html = quopri.decodestring(body).decode("utf-8", "replace")
    check("the create report exists, lists both calls and shows Workfront's whole refusal; no key", s == 200 and "application/x-mimearchive" in heads.get("Content-Type", "") and "POST /param" in html and "DUPLICATE_NAME_XYZ" in html and "J Tester" in html and KEY not in html
          and html.count("POST /param") >= 2, html[:300])
    fake.refuse.clear()
    s, r = post("/api/corefields/create", {"environment": "sandbox1", "actor": "J Tester"})
    st = poll(); rb = rowsby(st)
    check("pressing it again continues with the rest only, and every created row has its crosswalk entry (saved in core_field_crosswalk)", st["job"]["status"] == "done" and st["job"]["summary"]["created"] == 2 and rb["Unit Count"]["parameterId"] == "P0003"
          and [r["in_crosswalk"] for r in st["rows"]] == [True, False, True, True], (st["job"], rb))
    c = sqlite3.connect(DBF); n = c.execute("select count(*) from core_field_crosswalk where environment='sandbox1'").fetchone()[0]; c.close()
    check("the database holds one crosswalk row per created field (three), and none for the field that already existed", n == 3, n)
    s, body, heads = call("GET", "/api/corefields/crosswalk.csv", raw=True)
    import csv, io
    lines = list(csv.reader(io.StringIO(body.decode("utf-8"))))
    check("the crosswalk CSV lists every field with the Airtable facts from the schema, the environment and the new parameterId", s == 200 and lines[0][:16] == ["baseId", "baseName", "tableId", "tableName", "fieldId", "fieldName", "airtable_dataType", "environment",
          "workfront_name", "workfront_label", "workfront_dataType", "workfront_displayType", "workfront_description", "workfront_isRequired", "parameterId", "lastUpdateDate"]
          and lines[1][:9] == ["appSampleBase001", "Sample Shop", "tblOrd", "Orders", "fldA1", "Colour", "singleLineText", "sandbox1", "Color Code"] and lines[1][14] == "P0001" and lines[2][14] == "X1" and lines[4][14] == "P0003", lines)

    # ---- reset: deletes the listed fields that exist in Workfront (the scan found X1; this tool created the others) and clears their crosswalk rows
    n0 = len(fake.reqs)
    for label, body in (("no confirmation", {"reason": "done testing"}), ("no reason", {"confirm": True, "reason": " "})):
        s, r = post("/api/corefields/reset", dict({"environment": "sandbox1", "actor": "J Tester"}, **body))
        check("a reset with %s is refused and sends nothing" % label, s == 400 and len(fake.reqs) == n0, (s, r))
    st = call("GET", "/api/corefields/state")[1]
    check("the state offers the reset (red button) once a scan is complete and rows have a parameterId", st["can_reset"] is True, st)
    s, r = post("/api/corefields/reset", {"environment": "sandbox1", "actor": "J Tester", "reason": "wrong dataType on two fields", "confirm": True})
    st = poll(); rb = rowsby(st)
    check("reset deletes EVERY listed field that has a parameterId, including the one that already existed, clears the grid and needs a new scan", s == 200 and st["job"]["status"] == "done" and st["job"]["summary"]["deleted"] == 4
          and fake.params == {} and all(x["status"] == "deleted" and x["parameterId"] == "" and x["checked"] for x in st["rows"]) and not st["scan_complete"] and st["can_reset"] is False, (st["job"]["status"], list(fake.params), [(x["status"], x["parameterId"], x["checked"]) for x in st["rows"]], st["scan_complete"], st["can_reset"]))
    c = sqlite3.connect(DBF); n = c.execute("select count(*) from core_field_crosswalk").fetchone()[0]; c.close()
    check("the crosswalk table is empty after the reset", n == 0, n)
    s, body, heads = call("GET", "/api/corefields/report?file=" + st["reports"]["reset"], raw=True)
    html = quopri.decodestring(body).decode("utf-8", "replace")
    check("a separate reset report holds who, why and the DELETE calls", s == 200 and html.count("DELETE /param/") >= 4 and "wrong dataType on two fields" in html and "J Tester" in html and st["reports"]["reset"] != st["reports"]["create"], html[:300])
    s, r = post("/api/corefields/reset", {"environment": "sandbox1", "actor": "J Tester", "reason": "again", "confirm": True})
    check("a second reset without a fresh scan is refused", s == 400 and "Scan Workfront first" in r["error"], (s, r))
    s, r = post("/api/corefields/scan", {"environment": "sandbox1", "scan_ok": True}); st = poll()
    s, r = post("/api/corefields/reset", {"environment": "sandbox1", "actor": "J Tester", "reason": "again", "confirm": True})
    check("after a scan that finds nothing there is nothing to delete, and it is said", s == 400 and "nothing to delete" in r["error"], (s, r))
    s, r = post("/api/corefields/upload", {"csv_base64": csv_b64([["fldA1", "Color Code", "Color Code", "NMBR", "TEXT", "corrected", "true"]]), "filename": "fixed.csv"})
    s, r = post("/api/corefields/environment", {"environment": "sandbox1"})
    s, r = post("/api/corefields/scan", {"environment": "sandbox1", "scan_ok": True}); st = poll()
    s, r = post("/api/corefields/create", {"environment": "sandbox1", "actor": "J Tester"}); st = poll()
    check("the corrected CSV (a new upload scans only its own fields) recreates the field with the new type", st["job"]["status"] == "done" and len(fake.params) == 1 and list(fake.params.values())[0]["dataType"] == "NMBR", list(fake.params.values()))
    s, r = post("/api/corefields/environment", {"environment": "production"}); s, r = post("/api/corefields/scan", {"environment": "production", "scan_ok": True}); poll()
    n0 = len([q for q in fake.reqs if q[0] == "DELETE"])
    s, r = post("/api/corefields/reset", {"environment": "production", "actor": "J Tester", "reason": "x test", "confirm": True})
    check("a reset in production needs its own exact phrase and sends nothing without it", s == 400 and "DELETE FIELDS IN PRODUCTION" in r["error"] and len([q for q in fake.reqs if q[0] == "DELETE"]) == n0, (s, r))

    # ---- a failure before the first call still leaves a report
    cfg2 = json.loads(open("config.json", encoding="utf-8").read()); cfg2["workfront"]["core"]["sandbox1"]["base_url"] = "http://127.0.0.1:9"
    json.dump(cfg2, open("config.json", "w"))
    post("/api/corefields/environment", {"environment": "sandbox1"})
    s, r = post("/api/corefields/scan", {"environment": "sandbox1", "scan_ok": True}); st = poll(60)
    check("a scan that cannot reach Workfront ends as an error, is not complete, and still leaves a report", st["job"]["status"] == "stopped on error" and not st["scan_complete"] and st["reports"].get("scan", "").endswith(".mhtml"), st["job"])
    json.dump(cfg2 | {"workfront": {"core": {"sandbox1": blk(), "production": blk()}}}, open("config.json", "w"))

    folder = os.path.join("out", "corefields")
    text = "\n".join(seen) + "".join(open(os.path.join(folder, f), encoding="utf-8", errors="replace").read() for f in os.listdir(folder))
    check("the key is in no response and no report", KEY not in text)
    s, log = call("GET", "/api/calllog")
    check("the API call log does not hold it either", KEY not in json.dumps(log))
    check("the database does not hold it", KEY.encode() not in open(DBF, "rb").read())
    slog = open("server.log", encoding="utf-8", errors="replace").read() if os.path.exists("server.log") else ""
    check("the server's own log does not hold it", KEY not in slog)
    s, page = call("GET", "/corefields", raw=True)[0:2]
    check("the page and its script are served, and the navigation lists the page", s == 200 and b"CHECK FIELDS" in page and b"CREATE NEW FIELDS" in page and call("GET", "/corefields.js", raw=True)[0] == 200
          and '"/corefields"' in open(os.path.join("web", "js", "common.js"), encoding="utf-8").read())
finally:
    open("config.json", "w", encoding="utf-8").write(orig)
    srv.shutdown()
sys.exit(0 if ok else 1)
