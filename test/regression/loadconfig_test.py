"""Tests of loadconfig.py (Workfront credentials from config.json). Fabricated values only."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import loadconfig as lc, loader
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])

CORE_KEY, PLAN_TOK, AT_TOK = "fake-core-key-0001", "fake-planning-token-0002", "fake-airtable-token-0003"
data = {"token": AT_TOK, "workfront": {"sandbox": {"core": {"base_url": "https://example.sb01.workfront.com/", "api_key": CORE_KEY},
                                                 "planning": {"base_url": "https://example.sb01.workfront.com", "bearer_token": PLAN_TOK}},
                                     "production": {"core": {"base_url": "https://YOURCOMPANY.workfront.com", "api_key": "PASTE_KEY_HERE"}}}}
core = lc.credentials(data, "sandbox", "core")
check("a configured system is found, with the trailing slash removed", core is not None and core.base_url == "https://example.sb01.workfront.com" and core.kind == "api_key")
check("a bearer token is sent as an Authorization header", lc.credentials(data, "sandbox", "planning").authorization_header() == ("Authorization", "Bearer " + PLAN_TOK))
check("an api key travels in its own header", core.authorization_header() == ("apiKey", CORE_KEY))
check("a placeholder or missing block is not configured", lc.credentials(data, "production", "core") is None and lc.credentials(data, "production", "planning") is None)
check("a plain http address is refused", lc.credentials({"workfront": {"sandbox": {"core": {"base_url": "http://x.example.com", "api_key": CORE_KEY}}}}, "sandbox", "core") is None)
check("status is yes or no only", lc.status(data) == {"sandbox": {"core": True, "planning": True}, "production": {"core": False, "planning": False}})
check("the secret never appears in repr, str or status", all(v not in repr(core) + str(core) + json.dumps(lc.status(data)) for v in (CORE_KEY, PLAN_TOK)))
try:
    lc.credentials(data, "qa", "core"); raised = False
except lc.ConfigError:
    raised = True
check("an unknown environment raises a plain error", raised)
check("every secret is collected, the Airtable token included", lc.secret_values(data) == {CORE_KEY, PLAN_TOK, AT_TOK})
msg = lc.redact("failed with %s and %s then %s" % (PLAN_TOK, CORE_KEY, AT_TOK), lc.secret_values(data))
check("redact removes every configured secret", msg == "failed with [removed] and [removed] then [removed]", msg)

d = tempfile.mkdtemp(prefix="loadconfig_")
good = os.path.join(d, "config.json"); open(good, "w", encoding="utf-8").write(json.dumps(data))
st = loader.main(["config", "--config", good])
check("loader config reports yes or no", st["sandbox"]["core"] is True and st["production"]["planning"] is False)
bad = os.path.join(d, "bad.json"); open(bad, "w").write("{not json")
for path, why in ((bad, "unreadable"), (os.path.join(d, "none.json"), "missing")):
    try:
        loader.main(["config", "--config", path]); m = None
    except SystemExit as e:
        m = str(e.code)
    check("a %s config is refused with a plain message" % why, m is not None and "config.json" in m and CORE_KEY not in m, m)
ex = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config.example.json")))
check("the example config holds only placeholders, so nothing counts as configured", not any(v for e in lc.status(ex).values() for v in e.values()))
sys.exit(0 if ok else 1)
