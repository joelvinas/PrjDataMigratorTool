#!/usr/bin/env python3
"""Saved data type decisions per field and system (migrations 021 and 022). Standard library only.

Where a type lives:
  Planning  field_names.target_type          one of the 17 Planning types (src/datatypes.py)
  Core      field_names.core_target_type     the Core dataType, and
            field_names.core_display_type    the Core displayType (two values, because the Core API needs both)
The load plan reads those columns. This module is the only writer. Every change is a person's decision: who, when and why go to field_type_decisions
(the latest decision per field and system) and to data_type_events (append-only history; a bulk update shares one batch ID). The tool proposes a type from
data_type_defaults (the default mapping per Airtable type); a person applies the default, overrides it, or clears it. A stored value that is not on its list
is shown as invalid and is cleared by a person (clear_invalid); the old value is kept in the history. Only schema metadata is read, never a record value.
Nothing is random: a batch ID is "T" plus the next event number.
"""
import datatypes as dt
import fieldgroups as fgp
import fieldnames_store as fs
from fieldnames_store import StoreError

TABLES = ("data_type_defaults", "field_type_decisions", "data_type_events")
NEEDS_COLUMN = "core_display_type"
SOURCES = ("default", "override", "import")


def missing(conn):
    """What migrations 021 and 022 would still have to add (an empty list when the mapping is ready)."""
    out = [t for t in TABLES if not fs.has_table(conn, t)]
    if NEEDS_COLUMN not in {r[1] for r in conn.execute("PRAGMA table_info(field_names)")}:
        out.append("field_names." + NEEDS_COLUMN)
    if fs.has_table(conn, "data_type_defaults") and "rule_text" not in {r[1] for r in conn.execute("PRAGMA table_info(data_type_defaults)")}:
        out.append("data_type_defaults.rule_text")
    return out


def _require(conn):
    m = missing(conn)
    if m:
        raise StoreError("The data type mapping needs migrations 021 and 022 applied to this database first. Missing: %s." % ", ".join(m), 409)


def _system(s):
    if s not in dt.SYSTEMS:
        raise StoreError("The system must be 'planning' or 'core'.")
    return s


def _check_value(system, type_, display):
    """(type, display) in canonical form, or a StoreError with the plain reason."""
    if system == "planning":
        p = dt.planning_problem(type_)
        if p:
            raise StoreError(p)
        return dt.planning_type(type_), None
    p = dt.core_problem(type_, display)
    if p:
        raise StoreError(p)
    return dt.core_pair(type_, display)


# ---------------------------------------------------------------- the default mapping
def read_defaults(conn):
    """[{system, airtable_type, type, display_type, note, rule_text, updated_by, updated_at}], sorted. Empty until migration 021 exists (rule_text is NULL until 023)."""
    if not fs.has_table(conn, "data_type_defaults"):
        return []
    return [{"system": r["system"], "airtable_type": r["airtable_type"], "type": r["default_type"], "display_type": r["default_display_type"], "note": r["note"],
             "rule_text": r["rule_text"] if "rule_text" in r.keys() else None, "updated_by": r["updated_by"], "updated_at": r["updated_at"]}
            for r in conn.execute("SELECT * FROM data_type_defaults ORDER BY system, airtable_type")]


def set_default(conn, actor, system, airtable_type, type_, display_type, reason):
    """Change one default (type_ None = no default, a person must decide). Who and why are recorded."""
    _require(conn)
    actor, reason, system = fs.clean_actor(actor), fs._reason(reason, required=True), _system(system)
    at = str(airtable_type or "").strip()
    old = conn.execute("SELECT * FROM data_type_defaults WHERE system=? AND airtable_type=?", (system, at)).fetchone()
    if not old:
        raise StoreError("There is no default mapping row for %s fields of Airtable type %r." % (system, at), 404)
    if type_ or display_type:
        type_, display_type = _check_value(system, type_, display_type)
    else:
        type_ = display_type = None
    now = fs._now()
    with conn:
        conn.execute("UPDATE data_type_defaults SET default_type=?, default_display_type=?, updated_by=?, updated_at=? WHERE system=? AND airtable_type=?",
                     (type_, display_type, actor, now, system, at))
        conn.execute("INSERT INTO data_type_events(system, airtable_type, event_type, old_type, old_display_type, new_type, new_display_type, actor, reason) "
                     "VALUES (?,?,?,?,?,?,?,?,?)", (system, at, "default_change", old["default_type"], old["default_display_type"], type_, display_type, actor, reason))
    return {"changed": True}


# ---------------------------------------------------------------- the grid
def _decisions(conn):
    if not fs.has_table(conn, "field_type_decisions"):
        return {}
    return {(r["airtable_field_id"], r["system"]): r for r in conn.execute("SELECT * FROM field_type_decisions")}


def _fields(conn, base_id):
    """The base's fields in a fixed order, each with its stored types and decisions. Abandoned and consolidated-away fields are marked, not removed."""
    data = fs.load_fields(conn, base_id)
    _, names = fs._stored(conn, base_id)
    groups = fs._live_groups(fs.read_groups(conn, base_id)[0], data)
    merged = fgp.merged_ids(groups)
    defaults = {(d["system"], d["airtable_type"]): d for d in read_defaults(conn)}
    decisions = _decisions(conn)
    rows = []
    for f in sorted(data["fields"], key=lambda x: (x["table_name"].casefold(), x["table_id"], x["field_name"].casefold(), x["field_id"])):
        n = names.get(f["field_id"]) or {}
        out = {"field_id": f["field_id"], "table_id": f["table_id"], "table_name": f["table_name"], "field_name": f["field_name"], "airtable_type": f["data_type"],
               "abandoned": bool(n.get("abandoned")), "consolidated": f["field_id"] in merged}
        for system in dt.SYSTEMS:
            if system == "planning":
                cur, disp = n.get("target_type"), None
            else:
                cur, disp = n.get("core_target_type"), n.get("core_display_type")
            d = defaults.get((system, f["data_type"])) or {}
            dec = decisions.get((f["field_id"], system))
            proposed = None
            if d.get("type"):
                proposed = {"type": d["type"], "display_type": d["display_type"]}
            out[system] = {"type": cur, "display_type": disp, "state": dt.state(system, cur, disp), "proposed": proposed, "note": d.get("note"),
                           "decision": {"source": dec["source"], "by": dec["decided_by"], "at": dec["decided_at"], "reason": dec["reason"]} if dec else None}
        rows.append(out)
    return rows


def review(conn, base_id):
    """Everything the page shows. Works before the migrations are applied (defaults and decisions are then empty and 'ready' is false)."""
    rows = _fields(conn, base_id)
    counts = {s: {"set": 0, "unset": 0, "invalid": 0} for s in dt.SYSTEMS}
    for r in rows:
        if r["abandoned"] or r["consolidated"]:
            continue
        for s in dt.SYSTEMS:
            counts[s][r[s]["state"]] += 1
    m = missing(conn)
    return {"base_id": base_id, "ready": not m, "missing": m, "planning_types": list(dt.PLANNING_TYPES), "core_data_types": list(dt.CORE_DATA_TYPES),
            "core_display_types": list(dt.CORE_DISPLAY_TYPES), "unverified_display_types": sorted(dt.CORE_UNVERIFIED_DISPLAY), "defaults": read_defaults(conn),
            "fields": rows, "counts": counts, "airtable_types": sorted({r["airtable_type"] for r in rows})}


# ---------------------------------------------------------------- writing
def _batch(conn):
    return "T%d" % (conn.execute("SELECT COALESCE(MAX(event_id), 0) + 1 FROM data_type_events").fetchone()[0])


def _store(conn, fid, system, type_, display):
    if system == "planning":
        conn.execute("INSERT INTO field_names(airtable_field_id, target_type) VALUES (?,?) ON CONFLICT(airtable_field_id) DO UPDATE SET target_type=excluded.target_type, "
                     "updated_at=CURRENT_TIMESTAMP", (fid, type_))
    else:
        conn.execute("INSERT INTO field_names(airtable_field_id, core_target_type, core_display_type) VALUES (?,?,?) ON CONFLICT(airtable_field_id) DO UPDATE SET "
                     "core_target_type=excluded.core_target_type, core_display_type=excluded.core_display_type, updated_at=CURRENT_TIMESTAMP", (fid, type_, display))


def _write(conn, row, system, new, source, actor, reason, batch):
    """One field and system: store the value, the decision and the event. new = (type, display) or None to clear. True when something changed."""
    cur = row[system]
    old = (cur["type"], cur["display_type"])
    nt, nd = new if new else (None, None)
    if (nt, nd) == old and (new is None or cur["state"] == "set"):
        return False
    _store(conn, row["field_id"], system, nt, nd)
    if new:
        conn.execute("INSERT INTO field_type_decisions(airtable_field_id, system, chosen_type, chosen_display_type, source, decided_by, reason) VALUES (?,?,?,?,?,?,?) "
                     "ON CONFLICT(airtable_field_id, system) DO UPDATE SET chosen_type=excluded.chosen_type, chosen_display_type=excluded.chosen_display_type, "
                     "source=excluded.source, decided_by=excluded.decided_by, decided_at=CURRENT_TIMESTAMP, reason=excluded.reason",
                     (row["field_id"], system, nt, nd, source, actor, reason))
    else:
        conn.execute("DELETE FROM field_type_decisions WHERE airtable_field_id=? AND system=?", (row["field_id"], system))
    kind = "clear" if not new else ("set" if cur["state"] == "unset" else "change")
    conn.execute("INSERT INTO data_type_events(batch_id, airtable_field_id, system, event_type, old_type, old_display_type, new_type, new_display_type, source, actor, reason) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (batch, row["field_id"], system, kind, old[0], old[1], nt, nd, source if new else None, actor, reason))
    return True


def _select(rows, selection):
    """Fields chosen by the page: field_ids, table_ids, airtable_types and states (each optional; all given ones must match). Abandoned and merged fields never match."""
    sel = selection or {}
    ids, tabs, types = (set(sel.get(k) or []) for k in ("field_ids", "table_ids", "airtable_types"))
    return [r for r in rows if not r["abandoned"] and not r["consolidated"] and (not ids or r["field_id"] in ids) and (not tabs or r["table_id"] in tabs)
            and (not types or r["airtable_type"] in types)]


def apply(conn, base_id, actor, reason, system, mode, selection=None, value=None, changes=None, overwrite=False):
    """Set, default or clear types for a chosen set of fields, all or nothing, with one who and one why for the batch.
    mode 'defaults'  each chosen field takes the default for its Airtable type (a field that already has a valid type is kept unless overwrite is true)
         'value'     every chosen field gets value = {type, display_type} (a person's override)
         'each'      changes = [{field_id, type, display_type}]: a different value per field (the single-field edit)
         'clear'     every chosen field is emptied for this system
    selection filters the fields (see _select); states ('unset', 'invalid', 'set') filters by what is stored now."""
    _require(conn)
    actor, reason, system = fs.clean_actor(actor), fs._reason(reason, required=True), _system(system)
    if mode not in ("defaults", "value", "each", "clear"):
        raise StoreError("The mode must be defaults, value, each or clear.")
    rows = _fields(conn, base_id)
    by_id = {r["field_id"]: r for r in rows}
    plan, skipped = [], {"no_default": 0, "already_set": 0, "not_selected": 0}
    if mode == "each":
        if not isinstance(changes, list) or not changes:
            raise StoreError("Give at least one field to change.")
        for c in changes:
            r = by_id.get(str((c or {}).get("field_id") or ""))
            if not r or r["abandoned"] or r["consolidated"]:
                raise StoreError("Field %r is not an active field of this base." % str((c or {}).get("field_id") or ""), 404)
            plan.append((r, _check_value(system, c.get("type"), c.get("display_type"))))
    else:
        chosen = _select(rows, selection)
        states = set((selection or {}).get("states") or [])
        if states:
            chosen = [r for r in chosen if r[system]["state"] in states]
        if not chosen:
            raise StoreError("No fields match the selection.")
        fixed = _check_value(system, (value or {}).get("type"), (value or {}).get("display_type")) if mode == "value" else None
        for r in chosen:
            if mode == "clear":
                plan.append((r, None))
            elif mode == "value":
                plan.append((r, fixed))
            else:
                p = r[system]["proposed"]
                if not p:
                    skipped["no_default"] += 1
                elif r[system]["state"] == "set" and not overwrite:
                    skipped["already_set"] += 1
                else:
                    plan.append((r, _check_value(system, p["type"], p["display_type"])))
    with conn:
        batch = _batch(conn)
        source = "default" if mode == "defaults" else "override"
        changed = sum(1 for r, new in plan if _write(conn, r, system, new, source, actor, reason, batch))
    return {"batch": batch, "changed": changed, "unchanged": len(plan) - changed, "skipped": skipped}


def clear_invalid(conn, base_id, actor, reason):
    """Empty every stored type that is not on its list (a Planning value that is not one of the 17, a Core pair that is not a valid dataType and displayType, or
    half a pair). The API would refuse these values, so they are removed and must be set again. The old values are kept in the history."""
    _require(conn)
    actor, reason = fs.clean_actor(actor), fs._reason(reason, required=True)
    rows = _fields(conn, base_id)
    with conn:
        batch = _batch(conn)
        cleared = {s: sum(1 for r in rows if r[s]["state"] == "invalid" and _write(conn, r, s, None, None, actor, reason, batch)) for s in dt.SYSTEMS}
    return {"batch": batch, "cleared": cleared}


def record_import(conn, actor, items, batch):
    """After a spreadsheet import stored types, record who imported them. items = [(field_id, system, old_type, old_display, new_type, new_display)], new already on
    its list. Nothing happens before the migrations are applied."""
    if missing(conn):
        return 0
    for fid, system, old, old_disp, new, new_disp in sorted(items):
        conn.execute("INSERT INTO field_type_decisions(airtable_field_id, system, chosen_type, chosen_display_type, source, decided_by, reason) VALUES (?,?,?,?,?,?,?) "
                     "ON CONFLICT(airtable_field_id, system) DO UPDATE SET chosen_type=excluded.chosen_type, chosen_display_type=excluded.chosen_display_type, "
                     "source=excluded.source, decided_by=excluded.decided_by, decided_at=CURRENT_TIMESTAMP, reason=excluded.reason", (fid, system, new, new_disp, "import", actor, batch))
        conn.execute("INSERT INTO data_type_events(batch_id, airtable_field_id, system, event_type, old_type, old_display_type, new_type, new_display_type, source, actor, reason) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (batch, fid, system, "change" if old or old_disp else "set", old, old_disp, new, new_disp, "import", actor, batch))
    return len(items)
