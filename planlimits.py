#!/usr/bin/env python3
"""Workfront Planning limits, checked against the load plan before anything is sent. Standard library only; names and counts only.

The limits come from the saved Planning API v2 reference (create record type, create field) and are DOCUMENTED, not tested: the Postman tests confirm whether
each is enforced. A finding never changes an item's status on its own: it is a plain-language warning for a person to decide (split a table, drop a field,
shorten a description). Only the Planning items of the plan are checked (Core has its own limits, which are not checked here).

    record types per Workspace      100
    fields per record type          500
    long-text fields per record type 20
    formula fields per record type   20    (formulas are on hold in the plan, so they are counted only if they are planned)
    reference (link) fields per record type 30
    field description               512 characters (v2 reference). The v3 field PATCH documentation says 1,024; the owner chose to keep 512 as the safer limit
    record type name                255 characters, field name 256 (the app's own Planning name limit of 250 is stricter)
    select options                  a non-blank name, unique without regard to letter case, within one field
    records per record type         25,000  (needs the profile row count; see check_record_counts)
"""
MAX_RECORD_TYPES = 100
MAX_FIELDS = 500
MAX_LONG_TEXT = 20
MAX_FORMULA = 20
MAX_REFERENCE = 30
MAX_DESCRIPTION = 512
MAX_RECORDS = 25000
LONG_TEXT_TYPES = frozenset({"multilineText", "richText"})
FORMULA_TYPES = frozenset({"formula"})
REFERENCE_TYPES = frozenset({"multipleRecordLinks"})
LIMITS = {"record_types": MAX_RECORD_TYPES, "fields": MAX_FIELDS, "long_text": MAX_LONG_TEXT, "formula": MAX_FORMULA, "reference": MAX_REFERENCE, "description": MAX_DESCRIPTION, "records": MAX_RECORDS}


def _f(limit, scope, detail, **kw):
    return dict({"limit": limit, "scope": scope, "detail": detail}, **kw)


def check(items, airtable_types):
    """items: the plan's items. airtable_types: {airtable_field_id: Airtable field type}. Returns findings sorted by limit, scope. Only items that will be
    created are counted (status ready or hold); a blocked item is not, because it is not going to Workfront yet."""
    live = [i for i in items if i["system"] == "planning" and i["status"] in ("ready", "hold")]
    types = [i for i in live if i["kind"] == "record_type"]
    out = []
    if len(types) > MAX_RECORD_TYPES:
        out.append(_f("record_types", "Workspace", "%d record types are planned, and a Workspace holds at most %d. Split them across Workspaces or leave some out of scope." % (len(types), MAX_RECORD_TYPES)))
    by_type = {}
    for i in live:
        if i["kind"] == "field" and i["source"] == "airtable" and i.get("parent"):
            by_type.setdefault(i["parent"], []).append(i)
    names = {i["temp_id"]: i["name"] for i in types}
    count = lambda fs, kinds: sum(1 for f in fs if airtable_types.get(f["field_id"]) in kinds)
    for tid in sorted(by_type, key=lambda t: (str(names.get(t, t)).lower(), t)):
        fs, nm = by_type[tid], names.get(tid, tid)
        extra = sum(1 for i in live if i["kind"] == "field" and i["source"] == "migration" and i.get("parent") == tid)
        for key, n, cap, what in (("fields", len(fs) + extra, MAX_FIELDS, "fields"), ("long_text", count(fs, LONG_TEXT_TYPES), MAX_LONG_TEXT, "long-text fields"),
                                  ("formula", count(fs, FORMULA_TYPES), MAX_FORMULA, "formula fields"), ("reference", count(fs, REFERENCE_TYPES), MAX_REFERENCE, "reference (link) fields")):
            if n > cap:
                out.append(_f(key, nm, "%d %s are planned in this record type, and the limit is %d." % (n, what, cap), count=n, cap=cap))
            elif key == "reference" and n >= cap - 3 and n > 0:
                out.append(_f("reference_near", nm, "%d of %d reference (link) fields are planned. The limit leaves little room, and it is not yet known whether a back reference uses a second slot (Postman test 4)." % (n, cap), count=n, cap=cap))
    for i in live:
        if i["kind"] in ("record_type", "field") and i.get("description") and len(i["description"]) > MAX_DESCRIPTION:
            out.append(_f("description", i["name"], "The description is %d characters, and the limit is %d. The Fusion-driven marker comes first, so shorten the lineage text." % (len(i["description"]), MAX_DESCRIPTION),
                          temp_id=i["temp_id"], count=len(i["description"]), cap=MAX_DESCRIPTION))
    opts = {}
    for i in live:
        if i["kind"] == "option":
            opts.setdefault(i["parent"], []).append(i)
    fname = {i["temp_id"]: i["name"] for i in live if i["kind"] == "field"}
    for fid in sorted(opts, key=lambda t: (str(fname.get(t, t)).lower(), t)):
        seen, dup, blank = {}, [], 0
        for o in opts[fid]:
            n = (o["name"] or "").strip()
            if not n:
                blank += 1
                continue
            k = n.lower()
            if k in seen:
                dup.append(k)
            seen[k] = n
        if blank:
            out.append(_f("option_blank", fname.get(fid, fid), "%d select option(s) have no name, and every option needs one." % blank, temp_id=fid))
        if dup:
            out.append(_f("option_duplicate", fname.get(fid, fid), "%d select option(s) repeat another option's name once letter case is ignored (%s). Planning refuses duplicates, so one must be renamed or merged." % (len(dup), "; ".join(sorted(set(dup))[:5])),
                          temp_id=fid))
    out.sort(key=lambda f: (f["limit"], str(f["scope"]).lower(), f["detail"]))
    return out


CORE_HISTORY_MAX = 200      # the design assumes fewer than 200 historical objects go into Core (owner design, 13 November 2026)


def check_core_history(core_counts, table_names=None):
    """core_counts: {table_id: profiled record count} for the tables that land in Core as well as Planning. The design assumes fewer than 200 historical
    objects are rebuilt in Core, one per record. Returns findings: the total when it is 200 or more (per table, largest first), or a note when nothing was
    profiled, because then the assumption is unchecked."""
    table_names = table_names or {}
    if not core_counts:
        return []
    total = sum(n or 0 for n in core_counts.values())
    if total == 0:
        return [_f("core_history_unchecked", "Core", "No record counts are profiled for the %d table(s) that land in Core, so the assumption of fewer than %d historical objects is not checked yet. Run profiling (Stage 1, step 5)." % (len(core_counts), CORE_HISTORY_MAX))]
    if total < CORE_HISTORY_MAX:
        return []
    rows = sorted(core_counts.items(), key=lambda kv: (-(kv[1] or 0), str(table_names.get(kv[0], kv[0])).lower(), kv[0]))
    return [_f("core_history", "Core", "%s records are profiled across the %d table(s) that land in Core, and the design assumes fewer than %d historical objects. Largest: %s." % (
        format(total, ","), len(core_counts), CORE_HISTORY_MAX, "; ".join("%s (%s)" % (table_names.get(t, t), format(n or 0, ",")) for t, n in rows[:5])), count=total, cap=CORE_HISTORY_MAX)]


def check_record_counts(table_counts, table_names=None):
    """table_counts: {table_id: profiled record count}. A table over the Planning limit per record type. Names only, with counts."""
    table_names = table_names or {}
    return [_f("records", table_names.get(t, t), "%s records, and a record type holds at most %s. Loading would need a split or an archive." % (format(n, ","), format(MAX_RECORDS, ",")), count=n, cap=MAX_RECORDS)
            for t, n in sorted(table_counts.items(), key=lambda kv: (str(table_names.get(kv[0], kv[0])).lower(), kv[0])) if n is not None and n > MAX_RECORDS]
