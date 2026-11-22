#!/usr/bin/env python3
"""Bulk creation of Workfront Core custom fields (PARAM) from an uploaded CSV, with a crosswalk and a reset. Standard library only.

The page (/corefields) uploads a CSV with these columns, spelled exactly: field_id, name, label, dataType, displayType, description, isRequired. Everything here is plain
functions over a list of row dicts, so it is tested without a server:

    parse_upload(bytes)  -> (rows, problems)      read and validate; any problem refuses the whole file, with a row number for each
    scan(...)            GET /param/search per row  fills parameterId and lastUpdateDate of a field that already exists and unchecks it
    create(...)          POST /param per checked row, top to bottom, STOPPING at the first error; one crosswalk row per success
    reset(...)           DELETE /param/{id} per row this tool created, STOPPING at the first error; the crosswalk row goes after each success

Rules this module keeps (CLAUDE.md): no credential is stored, logged or reported (workfronthttp.Http cleans every text); only field metadata moves, never a record value;
the order is the order of the file (deterministic); a person decides, the tool never creates or deletes on its own. Reset deletes ONLY a parameter this tool created and
recorded in the crosswalk: a field that was already in Workfront when the scan found it is never deleted here.

Routes (Core, relative to base_url + api_path from config.json): POST /param, GET /param/search, GET /param/{id}, DELETE /param/{id}. The reply of a create is read for
data.ID and data.lastUpdateDate. None of this has been run against a real Workfront; the owner's examples were partly garbled, so the paths follow coretransport.py.
"""
import base64, binascii, csv, io, re, time
from datetime import datetime, timezone

import fieldnames as fn
import loadconfig as lc
import loadrun as lr
from workfronthttp import HttpError

HEADER = ["field_id", "name", "label", "dataType", "displayType", "description", "isRequired"]
DATA_TYPES = ("CURC", "DATE", "NMBR", "TEXT")          # case-sensitive, the owner's list
DISPLAY_TYPES = ("CHCK", "TEXT")
ENVIRONMENTS = lc.ENVIRONMENTS
RESET_PHRASE = "DELETE FIELDS IN PRODUCTION"            # typed to reset in production (CREATE IN PRODUCTION, from loadrun, is typed to create there)
MAX_ROWS = 5000
MAX_CELL = 4000
FIELD_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
TRUE_WORDS, FALSE_WORDS = ("true", "yes", "1"), ("false", "no", "0", "")
# Characters Workfront does not accept in a name (the documented list the field name validator already uses), plus line breaks and other control characters.
RESTRICTED = frozenset(fn.AUTO_DELETE_CHARS) | frozenset(c for c in fn.WS_CHARS)


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def template_csv():
    """The header only, ready to fill in (the owner supplies their own template; this one matches the validator)."""
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\r\n").writerow(HEADER)
    return buf.getvalue()


# ------------------------------------------------------------------ reading and validating the upload
def restricted_in(name):
    return "".join(sorted({c for c in name if c in RESTRICTED or ord(c) < 32}))


def parse_bool(text):
    t = str(text or "").strip().lower()
    if t in TRUE_WORDS:
        return True
    if t in FALSE_WORDS:
        return False
    return None


def parse_upload(b64_or_bytes):
    """(rows, problems). `rows` are dicts with the seven columns (isRequired a bool) plus the grid state; `problems` is a list of {"row", "column", "message"} (row 0 = the
    file itself). Any problem means the file is refused, so a half-valid list is never in the grid. Never repeats a cell longer than 60 characters."""
    if isinstance(b64_or_bytes, str):
        try:
            raw = base64.b64decode(b64_or_bytes, validate=True)
        except (binascii.Error, ValueError):
            return [], [{"row": 0, "column": "", "message": "The upload was not a readable file."}]
    else:
        raw = b64_or_bytes or b""
    if not raw.strip():
        return [], [{"row": 0, "column": "", "message": "The file is empty."}]
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp1252")                  # Excel's "CSV" on Windows
        except UnicodeDecodeError:
            return [], [{"row": 0, "column": "", "message": "The file is not text (save it as CSV from Excel and try again)."}]
    reader = csv.reader(io.StringIO(text))
    try:
        head = next(reader)
    except (StopIteration, csv.Error):
        return [], [{"row": 0, "column": "", "message": "The file has no header row."}]
    head = [h.strip() for h in head]
    missing = [h for h in HEADER if h not in head]
    extra = [h for h in head if h not in HEADER]
    if missing or extra or len(set(head)) != len(head):
        msg = []
        if missing:
            msg.append("missing column(s): " + ", ".join(missing))
        if extra:
            msg.append("unknown column(s): " + ", ".join(h[:40] for h in extra))
        if len(set(head)) != len(head):
            msg.append("a column appears twice")
        return [], [{"row": 0, "column": "", "message": "The header must be exactly: %s (spelled and cased as shown). It has %s." % (", ".join(HEADER), "; ".join(msg))}]
    idx = {h: head.index(h) for h in HEADER}
    cells, problems = [], []
    try:
        for n, rec in enumerate(reader, 2):               # n = the line number in the file's view (header is row 1)
            if not any(c.strip() for c in rec):
                continue
            if len(cells) >= MAX_ROWS:
                return [], [{"row": 0, "column": "", "message": "The file has more than %d rows; split it." % MAX_ROWS}]
            cells.append((n, {h: (rec[idx[h]] if idx[h] < len(rec) else "") for h in HEADER}))
    except csv.Error:
        return [], [{"row": 0, "column": "", "message": "The file is not valid CSV (an unclosed quote?)."}]
    if not cells:
        return [], [{"row": 0, "column": "", "message": "The file has a header but no rows."}]
    seen = {}
    rows = []
    for k, (n, c) in enumerate(cells, 1):
        def bad(col, msg):
            problems.append({"row": n, "column": col, "message": msg})
        for col in HEADER:
            if len(c[col]) > MAX_CELL:
                bad(col, "is longer than %d characters." % MAX_CELL)
        fid, name = c["field_id"].strip(), c["name"]
        if not FIELD_ID_RE.match(fid):
            bad("field_id", "must be 1 to 64 letters, digits, dots, hyphens or underscores (it is the Airtable field ID).")
        if not name.strip():
            bad("name", "is empty.")
        else:
            if name != name.strip() or "  " in name:
                bad("name", "has a leading, trailing or doubled space.")
            chars = restricted_in(name)
            if chars:
                bad("name", "has characters Workfront does not allow in a name: %s" % " ".join(repr(x)[1:-1] if ord(x) < 32 else x for x in chars))
            key = name.strip().casefold()
            if key in seen:
                bad("name", "is a duplicate of row %d (names must be unique in the file, ignoring letter case)." % seen[key])
            else:
                seen[key] = n
        if c["dataType"] not in DATA_TYPES:
            bad("dataType", "must be exactly one of %s (capitals)." % ", ".join(DATA_TYPES))
        if c["displayType"] not in DISPLAY_TYPES:
            bad("displayType", "must be exactly one of %s (capitals)." % ", ".join(DISPLAY_TYPES))
        req = parse_bool(c["isRequired"])
        if req is None:
            bad("isRequired", "must be true or false (or blank for false).")
        rows.append({"n": k, "file_row": n, "field_id": fid, "name": name, "label": c["label"], "dataType": c["dataType"], "displayType": c["displayType"], "description": c["description"],
                     "isRequired": bool(req), "checked": True, "parameterId": "", "lastUpdateDate": "", "status": "", "message": ""})
    problems.sort(key=lambda p: (p["row"], HEADER.index(p["column"]) if p["column"] in HEADER else 0))
    return ([], problems) if problems else (rows, [])


# ------------------------------------------------------------------ the crosswalk (database table, or memory until migration 026 is applied)
TABLE = "core_field_crosswalk"
COLUMNS = ("environment", "airtable_field_id", "workfront_name", "workfront_label", "workfront_data_type", "workfront_display_type", "workfront_description",
           "workfront_is_required", "parameter_id", "last_update_date")


def table_exists(conn):
    return conn is not None and conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone() is not None


class Crosswalk(object):
    """One row per Core field this tool created. In the database when migration 026 is applied, otherwise in the `mem` dict the caller keeps (lost when the app stops)."""
    def __init__(self, conn, mem):
        self.conn, self.mem = conn, mem
        self.persistent = table_exists(conn)

    def entries(self, env):
        if self.persistent:
            return [dict(r) for r in self.conn.execute("SELECT * FROM %s WHERE environment=? ORDER BY crosswalk_id" % TABLE, (env,))]
        return [dict(e) for e in self.mem.get(env, [])]

    def find(self, env, field_id, name):
        for e in self.entries(env):
            if e["airtable_field_id"] == field_id and e["workfront_name"] == name:
                return e
        return None

    def by_parameter(self, env, pid):
        return next((e for e in self.entries(env) if e["parameter_id"] == pid), None)

    def add(self, env, row, parameter_id, last_update):
        vals = (env, row["field_id"], row["name"], row["label"], row["dataType"], row["displayType"], row["description"], 1 if row["isRequired"] else 0, parameter_id, last_update or "")
        if self.persistent:
            self.conn.execute("INSERT INTO %s(%s) VALUES (%s)" % (TABLE, ", ".join(COLUMNS), ", ".join("?" * len(COLUMNS))), vals)
            self.conn.commit()
        else:
            self.mem.setdefault(env, []).append(dict(zip(COLUMNS, vals)))

    def remove(self, env, parameter_id):
        if self.persistent:
            self.conn.execute("DELETE FROM %s WHERE environment=? AND parameter_id=?" % TABLE, (env, parameter_id))
            self.conn.commit()
        else:
            self.mem[env] = [e for e in self.mem.get(env, []) if e["parameter_id"] != parameter_id]


# ------------------------------------------------------------------ the call log (every attempt, in order)
class CallLog(object):
    """Used as workfronthttp.Http.recorder. The caller sets `context` (operation, row number, query, body) before each call; every attempt, retries included, is one entry."""
    def __init__(self):
        self.entries, self.context = [], {}

    def __call__(self, e):
        c = self.context
        self.entries.append(dict(e, number=len(self.entries) + 1, timestamp=utc_now(), operation=c.get("operation", ""), row=c.get("row"), field_id=c.get("field_id", ""), name=c.get("name", ""),
                                 query=c.get("query") or {}, body=c.get("body")))

    def set(self, **k):
        self.context = k


class Stop(Exception):
    """The person pressed Cancel: the run ends after the call in progress."""


def _rows_of(reply):
    d = reply.get("data") if isinstance(reply, dict) else None
    return [x for x in (d if isinstance(d, list) else ([d] if isinstance(d, dict) else [])) if isinstance(x, dict)]


def _check(should_stop):
    if should_stop():
        raise Stop()


# ------------------------------------------------------------------ the three operations. Each returns a summary dict and never raises for a Workfront refusal.
def scan(rows, http, log, should_stop=lambda: False, progress=lambda p: None):
    """Ask Workfront, one GET per row, whether a field with the row's name already exists (names compare ignoring letter case, as Workfront treats them as one name).
    A found field fills parameterId and lastUpdateDate, unchecks the row and marks it 'exists'. A missing one clears them and is checked again. Stops at the first error: the
    scan is then NOT complete, and creating stays locked."""
    found, error, done = 0, None, 0
    try:
        for r in rows:
            _check(should_stop)
            progress({"phase": "scan", "done": done, "total": len(rows), "item": r["name"]})
            log.set(operation="scan", row=r["n"], field_id=r["field_id"], name=r["name"], query={"name": r["name"], "name_Mod": "eq", "fields": "ID,name,lastUpdateDate"})
            try:
                reply = http.request("GET", "/param/search", None, {"name": r["name"], "name_Mod": "eq", "fields": "ID,name,lastUpdateDate"})
            except HttpError as e:
                r["status"], r["message"] = "scan failed", str(e)
                error = {"row": r["n"], "name": r["name"], "message": str(e), "status": e.status}
                break
            hit = sorted((x for x in _rows_of(reply) if str(x.get("name") or "").casefold() == r["name"].casefold() and x.get("ID")), key=lambda x: str(x["ID"]))
            if hit:
                r["parameterId"], r["lastUpdateDate"] = str(hit[0]["ID"]), str(hit[0].get("lastUpdateDate") or "")
                r["status"], r["message"], r["checked"] = "exists", ("Found in Workfront as %r." % hit[0]["name"]) if hit[0]["name"] != r["name"] else "", False
                found += 1
            else:
                if r["status"] == "exists":                   # it was found before and is gone now: offer it for creation again
                    r["checked"] = True
                r["parameterId"], r["lastUpdateDate"], r["status"], r["message"] = "", "", "", ""
            done += 1
    except Stop:
        return {"operation": "scan", "complete": False, "cancelled": True, "found": found, "scanned": done, "error": None}
    return {"operation": "scan", "complete": error is None, "cancelled": False, "found": found, "scanned": done, "error": error}


def request_body(r):
    """The JSON of one create: label and description are left out when blank (Workfront keeps its own default), isRequired is always sent."""
    body = {"name": r["name"]}
    if r["label"].strip():
        body["label"] = r["label"]
    body["dataType"], body["displayType"] = r["dataType"], r["displayType"]
    if r["description"].strip():
        body["description"] = r["description"]
    body["isRequired"] = bool(r["isRequired"])
    return body


def create(rows, env, http, crosswalk, log, should_stop=lambda: False, progress=lambda p: None):
    """POST /param for each checked row that has no parameterId, from the top down, STOPPING at the first error (the failing row keeps its message; later rows are untouched).
    After each success the crosswalk gets its row and the grid row gets parameterId, lastUpdateDate, status 'created' and is unchecked."""
    todo = [r for r in rows if r["checked"] and not r["parameterId"]]
    created, error, cancelled = 0, None, False
    try:
        for r in todo:
            _check(should_stop)
            progress({"phase": "create", "done": created, "total": len(todo), "item": r["name"]})
            body = request_body(r)
            log.set(operation="create", row=r["n"], field_id=r["field_id"], name=r["name"], query={"fields": "ID,lastUpdateDate"}, body=body)
            try:
                reply = http.request("POST", "/param", body, {"fields": "ID,lastUpdateDate"})
            except HttpError as e:
                r["status"], r["message"] = "failed", str(e)
                error = {"row": r["n"], "name": r["name"], "message": str(e), "status": e.status}
                break
            d = (_rows_of(reply) or [{}])[0]
            pid = str(d.get("ID") or d.get("id") or "")
            if not pid:
                r["status"], r["message"] = "failed", "Workfront answered the create but its reply holds no ID, so the field cannot be recorded. Check Workfront for a field named %r." % r["name"]
                error = {"row": r["n"], "name": r["name"], "message": r["message"], "status": None}
                break
            last = str(d.get("lastUpdateDate") or "")
            if not last:                                  # the reply did not carry it: one read of the new parameter, never fatal
                log.set(operation="create", row=r["n"], field_id=r["field_id"], name=r["name"], query={"fields": "ID,lastUpdateDate"})
                try:
                    last = str((_rows_of(http.request("GET", "/param/" + pid, None, {"fields": "ID,lastUpdateDate"})) or [{}])[0].get("lastUpdateDate") or "")
                except HttpError:
                    last = ""
            r["parameterId"], r["lastUpdateDate"], r["status"], r["message"], r["checked"] = pid, last, "created", "", False
            try:
                crosswalk.add(env, r, pid, last)
            except Exception as e:                        # the field exists in Workfront but could not be recorded: stop, and say exactly that
                r["message"] = "Created in Workfront (parameterId %s) but the crosswalk row could not be saved: %s" % (pid, lr.scrub(e))
                error = {"row": r["n"], "name": r["name"], "message": r["message"], "status": None}
                created += 1
                break
            created += 1
    except Stop:
        cancelled = True
    return {"operation": "create", "created": created, "planned": len(todo), "error": error, "cancelled": cancelled}


def reset(rows, env, http, crosswalk, log, should_stop=lambda: False, progress=lambda p: None):
    """DELETE /param/{id} for each row the crosswalk says this tool created in `env`, from the top down, STOPPING at the first error. After each success the crosswalk row is removed
    and the grid row is cleared and checked again. A row with no crosswalk row (it never existed, or it was already in Workfront) is skipped and counted, never deleted."""
    todo, skipped = [], 0
    for r in rows:
        e = crosswalk.find(env, r["field_id"], r["name"])
        if e:
            todo.append((r, e))
        else:
            skipped += 1
    deleted, error, cancelled = 0, None, False
    try:
        for r, e in todo:
            _check(should_stop)
            progress({"phase": "reset", "done": deleted, "total": len(todo), "item": r["name"]})
            log.set(operation="reset", row=r["n"], field_id=r["field_id"], name=r["name"])
            try:
                http.request("DELETE", "/param/" + e["parameter_id"])
            except HttpError as x:
                r["status"], r["message"] = "delete failed", str(x)
                error = {"row": r["n"], "name": r["name"], "message": str(x), "status": x.status}
                break
            crosswalk.remove(env, e["parameter_id"])
            r["parameterId"], r["lastUpdateDate"], r["status"], r["message"], r["checked"] = "", "", "deleted", "", True
            deleted += 1
    except Stop:
        cancelled = True
    return {"operation": "reset", "deleted": deleted, "planned": len(todo), "skipped_not_created_here": skipped, "error": error, "cancelled": cancelled}


# ------------------------------------------------------------------ the crosswalk CSV
CSV_HEADER = ["baseId", "baseName", "tableId", "tableName", "fieldId", "fieldName", "airtable_dataType", "environment", "workfront_name", "workfront_label", "workfront_dataType",
              "workfront_displayType", "workfront_description", "workfront_isRequired", "parameterId", "lastUpdateDate", "status"]


def airtable_info(conn, field_ids, base_names=None):
    """{field_id: (baseId, baseName, tableId, tableName, fieldName, dataType)} from the Stage 1 schema. A field ID the schema does not hold is simply absent (blank cells)."""
    out = {}
    ids = sorted(set(field_ids))
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for r in conn.execute("SELECT base_id, table_id, table_name, field_id, field_name, data_type FROM airtable_fields WHERE field_id IN (%s)" % ",".join("?" * len(part)), part):
            out[r["field_id"]] = (r["base_id"], (base_names or {}).get(r["base_id"], ""), r["table_id"], r["table_name"], r["field_name"], r["data_type"])
    return out


def crosswalk_csv(rows, env, entries, info, redact=lambda t: t):
    """The grid rows in file order, then any crosswalk row of this environment that is not in the grid. Bytes, UTF-8, CRLF."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_HEADER)
    blank = ("", "", "", "", "", "")

    def line(fid, name, label, dt, disp, desc, req, pid, last, status):
        a = info.get(fid, blank)
        w.writerow([redact(str(x)) for x in (a[0], a[1], a[2], a[3], fid, a[4], a[5], env, name, label, dt, disp, desc, "true" if req else "false", pid, last, status)])
    listed = set()
    for r in rows:
        listed.add((r["field_id"], r["name"]))
        line(r["field_id"], r["name"], r["label"], r["dataType"], r["displayType"], r["description"], r["isRequired"], r["parameterId"], r["lastUpdateDate"], r["status"] or "not created")
    for e in entries:
        if (e["airtable_field_id"], e["workfront_name"]) not in listed:
            line(e["airtable_field_id"], e["workfront_name"], e["workfront_label"] or "", e["workfront_data_type"], e["workfront_display_type"], e["workfront_description"] or "",
                 bool(e["workfront_is_required"]), e["parameter_id"], e["last_update_date"] or "", "in crosswalk, not in this list")
    return buf.getvalue().encode("utf-8")
