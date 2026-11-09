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
import csv
import hashlib
import io
import heapq
import html as _html
import quopri
import http.client
import json
import mimetypes
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
from email.utils import formatdate
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
MAX_RPS = 5.0   # Airtable limit per base; config "requests_per_second" may lower it
_EMPTY_CFG = {"airtable_pat": "", "db_path": "", "scopes": [], "bases": [], "requests_per_second": MAX_RPS,
             "extract_dir": ""}
HTTP_TIMEOUT = 30
MAX_BODY_BYTES = 25 * 1024 * 1024
DEFAULT_CARDINALITY_CAP = 1_000
PAGE_SIZE = 100
# Bump when the API changes. The page compares it with its own expected version, so an old server process
# left running after files were replaced is reported instead of failing with confusing 'Not found' errors.
APP_VERSION = "1.10"

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
    -- Field settings from the Metadata API (all NULL when not applicable or not reported)
    field_description TEXT,        -- the description typed on the field in Airtable
    is_primary_field INTEGER,      -- 1 = the table's primary field, 0 = not, NULL = not reported
    options_json TEXT,             -- the field's full options object exactly as returned (lossless backup of everything below)
    choices_json TEXT,             -- singleSelect / multipleSelects: [{"id","name","color"}] in Airtable's order
    formula_text TEXT,             -- formula fields: options.formula (and a rollup's formula when Airtable returns one)
    referenced_field_ids TEXT,     -- JSON array: options.referencedFieldIds (formula, rollup, lookup, lastModifiedTime)
    source_link_field_id TEXT,     -- rollup / lookup / count: options.recordLinkFieldId, the link field it reads through
    source_field_id TEXT,          -- rollup / lookup: options.fieldIdInLinkedTable, the field read in the linked table
    is_valid INTEGER,              -- formula / rollup / lookup / count: 1 = valid, 0 = broken (options.isValid)
    result_type TEXT,              -- computed fields: options.result.type (what the formula, rollup or lookup returns)
    result_options_json TEXT,      -- computed fields: options.result.options (precision, date format, choices of the result)
    number_precision INTEGER,      -- number / percent / currency: decimal places (options.precision)
    currency_symbol TEXT,          -- currency: options.symbol
    date_format TEXT,              -- date / dateTime: options.dateFormat.name (for example us, european, iso, local, friendly)
    time_format TEXT,              -- dateTime: options.timeFormat.name (12hour or 24hour)
    time_zone TEXT,                -- dateTime: options.timeZone
    duration_format TEXT,          -- duration: options.durationFormat (for example h:mm:ss)
    rating_max INTEGER,            -- rating: options.max
    icon TEXT,                     -- rating / checkbox: options.icon
    option_color TEXT,             -- rating / checkbox: options.color
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_airtable_fields_table ON airtable_fields(table_id);
CREATE INDEX idx_airtable_fields_linked ON airtable_fields(linked_table_id);

-- 1b. Table-level and view metadata from the Metadata API (no record data)
CREATE TABLE airtable_tables (
    table_id TEXT NOT NULL PRIMARY KEY,
    base_id TEXT NOT NULL,
    table_name TEXT NOT NULL,
    table_description TEXT,        -- the description typed on the table in Airtable
    primary_field_id TEXT,         -- the table's primaryFieldId
    table_json TEXT,               -- the table object as returned, minus its fields and views (lossless for anything else)
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_airtable_tables_base ON airtable_tables(base_id);

CREATE TABLE airtable_views (
    view_id TEXT NOT NULL PRIMARY KEY,
    base_id TEXT NOT NULL,
    table_id TEXT NOT NULL,
    view_name TEXT NOT NULL,
    view_type TEXT NOT NULL,       -- grid, form, calendar, gallery, kanban, timeline, block (Gantt), ...
    view_position INTEGER,         -- 0-based position in the order Airtable listed the table's views
    visible_field_ids TEXT,        -- JSON array of Airtable field IDs the view shows (requested with include=visibleFieldIds)
    view_json TEXT,                -- the view object exactly as returned (lossless backup of everything above)
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (table_id) REFERENCES airtable_tables(table_id)
);

CREATE INDEX idx_airtable_views_table ON airtable_views(table_id);
CREATE INDEX idx_airtable_views_type ON airtable_views(view_type);

-- 1c. Attachment size and type statistics (counts, bytes and file types only: no filenames, links or file contents)
CREATE TABLE airtable_attachment_stats (
    base_id TEXT NOT NULL,
    table_id TEXT NOT NULL,
    field_id TEXT NOT NULL,
    file_extension TEXT NOT NULL,   -- from the attachment filename, else the MIME type, else 'bin'
    mime_type TEXT NOT NULL,        -- as reported by Airtable, lower case, 'unknown' if none
    file_count INTEGER NOT NULL DEFAULT 0,
    total_bytes INTEGER NOT NULL DEFAULT 0,
    min_bytes INTEGER,
    max_bytes INTEGER,
    unknown_size_count INTEGER NOT NULL DEFAULT 0,  -- attachments for which Airtable reported no size
    size_lt_1mb INTEGER NOT NULL DEFAULT 0,         -- size buckets use 1 MB = 1,048,576 bytes
    size_1_10mb INTEGER NOT NULL DEFAULT 0,
    size_10_100mb INTEGER NOT NULL DEFAULT 0,
    size_100mb_1gb INTEGER NOT NULL DEFAULT 0,
    size_ge_1gb INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (field_id, file_extension, mime_type)
);

CREATE INDEX idx_attachment_stats_table ON airtable_attachment_stats(table_id);
CREATE INDEX idx_attachment_stats_base ON airtable_attachment_stats(base_id);

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
        cfg = dict(_EMPTY_CFG)
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
                token = str(data.get("token") or data.get("airtable_pat") or "").strip()   # old key still accepted
                if not token or "REPLACE" in token.upper() or "PASTE" in token.upper():
                    err = ("config.json has no Airtable token yet. Paste your personal access token into "
                           "the \"token\" value, save the file, then reload this page.")
                    token = ""
                scopes = [str(x) for x in data.get("scopes") or [] if isinstance(x, str)]
                bases = []
                for b in data.get("bases") or []:
                    if isinstance(b, dict) and BASE_ID_RE.match(str(b.get("baseId") or "")) \
                            and "XXXX" not in str(b.get("baseId")).upper():
                        bases.append({"name": str(b.get("name") or "")[:80], "baseId": str(b["baseId"])})
                try:
                    rps = float(data.get("requests_per_second") or MAX_RPS)
                except (TypeError, ValueError):
                    rps = MAX_RPS
                rps = min(max(rps, 0.5), MAX_RPS)   # Airtable allows 5 per second per base
                cfg = {"airtable_pat": token, "db_path": str(data.get("db_path") or "").strip(),
                       "scopes": scopes, "bases": bases, "requests_per_second": rps,
                       "extract_dir": str(data.get("extract_dir") or "").strip()}
            except (OSError, ValueError):
                err = ("config.json could not be read. It must be valid JSON like "
                       "{\"token\": \"pat...\", \"bases\": [{\"name\": \"...\", \"baseId\": \"app...\"}]}.")
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
                             "existing tables. Run: python sql/apply_migrations.py <this database file> "
                             "(see the README), or choose a new file.")
    return result


def api_db_create(body):
    path = resolve_path(body.get("path"))
    if os.path.isdir(path):
        raise AppError("That path is a folder. Enter the full path of a file.")
    parent = os.path.dirname(path)
    if not os.path.isdir(parent) and os.path.abspath(parent) == os.path.join(BASE_DIR, "db"):
        try:
            os.makedirs(parent, exist_ok=True)   # the app's own db folder is created for you
        except OSError as e:
            raise AppError("Could not create the folder %s: %s" % (parent, scrub(e)))
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
        "version": APP_VERSION,
        "token_configured": bool(cfg["airtable_pat"]),
        "config_problem": err,
        "default_db_path": cfg["db_path"],
        "bases": cfg["bases"],
        "scopes": cfg["scopes"],
        "requests_per_second": cfg["requests_per_second"],
        "extract_dir": extract_dir(),
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
                  status="OPEN", conn=None, timestamp=None):
    sql = """INSERT INTO runbook_logs(timestamp, source_system, failing_component, error_message,
             root_cause_summary, mitigation_steps_taken, preventative_action, status)
             VALUES (?,?,?,?,?,?,?,?)"""
    args = (timestamp or utc_now(), "Airtable", component, scrub(message), root_cause, mitigation, preventative,
            status)
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
        _next_slot[key] = slot + 1.0 / load_config()[0]["requests_per_second"]
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


OPTION_COLUMNS = ["field_description", "is_primary_field", "options_json", "choices_json", "formula_text",
                  "referenced_field_ids", "source_link_field_id", "source_field_id", "is_valid", "result_type",
                  "result_options_json", "number_precision", "currency_symbol", "date_format", "time_format",
                  "time_zone", "duration_format", "rating_max", "icon", "option_color"]


def _j(v):
    """Compact, stable JSON for a value taken from the schema (None stays None)."""
    return None if v is None else json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _s(v):
    return v if isinstance(v, str) and v != "" else None


def _i(v):
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def extract_field_settings(f, primary_id):
    """Pull the settings that define a field out of one field of the Metadata API response. Anything the
    Metadata API did not report stays None. options_json keeps the whole options object, so nothing is lost
    even for a field type or setting this function does not know about."""
    ftype = f.get("type")
    opts = f.get("options")
    opts = opts if isinstance(opts, dict) else {}
    out = dict.fromkeys(OPTION_COLUMNS)
    out["field_description"] = _s(f.get("description"))
    out["is_primary_field"] = None if not primary_id else (1 if f.get("id") == primary_id else 0)
    out["options_json"] = _j(opts) if opts else None
    if ftype in ("singleSelect", "multipleSelects") and isinstance(opts.get("choices"), list):
        out["choices_json"] = json.dumps(
            [{"id": c.get("id"), "name": c.get("name"), "color": c.get("color")}
             for c in opts["choices"] if isinstance(c, dict)], separators=(",", ":"), ensure_ascii=False)
    if isinstance(opts.get("formula"), str):
        out["formula_text"] = opts["formula"]
    if isinstance(opts.get("referencedFieldIds"), list):
        out["referenced_field_ids"] = json.dumps(opts["referencedFieldIds"], separators=(",", ":"))
    out["source_link_field_id"] = _s(opts.get("recordLinkFieldId"))
    out["source_field_id"] = _s(opts.get("fieldIdInLinkedTable"))
    if isinstance(opts.get("isValid"), bool):
        out["is_valid"] = 1 if opts["isValid"] else 0
    res = opts.get("result")
    if isinstance(res, dict):
        out["result_type"] = _s(res.get("type"))
        if isinstance(res.get("options"), dict) and res["options"]:
            out["result_options_json"] = _j(res["options"])
    if ftype in ("number", "percent", "currency"):
        out["number_precision"] = _i(opts.get("precision"))
    if ftype == "currency":
        out["currency_symbol"] = _s(opts.get("symbol"))
    if ftype in ("date", "dateTime") and isinstance(opts.get("dateFormat"), dict):
        out["date_format"] = _s(opts["dateFormat"].get("name"))
    if ftype == "dateTime":
        if isinstance(opts.get("timeFormat"), dict):
            out["time_format"] = _s(opts["timeFormat"].get("name"))
        out["time_zone"] = _s(opts.get("timeZone"))
    if ftype == "duration":
        out["duration_format"] = _s(opts.get("durationFormat"))
    if ftype == "rating":
        out["rating_max"] = _i(opts.get("max"))
    if ftype in ("rating", "checkbox"):
        out["icon"] = _s(opts.get("icon"))
        out["option_color"] = _s(opts.get("color"))
    return out


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
            row = {"table_id": t["id"], "table_name": t["name"], "field_id": f["id"],
                   "field_name": f["name"], "data_type": f["type"],
                   "linked_table_id": linked, "inverse_link_field_id": inverse,
                   "prefers_single_record_link": prefers}
            row.update(extract_field_settings(f, t.get("primaryFieldId")))
            rows.append(row)
    if not rows:
        raise AppError("The schema contains no fields, so there is nothing to ingest.")
    return rows


def parse_table_meta(doc):
    """Table-level details and views from the same Metadata API response. Returns (table rows, view rows,
    whether any table reported a views list). Each view's whole object is kept in view_json."""
    tables = doc if isinstance(doc, list) else doc.get("tables")
    trows, vrows, seen_views, reported = [], [], set(), False
    for t in tables:
        rest = {k: v for k, v in t.items() if k not in ("fields", "views")}
        trows.append({"table_id": t["id"], "table_name": t["name"], "table_description": _s(t.get("description")),
                      "primary_field_id": _s(t.get("primaryFieldId")), "table_json": _j(rest)})
        views = t.get("views")
        if not isinstance(views, list):
            continue
        reported = True
        for pos, v in enumerate(views):
            if not isinstance(v, dict) or not v.get("id"):
                raise AppError("A view in table \"%s\" has no \"id\"." % t["name"])
            if v["id"] in seen_views:
                raise AppError("View ID %s appears twice in the schema." % v["id"])
            seen_views.add(v["id"])
            vis = v.get("visibleFieldIds")
            vrows.append({"view_id": v["id"], "table_id": t["id"], "view_name": _s(v.get("name")) or "(unnamed)",
                          "view_type": _s(v.get("type")) or "unknown", "view_position": pos,
                          "visible_field_ids": json.dumps(vis, separators=(",", ":")) if isinstance(vis, list) else None,
                          "view_json": _j(v)})
    return trows, vrows, reported


SOURCE_LABELS = {"api": "Pulled from the Airtable Metadata API",
                 "json": "Provided as a JSON document (uploaded file or pasted text)"}


def upsert_schema(base_id, rows, source, tables=None, views=None, views_reported=True):
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
            missing = sorted(before_in_base - new_ids)
            summary = {"base_id": base_id,
                       "tables": len({r["table_id"] for r in rows}),
                       "fields": len(rows),
                       "added": len(new_ids - before_in_base),
                       "updated": len(new_ids & before_in_base),
                       "no_longer_in_source": len(missing),
                       "views": len(views or [])}
            with conn:
                cols = (["base_id", "table_id", "table_name", "field_id", "field_name", "data_type", "linked_table_id",
                         "inverse_link_field_id", "prefers_single_record_link"] + OPTION_COLUMNS + ["updated_at"])
                updates = ", ".join("%s=excluded.%s" % (c, c) for c in cols if c != "field_id")
                conn.executemany(
                    "INSERT INTO airtable_fields(%s) VALUES (%s) ON CONFLICT(field_id) DO UPDATE SET %s"
                    % (", ".join(cols), ",".join("?" * len(cols)), updates),
                    [tuple([base_id if c == "base_id" else ts if c == "updated_at" else r[c] for c in cols])
                     for r in rows])
                if tables:
                    conn.executemany(
                        "INSERT INTO airtable_tables(table_id, base_id, table_name, table_description, primary_field_id, "
                        "table_json, updated_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(table_id) DO UPDATE SET "
                        "base_id=excluded.base_id, table_name=excluded.table_name, "
                        "table_description=excluded.table_description, primary_field_id=excluded.primary_field_id, "
                        "table_json=excluded.table_json, updated_at=excluded.updated_at",
                        [(t["table_id"], base_id, t["table_name"], t["table_description"], t["primary_field_id"],
                          t["table_json"], ts) for t in tables])
                if views:
                    conn.executemany(
                        "INSERT INTO airtable_views(view_id, base_id, table_id, view_name, view_type, view_position, "
                        "visible_field_ids, view_json, updated_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(view_id) DO "
                        "UPDATE SET base_id=excluded.base_id, table_id=excluded.table_id, view_name=excluded.view_name, "
                        "view_type=excluded.view_type, view_position=excluded.view_position, "
                        "visible_field_ids=excluded.visible_field_ids, view_json=excluded.view_json, "
                        "updated_at=excluded.updated_at",
                        [(v["view_id"], base_id, v["table_id"], v["view_name"], v["view_type"], v["view_position"],
                          v["visible_field_ids"], v["view_json"], ts) for v in views])
                elif tables and not views_reported:
                    # The schema had no views list at all, so nothing was reported as removed: keep existing views current.
                    conn.execute("UPDATE airtable_views SET updated_at=? WHERE base_id=?", (ts, base_id))
                # Audit entry, same transaction and same timestamp as the field rows, so the report can say
                # exactly where this schema came from and when. Informational: status RESOLVED.
                write_runbook("schema_ingest:%s" % base_id,
                              "Schema ingested for base %s: %d tables, %d fields (%d new, %d refreshed, %d no longer "
                              "in source)." % (base_id, summary["tables"], summary["fields"], summary["added"],
                                               summary["updated"], summary["no_longer_in_source"]),
                              root_cause="Source: " + SOURCE_LABELS[source],
                              mitigation="Informational audit entry; no action needed.",
                              status="RESOLVED", conn=conn, timestamp=ts)
            return summary
    except sqlite3.Error as e:
        raise DbError("Database write failed: %s. Nothing from this ingest was saved." % scrub(e))


def api_schema_ingest(body):
    base_id = check_base_id(body.get("base_id"))
    source = body.get("source")
    if source == "api":
        if not get_token():
            raise AppError("No Airtable token is configured. Edit config.json (see the setup message) and reload.")
        path = "/v0/meta/bases/%s/tables" % quote(base_id, safe="")
        try:
            # include=visibleFieldIds also returns the fields each view shows
            doc = api_call(purpose="schema_fetch", path=path, query={"include": "visibleFieldIds"},
                           base_id=base_id, records_key="tables")
        except ApiError as e:
            if e.http_status not in (400, 422):
                raise
            doc = api_call(purpose="schema_fetch", path=path, base_id=base_id, records_key="tables")
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
    trows, vrows, reported = parse_table_meta(doc)
    result = upsert_schema(base_id, rows, source, trows, vrows, reported)
    result["source"] = source
    return result


def settings_summary(r):
    """One short, human-readable line of the settings that define a field (for the schema view and report)."""
    p = []
    if r["is_primary_field"] == 1:
        p.append("primary field")
    t = r["data_type"]
    if r["choices_json"]:
        try:
            names = [c.get("name") or "(unnamed)" for c in json.loads(r["choices_json"])]
            p.append("%d choices: %s" % (len(names), ", ".join(names[:8]) + (", ..." if len(names) > 8 else "")))
        except ValueError:
            pass
    if r["formula_text"]:
        p.append("formula: " + (r["formula_text"] if len(r["formula_text"]) <= 120 else r["formula_text"][:117] + "..."))
    if r["source_link_field_id"]:
        p.append("reads %s via link %s" % (r["source_field_id"] or "(count)", r["source_link_field_id"]))
    if r["result_type"]:
        p.append("returns " + r["result_type"])
    if r["currency_symbol"]:
        p.append("symbol " + r["currency_symbol"])
    if r["number_precision"] is not None:
        p.append("%d decimal place(s)" % r["number_precision"])
    if r["date_format"]:
        p.append("date " + r["date_format"])
    if r["time_format"]:
        p.append("time " + r["time_format"])
    if r["time_zone"]:
        p.append("zone " + r["time_zone"])
    if r["duration_format"]:
        p.append("duration " + r["duration_format"])
    if r["rating_max"] is not None:
        p.append("max %d" % r["rating_max"])
    if t in ("rating", "checkbox") and r["icon"]:
        p.append("icon " + r["icon"] + (" (%s)" % r["option_color"] if r["option_color"] else ""))
    if r["is_valid"] == 0:
        p.append("INVALID in Airtable")
    if r["field_description"]:
        d = r["field_description"]
        p.append("description: " + (d if len(d) <= 100 else d[:97] + "..."))
    return "; ".join(p)



VIEW_STALE_SQL = ("(v.updated_at < (SELECT MAX(x.updated_at) FROM airtable_fields x WHERE x.base_id = v.base_id))")


def load_tables_and_views(conn):
    """(table details by table_id, views by table_id), views in Airtable's order, with a stale flag.
    A table row whose table has no live fields is simply never looked up, so no stale flag is needed for it."""
    tinfo = {r["table_id"]: dict(r) for r in conn.execute("SELECT * FROM airtable_tables")}
    views = {}
    for r in conn.execute("SELECT v.*, %s AS stale FROM airtable_views v ORDER BY v.table_id, v.view_position, v.view_id"
                          % VIEW_STALE_SQL):
        views.setdefault(r["table_id"], []).append(dict(r))
    return tinfo, views


def view_field_ids(v):
    try:
        ids = json.loads(v["visible_field_ids"]) if v["visible_field_ids"] else None
    except ValueError:
        ids = None
    return ids if isinstance(ids, list) else None


def api_schema_view(_q=None):
    with db() as conn:
        rows = conn.execute("""SELECT f.*, %s AS stale FROM airtable_fields f
                               ORDER BY f.base_id, lower(f.table_name), f.table_id, lower(f.field_name), f.field_id"""
                            % STALE_SQL).fetchall()
        tinfo, vmap = load_tables_and_views(conn)
    fname = {r["field_id"]: r["field_name"] for r in rows}
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
            "settings": settings_summary(r),
            "self_link": bool(link and link == r["table_id"]),
            "stale": bool(r["stale"])})
        if r["stale"]:
            t["stale_fields"] += 1
    out = []
    for b in sorted(bases):
        tables = []
        for t in sorted(bases[b]["tables"].values(), key=lambda x: (x["table_name"].lower(), x["table_id"])):
            t["stale"] = t["stale_fields"] == len(t["fields"])
            info = tinfo.get(t["table_id"]) or {}
            t["description"] = info.get("table_description")
            t["primary_field_id"] = info.get("primary_field_id")
            t["primary_field_name"] = fname.get(info.get("primary_field_id"))
            t["details_captured"] = bool(info)
            t["views"] = []
            for v in vmap.get(t["table_id"], []):
                ids = view_field_ids(v)
                t["views"].append({"view_id": v["view_id"], "view_name": v["view_name"], "view_type": v["view_type"],
                                   "visible_field_count": None if ids is None else len(ids),
                                   "stale": bool(v["stale"])})
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
        was not reported), the direction is unknown, so both fields stay as edges and the two tables form a
        cycle that loads together, with the links set in a second pass."""
    if mode not in ("literal", "collapse"):
        raise AppError("Unknown dependency mode.")
    rows = [r for r in conn.execute("SELECT f.*, %s AS stale FROM airtable_fields f" % STALE_SQL) if not r["stale"]]
    tables, base_of = {}, {}
    for r in rows:
        tables[r["table_id"]] = r["table_name"]
        base_of[r["table_id"]] = r["base_id"]
    by_field = {r["field_id"]: r for r in rows}
    links = [r for r in rows if r["linked_table_id"]]

    # inverse pair detection (always computed; only used for ordering in 'collapse' mode)
    paired, oriented, pair_list, pair_other = set(), set(), {}, {}
    for a in links:
        if a["table_id"] == a["linked_table_id"] or not a["inverse_link_field_id"]:
            continue
        b = by_field.get(a["inverse_link_field_id"])
        if b and b["linked_table_id"] == a["table_id"] and b["table_id"] == a["linked_table_id"]:
            paired.add(a["field_id"])
            paired.add(b["field_id"])
            pair_other[a["field_id"]] = b["field_id"]
            pair_other[b["field_id"]] = a["field_id"]
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
        if mode == "collapse" and a["field_id"] in paired and pair_other[a["field_id"]] in oriented:
            continue   # reverse side of a pair whose direction is set by the other field
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
            matrix.append({"table_id": n, "table_name": tables[n], "base_id": base_of[n],
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
    # How every link field is treated, for the report: does it order the load, or wait for a second pass?
    link_fields = []
    for a in sorted(links, key=lambda r: (r["table_name"].lower(), r["field_name"].lower(), r["field_id"])):
        tgt = a["linked_table_id"]
        if tgt == a["table_id"]:
            cls, second, why = ("Self-link", True,
                                "Links to its own table. Load the table's records first, then set these links.")
        elif tgt not in tables:
            cls, second, why = ("Unresolved", True,
                                "The linked table is not in the database. Ingest its base, then recompute.")
        elif mode == "collapse" and a["field_id"] in paired and pair_other[a["field_id"]] in oriented:
            cls, second, why = ("Inverse of an ordered pair", False,
                                "Reverse side of a two-way link whose direction is set by the other field. "
                                "Adds no ordering.")
        elif comp_of[a["table_id"]] == comp_of[tgt]:
            cls, second, why = ("In a dependency cycle", True,
                                "Part of a loop between tables. Load the records first, then set these links "
                                "in a second pass.")
        else:
            cls, second, why = ("Orders the load", False,
                                "The linked (parent) table must be loaded before this table.")
        link_fields.append({"base_id": a["base_id"], "table_id": a["table_id"], "table_name": a["table_name"],
                            "field_id": a["field_id"], "field_name": a["field_name"], "linked_table_id": tgt,
                            "linked_table_name": tables.get(tgt, tgt), "treatment": cls, "second_pass": second,
                            "explanation": why})
    return {"mode": mode, "matrix": matrix, "edges": edge_list, "cycles": cycles, "self_links": self_links,
            "unresolved_links": unresolved, "inverse_pairs": pairs, "link_fields": link_fields}


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
        self.save_records = False   # True = also write every record to the JSON files (RecordSink)
        self.names = {}             # field_id -> field name, only used when saving records
        self.sinks = {}             # table_id -> open RecordSink
        self.download_files = False # True = also download attachment files (needs save_records)


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
    save = bool(body.get("save_records"))
    dl = bool(body.get("download_attachments"))
    if dl and not save:
        raise AppError("Downloading attachment files needs \"Also save every record to JSON files\" to be ticked too, "
                       "because the files are matched to records through the saved JSON.")
    names = {}
    if save:
        for t in tables:
            _extract_path(t["base_id"], t["table_id"])   # refuse unsafe IDs before starting
        with db() as conn:
            names = {r["field_id"]: r["field_name"] for r in conn.execute("SELECT field_id, field_name FROM airtable_fields")}
    if not get_token():
        raise AppError("No Airtable token is configured. Edit config.json (see the setup message) and reload.")
    with _job_lock:
        cur = JOB["current"]
        if cur and cur.status == "running":
            raise AppError("A profiling run is already in progress. Wait for it to finish or cancel it.")
        job = Job(scope, cap, [{"base_id": t["base_id"], "table_id": t["table_id"], "table_name": t["table_name"],
                                "fields": len(t["fields"]), "status": "pending", "pages": 0, "records": 0,
                                "error": None, "capped_fields": 0, "saved": False,
                                "files": 0, "file_bytes": 0, "file_failed": 0} for t in tables])
        job.save_records, job.names, job.download_files = save, names, dl
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
    sink = None
    att_fields = {fid for fid, ftype in fields if ftype == "multipleAttachments"}
    att_stats = {}   # (field_id, extension, mime) -> counters; sizes and types only, never names or links
    if job.save_records:
        sink = job.sinks[t["table_id"]] = RecordSink(job, t)
    while True:
        q = {"pageSize": PAGE_SIZE, "returnFieldsByFieldId": "true"}
        if offset:
            q["offset"] = offset
        body = api_call(purpose="profile_page", path=path, query=q, base_id=t["base_id"], table_id=t["table_id"],
                        stats=job.api, cancel=job.cancel, records_key="records")
        for rec in body.get("records") or []:
            if sink:
                sink.add(rec)
            fv = rec.get("fields") or {}
            row_count += 1
            for fid in att_fields:
                for a in fv.get(fid) or []:
                    if isinstance(a, dict):
                        _tally_attachment(att_stats, fid, a)
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
    return row_count, nulls, sets, capped, att_stats


MB = 1048576
ATT_BUCKET_EDGES = (MB, 10 * MB, 100 * MB, 1024 * MB)   # < 1 MB, 1-10, 10-100, 100 MB-1 GB, >= 1 GB


def _tally_attachment(stats, fid, a):
    """Add one attachment to the per-(field, extension, MIME type) counters. Sizes and types only."""
    mime = str(a.get("type") or "").split(";")[0].strip().lower() or "unknown"
    key = (fid, attachment_ext(a.get("filename"), a.get("type")), mime)
    c = stats.setdefault(key, {"n": 0, "bytes": 0, "min": None, "max": None, "unknown": 0, "buckets": [0] * 5})
    c["n"] += 1
    size = a.get("size")
    if isinstance(size, bool) or not isinstance(size, (int, float)) or size < 0:
        c["unknown"] += 1
        return
    size = int(size)
    c["bytes"] += size
    c["min"] = size if c["min"] is None else min(c["min"], size)
    c["max"] = size if c["max"] is None else max(c["max"], size)
    c["buckets"][sum(1 for e in ATT_BUCKET_EDGES if size >= e)] += 1


def _save_table(job, t, row_count, nulls, sets, capped, att_stats=None):
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
                # Attachment sizes and types for this table are replaced as a whole, so a re-run never double counts.
                conn.execute("DELETE FROM airtable_attachment_stats WHERE table_id=?", (t["table_id"],))
                ts = utc_now()
                for (fid, ext, mime), c in sorted((att_stats or {}).items()):
                    conn.execute("INSERT INTO airtable_attachment_stats(base_id, table_id, field_id, file_extension, "
                                 "mime_type, file_count, total_bytes, min_bytes, max_bytes, unknown_size_count, "
                                 "size_lt_1mb, size_1_10mb, size_10_100mb, size_100mb_1gb, size_ge_1gb, updated_at) "
                                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (t["base_id"], t["table_id"], fid, ext, mime, c["n"], c["bytes"], c["min"], c["max"],
                                  c["unknown"], *c["buckets"], ts))
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
                sink = job.sinks.pop(t["table_id"], None)
                if sink:   # only after the statistics were saved, so file and statistics describe the same pass
                    sink.finish()
                    t["saved"] = True
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
            except AppError as e:   # for example a duplicate record while saving
                t["status"] = "failed"
                t["error"] = e.message
            except OSError as e:
                t["status"] = "failed"
                t["error"] = "Could not write the record file: %s" % scrub(e)
            finally:
                leftover = job.sinks.pop(t["table_id"], None)
                if leftover:
                    leftover.abort()
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
                    "save_records": job.save_records, "download_files": job.download_files, "tables": [dict(t) for t in job.tables]}}


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
# Optional record capture: while profiling reads every page, it can also write every record, every field, into
# temporary JSON files (a toggle on the profiling run, so there is one pass over the data, not two).
# Record values go ONLY to these files: never to SQLite, the API call log, runbook entries, error messages or the
# browser. The browser sees counts, sizes and checksums, never values.
# --------------------------------------------------------------------------
ID_SAFE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_manifest_lock = threading.Lock()
EXTRACT_FORMAT = "airtable-table-extract/1"


def extract_dir():
    d = (load_config()[0].get("extract_dir") or "data_extract")
    return os.path.abspath(d if os.path.isabs(d) else os.path.join(BASE_DIR, d))


def _extract_path(base_id, table_id):
    if not ID_SAFE_RE.match(base_id or "") or not ID_SAFE_RE.match(table_id or ""):
        raise AppError("A base or table ID contains characters that are not allowed in a file name.")
    return os.path.join(extract_dir(), base_id, table_id + ".json")


def _manifest_path():
    return os.path.join(extract_dir(), "manifest.json")


def _read_manifest():
    try:
        with open(_manifest_path(), encoding="utf-8") as f:
            m = json.load(f)
        return m if isinstance(m, dict) and isinstance(m.get("tables"), list) else {"tables": []}
    except (OSError, ValueError):
        return {"tables": []}


def _write_manifest(m):
    path = _manifest_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=1)
    os.replace(tmp, path)


def _file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _private(path, is_dir=False):
    try:
        os.chmod(path, 0o700 if is_dir else 0o600)   # best effort; Windows ignores most of this
    except OSError:
        pass


class RecordSink:
    """Streams one table's records into <extract_dir>/<base>/<table>.json while profiling reads the pages.
    The file is written to a .tmp name and only replaces the previous file in finish(), so a table that fails
    partway leaves its earlier file untouched."""

    def __init__(self, job, t):
        self.path = _extract_path(t["base_id"], t["table_id"])
        self.tmp = self.path + ".tmp"
        self.t, self.n, self.seen = t, 0, set()
        self.job = job
        self.att_fields = [fid for fid, ftype in job.fields[t["table_id"]] if ftype == "multipleAttachments"] \
            if job.download_files else []
        self.att_index, self.att_done = [], {}    # index entries; attachment id -> result of its download
        self.captured = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.fields_meta = [{"field_id": fid, "field_name": job.names.get(fid, fid), "data_type": ftype}
                            for fid, ftype in job.fields[t["table_id"]]]
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        _private(extract_dir(), True)
        _private(os.path.dirname(self.path), True)
        self.f = open(self.tmp, "w", encoding="utf-8")
        _private(self.tmp)
        self.f.write('{"format":%s,"base_id":%s,"table_id":%s,"table_name":%s,"captured_at":%s,"fields":%s,"records":['
                     % tuple(json.dumps(x, ensure_ascii=False) for x in
                             (EXTRACT_FORMAT, t["base_id"], t["table_id"], t["table_name"], self.captured,
                              self.fields_meta)))

    def add(self, rec):
        rid = rec.get("id")
        if not rid or rid in self.seen:
            raise AppError("Airtable returned a record twice (or without an ID) while paging %s, which can happen "
                           "when the table is edited during the run. Run it again." % self.t["table_name"])
        self.seen.add(rid)
        self.f.write(("," if self.n else "") + json.dumps(rec, ensure_ascii=False, separators=(",", ":")))
        self.n += 1
        if self.att_fields:
            self._attachments(rec)

    def _attachments(self, rec):
        """Download every attachment in this record now, while Airtable's temporary links are fresh."""
        t, job = self.t, self.job
        for fid in self.att_fields:
            for a in (rec.get("fields") or {}).get(fid) or []:
                if not isinstance(a, dict):
                    continue
                if job.cancel.is_set():
                    raise Cancelled()
                aid = a.get("id")
                if aid in self.att_done:      # the same attachment seen twice: reuse the first download
                    res = self.att_done[aid]
                else:
                    res = download_attachment(self.t["base_id"], a, job.cancel)
                    if aid:
                        self.att_done[aid] = res
                    if res["status"] == "failed":
                        t["file_failed"] += 1
                    else:
                        t["files"] += 1
                        t["file_bytes"] += res["bytes"]
                self.att_index.append({"record_id": rec.get("id"), "field_id": fid, "attachment_id": aid,
                                       "filename": a.get("filename"), "type": a.get("type"), "size": a.get("size"),
                                       "file": res.get("file"), "bytes": res.get("bytes"), "sha256": res.get("sha256"),
                                       "status": res["status"], "error": res.get("error")})

    def finish(self):
        t = self.t
        self.f.write('],"record_count":%d}' % self.n)
        self.f.close()
        os.replace(self.tmp, self.path)
        att = None
        if self.job.download_files:
            att = self._write_att_index()
        entry = {"base_id": t["base_id"], "table_id": t["table_id"], "table_name": t["table_name"],
                 "file": "%s/%s.json" % (t["base_id"], t["table_id"]), "record_count": self.n,
                 "bytes": os.path.getsize(self.path), "sha256": _file_digest(self.path),
                 "captured_at": self.captured, "fields": len(self.fields_meta)}
        if att:
            entry["attachments"] = att
        with _manifest_lock:
            m = _read_manifest()
            m["tables"] = [x for x in m["tables"] if x.get("table_id") != t["table_id"]] + [entry]
            m["tables"].sort(key=lambda x: (x["base_id"], x["table_name"].lower(), x["table_id"]))
            m["note"] = ("Temporary working copy of Airtable record data. Contains record values; keep it out of "
                         "source control and delete it when the migration no longer needs it. This manifest holds "
                         "counts and checksums only.")
            _write_manifest(m)

    def _write_att_index(self):
        t = self.t
        ipath = os.path.join(os.path.dirname(self.path), t["table_id"] + ".attachments.json")
        tmp = ipath + ".tmp"
        failed = [e for e in self.att_index if e["status"] == "failed"]
        doc = {"format": "airtable-attachment-index/1", "base_id": t["base_id"], "table_id": t["table_id"],
               "captured_at": self.captured, "folder": "attachments", "attachments": self.att_index}
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        _private(tmp)
        os.replace(tmp, ipath)
        uniq = {e["attachment_id"]: e for e in self.att_index if e["status"] != "failed"}
        return {"files": len(uniq), "bytes": sum(e["bytes"] or 0 for e in uniq.values()), "failed": len(failed),
                "index": "%s/%s.attachments.json" % (t["base_id"], t["table_id"])}

    def abort(self):
        try:
            self.f.close()
        except OSError:
            pass
        try:
            os.remove(self.tmp)
        except OSError:
            pass


# --------------------------------------------------------------------------
# Attachment files. Airtable's attachment links are signed and expire after a few hours, so the files are
# downloaded in the same pass that reads the records. The links are NOT secret-free: they are never logged,
# never put in an error message and never sent to the browser, and the Airtable token is never sent to them.
# --------------------------------------------------------------------------
ATT_HOST_SUFFIXES = ("airtableusercontent.com", "airtable.com")
ATT_CHUNK = 256 * 1024
ATT_TRIES = 3


def attachment_ext(filename, mime):
    """Extension for the saved file: the one in the original filename if it is plain, else one from the MIME type."""
    ext = os.path.splitext(str(filename or ""))[1].lstrip(".").lower()
    if re.match(r"^[a-z0-9]{1,10}$", ext):
        return ext
    guess = (mimetypes.guess_extension(str(mime or "").split(";")[0].strip()) or "").lstrip(".").lower()
    if guess == "jpe":
        guess = "jpg"
    return guess if re.match(r"^[a-z0-9]{1,10}$", guess) else "bin"


def _attachment_url_ok(url):
    if not isinstance(url, str):
        return False
    if AIRTABLE_API_BASE != "https://api.airtable.com" and url.startswith(AIRTABLE_API_BASE + "/"):
        return True   # the mock-server test plan serves files from the mock
    u = urlparse(url)
    h = (u.hostname or "").lower()
    return u.scheme == "https" and any(h == x or h.endswith("." + x) for x in ATT_HOST_SUFFIXES)


def download_attachment(base_id, a, cancel):
    """Download one attachment to <extract_dir>/<base>/attachments/<attachment id>.<ext>. Never raises for a bad
    file (the result says failed and why, without the link); raises Cancelled if the run is cancelled."""
    aid = a.get("id")
    if not isinstance(aid, str) or not ID_SAFE_RE.match(aid):
        return {"status": "failed", "error": "attachment has no usable ID"}
    if not _attachment_url_ok(a.get("url")):
        return {"status": "failed", "error": "attachment link is not an Airtable file link"}
    ext = attachment_ext(a.get("filename"), a.get("type"))
    rel = "attachments/%s.%s" % (aid, ext)
    d = os.path.join(extract_dir(), base_id, "attachments")
    path, part = os.path.join(d, "%s.%s" % (aid, ext)), os.path.join(d, "%s.%s.part" % (aid, ext))
    want = a.get("size") if isinstance(a.get("size"), int) else None
    os.makedirs(d, exist_ok=True)
    _private(extract_dir(), True)
    _private(os.path.dirname(d), True)
    _private(d, True)
    if os.path.isfile(path) and (want is None or os.path.getsize(path) == want):   # already have it: keep it
        return {"status": "existing", "file": rel, "bytes": os.path.getsize(path), "sha256": _file_digest(path)}
    err = "download failed"
    for attempt in range(ATT_TRIES):
        if cancel.is_set():
            raise Cancelled()
        h, n = hashlib.sha256(), 0
        try:
            # No Authorization header: the link is already signed, and the token must never leave for another host.
            req = urllib.request.Request(a["url"], method="GET", headers={"User-Agent": "airtable-migration-control"})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp, open(part, "wb") as out:
                _private(part)
                while True:
                    if cancel.is_set():
                        raise Cancelled()
                    chunk = resp.read(ATT_CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    h.update(chunk)
                    n += len(chunk)
            if want is not None and n != want:
                err = "downloaded %d bytes but Airtable reported %d" % (n, want)
                continue
            os.replace(part, path)
            return {"status": "downloaded", "file": rel, "bytes": n, "sha256": h.hexdigest()}
        except Cancelled:
            _rm(part)
            raise
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404, 410):
                _rm(part)
                return {"status": "failed", "error": "link expired or file no longer available (HTTP %d)" % e.code}
            err = "HTTP %d" % e.code
        except (urllib.error.URLError, http.client.HTTPException, socket.timeout, TimeoutError, ConnectionError) as e:
            err = "network error (%s)" % type(e).__name__    # the exception text can contain the link, so it is not kept
        except OSError as e:
            _rm(part)
            return {"status": "failed", "error": "could not write the file (%s)" % type(e).__name__}
        time.sleep(min(2.0 ** attempt, 8.0) * (0.2 if AIRTABLE_API_BASE != "https://api.airtable.com" else 1.0))
    _rm(part)
    return {"status": "failed", "error": err}


def _rm(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _profile_row_counts():
    with db() as conn:
        return {r[0]: r[1] for r in conn.execute(
            "SELECT f.table_id, MAX(f.row_count) FROM airtable_fields f WHERE NOT %s GROUP BY f.table_id" % STALE_SQL)}


def api_extract_files(_q=None):
    """What is on disk, from the manifest: counts, sizes, checksums and a count check against the profile. No values."""
    counts = _profile_row_counts()
    out = []
    with _manifest_lock:
        m = _read_manifest()
    for e in m["tables"]:
        try:
            path = _extract_path(e["base_id"], e["table_id"])
            if not os.path.isfile(path):
                continue
            size = os.path.getsize(path)
        except (AppError, OSError):
            continue
        prof = counts.get(e["table_id"]) or 0
        check = "no profile" if not prof and e["record_count"] else ("matches profile" if prof == e["record_count"] else "differs from profile (%s)" % prof)
        out.append(dict(e, bytes=size, check=check))
    return {"folder": extract_dir(), "files": out,
            "total_records": sum(x["record_count"] for x in out), "total_bytes": sum(x["bytes"] for x in out),
            "total_attachment_files": sum((x.get("attachments") or {}).get("files", 0) for x in out),
            "total_attachment_bytes": sum((x.get("attachments") or {}).get("bytes", 0) for x in out),
            "total_attachment_failed": sum((x.get("attachments") or {}).get("failed", 0) for x in out)}


def api_extract_delete(body):
    """Delete the extracted files (all, or one base) and their manifest entries. The database is not touched."""
    raw = (body or {}).get("base_id")
    only = check_base_id(raw) if raw else None
    root, removed = extract_dir(), 0
    try:
        subdirs = sorted(d for d in os.listdir(root) if BASE_ID_RE.match(d) and os.path.isdir(os.path.join(root, d)))
    except OSError:
        subdirs = []
    for d in subdirs:
        if only and d != only:
            continue
        p = os.path.join(root, d)
        for fn in os.listdir(p):
            if fn == "attachments" and os.path.isdir(os.path.join(p, fn)):
                ad = os.path.join(p, fn)
                for af in os.listdir(ad):
                    if re.match(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9]{1,10}){1,2}$", af):
                        try:
                            os.remove(os.path.join(ad, af))
                            removed += 1
                        except OSError as e:
                            raise AppError("Could not delete %s/attachments/%s: %s" % (d, af, scrub(e)))
                try:
                    os.rmdir(ad)
                except OSError:
                    pass
            elif re.match(r"^[A-Za-z0-9_-]+(\.attachments)?\.json(\.tmp)?$", fn):
                try:
                    os.remove(os.path.join(p, fn))
                    removed += 1
                except OSError as e:
                    raise AppError("Could not delete %s/%s: %s" % (d, fn, scrub(e)))
        try:
            os.rmdir(p)
        except OSError:
            pass
    with _manifest_lock:
        m = _read_manifest()
        m["tables"] = [x for x in m["tables"] if only and x.get("base_id") != only]
        try:
            if m["tables"]:
                _write_manifest(m)
            elif os.path.exists(_manifest_path()):
                os.remove(_manifest_path())
        except OSError as e:
            raise AppError("Could not update the manifest: %s" % scrub(e))
    try:
        os.rmdir(root)
    except OSError:
        pass
    return {"removed": removed}


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
# Load order report (self-contained HTML, also packaged as MHTML)
# Generated from the database only. Contains schema metadata, never record data. No LLM involved.
# --------------------------------------------------------------------------
def esc(x):
    return _html.escape("" if x is None else str(x), quote=True)


def _ts(ts):
    return (str(ts)[:19] + " UTC") if ts else "unknown"


def _code(x):
    return "<code>%s</code>" % esc(x)


REPORT_CSS = """
body{font:14px/1.5 -apple-system,"Segoe UI",Roboto,Arial,sans-serif;color:#1d2330;margin:0;background:#fff}
main{max-width:1180px;margin:0 auto;padding:24px 28px 48px}
h1{font-size:24px;margin:0 0 4px}
h2{font-size:18px;margin:34px 0 8px;padding-bottom:4px;border-bottom:2px solid #1f5fd1}
h3{font-size:15px;margin:20px 0 6px}
.meta{color:#5d667a;font-size:13px}
table{border-collapse:collapse;width:100%;margin:8px 0 14px;font-size:12.5px}
th,td{border:1px solid #d5d9e2;padding:4px 7px;text-align:left;vertical-align:top}
th{background:#eef1f7}
td.n,th.n{text-align:right}
code{background:#eef0f4;padding:0 4px;border-radius:3px;font-size:12px;white-space:nowrap}
.rep{color:#a0a8b8}
tr.second td{background:#fff6e5}
.box{border:1px solid #d5d9e2;border-radius:6px;padding:10px 14px;margin:10px 0;background:#f8f9fc}
.box.warn{background:#fff3df;border-color:#f0cf9a}
.box.err{background:#fdeceb;border-color:#f0b8b3}
.tag{display:inline-block;font-size:11px;padding:0 6px;border-radius:8px;background:#e9ecf3;margin-left:4px}
.tag.warn{background:#ffe5b8}
.graph{overflow:auto;border:1px solid #d5d9e2;border-radius:6px;padding:6px;background:#fcfcfd}
li{margin:3px 0}
.sign td{height:34px}
@media print{main{max-width:none;padding:0}h2{break-after:avoid}tr{break-inside:avoid}.graph{overflow:visible}}
"""

MODE_TEXT = {
    "literal": ("Literal",
                "Every link field is a dependency: a table that links to another table is the child, and the "
                "linked table is its parent. Self-links are ignored for ordering."),
    "collapse": ("Resolve two-way link pairs",
                 "As Literal, except that a link field and its inverse (a two-way pair) are resolved with "
                 "Airtable's single-record-link hint. If exactly one side is a single-record link, that side's "
                 "table is the child and only that field orders the load. Otherwise the pair adds no ordering "
                 "and its links are set in a second pass."),
}


def svg_graph(nodes, edges, self_tables):
    """Dependency graph as inline SVG. Parents on the left, children on the right."""
    if not nodes:
        return ""
    W, H, GX, GY, PAD = 210, 30, 90, 16, 20
    cols = {}
    for n in nodes:
        cols.setdefault(n["dependency_depth"], []).append(n)
    depths = sorted(cols)
    pos, max_rows = {}, 0
    for ci, d in enumerate(depths):
        cols[d].sort(key=lambda n: n["load_order_sequence"])
        max_rows = max(max_rows, len(cols[d]))
        for ri, n in enumerate(cols[d]):
            pos[n["table_id"]] = (PAD + ci * (W + GX), PAD + ri * (H + GY))
    width = PAD * 2 + len(depths) * W + (len(depths) - 1) * GX + 40
    height = PAD * 2 + max_rows * H + (max_rows - 1) * GY
    cyc = {n["table_id"] for n in nodes if n["in_cycle"]}
    names = {n["table_id"]: n["table_name"] for n in nodes}
    o = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" role="img" '
         'aria-label="Table dependency graph" style="max-width:100%%;height:auto;font:12px sans-serif">'
         % (width, height, width, height)]
    for mid, color in (("arr", "#8a93a6"), ("arrc", "#b3261e")):
        o.append('<defs><marker id="%s" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
                 'orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="%s"/></marker></defs>' % (mid, color))
    for e in edges:
        p, c = pos.get(e["parent"]), pos.get(e["child"])
        if not p or not c:
            continue
        in_cyc = e["parent"] in cyc and e["child"] in cyc
        if p[0] == c[0]:
            x1, y1, y2 = p[0] + W, p[1] + H / 2, c[1] + H / 2
            d = "M%g,%g C%g,%g %g,%g %g,%g" % (x1, y1, x1 + 45, y1, x1 + 45, y2, x1, y2)
        else:
            x1, y1, x2, y2 = p[0] + W, p[1] + H / 2, c[0], c[1] + H / 2
            mx = (x1 + x2) / 2
            d = "M%g,%g C%g,%g %g,%g %g,%g" % (x1, y1, mx, y1, mx, y2, x2, y2)
        o.append('<path d="%s" fill="none" stroke="%s" stroke-width="1.3" marker-end="url(#%s)"><title>%s</title></path>'
                 % (d, "#b3261e" if in_cyc else "#8a93a6", "arrc" if in_cyc else "arr",
                    esc("%s → %s: %s" % (names.get(e["parent"]), names.get(e["child"]), ", ".join(e["fields"])))))
    for n in nodes:
        x, y = pos[n["table_id"]]
        label = "%d. %s" % (n["load_order_sequence"], n["table_name"])
        if len(label) > 30:
            label = label[:29] + "…"
        o.append('<g><rect x="%d" y="%d" width="%d" height="%d" rx="5" fill="%s" stroke="%s" stroke-width="%s"/>'
                 '<text x="%d" y="%d" fill="#1d2330">%s</text>%s<title>%s</title></g>'
                 % (x, y, W, H, "#fff6f5" if n["in_cycle"] else "#fff", "#b3261e" if n["in_cycle"] else "#7b8498",
                    "2" if n["in_cycle"] else "1.2", x + 8, y + H // 2 + 4, esc(label),
                    ('<text x="%d" y="%d" fill="#1d2330">↻</text>' % (x + W - 18, y + H // 2 + 4))
                    if n["table_id"] in self_tables else "",
                    esc("%s (%s)" % (n["table_name"], n["table_id"]))))
    o.append("</svg>")
    return "".join(o)


def human_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024


def _tr(cells, cls=""):
    return "<tr%s>%s</tr>" % ((' class="%s"' % cls) if cls else "", "".join(cells))


def _td(html_text, cls=""):
    return "<td%s>%s</td>" % ((' class="%s"' % cls) if cls else "", html_text)


def _th(labels):
    return "<tr>%s</tr>" % "".join("<th%s>%s</th>" % (' class="n"' if l.endswith("#") or l == "Depth" else "", l)
                                   for l in labels)


def build_report(mode):
    if mode not in MODE_TEXT:
        raise AppError("Unknown link-handling mode.")
    with db() as conn:
        g = compute_graph(conn, mode)
        frows = conn.execute("SELECT f.*, %s AS stale FROM airtable_fields f ORDER BY f.base_id, "
                             "lower(f.table_name), f.table_id, lower(f.field_name), f.field_id" % STALE_SQL).fetchall()
        saved = [{"table_id": r["table_id"], "table_name": r["table_name"],
                  "parent_table_ids": json.loads(r["parent_table_ids"] or "[]"),
                  "load_order_sequence": r["load_order_sequence"], "dependency_depth": r["dependency_depth"]}
                 for r in conn.execute("SELECT * FROM dependency_matrix ORDER BY load_order_sequence")]
        audits = [dict(r) for r in conn.execute(
            "SELECT error_id, timestamp, failing_component, error_message, root_cause_summary FROM runbook_logs "
            "WHERE failing_component LIKE 'schema_ingest:%' ORDER BY error_id")]
        db_name = os.path.basename(STATE["db_path"] or "")
    if not frows:
        raise AppError("There is no schema in the database yet. Ingest a schema (step 3) first.")
    fresh = [{k: m[k] for k in ("table_id", "table_name", "parent_table_ids", "load_order_sequence",
                                "dependency_depth")} for m in g["matrix"]]
    in_sync = fresh == saved
    tname = {r["table_id"]: r["table_name"] for r in frows}
    bases = {}
    for r in frows:
        b = bases.setdefault(r["base_id"], {"tables": set(), "fields": 0, "stale": 0, "last": ""})
        b["tables"].add(r["table_id"])
        b["fields"] += 1
        b["stale"] += 1 if r["stale"] else 0
        b["last"] = max(b["last"], r["updated_at"] or "")

    def source_for(base):
        a = next((x for x in reversed(audits)
                  if x["failing_component"] == "schema_ingest:" + base and x["timestamp"] == bases[base]["last"]), None)
        if a and a["root_cause_summary"]:
            return a["root_cause_summary"].replace("Source: ", "", 1)
        return "Not recorded (this schema was ingested before audit entries were added)"

    mode_name, mode_desc = MODE_TEXT[mode]
    self_tables = {s["table_id"] for s in g["self_links"]}
    second = [l for l in g["link_fields"] if l["second_pass"]]
    h = []
    h.append("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
             "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
             "<title>Airtable Load Order Report</title><style>%s</style></head><body><main>" % REPORT_CSS)
    h.append("<h1>Airtable schema and load order report</h1>")
    h.append("<p class=\"meta\">Generated %s by the Airtable &rarr; Workfront Planning migration control plane "
             "(stage 1) from database file %s. Link handling: <strong>%s</strong>.</p>"
             % (esc(_ts(utc_now())), _code(db_name), esc(mode_name)))

    # 1. About
    h.append("<h2>1. About this report</h2>")
    h.append("<div class=\"box\"><p><strong>Where the data came from.</strong> Everything here comes from the "
             "<em>schema</em> (table, field and link definitions) of the Airtable bases listed in section 2, as "
             "captured in the database file named above. It does not contain any Airtable records, and none were "
             "stored to produce it. The load order is computed from the link fields in that schema.</p>"
             "<p><strong>For auditors:</strong> section 2 records the source and date of each schema, section 3 lists "
             "every table and field exactly as captured, and section 7 explains how the order is derived and how "
             "to re-check it. The computation is deterministic and uses no AI: the same schema always gives the "
             "same report content.</p>"
             "<p><strong>For whoever runs the next stage:</strong> section 4 is the order in which to load "
             "tables, section 5 lists the link fields that must wait for a second pass, and section 6 is the "
             "dependency graph. The next stage maps these fields to Workfront Planning fields and loads records "
             "in batches of 100.</p></div>")
    if not in_sync:
        h.append("<div class=\"box warn\"><strong>Note:</strong> the load order saved in the database "
                 "(<code>dependency_matrix</code>) differs from the order computed here for the current schema and "
                 "link handling. This report shows the freshly computed order. Click \"Recompute load order\" in "
                 "the app, with the same link handling, to save it.</div>")

    # 2. Provenance
    h.append("<h2>2. Schema source and date</h2><table>")
    h.append(_th(["Base", "Schema source", "Captured (UTC)", "Tables", "Fields", "Fields no longer in source"]))
    for base in sorted(bases):
        b = bases[base]
        h.append(_tr([_td(_code(base)), _td(esc(source_for(base))), _td(esc(_ts(b["last"]))),
                      _td(str(len(b["tables"])), "n"), _td(str(b["fields"]), "n"), _td(str(b["stale"]), "n")]))
    h.append("</table><p class=\"meta\">\"Captured\" is the moment the schema was pulled from Airtable or the file "
             "was provided to the app. If a base was ingested more than once, the latest ingest is shown above and "
             "every ingest appears in the history below. \"No longer in source\" fields were in an earlier ingest "
             "but absent from the latest one; they are kept for reference and excluded from the load order.</p>")
    if audits:
        h.append("<h3>Ingest history</h3><table>")
        h.append(_th(["Time (UTC)", "Base", "Source", "Event"]))
        for a in audits:
            h.append(_tr([_td(esc(_ts(a["timestamp"]))), _td(_code(a["failing_component"].split(":", 1)[1])),
                          _td(esc((a["root_cause_summary"] or "").replace("Source: ", "", 1))),
                          _td(esc(a["error_message"]))]))
        h.append("</table>")
    n_link = len(g["link_fields"])
    h.append("<h3>Summary</h3><ul>"
             "<li>%d base(s), %d table(s), %d field(s) captured (%d link fields).</li>"
             "<li>%d dependency cycle(s); %d self-link field(s); %d two-way link pair(s); %d unresolved link(s).</li>"
             "<li>%d of %d link fields must be set in a second pass (section 5).</li></ul>"
             % (len(bases), len(tname), len(frows), n_link, len(g["cycles"]), len(g["self_links"]),
                len(g["inverse_pairs"]), len(g["unresolved_links"]), len(second), n_link))

    # 3. Inventory
    h.append("<h2>3. Schema inventory: bases, tables and fields</h2>"
             "<p class=\"meta\">One row per field, as captured. Repeated base and table values are shown in grey so "
             "the grid can be filtered or copied into a spreadsheet. The Settings column summarizes the options that define "
             "each field (choices, formulas, precision, formats); the full options are stored in the database.</p><table>")
    h.append(_th(["Base", "Table ID", "Table", "Field ID", "Field", "Type", "Settings"]))
    prev_base = prev_table = None
    for r in frows:
        same_base, same_table = r["base_id"] == prev_base, r["table_id"] == prev_table
        typ = esc(r["data_type"])
        if r["linked_table_id"]:
            typ += " &rarr; %s (%s)" % (esc(tname.get(r["linked_table_id"], r["linked_table_id"])),
                                         _code(r["linked_table_id"]))
            if r["prefers_single_record_link"] == 1:
                typ += " &middot; single-record link"
            elif r["prefers_single_record_link"] == 0:
                typ += " &middot; multiple records"
            if r["inverse_link_field_id"]:
                typ += " &middot; inverse field " + _code(r["inverse_link_field_id"])
        if r["stale"]:
            typ += " <span class=\"tag warn\">no longer in source</span>"
        h.append(_tr([_td(_code(r["base_id"]), "rep" if same_base else ""),
                      _td(_code(r["table_id"]), "rep" if same_table else ""),
                      _td(esc(r["table_name"]), "rep" if same_table else ""),
                      _td(_code(r["field_id"])), _td(esc(r["field_name"])), _td(typ),
                      _td(esc(settings_summary(r)))]))
        prev_base, prev_table = r["base_id"], r["table_id"]
    h.append("</table>")

    # 3b. Tables and views
    with db() as conn2:
        tinfo, vmap = load_tables_and_views(conn2)
    fnames = {r["field_id"]: r["field_name"] for r in frows}
    h.append("<h3>3b. Tables and views</h3><p class=\"meta\">Table descriptions, primary fields and every view the "
             "schema reported. A view's visible fields are Airtable field IDs; use section 3 to look up their names. "
             "Filters, sorts, grouping and form layouts are not part of the schema response and are not captured.</p>"
             "<table>")
    h.append(_th(["Base", "Table", "Description", "Primary field", "View", "Type", "Visible fields"]))
    seen_t = set()
    for tid in [t for t in dict.fromkeys(r["table_id"] for r in frows)]:
        trow = next(r for r in frows if r["table_id"] == tid)
        info = tinfo.get(tid) or {}
        prim = info.get("primary_field_id")
        prim_txt = esc(fnames.get(prim, prim)) if prim else "Not recorded"
        vs = [v for v in vmap.get(tid, []) if not v["stale"]]
        if not vs:
            h.append(_tr([_td(_code(trow["base_id"])), _td(esc(trow["table_name"])),
                          _td(esc(info.get("table_description") or "")), _td(prim_txt),
                          _td("No views recorded"), _td(""), _td("")]))
            continue
        for k, v in enumerate(vs):
            ids = view_field_ids(v)
            vis = "Not reported" if ids is None else ("%d: %s" % (len(ids), ", ".join(esc(fnames.get(i, i)) for i in ids)) if ids else "None")
            h.append(_tr([_td(_code(trow["base_id"]), "rep" if k else ""), _td(esc(trow["table_name"]), "rep" if k else ""),
                          _td(esc(info.get("table_description") or ""), "rep" if k else ""),
                          _td(prim_txt, "rep" if k else ""),
                          _td(esc(v["view_name"]) + " " + _code(v["view_id"])), _td(esc(v["view_type"])), _td(vis)]))

    # 4. Load order
    # 3c. Attachments (sizes and types seen while profiling)
    with db() as conn3:
        astats = load_attachment_stats(conn3)
    h.append("<h3>3c. Attachments: how many, how large, what types</h3><p class=\"meta\">Counted from the attachment "
             "details Airtable returns with each record while profiling (step 5). No files are downloaded and no "
             "filenames or links are stored. 1 MB = 1,048,576 bytes. Run profiling to fill this in.</p>")
    if not astats:
        h.append("<p class=\"meta\">No attachment statistics yet (profiling has not found attachments, or has not run).</p>")
    else:
        tot, by_type, by_table = summarize_attachment_stats(astats)
        h.append("<p><strong>%s files, %s</strong> in total; largest single file %s%s.</p>" % (
            "{:,}".format(tot["files"]), human_bytes(tot["bytes"]), human_bytes(tot["max_bytes"]),
            "; %d with no size reported" % tot["unknown_size"] if tot["unknown_size"] else ""))
        lab = ["under 1 MB", "1-10 MB", "10-100 MB", "100 MB-1 GB", "1 GB or more"]
        h.append("<table>" + _th(["Size band", "Files"]) + "".join(
            _tr([_td(l), _td("{:,}".format(n))]) for l, n in zip(lab, tot["buckets"])) + "</table>")
        h.append("<h4>By file type</h4><table>" + _th(["Extension", "Files", "Total size", "Largest"]) + "".join(
            _tr([_td(esc(k)), _td("{:,}".format(v["files"])), _td(human_bytes(v["bytes"])), _td(human_bytes(v["max_bytes"]))])
            for k, v in sorted(by_type.items(), key=lambda kv: -kv[1]["bytes"])) + "</table>")
        h.append("<h4>By table</h4><table>" + _th(["Base", "Table", "Files", "Total size", "Largest"]) + "".join(
            _tr([_td(_code(k[0])), _td(esc(k[2])), _td("{:,}".format(v["files"])), _td(human_bytes(v["bytes"])),
                 _td(human_bytes(v["max_bytes"]))]) for k, v in sorted(by_table.items(), key=lambda kv: (kv[0][0], kv[0][2].lower()))) + "</table>")

    h.append("<h2>4. Load order</h2><div class=\"box\"><p><strong>How to read this.</strong> Load tables in "
             "ascending <em>Load #</em>. A table's parents always have a lower number. Tables with the same "
             "<em>Depth</em> do not depend on each other. Tables flagged <em>In a cycle</em> depend on each other: "
             "load their records first, then set their link fields in a second pass. Tables flagged <em>Self-link</em> "
             "link to themselves: set those links after the table's records exist.</p>"
             "<p><strong>Link handling: %s.</strong> %s</p></div><table>" % (esc(mode_name), esc(mode_desc)))
    h.append(_th(["Load #", "Base", "Table ID", "Table", "Depth", "Parent tables (must load first)", "Flags"]))
    for m in g["matrix"]:
        flags = [f for f, on in (("In a cycle", m["in_cycle"]), ("Self-link", m["table_id"] in self_tables)) if on]
        parents = "<br>".join("%s (%s)" % (esc(tname.get(p, p)), _code(p)) for p in m["parent_table_ids"]) or "none"
        h.append(_tr([_td(str(m["load_order_sequence"]), "n"), _td(_code(m["base_id"])), _td(_code(m["table_id"])),
                      _td("<strong>%s</strong>" % esc(m["table_name"])), _td(str(m["dependency_depth"]), "n"),
                      _td(parents), _td(esc(", ".join(flags)))]))
    h.append("</table>")
    for c in g["cycles"]:
        h.append("<div class=\"box err\"><strong>Cycle:</strong> %s<ul>%s</ul>Load these tables' records first, "
                 "then set the links in a second pass.</div>"
                 % (esc(" ↔ ".join(c["tables"])),
                    "".join("<li>%s links to %s via %s</li>" % (esc(e["child"]), esc(e["parent"]), esc(", ".join(e["fields"])))
                            for e in c["edges"])))
    if g["inverse_pairs"]:
        h.append("<h3>Two-way link pairs</h3><ul>%s</ul>" % "".join(
            "<li>%s.%s ⇄ %s.%s: %s</li>" % (
                esc(p["table_a"]), esc(p["field_a"]), esc(p["table_b"]), esc(p["field_b"]),
                esc("%s is the child of %s (single-record link)" % (p["child_table"], p["parent_table"])
                    if p["child_table"] else "direction unknown (many-to-many, or the hint was not reported): the two tables load together as a cycle"))
            for p in g["inverse_pairs"]))
        if mode == "literal":
            h.append("<p class=\"meta\">In Literal mode each pair counts as a two-table cycle. Regenerate with "
                     "\"Resolve two-way link pairs\" to order them using the single-link hint.</p>")
    if g["unresolved_links"]:
        h.append("<div class=\"box warn\"><strong>Unresolved links:</strong> these fields link to tables that are not "
                 "in the database (ingest the other base, then recompute).<ul>%s</ul></div>" % "".join(
                     "<li>%s.%s &rarr; %s</li>" % (esc(u["table_name"]), esc(u["field_name"]), _code(u["linked_table_id"]))
                     for u in g["unresolved_links"]))

    # 5. Link fields
    h.append("<h2>5. Link fields and second-pass loading</h2><p class=\"meta\">Every link field and how it is "
             "treated. Highlighted rows must be set in a second pass, after the records of both tables exist.</p><table>")
    h.append(_th(["Table", "Field ID", "Field", "Links to", "Treatment", "Second pass?", "What to do"]))
    for l in g["link_fields"]:
        h.append(_tr([_td(esc(l["table_name"])), _td(_code(l["field_id"])), _td(esc(l["field_name"])),
                      _td("%s (%s)" % (esc(l["linked_table_name"]), _code(l["linked_table_id"]))),
                      _td(esc(l["treatment"])), _td("<strong>Yes</strong>" if l["second_pass"] else "No"),
                      _td(esc(l["explanation"]))], "second" if l["second_pass"] else ""))
    if not g["link_fields"]:
        h.append(_tr(["<td colspan=\"7\">This schema has no link fields.</td>"]))
    h.append("</table>")

    # 6. Graph
    h.append("<h2>6. Dependency graph</h2><p class=\"meta\">Parents are on the left and children on the right; arrows "
             "point from parent to child. Numbers are the load order. A red outline means the table is in a cycle; "
             "↻ marks a self-link. The same information is in the table in section 4.</p>")
    h.append("<div class=\"graph\">%s</div>" % svg_graph(g["matrix"], g["edges"], self_tables))

    # 7. Method
    h.append("<h2>7. How the order is derived, and how to verify it</h2><h3>Method</h3><ol>"
             "<li>Each link field in the captured schema says which table it points to. A table that links to "
             "another table is the child; the linked table is its parent. Self-links are ignored.</li>"
             "<li>Tables that depend on each other in a loop are found with Tarjan's strongly-connected-components "
             "algorithm and treated as a single step.</li>"
             "<li>Steps are ordered parent-first (a topological order). Ties are broken by table name, then table "
             "ID, so the result is always the same for the same schema. Depth is the longest chain of parents above "
             "a table.</li>"
             "<li>Nothing here is estimated or generated by a language model, and no Airtable record data is read "
             "to produce this report.</li></ol><h3>Suggested checks for auditors</h3><ol>"
             "<li>Compare the capture date in section 2 with the date the Airtable base was last changed. If the "
             "base changed after that date, re-ingest and regenerate.</li>"
             "<li>Spot-check table and field counts against the Airtable base.</li>"
             "<li>Pick a few tables in section 4 and confirm in Airtable that every table they link to has a lower "
             "Load #, except those flagged as cycles or self-links.</li>"
             "<li>Regenerate the report from the same database: the load order, flags and tables must be identical "
             "(only the generated time changes).</li></ol><h3>Review sign-off</h3>"
             "<table class=\"sign\"><tr><th>Prepared by</th><th>Date</th><th>Reviewed by</th><th>Date</th>"
             "<th>Notes</th></tr><tr><td></td><td></td><td></td><td></td><td></td></tr></table>")
    h.append("</main></body></html>")
    return "".join(h)


def to_mhtml(html_text, title):
    """Wrap one HTML document as an MHTML (MIME multipart/related) archive."""
    raw = html_text.encode("utf-8")
    body = quopri.encodestring(raw).replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    boundary = "----=_MigrationReport_" + hashlib.sha256(raw).hexdigest()[:24]
    head = ("From: <Saved by Airtable Migration Control Plane>\r\nSubject: %s\r\nDate: %s\r\nMIME-Version: 1.0\r\n"
            "Content-Type: multipart/related; type=\"text/html\"; boundary=\"%s\"\r\n\r\n"
            "--%s\r\nContent-Type: text/html; charset=\"utf-8\"\r\nContent-Transfer-Encoding: quoted-printable\r\n"
            "Content-Location: file:///load-order-report.html\r\n\r\n"
            % (title, formatdate(usegmt=True), boundary, boundary))
    return head.encode("ascii") + body + ("\r\n--%s--\r\n" % boundary).encode("ascii")


def _report_text(q):
    mode = (q.get("mode") or ["literal"])[0]
    return scrub(build_report(mode))


def _stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")


def api_report_html(q):
    headers = {}
    text = _report_text(q)
    if (q.get("download") or [""])[0]:
        headers["Content-Disposition"] = 'attachment; filename="load-order-report_%s.html"' % _stamp()
    return (text.encode("utf-8"), "text/html; charset=utf-8", headers)


def api_report_mhtml(q):
    text = _report_text(q)
    return (to_mhtml(text, "Airtable Load Order Report"), "application/x-mimearchive",
            {"Content-Disposition": 'attachment; filename="load-order-report_%s.mhtml"' % _stamp()})


# --------------------------------------------------------------------------
# CSV exports (metadata only): load order and profile, for checking against an answer key
# --------------------------------------------------------------------------
def _csv_bytes(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def api_export_load_order(q):
    mode = (q.get("mode") or ["literal"])[0]
    base = (q.get("base_id") or [""])[0]
    with db() as conn:
        g = compute_graph(conn, mode)
    names = {m["table_id"]: m["table_name"] for m in g["matrix"]}
    rows, counter = [], {}
    for m in g["matrix"]:
        if base and m["base_id"] != base:
            continue
        counter[m["base_id"]] = counter.get(m["base_id"], 0) + 1
        rows.append([m["base_id"], m["table_name"], m["table_id"], counter[m["base_id"]], m["dependency_depth"],
                     "; ".join(sorted(names.get(p, p) for p in m["parent_table_ids"])),
                     "yes" if m["in_cycle"] else "no"])
    body = _csv_bytes(["base_id", "table", "table_id", "load_no", "depth", "parent_tables", "in_cycle"], rows)
    return (body, "text/csv; charset=utf-8",
            {"Content-Disposition": 'attachment; filename="load-order_%s_%s.csv"' % (mode, _stamp())})


def api_export_profile(q):
    data = api_profile_results(q)["rows"]
    rows = [[r["base_id"], r["table_name"], r["field_name"], r["data_type"], r["row_count"], r["null_count"],
             "" if r["cardinality"] is None else r["cardinality"], "yes" if r["cardinality_capped"] else "no"]
            for r in data if not r["stale"]]
    body = _csv_bytes(["base_id", "table", "field", "field_type", "row_count", "null_count", "cardinality",
                       "cardinality_capped"], rows)
    def part(key, plural):   # the ID the user chose, or "Bases" / "Tables" for all
        v = (q.get(key) or [""])[0]
        return v if ID_SAFE_RE.match(v) else plural
    name = "%s_%s_Profile_%s.csv" % (part("base_id", "Bases"), part("table_id", "Tables"), _stamp())
    return (body, "text/csv; charset=utf-8", {"Content-Disposition": 'attachment; filename="%s"' % name})


ATT_SUMMARY_SQL = ("SELECT s.base_id, s.table_id, COALESCE(t.table_name, s.table_id) AS table_name, s.field_id, "
                   "COALESCE(f.field_name, s.field_id) AS field_name, s.file_extension, s.mime_type, s.file_count, "
                   "s.total_bytes, s.min_bytes, s.max_bytes, s.unknown_size_count, s.size_lt_1mb, s.size_1_10mb, "
                   "s.size_10_100mb, s.size_100mb_1gb, s.size_ge_1gb FROM airtable_attachment_stats s "
                   "LEFT JOIN airtable_tables t ON t.table_id = s.table_id "
                   "LEFT JOIN airtable_fields f ON f.field_id = s.field_id WHERE 1=1")


def load_attachment_stats(conn, base="", table=""):
    sql, args = ATT_SUMMARY_SQL, []
    if base:
        sql += " AND s.base_id=?"
        args.append(base)
    if table:
        sql += " AND s.table_id=?"
        args.append(table)
    sql += (" ORDER BY s.base_id, lower(COALESCE(t.table_name, s.table_id)), s.table_id, "
            "lower(COALESCE(f.field_name, s.field_id)), s.field_id, s.total_bytes DESC, s.file_extension")
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def summarize_attachment_stats(rows):
    def blank():
        return {"files": 0, "bytes": 0, "max_bytes": 0, "unknown_size": 0, "buckets": [0] * 5}
    tot, by_type, by_table = blank(), {}, {}
    keys = ("size_lt_1mb", "size_1_10mb", "size_10_100mb", "size_100mb_1gb", "size_ge_1gb")
    for r in rows:
        for agg in (tot, by_type.setdefault(r["file_extension"], blank()),
                    by_table.setdefault((r["base_id"], r["table_id"], r["table_name"]), blank())):
            agg["files"] += r["file_count"]
            agg["bytes"] += r["total_bytes"]
            agg["max_bytes"] = max(agg["max_bytes"], r["max_bytes"] or 0)
            agg["unknown_size"] += r["unknown_size_count"]
            for i, k in enumerate(keys):
                agg["buckets"][i] += r[k]
    return tot, by_type, by_table


def api_attachment_stats(q):
    """Counts, bytes and file types of the attachments seen while profiling. No filenames, no links."""
    base = (q.get("base_id") or [""])[0]
    table = (q.get("table_id") or [""])[0]
    with db() as conn:
        rows = load_attachment_stats(conn, base, table)
    tot, by_type, by_table = summarize_attachment_stats(rows)
    return {"total": tot, "rows": rows,
            "by_type": [dict(extension=k, **v) for k, v in sorted(by_type.items(), key=lambda kv: -kv[1]["bytes"])],
            "by_table": [dict(base_id=k[0], table_id=k[1], table_name=k[2], **v)
                         for k, v in sorted(by_table.items(), key=lambda kv: (kv[0][0], kv[0][2].lower()))]}


def api_export_attachment_stats(q):
    base = (q.get("base_id") or [""])[0]
    table = (q.get("table_id") or [""])[0]
    with db() as conn:
        rows = load_attachment_stats(conn, base, table)
    cols = ["base_id", "table_id", "table_name", "field_id", "field_name", "file_extension", "mime_type", "file_count",
            "total_bytes", "min_bytes", "max_bytes", "unknown_size_count", "size_lt_1mb", "size_1_10mb",
            "size_10_100mb", "size_100mb_1gb", "size_ge_1gb"]
    body = _csv_bytes(cols, [["" if r[c] is None else r[c] for c in cols] for r in rows])
    return (body, "text/csv; charset=utf-8",
            {"Content-Disposition": 'attachment; filename="attachment_stats_%s.csv"' % _stamp()})


def api_export_views(q):
    base = (q.get("base_id") or [""])[0]
    with db() as conn:
        tinfo, vmap = load_tables_and_views(conn)
        trows = conn.execute("SELECT table_id, base_id, table_name FROM airtable_tables" +
                             (" WHERE base_id=?" if base else "") + " ORDER BY base_id, lower(table_name), table_id",
                             (base,) if base else ()).fetchall()
    out = []
    for t in trows:
        for v in vmap.get(t["table_id"], []):
            if v["stale"]:
                continue
            out.append([t["base_id"], t["table_id"], t["table_name"], v["view_id"], v["view_name"], v["view_type"],
                        v["view_position"], v["visible_field_ids"] or ""])
    body = _csv_bytes(["base_id", "table_id", "table_name", "view_id", "view_name", "view_type", "view_position",
                       "visible_field_ids"], out)
    return (body, "text/csv; charset=utf-8", {"Content-Disposition": 'attachment; filename="views_%s.csv"' % _stamp()})


def api_export_tables(q):
    base = (q.get("base_id") or [""])[0]
    with db() as conn:
        trows = conn.execute("SELECT * FROM airtable_tables" + (" WHERE base_id=?" if base else "") +
                             " ORDER BY base_id, lower(table_name), table_id", (base,) if base else ()).fetchall()
    body = _csv_bytes(["base_id", "table_id", "table_name", "table_description", "primary_field_id"],
                      [[t["base_id"], t["table_id"], t["table_name"], t["table_description"] or "",
                        t["primary_field_id"] or ""] for t in trows])
    return (body, "text/csv; charset=utf-8", {"Content-Disposition": 'attachment; filename="tables_%s.csv"' % _stamp()})


FIELD_CSV_COLUMNS = ["base_id", "table_id", "table_name", "field_id", "field_name", "data_type", "linked_table_id",
                     "inverse_link_field_id", "prefers_single_record_link"] + OPTION_COLUMNS


def api_export_field_options(q):
    base = (q.get("base_id") or [""])[0]
    sql = ("SELECT f.*, %s AS stale FROM airtable_fields f" % STALE_SQL) + (" WHERE f.base_id=?" if base else "")
    sql += " ORDER BY f.base_id, lower(f.table_name), f.table_id, lower(f.field_name), f.field_id"
    with db() as conn:
        rows = [r for r in conn.execute(sql, (base,) if base else ()) if not r["stale"]]
    body = _csv_bytes(FIELD_CSV_COLUMNS, [[r[c] for c in FIELD_CSV_COLUMNS] for r in rows])
    return (body, "text/csv; charset=utf-8",
            {"Content-Disposition": 'attachment; filename="field-options_%s.csv"' % _stamp()})


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/scripts.js": ("scripts.js", "application/javascript; charset=utf-8")}

GET_ROUTES = {"/api/status": api_status, "/api/db/counts": api_db_counts, "/api/schema": api_schema_view,
              "/api/depgraph": api_depgraph_view, "/api/profile/status": api_profile_status,
              "/api/profile/results": api_profile_results, "/api/extract/files": api_extract_files, "/api/calllog": api_calllog, "/api/runbook": api_runbook,
              "/api/report.html": api_report_html, "/api/report.mhtml": api_report_mhtml,
              "/api/export/load_order.csv": api_export_load_order, "/api/export/profile.csv": api_export_profile,
              "/api/export/field_options.csv": api_export_field_options,
              "/api/export/attachment_stats.csv": api_export_attachment_stats, "/api/attachments/stats": api_attachment_stats,
              "/api/export/views.csv": api_export_views, "/api/export/tables.csv": api_export_tables}
POST_ROUTES = {"/api/db/connect": api_db_connect, "/api/db/create": api_db_create,
               "/api/schema/ingest": api_schema_ingest, "/api/depgraph/recompute": api_depgraph_recompute,
               "/api/extract/delete": api_extract_delete, "/api/profile/start": api_profile_start, "/api/profile/cancel": api_profile_cancel}


class Handler(BaseHTTPRequestHandler):
    server_version = "MigrationControl/1.0"

    def log_message(self, fmt, *args):  # keep the console quiet; never print headers
        pass

    def _send(self, status, payload, ctype="application/json; charset=utf-8", headers=None):
        data = payload if isinstance(payload, bytes) else scrub(json.dumps(payload)).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
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
            result = fn(arg)
            if isinstance(result, tuple):  # (bytes, content type, extra headers): the report downloads
                self._send(200, result[0], result[1], result[2])
            else:
                self._send(200, result)
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
