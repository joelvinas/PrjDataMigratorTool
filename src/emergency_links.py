#!/usr/bin/env python3
"""The links pass of Git-R-Done: set ONE table's link fields in the DEV Planning Workspace, after the tables they point to are loaded. Standard library only.

Git-R-Done skips link fields when it loads a table (a link needs the Planning record IDs of the other table, which do not exist yet), and holds back every field whose value comes
through a link (linkdeps.py). This module is the second visit to the table. It never loads records again (a records reload would create each record twice): the records are already
in Planning and already marked. For one table, in this order:
    1  the link directions: a pair a person has not decided blocks THAT link only (listed with its pair); the table's other links go on
    2  the reference fields: each link field gets its Planning reference field, created in the table's record type and pointing at the other table's record type (createBackReference
       false, the 30 reference fields per record type checked); a link whose target record type is not in Planning yet WAITS, it does not fail
    3  the links: for every record with a finished records pass and no links marker, each Airtable record ID in a link cell becomes the Planning record ID the other record was given; the
       resolved IDs are sent in bulk (up to 100 records per call, a plain list per reference field). A link whose target has no Planning ID yet is WAITING, never failed, and is tried on
       a later run; the links marker is written only when every link of the record was set. A refusal is FAILED, listed by Airtable record ID with the error code only, and the record
       stays pending, so the next run tries only those.
A lookup, rollup or count cannot be created through the API, so the result lists the fields a person creates in Planning by hand (linkdeps.py).

Verified by the owner's Postman run in DEV on 23 November 2026: the reference field create, PATCH with {"data": {field: [record IDs]}} (single and bulk). Provisional: the bulk reply keys of
a success, the 500 connections per field, whether a list replaces or adds to what the field holds.

Rules kept (CLAUDE.md): a record VALUE is read from the saved file in memory only to find the record IDs in link cells, never stored or reported; reports hold field and table names, IDs,
counts and error codes. Every Workfront call goes through planningtransport over workfronthttp. Deterministic: sorted tables, fields and records."""
import sqlite3

import emergency as em
import linkdirections as ld
import loadrun as lr
import loadstate as ls
import planlimits as pl
import planningtransport as pt
import recordload as rl
import recordmap as rm

CONNECTION_CAP = 500         # connections per reference field (Planning API v2 reference; not yet tested)


def link_fields(schema):
    """The link fields of the table this pass handles, sorted: a real link to another record type that nobody abandoned or merged."""
    return sorted([f for f in schema["fields"] if f["data_type"] == "multipleRecordLinks" and f.get("linked_table_id") and not f["abandoned"] and not f["merged"]],
                  key=lambda f: (f["field_name"].lower(), f["field_id"]))


def _directions(conn):
    """{link field ID: True when its pair is decided}, or None when this database has no direction columns (then nothing is gated)."""
    old = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return ld.field_states(conn)
    except Exception:
        return None
    finally:
        conn.row_factory = old


def _loaded_counts(conn, table_id, env, ws):
    """(registered, loaded): how many records of the table are known and how many have a finished records pass in this stage and Workspace."""
    reg = conn.execute("SELECT COUNT(*) FROM source_records WHERE table_id=?", (table_id,)).fetchone()[0]
    done = conn.execute("SELECT COUNT(*) FROM record_pass_markers m JOIN source_records s ON s.source_record_uid=m.source_record_uid WHERE s.table_id=? AND m.pass='records' AND m.environment=? AND m.workspace_key=?",
                        (table_id, env, ws or "")).fetchone()[0]
    return reg, done


def _pair(f, schema_fields):
    """The partner field of a link, for the 'blocked' reason (names only)."""
    inv = f.get("inverse_link_field_id")
    other = next((x for x in schema_fields if x["field_id"] == inv), None)
    return other["field_name"] if other else None


# ------------------------------------------------------------------ the list of tables that need a second visit (nothing is sent)
def revisit(conn, base_id, table_id, env_name, ws):
    """What a second visit to one table would do, from the schema and the ID map only. {table_id, table_name, in_planning, records: {registered, loaded}, links_pending, link_fields: [...],
    waits_on: [{table_id, table_name, in_planning, registered, loaded, link_fields}], held_back: [...], undecided: [...], needs_visit, advice: [plain sentences]}."""
    schema = em.load_schema(conn, base_id, table_id)
    cat = em.formula_catalog(conn, base_id)
    state = ls.DbState(conn, base_id, ws)
    states = _directions(conn)
    in_planning = bool(state.get("P-T-" + table_id, env_name))
    reg, loaded = _loaded_counts(conn, table_id, env_name, ws)
    made = {r[0] for r in conn.execute("SELECT source_record_uid FROM record_pass_markers WHERE pass='records' AND environment=? AND workspace_key=?", (env_name, ws or ""))}
    links_pending = len([u for u, _r in rm.pending_records(conn, base_id, table_id, "links", env_name, "planning", ws or "") if u in made]) if reg else None
    rows, waits, undecided = [], {}, []
    for f in link_fields(schema):
        tgt = f["linked_table_id"]
        dec = None if states is None else states.get(f["field_id"])
        row = {"field_id": f["field_id"], "field_name": f["field_name"], "target_table_id": tgt, "target_table_name": cat.tables.get(tgt, tgt),
               "target_in_planning": bool(state.get("P-T-" + tgt, env_name)), "reference_field_exists": bool(state.get("P-F-" + f["field_id"], env_name)),
               "direction": "unknown" if dec is None else ("decided" if dec else "undecided")}
        rows.append(row)
        if dec is False:
            undecided.append({"field_id": f["field_id"], "field_name": f["field_name"], "partner": _pair(f, schema["fields"]), "target_table_name": row["target_table_name"]})
        w = waits.setdefault(tgt, {"table_id": tgt, "table_name": row["target_table_name"], "in_planning": row["target_in_planning"], "link_fields": 0})
        w["link_fields"] += 1
    for h in em.link_holds(cat, table_id).values():
        if h.get("target_table_id") and h["target_table_id"] not in waits:
            waits[h["target_table_id"]] = {"table_id": h["target_table_id"], "table_name": h["target_table_name"], "in_planning": bool(state.get("P-T-" + h["target_table_id"], env_name)), "link_fields": 0}
    for t, w in waits.items():
        w["registered"], w["loaded"] = _loaded_counts(conn, t, env_name, ws)
    held = [dict(h, field_id=fid, field_name=cat.f[fid]["field_name"]) for fid, h in sorted(em.link_holds(cat, table_id).items(), key=lambda kv: (cat.f[kv[0]]["field_name"].lower(), kv[0]))]
    waits_on = [w for _t, w in sorted(waits.items(), key=lambda kv: (kv[1]["table_name"].lower(), kv[0])) if w["table_id"] != table_id]
    advice = []
    if not in_planning:
        advice.append("%s is not in Planning yet: run it in Git-R-Done first." % schema["table_name"])
    for w in waits_on:
        if not w["in_planning"]:
            advice.append("Run %s first: %d link field(s) of %s point to it, and it is not in Planning yet." % (w["table_name"], w["link_fields"], schema["table_name"]))
        elif w["registered"] and w["loaded"] < w["registered"]:
            advice.append("%s has %d of %d records loaded: links to the others will wait." % (w["table_name"], w["loaded"], w["registered"]))
    if undecided:
        advice.append("Decide the direction of %s on the Link directions page: an undecided link is left out of the links pass (only that link)." % ", ".join(x["field_name"] for x in undecided))
    missing = [r for r in rows if not r["reference_field_exists"]]
    if missing:
        advice.append("%d reference field(s) will be created in %s." % (len(missing), schema["table_name"]))
    if held:
        advice.append("%d field(s) get their value through a link: they are not loaded, and a person creates them in Planning once the links are set." % len(held))
    needs = bool(rows) and (bool(missing) or bool(links_pending) or bool(undecided))
    return {"table_id": table_id, "table_name": schema["table_name"], "in_planning": in_planning, "records": {"registered": reg, "loaded": loaded}, "links_pending": links_pending,
            "link_fields": rows, "waits_on": waits_on, "held_back": held, "undecided": undecided, "needs_visit": needs, "advice": advice, "environment": env_name, "workspace_id": ws}


# ------------------------------------------------------------------ the run
def _row(f, cat):
    return {"field_id": f["field_id"], "field_name": f["field_name"], "target_table_id": f["linked_table_id"], "target_table_name": cat.tables.get(f["linked_table_id"], f["linked_table_id"]),
            "status": "", "reason": "", "reference_field_id": "", "sent_name": "", "single": f.get("prefers_single_record_link") == 1, "log_span": None}


def run_links(conn, base_id, table_id, ctx, tr, extract_dir, calls, send=True, progress=None, should_stop=None, batch=None, reader=rl.read_extract):
    """One links run for one table. send False only counts (no Workfront call is made). Returns the result dict the page shows and the reports are made from."""
    progress = progress or (lambda p: None)
    stop = should_stop or (lambda: False)
    schema = em.load_schema(conn, base_id, table_id)
    cat = em.formula_catalog(conn, base_id)
    state = ls.DbState(conn, base_id, ctx.ws)
    env = ctx.env
    res = {"kind": "links", "base_id": base_id, "table_id": table_id, "table_name": schema["table_name"], "stage": ctx.stage, "workspace_id": ctx.ws, "started_at": em.utc_now(), "finished_at": None,
           "base_name": "", "workspace_name": "", "dry_run": not send, "record_type_id": None, "reference_fields": [], "waiting_by_table": [], "blocked": [], "failures": [], "failure_count": 0,
           "held_back": [], "notes": [], "cancelled": False, "calls": calls.entries,
           "counts": {"reference_created": 0, "reference_existing": 0, "reference_failed": 0, "reference_waiting": 0, "blocked": 0, "records_pending": 0, "records_complete": 0,
                      "records_partial": 0, "records_waiting": 0, "records_failed": 0, "records_no_links": 0, "links_set": 0, "links_waiting": 0}}
    c = res["counts"]
    rt_id = state.get("P-T-" + table_id, env.name)
    if not rt_id:
        raise em.EmergencyError("This table's record type is not in Planning yet (stage %s, Workspace %s). Run the table in Git-R-Done first." % (ctx.stage, ctx.ws), 409)
    res["record_type_id"] = rt_id
    holds = em.link_holds(cat, table_id)
    res["held_back"] = [dict(h, field_id=fid, field_name=cat.f[fid]["field_name"]) for fid, h in sorted(holds.items(), key=lambda kv: (cat.f[kv[0]]["field_name"].lower(), kv[0]))]
    states = _directions(conn)
    fields = link_fields(schema)
    if not fields:
        res["notes"].append("This table has no link field that this pass handles.")

    # ---- 1 and 2: the directions and the reference fields
    calls.phase, calls.field_id = "reference_fields", None
    taken, refs_in_type = set(), 0
    if send and fields:
        try:
            listing = tr.list_fields(rt_id)
            taken = {x["name"].strip().lower() for x in listing}
            refs_in_type = sum(1 for x in listing if x["type"] == "reference")
        except Exception as e:
            res["notes"].append("The fields of the record type could not be listed (%s); the reference field limit and name clashes were not checked." % lr.scrub(e))
    active = []                                            # [(field row, Planning reference field ID or None when a dry run would create it)]
    inactive = []
    for f in fields:
        if stop():
            res["cancelled"] = True
            break
        row = _row(f, cat)
        res["reference_fields"].append(row)
        fid, tgt = f["field_id"], f["linked_table_id"]
        calls.field_id = fid
        if states is not None and states.get(fid) is False:
            row.update(status="blocked", reason="the direction of this link pair has not been decided on the Link directions page" + (" (pair: %s)" % _pair(f, schema["fields"]) if _pair(f, schema["fields"]) else ""))
            res["blocked"].append({"field_id": fid, "field_name": f["field_name"], "reason": row["reason"]})
            c["blocked"] += 1
            inactive.append(row)
            continue
        target_rt = state.get("P-T-" + tgt, env.name)
        if not target_rt:
            row.update(status="waiting", reason="%s is not in Planning yet: run it in Git-R-Done first" % row["target_table_name"])
            c["reference_waiting"] += 1
            inactive.append(row)
            continue
        have = state.get("P-F-" + fid, env.name)
        if have:
            row.update(status="existing", reference_field_id=have, sent_name=f["field_name"])
            c["reference_existing"] += 1
            active.append((row, have))
            continue
        if not send:
            row.update(status="would_create", reason="the reference field would be created")
            active.append((row, None))
            continue
        if refs_in_type >= pl.MAX_REFERENCE:
            row.update(status="failed", reason="the record type already has %d reference fields, the most Planning allows" % pl.MAX_REFERENCE)
            c["reference_failed"] += 1
            inactive.append(row)
            continue
        name = f["field_name"]
        sent = em.CONFLICT + name if name.strip().lower() in taken else name
        item = em._item("P-F-" + fid, "field", sent, "P-T-" + table_id, table_id, fid, "reference", {"linked_table_id": tgt, "single": row["single"], "airtable_type": "multipleRecordLinks"})
        item["requires"] = ["P-T-" + tgt]
        ok, wid, err, _how = em._try(state, env, tr, item)
        if ok:
            row.update(status="created", reference_field_id=wid, sent_name=sent, reason="" if sent == name else "the name is taken by a field the record type already has, so it was created as %s" % sent)
            c["reference_created"] += 1
            refs_in_type += 1
            taken.add(sent.strip().lower())
            active.append((row, wid))
        else:
            row.update(status="failed", reason=err or "Planning refused the reference field", sent_name=sent)
            c["reference_failed"] += 1
            inactive.append(row)
    calls.field_id = None
    for row in res["reference_fields"]:
        row["log_span"] = calls.span(row["field_id"])

    # ---- 3: the links, record by record, in bulk
    calls.phase = "links"
    by_target = {}
    if fields and not res["cancelled"]:
        try:
            doc = reader(extract_dir, base_id, table_id)
        except rl.RecordLoadError as e:
            doc = None
            res["notes"].append("The links could not be set: %s" % e)
        if doc is not None:
            _set_links(conn, base_id, table_id, doc, rt_id, fields, active, inactive, ctx, tr, send, res, by_target, progress, stop, batch)
    for t, w in sorted(by_target.items(), key=lambda kv: (kv[1]["table_name"].lower(), kv[0])):
        w["records"] = len(w.pop("record_ids"))
        w["registered"], w["loaded"] = _loaded_counts(conn, t, env.name, ctx.ws)
        w["in_planning"] = bool(state.get("P-T-" + t, env.name))
        res["waiting_by_table"].append(w)
    res["finished_at"] = em.utc_now()
    res["call_count"] = len(calls.entries)
    return res


def _set_links(conn, base_id, table_id, doc, rt_id, fields, active, inactive, ctx, tr, send, res, by_target, progress, stop, batch):
    c, env = res["counts"], ctx.env
    by_id = {r.get("id"): r for r in doc["records"] if isinstance(r, dict)}
    done_records = {r[0] for r in conn.execute("SELECT source_record_uid FROM record_pass_markers WHERE pass='records' AND environment=? AND workspace_key=?", (env.name, ctx.ws or ""))}
    pend = [(u, r) for u, r in rm.pending_records(conn, base_id, table_id, "links", env.name, "planning", ctx.ws or "") if u in done_records]
    c["records_pending"] = len(pend)
    mapped = rl._mapped(conn, env.name, ctx.ws)
    lookups = {t: rm.uid_map(conn, base_id, t) for t in sorted({r["target_table_id"] for r, _w in active})}
    field_by_id = {f["field_id"]: f for f in fields}
    to_send, marks = [], []

    def wait(target, rid, n):
        w = by_target.setdefault(target, {"table_id": target, "table_name": next((r["target_table_name"] for r in res["reference_fields"] if r["target_table_id"] == target), target), "links": 0, "record_ids": set()})
        w["links"] += n
        w["record_ids"].add(rid)

    for uid, rid in pend:
        rec = by_id.get(rid)
        pid = mapped.get(uid)
        if rec is None or not pid:
            c["records_failed"] += 1
            res["failure_count"] += 1
            res["failures"].append({"record_id": rid, "reason": "the record is not in the saved file" if rec is None else "the record has no Planning ID in the map"})
            continue
        data, complete, nlinks, too_many = {}, True, 0, False
        src = rec.get("fields") or {}
        for row, wid in active:
            f = field_by_id[row["field_id"]]
            ids, missing = rl.link_values({"field_id": f["field_id"]}, rec, lookups[row["target_table_id"]], mapped, [])
            if len(ids) > CONNECTION_CAP:
                too_many = True
            if ids:
                data[wid or "<new reference field>"] = ids
                nlinks += len(ids)
            if missing:
                complete = False
                c["links_waiting"] += missing
                wait(row["target_table_id"], rid, missing)
        for row in inactive:                               # a link field that is blocked, waiting for its table or failed: its values wait too
            vals = src.get(row["field_id"])
            n = len([v for v in (vals if isinstance(vals, list) else []) if isinstance(v, str)])
            if n:
                complete = False
                c["links_waiting"] += n
                wait(row["target_table_id"], rid, n)
        if too_many:
            c["records_failed"] += 1
            res["failure_count"] += 1
            res["failures"].append({"record_id": rid, "reason": "a link field holds more than %d links, the most Planning allows (not yet tested)" % CONNECTION_CAP})
            continue
        if data:
            to_send.append((uid, rid, pid, data, complete, nlinks))
        elif complete:
            c["records_no_links"] += 1
            marks.append(uid)
        else:
            c["records_waiting"] += 1
    if send and marks:
        rm.mark_pass(conn, marks, "links", env.name, "planning", ctx.ws or "")
    size = max(1, min(pt.BULK_MAX, int(batch or pt.BULK_MAX)))
    batches = [to_send[i:i + size] for i in range(0, len(to_send), size)]
    for n, part in enumerate(batches, 1):
        if stop():
            res["cancelled"] = True
            break
        if not send:
            for _u, _r, _p, _d, complete, nl in part:
                c["records_complete" if complete else "records_partial"] += 1
                c["links_set"] += nl
            continue
        done = _send_part(tr, rt_id, part, res)
        okay = [x for x in part if x[1] in done]
        for uid, rid, _p, _d, complete, nl in okay:
            c["records_complete" if complete else "records_partial"] += 1
            c["links_set"] += nl
        rm.mark_pass(conn, [x[0] for x in okay if x[4]], "links", env.name, "planning", ctx.ws or "")
        progress({"phase": "links", "done": n, "total": len(batches), "item": "records"})
    res["failures"] = res["failures"][:200]


def _send_part(tr, rt_id, part, res):
    """Send one bulk update and return the set of Airtable record IDs Planning accepted. A failure is added to res (record ID and short reason only). A call refused as a whole is split and each
    record is sent alone, so one bad record does not hold up the others; an answer that cannot be matched fails all of them with that reason (they may have been changed: check, then run again)."""
    c = res["counts"]
    try:
        got = tr.patch_records_bulk(rt_id, [(pid, data) for _u, _r, pid, data, _c, _n in part])
    except pt.UnmatchedReply as e:
        return _fail_all(part, res, lr.scrub(e))
    except Exception as e:
        if len(part) == 1:
            return _fail_all(part, res, lr.scrub(e))
        ok = set()
        for item in part:
            ok |= _send_part(tr, rt_id, [item], res)
        return ok
    ok = set()
    for (_u, rid, _p, _d, _c2, _n), g in zip(part, got):
        if isinstance(g, dict) and g.get("ok"):
            ok.add(rid)
        else:
            c["records_failed"] += 1
            res["failure_count"] += 1
            res["failures"].append({"record_id": rid, "reason": lr.scrub((g or {}).get("error") if isinstance(g, dict) else None) or "Planning gave no result for this record"})
    return ok


def _fail_all(part, res, reason):
    for _u, rid, _p, _d, _c2, _n in part:
        res["counts"]["records_failed"] += 1
        res["failure_count"] += 1
        res["failures"].append({"record_id": rid, "reason": reason})
    return set()


# ------------------------------------------------------------------ the table list: which tables are done, which need another pass, and in what order to take them
def visit_order(order, again, forward):
    """The order in which to take the tables. `order` is the load order (table IDs), `again` the set of tables that were run and need another pass, `forward` {table ID: the tables its links
    point to that come LATER in the load order}. A table that needs another pass moves to just after the last of those tables, because its links can only be set once they are loaded;
    it never moves earlier, and every table is judged by the load order (not by where earlier moves left the others), so two tables that link to each other do not swap forever.
    Example: 5, 8, 6 with Table 5 needing a pass after Table 6 becomes 8, 6, 5."""
    out = list(order)
    for t in order:
        targets = [x for x in forward.get(t, []) if x in out]
        if t in again and targets:
            last, i = max(out.index(x) for x in targets), out.index(t)
            if last > i:
                out.pop(i)
                out.insert(last, t)
    return out


def table_states(conn, base_id, env_name, ws, order):
    """{table ID: {state, reasons, waits_on}} for the tables of `order` (the load order, table IDs). state is 'new' (no record type in Planning yet), 'done' (100%: nothing more to do) or
    'again' (run, but another pass is needed). A run table needs another pass when its records are not all loaded, or it has link fields whose reference field is not made yet or whose
    links are not all set. A field held back for a person to create by hand does not count: it is a person's step, listed on the Links card. `waits_on` lists the tables its links point
    to that come later in `order`. Schema and ID map only; nothing is sent."""
    ws = ws or ""
    pos = {t: i for i, t in enumerate(order)}
    rts = {r[0] for r in conn.execute("SELECT table_id FROM load_id_map WHERE system='planning' AND kind='record_type' AND status='created' AND workfront_id IS NOT NULL AND environment=? AND workspace_key=? "
                                      "AND base_id=? AND table_id IS NOT NULL", (env_name, ws, base_id))}
    made = {r[0] for r in conn.execute("SELECT airtable_field_id FROM load_id_map WHERE system='planning' AND kind='field' AND status='created' AND workfront_id IS NOT NULL AND environment=? AND workspace_key=? "
                                       "AND base_id=? AND airtable_field_id IS NOT NULL", (env_name, ws, base_id))}
    abandoned = set()
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='field_names'").fetchone():
        abandoned = {r[0] for r in conn.execute("SELECT airtable_field_id FROM field_names WHERE abandoned=1")}
    links = {}
    for r in conn.execute("SELECT table_id, field_id, linked_table_id FROM airtable_fields WHERE base_id=? AND data_type='multipleRecordLinks' AND linked_table_id IS NOT NULL ORDER BY table_id, field_id", (base_id,)):
        if r[1] not in abandoned:
            links.setdefault(r[0], []).append((r[1], r[2]))
    expected = {r[0]: r[1] or 0 for r in conn.execute("SELECT table_id, MAX(row_count) FROM airtable_fields WHERE base_id=? GROUP BY table_id", (base_id,))}
    registered = {r[0]: r[1] for r in conn.execute("SELECT table_id, COUNT(*) FROM source_records WHERE base_id=? GROUP BY table_id", (base_id,))}
    marked = {r[0]: r[1] for r in conn.execute("SELECT s.table_id, COUNT(*) FROM source_records s JOIN record_pass_markers m ON m.source_record_uid=s.source_record_uid AND m.pass='records' AND m.environment=? "
                                              "AND m.workspace_key=? WHERE s.base_id=? GROUP BY s.table_id", (env_name, ws, base_id))}
    pending = {r[0]: r[1] for r in conn.execute("SELECT s.table_id, COUNT(*) FROM source_records s JOIN record_pass_markers m ON m.source_record_uid=s.source_record_uid AND m.pass='records' AND m.environment=? "
                                                "AND m.workspace_key=? WHERE s.base_id=? AND NOT EXISTS (SELECT 1 FROM record_pass_markers k WHERE k.source_record_uid=s.source_record_uid AND k.pass='links' "
                                                "AND k.environment=? AND k.workspace_key=?) GROUP BY s.table_id", (env_name, ws, base_id, env_name, ws))}
    out = {}
    for t in order:
        lk = links.get(t, [])
        forward = sorted({tgt for _f, tgt in lk if tgt != t and tgt in pos and pos[tgt] > pos[t]}, key=lambda x: pos[x])
        if t not in rts:
            out[t] = {"state": "new", "reasons": [], "waits_on": forward}
            continue
        why = []
        reg = registered.get(t, 0)
        if reg and marked.get(t, 0) < reg:
            why.append("%d record(s) are not loaded yet" % (reg - marked.get(t, 0)))
        elif not reg and expected.get(t, 0) > 0:
            why.append("no record has been loaded")
        missing = [f for f, _tgt in lk if f not in made]
        if missing:
            why.append("%d reference field(s) are not made yet" % len(missing))
        if lk and pending.get(t, 0):
            why.append("%d record(s) still have links to set" % pending[t])
        out[t] = {"state": "again" if why else "done", "reasons": why, "waits_on": forward}
    return out
