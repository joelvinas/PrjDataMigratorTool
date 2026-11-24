#!/usr/bin/env python3
"""Which Airtable fields get their value THROUGH a link, and so are held back by Git-R-Done. Standard library only.

A lookup, a rollup, a count, or a formula that reads a link (or one of those) has no value of its own: Airtable works it out from the records on the other side of a link. Planning
cannot create a lookup through its API (the owner's Postman run, 23 November 2026: HTTP 400, field type invalid), and the owner decided the same day that such a field must never
be loaded as text (a joined list of linked names overflows any text field) and that the old snapshot of a select lookup is replaced by this rule. So the field is SKIPPED when its
table is run, listed with what it reads and the table it comes from, and a person creates the lookup in Planning once the links are set.

Pure functions over the stored schema (field rows as dicts). No record value, no network, deterministic."""

LINK_TYPES = frozenset({"multipleRecordLinks"})
DERIVED_TYPES = frozenset({"multipleLookupValues", "rollup", "count"})
KIND = {"multipleLookupValues": "lookup", "rollup": "rollup", "count": "count", "formula": "formula"}


def _name(fields, fid):
    f = fields.get(fid)
    return f["field_name"] if f else (fid or "?")


def _table_name(fields, tables, table_id):
    return (tables or {}).get(table_id) or next((f["table_name"] for f in fields.values() if f["table_id"] == table_id), table_id or "?")


def hold_reason(field, fields, held, refs=(), tables=None):
    """None when the field is an ordinary field; else a dict describing why it is held back:
    {kind, link_field_id, link_field_name, target_table_id, target_table_name, source_field_id, source_field_name, reads, reason}.
    `fields` is {field_id: row}; `held` is {field_id: info} of the fields of the same table already judged (formulas are judged after the fields they read);
    `refs` are the field IDs a formula reads."""
    t, fid = field["data_type"], field["field_id"]
    if t in DERIVED_TYPES:
        link = fields.get(field.get("source_link_field_id") or "")
        target = (link or {}).get("linked_table_id")
        info = {"kind": KIND[t], "link_field_id": (link or {}).get("field_id"), "link_field_name": _name(fields, (link or {}).get("field_id")) if link else None, "target_table_id": target,
                "target_table_name": _table_name(fields, tables, target) if target else None, "source_field_id": field.get("source_field_id"),
                "source_field_name": _name(fields, field.get("source_field_id")) if field.get("source_field_id") else None, "reads": []}
        via = "through the link field %s to %s" % (info["link_field_name"], info["target_table_name"]) if link and target else "through a link"
        what = {"lookup": "a lookup of %s " % (info["source_field_name"] or "a field"), "rollup": "a rollup of %s " % (info["source_field_name"] or "a field"), "count": "a count of the records "}[info["kind"]]
        info["reason"] = ("held back: it is %s%s. Planning cannot create a lookup through its API and the value is not loaded as text: after the links are set, "
                          "create it in Planning by hand." % (what, via))
        return info
    if t == "formula":
        bad = [r for r in sorted(refs) if (fields.get(r) or {}).get("data_type") in LINK_TYPES | DERIVED_TYPES or r in held]
        if bad:
            first = bad[0]
            nm = _name(fields, first)
            return {"kind": "formula", "link_field_id": first if (fields.get(first) or {}).get("data_type") in LINK_TYPES else None, "link_field_name": None, "target_table_id": None,
                    "target_table_name": None, "source_field_id": None, "source_field_name": None, "reads": [_name(fields, r) for r in bad],
                    "reason": "held back: this formula reads %s (%s), which comes through a link. It is not loaded as text; rebuild it in Planning once the links and lookups exist."
                              % (nm, "a link field" if (fields.get(first) or {}).get("data_type") in LINK_TYPES else "a field held back for the same reason")}
    return None
