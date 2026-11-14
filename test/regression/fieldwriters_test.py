"""Tests for fieldwriters.py: the field-writer matrix, the Fusion-driven marker and the two-way validation. Fabricated tables and names; no server or database."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import fieldwriters as fw
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])

# ---- the marker
check("no automation, no marker", fw.marker_text([]) is None)
check("a named scenario is shown by its scenario name", fw.marker_text([{"automation_name": "Late fee", "scenario": "Order sync"}]) == 'Fusion-driven: written by scenario "Order sync".')
check("an unnamed scenario falls back to the automation, flagged", fw.marker_text([{"automation_name": "Late fee", "scenario": None}]) == 'Fusion-driven: written by automation "Late fee" (scenario not named yet).')
two = [{"automation_name": "B", "scenario": "Zed"}, {"automation_name": "A", "scenario": "Alpha"}, {"automation_name": "A2", "scenario": "Alpha"}]
check("several sources are sorted and not repeated, so the same input gives the same text", fw.marker_text(two) == 'Fusion-driven: written by scenario "Alpha"; scenario "Zed".' and fw.marker_text(two[::-1]) == fw.marker_text(two))
check("a quote or a line break in a name cannot break the marker", fw.marker_text([{"automation_name": 'He said "go"\nnow', "scenario": None}]) == 'Fusion-driven: written by automation "He said \'go\' now" (scenario not named yet).')
m = fw.marker_text([{"automation_name": "Late fee", "scenario": "Order sync"}, {"automation_name": "Nudge", "scenario": None}])
p = fw.parse_marker(m + " Source: Airtable. Origins: Orders.Total.")
check("the marker is read back: scenarios, automations and whether every scenario is named", p == {"scenarios": ["Order sync"], "automations": ["Nudge"], "named": False, "well_formed": True}, p)
check("a description with no marker, or one that does not start with it, is not marked", fw.parse_marker("Source: Airtable. Origins: A.B.") is None and fw.parse_marker("Note. Fusion-driven: written by scenario \"X\".") is None and fw.parse_marker(None) is None)
check("a hand-edited marker still counts as marked, without names", fw.parse_marker("Fusion-driven: by hand") == {"scenarios": [], "automations": [], "named": False, "well_formed": False})
desc = "Source: Airtable. Origins: Orders.Total."
w = fw.with_marker(desc, [{"automation_name": "Late fee", "scenario": "Order sync"}])
check("the marker goes first, so a long lineage can never push it out", w.startswith('Fusion-driven: written by scenario "Order sync". Source: Airtable.') and w.endswith(desc))
check("putting the marker on again changes nothing, and a new source replaces the old one", fw.with_marker(w, [{"automation_name": "Late fee", "scenario": "Order sync"}]) == w
      and fw.with_marker(w, [{"automation_name": "x", "scenario": "Other"}]).startswith('Fusion-driven: written by scenario "Other". Source:'))
check("when no automation sets the field the marker is removed and the rest kept", fw.with_marker(w, []) == desc and fw.with_marker(desc, []) == desc and fw.strip_marker(w) == desc)
check("a name containing a full stop and space does not end the marker early", fw.strip_marker(fw.with_marker(desc, [{"automation_name": "Fix it. Now", "scenario": "Fix it. Now"}])) == desc)

# ---- the matrix
F = lambda fid, name, typ="singleLineText", t="tA", tn="Orders": {"field_id": fid, "field_name": name, "table_id": t, "table_name": tn, "base_id": "appW", "data_type": typ}
FIELDS = [F("f1", "Status", "singleSelect"), F("f2", "Total", "formula"), F("f3", "Notes"), F("f4", "Late fee", "currency"), F("f5", "Created", "createdTime"),
          F("f6", "Owner", "singleLineText"), F("f7", "Check", "rollup"), F("f8", "Amount", "number", "tB", "Invoices"), F("f9", "Number", "autoNumber", "tB", "Invoices")]
AUTOS = [{"spec_id": 10, "automation_id": "aLate", "automation_name": "Late fee", "target_scenario_name": "Order sync"},
         {"spec_id": 11, "automation_id": "aNudge", "automation_name": "Nudge", "target_scenario_name": None},
         {"spec_id": 12, "automation_id": "aOld", "automation_name": "Old version", "target_scenario_name": "Stale"}]
REFS = [{"spec_id": 10, "field_id": "f4", "role": "set"}, {"spec_id": 10, "field_id": "f1", "role": "condition"}, {"spec_id": 11, "field_id": "f6", "role": "set"},
        {"spec_id": 11, "field_id": "f4", "role": "set"}, {"spec_id": 10, "field_id": "f3", "role": "read"}, {"spec_id": 11, "field_id": "f2", "role": "set"},
        {"spec_id": 99, "field_id": "f8", "role": "set"}]      # spec 99 is an older saved version: not in AUTOS, so it must not count
FORMS = [{"spec_id": 20, "form_id": "fmIntake", "form_name": "Intake"}]
ITEMS = [{"spec_id": 20, "airtable_field_id": "f6", "item_type": "field"}, {"spec_id": 20, "airtable_field_id": "f3", "item_type": "hidden_field"},
         {"spec_id": 20, "airtable_field_id": "f1", "item_type": "heading"}, {"spec_id": 21, "airtable_field_id": "f8", "item_type": "field"}]
M = fw.build_matrix(FIELDS, AUTOS, REFS, FORMS, ITEMS)
E = {e["field_id"]: e for e in M}
check("entries are sorted by table then field name", [e["field_id"] for e in M] == ["f8", "f9", "f7", "f5", "f4", "f3", "f6", "f1", "f2"], [e["field_id"] for e in M])
check("a field set by an automation and no form is automation-only, with its sources", E["f4"]["category"] == "automation_only" and [a["automation_name"] for a in E["f4"]["writers"]["automation"]] == ["Late fee", "Nudge"] and E["f4"]["fusion"])
check("a field set by an automation and a form is mixed", E["f6"]["category"] == "mixed" and E["f6"]["writers"]["form"] == ["Intake"])
check("a field only read or used in a condition by an automation is not written by it", E["f3"]["fusion"] is False and E["f1"]["fusion"] is False)
check("a field only filled by a form (or by users) is plain data", E["f3"]["category"] == "user_plain" and E["f1"]["category"] == "user_plain" and E["f3"]["writers"]["form"] == ["Intake"] and E["f3"]["marker"] is None)
check("a heading is not a form field", E["f1"]["writers"]["form"] == [])
check("a formula or rollup set by no automation is formula-only", E["f7"]["category"] == "formula_only" and E["f7"]["writers"]["formula"] and E["f7"]["writers"]["user"] == "n/a" and E["f7"]["needs_decision"])
check("a computed field that an automation claims to set is flagged, not trusted", E["f2"]["category"] == "formula_only" and E["f2"]["anomalies"] and E["f2"]["fusion"])
check("an Airtable-filled field is system: no marker, nothing to decide", E["f5"]["category"] == "system" and not E["f5"]["needs_decision"] and E["f5"]["marker"] is None and E["f9"]["category"] == "system")
check("only the latest saved versions count: a reference from another version is ignored", E["f8"]["fusion"] is False and E["f8"]["writers"]["form"] == [] and E["f8"]["category"] == "user_plain")
check("a user is only ever possible, never confirmed from the specs", E["f4"]["writers"]["user"] == "possible" and E["f3"]["writers"]["user"] == "possible")
check("the marker text and scenarios of a field come from its automations", E["f4"]["marker"] == 'Fusion-driven: written by automation "Nudge" (scenario not named yet); scenario "Order sync".' and E["f4"]["scenarios"] == ["Order sync"])
check("the same input gives the same matrix whatever order it arrives in", fw.build_matrix(FIELDS[::-1], AUTOS[::-1], REFS[::-1], FORMS, ITEMS[::-1]) == M)

# ---- decisions
D = {"f4": {"decision": "fusion_owned", "decided_by": "JV", "decided_at": "2026-11-14 10:00:00", "reason": "scenario owns it"},
     "f7": {"decision": "fusion_owned", "decided_by": "JV", "decided_at": "x", "reason": "wrong kind"}, "f3": {"decision": "park", "decided_by": "JV", "decided_at": "x", "reason": "r"}}
M2 = fw.apply_decisions(fw.build_matrix(FIELDS, AUTOS, REFS, FORMS, ITEMS), D)
E2 = {e["field_id"]: e for e in M2}
check("a recorded decision that fits is attached and settles the field", E2["f4"]["decision"]["decision"] == "fusion_owned" and not E2["f4"]["needs_decision"])
check("a decision that does not fit the field is ignored and flagged, never applied", E2["f7"]["decision"] is None and E2["f7"]["needs_decision"] and any("no longer fits" in a for a in E2["f7"]["anomalies"]) and E2["f3"]["decision"] is None)
M3 = fw.apply_decisions(fw.build_matrix(FIELDS, AUTOS, REFS, FORMS, ITEMS), {"f4": {"decision": "users_also_edit", "decided_by": "JV", "decided_at": "x", "reason": "r"}})
check("'people also edit it' makes an automation-only field mixed; the marker stays", {e["field_id"]: e for e in M3}["f4"]["category"] == "mixed" and {e["field_id"]: e for e in M3}["f4"]["fusion"])
sm = fw.summary(M2)
check("the summary counts fields, categories, Fusion fields, open decisions and unnamed scenarios", sm["fields"] == 9 and sm["categories"] == {"formula_only": 2, "automation_only": 1, "mixed": 1, "user_plain": 3, "system": 2}
      and sm["fusion_fields"] == 3 and sm["needs_decision"] == 2 and sm["unnamed_scenarios"] == 2 and sm["anomalies"] == 3, sm)

# ---- validation, in both directions
good4 = fw.with_marker("Source: Airtable. Origins: Orders.Late fee.", [{"automation_name": "Nudge", "scenario": None}, {"automation_name": "Late fee", "scenario": "Order sync"}])
pulled = {"core": {"f4": good4, "f6": "Source: Airtable. Origins: Orders.Owner.", "f3": 'Fusion-driven: written by scenario "Ghost".', "f1": "plain", "zz": 'Fusion-driven: written by scenario "X".'},
          "planning": {e["field_id"]: (good4 if e["field_id"] == "f4" else fw.with_marker("d", [{"automation_name": "n", "scenario": "Order sync"}]) if e["field_id"] == "f6" else "d") for e in M}}
r = fw.validate(M, pulled)
kinds = {(f["system"], f["kind"], f["field_id"]) for f in r["findings"]}
core = {(f["kind"], f["field_id"]) for f in r["findings"] if f["system"] == "core"}
check("core: exactly the expected findings in both directions", core == {("missing_marker", "f6"), ("unexpected_marker", "f3"), ("unknown_marked_field", "zz"),
      ("not_pulled", "f2"), ("not_pulled", "f5"), ("not_pulled", "f7"), ("not_pulled", "f8"), ("not_pulled", "f9")}, sorted(core))
check("a field an automation sets but whose description has no marker is reported (matrix to description)", ("missing_marker", "f6") in core)
check("a marked field that no captured automation sets is reported (description to matrix)", ("unexpected_marker", "f3") in core)
check("a marked field that is not in the schema at all is reported", ("unknown_marked_field", "zz") in core)
planning = {(f["kind"], f["field_id"]) for f in r["findings"] if f["system"] == "planning"}
check("planning: only the field whose marker is missing is reported", planning == {("missing_marker", "f2")}, sorted(planning))
check("a correct marker passes both checks", not any(f["field_id"] == "f4" for f in r["findings"]))
check("a field missing from a pull is counted as not pulled, not as a failure", ("not_pulled", "f9") in core and r["counts"]["not_pulled"] == 5 and not r["ok"])
check("only the systems given are checked", fw.validate(M, {"planning": pulled["planning"]})["systems"] == ["planning"] and all(f["system"] == "planning" for f in fw.validate(M, {"planning": pulled["planning"]})["findings"]))
perfect = {"core": {e["field_id"]: fw.with_marker("d", e["writers"]["automation"]) for e in M}}
check("when the descriptions agree with the matrix in both directions the result is ok", fw.validate(M, perfect)["ok"] and not fw.validate(M, perfect)["findings"])
mism = {"core": dict(perfect["core"], f4=fw.with_marker("d", [{"automation_name": "x", "scenario": "Wrong name"}]))}
check("a marker that names a different scenario than the recorded automation is a mismatch", [f["kind"] for f in fw.validate(M, mism)["findings"]] == ["scenario_mismatch"])
check("findings are sorted, so the same input gives the same report whatever order the matrix arrives in", fw.validate(M, pulled) == fw.validate(M[::-1], pulled))

print("ALL PASS" if ok else "FAILURES")
sys.exit(0 if ok else 1)
