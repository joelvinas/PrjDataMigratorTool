"""Pure-function tests for fieldnames.py. Fabricated names only; no server, database or network needed."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import fieldnames as fn
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from fieldnames import Settings, PASS, AUTO_FIXABLE, NEEDS_HUMAN

ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else x)

S = Settings()
def ev(name, target="planning_name", s=S, field=None, tmap=None, fields=None, base_tables=None):
    return fn.evaluate_name(name, target, s, field or {"field_id": "f1", "table_id": "t1"}, fields or {}, tmap or {}, base_tables or [])

# ---- verdicts
r = ev("Budget")
check("clean name passes", r["verdict"] == PASS and r["proposed"] is None and r["rules"] == [], r)
r = ev("Total  Cost ")
check("double and edge spaces need approval; repair is proposed, never applied", r["verdict"] == NEEDS_HUMAN and r["suggested"] is None and r["proposed"] == "Total Cost" and r["candidate"] == "Total  Cost "
      and {"DOUBLE_SPACE", "EDGE_SPACE"} <= set(r["rules"]), r)
r = ev("x" * 251)
check("over 250 chars: needs human, no fix", r["verdict"] == NEEDS_HUMAN and "TOO_LONG" in r["rules"] and r["proposed"] is None)
check("250 chars passes for Planning, 251 fails; Core name allows 255", ev("x" * 250)["verdict"] == PASS and ev("x" * 255, "core_name")["verdict"] == PASS
      and ev("x" * 256, "core_name")["verdict"] == NEEDS_HUMAN)
r = ev("   ")
check("blank name needs human", r["verdict"] == NEEDS_HUMAN and "EMPTY" in r["rules"], r)

# ---- special characters: the AUTO_DELETE_CHARS list is AUTO_FIXABLE (deleted with nothing); anything else needs approval
AUTO = "'\"`~!@#$%^&*()[]{};:<>,./?"
for ch in AUTO:
    r = ev("Alpha%sBeta" % ch)
    check("auto-fixable: %r is deleted with nothing" % ch, r["verdict"] == AUTO_FIXABLE and r["suggested"] == "AlphaBeta" and "SPECIAL_CHAR" in r["rules"], r)
BS = chr(92)
for ch in "_" + BS + "|+=":
    r = ev("Alpha%sBeta" % ch)
    check("not on the auto list, needs approval: %r" % ch, r["verdict"] == NEEDS_HUMAN and r["suggested"] is None and "SPECIAL_CHAR" in r["rules"]
          and r["proposed"] is not None and not fn.violations(r["proposed"], "planning_name", S), r)
check("underscore and backslash are proposed as a space", ev("a_b")["proposed"] == "a b" and ev("a" + BS + "b")["proposed"] == "a b")
check("a dot, slash and hash are deleted, not turned into spaces", ev("v1.2/x#")["suggested"] == "v12x")
check("spaces left by a deletion are closed up (part of the same auto fix)", ev("Terms & Conditions")["suggested"] == "Terms Conditions" and ev("Terms & Conditions")["verdict"] == AUTO_FIXABLE)
check("auto fix plus an unrelated problem is not auto", ev("Total  #Cost")["verdict"] == NEEDS_HUMAN and ev("1Q #Plan")["verdict"] == NEEDS_HUMAN)
check("deleting characters that exposes a quarter token is not auto", ev("1#Q Plan")["verdict"] == NEEDS_HUMAN)
check("auto fix that would leave an empty name needs a person", ev("#?")["verdict"] == NEEDS_HUMAN)
check("hyphen is allowed", ev("Pre-Approved")["verdict"] == PASS)

# ---- unicode and emoji
r = ev("Budget \U0001F680")
check("emoji detected; removal is proposed, needs approval", r["verdict"] == NEEDS_HUMAN and "NON_ASCII" in r["rules"] and r["proposed"] == "Budget", r)
check("accents are transliterated", ev("Café Résumé")["proposed"] == "Cafe Resume")
check("smart quotes and en dash: quotes removed, dash kept as hyphen", ev("It’s – ok")["proposed"] == "Its - ok", ev("It’s – ok"))
check("non-breaking space becomes a normal space", ev("a b")["proposed"] == "a b")
check("zero width space is detected and removed", ev("ab​c")["proposed"] == "abc" and "NON_ASCII" in ev("ab​c")["rules"])
check("tab and newline are replaced", ev("a\tb\nc")["proposed"] == "a b c")
check("composed and decomposed accents give the same result", ev("é")["proposed"] == ev("é")["proposed"] == "e")
check("CJK that cannot be transliterated is removed", ev("Budget 中文")["proposed"] == "Budget")
check("name made only of emoji is needs human (empty after repair)", ev("\U0001F680")["verdict"] == NEEDS_HUMAN)

# ---- quarter convention
check("1Q becomes Q1 as a proposal that needs approval", ev("1Q Budget")["proposed"] == "Q1 Budget" and ev("1Q Budget")["verdict"] == NEEDS_HUMAN)
check("2Q in the middle", ev("Plan 2Q Review")["proposed"] == "Plan Q2 Review")
check("1Q24 becomes Q1 24", ev("Sales 1Q24")["proposed"] == "Sales Q1 24")
r = ev("Plan 1Quality")
check("1Quality inside a name is never rewritten", "QUARTER_STYLE" not in r["rules"] and r["verdict"] == PASS, r)
check("21Q is not a quarter", "QUARTER_STYLE" not in ev("Item 21Q")["rules"])
check("5Q is not a quarter", "QUARTER_STYLE" not in ev("Item 5Q")["rules"])
check("Q1 already correct", ev("Q1 Budget")["verdict"] == PASS)
check("quarter style detection", [fn.quarter_style(x) for x in ("Q1 Plan", "1Q Plan", "Q1 and 2Q", "Plan")] == ["Q1", "1Q", "mixed", "none"])

# ---- labels keep the client's convention
r = ev("1Q Budget (Draft)", "core_label")
check("label keeps 1Q; parentheses are unsupported in labels (Adobe), so deleting them is auto-fixable", r["verdict"] == AUTO_FIXABLE and r["suggested"] == "1Q Budget Draft" and r["quarter_style"] == "1Q", r)
for ch in "[](){}:":
    check("Adobe-unsupported label character %r is auto-fixable" % ch, ev("Alpha%sBeta" % ch, "core_label")["verdict"] == AUTO_FIXABLE, ev("Alpha%sBeta" % ch, "core_label"))
check("characters Adobe does not list stay in a label", all(ev("Alpha%sBeta" % ch, "core_label")["verdict"] == PASS for ch in "#_&/" + chr(92) + "|"))
check("label tab and newline are flagged (control characters)", ev("a	b", "core_label")["verdict"] == NEEDS_HUMAN and ev("a	b", "core_label")["proposed"] == "a b")
r = ev("Budget \U0001F680 {x}", "core_label")
check("label drops emoji and blocklisted characters only", r["proposed"] == "Budget x" and r["verdict"] == NEEDS_HUMAN, r)
check("label converts 1Q only when the preset says so", ev("1Q Plan", "core_label", Settings(label_keep_quarter=False))["proposed"] == "Q1 Plan")
check("label may start with a digit", ev("500K Fund", "core_label")["verdict"] == PASS)

# ---- leading digits
r = ev("500K Acme Fund")
check("leading number moves to the end (preset), needs approval", r["verdict"] == NEEDS_HUMAN and r["proposed"] == "Acme Fund 500K", r)
r = ev("500K Acme Fund", s=Settings(digit_style="prefix_word", digit_word="Fuel"))
check("prefix word preset", r["proposed"] == "Fuel 500K Acme Fund", r)
r = ev("500K Acme Fund", s=Settings(digit_style="prefix_word"))
check("prefix preset without a word needs human", r["verdict"] == NEEDS_HUMAN and r["proposed"] is None, r)
check("a name that is only a number needs human", ev("2024")["verdict"] == NEEDS_HUMAN)
r = ev("500K Acme Fund", s=Settings(leading_digits=False))
check("leading digit baseline still flags when the step is off", r["verdict"] == NEEDS_HUMAN and "LEADING_DIGIT" in r["rules"] and "switched off" in " ".join(r["reasons"]), r)
check("1Q at the start is repaired before the leading-digit step sees it", ev("1Q Budget")["proposed"] == "Q1 Budget")

# ---- toggles
r = ev("A#B", s=Settings(char_cleanup=False))
check("cleanup off: special character still flagged, no fix", r["verdict"] == NEEDS_HUMAN and "SPECIAL_CHAR" in r["rules"] and r["proposed"] is None)
check("cleanup off: 1Q is not checked (not baseline)", ev("Plan 1Q", s=Settings(char_cleanup=False))["verdict"] == PASS)

# ---- table names
tabs = [{"table_id": "tA", "base_id": "b", "table_name": "1.2.3 Orders"}, {"table_id": "tB", "base_id": "b", "table_name": "PO#s"},
        {"table_id": "tC", "base_id": "b", "table_name": "Plain"}, {"table_id": "tD", "base_id": "b", "table_name": "500K Funds"}]
check("table prefix: end", fn.clean_table_name("1.2.3 Orders", Settings(table_prefix="end")) == "Orders 1-2-3")
check("table prefix: drop", fn.clean_table_name("1.2.3 Orders", Settings(table_prefix="drop")) == "Orders")
check("table prefix: plain keeps digits first", fn.clean_table_name("1.2.3 Orders", Settings(table_prefix="plain")) == "1-2-3 Orders")
check("PO#s becomes POs", fn.clean_table_name("PO#s", S) == "POs")
tm = fn.table_names(tabs, S)
check("table map has suggested names and verdicts", tm["tA"]["suggested"] == "Orders 1-2-3" and tm["tB"]["verdict"] == AUTO_FIXABLE and tm["tC"]["verdict"] == PASS
      and tm["tD"]["suggested"] == "Funds 500K", {k: v["suggested"] for k, v in tm.items()})
tm2 = fn.table_names(tabs, S, approved={"tB": "Purchase Orders"})
check("an approved table name wins over the suggestion", tm2["tB"]["effective"] == "Purchase Orders" and tm2["tB"]["approved"] and tm2["tB"]["suggested"] == "POs")
tm3 = fn.table_names([{"table_id": "x", "base_id": "b", "table_name": "Orders"}, {"table_id": "y", "base_id": "b", "table_name": "orders"}], S)
check("tables colliding after cleanup are flagged", all(v["verdict"] == NEEDS_HUMAN and "DUPLICATE" in v["rules"] for v in tm3.values()))
tm4 = fn.table_names([{"table_id": "x", "base_id": "b1", "table_name": "Orders"}, {"table_id": "y", "base_id": "b2", "table_name": "Orders"}], S)
check("same table name in different bases is fine", all(v["verdict"] == PASS for v in tm4.values()))

# ---- table-name swap through the schema
fields = [
    {"field_id": "fLink", "table_id": "tC", "table_name": "Plain", "field_name": "PO#s", "data_type": "multipleRecordLinks", "linked_table_id": "tB"},
    {"field_id": "fLook", "table_id": "tC", "table_name": "Plain", "field_name": "PO Number (from PO#s)", "data_type": "multipleLookupValues", "source_link_field_id": "fLink"},
    {"field_id": "fLook2", "table_id": "tC", "table_name": "Plain", "field_name": "Total (from Other)", "data_type": "multipleLookupValues", "source_link_field_id": "fLink"},
    {"field_id": "fForm", "table_id": "tC", "table_name": "Plain", "field_name": "Mentions PO#s in a formula", "data_type": "formula"},
    {"field_id": "fShort", "table_id": "tC", "table_name": "Plain", "field_name": "Reposition note", "data_type": "singleLineText"},
]
res = fn.run(fields, tabs, S)
lk = res["fields"]["fLook"]["targets"]["planning_name"]
check("PO Number (from PO#s) resolves through the schema; proposal needs approval", lk["verdict"] == NEEDS_HUMAN and lk["proposed"] == "PO Number from POs" and "TABLE_REF" in lk["rules"], lk)
res_nc = fn.run(fields, tabs, Settings(char_cleanup=False))
lk = res_nc["fields"]["fLook"]["targets"]["planning_name"]
check("with cleanup off the swap alone gives PO Number (from POs) (parentheses still flagged)", lk["final"] == "PO Number (from POs)" and lk["verdict"] == NEEDS_HUMAN, lk)
check("the swap never touches a lookup whose text does not contain the old table name", res["fields"]["fLook2"]["targets"]["planning_name"]["steps"].get("table_swap") is False)
fm = res["fields"]["fForm"]["targets"]["planning_name"]
check("a non-schema field that mentions a renamed table is ambiguous: needs human", fm["verdict"] == NEEDS_HUMAN and "TABLE_REF" in fm["rules"] and "ambiguous" in " ".join(fm["reasons"]), fm)
check("a short table name inside another word is not rewritten", res["fields"]["fShort"]["targets"]["planning_name"]["verdict"] == PASS)
tabs_s = [{"table_id": "t1", "base_id": "b", "table_name": "PO"}, {"table_id": "t2", "base_id": "b", "table_name": "PO#s"}]
check("whole names only: PO inside POs is not matched", fn._whole("PO").search("POs list") is None and fn._whole("PO").search("(from PO)") is not None)
res_off = fn.run(fields, tabs, Settings(table_swap=False))
check("swap off: no TABLE_REF", "TABLE_REF" not in res_off["fields"]["fLook"]["targets"]["planning_name"]["rules"])
check("link field itself resolves through its own linked table", "TABLE_REF" in res["fields"]["fLink"]["targets"]["planning_name"]["rules"]
      and res["fields"]["fLink"]["targets"]["planning_name"]["final"] == "POs")

# ---- labels get the table-name swap too
fl = [{"field_id": "lLink", "table_id": "tC", "field_name": "PO#s", "data_type": "multipleRecordLinks", "linked_table_id": "tB"},
      {"field_id": "lLook", "table_id": "tC", "field_name": "PO Number (from PO#s)", "data_type": "multipleLookupValues", "source_link_field_id": "lLink"}]
rl = fn.run(fl, tabs, S)
lab = rl["fields"]["lLook"]["targets"]["core_label"]
check("label follows the renamed table through the schema, then Adobe-unsupported parentheses go", lab["final"] == "PO Number from POs" and "TABLE_REF" in lab["rules"] and lab["verdict"] == NEEDS_HUMAN, lab)
check("name and label both get the swap", rl["fields"]["lLook"]["targets"]["planning_name"]["final"] == "PO Number from POs" and rl["fields"]["lLook"]["targets"]["core_name"]["final"] == "PO Number from POs")
check("swap off leaves the label's table name alone", "TABLE_REF" not in fn.run(fl, tabs, Settings(table_swap=False))["fields"]["lLook"]["targets"]["core_label"]["rules"])

# ---- duplicates
dup = [{"field_id": "d1", "table_id": "tX", "field_name": "Budget", "data_type": "number", "row_count": 10, "null_count": 0, "cardinality": 5, "profiled": True},
       {"field_id": "d2", "table_id": "tX", "field_name": "budget", "data_type": "number", "row_count": 10, "null_count": 10, "cardinality": 0, "profiled": True},
       {"field_id": "d3", "table_id": "tX", "field_name": "Budget!", "data_type": "number", "row_count": 10, "null_count": 4, "cardinality": 1000, "cardinality_is_capped": 1, "profiled": True},
       {"field_id": "d4", "table_id": "tY", "field_name": "Budget", "data_type": "number", "profiled": True}]
dt = [{"table_id": "tX", "base_id": "b", "table_name": "X"}, {"table_id": "tY", "base_id": "b", "table_name": "Y"}]
res = fn.run(dup, dt, S)
check("three-way cluster, case-insensitive, includes a collision created by the cleanup",
      len(res["clusters"]) == 2 and [len(c["members"]) for c in res["clusters"] if c["target"] == "planning_name"] == [3], [(c["target"], len(c["members"])) for c in res["clusters"]])
check("every member is needs human and carries DUPLICATE", all(res["fields"][i]["targets"]["planning_name"]["verdict"] == NEEDS_HUMAN
      and "DUPLICATE" in res["fields"][i]["targets"]["planning_name"]["rules"] for i in ("d1", "d2", "d3")))
check("same name in another table is not a duplicate", res["fields"]["d4"]["targets"]["planning_name"]["verdict"] == PASS)
check("labels are not duplicate-checked", all("DUPLICATE" not in res["fields"][i]["targets"]["core_label"]["rules"] for i in ("d1", "d2")))
mem = {m["field_id"]: m for m in res["clusters"][0]["members"]}
check("cluster members show original and repaired side by side", mem["d3"]["original"] == "Budget!" and mem["d3"]["final"] == "Budget")
check("capped cardinality shows with a plus sign", mem["d3"]["presence"]["cardinality_display"] == "1,000+" and mem["d1"]["presence"]["cardinality_display"] == "5")
check("zero populated, profiled field may be abandoned", mem["d2"]["presence"]["can_abandon"] and not mem["d1"]["presence"]["can_abandon"])
res = fn.run(dup, dt, S, abandoned=["d2"])
c = [c for c in res["clusters"] if c["target"] == "planning_name"][0]
check("abandoning one member shrinks the cluster and the check reruns", [m["field_id"] for m in c["members"]] == ["d1", "d3"] and res["summary"]["abandoned"] == 1)
res = fn.run(dup, dt, S, abandoned=["d2", "d3"])
check("abandoning down to one member resolves the cluster", not res["clusters"] and res["fields"]["d1"]["targets"]["planning_name"]["verdict"] == PASS)
check("an abandoned field has no verdicts", res["fields"]["d2"]["abandoned"] and res["fields"]["d2"]["targets"] == {})

# ---- data presence
check("unprofiled field never offers abandon", fn.presence({"data_type": "number", "row_count": 0, "null_count": 0})["can_abandon"] is False)
check("checkbox never offers abandon", fn.presence({"data_type": "checkbox", "row_count": 5, "null_count": 0, "profiled": True})["can_abandon"] is False)
check("capped 5 digit cardinality formats with comma and plus", fn.presence({"cardinality": 50000, "cardinality_is_capped": 1, "profiled": True})["cardinality_display"] == "50,000+")

# ---- manual names are validated, never overwritten, and diffed against the rubric
mf = [{"field_id": "m1", "table_id": "tM", "field_name": "1Q Review (old)", "data_type": "text", "profiled": True},
      {"field_id": "m2", "table_id": "tM", "field_name": "Other", "data_type": "text", "profiled": True}]
res = fn.run(mf, [{"table_id": "tM", "base_id": "b", "table_name": "M"}], S, manual={"m1": {"planning_name": "Q1 Review", "core_label": "1Q Review old"}})
m1 = res["fields"]["m1"]["targets"]
check("a compliant manual name passes and is kept", m1["planning_name"]["verdict"] == PASS and m1["planning_name"]["final"] == "Q1 Review" and m1["planning_name"]["source"] == "manual")
check("manual name that differs from the rubric is shown in the diff", m1["planning_name"]["differs_from_rubric"] is True and m1["planning_name"]["rubric_name"] == "Q1 Review old")
check("manual label equal to the rubric result is not a difference", m1["core_label"]["differs_from_rubric"] is False and m1["core_label"]["rubric_name"] == "1Q Review old", m1["core_label"])
check("targets without a manual name use the original", m1["core_name"]["source"] == "original")
res = fn.run(mf, [{"table_id": "tM", "base_id": "b", "table_name": "M"}], S, manual={"m1": {"planning_name": "Bad_Name?"}})
check("a non-compliant manual name gets a suggestion beside it, not an overwrite", res["fields"]["m1"]["targets"]["planning_name"]["candidate"] == "Bad_Name?"
      and res["fields"]["m1"]["targets"]["planning_name"]["proposed"] == "Bad Name")

# ---- scope, determinism, summary, rules document
res = fn.run(dup, dt, S, out_of_scope_tables=["tX"])
check("out-of-scope tables are skipped", set(res["fields"]) == {"d4"} and not res["clusters"])
a, b = fn.run(dup, dt, S), fn.run(dup, dt, S)
check("deterministic: same input, same output", a == b)
sm = fn.run(dup, dt, S)["summary"]["planning_name"]
check("summary counts per verdict and per rule", sm["verdicts"][NEEDS_HUMAN] == 3 and sm["rules"]["DUPLICATE"] == 3 and sm["rules"]["SPECIAL_CHAR"] == 1, sm)
check("summary counts how many names a step touched", sm["steps"]["char_cleanup"] == 1, sm)
doc = fn.rules_markdown()
check("rules document lists every rule and the allowlist wording", all(("### %s" % r.id) in doc for r in fn.RULES) and "allowlist" in doc and "whitelist" not in doc.lower())
check("every rule has a working example (bad breaks it or is descriptive)", all(r.bad and r.good for r in fn.RULES))
try:
    Settings(table_prefix="nope"); bad = False
except ValueError:
    bad = True
check("invalid preset raises", bad)
sys.exit(0 if ok else 1)
