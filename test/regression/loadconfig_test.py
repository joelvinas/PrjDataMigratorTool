"""Tests of loadconfig.py (Workfront credentials from config.json). Fabricated values only."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))
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
check("Planning needs no environment and carries its Workspace ID", plan is not None and plan.workspace_id == "ws-dev-0001" and plan.environment == "dev")
check("a bearer token is sent as an Authorization header", plan.authorization_header() == ("Authorization", "Bearer " + PLAN_TOK))
check("an api key travels in its own header", core.authorization_header() == ("apiKey", CORE_KEY))
check("a placeholder Core production block is not configured", lc.credentials(data, "core", "production") is None)
check("a plain http address is refused", lc.credentials({"workfront": {"core": {"sandbox1": {"base_url": "http://x.example.com", "api_key": CORE_KEY}}}}, "core", "sandbox1") is None)
check("a placeholder Workspace ID counts as not set", lc.credentials({"workfront": {"planning": {"base_url": "https://a.example.com", "bearer_token": PLAN_TOK, "workspace_id": "PASTE_ID"}}}, "planning").workspace_id is None)
check("status is yes or no only", lc.status(data) == {"core": {"sandbox1": True, "sandbox2": False, "preview": False, "production": False}, "planning": True, "planning_workspace": True, "planning_stages": {"dev": True, "uat": True}})
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
# ---- stages defined by the user: one block per stage, any capitalisation, each with its own address, token and Workspace
ST = {"workfront": {"planning": {"DEV": {"base_url": "https://a.example.com", "bearer_token": PLAN_TOK, "workspace_id": "wsD"}, "uat": {"base_url": "https://b.example.com", "bearer_token": "otherTOKEN0123456789", "workspace_id": "wsU"},
      "Production": {"base_url": "https://c.example.com", "bearer_token": "prodTOKEN0123456789", "workspace_id": "PASTE_PROD_WORKSPACE_ID_HERE"}, "qa": {"base_url": "https://d.example.com", "bearer_token": "x" * 20, "workspace_id": "wsQ"}}}}
check("the stages are the ones defined in config.json, in the fixed order, in lower case; a name that is not a known stage is ignored", list(lc.planning_blocks(ST)) == ["dev", "uat", "production"])
check("each stage has its own address, token and Workspace ID", lc.credentials(ST, "planning", "dev").workspace_id == "wsD" and lc.credentials(ST, "planning", "UAT").base_url == "https://b.example.com" and lc.credentials(ST, "planning", "uat").secret() == "otherTOKEN0123456789")
check("a stage that is not defined has no credentials", lc.credentials(ST, "planning", "qa") is None and lc.credentials({"workfront": {"planning": {"DEV": ST["workfront"]["planning"]["DEV"]}}}, "planning", "uat") is None)
check("a placeholder Workspace ID counts as not set for that stage only", lc.credentials(ST, "planning", "production").workspace_id is None and lc.status(ST)["planning_stages"] == {"dev": True, "uat": True, "production": False})
check("every stage's token is a secret to redact", {PLAN_TOK, "otherTOKEN0123456789", "prodTOKEN0123456789"} <= lc.secret_values(ST))
# ---- dev, test and prod: test is stored as uat and prod as production (the names the database allows)
one = lambda ws, tok: {"base_url": "https://a.example.com", "bearer_token": tok, "workspace_id": ws}
TP = {"workfront": {"planning": {"dev": one("wsD", "devTOKEN0123456789"), "Test": one("wsT", "testTOKEN0123456789"), "prod": one("wsP", "prodTOKEN0123456789")}}}
check("test and prod are the uat and production stages", list(lc.planning_blocks(TP)) == ["dev", "uat", "production"] and lc.planning_problems(TP) == [])
check("each is found by either name", lc.credentials(TP, "planning", "test").workspace_id == "wsT" and lc.credentials(TP, "planning", "uat").workspace_id == "wsT"
      and lc.credentials(TP, "planning", "PROD").workspace_id == "wsP" and lc.credentials(TP, "planning", "production").environment == "production")
check("the picker shows the name config.json uses", lc.planning_labels(TP) == {"dev": "dev", "uat": "test", "production": "prod"})
check("planning_stage maps the other names and leaves the rest alone", [lc.planning_stage(x) for x in ("Test", " PROD ", "dev", "uat", "qa", None)] == ["uat", "production", "dev", "uat", "qa", ""])
AMB = {"workfront": {"planning": {"dev": one("wsD", "devTOKEN0123456789"), "uat": one("wsU", "uatTOKEN0123456789"), "test": one("wsT", "testTOKEN0123456789")}}}
check("a stage named twice (uat and test) is left out, never guessed", list(lc.planning_blocks(AMB)) == ["dev"] and lc.credentials(AMB, "planning", "uat") is None and lc.credentials(AMB, "planning", "test") is None)
check("and the problem says which names clash, with no value from the blocks", len(lc.planning_problems(AMB)) == 1 and "uat" in lc.planning_problems(AMB)[0] and "test" in lc.planning_problems(AMB)[0]
      and "TOKEN" not in lc.planning_problems(AMB)[0] and "wsU" not in lc.planning_problems(AMB)[0], lc.planning_problems(AMB))
check("the tokens of a stage left out are still secrets to redact", {"uatTOKEN0123456789", "testTOKEN0123456789"} <= lc.secret_values(AMB))
import tableload
cf = os.path.join(d, "tp.json"); json.dump(TP, open(cf, "w"))
check("a run named prod uses the production block and is stored as production", tableload.planning_context(cf, "prod", False).stage == "production" and tableload.planning_context(cf, "prod", False).ws == "wsP")
try:
    tableload.planning_context(cf, "prod", True); m = None
except tableload.LoadError as e:
    m = e.message
check("a real send to prod still needs the production phrase", m is not None and "CREATE IN PRODUCTION" in m, m)
ca = os.path.join(d, "amb.json"); json.dump(AMB, open(ca, "w"))
try:
    tableload.planning_context(ca, "test", False); m = None
except tableload.LoadError as e:
    m = e.message
check("a run in a stage named twice is refused with the reason", m is not None and "twice" in m and "TOKEN" not in m, m)
sys.exit(0 if ok else 1)
