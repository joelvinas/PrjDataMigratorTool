#!/usr/bin/env python3
"""Saved decisions for the field name review (tables from migration 007). Standard library only.

Everything here takes an open sqlite3 connection (rows as sqlite3.Row or tuples) and returns plain dicts, so it can be tested without the web
server. server.py calls these functions from thin handlers. The rules live in fieldnames.py; nothing here changes a name unless a person
approved it: every write records who, when and why in name_events (append-only). Only schema metadata and profile counts are read; record
values are never touched.
"""
import base64
import binascii
import io
import json
import sqlite3
import zipfile
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone

import fieldnames as fn
import fieldnames_io as fio

ACTOR_MAX = 40
REASON_MAX = 500
STATUSES = ("unreviewed", "approved", "needs_human")
_COL = {t: (t, t + "_status", t + "_verdict") for t in fn.TARGETS}   # name column, status column, verdict-cache column (fixed names, never user input)


class StoreError(Exception):
    def __init__(self, message, status=400, extra=None):
        super(StoreError, self).__init__(message)
        self.message, self.status, self.extra = message, status, extra or {}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def clean_actor(s):
    """Reviewer initials or a short name, recorded with every decision."""
    s = (s or "").strip()
    if not s or len(s) > ACTOR_MAX or any(ord(c) < 32 for c in s):
        raise StoreError("Enter your initials or a short name (up to %d characters) before making a decision." % ACTOR_MAX)
    return s


def _reason(s, required=False):
    s = (s or "").strip()
    if required and not s:
        raise StoreError("A short reason is required.")
    if len(s) > REASON_MAX or any(ord(c) < 32 and c not in "\n\t" for c in s):
        raise StoreError("The reason must be plain text of up to %d characters." % REASON_MAX)
    return s


def has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------- reading the schema, scope, settings and decisions

def load_fields(conn, base_id):
    """{fields, tables} for one base. Fields no longer in the source are left out. 'profiled' = a profile_page call succeeded for the table at or
    after the field was last seen in the schema and no profile failure for the table is still open (a re-ingest means profiling again)."""
    fields = _rows(conn.execute("""
        SELECT f.field_id, f.table_id, f.table_name, f.field_name, f.data_type, f.linked_table_id, f.source_link_field_id,
               f.row_count, f.null_count, f.cardinality, f.cardinality_is_capped,
               (EXISTS(SELECT 1 FROM api_call_log a WHERE a.purpose='profile_page' AND a.success=1 AND a.table_id=f.table_id AND a.timestamp >= f.updated_at)
                AND NOT EXISTS(SELECT 1 FROM runbook_logs r WHERE r.failing_component='profile_page:' || f.table_id AND r.status='OPEN'
                               AND r.timestamp >= f.updated_at)) AS profiled
        FROM airtable_fields f
        WHERE f.base_id=? AND f.updated_at >= (SELECT MAX(x.updated_at) FROM airtable_fields x WHERE x.base_id=f.base_id)
        ORDER BY f.table_id, f.field_id""", (base_id,)))
    tables = []
    if has_table(conn, "airtable_tables"):
        tables = _rows(conn.execute("SELECT table_id, base_id, table_name FROM airtable_tables WHERE base_id=? ORDER BY table_id", (base_id,)))
    seen = {t["table_id"] for t in tables}
    for f in fields:                                    # a database ingested before airtable_tables existed
        if f["table_id"] not in seen:
            seen.add(f["table_id"])
            tables.append({"table_id": f["table_id"], "base_id": base_id, "table_name": f["table_name"]})
    return {"fields": fields, "tables": tables}


def read_scope(conn, base_id):
    """({table_id: {decision, decided_by, decided_at, reason}}, available). Reads table_scope when that table exists (a later migration); until then
    every table is simply undecided. A table with no row is undecided, which is different from out."""
    if not has_table(conn, "table_scope"):
        return {}, False
    cols = {r[1] for r in conn.execute("PRAGMA table_info(table_scope)")}
    if not {"table_id", "decision"} <= cols:
        return {}, False
    out = {}
    for r in conn.execute("SELECT * FROM table_scope WHERE base_id=?", (base_id,)):
        r = dict(r)
        out[r["table_id"]] = {"decision": r["decision"], "decided_by": r.get("decided_by"), "decided_at": r.get("decided_at"), "reason": r.get("reason")}
    return out, True


def settings_from_dict(d):
    d = d or {}
    known = {"table_swap", "char_cleanup", "leading_digits", "table_prefix", "digit_style", "digit_word", "label_keep_quarter", "targets"}
    try:
        return fn.Settings(**{k: v for k, v in d.items() if k in known})
    except (ValueError, TypeError) as e:
        raise StoreError("Those settings are not valid: %s" % e)


def read_settings(conn, base_id):
    """(Settings, saved): the newest saved settings for the base, else the defaults."""
    row = conn.execute("SELECT new_value FROM name_events WHERE subject_type='settings' AND subject_id=? ORDER BY event_id DESC LIMIT 1", (base_id,)).fetchone()
    if not row:
        return fn.Settings(), False
    try:
        return settings_from_dict(json.loads(row[0])), True
    except (ValueError, StoreError):
        return fn.Settings(), False


def save_settings(conn, base_id, settings, actor, reason=""):
    st = settings if isinstance(settings, fn.Settings) else settings_from_dict(settings)
    actor, reason = clean_actor(actor), _reason(reason)
    with conn:
        conn.execute("INSERT INTO name_events(subject_type, subject_id, event_type, new_value, actor, reason) VALUES ('settings', ?, 'settings', ?, ?, ?)",
                     (base_id, json.dumps(st.as_dict(), sort_keys=True), actor, reason or None))
    return st


def _stored(conn, base_id):
    """Saved decisions for the base: approved table names, the field_names rows, and who abandoned what."""
    table_rows = {r["airtable_table_id"]: r for r in _rows(conn.execute(
        "SELECT t.* FROM table_names t JOIN airtable_tables a ON a.table_id=t.airtable_table_id WHERE a.base_id=?", (base_id,)))}
    names = {r["airtable_field_id"]: r for r in _rows(conn.execute(
        "SELECT n.* FROM field_names n JOIN airtable_fields f ON f.field_id=n.airtable_field_id WHERE f.base_id=?", (base_id,)))}
    return table_rows, names


# ---------------------------------------------------------------- the review (everything the page shows)

def _compute(conn, base_id, settings, override=None):
    data = load_fields(conn, base_id)
    if not data["fields"]:
        raise StoreError("No fields are stored for base %s. Ingest its schema in Stage 1 first." % base_id, 404)
    scope, scope_ok = read_scope(conn, base_id)
    out_tables = [tid for tid, v in scope.items() if v["decision"] == "out"]
    table_rows, names = _stored(conn, base_id)
    approved = {tid: r["new_table_name"] for tid, r in table_rows.items() if r["status"] == "approved" and r["new_table_name"]}
    manual = {}
    for fid, r in names.items():
        m = {t: (r[t] or "") for t in fn.TARGETS if r.get(t)}
        if m:
            manual[fid] = m
    for (fid, target), name in (override or {}).items():
        manual.setdefault(fid, {})[target] = name
    abandoned = [fid for fid, r in names.items() if r["abandoned"]]
    res = fn.run(data["fields"], data["tables"], settings, manual=manual, approved_tables=approved, abandoned=abandoned,
                 out_of_scope_tables=out_tables, gate=True)
    return {"data": data, "scope": scope, "scope_ok": scope_ok, "out": set(out_tables), "table_rows": table_rows, "names": names,
            "approved": approved, "manual": manual, "abandoned": abandoned, "res": res}


def review(conn, base_id, settings=None):
    """The full payload for the page. Fields appear only when every in-scope table name is approved (the gate)."""
    saved = True
    if settings is None:
        settings, saved = read_settings(conn, base_id)
    c = _compute(conn, base_id, settings)
    res, data = c["res"], c["data"]
    by_table = defaultdict(int)
    for f in data["fields"]:
        by_table[f["table_id"]] += 1
    tables = []
    for t in sorted(data["tables"], key=lambda x: (x["table_name"].casefold(), x["table_id"])):
        tid = t["table_id"]
        sc = c["scope"].get(tid)
        row = {"table_id": tid, "old": t["table_name"], "field_count": by_table.get(tid, 0),
               "scope": sc["decision"] if sc else "undecided", "scope_by": sc and sc["decided_by"], "scope_at": sc and sc["decided_at"], "scope_reason": sc and sc["reason"]}
        m = res["tables"].get(tid)
        if m:
            sr = c["table_rows"].get(tid) or {}
            row.update({"suggested": m["suggested"], "effective": m["effective"], "approved": m["approved"], "verdict": m["verdict"], "rules": m["rules"],
                        "approved_by": sr.get("approved_by") if m["approved"] else None, "approved_at": sr.get("approved_at") if m["approved"] else None})
        else:                                           # out of scope: no table-name decision is needed
            row.update({"suggested": None, "effective": None, "approved": False, "verdict": None, "rules": [], "approved_by": None, "approved_at": None})
        tables.append(row)
    fields, counts = [], {t: {"approved": 0, "needs_human": 0, "unreviewed": 0, "stale": 0} for t in fn.TARGETS}
    fbi = {f["field_id"]: f for f in data["fields"]}
    for fid, e in res["fields"].items():
        n = c["names"].get(fid) or {}
        row = {"field_id": fid, "table_id": e["table_id"], "table_name": fbi[fid]["table_name"], "original": e["original"], "data_type": fbi[fid]["data_type"],
               "presence": e["presence"], "abandoned": e["abandoned"], "target_type": n.get("target_type"), "import_batch": n.get("import_batch"),
               "abandon": {"by": n.get("abandoned_by"), "at": n.get("abandoned_at"), "reason": n.get("abandoned_reason")} if e["abandoned"] else None,
               "targets": {}}
        for t, r in e["targets"].items():
            status = n.get(_COL[t][1]) or "unreviewed"
            stale = status == "approved" and r["verdict"] != fn.PASS
            tr = {k: r.get(k) for k in ("candidate", "final", "proposed", "suggested", "verdict", "rules", "reasons", "source", "differs_from_rubric",
                                       "rubric_name", "quarter_style")}
            tr.update({"status": status, "approved_name": n.get(t) if status == "approved" else None, "stale": stale})
            row["targets"][t] = tr
            counts[t][status] += 1
            counts[t]["stale"] += 1 if stale else 0
        fields.append(row)
    return {"base_id": base_id, "settings": res["settings"], "settings_saved": saved, "scope_available": c["scope_ok"], "tables": tables,
            "gate": res["gate"], "summary": res["summary"], "counts": counts, "fields": fields, "clusters": res["clusters"],
            "in_scope_tables": len(res["tables"]), "out_of_scope_tables": len(c["out"]),
            "scope_counts": {k: sum(1 for t in tables if t["scope"] == k) for k in ("in", "out", "undecided")}}


# ---------------------------------------------------------------- writing decisions

def _event(conn, subject_type, subject_id, event_type, actor, target=None, old=None, new=None, reason=None):
    conn.execute("INSERT INTO name_events(subject_type, subject_id, event_type, target, old_value, new_value, actor, reason) VALUES (?,?,?,?,?,?,?,?)",
                 (subject_type, subject_id, event_type, target, old, new, actor, reason or None))


def _table_name_problems(name, settings):
    bad = [r for r in fn.violations(name, "planning_name", settings) if not (r == "LEADING_DIGIT" and settings.table_prefix == "plain")]
    return [fn.RULE_BY_ID[r].title for r in bad]


def approve_tables(conn, base_id, actor, items=None, mode=None, reason=""):
    """Approve new table names. items: [{table_id, name?}] (name defaults to the proposal when it is PASS or AUTO_FIXABLE, else it must be typed).
    mode: 'unchanged' (every PASS table, name kept) or 'auto' (every AUTO_FIXABLE table, proposal used); a table that cannot be approved in a bulk
    mode is skipped and reported. Explicit items are all-or-nothing."""
    actor, reason = clean_actor(actor), _reason(reason)
    settings, _ = read_settings(conn, base_id)
    c = _compute(conn, base_id, settings)
    tmap = c["res"]["tables"]
    if mode not in (None, "unchanged", "auto"):
        raise StoreError("Unknown mode.")
    picks, skipped = [], []
    if mode:
        want = fn.PASS if mode == "unchanged" else fn.AUTO_FIXABLE
        for tid, m in tmap.items():
            if m["verdict"] == want and not m["approved"]:
                picks.append((tid, m["old"] if mode == "unchanged" else m["suggested"]))
    for it in items or []:
        tid = str(it.get("table_id") or "")
        if tid not in tmap:
            raise StoreError("Table %s is not an in-scope table of this base." % tid)
        m = tmap[tid]
        name = it.get("name")
        if name is None or str(name).strip() == "":
            if m["verdict"] not in (fn.PASS, fn.AUTO_FIXABLE):
                raise StoreError("The proposed name for '%s' needs a decision: type the name to approve." % m["old"])
            name = m["old"] if m["verdict"] == fn.PASS else m["suggested"]
        picks.append((tid, str(name)))
    if not picks:
        return {"approved": 0, "skipped": [], "message": "Nothing to approve."}
    pending_names = {tid: m["effective"] for tid, m in tmap.items()}
    done = []
    for tid, name in picks:
        probs = _table_name_problems(name, settings)
        clash = [o for o, n in pending_names.items() if o != tid and n.casefold() == name.casefold()]
        if probs or clash:
            why = ", ".join(probs) if probs else "another table already uses that name"
            if items:
                raise StoreError("'%s' cannot be approved: %s." % (name, why))
            skipped.append({"table_id": tid, "name": name, "reason": why})
            continue
        pending_names[tid] = name
        done.append((tid, name))
    with conn:
        for tid, name in done:
            prev = (c["table_rows"].get(tid) or {}).get("new_table_name")
            conn.execute("""INSERT INTO table_names(airtable_table_id, new_table_name, status, approved_by, approved_at, updated_at) VALUES (?,?, 'approved', ?, ?, ?)
                            ON CONFLICT(airtable_table_id) DO UPDATE SET new_table_name=excluded.new_table_name, status='approved',
                            approved_by=excluded.approved_by, approved_at=excluded.approved_at, updated_at=excluded.updated_at""", (tid, name, actor, _now(), _now()))
            _event(conn, "table", tid, "approve", actor, None, prev or tmap[tid]["old"], name, reason)
    return {"approved": len(done), "skipped": skipped}


def revoke_tables(conn, base_id, actor, table_ids, reason=""):
    actor, reason = clean_actor(actor), _reason(reason)
    rows, _names = _stored(conn, base_id)
    n = 0
    with conn:
        for tid in table_ids or []:
            r = rows.get(tid)
            if r and r["status"] == "approved":
                conn.execute("UPDATE table_names SET status='unreviewed', updated_at=? WHERE airtable_table_id=?", (_now(), tid))
                _event(conn, "table", tid, "reject", actor, None, r["new_table_name"], None, reason)
                n += 1
    return {"revoked": n}


def _gate_open(c):
    if not c["res"]["gate"]["ready"]:
        raise StoreError("Approve every in-scope table name before reviewing fields (%d still pending)." % len(c["res"]["gate"]["pending"]), 409)


def _upsert_field(conn, fid, target, name, status, verdict, now):
    nc, sc, vc = _COL[target]
    conn.execute("INSERT INTO field_names(airtable_field_id, %s, %s, %s, validated_at, updated_at) VALUES (?,?,?,?,?,?) "
                 "ON CONFLICT(airtable_field_id) DO UPDATE SET %s=excluded.%s, %s=excluded.%s, %s=excluded.%s, validated_at=excluded.validated_at, updated_at=excluded.updated_at"
                 % (nc, sc, vc, nc, nc, sc, sc, vc, vc), (fid, name, status, verdict, now, now))


def approve_fields(conn, base_id, actor, items=None, mode=None, targets=None, reason=""):
    """Approve names for fields. items: [{field_id, target, name?}] (explicit, all-or-nothing, at most 50; every name is re-validated with the
    field's duplicate group, so a name that would still collide is refused). mode: 'pass', 'auto' or 'pass_and_auto', optionally limited to
    targets; bulk modes only take names the validator already found clean and unique."""
    actor, reason = clean_actor(actor), _reason(reason)
    settings, _ = read_settings(conn, base_id)
    c = _compute(conn, base_id, settings)
    _gate_open(c)
    if mode not in (None, "pass", "auto", "pass_and_auto"):
        raise StoreError("Unknown mode.")
    tgts = [t for t in (targets or fn.TARGETS) if t in fn.TARGETS]
    res = c["res"]["fields"]
    picks = []
    if mode:
        ok = {"pass": (fn.PASS,), "auto": (fn.AUTO_FIXABLE,), "pass_and_auto": (fn.PASS, fn.AUTO_FIXABLE)}[mode]
        for fid, e in res.items():
            if e["abandoned"]:
                continue
            for t in tgts:
                r = e["targets"][t]
                status = (c["names"].get(fid) or {}).get(_COL[t][1]) or "unreviewed"
                if r["verdict"] in ok and status != "approved":
                    picks.append((fid, t, r["final"], False))
    items = items or []
    if len(items) > 50:
        raise StoreError("Approve at most 50 named items at a time; use a bulk mode for the rest.")
    override = {}
    for it in items:
        fid, t = str(it.get("field_id") or ""), str(it.get("target") or "")
        if fid not in res or t not in fn.TARGETS:
            raise StoreError("Unknown field or target.")
        if res[fid]["abandoned"]:
            raise StoreError("That field is abandoned; un-abandon it first.")
        r = res[fid]["targets"][t]
        name = it.get("name")
        name = r["final"] if name is None or str(name).strip() == "" else str(name).strip()
        if r["verdict"] == fn.NEEDS_HUMAN and it.get("name") in (None, "") and not r["proposed"]:
            raise StoreError("'%s' needs a decision: type the name to approve." % r["candidate"])
        override[(fid, t)] = name
        picks.append((fid, t, name, True))
    if override:                                        # one run with the typed names in place: each must be clean and no longer in a duplicate group
        again = _compute(conn, base_id, settings, override)["res"]["fields"]
        for (fid, t), name in override.items():
            r = again[fid]["targets"][t]
            if r["verdict"] != fn.PASS or r["final"] != name:
                why = "; ".join(r["reasons"]) or ("it would become '%s'" % r["final"] if r["final"] != name else "it does not pass the rules")
                raise StoreError("'%s' cannot be approved for the %s of '%s': %s." % (name, t.replace("_", " "), res[fid]["original"], why), 400,
                                 {"field_id": fid, "target": t})
    elif not picks:
        return {"approved": 0, "message": "Nothing to approve."}
    now, n = _now(), 0
    with conn:
        for fid, t, name, _explicit in picks:
            if not _explicit and fn.violations(name, t, settings):
                continue                                # a bulk name must be clean
            prev = (c["names"].get(fid) or {}).get(t) or res[fid]["original"]
            _upsert_field(conn, fid, t, name, "approved", fn.PASS, now)
            _event(conn, "field", fid, "approve", actor, t, prev, name, reason)
            n += 1
    return {"approved": n}


def set_field_status(conn, base_id, actor, items, status, reason=""):
    """Mark names 'needs_human' (flagged for a later decision) or 'unreviewed' (an approval withdrawn). items: [{field_id, target}]."""
    if status not in ("needs_human", "unreviewed"):
        raise StoreError("Unknown status.")
    actor, reason = clean_actor(actor), _reason(reason, required=(status == "unreviewed"))
    settings, _ = read_settings(conn, base_id)
    c = _compute(conn, base_id, settings)
    _gate_open(c)
    now, n = _now(), 0
    with conn:
        for it in items or []:
            fid, t = str(it.get("field_id") or ""), str(it.get("target") or "")
            if fid not in c["res"]["fields"] or t not in fn.TARGETS:
                raise StoreError("Unknown field or target.")
            row = c["names"].get(fid) or {}
            old = row.get(_COL[t][1]) or "unreviewed"
            conn.execute("INSERT INTO field_names(airtable_field_id, %s, updated_at) VALUES (?,?,?) ON CONFLICT(airtable_field_id) DO UPDATE SET %s=excluded.%s, updated_at=excluded.updated_at"
                         % (_COL[t][1], _COL[t][1], _COL[t][1]), (fid, status, now))
            _event(conn, "field", fid, "needs_human" if status == "needs_human" else "reject", actor, t, old, status, reason)
            n += 1
    return {"updated": n}


def abandon(conn, base_id, actor, field_id, reason):
    """A recorded human decision, never automatic: who, when and why. Only a field that was profiled and has no value in any record qualifies
    (a checkbox never does); the server enforces this, not the page."""
    actor, reason = clean_actor(actor), _reason(reason, required=True)
    settings, _ = read_settings(conn, base_id)
    data = load_fields(conn, base_id)
    f = next((x for x in data["fields"] if x["field_id"] == field_id), None)
    if not f:
        raise StoreError("That field is not in this base.", 404)
    p = fn.presence(f)
    if not p["can_abandon"]:
        raise StoreError("This field cannot be abandoned: %s." % (p["abandon_blocked_because"] or "it has values"))
    now = _now()
    with conn:
        conn.execute("""INSERT INTO field_names(airtable_field_id, abandoned, abandoned_by, abandoned_at, abandoned_reason, updated_at) VALUES (?,1,?,?,?,?)
                        ON CONFLICT(airtable_field_id) DO UPDATE SET abandoned=1, abandoned_by=excluded.abandoned_by, abandoned_at=excluded.abandoned_at,
                        abandoned_reason=excluded.abandoned_reason, updated_at=excluded.updated_at""", (field_id, actor, now, reason, now))
        _event(conn, "field", field_id, "abandon", actor, None, None, None, reason)
    return {"abandoned": True}


def unabandon(conn, base_id, actor, field_id, reason):
    actor, reason = clean_actor(actor), _reason(reason, required=True)
    _, names = _stored(conn, base_id)
    if not (names.get(field_id) or {}).get("abandoned"):
        raise StoreError("That field is not abandoned.", 404)
    with conn:
        conn.execute("UPDATE field_names SET abandoned=0, abandoned_by=NULL, abandoned_at=NULL, abandoned_reason=NULL, updated_at=? WHERE airtable_field_id=?", (_now(), field_id))
        _event(conn, "field", field_id, "unabandon", actor, None, None, None, reason)
    return {"abandoned": False}


def import_names(conn, base_id, actor, csv_base64=None, csv_text=None):
    """Import the renamed names by field ID. A name already approved is never overwritten. Returns the problems found (unmatched or repeated IDs,
    fields missing from the file, a stale old_name or type) so they can be flagged."""
    actor = clean_actor(actor)
    if csv_base64 is not None:
        try:
            data = base64.b64decode(csv_base64, validate=True)
        except (binascii.Error, ValueError):
            raise StoreError("The file could not be read.")
        text, note = fio.decode_csv(data)
    elif csv_text is not None:
        text, note = str(csv_text), ""
    else:
        raise StoreError("Choose a CSV file to import.")
    fields = load_fields(conn, base_id)["fields"]
    imp = fio.parse_import(text, fields)
    _, names = _stored(conn, base_id)
    batch = "import-" + _now().replace(" ", "T").replace(":", "")
    now, applied, kept = _now(), 0, 0
    with conn:
        for fid in sorted(set(imp["manual"]) | set(imp["target_types"])):
            row = names.get(fid) or {}
            for t, name in sorted((imp["manual"].get(fid) or {}).items()):
                if not name:
                    continue
                if row.get(_COL[t][1]) == "approved":
                    kept += 1
                    continue
                conn.execute("INSERT INTO field_names(airtable_field_id, %s, import_batch, updated_at) VALUES (?,?,?,?) ON CONFLICT(airtable_field_id) DO UPDATE SET %s=excluded.%s, "
                             "import_batch=excluded.import_batch, updated_at=excluded.updated_at" % (t, t, t), (fid, name, batch, now))
                _event(conn, "field", fid, "import", actor, t, row.get(t), name, batch)
                applied += 1
            if imp["target_types"].get(fid):
                conn.execute("INSERT INTO field_names(airtable_field_id, target_type, import_batch, updated_at) VALUES (?,?,?,?) ON CONFLICT(airtable_field_id) DO UPDATE SET "
                             "target_type=excluded.target_type, import_batch=excluded.import_batch, updated_at=excluded.updated_at", (fid, imp["target_types"][fid], batch, now))
    return {"batch": batch, "rows": imp["rows"], "names_imported": applied, "kept_approved": kept, "problems": imp["problems"][:2000],
            "problem_count": len(imp["problems"]), "note": note}


# ---------------------------------------------------------------- exports

def report_files(conn, base_id, settings=None):
    """{filename: text} for the CSV downloads, built from the saved names (the same builder as the command line)."""
    if settings is None:
        settings, _ = read_settings(conn, base_id)
    c = _compute(conn, base_id, settings)
    manual = {fid: {t: v for t, v in m.items()} for fid, m in c["manual"].items()}
    target_types = {fid: r["target_type"] for fid, r in c["names"].items() if r.get("target_type")}
    imp = {"manual": manual, "target_types": target_types, "problems": []}
    notes = []
    undecided = sum(1 for t in c["data"]["tables"] if t["table_id"] not in c["scope"])
    if not c["scope_ok"]:
        notes.append("Table scope decisions are not stored yet: every table is undecided and is validated.")
    elif undecided:
        notes.append("%d table(s) have no scope decision (undecided) and were validated like in-scope tables." % undecided)
    return fio.build_reports(c["res"], c["data"]["fields"], imp, notes)


def report_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))     # fixed date: the same input gives the same bytes
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, files[name].encode("utf-8-sig") if name.endswith(".csv") or name.endswith(".txt") else files[name].encode("utf-8"))
    return buf.getvalue()
