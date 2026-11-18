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

import datatypes as dt
import fielddesc as fd
import planlimits as pl
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

NO_PLANNING_TYPE = "no Workfront Planning data type is set for this field (Data types page)"
NO_PLANNING_TYPE_INVALID = "the stored Planning data type is not one of Adobe's 17 types: clear it and set one (Data types page)"
NO_CORE_TYPE = "no Workfront Core data type and display type are set for this field (Data types page)"
NO_CORE_TYPE_INVALID = "the stored Core data type and display type are not a valid pair: clear them and set both (Data types page)"
LINK_UNDECIDED = "the direction of this link is not confirmed (Stage 1, step 4, link directions): the schema cannot be built until a person decides"

# Fields the migration itself adds so a Core object and its Planning record can find each other (from the Migration Design). The names are
# checked against the same naming rules as every other name.
CORE_LINK_FIELDS = [
    ("planning", "core-object-type", "Core object type", "single-select", ["Project", "Request", "Task"], "Which kind of Core object this record is linked to."),
    ("planning", "core-object-id", "Core object ID", "text", [], "The GUID of the Core object (a 32-character text value, not a number)."),
    ("planning", "core-object-link", "Core object link", "text", [], "The full URL to the Core object in Workfront, as plain text."),
    ("core", "planning-record-id", "Planning record ID", ("TEXT", "TEXT"), [], "The Planning record's ID: write-once, set only through the API."),
    ("core", "sync-status", "Sync status", ("TEXT", "SLCT"), ["Not published", "Published", "Failed"], "Whether this object has been published to Planning."),
]


def _item(temp_id, system, kind, name, status, reasons=(), parent=None, table_id=None, field_id=None, label=None, data_type=None, position=None,
          description=None, source="airtable", group=None, spec=None, requires=None, pass_name=None, display_type=None):
    return OrderedDict([("temp_id", temp_id), ("system", system), ("kind", kind), ("parent", parent), ("table_id", table_id), ("field_id", field_id), ("name", name),
                        ("label", label), ("data_type", data_type), ("display_type", display_type), ("position", position), ("status", status), ("reasons", list(reasons)), ("description", description),
                        ("source", source), ("group", group), ("spec", spec), ("requires", list(requires or [])), ("pass", pass_name)])


def _approved(row, target):
    r = (row.get("targets") or {}).get(target) or {}
    return r.get("approved_name") if r.get("status") == "approved" and r.get("approved_name") else None


def _rank(item):
    return (1 if item.get("pass") == "links" else 0, KIND_RANK[item["kind"]])      # links are the SECOND PASS: every record type and ordinary field comes first


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


def restrict_items(items, only_tables):
    """A RUN FILTER, never a decision: keep the items of the chosen tables, plus the record type (and nothing else) of any table a ready link field of a chosen table
    points to, marked dependency=True so the field can be created. The scope decisions are not touched. Order is unchanged, so the result is deterministic."""
    only = set(only_tables)
    kept = [i for i in items if i["table_id"] in only]
    need = {r for i in kept if i["status"] == "ready" for r in i["requires"]}
    deps = []
    for i in items:
        if i["table_id"] not in only and i["kind"] == "record_type" and i["system"] == "planning" and i["temp_id"] in need:
            i["dependency"] = True
            deps.append(i)
    keep_ids = {i["temp_id"] for i in kept} | {i["temp_id"] for i in deps}
    return [i for i in items if i["temp_id"] in keep_ids]


def unknown_tables(rv, only_tables):
    """The chosen table IDs that are not in the base or are out of scope (so they have no items): sorted, for a plain refusal."""
    live = {t["table_id"] for t in rv["tables"] if t["scope"] != "out"}
    return sorted(set(only_tables) - live)


def build_plan(rv, choices=None, include_link_fields=True, link_state=None, record_counts=None, field_facts=None, only_tables=None):
    """rv: the review payload from fieldnames_store.review(). choices: {field_id: [{id, name}, ...]} select options in Airtable order.
    field_facts: {field_id: {precision, currency_symbol, linked_table_id, single}} read from the schema; a Planning link whose direction is confirmed is created in the
    SECOND PASS (status ready, after every record type) only when its facts and its linked table's record type are in the plan; otherwise it stays on hold.
    record_counts: {table_id: profiled record count}, for the 25,000-record and the Core-history checks (None = not checked).
    link_state: {link field_id: True when a person confirmed its direction}; a link field whose direction is not confirmed is BLOCKED (None = not checked).
    only_tables: limit the plan to these table IDs (a run filter; see restrict_items). None = every table.
    Returns {items, summary}. Items are ordered so every parent comes before its children."""
    choices = choices or {}
    lset = label_settings(rv.get("settings"))
    items, out_of_scope = [], 0
    facts = field_facts or {}
    in_plan = {t["table_id"] for t in rv["tables"] if t["scope"] != "out"}
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
            typ = f["data_type"]
            planning_type = dt.planning_type(f.get("target_type")) if dt.state("planning", f.get("target_type")) == "set" else None     # a stored value off the list is not used
            core_data, core_display = (f.get("core_target_type"), f.get("core_display_type")) if dt.state("core", f.get("core_target_type"), f.get("core_display_type")) == "set" else (None, None)
            hold = "derived" if typ in DERIVED_TYPES else "link" if typ in LINK_TYPES else "attachment" if typ in ATTACHMENT_TYPES else None
            ff = facts.get(f["field_id"]) or {}
            second_pass = (hold == "link" and link_state is not None and link_state.get(f["field_id"]) is True and ff.get("linked_table_id") in in_plan)
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
                is_link2 = second_pass and system == "planning"        # Core has no link field type: a link stays on hold there
                if not table_ok:
                    rs.append("the table is not ready (see its record type)")
                if hold and not is_link2:
                    status = "hold"
                    rs.append(HOLD_REASON[hold])
                    if hold == "link" and link_state is not None and link_state.get(f["field_id"]) is False:
                        status = "blocked"
                        rs.append(LINK_UNDECIDED)
                else:
                    status = "ready"
                    if not nm:
                        status = "blocked"
                        rs.append(why_name)
                    if system == "planning" and not planning_type:
                        status = "blocked"
                        rs.append(NO_PLANNING_TYPE_INVALID if f.get("target_type") else NO_PLANNING_TYPE)
                    if system == "core" and not core_data:
                        status = "blocked"
                        rs.append(NO_CORE_TYPE_INVALID if (f.get("core_target_type") or f.get("core_display_type")) else NO_CORE_TYPE)
                if not table_ok and status == "ready":
                    status = "blocked"
                fid = ("P-F-" if system == "planning" else "C-F-") + f["field_id"]
                spec = {"airtable_type": typ, "precision": ff.get("precision"), "currency_symbol": ff.get("currency_symbol"), "linked_table_id": ff.get("linked_table_id"), "single": bool(ff.get("single"))} if ff else None
                items.append(_item(fid, system, "field", nm or f["original"], status, rs, parent=parent, table_id=tid, field_id=f["field_id"], label=lb,
                                   data_type=planning_type if system == "planning" else core_data, display_type=None if system == "planning" else core_display,
                                   description=(f.get("description") or {}).get("text"), group=("C-T-" + tid if system == "core" else None), spec=spec,
                                   requires=["P-T-" + ff["linked_table_id"]] if is_link2 else None, pass_name="links" if is_link2 else None))
                if typ in SELECT_TYPES:
                    for pos, ch in enumerate(choices.get(f["field_id"]) or []):
                        ostatus, orx = option_check(ch.get("name"), status, lset)
                        items.append(_item("%s-O-%s-%s" % (fid[0], f["field_id"], ch.get("id")), system, "option", ch.get("name"), ostatus, list(rs) + orx, parent=fid, table_id=tid,
                                           field_id=f["field_id"], position=pos))
        if both and include_link_fields:
            for system, slug, nm, dtype, opts, desc in CORE_LINK_FIELDS:
                tmp = "S-%s-%s-%s" % ("P" if system == "planning" else "C", tid, slug)
                ptype, ctype = (dtype, None) if system == "planning" else (dtype[0], dtype[1])
                bad = fn.violations(nm, "planning_name" if system == "planning" else "core_name", fn.Settings())
                rs = (["the table is not ready (see its record type)"] if not table_ok else []) + (["the name breaks the naming rules: %s" % ", ".join(bad)] if bad else [])
                items.append(_item(tmp, system, "field", nm, "ready" if not rs else "blocked", rs, parent=("P-T-" + tid) if system == "planning" else None, table_id=tid,
                                   label=nm if system == "core" else None, data_type=ptype, display_type=ctype, source="migration", group=("C-T-" + tid) if system == "core" else None,
                                   description=fd.format_lineage("Migration", [(t["old"], nm)], desc)))
                for pos, o in enumerate(opts):
                    items.append(_item(tmp + "-O-%d" % pos, system, "option", o, "ready" if not rs else "blocked", rs, parent=tmp, table_id=tid, position=pos, source="migration"))
    if only_tables is not None:
        items = restrict_items(items, only_tables)
    chosen = {}
    for i in items:
        if i["kind"] == "option" and i["status"] == "ready":
            chosen.setdefault(i["parent"], []).append(i["name"])          # in Airtable's order; an option on hold is not sent with its field
    for i in items:
        if i["kind"] == "field" and (i.get("display_type") in ("SLCT", "MULT") if i["system"] == "core" else i["data_type"] in ("single-select", "multi-select")):
            i["spec"] = dict(i["spec"] or {}, options=chosen.get(i["temp_id"], []))
    items.sort(key=_rank)                                   # parents before children; the sort is stable, so the order inside a rank is unchanged
    summ = summarize(items, out_of_scope)
    if only_tables is not None:
        summ["only_tables"] = sorted(set(only_tables))
        summ["dependency_tables"] = sorted(i["table_id"] for i in items if i.get("dependency"))
    names = {t["table_id"]: t["old"] for t in rv["tables"]}
    found = pl.check(items, {f["field_id"]: f["data_type"] for f in rv["fields"]})
    if record_counts is not None:
        found += pl.check_record_counts({t: n for t, n in record_counts.items() if t in names and any(t == i["table_id"] and i["kind"] == "record_type" and i["system"] == "planning" and i["status"] != "blocked" for i in items)}, names)
        found += pl.check_core_history({t["table_id"]: record_counts.get(t["table_id"], 0) for t in rv["tables"] if t.get("target") == "planning_and_core" and t["scope"] != "out"
                                        and (only_tables is None or t["table_id"] in only_tables)}, names)
    found.sort(key=lambda f: (f["limit"], str(f["scope"]).lower(), f["detail"]))
    summ["limit_findings"] = found
    return {"items": items, "summary": summ}


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
