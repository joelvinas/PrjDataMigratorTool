#!/usr/bin/env python3
"""Compare the values of two or more fields in the same table, to test whether duplicate fields really hold the same data. Standard library only.

Pure and deterministic. The input is the records of one table (the saved record file: [{id, fields: {field_id: value}}]); the output is COUNTS and a few
record IDs only. No value is ever returned, logged or stored: the caller loads the file, calls compare_group(), and throws the records away.

For each record and pair of fields the two cells fall into exactly one bucket:
  both_empty     neither field has a value
  only_a/only_b  exactly one of them has a value
  agree          both have a value and the values are identical
  reordered      both are lists holding the same items in a different order
  loose          the same apart from case, surrounding spaces or number-versus-text (for example 5 and "5")
  conflict       both have a value and they differ
"Empty" follows the profiler: missing, null, an empty string or an empty list. A checkbox is never empty (an unchecked box is false).
"""
import json
from itertools import combinations

EXAMPLE_CAP = 5            # record IDs shown per bucket, so a conflict can be found in Airtable
MAX_FIELDS = 8             # fields compared at once (pairs grow quickly)


def is_empty(v, data_type=None):
    if data_type == "checkbox":
        return False
    return v is None or v == "" or v == [] or v == {}


def _norm_item(x):
    """An attachment is compared by file name and size, because the same file in another field gets another attachment ID."""
    if isinstance(x, dict) and "filename" in x:
        return {"filename": x.get("filename"), "size": x.get("size")}
    return x


def _canon(v):
    if isinstance(v, list):
        v = [_norm_item(i) for i in v]
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _loose(v):
    if isinstance(v, list):
        return tuple(sorted(str(_loose(i)) for i in [_norm_item(x) for x in v]))
    if isinstance(v, dict):
        return _canon(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip().casefold()


def classify(a, b, type_a=None, type_b=None):
    ea, eb = is_empty(a, type_a), is_empty(b, type_b)
    if ea and eb:
        return "both_empty"
    if ea:
        return "only_b"
    if eb:
        return "only_a"
    if type_a == "checkbox" and a is None:
        a = False
    if type_b == "checkbox" and b is None:
        b = False
    if _canon(a) == _canon(b):
        return "agree"
    if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (a, b)) and a == b:
        return "agree"                                  # 5 and 5.0 are the same number
    if isinstance(a, list) and isinstance(b, list) and sorted(_canon(i) for i in a) == sorted(_canon(i) for i in b):
        return "reordered"
    if _loose(a) == _loose(b):
        return "loose"
    return "conflict"


BUCKETS = ("both_empty", "only_a", "only_b", "agree", "reordered", "loose", "conflict")


def verdict(c):
    """(code, plain-language text) for one pair's counts."""
    both = c["agree"] + c["reordered"] + c["loose"] + c["conflict"]
    if c["total"] == c["both_empty"]:
        return "no_data", "Neither field has a value in any saved record."
    if both == 0:
        return "complementary", "Never filled together: each record has a value in at most one of the two, so they can be merged without losing anything."
    if c["conflict"] == 0 and c["reordered"] == 0 and c["loose"] == 0 and c["only_a"] == 0 and c["only_b"] == 0:
        return "identical", "Identical in every record where either has a value."
    if c["conflict"] == 0:
        extras = []
        if c["only_a"] or c["only_b"]:
            extras.append("one has values the other lacks")
        if c["reordered"] or c["loose"]:
            extras.append("some values differ only in order, case, spacing or number-versus-text")
        return "agree", "No conflicts: they agree wherever both are filled" + (" (%s)." % "; ".join(extras) if extras else ".")
    return "conflict", "%d record(s) have different values in the two fields." % c["conflict"]


def compare_pair(records, field_a, field_b, type_a=None, type_b=None):
    """Counts for one pair. field_a and field_b are field IDs. Returns {counts, examples, verdict, verdict_text}; no values."""
    counts = {k: 0 for k in BUCKETS}
    counts["total"] = 0
    examples = {k: [] for k in ("only_a", "only_b", "reordered", "loose", "conflict")}
    for r in records:
        f = r.get("fields") or {}
        k = classify(f.get(field_a), f.get(field_b), type_a, type_b)
        counts[k] += 1
        counts["total"] += 1
        if k in examples and len(examples[k]) < EXAMPLE_CAP:
            examples[k].append(r.get("id"))
    counts["a_filled"] = counts["only_a"] + counts["agree"] + counts["reordered"] + counts["loose"] + counts["conflict"]
    counts["b_filled"] = counts["only_b"] + counts["agree"] + counts["reordered"] + counts["loose"] + counts["conflict"]
    code, text = verdict(counts)
    return {"counts": counts, "examples": {k: v for k, v in examples.items() if v}, "verdict": code, "verdict_text": text}


def compare_group(records, fields):
    """fields: [{field_id, field_name, data_type}] (2 to MAX_FIELDS, all in the table the records came from). Every pair is compared, and a histogram
    says how many of the fields are filled in each record. Returns {pairs: [...], filled_together: {n_fields_filled: records}}."""
    if not 2 <= len(fields) <= MAX_FIELDS:
        raise ValueError("Compare between 2 and %d fields." % MAX_FIELDS)
    pairs = []
    for a, b in combinations(fields, 2):
        res = compare_pair(records, a["field_id"], b["field_id"], a.get("data_type"), b.get("data_type"))
        res.update({"a": {"field_id": a["field_id"], "field_name": a["field_name"], "data_type": a.get("data_type")},
                    "b": {"field_id": b["field_id"], "field_name": b["field_name"], "data_type": b.get("data_type")},
                    "types_differ": (a.get("data_type") != b.get("data_type"))})
        pairs.append(res)
    hist = {}
    for r in records:
        f = r.get("fields") or {}
        n = sum(1 for x in fields if not is_empty(f.get(x["field_id"]), x.get("data_type")))
        hist[n] = hist.get(n, 0) + 1
    return {"pairs": pairs, "filled_together": dict(sorted(hist.items()))}
