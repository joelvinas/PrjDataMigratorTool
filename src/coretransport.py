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
            data_type, display_type, _verified = tm.core_pair(item["data_type"], item.get("display_type"))
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
        return {"system": "core", "action": "create " + item["kind"], "name": item["name"], "label": item["label"], "data_type": item["data_type"], "display_type": item.get("display_type"), "position": item["position"],
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

    # ---- Core objects (UNVERIFIED routes and parent property names: see docs/LOADER_PLAN.md)
    OBJECT_ROUTE = {"PROJ": "/proj", "OPTASK": "/optask", "TASK": "/task"}
    PARENT_PROPERTY = {("PROJ", "PRGM"): "programID", ("PROJ", "PORT"): "portfolioID", ("OPTASK", "PROJ"): "projectID", ("TASK", "PROJ"): "projectID", ("TASK", "TASK"): "parentID"}
    MAX_NAME = 255

    def object_request(self, object_code, name, parent_code, parent_id):
        """(method, path, body) that creates one Core object under its decided parent. Raises ValueError with a plain message."""
        route = self.OBJECT_ROUTE.get(object_code)
        if route is None:
            raise ValueError("Core objects of type %r are not created by this loader." % str(object_code))
        name = str(name or "").strip()
        if not name:
            raise ValueError("A Core object needs a name.")
        if len(name) > self.MAX_NAME:
            raise ValueError("The name is %d characters and a Core object name is limited to %d here; it is never cut." % (len(name), self.MAX_NAME))
        body = {"name": name}
        if parent_code is not None or parent_id is not None:
            prop = self.PARENT_PROPERTY.get((object_code, parent_code))
            if prop is None or not parent_id:
                raise ValueError("A %s cannot go under a parent of type %r." % (object_code, str(parent_code)))
            body[prop] = parent_id
        return "POST", route, body

    def create_object(self, object_code, name, parent_code, parent_id):
        """Create one project, request (OPTASK) or task under its parent and return its new ID."""
        method, path, body = self.object_request(object_code, name, parent_code, parent_id)
        reply = self.http.request(method, path, body)
        new = reply_id(reply)
        if not new:
            raise HttpError("The create call worked but its reply holds no ID, so the object cannot be recorded.")
        return new

    # ---- documents (UNVERIFIED: the upload route, the field name and the document properties; see docs/LOADER_PLAN.md)
    DOC_OBJECT = {"PROJ": "PROJ", "OPTASK": "OPTASK", "TASK": "TASK"}

    def upload_file(self, file_path, filename, content_type="application/octet-stream"):
        """Upload one file and return the handle Core gives it (a second call, create_document, turns the handle into a document)."""
        reply = self.http.upload("/upload", "uploadedFile", filename, file_path, content_type)
        d = reply.get("data", reply) if isinstance(reply, dict) else None
        handle = d.get("handle") if isinstance(d, dict) else None
        if not handle:
            raise HttpError("The upload worked but its reply holds no file handle, so the document cannot be created.")
        return str(handle)

    def document_request(self, name, handle, object_code, object_id, file_name):
        if object_code not in self.DOC_OBJECT:
            raise ValueError("Documents cannot be attached to an object of type %r." % str(object_code))
        if not (name and handle and object_id):
            raise ValueError("A document needs a name, an uploaded file handle and the object it belongs to.")
        return "POST", "/docu", {"name": name, "handle": handle, "docObjCode": object_code, "objID": object_id, "currentVersion": {"version": "v1.0", "fileName": file_name or name}}

    def create_document(self, name, handle, object_code, object_id, file_name=None):
        """Create the document on its Core object from an uploaded handle and return the document's ID."""
        method, path, body = self.document_request(name, handle, object_code, object_id, file_name)
        new = reply_id(self.http.request(method, path, body))
        if not new:
            raise HttpError("The document call worked but its reply holds no ID, so it cannot be recorded.")
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
