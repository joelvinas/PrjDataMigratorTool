#!/usr/bin/env python3
"""The Workfront Planning transport: creates record types and fields (select options go with their field). Standard library only.

Routes and property names come from the saved Planning API v2 reference:
    POST /v2/workspaces/{workspaceId}/record-types       body {displayName, description}                 -> {id, ...}
    POST /v2/record-types/{recordTypeId}/fields          body {displayName, type, description, ...}      -> {id, options, ...}
    POST /v2/record-types/{recordTypeId}/records/bulk    body [{"data": {"<fieldId>": value}}]            -> 201 (all created) or 207 (some failed): {status, total, succeeded, failed,
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
A description over the 512 characters the plan allows is refused here and never cut. Nothing is sent by describe().
"""
import re

import loadrun as lr
import planlimits as pl
import typemap as tm
from workfronthttp import HttpError

_CONFLICT = re.compile(r"(?i)NOT_UNIQUE|already exists|must be unique|duplicate")
DATE_FORMAT = "locale"      # the example value in the v2 reference; unverified for other values
MAX_PAGES = 100


class UnmatchedReply(HttpError):
    """Planning answered, but the answer cannot be matched to the records sent, so they MAY exist. The caller must not send them again."""


class PlanningTransport(object):
    system = "planning"

    def __init__(self, http):
        if not http.creds.workspace_id:
            raise ValueError("The Planning settings in config.json need a workspace_id (the DEV Workspace to load into).")
        self.http, self.workspace_id = http, http.creds.workspace_id
        self.extra = http.creds.extra or {}
        self._options = {}          # field ID -> {displayName: option key}

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
            raise ValueError("The target data type %r is not one this loader knows; check the new_dataType value for this field." % str(item.get("data_type") or ""))
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
            body["options"] = [{"displayName": n} for n in names]
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
        return str(new)

    def _remember(self, field_id, options):
        self._options[field_id] = {str(o.get("displayName")): str(o.get("name") if o.get("name") not in (None, "") else o.get("displayName"))
                                   for o in (options or []) if isinstance(o, dict) and o.get("displayName") is not None}

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
        """Create records, one bulk call per record type: POST /v2/record-types/{recordTypeId}/records/bulk with a JSON array of {"data": {fieldId: value}}.
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
            reply = self.http.request("POST", "/v2/record-types/%s/records/bulk" % rt, [{"data": bodies[p].get("fields") or {}} for p in positions])
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
