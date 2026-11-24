"""HTTP test of the re-key endpoints (/api/rekey/...) against the running app and a FAKE Workfront served from this script. Fabricated tables, a fake bearer token. Covers the setup answer,
the check that only reads, the person's decision (initials, reason, the typed phrase, the Workspace ID, a chosen record type), the re-key itself and its report with who, when and why,
the report download and its file name guard, a second run that changes nothing, and that the token never appears in a response, a report or the database."""
import email, json, os, sqlite3, sys, time, urllib.error, urllib.request
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

import loadtable_fixture as fx
from loadtable_fixture import fake, BASE, TOKEN, DBF
srv = fx.start()
fx.build()
post("/api/db/connect", {"path": DBF})
_c = sqlite3.connect(DBF); _c.execute("DROP TABLE rekey_decisions"); _c.commit(); _c.close()      # an older database, as before migration 029: the page must still work and say so

def poll(path, secs=30):
    end = time.time() + secs
    while time.time() < end:
        s, r = call("GET", path)
        j = r.get("job")
        if j and j["status"] in ("done", "cancelled", "failed"): return j
        time.sleep(0.05)
    return None

orig = fx.set_config()
try:
    s, r = call("GET", "/api/rekey/setup?base_id=" + BASE)
    check("setup shows the dev Workspace ID and the phrase to type, no token, and no record type before anything was loaded", s == 200 and r["configured"] and r["workspace_id"] == "wsDev" and r["phrase"] == "REKEY RECORD TYPES"
          and r["rows"] == [] and TOKEN not in json.dumps(r), r)
    # load one table with Git-R-Done so a record type and a field exist, and tell the database which field is the primary field in Airtable
    s, r = post("/api/emergency/start", {"base_id": BASE, "table_id": "tC", "confirm": {"table_id": "tC", "workspace_id": "wsDev"}})
    j = poll("/api/emergency/status")
    check("a table was loaded first", s == 200 and j and j["status"] == "done", (s, j))
    dbc = sqlite3.connect(DBF); dbc.execute("UPDATE airtable_tables SET primary_field_id='fC1' WHERE table_id='tC'"); dbc.commit(); dbc.close()
    s, r = call("GET", "/api/rekey/setup?base_id=" + BASE)
    row = r["rows"][0] if r["rows"] else {}
    check("the record type of the table is listed with its Airtable primary field and the Planning field it became, not yet checked", s == 200 and len(r["rows"]) == 1 and row["table_id"] == "tC"
          and row["airtable_field_name"] == "Note Text" and row["planning_field_name"] == "Note Text" and row["status"] == "unchecked", r)
    n0 = len(fake.reqs)
    s, r = post("/api/rekey/check", {"base_id": BASE})
    jc = poll("/api/rekey/status")
    check("the check reads Planning only (no PATCH) and finds the text field eligible", s == 200 and jc and jc["status"] == "done" and jc["kind"] == "check" and jc["result"]["rows"][0]["status"] == "eligible"
          and jc["result"]["counts"]["eligible"] == 1 and not [x for x in fake.reqs[n0:] if x[0] in ("PATCH", "POST", "DELETE")] and TOKEN not in json.dumps(jc), (s, jc))
    check("the current primary field is AirtableRecordId and is named", jc["result"]["rows"][0]["current_primary_name"] == "AirtableRecordId", jc["result"]["rows"][0])
    rtid = jc["result"]["rows"][0]["record_type_id"]
    pf = jc["result"]["rows"][0]["planning_field_id"]

    good = {"base_id": BASE, "table_ids": ["tC"], "actor": "JV", "reason": "data validated", "phrase": "REKEY RECORD TYPES", "workspace_id": "wsDev"}
    n1 = len(fake.reqs)
    for label, patch in (("no initials", {"actor": ""}), ("no reason", {"reason": " "}), ("the wrong phrase", {"phrase": "rekey"}), ("the wrong Workspace", {"workspace_id": "wsProd"}), ("no record type chosen", {"table_ids": []}),
                         ("a malformed table ID", {"table_ids": ["bad id!"]})):
        s, r = post("/api/rekey/run", dict(good, **patch))
        check("a re-key with %s is refused and changes nothing" % label, s == 400 and len(fake.reqs) == n1 and fake.primary[rtid] != pf, (s, r))
    check("nothing has started", call("GET", "/api/rekey/status")[1]["job"]["kind"] == "check")

    s, r = post("/api/rekey/run", good)
    jr = poll("/api/rekey/status")
    check("a confirmed re-key finishes and switches the primary field in Planning", s == 200 and jr and jr["status"] == "done" and jr["kind"] == "run" and fake.primary[rtid] == pf
          and jr["result"]["results"][0]["outcome"] == "rekeyed", (s, r, jr))
    patches = [x for x in fake.reqs[n1:] if x[0] == "PATCH"]
    check("only the record type was patched, with its own name and the new primary field", len(patches) == 1 and patches[0][1].split("?")[0].endswith("/record-types/" + rtid) and patches[0][2]["primaryFieldId"] == pf
          and patches[0][2]["displayName"] == "x", patches)
    f = jr["result"]["file"]
    s, data, heads = call("GET", "/api/rekey/report?file=" + f, raw=True)
    msg = email.message_from_bytes(data)
    text = "".join((part.get_payload(decode=True) or b"").decode("utf-8", "replace") for part in msg.walk() if part.get_content_type() == "text/html")
    check("the report downloads and says who, when and why", s == 200 and heads.get("Content-Type", "").startswith("application/x-mimearchive") and "JV" in text and "data validated" in text and "rekeyed" in text, (s, heads))
    check("the report is saved in out/rekey", os.path.isfile(os.path.join("out", "rekey", f)))
    check("before migration 029 is applied the result says the decision is in the report only", "migration 029" in jr["result"]["db_note"] and "report only" in jr["result"]["db_note"] and "migration 029" in text, jr["result"].get("db_note"))
    for bad in ("../config.json", "..%5Cconfig.json", "config.json", "x.txt", "nosuch_report.mhtml", ""):
        s, r = call("GET", "/api/rekey/report?file=" + bad)
        check("a report request for %r is refused" % bad, s == 404, (s, r))
    s, r = post("/api/rekey/cancel", {})
    check("cancel with nothing running says so", s == 400 and "No re-key step" in r["error"], r)

    dbc = sqlite3.connect(DBF); dbc.executescript(open(os.path.join("sql", "migration_029_add_rekey_decisions.sql"), encoding="utf-8").read()); dbc.commit(); dbc.close()
    s, r = post("/api/rekey/run", good)
    jr2 = poll("/api/rekey/status")
    dbc = sqlite3.connect(DBF); dbc.row_factory = sqlite3.Row
    drows = [dict(x) for x in dbc.execute("select * from rekey_decisions")]; dbc.close()
    check("once the table exists the decision is stored with who, when and why", len(drows) == 1 and drows[0]["decided_by"] == "JV" and drows[0]["reason"] == "data validated" and drows[0]["table_id"] == "tC" and drows[0]["outcome"] == "skipped"
          and "saved to rekey_decisions" in jr2["result"]["db_note"] and drows[0]["environment"] == "dev" and drows[0]["workspace_key"] == "wsDev", (drows, jr2["result"].get("db_note")))
    check("a second re-key changes nothing: the record type is already done and is skipped", jr2["status"] == "done" and jr2["result"]["results"][0]["outcome"] == "skipped" and len([x for x in fake.reqs[n1:] if x[0] == "PATCH" and x[1].endswith("/record-types/" + rtid)]) == 1, [r["outcome"] for r in jr2["result"]["results"]])

    s, page = call("GET", "/rekey", raw=True)[0:2]
    check("the page is served on its own address and the navigation lists it", s == 200 and b"Re-key record types" in page and "/rekey" in open(os.path.join("web", "js", "common.js"), encoding="utf-8").read())
    text = "\n".join(seen) + open(os.path.join("out", "rekey", f), encoding="utf-8", errors="replace").read()
    check("the Workfront token is in no response and no report", TOKEN not in text and fake.bad_auth == 0)
    check("the database does not hold the token", TOKEN.encode() not in open(DBF, "rb").read())
finally:
    open("config.json", "w", encoding="utf-8").write(orig)
    srv.shutdown()
sys.exit(0 if ok else 1)
