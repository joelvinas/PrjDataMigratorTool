#!/usr/bin/env python3
"""Airtable formulas into Workfront PLANNING formula fields, for Git-R-Done. Standard library only. Plain deterministic code over the tree from calcparse.py: no network, no
model, no randomness. Same schema in, same expression and same advice out. It reads field and table metadata only (formula text, names, IDs, counts); no record value.

Three parts:
    translate(text, resolve)      an Airtable formula -> a Planning expression. A field is written {Display Name} (the name the field has in Planning, never its ID).
    return_type(result_type)      the Planning returnType for an Airtable result type, or None when there is no verified one.
    Catalog(...)                  the schema of one base, so a formula can be PRE-CHECKED: which of its inputs exist, which need ANOTHER table loaded first, and what order of tables
                                  gives the fewest left-over formulas (`advise`). The same Catalog tells a run which formulas to hold back.

Facts and choices (owner, 23 November 2026), kept in one place so a confirmed change is one line:
  * DATEDIFF(start, end) and WEEKDAYDIFF(start, end) return a positive number when end is after start. Airtable's DATETIME_DIFF(end, start, 'days') therefore becomes
    DATEDIFF(start, end) (the two dates swap), and WORKDAY_DIFF(start, end) keeps its order. ABS() is only used when the Airtable formula uses it.
  * ARRAYCONTAINS and every multi-select are NOT supported for now: a formula that reads a multi-select, or any array, is not sent (it falls back to a PRE- text field).
  * A boolean is used inside IF() conditions (ISBLANK, comparisons, && and ||). A formula whose RESULT is a checkbox is not sent: there is no verified boolean return type.
  * PROVISIONAL until tested in DEV: the function names marked in PROVISIONAL_FUNCTIONS, && for AND, the list of return types, and the date options of a DATE result.
"""
import calcparse as cp

TRANSLATABLE, BLOCKED = "TRANSLATABLE", "BLOCKED"
FUNCTIONS = {                      # Airtable function -> (Planning function, fewest arguments, most arguments or None for any number)
    "IF": ("IF", 3, 3), "SUM": ("SUM", 1, None), "ABS": ("ABS", 1, 1), "CONCATENATE": ("CONCAT", 1, None),
    "LEN": ("LEN", 1, 1), "ROUND": ("ROUND", 2, 2), "UPPER": ("UPPER", 1, 1), "TODAY": ("TODAY", 0, 0),
}
PROVISIONAL_FUNCTIONS = frozenset({"LEN", "ROUND", "UPPER", "TODAY"})     # named in Adobe's formula list but not yet seen working through the API
OPERATORS = {"+": "+", "-": "-", "*": "*", "/": "/", "=": "=", "!=": "!=", "<>": "!=", "<": "<", ">": ">", "<=": "<=", ">=": ">="}
DAY_UNITS = ("days", "d")
BLANK_TEXT = '""'
MAX_FORMULAS = 20                  # Planning allows 20 formula fields per record type (planlimits.MAX_FORMULA)
AND_NOTE = "AND is written with && (Adobe documents || for OR); && is not yet verified"
CONCAT_NOTE = "CONCAT with more than two parts is assumed to accept any number of parts"
DATEDIFF_NOTE = "DATEDIFF is written (start, end) with the dates swapped from Airtable's DATETIME_DIFF(end, start, 'days'); the result is expected to be positive"
BLANK_NOTE = "BLANK() is written as an empty text value (\"\")"
# Airtable formula result type -> Planning returnType. Types not listed (checkbox, arrays) have no verified return type and are not sent.
RETURN_TYPES = {"number": "NUMBER", "currency": "NUMBER", "duration": "NUMBER", "rating": "NUMBER", "percent": "PERCENTAGE",
                "singleLineText": "TEXT", "multilineText": "TEXT", "richText": "TEXT", "string": "TEXT", "text": "TEXT", "email": "TEXT", "url": "TEXT", "phoneNumber": "TEXT",
                "date": "DATE", "dateTime": "DATE"}
DATE_OPTIONS = {"dateFormat": "locale"}      # PROVISIONAL: the value Adobe's reference shows for a date field


def return_type(result_type):
    return RETURN_TYPES.get(str(result_type or ""))


def formula_options(expression, rtype):
    """The formulaOptions object of the create call."""
    out = {"formula": expression, "returnType": rtype}
    if rtype == "DATE":
        out["dateOptions"] = dict(DATE_OPTIONS)
    return out


class Translation(object):
    def __init__(self):
        self.status, self.reason, self.expression = TRANSLATABLE, "", None
        self.references, self.missing, self.unsupported, self.warnings = [], [], [], []


class _Emitter(object):
    def __init__(self, resolve):
        self.resolve, self.refs, self.missing, self.unsupported, self.warnings = resolve, set(), set(), [], set()

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
            return self.bad("%s used as a bare value has no verified Planning equivalent" % node[1])
        if kind == "neg":
            return "-" + self.emit(node[1], cp.UNARY_PREC)
        if kind == "call":
            return self.call(node)
        return self.binary(node, parent, right)

    def ref(self, node):
        key = node[1]
        self.refs.add(key)
        name = self.resolve(node[2], key)
        if not name:
            self.missing.add(key)
            return "?"
        if any(c in name for c in "{}"):
            return self.bad("the field name %s holds a brace, which cannot be written inside a reference" % name)
        return "{" + name + "}"

    def flatten(self, node, op):
        if node[0] == "bin" and node[1] == op:
            return self.flatten(node[2], op) + self.flatten(node[3], op)
        return [node]

    @staticmethod
    def is_blank(node):
        return node[0] == "call" and node[1] == "BLANK" and not node[2]

    def logic(self, op, parts):
        if op == "&&":
            self.warnings.add(AND_NOTE)
        return "(" + (" %s " % op).join(self.emit(p) for p in parts) + ")"

    def binary(self, node, parent, right):
        op, l, r = node[1], node[2], node[3]
        if op == "&":
            parts = self.flatten(node, "&")
            if len(parts) > 2:
                self.warnings.add(CONCAT_NOTE)
            return "CONCAT(" + ",".join(self.emit(p) for p in parts) + ")"
        if op in ("&&", "||"):
            return self.logic(op, self.flatten(node, op))
        if op in ("=", "!=", "<>") and (self.is_blank(l) or self.is_blank(r)):
            other = r if self.is_blank(l) else l
            if op == "=":
                return "ISBLANK(" + self.emit(other) + ")"
            self.warnings.add(BLANK_NOTE)
            return self.emit(other, 3) + "!=" + BLANK_TEXT
        if op not in OPERATORS:
            return self.bad("the operator %s has no verified Planning equivalent" % op)
        p = cp.PREC[op]
        text = self.emit(l, p, False) + OPERATORS[op] + self.emit(r, p, True)
        return "(" + text + ")" if p < parent or (p == parent and right) else text

    def call(self, node):
        name, args = node[1], node[2]
        if name == "BLANK":
            if args:
                return self.bad("BLANK with arguments has no verified Planning form")
            self.warnings.add(BLANK_NOTE)
            return BLANK_TEXT
        if name in ("AND", "OR"):
            if not args:
                return self.bad("%s with no arguments" % name)
            return self.logic("&&" if name == "AND" else "||", args)
        if name == "DATETIME_DIFF":
            if len(args) != 3 or args[2][0] != "str" or args[2][1].lower() not in DAY_UNITS:
                return self.bad("DATETIME_DIFF is translated only in days (DATEDIFF counts calendar days)")
            self.warnings.add(DATEDIFF_NOTE)
            return "DATEDIFF(" + self.emit(args[1]) + "," + self.emit(args[0]) + ")"
        if name == "WORKDAY_DIFF":
            if len(args) != 2:
                return self.bad("WORKDAY_DIFF with %d arguments is not translated: WEEKDAYDIFF takes two dates" % len(args))
            return "WEEKDAYDIFF(" + ",".join(self.emit(a) for a in args) + ")"
        spec = FUNCTIONS.get(name)
        if spec is None:
            return self.bad("%s has no verified Planning equivalent" % name)
        target, low, high = spec
        if len(args) < low or (high is not None and len(args) > high):
            return self.bad("%s with %d argument(s): the verified form takes %s" % (name, len(args), ("%d" % low) if low == high else ("at least %d" % low if high is None else "%d to %d" % (low, high))))
        if name in PROVISIONAL_FUNCTIONS:
            self.warnings.add("%s is assumed to exist under that name in Planning; not yet seen working" % target)
        if name == "CONCATENATE" and len(args) > 2:
            self.warnings.add(CONCAT_NOTE)
        return target + "(" + ",".join(self.emit(a) for a in args) + ")"


def translate(text, resolve):
    """resolve(kind, key) -> the field's Planning display name, or None. kind is "id" ({fldXXX}) or "name" ({Some Name}). A reference that cannot be resolved lands in `missing`."""
    out = Translation()
    try:
        tree = cp.parse(text or "")
    except cp.ParseError as e:
        out.status, out.reason = BLOCKED, "The formula could not be read: %s." % e
        return out
    em = _Emitter(resolve)
    written = em.emit(tree)
    out.references, out.missing, out.unsupported, out.warnings = sorted(em.refs), sorted(em.missing), em.unsupported, sorted(em.warnings)
    if em.unsupported:
        out.status, out.reason = BLOCKED, "; ".join(em.unsupported) + "."
    elif em.missing:
        out.status, out.reason = BLOCKED, "A field this formula reads is not available in Planning: %s." % ", ".join(sorted(em.missing))
    else:
        out.expression = written
    return out


def reference_ids(text):
    """Every {fldXXX} id and every {Name} the formula reads, as ([ids], [names]); both sorted. ([], []) when the formula cannot be read."""
    try:
        tree = cp.parse(text or "")
    except cp.ParseError:
        return [], []
    nodes = [n for n in cp.walk(tree) if n[0] == "ref"]
    return sorted({n[1] for n in nodes if n[2] == "id"}), sorted({n[1] for n in nodes if n[2] == "name"})


# ------------------------------------------------------------------ the schema of one base, for the pre-check and the hold-back
DERIVED = frozenset({"multipleLookupValues", "rollup", "count"})
TABLE_NODE = "tbl:"


class Catalog(object):
    """The fields of one base. `attempt(field) -> Planning type text` and the sets/predicates below come from emergency.py (they decide what a field becomes), passed in so this
    module never imports it. Everything is read from SQLite: the schema, the stored types and decisions, and (for `present`) what the ID map says is already in Planning."""

    def __init__(self, conn, base_id, attempt, array_types, skip_types, is_snapshot):
        self.base_id, self.attempt, self.array_types, self.skip_types, self.is_snapshot = base_id, attempt, set(array_types), dict(skip_types), is_snapshot
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        self.defaults = {}
        if "data_type_defaults" in have:
            self.defaults = {r[0]: r[1] for r in conn.execute("SELECT airtable_type, default_type FROM data_type_defaults WHERE system='planning'") if r[1]}
        stored = {}
        if "field_names" in have:
            stored = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT airtable_field_id, target_type, abandoned FROM field_names")}
        self.tables = {r[0]: r[1] for r in conn.execute("SELECT table_id, table_name FROM airtable_tables WHERE base_id=?", (base_id,))}
        self.f, self.order = {}, {}
        for r in conn.execute("SELECT * FROM airtable_fields WHERE base_id=? ORDER BY lower(field_name), field_id", (base_id,)):
            d = dict(r)
            st = stored.get(d["field_id"]) or (None, 0)
            d["stored_planning_type"], d["abandoned"] = st[0], bool(st[1])
            d["lookup_snapshot"] = bool(is_snapshot(d))
            self.f[d["field_id"]] = d
            self.tables.setdefault(d["table_id"], d["table_name"])
            self.order.setdefault(d["table_id"], []).append(d["field_id"])
        self._names, self._trimmed = {}, {}
        for fid, d in self.f.items():
            self._names.setdefault((d["table_id"], d["field_name"]), []).append(fid)
            self._trimmed.setdefault((d["table_id"], str(d["field_name"]).strip()), []).append(fid)
        self._plans = {}

    # ---- what a field becomes
    def type_of(self, fid):
        return self.attempt(self.f[fid], self.defaults)[0]

    def creatable(self, fid):
        """(True, '') when the field will exist in Planning after its table is loaded (as any type, text fallback included); else (False, why)."""
        d = self.f.get(fid)
        if d is None:
            return False, "a field that is not in the stored schema"
        if d["data_type"] in self.skip_types:
            return False, "a link or attachment field, which is loaded later"
        if d["abandoned"]:
            return False, "a field a person abandoned"
        return True, ""

    def usable(self, fid):
        """(True, '') when a formula can read the field as a real, non-array Planning field."""
        ok, why = self.creatable(fid)
        if not ok:
            return False, why
        d = self.f[fid]
        t = self.type_of(fid)
        if t == "multi-select":
            return False, "a multi-select, which formulas do not read yet"
        if d["data_type"] in self.array_types and not d["lookup_snapshot"]:
            return False, "an array, which is stored as raw text"
        return True, ""

    def id_by_name(self, table_id, name):
        got = self._names.get((table_id, name)) or []
        if not got:                                       # Airtable names may carry edge spaces that a formula reference does or does not repeat; Planning keeps the trimmed name
            got = self._trimmed.get((table_id, str(name).strip())) or []
        return got[0] if len(got) == 1 else None

    def formulas(self, table_id):
        return [fid for fid in self.order.get(table_id, []) if self.f[fid]["data_type"] == "formula" and not self.f[fid]["abandoned"] and self.type_of(fid) == "formula"]

    def table_of(self, node):
        return node[len(TABLE_NODE):] if node.startswith(TABLE_NODE) else (self.f[node]["table_id"] if node in self.f else None)

    def source_node(self, fid):
        """The thing another table must provide before a derived field is meaningful: the source field a lookup or rollup reads, or the linked table for a count."""
        d = self.f[fid]
        if d["data_type"] in ("multipleLookupValues", "rollup") and d.get("source_field_id"):
            return d["source_field_id"]
        link = self.f.get(d.get("source_link_field_id") or "")
        if link and link.get("linked_table_id"):
            return TABLE_NODE + link["linked_table_id"]
        return None

    # ---- what one formula needs
    def plan(self, fid):
        """{refs: [field IDs read], blocked: [plain reasons it can never be built as a formula], formulas: [formula IDs it reads], needs: [nodes of OTHER tables it waits for],
        held: [names of the link or derived fields it reads: the formula is held back, linkdeps.py]}.
        Derived from the schema only."""
        if fid in self._plans:
            return self._plans[fid]
        d = self.f[fid]
        ids, names = reference_ids(d.get("formula_text"))
        refs = list(ids)
        blocked, forms, needs, held = [], [], set(), []
        for n in names:
            got = self.id_by_name(d["table_id"], n)
            if got is None:
                blocked.append("it reads {%s}, a name that is not one single field of this table" % n)
            elif got not in refs:
                refs.append(got)
        tr = translate(d.get("formula_text"), lambda kind, key: "X")
        if tr.status == BLOCKED:
            blocked.append(tr.reason.rstrip("."))
        if not return_type(d.get("result_type")):
            blocked.append("its result type %s has no verified Planning return type" % (d.get("result_type") or "(unknown)"))
        for r in sorted(refs):
            rd = self.f.get(r)
            if rd is None:
                blocked.append("it reads a field that is not in the stored schema (%s)" % r)
                continue
            if rd["data_type"] in DERIVED or rd["data_type"] == "multipleRecordLinks":      # its value comes through a link: Git-R-Done holds the formula back (linkdeps.py)
                held.append(rd["field_name"])
                continue
            ok, why = self.usable(r)
            if not ok:
                blocked.append("it reads %s, %s" % (rd["field_name"], why))
            elif rd["data_type"] == "formula":
                forms.append(r)
            elif rd["data_type"] in DERIVED:
                node = self.source_node(r)
                if node is not None:
                    nd = self.f.get(node)
                    if nd is not None and not self.creatable(node)[0]:
                        blocked.append("it reads %s, which comes from %s, %s" % (rd["field_name"], nd["field_name"], self.creatable(node)[1]))
                    elif self.table_of(node) not in (None, d["table_id"]):
                        needs.add(node)
        self._plans[fid] = {"refs": sorted(refs), "blocked": blocked, "formulas": sorted(forms), "needs": sorted(needs), "held": sorted(held)}
        return self._plans[fid]

    def status(self, fid, present, stack=()):
        """('created'|'blocked'|'waiting'|'ready', reasons, waits). `present` = {Airtable field ID or tbl:<table> node: name} of what Planning already holds."""
        d = self.f[fid]
        if d["field_id"] in present:
            return ("pre" if str(present[fid]).startswith("PRE-") else "created"), [], []
        p = self.plan(fid)
        if p["held"] and not p["blocked"]:
            return "held", ["it reads %s, which comes through a link" % ", ".join(p["held"])], []
        reasons = list(p["blocked"])
        waits = {n for n in p["needs"] if n not in present}
        if fid in stack:
            return "blocked", ["it is part of a loop of formulas"], []
        for g in p["formulas"]:
            st, why, w = self.status(g, present, stack + (fid,))
            if st == "held":
                return "held", ["it reads the formula %s, which is held back" % self.f[g]["field_name"]], []
            if st == "blocked":
                reasons.append("it reads the formula %s, which cannot be built" % self.f[g]["field_name"])
            elif st == "pre":
                reasons.append("it reads the formula %s, which was stored as text" % self.f[g]["field_name"])
            elif st == "waiting":
                waits |= set(w)
        if reasons:
            return "blocked", reasons, []
        if waits:
            return "waiting", [], sorted(waits)
        return "ready", [], []

    # ---- the pre-check
    def present_from_map(self, conn, env, ws):
        """{Airtable field ID: name sent, 'tbl:<table>': name} for what the ID map says exists in Planning (status created)."""
        out = {}
        for r in conn.execute("SELECT kind, airtable_field_id, table_id, name FROM load_id_map WHERE system='planning' AND status='created' AND workfront_id IS NOT NULL AND environment=? "
                              "AND workspace_key=? AND base_id=? AND kind IN ('field','record_type')", (env, ws or "", self.base_id)):
            if r["kind"] == "record_type" and r["table_id"]:
                out[TABLE_NODE + r["table_id"]] = r["name"]
            elif r["kind"] == "field" and r["airtable_field_id"]:
                out[r["airtable_field_id"]] = r["name"]
        return out

    def _after_run(self, table_id, present):
        """What Planning holds after table_id is run: its record type, every field it creates, then each formula that can be built, in dependency order. Returns (present, [formula IDs created])."""
        now = dict(present)
        now[TABLE_NODE + table_id] = self.tables.get(table_id, "")
        formulas = set(self.formulas(table_id))
        for fid in self.order.get(table_id, []):
            if fid not in formulas and self.creatable(fid)[0]:
                now.setdefault(fid, self.f[fid]["field_name"])
        made = []
        progress = True
        while progress:
            progress = False
            for fid in sorted(formulas):
                if fid not in now and self.status(fid, now)[0] == "ready":
                    now[fid] = self.f[fid]["field_name"]
                    made.append(fid)
                    progress = True
        return now, made

    def _waiting(self, present):
        """Formula IDs (all tables) that are waiting for another table, given `present`."""
        return [fid for t in sorted(self.tables) for fid in self.formulas(t) if self.status(fid, present)[0] == "waiting"]

    def advise(self, table_id, present):
        """The pre-check of one table. {table_id, table_name, counts: {total, created, stored_as_text, ready, waiting, blocked}, blocked: [{field_id, name, reasons}],
        waiting_on: [{table_id, table_name, loaded, formulas: n, fields: [names]}], order: [{table_id, table_name, creates, unlocks, formulas: [names]}], advice: [plain sentences]}.
        `order` is the suggested sequence of runs for the base (a table may appear twice) cut after the last run that builds a formula of this table."""
        mine = self.formulas(table_id)
        counts = {"total": len(mine), "created": 0, "stored_as_text": 0, "ready": 0, "waiting": 0, "blocked": 0, "held_back": 0}
        blocked, waits_by_table = [], {}
        for fid in mine:
            st, why, waits = self.status(fid, present)
            key = {"created": "created", "pre": "stored_as_text", "ready": "ready", "waiting": "waiting", "blocked": "blocked", "held": "held_back"}[st]
            counts[key] += 1
            if st == "blocked":
                blocked.append({"field_id": fid, "name": self.f[fid]["field_name"], "reasons": why})
            for n in waits:
                t = self.table_of(n)
                waits_by_table.setdefault(t, set()).add(fid)
        waiting_on = [{"table_id": t, "table_name": self.tables.get(t, t), "loaded": (TABLE_NODE + t) in present, "formulas": len(ids), "fields": sorted(self.f[i]["field_name"] for i in ids)}
                      for t, ids in sorted(waits_by_table.items(), key=lambda kv: (-len(kv[1]), self.tables.get(kv[0], kv[0]).lower(), kv[0]))]
        order = self._order(table_id, present) if counts["waiting"] else []
        name = self.tables.get(table_id, table_id)
        advice = []
        if counts["waiting"]:
            advice.append("%d of the %d calculated field(s) in %s read fields that come from other tables, so they cannot be built yet." % (counts["waiting"], counts["total"], name))
            for w in waiting_on:
                advice.append("Load %s first: %d calculated field(s) of %s depend on it%s." % (w["table_name"], w["formulas"], name, " (its record type exists, but the fields they read do not)" if w["loaded"] else ""))
            if order:
                steps = ["%s (builds %d, unlocks %d)" % (o["table_name"], o["creates"], o["unlocks"]) for o in order]
                advice.append("Suggested order: " + ", then ".join(steps) + ".")
                if sum(1 for o in order if o["table_id"] == table_id) > 1 or any(o["table_id"] == table_id for o in order[:-1]):
                    advice.append("%s appears more than once because its calculated fields and another table's depend on each other: the first run builds what it can, a later run builds the rest." % name)
            if not order:
                advice.append("No order of runs builds them: what they wait for is itself blocked.")
        if counts["held_back"]:
            advice.append("%d calculated field(s) read a link or a field that comes through a link, so they are held back: not created, not stored as text. Set the links, then rebuild them in Planning by hand." % counts["held_back"])
        if counts["blocked"]:
            advice.append("%d calculated field(s) cannot be built as formulas (the reasons are listed); each is loaded as a PRE- text field instead." % counts["blocked"])
        if counts["ready"] and not counts["waiting"]:
            advice.append("%d calculated field(s) can be built when this table is run." % counts["ready"])
        return {"table_id": table_id, "table_name": name, "counts": counts, "blocked": blocked, "waiting_on": waiting_on, "order": order, "advice": advice}

    def _order(self, target, present):
        """Greedy simulation. Each step runs the table that builds the most formulas now; a tie goes to the table that unlocks the most waiting formulas elsewhere, then to the
        table ID. A run that builds nothing and unlocks nothing is never chosen. Stops when the target has no formula left that a run can build."""
        now, steps = dict(present), []
        for _ in range(2 * len(self.tables) + 2):
            if not any(self.status(f, now)[0] in ("waiting", "ready") for f in self.formulas(target)):
                break
            waiting_before = set(self._waiting(now))
            best = None
            for t in sorted(self.tables):
                after, made = self._after_run(t, now)
                unlocked = len((waiting_before - set(self._waiting(after))) - set(made))
                if not made and not unlocked:
                    continue
                key = (-len(made), -unlocked, t)
                if best is None or key < best[0]:
                    best = (key, t, after, made, unlocked)
            if best is None:
                return []
            _k, t, after, made, unlocked = best
            steps.append({"table_id": t, "table_name": self.tables.get(t, t), "creates": len(made), "unlocks": unlocked, "formulas": sorted(self.f[m]["field_name"] for m in made)})
            now = after
        if any(self.status(f, now)[0] in ("waiting", "ready") for f in self.formulas(target)):
            return []
        while steps and not (steps[-1]["table_id"] == target and steps[-1]["creates"]):
            steps.pop()
        return steps
