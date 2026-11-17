#!/usr/bin/env python3
"""The field and table name review as one self-contained HTML report. Standard library only. Pure: the same input gives the same HTML.

Built for two audiences, like the load-order report: people auditing the process (what rules ran, with which settings, who decided what and
when) and people acting on the result (which names are approved, which still need a person, and why). It holds names, counts and decisions
only; no record values. Every value is HTML-escaped, so a field name can never inject markup.

build_html(rv, events, css, base_label, generated) takes the payload fieldnames_store.review() returns, the event summary from
fieldnames_store.event_summary(), the report CSS (the same REPORT_CSS the load-order report uses, plus EXTRA_CSS below), a display label for
the base, and the generation time.
"""
import html as _html

import fielddesc as fd
import fieldnames as fn

EXTRA_CSS = """
.tag.v-pass{background:#d9f0e2}
.tag.v-auto{background:#dce7fb}
.tag.v-human{background:#ffe5b8}
.tag.st-approved{background:#d9f0e2}
.tag.st-needs_human{background:#ffe5b8}
.tag.stale{background:#f5c6c2}
tr.auto td{background:#f5f8ff}
tr.human td{background:#fffaf0}
tr.out td,tr.gone td{color:#7b8498;background:#fafbfc}
.num{font-variant-numeric:tabular-nums}
.small{font-size:12px;color:#5d667a}
nav.toc ol{margin:6px 0 0 18px;padding:0}
td.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px;word-break:break-word}
"""
VERDICT_TEXT = {fn.PASS: ("Passes", "v-pass"), fn.AUTO_FIXABLE: ("Auto-fixable", "v-auto"), fn.NEEDS_HUMAN: ("Needs a person", "v-human")}
STATUS_TEXT = {"unreviewed": "Unreviewed", "approved": "Approved", "needs_human": "Flagged"}
TARGET_TITLES = {t: fn.PROFILES[t]["title"] for t in fn.TARGETS}


class Raw(str):
    """Text that is already HTML and must not be escaped again."""


def h(x):
    return _html.escape("" if x is None else str(x), quote=True)


def cell(x):
    return x if isinstance(x, Raw) else h(x)


def tag(text, cls=""):
    return Raw('<span class="tag%s">%s</span>' % (" " + cls if cls else "", h(text)))


def verdict_tag(v):
    text, cls = VERDICT_TEXT.get(v, (v or "-", ""))
    return tag(text, cls)


def table(headers, rows, num=()):
    """rows: lists of cells, or (cells, row_class). num: column indexes to right-align."""
    out = ["<table><thead><tr>%s</tr></thead><tbody>" % "".join('<th%s>%s</th>' % (' class="n"' if i in num else "", h(x)) for i, x in enumerate(headers))]
    for r in rows:
        cells, cls = (r if isinstance(r, tuple) else (r, ""))
        out.append("<tr%s>%s</tr>" % (' class="%s"' % cls if cls else "", "".join('<td%s>%s</td>' % (' class="n"' if i in num else "", cell(c)) for i, c in enumerate(cells))))
    out.append("</tbody></table>")
    return Raw("".join(out))


def lines(*parts):
    return Raw("<br>".join(cell(p) for p in parts if p not in (None, "")))


def small(text):
    return Raw('<span class="small">%s</span>' % h(text)) if text else ""


def box(text, cls=""):
    return '<div class="box%s">%s</div>' % (" " + cls if cls else "", cell(text))


def rule_names(ids):
    return ", ".join(fn.RULE_BY_ID[r].title if r in fn.RULE_BY_ID else r for r in ids or [])


def data_cell(p):
    bits = "%s with values, %s empty, cardinality %s" % (p["populated"], p["null_count"], p["cardinality_display"])
    return lines(p["data_type"], small(bits), tag("not profiled", "warn") if not p["profiled"] else "")


def yn(v):
    return "On" if v else "Off"


def option_value(st, t, key):
    v = st.opt(t, key)
    if key in fn.BOOL_OPTS:
        return yn(v)
    if key == "digit_style":
        return {"move_to_end": "Move the number to the end", "prefix_word": "Put a word in front" + (" (%s)" % st.opt(t, "digit_word") if st.opt(t, "digit_word") else "")}[v]
    if key == "emoji_mode":
        return {"remove": "Remove it", "word": "Replace with its word"}[v]
    return str(v) if v != "" else "-"


# (key, label, applies to)
OPTION_ROWS = [
    ("table_swap", "Step 2: table-name swap", fn.TARGETS), ("char_cleanup", "Step 3: character cleanup", fn.TARGETS),
    ("leading_digits", "Step 4: leading digits", ("planning_name", "core_name")), ("allow_leading_digit", "A name may start with a digit", ("planning_name", "core_name")),
    ("digit_style", "Leading-digit rearrangement", ("planning_name", "core_name")), ("max_len", "Maximum length", fn.TARGETS),
    ("extra_allowed", "Extra punctuation allowed", ("planning_name", "core_name")), ("extra_blocked", "Extra characters removed", ("core_label",)),
    ("label_keep_quarter", "Keep the 1Q quarter style", ("core_label",)), ("emoji_mode", "Emoji", fn.TARGETS),
]


def build_html(rv, events, css, base_label, generated):
    base = rv["base_id"]
    ready = rv["gate"]["ready"]
    st = fn.Settings(**rv["settings"])
    fields = rv["fields"]
    live = [f for f in fields if not f["abandoned"] and not f.get("consolidated")]
    P = []
    add = P.append
    add('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Field name review - %s</title><style>%s</style></head><body><main>" % (h(base_label), css))
    add("<h1>Field and table name review</h1>")
    add('<p class="meta">%s &middot; generated %s UTC &middot; names, counts and decisions only (no record values)</p>' % (h(base_label), h(generated)))
    toc = ["About this report", "Summary", "Settings in force", "Table names"]
    if ready:
        toc += ["Field names (one grid per name)", "Duplicate groups", "Your names against the rules", "Descriptions", "Abandoned fields", "Consolidated fields"]
    toc += ["Decisions recorded", "The rules", "Method and checks"]
    add('<nav class="toc box"><b>Contents</b><ol>%s</ol></nav>' % "".join('<li><a href="#s%d">%s</a></li>' % (i + 1, h(t)) for i, t in enumerate(toc)))
    n = [0]

    def section(title):
        n[0] += 1
        add('<h2 id="s%d">%d. %s</h2>' % (n[0], n[0], h(title)))

    # ---- 1. about
    section("About this report")
    add("<p>This report documents whether the field and table names of one Airtable base follow the documented naming standard for Workfront Planning and "
        "Workfront Core, which names a person has approved, and which still need a decision. The tool proposes; a person approves. "
        "Nothing here renames anything on its own, and every decision below is recorded with who made it, when and why.</p>")
    add("<ul>")
    add("<li><b>Source:</b> the Airtable schema and profiling counts already stored by the migration tool (field and table metadata only).</li>")
    add("<li><b>Settings:</b> %s</li>" % ("the saved settings (section 3)." if rv["settings_saved"] else "the defaults: no settings have been saved yet (section 3)."))
    add("<li><b>Table scope:</b> %d in scope, %d out, %d undecided (undecided tables are still checked).</li>" % (rv["scope_counts"]["in"], rv["scope_counts"]["out"], rv["scope_counts"]["undecided"]))
    if ready:
        add("<li><b>Status:</b> every in-scope table name is approved, so field review is open.</li>")
    else:
        add("<li><b>Status:</b> <b>field review has not started</b>: %d in-scope table name(s) still need approval.</li>" % len(rv["gate"]["pending"]))
    add("</ul>")
    if not ready:
        add(box("Field review opens when every in-scope table name has been approved, because field names that mention a table follow the approved table name. "
                "This report therefore covers settings and table names only.", "warn"))

    # ---- 2. summary
    section("Summary")
    if ready:
        sm, ct = rv["summary"], rv["counts"]
        rows = []
        for t in fn.TARGETS:
            v, c = sm[t]["verdicts"], ct[t]
            tot = sum(v.values())
            rows.append([TARGET_TITLES[t], tot, v[fn.PASS], v[fn.AUTO_FIXABLE], v[fn.NEEDS_HUMAN], c["approved"], c["needs_human"], c["unreviewed"], c["stale"]])
        add("<h3>Verdicts and approval status, per name</h3>")
        add(table(["Name", "Fields", "Passes", "Auto-fixable", "Needs a person", "Approved", "Flagged", "Unreviewed", "Approved but no longer passes"], rows, num=range(1, 9)))
        all_rules = sorted({r for t in fn.TARGETS for r in sm[t]["rules"]})
        if all_rules:
            add("<h3>Fields breaking each rule, per name</h3>")
            add(table(["Rule", "What it checks"] + [TARGET_TITLES[t] for t in fn.TARGETS],
                      [[r, fn.RULE_BY_ID[r].title] + [sm[t]["rules"].get(r, 0) for t in fn.TARGETS] for r in all_rules], num=range(2, 5)))
        steps = [("table_swap", "Step 2: table-name swap"), ("char_cleanup", "Step 3: character cleanup"), ("leading_digits", "Step 4: leading digits")]
        add("<h3>Names each step would change</h3>")
        add(table(["Step"] + [TARGET_TITLES[t] for t in fn.TARGETS], [[lab] + [sm[t]["steps"].get(k, 0) for t in fn.TARGETS] for k, lab in steps], num=range(1, 4)))
        add("<p>%d duplicate group(s); %d abandoned and %d consolidated field(s) excluded from the counts above.</p>" % (len(rv["clusters"]), sm["abandoned"], sm.get("consolidated", 0)))
    else:
        add("<p>Field results are not available until table names are approved.</p>")
    add("<p>Tables: %d in scope, %d approved so far.</p>" % (rv["in_scope_tables"], rv["in_scope_tables"] - len(rv["gate"]["pending"])))

    # ---- 3. settings
    section("Settings in force")
    add("<p>The Planning name, the Core name and the Core label are configured separately. Each row shows the value each name uses.</p>")
    add(table(["Setting"] + [TARGET_TITLES[t] for t in fn.TARGETS],
              [[lab] + [option_value(st, t, k) if t in on else "-" for t in fn.TARGETS] for k, lab, on in OPTION_ROWS]))
    add("<ul><li>Table number prefix: <b>%s</b>.</li><li>Descriptions: Airtable's own description is %s; an emoji is %s.</li></ul>" % (
        h({"end": "moved to the end", "drop": "dropped", "plain": "kept as plain digits"}[st.table_prefix]), "included" if st.description_include_airtable else "not included",
        "removed" if st.description_emoji_mode == "remove" else "replaced with its word"))

    # ---- 4. tables
    section("Table names")
    add("<p>Every in-scope table name is approved before any field is reviewed. A table with no scope decision is <b>undecided</b>, which is different from <b>out</b>.</p>")
    trs = []
    for t in rv["tables"]:
        scope = {"in": "In scope", "out": "Out", "undecided": "Undecided"}[t["scope"]]
        target = {"planning_only": "Planning only", "planning_and_core": "Planning plus Core"}.get(t.get("target"), "")
        if t["scope"] == "out":
            trs.append(([scope, "", lines(t["old"], small(t["table_id"])), "", "", "", lines("Skipped", small(t.get("scope_reason") or ""))], "out"))
            continue
        status = lines(tag("Approved", "st-approved"), small("%s %s" % (t.get("approved_by") or "", t.get("approved_at") or ""))) if t["approved"] else tag("Pending", "warn")
        trs.append(([scope, target, lines(t["old"], small(t["table_id"])), lines(verdict_tag(t["verdict"]), small(rule_names(t["rules"]))),
                     t["suggested"] if t["suggested"] != t["old"] else "(unchanged)", t["effective"] if t["approved"] else "", status],
                    "" if t["verdict"] == fn.PASS else ("auto" if t["verdict"] == fn.AUTO_FIXABLE else "human")))
    add(table(["Scope", "Target", "Table", "Check", "Proposed name", "Approved name", "Status"], trs))
    if rv["scope_warnings"]:
        wt = table(["Field", "In table", "Out-of-scope table", "How"],
                   [[w["field_name"], w["table_name"], w["out_table_name"], "links to it" if w["kind"] == "link" else "its name mentions it"] for w in rv["scope_warnings"]])
        add(box(Raw("<b>%d field(s) in in-scope tables link to, or name, a table that is out of scope.</b> A link to a dropped table has nowhere to point: bring the table back in "
                    "scope, drop the field, or change what it points at.%s" % (len(rv["scope_warnings"]), wt)), "warn"))

    if ready:
        # ---- 5. field grids
        section("Field names (one grid per name)")
        add("<p>One row per Airtable field, with the original name, the name you supplied (if any), the proposed repair or the approved name, the check, and the data presence "
            "from profiling (records with a value, empty, cardinality; a capped cardinality is shown with a plus sign, never as a count).</p>")
        for t in fn.TARGETS:
            add("<h3>%s</h3>" % h(TARGET_TITLES[t]))
            rows = []
            for f in live:
                r = f["targets"][t]
                final = lines(tag(r["approved_name"], "st-approved") if r["status"] == "approved" and r["approved_name"] else (r["proposed"] or ("-" if r["verdict"] == fn.PASS else "")),
                              tag("approved name no longer passes", "stale") if r["stale"] else "")
                rows.append(([f["table_name"], lines(f["original"], small(f["field_id"])), data_cell(f["presence"]),
                              r["candidate"] if r["source"] == "manual" else small("(Airtable name)"), final,
                              lines(verdict_tag(r["verdict"]), small(rule_names(r["rules"])), *[small(x) for x in r["reasons"]]),
                              tag(STATUS_TEXT[r["status"]], "st-" + r["status"] if r["status"] != "unreviewed" else "")],
                             "" if r["verdict"] == fn.PASS else ("auto" if r["verdict"] == fn.AUTO_FIXABLE else "human")))
            add(table(["Table", "Field", "Data", "Your name", "Approved / proposed", "Check", "Status"], rows))

        # ---- 6. duplicates
        section("Duplicate groups")
        if not rv["clusters"]:
            add("<p>No duplicate names remain.</p>")
        else:
            add("<p>A duplicate is a group problem: every member is shown with its original and repaired name side by side. Names are compared ignoring case, within one table, "
                "after the repairs.</p>")
            tname = {t["table_id"]: t["old"] for t in rv["tables"]}
            byid = {f["field_id"]: f for f in fields}
            for c in rv["clusters"]:
                add("<h3>%s in %s: %d fields share the name &ldquo;%s&rdquo;</h3>" % (h(TARGET_TITLES[c["target"]]), h(tname.get(c["table_id"], c["table_id"])), len(c["members"]), h(c["members"][0]["final"])))
                add(table(["Field", "Your name", "Repaired name", "Data", "Can be abandoned"], [
                    [lines(m["original"], small(m["field_id"])), m["candidate"] if byid[m["field_id"]]["targets"][c["target"]]["source"] == "manual" else small("(Airtable name)"), m["final"],
                     data_cell(m["presence"]), "Yes" if m["presence"]["can_abandon"] else (m["presence"]["abandon_blocked_because"] or "No")] for m in c["members"]]))

        # ---- 7. diff
        section("Your names against the rules")
        add("<p>Where a name you supplied differs from what the rules alone would produce from the Airtable name. This is the list of gaps between the manual work and the standard.</p>")
        drows = []
        for f in live:
            for t in fn.TARGETS:
                r = f["targets"][t]
                if r["source"] == "manual" and r["differs_from_rubric"]:
                    drows.append([TARGET_TITLES[t], lines(f["original"], small(f["field_id"])), r["candidate"], verdict_tag(r["verdict"]), r["rubric_name"], verdict_tag(r.get("rubric_verdict"))])
        add(table(["Name", "Original", "Your name", "Your name checks as", "The rules alone give", "That checks as"], drows) if drows else "<p>No differences: every name you supplied matches the rules, or none were supplied.</p>")

        # ---- 8. descriptions
        section("Descriptions")
        add("<p>Each description lists the original Airtable table and field names the data comes from (<code>Source: Airtable. Origins: Table.Field; ...</code>). Generated descriptions "
            "are recomputed from the schema; a hand-edited one is shown as edited.</p>")
        add(table(["Table", "Field", "Description", "State", "Problems"], [
            [f["table_name"], lines(f["original"], small(f["field_id"])), Raw('<span class="mono">%s</span>' % h(f["description"]["text"])), tag("Edited by hand" if f["description"]["edited"] else "Generated"),
             lines(*[small(p["message"]) for p in f["description"]["problems"]])] for f in live]))

        # ---- abandoned
        section("Abandoned fields")
        gone = [f for f in fields if f["abandoned"]]
        add("<p>Abandoning is a recorded human decision: a field with no value in any record, never abandoned automatically because an empty field can still be used by a form or an automation.</p>")
        add(table(["Table", "Field", "Data", "By", "When", "Why"], [[f["table_name"], lines(f["original"], small(f["field_id"])), data_cell(f["presence"]), f["abandon"]["by"], f["abandon"]["at"], f["abandon"]["reason"]]
                                                                     for f in gone]) if gone else "<p>None.</p>")

        # ---- consolidated
        section("Consolidated fields")
        add("<p>Several Airtable fields that become one Workfront field. The survivor carries forward; the merged members drop out of the name review and the duplicate checks, "
            "and the survivor's description lists every origin. The conflict rule says what the loader will do later when two members are both filled and differ.</p>")
        add(table(["Table", "Survivor", "Merged into it", "If both are filled and differ", "Why", "Decided by", "When"], [
            [g["table_name"], g["survivor_name"], lines(*[m["name"] for m in g["members"] if m["role"] == "merged"]), g["conflict_rule_text"], g["reason"], g["by"], g["at"]]
            for g in rv["groups"]]) if rv["groups"] else "<p>None.</p>")

    # ---- decisions
    section("Decisions recorded")
    if events["total"]:
        add("<p>%d recorded event(s) between %s and %s. The record is append-only: nothing is edited or removed.</p>" % (events["total"], h(events["first"]), h(events["last"])))
        label = {"approve": "Approved", "reject": "Withdrawn or set out of scope", "needs_human": "Flagged for a person", "abandon": "Field abandoned", "unabandon": "Field restored",
                 "import": "Names imported", "settings": "Settings saved"}
        add(table(["Kind of decision", "Count"], [[label.get(k, k), v] for k, v in events["by_type"]], num=[1]))
        add(table(["Reviewer", "Decisions"], [[a, v] for a, v in events["by_actor"]], num=[1]))
    else:
        add("<p>No decisions have been recorded yet.</p>")

    # ---- rules
    section("The rules")
    add("<p>The rules this report applies, in the order they run: table names, the table-name swap, character cleanup, leading digits, and last the duplicate check on the repaired names. "
        "Names are ASCII-safe because data exported to CSV without the right encoding corrupts anything else.</p>")
    add(table(["Name", "Maximum length", "Status of that limit"], [[TARGET_TITLES[t], rv_len(st, t), fn.PROFILES[t]["len_status"]] for t in fn.TARGETS]))
    add(table(["Rule", "Applies to", "Step", "What it checks", "Example"], [[
        lines(Raw("<b>%s</b>" % h(r.id)), r.title), ", ".join(TARGET_TITLES[t] for t in r.targets), r.step + (" (always checked)" if r.baseline else ""), r.text,
        Raw("<code>%s</code> &rarr; <code>%s</code>" % (h(r.bad), h(r.good)))] for r in fn.RULES]))
    add("<p>Verdicts: <b>Passes</b> complies. <b>Auto-fixable</b>: the only problems are listed special characters, whitespace characters, repeated or edge spaces, or a renamed table's "
        "approved name inside a field name; the repair is shown and is applied only when approved. <b>Needs a person</b>: everything else, with a proposed repair where one exists.</p>")

    # ---- method
    section("Method and checks")
    add("<ul><li>Names are checked by deterministic rules; no AI or language model is used at any point.</li>"
        "<li>The same schema, settings and decisions always give the same report; only the generated time changes.</li>"
        "<li>Suggested checks for an auditor: pick a few fields marked auto-fixable and confirm the proposed name by hand; confirm a duplicate group by searching the original names; "
        "confirm that an abandoned field has no values in the source; confirm that every approval has a reviewer and a date in the decision record.</li>"
        "<li>Limits Adobe does not document (the Core name and label lengths, reserved names, description length) are marked provisional wherever they appear.</li></ul>")
    add('<h3>Review sign-off</h3><table class="sign"><thead><tr><th>Reviewer</th><th>Role</th><th>Date</th><th>Signature</th></tr></thead><tbody>'
        "<tr><td></td><td></td><td></td><td></td></tr><tr><td></td><td></td><td></td><td></td></tr></tbody></table>")
    add("</main></body></html>")
    return "".join(P)


def rv_len(st, t):
    return st.opt(t, "max_len")
