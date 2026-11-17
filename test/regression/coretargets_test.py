"""Tests for coretargets.py: the object type and parent decision for each Planning-plus-Core table, with its history. Fabricated IDs only; in-memory database."""
import os, sqlite3, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import coretargets as ct
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])
def err(fn, *a, **k):
    try:
        fn(*a, **k); return None
    except ct.TargetError as e:
        return e.message

GUID = "0123456789abcdef0123456789abcdef"
c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row; c.execute("PRAGMA foreign_keys = ON")
c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
for tid, nm, tgt, scope in (("tOrd", "Orders", "planning_and_core", "in"), ("tTsk", "Tasks", "planning_and_core", None), ("tCus", "Customers", "planning_only", "in"), ("tOld", "Old stuff", "planning_and_core", "out"),
                            ("tPrj", "Projects", "planning_and_core", "in")):
    c.execute("INSERT INTO airtable_tables(table_id, base_id, table_name) VALUES (?,?,?)", (tid, "appL", nm))
    c.execute("INSERT INTO table_targets(table_id, base_id, target, decided_by) VALUES (?,?,?,?)", (tid, "appL", tgt, "JV"))
    if scope:
        c.execute("INSERT INTO table_scope(base_id, table_id, decision, decided_by) VALUES (?,?,?,?)", ("appL", tid, scope, "JV"))
c.commit()

check("only in-scope tables that land in Planning plus Core are listed, sorted by name, all undecided", [r["table_name"] for r in ct.rows(c, "appL")] == ["Orders", "Projects", "Tasks"]
      and all(not r["ready"] and r["problem"] == "The object type is not decided." for r in ct.rows(c, "appL")))
check("a table that is planning only, out of scope or unknown cannot be decided", "Planning plus Core" in (err(ct.set_object_type, c, "appL", "tCus", "project", "JV") or "") and "Planning plus Core" in (err(ct.set_object_type, c, "appL", "tOld", "project", "JV") or "")
      and "not in this base" in (err(ct.set_object_type, c, "appL", "tNope", "project", "JV") or ""))
check("who is required, and an unknown object type is refused", "initials" in (err(ct.set_object_type, c, "appL", "tOrd", "project", "") or "") and "must be one of" in (err(ct.set_object_type, c, "appL", "tOrd", "folder", "JV") or ""))
check("a parent cannot be decided before the object type", "Decide the object type" in (err(ct.set_parent, c, "appL", "tOrd", "PROJ", GUID, "JV") or ""))

check("deciding the object type is recorded; the same again changes nothing", ct.set_object_type(c, "appL", "tOrd", "request", "JV", "queue items") == "decided" and ct.set_object_type(c, "appL", "tOrd", "request", "JV") == "unchanged")
check("the table now says the parent is missing", [r["problem"] for r in ct.rows(c, "appL") if r["table_id"] == "tOrd"] == ["The parent is not decided."])
check("a request is an OPTASK and its parent must be a project (the queue)", [r["object_code"] for r in ct.rows(c, "appL") if r["table_id"] == "tOrd"] == ["OPTASK"]
      and "goes under a project (PROJ), not program" in (err(ct.set_parent, c, "appL", "tOrd", "PRGM", GUID, "JV") or "") and "needs a parent" in (err(ct.set_parent, c, "appL", "tOrd", None, None, "JV") or ""))
check("both the parent code and ID are needed, and the ID must be plain text", "both" in (err(ct.set_parent, c, "appL", "tOrd", "PROJ", None, "JV") or "") and "plain text" in (err(ct.set_parent, c, "appL", "tOrd", "PROJ", "has space", "JV") or "")
      and "plain text" in (err(ct.set_parent, c, "appL", "tOrd", "PROJ", "x" * 101, "JV") or ""))
check("a parent ID is plain text of any shape: the 32-hex rule is the page's, not the database's or this module's", ct.set_parent(c, "appL", "tOrd", "PROJ", "OTHER-SYSTEM-id_42", "JV", "queue project") == "decided")
check("the parent can be changed, and the same value again changes nothing", ct.set_parent(c, "appL", "tOrd", "PROJ", GUID, "JV", "the right queue") == "changed" and ct.set_parent(c, "appL", "tOrd", "PROJ", GUID, "JV") == "unchanged")
r = [x for x in ct.rows(c, "appL") if x["table_id"] == "tOrd"][0]
check("the table is ready, with who, when and why for both decisions", r["ready"] and r["parent_obj_code"] == "PROJ" and r["parent_obj_id"] == GUID and r["type_by"] == "JV" and r["parent_by"] == "JV" and r["parent_reason"] == "the right queue" and r["parent_at"])
check("the loader gets the object type, code and parent for a ready table, and a plain message for one that is not", ct.target_of(c, "appL", "tOrd") == {"object_type": "request", "object_code": "OPTASK", "parent_code": "PROJ", "parent_id": GUID}
      and "Decide it on the Stage 4 page" in (err(ct.target_of, c, "appL", "tTsk") or "") and "not an in-scope" in (err(ct.target_of, c, "appL", "tCus") or ""))

# ---- a project may have no parent; a task goes under a project or a task
ct.set_object_type(c, "appL", "tPrj", "project", "JV")
check("a project can have no parent, once a person says so", ct.set_parent(c, "appL", "tPrj", None, None, "JV", "top level") == "decided" and [x for x in ct.rows(c, "appL") if x["table_id"] == "tPrj"][0]["ready"]
      and ct.target_of(c, "appL", "tPrj")["parent_code"] is None)
check("a project's parent may be a program or a portfolio but not a task", ct.set_parent(c, "appL", "tPrj", "PRGM", GUID, "JV") == "changed" and ct.set_parent(c, "appL", "tPrj", "PORT", GUID, "JV") == "changed"
      and "not a task" in (err(ct.set_parent, c, "appL", "tPrj", "TASK", GUID, "JV") or "").replace("not task", "not a task"))
ct.set_object_type(c, "appL", "tTsk", "task", "JV")
check("a task goes under a project or another task, never a program, and it needs a parent", ct.set_parent(c, "appL", "tTsk", "TASK", GUID, "JV") == "decided" and ct.set_parent(c, "appL", "tTsk", "PROJ", GUID, "JV") == "changed"
      and "goes under" in (err(ct.set_parent, c, "appL", "tTsk", "PRGM", GUID, "JV") or "") and "needs a parent" in (err(ct.set_parent, c, "appL", "tTsk", None, None, "JV") or ""))

# ---- changing the object type does not silently drop or keep a parent that no longer fits
ct.set_object_type(c, "appL", "tTsk", "project", "JV", "it is a project after all")
tt = [x for x in ct.rows(c, "appL") if x["table_id"] == "tTsk"][0]
check("a parent that no longer fits the new object type is flagged, not deleted", not tt["ready"] and "goes under" in tt["problem"] and tt["parent_obj_code"] == "PROJ")

# ---- clearing and history
check("clearing a parent and an object type is recorded; clearing nothing changes nothing", ct.set_parent(c, "appL", "tOrd", None, None, "JV", "redo", clear=True) == "cleared" and ct.set_parent(c, "appL", "tOrd", None, None, "JV", clear=True) == "unchanged"
      and ct.set_object_type(c, "appL", "tOrd", "clear", "JV", "redo") == "cleared" and ct.set_object_type(c, "appL", "tOrd", "clear", "JV") == "unchanged")
h = ct.history(c, "appL", "tOrd")
check("the history keeps every decision, newest first, with who, when and why", [(e["subject"], e["event_type"]) for e in h] == [("object_type", "clear"), ("parent", "clear"), ("parent", "change"), ("parent", "decide"), ("object_type", "decide")]
      and h[0]["old_value"] == "request" and h[0]["new_value"] is None and h[1]["old_value"] == "PROJ:" + GUID and h[2]["old_value"] == "PROJ:OTHER-SYSTEM-id_42" and h[2]["new_value"] == "PROJ:" + GUID
      and all(e["actor"] == "JV" and e["created_at"] and e["reason"] for e in h), h)
check("the history of a no-parent decision reads 'none'", [e["new_value"] for e in ct.history(c, "appL", "tPrj") if e["subject"] == "parent"][-1] == "none")
check("a database without migration 020 says what is missing", "migrations 011 and 020" in (err(ct.rows, sqlite3.connect(":memory:"), "appL") or ""))
sys.exit(0 if ok else 1)
