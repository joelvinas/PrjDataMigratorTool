#!/usr/bin/env python3
"""The Workfront Planning transport: creates record types and fields (select options go with their field). Standard library only.

Routes and property names come from the saved Planning API v2 reference:
    POST /v2/workspaces/{workspaceId}/record-types       body {displayName, description}                 -> {id, ...}
    POST /v2/record-types/{recordTypeId}/fields          body {displayName, type, description, ...}      -> {id, options, ...}
    POST /v2/record-types/{recordTypeId}/records/bulk    body [{"recordTypeId": id, "data": {"<fieldId>": value}}]  -> 201 (all created) or 207 (some failed): {status, total, succeeded, failed,
                                                          requestId, results: [{requestIndex, id, ...}]}; a failed result has status "error" and error {code, message}
Owner-verified (17 November 2026): a field CAN be changed in place with PATCH; the bulk record route, body and reply above (Postman test 5; the exact keys of a SUCCESSFUL
result beyond requestIndex and id, including its status text, were not copied, so success is "no error and an id"); record type names are checked per Workspace
(this loader creates no Global record types); a record needs only its recordTypeId; the description property is `description`; authentication is a Bearer token from config.json.

NOT yet verified, so marked in docs/LOADER_PLAN.md and meant to be proven in the first DEV run:
  * the type-specific options sent for each field type (numberOptions, dateOptions, userOptions, referenceOptions), in particular dateFormat "locale" (the reference's
    example) and no timeFormat, and that a field is created with no primary-field handling (a new record type may already have a primary field);
  * a select field is sent with ALL its ready options in one call (the reference requires a non-empty list), and the option's `name` in the reply is used as its ID, falling
    back to its displayName. Whether options can change afterwards is sandbox item S4;
  * a reference field is created on each side of a two-way pair as its own field, with createBackReference false (Postman test 4 decides whether to use it);
  * how a duplicate record type name is refused (a 409, or FIELD_DISPLAY_NAME_NOT_UNIQUE / "already exists" text, is treated as a name conflict).
Sharing (the API account that creates a record type is its only member and the record type is in no sidebar section, so nobody else can see it). With a `sharing` setting
(loadconfig.planning_sharing) every record type this transport creates is, right after the create call:
    PATCH /v2/permissions/record-types/{id}/members/bulk   body {"grant": [{"subjectId", "subjectType", "role"}], "inheritViewOnly": false}   (204 = accepted, NOT proof)
    GET   /v2/permissions/record-types/{id}/members        the grant is verified: every configured subject must be listed (the endpoint ignores unknown body keys and still answers 204)
    GET   /v2/workspaces/{id}                              the COMPLETE recordTypeSections array is read,
    PATCH /v2/workspaces/{id}   body {"recordTypeSections": [the whole array, with only the new ID added to the named section]}   then read back and checked
A failure of either step never fails the create: it is kept in share_results and shown in the run output. A reused record type is checked the same way (ensure_shared).
A description over the 512 characters the plan allows is refused here and never cut. Nothing is sent by describe().
"""
import copy
import re

import datatypes as dt
import loadrun as lr
import planlimits as pl
import typemap as tm
from workfronthttp import HttpError

_GONE_RE = re.compile(r"(?i)NOT_FOUND")
_CONFLICT = re.compile(r"(?i)NOT_UNIQUE|already exists|must be unique|duplicate")
DATE_FORMAT = "locale"      # the example value in the v2 reference; unverified for other values
MAX_PAGES = 100


def _fold(text):
    """How option display names are compared. Observed in the Planning UI and read back in Postman (23 November 2026):
      * the option `name` is the display name lowercased with each run of whitespace turned into ONE underscore, everything else kept ('Hot Pink' -> hot_pink,
        'Rouge (Dark)' -> rouge_(dark), 'Cafe' with an accent stays accented, an emoji stays);
      * a second display name that gives the same `name` is NOT refused: Planning suffixes it ('hot_pink' typed after 'Hot Pink' became hot_pink_1). So the `name` is never
        guessed here; the one Planning returns is used (read back by display name);
      * edge spaces are trimmed and the stored display name is the trimmed text; inner spaces are left as typed.
    So two options clash only when their trimmed display names match. Letter case is ignored here (PROVISIONAL: 'Red' then 'red' is not yet tested; a false clash only sends the
    field to a PRE- text field with the reason shown, and loses no data)."""
    return str(text).strip().lower()


def check_options(names, increment=False):
    """The duplicate check for a select field's options, from the SCHEMA only (no record value). Run before a single or multiple select field is created.
    Returns {options: the display names to send (trimmed, blank names and exact repeats removed, Airtable's order kept), exact_repeats: n removed, blank: n removed,
    conflicts: one entry per group of names that collide once edge spaces and letter case are ignored (the folded name), sorted,
    alias: {the trimmed source name: the display name sent for it}, incremented: [(source name, display name sent)] for each name that was renamed}.
    An exact repeat carries no information, so it is sent once. Two DIFFERENT names that fold together ('Red' and 'red') are never merged here: records may hold either one,
    so merging is a person's decision. With any conflict the field must not be sent as a select.
    increment=True (a lookup snapshot, where nobody can fix the source) keeps both instead: the first keeps its name and each later one that folds onto a name already
    taken gets a number, 'red 2' (no brackets: they are not allowed in option names), counted up until it is free. `alias` then tells the record load which option each source
    value belongs to. Nothing is guessed: the same names in the same order always give the same display names, and there are no conflicts to report."""
    options, seen, folded, repeats, blank, alias, incremented = [], set(), {}, 0, 0, {}, []
    taken = set()
    real = {_fold(n) for n in names or [] if str(n).strip()}          # every real name, folded, so a number is never put on a name that is a real option listed later
    for n in names or []:
        s = str(n).strip()                          # Planning stores the trimmed text, so the name is sent trimmed and read back by that text
        if not s:
            blank += 1
        elif s in seen:
            repeats += 1
        else:
            seen.add(s)
            shown = s
            if increment and _fold(s) in taken:
                k = 2
                while _fold("%s %d" % (s, k)) in taken or _fold("%s %d" % (s, k)) in real:
                    k += 1
                shown = "%s %d" % (s, k)
                incremented.append((s, shown))
            taken.add(_fold(shown))
            alias[s] = shown
            options.append(shown)
            folded.setdefault(_fold(shown), []).append(shown)
    return {"options": options, "exact_repeats": repeats, "blank": blank, "conflicts": sorted(k for k, v in folded.items() if len(v) > 1), "alias": alias, "incremented": incremented}


class UnmatchedReply(HttpError):
    """Planning answered, but the answer cannot be matched to the records sent, so they MAY exist. The caller must not send them again."""


class PlanningTransport(object):
    system = "planning"

    def __init__(self, http, sharing=None):
        if not http.creds.workspace_id:
            raise ValueError("The Planning settings in config.json need a workspace_id (the DEV Workspace to load into).")
        self.http, self.workspace_id = http, http.creds.workspace_id
        self.extra = http.creds.extra or {}
        self._options = {}          # field ID -> {displayName: option key}
        self.sharing = sharing      # {"share_with": [...], "section_name": "..."} from loadconfig.planning_sharing, or None (then record types are NOT shared)
        self.share_results = []     # one dict per record type shared or checked: {record_type_id, members, section, problems, notes}

    # ---- what would be sent
    def _currency(self, spec):
        sym = (spec or {}).get("currency_symbol")
        code = (self.extra.get("currency_by_symbol") or {}).get(sym) if isinstance(self.extra.get("currency_by_symbol"), dict) else None
        code = code or self.extra.get("default_currency")
        if not code:
            raise ValueError("A currency field needs an ISO currency code. Add default_currency (and optionally currency_by_symbol) to the Planning settings in config.json.")
        return str(code)

    def request_for(self, item, parent_id, refs=None):
        """(method, path, body), or None for an item that needs no call of its own (an option is sent with its field). Raises ValueError with a plain message."""
        desc = item.get("description")
        if desc and len(desc) > pl.MAX_DESCRIPTION:
            raise ValueError("The description is %d characters and the limit used here is %d; shorten it. It is never cut." % (len(desc), pl.MAX_DESCRIPTION))
        if item["kind"] == "record_type":
            body = {"displayName": item["name"]}
            if desc:
                body["description"] = desc
            return "POST", "/v2/workspaces/%s/record-types" % self.workspace_id, body
        if item["kind"] == "option":
            return None
        if item["kind"] != "field":
            raise ValueError("Planning creates record types and fields only.")
        if not parent_id:
            raise ValueError("A field needs its record type's Planning ID first.")
        vtype, k = tm.planning_type(item["data_type"])
        if vtype is None:
            if dt.planning_type(item.get("data_type")):
                raise ValueError("The Planning type %r is on the Adobe list but this loader does not create it (derived and system fields stay on hold)." % str(item["data_type"]))
            raise ValueError("The target data type %r is not a Planning data type; set one on the Data types page." % str(item.get("data_type") or ""))
        spec = item.get("spec") or {}
        body = {"displayName": item["name"], "type": vtype}
        if desc:
            body["description"] = desc
        if vtype in ("number", "percentage"):
            body["numberOptions"] = dict({"allowNegatives": True}, **({"precision": int(spec["precision"])} if spec.get("precision") is not None else {}))
        elif vtype == "currency":
            body["numberOptions"] = dict({"allowNegatives": True, "currency": self._currency(spec)}, **({"precision": int(spec["precision"])} if spec.get("precision") is not None else {}))
        elif vtype == "date":
            body["dateOptions"] = {"dateFormat": DATE_FORMAT}
        elif vtype == "user":
            body["userOptions"] = {"multipleUsersAllowed": True}
        elif vtype in ("single-select", "multi-select"):
            names = list(spec.get("options") or [])
            if not names:
                raise ValueError("A select field needs at least one option, and none of this field's options is ready.")
            chk = check_options(names)
            if chk["conflicts"]:
                dup = chk["conflicts"]
                raise ValueError("%d option name(s) repeat another option of this field once letter case is ignored, and Planning refuses that (option display names must be unique): %s%s. Nothing was sent; the options must be made distinct first."
                                 % (len(dup), "; ".join(repr(d) for d in dup[:10]), " and more" if len(dup) > 10 else ""))
            if not chk["options"]:
                raise ValueError("A select field needs at least one option, and none of this field's options is usable (they are all blank).")
            body["options"] = [{"displayName": n} for n in chk["options"]]
        elif vtype == "reference":
            target = [v for v in (refs or {}).values()]
            if not target:
                raise ValueError("A link needs the Planning ID of the record type it points to (second pass).")
            body["referenceOptions"] = {"recordTypeId": target[0], "multiple": not spec.get("single"), "createBackReference": False}
        return "POST", "/v2/record-types/%s/fields" % parent_id, body

    def describe(self, item, parent_id, refs=None):
        r = self.request_for(item, parent_id, refs)
        return {"system": "planning", "action": "create " + item["kind"], "name": item["name"], "label": item["label"], "data_type": item["data_type"], "position": item["position"],
                "parent": parent_id, "temp_id": item["temp_id"], "request": ("%s %s" % r[:2]) if r else "none: sent with its field", "body": r[2] if r else None}

    # ---- sending
    def create(self, item, parent_id, refs=None):
        r = self.request_for(item, parent_id, refs)
        if r is None:
            return self._option_key(item, parent_id)
        method, path, body = r
        try:
            reply = self.http.request(method, path, body)
        except HttpError as e:
            if e.status == 409 or _CONFLICT.search(str(e)):
                raise lr.NameConflict(str(e))
            raise
        new = reply.get("id") if isinstance(reply, dict) else None
        if not new:
            raise HttpError("The create call worked but its reply holds no ID, so the item cannot be recorded.")
        if item["kind"] == "field":
            self._remember(str(new), reply.get("options"))
        if item["kind"] == "record_type":
            self.share_record_type(str(new))        # never raises: a sharing problem is recorded, and must not lose the new ID
        return str(new)

    # ---- sharing and sidebar placement (see the module docstring). Every call goes through self.http, so it is in the API log.
    def get_members(self, record_type_id):
        reply = self.http.request("GET", "/v2/permissions/record-types/%s/members" % record_type_id)
        members = reply.get("members") if isinstance(reply, dict) else None
        if not isinstance(members, list):
            raise HttpError("The members reply has no members list, so the sharing cannot be checked.")
        return [m for m in members if isinstance(m, dict)]

    def _missing_subjects(self, members):
        have = {(str(m.get("subjectId")), str(m.get("subjectType") or "").lower()): m for m in members}
        return [s for s in self.sharing["share_with"] if (s["subjectId"], s["subjectType"].lower()) not in have], have

    def share_record_type(self, record_type_id, force=True):
        """Grant the configured subjects on the record type and put it in the configured sidebar section, then verify both by reading back. force=False (a record type that
        already existed) first reads the members and grants only the subjects that are missing. NEVER raises. Returns, and records in share_results,
        {record_type_id, members: ok|failed|not done, section: ok|failed|not done, problems: [plain text], notes: [plain text]}."""
        res = {"record_type_id": str(record_type_id), "members": "not done", "section": "not done", "problems": [], "notes": []}
        self.share_results.append(res)
        if not self.sharing:
            res["problems"].append("NOT SHARED: share_with and section_name are not set for this stage in config.json, so only the API account can see this record type.")
            return res
        try:
            self._grant(res, record_type_id, force)
        except Exception as e:                      # one failed step must not stop the run, and must not be silent
            res["members"] = "failed"
            res["problems"].append(self._plain("Sharing", e))
        try:
            self._place(res, record_type_id)
        except Exception as e:
            res["section"] = "failed"
            res["problems"].append(self._plain("Sidebar placement", e))
        return res

    def ensure_shared(self, record_type_id):
        """For a record type that already existed: grant only what is missing and place it if it is in no section. Never raises."""
        return self.share_record_type(record_type_id, force=False)

    @staticmethod
    def _plain(step, e):
        if isinstance(e, HttpError) and e.status == 404:
            return "%s failed: Workfront has no such record type (was it deleted?). Run 'loader.py planning-reset' to forget the old IDs. (%s)" % (step, e)
        return "%s failed: %s" % (step, lr.scrub(e))

    def _grant(self, res, rt, force):
        grant = self.sharing["share_with"]
        if not force:
            missing, _have = self._missing_subjects(self.get_members(rt))
            if not missing:
                res["members"] = "ok"
                res["notes"].append("already shared with every configured subject")
                return
            grant = missing
        # exactly these keys: the endpoint ignores unknown ones and still answers 204, so a wrong body would look like success
        body = {"grant": [{"subjectId": g["subjectId"], "subjectType": g["subjectType"], "role": g["role"]} for g in grant], "inheritViewOnly": False}
        self.http.request("PATCH", "/v2/permissions/record-types/%s/members/bulk" % rt, body)
        missing, have = self._missing_subjects(self.get_members(rt))        # a 204 is not proof
        if missing:
            res["members"] = "failed"
            res["problems"].append("Sharing NOT confirmed: the grant call was accepted but %d configured subject(s) are not listed as members: %s." % (len(missing), ", ".join(m["subjectId"] for m in missing)))
            return
        res["members"] = "ok"
        for g in self.sharing["share_with"]:
            got = have.get((g["subjectId"], g["subjectType"].lower()))
            if got is not None and got.get("role") not in (None, g["role"]):
                res["notes"].append("subject %s is a member with the role %r, not the configured %r" % (g["subjectId"], got.get("role"), g["role"]))

    @staticmethod
    def _norm(text):
        return " ".join(str(text or "").split()).lower()

    def _sections(self):
        w = self.http.request("GET", "/v2/workspaces/%s" % self.workspace_id)
        secs = w.get("recordTypeSections") if isinstance(w, dict) else None
        if not isinstance(secs, list):
            raise HttpError("The Workspace reply has no recordTypeSections list, so it was left alone (a partial list could hide the other record types).")
        return secs

    @staticmethod
    def _ids_of(secs):
        return {str(i) for s in secs if isinstance(s, dict) for i in (s.get("recordTypeIds") or [])}

    def _place(self, res, rt):
        """Add the record type to the named section. Reads the WHOLE array, changes only that section (or appends a new one), sends the WHOLE array back, reads it again."""
        name, rt = self.sharing["section_name"], str(rt)
        notes_before = list(res["notes"])
        for attempt in range(3):
            secs = copy.deepcopy(self._sections())
            before = self._ids_of(secs)
            holder = next((s for s in secs if isinstance(s, dict) and rt in [str(i) for i in (s.get("recordTypeIds") or [])]), None)
            if holder is not None:
                res["section"] = "ok"
                res["notes"].append("already in the sidebar section %r" % holder.get("displayName"))
                return
            target = next((s for s in secs if isinstance(s, dict) and self._norm(s.get("displayName")) == self._norm(name)), None)
            res["notes"] = list(notes_before)
            if target is None:
                secs.append({"displayName": name, "recordTypeIds": [rt]})
                res["notes"].append("created the sidebar section %r" % name)
            else:
                if not isinstance(target.get("recordTypeIds"), list):
                    target["recordTypeIds"] = []
                target["recordTypeIds"].append(rt)
            try:
                self.http.request("PATCH", "/v2/workspaces/%s" % self.workspace_id, {"recordTypeSections": secs})
                break
            except HttpError as e:
                if e.status == 409 and attempt < 2:         # a write conflict: read the array again and redo the merge
                    continue
                raise
        after = self._sections()
        named = next((s for s in after if isinstance(s, dict) and self._norm(s.get("displayName")) == self._norm(name)), None)
        lost = sorted(before - self._ids_of(after))
        if named is None or rt not in [str(i) for i in (named.get("recordTypeIds") or [])]:
            res["section"] = "failed"
            res["problems"].append("Sidebar placement NOT confirmed: the Workspace was updated but the record type is not in the section %r when read back." % name)
        elif lost:
            res["section"] = "failed"
            res["problems"].append("Sidebar placement changed other record types: %d ID(s) that were in a section are gone after the update (%s). Check the Workspace sidebar." % (len(lost), ", ".join(lost[:10])))
        else:
            res["section"] = "ok"

    def _remember(self, field_id, options):
        self._options[field_id] = {str(o.get("displayName")): str(o.get("name") if o.get("name") not in (None, "") else o.get("displayName"))
                                   for o in (options or []) if isinstance(o, dict) and o.get("displayName") is not None}

    def options_for(self, field_id, record_type_id):
        """{displayName: option name} for a select field, the names Planning returned (authoritative: never guessed from the label). Read from the create reply when it is
        remembered, otherwise from the record type's field list (GET /v2/record-types/{id}/fields), the way the owner's verified multi-select sample does."""
        if field_id not in self._options:
            for r in self._pages("/v2/record-types/%s/fields" % record_type_id):
                if isinstance(r, dict) and r.get("id") and r.get("options"):
                    self._remember(str(r["id"]), r.get("options"))
        return dict(self._options.get(field_id) or {})

    def _option_key(self, item, field_id):
        """A select option is created with its field, so its 'creation' reads its key back from the field (fetched again after a stop and restart)."""
        if not field_id:
            raise ValueError("An option needs its field's Planning ID first.")
        if field_id not in self._options:
            reply = self.http.request("GET", "/v2/fields/%s" % field_id)
            self._remember(field_id, reply.get("options") if isinstance(reply, dict) else None)
        got = self._options[field_id]
        key = got.get(item["name"])
        if key is None:
            low = {k.lower(): v for k, v in got.items()}
            key = low.get(item["name"].lower())
        if key is None:
            raise HttpError("The option %r is not in the field Planning created (it may not have been ready when the field was sent)." % item["name"])
        return key

    # ---- records (route, body and reply verified by Postman test 5, 17 November 2026; see the module docstring)
    def create_records(self, bodies):
        """Create records, one bulk call per record type: POST /v2/record-types/{recordTypeId}/records/bulk with a JSON array of {"recordTypeId": id, "data": {fieldId: value}}.
        Every element repeats the recordTypeId even though it is in the URL: without it Planning answers 400 INVALID_DATA (reported by a peer, 18 November 2026).
        Each body is {recordTypeId, fields}. Returns one result per body, in the order given: {"id": "..."} for a record Planning created, or {"error": "short reason"}.

        The reply (201 all created, 207 some failed) holds results[], each with requestIndex; a result is matched to its record by requestIndex, never by position.
        A result is a failure when it has an `error` object or status "error" (a failed result can still carry an id, so the error is checked first). Only the error CODE
        is kept as the reason: Planning's message text could echo a value. A record with no result is reported as an error, never assumed created.
        A call that is refused as a whole (an HTTP error) raises, so the caller knows nothing was recorded."""
        out = [None] * len(bodies)
        groups = {}
        for pos, b in enumerate(bodies):
            groups.setdefault(str(b.get("recordTypeId") or ""), []).append(pos)
        for rt in sorted(groups):
            if not rt:
                raise ValueError("A record needs its record type's Planning ID.")
            positions = groups[rt]
            reply = self.http.request("POST", "/v2/record-types/%s/records/bulk" % rt, [{"recordTypeId": rt, "data": bodies[p].get("fields") or {}} for p in positions])
            rows = reply.get("results") if isinstance(reply, dict) else None
            if not isinstance(rows, list):
                raise UnmatchedReply("The create call worked but its reply has no results list, so the records cannot be matched.")
            seen = {}
            for r in rows:
                i = r.get("requestIndex") if isinstance(r, dict) else None
                if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(positions):
                    seen.setdefault(i, []).append(r)
            for i, p in enumerate(positions):
                got = seen.get(i) or []
                if len(got) != 1:
                    out[p] = {"error": "the reply has no result for this record; it may exist in Planning, so check there before running again" if not got else "the reply holds two results for this record; check Planning before running again"}
                    continue
                r = got[0]
                if r.get("error") or str(r.get("status") or "").lower() == "error":
                    code = (r.get("error") or {}).get("code") if isinstance(r.get("error"), dict) else None
                    out[p] = {"error": "Planning refused this record (%s)" % (str(code)[:40] if code else "no error code")}
                elif r.get("id"):
                    out[p] = {"id": str(r["id"])}
                else:
                    out[p] = {"error": "the reply holds no ID for this record"}
        return out

    def update_record(self, record_id, fields):
        """Set fields on one record (used for the links second pass)."""
        if not record_id or not fields:
            raise ValueError("A record update needs the record's Planning ID and at least one field.")
        self.http.request("PATCH", "/v2/records/%s" % record_id, {"fields": fields})

    # ---- finding what already exists
    def _pages(self, path):
        cursor, n = None, 0
        while n < MAX_PAGES:
            q = {"limit": 100}
            if cursor:
                q["cursor"] = cursor
            reply = self.http.request("GET", path, None, q)
            if not isinstance(reply, dict):
                return
            for row in reply.get("content") or []:
                yield row
            cur = reply.get("cursor") or {}
            cursor = cur.get("nextCursor") if cur.get("hasMore") else None
            n += 1
            if not cursor:
                return

    def find_existing(self, item, parent_id, refs=None):
        """The IDs of record types or fields that already have this name (exactly), for adopting after an interrupted run."""
        if item["kind"] == "record_type":
            path = "/v2/workspaces/%s/record-types" % self.workspace_id
        elif item["kind"] == "field" and parent_id:
            path = "/v2/record-types/%s/fields" % parent_id
        else:
            return []
        return sorted(str(r["id"]) for r in self._pages(path) if isinstance(r, dict) and r.get("displayName") == item["name"] and r.get("id"))

    def get_workspace(self, workspace_id=None):
        """The Workspace record (GET /v2/workspaces/{id}, from the reference): used to prove the token works and that the Workspace ID exists, and to show its name."""
        reply = self.http.request("GET", "/v2/workspaces/%s" % (workspace_id or self.workspace_id))
        return reply if isinstance(reply, dict) else {}

    # ---- reading and deleting records, for a wipe (route and body from docs/WF_Planning_v2_API_simplified.yaml; not yet run against a real Workfront)
    def get_record_type(self, record_type_id):
        if not record_type_id:
            raise ValueError("A record type ID is needed.")
        reply = self.http.request("GET", "/v2/record-types/%s" % record_type_id)
        return reply if isinstance(reply, dict) else {}

    def delete_record_type(self, record_type_id, workspace_id=None):
        """Permanently delete a record type, with its fields and records: DELETE /v2/record-types/{id} (204; 403 RECORD_TYPE_IN_HIERARCHY; 404). Not yet run against a real Workfront.
        First reads the record type and refuses one whose primaryWorkspaceId is not `workspace_id` (when both are known). Never raises for a refusal: returns
        {"deleted": True, "already_gone": bool} or {"deleted": False, "status": HTTP status or None, "reason": "short cleaned text"}. A 404 counts as deleted (it is gone)."""
        if not record_type_id:
            raise ValueError("A record type ID is needed.")
        try:
            rt = self.get_record_type(record_type_id)
            home = rt.get("primaryWorkspaceId") if isinstance(rt, dict) else None
            if home and workspace_id and str(home) != str(workspace_id):
                return {"deleted": False, "status": None, "reason": "The record type belongs to a different Workspace than the workspace_id in config.json, so it was not deleted."}
            self.http.request("DELETE", "/v2/record-types/%s" % record_type_id)
        except HttpError as e:
            if e.status == 404:
                return {"deleted": True, "already_gone": True}
            return {"deleted": False, "status": e.status, "reason": str(e)}
        return {"deleted": True, "already_gone": False}

    # ---- preparing a new record type: its default fields (Name, Description, Start Date, End Date, Status) clash with Airtable names. The owner confirmed in the Planning UI (19 November
    #      2026) that once another field is the PRIMARY field, the defaults can be deleted. Routes from docs/WF_Planning_v2_API_simplified.yaml; not yet run against a real Workfront.
    def list_fields(self, record_type_id):
        """Every field of the record type, as [{id, name, type}] in the order Planning lists them (all pages)."""
        if not record_type_id:
            raise ValueError("A record type ID is needed.")
        return [{"id": str(r["id"]), "name": str(r.get("displayName") or ""), "type": str(r.get("type") or "")}
                for r in self._pages("/v2/record-types/%s/fields" % record_type_id) if isinstance(r, dict) and r.get("id")]

    def set_primary_field(self, record_type_id, field_id):
        """PATCH /v2/record-types/{id} with primaryFieldId (the displayName is read first and sent with it, because the update schema lists it as required), then read the record type
        again and check that Planning really holds the new primary field: an accepted call is not proof. Raises HttpError with a plain reason when it is not confirmed."""
        if not record_type_id or not field_id:
            raise ValueError("A record type ID and a field ID are needed.")
        rt = self.get_record_type(record_type_id)
        if str(rt.get("primaryFieldId") or "") == str(field_id):
            return True
        body = {"displayName": rt.get("displayName") or "", "primaryFieldId": str(field_id)}
        self.http.request("PATCH", "/v2/record-types/%s" % record_type_id, body)
        got = self.get_record_type(record_type_id).get("primaryFieldId")
        if str(got or "") != str(field_id):
            raise HttpError("The primary field change was accepted but Planning still reports a different primary field when the record type is read back.")
        return True

    def delete_field(self, field_id):
        """DELETE /v2/fields/{id} (204; 404 = already gone). Never raises for a refusal: {"deleted": True, "already_gone": bool} or {"deleted": False, "status", "reason"}."""
        if not field_id:
            raise ValueError("A field ID is needed.")
        try:
            self.http.request("DELETE", "/v2/fields/%s" % field_id)
        except HttpError as e:
            if e.status == 404:
                return {"deleted": True, "already_gone": True}
            return {"deleted": False, "status": e.status, "reason": lr.scrub(e)}
        return {"deleted": True, "already_gone": False}

    def field_permissions(self, field_id):
        """What THIS token may do with the field: GET /v2/permissions/fields/{id} -> {canView, canEdit, canDelete, canAdd} (only the flags that are booleans). Never raises: {} when it cannot be read.
        Used to explain a refused delete (403 FORBIDDEN: not enough access for the API account)."""
        try:
            reply = self.http.request("GET", "/v2/permissions/fields/%s" % field_id)
        except Exception:
            return {}
        return {k: reply[k] for k in ("canView", "canEdit", "canDelete", "canAdd") if isinstance(reply, dict) and isinstance(reply.get(k), bool)}

    def rename_field(self, field_id, new_name):
        """PATCH /v2/fields/{id} with only displayName (merge-patch: nothing else changes). Used when Planning refuses to delete a default field. Never raises for a refusal:
        {"renamed": True} or {"renamed": False, "status", "reason"}. Not yet run against a real Workfront."""
        if not field_id or not new_name:
            raise ValueError("A field ID and a new name are needed.")
        try:
            self.http.request("PATCH", "/v2/fields/%s" % field_id, {"displayName": new_name})
        except HttpError as e:
            return {"renamed": False, "status": e.status, "reason": lr.scrub(e)}
        return {"renamed": True}

    def list_record_ids(self, record_type_id, size=100):
        """(the first page's record IDs, total records in the record type). Only IDs are kept: the record values in the reply are dropped at once. GET /v2/record-types/{id}/records?page=0&size=N."""
        if not record_type_id:
            raise ValueError("A record type ID is needed.")
        reply = self.http.request("GET", "/v2/record-types/%s/records" % record_type_id, None, {"page": 0, "size": int(size)})
        rows = reply.get("content") if isinstance(reply, dict) else None
        if not isinstance(rows, list):
            raise HttpError("The record list reply has no content list, so the wipe stops rather than guess.")
        ids = [str(r["id"]) for r in rows if isinstance(r, dict) and r.get("id")]
        total = reply.get("totalElements")
        return ids, (total if isinstance(total, int) and not isinstance(total, bool) else len(ids))

    def delete_records(self, record_type_id, ids):
        """Bulk delete, at most 100 IDs: DELETE /v2/record-types/{id}/records/bulk with {"ids": [...]}. Answers 200 (all deleted) or 207 (some failed): results[] with requestIndex.
        Returns one dict per ID, in order: {"deleted": True} (a record that was already gone counts) or {"error": "short reason"} (the error CODE only, never Planning's message).
        A reply that cannot be matched fails every ID in it, so nothing is assumed deleted."""
        ids = [str(i) for i in ids]
        if not record_type_id or not ids or len(ids) > 100:
            raise ValueError("A bulk delete needs a record type and 1 to 100 record IDs.")
        reply = self.http.request("DELETE", "/v2/record-types/%s/records/bulk" % record_type_id, {"ids": ids})
        rows = reply.get("results") if isinstance(reply, dict) else None
        if not isinstance(rows, list):
            return [{"error": "the reply has no results list; check Planning"} for _ in ids]
        seen = {}
        for r in rows:
            i = r.get("requestIndex") if isinstance(r, dict) else None
            if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(ids):
                seen.setdefault(i, []).append(r)
        out = []
        for i in range(len(ids)):
            got = seen.get(i) or []
            if len(got) != 1:
                out.append({"error": "the reply has no single result for this record"})
                continue
            r = got[0]
            if r.get("error") or str(r.get("status") or "").lower() == "error":
                code = (r.get("error") or {}).get("code") if isinstance(r.get("error"), dict) else None
                out.append({"deleted": True} if code and _GONE_RE.search(str(code)) else {"error": "Planning refused the delete (%s)" % (str(code)[:40] if code else "no error code")})
            else:
                out.append({"deleted": True})
        return out
