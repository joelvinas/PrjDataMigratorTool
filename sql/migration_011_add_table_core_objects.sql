-- Migration 011: the Core object type of each table. Adds ONE table: table_core_objects.
-- Additive only: no existing table or column is changed. Safe to run more than once.
-- Applied to the live database on 2026-11-14 (backed up first) and folded into sql/DatabaseSchemaLibrary.sql, the DDL embedded in server.py and
-- sql/apply_migrations.py. Apply it to an existing database with: python sql/apply_migrations.py path/to/your.db (back it up first).

-- A table that has a Workfront Core presence (table_targets.target = 'planning_and_core') becomes ONE kind of Core object: a project, a request
-- or a task. That is a human decision, recorded with who, when and why. A table with no row has no Core object type yet, and the loader keeps
-- its Core form on hold until it does.
CREATE TABLE IF NOT EXISTS table_core_objects (
    table_id TEXT NOT NULL PRIMARY KEY,
    base_id TEXT NOT NULL,
    core_object_type TEXT NOT NULL CHECK(core_object_type IN ('project', 'request', 'task')),
    decided_by TEXT NOT NULL,         -- initials or a short name (the app has no logins)
    decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT,
    FOREIGN KEY (table_id) REFERENCES airtable_tables(table_id)
);
CREATE INDEX IF NOT EXISTS idx_table_core_objects_base ON table_core_objects(base_id);
