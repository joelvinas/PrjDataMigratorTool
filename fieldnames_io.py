#!/usr/bin/env python3
"""Reads the schema, imports the renamed names from a CSV, runs fieldnames.py and writes the reports. Standard library only.

    python fieldnames_io.py --db db/migration.db --base appXXXXXXXXXXXXXX --import renames.csv --out out/names

The database is opened READ-ONLY. Only schema metadata and profile counts are read; record values are never touched. The reports hold
names and counts only. Nothing here changes a name: every repair is a proposal in the report for a person to approve.

Import CSV headers (case does not matter): field_id, old_name, new_name, new_label, old_dataType, new_dataType.
  field_id        the stable Airtable field ID (the key)
  old_name        the Airtable name, used to detect a stale spreadsheet
  new_name        validated as BOTH the Planning name and the Core name (they are separate rule sets)
  new_label       validated as the Core label
  old_dataType    compared (loosely) with the Airtable type in the database
  new_dataType    the target Workfront data type, carried into the report
"""
import argparse, csv, io, os, pathlib, re, sqlite3, sys
import fielddesc as fd
import fieldnames as fn

COLUMNS = ("field_id", "old_name", "new_name", "new_label", "old_datatype", "new_datatype")


def decode_csv(data):
    """bytes -> (text, note). UTF-8 (with or without a BOM) first; if that fails the file is read as Windows-1252 and the note says so,
    because a spreadsheet saved without a UTF-8 encoding may have changed non-ASCII characters."""
    try:
        return data.decode("utf-8-sig"), ""
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "File is not UTF-8; read as Windows-1252. Non-ASCII characters may not be what the spreadsheet showed."


def _norm_type(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def parse_import(text, db_fields):
    """Match the spreadsheet to the database by field ID. Returns {manual, target_types, problems, rows}.
    problems: list of {kind, severity (error|warn|info), field_id, detail}."""
    by_id = {f["field_id"]: f for f in db_fields}
    problems, manual, target_types, seen = [], {}, {}, set()
    def prob(kind, sev, fid, detail):
        problems.append({"kind": kind, "severity": sev, "field_id": fid or "", "detail": detail})
    reader = csv.DictReader(io.StringIO(text, newline=""))
    cols = {(h or "").strip().lower(): h for h in (reader.fieldnames or [])}
    if "field_id" not in cols:
        prob("MISSING_COLUMN", "error", "", "The file has no field_id column; nothing was imported.")
        return {"manual": {}, "target_types": {}, "problems": problems, "rows": 0}
    for c in COLUMNS[1:]:
        if c not in cols:
            prob("MISSING_COLUMN", "warn", "", "No %s column." % c)
    get = lambda row, c: (row.get(cols[c]) or "").strip() if c in cols else ""
    n = 0
    for n, row in enumerate(reader, start=2):               # row 1 is the header
        fid = get(row, "field_id")
        if not fid:
            prob("BLANK_FIELD_ID", "error", "", "Row %d has no field_id." % n)
            continue
        if fid in seen:
            prob("DUPLICATE_ID", "error", fid, "Row %d repeats a field_id already seen; the first row is used." % n)
            continue
        seen.add(fid)
        f = by_id.get(fid)
        if f is None:
            prob("UNMATCHED_ID", "error", fid, "Row %d: this field_id is not in the database for the chosen base." % n)
            continue
        if "old_name" in cols and get(row, "old_name") != f["field_name"]:
            prob("OLD_NAME_MISMATCH", "warn", fid, "Spreadsheet says '%s'; the database has '%s'. The field may have been renamed in Airtable." % (get(row, "old_name"), f["field_name"]))
        if "old_datatype" in cols and get(row, "old_datatype") and _norm_type(get(row, "old_datatype")) != _norm_type(f["data_type"]):
            prob("OLD_TYPE_MISMATCH", "warn", fid, "Spreadsheet says '%s'; the database has '%s'." % (get(row, "old_datatype"), f["data_type"]))
        nm, lb = get(row, "new_name"), get(row, "new_label")
        if nm or lb:
            manual[fid] = {"planning_name": nm, "core_name": nm, "core_label": lb}
        if not nm:
            prob("BLANK_NEW_NAME", "warn", fid, "No new_name; the Airtable name is validated instead.")
        if not lb:
            prob("BLANK_NEW_LABEL", "info", fid, "No new_label; the Airtable name is validated as the label.")
        if get(row, "new_datatype"):
            target_types[fid] = get(row, "new_datatype")
    for fid in sorted(set(by_id) - seen):
        prob("MISSING_FROM_IMPORT", "warn", fid, "'%s' is in the database but not in the spreadsheet." % by_id[fid]["field_name"])
    return {"manual": manual, "target_types": target_types, "problems": problems, "rows": len(seen)}


def load_db(path, base_id):
    """Read-only. Returns {fields, tables, approved_tables, abandoned, saved_descriptions, notes}. Fields no longer in the source are left out."""
    import fieldnames_store as store                      # imported here: the store imports this module
    uri = pathlib.Path(path).resolve().as_uri() + "?mode=ro"
    c = sqlite3.connect(uri, uri=True)
    c.row_factory = sqlite3.Row
    try:
        data = store.load_fields(c, base_id)
        approved, abandoned, saved, notes = {}, [], {}, []
        groups = store._live_groups(store.read_groups(c, base_id)[0], data)
        try:
            approved = {r[0]: r[1] for r in c.execute("SELECT airtable_table_id, new_table_name FROM table_names WHERE status='approved' AND new_table_name IS NOT NULL")}
            abandoned = [r[0] for r in c.execute("SELECT airtable_field_id FROM field_names WHERE abandoned=1")]
            saved = {r[0]: r[1] for r in c.execute("SELECT airtable_field_id, description FROM field_names WHERE description IS NOT NULL")}
        except sqlite3.OperationalError:
            notes.append("Migration 007 is not applied in this database: no approved table names, abandon decisions or saved descriptions were read.")
        return {"fields": data["fields"], "tables": data["tables"], "approved_tables": approved, "abandoned": abandoned, "saved_descriptions": saved, "groups": groups, "notes": notes}
    finally:
        c.close()


# ---------------------------------------------------------------- reports

def _safe(v):
    """Excel runs a cell that starts with = + - @ as a formula; a leading apostrophe stops that."""
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


def _csv(header, rows):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(header)
    for r in rows:
        w.writerow([_safe(x) for x in r])
    return out.getvalue()


def build_reports(result, fields, imp=None, notes=(), descriptions=None, consolidated=None):
    """-> {filename: text}. Pure: the same input gives the same text. descriptions: {field_id: {text, source, problems}}; when omitted the
    generated lineage description is used."""
    imp = imp or {"manual": {}, "target_types": {}, "problems": []}
    consolidated = consolidated or {}
    fbi = {f["field_id"]: f for f in fields}
    if descriptions is None:
        descriptions = {f["field_id"]: {"text": fd.generate_description(f, fbi), "source": "generated", "problems": []} for f in fields}
    T = fn.TARGETS
    head = ["table_id", "table_name", "field_id", "original_name", "airtable_type", "imported_new_name", "imported_new_label", "target_type"]
    for t in T:
        head += [t + "_verdict", t + "_rules", t + "_proposed_repair", t + "_reasons"]
    head += ["label_quarter_style", "rows_with_value", "null_count", "cardinality", "profiled", "can_abandon", "abandoned", "description", "description_source", "description_problems", "consolidated_into"]
    rows = []
    for fid, e in result["fields"].items():
        f, m, p = fbi[fid], imp["manual"].get(fid, {}), e["presence"]
        row = [e["table_id"], f["table_name"], fid, e["original"], f["data_type"], m.get("planning_name", ""), m.get("core_label", ""), imp["target_types"].get(fid, "")]
        for t in T:
            r = e["targets"].get(t)
            row += [r["verdict"], " ".join(r["rules"]), r["proposed"] or "", " | ".join(r["reasons"])] if r else ["CONSOLIDATED" if fid in consolidated else "ABANDONED", "", "", ""]
        lab = e["targets"].get("core_label")
        row += [lab["quarter_style"] if lab else "", p["populated"], p["null_count"], p["cardinality_display"], "yes" if p["profiled"] else "no",
                "yes" if p["can_abandon"] else "no", "yes" if e["abandoned"] and fid not in consolidated else "no"]
        d = descriptions.get(fid) or {"text": "", "source": "", "problems": []}
        row += [d["text"], d["source"], " ".join(d["problems"]), consolidated.get(fid, "")]
        rows.append(row)
    out = {"names_report.csv": _csv(head, rows)}
    d = []
    for fid, e in result["fields"].items():
        for t, r in e["targets"].items():
            if r["source"] == "manual" and r["differs_from_rubric"]:
                d.append([fid, t, e["original"], r["candidate"], r["verdict"], r["rubric_name"], r["rubric_verdict"]])
    out["names_diff.csv"] = _csv(["field_id", "target", "original_name", "your_name", "your_name_verdict", "rubric_name", "rubric_verdict"], d)
    c = []
    for cl in result["clusters"]:
        for m in cl["members"]:
            p = m["presence"]
            c.append([cl["cluster_id"], cl["target"], cl["table_id"], m["field_id"], m["original"], m["candidate"], m["final"], p["data_type"], p["populated"],
                      p["null_count"], p["cardinality_display"], "yes" if p["profiled"] else "no", "yes" if p["can_abandon"] else "no"])
    out["names_duplicates.csv"] = _csv(["group", "target", "table_id", "field_id", "original_name", "your_name", "repaired_name", "airtable_type",
                                         "rows_with_value", "null_count", "cardinality", "profiled", "can_abandon"], c)
    out["names_tables.csv"] = _csv(["table_id", "old_name", "proposed_name", "name_used_for_swap", "approved", "verdict", "rules"],
                                   [[v["table_id"], v["old"], v["suggested"], v["effective"], "yes" if v["approved"] else "no", v["verdict"], " ".join(v["rules"])]
                                    for v in result["tables"].values()])
    out["names_import_problems.csv"] = _csv(["severity", "kind", "field_id", "detail"], [[p["severity"], p["kind"], p["field_id"], p["detail"]] for p in imp["problems"]])
    s = result["summary"]
    L = ["Field name validation summary", "", "Settings: " + ", ".join("%s=%s" % kv for kv in sorted(result["settings"].items())), ""]
    for t in T:
        v = s[t]["verdicts"]
        L += ["%s (%s)" % (t, fn.PROFILES[t]["title"]), "  PASS %d   AUTO_FIXABLE %d   NEEDS_HUMAN %d" % (v[fn.PASS], v[fn.AUTO_FIXABLE], v[fn.NEEDS_HUMAN]),
              "  names touched by step: " + (", ".join("%s=%d" % kv for kv in sorted(s[t]["steps"].items())) or "none"),
              "  violations by rule: " + (", ".join("%s=%d" % kv for kv in s[t]["rules"].items()) or "none"), ""]
    g = result.get("gate") or {"ready": True, "pending": [], "enforced": False}
    if not g["ready"]:
        names = ", ".join("%s (%s)" % (result["tables"][t]["old"], t) for t in g["pending"])
        L[2:2] = (["BLOCKED: field review cannot start until every table name is approved. %d table(s) waiting: %s" % (len(g["pending"]), names), ""]
                  if g["enforced"] else
                  ["DRAFT: %d table name(s) are not approved yet, so swapped names below are proposals only: %s" % (len(g["pending"]), names), ""])
    ncons = len(consolidated)
    L += ["Abandoned fields excluded: %d" % (s["abandoned"] - ncons), "Consolidated into another field (excluded): %d" % ncons, "Duplicate groups: %d" % len(result["clusters"]),
          "Import problems: %d (%d errors)" % (len(imp["problems"]), sum(1 for p in imp["problems"] if p["severity"] == "error"))]
    L += [""] + list(notes)
    out["names_summary.txt"] = "\n".join(L) + "\n"
    out["FIELD_NAMING_RULES.md"] = fn.rules_markdown()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate renamed Airtable fields against the naming rubric (read-only; writes reports).")
    ap.add_argument("--db", required=True)
    ap.add_argument("--base", required=True, help="Airtable base ID")
    ap.add_argument("--import", dest="imp", help="CSV with field_id, old_name, new_name, new_label, old_dataType, new_dataType")
    ap.add_argument("--out", default="out/names")
    ap.add_argument("--table-prefix", choices=("drop", "end", "plain"), default="end")
    ap.add_argument("--digit-style", choices=("move_to_end", "prefix_word"), default="move_to_end")
    ap.add_argument("--digit-word", default="")
    ap.add_argument("--no-table-swap", action="store_true")
    ap.add_argument("--no-char-cleanup", action="store_true")
    ap.add_argument("--no-leading-digits", action="store_true")
    ap.add_argument("--label-convert-quarter", action="store_true", help="propose 1Q -> Q1 in labels too (default: labels keep the client's convention)")
    ap.add_argument("--target-option", action="append", default=[], metavar="NAME.OPTION=VALUE",
                    help="an option for one name only, e.g. planning_name.allow_leading_digit=true, core_name.max_len=200, planning_name.extra_allowed=&' "
                         "(names: planning_name, core_name, core_label; options: %s). Repeatable." % ", ".join(fn.TARGET_OPTS))
    ap.add_argument("--emoji-mode", choices=("remove", "word"), default="remove", help="what to do with an emoji in names and labels: remove it (default) or replace it with its word")
    ap.add_argument("--description-emoji-mode", choices=("remove", "word"), default="remove", help="the same, for generated descriptions")
    ap.add_argument("--include-airtable-description", action="store_true", help="append each field's own Airtable description to its generated description")
    ap.add_argument("--draft", action="store_true", help="preview field results even though some table names are not approved (the report says DRAFT)")
    ap.add_argument("--skip-table", action="append", default=[], help="table ID to treat as out of scope (repeatable)")
    a = ap.parse_args(argv)
    if not os.path.isfile(a.db):
        sys.exit("No such database: %s" % a.db)
    per = {}
    for item in a.target_option:
        try:
            key, val = item.split("=", 1)
            name, opt = key.split(".", 1)
        except ValueError:
            sys.exit("--target-option must look like planning_name.max_len=200 (got %r)" % item)
        per.setdefault(name, {})[opt] = (val.lower() in ("1", "true", "yes", "on")) if opt in fn.BOOL_OPTS else val
    try:
        fn.Settings(targets=per)
    except ValueError as e:
        sys.exit("--target-option: %s" % e)
    st = fn.Settings(targets=per, table_swap=not a.no_table_swap, char_cleanup=not a.no_char_cleanup, leading_digits=not a.no_leading_digits,
                     description_include_airtable=a.include_airtable_description, emoji_mode=a.emoji_mode, description_emoji_mode=a.description_emoji_mode,
                     table_prefix=a.table_prefix, digit_style=a.digit_style, digit_word=a.digit_word, label_keep_quarter=not a.label_convert_quarter)
    db = load_db(a.db, a.base)
    if not db["fields"]:
        sys.exit("No fields found for base %s." % a.base)
    imp, notes = None, list(db["notes"])
    if a.imp:
        text, note = decode_csv(open(a.imp, "rb").read())
        imp = parse_import(text, db["fields"])
        notes += [note] if note else []
    merged = {m: g["survivor_id"] for g in db["groups"] for m in g["member_ids"][1:]}
    res = fn.run(db["fields"], db["tables"], st, manual=(imp or {}).get("manual"), approved_tables=db["approved_tables"],
                 abandoned=db["abandoned"] + [m for m in merged if m not in db["abandoned"]], out_of_scope_tables=a.skip_table, gate=not a.draft)
    os.makedirs(a.out, exist_ok=True)
    fbi = {f["field_id"]: f for f in db["fields"]}
    descs = {}
    surv = {g["survivor_id"]: g for g in db["groups"]}
    for fid, f in fbi.items():
        gen = fd.generate_description(f, fbi, st.description_include_airtable, st.description_emoji_mode,
                                      [fbi[m] for m in surv[fid]["member_ids"][1:]] if fid in surv else [])
        saved = db["saved_descriptions"].get(fid)
        text = saved if saved is not None else gen
        descs[fid] = {"text": text, "source": "saved" if saved is not None else "generated", "problems": [p["rule"] for p in fd.validate_description(text)]}
    for name, text in build_reports(res, db["fields"], imp, notes, descs, merged).items():
        with open(os.path.join(a.out, name), "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(text)
    print(open(os.path.join(a.out, "names_summary.txt"), encoding="utf-8-sig").read())
    print("Reports written to %s" % a.out)
    if res["gate"]["enforced"] and not res["gate"]["ready"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
