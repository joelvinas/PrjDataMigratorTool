#!/usr/bin/env python3
"""Draft migrations 021, 022 and 023 (the data type mapping): check for them and apply them. Standard library only.

Why this is separate: the three migrations are drafts until the owner confirms they are applied, so they are not in DatabaseSchemaLibrary.sql, the embedded DDL
or sql/apply_migrations.py, and the app does not refuse a database that lacks them. This module is how the app (database connect) and the owner's script
(sql/apply_draft_migrations.py) know what is still missing. Once the owner confirms the migrations are applied and folds them in, delete this module and its callers.
Schema only: no record value is read or written.
"""
import os

SQL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sql")
FILES = {21: "migration_021_add_data_type_mapping.sql", 22: "migration_022_add_core_display_type.sql", 23: "migration_023_add_rule_text_and_new_types.sql"}
TABLES_021 = ("data_type_defaults", "field_type_decisions", "data_type_events")


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}


def _columns(conn, table):
    return {r[1] for r in conn.execute('PRAGMA table_info("%s")' % table)}


def pending(conn):
    """[{migration, missing}] for each draft migration the database still lacks, in order. Empty when all three are in place."""
    have = _tables(conn)
    out = []
    m21 = [t for t in TABLES_021 if t not in have]
    if m21:
        out.append({"migration": 21, "missing": m21})
    m22 = []
    if "field_names" in have and "core_display_type" not in _columns(conn, "field_names"):
        m22.append("field_names.core_display_type")
    if "field_names" in have and "field_data_types" not in have:
        m22.append("view field_data_types")
    if m22:
        out.append({"migration": 22, "missing": m22})
    if "data_type_defaults" not in have or "rule_text" not in _columns(conn, "data_type_defaults"):
        out.append({"migration": 23, "missing": ["data_type_defaults.rule_text"]})
    return out


def describe(items):
    """One plain sentence for the Setup page and the command line, or '' when nothing is pending."""
    if not items:
        return ""
    return ("The data type mapping is not set up in this database yet (migration%s %s). Back up the file, then run: "
            "python sql/apply_draft_migrations.py <this database file>, and connect again. Until then the Data types page can show values but not save them."
            % ("s" if len(items) > 1 else "", ", ".join("%03d" % i["migration"] for i in items)))


def apply(conn):
    """Apply whatever is pending, in order, and return the migrations that ran. Repeatable: ADD COLUMN is guarded, everything else is IF NOT EXISTS / INSERT OR IGNORE.
    Raises ValueError (nothing changed) when field_names does not exist: run sql/apply_migrations.py first."""
    if "field_names" not in _tables(conn):
        raise ValueError("This database has no field_names table. Run python sql/apply_migrations.py on it first.")
    ran = []

    def run(n):
        conn.executescript(open(os.path.join(SQL, FILES[n]), encoding="utf-8").read())
        ran.append(n)
    todo = {i["migration"] for i in pending(conn)}
    if 21 in todo:
        run(21)
    if 22 in todo:
        if "core_display_type" not in _columns(conn, "field_names"):
            run(22)
        else:   # the column exists (a partial earlier run): only the view is missing, and ADD COLUMN cannot be repeated
            conn.executescript("CREATE VIEW IF NOT EXISTS field_data_types AS SELECT airtable_field_id, target_type AS planning_target_type, core_target_type, core_display_type FROM field_names;")
            ran.append(22)
    if 23 in todo:
        run(23)
    conn.commit()
    return ran


if __name__ == "__main__":
    import sqlite3
    import sys
    if len(sys.argv) != 2 or not os.path.isfile(sys.argv[1]):
        sys.exit("Usage: python src/draftmigrations.py path/to/your.db   (or: python sql/apply_draft_migrations.py path/to/your.db)")
    c = sqlite3.connect(sys.argv[1])
    try:
        done = apply(c)
    except ValueError as e:
        sys.exit(str(e))
    finally:
        c.close()
    print("Applied migration(s): %s" % ", ".join("%03d" % n for n in done) if done else "Already up to date.")
