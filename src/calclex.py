#!/usr/bin/env python3
"""Lexer for Airtable formula text (the calculated fields feature). Standard library only. Plain, deterministic string scanning: it splits a formula into numbers,
strings, field references, function names, operators and brackets. It makes no network call and uses no model, no randomness and no clock (a test checks the imports).

    lex(text)              -> list of Token                    raises LexError (with a position) on a character it does not know
    normalize(text)        -> str                              the formula with line breaks and repeated spaces closed up, strings left as they are
    field_references(toks) -> sorted unique list of ids        {fldXXX} references (kind "id"); {Name} references (kind "name") are listed apart by name_references()
    function_names(toks)   -> sorted unique list of UPPER names
    inventory(formulas)    -> [(NAME, count, [formula ids])]   across many formulas, sorted by name; the unsupported list is built from data, not memory

Airtable's metadata API returns a field reference as {fldXXXX} (the field ID in curly braces); the formula text may contain literal line breaks used only for layout. A
backslash before a double quote is JSON escaping, not part of the formula: when the text has no bare double quote at all, the escapes are removed first (see unescape_json).
Only formula text moves through here (metadata); no record value.
"""
import re

NUMBER, STRING, FIELD, FUNC, IDENT, OP, LPAREN, RPAREN, COMMA = "NUMBER", "STRING", "FIELD", "FUNC", "IDENT", "OP", "LPAREN", "RPAREN", "COMMA"

_NUM = re.compile(r"\d+(?:\.\d+)?|\.\d+")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FIELD_ID = re.compile(r"^fld[A-Za-z0-9]+$")
TWO_CHAR_OPS = ("<=", ">=", "!=", "<>", "&&", "||")
ONE_CHAR_OPS = "+-*/&=<>"
SPACE = " \t\r\n\f\v "


class LexError(ValueError):
    def __init__(self, message, pos):
        ValueError.__init__(self, "%s (at character %d)" % (message, pos))
        self.pos = pos


class Token(object):
    __slots__ = ("kind", "value", "pos", "end", "ref_kind")

    def __init__(self, kind, value, pos, end, ref_kind=""):
        self.kind, self.value, self.pos, self.end, self.ref_kind = kind, value, pos, end, ref_kind

    def __repr__(self):
        return "Token(%s, %r)" % (self.kind, self.value)

    def __eq__(self, other):
        return isinstance(other, Token) and (self.kind, self.value) == (other.kind, other.value)

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash((self.kind, self.value))


def unescape_json(text):
    """Remove JSON backslash escapes before double quotes, but only when the text has no bare double quote (a quote not preceded by a backslash). Mixed text is left alone,
    so a real escaped quote inside a string literal is never damaged."""
    if '\\"' not in text:
        return text
    if re.search(r'(?<!\\)"', text):
        return text
    return text.replace('\\"', '"')


def _read_string(text, i):
    """Read a quoted string starting at text[i]; returns (value, next index). A backslash escapes the next character (quote or backslash)."""
    q, j, out = text[i], i + 1, []
    while j < len(text):
        c = text[j]
        if c == "\\" and j + 1 < len(text):
            out.append(text[j + 1])
            j += 2
            continue
        if c == q:
            return "".join(out), j + 1
        out.append(c)
        j += 1
    raise LexError("a quoted string is never closed", i)


def lex(text):
    if not isinstance(text, str):
        raise LexError("the formula is not text", 0)
    text = unescape_json(text)
    toks, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in SPACE:
            i += 1
        elif c in "\"'":
            val, j = _read_string(text, i)
            toks.append(Token(STRING, val, i, j))
            i = j
        elif c == "{":
            j = text.find("}", i + 1)
            if j < 0:
                raise LexError("a field reference is never closed", i)
            name = text[i + 1:j].strip()
            if not name:
                raise LexError("an empty field reference", i)
            toks.append(Token(FIELD, name, i, j + 1, "id" if _FIELD_ID.match(name) else "name"))
            i = j + 1
        elif c.isdigit() or (c == "." and i + 1 < n and text[i + 1].isdigit()):
            m = _NUM.match(text, i)
            toks.append(Token(NUMBER, m.group(0), i, m.end()))
            i = m.end()
        elif c.isalpha() or c == "_":
            m = _IDENT.match(text, i)
            if m is None:
                raise LexError("unexpected character", i)
            k = m.end()
            while k < n and text[k] in SPACE:
                k += 1
            if k < n and text[k] == "(":
                toks.append(Token(FUNC, m.group(0).upper(), i, m.end()))
            else:
                toks.append(Token(IDENT, m.group(0).upper(), i, m.end()))
            i = m.end()
        elif text[i:i + 2] in TWO_CHAR_OPS:
            toks.append(Token(OP, text[i:i + 2], i, i + 2))
            i += 2
        elif c in ONE_CHAR_OPS:
            toks.append(Token(OP, c, i, i + 1))
            i += 1
        elif c == "(":
            toks.append(Token(LPAREN, c, i, i + 1))
            i += 1
        elif c == ")":
            toks.append(Token(RPAREN, c, i, i + 1))
            i += 1
        elif c == ",":
            toks.append(Token(COMMA, c, i, i + 1))
            i += 1
        else:
            raise LexError("unexpected character %r" % c if c.isprintable() else "unexpected control character", i)
    return toks


def normalize(text):
    """The formula with line breaks and runs of spaces outside strings and references closed up to one space, and the ends trimmed. Text inside quotes or braces is kept."""
    text = unescape_json(text)
    out, i, n, pending = [], 0, len(text), False
    while i < n:
        c = text[i]
        if c in SPACE:
            pending = True
            i += 1
            continue
        if pending and out:
            out.append(" ")
        pending = False
        if c in "\"'":
            _, j = _read_string(text, i)
        elif c == "{":
            j = text.find("}", i + 1)
            if j < 0:
                raise LexError("a field reference is never closed", i)
            j += 1
        else:
            j = i + 1
        out.append(text[i:j])
        i = j
    return "".join(out)


def field_references(tokens):
    """Sorted unique field IDs ({fldXXX}) the formula reads."""
    return sorted({t.value for t in tokens if t.kind == FIELD and t.ref_kind == "id"})


def name_references(tokens):
    """Sorted unique references written as a field NAME, not an ID. The crosswalk is keyed by ID, so a person must look at these."""
    return sorted({t.value for t in tokens if t.kind == FIELD and t.ref_kind == "name"})


def function_names(tokens):
    """Sorted unique upper-case function names."""
    return sorted({t.value for t in tokens if t.kind == FUNC})


def inventory(formulas):
    """formulas: iterable of (formula_id, text). Returns [(NAME, count of calls, sorted list of formula ids)] sorted by name. A formula that does not lex is skipped here;
    the preflight reports it on its own."""
    count, where = {}, {}
    for fid, text in formulas:
        try:
            toks = lex(text or "")
        except LexError:
            continue
        for t in toks:
            if t.kind == FUNC:
                count[t.value] = count.get(t.value, 0) + 1
                where.setdefault(t.value, set()).add(fid)
    return [(k, count[k], sorted(where[k])) for k in sorted(count)]
