#!/usr/bin/env python3
"""Apply the migrations (001 to 005) to an existing database. Standard library only.

    python sql/apply_migrations.py path/to/your.db

Adds only the columns and tables that are missing, so it is safe to run more than once and on a database that is already
up to date. It never changes data. The one drop is migration 005's removal of the empty placeholder tables form_sections and form_fields; a placeholder that has rows is kept and reported. Back up the file first if it matters to you."""
import os, re, sqlite3, sys

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
    dropped, kept = [], []
    for old in ("form_fields", "form_sections"):   # form_fields first: it references form_sections
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
    if dropped:
        print("Dropped empty placeholder table(s): %s" % ", ".join(dropped))
    if kept:
        print("Kept %s: they contain rows. The app no longer uses them; remove them by hand when you are sure." % ", ".join(kept))
    if not added and not new_tables and not dropped:
        print("Already up to date.")


if __name__ == "__main__":
    main()
