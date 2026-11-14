#!/usr/bin/env python3
"""Field and table name validator for the Airtable -> Workfront migration. Standard library only.

Pure functions: no database, no network, no file access, no randomness. The same input always gives the same output.
Nothing here ever changes a name. Every fix is a SUGGESTION that a person approves.

Three targets are validated independently, each with its own rules: planning_name (Workfront Planning field name),
core_name (Workfront Core custom field name) and core_label (Workfront Core custom field label).

Verdicts: PASS, AUTO_FIXABLE (a deterministic fix exists and is shown, but is applied only on approval),
NEEDS_HUMAN (the fix needs judgment, or the rule has no fix).

Pipeline order (fixed, because the order changes the output):
  1. table names are cleaned (suggested new table names)        - always runs
  2. table_swap      old table names inside field names -> new table names, resolved through the schema
  3. char_cleanup    characters outside the allowlist, double and edge spaces, 1Q -> Q1 (names only)
  4. leading_digits  a name that starts with a digit is rearranged by the chosen preset (names only)
  5. duplicates      case-insensitive, per table, on the repaired names - always runs
Steps 2 to 4 can be switched off in Settings. The baseline checks (see RULES, baseline=True) always run.

Limits marked PROVISIONAL below are not confirmed against Adobe's documentation and must be reviewed.
"""
import re
import unicodedata
from collections import OrderedDict, defaultdict, namedtuple

PASS, AUTO_FIXABLE, NEEDS_HUMAN = "PASS", "AUTO_FIXABLE", "NEEDS_HUMAN"
TARGETS = ("planning_name", "core_name", "core_label")
STEPS = ("table_swap", "char_cleanup", "leading_digits")   # the toggles, in pipeline order

# Per-target profile. kind "name" = strict ASCII allowlist; kind "label" = keeps the original as far as possible.
PROFILES = {
    "planning_name": {"kind": "name", "max_len": 250, "len_status": "confirmed (Adobe Workfront Planning, Field overview: 250 characters)",
                      "title": "Workfront Planning field name"},
    "core_name": {"kind": "name", "max_len": 255, "len_status": "PROVISIONAL (Adobe's custom form page states no length limit for API names; 255 is a placeholder)",
                  "title": "Workfront Core custom field name"},
    "core_label": {"kind": "label", "max_len": 255, "len_status": "PROVISIONAL (Adobe's custom form page states no length limit for labels; 255 is a placeholder)",
                   "title": "Workfront Core custom field label"},
}
# Characters Adobe documents as unsupported in custom field labels and API names (Experience League, 'Create a custom form'):
# tab, newline, carriage return, form feed (caught by the control-character rule) and [ ] ( ) : { }.
# Adobe also says special characters in a label do not display correctly in reports without listing them; emoji are not mentioned there.
LABEL_BLOCKLIST = frozenset('[](){}:')
# PROVISIONAL and empty: names Adobe reserves (to be confirmed). Compared case-insensitively.
RESERVED_NAMES = {t: frozenset() for t in TARGETS}

# The only characters whose removal is AUTO_FIXABLE. Each is deleted with nothing put in its place.
AUTO_DELETE_CHARS = frozenset("'\"`~!@#$%^&*()[]{};:<>,./?")
# Whitespace that is always turned into a plain space (ASCII 32) and must never remain in a name: no-break space (160), tab, new line,
# vertical tab, form feed, carriage return. This is AUTO_FIXABLE.
WS_CHARS = frozenset(chr(c) for c in (160, 9, 10, 11, 12, 13))
# Rules whose repair is AUTO_FIXABLE when nothing else is wrong (see evaluate_name).
AUTO_RULES = frozenset({"SPECIAL_CHAR", "WHITESPACE_CHAR", "EDGE_SPACE", "DOUBLE_SPACE"})
NAME_ALLOWED = re.compile(r"[A-Za-z0-9 \-]")
_PUNCT_MAP = {ord(c): r for c, r in {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "−": "-",
                                     " ": " ", " ": " ", " ": " "}.items()}
# Latin letters that Unicode does not decompose into an ASCII letter plus an accent: a fixed, deterministic spelling for each.
_LATIN_MAP = {0xDF: "ss", 0xE6: "ae", 0xC6: "AE", 0x153: "oe", 0x152: "OE", 0xF8: "o", 0xD8: "O", 0x142: "l", 0x141: "L",
              0x111: "d", 0x110: "D", 0xF0: "d", 0xD0: "D", 0xFE: "th", 0xDE: "Th", 0x131: "i"}
_QTR_1Q = re.compile(r"(?<![A-Za-z0-9])([1-4])Q(?![A-Za-z])(\d?)")
_QTR_Q1 = re.compile(r"(?<![A-Za-z0-9])Q([1-4])(?![A-Za-z0-9])")
_LEAD = re.compile(r"^(\d[A-Za-z0-9\-]*)\s+(.*\S)$")
_TABLE_PREFIX = re.compile(r"^(\d+(?:\.\d+)*)[.)]?\s+(.+)$")


# Options that can be set separately for each of the three names (planning_name, core_name, core_label). Anything not set falls back to the
# general value (the first six) or to the default shown, so by default the names behave the same.
BOOL_OPTS = ("table_swap", "char_cleanup", "leading_digits", "allow_leading_digit", "label_keep_quarter")
TARGET_OPTS = BOOL_OPTS + ("digit_style", "digit_word", "max_len", "extra_allowed", "extra_blocked")
MAX_LEN_CAP = {"planning_name": 250, "core_name": 1000, "core_label": 1000}   # Planning's 250 is documented by Adobe and cannot be exceeded
_PUNCT_POOL = frozenset(chr(c) for c in range(33, 127) if not chr(c).isalnum() and chr(c) != "-")   # printable ASCII punctuation, never a space, letter, digit or hyphen


def _punct_string(value, what):
    value = str(value or "")
    if any(c not in _PUNCT_POOL for c in value):
        raise ValueError("%s may contain only printable ASCII punctuation (no letters, digits, spaces or hyphens)." % what)
    return "".join(sorted(set(value)))


class Settings(object):
    """Toggles (steps 2 to 4) and presets. table_prefix: 'drop' | 'end' | 'plain'. digit_style: 'move_to_end' | 'prefix_word'.
    targets: optional per-name overrides, {target: {option: value}} using TARGET_OPTS, so Planning name, Core name and Core label can be
    configured separately: the three step toggles, allow_leading_digit, digit_style and digit_word, max_len, extra_allowed (names: extra
    punctuation that is allowed instead of removed) and extra_blocked (labels: extra punctuation that is removed)."""
    def __init__(self, table_swap=True, char_cleanup=True, leading_digits=True, table_prefix="end",
                 digit_style="move_to_end", digit_word="", label_keep_quarter=True, targets=None, description_include_airtable=False):
        if table_prefix not in ("drop", "end", "plain") or digit_style not in ("move_to_end", "prefix_word"):
            raise ValueError("unknown preset")
        self.table_swap, self.char_cleanup, self.leading_digits = bool(table_swap), bool(char_cleanup), bool(leading_digits)
        self.table_prefix, self.digit_style, self.digit_word = table_prefix, digit_style, (digit_word or "").strip()
        self.label_keep_quarter = bool(label_keep_quarter)
        self.description_include_airtable = bool(description_include_airtable)   # append Airtable's own field description to generated descriptions
        self.targets = {}
        for t, opts in (targets or {}).items():
            if t not in TARGETS:
                raise ValueError("unknown name: %s" % t)
            clean = {}
            for k, v in (opts or {}).items():
                if k not in TARGET_OPTS:
                    raise ValueError("unknown option: %s" % k)
                kind = PROFILES[t]["kind"]
                if k in BOOL_OPTS:
                    if k in ("leading_digits", "allow_leading_digit") and kind == "label":
                        continue                    # labels may start with a digit and have no leading-digit step
                    if k == "label_keep_quarter" and kind != "label":
                        continue
                    clean[k] = bool(v)
                elif k == "digit_style":
                    if v not in ("move_to_end", "prefix_word"):
                        raise ValueError("unknown preset")
                    clean[k] = v
                elif k == "digit_word":
                    clean[k] = str(v or "").strip()[:40]
                elif k == "max_len":
                    try:
                        n = int(v)
                    except (TypeError, ValueError):
                        raise ValueError("The maximum length must be a whole number.")
                    if not 1 <= n <= MAX_LEN_CAP[t]:
                        raise ValueError("The maximum length for %s must be between 1 and %d." % (PROFILES[t]["title"], MAX_LEN_CAP[t]))
                    clean[k] = n
                elif k == "extra_allowed":
                    if kind == "label":
                        if v:
                            raise ValueError("Extra allowed characters apply to names; a label is cleaned by removing characters (see extra_blocked).")
                        continue
                    clean[k] = _punct_string(v, "Extra allowed characters")
                elif k == "extra_blocked":
                    if kind != "label":
                        if v:
                            raise ValueError("Extra characters to remove apply to labels; a name uses an allowlist (see extra_allowed).")
                        continue
                    clean[k] = _punct_string(v, "Extra characters to remove")
            if clean:
                self.targets[t] = clean

    def opt(self, target, key):
        """The value of an option for one name: its own override, else the general value, else the default."""
        o = self.targets.get(target) or {}
        if key in o:
            return o[key]
        if key in ("table_swap", "char_cleanup", "leading_digits", "digit_style", "digit_word", "label_keep_quarter"):
            return getattr(self, key)
        return {"max_len": PROFILES[target]["max_len"], "allow_leading_digit": False, "extra_allowed": "", "extra_blocked": ""}[key]

    def enabled(self, step, target=None):
        return bool(self.opt(target, step)) if target else bool(getattr(self, step))

    def as_dict(self):
        d = dict(self.__dict__)
        d["targets"] = {t: dict(o) for t, o in self.targets.items()}
        return d


def auto_chars(settings, target):
    """The characters whose deletion is AUTO_FIXABLE for this name: for a name the agreed list minus anything the user allowed for it, for a
    label Adobe's unsupported characters plus anything the user chose to remove."""
    if PROFILES[target]["kind"] == "label":
        return LABEL_BLOCKLIST | frozenset(settings.opt(target, "extra_blocked"))
    return AUTO_DELETE_CHARS - frozenset(settings.opt(target, "extra_allowed"))


# ---------------------------------------------------------------- text helpers (pure)

def _translit(s):
    """Map smart quotes and dashes, then drop accents (NFKD). Deterministic."""
    s = s.translate(_PUNCT_MAP).translate(_LATIN_MAP)
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _squash(s):
    return re.sub(r" {2,}", " ", s).strip()


def delete_auto_chars(s, chars=None):
    """The AUTO_FIXABLE repair: every WS_CHARS character becomes a plain space, the listed characters are deleted with nothing in
    their place, then repeated and edge spaces are closed up."""
    chars = AUTO_DELETE_CHARS if chars is None else chars
    s = "".join(" " if c in WS_CHARS else c for c in s)
    return _squash("".join(c for c in s if c not in chars))


def clean_name_chars(s, extra_allowed=""):
    """Names, the full proposal: keep A-Z a-z 0-9 space hyphen (and any punctuation the user allowed for this name). AUTO_DELETE_CHARS are
    deleted; '_' and a backslash become a space; accents become plain letters; everything else (emoji, other symbols, other non-ASCII) is
    deleted. Only the first part is AUTO_FIXABLE."""
    keep = frozenset(extra_allowed or "")
    s = _translit(delete_auto_chars(s, AUTO_DELETE_CHARS - keep))
    s = "".join(" " if c in "_\\" and c not in keep else c for c in s)
    s = re.sub(r"\s", " ", s)
    return _squash("".join(c for c in s if NAME_ALLOWED.match(c) or c in keep))


def clean_label_chars(s, extra_blocked=""):
    """Labels: stay as close to the original as possible. Accents become plain letters, emoji and non-ASCII are removed,
    and LABEL_BLOCKLIST characters (and any the user chose to remove) are removed. Everything else is kept."""
    blocked = LABEL_BLOCKLIST | frozenset(extra_blocked or "")
    s = re.sub(r"\s", " ", _translit(s))
    return _squash("".join(c for c in s if 32 <= ord(c) <= 126 and c not in blocked))


def quarter_to_q1(s):
    """1Q -> Q1 only as a whole token ('1Quality' and '21Q' are untouched). '1Q24' becomes 'Q1 24'."""
    return _QTR_1Q.sub(lambda m: "Q" + m.group(1) + (" " + m.group(2) if m.group(2) else ""), s)


def quarter_style(s):
    """'Q1' | '1Q' | 'mixed' | 'none': which quarter convention a string uses."""
    a, b = bool(_QTR_Q1.search(s)), bool(_QTR_1Q.search(s))
    return "mixed" if a and b else "Q1" if a else "1Q" if b else "none"


def fix_leading_digits(s, style, word=""):
    """Move the leading number token behind the descriptive words, or put a chosen word in front. None when it cannot be done."""
    m = _LEAD.match(s)
    if not m:
        return None
    token, rest = m.group(1), m.group(2)
    if style == "move_to_end":
        return rest + " " + token
    if style == "prefix_word" and word:
        return "%s %s %s" % (word, token, rest)
    return None


def _whole(old):
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(old) + r"(?![A-Za-z0-9])")


# ---------------------------------------------------------------- rules (data + small checks)

Rule = namedtuple("Rule", "id title targets step baseline check text bad good")


def _c_empty(n, t, st): return n.strip() == ""
def _c_nonascii(n, t, st): return any((ord(c) > 126 or ord(c) < 32) and c not in WS_CHARS for c in n)
def _c_ws(n, t, st): return any(c in WS_CHARS for c in n)
def _c_special(n, t, st):
    if PROFILES[t]["kind"] == "label":
        blocked = LABEL_BLOCKLIST | frozenset(st.opt(t, "extra_blocked"))
        return any(c in blocked for c in n)
    keep = frozenset(st.opt(t, "extra_allowed"))
    return any(32 <= ord(c) <= 126 and not NAME_ALLOWED.match(c) and c not in keep for c in n)
def _c_edge(n, t, st): return n != n.strip()
def _c_double(n, t, st): return "  " in n
def _c_lead(n, t, st): return bool(n[:1].isdigit()) and not st.opt(t, "allow_leading_digit")
def _c_long(n, t, st): return len(n) > st.opt(t, "max_len")
def _c_quarter(n, t, st): return bool(_QTR_1Q.search(n))
def _c_reserved(n, t, st): return n.strip().casefold() in RESERVED_NAMES[t]


_NAMES = ("planning_name", "core_name")
RULES = [
    Rule("EMPTY", "Name is empty", TARGETS, "baseline", True, _c_empty, "A name must contain at least one character.", "(blank)", "Budget"),
    Rule("NON_ASCII", "Non-ASCII character", TARGETS, "char_cleanup", True, _c_nonascii,
         "Emoji, accented letters, smart quotes, zero-width characters and other non-ASCII characters are not allowed, because data exported to CSV "
         "without the right encoding corrupts them. Proposed repair (needs approval): accents become plain letters, the rest are removed.", "Café ✨ Budget", "Cafe Budget"),
    Rule("WHITESPACE_CHAR", "Tab, new line or no-break space", TARGETS, "char_cleanup", True, _c_ws,
         "A no-break space (ASCII 160), tab, new line, vertical tab, form feed or carriage return must never appear in a name or label. Each is turned into a "
         "plain space (ASCII 32), and the spaces are then closed up. AUTO_FIXABLE.", "Total\\tCost", "Total Cost"),
    Rule("SPECIAL_CHAR", "Special character", TARGETS, "char_cleanup", True, _c_special,
         "Names allow only letters, digits, spaces and hyphens. The characters ' \" ` ~ ! @ # $ % ^ & * ( ) [ ] { } ; : < > , . / ? are AUTO_FIXABLE: they are "
         "deleted with nothing in their place (and any double space that leaves is closed up). Any other symbol, such as an underscore, a backslash, | + =, "
         "needs approval; the proposal turns an underscore or backslash into a space and deletes the rest. Labels keep the original text and remove only "
         "the characters Adobe documents as unsupported in labels: [ ] ( ) { } : and tab, newline, carriage return, form feed.", "PO Number (from PO#s)", "PO Number from POs"),
    Rule("EDGE_SPACE", "Leading or trailing space", TARGETS, "char_cleanup", True, _c_edge, "No space at the start or end. Removing them is AUTO_FIXABLE.", " Budget ", "Budget"),
    Rule("DOUBLE_SPACE", "Double space", TARGETS, "char_cleanup", True, _c_double, "No two spaces in a row. Closing them up is AUTO_FIXABLE.", "Total  Cost", "Total Cost"),
    Rule("LEADING_DIGIT", "Starts with a digit", _NAMES, "leading_digits", True, _c_lead,
         "A name must not start with a digit. The proposed repair (needs approval) follows the chosen preset: move the leading number token behind the descriptive words, "
         "or put a chosen word in front. A name that is only a number needs a person.", "500K Acme Fund", "Acme Fund 500K"),
    Rule("TOO_LONG", "Name too long", TARGETS, "baseline", True, _c_long,
         "Maximum length per target (Planning 250, confirmed; Core name and label 255, provisional). There is no automatic shortening.", "(260 characters)", "(a shorter name chosen by a person)"),
    Rule("QUARTER_STYLE", "Quarter written 1Q", _NAMES, "char_cleanup", False, _c_quarter,
         "Names write quarters as Q1 to Q4, never 1Q to 4Q. Only a whole token is matched, so '1Quality' is untouched. Labels may keep the client's own convention; "
         "each label's convention is recorded instead.", "1Q Budget", "Q1 Budget"),
    Rule("RESERVED", "Reserved name", TARGETS, "baseline", True, _c_reserved,
         "Names Adobe reserves cannot be used (list to be confirmed; empty for now).", "(reserved word)", "(any other name)"),
    Rule("TABLE_REF", "Old table name inside a field name", TARGETS, "table_swap", False, None,
         "When a field name contains the name of a table that is being renamed (typical for lookup and rollup fields), the table's new name is suggested. "
         "The table is identified through the schema (link field -> linked table ID), never by guessing, and the old name must match as a whole name. "
         "Because every table name must be approved before fields are reviewed, a swap that follows the schema is AUTO_FIXABLE; a mention with no schema path is NEEDS_HUMAN.",
         "PO Number (from PO#s)  [table renamed POs]", "PO Number (from POs)"),
    Rule("DUPLICATE", "Duplicate name", _NAMES, "duplicates", True, None,
         "Names must be unique within a table, ignoring case. The check runs last, on the repaired names, so collisions created by the cleanup are caught. "
         "Duplicates are always a decision for a person; abandoning an empty member shrinks the group.", "Budget / budget", "Budget / Budget Q1"),
]
RULE_BY_ID = {r.id: r for r in RULES}


def violations(name, target, settings):
    """Rule IDs a name breaks. Baseline rules always; the others only while their step is switched on."""
    out = []
    for r in RULES:
        if r.check is None or target not in r.targets:
            continue
        if not r.baseline and not settings.enabled(r.step, target):
            continue
        if r.check(name, target, settings):
            out.append(r.id)
    return out


# ---------------------------------------------------------------- table names (step 1)

def clean_table_name(old, settings):
    """Suggested new table name: number prefix handled by the preset, then characters cleaned, then a leading digit rearranged."""
    s = _translit(old).strip()
    m = _TABLE_PREFIX.match(s)
    if m:
        num, rest = m.group(1).replace(".", "-"), clean_name_chars(m.group(2), settings.opt("planning_name", "extra_allowed"))
        if not rest:
            return clean_name_chars(old, settings.opt("planning_name", "extra_allowed"))
        s = {"drop": rest, "end": rest + " " + num, "plain": num + " " + rest}[settings.table_prefix]
    else:
        s = clean_name_chars(s, settings.opt("planning_name", "extra_allowed"))
    if s[:1].isdigit() and not (m and settings.table_prefix == "plain"):
        s = fix_leading_digits(s, settings.opt("planning_name", "digit_style"), settings.opt("planning_name", "digit_word")) or s
    return s


def table_names(tables, settings, approved=None):
    """tables: iterable of {table_id, table_name, base_id?}. approved: {table_id: name a person approved}.
    Returns OrderedDict table_id -> {old, suggested, effective, approved, verdict, rules}. 'effective' is what the swap step uses:
    the approved name, else the suggestion when it is clean, else the old name."""
    approved = approved or {}
    out = OrderedDict()
    for t in tables:
        old = t["table_name"]
        sug = clean_table_name(old, settings)
        bad = [r for r in violations(sug, "planning_name", settings) if not (r == "LEADING_DIGIT" and settings.table_prefix == "plain")]
        eff = approved.get(t["table_id"]) or (sug if not bad else old)
        old_rules = violations(old, "planning_name", settings)
        if sug == old and not old_rules:
            verdict = PASS
        elif not bad and old_rules and set(old_rules) <= AUTO_RULES and sug == delete_auto_chars(old, auto_chars(settings, "planning_name")):
            verdict = AUTO_FIXABLE       # only listed characters and whitespace were repaired; a number prefix or anything else is a person's decision
        else:
            verdict = NEEDS_HUMAN
        out[t["table_id"]] = {"table_id": t["table_id"], "base_id": t.get("base_id"), "old": old, "suggested": sug, "effective": eff,
                              "approved": t["table_id"] in approved, "verdict": verdict, "rules": bad if bad else violations(old, "planning_name", settings)}
    groups = defaultdict(list)                       # two tables ending up with the same name in one base
    for tid, v in out.items():
        groups[(v["base_id"], v["effective"].casefold())].append(tid)
    for tids in groups.values():
        if len(tids) > 1:
            for tid in tids:
                out[tid]["verdict"] = NEEDS_HUMAN
                out[tid]["rules"] = list(out[tid]["rules"]) + ["DUPLICATE"]
    return out


# ---------------------------------------------------------------- field names

def swap_table_refs(name, field, fields_by_id, tmap, base_tables):
    """Step 2. Returns (new_name, status, detail). status: 'none' | 'swapped' | 'ambiguous'.
    A link, lookup, rollup or count field names its table through the schema, so only that table's old name is replaced.
    A field without a schema path that still mentions a renamed table's old name is 'ambiguous' (needs a person)."""
    tid = field.get("linked_table_id")
    if not tid and field.get("source_link_field_id"):
        tid = (fields_by_id.get(field["source_link_field_id"]) or {}).get("linked_table_id")
    if tid and tid in tmap:
        old, new = tmap[tid]["old"], tmap[tid]["effective"]
        if old != new and _whole(old).search(name):
            # 'swapped' = the new table name is approved (safe to treat as AUTO_FIXABLE); 'swapped_unapproved' = it is still only a proposal
            return (_whole(old).sub(lambda m: new, name), "swapped" if tmap[tid]["approved"] else "swapped_unapproved",
                    "table %s: %s -> %s" % (tid, old, new))
        return name, "none", ""
    if tid:
        return name, "none", ""
    cur, hits = name, []
    for t2 in sorted(base_tables, key=lambda t: (-len(tmap[t["table_id"]]["old"]), tmap[t["table_id"]]["old"])):
        m = tmap[t2["table_id"]]
        if m["old"] != m["effective"] and _whole(m["old"]).search(cur):
            hits.append(m["old"])
            cur = _whole(m["old"]).sub(lambda x, new=m["effective"]: new, cur)
    return (cur, "ambiguous", "mentions renamed table(s): " + ", ".join(hits)) if hits else (name, "none", "")


def evaluate_name(candidate, target, settings, field, fields_by_id, tmap, base_tables):
    """Run steps 2 to 4 on one candidate name for one target. Duplicates are checked later, across fields."""
    kind = PROFILES[target]["kind"]
    steps, reasons, rules = {}, [], violations(candidate, target, settings)
    cur = candidate
    ambiguous = False
    swap_status = "none"
    if settings.opt(target, "table_swap") and tmap:
        new, status, detail = swap_table_refs(cur, field, fields_by_id, tmap, base_tables)
        swap_status = status
        steps["table_swap"] = new != cur
        if status != "none":
            rules.append("TABLE_REF")
        if status == "ambiguous":
            ambiguous = True
            reasons.append("Table reference is ambiguous (%s)" % detail)
        cur = new
    swapped_name = cur
    if settings.opt(target, "char_cleanup"):
        new = clean_name_chars(cur, settings.opt(target, "extra_allowed")) if kind == "name" else clean_label_chars(cur, settings.opt(target, "extra_blocked"))
        if kind == "name":
            new = quarter_to_q1(new)
        elif not settings.opt(target, "label_keep_quarter"):
            new = quarter_to_q1(new)
        steps["char_cleanup"] = new != cur
        cur = new
    style, word = settings.opt(target, "digit_style"), settings.opt(target, "digit_word")
    if kind == "name" and settings.opt(target, "leading_digits") and not settings.opt(target, "allow_leading_digit") and cur[:1].isdigit():
        new = fix_leading_digits(cur, style, word)
        if new:
            steps["leading_digits"] = True
            cur = new
        else:
            steps.setdefault("leading_digits", False)
            reasons.append("Starts with a digit and the '%s' preset cannot be applied (%s)" % (
                style, "no word given" if style == "prefix_word" and not word else "nothing follows the number"))
    lost = sum(1 for c in _translit(candidate) if ord(c) > 126 and unicodedata.category(c).startswith("L"))
    if settings.opt(target, "char_cleanup") and lost:
        reasons.append("%d letter(s) outside A-Z could not be converted and were removed; check the spelling of the proposed name" % lost)
    remaining = violations(cur, target, settings)
    for rid in remaining:
        r = RULE_BY_ID[rid]
        off = r.step in STEPS and not settings.enabled(r.step, target)
        reasons.append("%s%s" % (r.title, " (the '%s' step is switched off, so no fix is suggested)" % r.step if off else ""))
    # AUTO_FIXABLE: the only problems are listed characters and whitespace (plus a schema-resolved swap to an APPROVED table name), and the
    # proposed name is exactly what deleting/normalising those gives. Anything else (accents, emoji, 1Q, leading digits, ...) needs a person.
    allowed = AUTO_RULES | ({"TABLE_REF"} if swap_status == "swapped" else set())
    expected = delete_auto_chars(swapped_name, auto_chars(settings, target))
    auto = bool(rules) and set(rules) <= allowed and not remaining and not ambiguous and cur != candidate and cur == expected
    if not rules and cur == candidate and not ambiguous:
        verdict = PASS
    elif auto:
        verdict = AUTO_FIXABLE
    else:
        verdict = NEEDS_HUMAN
        if not remaining and not ambiguous and cur != candidate:
            reasons.append("A repair is proposed but needs approval (%s)" % ", ".join(RULE_BY_ID[r].title for r in sorted(set(rules))))
    res = {"candidate": candidate, "final": cur, "verdict": verdict, "rules": sorted(set(rules)),
           "suggested": cur if verdict == AUTO_FIXABLE else None, "proposed": cur if cur != candidate else None,
           "reasons": reasons, "steps": steps}
    if kind == "label":
        res["quarter_style"] = quarter_style(candidate)
    return res


def presence(f):
    """Data presence from the profiling results, for the review grid and duplicate clusters."""
    profiled = f.get("profiled")
    rc, nc, card = f.get("row_count") or 0, f.get("null_count") or 0, f.get("cardinality") or 0
    capped = bool(f.get("cardinality_is_capped"))
    populated = max(rc - nc, 0)
    why = None
    if not profiled:
        why = "not profiled yet"
    elif f.get("data_type") == "checkbox":
        why = "checkbox: an unchecked box is stored as false, never empty"
    elif populated:
        why = "has values"
    return {"data_type": f.get("data_type"), "row_count": rc, "populated": populated, "null_count": nc,
            "cardinality": card, "cardinality_display": ("{:,}+".format(card) if capped else "{:,}".format(card)),
            "profiled": bool(profiled), "can_abandon": bool(profiled) and populated == 0 and f.get("data_type") != "checkbox",
            "abandon_blocked_because": why}


def table_gate(tmap, approved):
    """Field review may start only when every in-scope table's new name has been approved (an unchanged name is approved too, in one click).
    Returns {ready, pending}: pending lists the table IDs still waiting."""
    pending = [tid for tid in tmap if tid not in (approved or {})]
    return {"ready": not pending, "pending": pending}


def run(fields, tables, settings=None, manual=None, approved_tables=None, abandoned=(), out_of_scope_tables=(), gate=False):
    """Validate every field for every target.
    fields: list of {field_id, table_id, table_name, field_name, data_type, linked_table_id, source_link_field_id, row_count, null_count,
            cardinality, cardinality_is_capped, profiled}. tables: list of {table_id, table_name, base_id}.
    manual: {field_id: {planning_name, core_name, core_label}} - a person's own names; they are validated and never overwritten.
    abandoned: field IDs a person abandoned (excluded, so clusters shrink). out_of_scope_tables: table IDs to skip.
    gate: when True and any in-scope table name is not yet approved, no field is validated: fields and clusters come back empty and
    result['gate'] lists the pending tables. This is the hard requirement for the app; False is for drafts and tests.
    Returns {settings, tables, gate, fields, clusters, summary}."""
    settings = settings or Settings()
    manual = manual or {}
    skip_tables, gone = set(out_of_scope_tables), set(abandoned)
    tmap = table_names([t for t in tables if t["table_id"] not in skip_tables], settings, approved_tables)
    g = table_gate(tmap, approved_tables)
    g["enforced"] = bool(gate)
    if gate and not g["ready"]:
        return {"settings": settings.as_dict(), "tables": tmap, "gate": g, "fields": OrderedDict(), "clusters": [], "summary": summarize({})}
    fields_by_id = {f["field_id"]: f for f in fields}
    tables_by_base = defaultdict(list)
    for t in tables:
        if t["table_id"] in tmap:
            tables_by_base[t.get("base_id")].append(t)
    table_base = {t["table_id"]: t.get("base_id") for t in tables}
    results = OrderedDict()
    live = [f for f in fields if f["table_id"] not in skip_tables]
    for f in sorted(live, key=lambda x: (x["table_id"], x["field_id"])):
        entry = {"field_id": f["field_id"], "table_id": f["table_id"], "original": f["field_name"], "abandoned": f["field_id"] in gone,
                 "presence": presence(f), "targets": {}}
        if entry["abandoned"]:
            results[f["field_id"]] = entry
            continue
        mine = manual.get(f["field_id"]) or {}
        for t in TARGETS:
            cand, src = (mine.get(t) or "").strip(), "manual"
            if not cand:
                cand, src = f["field_name"], "original"
            res = evaluate_name(cand, t, settings, f, fields_by_id, tmap, tables_by_base.get(table_base.get(f["table_id"]), []))
            res["source"] = src
            if src == "manual":      # what the rubric alone would have produced from the Airtable name
                rub = evaluate_name(f["field_name"], t, settings, f, fields_by_id, tmap, tables_by_base.get(table_base.get(f["table_id"]), []))
                res["rubric_name"] = rub["final"]
                res["rubric_verdict"] = rub["verdict"]
                res["differs_from_rubric"] = rub["final"] != cand
            entry["targets"][t] = res
        results[f["field_id"]] = entry
    clusters = []
    for t in _NAMES:                                 # step 5: duplicates, last, on repaired names
        groups = defaultdict(list)
        for e in results.values():
            if not e["abandoned"]:
                groups[(e["table_id"], e["targets"][t]["final"].casefold())].append(e)
        for (tid, key), members in sorted(groups.items()):
            if len(members) < 2:
                continue
            cid = "%s:%s:%s" % (t, tid, key)
            clusters.append({"cluster_id": cid, "target": t, "table_id": tid, "key": key, "members": [
                {"field_id": m["field_id"], "original": m["original"], "candidate": m["targets"][t]["candidate"], "final": m["targets"][t]["final"],
                 "presence": m["presence"]} for m in members]})
            for m in members:
                r = m["targets"][t]
                r["verdict"], r["suggested"] = NEEDS_HUMAN, None
                r["rules"] = sorted(set(r["rules"]) | {"DUPLICATE"})
                r["reasons"].append("Duplicate name within the table (%d fields share '%s'); see group %s" % (len(members), r["final"], cid))
    return {"settings": settings.as_dict(), "tables": tmap, "gate": g, "fields": results, "clusters": clusters, "summary": summarize(results)}


def summarize(results):
    """Counts per target and verdict, per rule, and how many names each step changed."""
    out = {t: {"verdicts": {PASS: 0, AUTO_FIXABLE: 0, NEEDS_HUMAN: 0}, "rules": defaultdict(int), "steps": defaultdict(int)} for t in TARGETS}
    abandoned = 0
    for e in results.values():
        if e["abandoned"]:
            abandoned += 1
            continue
        for t, r in e["targets"].items():
            out[t]["verdicts"][r["verdict"]] += 1
            for rid in r["rules"]:
                out[t]["rules"][rid] += 1
            for s, touched in r["steps"].items():
                out[t]["steps"][s] += 1 if touched else 0
    for t in TARGETS:
        out[t]["rules"], out[t]["steps"] = dict(sorted(out[t]["rules"].items())), dict(out[t]["steps"])
    out["abandoned"] = abandoned
    return out


# ---------------------------------------------------------------- rules document

def rules_markdown():
    """The client-facing rules document, generated from the same registry the validator runs, so the two cannot disagree."""
    L = ["# Field naming rules", "",
         "These rules are applied by the migration's field name validator. Every proposed change is a suggestion that a person approves; the tool never renames anything on its own.", "",
         "## Targets", ""]
    for t in TARGETS:
        p = PROFILES[t]
        L.append("- **%s** (`%s`): %s. Maximum length %d, %s." % (p["title"], t, "strict allowlist of ASCII letters, digits, spaces and hyphens" if p["kind"] == "name"
                                                                else "keeps the original text as far as possible; only emoji, non-ASCII and reporting-problematic characters are removed", p["max_len"], p["len_status"]))
    L += ["", "Names are unique within a table only. Adobe states that a Core custom field API name must be unique across the whole Workfront instance; "
          "that cross-table check is a separate step and is not part of this validator.", "", "## Order the rules run in", "",
          "0. Before any field is reviewed, every in-scope table name must be approved (an unchanged name is approved in one click).",
          "1. Table names are cleaned and a table rename map is built.", "2. Old table names inside field names are replaced (resolved through the schema).",
          "3. Characters are cleaned up and `1Q` becomes `Q1` (names only).", "4. Names that start with a digit are rearranged.",
          "5. Duplicates are checked last, on the repaired names. Always on.", "",
          "Steps 2 to 4 can be switched off for review. Rules marked *baseline* are always checked.", "",
          "## Settings for each name",
          "",
          "The Planning name, the Core name and the Core label can be configured separately. By default they behave the same. Each can have its own:",
          "",
          "- step toggles (table-name swap, character cleanup, leading digits);",
          "- maximum length (Planning cannot exceed Adobe's documented 250);",
          "- permission for a name to start with a digit (names only);",
          "- leading-digit preset and word (names only);",
          "- extra punctuation that is allowed instead of removed (names) or extra characters that are removed (labels).",
          "",
          "A setting that differs from the default is recorded with who saved it and when.", "",
          "## Verdicts", "", "- **PASS**: complies.", "- **AUTO_FIXABLE**: the only problem is one or more of the characters on the auto-delete list; deleting them gives a clean name. The fix is shown next to the original and a batch of these can be approved in one action.",
          "- **NEEDS_HUMAN**: everything else. A proposed repair is shown where one exists, with the reason, and is approved one item at a time. Nothing is ever applied without approval.", "", "## Rules", ""]
    for r in RULES:
        L += ["### %s - %s" % (r.id, r.title), "", r.text, "",
              "- Applies to: %s" % ", ".join(r.targets), "- Pipeline step: %s%s" % (r.step, " (baseline, always checked)" if r.baseline else ""),
              "- Example: `%s` becomes `%s`" % (r.bad, r.good), ""]
    L += ["## Labels and quarter convention", "",
          "A label keeps the client's own convention, including `1Q` style. The validator records which convention each label uses (`Q1`, `1Q`, `mixed` or `none`) so inconsistencies can be reviewed.", ""]
    return "\n".join(L)


if __name__ == "__main__":
    print(rules_markdown())
