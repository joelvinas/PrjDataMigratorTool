#!/usr/bin/env python3
"""Field consolidation groups: several Airtable fields of one table that become ONE Workfront field. Standard library only. Pure functions.

A group has a survivor (the field whose name and settings carry forward), the merged members, the reason, and a conflict rule that the loader
will use later when two members are both filled and differ:
  review          a person resolves each such record (the default: nothing is decided silently)
  survivor_wins   keep the survivor's value
  first_filled    use the first non-empty value, in member order (the survivor first)
Merged members drop out of the name review and the duplicate checks; the survivor's description lists every origin.

A group is a dict: {group_id, table_id, survivor_id, member_ids: [survivor first, then the merged ones in order], conflict_rule, reason, decided_by, decided_at}.
"""
import fieldcompare as fc

MAX_MEMBERS = fc.MAX_FIELDS
CONFLICT_RULES = ("review", "survivor_wins", "first_filled")
RULE_TEXT = {"review": "a person resolves each record where they differ",
             "survivor_wins": "the survivor's value is kept where they differ",
             "first_filled": "the first non-empty value is used, survivor first"}


def validate_group(survivor_id, member_ids, fields_by_id, abandoned_ids=(), grouped_ids=(), conflict_rule="review", out_table_ids=()):
    """Check a proposed group and return its member IDs with the survivor first. Raises ValueError with a plain-language reason.
    fields_by_id: {field_id: {field_id, table_id, field_name, ...}} for the live fields of the base."""
    ids = []
    for x in member_ids or []:
        if x not in ids:
            ids.append(x)
    if survivor_id not in ids:
        raise ValueError("The field that survives must be one of the fields being consolidated.")
    if not 2 <= len(ids) <= MAX_MEMBERS:
        raise ValueError("Consolidate between 2 and %d fields." % MAX_MEMBERS)
    if conflict_rule not in CONFLICT_RULES:
        raise ValueError("Unknown conflict rule.")
    for i in ids:
        f = fields_by_id.get(i)
        if not f:
            raise ValueError("One of those fields is not in this base.")
        if i in abandoned_ids:
            raise ValueError("'%s' is abandoned; restore it first, or leave it out." % f["field_name"])
        if i in grouped_ids:
            raise ValueError("'%s' is already in a consolidation group; dissolve that group first." % f["field_name"])
    tables = {fields_by_id[i]["table_id"] for i in ids}
    if len(tables) != 1:
        raise ValueError("Only fields of the same table can be consolidated.")
    if tables & set(out_table_ids):
        raise ValueError("That table is out of scope.")
    return [survivor_id] + [i for i in ids if i != survivor_id]


def merged_ids(groups):
    """{merged field_id: group} for every non-survivor member."""
    return {i: g for g in groups for i in g["member_ids"][1:]}


def survivors(groups):
    """{survivor field_id: group}."""
    return {g["survivor_id"]: g for g in groups}
