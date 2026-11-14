-- Migration 011: the Core object type of each table. Adds ONE table: table_core_objects.
-- Additive only: no existing table or column is changed. Safe to run more than once.
-- PROPOSED, NOT YET APPLIED (authorised in principle by the owner on 2026-11-14; review the DDL, then confirm it is applied).
-- It is deliberately NOT in DatabaseSchemaLibrary.sql, the embedded DDL in server.py or sql/apply_migrations.py: the app refuses to connect to a
-- database that lacks an expected table, so those change together, after the owner confirms it is applied.

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
