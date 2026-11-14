#!/usr/bin/env python3
"""The load plan: what the migration would create in Workfront, derived from the approved names. Standard library only. Pure and deterministic.

The plan is a flat, ordered list of ITEMS. Each item is a record type, a Core custom form, a field or a select option that would be created in
Workfront Planning or Workfront Core. Every item has a TEMPORARY ID that depends only on the Airtable IDs, so it is the same on every run: when a
Workfront ID is written back against a temporary ID, a restart skips what already exists and never creates a duplicate.

An item's status says whether it can be created now:
  ready    everything needed is decided and approved
  hold     a design decision is still open (a derived field, a relationship, an attachment, the Core object type); the reason says which
  blocked  something this tool controls is missing (a table or name not approved, no target data type, scope or target not decided)
Nothing here talks to Workfront. The plan says WHAT, never HOW: the request shapes for each system are added when they are verified.

A Planning field belongs to its record type and needs it first (it is the field's parent). A Core custom field exists on its own: it has no
parent, and "group" names the Core form it will be attached to in a later step.

Temporary IDs:  P-T-<table>  Planning record type      P-F-<field>  Planning field      P-O-<field>-<choice>  Planning select option
                C-T-<table>  Core custom form          C-F-<field>  Core custom field   C-O-<field>-<choice>  Core select option
                S-P-<table>-<slug> / S-C-<table>-<slug>  fields the migration itself adds for the Core-to-Planning link
"""
from collections import Counter, OrderedDict

import fielddesc as fd
import fieldnames as fn

SYSTEMS = ("planning", "core")
KIND_RANK = {"record_type": 0, "form": 0, "field": 1, "option": 2}
DERIVED_TYPES = frozenset({"formula", "multipleLookupValues", "rollup", "count", "autoNumber", "createdTime", "lastModifiedTime", "createdBy",
                           "lastModifiedBy", "externalSyncSource"})
LINK_TYPES = frozenset({"multipleRecordLinks"})
ATTACHMENT_TYPES = frozenset({"multipleAttachments"})
SELECT_TYPES = frozenset({"singleSelect", "multipleSelects"})
HOLD_REASON = {
    "derived": "a derived field (formula, lookup, rollup, count, automatic number or date): decide whether to drop it, rebuild it or load its values",
    "link": "a link to another table: relationships are created in a second pass, and which side of a two-way pair becomes the real relationship is not decided",
    "attachment": "attachments move as Core documents on the new objects, which is not decided or built yet",
}

# Fields the migration itself adds so a Core object and its Planning record can find each other (from the Migration Design). The names are
# checked against the same naming rules as every other name.
CORE_LINK_FIELDS = [
    ("planning", "core-object-type", "Core object type", "Single select", ["Project", "Request", "Task"], "Which kind of Core object this record is linked to."),
    ("planning", "core-object-id", "Core object ID", "Single-line text", [], "The GUID of the Core object (a 32-character text value, not a number)."),
    ("planning", "core-object-link", "Core object link", "Single-line text", [], "The full URL to the Core object in Workfront, as plain text."),
    ("core", "planning-record-id", "Planning record ID", "Single-line text", [], "The Planning record's ID: write-once, set only through the API."),
    ("core", "sync-status", "Sync status", "Single select", ["Not published", "Published", "Failed"], "Whether this object has been published to Planning."),
]


def _item(temp_id, system, kind, name, status, reasons=(), parent=None, table_id=None, field_id=None, label=None, data_type=None, position=None,
          description=None, source="airtable", group=None):
    return OrderedDict([("temp_id", temp_id), ("system", system), ("kind", kind), ("parent", parent), ("table_id", table_id), ("field_id", field_id), ("name", name),
                        ("label", label), ("data_type", data_type), ("position", position), ("status", status), ("reasons", list(reasons)), ("description", description),
                        ("source", source), ("group", group)])


def _approved(row, target):
    r = (row.get("targets") or {}).get(target) or {}
    return r.get("approved_name") if r.get("status") == "approved" and r.get("approved_name") else None


def _rank(item):
    return KIND_RANK[item["kind"]]


def label_settings(d):
    """The naming settings in force for the base (so the emoji choice is honoured); the defaults when they are missing or invalid."""
    known = {"table_swap", "char_cleanup", "leading_digits", "table_prefix", "digit_style", "digit_word", "label_keep_quarter", "targets", "description_include_airtable",
             "emoji_mode", "description_emoji_mode"}
    try:
        return fn.Settings(**{k: v for k, v in (d or {}).items() if k in known})
    except (ValueError, TypeError):
        return fn.Settings()


def option_check(name, status, settings):
    """A select option's name follows the Core LABEL rules (owner decision, 14 November 2026): it keeps the original text except Adobe's unsupported
    characters, emoji and non-ASCII characters. A name that breaks them is put on hold with the proposed repair, because nothing renames on its
    own and an approved option name is not stored yet. Returns (status, reasons)."""
    name = name or ""
    bad = fn.violations(name, "core_label", settings)
    if not bad:
        return status, []
    fixed = fn.clean_label_chars(name, settings.opt("core_label", "extra_blocked"), settings.opt("core_label", "emoji_mode"))
    why = "the option name breaks the label rules (%s); proposed: %s; a person must approve the change (option name decisions are not stored yet)" % (", ".join(bad), repr(fixed))
    return ("hold" if status in ("ready", "hold") else status), [why]


def build_plan(rv, choices=None, include_link_fields=True):
    """rv: the review payload from fieldnames_store.review(). choices: {field_id: [{id, name}, ...]} select options in Airtable order.
    Returns {items, summary}. Items are ordered so every parent comes before its children."""
    choices = choices or {}
    lset = label_settings(rv.get("settings"))
    items, out_of_scope = [], 0
    by_table = {}
    for f in rv["fields"]:
        by_table.setdefault(f["table_id"], []).append(f)
    for t in rv["tables"]:
        tid = t["table_id"]
        if t["scope"] == "out":
            out_of_scope += 1
            continue
        reasons = []
        if t["scope"] != "in":
            reasons.append("the table's scope is not decided (it is neither in nor out)")
        if not t.get("target"):
            reasons.append("it is not decided whether the table lands in Planning only or in Planning plus Core")
        if not t["approved"]:
            reasons.append("the table name is not approved")
        name = t["effective"] if t["approved"] else t["old"]
        table_ok = not reasons
        both = t.get("target") == "planning_and_core"
        items.append(_item("P-T-" + tid, "planning", "record_type", name, "ready" if table_ok else "blocked", reasons, table_id=tid, label=t["old"],
                           description="Source: Airtable. Origins: %s." % fd.quote_part(t["old"])))
        if both:
            items.append(_item("C-T-" + tid, "core", "form", name, "hold" if table_ok else "blocked",
                               (["the Core object type (project, request or task) this table becomes is not decided"] if table_ok else reasons), table_id=tid, label=t["old"]))
        for f in by_table.get(tid, []):
            if f["abandoned"] or f.get("consolidated"):
                continue
            typ, target_type = f["data_type"], f.get("target_type")
            hold = "derived" if typ in DERIVED_TYPES else "link" if typ in LINK_TYPES else "attachment" if typ in ATTACHMENT_TYPES else None
            for system in SYSTEMS if both else ("planning",):
                if system == "planning":
                    nm, lb, parent = _approved(f, "planning_name"), None, "P-T-" + tid
                    why_name = "the Planning name is not approved"
                else:
                    nm, lb, parent = _approved(f, "core_name"), _approved(f, "core_label"), None     # a Core custom field exists on its own; it is attached to its form later
                    why_name = "the Core name and label are not both approved"
                    if nm and not lb:
                        nm = None
                rs = []
                if not table_ok:
                    rs.append("the table is not ready (see its record type)")
                if hold:
                    status = "hold"
                    rs.append(HOLD_REASON[hold])
                else:
                    status = "ready"
                    if not nm:
                        status = "blocked"
                        rs.append(why_name)
                    if not target_type:
                        status = "blocked"
                        rs.append("no target data type (new_dataType) was imported for this field")
                if not table_ok and status == "ready":
                    status = "blocked"
                fid = ("P-F-" if system == "planning" else "C-F-") + f["field_id"]
                items.append(_item(fid, system, "field", nm or f["original"], status, rs, parent=parent, table_id=tid, field_id=f["field_id"], label=lb, data_type=target_type,
                                   description=(f.get("description") or {}).get("text"), group=("C-T-" + tid if system == "core" else None)))
                if typ in SELECT_TYPES:
                    for pos, ch in enumerate(choices.get(f["field_id"]) or []):
                        ostatus, orx = option_check(ch.get("name"), status, lset)
                        items.append(_item("%s-O-%s-%s" % (fid[0], f["field_id"], ch.get("id")), system, "option", ch.get("name"), ostatus, list(rs) + orx, parent=fid, table_id=tid,
                                           field_id=f["field_id"], position=pos))
        if both and include_link_fields:
            for system, slug, nm, dtype, opts, desc in CORE_LINK_FIELDS:
                tmp = "S-%s-%s-%s" % ("P" if system == "planning" else "C", tid, slug)
                bad = fn.violations(nm, "planning_name" if system == "planning" else "core_name", fn.Settings())
                rs = (["the table is not ready (see its record type)"] if not table_ok else []) + (["the name breaks the naming rules: %s" % ", ".join(bad)] if bad else [])
                items.append(_item(tmp, system, "field", nm, "ready" if not rs else "blocked", rs, parent=("P-T-" + tid) if system == "planning" else None, table_id=tid,
                                   label=nm if system == "core" else None, data_type=dtype, source="migration", group=("C-T-" + tid) if system == "core" else None,
                                   description=fd.format_lineage("Migration", [(t["old"], nm)], desc)))
                for pos, o in enumerate(opts):
                    items.append(_item(tmp + "-O-%d" % pos, system, "option", o, "ready" if not rs else "blocked", rs, parent=tmp, table_id=tid, position=pos, source="migration"))
    items.sort(key=_rank)                                   # parents before children; the sort is stable, so the order inside a rank is unchanged
    return {"items": items, "summary": summarize(items, out_of_scope)}


def summarize(items, out_of_scope=0):
    by_status = Counter(i["status"] for i in items)
    by = Counter((i["system"], i["kind"], i["status"]) for i in items)
    reasons = Counter(r for i in items if i["status"] != "ready" for r in i["reasons"])
    return {"total": len(items), "by_status": dict(by_status), "by_system_kind_status": {"%s/%s/%s" % k: v for k, v in sorted(by.items())},
            "reasons": dict(reasons.most_common()), "out_of_scope_tables": out_of_scope}


def children_of(items):
    """{parent temp_id: [children]} in plan order."""
    out = {}
    for i in items:
        if i["parent"]:
            out.setdefault(i["parent"], []).append(i)
    return out
