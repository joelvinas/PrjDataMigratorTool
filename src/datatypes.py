#!/usr/bin/env python3
"""The data types a field may be given, per system. Standard library only; pure and deterministic.

PLANNING: the 17 values in Adobe's Workfront Planning API documentation. A stored Planning type must be exactly one of them.
CORE: the API needs TWO values for a custom field, so they are kept apart: the dataType and the displayType (owner decision, 17 November 2026).
  dataType     DATE, DTTM, NMBR, CURC, TEXT, RICH   (owner-verified 17 November 2026)
  displayType  TEXT, TXTA work with any dataType (verified); SLCT, MULT, CHCK are in the schema but NOT yet verified against a sandbox
A value that is not on its list is never kept and never guessed: it is cleared by a person (datatypes_store.clear_invalid) and fixed.
"""

PLANNING_TYPES = ("text", "long-text", "number", "percentage", "currency", "date", "single-select", "multi-select", "boolean", "reference", "user", "formula",
                  "created-by", "created-at", "updated-by", "updated-at", "record-id")
CORE_DATA_TYPES = ("CURC", "DATE", "DTTM", "NMBR", "RICH", "TEXT")
CORE_DISPLAY_TYPES = ("CHCK", "MULT", "SLCT", "TEXT", "TXTA")
CORE_UNVERIFIED_DISPLAY = frozenset({"SLCT", "MULT", "CHCK"})
SYSTEMS = ("planning", "core")


def planning_type(value):
    """The canonical Planning type for a value (case and surrounding spaces are ignored), or None when it is not exactly one of the 17.
    'Single select' is NOT a Planning type; 'single-select' is."""
    v = str(value or "").strip().lower()
    return v if v in PLANNING_TYPES else None


def core_pair(data_type, display_type):
    """(dataType, displayType) in upper case when BOTH are on their lists, else None."""
    d, p = str(data_type or "").strip().upper(), str(display_type or "").strip().upper()
    return (d, p) if d in CORE_DATA_TYPES and p in CORE_DISPLAY_TYPES else None


def planning_problem(value):
    """A plain sentence when the value is not a Planning type, else None."""
    if planning_type(value):
        return None
    return "%r is not a Workfront Planning data type; it must be one of: %s." % (str(value), ", ".join(PLANNING_TYPES))


def core_problem(data_type, display_type):
    """A plain sentence when the pair is not a valid Core dataType and displayType, else None."""
    if core_pair(data_type, display_type):
        return None
    bad = []
    if str(data_type or "").strip().upper() not in CORE_DATA_TYPES:
        bad.append("dataType %r must be one of: %s" % (str(data_type or ""), ", ".join(CORE_DATA_TYPES)))
    if str(display_type or "").strip().upper() not in CORE_DISPLAY_TYPES:
        bad.append("displayType %r must be one of: %s" % (str(display_type or ""), ", ".join(CORE_DISPLAY_TYPES)))
    return "Not a Workfront Core data type: " + "; ".join(bad) + "."


def state(system, type_, display_type=None):
    """'unset' (nothing stored), 'set' (valid and stored in canonical form) or 'invalid' (something is stored that is not on the list)."""
    if system == "planning":
        if not type_:
            return "unset"
        return "set" if planning_type(type_) == type_ else "invalid"
    if not type_ and not display_type:
        return "unset"
    return "set" if core_pair(type_, display_type) == (type_, display_type) else "invalid"
