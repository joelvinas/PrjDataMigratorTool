#!/usr/bin/env python3
"""The Workfront Core transport: creates custom fields (PARAM) and their select options (POPT). Standard library only.

Verified by the owner in a Core sandbox (17 November 2026):
  * POST /param needs only `name` and `dataType`, and the reply carries the new ID.
  * displayType TEXT and TXTA work with any dataType.
  * A RICH field cannot be created through the API (it is built by hand in the custom form viewer), so it is refused with that message.
  * Attaching a field to a custom form (CTGY) is NOT done here: it is a manual step.
NOT yet verified (so marked in typemap.py and in docs/LOADER_PLAN.md): the displayTypes SLCT, MULT and CHCK, a POPT's accepted properties, the lookup used to adopt an
existing field, and how a duplicate name is refused (a 409, or a message saying the name already exists or must be unique, is treated as a name conflict).

The body is the bare object (the schema also accepts {"data": {...}}). Nothing is sent by describe().
"""
import re

import loadrun as lr
import typemap as tm
from workfronthttp import HttpError

_CONFLICT = re.compile(r"(?i)already exists|must be unique|not unique|duplicate")


def reply_id(reply):
    """The new item's ID from a create reply: {"data": {"ID": ...}} or {"ID": ...}. None when it is not there."""
    if isinstance(reply, dict):
        d = reply.get("data", reply)
        if isinstance(d, list) and d:
            d = d[0]
        if isinstance(d, dict):
            v = d.get("ID") or d.get("id")
            return str(v) if v else None
    return None


class CoreTransport(object):
    system = "core"

    def __init__(self, http):
        if not http.creds.api_path:
            raise ValueError("The Core settings in config.json need an api_path that holds the API version, for example /attask/api/v21.0.")
        self.http = http

    # ---- what would be sent
    def request_for(self, item, parent_id, refs=None):
        """(method, path, body) for one item. Raises ValueError (plain message) when the item cannot be created in Core."""
        if item["kind"] == "field":
            data_type, display_type, _verified = tm.core_type(item["data_type"])
            body = {"name": item["name"], "dataType": data_type, "displayType": display_type}
            if item.get("label"):
                body["label"] = item["label"]
            if item.get("description"):
                body["description"] = item["description"]
            return "POST", "/param", body
        if item["kind"] == "option":
            if not parent_id:
                raise ValueError("An option needs its field's Core ID first.")
            body = {"parameterID": parent_id, "label": item["name"], "value": item["name"]}
            if item.get("position") is not None:
                body["displayOrder"] = int(item["position"])
            return "POST", "/popt", body
        raise ValueError("Core forms are created by hand, and so is attaching a field to a form; this loader creates only custom fields and their options.")

    def describe(self, item, parent_id, refs=None):
        m, p, b = self.request_for(item, parent_id, refs)
        return {"system": "core", "action": "create " + item["kind"], "name": item["name"], "label": item["label"], "data_type": item["data_type"], "position": item["position"],
                "parent": parent_id, "temp_id": item["temp_id"], "request": "%s %s" % (m, p), "body": b}

    # ---- sending
    def create(self, item, parent_id, refs=None):
        method, path, body = self.request_for(item, parent_id, refs)
        try:
            reply = self.http.request(method, path, body)
        except HttpError as e:
            if e.status == 409 or _CONFLICT.search(str(e)):
                raise lr.NameConflict(str(e))
            raise
        new = reply_id(reply)
        if not new:
            raise HttpError("The create call worked but its reply holds no ID, so the item cannot be recorded.")
        return new

    def find_existing(self, item, parent_id, refs=None):
        """The IDs of items that already have this name (a field by name, an option by field and label), for adopting after an interrupted run."""
        if item["kind"] == "field":
            reply = self.http.request("GET", "/param/search", None, {"name": item["name"], "name_Mod": "eq", "fields": "ID,name"})
            want = item["name"]
        elif item["kind"] == "option" and parent_id:
            reply = self.http.request("GET", "/popt/search", None, {"parameterID": parent_id, "fields": "ID,label"})
            want = item["name"]
        else:
            return []
        rows = reply.get("data") if isinstance(reply, dict) else None
        key = "name" if item["kind"] == "field" else "label"
        return sorted(str(r["ID"]) for r in (rows or []) if isinstance(r, dict) and r.get(key) == want and r.get("ID"))
