#!/usr/bin/env python3
"""Apply the DRAFT data type migrations (021, 022, 023) to an existing database. Standard library only.

    python sql/apply_draft_migrations.py path/to/your.db

Run python sql/apply_migrations.py first (001 to 020). This adds only what is missing (three tables, one column, one view, seeded defaults), so it is safe to
run more than once, and it never changes existing data. Back up the file first if it matters to you. It is separate from apply_migrations.py until the
owner confirms 021 to 023 are applied; see CLAUDE.md."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import draftmigrations  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) != 2 or not os.path.isfile(sys.argv[1]):
        sys.exit(__doc__)
    c = sqlite3.connect(sys.argv[1])
    try:
        done = draftmigrations.apply(c)
    except ValueError as e:
        sys.exit(str(e))
    finally:
        c.close()
    print("Applied migration(s): %s" % ", ".join("%03d" % n for n in done) if done else "Already up to date.")
