#!/usr/bin/env python3
"""The record ID map (migration 017): one surrogate ID per Airtable record, and everything else points to it. Standard library only.

    source_records      one row per Airtable record: base ID, table ID, record ID (unique together) and an integer surrogate ID
    target_mappings     one row per new Workfront record (consolidation and fan-out are just more rows against the same surrogate)
    record_pass_markers what is DONE for a record in a stage (records, links, attachments, core_backfill), so a restart never repeats it
    attachment_map      each Airtable attachment ID paired with its Core document ID, with sizes and a hash, so a file is never uploaded twice

Rules kept here:
  * Holds IDs, statuses, sizes and hashes ONLY. No field value, no file name, no link (a link expires and is not secret-free). The record IDs come
    from the saved record files, and only the IDs are read from them.
  * Deterministic. Records are registered in sorted order (base, table, record ID), so the same input on a fresh database gives the same surrogate IDs.
    There is no random UUID: the app has no randomness at runtime.
  * Idempotent. Registering, mapping, marking and planning an attachment again changes nothing and never duplicates.
  * A later pass may only be marked for a record whose records pass is done (a link cannot be set on a record that does not exist yet).
  * An error message is scrubbed of anything that looks like a credential before it is stored.
"""
import re

import loadrun as lr

PASSES = ("records", "links", "attachments", "core_backfill")
SYSTEMS = ("planning", "core")
ATTACHMENT_STATUSES = ("pending", "downloaded", "uploaded", "failed")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class RecordMapError(Exception):
    def __init__(self, message, status=400):
        super(RecordMapError, self).__init__(message)
        self.message, self.status = message, status


def available(conn):
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {"source_records", "target_mappings", "record_pass_markers", "attachment_map"} <= have


def _need(conn):
    if not available(conn):
        raise RecordMapError("The record ID map needs migration 017 on this database.", 409)


def _env(env):
    if env not in lr.ENVIRONMENTS:
        raise RecordMapError("The environment must be one of: %s." % ", ".join(lr.ENVIRONMENTS))
    return env


def _ws(system, workspace_key):
    """Planning rows belong to a Workspace (a wiped and recreated Workspace must not collide with the first); Core rows use ''."""
    key = workspace_key or ""
    if system not in SYSTEMS:
        raise RecordMapError("The system must be planning or core.")
    if system == "planning" and not key:
        raise RecordMapError("A Planning row needs the Planning Workspace ID.")
    if system == "core" and key:
        raise RecordMapError("A Core row has no Workspace: leave it empty.")
    return key


def valid_record_id(rid):
    return isinstance(rid, str) and bool(_ID.match(rid))


# ------------------------------------------------------------------ the registry
def register(conn, base_id, table_id, record_ids):
    """Register records in sorted order. Returns {total, new, already, skipped_invalid}. A record already registered keeps its surrogate ID."""
    _need(conn)
    if not conn.execute("SELECT 1 FROM airtable_tables WHERE table_id=? AND base_id=?", (table_id, base_id)).fetchone():
        raise RecordMapError("That table is not in the database for this base. Ingest the schema first.", 404)
    ids = sorted({r for r in record_ids if valid_record_id(r)})
    invalid = len({r for r in record_ids if not valid_record_id(r)})
    have = {r[0] for r in conn.execute("SELECT record_id FROM source_records WHERE base_id=? AND table_id=?", (base_id, table_id))}
    fresh = [r for r in ids if r not in have]            # only the new ones are inserted: an ignored insert would still use up an autoincrement number
    with conn:
        conn.executemany("INSERT INTO source_records(base_id, table_id, record_id) VALUES (?,?,?)", [(base_id, table_id, r) for r in fresh])
    return {"total": len(ids), "new": len(fresh), "already": len(ids) - len(fresh), "skipped_invalid": invalid}


def uid_map(conn, base_id, table_id):
    """{record_id: surrogate ID} for one table."""
    _need(conn)
    return {r[0]: r[1] for r in conn.execute("SELECT record_id, source_record_uid FROM source_records WHERE base_id=? AND table_id=? ORDER BY record_id", (base_id, table_id))}


def lookup(conn, base_id, table_id, record_id):
    _need(conn)
    r = conn.execute("SELECT source_record_uid FROM source_records WHERE base_id=? AND table_id=? AND record_id=?", (base_id, table_id, record_id)).fetchone()
    return r[0] if r else None


def _known(conn, uid):
    if not conn.execute("SELECT 1 FROM source_records WHERE source_record_uid=?", (uid,)).fetchone():
        raise RecordMapError("Record %r is not registered." % (uid,), 404)


# ------------------------------------------------------------------ target mappings
def add_target(conn, uid, system, env, workspace_key, target_kind, target_record_id):
    """Record that source record `uid` became target record `target_record_id`. Returns True when new. The same mapping twice is one row."""
    _need(conn)
    _known(conn, uid)
    key = _ws(system, workspace_key)
    kind, tid = str(target_kind or "").strip(), str(target_record_id or "").strip()
    if not kind or len(kind) > 40 or not tid or len(tid) > 100 or any(ord(c) < 32 for c in kind + tid):
        raise RecordMapError("A target needs a kind (for example record, project, request, task) and the new Workfront ID.")
    with conn:
        n = conn.execute("INSERT OR IGNORE INTO target_mappings(source_record_uid, system, environment, workspace_key, target_kind, target_record_id) VALUES (?,?,?,?,?,?)",
                         (uid, system, _env(env), key, kind, tid)).rowcount
    return n == 1


def targets_of(conn, uid, system, env, workspace_key=""):
    """[(target_kind, target_record_id)] sorted: one source record may have several (fan-out)."""
    _need(conn)
    key = _ws(system, workspace_key)
    return [(r[0], r[1]) for r in conn.execute("SELECT target_kind, target_record_id FROM target_mappings WHERE source_record_uid=? AND system=? AND environment=? AND workspace_key=? "
                                               "ORDER BY target_kind, target_record_id", (uid, system, _env(env), key))]


def sources_of(conn, system, env, target_record_id, workspace_key=""):
    """Which Airtable records a Workfront record came from (consolidation: several): [(base_id, table_id, record_id)] sorted."""
    _need(conn)
    key = _ws(system, workspace_key)
    return [tuple(r) for r in conn.execute(
        "SELECT s.base_id, s.table_id, s.record_id FROM target_mappings m JOIN source_records s ON s.source_record_uid=m.source_record_uid "
        "WHERE m.system=? AND m.environment=? AND m.workspace_key=? AND m.target_record_id=? ORDER BY s.base_id, s.table_id, s.record_id", (system, _env(env), key, str(target_record_id)))]


# ------------------------------------------------------------------ pass markers
def mark_pass(conn, uids, pass_name, env, system, workspace_key=""):
    """Mark a pass done for these records. Returns the number newly marked. A later pass needs the records pass done first."""
    _need(conn)
    if pass_name not in PASSES:
        raise RecordMapError("The pass must be one of: %s." % ", ".join(PASSES))
    key = _ws(system, workspace_key)
    env = _env(env)
    uids = sorted(set(uids))
    for u in uids:
        _known(conn, u)
    if pass_name != "records" and uids:
        done = set()
        for i in range(0, len(uids), 500):                 # chunks: SQLite limits how many values one statement may take
            part = uids[i:i + 500]
            done.update(r[0] for r in conn.execute("SELECT source_record_uid FROM record_pass_markers WHERE pass='records' AND environment=? AND workspace_key=? AND source_record_uid IN (%s)"
                                                   % ",".join("?" * len(part)), [env, key] + part))
        missing = [u for u in uids if u not in done]
        if missing:
            raise RecordMapError("%d record(s) have no finished records pass in %s, so the %s pass cannot be marked for them yet." % (len(missing), env, pass_name))
    n = 0
    with conn:
        for u in uids:
            n += conn.execute("INSERT OR IGNORE INTO record_pass_markers(source_record_uid, pass, environment, workspace_key) VALUES (?,?,?,?)", (u, pass_name, env, key)).rowcount
    return n


def pending_records(conn, base_id, table_id, pass_name, env, system, workspace_key=""):
    """Registered records of the table with no marker for the pass: what a restart still has to do. [(surrogate ID, record ID)] sorted by record ID."""
    _need(conn)
    if pass_name not in PASSES:
        raise RecordMapError("The pass must be one of: %s." % ", ".join(PASSES))
    key = _ws(system, workspace_key)
    return [(r[0], r[1]) for r in conn.execute(
        "SELECT s.source_record_uid, s.record_id FROM source_records s WHERE s.base_id=? AND s.table_id=? AND NOT EXISTS ("
        "SELECT 1 FROM record_pass_markers m WHERE m.source_record_uid=s.source_record_uid AND m.pass=? AND m.environment=? AND m.workspace_key=?) ORDER BY s.record_id",
        (base_id, table_id, pass_name, _env(env), key))]


# ------------------------------------------------------------------ attachments
def plan_attachment(conn, uid, field_id, attachment_id, env, api_size=None):
    """Note an attachment that has to move. Returns its row ID (the same row if it was planned before: a rerun never makes a second one)."""
    _need(conn)
    _known(conn, uid)
    if not (valid_record_id(str(field_id)) and valid_record_id(str(attachment_id))):
        raise RecordMapError("The field ID and attachment ID must be plain Airtable IDs.")
    if api_size is not None and (not isinstance(api_size, int) or api_size < 0):
        raise RecordMapError("The reported size must be a whole number of bytes.")
    env = _env(env)
    with conn:
        conn.execute("INSERT OR IGNORE INTO attachment_map(source_record_uid, airtable_field_id, airtable_attachment_id, environment, api_size_bytes) VALUES (?,?,?,?,?)",
                     (uid, field_id, attachment_id, env, api_size))
    return conn.execute("SELECT attachment_row_id FROM attachment_map WHERE source_record_uid=? AND airtable_field_id=? AND airtable_attachment_id=? AND environment=?",
                        (uid, field_id, attachment_id, env)).fetchone()[0]


def _att(conn, row_id):
    r = conn.execute("SELECT * FROM attachment_map WHERE attachment_row_id=?", (row_id,)).fetchone()
    if r is None:
        raise RecordMapError("No such attachment row.", 404)
    return r


def attachment_downloaded(conn, row_id, nbytes, sha256):
    """The file was downloaded. Its size is checked against the size Airtable reported; a difference fails the row (the hash proves the transfer and the
    upload, not a match with the source, because Airtable gives no checksum)."""
    r = _att(conn, row_id)
    if not isinstance(nbytes, int) or nbytes < 0 or not _SHA256.match(str(sha256 or "")):
        raise RecordMapError("A download needs its size in bytes and a SHA-256 hash (64 hex characters).")
    if r["status"] == "uploaded":
        raise RecordMapError("This attachment is already uploaded; it is never downloaded or uploaded twice.")
    bad = r["api_size_bytes"] is not None and r["api_size_bytes"] != nbytes
    with conn:
        conn.execute("UPDATE attachment_map SET downloaded_bytes=?, sha256=?, status=?, last_error=?, updated_at=CURRENT_TIMESTAMP WHERE attachment_row_id=?",
                     (nbytes, sha256, "failed" if bad else "downloaded", ("the downloaded size (%d bytes) differs from the size Airtable reported (%d bytes)" % (nbytes, r["api_size_bytes"])) if bad else None, row_id))
    return not bad


def attachment_uploaded(conn, row_id, core_document_id):
    r = _att(conn, row_id)
    doc = str(core_document_id or "").strip()
    if not doc or len(doc) > 100:
        raise RecordMapError("An upload needs the Core document ID.")
    if r["status"] == "uploaded":
        if r["core_document_id"] == doc:
            return False
        raise RecordMapError("This attachment is already uploaded as a different Core document; it is never uploaded twice.")
    if r["status"] != "downloaded":
        raise RecordMapError("Only a downloaded, size-checked attachment can be marked uploaded.")
    with conn:
        conn.execute("UPDATE attachment_map SET core_document_id=?, status='uploaded', last_error=NULL, updated_at=CURRENT_TIMESTAMP WHERE attachment_row_id=?", (doc, row_id))
    return True


def attachment_failed(conn, row_id, message):
    _att(conn, row_id)
    with conn:
        conn.execute("UPDATE attachment_map SET status='failed', last_error=?, updated_at=CURRENT_TIMESTAMP WHERE attachment_row_id=? AND status!='uploaded'", (lr.scrub(message), row_id))


def attachment_retry(conn, row_id):
    """A failed upload (the file was downloaded and its size checked) goes back to 'downloaded' so the next run tries it again. A download that failed in Stage 1
    has no file and stays failed. Returns True when the row was reset."""
    r = _att(conn, row_id)
    if r["status"] != "failed" or r["sha256"] is None or r["downloaded_bytes"] is None:
        return False
    if r["api_size_bytes"] is not None and r["api_size_bytes"] != r["downloaded_bytes"]:
        return False                                                  # the size check failed: this file is not the one Airtable described, so it is never sent
    with conn:
        conn.execute("UPDATE attachment_map SET status='downloaded', last_error=NULL, updated_at=CURRENT_TIMESTAMP WHERE attachment_row_id=?", (row_id,))
    return True


def attachments_with_status(conn, env, status, limit=500):
    """Rows still to do (or failed), oldest first. Each is IDs, sizes and status only."""
    _need(conn)
    if status not in ATTACHMENT_STATUSES:
        raise RecordMapError("The status must be one of: %s." % ", ".join(ATTACHMENT_STATUSES))
    return [dict(r) for r in conn.execute("SELECT * FROM attachment_map WHERE environment=? AND status=? ORDER BY attachment_row_id LIMIT ?", (_env(env), status, int(limit)))]


# ------------------------------------------------------------------ what the registry holds
def summary(conn, base_id, env=None, system="core", workspace_key=""):
    """Counts only: registered records per table, and (given an environment) how many have each pass done, how many targets, and the attachment statuses."""
    _need(conn)
    tables = []
    for r in conn.execute("SELECT t.table_id, t.table_name, (SELECT COUNT(*) FROM source_records s WHERE s.base_id=t.base_id AND s.table_id=t.table_id) AS n "
                          "FROM airtable_tables t WHERE t.base_id=? ORDER BY lower(t.table_name), t.table_id", (base_id,)):
        row = {"table_id": r[0], "table_name": r[1], "registered": r[2]}
        if env is not None and r[2]:
            key = _ws(system, workspace_key)
            e = _env(env)
            row["passes"] = {p: conn.execute("SELECT COUNT(*) FROM record_pass_markers m JOIN source_records s ON s.source_record_uid=m.source_record_uid "
                                             "WHERE s.base_id=? AND s.table_id=? AND m.pass=? AND m.environment=? AND m.workspace_key=?", (base_id, r[0], p, e, key)).fetchone()[0] for p in PASSES}
            row["targets"] = conn.execute("SELECT COUNT(*) FROM target_mappings m JOIN source_records s ON s.source_record_uid=m.source_record_uid "
                                          "WHERE s.base_id=? AND s.table_id=? AND m.system=? AND m.environment=? AND m.workspace_key=?", (base_id, r[0], system, e, key)).fetchone()[0]
        tables.append(row)
    out = {"tables": tables, "registered": sum(t["registered"] for t in tables)}
    if env is not None:
        out["attachments"] = {s: conn.execute("SELECT COUNT(*) FROM attachment_map a JOIN source_records s ON s.source_record_uid=a.source_record_uid WHERE s.base_id=? AND a.environment=? AND a.status=?",
                                              (base_id, _env(env), s)).fetchone()[0] for s in ATTACHMENT_STATUSES}
    return out


# ------------------------------------------------------------------ screens: sortable, paged views (IDs, statuses and sizes only)
RECORD_SORTS = {"uid": "s.source_record_uid", "record_id": "s.record_id", "table": "lower(t.table_name)", "target": "targets", "passes": "passes"}
ATTACHMENT_SORTS = {"row": "a.attachment_row_id", "record_id": "s.record_id", "field": "a.airtable_field_id", "attachment": "a.airtable_attachment_id", "status": "a.status",
                    "size": "COALESCE(a.downloaded_bytes, a.api_size_bytes, -1)", "document": "COALESCE(a.core_document_id, '')"}
MAX_PAGE = 500


def _page(limit, offset):
    try:
        return max(1, min(int(limit), MAX_PAGE)), max(0, int(offset))
    except (TypeError, ValueError):
        raise RecordMapError("The page size and offset must be whole numbers.")


def _like(text):
    return "%" + str(text).replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"      # '!' is the LIKE escape character


def records_view(conn, base_id, env, system="planning", workspace_key="", table_id=None, q=None, sort="record_id", desc=False, limit=100, offset=0):
    """One row per registered record: surrogate ID, table, Airtable record ID, the new Workfront ID(s) and the passes done. Sorted on the server, in a fixed order
    (the sort column, then the surrogate ID), so the same data gives the same rows."""
    _need(conn)
    key, e = _ws(system, workspace_key), _env(env)
    if sort not in RECORD_SORTS:
        raise RecordMapError("Sort by one of: %s." % ", ".join(sorted(RECORD_SORTS)))
    limit, offset = _page(limit, offset)
    where, args = ["s.base_id=?"], [base_id]
    if table_id:
        where.append("s.table_id=?")
        args.append(table_id)
    if q:
        where.append("(s.record_id LIKE ? ESCAPE '!' OR EXISTS (SELECT 1 FROM target_mappings m WHERE m.source_record_uid=s.source_record_uid AND m.system=? AND m.environment=? AND m.workspace_key=? "
                     "AND m.target_record_id LIKE ? ESCAPE '!'))")
        args += [_like(q), system, e, key, _like(q)]
    w = " AND ".join(where)
    total = conn.execute("SELECT COUNT(*) FROM source_records s WHERE " + w, args).fetchone()[0]
    sql = ("SELECT s.source_record_uid AS uid, s.table_id, t.table_name, s.record_id, "
           "(SELECT group_concat(m.target_kind || ':' || m.target_record_id, ', ') FROM (SELECT * FROM target_mappings ORDER BY target_kind, target_record_id) m "
           " WHERE m.source_record_uid=s.source_record_uid AND m.system=? AND m.environment=? AND m.workspace_key=?) AS targets, "
           "(SELECT group_concat(p.pass, ', ') FROM (SELECT * FROM record_pass_markers ORDER BY pass) p WHERE p.source_record_uid=s.source_record_uid AND p.environment=? AND p.workspace_key=?) AS passes "
           "FROM source_records s JOIN airtable_tables t ON t.table_id=s.table_id AND t.base_id=s.base_id WHERE " + w +
           " ORDER BY %s %s, s.source_record_uid LIMIT ? OFFSET ?" % (RECORD_SORTS[sort], "DESC" if desc else "ASC"))
    rows = [dict(r) for r in conn.execute(sql, [system, e, key, e, key] + args + [limit, offset])]
    for r in rows:
        r["targets"] = r["targets"] or ""
        r["passes"] = [p for p in (r["passes"] or "").split(", ") if p]
    return {"rows": rows, "total": total, "limit": limit, "offset": offset}


def attachments_view(conn, base_id, env, status=None, sort="row", desc=False, limit=100, offset=0):
    """The attachment map: IDs, sizes, status and a short scrubbed error. Never a file name or a link."""
    _need(conn)
    e = _env(env)
    if sort not in ATTACHMENT_SORTS:
        raise RecordMapError("Sort by one of: %s." % ", ".join(sorted(ATTACHMENT_SORTS)))
    if status and status not in ATTACHMENT_STATUSES:
        raise RecordMapError("The status must be one of: %s." % ", ".join(ATTACHMENT_STATUSES))
    limit, offset = _page(limit, offset)
    where, args = ["s.base_id=?", "a.environment=?"], [base_id, e]
    if status:
        where.append("a.status=?")
        args.append(status)
    w = " AND ".join(where)
    base = "FROM attachment_map a JOIN source_records s ON s.source_record_uid=a.source_record_uid WHERE " + w
    total = conn.execute("SELECT COUNT(*) " + base, args).fetchone()[0]
    rows = [dict(r) for r in conn.execute(
        "SELECT a.attachment_row_id AS row_id, s.table_id, s.record_id, a.airtable_field_id AS field_id, a.airtable_attachment_id AS attachment_id, a.status, a.api_size_bytes, a.downloaded_bytes, "
        "a.sha256, a.core_document_id, a.last_error " + base + " ORDER BY %s %s, a.attachment_row_id LIMIT ? OFFSET ?" % (ATTACHMENT_SORTS[sort], "DESC" if desc else "ASC"), args + [limit, offset])]
    return {"rows": rows, "total": total, "limit": limit, "offset": offset}
