#!/usr/bin/env python3
"""Apply the column migrations (001 and 002) to an existing database. Standard library only.

    python sql/apply_migrations.py path/to/your.db

Adds only the columns that are missing, so it is safe to run more than once and on a database that is already
up to date. It never drops or changes data. Back up the file first if it matters to you."""
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
    conn.commit()
    conn.close()
    print("Added %d column(s): %s" % (len(added), ", ".join(added)) if added else "Already up to date.")


if __name__ == "__main__":
    main()
