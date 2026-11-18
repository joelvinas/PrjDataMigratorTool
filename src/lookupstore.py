#!/usr/bin/env python3
"""Lookup lists: which select fields a person has authorized to be migrated as a record type of their own (migration 024). Standard library only.

Why: a select field with a very long option list (hundreds of options, such as a list of cities) is not realistic as a select field. A person can authorize it to
become a LOOKUP LIST: a Workfront Planning record type with one record per option, loaded before the table that uses it, and the select field then becomes a
one-sided Planning reference to it. See docs/LOADER_PLAN.md, "Long option lists as their own record type". Several fields may share one list.

This module is the only writer of the DECISIONS. It builds nothing in Workfront and does not touch the load plan yet. Every change is a person's decision: who,
when and why go to field_lookup_decisions (the latest decision per field) and to lookup_list_events (append-only history; a bulk change shares one batch ID).
The tool only PROPOSES (a long option list is flagged over a threshold, a list name is suggested from the field name); nothing is decided automatically.

Only schema metadata is read (the declared options of a field, from airtable_fields.choices_json): never a record value. Option names are metadata, so a few may be
shown to help a person recognise a list. Nothing is random: a batch ID is "L" plus the next event number.
"""
import json

import datatypes_store as dts
import fieldnames as fn
import fieldnames_store as fs
from fieldnames_store import StoreError

TABLES = ("lookup_lists", "field_lookup_decisions", "lookup_list_events")
SELECT_TYPES = ("singleSelect", "multipleSelects")
DEFAULT_THRESHOLD = 50          # options; a starting value a person can change on the page. Not a limit of Workfront.
MAX_THRESHOLD = 100000
MAX_FIELDS_PER_CALL = 500
SAMPLE_NAMES = 5


def option_key(name):
    """How two option names are compared: spacing folded and letter case ignored, the way Planning refuses duplicates (see planningtransport.request_for)."""
    return " ".join(str(name).split()).lower()


def missing(conn):
    """The tables migration 024 would still have to add (an empty list when lookup lists are ready)."""
    return [t for t in TABLES if not fs.has_table(conn, t)]


def _require(conn):
    m = missing(conn)
    if m:
        raise StoreError("Lookup lists need migration 024 applied to this database first. Missing: %s." % ", ".join(m), 409)


def clean_threshold(value):
    if value in (None, ""):
        return DEFAULT_THRESHOLD
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise StoreError("The threshold must be a whole number of options.")
    if n < 1 or n > MAX_THRESHOLD:
        raise StoreError("The threshold must be between 1 and %d options." % MAX_THRESHOLD)
    return n


# ---------------------------------------------------------------- reading
def _choices(conn, base_id):
    """{field_id: [option name, ...]} in Airtable's order, for the select fields of the base. A stored list that cannot be read counts as empty."""
    out = {}
    for r in conn.execute("SELECT field_id, choices_json FROM airtable_fields WHERE base_id=? AND data_type IN (?,?)", (base_id,) + SELECT_TYPES):
        try:
            rows = json.loads(r["choices_json"]) if r["choices_json"] else []
        except ValueError:
            rows = []
        out[r["field_id"]] = [str(c.get("name") if c.get("name") is not None else "") for c in rows if isinstance(c, dict)]
    return out


def _lists(conn, base_id):
    if not fs.has_table(conn, "lookup_lists"):
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM lookup_lists WHERE base_id=? ORDER BY lower(list_name), list_id", (base_id,))]


def _decisions(conn):
    if not fs.has_table(conn, "field_lookup_decisions"):
        return {}
    return {r["airtable_field_id"]: dict(r) for r in conn.execute("SELECT * FROM field_lookup_decisions")}


def _taken_names(conn, base_id, settings, skip_list=None):
    """{lower-case name: what holds it}: the Planning name of every table of the base (the approved name, else the effective one) and every other lookup list."""
    data = fs.load_fields(conn, base_id)
    table_rows, _ = fs._stored(conn, base_id)
    approved = {tid: r["new_table_name"] for tid, r in table_rows.items() if r["status"] == "approved" and r["new_table_name"]}
    taken = {}
    for t in fn.table_names(data["tables"], settings, approved).values():
        taken.setdefault(t["effective"].casefold(), "the table %s" % t["effective"])
    for lst in _lists(conn, base_id):
        if lst["list_id"] != skip_list:
            taken.setdefault(lst["list_name"].casefold(), "the lookup list %s" % lst["list_name"])
    return taken


def review(conn, base_id, threshold=None):
    """Everything the page shows. Works before migration 024 is applied (lists and decisions are then empty and 'ready' is false)."""
    threshold = clean_threshold(threshold)
    settings, _ = fs.read_settings(conn, base_id)
    choices = _choices(conn, base_id)
    decisions, lists = _decisions(conn), _lists(conn, base_id)
    by_list = {lst["list_id"]: lst for lst in lists}
    rows = []
    for f in dts._fields(conn, base_id):
        if f["airtable_type"] not in SELECT_TYPES:
            continue
        names = choices.get(f["field_id"], [])
        keys = [option_key(n) for n in names if option_key(n)]
        dec = decisions.get(f["field_id"])
        lst = by_list.get(dec["list_id"]) if dec else None
        rows.append({"field_id": f["field_id"], "table_id": f["table_id"], "table_name": f["table_name"], "field_name": f["field_name"], "airtable_type": f["airtable_type"],
                     "abandoned": f["abandoned"], "consolidated": f["consolidated"], "option_count": len(names), "blank_count": len(names) - len(keys),
                     "repeat_count": len(keys) - len(set(keys)), "sample": names[:SAMPLE_NAMES], "long": len(names) >= threshold,
                     "proposed_name": fn.clean_table_name(f["field_name"], settings),
                     "list": {"list_id": lst["list_id"], "name": lst["list_name"], "by": dec["decided_by"], "at": dec["decided_at"], "reason": dec["reason"]} if lst else None})
    rows.sort(key=lambda r: (-r["option_count"], r["table_name"].casefold(), r["field_name"].casefold(), r["field_id"]))
    out_lists = []
    for lst in lists:
        mine = [r for r in rows if r["list"] and r["list"]["list_id"] == lst["list_id"]]
        union = {option_key(n) for r in mine for n in choices.get(r["field_id"], []) if option_key(n)}
        out_lists.append({"list_id": lst["list_id"], "name": lst["list_name"], "created_by": lst["created_by"], "created_at": lst["created_at"], "reason": lst["reason"],
                          "field_ids": [r["field_id"] for r in mine], "field_names": ["%s.%s" % (r["table_name"], r["field_name"]) for r in mine],
                          "option_count": len(union)})
    live = [r for r in rows if not r["abandoned"] and not r["consolidated"]]
    m = missing(conn)
    return {"base_id": base_id, "ready": not m, "missing": m, "threshold": threshold, "default_threshold": DEFAULT_THRESHOLD, "fields": rows, "lists": out_lists,
            "counts": {"select_fields": len(live), "long": sum(1 for r in live if r["long"]), "authorized": sum(1 for r in live if r["list"]),
                       "long_not_decided": sum(1 for r in live if r["long"] and not r["list"]), "repeats": sum(1 for r in live if r["repeat_count"])}}


# ---------------------------------------------------------------- writing
def _batch(conn):
    return "L%d" % (conn.execute("SELECT COALESCE(MAX(event_id), 0) + 1 FROM lookup_list_events").fetchone()[0])


def check_name(conn, base_id, name, skip_list=None):
    """The list name as stored (trimmed), or a StoreError with the plain reason. It is the name of a Planning record type, so the Planning naming rules apply, and it may
    not equal a table's Planning name or another list's name (record type names are checked per Workspace)."""
    name = (name or "").strip()
    settings, _ = fs.read_settings(conn, base_id)
    bad = fn.violations(name, "planning_name", settings)
    if bad:
        raise StoreError("The list name %r breaks the Planning naming rules: %s. Proposed: %r." % (name, "; ".join(fn.RULE_BY_ID[r].title for r in bad), fn.clean_table_name(name, settings)))
    clash = _taken_names(conn, base_id, settings, skip_list).get(name.casefold())
    if clash:
        raise StoreError("The list name %r is already used by %s (record type names must be unique in a Workspace). Choose another name." % (name, clash))
    return name


def _select_fields(conn, base_id, field_ids):
    """The chosen fields as review rows, each a live select field of the base; a StoreError names the first that is not."""
    if not isinstance(field_ids, list) or not field_ids:
        raise StoreError("Choose at least one field.")
    if len(field_ids) > MAX_FIELDS_PER_CALL:
        raise StoreError("Choose at most %d fields at a time." % MAX_FIELDS_PER_CALL)
    have = {r["field_id"]: r for r in dts._fields(conn, base_id) if r["airtable_type"] in SELECT_TYPES}
    out, seen = [], set()
    for fid in field_ids:
        fid = str(fid or "")
        r = have.get(fid)
        if r is None or r["abandoned"] or r["consolidated"]:
            raise StoreError("Field %r is not an active single or multiple select field of this base." % fid, 404)
        if fid not in seen:
            seen.add(fid)
            out.append(r)
    return out


def assign(conn, base_id, actor, reason, field_ids, list_id=None, list_name=None):
    """Authorize select fields to be migrated as a lookup list, all or nothing, with one who and one why. Give an existing list_id, or a list_name to create a new list
    (the name is checked by check_name). Returns {batch, list_id, created, changed, unchanged}."""
    _require(conn)
    actor, reason = fs.clean_actor(actor), fs._reason(reason, required=True)
    rows = _select_fields(conn, base_id, field_ids)
    if bool(list_id) == bool(list_name):
        raise StoreError("Give either an existing list or a name for a new one.")
    created = False
    if list_id:
        lst = conn.execute("SELECT * FROM lookup_lists WHERE list_id=? AND base_id=?", (list_id, base_id)).fetchone()
        if not lst:
            raise StoreError("There is no lookup list %r for this base." % str(list_id), 404)
        name = lst["list_name"]
    else:
        name, created = check_name(conn, base_id, list_name), True
    old = _decisions(conn)
    with conn:
        batch = _batch(conn)
        if created:
            list_id = conn.execute("INSERT INTO lookup_lists(base_id, list_name, created_by, reason) VALUES (?,?,?,?)", (base_id, name, actor, reason)).lastrowid
            conn.execute("INSERT INTO lookup_list_events(batch_id, base_id, event_type, list_id, new_name, actor, reason) VALUES (?,?,?,?,?,?,?)", (batch, base_id, "list_create", list_id, name, actor, reason))
        changed = 0
        for r in rows:
            prev = old.get(r["field_id"])
            if prev and prev["list_id"] == list_id:
                continue
            conn.execute("INSERT INTO field_lookup_decisions(airtable_field_id, list_id, decided_by, reason) VALUES (?,?,?,?) ON CONFLICT(airtable_field_id) DO UPDATE SET "
                         "list_id=excluded.list_id, decided_by=excluded.decided_by, decided_at=CURRENT_TIMESTAMP, reason=excluded.reason", (r["field_id"], list_id, actor, reason))
            conn.execute("INSERT INTO lookup_list_events(batch_id, base_id, event_type, list_id, airtable_field_id, old_list_id, actor, reason) VALUES (?,?,?,?,?,?,?,?)",
                         (batch, base_id, "assign", list_id, r["field_id"], prev["list_id"] if prev else None, actor, reason))
            changed += 1
    return {"batch": batch, "list_id": list_id, "created": created, "changed": changed, "unchanged": len(rows) - changed}


def unassign(conn, base_id, actor, reason, field_ids):
    """Return fields to ordinary select fields. The list stays (it may be empty), and the history keeps what was decided."""
    _require(conn)
    actor, reason = fs.clean_actor(actor), fs._reason(reason, required=True)
    rows = _select_fields(conn, base_id, field_ids)
    old = _decisions(conn)
    with conn:
        batch = _batch(conn)
        changed = 0
        for r in rows:
            prev = old.get(r["field_id"])
            if not prev:
                continue
            conn.execute("DELETE FROM field_lookup_decisions WHERE airtable_field_id=?", (r["field_id"],))
            conn.execute("INSERT INTO lookup_list_events(batch_id, base_id, event_type, list_id, airtable_field_id, old_list_id, actor, reason) VALUES (?,?,?,?,?,?,?,?)",
                         (batch, base_id, "unassign", None, r["field_id"], prev["list_id"], actor, reason))
            changed += 1
    return {"batch": batch, "changed": changed, "unchanged": len(rows) - changed}


def _own_list(conn, base_id, list_id):
    lst = conn.execute("SELECT * FROM lookup_lists WHERE list_id=? AND base_id=?", (list_id, base_id)).fetchone()
    if not lst:
        raise StoreError("There is no lookup list %r for this base." % str(list_id), 404)
    return lst


def rename_list(conn, base_id, actor, reason, list_id, new_name):
    _require(conn)
    actor, reason = fs.clean_actor(actor), fs._reason(reason, required=True)
    lst = _own_list(conn, base_id, list_id)
    name = check_name(conn, base_id, new_name, skip_list=lst["list_id"])
    if name == lst["list_name"]:
        return {"changed": False}
    with conn:
        conn.execute("UPDATE lookup_lists SET list_name=? WHERE list_id=?", (name, lst["list_id"]))
        conn.execute("INSERT INTO lookup_list_events(batch_id, base_id, event_type, list_id, old_name, new_name, actor, reason) VALUES (?,?,?,?,?,?,?,?)",
                     (_batch(conn), base_id, "list_rename", lst["list_id"], lst["list_name"], name, actor, reason))
    return {"changed": True}


def delete_list(conn, base_id, actor, reason, list_id):
    """Remove a list no field uses any more. A list that fields still use is refused: return those fields to select fields first."""
    _require(conn)
    actor, reason = fs.clean_actor(actor), fs._reason(reason, required=True)
    lst = _own_list(conn, base_id, list_id)
    n = conn.execute("SELECT COUNT(*) FROM field_lookup_decisions WHERE list_id=?", (lst["list_id"],)).fetchone()[0]
    if n:
        raise StoreError("%d field(s) still use the list %r. Return them to select fields first." % (n, lst["list_name"]), 409)
    with conn:
        conn.execute("DELETE FROM lookup_lists WHERE list_id=?", (lst["list_id"],))
        conn.execute("INSERT INTO lookup_list_events(batch_id, base_id, event_type, list_id, old_name, actor, reason) VALUES (?,?,?,?,?,?,?)",
                     (_batch(conn), base_id, "list_delete", lst["list_id"], lst["list_name"], actor, reason))
    return {"changed": True}
