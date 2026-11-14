#!/usr/bin/env python3
"""Field-writer matrix and the Fusion-driven marker. Standard library only; schema and recorded-spec metadata only (no record value is read).

THE MATRIX records, for each field, who fills its values day to day: a user typing, a form, an automation, or a formula. This is not who created
the field. It is built from what Stages 2 and 3 captured (the latest saved version of each form and automation) and from the field's own type.
What a captured spec cannot tell is whether a PERSON also types into a field, so a user is only ever 'possible', never confirmed from the specs.

THE MARKER is binary, because Fusion is the only automation engine in the target: a field carries the Fusion-driven marker if and only if a captured
automation SETS it. The marker is the first thing in the field's description, so a long lineage text can never push it past a length limit:

    Fusion-driven: written by scenario "Order sync"; automation "Late fee" (scenario not named yet).

THE OUTCOME for a field drives a decision a person records (the tool proposes, a person decides):
    formula_only     a formula, lookup, rollup or count, set by no automation: recompute it in the target, or park it
    automation_only  set by an automation and by no form: it becomes Fusion-owned in Workfront, its values go stale, or people also edit it
    mixed            set by an automation and also by a form: marker only, nothing to decide
    user_plain       typed by users or by a form: plain data that just migrates, no marker
    system           filled by Airtable itself (auto number, created or modified time or user): nothing to decide, no marker

THE VALIDATION compares the matrix with the descriptions pulled from Workfront Core and Planning, in both directions, so they cannot drift apart:
every field a captured automation sets must carry the marker, and every marked field must trace to a captured automation.
"""
import re

COMPUTED_TYPES = frozenset({"formula", "rollup", "count", "multipleLookupValues"})
SYSTEM_TYPES = frozenset({"autoNumber", "createdTime", "lastModifiedTime", "createdBy", "lastModifiedBy", "externalSyncSource"})
MARKER_PREFIX = "Fusion-driven:"
CATEGORIES = ("formula_only", "automation_only", "mixed", "user_plain", "system")
# The decisions a person can record, per category that needs one.
DECISIONS = {"formula_only": ("recompute", "park"), "automation_only": ("fusion_owned", "go_stale", "users_also_edit")}
DECISION_LABEL = {"recompute": "Recompute it in the target", "park": "Park it (do not rebuild)", "fusion_owned": "Fusion-owned in Workfront",
                  "go_stale": "Values go stale", "users_also_edit": "People also edit it (marker only)"}
_ITEM = re.compile(r'(scenario|automation) "([^"]*)"')


def clean_name(s):
    """A name made safe for the marker: control characters gone, double quotes turned into single quotes, spaces closed."""
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", str(s or "")).replace('"', "'")).strip()


def marker_text(sources):
    """The marker for a field. sources: [{automation_name, scenario}] (scenario None or empty when the Fusion scenario is not named yet).
    A named scenario is shown by its scenario name; otherwise by the automation, flagged. Sorted, no duplicates, so the same input gives the same text."""
    items = sorted({('scenario "%s"' % clean_name(s["scenario"])) if clean_name(s.get("scenario")) else ('automation "%s" (scenario not named yet)' % clean_name(s["automation_name"]))
                    for s in sources})
    if not items:
        return None
    return "%s written by %s." % (MARKER_PREFIX, "; ".join(items))


_MARKER = re.compile(r'Fusion-driven: written by (?:(?:scenario "[^"]*"|automation "[^"]*" \(scenario not named yet\))(?:; )?)+\.')


def parse_marker(description):
    """None when the description has no marker. Else {scenarios, automations, named, well_formed}. The marker must be the first thing in the description.
    A description that starts with the marker prefix but not in the standard form (a hand edit) still counts as marked, with no names."""
    t = str(description or "").lstrip()
    if not t.startswith(MARKER_PREFIX):
        return None
    m = _MARKER.match(t)
    items = _ITEM.findall(m.group(0)) if m else []
    sc = sorted({n for k, n in items if k == "scenario"})
    au = sorted({n for k, n in items if k == "automation"})
    return {"scenarios": sc, "automations": au, "named": bool(sc) and not au, "well_formed": m is not None}


def strip_marker(description):
    """The description without its marker (unchanged when it has none)."""
    t = str(description or "").lstrip()
    if not t.startswith(MARKER_PREFIX):
        return str(description or "")
    m = _MARKER.match(t)
    if m:
        return t[m.end():].lstrip()
    end = t.find(". ")
    return t[end + 2:] if end > 0 else ""


def with_marker(description, sources):
    """The description with the marker in front (replacing an old one), or without any marker when no automation sets the field. Idempotent."""
    base = strip_marker(description)
    m = marker_text(sources)
    return base if not m else (m + (" " + base if base else ""))


def build_matrix(fields, automations, refs, forms=(), form_items=()):
    """fields: airtable_fields rows (dict-like: field_id, field_name, table_id, table_name, base_id, data_type). automations: the LATEST saved version of each
    ({spec_id, automation_id, automation_name, target_scenario_name}). refs: automation_field_refs rows ({spec_id, field_id, role}). forms: the latest
    version of each form ({spec_id, form_id, form_name}). form_items: ({spec_id, airtable_field_id, item_type}). Returns entries sorted by table then field."""
    auto = {a["spec_id"]: a for a in automations}
    written = {}
    for r in refs:
        a = auto.get(r["spec_id"])
        if a is not None and r["role"] == "set":
            written.setdefault(r["field_id"], {})[a["automation_id"]] = {"automation_id": a["automation_id"], "automation_name": a["automation_name"], "scenario": a.get("target_scenario_name") or None}
    fm = {f["spec_id"]: f for f in forms}
    by_form = {}
    for it in form_items:
        f = fm.get(it["spec_id"])
        if f is not None and it["item_type"] in ("field", "hidden_field") and it["airtable_field_id"]:
            by_form.setdefault(it["airtable_field_id"], {})[f["form_id"]] = f["form_name"]
    out = []
    for f in sorted(fields, key=lambda r: (str(r["table_name"]).lower(), r["table_id"], str(r["field_name"]).lower(), r["field_id"])):
        fid, typ = f["field_id"], f["data_type"]
        sources = sorted(written.get(fid, {}).values(), key=lambda s: (s["automation_name"].lower(), s["automation_id"]))
        forms_ = sorted(by_form.get(fid, {}).values(), key=str.lower)
        computed, system = typ in COMPUTED_TYPES, typ in SYSTEM_TYPES
        anomalies = []
        if system:
            cat = "system"
            if sources:
                anomalies.append("an automation sets a field that Airtable fills itself")
        elif computed:
            cat = "formula_only"
            if sources:
                anomalies.append("an automation sets a computed field, which Airtable does not allow: check the recorded automation")
        elif sources:
            cat = "mixed" if forms_ else "automation_only"
        else:
            cat = "user_plain"
        out.append({"field_id": fid, "field_name": f["field_name"], "table_id": f["table_id"], "table_name": f["table_name"], "base_id": f["base_id"], "data_type": typ,
                    "writers": {"user": "n/a" if (computed or system) else "possible", "form": forms_, "automation": sources, "formula": computed, "system": system},
                    "category": cat, "base_category": cat, "fusion": bool(sources), "scenarios": sorted({clean_name(s["scenario"]) for s in sources if clean_name(s["scenario"])}),
                    "marker": marker_text(sources), "needs_decision": cat in DECISIONS, "decision": None, "anomalies": anomalies})
    return out


def apply_decisions(entries, decisions):
    """decisions: {field_id: {decision, decided_by, decided_at, reason}}. Adds each recorded decision to its entry and settles the effective category
    ('users_also_edit' makes an automation-only field mixed). A decision that does not fit the field's category is ignored and flagged, never applied."""
    for e in entries:
        d = decisions.get(e["field_id"])
        if not d:
            continue
        if d["decision"] not in DECISIONS.get(e["category"], ()):
            e["anomalies"].append("a recorded decision (%s) no longer fits this field (%s): decide again" % (d["decision"], e["category"]))
            continue
        e["decision"] = dict(d)
        e["needs_decision"] = False
        if d["decision"] == "users_also_edit":
            e["category"] = "mixed"
    return entries


# ------------------------------------------------------------------ stored decisions (migration 019)
class WriterError(Exception):
    def __init__(self, message, status=400):
        super(WriterError, self).__init__(message)
        self.message, self.status = message, status


def available(conn):
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {"field_writer_decisions", "field_writer_events"} <= have


def read_decisions(conn, base_id):
    """{field_id: {decision, decided_by, decided_at, reason}}. Empty until migration 019 is applied."""
    if not available(conn):
        return {}
    return {r[0]: {"decision": r[1], "decided_by": r[2], "decided_at": r[3], "reason": r[4]}
            for r in conn.execute("SELECT airtable_field_id, decision, decided_by, decided_at, reason FROM field_writer_decisions WHERE base_id=? ORDER BY airtable_field_id", (base_id,))}


def decide(conn, actor, base_id, entry, decision, reason=""):
    """Record a person's decision for one field, with who, when and why, and append it to the history. `entry` is the field's matrix entry BEFORE
    decisions were applied (so its base_category is what the tool proposed). decision 'clear' removes the decision. Returns 'decided', 'changed',
    'unchanged' or 'cleared'. A decision that does not fit the field's category is refused."""
    actor = (actor or "").strip()
    if not actor or len(actor) > 40 or any(ord(c) < 32 for c in actor):
        raise WriterError("Enter your initials or a short name (up to 40 characters) before making a decision.")
    reason = (reason or "").strip() or "set from the page"
    if len(reason) > 500 or any(ord(c) < 32 and c not in "\n\t" for c in reason):
        raise WriterError("The reason must be plain text of up to 500 characters.")
    if not available(conn):
        raise WriterError("Field-writer decisions need migration 019 on this database.", 409)
    fid = entry["field_id"]
    old = conn.execute("SELECT decision FROM field_writer_decisions WHERE airtable_field_id=?", (fid,)).fetchone()
    old = old[0] if old else None
    if decision == "clear":
        if old is None:
            return "unchanged"
        with conn:
            conn.execute("DELETE FROM field_writer_decisions WHERE airtable_field_id=?", (fid,))
            conn.execute("INSERT INTO field_writer_events(airtable_field_id, base_id, event_type, old_decision, new_decision, actor, reason) VALUES (?,?,?,?,?,?,?)", (fid, base_id, "clear", old, None, actor, reason))
        return "cleared"
    allowed = DECISIONS.get(entry["base_category"], ())
    if decision not in allowed:
        raise WriterError("%s is not a decision for a field in the outcome %s%s." % (decision, entry["base_category"].replace("_", " "), ("; choose " + ", ".join(allowed)) if allowed else ": nothing to decide"))
    if old == decision:
        return "unchanged"
    with conn:
        conn.execute("INSERT INTO field_writer_decisions(airtable_field_id, base_id, decision, decided_by, reason) VALUES (?,?,?,?,?) "
                     "ON CONFLICT(airtable_field_id) DO UPDATE SET decision=excluded.decision, decided_by=excluded.decided_by, decided_at=CURRENT_TIMESTAMP, reason=excluded.reason", (fid, base_id, decision, actor, reason))
        conn.execute("INSERT INTO field_writer_events(airtable_field_id, base_id, event_type, old_decision, new_decision, actor, reason) VALUES (?,?,?,?,?,?,?)",
                     (fid, base_id, "change" if old else "decide", old, decision, actor, reason))
    return "changed" if old else "decided"


# ------------------------------------------------------------------ reading what the matrix is built from
_STALE = "(f.updated_at < (SELECT MAX(x.updated_at) FROM airtable_fields x WHERE x.base_id = f.base_id))"


def load_inputs(conn, base_id):
    """(fields, automations, refs, forms, form_items) for one base: its live fields (tables out of scope left out), the LATEST saved version of each
    automation and form, and their field references. Schema and recorded-spec metadata only; an automation's 'set to' detail is never read."""
    out_ids = {r[0] for r in conn.execute("SELECT table_id FROM table_scope WHERE base_id=? AND decision='out'", (base_id,))} \
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='table_scope'").fetchone() else set()
    cols = ["field_id", "field_name", "table_id", "table_name", "base_id", "data_type", "stale"]
    fields = [dict(zip(cols, r)) for r in conn.execute("SELECT f.field_id, f.field_name, f.table_id, f.table_name, f.base_id, f.data_type, %s FROM airtable_fields f WHERE f.base_id=?" % _STALE, (base_id,))]
    fields = [f for f in fields if not f.pop("stale") and f["table_id"] not in out_ids]
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    autos, refs, forms, items = [], [], [], []
    if "automation_specs" in have:
        autos = [dict(zip(("spec_id", "automation_id", "automation_name", "target_scenario_name"), r)) for r in conn.execute(
            "SELECT s.spec_id, s.automation_id, s.automation_name, s.target_scenario_name FROM automation_specs s WHERE s.base_id=? "
            "AND s.version_no = (SELECT MAX(version_no) FROM automation_specs WHERE automation_id = s.automation_id)", (base_id,))]
        ids = [a["spec_id"] for a in autos]
        if ids:
            refs = [dict(zip(("spec_id", "field_id", "role"), r)) for r in conn.execute("SELECT spec_id, field_id, role FROM automation_field_refs WHERE spec_id IN (%s)" % ",".join("?" * len(ids)), ids)]
    if "form_specs" in have:
        forms = [dict(zip(("spec_id", "form_id", "form_name"), r)) for r in conn.execute(
            "SELECT s.spec_id, s.form_id, s.form_name FROM form_specs s WHERE s.base_id=? AND s.version_no = (SELECT MAX(version_no) FROM form_specs WHERE form_id = s.form_id)", (base_id,))]
        fids = [f["spec_id"] for f in forms]
        if fids:
            items = [dict(zip(("spec_id", "airtable_field_id", "item_type"), r)) for r in conn.execute("SELECT spec_id, airtable_field_id, item_type FROM form_items WHERE spec_id IN (%s)" % ",".join("?" * len(fids)), fids)]
    return fields, autos, refs, forms, items


def load_sources(conn, base_id):
    """{field_id: [{automation_id, automation_name, scenario}]} for every field a recorded automation sets: what the marker is made from."""
    return {e["field_id"]: e["writers"]["automation"] for e in build_matrix(*load_inputs(conn, base_id)) if e["fusion"]}


def summary(entries):
    cats = {c: 0 for c in CATEGORIES}
    for e in entries:
        cats[e["category"]] += 1
    return {"fields": len(entries), "categories": cats, "fusion_fields": sum(1 for e in entries if e["fusion"]), "needs_decision": sum(1 for e in entries if e["needs_decision"]),
            "unnamed_scenarios": sum(1 for e in entries if e["fusion"] and not e["scenarios"]), "anomalies": sum(1 for e in entries if e["anomalies"])}


def validate(entries, pulled):
    """Check the matrix against the descriptions pulled from Workfront, in both directions.
    pulled: {system: {airtable_field_id: description}} where system is 'core' or 'planning'; only the systems given are checked. A field of the matrix that
    is missing from a system's pull is 'not_pulled' (counted, not a failure). Returns {findings, counts}; findings are sorted."""
    by_id = {e["field_id"]: e for e in entries}
    findings = []
    for system in sorted(pulled):
        got = pulled[system]
        for e in entries:
            if e["field_id"] not in got:
                findings.append(_f("not_pulled", system, e, "the description was not in the pull"))
                continue
            mk = parse_marker(got[e["field_id"]])
            if e["fusion"] and mk is None:
                findings.append(_f("missing_marker", system, e, "a captured automation sets this field, but its description has no Fusion-driven marker"))
            elif not e["fusion"] and mk is not None:
                findings.append(_f("unexpected_marker", system, e, "the description is marked Fusion-driven, but no captured automation sets this field"))
            elif e["fusion"] and mk is not None:
                want = sorted(e["scenarios"])
                if want and mk["scenarios"] != want:
                    findings.append(_f("scenario_mismatch", system, e, "the marker names %s, the recorded automations name %s" % (mk["scenarios"] or "no scenario", want)))
        for fid in sorted(set(got) - set(by_id)):
            if parse_marker(got[fid]) is not None:
                findings.append({"kind": "unknown_marked_field", "system": system, "field_id": fid, "field_name": None, "table_name": None,
                                 "detail": "a marked field is not in the ingested schema, so it traces to no captured automation"})
    findings.sort(key=lambda f: (f["system"], f["kind"], str(f["table_name"] or "").lower(), str(f["field_name"] or "").lower(), f["field_id"]))
    counts = {}
    for f in findings:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    return {"findings": findings, "counts": counts, "systems": sorted(pulled), "ok": not any(k != "not_pulled" for k in counts)}


def _f(kind, system, e, detail):
    return {"kind": kind, "system": system, "field_id": e["field_id"], "field_name": e["field_name"], "table_name": e["table_name"], "detail": detail}
