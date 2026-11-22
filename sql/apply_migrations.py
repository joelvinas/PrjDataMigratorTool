#!/usr/bin/env python3
"""Apply every migration (001 to 027) to an existing database in one run. Standard library only.

    python sql/apply_migrations.py path/to/your.db

Adds only the columns and tables that are missing, so it is safe to run more than once and on a database that is already
up to date. It never changes data. The only drops are the removal of the empty placeholder tables form_sections and form_fields (migration 005) and automation_catalog and automation_steps (migration 006); a placeholder that has rows is kept and reported. Back up the file first if it matters to you."""
import os, re, sqlite3, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import draftmigrations  # noqa: E402  (021 to 027: data types, lookup lists, Git-R-Done results, Core field crosswalk, calculated fields)

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    if len(sys.argv) != 2 or not os.path.isfile(sys.argv[1]):
        sys.exit(__doc__)
    conn = sqlite3.connect(sys.argv[1])
    have = {r[1] for r in conn.execute("PRAGMA table_info(airtable_fields)")}
    if not have:
        sys.exit("That database has no airtable_fields table. Create it from the app's step 1 instead.")
    added = []
    for name in ("migration_001_add_columns.sql", "migration_002_add_field_options.sql"):
        for line in open(os.path.join(HERE, name), encoding="utf-8"):
            m = re.match(r"\s*ALTER TABLE airtable_fields ADD COLUMN (\w+) ", line)
            if m and m.group(1) not in have:
                conn.execute(line.strip())
                have.add(m.group(1))
                added.append(m.group(1))
    tables_before = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.executescript(open(os.path.join(HERE, "migration_003_add_tables_and_views.sql"), encoding="utf-8").read())
    conn.executescript(open(os.path.join(HERE, "migration_004_add_attachment_stats.sql"), encoding="utf-8").read())
    conn.executescript(open(os.path.join(HERE, "migration_005_add_form_specs.sql"), encoding="utf-8").read())
    conn.executescript(open(os.path.join(HERE, "migration_006_add_automation_specs.sql"), encoding="utf-8").read())
    conn.executescript(open(os.path.join(HERE, "migration_007_add_field_names.sql"), encoding="utf-8").read())
    conn.executescript(open(os.path.join(HERE, "migration_008_add_table_scope_and_targets.sql"), encoding="utf-8").read())
    conn.executescript(open(os.path.join(HERE, "migration_009_add_field_groups.sql"), encoding="utf-8").read())
    for name in ("migration_010_add_load_id_map.sql", "migration_011_add_table_core_objects.sql"):
        conn.executescript(open(os.path.join(HERE, name), encoding="utf-8").read())
    if "core_target_type" not in {r[1] for r in conn.execute("PRAGMA table_info(field_names)")}:   # migration 012: ADD COLUMN cannot be repeated
        conn.executescript(open(os.path.join(HERE, "migration_012_add_core_target_type.sql"), encoding="utf-8").read())
        added.append("field_names.core_target_type")
    for name in ("migration_013_add_field_relationships.sql", "migration_014_add_option_names.sql"):
        conn.executescript(open(os.path.join(HERE, name), encoding="utf-8").read())
    if "suggested_type" not in {r[1] for r in conn.execute("PRAGMA table_info(field_relationships)")}:   # migration 015: ADD COLUMN cannot be repeated
        conn.executescript(open(os.path.join(HERE, "migration_015_add_relationship_directions.sql"), encoding="utf-8").read())
        added.append("field_relationships.direction columns (6)")
    if "workspace_key" not in {r[1] for r in conn.execute("PRAGMA table_info(load_id_map)")}:   # migration 016 rebuilds the table: only while it is empty
        rows = conn.execute("SELECT COUNT(*) FROM load_id_map").fetchone()[0]
        if rows:
            sys.exit("Stopped before migration 016: load_id_map has %d row(s), and 016 rebuilds that table. Nothing was changed by 016 (earlier steps were kept)." % rows)
        conn.commit()
        conn.executescript(open(os.path.join(HERE, "migration_016_load_id_map_workspace_key.sql"), encoding="utf-8").read())
        added.append("load_id_map.workspace_key")
    conn.executescript(open(os.path.join(HERE, "migration_017_add_record_id_map.sql"), encoding="utf-8").read())
    conn.executescript(open(os.path.join(HERE, "migration_018_add_link_direction_events.sql"), encoding="utf-8").read())
    conn.executescript(open(os.path.join(HERE, "migration_019_add_field_writer_decisions.sql"), encoding="utf-8").read())
    conn.executescript(open(os.path.join(HERE, "migration_020_add_core_parents.sql"), encoding="utf-8").read())
    conn.commit()
    ran = draftmigrations.apply(conn)   # 021 to 027, each only if still missing
    dropped, kept = [], []
    for old in ("form_fields", "form_sections", "automation_steps", "automation_catalog"):   # children before parents
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (old,)).fetchone():
            if conn.execute('SELECT COUNT(*) FROM "%s"' % old).fetchone()[0] == 0:
                conn.execute('DROP TABLE "%s"' % old)
                dropped.append(old)
            else:
                kept.append(old)
    new_tables = sorted({r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} - tables_before)
    conn.commit()
    conn.close()
    if added:
        print("Added %d column(s): %s" % (len(added), ", ".join(added)))
    if new_tables:
        print("Added table(s): %s" % ", ".join(new_tables))
    if ran:
        print("Applied migration(s): %s" % ", ".join("%03d" % n for n in ran))
    if dropped:
        print("Dropped empty placeholder table(s): %s" % ", ".join(dropped))
    if kept:
        print("Kept %s: they contain rows. The app no longer uses them; remove them by hand when you are sure." % ", ".join(kept))
    if not added and not new_tables and not dropped:
        print("Already up to date.")


if __name__ == "__main__":
    main()
