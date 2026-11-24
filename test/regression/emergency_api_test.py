"""HTTP test of the Emergency mode endpoints (/api/emergency/...) against the running app and a FAKE Workfront served from this script. Fabricated tables and records; a fake
bearer token. Covers the setup answer, the confirmation rules, a Go run end to end (record type, fields, records, the three report files), the report download and its file
name guard, the dev-only rule, and that the token and the record values never appear in a response, a report, the database or the server's log."""
import json, os, sys, time, urllib.error, urllib.request
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

def poll(secs=30):
    end = time.time() + secs
    while time.time() < end:
        s, r = call("GET", "/api/emergency/status")
        j = r.get("job")
        if j and j["status"] in ("done", "cancelled", "failed"): return j
        time.sleep(0.05)
    return None
def conf(t, ws="wsDev"): return {"table_id": t, "workspace_id": ws}

s, r = call("GET", "/api/emergency/setup?base_id=" + BASE)
check("with no Workfront settings the page is told so plainly", s == 200 and r["configured"] is False and "dev stage is not configured" in r["problem"] and r["stage"] == "dev", r)
s, r = post("/api/emergency/start", {"base_id": BASE, "table_id": "tC", "confirm": conf("tC")})
check("a Go with no settings is refused with the reason and no job exists", s == 400 and call("GET", "/api/emergency/status")[1] == {"job": None}, (s, r))

orig = fx.set_config()
try:
    s, r = call("GET", "/api/emergency/setup?base_id=" + BASE)
    check("setup says configured and shows only the dev Workspace ID (no token), with the in-scope tables", s == 200 and r["configured"] and r["workspace_id"] == "wsDev" and r["stage"] == "dev" and TOKEN not in json.dumps(r)
          and "tD" not in [t["table_id"] for t in r["tables"]] and "tC" in [t["table_id"] for t in r["tables"]], r)
    s, r = call("GET", "/api/emergency/formulas?base_id=" + BASE + "&table_id=tC")
    check("the calculated-field pre-check answers with counts and advice, sends nothing and holds no token", s == 200 and r["table_id"] == "tC" and set(r["counts"]) >= {"total", "ready", "waiting", "blocked"}
          and isinstance(r["advice"], list) and TOKEN not in json.dumps(r) and r["workspace_id"] == "wsDev", (s, r))
    check("the pre-check refuses a missing or unknown table", call("GET", "/api/emergency/formulas?base_id=" + BASE + "&table_id=")[0] == 400 and call("GET", "/api/emergency/formulas?base_id=" + BASE + "&table_id=tNOPE")[0] == 404)
    n0 = len(fake.reqs)
    for label, body in (("no confirmation", {}), ("the wrong table", {"confirm": conf("tA")}), ("the wrong Workspace", {"confirm": conf("tC", "wsProd")})):
        s, r = post("/api/emergency/start", dict({"base_id": BASE, "table_id": "tC"}, **body))
        check("a Go with %s is refused and sends nothing" % label, s == 400 and len(fake.reqs) == n0 and "Confirm" in r["error"], (s, r))
    s, r = post("/api/emergency/start", {"base_id": BASE, "table_id": "tD", "confirm": conf("tD")})
    check("a table that is out of scope is refused", s == 404 and len(fake.reqs) == n0, (s, r))
    s, r = post("/api/emergency/start", {"base_id": BASE, "table_id": "bad id!", "confirm": conf("tC")})
    check("a malformed table ID is refused", s == 400, (s, r))

    s, r = post("/api/emergency/start", {"base_id": BASE, "table_id": "tC", "confirm": conf("tC")})
    j = poll()
    check("a confirmed Go runs to the end in the dev Workspace", s == 200 and j and j["status"] == "done" and j["workspace_id"] == "wsDev", (s, r, j))
    res = j["result"]
    check("the result holds the counts the page shows and the three file names", res["counts"]["clean"] == 1 and res["counts"]["records_loaded"] == 2 and res["counts"]["records_failed"] == 0 and sorted(res["files"]) == ["apilog", "csv", "fields"], res)
    check("the record type and the field were created with the Airtable names, nothing else was sent but the records", len(fake.posts("/record-types")) == 1 and fake.made("Notes") and fake.made("Note Text"), fake.reqs)
    check("the call count and the workspace stay in the result; the calls themselves are only in the reports", res["call_count"] >= 3 and "calls" not in res)
    folder = os.path.join("out", "emergency")
    check("the three files are in out/emergency with the same stem", all(os.path.isfile(os.path.join(folder, f)) for f in res["files"].values()) and len({f.rsplit("_", 1)[0] for f in res["files"].values()}) == 1, res["files"])
    for kind, ctype in (("fields", "application/x-mimearchive"), ("apilog", "application/x-mimearchive"), ("csv", "text/csv")):
        s, data, heads = call("GET", "/api/emergency/report?file=" + res["files"][kind], raw=True)
        check("the %s file downloads by name" % kind, s == 200 and heads.get("Content-Type", "").startswith(ctype) and len(data) > 100, (s, heads))
    for bad in ("../config.json", "..%5Cconfig.json", "config.json", "x.txt", "nosuchfile_fields.csv", ""):
        s, r = call("GET", "/api/emergency/report?file=" + bad)
        check("a report request for %r is refused" % bad, s == 404, (s, r))
    s, r = post("/api/emergency/cancel", {})
    check("cancel with nothing running says so", s == 400 and "No Git-R-Done run" in r["error"], r)

    # a second Go reuses everything
    rts, flds, bulks = len(fake.posts("/record-types")), len(fake.posts("/fields")), len(fake.posts("/records/bulk"))
    s, r = post("/api/emergency/start", {"base_id": BASE, "table_id": "tC", "confirm": conf("tC")})
    j2 = poll()
    check("a second Go creates no second record type or field and sends no record again", j2["status"] == "done" and len(fake.posts("/record-types")) == rts and len(fake.posts("/fields")) == flds
          and len(fake.posts("/records/bulk")) == bulks and j2["result"]["counts"]["records_already_loaded"] == 2, (j2["status"], j2.get("error"), j2["result"]["counts"] if j2["result"] else None))

    # ---- reset the table and try again (not gated by the Load a table steps)
    def rbody(send, **k): return dict({"base_id": BASE, "table_id": "tC", "send": send}, **k)
    n0 = len(fake.reqs)
    s, r = post("/api/emergency/reset", rbody(False))
    check("the reset preview gives this tool's own counts (the record type, AirtableRecordId and the field, the record IDs) and sends nothing", s == 200 and r["dry_run"] and r["before"]["map_total"] == 3 and r["before"]["mapped_records"] == 2 and r["before"]["record_type"]
          and r["attempt"] is None and r["cleared"] is None and r["workspace_id"] == "wsDev" and len(fake.reqs) == n0 and TOKEN not in json.dumps(r), (s, r))
    for label, body in (("no confirmation", {}), ("the wrong table", {"confirm": conf("tA")}), ("the wrong Workspace", {"confirm": conf("tC", "wsProd")})):
        s, r = post("/api/emergency/reset", rbody(True, **body))
        check("a reset with %s is refused and sends nothing" % label, s == 400 and "Confirm" in r["error"] and len(fake.reqs) == n0, (s, r))
    s, r = post("/api/emergency/forget", rbody(True))
    check("forgetting the IDs needs the same confirmation", s == 400 and "Confirm" in r["error"] and len(fake.reqs) == n0, (s, r))
    fake.rt_refuse = 403
    s, r = post("/api/emergency/reset", rbody(True, confirm=conf("tC")))
    check("when Workfront refuses the record type delete the answer says why, with no token, and nothing is forgotten", s == 200 and r["attempt"]["deleted"] is False and r["attempt"]["status"] == 403 and "RECORD_TYPE_IN_HIERARCHY" in r["attempt"]["reason"]
          and r["cleared"] is None and TOKEN not in json.dumps(r) and post("/api/emergency/reset", rbody(False))[1]["before"]["map_total"] == 3, (s, r))
    n1 = len(fake.reqs)
    s, r = post("/api/emergency/forget", rbody(True, confirm=conf("tC")))
    check("forgetting anyway clears the dev map without calling Workfront", s == 200 and r["cleared"]["load_id_map"] == 3 and r["cleared"]["mappings"] == 2 and len(fake.reqs) == n1, (s, r))
    s, r = post("/api/emergency/reset", rbody(False))
    check("with nothing held for the table the reset says so", s == 409 and "nothing to reset" in r["error"], (s, r))
    fake.rt_refuse = None
    rts, bulks = len(fake.posts("/record-types")), len(fake.posts("/records/bulk"))
    s, r = post("/api/emergency/start", {"base_id": BASE, "table_id": "tC", "confirm": conf("tC")})
    j3 = poll()
    check("Go after a reset builds the table again from scratch: a new record type, and every record is sent again", j3["status"] == "done" and len(fake.posts("/record-types")) == rts + 1 and len(fake.posts("/records/bulk")) > bulks
          and j3["result"]["counts"]["records_loaded"] == 2 and j3["result"]["counts"]["records_already_loaded"] == 0, (j3["status"], j3.get("error"), j3["result"]["counts"] if j3["result"] else None))
    s, r = post("/api/emergency/reset", rbody(True, confirm=conf("tC")))
    check("when Workfront deletes the record type the same call forgets the map", s == 200 and r["attempt"] == {"deleted": True, "already_gone": False} and len(fake.rt_deleted) == 1 and r["cleared"]["load_id_map"] == 3 and r["cleared"]["mappings"] == 2, (s, r))
    rts = len(fake.posts("/record-types"))
    s, r = post("/api/emergency/start", {"base_id": BASE, "table_id": "tC", "confirm": conf("tC")})
    j4 = poll()
    check("and Go works again straight after (create, reset, try again)", j4["status"] == "done" and len(fake.posts("/record-types")) == rts + 1 and j4["result"]["counts"]["records_loaded"] == 2, (j4["status"], j4.get("error")))

    text = "\n".join(seen) + "".join(open(os.path.join(folder, f), encoding="utf-8", errors="replace").read() for f in os.listdir(folder))
    check("the Workfront token is in no response and no report", TOKEN not in text and fake.bad_auth == 0)
    check("no record value is in any response or report", "SECRETVALUE" not in text)
    s, log = call("GET", "/api/calllog")
    check("the API call log holds neither the token nor a record value", TOKEN not in json.dumps(log) and "SECRETVALUE" not in json.dumps(log))
    check("the database holds neither", TOKEN.encode() not in open(DBF, "rb").read() and b"SECRETVALUE" not in open(DBF, "rb").read())
    slog = open("server.log", encoding="utf-8", errors="replace").read() if os.path.exists("server.log") else ""
    check("the server's own log holds neither", TOKEN not in slog and "SECRETVALUE" not in slog)
    s, page = call("GET", "/emergency", raw=True)[0:2]
    check("the page is served, and the navigation lists it with the siren", s == 200 and b"Git-R-Done" in page and "\\u{1F6A8}" in open(os.path.join("web", "js", "common.js"), encoding="utf-8").read())
finally:
    open("config.json", "w", encoding="utf-8").write(orig)
    srv.shutdown()
sys.exit(0 if ok else 1)
