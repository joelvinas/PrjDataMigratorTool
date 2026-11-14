"""Tests of loadconfig.py (Workfront credentials from config.json). Fabricated values only."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import loadconfig as lc, loader
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])

CORE_KEY, PLAN_TOK, AT_TOK = "fake-core-key-0001", "fake-planning-token-0002", "fake-airtable-token-0003"
data = {"token": AT_TOK, "workfront": {"core": {"sandbox1": {"base_url": "https://example.sb01.workfront.com/", "api_key": CORE_KEY},
                                                "production": {"base_url": "https://YOURCOMPANY.workfront.com", "api_key": "PASTE_KEY_HERE"}},
                                     "planning": {"base_url": "https://example.workfront.com", "bearer_token": PLAN_TOK, "workspace_id": "ws-dev-0001"}}}
core = lc.credentials(data, "core", "sandbox1")
plan = lc.credentials(data, "planning")
check("a configured Core environment is found, with the trailing slash removed", core is not None and core.base_url == "https://example.sb01.workfront.com" and core.kind == "api_key")
check("Planning needs no environment and carries its Workspace ID", plan is not None and plan.workspace_id == "ws-dev-0001" and plan.environment is None)
check("a bearer token is sent as an Authorization header", plan.authorization_header() == ("Authorization", "Bearer " + PLAN_TOK))
check("an api key travels in its own header", core.authorization_header() == ("apiKey", CORE_KEY))
check("a placeholder Core production block is not configured", lc.credentials(data, "core", "production") is None)
check("a plain http address is refused", lc.credentials({"workfront": {"core": {"sandbox1": {"base_url": "http://x.example.com", "api_key": CORE_KEY}}}}, "core", "sandbox1") is None)
check("a placeholder Workspace ID counts as not set", lc.credentials({"workfront": {"planning": {"base_url": "https://a.example.com", "bearer_token": PLAN_TOK, "workspace_id": "PASTE_ID"}}}, "planning").workspace_id is None)
check("status is yes or no only", lc.status(data) == {"core": {"sandbox1": True, "sandbox2": False, "preview": False, "production": False}, "planning": True, "planning_workspace": True})
check("the secret never appears in repr, str or status", all(v not in repr(core) + str(plan) + json.dumps(lc.status(data)) for v in (CORE_KEY, PLAN_TOK)))
for args in (("core", "qa"), ("core", None), ("nope", "sandbox1")):
    try:
        lc.credentials(data, *args); raised = False
    except lc.ConfigError:
        raised = True
    check("an unknown system or Core environment %r raises a plain error" % (args,), raised)
check("every secret is collected, the Airtable token included", lc.secret_values(data) == {CORE_KEY, PLAN_TOK, AT_TOK})
msg = lc.redact("failed with %s and %s then %s" % (PLAN_TOK, CORE_KEY, AT_TOK), lc.secret_values(data))
check("redact removes every configured secret", msg == "failed with [removed] and [removed] then [removed]", msg)

d = tempfile.mkdtemp(prefix="loadconfig_")
good = os.path.join(d, "config.json"); open(good, "w", encoding="utf-8").write(json.dumps(data))
st = loader.main(["config", "--config", good])
check("loader config reports yes or no", st["core"]["sandbox1"] is True and st["core"]["production"] is False and st["planning"] is True)
bad = os.path.join(d, "bad.json"); open(bad, "w").write("{not json")
for path, why in ((bad, "unreadable"), (os.path.join(d, "none.json"), "missing")):
    try:
        loader.main(["config", "--config", path]); m = None
    except SystemExit as e:
        m = str(e.code)
    check("a %s config is refused with a plain message" % why, m is not None and "config.json" in m and CORE_KEY not in m, m)
ex = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config.example.json")))
check("the example config holds only placeholders, so nothing counts as configured", not (any(lc.status(ex)["core"].values()) or lc.status(ex)["planning"] or lc.status(ex)["planning_workspace"]))
sys.exit(0 if ok else 1)
