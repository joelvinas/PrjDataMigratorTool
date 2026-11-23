#!/usr/bin/env python3
"""Emergency mode ("triage"): get ONE table's data into Workfront Planning today, whatever works. Standard library only.

A Go run does, for one table of one base, in the DEV Workspace of config.json:
    1  create the Planning record type (or reuse the one the database already maps to the table)
    2  for each field, in sorted order: skip links and attachments; send arrays as raw JSON text in a PRE- field (a multiple select with distinct options is a real multi-select); otherwise try the real Planning type, and when Planning
       refuses it create a text field named PRE-<field name> (long text when the longest value is over 255 characters, up to 20 long-text fields per record type)
    3  load the records (records pass only; links are for later)
    4  report: the result on screen, a CSV and two MHTML reports (see emergency_report.py)
A failing field or record never stops the run. Only a record type that cannot be created ends it, because nothing can follow.

Choices made by the owner (18 November 2026): names are sent EXACTLY as Airtable has them (no rewriting, nothing needs approval), and the crosswalk (Airtable name, the name
sent, and what the naming rules would suggest) goes into the reports. A field type the plan holds or blocks (formula, a missing type) is still attempted as given; when
Planning refuses it, it is created as PRE- text and both attempts are recorded.

Rules kept (CLAUDE.md): this module reads record values from the saved record file in memory only to measure them (the longest value, how many are empty, whether any is an
array or object) and to send them; it keeps numbers and field IDs, never a value. IDs go to the existing map tables (load_id_map, source_records, target_mappings,
record_pass_markers) through loadstate and recordmap. Nothing here talks HTTP itself: every Workfront call goes through planningtransport over workfronthttp, and an Airtable
re-read (only when the saved file is missing) is a callable the server supplies, which goes through api_call. Deterministic: tables, fields and records in sorted order.
"""
import json
import re
from datetime import datetime, timezone

import datatypes as dt
import fieldgroups as fgp
import fieldnames_store as fs
import loadrun as lr
import loadstate as ls
import planningtransport as pt
import recordload as rl
import recordmap as rm
import typemap as tm

STAGE = "dev"
PRE = "PRE-"
TEXT_MAX = 255                 # a value of this many characters or fewer fits a text field; longer needs long text
LONG_TEXT_CAP = 20             # Planning allows 20 long-text fields per record type
SKIP_LINKS, SKIP_ATTACH = "skipped (links later)", "skipped (attachments later)"
ARRAY_TYPES = frozenset({"multipleSelects", "multipleLookupValues", "multipleCollaborators"})      # always arrays; the scan also catches a rollup or other field that holds one
SKIP_TYPES = {"multipleRecordLinks": SKIP_LINKS, "multipleAttachments": SKIP_ATTACH}
OUTCOMES = ("clean", "pre-converted", "skipped", "failed")

# Used only when the database has no default mapping (migration 021 not applied). Mirrors the seed rows of that migration.
FALLBACK_PLANNING = {"singleLineText": "text", "email": "text", "url": "text", "phoneNumber": "text", "multilineText": "long-text", "richText": "long-text", "number": "number",
                     "rating": "number", "duration": "number", "percent": "percentage", "currency": "currency", "date": "date", "dateTime": "date", "checkbox": "boolean",
                     "singleSelect": "single-select", "multipleSelects": "multi-select", "singleCollaborator": "user", "multipleCollaborators": "user", "createdTime": "created-at",
                     "lastModifiedTime": "updated-at", "createdBy": "created-by", "lastModifiedBy": "updated-by", "formula": "formula"}


class EmergencyError(Exception):
    """A plain-language reason the run cannot start or cannot go on. Never holds a secret or a record value."""
    def __init__(self, message, status=400):
        super(EmergencyError, self).__init__(message)
        self.message, self.status = message, status


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ the call recorder (numbers every Workfront call of the run)
class CallLog(object):
    """Collects one entry per HTTP attempt, in order, numbered from 1. `phase` and `field_id` are set by the run before each step, so a failed field can point at its entries."""
    def __init__(self):
        self.entries, self.phase, self.field_id = [], "record_type", None

    def __call__(self, e):
        self.entries.append(dict(e, number=len(self.entries) + 1, timestamp=utc_now(), phase=self.phase, field_id=self.field_id))

    def span(self, field_id):
        n = [e["number"] for e in self.entries if e["field_id"] == field_id]
        return (min(n), max(n)) if n else None


# ------------------------------------------------------------------ reading the schema (SQLite only)
def _tables_in(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}


def load_schema(conn, base_id, table_id):
    """{table_name, fields: [dict per field, sorted by name then ID], suggestions, defaults}. Schema and profile numbers only; no record value."""
    t = conn.execute("SELECT table_name FROM airtable_tables WHERE base_id=? AND table_id=?", (base_id, table_id)).fetchone()
    if t is None:
        raise EmergencyError("That table is not in the database for this base. Ingest the schema in Stage 1 first.", 404)
    rows = conn.execute("SELECT * FROM airtable_fields WHERE base_id=? AND table_id=? ORDER BY lower(field_name), field_id", (base_id, table_id)).fetchall()
    if not rows:
        raise EmergencyError("No fields are stored for this table. Ingest the schema in Stage 1 first.", 404)
    have = _tables_in(conn)
    stored = {}
    if "field_names" in have:
        stored = {r["airtable_field_id"]: dict(r) for r in conn.execute("SELECT * FROM field_names")}
    names = {r["field_id"]: r["field_name"] for r in conn.execute("SELECT field_id, field_name FROM airtable_fields WHERE base_id=?", (base_id,))}
    defaults = {}
    if "data_type_defaults" in have:
        defaults = {r["airtable_type"]: r["default_type"] for r in conn.execute("SELECT airtable_type, default_type FROM data_type_defaults WHERE system='planning'") if r["default_type"]}
    merged = set()
    try:
        data = fs.load_fields(conn, base_id)
        groups, _ok = fs.read_groups(conn, base_id)
        merged = set(fgp.merged_ids(fs._live_groups(groups, data)))
    except Exception:                                    # consolidation is optional: without it no field is treated as merged
        merged = set()
    suggestions = _suggestions(conn, base_id)
    fields = []
    for r in rows:
        r = dict(r)
        n = stored.get(r["field_id"]) or {}
        r["stored_planning_type"] = n.get("target_type")
        r["abandoned"] = bool(n.get("abandoned"))
        r["merged"] = r["field_id"] in merged
        sug = suggestions.get(r["field_id"]) or {}
        r["suggested"] = {"planning": sug.get("planning_name") or n.get("planning_name") or "", "core": sug.get("core_name") or n.get("core_name") or "",
                          "label": sug.get("core_label") or n.get("core_label") or ""}
        r["choices"] = _choices(r.get("choices_json"))
        r["definition"] = _definition(r, names)
        fields.append(r)
    return {"table_name": t["table_name"], "fields": fields, "defaults": defaults}


def _suggestions(conn, base_id):
    """What the naming rules suggest per field (for the crosswalk). Only available once every in-scope table name is approved (the review's gate); {} otherwise."""
    try:
        rv = fs.review(conn, base_id)
    except Exception:
        return {}
    out = {}
    for f in rv.get("fields", []):
        t = f.get("targets") or {}
        pick = lambda k: (t.get(k) or {}).get("approved_name") or (t.get(k) or {}).get("proposed") or (t.get(k) or {}).get("suggested") or ""
        out[f["field_id"]] = {"planning_name": pick("planning_name"), "core_name": pick("core_name"), "core_label": pick("core_label")}
    return out


def _choices(raw):
    try:
        items = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return [str(x.get("name")) for x in items if isinstance(x, dict) and x.get("name") is not None]


def _definition(r, names):
    """The formula, or the lookup or rollup definition in words (field names only)."""
    if r.get("formula_text"):
        return str(r["formula_text"])
    if r.get("source_link_field_id") or r.get("source_field_id"):
        via = names.get(r.get("source_link_field_id"), r.get("source_link_field_id") or "?")
        what = names.get(r.get("source_field_id"), r.get("source_field_id") or "?")
        return "%s through link field %s, reading field %s" % (r["data_type"], via, what)
    return ""


# ------------------------------------------------------------------ the length scan (numbers only; every value is dropped as it is measured)
def _text_length(v):
    if isinstance(v, str):
        return len(v)
    return len(rl.raw_text(v))


def scan_records(records, field_ids):
    """{field_id: {total, empty, nonempty, max_len, has_container}} from the records of one table, held in memory. Values are measured and forgotten."""
    st = {f: {"total": 0, "empty": 0, "nonempty": 0, "max_len": 0, "has_container": False} for f in field_ids}
    for rec in records:
        src = (rec.get("fields") if isinstance(rec, dict) else None) or {}
        for f, s in st.items():
            s["total"] += 1
            v = src.get(f)
            if v is None or v == "" or v == [] or v == {}:
                s["empty"] += 1
                continue
            s["nonempty"] += 1
            if isinstance(v, (list, dict)):
                s["has_container"] = True
            n = _text_length(v)
            if n > s["max_len"]:
                s["max_len"] = n
    return st


def count_convert_failures(records, fields):
    """{field_id: values that could not be converted for the type Planning accepted}. Reasons are dropped; only the count is kept."""
    bad = {f["field_id"]: 0 for f in fields}
    for rec in records:
        src = (rec.get("fields") if isinstance(rec, dict) else None) or {}
        for f in fields:
            ok, _v = rl.convert(f, src.get(f["field_id"]))
            if not ok:
                bad[f["field_id"]] += 1
    return bad


# ------------------------------------------------------------------ creating one thing in Planning
class _Transport(object):
    """Wraps the real transport so a field can be tried with ANY type text, even one the loader does not build (the owner wants it attempted as given). The runner
    (loadrun.run_plan) keeps the state, the pending row and the adoption after an interrupted run; this class only builds the request."""
    system = "planning"

    def __init__(self, tr):
        self.tr = tr

    def create(self, item, parent_id, refs=None):
        if item["kind"] == "record_type" or tm.planning_type(item["data_type"])[0] is not None:
            return self.tr.create(item, parent_id, refs)
        if not parent_id:
            raise ValueError("A field needs its record type's Planning ID first.")
        body = {"displayName": item["name"], "type": str(item["data_type"])}
        reply = self.tr.http.request("POST", "/v2/record-types/%s/fields" % parent_id, body)
        new = reply.get("id") if isinstance(reply, dict) else None
        if not new:
            raise pt.HttpError("The create call worked but its reply holds no ID, so the item cannot be recorded.")
        return str(new)

    def find_existing(self, item, parent_id, refs=None):
        return self.tr.find_existing(item, parent_id, refs)


def _item(temp_id, kind, name, parent, table_id, field_id=None, data_type=None, spec=None):
    return {"temp_id": temp_id, "system": "planning", "kind": kind, "parent": parent, "table_id": table_id, "field_id": field_id, "name": name, "label": None, "data_type": data_type,
            "display_type": None, "position": None, "status": "ready", "reasons": [], "description": None, "source": "airtable", "group": None, "spec": spec, "requires": [], "pass": None}


def _try(state, env, tr, item):
    """Create one item through the runner. Returns (ok, workfront_id, error text)."""
    res = lr.run_plan([item], state, {"planning": _Transport(tr)}, env, dry_run=False)["results"][0]
    if res["outcome"] in ("created", "adopted", "already_created"):
        return True, str(res["detail"]), None, res["outcome"]
    return False, None, str(res["detail"]), res["outcome"]


def _error_code(text):
    m = re.search(r"\"(?:code|errorCode)\"\s*:\s*\"([A-Za-z0-9_.-]{1,60})\"", text or "")
    return m.group(1) if m else ""


# ------------------------------------------------------------------ the run
def attempted_type(field, defaults):
    """(Planning type text to try, where it came from). A stored type that is on Adobe's list wins, then the default mapping, then the built-in mapping; with none of
    them the Airtable type itself is sent as given (and will most likely be refused)."""
    stored = field.get("stored_planning_type")
    if stored and dt.planning_type(stored) == stored:
        return stored, "the type set on the Data types page"
    t = field["data_type"]
    if defaults.get(t):
        return defaults[t], "the default mapping for %s" % t
    if FALLBACK_PLANNING.get(t):
        return FALLBACK_PLANNING[t], "the built-in mapping for %s" % t
    return t, "no mapping exists: the Airtable type %s was sent as given" % t


PK_NAME = "AirtableRecordId"
CONFLICT = "CONFLICT-"                              # an Airtable field whose name is taken by a field Planning will not let the tool delete or rename is created with this prefix
RENAMED_PREFIX = "Unused default - "                  # a default field Planning will not delete is renamed with this, so its name cannot clash with an Airtable field
DEFAULT_FIELDS = frozenset({"name", "description", "start date", "end date", "status"})      # what a new Planning record type comes with (owner, 19 November 2026)


def prepare_record_type(conn, state, env, tr, rt_item, rt_id, base_id, table_id, reused, result, notes):
    """The preparatory step of a Git-R-Done run. Planning's default fields (Name, Description, Start Date, End Date, Status) clash with Airtable field names, and the owner
    confirmed in the Planning UI that they can be deleted once another field is the primary field. So: (1) create AirtableRecordId (text), recorded in the ID map under
    P-K-<table> so a rerun never makes a second one; (2) make it the primary field, checked by reading the record type back; (3) delete the other fields.
    A NEW record type loses every field except the ones this tool created; a REUSED one loses only a leftover default field by name, because a person may have added fields
    to it. A failed delete is a note, not the end of the run; steps 1 and 2 failing end it (returns False), with nothing deleted. Returns (ok, primary field ID).
    result["prepare"] = {field_id, created, primary, deleted: [{id, name, type}], failed: [{id, name, type, reason}], kept: [...]}."""
    prep = result["prepare"] = {"field_id": None, "created": None, "primary": False, "deleted": [], "renamed": [], "failed": [], "kept": [], "protected": [], "taken": [], "error": ""}
    item = _item("P-K-" + table_id, "field", PK_NAME, rt_item["temp_id"], table_id, None, "text")
    ok, pk_id, err, how = _try(state, env, tr, item)
    if not ok:
        prep["error"] = "AirtableRecordId could not be created: %s" % err
        return False, None
    prep.update(field_id=pk_id, created=how)
    try:
        tr.set_primary_field(rt_id, pk_id)
        prep["primary"] = True
    except Exception as e:
        prep["error"] = "AirtableRecordId was created but could not be made the primary field: %s" % lr.scrub(e)
        return False, pk_id
    try:
        fields = tr.list_fields(rt_id)
    except Exception as e:
        prep["error"] = "The fields of the record type could not be listed, so the default fields were not deleted: %s" % lr.scrub(e)
        return False, pk_id
    mine = {r[0] for r in conn.execute("SELECT workfront_id FROM load_id_map WHERE system='planning' AND kind='field' AND status='created' AND environment=? AND workspace_key=? AND base_id=? AND table_id=? "
                                       "AND workfront_id IS NOT NULL", (env.name, state.workspace_id or "", base_id, table_id))} | {pk_id}
    delete_refused = protected = False
    for f in sorted(fields, key=lambda x: (x["name"].lower(), x["id"])):
        if f["id"] in mine or (reused and f["name"].strip().lower() not in DEFAULT_FIELDS):
            prep["kept"].append(f)
            continue
        if protected:                                 # the first delete AND the first rename were refused: Planning protects these fields, so they are left alone
            prep["protected"].append(f)
            continue
        if not delete_refused:
            out = tr.delete_field(f["id"])
            if out["deleted"]:
                prep["deleted"].append(f)
                continue
            delete_refused = True                     # Planning refuses deletes (a 403 FORBIDDEN in the first real run): the rest are renamed without asking again
            prep["delete_refused"] = ("HTTP %s: " % out["status"] if out.get("status") else "") + str(out["reason"])[:200]
            perms = tr.field_permissions(f["id"])
            prep["permissions"] = perms
            if perms:
                prep["delete_refused"] += " (this token on the field %s: %s)" % (f["name"], ", ".join("%s=%s" % (k, str(v).lower()) for k, v in sorted(perms.items())))
        new_name = RENAMED_PREFIX + f["name"]
        out = tr.rename_field(f["id"], new_name)
        if out["renamed"]:
            prep["renamed"].append(dict(f, new_name=new_name))
        elif not prep["renamed"]:                     # nothing was ever renamed and the rename is refused too (403 in the first real run): stop trying
            protected = True
            prep["rename_refused"] = ("HTTP %s: " % out["status"] if out.get("status") else "") + str(out["reason"])[:200]
            prep["protected"].append(f)
        else:
            prep["failed"].append(dict(f, reason=("HTTP %s: " % out["status"] if out.get("status") else "") + str(out["reason"])[:200]))
    prep["taken"] = sorted({x["name"] for x in prep["protected"] + prep["failed"] + prep["kept"] if x["id"] not in mine and x["name"]}, key=str.lower)
    if prep["renamed"]:
        notes.append("Planning would not delete %d default field(s), so they were renamed with the prefix \"%s\" instead (%s). Workfront's answer to the delete: %s" % (len(prep["renamed"]), RENAMED_PREFIX.strip(), ", ".join(sorted(x["name"] for x in prep["renamed"])), prep.get("delete_refused") or "none"))
    if prep["protected"]:
        notes.append("Planning will neither delete nor rename its default fields through the API (%s). Delete: %s Rename: %s. They were left in place; an Airtable field with the same name is created with the prefix %s." % (
            ", ".join(sorted(x["name"] for x in prep["protected"])), prep.get("delete_refused") or "none", prep.get("rename_refused") or "none", CONFLICT))
    if prep["failed"]:
        notes.append("%d default field(s) could be neither deleted nor renamed (%s). An Airtable field with the same name is created with the prefix %s." % (len(prep["failed"]), ", ".join(sorted(x["name"] for x in prep["failed"])), CONFLICT))
    return True, pk_id


def run_table(conn, base_id, table_id, ctx, tr, extract_dir, calls, fetch_doc=None, progress=None, should_stop=None, batch=None, reader=rl.read_extract):
    """One Go run. `calls` is the CallLog already hooked to the transport's HTTP layer. Returns the result dict that the page shows and the reports are made from."""
    progress = progress or (lambda p: None)
    stop = should_stop or (lambda: False)
    started = utc_now()
    schema = load_schema(conn, base_id, table_id)
    state = ls.DbState(conn, base_id, ctx.ws)
    env = ctx.env
    notes = []
    result = {"base_id": base_id, "table_id": table_id, "table_name": schema["table_name"], "stage": ctx.stage, "workspace_id": ctx.ws, "started_at": started, "finished_at": None,
              "base_name": "", "workspace_name": "", "fields": [], "record_type": {"temp_id": "P-T-" + table_id, "name": schema["table_name"], "planning_id": None, "outcome": None, "error": "", "reused": False},
              "records": {"in_source": None, "loaded": 0, "already_loaded": 0, "failed": 0, "values_not_converted": 0, "failures": [], "note": None},
              "scan_source": None, "notes": notes, "cancelled": False, "calls": calls.entries, "db_written": False, "db_note": None}

    # ---- the records of the table, in memory only (the saved file, or a re-read from Airtable)
    doc = None
    try:
        doc = reader(extract_dir, base_id, table_id)
        result["scan_source"] = "the saved record file in data_extract"
    except rl.RecordLoadError as e:
        if fetch_doc is None:
            notes.append("No saved record file for this table, and no re-read from Airtable was possible: %s Lengths are unknown, so a field that needs text is made long text." % e)
        else:
            progress({"phase": "reread", "message": "No saved record file: reading the table from Airtable"})
            doc = fetch_doc(base_id, table_id)
            result["scan_source"] = "a re-read from Airtable (there was no saved record file)"
            result["reread"] = True
            notes.append("There was no saved record file for this table, so its records were re-read from Airtable (through the API call log) for the length scan and the load.")
    records = doc["records"] if doc else []
    result["records"]["in_source"] = len(records) if doc else None
    field_ids = [f["field_id"] for f in schema["fields"]]
    stats = scan_records(records, field_ids) if doc else {}

    # ---- 1. the record type
    calls.phase, calls.field_id = "record_type", None
    rt_item = _item("P-T-" + table_id, "record_type", schema["table_name"], None, table_id)
    reused_before = bool(state.get(rt_item["temp_id"], env.name))
    ok, rt_id, err, how = _try(state, env, tr, rt_item)
    rtr = result["record_type"]
    rtr.update(outcome="reused" if how == "already_created" else ("adopted" if how == "adopted" else "created") if ok else "failed", error=err or "", reused=reused_before)
    if not ok:
        rtr["error"] = err
        result["finished_at"] = utc_now()
        notes.append("The record type could not be created, so nothing else was attempted: %s" % err)
        return _finish(conn, result, calls)
    rtr["planning_id"] = rt_id

    # ---- 1b. the preparatory step: AirtableRecordId becomes the primary field, then Planning's default fields are deleted, BEFORE any Airtable field is added
    calls.phase, calls.field_id = "prepare", None
    progress({"phase": "prepare", "message": "Preparing the record type: AirtableRecordId as the primary field, then removing the default fields"})
    prep_ok, pk_id = prepare_record_type(conn, state, env, tr, rt_item, rt_id, base_id, table_id, reused_before, result, notes)
    if not prep_ok:
        result["finished_at"] = utc_now()
        notes.append("The preparatory step did not finish, so no Airtable field was added (the default fields could clash with them). Fix the cause and run again: nothing is created twice.")
        return _finish(conn, result, calls)

    # ---- what already exists in the record type (only when it was reused: a new one is empty)
    existing = {}
    if reused_before:
        try:
            calls.phase = "fields"
            existing = {str(r["id"]): r for r in tr._pages("/v2/record-types/%s/fields" % rt_id) if isinstance(r, dict) and r.get("id")}
        except Exception as e:
            notes.append("The fields already in the record type could not be listed (%s); the long-text count may be low." % lr.scrub(e))
    long_count = sum(1 for r in existing.values() if r.get("type") == "long-text")

    # ---- 2. the fields
    calls.phase = "fields"
    loadable = [{"field_id": "(record id)", "planning_id": pk_id, "kind": "text", "options": {}, "linked_table_id": None, "single": False, "pass": "records", "raw": True, "record_id": True}]
    for n, f in enumerate(schema["fields"], 1):
        if stop():
            result["cancelled"] = True
            break
        fid = f["field_id"]
        calls.field_id = fid
        progress({"phase": "fields", "done": n - 1, "total": len(schema["fields"]), "item": f["field_name"]})
        s = stats.get(fid) or {}
        row = {"field_id": fid, "field_name": f["field_name"], "airtable_type": f["data_type"], "suggested_planning": f["suggested"]["planning"], "suggested_core": f["suggested"]["core"],
               "suggested_label": f["suggested"]["label"], "sent_name": "", "attempted_type": "", "attempted_from": "", "attempt1": "", "attempt1_error_code": "", "attempt1_error": "",
               "attempt2_type": "", "attempt2": "", "attempt2_error_code": "", "attempt2_error": "", "final_name": "", "final_type": "", "outcome": "", "reason": "", "planning_field_id": "",
               "max_length": s.get("max_len") if s else None, "cardinality": f.get("cardinality"), "cardinality_capped": bool(f.get("cardinality_is_capped")),
               "nulls": f.get("null_count"), "total_records": f.get("row_count"), "options": list(f["choices"]), "definition": f["definition"], "log_span": None,
               "values_not_converted": 0, "note": "", "api_calls": 0, "api_successes": 0, "api_failures": 0}
        result["fields"].append(row)
        if f["data_type"] in SKIP_TYPES:
            row.update(outcome="skipped", reason=SKIP_TYPES[f["data_type"]])
            continue
        if f["abandoned"]:
            row.update(outcome="skipped", reason="skipped (abandoned by a person on the Field names page)")
            continue
        if f["merged"]:
            row.update(outcome="skipped", reason="skipped (merged into another field by a consolidation group)")
            continue
        item_spec = {"airtable_type": f["data_type"], "precision": f.get("number_precision"), "currency_symbol": f.get("currency_symbol"), "options": list(f["choices"]), "single": False}
        temp = "P-F-" + fid
        atype, src = attempted_type(f, schema["defaults"])
        # A multiple select is an array, but Planning has a real multi-select type for it (owner-verified 23 November 2026: send the option NAMES Planning returned), so it is
        # attempted as one. Every other array, or a multiple select the owner mapped to another type, is still kept as raw JSON text.
        is_array = (f["data_type"] in ARRAY_TYPES or bool(s.get("has_container"))) and not (f["data_type"] == "multipleSelects" and atype == "multi-select")
        planned = state.get(temp, env.name)
        longest = s.get("max_len") if s else None
        sent = f["field_name"]
        taken = {x.lower() for x in (result.get("prepare") or {}).get("taken", [])}
        send_name = CONFLICT + sent if sent.lower() in taken else sent
        row["sent_name"] = send_name
        if send_name != sent:
            row["note"] = "the name is taken by a field Planning keeps in the record type, so the field is created as %s" % send_name

        # two-part check: the real type was refused AND there is nothing to store (every value empty) -> plain text, so it does not use up a long-text slot.
        # "Empty" is known from the scan of the records, or, when no records could be read, from the Stage 1 profile (null count equals row count).
        all_null = (s.get("nonempty") == 0 and (s.get("total") or 0) > 0) or (not s and (f.get("row_count") or 0) > 0 and (f.get("null_count") or 0) >= f["row_count"])

        def fallback_type():
            nonlocal long_count
            if all_null:
                return "text"
            if longest is None or longest > TEXT_MAX:
                if long_count >= LONG_TEXT_CAP:
                    row["note"] = ("possibly truncated: the record type already has %d long-text fields, the most Planning allows, so this one is plain text" % LONG_TEXT_CAP)
                    return "text"
                return "long-text"
            return "text"

        def make_pre(why_first):
            """Create PRE-<name> as text or long text and return True when it exists."""
            nonlocal long_count
            ptype = fallback_type()
            row.update(attempt2_type=ptype)
            it = _item(temp, "field", PRE + sent, rt_item["temp_id"], table_id, fid, ptype, item_spec)
            ok2, wid2, err2, _how = _try(state, env, tr, it)
            row.update(attempt2="created" if ok2 else "refused", attempt2_error=err2 or "", attempt2_error_code=_error_code(err2))
            if ok2:
                if ptype == "long-text":
                    long_count += 1
                row.update(final_name=PRE + sent, final_type=ptype, planning_field_id=wid2, outcome="pre-converted", reason=why_first)
                return True, wid2, ptype
            row.update(outcome="failed", reason="both attempts were refused; the field is not in Planning")
            return False, None, ptype

        if planned and existing:                                                          # a field an earlier run created: read what it became
            e = existing.get(planned)
            if e is None:
                row.update(outcome="failed", reason="an earlier run recorded this field, but it is not in the record type any more; check Planning", planning_field_id=planned)
                continue
            etype, ename = e.get("type") or "", e.get("displayName") or ""
            is_pre = ename == PRE + sent and etype in ("text", "long-text")
            row.update(final_name=ename, final_type=etype, planning_field_id=planned, outcome="pre-converted" if is_pre else "clean", reason="already created by an earlier run (reused)",
                       attempted_type=etype)
            pt_opts = {str(o.get("displayName")): str(o.get("name") if o.get("name") not in (None, "") else o.get("displayName")) for o in (e.get("options") or []) if isinstance(o, dict)}
            loadable.append({"field_id": fid, "planning_id": planned, "kind": tm.kind(etype) or etype, "options": pt_opts, "linked_table_id": None, "single": False, "pass": "records", "raw": is_pre})
            continue
        if planned and not existing:                                                      # recorded as created, listing unavailable: trust the name
            row.update(final_name=send_name, planning_field_id=planned, outcome="clean", reason="already created by an earlier run (its type could not be read back)")
            continue

        if is_array:
            row.update(attempted_type="(not attempted: an array)", attempted_from="arrays are stored as raw JSON text", attempt1="not attempted")
            ok2, wid, ptype = make_pre("an array or object from Airtable, stored exactly as received as raw JSON text in a PRE- field")
            if ok2:
                loadable.append({"field_id": fid, "planning_id": wid, "kind": ptype, "options": {}, "linked_table_id": None, "single": False, "pass": "records", "raw": True})
            continue

        row.update(attempted_type=atype, attempted_from=src)
        add_note = lambda text: row.update(note=(row["note"] + "; " if row["note"] else "") + text)
        chk = None
        if atype in tm.SELECT_KINDS:
            # The duplicate check comes first, from the schema: a select field is only created with options that are all distinct (single and multiple alike).
            chk = pt.check_options(f["choices"])
            item_spec["options"] = chk["options"]
            if chk["exact_repeats"]:
                add_note("%d repeated option name(s) were sent once" % chk["exact_repeats"])
            if chk["blank"]:
                add_note("%d blank option name(s) were left out" % chk["blank"])
            if chk["conflicts"]:
                row.update(attempt1="not attempted")
                add_note("%d option name(s) differ only by letter case or spacing (%s); Planning refuses that, so the field was not created as a select. Make them distinct and run again"
                         % (len(chk["conflicts"]), "; ".join(repr(c) for c in chk["conflicts"][:10]) + (" and more" if len(chk["conflicts"]) > 10 else "")))
                ok2, wid, ptype = make_pre("the option names are not distinct once letter case and spacing are ignored")
                if ok2:
                    loadable.append({"field_id": fid, "planning_id": wid, "kind": ptype, "options": {}, "linked_table_id": None, "single": False, "pass": "records", "raw": True})
                continue
        it = _item(temp, "field", send_name, rt_item["temp_id"], table_id, fid, atype, item_spec)
        ok1, wid, err1, _how = _try(state, env, tr, it)
        row.update(attempt1="created" if ok1 else "refused", attempt1_error=err1 or "", attempt1_error_code=_error_code(err1))
        if ok1:
            if atype == "long-text":
                long_count += 1
            row.update(final_name=send_name, final_type=atype, planning_field_id=wid, outcome="clean", reason="")
            if f["data_type"] == "dateTime" and atype == "date":
                add_note("the time of day is not kept: Planning's date type has no time")
            opts = dict(tr._options.get(wid) or {})
            if chk is not None:
                try:
                    opts = tr.options_for(wid, rt_id)          # the option NAMES Planning returned (never the labels), read back from the field list when the create reply had none
                except Exception as e:
                    add_note("the options could not be read back from Planning (%s)" % lr.scrub(e))
                lost = [o for o in chk["options"] if o not in opts]
                if lost:
                    add_note("%d option(s) are not in the field Planning created, so records that use them are left empty for this field" % len(lost))
            loadable.append({"field_id": fid, "planning_id": wid, "kind": tm.kind(atype) or atype, "options": opts, "linked_table_id": None, "single": False, "pass": "records", "raw": False})
            continue
        ok2, wid, ptype = make_pre("Planning refused the type %s" % atype)
        if ok2:
            loadable.append({"field_id": fid, "planning_id": wid, "kind": ptype, "options": {}, "linked_table_id": None, "single": False, "pass": "records", "raw": True})
    calls.field_id = None
    for row in result["fields"]:
        row["log_span"] = calls.span(row["field_id"])
        mine = [e for e in calls.entries if e["field_id"] == row["field_id"]]
        row["api_calls"] = len(mine)
        row["api_successes"] = sum(1 for e in mine if e["status"] and 200 <= e["status"] < 300)
        row["api_failures"] = row["api_calls"] - row["api_successes"]

    # ---- values that cannot be converted for a field Planning accepted as a real type (counts only)
    if records and loadable:
        bad = count_convert_failures(records, [f for f in loadable if not f["raw"]])
        by = {r["field_id"]: r for r in result["fields"]}
        for fid2, n2 in bad.items():
            by[fid2]["values_not_converted"] = n2
            if n2:
                by[fid2]["note"] = (by[fid2]["note"] + "; " if by[fid2]["note"] else "") + "%d value(s) cannot be sent as this type and are left empty in Planning" % n2
        result["records"]["values_not_converted"] = sum(bad.values())

    # ---- 3. the records (records pass only)
    if result["cancelled"]:
        notes.append("The run was cancelled before the records were loaded.")
    elif not doc:
        notes.append("The records were not loaded: there is no record data to send.")
    else:
        calls.phase, calls.field_id = "records", None
        tr.http.record_text = False
        try:
            rm.register(conn, base_id, table_id, [r.get("id") for r in records if isinstance(r, dict)])
            before = len(rm.pending_records(conn, base_id, table_id, "records", env.name, "planning", ctx.ws))
            tables = {table_id: {"record_type": rt_id, "name": schema["table_name"], "fields": sorted(loadable, key=lambda f: f["field_id"]), "held": 0}}
            res = rl.load_records(conn, base_id, tables, extract_dir, tr, env.name, ctx.ws, batch=int(batch or ctx.creds.extra.get("record_batch_size") or rl.BATCH_DEFAULT),
                                  dry_run=False, reader=lambda *_a: doc, only=[table_id], should_stop=stop,
                                  progress=lambda p: progress({"phase": "records", "done": p.get("created", 0) + p.get("failed", 0), "total": before, "item": "records"}))
            row = res["tables"][0] if res["tables"] else {"created": 0, "failed": 0, "failures": []}
            result["records"].update(loaded=row["created"], failed=row["failed"], already_loaded=max(0, len(records) - before),
                                     failures=[{"record_id": rid, "reason": why} for rid, why in row["failures"][:200]])
            result["cancelled"] = bool(res.get("cancelled"))
        except (rm.RecordMapError, rl.RecordLoadError) as e:
            notes.append("The records could not be loaded: %s" % e)
        finally:
            tr.http.record_text = True
    result["finished_at"] = utc_now()
    return _finish(conn, result, calls)


def _finish(conn, result, calls):
    result["counts"] = summary_counts(result)
    note = store_results(conn, result)
    result["db_written"], result["db_note"] = note
    return result


def summary_counts(result):
    c = {k: 0 for k in OUTCOMES}
    for f in result["fields"]:
        if f["outcome"] in c:
            c[f["outcome"]] += 1
    r = result["records"]
    c.update(records_loaded=r["loaded"], records_already_loaded=r["already_loaded"], records_failed=r["failed"], values_not_converted=r["values_not_converted"],
             fields_total=len(result["fields"]))
    return c


# ------------------------------------------------------------------ the optional database table (draft migration 025)
RESULT_COLUMNS = ("run_id", "run_at", "base_id", "table_id", "airtable_field_id", "airtable_field_name", "airtable_type", "planning_name", "core_name", "label_name",
                  "attempted_planning_type", "outcome", "planning_field_id", "error_code", "error_text", "max_length", "cardinality", "null_count", "total_records", "option_list",
                  "formula_definition")


def run_id_of(result):
    return "E%s-%s" % (result["started_at"].replace("-", "").replace(":", ""), result["table_id"])


def store_results(conn, result):
    """Write one row per field to emergency_field_results when that table exists (draft migration 025). Returns (written, note). Until the owner applies the migration the
    write is skipped and the reports are unaffected."""
    if "emergency_field_results" not in _tables_in(conn):
        return False, "The table emergency_field_results does not exist yet (draft migration 025 is not applied), so the field results were not saved to the database. The reports are complete."
    rid = run_id_of(result)
    rows = []
    for f in sorted(result["fields"], key=lambda x: x["field_id"]):
        err = f["attempt2_error"] if f["outcome"] == "failed" else f["attempt1_error"]
        code = f["attempt2_error_code"] if f["outcome"] == "failed" else f["attempt1_error_code"]
        rows.append((rid, result["started_at"], result["base_id"], result["table_id"], f["field_id"], f["field_name"], f["airtable_type"], f["suggested_planning"], f["suggested_core"],
                     f["suggested_label"], f["attempted_type"], f["outcome"], f["planning_field_id"] or None, code or None, (err or "")[:500] or None, f["max_length"], f["cardinality"],
                     f["nulls"], f["total_records"], json.dumps(f["options"], ensure_ascii=False) if f["options"] else None, f["definition"] or None))
    with conn:
        conn.executemany("INSERT OR REPLACE INTO emergency_field_results(%s) VALUES (%s)" % (", ".join(RESULT_COLUMNS), ", ".join("?" * len(RESULT_COLUMNS))), rows)
    return True, "%d field result rows saved to emergency_field_results." % len(rows)
