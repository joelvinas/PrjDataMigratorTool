"""Test helper: give a fabricated test database the data type mapping (migrations 021, 022 and 023) and fill every field's types from the default mapping,
through the same store functions the Data types page uses. Used by the loader and plan suites, whose fields need a Planning type and a Core dataType and
displayType before they are ready. Needs src/ on the import path (every suite already adds it)."""
import os
import datatypes_store as dts

SQL = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "sql")


def apply_migrations(conn):
    """Apply migrations 021, 022 and 023 to a database built from DatabaseSchemaLibrary.sql (which does not hold them until the owner has applied them live)."""
    conn.executescript(open(os.path.join(SQL, "migration_021_add_data_type_mapping.sql"), encoding="utf-8").read())
    if "core_display_type" not in {r[1] for r in conn.execute("PRAGMA table_info(field_names)")}:
        conn.executescript(open(os.path.join(SQL, "migration_022_add_core_display_type.sql"), encoding="utf-8").read())
    else:
        conn.executescript("CREATE VIEW IF NOT EXISTS field_data_types AS SELECT airtable_field_id, target_type AS planning_target_type, core_target_type, core_display_type FROM field_names;")
    if "rule_text" not in {r[1] for r in conn.execute("PRAGMA table_info(data_type_defaults)")}:
        conn.executescript(open(os.path.join(SQL, "migration_023_add_rule_text_and_new_types.sql"), encoding="utf-8").read())
    conn.commit()


def fill_defaults(conn, base_id, actor="JV"):
    """Apply the default Planning and Core types to every field that has no valid one. Returns {system: changed}."""
    apply_migrations(conn)
    return {s: dts.apply(conn, base_id, actor, "test fixture", s, "defaults")["changed"] for s in ("planning", "core")}
