#!/usr/bin/env python3
"""Target type mapping: the data type text in the plan (from the names import, for example "Single select") to what Workfront Planning and Workfront Core ask for.
Standard library only; pure functions.

PLANNING (v2 reference): a field's `type` is one of text, long-text, number, percentage, currency, date, single-select, multi-select, boolean, reference, user, formula,
created-by, created-at, updated-by, updated-at, record-id. Only the types a load can create are mapped here; formula, lookup and document fields are not creatable and
stay on hold in the plan.

CORE (owner-verified 17 November 2026): POST /param needs only name and dataType. The displayTypes TEXT (shown as text) and TXTA (shown as a text area) work with any
dataType. dataType is one of DATE, DTTM, NMBR, CURC, TEXT, RICH. A RICH field cannot be created through the API: it is built by hand in the custom form viewer, so it is
refused here with that message. The select, multi-select and checkbox displayTypes (SLCT, MULT, CHCK) are in the schema but NOT yet verified: they are marked so, and the
first sandbox run must confirm them.

The label match ignores case, hyphens, underscores and extra spaces, and a few common spellings are accepted. A label this module does not know is never guessed: the
caller gets None and reports it, and the item fails with a plain message.
"""
import re

_ALIASES = {
    "text": ("text", "single line text", "singlelinetext", "short text", "string", "single line", "line text"),
    "long-text": ("long text", "longtext", "multiline text", "multi line text", "multilinetext", "text area", "textarea", "paragraph"),
    "rich-text": ("rich text", "richtext", "formatted text", "html"),
    "number": ("number", "integer", "decimal", "numeric", "float"),
    "percentage": ("percent", "percentage"),
    "currency": ("currency", "money"),
    "date": ("date",),
    "datetime": ("date time", "datetime", "date and time", "timestamp"),
    "single-select": ("single select", "singleselect", "select", "dropdown", "drop down", "single choice", "choice"),
    "multi-select": ("multi select", "multiselect", "multiple select", "multiple selects", "multiple choice", "multiple choices", "tags"),
    "boolean": ("boolean", "checkbox", "check box", "yes no", "true false"),
    "reference": ("reference", "link", "connection", "linked record", "linked records", "link to another record", "link to another records", "connected record", "connected records"),
    "user": ("user", "users", "person", "people", "collaborator", "collaborators"),
}
_LOOKUP = {a: k for k, v in _ALIASES.items() for a in v}
SELECT_KINDS = ("single-select", "multi-select")


def kind(label):
    """The canonical kind for a data type text, or None when it is not recognised."""
    t = re.sub(r"[\s_\-]+", " ", str(label or "").strip().lower())
    return _LOOKUP.get(t)


# ------------------------------------------------------------------ Planning
PLANNING_TYPE = {"text": "text", "long-text": "long-text", "number": "number", "percentage": "percentage", "currency": "currency", "date": "date", "datetime": "date",
                 "single-select": "single-select", "multi-select": "multi-select", "boolean": "boolean", "reference": "reference", "user": "user"}


def planning_type(label):
    """(v2 type, kind) or (None, None). Rich text has no Planning equivalent in v2: it is created as long text."""
    k = kind(label)
    if k == "rich-text":
        return "long-text", k
    return (PLANNING_TYPE.get(k), k) if k else (None, None)


# ------------------------------------------------------------------ Core
# kind -> (dataType, displayType, verified)
CORE_TYPE = {
    "text": ("TEXT", "TEXT", True), "long-text": ("TEXT", "TXTA", True),
    "number": ("NMBR", "TEXT", True), "percentage": ("NMBR", "TEXT", True), "currency": ("CURC", "TEXT", True),
    "date": ("DATE", "TEXT", True), "datetime": ("DTTM", "TEXT", True),
    "single-select": ("TEXT", "SLCT", False), "multi-select": ("TEXT", "MULT", False), "boolean": ("TEXT", "CHCK", False),
}
CORE_REFUSED = {
    "rich-text": "A rich text field cannot be created through the Workfront Core API. Build it by hand in the custom form viewer.",
    "reference": "A link has no Core custom field type; it is a Planning connection and is not created in Core.",
    "user": "A user field is not created as a Core custom field by this loader.",
}


def core_type(label):
    """(dataType, displayType, verified) for a Core custom field, or raises ValueError with a plain message (unknown or not creatable)."""
    k = kind(label)
    if k is None:
        raise ValueError("The target data type %r is not one this loader knows; check the new_dataType value for this field." % str(label or ""))
    if k in CORE_REFUSED:
        raise ValueError(CORE_REFUSED[k])
    return CORE_TYPE[k]
