#!/usr/bin/env python3
"""
Airtable -> Workfront Planning migration control plane, stage 1.

Python standard library only. Run with:  python server.py
Then open http://127.0.0.1:8765

What this stage does:
  * keeps a SQLite database (the attached DDL, used exactly as written)
  * ingests Airtable schema metadata (from the Metadata API or pasted JSON)
  * computes a table load order from link fields (dependency_matrix)
  * profiles record data (row_count / null_count / cardinality) page by page,
    in memory only, never persisting raw records

The server binds to 127.0.0.1 only. It owns every database and Airtable call.
The Airtable token is read from config.json and is never stored, logged, or
sent to the browser.
"""
import hashlib
import heapq
import http.client
import json
import os
import re
import socket
import sqlite3
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from random import random
from urllib.parse import parse_qs, quote, urlencode, urlparse

# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
HOST = "127.0.0.1"  # never anything else
PORT = int(os.environ.get("PORT", "8765"))

# AIRTABLE_API_BASE / AT_* are test hooks (the mock-server test plan uses them).
# Normal use: leave them unset.
AIRTABLE_API_BASE = os.environ.get("AIRTABLE_API_BASE", "https://api.airtable.com").rstrip("/")
MAX_ATTEMPTS = int(os.environ.get("AT_MAX_ATTEMPTS", "5"))
BACKOFF_BASE = float(os.environ.get("AT_BACKOFF_BASE", "1.0"))  # seconds; doubles each retry
BACKOFF_CAP = 30.0
RETRY_AFTER_CAP = 60.0
MIN_INTERVAL = 0.2  # seconds between request starts per base  (= 5 requests/second)
HTTP_TIMEOUT = 30
MAX_BODY_BYTES = 25 * 1024 * 1024
DEFAULT_CARDINALITY_CAP = 1_000
PAGE_SIZE = 100

# --------------------------------------------------------------------------
# The finalized DDL (sql/DatabaseSchemaLibrary.sql). Compared with the original file, airtable_fields has two
# added columns: prefers_single_record_link and cardinality_is_capped. Nothing else was renamed or changed.
# --------------------------------------------------------------------------
DDL = r'''-- 1. Airtable Schema & Metadata Tracking
CREATE TABLE airtable_fields (
    base_id TEXT NOT NULL,
    table_id TEXT NOT NULL,
    table_name TEXT NOT NULL,
    field_id TEXT NOT NULL PRIMARY KEY,
    field_name TEXT NOT NULL,
    data_type TEXT NOT NULL,
    linked_table_id TEXT,          -- populated only for multipleRecordLinks (options.linkedTableId)
    inverse_link_field_id TEXT,    -- optional: options.inverseLinkFieldId, the paired field on the linked table
    prefers_single_record_link INTEGER, -- 1/0 from options.prefersSingleRecordLink (link fields only), NULL otherwise
    row_count INTEGER DEFAULT 0,
    null_count INTEGER DEFAULT 0,
    cardinality INTEGER DEFAULT 0,
    cardinality_is_capped INTEGER DEFAULT 0, -- 1 = cardinality is a lower bound: more distinct values than the cap
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_airtable_fields_table ON airtable_fields(table_id);
CREATE INDEX idx_airtable_fields_linked ON airtable_fields(linked_table_id);

-- 2. Workfront Planning Environment Schema (Dev & Prod)
CREATE TABLE workfront_fields (
    workfront_field_pk TEXT PRIMARY KEY, -- Composite key: environment + field_id
    environment TEXT CHECK(environment IN ('DEV', 'PROD')) NOT NULL,
    workspace_name TEXT NOT NULL,
    record_type TEXT NOT NULL,
    sub_record_type TEXT,
    field_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    data_type TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. Field & ID Crosswalk Mapping
CREATE TABLE field_crosswalk (
    crosswalk_id INTEGER PRIMARY KEY AUTOINCREMENT,
    airtable_base_id TEXT NOT NULL,
    airtable_table_name TEXT NOT NULL,
    airtable_field_id TEXT NOT NULL,
    airtable_field_name TEXT NOT NULL,
    workfront_dev_field_id TEXT,
    workfront_prod_field_id TEXT,
    mapping_notes TEXT,
    FOREIGN KEY (airtable_field_id) REFERENCES airtable_fields(field_id)
);

-- 4. Custom Form Structure & Section-Level Field Placement
CREATE TABLE form_sections (
    form_id TEXT NOT NULL,
    form_name TEXT NOT NULL,
    section_id TEXT NOT NULL,
    section_name TEXT NOT NULL,
    placement_order INTEGER NOT NULL,
    PRIMARY KEY (form_id, section_id)
);

CREATE TABLE form_fields (
    form_id TEXT NOT NULL,
    section_id TEXT NOT NULL,
    airtable_field_id TEXT NOT NULL,
    workfront_dev_field_id TEXT,
    workfront_prod_field_id TEXT,
    field_order INTEGER NOT NULL,
    is_required INTEGER DEFAULT 0,
    PRIMARY KEY (form_id, section_id, airtable_field_id),
    FOREIGN KEY (form_id, section_id) REFERENCES form_sections(form_id, section_id)
);

-- 5. Automation Catalog & Action Steps (Airtable -> Workfront Fusion)
CREATE TABLE automation_catalog (
    automation_id TEXT PRIMARY KEY,
    automation_name TEXT NOT NULL,
    source_system TEXT DEFAULT 'Airtable',
    trigger_type TEXT NOT NULL,
    target_fusion_scenario_name TEXT,
    notes TEXT
);

CREATE TABLE automation_steps (
    step_id INTEGER PRIMARY KEY AUTOINCREMENT,
    automation_id TEXT NOT NULL,
    step_order INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    referenced_airtable_field_id TEXT,
    referenced_workfront_dev_field_id TEXT,
    referenced_workfront_prod_field_id TEXT,
    step_logic_description TEXT,
    FOREIGN KEY (automation_id) REFERENCES automation_catalog(automation_id)
);

-- 6. Dependency Matrix & Data Ingestion Load Order
CREATE TABLE dependency_matrix (
    table_id TEXT PRIMARY KEY,
    table_name TEXT NOT NULL,
    parent_table_ids TEXT, -- Comma-separated or JSON list of required parent table IDs
    load_order_sequence INTEGER NOT NULL,
    dependency_depth INTEGER DEFAULT 0
);

-- 7. Audit Log & Migration Runbook
CREATE TABLE runbook_logs (
    error_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source_system TEXT NOT NULL,
    failing_component TEXT NOT NULL,
    error_message TEXT NOT NULL,
    root_cause_summary TEXT,
    mitigation_steps_taken TEXT,
    preventative_action TEXT,
    status TEXT CHECK(status IN ('OPEN', 'INVESTIGATING', 'RESOLVED', 'BYPASSED')) DEFAULT 'OPEN',
    verified_by TEXT
);

-- 8. API Call Log (every call, success or failure)
CREATE TABLE api_call_log (
    call_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,   -- UTC
    source_system TEXT NOT NULL,                     -- 'Airtable' (later 'Workfront Planning')
    purpose TEXT NOT NULL,                           -- 'schema_fetch', 'profile_page', etc.
    http_method TEXT NOT NULL,
    endpoint TEXT NOT NULL,                          -- URL path only, never headers or token
    base_id TEXT,
    table_id TEXT,
    attempt INTEGER DEFAULT 1,                       -- retry number for the same request
    http_status INTEGER,                             -- NULL if no response (network error, timeout)
    success INTEGER NOT NULL CHECK(success IN (0, 1)),
    duration_ms INTEGER,
    records_returned INTEGER,
    error_message TEXT
);

CREATE INDEX idx_api_call_log_time ON api_call_log(timestamp);
CREATE INDEX idx_api_call_log_status ON api_call_log(success, http_status);
'''


def _split_statements(ddl):
    out, buf = [], ""
    for line in ddl.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            out.append(buf.strip())
            buf = ""
    return out


def _parse_ddl():
    stmts = []  # (kind, table, sql)
    for sql in _split_statements(DDL):
        m = re.search(r"CREATE\s+TABLE\s+(\w+)", sql, re.I)
        if m:
            stmts.append(("table", m.group(1), sql))
            continue
        m = re.search(r"CREATE\s+INDEX\s+\w+\s+ON\s+(\w+)", sql, re.I)
        if m:
            stmts.append(("index", m.group(1), sql))
    return stmts


DDL_STATEMENTS = _parse_ddl()
EXPECTED_TABLES = [t for k, t, _ in DDL_STATEMENTS if k == "table"]


def _expected_columns():
    mem = sqlite3.connect(":memory:")
    mem.executescript(DDL)
    cols = {t: [r[1] for r in mem.execute('PRAGMA table_info("%s")' % t)] for t in EXPECTED_TABLES}
    mem.close()
    return cols


EXPECTED_COLUMNS = _expected_columns()

# --------------------------------------------------------------------------
# Errors, time, config, token hygiene
# --------------------------------------------------------------------------


class AppError(Exception):
    def __init__(self, message, status=400, extra=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.extra = extra or {}


class DbError(AppError):
    def __init__(self, message):
        super().__init__(message, status=500)


class ApiError(AppError):
    def __init__(self, message, http_status=None, detail=None):
        super().__init__(message, status=502, extra={"http_status": http_status})
        self.http_status = http_status
        self.detail = detail


class Cancelled(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


_cfg_cache = {"mtime": None, "cfg": None, "err": None}
_cfg_lock = threading.Lock()


def load_config():
    """Returns (config dict, problem message or None). Re-reads when the file changes."""
    with _cfg_lock:
        try:
            mtime = os.path.getmtime(CONFIG_PATH)
        except OSError:
            mtime = None
        if _cfg_cache["cfg"] is not None and _cfg_cache["mtime"] == mtime:
            return _cfg_cache["cfg"], _cfg_cache["err"]
        cfg = {"airtable_pat": "", "db_path": ""}
        err = None
        if mtime is None:
            err = ("config.json was not found next to server.py. Copy config.example.json to "
                   "config.json, paste your Airtable personal access token into it, then reload this page.")
        else:
            try:
                with open(CONFIG_PATH, encoding="utf-8-sig") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("not an object")
                token = str(data.get("airtable_pat") or "").strip()
                if not token or "REPLACE" in token.upper():
                    err = ("config.json has no Airtable token yet. Paste your personal access token into "
                           "the \"airtable_pat\" value, save the file, then reload this page.")
                    token = ""
                cfg = {"airtable_pat": token, "db_path": str(data.get("db_path") or "").strip()}
            except (OSError, ValueError):
                err = ("config.json could not be read. It must be valid JSON like "
                       "{\"airtable_pat\": \"pat...\", \"db_path\": \"optional path\"}.")
        _cfg_cache.update(mtime=mtime, cfg=cfg, err=err)
        return cfg, err


def get_token():
    return load_config()[0]["airtable_pat"]


_PAT_RE = re.compile(r"pat[A-Za-z0-9]{6,}\.[A-Za-z0-9]{20,}")
_BEARER_RE = re.compile(r"Bearer\s+[^\s'\"]+", re.I)


def scrub(text):
    """Remove anything that looks like the token from a string."""
    s = str(text)
    tok = get_token()
    if tok:
        s = s.replace(tok, "[token hidden]")
    s = _PAT_RE.sub("[token hidden]", s)
    s = _BEARER_RE.sub("Bearer [hidden]", s)
    return s


# --------------------------------------------------------------------------
# Database access
# --------------------------------------------------------------------------
STATE = {"db_path": None}
_state_lock = threading.Lock()


def resolve_path(raw):
    p = (raw or "").strip().strip('"').strip("'")
    if not p:
        raise AppError("Enter the path of a SQLite file first.")
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = os.path.join(BASE_DIR, p)
    return os.path.abspath(p)


def _open(path):
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db(path=None):
    path = path or STATE["db_path"]
    if not path:
        raise AppError("No database is connected yet. Complete step 1 first.")
    try:
        conn = _open(path)
    except sqlite3.Error as e:
        raise DbError("Could not open the database: %s" % scrub(e))
    try:
        yield conn
    finally:
        conn.close()


def inspect_db(conn):
    present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [t for t in EXPECTED_TABLES if t not in present]
    column_problems = []
    for t in EXPECTED_TABLES:
        if t in present:
            have = {r[1] for r in conn.execute('PRAGMA table_info("%s")' % t)}
            lacking = [c for c in EXPECTED_COLUMNS[t] if c not in have]
            if lacking:
                column_problems.append({"table": t, "missing_columns": lacking})
    counts = {t: conn.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
              for t in EXPECTED_TABLES if t in present}
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    return {"missing_tables": missing, "column_problems": column_problems,
            "counts": counts, "foreign_keys": bool(fk)}


def _connect_result(path, info):
    ready = not info["missing_tables"] and not info["column_problems"]
    if ready:
        with _state_lock:
            STATE["db_path"] = path
    return dict(info, exists=True, path=path, ready=ready)


def api_db_connect(body):
    path = resolve_path(body.get("path"))
    if os.path.isdir(path):
        raise AppError("That path is a folder. Enter the full path of a .db or .sqlite file.")
    if not os.path.exists(path):
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            raise AppError("The folder %s does not exist. Create it first or choose another path." % parent)
        return {"exists": False, "path": path, "ready": False,
                "message": "That file does not exist yet. You can create it with the finalized schema."}
    try:
        with db(path) as conn:
            info = inspect_db(conn)
    except sqlite3.DatabaseError:
        raise AppError("That file exists but is not a SQLite database (or is damaged). Choose another file.")
    result = _connect_result(path, info)
    if info["column_problems"]:
        result["message"] = ("Some existing tables do not have the expected columns. This app never alters "
                             "existing tables. Run sql/migration_001_add_columns.sql on this database "
                             "(see the README), or choose a new file.")
    return result


def api_db_create(body):
    path = resolve_path(body.get("path"))
    if os.path.isdir(path):
        raise AppError("That path is a folder. Enter the full path of a file.")
    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        raise AppError("The folder %s does not exist. Create it first or choose another path." % parent)
    try:
        with db(path) as conn:
            present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            to_create = [t for t in EXPECTED_TABLES if t not in present]
            with conn:
                for kind, table, sql in DDL_STATEMENTS:
                    if table in to_create:
                        conn.execute(sql)
            info = inspect_db(conn)
    except sqlite3.DatabaseError as e:
        raise DbError("Could not create the tables: %s. Nothing was changed." % scrub(e))
    result = _connect_result(path, info)
    result["created_tables"] = to_create
    return result


def api_status(_q=None):
    cfg, err = load_config()
    return {
        "token_configured": bool(cfg["airtable_pat"]),
        "config_problem": err,
        "default_db_path": cfg["db_path"],
        "db_path": STATE["db_path"],
        "expected_tables": EXPECTED_TABLES,
        "custom_api_base": AIRTABLE_API_BASE != "https://api.airtable.com",
    }


def api_db_counts(_q=None):
    with db() as conn:
        return inspect_db(conn)


# --------------------------------------------------------------------------
# Logging helpers (api_call_log, runbook_logs)
# --------------------------------------------------------------------------


def log_api_attempt(row):
    try:
        with db() as conn:
            with conn:
                conn.execute(
                    """INSERT INTO api_call_log(timestamp, source_system, purpose, http_method, endpoint,
                       base_id, table_id, attempt, http_status, success, duration_ms, records_returned,
                       error_message) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (utc_now(), "Airtable", row["purpose"], row["method"], row["endpoint"], row["base_id"],
                     row["table_id"], row["attempt"], row["http_status"], row["success"], row["duration_ms"],
                     row["records_returned"], row["error_message"]))
    except sqlite3.Error as e:
        raise DbError("Database write failed while recording an API call: %s. The operation was stopped." % scrub(e))


def write_runbook(component, message, root_cause=None, mitigation=None, preventative=None,
                  status="OPEN", conn=None):
    sql = """INSERT INTO runbook_logs(timestamp, source_system, failing_component, error_message,
             root_cause_summary, mitigation_steps_taken, preventative_action, status)
             VALUES (?,?,?,?,?,?,?,?)"""
    args = (utc_now(), "Airtable", component, scrub(message), root_cause, mitigation, preventative, status)
    try:
        if conn is not None:
            conn.execute(sql, args)
        else:
            with db() as c:
                with c:
                    c.execute(sql, args)
    except sqlite3.Error as e:
        raise DbError("Database write failed while writing the runbook: %s" % scrub(e))


# --------------------------------------------------------------------------
# The single outbound API function
# --------------------------------------------------------------------------
_rate_lock = threading.Lock()
_next_slot = {}


def _throttle(base_id):
    key = base_id or "_none"
    with _rate_lock:
        now = time.monotonic()
        slot = max(now, _next_slot.get(key, 0.0))
        _next_slot[key] = slot + MIN_INTERVAL
    wait = slot - now
    if wait > 0:
        time.sleep(wait)


class RunStats:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = self.success = self.failure = 0

    def add(self, ok):
        with self.lock:
            self.calls += 1
            if ok:
                self.success += 1
            else:
                self.failure += 1

    def as_dict(self):
        with self.lock:
            return {"calls": self.calls, "success": self.success, "failure": self.failure}


SCOPES = {"schema_fetch": "schema.bases:read", "profile_page": "data.records:read"}


def explain(status, purpose, attempts):
    scope = SCOPES.get(purpose, "the required scope")
    if status is None:
        return ("Could not reach Airtable (network error or timeout) after %d attempt(s). Check your internet "
                "connection, VPN or proxy, then try again." % attempts)
    if status == 401:
        return ("Airtable rejected the token (401). It is invalid, expired or revoked. Create a new personal "
                "access token and update config.json, then restart the server.")
    if status == 403:
        return ("Airtable refused access (403). This usually means the token does not have access to this base, "
                "or it is missing the %s scope. Edit the token at airtable.com/create/tokens, add the base and "
                "scope, and retry." % scope)
    if status == 404:
        return ("Airtable could not find that base or table (404). Check the ID. Airtable also answers 404 when "
                "the token cannot see the base.")
    if status == 422:
        return ("Airtable rejected the request as invalid (422). The base or table may use a feature this "
                "request does not support. See the call log for Airtable's message.")
    if status == 429:
        return ("Airtable is rate limiting requests (429) and all %d attempts were used. Wait a minute and run "
                "it again." % attempts)
    if 500 <= status < 600:
        return ("Airtable had a server problem (%d) and all %d attempts were used. Try again later." % (status, attempts))
    return "Airtable returned an unexpected status (%s). See the call log for details." % status


def _short(text, n=500):
    t = scrub(text)
    return t if len(t) <= n else t[:n] + "..."


def _airtable_message(raw):
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
        err = data.get("error")
        if isinstance(err, dict):
            return "%s: %s" % (err.get("type", "error"), err.get("message", ""))
        if err:
            return str(err)
    except (ValueError, AttributeError):
        pass
    return raw.decode("utf-8", "replace")[:300]


def _retry_after_seconds(value):
    try:
        return max(0.0, min(float(value), RETRY_AFTER_CAP))
    except (TypeError, ValueError):
        return None


class _BadBody(Exception):
    pass


def api_call(*, purpose, path, query=None, base_id=None, table_id=None, stats=None, cancel=None,
             records_key=None):
    """GET one Airtable endpoint. Every attempt is written to api_call_log.
    Retries 429, 5xx and network errors with exponential backoff. Fails fast on 4xx others.
    On final failure writes a runbook_logs row and raises ApiError."""
    token = get_token()
    if not token:
        raise AppError("No Airtable token is configured. Edit config.json (see the setup message) and reload.")
    url = AIRTABLE_API_BASE + path + ("?" + urlencode(query) if query else "")
    status = None
    msg = None
    attempt = 0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        _throttle(base_id)
        t0 = time.monotonic()
        status, ok, nrec, msg, retry_after, body, retryable = None, 0, None, None, None, None, False
        try:
            req = urllib.request.Request(url, method="GET", headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/json",
                "User-Agent": "airtable-migration-control/1.0"})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                status = resp.status
                raw = resp.read()
            try:
                body = json.loads(raw.decode("utf-8"))
            except ValueError:
                raise _BadBody()
            ok = 1
            if records_key and isinstance(body, dict):
                nrec = len(body.get(records_key) or [])
        except urllib.error.HTTPError as e:
            status = e.code
            retry_after = e.headers.get("Retry-After") if e.headers else None
            try:
                raw = e.read(4000)
            except Exception:
                raw = b""
            msg = "HTTP %s %s" % (status, _airtable_message(raw))
            retryable = status == 429 or 500 <= status < 600
        except _BadBody:
            msg = "Airtable returned a response that was not valid JSON."
        except (urllib.error.URLError, socket.timeout, TimeoutError, http.client.HTTPException, OSError) as e:
            reason = getattr(e, "reason", e)
            msg = "Network error (%s): %s" % (type(reason).__name__, reason)
            status = None
            retryable = True
        duration = int((time.monotonic() - t0) * 1000)
        log_api_attempt({"purpose": purpose, "method": "GET", "endpoint": path, "base_id": base_id,
                         "table_id": table_id, "attempt": attempt, "http_status": status, "success": ok,
                         "duration_ms": duration, "records_returned": nrec,
                         "error_message": _short(msg) if msg else None})
        if stats is not None:
            stats.add(bool(ok))
        if ok:
            return body
        if not retryable or attempt == MAX_ATTEMPTS:
            break
        delay = _retry_after_seconds(retry_after)
        if delay is None:
            delay = min(BACKOFF_BASE * (2 ** (attempt - 1)), BACKOFF_CAP) * (1 + 0.25 * random())
        if cancel is not None:
            if cancel.wait(delay):
                raise Cancelled()
        else:
            time.sleep(delay)
    hint = explain(status, purpose, attempt)
    retried = ("Retried %d time(s) with backoff." % (attempt - 1)) if attempt > 1 else "Not retried (this error cannot be fixed by retrying)."
    write_runbook("%s:%s" % (purpose, table_id or base_id or "-"), "%s (%s)" % (hint, _short(msg, 300)),
                  root_cause=hint, mitigation=retried,
                  preventative="Check token scopes/base access before large runs.")
    raise ApiError(hint, http_status=status, detail=_short(msg))


# --------------------------------------------------------------------------
# Schema ingestion
# --------------------------------------------------------------------------
BASE_ID_RE = re.compile(r"^app[A-Za-z0-9]{4,}$")
STALE_SQL = ("(f.updated_at < (SELECT MAX(x.updated_at) FROM airtable_fields x WHERE x.base_id = f.base_id))")


def check_base_id(raw):
    b = (raw or "").strip()
    if not BASE_ID_RE.match(b):
        raise AppError("Enter a valid Airtable base ID (it starts with \"app\", for example appXXXXXXXXXXXXXX).")
    return b


def parse_schema(doc):
    if isinstance(doc, list):
        tables = doc
    elif isinstance(doc, dict) and isinstance(doc.get("tables"), list):
        tables = doc["tables"]
    else:
        raise AppError("The schema JSON must be an object with a \"tables\" list, in the shape the Airtable "
                       "Metadata API returns.")
    rows, seen_fields, seen_tables = [], set(), set()
    for i, t in enumerate(tables):
        if not isinstance(t, dict) or not t.get("id") or not t.get("name"):
            raise AppError("Table #%d in the schema has no \"id\" or \"name\"." % (i + 1))
        if t["id"] in seen_tables:
            raise AppError("Table ID %s appears twice in the schema." % t["id"])
        seen_tables.add(t["id"])
        fields = t.get("fields")
        if not isinstance(fields, list):
            raise AppError("Table \"%s\" has no \"fields\" list." % t["name"])
        for f in fields:
            if not isinstance(f, dict) or not f.get("id") or not f.get("name") or not f.get("type"):
                raise AppError("A field in table \"%s\" is missing \"id\", \"name\" or \"type\"." % t["name"])
            if f["id"] in seen_fields:
                raise AppError("Field ID %s appears twice in the schema." % f["id"])
            seen_fields.add(f["id"])
            linked = inverse = prefers = None
            if f["type"] == "multipleRecordLinks":
                opts = f.get("options") or {}
                linked = opts.get("linkedTableId")
                inverse = opts.get("inverseLinkFieldId")
                p = opts.get("prefersSingleRecordLink")
                prefers = None if p is None else (1 if p else 0)
            rows.append({"table_id": t["id"], "table_name": t["name"], "field_id": f["id"],
                         "field_name": f["name"], "data_type": f["type"],
                         "linked_table_id": linked, "inverse_link_field_id": inverse,
                         "prefers_single_record_link": prefers})
    if not rows:
        raise AppError("The schema contains no fields, so there is nothing to ingest.")
    return rows


def upsert_schema(base_id, rows):
    ts = utc_now()
    try:
        with db() as conn:
            existing = {r["field_id"]: r["base_id"] for r in conn.execute("SELECT field_id, base_id FROM airtable_fields")}
            clash = [r["field_id"] for r in rows if existing.get(r["field_id"], base_id) != base_id]
            if clash:
                other = existing[clash[0]]
                raise AppError("Field %s (and %d other(s)) already belong to base %s, not %s. Check that you "
                               "entered the right base ID. Nothing was saved." % (clash[0], len(clash) - 1, other, base_id))
            new_ids = {r["field_id"] for r in rows}
            before_in_base = {fid for fid, b in existing.items() if b == base_id}
            with conn:
                conn.executemany(
                    """INSERT INTO airtable_fields(base_id, table_id, table_name, field_id, field_name, data_type,
                                                   linked_table_id, inverse_link_field_id,
                                                   prefers_single_record_link, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(field_id) DO UPDATE SET
                         base_id=excluded.base_id, table_id=excluded.table_id, table_name=excluded.table_name,
                         field_name=excluded.field_name, data_type=excluded.data_type,
                         linked_table_id=excluded.linked_table_id,
                         inverse_link_field_id=excluded.inverse_link_field_id,
                         prefers_single_record_link=excluded.prefers_single_record_link,
                         updated_at=excluded.updated_at""",
                    [(base_id, r["table_id"], r["table_name"], r["field_id"], r["field_name"], r["data_type"],
                      r["linked_table_id"], r["inverse_link_field_id"], r["prefers_single_record_link"], ts)
                     for r in rows])
            missing = sorted(before_in_base - new_ids)
            return {"base_id": base_id,
                    "tables": len({r["table_id"] for r in rows}),
                    "fields": len(rows),
                    "added": len(new_ids - before_in_base),
                    "updated": len(new_ids & before_in_base),
                    "no_longer_in_source": len(missing)}
    except sqlite3.Error as e:
        raise DbError("Database write failed: %s. Nothing from this ingest was saved." % scrub(e))


def api_schema_ingest(body):
    base_id = check_base_id(body.get("base_id"))
    source = body.get("source")
    if source == "api":
        if not get_token():
            raise AppError("No Airtable token is configured. Edit config.json (see the setup message) and reload.")
        doc = api_call(purpose="schema_fetch", path="/v0/meta/bases/%s/tables" % quote(base_id, safe=""),
                       base_id=base_id, records_key="tables")
    elif source == "json":
        text = body.get("schema_json")
        if isinstance(text, str):
            if not text.strip():
                raise AppError("Paste or upload a schema JSON document first.")
            try:
                doc = json.loads(text)
            except ValueError:
                raise AppError("That text is not valid JSON. Paste the full response from the Metadata API.")
        elif isinstance(text, (dict, list)):
            doc = text
        else:
            raise AppError("Paste or upload a schema JSON document first.")
    else:
        raise AppError("Choose a schema source: Airtable API or JSON.")
    rows = parse_schema(doc)
    result = upsert_schema(base_id, rows)
    result["source"] = source
    return result


def api_schema_view(_q=None):
    with db() as conn:
        rows = conn.execute("""SELECT f.*, %s AS stale FROM airtable_fields f
                               ORDER BY f.base_id, lower(f.table_name), f.table_id, lower(f.field_name), f.field_id"""
                            % STALE_SQL).fetchall()
    names = {r["table_id"]: r["table_name"] for r in rows if not r["stale"]}
    for r in rows:
        names.setdefault(r["table_id"], r["table_name"])
    bases = {}
    for r in rows:
        b = bases.setdefault(r["base_id"], {"base_id": r["base_id"], "tables": {}})
        t = b["tables"].setdefault(r["table_id"], {"table_id": r["table_id"], "table_name": r["table_name"],
                                                   "fields": [], "stale_fields": 0})
        link = r["linked_table_id"]
        t["fields"].append({
            "field_id": r["field_id"], "field_name": r["field_name"], "data_type": r["data_type"],
            "linked_table_id": link, "linked_table_name": names.get(link) if link else None,
            "inverse_link_field_id": r["inverse_link_field_id"],
            "prefers_single_record_link": r["prefers_single_record_link"],
            "self_link": bool(link and link == r["table_id"]),
            "stale": bool(r["stale"])})
        if r["stale"]:
            t["stale_fields"] += 1
    out = []
    for b in sorted(bases):
        tables = []
        for t in sorted(bases[b]["tables"].values(), key=lambda x: (x["table_name"].lower(), x["table_id"])):
            t["stale"] = t["stale_fields"] == len(t["fields"])
            tables.append(t)
        out.append({"base_id": b, "tables": tables})
    return {"bases": out}


# --------------------------------------------------------------------------
# Dependency graph
# --------------------------------------------------------------------------
def _tarjan(nodes, adj):
    index, low, onstack, stack, sccs = {}, {}, set(), [], []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        onstack.add(root)
        work = [(root, iter(adj[root]))]
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    onstack.add(w)
                    work.append((w, iter(adj[w])))
                    advanced = True
                    break
                if w in onstack:
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                u = work[-1][0]
                low[u] = min(low[u], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    onstack.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                sccs.append(comp)
    return sccs


def compute_graph(conn, mode="literal"):
    """Pure computation from airtable_fields. Writes nothing.
    mode 'literal': every link field is an edge (child -> parent), as specified.
    mode 'collapse': two-way link pairs (a link field and its inverse) are resolved with Airtable's
        prefersSingleRecordLink hint. If exactly one side is a single-record link, that side's table is the
        child and only that field is an ordering edge. If both or neither side is (many-to-many, or the hint
        was not reported), the pair adds no ordering edge and is listed for a second-pass load."""
    if mode not in ("literal", "collapse"):
        raise AppError("Unknown dependency mode.")
    rows = [r for r in conn.execute("SELECT f.*, %s AS stale FROM airtable_fields f" % STALE_SQL) if not r["stale"]]
    tables = {}
    for r in rows:
        tables[r["table_id"]] = r["table_name"]
    by_field = {r["field_id"]: r for r in rows}
    links = [r for r in rows if r["linked_table_id"]]

    # inverse pair detection (always computed; only used for ordering in 'collapse' mode)
    paired, oriented, pair_list = set(), set(), {}
    for a in links:
        if a["table_id"] == a["linked_table_id"] or not a["inverse_link_field_id"]:
            continue
        b = by_field.get(a["inverse_link_field_id"])
        if b and b["linked_table_id"] == a["table_id"] and b["table_id"] == a["linked_table_id"]:
            paired.add(a["field_id"])
            paired.add(b["field_id"])
            key = tuple(sorted((a["field_id"], b["field_id"])))
            if key in pair_list:
                continue
            pa = a["prefers_single_record_link"] == 1
            pb = b["prefers_single_record_link"] == 1
            child = a if (pa and not pb) else (b if (pb and not pa) else None)
            if child is not None:
                oriented.add(child["field_id"])
            x, y = (a, b) if a["field_id"] <= b["field_id"] else (b, a)
            pair_list[key] = {"table_a": x["table_name"], "table_b": y["table_name"],
                              "field_a": x["field_name"], "field_b": y["field_name"],
                              "child_table": child["table_name"] if child else None,
                              "parent_table": tables[child["linked_table_id"]] if child else None}
    pairs = sorted(pair_list.values(), key=lambda p: (p["table_a"].lower(), p["table_b"].lower(), p["field_a"].lower()))

    self_links, unresolved, edges = [], [], {}
    for a in links:
        if a["linked_table_id"] == a["table_id"]:
            self_links.append({"table_id": a["table_id"], "table_name": a["table_name"], "field_name": a["field_name"]})
            continue
        if a["linked_table_id"] not in tables:
            unresolved.append({"table_name": a["table_name"], "field_name": a["field_name"],
                               "linked_table_id": a["linked_table_id"]})
            continue
        if mode == "collapse" and a["field_id"] in paired and a["field_id"] not in oriented:
            continue
        edges.setdefault((a["table_id"], a["linked_table_id"]), []).append(a["field_name"])

    def skey(tid):
        return (tables[tid].lower(), tid)

    nodes = sorted(tables, key=skey)
    adj = {n: [] for n in nodes}
    for (child, parent) in edges:
        adj[child].append(parent)
    for n in nodes:
        adj[n].sort(key=skey)
    sccs = _tarjan(nodes, adj)
    comp_of = {}
    for i, comp in enumerate(sccs):
        for n in comp:
            comp_of[n] = i
    parents_of, children_of = {i: set() for i in range(len(sccs))}, {i: set() for i in range(len(sccs))}
    for (child, parent) in edges:
        c, p = comp_of[child], comp_of[parent]
        if c != p:
            parents_of[c].add(p)
            children_of[p].add(c)
    ckey = {i: min(skey(n) for n in comp) for i, comp in enumerate(sccs)}
    indeg = {i: len(parents_of[i]) for i in parents_of}
    heap = [(ckey[i], i) for i in indeg if indeg[i] == 0]
    heapq.heapify(heap)
    depth, order = {}, []
    while heap:
        _, i = heapq.heappop(heap)
        depth[i] = 0 if not parents_of[i] else 1 + max(depth[p] for p in parents_of[i])
        order.append(i)
        for ch in children_of[i]:
            indeg[ch] -= 1
            if indeg[ch] == 0:
                heapq.heappush(heap, (ckey[ch], ch))

    parents_by_table = {n: [] for n in nodes}
    for (child, parent) in edges:
        parents_by_table[child].append(parent)
    matrix, seq = [], 0
    for i in order:
        for n in sorted(sccs[i], key=skey):
            seq += 1
            matrix.append({"table_id": n, "table_name": tables[n],
                           "parent_table_ids": sorted(parents_by_table[n]),
                           "load_order_sequence": seq, "dependency_depth": depth[i],
                           "in_cycle": len(sccs[i]) > 1})
    cycles = []
    for comp in sccs:
        if len(comp) < 2:
            continue
        members = sorted(comp, key=skey)
        inside = set(members)
        cyc_edges = [{"child": tables[c], "parent": tables[p], "fields": sorted(f)}
                     for (c, p), f in sorted(edges.items(), key=lambda kv: (skey(kv[0][0]), skey(kv[0][1])))
                     if c in inside and p in inside]
        cycles.append({"table_ids": members, "tables": [tables[m] for m in members], "edges": cyc_edges})
    cycles.sort(key=lambda c: skey(c["table_ids"][0]))
    edge_list = [{"child": c, "parent": p, "fields": sorted(f)}
                 for (c, p), f in sorted(edges.items(), key=lambda kv: (skey(kv[0][0]), skey(kv[0][1])))]
    return {"mode": mode, "matrix": matrix, "edges": edge_list, "cycles": cycles, "self_links": self_links,
            "unresolved_links": unresolved, "inverse_pairs": pairs}


def _cycle_message(cyc, mode):
    members = ", ".join("%s (%s)" % (n, i) for n, i in zip(cyc["tables"], cyc["table_ids"]))
    via = "; ".join("%s -> %s via %s" % (e["child"], e["parent"], ", ".join(e["fields"])) for e in cyc["edges"])
    return ("Dependency cycle among %d tables: %s. Links: %s. Mode: %s. These tables need their records loaded "
            "first, with the link fields populated in a second pass." % (len(cyc["tables"]), members, via, mode))


def api_depgraph_recompute(body):
    mode = (body or {}).get("mode", "literal")
    try:
        with db() as conn:
            g = compute_graph(conn, mode)
            with conn:
                conn.execute("DELETE FROM dependency_matrix")
                conn.executemany(
                    "INSERT INTO dependency_matrix(table_id, table_name, parent_table_ids, load_order_sequence, "
                    "dependency_depth) VALUES (?,?,?,?,?)",
                    [(m["table_id"], m["table_name"], json.dumps(m["parent_table_ids"]),
                      m["load_order_sequence"], m["dependency_depth"]) for m in g["matrix"]])
                logged = 0
                for cyc in g["cycles"]:
                    msg = _cycle_message(cyc, mode)
                    dup = conn.execute("SELECT 1 FROM runbook_logs WHERE failing_component='dependency_graph' "
                                       "AND status='OPEN' AND error_message=?", (msg,)).fetchone()
                    if not dup:
                        write_runbook("dependency_graph", msg,
                                      root_cause="Tables link to each other in a loop, so no strict parent-first order exists.",
                                      mitigation="Best-effort deterministic order written to dependency_matrix.",
                                      preventative="Load records without links first, then populate links in a second pass.",
                                      conn=conn)
                        logged += 1
    except sqlite3.Error as e:
        raise DbError("Database write failed: %s. The previous load order was kept." % scrub(e))
    g["runbook_rows_written"] = logged
    g["tables"] = len(g["matrix"])
    return g


def api_depgraph_view(q):
    mode = (q.get("mode") or ["literal"])[0]
    with db() as conn:
        g = compute_graph(conn, mode)
        stored = conn.execute("SELECT * FROM dependency_matrix ORDER BY load_order_sequence").fetchall()
    stored_rows = [{"table_id": r["table_id"], "table_name": r["table_name"],
                    "parent_table_ids": json.loads(r["parent_table_ids"] or "[]"),
                    "load_order_sequence": r["load_order_sequence"],
                    "dependency_depth": r["dependency_depth"]} for r in stored]
    cyc_ids = {t for c in g["cycles"] for t in c["table_ids"]}
    for r in stored_rows:
        r["in_cycle"] = r["table_id"] in cyc_ids
    fresh = [{k: m[k] for k in ("table_id", "table_name", "parent_table_ids", "load_order_sequence", "dependency_depth")}
             for m in g["matrix"]]
    saved = [{k: m[k] for k in ("table_id", "table_name", "parent_table_ids", "load_order_sequence", "dependency_depth")}
             for m in stored_rows]
    g["stored_matrix"] = stored_rows
    g["matrix_in_sync"] = fresh == saved
    del g["matrix"]
    return g


# --------------------------------------------------------------------------
# Profiling
# --------------------------------------------------------------------------
def canon(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def digest(s):
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "big")


def value_key(ftype, v):
    if ftype == "multipleAttachments" and isinstance(v, list):
        ids = [a["id"] if isinstance(a, dict) and a.get("id") else canon(a) for a in v]
        return digest(canon(ids))
    return digest(canon(v))


def is_null(v):
    # None / missing, "" and [] are null. 0 and False are real values.
    return v is None or (isinstance(v, str) and v == "") or (isinstance(v, list) and len(v) == 0)


class Job:
    def __init__(self, scope, cap, tables):
        self.id = str(int(time.time() * 1000))
        self.status = "running"
        self.error = None
        self.scope = scope
        self.cap = cap
        self.started_at = utc_now()
        self.finished_at = None
        self.cancel = threading.Event()
        self.api = RunStats()
        self.tables = tables  # list of dicts (serializable)
        self.fields = {}      # table_id -> [(field_id, type)]


JOB = {"current": None}
_job_lock = threading.Lock()


def eligible_tables(conn):
    rows = conn.execute("SELECT f.base_id, f.table_id, f.table_name, f.field_id, f.data_type FROM airtable_fields f "
                        "WHERE NOT %s ORDER BY f.base_id, lower(f.table_name), f.table_id, f.field_id" % STALE_SQL).fetchall()
    out = {}
    for r in rows:
        t = out.setdefault(r["table_id"], {"base_id": r["base_id"], "table_id": r["table_id"],
                                           "table_name": r["table_name"], "fields": []})
        t["fields"].append((r["field_id"], r["data_type"]))
    return list(out.values())


def api_profile_start(body):
    scope = body.get("scope", "all")
    try:
        cap = int(body.get("cap") or DEFAULT_CARDINALITY_CAP)
    except (TypeError, ValueError):
        raise AppError("The distinct-value cap must be a whole number.")
    if cap < 100:
        raise AppError("The distinct-value cap must be at least 100.")
    with db() as conn:
        tables = eligible_tables(conn)
    if scope == "base":
        tables = [t for t in tables if t["base_id"] == body.get("base_id")]
    elif scope == "table":
        tables = [t for t in tables if t["table_id"] == body.get("table_id")]
    elif scope != "all":
        raise AppError("Unknown profiling scope.")
    if not tables:
        raise AppError("There are no tables to profile for that choice. Ingest a schema first (step 3).")
    if not get_token():
        raise AppError("No Airtable token is configured. Edit config.json (see the setup message) and reload.")
    with _job_lock:
        cur = JOB["current"]
        if cur and cur.status == "running":
            raise AppError("A profiling run is already in progress. Wait for it to finish or cancel it.")
        job = Job(scope, cap, [{"base_id": t["base_id"], "table_id": t["table_id"], "table_name": t["table_name"],
                                "fields": len(t["fields"]), "status": "pending", "pages": 0, "records": 0,
                                "error": None, "capped_fields": 0} for t in tables])
        for t in tables:
            job.fields[t["table_id"]] = t["fields"]
        JOB["current"] = job
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return {"started": True, "job_id": job.id, "tables": len(job.tables)}


def _profile_table(job, t):
    fields = job.fields[t["table_id"]]
    nulls = {fid: 0 for fid, _ in fields}
    sets = {fid: set() for fid, _ in fields}
    capped = set()
    row_count, offset = 0, None
    path = "/v0/%s/%s" % (quote(t["base_id"], safe=""), quote(t["table_id"], safe=""))
    while True:
        q = {"pageSize": PAGE_SIZE, "returnFieldsByFieldId": "true"}
        if offset:
            q["offset"] = offset
        body = api_call(purpose="profile_page", path=path, query=q, base_id=t["base_id"], table_id=t["table_id"],
                        stats=job.api, cancel=job.cancel, records_key="records")
        for rec in body.get("records") or []:
            fv = rec.get("fields") or {}
            row_count += 1
            for fid, ftype in fields:
                v = fv.get(fid)
                if ftype == "checkbox" and v is None:
                    v = False  # Airtable omits unchecked boxes: that is a real "false"
                if is_null(v):
                    nulls[fid] += 1
                    continue
                if fid in capped:
                    continue
                s = sets[fid]
                s.add(value_key(ftype, v))
                if len(s) > job.cap:
                    capped.add(fid)
                    sets[fid] = None  # free memory; exact count is no longer tracked
        t["pages"] += 1
        t["records"] = row_count
        t["capped_fields"] = len(capped)
        offset = body.get("offset")
        if not offset:
            break
    return row_count, nulls, sets, capped


def _save_table(job, t, row_count, nulls, sets, capped):
    try:
        with db() as conn:
            with conn:
                # updated_at is deliberately left alone: it records "last seen in the schema".
                for fid, _ in job.fields[t["table_id"]]:
                    # A capped field stores the cap itself as a lower bound ("more than cap") and sets
                    # cardinality_is_capped = 1, so a capped number is never mistaken for an exact one.
                    conn.execute("UPDATE airtable_fields SET row_count=?, null_count=?, cardinality=?, "
                                 "cardinality_is_capped=? WHERE field_id=?",
                                 (row_count, nulls[fid], job.cap if fid in capped else len(sets[fid]),
                                  1 if fid in capped else 0, fid))
    except sqlite3.Error as e:
        raise DbError("Database write failed while saving %s: %s" % (t["table_name"], scrub(e)))


def _run_job(job):
    try:
        for idx, t in enumerate(job.tables):
            if job.cancel.is_set():
                break
            t["status"] = "running"
            try:
                result = _profile_table(job, t)
                _save_table(job, t, *result)
                t["status"] = "done"
            except Cancelled:
                t["status"] = "skipped"
                break
            except ApiError as e:
                t["status"] = "failed"
                t["error"] = e.message
            except DbError as e:
                t["status"] = "failed"
                t["error"] = e.message
                job.error = e.message
                job.status = "error"
                break
        for t in job.tables:
            if t["status"] in ("pending", "running"):
                t["status"] = "skipped"
        if job.status == "running":
            job.status = "cancelled" if job.cancel.is_set() else "done"
    except Exception as e:  # never let the thread die silently
        job.status = "error"
        job.error = "Unexpected error: %s" % scrub(e)
        for t in job.tables:
            if t["status"] in ("pending", "running"):
                t["status"] = "skipped"
        print(scrub(traceback.format_exc()), file=sys.stderr)
    finally:
        job.finished_at = utc_now()


def api_profile_status(_q=None):
    job = JOB["current"]
    if not job:
        return {"job": None}
    return {"job": {"id": job.id, "status": job.status, "error": job.error, "scope": job.scope, "cap": job.cap,
                    "started_at": job.started_at, "finished_at": job.finished_at, "api": job.api.as_dict(),
                    "tables": [dict(t) for t in job.tables]}}


def api_profile_cancel(_body=None):
    job = JOB["current"]
    if not job or job.status != "running":
        raise AppError("No profiling run is in progress.")
    job.cancel.set()
    return {"cancelling": True}


def api_profile_results(q):
    base = (q.get("base_id") or [""])[0]
    table = (q.get("table_id") or [""])[0]
    sql = ("SELECT f.base_id, f.table_id, f.table_name, f.field_id, f.field_name, f.data_type, f.row_count, "
           "f.null_count, f.cardinality, f.cardinality_is_capped, %s AS stale FROM airtable_fields f WHERE 1=1" % STALE_SQL)
    args = []
    if base:
        sql += " AND f.base_id=?"
        args.append(base)
    if table:
        sql += " AND f.table_id=?"
        args.append(table)
    sql += " ORDER BY f.base_id, lower(f.table_name), f.table_id, lower(f.field_name), f.field_id"
    with db() as conn:
        rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        rc = r["row_count"] or 0
        capped = bool(r["cardinality_is_capped"])
        out.append({"base_id": r["base_id"], "table_id": r["table_id"], "table_name": r["table_name"],
                    "field_id": r["field_id"], "field_name": r["field_name"], "data_type": r["data_type"],
                    "row_count": rc, "null_count": r["null_count"] or 0,
                    "null_pct": round(100.0 * (r["null_count"] or 0) / rc, 1) if rc else None,
                    "cardinality": r["cardinality"], "cardinality_capped": capped,
                    "stale": bool(r["stale"])})
    return {"rows": out}


# --------------------------------------------------------------------------
# Call log + runbook panels
# --------------------------------------------------------------------------
def api_calllog(q):
    def one(name):
        return (q.get(name) or [""])[0]
    where, args = [], []
    if one("failures") in ("1", "true"):
        where.append("success=0")
    if one("table_id"):
        where.append("table_id=?")
        args.append(one("table_id"))
    if one("purpose"):
        where.append("purpose=?")
        args.append(one("purpose"))
    try:
        limit = max(1, min(int(one("limit") or 100), 500))
    except ValueError:
        limit = 100
    w = (" WHERE " + " AND ".join(where)) if where else ""
    with db() as conn:
        rows = conn.execute("SELECT * FROM api_call_log%s ORDER BY call_id DESC LIMIT ?" % w, args + [limit]).fetchall()
        tot = conn.execute("SELECT COUNT(*), COALESCE(SUM(success),0) FROM api_call_log%s" % w, args).fetchone()
        purposes = [r[0] for r in conn.execute("SELECT DISTINCT purpose FROM api_call_log ORDER BY purpose")]
        tables = [r[0] for r in conn.execute("SELECT DISTINCT table_id FROM api_call_log WHERE table_id IS NOT NULL ORDER BY table_id")]
    return {"rows": [dict(r) for r in rows],
            "summary": {"total": tot[0], "success": tot[1], "failure": tot[0] - tot[1]},
            "purposes": purposes, "table_ids": tables}


def api_runbook(q):
    status = (q.get("status") or ["OPEN"])[0]
    with db() as conn:
        if status == "ALL":
            rows = conn.execute("SELECT * FROM runbook_logs ORDER BY error_id DESC LIMIT 100").fetchall()
        else:
            rows = conn.execute("SELECT * FROM runbook_logs WHERE status=? ORDER BY error_id DESC LIMIT 100",
                                (status,)).fetchall()
    return {"rows": [dict(r) for r in rows]}


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/scripts.js": ("scripts.js", "application/javascript; charset=utf-8")}

GET_ROUTES = {"/api/status": api_status, "/api/db/counts": api_db_counts, "/api/schema": api_schema_view,
              "/api/depgraph": api_depgraph_view, "/api/profile/status": api_profile_status,
              "/api/profile/results": api_profile_results, "/api/calllog": api_calllog, "/api/runbook": api_runbook}
POST_ROUTES = {"/api/db/connect": api_db_connect, "/api/db/create": api_db_create,
               "/api/schema/ingest": api_schema_ingest, "/api/depgraph/recompute": api_depgraph_recompute,
               "/api/profile/start": api_profile_start, "/api/profile/cancel": api_profile_cancel}


class Handler(BaseHTTPRequestHandler):
    server_version = "MigrationControl/1.0"

    def log_message(self, fmt, *args):  # keep the console quiet; never print headers
        pass

    def _send(self, status, payload, ctype="application/json; charset=utf-8"):
        data = payload if isinstance(payload, bytes) else scrub(json.dumps(payload)).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _guard(self, post):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        if host not in ("127.0.0.1", "localhost"):
            self._send(403, {"error": "Requests must use http://127.0.0.1 or http://localhost."})
            return False
        if post:
            origin = self.headers.get("Origin")
            if origin:
                o = urlparse(origin)
                if (o.hostname or "").lower() not in ("127.0.0.1", "localhost") or (o.port or 80) != self.server.server_port:
                    self._send(403, {"error": "Cross-site requests are not allowed."})
                    return False
            if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
                self._send(415, {"error": "Requests must be JSON."})
                return False
        return True

    def _run(self, fn, arg):
        try:
            self._send(200, fn(arg))
        except AppError as e:
            self._send(e.status, dict({"error": e.message}, **e.extra))
        except sqlite3.Error as e:
            self._send(500, {"error": "Database error: %s" % scrub(e)})
        except Exception as e:
            print(scrub(traceback.format_exc()), file=sys.stderr)
            self._send(500, {"error": "Unexpected error: %s" % scrub(e)})

    def do_GET(self):
        if not self._guard(False):
            return
        u = urlparse(self.path)
        if u.path in STATIC:
            name, ctype = STATIC[u.path]
            try:
                with open(os.path.join(BASE_DIR, name), "rb") as f:
                    self._send(200, f.read(), ctype)
            except OSError:
                self._send(404, {"error": "%s is missing next to server.py." % name})
            return
        fn = GET_ROUTES.get(u.path)
        if not fn:
            self._send(404, {"error": "Not found."})
            return
        self._run(fn, parse_qs(u.query))

    def do_POST(self):
        if not self._guard(True):
            return
        fn = POST_ROUTES.get(urlparse(self.path).path)
        if not fn:
            self._send(404, {"error": "Not found."})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n > MAX_BODY_BYTES:
            self._send(413, {"error": "That upload is too large (limit 25 MB)."})
            return
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            self._send(400, {"error": "The request was not valid JSON."})
            return
        self._run(fn, body)


def main():
    try:
        httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        print("Port %d is already in use. Close the other program or run: PORT=8800 python server.py" % PORT)
        sys.exit(1)
    httpd.daemon_threads = True
    cfg, problem = load_config()
    print("Airtable migration control plane")
    print("  Open:  http://127.0.0.1:%d" % PORT)
    print("  Token: %s" % ("configured" if cfg["airtable_pat"] else "NOT configured (%s)" % problem))
    print("  Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
