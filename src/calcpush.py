#!/usr/bin/env python3
"""The push of the calculated fields feature (Stage D): create the calculated fields in Workfront Core, put them on one custom form, then write their expressions. Standard library
only. It works from the APPROVED preflight snapshot saved by calcpreflight.py (it never translates again) and sends calls only through a workfronthttp.Http given to it.

    push(conn, run_id, http, valid_form_ids, should_stop, progress, item_ids=None) -> summary dict
    read_form(http, category_id)                                                    -> {"id", "name", "associations"} (one read-only GET, no write)

Order of work (owner's guidance, 22 November 2026; every route is provisional until tried in a sandbox):
  1. For each translatable formula, in push order: look for a field (PARAM) that already has the target name (GET /param/search). One with the right dataType and displayType is USED
     as it is, so a shared field, or one made earlier, never fails the run; one with another type fails ONLY that formula, with the reason. Nothing is found: POST /param creates it.
  2. Put the new fields on the form: GET /CTGY/{id} for the existing associations, then ONE PUT /CTGY/{id} with the whole ordered list. Workfront replaces
     a nested collection as a whole, so every existing association is sent back with ALL the settings it was read with (section, shared row, security, configurations); the new fields
     go last, with no section and no shared row (beside a field, or in a section, is a person's decision). The form as first read goes to a `backup` callback before the PUT, and
     the form is read again and compared: an existing association that is missing or changed is reported in `form_notes`. That read also gives the association IDs (CTGYPA).
  3. Only after ALL fields exist and are on the form: PUT /CTGYPA/{id} {"rawCustomExpression": ...} for each, then one GET to read the expressions back.
A formula's progress is saved after each step (calc_push_items), so a retry resumes where it stopped and touches only what is not done; one formula failing does not stop the others.
A refusal of the credentials (401, 403) or an unreachable server stops the whole push at once. Every call, retries included, is logged in calc_push_log: method, path (no query, no
body, no header), when sent and received, HTTP status and a cleaned error. The form must be on the list of valid forms; nothing else is ever read or written. No record value, no token.
"""
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import calcpreflight as cpf
from workfronthttp import HttpError

STEPS = ("none", "created", "attached", "expression_written", "verified")
ID_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")
PARAM_FIELDS = "ID,name,dataType,displayType,lastUpdateDate"
# The properties of an association (a field on a form) that are read and carried forward unchanged. Workfront replaces a nested collection as a whole on update, so every existing
# association must be sent back with ALL of its own settings, or its section, shared row and security would be lost (guidance received 22 November 2026; which of these a given API
# version exposes is for the API Explorer to confirm, and a property the reply does not hold is simply not sent).
ASSOC_KEYS = ("parameterID", "displayOrder", "isRequired", "securityLevel", "viewSecurityLevel", "parameterGroupID", "rowShared", "configurations", "hideFormulaFromDescription")
FORM_FIELDS = "ID,name,categoryParameters:ID," + ",".join("categoryParameters:" + k for k in ASSOC_KEYS)
EXPR_FIELDS = "ID,name,categoryParameters:ID,categoryParameters:parameterID,categoryParameters:rawCustomExpression,categoryParameters:categoryParameterExpression:customExpression"
DEFAULT_SECURITY = "E"          # the securityLevel of a field on a form, as in the owner's example (provisional)


class PushError(Exception):
    def __init__(self, message, status=409):
        Exception.__init__(self, message)
        self.message, self.status = message, status


class Stop(Exception):
    """The person pressed Cancel: the push ends after the call in progress."""


class Fatal(Exception):
    """The credentials were refused or the server cannot be reached: nothing more is tried."""


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _rows_of(reply):
    d = reply.get("data") if isinstance(reply, dict) else None
    return [x for x in (d if isinstance(d, list) else ([d] if isinstance(d, dict) else [])) if isinstance(x, dict)]


def _squash(text):
    return re.sub(r"\s+", "", str(text or ""))


class CallLog(object):
    """Used as workfronthttp.Http.recorder: writes one calc_push_log row per attempt, with the run, the formula and the step set just before the call."""
    def __init__(self, conn, run_id):
        self.conn, self.run_id, self.step, self.item_id = conn, run_id, "", None

    def set(self, step, item_id=None):
        self.step, self.item_id = step, item_id

    def __call__(self, e):
        received = datetime.now(timezone.utc)
        sent = received - timedelta(milliseconds=int(e.get("duration_ms") or 0))
        status = e.get("status")
        self.conn.execute("INSERT INTO calc_push_log (run_id, item_id, step, http_method, endpoint, sent_at, received_at, http_status, success, error_message) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (self.run_id, self.item_id, self.step, e.get("method"), e.get("path"), sent.strftime("%Y-%m-%d %H:%M:%S"), received.strftime("%Y-%m-%d %H:%M:%S"), status,
                           1 if status and 200 <= status < 300 and not e.get("error_text") else 0, e.get("error_text")))
        self.conn.commit()


def read_form(http, category_id, log=None):
    """One read-only GET of a form: its ID, name and current associations. The caller has already checked the ID against the list of valid forms."""
    if not ID_RE.match(category_id or ""):
        raise PushError("The form ID is not a plain ID.", 400)
    if log:
        log.set("read_form")
    data = (_rows_of(http.request("GET", "/CTGY/" + category_id, None, {"fields": FORM_FIELDS})) or [{}])[0]
    cps = [c for c in (data.get("categoryParameters") or []) if isinstance(c, dict)]
    return {"id": str(data.get("ID") or ""), "name": str(data.get("name") or ""), "associations": cps}


class _Pusher(object):
    def __init__(self, conn, run, http, should_stop, progress, backup=None):
        self.conn, self.run, self.http, self.should_stop, self.progress, self.backup = conn, run, http, should_stop, progress, backup
        self.form_notes = []                               # things a person should look at on the form (never a failure of one formula)
        self.log = CallLog(conn, run["run_id"])
        http.recorder = self.log
        self.failed = {}                                  # item_id -> (step, message)
        self.counts = {"created": 0, "reused": 0, "attached": 0, "written": 0, "verified": 0}

    # ---- plumbing
    def call(self, step, item_id, method, path, body=None, query=None):
        if self.should_stop():
            raise Stop()
        self.log.set(step, item_id)
        try:
            return self.http.request(method, path, body, query)
        except HttpError as e:
            if e.status in (None, 401, 403):
                raise Fatal(str(e))
            raise

    def set_step(self, it, step):
        it["step"] = step
        self.conn.execute("UPDATE calc_push_items SET step_done=?, last_error=NULL, updated_at=? WHERE item_id=?", (step, utc_now(), it["item_id"]))
        self.conn.commit()

    def fail(self, it, step, message):
        self.failed[it["item_id"]] = (step, message)
        self.conn.execute("UPDATE calc_push_items SET last_error=?, updated_at=? WHERE item_id=?", (message[:500], utc_now(), it["item_id"]))
        self.conn.commit()

    def place(self, it, pid):
        """The placement of this formula's field on this form (calc_formula_crosswalk): one row per (environment, form, Airtable field)."""
        r, env, cat = self.run, self.run["environment"], self.run["category_id"]
        row = self.conn.execute("SELECT placement_id, parameter_id FROM calc_formula_crosswalk WHERE environment=? AND category_id=? AND airtable_field_id=?", (env, cat, it["field_id"])).fetchone()
        now = utc_now()
        if row is None:
            self.conn.execute("INSERT INTO calc_formula_crosswalk (environment, category_id, parameter_id, airtable_field_id, airtable_table_id, workfront_name, source_run_id, created_at, updated_at) "
                              "VALUES (?,?,?,?,?,?,?,?,?)", (env, cat, pid, it["field_id"], r["table_id"], it["target_name"], r["run_id"], now, now))
        elif row[1] != pid:
            self.conn.execute("UPDATE calc_formula_crosswalk SET parameter_id=?, ctgypa_id=NULL, expression=NULL, source_run_id=?, updated_at=? WHERE placement_id=?", (pid, r["run_id"], now, row[0]))
        self.conn.commit()

    def pid_of(self, it):
        row = self.conn.execute("SELECT parameter_id FROM calc_formula_crosswalk WHERE environment=? AND category_id=? AND airtable_field_id=?", (self.run["environment"], self.run["category_id"], it["field_id"])).fetchone()
        return row[0] if row else None

    # ---- pass 1: the fields
    def pass_create(self, items):
        todo = [it for it in items if it["step"] == "none"]
        for n, it in enumerate(todo):
            self.progress({"phase": "create", "done": n, "total": len(todo), "item": it["target_name"]})
            if not it["data_type"] or not it["display_type"]:
                self.fail(it, "create", "No dataType or displayType was chosen for this field, so it cannot be created. Run a new preflight after setting the Core type.")
                continue
            try:
                hits = sorted((x for x in _rows_of(self.call("find_param", it["item_id"], "GET", "/param/search", None, {"name": it["target_name"], "name_Mod": "eq", "fields": PARAM_FIELDS}))
                               if str(x.get("name") or "").casefold() == it["target_name"].casefold() and x.get("ID")), key=lambda x: str(x["ID"]))
                if len(hits) > 1:
                    self.fail(it, "create", "More than one field in Workfront is named %s, so none can be chosen. Remove or rename the extras." % it["target_name"])
                    continue
                if hits:
                    h = hits[0]
                    have = (str(h.get("dataType") or ""), str(h.get("displayType") or ""))
                    want = (it["data_type"], it["display_type"])
                    if (have[0] and have[0] != want[0]) or (have[1] and have[1] != want[1]):
                        self.fail(it, "create", "A field named %s already exists with the dataType %s and displayType %s; this formula needs %s and %s. It was not changed." % (it["target_name"], have[0] or "?", have[1] or "?", want[0], want[1]))
                        continue
                    pid = str(h["ID"])
                    self.counts["reused"] += 1
                else:
                    body = {"name": it["target_name"], "dataType": it["data_type"], "displayType": it["display_type"]}
                    if it["target_label"] and it["target_label"] != it["target_name"]:
                        body["label"] = it["target_label"]
                    d = (_rows_of(self.call("create_param", it["item_id"], "POST", "/param", body, {"fields": "ID,lastUpdateDate"})) or [{}])[0]
                    pid = str(d.get("ID") or "")
                    if not pid:
                        self.fail(it, "create", "Workfront answered the create but its reply holds no ID. Check Workfront for a field named %s." % it["target_name"])
                        continue
                    self.counts["created"] += 1
                self.place(it, pid)
            except HttpError as e:
                self.fail(it, "create", str(e))
                continue
            except sqlite3.IntegrityError as e:
                self.fail(it, "create", "The field exists in Workfront (%s) but its placement on this form could not be recorded: %s" % (pid, e))
                continue
            self.set_step(it, "created")

    # ---- pass 2: the form
    def pass_attach(self, items):
        todo = [it for it in items if it["step"] == "created"]
        if not todo:
            return
        cat, pids = self.run["category_id"], {}
        for it in todo:
            pids[it["item_id"]] = self.pid_of(it)
        try:
            form = self.form_read("read_form", None, FORM_FIELDS)
            have = [c for c in form["associations"] if c.get("parameterID")]
            present = {str(c["parameterID"]) for c in have}
            new = []
            for it in todo:
                if pids[it["item_id"]] not in present and pids[it["item_id"]] not in new:
                    new.append(pids[it["item_id"]])
            if new:
                if self.backup:                            # the form as it was, before anything is changed
                    self.backup(self.run, form["raw"])
                keep = [self.carry(c, i) for i, c in enumerate(have)]
                top = max([k["displayOrder"] for k in keep] or [0])
                # A new field goes at the END of the form, with no section and no shared row: only its order is set. Beside a field, or in a section, is a decision for a person.
                keep += [{"parameterID": pid, "displayOrder": top + i + 1, "isRequired": False, "securityLevel": DEFAULT_SECURITY} for i, pid in enumerate(new)]
                self.call("attach_param", None, "PUT", "/CTGY/" + cat, {"categoryParameters": keep})
                after = self.form_read("read_association", None, FORM_FIELDS)
                self.compare(keep[:len(have)], after["associations"])
            else:
                after = form
            assoc = {str(c["parameterID"]): str(c["ID"]) for c in after["associations"] if c.get("parameterID") and c.get("ID")}
        except HttpError as e:
            for it in todo:
                self.fail(it, "attach", "The form could not be updated: %s" % e)
            return
        for it in todo:
            pid = pids[it["item_id"]]
            if pid not in assoc:
                self.fail(it, "attach", "The field %s is not on the form after the update, so its expression cannot be written." % it["target_name"])
                continue
            self.conn.execute("UPDATE calc_formula_crosswalk SET ctgypa_id=?, updated_at=? WHERE environment=? AND category_id=? AND airtable_field_id=?", (assoc[pid], utc_now(), self.run["environment"], cat, it["field_id"]))
            self.conn.commit()
            self.counts["attached"] += 1
            self.set_step(it, "attached")

    def form_read(self, step, item_id, fields):
        data = (_rows_of(self.call(step, item_id, "GET", "/CTGY/" + self.run["category_id"], None, {"fields": fields})) or [{}])[0]
        return {"associations": [c for c in (data.get("categoryParameters") or []) if isinstance(c, dict)], "raw": data}

    @staticmethod
    def carry(c, index):
        """One existing association as it must be sent back: every setting Workfront returned for it, unchanged. Only a missing displayOrder gets a value (its place in the list)."""
        out = {k: c[k] for k in ASSOC_KEYS if c.get(k) is not None}
        out["parameterID"] = str(c["parameterID"])
        if not isinstance(out.get("displayOrder"), int):
            out["displayOrder"] = index + 1
        out.setdefault("isRequired", False)
        out.setdefault("securityLevel", DEFAULT_SECURITY)
        return out

    def compare(self, sent, after):
        """Step 5 of the safe sequence: every association that was on the form must still be there with the settings that were sent. A difference is reported, never hidden."""
        now = {str(c.get("parameterID")): c for c in after}
        for s in sent:
            c = now.get(s["parameterID"])
            if c is None:
                self.form_notes.append("The field %s was on the form before the update and is not there after it. Check the form in Workfront." % s["parameterID"])
                continue
            diff = [k for k in ASSOC_KEYS if k in s and k in c and s[k] != c[k]]
            if diff:
                self.form_notes.append("The field %s on the form came back with a different %s after the update. Check the form in Workfront." % (s["parameterID"], ", ".join(diff)))

    # ---- pass 3: the expressions
    def pass_write(self, items):
        todo = [it for it in items if it["step"] == "attached"]
        for n, it in enumerate(todo):
            self.progress({"phase": "write", "done": n, "total": len(todo), "item": it["target_name"]})
            row = self.conn.execute("SELECT ctgypa_id FROM calc_formula_crosswalk WHERE environment=? AND category_id=? AND airtable_field_id=?", (self.run["environment"], self.run["category_id"], it["field_id"])).fetchone()
            ctgypa = row[0] if row else None
            if not ctgypa or not ID_RE.match(ctgypa):
                self.fail(it, "write", "The association ID of this field on the form is not known.")
                continue
            try:
                self.call("write_expression", it["item_id"], "PUT", "/CTGYPA/" + ctgypa, {"rawCustomExpression": it["expression"]})
            except HttpError as e:
                self.fail(it, "write", str(e))
                continue
            self.conn.execute("UPDATE calc_formula_crosswalk SET expression=?, updated_at=? WHERE environment=? AND category_id=? AND airtable_field_id=?", (it["expression"], utc_now(), self.run["environment"], self.run["category_id"], it["field_id"]))
            self.conn.commit()
            self.counts["written"] += 1
            self.set_step(it, "expression_written")

    def pass_verify(self, items):
        todo = [it for it in items if it["step"] == "expression_written"]
        if not todo:
            return
        try:
            read = {str(c["parameterID"]): c for c in self.form_read("verify", None, EXPR_FIELDS)["associations"] if c.get("parameterID")}
        except HttpError as e:
            for it in todo:
                self.fail(it, "verify", "The expression was written but could not be read back: %s" % e)
            return
        for it in todo:
            c = read.get(self.pid_of(it) or "")
            got = None if c is None else (c.get("rawCustomExpression") if c.get("rawCustomExpression") not in (None, "") else (c.get("categoryParameterExpression") or {}).get("customExpression"))
            if c is not None and _squash(got) == _squash(it["expression"]):
                self.counts["verified"] += 1
                self.set_step(it, "verified")
            else:
                shown = "nothing" if got in (None, "") else "%r" % str(got)[:200]
                self.fail(it, "verify", "The expression was written, but Workfront read back %s instead of the one sent. Check the field in Workfront." % shown)


def check_ready(conn, run_id, valid_form_ids):
    """The run row, or PushError (nothing sent) when the run is not approved, its form is not on the list of valid forms, or the formula fields changed since the preflight."""
    try:
        run = cpf._run_row(conn, run_id)
    except cpf.PreflightError as e:
        raise PushError(e.message, e.status)
    if run["state"] not in ("approved", "pushing", "pushed"):
        raise PushError("This run is %s. Only an approved run can be pushed." % run["state"])
    if not ID_RE.match(run["category_id"]) or run["category_id"] not in set(valid_form_ids):
        raise PushError("The form %s is not on the list of valid forms. Upload the list that holds it first." % run["category_id"])
    changes = cpf.drift(conn, run_id)
    if changes:
        raise PushError("Run a new preflight first: " + " ".join(changes))
    return run


def push(conn, run_id, http, valid_form_ids, should_stop=lambda: False, progress=lambda p: None, item_ids=None, backup=None):
    """Run the push of an approved run (or resume one that was started). Returns {"run_id", "state", "counts", "failed": [...], "fatal", "cancelled", "nothing_to_do"}. Raises PushError, with
    nothing sent, when the run is not approved, the form is not on the list, the schema drifted, or a named item is not part of the run."""
    run = check_ready(conn, run_id, valid_form_ids)
    now = utc_now()
    for r in conn.execute("SELECT item_id FROM calc_preflight_items WHERE run_id=? AND status='TRANSLATABLE'", (run_id,)).fetchall():
        conn.execute("INSERT OR IGNORE INTO calc_push_items (item_id, step_done, updated_at) VALUES (?, 'none', ?)", (r[0], now))
    rows = conn.execute("SELECT i.item_id, i.formula_field_id, i.translated_expression, i.target_name, i.target_label, i.workfront_data_type, i.workfront_display_type, i.push_order, p.step_done "
                        "FROM calc_preflight_items i JOIN calc_push_items p ON p.item_id = i.item_id WHERE i.run_id=? AND i.status='TRANSLATABLE' ORDER BY i.push_order, i.formula_field_id", (run_id,)).fetchall()
    items = [dict(zip(("item_id", "field_id", "expression", "target_name", "target_label", "data_type", "display_type", "push_order", "step"), r)) for r in rows]
    if item_ids is not None:
        wanted = {int(x) for x in item_ids}
        unknown = wanted - {it["item_id"] for it in items}
        if unknown:
            raise PushError("Item(s) %s are not translatable items of this run." % ", ".join(str(x) for x in sorted(unknown)), 400)
        items = [it for it in items if it["item_id"] in wanted]
    open_items = [it for it in items if it["step"] != "verified"]
    summary = {"run_id": run_id, "state": run["state"], "counts": {}, "failed": [], "fatal": None, "cancelled": False, "form_notes": [], "nothing_to_do": not open_items, "items": len(items)}
    if not open_items:
        conn.commit()
        return summary
    conn.execute("UPDATE calc_preflight_runs SET state='pushing' WHERE run_id=?", (run_id,))
    conn.commit()
    p = _Pusher(conn, run, http, should_stop, progress, backup)
    try:
        p.pass_create(items)
        p.pass_attach(items)
        p.pass_write(items)
        p.pass_verify(items)
    except Stop:
        summary["cancelled"] = True
    except Fatal as e:
        summary["fatal"] = str(e)
    finally:
        http.recorder = None
    summary["counts"] = p.counts
    summary["form_notes"] = p.form_notes
    names = {it["item_id"]: it for it in items}
    summary["failed"] = [{"item_id": i, "field_id": names[i]["field_id"], "name": names[i]["target_name"], "step": s, "message": m} for i, (s, m) in sorted(p.failed.items())]
    left = conn.execute("SELECT COUNT(*) FROM calc_push_items p JOIN calc_preflight_items i ON i.item_id = p.item_id WHERE i.run_id=? AND p.step_done NOT IN ('expression_written', 'verified')", (run_id,)).fetchone()[0]
    state = "pushed" if left == 0 else "pushing"
    conn.execute("UPDATE calc_preflight_runs SET state=? WHERE run_id=?", (state, run_id))
    conn.commit()
    summary["state"] = state
    return summary
