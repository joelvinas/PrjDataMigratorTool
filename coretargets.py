#!/usr/bin/env python3
"""Where each table's Workfront Core objects go: the OBJECT TYPE a table becomes (project, request or task) and its PARENT (the parent's object code and ID).
Standard library only. Tables: table_core_objects (migration 011), table_core_parents and table_core_events (migration 020).

Rules kept here:
  * A person decides, with who, when and why. Nothing is decided or guessed by the tool. Every change, and every clear, is appended to table_core_events.
  * The parent is per table, in the database, never in config.json: a base can have dozens of parent projects.
  * Core object codes: project = PROJ, request = OPTASK (a request is an OPTASK, normally a child of a Request Queue project), task = TASK.
  * What a parent may be (checked here, not by the database, so the rules can change without a migration):
        project  -> a program (PRGM) or a portfolio (PORT), or none (decided: no parent)
        request  -> the Request Queue project that holds it (PROJ)
        task     -> a project (PROJ) or another task (TASK)
  * A parent ID is plain text (any system's IDs fit). The 32-hexadecimal-character check for Workfront is the page's job; here an ID only has to be
    non-empty, free of spaces and control characters, and at most 100 characters.
  * Only tables that are in scope and land in Planning plus Core can be decided here.
  * Codes, IDs, names and reasons only: no record value.
"""
OBJECT_CODE = {"project": "PROJ", "request": "OPTASK", "task": "TASK"}
PARENT_CODES = {"project": ("PRGM", "PORT"), "request": ("PROJ",), "task": ("PROJ", "TASK")}
PARENT_OPTIONAL = {"project": True, "request": False, "task": False}
PARENT_LABEL = {"PRGM": "program", "PORT": "portfolio", "PROJ": "project", "TASK": "task"}


class TargetError(Exception):
    def __init__(self, message, status=400):
        super(TargetError, self).__init__(message)
        self.message, self.status = message, status


def available(conn):
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {"table_core_objects", "table_core_parents", "table_core_events"} <= have


def _need(conn):
    if not available(conn):
        raise TargetError("Core object decisions need migrations 011 and 020 on this database.", 409)


def _actor(actor):
    actor = (actor or "").strip()
    if not actor or len(actor) > 40 or any(ord(c) < 32 for c in actor):
        raise TargetError("Enter your initials or a short name (up to 40 characters) before making a decision.")
    return actor


def _reason(reason):
    reason = (reason or "").strip() or "set from the page"
    if len(reason) > 500 or any(ord(c) < 32 and c not in "\n\t" for c in reason):
        raise TargetError("The reason must be plain text of up to 500 characters.")
    return reason


def valid_id(value):
    return isinstance(value, str) and 0 < len(value) <= 100 and not any(c.isspace() or ord(c) < 32 for c in value)


def check_parent(object_type, code, parent_id):
    """None when (object type, parent code, parent ID) is allowed, else a plain message. code and ID both None means 'no parent'."""
    if object_type not in OBJECT_CODE:
        return "Decide the object type (project, request or task) first."
    if code is None and parent_id is None:
        return None if PARENT_OPTIONAL[object_type] else "A %s needs a parent (%s)." % (object_type, " or ".join(PARENT_LABEL[c] for c in PARENT_CODES[object_type]))
    if code is None or parent_id is None:
        return "Give both the parent's object code and its ID, or neither."
    if code not in PARENT_CODES[object_type]:
        return "A %s (%s) goes under %s, not %s." % (object_type, OBJECT_CODE[object_type], " or ".join("a %s (%s)" % (PARENT_LABEL[c], c) for c in PARENT_CODES[object_type]),
                                                      PARENT_LABEL.get(code, repr(code)))
    if not valid_id(parent_id):
        return "The parent ID must be plain text with no spaces, up to 100 characters."
    return None


def _table(conn, base_id, table_id):
    """The table's row, if it is in this base, in scope, and lands in Planning plus Core."""
    r = conn.execute("SELECT t.table_id, t.table_name FROM airtable_tables t WHERE t.base_id=? AND t.table_id=?", (base_id, table_id)).fetchone()
    if r is None:
        raise TargetError("That table is not in this base.", 404)
    scope = conn.execute("SELECT decision FROM table_scope WHERE base_id=? AND table_id=?", (base_id, table_id)).fetchone()
    target = conn.execute("SELECT target FROM table_targets WHERE base_id=? AND table_id=?", (base_id, table_id)).fetchone()
    if (scope and scope[0] == "out") or not target or target[0] != "planning_and_core":
        raise TargetError("Only a table that is in scope and lands in Planning plus Core has Core objects.")
    return r


def _event(conn, base_id, table_id, subject, kind, old, new, actor, reason):
    conn.execute("INSERT INTO table_core_events(table_id, base_id, subject, event_type, old_value, new_value, actor, reason) VALUES (?,?,?,?,?,?,?,?)",
                 (table_id, base_id, subject, kind, old, new, actor, reason))


def _parent_text(row):
    return None if row is None or row["parent_obj_code"] is None else "%s:%s" % (row["parent_obj_code"], row["parent_obj_id"])


# ------------------------------------------------------------------ decisions
def set_object_type(conn, base_id, table_id, object_type, actor, reason=""):
    """Decide what the table becomes. 'clear' removes the decision (and says so in the history). Returns decided, changed, unchanged or cleared."""
    _need(conn)
    actor, reason = _actor(actor), _reason(reason)
    _table(conn, base_id, table_id)
    old = conn.execute("SELECT core_object_type FROM table_core_objects WHERE table_id=?", (table_id,)).fetchone()
    old = old[0] if old else None
    if object_type == "clear":
        if old is None:
            return "unchanged"
        with conn:
            conn.execute("DELETE FROM table_core_objects WHERE table_id=?", (table_id,))
            _event(conn, base_id, table_id, "object_type", "clear", old, None, actor, reason)
        return "cleared"
    if object_type not in OBJECT_CODE:
        raise TargetError("The object type must be one of: %s." % ", ".join(sorted(OBJECT_CODE)))
    if old == object_type:
        return "unchanged"
    with conn:
        conn.execute("INSERT INTO table_core_objects(table_id, base_id, core_object_type, decided_by, reason) VALUES (?,?,?,?,?) "
                     "ON CONFLICT(table_id) DO UPDATE SET core_object_type=excluded.core_object_type, decided_by=excluded.decided_by, decided_at=CURRENT_TIMESTAMP, reason=excluded.reason",
                     (table_id, base_id, object_type, actor, reason))
        _event(conn, base_id, table_id, "object_type", "change" if old else "decide", old, object_type, actor, reason)
    return "changed" if old else "decided"


def set_parent(conn, base_id, table_id, code, parent_id, actor, reason="", clear=False):
    """Decide where the table's objects go. code and parent_id both None = decided 'no parent' (a project only). clear=True removes the decision."""
    _need(conn)
    actor, reason = _actor(actor), _reason(reason)
    _table(conn, base_id, table_id)
    old = conn.execute("SELECT * FROM table_core_parents WHERE table_id=?", (table_id,)).fetchone()
    if clear:
        if old is None:
            return "unchanged"
        with conn:
            conn.execute("DELETE FROM table_core_parents WHERE table_id=?", (table_id,))
            _event(conn, base_id, table_id, "parent", "clear", _parent_text(old) or "none", None, actor, reason)
        return "cleared"
    typ = conn.execute("SELECT core_object_type FROM table_core_objects WHERE table_id=?", (table_id,)).fetchone()
    problem = check_parent(typ[0] if typ else None, code, parent_id)
    if problem:
        raise TargetError(problem)
    if old is not None and old["parent_obj_code"] == code and old["parent_obj_id"] == parent_id:
        return "unchanged"
    with conn:
        conn.execute("INSERT INTO table_core_parents(table_id, base_id, parent_obj_code, parent_obj_id, decided_by, reason) VALUES (?,?,?,?,?,?) "
                     "ON CONFLICT(table_id) DO UPDATE SET parent_obj_code=excluded.parent_obj_code, parent_obj_id=excluded.parent_obj_id, decided_by=excluded.decided_by, "
                     "decided_at=CURRENT_TIMESTAMP, reason=excluded.reason", (table_id, base_id, code, parent_id, actor, reason))
        _event(conn, base_id, table_id, "parent", "change" if old else "decide", (_parent_text(old) or "none") if old else None, ("%s:%s" % (code, parent_id)) if code else "none", actor, reason)
    return "changed" if old else "decided"


# ------------------------------------------------------------------ reading
def rows(conn, base_id):
    """One row per in-scope table that lands in Planning plus Core, sorted by table name then ID: its object type decision, its parent decision and whether
    the pair is complete and allowed. A table with no decision is simply undecided."""
    _need(conn)
    out = []
    for t in conn.execute(
            "SELECT t.table_id, t.table_name, o.core_object_type, o.decided_by AS type_by, o.decided_at AS type_at, o.reason AS type_reason, "
            "p.table_id AS has_parent, p.parent_obj_code, p.parent_obj_id, p.decided_by AS parent_by, p.decided_at AS parent_at, p.reason AS parent_reason "
            "FROM airtable_tables t JOIN table_targets g ON g.table_id=t.table_id AND g.base_id=t.base_id AND g.target='planning_and_core' "
            "LEFT JOIN table_scope s ON s.table_id=t.table_id AND s.base_id=t.base_id LEFT JOIN table_core_objects o ON o.table_id=t.table_id "
            "LEFT JOIN table_core_parents p ON p.table_id=t.table_id WHERE t.base_id=? AND COALESCE(s.decision, 'in')!='out' ORDER BY lower(t.table_name), t.table_id", (base_id,)):
        r = dict(t)
        decided_parent = r.pop("has_parent") is not None
        r["object_code"] = OBJECT_CODE.get(r["core_object_type"])
        r["parent_decided"] = decided_parent
        problem = None
        if r["core_object_type"] is None:
            problem = "The object type is not decided."
        elif not decided_parent:
            problem = "The parent is not decided."
        else:
            problem = check_parent(r["core_object_type"], r["parent_obj_code"], r["parent_obj_id"])
        r["problem"] = problem
        r["ready"] = problem is None
        out.append(r)
    return out


def target_of(conn, base_id, table_id):
    """What the loader needs for one table: {object_type, object_code, parent_code, parent_id}, or raises TargetError saying what is missing."""
    for r in rows(conn, base_id):
        if r["table_id"] == table_id:
            if not r["ready"]:
                raise TargetError("%s: %s Decide it on the Stage 4 page first." % (r["table_name"], r["problem"]), 409)
            return {"object_type": r["core_object_type"], "object_code": r["object_code"], "parent_code": r["parent_obj_code"], "parent_id": r["parent_obj_id"]}
    raise TargetError("That table is not an in-scope Planning plus Core table of this base.", 404)


def history(conn, base_id, table_id=None, limit=200):
    """The decision history, newest first: what changed, who, when and why."""
    _need(conn)
    q, a = "SELECT event_id, table_id, subject, event_type, old_value, new_value, actor, reason, created_at FROM table_core_events WHERE base_id=?", [base_id]
    if table_id:
        q += " AND table_id=?"
        a.append(table_id)
    return [dict(r) for r in conn.execute(q + " ORDER BY event_id DESC LIMIT ?", a + [int(limit)])]
