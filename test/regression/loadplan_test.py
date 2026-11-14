"""Tests for loadplan.py (the load plan) and loadrun.py (the runner, environments and dry run). Fabricated names only; no server, database or network."""
import copy, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import loadplan as lp, loadrun as lr
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])

def tr(name):
    return {"candidate": name, "status": "approved", "approved_name": name}
def row(fid, table, name, typ="singleLineText", target_type="Text", planning=True, core=True, **kw):
    r = {"field_id": fid, "table_id": table, "table_name": "T", "original": name, "data_type": typ, "abandoned": False, "consolidated": None, "target_type": target_type,
         "description": {"text": "Source: Airtable. Origins: T.%s." % name}, "targets": {}}
    if planning:
        r["targets"]["planning_name"] = tr(name)
    if core:
        r["targets"]["core_name"] = tr(name)
        r["targets"]["core_label"] = tr(name + " label")
    r.update(kw)
    return r
def tbl(tid, old, scope="in", target="planning_only", approved=True, effective=None):
    return {"table_id": tid, "old": old, "scope": scope, "target": target, "approved": approved, "effective": effective or old}
RV = {"tables": [tbl("tA", "Orders", target="planning_and_core"), tbl("tB", "PO#s", effective="POs"), tbl("tC", "Archive", scope="undecided", target=None, approved=False), tbl("tD", "Hidden", scope="out", target=None)],
      "fields": [row("fA1", "tA", "Order Name"), row("fA2", "tA", "Amount", "number", "Number", core=False), row("fA3", "tA", "Status", "singleSelect", "Single select"),
                 row("fA4", "tA", "Total", "formula", "Number"), row("fA5", "tA", "PO", "multipleRecordLinks", "Link"), row("fA6", "tA", "Files", "multipleAttachments", "Attachment"),
                 row("fA7", "tA", "Old", abandoned=True), row("fA8", "tA", "Merged", consolidated={"survivor_id": "fA1"}), row("fA9", "tA", "No Type", target_type=None),
                 row("fB1", "tB", "PO Number", planning=True, core=False), row("fC1", "tC", "Loose", core=False)]}
CHOICES = {"fA3": [{"id": "selA", "name": "Open"}, {"id": "selB", "name": "Café Closed"}, {"id": "selC", "name": "Done"}]}
plan = lp.build_plan(RV, CHOICES)
I = {i["temp_id"]: i for i in plan["items"]}
ids = [i["temp_id"] for i in plan["items"]]

# ---- identity and order
check("temporary IDs depend only on the Airtable IDs, so they are stable", "P-T-tA" in I and "P-F-fA1" in I and "C-F-fA1" in I and "P-O-fA3-selA" in I and "C-O-fA3-selB" in I)
check("the plan is deterministic", json.dumps(lp.build_plan(copy.deepcopy(RV), CHOICES), sort_keys=True) == json.dumps(plan, sort_keys=True))
check("temporary IDs are unique", len(ids) == len(set(ids)))
rank = [lp.KIND_RANK[i["kind"]] for i in plan["items"]]
check("every parent comes before its children (tables, then fields, then options)", rank == sorted(rank) and all(ids.index(i["parent"]) < ids.index(i["temp_id"]) for i in plan["items"] if i["parent"]))

# ---- tables
check("a table that is in scope, has a target and an approved name is ready; its record type uses the approved name", I["P-T-tB"]["status"] == "ready" and I["P-T-tB"]["name"] == "POs" and I["P-T-tB"]["label"] == "PO#s")
check("a table with no scope or target decision, or an unapproved name, is blocked with every reason", I["P-T-tC"]["status"] == "blocked" and len(I["P-T-tC"]["reasons"]) == 3, I["P-T-tC"]["reasons"])
check("an out-of-scope table has no items and is counted", not any(i["table_id"] == "tD" for i in plan["items"]) and plan["summary"]["out_of_scope_tables"] == 1)
check("a Core form exists only for Planning plus Core, and is on hold until the Core object type is decided", "C-T-tA" in I and "C-T-tB" not in I and I["C-T-tA"]["status"] == "hold" and "object type" in I["C-T-tA"]["reasons"][0])

# ---- fields
check("a plain field with an approved name and a target type is ready, with its lineage description", I["P-F-fA1"]["status"] == "ready" and I["P-F-fA1"]["name"] == "Order Name" and I["P-F-fA1"]["data_type"] == "Text"
      and I["P-F-fA1"]["description"] == "Source: Airtable. Origins: T.Order Name.")
check("the Core field carries the Core name and its own label; it needs no parent but names the form it will join", I["C-F-fA1"]["name"] == "Order Name" and I["C-F-fA1"]["label"] == "Order Name label"
      and I["C-F-fA1"]["parent"] is None and I["C-F-fA1"]["group"] == "C-T-tA" and I["P-F-fA1"]["parent"] == "P-T-tA" and I["P-F-fA1"]["group"] is None)
check("a Planning-only table gets no Core fields", not any(k.startswith("C-F-fB") for k in I))
check("a Core field is blocked when the Core name and label are not both approved", "C-F-fA2" in I and I["C-F-fA2"]["status"] == "blocked" and "Core name and label" in I["C-F-fA2"]["reasons"][0] and I["P-F-fA2"]["status"] == "ready")
check("a field with no target data type is blocked", I["P-F-fA9"]["status"] == "blocked" and any("target data type" in r for r in I["P-F-fA9"]["reasons"]))
check("a derived field is on hold with the reason", I["P-F-fA4"]["status"] == "hold" and "derived field" in I["P-F-fA4"]["reasons"][0])
check("a link and an attachment are on hold with their own reasons", I["P-F-fA5"]["status"] == "hold" and "second pass" in I["P-F-fA5"]["reasons"][0] and I["P-F-fA6"]["status"] == "hold" and "Core documents" in I["P-F-fA6"]["reasons"][0])
check("abandoned and consolidated fields are not in the plan", not any(i["field_id"] in ("fA7", "fA8") for i in plan["items"]))
check("a field of a blocked table is blocked and says the table is not ready", I["P-F-fC1"]["status"] == "blocked" and any("table is not ready" in r for r in I["P-F-fC1"]["reasons"]))

# ---- select options
opts = [i for i in plan["items"] if i["kind"] == "option" and i["parent"] == "P-F-fA3"]
check("select options come in Airtable order with their position", [(o["name"], o["position"]) for o in opts] == [("Open", 0), ("Café Closed", 1), ("Done", 2)])
check("an option follows its field's status, except a name that breaks the label rules goes on hold", [o["status"] for o in opts] == ["ready", "hold", "ready"] and {i["status"] for i in plan["items"] if i["parent"] == "P-F-fA4"} <= {"hold"})
check("an option name that breaks the label rules is held with a proposal, and never changed", any("label rules" in r and "Cafe Closed" in r for r in I["P-O-fA3-selB"]["reasons"]) and I["P-O-fA3-selB"]["name"] == "Café Closed" and I["P-O-fA3-selB"]["status"] == "hold")
check("options exist for Core as well as Planning when the table has both", [o["name"] for o in plan["items"] if o["kind"] == "option" and o["parent"] == "C-F-fA3"] == ["Open", "Café Closed", "Done"])

# ---- the fields the migration adds for the Core-to-Planning link
sysf = {i["temp_id"]: i for i in plan["items"] if i["source"] == "migration" and i["kind"] == "field"}
check("a Planning plus Core table gets the three Planning link fields and the two Core fields", sorted(sysf) == sorted("S-%s-tA-%s" % (s, slug) for s, slug in
      [("P", "core-object-type"), ("P", "core-object-id"), ("P", "core-object-link"), ("C", "planning-record-id"), ("C", "sync-status")]))
check("they use the names and types from the design, and pass the naming rules", [(i["name"], i["data_type"]) for i in (sysf["S-P-tA-core-object-type"], sysf["S-P-tA-core-object-id"], sysf["S-P-tA-core-object-link"])]
      == [("Core object type", "Single select"), ("Core object ID", "Single-line text"), ("Core object link", "Single-line text")] and all(i["status"] == "ready" for i in sysf.values()))
check("the object-type and sync-status fields carry their options in order", [o["name"] for o in plan["items"] if o["parent"] == "S-P-tA-core-object-type"] == ["Project", "Request", "Task"]
      and [o["name"] for o in plan["items"] if o["parent"] == "S-C-tA-sync-status"] == ["Not published", "Published", "Failed"])
check("their descriptions say they are set by the migration", sysf["S-P-tA-core-object-id"]["description"].startswith("Source: Migration. Origins: "))
check("a Planning-only table gets none of them", not any(k.endswith("-tB-core-object-id") for k in sysf))
check("they can be left out", not any(i["source"] == "migration" for i in lp.build_plan(RV, CHOICES, include_link_fields=False)["items"]))

# ---- summary and the gate
sm = plan["summary"]
check("the summary counts items by status and tallies the reasons", sm["total"] == len(plan["items"]) and sum(sm["by_status"].values()) == sm["total"] and sm["reasons"] and "P/" not in json.dumps(sm["by_system_kind_status"]))
closed = lp.build_plan({"tables": RV["tables"], "fields": []})
check("with field review closed (no fields yet) no field from Airtable appears, only record types and the migration's own link fields", {i["source"] for i in closed["items"] if i["kind"] in ("field", "option")} <= {"migration"}
      and {i["kind"] for i in closed["items"]} >= {"record_type", "form"})
check("children_of groups children under their parent in plan order", [c["temp_id"] for c in lp.children_of(plan["items"])["P-F-fA3"]] == ["P-O-fA3-selA", "P-O-fA3-selB", "P-O-fA3-selC"])

# ================= the runner =================
check("the environment defaults to sandbox", lr.resolve_environment().name == "sandbox" and lr.resolve_environment(None, None, dry_run=False).name == "sandbox")
def raises(fn_, *a, **k):
    try:
        fn_(*a, **k)
    except ValueError as e:
        return str(e)
    return None
check("an unknown environment name is refused", raises(lr.resolve_environment, "staging") is not None)
check("production can be DESCRIBED without the phrase, but a real production run needs it exactly", lr.resolve_environment("production", None, dry_run=True).name == "production"
      and "deliberate extra step" in (raises(lr.resolve_environment, "production", None, False) or "") and raises(lr.resolve_environment, "production", "create in production", False) is not None
      and lr.resolve_environment("Production", lr.PRODUCTION_PHRASE, dry_run=False).confirmed)
check("sandbox and production map to the DEV and PROD columns the crosswalk already has", lr.ENV_COLUMN == {"sandbox": "DEV", "production": "PROD"})

class Fake(object):
    """A transport that records its calls and can be told to fail on certain items."""
    def __init__(self, system, fail=()):
        self.system, self.fail, self.calls, self.n = system, set(fail), [], 0
    def describe(self, item, parent_id):
        return {"would": item["temp_id"]}
    def create(self, item, parent_id):
        self.calls.append((item["temp_id"], parent_id))
        if item["temp_id"] in self.fail:
            raise RuntimeError("HTTP 500 Authorization: Bearer abc.def.ghi token=SECRETVALUE")
        self.n += 1
        return "wf-%s-%d" % (self.system, self.n)
ready = [i for i in plan["items"] if i["status"] == "ready"]
sandbox = lr.resolve_environment()
state, log = lr.MemoryState(), []
dry = lr.run_plan(plan["items"], state, {"planning": lr.DryRunTransport("planning"), "core": lr.DryRunTransport("core")}, sandbox, dry_run=True, log=lambda *a: log.append(a))
check("a dry run describes every ready item and changes nothing", dry["counts"]["would_create"] == len(ready) and not state.ids and dry["dry_run"] is True and dry["environment"] == "sandbox")
check("a dry run's children see their parent as 'would be created'", all(r["outcome"] == "would_create" for r in dry["results"] if r["temp_id"] in ("P-F-fA1", "P-O-fA3-selA")))
check("items that are not ready are reported with their reasons, not described", dry["counts"]["not_ready"] == len(plan["items"]) - len(ready)
      and any("derived field" in (r["detail"] or "") for r in dry["results"] if r["temp_id"] == "P-F-fA4"))
check("the log callback sees every item", len(log) == len(plan["items"]))
check("a dry run shows intent only and says so", all("intent only" in r["detail"]["note"] for r in lr.run_plan(ready[:2], lr.MemoryState(), {"planning": lr.DryRunTransport("planning")}, sandbox)["results"] if isinstance(r["detail"], dict)))
check("a real run without a transport for a system is reported, not crashed", lr.run_plan(ready, lr.MemoryState(), {"planning": Fake("planning")}, sandbox, dry_run=False)["counts"].get("no_transport", 0) > 0)

# ---- a real run: parents first, IDs written back, a second run creates nothing
P, C = Fake("planning"), Fake("core")
state = lr.MemoryState()
r1 = lr.run_plan(plan["items"], state, {"planning": P, "core": C}, sandbox, dry_run=False)
check("a real run creates every ready item, parents before children, passing the parent's Workfront ID", r1["counts"]["created"] == len(ready)
      and dict(P.calls)["P-F-fA1"] == state.get("P-T-tA", "sandbox") and dict(P.calls)["P-O-fA3-selA"] == state.get("P-F-fA3", "sandbox"))
check("each Workfront ID is written back against its temporary ID, per environment", state.get("P-F-fA1", "sandbox") is not None and state.get("P-F-fA1", "production") is None)
before = len(P.calls) + len(C.calls)
r2 = lr.run_plan(plan["items"], state, {"planning": P, "core": C}, sandbox, dry_run=False)
check("running again creates nothing: everything is already created (idempotent)", len(P.calls) + len(C.calls) == before and r2["counts"]["already_created"] == len(ready) and "created" not in r2["counts"])
check("the same plan in the other environment is independent", lr.run_plan(plan["items"], state, {"planning": Fake("planning"), "core": Fake("core")}, lr.resolve_environment("production", lr.PRODUCTION_PHRASE, False), dry_run=False)["counts"]["created"] == len(ready))

# ---- failure and restart
P2, C2 = Fake("planning", fail={"P-F-fA3"}), Fake("core")
st2 = lr.MemoryState()
r3 = lr.run_plan(plan["items"], st2, {"planning": P2, "core": C2}, sandbox, dry_run=False)
check("one failure does not stop the run", r3["counts"]["failed"] == 1 and r3["counts"]["parent_missing"] == 2 and r3["counts"]["created"] == len(ready) - 1 - 2, r3["counts"])
failed = [r for r in r3["results"] if r["outcome"] == "failed"][0]
check("a failed item has a scrubbed message: no credential survives", "SECRETVALUE" not in failed["detail"] and "Bearer abc" not in failed["detail"] and "[removed]" in failed["detail"] and st2.status[("P-F-fA3", "sandbox")].startswith("failed"), failed["detail"])
check("the failed item's children are skipped because their parent has no ID", {r["temp_id"]: r["outcome"] for r in r3["results"] if r["temp_id"].startswith("P-O-fA3")} == {"P-O-fA3-selA": "parent_missing", "P-O-fA3-selB": "not_ready", "P-O-fA3-selC": "parent_missing"})
P3 = Fake("planning")
r4 = lr.run_plan(plan["items"], st2, {"planning": P3, "core": C2}, sandbox, dry_run=False)
check("after the problem is fixed, a restart creates only the failed item and what depended on it", sorted(t for t, _ in P3.calls) == sorted(["P-F-fA3", "P-O-fA3-selA", "P-O-fA3-selC"])
      and r4["counts"]["created"] == 3)
check("a restart after the fix does not touch what already existed", all(t.startswith("P-F-fA3") or t.startswith("P-O-fA3") for t, _ in P3.calls))

# ---- production safety at the runner too
prod_unconfirmed = lr.Environment("production", False)
check("the runner itself refuses an unconfirmed production run, but allows a dry run", raises(lr.run_plan, ready, lr.MemoryState(), {}, prod_unconfirmed, False) is not None
      and lr.run_plan(ready[:1], lr.MemoryState(), {"planning": lr.DryRunTransport("planning")}, prod_unconfirmed, dry_run=True)["dry_run"] is True)
check("the runner refuses a bare string instead of an Environment", raises(lr.run_plan, ready, lr.MemoryState(), {}, "sandbox") is not None)
for secret_text, leaked in (("Authorization: Bearer abc123xyz", "abc123xyz"), ("Bearer abc.def.ghi failed", "abc.def.ghi"), ("token=TOK123", "TOK123"), ("password: pw1", "pw1"),
                            ("client_secret=SEC9", "SEC9"), ("api_key=KEY7", "KEY7"), ("x eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4 y", "eyJhbGciOi"),
                            ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz")):
    check("scrub removes a credential: %s" % secret_text.split()[0], leaked not in lr.scrub(secret_text), lr.scrub(secret_text))
check("scrub shortens long messages and leaves ordinary text alone", len(lr.scrub("x" * 1000)) == 300 and lr.scrub("HTTP 500 from the server") == "HTTP 500 from the server")
try:
    lr.DryRunTransport("planning").create({}, None); cannot = False
except RuntimeError:
    cannot = True
check("the dry-run transport cannot create", cannot)

# ---- option names follow the label rules (decision of 14 November 2026)
import fieldnames as fn
S0 = fn.Settings()
check("a clean option name is left as it was", lp.option_check("Open", "ready", S0) == ("ready", []))
check("Adobe's unsupported characters put an option on hold with the proposed repair", lp.option_check("In (Review)", "ready", S0)[0] == "hold" and "'In Review'" in lp.option_check("In (Review)", "ready", S0)[1][0])
check("a blocked option stays blocked", lp.option_check("A:B", "blocked", S0)[0] == "blocked")
check("the emoji choice is honoured in the proposal", "'Done'" in lp.option_check("Done 🎉", "ready", S0)[1][0] and "Party" in lp.option_check("Done 🎉", "ready", lp.label_settings({"emoji_mode": "word"}))[1][0].title())
check("invalid saved settings fall back to the defaults", lp.label_settings({"emoji_mode": "nonsense"}).emoji_mode == "remove")
sys.exit(0 if ok else 1)
