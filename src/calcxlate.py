#!/usr/bin/env python3
"""Translate an Airtable formula into a Workfront Core calculated field expression (the calculated fields feature). Standard library only. Plain deterministic code over
the tree from calcparse.py: no network, no model, no randomness (a test checks the imports). Same formula and same names in, same expression out.

    translate(text, resolve) -> Translation     resolve(field_id) returns the Core field's API name, or None when the crosswalk has no target for it
    order_formulas(deps)     -> (order, cyclic)  dependency order of formula fields (a formula after the formulas it reads), and the ones caught in a loop

A Translation has: status (TRANSLATABLE, BLOCKED_MISSING_REFERENCE or BLOCKED_UNSUPPORTED_FUNCTION), reason (plain language), expression (None when blocked), references,
missing (field IDs with no target, and names written as names), functions (every distinct function name in the formula), unsupported (what has no verified equivalent), and
warnings (provisional choices the expression relies on). BLOCKED_DEPENDENCY is decided by the preflight, which knows the other formulas.

THE MAPPING IS A PLAN, NOT VERIFIED (owner, 22 November 2026): the prefix DE: and every Workfront function name below are provisional until tested in a sandbox. They live in
REF_PREFIX, FUNCTIONS and OPERATORS only, so a confirmed change is one line. Anything not listed is BLOCKED_UNSUPPORTED_FUNCTION and is never guessed.
"""
import calcparse as cp

STATUSES = ("TRANSLATABLE", "BLOCKED_MISSING_REFERENCE", "BLOCKED_UNSUPPORTED_FUNCTION", "BLOCKED_DEPENDENCY")
REF_PREFIX = "DE:"                       # a reference to a custom field is {DE:<API name>} (owner, 22 November 2026; provisional until a sandbox test)
SAFE_NAME = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")

# Airtable function -> (Workfront function, fewest arguments, most arguments or None for any number). Provisional: each name is confirmed in a sandbox before it is trusted.
FUNCTIONS = {
    "IF": ("IF", 3, 3), "SUM": ("SUM", 1, None), "AND": ("AND", 1, None), "OR": ("OR", 1, None), "TODAY": ("TODAY", 0, 0),
    "CONCATENATE": ("CONCAT", 1, None), "LEN": ("LEN", 1, 1), "ROUND": ("ROUND", 2, 2), "UPPER": ("UPPER", 1, 1),
}
# Airtable operator -> Workfront operator (comparisons and arithmetic). & becomes CONCAT, && becomes AND, || becomes OR (see below).
OPERATORS = {"+": "+", "-": "-", "*": "*", "/": "/", "=": "=", "!=": "!=", "<>": "!=", "<": "<", ">": ">", "<=": "<=", ">=": ">="}
BLANK_TEST = "ISBLANK"                   # X = BLANK() becomes ISBLANK(X)
# Functions kept for a person to decide, with why. Anything else not in FUNCTIONS is reported as having no verified equivalent.
MANUAL_REVIEW = {
    "DATETIME_FORMAT": "no verified Workfront equivalent takes a format string; each distinct format needs its own decision",
    "WEEKNUM": "week numbering may differ at year boundaries; edge dates need testing against Airtable",
}
DAY_UNITS = ("days", "d")                # the only DATETIME_DIFF units translated: Workfront DATEDIFF counts calendar days (owner, 22 November 2026)
BLANK_TEXT = '""'                        # two double quotes express blank (owner, 22 November 2026)
DATEDIFF_NOTE = "DATEDIFF is written with the dates swapped (Airtable counts first minus second); the sign Workfront gives is not verified"
WEEKDAYDIFF_NOTE = "WEEKDAYDIFF counts working days by the organization's schedule in Workfront, not Monday to Friday; inclusive and exclusive end dates and weekend starts are not verified"
BLANK_NOTE = "BLANK() is written as an empty text value (\"\")"
CONCAT_NOTE = "CONCAT with several arguments is assumed to accept any number of parts"


class Translation(object):
    def __init__(self):
        self.status, self.reason, self.expression = "TRANSLATABLE", "", None
        self.references, self.missing, self.functions, self.unsupported, self.warnings = [], [], [], [], []


def _parens(inner, own, parent, right):
    return "(" + inner + ")" if own < parent or (own == parent and right) else inner


class _Emitter(object):
    def __init__(self, resolve):
        self.resolve, self.missing, self.unsupported, self.warnings, self.refs = resolve, set(), [], set(), set()

    def bad(self, text):
        if text not in self.unsupported:
            self.unsupported.append(text)
        return "?"

    def emit(self, node, parent=0, right=False):
        kind = node[0]
        if kind == "num":
            return node[1]
        if kind == "str":
            if any(c in node[1] for c in '"\\\n\r'):
                return self.bad("a text value containing a quote, backslash or line break has no verified form")
            return '"' + node[1] + '"'
        if kind == "ref":
            return self.ref(node)
        if kind == "ident":
            return self.bad("%s used as a bare value has no verified Workfront equivalent" % node[1])
        if kind == "neg":
            return "-" + self.emit(node[1], cp.UNARY_PREC)
        if kind == "call":
            return self.call(node)
        return self.binary(node, parent, right)

    def ref(self, node):
        if node[2] == "name":
            self.missing.add(node[1])
            return "?"
        self.refs.add(node[1])
        name = self.resolve(node[1])
        if not name:
            self.missing.add(node[1])
            return "?"
        if not set(name) <= SAFE_NAME:
            self.warnings.add("the field name %s has a character other than a letter, digit or underscore; how Workfront reads it inside {%s...} is not verified" % (name, REF_PREFIX))
        return "{" + REF_PREFIX + name + "}"

    def is_blank(self, node):
        return node[0] == "call" and node[1] == "BLANK" and not node[2]

    def binary(self, node, parent, right):
        op, l, r = node[1], node[2], node[3]
        if op == "&":
            parts = self.flatten(node, "&")
            if len(parts) > 2:
                self.warnings.add(CONCAT_NOTE)
            return "CONCAT(" + ",".join(self.emit(p) for p in parts) + ")"
        if op in ("&&", "||"):
            fn = "AND" if op == "&&" else "OR"
            return fn + "(" + ",".join(self.emit(p) for p in self.flatten(node, op)) + ")"
        if op in ("=", "!=", "<>") and (self.is_blank(l) or self.is_blank(r)):
            other = r if self.is_blank(l) else l
            test = BLANK_TEST + "(" + self.emit(other) + ")"
            if op == "=":
                return test
            self.warnings.add(BLANK_NOTE)
            return self.emit(other, 3) + "!=" + BLANK_TEXT
        if op not in OPERATORS:
            return self.bad("the operator %s has no verified Workfront equivalent" % op)
        p = cp.PREC[op]
        text = self.emit(l, p, False) + OPERATORS[op] + self.emit(r, p, True)
        return _parens(text, p, parent, right)

    def flatten(self, node, op):
        if node[0] == "bin" and node[1] == op:
            return self.flatten(node[2], op) + self.flatten(node[3], op)
        return [node]

    def date_diff(self, args):
        if len(args) != 3 or args[2][0] != "str":
            return self.bad("DATETIME_DIFF is translated only with a typed unit")
        if args[2][1].lower() not in DAY_UNITS:
            return self.bad("DATETIME_DIFF in the unit '%s' is not translated: only days are (DATEDIFF counts calendar days)" % args[2][1])
        self.warnings.add(DATEDIFF_NOTE)
        return "DATEDIFF(" + self.emit(args[1]) + "," + self.emit(args[0]) + ")"

    def call(self, node):
        name, args = node[1], node[2]
        if name == "BLANK":
            if args:
                return self.bad("BLANK with arguments has no verified Workfront form")
            self.warnings.add(BLANK_NOTE)
            return BLANK_TEXT
        if name == "DATETIME_DIFF":
            return self.date_diff(args)
        if name == "WORKDAY_DIFF":
            if len(args) != 2:
                return self.bad("WORKDAY_DIFF with %d arguments is not translated: WEEKDAYDIFF takes two dates and does not consider holidays" % len(args))
            self.warnings.add(WEEKDAYDIFF_NOTE)
            return "WEEKDAYDIFF(" + ",".join(self.emit(a) for a in args) + ")"
        spec = FUNCTIONS.get(name)
        if spec is None:
            why = MANUAL_REVIEW.get(name)
            return self.bad("%s is not translated: %s" % (name, why) if why else "%s has no verified Workfront equivalent" % name)
        target, low, high = spec
        if len(args) < low or (high is not None and len(args) > high):
            want = "%d" % low if low == high else ("at least %d" % low if high is None else "%d to %d" % (low, high))
            return self.bad("%s with %d argument%s: the verified form takes %s" % (name, len(args), "" if len(args) == 1 else "s", want))
        if name == "CONCATENATE" and len(args) > 2:
            self.warnings.add(CONCAT_NOTE)
        return target + "(" + ",".join(self.emit(a) for a in args) + ")"


def translate(text, resolve):
    out = Translation()
    try:
        tree = cp.parse(text or "")
    except cp.ParseError as e:
        out.status, out.reason = "BLOCKED_UNSUPPORTED_FUNCTION", "The formula could not be read: %s." % e
        return out
    out.functions = sorted({n[1] for n in cp.walk(tree) if n[0] == "call"})
    em = _Emitter(resolve)
    text_out = em.emit(tree)
    out.references, out.missing, out.unsupported = sorted(em.refs), sorted(em.missing), em.unsupported
    out.warnings = sorted(em.warnings)
    if em.unsupported:
        out.status = "BLOCKED_UNSUPPORTED_FUNCTION"
        out.reason = "; ".join(em.unsupported) + "."
        if em.missing:
            out.reason += " Also missing a target field for: %s." % ", ".join(sorted(em.missing))
    elif em.missing:
        out.status = "BLOCKED_MISSING_REFERENCE"
        out.reason = "No target field in the crosswalk (or the reference is a name, not an ID) for: %s." % ", ".join(sorted(em.missing))
    else:
        out.expression = text_out
    return out


def order_formulas(deps):
    """deps: {formula field ID: iterable of the formula field IDs it reads}. Returns (order, cyclic): the IDs in an order where each comes after what it reads, ties broken by
    sorted ID (so the same input always gives the same order), and the sorted IDs caught in a loop or reading one (they have no valid place in the order). IDs read but not
    keys of deps are not formulas and are ignored."""
    ids = set(deps)
    need = {k: {d for d in deps[k] if d in ids} for k in ids}
    order, done = [], set()
    while True:
        ready = sorted(k for k in ids - done if need[k] <= done)
        if not ready:
            break
        for k in ready:
            order.append(k)
            done.add(k)
    return order, sorted(ids - done)
