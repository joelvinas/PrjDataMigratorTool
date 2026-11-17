#!/usr/bin/env python3
"""Link directions: which side of each link loads first. Standard library only; schema metadata only (no record value is read).

The tool DETECTS pairs (a link field and its inverse) and SUGGESTS a direction from Airtable's single-or-multiple-record setting.
A person CONFIRMS each one (who, when and why are kept on the row, in field_relationships, migration 015). Nothing is decided silently.

The choice is made on ONE field and sets its partner, which is shown as auto-set and can be changed from either end:
    one_to_many   this field's table loads first (it is the parent)        partner is many_to_one
    many_to_one   the other table loads first (this table is the child)    partner is one_to_many
    one_to_one    the tiebreaker says which table loads first              partner is one_to_one
    many_to_many  the tiebreaker says which table loads first              partner is many_to_many
The tiebreaker is this_first, other_first or automatic. An automatic pair is a non-binding edge: the load order settles it (derived) when
the other decisions already do, otherwise the tool picks one deterministically and flags it for a person to review.
"""
from datetime import datetime, timezone

TYPES = ("one_to_many", "many_to_one", "one_to_one", "many_to_many")
TIEBREAKERS = ("this_first", "other_first", "automatic")
NEEDS_TIEBREAKER = ("one_to_one", "many_to_many")
MIRROR = {"one_to_many": "many_to_one", "many_to_one": "one_to_many", "one_to_one": "one_to_one", "many_to_many": "many_to_many"}
TIE_MIRROR = {"this_first": "other_first", "other_first": "this_first", "automatic": "automatic"}
TYPE_LABEL = {"one_to_many": "One to many (this table loads first)", "many_to_one": "Many to one (the other table loads first)",
              "one_to_one": "One to one (tiebreaker)", "many_to_many": "Many to many (tiebreaker)"}
REASON_MAX = 500


class DirectionError(Exception):
    def __init__(self, message, status=400):
        super(DirectionError, self).__init__(message)
        self.message, self.status = message, status


def suggest(single, partner_single, has_partner):
    """What the schema suggests. single / partner_single: Airtable's prefersSingleRecordLink (True, False or None when not reported)."""
    if not has_partner:
        return "many_to_one" if single else "many_to_many"
    if single and not partner_single:
        return "many_to_one"
    if partner_single and not single:
        return "one_to_many"
    return "one_to_one" if (single and partner_single) else "many_to_many"


def ordering(this_table, other_table, rtype, tiebreaker):
    """(child, parent) for a confirmed direction, 'automatic' for a non-binding edge, or None when there is no usable decision."""
    if rtype == "one_to_many":
        return (other_table, this_table)
    if rtype == "many_to_one":
        return (this_table, other_table)
    if rtype in NEEDS_TIEBREAKER:
        if tiebreaker == "this_first":
            return (other_table, this_table)
        if tiebreaker == "other_first":
            return (this_table, other_table)
        if tiebreaker == "automatic":
            return "automatic"
    return None


def build_units(rows, rel):
    """rows: non-stale airtable_fields rows (dict-like) for every link field to consider, plus the fields they pair with (all rows of those
    tables are fine). rel: {field_id: stored field_relationships row (dict) or absent}. Returns units sorted for display: one per two-way pair
    or one-way field, each with its fields and the decision (if any). Self-links are left out: they order nothing."""
    by_field = {r["field_id"]: r for r in rows}
    seen, units = set(), []
    link_rows = sorted((r for r in rows if r["linked_table_id"] and r["linked_table_id"] != r["table_id"]),
                       key=lambda r: (r["table_name"].lower(), r["field_name"].lower(), r["field_id"]))
    tname = {r["table_id"]: r["table_name"] for r in rows}
    for a in link_rows:
        if a["field_id"] in seen:
            continue
        b = by_field.get(a["inverse_link_field_id"]) if a["inverse_link_field_id"] else None
        if b is not None and not (b["linked_table_id"] == a["table_id"] and b["table_id"] == a["linked_table_id"]):
            b = None
        members = [a] if b is None else sorted((a, b), key=lambda r: (r["table_name"].lower(), r["field_name"].lower(), r["field_id"]))
        for m in members:
            seen.add(m["field_id"])
        fields = []
        for m in members:
            p = None if b is None else (b if m is a else a)
            single = None if m["prefers_single_record_link"] is None else m["prefers_single_record_link"] == 1
            psingle = None if (p is None or p["prefers_single_record_link"] is None) else p["prefers_single_record_link"] == 1
            st = rel.get(m["field_id"]) or {}
            fields.append({"field_id": m["field_id"], "field_name": m["field_name"], "table_id": m["table_id"], "table_name": m["table_name"],
                           "base_id": m["base_id"], "linked_table_id": m["linked_table_id"], "linked_table_name": tname.get(m["linked_table_id"], m["linked_table_id"]),
                           "partner_field_id": p["field_id"] if p else None, "single": single, "partner_field_name": p["field_name"] if p else None,
                           "suggested_type": suggest(single, psingle, p is not None),
                           "relationship_type": st.get("relationship_type"), "tiebreaker": st.get("tiebreaker"),
                           "confirmed": bool(st.get("confirmed")), "auto_set": bool(st.get("auto_set")), "derived": bool(st.get("derived")),
                           "decided_by": st.get("approved_by"), "decided_at": st.get("approved_at"), "reason": st.get("reason"),
                           "row_count": m["row_count"], "null_count": m["null_count"], "cardinality": m["cardinality"], "cardinality_capped": bool(m["cardinality_is_capped"])})
        units.append({"key": "|".join(sorted(f["field_id"] for f in fields)), "fields": fields, "decision": decision_of(fields)})
    return units


def decision_of(fields):
    """The decision for one unit, read from the field a person chose on (a field that is not auto-set), else from an auto-set one. None = undecided.
    Returns {field_id, table_id, other_table_id, relationship_type, tiebreaker, ordering} or None."""
    cands = [f for f in fields if f["confirmed"] and f["relationship_type"]]
    if not cands:
        return None
    cands.sort(key=lambda f: (f["auto_set"], f["table_name"].lower(), f["field_name"].lower(), f["field_id"]))
    f = cands[0]
    return {"field_id": f["field_id"], "table_id": f["table_id"], "other_table_id": f["linked_table_id"], "relationship_type": f["relationship_type"],
            "tiebreaker": f["tiebreaker"], "ordering": ordering(f["table_id"], f["linked_table_id"], f["relationship_type"], f["tiebreaker"])}


def _reaches(adj, src, dst):
    stack, seen = [src], {src}
    while stack:
        n = stack.pop()
        if n == dst:
            return True
        for m in adj.get(n, ()):
            if m not in seen:
                seen.add(m)
                stack.append(m)
    return False


def settle_automatic(binding_edges, automatic, order_key):
    """binding_edges: {(child, parent)} already decided. automatic: [(unit_key, table_x, table_y)] sorted. Returns (settled, added, loops):
    settled: unit keys the decided edges already order (derived, nothing to do); added: {unit_key: (child, parent)} the tool had to choose, repeatably
    (the table that sorts first loads first), flagged for review; loops: unit keys already tied to the tool's own earlier choices (automatic pairs that
    form a loop or a chain nothing decided breaks). No edge is added for those: a person has to settle them, so nothing is chosen silently."""
    decided, every, chosen = {}, {}, {}
    for c, p in sorted(binding_edges):
        decided.setdefault(c, set()).add(p)
        every.setdefault(c, set()).add(p)
    settled, added, loops = set(), {}, set()
    for key, x, y in automatic:
        if _reaches(decided, x, y) or _reaches(decided, y, x):
            settled.add(key)
        elif _reaches(every, x, y) or _reaches(every, y, x) or _reaches(chosen, x, y):
            loops.add(key)
        else:
            first, second = sorted((x, y), key=order_key)
            added[key] = (second, first)
            every.setdefault(second, set()).add(first)
            chosen.setdefault(x, set()).add(y)
            chosen.setdefault(y, set()).add(x)
    return settled, added, loops


CAP = 30            # reference (connection) fields per Planning record type (v2 reference)
NEAR = 3            # warn when a table is within this many of the cap


def connection_counts(rows):
    """Link fields per table, from the ingested schema (rows: non-stale airtable_fields rows of tables in scope). Counts every link field whose linked table is also in
    scope, self-links included: those are the reference fields Planning would have to create. Each side of a two-way pair is its own field, so a pair uses a slot on
    both tables. The loader sends no back reference (Postman test 4 showed one would use a slot on the other table too), so no
    extra slot is assumed. Returns [{table_id, table_name, links, status}] sorted by count (largest first) then name; status is over, near or ok."""
    tables = {r["table_id"] for r in rows}
    n, names = {}, {}
    for r in rows:
        names[r["table_id"]] = r["table_name"]
        if r["linked_table_id"] and r["linked_table_id"] in tables:
            n[r["table_id"]] = n.get(r["table_id"], 0) + 1
    out = [{"table_id": t, "table_name": names[t], "links": c, "cap": CAP, "status": "over" if c > CAP else "near" if c >= CAP - NEAR else "ok"} for t, c in n.items()]
    out.sort(key=lambda x: (-x["links"], x["table_name"].lower(), x["table_id"]))
    return out


def ambiguous(units):
    """Pairs the schema cannot orient (one to one, or many to many): they need a tiebreaker. {total, undecided, by_type}."""
    amb = [u for u in units if any(f["suggested_type"] in NEEDS_TIEBREAKER for f in u["fields"])]
    kinds = {}
    for u in amb:
        k = sorted({f["suggested_type"] for f in u["fields"] if f["suggested_type"] in NEEDS_TIEBREAKER})[0]
        kinds[k] = kinds.get(k, 0) + 1
    return {"total": len(amb), "undecided": sum(1 for u in amb if not u["decision"]), "by_type": dict(sorted(kinds.items()))}


# ------------------------------------------------------------------ stored decisions (field_relationships)
def has_direction_columns(conn):
    return "suggested_type" in {r[1] for r in conn.execute("PRAGMA table_info(field_relationships)")}


def read_stored(conn):
    """{field_id: row as a dict}. Empty when the table or its direction columns are missing (an older database)."""
    if not has_direction_columns(conn):
        return {}
    cur = conn.execute("SELECT * FROM field_relationships")
    names = [d[0] for d in cur.description]
    return {r[0]: dict(zip(names, tuple(r))) for r in cur.fetchall()}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def sync(conn, rows):
    """Write what the tool PROPOSES: one row per link field that points at another known table, with its pair and its suggested direction.
    A confirmed row keeps its decision; only the facts read from the schema are refreshed. rows: non-stale airtable_fields rows. Returns counts."""
    if not has_direction_columns(conn):
        raise DirectionError("Link directions need migrations 015 to 017 on this database.", 409)
    stored = read_stored(conn)
    known = {r[0] for r in conn.execute("SELECT table_id FROM airtable_tables")}
    units = build_units(rows, stored)
    added = updated = 0
    with conn:
        for u in units:
            for f in u["fields"]:
                if f["linked_table_id"] not in known or f["table_id"] not in known:
                    continue
                card = None if f["single"] is None else ("one" if f["single"] else "many")
                if f["field_id"] in stored:
                    conn.execute("UPDATE field_relationships SET inverse_field_id=?, cardinality=?, suggested_type=?, updated_at=CURRENT_TIMESTAMP WHERE airtable_field_id=?",
                                 (f["partner_field_id"], card, f["suggested_type"], f["field_id"]))
                    updated += 1
                else:
                    conn.execute("INSERT INTO field_relationships(airtable_field_id, base_id, from_table_id, to_table_id, inverse_field_id, cardinality, suggested_type) "
                                 "VALUES (?,?,?,?,?,?,?)", (f["field_id"], f["base_id"], f["table_id"], f["linked_table_id"], f["partner_field_id"], card, f["suggested_type"]))
                    added += 1
    return {"added": added, "updated": updated}


def has_events_table(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='link_direction_events'").fetchone() is not None


def _event(conn, field_id, event_type, actor, reason, old, new):
    """Append to the history (migration 018) when that table exists; before it is applied nothing is written and nothing breaks."""
    if not has_events_table(conn):
        return
    base = conn.execute("SELECT base_id FROM field_relationships WHERE airtable_field_id=?", (field_id,)).fetchone()[0]
    conn.execute("INSERT INTO link_direction_events(airtable_field_id, base_id, event_type, old_relationship_type, old_tiebreaker, new_relationship_type, new_tiebreaker, actor, reason) "
                 "VALUES (?,?,?,?,?,?,?,?,?)", (field_id, base, event_type, old[0], old[1], new[0], new[1], actor, reason))


def _current(conn, field_id):
    r = conn.execute("SELECT relationship_type, tiebreaker, confirmed FROM field_relationships WHERE airtable_field_id=?", (field_id,)).fetchone()
    return (r[0], r[1]), bool(r[2])


def decide(conn, actor, field_id, relationship_type, tiebreaker, reason=""):
    """Record a person's choice on one field and set its partner (auto-set). relationship_type 'clear' returns the pair to undecided."""
    actor = (actor or "").strip()
    if not actor or len(actor) > 40 or any(ord(c) < 32 for c in actor):
        raise DirectionError("Enter your initials or a short name (up to 40 characters) before making a decision.")
    reason = (reason or "").strip() or "set from the page"
    if len(reason) > REASON_MAX or any(ord(c) < 32 and c not in "\n\t" for c in reason):
        raise DirectionError("The reason must be plain text of up to %d characters." % REASON_MAX)
    if not has_direction_columns(conn):
        raise DirectionError("Link directions need migrations 015 to 017 on this database.", 409)
    row = conn.execute("SELECT airtable_field_id, inverse_field_id FROM field_relationships WHERE airtable_field_id=?", (str(field_id),)).fetchone()
    if not row:
        raise DirectionError("That link field has no saved direction row. Refresh the list first.", 404)
    partner = row[1] if row[1] and conn.execute("SELECT 1 FROM field_relationships WHERE airtable_field_id=?", (row[1],)).fetchone() else None
    if relationship_type == "clear":
        with conn:
            for fid in [row[0]] + ([partner] if partner else []):
                old, was = _current(conn, fid)
                if was:
                    _event(conn, fid, "clear", actor, reason, old, (None, None))
                conn.execute("UPDATE field_relationships SET relationship_type=NULL, tiebreaker=NULL, confirmed=0, derived=0, auto_set=0, approved_by=NULL, approved_at=NULL, "
                             "reason=NULL, updated_at=CURRENT_TIMESTAMP WHERE airtable_field_id=?", (fid,))
        return {"field_id": row[0], "partner_field_id": partner, "cleared": True}
    if relationship_type not in TYPES:
        raise DirectionError("Choose one to many, many to one, one to one or many to many.")
    if relationship_type in NEEDS_TIEBREAKER:
        if tiebreaker not in TIEBREAKERS:
            raise DirectionError("For %s, choose which table loads first: this one, the other one, or automatic." % relationship_type.replace("_", " "))
    else:
        tiebreaker = None
    now = _now()
    with conn:
        old, was = _current(conn, row[0])
        _event(conn, row[0], "change" if was else "confirm", actor, reason, old, (relationship_type, tiebreaker))
        if partner:
            pold, _w = _current(conn, partner)
            _event(conn, partner, "auto_set", actor, "set with its pair field: " + reason, pold, (MIRROR[relationship_type], TIE_MIRROR.get(tiebreaker) if tiebreaker else None))
        conn.execute("UPDATE field_relationships SET relationship_type=?, tiebreaker=?, confirmed=1, derived=0, auto_set=0, approved_by=?, approved_at=?, reason=?, "
                     "updated_at=CURRENT_TIMESTAMP WHERE airtable_field_id=?", (relationship_type, tiebreaker, actor, now, reason, row[0]))
        if partner:
            conn.execute("UPDATE field_relationships SET relationship_type=?, tiebreaker=?, confirmed=1, derived=0, auto_set=1, approved_by=?, approved_at=?, reason=?, "
                         "updated_at=CURRENT_TIMESTAMP WHERE airtable_field_id=?",
                         (MIRROR[relationship_type], TIE_MIRROR.get(tiebreaker) if tiebreaker else None, actor, now, "set with its pair field: " + reason, partner))
    return {"field_id": row[0], "partner_field_id": partner, "cleared": False}


def mark_derived(conn, derived_keys, units):
    """After a recompute: flag the automatic pairs the load order already settles (derived). Not a person's choice, so confirmed is untouched."""
    if not has_direction_columns(conn):
        return
    ids = {f["field_id"] for u in units if u["key"] in derived_keys for f in u["fields"]}
    with conn:
        conn.execute("UPDATE field_relationships SET derived=0 WHERE derived=1")
        for fid in sorted(ids):
            conn.execute("UPDATE field_relationships SET derived=1 WHERE airtable_field_id=?", (fid,))


def field_states(conn):
    """{link field_id: True when its pair is decided, False when not}, for the load plan. None when the database has no direction columns (older database:
    the plan then behaves as before). conn must return rows by name (sqlite3.Row)."""
    if not has_direction_columns(conn):
        return None
    rows = [r for r in conn.execute("SELECT f.*, (f.updated_at < (SELECT MAX(x.updated_at) FROM airtable_fields x WHERE x.base_id = f.base_id)) AS stale FROM airtable_fields f")
            if not r["stale"]]
    tables = {r["table_id"] for r in rows}
    return {f["field_id"]: u["decision"] is not None for u in build_units(rows, read_stored(conn)) for f in u["fields"] if all(x["linked_table_id"] in tables for x in u["fields"])}
