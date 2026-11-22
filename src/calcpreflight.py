#!/usr/bin/env python3
"""Preflight for the calculated fields feature (Stage B and C): read the formula fields of one Airtable table, translate each one, check it against the crosswalks, order the
work by dependency and save ONE row per formula per run (tables from migration 027). Standard library only. It reads the database and writes only the calc_* tables. It makes NO
Workfront call and sends nothing: the push is a separate, explicit step that reads the approved snapshot saved here. Metadata only: formula text, expression text, IDs, statuses,
reasons. No record value, no credential. No model, no randomness; the same schema and crosswalk give the same rows.

    run(conn, environment, base_id, table_id, category_id, category_name, actor)   -> run_id     one preflight, saved
    report(conn, run_id)                                                            -> dict         counts per status, translatable and blocked lists, function inventory
    approve(conn, run_id, actor, reason)                                            -> dict         a person approves the run (who, when, why); refused when something drifted
    drift(conn, run_id)                                                             -> [str]        what changed since the snapshot (empty = nothing)

One status per formula, in this order of precedence: the formula is broken or has something with no verified equivalent (BLOCKED_UNSUPPORTED_FUNCTION); it reads a formula that is
itself blocked or part of a loop (BLOCKED_DEPENDENCY); a referenced field has no target in the crosswalk, the formula has no approved Core name, or the target field conflicts with
a field already in Core (BLOCKED_MISSING_REFERENCE; a conflict says "Target field conflict" in the reason, because migration 027 has no fifth status); otherwise TRANSLATABLE.
"Exists in Core" means a row in core_field_crosswalk (made or scanned by the Core fields page) or calc_formula_crosswalk; this module does not ask Workfront.

The displayType of a calculated field is CALC (owner, 22 November 2026: "generally"; provisional until a sandbox test). It is always CALC here: a displayType a person chose on the Data
types page is for ordinary fields, and CALC is not on that list. The dataType comes from the formula's result_type through RESULT_TYPES unless a person set a Core type.
"""
import hashlib
import json
import re
from datetime import datetime, timezone

import calclex as cl
import calcxlate as cx
import loadconfig as lc

ENVIRONMENTS = lc.ENVIRONMENTS
CATEGORY_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")
ACTOR_MAX, REASON_MAX = 40, 500
TABLES = ("calc_preflight_runs", "calc_preflight_items", "calc_formula_crosswalk", "calc_push_items", "calc_push_log")
# Airtable result type of a formula -> Core dataType (CURC, DATE, DTTM, NMBR, RICH, TEXT). Provisional until tested with a calculated field in a sandbox.
RESULT_TYPES = {"number": "NMBR", "percent": "NMBR", "rating": "NMBR", "duration": "NMBR", "currency": "CURC", "singleLineText": "TEXT", "multilineText": "TEXT", "email": "TEXT",
                "url": "TEXT", "phoneNumber": "TEXT", "singleSelect": "TEXT", "date": "DATE", "dateTime": "DTTM"}
CALC_DISPLAY_TYPE = "CALC"   # the displayType of a calculated field (owner, 22 November 2026: "generally CALC"); provisional until a sandbox test
CONFLICT = "Target field conflict: "


class PreflightError(Exception):
    def __init__(self, message, status=400):
        Exception.__init__(self, message)
        self.message, self.status = message, status


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _has_tables(conn):
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return all(t in have for t in TABLES)


def _marks(n):
    return ",".join("?" * n) if n else "NULL"


def _clean(text, limit, what, required=True):
    text = (text or "").strip()
    if required and not text:
        raise PreflightError("%s is required." % what)
    if len(text) > limit or any(ord(c) < 32 and c not in "\n\t" for c in text):
        raise PreflightError("%s must be plain text of up to %d characters." % (what, limit))
    return text


def _j(items):
    return json.dumps(sorted(items))


# ------------------------------------------------------------------ fingerprints (drift detection)
def fingerprints(conn, environment, category_id, formula_ids, referenced_ids, target_names):
    """(schema_hash, crosswalk_hash). The schema hash covers the formula fields' IDs, formula text and result types. The crosswalk hash covers the rows of core_field_crosswalk and
    calc_formula_crosswalk that matter to this run (the formulas, what they read, the target names, this form); the written expression and the CTGYPA ID are left out, because the
    push changes them."""
    fids = sorted(set(formula_ids))
    rows = conn.execute("SELECT field_id, data_type, formula_text, result_type FROM airtable_fields WHERE field_id IN (%s) ORDER BY field_id" % _marks(len(fids)), fids).fetchall()
    schema = hashlib.sha256(json.dumps([list(r) for r in rows]).encode("utf-8")).hexdigest()
    ids, names = sorted(set(formula_ids) | set(referenced_ids)), sorted(set(n for n in target_names if n))
    core = conn.execute("SELECT airtable_field_id, workfront_name, parameter_id, workfront_data_type, workfront_display_type FROM core_field_crosswalk WHERE environment=? AND "
                        "(airtable_field_id IN (%s) OR workfront_name IN (%s)) ORDER BY airtable_field_id, workfront_name, parameter_id" % (_marks(len(ids)), _marks(len(names))),
                        [environment] + ids + names).fetchall()
    calc = conn.execute("SELECT category_id, parameter_id, airtable_field_id, workfront_name FROM calc_formula_crosswalk WHERE environment=? AND "
                        "(category_id=? OR airtable_field_id IN (%s) OR workfront_name IN (%s)) ORDER BY category_id, parameter_id, airtable_field_id" % (_marks(len(ids)), _marks(len(names))),
                        [environment, category_id] + ids + names).fetchall()
    cross = hashlib.sha256(json.dumps([[list(r) for r in core], [list(r) for r in calc]]).encode("utf-8")).hexdigest()
    return schema, cross


# ------------------------------------------------------------------ the preflight
def _load_formulas(conn, base_id, table_id):
    return [dict(zip(("field_id", "field_name", "formula_text", "result_type", "is_valid", "core_name", "core_name_status", "core_label", "core_target_type", "core_display_type"), r))
            for r in conn.execute(
                "SELECT f.field_id, f.field_name, f.formula_text, f.result_type, f.is_valid, n.core_name, n.core_name_status, n.core_label, n.core_target_type, n.core_display_type "
                "FROM airtable_fields f LEFT JOIN field_names n ON n.airtable_field_id = f.field_id "
                "WHERE f.base_id=? AND f.table_id=? AND f.data_type='formula' AND COALESCE(n.abandoned, 0)=0 ORDER BY f.field_id", (base_id, table_id))]


def _known_names(conn, environment):
    """{airtable field ID: set of Core API names} from the two crosswalks (a field with a target in Core)."""
    known = {}
    for sql in ("SELECT airtable_field_id, workfront_name FROM core_field_crosswalk WHERE environment=?", "SELECT airtable_field_id, workfront_name FROM calc_formula_crosswalk WHERE environment=?"):
        for fid, name in conn.execute(sql, (environment,)):
            known.setdefault(fid, set()).add(name)
    return known


def _params_by_name(conn, environment, name):
    """[(parameter_id, data_type or None)] for Core fields already called `name`, from both crosswalks."""
    out = {}
    for pid, dtype in conn.execute("SELECT parameter_id, workfront_data_type FROM core_field_crosswalk WHERE environment=? AND workfront_name=?", (environment, name)):
        out[pid] = dtype
    for (pid,) in conn.execute("SELECT parameter_id FROM calc_formula_crosswalk WHERE environment=? AND workfront_name=?", (environment, name)):
        out.setdefault(pid, None)
    return sorted(out.items())


def run(conn, environment, base_id, table_id, category_id, category_name, actor, now=None):
    if not _has_tables(conn):
        raise PreflightError("The calculated fields tables are not in this database yet (migration 027).", 409)
    if environment not in ENVIRONMENTS:
        raise PreflightError("Choose one of the Core environments: %s." % ", ".join(ENVIRONMENTS))
    if not CATEGORY_RE.match(category_id or ""):
        raise PreflightError("Choose the custom form (category) the fields go on.")
    actor = _clean(actor, ACTOR_MAX, "Your initials")
    forms = _load_formulas(conn, base_id, table_id)
    if not forms:
        raise PreflightError("That table has no formula fields to translate.", 404)
    by_id = {f["field_id"]: f for f in forms}
    known = _known_names(conn, environment)
    ambiguous = {k for k, v in known.items() if len(v) > 1}

    for f in forms:   # the Core name of each formula: the approved name, else the one name the crosswalk already holds for it
        if f["core_name"] and f["core_name_status"] == "approved":
            f["target"] = f["core_name"]
        elif len(known.get(f["field_id"], ())) == 1:
            f["target"] = sorted(known[f["field_id"]])[0]
        else:
            f["target"] = None

    def resolve(fid):
        if fid in by_id:
            return by_id[fid]["target"]
        names = known.get(fid, ())
        return sorted(names)[0] if len(names) == 1 else None

    for f in forms:
        f["tr"] = tr = cx.translate(f["formula_text"], resolve)
        f["deps"] = sorted(set(tr.references) & set(by_id) - {f["field_id"]})
        if f["field_id"] in tr.references:
            f["deps"] = sorted(set(f["deps"]) | {f["field_id"]})   # reads itself: a loop
    order, cyclic = cx.order_formulas({f["field_id"]: f["deps"] for f in forms})
    position = {fid: i + 1 for i, fid in enumerate(order)}
    final = {}                                                      # field ID -> (status, reason)

    def blocked(f, status, reason):
        final[f["field_id"]] = (status, reason)

    for fid in order + cyclic:
        f, tr = by_id[fid], by_id[fid]["tr"]
        if f["is_valid"] == 0:
            blocked(f, "BLOCKED_UNSUPPORTED_FUNCTION", "Airtable reports this formula as not valid.")
        elif tr.status == "BLOCKED_UNSUPPORTED_FUNCTION":
            blocked(f, tr.status, tr.reason)
        elif fid in cyclic:
            blocked(f, "BLOCKED_DEPENDENCY", "It reads itself or is part of a loop of formulas, or reads one that is.")
        elif any(final[d][0] != "TRANSLATABLE" for d in f["deps"]):
            bad = [by_id[d]["field_name"] for d in f["deps"] if final[d][0] != "TRANSLATABLE"]
            blocked(f, "BLOCKED_DEPENDENCY", "It reads a formula that is itself blocked: %s." % ", ".join(sorted(bad)))
        elif tr.status == "BLOCKED_MISSING_REFERENCE":
            amb = sorted(m for m in tr.missing if m in ambiguous)
            blocked(f, tr.status, tr.reason + (" More than one target is recorded for: %s." % ", ".join(amb) if amb else ""))
        elif not f["target"]:
            blocked(f, "BLOCKED_MISSING_REFERENCE", "This formula field has no approved Core name (approve one on the Names page) and no single target in the crosswalk.")
        else:
            final[fid] = ("TRANSLATABLE", "")

    # shared-field checks: the target name against Core, and against the other formulas of this run
    same = {}
    for f in forms:
        if final[f["field_id"]][0] == "TRANSLATABLE":
            same.setdefault(f["target"], []).append(f["field_id"])
    for f in forms:
        fid = f["field_id"]
        if final[fid][0] != "TRANSLATABLE":
            continue
        notes = list(f["tr"].warnings)
        ctype = f["core_target_type"] or RESULT_TYPES.get(f["result_type"] or "")
        cdisp = CALC_DISPLAY_TYPE
        if not ctype:
            notes.append("No Core dataType could be chosen for the result type '%s'; set one on the Data types page." % (f["result_type"] or "unknown"))
        f["ctype"], f["cdisp"], f["reuse"] = ctype, cdisp, None
        problem = ""
        if len(same[f["target"]]) > 1:
            problem = "%d formulas of this table share the name %s; one form can hold a field only once." % (len(same[f["target"]]), f["target"])
        else:
            params = _params_by_name(conn, environment, f["target"])
            if len(params) > 1:
                problem = "The name %s belongs to more than one field in Core." % f["target"]
            elif params:
                pid, existing = params[0]
                f["reuse"] = pid
                if existing and ctype and existing != ctype:
                    problem = "The field %s already exists with the data type %s, but this formula returns %s." % (f["target"], existing, ctype)
                else:
                    other = conn.execute("SELECT airtable_field_id FROM calc_formula_crosswalk WHERE environment=? AND category_id=? AND parameter_id=?", (environment, category_id, pid)).fetchone()
                    if other and other[0] != fid:
                        problem = "The field %s is already on this form for another Airtable field." % f["target"]
                    elif other:
                        notes.append("Already placed on this form; the push would only update its expression.")
                    else:
                        notes.append("The field %s already exists in Core and is shared: it is only placed on this form." % f["target"])
        if problem:
            blocked(f, "BLOCKED_MISSING_REFERENCE", CONFLICT + problem)
        else:
            final[fid] = ("TRANSLATABLE", " ".join(notes))

    schema, cross = fingerprints(conn, environment, category_id, [f["field_id"] for f in forms], [r for f in forms for r in f["tr"].references], [f["target"] for f in forms])
    now = now or utc_now()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE calc_preflight_runs SET state='superseded' WHERE environment=? AND table_id=? AND category_id=? AND state IN ('preflight', 'approved')", (environment, table_id, category_id))
        cur.execute("INSERT INTO calc_preflight_runs (environment, base_id, table_id, category_id, category_name, run_at, run_by, schema_hash, crosswalk_hash, state) VALUES (?,?,?,?,?,?,?,?,?, 'preflight')",
                    (environment, base_id, table_id, category_id, category_name or None, now, actor, schema, cross))
        run_id = cur.lastrowid
        for f in forms:
            fid = f["field_id"]
            status, reason = final[fid]
            ok = status == "TRANSLATABLE"
            cur.execute("INSERT INTO calc_preflight_items (run_id, formula_field_id, status, reason, source_formula, referenced_field_ids, functions_found, depends_on, push_order, translated_expression, "
                        "target_name, target_label, workfront_data_type, workfront_display_type, reuse_parameter_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (run_id, fid, status, reason or None, f["formula_text"], _j(f["tr"].references), _j(f["tr"].functions), _j(f["deps"]), position.get(fid),
                         f["tr"].expression if ok else None, f["target"], f["core_label"] or f["target"], f.get("ctype") if ok else None, f.get("cdisp") if ok else None, f.get("reuse") if ok else None))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return run_id


# ------------------------------------------------------------------ reading a run
def _run_row(conn, run_id):
    cur = conn.execute("SELECT run_id, environment, base_id, table_id, category_id, category_name, run_at, run_by, schema_hash, crosswalk_hash, state, approved_by, approved_at, approval_reason "
                       "FROM calc_preflight_runs WHERE run_id=?", (run_id,))
    row = cur.fetchone()
    if row is None:
        raise PreflightError("There is no such preflight run.", 404)
    return dict(zip([d[0] for d in cur.description], row))


def report(conn, run_id):
    """Everything the report page shows, from the saved snapshot (not recomputed). Counts per status, the translatable list with each expression, the blocked lists kept apart by
    status, the distinct functions with how many formulas use each and whether a translation is known, and the dependency order."""
    run_row = _run_row(conn, run_id)
    items = []
    for r in conn.execute("SELECT i.item_id, i.formula_field_id, f.field_name, i.status, i.reason, i.source_formula, i.referenced_field_ids, i.functions_found, i.depends_on, i.push_order, "
                          "i.translated_expression, i.target_name, i.target_label, i.workfront_data_type, i.workfront_display_type, i.reuse_parameter_id "
                          "FROM calc_preflight_items i LEFT JOIN airtable_fields f ON f.field_id = i.formula_field_id WHERE i.run_id=? ORDER BY COALESCE(i.push_order, 1000000), i.formula_field_id", (run_id,)):
        d = dict(zip(("item_id", "field_id", "field_name", "status", "reason", "source_formula", "referenced", "functions", "depends_on", "push_order", "expression", "target_name", "target_label",
                      "data_type", "display_type", "reuse_parameter_id"), r))
        for k in ("referenced", "functions", "depends_on"):
            d[k] = json.loads(d[k] or "[]")
        items.append(d)
    counts = {s: 0 for s in cx.STATUSES}
    for d in items:
        counts[d["status"]] += 1
    inv = cl.inventory([(d["field_id"], d["source_formula"]) for d in items])
    handled = set(cx.FUNCTIONS) | {"BLANK", "DATETIME_DIFF", "WORKDAY_DIFF"}
    blocked_by = {s: [d for d in items if d["status"] == s] for s in cx.STATUSES if s != "TRANSLATABLE"}
    return {"run": run_row, "counts": counts, "total": len(items), "translatable": [d for d in items if d["status"] == "TRANSLATABLE"], "blocked": blocked_by,
            "functions": [{"name": n, "calls": c, "formulas": ids, "translated": n in handled} for n, c, ids in inv],
            "unsupported_functions": sorted(n for n, c, ids in inv if n not in handled)}


def drift(conn, run_id):
    """What changed since the snapshot, in plain words; empty when nothing did. Recomputes the two fingerprints from the run's own items."""
    run_row = _run_row(conn, run_id)
    items = conn.execute("SELECT formula_field_id, referenced_field_ids, target_name FROM calc_preflight_items WHERE run_id=?", (run_id,)).fetchall()
    schema, cross = fingerprints(conn, run_row["environment"], run_row["category_id"], [i[0] for i in items], [x for i in items for x in json.loads(i[1] or "[]")], [i[2] for i in items])
    out = []
    if schema != run_row["schema_hash"]:
        out.append("A formula field changed (its formula, result type or existence) since the preflight.")
    if cross != run_row["crosswalk_hash"]:
        out.append("The crosswalk changed (a target field, or a placement on a form) since the preflight.")
    return out


def approve(conn, run_id, actor, reason):
    """A person approves a run: who, when and why are saved on the run. Refused when the run is not waiting for approval, has nothing translatable, or has drifted."""
    actor, reason = _clean(actor, ACTOR_MAX, "Your initials"), _clean(reason, REASON_MAX, "A short reason")
    run_row = _run_row(conn, run_id)
    if run_row["state"] != "preflight":
        raise PreflightError("This run is %s, so it cannot be approved." % run_row["state"], 409)
    if not conn.execute("SELECT 1 FROM calc_preflight_items WHERE run_id=? AND status='TRANSLATABLE'", (run_id,)).fetchone():
        raise PreflightError("Nothing in this run can be translated, so there is nothing to approve.", 409)
    changes = drift(conn, run_id)
    if changes:
        raise PreflightError("Run a new preflight first: " + " ".join(changes), 409)
    now = utc_now()
    conn.execute("UPDATE calc_preflight_runs SET state='approved', approved_by=?, approved_at=?, approval_reason=? WHERE run_id=?", (actor, now, reason, run_id))
    conn.commit()
    return {"run_id": run_id, "state": "approved", "approved_by": actor, "approved_at": now}
