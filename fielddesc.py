#!/usr/bin/env python3
"""Field descriptions in the lineage syntax. Standard library only. Pure functions: no database, no network, no randomness.

The syntax:

    Source: Airtable. Origins: Orders.Amount; Customers.Name.

"Source" says where the data came from (Airtable, or Migration for fields the migration itself creates). "Origins" lists the original Airtable
Table.Field names the data comes from, so a client can trace any new field back to where it was. A name that contains a period, a semicolon or
a double quote is wrapped in double quotes (inner quotes doubled) so the list can always be read back. An optional free-text part may follow
the origins, for example "Airtable description: ...".

A description is generated, shown, and may be hand-edited and saved. parse_lineage() reads a description back and validate_description()
checks it, so a stricter validator (including a length limit once one is confirmed) can be added without changing the generator.
"""
import json

import emojitext

# UNCONFIRMED. Adobe's documentation gives no length limit for field descriptions, and one is on the sandbox test plan. Leave None (no length check)
# until it is confirmed, then set it here (or pass max_len to validate_description).
DESCRIPTION_MAX_LEN = None
_NEEDS_QUOTES = '.;"'
_DERIVED = ("multipleLookupValues", "rollup", "count", "formula")


def _one_line(text):
    """Control characters (tabs, new lines and the rest) become a single space."""
    return "".join(" " if ord(c) < 32 or ord(c) == 127 else c for c in str(text))


def quote_part(name):
    """A table or field name as it appears in the Origins list: plain when it can be read back as is, otherwise quoted."""
    s = _one_line(name)
    if s == "" or s != s.strip() or any(c in s for c in _NEEDS_QUOTES):
        return '"' + s.replace('"', '""') + '"'
    return s


def format_lineage(source, origins, rest=None):
    """'Source: <source>. Origins: T.F; T.F.' plus an optional free-text part. origins: [(table, field), ...]."""
    if not source or any(c in source for c in _NEEDS_QUOTES):
        raise ValueError("The source must be a short word or phrase without a period, semicolon or quote.")
    parts = "; ".join(quote_part(t) + "." + quote_part(f) for t, f in origins) or "unknown"
    text = "Source: %s. Origins: %s." % (source, parts)
    rest = " ".join(_one_line(rest).split()) if rest else ""
    return text + (" " + rest if rest else "")


def parse_lineage(text):
    """Read a description back. Returns {source, origins: [(table, field)], rest} or None when it is not in the lineage syntax."""
    text = str(text or "")
    n = len(text)
    if not text.startswith("Source: "):
        return None
    dot = text.find(". Origins: ")
    if dot < 0:
        return None
    source = text[len("Source: "):dot]
    if not source or any(c in source for c in _NEEDS_QUOTES):
        return None
    i = dot + len(". Origins: ")
    if text[i:i + 8] == "unknown.":
        return {"source": source, "origins": [], "rest": text[i + 8:].strip()}

    def part(i):
        if i < n and text[i] == '"':
            j, buf = i + 1, []
            while j < n:
                if text[j] == '"':
                    if j + 1 < n and text[j + 1] == '"':
                        buf.append('"'); j += 2; continue
                    return "".join(buf), j + 1
                buf.append(text[j]); j += 1
            return None
        j = i
        while j < n and text[j] not in _NEEDS_QUOTES:
            j += 1
        return (text[i:j], j) if j > i else None

    origins = []
    while True:
        a = part(i)
        if a is None or a[1] >= n or text[a[1]] != ".":
            return None
        b = part(a[1] + 1)
        if b is None:
            return None
        origins.append((a[0], b[0]))
        i = b[1]
        if text.startswith("; ", i):
            i += 2
            continue
        if i < n and text[i] == ".":
            i += 1
            break
        return None
    rest = text[i:]
    if rest and not rest.startswith(" "):
        return None
    return {"source": source, "origins": origins, "rest": rest.strip()}


def _no_emoji(text, mode):
    """Remove (or spell out) any emoji in a name or text; text without an emoji is returned unchanged."""
    if not emojitext.has_emoji(text):
        return text
    return " ".join(emojitext.replace_emoji(text, mode).split())


def field_origins(f, fields_by_id, emoji_mode="remove"):
    """The original (table, field) names this field's data comes from. A plain field: itself. A lookup or rollup: the field it reads in the linked
    table, then the link field it reads through. A count: the link field. A formula: the fields it references. Anything that cannot be resolved
    falls back to the field itself, so every field has at least one origin."""
    own = [(_no_emoji(f["table_name"], emoji_mode), _no_emoji(f["field_name"], emoji_mode))]

    def ref(fid):
        g = fields_by_id.get(fid) if fid else None
        return (_no_emoji(g["table_name"], emoji_mode), _no_emoji(g["field_name"], emoji_mode)) if g else None

    out = []
    t = f.get("data_type")
    if t in ("multipleLookupValues", "rollup", "count"):
        for x in (ref(f.get("source_field_id")) if t != "count" else None, ref(f.get("source_link_field_id"))):
            if x and x not in out:
                out.append(x)
    if t in ("formula", "rollup") and f.get("referenced_field_ids"):
        try:
            ids = json.loads(f["referenced_field_ids"])
        except (TypeError, ValueError):
            ids = []
        for fid in ids if isinstance(ids, list) else []:
            x = ref(fid)
            if x and x not in out:
                out.append(x)
    return out or own


def generate_description(f, fields_by_id, include_airtable_description=False, emoji_mode="remove"):
    """The generated description for one field (a row from airtable_fields). Deterministic. Emoji in the names (and in Airtable's own description)
    are removed, or spelled out as their word when emoji_mode is 'word'."""
    rest = None
    if include_airtable_description and (f.get("field_description") or "").strip():
        rest = "Airtable description: " + _no_emoji(f["field_description"], emoji_mode)
    return format_lineage("Airtable", field_origins(f, fields_by_id, emoji_mode), rest)


def validate_description(text, max_len=None):
    """Problems found in a description, as [{rule, severity, message}] (empty when it is fine). severity is 'error' or 'warn'. A description that
    is not in the lineage syntax is a warning, because a hand edit may be free text on purpose."""
    problems = []
    text = str(text or "")
    if not text.strip():
        return [{"rule": "EMPTY", "severity": "error", "message": "The description is empty."}]
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        problems.append({"rule": "CONTROL_CHARS", "severity": "error", "message": "The description contains a tab, new line or other control character."})
    if any(ord(c) > 126 for c in text):
        problems.append({"rule": "NON_ASCII", "severity": "warn",
                         "message": "The description contains non-ASCII characters (often from an original name); data exported to CSV without the right encoding corrupts them."})
    if parse_lineage(text) is None:
        problems.append({"rule": "NOT_LINEAGE", "severity": "warn", "message": "The description is not in the lineage syntax (Source: ... Origins: Table.Field; ...)."})
    limit = max_len if max_len is not None else DESCRIPTION_MAX_LEN
    if limit is not None and len(text) > limit:
        problems.append({"rule": "TOO_LONG", "severity": "error", "message": "The description is %d characters; the limit is %d." % (len(text), limit)})
    return problems
