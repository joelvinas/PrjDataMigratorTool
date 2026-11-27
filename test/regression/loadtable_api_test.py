"""HTTP test of the Load a table endpoints (/api/loadtable/...) against the running app and a FAKE Workfront served from this script. Fabricated tables and records; a fake
bearer token. Covers each endpoint, step gating, the confirm rules, a failed step, a cancel, a rerun after a stop, and that the token and the record values never appear in a
response, the database, the call log or the server's own log."""
import base64, json, os, sqlite3, sys, threading, time, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
B = "http://127.0.0.1:8765"
ok = True
seen = []                       # every response body, to search at the end
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:500])
def call(method, p, b=None):
    req = urllib.request.Request(B + p, method=method, data=None if b is None else json.dumps(b).encode(), headers={"Content-Type": "application/json"} if method == "POST" else {})
    try:
        with urllib.request.urlopen(req) as r: raw, code = r.read(), r.status
    except urllib.error.HTTPError as e: raw, code = e.read(), e.code
    seen.append(raw.decode("utf-8", "replace"))
    return code, json.loads(raw)
post = lambda p, b=None: call("POST", p, b)

import loadtable_fixture as fx
from loadtable_fixture import fake, BASE, TOKEN, DBF
srv = fx.start()
fx.build()
post("/api/db/connect", {"path": DBF})
ex = os.path.join("data_extract", BASE)

def poll(want=("done", "cancelled", "failed"), secs=30, until=None):
    end = time.time() + secs
    while time.time() < end:
        s, r = call("GET", "/api/loadtable/status")
        j = r.get("job")
        if j and (j["status"] in want or (until and until(j))): return j
        time.sleep(0.05)
    return None
def conf(t, stage="dev", ws="wsDev"): return {"table_id": t, "stage": stage, "workspace_id": ws}
def start(step, t, send=False, stage="dev", **k):
    body = {"base_id": BASE, "table_id": t, "stage": stage, "step": step, "send": send}
    body.update(k)
    return post("/api/loadtable/start", body)
def run(step, t, send=False, **k):
    if send: k.setdefault("confirm", conf(t))
    s, r = start(step, t, send, **k)
    return s, r, (poll() if s == 200 else None)
def state(t, stage="dev"): return post("/api/loadtable/check", {"base_id": BASE, "table_id": t, "stage": stage})[1]

# ------------------------------------------------------------------ nothing configured yet
s, r = call("GET", "/api/loadtable/setup?base_id=" + BASE)
check("with no Workfront settings the page is told so plainly, and the picker still lists the in-scope tables", s == 200 and r["configured"] is False and "No Planning stage is configured" in (r["problem"] or "") and [t["table_id"] for t in r["tables"]] == ["tB", "tC", "tA", "tE"], r)
s, r = post("/api/loadtable/check", {"base_id": BASE, "table_id": "tC", "stage": "dev"})
check("a check without settings is refused with the reason, and no job exists", s == 400 and "not configured" in r["error"] and call("GET", "/api/loadtable/status")[1] == {"job": None}, r)

orig = fx.set_config()
try:
    s, r = call("GET", "/api/loadtable/setup?base_id=" + BASE)
    check("setup shows the stages the user defined in config.json (lower case) with their Workspace IDs and the typed phrases, and says configured; no token", s == 200 and r["configured"] and r["stages"] == ["dev", "uat", "production"]
          and r["stage_info"] == {"dev": {"workspace_id": "wsDev", "label": "dev"}, "uat": {"workspace_id": "wsMissing", "label": "uat"}, "production": {"workspace_id": "wsProd", "label": "production"}}
          and r["stage_problems"] == [] and r["phrases"]["production"] == "DELETE RECORDS IN PRODUCTION"
          and r["production_phrase"] == "CREATE IN PRODUCTION" and TOKEN not in json.dumps(r), r)
    check("the picker rows carry scope, target and name approval, in-scope tables only (Hidden is absent)", all(t["scope"] == "in" and t["target"] == "planning_only" and t["name_approved"] for t in r["tables"]) and "tD" not in [t["table_id"] for t in r["tables"]])
    s, r = call("GET", "/api/loadtable/setup?base_id=appNoSuchBase1")
    check("a base the database does not know still shows the stages and the settings, with the problem in plain words", s == 200 and r["configured"] and r["stages"] and r["tables"] == [], r)
    s, r = post("/api/loadtable/check", {"base_id": BASE, "table_id": "tC", "stage": "qa"})
    check("a stage that is not defined in config.json is refused and the defined ones are named", s == 400 and "not defined" in r["error"] and "dev, uat, production" in r["error"], r)
    s, w = post("/api/loadtable/workspace", {"stage": "dev"})
    check("the Workspace read proves the token and the Workspace ID and returns the Workspace's name", s == 200 and w["ok"] and w["name"] == "Rehearsal Workspace" and w["workspace_id"] == "wsDev" and TOKEN not in json.dumps(w), w)
    s, w = post("/api/loadtable/workspace", {"stage": "uat"})
    check("a Workspace ID Workfront does not know says to correct workspace_id in config.json", s == 200 and not w["ok"] and w["fix"] == "workspace_id" and "workspace_id" in w["problem"], w)
    fake.reject = True
    s, w = post("/api/loadtable/workspace", {"stage": "dev"})
    fake.reject = False
    check("a refused (expired) token says tokens last 24 hours and to paste a new bearer_token, without echoing it", s == 200 and not w["ok"] and w["fix"] == "token" and "24 hours" in w["problem"] and TOKEN not in json.dumps(w), w)
    check("the Workspace read for an undefined stage is refused", post("/api/loadtable/workspace", {"stage": "qa"})[0] == 400)
    check("a bad table or base is refused", post("/api/loadtable/check", {"base_id": BASE, "table_id": "tZ", "stage": "dev"})[0] == 404 and post("/api/loadtable/check", {"base_id": "x y", "table_id": "tC"})[0] == 400
          and post("/api/loadtable/check", {"base_id": BASE, "table_id": "", "stage": "dev"})[0] == 400)

    # ---- step 1 and the gates
    sE = state("tE")
    check("step 1: a table with a blocked field reports blocked with reasons and every later step closed", sE["check"]["counts"]["blocked"] >= 1 and sE["check"]["reasons"] and not any(sE["gates"].values()), sE["gates"])
    s, r = start("structure", "tE")
    check("step 2 is refused for a blocked table, even as a dry run, and no job starts", s == 409 and "Step 1" in r["error"] and call("GET", "/api/loadtable/status")[1] == {"job": None}, r)
    sC = state("tC")
    check("step 1 for a clear table: counts, no reasons, step 2 open", sC["check"]["counts"] == {"ready": 2, "hold": 0, "blocked": 0} and sC["gates"]["can_structure"] and not sC["gates"]["can_records"] and sC["workspace_id"] == "wsDev", sC)
    for step in ("records", "links", "reset"):
        s, r = start(step, "tC")
        check("%s is refused until the step before is done" % step, s == 409, (s, r))
    check("an unknown step is refused", start("explode", "tC")[0] == 400)

    # ---- step 2: dry run, the confirm rules, the send
    n0 = len(fake.reqs)
    s, r, j = run("structure", "tC")
    check("the structure dry run is a background job that reports what it would create and sends nothing", s == 200 and j and j["status"] == "done" and j["result"]["dry_run"] and j["result"]["counts"] == {"would_create": 2} and len(fake.reqs) == n0, j)
    for label, body in (("no confirmation", {}), ("the wrong Workspace ID", {"confirm": conf("tC", ws="wsOther")}), ("the wrong table", {"confirm": conf("tA")}), ("the wrong stage", {"confirm": conf("tC", stage="uat")}), ("a confirmation that is not an object", {"confirm": "yes"})):
        s, r = start("structure", "tC", True, **body)
        check("a send with %s is refused and sends nothing" % label, s == 400 and "Confirm the table" in r["error"] and len(fake.reqs) == n0, (s, r))
    s, r, j = run("structure", "tC", True)
    check("a confirmed send creates the record type and the field; the progress and result show the table and the counts", j["status"] == "done" and j["result"]["counts"] == {"created": 2} and j["progress"]["table_id"] == "tC" and j["progress"]["done"] == 2
          and j["workspace_id"] == "wsDev" and len(fake.posts("/record-types")) == 1 and len(fake.posts("/fields")) == 1 and fake.bad_auth == 0, j)
    sC = state("tC")
    check("the state comes from the database: step 3 is open now, link and verify are not", sC["steps"]["structure"]["done"] and sC["gates"]["can_records"] and not sC["gates"]["can_links"] and not sC["gates"]["can_verify"] and sC["gates"]["can_reset"], sC["gates"])
    s, mp = post("/api/loadtable/mapping", {"base_id": BASE, "table_id": "tC", "stage": "dev"})
    check("the Workfront ID map lists the record type and the field with their IDs", s == 200 and [(x["kind"], x["new_name"], bool(x["workfront_id"])) for x in mp["rows"]] == [("record type", "Notes", True), ("field", "Note Text", True)] and mp["workspace_id"] == "wsDev", mp)
    s, r, j = run("structure", "tC", True)
    check("a rerun creates nothing twice", j["result"]["counts"] == {"already_created": 2} and len(fake.posts("/record-types")) == 1, j)

    # ---- a failed step
    fake.refuse_fields = {"Order Name"}
    s, r, j = run("structure", "tA", True)
    res = j["result"]
    check("a refused field is reported as failed, with the token removed from its reason; the rest of the table still ran", j["status"] == "done" and res["counts"].get("failed") == 1 and TOKEN not in json.dumps(res)
          and [x for x in res["items"] if x["outcome"] == "failed"][0]["name"] == "Order Name", res["counts"])
    s, r = start("records", "tA")
    check("a failed step keeps step 3 closed", s == 409 and "Step 2" in r["error"], r)
    fake.refuse_fields = set()
    s, r, j = run("structure", "tA", True)
    check("after the cause is fixed, a rerun creates only the missing field", j["result"]["counts"].get("created") == 1 and j["result"]["counts"].get("failed", 0) == 0 and state("tA")["steps"]["structure"]["done"], j["result"]["counts"])
    for t in ("tB",):
        s, r, j = run("structure", t, True)
    check("a table whose link target was created as a dependency finishes its own structure", j["result"]["counts"].get("failed", 0) == 0 and state("tB")["steps"]["structure"]["done"], j["result"]["counts"])

    # ---- step 3: records
    s, r, j = run("records", "tC")
    check("the records dry run gives counts from the saved file and sends nothing", j["result"]["dry_run"] and j["result"]["in_file"] == 2 and j["result"]["would_send"] == 2 and not fake.posts("/records/bulk"), j)
    os.rename(os.path.join(ex, "tC.json"), os.path.join(ex, "tC.off"))
    s, r = start("records", "tC")
    check("a missing saved file is refused before any job starts", s == 409 and "no saved record file" in r["error"].lower(), r)
    os.rename(os.path.join(ex, "tC.off"), os.path.join(ex, "tC.json"))
    s, r, j = run("records", "tC", True)
    check("the records send creates the records; the result has counts and no record value", j["status"] == "done" and j["result"]["created"] == 2 and j["result"]["failed"] == 0 and "SECRETVALUE" not in json.dumps(j), j)
    check("step 4 does not apply to a table with no link fields; verify opens", not state("tC")["gates"]["can_links"] and state("tC")["gates"]["can_verify"] and state("tC")["steps"]["links"]["done"])
    s, v = post("/api/loadtable/verify", {"base_id": BASE, "table_id": "tC", "stage": "dev"})
    check("verify gives the counts side by side and they match, including Workfront's own", s == 200 and v["match"] and [x["count"] for x in v["counts"]] == [2, 2, 2, 2, 2] and v["differences"] == [], v)

    # ---- a cancel, a second job refused while one runs, and a rerun after the stop
    fake.delay = 0.5
    s, r = start("records", "tA", True, confirm=conf("tA"))
    j = poll(until=lambda j: j["progress"].get("batch", 0) >= 1, secs=20)
    s2, r2 = start("structure", "tA", False)
    check("while a step runs, another cannot start", s == 200 and s2 == 409 and "already running" in r2["error"], (s, s2, r2))
    check("progress shows the table, the batch number and the counts", j and j["progress"]["table_id"] == "tA" and j["progress"]["batch"] >= 1 and j["progress"]["batches"] == 3 and j["progress"]["created"] >= 2, j and j["progress"])
    check("cancel is accepted", post("/api/loadtable/cancel", {})[0] == 200)
    j = poll()
    made = len(fake.store.get(state("tA")["steps"]["structure"]["record_type"], []))
    check("a cancelled job stops between batches and says so; some records exist, not all", j["status"] == "cancelled" and j["result"]["cancelled"] and 0 < made < 5, (j["status"], made))
    check("cancel with nothing running is refused", post("/api/loadtable/cancel", {})[0] == 400)
    fake.delay = 0
    s, r, j = run("records", "tA", True)
    check("the rerun after the stop loads only the rest: every record exists exactly once", j["status"] == "done" and made + j["result"]["created"] == 5 and len(fake.store[state("tA")["steps"]["structure"]["record_type"]]) == 5 and state("tA")["steps"]["records"]["complete"], j)

    # ---- step 3 for tB, step 4, verify
    s, r, j = run("records", "tB", True)
    s, r, j = run("links", "tA")
    check("the links dry run counts the records waiting and sends nothing", j["result"]["dry_run"] and j["result"]["pending"] == 5 and not [x for x in fake.reqs if x[0] == "PATCH"], j)
    s, r, j = run("links", "tA", True)
    check("the links pass sets the links; none waits because Customers was loaded first", j["status"] == "done" and j["result"]["updated"] == 5 and j["result"]["waiting"] == 0 and len([x for x in fake.reqs if x[0] == "PATCH"]) == 5, j)
    fake.store[state("tA")["steps"]["structure"]["record_type"]].append("STRAY")
    s, v = post("/api/loadtable/verify", {"base_id": BASE, "table_id": "tA", "stage": "dev"})
    check("verify reports a difference as counts only", s == 200 and not v["match"] and v["differences"][0]["difference"] == 1 and "STRAY" not in json.dumps(v), v)

    # ---- step 6: reset
    s, r = start("reset", "tC", False)
    j = poll()
    check("the reset dry run reads this tool's own counts and sends nothing", s == 200 and j["result"]["dry_run"] and j["result"]["before"]["mapped"] == 2 and j["result"]["wipe"] is None, j)
    n0 = len(fake.reqs)
    s, r = start("reset", "tC", True, confirm=conf("tC"))
    check("a reset send without the typed phrase is refused", s == 400 and "DELETE RECORDS IN DEV" in r["error"] and len(fake.reqs) == n0, r)
    s, r = start("reset", "tC", True, confirm=conf("tC"), phrase="delete records in dev")
    check("a wrong phrase is refused", s == 400 and len(fake.reqs) == n0)
    s, r = start("reset", "tC", True, phrase="DELETE RECORDS IN DEV")
    check("the phrase alone is not enough: the table, stage and Workspace must be confirmed too", s == 400 and len(fake.reqs) == n0, r)
    s, r = start("reset", "tC", True, confirm=conf("tC"), phrase="DELETE RECORDS IN DEV")
    j = poll()
    check("a confirmed reset with the phrase deletes the table's records and clears the local memory", s == 200 and j["status"] == "done" and j["result"]["wipe"]["deleted"] == 2 and j["result"]["cleared"]["mappings"] == 2
          and not fake.store[state("tC")["steps"]["structure"]["record_type"]], j)
    sC = state("tC")
    check("afterwards the table can be loaded again from step 3", sC["gates"]["can_records"] and not sC["steps"]["records"]["complete"] and sC["steps"]["structure"]["done"], sC["steps"])

    # ---- the production stage: its own Workspace, and a typed phrase on top of the confirmation
    n0 = len(fake.reqs)
    s, r, j = run("structure", "tC", stage="production")
    check("a dry run at the production stage needs no phrase and sends nothing", s == 200 and j["result"]["dry_run"] and j["workspace_id"] == "wsProd" and len(fake.reqs) == n0, (s, r))
    for label, body in (("only the confirmation", {"confirm": conf("tC", "production", "wsProd")}), ("a wrong phrase", {"confirm": conf("tC", "production", "wsProd"), "phrase": "create in production"}), ("the dev Workspace", {"confirm": conf("tC", "production", "wsDev"), "phrase": "CREATE IN PRODUCTION"})):
        s, r = start("structure", "tC", True, "production", **body)
        check("a send to production with %s is refused and sends nothing" % label, s == 400 and len(fake.reqs) == n0, (s, r))
    rts = len(fake.posts("/record-types"))
    s, r = start("structure", "tC", True, "production", confirm=conf("tC", "production", "wsProd"), phrase="CREATE IN PRODUCTION")
    j = poll()
    check("with the phrase and the matching confirmation the production stage creates its own record type, separate from dev", s == 200 and j["status"] == "done" and j["result"]["counts"] == {"created": 2} and len(fake.posts("/record-types")) == rts + 1, j)
    s, r = start("reset", "tC", True, "production", confirm=conf("tC", "production", "wsProd"), phrase="DELETE RECORDS IN DEV")
    check("a production reset needs its own phrase", s == 400 and "DELETE RECORDS IN PRODUCTION" in r["error"], r)

    # ---- what must never leave
    text = "\n".join(seen)
    check("the Workfront token is in no response body", TOKEN not in text and fake.bad_auth == 0)
    check("no record value is in any response", "SECRETVALUE" not in text)
    s, log = call("GET", "/api/calllog")
    check("the API call log holds neither the token nor a record value", TOKEN not in json.dumps(log) and "SECRETVALUE" not in json.dumps(log))
    check("the database holds neither", TOKEN.encode() not in open(DBF, "rb").read() and b"SECRETVALUE" not in open(DBF, "rb").read())
    slog = open("server.log", encoding="utf-8", errors="replace").read() if os.path.exists("server.log") else ""
    check("the server's own log holds neither", TOKEN not in slog and "SECRETVALUE" not in slog)
finally:
    open("config.json", "w", encoding="utf-8").write(orig)
    srv.shutdown()
sys.exit(0 if ok else 1)
