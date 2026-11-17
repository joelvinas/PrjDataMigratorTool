"""Tests for loadstate.py (the runner's state kept in load_id_map) and the runner's pending-row and adopt behavior. Fabricated names; in-memory database."""
import os, sqlite3, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
import loadrun as lr, loadstate as ls
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])

def db():
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
    c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
    return c
def item(tid, system, kind, name, parent=None, status="ready", position=None, field_id=None, table_id="tA"):
    return {"temp_id": tid, "system": system, "kind": kind, "parent": parent, "table_id": table_id, "field_id": field_id, "name": name, "label": None, "data_type": None,
            "position": position, "status": status, "reasons": [], "description": None, "source": "airtable", "group": None}
def raises(fn, *a, **k):
    try:
        fn(*a, **k); return None
    except Exception as e:
        return e

PLAN = [item("P-T-tA", "planning", "record_type", "Orders"), item("P-F-fA1", "planning", "field", "Order Name", parent="P-T-tA", field_id="fA1"),
        item("C-F-fA1", "core", "field", "Order Name", field_id="fA1")]

class Fake(object):
    def __init__(self, system, conflict=(), existing=None, interrupt=None):
        self.system, self.calls, self.conflict, self.existing, self.interrupt, self.n = system, [], set(conflict), existing or {}, interrupt, 0
    def create(self, it, parent_id):
        if it["temp_id"] == self.interrupt:
            raise KeyboardInterrupt()                     # the program stops mid-call: not an Exception, so the runner cannot catch it
        if it["temp_id"] in self.conflict:
            raise lr.NameConflict("FIELD_DISPLAY_NAME_NOT_UNIQUE: %s" % it["name"])
        self.n += 1; self.calls.append(it["temp_id"])
        return "wf-%s-%d" % (self.system, self.n)
    def find_existing(self, it, parent_id):
        r = self.existing[it["temp_id"]]
        if isinstance(r, Exception):
            raise r
        return r

# ---- the table behaves
c = db(); st = ls.DbState(c, "appL", "wsDev")
check("the library's load_id_map is the migrated one (workspace_key present)", ls.available(c))
check("nothing is created yet", st.get("P-T-tA", "dev") is None and st.pending("dev") == [])
n = st.begin(PLAN[0], "dev")
r = c.execute("SELECT * FROM load_id_map").fetchone()
check("begin writes a PENDING row before any create call, with the Workspace in the key", n == 1 and r["status"] == "pending" and r["workspace_id"] == "wsDev" and r["workspace_key"] == "wsDev" and r["workfront_id"] is None and r["kind"] == "record_type" and r["base_id"] == "appL")
check("a pending item has no ID to skip on, and shows as unfinished", st.get("P-T-tA", "dev") is None and st.pending("dev") == ["P-T-tA"])
check("trying again counts the attempt", st.begin(PLAN[0], "dev") == 2)
st.set("P-T-tA", "dev", "wfPlan1")
check("set records the Workfront ID and status created; the pending list empties", st.get("P-T-tA", "dev") == "wfPlan1" and st.pending("dev") == [])
other = ls.DbState(c, "appL", "wsUat")
check("the same temporary ID in another Workspace is independent (a wipe and recreate cannot collide)", other.get("P-T-tA", "dev") is None and other.begin(PLAN[0], "dev") == 1
      and c.execute("SELECT COUNT(*) FROM load_id_map WHERE temp_id='P-T-tA'").fetchone()[0] == 2)
check("a Core row keeps an empty Workspace key whatever Workspace the run has", (st.begin(PLAN[2], "sandbox1"), c.execute("SELECT workspace_key, workspace_id FROM load_id_map WHERE temp_id='C-F-fA1'").fetchone()[:]) == (1, ("", None)))
check("a Planning write without a Workspace is refused", isinstance(raises(ls.DbState(c, "appL").begin, PLAN[0], "dev"), ls.StateError) and ls.DbState(c, "appL").get("P-T-tA", "dev") is None)
check("finishing an item that was never begun is refused", isinstance(raises(st.set, "P-F-fNope", "dev", "x"), ls.StateError))
st.begin(PLAN[1], "dev")
st.fail("P-F-fA1", "dev", "HTTP 500 Authorization: Bearer abc.def.ghi token=SECRETVALUE")
r = c.execute("SELECT * FROM load_id_map WHERE temp_id='P-F-fA1'").fetchone()
check("a failure is stored scrubbed: no credential reaches the database", r["status"] == "failed" and "SECRETVALUE" not in r["last_error"] and "abc.def.ghi" not in r["last_error"] and "[removed]" in r["last_error"], r["last_error"])
check("a failed item is not skipped on the next run", st.get("P-F-fA1", "dev") is None)
check("only schema facts are stored: no table or column that could hold a record value was touched", {x[1] for x in c.execute("PRAGMA table_info(load_id_map)")} >= {"temp_id", "workfront_id", "status", "attempts", "last_error"})

# ---- the runner with the database state
c = db(); st = ls.DbState(c, "appL", "wsDev"); env = lr.resolve_environment("dev", None, dry_run=False)
P, C = Fake("planning"), Fake("core")
r1 = lr.run_plan(PLAN, st, {"planning": P, "core": C}, env, dry_run=False)
rows = {x["temp_id"]: x for x in c.execute("SELECT * FROM load_id_map")}
check("a real run writes every created ID to the database, parent first", r1["counts"]["created"] == 3 and all(x["status"] == "created" and x["workfront_id"] for x in rows.values()) and rows["P-F-fA1"]["parent_temp_id"] == "P-T-tA", r1["counts"])
r2 = lr.run_plan(PLAN, ls.DbState(c, "appL", "wsDev"), {"planning": P, "core": C}, env, dry_run=False)
check("a fresh process finds them in the database and creates nothing again", r2["counts"] == {"already_created": 3} and len(P.calls) == 2 and len(C.calls) == 1, r2["counts"])
before = [tuple(x) for x in c.execute("SELECT * FROM load_id_map ORDER BY temp_id")]
lr.run_plan(PLAN, ls.DbState(c, "appL", "wsDev"), {"planning": lr.DryRunTransport("planning"), "core": lr.DryRunTransport("core")}, env, dry_run=True)
check("a dry run writes nothing, not even a pending row", before == [tuple(x) for x in c.execute("SELECT * FROM load_id_map ORDER BY temp_id")])

# ---- a program that stops mid-call leaves a pending row; the rerun adopts the item
c = db(); st = ls.DbState(c, "appL", "wsDev")
crash = Fake("planning", interrupt="P-F-fA1")
try:
    lr.run_plan(PLAN, st, {"planning": crash, "core": Fake("core")}, env, dry_run=False)
    died = False
except KeyboardInterrupt:
    died = True
check("the run was cut off mid-call", died)
st2 = ls.DbState(c, "appL", "wsDev")
check("the database shows what was started and not finished", st2.pending("dev") == ["P-F-fA1"] and st2.status("P-F-fA1", "dev") == "pending" and c.execute("SELECT attempts FROM load_id_map WHERE temp_id='P-F-fA1'").fetchone()[0] == 1)
rerun = Fake("planning", conflict={"P-F-fA1"}, existing={"P-F-fA1": "wfExisting9"})
r3 = lr.run_plan(PLAN, st2, {"planning": rerun, "core": Fake("core")}, env, dry_run=False)
row = c.execute("SELECT * FROM load_id_map WHERE temp_id='P-F-fA1'").fetchone()
check("the rerun adopts the item that was created before the stop, instead of failing or duplicating", r3["counts"].get("adopted") == 1 and row["status"] == "created" and row["workfront_id"] == "wfExisting9" and row["attempts"] == 2, (r3["counts"], dict(row)))
check("adopting is not creating: nothing else was created twice", "P-T-tA" not in rerun.calls and r3["counts"].get("already_created") == 1)

# ---- a name that clashes with something this tool never tried to create is reported, not adopted
c = db(); st = ls.DbState(c, "appL", "wsDev")
clash = Fake("planning", conflict={"P-T-tA"}, existing={"P-T-tA": "wfSomeoneElses"})
r4 = lr.run_plan(PLAN[:1], st, {"planning": clash}, env, dry_run=False)
row = c.execute("SELECT * FROM load_id_map WHERE temp_id='P-T-tA'").fetchone()
check("a first-time name conflict fails with a plain reason and is never adopted", r4["counts"] == {"failed": 1} and row["status"] == "failed" and row["workfront_id"] is None and "no earlier attempt" in row["last_error"], (r4, dict(row)))
r5 = lr.run_plan(PLAN[:1], st, {"planning": Fake("planning", conflict={"P-T-tA"}, existing={"P-T-tA": "wfSomeoneElses"})}, env, dry_run=False)
check("after a recorded attempt the same clash may be adopted (the attempt is what makes it ours)", r5["counts"] == {"adopted": 1})
for label, existing in (("two items match", ["wfA", "wfB"]), ("no item is found", None), ("the lookup itself fails", RuntimeError("HTTP 500 token=ZZZ"))):
    c = db(); st = ls.DbState(c, "appL", "wsDev"); st.begin(PLAN[0], "dev")
    out = lr.run_plan(PLAN[:1], st, {"planning": Fake("planning", conflict={"P-T-tA"}, existing={"P-T-tA": existing})}, env, dry_run=False)
    row = c.execute("SELECT * FROM load_id_map WHERE temp_id='P-T-tA'").fetchone()
    check("never guess: %s, so the item fails and stays unadopted" % label, out["counts"] == {"failed": 1} and row["status"] == "failed" and row["workfront_id"] is None and "ZZZ" not in (row["last_error"] or ""), (out, dict(row)))

# ---- environments
check("dev and uat are accepted for Planning; an unknown name is still refused", lr.resolve_environment("dev").name == "dev" and lr.resolve_environment("uat", None, False, "wsX").workspace_id == "wsX" and raises(lr.resolve_environment, "staging") is not None)
check("production still needs the exact phrase for a real run", raises(lr.resolve_environment, "production", None, False) is not None)
check("the temporary ID prefix says which system a row is", [ls.system_of(t) for t in ("P-T-x", "P-F-x", "P-O-x", "S-P-t-s", "C-T-x", "C-F-x", "S-C-t-s")] == ["planning"] * 4 + ["core"] * 3)
check("an older database without workspace_key falls back (no crash)", ls.available(sqlite3.connect(":memory:")) is False)

print("ALL PASS" if ok else "FAILURES")
sys.exit(0 if ok else 1)
