#!/usr/bin/env python3
"""Select-value inventory: the values that really sit in the records of a single or multiple select field, compared with the options the schema
declares. Standard library only. Pure and deterministic.

Why: a select field can hold a value that is not in its declared option list (an option removed after records used it, a name that differs only
by case or spacing, data typed in through an import). Loading such a value into Workfront needs a decision, so the difference is found BEFORE any
push. The declared options come from the schema (metadata). The values come from the saved record file (record data): they are read into memory
here and thrown away. What this module returns is COUNTS, record IDs and the names of DECLARED options only; a value that is not a declared option
is described (how many records hold it, which declared option it nearly matches) but its text is never returned, so it cannot reach the browser,
the database, a log or a report.

A value is matched to an option the way Airtable does: by exact name. A value that matches no option exactly but matches one apart from case,
surrounding spaces or repeated spaces is a 'near match' (the likely fix is to point it at that option, which a person confirms).
"""
import re

SELECT_TYPES = frozenset({"singleSelect", "multipleSelects"})
EXAMPLE_CAP = 5            # record IDs shown per unmatched value, so a record can be found in Airtable


def _loose(s):
    return re.sub(r"\s+", " ", str(s)).strip().casefold()


def values_of(v):
    """The select values in one cell: a single select holds a name, a multiple select a list of names. Empty cells give nothing."""
    if v is None or v == "" or v == []:
        return []
    if isinstance(v, list):
        return [x for x in v if x is not None and x != ""]
    return [v]


def inventory(records, fields):
    """records: [{id, fields: {field_id: value}}]. fields: [{field_id, data_type}] (only select fields are read).
    Returns {field_id: {"values": {value: {"records": n, "examples": [record ids]}}, "filled": records with a value, "total": records}}.
    The values stay inside the returned dict, which the caller must not send anywhere (diff() reduces it to counts)."""
    sel = [f["field_id"] for f in fields if f["data_type"] in SELECT_TYPES]
    out = {fid: {"values": {}, "filled": 0, "total": 0} for fid in sel}
    for rec in records:
        cells = rec.get("fields") or {}
        rid = rec.get("id")
        for fid in sel:
            o = out[fid]
            o["total"] += 1
            vals = values_of(cells.get(fid))
            if vals:
                o["filled"] += 1
            for val in {x if isinstance(x, str) else repr(x) for x in vals}:        # a value listed twice in one cell counts once
                e = o["values"].setdefault(val, {"records": 0, "examples": []})
                e["records"] += 1
                if len(e["examples"]) < EXAMPLE_CAP:
                    e["examples"].append(rid)
    return out


def diff(inv, choices):
    """inv: one field's entry from inventory(). choices: the declared options [{id, name}] in Airtable's order (airtable_fields.choices_json).
    Returns counts only (see the module note)."""
    declared = [c.get("name") for c in choices or [] if isinstance(c, dict)]
    by_name = {}
    for c in choices or []:
        if isinstance(c, dict) and c.get("name") is not None:
            by_name.setdefault(c["name"], c)
    loose_to = {}
    for c in choices or []:
        if isinstance(c, dict) and c.get("name") is not None:
            loose_to.setdefault(_loose(c["name"]), c)
    matched, used, unmatched = 0, set(), []
    for val, e in inv["values"].items():
        if val in by_name:
            matched += 1
            used.add(val)
            continue
        near = loose_to.get(_loose(val))
        unmatched.append({"records": e["records"], "examples": list(e["examples"]),
                          "near_match_option": near.get("name") if near else None, "near_match_option_id": near.get("id") if near else None})
    unmatched.sort(key=lambda u: (-u["records"], u["examples"][:1]))           # deterministic; never sorted by the text itself
    for i, u in enumerate(unmatched, 1):
        u["label"] = "value %d" % i
    return {"declared_options": len(declared), "observed_values": len(inv["values"]), "matched_values": matched,
            "unmatched_values": len(unmatched), "unmatched_records": sum(u["records"] for u in unmatched),
            "near_matches": sum(1 for u in unmatched if u["near_match_option"] is not None),
            "unused_options": [n for n in declared if n not in used], "unmatched": unmatched,
            "records_with_value": inv["filled"], "records_total": inv["total"]}


def table_report(records, fields, choices_by_field):
    """The diff for every select field of one table. fields: [{field_id, field_name, data_type}]. Fields in field_id order."""
    inv = inventory(records, fields)
    out = []
    for f in sorted((f for f in fields if f["data_type"] in SELECT_TYPES), key=lambda f: f["field_id"]):
        d = diff(inv[f["field_id"]], choices_by_field.get(f["field_id"]))
        d.update(field_id=f["field_id"], field_name=f["field_name"], data_type=f["data_type"])
        out.append(d)
    return out
