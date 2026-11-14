-- Migration 009: field consolidation groups. Adds two tables: field_groups and field_group_members.
-- Additive only: no existing table or column is changed. Safe to run more than once.
-- Approved by the owner on 2026-11-14 and folded into sql/DatabaseSchemaLibrary.sql, the DDL embedded in server.py and
-- sql/apply_migrations.py. Apply it to an existing database with: python sql/apply_migrations.py path/to/your.db (back it up first).
-- Safe to run again: nothing happens when the tables already exist.

-- A consolidation group is several Airtable fields of ONE table that will become ONE Workfront field. A person chooses the survivor (the
-- field whose name and settings carry forward), records why, and says what the loader should do later when two members are both filled
-- and differ. Merged members drop out of the name review and the duplicate checks, and the survivor's description lists every origin.
-- A group can be dissolved at any time (the rows are deleted; the decision history stays in name_events, which is append-only).
CREATE TABLE IF NOT EXISTS field_groups (
    group_id INTEGER PRIMARY KEY AUTOINCREMENT,
    base_id TEXT NOT NULL,
    table_id TEXT NOT NULL,
    survivor_field_id TEXT NOT NULL,
    conflict_rule TEXT NOT NULL DEFAULT 'review' CHECK(conflict_rule IN ('review', 'survivor_wins', 'first_filled')),
                                      -- for the loader, when two members are both filled and differ:
                                      -- 'review' = a person resolves each such record, 'survivor_wins' = keep the survivor's value,
                                      -- 'first_filled' = use the first non-empty value in member order
    reason TEXT NOT NULL,             -- why these fields are being consolidated
    decided_by TEXT NOT NULL,         -- initials or a short name (the app has no logins)
    decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (table_id) REFERENCES airtable_tables(table_id),
    FOREIGN KEY (survivor_field_id) REFERENCES airtable_fields(field_id)
);
CREATE INDEX IF NOT EXISTS idx_field_groups_table ON field_groups(base_id, table_id);

-- The members of each group, including the survivor. A field can be in at most one group (field_id is the primary key).
CREATE TABLE IF NOT EXISTS field_group_members (
    group_id INTEGER NOT NULL,
    field_id TEXT NOT NULL PRIMARY KEY,
    member_order INTEGER NOT NULL,    -- 0 = the survivor; the others in the order their origins are listed and 'first_filled' is applied
    FOREIGN KEY (group_id) REFERENCES field_groups(group_id),
    FOREIGN KEY (field_id) REFERENCES airtable_fields(field_id)
);
CREATE INDEX IF NOT EXISTS idx_field_group_members_group ON field_group_members(group_id);
